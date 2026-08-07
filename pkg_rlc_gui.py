"""
pkg_rlc_gui.py  --  Tkinter GUI for PKG RLC Extractor.

Layout (horizontal PanedWindow):
  Left  ~ 460 px : Files / Traces / Editor / Global controls
  Right          : Results text (top) + plot panel (bottom)
                   (vertical PanedWindow, sash draggable by user)

Measurement modes (integer codes are stable and never renumbered):
  1  Port(s) -> GND
  2  A <-> B
  3  A <-> B + Short Pairs
  4  RETIRED (A <-> B + VDD/GND).  For AC small-signal VDD *is* an AC ground,
     so a mode-4 trace migrates to mode 2 with its VDD ports folded into GND.
  5  Custom (advanced) -- the Mode 5 termination DSL
  6  +/- Ports / Coupling (M, k) -- any number of measurement ports, each a
     pair of probes (red = plus side, black = minus side).  Produces a G x G
     impedance matrix: self impedance on the diagonal, open-circuit mutual
     impedance off it, from which M, k, C_c and the M/L ratios are read.
"""

from __future__ import annotations

import csv
import math
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

from pkg_rlc_core import (
    DEFAULT_Z0,
    RECIPROCITY_WARN,
    SI_SUFFIXES,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    Open,
    ShortPair,
    Signal,
    TerminationSet,
    TouchstoneData,
    Vdd,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    compute_z,
    compute_z_matrix,
    eval_capacitor_model,
    eval_inductor_model,
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    parse_custom_termination_text,
    parse_kv_rlc_params,
    parse_mport_spec,
    parse_port_range,
    parse_short_pairs,
    parse_si,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
    format_si,
)
from pkg_rlc_plot import (
    COLORS, LINESTYLES, MAX_LABEL_LEN, PlotPanel, Trace as PlotTrace,
)
from pkg_rlc_help import HelpWindow


# ============================================================================
# Helpers
# ============================================================================
#
# SI_SUFFIXES, parse_si, parse_kv_rlc_params and parse_custom_termination_text
# now live in pkg_rlc_core (terminations belong to core). They are re-exported
# here so `from pkg_rlc_gui import parse_si` and friends keep resolving.
#
# build_terminations_mode4 is likewise kept in the import list purely as a
# re-export: mode 4 is retired in the GUI (VDD is an AC ground, so it folds
# into mode 2), but the CLI and old scripts still reference the builder.


# ============================================================================
# Data classes
# ============================================================================

class FileEntry:
    """A loaded Touchstone file with its derived Y-matrix."""

    def __init__(self, ts: TouchstoneData):
        self.ts = ts
        self.Y = s_to_y(ts.s, ts.z0)
        self.label = Path(ts.source_path).name

    def info_str(self) -> str:
        return (f"{self.label}  "
                f"(N={self.ts.nports}, M={len(self.ts.freqs)}, "
                f"Z0={self.ts.z0:g}Ω)")


@dataclass
class TraceConfig:
    """
    User-editable trace specification (1-based ports).

    Mode codes are stable and never renumbered: 1, 2, 3, 5 and 6 are live;
    4 ("A ↔ B + VDD/GND") is RETIRED — for AC small-signal VDD is just another
    AC ground, so a mode-4 trace is migrated to mode 2 with its VDD ports
    unioned into the GND ports (see `migrate_legacy_mode`).  `vdd_ports` is
    kept as a field so old configs still load; nothing edits it any more.
    """
    id: int = 0
    file_label: str = ""
    mode: int = 1            # 1, 2, 3, 5, 6  (4 retired -> migrated to 2)
    port_a: str = "1"
    port_b: str = ""
    short_pairs: str = ""
    gnd_ports: str = ""
    vdd_ports: str = ""      # retired; kept so mode-4 configs still migrate
    custom_text: str = ""
    # --- Mode 6 (+/- measurement ports / coupling) ---
    mp1_name: str = ""
    mp1_plus: str = ""
    mp1_minus: str = ""
    mp2_name: str = ""
    mp2_plus: str = ""
    mp2_minus: str = ""
    mp_more: str = ""
    plot_self: bool = True
    plot_mutual: bool = True
    label: str = ""
    color_idx: int = 0
    ls_idx: int = 0
    # Computed (filled in after Calculate)
    Z: Optional[np.ndarray] = None
    rlc: Optional[object] = None
    fit_kind: str = ""
    fit: Optional[object] = None
    # Computed, mode 6 only
    Zmat: Optional[np.ndarray] = None          # (nfreqs, G, G) complex
    mport_names: Optional[list[str]] = None    # length G
    coupling: Optional[object] = None          # CouplingResult at marker freq

    MODE_NAMES = {1: "GND", 2: "A↔B", 3: "A↔B+Short",
                  4: "A↔B+VDD (retired)", 5: "Custom", 6: "+/- Coupling"}

    def info_str(self) -> str:
        return (f"[{self.id}] {self.label}  |  "
                f"{self.file_label}  {self.MODE_NAMES.get(self.mode, '?')}")

    def port_descriptor(self) -> str:
        """Compact one-line port-config descriptor for the results table."""
        return _port_descriptor(self)

    def mode_name(self) -> str:
        return self.MODE_NAMES.get(self.mode, f"mode{self.mode}")

    def migrate_legacy_mode(self) -> bool:
        """
        Retired mode 4 -> mode 2 with VDD folded into GND.  Returns True when a
        migration actually happened, so the caller can tell the user.
        """
        if self.mode != 4:
            return False
        self.gnd_ports = _union_port_specs(self.gnd_ports, self.vdd_ports)
        self.vdd_ports = ""
        self.mode = 2
        return True


def _fmt_port_terminal(spec: str) -> str:
    """Render a port terminal spec: '1' -> '1', '2,3' -> '{2,3}', '' -> '?'."""
    try:
        ports = parse_port_range(spec)
    except Exception:
        return spec.strip() or "?"
    if not ports:
        return "?"
    if len(ports) == 1:
        return str(ports[0])
    return "{" + ",".join(str(p) for p in ports) + "}"


def _fmt_port_set(spec: str) -> str:
    """Render a port-set spec (gnd/vdd): always bracketed. '' -> '[]'."""
    try:
        ports = parse_port_range(spec)
    except Exception:
        return f"[{spec.strip()}]"
    return "[" + ",".join(str(p) for p in ports) + "]"


def _fmt_short_pairs(spec: str) -> str:
    """Render short-pair groups: '1-2,3-4-5' -> '[1-2,3-4-5]'."""
    s = (spec or "").strip()
    return f"[{s}]"


def _union_port_specs(*specs: str) -> str:
    """
    Merge port-range specs into one sorted comma list ('5' + '7,8' -> '5,7,8').

    Used by the retired-mode-4 migration to fold VDD ports into GND.  If a spec
    cannot be parsed it is passed through verbatim rather than silently lost.
    """
    ports: list[int] = []
    leftovers: list[str] = []
    for spec in specs:
        text = (spec or "").strip()
        if not text:
            continue
        try:
            ports.extend(parse_port_range(text))
        except Exception:
            leftovers.append(text)
    merged = [str(p) for p in sorted(set(ports))]
    merged.extend(leftovers)
    return ",".join(merged)


def _fmt_mport(name: str, plus: str, minus: str) -> str:
    """Render one measurement port: 'tank:1/2', '3,4', 'rx:{5,6}'."""
    body = _fmt_port_terminal(plus)
    if (minus or "").strip():
        body += "/" + _fmt_port_terminal(minus)
    name = (name or "").strip()
    return f"{name}:{body}" if name else body


def _mport_more_lines(text: str) -> list[str]:
    """Non-empty, non-comment lines of the 'More ports' box."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _port_descriptor(tc: "TraceConfig") -> str:
    if tc.mode == 1:
        return f"M1: S:{_fmt_port_set(tc.port_a)} G:{_fmt_port_set(tc.gnd_ports)}"
    if tc.mode == 2:
        return (f"M2: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(tc.gnd_ports)}")
    if tc.mode == 3:
        return (f"M3: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(tc.gnd_ports)} S:{_fmt_short_pairs(tc.short_pairs)}")
    if tc.mode == 4:
        # Retired: shown only if a stale config has not been migrated yet.
        return (f"M4→M2: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(_union_port_specs(tc.gnd_ports, tc.vdd_ports))}")
    if tc.mode == 6:
        parts = []
        if (tc.mp1_plus or "").strip():
            parts.append(_fmt_mport(tc.mp1_name, tc.mp1_plus, tc.mp1_minus))
        if (tc.mp2_plus or "").strip():
            parts.append(_fmt_mport(tc.mp2_name, tc.mp2_plus, tc.mp2_minus))
        extra = len(_mport_more_lines(tc.mp_more))
        if extra:
            parts.append(f"+{extra}")
        body = " ".join(parts) if parts else "(empty)"
        return f"M6: {body} G:{_fmt_port_set(tc.gnd_ports)}"
    if tc.mode == 5:
        text = (tc.custom_text or "").strip().replace("\n", " ")
        if len(text) > 28:
            text = text[:25] + "..."
        return f"M5: {text}" if text else "M5: (empty)"
    return f"M?: mode={tc.mode}"


# ============================================================================
# Mode 6 helpers (+/- measurement ports, coupling)
# ============================================================================

def _collect_mports(tc: "TraceConfig") -> list[tuple[str, list[int], list[int]]]:
    """
    Editor fields -> the (name, plus_1based, minus_1based) triples that
    build_terminations_coupling expects.  Ports stay 1-based here; the core
    builder is the 1-based/0-based boundary.
    """
    out: list[tuple[str, list[int], list[int]]] = []
    for idx, (name, plus, minus) in enumerate(
            ((tc.mp1_name, tc.mp1_plus, tc.mp1_minus),
             (tc.mp2_name, tc.mp2_plus, tc.mp2_minus)), start=1):
        plus = (plus or "").strip()
        minus = (minus or "").strip()
        if not plus:
            if minus:
                raise ValueError(
                    f"Port {idx} has a '-' side but no '+' side; the red probe "
                    "must touch at least one port.")
            continue
        out.append(((name or "").strip(),
                    parse_port_range(plus), parse_port_range(minus)))

    for line in _mport_more_lines(tc.mp_more):
        out.append(parse_mport_spec(line))

    if not out:
        raise ValueError(
            "No measurement ports defined: fill in Port 1 (+) "
            "(or add lines under 'More ports').")
    return out


def _coupling_k_array(Zmat: np.ndarray, freqs: np.ndarray,
                      a: int, b: int) -> np.ndarray:
    """
    Coupling coefficient k(f) = M / sqrt(L_a * L_b) for the pair (a, b).

    Signed and never clipped, exactly like extract_coupling_at_freq: NaN only
    where k is genuinely undefined (a port that is not inductive at that
    frequency).  omega cancels out of the ratio but is kept explicit so the
    formula matches the core one line for line.
    """
    omega = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        La = Zmat[:, a, a].imag / omega
        Lb = Zmat[:, b, b].imag / omega
        M = Zmat[:, a, b].imag / omega
        k = M / np.sqrt(La * Lb)
        k = np.where((La > 0.0) & (Lb > 0.0), k, np.nan)
    return k


def _compose_curve_label(trace_label: str, suffix: str,
                         limit: int = MAX_LABEL_LEN) -> str:
    """
    '<trace label> : <suffix>' clipped to `limit` chars (the plot-legend
    invariant).  The trace label is trimmed first so the measurement-port /
    pair name -- the part that distinguishes the expanded curves -- survives.
    """
    sep = " : "
    trace_label = (trace_label or "").strip()
    suffix = (suffix or "").strip()
    full = f"{trace_label}{sep}{suffix}"
    if len(full) <= limit:
        return full
    keep = limit - len(sep) - len(suffix)
    if keep >= 2:
        return f"{trace_label[:keep - 1]}…{sep}{suffix}"
    return full[:limit]


# The plot module grows a Trace.aux dict for per-curve extras (the k array on a
# mutual curve).  Detect it rather than assume it, so this file works whether
# or not that change has landed yet.
_PLOT_TRACE_FIELDS = set(getattr(PlotTrace, "__dataclass_fields__", {}) or {})
PLOT_TRACE_SUPPORTS_AUX = "aux" in _PLOT_TRACE_FIELDS


def _make_plot_trace(aux: Optional[dict] = None, **kwargs) -> PlotTrace:
    """Build a PlotTrace, attaching `aux` only if pkg_rlc_plot supports it."""
    if aux and PLOT_TRACE_SUPPORTS_AUX:
        kwargs["aux"] = aux
    return PlotTrace(**kwargs)


def _write_coupling_csv(fh, writer, tc: "TraceConfig", fe: "FileEntry") -> None:
    """
    Mode-6 CSV block: Re/Im of every Z_ij, then M_nH and k for every unordered
    pair, one row per frequency.  Every value keeps its physical sign; nothing
    is clipped to NaN except where it is genuinely undefined.
    """
    Zmat = tc.Zmat
    names = list(tc.mport_names or [])
    freqs = fe.ts.freqs
    G = int(Zmat.shape[1])
    pairs = [(a, b) for a in range(G) for b in range(a + 1, G)]

    fh.write("# Measurement ports: " + ", ".join(names) + "\n")
    fh.write("# Off-diagonal Z is open-circuit mutual impedance "
             "(every other measurement port open).\n")
    header = ["Freq_GHz"]
    for i in range(G):
        for j in range(G):
            header.append(f"Re_Z_{names[i]}_{names[j]}")
            header.append(f"Im_Z_{names[i]}_{names[j]}")
    for a, b in pairs:
        header.append(f"M_nH_{names[a]}_{names[b]}")
        header.append(f"k_{names[a]}_{names[b]}")
    writer.writerow(header)

    omega = 2.0 * np.pi * freqs
    k_arrays = {ab: _coupling_k_array(Zmat, freqs, *ab) for ab in pairs}
    for idx in range(len(freqs)):
        row = [f"{freqs[idx] / 1e9:.6g}"]
        for i in range(G):
            for j in range(G):
                z = Zmat[idx, i, j]
                row.append(f"{z.real:.6e}")
                row.append(f"{z.imag:.6e}")
        for ab in pairs:
            a, b = ab
            M_nH = (Zmat[idx, a, b].imag / omega[idx] * 1e9
                    if omega[idx] != 0.0 else float("nan"))
            row.append(f"{M_nH:.6e}")
            row.append(f"{float(k_arrays[ab][idx]):.6e}")
        writer.writerow(row)


# Header units (kept aligned with format_si base unit). Tk Text uses a
# monospace font, so rendering 'Ω' is fine.
_TABLE_BASE_UNITS = {"R": "Ω", "L": "H", "C": "F", "Q": ""}

# Aligned mode: pick the column unit by the largest absolute value seen.
_ALIGNED_PREFIXES = [
    (-15, "f"), (-12, "p"), (-9, "n"), (-6, "u"), (-3, "m"),
    (0, ""), (3, "k"), (6, "M"), (9, "G"),
]


def _aligned_prefix_for(values):
    """Pick the SI prefix exponent best suited for the largest |v| in `values`."""
    finite = [abs(v) for v in values if math.isfinite(v) and v != 0.0]
    if not finite:
        return 0, ""
    largest = max(finite)
    log10 = math.log10(largest)
    chosen = (-15, "f")
    for exp, pfx in _ALIGNED_PREFIXES:
        if log10 >= exp:
            chosen = (exp, pfx)
        else:
            break
    return chosen


def _fmt_aligned(value: float, exp: int, sig: int = 4) -> str:
    if not math.isfinite(value):
        return "nan"
    return f"{value / (10 ** exp):.{sig}g}"


def _sign_flag(res) -> str:
    """Compact flag: always 'cap' or 'ind' (per Im(Z) sign), plus 'R<0' if non-passive."""
    flags = []
    if math.isfinite(res.L_henry):
        if res.L_henry < 0:
            flags.append("cap")
        elif res.L_henry > 0:
            flags.append("ind")
    if math.isfinite(res.R_ohm) and res.R_ohm < 0:
        flags.append("R<0")
    return ",".join(flags)


def _format_results_table(rows, units_mode: str) -> str:
    """
    rows: list of (tc, file_label, res). Returns a multi-line aligned table.
    units_mode in {'smart', 'aligned'}.
    """
    if not rows:
        return ""

    file_labels_in_order = []
    seen = set()
    for _, fl, _ in rows:
        if fl not in seen:
            seen.add(fl)
            file_labels_in_order.append(fl)
    multi_file = len(file_labels_in_order) > 1
    file_alias = {fl: f"F{i + 1}" for i, fl in enumerate(file_labels_in_order)}

    # Truncation widths
    LABEL_W = 18
    PORT_W = 24
    FILE_W = 4
    NUM_W = 10  # per numeric cell (smart mode); aligned mode tighter

    def _trunc(s: str, w: int) -> str:
        if len(s) <= w:
            return s
        return s[: w - 1] + "…"

    lines = []
    if multi_file:
        lines.append("  " + "  ".join(
            f"{file_alias[fl]}={fl}" for fl in file_labels_in_order
        ))
    else:
        lines.append(f"  file: {file_labels_in_order[0]}")

    # Header
    if units_mode == "aligned":
        # Pick per-column prefix from the data
        Rs = [tc_res[2].R_ohm for tc_res in rows]
        Ls = [tc_res[2].L_henry for tc_res in rows]
        Cs = [tc_res[2].C_farad for tc_res in rows]
        Qs = [tc_res[2].Q for tc_res in rows]
        r_exp, r_pfx = _aligned_prefix_for(Rs)
        l_exp, l_pfx = _aligned_prefix_for(Ls)
        c_exp, c_pfx = _aligned_prefix_for(Cs)
        col_R = f"R[{r_pfx}Ω]"
        col_L = f"L[{l_pfx}H]"
        col_C = f"C[{c_pfx}F]"
        col_Q = "Q"
        NUM_W = 9
    else:
        col_R, col_L, col_C, col_Q = "R", "L", "C", "Q"
        NUM_W = 10

    parts = ["ID  ", f"{'Label':<{LABEL_W}}  "]
    if multi_file:
        parts.append(f"{'File':<{FILE_W}}  ")
    parts.append(f"{'Ports':<{PORT_W}}  ")
    parts.append(f"{col_R:>{NUM_W}}  ")
    parts.append(f"{col_L:>{NUM_W}}  ")
    parts.append(f"{col_C:>{NUM_W}}  ")
    parts.append(f"{col_Q:>{NUM_W}}  ")
    parts.append("Sign")
    lines.append("".join(parts))

    for tc, fl, res in rows:
        flag = _sign_flag(res)
        if units_mode == "aligned":
            r_str = _fmt_aligned(res.R_ohm, r_exp)
            l_str = _fmt_aligned(res.L_henry, l_exp)
            c_str = _fmt_aligned(res.C_farad, c_exp)
            q_str = "nan" if not math.isfinite(res.Q) else f"{res.Q:.4g}"
        else:
            r_str = format_si(res.R_ohm, "Ω")
            l_str = format_si(res.L_henry, "H")
            c_str = format_si(res.C_farad, "F")
            q_str = "nan" if not math.isfinite(res.Q) else f"{res.Q:.3g}"

        row_parts = [
            f"[{tc.id:>2}] ",
            f"{_trunc(tc.label, LABEL_W):<{LABEL_W}}  ",
        ]
        if multi_file:
            row_parts.append(f"{file_alias[fl]:<{FILE_W}}  ")
        row_parts.append(f"{_trunc(tc.port_descriptor(), PORT_W):<{PORT_W}}  ")
        row_parts.append(f"{r_str:>{NUM_W}}  ")
        row_parts.append(f"{l_str:>{NUM_W}}  ")
        row_parts.append(f"{c_str:>{NUM_W}}  ")
        row_parts.append(f"{q_str:>{NUM_W}}  ")
        row_parts.append(flag)
        lines.append("".join(row_parts))

    lines.append(
        "  legend: ind = Im(Z)>0 (inductive) | "
        "cap = Im(Z)<0 (capacitive; past SRF for an inductor) | "
        "R<0 = non-passive"
    )
    return "\n".join(lines)


# ============================================================================
# Mode 6 results block (Z matrix + self table + per-pair coupling)
# ============================================================================

# RECIPROCITY_WARN (the threshold above which Z_ab and Z_ba disagree enough
# that the S-parameters, not the maths, are the likely problem) now lives in
# pkg_rlc_core and is imported above, so the GUI and the CLI cannot drift into
# giving the same file opposite verdicts.  It is re-exported here because it
# used to be defined in this module.


def _trunc_str(s: str, w: int) -> str:
    s = s or ""
    return s if len(s) <= w else s[: w - 1] + "…"


def _value_formatter(values, unit: str, units_mode: str):
    """
    (header suffix, format function) for one column, honouring the units mode.

    'smart' delegates to format_si (per-value prefix, unit inline); 'aligned'
    picks one SI prefix for the whole column and puts it in the header, exactly
    like the main results table.
    """
    if units_mode == "aligned":
        exp, pfx = _aligned_prefix_for(list(values))
        return f"[{pfx}{unit}]", (lambda v: _fmt_aligned(v, exp))
    return "", (lambda v: format_si(v, unit))


def _fmt_plain(value: float, sig: int = 4) -> str:
    return "nan" if not math.isfinite(value) else f"{value:.{sig}g}"


def _pair_flag(pair) -> str:
    """Compact sign flag for a pair, mirroring _sign_flag on the diagonal."""
    flags = []
    im = pair.Z_ab.imag
    if math.isfinite(im):
        if im > 0:
            flags.append("ind")
        elif im < 0:
            flags.append("cap")
    if math.isfinite(pair.k) and abs(pair.k) > 1.0:
        flags.append("|k|>1")
    return ",".join(flags)


def _format_z_matrix(names, Zk, indent: str = "      ") -> str:
    """Render the G x G Z matrix with aligned columns (Re + jIm, in ohms)."""
    G = len(names)
    disp = [_trunc_str(n, 12) for n in names]
    cells = [[f"{Zk[i, j].real:.4g}{Zk[i, j].imag:+.4g}j" for j in range(G)]
             for i in range(G)]
    name_w = max(len(n) for n in disp)
    col_w = max([len(c) for row in cells for c in row] + [name_w])
    out = [indent + " " * name_w + "  "
           + "  ".join(f"{n:>{col_w}}" for n in disp)]
    for i, n in enumerate(disp):
        out.append(indent + f"{n:<{name_w}}" + "  "
                   + "  ".join(f"{c:>{col_w}}" for c in cells[i]))
    return "\n".join(out)


def _format_coupling_block(tc: "TraceConfig", file_label: str,
                           cres, units_mode: str) -> str:
    """
    Full mode-6 results block for one trace at the marker frequency:
    the Z matrix, the per-port self table, then one entry per pair.
    """
    names = list(cres.names)
    lines = [
        f"  [{tc.id}] {tc.label}  |  file: {file_label}  |  "
        f"{tc.port_descriptor()}",
        f"  Z matrix @ {cres.freq_hz / 1e9:.6g} GHz   (Ω, Re+jIm; "
        f"off-diagonal = mutual, every other port open)",
        _format_z_matrix(names, cres.Z_matrix),
    ]

    # --- self impedance table -------------------------------------------
    ports = list(cres.ports)
    r_sfx, fmt_r = _value_formatter([p.R_ohm for p in ports], "Ω", units_mode)
    l_sfx, fmt_l = _value_formatter([p.L_henry for p in ports], "H", units_mode)
    c_sfx, fmt_c = _value_formatter([p.C_farad for p in ports], "F", units_mode)
    NAME_W = max([len(_trunc_str(n, 14)) for n in names] + [4])
    NUM_W = 11
    lines.append("  self impedance (diagonal):")
    lines.append(
        f"      {'Port':<{NAME_W}}  {'R' + r_sfx:>{NUM_W}}  "
        f"{'L' + l_sfx:>{NUM_W}}  {'C' + c_sfx:>{NUM_W}}  "
        f"{'Q':>{NUM_W}}  Sign")
    for p in ports:
        lines.append(
            f"      {_trunc_str(p.name, 14):<{NAME_W}}  "
            f"{fmt_r(p.R_ohm):>{NUM_W}}  {fmt_l(p.L_henry):>{NUM_W}}  "
            f"{fmt_c(p.C_farad):>{NUM_W}}  {_fmt_plain(p.Q):>{NUM_W}}  "
            f"{_sign_flag(p)}")

    # --- per-pair coupling ----------------------------------------------
    pairs = list(cres.pairs)
    if not pairs:
        lines.append("  coupling: (only one measurement port -- "
                     "add Port 2 to get M and k)")
    else:
        m_sfx, fmt_m = _value_formatter([p.M_henry for p in pairs], "H",
                                        units_mode)
        cc_sfx, fmt_cc = _value_formatter([p.C_c_farad for p in pairs], "F",
                                          units_mode)
        lines.append("  coupling (mutual, all other measurement ports open):")
        for p in pairs:
            flag = _pair_flag(p)
            lines.append(
                f"      {p.name_a} x {p.name_b}:  "
                f"M{m_sfx} = {fmt_m(p.M_henry)}   "
                f"k = {_fmt_plain(p.k)}   "
                f"C_c{cc_sfx} = {fmt_cc(p.C_c_farad)}"
                + (f"   [{flag}]" if flag else ""))
            lines.append(
                f"          M/L({p.name_a}) = {_fmt_plain(p.M_over_La)} "
                f"({_fmt_plain(p.M_over_La_dB)} dB)   "
                f"M/L({p.name_b}) = {_fmt_plain(p.M_over_Lb)} "
                f"({_fmt_plain(p.M_over_Lb_dB)} dB)")
            for note in p.notes:
                lines.append(f"          note: {note}")

    # --- health check -----------------------------------------------------
    recip = cres.reciprocity_error
    checkable = any(math.isfinite(p.Z_ab.real) and math.isfinite(p.Z_ab.imag)
                    for p in pairs)
    if not checkable:
        hint = "nothing to check -- every mutual term is undefined"
    elif recip <= RECIPROCITY_WARN:
        hint = f"data looks reciprocal (alarm above {RECIPROCITY_WARN:g})"
    else:
        hint = ("LARGE -- Z_ab and Z_ba disagree; the input S-parameters are "
                "suspect (non-reciprocal or under-converged EM solve)")
    lines.append(f"  reciprocity error = {recip:.3g}  "
                 f"(max|Z_ab-Z_ba| / max|Z_ab| over the finite off-diagonal "
                 f"entries; {hint})")
    lines.append(
        "  legend: ind = Im(Z)>0 (inductive, read M) | "
        "cap = Im(Z)<0 (capacitive, read C_c) | "
        "R<0 = non-passive | M/L(x) = Norton injection ratio into x (not the "
        "exact current ratio |Z_ab/Z_aa|; equal only where wL_x >> R_x) | "
        "signs are physical (Cadence convention), never clipped")
    return "\n".join(lines)


# ============================================================================
# Placeholder-text helpers
# ============================================================================

PLACEHOLDER_FG = "#888888"


class PlaceholderEntry(ttk.Entry):
    """ttk.Entry that shows greyed-out hint text when empty and unfocused."""

    def __init__(self, master, placeholder: str = "", width: int = 42, **kwargs):
        kwargs.pop("textvariable", None)
        self._var = tk.StringVar()
        super().__init__(master, textvariable=self._var, width=width, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._show_if_empty()

    def _set_fg(self, color: str) -> None:
        try:
            self.configure(foreground=color)
        except tk.TclError:
            pass  # some themes may not honor this

    def _show_if_empty(self) -> None:
        if not self._var.get():
            self._var.set(self._placeholder)
            self._set_fg(PLACEHOLDER_FG)
            self._showing = True

    def _on_focus_in(self, _event=None) -> None:
        if self._showing:
            self._var.set("")
            self._set_fg("")
            self._showing = False

    def _on_focus_out(self, _event=None) -> None:
        self._show_if_empty()

    def get_value(self) -> str:
        return "" if self._showing else self._var.get().strip()

    def set_value(self, s: str) -> None:
        if s:
            self._var.set(s)
            self._set_fg("")
            self._showing = False
        else:
            self._var.set("")
            self._showing = False
            self._show_if_empty()

    def set_placeholder(self, placeholder: str) -> None:
        self._placeholder = placeholder
        if self._showing:
            self._var.set(placeholder)
        elif not self._var.get():
            self._show_if_empty()


class PlaceholderText(tk.Text):
    """tk.Text that shows greyed-out hint when empty and unfocused."""

    def __init__(self, master, placeholder: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self._fg_normal = self.cget("foreground") or "black"
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._show_if_empty()

    def _is_empty(self) -> bool:
        return not self.get("1.0", "end-1c").strip()

    def _show_if_empty(self) -> None:
        if self._is_empty():
            self.delete("1.0", "end")
            self.insert("1.0", self._placeholder)
            self.configure(foreground=PLACEHOLDER_FG)
            self._showing = True

    def _on_focus_in(self, _event=None) -> None:
        if self._showing:
            self.delete("1.0", "end")
            self.configure(foreground=self._fg_normal)
            self._showing = False

    def _on_focus_out(self, _event=None) -> None:
        self._show_if_empty()

    def get_value(self) -> str:
        return "" if self._showing else self.get("1.0", "end-1c")

    def set_value(self, s: str) -> None:
        self.delete("1.0", "end")
        if s:
            self.insert("1.0", s)
            self.configure(foreground=self._fg_normal)
            self._showing = False
        else:
            self._showing = False
            self._show_if_empty()

    def set_placeholder(self, placeholder: str) -> None:
        if self._showing:
            self.delete("1.0", "end")
            self._placeholder = placeholder
            self.insert("1.0", placeholder)
        else:
            self._placeholder = placeholder
            if self._is_empty():
                self._show_if_empty()


# Per-mode placeholder hints. Keyed by (field, mode) -> hint text.
# Fields: port_a, port_b, short_pairs, gnd, mp1_name, mp1_plus, mp1_minus,
#         mp2_name, mp2_plus, mp2_minus, mp_more
MODE_PLACEHOLDERS: dict[str, dict[int, str]] = {
    "port_a": {
        1: "e.g.  1   (signal port to drive)",
        2: "e.g.  1,2   (positive group; ports shorted internally)",
        3: "e.g.  1,2",
    },
    "port_b": {
        2: "e.g.  3,4   (negative group; ports shorted internally)",
        3: "e.g.  3,4",
    },
    "short_pairs": {
        3: "e.g.  3-4   or   3-4, 5-6   or   1-2-3-4   (chain dashes to short >2 ports)",
    },
    "gnd": {
        1: "e.g.  5   or   6:1:14   (V=0; put supply/VDD balls here too)",
        2: "e.g.  5   (optional; V=0 -- supply balls belong here too)",
        3: "e.g.  5   (optional; V=0 -- supply balls belong here too)",
        6: "e.g.  5:1:8   (optional; V=0 -- supply balls belong here too)",
    },
    "mp1_name": {
        6: "e.g.  tank   (shown in the legend and the Z matrix; optional)",
    },
    "mp1_plus": {
        6: "e.g.  1   or  1,3   (red probe; ports listed here are tied together)",
    },
    "mp1_minus": {
        6: "e.g.  2   (black probe; empty = referenced to GND). "
           "+ and - both set = differential self-inductance L_diff",
    },
    "mp2_name": {
        6: "e.g.  rx   (optional)",
    },
    "mp2_plus": {
        6: "e.g.  3   (leave Port 2 empty for self-impedance only, no coupling)",
    },
    "mp2_minus": {
        6: "e.g.  4   (black probe; empty = referenced to GND)",
    },
}

LABEL_PLACEHOLDER = "trace name shown in plot legend (optional)"

MP_MORE_PLACEHOLDER = (
    "# Extra measurement ports, one per line:\n"
    "#   <name> = <+ ports> / <- ports>\n"
    "vco = 5,7 / 6,8\n"
    "sense = 9 /\n"
    "# Ranges work on both sides. Empty = not used.\n"
)

MODE_PLACEHOLDERS["mp_more"] = {6: MP_MORE_PLACEHOLDER}

# Shown under the mode-6 plot checkboxes: the subplot grid is shared with the
# self curves, so the axis titles need reinterpreting on a mutual curve.
MUTUAL_CURVE_HINT = (
    "On a mutual curve the L(nH) subplot IS M in nH and C(pF) IS the coupling "
    "capacitance C_c; the k subplot is filled in for mutual curves only."
)

CUSTOM_PLACEHOLDER = (
    "# Mode 5: per-port termination spec (one directive per line)\n"
    "# Examples:\n"
    "1 signal A\n"
    "2 signal B\n"
    "3 ground\n"
    "4 lumped_to_gnd R=50\n"
    "5 short_to 6\n"
    "7 lumped_between 8 R=1 L=1n\n"
    "# Use the Help button for full syntax.\n"
)


# ============================================================================
# Main GUI
# ============================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PKG RLC Extractor")
        self.geometry("1500x900")

        self.files: list[FileEntry] = []
        self.traces: list[TraceConfig] = []
        self._next_trace_id = 1
        self._suppress_editor_sync = False

        self._build_ui()
        self._bind_events()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # Outer horizontal split
        outer = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(outer, width=460)
        outer.add(left, weight=0)

        right = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        outer.add(right, weight=1)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        parent.pack_propagate(False)

        # --- Files section ---
        files_frame = ttk.LabelFrame(parent, text="Loaded Files")
        files_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        btn_row = ttk.Frame(files_frame)
        btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(btn_row, text="Add File...", command=self._on_add_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Remove", command=self._on_remove_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Show Ports", command=self._on_show_ports
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.files_lb = tk.Listbox(files_frame, height=5, exportselection=False,
                                   activestyle="dotbox")
        self.files_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

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
        self.traces_lb = tk.Listbox(traces_frame, height=8, exportselection=False,
                                    activestyle="dotbox")
        self.traces_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        # --- Editor section ---
        ed = ttk.LabelFrame(parent, text="Edit Selected Trace")
        ed.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._build_editor(ed)

        # --- Global controls ---
        gc = ttk.LabelFrame(parent, text="Global Controls")
        gc.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        self._build_global_controls(gc)

    def _build_editor(self, parent: ttk.LabelFrame) -> None:
        # File combobox
        row = 0
        ttk.Label(parent, text="File:").grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_file_var = tk.StringVar()
        self.ed_file_cbo = ttk.Combobox(parent, textvariable=self.ed_file_var,
                                        state="readonly", width=40)
        self.ed_file_cbo.grid(row=row, column=1, columnspan=3, sticky="we", padx=2, pady=1)
        row += 1

        # Mode radio
        ttk.Label(parent, text="Mode:").grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        mode_frame = ttk.Frame(parent)
        mode_frame.grid(row=row, column=1, columnspan=3, sticky="w")
        self.ed_mode_var = tk.IntVar(value=1)
        # Mode 4 ("A ↔ B + VDD/GND") is retired: VDD is an AC ground, so it is
        # mode 2 with the supply ports merged into GND. Codes stay stable.
        for v, label in [(1, "Port(s) → GND"),
                         (2, "A ↔ B"),
                         (3, "A ↔ B + Short Pairs"),
                         (6, "+/- Ports / Coupling (M, k)"),
                         (5, "Custom (advanced)")]:
            ttk.Radiobutton(mode_frame, text=label, variable=self.ed_mode_var,
                            value=v, command=self._on_mode_changed
                            ).pack(side=tk.TOP, anchor="w")
        row += 1

        # Port A
        self.ed_porta_lbl = ttk.Label(parent, text="Signal / Port A:")
        self.ed_porta_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_porta = PlaceholderEntry(parent, width=42,
                                         placeholder=MODE_PLACEHOLDERS["port_a"][1])
        self.ed_porta.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Port B
        self.ed_portb_lbl = ttk.Label(parent, text="Port B:")
        self.ed_portb_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_portb = PlaceholderEntry(parent, width=42,
                                         placeholder=MODE_PLACEHOLDERS["port_b"][2])
        self.ed_portb.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Short pairs
        self.ed_short_lbl = ttk.Label(parent, text="Short Pairs:")
        self.ed_short_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_short = PlaceholderEntry(parent, width=42,
                                         placeholder=MODE_PLACEHOLDERS["short_pairs"][3])
        self.ed_short.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # --- Mode 6: measurement ports (probe pairs) ---
        # Two structured ports cover the common case; "More ports" takes any
        # number of extra "name = +ports / -ports" lines.
        self.ed_mp_widgets: list[tuple] = []   # (label, entry) pairs, mode 6

        def _mp_row(r, text, field):
            lbl = ttk.Label(parent, text=text)
            lbl.grid(row=r, column=0, sticky="e", padx=2, pady=1)
            ent = PlaceholderEntry(parent, width=42,
                                   placeholder=MODE_PLACEHOLDERS[field][6])
            ent.grid(row=r, column=1, columnspan=3, sticky="we", padx=2, pady=1)
            self.ed_mp_widgets.append((lbl, ent))
            return ent

        self.ed_mp1_name = _mp_row(row, "Port 1 name:", "mp1_name")
        row += 1
        self.ed_mp1_plus = _mp_row(row, "Port 1  (+):", "mp1_plus")
        row += 1
        self.ed_mp1_minus = _mp_row(row, "Port 1  (−):", "mp1_minus")
        row += 1
        self.ed_mp2_name = _mp_row(row, "Port 2 name:", "mp2_name")
        row += 1
        self.ed_mp2_plus = _mp_row(row, "Port 2  (+):", "mp2_plus")
        row += 1
        self.ed_mp2_minus = _mp_row(row, "Port 2  (−):", "mp2_minus")
        row += 1

        self.ed_mp_more_lbl = ttk.Label(parent, text="More ports:")
        self.ed_mp_more_lbl.grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        self.ed_mp_more = PlaceholderText(parent, width=42, height=5,
                                          font=("Consolas", 9),
                                          placeholder=MP_MORE_PLACEHOLDER)
        self.ed_mp_more.grid(row=row, column=1, columnspan=3, sticky="we",
                             padx=2, pady=1)
        row += 1

        # GND / VDD ports (VDD merged in: for AC small-signal they are the same)
        self.ed_gnd_lbl = ttk.Label(parent, text="GND / VDD (AC gnd):")
        self.ed_gnd_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_gnd = PlaceholderEntry(parent, width=42,
                                       placeholder=MODE_PLACEHOLDERS["gnd"][1])
        self.ed_gnd.grid(row=row, column=1, columnspan=3, sticky="we",
                         padx=2, pady=1)
        row += 1

        # Mode 6: which curves to plot
        self.ed_plot_lbl = ttk.Label(parent, text="Plot:")
        self.ed_plot_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_plot_frame = ttk.Frame(parent)
        self.ed_plot_frame.grid(row=row, column=1, columnspan=3, sticky="w",
                                padx=2, pady=1)
        self.ed_plot_self_var = tk.BooleanVar(value=True)
        self.ed_plot_mutual_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.ed_plot_frame, text="self",
                        variable=self.ed_plot_self_var).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(self.ed_plot_frame, text="mutual",
                        variable=self.ed_plot_mutual_var).pack(side=tk.LEFT)
        row += 1

        self.ed_mutual_hint = ttk.Label(parent, text=MUTUAL_CURVE_HINT,
                                        foreground=PLACEHOLDER_FG,
                                        justify=tk.LEFT, wraplength=320)
        self.ed_mutual_hint.grid(row=row, column=1, columnspan=3, sticky="w",
                                 padx=2, pady=(0, 2))
        row += 1

        # Custom text (Mode 5 only)
        self.ed_custom_lbl = ttk.Label(parent, text="Custom Spec:")
        self.ed_custom_lbl.grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        self.ed_custom_text = PlaceholderText(parent, width=42, height=8,
                                              font=("Consolas", 9),
                                              placeholder=CUSTOM_PLACEHOLDER)
        self.ed_custom_text.grid(row=row, column=1, columnspan=3, sticky="we",
                                 padx=2, pady=1)
        row += 1

        # Label
        ttk.Label(parent, text="Label:").grid(row=row, column=0, sticky="e",
                                              padx=2, pady=1)
        self.ed_label = PlaceholderEntry(parent, width=42,
                                         placeholder=LABEL_PLACEHOLDER)
        self.ed_label.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Color / linestyle
        ttk.Label(parent, text="Color idx:").grid(row=row, column=0, sticky="e",
                                                  padx=2, pady=1)
        self.ed_color_var = tk.IntVar(value=0)
        tk.Spinbox(parent, from_=0, to=len(COLORS) - 1, textvariable=self.ed_color_var,
                   width=5).grid(row=row, column=1, sticky="w", padx=2, pady=1)
        ttk.Label(parent, text="LS idx:").grid(row=row, column=2, sticky="e",
                                              padx=2, pady=1)
        self.ed_ls_var = tk.IntVar(value=0)
        tk.Spinbox(parent, from_=0, to=len(LINESTYLES) - 1, textvariable=self.ed_ls_var,
                   width=5).grid(row=row, column=3, sticky="w", padx=2, pady=1)
        row += 1

        # Apply button
        ttk.Button(parent, text="Apply to Trace", command=self._on_apply_editor
                   ).grid(row=row, column=1, columnspan=2, pady=4)

        parent.columnconfigure(1, weight=1)
        self._update_mode_visibility()

    def _build_global_controls(self, parent: ttk.LabelFrame) -> None:
        # RLC freq + band-fit on the same compact form
        ttk.Label(parent, text="RLC Freq (GHz):").grid(row=0, column=0, sticky="e",
                                                      padx=2, pady=1)
        self.rlc_freq_var = tk.StringVar(value="0.1")
        ttk.Entry(parent, textvariable=self.rlc_freq_var, width=10
                  ).grid(row=0, column=1, sticky="w", padx=2, pady=1)

        ttk.Label(parent, text="Fit f_min/f_max (GHz):").grid(row=1, column=0,
                                                              sticky="e", padx=2, pady=1)
        self.fit_fmin_var = tk.StringVar(value="0.1")
        self.fit_fmax_var = tk.StringVar(value="5.0")
        ttk.Entry(parent, textvariable=self.fit_fmin_var, width=8
                  ).grid(row=1, column=1, sticky="w", padx=2, pady=1)
        ttk.Entry(parent, textvariable=self.fit_fmax_var, width=8
                  ).grid(row=1, column=2, sticky="w", padx=2, pady=1)

        ttk.Label(parent, text="Fit Model:").grid(row=2, column=0, sticky="e",
                                                  padx=2, pady=1)
        self.fit_model_var = tk.StringVar(value="none")
        ttk.Combobox(parent, textvariable=self.fit_model_var,
                     values=["none", "auto", "inductor", "capacitor"],
                     state="readonly", width=10
                     ).grid(row=2, column=1, sticky="w", padx=2, pady=1)

        ttk.Button(parent, text="Calculate All & Plot",
                   command=self._on_calculate
                   ).grid(row=3, column=0, columnspan=2, pady=4, sticky="we", padx=2)
        ttk.Button(parent, text="Export CSV", command=self._on_export_csv
                   ).grid(row=3, column=2, pady=4, sticky="we", padx=2)
        ttk.Button(parent, text="Help", command=self._on_help
                   ).grid(row=3, column=3, pady=4, sticky="we", padx=2)

        parent.columnconfigure(1, weight=1)

    def _build_right_panel(self, parent: ttk.PanedWindow) -> None:
        results_frame = ttk.Frame(parent, height=180)
        plot_frame = ttk.Frame(parent)
        parent.add(results_frame, weight=0)
        parent.add(plot_frame, weight=1)

        header = ttk.Frame(results_frame)
        header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(header, text="Results", anchor="w").pack(side=tk.LEFT)
        ttk.Label(header, text="Units:").pack(side=tk.LEFT, padx=(12, 2))
        self.units_mode_var = tk.StringVar(value="smart")
        units_combo = ttk.Combobox(
            header, textvariable=self.units_mode_var,
            values=["smart", "aligned"], state="readonly", width=8,
        )
        units_combo.pack(side=tk.LEFT)
        units_combo.bind("<<ComboboxSelected>>",
                         lambda _e: self._on_units_mode_changed())
        self.results_text = ScrolledText(results_frame, height=10, wrap=tk.NONE,
                                         font=("Consolas", 9))
        self.results_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Tag for highlighting non-empty Sign flags. Configured once.
        self.results_text.tag_configure("flag", foreground="#b04000")

        self.plot = PlotPanel(plot_frame, on_marker_changed=self._on_marker_drag)
        self.plot.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _bind_events(self) -> None:
        self.files_lb.bind("<<ListboxSelect>>", lambda e: self._on_file_selected())
        self.traces_lb.bind("<<ListboxSelect>>", lambda e: self._on_trace_selected())

    # --------------------------------------------------------------- File ops

    def _on_add_file(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select Touchstone file(s)",
            filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                       ("All files", "*.*")],
        )
        for p in paths:
            try:
                ts = parse_touchstone(p)
            except Exception as e:
                messagebox.showerror("Parse error", f"{p}\n\n{e}")
                continue
            fe = FileEntry(ts)
            self.files.append(fe)
            self._append_result(f"Loaded {fe.info_str()}")
            for w in ts.parser_warnings:
                self._append_result(f"  WARN: {w}")
            # Auto-create a default trace bound to this file
            tc = self._make_default_trace(fe)
            self.traces.append(tc)
        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        # Select last-added file/trace for convenience
        if self.files:
            self.files_lb.selection_clear(0, tk.END)
            self.files_lb.selection_set(tk.END)
            self.files_lb.activate(tk.END)
        if self.traces:
            self.traces_lb.selection_clear(0, tk.END)
            self.traces_lb.selection_set(tk.END)
            self.traces_lb.activate(tk.END)
            self._on_trace_selected()

    def _on_remove_file(self) -> None:
        idx = self._sel_idx(self.files_lb)
        if idx is None:
            return
        fe = self.files.pop(idx)
        # Drop traces bound to this file
        self.traces = [t for t in self.traces if t.file_label != fe.label]
        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        self._append_result(f"Removed {fe.label}")

    def _on_show_ports(self) -> None:
        idx = self._sel_idx(self.files_lb)
        if idx is None:
            return
        fe = self.files[idx]
        self._append_result(f"\nPorts of {fe.label}:")
        for i, name in enumerate(fe.ts.port_names, 1):
            self._append_result(f"  {i:3d}: {name or '(unnamed)'}")

    def _on_file_selected(self) -> None:
        pass  # currently no-op

    # --------------------------------------------------------------- Trace ops

    def _make_default_trace(self, fe: FileEntry) -> TraceConfig:
        tc = TraceConfig(
            id=self._next_trace_id,
            file_label=fe.label,
            mode=1,
            port_a="1",
            label=f"{fe.label}_p1_to_gnd",
            color_idx=(self._next_trace_id - 1) % len(COLORS),
            ls_idx=((self._next_trace_id - 1) // len(COLORS)) % len(LINESTYLES),
        )
        self._next_trace_id += 1
        return tc

    def _on_add_trace(self) -> None:
        if not self.files:
            messagebox.showinfo("No file", "Add a file first.")
            return
        fe = self.files[self._sel_idx(self.files_lb) or 0]
        tc = self._make_default_trace(fe)
        self.traces.append(tc)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        self._on_trace_selected()

    def _on_remove_trace(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        self.traces.pop(idx)
        self._refresh_trace_list()

    def _on_duplicate_trace(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        src = self.traces[idx]
        new = TraceConfig(**{**src.__dict__,
                             "id": self._next_trace_id,
                             "label": src.label + "_copy",
                             "Z": None, "rlc": None, "fit": None, "fit_kind": "",
                             "Zmat": None, "mport_names": None,
                             "coupling": None})
        self._next_trace_id += 1
        self.traces.append(new)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        self._on_trace_selected()

    def _on_trace_selected(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = self.traces[idx]
        self._migrate_trace(tc)
        self._suppress_editor_sync = True
        try:
            self.ed_file_var.set(tc.file_label)
            self.ed_mode_var.set(tc.mode)
            self.ed_porta.set_value(tc.port_a)
            self.ed_portb.set_value(tc.port_b)
            self.ed_short.set_value(tc.short_pairs)
            self.ed_gnd.set_value(tc.gnd_ports)
            self.ed_mp1_name.set_value(tc.mp1_name)
            self.ed_mp1_plus.set_value(tc.mp1_plus)
            self.ed_mp1_minus.set_value(tc.mp1_minus)
            self.ed_mp2_name.set_value(tc.mp2_name)
            self.ed_mp2_plus.set_value(tc.mp2_plus)
            self.ed_mp2_minus.set_value(tc.mp2_minus)
            self.ed_mp_more.set_value(tc.mp_more or "")
            self.ed_plot_self_var.set(bool(tc.plot_self))
            self.ed_plot_mutual_var.set(bool(tc.plot_mutual))
            self.ed_label.set_value(tc.label)
            self.ed_color_var.set(tc.color_idx)
            self.ed_ls_var.set(tc.ls_idx)
            self.ed_custom_text.set_value(tc.custom_text or "")
        finally:
            self._suppress_editor_sync = False
        self._update_mode_visibility()

    def _migrate_trace(self, tc: TraceConfig) -> None:
        """Fold a retired mode-4 trace into mode 2 (VDD is an AC ground)."""
        if tc.migrate_legacy_mode():
            self._append_result(
                f"  [{tc.id}] {tc.label}: mode 4 (A↔B + VDD/GND) is retired; "
                f"migrated to mode 2 with VDD folded into GND "
                f"(GND = {tc.gnd_ports or '(none)'})")
            self._refresh_trace_list()

    def _on_mode_changed(self) -> None:
        self._update_mode_visibility()

    def _update_mode_visibility(self) -> None:
        mode = self.ed_mode_var.get()

        def show(widget, on):
            if on:
                widget.grid()
            else:
                widget.grid_remove()

        # Mode 5 (Custom) replaces the structured fields with the Custom Spec
        # text widget; mode 6 replaces them with the +/- measurement ports.
        ab_modes = mode in (1, 2, 3)
        coupling = mode == 6
        show(self.ed_porta_lbl, ab_modes)
        show(self.ed_porta, ab_modes)
        show(self.ed_portb_lbl, mode in (2, 3))
        show(self.ed_portb, mode in (2, 3))
        show(self.ed_short_lbl, mode == 3)
        show(self.ed_short, mode == 3)
        for lbl, ent in self.ed_mp_widgets:
            show(lbl, coupling)
            show(ent, coupling)
        show(self.ed_mp_more_lbl, coupling)
        show(self.ed_mp_more, coupling)
        show(self.ed_gnd_lbl, ab_modes or coupling)
        show(self.ed_gnd, ab_modes or coupling)
        show(self.ed_plot_lbl, coupling)
        show(self.ed_plot_frame, coupling)
        show(self.ed_mutual_hint, coupling)
        show(self.ed_custom_lbl, mode == 5)
        show(self.ed_custom_text, mode == 5)

        # Update placeholders to match the active mode
        self.ed_porta.set_placeholder(
            MODE_PLACEHOLDERS["port_a"].get(mode, ""))
        self.ed_portb.set_placeholder(
            MODE_PLACEHOLDERS["port_b"].get(mode, ""))
        self.ed_short.set_placeholder(
            MODE_PLACEHOLDERS["short_pairs"].get(mode, ""))
        self.ed_gnd.set_placeholder(
            MODE_PLACEHOLDERS["gnd"].get(mode, ""))
        for field, widget in (("mp1_name", self.ed_mp1_name),
                              ("mp1_plus", self.ed_mp1_plus),
                              ("mp1_minus", self.ed_mp1_minus),
                              ("mp2_name", self.ed_mp2_name),
                              ("mp2_plus", self.ed_mp2_plus),
                              ("mp2_minus", self.ed_mp2_minus),
                              ("mp_more", self.ed_mp_more)):
            widget.set_placeholder(MODE_PLACEHOLDERS[field].get(mode, ""))

    def _on_apply_editor(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            messagebox.showinfo("No trace", "Select a trace first.")
            return
        try:
            self._sync_editor_to_trace(self.traces[idx])
        except Exception as e:
            messagebox.showerror("Editor error", str(e))
            return
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)

    def _sync_editor_to_trace(self, tc: TraceConfig) -> None:
        tc.file_label = self.ed_file_var.get()
        tc.mode = int(self.ed_mode_var.get())
        tc.port_a = self.ed_porta.get_value()
        tc.port_b = self.ed_portb.get_value()
        tc.short_pairs = self.ed_short.get_value()
        tc.gnd_ports = self.ed_gnd.get_value()
        tc.mp1_name = self.ed_mp1_name.get_value()
        tc.mp1_plus = self.ed_mp1_plus.get_value()
        tc.mp1_minus = self.ed_mp1_minus.get_value()
        tc.mp2_name = self.ed_mp2_name.get_value()
        tc.mp2_plus = self.ed_mp2_plus.get_value()
        tc.mp2_minus = self.ed_mp2_minus.get_value()
        tc.mp_more = self.ed_mp_more.get_value().rstrip()
        tc.plot_self = bool(self.ed_plot_self_var.get())
        tc.plot_mutual = bool(self.ed_plot_mutual_var.get())
        tc.custom_text = self.ed_custom_text.get_value().rstrip()
        tc.label = self.ed_label.get_value() or f"trace_{tc.id}"
        tc.color_idx = int(self.ed_color_var.get())
        tc.ls_idx = int(self.ed_ls_var.get())

    # --------------------------------------------------------------- Calculate

    def _on_calculate(self) -> None:
        # Auto-sync editor for the currently selected trace before calculating.
        idx = self._sel_idx(self.traces_lb)
        if idx is not None:
            try:
                self._sync_editor_to_trace(self.traces[idx])
            except Exception as e:
                messagebox.showerror("Editor sync error", str(e))
                return
            self._refresh_trace_list()
            self.traces_lb.selection_set(idx)

        try:
            f_rlc_hz = parse_si(self.rlc_freq_var.get()) * 1e9
        except ValueError as e:
            messagebox.showerror("Bad RLC freq", str(e))
            return

        do_fit = self.fit_model_var.get() != "none"
        if do_fit:
            try:
                fmin_hz = float(self.fit_fmin_var.get()) * 1e9
                fmax_hz = float(self.fit_fmax_var.get()) * 1e9
                if fmin_hz >= fmax_hz:
                    raise ValueError("f_min must be < f_max")
            except ValueError as e:
                messagebox.showerror("Bad fit band", str(e))
                return

        plot_traces: list[PlotTrace] = []
        self._append_result("\n=== Calculate @ {:.4g} GHz ==="
                            .format(f_rlc_hz / 1e9))

        # First pass: compute Z and per-freq RLC; collect rows + fit_lines.
        result_rows: list[tuple] = []   # (tc, file_label, res)
        fit_lines: list[str] = []       # post-table fit summaries
        coupling_blocks: list[tuple] = []   # (tc, file_label, CouplingResult)
        for tc in self.traces:
            fe = self._file_by_label(tc.file_label)
            if fe is None:
                self._append_result(f"  [{tc.id}] {tc.label}: file '{tc.file_label}' not loaded")
                continue

            # Drop last run's matrix so a failed or re-moded trace can never
            # export stale coupling data.
            tc.Zmat = None
            tc.mport_names = None
            tc.coupling = None

            # Mode 6 produces a G x G Z matrix, not one curve; it gets its own
            # results block and expands into several plot curves.
            if tc.mode == 6:
                try:
                    cres = self._calculate_coupling_trace(
                        tc, fe, f_rlc_hz, plot_traces)
                except Exception as e:
                    tc.Z = None
                    self._append_result(f"  [{tc.id}] {tc.label}: ERROR {e}")
                    self._append_result(traceback.format_exc())
                    continue
                coupling_blocks.append((tc, fe.label, cres))
                if do_fit:
                    fit_lines.append(
                        f"  fit[{tc.id}]: skipped -- a band fit applies to one Z "
                        "curve, and a +/- coupling trace expands into several.")
                continue

            try:
                term = self._build_termination(tc, nports=fe.ts.nports)
                Z, warns = compute_z(fe.Y, fe.ts.freqs, term)
            except Exception as e:
                self._append_result(f"  [{tc.id}] {tc.label}: ERROR {e}")
                self._append_result(traceback.format_exc())
                continue
            for w in warns:
                self._append_result(f"    [{tc.id}] {w}")
            tc.Z = Z
            res = extract_rlc_at_freq(fe.ts.freqs, Z, f_rlc_hz)
            tc.rlc = res
            result_rows.append((tc, fe.label, res))

            fit_freqs = None
            fit_Z = None
            if do_fit:
                try:
                    model = self.fit_model_var.get()
                    if model == "auto":
                        which, fit = fit_auto(fe.ts.freqs, Z, fmin_hz, fmax_hz)
                    elif model == "inductor":
                        which, fit = "inductor", fit_inductor(fe.ts.freqs, Z, fmin_hz, fmax_hz)
                    else:
                        which, fit = "capacitor", fit_capacitor(fe.ts.freqs, Z, fmin_hz, fmax_hz)
                    tc.fit_kind = which
                    tc.fit = fit
                    fit_freqs = fe.ts.freqs[(fe.ts.freqs >= fmin_hz)
                                            & (fe.ts.freqs <= fmax_hz)]
                    if which == "inductor":
                        fit_Z = eval_inductor_model(fit, fit_freqs)
                        fit_lines.append(
                            f"  fit[{tc.id} {which}]: "
                            f"L={format_si(fit.L_henry, 'H')}, "
                            f"R_dc={format_si(fit.R_dc_ohm, 'Ω')}, "
                            f"R_ac={fit.R_ac_ohm_per_sqrtHz:.3g}Ω/√Hz, "
                            f"Q@center={fit.Q_at_center:.3g}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}")
                    else:
                        fit_Z = eval_capacitor_model(fit, fit_freqs)
                        srf_str = ("nan" if math.isnan(fit.SRF_hz)
                                   else format_si(fit.SRF_hz, 'Hz'))
                        fit_lines.append(
                            f"  fit[{tc.id} {which}]: "
                            f"C={format_si(fit.C_farad, 'F')}, "
                            f"R_esr={format_si(fit.R_esr_ohm, 'Ω')}, "
                            f"L_esl={format_si(fit.L_esl_henry, 'H')}, "
                            f"SRF={srf_str}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}")
                except Exception as e:
                    fit_lines.append(f"  fit[{tc.id}] ERROR: {e}")

            plot_traces.append(PlotTrace(
                label=tc.label,
                freqs=fe.ts.freqs,
                Z=Z,
                color_idx=tc.color_idx,
                ls_idx=tc.ls_idx,
                fit_freqs=fit_freqs,
                fit_Z=fit_Z,
            ))

        # Second pass: render the table, fit lines and coupling blocks.
        self._last_result_rows = result_rows
        self._last_fit_lines = fit_lines
        self._last_coupling_blocks = coupling_blocks
        self._render_results(result_rows, fit_lines, coupling_blocks)

        self.plot.set_traces(plot_traces)
        self.plot.set_marker_freq(f_rlc_hz)

    def _calculate_coupling_trace(self, tc: TraceConfig, fe: FileEntry,
                                  f_rlc_hz: float,
                                  plot_traces: list) -> object:
        """
        Mode 6: reduce to the G x G measurement-port Z matrix, extract the
        coupling result at the marker frequency, and append the expanded
        self / mutual curves to `plot_traces`.  Returns the CouplingResult.
        """
        term = self._build_termination(tc, nports=fe.ts.nports)
        Zmat, names, warns = compute_z_matrix(fe.Y, fe.ts.freqs, term)
        for w in warns:
            self._append_result(f"    [{tc.id}] {w}")
        if any("Rank-deficient" in w for w in warns):
            self._append_result(
                f"    [{tc.id}] (informational, not an error: a fully floating "
                "+/- structure is rank-deficient at every frequency and pinv "
                "handles it correctly)")
        if any("row and column of Z are NaN" in w for w in warns):
            self._append_result(
                f"    [{tc.id}] (this one IS an error in the port setup: the "
                "named measurement ports read nan because their probe current "
                "has nowhere to return. Give the port a '-' side, or add the "
                "ground ports the structure needs.)")
        if any("cancelled to roundoff" in w for w in warns):
            self._append_result(
                f"    [{tc.id}] (also an error in the port setup: the numbers "
                "below are shown but they are roundoff noise, not a "
                "measurement. Fix the ports before reading them.)")

        tc.Zmat = Zmat
        tc.mport_names = list(names)
        # Keep the scalar field populated with measurement port 1's self
        # impedance so anything expecting tc.Z keeps working. Zmat[:, 0, 0] is
        # a strided view, so copy it.
        tc.Z = np.ascontiguousarray(Zmat[:, 0, 0])
        tc.rlc = extract_rlc_at_freq(fe.ts.freqs, tc.Z, f_rlc_hz)
        cres = extract_coupling_at_freq(fe.ts.freqs, Zmat, names, f_rlc_hz)
        tc.coupling = cres

        curves = self._coupling_plot_traces(tc, fe, Zmat, names)
        if not curves:
            self._append_result(
                f"    [{tc.id}] both 'self' and 'mutual' are unchecked -- "
                "nothing plotted for this trace")
        plot_traces.extend(curves)
        return cres

    def _coupling_plot_traces(self, tc: TraceConfig, fe: FileEntry,
                              Zmat: np.ndarray, names: list) -> list:
        """
        Expand one mode-6 trace into plot curves: one self curve per
        measurement port and one mutual curve per unordered pair.  A mutual
        curve is just another complex Z array, so every subplot works on it --
        L(nH) then reads M in nH and C(pF) reads the coupling capacitance.
        """
        out: list = []
        G = len(names)
        n = 0
        if tc.plot_self:
            for g, nm in enumerate(names):
                out.append(_make_plot_trace(
                    label=_compose_curve_label(tc.label, nm),
                    freqs=fe.ts.freqs,
                    Z=np.ascontiguousarray(Zmat[:, g, g]),
                    color_idx=(tc.color_idx + n) % len(COLORS),
                    ls_idx=tc.ls_idx % len(LINESTYLES),
                ))
                n += 1
        if tc.plot_mutual and G >= 2:
            for a in range(G):
                for b in range(a + 1, G):
                    out.append(_make_plot_trace(
                        label=_compose_curve_label(
                            tc.label, f"{names[a]} x {names[b]}"),
                        freqs=fe.ts.freqs,
                        Z=np.ascontiguousarray(Zmat[:, a, b]),
                        color_idx=(tc.color_idx + n) % len(COLORS),
                        ls_idx=(tc.ls_idx + 1) % len(LINESTYLES),
                        aux={"k": _coupling_k_array(Zmat, fe.ts.freqs, a, b)},
                    ))
                    n += 1
        return out

    def _render_results(self, rows, fit_lines, coupling_blocks) -> None:
        units = self.units_mode_var.get()
        if rows:
            self._append_result(_format_results_table(rows, units))
            for fl in fit_lines:
                self._append_result(fl)
        for tc, file_label, cres in coupling_blocks:
            self._append_result("")
            self._append_result(
                _format_coupling_block(tc, file_label, cres, units))

    def _on_units_mode_changed(self) -> None:
        rows = getattr(self, "_last_result_rows", None)
        blocks = getattr(self, "_last_coupling_blocks", None)
        if not rows and not blocks:
            return
        self._append_result(
            f"\n--- re-rendered with units={self.units_mode_var.get()} ---")
        self._render_results(rows or [], getattr(self, "_last_fit_lines", []),
                             blocks or [])

    def _build_termination(self, tc: TraceConfig,
                           nports: int | None = None) -> TerminationSet:
        self._migrate_trace(tc)
        if tc.mode == 6:
            # nports lets the builder reject a port number the file does not
            # have (a one-digit typo in a '+/-' spec would otherwise silently
            # demote a differential probe to a ground-referenced one).
            return build_terminations_coupling(
                _collect_mports(tc), parse_port_range(tc.gnd_ports),
                nports=nports)
        if tc.mode == 5:
            return parse_custom_termination_text(tc.custom_text)
        a = parse_port_range(tc.port_a)
        b = parse_port_range(tc.port_b)
        g = parse_port_range(tc.gnd_ports)
        sp = parse_short_pairs(tc.short_pairs)
        if tc.mode == 1:
            return build_terminations_mode1(a, g)
        if tc.mode == 2:
            return build_terminations_mode2(a, b, g)
        if tc.mode == 3:
            return build_terminations_mode3(a, b, g, sp)
        raise ValueError(f"Unknown mode: {tc.mode}")

    def _on_marker_drag(self, freq_hz: float) -> None:
        self.rlc_freq_var.set(f"{freq_hz/1e9:.4g}")

    def _on_help(self) -> None:
        HelpWindow(self)

    # --------------------------------------------------------------- CSV

    def _on_export_csv(self) -> None:
        traces_with_data = [tc for tc in self.traces
                            if tc.Z is not None or tc.Zmat is not None]
        if not traces_with_data:
            messagebox.showinfo("No data", "Run Calculate first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                for tc in traces_with_data:
                    fe = self._file_by_label(tc.file_label)
                    if fe is None:
                        continue
                    fh.write(f"# Trace: {tc.label}\n")
                    fh.write(f"# File: {fe.label}, Mode: {tc.mode_name()}\n")
                    if tc.mode == 6 and tc.Zmat is not None:
                        _write_coupling_csv(fh, w, tc, fe)
                        fh.write("\n")
                        continue
                    w.writerow(["Freq_GHz", "Re_Z", "Im_Z", "abs_Z",
                                "R_mOhm", "L_nH", "C_pF", "Q"])
                    omega = 2 * np.pi * fe.ts.freqs
                    for k in range(len(fe.ts.freqs)):
                        z = tc.Z[k]
                        f = fe.ts.freqs[k]
                        r = z.real
                        im = z.imag
                        L = im / omega[k] * 1e9 if omega[k] != 0.0 else float("nan")
                        C = (-1.0 / (omega[k] * im) * 1e12) if (omega[k] != 0.0 and im != 0.0) else float("nan")
                        Q = im / r if r != 0.0 else float("nan")
                        w.writerow([f"{f/1e9:.6g}",
                                    f"{r:.6e}", f"{im:.6e}", f"{abs(z):.6e}",
                                    f"{r*1000:.6e}", f"{L:.6e}",
                                    f"{C:.6e}", f"{Q:.6e}"])
                    fh.write("\n")
            self._append_result(f"Exported CSV: {path}")
        except Exception as e:
            messagebox.showerror("Export error", str(e))

    # --------------------------------------------------------------- Misc

    def _refresh_file_list(self) -> None:
        self.files_lb.delete(0, tk.END)
        for fe in self.files:
            self.files_lb.insert(tk.END, fe.info_str())

    def _refresh_trace_list(self) -> None:
        sel = self._sel_idx(self.traces_lb)
        self.traces_lb.delete(0, tk.END)
        for tc in self.traces:
            self.traces_lb.insert(tk.END, tc.info_str())
        if sel is not None and sel < len(self.traces):
            self.traces_lb.selection_set(sel)

    def _refresh_file_combobox(self) -> None:
        self.ed_file_cbo["values"] = [fe.label for fe in self.files]

    def _file_by_label(self, label: str) -> Optional[FileEntry]:
        for fe in self.files:
            if fe.label == label:
                return fe
        return None

    def _sel_idx(self, lb: tk.Listbox) -> Optional[int]:
        sel = lb.curselection()
        if not sel:
            return None
        return int(sel[0])

    def _append_result(self, text: str) -> None:
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
