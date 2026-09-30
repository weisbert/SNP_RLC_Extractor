"""
pkg_rlc/panels/compare_gui.py  --  the Compare Files window.

"I have the inductor extracted to 30 GHz and again to 80 GHz: inside 0-30 GHz,
is the 80 GHz one the same?"  The window answers it at the two levels
`pkg_rlc.physics.similarity` computes, over the band the two files share:

  * the FILE -- the largest |S_B - S_A| over every entry, in dB;
  * what was EXTRACTED -- one trace's port setup applied to BOTH files, and
    the signed difference of L, Q and R, B against A.

Each has a limit the reader can edit, a verdict against it, and the curve the
verdict was read off, so a near-miss at one frequency is visible as exactly
that.  The limits are defaults, not a rule: how close is close enough is the
design margin's call.

Modeless, not `transient`, no `<Escape>` -- the Attribution window's reasons.
Reached from Analyze -> Compare files… and from the Files list's right-click
menu.  Imports `pkg_rlc.frontend.app` NOT AT ALL: the App is passed in.

The pure half (`compare_summary_lines`, `verdict`, `trace_choices`) is
testable with no display.
"""

from __future__ import annotations

import math
import tkinter as tk
import weakref
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Optional, Sequence

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from pkg_rlc.model.trace import TraceConfig
from pkg_rlc.model.validate import trace_is_composed
from pkg_rlc.services import run
from pkg_rlc.physics import similarity as sim
from pkg_rlc.physics.core import compute_z_matrix, format_freq

__all__ = [
    "COMPARE_MENU_LABEL", "COMPARE_TITLE", "DEFAULT_SETUP",
    "CompareResult", "CompareWindow", "compare_files", "compare_summary_lines",
    "verdict", "trace_choices", "compare_refusal", "s_matrix_lines",
    "open_compare_window", "live_windows", "refresh_compare_windows",
]

COMPARE_MENU_LABEL = "Compare files…"
COMPARE_TITLE = "Compare files"
CMP_GEOMETRY = "900x720"
CMP_MIN_W = 640
CMP_MIN_H = 480
CMP_FONT = ("Consolas", 9)

#: The port-setup choice that needs no trace: what a freshly loaded file's
#: default trace is, so a user who has done nothing but load two files still
#: gets the L / Q comparison.
DEFAULT_SETUP = "port 1 to ground (default)"

SAME = "SAME"
DIFFERENT = "DIFFERENT"

_LIMIT_FG = "#b03030"
_CURVE_FG = ("#1b6ca8", "#2e8b57", "#b8860b", "#8b3a62")


# ============================================================================
# Pure
# ============================================================================

def verdict(value: float, limit: float, *, db: bool) -> str:
    """SAME / DIFFERENT / "" (nothing comparable).  A dB error is compared as
    is (lower is better); a percentage by its magnitude."""
    if not (isinstance(value, float) and math.isfinite(value)):
        return ""
    if db:
        return SAME if value <= limit else DIFFERENT
    return SAME if abs(value) <= abs(limit) else DIFFERENT


@dataclass
class PortCompare:
    name: str
    z: sim.ZCompare


@dataclass
class CompareResult:
    label_a: str
    label_b: str
    setup: str
    s: Optional[sim.SCompare] = None
    s_why: str = ""                      # why S was not compared
    ports: list = field(default_factory=list)   # [PortCompare]
    z_why: str = ""                      # why L/Q/R were not compared
    notes: list = field(default_factory=list)


def _pct(v: float) -> str:
    return "n/a" if not math.isfinite(v) else f"{v:+.3g} %"


def compare_summary_lines(res: CompareResult, s_limit_db: float,
                          l_limit_pct: float, q_limit_pct: float) -> list:
    """The text half of the window, and what a test reads."""
    out = [f"A = {res.label_a}   (reference)",
           f"B = {res.label_b}"]
    axis = res.s.axis if res.s is not None else (
        res.ports[0].z.axis if res.ports else None)
    if axis is not None:
        out.append(f"Compared over  {axis.describe()}")
    out.append("")
    verdicts = []
    if res.s is not None:
        v = verdict(res.s.worst_db, s_limit_db, db=True)
        verdicts.append(("S-parameters", v))
        out += s_matrix_lines(res.s, s_limit_db, v)
        out.append("")
    else:
        out.append(f"S-parameters   not compared -- {res.s_why}")
    out.append(f"Port setup     {res.setup}")
    if not res.ports:
        out.append(f"L / Q / R      not compared -- {res.z_why}")
    elif res.ports:
        out.append("L / Q / R  -- the port setup above, solved on both files; "
                   "percent of A's value, + means B is larger")
    for pc in res.ports:
        z = pc.z
        vl = verdict(z.l.value, l_limit_pct, db=False)
        vq = verdict(z.q.value, q_limit_pct, db=False)
        tag = f" ({pc.name})" if len(res.ports) > 1 else ""
        verdicts += [(f"L{tag}", vl), (f"Q{tag}", vq)]
        for sym, w, lim, v in (("L", z.l, l_limit_pct, vl),
                               ("Q", z.q, q_limit_pct, vq),
                               ("R", z.r, None, "")):
            at = (f"at {format_freq(w.freq)}" if math.isfinite(w.freq)
                  else "")
            lim_s = (f"limit {lim:g} %  -> {v}" if lim is not None
                     else "(no limit -- shown for reference)")
            out.append(f"  {sym}{tag:<10} worst B vs A = {_pct(w.value):>10}"
                       f"  {at:<16}  {lim_s}")
            if w.excluded:
                out.append(f"      {w.excluded} point(s) left out where A's "
                           f"{sym} is within {sim.NEAR_ZERO_FRAC:.0%} of zero "
                           f"(a relative difference means nothing there)")
    out.append("")
    bad = [name for name, v in verdicts if v == DIFFERENT]
    judged = [name for name, v in verdicts if v]
    if not judged:
        out.append("Overall: nothing could be compared.")
    elif bad:
        out.append(f"Overall: {DIFFERENT} -- over their limit: "
                   + ", ".join(bad))
    else:
        out.append(f"Overall: {SAME} within every limit over the shared band.")
    for n in res.notes + (res.s.notes if res.s is not None else []):
        out.append(f"note: {n}")
    return out


#: Above this many ports the full difference matrix is not printed -- it
#: would be wider than any pane -- and the ranked list stands in for it.
S_MATRIX_MAX_PORTS = 32
#: How many entries the ranked list names.
S_RANK_SHOWN = 8
#: Below this, an entry is the same to printing precision (identical data
#: floors at -300 dB) and reads '--' rather than a number nobody can use.
S_IDENTICAL_DB = -200.0


def _entry(i: int, j: int) -> str:
    # S(14,15), never S1415: with ten or more ports the concatenated form
    # cannot be read back -- S1415 is S(14,15), S(1,415) or S(141,5).
    return f"S({i},{j})"


def _db_pct(db: float) -> str:
    """-39.5 dB -> '1.06 %': what a dB difference means on a |S| <= 1
    scale, for a reader who does not think in dB."""
    return f"{100.0 * 10.0 ** (db / 20.0):.3g} %"


def s_matrix_lines(sc, s_limit_db: float, v: str) -> list:
    """The raw-matrix comparison: the worst entry, how many entries are over
    the limit, the largest few, and -- up to S_MATRIX_MAX_PORTS -- every entry
    as a matrix, so WHICH ports moved is on screen and not one number."""
    i, j = sc.worst_entry
    out = ["S-PARAMETERS  -- the two matrices entry by entry, "
           "|S_B(i,j) - S_A(i,j)|",
           f"  worst entry   {_entry(i, j)} = {sc.worst_db:.1f} dB "
           f"(a {_db_pct(sc.worst_db)} difference)  at "
           f"{format_freq(sc.worst_f)}"]
    ranked = sc.ranked_entries()
    over = sum(1 for _i, _j, db, _f in ranked if db > s_limit_db)
    if ranked:
        out.append(f"  limit {s_limit_db:g} dB  -> {v}   "
                   f"({over} of {len(ranked)} entries over the limit)")
        out.append("  largest differences (each entry's worst over the band):")
        shown = [e for e in ranked if e[2] > S_IDENTICAL_DB][:S_RANK_SHOWN]
        if not shown:
            out.append("    (none -- every entry is identical)")
        for ii, jj, db, f in shown:
            mark = "  over" if db > s_limit_db else ""
            out.append(f"    {_entry(ii, jj):<10} {db:7.1f} dB "
                       f"({_db_pct(db):>8})  at {format_freq(f)}{mark}")
    else:
        out.append(f"  limit {s_limit_db:g} dB  -> {v}")
    n = 0 if sc.entry_db is None else sc.entry_db.shape[0]
    if 1 < n <= S_MATRIX_MAX_PORTS:
        out.append(f"  every entry, dB (row i = port i, column j = port j; "
                   f"* = over the limit, -- = identical):")
        # Each cell is a 6-wide number plus a 1-wide '*' slot; the column
        # number sits over the NUMBER, not over the star.
        out.append("      " + "".join(f"{c:>6} " for c in range(1, n + 1)))
        for r in range(n):
            cells = []
            for c in range(n):
                db = float(sc.entry_db[r, c])
                if db <= S_IDENTICAL_DB:
                    cells.append(f"{'--':>6} ")
                else:
                    # One decimal: -39.6 printed as -40 beside a -40 dB
                    # limit, starred as over it, reads as a contradiction.
                    cells.append(f"{db:6.1f}"
                                 + ("*" if db > s_limit_db else " "))
            out.append(f"  {r + 1:>3} " + "".join(cells))
    elif n > S_MATRIX_MAX_PORTS:
        out.append(f"  ({n} ports: the full matrix is not printed -- "
                   f"the list above is ranked over all {n * n} entries)")
    return out


def pct_view_span(abs_pct, limit: float) -> tuple:
    """
    (half-height of a percentage axis, points left off it), or (None, 0) to
    leave matplotlib's autoscale alone.

    One point of a near-open port can read -4e8 % while the rest of the band
    sits inside +-2 %: autoscaled, the axis runs to 1e8 and every other point
    lies on the zero line.  The axis covers the 98th percentile (never less
    than twice the limit, so the limit lines are always on screen); what it
    leaves out is COUNTED on the plot, the worst value is in the text above.
    """
    v = np.asarray(abs_pct, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None, 0
    span = max(float(np.percentile(v, 98)) * 1.1, 2.0 * abs(limit))
    if float(v.max()) <= span:
        return None, 0
    return span, int(np.count_nonzero(v > span))


def trace_choices(traces: Sequence) -> list:
    """(display text, trace or None) for the port-setup box: the default
    first, then every single-file trace.  A composed trace is left out -- its
    port numbers name ports of several files, which neither file has alone."""
    out = [(DEFAULT_SETUP, None)]
    for tc in traces:
        if trace_is_composed(tc):
            continue
        out.append((f"[{tc.id}] {tc.label}", tc))
    return out


def compare_refusal(files: Sequence) -> Optional[str]:
    if len(files) < 2:
        return ("Load two files first (Add File...). This window compares one "
                "file against another over the band they share.")
    return None


# ============================================================================
# The computation (needs the App only to read a trace's spec)
# ============================================================================

def _extracted_z(app, tc, fe):
    """[(port name, Z(f))] for this spec on this file, on the file's grid."""
    if tc is None:
        # The App's own default trace, spelled the way it spells it -- read
        # through the same builder, so "port 1" means what it means there.
        term = run._build_termination(TraceConfig(mode=1, port_a="1"),
                                      nports=fe.ts.nports)
    else:
        term = app._build_termination(tc, nports=fe.ts.nports)
    Zmat, names, _warns = compute_z_matrix(fe.Y, fe.ts.freqs, term)
    names = list(names) or ["Z"]
    return [(names[g] if g < len(names) else f"port {g + 1}", Zmat[:, g, g])
            for g in range(Zmat.shape[1])]


def compare_files(app, fe_a, fe_b, tc=None) -> CompareResult:
    """Both levels, each failing on its own: a port-count mismatch stops the S
    comparison, not the L / Q one, and a spec that one file cannot hold stops
    L / Q, not S."""
    res = CompareResult(label_a=fe_a.label, label_b=fe_b.label,
                        setup=DEFAULT_SETUP if tc is None
                        else f"from trace [{tc.id}] {tc.label}, "
                             f"applied to both files")
    if fe_a is fe_b:
        res.notes.append("A and B are the same loaded file.")
    try:
        res.s = sim.compare_s(fe_a.ts.freqs, fe_a.ts.s, fe_a.ts.z0,
                              fe_b.ts.freqs, fe_b.ts.s, fe_b.ts.z0)
    except Exception as e:                                  # noqa: BLE001
        res.s_why = str(e)
    try:
        za = _extracted_z(app, tc, fe_a)
        zb = _extracted_z(app, tc, fe_b)
        if len(za) != len(zb):
            raise ValueError("the setup gives a different number of "
                             "measurement ports on the two files")
        for (name, a), (_n, b) in zip(za, zb):
            res.ports.append(PortCompare(
                name, sim.compare_z(fe_a.ts.freqs, a, fe_b.ts.freqs, b)))
    except Exception as e:                                  # noqa: BLE001
        res.ports = []
        res.z_why = str(e)
    return res


# ============================================================================
# The window
# ============================================================================

_LIVE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def live_windows(app) -> list:
    out = []
    for w in list(_LIVE.get(app, [])):
        try:
            if w.winfo_exists():
                out.append(w)
        except Exception:                                   # noqa: BLE001
            pass
    _LIVE[app] = out
    return out


def _float(text: str, default: float) -> float:
    try:
        v = float(str(text).strip())
        return v if math.isfinite(v) else default
    except ValueError:
        return default


class CompareWindow(tk.Toplevel):

    def __init__(self, app, fe_a, fe_b):
        super().__init__(app)
        self._app = app
        self._fe_a = fe_a
        self._fe_b = fe_b
        self._res: Optional[CompareResult] = None
        self.title(COMPARE_TITLE)
        self.geometry(CMP_GEOMETRY)
        self.minsize(CMP_MIN_W, CMP_MIN_H)

        foot = ttk.Frame(self)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        pick = ttk.Frame(self)
        pick.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))
        self.a_var = tk.StringVar()
        self.b_var = tk.StringVar()
        self.setup_var = tk.StringVar(value=DEFAULT_SETUP)
        ttk.Label(pick, text="A (reference)").grid(row=0, column=0, sticky="w")
        self.a_cbo = ttk.Combobox(pick, textvariable=self.a_var,
                                  state="readonly", width=34,
                                  postcommand=self._refresh_choices)
        self.a_cbo.grid(row=0, column=1, sticky="we", padx=(4, 12))
        ttk.Label(pick, text="B").grid(row=0, column=2, sticky="w")
        self.b_cbo = ttk.Combobox(pick, textvariable=self.b_var,
                                  state="readonly", width=34,
                                  postcommand=self._refresh_choices)
        self.b_cbo.grid(row=0, column=3, sticky="we", padx=(4, 0))
        ttk.Label(pick, text="Port setup").grid(row=1, column=0, sticky="w",
                                                pady=(4, 0))
        self.setup_cbo = ttk.Combobox(pick, textvariable=self.setup_var,
                                      state="readonly",
                                      postcommand=self._refresh_choices)
        self.setup_cbo.grid(row=1, column=1, columnspan=3, sticky="we",
                            padx=(4, 0), pady=(4, 0))
        pick.columnconfigure(1, weight=1)
        pick.columnconfigure(3, weight=1)
        for cbo in (self.a_cbo, self.b_cbo, self.setup_cbo):
            cbo.bind("<<ComboboxSelected>>", lambda _e: self._on_pick())

        lim = ttk.Frame(self)
        lim.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 4))
        ttk.Label(lim, text="Same if   |S_B - S_A| <").pack(side=tk.LEFT)
        self.s_lim_var = tk.StringVar(value=f"{sim.DEFAULT_S_LIMIT_DB:g}")
        self.l_lim_var = tk.StringVar(value=f"{sim.DEFAULT_L_LIMIT_PCT:g}")
        self.q_lim_var = tk.StringVar(value=f"{sim.DEFAULT_Q_LIMIT_PCT:g}")
        for var, unit, tail in ((self.s_lim_var, "dB", "   |ΔL| <"),
                                (self.l_lim_var, "%", "   |ΔQ| <"),
                                (self.q_lim_var, "%", "")):
            e = ttk.Entry(lim, textvariable=var, width=6)
            e.pack(side=tk.LEFT, padx=(4, 2))
            e.bind("<Return>", lambda _e: self._render())
            e.bind("<FocusOut>", lambda _e: self._render())
            ttk.Label(lim, text=unit + tail).pack(side=tk.LEFT)

        body = ttk.PanedWindow(self, orient=tk.VERTICAL)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=2)
        tf = ttk.Frame(body)
        # Scrolls both ways: the S matrix alone is one line per port.
        self.text = tk.Text(tf, height=18, wrap=tk.NONE, font=CMP_FONT)
        xs = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.text.xview)
        ys = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.text.tag_configure("bad", foreground=_LIMIT_FG)
        body.add(tf, weight=0)

        pf = ttk.Frame(body)
        self.figure = Figure(figsize=(8, 4.2), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.figure, master=pf)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        body.add(pf, weight=1)

        _LIVE.setdefault(app, []).append(self)
        self._refresh_choices()
        self.a_var.set(fe_a.label)
        self.b_var.set(fe_b.label)
        self._recompute()

    # ------------------------------------------------------------- choices

    def _setup_map(self) -> dict:
        return dict(trace_choices(self._app.traces))

    def _refresh_choices(self) -> None:
        labels = [fe.label for fe in self._app.files]
        self.a_cbo["values"] = labels
        self.b_cbo["values"] = labels
        self.setup_cbo["values"] = [t for t, _tc in
                                    trace_choices(self._app.traces)]

    def _file(self, label: str):
        for fe in self._app.files:
            if fe.label == label:
                return fe
        return None

    def _on_pick(self) -> None:
        fa = self._file(self.a_var.get())
        fb = self._file(self.b_var.get())
        if fa is not None:
            self._fe_a = fa
        if fb is not None:
            self._fe_b = fb
        self._recompute()

    def refresh(self) -> None:
        """A file was renamed or removed under the window."""
        self._refresh_choices()
        files = list(self._app.files)
        gone = [fe for fe in (self._fe_a, self._fe_b)
                if not any(fe is f for f in files)]
        if gone:
            self._res = None
            self._write([f"{', '.join(fe.label for fe in gone)} is no longer "
                         f"loaded -- pick files above."])
            self.figure.clear()
            self.canvas.draw_idle()
            return
        self.a_var.set(self._fe_a.label)
        self.b_var.set(self._fe_b.label)

    # ----------------------------------------------------------- compute

    def _recompute(self) -> None:
        tc = self._setup_map().get(self.setup_var.get())
        if self.setup_var.get() not in self._setup_map():
            self.setup_var.set(DEFAULT_SETUP)
        self._res = compare_files(self._app, self._fe_a, self._fe_b, tc)
        self._render()

    def _limits(self) -> tuple:
        return (_float(self.s_lim_var.get(), sim.DEFAULT_S_LIMIT_DB),
                _float(self.l_lim_var.get(), sim.DEFAULT_L_LIMIT_PCT),
                _float(self.q_lim_var.get(), sim.DEFAULT_Q_LIMIT_PCT))

    def _write(self, lines: list) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        for ln in lines:
            self.text.insert(tk.END, ln + "\n",
                             ("bad",) if (DIFFERENT in ln
                                         or ln.endswith("  over")) else ())
        self.text.configure(state=tk.DISABLED)

    def _render(self) -> None:
        if self._res is None:
            return
        s_lim, l_lim, q_lim = self._limits()
        self._write(compare_summary_lines(self._res, s_lim, l_lim, q_lim))
        self._draw(s_lim, l_lim, q_lim)

    def _draw(self, s_lim: float, l_lim: float, q_lim: float) -> None:
        res = self._res
        self.figure.clear()
        panels = []
        if res.s is not None:
            panels.append("S")
        if res.ports:
            panels += ["L", "Q"]
        if not panels:
            self.canvas.draw_idle()
            return
        axes = []
        for i, kind in enumerate(panels):
            ax = self.figure.add_subplot(len(panels), 1, i + 1,
                                         sharex=axes[0] if axes else None)
            axes.append(ax)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=7)
            if kind == "S":
                f = res.s.axis.freqs / 1e9
                ax.plot(f, res.s.err_db, color=_CURVE_FG[0], linewidth=1.2)
                ax.axhline(s_lim, color=_LIMIT_FG, linestyle="--",
                           linewidth=1.0)
                ax.set_ylabel("max |ΔS| (dB)", fontsize=8)
                continue
            lim = l_lim if kind == "L" else q_lim
            for k, pc in enumerate(res.ports):
                f = pc.z.axis.freqs / 1e9
                y = pc.z.dl_pct if kind == "L" else pc.z.dq_pct
                ax.plot(f, y, color=_CURVE_FG[k % len(_CURVE_FG)],
                        linewidth=1.2,
                        label=pc.name if len(res.ports) > 1 else None)
            for sgn in (1, -1):
                ax.axhline(sgn * abs(lim), color=_LIMIT_FG, linestyle="--",
                           linewidth=1.0)
            ys = np.concatenate([np.abs(pc.z.dl_pct if kind == "L"
                                        else pc.z.dq_pct)
                                 for pc in res.ports])
            span, n_off = pct_view_span(ys, lim)
            if span is not None:
                ax.set_ylim(-span, span)
                if n_off:
                    ax.text(0.99, 0.02, f"{n_off} pt{'s' if n_off > 1 else ''}"
                            f" off scale", transform=ax.transAxes,
                            ha="right", va="bottom", fontsize=7, alpha=0.7)
            ax.set_ylabel(f"Δ{kind} B vs A (%)", fontsize=8)
            if len(res.ports) > 1:
                ax.legend(fontsize=7, loc="best")
        for ax in axes[:-1]:
            ax.tick_params(labelbottom=False)
        axes[-1].set_xlabel("Freq (GHz)", fontsize=8)
        axes[0].set_xmargin(0.0)
        try:
            self.figure.tight_layout()
        except Exception:                                   # noqa: BLE001
            pass
        self.canvas.draw_idle()


def open_compare_window(app) -> Optional[CompareWindow]:
    """Open (or raise) the window on the file selected in the Files list
    (else the first) and the one after it, the lower-band one as A."""
    files = list(app.files)
    why = compare_refusal(files)
    if why:
        messagebox.showinfo(COMPARE_TITLE, why, parent=app)
        return None
    for w in live_windows(app):
        try:
            w.deiconify()
            w.lift()
            w.refresh()
        except Exception:                                   # noqa: BLE001
            pass
        return w
    try:
        idx = app._sel_idx(app.files_lb)
    except Exception:                                       # noqa: BLE001
        idx = None
    ia = idx if idx is not None and idx < len(files) else 0
    ib = (ia + 1) % len(files)
    # The REFERENCE is the file whose whole sweep is inside the other's --
    # the lower top frequency.  "Is the 80 GHz run the 30 GHz run, below
    # 30 GHz?" reads the 80 GHz one against the 30 GHz one, whichever of the
    # two happens to be selected (after Add File it is always the last).
    if files[ib].ts.freqs[-1] < files[ia].ts.freqs[-1]:
        ia, ib = ib, ia
    return CompareWindow(app, files[ia], files[ib])


def refresh_compare_windows(app) -> None:
    """NEVER raises -- called from file add / remove / relabel handlers."""
    try:
        wins = live_windows(app)
    except Exception:                                       # noqa: BLE001
        return
    for w in wins:
        try:
            w.refresh()
        except Exception:                                   # noqa: BLE001
            pass
