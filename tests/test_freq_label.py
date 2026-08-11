"""
Frequency-label honesty: a printed marker frequency says where it came from.

THE BUG.  extract_rlc_at_freq and extract_coupling_at_freq both pick their
point with argmin(|freqs - target|) and report nothing about the distance, so
the report printed TWO frequencies and explained neither: the Calculate header
and the run page printed f_rlc_hz (what the user typed) while the Z-matrix line
printed cres.freq_hz (where the numbers came from).  A real user read
"@ 5.6 GHz" and "@ 5.512 GHz" in one report with nothing to reconcile them.

It is the default, not a corner.  On tests/fixtures/diff_pair_4port.s4p
(401 points, 1 MHz .. 10 GHz, step 24.9975 MHz) the default marker of 0.1 GHz
resolves to 0.10099 GHz.  Every default session in this repo snapped by
990 kHz and said nothing -- which is why the first test here is the one that
measures the fixture rather than asserting about it.

What is pinned:

  * THE UNCHANGED CASE IS UNCHANGED.  A marker that IS a data point renders
    byte-for-byte what it rendered before, at every one of the six sites.
    That is not a nicety: tests/fixtures/render_reference.json pins the
    Z-matrix line, and tests elsewhere pin the banner and the CSV header.
  * When it snapped, every site prints the SAME string -- the point the
    numbers came from, then what was asked for and the grid step.  Two sites
    rounding one point differently is the same disagreement in another form,
    so FREQ_WIDE_FMT is checked as a property, not as a constant.
  * The grid step comes FROM THE DATA and a non-uniform sweep gets no step
    rather than a guessed one.
  * LOG_WARN only when the snap exceeds half a grid step, which for any
    monotone axis means the requested frequency is off the end of the sweep.
    An in-band snap is reported without badging; an exact marker says nothing.

Pure functions get pure tests (no Tk at all); the severity routing is measured
off real widgets and skips cleanly with no display.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import pkg_rlc_gui  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    MeasPortRow,
    RLCResult,
    parse_si,
    parse_touchstone,
)
from pkg_rlc_gui import (  # noqa: E402
    FREQ_WIDE_FMT,
    App,
    FileEntry,
    FreqSnap,
    RowSnapshot,
    RunSnapshot,
    TraceConfig,
    _format_results_table,
    _run_marker_text,
    _snapshot_block,
    combine_freq_snaps,
    freq_grid_step,
    marker_freq_text,
    run_freq_snap,
    snap_to_grid,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"


def _ensure_fixtures() -> None:
    if FIXTURE.exists():
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


def _tk_available() -> bool:
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()

# A clean 100 MHz grid, 1 .. 10 GHz.  Small enough to reason about by hand.
LINEAR = np.arange(1, 101, dtype=float) * 1e8


# ============================================================================
# 0 -- the measurement that says the bug is the default case
# ============================================================================

class TestTheDefaultSessionReallySnaps(unittest.TestCase):
    """
    Not an assertion about the code -- an assertion about the FIXTURE the
    Tk tests in this repo all run on, and about the default marker the app
    starts with.  If this ever goes green-by-accident (someone regenerates the
    fixture onto a round grid), the severity tests below stop testing anything
    and this is the test that says so.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_the_default_marker_is_not_a_data_point_on_the_fixture(self):
        freqs = parse_touchstone(FIXTURE).freqs
        requested = parse_si("0.1") * 1e9          # what the entry box holds
        snap = snap_to_grid(freqs, requested)
        self.assertFalse(snap.exact,
                         "the fixture's grid now contains the default marker; "
                         "the snap tests below no longer exercise a snap")
        self.assertAlmostEqual(snap.actual_hz, 100990000.0, places=1)
        self.assertAlmostEqual(abs(snap.delta_hz), 990000.0, places=1)
        # ... and it is a HALF-STEP-OR-LESS snap, so it must not badge.
        self.assertFalse(snap.off_grid)


# ============================================================================
# 1 -- resolving a request against a real axis (pure)
# ============================================================================

class TestSnapToGrid(unittest.TestCase):

    def test_a_request_that_is_a_data_point_is_exact(self):
        snap = snap_to_grid(LINEAR, 5.0e9)
        self.assertTrue(snap.exact)
        self.assertEqual(snap.actual_hz, 5.0e9)
        self.assertEqual(snap.delta_hz, 0.0)

    def test_it_picks_the_same_point_the_extractors_pick(self):
        """
        The whole claim is that this describes what the numbers did, so it has
        to agree with argmin -- the extractors' rule -- at every request, not
        only at the convenient ones.
        """
        for target in (0.0, 1e8, 1.49e9, 1.51e9, 5.6e9, 1e10, 2e10):
            with self.subTest(target=target):
                idx = int(np.argmin(np.abs(LINEAR - target)))
                self.assertEqual(snap_to_grid(LINEAR, target).actual_hz,
                                 LINEAR[idx])

    def test_float_noise_from_unit_scaling_is_not_a_snap(self):
        """
        The tolerance FREQ_EXACT_FRAC exists for, with the real mechanism.

        parse_si is exact -- "5.6" is 5.6e9 to the bit -- so the noise is not
        in the request.  It is in the AXIS: a file written in MHz or kHz
        carries decimal text that the parser multiplies by 1e6 / 1e3, and
        33023.73 * 1e6 is 33023730000.000004 where the same point typed in GHz
        is 33023730000.0.  Without the tolerance every report on every
        MHz-unit file would grow a parenthetical about four microhertz.
        """
        axis = np.array([33023.73 + k * 25.0 for k in range(-3, 4)]) * 1e6
        snap = snap_to_grid(axis, parse_si("33.02373") * 1e9)
        self.assertNotEqual(snap.delta_hz, 0.0,
                            "the round-off this test exists for is gone; the "
                            "tolerance may no longer be load-bearing")
        self.assertLess(abs(snap.delta_hz), 1e-3)
        self.assertTrue(snap.exact, f"delta was {snap.delta_hz} Hz")
        self.assertEqual(marker_freq_text(snap), "33.02 GHz")

    def test_a_request_typed_exactly_is_exact_with_no_tolerance_needed(self):
        self.assertEqual(snap_to_grid(LINEAR, parse_si("5.6") * 1e9).delta_hz,
                         0.0)

    def test_the_local_gap_is_the_WIDER_neighbour(self):
        """
        Non-uniform sweeps are the case this choice is for, and getting it
        wrong is a FALSE ALARM, not a miss: on [1.0, 1.1, 5.0] GHz a request
        for 1.6 GHz is plainly inside the swept band, 0.5 GHz from its nearest
        point -- more than half the 0.1 GHz gap on the left, less than half the
        3.9 GHz gap on the right.  Judged against the narrow neighbour the tool
        would announce that 1.6 GHz is "outside the swept band" of a file that
        sweeps to 5 GHz.
        """
        snap = snap_to_grid(np.array([1.0e9, 1.1e9, 5.0e9]), 1.6e9)
        self.assertEqual(snap.actual_hz, 1.1e9)
        self.assertEqual(snap.local_step_hz, 3.9e9)
        self.assertFalse(snap.off_grid)
        self.assertNotIn("outside", marker_freq_text(snap))

    def test_a_snap_inside_the_band_is_not_off_grid(self):
        snap = snap_to_grid(LINEAR, 5.53e9)         # 30 MHz off a 100 MHz grid
        self.assertFalse(snap.exact)
        self.assertFalse(snap.off_grid)
        self.assertAlmostEqual(snap.actual_hz, 5.5e9)

    def test_a_request_off_the_end_of_the_sweep_is_off_grid(self):
        """Both ends: it is a DISTANCE rule, not a direction rule."""
        for target in (0.01e9, 50e9):
            with self.subTest(target=target):
                self.assertTrue(snap_to_grid(LINEAR, target).off_grid)

    def test_the_boundary_is_half_a_step(self):
        """
        Half a step is exactly the largest distance an in-band request can be
        from its nearest point, so a bigger distance can only mean the request
        is off the end.  Exactly half is left ALONE -- at that distance the
        request is as close to the grid as any in-band request ever gets, and
        a rule that fires there would badge a legitimate midpoint.  Measured:
        50 MHz below a 100 MHz grid is silent, 60 MHz below it is not.
        """
        self.assertFalse(snap_to_grid(LINEAR, LINEAR[0] - 0.50 * 1e8).off_grid)
        self.assertTrue(snap_to_grid(LINEAR, LINEAR[0] - 0.60 * 1e8).off_grid)

    def test_a_one_point_sweep_has_no_step_and_refuses_to_guess(self):
        snap = snap_to_grid(np.array([2.5e9]), 2.5e9)
        self.assertTrue(snap.exact)
        self.assertTrue(math.isnan(snap.step_hz))
        other = snap_to_grid(np.array([2.5e9]), 3.0e9)
        self.assertTrue(other.off_grid, "a one-point sweep cannot answer any "
                                        "other frequency")

    def test_an_empty_or_nan_input_resolves_to_nothing_rather_than_raising(self):
        for snap in (snap_to_grid(np.array([]), 1e9),
                     snap_to_grid(LINEAR, float("nan"))):
            self.assertFalse(snap.resolved)
            self.assertTrue(snap.exact, "an unresolved snap must stay silent")


class TestGridStep(unittest.TestCase):

    def test_a_linear_sweep_reports_its_step(self):
        self.assertAlmostEqual(freq_grid_step(LINEAR), 1e8)

    def test_the_real_fixture_step_comes_from_the_data(self):
        _ensure_fixtures()
        step = freq_grid_step(parse_touchstone(FIXTURE).freqs)
        self.assertAlmostEqual(step, 24997500.0, places=1)

    def test_a_log_sweep_has_no_step(self):
        self.assertTrue(math.isnan(freq_grid_step(np.logspace(6, 10, 200))))

    def test_a_band_densified_round_a_resonance_has_no_step(self):
        f = np.concatenate([np.arange(1, 51) * 1e8,
                            np.arange(500, 510) * 1e7])
        self.assertTrue(math.isnan(freq_grid_step(np.sort(f))))

    def test_decimal_round_off_still_counts_as_uniform(self):
        """A real axis is parsed from decimal text, so the gaps differ in the
        last bit.  FREQ_UNIFORM_TOL exists for that and for nothing else."""
        f = np.array([float(f"{k * 0.1:.17g}") for k in range(1, 200)]) * 1e9
        self.assertFalse(math.isnan(freq_grid_step(f)))

    def test_fewer_than_two_points_has_no_step(self):
        self.assertTrue(math.isnan(freq_grid_step(np.array([1e9]))))


# ============================================================================
# 2 -- the ONE renderer (pure)
# ============================================================================

class TestMarkerFreqTextLeavesTheCommonCaseAlone(unittest.TestCase):
    """
    The rule that lets this ship: when the marker IS a data point, every site
    renders exactly the string it rendered before.  Each case below is the
    literal old expression, evaluated beside the new one.
    """

    def test_a_bare_float_renders_as_the_old_expression_did(self):
        for hz, fmt in ((1e8, "{:.4g}"), (5.6e9, "{:.4g}"),
                        (1e8, "{:.3f}"), (1e8, "{:.6g}")):
            with self.subTest(hz=hz, fmt=fmt):
                self.assertEqual(marker_freq_text(hz, fmt),
                                 f"{fmt.format(hz / 1e9)} GHz")

    def test_an_exact_snap_renders_as_the_old_expression_did(self):
        snap = snap_to_grid(LINEAR, 5.6e9)
        for fmt in ("{:.4g}", "{:.3f}", "{:.6g}"):
            with self.subTest(fmt=fmt):
                self.assertEqual(marker_freq_text(snap, fmt),
                                 f"{fmt.format(5.6e9 / 1e9)} GHz")

    def test_an_unresolved_snap_renders_as_the_old_expression_did(self):
        snap = FreqSnap(requested_hz=5.6e9)
        self.assertEqual(marker_freq_text(snap), "5.6 GHz")

    def test_the_old_calculate_banner_is_reproduced_verbatim(self):
        f_rlc_hz = 5.6e9
        old = "=== Calculate @ {:.4g} GHz ===".format(f_rlc_hz / 1e9)
        new = f"=== Calculate @ {marker_freq_text(snap_to_grid(LINEAR, f_rlc_hz))} ==="
        self.assertEqual(new, old)

    def test_the_old_run_marker_is_reproduced_verbatim(self):
        for hz in (5e9, 1e8, 1e10):
            with self.subTest(hz=hz):
                self.assertEqual(_run_marker_text(snap_to_grid(LINEAR, hz)),
                                 f"@ {hz / 1e9:.3f} GHz")

    def test_no_marker_at_all_still_says_so_rather_than_printing_nan(self):
        self.assertEqual(_run_marker_text(float("nan")), "no marker")
        self.assertEqual(_run_marker_text(None), "no marker")
        self.assertEqual(_run_marker_text(FreqSnap(requested_hz=float("nan"))),
                         "no marker")


class TestMarkerFreqTextWhenItSnapped(unittest.TestCase):

    def setUp(self):
        # 5.53 GHz on a 100 MHz grid -> 5.5 GHz, 30 MHz away, in band.
        self.snap = snap_to_grid(LINEAR, 5.53e9)
        self.text = marker_freq_text(self.snap)

    def test_the_primary_number_is_where_the_values_came_from(self):
        """
        Not what was typed.  Everything printed under this heading was read at
        the actual point, and naming the request there is the original bug.
        """
        self.assertTrue(self.text.startswith("5.5 GHz"), self.text)

    def test_it_names_the_request_and_the_step(self):
        self.assertIn("requested 5.53 GHz", self.text)
        self.assertIn("nearest point", self.text)
        self.assertIn("grid step 100 MHz", self.text)

    def test_the_step_is_the_data_step_not_a_constant(self):
        other = snap_to_grid(np.arange(1, 100) * 2.5e7, 1.01e9)
        self.assertIn("grid step 25 MHz", marker_freq_text(other))

    def test_a_non_uniform_sweep_says_nearest_point_and_no_step(self):
        snap = snap_to_grid(np.logspace(6, 10, 200), 5.6e9)
        text = marker_freq_text(snap)
        self.assertIn("nearest point", text)
        self.assertNotIn("grid step", text)

    def test_an_off_band_request_says_which_way_it_is_wrong(self):
        text = marker_freq_text(snap_to_grid(LINEAR, 50e9))
        self.assertTrue(text.startswith("10 GHz"), text)
        self.assertIn("outside the swept band", text)
        self.assertIn("40 GHz away", text)

    def test_both_numbers_are_rendered_at_the_same_precision(self):
        """
        The property, not the constant.  Two sites rounding one point
        differently is the disagreement this change exists to end, so the
        caller's precision governs the unchanged case and only that: measured,
        a 4-significant-digit banner said "0.101 GHz" over a table saying
        "0.10099 GHz".
        """
        snap = snap_to_grid(LINEAR, 5.53e9)
        rendered = {marker_freq_text(snap, fmt)
                    for fmt in ("{:.4g}", "{:.3f}", "{:.6g}")}
        self.assertEqual(len(rendered), 1, sorted(rendered))
        self.assertIn(FREQ_WIDE_FMT.format(5.5), rendered.pop().split(" ")[0])

    def test_two_frequencies_too_close_to_tell_apart_are_widened(self):
        """
        "0.1 GHz (requested 0.1 GHz)" reads as a bug in the tool.  A snap that
        survives FREQ_EXACT_FRAC but collides at 6 significant digits gets more
        digits rather than a contradiction.
        """
        # 4 kHz off a 100 MHz grid: past FREQ_EXACT_FRAC (100 Hz here), and
        # still "5.6" at both ends when rendered to 6 significant digits.
        snap = FreqSnap(requested_hz=5.6e9 + 4000.0, actual_hz=5.6e9,
                        step_hz=1e8, local_step_hz=1e8)
        self.assertFalse(snap.exact)
        self.assertEqual(FREQ_WIDE_FMT.format(snap.requested_hz / 1e9),
                         FREQ_WIDE_FMT.format(snap.actual_hz / 1e9),
                         "the collision this test exists for did not happen")
        text = marker_freq_text(snap)
        head, _, bracket = text.partition("(")
        self.assertNotEqual(head.split(" GHz")[0].strip(),
                            bracket.split("requested ")[1].split(" GHz")[0])


class TestCombineFreqSnaps(unittest.TestCase):
    """A run is not one frequency when it is not one sweep."""

    def test_nothing_to_combine_is_none(self):
        self.assertIsNone(combine_freq_snaps([]))
        self.assertIsNone(combine_freq_snaps([None]))

    def test_two_files_landing_on_the_same_point_agree(self):
        a = snap_to_grid(LINEAR, 5.0e9)
        b = snap_to_grid(np.arange(1, 21) * 5e8, 5.0e9)
        out = combine_freq_snaps([a, b])
        self.assertTrue(out.agreed)
        self.assertEqual(out.actual_hz, 5.0e9)

    def test_two_files_landing_elsewhere_refuse_to_pick_one(self):
        a = snap_to_grid(LINEAR, 5.53e9)                 # -> 5.5 GHz
        b = snap_to_grid(np.arange(1, 21) * 4e8, 5.53e9)  # -> 5.6 GHz
        out = combine_freq_snaps([a, b])
        self.assertFalse(out.agreed)
        text = marker_freq_text(out)
        self.assertIn("several points", text)
        self.assertIn("requested 5.53 GHz", text)
        # And it must not name either of them as THE answer.
        self.assertNotIn("5.5 GHz;", text)

    def test_an_unresolved_snap_does_not_make_a_run_disagree(self):
        a = snap_to_grid(LINEAR, 5.0e9)
        out = combine_freq_snaps([FreqSnap(requested_hz=5.0e9), a])
        self.assertTrue(out.agreed)
        self.assertEqual(out.actual_hz, 5.0e9)


# ============================================================================
# 3 -- the sites (pure): table header, Z-matrix line, run headline
# ============================================================================

def _row(freq_hz: float, tid: int = 1, file_label: str = "coil.s4p"):
    res = RLCResult(freq_hz=freq_hz, Z=complex(1.5, 1.26), R_ohm=1.5,
                    L_henry=2e-9, C_farad=-1e-12, Q=0.84)
    return RowSnapshot(id=tid, label=f"t{tid}", port_desc="M1: 1 -> GND",
                       enabled=True, color_idx=0, file_label=file_label,
                       res=res)


class TestResultsTableHeader(unittest.TestCase):

    def test_an_exact_marker_leaves_the_table_byte_identical(self):
        rows = [_row(5.0e9)]
        self.assertEqual(_format_results_table(rows, "smart",
                                               snap_to_grid(LINEAR, 5.0e9)),
                         _format_results_table(rows, "smart"))

    def test_no_snap_at_all_leaves_the_table_byte_identical(self):
        """tests/_render_capture.py calls it with two arguments, and
        tests/fixtures/render_reference.json is what that pins."""
        rows = [_row(5.0e9)]
        self.assertEqual(_format_results_table(rows, "smart", None),
                         _format_results_table(rows, "smart"))

    def test_a_snapped_marker_adds_one_line_that_says_where(self):
        rows = [_row(5.5e9)]
        text = _format_results_table(rows, "smart",
                                     snap_to_grid(LINEAR, 5.53e9))
        note = [ln for ln in text.split("\n") if "read at" in ln]
        self.assertEqual(len(note), 1, text)
        self.assertIn("5.5 GHz", note[0])
        self.assertIn("requested 5.53 GHz", note[0])
        # One line, and only one: the table under it must not move.
        self.assertEqual(len(text.split("\n")),
                         len(_format_results_table(rows, "smart").split("\n")) + 1)

    def test_the_actual_comes_from_the_ROWS_not_from_the_snap(self):
        """
        A row Calculate skipped this run (a frozen trace, or "Calculate This
        Trace") carries numbers from an earlier marker.  The note has to
        describe the rows that are actually printed under it.
        """
        rows = [_row(2.0e9)]                     # measured at 2 GHz, long ago
        text = _format_results_table(rows, "smart",
                                     snap_to_grid(LINEAR, 5.53e9))
        note = next(ln for ln in text.split("\n") if "read at" in ln)
        self.assertIn("2 GHz", note)
        self.assertNotIn("5.5 GHz", note)

    def test_rows_at_different_frequencies_are_not_folded_into_one(self):
        rows = [_row(5.5e9, tid=1), _row(2.0e9, tid=2)]
        note = next(ln for ln in
                    _format_results_table(rows, "smart",
                                          snap_to_grid(LINEAR, 5.53e9)
                                          ).split("\n")
                    if "read at" in ln)
        self.assertIn("several points", note)

    def test_the_note_starts_with_the_swatch_padding(self):
        """Every non-data line of the table is padded to the swatch column, or
        _append_swatched's row scan picks it up as a data row."""
        text = _format_results_table([_row(5.5e9)], "smart",
                                     snap_to_grid(LINEAR, 5.53e9))
        note = next(ln for ln in text.split("\n") if "read at" in ln)
        self.assertFalse(note.startswith(pkg_rlc_gui.RESULTS_SWATCH))
        self.assertTrue(note.startswith(pkg_rlc_gui._SWATCH_PAD))


class TestZMatrixLine(unittest.TestCase):
    """The site that was already honest, and now says so out loud."""

    def setUp(self):
        import tests._render_capture as rc            # noqa: WPS433
        case = next(c for c in rc.CASES
                    if c["name"] == "block_three_pairs_smart")
        self.cres = rc.build_cres(case["block"])      # cres.freq_hz == 1e8
        self.tc = TraceConfig(id=4, label="osc", file_label="coil.s4p", mode=6)

    def _line(self, freq):
        block = _snapshot_block(self.tc, "coil.s4p", self.cres, freq=freq)
        return pkg_rlc_gui._format_coupling_block(block, "smart").split("\n")[1]

    def test_with_no_snap_it_is_exactly_the_old_line(self):
        self.assertEqual(
            self._line(None),
            "  Z matrix @ 0.1 GHz   (Ω, Re+jIm; off-diagonal = mutual, "
            "every other port open)")

    def test_an_exact_snap_is_also_exactly_the_old_line(self):
        self.assertEqual(self._line(snap_to_grid(LINEAR, 1e8)),
                         self._line(None))

    def test_a_snapped_marker_names_the_request(self):
        line = self._line(snap_to_grid(LINEAR, 0.088e9))
        self.assertIn("Z matrix @ 0.1 GHz", line)
        self.assertIn("requested 0.088 GHz", line)
        self.assertIn("Re+jIm", line, "the existing parenthetical survived")

    def test_the_matrix_frequency_always_wins_over_the_snap(self):
        """
        cres.freq_hz is where this matrix was read and always was.  A snap
        carrying some other actual must not be able to relabel it -- that is
        precisely the two-numbers-one-screen failure, rebuilt.
        """
        wrong = FreqSnap(requested_hz=0.088e9, actual_hz=7.7e9,
                         step_hz=1e8, local_step_hz=1e8)
        line = self._line(wrong)
        self.assertIn("Z matrix @ 0.1 GHz", line)
        self.assertNotIn("7.7 GHz", line)


class TestRunHeadlineAndRecord(unittest.TestCase):

    def _run(self, **kw):
        base = dict(number=3, when=None, marker_freq_hz=5.53e9)
        base.update(kw)
        return RunSnapshot(**base)

    def test_a_record_with_no_grids_falls_back_to_the_requested_value(self):
        run = self._run()
        self.assertEqual(run_freq_snap(run), 5.53e9)
        self.assertEqual(_run_marker_text(run_freq_snap(run)), "@ 5.530 GHz")

    def test_marker_freq_hz_stays_the_REQUESTED_frequency(self):
        """
        It is the run's identity and what the entry box said.  Where the
        numbers were read is `freqs`, because that is a property of each
        file's sweep and not of the run.
        """
        run = self._run(freqs=(("coil.s4p", snap_to_grid(LINEAR, 5.53e9)),))
        self.assertEqual(run.marker_freq_hz, 5.53e9)
        self.assertEqual(run_freq_snap(run).actual_hz, 5.5e9)

    def test_one_file_gives_the_run_that_file_s_answer(self):
        run = self._run(freqs=(("coil.s4p", snap_to_grid(LINEAR, 5.53e9)),))
        self.assertIn("requested 5.53 GHz", _run_marker_text(run_freq_snap(run)))

    def test_a_run_record_holds_no_arrays(self):
        """The run-snapshot invariant: a record must not grow with the sweep,
        and a FreqSnap is the newest thing on one."""
        snap = snap_to_grid(np.linspace(1e6, 1e10, 5000), 5.53e9)
        for value in vars(snap).values():
            self.assertNotIsInstance(value, np.ndarray)
        self.assertTrue(all(isinstance(v, (float, bool))
                            for v in vars(snap).values()))


# ============================================================================
# 4 -- severity (Tk): a snap reports, an off-band request warns
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSeverityRouting(unittest.TestCase):
    """
    LOG_WARN is measured through the thing it actually drives -- the Log tab's
    unseen count -- with the Log off screen, because a warning written while
    the Log IS on screen is not unseen and would read as no warning at all.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                              label="tank", port_a="1", color_idx=0)
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self._settle()
        # Park the reader anywhere but the Log, so a warning counts as unseen.
        self.scratch = ttk.Frame(self.app.results_nb)
        self.app.results_nb.add(self.scratch, text="scratch")
        self.app.results_nb.select(self.scratch)
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _calc(self, ghz: str) -> int:
        """Calculate at `ghz` and return how many warnings it badged."""
        self.app.results_nb.select(self.scratch)
        self.app._log_unseen = 0
        self.app.rlc_freq_var.set(ghz)
        self.app._on_calculate()
        self._settle()
        self.app.results_nb.select(self.scratch)
        self._settle()
        return self.app._log_unseen

    def test_an_exact_marker_does_not_badge(self):
        # 0.250975 GHz IS a point of this fixture (1 MHz + 10 * 24.9975 MHz).
        self.assertEqual(self._calc("0.250975"), 0)

    def test_an_in_band_snap_does_not_badge_either(self):
        """
        The default marker, which snaps by 990 kHz of a 25 MHz step.  Badging
        it would badge every session this tool has ever run, and
        tests/test_results_notebook.py::test_a_clean_calculate_leaves_the_badge
        _alone is the standing guard on that.
        """
        self.assertEqual(self._calc("0.1"), 0)

    def test_a_request_off_the_end_of_the_sweep_badges_once(self):
        self.assertEqual(self._calc("20"), 1)

    def test_the_off_band_warning_is_the_frequency_line_and_says_why(self):
        self.app.results_text.delete("1.0", self.tk.END)
        self._calc("20")
        head = self.app.results_text.get("1.0", self.tk.END).splitlines()
        banner = next(ln for ln in head if "Calculate @" in ln)
        self.assertIn("outside the swept band", banner)
        self.assertIn("requested 20 GHz", banner)

    def test_just_below_the_band_does_NOT_badge_on_this_fixture(self):
        """
        The no-false-alarm side, and it is a measurement, not a preference.
        This sweep starts at 1 MHz with a 25 MHz step, so EVERY frequency
        below the band is still within half a step of the first point --
        0.0001 GHz is 900 kHz away against a 12.5 MHz half-step.  The answer
        really is the neighbouring data point, so it is reported and not
        badged.  The badge is for a request the file cannot answer at all,
        which on this fixture means above 10 GHz.
        """
        self.assertEqual(self._calc("0.0001"), 0)

    def test_the_severity_follows_the_snap_and_not_the_run_count(self):
        """Same app, three markers, three answers -- so the badge is not just
        counting Calculates."""
        self.assertEqual([self._calc("0.250975"), self._calc("20"),
                          self._calc("0.250975")],
                         [0, 1, 0])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEverySurfaceAgrees(unittest.TestCase):
    """
    The end of the bug, stated directly: the banner, the run page headline,
    the results table and the Z-matrix line print the SAME frequency string.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        import tkinter as tk
        self.tk = tk
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(
            FIX / "coupled_2port_gndref.s2p"))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        # Both report shapes in ONE run, off ONE file: the mode-6 block owns
        # the Z-matrix line and the mode-1 trace owns the results table, and
        # the point of this class is that all of them say the same thing.
        self.tc = TraceConfig(
            id=1, file_label=self.fe.label, mode=6, label="coils",
            mports=[MeasPortRow(name="c1", plus="1"),
                    MeasPortRow(name="c2", plus="2")])
        self.app.traces.append(self.tc)
        self.app.traces.append(
            TraceConfig(id=2, file_label=self.fe.label, mode=1, label="one",
                        port_a="1", color_idx=1))
        self.app._refresh_trace_list()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def test_the_reported_case_no_longer_shows_two_frequencies(self):
        """
        The user's report: "@ 5.6 GHz" in the header and "@ 5.512 GHz" on the
        Z matrix.  Reproduced in miniature -- 0.088 GHz requested against a
        100 MHz grid -- and both must now read the same.
        """
        self.app.rlc_freq_var.set("0.088")
        self.app.results_text.delete("1.0", self.tk.END)
        self.app._on_calculate()
        self._settle()

        log = self.app.results_text.get("1.0", self.tk.END)
        banner = next(ln for ln in log.splitlines() if "Calculate @" in ln)
        zline = next(ln for ln in log.splitlines() if "Z matrix @" in ln)

        stated = banner.split("Calculate @ ")[1].rsplit(" ===", 1)[0]
        self.assertIn(stated, zline,
                      f"banner says {stated!r}, Z matrix says {zline!r}")
        self.assertIn("0.1 GHz", stated)
        self.assertIn("requested 0.088 GHz", stated)

        page = self.app._run_tabs[0].text.get("1.0", self.tk.END)
        self.assertIn(stated, page.splitlines()[0], "the run page disagrees")

    def test_the_table_note_reaches_the_log_AND_the_run_page(self):
        """
        The table is where the numbers are, and it is what gets copied into a
        mail -- so the note has to travel with it, on both surfaces.  The Log
        and the page are built by one function for exactly this reason.
        """
        self.app.rlc_freq_var.set("0.088")
        self.app.results_text.delete("1.0", self.tk.END)
        self.app._on_calculate()
        self._settle()
        for where, body in (
                ("log", self.app.results_text.get("1.0", self.tk.END)),
                ("page", self.app._run_tabs[0].text.get("1.0", self.tk.END))):
            with self.subTest(where=where):
                note = [ln for ln in body.splitlines() if "read at:" in ln]
                self.assertEqual(len(note), 1, body)
                self.assertIn("0.1 GHz", note[0])
                self.assertIn("requested 0.088 GHz", note[0])

    def test_an_on_grid_marker_prints_no_provenance_anywhere(self):
        self.app.rlc_freq_var.set("0.5")
        self.app.results_text.delete("1.0", self.tk.END)
        self.app._on_calculate()
        self._settle()
        log = self.app.results_text.get("1.0", self.tk.END)
        self.assertIn("=== Calculate @ 0.5 GHz ===", log)
        self.assertNotIn("requested", log)
        self.assertNotIn("read at", log)
        self.assertIn("Z matrix @ 0.5 GHz   (Ω", log)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTwoFilesWithDifferentSweeps(unittest.TestCase):
    """
    Multi-file comparison is a feature, and two files rarely carry the same
    sweep -- so one run can genuinely have two answers, and the report has to
    give both rather than pick.  coupled_2port_gndref.s2p is a 100 MHz grid
    from 0.1 GHz; diff_pair_4port.s4p is a 24.9975 MHz grid from 1 MHz.  At
    the default 0.1 GHz marker the first is exact and the second snaps.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        import tkinter as tk
        self.tk = tk
        self.app = App()
        self.app.withdraw()
        for name in ("coupled_2port_gndref.s2p", "diff_pair_4port.s4p"):
            self.app.files.append(FileEntry(parse_touchstone(FIX / name)))
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        for i, fe in enumerate(self.app.files):
            self.app.traces.append(
                TraceConfig(id=i + 1, file_label=fe.label, mode=1,
                            label=f"t{i + 1}", port_a="1", color_idx=i))
        self.app._refresh_trace_list()
        self._settle()
        self.app.results_text.delete("1.0", self.tk.END)
        self.app._on_calculate()
        self._settle()
        self.log = self.app.results_text.get("1.0", self.tk.END)

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def test_the_precondition_the_two_sweeps_really_do_disagree(self):
        """Without this the whole class passes on a run that agreed."""
        run = self.app._last_run
        self.assertEqual(len(run.freqs), 2)
        self.assertFalse(run_freq_snap(run).agreed)

    def test_no_single_frequency_is_claimed(self):
        banner = next(ln for ln in self.log.splitlines()
                      if "Calculate @" in ln)
        self.assertIn("several points", banner)
        self.assertIn("requested 0.1 GHz", banner)

    def test_each_file_gets_a_line_naming_its_own_point(self):
        lines = {ln.split(":")[0].strip(): ln
                 for ln in self.log.splitlines() if "read at " in ln}
        self.assertIn("coupled_2port_gndref.s2p", lines)
        self.assertIn("diff_pair_4port.s4p", lines)
        self.assertIn("0.1 GHz", lines["coupled_2port_gndref.s2p"])
        self.assertIn("0.10099 GHz", lines["diff_pair_4port.s4p"])
        self.assertIn("requested 0.1 GHz", lines["diff_pair_4port.s4p"])

    def test_the_per_file_lines_are_on_the_run_page_too(self):
        page = self.app._run_tabs[0].text.get("1.0", self.tk.END)
        self.assertEqual(len([ln for ln in page.splitlines()
                              if "read at " in ln]), 2, page)

    def test_the_csv_heads_each_trace_with_ITS_file_s_point(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "out.csv"
        real = pkg_rlc_gui.filedialog.asksaveasfilename
        pkg_rlc_gui.filedialog.asksaveasfilename = lambda **kw: str(path)
        try:
            self.app._on_export_csv()
        finally:
            pkg_rlc_gui.filedialog.asksaveasfilename = real
        text = path.read_text(encoding="utf-8")
        # The exact file gets no Marker line; the snapped one gets exactly one.
        markers = [ln for ln in text.splitlines() if ln.startswith("# Marker:")]
        self.assertEqual(len(markers), 1, text)
        self.assertIn("0.10099 GHz", markers[0])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestCsvHeader(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        import tkinter as tk
        self.tk = tk
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.app.traces.append(TraceConfig(id=1, file_label=self.fe.label,
                                           mode=1, label="tank", port_a="1"))
        self.app._refresh_trace_list()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _export(self, ghz: str) -> str:
        import tempfile
        self.app.rlc_freq_var.set(ghz)
        self.app._on_calculate()
        self._settle()
        path = Path(tempfile.mkdtemp()) / "out.csv"
        real = pkg_rlc_gui.filedialog.asksaveasfilename
        pkg_rlc_gui.filedialog.asksaveasfilename = lambda **kw: str(path)
        try:
            self.app._on_export_csv()
        finally:
            pkg_rlc_gui.filedialog.asksaveasfilename = real
        self._settle()
        return path.read_text(encoding="utf-8")

    def test_an_exact_marker_writes_no_marker_line(self):
        text = self._export("0.250975")
        self.assertIn("# Run: #1 @ 0.251 GHz,", text)
        self.assertNotIn("# Marker:", text)

    def test_a_snapped_marker_gets_its_own_key_line(self):
        """
        The Run line keeps naming the REQUESTED marker -- it is the run's
        identity and the rows below it are the whole sweep, so nothing in the
        file was snapped -- and where the results pane read its numbers goes
        on its own key, not into a parenthetical a script has to survive.
        """
        text = self._export("0.1")
        self.assertIn("# Run: #1 @ 0.100 GHz,", text)
        marker = next(ln for ln in text.splitlines()
                      if ln.startswith("# Marker:"))
        self.assertIn("0.10099 GHz", marker)
        self.assertIn("requested 0.1 GHz", marker)
        self.assertIn("grid step 25 MHz", marker)


if __name__ == "__main__":
    unittest.main()
