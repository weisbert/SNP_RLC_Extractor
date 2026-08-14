"""
The `--cold-start` command line: the cold-start screen driven end to end.

`tests/test_attrib_coldstart.py` pins the arithmetic -- every closed form there
is checked against an honest re-solve through `compute_z_matrix`.  This file
pins the WRAPPER, and its job is the four things a wrapper can get wrong on its
own:

  * a bad flag is REFUSED with a message that names what was wrong.  Every
    refusal test asserts on the offending token AND on the way out; "raises
    SystemExit" or "exits 2" would have passed before any of this existed,
    because exit 2 is also what argparse produces for an unrelated typo;
  * the ORDER of the report is a requirement, not a layout preference.  The
    bracket answers "is any of this worth my time" and it is worthless printed
    after the ranking it exists to gate, so the ordering is pinned index by
    index rather than left to whoever next edits the function;
  * the numbers printed and exported are the ENGINE'S numbers.  Nothing here
    re-derives an impedance: every assertion compares against `pkg_rlc_attrib`
    called independently of the CLI.  A wrapper that quietly computed its own
    dM would be the worst available failure, because it would look right;
  * the CSV round-trips and is UNCAPPED.  The report prints ten screen rows out
    of a hundred and fifty-one and points at the CSV for the rest, so that
    pointer is a claim about a file.

Two structural claims get their own tests because they are exactly the ones a
tidy-up reverts.  The screen prints `COLD_RANK_ROWS` rows and the sentence that
covers the rest of the file counts from that same number -- print twenty and
the sentence re-describes ten ports the reader has just read.  And the pair
scan is not an optional refinement: the measured shield case is 90x the largest
single-port effect with the opposite sign, and a single-port ranking reports it
as two minor positive entries.

Numbers that ARE hard-coded were measured through the shipped module and the
measurement is written beside them.  Every guard here was mutation-checked; the
mutation that defeats it is named in the test.

The whole module is Tk-free: `main(["--cli", ...])` never reaches pkg_rlc_gui.
"""

from __future__ import annotations

import contextlib
import csv
import io
import math
import re
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
    y_to_s,
)
from generate_test_snp import write_touchstone  # noqa: E402

# The two planted networks come from tests/test_attrib_coldstart.py rather than
# being copied here.  They are the networks the contract's own measurements
# were taken on (+9.689 pH / -870.268 pH for the shield, the port-7 red herring
# for the planted case), and the numbers asserted below are those numbers -- a
# second copy of the builder would be free to drift away from the one the
# engine's tests pin, and then this file would be measuring a different
# network while quoting the same figures.
from test_attrib_coldstart import (  # noqa: E402
    F_TEST,
    PLANTED_NAMES,
    PLANTED_RED_HERRING,
    SHIELD_NAMES,
    planted_network,
    shield_network,
)

FIXTURES = _HERE / "fixtures"

DIFF_PAIR = str(FIXTURES / "diff_pair_4port.s4p")     # in_p/in_n/out_p/out_n
PI_2PORT = str(FIXTURES / "pi_2port.s2p")

#: The command every refusal test starts from: two single-ended probes on the
#: near ends of tests/fixtures/diff_pair_4port.s4p, read at 5 GHz.  Nothing is
#: declared -- the cold start is the ALL-OPEN question and declaring ports is
#: what --attribute is for.
BASE = ["--cli", DIFF_PAIR, "--mode", "coupling",
        "--mport", "vic = 1", "--mport", "agg = 2", "--freq", "5"]

F0 = float(F_TEST[0])


def run(argv: list[str]) -> tuple[int, str, str]:
    """main() in-process, with stdout and stderr captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ex.main(argv)
    return rc, out.getvalue(), err.getvalue()


def engine_inputs(fixture: str, mports=("vic = 1", "agg = 2"), gnd=(),
                  shorts=()):
    """
    (Y, freqs, terminations, port_names) built WITHOUT going through the CLI.

    Deliberately not by calling into pkg_rlc_extractor: a test that got its
    reference from the code under test would pass whatever that code did.
    """
    d = parse_touchstone(fixture)
    Y = s_to_y(d.s, d.z0)
    term = build_terminations_coupling(
        [parse_mport_spec(s) for s in mports], list(gnd), list(shorts),
        nports=d.nports)
    return Y, d.freqs, term, list(d.port_names)


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        body = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(body))


def csv_comment_block(path) -> str:
    with open(path, encoding="utf-8") as fh:
        return "".join(line for line in fh if line.startswith("#"))


def write_network(path: Path, Y: np.ndarray, freqs: np.ndarray,
                  names=None) -> None:
    """
    A synthetic Y as a Touchstone file the CLI can actually open.

    digits=17, like the coupled-inductor fixtures: these networks are read
    through a pinv with rcond=1e-12, and at the default 10 significant digits
    the round-trip noise floor sits above that cutoff.
    """
    write_touchstone(path, freqs, y_to_s(Y, 50.0),
                     port_names=(list(names) if names else None), digits=17)


def flat(text: str) -> str:
    """Whitespace-collapsed, so a wrapped sentence can be searched for."""
    return " ".join(text.split())


#: A DATA row of one of the port tables.  Deliberately not
#: `startswith("  port ")`: the header cell is the word 'port' too, so that
#: spelling counts eleven rows in a ten-row table and every cap assertion below
#: passes by one.
_PORT_ROW = re.compile(r"^ +port \d")


def port_rows(text: str) -> list[str]:
    return [l for l in text.splitlines() if _PORT_ROW.match(l)]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestColdStartRefusals(unittest.TestCase):
    """
    Every bad flag exits 2 and says what was wrong, by name.

    Asserting only on the exit code is what these must NOT do: argparse exits 2
    for any typo at all, so the token the user got wrong has to appear in the
    message or the test proves nothing.
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
        # screens 'vic' against itself.
        self.refuse(["--cold-start", "vic"],
                    "VICTIM,AGGRESSOR", "'vic'", "1 field(s)")

    def test_a_pair_with_three_fields_is_refused(self):
        self.refuse(["--cold-start", "vic,agg,extra"], "3 field(s)")

    def test_an_unknown_measurement_port_names_itself_and_the_alternatives(self):
        # The listing is the actionable half: without it the user has no way
        # to discover that the ports are called 'vic' and 'agg'.
        err = self.refuse(["--cold-start", "vic,bogus"], "'bogus'")
        self.assertIn("1 'vic'", err)
        self.assertIn("2 'agg'", err)

    def test_victim_equal_to_aggressor_is_refused_by_name(self):
        # The ENGINE allows it (its own fixture walk screens self terms on
        # 2-port files) and this CLI does not, on purpose: with a == b the
        # quantity printed as M is L_a, so the whole page would be headed 'M'
        # for a self inductance -- and --attribute, the other half of this flag
        # family, already refuses the same pair.  Mutation: dropping the check
        # produces a full, plausible, mislabelled report.
        err = self.refuse(["--cold-start", "vic,vic"], "'vic'", "MUTUAL")
        self.assertIn("two different --mport", err)

    def test_a_position_outside_the_mport_list_names_the_range(self):
        self.refuse(["--cold-start", "1,9"], "9", "1..2")

    def test_a_position_is_ONE_based(self):
        # The repo's rule is 1-based at the CLI boundary, and --cold-start
        # resolves through the same helper --attribute does.
        rc, _out, err = run(BASE + ["--cold-start", "1,2"])
        self.assertEqual(rc, 0, err)
        self.refuse(["--cold-start", "0,1"], "1..2")

    def test_a_top_of_one_is_refused_because_a_pair_needs_two(self):
        # Mutation: accepting it.  The engine's `usable[:1]` then scans ZERO
        # pairs and the report says "no pair could be scanned", which reads as
        # a property of the FILE rather than of the flag that was just typed.
        err = self.refuse(["--cold-start", "vic,agg", "--cold-start-top", "1"],
                          "--cold-start-top 1", "at least two ports")
        self.assertIn("not optional", err)
        self.assertIn("870.268 pH", err)      # why it is not optional

    def test_a_top_of_zero_or_negative_is_refused_too(self):
        self.refuse(["--cold-start", "vic,agg", "--cold-start-top", "0"],
                    "--cold-start-top 0")
        self.refuse(["--cold-start", "vic,agg", "--cold-start-top", "-4"],
                    "--cold-start-top -4")

    def test_a_negative_curve_depth_is_refused_and_zero_is_explained(self):
        # 0 is legal and means "every candidate", which is the engine's own
        # `max_k <= 0` semantics -- so the refusal has to say that rather than
        # leaving the user to guess that 0 might be "off".
        err = self.refuse(["--cold-start", "vic,agg",
                           "--cold-start-cumulative", "-3"],
                          "--cold-start-cumulative -3", "cannot be negative")
        self.assertIn("0 means every candidate port", err)

    def test_a_cold_start_flag_without_cold_start_is_refused_by_name(self):
        # Mutation: ignoring them silently makes '--cold-start-csv out.csv'
        # (with --cold-start forgotten) exit 0 having written nothing.
        err = self.refuse(["--cold-start-csv", "x.csv"], "--cold-start-csv",
                          "--cold-start VICTIM,AGGRESSOR")
        self.assertIn("only means", err)          # one flag: singular

    def test_several_dependent_flags_are_all_named(self):
        err = self.refuse(["--cold-start-top", "4",
                           "--cold-start-cumulative", "6"],
                          "--cold-start-top", "--cold-start-cumulative")
        self.assertIn("only mean", err)           # two flags: plural

    def test_a_cold_start_flag_points_at_COLD_START_not_at_attribute(self):
        """
        The two flag families have the same shape and the message must not
        send the user to the wrong one.

        Mutation: folding the cold-start flags into `_attr_dependent_flags`.
        The command below is a complete, valid --attribute run, so the
        attribution's own check would pass it straight through and
        --cold-start-csv would be silently ignored; and with --attribute
        absent it would say "only means something with --attribute", which is
        a true sentence about a flag the user never typed.
        """
        rc, _out, err = run(BASE + ["--attribute", "vic,agg",
                                    "--cold-start-csv", "x.csv"])
        self.assertEqual(rc, 2, err)
        self.assertIn("--cold-start-csv", err)
        self.assertIn("--cold-start VICTIM,AGGRESSOR", err)
        self.assertNotIn("--attribute VICTIM,AGGRESSOR", err)

    def test_the_dependent_check_does_not_need_a_readable_file(self):
        # It is a statement about argv, so it must not be gated on a parse.
        rc, _out, err = run(["--cli", str(FIXTURES / "no_such_file.s4p"),
                             "--mode", "coupling", "--cold-start-csv", "x.csv"])
        self.assertEqual(rc, 2)
        self.assertIn("--cold-start-csv", err)

    def test_cold_start_outside_coupling_mode_is_refused_like_attribute_is(self):
        rc, _out, err = run(["--cli", DIFF_PAIR, "--mode", "gnd",
                             "--porta", "1", "--gnd", "3,4",
                             "--cold-start", "a,b"])
        self.assertEqual(rc, 2)
        self.assertIn("--mode coupling", err)
        self.assertIn("--mport", err)

    def test_a_bad_pair_costs_a_message_and_not_a_solve(self):
        """
        Every command-line check runs BEFORE any linear algebra.

        Not a timing assertion -- those are flaky -- but a structural one: the
        refusal must arrive on a file the screen could never finish, and the
        153-port measurement (9.5 s for the four steps) is what makes that
        worth pinning.  `--cold-start-top 1` is checked after the pair is
        resolved and before the context is built, so it is the one that
        proves the ordering rather than just the first branch.
        """
        rc, out, err = run(BASE + ["--cold-start", "vic,agg",
                                   "--cold-start-top", "1"])
        self.assertEqual(rc, 2)
        self.assertNotIn("COLD START", out)
        self.assertIn("--cold-start-top 1", err)


# ---------------------------------------------------------------------------
# The order of the report
# ---------------------------------------------------------------------------


class TestReportOrder(unittest.TestCase):
    """
    The contract's order, pinned index by index.

    App-level ordering is the first thing a refactor loses and the last thing
    anybody notices, and here it is a requirement with a reason attached: the
    bracket is the number that decides whether the other three steps are worth
    reading, so printing it after them makes it decoration.
    """

    @classmethod
    def setUpClass(cls):
        cls.rc, cls.out, cls.err = run(BASE + ["--cold-start", "vic,agg"])

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_BRACKET_comes_before_the_RANKING(self):
        """
        The brief's own ordering requirement, on its own, with no other
        section involved.

        Mutation: move `_cold_print_screen` above `_cold_print_bracket`.
        """
        i_bracket = self.out.index("the whole question is worth")
        i_rank = self.out.index("|Z_ap|")
        self.assertLess(i_bracket, i_rank,
                        "the bracket must be printed before the ranking it "
                        "exists to gate")

    def test_the_sections_are_in_the_contract_order(self):
        order = ["COLD START -- which ports matter",
                 "Sign convention",
                 "STEP 0. The bracket",
                 "STEP 1. Every candidate port",
                 "STEP 2. Two ports at once",
                 "STEP 3. The greedy cumulative curve",
                 "Name-family suggestions",
                 "What this screen cannot find"]
        found = [self.out.index(s) for s in order]
        self.assertEqual(found, sorted(found),
                         "sections are out of order:\n" + "\n".join(order))

    def test_the_sign_convention_comes_before_any_signed_number(self):
        # Same requirement as the attribution report's section 0, and the
        # ORDER is the requirement: a reader who meets the first signed number
        # before the convention has already guessed.
        i_sign = self.out.index("Sign convention.")
        i_number = self.out.index("every non-probe port GROUNDED")
        self.assertLess(i_sign, i_number)
        self.assertIn(attrib.SIGN_CONVENTION_TEXT.split(".")[0], self.out)

    def test_the_bracket_caveat_is_printed_verbatim_with_the_numbers(self):
        # It is a single module constant precisely so that every surface
        # prints the same sentence; a paraphrase here would be a different
        # claim about what the bracket bounds.
        # Mutation: dropping it, or summarising it.
        for phrase in ("OPEN..IDEAL-GROUND family ONLY",
                       "not a bound over all possible terminations",
                       "Mobius arc leaves the segment"):
            self.assertIn(phrase, flat(self.out))

    def test_what_it_cannot_find_is_on_the_page_not_in_a_footnote(self):
        # The brief: "Print what it cannot find (three-port effects)."
        f = flat(self.out)
        self.assertIn("THREE OR MORE ports", f)
        self.assertIn("has no guarantee", f)
        self.assertIn("CANNOT EVALUATE NEW METAL", f)

    def test_the_baseline_is_named_before_any_delta(self):
        # Every number in the report is a delta FROM something, and on a
        # structure with no DC reference that something is not all-open.
        i_base = self.out.index("baseline")
        i_delta = self.out.index("STEP 1.")
        self.assertLess(i_base, i_delta)
        self.assertIn("every non-probe port OPEN", flat(self.out))

    def test_the_report_carries_both_coupling_columns(self):
        # Mutation: collapsing them into their product.  The measured red
        # herring has the largest |Z_ap| in its file and a negligible effect.
        self.assertIn("|Z_ap|", self.out)
        self.assertIn("|Z_pb|", self.out)
        self.assertIn("must not be read as their product", flat(self.out))


class TestOrderAgainstTheAttributionReport(unittest.TestCase):
    """
    --attribute and --cold-start in one command: two reports, in one order.

    They are not mutually exclusive -- they answer different questions from
    different baselines -- and the attribution has to stay glued to the
    coupling report it explains, so the cold start goes last.
    """

    @classmethod
    def setUpClass(cls):
        cls.rc, cls.out, cls.err = run(
            BASE + ["--gnd", "3,4", "--attribute", "vic,agg",
                    "--cold-start", "vic,agg"])

    def test_both_reports_are_printed(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertIn("ATTRIBUTION of the mutual impedance", self.out)
        self.assertIn("COLD START -- which ports matter", self.out)

    def test_the_attribution_stays_next_to_the_numbers_it_explains(self):
        # Mutation: printing the cold start first.  It reports on a DIFFERENT
        # network (all-open, every declaration set aside), so between the
        # coupling report and its own explanation it separates the two things
        # that belong together.
        i_coupling = self.out.index("Mutual coupling (per unordered pair)")
        i_attr = self.out.index("ATTRIBUTION of the mutual impedance")
        i_cold = self.out.index("COLD START -- which ports matter")
        self.assertLess(i_coupling, i_attr)
        self.assertLess(i_attr, i_cold)

    def test_the_cold_start_says_the_declared_grounds_are_NOT_in_force(self):
        """
        The spec declares --gnd 3,4 and the cold start ignores it on purpose.

        Mutation: dropping the engine's note from the header.  Then the report
        prints a bracket and a ranking for a network the user did not describe,
        under a header that shows their own --gnd on the command line, with
        nothing on screen to say the two differ.
        """
        cold = self.out[self.out.index("COLD START"):]
        f = flat(cold)
        self.assertIn("are NOT in force here", f)
        self.assertIn("ground on port 3; ground on port 4", f)
        # ... and the screen's own `declared` column still shows what was said.
        self.assertRegex(cold, r"port 3[^\n]*\bground\b")


# ---------------------------------------------------------------------------
# The numbers are the engine's numbers
# ---------------------------------------------------------------------------


class TestNumbersAreTheEnginesNumbers(unittest.TestCase):
    """
    Nothing the CLI prints or exports may be a second derivation.

    A wrapper that recomputed dM with its own 1/omega would agree with the
    engine on this fixture and disagree on the next one, and nothing would
    raise.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cold_cli_"))
        cls.path = cls.tmp / "screen.csv"
        cls.rc, cls.out, cls.err = run(
            BASE + ["--cold-start", "vic,agg",
                    "--cold-start-csv", str(cls.path)])
        Y, freqs, term, names = engine_inputs(DIFF_PAIR)
        cls.csc = attrib.cold_start_context(Y, freqs, term, 5e9,
                                            port_names=names)
        cls.want = attrib.cold_start_report(Y, freqs, term, 0, 1, 5e9,
                                            context=cls.csc)
        cls.rows = read_csv(cls.path)

    def close(self, got: complex, want: complex, what: str) -> None:
        # 6 significant digits is the CSV's own %.6e, the same format every
        # other exporter in this repo uses.
        self.assertLess(abs(got - want),
                        1e-6 * max(abs(want), 1e-30) + 1e-30,
                        f"{what}: {got} != {want}")

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_exported_bracket_is_the_engines_bracket(self):
        br = self.want.bracket
        row = [r for r in self.rows if r["section"] == "bracket"]
        self.assertEqual(len(row), 1)
        row = row[0]
        self.close(complex(float(row["value_re"]), float(row["value_im"])),
                   br.value_open, "value_open")
        # The grounded end is recoverable as value + delta, and is ALSO in
        # `extra` -- so a reader who never adds two columns still has it.
        got = (complex(float(row["value_re"]), float(row["value_im"]))
               + complex(float(row["delta_re"]), float(row["delta_im"])))
        self.close(got, br.value_grounded, "value_grounded")
        self.assertIn(f"grounded_re={br.value_grounded.real:.6e}",
                      row["extra"])
        # %.6e, so a relative tolerance -- places=9 on a 12 dB span asks
        # for more digits than the file has.
        self.assertLess(abs(float(row["delta_dB"]) - br.span_db),
                        1e-6 * abs(br.span_db) + 1e-12)

    def test_the_exported_screen_is_the_engines_screen_row_for_row(self):
        want = {r.port + 1: r for r in self.want.screen}
        got = {int(r["port"]): r for r in self.rows
               if r["section"] == "screen"}
        self.assertEqual(set(got), set(want))
        for p, w in want.items():
            g = got[p]
            self.close(complex(float(g["delta_re"]), float(g["delta_im"])),
                       w.delta, f"port {p} delta")
            self.close(complex(float(g["value_re"]), float(g["value_im"])),
                       w.value, f"port {p} value")
            # BOTH coupling columns, complex, uncollapsed.  Mutation: exporting
            # only their product, or only the magnitudes the table prints.
            self.close(complex(float(g["z_ap_re"]), float(g["z_ap_im"])),
                       w.z_ap, f"port {p} z_ap")
            self.close(complex(float(g["z_pb_re"]), float(g["z_pb_im"])),
                       w.z_pb, f"port {p} z_pb")
            self.close(complex(float(g["z_pp_re"]), float(g["z_pp_im"])),
                       w.z_pp, f"port {p} z_pp")

    def test_the_exported_pairs_are_the_engines_pairs(self):
        want = {(p.port_i + 1, p.port_j + 1): p for p in self.want.pairs}
        got = {(int(r["port"]), int(r["port_j"])): r for r in self.rows
               if r["section"] == "pair"}
        self.assertEqual(set(got), set(want))
        for key, w in want.items():
            g = got[key]
            self.close(complex(float(g["value_re"]), float(g["value_im"])),
                       w.delta_pair, f"{key} delta_pair")
            self.close(complex(float(g["delta_re"]), float(g["delta_im"])),
                       w.non_additivity, f"{key} non_additivity")
            self.assertAlmostEqual(float(g["threshold"]), w.threshold,
                                   places=12)
            self.assertEqual(g["flagged"], str(w.flagged))

    def test_the_exported_mirror_is_the_engines_mirror(self):
        want = {s.label: s for s in self.want.mirror}
        got = [r for r in self.rows if r["section"] == "mirror"]
        self.assertEqual(len(got), len(want))
        for g in got:
            label = g["extra"].split("element=")[1].split(";")[0]
            w = want[label]
            self.close(complex(float(g["delta_re"]), float(g["delta_im"])),
                       w.delta, f"{label} delta")
            # The mirror table names a PORT, not the element label: 'ground
            # port 3' is an internal description of a stamp, and the user is
            # deciding about port 3.
            self.assertTrue(g["port"], "a mirror row must carry its port")
            self.assertIn(f"port {g['port']}", label)

    def test_the_exported_curve_is_the_engines_curve(self):
        cv = self.want.curve
        got = sorted((r for r in self.rows if r["section"] == "cumulative"),
                     key=lambda r: int(r["k"]))
        self.assertEqual([int(r["k"]) for r in got], list(cv.k))
        self.assertEqual([int(r["port"]) - 1 for r in got], list(cv.order))
        for i, g in enumerate(got):
            self.close(complex(float(g["value_re"]), float(g["value_im"])),
                       cv.values[i], f"k={g['k']} value")
        self.assertIn(f"saturation_k={cv.saturation_k}", got[0]["extra"])

    def test_every_port_name_column_is_the_bare_name(self):
        """
        `port_name` means the file's own name for the port, in every section.

        Mutation: writing the rendered label 'port 3 (out_p)' in the
        cumulative section, which is what the table already shows.  A column
        that reads 'out_p' in five sections and 'port 3 (out_p)' in the sixth
        cannot be grouped or joined on, and the CSV exists to be joined.
        """
        names = {p + 1: r.name for p, r in
                 zip((r.port for r in self.want.screen), self.want.screen)}
        for r in self.rows:
            if r["section"] in ("screen", "mirror", "cumulative") and r["port"]:
                self.assertEqual(r["port_name"], names[int(r["port"])],
                                 f"{r['section']} row for port {r['port']}")

    def test_the_printed_bracket_ends_are_the_engines_ends(self):
        # The report prints through format_si, so this compares the RENDERED
        # string against the engine value rendered the same way -- which is
        # what makes it a test of the wiring rather than of format_si.
        from pkg_rlc.physics.core import format_si
        br = self.want.bracket
        self.assertIn(format_si(br.value_open.real, br.unit), self.out)
        self.assertIn(format_si(br.value_grounded.real, br.unit), self.out)


# ---------------------------------------------------------------------------
# The shield: why the pair scan is not optional
# ---------------------------------------------------------------------------


class TestShieldCase(unittest.TestCase):
    """
    A guard-ring segment brought out as two ports, driven through the real CLI.

    Measured (and re-measured here through a written Touchstone file):
    grounding either end alone is +9.69 pH and grounding both is -870 pH, 90x
    with the opposite sign.  A report that shows only step 1 tells the user
    those two ports are the least interesting in the file.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cold_shield_"))
        cls.path = cls.tmp / "shield.s6p"
        write_network(cls.path, shield_network(), F_TEST, SHIELD_NAMES)
        cls.csv_path = cls.tmp / "shield.csv"
        cls.argv = ["--cli", str(cls.path), "--mode", "coupling",
                    "--mport", "vic = 1 / 2", "--mport", "agg = 3 / 4",
                    "--freq", f"{F0 / 1e9:g}", "--cold-start", "vic,agg"]
        cls.rc, cls.out, cls.err = run(
            cls.argv + ["--cold-start-csv", str(cls.csv_path)])

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_step_1_alone_would_have_reported_the_shield_as_negligible(self):
        """
        The premise of the whole step, asserted rather than assumed.

        If this ever stops holding, the shield case has stopped being a case
        and the tests below are measuring something else.
        """
        step1 = self.out.split("STEP 1.")[1].split("STEP 2.")[0]
        self.assertIn("9.69 pH", step1)       # each end alone: +9.689 pH
        self.assertNotIn("-870", step1)

    def test_the_pair_is_flagged_and_the_sign_flip_is_named(self):
        # Mutation: dropping the pair section, or abs()-ing the pair effect,
        # or hiding `sign_flip`.  Each turns a 90x sign-reversing structure
        # into two minor positive entries.
        step2 = self.out.split("STEP 2.")[1].split("STEP 3.")[0]
        # On the ROW, not merely somewhere in the section: the paragraph under
        # the table explains what a sign flip is and contains the words, so a
        # mutation that empties the flag COLUMN leaves 'SIGN FLIP' on screen
        # attached to nothing.  (Measured: it did, and this test passed.)
        row = [l for l in step2.splitlines()
               if l.lstrip().startswith("ports 5,6") and "pH" in l]
        self.assertEqual(len(row), 1, step2)
        self.assertIn("SIGN FLIP", row[0])
        self.assertIn("-870 pH", row[0])      # both grounded
        self.assertIn("89.8x", row[0])        # vs the larger single
        self.assertIn("9.69 pH", row[0])      # ... and each one alone
        self.assertIn("move the answer the OTHER WAY", flat(step2))

    def test_the_pair_names_are_printed_IN_FULL_under_the_table(self):
        """
        The names go under the table because they do not fit in it.

        Mutation: put them back in the cell.  `_trunc` keeps the HEAD, so
        'guard_ring1' and 'guard_ring2' both render as 'guard_rin~' -- two
        indistinguishable stumps naming the one pair the section exists to
        point at, which is the same failure `freeze_label` documents.
        Measured: the table is 110 columns with the names in it and 89 with
        them under it.
        """
        step2 = self.out.split("STEP 2.")[1].split("STEP 3.")[0]
        self.assertIn("ports 5,6 = guard_ring1, guard_ring2", step2)
        self.assertNotIn("guard_rin~", step2)
        widest = max(len(l) for l in step2.splitlines())
        self.assertLessEqual(widest, 95, "the pair section got wider")

    def test_the_threshold_is_reported_next_to_the_verdict(self):
        # "flagged" with no threshold on screen is an opinion.  Measured:
        # 4.84 pH here, and the pair clears it at 890 pH, i.e. 184x.
        f = flat(self.out.split("STEP 2.")[1])
        self.assertIn("non-additivity exceeds 4.84 pH", f)
        pair = [r for r in read_csv(self.csv_path) if r["section"] == "pair"]
        self.assertEqual(len(pair), 1)
        self.assertAlmostEqual(float(pair[0]["threshold"]), 4.8446e-12,
                               delta=1e-15)

    def test_the_mirror_direction_is_run_and_reported(self):
        # Mutation: dropping it.  From all-grounded each end reads +880 pH --
        # the number that says "these two are one thing" -- and that is the
        # OPPOSITE failure from the one step 1 has: a set sharing a return
        # reads ~0 one at a time from all-grounded, not from all-open.
        step2 = self.out.split("STEP 2.")[1].split("STEP 3.")[0]
        self.assertIn("Mirror: from ALL candidate ports GROUNDED", step2)
        self.assertEqual(step2.count("880 pH"), 2)   # one row per end

    def test_the_curve_saturates_at_two_and_says_so(self):
        step3 = self.out.split("STEP 3.")[1]
        self.assertIn("Saturation: 2 of 2 candidate port(s)", flat(step3))
        # ... and the tolerance it was judged against is on screen, so
        # "saturated" is a number and not a verdict.
        self.assertIn("within 10%", flat(step3))

    def test_the_name_family_is_a_SUGGESTION_and_changed_no_number(self):
        f = flat(self.out)
        self.assertIn("share the name family 'guard_ring'", f)
        self.assertIn("if they are one structure, group them", f)
        self.assertIn("the grouping is a suggestion from the port NAMES", f)
        self.assertIn("this tool will not make it", f)

    def test_removing_the_port_NAMES_moves_no_number(self):
        """
        The honesty rule, at the CLI: a name may propose, never decide.

        The same S-matrix written without the '! Port[i] =' lines must produce
        the same bracket, the same ranking, the same pairs, the same mirror and
        the same curve -- only the labels lose their names, and only the
        name-family section disappears.

        Mutation: letting a flagged family reorder or rewrite the screen.
        """
        bare_snp = self.tmp / "shield_noname.s6p"
        write_network(bare_snp, shield_network(), F_TEST, None)
        bare_csv = self.tmp / "shield_noname.csv"
        rc, out, err = run(["--cli", str(bare_snp), "--mode", "coupling",
                            "--mport", "vic = 1 / 2", "--mport", "agg = 3 / 4",
                            "--freq", f"{F0 / 1e9:g}",
                            "--cold-start", "vic,agg",
                            "--cold-start-csv", str(bare_csv)])
        self.assertEqual(rc, 0, err)
        # The family section is the ONLY thing that may differ, so it is the
        # only thing dropped -- along with the two columns that carry a name.
        cols = [c for c in ex._COLD_CSV_FIELDS
                if c not in ("port_name", "extra")]

        def numeric(path):
            return [tuple(r[c] for c in cols) for r in read_csv(path)
                    if r["section"] != "family"]

        self.assertEqual(numeric(bare_csv), numeric(self.csv_path))
        # ... and the unnamed run says the grouping was not proposed rather
        # than silently omitting the section.
        self.assertIn("No name family was proposed", flat(out))


# ---------------------------------------------------------------------------
# The red herring: two coupling columns, not their product
# ---------------------------------------------------------------------------


class TestPlantedRedHerring(unittest.TestCase):
    """
    The port with the largest coupling to the victim in the whole file is
    worthless, and the report has to show why rather than merely not ranking it
    first.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cold_planted_"))
        cls.path = cls.tmp / "planted.s12p"
        write_network(cls.path, planted_network(), F_TEST, PLANTED_NAMES)
        cls.rc, cls.out, cls.err = run(
            ["--cli", str(cls.path), "--mode", "coupling",
             "--mport", "vic = 1 / 2", "--mport", "agg = 3 / 4",
             "--freq", f"{F0 / 1e9:g}", "--cold-start", "vic,agg"])
        cls.step1 = cls.out.split("STEP 1.")[1].split("STEP 2.")[0]

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_bracket_is_the_measured_25_67_dB(self):
        # The contract's own number, arrived at through the shipped module and
        # a real Touchstone round trip.
        self.assertIn("25.67 dB", self.out)

    def test_the_two_real_paths_rank_first_and_second(self):
        lines = port_rows(self.step1)
        self.assertGreaterEqual(len(lines), 2)
        self.assertIn("port 5 (aux1)", lines[0])
        self.assertIn("port 6 (aux2)", lines[1])

    def test_the_red_herring_is_NOT_first_and_its_two_columns_disagree(self):
        """
        Mutation: rank by |Z_ap| alone (or print only the product).

        Port 7 has |Z_ap| = 34.78, the largest in the file and 67% more than
        the real path's 20.87, and |Z_pb| = 0.0383.  Its true effect is
        -378 fH against the real path's -395 pH -- three orders of magnitude.
        Ranking on coupling to the victim puts it FIRST.
        """
        lines = port_rows(self.step1)
        rank = [i for i, l in enumerate(lines)
                if f"port {PLANTED_RED_HERRING} " in l]
        self.assertEqual(len(rank), 1)
        self.assertGreater(rank[0], 1, "the red herring must not rank first")
        row = lines[rank[0]]
        self.assertIn("34.78", row)      # largest |Z_ap| in the file
        self.assertIn("0.0383", row)     # ... and a negligible |Z_pb|
        self.assertIn("-378 fH", row)

    def test_no_pair_is_flagged_and_that_is_reported_as_a_RESULT(self):
        # There is no pair mechanism planted here, so the right answer is
        # "none", and it has to be said rather than left as an empty table.
        # Mutation: a threshold of 0 flags all 28.
        step2 = self.out.split("STEP 2.")[1].split("STEP 3.")[0]
        self.assertIn("28 pair(s) scanned", step2)
        self.assertIn("No pair exceeds the threshold", step2)
        self.assertIn("That is a result, not a gap", flat(step2))

    def test_the_curve_saturates_at_two_of_eight(self):
        self.assertIn("Saturation: 2 of 8 candidate port(s)",
                      flat(self.out.split("STEP 3.")[1]))


# ---------------------------------------------------------------------------
# Caps, the negative result, and the uncapped CSV
# ---------------------------------------------------------------------------


class TestCapsAndTheNegativeResult(unittest.TestCase):
    """
    A file with more candidates than the report prints.

    No fixture in the repo has more than four ports, and every interesting
    property of the caps is about what happens past the tenth row, so the file
    is synthesised into a temp directory: it is a smoke target, not a
    reference, and nothing here asserts on its physics.
    """

    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(17)
        n = 20
        freqs = np.array([5e9])
        w = 2 * math.pi * float(freqs[0])
        ell = 1e-9 * (0.5 + rng.random((n, n)))
        ell = (ell + ell.T) / 2 + np.eye(n) * 2e-9
        Yk = (np.linalg.inv(0.3 * np.eye(n) + 1j * w * ell)
              + np.eye(n) * 1j * w * 1e-14)
        cls.tmp = Path(tempfile.mkdtemp(prefix="cold_caps_"))
        cls.path = cls.tmp / "many.s20p"
        write_network(cls.path, ((Yk + Yk.T) / 2)[None], freqs, None)
        cls.csv_path = cls.tmp / "many.csv"
        cls.base = ["--cli", str(cls.path), "--mode", "coupling",
                    "--mport", "vic = 1", "--mport", "agg = 2", "--freq", "5",
                    "--cold-start", "vic,agg"]
        cls.rc, cls.out, cls.err = run(
            cls.base + ["--cold-start-csv", str(cls.csv_path)])
        cls.rows = read_csv(cls.csv_path)

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_screen_prints_exactly_COLD_RANK_ROWS_rows(self):
        step1 = self.out.split("STEP 1.")[1].split("STEP 2.")[0]
        lines = port_rows(step1)
        self.assertEqual(len(lines), ex.COLD_RANK_ROWS)
        self.assertIn(f"(the top {ex.COLD_RANK_ROWS} of 18", step1)

    def test_the_negative_result_counts_from_the_rows_that_were_PRINTED(self):
        """
        The one number in this report that two pieces of code have to agree on.

        `cold_start_report` builds the sentence with
        `cold_start_negative_result(rows, unit)` at its default
        `top=COLD_START_SHOW`, so the count in it is `len(rows) - that`.  Print
        any other number of rows and the sentence is wrong in a way nobody can
        see: at 20 printed rows it re-describes ten ports the reader has just
        read as "the other ports"; at 5 it leaves five out of both halves.

        Mutation: `COLD_RANK_ROWS = ex.ATTR_RANK_ROWS` (20).  The printed row
        count and the "other N" then disagree by ten and this test goes red.
        """
        n_screen = len([r for r in self.rows if r["section"] == "screen"])
        self.assertEqual(n_screen, 18)
        step1 = self.out.split("STEP 1.")[1].split("STEP 2.")[0]
        printed = len(port_rows(step1))
        self.assertIn(f"The other {n_screen - printed} port(s)", flat(step1))
        self.assertEqual(printed, ex.COLD_RANK_ROWS)

    def test_the_negative_result_says_what_it_MEANS_not_only_a_number(self):
        # The brief calls the negative result valuable in its own right: it is
        # permission to stop looking, and a bare count is not that.
        f = flat(self.out)
        self.assertIn("would each move the answer by at most", f)
        self.assertIn("the largest being port", f)

    def test_the_CSV_has_every_candidate_even_though_the_report_has_ten(self):
        # This is what makes the "(all in --cold-start-csv, which has no cap)"
        # pointer a true claim about a file.
        # Mutation: exporting `cs.screen[:COLD_RANK_ROWS]`.
        ports = sorted(int(r["port"]) for r in self.rows
                       if r["section"] == "screen")
        self.assertEqual(ports, list(range(3, 21)))

    def test_the_mirror_is_capped_on_screen_and_uncapped_in_the_CSV(self):
        step2 = self.out.split("STEP 2.")[1].split("STEP 3.")[0]
        mirror = step2.split("Mirror:")[1]
        rows = port_rows(mirror)
        self.assertEqual(len(rows), ex.COLD_MIRROR_ROWS)
        self.assertIn("more (all of them are in --cold-start-csv", mirror)
        self.assertEqual(
            len([r for r in self.rows if r["section"] == "mirror"]), 18)

    def test_cold_start_top_changes_how_many_pairs_are_scanned(self):
        # Mutation: ignoring the flag and always using COLD_START_TOP_K.
        rc, out, err = run(self.base + ["--cold-start-top", "4"])
        self.assertEqual(rc, 0, err)
        self.assertIn("6 pair(s) scanned over the top 4", out)
        self.assertIn("(--cold-start-top 4)", out)
        rc, out, err = run(self.base + ["--cold-start-top", "12"])
        self.assertEqual(rc, 0, err)
        self.assertIn("66 pair(s) scanned over the top 12", out)

    def test_the_scanned_depth_names_what_was_SCANNED_not_the_flag(self):
        # With 18 candidates and --cold-start-top 30 the engine scans 18, and
        # saying "the top 30" would name a depth that does not exist.
        rc, out, err = run(self.base + ["--cold-start-top", "30"])
        self.assertEqual(rc, 0, err)
        self.assertIn("153 pair(s) scanned over the top 18", out)
        self.assertIn("(--cold-start-top 30)", out)

    def test_cold_start_cumulative_sets_the_depth_of_the_curve(self):
        # The curve is ALWAYS run -- it is step 3 and at 151 candidates it is
        # 132 ms of a 9.5 s report -- so the flag is a depth, not a switch.
        # Mutation: ignoring it and always using COLD_START_MAX_K (12).
        rc, out, err = run(self.base + ["--cold-start-cumulative", "3"])
        self.assertEqual(rc, 0, err)
        step3 = out.split("STEP 3.")[1]
        ks = [l.split()[0] for l in step3.splitlines()
              if l.strip()[:1].isdigit() and "port" in l]
        self.assertEqual(ks, ["1", "2", "3"])

    def test_a_curve_depth_of_zero_means_every_candidate(self):
        # The engine's own `max_k <= 0` semantics, which the refusal message
        # for a negative value promises.
        rc, out, err = run(self.base + ["--cold-start-cumulative", "0"])
        self.assertEqual(rc, 0, err)
        step3 = out.split("STEP 3.")[1]
        ks = [l.split()[0] for l in step3.splitlines()
              if l.strip()[:1].isdigit() and "port" in l]
        self.assertEqual(ks, [str(i) for i in range(1, 19)])

    def test_the_curve_runs_with_no_flag_at_all(self):
        # Mutation: making step 3 opt-in.  It is the only step that answers
        # "how many ports actually matter", and a default report missing one of
        # the contract's four steps is a different feature.
        self.assertIn("STEP 3.", self.out)
        step3 = self.out.split("STEP 3.")[1]
        self.assertIn("port grounded", step3)
        self.assertRegex(step3, r"\n\s+1\s+port \d+")


# ---------------------------------------------------------------------------
# The CSV as a file
# ---------------------------------------------------------------------------


class TestColdStartCsv(unittest.TestCase):
    """The export is the only uncapped surface, so it has to be readable."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cold_csv_"))
        cls.path = cls.tmp / "out.csv"
        cls.rc, cls.out, cls.err = run(
            BASE + ["--gnd", "3", "--cold-start", "vic,agg",
                    "--cold-start-csv", str(cls.path)])

    def test_it_exits_zero_and_says_where_it_wrote(self):
        self.assertEqual(self.rc, 0, self.err)
        self.assertIn(f"Wrote cold-start CSV: {self.path}", self.out)

    def test_every_section_is_present_and_round_trips(self):
        rows = read_csv(self.path)
        got = {r["section"] for r in rows}
        self.assertEqual(got, {"bracket", "screen", "pair", "mirror",
                               "cumulative"})
        # DictReader round-trips means every row has every field, no more.
        for r in rows:
            self.assertEqual(set(r), set(ex._COLD_CSV_FIELDS))

    def test_an_unknown_field_name_RAISES_rather_than_vanishing(self):
        # Mutation: `row.update(kw)` without the check.  A typo'd field then
        # silently drops the value it was carrying.
        with self.assertRaises(KeyError):
            ex._cold_row("screen", not_a_field="x")

    def test_the_header_carries_the_three_texts_that_must_outlive_the_terminal(self):
        # A CSV outlives the terminal it was printed in, so the sign
        # convention, the bracket's honesty clause and the blind spot travel
        # with it -- the same rule the attribution CSV follows.
        head = flat(csv_comment_block(self.path))
        self.assertIn(flat(attrib.SIGN_CONVENTION_TEXT)[:60], head)
        self.assertIn(flat(attrib.COLD_START_BRACKET_CAVEAT)[:60], head)
        self.assertIn(flat(attrib.COLD_START_BLIND_SPOT_TEXT)[:60], head)

    def test_the_header_names_the_baseline_and_the_dropped_declarations(self):
        # --gnd 3 is on the command line and is NOT in force in this file's
        # numbers.  A CSV that recorded the flags without that sentence would
        # be read months later as the numbers for the declared spec.
        head = flat(csv_comment_block(self.path))
        self.assertIn("Baseline:", head)
        self.assertIn("every non-probe port OPEN", head)
        self.assertIn("are NOT in force here", head)

    def test_the_header_explains_what_value_and_delta_mean_per_section(self):
        # They genuinely differ -- a pair row's `value` is the joint DELTA --
        # so the file says so rather than leaving a reader to infer it from a
        # column name that is right in five sections out of six.
        head = flat(csv_comment_block(self.path))
        self.assertIn("value=open end, delta=grounded-open", head)
        self.assertIn("value=change with BOTH grounded, delta=non-additivity",
                      head)

    def test_the_declared_column_records_what_the_SPEC_said(self):
        # Port 3 is on --gnd and the screen still hypothesises it as one
        # candidate among the rest; the column is how a reader sees both.
        rows = {int(r["port"]): r for r in read_csv(self.path)
                if r["section"] == "screen"}
        self.assertEqual(rows[3]["declared"], "ground")
        self.assertEqual(rows[4]["declared"], "open")


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


class TestNothingToScreen(unittest.TestCase):
    """
    Every port is a probe: 0 candidates, 0 dB, and a report that says why.

    Exit 0, not an error: "there is nothing here to decide" is an answer, and
    the one thing it must not do is print a 0 dB bracket that reads as
    "measured, and nothing matters".
    """

    @classmethod
    def setUpClass(cls):
        cls.rc, cls.out, cls.err = run(
            ["--cli", PI_2PORT, "--mode", "coupling",
             "--mport", "a1 = 1", "--mport", "a2 = 2", "--freq", "1",
             "--cold-start", "a1,a2"])

    def test_it_exits_zero(self):
        self.assertEqual(self.rc, 0, self.err)

    def test_the_structural_zero_dB_is_labelled_as_structural(self):
        """
        Mutation: skipping `Bracket.notes`.  The bracket then reads
        '0 dB', which is exactly what a file whose ports are all irrelevant
        looks like -- the opposite of "nothing was measured".

        The assertion is on the STEP 0 block, not on the whole report: step 1's
        empty branch says the same thing in its own words, so a report-wide
        search passes with the bracket's note gone.  (Measured: it did.)
        """
        step0 = self.out.split("STEP 0.")[1].split("STEP 1.")[0]
        self.assertIn("the whole question is worth", step0)
        self.assertIn("No candidate port could be screened", flat(step0))
        self.assertIn("0 dB by construction, not by measurement", flat(step0))

    def test_each_empty_step_says_which_emptiness_it_is(self):
        f = flat(self.out)
        self.assertIn("There is no candidate port to screen", f)
        self.assertIn("No pair could be scanned", f)
        self.assertIn("nothing to open", f)
        self.assertIn("The curve is empty", f)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
