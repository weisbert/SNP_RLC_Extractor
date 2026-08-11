"""
The Attribution window, END TO END, through the REAL application.

`tests/test_attrib_window.py` owns the window in isolation -- the pure
formatters, and the widget behaviour off a `TraceConfig` built by hand.  This
file owns the JOIN: the path a user actually walks, from `Add File` to a number
on the table, and every place where `pkg_rlc_gui`'s hooks and
`pkg_rlc_attrib_gui`'s window have to agree about what is going on.  Nothing
here constructs a `TraceConfig`, a `FileEntry` or an `AttribResult`: the file
is loaded through `_on_add_file`, the measurement ports are typed into the real
`RowTable`, and the window is opened through the menu entries the user clicks.
That is the whole point -- every defect this file exists to catch is one where
each half is right on its own.

THE WINDOW IS MAPPED, EVERYWHERE.  A withdrawn root answers 0 to every geometry
query and `Listbox.nearest()` then answers row 0 for every y -- which is
exactly the answer the right-click tests are trying to rule out, so they would
pass against a context menu that had no selection logic at all.  `App()` is
1.2 s with the fixture loaded and calculated (measured), so the classes below
are small and the parallel runner shards them apart.

WHAT IS COVERED, and why each one is here
-----------------------------------------
  * the whole path (menubar route), with a number read off the table checked
    against `pkg_rlc_attrib` called directly AND against the M the results pane
    printed from the completely separate `compute_z_matrix` path;
  * the right-click route, including that the click SELECTS the row under the
    pointer first -- a menu acting on the previous selection is how you
    attribute the wrong trace, and it is silent when it happens;
  * every refusal, by name: frozen, stale, one measurement port, no numbers
    yet, file not loaded, nothing selected;
  * rule 6 in full -- the window must NOT re-decompose while the spec is being
    edited (it would reconcile the new spec against the old authoritative
    total and blank its own table), and the staleness banner must appear;
  * rule 11 -- remove the trace, remove the file, load a session, each with the
    window open;
  * the coupling-block pointer in the Results pane, and its gate;
  * the session round trip of the window's choices, including that a
    hand-edited bad value costs its own field and never the file;
  * that none of this put a run, a result or a new `TraceConfig` field into the
    session file.

Every guard was mutation-checked; the mutation that turns it red is named in
the test's docstring.  The mutations were applied by monkeypatching in a
scratch process rather than by editing the modules under test, so nothing in
this repository was left mutated.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import pkg_rlc_attrib as at  # noqa: E402
import pkg_rlc_attrib_gui as ag  # noqa: E402
import pkg_rlc_gui  # noqa: E402
from pkg_rlc_core import format_si, parse_touchstone  # noqa: E402
from pkg_rlc_gui import App, TraceConfig, WARN_FG  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Two coupled coils.  Probes on 1 and 3, ports 2 and 4 grounded -- the same
#: configuration `tests/test_attrib_window.py` uses, and for the same reason:
#: measured at 5.1 GHz it decomposes M = 821 pH into a bare EM term (203 pH,
#: 24.68%) and two ground terms (413 pH / 50.26% and 206 pH / 25.06%), so a
#: table that dropped, reordered or mis-signed a row is visible in the text
#: rather than hidden in a rounding difference.
FIXTURE = "coupled_4port_diff.s4p"

#: The marker frequency every test calculates at.  On the grid of this fixture
#: (100 points, 100 MHz step), so nothing here has to reason about a snap.
FREQ_GHZ = "5.1"


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                    # pragma: no cover
        return False


TK_OK = _tk_ok()


# ============================================================================
# The harness
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _AppCase(unittest.TestCase):
    """
    A real App, MAPPED, with the fixture loaded through the real Add File path.

    Never shared between tests.  Every test below either measures live geometry
    or mutates application state (removes a trace, freezes one, edits a spec,
    loads a session over the top), and an App carried between those is the
    silent cross-contamination this repo refuses -- `run_parallel.py`'s header
    says so in as many words.

    Dialogs are captured rather than suppressed.  Half the assertions here are
    about WHICH refusal was shown, and an unanswered modal dialog in a test run
    is a hang, not a failure.
    """

    #: An INDEPENDENT parse of the fixture, once per class.  `_on_add_file`
    #: re-parses from disk itself -- that is the path under test -- so this
    #: copy exists only to be compared against what the App ended up holding
    #: (see `test_the_path_up_to_calculate_really_worked`), and to make a
    #: fixture problem report itself as a fixture problem rather than as a GUI
    #: failure three layers up.
    @classmethod
    def setUpClass(cls):
        cls.ts = parse_touchstone(FIXTURES / FIXTURE)

    def setUp(self):
        self.dialogs: list = []
        self._patch_dialogs()
        self.app = App()
        # MAPPED.  `Listbox.nearest()`, `bbox()` and every `winfo_*` geometry
        # query read pixels, and on a withdrawn root they answer 0 / None --
        # i.e. row 0 for every click, which is the wrong answer the right-click
        # tests exist to rule out.
        self.app.deiconify()
        self.app.geometry("1200x800")
        self._settle()
        self._add_file()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:                                # pragma: no cover
            pass

    # -- dialogs ---------------------------------------------------------

    def _patch_dialogs(self) -> None:
        """
        Capture every modal.

        `pkg_rlc_gui` and `pkg_rlc_attrib_gui` both do
        `from tkinter import messagebox`, so both names are the SAME module
        object and one patch covers both -- which is what makes
        `self.dialogs` a complete record of what the user was shown, whichever
        module put it there.
        """
        mb, fd = pkg_rlc_gui.messagebox, pkg_rlc_gui.filedialog
        self.assertIs(mb, ag.messagebox,
                      "the two modules no longer share tkinter.messagebox; "
                      "this harness would only be capturing half the dialogs")
        for name in ("showinfo", "showerror", "showwarning"):
            real = getattr(mb, name)
            self.addCleanup(setattr, mb, name, real)
            setattr(mb, name, self._recorder(name))
        real_yes = mb.askyesno
        self.addCleanup(setattr, mb, "askyesno", real_yes)
        # Loading a session over a non-empty one asks first, and an unanswered
        # dialog is a hung run.
        mb.askyesno = lambda *a, **k: (self.dialogs.append(("askyesno",) + a),
                                       True)[1]
        # Not patched here -- `_add_file` supplies its own path, and nothing
        # below saves through a dialog -- but registered for restoration all
        # the same, so a test that DOES patch one cannot leak it into the next
        # (these are attributes of the real `tkinter.filedialog`, shared by
        # every test in the process).
        for name in ("askopenfilenames", "asksaveasfilename"):
            real = getattr(fd, name)
            self.addCleanup(setattr, fd, name, real)

    def _recorder(self, kind: str):
        def show(title="", message="", **_kw):
            self.dialogs.append((kind, str(title), str(message)))
        return show

    def _last_dialog(self) -> str:
        self.assertTrue(self.dialogs, "no dialog was shown at all")
        return self.dialogs[-1][-1]

    # -- building the session --------------------------------------------

    def _settle(self, rounds: int = 8) -> None:
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _add_file(self, name: str = FIXTURE) -> None:
        """Through `_on_add_file`, not by appending a FileEntry.

        That path is what creates the default trace, fills the Files list, the
        editor combobox and the Results pane summary -- all of which the window
        then reads.  Constructing a FileEntry by hand skips every one of them.
        """
        pkg_rlc_gui.filedialog.askopenfilenames = \
            lambda *a, **k: (str(FIXTURES / name),)
        self.app._on_add_file()
        self._settle()

    def _select_trace(self, idx: int) -> None:
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _make_coupling(self, idx: int = 0, names=(("vic", "1"), ("agg", "3")),
                       gnd: str = "2,4", label: str = "coil") -> TraceConfig:
        """
        Turn trace `idx` into a coupling trace BY TYPING INTO THE EDITOR.

        The mode comes from the radiobutton's own `invoke()` and the
        measurement ports from `RowTable.add_row`, so this exercises
        `_on_mode_changed`, `_update_mode_visibility`, the deferred auto-apply
        and `_sync_editor_to_trace` -- the same route a user's keystrokes take.
        Poking `TraceConfig.mports` directly would skip all of it, and the
        editor is precisely where the "which trace did that edit land on"
        questions below are decided.
        """
        self._select_trace(idx)
        self.app._ed_mode_buttons[3].invoke()            # (1, 2, 3, 6, 5)
        self._settle()
        self.app.ed_mp_table.set_rows([])
        for name, plus in names:
            self.app.ed_mp_table.add_row({"name": name, "plus": plus,
                                          "minus": ""})
        self.app.ed_gnd.set_value(gnd)
        self.app.ed_label.set_value(label)
        self._settle()
        tc = self.app.traces[idx]
        self.assertEqual([(r.name, r.plus) for r in tc.mports],
                         [(n, p) for n, p in names],
                         "the editor did not reach the trace")
        return tc

    def _calculate(self) -> None:
        self.app.rlc_freq_var.set(FREQ_GHZ)
        self.app._on_calculate()
        self._settle()

    def _ready(self, **kw) -> TraceConfig:
        """The normal starting state: one calculated coupling trace."""
        tc = self._make_coupling(**kw)
        self._calculate()
        return tc

    # -- opening ----------------------------------------------------------

    def _analyze_menu(self) -> tk.Menu:
        """The Analyze cascade, found on the REAL menubar rather than by
        reaching for `app._analyze_menu` -- an attribute nobody can click."""
        menubar = self.app.nametowidget(self.app.cget("menu"))
        for i in range(menubar.index("end") + 1):
            # Index 0 is Tk's own tearoff entry on this platform and has no
            # -label at all; asking for one raises TclError.
            if menubar.type(i) != "cascade":
                continue
            if menubar.entrycget(i, "label") == "Analyze":
                return self.app.nametowidget(menubar.entrycget(i, "menu"))
        self.fail("there is no Analyze cascade on the menubar")

    def _open_from_menubar(self):
        self._analyze_menu().invoke(0)
        self._settle(10)
        return ag.live_windows(self.app)

    def _results(self) -> str:
        return self.app.results_text.get("1.0", tk.END)

    def _table(self, win) -> str:
        return win.table.get("1.0", "end-1c")


# ============================================================================
# The whole path, through the menubar
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheWholeUserPath(_AppCase):
    """Add File -> two measurement ports -> Calculate -> Analyze -> a number."""

    def test_the_path_up_to_calculate_really_worked(self):
        """
        Precondition, ASSERTED rather than assumed.

        Without it every test in this class would be silently exercising the
        refusal path instead of the thing it names -- which is exactly how a
        green suite comes to mean nothing.
        """
        tc = self._ready()
        self.assertEqual(len(self.app.files), 1)
        # It really is THIS file: `_on_add_file` parsed it from disk itself,
        # and the independent parse in setUpClass is what says so.
        self.assertEqual(self.app.files[0].ts.nports, self.ts.nports)
        self.assertEqual(len(self.app.files[0].ts.freqs), len(self.ts.freqs))
        self.assertIsNotNone(tc.Zmat, "Calculate produced no coupling matrix")
        self.assertEqual(list(tc.mport_names), ["vic", "agg"])
        self.assertFalse(tc.stale)
        self.assertIsNotNone(tc.coupling)

    def test_analyze_is_a_real_cascade_and_carries_exactly_the_one_entry(self):
        """
        Reached from the menubar widget, not from `app._analyze_menu`.

        Mutation: drop `menubar.add_cascade(label="Analyze", ...)` and keep the
        attribute -- every other test here still passes, because they all go
        through `_analyze_menu()`... which is why THIS one walks the real bar.
        """
        menu = self._analyze_menu()
        labels = [menu.entrycget(i, "label")
                  for i in range(menu.index("end") + 1)]
        self.assertEqual(labels, [ag.ATTRIB_MENU_LABEL])

    def test_the_entry_carries_no_accelerator(self):
        """
        Rule 9.  `bind_all` reaches every Toplevel -- measured elsewhere in
        this repo: Ctrl+S typed into a Toplevel Entry fires the App's
        `_on_save_config`.  An accelerator here would therefore also fire from
        INSIDE the Attribution window, opening a second window on whatever the
        main window happens to have selected.

        Mutation: add `accelerator="Ctrl+A"` to the menu entry.
        """
        self.assertEqual(self._analyze_menu().entrycget(0, "accelerator"), "")

    def test_the_menubar_entry_opens_the_window_on_the_selected_trace(self):
        """Mutation: point the entry's command at a no-op."""
        tc = self._ready()
        wins = self._open_from_menubar()
        self.assertEqual(len(wins), 1)
        self.assertIs(wins[0]._trace, tc)
        self.assertIsInstance(wins[0], ag.AttributionWindow)

    def test_the_table_has_one_row_per_declared_element_plus_the_bare_term(self):
        """
        Three rows: the bare EM coupling FIRST, then the two grounds the
        editor declared, strongest first (413 pH before 206 pH).

        Mutation: drop the bare term from `decompose`'s output, or let it be
        ranked with the others -- at 203 pH it would sort second and the
        baseline would read as just another correction.
        """
        self._ready()
        win = self._open_from_menubar()[0]
        rows = [ln for ln in self._table(win).splitlines()
                if ln.startswith(ag.ATTRIB_SWATCH)]
        self.assertEqual(len(rows), 3, self._table(win))
        self.assertIn("bare EM coupling", rows[0])
        self.assertIn("ground port 2", rows[1])
        self.assertIn("ground port 4", rows[2])

    def test_every_number_on_the_table_is_what_pkg_rlc_attrib_returns(self):
        """
        THE end-to-end assertion: the cells are `pkg_rlc_attrib`'s own numbers,
        formatted, and not something the window computed a second way.

        The reference decomposition is built here from the file and the
        termination the App resolved -- `_build_termination` is deliberately
        the App's, because it is the ONE place a TraceConfig becomes a
        TerminationSet and the one that passes `nports`; recopying it here
        would be testing a second implementation against a third.

        Measured on this fixture at 5.1 GHz: bare 203 pH, ground port 2
        413 pH, ground port 4 206 pH.

        Mutation: have `contributions_table` print `abs(contribution)` -- the
        signs survive because they are all positive here, but change the
        formatter's unit or drop the `format_si` call and this goes red.
        """
        tc = self._ready()
        fe = self.app.files[0]
        term = self.app._build_termination(tc, nports=fe.ts.nports)
        sources = pkg_rlc_gui._trace_role_rows(tc)[3]
        ctx = at.build_context(fe.Y, fe.ts.freqs, term,
                               float(FREQ_GHZ) * 1e9, sources=sources)
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertEqual(len(dec.terms), 3, "the fixture stopped decomposing")

        win = self._open_from_menubar()[0]
        body = self._table(win)
        for t in dec.terms:
            # The window forces a sign onto every numeric cell (rule 4) and
            # these are all positive, so the expected cell is '+' + the
            # engineering-unit rendering the results pane would use.
            cell = ag.PLUS + format_si(t.contribution.real, "H")
            row = [ln for ln in body.splitlines() if t.label in ln]
            self.assertEqual(len(row), 1, f"{t.label!r} is not on the table")
            self.assertIn(cell, row[0],
                          f"{t.label}: expected {cell!r} in {row[0]!r}")

    def test_the_total_is_the_M_the_results_pane_printed(self):
        """
        The window and the measurement pipeline must agree about the answer
        they are splitting up.

        `tc.coupling` comes from `_calculate_coupling_trace` ->
        `extract_coupling_at_freq` -> `compute_z_matrix`; the window's total
        comes from `attrib.decompose`, whose reference is a one-frequency slice
        through the same engine.  They are the same number to the bit
        (measured: both 8.209586719575929e-10), and that identity is what makes
        the decomposition a decomposition OF the reported M rather than of
        something adjacent to it.

        Mutation: give `compute_attribution` a different frequency (e.g. drop
        the `* 1e9`) -- the two stop matching immediately.
        """
        tc = self._ready()
        win = self._open_from_menubar()[0]
        pair = tc.coupling.pairs[0]
        self.assertEqual(win._res.dec.total_reference.real, pair.M_henry)
        # And it is on screen, in the strip that gates trust in the table.
        self.assertIn(ag.PLUS + format_si(pair.M_henry, "H"),
                      win.recon.cget("text"))

    def test_the_window_names_the_run_the_numbers_came_from(self):
        """
        Rule 6 / rule 12: the provenance is permanent and it is on screen.

        Mutation: freeze `run_number=0` in `compute_attribution`.
        """
        self._ready()
        self._calculate()                       # a second run, so #1 != #2
        win = self._open_from_menubar()[0]
        self.assertEqual(win._res.prov.run_number,
                         self.app._current_run_number())
        self.assertIn(f"from run #{self.app._current_run_number()}",
                      win.banner.cget("text"))

    def test_the_reconciliation_verdict_is_good_on_a_healthy_spec(self):
        """
        If this ever goes red the fixture or the engine moved, and every
        "the split is trustworthy" assumption in the file above it is void.
        Measured: rel diff 1.36e-13 against a floor of 4.25e-10.
        """
        self._ready()
        win = self._open_from_menubar()[0]
        text = win.recon.cget("text")
        self.assertIn("reconciled", text)
        self.assertNotIn("NOT reconciled", text)
        self.assertEqual(str(win.recon.cget("foreground")), "")


# ============================================================================
# The right-click route
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheRightClickRoute(_AppCase):
    """
    The second route, and the one that is already under the pointer.

    Every test here needs a MAPPED window: `Listbox.nearest()` reads pixel
    geometry and on a withdrawn root every y answers row 0, which is precisely
    the wrong answer being ruled out.
    """

    def _two_traces(self):
        """Two calculated coupling traces with DIFFERENT measurement ports.

        Different names, not just different labels: the window's Provenance
        carries victim/aggressor, so the pair alone says which trace was
        attributed even if the labels were somehow swapped.
        """
        a = self._make_coupling(0, names=(("vicA", "1"), ("aggA", "3")),
                                label="coilA")
        self.app._on_add_trace()
        self._settle()
        b = self._make_coupling(1, names=(("vicB", "3"), ("aggB", "1")),
                                label="coilB")
        self._calculate()
        self.assertIsNotNone(a.Zmat)
        self.assertIsNotNone(b.Zmat)
        return a, b

    def _right_click(self, row: int) -> list:
        """Post the context menu over `row`, capturing the popup."""
        box = self.app.traces_lb.bbox(row)
        self.assertIsNotNone(box, f"row {row} is not on screen; nothing to "
                                  f"click -- the window is not mapped")
        popped: list = []
        real = self.app._trace_menu.tk_popup
        self.addCleanup(setattr, self.app._trace_menu, "tk_popup", real)
        # tk_popup enters a nested event loop until the menu is dismissed; in
        # a test that is a hang, not a failure.
        self.app._trace_menu.tk_popup = lambda *a, **k: popped.append(a)
        event = type("E", (), {"y": box[1] + box[3] // 2,
                               "x_root": 0, "y_root": 0})()
        self.app._on_trace_context_menu(event)
        self._settle()
        self.assertEqual(len(popped), 1, "the menu was never posted")
        return popped

    def test_the_entry_is_on_the_traces_context_menu(self):
        """Mutation: remove the `add_command` from `_build_trace_menu`."""
        self._ready()
        menu = self.app._trace_menu
        labels = [menu.entrycget(i, "label")
                  for i in range(menu.index("end") + 1)]
        self.assertIn(ag.ATTRIB_MENU_LABEL, labels)
        self.assertTrue(self.app.traces_lb.bind("<Button-3>"),
                        "there is no way to reach the menu")

    def test_the_right_click_selects_the_row_under_the_pointer_first(self):
        """
        A menu that acts on whatever was selected BEFORE the click attributes
        the wrong trace, and says nothing while it does it.

        Mutation: delete the `selection_clear` / `selection_set` pair from
        `_on_trace_context_menu`.  Measured with that pair removed: the
        selection stays on row 0 and the window that opens describes coilA
        while the pointer is over coilB.
        """
        self._two_traces()
        self._select_trace(0)
        self._right_click(1)
        self.assertEqual(self.app._sel_idx(self.app.traces_lb), 1)

    def test_the_menu_attributes_the_clicked_trace_not_the_previous_one(self):
        """
        The same defect, followed all the way through to the window.

        This is the assertion that matters: the selection moving is only
        interesting because the window that opens is built from it.  Mutation:
        the same one as above -- the window comes up on 'vicA' <- 'aggA'.
        """
        _a, b = self._two_traces()
        self._select_trace(0)
        self._right_click(1)
        idx = [self.app._trace_menu.entrycget(i, "label")
               for i in range(self.app._trace_menu.index("end") + 1)
               ].index(ag.ATTRIB_MENU_LABEL)
        self.app._trace_menu.invoke(idx)
        self._settle(10)
        wins = ag.live_windows(self.app)
        self.assertEqual(len(wins), 1)
        self.assertIs(wins[0]._trace, b)
        # As a SET: `open_attribution_window` takes `names[0]` for the victim
        # and `resolve_meas_ports` orders by port index, not by the order the
        # rows were typed, so trace B (vicB on port 3, aggB on port 1) opens
        # aggB <- vicB.  Which of the two is the victim is not what this test
        # is about; that they are B's names and not A's is.
        self.assertEqual({wins[0]._res.prov.victim,
                          wins[0]._res.prov.aggressor}, {"vicB", "aggB"})
        self.assertEqual(wins[0]._res.prov.trace_label, "coilB")

    def test_the_entry_stays_LIVE_on_a_trace_it_is_going_to_refuse(self):
        """
        The identical decision, in the identical words, as the Freeze entry on
        a stale trace: "a greyed entry would be the same bug report".
        `attribution_refusal` names five different reasons and each of them
        tells the user what to do next, so the entry has to be reachable in
        order to say any of them.

        Mutation: `state=tk.DISABLED if <cannot run> else tk.NORMAL` in
        `_sync_trace_menu`.
        """
        tc = self._ready()
        self.app._on_freeze_trace()              # a trace it must refuse
        self._settle()
        frozen = self.app.traces[1]
        self.assertTrue(frozen.frozen)
        for target in (tc, frozen):
            with self.subTest(frozen=target.frozen):
                self.app._sync_trace_menu(target)
                self.assertEqual(
                    str(self.app._trace_menu.entrycget(
                        ag.ATTRIB_MENU_LABEL, "state")), tk.NORMAL)


# ============================================================================
# The refusals, by name
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRefusals(_AppCase):
    """
    Five reasons an attribution cannot run, and one for no selection.

    Each asserts BOTH halves: that the reason names itself in words the user
    can act on, and that no window opened.  Half of a refusal is worse than
    none -- a window that opens on numbers it cannot justify is the run
    snapshot bug all over again.

    Mutation for the whole class: make `attribution_refusal` return None.
    Measured, that turns SEVEN of the eight red on the "no window opened"
    half -- and the eighth, the mode-6 one, is the defect it is named after.
    """

    def _refuse(self) -> str:
        self.dialogs.clear()
        self.app._on_attribution()
        self._settle()
        self.assertEqual(ag.live_windows(self.app), [],
                         "a window opened on a trace that cannot be attributed")
        return self._last_dialog()

    def test_nothing_selected(self):
        self._ready()
        self.app.traces_lb.selection_clear(0, tk.END)
        self.assertIn("Select a trace", self._refuse())

    def test_a_frozen_trace(self):
        """A snapshot's numbers came from an EARLIER run and it can never be
        recalculated, so an attribution of it could only be stamped with the
        CURRENT run -- the mislabelling the frozen-trace CSV header exists to
        prevent, arriving through a different door."""
        self._ready()
        self.app._on_freeze_trace()
        self._settle()
        self._select_trace(1)
        self.assertTrue(self.app.traces[1].frozen)
        why = self._refuse()
        self.assertIn("frozen snapshot", why)
        self.assertIn(pkg_rlc_gui.UNFREEZE_MENU_LABEL, why,
                      "the refusal does not say how to get out of it")

    def test_a_stale_trace(self):
        """
        Rule 7.  The numbers and the spec beside them no longer describe each
        other, so a decomposition stamped with that run would carry the run's
        provenance over a different network.

        The edit goes through the editor, which is what makes `stale` mean what
        the user can see: auto-apply is deferred to `after_idle`, and
        `open_attribution_window` flushes it before asking.
        """
        tc = self._ready()
        self.app.ed_gnd.set_value("2")
        self._settle()
        self.assertTrue(tc.stale, "the edit did not mark the trace stale")
        self.assertIn("has been edited since it was last calculated",
                      self._refuse())

    def test_a_trace_with_no_numbers_yet(self):
        self._make_coupling()                    # built, never calculated
        self.assertIsNone(self.app.traces[0].Zmat)
        why = self._refuse()
        self.assertIn("no numbers yet", why)
        self.assertIn("Calculate", why)

    def test_a_trace_whose_file_is_not_loaded(self):
        """`_file_by_label` returns None, so there is no Y matrix to
        decompose.  Reached by re-pointing the trace, which is exactly what a
        hand-edited session file does."""
        tc = self._ready()
        tc.file_label = "somewhere_else.s4p"
        why = self._refuse()
        self.assertIn("somewhere_else.s4p", why)
        self.assertIn("not loaded", why)

    def test_one_measurement_port_says_there_is_no_pair(self):
        """
        Z_ab is a MUTUAL impedance; one probe is not a pair.

        Reached through modes 1/2/3/5, where `_on_calculate` takes the
        `compute_z` path and leaves `Zmat` at None.  The default trace this
        fixture loads with is a mode-1 one, so this is the shape a user
        actually arrives in.
        """
        self._select_trace(0)
        self._calculate()
        tc = self.app.traces[0]
        self.assertIsNotNone(tc.Z)
        self.assertIsNone(tc.Zmat)
        why = self._refuse()
        self.assertIn("one measurement port", why)
        self.assertIn("Mode 6", why, "the refusal does not say what to do")

    def test_one_measurement_port_in_MODE_6_is_also_turned_away(self):
        """
        The same shortfall, reached the other way, and it is NOT the same
        message.

        A mode-6 trace with one measurement port still takes the coupling path
        (`if tc.mode == 6 or n_mports > 1`), so `_calculate_coupling_trace`
        leaves a (F, 1, 1) `Zmat` behind and `attribution_refusal` -- which
        tests `Zmat is None` -- lets it through.  What refuses it is
        `open_attribution_window`'s `len(names) < 2` backstop, whose message
        describes an internal inconsistency ("fewer than two measurement port
        names cached. Calculate it again.") that is not what happened and whose
        advice cannot help.

        FIXED: `attribution_refusal` now tests the measurement-port COUNT as
        well as `Zmat is None`, so both routes to the same shortfall -- one
        port in modes 1/2/3/5 (Zmat is None) and one port in mode 6 (a real
        1x1) -- reach the one message that names the actual problem.  This test
        now pins that wording, and the backstop it used to hit is unreachable.

        Mutation: drop `or n_names < 2` from the condition in
        `attribution_refusal` and the "cached" assertion below goes red;
        `attribution_refusal` -> None turns the whole class red, this test
        included, which it did NOT before the fix.
        """
        self._make_coupling(names=(("vic", "1"),))
        self._calculate()
        tc = self.app.traces[0]
        self.assertIsNotNone(tc.Zmat)
        self.assertEqual(tc.Zmat.shape[1:], (1, 1))
        why = self._refuse()
        self.assertIn("only one measurement port", why)
        self.assertIn("victim AND an aggressor", why)
        self.assertNotIn("cached", why,
                         "it is still hitting the internal-inconsistency "
                         "backstop, whose advice cannot help")

    def test_a_refusal_leaves_the_application_usable(self):
        """
        A refusal is not an error path: the trace, the plot and the Results
        pane must be exactly as they were.  Mutation: have
        `open_attribution_window` clear `tc.Zmat` on the way out.
        """
        tc = self._ready()
        before = (tc.Zmat.copy(), self._results())
        self.app.ed_gnd.set_value("2")
        self._settle()
        self._refuse()
        self.assertIsNotNone(tc.Zmat)
        self.assertTrue((tc.Zmat == before[0]).all())
        self.assertIn("=== Calculate @", self._results())


# ============================================================================
# Rule 6: the window must not re-decompose while the spec is being edited
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _Rule6Case(_AppCase):
    """
    An open window, a spec that can be edited, and a count of every
    `decompose` the edit provokes.

    Two concrete classes below, for the same makespan reason the session case
    is split: `run_parallel.py` shards by CLASS and the run's floor is its
    slowest one (measured at 172 s), so a nine-test Tk class here is one test
    away from being what the whole suite waits for.
    """

    def setUp(self):
        super().setUp()
        self.tc = self._ready()
        self.win = self._open_from_menubar()[0]
        self.calls: list = []
        real = at.decompose

        def counted(*a, **k):
            self.calls.append(1)
            return real(*a, **k)

        # `pkg_rlc_attrib_gui` calls `attrib.decompose(...)`, i.e. it looks the
        # attribute up on the module at call time, so patching the module
        # reaches it.  `at` and `ag.attrib` are the same object.
        self.addCleanup(setattr, at, "decompose", real)
        at.decompose = counted

    def _edit(self) -> None:
        """One real edit, through the editor, exactly as a keystroke arrives."""
        self.app.ed_gnd.set_value("2")
        self._settle()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRule6NoAutoRefresh(_Rule6Case):
    """
    The one that would have shipped broken.

    `tc.Zmat` is written ONLY by `_on_calculate`.  Editing the spec sets
    `tc.stale` and leaves the numbers at the PREVIOUS run's, so a window that
    refreshed itself from `_apply_editor_strips` would decompose the NEW spec
    on the first keystroke, reconcile it against the OLD authoritative total --
    a residual not of 1e-13 but of however much the edit changed -- and then
    withhold its own table.  The documented behaviour of the auto-refresh
    design is a window that erases itself while you type.

    What the hook MAY do is one Label write.
    """

    def test_the_counter_can_count(self):
        """
        Precondition for every assertion in this class AND in
        `TestRule6TheBannerAndRecompute`: without it they are all assertions
        that a broken patch produced no calls.  Mutation: patch the wrong
        module.
        """
        self.win._on_recompute()
        self._settle()
        self.assertEqual(len(self.calls), 1)

    def test_an_edit_does_not_re_decompose(self):
        """
        Mutation: make `refresh_attribution_windows` call `w._on_recompute()`
        (or default `rerender=True` and have `_render` recompute).  Measured
        with the recompute wired in: one `decompose` per keystroke, against a
        `tc.Zmat` from the previous run.
        """
        self._edit()
        self.assertTrue(self.tc.stale, "the edit never reached the trace")
        self.assertEqual(self.calls, [],
                         "the window re-decomposed on a keystroke")

    def test_the_result_object_is_not_even_replaced(self):
        """Identity, not equality: an `AttribResult` rebuilt from the same
        numbers would pass a value comparison and still be the bug."""
        before = self.win._res
        self._edit()
        self.assertIs(self.win._res, before)

    def test_the_table_is_byte_identical_after_an_edit(self):
        """Mutation: any of the above.  This is the user-visible half -- the
        numbers on screen must not move because a character was typed."""
        before = self._table(self.win)
        self._edit()
        self.assertEqual(self._table(self.win), before)

    def test_the_window_does_not_blank_itself(self):
        """
        The specific failure the auto-refresh design produces: the
        reconciliation strip flips to "NOT reconciled" and the table is
        withheld, because the new spec was reconciled against the old total.

        Mutation: as above.
        """
        self._edit()
        recon = self.win.recon.cget("text")
        self.assertIn("reconciled", recon)
        self.assertNotIn("NOT reconciled", recon)
        self.assertIn("ground port 2", self._table(self.win))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRule6TheBannerAndRecompute(_Rule6Case):
    """
    The other half of rule 6: what the editor hook IS allowed to do, and what
    [Recompute] does once the user presses it.
    """

    def test_the_staleness_banner_appears_and_is_a_warning(self):
        """
        The ONE thing the editor hook writes, and what makes [Recompute]
        honest.

        Mutation A: drop `refresh_attribution_windows(self)` from
        `_apply_editor_strips`.  Mutation B: drop the
        `or bool(attribution_windows(self))` clause from `_strips_wanted` --
        measured, that clause is load-bearing here and not redundant with the
        other two, because an attribution needs two measurement ports and so
        its trace is a MODE 6 one, which `mode == 5 or Ports&Roles open` does
        not cover.  With B applied the banner still read
        "from run #1 @ 5.1 GHz" with no warning while `tc.stale` was already
        True.
        """
        self.assertNotIn("EDITED", self.win.banner.cget("text"))
        self.assertEqual(str(self.win.banner.cget("foreground")), "")
        self._edit()
        banner = self.win.banner.cget("text")
        self.assertIn("EDITED", banner)
        self.assertIn("Recompute", banner, "it does not name the way out")
        self.assertEqual(str(self.win.banner.cget("foreground")), WARN_FG)

    def test_recompute_is_what_re_decomposes_and_it_moves_the_numbers(self):
        """
        [Recompute] is deliberately ALLOWED on an edited spec: refusing would
        mean a full Calculate of every trace just to re-attribute, which is
        hostile in the one workflow this window exists for.

        Grounding port 2 alone instead of 2 and 4 removes a 206 pH term, so
        this is a change the table cannot hide.
        """
        self._edit()
        before = self._table(self.win)
        self.win._on_recompute()
        self._settle()
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(self._table(self.win), before)
        self.assertNotIn("ground port 4", self._table(self.win))

    def test_a_recomputed_block_stops_claiming_the_run(self):
        """
        Rule 12.  The plot, the results table and Export CSV are still showing
        the run; this block is not.  The frozen-trace CSV precedent is explicit
        that a block attributed to the wrong run is a real bug.

        Mutation A: hard-code `spec_matches_run=True` in
        `compute_attribution`.  Mutation B: drop the `if not
        prov.spec_matches_run` branch from `staleness_text` -- the banner then
        goes back to a bare "from run #1 @ 5.1 GHz" in the theme foreground
        while M on the table is 407 pH against the 821 pH the plot, the results
        table and Export CSV are all still showing.  Measured: 2.0x, silent.

        The banner is checked here as well as the report because
        `_on_recompute`'s own docstring promises all three, and because after a
        Recompute the live signature EQUALS the one just stored -- so the
        existing `spec_signature != prov.signature` route cannot fire, and
        nothing else was consulting `spec_matches_run`.
        """
        self._edit()
        self.win._on_recompute()
        self._settle()
        self.assertFalse(self.win._res.prov.spec_matches_run)
        report = self.win._report()
        self.assertIn("EDITED", report)
        self.assertIn("Export CSV still show the run", report)
        banner = self.win.banner.cget("text")
        self.assertIn("AS EDITED", banner)
        self.assertIn("#1", banner, "the banner does not name the run it is "
                                    "no longer describing")
        self.assertEqual(str(self.win.banner.cget("foreground")), WARN_FG)

    def test_RECOMPUTE_flushes_the_queued_editor_sync_first(self):
        """
        The documented "Auto-sync editor on Calculate" invariant, on THE button
        the module's own docstring calls "Nothing else recomputes anything".

        Auto-apply is deferred to `after_idle`, so a keystroke in the same
        event burst as the click is still in the idle queue.  Measured with the
        trace calculated at `gnd_ports = "2,4"`: typing "2" into the GND field
        and pressing Recompute in the same burst decomposed the OLD spec -- the
        table came back with `ground port 2` AND `ground port 4` -- and only
        then did the queued sync land, after which the banner said "the spec
        has been EDITED since — press Recompute", pointing at the very edit
        Recompute was supposed to have picked up.

        `_settle()` is deliberately NOT called between the edit and the click:
        that is the whole hazard.

        Mutation: drop `self.app._flush_editor_sync()` from `_on_recompute`.
        """
        self.app.ed_gnd.set_value("2")
        self.assertIsNotNone(self.app._ed_sync_after,
                             "the sync is not deferred, so this test cannot "
                             "exercise the hazard it is about")
        self.assertEqual(self.tc.gnd_ports, "2,4",
                         "the edit reached the trace early; nothing to flush")
        self.win._on_recompute()
        table = self._table(self.win)
        self.assertIn("ground port 2", table)
        self.assertNotIn("ground port 4", table,
                         "Recompute decomposed the spec from before the "
                         "keystroke")

    def test_typing_after_the_window_is_closed_raises_nothing_at_all(self):
        """
        `_strips_wanted` grew a clause and `_apply_editor_strips` grew a call,
        and both now run on the per-keystroke path.  An exception raised there
        does NOT reach this test through `update()` -- Tk hands it to
        `report_callback_exception`, which prints it to a console a
        double-clicked GUI does not have and then carries on.  So the assertion
        has to be on that hook, or a hook that raises on every keystroke looks
        exactly like a hook that works.

        Mutation: leave the window in `_LIVE` after it is destroyed (a no-op
        `_on_destroy`) and drop the `winfo_exists` prune from `live_windows`.
        Measured with that pair applied: one TclError per keystroke, the test
        suite green, and nothing on screen.
        """
        errors: list = []
        self.app.report_callback_exception = \
            lambda *a: errors.append(a)              # instead of printing it
        self.win.destroy()
        self._settle()
        self.assertEqual(ag.live_windows(self.app), [])
        self._edit()
        self.assertEqual(errors, [], f"the editor path raised: {errors}")
        self.assertTrue(self.tc.stale, "the edit no longer reaches the trace")


# ============================================================================
# Rule 11: the window outlives its subject
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRule11OutlivesItsSubject(_AppCase):
    """
    Unlike `PortRolesWindow`, this window HOLDS a result -- it cannot re-read
    `app.traces` and degrade, so it has to be told.  Same class of omission as
    the documented `_on_remove_file` forgot-to-replot bug: nothing raises, and
    the window carries on naming a trace that is gone with a [Recompute] button
    that would answer about nothing.

    Mutation for the three orphaning tests: replace `pkg_rlc_gui`'s
    `refresh_attribution_windows` with a no-op.  Measured, all three go red on
    the banner assertion -- i.e. no other path (not the editor strips, not the
    replot, not the trace-list rebuild) was quietly doing the job, which is the
    thing worth knowing.  The last two tests here carry their own.
    """

    def setUp(self):
        super().setUp()
        self.tc = self._ready()
        self.win = self._open_from_menubar()[0]
        self.before = self._table(self.win)
        self.assertIn("ground port 2", self.before)
        self.assertEqual(self.win.recompute_btn.state(), ())

    def _assert_orphaned(self) -> None:
        """Alive, honest, and still holding the numbers it computed."""
        self.assertTrue(self.win.winfo_exists(), "the window was destroyed")
        banner = self.win.banner.cget("text")
        self.assertIn("REMOVED", banner)
        self.assertIn("nothing here can be recomputed", banner)
        self.assertEqual(str(self.win.banner.cget("foreground")), WARN_FG)
        # Disabled, because there is nothing left to recompute FROM.  A live
        # button that silently does nothing is the same bug report.
        self.assertIn("disabled", self.win.recompute_btn.state())
        # The numbers are a record; they do not evaporate with their subject.
        self.assertEqual(self._table(self.win), self.before)

    def test_removing_the_trace_reaches_the_window(self):
        self._select_trace(0)
        self.app._on_remove_trace()
        self._settle()
        self.assertEqual(self.app.traces, [])
        self._assert_orphaned()

    def test_removing_the_FILE_reaches_the_window(self):
        """`_on_remove_file` drops the file's traces too, so this arrives by
        both routes at once -- and before the hook existed it arrived by
        neither."""
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(0)
        self.app._on_remove_file()
        self._settle()
        self.assertEqual(self.app.files, [])
        self._assert_orphaned()

    def test_loading_a_session_reaches_the_window(self):
        """
        A session load replaces every trace wholesale, so the object this
        window holds is not in `app.traces` any more whatever the new session
        calls its traces.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.json")
            self.app._write_session(path, tmp)
            self.assertTrue(self.app._load_session_file(path, "test"))
            self._settle()
        self.assertTrue(self.app.traces, "the session came back empty")
        self.assertFalse(any(t is self.tc for t in self.app.traces))
        self._assert_orphaned()

    def test_recompute_on_an_orphan_says_so_instead_of_raising(self):
        """A disabled button can still be invoked from code, and
        `refresh_banner` runs from a variable trace where a raise reaches no
        handler anyone controls."""
        self._select_trace(0)
        self.app._on_remove_trace()
        self._settle()
        self.win._on_recompute()                       # must not raise
        self._settle()
        self.assertIn("no longer loaded", self.win.recon.cget("text"))
        self.assertEqual(self._table(self.win), self.before)

    def test_the_autosave_still_happens_with_a_window_open(self):
        """
        The autosave runs inside WM_DELETE_WINDOW, and it now serialises the
        open windows' state on the way out.

        The assertion is that the FILE APPEARS, not merely that `_on_close`
        did not raise -- `_autosave_session` swallows everything on purpose (a
        raise there is an application that cannot be closed), so a broken
        `_attribution_state` does not crash the exit, it silently costs the
        user the whole session file.  Mutation: make `_attribution_state`
        raise; measured, `_on_close` still returns and `auto.json` is simply
        not there.
        """
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "auto.json"
            real = pkg_rlc_gui.autosave_path
            self.addCleanup(setattr, pkg_rlc_gui, "autosave_path", real)
            pkg_rlc_gui.autosave_path = lambda: target
            self.app._on_close()                       # must not raise
            self.assertTrue(target.exists(), "the autosave was lost")
            data = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("attribution", data)
        self.assertEqual(len(data["attribution"]["windows"]), 1)
        # The App is gone; tearDown's destroy() is a no-op after this.


# ============================================================================
# The pointer under the coupling block
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheCouplingBlockPointer(_AppCase):
    """
    The third route, and the one that reaches the user who is staring at
    "M = 821 pH" wondering where it came from.  The "Show Ports needed five
    pointers before anyone found it" history is what says an analysis window
    nobody can find is an analysis window nobody uses.
    """

    NEEDLE = "where each M above comes from"

    def test_it_is_in_the_results_pane_after_a_coupling_run(self):
        """Mutation: delete the `segs.append(...)` in
        `_run_report_segments`."""
        self._ready()
        self.assertIn(self.NEEDLE, self._results())

    def test_it_names_the_menu_entry_and_both_routes_to_it(self):
        """
        A pointer that names a menu entry which does not exist is worse than
        no pointer.  `ATTRIB_MENU_LABEL` is interpolated rather than spelled
        out, and this is the guard on that staying true.

        Mutation: write "Attribution..." (ASCII dots) into the segment.
        """
        self._ready()
        body = self._results()
        self.assertIn(f"Analyze → {ag.ATTRIB_MENU_LABEL}", body)
        self.assertIn("right-click menu", body)

    def test_it_is_on_the_run_PAGE_too(self):
        """
        `_run_report_segments` is the ONE builder of a run's report, consumed
        by the Log and by the run page.  A second copy would let the two
        disagree about a run's contents with nothing to tell them apart.

        Mutation: emit the line from `_render_results` instead -- the Log keeps
        it and the page silently loses it.
        """
        self._ready()
        page = self.app._run_tabs[-1].text.get("1.0", tk.END)
        self.assertIn(self.NEEDLE, page)

    def test_a_run_with_no_coupling_at_all_does_not_carry_it(self):
        """It is gated on a PAIR existing.  A mode-1 run has no coupling block
        and no pair, and a pointer there would send the user to a refusal.

        Mutation: drop the `if any(b.cres.pairs ...)` gate."""
        self._select_trace(0)
        self._calculate()
        self.assertIsNone(self.app.traces[0].Zmat)
        self.assertNotIn(self.NEEDLE, self._results())

    def test_a_single_measurement_port_block_does_not_point_at_a_refusal(self):
        """
        The gate is on the PAIR, not on the block, and this is why.  A mode-6
        trace with one measurement port DOES produce a coupling block -- it
        prints "(only one measurement port ...)" -- and
        `attribution_refusal` turns that trace away.  Pointing at a refusal is
        worse than not pointing at all.

        Mutation: gate on `shown_blocks` instead of on `b.cres.pairs`.
        """
        self._make_coupling(names=(("vic", "1"),))
        self._calculate()
        body = self._results()
        self.assertIn("only one measurement port", body,
                      "the fixture no longer produces the block this is about")
        self.assertNotIn(self.NEEDLE, body)

    def test_the_pointer_does_not_badge_the_log(self):
        """It is LOG_INFO: an invitation is not a warning.  Mutation: pass
        LOG_WARN.  (The Log is tab 0 and selected at startup, so the count is
        only incremented for a tab the user is not looking at -- hence the
        deliberate switch away first.)"""
        self._ready()
        self.app._select_results_tab(self.app._run_tabs[-1].frame)
        self._settle()
        self.app._log_unseen = 0
        self._calculate()
        self.assertEqual(self.app._log_unseen, 0,
                         "the pointer (or something beside it) badged the Log")


# ============================================================================
# The session file
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _SessionCase(_AppCase):
    """
    Shared save / load plumbing.

    Split into two concrete classes below because `run_parallel.py` shards by
    CLASS and the floor of the run is the slowest one: measured under the full
    8-way suite, these nine tests as one class were 168 s against the run's
    172 s floor (`test_mode5_editor.TestEditorLayout`), i.e. one more test here
    would have made this file the thing everyone waits for.  The split is along
    the seam that was already in the docstring -- what is written, and what a
    hand-edited file costs.
    """

    #: Exactly what `attribution_session_state` claims to keep.  Pinned as a
    #: SET so a new key has to be a deliberate edit here.
    #:
    #: `candidates` is on the list because it is the one field here the user
    #: TYPED and that this tool refuses to guess -- STRUCTURAL_CANDIDATES'
    #: docstring says in as many words that anything past "open" and "ideal" is
    #: the user's to name.  It was missing from the first version, so the round
    #: trip handed back the default pair and the only part of the state that
    #: cost anyone any thought was the only part not kept.
    KEYS = {"trace_id", "victim", "aggressor", "quantity", "freq_ghz", "view",
            "candidates"}

    def _save(self, tmp: str) -> dict:
        path = Path(tmp) / "s.json"
        self.app._write_session(str(path), tmp)
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_load(self, mutate=None) -> str:
        """Save, optionally corrupt the file by hand, load, return the pane."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            self.app._write_session(str(path), tmp)
            if mutate is not None:
                raw = json.loads(path.read_text(encoding="utf-8"))
                mutate(raw)
                path.write_text(json.dumps(raw), encoding="utf-8")
            self.app.results_text.configure(state=tk.NORMAL)
            self.app.results_text.delete("1.0", tk.END)
            self.assertTrue(self.app._load_session_file(str(path), "test"),
                            "the session file would not load at all")
            self._settle()
        return self._results()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheSessionRoundTrip(_SessionCase):
    """What an open window contributes to a session file, and what comes
    back."""

    def test_nothing_is_written_when_no_window_is_open(self):
        """An empty block on every session file is noise that buries the ones
        that carry state -- the `_LEGACY_TRACE_FIELDS` rule.

        Mutation: write `attribution` unconditionally in `session_to_dict`."""
        self._ready()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("attribution", self._save(tmp))

    def test_a_typed_candidate_reaches_the_sensitivity_table(self):
        """
        The Candidates field is free text a user types under time pressure and
        it drives real work -- one full re-solve per element per candidate --
        so it is exercised here end to end rather than only in the parser's
        own unit tests.

        Measured with 'open, R=0.1' on this fixture: four rows, `open` moving
        each ground by −6.1 dB and `R=0.1` by −0.00 dB (0.005 fH), which is
        the whole point of asking -- a 0.1 Ω ball is not the thing to fix.

        It also round-trips through the session file
        (`test_the_choices_are_written_and_nothing_else_is` and
        `test_the_view_and_the_candidates_come_back`); this test is where the
        input is proven to be real work rather than a stored string.

        Mutation: make `candidate_list` return the two structural candidates
        whatever it was handed -- the R=0.1 rows vanish.
        """
        self._ready()
        win = self._open_from_menubar()[0]
        win.cand_var.set("open, R=0.1")
        win._on_candidates_changed()
        win._view.set("sens")
        win._on_view_changed()
        self._settle()
        body = self._table(win)
        rows = [ln for ln in body.splitlines()
                if ln.startswith(ag.ATTRIB_SWATCH)]
        self.assertEqual(len(rows), 4, body)             # 2 elements x 2
        self.assertEqual(sum("R=0.1" in ln for ln in rows), 2, body)
        self.assertEqual(sum("open" in ln for ln in rows), 2, body)
        # Signed, never abs()-ed: both candidates REDUCE M here.
        self.assertTrue(all(ag.MINUS in ln for ln in rows), body)

    def test_the_choices_are_written_and_nothing_else_is(self):
        """
        Mutation A: return `{}` from `App._attribution_state`.
        Mutation B: add the numbers to the entry -- json cannot carry a numpy
        array and the run-snapshot rule forbids it anyway, so this pins the
        key set rather than trusting the encoder to notice.
        """
        self._ready()
        win = self._open_from_menubar()[0]
        win._view.set("sens")
        # Set to something OTHER than the default, or "the candidates were
        # written" is indistinguishable from "the reader supplied the default".
        win.cand_var.set("open, R=0.1")
        self._settle()
        with tempfile.TemporaryDirectory() as tmp:
            block = self._save(tmp)["attribution"]
        self.assertEqual(block["version"], ag.ATTRIB_SESSION_VERSION)
        self.assertEqual(len(block["windows"]), 1)
        entry = block["windows"][0]
        self.assertEqual(set(entry), self.KEYS)
        self.assertEqual(entry["victim"], "vic")
        self.assertEqual(entry["aggressor"], "agg")
        self.assertEqual(entry["quantity"], "M")
        self.assertAlmostEqual(entry["freq_ghz"], float(FREQ_GHZ))
        self.assertEqual(entry["view"], "sens")
        self.assertEqual(entry["candidates"], "open, R=0.1")

    def test_the_view_and_the_candidates_come_back(self):
        """
        Saving a choice and never applying it is the same as not saving it.

        Both of these were written to the file, stored in `_RESTORED` by
        `apply_attribution_session_state`, and then never read:
        `open_attribution_window` consumed victim / aggressor / quantity /
        freq_ghz only.  Measured before the fix -- save with `"view": "sens"`
        and `"candidates": "open, R=0.1"`, reload, Calculate, reopen, and the
        window came up on Contributions with `open, ideal`.

        THE FIRST WINDOW IS DESTROYED BEFORE THE RELOAD, and the assertion
        below is that a DIFFERENT object came back with the same choices.
        Without that this test passes with no restore code at all: rule 11 says
        this window outlives its subject, so the original survives a session
        load, `open_attribution_window` finds no matching trace and opens a
        second one, and `live_windows(app)[0]` is still the FIRST -- which has
        been sitting there with `sens` and `open, R=0.1` on it the whole time.
        Measured: with the restore monkeypatched away the naive version stayed
        green.

        Mutation: drop either branch of the restore in
        `open_attribution_window` and the matching assertion below goes red.
        """
        self._ready()
        win = self._open_from_menubar()[0]
        win.cand_var.set("open, R=0.1")
        win._on_candidates_changed()
        win._view.set("sens")
        self._settle()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            self.app._write_session(str(path), tmp)
            win.destroy()
            self._settle()
            self.assertEqual(ag.live_windows(self.app), [],
                             "the original window is still open, so anything "
                             "read back below could be ITS state")
            self.assertTrue(self.app._load_session_file(str(path), "test"))
            self._settle()
        # A session load drops the numbers, so Calculate before reopening --
        # `attribution_refusal` would (rightly) turn the trace away otherwise.
        self._calculate()
        back = self._open_from_menubar()[0]
        self.assertIsNot(back, win)
        self.assertEqual(back._view.get(), "sens")
        self.assertEqual(back.cand_var.get(), "open, R=0.1")

    def test_nothing_is_reopened_and_the_pane_says_why(self):
        """
        `attribution_refusal` turns away a trace with no numbers, and a freshly
        loaded session has none until Calculate has run -- so an auto-reopen
        could only produce one refusal dialog per entry before the user had
        asked for anything.  A restore that silently drops part of what was
        saved is the failure mode the Results pane exists to prevent, so it is
        said out loud.

        Mutation A: reopen the windows in `_apply_session` -- the count below
        goes to two, the second one being a refusal dialog's worth of nothing.
        Mutation B: `App._attribution_state` -> {} -- there is then no block to
        report and the note disappears with it.
        """
        self._ready()
        opened = self._open_from_menubar()
        self.assertEqual(len(opened), 1)
        body = self._save_load()
        self.assertIn("was not reopened", body)
        self.assertIn(ag.ATTRIB_MENU_LABEL, body,
                      "the note does not name the way back")
        # Nothing NEW: the one window still on screen is the one that was
        # already there (orphaned by the load -- see TestRule11), not a
        # restored copy.
        after = ag.live_windows(self.app)
        self.assertEqual(len(after), 1)
        self.assertIs(after[0], opened[0])

    def test_the_restored_choices_are_used_when_the_window_is_reopened(self):
        """
        The point of keeping them.  Reopening lands on the pair that was being
        read rather than on the alphabetically first one.

        Mutation: drop the `_RESTORED` lookup in `open_attribution_window` --
        the window comes up on 'vic' <- 'agg' at the marker frequency, which is
        also the default, so the assertion deliberately uses the REVERSED pair
        and a different quantity and frequency.
        """
        self._ready()
        tid = self.app.traces[0].id

        def swap(raw):
            raw["attribution"] = {
                "version": ag.ATTRIB_SESSION_VERSION,
                "windows": [{"trace_id": tid, "victim": "agg",
                             "aggressor": "vic", "quantity": "ImZ",
                             "freq_ghz": 3.0, "view": "contrib"}]}

        self._save_load(swap)
        self._select_trace(0)
        self._calculate()
        wins = self._open_from_menubar()
        self.assertEqual(len(wins), 1)
        prov = wins[0]._res.prov
        self.assertEqual((prov.victim, prov.aggressor), ("agg", "vic"))
        self.assertEqual(prov.quantity, "ImZ")
        self.assertAlmostEqual(prov.requested_hz, 3.0e9)


    def test_no_run_no_result_and_no_new_trace_field_reached_the_file(self):
        """
        The three standing rules, re-asserted at the point this feature could
        have broken them: run history is in-memory only, a session holds the
        CONFIG and never the results, and no `TraceConfig` field was added
        (`test_session.py::TestFieldCoverage` and
        `test_run_history.py::TestRunHistoryIsNotSaved` are the owners; this is
        the integration-level echo, taken with a window OPEN).
        """
        self._ready()
        self._calculate()                                # two runs on record
        self._open_from_menubar()
        with tempfile.TemporaryDirectory() as tmp:
            data = self._save(tmp)
        text = json.dumps(data)
        self.assertTrue(self.app._run_tabs, "there is no run history to omit")
        for key in ("runs", "run_tabs"):
            self.assertNotIn(key, data)
        for needle in ("Run #", "not this page", "changed since"):
            self.assertNotIn(needle, text)
        for tr in data["traces"]:
            for key in ("run", "attribution", "Zmat", "rlc", "coupling",
                        "mport_names"):
                self.assertNotIn(key, tr)
        declared = {f.name for f in fields(TraceConfig)}
        config = set(pkg_rlc_gui._config_trace_fields())
        computed = set(pkg_rlc_gui._COMPUTED_TRACE_FIELDS)
        self.assertEqual(config | computed, declared)
        self.assertEqual(config & computed, set())
        self.assertNotIn("attribution", declared)

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestAHandEditedSessionFile(_SessionCase):
    """
    A session file is readable text and WILL be hand-edited, so the rule is the
    loader's existing one: a bad value costs its own field, never the file.
    """

    def test_a_block_that_is_not_an_object_costs_its_own_field(self):
        """
        Losing a port map that took ten minutes to type because an analysis
        window's record was mangled is the wrong trade.

        Mutation: `sess.attribution = data["attribution"]` with no isinstance
        check -- `apply_attribution_session_state` then walks a string and the
        load takes the whole file with it.
        """
        self._ready()
        for junk in ("junk", 123, [1, 2, 3]):
            with self.subTest(junk=junk):
                body = self._save_load(
                    lambda raw, j=junk: raw.__setitem__("attribution", j))
                self.assertEqual(len(self.app.files), 1)
                self.assertEqual(len(self.app.traces), 1)
                self.assertIn("'attribution' is not an object", body)

    def test_one_malformed_entry_costs_its_own_entry(self):
        """
        The same rule one level down, and the load must not raise: the entry
        with no usable trace id is dropped by name and the good one beside it
        is still restored.

        Mutation: drop the `try` inside `apply_attribution_session_state`'s
        loop.
        """
        self._ready()
        tid = self.app.traces[0].id

        def mixed(raw):
            raw["attribution"] = {
                "version": ag.ATTRIB_SESSION_VERSION,
                "windows": [{"trace_id": "not a number"},
                            {"trace_id": tid, "victim": "agg",
                             "aggressor": "vic", "quantity": "M",
                             "freq_ghz": 3.0, "view": "contrib"}]}

        body = self._save_load(mixed)
        self.assertIn("one saved window entry was malformed", body)
        self.assertIn("was not reopened", body)
        # And the survivor really was kept, not merely reported.
        self._select_trace(0)
        self._calculate()
        prov = self._open_from_menubar()[0]._res.prov
        self.assertEqual((prov.victim, prov.aggressor), ("agg", "vic"))

    def test_a_block_from_another_version_is_reported_not_guessed_at(self):
        """`SessionError`'s rule: say which, and do not try to read it
        anyway.  Mutation: ignore the version field."""
        self._ready()

        def bump(raw):
            raw["attribution"] = {"version": 99, "windows": []}

        body = self._save_load(bump)
        self.assertIn("version 99", body)
        self.assertIn("was ignored", body)
        self.assertEqual(len(self.app.traces), 1)


if __name__ == "__main__":                               # pragma: no cover
    unittest.main()
