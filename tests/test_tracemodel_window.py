"""
The Trace Model window: `pkg_rlc.panels.tracemodel_gui`.

TWO HALVES, the split `tests/test_attrib_window.py` established.

  PURE -- refusal, end resolution, staleness, the header strip.  No Tk root,
  no App, no file, no solve; it runs off fakes in milliseconds and it is where
  a wrong DECISION gets caught.

  TK -- a real `App` with a real Calculate behind it, measured off a mapped
  window.  It is where a wrong WIRE gets caught: the module imported fine, the
  layering gate was green and every pure test passed while
  `compute_trace_model` still reached for `file_entry.data`, an attribute
  `FileEntry` does not have.  Nothing but driving the real App found that.

This module is deliberately NOT in `FAST_MODULES`: it imports tkinter, which
is the one property that list is about.  The geometry of the drawing is tested
without a display in `tests/test_tracemodel.py`, which IS in it.
"""

from __future__ import annotations

import math
import pathlib
import tkinter as tk
import unittest

import numpy as np
from unittest import mock

import pkg_rlc.panels.tracemodel_gui as tmg
from pkg_rlc.physics import tracemodel as tmod

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                       # noqa: BLE001
        return False


TK_OK = _tk_ok()


# ============================================================================
# Fakes, so the pure half needs neither a file nor a solve
# ============================================================================

class _Row:
    """Shaped like a MeasPortRow and deliberately not one."""

    def __init__(self, name="", plus="", minus=""):
        self.name, self.plus, self.minus = name, plus, minus

    def is_blank(self) -> bool:
        return not (self.name.strip() or self.plus.strip()
                    or self.minus.strip())


class _Trace:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.label = kw.get("label", "t")
        self.file_label = kw.get("file_label", "f.s3p")
        self.stale = kw.get("stale", False)
        self.frozen = kw.get("frozen", False)
        self.Zmat = kw.get("Zmat", np.zeros((3, 2, 2), dtype=complex))
        self.mport_names = kw.get("mport_names", ["in", "out"])
        self.mports = kw.get("mports", [_Row("in", "1", ""),
                                        _Row("out", "2", "")])


def _pi_Z(Rs, Ls, C1, R1, C2, R2, f):
    w = 2 * math.pi * f
    ys = 1.0 / complex(Rs, w * Ls)
    y1 = 1.0 / complex(R1, -1.0 / (w * C1))
    y2 = 1.0 / complex(R2, -1.0 / (w * C2))
    return np.linalg.inv(np.array([[ys + y1, -ys], [-ys, ys + y2]],
                                  dtype=complex))


def _result(**kw) -> tmg.TraceModelResult:
    m = tmod.extract_pi(_pi_Z(344, 1.69e-9, 33.2e-15, 90.0,
                              33.2e-15, 90.0, 1e8), 1e8, "in", "out",
                        kw.pop("differential", False))
    return tmg.TraceModelResult(
        model=m, reference=m, mode_conversion=kw.pop("mc", None),
        port_note="", freq_snap=None, trace_id=1, trace_label="t",
        file_label="f.s3p", run_number=kw.pop("run_number", 3),
        signature=kw.pop("signature", ("sig",)))


# ============================================================================
# PURE
# ============================================================================

class TestResolveEnds(unittest.TestCase):
    """A pi has two nodes, so two measurement ports need no picker."""

    def test_two_single_ended_ports_are_the_two_ends(self):
        self.assertEqual(tmg.resolve_ends(_Trace()), (0, 1, False))

    def test_a_minus_side_on_either_port_makes_it_differential(self):
        for rows in ([_Row("in", "1", "2"), _Row("out", "3", "")],
                     [_Row("in", "1", ""), _Row("out", "3", "4")],
                     [_Row("in", "1", "2"), _Row("out", "3", "4")]):
            with self.subTest(rows=[r.minus for r in rows]):
                t = _Trace(mports=rows, mport_names=["in", "out"])
                self.assertEqual(tmg.resolve_ends(t), (0, 1, True))

    def test_anything_but_two_ports_is_not_a_trace(self):
        for names in ([], ["only"], ["a", "b", "c"]):
            with self.subTest(names=names):
                self.assertIsNone(
                    tmg.resolve_ends(_Trace(mport_names=names)))


class TestRefusal(unittest.TestCase):
    """
    Every reason names the FIX, not the symptom.

    The order matters as much as the set: a trace with no Zmat AND three
    measurement ports should be told to press Calculate in Mode 6 first, not
    handed a lecture about the number of ports it will need afterwards.
    """

    def test_no_trace_asks_for_a_selection(self):
        why = tmg.trace_model_refusal(None, object())
        self.assertIn("Select a trace", why)

    def test_a_missing_file_names_the_label(self):
        why = tmg.trace_model_refusal(_Trace(file_label="gone.s3p"), None)
        self.assertIn("gone.s3p", why)

    def test_no_port_matrix_points_at_mode_6_and_calculate(self):
        why = tmg.trace_model_refusal(_Trace(Zmat=None), object())
        self.assertIn("Mode 6", why)
        self.assertIn("Calculate", why)

    def test_the_wrong_number_of_ports_says_how_many_there_are(self):
        why = tmg.trace_model_refusal(
            _Trace(mport_names=["a", "b", "c"]), object())
        self.assertIn("3 measurement port", why)
        self.assertIn("exactly two", why)

    def test_a_stale_trace_is_refused_before_it_is_drawn(self):
        """
        The cached matrix is not what the spec now says.

        Drawing it would be a circuit that answers a question nobody asked --
        the exact failure the `stale` flag exists for.
        """
        why = tmg.trace_model_refusal(_Trace(stale=True), object())
        self.assertIn("edited since", why)

    def test_a_good_trace_is_not_refused(self):
        self.assertIsNone(tmg.trace_model_refusal(_Trace(), object()))

    def test_no_port_matrix_beats_the_port_count(self):
        why = tmg.trace_model_refusal(
            _Trace(Zmat=None, mport_names=["a", "b", "c"]), object())
        self.assertIn("Mode 6", why)
        self.assertNotIn("exactly two", why)


class TestStaleness(unittest.TestCase):
    """
    A result that WAS right and is not any more looks exactly like one that
    still is. That is the whole reason for a banner.
    """

    def test_a_removed_subject_is_a_warning(self):
        text, warn = tmg.staleness_text(_result(), _Trace(), exists=False)
        self.assertTrue(warn)
        self.assertIn("removed", text)

    def test_an_edited_spec_is_a_warning_naming_recompute(self):
        # `spec_signature` is patched rather than fed a fake with every field
        # `_config_signature` reads: what is under test is the COMPARISON,
        # and a fake that has to track that function is a fake that rots.
        with mock.patch.object(tmg, "spec_signature", lambda _t: ("new",)):
            text, warn = tmg.staleness_text(_result(signature=("old",)),
                                            _Trace(), exists=True)
        self.assertTrue(warn)
        self.assertIn("Recompute", text)

    def test_agreement_is_not_a_warning_and_names_the_run(self):
        with mock.patch.object(tmg, "spec_signature", lambda _t: ("same",)):
            text, warn = tmg.staleness_text(
                _result(run_number=7, signature=("same",)),
                _Trace(), exists=True)
        self.assertFalse(warn)
        self.assertIn("#7", text)

    def test_a_trace_awaiting_recalculation_is_a_warning(self):
        with mock.patch.object(tmg, "spec_signature", lambda _t: ("same",)):
            text, warn = tmg.staleness_text(
                _result(signature=("same",)), _Trace(stale=True), exists=True)
        self.assertTrue(warn)
        self.assertIn("Calculate", text)


class TestHeader(unittest.TestCase):
    def test_it_names_the_trace_the_file_and_the_kind(self):
        txt = tmg.header_text(_result())
        for want in ("[1]", "t", "f.s3p", "single-ended", "in", "out"):
            self.assertIn(want, txt)

    def test_a_differential_model_says_so(self):
        self.assertIn("differential",
                      tmg.header_text(_result(differential=True)))


class TestLabels(unittest.TestCase):
    def test_the_menu_label_uses_a_real_ellipsis(self):
        """Three full stops would measure and sort differently beside
        `Attribution…`, which is the entry directly above it."""
        from pkg_rlc.panels.attrib_gui import ATTRIB_MENU_LABEL
        self.assertTrue(tmg.TRACE_MODEL_MENU_LABEL.endswith("…"))
        self.assertTrue(ATTRIB_MENU_LABEL.endswith("…"))


# ============================================================================
# TK -- the real App, a real Calculate, a mapped window
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _WindowCase(unittest.TestCase):
    FIXTURE = "pi_2port.s2p"
    ROWS = (("in", "1", ""), ("out", "2", ""))
    GND = ""

    def setUp(self):
        from pkg_rlc.frontend.app import App
        from pkg_rlc.model.trace import FileEntry, TraceConfig
        from pkg_rlc.physics.core import parse_touchstone
        from pkg_rlc.physics.spec import MeasPortRow

        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURES / self.FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(
            id=1, file_label=self.fe.label, mode=6, label="trace",
            gnd_ports=self.GND,
            mports=[MeasPortRow(*r) for r in self.ROWS])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app.rlc_freq_var.set("1.0")
        self.app._on_calculate()
        self.app.update()

    def tearDown(self):
        try:
            for w in tmg.live_windows(self.app):
                w.destroy()
            self.app.update()
            self.app.destroy()
        except Exception:                                   # noqa: BLE001
            pass

    def _open(self):
        win = tmg.open_trace_model_window(self.app, self.tc)
        self.assertIsNotNone(win, "the window refused a good trace")
        win.update_idletasks()
        win.update()
        return win


class TestTheWindowOpensOnARealTrace(_WindowCase):

    def test_the_fixture_values_reach_the_drawing(self):
        """
        `pi_2port.s2p` carries R_series = 1.0, L_series = 1e-9 and
        C_shunt_each_port = 1e-15 in its own header. Those are what has to
        appear ON THE CANVAS -- the whole stack, in one assertion.
        """
        win = self._open()
        texts = [win._canvas.itemcget(i, "text")
                 for i in win._canvas.find_all()
                 if win._canvas.type(i) == "text"]
        self.assertIn("R = 1 Ω", texts)
        self.assertIn("L = 1 nH", texts)
        self.assertEqual(texts.count("C = 1 fF"), 2)

    def test_something_is_actually_drawn(self):
        win = self._open()
        self.assertGreater(len(win._canvas.find_all()), 10)

    def test_the_single_ended_drawing_has_a_ground_and_a_reference_label(self):
        win = self._open()
        texts = [win._canvas.itemcget(i, "text")
                 for i in win._canvas.find_all()
                 if win._canvas.type(i) == "text"]
        self.assertIn("reference", texts)

    def test_the_text_panel_carries_the_verdicts_and_the_lumped_check(self):
        win = self._open()
        body = win._text.get("1.0", tk.END)
        self.assertIn("|Q|", body)
        self.assertIn("lumped check", body)
        self.assertIn("read C", body)

    def test_the_text_panel_does_NOT_repeat_the_ascii_drawing(self):
        """
        The window draws on a Canvas. Printing the picture a second time in a
        second notation underneath it is how the two start disagreeing.
        """
        body = self._open()._text.get("1.0", tk.END)
        self.assertNotIn("*---", body)
        self.assertNotIn("──", body)

    def test_the_banner_says_it_agrees_with_the_live_trace(self):
        win = self._open()
        self.assertIn("has not moved", win._banner.cget("text"))

    def test_a_second_open_raises_the_same_window(self):
        """Two windows on one trace are two copies of one answer."""
        a = self._open()
        b = tmg.open_trace_model_window(self.app, self.tc)
        self.assertIs(a, b)
        self.assertEqual(len(tmg.live_windows(self.app)), 1)

    def test_destroying_it_deregisters_it(self):
        win = self._open()
        self.assertEqual(len(tmg.live_windows(self.app)), 1)
        win.destroy()
        self.app.update()
        self.assertEqual(len(tmg.live_windows(self.app)), 0)

    def test_the_refresh_hook_never_raises_and_updates_the_banner(self):
        win = self._open()
        self.tc.stale = True
        tmg.refresh_trace_model_windows(self.app)
        self.assertIn("not been recalculated", win._banner.cget("text"))
        tmg.refresh_trace_model_windows(self.app, rerender=True)

    def test_resizing_redraws_without_losing_a_value(self):
        win = self._open()
        before = len(win._canvas.find_all())
        win.geometry("1100x760")
        win.update_idletasks()
        win.update()
        win._draw()
        self.assertEqual(len(win._canvas.find_all()), before)


class TestTheDifferentialWindow(_WindowCase):
    FIXTURE = "diff_pair_4port.s4p"
    ROWS = (("in", "1", "2"), ("out", "3", "4"))

    def test_the_loop_inductance_from_the_fixture_header_is_drawn(self):
        """`diff_pair_4port.s4p` says L_loop = 8e-9."""
        win = self._open()
        texts = [win._canvas.itemcget(i, "text")
                 for i in win._canvas.find_all()
                 if win._canvas.type(i) == "text"]
        self.assertIn("L = 8 nH", texts)

    def test_both_capacitance_conventions_are_on_the_drawing(self):
        """
        C across the pair AND the per-line odd-mode restatement, because a
        bare C on a differential pi is ambiguous by a factor of two.
        """
        win = self._open()
        texts = [win._canvas.itemcget(i, "text")
                 for i in win._canvas.find_all()
                 if win._canvas.type(i) == "text"]
        self.assertEqual(texts.count("C = 0.5 fF"), 2)
        self.assertEqual(texts.count("(odd 1 fF)"), 2)

    def test_there_is_NO_ground_under_a_differential_pi(self):
        """Drawing a ground rail here is a lie that reads as a diagram."""
        win = self._open()
        texts = [win._canvas.itemcget(i, "text")
                 for i in win._canvas.find_all()
                 if win._canvas.type(i) == "text"]
        self.assertNotIn("reference", texts)
        self.assertTrue(any("across the pair" in t for t in texts))

    def test_the_imbalance_is_measured_and_reported(self):
        body = self._open()._text.get("1.0", tk.END)
        self.assertIn("mode conversion", body)
        self.assertIn("balanced", body)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheRoutesIn(_WindowCase):
    """Both menu routes exist, neither is ever greyed, and they agree."""

    def test_the_analyze_menu_carries_the_entry(self):
        labels = []
        m = self.app._analyze_menu
        for i in range(m.index("end") + 1):
            try:
                labels.append(str(m.entrycget(i, "label")))
            except Exception:                               # noqa: BLE001
                labels.append("")
        self.assertIn(tmg.TRACE_MODEL_MENU_LABEL, labels)

    def test_the_traces_right_click_menu_carries_it_too(self):
        self.app._sync_trace_menu(self.tc)
        m = self.app._trace_menu
        labels = [str(m.entrycget(i, "label"))
                  for i in range(m.index("end") + 1)]
        self.assertIn(tmg.TRACE_MODEL_MENU_LABEL, labels)

    def test_it_is_never_greyed_out_frozen_or_not(self):
        """
        The window explains its own five refusals by name; a disabled menu
        entry explains none of them. Same call as the Attribution entry, and
        checked on a FROZEN trace too -- freezing greys `Freeze as new trace`
        and must not touch this one.
        """
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                self.tc.frozen = frozen
                self.app._sync_trace_menu(self.tc)
                self.assertEqual(
                    str(self.app._trace_menu.entrycget(
                        tmg.TRACE_MODEL_MENU_LABEL, "state")), "normal")
        self.tc.frozen = False

    def test_the_app_handler_opens_it_on_the_selected_trace(self):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_model()
        self.app.update()
        wins = tmg.live_windows(self.app)
        self.assertEqual(len(wins), 1)
        self.assertIs(wins[0]._trace, self.tc)


if __name__ == "__main__":
    unittest.main()
