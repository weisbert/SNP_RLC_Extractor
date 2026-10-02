"""
pkg_rlc/panels/ws_tracemodel.py -- the Trace model WORKSPACE (L5).

`docs/design_workspaces.md` § 2, stage 1.  The owner's complaint: "I wanted
the loading of one PN signal in one .sNp file, I opened the menu, clicked
Trace model, and got a warning I could not make head or tail of."  So the
trace model is a WORKSPACE now -- one of the strip's buttons -- and it asks
for exactly what the question needs: which file, which nets (named by the
user, ends picked by the user), what is done with the other ports, and the
marker frequency.  Nothing is inferred from the Traces list and no name is
invented.

    LEFT  (packed into `app.trace_ws_left`)         RIGHT (`app.trace_ws_right`)
    Nets: File [cbo]  the RowTable  + Add net        Summary  (mono tk.Text, sortable)
          [Duplicate row] [Remove]  <issues>          Schematic (tk.Canvas)
    Other ports: the connections table                Response  (tk.Canvas, overlays)
          "Ports not listed anywhere are OPEN."      > Details (collapsed)
    Conditions: Freq [0.1] GHz  Source [1] Ω  Load [0] fF
    [Calculate all]  [Export CSV]

OTHER PORTS ARE CONNECTION ROWS (stage 3, design § 8 item 2).  Stage 1 had
a GND field; it is now the shared `ConnectionsTable`
(`pkg_rlc.panels.setup_tables`) -- ground, vdd, open, short, R/L/C rows,
the RLC editor's columns and rules -- and every net is solved under those
rows (`pkg_rlc.services.tracenets`).  Ground-only rows compute exactly what
the GND field did.  A session saved with a `gnd` string reads it back as one
ground row.

VALIDATION IS IN THE TABLE, NOT IN A DIALOG.  Every edit runs
`tracenets.validate_nets` and paints the offending CELL (red for an error,
amber for a warning) with the reason listed under the table.  No messagebox
anywhere in this module: a dialog interrupts the typing it complains about,
and the owner's original warning was a dialog.

STALENESS BY SIGNATURE.  A `NetResult` carries the `net_signature` of what it
was solved from (the five cells, the connection rows, the file label and the
frequency).  The panel never edits a result: on every change it re-derives
the display list -- a row whose signature has a solved result shows it, a
row whose signature has none shows its last numbers marked `stale` (or
"not calculated yet"), and `Calculate all` solves ONLY the rows without a
current `ok` result.  Source / Load are deliberately not in the signature:
they change the bandwidth block only, and `rebandwidth` answers them with no
solve at all.

THE ENGINE IS NOT HERE.  `pkg_rlc.services.tracenets` (L2) validates and
solves; `pkg_rlc.present.tracemodel_report` (L3) owns every line of text and
every canvas coordinate.  This module hands items to `Canvas.create_*` with a
colour and a font -- the palette and the aspect scaling are ported from the
old `tracemodel_gui.TraceModelWindow`, which stage 1 deletes.  Two defects of
that window are fixed here rather than there: the response curves are
clipped to the window's dB range before they are handed to the geometry (a
peak above +3 dB used to run off the top of the canvas), and the load
labels are a legend column beside the plot rather than text over the
curves.

No `pkg_rlc.frontend.app` import, at module level or inside a function: the
App is passed in and duck-typed.  What it uses, in full:

    app.files                       app._file_by_label(label)
    app.files_lb / app._sel_idx     app._append_result(text, LOG_WARN)
    app._register_scrollable        (all but `app.files` are optional)
"""

from __future__ import annotations

import csv
import itertools
import math
import weakref
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, ttk
from typing import Optional

import numpy as np

from pkg_rlc.physics import tracemodel as tmod
from pkg_rlc.panels.setup_tables import (
    ConnectionsTable, conn_rows_from_json, port_choices,
    port_text_from_choice, rows_to_json, table_head,
)
from pkg_rlc.present.conntable import ColumnSpec
from pkg_rlc.present.report import LOG_WARN
from pkg_rlc.present.tracemodel_report import (
    CANVAS_H, CANVAS_W, RESPONSE_DB_CEIL, RESPONSE_DB_FLOOR, SUMMARY_COLUMNS,
    bandwidth_lines, pi_canvas_items, pi_report_lines, response_canvas_items,
    summary_order, summary_sort_value, summary_table_lines,
)
from pkg_rlc.services.tracenets import (
    DEFAULT_LOAD_EXTRA_F, DEFAULT_SRC_OHM, NET_COLUMNS, NetResult, NetRow,
    ground_rows, net_signature, rebandwidth, retarget, solve_net,
    validate_nets,
)
from pkg_rlc.widgets.widgets import PLACEHOLDER_FG, WARN_FG, RowTable

__all__ = [
    "TraceModelWorkspace", "NET_TABLE_COLUMNS",
    "DEFAULT_FREQ_GHZ", "ERROR_FG", "WARN_CELL_FG",
    "port_choices", "port_text_from_choice", "csv_rows",
    "issue_lines", "TRACE_STATE_VERSION",
]

#: A serial per loaded FileEntry object, so a result's signature names the
#: OBJECT it was solved on and not only its label (see `_file_key`).  Weak:
#: a removed file's entry goes with it.
_FILE_SERIALS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_FILE_SERIAL_COUNTER = itertools.count(1)

#: The marker frequency the workspace starts at, in GHz -- its own field,
#: not the RLC workspace's marker (spec § 2.1).
DEFAULT_FREQ_GHZ = "0.1"

#: Cell colours.  Red is an error (the row is skipped), amber a warning (the
#: row is solved; something about it is worth knowing).  Amber is the one
#: warning colour the application has.
ERROR_FG = "#b00020"
WARN_CELL_FG = WARN_FG

#: The column labels as the tables show them, keyed as `CellIssue.column`.
_COLUMN_TITLE = {"name": "Name", "in_p": "IN+", "in_n": "IN-",
                 "out_p": "OUT+", "out_n": "OUT-"}
_CONN_COLUMN_TITLE = {"ports": "Port", "to": "To", "R": "R", "L": "L",
                      "C": "C", "kind": "Type", "net": "Net"}

#: The inner version of the session's `workspaces.trace` block.  1 (or no
#: number) is stage 1's, with a `gnd` string; 2 carries `conn_rows`.  A
#: block newer than this build is refused whole, which the switch turns
#: into one Log note and the defaults.
TRACE_STATE_VERSION = 2

#: The Nets table.  Port cells are comboboxes that ACCEPT TYPED TEXT: the
#: dropdown offers the file's ports as "N  name" and a user may still type
#: "1,3" or "6-14", which `port_text_from_choice` leaves alone.
NET_TABLE_COLUMNS = (
    ColumnSpec("name", "Name", 10),
    ColumnSpec("in_p", "IN+", 7, kind="combo"),
    ColumnSpec("in_n", "IN-", 7, kind="combo"),
    ColumnSpec("out_p", "OUT+", 7, kind="combo"),
    ColumnSpec("out_n", "OUT-", 7, kind="combo"),
)

TM_FONT = ("Consolas", 9)

#: Canvas styling, keyed by the `role` the geometry declares -- the old
#: window's palette, verbatim.  The coordinates live at L3; this is the half
#: that has to know what a colour is.
_WIRE_FG = "#2c2c2c"
_BOX_FILL = "#eef2f7"
_BOX_EDGE = "#5a6b80"
_VALUE_FG = "#12304f"
_GND_FG = "#2c2c2c"
_AXIS_FG = "#7a8694"
_GRID_FG = "#c05a5a"          # the -3 dB rule: the one line the eye wants
_MARKER_FG = "#3b7dd8"        # the working frequency
#: One colour per load capacitance in the sensitivity table (four, because
#: `DEFAULT_LOADS_F` is four; the geometry cycles `curve0..curve3`).
_CURVE_FG = ("#1b6ca8", "#2e8b57", "#b8860b", "#8b3a62")
#: Overlaid nets: dashed, in muted colours, so the selected net's four
#: curves stay the picture and the overlays stay a comparison.
_OVERLAY_FG = ("#6b6b6b", "#9c6b9c", "#5b8a8a", "#8a7a3b")
_LEGEND_W = 110               # the legend column beside the response plot

_SEL_BG = "#d9e6f5"
#: The Summary's own selected-row tag (see where it is configured).
SUMMARY_SEL_TAG = "rowsel"           # the selected row in the Summary


# ============================================================================
# Pure helpers -- no Tk
# ============================================================================

def issue_lines(issues, rows) -> list[str]:
    """
    One line per complaint, errors before warnings, naming the row by its
    number AND its name so a user can find it without counting.  A
    connection row is "Connection row N, <cell>".
    """
    out = []
    for iss in sorted(issues, key=lambda i: (0 if i.is_error else 1)):
        if getattr(iss, "table", "nets") == "conn":
            where = (f"Connection row {iss.row + 1}, "
                     f"{_CONN_COLUMN_TITLE.get(iss.column, iss.column)}")
        else:
            name = ""
            if iss.row < len(rows):
                name = str(getattr(rows[iss.row], "name", "")).strip()
            tag = f" '{name}'" if name else ""
            where = f"Row {iss.row + 1}{tag}, {_COLUMN_TITLE.get(iss.column, iss.column)}"
        kind = "" if iss.is_error else "warning: "
        out.append(f"{where}: {kind}{iss.message}")
    return out


def _num(v) -> str:
    """Full precision for the CSV; blank for a missing number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if math.isnan(f):
        return ""
    return repr(f)


def csv_rows(results) -> list[list]:
    """
    The CSV export as rows: the Summary at full precision, then every
    net's three branches, then every net's bandwidth table.  Three blocks,
    each with its own header line, separated by a blank row.  Every value
    is `repr(float)` -- the pane rounds, the file does not.
    """
    rows: list[list] = [[
        "net", "status", "differential", "freq_Hz", "R_ser_ohm", "L_ser_H",
        "C_in_F", "C_out_F", "f_3dB_open_Hz", "lumped_worst_frac", "error",
    ]]
    for res in results:
        m = res.model
        if m is None:
            rows.append([res.name, res.status, int(bool(res.differential)),
                         "", "", "", "", "", "", "", res.error])
            continue
        rows.append([
            res.name, res.status, int(bool(m.differential)), _num(m.freq_hz),
            _num(m.series.R_ohm), _num(m.series.L_henry),
            _num(m.shunt_in.C_farad), _num(m.shunt_out.C_farad),
            _num(summary_sort_value(res, "f_3db")),
            _num(summary_sort_value(res, "lumped")), res.error,
        ])
    rows.append([])
    rows.append(["net", "branch", "R_ohm", "L_H", "C_F", "Q", "ReZ_ohm",
                 "ImZ_ohm", "reads_as"])
    for res in results:
        if res.model is None:
            continue
        for br in res.model.branches:
            rows.append([res.name, br.name, _num(br.R_ohm), _num(br.L_henry),
                         _num(br.C_farad), _num(br.Q), _num(br.Z.real),
                         _num(br.Z.imag), br.reads_as])
    rows.append([])
    rows.append(["net", "z_src_ohm", "c_load_F", "f_3dB_Hz", "crossed",
                 "passband_ok", "droop_top_dB", "peak_dB", "peak_Hz"])
    for res in results:
        for bw in (res.bw_table or ()):
            rows.append([res.name, _num(bw.z_src_ohm), _num(bw.c_load_farad),
                         _num(bw.f_3db_hz), int(bool(bw.crossed)),
                         int(bool(bw.passband_ok)), _num(bw.droop_top_db),
                         _num(bw.peak_db), _num(bw.peak_hz)])
    return rows


def _unsolved(row: NetRow, status: str, error: str, signature: tuple,
              z_src: float, c_load: float) -> NetResult:
    """A result with no model: an error row, or a row never calculated."""
    return NetResult(
        name=str(row.name).strip(), status=status, error=error,
        differential=row.differential, model=None, reference=None,
        mode_conversion=None, mc_note="", freq_snap=None, freqs=None,
        Z2=None, bw_table=(), corners=(), model_band=(float("nan"), ""),
        z_src_ohm=float(z_src), c_load_extra_f=float(c_load),
        signature=signature)


def _parse_terminations(src_text: str, load_text: str) -> tuple[float, float]:
    """(ohms, farads).  A bad entry falls back to its default: these are
    what-if knobs, and a blank picture mid-keystroke is worse than the
    default one."""
    try:
        src = float(src_text)
        if not math.isfinite(src) or src < 0.0:
            raise ValueError
    except (TypeError, ValueError):
        src = DEFAULT_SRC_OHM
    try:
        load = float(load_text) * 1e-15
        if not math.isfinite(load) or load < 0.0:
            raise ValueError
    except (TypeError, ValueError):
        load = DEFAULT_LOAD_EXTRA_F
    return (src, load)


# ============================================================================
# The workspace
# ============================================================================

class TraceModelWorkspace:
    """
    Builds the Trace model workspace into the two frames the App owns, and
    holds its state.  Not a widget itself: the left frame is a child of the
    App's left host and the right one a pane of the outer PanedWindow, and
    `WorkspaceSwitch` needs them to stay exactly that.
    """

    def __init__(self, app, left: ttk.Frame, right: ttk.Frame) -> None:
        self.app = app
        self.left = left
        self.right = right

        # -- state
        self._solved: dict[tuple, NetResult] = {}     # signature -> result
        self._last_by_name: dict[str, NetResult] = {}  # name -> last with a model
        self._results: list[NetResult] = []           # the display list
        self._issues: list = []
        self._painted: list = []                      # cells coloured last pass
        self._selected_name: Optional[str] = None
        self._sort_key: Optional[str] = None
        self._sort_desc = False
        self._overlay_vars: dict[str, tk.BooleanVar] = {}
        self._overlay_btns: list = []
        self._drawn: Optional[NetResult] = None
        self._details_open = False
        self._building = True

        self._build_left(left)
        self._build_right(right)
        self._building = False
        self.refresh_files()

    # ------------------------------------------------------------------ left

    def _build_left(self, parent: ttk.Frame) -> None:
        """
        The left column.  ITS HEIGHT IS A MEASURED BUDGET, because the
        stage-3 connections table took the place of a one-line GND field.

        Measured on this box (Microsoft YaHei UI 9, tk scaling 1.333) with
        `diff_pair_4port.s4p`, two nets, the left host 460 px wide; numbers
        are the column's requested height against the host's height, and
        where Calculate all's bottom edge lands in it:

            stage 1, GND field:        req 382; at 1040x600 the host is
                                       421 tall and Calculate ends at 355
                                       (66 px spare); 1500x900: 721 / 355.
            connections table, naive  req 449 with one ground row --
            (LabelFrame + OPEN line   Calculate at 419 of 421, 2 px spare;
            + its own button row):    three rows: req 507, Calculate at
                                      480 of 421, CLIPPED (unmapped).
            as built:                 see AFTER below.

        So three things, all here: (1) the table's caption and the OPEN line
        ride in the RowTable's own '+ Add' row instead of a LabelFrame and a
        line of their own (`ConnectionsTable(caption=...)`), and so do the
        nets table's Duplicate / Remove buttons -- rows that were mostly
        empty space; (2) the connections table shows two rows before it
        scrolls; (3) the action row (Calculate all / Export CSV) and the
        status line are packed FIRST, at the BOTTOM of an inner column that
        is only as tall as it asks to be -- so with room to spare they sit
        right under Conditions, and on a window too short for the column
        it is the column's TOP-packed content that gives, never the button.

        AFTER (same measurement; `tests/test_ws_tracemodel.py::
        TestLeftColumnBudget` re-measures the 1040x600 cases):
            one ground row:   req 370; Calculate ends at 343 of 421 at
                              1040x600 (78 px spare, stage 1 had 66) and
                              at 343 of 721 at 1500x900.
            two rows:         req 399; 372 of 421 (49 spare).
            three or five:    req 402 / 406 (the rows past two scroll);
                              375 / 379 of 421.
            worst case, six nets and every Kind at once: req 520 against
                              421.  Calculate still ends at 394 of 421 and
                              it is Conditions that is squeezed off; in
                              stage 1 six nets alone clipped Calculate.
            width, worst case: 443 px requested in the 460 px host.
        """
        # Only as tall as it asks: pack gives a non-expanding slave its
        # requested height when there is room and the cavity when there is
        # not, and the bottom-packed action row is allocated first.
        col = ttk.Frame(parent)
        col.pack(side=tk.TOP, fill=tk.X)
        self._col = col

        # ---- packed FIRST, at the bottom: never the thing that is clipped
        self.status_lbl = ttk.Label(col, text="", anchor="w",
                                    justify=tk.LEFT, wraplength=430,
                                    foreground=PLACEHOLDER_FG)
        self.status_lbl.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))
        arow = ttk.Frame(col)
        arow.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(4, 2))
        self.calc_btn = ttk.Button(arow, text="Calculate all",
                                   command=self.calculate_all)
        self.calc_btn.pack(side=tk.LEFT)
        self.export_btn = ttk.Button(arow, text="Export CSV",
                                     command=self.export_csv)
        self.export_btn.pack(side=tk.RIGHT)

        nets = ttk.LabelFrame(col, text="Nets")
        nets.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 2))

        frow = ttk.Frame(nets)
        frow.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 2))
        ttk.Label(frow, text="File").pack(side=tk.LEFT)
        self.file_var = tk.StringVar(master=parent, value="")
        self.file_cbo = ttk.Combobox(frow, textvariable=self.file_var,
                                     state="readonly", width=34)
        self.file_cbo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        self.file_cbo.bind("<<ComboboxSelected>>", self._on_file_changed)

        self.table = RowTable(nets, NET_TABLE_COLUMNS, NetRow,
                              on_change=self._on_table_change, min_rows=1,
                              max_visible=6, add_text="+ Add net",
                              from_cells=self._from_cells)
        self.table.pack(side=tk.TOP, fill=tk.X, padx=4)
        reg = getattr(self.app, "_register_scrollable", None)
        if reg is not None:
            self.table.register_wheel(reg)

        # Duplicate / Remove share the '+ Add net' row (budget item 1).
        head = table_head(self.table)
        self.dup_btn = ttk.Button(head, text="Duplicate row",
                                  command=self.duplicate_row)
        self.dup_btn.pack(side=tk.LEFT)
        self.remove_btn = ttk.Button(head, text="Remove",
                                     command=self.remove_row)
        self.remove_btn.pack(side=tk.LEFT, padx=(4, 0))

        self.issues_lbl = ttk.Label(nets, text="", anchor="w", justify=tk.LEFT,
                                    wraplength=430, foreground=ERROR_FG)
        self.issues_lbl.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))

        # The other ports: the RLC editor's connection rows, through the
        # shared component.  Two rows show before it scrolls (budget item 2).
        self.conn_table = ConnectionsTable(col, caption="Other ports:",
                                           on_change=self._on_fields_changed,
                                           min_rows=1, max_visible=2)
        self.conn_table.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 2))
        if reg is not None:
            self.conn_table.register_wheel(reg)

        cond = ttk.LabelFrame(col, text="Conditions")
        cond.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        crow = ttk.Frame(cond)
        crow.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        ttk.Label(crow, text="Freq").pack(side=tk.LEFT)
        self.freq_var = tk.StringVar(master=parent, value=DEFAULT_FREQ_GHZ)
        self.freq_entry = ttk.Entry(crow, textvariable=self.freq_var, width=7)
        self.freq_entry.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(crow, text="GHz").pack(side=tk.LEFT, padx=(0, 10))
        self.freq_var.trace_add("write", lambda *_a: self._on_fields_changed())
        ttk.Label(crow, text="Source").pack(side=tk.LEFT)
        self.src_var = tk.StringVar(master=parent, value=f"{DEFAULT_SRC_OHM:g}")
        self.src_entry = ttk.Entry(crow, textvariable=self.src_var, width=6)
        self.src_entry.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(crow, text="Ω").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(crow, text="Load").pack(side=tk.LEFT)
        self.load_var = tk.StringVar(master=parent,
                                     value=f"{DEFAULT_LOAD_EXTRA_F * 1e15:g}")
        self.load_entry = ttk.Entry(crow, textvariable=self.load_var, width=6)
        self.load_entry.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(crow, text="fF").pack(side=tk.LEFT)
        for e in (self.src_entry, self.load_entry):
            e.bind("<Return>", self._on_terminations_changed)
            e.bind("<FocusOut>", self._on_terminations_changed)

    # ----------------------------------------------------------------- right

    def _build_right(self, parent: ttk.Frame) -> None:
        # Details bar FIRST and at the BOTTOM: pack unmaps from the end, so
        # the three panes above give up height before the bar does.
        bar = ttk.Frame(parent)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 2))
        self.details_btn = ttk.Button(bar, text="▸ Details", width=10,
                                      command=self.toggle_details)
        self.details_btn.pack(side=tk.LEFT)

        self._details_frame = ttk.Frame(parent)
        self.details_text = tk.Text(self._details_frame, wrap=tk.NONE,
                                    font=TM_FONT, height=12)
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
        self.details_text.tag_configure("warn", foreground=WARN_FG)
        self.details_text.configure(state=tk.DISABLED)

        paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._paned = paned

        sframe = ttk.LabelFrame(paned, text="Summary")
        self.summary = tk.Text(sframe, wrap=tk.NONE, font=TM_FONT, height=6,
                               cursor="arrow", takefocus=0)
        sxsb = ttk.Scrollbar(sframe, orient=tk.HORIZONTAL,
                             command=self.summary.xview)
        sysb = ttk.Scrollbar(sframe, orient=tk.VERTICAL,
                             command=self.summary.yview)
        self.summary.configure(xscrollcommand=sxsb.set, yscrollcommand=sysb.set)
        self.summary.grid(row=0, column=0, sticky="nsew")
        sysb.grid(row=0, column=1, sticky="ns")
        sxsb.grid(row=1, column=0, sticky="ew")
        sframe.rowconfigure(0, weight=1)
        sframe.columnconfigure(0, weight=1)
        self.summary.tag_configure("hdr", underline=1)
        # NOT Tk's built-in "sel": that one paints only while the Text has
        # keyboard focus (this one never takes it), so the selected row showed
        # no band at all, and it claimed the X selection on every click.
        self.summary.tag_configure(SUMMARY_SEL_TAG, background=_SEL_BG)
        self.summary.tag_configure("stale", foreground=PLACEHOLDER_FG)
        self.summary.tag_configure("error", foreground=WARN_FG)
        self.summary.bind("<Button-1>", self._on_summary_click)
        self.summary.configure(state=tk.DISABLED)
        paned.add(sframe, weight=1)

        # Both canvases ask for LESS than Tk's default 265 px, so the
        # Summary keeps its six lines at the 1040x600 minsize (measured:
        # with the defaults the PanedWindow squeezed it to 56 px, three
        # lines); the weights hand the canvases every pixel above that.
        cframe = ttk.LabelFrame(paned, text="Schematic")
        self.canvas = tk.Canvas(cframe, background="white", height=150,
                                highlightthickness=1,
                                highlightbackground="#c8d0da")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_schematic())
        paned.add(cframe, weight=3)

        rframe = ttk.LabelFrame(paned, text="Response")
        self._overlay_row = ttk.Frame(rframe)
        self._overlay_row.pack(side=tk.TOP, fill=tk.X, padx=4)
        self._overlay_lbl = ttk.Label(self._overlay_row, text="Overlay:",
                                      foreground=PLACEHOLDER_FG)
        self._overlay_lbl.pack(side=tk.LEFT)
        self.resp = tk.Canvas(rframe, background="white", height=110,
                              highlightthickness=1,
                              highlightbackground="#c8d0da")
        self.resp.pack(fill=tk.BOTH, expand=True)
        self.resp.bind("<Configure>", lambda _e: self._draw_response())
        # The marker is dragged HERE, not only typed: a ghost line follows the
        # pointer and the release writes Freq, which re-reads every solved net
        # at the new point from its cached sweep (`retarget`, no Calculate).
        self.resp.bind("<ButtonPress-1>", self._on_resp_drag)
        self.resp.bind("<B1-Motion>", self._on_resp_drag)
        self.resp.bind("<ButtonRelease-1>", self._on_resp_release)
        paned.add(rframe, weight=2)

        self._render_summary()

    # ------------------------------------------------------------ the file

    def _file_key(self, fe=None) -> str:
        """
        What a result's signature says the FILE is: its label AND which
        loaded object it is.  The label alone is not enough -- measured, a
        different file loaded under the same name ('net.s4p' from another
        folder, after the first was removed) kept the old file's numbers as
        'ok', and Calculate all reported '0 solved, 1 unchanged'.  Each
        FileEntry object gets a serial the first time it is seen, held
        weakly, so a reload is a new key and every row reads stale.
        """
        fe = self._file_entry() if fe is None else fe
        if fe is None:
            return self.file_var.get()
        serial = _FILE_SERIALS.get(fe)
        if serial is None:
            serial = _FILE_SERIALS[fe] = next(_FILE_SERIAL_COUNTER)
        return f"{getattr(fe, 'label', '')}#{serial}"

    def _file_entry(self):
        label = self.file_var.get()
        if not label:
            return None
        by_label = getattr(self.app, "_file_by_label", None)
        if by_label is not None:
            try:
                return by_label(label)
            except Exception:                               # noqa: BLE001
                return None
        for fe in getattr(self.app, "files", []) or []:
            if getattr(fe, "label", None) == label:
                return fe
        return None

    def _files_list_selection(self, labels: list[str]) -> str:
        """The label selected in the shared Files list, if any."""
        lb = getattr(self.app, "files_lb", None)
        sel_idx = getattr(self.app, "_sel_idx", None)
        if lb is None or sel_idx is None:
            return ""
        try:
            idx = sel_idx(lb)
        except Exception:                                   # noqa: BLE001
            return ""
        if idx is not None and 0 <= idx < len(labels):
            return labels[idx]
        return ""

    def refresh_files(self) -> None:
        """
        The loaded files changed (add, remove, relabel, clear, session
        load).  Repopulate the File combobox; a chosen file that is gone
        falls back to the Files list's selection, then the first file, then
        nothing -- and every row's signature stops matching, so the rows
        read `stale` rather than keep numbers from a file that is not here.
        Never raises: it is called from the file handlers.
        """
        try:
            labels = [str(getattr(fe, "label", "")) for fe in
                      (getattr(self.app, "files", []) or [])]
            self.file_cbo.configure(values=labels)
            if self.file_var.get() not in labels:
                self.file_var.set(self._files_list_selection(labels)
                                  or (labels[0] if labels else ""))
            self._refresh_port_choices()
            if not self._building:
                self._refresh()
        except Exception:                                   # noqa: BLE001
            pass

    def on_enter(self) -> None:
        """The strip showed this workspace: pick up a file if none is."""
        self.refresh_files()

    def _refresh_port_choices(self) -> None:
        fe = self._file_entry()
        choices = port_choices(fe.ts) if fe is not None else []
        for key in ("in_p", "in_n", "out_p", "out_n"):
            self.table.set_column_values(key, choices)
        self.conn_table.set_port_choices(choices)

    def _on_file_changed(self, _event=None) -> None:
        self._refresh_port_choices()
        self._refresh()

    # ------------------------------------------------------------ the table

    @staticmethod
    def _from_cells(vals: dict) -> NetRow:
        return NetRow(name=str(vals.get("name", "")).strip(),
                      in_p=port_text_from_choice(vals.get("in_p", "")),
                      in_n=port_text_from_choice(vals.get("in_n", "")),
                      out_p=port_text_from_choice(vals.get("out_p", "")),
                      out_n=port_text_from_choice(vals.get("out_n", "")))

    def rows(self) -> list[NetRow]:
        return self.table.get_rows()

    def conn_rows(self) -> list:
        """The connection rows the nets are solved under, blanks dropped."""
        return self.conn_table.get_rows()

    def set_conn_rows(self, rows) -> None:
        """Fill the connections table programmatically and re-validate."""
        self.conn_table.set_rows(list(rows))
        if not self._building:
            self._refresh()

    def set_rows(self, rows) -> None:
        """Fill the table programmatically (a session load, a test) and run
        the same validation a keystroke would."""
        self.table.set_rows(list(rows))
        self._refresh()

    def _on_table_change(self) -> None:
        if not self._building:
            self._refresh()

    def _on_fields_changed(self) -> None:
        if not self._building:
            self._refresh()

    def _focused_row_index(self) -> Optional[int]:
        try:
            focus = self.left.focus_get()
        except Exception:                                   # noqa: BLE001
            return None
        if focus is None:
            return None
        for i in range(len(self.table.get_rows())):
            for key in NET_COLUMNS:
                if self.table.data_row_widget(i, key) is focus:
                    return i
        return None

    def duplicate_row(self, index: Optional[int] = None) -> None:
        """Copy a row below itself: the focused one, else the last."""
        rows = self.table.get_rows()
        if not rows:
            return
        i = index if index is not None else self._focused_row_index()
        if i is None or not (0 <= i < len(rows)):
            i = len(rows) - 1
        rows.insert(i + 1, replace(rows[i]))
        self.table.set_rows(rows)
        self._refresh()

    def remove_row(self, index: Optional[int] = None) -> None:
        """Drop a row: the focused one, else the last."""
        rows = self.table.get_rows()
        if not rows:
            return
        i = index if index is not None else self._focused_row_index()
        if i is None or not (0 <= i < len(rows)):
            i = len(rows) - 1
        rows.pop(i)
        self.table.set_rows(rows)
        self._refresh()

    # -------------------------------------------------------- the fields

    def _freq_hz(self) -> float:
        try:
            f = float(self.freq_var.get()) * 1e9
        except (TypeError, ValueError):
            return float("nan")
        return f if math.isfinite(f) and f > 0.0 else float("nan")

    def _terminations(self) -> tuple[float, float]:
        return _parse_terminations(self.src_var.get(), self.load_var.get())

    def _on_terminations_changed(self, _event=None) -> None:
        """Source / Load moved: the bandwidth block again, NO re-solve."""
        src, load = self._terminations()
        changed = False
        for sig, res in list(self._solved.items()):
            if res.z_src_ohm != src or res.c_load_extra_f != load:
                self._solved[sig] = rebandwidth(res, src, load)
                changed = True
        for name, res in list(self._last_by_name.items()):
            if res.z_src_ohm != src or res.c_load_extra_f != load:
                self._last_by_name[name] = rebandwidth(res, src, load)
                changed = True
        if changed:
            self._refresh(force_draw=True)

    # ---------------------------------------------------------- validation

    def _paint_issues(self) -> None:
        for w in self._painted:
            try:
                if w.winfo_exists():
                    w.configure(foreground="")
            except Exception:                               # noqa: BLE001
                pass
        self._painted = []
        fe = self._file_entry()
        nports = int(getattr(getattr(fe, "ts", None), "nports", 0) or 0)
        rows = self.table.get_rows()
        issues = validate_nets(rows, self.conn_rows(), nports)
        self._issues = issues
        self.conn_table.paint(issues)
        severity: dict = {}
        for iss in issues:
            if iss.table != "nets":
                continue
            w = self.table.data_row_widget(iss.row, iss.column)
            if w is None:
                continue
            key = str(w)
            if key in severity and severity[key][1] == "error":
                continue
            severity[key] = (w, iss.severity)
        for w, sev in severity.values():
            try:
                w.configure(foreground=ERROR_FG if sev == "error"
                            else WARN_CELL_FG)
                self._painted.append(w)
            except Exception:                               # noqa: BLE001
                pass
        lines = issue_lines(issues, rows)
        any_error = any(i.is_error for i in issues)
        self.issues_lbl.configure(
            text="\n".join(lines),
            foreground=ERROR_FG if any_error else WARN_CELL_FG)

    @property
    def issues(self) -> list:
        return list(self._issues)

    # ------------------------------------------------------------- results

    def _rebuild_results(self) -> None:
        conn = self.conn_rows()
        label = self._file_key()
        f = self._freq_hz()
        src, load = self._terminations()
        out = []
        for row in self.table.get_rows():
            sig = net_signature(row, conn, label, f)
            res = self._solved.get(sig)
            if res is None:
                res = self._retargeted(row, sig, conn, label, f)
            if res is None:
                prev = self._last_by_name.get(row.name.strip())
                if prev is not None:
                    res = replace(prev, status="stale")
                else:
                    res = _unsolved(row, "stale", "", sig, src, load)
            out.append(res)
        self._results = out
        names = [r.name for r in out]
        if self._selected_name not in names:
            self._selected_name = None

    @property
    def results(self) -> list[NetResult]:
        return list(self._results)

    @property
    def selected_name(self) -> Optional[str]:
        return self._selected_name

    def _selected_result(self) -> Optional[NetResult]:
        if self._selected_name is None:
            return None
        for r in self._results:
            if r.name == self._selected_name:
                return r
        return None

    def select_net(self, name: Optional[str]) -> None:
        self._selected_name = name
        self._render_summary()
        self._draw_schematic()
        self._draw_response()
        self._render_details()

    def _refresh(self, force_draw: bool = False) -> None:
        """Every edit lands here: validate, re-derive the display list,
        repaint.  Synchronous -- validation is microseconds and the
        canvases redraw only when the selected result changed."""
        self._paint_issues()
        self._rebuild_results()
        self._render_summary()
        self._rebuild_overlay_ticks()
        sel = self._selected_result()
        if force_draw or sel is not self._drawn:
            self._draw_schematic()
            self._draw_response()
            self._render_details()

    def _retargeted(self, row, sig, conn, label, f) -> Optional[NetResult]:
        """
        A row whose ONLY change is the marker frequency is not stale: its
        cached sweep is read at the new frequency (`retarget`, no sweep
        re-solve) and cached under the new signature.  This is what lets the
        Freq field and the marker drag answer at once instead of asking for
        Calculate all.
        """
        if not math.isfinite(f):
            return None
        same = sig[:-1]
        src = next((r for s, r in self._solved.items()
                    if s[:-1] == same and r.status == "ok"), None)
        if src is None:
            return None
        fe = self._file_entry()
        if fe is None:
            return None
        res = retarget(src, f, fe.Y, int(fe.ts.nports), row, conn, label)
        if res is src or res.signature != sig:
            return None
        self._solved[sig] = res
        self._last_by_name[res.name] = res
        return res

    def calculate_all(self) -> None:
        """
        Solve the rows that need it: every non-blank row without an `ok`
        result under its CURRENT signature.  Rows the table rules reject
        become error rows that say why in the Summary; the others are
        solved regardless.  No dialog on any path -- the status line and
        the Summary carry every outcome.
        """
        fe = self._file_entry()
        if fe is None:
            self.status_lbl.configure(
                text="Load a Touchstone file and pick it under File first.")
            return
        f = self._freq_hz()
        if not math.isfinite(f):
            self.status_lbl.configure(
                text="Freq is not a number -- give the marker frequency in "
                     "GHz.")
            return
        rows = self.table.get_rows()
        if not rows:
            self.status_lbl.configure(text="Add a net first (+ Add net).")
            return
        conn = self.conn_rows()
        src, load = self._terminations()
        nports = int(fe.ts.nports)
        issues = validate_nets(rows, conn, nports)
        # A connection row that does not parse is every net's error: none
        # of them can be solved under a spec that is not one.
        table_errors = [i.message for i in issues
                        if i.table == "conn" and i.is_error]
        row_errors: dict[int, str] = {}
        for i in issues:
            if i.table == "nets" and i.is_error and i.row not in row_errors:
                row_errors[i.row] = i.message

        solved = reused = skipped = 0
        key = self._file_key(fe)
        current: set[tuple] = set()
        for r, row in enumerate(rows):
            sig = net_signature(row, conn, key, f)
            current.add(sig)
            why = table_errors[0] if table_errors else row_errors.get(r)
            if why:
                self._solved[sig] = _unsolved(row, "error", why, sig, src, load)
                skipped += 1
                continue
            cached = self._solved.get(sig)
            if cached is not None and cached.status == "ok":
                reused += 1
                continue
            res = solve_net(fe.ts.freqs, fe.Y, nports, row, conn, f,
                            key, src, load)
            self._solved[sig] = res
            if res.status == "ok":
                solved += 1
                self._last_by_name[res.name] = res
                for w in list(res.warnings) + list(res.model.warnings):
                    self._log(f"  Trace model '{res.name}': {w}")
            else:
                skipped += 1
                self._log(f"  Trace model '{res.name}': {res.error}")
        # Only what the table holds now is worth keeping by signature; the
        # last good numbers of a renamed-away net stay reachable by name.
        for sig in list(self._solved):
            if sig not in current:
                del self._solved[sig]
        bits = [f"{solved} solved"]
        if reused:
            bits.append(f"{reused} unchanged")
        if skipped:
            bits.append(f"{skipped} skipped (see the Summary)")
        self.status_lbl.configure(text=", ".join(bits) + ".")
        self._rebuild_results()
        if self._selected_name is None:
            for res in self._results:
                if res.model is not None:
                    self._selected_name = res.name
                    break
        self._refresh(force_draw=True)

    def _log(self, text: str) -> None:
        append = getattr(self.app, "_append_result", None)
        if append is None:
            return
        try:
            append(text, LOG_WARN)
        except Exception:                                   # noqa: BLE001
            pass

    # ------------------------------------------------------------- summary

    def _render_summary(self) -> None:
        lines = summary_table_lines(self._results, self._sort_key,
                                    self._sort_desc)
        order = summary_order(self._results, self._sort_key, self._sort_desc)
        txt = self.summary
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        txt.insert(tk.END, lines[0], ("hdr",))
        for k, line in enumerate(lines[1:]):
            res = self._results[order[k]]
            tags = []
            if res.status == "stale":
                tags.append("stale")
            elif res.status == "error":
                tags.append("error")
            if res.name == self._selected_name:
                tags.append(SUMMARY_SEL_TAG)
            # The newline is inserted untagged, so a row's tags start at
            # its own column 0 and the selection band is one line, not the
            # tail of the line above it as well.
            txt.insert(tk.END, "\n")
            txt.insert(tk.END, line, tuple(tags))
        if not self._results:
            txt.insert(tk.END, "\n(no nets -- add one on the left and press "
                               "Calculate all)", ("stale",))
        txt.configure(state=tk.DISABLED)

    def summary_lines(self) -> list[str]:
        """What the Summary pane shows, line by line."""
        return self.summary.get("1.0", "end-1c").split("\n")

    def sort_by(self, key: Optional[str]) -> None:
        """Click a header: sort on it; click again: the other way."""
        if key is None:
            self._sort_key, self._sort_desc = None, False
        elif key == self._sort_key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key, self._sort_desc = key, False
        self._render_summary()

    @property
    def sort_state(self) -> tuple[Optional[str], bool]:
        return (self._sort_key, self._sort_desc)

    def _on_summary_click(self, event) -> str:
        try:
            idx = self.summary.index(f"@{event.x},{event.y}")
            line, col = (int(v) for v in idx.split("."))
        except (tk.TclError, ValueError):
            return "break"
        self.summary_click(line, col)
        return "break"

    def summary_click(self, line: int, col: int) -> None:
        """Line 1 is the header (sort by the column under `col`); a body
        line selects the net it shows."""
        if line <= 1:
            for key, _hdr, start, end in SUMMARY_COLUMNS:
                if start <= col < end:
                    self.sort_by(key)
                    return
            return
        order = summary_order(self._results, self._sort_key, self._sort_desc)
        k = line - 2
        if 0 <= k < len(order):
            self.select_net(self._results[order[k]].name)

    # ----------------------------------------------------------- schematic

    def _draw_schematic(self) -> None:
        cv = self.canvas
        cv.delete("all")
        res = self._selected_result()
        self._drawn = res
        w = max(cv.winfo_width(), 300)
        h = max(cv.winfo_height(), 160)
        if res is None:
            cv.create_text(w / 2, h / 2, fill=PLACEHOLDER_FG,
                           font=("TkDefaultFont", 9),
                           text="Select a net in the Summary to draw it.")
            return
        if res.model is None:
            why = (f"error: {res.error}" if res.status == "error"
                   else "not calculated yet -- press Calculate all")
            cv.create_text(w / 2, h / 2, fill=WARN_FG, width=w - 20,
                           font=("TkDefaultFont", 9),
                           text=f"{res.name}: {why}")
            return
        # Keep the drawing's aspect so a very wide pane does not stretch the
        # legs into a letterbox; centre what is left over.
        scale = min(w / CANVAS_W, h / CANVAS_H)
        dw, dh = CANVAS_W * scale, CANVAS_H * scale
        ox, oy = (w - dw) / 2.0, (h - dh) / 2.0
        fs = max(7, int(round(9 * scale)))
        mono = (TM_FONT[0], fs)
        for it in pi_canvas_items(res.model, dw, dh):
            c = [v + (ox if k % 2 == 0 else oy) for k, v in enumerate(it.coords)]
            if it.kind == "line":
                cv.create_line(*c, fill=_GND_FG if it.role == "gnd" else _WIRE_FG,
                               width=2)
            elif it.kind == "rect":
                cv.create_rectangle(*c, fill=_BOX_FILL, outline=_BOX_EDGE,
                                    width=1)
            elif it.kind == "oval":
                cv.create_oval(*c, fill=_WIRE_FG, outline=_WIRE_FG)
            elif it.kind == "text":
                if it.role == "value":
                    fill, font = _VALUE_FG, mono
                elif it.role == "note":
                    fill, font = PLACEHOLDER_FG, (TM_FONT[0], max(7, fs - 1))
                else:
                    fill, font = _WIRE_FG, ("TkDefaultFont", max(7, fs))
                cv.create_text(c[0], c[1], text=it.text, fill=fill, font=font,
                               anchor=it.anchor)
        kind = "differential" if res.model.differential else "single-ended"
        cv.create_text(6, 4, anchor="nw", fill=PLACEHOLDER_FG,
                       font=("TkDefaultFont", 8),
                       text=f"{res.name}  ({kind})")
        if res.status == "stale":
            cv.create_text(w - 6, 4, anchor="ne", fill=WARN_FG,
                           font=("TkDefaultFont", 8),
                           text="STALE -- press Calculate all")

    # ------------------------------------------------------------ response

    def _rebuild_overlay_ticks(self) -> None:
        names = [r.name for r in self._results if r.model is not None]
        current = [b.cget("text") for b in self._overlay_btns]
        if names == current:
            return
        for b in self._overlay_btns:
            b.destroy()
        self._overlay_btns = []
        for name in names:
            var = self._overlay_vars.get(name)
            if var is None:
                var = tk.BooleanVar(master=self._overlay_row, value=False)
                self._overlay_vars[name] = var
            b = ttk.Checkbutton(self._overlay_row, text=name, variable=var,
                                command=self._draw_response)
            b.pack(side=tk.LEFT, padx=(6, 0))
            self._overlay_btns.append(b)
        for name in list(self._overlay_vars):
            if name not in names:
                del self._overlay_vars[name]

    def overlay_names(self) -> list[str]:
        """The nets ticked for overlay, other than the selected one."""
        return [n for n, v in self._overlay_vars.items()
                if v.get() and n != self._selected_name]

    def set_overlay(self, name: str, on: bool) -> None:
        var = self._overlay_vars.get(name)
        if var is not None:
            var.set(bool(on))
            self._draw_response()

    @staticmethod
    def _db_curve(res: NetResult, z_src: float, c_load: float):
        H = tmod.transfer_function(res.Z2, res.freqs, z_src, c_load)
        mag = np.abs(H)
        ref = mag[0] if (len(mag) and np.isfinite(mag[0]) and mag[0] > 0) \
            else 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            db = 20.0 * np.log10(mag / ref)
        # Clipped to the window's dB range HERE, so a peak above the
        # ceiling flattens against the top instead of running off the
        # canvas (the old window's first defect).  NaN stays NaN.
        return np.clip(db, RESPONSE_DB_FLOOR, RESPONSE_DB_CEIL)

    def _resp_axis(self):
        """(x0, x1, log10 f_lo, log10 f_hi, freqs) of the drawn response, or
        None -- the SAME padding `response_canvas_items` lays out with."""
        res = self._selected_result()
        if res is None or res.model is None or res.freqs is None:
            return None
        f = np.asarray(res.freqs, dtype=float)
        pos = f[f > 0.0]
        if pos.size < 2:
            return None
        plot_w = max(120, max(self.resp.winfo_width(), 300) - _LEGEND_W)
        x0, x1 = plot_w * 0.115, plot_w - plot_w * 0.035
        lo, hi = math.log10(float(pos[0])), math.log10(float(pos[-1]))
        if hi <= lo or x1 <= x0:
            return None
        return x0, x1, lo, hi, pos

    def _resp_hz_at(self, x: float) -> Optional[float]:
        ax = self._resp_axis()
        if ax is None:
            return None
        x0, x1, lo, hi, pos = ax
        t = min(1.0, max(0.0, (float(x) - x0) / (x1 - x0)))
        hz = 10.0 ** (lo + t * (hi - lo))
        # Snap to the sweep: the pi is read at the nearest point anyway, and
        # the field then shows the frequency the numbers are actually at.
        return float(pos[int(np.argmin(np.abs(pos - hz)))])

    def _on_resp_drag(self, event) -> None:
        hz = self._resp_hz_at(event.x)
        if hz is None:
            return
        ax = self._resp_axis()
        x0, x1, lo, hi, _pos = ax
        x = x0 + (math.log10(hz) - lo) / (hi - lo) * (x1 - x0)
        h = max(self.resp.winfo_height(), 100)
        self.resp.delete("drag")
        self.resp.create_line(x, h * 0.12, x, h * 0.78, fill=_MARKER_FG,
                              width=2, tags="drag")
        self.resp.create_text(x, h * 0.06, fill=_MARKER_FG, tags="drag",
                              font=(TM_FONT[0], 8),
                              text=f"{hz / 1e9:.6g} GHz")

    def _on_resp_release(self, event) -> None:
        hz = self._resp_hz_at(event.x)
        self.resp.delete("drag")
        if hz is None:
            return
        self.freq_var.set(f"{hz / 1e9:.6g}")

    def _draw_response(self) -> None:
        cv = self.resp
        cv.delete("all")
        res = self._selected_result()
        w = max(cv.winfo_width(), 300)
        h = max(cv.winfo_height(), 100)
        if res is None or res.model is None or res.freqs is None \
                or len(res.freqs) < 2 or not res.bw_table:
            cv.create_text(w / 2, h / 2, fill=PLACEHOLDER_FG,
                           font=("TkDefaultFont", 9),
                           text="|H(f)| of the selected net, one curve per "
                                "load.")
            return
        plot_w = max(120, w - _LEGEND_W)
        fs = max(7, min(9, int(round(h / 18))))
        f = np.asarray(res.freqs, dtype=float)

        def drawable(db) -> bool:
            return int(np.sum((f > 0.0) & np.isfinite(db))) >= 2

        curves: list[tuple[str, object]] = []
        for bw in res.bw_table:
            db = self._db_curve(res, bw.z_src_ohm, bw.c_load_farad)
            if drawable(db):
                label = (f"{bw.c_load_farad * 1e15:g} fF"
                         if bw.c_load_farad else "open")
                curves.append((label, db))
        n_sel = len(curves)
        for name in self.overlay_names():
            other = next((r for r in self._results if r.name == name), None)
            if other is None or other.model is None or other.Z2 is None:
                continue
            if not np.array_equal(np.asarray(other.freqs, dtype=float), f):
                continue
            db = self._db_curve(other, res.bw_table[0].z_src_ohm, 0.0)
            if drawable(db):
                curves.append((name, db))

        line_i = 0
        for it in response_canvas_items(f, curves, plot_w, h,
                                        res.model.freq_hz):
            if it.kind == "line":
                if it.role == "grid":
                    cv.create_line(*it.coords, fill=_GRID_FG, dash=(4, 3))
                elif it.role == "marker":
                    cv.create_line(*it.coords, fill=_MARKER_FG, dash=(2, 3))
                elif it.role.startswith("curve"):
                    if line_i < n_sel:
                        cv.create_line(*it.coords, width=2,
                                       fill=_CURVE_FG[line_i % 4])
                    else:
                        j = line_i - n_sel
                        cv.create_line(*it.coords, width=1, dash=(5, 3),
                                       fill=_OVERLAY_FG[j % 4])
                    line_i += 1
                else:
                    cv.create_line(*it.coords, fill=_AXIS_FG)
            elif it.kind == "text":
                if it.role.startswith("curve"):
                    # The load labels are the LEGEND beside the plot, not
                    # text over the curves (the old window's second defect).
                    continue
                fill = _MARKER_FG if it.role == "note" else _AXIS_FG
                cv.create_text(it.coords[0], it.coords[1], text=it.text,
                               fill=fill, font=(TM_FONT[0], fs),
                               anchor=it.anchor)
        # ---- the legend column
        x = plot_w + 10
        y = h * 0.12
        step = max(12, fs + 6)
        for i, (label, _db) in enumerate(curves):
            if i < n_sel:
                cv.create_line(x, y, x + 18, y, width=2, fill=_CURVE_FG[i % 4])
            else:
                cv.create_line(x, y, x + 18, y, width=1, dash=(5, 3),
                               fill=_OVERLAY_FG[(i - n_sel) % 4])
            cv.create_text(x + 24, y, text=label[:12], anchor="w",
                           fill=_AXIS_FG, font=(TM_FONT[0], fs))
            y += step

    def legend_texts(self) -> list[str]:
        """The legend's labels, left to right in the column (for tests)."""
        out = []
        for i in self.resp.find_all():
            if self.resp.type(i) == "text":
                x = self.resp.coords(i)[0]
                if x >= max(120, max(self.resp.winfo_width(), 300) - _LEGEND_W):
                    out.append(self.resp.itemcget(i, "text"))
        return out

    # ------------------------------------------------------------- details

    def toggle_details(self) -> None:
        self._details_open = not self._details_open
        if self._details_open:
            self.details_btn.configure(text="▾ Details")
            self._details_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, padx=4,
                                     pady=(0, 2))
        else:
            self.details_btn.configure(text="▸ Details")
            self._details_frame.pack_forget()

    @property
    def details_open(self) -> bool:
        return self._details_open

    def details_lines(self) -> list[str]:
        res = self._selected_result()
        if res is None:
            return ["Select a net in the Summary."]
        lines: list[str] = []
        if res.status == "stale":
            lines.append("STALE: the row, the connections, the file or "
                         "the frequency changed "
                         "since this was solved; the numbers below are the "
                         "ones it had.  Press Calculate all.")
        if res.model is None:
            if res.status == "error":
                lines.append(f"{res.name}: error: {res.error}")
            else:
                lines.append(f"{res.name}: not calculated yet -- press "
                             "Calculate all.")
            return lines
        lines.extend(pi_report_lines(
            res.model, freq_snap=res.freq_snap, reference=res.reference,
            mode_conversion=res.mode_conversion, port_note=res.mc_note,
            drawing=False))
        top = float(res.freqs[-1]) if len(res.freqs) else float("nan")
        lines.extend(bandwidth_lines(list(res.bw_table), list(res.corners),
                                     tuple(res.model_band), top))
        for wtxt in res.warnings:
            lines.append(f"  WARN: {wtxt}")
        return lines

    def _render_details(self) -> None:
        txt = self.details_text
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        txt.insert("1.0", "\n".join(self.details_lines()))
        txt.configure(state=tk.DISABLED)

    # ------------------------------------------------------------- export

    def export_csv(self, path: Optional[str] = None) -> Optional[str]:
        """Write the Summary and every net's branch values; stdlib csv."""
        if path is None:
            path = filedialog.asksaveasfilename(
                parent=self.right, title="Export trace model summary",
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
            if not path:
                return None
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(csv_rows(self._results))
        self.status_lbl.configure(text=f"Exported {len(self._results)} net(s) "
                                       f"to {path}")
        return path

    # ------------------------------------------------------------- session

    def state_get(self) -> dict:
        """The block under "trace" in the session's "workspaces": the
        table, the connection rows and the four fields, at inner version
        `TRACE_STATE_VERSION`.  Results are NOT saved.  {} at the
        defaults, so a session that never used the workspace carries no
        block."""
        rows = [{k: str(getattr(r, k, "")) for k in NET_COLUMNS}
                for r in self.table.get_rows()]
        conn = rows_to_json(self.conn_rows())
        state = {
            "version": TRACE_STATE_VERSION,
            "file": self.file_var.get(),
            "rows": rows,
            "conn_rows": conn,
            "freq_ghz": self.freq_var.get().strip(),
            "src_ohm": self.src_var.get().strip(),
            "load_ff": self.load_var.get().strip(),
        }
        if (not rows and not conn
                and state["freq_ghz"] == DEFAULT_FREQ_GHZ
                and state["src_ohm"] == f"{DEFAULT_SRC_OHM:g}"
                and state["load_ff"] == f"{DEFAULT_LOAD_EXTRA_F * 1e15:g}"):
            return {}
        return state

    def state_set(self, data: dict) -> None:
        """
        Restore `state_get`'s block.  Unknown keys are ignored; a garbled
        row list costs that list.  Results start empty.

        A stage-1 block (no `version`, or 1) has a `gnd` STRING and no
        `conn_rows`: it is read as ONE ground row (`ground_rows`), which
        computes exactly what that field did.  A block newer than this
        build raises, and the switch drops it with a note in the Log.
        """
        raw_version = data.get("version", 1)
        try:
            version = int(raw_version if raw_version is not None else 1)
        except (TypeError, ValueError):
            raise ValueError(f"trace block version {raw_version!r} is not "
                             "a number") from None
        if version > TRACE_STATE_VERSION:
            raise ValueError(f"trace block version {version} is newer than "
                             f"this build reads ({TRACE_STATE_VERSION})")
        if "conn_rows" in data:
            conn = conn_rows_from_json(data.get("conn_rows"))
        else:
            conn = ground_rows(str(data.get("gnd", "") or ""))
        self._building = True
        try:
            self.file_var.set(str(data.get("file", "") or ""))
            self.conn_table.set_rows(conn)
            self.freq_var.set(str(data.get("freq_ghz", DEFAULT_FREQ_GHZ)
                                  or DEFAULT_FREQ_GHZ))
            self.src_var.set(str(data.get("src_ohm", f"{DEFAULT_SRC_OHM:g}")
                                 or f"{DEFAULT_SRC_OHM:g}"))
            self.load_var.set(str(data.get("load_ff",
                                           f"{DEFAULT_LOAD_EXTRA_F * 1e15:g}")
                                  or f"{DEFAULT_LOAD_EXTRA_F * 1e15:g}"))
            rows = []
            for d in (data.get("rows") or []):
                if isinstance(d, dict):
                    rows.append(NetRow(**{k: str(d.get(k, "") or "")
                                          for k in NET_COLUMNS}))
            self.table.set_rows(rows)
            self._solved.clear()
            self._last_by_name.clear()
            self._selected_name = None
            self._refresh_port_choices()
        finally:
            self._building = False
        self._refresh(force_draw=True)
