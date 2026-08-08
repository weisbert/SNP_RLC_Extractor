"""
Auto-apply, the style picker, and per-trace plot visibility.

Three changes that share one theme -- the editor stopped needing a commit step
-- and one hazard: the editor now writes into a TraceConfig from a Tcl variable
trace, so the tests here are mostly about WHEN that write happens and WHICH
trace it lands on, not about what it writes.

  * AUTO-APPLY.  Deferred to after_idle (a synchronous write trace reads the
    placeholder hint as user input), targeted by OBJECT (an index resolved at
    callback time lands the edit on whichever trace is selected by then), and
    flushed before the selection changes.  Each of those has a test that fails
    if the property is removed.
  * STYLE PICKER.  Replaces two integer Spinboxes.  Pinned: it stores indices
    (so pkg_rlc_plot and the golden reference are untouched), it is reachable
    from the keyboard, and it tells the truth about a coupling trace expanding
    into several curves.
  * VISIBILITY.  A hidden trace leaves the plot AND the results table (a row
    for an undrawn curve reads as a duplicate of the drawn one), but it stays
    measured: named on one line under the table and still written to the CSV.
    Toggling must NOT re-run the reduction or destroy the cursors the user
    placed.  The recompute test counts real calls into pkg_rlc_core; the
    cursor test reads the artists off the figure.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_gui  # noqa: E402
import pkg_rlc_plot  # noqa: E402
from pkg_rlc_core import MeasPortRow, parse_touchstone  # noqa: E402
from pkg_rlc_gui import App, FileEntry, TraceConfig  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"


def _ensure_fixtures():
    if not FIXTURE.exists():
        import generate_test_snp
        generate_test_snp.main()


try:
    _root = tk.Tk()
    _root.destroy()
    TK_OK = True
except Exception:                                   # pragma: no cover
    TK_OK = False


class _Case(unittest.TestCase):
    """An App with one file and two traces, the first one selected."""

    MODE = 1

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=self.MODE,
                              port_a="1", gnd_ports="2-4", label="t1")
        self.tc2 = TraceConfig(id=2, file_label=self.fe.label, mode=self.MODE,
                               port_a="2", gnd_ports="1,3,4", label="t2",
                               color_idx=1)
        self.app.traces.extend([self.tc, self.tc2])
        self.app._refresh_trace_list()
        self._select(0)

    def tearDown(self):
        self.app.destroy()

    def _select(self, idx):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()


# ============================================================================
# Auto-apply
# ============================================================================


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestAutoApply(_Case):
    def test_typing_reaches_the_trace_with_no_apply_step(self):
        self.app.ed_porta.set_value("3")
        self._settle()
        self.assertEqual(self.tc.port_a, "3")

    def test_there_is_no_apply_button_left_to_press(self):
        """The change is only real if the old commit step is gone."""
        found = []

        def walk(w):
            try:
                if str(w.cget("text")) == "Apply to Trace":
                    found.append(w)
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)

        walk(self.app)
        self.assertEqual(found, [])
        self.assertFalse(hasattr(self.app, "_on_apply_editor"))

    def test_a_queued_edit_lands_on_the_trace_it_was_typed_into(self):
        """
        THE race this feature can lose silently.

        The sync is deferred, so an edit typed into trace 1 is still queued
        when the user clicks trace 2.  Resolving the target from the Listbox
        when the callback finally runs would write trace 2's freshly loaded
        editor content into trace 2 and drop the edit entirely -- the exact
        loss auto-apply exists to prevent, now rare instead of reliable.
        """
        self.app.ed_porta.set_value("9")        # queued, not yet applied
        self.assertIsNotNone(self.app._ed_sync_after,
                             "nothing was queued -- the test proves nothing")
        self._select(1)                          # click the other trace
        self.assertEqual(self.tc.port_a, "9", "the edit went missing")
        self.assertEqual(self.tc2.port_a, "2", "the edit landed on trace 2")

    def test_the_placeholder_hint_never_reaches_the_trace(self):
        """
        PlaceholderEntry._show_if_empty() sets the variable BEFORE it sets
        _showing, and Tcl runs write traces synchronously inside .set(), so a
        SYNCHRONOUS handler would store the grey hint text as if the user had
        typed it ("e.g.  1  (signal port to drive)" as a port spec).  The
        deferral is what makes get_value() honest by the time it is read.
        """
        self.app.ed_porta.set_value("")          # -> hint is shown
        self._settle()
        self.assertEqual(self.tc.port_a, "")
        self.assertNotIn("e.g", self.tc.port_a)

    def test_a_synchronous_reader_really_would_see_the_placeholder(self):
        """
        The hazard above, demonstrated rather than asserted in a comment.

        If this ever goes green-by-accident (because _show_if_empty started
        setting _showing first), the deferral is no longer load-bearing for
        this reason -- but it still is for the target-capture reason, so do not
        delete it on the strength of that alone.
        """
        seen = []
        self.app.ed_porta.on_change(
            lambda: seen.append(self.app.ed_porta.get_value()))
        self.app.ed_porta.set_value("")      # -> _show_if_empty writes the hint
        self.assertTrue(
            any("e.g" in s for s in seen),
            "a synchronous handler no longer sees the hint; re-read the "
            "comment on _schedule_editor_sync before relying on that")

    def test_selecting_a_trace_does_not_rewrite_it(self):
        """
        _update_mode_visibility() calls set_placeholder() on four entries, and
        each of those writes its variable.  Run outside the suppression guard
        it fires four syncs per selection -- and _sync_editor_to_trace turns an
        empty Label into 'trace_<id>', so merely LOOKING at a trace could
        rename it.
        """
        self.tc2.label = ""
        self._select(1)
        self.assertEqual(self.tc2.label, "",
                         "selecting the trace renamed it")

    def test_a_failing_sync_does_not_escape_the_idle_callback(self):
        """
        _apply_editor_sync runs from after_idle: a raise there reaches no
        handler we control, Tk prints it to stderr, and the GUI carries on
        showing a trace that was silently not updated.
        """
        def boom(_tc):
            raise RuntimeError("editor is unhappy")

        self.app._sync_editor_to_trace = boom
        self.app._schedule_editor_sync()
        self.app._apply_editor_sync()            # must not raise

    def test_the_trace_list_is_not_rebuilt_when_nothing_changed(self):
        """
        Auto-apply refreshes the list on every keystroke, and rebuilding a
        Listbox resets its scroll position -- so a user editing trace 9 of 12
        would be yanked back to the top on every character.
        """
        self.app.traces_lb.yview_moveto(0.0)
        before = self.app._trace_list_shown[:]
        self.app.traces_lb.delete(0, tk.END)     # sabotage: list now empty
        self.app._refresh_trace_list()           # nothing changed -> no rebuild
        self.assertEqual(self.app.traces_lb.size(), 0,
                         "the list was rebuilt even though nothing changed")
        self.assertEqual(self.app._trace_list_shown, before)

    def test_editing_the_spec_marks_the_computed_curve_stale(self):
        self.app._on_calculate()
        self._settle()
        self.assertFalse(self.tc.stale)
        self.app.ed_porta.set_value("3")
        self._settle()
        self.assertTrue(self.tc.stale)
        self.assertIn("*", self.app.traces_lb.get(0))
        self.app._on_calculate()
        self._settle()
        self.assertFalse(self.tc.stale, "Calculate did not clear it")

    def test_changing_only_the_colour_does_not_mark_it_stale(self):
        """Colour is a _draw_signature field: it changes the picture, not the
        numbers, so it must replot rather than flag the curve as out of date."""
        self.app._on_calculate()
        self._settle()
        self.app.ed_style._choose(color=7)
        self._settle()
        self.assertEqual(self.tc.color_idx, 7)
        self.assertFalse(self.tc.stale)

    def test_duplicate_copies_the_edit_still_in_the_editor(self):
        self.app.ed_label.set_value("edited")    # queued
        self.app._on_duplicate_trace()
        self._settle()
        self.assertEqual(self.app.traces[-1].label, "edited_copy")


# ============================================================================
# Style picker
# ============================================================================


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestStylePicker(_Case):
    def test_it_still_stores_plain_indices(self):
        """
        The storage is what keeps pkg_rlc_plot and golden_legacy.npz out of
        this change, and what lets _coupling_plot_traces keep deriving sibling
        colours as (color_idx + n) % len(COLORS).
        """
        self.app.ed_style._choose(color=5, ls=2)
        self._settle()
        self.assertEqual((self.tc.color_idx, self.tc.ls_idx), (5, 2))
        self.assertIsInstance(self.tc.color_idx, int)

    def test_every_palette_cell_is_reachable(self):
        n = len(pkg_rlc_plot.COLORS) + len(pkg_rlc_plot.LINESTYLES)
        self.assertEqual(len(self.app.ed_style._cells), n)

    def test_the_palette_expands_and_collapses(self):
        self.app.ed_style.expand()
        self._settle()
        self.assertEqual(self.app.ed_style._palette.winfo_ismapped(), 1)
        self.app.ed_style.collapse()
        self._settle()
        self.assertEqual(self.app.ed_style._palette.winfo_ismapped(), 0)

    def test_the_preview_is_in_the_tab_order(self):
        """
        A bare tk.Canvas has takefocus='' and Tk's traversal heuristic skips a
        widget with no key bindings, so replacing two Spinboxes with a Canvas
        would have dropped Style out of the keyboard path entirely.
        """
        self.assertTrue(self.app.ed_style._preview.cget("takefocus"))
        self.assertTrue(self.app.ed_style._preview.bind("<Return>"))

    def test_the_preview_admits_a_coupling_trace_is_several_curves(self):
        """
        A mode-6 trace with G measurement ports draws G self curves plus
        G(G-1)/2 mutual ones, each taking the NEXT palette slot.  A preview
        showing one line would be a positive false claim about a trace that
        arrives as six -- and two traces can then collide invisibly.
        """
        self.assertEqual(self.app.ed_style._span, 1)
        self.tc.mode = 6
        # NOT "a"/"b": those are reserved (the legacy alias folds Signal("B")
        # into the minus side of "A"), which is its own bug, not this one.
        self.tc.mports = [MeasPortRow("tank", "1", "2"),
                          MeasPortRow("vco", "3", "4")]
        self._select(0)
        self._settle()
        # 2 self + 1 mutual
        self.assertEqual(self.app.ed_style._span, 3)

    def test_dash_patterns_are_all_distinct(self):
        pats = [pkg_rlc_gui._tk_dash(ls) for ls in pkg_rlc_plot.LINESTYLES]
        self.assertEqual(len(set(pats)), len(pats), pats)

    def test_expanding_the_palette_costs_no_width_in_any_mode(self):
        """
        The editor's column budget is a measured 13 px of headroom (see the
        note beside CONN_TABLE_COLUMNS), so a control that widened the form
        when opened would push the connections table off the right edge in
        exactly the mode that has least room.  Measured: 0 px in all five.

        Height is free -- the form scrolls -- but only if the scrollregion is
        refreshed, which is why StylePicker.toggle() asks for that.
        """
        self.app.deiconify()
        for geom in ("1500x900", "1040x600"):
            for mode in (1, 2, 3, 5, 6):
                with self.subTest(geom=geom, mode=mode):
                    self.app.geometry(geom)
                    self.app.ed_mode_var.set(mode)
                    self.app._on_mode_changed()
                    self._settle()
                    self.app.ed_style.collapse()
                    self._settle()
                    w0 = self.app._ed_form.winfo_reqwidth()
                    h0 = self.app._ed_form.winfo_reqheight()
                    self.app.ed_style.expand()
                    self._settle()
                    self.assertEqual(self.app._ed_form.winfo_reqwidth(), w0,
                                     "the palette widened the form")
                    self.assertGreater(self.app._ed_form.winfo_reqheight(), h0,
                                       "the palette did not open at all")
                    self.app.ed_style.collapse()
                    self._settle()


# ============================================================================
# Plot visibility
# ============================================================================


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestPlotVisibility(_Case):
    def test_toggle_flips_the_flag_the_marker_and_the_colour(self):
        self.app._on_toggle_trace()
        self._settle()
        self.assertFalse(self.tc.enabled)
        self.assertTrue(self.app.traces_lb.get(0).startswith("☐"))
        self.assertEqual(str(self.app.traces_lb.itemcget(0, "foreground")),
                         "#909090")
        self.app._on_toggle_trace()
        self._settle()
        self.assertTrue(self.tc.enabled)
        self.assertTrue(self.app.traces_lb.get(0).startswith("☑"))

    def test_the_marker_glyphs_do_not_shift_the_rest_of_the_line(self):
        """
        Both states must render at the same width or every toggle jitters the
        whole list.  Measured in the Listbox's own font, not assumed.
        """
        import tkinter.font as tkfont
        f = tkfont.Font(font=self.app.traces_lb.cget("font"))
        self.assertEqual(f.measure("☑ "), f.measure("☐ "))

    def test_the_editor_checkbox_and_the_button_agree(self):
        self.app._on_toggle_trace()
        self._settle()
        self.assertFalse(bool(self.app.ed_enabled_var.get()))
        self.app.ed_enabled_var.set(True)
        self.app._on_enabled_toggled()
        self._settle()
        self.assertTrue(self.tc.enabled)

    def test_space_on_the_trace_list_toggles(self):
        self.assertTrue(self.app.traces_lb.bind("<space>"))
        self.app._on_toggle_trace_key()
        self.assertFalse(self.tc.enabled)

    def test_a_hidden_trace_is_off_the_plot_and_off_the_table(self):
        """
        The table is read as "what is on the plot".  A row for a curve that is
        not drawn reads as a duplicate of the one that is -- which is exactly
        how a hidden trace normally comes about (Duplicate, then hide the
        copy), so the two rows really are near-identical.
        """
        self.app._on_calculate()
        self._settle()
        n_all = len(self.app.plot.view.traces)
        # Hide the trace that is NOT selected: the editor owns the selected
        # one, so Calculate's pre-sync would write its checkbox back over a
        # field poked directly here.
        self.tc2.enabled = False
        mark = self.app.results_text.index(tk.END)
        self.app._on_calculate()
        self._settle()
        self.assertEqual(len(self.app.plot.view.traces), n_all - 1)
        body = self.app.results_text.get(mark, tk.END)
        self.assertNotIn(f"[{self.tc2.id:>2}] {self.tc2.label}", body,
                         "the hidden trace still has a table row")
        self.assertIn(f"[{self.tc.id:>2}] {self.tc.label}", body,
                      "the visible trace lost its row")
        # Collection is unchanged -- only the rendering filters -- so a units
        # re-render follows the visibility as it stands then.
        self.assertEqual(len(self.app._last_result_rows), 2)

    def test_a_hidden_trace_is_named_under_the_table(self):
        """
        Not silently dropped: it WAS measured and it IS in the CSV, so a report
        that just omits it leaves a trace missing with no explanation.
        """
        self.tc2.enabled = False
        mark = self.app.results_text.index(tk.END)
        self.app._on_calculate()
        self._settle()
        body = self.app.results_text.get(mark, tk.END)
        self.assertIn("hidden (measured, not plotted", body)
        self.assertIn(f"[{self.tc2.id}] {self.tc2.label}", body)

    def test_a_hidden_trace_takes_its_fit_summary_with_it(self):
        """A fit line under a table that has no such row is an orphan."""
        self.app.fit_model_var.set("inductor")
        self.tc2.enabled = False
        mark = self.app.results_text.index(tk.END)
        self.app._on_calculate()
        self._settle()
        body = self.app.results_text.get(mark, tk.END)
        self.assertIn(f"fit[{self.tc.id} ", body, "the visible fit is missing")
        self.assertNotIn(f"fit[{self.tc2.id} ", body,
                         "the hidden trace still printed a fit summary")

    def test_hiding_replots_without_recomputing(self):
        """
        The point of the feature.  Recomputing to REMOVE a curve is seconds of
        Schur reduction on a 153-port package file to arrive at numbers that
        did not change -- and it would also mean the toggle needs a Calculate
        press to take effect at all.
        """
        self.app._on_calculate()
        self._settle()
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_toggle_trace()
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(calls, [], "toggling re-ran the reduction")
        self.assertEqual(len(self.app.plot.view.traces), 1,
                         "the plot did not follow the toggle")

    def test_toggling_keeps_the_cursors_the_user_placed(self):
        """
        set_traces() clears _anno_stack and _vline_freqs, and redraw() clears
        them again.  Toggling is a one-click gesture, so without keep_cursors
        every tidy-up of the view destroys the V lines the comparison was set
        up to make.
        """
        self.app._on_calculate()
        self._settle()
        view = self.app.plot.view
        view._place_vline(2e9)
        view._place_vline(4e9)
        view._refresh_marker()
        self.assertEqual(len(view._vline_freqs), 2)

        self.app._on_toggle_trace()
        self._settle()
        self.assertEqual(view._vline_freqs, [2e9, 4e9])
        self.assertEqual(len([k for k, _a in view._anno_stack if k == "v"]), 2)

    def test_a_full_calculate_still_clears_them(self):
        """The opposite half of the same rule: new numbers, no stale cursor."""
        self.app._on_calculate()
        self._settle()
        self.app.plot.view._place_vline(2e9)
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.app.plot.view._vline_freqs, [])

    def test_hiding_everything_says_so(self):
        self.app._on_toggle_trace()         # the selected one, through the UI
        self.tc2.enabled = False
        self.app._on_calculate()
        self._settle()
        body = self.app.results_text.get("1.0", tk.END)
        self.assertIn("the plot is empty on purpose", body)

    def test_show_hide_button_is_on_screen_at_the_minsize(self):
        """
        FOURTH button in the Traces row, and pack unmaps from the END.
        Measured: the row is 448 px and four buttons ask 364 (three ask 273).
        """
        self.app.deiconify()
        self.app.geometry("1040x600")
        self._settle()
        btns = [w for w in self.app.traces_lb.master.winfo_children()[0]
                .winfo_children()]
        self.assertEqual([str(b.cget("text")) for b in btns],
                         ["Add Trace", "Remove", "Duplicate", "Show/Hide"])
        for b in btns:
            self.assertEqual(b.winfo_ismapped(), 1, str(b.cget("text")))


# ============================================================================
# Calculate This Trace
# ============================================================================


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestCalculateOneTrace(_Case):
    def test_it_computes_only_the_selected_trace(self):
        self.app._on_calculate()
        self._settle()
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_calculate_selected()
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(len(calls), 1, "it recomputed more than the selection")

    def test_it_narrows_the_work_not_the_report(self):
        """
        A table that shrank to one row would make the fast path look like it
        had thrown the other traces away.
        """
        self.app._on_calculate()
        self._settle()
        self.app._on_calculate_selected()
        self._settle()
        self.assertEqual(len(self.app._last_result_rows), 2)

    def test_it_keeps_the_cursors(self):
        """This is the fast iteration loop; the cursors are what it is for."""
        self.app._on_calculate()
        self._settle()
        self.app.plot.view._place_vline(3e9)
        self.app._on_calculate_selected()
        self._settle()
        self.assertEqual(self.app.plot.view._vline_freqs, [3e9])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestBothCurveKindsUnchecked(_Case):
    """
    The "both 'self' and 'mutual' are unchecked" note.

    It used to be spelled `if not self._coupling_plot_traces(...)` -- i.e. the
    curves were built, tested for emptiness and thrown away, which recomputed
    _coupling_k_array over every frequency for every pair a moment before
    _replot_from_cache built them for real.  It is now the emptiness CONDITION,
    so it needs a test that the condition still matches the function.
    """

    MODE = 6

    def setUp(self):
        super().setUp()
        self.tc.mports = [MeasPortRow("tank", "1", "2"),
                          MeasPortRow("vco", "3", "4")]
        # The base case grounds 2-4, and build_terminations_coupling refuses a
        # port that is both a probe side and a GND port -- grounding one side
        # grounds the whole side.
        self.tc.gnd_ports = ""
        self._select(0)

    def _note_shown(self, self_on, mutual_on):
        self.app.ed_plot_self_var.set(self_on)
        self.app.ed_plot_mutual_var.set(mutual_on)
        self._settle()
        self.app.results_text.delete("1.0", tk.END)
        self.app._on_calculate()
        self._settle()
        body = self.app.results_text.get("1.0", tk.END)
        curves = self.app._coupling_plot_traces(
            self.tc, self.fe, self.tc.Zmat, self.tc.mport_names)
        return ("both 'self' and 'mutual' are unchecked" in body), len(curves)

    def test_the_note_matches_what_is_actually_drawn(self):
        for self_on, mutual_on in ((True, True), (True, False),
                                   (False, True), (False, False)):
            with self.subTest(self_on=self_on, mutual=mutual_on):
                noted, n = self._note_shown(self_on, mutual_on)
                self.assertEqual(noted, n == 0,
                                 f"note={noted} but {n} curves were built")

    def test_a_single_measurement_port_has_no_mutual_curve_to_draw(self):
        """
        Mode 6 takes the coupling path even with ONE measurement port, and
        there is no pair, so 'mutual' alone draws nothing.  Without the
        `len(names) >= 2` half of the condition the note is suppressed and the
        user is left with an empty subplot and no explanation.  (The G=2 cases
        above cannot see that half at all -- caught by mutation.)
        """
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self._select(0)
        noted, n = self._note_shown(False, True)
        self.assertEqual(n, 0, "a lone measurement port produced a pair curve")
        self.assertTrue(noted, "nothing was drawn and nothing was said")

    def test_unchecking_both_empties_the_plot_for_that_trace(self):
        self._note_shown(True, True)
        self.assertGreater(len(self.app.plot.view.traces), 0)
        self._note_shown(False, False)
        self.assertEqual(len(self.app.plot.view.traces), 0)


if __name__ == "__main__":
    unittest.main()
