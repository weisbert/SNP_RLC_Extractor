"""
Compare files: is the 80 GHz extraction the 30 GHz one, below 30 GHz?

The owner's request, verbatim in spirit: an inductor extracted to 30 GHz and
again to 80 GHz -- inside 0-30 GHz, is the 80 GHz one the same?  Pinned:

  * THE MATHS (`pkg_rlc.physics.similarity`): identical data reads -300 dB and
    0 %; a known 1 % inductance change reads +1 % at low frequency; the band is
    the OVERLAP and nothing is extrapolated; the grid is the COARSER file's,
    because interpolating the coarse file would invent a difference; a Z0
    mismatch is renormalised away, a port-count mismatch is refused; a point
    where the reference crosses zero is excluded and COUNTED.
  * THE TEXT: the verdict follows the reader's limit, not a constant.
  * THE WINDOW, through the real App: reachable from the Analyze menu and the
    Files right-click menu; A defaults to the lower-band file; a removed file
    is said to be gone rather than compared from memory.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from pkg_rlc.physics import similarity as sim  # noqa: E402
from pkg_rlc.panels import compare_gui as cg  # noqa: E402


def _z(f, L=1e-9, R=0.8, C=20e-15):
    w = 2 * np.pi * f
    return 1 / (1 / (R + 1j * w * L) + 1j * w * C)


def _s(z, z0=50.0):
    return ((z - z0) / (z + z0)).reshape(-1, 1, 1)


F30 = np.linspace(1e8, 30e9, 301)
F80 = np.linspace(1e8, 80e9, 401)


# ============================================================================
# Pure -- the maths
# ============================================================================


class TestCommonAxis(unittest.TestCase):

    def test_the_band_is_the_overlap(self):
        ax = sim.common_axis(F30, F80)
        self.assertEqual((ax.lo, ax.hi), (1e8, 30e9))
        self.assertLessEqual(ax.freqs.max(), 30e9)
        self.assertGreaterEqual(ax.freqs.min(), 1e8)

    def test_the_grid_is_the_coarser_files(self):
        # Inside 0.1-30 GHz: F30 has 301 points, F80 has 150 -- F80 is coarser.
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
        # Q = wL/R follows L exactly here.
        np.testing.assert_allclose(c.dq_pct, 1.0, rtol=1e-9)

    def test_the_sign_says_which_way(self):
        f = np.linspace(1e8, 1e9, 51)
        c = sim.compare_z(f, _z(f, C=0.0), f, _z(f, L=0.98e-9, C=0.0))
        self.assertLess(c.l.value, 0)

    def test_a_zero_crossing_of_the_reference_is_excluded_and_counted(self):
        # A tank through its self-resonance: Im(Z) -- so L and Q -- crosses
        # zero; the percentage there is noise divided by nothing.
        L, R, C = 1e-9, 0.8, 20e-15
        f0 = np.sqrt(1 / (L * C) - (R / L) ** 2) / (2 * np.pi)   # Im(Z) = 0
        f = np.sort(np.append(np.linspace(1e9, 60e9, 591), f0))
        c = sim.compare_z(f, _z(f), f, _z(f, C=20.2e-15))
        self.assertGreater(c.l.excluded, 0)
        self.assertTrue(np.isnan(c.dl_pct).any())
        self.assertTrue(np.isfinite(c.l.value))

    def test_ordinary_R_is_not_mistaken_for_zero(self):
        # R climbs four decades into the resonance; 1 % of the PEAK would have
        # thrown the low-frequency points away.  The median does not.
        # 0.1-80 GHz runs through the 35.6 GHz self-resonance, where R peaks.
        c = sim.compare_z(F80, _z(F80), F80, _z(F80, R=0.81))
        self.assertGreater(np.nanmax(_z(F80).real), 1000 * 0.8)
        self.assertEqual(c.r.excluded, 0)


# ============================================================================
# Pure -- the text
# ============================================================================


def _result(dl_worst=0.4):
    ax = sim.common_axis(F30, F30)
    s = sim.SCompare(axis=ax, err_db=np.full(len(F30), -52.0), worst_db=-52.0,
                     worst_f=27e9, worst_entry=(1, 1))
    z = sim.ZCompare(axis=ax, dl_pct=np.zeros(len(F30)),
                     dq_pct=np.zeros(len(F30)), dr_pct=np.zeros(len(F30)),
                     l=sim.Worst(dl_worst, 29e9, 0), q=sim.Worst(-3.0, 1e9, 0),
                     r=sim.Worst(2.0, 1e9, 0))
    return cg.CompareResult("ind_30G/L.s1p", "ind_80G/L.s1p", cg.DEFAULT_SETUP,
                            s=s, ports=[cg.PortCompare("Z", z)])


class TestSummary(unittest.TestCase):

    def _fifteen_port(self):
        """The owner's case: 15 ports, S(14,15) / S(15,14) just over -40 dB."""
        f = np.linspace(0, 30e9, 31)
        rng = np.random.default_rng(0)
        sa = (rng.normal(size=(15, 15)) * 0.3 + 0j)[None] * np.ones((31, 1, 1))
        sb = sa.copy()
        sb[1, 13, 14] += 0.0106                     # -39.5 dB
        sb[1, 14, 13] += 0.0105
        sb[:, 2, 3] += 0.001                        # -60 dB
        return sim.compare_s(f, sa, 50, f, sb, 50)

    def test_a_two_digit_port_is_never_run_together(self):
        """'S1415' was printed for S(14,15) -- unreadable, and ambiguous."""
        text = "\n".join(cg.s_matrix_lines(self._fifteen_port(), -40,
                                            cg.DIFFERENT))
        self.assertIn("S(14,15) = -39.5 dB", text)
        self.assertNotIn("S1415", text)
        self.assertIn("1.06 %", text)

    def test_every_entry_of_the_matrix_is_shown(self):
        lines = cg.s_matrix_lines(self._fifteen_port(), -40, cg.DIFFERENT)
        self.assertIn("(2 of 225 entries over the limit)", "\n".join(lines))
        head = next(i for i, ln in enumerate(lines) if "every entry" in ln)
        rows = lines[head + 2:head + 17]
        self.assertEqual(len(rows), 15)
        self.assertIn("-39.5*", rows[13])
        self.assertIn("-60.0 ", rows[2])
        self.assertEqual(rows[0].split()[1:], ["--"] * 15)

    def test_one_wild_point_does_not_flatten_the_percentage_axis(self):
        """A near-open port read -4e8 % at 1 MHz while the band sat inside
        +-2 %: autoscaled, every other point lay on the zero line."""
        ys = np.abs(np.r_[4e8, np.linspace(-2, 2, 200)])
        span, n_off = cg.pct_view_span(ys, 1.0)
        self.assertLess(span, 10)
        self.assertGreaterEqual(span, 2.0)          # the limit lines show
        self.assertEqual(n_off, 1)
        self.assertEqual(cg.pct_view_span(np.linspace(0, 2, 50), 1.0),
                         (None, 0))

    def test_identical_entries_are_not_ranked(self):
        lines = cg.s_matrix_lines(self._fifteen_port(), -40, cg.DIFFERENT)
        head = next(i for i, ln in enumerate(lines) if "largest" in ln)
        ranked = lines[head + 1:head + 4]
        self.assertEqual([r.split()[0] for r in ranked],
                         ["S(14,15)", "S(15,14)", "S(3,4)"])
        self.assertIn("every entry", lines[head + 4])

    def test_within_every_limit_is_SAME(self):
        text = "\n".join(cg.compare_summary_lines(_result(), -40, 1, 5))
        self.assertIn("Overall: SAME", text)
        self.assertIn("A = ind_30G/L.s1p", text)
        self.assertIn("S(1,1)", text)

    def test_the_verdict_follows_the_readers_limit(self):
        text = "\n".join(cg.compare_summary_lines(_result(0.4), -40, 0.2, 5))
        self.assertIn("Overall: DIFFERENT", text)
        self.assertIn("L", text.split("over their limit:")[1])
        text = "\n".join(cg.compare_summary_lines(_result(0.4), -60, 1, 5))
        self.assertIn("S-parameters", text.split("over their limit:")[1])

    def test_a_negative_percentage_is_judged_by_magnitude(self):
        self.assertEqual(cg.verdict(-3.0, 5.0, db=False), cg.SAME)
        self.assertEqual(cg.verdict(-6.0, 5.0, db=False), cg.DIFFERENT)
        self.assertEqual(cg.verdict(float("nan"), 5.0, db=False), "")

    def test_nothing_compared_says_so(self):
        res = cg.CompareResult("a", "b", cg.DEFAULT_SETUP, s_why="x", z_why="y")
        text = "\n".join(cg.compare_summary_lines(res, -40, 1, 5))
        self.assertIn("not compared -- x", text)
        self.assertIn("nothing could be compared", text)


# ============================================================================
# Tk -- the window in the real App
# ============================================================================

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    TK_OK = True
except Exception:                                   # pragma: no cover
    TK_OK = False


@unittest.skipUnless(TK_OK, "no display")
class TestCompareWindow(unittest.TestCase):

    def setUp(self):
        import pkg_rlc.frontend.app as pkg_rlc_gui
        import pkg_rlc.panels.panels_files as panels_files
        from generate_test_snp import write_touchstone
        self.panels_files = panels_files
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.p30 = tmp / "ind_30G" / "L.s1p"
        self.p80 = tmp / "ind_80G" / "L.s1p"
        write_touchstone(self.p30, F30, _s(_z(F30)))
        write_touchstone(self.p80, F80, _s(_z(F80, L=1.004e-9)))
        self.app = pkg_rlc_gui.App()
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()
        self._tmp.cleanup()

    def _settle(self):
        for _ in range(3):
            self.app.update_idletasks()
            self.app.update()

    def _add(self, *paths):
        with mock.patch.object(self.panels_files.filedialog,
                               "askopenfilenames",
                               return_value=tuple(str(p) for p in paths)):
            self.app._on_add_file()
        self._settle()

    def _menu_labels(self, menu):
        out = []
        for i in range(menu.index("end") + 1):
            try:
                out.append(menu.entrycget(i, "label"))
            except tk.TclError:
                pass
        return out

    def test_it_is_on_the_analyze_menu_and_the_files_menu(self):
        menubar = self.app.nametowidget(self.app["menu"])
        analyze = None
        for i in range(menubar.index("end") + 1):
            try:
                if menubar.entrycget(i, "label") == "Analyze":
                    analyze = self.app.nametowidget(
                        menubar.entrycget(i, "menu"))
            except tk.TclError:
                pass
        self.assertIsNotNone(analyze)
        self.assertIn(cg.COMPARE_MENU_LABEL, self._menu_labels(analyze))
        self.assertIn(cg.COMPARE_MENU_LABEL,
                      self._menu_labels(self.app._files_panel._files_menu))

    def test_one_file_is_refused_by_name(self):
        self._add(self.p30)
        with mock.patch.object(cg.messagebox, "showinfo") as info:
            self.assertIsNone(self.app._on_compare_files())
        self.assertIn("two files", info.call_args[0][1])

    def test_the_lower_band_file_is_the_reference(self):
        # The 80 GHz file is added LAST, so it is the selected one.
        self._add(self.p30, self.p80)
        self.app._on_compare_files()
        self._settle()
        w = cg.live_windows(self.app)[0]
        self.assertEqual(w.a_var.get(), "ind_30G/L.s1p")
        self.assertEqual(w.b_var.get(), "ind_80G/L.s1p")
        text = w.text.get("1.0", tk.END)
        self.assertIn("100 MHz - 30 GHz", text)
        # A 0.4 % L change, read at the band's low end where C is negligible.
        lpct = w._res.ports[0].z.dl_pct
        self.assertAlmostEqual(float(lpct[0]), 0.4, delta=0.01)

    def test_editing_a_limit_changes_the_verdict_without_recomputing(self):
        self._add(self.p30, self.p80)
        self.app._on_compare_files()
        self._settle()
        w = cg.live_windows(self.app)[0]
        res = w._res
        w.l_lim_var.set("100")
        w.q_lim_var.set("100")
        w.s_lim_var.set("0")
        w._render()
        self.assertIn("Overall: SAME", w.text.get("1.0", tk.END))
        w.l_lim_var.set("0.01")
        w._render()
        self.assertIn("Overall: DIFFERENT", w.text.get("1.0", tk.END))
        self.assertIs(w._res, res)

    def test_a_removed_file_is_said_to_be_gone(self):
        self._add(self.p30, self.p80)
        self.app._on_compare_files()
        self._settle()
        w = cg.live_windows(self.app)[0]
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(1)
        self.app._on_remove_file()
        self._settle()
        self.assertIn("no longer loaded", w.text.get("1.0", tk.END))

    def test_a_trace_setup_is_applied_to_both_files(self):
        self._add(self.p30, self.p80)
        self.app._on_compare_files()
        self._settle()
        w = cg.live_windows(self.app)[0]
        choices = [t for t, _tc in cg.trace_choices(self.app.traces)]
        self.assertEqual(len(choices), 3)
        w.setup_var.set(choices[1])
        w._recompute()
        self.assertIn("from trace [1]", w.text.get("1.0", tk.END))
        self.assertEqual(len(w._res.ports), 1)


if __name__ == "__main__":
    unittest.main()
