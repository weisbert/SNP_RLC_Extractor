"""
pkg_rlc_panels_results.py  --  the Results pane of the main window.

The right-hand top pane: the header strip (View / Units / Runs / Keep), the
`ttk.Notebook` whose tab 0 is the Log, the Log badge, the run-history tabs
with their keep / evict rules, the tab-strip right-click menu, the Runs
menubutton -- and the ONE builder that turns a finished run into text
(`_run_report_segments`), which the Log and every run page both print through
so that they cannot disagree about a run's contents.

HAS-A, NOT IS-A, for the reason spelled out in pkg_rlc_panels_files: this pane
is almost entirely rules about ORDER, and about what is populated before
`PanedWindow.add()`.  `App._build_right_panel` therefore still creates both
frames, hands this one to the panel, builds the plot, and adds both -- in that
order -- so the rule it turns on ("populate before add(), or a stray
update_idletasks() elsewhere pins the sash at ~2 px and the whole Results pane
disappears") stays where a reader of App will find it.

WHAT THE PANEL OWNS, AND WHAT THE APP STILL OWNS.  The panel owns its WIDGETS
-- `results_text`, `results_nb`, `_log_tab`, `_results_header`, `_keep_btn`,
`_runs_menu`, `_run_tab_menu` and the two view / units StringVars -- and App
aliases every one of them onto itself, so `app.results_text` and friends keep
resolving for every existing caller and test.  The panel does NOT own the run
history STATE: `_run_tabs`, `_last_run`, `_run_counter`, `_log_unseen`,
`_log_forced`, the two caps and their IntVars stay on App and are read and
written through `self.app`.  That split is deliberate -- those are REASSIGNED
at runtime and are read straight off `app` by the tests, so moving them would
need forwarding properties on both sides, i.e. two places one value can be
read from and one more pair that can drift.

It may not import `pkg_rlc_gui` (L5 -> L6, tests/test_layering.py).  `RunTab`
and `_tag_swatch_rows` moved here WITH the notebook they belong to, and are
re-exported from `pkg_rlc_gui`.  `RunSnapshot` could not follow: it is the run
RECORD and belongs at L2 with the rest of the model.  It survives here only as
an unevaluated annotation (`from __future__ import annotations`), and
`_empty_run` -- the one place it is CONSTRUCTED -- stayed on App.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from pkg_rlc.widgets.plot import COLORS, ReflowRow
from pkg_rlc.present.report import (
    COUPLING_LEGEND_LINES,
    FreqSnap,
    LOG_ERROR,
    LOG_INFO,
    LOG_WARN,
    RESULTS_SWATCH,
    RESULTS_VIEWS,
    RUN_AUTO_MAX_UI,
    RUN_KEPT_GLYPH,
    RUN_OPEN_GLYPH,
    RUN_TABS_HARD_CAP,
    RUN_TABS_MIN,
    VIEW_COMPARE,
    VIEW_DETAIL,
    VIEW_SUMMARY,
    _format_compare,
    _format_coupling_block,
    _format_results_table,
    _format_summary_coupling,
    _format_summary_self,
    _trunc_str,
    keep_button_label,
    log_tab_label,
    marker_freq_text,
    run_change_line,
    run_freq_snap,
    run_headline,
    run_stale_banner,
    run_tab_label,
)
from pkg_rlc.panels.attrib_gui import ATTRIB_MENU_LABEL, refresh_attribution_windows


@dataclass
class RunTab:
    """One page of the Results notebook: a run record and the widgets showing it.

    TRACKED BY WIDGET, NEVER BY INDEX.  Measured: evicting a lower index
    renumbers every tab after it but keeps the same widget selected and
    preserves its scroll position exactly -- so a list of records keyed on the
    frame survives eviction, while any stored index silently starts pointing at
    the neighbour.
    """
    run: "RunSnapshot"                 # still L6 -- see the module docstring
    frame: object                       # ttk.Frame, the notebook's child
    text: object                        # the ScrolledText inside it
    kept: bool = False
    unseen: bool = False                # arrived while the reader was elsewhere


# ============================================================================
# The one results-pane renderer that is NOT a formatter
# ============================================================================
#
# Everything else that turns a run into text lives in pkg_rlc_report, which is
# pure and testable with no display.  This one WRITES INTO A Tk TEXT WIDGET, so
# it belongs on this side of that seam -- and it reaches COLORS, which is
# pkg_rlc_plot's.

def _tag_swatch_rows(txt, base_line: int, text: str,
                     color_idxs: Sequence[int]) -> None:
    """
    Colour the leading swatch of each data row of `text`, already inserted
    starting at line `base_line` of the Text widget `txt`.

    Rows are found by their RESULTS_SWATCH prefix and consumed in order, so no
    line-number arithmetic has to be kept in step with however many header
    lines _format_results_table decides to emit (the file-alias line alone is
    already conditional on the trace count).  Shared by the Log and by every
    run page, so a row cannot be one colour on one and another on the other.
    """
    pending = iter(color_idxs)
    for off, line in enumerate(text.split("\n")):
        # EVERY occurrence, not only a leading one.  A results table puts one
        # swatch at the head of each row; the compare view puts one in each
        # COLUMN HEADING, because there a column is a trace and the heading is
        # the only cell that names it.  Consuming them left to right, top to
        # bottom is the same contract either way, and no other line this
        # module emits carries the character at all.
        col = line.find(RESULTS_SWATCH)
        while col >= 0:
            idx = next(pending, None)
            if idx is None:
                return          # more swatches than colours: leave them plain
            ln = base_line + off
            txt.tag_add(f"c{idx % len(COLORS)}",
                        f"{ln}.{col}", f"{ln}.{col + len(RESULTS_SWATCH)}")
            col = line.find(RESULTS_SWATCH, col + len(RESULTS_SWATCH))


class ResultsPanel:
    """The Results pane: the header strip, the notebook, and the run pages."""

    def __init__(self, parent: ttk.Frame, app) -> None:
        self.app = app

        # A ReflowRow, not a pack(side=LEFT) run, and that is a measurement.
        # With five controls the strip already asked for 667 px against the
        # 575 it gets at the 1040x600 minsize at 150% font scaling, and pack
        # UNMAPS FROM THE END -- so the Keep button, whose label is the only
        # place the kept cap is stated at the moment it bites, was the one
        # being squeezed.  A 'View:' label plus a readonly combobox is a
        # further 127 px at 100% and 240 px at 150% (measured), which would
        # have taken it off screen outright with no scrollbar and no other
        # route to it: exactly the defect tests/test_plot_controls.py exists to
        # stop recurring.  ReflowRow wraps instead, keeps its own requested
        # width at 1 px so the strip can never force the pane wider, and reads
        # an imposed width while writing only a height -- a fixed point, not
        # the _apply_editor_scrollbars limit cycle.  At 100% the whole strip is
        # 477 px of 575 and stays one row, so nothing about the default window
        # moves.
        header = ReflowRow(parent)
        header.pack(side=tk.TOP, fill=tk.X)
        header.add(ttk.Label(header, text="Results", anchor="w"))
        # WHICH RENDERING, not which numbers.  Kept beside Units: because it is
        # the same kind of choice -- both repaint every run page in place and
        # neither creates a run.
        header.add(ttk.Label(header, text="View:"), padx=(6))
        self.results_view_var = tk.StringVar(value=VIEW_DETAIL)
        view_combo = ttk.Combobox(
            header, textvariable=self.results_view_var,
            values=list(RESULTS_VIEWS), state="readonly", width=8,
        )
        header.add(view_combo)
        view_combo.bind("<<ComboboxSelected>>",
                        lambda _e: self._on_results_view_changed())
        header.add(ttk.Label(header, text="Units:"), padx=(6))
        self.units_mode_var = tk.StringVar(value="smart")
        units_combo = ttk.Combobox(
            header, textvariable=self.units_mode_var,
            values=["smart", "aligned"], state="readonly", width=8,
        )
        header.add(units_combo)
        units_combo.bind("<<ComboboxSelected>>",
                         lambda _e: self._on_units_mode_changed())
        # Tk 8.6's ttk.Notebook has no tab-strip scrolling and no overflow
        # chevron, and past ~12 tabs a label is three characters wide.  The
        # Runs menu is where a compressed tab stays identifiable: it carries
        # the FULL description of every run, and it is also where the two caps
        # are set.  Rebuilt on post, because the list changes on every run.
        self._runs_menubutton = ttk.Menubutton(header, text="Runs ▾")
        self._runs_menu = tk.Menu(self._runs_menubutton, tearoff=0)
        self._runs_menubutton["menu"] = self._runs_menu
        self._runs_menu.configure(postcommand=self._rebuild_runs_menu)
        header.add(self._runs_menubutton, padx=(6))
        # Keep is a BUTTON, not a menu entry, because its label is the only
        # place the kept cap can be stated at the moment it bites.
        self._keep_btn = ttk.Button(header, text=keep_button_label(0, 1, "none"),
                                    command=self._on_keep_run)
        header.add(self._keep_btn)
        #: The strip has to be re-laid when the Keep button's TEXT grows --
        #: `_reflow` runs from `add()` and from the strip's own <Configure>,
        #: and neither fires for that.  See ReflowRow.refresh().
        self._results_header = header
        # The Results pane is a ttk.Notebook and tab 0 is the Log, holding the
        # same ScrolledText it has always held.
        #
        # results_text stays a REAL, PERSISTENT widget attribute -- never a
        # property resolving to whichever tab is active.  Six tests take an
        # index(END) mark, run Calculate and read back from that mark; a fresh
        # widget returns '' for a stale mark, so a property turns all six into
        # empty-string assertions that read like formatting bugs.
        #
        # There is deliberately NO second tab, and the Log is the SELECTED tab
        # at startup.  focus_set() and event_generate() are no-ops on an
        # unmapped widget, and a non-selected tab's widget is unmapped -- so
        # test_session.py::test_control_o_does_not_also_scribble_in_the_results
        # _pane, which focuses results_text and synthesises Ctrl+O, proves
        # nothing whatsoever if the Log is not on screen.  "Tidying up" by
        # pre-creating an empty run tab here moves the failure to a test whose
        # name points at the menubar.
        #
        # No <<NotebookTabChanged>> -> canvas.focus_set() handler either.
        # Measured: nb.select() does not steal focus, and that property is what
        # makes a later auto-switch safe.  Wiring a focus_set here would invent
        # a focus steal on every Calculate that does not exist today.  (That
        # handler belongs to a PLOT notebook, which is not what this is.)
        self.results_nb = ttk.Notebook(parent)
        self._log_tab = ttk.Frame(self.results_nb)
        self.results_text = ScrolledText(self._log_tab, height=10,
                                         wrap=tk.NONE, font=("Consolas", 9))
        self.results_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Tag for highlighting non-empty Sign flags. Configured once.
        self.results_text.tag_configure("flag", foreground="#b04000")
        # One tag per palette slot, configured once, for the swatch at the head
        # of each results row (_append_swatched).  Same precedent as "flag":
        # a Text tag, not a Treeview -- a Treeview would take the 'aligned'
        # units mode (one SI prefix per column, right-aligned) with it, lose
        # select-drag-copy into a mail, and freeze its row height at 20 px so
        # the text clips under DPI scaling.
        for _i, _c in enumerate(COLORS):
            self.results_text.tag_configure(f"c{_i}", foreground=_c)
        self.results_nb.add(self._log_tab, text=log_tab_label(0))
        self.results_nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.results_nb.bind("<<NotebookTabChanged>>",
                             self._on_results_tab_changed, add="+")
        # Right-click a tab for Keep / Close / Close others.  nb.index("@x,y")
        # resolves the clicked tab at every tab count, and <Button-3> is not a
        # Notebook class binding, so it is free.  (Deliberately NOT a per-tab
        # close button: on the vista theme Style.element_create means replacing
        # the layout that draws the native tab, hand-wiring hit-testing, and a
        # result that renders differently on the red-zone box with no test able
        # to see it.)
        self._run_tab_menu = tk.Menu(self.app, tearoff=0)
        self._run_tab_menu.add_command(label="Keep this run",
                                       command=self._on_menu_keep_run)
        self._run_tab_menu.add_command(label="Close this run",
                                       command=self._on_menu_close_run)
        self._run_tab_menu.add_command(
            label="Close other runs (kept runs stay)",
            command=self._on_menu_close_other_runs)
        self.app._run_tab_menu_target: Optional[RunTab] = None
        self.results_nb.bind("<Button-3>", self._on_run_tab_context_menu)

    def _render_results(self, run: RunSnapshot) -> None:
        """
        Print one RUN's table, fit summaries and coupling blocks -- for the
        records that are ON THE PLOT.

        A hidden trace is filtered out here rather than at collection time, so
        `run.rows` still holds everything and the caller can decide whether the
        visibility is the one that was recorded (any past run) or the one that
        holds now (`with_visibility`, the current run).  Its numbers are not
        lost -- they stay cached on the trace, so showing it again needs no
        Calculate -- but they are not reported anywhere until it is shown,
        which is why the line under the table has to name it.

        Everything read here comes off the snapshot, never off a TraceConfig:
        the id, label and port descriptor beside a number have to be the ones
        that produced it.
        """
        for text, colors, sev in self._run_report_segments(run):
            if colors:
                self._append_swatched(text, colors)
            else:
                self._append_result(text, sev)

    def _run_report_segments(self, run: RunSnapshot) -> list:
        """
        One run's report as (text block, swatch colours, severity) segments.

        The Log and the run page print the SAME report, so they are built once
        here.  A second copy of this code would let the page the user reads and
        the log they scroll back through disagree about a run's contents, with
        nothing to tell them apart.

        THE VIEW IS READ LIVE OFF THE APP, exactly as the units mode is, and
        for the same reason: which of the three renderings is on screen is a
        RENDERING CHOICE, not a recorded fact, so freezing it onto the run
        would leave one page in a layout the switch had already left.  Both
        halves are the run pages' documented rule -- `_on_results_view_changed`
        repaints every page in place and creates no tab, because choosing a
        view measures nothing.
        """
        units = self.units_mode_var.get()
        view = self.results_view_var.get()
        shown_rows = [r for r in run.rows if r.enabled]
        shown_blocks = [b for b in run.blocks if b.enabled]
        hidden = [r for r in run.rows if not r.enabled]
        hidden += [b for b in run.blocks if not b.enabled]

        if view == VIEW_SUMMARY:
            segs = self._summary_segments(run, shown_rows, shown_blocks, units)
        elif view == VIEW_COMPARE:
            segs = self._compare_segments(run, shown_rows, shown_blocks, units)
        else:
            segs = self._detail_segments(run, shown_rows, shown_blocks, units)
        return segs + self._footer_segments(shown_rows, shown_blocks, hidden)

    def _detail_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                         units: str) -> list:
        """The full report: the results table, the fits, one block per trace.

        This is the view the tool has always had, minus the two paragraphs
        that were repeated verbatim under every block (see
        COUPLING_LEGEND_LINES and _footer_segments).
        """
        run_freq = run_freq_snap(run)
        segs: list = []
        if shown_rows:
            segs.append((_format_results_table(shown_rows, units, run_freq),
                         tuple(r.color_idx for r in shown_rows), LOG_INFO))
            for f in run.fits:
                if f.enabled:
                    # A fit that raised is reported on the same line as one
                    # that worked, so the severity has to come off the text.
                    segs.append((f.text, (),
                                 LOG_WARN if "ERROR" in f.text else LOG_INFO))
        if (isinstance(run_freq, FreqSnap) and not run_freq.agreed
                and (shown_rows or shown_blocks)):
            # Several files, several sweeps, several answers: no one line can
            # name the frequency, so each file names its own.  Built here
            # rather than at the Calculate call site because the run PAGE has
            # to carry it too -- _run_report_segments is the one builder.
            for lbl, snap in run.freqs:
                segs.append((f"  {lbl}: read at "
                             f"{marker_freq_text(snap, '{:.6g}')}", (),
                             LOG_WARN if snap.off_grid else LOG_INFO))
        for block in shown_blocks:
            segs.append(("", (), LOG_INFO))
            segs.append((_format_coupling_block(block, units), (), LOG_INFO))
        return segs

    def _run_heading(self, run: RunSnapshot, what: str) -> str:
        """'self impedance @ 5.55 GHz', with the frequency's provenance.

        The compact views drop the per-block `Z matrix @ <freq>` line, so this
        is where the frequency they were read at is stated -- a table of
        numbers with no frequency on it is the two-numbers-one-screen failure
        `tests/test_freq_label.py` exists about.
        """
        return f"  {what} @ {marker_freq_text(run_freq_snap(run), '{:.6g}')}"

    def _summary_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                          units: str) -> list:
        """One run as two tables: every self measurement, then every pair."""
        segs: list = []
        text, colors = _format_summary_self(shown_rows, shown_blocks, units)
        if text:
            segs.append((self._run_heading(run, "self impedance"), (),
                         LOG_INFO))
            segs.append((text, colors, LOG_INFO))
        # The fits belong to the self table and are already one line each.
        for f in run.fits:
            if f.enabled:
                segs.append((f.text, (),
                             LOG_WARN if "ERROR" in f.text else LOG_INFO))
        text, colors = _format_summary_coupling(shown_blocks, units)
        if text:
            segs.append(("", (), LOG_INFO))
            segs.append((self._run_heading(run, "coupling"), (), LOG_INFO))
            segs.append((text, colors, LOG_INFO))
        if not segs:
            segs.append(("  (nothing on the plot)", (), LOG_INFO))
        return segs

    def _compare_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                          units: str) -> list:
        """Traces as columns, with a delta at exactly two of them.

        FALLS BACK TO THE SUMMARY, NAMING THE REASON, rather than showing an
        empty pane: the view is chosen once and then stays chosen, so a run
        that cannot be compared must still print its numbers.  Same rule as
        the attribution split -- degrade, never refuse, and say which.
        """
        text, colors, refusal = _format_compare(shown_rows, shown_blocks,
                                                units)
        if refusal:
            return ([(f"  compare: {refusal}", (), LOG_INFO)]
                    + self._summary_segments(run, shown_rows, shown_blocks,
                                             units))
        return [(self._run_heading(run, "compare"), (), LOG_INFO),
                (text, colors, LOG_INFO)]

    def _footer_segments(self, shown_rows, shown_blocks, hidden) -> list:
        """
        The lines that qualify the whole run, once each, whatever the view.

        EVERY ONE OF THESE USED TO BE PER BLOCK OR PER RECORD, and two of them
        were the same sentence rendered twice.  Measured on the reported
        two-trace run: 3538 characters of report, of which the 272-column
        coupling legend and the 262-column reference-node verdict accounted for
        1068 -- 30% of the report was one of two paragraphs said twice, in a
        pane that is 144 columns wide at the default window size and does not
        wrap.  They are deduplicated here rather than shortened at the source
        because both of them are honest: what was wrong was the repetition.
        """
        segs: list = []
        # The legend belongs to the coupling blocks, so it is emitted only when
        # one was printed -- a run of nothing but mode-1 traces has no ind/cap
        # column and no M/L to qualify.  The results table keeps its own,
        # shorter legend line, which was never repeated.
        if shown_blocks:
            for line in COUPLING_LEGEND_LINES:
                segs.append((line, (), LOG_INFO))
        # WHERE DID THAT M COME FROM?  This is the pointer that matters: the
        # user is looking at "M = 2.16 pH" here, not at a menu bar, and the
        # "Show Ports needed five pointers before anyone found it" history is
        # what says an analysis window nobody can find is an analysis window
        # nobody uses.  Same idiom as the "(see Export CSV)" line inside the
        # block: one line, naming the route.
        #
        # It is emitted HERE and not inside `_format_coupling_block` so that it
        # is said once per RUN rather than once per block -- the same rule as
        # the legend above it and the hidden-traces line below: six coupling
        # traces do not need six copies of one sentence.  (It also keeps that
        # function's output free of anything the summary and compare views
        # would have to strip back out.)
        #
        # Gated on a PAIR existing, not merely on a block: a block with one
        # measurement port prints "(only one measurement port ...)" and
        # `attribution_refusal` turns that trace away by name.  Pointing at a
        # refusal is worse than not pointing at all.
        #
        # TWO PHRASES ARE LOAD-BEARING and both are pinned by
        # tests/test_attrib_gui_integration.py: 'where each M above comes from'
        # is how the line is found, and 'right-click menu' is the second route
        # -- a pointer that names one way in is a pointer that fails whenever
        # the reader is already holding the mouse over the Traces list.  The
        # tail was shortened around them to bring the whole sentence inside the
        # pane's measured 144 columns; it was 147, so the ROUTE was the part
        # falling off the right-hand edge.
        if any(b.cres.pairs for b in shown_blocks):
            segs.append((
                f"  where each M above comes from, and what would move it: "
                f"select the trace → Analyze → {ATTRIB_MENU_LABEL} "
                f"(or its right-click menu)", (), LOG_INFO))
        # R3-5: THE WELD, WHERE THE NUMBER IS READ.  A weld raises nothing and
        # makes no number look wrong -- measured in pkg_rlc_compose, the
        # package ground pad grounded / open / through 1 nH give
        # L_eff = 2.1454 nH, bit-identical, spread 0.000e+00 -- so it does not
        # change the number, it changes how the number must be READ.  A report
        # nobody opened is therefore the wrong place for it, and this is the
        # right one: directly under the table and the blocks it qualifies, in
        # the Log AND on every run page, because `_run_report_segments` is the
        # one builder of both.
        #
        # Read off the SNAPSHOT, frozen at Calculate time.  A page re-rendered
        # by a units switch must not pick up a verdict from a composition that
        # has been rebuilt since.
        #
        # IDENTICAL VERDICTS ARE SAID ONCE, NAMING EVERY TRACE THEY ARE ABOUT.
        # Two traces over the same two files get the same 262-column sentence,
        # and the reported run printed it twice for [1] and [4] -- 524 of 3538
        # characters, in a pane that shows 144 of them.  The id list is what
        # keeps it a statement about specific traces rather than a general
        # remark, so nothing is lost by saying it once: `[1][4] Reference-node
        # check: …`.  Grouped on the FULL verdict (strip, warn flag and detail
        # lines together), so two traces whose checks differ in any way keep
        # their own lines -- collapsing on the strip alone would put one
        # trace's id on another trace's detail paragraph.
        groups: dict = {}
        for rec in list(shown_rows) + list(shown_blocks):
            if not getattr(rec, "ref_strip", ""):
                continue
            key = (rec.ref_strip, bool(rec.ref_warn),
                   tuple(rec.ref_lines or ()))
            groups.setdefault(key, []).append(rec.id)
        for (strip, warn, detail), ids in groups.items():
            tag = "".join(f"[{i}]" for i in ids)
            segs.append((f"  {tag} {strip}", (),
                         LOG_WARN if warn else LOG_INFO))
            if warn:
                for line in detail:
                    segs.append((f"      {line}", (), LOG_WARN))
        if hidden:
            # Named, not silently dropped: Calculate still measured them, and
            # since they are in no other output either, this line is the only
            # place the report says where they went -- otherwise a trace is
            # simply missing from it.
            segs.append((
                "  hidden (measured, not plotted, not exported; show it to "
                "read or export it): "
                + ", ".join(f"[{r.id}] {_trunc_str(r.label, 18)}"
                            for r in hidden), (), LOG_INFO))
        return segs

    def _write_run_report(self, txt, run: RunSnapshot) -> None:
        """The same segments, written into a run page instead of the Log.

        No severity routing here: a run page is not the Log, and badging the
        Log for a line the user is looking at on another tab would be a lie.
        """
        for text, colors, _sev in self._run_report_segments(run):
            base = int(txt.index("end-1c").split(".")[0])
            txt.insert(tk.END, text + "\n")
            if colors:
                _tag_swatch_rows(txt, base, text, colors)

    def _on_results_view_changed(self) -> None:
        """Repaint the Log and every run page in the newly chosen view.

        SAME RULE AS THE UNITS SWITCH, and deliberately the same code path: the
        view is a rendering choice, not a recorded fact, so it repaints every
        page (leaving one page in the previous layout is the "one screen, two
        formattings, then a silent flip" failure that rule is written from) and
        it creates NO run tab, because choosing a view measures nothing.

        The Attribution window is NOT poked: it has tables of its own and no
        view selector, so unlike the units mode there is nothing there this
        choice can leave stale.
        """
        self._rerender_every_page(
            f"\n--- re-rendered with view={self.results_view_var.get()} ---")

    def _on_units_mode_changed(self) -> None:
        self._rerender_every_page(
            f"\n--- re-rendered with units={self.units_mode_var.get()} ---")
        # The ONE caller that passes rerender=True, and the reason is the same
        # one that makes this repaint every run page: the unit is a RENDERING
        # choice, not a recorded fact.  An Attribution window's tables are
        # formatted through the app's units_mode_var exactly as
        # `_run_report_segments` is, and there is no other way for it to hear
        # that the choice changed -- it would sit in the previous formatting
        # beside a Results pane that had already flipped.  Not the default: a
        # re-render redraws the sweep too, and on the editor's per-keystroke
        # path that would be a closed-form solve per character.
        if self.app._last_run is not None and (self.app._last_run.rows
                                           or self.app._last_run.blocks):
            refresh_attribution_windows(self.app, rerender=True)

    def _rerender_every_page(self, log_note: str) -> None:
        run = self.app._last_run
        if run is None or not (run.rows or run.blocks):
            return
        self._append_result(log_note)
        # The CURRENT run follows the visibility as it stands now -- `enabled`
        # gates the results table as well as the plot, so a row for a curve
        # that is no longer drawn would read as a duplicate of one that is.
        # Every other field is frozen.  A PAST run is rendered as recorded.
        self.app._last_run = run.with_visibility(self.app.traces)
        self._render_results(self.app._last_run)
        # EVERY run page is rewritten in place, and no tab is created: a units
        # switch measures nothing, so it is not a run.  (The Log is a
        # chronological log and does append, which is what it is for.)
        #
        # Every page, not just the newest, because the unit is a RENDERING
        # choice and not a recorded fact -- the run snapshots hold numbers, and
        # _run_report_segments reads units_mode_var live.  Repainting only the
        # newest left the older pages in the previous formatting until the next
        # Calculate repainted them anyway (it re-renders all pages so their
        # banners name the current run), so "a past run is rendered as
        # recorded" was never what the user saw: they saw one screen showing
        # two formattings, and then a silent flip.
        newest = self._newest_run_tab()
        if newest is not None and newest.run.number == self.app._last_run.number:
            newest.run = self.app._last_run
        self._render_all_run_tabs()


    def _log_selected(self) -> bool:
        """True when the Log tab is the one on screen."""
        try:
            return self.results_nb.select() == str(self._log_tab)
        except Exception:                               # pragma: no cover
            return False

    def _render_log_badge(self) -> None:
        """Repaint the Log tab's label. Never raises -- it runs from a trace."""
        try:
            self.results_nb.tab(self._log_tab,
                                text=log_tab_label(self.app._log_unseen))
        except Exception:                               # pragma: no cover
            pass

    def _on_results_tab_changed(self, _event=None) -> None:
        """
        The badge is cleared by LOOKING at the Log, not by any other action.

        Selecting some other tab is the user (or a later automatic switch)
        deliberately moving on, which is what releases the claim an ERROR line
        put on the pane.
        """
        if self._log_selected():
            self.app._log_unseen = 0
            self._render_log_badge()
        else:
            self.app._log_forced = False
        # A run tab that is now on screen has been read, so its "!" goes.
        rt = self._selected_run_tab()
        if rt is not None and rt.unseen:
            rt.unseen = False
            self._render_run_tab_label(rt)
        self._refresh_keep_button()

    def _select_log_tab(self) -> None:
        """
        Bring the Log to the front and keep it there.

        The flag is set AFTER select() so it cannot be cleared by the
        <<NotebookTabChanged>> that select() generates, whose delivery order
        relative to this line is not something to depend on.
        """
        try:
            self.results_nb.select(self._log_tab)
        except Exception:                               # pragma: no cover
            return
        self.app._log_forced = True

    def _select_results_tab(self, tab) -> bool:
        """
        Switch the Results notebook to `tab` -- unless an error owns it.

        This is the polite switch, and it is the one an automatic
        "show the run that just finished" must use: an ERROR line already
        pulled the Log to the front and moving off it would hide the only
        explanation of why the numbers are missing.  Returns True when the
        switch happened.
        """
        if self.app._log_forced:
            return False
        try:
            self.results_nb.select(tab)
        except Exception:                               # pragma: no cover
            return False
        return True

    # ------------------------------------------------------- run history tabs

    def _kept_run_tabs(self) -> list[RunTab]:
        return [rt for rt in self.app._run_tabs if rt.kept]

    def _auto_run_tabs(self) -> list[RunTab]:
        """The auto ring, newest first.  This is the ONLY set Calculate touches."""
        return [rt for rt in self.app._run_tabs if not rt.kept]

    def _kept_cap(self) -> int:
        """
        How many runs may be kept at once.

        Total budget minus the auto ring, so the two disjoint sets together can
        never exceed the tab count the strip was measured to stay readable at.
        """
        return max(1, self.app._run_tabs_max - self.app._run_auto_max)

    def _selected_run_tab(self) -> Optional[RunTab]:
        try:
            sel = self.results_nb.select()
        except Exception:                               # pragma: no cover
            return None
        for rt in self.app._run_tabs:
            if str(rt.frame) == sel:
                return rt
        return None

    def _current_run_number(self) -> int:
        """
        The run the PLOT and Export CSV are showing.

        Deliberately not "the highest number still on a tab": closing the
        newest page does not un-plot its curves, and a banner derived from the
        surviving tabs would then quietly promote an older page to "current"
        and stop warning about exactly the disagreement it exists for.
        """
        return self.app._last_run.number if self.app._last_run is not None else 0

    def _newest_run_tab(self) -> Optional[RunTab]:
        """The tab holding the run the plot and Export CSV are showing, if it
        still exists -- the user may have closed it."""
        for rt in self.app._run_tabs:
            if rt.run.number == self._current_run_number():
                return rt
        return None

    def _make_results_text(self, parent):
        """
        A results page: the same ScrolledText the Log has always been.

        height=10 on every one of them, so the notebook's requested height is
        the same whichever page is on screen and the vertical sash cannot creep
        as runs accumulate.
        """
        txt = ScrolledText(parent, height=10, wrap=tk.NONE,
                           font=("Consolas", 9))
        txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        txt.tag_configure("flag", foreground="#b04000")
        txt.tag_configure("stale", foreground="#b04000")
        for i, c in enumerate(COLORS):
            txt.tag_configure(f"c{i}", foreground=c)
        return txt

    def _new_run_tab(self, run: RunSnapshot) -> RunTab:
        """Add a page for `run` at the front of the run tabs and fill it in."""
        frame = ttk.Frame(self.results_nb)
        txt = self._make_results_text(frame)
        rt = RunTab(run=run, frame=frame, text=txt)
        # Newest first: index 1 is straight after the Log.  ttk's insert()
        # refuses a position past the last existing tab ("Slave index 1 out of
        # bounds"), so the very first run page is an add().
        if len(self.results_nb.tabs()) > 1:
            self.results_nb.insert(1, frame, text="")
        else:
            self.results_nb.add(frame, text="")
        self.app._run_tabs.insert(0, rt)
        self._render_run_tab_label(rt)
        return rt

    def _reader_is_at_the_newest_run(self) -> bool:
        """
        True when switching to the run that just finished is what the reader
        was going to do anyway.

        The Log counts: it is where the user watches a run happen.  A tab they
        deliberately kept does not -- yanking them off it is the opposite of
        what keeping means, and Calculate is pressed constantly in the
        edit/compute/read loop.
        """
        if self._log_selected():
            return True
        rt = self._selected_run_tab()
        if rt is None:                                  # pragma: no cover
            return True
        if rt.kept:
            # The natural gesture is to press Keep on the page you are looking
            # at, which is by definition the newest -- so without this the very
            # next Calculate yanked the reader off the page they had just
            # deliberately kept, which is what the docstring above says must
            # not happen.  Measured: Calculate -> land on '#2' -> Keep ->
            # Calculate -> selected '#3'.  The page they stay on is marked
            # unseen instead, which is the documented fallback.
            return False
        # Against the YOUNGEST PAGE ON SCREEN, not _current_run_number(): this
        # is called from _add_run_tab, by which point _last_run is already the
        # run being added, so "am I at the newest?" would answer itself with a
        # flat no and the switch would never happen.  It is also the right
        # frame of reference after the current run's page has been closed --
        # the reader is then at the front of what is left.
        return rt.run.number == max(t.run.number for t in self.app._run_tabs)

    def _add_run_tab(self, run: RunSnapshot) -> RunTab:
        """
        Give the finished run a page, trim the auto ring, and switch to it --
        CONDITIONALLY.

        The decision is taken BEFORE the new page exists, or "am I at the
        newest?" answers itself.  When the switch does not happen the page is
        marked unseen instead, so nothing arrives silently.  An ERROR has
        already claimed the pane by this point and _select_results_tab declines
        to move off it, which is how "an error still wins" holds without a
        second rule for it.
        """
        at_newest = self._reader_is_at_the_newest_run()
        rt = self._new_run_tab(run)
        self._evict_run_tabs()
        # Every OTHER page's "not this page" banner is relative to this run.
        self._render_all_run_tabs()
        rt.unseen = not at_newest
        self._render_run_tab_label(rt)
        if at_newest and not self._select_results_tab(rt.frame):
            rt.unseen = True
            self._render_run_tab_label(rt)
        self._refresh_keep_button()
        return rt

    def _destroy_run_tab(self, rt: RunTab) -> None:
        """
        The ONE teardown.  forget() THEN destroy(), in that order.

        Measured: forget() alone does not destroy the child -- 300 runs at a
        limit of 10 left 290 orphan widgets and +21.5 MB, growing linearly.
        tests/test_run_history.py asserts len(nb.winfo_children()) ==
        len(nb.tabs()) after a churn loop, which is the only honest form of
        that check (the working set does not drop even on correct teardown, so
        an RSS assertion would be measuring the allocator).
        """
        try:
            self.results_nb.forget(rt.frame)
        except Exception:                               # pragma: no cover
            pass
        try:
            rt.frame.destroy()
        except Exception:                               # pragma: no cover
            pass
        if rt in self.app._run_tabs:
            self.app._run_tabs.remove(rt)

    def _evict_run_tabs(self) -> None:
        """
        Trim the AUTO RING to its size, oldest first.  Nothing else is touched.

        A kept run is not a candidate -- that is what keeping means -- and
        neither is the tab that is ON SCREEN: evicting what the user is reading
        raises no error at all, Tk silently selects a neighbour, which is worse
        than an error.  Nor is the page for the CURRENT run, which is the one
        the plot and Export CSV are showing: at an auto ring of 1, with the
        reader parked on the older page, the oldest-first scan would otherwise
        skip the page they are on and take the run that just finished.

        So the ring is allowed to sit one over its size while a page is
        protected; the next Calculate that finds the reader elsewhere trims it.
        """
        try:
            sel = self.results_nb.select()
        except Exception:                               # pragma: no cover
            sel = ""
        current = self._current_run_number()
        autos = self._auto_run_tabs()
        while len(autos) > self.app._run_auto_max:
            victim = None
            for rt in reversed(autos):          # oldest first
                if str(rt.frame) != sel and rt.run.number != current:
                    victim = rt
                    break
            if victim is None:
                break
            autos.remove(victim)
            self._destroy_run_tab(victim)

    def _render_run_tab_label(self, rt: RunTab) -> None:
        try:
            self.results_nb.tab(rt.frame,
                                text=run_tab_label(rt.run.number, rt.run.when,
                                                   rt.kept, rt.unseen))
        except Exception:                               # pragma: no cover
            pass

    def _render_run_tab(self, rt: RunTab) -> None:
        """
        (Re)write one run page from its record.  In place -- never appended.

        Three header lines, then exactly the report _render_results prints to
        the Log.  Line 3 is mandatory on every page but the newest: without it
        three surfaces on one screen disagree with nothing to explain it.
        """
        current = self._current_run_number()
        is_newest = current == 0 or rt.run.number == current
        txt = rt.text
        try:
            # Line 3 names the NEWEST run, so every other page is rewritten on
            # every run -- and a reader scrolled halfway down an old page must
            # not be thrown back to the top by that.  Empty means "first draw",
            # where the top is where they want to be.
            had_text = txt.index("end-1c") != "1.0"
            where = txt.yview()[0]
            txt.delete("1.0", tk.END)
        except Exception:                               # pragma: no cover
            return
        head = [run_headline(rt.run)]
        line2 = run_change_line(rt.run.prev_number, rt.run.changed)
        if line2:
            head.append(line2)
        if not is_newest:
            head.append(run_stale_banner(current))
        for line in head:
            txt.insert(tk.END, line + "\n")
        if not is_newest:
            ln = len(head)
            txt.tag_add("stale", f"{ln}.0", f"{ln}.end")
        txt.insert(tk.END, "\n")
        self._write_run_report(txt, rt.run)
        if had_text:
            txt.yview_moveto(where)
        else:
            txt.see("1.0")

    def _render_all_run_tabs(self) -> None:
        """Re-render every page: the 'not this page' banner is relative to the
        newest run, so a new run changes what every OTHER page has to say."""
        for rt in list(self.app._run_tabs):
            self._render_run_tab(rt)
            self._render_run_tab_label(rt)

    def _refresh_keep_button(self) -> None:
        rt = self._selected_run_tab()
        kept = len(self._kept_run_tabs())
        cap = self._kept_cap()
        if rt is None:
            state = "none"
        elif rt.kept:
            state = "kept"
        elif kept >= cap:
            state = "full"
        else:
            state = "free"
        try:
            self._keep_btn.configure(text=keep_button_label(kept, cap, state))
            if state == "free":
                self._keep_btn.state(["!disabled"])
            else:
                self._keep_btn.state(["disabled"])
            # The label just changed WIDTH -- 'Keep run' to 'Keep (5/5) — full'
            # is the whole point of it -- and a ReflowRow re-lays only from
            # add() and from its own <Configure>, neither of which a child's
            # new text fires.  Without this the strip goes on forcing the old
            # width and the button is CLIPPED with no ellipsis, which is
            # exactly the state the long-label measurement was taken from.
            self._results_header.refresh()
        except Exception:                               # pragma: no cover
            pass

    def _keep_run_tab(self, rt: RunTab) -> bool:
        """
        Move one page out of the auto ring and into the kept set.

        The cap bites HERE, on the action the user took -- and by then the
        button is already disabled and already says why, so this is the
        backstop, not the message.
        """
        if rt.kept:
            return False
        if len(self._kept_run_tabs()) >= self._kept_cap():
            return False
        rt.kept = True
        self._render_run_tab_label(rt)
        self._refresh_keep_button()
        return True

    def _on_keep_run(self) -> None:
        rt = self._selected_run_tab()
        if rt is None:
            return
        if self._keep_run_tab(rt):
            self._append_result(
                f"  Keeping run #{rt.run.number}: Calculate will not evict it "
                f"({len(self._kept_run_tabs())}/{self._kept_cap()} kept).")

    # -- the tab strip's right-click menu

    def _run_tab_at(self, x: int, y: int) -> Optional[RunTab]:
        try:
            idx = self.results_nb.index(f"@{x},{y}")
        except Exception:
            return None
        try:
            name = self.results_nb.tabs()[idx]
        except Exception:                               # pragma: no cover
            return None
        for rt in self.app._run_tabs:
            if str(rt.frame) == name:
                return rt
        return None

    def _on_run_tab_context_menu(self, event) -> None:
        rt = self._run_tab_at(event.x, event.y)
        if rt is None:
            # The Log, or the empty strip to the right of the last tab.  The
            # Log cannot be kept and cannot be closed, so there is no menu.
            return
        self._sync_run_tab_menu(rt)
        try:
            self._run_tab_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._run_tab_menu.grab_release()

    def _sync_run_tab_menu(self, rt: "RunTab") -> None:
        """Point the tab menu at one page and label its entries for it.

        Split out of the popup handler so it can be driven without an event --
        same shape as _sync_trace_menu, and the same reason.
        """
        self.app._run_tab_menu_target = rt
        kept = len(self._kept_run_tabs())
        cap = self._kept_cap()
        can_keep = (not rt.kept) and kept < cap
        if rt.kept:
            state = "kept"
        elif can_keep:
            state = "free"
        else:
            state = "full"
        # long=True: a menu entry is not width-bound, so this is where the
        # sentence the button cannot afford at 150% DPI actually lives.  The
        # button is what sends the user here, so it has to be here.
        self._run_tab_menu.entryconfigure(
            0, state=(tk.NORMAL if can_keep else tk.DISABLED),
            label=keep_button_label(kept, cap, state, long=True))
        self._run_tab_menu.entryconfigure(
            2, state=(tk.NORMAL if len(self.app._run_tabs) > 1 else tk.DISABLED))

    def _on_menu_keep_run(self) -> None:
        rt = self.app._run_tab_menu_target
        if rt is not None and self._keep_run_tab(rt):
            self._append_result(
                f"  Keeping run #{rt.run.number}: Calculate will not evict it "
                f"({len(self._kept_run_tabs())}/{self._kept_cap()} kept).")

    def _on_menu_close_run(self) -> None:
        rt = self.app._run_tab_menu_target
        if rt is None:
            return
        self.app._run_tab_menu_target = None
        self._destroy_run_tab(rt)
        self._render_all_run_tabs()
        self._refresh_keep_button()

    def _on_menu_close_other_runs(self) -> None:
        """
        Close every other run page -- except the kept ones.

        "A kept run is destroyed only by Close THIS run" is the whole of the
        rule, and a bulk command that quietly broke it is exactly the surprise
        keeping exists to prevent.  The menu entry says so.
        """
        rt = self.app._run_tab_menu_target
        if rt is None:
            return
        for other in list(self.app._run_tabs):
            if other is not rt and not other.kept:
                self._destroy_run_tab(other)
        self._render_all_run_tabs()
        self._refresh_keep_button()

    # -- the Runs menubutton

    def _rebuild_runs_menu(self) -> None:
        m = self._runs_menu
        m.delete(0, tk.END)
        if not self.app._run_tabs:
            m.add_command(label="(no runs yet — press Calculate)",
                          state=tk.DISABLED)
        else:
            for rt in self.app._run_tabs:
                mark = RUN_KEPT_GLYPH if rt.kept else RUN_OPEN_GLYPH
                m.add_command(
                    label=f"{mark} {run_headline(rt.run)}",
                    command=lambda t=rt: self._select_results_tab(t.frame))
        m.add_separator()
        auto = tk.Menu(m, tearoff=0)
        for n in range(1, RUN_AUTO_MAX_UI + 1):
            auto.add_radiobutton(
                label=str(n), value=n, variable=self.app._run_auto_var,
                command=self._on_run_caps_changed)
        m.add_cascade(label="Auto runs kept (evicted oldest first)", menu=auto)
        total = tk.Menu(m, tearoff=0)
        for n in range(RUN_TABS_MIN, RUN_TABS_HARD_CAP + 1):
            total.add_radiobutton(
                label=str(n), value=n, variable=self.app._run_tabs_var,
                command=self._on_run_caps_changed)
        m.add_cascade(label="Max run tabs (auto + kept)", menu=total)

    def _on_run_caps_changed(self) -> None:
        """
        Apply the two caps from the Runs menu.

        The auto ring is clamped to leave at least one kept slot, so the kept
        cap can never reach zero and the Keep button can never be permanently
        disabled with nothing to close.
        """
        try:
            total = int(self.app._run_tabs_var.get())
            auto = int(self.app._run_auto_var.get())
        except Exception:                               # pragma: no cover
            return
        self.app._run_tabs_max = max(RUN_TABS_MIN,
                                 min(total, RUN_TABS_HARD_CAP))
        self.app._run_auto_max = max(1, min(auto, self.app._run_tabs_max - 1))
        self.app._run_auto_var.set(self.app._run_auto_max)
        self.app._run_tabs_var.set(self.app._run_tabs_max)
        self._evict_run_tabs()
        self._render_all_run_tabs()
        self._refresh_keep_button()

    def _append_result(self, text: str, severity: str = LOG_INFO) -> None:
        """
        Write one line to the Log.

        `severity` defaults to LOG_INFO, which is exactly what every call site
        did before the Results pane became a notebook.  LOG_WARN counts
        towards the Log tab's badge while the Log is not on screen; LOG_ERROR
        brings the Log to the front instead, because an error the user never
        sees is worse than a tab switch they did not ask for.
        """
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)
        if severity == LOG_ERROR:
            # No badge: the log is now on screen, so nothing about it is unseen.
            self._select_log_tab()
        elif severity == LOG_WARN and not self._log_selected():
            self.app._log_unseen += 1
            self._render_log_badge()

    def _append_swatched(self, text: str, color_idxs: Sequence[int]) -> None:
        """
        Append `text` and colour the leading swatch of each of its data rows.

        Rows are found by their RESULTS_SWATCH prefix and consumed in order,
        so no line-number arithmetic has to be kept in step with however many
        header lines _format_results_table decides to emit (the file-alias
        line alone is already conditional on the trace count).

        Tk clamps an insert at END to just before the Text's trailing newline,
        so the first line of `text` lands on the line `end-1c` names now.
        """
        base = int(self.results_text.index("end-1c").split(".")[0])
        self._append_result(text)
        _tag_swatch_rows(self.results_text, base, text, color_idxs)
