"""
pkg_rlc_plot.py  --  Matplotlib plot panel with interactive features.

Features (per spec):
  - Multi-trace overlay with 12 colors x 4 linestyles
  - Subplot grid (max 4 cols) over R, L, C, |Z|, Re(Z), Im(Z), Q, k
  - Derived quantities that cannot be computed from a single Z curve (today only
    the coupling coefficient k = M / sqrt(L_a * L_b)) are supplied per trace via
    the optional ``Trace.aux`` dict of precomputed arrays aligned with ``freqs``.
  - X / Y log toggles (Y uses symlog for sign-crossing data)
  - Draggable red-dashed freq marker line
  - One cursor readout box per subplot, which doubles as the legend: the
    frequency once in its title, one row per curve, one value column per
    cursor.  It is itself draggable, and a double-click inside it hands the
    placement back to the automatic corner scoring.
  - 'M' key  : add square marker at nearest data point
  - 'V' key  : add a gray dotted cursor -- another column in the readout
  - Delete   : remove most-recent annotation/v-line (LIFO)
  - Fullscreen: open one selected plot type in a Toplevel for detail work
"""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Optional, Sequence

import numpy as np
import matplotlib

matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.lines import Line2D                # noqa: E402
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg, NavigationToolbar2Tk,
)

# The readout must print engineering units ("300 pH", not "0.3 nH"), and the
# repo already has exactly one implementation of that rule.  Duplicating it
# here would give the plot and the results pane two ways to render the same
# number.  pkg_rlc_core imports nothing from this module, so this is acyclic.
from pkg_rlc_core import format_si                 # noqa: E402


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

# ---- cursor readout -------------------------------------------------------
# The physical unit each plot type's y values are already expressed in, as
# (unit, scale-to-SI).  R(mOhm) values are milliohms, so 1500 -> 1.5 Ohm.
READOUT_UNITS: dict[str, tuple[str, float]] = {
    "R(mOhm)": ("Ω", 1e-3),
    "L(nH)": ("H", 1e-9),
    "C(pF)": ("F", 1e-12),
    "|Z|(Ohm)": ("Ω", 1.0),
    "Re(Z)": ("Ω", 1.0),
    "Im(Z)": ("Ω", 1.0),
    "Q": ("", 1.0),
    "k": ("", 1.0),
}
READOUT_MAX_ROWS = 10          # beyond this the box would eat the subplot
READOUT_NAME_LEN = 18          # hard ceiling; the real budget is measured
READOUT_NAME_MIN = 8           # floor: below this the rows stop being distinct
READOUT_FONT = "DejaVu Sans Mono"   # values only line up in a monospace face
READOUT_FONT_SIZE = 6.5
# The box must fit the subplot it sits in, and a 4-column grid gives each axes
# ~210 px at the default window size against ~320 px at 3 columns -- so the
# name column is a measured budget, not a constant.  Both numbers below are
# in em of the readout font, which keeps them right at any DPI:
#   0.60  advance width of DejaVu Sans Mono
#   2.70  legend chrome (handlelength 1.5 + handletextpad 0.5 + 2x borderpad)
READOUT_MAX_WIDTH_FRAC = 0.62
READOUT_CHAR_EM = 0.60
READOUT_CHROME_EM = 2.70
# Corner scoring samples the curves rather than the rendered pixels: the score
# must not depend on the readout's own text, or the box hops corners while the
# user drags the marker and the value strings change width.
# The two centre slots matter more than they look: a coupling plot puts the
# self curves at the top and the mutual ones near zero, so all four CORNERS
# hold data while the middle band is empty.  They are listed last so they win
# only on a strictly lower score, not on a tie.
READOUT_CORNERS = {
    "upper right": (0.5, 1.0, 0.5, 1.0),
    "upper left": (0.0, 0.5, 0.5, 1.0),
    "lower left": (0.0, 0.5, 0.0, 0.5),
    "lower right": (0.5, 1.0, 0.0, 0.5),
    "center left": (0.0, 0.5, 0.25, 0.75),
    "center right": (0.5, 1.0, 0.25, 0.75),
}


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


def _readout_value(v: float, plot_type: str) -> str:
    """
    One cell of the cursor readout, in engineering units.

    The axis is labelled in a fixed prefix (nH, pF, mOhm) because that keeps
    the tick labels comparable; the readout is what the user actually reads a
    number off, so it picks the prefix per value: "300 pH", not "0.3 nH", and
    "1.5 Ω", not the "1.5e+03 mΩ" that a plain %.3g produced.

    Non-finite reads as "--": a self curve has no k, and a NaN there is the
    honest answer rather than a gap that shifts every row below it.
    """
    if not np.isfinite(v):
        return "--"
    unit, scale = READOUT_UNITS.get(plot_type, ("", 1.0))
    if not unit:
        return f"{v:.3g}"
    return format_si(v * scale, unit)


def _format_value(v: float, plot_type: str) -> str:
    """
    Standalone value label, used by the 'M' key annotation, which names no
    curve and so has to carry the quantity itself.
    """
    if not np.isfinite(v):
        return "nan"
    if plot_type == "Q":
        return f"Q={v:.3g}"
    if plot_type == "k":
        return f"k={v:.3g}"
    return _readout_value(v, plot_type)


def _fit_names(names: list[str], budget: int) -> list[str]:
    """
    Clip readout row names to `budget` characters, keeping the TAIL.

    'float4p_run2 : L1 x L2' has already lost its shared prefix by the time it
    gets here, but two measurement ports called 'osc_primary' and
    'osc_secondary' differ only at the end -- cutting the head is what keeps
    the rows distinguishable.
    """
    if budget <= 0:
        return list(names)
    return [n if len(n) <= budget else "…" + n[-(budget - 1):] for n in names]


def _strip_common_prefix(labels: list[str]) -> tuple[list[str], str]:
    """
    Drop the shared '<trace> : ' head from a set of curve labels.

    One mode-6 trace expands into '<trace> : L1', '<trace> : L1 x L2', ... so
    without this every readout row spends its width on the same prefix.
    Returns (short_labels, prefix); prefix is "" when they do not all share one.
    """
    if len(labels) < 2 or " : " not in labels[0]:
        return list(labels), ""
    head = labels[0].split(" : ", 1)[0]
    if not all(l.startswith(head + " : ") for l in labels):
        return list(labels), ""
    return [l.split(" : ", 1)[1] for l in labels], head


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
        self.show_readout = True
        self.marker_freq_hz: float = 1e9

        self.axes: list = []
        self._marker_lines: list = []
        self._marker_annots: list = []
        # LIFO: each entry is (kind, artists); kind "v" also owns one entry of
        # _vline_freqs, so Delete has to pop both or the readout keeps a column
        # for a line that is no longer drawn.
        self._anno_stack: list[tuple[str, list]] = []
        self._vline_freqs: list[float] = []
        # Chosen corner per axes, decided from the DATA only and frozen until
        # the next redraw -- see READOUT_CORNERS.
        self._readout_loc: dict[int, str] = {}
        # Boxes the user has dragged, keyed by plot type so the placement
        # outlives the axes.  Beats _readout_loc; double-click clears it.
        self._readout_manual: dict[str, tuple] = {}
        self._dragging = False

        # Cache axes-type pairs for hit-testing
        self._axes_types: list[tuple] = []

        self.canvas.mpl_connect("key_press_event", self._on_key)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)

    # -------- Public API --------

    def set_traces(self, traces: list[Trace],
                   keep_cursors: bool = False) -> None:
        """
        Replace the curves.  ``keep_cursors`` re-places the V lines afterwards.

        Every V line is a cursor the user placed deliberately and is reading
        values off, so a call that only changes WHICH curves are drawn must not
        throw them away -- toggling a trace's visibility would otherwise wipe
        the comparison the user set the toggle up to make.  Calculate still
        clears them (its numbers are new), which is why this is opt-in.

        M markers are NOT restored: each one is anchored to one data point of
        one trace, and after a visibility toggle that trace may not be drawn.
        """
        keep = list(self._vline_freqs) if keep_cursors else []
        self.traces = list(traces)
        self._anno_stack = []
        self._vline_freqs = []
        self.redraw()
        if keep:
            for f in keep:
                self._place_vline(f)
            self._refresh_marker()      # once, not once per restored line
            self.canvas.draw_idle()

    def set_marker_freq(self, freq_hz: float) -> None:
        self.marker_freq_hz = float(freq_hz)
        self._refresh_marker()
        self.canvas.draw_idle()

    def redraw(self) -> None:
        self._capture_manual_locs()     # before figure.clear() destroys them
        self.figure.clear()
        self._anno_stack = []      # axes go away with figure.clear()
        self._vline_freqs = []
        self._marker_lines = []
        self._marker_annots = []
        self._readout_loc = {}
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
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        # The readout box carries a colour/linestyle swatch and the curve name,
        # so where it is drawn it IS the legend.  A separate legend would be
        # the same names twice, competing for the same empty corner.
        # _refresh_marker falls back to the plain legend when there is no
        # cursor to read -- going through it here keeps the marker line drawn
        # even with the readout switched off.
        if self.show_marker:
            self._refresh_marker()
        else:
            self._draw_plain_legend()
        self.canvas.draw_idle()

    # -------- Legend / readout --------

    def _cursor_freqs(self) -> list[float]:
        """Every vertical cursor currently on the plot, marker first."""
        if not self.axes or not self.traces or not self.show_readout:
            return []
        out = [self.marker_freq_hz] if self.show_marker else []
        out.extend(self._vline_freqs)
        return out

    def _draw_plain_legend(self) -> None:
        """Names-only legend on the first axes -- the pre-readout behaviour,
        used when there is no cursor to read values off."""
        if not self.axes or not self.traces:
            return
        handles, labels = self.axes[0].get_legend_handles_labels()
        if handles:
            self.axes[0].legend(loc="best", fontsize=7)

    def _capture_manual_locs(self) -> None:
        """
        Remember any box the user has dragged, before the legend is replaced.

        Read here rather than from a button-release handler on purpose: the
        DraggableLegend registers its own release callback when the box is
        built, i.e. AFTER _PlotView's, so ours would run first and read the
        position from before the drag.  Every path that destroys a legend
        (marker refresh, redraw) calls this first instead, which does not
        depend on callback order at all.

        A dragged legend stores its position as a 2-tuple in axes fraction;
        an auto-placed one keeps the integer code of its named location.  That
        is the discriminator.  Keyed by PLOT TYPE, not by axes id, so the
        placement survives Calculate rebuilding the axes.
        """
        for ax, plot_type in self._axes_types:
            lg = ax.get_legend()
            if lg is None:
                continue
            loc = getattr(lg, "_loc", None)
            if isinstance(loc, tuple) and len(loc) == 2:
                self._readout_manual[plot_type] = (float(loc[0]), float(loc[1]))

    def _legend_at(self, event):
        """(axes, plot_type, legend) under the pointer, or None."""
        for ax, plot_type in self._axes_types:
            lg = ax.get_legend()
            if lg is None or event.x is None or event.y is None:
                continue
            try:
                if lg.get_window_extent().contains(event.x, event.y):
                    return ax, plot_type, lg
            except Exception:
                continue
        return None

    def _pick_readout_loc(self, ax, plot_type: str):
        """
        Where the readout box goes: the user's own placement if they dragged
        it, else the corner with the least data in it, in axes fraction.

        The automatic choice is deliberately scored from the curves and not
        from the rendered legend: matplotlib's own loc="best" also weighs the
        legend's size, so a value going from "2 nH" to "2.05 nH" during a drag
        can flip the box to another corner.  A cursor readout that hops while
        you drag the cursor is worse than one sitting in a slightly busier
        corner.
        """
        manual = self._readout_manual.get(plot_type)
        if manual is not None:
            return manual
        key = id(ax)
        if key in self._readout_loc:
            return self._readout_loc[key]
        counts = {name: 0 for name in READOUT_CORNERS}
        try:
            inv = ax.transAxes.inverted()
            for tr in self.traces:
                y = trace_y_values(tr.freqs, tr.Z, plot_type, tr.aux)
                m = np.isfinite(y)
                if not m.any():
                    continue
                xs, ys = tr.freqs[m], y[m]
                if len(xs) > 200:              # scoring needs shape, not detail
                    step = len(xs) // 200 + 1
                    xs, ys = xs[::step], ys[::step]
                pts = inv.transform(ax.transData.transform(
                    np.column_stack([xs, ys])))
                for name, (x0, x1, y0, y1) in READOUT_CORNERS.items():
                    inside = ((pts[:, 0] >= x0) & (pts[:, 0] <= x1) &
                              (pts[:, 1] >= y0) & (pts[:, 1] <= y1))
                    counts[name] += int(inside.sum())
        except Exception:
            self._readout_loc[key] = "best"
            return "best"
        # Ties resolve by READOUT_CORNERS order, which is the conventional
        # preference: top-right first.
        loc = min(READOUT_CORNERS, key=lambda nm: counts[nm])
        self._readout_loc[key] = loc
        return loc

    def _name_budget(self, ax, wcol: list[int]) -> int:
        """
        How many characters the name column may use in THIS axes.

        Measured from the axes width, not fixed: a 4-column grid gives each
        subplot about a third less width than a 3-column one, and a box that
        is wider than its subplot sticks out over the neighbour -- which is
        the failure the readout exists to remove.
        """
        em_px = READOUT_FONT_SIZE * float(ax.figure.dpi) / 72.0
        try:
            avail = ax.get_window_extent().width * READOUT_MAX_WIDTH_FRAC
        except Exception:
            return READOUT_NAME_LEN
        chars = (avail - READOUT_CHROME_EM * em_px) / (READOUT_CHAR_EM * em_px)
        values = sum(wcol) + len(wcol)      # value columns plus one space each
        # READOUT_NAME_MIN is a floor, not a target: past it the rows stop
        # telling curves apart, and a box slightly wider than the budget beats
        # ten rows that all read the same.  That corner (many curves x several
        # cursors x a 4-column grid) is what the Readout toggle and Fullscreen
        # are for.
        return max(READOUT_NAME_MIN, min(READOUT_NAME_LEN, int(chars) - values))

    def _readout_rows(self, ax, plot_type: str, freqs: list[float],
                      names: list[str]) -> tuple[list, list[str], str]:
        """
        Build (handles, row texts, title) for one axes' readout box.

        One row per trace, one value column per cursor.  Columns are padded to
        a common width and drawn in a monospace face, which is the only way a
        matplotlib legend entry can hold an aligned number column.
        """
        cols: list[list[str]] = []
        for f_hz in freqs:
            col = []
            for tr in self.traces:
                if len(tr.freqs) == 0:
                    col.append("--")
                    continue
                y = trace_y_values(tr.freqs, tr.Z, plot_type, tr.aux)
                i = int(np.argmin(np.abs(tr.freqs - f_hz)))
                col.append(_readout_value(float(y[i]), plot_type))
            cols.append(col)

        shown = min(len(self.traces), READOUT_MAX_ROWS)
        heads = [format_si(f, "Hz") for f in freqs] if len(freqs) > 1 else []
        # A column is as wide as its widest cell OR its header -- sizing on the
        # values alone put a 7-char "5.1 GHz" over a 5-char column and threw
        # every heading one place to the left of the numbers it names.
        wcol = [max([len(c[r]) for r in range(shown)] + ([len(h)] if h else []))
                for c, h in zip(cols, heads or [""] * len(cols))]
        names = _fit_names(names[:shown], self._name_budget(ax, wcol))
        wname = max((len(n) for n in names), default=0)

        handles, rows = [], []
        if heads:
            # Column header, on an invisible handle so it lines up with the
            # rows through the same legend layout rather than by guesswork.
            head = " ".join(f"{h:>{w}}" for h, w in zip(heads, wcol))
            handles.append(Line2D([], [], color="none"))
            rows.append(f"{'':<{wname}} {head}")
        for r in range(shown):
            tr = self.traces[r]
            handles.append(Line2D(
                [], [], color=COLORS[tr.color_idx % len(COLORS)],
                linestyle=LINESTYLES[tr.ls_idx % len(LINESTYLES)],
                linewidth=1.2))
            cells = " ".join(f"{c[r]:>{w}}" for c, w in zip(cols, wcol))
            rows.append(f"{names[r]:<{wname}} {cells}")
        if len(self.traces) > shown:
            handles.append(Line2D([], [], color="none"))
            rows.append(f"… +{len(self.traces) - shown} more (see Results)")

        title = (f"@ {format_si(freqs[0], 'Hz')}" if len(freqs) == 1
                 else "cursors")
        return handles, rows, title

    def _draw_readout(self, ax, plot_type: str, freqs: list[float],
                      names: list[str]) -> None:
        handles, rows, title = self._readout_rows(ax, plot_type, freqs, names)
        if not rows:
            return
        lg = ax.legend(handles, rows, title=title,
                       loc=self._pick_readout_loc(ax, plot_type),
                       framealpha=0.85, fancybox=False, borderpad=0.35,
                       labelspacing=0.28, handlelength=1.5, handletextpad=0.5,
                       borderaxespad=0.4,
                       prop={"family": READOUT_FONT, "size": READOUT_FONT_SIZE},
                       title_fontsize=READOUT_FONT_SIZE)
        # Auto-placement cannot win every time -- four subplots, several
        # cursors and a curve that happens to run through the chosen corner
        # all end the same way.  update="loc" makes the dragged position a
        # plain (x, y) axes-fraction tuple, which is what loc= accepts on
        # every matplotlib the red zone might have (Legend.set_loc is 3.8+,
        # the target box is 3.7.2).  Guarded: a Figure with no canvas yet
        # cannot register the drag callbacks, and that must not stop the plot
        # from being drawn.
        try:
            lg.set_draggable(True, use_blit=False, update="loc")
        except Exception:
            pass

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
        self._capture_manual_locs()     # the legends below get replaced
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
        if not self.axes:
            return
        if self.show_marker:
            for ax, _t in self._axes_types:
                self._marker_lines.append(
                    ax.axvline(self.marker_freq_hz, color="red",
                               linestyle="--", linewidth=1.0, alpha=0.7))

        freqs = self._cursor_freqs()
        if not freqs:
            # No cursor to read: fall back to the plain names-only legend, or
            # the plot would lose its legend along with the readout.
            self._draw_plain_legend()
            return

        # Strip the shared prefix off the FULL labels: MAX_LABEL_LEN cuts the
        # head, and two measurement ports that differ only near the end
        # ('osc_primary_coil_00/01') would arrive here already identical.  The
        # readout has its own measured width budget and does not need that cap.
        names, _prefix = _strip_common_prefix(
            [(tr.label or "") for tr in self.traces])
        # Clipping to width happens per axes in _fit_names -- the budget
        # depends on how wide that subplot is.
        names = [n.replace(" x ", "×") for n in names]
        for ax, t in self._axes_types:
            # A dot per curve per cursor: the readout box says which value
            # belongs to which curve by colour, and the dot is where that
            # colour is on the plot.
            for f_hz in freqs:
                for tr in self.traces:
                    if len(tr.freqs) == 0:
                        continue
                    y_arr = trace_y_values(tr.freqs, tr.Z, t, tr.aux)
                    i = int(np.argmin(np.abs(tr.freqs - f_hz)))
                    v = float(y_arr[i])
                    if not np.isfinite(v):
                        continue
                    dot, = ax.plot(
                        [tr.freqs[i]], [v], marker="o", markersize=4,
                        color=COLORS[tr.color_idx % len(COLORS)],
                        markeredgecolor="white", markeredgewidth=0.6,
                        zorder=5)
                    self._marker_lines.append(dot)
            self._draw_readout(ax, t, freqs, names)

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
        self._anno_stack.append(("m", [m_artist, an]))
        self.canvas.draw_idle()

    def _add_v_line(self, event) -> None:
        """
        Add a persistent cursor at the clicked frequency.

        The values go into the readout box as one more column rather than as a
        label per curve: a V line has exactly the same one-cursor-many-curves
        shape as the marker, so per-curve labels stack on top of each other the
        same way -- and you can place several V lines.
        """
        ax = event.inaxes
        if ax is None or event.xdata is None:
            return
        self._place_vline(float(event.xdata))
        self._refresh_marker()      # rebuilds every box with the new column
        self.canvas.draw_idle()

    def _place_vline(self, x_freq: float) -> None:
        """Draw one V cursor and push it onto both stacks.  Does NOT refresh
        the readout -- the caller does, once, however many lines it places."""
        artists = [a.axvline(x_freq, color="gray", linestyle=":",
                             linewidth=0.8, alpha=0.7)
                   for a, _t in self._axes_types]
        self._vline_freqs.append(x_freq)
        self._anno_stack.append(("v", artists))

    def _delete_last(self) -> None:
        if not self._anno_stack:
            return
        kind, artists = self._anno_stack.pop()
        for a in artists:
            try:
                a.remove()
            except Exception:
                pass
        if kind == "v":
            if self._vline_freqs:
                self._vline_freqs.pop()
            self._refresh_marker()  # drop that cursor's readout column too
        self.canvas.draw_idle()

    def _on_press(self, event) -> None:
        if event.button != 1 or event.inaxes is None:
            return
        # The readout box wins the gesture: it is draggable itself, and a box
        # sitting over the marker line would otherwise move the cursor and the
        # box at the same time.  Double-click inside it hands the placement
        # back to the automatic corner scoring -- the way out of a box dragged
        # somewhere useless.
        hit = self._legend_at(event)
        if hit is not None:
            hit_ax, plot_type, hit_lg = hit
            if getattr(event, "dblclick", False):
                # Drop the legend BEFORE refreshing: _refresh_marker captures
                # dragged positions first, and it would read this one straight
                # back out of the artist and undo the reset.
                try:
                    hit_lg.remove()
                except Exception:
                    pass
                self._readout_manual.pop(plot_type, None)
                self._readout_loc.pop(id(hit_ax), None)
                self._refresh_marker()
                self.canvas.draw_idle()
            return
        if not self.show_marker:
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
# A control strip that wraps instead of losing its tail
# ============================================================================

def reflow_rows(widths: Sequence[int], width: int) -> list[list[int]]:
    """
    Greedy left-to-right wrap: indices into `widths`, grouped into lines.

    Pure, so the packing decision can be tested without a display.  A single
    item that is wider than the whole strip still gets a line of its own rather
    than an empty one -- there is nothing better to do with it, and returning
    an empty line would place the next item on top of it.
    """
    rows: list[list[int]] = []
    cur: list[int] = []
    cur_w = 0
    for i, w in enumerate(widths):
        if cur and cur_w + w > width:
            rows.append(cur)
            cur, cur_w = [], 0
        cur.append(i)
        cur_w += w
    if cur:
        rows.append(cur)
    return rows


class ReflowRow(ttk.Frame):
    """
    A horizontal control strip that WRAPS onto a second line when it does not
    fit, instead of letting pack silently unmap its tail.

    This exists because the plot's control row is the one panel in the
    application nothing guarded.  Measured at the declared 1040x600 minsize the
    row asked for 918 px and got 575, and pack -- which unmaps from the END --
    took 'Im(Z)', 'Q', 'k', the fullscreen-quantity combobox and the Fullscreen
    button off screen with no scrollbar and no other route to them.  'k' is the
    quantity Mode 6 exists to produce and Fullscreen is the documented escape
    hatch for a readout box too wide for a 4-subplot grid, so neither has an
    alternative.  It was not only the minsize: _clamp_to_screen opens the
    window at min(1500, screen-80), which on a 1280-logical-px laptop is 1200
    px, and Fullscreen was off screen out of the box.

    Layout is by `place`, and that is load-bearing twice over.  Place does not
    propagate, so the strip's REQUESTED width no longer carries the 918 px into
    PlotPanel and out to the PanedWindow sash; and the wrap decision reads the
    strip's IMPOSED width (fill=X from the parent) and writes only its height,
    which cannot change that width.  That makes it a fixed point rather than
    the limit cycle _apply_editor_scrollbars documents -- a layout rule that
    reads a size it can itself change flips forever and update() never returns.
    """

    def __init__(self, master, pady: int = 1, **kw):
        super().__init__(master, height=1, **kw)
        self._items: list[tuple[tk.Widget, int, bool]] = []
        self._applied: tuple = ()
        self._pady = pady
        #: The one pending `after_idle` `refresh()` may own.  Held so it can be
        #: coalesced and, more importantly, CANCELLED on destroy: an
        #: un-cancelled `after` fires against a Tcl command the widget's
        #: teardown has already deleted, and Tk prints `invalid command name
        #: "..._reflow"` to a console a double-clicked GUI does not have --
        #: noise in a test run, invisible in production.
        self._pending = None
        self.bind("<Configure>", lambda _e: self._reflow())
        self.bind("<Destroy>", self._cancel_refresh)

    def add(self, widget, padx: int = 2, fill_y: bool = False):
        """Append a control.  `fill_y` is for vertical separators."""
        self._items.append((widget, padx, fill_y))
        self._reflow()
        return widget

    def item_widths(self) -> list[int]:
        return [w.winfo_reqwidth() + 2 * padx for w, padx, _f in self._items]

    def refresh(self) -> None:
        """
        Re-lay the strip after a CHILD's requested size changed.

        `_reflow` runs from `add()` and from this strip's own `<Configure>`,
        and a child whose TEXT grew fires neither -- `place` then goes on
        forcing the stale width and the child is CLIPPED, with no ellipsis and
        no overflow marker.  Measured in the Attribution window, whose header
        is a ReflowRow carrying a label built from the trace name: relabelling
        the trace to the documented 18-character cap took that item's request
        from 220 px to 307 while place kept it at 220, i.e. 87 px / 14
        characters cut in silence, and the strip went on reporting one row
        (29 px) while its items asked 1048 px of 964.  A 1 px window resize
        fixed both, which is what makes it a missing notification rather than
        a layout bug.

        Deferred to `after_idle` because the child's own geometry request has
        not been recomputed at the moment its text is set.  It reads the
        strip's IMPOSED width and writes only its height, so it is the same
        fixed point `_reflow` is and cannot become the limit cycle
        `_apply_editor_scrollbars` documents.  Coalesced, because a repaint
        that sets three labels must still cost one relayout.
        """
        if self._pending is not None:
            return
        try:
            self._pending = self.after_idle(self._refresh_now)
        except Exception:                       # pragma: no cover
            self._pending = None                # a dead widget cannot reflow

    def _refresh_now(self) -> None:
        self._pending = None
        if self.winfo_exists():
            self._reflow()

    def _cancel_refresh(self, event=None) -> None:
        # <Destroy> reaches every descendant too, so only the strip's own event
        # may cancel -- the same guard `AttributionWindow._on_destroy` carries.
        if event is not None and event.widget is not self:
            return
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except Exception:                   # pragma: no cover
                pass
            self._pending = None

    def _reflow(self) -> None:
        if not self._items:
            return
        width = self.winfo_width()
        if width <= 1:
            # Not laid out yet.  The <Configure> that gives it a real width
            # will call back; doing nothing here is what keeps the first pass
            # from wrapping every item onto its own line.
            return
        rows = reflow_rows(self.item_widths(), width)
        row_h = max(w.winfo_reqheight()
                    for w, _p, _f in self._items) + 2 * self._pady
        key = (tuple(tuple(r) for r in rows), row_h)
        if key == self._applied:
            return
        self._applied = key
        y = 0
        for row in rows:
            x = 0
            for i in row:
                w, padx, fill_y = self._items[i]
                ww = w.winfo_reqwidth()
                x += padx
                if fill_y:
                    w.place(x=x, y=y + self._pady, width=ww,
                            height=row_h - 2 * self._pady)
                else:
                    wh = w.winfo_reqheight()
                    w.place(x=x, y=y + max(0, (row_h - wh) // 2),
                            width=ww, height=wh)
                x += ww + padx
            y += row_h
        self.configure(height=row_h * len(rows))


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
        self.view.show_readout = self.show_readout_var.get()
        self.view.redraw()

    # -------- Public API (used by GUI) --------

    def set_traces(self, traces: list[Trace],
                   keep_cursors: bool = False) -> None:
        self.view.set_traces(traces, keep_cursors=keep_cursors)

    def set_marker_freq(self, freq_hz: float) -> None:
        self.view.set_marker_freq(freq_hz)

    def view_state(self) -> dict:
        """
        How the plot is being LOOKED AT -- axes scales, cursor, which
        quantities are on screen.  Not the data, and not the cursors the user
        placed (an M marker is anchored to one data point of one trace, which a
        saved session has not computed yet).

        Exists so the session file can restore it: the checkbox row is part of
        the setup a user rebuilds every morning, and it costs six values.
        """
        return {
            "x_log": bool(self.x_log_var.get()),
            "y_log": bool(self.y_log_var.get()),
            "marker": bool(self.show_marker_var.get()),
            "readout": bool(self.show_readout_var.get()),
            "types": self._active_types(),
            "marker_freq_hz": float(self.view.marker_freq_hz),
        }

    def set_view_state(self, state: dict) -> None:
        """
        Apply what `view_state` returned.  Missing or malformed keys keep the
        current value -- a session file is user-editable text, and a typo in it
        must not leave the plot in a state with no checkbox to get out of.

        Setting a Checkbutton's variable does NOT fire its `command`, so the
        mirrored fields on the view are written here explicitly and the redraw
        happens once at the end rather than four times.
        """
        if not isinstance(state, dict):
            return
        for key, var in (("x_log", self.x_log_var), ("y_log", self.y_log_var),
                         ("marker", self.show_marker_var),
                         ("readout", self.show_readout_var)):
            if isinstance(state.get(key), bool):
                var.set(state[key])
        types = state.get("types")
        # `any` guard, not just `isinstance`: an empty or entirely unknown list
        # would clear every checkbox, and _on_types_changed is not in the loop
        # here to force one back on.
        if isinstance(types, list) and any(t in self.type_vars for t in types):
            for t, var in self.type_vars.items():
                var.set(t in types)
        freq = state.get("marker_freq_hz")
        if isinstance(freq, (int, float)) and not isinstance(freq, bool) \
                and math.isfinite(freq) and freq > 0:
            self.view.marker_freq_hz = float(freq)
        self.view.x_log = self.x_log_var.get()
        self.view.y_log = self.y_log_var.get()
        self.view.show_marker = self.show_marker_var.get()
        self.view.show_readout = self.show_readout_var.get()
        self.view.redraw()

    # -------- UI construction --------

    def _build_ui(self) -> None:
        ctrl = ReflowRow(self)
        ctrl.pack(side=tk.TOP, fill=tk.X, pady=(2, 2))
        self.ctrl = ctrl

        self.x_log_var = tk.BooleanVar(value=True)
        self.y_log_var = tk.BooleanVar(value=False)
        self.show_marker_var = tk.BooleanVar(value=True)
        self.show_readout_var = tk.BooleanVar(value=True)

        ctrl.add(ttk.Checkbutton(ctrl, text="X log", variable=self.x_log_var,
                                 command=self._on_log_changed))
        ctrl.add(ttk.Checkbutton(ctrl, text="Y log", variable=self.y_log_var,
                                 command=self._on_log_changed))
        ctrl.add(ttk.Checkbutton(ctrl, text="Marker",
                                 variable=self.show_marker_var,
                                 command=self._on_marker_show_changed), padx=6)
        ctrl.add(ttk.Checkbutton(ctrl, text="Readout",
                                 variable=self.show_readout_var,
                                 command=self._on_readout_changed))

        ctrl.add(ttk.Separator(ctrl, orient=tk.VERTICAL), padx=4, fill_y=True)

        self.type_vars: dict[str, tk.BooleanVar] = {}
        for t in PLOT_TYPES:
            v = tk.BooleanVar(value=False)
            self.type_vars[t] = v
            ctrl.add(ttk.Checkbutton(ctrl, text=t, variable=v,
                                     command=self._on_types_changed), padx=1)

        ctrl.add(ttk.Separator(ctrl, orient=tk.VERTICAL), padx=4, fill_y=True)

        self._fs_type_var = tk.StringVar(value="|Z|(Ohm)")
        ctrl.add(ttk.Combobox(ctrl, textvariable=self._fs_type_var,
                              values=PLOT_TYPES, width=10, state="readonly"))
        ctrl.add(ttk.Button(ctrl, text="Fullscreen",
                            command=self._open_fullscreen))

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

    def _on_readout_changed(self) -> None:
        self.view.show_readout = self.show_readout_var.get()
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
            show_readout=self.view.show_readout,
        )


# ============================================================================
# Fullscreen plot window (single subplot)
# ============================================================================

class FullscreenPlotWindow(tk.Toplevel):
    def __init__(self, master, plot_type: str, traces: list[Trace],
                 marker_freq_hz: float, x_log: bool, y_log: bool,
                 show_marker: bool, show_readout: bool = True):
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
        self.view.show_readout = show_readout
        self.view.marker_freq_hz = marker_freq_hz
        self.view.set_traces(traces)
        widget = self.canvas.get_tk_widget()
        widget.focus_set()
        widget.bind("<Enter>", lambda e: widget.focus_set())
