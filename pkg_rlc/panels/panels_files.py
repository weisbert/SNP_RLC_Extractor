"""
pkg_rlc_panels_files.py  --  the "Loaded Files" panel of the main window.

The Files section of the left-hand column: the four buttons, the Listbox, the
right-click menu, and every operation that adds, removes, checks or re-renders
a loaded Touchstone file.

HAS-A, NOT IS-A.  This is a plain object that OWNS its widgets and is handed
the `App` at construction; it is deliberately NOT a mixin.  Almost every rule
this panel has to keep is a rule about ORDER -- which button is fourth in the
row (pack unmaps from the END, and `Check File` is the one measured against
that), when the right-click menu is created, when the Listbox bindings are
installed -- and a mixin hides exactly that.  So the panel exposes
`bind_events()` and `App._bind_events` calls it at the same moment the same
lines used to run.

It may not import `pkg_rlc_gui`: this module is L5 in `tests/test_layering.py`
and the frontend is L6, so reaching up for `FileEntry` -- even from inside a
function -- is the dodge that gate exists to refuse.  The one L6 thing this
panel needs is a `FileEntry`, and it asks the App for one
(`App._make_file_entry`), exactly as it already asks for a default
`TraceConfig` (`App._make_default_trace`).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pkg_rlc.physics.core import (
    TouchstoneData,
    TouchstoneParseError,
    diagnose_touchstone,
    parse_touchstone,
)
from pkg_rlc.present.report import LOG_INFO, LOG_WARN, _trunc_str
from pkg_rlc.model.validate import trace_file_labels
from pkg_rlc.panels.attrib_gui import refresh_attribution_windows
from pkg_rlc.panels.files_gui import FILES_MENU_LABEL, refresh_files_windows


#: The Files-list context-menu entry that empties the list.  A named constant
#: because the test looks the entry up by label, and a menu entry nobody can
#: find is the same as no feature at all.  Re-exported from
#: `pkg_rlc.frontend.app`, where every other menu label of this kind is.
CLEAR_FILES_MENU_LABEL = "Clear all files"


class FilesPanel:
    """The Files section: `Loaded Files` frame, its buttons and its Listbox."""

    def __init__(self, parent: ttk.Frame, app) -> None:
        self.app = app
        self._files_menu: tk.Menu | None = None

        # --- Files section ---
        files_frame = ttk.LabelFrame(parent, text="Loaded Files")
        files_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        btn_row = ttk.Frame(files_frame)
        btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(btn_row, text="Add File...", command=self._on_add_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Remove", command=self._on_remove_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Show Ports", command=app._on_show_ports
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Check File", command=self._on_check_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.files_lb = tk.Listbox(files_frame, height=5, exportselection=False,
                                   activestyle="dotbox")
        self.files_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

    # ---------------------------------------------------------------- events

    # Two methods, not one, because `App._bind_events` interleaves these with
    # the Traces list's bindings and its right-click menu, and the order in
    # which two `tk.Menu(app)` widgets are created is the order of their Tcl
    # pathnames.  Nothing is known to depend on it; keeping the calls where
    # the lines were is cheaper than finding out.

    def bind_selection(self) -> None:
        """Called from `App._bind_events`, in the position this line held."""
        self.files_lb.bind("<<ListboxSelect>>", lambda e: self._on_file_selected())

    def bind_context_menu(self) -> None:
        """Called from `App._bind_events`, in the position these lines held."""
        # The Files list gets the SAME entry, because "which files is this
        # trace made of" is the question a user asks while looking at the
        # Files list -- and because a right-click there is the only gesture
        # that reaches the window without going via the menubar.  It does NOT
        # select a file row: the window is about the SELECTED TRACE, and
        # moving the file selection under the pointer would change what the
        # editor and Ports & Roles are describing as a side effect of asking a
        # question about something else.
        self._files_menu = tk.Menu(self.app, tearoff=0)
        self._files_menu.add_command(label=FILES_MENU_LABEL,
                                     command=self.app._on_files_window)
        # APPENDED, and on the MENU rather than as a fifth button: the row is
        # measured at 448 px with four buttons already asking 364, and `pack`
        # unmaps from the END -- `Check File` is the one that would go, and
        # `tests/test_parse_diagnostics.py` asserts it is on screen at the
        # minsize.  Same reason Freeze / Unfreeze are on the Traces menu.
        # No separator: one carries no `-label`, and both menus in this window
        # are enumerated by label.
        self._files_menu.add_command(label=CLEAR_FILES_MENU_LABEL,
                                     command=self._on_clear_files)
        self.files_lb.bind("<Button-3>", self._on_files_context_menu)

    # --------------------------------------------------------------- File ops

    def _load_one_file(self, path: str) -> TouchstoneData | None:
        """
        Parse one file, reporting a failure in terms the user can act on.

        `str(TouchstoneParseError)` is already the full report -- line number,
        verdict, next step -- so the dialog just shows it.  When the parser
        says the file could still be read by skipping the bad values, that is
        offered as a button rather than buried in the text: a user who only
        wants to look at a sweep should not have to find a CLI flag, and the
        warnings the lenient read produces say loudly enough that the numbers
        are suspect.
        """
        try:
            return parse_touchstone(path)
        except TouchstoneParseError as e:
            if not e.retry_lenient:
                messagebox.showerror("Cannot read file", str(e))
                return None
            if not messagebox.askyesno(
                    "Cannot read file",
                    f"{e}\n\nLoad it anyway, skipping the values that do not "
                    f"parse?"):
                return None
            try:
                return parse_touchstone(path, lenient=True)
            except TouchstoneParseError as e2:
                messagebox.showerror("Cannot read file", str(e2))
                return None
        except Exception as e:                          # pragma: no cover
            messagebox.showerror("Cannot read file", f"{path}\n\n{e}")
            return None

    def _on_add_file(self) -> None:
        app = self.app
        paths = filedialog.askopenfilenames(
            title="Select Touchstone file(s)",
            filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                       ("All files", "*.*")],
        )
        for p in paths:
            ts = self._load_one_file(p)
            if ts is None:
                continue
            fe = app._make_file_entry(ts)
            app.files.append(fe)
            app._append_result("")
            for line in ts.summary_lines():
                # The summary is a description of the file (info), except for
                # the parser's own WARN lines -- "I guessed" / "I threw
                # something away" is the one part of it that must announce
                # itself when the Log is not the tab on screen.
                app._append_result(
                    line,
                    LOG_WARN if line.lstrip().startswith("WARN:") else LOG_INFO)
            # Auto-create a default trace bound to this file
            tc = app._make_default_trace(fe)
            app.traces.append(tc)
        self._refresh_file_list()
        app._refresh_trace_list()
        app._refresh_file_combobox()
        # Select last-added file/trace for convenience
        if app.files:
            self.files_lb.selection_clear(0, tk.END)
            self.files_lb.selection_set(tk.END)
            self.files_lb.activate(tk.END)
        if app.traces:
            app.traces_lb.selection_clear(0, tk.END)
            app.traces_lb.selection_set(tk.END)
            app.traces_lb.activate(tk.END)
            app._on_trace_selected()

    def _on_remove_file(self) -> None:
        app = self.app
        idx = app._sel_idx(self.files_lb)
        if idx is None:
            return
        fe = app.files.pop(idx)
        # Drop traces bound to this file -- in ANY of their slots, not only as
        # the home file.  A composed trace whose second file has gone is not a
        # single-file trace, it is a trace that can no longer be computed at
        # all, and leaving it in the list to say so on the next Calculate is
        # the same "the plot and the Traces list disagree" the call below
        # exists to prevent.
        #
        # Identity, never `t not in dropped`: TraceConfig is an eq=True
        # dataclass holding numpy arrays, so == against a non-matching trace
        # raises "truth value of an array is ambiguous" -- the documented
        # reason _apply_editor_sync uses `any(t is tc ...)`.
        keep, dropped = [], []
        for t in app.traces:
            (dropped if fe.label in trace_file_labels(t) else keep).append(t)
        app.traces = keep
        # A trace removed because of its HOME file needs no explanation -- the
        # Files row is right there.  One removed because of a file it merely
        # composed with does: the name that went is not the name on the trace.
        by_extra = [t for t in dropped if t.file_label != fe.label]
        self._refresh_file_list()
        app._refresh_trace_list()
        app._refresh_file_combobox()
        # Same call, same position, same reason as _on_remove_trace: the traces
        # bound to this file are gone from the list, and without this the PLOT
        # keeps drawing and legending their curves until the next Calculate.
        # The readout box IS the legend, so the stale name sat in the cursor
        # readout too -- the plot and the Traces list disagreeing about which
        # measurements exist is exactly what the run pages' banner exists to
        # prevent.  _replot_from_cache already skips a trace whose file is
        # gone, so this needs nothing but the call.
        app._replot_from_cache()
        # Same call, same reason, as in _on_remove_trace: a window whose trace
        # went with this file, or whose file alone went, must stop claiming it
        # can recompute.  Both cases land here -- the traces bound to the file
        # were dropped above, and a window on a trace bound to it resolves its
        # file through _file_by_label, which now returns None.
        refresh_attribution_windows(app)
        # And the same for the file windows, which is the LOUDER case here:
        # this is the one path that can remove a file a surviving trace still
        # composes with, so an open window would keep listing a file that is
        # gone with a port count beside it.
        refresh_files_windows(app)
        app._append_result(f"Removed {fe.label}")
        if by_extra:
            app._append_result(
                f"  also removed {len(by_extra)} trace(s) that composed with "
                f"it: " + ", ".join(f"[{t.id}] {_trunc_str(t.label, 18)}"
                                    for t in by_extra), LOG_WARN)

    def _on_clear_files(self) -> None:
        """
        Empty the Files list in one gesture, and say what goes with it.

        SAME RULE AS `Remove`, applied to every row at once: a trace bound to
        a file that is gone -- in ANY of its slots, not only as its home file
        -- cannot be computed at all, so it goes too.  Leaving it in the list
        to fail on the next Calculate is the "the plot and the Traces list
        disagree" this panel's other handler is written against.

        THE CONFIRMATION NAMES BOTH COUNTS, because the trace count is the
        half the user cannot see coming: the gesture says 'files' and a mode-6
        spec that took ten minutes to type is not recoverable by re-adding the
        file.  It is asked only when there is something to lose.

        A trace bound to a label that is NOT loaded (a session whose data
        moved) survives, exactly as it survives `Remove` -- it was already not
        computable, and this gesture is about the files that ARE here.
        """
        app = self.app
        if not app.files:
            return
        labels = {fe.label for fe in app.files}
        keep, doomed = [], []
        for t in app.traces:
            (doomed if labels & set(trace_file_labels(t))
             else keep).append(t)
        note = (f"Remove all {len(app.files)} loaded file(s)?")
        if doomed:
            note += (f"\n\n{len(doomed)} trace(s) are bound to them and go "
                     f"too. This cannot be undone.")
        if not messagebox.askyesno("Clear all files", note, parent=app):
            return
        # Cancel, don't flush: the queued edit belongs to a trace that is
        # about to be discarded.  `_apply_session` cancels for this reason and
        # the identity check in `_apply_editor_sync` would decline it anyway --
        # running it just to be declined is a way for that check to rot.
        app._cancel_editor_sync()
        app.files = []
        # `keep`, never `[t for t in app.traces if t not in doomed]`:
        # TraceConfig is an eq=True dataclass holding numpy arrays, so `in`
        # runs == against a non-matching trace and raises "truth value of an
        # array is ambiguous".  The partition above is the documented idiom
        # (`_on_remove_file`, `_apply_editor_sync`).
        app.traces = keep
        # Every entry is keyed by file labels and validated by FileEntry
        # IDENTITY, and every FileEntry it could validate against has just
        # gone -- so the whole cache is dead weight, and a composed stack is
        # the largest thing this app holds.  `Remove` leaves it to the
        # identity check; clearing everything makes dropping it exact.
        app._compose_cache.clear()
        self._refresh_file_list()
        app._refresh_trace_list()
        app._refresh_file_combobox()
        # Same call, same position, same reason as `Remove`: without it the
        # PLOT keeps drawing and legending the curves of traces that are gone,
        # and the readout box IS the legend.
        app._replot_from_cache()
        # And the same two windows, for the same reason: both resolve their
        # subject by identity and neither can re-read its way out of a subject
        # that no longer exists.
        refresh_attribution_windows(app)
        refresh_files_windows(app)
        app._append_result(f"Cleared all files ({len(labels)})")
        if doomed:
            app._append_result(
                f"  also removed {len(doomed)} trace(s) bound to them: "
                + ", ".join(f"[{t.id}] {_trunc_str(t.label, 18)}"
                            for t in doomed), LOG_WARN)

    def _on_check_file(self) -> None:
        """
        Print the file-structure report for the selected file, or for one
        picked from disk when nothing is selected.

        This covers the case the error dialog cannot: the file LOADS, but the
        numbers look wrong.  Then the question is whether the port count was
        guessed, whether the sweep is what was simulated, and whether the
        record grid actually lines up -- and none of that is visible anywhere
        else.  It also reaches files that fail to load, since those never make
        it into the list.
        """
        app = self.app
        idx = app._sel_idx(self.files_lb)
        fe = app.files[idx] if idx is not None else None
        if fe is not None:
            path = fe.ts.source_path
        else:
            path = filedialog.askopenfilename(
                title="Check which Touchstone file?",
                filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                           ("All files", "*.*")])
            if not path:
                return
        app._append_result("")
        for line in diagnose_touchstone(path).splitlines():
            app._append_result(line)

    def _on_file_selected(self) -> None:
        # The Ports & Roles window resolves its file from the Files list first,
        # so selecting a different file there has to re-render it.  Nothing
        # else in the application reacts to this selection.
        self.app._refresh_port_roles_window()

    def _on_files_context_menu(self, event) -> None:
        """
        Right-click on the Files list -> the per-trace file window.

        Deliberately does NOT move the file selection: the window is about the
        selected TRACE, and re-selecting a file would change what the editor's
        Ports & Roles view is describing as a side effect of a question about
        something else.  `_on_show_ports` already falls back to the editor's
        file when nothing is selected, so nothing here depends on it either.
        """
        try:
            self._files_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._files_menu.grab_release()

    def _refresh_file_list(self) -> None:
        self.files_lb.delete(0, tk.END)
        for fe in self.app.files:
            self.files_lb.insert(tk.END, fe.info_str())
