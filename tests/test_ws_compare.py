"""
The Compare files WORKSPACE: `pkg_rlc.panels.ws_compare` in a real `App`.

`docs/design_workspaces.md` § 4, stage 3.  It replaced the modeless Compare
window (`pkg_rlc/panels/compare_gui.py`, deleted with its Analyze menu item,
its Files right-click item and its refresh calls), and this file replaced
`tests/test_compare_files.py`: the pure half of that file -- the maths and
the reading -- lives in `tests/test_compare_report.py` (no Tk); what is here
is the workspace on screen.  Pinned:

  * It is the THIRD button on the strip, and the old routes are gone.
  * The reference defaults to the LOWEST top frequency; the user's pick
    sticks.  N ticked files give N verdict lines and N curves per panel.
  * NO default setup: empty tables (or "Raw S-parameters only") compare S
    only and say "No setup defined".
  * "Copy setup from trace" copies ONCE: nothing links the two afterwards.
  * A refused setup paints its cells and is the extracted level's "why", and
    is never handed to the solver.
  * Limits re-judge and the marker re-reads WITHOUT solving; a drag on the
    plot moves the marker field to a grid point of the reference.
  * Removing a loaded file updates the workspace at once (the old window
    compared from arrays it had kept).
  * The session block round-trips; switching through the workspace keeps
    the RLC plot's M / V / Delete keys.
  * The left column is a measured budget (nothing clipped at 1040x600).

Not in `FAST_MODULES`: it imports tkinter.
"""

from __future__ import annotations

import importlib
import math
import pathlib
import sys
import tempfile
import tkinter as tk
import unittest
from tkinter import messagebox
from unittest import mock

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pkg_rlc.panels.ws_compare as wsc  # noqa: E402
from pkg_rlc.panels.setup_tables import ERROR_FG  # noqa: E402
from pkg_rlc.panels.workspaces import (  # noqa: E402
    WORKSPACES_SESSION_VERSION,
)
from pkg_rlc.panels.ws_compare import (  # noqa: E402
    COMPARE_STATE_VERSION, WHAT_S, WHAT_Z, limits_from_text, snap_marker_hz,
)
from pkg_rlc.physics import similarity as sim  # noqa: E402
from pkg_rlc.physics.core import ConnectionRow, MeasPortRow  # noqa: E402
from pkg_rlc.present.compare_report import (  # noqa: E402
    MARK_DIFFERENT, MARK_SAME, NO_SETUP, S_LIMIT_PCT_DEFAULT,
)
from pkg_rlc.services.session import session_from_dict  # noqa: E402


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                       # noqa: BLE001
        return False


TK_OK = _tk_ok()


def _z(f, L=1e-9, R=0.8, C=20e-15):
    w = 2 * np.pi * f
    return 1 / (1 / (R + 1j * w * L) + 1j * w * C)


def _s(z, z0=50.0):
    return ((z - z0) / (z + z0)).reshape(-1, 1, 1)


F30 = np.linspace(1e8, 30e9, 301)
F50 = np.linspace(1e8, 50e9, 351)
F80 = np.linspace(1e8, 80e9, 401)

L30 = "ind_30G/L.s1p"
L50 = "ind_50G/L.s1p"
L80 = "ind_80G/L.s1p"

P1 = [MeasPortRow("P1", "1", "")]


# ============================================================================
# Pure
# ============================================================================

class TestTheModule(unittest.TestCase):

    def test_it_never_imports_the_app_and_opens_no_dialog(self):
        src = pathlib.Path(wsc.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(import|from)\s+pkg_rlc\.frontend")
        self.assertNotIn("messagebox", src)

    def test_the_old_window_is_gone(self):
        with self.assertRaises(ImportError):
            importlib.import_module("pkg_rlc.panels.compare_gui")


class TestSnapMarker(unittest.TestCase):

    def test_nearest_in_log_f_on_a_log_axis(self):
        grid = [1e9, 10e9]
        # 3.5 GHz is nearer 1 GHz linearly ... but nearer 10 GHz in log f
        # (log10 3.5 = 0.544, past the 0.5 midpoint).
        self.assertEqual(snap_marker_hz(3.5e9, grid, log=True), 10e9)
        self.assertEqual(snap_marker_hz(3.5e9, grid, log=False), 1e9)

    def test_an_empty_grid_or_no_frequency_is_left_alone(self):
        self.assertEqual(snap_marker_hz(2e9, [], log=True), 2e9)
        self.assertTrue(math.isnan(snap_marker_hz(float("nan"), [1e9])))


class TestLimitsFromText(unittest.TestCase):

    def test_a_box_that_is_not_a_number_falls_back_to_its_default(self):
        lim = limits_from_text("2", "x", "")
        self.assertEqual(lim.s_pct, 2.0)
        self.assertEqual(lim.l_pct, sim.DEFAULT_L_LIMIT_PCT)
        self.assertEqual(lim.q_pct, sim.DEFAULT_Q_LIMIT_PCT)


# ============================================================================
# Tk: the real App
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _CmpCase(unittest.TestCase):
    """Three one-port inductors written to a temp dir -- 80 GHz with L
    +0.4 %, 30 GHz, 50 GHz with L +3 % -- added through Add File in that
    order, so the LAST added (the selected one) is NOT the reference."""

    def setUp(self):
        import pkg_rlc.frontend.app as pkg_rlc_gui
        import pkg_rlc.panels.panels_files as panels_files
        from generate_test_snp import write_touchstone
        self.panels_files = panels_files
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        self.paths = {}
        for name, f, L in (("ind_80G", F80, 1.004e-9), ("ind_30G", F30, 1e-9),
                           ("ind_50G", F50, 1.03e-9)):
            p = tmp / name / "L.s1p"
            write_touchstone(p, f, _s(_z(f, L=L)))
            self.paths[name] = p
        self.app = pkg_rlc_gui.App()
        self.app.withdraw()
        self._dialogs = {}
        for name in ("showinfo", "showwarning", "showerror"):
            p = mock.patch.object(messagebox, name)
            self._dialogs[name] = p.start()
            self.addCleanup(p.stop)
        self._add(*self.paths.values())
        self.app.show_workspace("compare")
        self._settle()
        self.ws = self.app.compare_ws

    def tearDown(self):
        for d in self._dialogs.values():
            d.assert_not_called()
        try:
            # Run the queued idle work (RowTable's resize, matplotlib's
            # draw_idle) BEFORE the widgets go, or Tk prints "invalid command
            # name" for each one.
            self._settle(1)
            self.app.destroy()
        except Exception:                                   # noqa: BLE001
            pass
        self._tmp.cleanup()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _map(self, geo="1040x600"):
        self.app.geometry(geo)
        self.app.deiconify()
        self._settle(5)

    def _add(self, *paths):
        with mock.patch.object(self.panels_files.filedialog,
                               "askopenfilenames",
                               return_value=tuple(str(p) for p in paths)):
            self.app._on_add_file()
        self._settle()

    def _fe(self, label):
        return self.app._file_by_label(label)

    def _file_lines(self):
        """The strip's per-file lines (the ones carrying a mark)."""
        return [ln for ln in self.ws.strip_lines()
                if ln.startswith((MARK_SAME + " ", MARK_DIFFERENT + " ",
                                  "? "))]

    def _menu_labels(self, menu):
        out = []
        end = menu.index("end")
        for i in range(0 if end is None else end + 1):
            try:
                out.append(menu.entrycget(i, "label"))
            except tk.TclError:
                pass
        return out


class TestItIsTheThirdWorkspace(_CmpCase):

    def test_the_third_toolbutton(self):
        sw = self.app.workspaces
        self.assertEqual(sw.keys(), ["rlc", "trace", "compare"])
        btn = sw.button("compare")
        self.assertEqual(btn.cget("text"), "Compare files")
        self.assertEqual(str(btn.cget("style")), "Toolbutton")
        self.assertIs(sw.workspace("compare").left_frame,
                      self.app.compare_ws_left)
        self.assertIs(sw.workspace("compare").right_widget,
                      self.app.compare_ws_right)

    def test_the_old_routes_are_gone(self):
        self.assertFalse(hasattr(self.app, "_on_compare_files"))
        for menu in (self.app._analyze_menu, self.app._files_panel._files_menu):
            self.assertFalse([lb for lb in self._menu_labels(menu)
                              if "Compare" in lb], self._menu_labels(menu))


class TestTheFiles(_CmpCase):

    def test_the_reference_defaults_to_the_lowest_top_frequency(self):
        self.assertEqual(self.ws.ref_var.get(), L30)
        self.assertIs(self.ws.reference, self._fe(L30))
        # One tick per OTHER loaded file, every one ticked to begin with.
        self.assertEqual(self.ws.check_labels(), [L80, L50])
        self.assertEqual([fe.label for fe in self.ws.checked_files()],
                         [L80, L50])

    def test_the_users_pick_sticks_when_a_file_is_added(self):
        self.ws.set_reference(L50)
        self.assertEqual(self.ws.check_labels(), [L80, L30])
        tmp = pathlib.Path(self._tmp.name)
        from generate_test_snp import write_touchstone
        f = np.linspace(1e8, 10e9, 101)
        p = tmp / "ind_10G" / "L.s1p"
        write_touchstone(p, f, _s(_z(f)))
        self._add(p)
        # A lower-band file arrived; the user's reference does not move.
        self.assertEqual(self.ws.ref_var.get(), L50)
        self.assertIn("ind_10G/L.s1p", self.ws.check_labels())

    def test_N_ticked_files_give_N_verdict_lines_and_N_curves_per_panel(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self._settle()
        self.assertEqual(len(self.ws.results), 2)
        lines = self._file_lines()
        self.assertEqual(len(lines), 2)
        self.assertIn(L80, lines[0])
        self.assertIn(L50, lines[1])
        self.assertEqual({k: len(v) for k, v in self.ws.curves.items()},
                         {"S": 2, "L": 2, "Q": 2, "R": 2})
        # Each pair on its own band: both read against the 30 GHz grid.
        for res in self.ws.results:
            self.assertEqual(res.label_a, L30)
        # Untick one: out of date until Compare, then ONE line, ONE curve.
        self.ws.set_checked([L50])
        self.assertTrue(self.ws.is_stale())
        self.assertTrue(self.ws.strip_lines()[0].startswith("Out of date"))
        self.ws.compare()
        self.assertFalse(self.ws.is_stale())
        self.assertEqual(len(self._file_lines()), 1)
        self.assertEqual({k: len(v) for k, v in self.ws.curves.items()},
                         {"S": 1, "L": 1, "Q": 1, "R": 1})

    def test_nothing_ticked_is_said_not_solved(self):
        self.ws.set_checked([])
        with mock.patch.object(wsc, "compare_against") as solver:
            self.ws.compare()
        solver.assert_not_called()
        self.assertIn("tick at least one", self.ws.status_lbl.cget("text"))

    def test_changing_the_reference_marks_the_results_out_of_date(self):
        self.ws.compare()
        self.assertFalse(self.ws.is_stale())
        self.ws.set_reference(L80)
        self.assertTrue(self.ws.is_stale())
        self.assertTrue(self.ws.strip_lines()[0].startswith("Out of date"))


class TestNoSetupDefined(_CmpCase):

    def test_empty_tables_compare_s_only_and_say_so(self):
        self.assertEqual(self.ws.setup.get(), ([], []))
        # Every file within the S limit, so every line is the one sentence
        # that names what was (not) judged.
        self.ws.s_lim_var.set("100")
        self.ws.compare()
        self._settle()
        self.assertEqual(list(self.ws.curves), ["S"])
        for res in self.ws.results:
            self.assertFalse(res.has_setup)
            self.assertEqual(res.ports, [])
        self.assertTrue(all("no setup defined" in ln
                            for ln in self._file_lines()))
        self.ws.toggle_details()
        self.assertTrue(any(NO_SETUP in ln for ln in self.ws.details_lines()))

    def test_raw_s_only_ignores_the_tables_and_greys_them(self):
        self.ws.setup.set(P1, [])
        self.ws.what_var.set(WHAT_S)
        self.ws._on_what_changed()
        self.assertEqual(str(self.ws.copy_mb.cget("state")), "disabled")
        self.ws.compare()
        self.assertEqual(list(self.ws.curves), ["S"])
        self.assertTrue(all(not r.has_setup for r in self.ws.results))
        # The tables were left as typed: choosing L/Q/R again uses them.
        self.ws.what_var.set(WHAT_Z)
        self.ws._on_what_changed()
        self.assertTrue(self.ws.is_stale())
        self.ws.compare()
        self.assertEqual(list(self.ws.curves), ["S", "L", "Q", "R"])


class TestCopySetupFromTrace(_CmpCase):

    def test_it_copies_once_and_does_not_link(self):
        tc = self.app.traces[0]
        tc.mports = [MeasPortRow("P1", "1", "")]
        tc.conn_rows = []
        self.ws._fill_copy_menu()
        labels = self._menu_labels(self.ws.copy_menu)
        # The entry names the trace AND the setup it would copy (a label
        # can outlive the setup it describes).
        mine = [t for t in labels if t.startswith(f"[{tc.id}] {tc.label}")]
        self.assertEqual(len(mine), 1, labels)
        self.assertIn("P1 (+1)", mine[0])
        self.ws.copy_setup_from_trace(tc)
        mports, conn = self.ws.setup.get()
        self.assertEqual([(r.name, r.plus, r.minus) for r in mports],
                         [("P1", "1", "")])
        self.assertIn("a copy", self.ws.status_lbl.cget("text"))
        # The trace moves; the tables do not ...
        tc.mports[0].plus = "2"
        tc.mports.append(MeasPortRow("P2", "1", ""))
        self.assertEqual([(r.name, r.plus) for r in self.ws.setup.get()[0]],
                         [("P1", "1")])
        # Nor at Compare time: nothing re-reads the trace.
        self.ws.compare()
        self.assertEqual([(r.name, r.plus) for r in self.ws.setup.get()[0]],
                         [("P1", "1")])
        self.assertTrue(all(res.setup.startswith("P1 (+1)")
                            for res in self.ws.results),
                        [res.setup for res in self.ws.results])
        # ... and the tables move; the trace does not.
        self.ws.setup.set([MeasPortRow("Q", "1", "")], [])
        self.assertEqual([r.name for r in tc.mports], ["P1", "P2"])
        self.assertEqual(tc.mports[0].plus, "2")


class TestARefusedSetup(_CmpCase):

    def test_the_cell_is_painted_and_the_reason_is_the_why(self):
        # Port 1 probed AND grounded: the probe rules refuse it.
        self.ws.setup.set(P1, [ConnectionRow("ground", "1")])
        self._settle()
        self.assertTrue(self.ws.setup.has_errors())
        cell = self.ws.setup.mp_table.data_row_widget(0, "plus")
        self.assertEqual(str(cell.cget("foreground")), ERROR_FG)
        calls = []
        real = wsc.compare_against

        def spy(ref, others, mports, conn, marker):
            calls.append((list(mports), list(conn)))
            return real(ref, others, mports, conn, marker)
        with mock.patch.object(wsc, "compare_against", side_effect=spy):
            self.ws.compare()
        # Never handed to the solver ...
        self.assertEqual(calls, [([], [])])
        # ... S is still judged, and the extracted level says why.
        for res in self.ws.results:
            self.assertIsNotNone(res.s)
            self.assertEqual(res.ports, [])
            self.assertTrue(res.has_setup)
            self.assertTrue(res.z_why.startswith("The setup is refused"),
                            res.z_why)
            self.assertIn("ground row", res.z_why)
        self.ws.s_lim_var.set("100")
        self.ws.rejudge()
        lines = self._file_lines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("refused" in ln for ln in lines), lines)

    def test_a_port_the_file_does_not_have_is_painted_too(self):
        self.ws.setup.set([MeasPortRow("P1", "2", "")], [])
        cell = self.ws.setup.mp_table.data_row_widget(0, "plus")
        self.assertEqual(str(cell.cget("foreground")), ERROR_FG)
        self.ws.compare()
        self.assertIn("not in this file", self.ws.results[0].z_why)


class TestLimitsAndMarkerSolveNothing(_CmpCase):

    def _limit_ys(self, kind):
        ax = self.ws.figure.axes[["S", "L", "Q", "R"].index(kind)]
        return sorted(float(ln.get_ydata()[0]) for ln in ax.get_lines()
                      if ln.get_gid() == "limit")

    def test_limits_rejudge_the_cached_results(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        before = list(self.ws.results)
        with mock.patch.object(wsc, "compare_against",
                               side_effect=AssertionError("solved")):
            for var in (self.ws.s_lim_var, self.ws.l_lim_var,
                        self.ws.q_lim_var):
                var.set("100")
            self.ws.rejudge()
            self.assertTrue(all(ln.startswith(MARK_SAME)
                                for ln in self._file_lines()),
                            self._file_lines())
            self.assertEqual(self._limit_ys("L"), [-100.0, 100.0])
            self.ws.l_lim_var.set("0.01")
            self.ws.rejudge()
            self.assertTrue(all(ln.startswith(MARK_DIFFERENT)
                                for ln in self._file_lines()))
            self.assertEqual(self._limit_ys("L"), [-0.01, 0.01])
        for a, b in zip(before, self.ws.results):
            self.assertIs(a, b)
        # A limit is not an input: the results are not out of date.
        self.assertFalse(self.ws.is_stale())

    def test_typing_a_limit_rejudges_by_itself(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self.ws.l_lim_var.set("100")
        self.ws.q_lim_var.set("100")
        self.ws.s_lim_var.set("100")
        for _ in range(30):
            self._settle(1)
            if self.ws._rejudge_job is None:
                break
            self.app.after(20)
        self.assertTrue(all(ln.startswith(MARK_SAME)
                            for ln in self._file_lines()))

    def test_the_marker_rereads_the_cached_curves(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self.ws.toggle_details()
        with mock.patch.object(wsc, "compare_against",
                               side_effect=AssertionError("solved")):
            self.ws.marker_var.set("10")
        for res in self.ws.results:
            self.assertEqual(res.marker_hz, 10e9)
        text = "\n".join(self.ws.details_lines())
        self.assertIn("At the marker frequency, 10", text)
        self.assertEqual([float(ln.get_xdata()[0])
                          for ln in self.ws.marker_lines], [10.0] * 4)
        self.assertTrue(all(ln.get_visible() for ln in self.ws.marker_lines))
        self.assertFalse(self.ws.is_stale())

    def test_a_drag_on_the_plot_moves_the_marker_field_to_a_grid_point(self):
        from matplotlib.backend_bases import MouseEvent
        self._map()
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self._settle()
        canvas = self.ws.canvas
        canvas.draw()
        ax = self.ws.figure.axes[1]                       # the dL panel
        y0 = float(np.mean(ax.get_ylim()))

        def ev(name, f_ghz):
            x, y = ax.transData.transform((f_ghz, y0))
            return MouseEvent(name, canvas, x, y, button=1)
        canvas.callbacks.process("button_press_event",
                                 ev("button_press_event", 3.0))
        self.assertTrue(self.ws._dragging)
        canvas.callbacks.process("motion_notify_event",
                                 ev("motion_notify_event", 12.3456))
        self.assertAlmostEqual(
            float(self.ws.marker_lines[0].get_xdata()[0]), 12.3456, places=2)
        canvas.callbacks.process("button_release_event",
                                 ev("button_release_event", 12.3456))
        self.assertFalse(self.ws._dragging)
        f = float(self.ws.marker_var.get()) * 1e9
        grid = self._fe(L30).ts.freqs
        k = int(np.argmin(np.abs(grid - f)))
        self.assertAlmostEqual(f / grid[k], 1.0, places=6)
        # The grid point nearest the release, in log f.
        want = snap_marker_hz(12.3456e9, grid, log=True)
        self.assertAlmostEqual(f / want, 1.0, places=6)
        # Every result reads there now.
        for res in self.ws.results:
            self.assertAlmostEqual(res.marker_hz / want, 1.0, places=6)


class TestNotJudgedIsGrey(_CmpCase):
    """Two LOSSLESS one-ports, 6 significant digits, L 0.4 % apart: the
    reference's |Re Z| is the file's rounding, so Q and R are not judged
    there (`similarity.RE_JUDGE_FRAC`) -- drawn grey, kept out of the
    verdict, never the +-100 % square wave."""

    def test_the_lossless_band_is_shaded_on_Q_and_R_and_not_judged(self):
        from generate_test_snp import write_touchstone
        tmp = pathlib.Path(self._tmp.name)
        f = np.linspace(1e8, 10e9, 201)
        pa = tmp / "lossless_a" / "L.s1p"
        pb = tmp / "lossless_b" / "L.s1p"
        write_touchstone(pa, f, _s(_z(f, R=0.0, C=1e-18)), digits=6)
        write_touchstone(pb, f, _s(_z(f, L=1.004e-9, R=0.0, C=1e-18)),
                         digits=6)
        self._add(pa, pb)
        self.ws.set_reference("lossless_a/L.s1p")
        self.ws.set_checked(["lossless_b/L.s1p"])
        self.ws.setup.set(P1, [])
        self.ws.q_lim_var.set("5")
        self.ws.compare()
        res = self.ws.results[0]
        self.assertGreater(res.ports[0].z.n_not_judged, 0)
        panels = ["S", "L", "Q", "R"]
        for kind in ("Q", "R"):
            ax = self.ws.figure.axes[panels.index(kind)]
            grey = [p for p in ax.patches if p.get_gid() == "not_judged"]
            self.assertTrue(grey, kind)
        ax_l = self.ws.figure.axes[panels.index("L")]
        self.assertFalse([p for p in ax_l.patches
                          if p.get_gid() == "not_judged"])
        # Q is not what the verdict reads: L (0.4 %) is within 1 %.
        line = self._file_lines()[0]
        self.assertNotIn("quality factor", line)
        self.ws.toggle_details()
        self.assertIn("Q and R not judged", "\n".join(self.ws.details_lines()))


class TestRemovingAFile(_CmpCase):

    def test_a_removed_file_loses_its_tick_its_line_and_its_curves(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self.assertEqual(len(self.ws.results), 2)
        idx = [fe.label for fe in self.app.files].index(L50)
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(idx)
        self.app._on_remove_file()
        self._settle()
        self.assertEqual(self.ws.check_labels(), [L80])
        self.assertEqual([r.label_b for r in self.ws.results], [L80])
        lines = self._file_lines()
        self.assertEqual(len(lines), 1)
        self.assertIn(L80, lines[0])
        self.assertTrue(self.ws.strip_lines()[0].startswith("Out of date"))
        self.assertEqual({k: len(v) for k, v in self.ws.curves.items()},
                         {"S": 1, "L": 1, "Q": 1, "R": 1})

    def test_removing_the_reference_drops_every_result(self):
        self.ws.compare()
        idx = [fe.label for fe in self.app.files].index(L30)
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(idx)
        self.app._on_remove_file()
        self._settle()
        self.assertEqual(self.ws.results, [])
        self.assertEqual(self.ws.ref_var.get(), L50)   # the new lowest
        self.assertEqual(self.ws.curves, {})

    def test_clear_all_files_is_heard_too(self):
        self.ws.compare()
        with mock.patch.object(messagebox, "askyesno", return_value=True):
            self.app._files_panel._on_clear_files()
        self._settle()
        self.assertEqual(self.ws.results, [])
        self.assertEqual(self.ws.ref_var.get(), "")
        self.assertEqual(self.ws.check_labels(), [])
        self.assertIn("Load at least two files", self.ws.strip_lines()[0])


class TestTheSession(_CmpCase):

    def _block(self):
        return self.app._session_dict(None).get("workspaces", {}).get(
            "compare")

    def test_the_defaults_write_no_block(self):
        self.app.show_workspace("rlc")
        self.assertIsNone(self._block())

    def test_it_round_trips(self):
        self.ws.set_reference(L50)
        self.ws.set_checked([L80])
        self.ws.setup.set(P1, [ConnectionRow("rlc_gnd", "1", R="1k")])
        self.ws.what_var.set(WHAT_Z)
        self.ws.s_lim_var.set("2")
        self.ws.l_lim_var.set("3")
        self.ws.q_lim_var.set("7")
        self.ws.marker_var.set("5")
        self.ws.compare()
        data = self.app._session_dict(None)
        block = data["workspaces"]["compare"]
        self.assertEqual(block["version"], COMPARE_STATE_VERSION)
        self.assertEqual(block["reference"], L50)
        self.assertEqual(block["checked"], [L80])
        self.assertEqual(block["limits"], {"s": "2", "l": "3", "q": "7"})
        self.assertEqual(block["marker_ghz"], "5")
        self.assertEqual(data["workspaces"]["active"], "compare")
        # Somewhere else entirely, then back from the file.
        self.ws.set_reference(L30)
        self.ws.setup.set([], [])
        self.ws.s_lim_var.set("9")
        self.ws.marker_var.set("")
        self.app.show_workspace("rlc")
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        ws = self.app.compare_ws
        self.assertEqual(self.app.workspaces.active, "compare")
        self.assertEqual(ws.ref_var.get(), L50)
        self.assertIs(ws.reference, self._fe(L50))        # the NEW entry
        self.assertEqual([fe.label for fe in ws.checked_files()], [L80])
        mports, conn = ws.setup.get()
        self.assertEqual([(r.name, r.plus) for r in mports], [("P1", "1")])
        self.assertEqual([(r.kind, r.ports, r.R) for r in conn],
                         [("rlc_gnd", "1", "1k")])
        self.assertEqual((ws.s_lim_var.get(), ws.l_lim_var.get(),
                          ws.q_lim_var.get()), ("2", "3", "7"))
        self.assertEqual(ws.marker_var.get(), "5")
        self.assertEqual(ws.results, [])                  # never saved

    def test_a_block_this_build_cannot_read_costs_only_itself(self):
        data = self.app._session_dict(None)
        data["workspaces"] = {"version": WORKSPACES_SESSION_VERSION,
                              "active": "compare",
                              "compare": {"version": 99}}
        self.app.results_text.delete("1.0", tk.END)
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "compare")
        log = self.app.results_text.get("1.0", tk.END)
        self.assertIn("compare", log)
        self.assertIn("99", log)

    def test_the_raw_s_choice_round_trips(self):
        self.ws.what_var.set(WHAT_S)
        self.ws._on_what_changed()
        block = self._block()
        self.assertEqual(block["what"], WHAT_S)
        self.ws.state_set({"version": 1, "what": WHAT_Z})
        self.assertEqual(self.ws.what_var.get(), WHAT_Z)
        self.ws.state_set(block)
        self.assertEqual(self.ws.what_var.get(), WHAT_S)
        self.assertEqual(str(self.ws.copy_mb.cget("state")), "disabled")


class TestThePlotKeysSurviveTheWorkspace(_CmpCase):
    """The RLC plot's M / V / Delete keys after a trip through Compare files
    -- real key events, the `tests/test_workspaces.py` method: the switch
    hands the plot canvas focus back, nothing else does."""

    def _axes_center(self):
        widget = self.app.plot.canvas.get_tk_widget()
        bb = self.app.plot.figure.axes[0].get_window_extent()
        return (int((bb.x0 + bb.x1) / 2),
                int(widget.winfo_height() - (bb.y0 + bb.y1) / 2))

    def _own_focus(self):
        for _ in range(10):
            if self.app.focus_get() is not None:
                return
            target = self.app.tk.call("focus", "-lastfor", self.app)
            self.app.tk.call("focus", "-force", target)
            self._settle()
        self.skipTest("could not obtain the keyboard focus on this desktop")

    def _send(self, seq):
        self._own_focus()
        widget = self.app.plot.canvas.get_tk_widget()
        x, y = self._axes_center()
        widget.event_generate("<Motion>", x=x, y=y)
        self._settle(2)
        widget.event_generate(seq, x=x, y=y, when="now")
        self._settle(2)
        return [kind for kind, _ in self.app.plot.view._anno_stack]

    def test_M_V_and_Delete_land_after_a_round_trip(self):
        self.app.show_workspace("rlc")
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app._on_calculate()
        self._map()
        self.app.show_workspace("compare")
        self._settle()
        # Focus somewhere in this workspace, as a user would leave it.
        self.ws.marker_entry.focus_set()
        self._settle()
        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(str(self.app.tk.call("focus", "-lastfor", self.app)),
                         str(self.app.plot.canvas.get_tk_widget()))
        self.assertEqual(self._send("<Key-m>"), ["m"])
        self.assertEqual(self._send("<Key-v>"), ["m", "v"])
        self.assertEqual(self._send("<Key-Delete>"), ["m"])


class TestTheLayoutBudget(_CmpCase):
    """
    Measured on this box (Microsoft YaHei UI 9, tk scaling 1.333), the
    numbers are in `CompareWorkspace._build_left`'s docstring.  Nothing on
    the left is clipped at 1040x600 in ordinary use; in the worst case the
    Compare button and the limits are still on screen and it is the setup
    tables that give.
    """

    def _measure(self):
        host = self.app.compare_ws_left.master
        b = self.ws.compare_btn
        bottom = b.winfo_rooty() + b.winfo_height() - host.winfo_rooty()
        return bool(b.winfo_ismapped()), bottom, host.winfo_height()

    def _right_edge(self, w):
        host = self.app.compare_ws_left.master
        return w.winfo_rootx() + w.winfo_width() - host.winfo_rootx()

    def test_ordinary_use_fits_at_the_minsize_with_room(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self._map("1040x600")
        mapped, bottom, host_h = self._measure()
        self.assertEqual(host_h, 421)
        self.assertTrue(mapped)
        self.assertLessEqual(bottom + 20, host_h, (bottom, host_h))
        host_w = self.app.compare_ws_left.master.winfo_width()
        self.assertLessEqual(self.ws._col.winfo_reqwidth(), host_w)
        for w in (self.ws.copy_mb, self.ws.marker_entry, self.ws.ref_cbo,
                  *self.ws.limit_entries.values()):
            self.assertTrue(w.winfo_ismapped(), w)
            self.assertLessEqual(self._right_edge(w), host_w, w)
        # The setup tables were given what they asked for.
        self.assertEqual(self.ws.setup.winfo_height(),
                         self.ws.setup.winfo_reqheight())
        # The right side: every verdict line on screen, and a plot.
        n = int(self.ws.verdict_text.count("1.0", "end", "displaylines")[0])
        self.assertLessEqual(n, int(self.ws.verdict_text.cget("height")))
        self.assertGreater(self.ws.canvas.get_tk_widget().winfo_height(), 400)
        self.assertTrue(self.ws.details_btn.winfo_ismapped())

    def test_at_1500x900_the_details_open_beside_a_full_plot(self):
        self.ws.setup.set(P1, [])
        self.ws.compare()
        self._map("1500x900")
        self.ws.toggle_details()
        self._settle(5)
        mapped, bottom, host_h = self._measure()
        self.assertTrue(mapped)
        self.assertLessEqual(bottom, host_h)
        self.assertTrue(self.ws.details_text.winfo_ismapped())
        self.assertGreater(self.ws.details_text.winfo_height(), 150)
        self.assertGreater(self.ws.canvas.get_tk_widget().winfo_height(), 450)

    def test_the_worst_case_squeezes_the_tables_not_compare(self):
        self.ws.setup.set(
            [MeasPortRow(f"P{i}", "1", "") for i in range(1, 5)],
            [ConnectionRow("rlc_gnd", "1", R="1")] * 4)
        self._map("1040x600")
        mapped, bottom, host_h = self._measure()
        self.assertGreater(self.ws._col.winfo_reqheight(), host_h)
        self.assertTrue(mapped)
        self.assertLessEqual(bottom, host_h)
        self.assertTrue(self.ws.marker_entry.winfo_ismapped())
        self.assertGreater(self.ws.compare_btn.winfo_rooty(),
                           self.ws.setup.winfo_rooty())


if __name__ == "__main__":
    unittest.main()
