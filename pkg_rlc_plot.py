"""
pkg_rlc_plot.py  --  Matplotlib plot panel with interactive features.

Features (per spec):
  - Multi-trace overlay with 12 colors x 4 linestyles
  - Subplot grid (max 4 cols) over R, L, C, |Z|, Re(Z), Im(Z), Q, k
  - Derived quantities that cannot be computed from a single Z curve (today only
    the coupling coefficient k = M / sqrt(L_a * L_b)) are supplied per trace via
    the optional ``Trace.aux`` dict of precomputed arrays aligned with ``freqs``.
  - X / Y log toggles (Y uses symlog for sign-crossing data)
  - Draggable red-dashed freq marker line with intersection annotations
  - 'M' key  : add square marker at nearest data point
  - 'V' key  : add gray dotted vertical line + diamond markers on all traces
  - Delete   : remove most-recent annotation/v-line (LIFO)
  - Fullscreen: open one selected plot type in a Toplevel for detail work
"""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Optional

import numpy as np
import matplotlib

matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg, NavigationToolbar2Tk,
)


# ============================================================================
# Constants
# ============================================================================

PLOT_TYPES = ["R(mOhm)", "L(nH)", "C(pF)", "|Z|(Ohm)", "Re(Z)", "Im(Z)", "Q", "k"]

# Plot types that cannot be derived from a single (freqs, Z) pair and must be
# fed through Trace.aux instead.  Traces without the matching aux entry draw
# nothing on that subplot rather than raising.
AUX_PLOT_TYPES: tuple[str, ...] = ("k",)

COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#1a5e1a", "#5e2a1a",
]
LINESTYLES = ["-", "--", "-.", ":"]
MAX_LABEL_LEN = 30
MARKER_PIXEL_TOLERANCE = 8


# ============================================================================
# Trace dataclass
# ============================================================================

@dataclass
class Trace:
    label: str
    freqs: np.ndarray         # Hz, shape (nfreqs,)
    Z: np.ndarray             # complex, shape (nfreqs,)
    color_idx: int = 0
    ls_idx: int = 0
    fit_freqs: Optional[np.ndarray] = None    # optional fit overlay
    fit_Z: Optional[np.ndarray] = None
    # Precomputed derived quantities that are not a function of Z alone,
    # e.g. {"k": <float array aligned with freqs>} for a mutual-coupling
    # trace.  Missing / absent entries simply plot as NaN.
    aux: Optional[dict] = None


# ============================================================================
# Plot-type to y-axis values
# ============================================================================

def _aux_series(aux: Optional[dict], key: str, n: int) -> np.ndarray:
    """
    Fetch ``aux[key]`` as a float array of length ``n``.

    Returns an all-NaN array when there is no aux dict, no such key, or the
    stored array does not line up with the frequency axis.  That way a plain
    self-impedance trace draws nothing on an aux subplot instead of raising.
    """
    nan = np.full(n, np.nan, dtype=float)
    if not aux:
        return nan
    v = aux.get(key)
    if v is None:
        return nan
    arr = np.asarray(v, dtype=float)
    if arr.ndim != 1 or arr.shape[0] != n:
        return nan
    return arr


def trace_y_values(freqs: np.ndarray, Z: np.ndarray, plot_type: str,
                   aux: Optional[dict] = None) -> np.ndarray:
    """
    Map a trace onto the y values for ``plot_type``.

    ``aux`` is optional so every existing positional call site keeps working.
    It carries precomputed series for the plot types listed in
    ``AUX_PLOT_TYPES`` (currently just "k", the coupling coefficient, which
    needs three curves at once and so cannot be derived from ``Z``).

    Values keep their physical sign -- nothing here clips or takes abs().
    """
    if plot_type == "k":
        return _aux_series(aux, "k", len(freqs))
    omega = 2.0 * np.pi * freqs
    with np.errstate(divide="ignore", invalid="ignore"):
        if plot_type == "R(mOhm)":
            return Z.real * 1000.0
        if plot_type == "L(nH)":
            return Z.imag / omega * 1e9
        if plot_type == "C(pF)":
            return -1.0 / (omega * Z.imag) * 1e12
        if plot_type == "|Z|(Ohm)":
            return np.abs(Z)
        if plot_type == "Re(Z)":
            return Z.real
        if plot_type == "Im(Z)":
            return Z.imag
        if plot_type == "Q":
            return Z.imag / Z.real
    raise ValueError(f"Unknown plot type: {plot_type}")


def _format_value(v: float, plot_type: str) -> str:
    if not np.isfinite(v):
        return "nan"
    if plot_type in ("R(mOhm)",):
        return f"{v:.3g} mΩ"
    if plot_type == "L(nH)":
        return f"{v:.3g} nH"
    if plot_type == "C(pF)":
        return f"{v:.3g} pF"
    if plot_type in ("|Z|(Ohm)", "Re(Z)", "Im(Z)"):
        return f"{v:.3g} Ω"
    if plot_type == "Q":
        return f"Q={v:.3g}"
    if plot_type == "k":
        return f"k={v:.3g}"
    return f"{v:.3g}"


# ============================================================================
# Interactive plot view (shared by main panel and fullscreen)
# ============================================================================

class _PlotView:
    """
    Encapsulates a Figure with axes, traces, and event handlers.
    Reused by PlotPanel (multi-subplot grid) and FullscreenPlotWindow
    (single subplot).
    """

    def __init__(self, figure: Figure, canvas: FigureCanvasTkAgg,
                 get_active_types: Callable[[], list[str]],
                 on_marker_changed: Optional[Callable[[float], None]] = None):
        self.figure = figure
        self.canvas = canvas
        self.get_active_types = get_active_types
        self.on_marker_changed = on_marker_changed or (lambda f: None)

        self.traces: list[Trace] = []
        self.x_log = True
        self.y_log = False
        self.show_marker = True
        self.marker_freq_hz: float = 1e9

        self.axes: list = []
        self._marker_lines: list = []
        self._marker_annots: list = []
        self._anno_stack: list[list] = []  # LIFO: each entry is a list of artists
        self._dragging = False

        # Cache axes-type pairs for hit-testing
        self._axes_types: list[tuple] = []

        self.canvas.mpl_connect("key_press_event", self._on_key)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)

    # -------- Public API --------

    def set_traces(self, traces: list[Trace]) -> None:
        self.traces = list(traces)
        self._anno_stack = []
        self.redraw()

    def set_marker_freq(self, freq_hz: float) -> None:
        self.marker_freq_hz = float(freq_hz)
        self._refresh_marker()
        self.canvas.draw_idle()

    def redraw(self) -> None:
        self.figure.clear()
        self._anno_stack = []      # axes go away with figure.clear()
        self._marker_lines = []
        self._marker_annots = []
        active = self.get_active_types()
        if not active:
            self.canvas.draw_idle()
            return
        n = len(active)
        ncols = min(n, 4)
        nrows = (n + ncols - 1) // ncols
        self.axes = []
        self._axes_types = []
        for i, t in enumerate(active):
            ax = self.figure.add_subplot(nrows, ncols, i + 1)
            self.axes.append(ax)
            self._axes_types.append((ax, t))
            self._draw_axes(ax, t)
        # Single legend on first axes
        if self.axes and self.traces:
            first = self.axes[0]
            handles, labels = first.get_legend_handles_labels()
            if handles:
                first.legend(loc="best", fontsize=7)
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        if self.show_marker:
            self._refresh_marker()
        self.canvas.draw_idle()

    # -------- Drawing helpers --------

    def _draw_axes(self, ax, plot_type: str) -> None:
        ax.set_title(plot_type, fontsize=10)
        ax.set_xlabel("Freq (Hz)", fontsize=8)
        ax.set_ylabel(plot_type, fontsize=8)
        ax.tick_params(labelsize=7)
        if self.x_log:
            ax.set_xscale("log")
        if self.y_log:
            ax.set_yscale("symlog", linthresh=1e-6)
        for tr in self.traces:
            y = trace_y_values(tr.freqs, tr.Z, plot_type, tr.aux)
            color = COLORS[tr.color_idx % len(COLORS)]
            ls = LINESTYLES[tr.ls_idx % len(LINESTYLES)]
            label = (tr.label or "")[:MAX_LABEL_LEN]
            ax.plot(tr.freqs, y, color=color, linestyle=ls, label=label, linewidth=1.2)
            if tr.fit_freqs is not None and tr.fit_Z is not None:
                # aux is aligned with tr.freqs, not the fit grid, so the fit
                # overlay has no aux series -- it simply skips aux subplots.
                yf = trace_y_values(tr.fit_freqs, tr.fit_Z, plot_type)
                ax.plot(tr.fit_freqs, yf, color=color, linestyle=":",
                        linewidth=1.0, alpha=0.7)
        ax.grid(True, which="both", alpha=0.3)

    def _refresh_marker(self) -> None:
        for ln in self._marker_lines:
            try:
                ln.remove()
            except Exception:
                pass
        for an in self._marker_annots:
            try:
                an.remove()
            except Exception:
                pass
        self._marker_lines = []
        self._marker_annots = []
        if not self.show_marker or not self.axes:
            return
        for ax, t in self._axes_types:
            ln = ax.axvline(self.marker_freq_hz, color="red", linestyle="--",
                            linewidth=1.0, alpha=0.7)
            self._marker_lines.append(ln)
            for tr in self.traces:
                y_arr = trace_y_values(tr.freqs, tr.Z, t, tr.aux)
                if len(tr.freqs) == 0:
                    continue
                idx = int(np.argmin(np.abs(tr.freqs - self.marker_freq_hz)))
                v = float(y_arr[idx])
                if not np.isfinite(v):
                    continue
                color = COLORS[tr.color_idx % len(COLORS)]
                txt = f"{tr.freqs[idx]/1e9:.4g}G\n{_format_value(v, t)}"
                an = ax.annotate(
                    txt, xy=(tr.freqs[idx], v),
                    xytext=(6, 4), textcoords="offset points",
                    fontsize=7, color=color,
                )
                self._marker_annots.append(an)

    # -------- Event handlers --------

    def _axes_type_for(self, ax) -> Optional[str]:
        for a, t in self._axes_types:
            if a is ax:
                return t
        return None

    def _on_key(self, event) -> None:
        if event.key is None:
            return
        key = event.key.lower()
        if key == "m":
            self._add_m_marker(event)
        elif key == "v":
            self._add_v_line(event)
        elif key == "delete":
            self._delete_last()

    def _add_m_marker(self, event) -> None:
        ax = event.inaxes
        if ax is None:
            return
        t = self._axes_type_for(ax)
        if t is None or not self.traces:
            return
        # Find nearest data point across all traces in *display* coords
        best = None  # (dist, tr, idx, x_data, y_data)
        for tr in self.traces:
            y_arr = trace_y_values(tr.freqs, tr.Z, t, tr.aux)
            mask = np.isfinite(y_arr)
            if not mask.any():
                continue
            xs = tr.freqs[mask]
            ys = y_arr[mask]
            xy_disp = ax.transData.transform(np.column_stack([xs, ys]))
            mouse = np.array([event.x, event.y])
            d = np.hypot(xy_disp[:, 0] - mouse[0], xy_disp[:, 1] - mouse[1])
            j = int(np.argmin(d))
            if best is None or d[j] < best[0]:
                best = (d[j], tr, j, float(xs[j]), float(ys[j]))
        if best is None:
            return
        _, tr, _, xd, yd = best
        color = COLORS[tr.color_idx % len(COLORS)]
        m_artist, = ax.plot([xd], [yd], marker="s", markersize=8,
                            color=color, markerfacecolor="none", markeredgewidth=1.5)
        an = ax.annotate(
            f"{xd/1e9:.4g} GHz\n{_format_value(yd, t)}",
            xy=(xd, yd), xytext=(20, 20), textcoords="offset points",
            fontsize=8, color=color,
            arrowprops=dict(arrowstyle="->", color=color, alpha=0.6),
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.8,
                      edgecolor=color),
        )
        self._anno_stack.append([m_artist, an])
        self.canvas.draw_idle()

    def _add_v_line(self, event) -> None:
        ax = event.inaxes
        if ax is None or event.xdata is None:
            return
        x_freq = float(event.xdata)
        artists = []
        for a, t in self._axes_types:
            ln = a.axvline(x_freq, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
            artists.append(ln)
            for tr in self.traces:
                if len(tr.freqs) == 0:
                    continue
                y_arr = trace_y_values(tr.freqs, tr.Z, t, tr.aux)
                idx = int(np.argmin(np.abs(tr.freqs - x_freq)))
                yv = float(y_arr[idx])
                if not np.isfinite(yv):
                    continue
                color = COLORS[tr.color_idx % len(COLORS)]
                m, = a.plot([tr.freqs[idx]], [yv], marker="D", markersize=6,
                            color=color, markerfacecolor="none", markeredgewidth=1.2)
                artists.append(m)
                an = a.annotate(
                    f"{tr.freqs[idx]/1e9:.4g}G\n{_format_value(yv, t)}",
                    xy=(tr.freqs[idx], yv), xytext=(5, 5), textcoords="offset points",
                    fontsize=7, color=color,
                )
                artists.append(an)
        self._anno_stack.append(artists)
        self.canvas.draw_idle()

    def _delete_last(self) -> None:
        if not self._anno_stack:
            return
        artists = self._anno_stack.pop()
        for a in artists:
            try:
                a.remove()
            except Exception:
                pass
        self.canvas.draw_idle()

    def _on_press(self, event) -> None:
        if event.button != 1 or event.inaxes is None or not self.show_marker:
            return
        # Hit-test against this axes' marker line
        ax = event.inaxes
        try:
            marker_x, _ = ax.transData.transform((self.marker_freq_hz,
                                                  ax.get_ylim()[0]))
        except Exception:
            return
        if event.x is None:
            return
        if abs(event.x - marker_x) < MARKER_PIXEL_TOLERANCE:
            self._dragging = True

    def _on_motion(self, event) -> None:
        if not self._dragging or event.inaxes is None or event.xdata is None:
            return
        new_freq = float(event.xdata)
        if new_freq <= 0:
            return
        self.marker_freq_hz = new_freq
        self._refresh_marker()
        self.canvas.draw_idle()

    def _on_release(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.on_marker_changed(self.marker_freq_hz)


# ============================================================================
# Main plot panel (multi-subplot grid)
# ============================================================================

class PlotPanel(tk.Frame):
    def __init__(self, master, on_marker_changed: Callable[[float], None] = None,
                 **kw):
        super().__init__(master, **kw)
        self._on_marker_changed_cb = on_marker_changed or (lambda f: None)
        self._build_ui()
        # Initial active types (first three)
        for t in PLOT_TYPES[:3]:
            self.type_vars[t].set(True)
        # Construct view
        self.view = _PlotView(
            figure=self.figure, canvas=self.canvas,
            get_active_types=self._active_types,
            on_marker_changed=self._on_marker_changed,
        )
        self.view.x_log = self.x_log_var.get()
        self.view.y_log = self.y_log_var.get()
        self.view.show_marker = self.show_marker_var.get()
        self.view.redraw()

    # -------- Public API (used by GUI) --------

    def set_traces(self, traces: list[Trace]) -> None:
        self.view.set_traces(traces)

    def set_marker_freq(self, freq_hz: float) -> None:
        self.view.set_marker_freq(freq_hz)

    # -------- UI construction --------

    def _build_ui(self) -> None:
        ctrl = ttk.Frame(self)
        ctrl.pack(side=tk.TOP, fill=tk.X, pady=(2, 2))

        self.x_log_var = tk.BooleanVar(value=True)
        self.y_log_var = tk.BooleanVar(value=False)
        self.show_marker_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(ctrl, text="X log", variable=self.x_log_var,
                        command=self._on_log_changed).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(ctrl, text="Y log", variable=self.y_log_var,
                        command=self._on_log_changed).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(ctrl, text="Marker", variable=self.show_marker_var,
                        command=self._on_marker_show_changed).pack(side=tk.LEFT, padx=6)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self.type_vars: dict[str, tk.BooleanVar] = {}
        for t in PLOT_TYPES:
            v = tk.BooleanVar(value=False)
            self.type_vars[t] = v
            ttk.Checkbutton(ctrl, text=t, variable=v,
                            command=self._on_types_changed).pack(side=tk.LEFT, padx=1)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self._fs_type_var = tk.StringVar(value="|Z|(Ohm)")
        ttk.Combobox(ctrl, textvariable=self._fs_type_var, values=PLOT_TYPES,
                     width=10, state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="Fullscreen",
                   command=self._open_fullscreen).pack(side=tk.LEFT, padx=2)

        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Make canvas focusable for key events
        widget = self.canvas.get_tk_widget()
        widget.focus_set()
        widget.bind("<Enter>", lambda e: widget.focus_set())

    def _active_types(self) -> list[str]:
        out = [t for t in PLOT_TYPES if self.type_vars[t].get()]
        return out

    def _on_types_changed(self) -> None:
        if not self._active_types():
            # keep at least one
            self.type_vars["R(mOhm)"].set(True)
        self.view.redraw()

    def _on_log_changed(self) -> None:
        self.view.x_log = self.x_log_var.get()
        self.view.y_log = self.y_log_var.get()
        self.view.redraw()

    def _on_marker_show_changed(self) -> None:
        self.view.show_marker = self.show_marker_var.get()
        self.view.redraw()

    def _on_marker_changed(self, freq_hz: float) -> None:
        # Bubble to the GUI controller (e.g., update RLC freq entry)
        self._on_marker_changed_cb(freq_hz)

    def _open_fullscreen(self) -> None:
        FullscreenPlotWindow(
            master=self.winfo_toplevel(),
            plot_type=self._fs_type_var.get(),
            traces=self.view.traces,
            marker_freq_hz=self.view.marker_freq_hz,
            x_log=self.view.x_log,
            y_log=self.view.y_log,
            show_marker=self.view.show_marker,
        )


# ============================================================================
# Fullscreen plot window (single subplot)
# ============================================================================

class FullscreenPlotWindow(tk.Toplevel):
    def __init__(self, master, plot_type: str, traces: list[Trace],
                 marker_freq_hz: float, x_log: bool, y_log: bool,
                 show_marker: bool):
        super().__init__(master)
        self.title(f"Fullscreen: {plot_type}")
        self.geometry("1200x700")
        self._plot_type = plot_type
        self.figure = Figure(figsize=(11, 6))
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.view = _PlotView(
            figure=self.figure, canvas=self.canvas,
            get_active_types=lambda: [self._plot_type],
        )
        self.view.x_log = x_log
        self.view.y_log = y_log
        self.view.show_marker = show_marker
        self.view.marker_freq_hz = marker_freq_hz
        self.view.set_traces(traces)
        widget = self.canvas.get_tk_widget()
        widget.focus_set()
        widget.bind("<Enter>", lambda e: widget.focus_set())
