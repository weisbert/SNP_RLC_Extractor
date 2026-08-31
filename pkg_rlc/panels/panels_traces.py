"""
pkg_rlc_panels_traces.py  --  the "Traces" panel of the main window.

The Traces section of the left-hand column: the four buttons, the Listbox, the
right-click menu, and every operation that adds, removes, duplicates, hides,
freezes or unfreezes a trace -- plus the list renderer, which runs on every
keystroke and is why it returns early when nothing would look different.

HAS-A, NOT IS-A, for the reason spelled out in pkg_rlc_panels_files: what this
section has to keep right is ORDER.  `Show/Hide` is the FOURTH button in a row
measured at 448 px against 364 for four buttons, and pack unmaps from the END;
the right-click menu is created after the `<space>` binding and before the
Files menu, and its four entries are enumerated BY INDEX by
tests/test_freeze_trace.py.  So the panel exposes `bind_events()` and
`App._bind_events` calls it at the moment those lines used to run.

It may not import `pkg_rlc_gui` (L5 -> L6, tests/test_layering.py).  The two
context-menu labels moved here WITH the menu they name and are re-exported
from `pkg_rlc.frontend.app`; the handful of pure model helpers this panel still needs
-- `_duplicate_trace_config`, `freeze_refusal`, `_freeze_trace_config`,
`_snapshot_row`, `_snapshot_block` -- construct a `TraceConfig` or a
`RunSnapshot` and so cannot come down until those types do.  Until then they
are reached through the injected App, which holds them as plain aliases of the
module-level functions, so the two spellings are the same object and cannot
drift.
"""

from __future__ import annotations

from dataclasses import replace

import tkinter as tk
from tkinter import messagebox, ttk

from pkg_rlc.widgets.plot import COLORS
from pkg_rlc.present.report import LOG_WARN
from pkg_rlc.panels.attrib_gui import ATTRIB_MENU_LABEL, refresh_attribution_windows
from pkg_rlc.panels.files_gui import FILES_MENU_LABEL, refresh_files_windows

# The two Traces-list context-menu entries.  Named constants because three
# tests and one menu lookup key off them, and a menu entry nobody can find is
# the same as no feature at all.  They live beside the menu they label and are
# re-exported from pkg_rlc_gui, where every existing caller looks for them.
# (`FROZEN_EDITOR_NOTE`, which was written next to these, belongs to the
# EDITOR and stayed there.)
FREEZE_MENU_LABEL = "Freeze as new trace"
UNFREEZE_MENU_LABEL = "Unfreeze"
#: The entry that empties the Traces list.  On the MENU and not as a fifth
#: button for the reason the two above are: the row is 448 px against 364 for
#: four buttons and `pack` unmaps from the end.
CLEAR_TRACES_MENU_LABEL = "Clear all traces"


class TracesPanel:
    """The Traces section: `Traces` frame, its buttons and its Listbox."""

    def __init__(self, parent: ttk.Frame, app) -> None:
        self.app = app
        self._trace_menu: tk.Menu | None = None

        # --- Traces section ---
        traces_frame = ttk.LabelFrame(parent, text="Traces")
        traces_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        tr_btn_row = ttk.Frame(traces_frame)
        tr_btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(tr_btn_row, text="Add Trace", command=self._on_add_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(tr_btn_row, text="Remove", command=self._on_remove_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(tr_btn_row, text="Duplicate", command=self._on_duplicate_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        # FOURTH button in this row, and pack unmaps from the end, so this was
        # measured before it was added: at the 1040x600 minsize the row is
        # 448 px and four buttons ask 364 (three ask 273).  Re-measure before a
        # fifth.  It duplicates the editor's "Plot: this trace" checkbox on
        # purpose -- the checkbox needs the trace selected first, and the
        # keyboard route (space) is invisible.
        ttk.Button(tr_btn_row, text="Show/Hide", command=self._on_toggle_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.traces_lb = tk.Listbox(traces_frame, height=8, exportselection=False,
                                    activestyle="dotbox")
        self.traces_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

    # ---------------------------------------------------------------- events

    def bind_selection(self) -> None:
        """Called from `App._bind_events`, in the position this line held."""
        self.traces_lb.bind("<<ListboxSelect>>",
                            lambda e: self.app._on_trace_selected())

    def bind_context_menu(self) -> None:
        """Called from `App._bind_events`, in the position these lines held."""
        app = self.app
        # Space toggles the selected trace's visibility -- the sweep gesture for
        # "hide these four".  Returning "break" stops the Listbox class binding
        # from also treating space as select-activate-item.
        self.traces_lb.bind("<space>", self._on_toggle_trace_key)

        # Freeze / Unfreeze live on a right-click menu, NOT on a fifth button:
        # the Traces row is measured at 448 px with four buttons already asking
        # 364, and Global Controls has no spare row either (a fifth one comes
        # straight out of an editor viewport that is down to 45 px at the
        # minsize).
        self._trace_menu = tk.Menu(app, tearoff=0)
        self._trace_menu.add_command(label=FREEZE_MENU_LABEL,
                                     command=self._on_freeze_trace)
        self._trace_menu.add_command(label=UNFREEZE_MENU_LABEL,
                                     command=self._on_unfreeze_trace)
        # Attribution is on this menu for the same reason Freeze is: it acts on
        # ONE trace, and the right-click selects the row under the pointer
        # first, so the gesture and the subject cannot disagree.  It is also on
        # the Analyze menu, which is the discoverable route -- a right-click
        # menu is invisible until you try it.
        #
        # APPENDED, and with NO separator in front of it.  A separator carries
        # no -label, so `entrycget(i, "label")` raises TclError on it, and the
        # existing guard in tests/test_freeze_trace.py enumerates this menu's
        # labels by index; a separator would turn a one-token test update into
        # an error.  Three commands, all acting on the selected trace, do not
        # need a rule between them anyway.
        self._trace_menu.add_command(label=ATTRIB_MENU_LABEL,
                                     command=app._on_attribution)
        # APPENDED for the same two reasons, and it is the FOURTH entry: the
        # file set belongs to a trace, and the right-click selects the row
        # under the pointer first so the gesture and the subject cannot
        # disagree.  Still no separator -- see above.
        self._trace_menu.add_command(label=FILES_MENU_LABEL,
                                     command=app._on_files_window)
        # APPENDED, and it is the FIFTH entry.  Still no separator, for the
        # reason above: `tests/test_freeze_trace.py` enumerates this menu's
        # labels by index and a separator carries no `-label`.  It is the one
        # entry here that does NOT act on the row under the pointer, which is
        # why it is last and why it asks before it acts.
        self._trace_menu.add_command(label=CLEAR_TRACES_MENU_LABEL,
                                     command=self._on_clear_traces)
        self.traces_lb.bind("<Button-3>", self._on_trace_context_menu)

    # -------------------------------------------------------------- Trace ops

    def _on_add_trace(self) -> None:
        app = self.app
        if not app.files:
            messagebox.showinfo("No file", "Add a file first.")
            return
        fe = app.files[app._sel_idx(app.files_lb) or 0]
        tc = app._make_default_trace(fe)
        app.traces.append(tc)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        app._on_trace_selected()

    def _on_remove_trace(self) -> None:
        app = self.app
        idx = app._sel_idx(self.traces_lb)
        if idx is None:
            return
        app.traces.pop(idx)
        self._refresh_trace_list()
        app._replot_from_cache()
        # An Attribution window HOLDS a result, so unlike the Ports & Roles
        # window it cannot re-read app.traces and degrade -- it has to be
        # told.  Same class of omission as the _on_remove_file
        # forgot-to-replot bug: nothing raises, and the window carries on
        # naming a trace that is gone with a [Recompute] button that would
        # answer about nothing.  It resolves its subject by identity against
        # app.traces, so this call is the whole of what is needed.
        refresh_attribution_windows(app)
        # Same reason, same position: a file window resolves its subject by
        # identity too, and one on a trace that is gone would keep offering
        # [Set as home] on it.
        refresh_files_windows(app)

    def _on_clear_traces(self) -> None:
        """
        Empty the Traces list in one gesture.  The FILES stay loaded.

        That is the whole difference from `Clear all files`, and it is the
        case this entry exists for: keeping the measurement data and throwing
        away the specs written against it is how a second port map gets tried
        without re-parsing a 300-port file.

        A spec is not recoverable by retyping it in a hurry, so this asks
        first -- and it asks only when there is something to lose.  A FROZEN
        trace is included: it is a snapshot of results, and this gesture says
        every trace.  The run PAGES are untouched, so the numbers a frozen
        trace was compared against are still on screen to read.
        """
        app = self.app
        if not app.traces:
            return
        frozen = sum(1 for t in app.traces if t.frozen)
        note = f"Remove all {len(app.traces)} trace(s)? The loaded files stay."
        if frozen:
            note += (f" {frozen} of them are frozen snapshots, which cannot "
                     f"be recomputed from a file that has since changed.")
        if not messagebox.askyesno(CLEAR_TRACES_MENU_LABEL, note, parent=app):
            return
        # Cancel, don't flush -- the queued edit belongs to a trace that is
        # about to be discarded (`_apply_session`'s rule and its reason).
        app._cancel_editor_sync()
        n = len(app.traces)
        app.traces = []
        self._refresh_trace_list()
        # The same three calls `_on_remove_trace` ends on, and each for its own
        # reason: the plot would keep drawing curves whose traces are gone, and
        # neither window can re-read its way out of a subject that no longer
        # exists -- they have to be told.
        app._replot_from_cache()
        refresh_attribution_windows(app)
        refresh_files_windows(app)
        app._append_result(f"Cleared all traces ({n})")

    def _on_toggle_trace_key(self, _event=None) -> str:
        self._on_toggle_trace()
        return "break"      # or the Listbox also select-activates on space

    def _on_toggle_trace(self) -> None:
        """
        Show / hide the selected trace without deleting it.

        Replots from the cached Z instead of recomputing: the whole point is
        that taking a curve off the plot should not cost a Schur reduction of a
        153-port file to arrive at numbers that have not changed.
        """
        app = self.app
        app._flush_editor_sync()
        idx = app._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = app.traces[idx]
        tc.enabled = not tc.enabled
        # The editor is showing this trace, so its checkbox has to follow --
        # suppressed, or the write trace schedules a sync that would just write
        # the same value back.
        app._suppress_editor_sync = True
        try:
            app.ed_enabled_var.set(tc.enabled)
        finally:
            app._suppress_editor_sync = False
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)
        app._replot_from_cache()

    def _on_duplicate_trace(self) -> None:
        app = self.app
        # Without the flush the copy is taken from the trace as it was BEFORE
        # the edit still queued in the editor -- i.e. Duplicate would silently
        # copy something the user cannot see any more.
        app._flush_editor_sync()
        idx = app._sel_idx(self.traces_lb)
        if idx is None:
            return
        new = app._duplicate_trace_config(app.traces[idx], app._next_trace_id)
        app._next_trace_id += 1
        app.traces.append(new)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        app._on_trace_selected()

    # ------------------------------------------------------- Freeze / Unfreeze
    #
    # "Freeze as new trace" is the answer to "is this better than what I had?".
    # A results log can only compare two NUMBERS at the marker frequency; a
    # frozen trace is a second curve, so the two specs are compared over the
    # whole sweep, side by side, with the cursor readout printing both in
    # adjacent columns and both landing in the results table and the CSV.
    #
    # What makes it trustworthy is that a frozen trace is inert: Calculate
    # skips it (_on_calculate) and the editor refuses to write it
    # (_sync_editor_to_trace).  Everything else is unchanged -- it plots, it
    # hides, it exports, Remove removes it.

    def _on_trace_context_menu(self, event) -> None:
        """
        Right-click on the Traces list.

        The click SELECTS the row under the pointer first.  A menu that acts on
        whatever happened to be selected before is how you freeze the wrong
        trace -- and the two entries are enabled from that row's state, so the
        selection has to be settled before the menu is posted.
        """
        app = self.app
        idx = self.traces_lb.nearest(event.y)
        if idx < 0 or idx >= len(app.traces):
            return
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(idx)
        self.traces_lb.activate(idx)
        app._on_trace_selected()
        self._sync_trace_menu(app.traces[idx])
        try:
            self._trace_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._trace_menu.grab_release()

    def _sync_trace_menu(self, tc) -> None:
        """Only one of the FREEZE pair is ever live, and it says which."""
        self._trace_menu.entryconfigure(
            FREEZE_MENU_LABEL, state=tk.DISABLED if tc.frozen else tk.NORMAL)
        self._trace_menu.entryconfigure(
            UNFREEZE_MENU_LABEL,
            state=tk.NORMAL if tc.frozen else tk.DISABLED)
        # Attribution is the exception: it stays LIVE even on a trace it cannot
        # run on, because `attribution_refusal` names five different reasons
        # (frozen / file not loaded / never calculated / one measurement port /
        # edited since the last Calculate) and each of them tells the user what
        # to do next.  A greyed entry would be the same bug report -- the
        # identical decision, in the identical words, as the Freeze entry on a
        # stale trace.  It is set explicitly rather than left at its default so
        # the invariant is stated where it can be read and asserted.
        self._trace_menu.entryconfigure(ATTRIB_MENU_LABEL, state=tk.NORMAL)
        # LIVE on a frozen trace too, and for the same reason: the window is
        # read-only on one (`_apply_file_set` refuses by name), and a snapshot
        # is exactly the trace whose file set someone wants to READ while
        # comparing it against the live one beside it.
        self._trace_menu.entryconfigure(FILES_MENU_LABEL, state=tk.NORMAL)

    def _on_freeze_trace(self) -> None:
        app = self.app
        # Same flush as Duplicate, for the same reason: without it the snapshot
        # is taken from the trace as it was BEFORE the edit still sitting in
        # the idle queue -- i.e. it would freeze a spec that is not on screen.
        app._flush_editor_sync()
        idx = app._sel_idx(self.traces_lb)
        if idx is None:
            messagebox.showinfo("No trace", "Select a trace first.")
            return
        src = app.traces[idx]
        if src.frozen:
            return
        # The flush above is what makes the stale check reliable: the freshest
        # spec is on the trace by now, so `stale` answers about the spec the
        # user can see rather than the one that was there an event ago.
        refusal = app.freeze_refusal(src)
        if refusal:
            messagebox.showinfo(*refusal)
            return
        tc = app._freeze_trace_config(src, app._next_trace_id)
        app._next_trace_id += 1
        app.traces.append(tc)
        self._refresh_trace_list()
        # The SOURCE stays selected: freezing is the first half of "now change
        # something and look at the difference", so the editor must not jump to
        # the copy the user is not going to edit.
        self.traces_lb.selection_set(idx)
        app._append_result(
            f"  Froze [{src.id}] {src.label} as [{tc.id}] {tc.label}: it keeps "
            f"these numbers, Calculate skips it and the editor will not write "
            f"it (right-click → {UNFREEZE_MENU_LABEL} to release it).")
        # It goes into the results table NOW, not at the next Calculate -- the
        # table is where the two are read against each other, and a baseline
        # that appears one press later is a baseline nobody trusts.  It joins
        # the CURRENT run rather than starting one: the run number counts
        # Calculates, and freezing measures nothing.
        run = app._last_run or app._empty_run()
        if tc.coupling is not None:
            run = replace(run, blocks=run.blocks + (
                app._snapshot_block(tc, tc.file_label, tc.coupling),))
        elif tc.rlc is not None:
            run = replace(run, rows=run.rows + (
                app._snapshot_row(tc, tc.file_label, tc.rlc),))
        app._last_run = run
        app._render_results(run)
        # The run PAGE is rewritten in place for the same reason: freezing
        # measures nothing, so it is not a new run and gets no new tab.
        newest = app._newest_run_tab()
        if newest is not None and newest.run.number == run.number:
            newest.run = run
            app._render_run_tab(newest)
        app._replot_from_cache()

    def _on_unfreeze_trace(self) -> None:
        app = self.app
        idx = app._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = app.traces[idx]
        if not tc.frozen:
            return
        tc.frozen = False
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)
        # The selection is this trace, so the editor showing it has to come
        # back to life in the same gesture.
        app._set_editor_editable(True)
        app._append_result(
            f"  [{tc.id}] {tc.label} is no longer frozen: the next Calculate "
            f"will recompute it and REPLACE the snapshot numbers it is "
            f"holding.", LOG_WARN)

    # ------------------------------------------------------------- rendering

    def _refresh_trace_list(self) -> None:
        """
        Re-render the trace list, but only when it would actually look
        different.

        Auto-apply calls this on every keystroke, and rebuilding a Listbox
        resets its scroll position -- so a user editing trace 9 of 12 would be
        yanked back to the top on each character.  Comparing the rendered
        strings costs nothing and is what makes the live list usable.
        (Programmatic delete / insert / selection_set do NOT fire
        <<ListboxSelect>>, verified on Tk 8.6, so this cannot re-enter
        _on_trace_selected and reload the editor mid-typing.)
        """
        app = self.app
        lines = [tc.info_str() for tc in app.traces]
        # The cache key carries the COLOUR as well as the text.  info_str() has
        # no colour in it -- deliberately, the ☑/☐ prefix is the only state it
        # renders -- so picking a new palette slot leaves `lines` byte-identical
        # and the early return would keep the old foreground on screen forever,
        # with the plot already redrawn in the new one.
        key = [(ln, tc.color_idx) for ln, tc in zip(lines, app.traces)]
        if key == app._trace_list_shown:
            return
        app._trace_list_shown = key
        sel = app._sel_idx(self.traces_lb)
        self.traces_lb.delete(0, tk.END)
        for i, (tc, line) in enumerate(zip(app.traces, lines)):
            self.traces_lb.insert(tk.END, line)
            # itemconfig does not survive delete(), so BOTH foregrounds are
            # re-applied here every time rather than at the point of the
            # toggle.  The colour is the only thing tying a name in this list
            # to a curve on the plot -- without it four traces are four
            # identical lines of black text and the reader has to open the
            # editor on each one to find out which curve is which.  A hidden
            # trace keeps the grey: it has no curve to be tied to, and grey
            # is the state, not the style.
            #
            # A mode-6 trace expands into several curves taking consecutive
            # palette slots (_coupling_plot_traces), so this is its FIRST
            # colour -- the same one the style preview shows.
            self.traces_lb.itemconfig(
                i, foreground=("#909090" if not tc.enabled
                               else COLORS[tc.color_idx % len(COLORS)]))
        if sel is not None and sel < len(app.traces):
            self.traces_lb.selection_set(sel)
