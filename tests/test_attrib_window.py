"""
The Attribution window: `pkg_rlc_attrib_gui`.

Two halves, and the split is deliberate.

  * PURE -- the refusal, the sign rendering, the monospace table model, the
    reconciliation verdict, the provenance block, the folding rule, the
    candidate parser, the across-frequency verdict, the CSV records and the
    session state.  None of it touches a widget, so it runs in the `--fast`
    shard's company and it can be reasoned about without a display.
  * TK -- everything that is a claim about PIXELS or about Tk's own
    behaviour, measured off a MAPPED window.  A withdrawn root answers 0 to
    every geometry query, which is exactly the wrong answer being ruled out,
    so the App is deiconified and so is the Toplevel (which is NOT transient,
    so the WM will not withdraw it with its master either way -- that is its
    own test below).

Every number written down here was MEASURED in this environment (Tk 8.6, vista
theme, TkDefaultFont = Microsoft YaHei UI 9, tk scaling 1.333) and the
measurement is beside it.  Every guard was mutation-checked; the mutation that
defeats it is named in the test's docstring or in a comment.
"""

from __future__ import annotations

import math
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_attrib as at  # noqa: E402
import pkg_rlc_attrib_gui as ag  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    MeasPortRow,
    ROLE_ELEMENT,
    parse_touchstone,
)
from pkg_rlc_gui import (  # noqa: E402
    App,
    FileEntry,
    PORT_ROLE_FG,
    RESULTS_SWATCH,
    TraceConfig,
    _config_signature,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Two coupled coils, probes on 1 and 3, ports 2 and 4 grounded.  Chosen because
# it produces a decomposition with a bare EM term AND two element terms whose
# shares are large and different (measured: bare 24.68%, ground port 2 50.26%,
# ground port 4 25.06% of M = 821 pH at 5.1 GHz), so a table that silently
# dropped or reordered a row would be visible.
FIXTURE = "coupled_4port_diff.s4p"


#: The interpreter's `tk scaling` BEFORE anything in this file touches it.
#:
#: `tk scaling` is PROCESS-WIDE on Windows, not per-interpreter -- measured:
#: `app2.tk.call("tk", "scaling", 2.0)` in `_scaled()` takes the FIRST App's
#: reading from 1.333 to 2.001 as well, and Tk then derives a new default font
#: size from it, so a third interpreter's `TkDefaultFont` starts at 6 pt rather
#: than 9.  Two guards were sitting on that: the 100% minimum read (720, 430)
#: instead of (720, 420) because a 9 pt Consolas linespace went 14 -> 22 px on
#: a window nobody had rescaled, and the sign strip was measured against a
#: half-rescaled font.  `_scaled()` restores this in a cleanup.
TK_SCALING = 0.0


def _tk_ok() -> bool:
    global TK_SCALING
    try:
        r = tk.Tk()
        TK_SCALING = float(r.tk.call("tk", "scaling"))
        r.destroy()
        return True
    except Exception:
        return False


TK_OK = _tk_ok()


# ============================================================================
# Fakes, so the pure half needs neither a file nor a solve
# ============================================================================

def _elem(index: int, kind: str = "ground", port: int = 2,
          source: str = "conn row 1") -> at.Element:
    # A series element is TWO-terminal and `Element.describe()` indexes both,
    # so the fake has to be shaped like the real thing or it raises IndexError
    # before any assertion is reached.
    ports = ((port - 1, port) if kind in ("short", "lumped_between")
             else (port - 1,))
    return at.Element(kind=kind, ports=ports, source=source,
                      ideal=True, index=index)


def _term(el, contrib: complex, share: float = 0.0,
          quad: float = 0.0) -> at.Term:
    return at.Term(element=el, contribution=contrib, current=complex(1e-3, 0),
                   trans_z=complex(-1.0, 0), share_inline=share,
                   share_quad=quad)


def fake_dec(terms, quantity: str = "M", unit: str = "H",
             total: complex = 1e-9 + 0j, resid: float = 1e-13,
             floor: float = 1e-10, applicable: bool = True,
             trustworthy: bool = True) -> at.Decomposition:
    """A `Decomposition` built by hand -- no file, no solve, no engine."""
    rb = at.ReturnBudget(em_reference=1.0, declared=0.5, declared_all=0.5,
                         em_fraction=0.5, dominant=False,
                         note="Return path: half and half.")
    return at.Decomposition(
        victim="vic", aggressor="agg", freq_hz=5.1e9, requested_hz=5.1e9,
        quantity=quantity, unit=unit, total_reference=total, total_sum=total,
        residual_rel=resid, residual_floor=floor, terms=list(terms),
        return_budget=rb, reference_note="ref note",
        split_trustworthy=trustworthy, reference_applicable=applicable)


def fake_prov(**kw) -> ag.Provenance:
    base = dict(trace_id=3, trace_label="coil", file_label="pkg.s4p",
                run_number=7, spec_matches_run=True, victim="vic",
                aggressor="agg", quantity="M", requested_hz=5.6e9,
                actual_hz=5.6e9, spec_text="1 signal vic +\n2 ground",
                units_mode="smart", signature=())
    base.update(kw)
    return ag.Provenance(**base)


class _FakeTrace:
    """The duck `attribution_refusal` reads.  Deliberately not a TraceConfig:
    the refusal is the one function the hooks agent calls before anything is
    built, and it must not need the GUI's dataclass."""

    def __init__(self, **kw):
        self.label = "t1"
        self.file_label = "pkg.s4p"
        self.frozen = False
        self.stale = False
        self.Z = object()
        self.Zmat = object()
        #: A healthy trace has TWO of these, and the refusal reads them.
        #: `Zmat is not None` alone is not the question: `_on_calculate` routes
        #: on `tc.mode == 6 or n_mports > 1`, so a mode-6 trace with one
        #: measurement port comes back with a real (F, 1, 1) `Zmat` and no pair
        #: to attribute -- see
        #: `test_a_ONE_by_ONE_Zmat_is_still_one_measurement_port`.
        self.mport_names = ("vic", "agg")
        self.__dict__.update(kw)


# ============================================================================
# PURE: the refusal (rule 7)
# ============================================================================

class TestRefusal(unittest.TestCase):
    def test_no_trace_at_all(self):
        self.assertIn("Select a trace", ag.attribution_refusal(None, None))

    def test_a_healthy_trace_is_not_refused(self):
        self.assertIsNone(ag.attribution_refusal(_FakeTrace(), object()))

    def test_a_frozen_trace_is_refused_by_name(self):
        why = ag.attribution_refusal(_FakeTrace(frozen=True), object())
        self.assertIn("frozen snapshot", why)
        self.assertIn("Unfreeze", why)

    def test_frozen_is_tested_BEFORE_the_file(self):
        """A frozen trace can never be attributed whatever the file is doing.

        Mutation: move the `frozen` branch below the `file_entry is None` one
        and this reads "the Touchstone file ... is not loaded", which sends the
        user off to load a file that will not help.
        """
        why = ag.attribution_refusal(_FakeTrace(frozen=True), None)
        self.assertIn("frozen snapshot", why)
        self.assertNotIn("not loaded", why)

    def test_a_missing_file_is_refused(self):
        why = ag.attribution_refusal(_FakeTrace(), None)
        self.assertIn("pkg.s4p", why)
        self.assertIn("not loaded", why)

    def test_no_numbers_at_all(self):
        why = ag.attribution_refusal(_FakeTrace(Z=None, Zmat=None), object())
        self.assertIn("no numbers yet", why)

    def test_one_measurement_port_says_so_rather_than_no_numbers(self):
        """Z and no Zmat is the compute_z path, i.e. exactly one probe.

        Mutation: fold this into the "no numbers yet" branch and a user with a
        perfectly good single-port trace is told to Calculate, which they just
        did, and told nothing about the second port they actually need.
        """
        why = ag.attribution_refusal(_FakeTrace(Zmat=None), object())
        self.assertIn("only one measurement port", why)
        self.assertIn("victim AND an aggressor", why)

    def test_a_ONE_by_ONE_Zmat_is_still_one_measurement_port(self):
        """The other route to the same shortfall, and it used to slip through.

        `_on_calculate` takes the coupling path on `tc.mode == 6` whatever the
        measurement-port count, so a mode-6 trace with one port comes back with
        a perfectly real (F, 1, 1) `Zmat`.  Testing `Zmat is None` alone let it
        past, and what stopped it was `open_attribution_window`'s
        "fewer than two measurement port names cached. Calculate it again."
        backstop -- a message about an internal inconsistency that had not
        happened, advising something that cannot help.

        Mutation: drop `or n_names < 2` from the condition and this is the only
        test in the class that goes red.
        """
        why = ag.attribution_refusal(_FakeTrace(mport_names=("vic",)),
                                     object())
        self.assertIn("only one measurement port", why)
        self.assertNotIn("cached", why)

    def test_a_stale_trace_is_refused_by_name(self):
        why = ag.attribution_refusal(_FakeTrace(stale=True), object())
        self.assertIn("edited since", why)
        self.assertIn("Calculate", why)

    def test_only_RECOMPUTE_may_switch_the_stale_branch_off(self):
        """`allow_stale` is for [Recompute] and nothing else.

        Opening the window on a stale trace is refused (rule 7).  [Recompute]
        exists to decompose the spec AS EDITED -- refusing there would mean a
        full Calculate of every trace just to re-attribute -- and it pays for
        that by stamping `spec_matches_run=False` through the banner, the
        report and the CSV header.  What `allow_stale` must NOT do is switch
        off any other refusal.

        Mutation: apply the flag to the `frozen` branch as well and the second
        half of this goes red -- a frozen snapshot recomputed here comes back
        stamped with the current run, which is the exact mislabelling the
        frozen-trace CSV header exists to prevent.
        """
        self.assertIsNone(ag.attribution_refusal(
            _FakeTrace(stale=True), object(), allow_stale=True))
        why = ag.attribution_refusal(_FakeTrace(frozen=True, stale=True),
                                     object(), allow_stale=True)
        self.assertIn("frozen snapshot", why)


# ============================================================================
# PURE: signs (rule 4)
# ============================================================================

class TestSignedStr(unittest.TestCase):
    def test_negative_takes_the_unicode_minus(self):
        self.assertEqual(ag.signed_str("-1.23 nH"), "−1.23 nH")

    def test_positive_takes_an_explicit_plus(self):
        """One of the pair is ALWAYS emitted.

        Mutation: return `s` unchanged for a positive value.  Every mixed-sign
        column then shifts by one monospace cell per row, which is the whole
        reason the pair exists.
        """
        self.assertEqual(ag.signed_str("1.23 nH"), "+1.23 nH")
        self.assertEqual(ag.signed_str(".5"), "+.5")

    def test_only_position_zero_is_touched(self):
        """A number below the SI table reads '1.23e-05'; that '-' is an
        exponent, not the sign of the value."""
        self.assertEqual(ag.signed_str("1.23e-05"), "+1.23e-05")
        self.assertEqual(ag.signed_str("-1.23e-05"), "−1.23e-05")

    def test_non_numeric_readings_get_no_sign(self):
        """'--' is the no-reading marker and must survive untouched.

        Mutation: take the `-` branch on the first character alone and '--'
        renders as '−-', which reads as a negative number whose digits went
        missing.
        """
        for s in ("nan", "--", "inf", ""):
            self.assertEqual(ag.signed_str(s), s)

    def test_an_already_signed_string_is_left_alone(self):
        self.assertEqual(ag.signed_str("+1"), "+1")
        self.assertEqual(ag.signed_str("−1"), "−1")


# ============================================================================
# PURE: the table model (rule 3)
# ============================================================================

class TestTableModel(unittest.TestCase):
    def setUp(self):
        self.dec = fake_dec([
            _term(None, 2.03e-10, 0.2468),
            _term(_elem(0, port=2), 4.13e-10, 0.5026),
            _term(_elem(1, port=4, source="conn row 2"), 2.06e-10, 0.2506),
        ], total=8.21e-10)

    def test_every_line_is_the_same_length_in_characters(self):
        """Monospace alignment is a character-count property first.

        Mutation: drop the `_fit`/width padding in `render_table` and the rows
        stop lining up under the header.
        """
        t = ag.contributions_table(self.dec, "smart")
        lengths = {len(ln) for ln in t.lines}
        self.assertEqual(len(lengths), 1, f"ragged table: {t.lines}")

    def test_data_rows_start_with_the_swatch_and_headers_do_not(self):
        t = ag.contributions_table(self.dec, "smart")
        for line_idx, _key, _kind in t.rows:
            self.assertTrue(t.lines[line_idx].startswith(ag.ATTRIB_SWATCH))
        self.assertFalse(t.lines[0].startswith(ag.ATTRIB_SWATCH))
        self.assertFalse(t.lines[1].startswith(ag.ATTRIB_SWATCH))

    def test_the_row_map_carries_the_element_index_and_kind(self):
        """The widget selects and colours off THIS, never off line arithmetic.

        Mutation: return the rows in a different order from the lines and the
        detail pane starts describing the row above the one clicked.
        """
        t = ag.contributions_table(self.dec, "smart")
        self.assertEqual([(k, kind) for _l, k, kind in t.rows],
                         [(None, ""), (0, "ground"), (1, "ground")])

    def test_the_bare_EM_row_comes_first_and_is_never_ranked_away(self):
        dec = fake_dec([
            _term(_elem(0), 1.0, 1.0),
            _term(None, 1e-30, 0.0),
        ], total=1.0)
        t = ag.contributions_table(dec, "smart")
        self.assertEqual(t.rows[0][1], None)

    def test_a_text_cell_ellipsises_and_never_clips_silently(self):
        el = at.Element(kind="ground", ports=(0,),
                        source="a very long provenance label", ideal=True,
                        index=0)
        t = ag.contributions_table(fake_dec([_term(el, 1e-9, 1.0)]), "smart")
        body = t.lines[2]
        self.assertIn("…", body)

    def test_a_numeric_column_is_never_capped(self):
        """A clipped number is a plausible wrong number -- the measured
        Treeview failure this table exists to avoid.

        Mutation: give the value Column a `cap` and '-0.6231' silently becomes
        '-0.623'.
        """
        big = _term(_elem(0), 1.2345678e-3, 1.0)
        t = ag.contributions_table(fake_dec([big], unit="Ohm"), "smart")
        self.assertIn("1.23 mΩ", t.lines[2])

    def test_a_value_column_is_at_least_as_wide_as_its_header(self):
        """Sizing on the values alone throws the heading off the numbers."""
        t = ag.contributions_table(fake_dec([_term(_elem(0), 1e-12, 1.0)]),
                                   "smart")
        self.assertEqual(len(t.lines[0]), len(t.lines[2]))

    def test_a_withheld_split_says_so_instead_of_showing_an_empty_table(self):
        """`decompose` empties `terms` when the two algorithms disagree.

        Mutation: let the empty table render and the pane shows a bare header,
        which reads as "this spec declares no elements".
        """
        t = ag.contributions_table(fake_dec([], trustworthy=False), "smart")
        self.assertIn("no per-element split", t.text)
        self.assertIn("reconciliation", t.text)

    def test_a_complex_quantity_gets_two_value_columns(self):
        t = ag.contributions_table(
            fake_dec([_term(_elem(0), complex(1e-3, 26.0), 1.0)],
                     quantity="Z", unit="Ohm", total=complex(1e-3, 26.0)),
            "smart")
        self.assertIn("Re Z", t.lines[0])
        self.assertIn("Im Z", t.lines[0])

    def test_aligned_units_put_the_prefix_in_the_header_not_the_cells(self):
        t = ag.contributions_table(self.dec, "aligned")
        self.assertIn("[pH]", t.lines[0])
        self.assertNotIn("pH", t.lines[2])


class TestSwatchMatchesResultsPane(unittest.TestCase):
    def test_the_swatch_is_the_same_glyph_the_results_pane_uses(self):
        """ATTRIB_SWATCH is a duplicate of RESULTS_SWATCH, pinned here.

        It is duplicated so the pure formatters need no deferred import of
        pkg_rlc_gui on a per-cell path; THIS is what stops the two drifting,
        and the whole width argument (7 px in Consolas 9, one monospace cell)
        rests on them being the same character.
        """
        self.assertEqual(ag.ATTRIB_SWATCH, RESULTS_SWATCH)


class TestRolePalette(unittest.TestCase):
    def test_every_element_kind_maps_to_a_real_ports_and_roles_colour(self):
        """Rule 4: reuse the palette the user already learned.

        Mutation: invent a role name here and `_role_colour` silently falls
        back to the element colour for a whole kind.
        """
        for kind, role in ag.ELEMENT_KIND_ROLE.items():
            with self.subTest(kind=kind):
                self.assertIn(role, PORT_ROLE_FG)

    def test_every_kind_pkg_rlc_attrib_can_emit_is_covered(self):
        """The five element kinds `Element.describe()` knows about, plus the
        bare EM term's empty kind."""
        for kind in ("ground", "vdd", "lumped_to_gnd", "short",
                     "lumped_between", ""):
            self.assertIn(kind, ag.ELEMENT_KIND_ROLE)

    def test_an_unknown_kind_degrades_to_the_element_role(self):
        self.assertEqual(ag.element_role("something_new"), ROLE_ELEMENT)


# ============================================================================
# PURE: folding
# ============================================================================

class TestFolding(unittest.TestCase):
    def _many(self):
        terms = [_term(None, 1e-12, 0.001)]
        terms.append(_term(_elem(0), 1e-9, 0.999))
        for i in range(1, 5):
            terms.append(_term(_elem(i, port=i + 4), 1e-15, 1e-6))
        return fake_dec(terms, total=1e-9)

    def test_the_negligible_tail_is_folded_into_one_line(self):
        t = ag.contributions_table(self._many(), "smart")
        self.assertIn("4 more terms below", t.text)
        self.assertEqual(len(t.rows), 2)   # bare EM + the one strong element

    def test_the_folded_line_points_at_a_csv_that_really_has_no_floor(self):
        """The pointer is only true while `csv_records` enumerates the lot.

        Mutation: give `csv_records` the same floor and the on-screen line
        becomes a lie with nothing to catch it.
        """
        dec = self._many()
        self.assertIn("Export CSV", ag.contributions_table(dec, "smart").text)
        recs = ag.csv_records(fake_prov(), dec)
        terms = [r for r in recs if r["kind"] == "term"]
        self.assertEqual(len(terms), len(dec.terms))

    def test_the_strongest_term_survives_however_small_the_whole_spec_is(self):
        """A table whose whole content is 'N terms were too weak' answers
        nothing.

        The floor is RELATIVE to the strongest term, so unlike
        `rank_coupling_pairs` -- whose floor is an absolute -60 dB and which
        therefore needs an explicit rescue -- this holds by construction.  The
        guard is on the two early returns that make it true; mutation: drop
        the `strongest == 0.0` return and an all-zero spec divides by nothing
        and folds every row away.
        """
        tiny = [_term(_elem(i), 1e-30 * (i + 1), 0.0) for i in range(4)]
        shown, folded = ag._fold_terms(fake_dec(tiny, total=4e-30))
        self.assertEqual(len(shown), 4)
        self.assertEqual(folded, [])
        zero = [_term(_elem(i), 0j, 0.0) for i in range(3)]
        shown, folded = ag._fold_terms(fake_dec(zero, total=0j))
        self.assertEqual(len(shown), 3)
        self.assertEqual(folded, [])

    def test_an_undefined_contribution_is_never_folded(self):
        """NaN is a missing measurement, not a small number.

        Mutation: spell the fold test as `not (m >= floor)` -- the natural
        slip, and IEEE makes it true for every NaN -- and the one row the
        reader most needs disappears into a summary line.  The tail here is
        deliberately long enough that a fold really happens, so the NaN is
        being spared and not merely surviving an empty fold.
        """
        nan = complex(float("nan"), float("nan"))
        terms = [_term(_elem(0), 1e-9, 1.0),
                 _term(_elem(1, port=5), nan, float("nan"))]
        terms += [_term(_elem(i, port=i + 6), 1e-18, 0.0) for i in range(2, 5)]
        shown, folded = ag._fold_terms(fake_dec(terms, total=1e-9))
        self.assertEqual(len(folded), 3, "the tail must really fold")
        self.assertIn(nan, [t.contribution for t in shown])

    def test_an_undefined_contribution_sorts_BELOW_a_measured_zero(self):
        """A NaN keyed at 0.0 ties with an INERT element, whose contribution is
        exactly 0 and whose key is -0.0.

        Exactly-zero is not a corner: a lumped element the reduction
        annihilates sums to exactly 0 -- `inert_lumped_messages` in core exists
        for that case.  Mutation: key NaN as 0.0 instead of +inf; the two tie,
        the stable sort keeps input order, and the missing measurement prints
        ABOVE the measured zero.
        """
        nan = complex(float("nan"), float("nan"))
        terms = [_term(_elem(0), nan, float("nan")),
                 _term(_elem(1, port=5), 0j, 0.0),
                 _term(_elem(2, port=6), 1e-9, 1.0)]
        shown, folded = ag._fold_terms(fake_dec(terms, total=1e-9), fold=False)
        self.assertEqual(folded, [])
        self.assertEqual([t.element.index for t in shown], [2, 1, 0])

    def test_fold_false_still_RANKS(self):
        """The exported report uses it, and a report in declaration order
        beside a screen in strength order is two answers to "which of these
        matters" from one set of numbers."""
        terms = [_term(None, 5e-10, 0.5),
                 _term(_elem(0), 1e-12, 0.001),
                 _term(_elem(1, port=5), 1e-9, 0.999)]
        t = ag.contributions_table(fake_dec(terms, total=1e-9), "smart",
                                   fold=False)
        # The bare EM row is first and unranked; the elements are ranked.
        self.assertEqual([k for _l, k, _kd in t.rows], [None, 1, 0])

    def test_fold_false_keeps_everything(self):
        t = ag.contributions_table(self._many(), "smart", fold=False)
        self.assertEqual(len(t.rows), 6)


# ============================================================================
# PURE: reconciliation (rule 5)
# ============================================================================

class TestReconciliation(unittest.TestCase):
    def test_a_clean_split_reads_reconciled_with_both_numbers(self):
        line = ag.reconciliation_line(fake_dec([_term(_elem(0), 1e-9, 1.0)]))
        self.assertTrue(line.startswith("reconciled"))
        self.assertIn("rel diff 1e-13", line)
        self.assertIn("floor 1e-10", line)

    def test_a_withheld_split_says_NOT_reconciled_and_is_flagged_bad(self):
        dec = fake_dec([], resid=0.5, trustworthy=False)
        verdict, ok = ag.reconciliation_verdict(dec)
        self.assertEqual(verdict, "NOT reconciled")
        self.assertFalse(ok)

    def test_the_TOTAL_is_shown_even_when_the_split_is_withheld(self):
        """Rule 5: the total is always shown.

        Mutation: return early on `not trustworthy` and the user is left with
        a verdict and no number at all.
        """
        line = ag.reconciliation_line(
            fake_dec([], resid=0.5, trustworthy=False, total=8.21e-10))
        self.assertIn("821 pH", line)

    def test_above_the_floor_is_a_third_state_not_a_failure(self):
        dec = fake_dec([_term(_elem(0), 1e-9, 1.0)], resid=1e-8, floor=1e-10)
        verdict, ok = ag.reconciliation_verdict(dec)
        self.assertEqual(verdict, "reconciled (above floor)")
        self.assertTrue(ok)

    def test_a_whatif_is_not_comparable_rather_than_disagreeing(self):
        """`reference_applicable` False means the engine was never ASKED.

        Mutation: treat it as a disagreement and a correct what-if reads as a
        broken one -- the exact failure the attrib module documents.
        """
        dec = fake_dec([_term(_elem(0), 1e-9, 1.0)], applicable=False)
        verdict, ok = ag.reconciliation_verdict(dec)
        self.assertEqual(verdict, "not comparable")
        self.assertTrue(ok)
        self.assertIn("never asked about", ag.reconciliation_line(dec))


# ============================================================================
# PURE: provenance and staleness (rules 6 and 12)
# ============================================================================

class TestProvenance(unittest.TestCase):
    def test_the_block_names_the_run(self):
        lines = ag.provenance_lines(fake_prov())
        self.assertTrue(any("Run     : #7" in ln for ln in lines))

    def test_an_edited_spec_is_named_in_the_block_itself(self):
        """Rule 12 / the frozen-CSV precedent: a block attributed to the wrong
        run is a real bug.

        Mutation: drop the `spec_matches_run` clause and an export computed off
        an edited spec is headed 'Run: #7' with nothing saying otherwise.
        """
        lines = ag.provenance_lines(fake_prov(spec_matches_run=False))
        joined = "\n".join(lines)
        self.assertIn("EDITED since that run", joined)
        self.assertIn("Export CSV still show the run", joined)

    def test_the_full_sign_convention_is_carried_verbatim(self):
        joined = "\n".join(ag.provenance_lines(fake_prov()))
        self.assertIn(at.SIGN_CONVENTION_TEXT, joined)
        self.assertIn(ag.SIGN_NOTE_TERMS, joined)
        self.assertIn(ag.SIGN_NOTE_SHARES, joined)

    def test_the_ground_model_is_declared_with_its_measurement(self):
        joined = "\n".join(ag.provenance_lines(fake_prov()))
        self.assertIn("9.60 dB", joined)

    def test_the_termination_spec_is_carried_verbatim(self):
        joined = "\n".join(ag.provenance_lines(fake_prov()))
        self.assertIn("    1 signal vic +", joined)
        self.assertIn("    2 ground", joined)

    def test_a_snapped_frequency_says_what_was_asked_for(self):
        joined = "\n".join(ag.provenance_lines(
            fake_prov(requested_hz=5.6e9, actual_hz=5.5983e9)))
        self.assertIn("asked for", joined)
        self.assertIn("nearest data point", joined)

    def test_an_exact_frequency_is_silent_about_snapping(self):
        joined = "\n".join(ag.provenance_lines(fake_prov()))
        self.assertNotIn("asked for", joined)


class TestStalenessText(unittest.TestCase):
    def test_an_unchanged_spec_is_not_a_warning(self):
        tc = TraceConfig(id=1, label="t", port_a="1")
        prov = fake_prov(signature=_config_signature(tc))
        text, warn = ag.staleness_text(prov, tc, True)
        self.assertFalse(warn)
        self.assertIn("from run #7", text)

    def test_an_edited_spec_warns_and_names_the_button(self):
        """Rule 6: the banner is the whole of what the editor hook may do.

        Mutation: compare something that does not move with the spec (the
        label, say) and the banner never warns, so [Recompute] is a button
        with no reason on it.
        """
        tc = TraceConfig(id=1, label="t", port_a="1")
        prov = fake_prov(signature=_config_signature(tc))
        tc.port_a = "2"
        text, warn = ag.staleness_text(prov, tc, True)
        self.assertTrue(warn)
        self.assertIn("Recompute", text)

    def test_a_removed_trace_says_so_and_says_nothing_can_be_recomputed(self):
        text, warn = ag.staleness_text(fake_prov(), None, False)
        self.assertTrue(warn)
        self.assertIn("REMOVED", text)

    def test_a_block_that_does_not_belong_to_the_run_SAYS_SO_ON_THE_BANNER(self):
        """
        `_on_recompute`'s own docstring promises "the banner, the export and
        the copied report all say the plot and the results table are showing
        something else".  The export and the report did; the banner did not.

        The signature is EQUAL here on purpose -- that is the state right after
        a Recompute on an edited spec, and it is why the existing
        `spec_signature(trace) != prov.signature` test cannot fire: the
        Recompute has just re-captured the signature.  Measured on
        `coupled_4port_diff.s4p`: run #1 is M = +821 pH, editing GND from "2,4"
        to "2" and pressing Recompute gives +407 pH -- 2.0x what the plot, the
        results table and Export CSV are still showing -- and the banner read
        `from run #1 @ 5.1 GHz   ·   M: 'vic' ← 'agg'` in the theme foreground
        with no warning at all.

        Mutation: drop the `if not prov.spec_matches_run` branch and this is
        the only test in the class that goes red.
        """
        tc = TraceConfig(id=1, label="t", port_a="1")
        prov = fake_prov(signature=_config_signature(tc),
                         spec_matches_run=False)
        text, warn = ag.staleness_text(prov, tc, True)
        self.assertTrue(warn, "a block that is not the run must warn")
        self.assertIn("AS EDITED", text)
        self.assertIn("#7", text, "it must name the run it is NOT")

    def test_a_MOVED_signature_outranks_a_mismatched_run(self):
        """Both can be true at once; "press Recompute" is the actionable one.

        Mutation: swap the two branches and a user who has edited the spec
        again since the last Recompute is told to press Calculate, which is
        not what will make the window agree with itself.
        """
        tc = TraceConfig(id=1, label="t", port_a="1")
        prov = fake_prov(signature=_config_signature(tc),
                         spec_matches_run=False)
        tc.port_a = "2"
        text, warn = ag.staleness_text(prov, tc, True)
        self.assertTrue(warn)
        self.assertIn("Recompute", text)

    def test_the_signature_is_pkg_rlc_gui_s_own(self):
        """One definition of "did this edit change the answer".

        Mutation: hand-roll a second tuple here and the trailing `*` in the
        Traces list and this banner can disagree about the same edit.
        """
        tc = TraceConfig(id=1, label="t", port_a="1")
        self.assertEqual(ag.spec_signature(tc), _config_signature(tc))


# ============================================================================
# PURE: the across-frequency verdict
# ============================================================================

class TestStabilityLine(unittest.TestCase):
    def test_one_frequency_is_not_a_check(self):
        line = ag.stability_line([1e9], [{}])
        self.assertIn("not checked", line)

    def test_a_stable_rank_says_so_with_the_span(self):
        ranks = [{"a": 1, "b": 2}, {"a": 1, "b": 2}]
        line = ag.stability_line([1e8, 1e10], ranks)
        self.assertIn("STABLE", line)
        self.assertIn("100 MHz", line)
        self.assertIn("10 GHz", line)

    def test_a_moving_rank_names_the_elements_that_moved(self):
        ranks = [{"a": 1, "b": 2}, {"a": 2, "b": 1}]
        line = ag.stability_line([1e8, 1e10], ranks)
        self.assertIn("NOT stable", line)
        self.assertIn("'a'", line)
        self.assertIn("'b'", line)

    def test_no_ranking_anywhere_is_not_reported_as_stable(self):
        """An empty comparison under a 'stable' verdict is a claim about a
        comparison that never happened.

        Mutation: drop the `if not labels` branch and every column having
        withheld its split reads as 'rank is STABLE'.
        """
        line = ag.stability_line([1e8, 1e10], [{}, {}])
        self.assertIn("no ranking is available", line)
        self.assertNotIn("STABLE", line)


# ============================================================================
# PURE: candidates
# ============================================================================

class TestCandidates(unittest.TestCase):
    OMEGA = 2 * math.pi * 5e9

    def test_open_and_ideal(self):
        self.assertTrue(ag.parse_candidate("open", self.OMEGA).is_open)
        self.assertTrue(ag.parse_candidate("ideal", self.OMEGA).is_ideal)

    def test_a_resistor_and_an_inductor(self):
        self.assertAlmostEqual(
            ag.parse_candidate("R=50", self.OMEGA).z.real, 50.0)
        self.assertAlmostEqual(
            ag.parse_candidate("L=1n", self.OMEGA).z.imag,
            self.OMEGA * 1e-9)

    def test_a_value_split_across_a_space_is_REFUSED(self):
        """`parse_kv_rlc_params` silently DROPS a token with no '='.

        Measured elsewhere in this repo: 'R=5 m' then computed 5 ohm where
        5 milliohm was typed.  Mutation: drop the `bad` check and this returns
        R=5 with no complaint.
        """
        with self.assertRaises(ValueError) as cm:
            ag.parse_candidate("R=5 m", self.OMEGA)
        self.assertIn("no '='", str(cm.exception))

    def test_a_bad_entry_costs_its_own_entry_and_not_the_field(self):
        alts, problems = ag.candidate_list("open, R=5 m, L=1n", self.OMEGA)
        self.assertEqual([a.name for a in alts], ["open", "L=1n"])
        self.assertEqual(len(problems), 1)

    def test_the_two_structural_candidates_need_no_judgement(self):
        """This tool will not guess a package value -- the CLI's rule."""
        self.assertEqual(ag.STRUCTURAL_CANDIDATES, ("open", "ideal"))


# ============================================================================
# PURE: CSV
# ============================================================================

class TestCsvRecords(unittest.TestCase):
    def test_every_row_carries_exactly_the_declared_fields(self):
        dec = fake_dec([_term(None, 1e-12, 0.1), _term(_elem(0), 9e-10, 0.9)])
        sens = [at.SensitivityResult("element", "ground port 2", (0,), "open",
                                     "M", "H", 1e-9, 5e-10, -5e-10, -6.02)]
        for r in ag.csv_records(fake_prov(), dec, sens):
            self.assertEqual(set(r), set(ag.CSV_FIELDS))

    def test_both_totals_are_written_and_named(self):
        recs = ag.csv_records(fake_prov(), fake_dec([]))
        elems = [r["element"] for r in recs if r["kind"] == "total"]
        self.assertEqual(elems, ["(compute_z_matrix)", "(sum of terms)"])

    def test_a_non_finite_reading_is_written_not_blanked(self):
        """nan / inf are readings, not missing cells.

        Mutation: blank them and "this probe has no return path" becomes "we
        did not measure it".
        """
        self.assertEqual(ag._e(float("nan")), "nan")
        self.assertEqual(ag._e(float("-inf")), "-inf")
        self.assertEqual(ag._e(float("inf")), "inf")

    def test_the_residual_is_not_smuggled_into_a_numeric_column(self):
        recs = ag.csv_records(fake_prov(), fake_dec([]))
        total = [r for r in recs if r["element"] == "(sum of terms)"][0]
        self.assertIn("residual=", total["note"])
        self.assertEqual(total["delta_dB"], "")


class TestReportText(unittest.TestCase):
    def test_the_report_carries_the_provenance_and_the_reconciliation(self):
        txt = ag.report_text(fake_prov(),
                             fake_dec([_term(_elem(0), 1e-9, 1.0)]))
        self.assertIn("Run     : #7", txt)
        self.assertIn("Reconciliation: reconciled", txt)

    def test_the_report_shows_every_term_with_no_floor(self):
        """The window folds; the report does not.

        Mutation: drop `fold=False` and a copied report silently loses the
        same rows the screen folded, with no 'see the CSV' pointer to follow.
        """
        terms = [_term(_elem(0), 1e-9, 1.0)]
        terms += [_term(_elem(i, port=i + 4), 1e-18, 0.0) for i in range(1, 5)]
        txt = ag.report_text(fake_prov(), fake_dec(terms, total=1e-9))
        self.assertNotIn("more terms below", txt)
        for i in range(1, 5):
            self.assertIn(f"ground port {i + 4}", txt)

    def test_a_sensitivity_scan_that_was_never_run_is_SAID(self):
        txt = ag.report_text(fake_prov(), fake_dec([]))
        self.assertIn("Sensitivity: not run", txt)

    def test_the_report_carries_what_the_sweep_label_had_to_clip(self):
        """"see Copy report" on the capped sweep note has to be TRUE.

        The label under the plot shows `SWEEP_NOTE_LINES` clipping lines
        because every line it takes comes off the curve beside it (measured:
        the uncapped caption asked 293 px and pinned the canvas at its 90 px
        floor).  That is only acceptable while the tail is somewhere -- the
        same contract the contributions table's "all of them are in Export
        CSV" pointer carries.

        Mutation: drop the `sweep` / `problems` blocks from `report_text` and
        the capped label's pointer becomes a lie.
        """
        caption = ["M over series inductance ∈ [0, ∞): [a, b]",
                   "NON-MONOTONIC: the curve LEAVES the bracket.",
                   "The extremum is 5.15e+03 times the bracket.",
                   "A series L resonates with the shunt C."]
        problems = ["'R=5 m': 'm' has no '='."]
        txt = ag.report_text(fake_prov(), fake_dec([]), sweep=caption,
                             problems=problems)
        for line in caption:
            self.assertIn(line, txt)
        self.assertIn("REFUSED", txt)
        self.assertIn("'R=5 m'", txt)


# ============================================================================
# PURE: what the label under the sweep is allowed to show
# ============================================================================

class TestSweepNoteText(unittest.TestCase):
    """
    `sweep_note_text` -- the cap that stopped the caption eating the plot.

    Measured before it existed: `sweep_caption` returns up to four sentences
    (957 characters on a non-monotonic sweep), the Label wrapped them at
    `wraplength=420` into a 293 px request, and because it is packed
    `side=BOTTOM` against an `expand=True` canvas the plot was pinned at its
    90 px floor at every window size -- 194 -> 90 px at 980x700, 274 -> 90 at
    1400x900, and 103x6 PIXELS of axes at the 720x420 minimum.
    """

    def test_it_never_returns_more_lines_than_the_cap(self):
        out = ag.sweep_note_text([f"line {i}" for i in range(9)])
        self.assertEqual(len(out.splitlines()), ag.SWEEP_NOTE_LINES)

    def test_a_dropped_tail_is_COUNTED_and_pointed_at_the_report(self):
        """Mutation: slice to `max_lines` and return -- the caption then loses
        sentences with nothing on screen saying anything was dropped, which is
        the silent Treeview clip rule 3 exists to refuse."""
        out = ag.sweep_note_text(["a", "b", "c", "d"], max_lines=2)
        self.assertIn("+3 more", out)
        self.assertIn("Copy report", out)

    def test_rule_8s_NON_MONOTONICITY_label_survives_the_cap(self):
        """
        Rule 8 requires the non-monotonicity to be LABELLED, so the budget has
        to be big enough for it and the pointer must not displace it.

        This is why `SWEEP_NOTE_LINES` is 3 and not 2.  Measured on the real
        window at 1020x700 with the shipped fixture, the caption is FIVE lines
        -- interval, the warning, and two module notes -- and at a cap of 2 the
        second line was "… +4 more" with the mandatory warning inside the +4.

        Mutation: `SWEEP_NOTE_LINES = 2`.
        """
        caption = ["M over series inductance ∈ [0, ∞): [−394 uH, +375 uH]",
                   "NON-MONOTONIC: the curve LEAVES the [ideal, open] "
                   "bracket, so those two endpoints are not a bound.",
                   "The extremum is 5.15e+03 times the bracket …",
                   "A series L resonates with the structure's shunt C …"]
        out = ag.sweep_note_text(caption)
        self.assertIn("NON-MONOTONIC", out)
        self.assertIn("+2 more", out)

    def test_a_REFUSED_CANDIDATE_comes_before_the_caption(self):
        """The whole of the fix for a candidate dropped in silence.

        `_alternatives` used to write its problems straight into this Label and
        `_draw_sweep` overwrote them later in the same `_render()` pass, so
        `candidate_list("open, R=5 m", omega)` -- which produces exactly the
        `_rlc_tokens` message this repo requires -- reached no widget at all.

        Mutation: append the problems instead of prepending them and, at
        `SWEEP_NOTE_LINES = 2`, the message is what the cap throws away.
        """
        out = ag.sweep_note_text(["the interval", "NON-MONOTONIC: …"],
                                 ["'R=5 m': 'm' has no '='"])
        self.assertTrue(out.startswith("'R=5 m'"), out)

    def test_nothing_at_all_is_an_empty_string_not_a_blank_line(self):
        self.assertEqual(ag.sweep_note_text([], []), "")
        self.assertEqual(ag.sweep_note_text(["", "  "], []), "")

    def test_a_caption_inside_the_cap_is_passed_through_verbatim(self):
        """The normal case must not grow a pointer it does not need."""
        out = ag.sweep_note_text(["one", "two"])
        self.assertEqual(out.splitlines(), ["one", "two"])


# ============================================================================
# PURE: the sweep's pole, and the axis it forces  (item 1)
# ============================================================================

def fake_sweep(lam, residue, c0, *, scale=1.0, n=161,
               t_lo=1e-11, t_hi=1e-3, notes=()) -> at.Sweep:
    """
    A one-pole Mobius sweep built from its PARTIAL FRACTIONS by hand.

    `Z(t) = c0 - residue / (lam + t)`, which is exactly the canonical form
    `sweep_mobius` returns, so the pole is at `t = -lam` and is known to the
    test independently of any sample.  Built as the REAL `at.Sweep`, not a
    duck: a field renamed in `pkg_rlc_attrib` must break this loudly rather
    than leave the picture reading a `getattr` default.

    The sign of `lam` is the whole experiment.  `lam < 0` puts the pole on the
    swept half-line; `lam > 0` puts it at a negative `t`, where nothing sweeps
    through it -- the same map, the same endpoints, and no pole in range.
    """
    lam = complex(lam)
    residue = complex(residue)
    c0 = complex(c0)
    ts = np.concatenate([[0.0], np.logspace(math.log10(t_lo),
                                            math.log10(t_hi), n - 1)])
    vals = np.array([complex(scale * (c0 - residue / (lam + t)).real, 0.0)
                     for t in ts])
    v_ideal = complex(scale * (c0 - residue / lam).real, 0.0)
    v_open = complex(scale * c0.real, 0.0)
    b_lo = min(v_ideal.real, v_open.real)
    b_hi = max(v_ideal.real, v_open.real)
    # `interval` is the ANALYTIC extremum, which for a near-pole is the peak at
    # `t = -Re(lam)` and is what `sweep_mobius` reports there -- a log sample
    # grid lands nowhere near it.  Adding it by hand is what makes this fixture
    # the shape of the measured one: on coupled_4port_diff.s4p the analytic
    # interval reads (-394 uH, +375 uH) while the samples peak at 4.2 nH.
    peak = complex(scale * (c0 - residue / (lam - lam.real)).real, 0.0)
    finite = [float(v) for v in np.real(vals) if math.isfinite(float(v))]
    if math.isfinite(peak.real):
        finite.append(peak.real)
    interval = (float(min(finite)), float(max(finite)))
    return at.Sweep(
        elements=(0,), quantity="M", unit="H", param_name="series inductance",
        param_unit="H", z_unit=1j, num=(), den=(), alpha=0j, beta=0j,
        gamma=0j, delta=0j, value_ideal=v_ideal, value_open=v_open,
        interval=interval, arg_min=0.0, arg_max=0.0, bracket=(b_lo, b_hi),
        leaves_bracket=bool(interval[0] < b_lo or interval[1] > b_hi),
        samples=(ts, vals), method="closed-form", scale=scale, part="re",
        notes=tuple(notes), c0=c0, lam=(lam,), residues=(residue,))


#: Ideal +821 pH, open +408 pH, pole at exactly 100 nH -- the shape of the
#: shipped fixture (measured there: endpoints +821 pH / +408 pH, pole 96.9 nH
#: with `lam = -9.688e-08 - 4.73e-12j`) with round numbers so every assertion
#: can be exact.  The imaginary part is what makes it a NEAR-pole with a finite
#: peak rather than a true singularity, exactly as the real one is; the pole
#: location is its real part and is exact whatever the sampling does.
POLE_LAM = -1e-7 - 1e-11j
#: The residue is COMPLEX, like the real one (`3.11e-10 + 1.28e-06j`), and
#: both parts do a job: the real part sets the endpoint separation
#: (`c0 - Re(res/lam)` = 408 pH + 413 pH = 821 pH) and the imaginary part sets
#: the height of the near-pole peak (`Re(res / (i Im lam))` = 1 uH).  With a
#: purely real residue the peak lands entirely in Im(Z) and the REAL part --
#: which is what `part="re"` plots -- has no spike to exclude at all.
POLE_RES = 4.13e-17 + 1e-17j
POLE_C0 = 4.08e-10


class TestSweepPicture(unittest.TestCase):
    """
    Item 1: what the sweep's y axis is scaled to, and where the pole is.

    THE ASSERTION THAT MATTERS is that the limits bracket M(0) and M(inf) in
    BOTH cases -- with a pole and without.  Those two numbers are what the pane
    is opened for ("ideal ground" and "open"), and before this the pole's
    excursion set the scale and flattened them onto one line: measured on
    coupled_4port_diff.s4p, an axis reading `1e-10` over a single vertical
    spike, with a headline interval of (-394 uH, +375 uH) beside endpoints of
    +203 pH and +821 pH.
    """

    def with_pole(self):
        return fake_sweep(POLE_LAM, POLE_RES, POLE_C0)

    def without_pole(self):
        # Same endpoints, mirrored pole: lam > 0 puts it at t = -100 nH.
        return fake_sweep(-POLE_LAM, -POLE_RES, POLE_C0)

    def test_the_endpoints_really_are_the_same_in_both_cases(self):
        """Precondition, asserted rather than assumed: if the two fixtures did
        not share their endpoints, "the limits bracket them in both" would be
        two different claims."""
        a, b = self.with_pole(), self.without_pole()
        for sw in (a, b):
            self.assertAlmostEqual(sw.value_ideal.real, 8.21e-10, places=13)
            self.assertAlmostEqual(sw.value_open.real, 4.08e-10, places=13)

    def test_the_Y_LIMITS_BRACKET_BOTH_ENDPOINTS_with_a_pole(self):
        """Mutation: scale the axis from the full sample range (or drop
        `set_ylim` and let matplotlib autoscale) -- measured on this fixture
        the samples reach 4.7 uH, i.e. 5700x the ideal endpoint, and both
        endpoints land on the same pixel row."""
        pic = ag.sweep_picture(self.with_pole())
        lo, hi = pic.ylim
        self.assertLessEqual(lo, 4.08e-10)
        self.assertGreaterEqual(hi, 8.21e-10)
        # And it is the POLE-FREE range, not the excursion.  The threshold is
        # MEASURED, not round: the pole-free high is +860 pH, the sampled peak
        # on this grid is +7.75 nH and the analytic one is +1 uH, so 2 nH
        # separates them and is still 2.4x the ideal endpoint -- i.e. it is not
        # a tautology in either direction.  At 1e-8 the "scale from every
        # sample" mutation passed.
        self.assertLess(hi, 2e-9)

    def test_the_Y_LIMITS_BRACKET_BOTH_ENDPOINTS_without_a_pole(self):
        """The same assertion, and it is the one that says the fix did not
        simply clip everything: with no pole nothing is suppressed and the
        limits still have to contain both endpoints."""
        pic = ag.sweep_picture(self.without_pole())
        lo, hi = pic.ylim
        self.assertLessEqual(lo, 4.08e-10)
        self.assertGreaterEqual(hi, 8.21e-10)

    def flat(self):
        """A sweep that does not move: every residue zero, so M(0) == M(inf).

        This is not a contrived shape.  MEASURED on the SHIPPED fixture
        decap_4port.s4p (ordinary mode 6, probes 1/2, `gnd_ports="3,4"`, 5 GHz,
        either ground row): `residues == 0` exactly and
        ideal = open = -506.755 nH.
        """
        # `lam` verbatim from that run -- its tiny imaginary part is what
        # `fake_sweep` needs to evaluate the analytic peak at all.
        return fake_sweep(complex(-1.0119972272183943e-09, 6.1651954842864e-20),
                          0.0, complex(-5.06754728930068e-07, 0.0))

    def test_a_FLAT_sweep_gets_an_axis_the_size_of_ITS_OWN_VALUE(self):
        """
        The bracket-the-endpoints guard passes this case with NO fix at all,
        which is why it needed one of its own.

        MEASURED on decap_4port.s4p before: `ylim` was
        (-120.00005 mH, +119.99995 mH) around a -506.755 nH constant -- the
        axis was **473 602x** the value it was drawn to show, the curve and
        both asymptote lines landed on ONE pixel row (endpoint separation
        0.0 px of a 223.5 px axes), and because `linear_ticks` came out False
        the symlog decade locator printed 17 labelled decades from -10^0 to
        +10^0.  The margin was `SWEEP_Y_PAD * max(abs(hi), abs(lo), 1.0)` and
        that bare `1.0` is one HENRY in an expression whose other terms are
        picohenries.

        `assertLessEqual(lo, v)` / `assertGreaterEqual(hi, v)` are both TRUE of
        +-0.12 H, so they are asserted here as well -- to show that they are
        not the guard.  The guard is the ratio.

        Mutation: restore the `1.0` and the ratio goes to 4.7e5.
        """
        sw = self.flat()
        self.assertEqual(sw.value_ideal, sw.value_open,
                         "precondition: this fixture is not flat")
        v = abs(sw.value_ideal.real)
        pic = ag.sweep_picture(sw)
        lo, hi = pic.ylim
        # The old guard, which passes either way.
        self.assertLessEqual(lo, sw.value_ideal.real)
        self.assertGreaterEqual(hi, sw.value_ideal.real)
        # The one that does not.  1.5x is not a tautology in either direction:
        # the fix lands at 1.12x and the defect at 473 602x.
        self.assertLessEqual(max(abs(lo), abs(hi)), 1.5 * v,
                             f"the axis {pic.ylim} is "
                             f"{max(abs(lo), abs(hi)) / v:.0f}x the value it "
                             f"is drawn to show")
        # And a sub-decade range gets a LINEAR major locator, or matplotlib's
        # symlog locator puts no labelled tick inside it at all.
        self.assertTrue(pic.linear_ticks)

    def test_an_ALL_ZERO_sweep_asks_for_no_limits_rather_than_inventing_some(
            self):
        """
        `pad` is a fraction of the value, so a value of exactly zero gives a
        zero pad -- and `_scale_sweep_axis`'s `yhi > ylo` guard then declines
        to set any limit, leaving matplotlib's own autoscale.  That is the
        honest answer for a curve that is identically zero, and it is the
        reason the flat branch does not need a floor of its own.
        """
        pic = ag.sweep_picture(fake_sweep(
            complex(-1.0119972272183943e-09, 6.1651954842864e-20), 0.0, 0j))
        self.assertEqual(pic.ylim[0], pic.ylim[1])
        self.assertEqual(pic.linthresh, 0.0)

    def test_a_pole_in_range_is_found_and_a_pole_out_of_range_is_not(self):
        self.assertEqual(len(ag.sweep_picture(self.with_pole()).drawn), 1)
        self.assertEqual(ag.sweep_picture(self.without_pole()).drawn, ())

    def test_the_pole_is_CLOSED_FORM_and_not_the_nearest_sample(self):
        """
        `t = -lam`, straight off the partial fractions.

        The grid is deliberately coarse (21 points over 8 decades, a factor of
        2.5 apart) so that no sample sits at 100 nH: a scan would report
        79.4 nH or 199 nH.  Mutation: take the pole from
        `ts[argmax(abs(vals))]`.
        """
        sw = fake_sweep(POLE_LAM, POLE_RES, POLE_C0, n=21)
        pic = ag.sweep_picture(sw)
        self.assertEqual(len(pic.drawn), 1)
        self.assertEqual(pic.drawn[0].t, 1e-7)
        ts_arr = sw.samples[0]
        self.assertNotIn(1e-7, list(ts_arr))

    def test_the_pole_free_interval_is_the_headline_and_the_spike_is_not(self):
        """Mutation: report `sw.interval` (the analytic extremum over the whole
        half-line).  Measured on this fixture that is (-33.6 uH, +34.4 uH)
        against a pole-free (+369 pH, +860 pH)."""
        sw = self.with_pole()
        pic = ag.sweep_picture(sw)
        self.assertLess(pic.interval[1], 2e-9)
        self.assertGreater(max(abs(sw.interval[0]), abs(sw.interval[1])),
                           1e-6, "the fixture has no spike to exclude")
        self.assertGreater(pic.n_offscale, 0)

    def test_without_a_pole_nothing_is_suppressed(self):
        pic = ag.sweep_picture(self.without_pole())
        self.assertEqual(pic.n_offscale, 0)

    def test_linthresh_comes_from_the_data_and_never_from_a_constant(self):
        """It is the LARGER endpoint magnitude, so the endpoint band is
        rendered linearly and a decade-scale excursion is not.

        Mutation: a fixed 1e-6 (or any constant) -- these are henries, and the
        same constant is three decades wrong on a picohenry file and three
        decades wrong the other way on a microhenry one.
        """
        pic = ag.sweep_picture(self.with_pole())
        self.assertAlmostEqual(pic.linthresh, 8.21e-10, places=13)
        # 1000x the endpoints: the same rule scales with the data.
        big = fake_sweep(POLE_LAM, POLE_RES * 1e3, POLE_C0 * 1e3)
        self.assertAlmostEqual(ag.sweep_picture(big).linthresh, 8.21e-7,
                               places=10)

    def test_a_sub_decade_range_asks_for_LINEAR_ticks(self):
        """matplotlib's symlog locator ticks at DECADES and produced no
        labelled tick at all inside (310 pH, 919 pH) -- measured, `[]` with the
        default locator and `['500 pH']` with subs=[1,2,5].  Inside `linthresh`
        the transform is the identity, so a linear locator is exact there."""
        self.assertTrue(ag.sweep_picture(self.with_pole()).linear_ticks)

    def test_a_decade_scale_excursion_keeps_the_LOG_ticks(self):
        """The other half of the same rule, or `linear_ticks` would be a
        constant dressed as a decision."""
        sw = fake_sweep(POLE_LAM, POLE_RES, POLE_C0)
        wide = replace_sweep_interval(sw)
        self.assertFalse(ag.sweep_picture(wide).linear_ticks)

    def test_poles_sharing_one_excursion_are_ONE_marker(self):
        """Measured on the shipped fixture sweeping both grounds as one group:
        two poles at 96.5 nH and 97.0 nH, 0.5% apart, drew two vertical lines
        one pixel apart with two rotated labels printed over each other.

        Mutation: draw `pic.drawn` instead of `pic.clusters`.
        """
        sw = fake_sweep(POLE_LAM, POLE_RES, POLE_C0)
        two = at.Sweep(**{**sw.__dict__,
                          "lam": (complex(POLE_LAM), complex(POLE_LAM * 1.005)),
                          "residues": (complex(POLE_RES), complex(0.0))})
        pic = ag.sweep_picture(two)
        self.assertEqual(len(pic.drawn), 2)
        self.assertEqual(len(pic.clusters), 1)
        label = ag.pole_label(pic.clusters[0], "H")
        self.assertIn("2 poles", label)
        # Both values are named, not just the first.
        self.assertIn("100 nH", label)

    def test_a_single_pole_marker_is_labelled_with_its_value(self):
        pic = ag.sweep_picture(self.with_pole())
        self.assertEqual(ag.pole_label(pic.clusters[0], "H"), "pole  100 nH")


def replace_sweep_interval(sw):
    """The same sweep with its pole-free portion stretched 300x.

    Used only by `test_a_decade_scale_excursion_keeps_the_LOG_ticks`: it moves
    ONE sample, far from the pole, so the pole-free range genuinely spans
    decades and `linear_ticks` has something to answer no to.
    """
    ts_arr, vals = sw.samples
    vals = np.array(vals, dtype=complex)
    vals[1] = complex(300.0 * sw.value_ideal.real, 0.0)
    return at.Sweep(**{**sw.__dict__, "samples": (ts_arr, vals)})


class TestSweepCaptionWithAPole(unittest.TestCase):
    def test_with_no_pole_the_caption_is_what_it_always_was(self):
        """Item 1's own rule: if there is no pole in range, nothing changes.

        Mutation: make the pole wording unconditional and every ordinary sweep
        grows a sentence about a feature it has not got.
        """
        sw = fake_sweep(-POLE_LAM, -POLE_RES, POLE_C0)
        lines = ag.sweep_caption(sw)
        # The NUMBERS lead: this Label is 179 px at the minimum and 329 px at
        # 1020x700, so anything past the first ~30 characters is off screen.
        self.assertTrue(lines[0].startswith("M ∈ ["), lines[0])
        self.assertIn("over series inductance ∈ [0, ∞)", lines[0])
        self.assertNotIn("POLE", "\n".join(lines))
        self.assertIn("ideal", lines[0])
        self.assertIn("open", lines[0])

    def test_with_a_pole_the_headline_is_the_POLE_FREE_interval(self):
        lines = ag.sweep_caption(fake_sweep(POLE_LAM, POLE_RES, POLE_C0))
        self.assertIn("AWAY FROM THE POLE", lines[0])
        # +821 pH / +408 pH are the endpoints, and the headline interval is
        # around them -- not the microhenry excursion.
        self.assertNotIn("uH", lines[0])
        self.assertIn("+821 pH", lines[0])

    def test_the_THREE_NUMBERS_are_inside_the_narrowest_measured_budget(self):
        """
        This Label is the narrowest clipping strip in the window -- it sits
        under the plot, in the RIGHT half of a horizontal split, so it is only
        as wide as the plot.

        MEASURED on the real widget with a row selected, the interval line
        being 107 characters: **51 visible at 1020x700** (329 px), 93 at
        1500x900 (569 px) and 29 at the 720x420 minimum (179 px).  With the
        prose first the line read `M over series inductance ∈ [0, ∞), AWAY
        FROM THE PO` and the interval, the ideal and the open -- the three
        numbers this pane exists to report -- were ALL off screen at 1020x700,
        with only `ideal` surviving even at 1500 px.  This is the same defect
        as the badge's and the sign strip's, in the caption the plot is read
        with.

        52 characters is the measured 1020x700 budget.  Mutation: put the
        prose back in front.
        """
        for name, sw in (("pole", fake_sweep(POLE_LAM, POLE_RES, POLE_C0)),
                         ("no pole", fake_sweep(-POLE_LAM, -POLE_RES,
                                                POLE_C0))):
            head = ag.sweep_caption(sw)[0][:52]
            with self.subTest(case=name):
                self.assertIn("ideal", head)
                self.assertIn("open", head)
                self.assertTrue(head.startswith("M ∈ ["), head)
        # And the POLE line leads with WHERE, not with prose: 56 characters is
        # the measured budget for line 2 at the same size.
        pole_head = ag.sweep_caption(
            fake_sweep(POLE_LAM, POLE_RES, POLE_C0))[1][:56]
        self.assertIn("100 nH", pole_head)

    def test_the_pole_is_a_STATEMENT_OF_ITS_OWN_naming_where_it_is(self):
        """Rule 8's mandatory "LEAVES the [ideal, open] bracket" label has to
        survive, and the pole line is where it now lives -- with the location
        and the whole-range interval the headline no longer carries.

        Mutation: drop the pole line and the caption says the curve is bounded
        by numbers it leaves by five orders of magnitude.
        """
        lines = ag.sweep_caption(fake_sweep(POLE_LAM, POLE_RES, POLE_C0))
        joined = "\n".join(lines)
        self.assertIn("POLE at 100 nH", joined)
        self.assertIn("LEAVES the [ideal, open] bracket", joined)
        self.assertIn("OFF-SCALE", joined)
        # The arithmetic is not hidden, only demoted.
        self.assertIn("whole half-line, pole included", joined)

    def test_the_caption_and_the_axis_agree_because_they_share_one_picture(self):
        """`sweep_caption` takes the SAME `SweepPicture` the axis was drawn
        from.  Two computations of "where is the pole" is how a caption comes
        to name a line that is not on the plot."""
        sw = fake_sweep(POLE_LAM, POLE_RES, POLE_C0)
        pic = ag.sweep_picture(sw)
        self.assertEqual(ag.sweep_caption(sw, pic), ag.sweep_caption(sw))


# ============================================================================
# PURE: the across-frequency badge  (item 3)
# ============================================================================

class TestStabilityOffer(unittest.TestCase):
    """
    Item 3: "not checked" is too soft a default for the thing acceptance item 5
    is about, and turning the check ON unconditionally costs a re-solve per
    frequency.  So the OFF state carries the action.
    """

    def test_the_off_state_names_what_the_check_would_COST(self):
        """Mutation: go back to a bare caveat and the reader is left with a
        warning and no way to act on it."""
        line = ag.stability_offer(5, 153)
        self.assertIn("4 more solves", line)
        self.assertIn("153-port", line)

    def test_the_off_state_names_the_GESTURE(self):
        self.assertIn(ag.EXPAND_COLLAPSED, ag.stability_offer())

    def test_the_badge_default_IS_the_offer(self):
        """One frequency is not a check, and the line that says so is the same
        one that offers to run it -- not two wordings to drift apart."""
        self.assertEqual(ag.stability_line([1e9], [{}]), ag.stability_offer())

    def test_a_stable_ranking_is_a_RESULT_and_is_said_in_those_words(self):
        line = ag.stability_line([1e8, 1e10], [{"a": 1, "b": 2},
                                               {"a": 1, "b": 2}])
        self.assertIn("STABLE", line)
        self.assertIn("nothing changed places", line)

    def test_a_moved_rank_says_WHAT_moved_and_AT_WHICH_FREQUENCY(self):
        """Naming the elements was not enough: "'a', 'b' change places" leaves
        the reader to go and find out where, which is the re-solve they just
        paid for.

        Mutation: drop the rank pair and the frequency from each entry.
        """
        line = ag.stability_line([1e8, 1e9, 1e10],
                                 [{"a": 1, "b": 2}, {"a": 1, "b": 2},
                                  {"a": 2, "b": 1}])
        self.assertIn("'a' #1→#2 at 10 GHz", line)
        self.assertIn("'b' #2→#1 at 10 GHz", line)
        # And it says which frequency the table on screen belongs to.
        self.assertIn("belongs to 100 MHz only", line)

    def test_an_element_that_VANISHES_is_absent_and_not_a_rank(self):
        """A lumped element whose admittance vanishes at one frequency is
        dropped there, so the column has no entry for it.  Printing a number
        would invent one."""
        line = ag.stability_line([1e8, 1e10], [{"a": 1, "b": 2}, {"a": 1}])
        self.assertIn("'b' #2→absent at 10 GHz", line)


# ============================================================================
# PURE: the ground model  (item 4)
# ============================================================================

class TestGroundModelSpelling(unittest.TestCase):
    """
    Item 4.  The window's field and `--attribute-ground-model` are ONE parser,
    reached by a deferred import -- a second copy is how the two come to
    disagree about what `shared:L=1n` means.
    """

    OMEGA = 2 * math.pi * 5e9

    def test_the_default_is_the_declared_model(self):
        kind, z, label = ag.parse_ground_model(ag.GROUND_MODEL_DEFAULT,
                                               self.OMEGA)
        self.assertEqual(kind, "diag")
        self.assertIsNone(z)
        self.assertIn("declared", label)

    def test_shared_takes_the_CLI_spelling(self):
        kind, z, label = ag.parse_ground_model("shared:L=1n", self.OMEGA)
        self.assertEqual(kind, "shared")
        self.assertAlmostEqual(z.imag, self.OMEGA * 1e-9, places=6)
        self.assertEqual(label, "shared:L=1n")

    def test_it_IS_the_CLI_parser_and_not_a_copy(self):
        """Mutation: reimplement the grammar here.  Both then pass their own
        tests and `shared:0.3n` (no `R=`/`L=`) is refused by one and accepted
        by the other."""
        import pkg_rlc_extractor as cli
        for spec in ("diag", "diag:L=1n", "shared:R=0.5,L=1n"):
            with self.subTest(spec=spec):
                self.assertEqual(ag.parse_ground_model(spec, self.OMEGA),
                                 cli._attr_ground_model(spec, self.OMEGA))

    def test_a_bad_model_is_refused_with_the_CLI_s_own_wording(self):
        with self.assertRaises(ValueError) as cm:
            ag.parse_ground_model("shared:", self.OMEGA)
        self.assertIn("needs an impedance after the colon", str(cm.exception))

    def test_the_export_names_the_model_and_carries_its_measurement(self):
        joined = "\n".join(ag.provenance_lines(
            fake_prov(ground_model="shared:L=1n",
                      ground_model_label="shared:L=1n")))
        self.assertIn("Ground model: shared:L=1n", joined)
        self.assertIn("9.60 dB", joined)

    def test_the_sign_strip_states_the_model_in_force(self):
        line = ag.sign_strip_text("shared:L=1n")
        self.assertIn("Grounds: shared:L=1n", line)
        self.assertTrue(line.startswith(ag.SIGN_STRIP_TEXT))
        # The sign rule and the MODEL own the front of the line -- the strip's
        # whole budget at 150% / 720 px is 48 characters, and a model that is
        # not inside it is not on screen at that scaling at any supported
        # width.  The shares rule follows; see the layout test for the trade.
        # `Grounds:` and the START of the model, not the whole of it -- a long
        # enough model name cannot fit any budget, and the label is in a
        # proportional font so 48 characters is the measured worst case rather
        # than an exact cut.
        for word in ("opposes", "adds", "Grounds: shared"):
            self.assertIn(word, line[:48])
        self.assertIn("SIGNED", line)

    def test_the_hint_says_why_the_default_is_not_obviously_right(self):
        self.assertIn("understate", ag.GROUND_MODEL_HINT)
        self.assertIn("shared:", ag.GROUND_MODEL_HINT)

    def test_the_export_carries_the_parsers_NOTES_about_the_model(self):
        """
        `_attr_zt` returns notes and the window used to drop them on the floor
        (`zt, _gm_notes = ground_model_zt(...)`), while the CLI prints them in
        `header_notes`.  One of them means the model was NOT APPLIED.

        MEASURED on coupled_4port_diff.s4p, probes 1/3, one connection row
        `2 short_to 4` (a legal spelling of the same network), `shared:L=1n` +
        [Recompute]: `_attr_zt` returned `zt is None` with *"The ground model
        was ignored: this spec declares no shunt element … there is no ground
        lead to model."*, the numbers stayed the declared network's and
        `reference_applicable` stayed True -- while the sign strip read
        `Grounds: shared:L=1n` and both exports headed the block
        `Ground model: shared:L=1n`.  A reader who typed it, saw nothing move
        and read that strip concludes the shared return is worth 0 dB.

        Mutation: drop the notes loop from `provenance_lines`.
        """
        joined = "\n".join(ag.provenance_lines(fake_prov(
            ground_model="shared:L=1n",
            ground_model_label="shared:L=1n — NOT APPLIED",
            ground_model_notes=("The ground model was ignored: this spec "
                                "declares no shunt element.",),
            ground_model_applied=False)))
        self.assertIn("NOT APPLIED", joined)
        self.assertIn("declares no shunt element", joined)

    def test_a_model_that_WAS_applied_carries_no_such_marker(self):
        """The other half, or "NOT APPLIED" would be decoration."""
        joined = "\n".join(ag.provenance_lines(
            fake_prov(ground_model="shared:L=1n",
                      ground_model_label="shared:L=1n")))
        self.assertNotIn("NOT APPLIED", joined)
        self.assertIn("Ground model: shared:L=1n", joined)


# ============================================================================
# PURE: session state
# ============================================================================

class _FakeApp:
    """Something weak-referenceable to key the per-App stores off."""


class TestSessionState(unittest.TestCase):
    def test_none_gives_an_empty_dict(self):
        self.assertEqual(ag.attribution_session_state(None), {})

    def test_a_bad_version_is_reported_and_nothing_is_restored(self):
        app = _FakeApp()
        notes = ag.apply_attribution_session_state(
            app, {"version": 99, "windows": [{"trace_id": 1}]})
        self.assertEqual(len(notes), 1)
        self.assertIn("99", notes[0])

    def test_the_choices_round_trip_and_the_note_says_why_nothing_reopened(self):
        app = _FakeApp()
        notes = ag.apply_attribution_session_state(app, {
            "version": ag.ATTRIB_SESSION_VERSION,
            "windows": [{"trace_id": 4, "victim": "v", "aggressor": "a",
                         "quantity": "k", "freq_ghz": 5.6, "view": "sens"}]})
        self.assertEqual(len(notes), 1)
        self.assertIn("needs numbers", notes[0])
        self.assertEqual(ag._RESTORED[app][4]["quantity"], "k")

    def test_a_malformed_entry_costs_its_own_entry_and_never_raises(self):
        """The session file is readable text and WILL be hand-edited."""
        app = _FakeApp()
        notes = ag.apply_attribution_session_state(app, {
            "version": ag.ATTRIB_SESSION_VERSION,
            "windows": [{"trace_id": "not a number"},
                        {"trace_id": 2, "victim": "v", "aggressor": "a"}]})
        self.assertEqual(len(notes), 2)
        self.assertIn("malformed", notes[0])
        self.assertIn(2, ag._RESTORED[app])

    def test_junk_instead_of_a_dict_is_ignored(self):
        self.assertEqual(
            ag.apply_attribution_session_state(_FakeApp(), None), [])
        self.assertEqual(
            ag.apply_attribution_session_state(_FakeApp(), []), [])


# ============================================================================
# TK: glyph widths -- the premise of the whole monospace argument
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestGlyphWidths(unittest.TestCase):
    """
    MEASURE, do not assume.  Every claim about the tables lining up rests on
    every glyph they can emit being exactly one cell wide in Consolas 9, and
    on the signed pair being width-stable.  The comparison font is shown too,
    because that difference is the reason this is not a Treeview.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.mono = tkfont.Font(family=ag.ATTRIB_FONT[0], size=ag.ATTRIB_FONT[1])
        cls.prop = tkfont.nametofont("TkDefaultFont")

    @classmethod
    def tearDownClass(cls):
        # The Font objects go FIRST and the interpreter is pumped before it is
        # torn down.  Measured: destroying this root while named-font handles
        # were still alive left ttk's ThemeChanged to fire into a dead
        # interpreter, and the error surfaced inside the NEXT class's first
        # test ("can't invoke event command: application has been destroyed"),
        # which reads as a failure of a test that has nothing to do with it.
        cls.mono = cls.prop = None
        cls.root.update_idletasks()
        cls.root.update()
        cls.root.destroy()

    def test_every_glyph_a_table_can_emit_is_one_cell_in_consolas(self):
        # Measured here: all of these are 7 px.  U+2713 is 12 px and is
        # therefore banned from the tables -- see the next test.
        cell = self.mono.measure("0")
        self.assertEqual(cell, 7)
        for g in (" ", "0", "9", "-", ag.PLUS, ag.MINUS, ag.ATTRIB_SWATCH,
                  ag.EXPAND_COLLAPSED, ag.EXPAND_EXPANDED, ".", "M", "X",
                  "(", ")", "%", "j", "…", "Ω", "←",
                  "─"):
            with self.subTest(glyph=hex(ord(g))):
                self.assertEqual(self.mono.measure(g), cell)

    def test_the_tables_never_emit_a_glyph_that_is_not_one_cell(self):
        """Guards the ban directly, off a real rendered table.

        Mutation: put a '✓' in a cell (it is 12 px, measured) and this
        goes red.
        """
        cell = self.mono.measure("0")
        dec = fake_dec([_term(None, 1e-12, 0.1),
                        _term(_elem(0), 9e-10, 0.9),
                        _term(_elem(1, kind="short", port=5), -1e-11, -0.01)])
        for table in (ag.contributions_table(dec, "smart"),
                      ag.contributions_table(dec, "aligned")):
            for line in table.lines:
                for ch in set(line):
                    with self.subTest(ch=hex(ord(ch))):
                        self.assertEqual(self.mono.measure(ch), cell)

    def test_the_signed_pair_is_width_stable_where_a_proportional_font_is_not(self):
        """The measurement that rules out a right-aligned Treeview column.

        Measured in TkDefaultFont: '-' 5 px, '+' 9, U+2212 9, '.' 3, ' ' 4,
        digits 7 -- so a column of signed values has its decimal point
        wandering.  In Consolas every one of them is 7.
        """
        self.assertEqual(self.mono.measure(ag.PLUS),
                         self.mono.measure(ag.MINUS))
        self.assertEqual(self.mono.measure(ag.PLUS), self.mono.measure(" "))
        widths = {self.prop.measure(c) for c in ("-", "+", "−", ".", " ")}
        self.assertGreater(len(widths), 1,
                           "the proportional font would have been fine after "
                           "all -- re-derive the Treeview rejection")

    def test_the_badge_expander_pair_is_width_stable_in_BOTH_fonts(self):
        """The badge lives in a ttk.Label, so the proportional font is the one
        that matters there; it is measured in both because the same pair is
        allowed inside a table."""
        for f in (self.mono, self.prop):
            self.assertEqual(f.measure(ag.EXPAND_COLLAPSED),
                             f.measure(ag.EXPAND_EXPANDED))


# ============================================================================
# TK: the window
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _WindowCase(unittest.TestCase):
    """
    A real App with a real Calculate behind it.

    Not shared between tests: several of them measure live geometry or mutate
    app state (removing a trace, editing a spec), and sharing an App across
    those is exactly the silent cross-contamination this repo refuses.
    """

    @classmethod
    def setUpClass(cls):
        cls.ts = parse_touchstone(FIXTURES / FIXTURE)

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(self.ts)
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=6,
                              label="coil", gnd_ports="2,4",
                              mports=[MeasPortRow("vic", "1", ""),
                                      MeasPortRow("agg", "3", "")])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app.rlc_freq_var.set("5.1")
        self.app._on_calculate()
        self._settle()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _settle(self, rounds=6):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _open(self, mapped=False):
        if mapped:
            self.app.deiconify()
        win = ag.open_attribution_window(self.app, self.tc)
        self.assertIsNotNone(win, "the window refused to open")
        if mapped:
            win.deiconify()
        self._settle(10)
        return win


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowOpens(_WindowCase):
    def test_calculate_really_produced_a_coupling_matrix(self):
        """Precondition, asserted rather than assumed: without it every test
        below would be exercising the refusal path instead."""
        self.assertIsNotNone(self.tc.Zmat)
        self.assertEqual(list(self.tc.mport_names), ["vic", "agg"])
        self.assertFalse(self.tc.stale)

    def test_it_opens_and_registers_itself(self):
        win = self._open()
        self.assertIsInstance(win, ag.AttributionWindow)
        self.assertEqual(ag.live_windows(self.app), [win])

    def test_it_is_MODELESS(self):
        """No grab_set anywhere: a modal Toplevel that outlives its opener
        blocks event delivery and update() never returns."""
        self._open()
        self.assertIsNone(self.app.grab_current())

    def test_it_is_deliberately_NOT_transient(self):
        """Decision 1, and it is a decision, not an omission.

        `transient` removes the taskbar button and the Alt-Tab entry on Windows
        and makes the WM withdraw the child with its master; this window is
        read against the plot and the editor over many edits and is meant to be
        parkable on a second monitor.  PortRolesWindow sets it and is right to.
        """
        win = self._open()
        self.assertEqual(win.wm_transient(), "")

    def test_reopening_the_same_pair_raises_the_window_instead_of_duplicating(self):
        first = self._open()
        second = ag.open_attribution_window(self.app, self.tc)
        self.assertIs(second, first)
        self.assertEqual(len(ag.live_windows(self.app)), 1)

    def test_destroying_it_deregisters_it(self):
        win = self._open()
        win.destroy()
        self._settle(2)
        self.assertEqual(ag.live_windows(self.app), [])

    def test_it_adds_no_bind_all_of_its_own(self):
        """Rule 9: `bind_all` reaches every Toplevel.  Measured elsewhere in
        this repo: Ctrl+S typed into a Toplevel Entry fires the App's
        _on_save_config, and Ctrl+O would open Load Config and replace the very
        trace this window describes.

        Mutation: add `self.bind_all("<Control-o>", ...)` in __init__.
        """
        before = set(self.app.bind_all())
        self._open()
        self.assertEqual(set(self.app.bind_all()), before)

    def test_it_registers_nothing_with_the_apps_wheel_router(self):
        """Rule 9: "Canvas" is not in App._WHEEL_OWNERS, so a registered
        scrollable ANCESTOR would capture the wheel over the matplotlib canvas.
        There is none, by construction; "Text" is in the set and scrolls
        itself."""
        before = set(self.app._scrollables)
        win = self._open()
        self.assertEqual(set(self.app._scrollables), before)
        self.assertIn("Text", App._WHEEL_OWNERS)
        self.assertNotIn("Canvas", App._WHEEL_OWNERS)
        self.assertEqual(win.table.winfo_class(), "Text")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowLayout(_WindowCase):
    """
    Pixels, measured off a MAPPED window at both declared sizes.

    A withdrawn root answers 0 to every geometry query -- including
    `winfo_ismapped()`, which is the very thing being asserted -- so the App is
    deiconified and so is the Toplevel.
    """

    def _at(self, geom, win):
        win.geometry(geom)
        self._settle(12)
        return win

    def test_the_header_controls_lie_WHOLLY_inside_the_strip_at_both_sizes(self):
        """The header is a ReflowRow, and place() keeps a placed widget MAPPED
        while it hangs off the right edge -- so `winfo_ismapped()` proves
        nothing here and the assertion is containment.

        Mutation: replace ReflowRow with `pack(side=LEFT)` in `_build_ui`.
        Measured: the six items ask 970 px against a 964 px strip at the
        980 px default and 704 px at the minimum, so pack -- which unmaps from
        the END -- takes [Recompute] off screen outright.
        """
        win = self._open(mapped=True)
        for geom in (ag.ATTRIB_GEOMETRY,
                     f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}"):
            self._at(geom, win)
            sw, sh = win.header.winfo_width(), win.header.winfo_height()
            self.assertGreater(sw, 1, "the strip never got a real width")
            for w, _padx, _fill in win.header._items:
                with self.subTest(geom=geom, widget=str(w)):
                    self.assertLessEqual(w.winfo_x() + w.winfo_width(), sw)
                    self.assertLessEqual(w.winfo_y() + w.winfo_height(), sh)
                    self.assertGreaterEqual(w.winfo_x(), 0)

    def test_the_header_wraps_only_when_it_has_to(self):
        """A wrap costs pane height, so it must not be unconditional.

        Measured with the default trace label: one row (29 px) at 980 and two
        (58 px) at 720.  Mutation: force two rows always and the default window
        pays 29 px it does not owe.
        """
        win = self._open(mapped=True)
        self._at(ag.ATTRIB_GEOMETRY, win)
        wide = win.header.winfo_height()
        self._at(f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}", win)
        narrow = win.header.winfo_height()
        self.assertLess(wide, narrow)

    def test_the_header_strip_keeps_its_requested_width_out_of_the_window(self):
        """place() does not propagate -- that is half of why ReflowRow exists.

        Mutation: pack the items instead and the strip's ~970 px request
        travels up and forces the Toplevel wider than the user set it.
        """
        win = self._open(mapped=True)
        self._at(f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}", win)
        self.assertLessEqual(win.header.winfo_reqwidth(), 2)
        self.assertGreater(sum(win.header.item_widths()), ag.ATTRIB_MIN_W)

    def test_everything_is_on_screen_at_both_declared_sizes(self):
        """pack unmaps from the END, so the order in `_build_ui` is the whole
        of this guard.

        Mutation: pack the PanedWindow BEFORE the footer and the buttons go
        first; or restore the Text `height=8` / the 420x240 canvas request and
        the ttk.Panedwindow starves its FIRST pane -- measured at 720x420, the
        split wanted 445 px, got 168, and the TABLE read
        `winfo_ismapped() == 0` while the detail pane below it was fine.
        """
        win = self._open(mapped=True)
        wanted = (("Export CSV", win.csv_btn), ("Copy report", win.copy_btn),
                  ("reconciliation", win.recon), ("banner", win.banner),
                  ("sign strip", win.sign_lbl), ("badge", win.badge_btn),
                  ("Recompute", win.recompute_btn), ("table", win.table),
                  ("detail", win.detail),
                  ("sweep canvas", win.canvas.get_tk_widget()))
        for geom in (ag.ATTRIB_GEOMETRY,
                     f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}"):
            self._at(geom, win)
            for name, w in wanted:
                with self.subTest(geom=geom, widget=name):
                    self.assertEqual(w.winfo_ismapped(), 1,
                                     f"{name} is off screen at {geom}")

    # A string that is unambiguously wider than the 704 px strip at the
    # minimum size: measured, the composed sign strip is 838 px in
    # TkDefaultFont and doubling it is 1676.  Whatever text a verdict happens
    # to carry, the strips must not grow when handed this.  It is built from
    # `sign_strip_text`, not from `SIGN_STRIP_TEXT`, because the constant is
    # only the front of the line now -- at 29 characters it measured 386 px
    # against a 964 px strip, so doubling THAT stopped being wider than the
    # widget and the precondition assertion below caught it.
    LONG = (ag.sign_strip_text("shared:L=0.3n") + " ") * 2

    def test_the_three_strips_stay_ONE_line_however_long_their_text_is(self):
        """`wraplength=0` -- they clip, they do not wrap.

        The text is pushed in by hand rather than left to whatever the current
        verdict happens to say: measured, the reconciliation line for a clean
        decomposition is only 546 px, so a `wraplength` regression of 600 or
        940 would not wrap it and the test would pass while the NEXT, longer
        verdict blew the budget.  Mutation: `wraplength=300` on any of the
        three (or the usual "bind wraplength to the window width" idiom) and
        this goes red -- measured, that strip becomes 38 px, and three of them
        cost 51 px of a split budget that at 720x420 is 213 px.
        """
        win = self._open(mapped=True)
        one = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        strips = (("banner", win.banner), ("sign", win.sign_lbl),
                  ("recon", win.recon))
        for _name, w in strips:
            w.configure(text=self.LONG)
        for geom in (ag.ATTRIB_GEOMETRY,
                     f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}"):
            self._at(geom, win)
            for name, w in strips:
                with self.subTest(geom=geom, strip=name):
                    self.assertLessEqual(w.winfo_height(), one + 6)
                    # Precondition: the text really is wider than the strip,
                    # or "it did not wrap" is a statement about nothing.
                    self.assertGreater(w.winfo_reqwidth(), w.winfo_width())

    def test_the_declared_minimum_is_really_enforced_by_Tk(self):
        win = self._open(mapped=True)
        self.assertEqual(tuple(win.minsize()),
                         (ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTableWidget(_WindowCase):
    def test_the_table_is_the_monospace_Text_and_not_a_Treeview(self):
        win = self._open()
        self.assertEqual(win.table.winfo_class(), "Text")
        # Tk normalises a font tuple to the string "Consolas 9", so the family
        # is read back through the font object rather than compared as a tuple.
        actual = tkfont.Font(win, font=win.table.cget("font")).actual()
        self.assertEqual(actual["family"], ag.ATTRIB_FONT[0])
        self.assertEqual(actual["size"], ag.ATTRIB_FONT[1])
        self.assertEqual(str(win.table.cget("wrap")), "none")

    def test_its_columns_line_up_MEASURED_in_its_own_font(self):
        """Character counts are not the claim; pixels are.

        Mutation: put a proportional font on the Text (or a non-7px glyph in a
        cell) and the rendered lines stop being the same width.
        """
        win = self._open()
        font = tkfont.Font(win, font=win.table.cget("font"))
        body = win.table.get("1.0", "end-1c").split("\n")
        self.assertGreaterEqual(len(body), 4)
        widths = {font.measure(ln) for ln in body if ln}
        self.assertEqual(len(widths), 1, f"ragged: {body}")

    def test_rows_are_tagged_by_ELEMENT_KIND_and_never_by_sign(self):
        """Rule 4: red is WARN_FG everywhere else here, and a red negative
        makes a correct answer look like a fault."""
        win = self._open()
        self.assertIn("kind_ground", win.table.tag_names())
        self.assertIn("kind_bare", win.table.tag_names())
        line = win._contrib_rows[1][0] + 1
        self.assertIn("kind_ground",
                      win.table.tag_names(f"{line}.0"))
        gnd = PORT_ROLE_FG[ag.ELEMENT_KIND_ROLE["ground"]]
        self.assertEqual(win.table.tag_cget("kind_ground", "foreground"), gnd)

    def test_clicking_a_row_drives_the_detail_pane(self):
        win = self._open(mapped=True)
        self.assertIsNone(win._selected)
        win._select(win._contrib_rows[1][1])
        self._settle(4)
        self.assertEqual(win._selected, 0)
        self.assertIn("element current", win.detail.get("1.0", "end-1c"))

    def test_the_bare_EM_row_detail_carries_the_return_path_budget(self):
        """It moved out of the footer -- the footer is one clipped line and
        this is a paragraph.  Mutation: put it back in `foot_note` and the
        footer wraps to two lines, costing 16 px of the split budget."""
        win = self._open(mapped=True)
        win._select(None)
        self._settle(2)
        self.assertIn("Return path", win.detail.get("1.0", "end-1c"))

    def test_switching_to_the_sensitivity_view_changes_the_table(self):
        win = self._open()
        before = win.table.get("1.0", "end-1c")
        win._view.set("sens")
        win._on_view_changed()
        self._settle(2)
        after = win.table.get("1.0", "end-1c")
        self.assertNotEqual(before, after)
        self.assertIn("candidate", after)
        self.assertIn("open", after)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSweepCanvas(_WindowCase):
    def test_crossing_the_plot_does_NOT_steal_focus_from_an_entry(self):
        """Rule 8, and the one place this window must differ from
        FullscreenPlotWindow -- stated BEHAVIOURALLY, because the property is
        about focus and not about a binding string.

        Measured here, on a mapped Toplevel: with no extra binding, focus stays
        on the Entry across an <Enter> on the canvas; add
        `widget.bind("<Enter>", lambda e: widget.focus_set())` -- exactly what
        the precedent does -- and focus lands on the canvas.  This window has
        Entry fields directly above the plot, so the user types 5.6 into Freq,
        moves the mouse toward [Recompute], crosses the plot, and the rest of
        the keystrokes vanish.
        """
        win = self._open(mapped=True)
        widget = win.canvas.get_tk_widget()
        entry = win.freq_entry

        def focused() -> str:
            # `focus -lastfor` and NOT `focus_get()`.  `focus_get()` answers
            # about the WM's input focus, so it returns None whenever this
            # process does not own it -- which under the 8-way parallel runner
            # is most of the time, and the test then fails on a machine state
            # that has nothing to do with the binding.  `-lastfor` is the
            # widget that WOULD take focus inside this Toplevel, it is what
            # `focus_set()` writes, and it is WM-independent: verified, with
            # the precedent's binding installed it moves to the canvas and
            # without it stays on the Entry, on a Toplevel that never had WM
            # focus at all.
            return str(win.tk.call("focus", "-lastfor", win))

        entry.focus_set()
        self._settle(4)
        # Precondition, asserted rather than assumed: without it this test
        # passes on a window where nothing was ever focused.
        self.assertEqual(focused(), str(entry))
        widget.event_generate("<Enter>", x=5, y=5)
        self._settle(4)
        self.assertEqual(focused(), str(entry),
                         "crossing the sweep plot stole focus from the "
                         "frequency field")

    def test_matplotlibs_own_enter_handler_is_the_only_one(self):
        """The structural half of the same guard.

        Measured: `bind("<Enter>")` returns one Tcl script per binding, each
        carrying its Python callable's name -- 'enter_notify_event' for
        matplotlib's own.  A plain `bind` REPLACES it (so the name disappears)
        and `bind(..., add="+")` appends a second script.  Both mutations are
        caught here, and neither depends on a WM giving anyone focus.
        """
        script = self.canvas_widget_script(self._open())
        self.assertIn("enter_notify_event", script)
        self.assertEqual(script.count("%#"), 1,
                         "something else is also bound to <Enter>")

    @staticmethod
    def canvas_widget_script(win) -> str:
        return win.canvas.get_tk_widget().bind("<Enter>")

    def test_the_plot_gets_no_M_V_or_Delete_keys(self):
        """It is a read-only what-if curve, not the measurement plot."""
        win = self._open()
        widget = win.canvas.get_tk_widget()
        for seq in ("<Key-m>", "<Key-v>", "<Key-Delete>"):
            self.assertEqual(widget.bind(seq), "")

    def test_it_is_not_drawn_while_it_has_no_size(self):
        """A canvas in an unmapped pane lays out for a 1x1 widget.

        The Toplevel is WITHDRAWN here on purpose: this window is not
        `transient`, so it maps itself even when the App is withdrawn, and a
        test that only withdrew the App would be measuring a canvas that has a
        perfectly good size.

        Mutation: drop the `winfo_ismapped()` / `winfo_width() <= 1` guard in
        `_draw_sweep_if_visible`.
        """
        win = self._open()
        win.withdraw()
        self._settle(4)
        win._selected = 0
        win._sweep_drawn = False
        win._draw_sweep_if_visible()
        self.assertFalse(win._sweep_drawn)

    def test_it_IS_drawn_once_the_pane_is_really_on_screen(self):
        win = self._open(mapped=True)
        win._select(0)
        self._settle(6)
        self.assertTrue(win._sweep_drawn)
        self.assertIn("ideal", win.sweep_note.cget("text"))

    def test_a_quantity_the_module_refuses_to_sweep_says_so(self):
        """`sweep_mobius` refuses M/L_a and k BY NAME -- their scale is itself
        a function of the swept parameter.  An empty plot would read as 'no
        effect'."""
        win = self._open(mapped=True)
        win.quantity_var.set("k")
        win._on_recompute()
        self._settle(4)
        win._select(0)
        self._settle(6)
        self.assertIn("cannot sweep", win.sweep_note.cget("text"))

    def test_the_figure_is_not_a_pyplot_figure(self):
        """pyplot keeps a global registry, so a figure created that way
        outlives the Toplevel that owned it."""
        import matplotlib.pyplot as plt
        win = self._open()
        self.assertNotIn(win.figure, [plt.figure(n) for n in plt.get_fignums()])
        plt.close("all")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRecomputeIsTheOnlyRefresh(_WindowCase):
    """
    Rule 6 -- the one that would have shipped broken.

    An auto-refreshing window would, on the first keystroke, decompose the NEW
    spec, reconcile it against a total taken from the OLD run, find a residual
    of however much the edit changed, and by rule 5 blank its own table.  The
    editor hook therefore updates ONE THING.
    """

    def test_the_editor_hook_moves_the_banner_and_NOTHING_else(self):
        """Mutation: make `refresh_attribution_windows` call `_render` (or
        `_on_recompute`) and the table changes under the reader's hands.
        """
        win = self._open()
        before = win.table.get("1.0", "end-1c")
        before_recon = win.recon.cget("text")
        self.tc.gnd_ports = "2"
        self.tc.stale = True
        ag.refresh_attribution_windows(self.app)
        self._settle(2)
        self.assertEqual(win.table.get("1.0", "end-1c"), before)
        self.assertEqual(win.recon.cget("text"), before_recon)
        self.assertIn("EDITED since", win.banner.cget("text"))

    def test_a_units_switch_DOES_reach_the_table_but_only_via_rerender(self):
        """The unit is a RENDERING choice, not a recorded fact -- the same rule
        that makes `_on_units_mode_changed` repaint every run page.

        Two halves and both are guards.  Mutation A: freeze the units on the
        Provenance (`prov.units_mode` instead of `self._units()`) and this
        window keeps printing `+203 pH` beside a results pane already showing
        `+202.6`.  Mutation B: make `rerender` the default and the editor's
        per-keystroke path redraws the sweep, i.e. a closed-form solve per
        character.
        """
        win = self._open()
        self.assertNotIn("[pH]", win.table.get("1.0", "end-1c"))
        self.app.units_mode_var.set("aligned")
        ag.refresh_attribution_windows(self.app)          # banner only
        self._settle(2)
        self.assertNotIn("[pH]", win.table.get("1.0", "end-1c"))
        ag.refresh_attribution_windows(self.app, rerender=True)
        self._settle(2)
        self.assertIn("[pH]", win.table.get("1.0", "end-1c"))

    def test_an_export_carries_the_units_it_is_being_read_in(self):
        win = self._open()
        self.app.units_mode_var.set("aligned")
        self.assertIn("Units   : aligned", win._report())

    def test_the_hook_never_raises_even_with_a_wrecked_window(self):
        """It runs inside Tk variable traces, where a raise reaches no handler
        anyone controls."""
        win = self._open()
        win._res = None                      # nothing survives this
        ag.refresh_attribution_windows(self.app)
        ag.refresh_attribution_windows(self.app)   # and it is idempotent

    def test_recompute_on_an_edited_spec_stops_claiming_the_run(self):
        """[Recompute] is ALLOWED on a stale trace -- refusing would mean a
        full Calculate of every trace just to re-attribute.  What it may not do
        is keep the run's provenance.

        Mutation: hardcode `spec_matches_run=True` and the exported block is
        headed 'Run: #N' over a different network.
        """
        win = self._open()
        self.assertTrue(win._res.prov.spec_matches_run)
        self.tc.gnd_ports = "2"
        self.tc.stale = True
        win._on_recompute()
        self._settle(2)
        self.assertFalse(win._res.prov.spec_matches_run)
        self.assertIn("EDITED since that run",
                      "\n".join(ag.provenance_lines(win._res.prov)))

    def test_a_bad_frequency_is_reported_and_nothing_moves(self):
        win = self._open()
        before = win.table.get("1.0", "end-1c")
        win.freq_var.set("not a number")
        win._on_recompute()
        self._settle(2)
        self.assertIn("must be a number", win.recon.cget("text"))
        self.assertEqual(win.table.get("1.0", "end-1c"), before)

    def test_the_same_port_twice_is_refused_by_name(self):
        win = self._open()
        win.aggr_var.set(win.victim_var.get())
        win._on_recompute()
        self._settle(2)
        self.assertIn("MUTUAL impedance", win.recon.cget("text"))

    def test_recompute_re_reads_the_measurement_port_NAMES(self):
        """An edit can rename a measurement port, and [Recompute] is allowed on
        an edited spec.

        Mutation: drop `_refresh_port_choices` and the combobox keeps offering
        the OLD names -- `decompose` then refuses one of them by name and the
        user has no widget from which to pick the name it just told them
        about.
        """
        win = self._open()
        self.assertEqual(list(win.victim_cb.cget("values")), ["vic", "agg"])
        self.tc.mports[0].name = "victim2"
        self.tc.stale = True
        win._on_recompute()          # refuses: 'vic' is gone
        self._settle(2)
        self.assertEqual(list(win.victim_cb.cget("values")),
                         ["victim2", "agg"])
        win.victim_var.set("victim2")
        win._on_recompute()
        self._settle(2)
        self.assertEqual(win._res.prov.victim, "victim2")

    def test_the_port_comboboxes_are_readonly(self):
        """A typed-in name that does not exist is a refusal with no way back
        through the widget."""
        win = self._open()
        for cb in (win.victim_cb, win.aggr_cb):
            self.assertIn("readonly", str(cb.cget("state")))

    def test_a_real_recompute_moves_the_frequency_it_reports(self):
        win = self._open()
        first = win._res.prov.actual_hz
        win.freq_var.set("1.0")
        win._on_recompute()
        self._settle(2)
        self.assertNotEqual(win._res.prov.actual_hz, first)
        self.assertIn("1 GHz", win.banner.cget("text"))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowOutlivesItsSubject(_WindowCase):
    """
    Rule 11.  PortRolesWindow re-reads app.traces every refresh and degrades;
    this window holds a RESULT, so every removal path has to poke it.  Same
    class of omission as the documented `_on_remove_file` forgot-to-replot bug.
    """

    def test_removing_the_trace_leaves_the_window_alive_and_honest(self):
        win = self._open()
        self.app.traces.remove(self.tc)
        ag.refresh_attribution_windows(self.app)
        self._settle(2)
        self.assertTrue(win.winfo_exists())
        self.assertIn("REMOVED", win.banner.cget("text"))
        self.assertIn("disabled", win.recompute_btn.state())

    def test_the_numbers_it_already_has_are_still_there(self):
        """A record of a measurement that was made does not stop being one."""
        win = self._open()
        before = win.table.get("1.0", "end-1c")
        self.app.traces.remove(self.tc)
        ag.refresh_attribution_windows(self.app)
        self._settle(2)
        self.assertEqual(win.table.get("1.0", "end-1c"), before)

    def test_recompute_after_the_trace_is_gone_says_so_and_does_not_raise(self):
        win = self._open()
        self.app.traces.remove(self.tc)
        win._on_recompute()
        self._settle(2)
        self.assertIn("no longer loaded", win.recon.cget("text"))

    def test_removing_the_FILE_is_caught_too(self):
        win = self._open()
        self.app.files.remove(self.fe)
        ag.refresh_attribution_windows(self.app)
        self._settle(2)
        self.assertTrue(win.winfo_exists())
        self.assertIn("REMOVED", win.banner.cget("text"))

    def test_a_trace_is_matched_by_IDENTITY_and_never_by_in(self):
        """`TraceConfig` is an eq=True dataclass holding numpy arrays, so
        `tc in list` raises "truth value of an array is ambiguous".

        The twin below agrees with the real trace on EVERY config field, which
        is what makes the hazard reachable: a dataclass `__eq__` is a tuple
        comparison and stops at the first unequal element, so a twin that
        differs in `id` never reaches the arrays and `in` works by accident.
        Measured with a twin that agrees up to `Z`: `twin in [tc]` raises
        ValueError while `any(t is twin ...)` answers False.

        Mutation: `if self._trace not in self.app.traces` in `_subject`.  It
        is caught by the raise, not by the verdict, which is why the window
        must still be answering correctly afterwards.
        """
        import dataclasses
        win = self._open()
        self.assertIsNotNone(self.tc.Z, "precondition: the trace has arrays")
        cfg = {f.name: getattr(self.tc, f.name)
               for f in dataclasses.fields(self.tc)
               if f.name not in ("Z", "Zmat", "rlc", "fit", "fit_freqs",
                                 "fit_Z", "mport_names", "coupling")}
        twin = TraceConfig(**cfg)
        twin.Z = np.zeros_like(self.tc.Z)
        with self.assertRaises(ValueError):
            twin in self.app.traces          # noqa: B015 -- that IS the point
        self.app.traces.insert(0, twin)
        ag.refresh_attribution_windows(self.app)      # must not raise
        self._settle(2)
        self.assertNotIn("REMOVED", win.banner.cget("text"))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRefusalAtTheDoor(_WindowCase):
    def test_a_stale_trace_refuses_and_opens_nothing(self):
        self.tc.stale = True
        with _no_dialogs() as shown:
            win = ag.open_attribution_window(self.app, self.tc)
        self.assertIsNone(win)
        self.assertIn("edited since", shown[-1])
        self.assertEqual(ag.live_windows(self.app), [])

    def test_a_frozen_trace_refuses_and_opens_nothing(self):
        self.tc.frozen = True
        with _no_dialogs() as shown:
            win = ag.open_attribution_window(self.app, self.tc)
        self.assertIsNone(win)
        self.assertIn("frozen snapshot", shown[-1])

    def test_opening_FLUSHES_the_editor_first(self):
        """Same rule and same reason as `_on_freeze_trace`: auto-apply is
        deferred to after_idle, so a keystroke in the same event burst as the
        click is still queued and the staleness check would answer about the
        spec from an event ago.

        Mutation: drop the `_flush_editor_sync()` call.  Here the pending sync
        is what makes the trace stale, so without the flush the window opens on
        a spec that is about to change under it.
        """
        self.app.ed_gnd.set_value("2")       # queues a deferred sync
        with _no_dialogs() as shown:
            win = ag.open_attribution_window(self.app, self.tc)
        self.assertIsNone(win, "the flush should have made the trace stale")
        self.assertIn("edited since", shown[-1])


class _no_dialogs:
    """Swallow `messagebox.showinfo` / `showerror` and record what they said.

    A modal dialog in a test HANGS -- it does not fail -- so this is a
    precondition of the refusal tests, not a convenience.
    """

    def __enter__(self):
        from tkinter import messagebox
        self._mb = messagebox
        self._saved = (messagebox.showinfo, messagebox.showerror)
        self.shown: list[str] = []

        def cap(title, message=None, **kw):
            self.shown.append(str(message))
            return "ok"

        messagebox.showinfo = cap
        messagebox.showerror = cap
        ag.messagebox = messagebox
        return self.shown

    def __exit__(self, *a):
        self._mb.showinfo, self._mb.showerror = self._saved
        return False


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestExport(_WindowCase):
    def test_the_csv_is_headed_with_the_whole_provenance(self):
        win = self._open()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.csv"
            ag.write_attribution_csv(str(path), win._res)
            text = path.read_text(encoding="utf-8")
        self.assertIn("# Run     : #", text)
        self.assertIn("# " + at.SIGN_CONVENTION_TEXT.split(".")[0], text)
        self.assertIn("9.60 dB", text)
        self.assertIn("# Reconciliation:", text)
        self.assertIn(",".join(ag.CSV_FIELDS), text)

    def test_the_csv_has_a_row_for_every_term_with_no_floor(self):
        win = self._open()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.csv"
            ag.write_attribution_csv(str(path), win._res)
            body = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("term,")]
        self.assertEqual(len(body), len(win._res.dec.terms))

    def test_copy_report_puts_the_report_on_the_clipboard(self):
        win = self._open(mapped=True)
        win._on_copy()
        self._settle(2)
        self.assertIn("Attribution of M", win.clipboard_get())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestStabilityBadge(_WindowCase):
    def test_it_starts_collapsed_and_says_it_has_not_checked(self):
        win = self._open()
        self.assertEqual(win.badge_btn.cget("text"), ag.EXPAND_COLLAPSED)
        self.assertIn("not checked", win.badge_lbl.cget("text"))

    def test_the_expander_glyph_flips_IMMEDIATELY(self):
        """The work is deferred to the event loop, the glyph is not.

        Mutation: set the glyph only in `_render` (i.e. after the solves) and
        the expander is dead for however long five build_contexts take -- on
        a 153-port file, seconds.
        """
        win = self._open()
        win._on_toggle_stability()
        self.assertEqual(win.badge_btn.cget("text"), ag.EXPAND_EXPANDED)

    def test_expanding_it_computes_a_verdict(self):
        win = self._open()
        win._on_toggle_stability()
        # The work is scheduled with `after(1)`, so the loop must let real
        # wall-clock time pass -- measured, 100 bare `update()` calls go by
        # without the timer ever becoming due, and the callback then fires
        # during tearDown ("invalid command name ..._compute_stability").
        for _ in range(200):
            time.sleep(0.005)
            self._settle(1)
            if win._res.stability:
                break
        self.assertIn("frequencies", win.badge_lbl.cget("text"))
        # A VERDICT, either way -- and the two words are what the badge leads
        # with, because the tail of this line is off screen at 150% (see
        # `stability_line`).  Mutation: return a bare "checked".
        self.assertTrue(win._res.stability.startswith("STABLE")
                        or win._res.stability.startswith("NOT stable"),
                        repr(win._res.stability))

    def test_the_check_is_a_ONE_SHOT_and_the_button_says_so(self):
        """The button was an EXPANDER that expanded nothing.

        MEASURED before this: press -> `▾` plus the verdict; press again ->
        the glyph went back to `▸` and the label text was UNCHANGED, still the
        verdict, with `_badge_row` 27 px throughout.  `_expanded` gated no
        content -- only the glyph -- so the collapse offer was inert, and
        re-running it would spend four more solves on an answer already on the
        line beside it.

        A disabled button needs its reason on screen (the Keep button's rule);
        here the reason is the verdict in the Label next to it.

        Mutation: drop the `state(["disabled"])` in `_render_impl` and the
        second press is inert again.
        """
        win = self._open()
        self.assertNotIn("disabled", win.badge_btn.state())
        win._on_toggle_stability()
        for _ in range(200):
            time.sleep(0.005)
            self._settle(1)
            if win._res.stability:
                break
        verdict = win.badge_lbl.cget("text")
        self.assertIn("disabled", win.badge_btn.state())
        self.assertEqual(win.badge_btn.cget("text"), ag.EXPAND_EXPANDED)
        # A second press changes NOTHING -- glyph, label and state all hold.
        win._on_toggle_stability()
        self._settle(2)
        self.assertEqual(win.badge_lbl.cget("text"), verdict)
        self.assertEqual(win.badge_btn.cget("text"), ag.EXPAND_EXPANDED)
        self.assertIn("disabled", win.badge_btn.state())

    def test_the_check_is_never_run_on_an_automatic_path(self):
        """Each extra frequency is a fresh build_context + decompose, O(N^3) in
        the PORT count.  Only the user's click may start it.

        Mutation: compute it in `_render` and every keystroke on the editor
        hook would pay for five solves of a 153-port file.
        """
        win = self._open()
        ag.refresh_attribution_windows(self.app)
        win._render()
        self._settle(2)
        self.assertEqual(win._res.stability, "")


# ============================================================================
# The gestures that were throwing work away
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestGesturesThatMustNotDiscardWork(_WindowCase):
    """
    Three separate ways this window used to lose a result or select something
    nobody clicked.  Grouped because they are all "a gesture did more than it
    looked like", and kept out of the layout classes so the shards stay small.
    """

    def test_ESCAPE_does_not_destroy_the_window_from_inside_a_field(self):
        """
        A Toplevel is in every descendant's bindtags, so
        `self.bind("<Escape>", ...destroy)` fires from ANYWHERE inside the
        window.  Measured with the binding in place: Escape in the Freq entry,
        in either port combobox, in the table and on the [Recompute] button all
        destroyed it -- the only survivor being an OPEN combobox popdown, which
        grabs the key itself.

        `PortRolesWindow` binds Escape and is right to: it is a read-only list
        that rebuilds from live state on reopen.  This window HOLDS a result --
        a Recompute, plus five more `build_context` + `decompose` passes if the
        badge was expanded, all O(N^3) in the port count -- and nothing
        restores it (`_RESTORED` is only ever filled from a session file).
        Backing out of a half-typed frequency with the key everyone uses for
        that must not throw the lot away.

        Mutation: restore `self.bind("<Escape>", lambda _e: self.destroy())`.
        """
        win = self._open(mapped=True)
        for name, widget in (("freq entry", win.freq_entry),
                             ("victim combobox", win.victim_cb),
                             ("table", win.table),
                             ("Recompute", win.recompute_btn)):
            with self.subTest(widget=name):
                widget.focus_set()
                self._settle(2)
                widget.event_generate("<Escape>")
                self._settle(2)
                self.assertTrue(win.winfo_exists(),
                                "Escape in the " + name + " destroyed the "
                                "window and everything it had computed")

    def test_a_click_BELOW_the_last_row_selects_nothing(self):
        """
        Tk's `@x,y` index CLAMPS to the nearest existing line, so a click in
        the empty space under the table resolves to the last row.  Measured
        with a 5-line table in a 222 px widget: `index("@50,218")` -- about
        150 px below the last text line -- returned "5.51" and selected the
        final element, which silently re-drives the detail pane and runs a
        closed-form sweep solve for something nobody clicked.

        Mutation: drop the bbox test in `_on_table_click`.
        """
        win = self._open(mapped=True)
        win.geometry("980x700")
        self._settle(12)
        win._selected = None
        h = win.table.winfo_height()
        self.assertGreater(h, 40, "the table never got a height")
        # TWO preconditions, and both are what makes this a test rather than a
        # tautology.  (a) the click is BELOW the last row's own box; (b) Tk's
        # `@x,y` nevertheless answers with that row's line number -- which is
        # the clamp, and the whole reason a bare "did any row claim this line"
        # test is not enough.
        last = win._contrib_rows[-1][0] + 1
        box = win.table.bbox(str(last) + ".0")
        self.assertIsNotNone(box, "the last row is not on screen")
        self.assertGreater(h - 4, box[1] + box[3],
                           "the click did not land below the last row")
        clamped = win.table.index("@50," + str(h - 4)).split(".")[0]
        self.assertEqual(int(clamped), last,
                         "Tk stopped clamping; this test needs rewriting "
                         "around whatever it does now")

        class _Ev:
            x = 50
            y = h - 4

        win._on_table_click(_Ev())
        self.assertIsNone(win._selected,
                          "clicking empty space selected a row")

    def test_a_click_ON_a_row_still_selects_it(self):
        """The other half: the guard must not have disabled selection.

        Mutation: return unconditionally from `_on_table_click`."""
        win = self._open(mapped=True)
        win.geometry("980x700")
        self._settle(12)
        line, key, _kind = win._contrib_rows[1]
        box = win.table.bbox(str(line + 1) + ".0")
        self.assertIsNotNone(box, "that row is not on screen to be clicked")

        class _Ev:
            x = box[0] + 2
            y = box[1] + 2

        win._on_table_click(_Ev())
        self.assertEqual(win._selected, key)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheDetailPaneSaysWhatItRefused(_WindowCase):
    """The candidates field, the sweep caption, and the plot they share."""

    def test_a_REFUSED_candidate_reaches_a_widget(self):
        """
        `candidate_list("open, R=5 m", omega)` produces exactly the message
        this repo's `_rlc_tokens` rule demands -- "'R=5 m' would silently mean
        5 Ohm, not 5 mOhm" -- and NONE of it used to reach the screen:
        `_alternatives` wrote it into `sweep_note` and `_draw_sweep` overwrote
        that Label later in the same `_render()` pass.  Measured: `sweep_note`
        held the sweep caption, `foot_note` was empty, the reconciliation strip
        was unchanged, and the Sensitivity table simply went from four rows to
        two with its own note saying "2 rows" and nothing else.

        Mutation: have `_set_sweep_note` ignore `self._cand_problems`.
        """
        win = self._open(mapped=True)
        win.cand_var.set("open, R=5 m")
        win._on_candidates_changed()
        self._settle(6)
        self.assertTrue(win._cand_problems, "the parser did not refuse it")
        self.assertIn("R=5 m", win.sweep_note.cget("text"))
        # And the row count is no longer the only thing the table says.
        win._view.set("sens")
        win._on_view_changed()
        self._settle(4)
        self.assertIn("REFUSED", win.table_note.cget("text"))

    def test_a_good_candidate_leaves_no_complaint_behind(self):
        """Mutation: never clear `_cand_problems` and a fixed typo keeps
        being reported forever."""
        win = self._open(mapped=True)
        win.cand_var.set("open, R=5 m")
        win._on_candidates_changed()
        self._settle(4)
        win.cand_var.set("open, ideal")
        win._on_candidates_changed()
        self._settle(4)
        self.assertEqual(win._cand_problems, [])
        self.assertNotIn("R=5 m", win.sweep_note.cget("text"))

    def test_selecting_a_row_does_not_collapse_the_plot(self):
        """
        The caption used to be a wrapping Label with a 957-character text and a
        293 px request, packed `side=BOTTOM` against an `expand=True` canvas --
        so pack satisfied the canvas's 90 px FLOOR and handed the note the
        rest.  Measured before the cap: 194 -> 90 px of canvas at 980x700 and
        274 -> 90 at 1400x900, i.e. the floor that exists to stop the canvas
        starving the table had become the plot's permanent size.

        The tolerance is a MEASURED CONSTANT and not `sweep_note.reqheight()`.
        Deriving it from the label is self-defeating -- the bug INFLATES that
        very number (21 px capped, 293 px wrapped), so the tolerance grows with
        the defect and the assertion passes through it.  Measured with the
        mutation applied: the naive version stayed green.

        `SWEEP_NOTE_MAX_COST` is three lines of TkDefaultFont plus slack: at
        100% the note is 38 px for two rendered lines and the canvas goes
        194 -> 160 px, against 90 with the bug.

        Mutation: restore `wraplength=420` and drop the `SWEEP_NOTE_LINES`
        cap.
        """
        win = self._open(mapped=True)
        win.geometry("980x700")
        self._settle(14)
        before = win.canvas.get_tk_widget().winfo_height()
        self.assertGreater(before, 120, "the canvas never got a size")
        win._select(win._contrib_rows[1][1])
        self._settle(14)
        after = win.canvas.get_tk_widget().winfo_height()
        self.assertTrue(win.sweep_note.cget("text"),
                        "no caption was written at all")
        # ismapped FIRST, and it is not belt and braces: the caption is packed
        # BEFORE the canvas now (rule 10 inside this frame), so a caption that
        # outgrows the pane does not SHRINK the canvas, it UNMAPS it -- and
        # `winfo_height()` on an unmapped widget answers with its last valid
        # size, which is the healthy 194 px.  Measured with the wrapping
        # mutation applied: `ismapped` 1 -> 0 with `winfo_height()` 194 -> 194.
        self.assertEqual(win.canvas.get_tk_widget().winfo_ismapped(), 1,
                         "the caption pushed the plot off screen")
        budget = (ag.SWEEP_NOTE_LINES + 1) * tkfont.nametofont(
            "TkDefaultFont", root=win).metrics("linespace")
        self.assertGreaterEqual(
            after, before - budget,
            "selecting a row collapsed the plot: " + str(before) + " -> "
            + str(after) + " (budget " + str(budget) + ")")
        self.assertGreater(after, 120)

    def test_the_candidates_field_is_on_screen_at_the_minimum(self):
        """
        The hint Label was packed `side=RIGHT` BEFORE the Entry it describes,
        and pack unmaps from the end.  Measured at 100%: the caption row needs
        103 + 615 + 188 = 920 px, so the entry was 112/188 px wide at 860, 12
        at 760 and `winfo_ismapped() == 0` at the declared 720 minimum -- the
        field gone while the 601 px sentence telling you to type into it was
        still there.  Custom candidates are the whole of what the field is for.

        Mutation: swap the two `pack` calls back.
        """
        win = self._open(mapped=True)
        for w in (1400, 980, 860, 760, ag.ATTRIB_MIN_W):
            win.geometry(str(w) + "x700")
            self._settle(12)
            with self.subTest(width=w):
                self.assertEqual(win.cand_entry.winfo_ismapped(), 1)
                self.assertEqual(win.cand_entry.winfo_width(),
                                 win.cand_entry.winfo_reqwidth(),
                                 "the candidates field is clipped")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheHeaderHearsAboutItsOwnText(_WindowCase):
    def test_a_longer_trace_label_reflows_the_strip(self):
        """
        `ReflowRow._reflow` runs from `add()` and from the strip's own
        `<Configure>`, and a child whose TEXT grew fires neither -- `place`
        then goes on forcing the stale width and the label is CLIPPED, with no
        ellipsis and no overflow marker, which is the exact Treeview failure
        rule 3 rejects.  Measured at 980x700 with the trace relabelled to the
        documented 18-character cap: the item asked 307 px and was placed at
        220 -- 87 px / 14 characters gone in silence -- and the strip went on
        reporting one row while its items asked 1048 px of 964.  A 1 px window
        resize fixed both, which is what makes it a missing notification.

        Mutation: drop `self.header.refresh()` from `_render_impl`.
        """
        win = self._open(mapped=True)
        win.geometry("980x700")
        self._settle(12)
        self.tc.label = "an_18_char_label_x"
        win._on_recompute()
        self._settle(12)
        self.assertEqual(win.trace_lbl.winfo_width(),
                         win.trace_lbl.winfo_reqwidth(),
                         "the header item is placed narrower than it asked "
                         "for, i.e. clipped with no ellipsis")
        strip = win.header
        for w, _padx, _fill in strip._items:
            with self.subTest(widget=str(w)):
                self.assertLessEqual(w.winfo_x() + w.winfo_width(),
                                     strip.winfo_width())
                self.assertLessEqual(w.winfo_y() + w.winfo_height(),
                                     strip.winfo_height())


# ============================================================================
# The window at 150% DPI, and the two places it lost its content
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheDeclaredMinimumShowsContent(_WindowCase):
    """
    A separate class from `TestWindowLayout` on purpose.  `run_parallel.py`
    shards by CLASS and the run's floor is its slowest one -- `TestWindowLayout`
    is already ~55 s for six tests, and these drive a SECOND, rescaled App on
    top of the inherited one.  Splitting keeps either shard off the critical
    path.

    WHAT THIS EXISTS FOR.  The declared minimum used to be a size at which the
    window showed nothing: measured at `tk scaling 2.0` with every named font
    x1.5 (this repo's definition of 150%, the one
    `test_run_history.py::test_the_keep_button_is_READABLE_at_150_percent_font_scaling`
    uses), at exactly 720x420 the whole PanedWindow read
    `winfo_ismapped() == 0` -- table, detail pane and sweep canvas all gone,
    with no scrollbar, no message and no route out, because the user is already
    AT the minimum.  The fixed sections cost 436 px there against a declared
    420.  The pack ORDER was right; what was wrong is that "the table gives up
    height" was allowed to mean "the table gives up all of it".
    """

    def _scaled(self):
        """
        A second App at 150%.  Its own, because the named fonts are
        per-interpreter.

        `tk scaling` is NOT: it is process-wide on Windows (see `TK_SCALING`),
        so it is restored in a cleanup registered AFTER `app.destroy` -- and
        therefore, cleanups being LIFO, run BEFORE it, while there is still an
        interpreter to set it on.  Without that, every later test in the
        process measures a window whose points-to-pixels factor was changed
        under it: measured, the 100% minimum read (720, 430) against a declared
        (720, 420) with nothing in the diff to explain it.
        """
        app = App()
        app.tk.call("tk", "scaling", 2.0)
        for name in tkfont.names(app):
            try:
                f = tkfont.nametofont(name, root=app)
                size = f.cget("size")
                if size:
                    f.configure(size=int(round(abs(size) * 1.5))
                                * (1 if size > 0 else -1))
            except Exception:
                pass
        self.addCleanup(app.destroy)
        if TK_SCALING:
            self.addCleanup(app.tk.call, "tk", "scaling", TK_SCALING)
        fe = FileEntry(self.ts)
        app.files.append(fe)
        app._refresh_file_list()
        app._refresh_file_combobox()
        tc = TraceConfig(id=1, file_label=fe.label, mode=6, label="coil",
                         gnd_ports="2,4",
                         mports=[MeasPortRow("vic", "1", ""),
                                 MeasPortRow("agg", "3", "")])
        app.traces.append(tc)
        app._refresh_trace_list()
        app.traces_lb.selection_set(0)
        app._on_trace_selected()
        app.rlc_freq_var.set("5.1")
        app._on_calculate()
        app.deiconify()
        for _ in range(8):
            app.update_idletasks()
            app.update()
        win = ag.open_attribution_window(app, tc)
        self.assertIsNotNone(win)
        win.deiconify()
        for _ in range(12):
            app.update_idletasks()
            app.update()
        return app, win

    @staticmethod
    def _resize(win, w, h, rounds=16):
        win.geometry(f"{w}x{h}")
        for _ in range(rounds):
            win.update_idletasks()
            win.update()

    def test_at_150_percent_the_content_is_on_screen_at_the_minimum(self):
        """
        Ask for the declared minimum, take what Tk enforces, and assert the
        CONTENT is there.

        Mutation: pin `minsize(ATTRIB_MIN_W, ATTRIB_MIN_H)` back as a constant
        -- measured, the PanedWindow, the table, the detail pane and the sweep
        canvas are then all `winfo_ismapped() == 0` at 720x420, while the
        header, the three strips and all three buttons are fine.
        """
        _app, win = self._scaled()
        self._resize(win, ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H)
        for name, w in (("paned", win.paned), ("table", win.table),
                        ("detail", win.detail),
                        ("sweep canvas", win.canvas.get_tk_widget()),
                        ("Export CSV", win.csv_btn),
                        ("Copy report", win.copy_btn),
                        ("reconciliation", win.recon)):
            with self.subTest(widget=name):
                self.assertEqual(
                    w.winfo_ismapped(), 1,
                    name + " is off screen at the enforced minimum "
                    + str(win.winfo_width()) + "x" + str(win.winfo_height()))

    def test_at_150_percent_the_SWEEP_PLOT_survives_a_row_being_selected(self):
        """
        The state the sweep pane exists for, which the guard above never
        enters -- and it is where the pane was lost.

        With NO selection the caption is one line (40 px) and the canvas 74 px,
        so `winfo_ismapped()` is 1 and nothing is wrong.  SELECT a row and the
        caption becomes the real three-sentence one.  MEASURED before this, on
        coupled_4port_diff.s4p, with a row selected:

            150% 980x700 (the DEFAULT size)  canvas 309x2, sweep_note 112 px
                                             of a 114 px detail pane
            150% 720x678 (the MINIMUM)       canvas 309x2, ismapped() == 0,
                                             and 309 px of it hanging off a
                                             179 px parent -- the only
                                             containment violation in the
                                             window

        i.e. everything item 1 does was invisible at 150% DPI.  `wraplength=0`
        clipping is a WIDTH rule; three lines is a HEIGHT the note takes out of
        the plot, and the note is packed first.

        AFTER: the cap falls to one line and the canvas is 309x74 at 980x700
        and 179x30 at the minimum, mapped and contained at both.  100% is
        untouched -- see the sibling test.

        Mutation: return `SWEEP_NOTE_LINES` unconditionally from
        `_sweep_note_cap`.
        """
        _app, win = self._scaled()
        cw = win.canvas.get_tk_widget()
        for w, h in ((980, 700), (ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H)):
            self._resize(win, w, h)
            win._select(win._contrib_rows[1][1])
            self._resize(win, w, h)
            with self.subTest(asked=f"{w}x{h}"):
                # Precondition: a row really is selected, or the caption is the
                # short one and this measures the state that never broke.
                self.assertIsNotNone(win._selected)
                self.assertEqual(cw.winfo_ismapped(), 1,
                                 "the sweep canvas is off screen")
                # 20 px is not a tautology in either direction: the defect
                # measured 2 px and the fix 74 / 30.
                self.assertGreaterEqual(
                    cw.winfo_height(), 20,
                    f"the plot is {cw.winfo_height()} px tall")
                # And it is INSIDE its parent -- the containment violation.
                self.assertLessEqual(cw.winfo_x() + cw.winfo_width(),
                                     cw.master.winfo_width())
                self.assertLessEqual(cw.winfo_y() + cw.winfo_height(),
                                     cw.master.winfo_height())
                # The caption is still THERE, just shorter: whatever it drops
                # is counted and pointed at Copy report.
                self.assertTrue(win.sweep_note.cget("text").strip())

    def test_the_caption_keeps_all_THREE_lines_at_100_percent(self):
        """The other half of the trade, and the one that says the cap is a
        rule about DPI rather than a cap on the caption.

        At 100% the note is 55 px at every supported size -- including
        720x420, where it sits over a 35 px canvas.  That is the documented
        trade ("six pixels of curve is worth nothing; the sentence saying the
        two endpoints are not a bound is worth the whole pane") and the cap
        must not take it back.

        Mutation: make `_sweep_note_cap` a fraction of the budget rather than
        `budget // line - RESERVE` -- measured, a quarter puts 720x420 at two
        lines, which drops rule 8's mandatory NON-MONOTONIC label into the
        "+N more".
        """
        win = self._open(mapped=True)
        for geom in ("980x700", "720x420"):
            win.geometry(geom)
            self._settle(14)
            with self.subTest(geom=geom):
                self.assertEqual(win._sweep_note_cap(), ag.SWEEP_NOTE_LINES)

    def test_the_100_percent_minimum_is_UNCHANGED(self):
        """The declared value is a FLOOR: nothing about the 100% window moves.

        Measured at 100%: chrome is 207 px at 720 wide and the split needs 124,
        so the computed minimum is 333 -- under the declared 420, which
        therefore still wins.  Mutation: raise `ATTRIB_SPLIT_FLOOR_LINES` past
        15 and the 100% window starts demanding height it does not need.
        """
        win = self._open(mapped=True)
        self._resize(win, ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H)
        self.assertEqual(tuple(win.minsize()),
                         (ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H))

    def test_the_minimum_SETTLES_instead_of_oscillating(self):
        """
        THE hazard of a layout rule that measures: one that reads a size it can
        itself change flips forever and `update()` never returns, taking the
        GUI and the test suite down together (the documented style-picker /
        scrollbar hang).  This one reads the WIDTH and writes only a minimum
        HEIGHT, so it is a fixed point in the same way `ReflowRow` is.

        Driven in both directions, at 150%, where the minimum really does move
        (measured: 634 px at 720 wide, 586 at 980, 538 at 1200 and above).
        Mutation: recompute from the ACTUAL height instead of the requested
        chrome and the tail below stops being constant.
        """
        _app, win = self._scaled()
        for w, h in ((1500, 900), (720, 420), (1200, 500), (720, 420),
                     (980, 700), (1500, 420)):
            win.geometry(str(w) + "x" + str(h))
            seen = []
            for _ in range(40):
                win.update_idletasks()
                win.update()
                seen.append((win.winfo_width(), win.winfo_height(),
                             tuple(win.minsize())))
            tail = seen[-12:]
            with self.subTest(asked=str(w) + "x" + str(h)):
                self.assertEqual(len(set(tail)), 1,
                                 "the layout is still moving after 40 rounds: "
                                 + repr(sorted(set(tail))))
                self.assertEqual(win.table.winfo_ismapped(), 1)

    def test_the_sign_rule_and_the_GROUND_MODEL_survive_the_clip(self):
        """The two things on this strip that can change a NUMBER must be on
        screen at every supported size; the shares rule is what gives way.

        Measured budget for this strip, on the real widget, the composed line
        being 137 characters:

            100% 1500 px  137   100% 980 px  137   100% 720 px  114
            150% 1500 px  106   150% 980 px   66   150% 720 px   48

        With the model LAST -- which is where it was -- `'Grounds:' in shown`
        was False at 980, 860 and 720 px at 150%, i.e. at every supported size
        at that scaling bar a maximised window there was no on-screen statement
        of the model that produced the numbers.  It is worth a measured
        7.19 dB on this fixture and the control beside it shows the FIELD,
        which can have been edited without a Recompute, so "it is also on the
        control" is not a substitute.

        The shares rule clips instead.  That is the deliberate half of the
        trade: it is a reading convention, it is also in the table's column
        heading, in Help and in the README, and no wording of it changes a
        number.  Both are in Copy report and in the CSV in full.

        Mutation: put the model back after the shares rule -- `Grounds` leaves
        the 48-character budget and this goes red.
        """
        _app, win = self._scaled()
        self._resize(win, ag.ATTRIB_MIN_W, ag.ATTRIB_MIN_H)
        font = tkfont.nametofont("TkDefaultFont", root=win)
        avail = win.sign_lbl.winfo_width()
        self.assertGreater(avail, 1, "the strip never got a width")
        # The WIDGET's text, not the constant.  The strip also names the
        # ground model in force (item 4), so the constant is only its front --
        # and clipping the front alone measures a line that is not on screen.
        full = win.sign_lbl.cget("text")
        shown = full
        while shown and font.measure(shown) > avail:
            shown = shown[:-1]
        for word in ("opposes", "adds", "Grounds:"):
            with self.subTest(word=word):
                self.assertIn(word, shown)
        # Precondition: the line really is being clipped, or this asserts
        # nothing at all.
        self.assertLess(len(shown), len(full))
        # And the budget is stated as a CHARACTER COUNT as well, which no
        # scaling accident can move: the sign rule and the model must both be
        # inside the 48 characters measured as this strip's worst case.
        head = ag.sign_strip_text("diag (as declared)")[:48]
        for word in ("opposes", "adds", "Grounds:"):
            with self.subTest(word=word, budget=48):
                self.assertIn(word, head)
        # The shares rule is still SAID -- it just says it later.
        self.assertIn("SIGNED", ag.sign_strip_text("diag (as declared)"))


# ============================================================================
# TK: the four things a person could not use  (items 1-4 on the real window)
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSweepAxisOnTheRealCanvas(_WindowCase):
    """
    Item 1, measured on the shipped fixture, which really does have a pole:
    sweeping the ground GROUP at 5.1 GHz puts two poles at 96.5 nH and 97.0 nH
    (closed form, `t = -Re(lam)`), the analytic interval reads
    (-394 uH, +375 uH) against endpoints of +821 pH and +203 pH, and the raw
    plot was one vertical spike on a `1e-10` axis.
    """

    def _drawn(self):
        win = self._open(mapped=True)
        win.geometry("980x700")
        self._settle(14)
        win._select(win._contrib_rows[1][1])
        self._settle(14)
        return win, win.figure.axes[0]

    def test_the_fixture_really_has_a_pole(self):
        """Precondition, asserted rather than assumed: without it every
        assertion below is about a picture with nothing to exclude."""
        win, _ax = self._drawn()
        self.assertTrue(win._sweep_pic.drawn, "no pole on this sweep")
        self.assertGreater(win._sweep_pic.n_offscale, 0)

    def test_the_pole_is_DRAWN_as_a_vertical_line_at_its_own_value(self):
        """Mutation: drop the `axvline` and the curve simply leaves the frame
        with nothing saying why."""
        win, ax = self._drawn()
        t = win._sweep_pic.clusters[0][0].t
        verticals = [ln for ln in ax.lines
                     if len(set(ln.get_xdata())) == 1
                     and abs(list(ln.get_xdata())[0] - t) < t * 1e-9]
        self.assertEqual(len(verticals), 1,
                         "no single vertical line at the pole")

    def test_the_pole_line_is_LABELLED_with_its_parameter_value(self):
        """A line with no label is a mark, not a reading.

        Mutation: drop the `ax.text`, or label it "pole" with no number.
        """
        win, ax = self._drawn()
        cluster = win._sweep_pic.clusters[0]
        texts = [t.get_text() for t in ax.texts]
        self.assertTrue(texts, "the pole line carries no annotation at all")
        want = ag.pole_span(cluster, "H")
        self.assertTrue(any(want in t for t in texts),
                        f"{want!r} is on no annotation: {texts!r}")

    def test_the_Y_LIMITS_BRACKET_BOTH_PHYSICAL_ENDPOINTS(self):
        """THE assertion.  Ideal and open are the two numbers the pane is
        opened for, and the pole's excursion must not scale them off it.

        Mutation: remove `set_ylim` -- measured, matplotlib then autoscales to
        the excursion and the two endpoints land inside one pixel row.
        """
        win, ax = self._drawn()
        pic = win._sweep_pic
        lo, hi = ax.get_ylim()
        for name, v in (("ideal", 8.21e-10), ("open", 2.03e-10)):
            with self.subTest(endpoint=name):
                self.assertLessEqual(lo, v)
                self.assertGreaterEqual(hi, v)
        # The excursion is off the top, which is the point of the limits.
        # Measured on this fixture: the pole-free high is +962 pH and the
        # sampled peak is +4.17 nH, so 2 nH separates them.
        self.assertLess(hi, 2e-9)
        self.assertGreater(max(abs(v) for v in pic.interval), 0.0)

    def test_the_axis_is_symlog_with_a_linthresh_taken_from_the_data(self):
        win, ax = self._drawn()
        self.assertEqual(ax.get_yscale(), "symlog")
        self.assertAlmostEqual(win._sweep_pic.linthresh, 8.21e-10, places=12)

    def test_the_caption_headline_is_the_POLE_FREE_interval(self):
        """Mutation: hand `sweep_caption` no picture and it recomputes -- which
        is harmless -- or headline `sw.interval` and the label under the plot
        reads `[-394 uH, +375 uH]` beside a picohenry axis, which is what was
        on screen."""
        win, _ax = self._drawn()
        note = win.sweep_note.cget("text")
        self.assertIn("AWAY FROM THE POLE", note)
        self.assertNotIn("uH", note.splitlines()[0])
        self.assertIn("POLE at 96.5 nH", note)

    def test_BOTH_AXES_print_engineering_units_and_no_bare_exponent(self):
        """
        Every other number in this window goes through `format_si`.

        MEASURED before this, on this fixture at 980x700:
        `ax.yaxis.get_offset_text()` read `'1e−10'` over tick labels
        `['−2.5','0.0','2.5','5.0','7.5','10.0']` with an ylabel of `M [H]`,
        beside a table cell reading `+413 pH` and a caption reading
        `ideal +821 pH`.  The original complaint about this plot was literally
        "the y axis reads 1e-5"; the symlog change moved it to 1e-10 and left
        it a bare exponent.  The x axis read `10^-14 … 10^0` in bare henries.

        Mutation: drop either `set_major_formatter`, or put the
        `ScalarFormatter` back -- the offset returns.
        """
        _win, ax = self._drawn()
        for name, axis in (("y", ax.yaxis), ("x", ax.xaxis)):
            with self.subTest(axis=name):
                self.assertEqual(axis.get_offset_text().get_text(), "",
                                 "a bare exponent offset is still in the "
                                 "corner")
                labels = [t.get_text() for t in axis.get_ticklabels()
                          if t.get_text()]
                self.assertTrue(labels, "the axis has no labelled tick")
                # Every label carries the unit, which is what makes it an
                # engineering reading rather than a scaled number.
                self.assertTrue(
                    all("H" in t or t == "0" or t == "--" for t in labels),
                    f"{name} ticks are not in engineering units: {labels!r}")
        # The unit is on the TICKS, so it is not repeated in the axis label.
        self.assertNotIn("[", ax.get_ylabel())
        self.assertNotIn("[", ax.get_xlabel())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheSplitFollowsTheContent(_WindowCase):
    """
    Item 2.  Measured BEFORE, at 980x700 / 100%: `sashpos` 279, the table
    holding 5 rendered lines (70 px of text) in a 239 px widget -- 169 px of
    empty space -- over a detail pane of 198 px that was scrolling
    (`yview` (0, 0.729)) and, at the 720 px minimum, clipping horizontally
    (`xview` (0, 0.950)).  AFTER: `sashpos` 138, table 98 px, detail 331 px,
    `yview` (0, 1.0) and `xview` (0, 1.0).
    """

    def _at(self, win, geom, rounds=16):
        win.geometry(geom)
        self._settle(rounds)

    def _line(self, win) -> int:
        return tkfont.Font(root=win, font=ag.ATTRIB_FONT).metrics("linespace")

    def test_the_table_pane_is_about_as_tall_as_what_is_IN_it(self):
        """The whole of item 2's first half.

        Mutation: drop `_apply_sash` from `_render_impl` and ttk's weight
        split returns -- measured, 239 px of widget for 70 px of text.
        """
        win = self._open(mapped=True)
        self._at(win, "980x700")
        line = self._line(win)
        rendered = int(win.table.index("end-1c").split(".")[0])
        self.assertGreaterEqual(rendered, 3, "nothing in the table to size to")
        slack = win.table.winfo_height() - rendered * line
        self.assertLess(slack, (ag.ATTRIB_SASH_SPARE_LINES + 2) * line,
                        f"{slack} px of empty table under {rendered} lines")
        # BOTH bounds, and the lower one is not redundant: with `_apply_sash`
        # never called from `_render_impl` the paned's own <Configure> still
        # applies the FLOOR (3 lines), which is under the 5 this table holds --
        # so the upper bound alone passed the mutation.  A table that has to
        # scroll its own five rows is the same defect from the other side.
        self.assertEqual(tuple(win.table.yview()), (0.0, 1.0),
                         "the table is scrolling content the pane has room for")
        self.assertGreater(win.detail.winfo_height(),
                           win.table.winfo_height(),
                           "the pane with the reading in it is still smaller")

    def test_thirty_rows_ask_for_more_room_than_three(self):
        """It is derived from the ROW COUNT, so it has to move with it.

        Mutation: a constant sash position passes the test above and fails
        this one.
        """
        win = self._open(mapped=True)
        self._at(win, "980x700")
        win._apply_sash(5)
        self._settle(6)
        small = win.paned.sashpos(0)
        win._apply_sash(32)
        self._settle(6)
        big = win.paned.sashpos(0)
        self.assertGreater(big, small)
        for name, w in (("table", win.table), ("detail", win.detail)):
            with self.subTest(widget=name):
                self.assertEqual(w.winfo_ismapped(), 1)

    def test_the_detail_pane_WRAPS_and_the_table_still_does_NOT(self):
        """Prose wraps; a table that wraps stops being a table.

        Mutation: `wrap=WORD` on both and the columns fold onto the next line,
        which is the silent-clip failure rule 3 exists to refuse arriving as a
        silent fold.
        """
        win = self._open(mapped=True)
        self.assertEqual(str(win.detail.cget("wrap")), "word")
        self.assertEqual(str(win.table.cget("wrap")), "none")

    def test_the_detail_pane_needs_no_horizontal_scroll_at_either_size(self):
        """Measured before: at the 720 px minimum the longest detail line is
        75 characters = 525 px against a 503 px widget, and `xview` read
        (0.0, 0.950) -- the tail of every long line off the right edge with a
        scrollbar as the only route to it.

        Mutation: restore `wrap=NONE` on the detail pane.
        """
        win = self._open(mapped=True)
        for geom in ("980x700", f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}"):
            self._at(win, geom)
            win._select(win._contrib_rows[1][1])
            self._settle(10)
            text = win.detail.get("1.0", "end-1c")
            font = tkfont.Font(root=win, font=ag.ATTRIB_FONT)
            longest = max(font.measure(ln) for ln in text.splitlines())
            with self.subTest(geom=geom):
                self.assertEqual(tuple(win.detail.xview()), (0.0, 1.0),
                                 "the detail pane scrolls horizontally")
                if geom.startswith(str(ag.ATTRIB_MIN_W)):
                    # Precondition at the size where it bit: the content
                    # really is wider than the widget, or "it does not
                    # scroll" is a statement about nothing.
                    self.assertGreater(longest, win.detail.winfo_width())

    def test_a_DRAG_claims_the_split_and_the_next_render_leaves_it_alone(self):
        """Once the reader has moved the divider it is theirs until the window
        closes.

        The gesture is driven through the two handlers rather than by poking
        `_sash_user`, because "was that a drag?" is the thing being tested.
        Mutation: re-derive unconditionally in `_render_impl`.
        """
        win = self._open(mapped=True)
        self._at(win, "980x700")
        win._on_sash_press()
        moved = win.paned.sashpos(0) + 90
        win.paned.sashpos(0, moved)
        self._settle(4)
        win._on_sash_release()
        self.assertTrue(win._sash_user)
        win._apply_sash(32)
        win._render()
        self._settle(8)
        self.assertEqual(win.paned.sashpos(0), moved)

    def test_a_click_that_moves_NOTHING_is_not_a_drag(self):
        """Mutation: set `_sash_user` on every ButtonRelease and a stray click
        anywhere on the divider freezes the split for the session."""
        win = self._open(mapped=True)
        self._at(win, "980x700")
        win._on_sash_press()
        win._on_sash_release()
        self.assertFalse(win._sash_user)

    def test_OUR_OWN_WRITE_between_press_and_release_is_not_a_drag(self):
        """
        A re-derivation landing while a button is held must not become a
        permanent claim on the split.

        MEASURED at 100% / 980x700 before this: `_on_sash_press()` ->
        `_apply_sash(30)` -- which is exactly what a new decomposition does --
        -> `_on_sash_release()` left `_sash_user` True with no pointer
        movement at all, and item 2's content-derived position then stopped
        working for the rest of the session.  `_apply_sash` is reachable while
        a button is down from `_render_impl` (Recompute, a view switch, and
        the units switch's `refresh_attribution_windows(rerender=True)`) and
        from the `after_idle` `<Configure>`.

        The comment on `_on_sash_release` correctly rejects comparing against
        "the last value applied" -- so the fix is a WRITE COUNTER, which is
        orthogonal to that.

        Mutation: drop `self._sash_writes += 1` from `_apply_sash`, or the
        counter test from `_on_sash_release`.
        """
        win = self._open(mapped=True)
        self._at(win, "980x700")
        before = win.paned.sashpos(0)
        win._on_sash_press()
        win._apply_sash(30)
        self._settle(4)
        # Precondition: the sash really did move, or "it was not called a
        # drag" is a statement about nothing.
        self.assertNotEqual(win.paned.sashpos(0), before,
                            "the write did not move the sash on this box; "
                            "the case being ruled out did not arise")
        win._on_sash_release()
        self.assertFalse(win._sash_user)
        # And a real drag STILL claims it -- the counter must not have made
        # every gesture unclaimable.
        win._on_sash_press()
        moved = win.paned.sashpos(0) + 60
        win.paned.sashpos(0, moved)
        self._settle(4)
        win._on_sash_release()
        self.assertTrue(win._sash_user)

    def test_a_RESIZE_is_not_a_drag_either(self):
        """ttk moves the sash itself when the window is resized, so comparing
        against the last value APPLIED would call every resize a gesture.

        Mutation: compare `sashpos` against `_sash_applied` instead of against
        the position at ButtonPress.
        """
        win = self._open(mapped=True)
        self._at(win, "980x700")
        self._at(win, "1300x820")
        self._at(win, "980x700")
        self.assertFalse(win._sash_user)
        line = self._line(win)
        rendered = int(win.table.index("end-1c").split(".")[0])
        self.assertLess(win.table.winfo_height() - rendered * line,
                        (ag.ATTRIB_SASH_SPARE_LINES + 2) * line)
        self.assertEqual(tuple(win.table.yview()), (0.0, 1.0))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheBadgeOffersTheCheck(_WindowCase):
    """Item 3, on the widget."""

    def test_the_off_state_on_screen_names_the_cost_and_the_gesture(self):
        """Mutation: go back to the bare "not checked" caveat."""
        win = self._open()
        text = win.badge_lbl.cget("text")
        self.assertIn("not checked", text)
        self.assertIn("more solves", text)
        self.assertIn("4-port", text)
        self.assertIn(ag.EXPAND_COLLAPSED, text)

    def test_once_checked_it_says_STABLE_or_WHAT_MOVED(self):
        """"checked" on its own is not a result.  Either wording is a real
        answer; neither is the absence of one.

        Mutation: return a bare "checked" and this goes red whichever way the
        fixture falls.
        """
        win = self._open()
        win._on_toggle_stability()
        win._compute_stability()
        self._settle(4)
        text = win.badge_lbl.cget("text")
        self.assertNotIn("not checked", text)
        if "STABLE" in text:
            self.assertIn("nothing changed places", text)
        else:
            self.assertIn("NOT stable", text)
            self.assertIn("→", text)
            self.assertIn(" at ", text)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheGroundModelIsOnTheWindow(_WindowCase):
    """
    Item 4.  Four ground balls at 1 nH each INDEPENDENTLY against the same four
    tied through ONE shared 1 nH moves |M| by 9.60 dB -- larger than the
    6.07 dB dispute this feature exists to settle -- and the window offered no
    way to ask the question.
    """

    SHARED = "shared:L=1n"

    def test_the_control_is_on_screen_at_both_declared_sizes(self):
        """A control that is not on screen is not a control.  It is a row of
        its own precisely so it does not depend on the header's wrap: measured,
        the six header items ask 961 px against a 964 px strip at 980."""
        win = self._open(mapped=True)
        for geom in (ag.ATTRIB_GEOMETRY,
                     f"{ag.ATTRIB_MIN_W}x{ag.ATTRIB_MIN_H}"):
            win.geometry(geom)
            self._settle(12)
            with self.subTest(geom=geom):
                self.assertEqual(win.ground_entry.winfo_ismapped(), 1)
                self.assertEqual(win.ground_entry.winfo_width(),
                                 win.ground_entry.winfo_reqwidth(),
                                 "the ground-model field is clipped")

    def test_switching_the_model_CHANGES_THE_ANSWER(self):
        """The whole reason for the item.  Mutation: accept the field and
        ignore it -- the window then has a control that does nothing, which is
        worse than not having one."""
        win = self._open()
        before = complex(win._res.dec.total_sum)
        win.ground_var.set(self.SHARED)
        win._on_recompute()
        self._settle(4)
        after = complex(win._res.dec.total_sum)
        self.assertNotEqual(win._res.prov.ground_model, "diag")
        rel = abs(after - before) / max(abs(before), 1e-300)
        self.assertGreater(rel, 0.01,
                           f"the shared return moved M by only {rel:.3%}")

    def test_it_does_NOT_recompute_on_the_keystroke(self):
        """Rule 6, and the item says so explicitly: it must go through the same
        [Recompute] path as any other input.

        Mutation: trace the variable and recompute -- which is the auto-refresh
        design rule 6 documents erasing its own table.
        """
        win = self._open()
        before = complex(win._res.dec.total_sum)
        win.ground_var.set(self.SHARED)
        self._settle(6)
        self.assertEqual(complex(win._res.dec.total_sum), before)
        self.assertEqual(win._res.prov.ground_model, ag.GROUND_MODEL_DEFAULT)

    def test_the_reconciliation_is_RE_DERIVED_as_not_comparable(self):
        """CLAUDE.md's rule for a dense `Zt`: what is lost is the second
        OPINION, not the check.  `compute_z_matrix` cannot be handed a mutual
        impedance between ground leads, so `reference_applicable` goes False
        and the verdict says "not comparable" rather than reporting a
        disagreement about a network nobody asked it about.

        Mutation: keep the declared context and decompose the what-if against
        it -- measured on diff_pair_4port.s4p, the residual then read 1.01,
        sailed past RESIDUAL_CATASTROPHIC and emptied the table.
        """
        win = self._open()
        self.assertTrue(win._res.dec.reference_applicable)
        win.ground_var.set(self.SHARED)
        win._on_recompute()
        self._settle(4)
        self.assertFalse(win._res.dec.reference_applicable)
        verdict, ok = ag.reconciliation_verdict(win._res.dec)
        self.assertEqual(verdict, "not comparable")
        self.assertTrue(ok)
        self.assertIn("not comparable", win.recon.cget("text"))
        # The split still stands -- the arithmetic was checked on the declared
        # configuration, which is the check that was always meant.
        self.assertTrue(win._res.dec.terms)

    def test_the_sign_strip_AND_the_export_both_name_the_model(self):
        """The item asks for both, and they answer different questions: the
        strip is what a reader who has not touched the field sees, the export
        is where it cannot clip."""
        win = self._open()
        win.ground_var.set(self.SHARED)
        win._on_recompute()
        self._settle(4)
        self.assertIn("Grounds: " + self.SHARED, win.sign_lbl.cget("text"))
        report = win._report()
        self.assertIn("Ground model: " + self.SHARED, report)
        self.assertIn("9.60 dB", report)

    def test_the_strip_still_names_the_model_the_NUMBERS_came_from(self):
        """The field can be edited without a Recompute, and the strip
        describes what is on screen.

        Mutation: render the strip from `ground_var` and it claims a model the
        numbers were not computed under -- the same class of lie the staleness
        banner exists to stop.
        """
        win = self._open()
        win.ground_var.set(self.SHARED)
        win._render()
        self._settle(4)
        self.assertIn("Grounds: diag (as declared)", win.sign_lbl.cget("text"))

    def test_a_model_it_cannot_read_is_reported_and_nothing_moves(self):
        """A bad value costs its own field, never the window -- and the message
        is the CLI's own, which is what makes it correctable."""
        win = self._open()
        before = complex(win._res.dec.total_sum)
        win.ground_var.set("shared:")
        win._on_recompute()
        self._settle(4)
        self.assertIn("needs an impedance after the colon",
                      win.recon.cget("text"))
        self.assertEqual(complex(win._res.dec.total_sum), before)

    def test_the_choice_round_trips_through_a_session(self):
        """It is on the same footing as `candidates`: a CHOICE the user typed,
        that this tool refuses to guess, and that is worth 9.60 dB."""
        win = self._open()
        win.ground_var.set(self.SHARED)
        win._on_recompute()
        self._settle(4)
        state = ag.attribution_session_state(win)
        self.assertEqual(state["windows"][0]["ground_model"], self.SHARED)
        # The key is written ONLY for a chosen model -- see
        # `attribution_session_state`, which records why (the integration
        # test's pinned key set) and what to do about it.
        win.ground_var.set(ag.GROUND_MODEL_DEFAULT)
        self.assertNotIn("ground_model",
                         ag.attribution_session_state(win)["windows"][0])
        win.ground_var.set(self.SHARED)
        win.destroy()
        self._settle(2)
        ag.apply_attribution_session_state(self.app, state)
        again = ag.open_attribution_window(self.app, self.tc)
        self._settle(6)
        self.assertEqual(again._res.prov.ground_model, self.SHARED)
        self.assertEqual(again.ground_var.get(), self.SHARED)


if __name__ == "__main__":
    unittest.main()
