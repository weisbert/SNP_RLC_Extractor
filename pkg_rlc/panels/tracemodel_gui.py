"""
pkg_rlc/panels/tracemodel_gui.py -- the Trace Model window.

A modeless `Toplevel` over `pkg_rlc.physics.tracemodel`: the routed trace
between two measurement ports, DRAWN on a `tk.Canvas` with every element value
on it, plus the verdicts a reader needs before believing any of them.

It imports `pkg_rlc.frontend.app` NOT AT ALL -- everything it needs off the App
is duck-typed through the object it is handed, which is the rule
`pkg_rlc.panels.attrib_gui` follows and the reason neither module has a
deferred import left.  The hooks it uses are, in full:

    app._flush_editor_sync()        app._file_by_label(label)
    app.traces                      app._trace_network(trace)
    app._build_termination(...)     app._current_run_number()
    app.rlc_freq_var.get()          app._append_result(text, LOG_WARN)

WHY A CANVAS, AND WHY THIS ONE
------------------------------
`docs/conventions/rejected_ui.md` turned down a matplotlib schematic in a tab
beside the plot, and ends: "If a schematic is ever built, it is a `tk.Canvas`
in a Toplevel, like the Ports & Roles window."  This is that.  It is not a
tab -- it costs the plot no height at all -- and it is not matplotlib, so it
does not pay the ~10x redraw this tool measured.

THE DRAWING IS NOT AUTHORED HERE
--------------------------------
Every coordinate comes from `pkg_rlc.present.tracemodel_report.pi_canvas_items`
and this module does nothing but hand each item to `Canvas.create_*` with a
colour and a font.  Two reasons, and the second is the load-bearing one:

  * the geometry is then testable with NO DISPLAY, in `FAST_MODULES`, which is
    where a bug in "where things are" can actually be caught cheaply;
  * the window and the text block read the SAME `PiModel` through the SAME
    `branch_value_lines`, so they cannot print different numbers for one
    measurement.  A second formatter is how two surfaces of one tool drift.

NOTHING IS RE-SOLVED FOR THE PI
-------------------------------
`Calculate` already caches `Zmat` (nfreqs, G, G) on the `TraceConfig`, and it
is the OPEN-CIRCUIT matrix, so the 2x2 sub-block over the two measurement
ports IS the two-port Z the pi identity wants.  The window reads it.  The one
solve it ever runs is the differential imbalance check, and that one is a
single frequency wide.

STALENESS, NOT AUTO-REFRESH
---------------------------
Same rule as the Attribution window, for the same reason: a window holding a
result cannot re-read its way out of the subject changing underneath it.  The
result is frozen at open time with the spec signature and run number stamped
on it; `refresh_banner` compares those against the live trace and says which
of four states it is in, and `[Recompute]` is the user's move, not ours.
"""

from __future__ import annotations

import math
import tkinter as tk
import weakref
from dataclasses import dataclass, replace
from tkinter import messagebox, ttk
from typing import Optional

import numpy as np

# Every one of these is a DOWNWARD import (L5 reaching L0/L1/L3/L4) and so
# belongs at module level.  Writing any of them inside a function would make
# `tests/test_layering.py` refuse it as an undeclared back-import -- rightly:
# a lazy import that dodges nothing only hides where the edge is.
from pkg_rlc.model.trace import _config_signature, snap_to_grid
from pkg_rlc.physics import tracemodel as tmod
from pkg_rlc.physics.core import (
    build_terminations_coupling, compute_z_matrix,
)
from pkg_rlc.present.report import LOG_WARN
from pkg_rlc.present.tracemodel_report import (
    CANVAS_H, CANVAS_W, bandwidth_lines, pi_canvas_items, pi_report_lines,
    response_canvas_items,
)
from pkg_rlc.widgets.widgets import PLACEHOLDER_FG, WARN_FG

__all__ = [
    "TRACE_MODEL_MENU_LABEL", "TRACE_MODEL_TITLE",
    "TraceModelResult", "TraceModelWindow",
    "trace_model_refusal", "resolve_ends", "spec_signature",
    "staleness_text", "header_text",
    "compute_trace_model", "open_trace_model_window",
    "live_windows", "refresh_trace_model_windows",
]

#: The menubar / right-click label.  A real U+2026, matching `ATTRIB_MENU_LABEL`
#: -- three full stops would sort and measure differently beside it.
TRACE_MODEL_MENU_LABEL = "Trace model…"
TRACE_MODEL_TITLE = "Trace model"

TM_GEOMETRY = "820x660"
TM_MIN_W = 600
TM_MIN_H = 480
TM_FONT = ("Consolas", 9)

#: Canvas styling, keyed by the `role` the geometry declares.  The palette
#: lives HERE and the coordinates live at L3, which is the whole split.
_WIRE_FG = "#2c2c2c"
_BOX_FILL = "#eef2f7"
_BOX_EDGE = "#5a6b80"
_VALUE_FG = "#12304f"
_GND_FG = "#2c2c2c"
_AXIS_FG = "#7a8694"
_GRID_FG = "#c05a5a"          # the -3 dB rule: the one line the eye wants
_MARKER_FG = "#3b7dd8"        # the working frequency
#: One colour per load capacitance in the sensitivity table.  Four, because
#: `DEFAULT_LOADS_F` is four; a fifth load would need a fifth colour here and
#: the geometry cycles `curve0..curve3`, so the two have to move together.
_CURVE_FG = ("#1b6ca8", "#2e8b57", "#b8860b", "#8b3a62")

#: Defaults for the two termination fields.  1 ohm stands in for a near-ideal
#: voltage source (0 would put an exact zero into the denominator), and an
#: OPEN far end is the upper bound on any real answer -- the honest starting
#: point, because every real load can only bring the number down.
DEFAULT_SRC_OHM = 1.0
DEFAULT_LOAD_FF = 0.0


# ============================================================================
# Pure -- no Tk, testable with no display
# ============================================================================

@dataclass(frozen=True)
class TraceModelResult:
    """
    One computed trace model, frozen.

    It holds NO `TraceConfig` and NO `FileEntry` -- identity is resolved to
    plain strings and ints at compute time.  That is the same rule
    `AttribResult` and `RunSnapshot` follow, and it is what stops a window
    from quietly re-reading a subject that has been edited underneath it.
    """
    model: tmod.PiModel
    reference: Optional[tmod.PiModel]
    mode_conversion: Optional[float]
    port_note: str
    freq_snap: object
    # The sweep this was read from, kept so the bandwidth block and the
    # response curve can be recomputed for new terminations WITHOUT a
    # re-solve.  Plain arrays, no TraceConfig and no FileEntry -- the
    # frozen-identity rule is about what can go stale underneath you, and an
    # array that was copied out of the trace cannot.
    freqs: object
    Z2: object
    bw_table: tuple
    corners: tuple
    model_band: tuple
    trace_id: int
    trace_label: str
    file_label: str
    run_number: int
    signature: tuple


def spec_signature(trace) -> tuple:
    """
    The trace's spec as a comparable tuple.

    A pass-through to `pkg_rlc.model.trace._config_signature` on purpose, not
    a second copy: two definitions of "the spec changed" is two answers.
    """
    return _config_signature(trace)


def resolve_ends(trace) -> Optional[tuple[int, int, bool]]:
    """
    Which two measurement ports are the two ENDS -- or None.

    A pi has two nodes, so a trace with exactly two measurement ports needs no
    picker and gets none: they ARE the ends, in the order they were declared.
    More than two is a coupling study rather than a trace, and is refused by
    name rather than guessed at.

    Returns `(0, 1, differential)`, where `differential` is true when either
    declared port carries a minus side -- which is exactly what makes
    `compute_z_matrix` return the differential 2x2.
    """
    names = getattr(trace, "mport_names", None)
    if not names or len(names) != 2:
        return None
    rows = [r for r in (getattr(trace, "mports", None) or [])
            if not getattr(r, "is_blank", lambda: False)()]
    differential = any(str(getattr(r, "minus", "")).strip() for r in rows[:2])
    return (0, 1, differential)


def trace_model_refusal(trace, file_entry) -> Optional[str]:
    """
    Why this trace cannot be drawn as a pi -- or None when it can.

    Pure and `getattr`-only, so it is importable and assertable with no Tk.
    Order matters: the cheapest and most likely reason first, and every one of
    them names the fix rather than the symptom.
    """
    if trace is None:
        return ("Select a trace first.\n\nThe trace model draws ONE trace as a "
                "circuit, so it needs to know which one.")
    if file_entry is None:
        return (f"The file this trace refers to is not loaded.\n\n"
                f"Load '{getattr(trace, 'file_label', '?')}' again, or point "
                f"the trace at a file that is open.")
    Zmat = getattr(trace, "Zmat", None)
    if Zmat is None:
        return ("This trace has not been calculated in a mode that produces a "
                "port matrix.\n\nThe trace model reads the OPEN-CIRCUIT Z "
                "matrix that Mode 6 (coupling / measurement ports) leaves "
                "behind. Set the trace to Mode 6, declare the two ends as "
                "measurement ports, and press Calculate.")
    ends = resolve_ends(trace)
    if ends is None:
        n = len(getattr(trace, "mport_names", None) or [])
        return (f"A pi has two nodes, and this trace declares "
                f"{n} measurement port(s).\n\nDeclare exactly two -- the IN "
                f"end and the OUT end of the trace. For a differential trace "
                f"give each one a minus side too (e.g. + 1  - 2 and "
                f"+ 3  - 4).")
    if getattr(trace, "stale", False):
        return ("The spec has been edited since this trace was last "
                "calculated, so the cached matrix is not what the spec now "
                "says.\n\nPress Calculate, then open this window again.")
    return None


def header_text(res: TraceModelResult) -> str:
    """The one-line subject strip: which trace, which file, which ports."""
    kind = "differential" if res.model.differential else "single-ended"
    return (f"[{res.trace_id}] {res.trace_label}   ·   {res.file_label}   ·   "
            f"{kind}:  {res.model.in_name} → {res.model.out_name}")


def staleness_text(res: TraceModelResult, trace, exists: bool) -> tuple[str, bool]:
    """
    Whether what is on screen still describes the live trace.

    Returns `(text, is_warning)`.  Four states, and the middle two are the
    ones worth having a banner for at all: a result that was right when it was
    computed and is not any more looks exactly like one that is still right.
    """
    if not exists:
        return ("The trace this was computed from has been removed. The "
                "numbers below are the ones it had.", True)
    if spec_signature(trace) != res.signature:
        return ("The trace's spec has been EDITED since this was computed. "
                "Press Recompute to draw what it says now.", True)
    if getattr(trace, "stale", False):
        return ("The trace has not been recalculated since it was edited. "
                "Press Calculate in the main window, then Recompute here.",
                True)
    return (f"Computed from run #{res.run_number}; the spec has not moved "
            f"since.", False)


def _mode_conversion_for(app, trace, file_entry, rows, k) -> tuple[Optional[float], str]:
    """
    The differential imbalance, and why it was skipped when it was.

    ONE solve, ONE frequency wide (`Y[k:k+1]`).  It is here rather than left
    unsaid because the differential pi assumes common mode OPEN at both ends:
    exact for a symmetric pair, an unstated assumption otherwise, and an
    unstated assumption is what `attrib` exists because of.
    """
    try:
        plus_minus = []
        for r in rows[:2]:
            p = [int(t) for t in str(getattr(r, "plus", "")).replace(",", " ").split()]
            m = [int(t) for t in str(getattr(r, "minus", "")).replace(",", " ").split()]
            if len(p) != 1 or len(m) != 1:
                return (None, "imbalance check skipped -- a probe side names "
                              "more than one port, which has no four-port form.")
            plus_minus.append((p[0], m[0]))
    except ValueError:
        return (None, "imbalance check skipped -- a port spec here is a range "
                      "or a name, which has no four-port form.")

    try:
        net = app._trace_network(trace)
    except Exception as e:                                  # noqa: BLE001
        return (None, f"imbalance check skipped -- {e}")
    if getattr(net, "composed", False):
        # The four ports would live in different files and the port numbers
        # here are scoped to the home alias, so the four-port form is not the
        # same question.  Skipped by name rather than answered wrongly.
        return (None, "imbalance check skipped on a composed network -- the "
                      "four ports are not all in one file.")

    try:
        (a_p, a_m), (b_p, b_m) = plus_minus
        four = [("A_p", [a_p], []), ("A_n", [a_m], []),
                ("B_p", [b_p], []), ("B_n", [b_m], [])]
        term4 = build_terminations_coupling(four, (), (), nports=net.nports)
        Z4, _n, _w = compute_z_matrix(net.Y[k:k + 1], net.freqs[k:k + 1],
                                      term4)
        return (tmod.mode_conversion_ratio(Z4[0]), "")
    except Exception as e:                                  # noqa: BLE001
        return (None, f"imbalance check skipped -- {e}")


def compute_trace_model(app, trace, file_entry, freq_hz: float,
                        z_src_ohm: float = DEFAULT_SRC_OHM,
                        c_load_farad_extra: "tuple[float, ...]" =
                        tmod.DEFAULT_LOADS_F) -> TraceModelResult:
    """
    Read the pi off the trace's CACHED matrix.  No re-solve for the pi itself.

    `Zmat` is the open-circuit matrix, so the 2x2 sub-block over the two
    measurement ports is the two-port Z the identity wants, with every
    termination, short, lumped element and merged node already baked in.
    """
    ends = resolve_ends(trace)
    assert ends is not None, "refusal should have caught this"
    i, j, differential = ends

    Zmat = np.asarray(trace.Zmat, dtype=complex)
    # `net_freqs` is None for every single-file trace and means "the home
    # file's own sweep"; on a COMPOSED trace it is the composed axis, and
    # drawing against the home file's freqs there would misplace every
    # point in the sweep with no symptom but a plausible curve.
    freqs = getattr(trace, "net_freqs", None)
    if freqs is None:
        freqs = file_entry.ts.freqs
    freqs = np.asarray(freqs, dtype=float)

    sel = np.array([i, j])
    Z2 = Zmat[:, sel[:, None], sel[None, :]]
    names = list(trace.mport_names)

    model = tmod.extract_pi_at(freqs, Z2, freq_hz, names[i], names[j],
                               differential)
    reference = tmod.extract_pi_at(freqs, Z2, float(freqs[0]), names[i],
                                   names[j], differential)

    mc: Optional[float] = None
    note = ""
    if differential:
        rows = [r for r in (getattr(trace, "mports", None) or [])
                if not getattr(r, "is_blank", lambda: False)()]
        k = int(np.argmin(np.abs(freqs - float(freq_hz))))
        mc, note = _mode_conversion_for(app, trace, file_entry, rows, k)

    # The same two lines `_cli_marker` uses: snap_to_grid re-derives how far
    # the marker moved and how coarse the grid is, and the extractor's own
    # freq_hz OVERRIDES the re-derived point so the two can never drift into
    # printing different frequencies for one number.
    snap = snap_to_grid(freqs, float(freq_hz))
    if snap.resolved:
        snap = replace(snap, actual_hz=float(model.freq_hz))

    bw_table = tuple(tmod.bandwidth_table(freqs, Z2, freq_hz,
                                          z_src_ohm, c_load_farad_extra))
    return TraceModelResult(
        model=model, reference=reference, mode_conversion=mc, port_note=note,
        freq_snap=snap, freqs=freqs, Z2=Z2, bw_table=bw_table,
        corners=tuple(tmod.branch_corners(model)),
        model_band=tmod.model_band_hz(freqs, Z2, names[i], names[j],
                                      differential),
        trace_id=int(getattr(trace, "id", 0)),
        trace_label=str(getattr(trace, "label", "")),
        file_label=str(getattr(trace, "file_label", "")),
        run_number=int(app._current_run_number()),
        signature=spec_signature(trace))


# ============================================================================
# The window
# ============================================================================

_LIVE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def live_windows(app) -> list:
    """Open Trace Model windows for this App, pruned of dead Toplevels."""
    out = []
    for w in list(_LIVE.get(app, [])):
        try:
            if w.winfo_exists():
                out.append(w)
        except Exception:                                   # noqa: BLE001
            pass
    _LIVE[app] = out
    return out


class TraceModelWindow(tk.Toplevel):
    """
    The drawn pi, with the verdicts under it.

    Modeless and deliberately NOT `transient(app)`: this is a thing you keep
    open beside the main window while you edit the spec, the same call the
    Attribution window makes.  No `<Escape>` binding either -- a Toplevel is
    in every descendant's bindtags, so Escape typed in a field would destroy
    the window and throw the result away.
    """

    def __init__(self, app, trace, file_entry, res: TraceModelResult):
        super().__init__(app)
        self._app = app
        self._trace = trace
        self._fe = file_entry
        self._res = res
        self.title(f"{TRACE_MODEL_TITLE} - [{res.trace_id}] {res.trace_label}")
        self.geometry(TM_GEOMETRY)
        self.minsize(TM_MIN_W, TM_MIN_H)

        # Footer FIRST, then the strips, then the expanding body -- pack
        # unmaps from the end, so the drawing is what gives up height when the
        # window is squeezed, not the buttons.
        foot = ttk.Frame(self)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        self._recompute_btn = ttk.Button(foot, text="Recompute",
                                         command=self._on_recompute)
        self._recompute_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self._header = ttk.Label(self, text=header_text(res), wraplength=0)
        self._header.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))
        self._banner = ttk.Label(self, text="", wraplength=0,
                                 foreground=PLACEHOLDER_FG)
        self._banner.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))

        # The two terminations, ON THE WINDOW rather than in a dialog.  The
        # -3 dB bandwidth is a property of trace PLUS source PLUS load -- on a
        # real routed line the load alone moves it 7.3x -- so the two numbers
        # that decide it have to be visible beside the answer, and editable
        # without leaving it.  They are window-local: no TraceConfig field, so
        # no session migration is owed for a pair of what-if knobs.
        strip = ttk.Frame(self)
        strip.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(strip, text="source").pack(side=tk.LEFT)
        self._src_var = tk.StringVar(value=f"{DEFAULT_SRC_OHM:g}")
        e1 = ttk.Entry(strip, textvariable=self._src_var, width=7)
        e1.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(strip, text="\u03a9     extra load").pack(side=tk.LEFT)
        self._load_var = tk.StringVar(value=f"{DEFAULT_LOAD_FF:g}")
        e2 = ttk.Entry(strip, textvariable=self._load_var, width=7)
        e2.pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(strip, text="fF").pack(side=tk.LEFT)
        ttk.Label(strip, text="   (the table below sweeps the load anyway; "
                              "these two pin the highlighted row)",
                  foreground=PLACEHOLDER_FG).pack(side=tk.LEFT, padx=(8, 0))
        for e in (e1, e2):
            e.bind("<Return>", self._on_terminations_changed)
            e.bind("<FocusOut>", self._on_terminations_changed)

        body = ttk.PanedWindow(self, orient=tk.VERTICAL)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        self._canvas = tk.Canvas(body, background="white",
                                 highlightthickness=1,
                                 highlightbackground="#c8d0da")
        body.add(self._canvas, weight=3)

        # |H(f)| sits in the SAME window as the two fields that define it, so
        # the curve and the table can never be drawn for different
        # terminations.  It is not a PLOT_TYPES entry for exactly that reason
        # -- see the note on `response_canvas_items`.
        self._resp = tk.Canvas(body, background="white",
                               highlightthickness=1,
                               highlightbackground="#c8d0da")
        body.add(self._resp, weight=2)
        self._resp.bind("<Configure>", self._on_canvas_configure)

        txt_frame = ttk.Frame(body)
        self._text = tk.Text(txt_frame, wrap=tk.NONE, font=TM_FONT, height=10)
        ysb = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL,
                            command=self._text.yview)
        xsb = ttk.Scrollbar(txt_frame, orient=tk.HORIZONTAL,
                            command=self._text.xview)
        self._text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        txt_frame.rowconfigure(0, weight=1)
        txt_frame.columnconfigure(0, weight=1)
        body.add(txt_frame, weight=2)
        self._text.tag_configure("warn", foreground=WARN_FG)

        _LIVE.setdefault(app, []).append(self)
        self.bind("<Destroy>", self._on_destroy)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._render()
        self.lift()

    # ---------------------------------------------------------------- render

    def _on_canvas_configure(self, _event=None) -> None:
        self._draw()
        self._draw_response()

    def _terminations(self) -> "tuple[float, float]":
        """
        The two fields, as (ohms, farads).  A bad entry falls back to the
        default and SAYS so rather than refusing to redraw: this is a what-if
        knob, and a window that goes blank while you are mid-keystroke is
        worse than one that shows the default.
        """
        try:
            src = float(self._src_var.get())
            if not math.isfinite(src) or src < 0.0:
                raise ValueError
        except Exception:                                   # noqa: BLE001
            src = DEFAULT_SRC_OHM
        try:
            load = float(self._load_var.get()) * 1e-15
            if not math.isfinite(load) or load < 0.0:
                raise ValueError
        except Exception:                                   # noqa: BLE001
            load = DEFAULT_LOAD_FF * 1e-15
        return (src, load)

    def _on_terminations_changed(self, _event=None) -> None:
        """Recompute the bandwidth block and the curve. No re-solve."""
        src, load = self._terminations()
        loads = tuple(sorted(set(tmod.DEFAULT_LOADS_F) | {load}))
        try:
            table = tuple(tmod.bandwidth_table(
                self._res.freqs, self._res.Z2,
                self._res.model.requested_hz, src, loads))
        except Exception:                                   # noqa: BLE001
            return
        self._res = replace(self._res, bw_table=table)
        self._render_text()
        self._draw_response()

    def _draw_response(self) -> None:
        """The |H(f)| curves, one per row of the sensitivity table."""
        cv = self._resp
        cv.delete("all")
        res = self._res
        if res.freqs is None or len(res.freqs) < 2 or not res.bw_table:
            return
        w = max(cv.winfo_width(), TM_MIN_W - 40)
        h = max(cv.winfo_height(), 120)
        fs = max(7, min(9, int(round(h / 18))))

        curves = []
        for bw in res.bw_table:
            H = tmod.transfer_function(res.Z2, res.freqs, bw.z_src_ohm,
                                       bw.c_load_farad)
            mag = np.abs(H)
            ref = mag[0] if (len(mag) and np.isfinite(mag[0]) and mag[0] > 0) \
                else 1.0
            with np.errstate(divide="ignore", invalid="ignore"):
                db = 20.0 * np.log10(mag / ref)
            label = (f"{bw.c_load_farad * 1e15:g} fF"
                     if bw.c_load_farad else "open")
            curves.append((label, db))

        for it in response_canvas_items(res.freqs, curves, w, h,
                                        res.model.freq_hz):
            if it.kind == "line":
                if it.role == "grid":
                    cv.create_line(*it.coords, fill=_GRID_FG, dash=(4, 3))
                elif it.role == "marker":
                    cv.create_line(*it.coords, fill=_MARKER_FG, dash=(2, 3))
                elif it.role.startswith("curve"):
                    cv.create_line(*it.coords, width=2,
                                   fill=_CURVE_FG[int(it.role[-1]) % 4])
                else:
                    cv.create_line(*it.coords, fill=_AXIS_FG)
            elif it.kind == "text":
                if it.role.startswith("curve"):
                    fill = _CURVE_FG[int(it.role[-1]) % 4]
                elif it.role == "note":
                    fill = _MARKER_FG
                else:
                    fill = _AXIS_FG
                cv.create_text(it.coords[0], it.coords[1], text=it.text,
                               fill=fill, font=(TM_FONT[0], fs),
                               anchor=it.anchor)

    def _draw(self) -> None:
        """Hand every geometry item to the canvas.  No layout decisions here."""
        cv = self._canvas
        cv.delete("all")
        w = max(cv.winfo_width(), TM_MIN_W - 40)
        h = max(cv.winfo_height(), 240)
        # Keep the drawing's aspect so a very wide window does not stretch the
        # legs into a letterbox; centre what is left over.
        scale = min(w / CANVAS_W, h / CANVAS_H)
        dw, dh = CANVAS_W * scale, CANVAS_H * scale
        ox, oy = (w - dw) / 2.0, (h - dh) / 2.0
        fs = max(7, int(round(9 * scale)))
        mono = (TM_FONT[0], fs)

        for it in pi_canvas_items(self._res.model, dw, dh):
            c = [v + (ox if k % 2 == 0 else oy) for k, v in enumerate(it.coords)]
            if it.kind == "line":
                cv.create_line(*c, fill=_GND_FG if it.role == "gnd" else _WIRE_FG,
                               width=2 if it.role == "wire" else 2)
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

    def _render(self) -> None:
        self._render_text()
        self.refresh_banner()
        self._draw()
        self._draw_response()

    def _render_text(self) -> None:
        self._header.configure(text=header_text(self._res))
        res = self._res
        lines = pi_report_lines(
            res.model, freq_snap=res.freq_snap, reference=res.reference,
            mode_conversion=res.mode_conversion,
            port_note=res.port_note, drawing=False)
        top = float(res.freqs[-1]) if len(res.freqs) else float("nan")
        lines.extend(bandwidth_lines(list(res.bw_table), list(res.corners),
                                     tuple(res.model_band), top))
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", "\n".join(lines))
        self._text.configure(state=tk.DISABLED)

    def refresh_banner(self) -> None:
        """One tuple compare, one Label write.  Never raises."""
        try:
            trace, exists = self._subject()
            text, warn = staleness_text(self._res, trace, exists)
            self._banner.configure(
                text=text, foreground=WARN_FG if warn else PLACEHOLDER_FG)
            self._recompute_btn.configure(
                state=tk.NORMAL if exists else tk.DISABLED)
        except Exception:                                   # noqa: BLE001
            pass

    def _subject(self):
        """The live trace by IDENTITY -- `in` would compare numpy arrays."""
        for tc in getattr(self._app, "traces", []) or []:
            if tc is self._trace:
                return (tc, True)
        return (self._trace, False)

    def _on_recompute(self) -> None:
        trace, exists = self._subject()
        if not exists:
            return
        try:
            self._app._flush_editor_sync()
        except Exception:                                   # noqa: BLE001
            pass
        why = trace_model_refusal(trace, self._fe)
        if why:
            messagebox.showinfo(TRACE_MODEL_TITLE, why, parent=self)
            return
        try:
            freq_hz = float(self._app.rlc_freq_var.get()) * 1e9
        except Exception:                                   # noqa: BLE001
            freq_hz = self._res.model.requested_hz
        try:
            self._res = compute_trace_model(self._app, trace, self._fe, freq_hz)
        except Exception as e:                              # noqa: BLE001
            messagebox.showinfo(TRACE_MODEL_TITLE,
                                f"Could not recompute: {e}", parent=self)
            return
        self._render()

    def _on_destroy(self, event=None) -> None:
        # A descendant's <Destroy> must not deregister the window: a Toplevel
        # is in every child's bindtags.
        if event is not None and event.widget is not self:
            return
        try:
            _LIVE[self._app] = [w for w in _LIVE.get(self._app, [])
                                if w is not self]
        except Exception:                                   # noqa: BLE001
            pass


# ============================================================================
# The routes in, and the refresh hook
# ============================================================================

def open_trace_model_window(app, trace) -> Optional[TraceModelWindow]:
    """
    Open the Trace Model window on `trace`.  ONE refusal path for every route.

    The menubar entry and the right-click entry both land here, so they cannot
    start refusing different things -- which is also why neither of them is
    ever greyed out: this window explains its own five reasons, and a disabled
    menu entry explains none of them.
    """
    try:
        app._flush_editor_sync()
    except Exception:                                       # noqa: BLE001
        pass

    fe = None
    if trace is not None:
        try:
            fe = app._file_by_label(trace.file_label)
        except Exception:                                   # noqa: BLE001
            fe = None

    why = trace_model_refusal(trace, fe)
    if why:
        messagebox.showinfo(TRACE_MODEL_TITLE, why, parent=app)
        return None

    # Raise, do not duplicate: a second window on the same trace is two copies
    # of one answer that drift apart the moment one is recomputed.
    for w in live_windows(app):
        if w._trace is trace:
            try:
                w.deiconify()
                w.lift()
                w.focus_set()
            except Exception:                               # noqa: BLE001
                pass
            return w

    try:
        freq_hz = float(app.rlc_freq_var.get()) * 1e9
    except Exception:                                       # noqa: BLE001
        freq_hz = float("nan")
    if not math.isfinite(freq_hz):
        messagebox.showinfo(TRACE_MODEL_TITLE,
                            "The marker frequency is not a number. Fix the "
                            "frequency box in the main window first.",
                            parent=app)
        return None

    try:
        res = compute_trace_model(app, trace, fe, freq_hz)
    except Exception as e:                                  # noqa: BLE001
        messagebox.showinfo(TRACE_MODEL_TITLE,
                            f"Could not read a pi from this trace: {e}",
                            parent=app)
        return None

    for w in res.model.warnings:
        try:
            app._append_result(f"  Trace model: {w}", LOG_WARN)
        except Exception:                                   # noqa: BLE001
            pass

    win = TraceModelWindow(app, trace, fe, res)
    return win


def refresh_trace_model_windows(app, rerender: bool = False) -> None:
    """
    Poke every open window's banner (or re-render it).  NEVER raises.

    It is called from the same eight sites the Attribution refresh is, and it
    has to be as unkillable as that one: a failure here would take down a
    keystroke handler, a session load or a Clear All.
    """
    try:
        wins = live_windows(app)
    except Exception:                                       # noqa: BLE001
        return
    for w in wins:
        try:
            if rerender:
                w._render()
            else:
                w.refresh_banner()
        except Exception:                                   # noqa: BLE001
            pass
