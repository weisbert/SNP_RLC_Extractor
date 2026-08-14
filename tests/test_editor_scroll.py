"""
The editor keeps the reader's place when the trace changes, and only then.

The editor form is taller than its viewport in every mode that has a table --
measured here at 1500x900, a mode-5 form is 728 px against a 345 px canvas --
so the connections table lives BELOW THE FOLD and reaching it is a scroll the
reader performs on purpose.  `_update_mode_visibility` ended by resetting that
scroll unconditionally, and it runs on every TRACE SELECTION as well as on a
mode change: so clicking the other trace to compare two specs -- the whole
reason for having two traces -- put the reader back at the top of the form with
the table they were reading no longer on screen.  It was reported as the
Connections table "disappearing from the GUI" while "the calculation is still
fine", which is exactly right: nothing was hidden, nothing was lost, and the
spec still computed.  Only the viewport moved.

The reset itself is NOT wrong, it was only too broadly applied: on a real mode
change the fields the view is scrolled past have been replaced, and a now-short
form must not stay parked out of sight.  `test_a_MODE_change_still_resets` is
the guard on that half, and it is the half a careless fix removes.

This is a separate module rather than another class in test_mode5_editor.py
because it is about the CANVAS OFFSET across a selection, not about what mode 5
renders; the layout tests there already own `winfo_ismapped` and the width
budget.  Every guard below was mutation-checked against
`_refresh_editor_scrollregion(preserve=False)` restored unconditionally.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    parse_touchstone,
)
from pkg_rlc.frontend.app import App, FileEntry, TraceConfig  # noqa: E402

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
        r = tk.Tk()
    except Exception:
        return False
    r.destroy()
    return True


TK_OK = _tk_available()


# The reported case: a nine-row trace and a two-row trace on one screen.  The
# row counts differ on purpose -- the two forms are different heights, which is
# what makes "preserve the offset" a question rather than a no-op.
_MANY_ROWS = [
    ConnectionRow(kind="rlc_between", ports="1", to="2", R="0.8"),
    ConnectionRow(kind="short", ports="3,4", net="VSS_A"),
    ConnectionRow(kind="rlc_gnd", ports="3", R="0.08", L="50p"),
    ConnectionRow(kind="rlc_gnd", ports="4", R="0.08", L="50p"),
    ConnectionRow(kind="ground", ports="2"),
    ConnectionRow(kind="short", ports="1,2", net="VSS_B"),
    ConnectionRow(kind="rlc_gnd", ports="1", R="0.08", L="50p"),
    ConnectionRow(kind="ground", ports="3"),
    ConnectionRow(kind="short", ports="2,3"),
]
_FEW_ROWS = [
    ConnectionRow(kind="short", ports="1,2", net="IND_con"),
    ConnectionRow(kind="short", ports="3,4", net="rfin_out"),
]


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheEditorKeepsTheReadersPlace(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        # MAPPED, not withdrawn: every number here is pixel geometry, and a
        # withdrawn root reports a 1x1 canvas whatever the layout is -- the
        # yview assertions would then hold for the wrong reason.
        self.app.geometry("1500x900")
        self.app.deiconify()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        mports = [MeasPortRow("agg", "1", "2"), MeasPortRow("vic", "3", "4")]
        self.big = TraceConfig(id=1, file_label=self.fe.label, mode=5,
                               label="many", mports=list(mports),
                               conn_rows=list(_MANY_ROWS))
        self.small = TraceConfig(id=2, file_label=self.fe.label, mode=5,
                                 label="few", mports=list(mports),
                                 conn_rows=list(_FEW_ROWS))
        self.app.traces.extend([self.big, self.small])
        self.app._refresh_trace_list()
        self._select(0)

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=8):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _select(self, idx):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _visible_px_of_conn_table(self) -> int:
        t, cv = self.app.ed_conn_table, self.app._ed_canvas
        ty, th = t.winfo_rooty(), t.winfo_height()
        cy, ch = cv.winfo_rooty(), cv.winfo_height()
        return max(0, min(ty + th, cy + ch) - max(ty, cy))

    def _scroll_to_conn_table(self) -> float:
        """Do what the reader does: scroll down until the table is on screen."""
        cv = self.app._ed_canvas
        for step in range(41):
            cv.yview_moveto(step / 40.0)
            self._settle()
            if self._visible_px_of_conn_table() >= self.app.ed_conn_table \
                    .winfo_height():
                return cv.yview()[0]
        self.fail("the connections table was never fully on screen at 1500x900 "
                  "-- the precondition for every test in this class")

    # -- the precondition, asserted rather than assumed ---------------------

    def test_the_connections_table_really_is_below_the_fold(self):
        # Without this the whole class passes vacuously on a form that happens
        # to fit: there would be no scroll to lose.
        self.assertGreater(self.app._ed_form.winfo_reqheight(),
                           self.app._ed_canvas.winfo_height(),
                           "the mode-5 form must not fit its viewport")
        self.app._ed_canvas.yview_moveto(0.0)
        self._settle()
        self.assertEqual(self._visible_px_of_conn_table(), 0,
                         "at the top of the form the table must be off screen")

    # -- the defect -----------------------------------------------------------

    def test_switching_TRACE_keeps_the_offset(self):
        # What is preserved is the FRACTION, which is what the canvas stores
        # and what `preserve=True` has always re-applied.  Across two forms of
        # different heights that is not the same as the same pixel: measured
        # here, 0.3007 of a 728 px form (top pixel 219) comes back as 0.3014 of
        # a 574 px one (top pixel 173), i.e. the content shifts 46 px.  The
        # tolerance is on the fraction because the fraction is the mechanism;
        # the property that matters is asserted in the next test, and both are
        # needed -- a fix that pinned only the number could satisfy it by not
        # scrolling anywhere useful.
        at = self._scroll_to_conn_table()
        self.assertGreater(at, 0.0)
        self._select(1)
        self.assertAlmostEqual(self.app._ed_canvas.yview()[0], at, delta=0.01)

    def test_switching_TRACE_keeps_the_connections_table_on_screen(self):
        # The offset is the mechanism; THIS is the thing the reader reported,
        # and it is the assertion to keep if the mechanism is ever replaced.
        self._scroll_to_conn_table()
        h = self.app.ed_conn_table.winfo_height()
        self._select(1)
        self.assertGreater(
            self._visible_px_of_conn_table(), 0,
            "the connections table left the screen when the trace changed")
        self.assertGreaterEqual(
            self._visible_px_of_conn_table(),
            min(h, self.app.ed_conn_table.winfo_height()) // 2,
            "less than half the table survived the switch")

    def test_it_survives_switching_BACK_AND_FORTH(self):
        at = self._scroll_to_conn_table()
        for idx in (1, 0, 1, 0):
            self._select(idx)
            self.assertAlmostEqual(self.app._ed_canvas.yview()[0], at,
                                   delta=0.01, msg=f"lost the place at {idx}")
            self.assertGreater(self._visible_px_of_conn_table(), 0)

    def test_the_SHORTER_form_clamps_instead_of_parking_past_its_end(self):
        # Why preserving is safe now and was not when the reset was written:
        # _apply_editor_scrollregion re-measures the scrollregion BEFORE it
        # re-applies the offset.  Scrolled to the very bottom of the nine-row
        # form, the two-row form is ~150 px shorter and must still show content.
        self.app._ed_canvas.yview_moveto(1.0)
        self._settle()
        self._select(1)
        cv = self.app._ed_canvas
        first, last = cv.yview()
        self.assertLessEqual(last, 1.0 + 1e-9)
        self.assertLess(first, last, "the viewport shows nothing at all")
        _x0, _y0, _x1, y1 = [float(v) for v in
                             cv.cget("scrollregion").split()]
        self.assertGreaterEqual(y1 + 1, self.app._ed_form.winfo_reqheight(),
                                "the scrollregion is short of the form")

    # -- the half a careless fix removes -------------------------------------

    def test_a_MODE_change_still_resets(self):
        # test_mode5_editor.py::test_switching_from_mode5_to_mode1_resets_the
        # _scroll is the other guard on this; it is repeated here because the
        # change that breaks it is the change this module is about.
        self._scroll_to_conn_table()
        self.assertGreater(self.app._ed_canvas.yview()[0], 0.0)
        self.app.ed_mode_var.set(1)
        self.app._on_mode_changed()
        self._settle()
        self.assertEqual(self.app._ed_canvas.yview()[0], 0.0)

    def test_a_mode_change_resets_even_when_the_trace_changed_too(self):
        # Selecting a trace whose mode differs is ONE call to
        # _update_mode_visibility that is both -- and the mode is what decides.
        self.small.mode = 1
        self._scroll_to_conn_table()
        self._select(1)
        self.assertEqual(self.app._ed_canvas.yview()[0], 0.0)


if __name__ == "__main__":
    unittest.main()
