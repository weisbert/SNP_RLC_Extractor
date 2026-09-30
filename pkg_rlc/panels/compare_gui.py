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
import matplotlib.ticker as mticker
from matplotlib.figure import Figure

from pkg_rlc.model.trace import TraceConfig
from pkg_rlc.model.validate import trace_is_composed
from pkg_rlc.services import run
from pkg_rlc.physics import similarity as sim
from pkg_rlc.physics.core import compute_z_matrix, format_freq, parse_si

__all__ = [
    "COMPARE_MENU_LABEL", "COMPARE_TITLE", "DEFAULT_SETUP",
    "CompareResult", "CompareWindow", "compare_files", "compare_summary_lines",
    "verdict", "trace_choices", "compare_refusal", "s_matrix_lines",
    "line_tags", "marker_hz_of", "pct_view_span",
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
    marker_hz: float = float("nan")      # the main window's working frequency
    span_a: str = ""                     # each file's own sweep, in words
    span_b: str = ""


def _num(p: float) -> str:
    """A positive percentage's number: never scientific notation -- '280',
    '9.7', '0.12', '<0.001'."""
    if p < 0.001:
        return "<0.001"
    if p >= 100:
        return f"{p:.0f}"
    return f"{p:.2g}"


def _sign_words(v: float) -> str:
    """+0.42 -> '0.42 % higher', -9.7 -> '9.7 % lower' (B against A)."""
    if not math.isfinite(v):
        return "not comparable"
    if v == 0:
        return "identical"
    return f"{_num(abs(v))} % {'higher' if v > 0 else 'lower'}"


def _pct_str(p: float) -> str:
    """A positive percentage, with as many digits as it needs to be read."""
    return f"{_num(p)} %"


def _pair(i: int, j: int) -> str:
    # Never 'S1415': with ten or more ports that cannot be read back -- it is
    # S(14,15), S(1,415) or S(141,5).  In words, which is what the reader
    # thinks in, with the S(i,j) form first for anyone who wants it.
    if i == j:
        return f"S({i},{i}), the reflection at port {i}"
    return f"S({i},{j}), between port {i} and port {j}"


_QUANTITY = {"L": "inductance L", "Q": "quality factor Q",
             "R": "resistance R"}
# str.capitalize() lowercases the rest -- "Inductance l" -- so spelled out.
_TITLE = {"L": "Inductance L", "Q": "Quality factor Q", "R": "Resistance R"}


def _near(f: float, f0: float, frac: float = 0.15) -> bool:
    return (math.isfinite(f) and math.isfinite(f0) and f0 > 0
            and abs(f - f0) <= frac * f0)


def _freq_or_none(f: float) -> str:
    return format_freq(f) if math.isfinite(f) else "none in range"


def compare_summary_lines(res: CompareResult, s_limit_pct: float,
                          l_limit_pct: float, q_limit_pct: float) -> list:
    """
    The window's reading, in the order a reader wants it: the ANSWER, what it
    means, what to do next -- and only then the evidence.

    Written for someone deciding, not auditing (the owner, on the first
    version: "根本看不懂").  So: no dB anywhere (a percentage of full scale
    instead), ports in words, "B is 9.7 % lower" instead of a signed ratio,
    the working frequency's numbers up front, and when the worst L sits
    beside a self-resonance, a sentence saying that is what it is.
    """
    s_lim = abs(s_limit_pct)
    over: list = []          # (kind, sentence) for what is over its limit
    within: list = []        # names of what was judged and is within
    lines_s: list = []
    lines_z: list = []
    srf_hint = ""
    marker_rows: list = []
    marker_f = float("nan")

    # ---- the raw file -------------------------------------------------
    sc = res.s
    if sc is not None:
        worst = float(sim.db_to_pct(sc.worst_db))
        i, j = sc.worst_entry
        ranked = [e for e in sc.ranked_entries() if e[2] > S_IDENTICAL_DB]
        n_pairs = 0 if sc.entry_db is None else sc.entry_db.size
        n_over = sum(1 for e in ranked if sim.db_to_pct(e[2]) > s_lim)
        is_over = worst > s_lim
        if is_over:
            over.append(("S", f"the raw S-parameters differ by up to "
                              f"{_pct_str(worst)} -- worst is {_pair(i, j)}, "
                              f"at {format_freq(sc.worst_f)}"))
        else:
            within.append("the raw S-parameters")
        lines_s.append(f"  Largest difference: {_pct_str(worst)} -- "
                       f"{_pair(i, j)}, at {format_freq(sc.worst_f)}.")
        lines_s.append(f"  Your limit is {s_lim:g} %  ->  "
                       + ("OVER the limit." if is_over else "within it."))
        if n_pairs > 1:
            lines_s.append(f"  {n_over} of the {n_pairs} port pairs differ by "
                           f"more than {s_lim:g} %."
                           + (" The largest:" if ranked else
                              " Every pair is identical."))
        for ii, jj, db, f in (ranked[:S_RANK_SHOWN] if n_pairs > 1 else []):
            p = float(sim.db_to_pct(db))
            mark = "   over" if p > s_lim else ""
            name = f"S({ii},{jj})"
            lines_s.append(f"     {name:<9} {_pct_str(p):>10}  at "
                           f"{format_freq(f)}{mark}")
        lines_s.append("  (1 % means the two S values are 0.01 apart; "
                       "|S| is never more than 1.)")
    else:
        lines_s.append(f"  Not compared: {res.s_why}")

    # ---- the inductor -------------------------------------------------
    for pc in res.ports:
        z = pc.z
        tag = f" [{pc.name}]" if len(res.ports) > 1 else ""
        if tag:
            lines_z.append(f"  Measurement port {pc.name}:")
        top = z.usable_limit()
        lo = z.axis.lo
        resonant = math.isfinite(top)
        if resonant:
            lines_z += _wrap(
                f"Self-resonance: A at {_freq_or_none(z.srf_a)}, B at "
                f"{_freq_or_none(z.srf_b)}. L and Q are judged below "
                f"{format_freq(top)} ({sim.SRF_JUDGE_FRAC:.0%} of the lower "
                f"one): near and past the resonance the part is no longer "
                f"an inductor, so a percentage of L there says little.",
                WRAP_AT, indent="  ")
            head = f"below {format_freq(top)}"
        else:
            head = "largest difference"
        lines_z.append(f"  {'':<20}{head:<24}{'at':<12}{'your limit'}")
        for key, lim in (("L", l_limit_pct), ("Q", q_limit_pct), ("R", None)):
            w = z.worst_in(key, lo, top) if resonant else \
                {"L": z.l, "Q": z.q, "R": z.r}[key]
            name = _QUANTITY[key]
            where = format_freq(w.freq) if math.isfinite(w.freq) else "-"
            if lim is None:
                judged = "(none)"
            elif not math.isfinite(w.value):
                judged = "-"
            elif abs(w.value) > abs(lim):
                judged = f"{abs(lim):g} %  OVER"
                span = f" below {format_freq(top)}" if resonant else ""
                over.append(("Z", f"{name}{tag}: B is "
                                  f"{_sign_words(w.value)} than A{span} "
                                  f"(worst at {where})"))
            else:
                judged = f"{abs(lim):g} %  ok"
                within.append(f"{key}{tag}")
            said = "B is " + _sign_words(w.value)
            lines_z.append(f"  {name:<20}{said:<24}{where:<12}{judged}")
        if resonant:
            wl = z.worst_in("L", top, z.axis.hi + 1.0)
            wq = z.worst_in("Q", top, z.axis.hi + 1.0)
            if math.isfinite(wl.value) or math.isfinite(wq.value):
                lines_z.append(
                    f"  Near and past the resonance (not judged): L up to "
                    f"{_num(abs(wl.value)) if math.isfinite(wl.value) else '-'}"
                    f" %, Q up to "
                    f"{_num(abs(wq.value)) if math.isfinite(wq.value) else '-'}"
                    f" % different.")
            if not srf_hint and math.isfinite(z.srf_a) and \
                    math.isfinite(z.srf_b):
                shift = 100.0 * (z.srf_b - z.srf_a) / z.srf_a
                srf_hint = (
                    f"The self-resonance moved from {format_freq(z.srf_a)} "
                    f"(A) to {format_freq(z.srf_b)} (B), "
                    f"{_sign_words(shift).replace('higher', 'up').replace('lower', 'down')}. "
                    f"Near it L and Q change very fast, which is why they "
                    f"differ much more there than below it.")
        excl = [(k, w.excluded) for k, w in (("L", z.l), ("Q", z.q),
                                              ("R", z.r)) if w.excluded]
        if excl:
            lines_z.append("  (" + ", ".join(
                f"{n} {k} point{'s' if n > 1 else ''}" for k, n in excl)
                + " skipped where A's value is almost zero -- a percentage "
                  "of nearly nothing means nothing.)")
        at = z.at(res.marker_hz) if math.isfinite(res.marker_hz) else None
        if at is not None:
            dl, dq, dr, marker_f = at
            marker_rows.append(
                f"    {tag.strip() + '  ' if tag else ''}"
                f"L: B is {_sign_words(dl)}    Q: B is {_sign_words(dq)}    "
                f"R: B is {_sign_words(dr)}")
    if not res.ports:
        lines_z.append(f"  Not compared: {res.z_why}")

    # ---- the answer, first --------------------------------------------
    band = ""
    axis = sc.axis if sc is not None else (
        res.ports[0].z.axis if res.ports else None)
    if axis is not None:
        band = f"{format_freq(axis.lo)} - {format_freq(axis.hi)}"
    out = ["IN SHORT"]
    if not over and not within:
        out.append("  Nothing could be compared -- the reasons are below.")
    elif not over:
        out.append(f"  THE SAME within your limits, everywhere in {band}.")
    else:
        out.append(f"  NOT THE SAME in {band}:")
        for _k, o in over:
            out += _wrap(f"- {o}", WRAP_AT, indent="    ")
        if within:
            out.append(f"  Within your limits: {', '.join(within)}.")
    if srf_hint:
        out.append("")
        out += _wrap(srf_hint, WRAP_AT, indent="  ")
    if marker_rows:
        out.append("")
        out.append(f"  At the marker frequency, {format_freq(marker_f)} "
                   f"(set in the main window):")
        out += marker_rows
    elif res.ports and math.isfinite(res.marker_hz):
        out.append("")
        out.append(f"  The marker frequency ({format_freq(res.marker_hz)}) "
                   f"is outside the compared range -- no reading there.")

    out += ["", "WHAT TO DO NEXT"]
    out += _next_steps(res, over, srf_hint, band)

    out += ["", "WHAT WAS COMPARED",
            f"  A (the reference): {res.label_a}"
            + (f"   ({res.span_a})" if res.span_a else ""),
            f"  B:                 {res.label_b}"
            + (f"   ({res.span_b})" if res.span_b else "")]
    if axis is not None:
        out.append(f"  Only {band} is compared -- the range both files cover.")
        if axis.grid_of == "both":
            out.append(f"  Both files have the same {len(axis.freqs)} "
                       f"frequency points there, so they are compared point "
                       f"by point.")
        else:
            out.append(f"  Compared on {axis.grid_of}'s {len(axis.freqs)} "
                       f"points (the coarser file); {axis.interpolated} was "
                       f"interpolated onto them.")
    for n in res.notes + (sc.notes if sc is not None else []):
        out.append(f"  Note: {n}")

    out += ["", "1. THE RAW FILE -- every S-parameter of every port pair"]
    out += lines_s
    out += ["", f"2. THE INDUCTOR -- {res.setup}"]
    out += lines_z
    if sc is not None:
        table = s_matrix_lines(sc, s_lim)
        if table:
            out += [""] + table
    return out


#: The reading is wrapped here, a little inside the window's default width.
WRAP_AT = 96


def _wrap(text: str, width: int, indent: str = "") -> list:
    import textwrap
    hang = "  " if text.startswith("- ") else ""     # a bullet hangs
    return textwrap.wrap(text, width=width, initial_indent=indent,
                         subsequent_indent=indent + hang) or [""]


def _next_steps(res: CompareResult, over: list, srf_hint: str,
                band: str) -> list:
    s_over = any(k == "S" for k, _o in over)
    z_over = any(k == "Z" for k, _o in over)
    if not over and not res.ports and res.s is None:
        return ["  Fix what is named below, then pick the files again."]
    if not over:
        steps = [f"Nothing to do: B can stand in for A over {band} within "
                 f"your limits.",
                 "If your margin is tighter, lower the limits at the top."]
        if srf_hint:
            steps.insert(1, "If the circuit relies on the self-resonance "
                            "itself, look at the dotted lines on the L curve "
                            "below: that is where the two differ.")
    else:
        steps = []
        if z_over:
            steps.append("The inductor itself differs. The L and Q curves "
                         "below show over which frequencies; move the marker "
                         "to your operating frequency to read it there.")
        if s_over and not z_over and res.ports:
            steps.append("The raw files differ a little, but not in what "
                         "this port setup measures. If this setup is how the "
                         "part is used, the two files are equivalent for it.")
        elif s_over and res.s.entry_db is not None and                 res.s.entry_db.shape[0] > 1:
            steps.append("The table at the end shows which port pairs "
                         "moved; the top curve shows at which frequencies.")
        steps.append("If your margin is looser than the limits at the top, "
                     "change them there -- the answer updates at once.")
    return [ln for st in steps for ln in _wrap(f"- {st}", WRAP_AT, "  ")]


#: Above this many ports the full difference table is not printed -- it
#: would be wider than any pane -- and the ranked list stands in for it.
S_MATRIX_MAX_PORTS = 32
#: How many port pairs the ranked list names.
S_RANK_SHOWN = 5
#: Below this, an entry is the same to printing precision (identical data
#: floors at -300 dB) and reads '.' rather than a number nobody can use.
S_IDENTICAL_DB = -200.0


def s_matrix_lines(sc, s_limit_pct: float) -> list:
    """Every port pair's largest difference over the band, as a table --
    WHICH ports moved, at a glance.  Up to S_MATRIX_MAX_PORTS."""
    n = 0 if sc.entry_db is None else sc.entry_db.shape[0]
    if n <= 1:
        return []
    if n > S_MATRIX_MAX_PORTS:
        return [f"ALL PORT PAIRS -- {n} ports are too many for a table; the "
                f"list in section 1 is ranked over all {n * n}."]
    out = ["ALL PORT PAIRS -- the largest difference of each, in %",
           "  (row i, column j = S(i,j);   * = over your limit,   "
           ". = identical)",
           "      " + "".join(f"{c:>7} " for c in range(1, n + 1))]
    for r in range(n):
        cells = []
        for c in range(n):
            db = float(sc.entry_db[r, c])
            if db <= S_IDENTICAL_DB:
                cells.append(f"{'.':>7} ")
                continue
            p = float(sim.db_to_pct(db))
            txt = "<0.001" if p < 0.001 else f"{p:.2g}"
            cells.append(f"{txt:>7}" + ("*" if p > s_limit_pct else " "))
        out.append(f"  {r + 1:>3} " + "".join(cells))
    return out


#: The S limit the window opens with, in percent of full scale (-40 dB).
S_LIMIT_PCT_DEFAULT = float(sim.db_to_pct(sim.DEFAULT_S_LIMIT_DB))

_HEADS = ("IN SHORT", "WHAT TO DO NEXT", "WHAT WAS COMPARED", "1. ", "2. ",
          "ALL PORT PAIRS")


def line_tags(ln: str) -> tuple:
    """Section headings bold; what is over a limit red."""
    if ln.startswith(_HEADS):
        return ("head",)
    if ("NOT THE SAME" in ln or "OVER" in ln or ln.endswith("   over")
            or ln.startswith("    - ")):
        return ("bad",)
    return ()


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


def compare_files(app, fe_a, fe_b, tc=None,
                  marker_hz: float = float("nan")) -> CompareResult:
    """Both levels, each failing on its own: a port-count mismatch stops the S
    comparison, not the L / Q one, and a spec that one file cannot hold stops
    L / Q, not S."""
    res = CompareResult(
        label_a=fe_a.label, label_b=fe_b.label,
        setup=("port 1 to ground, other ports open (the default), solved on "
               "both files") if tc is None else
              (f'the port setup of trace [{tc.id}] "{tc.label}", solved on '
               f'both files'),
        marker_hz=marker_hz,
        span_a=_span(fe_a), span_b=_span(fe_b))
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


def _span(fe) -> str:
    try:
        return fe.ts.freq_span_str()
    except Exception:                                       # noqa: BLE001
        return ""


def marker_hz_of(app) -> float:
    """The main window's marker frequency (the box is in GHz), or nan."""
    try:
        return float(parse_si(app.rlc_freq_var.get())) * 1e9
    except Exception:                                       # noqa: BLE001
        return float("nan")


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
        # The marker lives in the main window and this one does not watch it:
        # moved there, it is read here on the next Refresh.
        ttk.Button(foot, text="Refresh", command=self._recompute
                   ).pack(side=tk.RIGHT, padx=(0, 6))

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
        # In PERCENT, all three: "1 %" is what a reader means by close; a
        # dB box for S was the first thing the owner could not read.
        ttk.Label(lim, text="Call them the same if they differ by less "
                            "than:").pack(side=tk.LEFT)
        self.s_lim_var = tk.StringVar(value=f"{S_LIMIT_PCT_DEFAULT:g}")
        self.l_lim_var = tk.StringVar(value=f"{sim.DEFAULT_L_LIMIT_PCT:g}")
        self.q_lim_var = tk.StringVar(value=f"{sim.DEFAULT_Q_LIMIT_PCT:g}")
        for var, head in ((self.s_lim_var, "   S-parameters"),
                          (self.l_lim_var, "   L"),
                          (self.q_lim_var, "   Q")):
            ttk.Label(lim, text=head).pack(side=tk.LEFT)
            unit = "%"
            e = ttk.Entry(lim, textvariable=var, width=5)
            e.pack(side=tk.LEFT, padx=(4, 2))
            e.bind("<Return>", lambda _e: self._render())
            e.bind("<FocusOut>", lambda _e: self._render())
            ttk.Label(lim, text=unit).pack(side=tk.LEFT)

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
        self.text.tag_configure("head", font=CMP_FONT + ("bold",))
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
        self._res = compare_files(self._app, self._fe_a, self._fe_b, tc,
                                  marker_hz=marker_hz_of(self._app))
        self._render()

    def _limits(self) -> tuple:
        return (_float(self.s_lim_var.get(), S_LIMIT_PCT_DEFAULT),
                _float(self.l_lim_var.get(), sim.DEFAULT_L_LIMIT_PCT),
                _float(self.q_lim_var.get(), sim.DEFAULT_Q_LIMIT_PCT))

    def _write(self, lines: list) -> None:
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        for ln in lines:
            self.text.insert(tk.END, ln + "\n", line_tags(ln))
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
                # Floored at 0.0001 %: below that is "identical" to any
                # reader, and a tick reading 1e-06 % is noise on the page.
                y = np.maximum(sim.db_to_pct(res.s.err_db), 1e-4)
                ax.plot(f, y, color=_CURVE_FG[0], linewidth=1.2)
                ax.set_yscale("log")
                # '1 %', '0.1 %' -- not 10^0, 10^-1: the text above speaks
                # in percent, and so does the limit box.
                ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                    lambda v, _p: f"{v:g} %"))
                ax.axhline(abs(s_lim), color=_LIMIT_FG, linestyle="--",
                           linewidth=1.0)
                ax.set_title("Raw file: the largest S difference of any port "
                             "pair (%)  -- red dashes = your limit",
                             fontsize=8, loc="left")
                self._mark(ax, res, None)
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
            ax.set_title(f"{_TITLE[kind]}: how much B "
                         f"differs from A (%, + = B larger)", fontsize=8,
                         loc="left")
            self._mark(ax, res, res.ports[0].z)
            if len(res.ports) > 1:
                ax.legend(fontsize=7, loc="best")
        for ax in axes[:-1]:
            ax.tick_params(labelbottom=False)
        axes[-1].set_xlabel("Frequency (GHz)", fontsize=8)
        axes[0].set_xmargin(0.0)
        try:
            self.figure.tight_layout()
        except Exception:                                   # noqa: BLE001
            pass
        self.canvas.draw_idle()


    @staticmethod
    def _mark(ax, res, z) -> None:
        """The marker frequency (grey) and, on L / Q, each file's
        self-resonance (dotted) -- the two places the text talks about."""
        tx = ax.get_xaxis_transform()
        if math.isfinite(res.marker_hz):
            ax.axvline(res.marker_hz / 1e9, color="#888888", linewidth=0.9)
            ax.text(res.marker_hz / 1e9, 0.02, " marker", transform=tx,
                    fontsize=6, va="bottom", ha="left", alpha=0.8)
        if z is None:
            return
        top = z.usable_limit()
        if math.isfinite(top):
            # What the verdict does NOT read: shaded, and said so.
            ax.axvspan(top / 1e9, z.axis.hi / 1e9, color="#000000",
                       alpha=0.06, linewidth=0)
            ax.text(top / 1e9, 0.02, " not judged (resonance) ",
                    transform=tx, fontsize=6, va="bottom", ha="left",
                    alpha=0.8)
        # A's label at the top, B's at the bottom: two resonances 1 % apart
        # otherwise print one label over the other.
        for f0, who, y, va in ((z.srf_a, "A", 0.98, "top"),
                               (z.srf_b, "B", 0.12, "bottom")):
            if math.isfinite(f0):
                ax.axvline(f0 / 1e9, color="#555555", linestyle=":",
                           linewidth=0.9)
                ax.text(f0 / 1e9, y, f" {who} resonance", transform=tx,
                        fontsize=6, va=va, ha="left", alpha=0.8)


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
