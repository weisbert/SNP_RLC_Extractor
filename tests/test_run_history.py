"""
Stage E2: every Calculate leaves a page behind, like a simulation run.

The shape of the thing is what makes it safe, so that is what these tests pin:

  * TWO DISJOINT SETS.  The auto ring is all Calculate ever touches; the kept
    set is entered only by the user pressing Keep and is capped AT THE MOMENT
    THEY PRESS IT.  So Calculate can never block, never prompt and never
    destroy something the user asked to keep -- the all-locked deadlock is
    unreachable by construction rather than handled.
  * Eviction is forget() THEN destroy(), in ONE function.  forget() alone does
    not destroy the child; the guard is a WIDGET COUNT after a churn loop, not
    an RSS reading (measured: the working set does not drop even on correct
    teardown, so an RSS assertion measures the allocator).
  * The auto-switch is CONDITIONAL.  Yanking a reader off a page they kept is
    the opposite of what keeping means, and Calculate is pressed constantly.
  * Every page that is not the newest says so, because otherwise three
    surfaces on one screen disagree with nothing to explain it: the tab shows
    run #3, the plot 200 px below shows run #7, and Export CSV pressed while
    reading it writes run #7.

Pure string / diff properties get pure tests; everything else is measured off
real Tk widgets and skips cleanly with no display.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import pkg_rlc.frontend.app as pkg_rlc_gui  # noqa: E402
from pkg_rlc.physics.core import parse_touchstone  # noqa: E402
from pkg_rlc.frontend.app import (  # noqa: E402
    RUN_AUTO_DEFAULT,
    RUN_KEPT_GLYPH,
    RUN_MARK_NEW,
    RUN_MARK_SEEN,
    RUN_OPEN_GLYPH,
    RUN_TABS_DEFAULT,
    RUN_TABS_HARD_CAP,
    App,
    ConnectionRow,
    FileEntry,
    MeasPortRow,
    RunSnapshot,
    TraceConfig,
    _config_signature,
    describe_run_change,
    keep_button_label,
    run_change_line,
    run_headline,
    run_signatures,
    run_stale_banner,
    run_tab_label,
    run_trace_ids,
    session_to_dict,
    trace_signature_fields,
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


def _when(h=14, m=32, s=7):
    return datetime(2026, 8, 8, h, m, s)


# ============================================================================
# 1 -- the tab label (pure text, then measured pixels)
# ============================================================================

class TestRunTabLabel(unittest.TestCase):

    def test_it_carries_the_run_number_and_a_clock(self):
        self.assertEqual(run_tab_label(7, _when(10, 42), False, False),
                         f"{RUN_MARK_SEEN}{RUN_OPEN_GLYPH}#7 10:42")

    def test_kept_and_unseen_are_separate_columns(self):
        self.assertEqual(run_tab_label(7, _when(10, 42), True, False),
                         f"{RUN_MARK_SEEN}{RUN_KEPT_GLYPH}#7 10:42")
        self.assertEqual(run_tab_label(7, _when(10, 42), False, True),
                         f"{RUN_MARK_NEW}{RUN_OPEN_GLYPH}#7 10:42")

    def test_every_state_is_the_same_number_of_characters(self):
        lens = {len(run_tab_label(7, _when(10, 42), k, u))
                for k in (False, True) for u in (False, True)}
        self.assertEqual(len(lens), 1)

    def test_one_of_each_pair_is_ALWAYS_emitted(self):
        """
        The rule that makes the width stable: never a conditional glyph.  A
        label that dropped the marker when there was nothing to say would be
        narrower, and on a compressing strip that reflows every tab.
        """
        for kept in (False, True):
            for unseen in (False, True):
                lbl = run_tab_label(3, _when(), kept, unseen)
                self.assertIn(lbl[0], (RUN_MARK_NEW, RUN_MARK_SEEN))
                self.assertIn(lbl[1], (RUN_KEPT_GLYPH, RUN_OPEN_GLYPH))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRunTabLabelWidth(unittest.TestCase):
    """
    Measured in the tab strip's OWN font, the way the results swatch and the
    Log badge were measured.

    In TkDefaultFont (Microsoft YaHei UI 9, what the vista theme's
    TNotebook.Tab uses): '!' and ' ' are both 4 px, '☑' and '☐' are both 12 px.
    The brief's leading '*' is 5 px against a 4 px space and NO blank glyph in
    this font measures 5 px (U+0020/00A0/2002/2003/2005..200A/205F/3000 come
    out 2, 3, 4, 6, 8 and 12) -- so '*' cannot be made width-stable here and
    '!' already means "unread" on the Log tab of this very notebook.
    """

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        import tkinter.font as tkfont
        name = ttk.Style(self.root).lookup("TNotebook.Tab", "font")
        self.font = tkfont.Font(self.root, font=name or "TkDefaultFont")

    def tearDown(self):
        self.root.destroy()

    def test_the_kept_pair_measures_equal(self):
        self.assertEqual(self.font.measure(RUN_KEPT_GLYPH),
                         self.font.measure(RUN_OPEN_GLYPH),
                         "the kept glyph pair is not width-stable")

    def test_the_unseen_pair_measures_equal(self):
        self.assertEqual(self.font.measure(RUN_MARK_NEW),
                         self.font.measure(RUN_MARK_SEEN),
                         "the unseen marker pair is not width-stable")

    def test_every_label_state_measures_the_same(self):
        widths = {self.font.measure(run_tab_label(7, _when(10, 42), k, u))
                  for k in (False, True) for u in (False, True)}
        self.assertEqual(len(widths), 1,
                         f"a run tab changes width with its state: "
                         f"{sorted(widths)} px")

    def test_the_measurement_that_rejected_a_leading_star(self):
        """
        Not an assertion about the label -- an assertion about the FONT fact
        that decided the marker.  If a blank glyph ever measures the same as
        '*', a '*' marker becomes available and this is the test that says so.
        """
        star = self.font.measure("*")
        blanks = {self.font.measure(c) for c in
                  "         "
                  "  　"}
        self.assertNotIn(star, blanks,
                         "a blank glyph now measures the same as '*' -- a "
                         "leading '*' marker has become width-stable here")


# ============================================================================
# 2 -- the page's three header lines (pure)
# ============================================================================

class _Rec:
    def __init__(self, tid, enabled=True):
        self.id = tid
        self.enabled = enabled


class TestRunHeadline(unittest.TestCase):

    def _run(self, **kw):
        base = dict(number=12, when=_when(), marker_freq_hz=5e9)
        base.update(kw)
        return RunSnapshot(**base)

    def test_line_one_names_the_run_the_time_the_marker_and_the_traces(self):
        run = self._run(rows=(_Rec(1), _Rec(2), _Rec(5)), blocks=(_Rec(3),))
        self.assertEqual(
            run_headline(run),
            "Run #12 · 14:32:07 · @ 5.000 GHz · 4 traces [1,2,3,5]")

    def test_the_trace_ids_come_from_both_collections_without_repeats(self):
        run = self._run(rows=(_Rec(2), _Rec(1)), blocks=(_Rec(1),))
        self.assertEqual(run_trace_ids(run), [1, 2])

    def test_one_trace_is_singular(self):
        self.assertIn("1 trace [4]", run_headline(self._run(rows=(_Rec(4),))))

    def test_a_run_with_no_marker_frequency_says_so_rather_than_printing_nan(self):
        run = self._run(marker_freq_hz=float("nan"))
        self.assertIn("no marker", run_headline(run))
        self.assertNotIn("nan", run_headline(run))

    def test_a_hidden_trace_still_counts_it_was_measured(self):
        run = self._run(rows=(_Rec(1), _Rec(2, enabled=False)))
        self.assertIn("2 traces [1,2]", run_headline(run))


class TestStaleBanner(unittest.TestCase):

    def test_it_names_the_run_the_plot_and_the_csv_are_showing(self):
        self.assertEqual(
            run_stale_banner(12),
            "! the plot and Export CSV show run #12, not this page")


# ============================================================================
# 3 -- "what changed", the real discriminator (pure)
# ============================================================================

def _tc(**kw):
    base = dict(id=1, file_label="coil.s4p", mode=1, port_a="1")
    base.update(kw)
    return TraceConfig(**base)


class TestSignatureFieldsCoverConfigSignature(unittest.TestCase):
    """
    trace_signature_fields is a NAMED _config_signature and must stay one for
    one with it, or a run page will claim nothing changed while the numbers
    moved.
    """

    def test_the_two_have_the_same_number_of_fields(self):
        tc = _tc()
        self.assertEqual(len(trace_signature_fields(tc)),
                         len(_config_signature(tc)))

    def test_every_field_config_signature_watches_is_named_here(self):
        """
        Mutate each field _config_signature reads and demand the named version
        notices too.  This is the guard that survives someone adding a tenth
        field to _config_signature.
        """
        mutations = [
            ("file_label", "other.s4p"),
            ("mode", 2),
            ("port_a", "9"),
            ("port_b", "9"),
            ("short_pairs", "1-2"),
            ("gnd_ports", "6-14"),
            ("extra_lines", "3 ground"),
        ]
        for name, value in mutations:
            with self.subTest(field=name):
                a, b = _tc(), _tc()
                setattr(b, name, value)
                self.assertNotEqual(_config_signature(a), _config_signature(b))
                self.assertNotEqual(trace_signature_fields(a),
                                    trace_signature_fields(b),
                                    f"{name} changes the answer but no run "
                                    f"page would mention it")

    def test_the_two_table_fields_are_named_too(self):
        a = _tc(mode=5)
        b = _tc(mode=5, mports=[MeasPortRow(name="L1", plus="1", minus="2")])
        self.assertNotEqual(trace_signature_fields(a),
                            trace_signature_fields(b))
        c = _tc(mode=5, conn_rows=[ConnectionRow(kind="ground", ports="3")])
        self.assertNotEqual(trace_signature_fields(a),
                            trace_signature_fields(c))


class TestDescribeRunChange(unittest.TestCase):

    def test_a_changed_field_is_reported_old_then_new(self):
        a = run_signatures([_tc(gnd_ports="6-14")])
        b = run_signatures([_tc(gnd_ports="6-16")])
        self.assertEqual(describe_run_change(a, b),
                         ["[1] gnd 6-14 -> 6-16"])

    def test_an_unchanged_run_reports_nothing(self):
        a = run_signatures([_tc()])
        self.assertEqual(describe_run_change(a, run_signatures([_tc()])), [])

    def test_an_empty_value_reads_as_none_not_as_a_blank(self):
        a = run_signatures([_tc(gnd_ports="")])
        b = run_signatures([_tc(gnd_ports="3")])
        self.assertEqual(describe_run_change(a, b), ["[1] gnd (none) -> 3"])

    def test_a_new_trace_is_reported_as_added(self):
        a = run_signatures([_tc(id=1)])
        b = run_signatures([_tc(id=1), _tc(id=2)])
        self.assertEqual(describe_run_change(a, b), ["[2] added"])

    def test_a_deleted_trace_is_reported_as_removed(self):
        a = run_signatures([_tc(id=1), _tc(id=2)])
        b = run_signatures([_tc(id=1)])
        self.assertEqual(describe_run_change(a, b), ["[2] removed"])

    def test_the_list_is_capped_and_says_how_many_it_dropped(self):
        a = run_signatures([_tc(id=i) for i in range(1, 8)])
        b = run_signatures([_tc(id=i, gnd_ports="9") for i in range(1, 8)])
        items = describe_run_change(a, b, max_items=4)
        self.assertEqual(len(items), 5)
        self.assertEqual(items[-1], "… +3 more")

    def test_a_long_value_is_elided_rather_than_flooding_the_line(self):
        a = run_signatures([_tc(gnd_ports="1")])
        b = run_signatures([_tc(gnd_ports="1,2,3,4,5,6,7,8,9,10,11,12,13,14")])
        self.assertIn("…", describe_run_change(a, b)[0])
        self.assertLess(len(describe_run_change(a, b)[0]), 60)


class TestRunChangeLine(unittest.TestCase):

    def test_it_names_the_run_it_is_comparing_against(self):
        self.assertEqual(run_change_line(11, ["[3] gnd 6-14 -> 6-16"]),
                         "changed since #11:  [3] gnd 6-14 -> 6-16")

    def test_no_change_is_no_line_at_all(self):
        self.assertEqual(run_change_line(11, []), "")


class TestKeepButtonLabel(unittest.TestCase):

    def test_at_the_cap_the_label_states_the_reason(self):
        lbl = keep_button_label(4, 4, "full")
        self.assertIn("4/4", lbl)
        self.assertIn("full", lbl)

    def test_the_menu_gets_the_sentence_the_button_cannot_afford(self):
        """
        The button's slot is width-bound and the menu entry is not.

        Measured with TkDefaultFont scaled 1.5x at the 1040x600 minsize: the
        Results header is 575 px, requests 687 with the long label, and pack
        gave the last-packed Keep button the 213 px that were left -- the
        sentence was clipped mid-phrase with winfo_ismapped() still 1, so no
        ismapped assertion could see it.
        """
        self.assertIn("close a kept run first",
                      keep_button_label(4, 4, "full", long=True))
        self.assertNotIn("close a kept run first",
                         keep_button_label(4, 4, "full"))
        # Both still carry the budget, which is the part that says WHY.
        self.assertIn("4/4", keep_button_label(4, 4, "full", long=True))

    def test_below_the_cap_it_shows_the_budget(self):
        self.assertEqual(keep_button_label(1, 5, "free"), "Keep run (1/5)")

    def test_an_already_kept_run_says_kept(self):
        self.assertTrue(keep_button_label(1, 5, "kept").startswith("Kept"))

    def test_with_no_run_on_screen_it_is_plain(self):
        self.assertEqual(keep_button_label(0, 5, "none"), "Keep run")


# ============================================================================
# 4 -- the notebook itself
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
                              label="tank", port_a="1", color_idx=0)
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _calc(self, n=1):
        for _ in range(n):
            self.app._on_calculate()
        self._settle()

    def _numbers(self):
        return [rt.run.number for rt in self.app._run_tabs]

    def _deselect_the_trace(self):
        """Calculate flushes the editor into the SELECTED trace first, so a
        spec poked onto tc directly is written straight back over."""
        self.app.traces_lb.selection_clear(0, tk.END)
        self._settle()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestStructure(_AppCase):

    def test_the_log_is_still_tab_zero_and_the_only_tab_at_startup(self):
        self.assertEqual(len(self.app.results_nb.tabs()), 1)
        self.assertEqual(self.app.results_nb.tabs()[0],
                         str(self.app._log_tab))
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._log_tab))

    def test_every_calculate_adds_a_page_newest_first(self):
        self._calc(3)
        self.assertEqual(self._numbers(), [3, 2, 1])
        tabs = self.app.results_nb.tabs()
        self.assertEqual(tabs[0], str(self.app._log_tab))
        self.assertEqual([str(rt.frame) for rt in self.app._run_tabs],
                         list(tabs[1:]))

    def test_a_page_is_a_text_of_the_same_height_as_the_log(self):
        """
        Same height=10, so the notebook's requested height cannot creep as
        runs accumulate and drag the vertical sash with it.
        """
        self._calc()
        rt = self.app._run_tabs[0]
        self.assertEqual(int(rt.text.cget("height")),
                         int(self.app.results_text.cget("height")))

    def test_the_strip_never_grows_taller_however_many_runs(self):
        before = self.app.results_nb.winfo_reqheight()
        self._calc(12)
        self.assertEqual(self.app.results_nb.winfo_reqheight(), before,
                         "the tab strip wrapped -- it must compress instead, "
                         "or every run steals plot height")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEvictionTouchesOnlyTheAutoRing(_AppCase):

    def test_the_auto_ring_stops_at_its_size(self):
        self._calc(RUN_AUTO_DEFAULT + 4)
        self.assertEqual(len(self.app._auto_run_tabs()), RUN_AUTO_DEFAULT)

    def test_the_oldest_goes_first(self):
        self._calc(RUN_AUTO_DEFAULT + 2)
        self.assertEqual(self._numbers(),
                         list(range(RUN_AUTO_DEFAULT + 2,
                                    2, -1))[:RUN_AUTO_DEFAULT])

    def test_a_kept_run_is_never_evicted_however_many_runs_follow(self):
        self._calc()
        keeper = self.app._run_tabs[0]
        self.assertTrue(self.app._keep_run_tab(keeper))
        self._calc(20)
        self.assertIn(keeper, self.app._run_tabs)
        self.assertEqual(keeper.run.number, 1)

    def test_a_kept_run_does_not_occupy_an_auto_slot(self):
        """The two sets are disjoint: keeping one must not shrink the ring."""
        self._calc()
        self.app._keep_run_tab(self.app._run_tabs[0])
        self._calc(6)
        self.assertEqual(len(self.app._auto_run_tabs()), RUN_AUTO_DEFAULT)
        self.assertEqual(len(self.app._kept_run_tabs()), 1)

    def test_calculate_never_blocks_even_with_the_kept_set_full(self):
        """
        The property the whole design exists for.  Fill the kept set, then run
        Calculate many more times: no exception, no prompt, and the auto ring
        keeps turning over.
        """
        cap = self.app._kept_cap()
        for _ in range(cap):
            self._calc()
            self.assertTrue(self.app._keep_run_tab(self.app._run_tabs[0]))
        self.assertEqual(len(self.app._kept_run_tabs()), cap)
        self._calc(15)
        self.assertEqual(len(self.app._kept_run_tabs()), cap)
        self.assertEqual(len(self.app._auto_run_tabs()), RUN_AUTO_DEFAULT)

    def test_the_page_being_read_survives_as_the_SAME_widget(self):
        """
        Evicting what the user is reading raises no error -- Tk silently
        selects a neighbour -- which is worse than an error.  Its scroll
        position must survive too: eviction renumbers the tabs after it and
        anything tracking an index would follow the wrong page.
        """
        self._calc(RUN_AUTO_DEFAULT)
        oldest = self.app._run_tabs[-1]
        self.app.results_nb.select(oldest.frame)
        self._settle()
        oldest.text.insert(tk.END, "\n" * 60 + "bottom\n")
        oldest.text.yview_moveto(1.0)
        self._settle()
        where = oldest.text.yview()[0]
        frame_id = str(oldest.frame)
        self._calc(2)
        self.assertIn(oldest, self.app._run_tabs)
        self.assertEqual(str(oldest.frame), frame_id)
        self.assertEqual(self.app.results_nb.select(), frame_id)
        # Not exact: the page gained the "not this page" banner, so it is one
        # line longer than it was.  What must not happen is the reader being
        # thrown back to the top.
        self.assertLess(abs(oldest.text.yview()[0] - where), 0.05,
                        "the reader was scrolled away from where they were")
        self.assertGreater(oldest.text.yview()[0], 0.5)

    def test_the_page_for_the_run_on_the_plot_is_never_evicted(self):
        """
        At an auto ring of 1 with the reader parked on the older page, the
        oldest-first scan would skip the page they are on and take the run
        that just finished -- the one the plot is showing.
        """
        self._calc(2)
        self.app.results_nb.select(self.app._run_tabs[1].frame)
        self._settle()
        self.app._run_auto_var.set(1)
        self.app._on_run_caps_changed()
        self._settle()
        self._calc()
        numbers = [rt.run.number for rt in self.app._run_tabs]
        self.assertIn(self.app._last_run.number, numbers,
                      "the page for the run on the plot was evicted")

    def test_the_ring_stays_bounded_even_while_a_page_is_protected(self):
        self._calc(RUN_AUTO_DEFAULT)
        self.app.results_nb.select(self.app._run_tabs[-1].frame)
        self._settle()
        self._calc(10)
        self.assertLessEqual(len(self.app._auto_run_tabs()),
                             RUN_AUTO_DEFAULT + 1)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestNoLeak(_AppCase):
    """
    forget() alone does NOT destroy the child: 300 runs at a limit of 10 left
    290 orphan widgets and +21.5 MB, growing linearly.  The honest guard is a
    widget count -- an RSS reading does not drop even on correct teardown.
    """

    def test_the_notebook_holds_no_widget_it_does_not_show(self):
        self._calc(40)
        self.assertEqual(len(self.app.results_nb.winfo_children()),
                         len(self.app.results_nb.tabs()))

    def test_the_count_does_not_grow_with_the_number_of_runs(self):
        self._calc(10)
        first = len(self.app.results_nb.winfo_children())
        self._calc(30)
        self.assertEqual(len(self.app.results_nb.winfo_children()), first)

    def test_a_closed_page_is_gone_from_the_record_too(self):
        self._calc(2)
        rt = self.app._run_tabs[0]
        self.app._destroy_run_tab(rt)
        self.assertNotIn(rt, self.app._run_tabs)
        self.assertEqual(len(self.app.results_nb.winfo_children()),
                         len(self.app.results_nb.tabs()))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheKeptCapBitesAtKeepTime(_AppCase):

    def test_the_button_is_disabled_and_says_why_at_the_cap(self):
        cap = self.app._kept_cap()
        for _ in range(cap):
            self._calc()
            self.app._keep_run_tab(self.app._run_tabs[0])
        self._calc()
        self.app.results_nb.select(self.app._run_tabs[0].frame)
        self._settle()
        self.assertTrue(self.app._keep_btn.instate(["disabled"]))
        label = self.app._keep_btn.cget("text")
        self.assertIn(f"{cap}/{cap}", label)
        self.assertIn("full", label)

    def test_keeping_is_refused_at_the_cap(self):
        cap = self.app._kept_cap()
        for _ in range(cap):
            self._calc()
            self.assertTrue(self.app._keep_run_tab(self.app._run_tabs[0]))
        self._calc()
        self.assertFalse(self.app._keep_run_tab(self.app._run_tabs[0]))
        self.assertEqual(len(self.app._kept_run_tabs()), cap)

    def test_closing_a_kept_run_frees_a_slot_which_is_what_the_label_says(self):
        cap = self.app._kept_cap()
        kept = []
        for _ in range(cap):
            self._calc()
            self.app._keep_run_tab(self.app._run_tabs[0])
            kept.append(self.app._run_tabs[0])
        self.app._destroy_run_tab(kept[0])
        self._calc()
        self.assertTrue(self.app._keep_run_tab(self.app._run_tabs[0]))

    def test_the_button_tracks_the_page_on_screen(self):
        self._calc(2)
        self.app.results_nb.select(self.app._log_tab)
        self._settle()
        self.assertEqual(self.app._keep_btn.cget("text"),
                         keep_button_label(0, self.app._kept_cap(), "none"))
        self.assertTrue(self.app._keep_btn.instate(["disabled"]))
        rt = self.app._run_tabs[0]
        self.app.results_nb.select(rt.frame)
        self._settle()
        self.assertFalse(self.app._keep_btn.instate(["disabled"]))
        self.app._on_keep_run()
        self._settle()
        self.assertTrue(self.app._keep_btn.cget("text").startswith("Kept"))
        self.assertTrue(self.app._keep_btn.instate(["disabled"]))

    def test_the_kept_glyph_lands_on_the_tab(self):
        self._calc()
        rt = self.app._run_tabs[0]
        self.assertIn(RUN_OPEN_GLYPH, self.app.results_nb.tab(rt.frame, "text"))
        self.app._keep_run_tab(rt)
        self.assertIn(RUN_KEPT_GLYPH, self.app.results_nb.tab(rt.frame, "text"))

    def test_the_caps_are_the_measured_ones(self):
        self.assertEqual(self.app._run_auto_max, RUN_AUTO_DEFAULT)
        self.assertEqual(self.app._run_tabs_max, RUN_TABS_DEFAULT)
        self.assertLessEqual(RUN_TABS_DEFAULT, RUN_TABS_HARD_CAP)
        self.assertGreaterEqual(self.app._kept_cap(), 1)

    def test_the_auto_ring_can_never_swallow_the_last_kept_slot(self):
        self.app._run_auto_var.set(RUN_TABS_DEFAULT + 5)
        self.app._run_tabs_var.set(RUN_TABS_DEFAULT)
        self.app._on_run_caps_changed()
        self.assertGreaterEqual(self.app._kept_cap(), 1)
        self.assertLess(self.app._run_auto_max, self.app._run_tabs_max)

    def test_lowering_the_auto_cap_trims_the_ring_immediately(self):
        self._calc(RUN_AUTO_DEFAULT)
        self.app._run_auto_var.set(1)
        self.app._on_run_caps_changed()
        self._settle()
        self.assertLessEqual(len(self.app._auto_run_tabs()), 2)

    def test_the_total_is_clamped_to_the_hard_cap(self):
        self.app._run_tabs_var.set(99)
        self.app._on_run_caps_changed()
        self.assertEqual(self.app._run_tabs_max, RUN_TABS_HARD_CAP)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestConditionalAutoSwitch(_AppCase):

    def test_it_switches_when_the_reader_was_on_the_log(self):
        self.assertEqual(self.app.results_nb.select(), str(self.app._log_tab))
        self._calc()
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._run_tabs[0].frame))

    def test_it_switches_again_when_the_reader_is_on_the_newest(self):
        self._calc()
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._run_tabs[0].frame))
        self._calc()
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._run_tabs[0].frame))
        self.assertEqual(self.app._run_tabs[0].run.number, 2)

    def test_it_leaves_a_reader_parked_on_an_older_page_alone(self):
        """
        Yanking a reader off a page they deliberately kept is the opposite of
        what keeping means, and Calculate is pressed constantly in the
        edit/compute/read loop.
        """
        self._calc(2)
        older = self.app._run_tabs[1]
        self.app.results_nb.select(older.frame)
        self._settle()
        self._calc()
        self.assertEqual(self.app.results_nb.select(), str(older.frame))

    def test_a_KEPT_page_is_not_yanked_away_even_though_it_is_the_newest(self):
        """
        The natural gesture is to press Keep on the page you are looking at,
        which is by definition the newest -- so "am I at the newest?" answered
        yes and the very next Calculate moved the reader off the page they had
        just deliberately kept.  Measured: Calculate -> land on '#2' -> Keep ->
        Calculate -> selected '#3'.
        """
        self._calc()
        kept = self.app._run_tabs[0]
        self.app.results_nb.select(kept.frame)
        self._settle()
        self.assertTrue(self.app._keep_run_tab(kept))
        self._settle()
        self._calc()
        self.assertEqual(self.app.results_nb.select(), str(kept.frame),
                         "Calculate moved the reader off a kept page")
        # ... and the new page announces itself instead of arriving silently.
        newest = self.app._run_tabs[0]
        self.assertIsNot(newest, kept)
        self.assertTrue(newest.unseen)

    def test_an_unkept_newest_page_still_follows_the_run(self):
        """The kept check must not switch the whole feature off."""
        self._calc()
        first = self.app._run_tabs[0]
        self.app.results_nb.select(first.frame)
        self._settle()
        self._calc()
        self.assertNotEqual(self.app.results_nb.select(), str(first.frame))
        self.assertEqual(self.app.results_nb.select(),
                         str(self.app._run_tabs[0].frame))

    def test_the_page_it_did_not_switch_to_is_marked_unseen(self):
        self._calc(2)
        older = self.app._run_tabs[1]
        self.app.results_nb.select(older.frame)
        self._settle()
        self._calc()
        newest = self.app._run_tabs[0]
        self.assertTrue(newest.unseen)
        self.assertTrue(
            self.app.results_nb.tab(newest.frame, "text")
            .startswith(RUN_MARK_NEW))

    def test_reading_it_clears_the_mark(self):
        self._calc(2)
        older = self.app._run_tabs[1]
        self.app.results_nb.select(older.frame)
        self._settle()
        self._calc()
        newest = self.app._run_tabs[0]
        self.app.results_nb.select(newest.frame)
        self._settle()
        self.assertFalse(newest.unseen)
        self.assertTrue(
            self.app.results_nb.tab(newest.frame, "text")
            .startswith(RUN_MARK_SEEN))

    def test_a_page_switched_to_is_never_born_unseen(self):
        self._calc()
        self.assertFalse(self.app._run_tabs[0].unseen)

    def test_an_error_still_wins_and_the_run_page_waits(self):
        """
        An ERROR pulled the Log forward before the page existed, and
        _select_results_tab declines to move off it.  The page is still
        created -- the run happened -- and it is marked unseen.
        """
        self._deselect_the_trace()
        self.tc.port_a = "99"           # a port the file does not have
        self._calc()
        self.assertEqual(self.app.results_nb.select(), str(self.app._log_tab))
        self.assertIn("ERROR", self.app.results_text.get("1.0", tk.END))
        self.assertEqual(len(self.app._run_tabs), 1)
        self.assertTrue(self.app._run_tabs[0].unseen)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestPageContents(_AppCase):

    def _page(self, rt):
        return rt.text.get("1.0", tk.END)

    def test_line_one_is_the_headline(self):
        self._calc()
        rt = self.app._run_tabs[0]
        self.assertEqual(self._page(rt).split("\n")[0],
                         run_headline(rt.run))

    def test_the_report_is_on_the_page_not_only_in_the_log(self):
        self._calc()
        body = self._page(self.app._run_tabs[0])
        self.assertIn("tank", body)
        self.assertIn(pkg_rlc_gui.RESULTS_SWATCH, body)

    def test_the_banner_is_on_every_page_but_the_newest(self):
        self._calc(3)
        newest = self.app._run_tabs[0]
        self.assertNotIn("not this page", self._page(newest))
        for rt in self.app._run_tabs[1:]:
            self.assertIn(run_stale_banner(newest.run.number),
                          self._page(rt),
                          f"run #{rt.run.number} does not say the plot is "
                          f"showing something else")

    def test_the_banner_follows_the_newest_run(self):
        self._calc(2)
        was_newest = self.app._run_tabs[0]
        self.assertNotIn("not this page", self._page(was_newest))
        self._calc()
        self.assertIn(run_stale_banner(3), self._page(was_newest))

    def test_closing_the_newest_page_does_not_promote_an_older_one(self):
        """
        "Newest" means THE RUN THE PLOT IS SHOWING, not the youngest surviving
        tab.  Closing the newest page does not un-plot its curves, so the
        pages left behind must keep warning about exactly that.
        """
        self._calc(3)
        newest = self.app._run_tabs[0]
        self.app._run_tab_menu_target = newest
        self.app._on_menu_close_run()
        self._settle()
        for rt in self.app._run_tabs:
            self.assertIn(run_stale_banner(3), self._page(rt),
                          f"run #{rt.run.number} was promoted to 'current' "
                          f"by the newest page being closed")

    def test_line_two_says_what_changed(self):
        self._calc()
        self._deselect_the_trace()
        self.tc.gnd_ports = "3-4"
        self._calc()
        page = self._page(self.app._run_tabs[0])
        self.assertIn("changed since #1:", page)
        self.assertIn("[1] gnd (none) -> 3-4", page)

    def test_line_two_is_absent_when_nothing_changed(self):
        self._calc(2)
        page = self._page(self.app._run_tabs[0])
        self.assertNotIn("changed since", page)

    def test_the_first_run_has_nothing_to_compare_against(self):
        self._calc()
        self.assertNotIn("changed since", self._page(self.app._run_tabs[0]))

    def test_a_page_keeps_its_own_numbers_when_the_trace_moves_on(self):
        """
        The REPORT on an old page is frozen -- only the banner, which is about
        the newest run and not about this one, is allowed to appear.
        """
        self._calc()
        first = self._page(self.app._run_tabs[-1])
        self._deselect_the_trace()
        self.tc.label = "renamed"
        self.app._refresh_trace_list()
        self._calc()

        def body(text):
            return [ln for ln in text.split("\n")
                    if "not this page" not in ln]

        self.assertEqual(body(self._page(self.app._run_tabs[-1])),
                         body(first))
        self.assertIn("tank", self._page(self.app._run_tabs[-1]))
        self.assertNotIn("renamed", self._page(self.app._run_tabs[-1]))
        self.assertIn("renamed", self._page(self.app._run_tabs[0]))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestUnitsRerender(_AppCase):

    def test_it_creates_no_tab(self):
        self._calc()
        before = len(self.app.results_nb.tabs())
        self.app.units_mode_var.set("aligned")
        self.app._on_units_mode_changed()
        self._settle()
        self.assertEqual(len(self.app.results_nb.tabs()), before)

    def test_it_rewrites_the_newest_page_IN_PLACE(self):
        self._calc()
        rt = self.app._run_tabs[0]
        before = rt.text.get("1.0", tk.END)
        self.app.units_mode_var.set("aligned")
        self.app._on_units_mode_changed()
        self._settle()
        after = rt.text.get("1.0", tk.END)
        self.assertNotEqual(after, before, "the page did not re-render")
        self.assertEqual(after.count(run_headline(rt.run)), 1,
                         "the page grew a second copy of the report")

    def test_every_page_follows_the_unit_not_just_the_newest(self):
        """
        The unit is a RENDERING choice, not a recorded fact.

        This used to assert the opposite -- that an older page keeps the
        formatting it was written with -- and that was never what the user saw.
        _run_report_segments reads units_mode_var live and the next Calculate
        re-renders every page (so their banners name the current run), so the
        old page flipped to the new units one Calculate later without the user
        touching Units again.  What "leave it as recorded" actually bought was
        one screen showing two formattings and then a silent flip.
        """
        self._calc(2)
        older = self.app._run_tabs[1]
        before = older.text.get("1.0", tk.END)
        self.app.units_mode_var.set("aligned")
        self.app._on_units_mode_changed()
        self._settle()
        self.assertNotEqual(older.text.get("1.0", tk.END), before,
                            "the older page kept the previous unit formatting")
        # ... and it is a re-render, not an append.
        self.assertEqual(
            older.text.get("1.0", tk.END).count(run_headline(older.run)), 1,
            "the older page grew a second copy of the report")

    def test_the_pages_agree_with_each_other_after_a_units_switch(self):
        """Two pages of the same one-trace run must format its row the same
        way; the disagreement is what made this visible at all."""
        self._calc(2)
        self.app.units_mode_var.set("aligned")
        self.app._on_units_mode_changed()
        self._settle()

        def data_row(rt):
            for line in rt.text.get("1.0", tk.END).splitlines():
                if line.startswith(pkg_rlc_gui.RESULTS_SWATCH):
                    return line[line.index("]") + 1:]
            return ""

        rows = [data_row(rt) for rt in self.app._run_tabs]
        self.assertTrue(all(rows), "no data row on one of the pages")
        self.assertEqual(len(set(rows)), 1,
                         f"the pages disagree about the units: {rows}")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestExportNamesItsRun(_AppCase):

    def _export(self, path):
        real = pkg_rlc_gui.filedialog.asksaveasfilename
        pkg_rlc_gui.filedialog.asksaveasfilename = lambda **kw: str(path)
        try:
            self.app._on_export_csv()
        finally:
            pkg_rlc_gui.filedialog.asksaveasfilename = real
        self._settle()

    def test_the_header_names_the_run_the_numbers_came_from(self):
        import tempfile
        self._calc(2)
        d = tempfile.mkdtemp()
        path = Path(d) / "out.csv"
        self._export(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("# Run: #2 @ 0.100 GHz,", text)

    def test_reading_an_older_page_does_not_change_what_is_exported(self):
        """
        Export writes the CURRENT cached state, which is the newest run.  That
        is exactly why the older page carries the banner.
        """
        import tempfile
        self._calc(2)
        self.app.results_nb.select(self.app._run_tabs[1].frame)
        self._settle()
        d = tempfile.mkdtemp()
        path = Path(d) / "out.csv"
        self._export(path)
        self.assertIn("# Run: #2 ", path.read_text(encoding="utf-8"))

    def test_the_log_echo_names_the_run(self):
        import tempfile
        self._calc()
        mark = self.app.results_text.index("end-1c")
        d = tempfile.mkdtemp()
        self._export(Path(d) / "out.csv")
        self.assertIn("Exported CSV (run #1):",
                      self.app.results_text.get(mark, tk.END))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRunHistoryIsNotSaved(_AppCase):
    """Run history is computed output and is in-memory only."""

    def test_the_session_dict_carries_no_run_history(self):
        self._calc(3)
        data = self.app._session_dict(None)
        text = json.dumps(data)
        self.assertNotIn("run", data)
        self.assertNotIn("runs", data)
        for key in ("Run #", "changed since", "not this page"):
            self.assertNotIn(key, text)

    def test_no_trace_carries_a_run_field(self):
        self._calc()
        data = self.app._session_dict(None)
        for tr in data["traces"]:
            self.assertNotIn("run", tr)

    def test_the_controls_block_carries_no_run_state(self):
        """
        The point of this one is the ABSENCE of run history from the session,
        not the exact membership of the controls block -- run tabs are
        in-memory only and nothing about them may reach the file.

        It is pinned as an exact set anyway, because that is what makes a run
        field added here impossible to miss.  `results_view` joined it with the
        three results views: it is a RENDERING choice, exactly like
        `units_mode` beside it, and neither is a measurement.  Anything else
        appearing in this set is what this test is for.
        """
        data = self.app._session_dict(None)
        self.assertEqual(
            set(data["controls"]),
            {"rlc_freq_ghz", "fit_fmin_ghz", "fit_fmax_ghz", "fit_model",
             "units_mode", "results_view"})
        for key in set(data["controls"]):
            self.assertNotIn("run", key)

    def test_a_session_with_runs_still_encodes(self):
        self._calc(2)
        json.dumps(session_to_dict(files=self.app.files,
                                   traces=self.app.traces,
                                   controls={}, plot_state={}))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTabContextMenu(_AppCase):

    def test_a_click_on_a_tab_resolves_to_that_page(self):
        self._calc(2)
        self.app.deiconify()
        self.app.geometry("1200x800")
        self._settle()
        # The first run tab starts just right of the Log's.
        found = None
        for x in range(0, 400, 3):
            rt = self.app._run_tab_at(x, 8)
            if rt is not None:
                found = rt
                break
        self.assertIsNotNone(found, "no run tab was hit by the pixel scan")
        self.assertIn(found, self.app._run_tabs)

    def test_a_click_that_is_not_on_a_tab_resolves_to_nothing(self):
        self._calc()
        self.assertIsNone(self.app._run_tab_at(-50, -50))

    def test_the_menu_carries_the_sentence_the_button_cannot_afford(self):
        """
        The Keep BUTTON says 'Keep (5/5) — full' because at 150% DPI the
        1040x600 Results header clips anything longer.  That is only honest if
        the sentence exists somewhere the user can reach, and this menu -- the
        one the disabled button sends them to -- is not width-bound.
        """
        cap = self.app._kept_cap()
        for _ in range(cap):
            self._calc()
            self.app._keep_run_tab(self.app._run_tabs[0])
        self._calc()
        target = self.app._run_tabs[0]
        self.assertFalse(target.kept)
        self.app._sync_run_tab_menu(target)
        self._settle()
        self.assertIn("close a kept run first",
                      str(self.app._run_tab_menu.entrycget(0, "label")))

    def test_close_this_run_removes_exactly_one_page(self):
        self._calc(3)
        victim = self.app._run_tabs[1]
        self.app._run_tab_menu_target = victim
        self.app._on_menu_close_run()
        self._settle()
        self.assertNotIn(victim, self.app._run_tabs)
        self.assertEqual(len(self.app._run_tabs), 2)

    def test_close_this_run_closes_a_kept_one_too(self):
        """The only route by which a kept run is ever destroyed -- and it is
        what the disabled Keep button tells the user to do."""
        self._calc()
        rt = self.app._run_tabs[0]
        self.app._keep_run_tab(rt)
        self.app._run_tab_menu_target = rt
        self.app._on_menu_close_run()
        self._settle()
        self.assertEqual(self.app._run_tabs, [])

    def test_close_others_leaves_the_kept_ones_standing(self):
        self._calc()
        keeper = self.app._run_tabs[0]
        self.app._keep_run_tab(keeper)
        self._calc(2)
        target = self.app._run_tabs[0]
        self.app._run_tab_menu_target = target
        self.app._on_menu_close_other_runs()
        self._settle()
        self.assertIn(keeper, self.app._run_tabs)
        self.assertIn(target, self.app._run_tabs)
        self.assertEqual(len(self.app._run_tabs), 2)

    def test_closing_leaves_no_orphan_widget(self):
        self._calc(3)
        self.app._run_tab_menu_target = self.app._run_tabs[0]
        self.app._on_menu_close_other_runs()
        self._settle()
        self.assertEqual(len(self.app.results_nb.winfo_children()),
                         len(self.app.results_nb.tabs()))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRunsMenu(_AppCase):
    """Tk 8.6 ttk has no tab-strip scrolling and no overflow chevron, so this
    menu is how a compressed tab stays identifiable."""

    def test_it_lists_every_page_with_its_full_description(self):
        self._calc(2)
        self.app._rebuild_runs_menu()
        labels = [self.app._runs_menu.entrycget(i, "label")
                  for i in range(self.app._runs_menu.index("end") + 1)
                  if self.app._runs_menu.type(i) == "command"]
        for rt in self.app._run_tabs:
            self.assertTrue(any(run_headline(rt.run) in l for l in labels),
                            f"run #{rt.run.number} is not in the Runs menu")

    def test_with_no_runs_it_says_so_rather_than_being_empty(self):
        self.app._rebuild_runs_menu()
        self.assertIn("no runs yet",
                      self.app._runs_menu.entrycget(0, "label"))

    def test_it_carries_the_two_caps(self):
        self.app._rebuild_runs_menu()
        labels = [self.app._runs_menu.entrycget(i, "label")
                  for i in range(self.app._runs_menu.index("end") + 1)
                  if self.app._runs_menu.type(i) == "cascade"]
        self.assertEqual(len(labels), 2)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestHeaderLayoutAtTheMinsize(unittest.TestCase):
    """
    The Results header gained two widgets, and pack UNMAPS what does not fit,
    starting from the end -- so the Keep button is the one that would vanish.
    Measured at the 1040x600 minsize with the WIDEST label the button can take.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_every_header_widget_is_on_screen_with_the_widest_keep_label(self):
        app = App()
        try:
            app.geometry("1040x600")
            app.deiconify()
            app.update()
            app._keep_btn.configure(text=keep_button_label(5, 5, "full"))
            for _ in range(3):
                app.update_idletasks()
                app.update()
            header = app._keep_btn.master
            for w in header.winfo_children():
                self.assertTrue(
                    w.winfo_ismapped(),
                    f"{w.winfo_class()} fell off the Results header")
            self.assertLessEqual(
                header.winfo_reqwidth(), header.winfo_width(),
                "the Results header asks for more room than it has")
        finally:
            app.destroy()

    def test_the_keep_button_is_READABLE_at_150_percent_font_scaling(self):
        """
        winfo_ismapped() cannot see this failure.  With the long sentence on
        the button, measured with TkDefaultFont scaled 1.5x (the supported
        150% DPI) at the 1040x600 minsize: the header is 575 px, requests 687,
        and the Keep button -- packed last of five side=LEFT -- got the 213 px
        that were left and the text was clipped mid-phrase.  ismapped stayed 1
        the whole time.  'A disabled button with no reason on it is a bug
        report', and a reason cut in half is no reason.
        """
        import tkinter.font as tkfont
        app = App()
        try:
            app.geometry("1040x600")
            app.deiconify()
            app.update()
            f = tkfont.nametofont("TkDefaultFont", root=app)
            base = f.cget("size")
            f.configure(size=int(round(abs(base) * 1.5)) * (1 if base > 0 else -1))
            app._keep_btn.configure(text=keep_button_label(5, 5, "full"))
            for _ in range(4):
                app.update_idletasks()
                app.update()
            self.assertGreaterEqual(
                app._keep_btn.winfo_width(), app._keep_btn.winfo_reqwidth(),
                f"the Keep button's label is clipped: "
                f"{app._keep_btn.cget('text')!r} needs "
                f"{app._keep_btn.winfo_reqwidth()} px and has "
                f"{app._keep_btn.winfo_width()}")
            f.configure(size=base)
        finally:
            app.destroy()

    def test_thirty_run_pages_do_not_move_the_editor_canvas(self):
        """
        The same measurement stage C pinned for the tab strip, now driven by
        real run pages rather than empty frames.
        """
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
            w = app.results_text
            while w is not None and w.winfo_class() != "TPanedwindow":
                w = w.master
            outer = w.master
            left = outer.panes()[0]
            before = (outer.sashpos(0),
                      app.nametowidget(left).winfo_width(),
                      app._ed_canvas.winfo_width())
            self.assertEqual(before, (460, 460, 431))
            app._run_tabs_var.set(RUN_TABS_HARD_CAP)
            app._run_auto_var.set(RUN_TABS_HARD_CAP - 1)
            app._on_run_caps_changed()
            for i in range(30):
                app._add_run_tab(RunSnapshot(number=i + 1, when=_when(),
                                             marker_freq_hz=5e9))
            for _ in range(3):
                app.update_idletasks()
                app.update()
            after = (outer.sashpos(0),
                     app.nametowidget(left).winfo_width(),
                     app._ed_canvas.winfo_width())
            self.assertEqual(after, before,
                             f"a {app.results_nb.winfo_reqwidth()} px tab "
                             f"strip moved the left panel: {before} -> {after}")
        finally:
            app.destroy()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
