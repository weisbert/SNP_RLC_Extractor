"""
Stage C: the Results pane is a ttk.Notebook whose first tab is the Log.

This is a REFACTOR with one visible addition, so the tests are shaped around
what must NOT have changed as much as around what was added:

  * results_text is the same persistent ScrolledText it always was, it is tab
    0, and that tab is SELECTED at startup.  Both matter mechanically, not
    aesthetically: six tests elsewhere take an index(END) mark, run Calculate
    and read back from it (a fresh widget returns '' for a stale mark), and
    test_session's Ctrl+O guard focuses results_text and synthesises a key
    event -- both focus_set and event_generate are NO-OPS on an unmapped
    widget, and a non-selected tab's widget is unmapped.
  * A tab that is not on screen still accepts insert/get/see, so warnings
    could now be written and never seen.  The Log tab's label therefore
    carries a badge of UNSEEN warnings, and an ERROR pulls the Log to the
    front outright.
  * The badge must be WIDTH-STABLE.  The Log is the leftmost tab, so a label
    that changes width reflows every tab to its right.

Pure string properties get pure tests; everything else is measured off a real
Tk widget and skips cleanly with no display.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import pkg_rlc.frontend.app as pkg_rlc_gui  # noqa: E402
from pkg_rlc.physics.core import parse_touchstone  # noqa: E402
from pkg_rlc.frontend.app import (  # noqa: E402
    LOG_BADGE_CAP,
    LOG_ERROR,
    LOG_INFO,
    LOG_WARN,
    App,
    FileEntry,
    TraceConfig,
    log_tab_label,
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
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


# ============================================================================
# The badge label (pure)
# ============================================================================

class TestLogTabLabelText(unittest.TestCase):
    """
    The shape of the label, independent of any font.

    The count is zero-padded to two digits and shown even when it is zero
    because that is the ONLY way the two states can measure the same width --
    see TestLogTabLabelWidth for the pixels that force it.
    """

    def test_a_clean_log_carries_no_marker(self):
        self.assertEqual(log_tab_label(0), "Log  00")
        self.assertNotIn("!", log_tab_label(0))

    def test_a_warning_carries_the_marker_and_the_count(self):
        self.assertEqual(log_tab_label(3), "Log !03")
        self.assertEqual(log_tab_label(27), "Log !27")

    def test_the_count_is_capped_rather_than_widening_the_tab(self):
        self.assertEqual(log_tab_label(LOG_BADGE_CAP), "Log !99")
        self.assertEqual(log_tab_label(1000), "Log !99")

    def test_every_form_is_the_same_number_of_characters(self):
        lens = {len(log_tab_label(n)) for n in range(0, LOG_BADGE_CAP + 5)}
        self.assertEqual(lens, {7})

    def test_a_negative_count_is_a_clean_log_not_a_crash(self):
        self.assertEqual(log_tab_label(-2), log_tab_label(0))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestLogTabLabelWidth(unittest.TestCase):
    """
    Measured in the tab strip's OWN font, the way the ☑/☐ prefix and the
    results swatch were measured.

    In TkDefaultFont (Microsoft YaHei UI 9, what the vista theme's
    TNotebook.Tab uses): ' ' and '!' are both 4 px and every digit is 7 px, so
    every form of the label is 44 px.  A digit-free "Log" is 22 px and cannot
    be padded to 44 with spaces at all -- 22 + 4a == 37 + 4b has no integer
    solution -- which is why the count is always rendered.
    """

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        import tkinter.font as tkfont
        name = ttk.Style(self.root).lookup("TNotebook.Tab", "font")
        self.font = tkfont.Font(self.root, font=name or "TkDefaultFont")

    def tearDown(self):
        self.root.destroy()

    def test_every_badge_state_measures_the_same_width(self):
        widths = {self.font.measure(log_tab_label(n))
                  for n in range(0, LOG_BADGE_CAP + 2)}
        self.assertEqual(
            len(widths), 1,
            f"the Log tab changes width with the count: {sorted(widths)} px. "
            "The leftmost tab reflows every tab to its right.")

    def test_the_clean_and_the_warning_form_agree(self):
        self.assertEqual(self.font.measure(log_tab_label(0)),
                         self.font.measure(log_tab_label(3)))

    def test_the_measurement_that_forces_the_padded_zero(self):
        """
        Not an assertion about the label -- an assertion about the FONT fact
        the label's design rests on.  If a space and '!' ever stop agreeing,
        or the digits stop being tabular, the padding scheme is void and this
        is the test that says why.
        """
        self.assertEqual(self.font.measure(" "), self.font.measure("!"))
        self.assertEqual({self.font.measure(d) for d in "0123456789"},
                         {self.font.measure("0")})
        self.assertNotEqual(self.font.measure("Log"),
                            self.font.measure(log_tab_label(0)))


# ============================================================================
# The notebook itself
# ============================================================================

class _AppCase(unittest.TestCase):
    """An App with one file and one trace, selected."""

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
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                              label="coil_a", port_a="1", color_idx=0)
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _scratch_tab(self, text="Run 1"):
        """
        A second tab, which stage C deliberately does not create for itself.

        Every "unseen" property is invisible while the Log is the only tab --
        it is always selected, so nothing can ever be unseen.  This stands in
        for the run tab a later stage adds, which is the whole point of the
        badge.
        """
        frame = ttk.Frame(self.app.results_nb)
        self.app.results_nb.add(frame, text=text)
        self._settle()
        return frame

    def _tab_text(self):
        return self.app.results_nb.tab(self.app._log_tab, "text")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestLogTabIsFirstAndSelected(_AppCase):
    """C1: the structure the rest of the suite silently depends on."""

    def test_the_log_is_tab_zero(self):
        tabs = self.app.results_nb.tabs()
        self.assertEqual(tabs[0], str(self.app._log_tab))

    def test_the_log_is_the_only_tab_at_startup(self):
        """
        Pre-creating an empty run tab here is the 'tidy up' that breaks
        test_session's Ctrl+O guard -- with the Log unselected, results_text is
        unmapped and both focus_set and event_generate become no-ops, so that
        test passes vacuously or fails pointing at the menubar.
        """
        self.assertEqual(len(self.app.results_nb.tabs()), 1)

    def test_the_log_is_selected_at_startup(self):
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._log_tab))

    def test_results_text_is_a_persistent_widget_not_a_property(self):
        """
        Two reads of app.results_text must be the same widget, and it must
        survive a Calculate: six tests elsewhere mark index(END), calculate and
        read back from that mark, which a fresh widget answers with ''.
        """
        first = self.app.results_text
        self.assertIs(self.app.results_text, first)
        mark = self.app.results_text.index(tk.END)
        self.app._on_calculate()
        self._settle()
        self.assertIs(self.app.results_text, first)
        self.assertIn("Calculate @", self.app.results_text.get(mark, tk.END))

    def test_the_walk_from_results_text_still_reaches_a_paned_window(self):
        """
        tests/test_row_table.py::TestResultsPaneVisible and
        tests/test_mode5_editor.py::test_results_pane_still_usable_in_mode_5
        both walk up from results_text to the first TPanedwindow.  The walk now
        passes through the notebook, so pin that it does.
        """
        chain, w = [], self.app.results_text
        while w is not None and w.winfo_class() != "TPanedwindow":
            chain.append(w.winfo_class())
            w = w.master
        self.assertIsNotNone(w, "results_text is not inside a PanedWindow")
        self.assertIn("TNotebook", chain)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestLogTabIsOnScreen(unittest.TestCase):
    """
    The Log's widget must be MAPPED at startup, measured on a shown window.

    A withdrawn root reports winfo_ismapped() == 0 for everything, so this is
    the only way to tell "tab 0 is selected" from "tab 0 is on screen".
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_results_text_is_mapped_at_startup(self):
        app = App()
        try:
            app.geometry("1200x800")
            app.deiconify()
            app.update()
            self.assertTrue(app.results_text.winfo_ismapped())
            self.assertGreater(app.results_text.winfo_height(), 1)
        finally:
            app.destroy()

    def test_an_unselected_tab_is_unmapped_which_is_why_the_badge_exists(self):
        """
        Demonstrates the hazard rather than asserting a behaviour: the widget
        of a tab that is not selected is unmapped, while insert/get keep
        working perfectly.  That is exactly how a warning gets written and
        never read.
        """
        app = App()
        try:
            app.geometry("1200x800")
            app.deiconify()
            app.update()
            other = ttk.Frame(app.results_nb)
            app.results_nb.add(other, text="Run 1")
            app.results_nb.select(other)
            app.update()
            self.assertFalse(app.results_text.winfo_ismapped())
            app.results_text.insert(tk.END, "still writable\n")
            self.assertIn("still writable", app.results_text.get("1.0", tk.END))
        finally:
            app.destroy()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestUnseenWarningBadge(_AppCase):
    """C2: a warning written to a hidden Log announces itself."""

    def test_the_badge_starts_clean(self):
        self.assertEqual(self._tab_text(), log_tab_label(0))
        self.assertEqual(self.app._log_unseen, 0)

    def test_a_warning_written_off_screen_increments_the_badge(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("something was guessed", LOG_WARN)
        self.assertEqual(self.app._log_unseen, 1)
        self.assertEqual(self._tab_text(), log_tab_label(1))
        self.app._append_result("and again", LOG_WARN)
        self.assertEqual(self._tab_text(), log_tab_label(2))

    def test_an_info_line_never_badges(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("2 ports, 401 points")
        self.app._append_result("explicitly informational", LOG_INFO)
        self.assertEqual(self.app._log_unseen, 0)
        self.assertEqual(self._tab_text(), log_tab_label(0))

    def test_a_warning_written_while_the_log_is_on_screen_is_not_unseen(self):
        self.app._append_result("read as it was written", LOG_WARN)
        self.assertEqual(self.app._log_unseen, 0)
        self.assertEqual(self._tab_text(), log_tab_label(0))

    def test_selecting_the_log_clears_the_badge(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("unseen", LOG_WARN)
        self.assertEqual(self.app._log_unseen, 1)
        self.app.results_nb.select(self.app._log_tab)
        self._settle()
        self.assertEqual(self.app._log_unseen, 0)
        self.assertEqual(self._tab_text(), log_tab_label(0))

    def test_the_line_itself_is_written_whatever_the_severity(self):
        """The severity decides the badge and nothing else about the text."""
        self.app.results_text.delete("1.0", tk.END)
        self.app._append_result("plain", LOG_INFO)
        self.app._append_result("noisy", LOG_WARN)
        self.app._append_result("fatal", LOG_ERROR)
        body = self.app.results_text.get("1.0", tk.END)
        self.assertEqual(body, "plain\nnoisy\nfatal\n\n")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestErrorClaimsTheLogTab(_AppCase):
    """C2: an error brings the Log to the front, and keeps it there."""

    def test_an_error_selects_the_log(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.assertEqual(self.app.results_nb.select(), str(other))
        self.app._append_result("  [1] coil_a: ERROR boom", LOG_ERROR)
        self._settle()
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._log_tab))

    def test_an_error_does_not_leave_a_badge_it_has_already_answered(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("  [1] coil_a: ERROR boom", LOG_ERROR)
        self._settle()
        self.assertEqual(self._tab_text(), log_tab_label(0))

    def test_an_automatic_switch_may_not_move_off_an_error(self):
        """
        The ordering a later 'switch to the run that just finished' needs: the
        error has already pulled the Log forward, and moving off it would hide
        the only explanation of why the numbers are missing.
        """
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("  [1] coil_a: ERROR boom", LOG_ERROR)
        self._settle()
        self.assertFalse(self.app._select_results_tab(other))
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._log_tab))

    def test_an_automatic_switch_works_when_no_error_owns_the_pane(self):
        other = self._scratch_tab()
        self.assertTrue(self.app._select_results_tab(other))
        self._settle()
        self.assertEqual(self.app.results_nb.select(), str(other))

    def test_the_user_moving_off_the_log_releases_the_claim(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        self.app._append_result("  [1] coil_a: ERROR boom", LOG_ERROR)
        self._settle()
        self.app.results_nb.select(other)          # the user, by hand
        self._settle()
        self.assertFalse(self.app._log_forced)
        self.assertTrue(self.app._select_results_tab(other))

    def test_a_new_calculate_releases_the_claim(self):
        """
        The previous run's error is history the moment a new run starts, or
        every later run would be pinned to the Log for the rest of the session.
        """
        self.app._log_forced = True
        self.app._on_calculate()
        self._settle()
        self.assertFalse(self.app._log_forced)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSeverityRouting(_AppCase):
    """C2: the existing call sites reach the severity they deserve."""

    def _off_screen(self):
        other = self._scratch_tab()
        self.app.results_nb.select(other)
        self._settle()
        return other

    def _deselect_the_trace(self):
        """
        Calculate flushes the editor into the SELECTED trace first, so a spec
        poked directly onto tc is written straight back over before it is used.
        Dropping the Listbox selection is the way to make the poke stick.
        """
        self.app.traces_lb.selection_clear(0, tk.END)
        self._settle()

    def test_a_clean_calculate_leaves_the_badge_alone(self):
        self._off_screen()
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.app._log_unseen, 0,
                         "a clean run badged the Log: "
                         + self._tab_text())

    def test_a_trace_bound_to_a_missing_file_is_a_warning(self):
        self._off_screen()
        self._deselect_the_trace()
        self.tc.file_label = "not_loaded.s4p"
        self.app._on_calculate()
        self._settle()
        self.assertGreaterEqual(self.app._log_unseen, 1)

    def test_a_raising_trace_pulls_the_log_forward(self):
        """
        A port number the file does not have: _build_termination raises,
        _on_calculate prints ERROR + the traceback, and the pane must be
        showing them.
        """
        other = self._off_screen()
        self._deselect_the_trace()
        self.tc.port_a = "99"
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._log_tab))
        self.assertNotEqual(self.app.results_nb.select(), str(other))
        self.assertIn("ERROR", self.app.results_text.get("1.0", tk.END))

    def _add_files(self, *paths):
        """Drive the real _on_add_file with the file dialog stubbed out."""
        real = pkg_rlc_gui.filedialog.askopenfilenames
        pkg_rlc_gui.filedialog.askopenfilenames = lambda **kw: tuple(paths)
        try:
            self.app._on_add_file()
        finally:
            pkg_rlc_gui.filedialog.askopenfilenames = real
        self._settle()

    def test_a_clean_file_summary_is_informational(self):
        """
        summary_lines() is mostly a DESCRIPTION of the file -- port count,
        span, max |S|, the descriptive Notes.  None of it is a warning.
        """
        self._off_screen()
        self._add_files(str(FIXTURE))
        self.assertIn("ports", self.app.results_text.get("1.0", tk.END))
        self.assertEqual(self.app._log_unseen, 0,
                         "a clean file badged the Log: " + self._tab_text())

    def test_a_parser_warning_on_load_badges_the_log(self):
        """
        The other half of the same routing: 'I guessed' / 'I threw something
        away' has to announce itself, because with the Log off screen the
        summary is written where nobody is looking.
        """
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = Path(d) / "no_option_line.s1p"
        # No '#' line at all -> the parser assumes '# GHZ S MA R 50' and says so.
        path.write_text("1.0 0.5 10.0\n2.0 0.4 20.0\n", encoding="utf-8")
        self._off_screen()
        self._add_files(str(path))
        body = self.app.results_text.get("1.0", tk.END)
        self.assertIn("WARN:", body)
        self.assertGreaterEqual(self.app._log_unseen, 1)

    def test_a_legacy_mode_migration_is_a_warning(self):
        self._off_screen()
        self.tc.mode = 4
        self.tc.vdd_ports = "3"
        self.app._migrate_trace(self.tc)
        self._settle()
        self.assertGreaterEqual(self.app._log_unseen, 1)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestNotebookDoesNotSqueezeTheLeftPanel(unittest.TestCase):
    """
    The tab strip must not reach the outer sash.

    Two existing properties make this structural rather than lucky: the left
    panel is a fixed-width ttk.Frame with pack_propagate(False) and weight=0,
    and _build_right_panel populates a pane BEFORE add()ing it (ttk sizes a
    pane from its requested size at add() time and never recomputes).  A change
    to either reopens the risk, so this pins the numbers.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    LEFT_W = 460            # ttk.Frame(outer, width=460), pack_propagate(False)
    ED_CANVAS_W = 431       # the editor viewport in mode 5, vertical bar shown

    @staticmethod
    def _outer_paned(app):
        w = app.results_text
        while w is not None and w.winfo_class() != "TPanedwindow":
            w = w.master            # the RIGHT paned window
        return w.master             # its parent is the outer one

    def test_thirty_tabs_do_not_move_the_editor_canvas(self):
        app = App()
        try:
            app.geometry("1040x600")
            app.deiconify()
            app.update()
            app.ed_mode_var.set(5)
            app._on_mode_changed()
            for _ in range(3):
                app.update_idletasks()
                app.update()
            outer = self._outer_paned(app)
            self.assertEqual(outer.winfo_class(), "TPanedwindow")
            left = outer.panes()[0]
            before = (outer.sashpos(0), app.nametowidget(left).winfo_width(),
                      app._ed_canvas.winfo_width())
            # The absolute numbers, not just "it did not move": a relative
            # check passes just as happily when BOTH readings are wrong.
            self.assertEqual(
                before,
                (self.LEFT_W, self.LEFT_W, self.ED_CANVAS_W),
                "the left panel is not where it was measured to be")
            for i in range(30):
                app.results_nb.add(ttk.Frame(app.results_nb),
                                   text=f"Calc {i}  2026-08-08 14:03:22")
            for _ in range(3):
                app.update_idletasks()
                app.update()
            after = (outer.sashpos(0), app.nametowidget(left).winfo_width(),
                     app._ed_canvas.winfo_width())
            self.assertEqual(
                after, before,
                f"a {app.results_nb.winfo_reqwidth()} px tab strip moved the "
                f"left panel: {before} -> {after}")
        finally:
            app.destroy()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
