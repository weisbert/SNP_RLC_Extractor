"""
Compare files, the pure half: `pkg_rlc.physics.similarity` (the maths) and
`pkg_rlc.present.compare_report` (one reference against N files, the verdict
strip and the reading) -- no tkinter anywhere.

Pinned:

  * THE MATHS: identical data reads -300 dB and 0 %; a known 1 % inductance
    change reads +1 %; the band is the OVERLAP and nothing is extrapolated;
    the grid is the COARSER file's; a Z0 mismatch is renormalised away, a
    port-count mismatch is refused; a zero of the reference is excluded and
    COUNTED.  (Ported from tests/test_compare_files.py.)
  * NOT JUDGED Q / R (`similarity.RE_JUDGE_FRAC`): where the reference is
    almost lossless, Q and R are not computed -- a lossless pair no longer
    reads the +-100 %-and-beyond square wave the owner saw -- and a REAL Q
    difference is still caught.
  * ONE REFERENCE, N FILES: each pair on its own band and grid, one strip
    line per compared file, in order.
  * NO DEFAULT SETUP: an empty setup is "No setup defined", S only.
  * THE FORMATTING FIXES: 99.5-100 % is never '1e+02' (text and S table);
    from +100 % a relative difference is a multiple; table columns are as
    wide as their content.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from pkg_rlc.physics import similarity as sim  # noqa: E402
from pkg_rlc.physics.core import (ConnectionRow, MeasPortRow,  # noqa: E402
                                  parse_touchstone, y_to_s)
from pkg_rlc.model.trace import FileEntry, TraceConfig  # noqa: E402
from pkg_rlc.present import compare_report as cr  # noqa: E402
from generate_test_snp import (fixture_diff_pair_4port,  # noqa: E402
                               write_touchstone)

REPO = Path(__file__).resolve().parent.parent


def _z(f, L=1e-9, R=0.8, C=20e-15):
    w = 2 * np.pi * f
    return 1 / (1 / (R + 1j * w * L) + 1j * w * C)


def _s(z, z0=50.0):
    return ((z - z0) / (z + z0)).reshape(-1, 1, 1)


F30 = np.linspace(1e8, 30e9, 301)
F80 = np.linspace(1e8, 80e9, 401)


class _Files:
    """Touchstone files written to a temp dir and loaded as FileEntry --
    the real parser, the real precision, what the workspace hands over."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def fe(self, name, f, S, digits=9):
        p = self.tmp / name
        write_touchstone(p, f, S, digits=digits)
        return FileEntry(parse_touchstone(p))


P1 = [MeasPortRow("P1", "1", "")]


# ============================================================================
# The maths (ported from test_compare_files.py)
# ============================================================================


class TestCommonAxis(unittest.TestCase):

    def test_the_band_is_the_overlap(self):
        ax = sim.common_axis(F30, F80)
        self.assertEqual((ax.lo, ax.hi), (1e8, 30e9))
        self.assertLessEqual(ax.freqs.max(), 30e9)
        self.assertGreaterEqual(ax.freqs.min(), 1e8)

    def test_the_grid_is_the_coarser_files(self):
        ax = sim.common_axis(F30, F80)
        self.assertEqual((ax.grid_of, ax.interpolated), ("B", "A"))
        np.testing.assert_array_equal(ax.freqs, F80[F80 <= 30e9])

    def test_identical_grids_are_not_interpolated(self):
        ax = sim.common_axis(F30, F30 * (1 + 1e-13))
        self.assertEqual((ax.grid_of, ax.interpolated), ("both", ""))

    def test_no_overlap_is_refused(self):
        with self.assertRaises(sim.SimilarityError):
            sim.common_axis(np.linspace(1e8, 1e9, 5), np.linspace(2e9, 3e9, 5))


class TestCompareS(unittest.TestCase):

    def test_identical_data_reads_the_floor(self):
        r = sim.compare_s(F30, _s(_z(F30)), 50, F30, _s(_z(F30)), 50)
        self.assertLessEqual(r.worst_db, -290)

    def test_the_same_network_under_another_z0_is_the_same(self):
        r = sim.compare_s(F30, _s(_z(F30), 50), 50,
                          F30, _s(_z(F30), 75), 75)
        self.assertLess(r.worst_db, -200)
        self.assertTrue(any("renormalised" in n for n in r.notes))

    def test_a_different_port_count_is_refused(self):
        s2 = np.zeros((len(F30), 2, 2), dtype=complex)
        with self.assertRaises(sim.SimilarityError):
            sim.compare_s(F30, _s(_z(F30)), 50, F30, s2, 50)

    def test_the_worst_entry_and_frequency_are_named(self):
        sa = np.zeros((len(F30), 2, 2), dtype=complex)
        sb = sa.copy()
        sb[123, 1, 0] = 0.01                        # -40 dB, S21, one point
        r = sim.compare_s(F30, sa, 50, F30, sb, 50)
        self.assertAlmostEqual(r.worst_db, -40.0, places=6)
        self.assertEqual(r.worst_entry, (2, 1))
        self.assertEqual(r.worst_f, F30[123])


class TestCompareZ(unittest.TestCase):

    def test_identical_is_zero(self):
        c = sim.compare_z(F30, _z(F30), F30, _z(F30))
        self.assertEqual((c.l.value, c.q.value, c.r.value), (0.0, 0.0, 0.0))

    def test_a_one_percent_L_change_reads_one_percent_where_C_is_negligible(self):
        f = np.linspace(1e8, 1e9, 51)
        c = sim.compare_z(f, _z(f, C=0.0), f, _z(f, L=1.01e-9, C=0.0))
        np.testing.assert_allclose(c.dl_pct, 1.0, rtol=1e-9)
        self.assertAlmostEqual(c.l.value, 1.0, places=9)
        np.testing.assert_allclose(c.dq_pct, 1.0, rtol=1e-9)

    def test_the_sign_says_which_way(self):
        f = np.linspace(1e8, 1e9, 51)
        c = sim.compare_z(f, _z(f, C=0.0), f, _z(f, L=0.98e-9, C=0.0))
        self.assertLess(c.l.value, 0)

    def test_a_zero_crossing_of_the_reference_is_excluded_and_counted(self):
        L, R, C = 1e-9, 0.8, 20e-15
        f0 = np.sqrt(1 / (L * C) - (R / L) ** 2) / (2 * np.pi)   # Im(Z) = 0
        f = np.sort(np.append(np.linspace(1e9, 60e9, 591), f0))
        c = sim.compare_z(f, _z(f), f, _z(f, C=20.2e-15))
        self.assertGreater(c.l.excluded, 0)
        self.assertTrue(np.isnan(c.dl_pct).any())
        self.assertTrue(np.isfinite(c.l.value))

    def test_ordinary_R_is_not_mistaken_for_zero(self):
        c = sim.compare_z(F80, _z(F80), F80, _z(F80, R=0.81))
        self.assertGreater(np.nanmax(_z(F80).real), 1000 * 0.8)
        self.assertEqual(c.r.excluded, 0)


# ============================================================================
# NOT JUDGED Q / R -- the square wave
# ============================================================================


class TestLosslessQIsNotJudged(_Files, unittest.TestCase):
    """The owner's Q plot: a +-100 %-and-beyond square wave.  A lossless
    file's Re Z is its rounding; Q = Im/Re is that rounding inverted."""

    def _lossless_pair(self, digits=5):
        f = np.linspace(1e6, 20e9, 401)
        a = self.fe("a.s1p", f, _s(1j * 2 * np.pi * f * 1e-9), digits)
        b = self.fe("b.s1p", f, _s(1j * 2 * np.pi * f * 1.004e-9), digits)
        return a, b

    def test_a_lossless_pair_is_no_longer_a_square_wave(self):
        a, b = self._lossless_pair()
        res = cr.compare_pair(a, b, P1, [])
        z = res.ports[0].z
        # Every point: nothing of Q or R is computed, nothing flips sign.
        self.assertFalse(np.isfinite(z.dq_pct).any())
        self.assertFalse(np.isfinite(z.dr_pct).any())
        self.assertEqual(z.n_not_judged, len(z.axis.freqs))
        # L does not care about Re Z and is still judged: +0.4 %.
        self.assertAlmostEqual(z.l.value, 0.4, delta=0.01)
        strip = cr.verdict_strip_lines([res], cr.Limits(1, 1, 5))
        self.assertTrue(strip[0].startswith(cr.MARK_SAME), strip)

    def test_the_count_is_its_own_not_the_median_rules(self):
        a, b = self._lossless_pair()
        z = cr.compare_pair(a, b, P1, []).ports[0].z
        self.assertEqual(z.q.excluded, 0)
        self.assertEqual(z.r.excluded, 0)

    def test_details_say_how_many_and_why(self):
        a, b = self._lossless_pair()
        res = cr.compare_pair(a, b, P1, [], marker_hz=5e9)
        text = "\n".join(cr.compare_summary_lines(res, 1, 1, 5))
        self.assertIn("401 of 401 points: Q and R not judged", text)
        self.assertIn("A is almost lossless", text)
        self.assertIn("Q above about 1000", text)
        self.assertIn("Q: not judged (A is lossless)", text)
        self.assertIn("Q and R are not judged anywhere", text)

    def test_a_real_Q_difference_is_still_caught(self):
        # Q = 100 at 5 GHz, B has 5 % more R: a real loss difference.
        f = np.linspace(1e8, 10e9, 100)
        R = 2 * np.pi * 5e9 * 1e-9 / 100
        a = self.fe("qa.s1p", f, _s(R + 1j * 2 * np.pi * f * 1e-9))
        b = self.fe("qb.s1p", f, _s(1.05 * R + 1j * 2 * np.pi * f * 1e-9))
        res = cr.compare_pair(a, b, P1, [])
        z = res.ports[0].z
        self.assertEqual(z.n_not_judged, 0)
        self.assertAlmostEqual(z.q.value, -100 * (1 - 1 / 1.05), delta=0.01)
        self.assertAlmostEqual(z.r.value, 5.0, delta=0.01)
        strip = cr.verdict_strip_lines([res], cr.Limits(1, 1, 1))
        self.assertTrue(strip[0].startswith(cr.MARK_DIFFERENT), strip)
        self.assertIn("its quality factor Q is 4.76 % lower than the reference",
                      strip[0])

    def test_a_lossy_B_against_a_lossless_A_is_not_hidden(self):
        # Stage-3 review: A lossless (6 digits) against B at Q = 100 read ✓,
        # and ✗ with the two swapped.  Where A is lossless and B clearly is
        # not, that IS the difference, and the strip says so.
        f = np.linspace(1e8, 10e9, 100)
        R = 2 * np.pi * 5e9 * 1e-9 / 100
        a = self.fe("la.s1p", f, _s(1j * 2 * np.pi * f * 1e-9), 6)
        b = self.fe("lb.s1p", f, _s(R + 1j * 2 * np.pi * f * 1e-9), 6)
        res = cr.compare_pair(a, b, P1, [])
        z = res.ports[0].z
        self.assertGreater(z.n_not_judged, 0)
        self.assertGreater(z.n_b_lossy, 0)
        # S limit 2 %: the raw files differ by 1.25 %, so the loss is the
        # one thing over and leads the line.
        strip = cr.verdict_strip_lines([res], cr.Limits(2, 1, 5))
        self.assertTrue(strip[0].startswith(cr.MARK_DIFFERENT), strip)
        self.assertIn("has loss where the reference is lossless", strip[0])

    def test_a_same_verdict_says_q_and_r_were_not_judged(self):
        a, b = self._lossless_pair()
        strip = cr.verdict_strip_lines([cr.compare_pair(a, b, P1, [])],
                                       cr.Limits(1, 1, 5))
        self.assertIn("Q and R not judged at 401 points", strip[0])

    def test_it_is_the_reference_that_decides(self):
        # A lossy, B lossless: B lost all its loss -- a real difference.
        f = np.linspace(1e8, 10e9, 100)
        za = 0.5 + 1j * 2 * np.pi * f * 1e-9
        zb = 1e-5 + 1j * 2 * np.pi * f * 1e-9
        c = sim.compare_z(f, za, f, zb)
        self.assertEqual(c.n_not_judged, 0)
        self.assertTrue(np.isfinite(c.q.value))

    def test_the_tests_synthetic_rlc_is_untouched(self):
        c = sim.compare_z(F30, _z(F30), F80, _z(F80, L=1.004e-9))
        self.assertEqual(c.n_not_judged, 0)

    def test_the_lossless_four_port_loop(self):
        """diff_pair_4port has no R at all: the loop (P1 +1 -2, short 3,4)
        of it and of a +0.4 % L_self on another grid."""
        fa = fixture_diff_pair_4port()
        fb = fixture_diff_pair_4port(L_self=5.02e-9, f_stop=20e9, n_pts=301)
        a = self.fe("dp_a.s4p", fa["freqs"], y_to_s(fa["Y"], 50.0), 5)
        b = self.fe("dp_b.s4p", fb["freqs"], y_to_s(fb["Y"], 50.0), 5)
        res = cr.compare_pair(a, b, [MeasPortRow("L", "1", "2")],
                              [ConnectionRow(kind="short", ports="3,4")])
        z = res.ports[0].z
        self.assertFalse(np.isfinite(z.dq_pct).any())
        self.assertLess(abs(z.l.value), 1.0)

    def test_the_constant_is_the_measured_one(self):
        self.assertEqual(sim.RE_JUDGE_FRAC, 1e-3)

    def test_not_judged_spans(self):
        f = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        m = np.array([True, True, False, False, True, False])
        self.assertEqual(cr.not_judged_spans(f, m),
                         [(1.0, 2.5), (4.5, 5.5)])
        self.assertEqual(cr.not_judged_spans(f, None), [])
        self.assertEqual(cr.not_judged_spans(f, np.ones(6, bool)),
                         [(1.0, 6.0)])


# ============================================================================
# One reference, N files
# ============================================================================


class TestOneReferenceNFiles(_Files, unittest.TestCase):

    def setUp(self):
        super().setUp()
        f50 = np.linspace(1e8, 50e9, 351)
        self.a = self.fe("ind_30G.s1p", F30, _s(_z(F30)))
        self.b = self.fe("ind_50G.s1p", f50, _s(_z(f50, L=1.002e-9)))
        self.c = self.fe("ind_80G.s1p", F80, _s(_z(F80, L=1.05e-9)))

    def test_each_pair_is_computed_on_its_own(self):
        out = cr.compare_against(self.a, [self.b, self.c], P1, [])
        self.assertEqual([r.label_b for r in out],
                         ["ind_50G.s1p", "ind_80G.s1p"])
        self.assertTrue(all(r.label_a == "ind_30G.s1p" for r in out))
        # Own grid per pair: the coarser of each two inside 0.1-30 GHz.
        self.assertNotEqual(len(out[0].s.axis.freqs),
                            len(out[1].s.axis.freqs))
        self.assertAlmostEqual(float(out[0].ports[0].z.dl_pct[0]), 0.2,
                               delta=0.01)
        self.assertAlmostEqual(float(out[1].ports[0].z.dl_pct[0]), 5.0,
                               delta=0.05)

    def test_one_strip_line_per_compared_file_in_order(self):
        out = cr.compare_against(self.a, [self.b, self.c], P1, [])
        lines = cr.verdict_strip_lines(out, cr.Limits(10, 1, 50))
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(f"{cr.MARK_SAME} ind_50G.s1p: "))
        self.assertTrue(lines[1].startswith(
            f"{cr.MARK_DIFFERENT} ind_80G.s1p: "))
        self.assertIn("its inductance L is ", lines[1])
        self.assertIn("higher than the reference", lines[1])

    def test_a_tuple_of_limits_is_accepted(self):
        out = cr.compare_against(self.a, [self.b], P1, [])
        self.assertEqual(cr.verdict_strip_lines(out, (5, 1, 50)),
                         cr.verdict_strip_lines(out, cr.Limits(5, 1, 50)))

    def test_limits_rejudge_the_same_results(self):
        out = cr.compare_against(self.a, [self.c], P1, [])
        self.assertTrue(cr.verdict_strip_lines(out, (100, 100, 100))[0]
                        .startswith(cr.MARK_SAME))
        self.assertTrue(cr.verdict_strip_lines(out, (100, 1, 100))[0]
                        .startswith(cr.MARK_DIFFERENT))

    def test_stale_is_said_first(self):
        out = cr.compare_against(self.a, [self.b], P1, [])
        lines = cr.verdict_strip_lines(out, cr.Limits(), stale=True)
        self.assertIn("Out of date", lines[0])
        self.assertEqual(len(lines), 2)

    def test_nothing_yet(self):
        self.assertIn("Nothing compared yet",
                      cr.verdict_strip_lines([], cr.Limits())[0])

    def test_the_reference_defaults_to_the_lowest_top_frequency(self):
        self.assertIs(cr.default_reference([self.c, self.b, self.a]), self.a)
        self.assertIsNone(cr.default_reference([]))

    def test_a_file_that_cannot_be_compared_says_why(self):
        far = np.linspace(100e9, 110e9, 11)
        d = self.fe("far.s1p", far, _s(_z(far)))
        line = cr.verdict_strip_lines(cr.compare_against(self.a, [d], P1, []),
                                      cr.Limits())[0]
        self.assertTrue(line.startswith(f"{cr.MARK_NOT_COMPARED} far.s1p: "))
        self.assertIn("do not overlap", line)


# ============================================================================
# No default setup
# ============================================================================


class TestNoSetupDefined(_Files, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.a = self.fe("a.s1p", F30, _s(_z(F30)))
        self.b = self.fe("b.s1p", F30, _s(_z(F30, L=1.3e-9)))

    def test_an_empty_setup_compares_s_only(self):
        for mports, conn in (([], []), ([MeasPortRow()], []),
                             ([], [ConnectionRow(kind="ground", ports="1")])):
            res = cr.compare_pair(self.a, self.b, mports, conn)
            self.assertFalse(res.has_setup)
            self.assertEqual(res.ports, [])
            self.assertTrue(res.z_why.startswith(cr.NO_SETUP), res.z_why)
            self.assertIsNotNone(res.s)

    def test_it_is_said_in_the_reading_and_the_strip(self):
        res = cr.compare_pair(self.a, self.b, [], [])
        text = "\n".join(cr.compare_summary_lines(res, 100, 1, 5))
        self.assertIn("No setup defined: only the raw S-parameters are judged",
                      text)
        self.assertIn("2. THE INDUCTOR -- No setup defined", text)
        self.assertNotIn("port 1 to ground", text)
        # A 30 % L change, and yet only S is judged -- nobody chose a setup.
        line = cr.verdict_strip_lines([res], (100, 1, 5))[0]
        self.assertTrue(line.startswith(cr.MARK_SAME), line)
        self.assertIn("no setup defined", line)

    def test_a_defined_setup_is_solved_on_both_files(self):
        res = cr.compare_pair(self.a, self.b, P1, [])
        self.assertTrue(res.has_setup)
        self.assertEqual(len(res.ports), 1)
        self.assertAlmostEqual(float(res.ports[0].z.dl_pct[0]), 30.0,
                               delta=0.1)
        self.assertIn("P1 (+1)", res.setup)

    def test_a_refused_setup_gives_its_reason_and_keeps_s(self):
        res = cr.compare_pair(self.a, self.b, [MeasPortRow("P1", "3", "")], [])
        self.assertEqual(res.ports, [])
        self.assertIn("Port 3", res.z_why)
        self.assertIn("a.s1p", res.z_why)
        self.assertIsNotNone(res.s)
        line = cr.verdict_strip_lines([res], (100, 1, 5))[0]
        self.assertIn("the setup could not be solved", line)
        text = "\n".join(cr.compare_summary_lines(res, 100, 1, 5))
        self.assertIn("L, Q and R were not compared", text)

    def test_a_probe_rule_error_is_the_reason(self):
        # A probe port that is also grounded: the one row model refuses it.
        res = cr.compare_pair(self.a, self.b, P1,
                              [ConnectionRow(kind="ground", ports="1")])
        self.assertEqual(res.ports, [])
        self.assertTrue(res.z_why)


class TestCopySetupFromTrace(unittest.TestCase):

    def test_a_copy_is_not_linked_to_the_trace(self):
        tc = TraceConfig(id=3, label="loop", mode=5, table_version=1,
                         mports=[MeasPortRow("L", "1", "2")],
                         conn_rows=[ConnectionRow(kind="short", ports="3,4")])
        mports, conn, note = cr.copy_trace_setup(tc)
        self.assertEqual(note, "")
        mports[0].plus = "9"
        conn[0].ports = "7,8"
        self.assertEqual(tc.mports[0].plus, "1")
        self.assertEqual(tc.conn_rows[0].ports, "3,4")

    def test_composed_traces_are_not_offered(self):
        one = TraceConfig(id=1, label="one", file_label="a.s4p")
        two = TraceConfig(id=2, label="two", file_label="a.s4p",
                          file_labels=["a.s4p", "b.s4p"])
        self.assertEqual([t for _x, t in cr.trace_setup_choices([one, two])],
                         [one])


# ============================================================================
# The text (ported from test_compare_files.py, plus the formatting fixes)
# ============================================================================


def _result(dl_worst=0.4, marker_hz=float("nan")):
    ax = sim.common_axis(F30, F30)
    s = sim.SCompare(axis=ax, err_db=np.full(len(F30), -52.0), worst_db=-52.0,
                     worst_f=27e9, worst_entry=(1, 1))
    n = len(F30)
    dl = np.zeros(n)
    dl[-10] = dl_worst
    z = sim.ZCompare(axis=ax, dl_pct=dl, dq_pct=np.full(n, -3.0),
                     dr_pct=np.full(n, 2.0),
                     l=sim.Worst(dl_worst, F30[-10], 0),
                     q=sim.Worst(-3.0, 1e9, 0), r=sim.Worst(2.0, 1e9, 0))
    return cr.CompareResult("ind_30G/L.s1p", "ind_80G/L.s1p", "the setup",
                            s=s, ports=[cr.PortCompare("Z", z)],
                            marker_hz=marker_hz)


def _text(res, s=1.0, l=1.0, q=5.0):
    return "\n".join(cr.compare_summary_lines(res, s, l, q))


def _fifteen_port():
    """The owner's case: 15 ports, S(14,15) / S(15,14) just over 1 %."""
    f = np.linspace(0, 30e9, 31)
    rng = np.random.default_rng(0)
    sa = (rng.normal(size=(15, 15)) * 0.3 + 0j)[None] * np.ones((31, 1, 1))
    sb = sa.copy()
    sb[1, 13, 14] += 0.0106                     # 1.06 %
    sb[1, 14, 13] += 0.0105
    sb[:, 2, 3] += 0.001                        # 0.1 %
    return sim.compare_s(f, sa, 50, f, sb, 50)


class TestSummary(unittest.TestCase):
    """The reading is for someone DECIDING -- the owner, on the first
    version: "根本看不懂".  So the answer comes first, in words."""

    def test_the_answer_comes_first(self):
        lines = cr.compare_summary_lines(_result(), 1, 1, 5)
        self.assertEqual(lines[0], "IN SHORT")
        self.assertIn("THE SAME within your limits", lines[1])
        heads = [ln for ln in lines if ln and not ln.startswith(" ")]
        self.assertEqual(heads[:3], ["IN SHORT", "WHAT TO DO NEXT",
                                     "WHAT WAS COMPARED"])

    def test_no_dB_and_no_scientific_notation_anywhere(self):
        res = _result(dl_worst=280.0)
        res.s = _fifteen_port()
        text = _text(res)
        self.assertNotIn(" dB", text)
        self.assertNotRegex(text, r"\de[+-]\d")

    def test_what_is_over_is_said_in_words_with_the_worst_place(self):
        text = _text(_result(0.4), l=0.2)
        self.assertIn("NOT THE SAME", text)
        self.assertIn("inductance L: B is 0.4 % higher than A", text)
        self.assertIn("Within your limits: the raw S-parameters, Q.", text)

    def test_the_s_limit_is_a_percentage(self):
        self.assertIn("Your limit is 1 %  ->  within it.", _text(_result(), s=1.0))
        self.assertIn("the raw S-parameters differ by up to 0.251 %",
                      _text(_result(), s=0.1))

    def test_the_marker_frequency_is_read_out(self):
        text = _text(_result(marker_hz=1e9))
        self.assertIn("At the marker frequency", text)
        self.assertIn("Q: B is 3 % lower than A", text)
        # The marker is the workspace's own now, not the main window's.
        self.assertNotIn("main window", text)
        text = _text(_result(marker_hz=90e9))
        self.assertIn("outside the compared range", text)

    def test_a_two_digit_port_is_never_run_together(self):
        res = _result()
        res.s = _fifteen_port()
        text = _text(res)
        self.assertIn("S(14,15), between port 14 and port 15", text)
        self.assertNotIn("S1415", text)
        self.assertIn("1.06 %", text)

    def test_every_port_pair_is_in_the_table(self):
        lines = cr.s_matrix_lines(_fifteen_port(), 1.0)
        self.assertTrue(lines[0].startswith("ALL PORT PAIRS"))
        rows = lines[3:]
        self.assertEqual(len(rows), 15)
        self.assertIn("1.06*", rows[13])
        self.assertIn("0.1 ", rows[2])
        self.assertEqual(rows[0].split()[1:], ["."] * 15)

    def test_identical_pairs_are_not_ranked(self):
        res = _result()
        res.s = _fifteen_port()
        lines = cr.compare_summary_lines(res, 1, 1, 5)
        k = next(i for i, ln in enumerate(lines) if "The largest:" in ln)
        self.assertEqual([ln.split()[0] for ln in lines[k + 1:k + 4]],
                         ["S(14,15)", "S(15,14)", "S(3,4)"])
        self.assertIn("2 of the 225 port pairs", lines[k])

    def test_a_negative_percentage_is_judged_by_magnitude(self):
        self.assertEqual(cr.verdict(-3.0, 5.0, db=False), cr.SAME)
        self.assertEqual(cr.verdict(-6.0, 5.0, db=False), cr.DIFFERENT)
        self.assertEqual(cr.verdict(float("nan"), 5.0, db=False), "")

    def test_nothing_compared_says_so(self):
        res = cr.CompareResult("a", "b", "x", s_why="port counts differ",
                               z_why="y")
        text = _text(res)
        self.assertIn("Nothing could be compared", text)
        self.assertIn("Not compared: port counts differ", text)

    def test_the_resonance_is_not_what_the_verdict_reads(self):
        f = np.linspace(1e8, 30e9, 300)
        c = sim.compare_z(f, _z(f, C=200e-15), f, _z(f, C=200.4e-15))
        self.assertTrue(np.isfinite(c.srf_a))       # 11.25 GHz, in band
        res = cr.CompareResult("A", "B", "x", ports=[cr.PortCompare("Z", c)])
        text = _text(res, l=1.0, q=5.0)
        self.assertGreater(abs(c.l.value), 5)
        self.assertIn("THE SAME within your limits", text)
        self.assertIn("judged below", text)
        self.assertIn("not judged", text)
        self.assertIn("The self-resonance moved from", text)


class TestFormattingFixes(unittest.TestCase):

    def test_99_5_to_100_is_never_1e_plus_02(self):
        for p in (99.5, 99.7, 99.99, 100.0):
            self.assertNotIn("e", cr._num(p), p)
        self.assertEqual(cr._num(99.7), "100")
        self.assertEqual(cr._num(9.7), "9.7")
        self.assertEqual(cr._num(0.12), "0.12")
        self.assertEqual(cr._num(0.0004), "<0.001")
        self.assertEqual(cr._num(280.0), "280")

    def test_the_s_table_has_the_same_cure(self):
        n = 2
        f = np.linspace(1e9, 2e9, 3)
        sa = np.zeros((3, n, n), dtype=complex)
        sb = sa.copy()
        sb[:, 0, 1] = 0.997                         # 99.7 % of full scale
        sc = sim.compare_s(f, sa, 50, f, sb, 50)
        text = "\n".join(cr.s_matrix_lines(sc, 1.0))
        self.assertNotRegex(text, r"\de[+-]\d")
        self.assertIn("100*", text)
        self.assertNotRegex(_text(_s_result(sc)), r"\de[+-]\d")

    def test_from_100_percent_up_it_is_a_multiple(self):
        self.assertEqual(cr._rel(280.0), "3.8 × A")
        self.assertEqual(cr._rel(1200.0), "13 × A")
        self.assertEqual(cr._rel(-250.0), "-1.5 × A (the sign flipped)")
        self.assertEqual(cr._rel(99.0), "99 % higher than A")
        self.assertEqual(cr._rel(-9.7), "9.7 % lower than A")
        text = _text(_result(dl_worst=280.0))
        self.assertIn("inductance L: B is 3.8 × A", text)
        self.assertNotIn("280 %", text)

    def test_the_lqr_table_columns_are_sized_from_their_content(self):
        """A fixed 24 ran 'B is 280 % higher than A' into the next column;
        every row's 'at' column must start at the same place, after a gap."""
        for dl in (0.4, 280.0, -1234.5):
            lines = cr.compare_summary_lines(_result(dl_worst=dl), 1, 1, 5)
            k = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("largest difference"))
            head = lines[k]
            col = head.index("at  ")
            for ln in lines[k + 1:k + 4]:
                self.assertEqual(ln[col - 2:col], "  ", (dl, ln))
                self.assertNotEqual(ln[col], " ", (dl, ln))

    def test_the_ranked_s_list_is_aligned(self):
        res = _result()
        res.s = _fifteen_port()
        lines = cr.compare_summary_lines(res, 1, 1, 5)
        k = next(i for i, ln in enumerate(lines) if "The largest:" in ln)
        rows = lines[k + 1:k + 4]
        self.assertEqual(len({ln.index(" %") for ln in rows}), 1, rows)


def _s_result(sc):
    return cr.CompareResult("a", "b", "", s=sc, has_setup=False,
                            z_why=cr.NO_SETUP)


class TestLineTags(unittest.TestCase):

    def test_headings_bold_and_overs_red(self):
        self.assertEqual(cr.line_tags("IN SHORT"), ("head",))
        self.assertEqual(cr.line_tags("  NOT THE SAME in 0 Hz - 30 GHz:"),
                         ("bad",))
        self.assertEqual(cr.line_tags("  inductance L   ...   1 %  OVER"),
                         ("bad",))
        self.assertEqual(cr.line_tags("  inductance L   ...   1 %  ok"), ())
        self.assertEqual(cr.line_tags(f"{cr.MARK_DIFFERENT} b.s1p: NOT"),
                         ("bad",))
        self.assertEqual(cr.line_tags(f"{cr.MARK_SAME} b.s1p: the same"), ())


class TestReviewFormatting(unittest.TestCase):

    def test_three_digits_below_ten_so_over_never_reads_equal(self):
        self.assertEqual(cr._num(1.04), "1.04")
        self.assertEqual(cr._num(5.04), "5.04")

    def test_b_near_zero_is_not_scientific(self):
        self.assertEqual(cr._rel(-100.0004), "about zero where A is not")

    def test_a_file_called_COVER_is_not_red(self):
        self.assertEqual(cr.line_tags("  B:   COVER.s2p"), ())
        self.assertEqual(cr.line_tags("  Your limit is 1 %  ->  OVER the "
                                      "limit."), ("bad",))


class TestPctViewSpan(unittest.TestCase):

    def test_one_wild_point_does_not_scale_the_axis(self):
        v = np.full(100, 1.0)
        v[3] = 4e8
        span, off = cr.pct_view_span(v, 1.0)
        self.assertIsNotNone(span)
        self.assertLess(span, 10)
        self.assertEqual(off, 1)
        self.assertEqual(cr.pct_view_span(np.full(5, np.nan), 1.0), (None, 0))


class TestNoTk(unittest.TestCase):

    def test_importing_it_pulls_in_no_tkinter_or_matplotlib(self):
        code = ("import sys; import pkg_rlc.present.compare_report; "
                "bad = [m for m in ('tkinter', 'matplotlib') "
                "if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()


class TestSelfResonanceIsReadThroughThePole(unittest.TestCase):
    """Stage-3 review: one network on three grids read 26.991 / 26.858 /
    26.922 GHz (true 26.902) because Im Z was interpolated across its pole,
    and the reading said the resonance had moved 0.65 %."""

    def test_one_network_on_three_grids_gives_one_resonance(self):
        L, C, R = 1e-9, 35e-15, 0.5

        def z(f):
            w = 2 * np.pi * f
            zl = R + 1j * w * L
            zc = 1 / (1j * w * C)
            return zl * zc / (zl + zc)
        got = [sim.self_resonance(f, z(f)) for f in
               (np.linspace(1e8, 40e9, n) for n in (301, 401, 527))]
        spread = (max(got) - min(got)) / min(got)
        self.assertLess(spread, 1e-5)
