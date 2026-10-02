"""
pkg_rlc/panels/ws_compare.py -- the Compare files WORKSPACE (L5).

`docs/design_workspaces.md` § 4, stage 3.  The owner's question: "I have the
inductor extracted to 30 GHz and again to 50 and 80 GHz -- inside the band
they share, are the others the 30 GHz one?"  ONE reference against N files,
each pair on its own band and grid (`pkg_rlc.present.compare_report`), at
two levels: the raw S-parameters, and L / Q / R under a port setup the user
DEFINED.  It replaces the modeless Compare window (`compare_gui`, deleted).

    LEFT  (packed into `app.compare_ws_left`)     RIGHT (`app.compare_ws_right`)
    Files to compare                              Verdict strip: one line per
      Reference [cbo]                               compared file, a mark and
      Compare  [x] file  [x] file ...               one sentence
    What to compare                               dS / dL / dQ / dR (%), one
      ( ) Raw S-parameters only                     curve per compared file,
      (*) Extracted L/Q/R under this setup:         limit lines, not-judged
          Template [..]  [Copy setup from trace]    regions grey, the marker
          Measurement ports / Connections           dragged on the plot
          (the shared `SetupTables`)              > Details (collapsed): the
    Limits  S [1] %  L [1] %  Q [5] %               full reading per file
            Marker [ ] GHz
    [Compare]

NO DEFAULT PORT SETUP.  With the tables empty, or "Raw S-parameters only"
chosen, the extracted level says "No setup defined" and only S is judged --
a default nobody chose decided the big verdict of the old window.  "Copy
setup from trace" is a ONE-SHOT deep copy (`compare_report.copy_trace_setup`);
nothing links the tables to the trace afterwards.  Composed traces are not
offered: their port numbers span files.

A REFUSED SETUP is painted in the cells (the shared component's probe rules,
never a dialog) AND is the extracted level's "why": a setup with a red cell
is not handed to the solver at all, so the reading cannot show a number the
cells call refused.

STALENESS, as the Trace model workspace: nothing is recomputed behind the
reader's back.  Changing the reference, the checked files, the choice or the
setup -- or the loaded files under it -- marks the results out of date, and
the verdict strip says so first.  The LIMITS re-judge the cached results
without solving anything, and the MARKER re-reads the cached curves.  A
compared file that is no longer loaded loses its line and its curves at
once: the old window went on comparing from arrays it had kept.

The marker belongs to this workspace -- its own GHz field, and a press
anywhere on the plot moves it, a drag follows the pointer and the release
snaps it to the nearest point of the reference's own sweep (nearest in
log f on a log axis, which is the log-space rule CLAUDE.md sets for the RLC
plot's drag).  It no longer reads the RLC workspace's marker.

No `pkg_rlc.frontend.app` import, at module level or inside a function: the
App is passed in and duck-typed.  What it uses: `app.files`, `app.traces`,
and optionally `app._register_scrollable`.
"""

from __future__ import annotations

import math
import tkinter as tk
import warnings
from tkinter import ttk
from typing import Optional

import numpy as np
import matplotlib.ticker as mticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from pkg_rlc.physics import similarity as sim
from pkg_rlc.panels.setup_tables import (
    SetupTables, conn_rows_from_json, issue_text, mports_from_json,
    port_choices, rows_to_json, table_head,
)
from pkg_rlc.present.compare_report import (
    MARK_SAME, NO_SETUP, S_LIMIT_PCT_DEFAULT, Limits, compare_against,
    compare_summary_lines, default_reference, line_tags, not_judged_spans,
    pct_view_span, setup_is_empty, setup_words, trace_setup_choices,
    copy_trace_setup, verdict_strip_lines,
)
from pkg_rlc.widgets.widgets import PLACEHOLDER_FG, ReflowRow

__all__ = [
    "CompareWorkspace", "COMPARE_STATE_VERSION", "WHAT_S", "WHAT_Z",
    "COPY_SETUP_LABEL", "snap_marker_hz", "limits_from_text",
]

#: The inner version of the session's `workspaces.compare` block.  A block
#: newer than this build raises, which the switch turns into one Log note.
COMPARE_STATE_VERSION = 1

#: The two "What to compare" choices, as the session file spells them.
WHAT_S = "s"
WHAT_Z = "z"
DEFAULT_WHAT = WHAT_Z

COPY_SETUP_LABEL = "Copy setup from trace…"   # ttk adds the ▾

CMP_FONT = ("Consolas", 9)
_LIMIT_FG = "#b03030"
_GOOD_FG = "#2e7d32"
_MARKER_FG = "#3b7dd8"
_GREY = "#9a9a9a"
# One colour per COMPARED FILE (the reference is the zero line), one dash
# per measurement port of a multi-port setup.
_CURVE_FG = ("#1b6ca8", "#2e8b57", "#b8860b", "#8b3a62", "#5b5bb0",
             "#a0522d")
_PORT_LS = ("-", "--", ":", "-.")
#: The plot legend's labels are cut here, or the subplots squeeze
#: (CLAUDE.md: "Truncate trace labels to 30 chars").
LEGEND_CHARS = 30

#: The verdict strip grows to this many wrapped lines, then scrolls.
STRIP_MAX_LINES = 7

_PANEL_LABEL = {"S": "ΔS (%)", "L": "ΔL (%)", "Q": "ΔQ (%)", "R": "ΔR (%)"}


# ============================================================================
# Pure helpers
# ============================================================================

def _float(text, default: float) -> float:
    try:
        v = float(str(text).strip())
        return v if math.isfinite(v) else default
    except ValueError:
        return default


def limits_from_text(s_text, l_text, q_text) -> Limits:
    """The three limit boxes as a `Limits`; a box that does not read as a
    number falls back to its default rather than refusing the reading."""
    return Limits(_float(s_text, S_LIMIT_PCT_DEFAULT),
                  _float(l_text, sim.DEFAULT_L_LIMIT_PCT),
                  _float(q_text, sim.DEFAULT_Q_LIMIT_PCT))


def snap_marker_hz(f_hz: float, grid, log: bool = True) -> float:
    """
    The grid point nearest `f_hz` -- nearest in log f when the axis is log
    (the distance the eye judges on a log axis; CLAUDE.md's log-space rule),
    linear otherwise.  `f_hz` unchanged when the grid is empty or the value
    is not a frequency.
    """
    g = np.asarray(grid, dtype=float)
    if g.size == 0 or not math.isfinite(f_hz):
        return f_hz
    if log and f_hz > 0 and np.any(g > 0):
        pos = g[g > 0]
        k = int(np.argmin(np.abs(np.log10(pos) - math.log10(f_hz))))
        return float(pos[k])
    return float(g[int(np.argmin(np.abs(g - f_hz)))])


def _trunc(text: str, n: int = LEGEND_CHARS) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def _setup_key(mports, conn_rows) -> tuple:
    """What a setup IS, for the staleness signature: every row's cells."""
    return (tuple(tuple(sorted(d.items())) for d in rows_to_json(mports)),
            tuple(tuple(sorted(d.items())) for d in rows_to_json(conn_rows)))


# ============================================================================
# The workspace
# ============================================================================

class CompareWorkspace:
    """
    Builds the Compare files workspace into the two frames the App owns, and
    holds its state.  Not a widget itself (the `TraceModelWorkspace` rule):
    the left frame is a child of the App's left host and the right one a
    pane of the outer PanedWindow, and `WorkspaceSwitch` needs them to stay
    exactly that.
    """

    def __init__(self, app, left: ttk.Frame, right: ttk.Frame) -> None:
        self.app = app
        self.left = left
        self.right = right

        # -- state
        self._ref_fe = None                  # the reference FileEntry
        self._ref_chosen = False             # picked by the user / a session
        self._checks: list = []              # [(FileEntry, BooleanVar, Checkbutton)]
        self._check_memory: dict = {}        # id(fe) -> (fe, bool) across rebuilds
        self._pairs: list = []               # [(fe_b, CompareResult)] -- cached
        self._computed_ref = None
        self._computed_inputs: Optional[tuple] = None
        self._details_open = False
        self._dragging = False
        self._rejudge_job = None
        self._relayout_job = None
        self._building = True
        self.x_log = True
        self.curves: dict = {}               # panel -> [Line2D], for tests
        self.marker_lines: list = []

        self._build_left(left)
        self._build_right(right)
        self._building = False
        self.refresh_files()

    # ================================================================ left

    def _build_left(self, parent: ttk.Frame) -> None:
        """
        The left column.  ITS HEIGHT IS A MEASURED BUDGET (Microsoft YaHei UI
        9, tk scaling 1.333; the left host is 460 px wide and, under the
        shared Files panel, 421 px tall at the 1040x600 minsize and 721 at
        1500x900).  Same three rules as the Trace model column: the
        Compare button, the status line and Limits are packed FIRST, at the
        BOTTOM of an inner column only as tall as it asks to be, so on a
        short window it is the setup tables that give, never the button;
        the tables show two rows each before they scroll; and the "Copy
        setup from trace" button rides in the measurement-port table's '+ Add'
        row (`table_head`), which is mostly empty space.

        MEASURED (2026-10-03, this box; three one-port files loaded, setup
        P1 +1 and the connections table's one blank row, after Compare):

          first draft (radios on two rows, Limits in a titled LabelFrame,
          status line always packed, copy button in the Template row):
              req 478 x 472 against a 460 x 421 host -- 18 px too WIDE
              (the copy button cut at the column's edge) and the setup
              tables squeezed from 196 to 145 px at 1040x600.
          as built:
              req 450 x 393.  1040x600: Compare ends at 391 of 421 (30 px
              spare), the setup tables get their full 194 px.  1500x900:
              391 of 721.  The widest child asks 442 of the 460 px.
          worst case (four measurement-port rows, four connection rows,
          seven issue lines under the tables): req 450 x 750; Compare still
          ends at 419 of 421 and the limits row is on screen -- the
          setup tables (222 px) are what is cut.

        The right side at 1040x600 is 575 x 575: verdict strip 74 px (two
        files, five wrapped lines), the plot 567 x 462 with Details closed
        and 567 x 270 with it open (Details 173).  At 1500x900: 1035 x 875,
        strip 46 px, plot 1027 x 785 closed / 566 open (Details 200).
        `tests/test_ws_compare.py::TestTheLayoutBudget` re-measures it.
        """
        col = ttk.Frame(parent)
        col.pack(side=tk.TOP, fill=tk.X)
        self._col = col

        # ---- packed FIRST, at the bottom: never the thing that is clipped
        # The status line is packed only while it has something to say
        # (`_status`): empty, it is 17 px of nothing in a column that has
        # none to spare.
        self.status_lbl = ttk.Label(col, text="", anchor="w",
                                    justify=tk.LEFT, wraplength=430,
                                    foreground=PLACEHOLDER_FG)
        arow = ttk.Frame(col)
        arow.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(4, 2))
        self._arow = arow
        self.compare_btn = ttk.Button(arow, text="Compare",
                                      command=self.compare)
        self.compare_btn.pack(side=tk.LEFT)

        # Limits and the marker: one row, no LabelFrame title of its own --
        # the title line was 20 px.  "Same if within" is what the boxes MEAN.
        lim = ttk.Frame(col)
        lim.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(4, 0))
        lrow = lim
        ttk.Label(lrow, text="Same if within").pack(side=tk.LEFT,
                                                     padx=(4, 0))
        self.s_lim_var = tk.StringVar(master=parent,
                                      value=f"{S_LIMIT_PCT_DEFAULT:g}")
        self.l_lim_var = tk.StringVar(master=parent,
                                      value=f"{sim.DEFAULT_L_LIMIT_PCT:g}")
        self.q_lim_var = tk.StringVar(master=parent,
                                      value=f"{sim.DEFAULT_Q_LIMIT_PCT:g}")
        self.limit_entries = {}
        for key, var in (("S", self.s_lim_var), ("L", self.l_lim_var),
                         ("Q", self.q_lim_var)):
            ttk.Label(lrow, text=key).pack(side=tk.LEFT,
                                           padx=(6 if key == "S" else 4, 0))
            e = ttk.Entry(lrow, textvariable=var, width=4)
            e.pack(side=tk.LEFT, padx=(3, 1))
            ttk.Label(lrow, text="%").pack(side=tk.LEFT)
            self.limit_entries[key] = e
            var.trace_add("write", lambda *_a: self._schedule_rejudge())
        ttk.Label(lrow, text="Marker").pack(side=tk.LEFT, padx=(12, 0))
        self.marker_var = tk.StringVar(master=parent, value="")
        self.marker_entry = ttk.Entry(lrow, textvariable=self.marker_var,
                                      width=7)
        self.marker_entry.pack(side=tk.LEFT, padx=(3, 1))
        ttk.Label(lrow, text="GHz").pack(side=tk.LEFT)
        self.marker_var.trace_add("write", lambda *_a: self._on_marker_text())

        # ---- the top, which gives way first
        files = ttk.LabelFrame(col, text="Files to compare")
        files.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 2))
        rrow = ttk.Frame(files)
        rrow.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 2))
        ttk.Label(rrow, text="Reference", width=10).pack(side=tk.LEFT)
        self.ref_var = tk.StringVar(master=parent, value="")
        self.ref_cbo = ttk.Combobox(rrow, textvariable=self.ref_var,
                                    state="readonly", width=34)
        self.ref_cbo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ref_cbo.bind("<<ComboboxSelected>>", self._on_ref_picked)
        crow = ttk.Frame(files)
        crow.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))
        ttk.Label(crow, text="Compare", width=10).pack(side=tk.LEFT,
                                                       anchor="n")
        self._checks_host = ttk.Frame(crow)
        self._checks_host.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._checks_row: Optional[ReflowRow] = None
        self._no_checks_lbl = ttk.Label(
            self._checks_host, foreground=PLACEHOLDER_FG,
            text="(load another file to compare against the reference)")

        what = ttk.LabelFrame(col, text="What to compare")
        what.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        self.what_var = tk.StringVar(master=parent, value=DEFAULT_WHAT)
        # Both choices on ONE row: a second row was 22 px of the budget.
        wrow = ttk.Frame(what)
        wrow.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(2, 0))
        self.raw_rb = ttk.Radiobutton(wrow, text="Raw S-parameters only",
                                      value=WHAT_S, variable=self.what_var,
                                      command=self._on_what_changed)
        self.raw_rb.pack(side=tk.LEFT)
        self.z_rb = ttk.Radiobutton(wrow, text="Extracted L/Q/R under this "
                                               "setup:",
                                    value=WHAT_Z, variable=self.what_var,
                                    command=self._on_what_changed)
        self.z_rb.pack(side=tk.LEFT, padx=(12, 0))
        self.setup = SetupTables(what, on_change=self._on_setup_changed,
                                 mport_max_visible=2, conn_max_visible=2,
                                 wraplength=420)
        self.setup.pack(side=tk.TOP, fill=tk.X, padx=(12, 4), pady=(0, 4))
        # The copy button rides in the measurement-port table's own '+ Add'
        # row (`table_head`): no height of its own -- and the Template row it
        # was first put in ran 18 px past the 460 px column.
        self.copy_mb = ttk.Menubutton(table_head(self.setup.mp_table),
                                      text=COPY_SETUP_LABEL)
        self.copy_menu = tk.Menu(self.copy_mb, tearoff=0,
                                 postcommand=self._fill_copy_menu)
        self.copy_mb["menu"] = self.copy_menu
        self.copy_mb.pack(side=tk.LEFT, padx=(8, 0))
        reg = getattr(self.app, "_register_scrollable", None)
        if reg is not None:
            try:
                self.setup.register_wheel(reg)
            except Exception:                               # noqa: BLE001
                pass

    # =============================================================== right

    def _build_right(self, parent: ttk.Frame) -> None:
        # Details bar FIRST and at the BOTTOM: pack unmaps from the end, so
        # the plot gives up height before the bar does.
        bar = ttk.Frame(parent)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 2))
        self.details_btn = ttk.Button(bar, text="▸ Details", width=10,
                                      command=self.toggle_details)
        self.details_btn.pack(side=tk.LEFT)
        self.xlog_var = tk.BooleanVar(master=parent, value=self.x_log)
        ttk.Checkbutton(bar, text="Log frequency axis", variable=self.xlog_var,
                        command=self._on_xlog).pack(side=tk.LEFT, padx=(10, 0))
        self.hint_lbl = ttk.Label(bar, foreground=PLACEHOLDER_FG,
                                  text="Click or drag on the plot to move the "
                                       "marker.")
        self.hint_lbl.pack(side=tk.LEFT, padx=(10, 0))

        self._details_frame = ttk.Frame(parent)
        self.details_text = tk.Text(self._details_frame, wrap=tk.NONE,
                                    font=CMP_FONT, height=14)
        ysb = ttk.Scrollbar(self._details_frame, orient=tk.VERTICAL,
                            command=self.details_text.yview)
        xsb = ttk.Scrollbar(self._details_frame, orient=tk.HORIZONTAL,
                            command=self.details_text.xview)
        self.details_text.configure(yscrollcommand=ysb.set,
                                    xscrollcommand=xsb.set)
        self.details_text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self._details_frame.rowconfigure(0, weight=1)
        self._details_frame.columnconfigure(0, weight=1)
        self._tag_text(self.details_text)
        self.details_text.configure(state=tk.DISABLED)

        # The verdict strip: one line per compared file.  Wrapped, because a
        # sentence is the point of it; as tall as its lines, up to six.
        vframe = ttk.Frame(parent)
        vframe.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 2))
        self.verdict_text = tk.Text(vframe, wrap=tk.WORD, font=CMP_FONT,
                                    height=2, width=20, cursor="arrow", takefocus=0,
                                    background="#f7f7f7", relief=tk.FLAT)
        vsb = ttk.Scrollbar(vframe, orient=tk.VERTICAL,
                            command=self.verdict_text.yview)
        self.verdict_text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.verdict_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.verdict_text.bind("<Configure>", lambda _e: self._fit_strip(),
                               add="+")
        self._tag_text(self.verdict_text)
        self.verdict_text.configure(state=tk.DISABLED)

        pframe = ttk.Frame(parent)
        pframe.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)
        # A small REQUEST: the frame's expand hands the canvas every pixel
        # there is, and a large figsize would ask the window for more.
        self.figure = Figure(figsize=(4.0, 3.0), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.figure, master=pframe)
        widget = self.canvas.get_tk_widget()
        widget.pack(fill=tk.BOTH, expand=True)
        # CLAUDE.md invariant: every FigureCanvasTkAgg hands its widget focus.
        widget.focus_set()
        widget.bind("<Configure>", lambda _e: self._schedule_relayout(),
                    add="+")
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)

        self._render_strip()
        self._draw()
        # A queued re-judge or relayout must not outlive the widgets: Tk
        # would print "invalid command name" for it.
        parent.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.right:
            return
        for attr in ("_rejudge_job", "_relayout_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.right.after_cancel(job)
                except Exception:                           # noqa: BLE001
                    pass
                setattr(self, attr, None)

    @staticmethod
    def _tag_text(txt: tk.Text) -> None:
        txt.tag_configure("bad", foreground=_LIMIT_FG)
        txt.tag_configure("good", foreground=_GOOD_FG)
        txt.tag_configure("stale", foreground=PLACEHOLDER_FG)
        txt.tag_configure("head", font=CMP_FONT + ("bold",))
        txt.tag_configure("pair", font=CMP_FONT + ("bold",),
                          background="#eef2f7")

    # =========================================================== the files

    def _files(self) -> list:
        return list(getattr(self.app, "files", []) or [])

    def _loaded(self, fe) -> bool:
        return any(fe is f for f in self._files())

    def _fe_by_label(self, label: str):
        for fe in self._files():
            if getattr(fe, "label", None) == label:
                return fe
        return None

    def refresh_files(self) -> None:
        """
        The loaded files changed (add, remove, relabel, clear, session load)
        -- `App._refresh_file_combobox` calls this on every one of them.
        Re-read the list: the reference box, one check per other file, the
        setup's port count.  A compared file that is gone loses its line and
        its curves NOW, and what is left is marked out of date.  Never
        raises.
        """
        try:
            files = self._files()
            labels = [str(getattr(fe, "label", "")) for fe in files]
            self.ref_cbo.configure(values=labels)
            if self._ref_fe is None or not self._loaded(self._ref_fe) \
                    or not self._ref_chosen:
                self._ref_fe = default_reference(files)
                if self._ref_fe is None:
                    self._ref_chosen = False
            self.ref_var.set(self._ref_fe.label if self._ref_fe is not None
                             else "")
            self._rebuild_checks()
            self._refresh_nports()
            self._drop_gone_pairs()
            if not self._building:
                self._render_all()
        except Exception:                                   # noqa: BLE001
            pass

    def on_enter(self) -> None:
        """The strip showed this workspace: re-read the files."""
        self.refresh_files()

    def _refresh_nports(self) -> None:
        fe = self._ref_fe
        if fe is None:
            self.setup.set_nports(None)
        else:
            self.setup.set_nports(fe.ts.nports, port_choices(fe.ts))

    def _rebuild_checks(self) -> None:
        """One Checkbutton per loaded file other than the reference, keyed by
        the FileEntry itself -- a relabel keeps its tick.  A file not seen
        before starts ticked."""
        for fe, var, _cb in self._checks:
            self._check_memory[id(fe)] = (fe, bool(var.get()))
        if self._checks_row is not None:
            self._checks_row.destroy()
            self._checks_row = None
        self._no_checks_lbl.pack_forget()
        self._checks = []
        others = [fe for fe in self._files() if fe is not self._ref_fe]
        if not others:
            self._no_checks_lbl.pack(side=tk.LEFT, anchor="w")
            return
        row = ReflowRow(self._checks_host)
        row.pack(side=tk.TOP, fill=tk.X)
        for fe in others:
            mem = self._check_memory.get(id(fe))
            on = mem[1] if mem is not None and mem[0] is fe else True
            var = tk.BooleanVar(master=self._checks_host, value=on)
            cb = ttk.Checkbutton(row, text=str(fe.label), variable=var,
                                 command=self._on_inputs_changed)
            row.add(cb, padx=2)
            self._checks.append((fe, var, cb))
        self._checks_row = row
        # Forget what belongs to files that are gone.
        live = {id(fe) for fe in self._files()}
        self._check_memory = {k: v for k, v in self._check_memory.items()
                              if k in live}

    def checked_files(self) -> list:
        return [fe for fe, var, _cb in self._checks if var.get()]

    def set_checked(self, labels) -> None:
        """Tick exactly the files named (a session, a test)."""
        want = set(labels)
        for fe, var, _cb in self._checks:
            var.set(fe.label in want)
        self._on_inputs_changed()

    def check_labels(self) -> list:
        return [fe.label for fe, _v, _cb in self._checks]

    def _on_ref_picked(self, _event=None) -> None:
        self.set_reference(self.ref_var.get())

    def set_reference(self, label: str) -> None:
        fe = self._fe_by_label(label)
        if fe is None:
            return
        self._ref_fe = fe
        self._ref_chosen = True
        self.ref_var.set(fe.label)
        self._rebuild_checks()
        self._refresh_nports()
        self._render_all()

    @property
    def reference(self):
        return self._ref_fe

    def _drop_gone_pairs(self) -> None:
        if self._computed_ref is not None and \
                not self._loaded(self._computed_ref):
            self._pairs = []
            self._computed_ref = None
            return
        self._pairs = [(fe, res) for fe, res in self._pairs
                       if self._loaded(fe)]

    # ===================================================== inputs and stale

    def _what(self) -> str:
        w = self.what_var.get()
        return w if w in (WHAT_S, WHAT_Z) else DEFAULT_WHAT

    def _active_setup(self) -> tuple:
        """The setup that is compared: the tables, or nothing for raw S."""
        if self._what() == WHAT_S:
            return [], []
        return self.setup.get()

    def _inputs(self) -> tuple:
        mports, conn = self._active_setup()
        return (self._ref_fe, getattr(self._ref_fe, "label", None),
                tuple((fe, fe.label) for fe in self.checked_files()),
                self._what(), _setup_key(mports, conn))

    @staticmethod
    def _same_inputs(a, b) -> bool:
        if a is None or b is None:
            return False
        if a[0] is not b[0] or a[1] != b[1] or a[3:] != b[3:]:
            return False
        if len(a[2]) != len(b[2]):
            return False
        return all(x[0] is y[0] and x[1] == y[1] for x, y in zip(a[2], b[2]))

    def is_stale(self) -> bool:
        """True when there are results and they were not computed from what
        the left side says now."""
        if not self._pairs and self._computed_inputs is None:
            return False
        return not self._same_inputs(self._computed_inputs, self._inputs())

    def _on_inputs_changed(self) -> None:
        if not self._building:
            self._render_strip()
            self._render_details()

    def _on_setup_changed(self) -> None:
        self._on_inputs_changed()

    def _on_what_changed(self) -> None:
        on = self._what() == WHAT_Z
        self.setup.set_editable(on)
        self.copy_mb.configure(state=tk.NORMAL if on else tk.DISABLED)
        self._on_inputs_changed()

    # ===================================================== copy from trace

    def _fill_copy_menu(self) -> None:
        self.copy_menu.delete(0, tk.END)
        choices = trace_setup_choices(getattr(self.app, "traces", []) or [])
        if not choices:
            self.copy_menu.add_command(
                label="(no single-file trace to copy from)", state=tk.DISABLED)
            return
        for text, tc in choices:
            # The setup itself beside the name: a trace's label can describe
            # a setup it no longer has (its default '..._p1_to_gnd' survives
            # a template), and the copy is what the user is choosing.
            try:
                words = setup_words(tc.mports, tc.conn_rows)
            except Exception:                               # noqa: BLE001
                words = ""
            if len(words) > 60:
                words = words[:59] + "…"
            self.copy_menu.add_command(
                label=f"{text}  —  {words}" if words else text,
                command=lambda t=tc: self.copy_setup_from_trace(t))

    def copy_setup_from_trace(self, tc) -> None:
        """A ONE-SHOT copy of the trace's setup into the tables.  Nothing
        links them afterwards: editing either leaves the other alone."""
        mports, conn, note = copy_trace_setup(tc)
        self.setup.set(mports, conn)
        self.what_var.set(WHAT_Z)
        self._on_what_changed()
        msg = (f"Copied the setup of trace [{tc.id}] \"{tc.label}\". It is "
               f"a copy: editing it here does not change the trace.")
        if note:
            msg += " " + note
        self._status(msg)

    def _status(self, text: str) -> None:
        self.status_lbl.configure(text=text)
        if text:
            if not self.status_lbl.winfo_manager():
                self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X, padx=4,
                                     pady=(0, 4), before=self._arow)
        else:
            self.status_lbl.pack_forget()

    # ============================================================= compare

    def compare(self) -> None:
        """Solve every (reference, checked file) pair -- the one place
        anything is solved."""
        ref = self._ref_fe
        others = [fe for fe in self.checked_files() if fe is not ref]
        if ref is None or not others:
            self._status(
                "Pick a reference and tick at least one file to compare "
                     "against it." if len(self._files()) >= 2 else
                     "Load at least two files (Add File...) to compare one "
                     "against another.")
            return
        mports, conn = self._active_setup()
        refused = (self._what() == WHAT_Z and not setup_is_empty(mports, conn)
                   and self.setup.has_errors())
        marker = self.marker_hz()
        if refused:
            # NOT handed to the solver: the cells say refused, and a number
            # computed from it anyway would contradict them.
            results = compare_against(ref, others, (), (), marker)
            errs = [i for i in self.setup.issues() if i.is_error]
            why = ("The setup is refused -- "
                   + issue_text(errs[:1]).replace("\n", " ")
                   + (f" (and {len(errs) - 1} more; see the red cells)"
                      if len(errs) > 1 else " (the red cell)") + ".")
            for res in results:
                res.has_setup = True
                res.setup = setup_words(mports, conn)
                res.z_why = why
        else:
            results = compare_against(ref, others, mports, conn, marker)
        self._pairs = list(zip(others, results))
        self._computed_ref = ref
        self._computed_inputs = self._inputs()
        # The strip says what came of it; the status line has nothing to add.
        self._status("")
        self._render_all()

    @property
    def results(self) -> list:
        return [res for _fe, res in self._pairs]

    def limits(self) -> Limits:
        return limits_from_text(self.s_lim_var.get(), self.l_lim_var.get(),
                                self.q_lim_var.get())

    # ======================================================== limits/marker

    def _schedule_rejudge(self) -> None:
        if self._building:
            return
        if self._rejudge_job is not None:
            try:
                self.right.after_cancel(self._rejudge_job)
            except Exception:                               # noqa: BLE001
                pass
        try:
            self._rejudge_job = self.right.after(150, self.rejudge)
        except Exception:                                   # noqa: BLE001
            self._rejudge_job = None

    def rejudge(self) -> None:
        """The limits changed: judge the CACHED results again.  Nothing is
        solved."""
        job, self._rejudge_job = self._rejudge_job, None
        if job is not None:                 # called directly: drop the queued one
            try:
                self.right.after_cancel(job)
            except Exception:                               # noqa: BLE001
                pass
        self._render_all()

    def marker_hz(self) -> float:
        txt = self.marker_var.get().strip()
        if not txt:
            return float("nan")
        v = _float(txt, float("nan"))
        return v * 1e9 if math.isfinite(v) and v > 0 else float("nan")

    def _on_marker_text(self) -> None:
        if self._building:
            return
        f = self.marker_hz()
        for res in self.results:
            res.marker_hz = f
        self._move_marker_lines(f)
        self._render_details()

    def set_marker_hz(self, f_hz: float) -> None:
        self.marker_var.set(f"{f_hz / 1e9:.6g}" if math.isfinite(f_hz)
                            and f_hz > 0 else "")

    def _snap_grid(self):
        fe = self._ref_fe
        return fe.ts.freqs if fe is not None else ()

    # --------------------------------------------------- the drag on the plot

    def _on_press(self, event) -> None:
        if getattr(event, "button", None) != 1 or event.inaxes is None \
                or event.xdata is None:
            return
        self._dragging = True
        self._move_marker_lines(float(event.xdata) * 1e9)
        self.canvas.draw_idle()

    def _on_motion(self, event) -> None:
        if not self._dragging or event.inaxes is None or event.xdata is None:
            return
        f = float(event.xdata) * 1e9
        if f <= 0:
            return
        self._move_marker_lines(f)
        self.canvas.draw_idle()

    def _on_release(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        f = None
        if event is not None and event.xdata is not None:
            f = float(event.xdata) * 1e9
        elif self.marker_lines:
            f = float(self.marker_lines[0].get_xdata()[0]) * 1e9
        if f is None or not f > 0:
            return
        self.set_marker_hz(snap_marker_hz(f, self._snap_grid(), self.x_log))

    def _move_marker_lines(self, f_hz: float) -> None:
        for ln in self.marker_lines:
            try:
                if math.isfinite(f_hz):
                    ln.set_xdata([f_hz / 1e9, f_hz / 1e9])
                    ln.set_visible(True)
                else:
                    ln.set_visible(False)
            except Exception:                               # noqa: BLE001
                pass
        if not self.marker_lines and math.isfinite(f_hz) and self.results:
            self._draw()
            return
        self.canvas.draw_idle()

    def _on_xlog(self) -> None:
        self.x_log = bool(self.xlog_var.get())
        self._draw()

    # ============================================================ rendering

    def _render_all(self) -> None:
        self._render_strip()
        self._draw()
        self._render_details()

    @staticmethod
    def _write(txt: tk.Text, lines, tagger) -> None:
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        for k, ln in enumerate(lines):
            txt.insert(tk.END, ln + ("\n" if k < len(lines) - 1 else ""),
                       tagger(ln))
        txt.configure(state=tk.DISABLED)

    def strip_lines(self) -> list:
        if not self._pairs and len(self._files()) < 2:
            return ["Load at least two files (Add File...) to compare one "
                    "against another."]
        return verdict_strip_lines(self.results, self.limits(),
                                   stale=self.is_stale())

    def _render_strip(self) -> None:
        lines = self.strip_lines()

        def tagger(ln):
            if ln.startswith("Out of date"):
                return ("stale",)
            if ln.startswith(MARK_SAME + " "):
                return ("good",)
            return line_tags(ln)
        self._write(self.verdict_text, lines, tagger)
        self._fit_strip()

    def _fit_strip(self) -> None:
        """As tall as its WRAPPED lines (2 to `STRIP_MAX_LINES`): a sentence
        cut off by the pane edge is a verdict the reader does not get.  Reads
        the display-line count at the widget's imposed width and writes only
        its height, so the <Configure> it causes settles at once."""
        txt = self.verdict_text
        try:
            n = int(txt.count("1.0", "end", "displaylines")[0])
        except Exception:                                   # noqa: BLE001
            n = int(txt.index("end-1c").split(".")[0])
        n = max(2, min(STRIP_MAX_LINES, n))
        if int(txt.cget("height")) != n:
            txt.configure(height=n)

    def details_lines(self) -> list:
        if not self._pairs:
            return ["Nothing compared yet -- press Compare."]
        lim = self.limits()
        out: list = []
        if self.is_stale():
            out += ["Out of date: the files, the reference or the setup "
                    "changed since this was computed -- press Compare.", ""]
        for k, (_fe, res) in enumerate(self._pairs):
            if k:
                out.append("")
            out.append(f"{res.label_b}  against the reference  "
                       f"{res.label_a}")
            out += compare_summary_lines(res, lim.s_pct, lim.l_pct,
                                         lim.q_pct)
        return out

    def _render_details(self) -> None:
        if not self._details_open:
            return
        heads = {f"{res.label_b}  against the reference  {res.label_a}"
                 for res in self.results}

        def tagger(ln):
            if ln in heads:
                return ("pair",)
            if ln.startswith("Out of date"):
                return ("stale",)
            return line_tags(ln)
        self._write(self.details_text, self.details_lines(), tagger)

    def toggle_details(self) -> None:
        self._details_open = not self._details_open
        if self._details_open:
            self.details_btn.configure(text="▾ Details")
            self._details_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, padx=4,
                                     pady=(0, 2))
            self._render_details()
        else:
            self.details_btn.configure(text="▸ Details")
            self._details_frame.pack_forget()

    @property
    def details_open(self) -> bool:
        return self._details_open

    # ---------------------------------------------------------------- plot

    def _panels(self) -> list:
        res = self.results
        panels = []
        if any(r.s is not None for r in res):
            panels.append("S")
        if any(r.ports for r in res):
            panels += ["L", "Q", "R"]
        return panels

    def _draw(self) -> None:
        """Every panel from the CACHED results -- nothing solved here."""
        fig = self.figure
        # Clearing axes that share a LOG x axis resets each to (0, 1) on its
        # way out, and matplotlib warns about the 0 once per panel -- about
        # axes that are being thrown away.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fig.clear()
        self.curves = {}
        self.marker_lines = []
        panels = self._panels()
        if not panels:
            msg = ("Nothing compared yet -- pick the files on the left and "
                   "press Compare." if not self._pairs else
                   "Nothing could be compared -- the strip above says why.")
            fig.text(0.5, 0.5, msg, ha="center", va="center", fontsize=9,
                     color="#777777", wrap=True)
            self.canvas.draw_idle()
            return
        lim = self.limits()
        marker = self.marker_hz()
        axes = []
        for i, kind in enumerate(panels):
            ax = fig.add_subplot(len(panels), 1, i + 1,
                                 sharex=axes[0] if axes else None)
            axes.append(ax)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            ax.set_ylabel(_PANEL_LABEL[kind], fontsize=8)
            self.curves[kind] = []
            if kind == "S":
                self._draw_s(ax, lim)
            else:
                self._draw_z(ax, kind, lim)
            ln = ax.axvline(marker / 1e9 if math.isfinite(marker) else 1.0,
                            color=_MARKER_FG, linewidth=1.0, gid="marker")
            ln.set_visible(math.isfinite(marker))
            self.marker_lines.append(ln)
        if self.x_log:
            axes[0].set_xscale("log")
        for ax in axes[:-1]:
            ax.tick_params(labelbottom=False)
        axes[-1].set_xlabel("Frequency (GHz)", fontsize=8)
        n_curves = len(self.curves.get(panels[0], []))
        if n_curves > 1 or any(len(v) > 1 for v in self.curves.values()):
            src = max(self.curves.values(), key=len)
            axes[0].legend(handles=src, fontsize=7, loc="best")
        self._relayout()

    def _pair_color(self, k: int) -> str:
        return _CURVE_FG[k % len(_CURVE_FG)]

    def _draw_s(self, ax, lim: Limits) -> None:
        for k, (_fe, res) in enumerate(self._pairs):
            if res.s is None:
                continue
            f = res.s.axis.freqs / 1e9
            # Floored at 0.0001 %: below that is "identical" to any reader.
            y = np.maximum(sim.db_to_pct(res.s.err_db), 1e-4)
            ln, = ax.plot(f, y, color=self._pair_color(k), linewidth=1.2,
                          label=_trunc(res.label_b), gid="curve")
            self.curves["S"].append(ln)
        ax.set_yscale("log")
        # '1 %', '0.1 %' -- the text speaks in percent, and so does the box.
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _p: f"{v:g} %"))
        ax.axhline(abs(lim.s_pct), color=_LIMIT_FG, linestyle="--",
                   linewidth=1.0, gid="limit")

    def _draw_z(self, ax, kind: str, lim: Limits) -> None:
        limit = {"L": lim.l_pct, "Q": lim.q_pct, "R": None}[kind]
        tx = ax.get_xaxis_transform()
        ys = []
        said_srf = said_lossless = False
        for k, (_fe, res) in enumerate(self._pairs):
            multi = len(res.ports) > 1
            for p, pc in enumerate(res.ports):
                z = pc.z
                f = z.axis.freqs / 1e9
                y = {"L": z.dl_pct, "Q": z.dq_pct, "R": z.dr_pct}[kind]
                # Cut AFTER the port suffix, so the whole legend entry
                # keeps CLAUDE.md's 30-character rule.
                label = _trunc(res.label_b + (f" [{pc.name}]" if multi
                                              else ""))
                ln, = ax.plot(f, y, color=self._pair_color(k),
                              linestyle=_PORT_LS[p % len(_PORT_LS)],
                              linewidth=1.2, label=label, gid="curve")
                self.curves[kind].append(ln)
                ys.append(np.abs(np.asarray(y, dtype=float)))
                # What the verdict does NOT read is shaded, and said so:
                # past 85 % of the self-resonance (L and Q) ...
                top = z.usable_limit()
                if kind in ("L", "Q") and math.isfinite(top):
                    ax.axvspan(top / 1e9, z.axis.hi / 1e9, color=_GREY,
                               alpha=0.18, linewidth=0, gid="not_judged")
                    if not said_srf:
                        # Right-aligned ON the span's left edge, pointing
                        # into it: the span always ends at the top of the
                        # band, so a left-aligned label ran off the axes.
                        ax.text(top / 1e9, 0.02, "not judged (resonance) ▸",
                                transform=tx, fontsize=6, va="bottom",
                                ha="right", alpha=0.8)
                        said_srf = True
                # ... and Q / R where the reference is almost lossless.
                if kind in ("Q", "R"):
                    for lo, hi in not_judged_spans(z.axis.freqs,
                                                   z.qr_not_judged):
                        ax.axvspan(lo / 1e9, hi / 1e9, color=_GREY,
                                   alpha=0.18, linewidth=0, gid="not_judged")
                        if not said_lossless:
                            ax.text(lo / 1e9, 0.92, " not judged (A lossless)",
                                    transform=tx, fontsize=6, va="top",
                                    ha="left", alpha=0.8)
                            said_lossless = True
        if limit is not None:
            for sgn in (1, -1):
                ax.axhline(sgn * abs(limit), color=_LIMIT_FG, linestyle="--",
                           linewidth=1.0, gid="limit")
        if ys:
            span, n_off = pct_view_span(np.concatenate(ys),
                                        limit if limit is not None else 0.0)
            if span is not None:
                ax.set_ylim(-span, span)
                if n_off:
                    # Top right: the resonance label sits bottom right of
                    # the same panels (it always ends at the top of the
                    # band), and the two overlapped there.
                    ax.text(0.99, 0.97, f"{n_off} pt{'s' if n_off > 1 else ''}"
                            f" off scale", transform=ax.transAxes,
                            ha="right", va="top", fontsize=7, alpha=0.7)

    def _schedule_relayout(self) -> None:
        if self._relayout_job is not None:
            return
        try:
            self._relayout_job = self.right.after_idle(self._relayout)
        except Exception:                                   # noqa: BLE001
            self._relayout_job = None

    def _relayout(self) -> None:
        """tight_layout at the canvas's CURRENT size (a resize does not
        re-run it), quietly: a canvas too small for four panels makes
        matplotlib warn, and the warning is no use to anybody."""
        self._relayout_job = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.figure.tight_layout(pad=0.4, h_pad=0.2)
        except Exception:                                   # noqa: BLE001
            pass
        self.canvas.draw_idle()

    # ============================================================= session

    def state_get(self) -> dict:
        """
        The block under "compare" in the session's "workspaces": the
        reference, the ticked files, the choice, the setup, the limits and
        the marker, at inner version `COMPARE_STATE_VERSION`.  Results are
        NOT saved.  {} at the defaults, so a session that never used the
        workspace carries no block.
        """
        mports, conn = self.setup.get()
        unticked = any(not var.get() for _fe, var, _cb in self._checks)
        state = {
            "version": COMPARE_STATE_VERSION,
            "reference": self._ref_fe.label if self._ref_fe is not None
            else "",
            "checked": [fe.label for fe in self.checked_files()],
            "what": self._what(),
            "mports": rows_to_json(mports),
            "conn_rows": rows_to_json(conn),
            "limits": {"s": self.s_lim_var.get().strip(),
                       "l": self.l_lim_var.get().strip(),
                       "q": self.q_lim_var.get().strip()},
            "marker_ghz": self.marker_var.get().strip(),
        }
        at_default = (
            not mports and not conn and not unticked and not self._ref_chosen
            and state["what"] == DEFAULT_WHAT and not state["marker_ghz"]
            and state["limits"] == {"s": f"{S_LIMIT_PCT_DEFAULT:g}",
                                    "l": f"{sim.DEFAULT_L_LIMIT_PCT:g}",
                                    "q": f"{sim.DEFAULT_Q_LIMIT_PCT:g}"})
        return {} if at_default else state

    def state_set(self, data: dict) -> None:
        """
        Restore `state_get`'s block.  Unknown keys are ignored; a garbled row
        list costs that list.  A reference or ticked file that is not loaded
        is skipped.  A block newer than this build raises, and the switch
        drops it with a note in the Log.  Results start empty.
        """
        raw = data.get("version", COMPARE_STATE_VERSION)
        try:
            version = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"compare block version {raw!r} is not a "
                             "number") from None
        if version > COMPARE_STATE_VERSION:
            raise ValueError(f"compare block version {version} is newer than "
                             f"this build reads ({COMPARE_STATE_VERSION})")
        mports = mports_from_json(data.get("mports"))
        conn = conn_rows_from_json(data.get("conn_rows"))
        self._building = True
        try:
            ref = self._fe_by_label(str(data.get("reference", "") or ""))
            if ref is not None:
                self._ref_fe = ref
                # Chosen only if it is NOT what the default rule picks:
                # restoring the default as "user-chosen" stopped the
                # lowest-top-frequency rule from applying to files added
                # after a session load (stage-3 review).
                files = list(getattr(self.app, "files", []) or [])
                self._ref_chosen = ref is not default_reference(files)
                self.ref_var.set(ref.label)
            self._rebuild_checks()
            if isinstance(data.get("checked"), list):
                want = {str(x) for x in data["checked"]}
                for fe, var, _cb in self._checks:
                    var.set(fe.label in want)
            what = data.get("what", DEFAULT_WHAT)
            self.what_var.set(what if what in (WHAT_S, WHAT_Z)
                              else DEFAULT_WHAT)
            lim = data.get("limits") or {}
            if isinstance(lim, dict):
                for key, var, dflt in (
                        ("s", self.s_lim_var, S_LIMIT_PCT_DEFAULT),
                        ("l", self.l_lim_var, sim.DEFAULT_L_LIMIT_PCT),
                        ("q", self.q_lim_var, sim.DEFAULT_Q_LIMIT_PCT)):
                    var.set(str(lim.get(key, f"{dflt:g}") or f"{dflt:g}"))
            self.marker_var.set(str(data.get("marker_ghz", "") or ""))
            self._refresh_nports()
            self.setup.set(mports, conn)
            self._pairs = []
            self._computed_ref = None
            self._computed_inputs = None
        finally:
            self._building = False
        self._on_what_changed()
        self._render_all()
