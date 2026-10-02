"""
The workspace switch: `pkg_rlc.panels.workspaces` and its wiring in the App.

`docs/design_workspaces.md` § 1.  Two halves, the split the other panel
suites use:

  PURE -- the session block, built from and applied to a fake switch.  No Tk
  root, no App: it is where a wrong DECISION about what is written and what
  is ignored gets caught.

  TK -- a real `App`, mapped at the 1040x600 minsize where the numbers are
  pinned.  It is where a wrong WIRE gets caught: the strip is above the
  PanedWindow, the right pane really swaps, the Files panel really is the
  same widget, and the M / V / Delete keys really land on the plot after a
  round trip -- sent as Tk key events, which the plot receives only when
  its canvas has focus (measured: with an Entry focused, `<Key-m>` on the
  canvas does nothing).

Not in `FAST_MODULES`: it imports tkinter.
"""

from __future__ import annotations

import pathlib
import sys
import tkinter as tk
import unittest
from tkinter import ttk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pkg_rlc.panels.workspaces as wsm  # noqa: E402
from pkg_rlc.panels.workspaces import (  # noqa: E402
    DEFAULT_WORKSPACE,
    WORKSPACES_SESSION_VERSION,
    apply_workspaces_session_state,
    workspaces_session_state,
)
from pkg_rlc.services.session import session_from_dict, session_to_dict  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "pi_2port.s2p"


def _ensure_fixtures():
    if not FIXTURE.exists():
        import generate_test_snp
        generate_test_snp.main()


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                       # noqa: BLE001
        return False


TK_OK = _tk_ok()


# ============================================================================
# Pure: the session block over a fake switch
# ============================================================================

class _FakeWs:
    def __init__(self, state_get=None, state_set=None):
        self.state_get = state_get
        self.state_set = state_set


class _FakeSwitch:
    """Shaped like WorkspaceSwitch and deliberately not one."""

    def __init__(self, keys=("rlc", "trace"), active="rlc", **ws):
        self._keys = list(keys)
        self.active = active
        self._ws = {k: ws.get(k, _FakeWs()) for k in self._keys}
        self.shown: list[str] = []

    def keys(self):
        return list(self._keys)

    def workspace(self, key):
        return self._ws[key]

    def show(self, key):
        if key not in self._ws:
            raise KeyError(key)
        self.active = key
        self.shown.append(key)


class TestTheBlockIsWrittenOnlyWhenItSaysSomething(unittest.TestCase):

    def test_the_defaults_write_nothing(self):
        self.assertEqual(workspaces_session_state(_FakeSwitch()), {})

    def test_a_non_default_active_workspace_is_written(self):
        self.assertEqual(
            workspaces_session_state(_FakeSwitch(active="trace")),
            {"version": WORKSPACES_SESSION_VERSION, "active": "trace"})

    def test_a_workspace_with_state_is_written_under_its_key(self):
        sw = _FakeSwitch(trace=_FakeWs(state_get=lambda: {"nets": [1]}))
        self.assertEqual(
            workspaces_session_state(sw),
            {"version": WORKSPACES_SESSION_VERSION, "active": "rlc",
             "trace": {"nets": [1]}})

    def test_an_empty_state_contributes_no_key(self):
        sw = _FakeSwitch(trace=_FakeWs(state_get=lambda: {}))
        self.assertEqual(workspaces_session_state(sw), {})

    def test_session_to_dict_leaves_an_empty_block_out(self):
        d = session_to_dict([], [], {}, {}, workspaces={})
        self.assertNotIn("workspaces", d)
        d = session_to_dict([], [], {}, {},
                            workspaces={"version": 1, "active": "trace"})
        self.assertEqual(d["workspaces"], {"version": 1, "active": "trace"})


class TestTheBlockIsReadForgivingly(unittest.TestCase):

    def test_no_block_shows_the_default(self):
        sw = _FakeSwitch(active="trace")
        self.assertEqual(apply_workspaces_session_state(sw, {}), [])
        self.assertEqual(sw.active, DEFAULT_WORKSPACE)
        sw = _FakeSwitch(active="trace")
        apply_workspaces_session_state(sw, None)
        self.assertEqual(sw.active, DEFAULT_WORKSPACE)

    def test_the_active_workspace_is_restored(self):
        sw = _FakeSwitch()
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "trace"})
        self.assertEqual(notes, [])
        self.assertEqual(sw.active, "trace")

    def test_a_wrong_version_costs_the_block_and_says_so(self):
        sw = _FakeSwitch(active="trace")
        notes = apply_workspaces_session_state(
            sw, {"version": 99, "active": "trace"})
        self.assertEqual(len(notes), 1)
        self.assertIn("99", notes[0])
        self.assertEqual(sw.active, DEFAULT_WORKSPACE)

    def test_an_unknown_active_key_falls_back_with_a_note(self):
        sw = _FakeSwitch()
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "compare"})
        self.assertEqual(len(notes), 1)
        self.assertIn("compare", notes[0])
        self.assertEqual(sw.active, DEFAULT_WORKSPACE)

    def test_a_state_is_handed_to_its_workspace(self):
        got = []
        sw = _FakeSwitch(trace=_FakeWs(state_set=got.append))
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "rlc",
                 "trace": {"nets": [1]}})
        self.assertEqual(notes, [])
        self.assertEqual(got, [{"nets": [1]}])

    def test_a_state_for_a_workspace_this_build_lacks_is_a_note(self):
        sw = _FakeSwitch()
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "rlc",
                 "compare": {"a": 1}})
        self.assertEqual(len(notes), 1)
        self.assertIn("compare", notes[0])
        self.assertEqual(sw.active, "rlc")

    def test_a_garbled_state_costs_only_itself(self):
        got = []
        sw = _FakeSwitch(trace=_FakeWs(state_set=got.append))
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "trace",
                 "trace": "nope"})
        self.assertEqual(len(notes), 1)
        self.assertEqual(got, [])
        self.assertEqual(sw.active, "trace")

    def test_a_state_set_that_raises_is_a_note_not_a_crash(self):
        def boom(_):
            raise ValueError("bad nets")
        sw = _FakeSwitch(trace=_FakeWs(state_set=boom))
        notes = apply_workspaces_session_state(
            sw, {"version": WORKSPACES_SESSION_VERSION, "active": "trace",
                 "trace": {"nets": [1]}})
        self.assertEqual(len(notes), 1)
        self.assertIn("bad nets", notes[0])
        self.assertEqual(sw.active, "trace")

    def test_the_session_reader_carries_the_block_opaquely(self):
        d = session_to_dict([], [], {}, {},
                            workspaces={"version": 1, "active": "trace"})
        sess = session_from_dict(d)
        self.assertEqual(sess.workspaces, {"version": 1, "active": "trace"})
        self.assertEqual(sess.warnings, [])

    def test_a_block_that_is_not_an_object_is_a_warning_not_a_refusal(self):
        d = session_to_dict([], [], {}, {})
        d["workspaces"] = "nope"
        sess = session_from_dict(d)
        self.assertEqual(sess.workspaces, {})
        self.assertTrue(any("workspaces" in w for w in sess.warnings),
                        sess.warnings)

    def test_an_old_session_has_an_empty_block(self):
        d = session_to_dict([], [], {}, {})
        self.assertNotIn("workspaces", d)
        self.assertEqual(session_from_dict(d).workspaces, {})


# ============================================================================
# Tk: the real App
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _AppCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        from pkg_rlc.frontend.app import App
        self.app = App()
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:                                   # noqa: BLE001
            pass

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _map(self):
        self.app.geometry("1040x600")
        self.app.deiconify()
        self._settle()

    def _outer_and_right(self):
        """The walk four other suites do: results_text -> the right
        PanedWindow -> its master, the outer one."""
        w = self.app.results_text
        while w is not None and w.winfo_class() != "TPanedwindow":
            w = w.master
        return w.master, w

    def _load_one_trace(self):
        from pkg_rlc.model.trace import FileEntry, TraceConfig
        from pkg_rlc.physics.core import parse_touchstone
        fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        tc = TraceConfig(id=1, file_label=fe.label, mode=1, port_a="1",
                         gnd_ports="2", label="t")
        self.app.traces.append(tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app._on_calculate()
        self._settle()


class TestTheStrip(_AppCase):

    def test_it_is_above_the_paned_window_and_full_width(self):
        self._map()
        outer, _ = self._outer_and_right()
        strip = self.app.workspaces
        self.assertEqual(strip.winfo_y(), 0)
        self.assertEqual(outer.winfo_y(), strip.winfo_height())
        self.assertEqual(strip.winfo_width(), self.app.winfo_width())

    def test_it_costs_the_plot_about_26_px_and_no_more(self):
        """The known cost `docs/design_workspaces.md` § 1.2 accepts.
        Measured 25 px on this box; the ceiling is the number the design
        quoted, so a taller strip is a change someone has to see."""
        self._map()
        self.assertLessEqual(self.app.workspaces.winfo_height(), 26)
        self.assertGreater(self.app.workspaces.winfo_height(), 0)

    def test_two_toolbuttons_and_no_compare_yet(self):
        self.assertEqual(self.app.workspaces.keys(), ["rlc", "trace"])
        for key, title in (("rlc", "RLC extraction"), ("trace", "Trace model")):
            btn = self.app.workspaces.button(key)
            self.assertEqual(btn.winfo_class(), "TRadiobutton")
            self.assertEqual(str(btn.cget("style")), "Toolbutton")
            self.assertEqual(btn.cget("text"), title)

    def test_rlc_is_showing_at_startup(self):
        self.assertEqual(self.app.workspaces.active, DEFAULT_WORKSPACE)

    def test_a_button_switches_and_the_radio_follows_a_programmatic_switch(self):
        self._map()
        self.app.workspaces.button("trace").invoke()
        self._settle()
        self.assertEqual(self.app.workspaces.active, "trace")
        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "rlc")
        self.assertEqual(self.app.workspaces._var.get(), "rlc")

    def test_an_unregistered_key_is_refused(self):
        with self.assertRaises(KeyError):
            self.app.show_workspace("compare")
        self.assertEqual(self.app.workspaces.active, "rlc")


class TestSwitchingSwapsTheRegions(_AppCase):

    def test_the_right_pane_swaps_and_the_rlc_tree_is_untouched(self):
        self._map()
        outer, right = self._outer_and_right()
        before = (outer.sashpos(0), right.sashpos(0),
                  self.app._ed_canvas.winfo_width(),
                  self.app.plot.master.winfo_height())
        self.assertEqual(before[:3], (460, 173, 431),
                         "the RLC layout is not where it was measured to be")

        self.app.show_workspace("trace")
        self._settle()
        self.assertEqual(outer.panes()[1], str(self.app.trace_ws_right))
        self.assertNotIn(str(right), outer.panes())
        self.assertFalse(self.app.results_text.winfo_ismapped())
        self.assertFalse(self.app.plot.winfo_ismapped())
        self.assertTrue(self.app.trace_ws_right.winfo_ismapped())
        # The forgotten pane is still the outer PanedWindow's child, which is
        # what the four suites that walk `.master` from results_text read.
        self.assertIs(right.master, outer)
        self.assertEqual(outer.sashpos(0), 460)

        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(outer.panes()[1], str(right))
        self.assertFalse(self.app.trace_ws_right.winfo_ismapped())
        self.assertTrue(self.app.results_text.winfo_ismapped())
        self.assertTrue(self.app.plot.winfo_ismapped())
        self.assertIs(self._outer_and_right()[1], right)
        after = (outer.sashpos(0), right.sashpos(0),
                 self.app._ed_canvas.winfo_width(),
                 self.app.plot.master.winfo_height())
        self.assertEqual(after, before, "the round trip moved the layout")

    def test_the_left_region_swaps_under_a_shared_files_panel(self):
        self._map()
        files_lb = self.app.files_lb
        self.assertTrue(files_lb.winfo_ismapped())
        self.assertTrue(self.app.traces_lb.winfo_ismapped())
        self.assertFalse(self.app.trace_ws_left.winfo_ismapped())

        self.app.show_workspace("trace")
        self._settle()
        self.assertIs(self.app.files_lb, files_lb)
        self.assertTrue(files_lb.winfo_ismapped(),
                        "the Files panel left the screen with the workspace")
        self.assertFalse(self.app.traces_lb.winfo_ismapped())
        self.assertFalse(self.app._ed_canvas.winfo_ismapped())
        self.assertTrue(self.app.trace_ws_left.winfo_ismapped())
        # Below the Files panel, not beside it or above it.
        self.assertGreaterEqual(self.app.trace_ws_left.winfo_rooty(),
                                files_lb.winfo_rooty() + files_lb.winfo_height())

        self.app.show_workspace("rlc")
        self._settle()
        self.assertIs(self.app.files_lb, files_lb)
        self.assertTrue(files_lb.winfo_ismapped())
        self.assertTrue(self.app.traces_lb.winfo_ismapped())
        self.assertFalse(self.app.trace_ws_left.winfo_ismapped())

    def test_the_trace_containers_are_the_ones_the_panel_stage_builds_into(self):
        self.assertIs(self.app.trace_ws_left.master, self.app._ws_left_host)
        outer, _ = self._outer_and_right()
        self.assertIs(self.app.trace_ws_right.master, outer)
        ws = self.app.workspaces.workspace("trace")
        self.assertIs(ws.left_frame, self.app.trace_ws_left)
        self.assertIs(ws.right_widget, self.app.trace_ws_right)

    def test_the_hooks_fire_in_order(self):
        calls: list[str] = []
        rlc = self.app.workspaces.workspace("rlc")
        trace = self.app.workspaces.workspace("trace")
        rlc.on_leave = lambda: calls.append("leave rlc")
        trace.on_enter = lambda: calls.append("enter trace")
        trace.on_leave = lambda: calls.append("leave trace")
        self.app.show_workspace("trace")
        self.app.show_workspace("trace")          # idempotent: no hooks
        self.app.show_workspace("rlc")
        self.assertEqual(calls, ["leave rlc", "enter trace", "leave trace"])


class TestThePlotKeysSurviveARoundTrip(_AppCase):
    """
    `rejected_ui.md`'s second objection to a notebook beside the plot, met:
    switching away and back hands the plot canvas focus, so M / V / Delete
    still land.  Sent as real Tk key events, not by calling the handler --
    the handler is not what a switch can break, focus is.
    """

    def _axes_center(self):
        widget = self.app.plot.canvas.get_tk_widget()
        ax = self.app.plot.figure.axes[0]
        bb = ax.get_window_extent()
        return (int((bb.x0 + bb.x1) / 2),
                int(widget.winfo_height() - (bb.y0 + bb.y1) / 2))

    def _own_focus(self):
        """
        Make sure the APPLICATION owns the keyboard focus before a key is
        sent; without it Tk routes the key nowhere and the test fails on the
        desktop's state, not on the switch.  Re-acquired through
        `focus -lastfor`, never by forcing a widget of this test's choosing:
        if the switch did not hand focus back to the canvas, -lastfor still
        names the Files list and M still goes nowhere -- the failure stays
        the real one.  Another process on the box can take the focus at any
        moment (the attrib-window suite measured this), hence the retries.
        """
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

    def test_M_V_and_Delete_land_after_switching_away_and_back(self):
        self._load_one_trace()
        self._map()
        # The application must OWN the focus for a key event to be routed
        # at all (the test_multifile_table precedent); then it is moved to
        # the one widget both workspaces share, so that nothing but the
        # switch's own on_enter can bring it back to the canvas.
        self.app.files_lb.focus_set()
        self._settle()
        self._own_focus()
        self.assertEqual(self._send("<Key-m>"), [],
                         "precondition: with the Files list focused, M must "
                         "not reach the plot")

        self.app.show_workspace("trace")
        self._settle()
        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(str(self.app.tk.call("focus", "-lastfor", self.app)),
                         str(self.app.plot.canvas.get_tk_widget()))

        self.assertEqual(self._send("<Key-m>"), ["m"])
        self.assertEqual(self._send("<Key-v>"), ["m", "v"])
        self.assertEqual(len(self.app.plot.view._vline_freqs), 1)
        self.assertEqual(self._send("<Key-Delete>"), ["m"])
        self.assertEqual(self.app.plot.view._vline_freqs, [])
        self.assertEqual(self._send("<Key-Delete>"), [])


class TestTheSessionRoundTrip(_AppCase):

    def test_rlc_at_the_defaults_writes_no_block(self):
        data = self.app._session_dict(None)
        self.assertNotIn("workspaces", data)

    def test_the_active_workspace_round_trips(self):
        self.app.show_workspace("trace")
        data = self.app._session_dict(None)
        self.assertEqual(data["workspaces"],
                         {"version": WORKSPACES_SESSION_VERSION,
                          "active": "trace"})
        self.app.show_workspace("rlc")
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "trace")

    def test_an_old_session_with_no_block_loads_into_rlc(self):
        data = self.app._session_dict(None)
        self.assertNotIn("workspaces", data)
        self.app.show_workspace("trace")
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "rlc")

    def test_a_block_this_build_cannot_read_is_one_log_line(self):
        data = self.app._session_dict(None)
        data["workspaces"] = {"version": 99, "active": "trace"}
        self.app.results_text.delete("1.0", tk.END)
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "rlc")
        log = self.app.results_text.get("1.0", tk.END)
        self.assertIn("Workspaces:", log)
        self.assertIn("99", log)

    def test_a_garbled_block_is_a_note_from_the_reader(self):
        data = self.app._session_dict(None)
        data["workspaces"] = "nope"
        self.app.results_text.delete("1.0", tk.END)
        self.app._apply_session(session_from_dict(data), "test")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "rlc")
        self.assertIn("workspaces", self.app.results_text.get("1.0", tk.END))

    def test_the_autosave_carries_it(self):
        """Through the file, not just the dict: `_write_session` then
        `session_from_dict` on what it wrote."""
        import json
        import tempfile
        self._load_one_trace()
        self.app.show_workspace("trace")
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "s.json")
            self.app._write_session(path, tmp)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(data["workspaces"]["active"], "trace")
        self.assertEqual(session_from_dict(data).workspaces["active"], "trace")


class TestTheRegistrationPoint(_AppCase):
    """Stage 3 adds 'compare' with one call, and nothing else moves."""

    def test_one_register_call_adds_a_workspace_and_its_button(self):
        self._map()
        outer, _ = self._outer_and_right()
        left = ttk.Frame(self.app._ws_left_host)
        right = ttk.Frame(outer)
        ws = self.app.workspaces.register("compare", "Compare files",
                                          left, right)
        self.assertEqual(self.app.workspaces.keys(), ["rlc", "trace", "compare"])
        self.assertEqual(self.app.workspaces.button("compare").cget("text"),
                         "Compare files")
        self.app.show_workspace("compare")
        self._settle()
        self.assertEqual(self.app.workspaces.active, "compare")
        self.assertEqual(outer.panes()[1], str(right))
        self.assertTrue(left.winfo_ismapped())
        self.assertIs(self.app.workspaces.workspace("compare"), ws)
        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(outer.sashpos(0), 460)

    def test_registering_a_key_twice_is_refused(self):
        with self.assertRaises(ValueError):
            self.app.workspaces.register("rlc", "again", ttk.Frame(self.app),
                                         ttk.Frame(self.app))


if __name__ == "__main__":
    unittest.main()
