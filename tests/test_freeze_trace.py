"""
"Freeze as new trace" -- the before/after comparison.

A frozen trace is a snapshot: it keeps the numbers it was computed with, and
from then on Calculate skips it and the editor refuses to write it.  That is
what makes a comparison a comparison of CURVE SHAPES over the whole sweep
rather than of two numbers in a log -- but only if the snapshot really is
inert, so most of the tests here are about what does NOT happen to it.

  * THE COPY.  Config copied (lists element-wise -- the documented Duplicate
    aliasing bug), results REFERENCED (a deepcopy would carry megabytes, and
    _on_calculate assigns new arrays rather than writing into the old ones).
  * THE REFUSALS.  Calculate skips it but still reports its cached numbers;
    _sync_editor_to_trace declines outright and the editor greys itself out
    with a note saying why.
  * WHAT STILL WORKS.  It plots, it hides, it exports, Remove removes it.
  * THE SESSION.  Results are deliberately not in a session file, so a frozen
    trace comes back with its spec and no numbers.  It says so, in the Results
    pane and in the Traces list, instead of silently plotting nothing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_gui  # noqa: E402
from pkg_rlc_core import ConnectionRow, MeasPortRow, parse_touchstone  # noqa: E402
from pkg_rlc_gui import (  # noqa: E402
    ATTRIB_MENU_LABEL, COLORS, FREEZE_MENU_LABEL, FROZEN_EDITOR_NOTE,
    LINESTYLES, MAX_LABEL_LEN,
    UNFREEZE_MENU_LABEL, App, FileEntry, TraceConfig,
    _duplicate_trace_config, _freeze_stamp_of, _freeze_trace_config,
    freeze_label, freeze_refusal, trace_from_dict, trace_to_dict,
)

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


def _computed_trace(**kw) -> TraceConfig:
    """A trace that looks like Calculate has been over it."""
    tc = TraceConfig(id=1, file_label="f.s4p", mode=5, port_a="1",
                     label="tank", color_idx=3, ls_idx=1,
                     mports=[MeasPortRow("a", "1", "2")],
                     conn_rows=[ConnectionRow(ports="3", kind="ground")],
                     **kw)
    tc.Z = np.array([1 + 2j, 3 + 4j])
    tc.Zmat = np.zeros((2, 1, 1), dtype=complex)
    tc.rlc = object()
    tc.fit_freqs = np.array([1.0, 2.0])
    tc.fit_Z = np.array([1 + 0j, 2 + 0j])
    tc.mport_names = ["a"]
    return tc


# ============================================================================
# The copy (pure)
# ============================================================================


class TestFreezeConfigCopy(unittest.TestCase):
    def test_it_is_frozen_and_carries_a_new_id_and_a_stamp(self):
        src = _computed_trace()
        tc = _freeze_trace_config(src, 7, stamp="14:32")
        self.assertTrue(tc.frozen)
        self.assertEqual(tc.id, 7)
        self.assertEqual(tc.label, "tank <14:32>")
        self.assertFalse(src.frozen, "freezing changed the SOURCE")

    def test_the_source_keeps_its_own_id_and_label(self):
        src = _computed_trace()
        _freeze_trace_config(src, 7, stamp="14:32")
        self.assertEqual((src.id, src.label), (1, "tank"))

    def test_it_takes_the_next_colour_and_linestyle(self):
        """
        A snapshot drawn in its source's exact colour and dash is
        indistinguishable from it -- which defeats the one picture the whole
        feature exists to produce.  Both move, so the pair stays distinct even
        where the palette wraps onto a colour already in use.
        """
        src = _computed_trace()
        tc = _freeze_trace_config(src, 7, stamp="x")
        self.assertEqual(tc.color_idx, (src.color_idx + 1) % len(COLORS))
        self.assertEqual(tc.ls_idx, (src.ls_idx + 1) % len(LINESTYLES))
        self.assertNotEqual((tc.color_idx, tc.ls_idx),
                            (src.color_idx, src.ls_idx))

    def test_the_colour_wraps_instead_of_running_off_the_palette(self):
        src = _computed_trace()
        src.color_idx = len(COLORS) - 1
        src.ls_idx = len(LINESTYLES) - 1
        tc = _freeze_trace_config(src, 7, stamp="x")
        self.assertEqual(tc.color_idx, 0)
        self.assertEqual(tc.ls_idx, 0)

    def test_the_two_traces_share_no_list(self):
        """
        The documented Duplicate hazard: TraceConfig(**src.__dict__) is a
        shallow splat and hands both traces the same row list, so editing one
        silently edits the other and the only symptom is two curves agreeing.
        """
        src = _computed_trace()
        tc = _freeze_trace_config(src, 7, stamp="x")
        self.assertIsNot(tc.mports, src.mports)
        self.assertIsNot(tc.conn_rows, src.conn_rows)
        self.assertIsNot(tc.mports[0], src.mports[0])
        self.assertIsNot(tc.conn_rows[0], src.conn_rows[0])
        src.mports[0].plus = "9"
        src.conn_rows[0].ports = "9"
        self.assertEqual(tc.mports[0].plus, "1")
        self.assertEqual(tc.conn_rows[0].ports, "3")

    def test_the_results_are_referenced_not_copied(self):
        """
        Deliberately NOT a deepcopy: a 6x6 Zmat over 5000 frequencies is
        2.88 MB, and _on_calculate ASSIGNS new objects to these attributes on
        every run instead of writing into the arrays, so nothing can change
        them under the snapshot.
        """
        src = _computed_trace()
        tc = _freeze_trace_config(src, 7, stamp="x")
        for name in ("Z", "Zmat", "rlc", "fit_freqs", "fit_Z"):
            self.assertIs(getattr(tc, name), getattr(src, name), name)

    def test_a_recomputed_source_does_not_move_the_snapshot(self):
        """The assign-don't-mutate contract, stated as a test."""
        src = _computed_trace()
        tc = _freeze_trace_config(src, 7, stamp="x")
        keep = tc.Z
        src.Z = np.array([99 + 0j, 99 + 0j])        # what _on_calculate does
        np.testing.assert_array_equal(tc.Z, keep)
        self.assertEqual(tc.Z[0], 1 + 2j)

    def test_it_is_never_born_stale(self):
        src = _computed_trace()
        src.stale = True
        tc = _freeze_trace_config(src, 7, stamp="x")
        self.assertFalse(tc.stale)

    def test_duplicating_a_frozen_trace_does_not_produce_a_frozen_one(self):
        """
        Duplicate drops the results, and a frozen trace with no numbers is one
        Calculate will never fill in -- a dead row.  Duplicate means "carry on
        editing from here", which is the opposite of frozen.
        """
        tc = _freeze_trace_config(_computed_trace(), 7, stamp="x")
        copy = _duplicate_trace_config(tc, 8)
        self.assertFalse(copy.frozen)
        self.assertIsNone(copy.Z)


# ============================================================================
# The two refusals, and the label (pure)
# ============================================================================


class TestFreezeRefusal(unittest.TestCase):
    """
    A snapshot's whole contract is "this spec produced these numbers".  A
    frozen trace can never clear `stale` again -- _on_calculate skips it and
    _sync_editor_to_trace refuses it -- so a STALE trace frozen once is
    mislabelled forever, with nothing on screen saying so.
    """

    def test_a_computed_clean_trace_is_freezable(self):
        self.assertEqual(freeze_refusal(_computed_trace()), ())

    def test_a_trace_with_no_numbers_is_refused(self):
        tc = TraceConfig(id=1, label="t")
        title, msg = freeze_refusal(tc)
        self.assertIn("Nothing to freeze", title)
        self.assertIn("Calculate it first", msg)

    def test_a_STALE_trace_is_refused(self):
        """
        Measured on coupled_2port_gndref.s2p (port 1 = 0.6 ohm / 2 nH, port 2 =
        0.9 ohm / 3 nH): Calculate with Port A = 1, type '2' into Port A,
        freeze without recalculating, and the results table read
        '[ 2] coil <21:36>  M1: S:[2] G:[]  600 mOhm  2 nH ...' -- port 2's
        descriptor over port 1's numbers, a 50% error on L, and the same wrong
        pairing in the run page, the CSV and the plot legend.
        """
        tc = _computed_trace()
        tc.stale = True
        title, msg = freeze_refusal(tc)
        self.assertIn("Spec has changed", title)
        self.assertIn("Calculate it first", msg)

    def test_the_message_says_a_snapshot_can_never_catch_up(self):
        """The reason is what makes the refusal actionable rather than
        obstructive: this is not "later", it is "never"."""
        tc = _computed_trace()
        tc.stale = True
        self.assertIn("never", freeze_refusal(tc)[1])

    def test_an_already_frozen_trace_is_not_re_refused(self):
        """_on_freeze_trace returns before this for a frozen trace; the guard
        must not claim a snapshot is broken."""
        tc = _freeze_trace_config(_computed_trace(), 7, stamp="x")
        self.assertEqual(freeze_refusal(tc), ())


class TestFreezeLabelSurvivesTheLegend(unittest.TestCase):
    """
    pkg_rlc_plot truncates a legend entry to the FIRST MAX_LABEL_LEN
    characters, so a stamp appended to the end of a long label is exactly what
    head-truncation deletes.  The tool's own default label is
    f"{fe.label}_p1_to_gnd", so any file name of 20 characters already
    overflows.
    """

    LONG = "coupled_2port_gndref.s2p_p1_to_gnd"     # 34 chars, a real default

    def test_a_short_label_is_untouched(self):
        self.assertEqual(freeze_label("tank", "14:32"), "tank <14:32>")

    def test_the_snapshot_and_its_source_legend_differently(self):
        frozen = freeze_label(self.LONG, "21:29")
        self.assertNotEqual(frozen[:MAX_LABEL_LEN], self.LONG[:MAX_LABEL_LEN],
                            "the two curves get byte-identical legend entries")

    def test_the_stamp_itself_survives_truncation(self):
        frozen = freeze_label(self.LONG, "21:29")
        self.assertIn("<21:29>", frozen[:MAX_LABEL_LEN])

    def test_the_whole_label_fits_the_legend(self):
        for n in (5, 20, 29, 30, 34, 90):
            with self.subTest(n=n):
                self.assertLessEqual(len(freeze_label("x" * n, "21:29")),
                                     MAX_LABEL_LEN)

    def test_a_trimmed_base_says_it_was_trimmed(self):
        self.assertIn("…", freeze_label(self.LONG, "21:29"))

    def test_the_real_freeze_uses_it(self):
        src = _computed_trace()
        src.label = self.LONG
        tc = _freeze_trace_config(src, 7, stamp="21:29")
        self.assertNotEqual(tc.label[:MAX_LABEL_LEN],
                            src.label[:MAX_LABEL_LEN])


class TestFreezeStampOf(unittest.TestCase):
    def test_it_reads_the_stamp_back_off_the_label(self):
        self.assertEqual(_freeze_stamp_of("tank <14:32>"), "14:32")

    def test_a_renamed_snapshot_degrades_instead_of_raising(self):
        self.assertEqual(_freeze_stamp_of("my baseline"), "(unknown)")


# ============================================================================
# The session partition (pure)
# ============================================================================


class TestFrozenIsAConfigField(unittest.TestCase):
    def test_it_is_saved_not_computed(self):
        self.assertIn("frozen", pkg_rlc_gui._config_trace_fields())
        self.assertNotIn("frozen", pkg_rlc_gui._COMPUTED_TRACE_FIELDS)

    def test_it_round_trips_both_ways(self):
        for value in (True, False):
            with self.subTest(value=value):
                src = TraceConfig(id=1, frozen=value)
                back = trace_from_dict(trace_to_dict(src), lambda m: None)
                self.assertIs(back.frozen, value)

    def test_a_hand_edited_false_does_not_become_true(self):
        """_coerce_bool, not bool(): bool('false') is True."""
        back = trace_from_dict({"id": 1, "frozen": "false"}, lambda m: None)
        self.assertFalse(back.frozen)


# ============================================================================
# The list line (pure)
# ============================================================================


class TestFrozenInfoString(unittest.TestCase):
    def test_a_live_trace_carries_no_marker(self):
        self.assertNotIn("❄", TraceConfig(id=1, label="t").info_str())

    def test_a_frozen_trace_with_numbers_is_marked(self):
        tc = _freeze_trace_config(_computed_trace(), 7, stamp="x")
        line = tc.info_str()
        self.assertIn("❄", line)
        self.assertNotIn("no numbers", line)

    def test_a_frozen_trace_without_numbers_says_so(self):
        """
        The one place a trace that will plot nothing is visible before the next
        Calculate -- which is exactly the state a session reload leaves it in.
        """
        tc = TraceConfig(id=1, label="t", frozen=True)
        self.assertIn("❄ no numbers", tc.info_str())


# ============================================================================
# Tk-driven
# ============================================================================


class _Case(unittest.TestCase):
    """An App with one file and one calculated trace, selected."""

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
                              port_a="1", gnd_ports="2-4", label="t1")
        self.app.traces.append(self.tc)
        self.app._next_trace_id = 2
        self.app._refresh_trace_list()
        self._select(0)
        self.app._on_calculate()
        self._settle()

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

    def _freeze(self) -> TraceConfig:
        self.app._on_freeze_trace()
        self._settle()
        return self.app.traces[-1]

    def _results(self) -> str:
        return self.app.results_text.get("1.0", tk.END)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFreezingFromTheApp(_Case):
    def test_it_appends_a_second_trace_holding_the_same_numbers(self):
        frozen = self._freeze()
        self.assertEqual(len(self.app.traces), 2)
        self.assertTrue(frozen.frozen)
        np.testing.assert_array_equal(frozen.Z, self.tc.Z)

    def test_the_source_stays_selected(self):
        """
        Freezing is the first half of "now change something and look at the
        difference".  Jumping the editor to a copy the user is not going to
        edit would put the next keystroke in the wrong place.
        """
        frozen = self._freeze()
        self.assertEqual(self.app._sel_idx(self.app.traces_lb), 0)
        self.assertIs(self.app.traces[0], self.tc)
        self.assertIsNot(self.app.traces[0], frozen)

    def test_freezing_an_uncomputed_trace_is_refused_with_a_reason(self):
        tc2 = TraceConfig(id=9, file_label=self.fe.label, mode=1, port_a="2",
                          label="never_run")
        self.app.traces.append(tc2)
        self.app._refresh_trace_list()
        self._select(1)
        seen = []
        real = pkg_rlc_gui.messagebox.showinfo
        pkg_rlc_gui.messagebox.showinfo = lambda t, m, *a, **k: seen.append((t, m))
        try:
            self.app._on_freeze_trace()
            self._settle()
        finally:
            pkg_rlc_gui.messagebox.showinfo = real
        self.assertEqual(len(self.app.traces), 2, "it froze a trace with no data")
        self.assertEqual(len(seen), 1)
        self.assertIn("Calculate it first", seen[0][1])

    def test_an_edited_trace_cannot_be_frozen_until_it_is_recalculated(self):
        """
        End to end, through the editor, the way it actually happens: type a
        different port into the field, do NOT press Calculate, right-click ->
        Freeze.  _on_freeze_trace flushes the editor first, which guarantees
        the freshest -- and unmeasured -- spec is the one that would be copied.
        """
        self.app.ed_porta.set_value("2")
        self.app._flush_editor_sync()
        self._settle()
        self.assertTrue(self.tc.stale, "the edit did not mark the trace stale")
        seen = []
        real = pkg_rlc_gui.messagebox.showinfo
        pkg_rlc_gui.messagebox.showinfo = lambda t, m, *a, **k: seen.append((t, m))
        try:
            self.app._on_freeze_trace()
            self._settle()
        finally:
            pkg_rlc_gui.messagebox.showinfo = real
        self.assertEqual(len(self.app.traces), 1,
                         "it froze a spec that had never been measured")
        self.assertEqual(len(seen), 1)
        self.assertIn("Calculate it first", seen[0][1])

    def test_recalculating_makes_it_freezable_again(self):
        """The refusal has to be a step, not a wall."""
        self.app.ed_porta.set_value("2")
        self.app._flush_editor_sync()
        self._settle()
        self.app._on_calculate()
        self._settle()
        self.assertFalse(self.tc.stale)
        frozen = self._freeze()
        self.assertEqual(len(self.app.traces), 2)
        # And the snapshot's spec really is the one that produced its numbers.
        self.assertEqual(frozen.port_a, "2")
        self.assertFalse(frozen.stale)

    def test_it_lands_in_the_results_table_straight_away(self):
        """
        The table is where the two are read against each other.  A baseline
        that only appears at the next Calculate is a baseline nobody trusts.
        """
        self.app.results_text.delete("1.0", tk.END)
        frozen = self._freeze()
        body = self._results()
        self.assertIn(f"[{frozen.id:>2}] {frozen.label}", body)

    def test_freezing_does_not_recompute_anything(self):
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self._freeze()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(calls, [], "freezing re-ran the reduction")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestCalculateSkipsAFrozenTrace(_Case):
    def test_the_snapshot_survives_a_recompute_of_a_changed_source(self):
        """
        THE test.  Freeze, change the spec, Calculate: the snapshot must still
        hold the OLD curve, and the source must hold a different one -- or the
        comparison compares a curve with itself.
        """
        frozen = self._freeze()
        before = frozen.Z.copy()
        self.app.ed_porta.set_value("2")
        self.app.ed_gnd.set_value("1,3,4")
        self._settle()
        self.app._on_calculate()
        self._settle()
        np.testing.assert_array_equal(frozen.Z, before)
        self.assertFalse(np.array_equal(self.tc.Z, before),
                         "the source's numbers did not change, so this test "
                         "proves nothing")

    def test_the_reduction_runs_once_not_twice(self):
        frozen = self._freeze()
        self.assertIsNotNone(frozen.Z)
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_calculate()
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(len(calls), 1,
                         "the frozen trace was recomputed with the live one")

    def test_it_is_still_in_the_report(self):
        """
        Skipping the WORK, not the report -- the same rule "Calculate This
        Trace" follows.  A snapshot missing from the table it exists to be
        compared against is worse than useless.
        """
        frozen = self._freeze()
        self.app.results_text.delete("1.0", tk.END)
        self.app._on_calculate()
        self._settle()
        body = self._results()
        self.assertIn(f"[{frozen.id:>2}] {frozen.label}", body)
        self.assertIn(f"[{self.tc.id:>2}] {self.tc.label}", body)

    def test_asking_for_it_by_name_says_no_by_name(self):
        frozen = self._freeze()
        self.app.results_text.delete("1.0", tk.END)
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_calculate(only=frozen)
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(calls, [], "'Calculate This Trace' recomputed it")
        body = self._results()
        self.assertIn("frozen snapshot -- not recomputed", body)
        self.assertIn(UNFREEZE_MENU_LABEL, body)

    def test_a_full_calculate_does_not_clear_the_frozen_flag(self):
        frozen = self._freeze()
        self.app._on_calculate()
        self._settle()
        self.assertTrue(frozen.frozen)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheEditorCannotWriteAFrozenTrace(_Case):
    def setUp(self):
        super().setUp()
        self.frozen = self._freeze()
        self._select(1)             # the snapshot

    def test_typing_a_port_does_not_reach_it(self):
        self.app.ed_porta.set_value("3")
        self._settle()
        self.assertEqual(self.frozen.port_a, "1")

    def test_typing_a_label_does_not_reach_it(self):
        keep = self.frozen.label
        self.app.ed_label.set_value("renamed")
        self._settle()
        self.assertEqual(self.frozen.label, keep)

    def test_a_direct_sync_declines(self):
        """
        The guard is in _sync_editor_to_trace, not at its four call sites --
        the one that forgot would relabel a snapshot with whatever the editor
        happened to be showing.
        """
        self.app.ed_porta.set_value("4")
        self.app._sync_editor_to_trace(self.frozen)
        self.assertEqual(self.frozen.port_a, "1")

    def test_calculate_does_not_sync_it_either(self):
        self.app.ed_porta.set_value("3")
        self._settle()
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.frozen.port_a, "1")

    def test_the_note_is_on_screen_and_says_what_to_do(self):
        self.assertTrue(self.app.ed_frozen_note.winfo_manager(),
                        "the frozen note was not shown")
        self.assertIn(UNFREEZE_MENU_LABEL.lower(),
                      str(self.app.ed_frozen_note.cget("text")).lower())

    def test_the_fields_are_greyed_out(self):
        for w in self.app._ed_lockable:
            self.assertIn("disabled", w.state(), str(w))

    def test_both_tables_and_the_palette_are_locked(self):
        rows = self.app.ed_mp_table._rows
        self.assertTrue(rows)
        for w in rows[0]["_widgets"] + [self.app.ed_mp_table._add_btn]:
            self.assertIn("disabled", w.state(), str(w))
        before = self.app.ed_style.get()
        self.app.ed_style._choose(color=(before[0] + 5) % len(COLORS))
        self.assertEqual(self.app.ed_style.get(), before,
                         "the palette still changed the style")

    def test_the_note_costs_the_editor_nothing_it_cannot_afford(self):
        """
        It is a row of the FORM, which is inside a Canvas and is already many
        times taller than its viewport -- NOT a line in the footer, whose whole
        spare budget is one line and mode 5 already spends it.  Measured at the
        1040x600 minsize: the canvas stays mapped and the footer does not grow.
        """
        self.app.deiconify()
        self.app.geometry("1040x600")
        self._settle()
        foot = self.app._ed_foot
        button = [w for w in foot.winfo_children()
                  if w.winfo_class() == "TButton"][0]
        for mode in (1, 2, 3, 5, 6):
            with self.subTest(mode=mode):
                self.app.ed_mode_var.set(mode)
                self.app._on_mode_changed()
                self._settle()
                self.assertEqual(self.app._ed_canvas.winfo_ismapped(), 1,
                                 f"mode {mode}: the editor form disappeared")
                self.assertGreater(self.app._ed_canvas.winfo_height(), 0)
                self.assertEqual(button.winfo_ismapped(), 1)
                self.assertLessEqual(foot.winfo_reqheight(),
                                     button.winfo_reqheight() + 6,
                                     "the footer grew a row")

    def test_selecting_a_live_trace_puts_the_editor_back(self):
        self._select(0)
        self.assertFalse(self.app.ed_frozen_note.winfo_manager(),
                         "the frozen note stayed on a live trace")
        for w in self.app._ed_lockable:
            self.assertNotIn("disabled", w.state(), str(w))
        self.app.ed_porta.set_value("2")
        self._settle()
        self.assertEqual(self.tc.port_a, "2")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEverythingElseStillWorks(_Case):
    def setUp(self):
        super().setUp()
        self.frozen = self._freeze()

    def test_it_is_on_the_plot(self):
        labels = [t.label for t in self.app.plot.view.traces]
        self.assertIn(self.frozen.label, labels)
        self.assertIn(self.tc.label, labels)

    def test_show_hide_works_on_it(self):
        self._select(1)
        self.app._on_toggle_trace()
        self._settle()
        self.assertFalse(self.frozen.enabled)
        self.assertNotIn(self.frozen.label,
                         [t.label for t in self.app.plot.view.traces])
        self.app._on_toggle_trace()
        self._settle()
        self.assertTrue(self.frozen.enabled)

    def test_hiding_it_does_not_recompute(self):
        self._select(1)
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_toggle_trace()
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(calls, [])

    def test_it_is_exported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.csv")
            real = pkg_rlc_gui.filedialog.asksaveasfilename
            pkg_rlc_gui.filedialog.asksaveasfilename = lambda *a, **k: path
            try:
                self.app._on_export_csv()
            finally:
                pkg_rlc_gui.filedialog.asksaveasfilename = real
            body = Path(path).read_text(encoding="utf-8")
        self.assertIn(f"# Trace: {self.frozen.label}\n", body)
        self.assertIn(f"# Trace: {self.tc.label}\n", body)

    def test_remove_removes_it(self):
        self._select(1)
        self.app._on_remove_trace()
        self._settle()
        self.assertEqual([t.id for t in self.app.traces], [self.tc.id])

    def test_the_csv_does_not_attribute_its_numbers_to_the_newest_run(self):
        """
        Export writes the current cached state, which for every OTHER trace is
        the newest run -- but a frozen trace's numbers came from an earlier one
        and cannot be recomputed.  A before/after CSV is the only reason two
        such traces are in one file, and both blocks used to be headed
        '# Run: #2 @ 0.100 GHz, 21:34:14'.
        """
        import tempfile
        self.app._on_calculate()            # a second run, after the freeze
        self._settle()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.csv")
            real = pkg_rlc_gui.filedialog.asksaveasfilename
            pkg_rlc_gui.filedialog.asksaveasfilename = lambda *a, **k: path
            try:
                self.app._on_export_csv()
            finally:
                pkg_rlc_gui.filedialog.asksaveasfilename = real
            body = Path(path).read_text(encoding="utf-8")
        blocks = {}
        label = None
        for line in body.splitlines():
            if line.startswith("# Trace: "):
                label = line[len("# Trace: "):]
            elif line.startswith("# Run: ") and label is not None:
                blocks.setdefault(label, line)
        self.assertIn(self.frozen.label, blocks)
        self.assertIn(self.tc.label, blocks)
        self.assertIn("frozen snapshot", blocks[self.frozen.label])
        self.assertNotIn("frozen snapshot", blocks[self.tc.label])
        self.assertRegex(blocks[self.tc.label], r"# Run: #\d+")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheContextMenu(_Case):
    def test_the_traces_list_answers_a_right_click(self):
        self.assertTrue(self.app.traces_lb.bind("<Button-3>"),
                        "there is no way to reach the menu")

    def test_both_entries_are_on_it(self):
        """
        Freeze and Unfreeze are the FIRST TWO entries, in that order.

        Pinned as a prefix rather than as the whole list.  The menu grew a
        third entry (`Attribution…`) and `invoke(0)` / `invoke(1)` elsewhere in
        this class depend on the first two positions, not on there being
        exactly two -- an equality here turns every later addition into a
        failure in a file about freezing.  What still has to hold is the order
        and that no SEPARATOR was slipped in front of them: a separator has no
        `-label` at all and `entrycget(i, "label")` on one returns '', which
        this comparison catches.
        """
        labels = [self.app._trace_menu.entrycget(i, "label")
                  for i in range(self.app._trace_menu.index("end") + 1)]
        self.assertEqual(labels[:2], [FREEZE_MENU_LABEL, UNFREEZE_MENU_LABEL])
        self.assertIn(ATTRIB_MENU_LABEL, labels,
                      "the Attribution entry is gone from the trace menu")

    def test_only_the_applicable_entry_is_live(self):
        self.app._sync_trace_menu(self.tc)
        self.assertEqual(str(self.app._trace_menu.entrycget(
            FREEZE_MENU_LABEL, "state")), tk.NORMAL)
        self.assertEqual(str(self.app._trace_menu.entrycget(
            UNFREEZE_MENU_LABEL, "state")), tk.DISABLED)
        frozen = self._freeze()
        self.app._sync_trace_menu(frozen)
        self.assertEqual(str(self.app._trace_menu.entrycget(
            FREEZE_MENU_LABEL, "state")), tk.DISABLED)
        self.assertEqual(str(self.app._trace_menu.entrycget(
            UNFREEZE_MENU_LABEL, "state")), tk.NORMAL)

    def test_the_freeze_entry_actually_freezes(self):
        self.app._trace_menu.invoke(0)
        self._settle()
        self.assertEqual(len(self.app.traces), 2)
        self.assertTrue(self.app.traces[1].frozen)

    def test_the_unfreeze_entry_releases_it_and_wakes_the_editor(self):
        frozen = self._freeze()
        self._select(1)
        self.app._trace_menu.invoke(1)
        self._settle()
        self.assertFalse(frozen.frozen)
        self.assertFalse(self.app.ed_frozen_note.winfo_manager())
        self.app.ed_porta.set_value("2")
        self._settle()
        self.assertEqual(frozen.port_a, "2",
                         "the editor is still refusing an unfrozen trace")

    def test_an_unfrozen_trace_is_recomputed_again(self):
        frozen = self._freeze()
        self._select(1)
        self.app._trace_menu.invoke(1)
        self._settle()
        calls = []
        real = pkg_rlc_gui.compute_z
        pkg_rlc_gui.compute_z = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.app._on_calculate()
            self._settle()
        finally:
            pkg_rlc_gui.compute_z = real
        self.assertEqual(len(calls), 2)

    def test_unfreezing_says_the_numbers_are_about_to_be_replaced(self):
        self._freeze()
        self._select(1)
        self.app.results_text.delete("1.0", tk.END)
        self.app._trace_menu.invoke(1)
        self._settle()
        self.assertIn("REPLACE", self._results())

    def test_a_right_click_selects_the_row_under_the_pointer(self):
        """
        A menu that acts on whatever was selected BEFORE the click is how you
        freeze the wrong trace.
        """
        self._freeze()
        self._select(0)
        # A MAPPED window: Listbox.nearest() reads pixel geometry, and on a
        # withdrawn root every y answers row 0 -- which is the value this test
        # is trying to rule out, so it would pass without the code under test.
        self.app.deiconify()
        self.app.geometry("1040x600")
        self._settle()
        box = self.app.traces_lb.bbox(1)
        self.assertIsNotNone(box, "row 1 is not on screen; nothing to click")
        popped = []
        self.app._trace_menu.tk_popup = lambda *a, **k: popped.append(a)
        event = type("E", (), {"y": box[1] + box[3] // 2,
                               "x_root": 0, "y_root": 0})()
        self.app._on_trace_context_menu(event)
        self._settle()
        self.assertEqual(self.app._sel_idx(self.app.traces_lb), 1)
        self.assertEqual(len(popped), 1)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheSessionRoundTrip(_Case):
    def setUp(self):
        super().setUp()
        # Loading over a non-empty session asks first, and an unanswered modal
        # dialog is a hung test run, not a failing one.
        real = pkg_rlc_gui.messagebox.askyesno
        pkg_rlc_gui.messagebox.askyesno = lambda *a, **k: True
        self.addCleanup(setattr, pkg_rlc_gui.messagebox, "askyesno", real)

    def test_the_flag_survives_and_the_missing_numbers_are_reported(self):
        """
        A session file holds the CONFIG, never the results, so a frozen trace
        comes back with its spec and nothing to draw.  Chosen over dropping it
        on save because the SPEC is still worth having -- unfreeze and
        recompute reproduces the snapshot whenever the file has not changed --
        but it has to SAY so, in both places the user is looking.
        """
        import tempfile
        frozen = self._freeze()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.json")
            self.app._write_session(path, tmp)
            self.app.files = []
            self.app.traces = []
            self.app._trace_list_shown = []
            self.app._refresh_trace_list()
            self.app.results_text.delete("1.0", tk.END)
            self.assertTrue(self.app._load_session_file(path, "test"))
            self._settle()

        back = [t for t in self.app.traces if t.label == frozen.label]
        self.assertEqual(len(back), 1)
        self.assertTrue(back[0].frozen, "the frozen flag did not round-trip")
        self.assertIsNone(back[0].Z)
        body = self._results()
        self.assertIn("WITHOUT their numbers", body)
        self.assertIn(frozen.label[:18], body)
        self.assertIn(UNFREEZE_MENU_LABEL, body)
        lines = [self.app.traces_lb.get(i)
                 for i in range(self.app.traces_lb.size())]
        self.assertTrue(any("❄ no numbers" in ln for ln in lines),
                        f"the Traces list does not say it is empty: {lines}")

    def test_a_session_with_no_frozen_trace_says_nothing_about_them(self):
        """The report is a warning; a normal session must not carry it."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.json")
            self.app._write_session(path, tmp)
            self.app.results_text.delete("1.0", tk.END)
            self.app._load_session_file(path, "test")
            self._settle()
        self.assertNotIn("WITHOUT their numbers", self._results())


if __name__ == "__main__":
    unittest.main()
