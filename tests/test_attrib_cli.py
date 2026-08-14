"""
The `--attribute` command line: the attribution engine driven end to end.

`tests/test_attrib_core.py` pins the arithmetic.  This file pins the CLI, and
its job is the three things a wrapper can get wrong on its own:

  * a bad flag is REFUSED with a message that names what was wrong.  Every
    refusal test below asserts on the offending token AND on the way out --
    "raises SystemExit" would have passed before any of this existed, and an
    error message that does not name the typo is a bug report, not a message;
  * the numbers printed and exported are the ENGINE'S numbers.  Nothing here
    re-derives an impedance: the assertions compare against `pkg_rlc_attrib`
    and, through it, against `compute_z_matrix`.  A CLI that quietly computed
    its own M would be the worst available failure, because it would look
    right;
  * the CSV round-trips.  It is the only uncapped output, so the report's
    "(see --attribute-csv)" pointer is a claim about a file, and the terms in
    it must still add up to the total after a trip through %.6e.

Numbers that ARE hard-coded were measured in this session and the measurement
is written beside them.  Every guard was mutation-checked; the mutation that
defeats it is named in the test.

The whole module is Tk-free: `main(["--cli", ...])` never reaches pkg_rlc_gui.
"""

from __future__ import annotations

import csv
import io
import contextlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import numpy as np  # noqa: E402

import pkg_rlc.physics.attrib as attrib  # noqa: E402
import pkg_rlc.frontend.cli as ex  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    build_terminations_coupling,
    parse_mport_spec,
    parse_touchstone,
    s_to_y,
)

FIXTURES = _HERE / "fixtures"

DIFF_PAIR = str(FIXTURES / "diff_pair_4port.s4p")     # in_p/in_n/out_p/out_n
COUPLED_DIFF = str(FIXTURES / "coupled_4port_diff.s4p")   # c1_p/c1_n/c2_p/c2_n
COUPLED_FLOAT = str(FIXTURES / "coupled_4port_float.s4p")  # cond(Y) = 2.5e16
DECAP = str(FIXTURES / "decap_4port.s4p")             # two UNCOUPLED pi nets

# The command every test starts from: two single-ended probes on the near ends
# of tests/fixtures/diff_pair_4port.s4p, both far ends grounded, read at 5 GHz.
# Measured there: M = 1.010 nH, of which the bare EM coupling is 251 pH.
BASE = ["--cli", DIFF_PAIR, "--mode", "coupling",
        "--mport", "vic = 1", "--mport", "agg = 2",
        "--gnd", "3,4", "--freq", "5"]


def run(argv: list[str]) -> tuple[int, str, str]:
    """main() in-process, with stdout and stderr captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ex.main(argv)
    return rc, out.getvalue(), err.getvalue()


def engine_context(fixture: str = DIFF_PAIR, mports=("vic = 1", "agg = 2"),
                   gnd=(3, 4), shorts=(), freq_hz: float = 5e9, zt=None):
    """
    The same context the CLI builds, built independently of it.

    Deliberately NOT by calling into pkg_rlc_extractor: a test that got its
    reference from the code under test would pass whatever that code did.
    """
    d = parse_touchstone(fixture)
    Y = s_to_y(d.s, d.z0)
    term = build_terminations_coupling(
        [parse_mport_spec(s) for s in mports], list(gnd), list(shorts),
        nports=d.nports)
    return attrib.build_context(Y, d.freqs, term, freq_hz, zt=zt)


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        body = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(body))


def csv_comment_block(path: Path) -> str:
    with open(path, encoding="utf-8") as fh:
        return "".join(line for line in fh if line.startswith("#"))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestAttributeRefusals(unittest.TestCase):
    """
    Every bad flag exits 2 and says what was wrong, by name.

    Asserting only on the exit code is what these tests must NOT do: exit 2 is
    also what argparse produces for an unrelated typo, so the token the user
    got wrong has to appear in the message or the test proves nothing.
    """

    def refuse(self, extra: list[str], *must_contain: str,
               argv: list[str] | None = None) -> str:
        rc, out, err = run((argv if argv is not None else BASE) + extra)
        self.assertEqual(rc, 2, f"expected exit 2, got {rc}\n{out}\n{err}")
        for frag in must_contain:
            self.assertIn(frag, err)
        return err

    def test_a_pair_without_a_comma_is_refused_and_says_so(self):
        # Mutation: accepting split(",")[0:2] on a 1-element list silently
        # attributes 'vic' against itself.
        self.refuse(["--attribute", "vic"],
                    "VICTIM,AGGRESSOR", "'vic'", "1 field(s)")

    def test_a_pair_with_three_fields_is_refused(self):
        self.refuse(["--attribute", "vic,agg,extra"], "3 field(s)")

    def test_an_unknown_measurement_port_names_itself_and_the_alternatives(self):
        # The listing is the actionable half: without it the user has no way to
        # discover that the ports are called 'vic' and 'agg'.
        err = self.refuse(["--attribute", "vic,bogus"], "'bogus'")
        self.assertIn("1 'vic'", err)
        self.assertIn("2 'agg'", err)

    def test_victim_equal_to_aggressor_is_refused_by_name(self):
        # A self impedance is not what this report is about: 'k' would come out
        # identically 1 and M would be L_a, both silently.
        self.refuse(["--attribute", "vic,vic"], "'vic'", "MUTUAL")

    def test_a_position_outside_the_mport_list_names_the_range(self):
        self.refuse(["--attribute", "1,9"], "9", "1..2")

    def test_a_position_is_ONE_based(self):
        # 1,2 must be the two ports, and 0 must be out of range -- the repo's
        # rule is 1-based at the CLI boundary.
        rc, _out, _err = run(BASE + ["--attribute", "1,2"])
        self.assertEqual(rc, 0)
        self.refuse(["--attribute", "0,1"], "1..2")

    def test_a_measurement_port_NAMED_with_a_digit_wins_over_the_position(self):
        # Both readings of '2' are available here: the port named '2' is the
        # FIRST --mport, and position 2 is the one named 'agg'.  The name has
        # to win, and it has to say it won -- silently resolving to position 2
        # would attribute the wrong pair with nothing on screen to notice.
        argv = ["--cli", DIFF_PAIR, "--mode", "coupling",
                "--mport", "2 = 1", "--mport", "agg = 2",
                "--gnd", "3,4", "--freq", "5", "--attribute", "2,agg"]
        rc, out, err = run(argv)
        self.assertEqual(rc, 0, err)
        self.assertIn("victim '2'", out)
        flat = " ".join(out.split())
        self.assertIn("was resolved to the measurement port NAMED '2'", flat)
        self.assertIn("a name always wins over a position", flat)

    def test_a_bare_number_alternative_is_refused_with_the_reason(self):
        # Mutation: reading '50' as R=50 guesses a unit the user never wrote.
        self.refuse(["--attribute", "vic,agg", "--attribute-alt", "50"],
                    "'50'", "ohms, henries or farads")

    def test_a_comma_field_without_an_equals_is_refused(self):
        # This is core's `R=5 m` factor-of-1000 trap arriving through a
        # different door: parse_kv_rlc_params DROPS a token with no '=', so
        # 'R=5,m' would compute 5 ohm where 5 milliohm was meant.
        self.refuse(["--attribute", "vic,agg", "--attribute-alt", "R=5,m"],
                    "'R=5,m'", "'m'", "no '='")

    def test_an_unknown_rlc_key_names_the_key_and_the_spec(self):
        self.refuse(["--attribute", "vic,agg", "--attribute-alt", "X=5"],
                    "'X=5'", "'X'", "R, L, or C")

    def test_a_bad_ground_model_lists_the_accepted_forms(self):
        self.refuse(["--attribute", "vic,agg",
                     "--attribute-ground-model", "nonsense"],
                    "'nonsense'", "diag:SPEC", "shared:SPEC")

    def test_a_ground_model_with_no_impedance_is_refused(self):
        self.refuse(["--attribute", "vic,agg",
                     "--attribute-ground-model", "shared:"],
                    "needs an impedance", "shared:L=1n")

    def test_an_open_ground_lead_is_refused(self):
        # An element that is not in the network has no impedance to share, so
        # 'shared:open' is not a model -- it is a request to drop the port.
        self.refuse(["--attribute", "vic,agg",
                     "--attribute-ground-model", "shared:open"],
                    "OPEN lead", "--gnd")

    def test_a_bad_frequency_list_names_the_bad_token(self):
        self.refuse(["--attribute", "vic,agg", "--attribute-freqs", "1,abc"],
                    "'abc'", "GHz")

    def test_an_attribute_flag_without_attribute_is_refused_by_name(self):
        # Mutation: ignoring them silently makes '--attribute-csv out.csv'
        # (with --attribute forgotten) exit 0 having written nothing.
        err = self.refuse(["--attribute-alt", "open"], "--attribute-alt",
                          "--attribute VICTIM,AGGRESSOR")
        self.assertIn("only means", err)          # one flag: singular

    def test_several_dependent_flags_are_all_named(self):
        err = self.refuse(["--attribute-alt", "open",
                           "--attribute-group", "flat"],
                          "--attribute-alt", "--attribute-group")
        self.assertIn("only mean", err)           # two flags: plural

    def test_the_dependent_check_does_not_need_a_readable_file(self):
        # It is a statement about argv, so it must not be gated on a parse.
        rc, _out, err = run(["--cli", str(FIXTURES / "no_such_file.s4p"),
                             "--mode", "coupling", "--attribute-csv", "x.csv"])
        self.assertEqual(rc, 2)
        self.assertIn("--attribute-csv", err)

    def test_attribute_outside_coupling_mode_is_refused_like_mport_is(self):
        # Same idiom as the pre-existing "--mport is only valid with --mode
        # coupling", and the message has to carry the way out.
        rc, _out, err = run(["--cli", DIFF_PAIR, "--mode", "gnd",
                             "--porta", "1", "--gnd", "3,4",
                             "--attribute", "a,b"])
        self.assertEqual(rc, 2)
        self.assertIn("--mode coupling", err)
        self.assertIn("--mport", err)

    def test_a_single_measurement_port_is_refused(self):
        rc, _out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                             "--mport", "vic = 1", "--gnd", "3,4",
                             "--freq", "5", "--attribute", "1,1"])
        self.assertEqual(rc, 2)
        self.assertIn("vic", err)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class TestReportStructure(unittest.TestCase):
    """Everything the contract says must be on screen, in the order it says."""

    @classmethod
    def setUpClass(cls):
        rc, out, err = run(BASE + ["--attribute", "vic,agg"])
        cls.rc, cls.out, cls.err = rc, out, err

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_sign_convention_comes_before_any_signed_number(self):
        # Requirement 11, and the ORDER is the requirement: a reader who meets
        # the first signed term before the convention has already guessed.
        i_sign = self.out.index("Sign convention.")
        i_table = self.out.index("bare EM coupling")
        self.assertLess(i_sign, i_table)
        # Mutation: dropping the verbatim text for a paraphrase.
        self.assertIn(attrib.SIGN_CONVENTION_TEXT.split(".")[0], self.out)
        for phrase in ("V(+) - V(-)", "OUT of the structure into ground",
                       "RELATIVE signs between terms are physical"):
            self.assertIn(phrase, " ".join(self.out.split()))

    def test_the_sections_are_in_the_contract_order(self):
        order = ["0. Sign convention",
                 "1. Decomposition of Z_ab",
                 "2. Reconciliation against compute_z_matrix",
                 "3. Return-path budget",
                 "4. Sensitivity",
                 "5. Joint effects",
                 "6. Cumulative",
                 "7. Series-inductance sweep",
                 "8. Cross-frequency rank stability",
                 "9. Exact current-transfer ratio",
                 "Caveats"]
        found = [self.out.index(s) for s in order]
        self.assertEqual(found, sorted(found),
                         "sections are out of order:\n" + "\n".join(order))

    def test_every_declared_element_has_a_row_under_its_flag(self):
        # The group label is the CLI's row_sources: the flag that declared the
        # port, not "conn row 1", which names a row that exists nowhere on a
        # command line.
        self.assertIn("ground port 3", self.out)
        self.assertIn("ground port 4", self.out)
        self.assertIn("--gnd 3,4", self.out)

    def test_the_reconciliation_reports_the_residual_AND_its_floor(self):
        # Requirement 5: a residual with no floor beside it is unreadable --
        # 1e-7 is excellent on a 153-port file and terrible on a 2-port one.
        self.assertRegex(self.out, r"residual [-\d.e+]+ relative, against an "
                                   r"achievable floor of [-\d.e+]+")
        self.assertIn("cond(Ybase)", self.out)
        self.assertIn("cond(H)", self.out)

    def test_the_return_path_budget_is_always_reported(self):
        self.assertIn("Return-path budget", self.out)
        self.assertRegex(" ".join(self.out.split()),
                         r"\d+\.\d+% of the aggressor's return")

    def test_the_reciprocity_diagnostic_is_reported_not_assumed(self):
        # Requirement 1: r_a is its own solve and the gap is a number on
        # screen, because the user's real file misses reciprocity by 3.4e-10.
        self.assertIn("reciprocity |r_a - p_a|/|p_a|", self.out)

    def test_all_three_caveats_are_printed(self):
        for frag in ("BLIND TO OPEN PORTS",
                     "DEPENDS ON HOW THE SPEC IS SPELLED",
                     "CANNOT EVALUATE NEW METAL"):
            self.assertIn(frag, self.out)

    def test_the_open_port_caveat_says_what_to_do_instead(self):
        # "we cannot answer that" with no next step is the failure mode; the
        # route is to DECLARE the port and read its 'open' row.
        flat = " ".join(self.out.split())
        self.assertIn("declare it and read its 'open' row", flat)

    def test_the_measurement_ports_are_listed_ONE_based_next_to_the_hint(self):
        flat = " ".join(self.out.split())
        self.assertIn("1 'vic' 2 'agg'", flat)
        self.assertIn("--attribute takes either the name or the number", flat)

    def test_the_transfer_ratio_names_both_readings_and_their_difference(self):
        self.assertIn("-Z_ab / Z_aa", self.out)
        self.assertIn("M / L_a", self.out)
        self.assertIn("Norton", self.out)

    def test_an_all_dropped_spec_is_not_called_an_empty_one(self):
        # `--short 1-3,2-4` declares two shorts and the reduction annihilates
        # both (each ties a probe to itself).  Saying "this spec declares no
        # non-probe termination at all" there is a false statement about what
        # the user typed, and it points at the wrong fix.
        rc, out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                            "--mport", "vic = 1", "--mport", "agg = 2",
                            "--short", "1-3,2-4", "--freq", "5",
                            "--attribute", "vic,agg"])
        self.assertEqual(rc, 0, err)
        flat = " ".join(out.split())
        self.assertIn("was dropped before the split", flat)
        self.assertIn("almost certainly not what the spec meant", flat)
        self.assertNotIn("declares no non-probe termination at all", flat)
        # ... and the header already named them one at a time.
        self.assertIn("dropped: short 1-3", flat)

    def test_one_frequency_says_how_to_get_more(self):
        flat = " ".join(self.out.split())
        self.assertIn("Only one frequency was evaluated", flat)
        self.assertIn("--attribute-freqs 1,5,10", flat)


class TestNumbersAreTheEnginesNumbers(unittest.TestCase):
    """
    Nothing the CLI prints or exports may be a second derivation.

    This is the guard that matters most: a wrapper that recomputed M with its
    own 1/omega would agree with the engine on this fixture and disagree on the
    next one, and nothing would raise.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_cli_"))

    def test_the_exported_terms_are_the_engines_terms_to_the_last_digit(self):
        path = self.tmp / "terms.csv"
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)

        ctx = engine_context()
        dec = attrib.decompose(ctx, 0, 1, "Z")
        want = {t.label: t.contribution for t in dec.terms}

        got = {r["element"]: complex(float(r["value_re"]), float(r["value_im"]))
               for r in read_csv(path)
               if r["section"] == "term" and r["quantity"] == "Z"}
        self.assertEqual(set(got), set(want))
        for label, value in want.items():
            # 6 significant digits is the CSV's own %.6e format, which every
            # other exporter in this repo uses; the comparison is against the
            # engine's float, so anything looser would hide a real drift.
            self.assertLess(abs(got[label] - value),
                            1e-6 * max(abs(value), 1e-30) + 1e-30,
                            f"{label}: {got[label]} != {value}")

    def test_the_exported_M_column_is_Im_over_omega_of_the_Z_column(self):
        # Mutation: computing M in the CLI as Im(Z)/(2*pi*f_requested) instead
        # of at the SNAPPED grid frequency.  On this fixture the request is
        # 5 GHz and the grid point is 5.0005 GHz, a 0.01% error that no eyeball
        # catches.
        path = self.tmp / "m.csv"
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        rows = read_csv(path)
        zs = {r["element"]: complex(float(r["value_re"]), float(r["value_im"]))
              for r in rows if r["section"] == "term" and r["quantity"] == "Z"}
        ms = {r["element"]: float(r["value_re"])
              for r in rows if r["section"] == "term" and r["quantity"] == "M"}
        f_snapped = float(rows[0]["freq_GHz"]) * 1e9
        self.assertNotAlmostEqual(f_snapped, 5e9, delta=1.0,
                                  msg="precondition: the fixture's grid does "
                                      "not sit exactly on 5 GHz, which is what "
                                      "makes this test able to fail")
        om = 2.0 * math.pi * f_snapped
        for label, z in zs.items():
            self.assertAlmostEqual(ms[label], z.imag / om,
                                   delta=1e-6 * abs(z.imag / om))

    def test_the_total_is_compute_z_matrixs_value_not_the_modules_sum(self):
        # Requirement 5: the engine's value is the authoritative total.
        path = self.tmp / "tot.csv"
        run(BASE + ["--attribute", "vic,agg", "--attribute-csv", str(path)])
        ctx = engine_context()
        row = [r for r in read_csv(path)
               if r["section"] == "total" and r["quantity"] == "Z"][0]
        got = complex(float(row["value_re"]), float(row["value_im"]))
        want = complex(ctx.Zref[0, 1])
        self.assertLess(abs(got - want), 1e-6 * abs(want))

    def test_the_terms_still_sum_to_the_total_after_the_csv_round_trip(self):
        path = self.tmp / "sum.csv"
        run(BASE + ["--attribute", "vic,agg", "--attribute-csv", str(path)])
        rows = read_csv(path)
        terms = [complex(float(r["value_re"]), float(r["value_im"]))
                 for r in rows if r["section"] == "term" and r["quantity"] == "Z"]
        total = [complex(float(r["value_re"]), float(r["value_im"]))
                 for r in rows if r["section"] == "total"
                 and r["quantity"] == "Z"][0]
        self.assertEqual(len(terms), 3)       # bare EM + two grounds
        self.assertLess(abs(sum(terms) - total) / abs(total), 1e-5)

    def test_the_exported_sensitivity_matches_an_independent_evaluation(self):
        path = self.tmp / "sens.csv"
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-alt", "L=0.3n",
                                    "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        ctx = engine_context()
        # The Alternative is built here rather than taken from the CLI's own
        # parser, so this is a genuinely independent reference; its NAME is
        # the user's spelling because that is what the CLI echoes back.
        alt = attrib.Alternative("L=0.3n", 1j * ctx.omega * 0.3e-9)
        want = {(r.label, r.alternative): r.new_value
                for r in attrib.sensitivity(ctx, 0, 1, [alt], "M")}
        got = {(r["element"], r["alternative"]): float(r["value_re"])
               for r in read_csv(path) if r["section"] == "sensitivity"}
        self.assertEqual(set(got), set(want))
        for key, value in want.items():
            self.assertAlmostEqual(got[key], value.real,
                                   delta=1e-6 * abs(value.real))

    def test_the_sign_of_a_negative_M_survives_the_whole_pipeline(self):
        # R/L/C/M/k are never clipped or abs()-ed anywhere in this repo.  On
        # coupled_4port_diff with ports 2 and 4 shorted together the mutual
        # reads -1.27 uH (capacitive); measured this session.
        path = self.tmp / "neg.csv"
        rc, out, err = run(["--cli", COUPLED_DIFF, "--mode", "coupling",
                            "--mport", "c1 = 1", "--mport", "c2 = 3",
                            "--short", "2-4", "--freq", "1",
                            "--attribute", "c1,c2",
                            "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        total = [r for r in read_csv(path)
                 if r["section"] == "total" and r["quantity"] == "M"][0]
        self.assertLess(float(total["value_re"]), 0.0)
        self.assertIn("-1.27 uH", out)

    def test_C_c_is_headlined_when_the_coupling_is_capacitive(self):
        # Requirement 8: C_c is a first-class reading and must still be shown,
        # as a TOTAL, with the reason it has no per-term column -- quoted from
        # the engine so the two cannot drift.
        rc, out, _err = run(["--cli", COUPLED_DIFF, "--mode", "coupling",
                             "--mport", "c1 = 1", "--mport", "c2 = 3",
                             "--short", "2-4", "--freq", "1",
                             "--attribute", "c1,c2"])
        self.assertEqual(rc, 0)
        flat = " ".join(out.split())
        self.assertIn("Im(Z_ab) < 0: the coupling is CAPACITIVE", flat)
        self.assertIn("C_c has NO per-element split", flat)
        self.assertIn("RECIPROCAL of the decomposed quantity", flat)

    def test_a_singular_baseline_is_handled_and_says_so(self):
        # coupled_4port_float.s4p is the repo's flagship Mode 6 example and has
        # cond(Y) = 2.5e16, so a naive inv() is wrong on day one.
        rc, out, err = run(["--cli", COUPLED_FLOAT, "--mode", "coupling",
                            "--mport", "c1 = 1 / 2", "--mport", "c2 = 3 / 4",
                            "--freq", "1", "--attribute", "c1,c2"])
        self.assertEqual(rc, 0, err)
        self.assertIn("800 pH", out)               # the fixture's known M
        self.assertRegex(out, r"cond\(Ybase\) [\d.]+e\+1[5-9]")

    def test_an_exactly_zero_mutual_suppresses_the_share_and_says_why(self):
        # Requirement 7's other half: a share of nothing is noise over noise.
        # decap_4port.s4p is two UNCOUPLED pi networks, so Z_ab is 0 by
        # construction, not small.
        rc, out, err = run(["--cli", DECAP, "--mode", "coupling",
                            "--mport", "s = 1", "--mport", "c = 3",
                            "--gnd", "2,4", "--freq", "5",
                            "--attribute", "s,c"])
        self.assertEqual(rc, 0, err)
        flat = " ".join(out.split())
        self.assertIn("The share column is suppressed", flat)
        self.assertIn("Z_ab is exactly zero", flat)


class TestQuadratureShare(unittest.TestCase):
    """
    Requirement 7: the share is a signed PROJECTION plus a quadrature part,
    never a complex ratio.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_quad_"))

    def test_both_share_columns_are_exported_and_the_inline_ones_sum_to_one(self):
        path = self.tmp / "q.csv"
        run(BASE + ["--attribute", "vic,agg", "--attribute-csv", str(path)])
        rows = [r for r in read_csv(path)
                if r["section"] == "term" and r["quantity"] == "Z"]
        inline = [float(r["share_inline"]) for r in rows]
        quad = [float(r["share_quad"]) for r in rows]
        # Mutation: reporting abs(term)/abs(total) instead of the projection.
        # That does NOT sum to 1 -- measured on this fixture it sums to 1.0000
        # only because the terms happen to be nearly collinear, so the test
        # also pins the quadrature parts to zero, which a magnitude ratio
        # cannot reproduce (it has no quadrature at all).
        # 6 places, not more: the CSV is written at %.6e like every other
        # exporter here, so the shares come back rounded.
        self.assertAlmostEqual(sum(inline), 1.0, places=6)
        self.assertTrue(all(abs(q) < 1e-9 for q in quad), quad)
        self.assertIn("share", " ".join(rows[0].keys()))

    def test_the_printed_table_carries_a_share_and_a_quad_column(self):
        _rc, out, _err = run(BASE + ["--attribute", "vic,agg"])
        header = [ln for ln in out.splitlines() if "Z term" in ln][0]
        self.assertIn("share", header)
        self.assertIn("quad", header)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


class TestGrouping(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_grp_"))

    def groups_of(self, extra: list[str]) -> set[str]:
        path = self.tmp / "g.csv"
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-csv", str(path)] + extra)
        self.assertEqual(rc, 0, err)
        return {r["group"] for r in read_csv(path)
                if r["section"] == "term" and r["group"]}

    def test_row_is_the_default_and_groups_by_the_flag_that_declared_it(self):
        # Mutation: falling through to build_context's sources=None, which
        # groups by element KIND -- so nine ports written as one range become
        # indistinguishable from nine separate rows.
        self.assertEqual(self.groups_of([]), {"--gnd 3,4"})
        self.assertEqual(self.groups_of(["--attribute-group", "row"]),
                         {"--gnd 3,4"})

    def test_flat_puts_every_element_on_its_own(self):
        self.assertEqual(self.groups_of(["--attribute-group", "flat"]),
                         {"port 3", "port 4"})

    def test_name_uses_the_files_port_names_and_flags_itself_as_a_heuristic(self):
        # diff_pair_4port.s4p names its ports in_p / in_n / out_p / out_n, and
        # core's name_prefix strips only a TRAILING run of digits, so these are
        # four families of one -- which is the documented false-alarm-avoiding
        # behaviour, not a bug.
        self.assertEqual(self.groups_of(["--attribute-group", "name"]),
                         {"name 'out_p*'", "name 'out_n*'"})
        _rc, out, _err = run(BASE + ["--attribute", "vic,agg",
                                     "--attribute-group", "name"])
        flat = " ".join(out.split())
        self.assertIn("NAMING HEURISTIC", flat)
        self.assertIn("statement about spelling, not about the network", flat)

    def test_name_collapses_a_numbered_family_into_one_group(self):
        # coupled_4port_diff.s4p names its ports c1_p / c1_n / c2_p / c2_n;
        # grounding 2 and 4 gives the families 'c1' and 'c2'.
        path = self.tmp / "n.csv"
        rc, _out, err = run(["--cli", COUPLED_DIFF, "--mode", "coupling",
                             "--mport", "c1 = 1", "--mport", "c2 = 3",
                             "--gnd", "2,4", "--freq", "1",
                             "--attribute", "c1,c2",
                             "--attribute-group", "name",
                             "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        groups = {r["group"] for r in read_csv(path)
                  if r["section"] == "term" and r["group"]}
        self.assertEqual(groups, {"name 'c1_n*'", "name 'c2_n*'"})

    def test_name_falls_back_to_the_flag_when_the_file_names_nothing(self):
        # A file with no port names has no evidence to offer, and a family
        # derived from nothing would look authoritative and be empty.  Pure,
        # because no fixture in the repo is nameless.
        args = ex._make_arg_parser().parse_args(
            ["--cli", DIFF_PAIR, "--gnd", "3,4"])
        src, notes = ex._attr_sources("name", args, ["vic = 1", "agg = 2"],
                                      [3, 4], [], ["", "", "", ""], 4)
        self.assertEqual({src[3], src[4]}, {"--gnd 3,4"})
        self.assertIn("found no port names", " ".join(notes))

    def test_a_vdd_port_is_named_in_the_group_label_it_was_merged_into(self):
        # --vdd is unioned into --gnd upstream, so the group label has to say
        # where a port came from or the provenance column lies about the flag
        # the user typed.
        rc, out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                            "--mport", "vic = 1", "--mport", "agg = 2",
                            "--gnd", "3", "--vdd", "4", "--freq", "5",
                            "--attribute", "vic,agg"])
        self.assertEqual(rc, 0, err)
        self.assertIn("--gnd 3 (+ --vdd 4)", out)

    def test_a_port_named_by_two_flags_is_reported_not_silently_relabelled(self):
        # row_sources is last-assignment-wins and so is this; what must not
        # happen is that the ground element quietly reads as belonging to
        # --short with nothing on screen to say so.
        rc, out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                            "--mport", "vic = 1", "--mport", "agg = 2",
                            "--gnd", "3", "--short", "3-4", "--freq", "5",
                            "--attribute", "vic,agg"])
        self.assertEqual(rc, 0, err)
        flat = " ".join(out.split())
        self.assertIn("named by both --gnd and --short", flat)
        self.assertIn("last-assignment-wins", flat)


# ---------------------------------------------------------------------------
# Candidate terminations
# ---------------------------------------------------------------------------


class TestAlternatives(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_alt_"))

    def alts_in(self, extra: list[str]) -> set[str]:
        path = self.tmp / "a.csv"
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-csv", str(path)] + extra)
        self.assertEqual(rc, 0, err)
        return {r["alternative"] for r in read_csv(path)
                if r["section"] == "sensitivity"}

    def test_with_no_candidate_only_the_two_STRUCTURAL_ones_are_assumed(self):
        # The requirement's second stated concern: never infer what a port
        # "should" be.  open and ideal need no judgement; L=1n does.
        # Mutation: falling back to attrib.default_alternatives(), which
        # invents R=50, L=1n, R=0.1+L=1n and C=100pF out of nowhere.
        self.assertEqual(self.alts_in([]), {"open", "ideal"})

    def test_the_report_says_the_scan_was_limited_and_how_to_widen_it(self):
        _rc, out, _err = run(BASE + ["--attribute", "vic,agg"])
        flat = " ".join(out.split())
        self.assertIn("STRUCTURAL candidates", flat)
        self.assertIn("--attribute-alt L=0.3n", flat)
        self.assertIn("This tool will not guess", flat)

    def test_user_candidates_REPLACE_the_structural_pair_exactly(self):
        self.assertEqual(self.alts_in(["--attribute-alt", "R=50"]), {"R=50"})
        self.assertEqual(
            self.alts_in(["--attribute-alt", "open",
                          "--attribute-alt", "R=0.5,L=1n"]),
            {"open", "R=0.5,L=1n"})

    def test_every_spelling_the_help_promises_parses(self):
        ctx = engine_context()
        om = ctx.omega
        cases = {
            "open": None,
            "ideal": 0j,
            "gnd": 0j,
            "R=50": complex(50.0, 0.0),
            "L=0.3n": 1j * om * 0.3e-9,
            "R=0.5,L=1n": complex(0.5, 0.0) + 1j * om * 1e-9,
            "C=100p": 1.0 / (1j * om * 100e-12),
        }
        for spec, want in cases.items():
            with self.subTest(spec):
                alt = ex._attr_alternative(spec, om)
                if want is None:
                    self.assertIsNone(alt.z)
                else:
                    self.assertAlmostEqual(abs(alt.z - want), 0.0, places=12)

    def test_a_space_separates_fields_exactly_as_a_comma_does(self):
        """The window's spelling has to work here, and mean the same thing.

        This flag split on the comma and the Attribution window's Candidates
        field splits on whitespace, so a spelling a user had just got working
        on one surface failed on the other -- the only one of the refactor's
        seven CLI/GUI divergences that was a trap rather than a presentation
        choice.

        Mutation: put `raw.split(",")` back and 'R=0.5 L=1n' dies inside
        parse_si with "could not convert string to float: '0.5 L=1'".
        """
        om = engine_context().omega
        want = ex._attr_series_impedance("R=0.5,L=1n", om)
        for spelling in ("R=0.5 L=1n", "R=0.5, L=1n", "R=0.5   L=1n"):
            with self.subTest(spelling):
                self.assertEqual(ex._attr_series_impedance(spelling, om), want)
        # The LABEL is normalised to this surface's own spelling, which is
        # what keeps every pre-existing comma spec byte-identical in the
        # report and in the CSV.
        self.assertEqual(want[1], "R=0.5,L=1n")

    def test_a_space_inside_a_VALUE_is_still_refused_on_either_separator(self):
        """Splitting on both must not rescue 'R=5 m' into 'R=5' + a dropped 'm'.

        That is core's factor-of-1000 trap: `parse_kv_rlc_params` DROPS a
        token with no '=', so the answer would be 5 ohm where 5 milliohm was
        typed.  It is also the one thing this flag got quietly WRONG before
        whitespace was a separator: `parse_si` tolerates 'R=5 m' as one field
        and read it as 5 milliohm, while the window refused the identical
        string by name.  One grammar, one answer, and the answer is the
        documented refusal.

        Mutation: drop the no-'=' check, or attach a suffix token to the field
        in front of it -- either turns a loud refusal into a silent reading.
        """
        om = engine_context().omega
        for spec in ("R=5 m", "R=5,m", "C=1 uF"):
            with self.subTest(spec):
                with self.assertRaises(ValueError) as cm:
                    ex._attr_series_impedance(spec, om)
                self.assertIn("no '='", str(cm.exception))

    def test_every_word_for_a_perfect_short_is_taken_on_both_surfaces(self):
        # 'gnd' / 'ground' were CLI-only and '0' was window-only, so each
        # refused two words the other took.  `_IDEAL_WORDS` is one tuple and
        # both import it.
        om = engine_context().omega
        for word in ("gnd", "ground", "ideal", "short", "0", "IDEAL"):
            with self.subTest(word):
                self.assertEqual(ex._attr_series_impedance(word, om),
                                 (0j, "ideal"))

    def test_a_series_capacitor_at_DC_is_an_OPEN_and_not_a_perfect_short(self):
        """0 ohms where the answer is infinite -- the widest a value can be wrong.

        `y_series_rlc` evaluates 1/(1j*0*C) as inf, so Z is nan, y is nan, and
        the non-finite branch read that as a PERFECT SHORT.  Reachable: every
        composed sweep keeps its 0 Hz point, so `--freq 0` with a 'C='
        candidate lands here.  The window has always answered 'open'.

        Mutation: remove the omega == 0 guard and the assertion below reads
        0j -- a candidate that removes the element becoming one that shorts it.
        """
        for spec in ("C=100p", "R=0.5,C=100p", "R=0.5,L=1n,C=100p"):
            with self.subTest(spec):
                z, _label = ex._attr_series_impedance(spec, 0.0)
                self.assertIsNone(z)
        # No capacitor: DC is still an ordinary evaluation, and R=L=0 is still
        # the perfect short it always was.
        self.assertEqual(ex._attr_series_impedance("R=0.5,L=1n", 0.0)[0],
                         complex(0.5, 0.0))
        self.assertEqual(ex._attr_series_impedance("R=0,L=0", 0.0)[0], 0j)

    def test_the_SI_suffix_M_is_Mega_here_too(self):
        # A repo-wide invariant: 'M' is Mega, 'm' is milli.  The CLI reaches it
        # through core's parse_si, so this pins that it did not grow a private
        # table on the way.
        om = engine_context().omega
        self.assertAlmostEqual(ex._attr_alternative("R=1M", om).z.real, 1e6)
        self.assertAlmostEqual(ex._attr_alternative("R=1m", om).z.real, 1e-3)

    def test_the_ranking_is_capped_on_screen_and_uncapped_in_the_csv(self):
        # The "(see --attribute-csv)" pointer is only true if the file really
        # has no cap.  Eleven candidates x two elements = 22 rows against a
        # printed cap of 20.
        specs = ["open", "ideal", "R=1", "R=10", "R=50", "L=0.1n", "L=0.3n",
                 "L=1n", "C=1p", "C=10p", "C=100p"]
        flags: list[str] = []
        for s in specs:
            flags += ["--attribute-alt", s]
        path = self.tmp / "cap.csv"
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-csv", str(path)] + flags)
        self.assertEqual(rc, 0, err)
        rows = [r for r in read_csv(path) if r["section"] == "sensitivity"]
        self.assertEqual(len(rows), 2 * len(specs))
        self.assertGreater(len(rows), ex.ATTR_RANK_ROWS)
        self.assertIn(f"... {len(rows) - ex.ATTR_RANK_ROWS} more rows", out)
        self.assertIn("--attribute-csv", out)

    def test_a_finite_candidate_is_also_used_as_a_victim_load(self):
        # The exact loaded transfer ratio needs a load and this tool will not
        # invent one; the user's own --attribute-alt is the only impedance it
        # has been given.
        rc, out, _err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-alt", "R=50"])
        self.assertEqual(rc, 0)
        self.assertIn("-Z_ab/(Z_aa+Z_load)", out)
        self.assertIn("R=50", out.split("9. Exact current-transfer")[1])

    def test_no_finite_candidate_means_no_loaded_ratio_is_invented(self):
        rc, out, _err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-alt", "open"])
        self.assertEqual(rc, 0)
        self.assertNotIn("-Z_ab/(Z_aa+Z_load)", out)


# ---------------------------------------------------------------------------
# The ground model (requirement 2)
# ---------------------------------------------------------------------------


class TestGroundModel(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_gm_"))

    def m_under(self, model: str) -> float:
        path = self.tmp / (model.replace(":", "_").replace("=", "_") + ".csv")
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--attribute-ground-model", model,
                                    "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        rows = [r for r in read_csv(path) if r["section"] == "ground_model"]
        self.assertEqual(len(rows), 1, "no ground_model record was exported")
        return float(rows[0]["value_re"])

    def test_the_default_is_diag_and_changes_nothing(self):
        rc, out, _err = run(BASE + ["--attribute", "vic,agg"])
        self.assertEqual(rc, 0)
        self.assertIn("ground model     : diag (as declared)", out)
        # No 3b section at all: there is no model to report.
        self.assertNotIn("3b. Ground model", out)

    def test_shared_and_independent_leads_are_different_answers(self):
        # Requirement 2, and the number --help quotes.  Measured this session
        # on tests/fixtures/diff_pair_4port.s4p at 5 GHz with ports 3 and 4
        # grounded: diag:L=1n -> 1.011955 nH, shared:L=1n -> 2.025922 nH,
        # 6.029 dB apart.  Mutation: building the shared matrix as a plain
        # diagonal (i.e. dropping the z_ret * ones term) makes the two equal
        # and the difference 0 dB.
        m_diag = self.m_under("diag:L=1n")
        m_shared = self.m_under("shared:L=1n")
        self.assertAlmostEqual(m_diag, 1.011955e-9, delta=1e-14)
        self.assertAlmostEqual(m_shared, 2.025922e-9, delta=1e-14)
        self.assertAlmostEqual(20 * math.log10(m_shared / m_diag), 6.029,
                               places=2)

    def test_the_shared_matrix_is_what_the_engines_builder_produces(self):
        ctx = engine_context()
        z = 1j * ctx.omega * 1e-9
        want = attrib.termination_impedance_shared_return(
            [0j] * ctx.n_elements, z)
        got, notes = ex._attr_zt(ctx, "shared", z)
        np.testing.assert_allclose(got, want)
        self.assertEqual(notes, [])

    def test_a_series_element_is_left_out_of_the_shared_block(self):
        # termination_impedance_shared_return assumes every element is a ball
        # sharing the plane.  Handing it a short_to would give the short a
        # return impedance and quietly stop it being a short.
        ctx = engine_context(gnd=(3,), shorts=((3, 4),))
        kinds = [e.kind for e in ctx.elements]
        self.assertIn("short", kinds)
        z = 1j * ctx.omega * 1e-9
        got, _notes = ex._attr_zt(ctx, "shared", z)
        short_i = kinds.index("short")
        self.assertEqual(complex(got[short_i, short_i]), 0j)
        self.assertTrue(all(complex(got[short_i, j]) == 0j
                            for j in range(got.shape[0])))

    def test_a_model_with_no_shunt_element_is_ignored_out_loud(self):
        rc, out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                            "--mport", "vic = 1", "--mport", "agg = 2",
                            "--short", "3-4", "--freq", "5",
                            "--attribute", "vic,agg",
                            "--attribute-ground-model", "shared:L=1n"])
        self.assertEqual(rc, 0, err)
        self.assertIn("no shunt element", " ".join(out.split()))

    def test_the_modelled_split_IS_delivered_and_sums_to_the_modelled_total(self):
        """
        Requirement 2's headline feature has to produce a SPLIT, not only a
        total.

        This test used to assert the opposite, and said so: `Decomposition`
        reconciled its own sum against compute_z_matrix's value for the
        DECLARED spec, so a shared return -- which doubles M -- read as a 100%
        algorithm disagreement and the per-element table vanished at exactly
        the setting the section exists for.  It is a cross-ALGORITHM check, and
        it is now taken on the declared configuration whatever model is in
        force, which is the only comparison that was ever meaningful.

        MEASURED here on diff_pair_4port.s4p at 5 GHz, probes 1/2, grounds 3/4,
        under `shared:L=1n`: M = 2.026 nH against 1.010 nH as declared
        (+6.05 dB), split 1.52 nH / 253 pH / 251 pH over ground 3 / ground 4 /
        the bare EM term -- and those three add up to the 2.026 nH above them,
        which is the property the whole section is for.

        Mutation: reconcile against `ctx.Zref` again and section 3b prints a
        paragraph of apology where the table is.
        """
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-ground-model", "shared:L=1n"])
        self.assertEqual(rc, 0, err)
        flat = " ".join(out.split())
        self.assertIn("3b. Ground model", out)
        self.assertIn("M as declared", flat)
        self.assertNotIn("per-element split under this model is not available",
                         flat)
        # the modelled section really does carry its own table
        body = out.split("3b. Ground model")[1].split("4. Sensitivity")[0]
        self.assertIn("ground port 3", body)
        self.assertIn("bare EM coupling", body)

        # ... and the terms in it sum to the modelled total, not the declared
        # one.  Read off the engine rather than re-parsed out of the columns.
        om = 2.0 * math.pi * engine_context().freq_hz
        ctx = engine_context(zt=attrib.termination_impedance_shared_return(
            0j, 1j * om * 1e-9, 2))
        dec = attrib.decompose(ctx, 0, 1, "M")
        self.assertTrue(dec.terms)
        self.assertFalse(dec.reference_applicable)
        total = sum(t.contribution for t in dec.terms)
        self.assertAlmostEqual(total.real, dec.total_sum.real,
                               delta=1e-9 * abs(dec.total_sum.real))
        self.assertAlmostEqual(dec.total_sum.real * 1e9, 2.026, delta=0.002)
        self.assertAlmostEqual(dec.total_reference.real * 1e9, 1.010,
                               delta=0.002)

    def test_section_1_still_reconciles_when_a_model_is_in_force(self):
        # Sections 1-3 run on the DECLARED spec precisely so that the
        # reconciliation keeps meaning something.  Mutation: running them on
        # the modelled context makes the residual read 1.01 and empties the
        # table the report exists to print.
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-ground-model", "shared:L=1n"])
        self.assertEqual(rc, 0, err)
        head = out.split("3b. Ground model")[0]
        self.assertIn("ground port 3", head)
        self.assertIn("ground port 4", head)
        self.assertNotIn("WITHHELD", head)

    def test_the_help_spells_out_the_default_and_why_it_is_a_hazard(self):
        parser = ex._make_arg_parser()
        text = parser.format_help()
        self.assertIn("--attribute-ground-model", text)
        for frag in ("DEFAULT", "share a return plane", "(1 + (n-1)k)",
                     "6.03 dB"):
            self.assertIn(frag, " ".join(text.split()))


# ---------------------------------------------------------------------------
# Joint / cumulative / sweep / cross-frequency
# ---------------------------------------------------------------------------


class TestJointAndCumulative(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_joint_"))
        self.path = self.tmp / "j.csv"
        rc, self.out, err = run(BASE + ["--attribute", "vic,agg",
                                        "--attribute-csv", str(self.path)])
        self.assertEqual(rc, 0, err)
        self.rows = read_csv(self.path)

    def rows_of(self, section: str) -> list[dict]:
        return [r for r in self.rows if r["section"] == section]

    def test_the_group_joint_effect_is_exact_and_not_the_sum_of_the_parts(self):
        # Requirement 9b/9c.  Measured on this fixture with both grounds
        # opened together: joint -759 pH against a sum of individuals of
        # -1.012 nH, i.e. 254 pH of non-additivity from only TWO elements.
        row = self.rows_of("group_joint")[0]
        self.assertEqual(row["group"], "--gnd 3,4")
        self.assertAlmostEqual(float(row["delta_re"]), -7.587e-10, delta=1e-13)
        self.assertIn("non_additivity=", row["extra"])
        self.assertIn("(2.5373", row["extra"])       # +253.7 pH

    def test_the_joint_result_matches_an_independent_group_joint_call(self):
        ctx = engine_context()
        want = attrib.group_joint(ctx, 0, 1, list(range(ctx.n_elements)),
                                  attrib.alt_open(), "M")
        row = self.rows_of("group_joint")[0]
        self.assertAlmostEqual(float(row["value_re"]), want.joint_value.real,
                               delta=1e-6 * abs(want.joint_value.real))

    def test_pairwise_non_additivity_is_reported_as_well_as_group_level(self):
        # Requirement 9c says GROUPS and PAIRS, and with 60 balls the pairwise
        # second difference is ~0 while the group effect is not -- which is
        # only visible if both are on screen.
        pairs = self.rows_of("pair_joint")
        self.assertEqual(len(pairs), 1)          # 2 elements -> 1 pair
        self.assertIn("+", pairs[0]["group"])
        self.assertIn("Pairwise non-additivity", self.out)

    def test_the_cumulative_curve_walks_k_and_reports_non_additivity(self):
        cum = self.rows_of("cumulative")
        ks = [int(r["extra"].split(";")[0].split("=")[1]) for r in cum]
        self.assertEqual(ks, [1, 2])
        # k=1 can have no non-additivity by definition; k=2 must.
        self.assertIn("non_additivity=0j", cum[0]["extra"].replace(" ", ""))
        self.assertNotIn("non_additivity=0j",
                         cum[1]["extra"].replace(" ", ""))

    def test_leave_one_out_starts_from_all_ideal(self):
        # Requirement 9e.  From all-open the first ground you add changes
        # everything and the rest change nothing; from all-ideal the number
        # that moves is the one carrying something.
        loo = self.rows_of("leave_one_out")
        self.assertEqual(len(loo), 2)
        self.assertTrue(all(r["alternative"] == "removed (from all-ideal)"
                            for r in loo))
        self.assertIn("Leave-one-out, starting from ALL elements ideal",
                      self.out)

    def test_the_mobius_sweep_reports_an_interval_and_both_endpoints(self):
        # Requirement 10: the INTERVAL is the headline, and both endpoints are
        # exact rather than "a very small" and "a very large" L.
        sweep = self.rows_of("sweep")
        self.assertEqual(len(sweep), 1)
        extra = dict(kv.split("=", 1) for kv in sweep[0]["extra"].split(";"))
        ctx = engine_context()
        want = attrib.sweep_mobius(ctx, 0, 1, list(range(ctx.n_elements)),
                                   "M", "L")
        self.assertAlmostEqual(float(extra["ideal"]), want.value_ideal.real,
                               delta=1e-6 * abs(want.value_ideal.real))
        self.assertAlmostEqual(float(extra["open"]), want.value_open.real,
                               delta=1e-6 * abs(want.value_open.real))
        self.assertIn("M(ideal, L=0)", self.out)
        self.assertIn("M(open,  L=inf)", self.out)
        self.assertIn("M over L in [0, inf) lies in", self.out)

    def test_a_sweep_that_leaves_the_bracket_says_so(self):
        # A series L resonates with the package's shunt C and M can leave the
        # [ideal, open] bracket entirely, which is exactly what a two-point
        # best-case/worst-case estimate gets wrong.  Measured on this fixture:
        # the unbounded sweep peaks at 2.14 mH of apparent M at L = 505 nH.
        sweep = self.rows_of("sweep")[0]
        self.assertIn("leaves_bracket=True", sweep["extra"])
        flat = " ".join(self.out.split())
        self.assertIn("LEAVES the [ideal, open] bracket", flat)
        self.assertIn("near-POLE", flat)

    def test_a_user_supplied_inductance_bounds_the_interval(self):
        # t_max is the caller's call and this CLI takes it from the largest
        # inductance the USER named -- it does not invent a "physical" range.
        path = self.tmp / "b.csv"
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-alt", "L=1n",
                                   "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        bounded = [r for r in read_csv(path)
                   if r["section"] == "sweep" and "0, 1" in r["alternative"]]
        self.assertEqual(len(bounded), 1)
        lo, hi = float(bounded[0]["value_re"]), float(bounded[0]["value_im"])
        # Inside the [ideal, open] bracket, unlike the unbounded sweep above.
        self.assertGreater(lo, 0.0)
        self.assertLess(hi, 1e-8)
        self.assertIn("bounded by the largest inductance YOU named", out)


class TestBigGroundGroup(unittest.TestCase):
    """
    A package-shaped spec: one --gnd flag carrying every ground ball.

    No fixture in the repo has more than four ports, and the interesting
    failures of this feature are all order-N in the ball count -- the
    order-60 joint effect the requirement is about, and the closed-form
    sweep's degree-|S| polynomial.  The file is therefore synthesised into a
    temp directory rather than added to tests/fixtures: it is a smoke target,
    not a reference, and nothing here asserts on its numbers.
    """

    @classmethod
    def setUpClass(cls):
        from generate_test_snp import write_touchstone
        from pkg_rlc.physics.core import y_to_s

        rng = np.random.default_rng(7)
        n, nf = 40, 20
        freqs = np.linspace(1e6, 10e9, nf)
        ell = 1e-9 * (0.5 + rng.random((n, n)))
        ell = (ell + ell.T) / 2
        cap = 1e-13 * (0.5 + rng.random(n))
        Y = np.zeros((nf, n, n), dtype=complex)
        for k, f in enumerate(freqs):
            w = 2 * math.pi * f
            Yk = (np.linalg.inv(0.1 * np.eye(n) + 1j * w * ell)
                  + np.diag(1j * w * cap))
            Y[k] = (Yk + Yk.T) / 2
        cls.tmp = Path(tempfile.mkdtemp(prefix="attrib_big_"))
        cls.path = cls.tmp / "big.s40p"
        write_touchstone(cls.path, freqs, y_to_s(Y, 50.0))
        cls.argv = ["--cli", str(cls.path), "--mode", "coupling",
                    "--mport", "vic = 1", "--mport", "agg = 2",
                    "--gnd", "3:1:40", "--freq", "5", "--attribute", "vic,agg"]

    def test_38_balls_in_one_group_report_without_falling_over(self):
        rc, out, err = run(self.argv + ["--attribute-alt", "L=0.3n",
                                        "--attribute-alt", "R=50",
                                        "--attribute-freqs", "1,9"])
        self.assertEqual(rc, 0, err)
        self.assertIn("38 declared, 1 group(s)", out)
        # The pair scan is capped at ATTR_PAIR_POOL elements, i.e. 28 pairs --
        # 38 elements would be 703 full re-solves.
        pairs = ex.ATTR_PAIR_POOL * (ex.ATTR_PAIR_POOL - 1) // 2
        self.assertIn(f"({pairs} pairs", out)
        # ... and the printed ranking is capped while the report says so.
        self.assertIn("more rows", out)

    def test_a_38_ball_group_sweep_has_FINITE_endpoints(self):
        """
        Requirement 10's headline, at the size requirement 9 is written for.

        This test used to assert the opposite -- that both endpoints came back
        NaN and the CLI apologised for it.  `sweep_mobius` expanded its
        degree-38 rational function, and with param="L" every eigenvalue is of
        order 1e-9, so the constant term of the denominator was a product of 38
        of them: measured, 5.98e-273 at 30 elements, 3.70e-309 at 34, exactly 0
        at 36.  `value_ideal` is num[-1]/den[-1], so it printed +inf and then
        NaN, while the interval -- taken over the interior points, which were
        all fine -- printed a confident span beside it.

        The sweep is now evaluated from its partial fractions, where t -> inf
        is simply c0 and t = 0 is one sum, so nothing has a product to
        underflow.  MEASURED on this same synthetic 40-port part: M(ideal) =
        -3.386 nH, M(open) = -286.7 pH, both finite, and the report says the
        expanded coefficients are gone rather than leaving them as garbage.

        Mutation: put the endpoints back on num[-1]/den[-1] and both read nan.
        """
        rc, out, err = run(self.argv)
        self.assertEqual(rc, 0, err)
        sweep = out.split("7. Series-inductance sweep")[1].split(
            "8. Cross-frequency")[0]
        self.assertNotIn("nan", sweep)
        flat = " ".join(sweep.split())
        self.assertNotIn("an endpoint of this sweep is UNDEFINED", flat)
        # both endpoints printed, both finite, and named as such
        self.assertIn("M(ideal, L=0)", flat)
        self.assertIn("M(open,  L=inf)", " ".join(sweep.split("\n")))
        # the diagnostic coefficients are declared absent rather than wrong
        self.assertIn("`num` / `den`) are NOT available", flat)

        # and the curve really is the network, at a value in between
        ctx = engine_context(fixture=str(self.path),
                             mports=("vic = 1", "agg = 2"),
                             gnd=tuple(range(3, 41)))
        sw = attrib.sweep_mobius(ctx, 0, 1, list(range(ctx.n_elements)),
                                 "M", "L")
        self.assertTrue(math.isfinite(sw.value_ideal.real))
        self.assertTrue(math.isfinite(sw.value_open.real))
        direct = attrib._zab(
            ctx, 0, 1,
            attrib._zt_with(ctx, range(ctx.n_elements),
                            1j * 2.0 * math.pi * ctx.freq_hz * 1e-9),
            range(ctx.n_elements)).imag / (2.0 * math.pi * ctx.freq_hz)
        self.assertAlmostEqual(sw.quantity_at(1e-9).real, direct,
                               delta=1e-9 * abs(direct))

    def test_numpys_own_warning_does_not_reach_stderr(self):
        # Same rule core follows for the open-probe divide: a RuntimeWarning
        # on fd 2 is the wrong channel, and the report's own line is the right
        # one.  Through a subprocess, because the in-process capture would not
        # see a warning written by the C layer.
        p = subprocess.run([sys.executable, "pkg_rlc_extractor.py"] + self.argv,
                           capture_output=True, text=True,
                           cwd=str(_HERE.parent), timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("RuntimeWarning", p.stderr)
        self.assertNotIn("attrib.py", p.stderr)


class TestCrossFrequency(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_freq_"))

    def test_freq_is_always_the_first_column_and_the_extras_follow(self):
        path = self.tmp / "f.csv"
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-freqs", "1,10",
                                   "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        seen: list[float] = []
        for r in read_csv(path):
            if r["section"] == "rank":
                f = round(float(r["freq_GHz"]), 6)
                if f not in seen:
                    seen.append(f)
        self.assertEqual(len(seen), 3)
        # The fixture is linspace(1 MHz, 10 GHz, 401), i.e. a 24.9975 MHz
        # step, so 5 and 1 GHz both snap to a neighbour while 10 GHz is the
        # last grid point exactly.
        self.assertAlmostEqual(seen[0], 5.0005, places=4)   # --freq, snapped
        self.assertAlmostEqual(seen[1], 1.0009, places=4)
        self.assertAlmostEqual(seen[2], 10.0, places=4)
        self.assertIn("Rank is stable across every frequency evaluated", out)

    def test_two_requests_inside_one_sweep_step_collapse_to_one_column(self):
        # The file's step is 24.9975 MHz, so --freq 5, 5.0 and 5.001 GHz all
        # snap to the SAME grid point.  Three identical columns would show a
        # ranking "confirmed" against itself.  The observable is that the
        # report falls back to its one-frequency wording, which it can only do
        # if the duplicates were dropped.
        rc, out, err = run(BASE + ["--attribute", "vic,agg",
                                   "--attribute-freqs", "5.0,5.001"])
        self.assertEqual(rc, 0, err)
        section = out.split("8. Cross-frequency rank stability")[1]
        self.assertEqual(section.count("5 GHz"), 1, section[:400])
        self.assertIn("Only one frequency was evaluated",
                      " ".join(section.split()))

    def test_a_withheld_split_leaves_no_ranking_and_says_so(self):
        # Probes shorted to their own far ends leaves Z_ab at the arithmetic
        # noise floor (measured: -1.76e-11 ohm against a 1.6e4 diagonal), the
        # engine withholds the split, and there is then nothing to rank.  An
        # empty table under "rank is stable" would be a verdict on a
        # comparison that never happened.
        rc, out, err = run(["--cli", DIFF_PAIR, "--mode", "coupling",
                            "--mport", "vic = 1", "--mport", "agg = 2",
                            "--short", "1-3,2-4", "--freq", "5",
                            "--attribute", "vic,agg",
                            "--attribute-freqs", "1,10"])
        self.assertEqual(rc, 0, err)
        section = " ".join(
            out.split("8. Cross-frequency rank stability")[1].split())
        self.assertIn("No ranking is available at any of these frequencies",
                      section)
        self.assertNotIn("Rank is stable", section)

    def test_the_ranking_really_is_re_evaluated_per_frequency(self):
        # Mutation: printing the primary frequency's ranks in every column.
        # Z_ab differs by more than a factor of 10 between 1 and 10 GHz here,
        # so the reported Z_ab line has to differ too.
        _rc, out, _err = run(BASE + ["--attribute", "vic,agg",
                                     "--attribute-freqs", "1,10"])
        line = [ln for ln in out.splitlines()
                if ln.strip().startswith("Z_ab per frequency")][0]
        self.assertIn("1 GHz", line)
        self.assertIn("10 GHz", line)
        self.assertNotEqual(line.count("j6.29"), line.count("j65.4") + 1)


# ---------------------------------------------------------------------------
# The CSV
# ---------------------------------------------------------------------------


class TestCsv(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attrib_csv_"))
        self.path = self.tmp / "all.csv"
        rc, self.out, err = run(BASE + ["--attribute", "vic,agg",
                                        "--attribute-alt", "L=1n",
                                        "--attribute-freqs", "1",
                                        "--attribute-csv", str(self.path)])
        self.assertEqual(rc, 0, err)

    def test_it_says_where_it_wrote(self):
        self.assertIn(f"Wrote attribution CSV: {self.path}", self.out)

    def test_every_row_has_every_declared_field(self):
        rows = read_csv(self.path)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(list(r.keys()), ex._ATTR_CSV_FIELDS)
            self.assertNotIn(None, r.values())

    def test_every_section_the_header_promises_is_present(self):
        # The comment block advertises the sections; a promise the file does
        # not keep is worse than no promise.
        sections = {r["section"] for r in read_csv(self.path)}
        for s in ("term", "total", "return_budget", "sensitivity",
                  "leave_one_out", "group_joint", "pair_joint", "cumulative",
                  "sweep", "transfer", "rank"):
            self.assertIn(s, sections)
        comments = csv_comment_block(self.path)
        for s in sections:
            self.assertIn(s, comments)

    def test_the_sign_convention_travels_with_the_export(self):
        # Requirement 11: "globally AND in every export".  A CSV outlives the
        # terminal it was printed next to.
        comments = " ".join(csv_comment_block(self.path).split())
        for phrase in ("V(+) - V(-)", "OUT of the structure into ground",
                       "absolute signs are a labelling choice"):
            self.assertIn(phrase, comments)

    def test_the_provenance_of_the_run_is_recorded(self):
        comments = csv_comment_block(self.path)
        self.assertIn("diff_pair_4port.s4p", comments)
        self.assertIn("vic = 1", comments)
        self.assertIn("gnd=3,4", comments)
        self.assertIn("Ground model: diag", comments)
        self.assertIn("grouping: row", comments)

    def test_an_unknown_field_name_raises_instead_of_vanishing(self):
        # The CSV builder takes keyword arguments, so a typo would silently
        # drop a column's worth of data.  Mutation: `row.update(kw)` with no
        # check in front of it.
        with self.assertRaises(KeyError):
            ex._attr_row("term", nonexistent_field="x")

    def test_non_finite_values_are_spelled_out_not_blank(self):
        self.assertEqual(ex._e(float("nan")), "nan")
        self.assertEqual(ex._e(float("inf")), "inf")
        self.assertEqual(ex._e(float("-inf")), "-inf")
        # A blank cell reads as "no data"; nan reads as "undefined", and the
        # difference is the whole point of the signed-value invariant.
        self.assertNotEqual(ex._e(float("nan")), "")

    def test_a_spec_with_no_elements_still_writes_a_usable_csv(self):
        path = self.tmp / "empty.csv"
        rc, out, err = run(["--cli", COUPLED_FLOAT, "--mode", "coupling",
                            "--mport", "c1 = 1 / 2", "--mport", "c2 = 3 / 4",
                            "--freq", "1", "--attribute", "c1,c2",
                            "--attribute-csv", str(path)])
        self.assertEqual(rc, 0, err)
        self.assertIn("nothing to attribute", " ".join(out.split()))
        sections = {r["section"] for r in read_csv(path)}
        # Sections 4-7 need an element to change; 8 and 9 do not, so the
        # exact transfer ratio is still there.  A spec with nothing declared
        # is still owed the one answer that does not depend on a declaration.
        self.assertEqual(sections,
                         {"term", "total", "return_budget", "transfer"})


# ---------------------------------------------------------------------------
# The real entry point
# ---------------------------------------------------------------------------


class TestSubprocess(unittest.TestCase):
    """
    The same commands through the actual process boundary.

    run() above calls main() in-process, which is what makes the rest of this
    file fast -- but it cannot catch an import that only fails from a clean
    interpreter, a `sys.exit` that never happens, or output written to the
    wrong stream.
    """

    REPO = str(_HERE.parent)

    def cli(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "pkg_rlc_extractor.py"] + argv,
            capture_output=True, text=True, cwd=self.REPO, timeout=300)

    def test_a_full_run_exits_zero_and_prints_the_report_on_stdout(self):
        p = self.cli(BASE + ["--attribute", "vic,agg",
                             "--attribute-alt", "L=0.3n"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ATTRIBUTION of the mutual impedance", p.stdout)
        self.assertIn("Sign convention", p.stdout)
        self.assertEqual(p.stderr, "")

    def test_a_refusal_exits_two_with_the_message_on_stderr(self):
        p = self.cli(BASE + ["--attribute", "vic,nope"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("nope", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_the_help_text_renders(self):
        p = self.cli(["--help"])
        self.assertEqual(p.returncode, 0)
        self.assertIn("attribution (--mode coupling only)", p.stdout)
        self.assertIn("--attribute-alt", p.stdout)


if __name__ == "__main__":
    unittest.main()
