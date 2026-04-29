"""
pkg_rlc_gui.py  --  Tkinter GUI for PKG RLC Extractor.

Layout (horizontal PanedWindow):
  Left  ~ 460 px : Files / Traces / Editor / Global controls
  Right          : Results text (top) + plot panel (bottom)
                   (vertical PanedWindow, sash draggable by user)
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
    Ground,
    LumpedBetween,
    LumpedToGnd,
    Open,
    ShortPair,
    Signal,
    TerminationSet,
    TouchstoneData,
    Vdd,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    compute_z,
    eval_capacitor_model,
    eval_inductor_model,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    parse_port_range,
    parse_short_pairs,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
    format_si,
)
from pkg_rlc_plot import COLORS, LINESTYLES, PlotPanel, Trace as PlotTrace
from pkg_rlc_help import HelpWindow


# ============================================================================
# Helpers
# ============================================================================

SI_SUFFIXES = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}


def parse_si(s: str) -> float:
    """Parse '50', '1e-9', '1n', '0.5p', etc. -> float. Empty/0 raises ValueError."""
    s = s.strip()
    if not s:
        raise ValueError("Empty value")
    if s[-1] in SI_SUFFIXES:
        return float(s[:-1]) * SI_SUFFIXES[s[-1]]
    return float(s)


def parse_kv_rlc_params(tokens: list[str]) -> dict:
    """Parse 'R=50 L=1n C=1p' tokens into kwargs for y_series_rlc."""
    out = {"R": 0.0, "L": 0.0, "C": math.inf}
    for t in tokens:
        if "=" not in t:
            continue
        k, v = t.split("=", 1)
        k = k.strip().upper()
        if k not in out:
            raise ValueError(f"Unknown lumped param '{k}' (expected R, L, or C)")
        out[k] = parse_si(v)
    return out


def parse_custom_termination_text(text: str) -> TerminationSet:
    """
    Parse a text spec for Mode 5 (Custom).  One per-port directive per line, e.g.
        1 signal A
        2 signal B
        3 ground
        4 vdd
        5 lumped_to_gnd R=50
        6 short_to 7
        8 lumped_between 9 R=1 L=1n
        10 open
    Blank lines and lines starting with '#' are ignored.
    """
    ts = TerminationSet()
    for ln_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        try:
            port = int(parts[0])  # 1-based
        except ValueError:
            raise ValueError(f"Line {ln_no}: first token must be an integer port number")
        if port < 1:
            raise ValueError(f"Line {ln_no}: port must be >= 1, got {port}")
        if len(parts) < 2:
            raise ValueError(f"Line {ln_no}: missing termination kind")
        kind = parts[1].lower()
        rest = parts[2:]
        if kind in ("open",):
            ts.per_port[port - 1] = Open()
        elif kind in ("ground", "gnd"):
            ts.per_port[port - 1] = Ground()
        elif kind == "vdd":
            ts.per_port[port - 1] = Vdd()
        elif kind == "signal":
            grp = (rest[0].upper() if rest else "A")
            if grp not in ("A", "B"):
                raise ValueError(f"Line {ln_no}: signal group must be A or B")
            ts.per_port[port - 1] = Signal(grp)
        elif kind == "short_to":
            if not rest:
                raise ValueError(f"Line {ln_no}: short_to needs a partner port")
            other = int(rest[0])
            ts.couplings.append(ShortPair(port - 1, other - 1))
        elif kind == "lumped_to_gnd":
            params = parse_kv_rlc_params(rest)
            ts.per_port[port - 1] = LumpedToGnd(y_series_rlc(**params))
        elif kind == "lumped_between":
            if not rest:
                raise ValueError(f"Line {ln_no}: lumped_between needs a partner port")
            other = int(rest[0])
            params = parse_kv_rlc_params(rest[1:])
            ts.couplings.append(LumpedBetween(port - 1, other - 1,
                                              y_series_rlc(**params)))
        else:
            raise ValueError(f"Line {ln_no}: unknown termination kind '{kind}'")
    return ts


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
    """User-editable trace specification (1-based ports)."""
    id: int = 0
    file_label: str = ""
    mode: int = 1            # 1..5
    port_a: str = "1"
    port_b: str = ""
    short_pairs: str = ""
    gnd_ports: str = ""
    vdd_ports: str = ""
    custom_text: str = ""
    label: str = ""
    color_idx: int = 0
    ls_idx: int = 0
    # Computed (filled in after Calculate)
    Z: Optional[np.ndarray] = None
    rlc: Optional[object] = None
    fit_kind: str = ""
    fit: Optional[object] = None

    MODE_NAMES = {1: "GND", 2: "A↔B", 3: "A↔B+Short",
                  4: "A↔B+VDD", 5: "Custom"}

    def info_str(self) -> str:
        return (f"[{self.id}] {self.label}  |  "
                f"{self.file_label}  {self.MODE_NAMES.get(self.mode, '?')}")

    def port_descriptor(self) -> str:
        """Compact one-line port-config descriptor for the results table."""
        return _port_descriptor(self)


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
        return (f"M4: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"V:{_fmt_port_set(tc.vdd_ports)} G:{_fmt_port_set(tc.gnd_ports)}")
    if tc.mode == 5:
        text = (tc.custom_text or "").strip().replace("\n", " ")
        if len(text) > 28:
            text = text[:25] + "..."
        return f"M5: {text}" if text else "M5: (empty)"
    return f"M?: mode={tc.mode}"


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
    """Compact flag string: 'cap', 'ind', 'R<0', combinations, or ''."""
    flags = []
    if math.isfinite(res.L_henry) and res.L_henry < 0:
        flags.append("cap")
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

    saw_flag = False
    for tc, fl, res in rows:
        flag = _sign_flag(res)
        if flag:
            saw_flag = True
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

    if saw_flag:
        lines.append(
            "  legend: cap = Im(Z)<0 (past SRF if inductor) | "
            "R<0 = non-passive at this freq"
        )
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
# Field is one of: port_a, port_b, short_pairs, gnd, vdd
MODE_PLACEHOLDERS: dict[str, dict[int, str]] = {
    "port_a": {
        1: "e.g.  1   (signal port to drive)",
        2: "e.g.  1,2   (positive group; ports shorted internally)",
        3: "e.g.  1,2",
        4: "e.g.  1,2",
    },
    "port_b": {
        2: "e.g.  3,4   (negative group; ports shorted internally)",
        3: "e.g.  3,4",
        4: "e.g.  3,4",
    },
    "short_pairs": {
        3: "e.g.  3-4   or   3-4, 5-6   or   1-2-3-4   (chain dashes to short >2 ports)",
    },
    "gnd": {
        1: "e.g.  5   or   6:1:14   (ports tied to V=0)",
        2: "e.g.  5   (optional)",
        3: "e.g.  5   (optional)",
        4: "e.g.  5   (V=0 ports)",
    },
    "vdd": {
        4: "e.g.  7,8   (AC ground -- ideal supply)",
    },
}

LABEL_PLACEHOLDER = "trace name shown in plot legend (optional)"

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
        for v, label in [(1, "Port(s) → GND"),
                         (2, "A ↔ B"),
                         (3, "A ↔ B + Short Pairs"),
                         (4, "A ↔ B + VDD/GND"),
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

        # GND ports
        self.ed_gnd_lbl = ttk.Label(parent, text="GND Ports:")
        self.ed_gnd_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_gnd = PlaceholderEntry(parent, width=42,
                                       placeholder=MODE_PLACEHOLDERS["gnd"][1])
        self.ed_gnd.grid(row=row, column=1, columnspan=3, sticky="we",
                         padx=2, pady=1)
        row += 1

        # VDD ports
        self.ed_vdd_lbl = ttk.Label(parent, text="VDD Ports:")
        self.ed_vdd_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_vdd = PlaceholderEntry(parent, width=42,
                                       placeholder=MODE_PLACEHOLDERS["vdd"][4])
        self.ed_vdd.grid(row=row, column=1, columnspan=3, sticky="we",
                         padx=2, pady=1)
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
                             "Z": None, "rlc": None, "fit": None, "fit_kind": ""})
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
        self._suppress_editor_sync = True
        try:
            self.ed_file_var.set(tc.file_label)
            self.ed_mode_var.set(tc.mode)
            self.ed_porta.set_value(tc.port_a)
            self.ed_portb.set_value(tc.port_b)
            self.ed_short.set_value(tc.short_pairs)
            self.ed_gnd.set_value(tc.gnd_ports)
            self.ed_vdd.set_value(tc.vdd_ports)
            self.ed_label.set_value(tc.label)
            self.ed_color_var.set(tc.color_idx)
            self.ed_ls_var.set(tc.ls_idx)
            self.ed_custom_text.set_value(tc.custom_text or "")
        finally:
            self._suppress_editor_sync = False
        self._update_mode_visibility()

    def _on_mode_changed(self) -> None:
        self._update_mode_visibility()

    def _update_mode_visibility(self) -> None:
        mode = self.ed_mode_var.get()

        def show(widget, on):
            if on:
                widget.grid()
            else:
                widget.grid_remove()

        # In Mode 5 (Custom) the structured fields are replaced by the
        # Custom Spec text widget; hide all of them.
        structured = mode in (1, 2, 3, 4)
        show(self.ed_porta_lbl, structured)
        show(self.ed_porta, structured)
        show(self.ed_portb_lbl, mode in (2, 3, 4))
        show(self.ed_portb, mode in (2, 3, 4))
        show(self.ed_short_lbl, mode == 3)
        show(self.ed_short, mode == 3)
        show(self.ed_gnd_lbl, structured)
        show(self.ed_gnd, structured)
        show(self.ed_vdd_lbl, mode == 4)
        show(self.ed_vdd, mode == 4)
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
        self.ed_vdd.set_placeholder(
            MODE_PLACEHOLDERS["vdd"].get(mode, ""))

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
        tc.vdd_ports = self.ed_vdd.get_value()
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
        for tc in self.traces:
            fe = self._file_by_label(tc.file_label)
            if fe is None:
                self._append_result(f"  [{tc.id}] {tc.label}: file '{tc.file_label}' not loaded")
                continue
            try:
                term = self._build_termination(tc)
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

        # Second pass: render the table and fit lines.
        self._last_result_rows = result_rows
        self._last_fit_lines = fit_lines
        if result_rows:
            self._append_result(
                _format_results_table(result_rows, self.units_mode_var.get()))
            for fl in fit_lines:
                self._append_result(fl)

        self.plot.set_traces(plot_traces)
        self.plot.set_marker_freq(f_rlc_hz)

    def _on_units_mode_changed(self) -> None:
        rows = getattr(self, "_last_result_rows", None)
        if not rows:
            return
        self._append_result(
            f"\n--- re-rendered with units={self.units_mode_var.get()} ---")
        self._append_result(
            _format_results_table(rows, self.units_mode_var.get()))
        for fl in getattr(self, "_last_fit_lines", []):
            self._append_result(fl)

    def _build_termination(self, tc: TraceConfig) -> TerminationSet:
        a = parse_port_range(tc.port_a)
        b = parse_port_range(tc.port_b)
        g = parse_port_range(tc.gnd_ports)
        v = parse_port_range(tc.vdd_ports)
        sp = parse_short_pairs(tc.short_pairs)
        if tc.mode == 1:
            return build_terminations_mode1(a, g)
        if tc.mode == 2:
            return build_terminations_mode2(a, b, g)
        if tc.mode == 3:
            return build_terminations_mode3(a, b, g, sp)
        if tc.mode == 4:
            return build_terminations_mode4(a, b, g, v)
        if tc.mode == 5:
            return parse_custom_termination_text(tc.custom_text)
        raise ValueError(f"Unknown mode: {tc.mode}")

    def _on_marker_drag(self, freq_hz: float) -> None:
        self.rlc_freq_var.set(f"{freq_hz/1e9:.4g}")

    def _on_help(self) -> None:
        HelpWindow(self)

    # --------------------------------------------------------------- CSV

    def _on_export_csv(self) -> None:
        traces_with_data = [tc for tc in self.traces if tc.Z is not None]
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
                    fh.write(f"# File: {fe.label}, Mode: {tc.MODE_NAMES[tc.mode]}\n")
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
