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
from dataclasses import astuple, dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

from pkg_rlc_core import (
    CONN_KINDS,
    CONN_KINDS_WITH_RLC,
    DEFAULT_Z0,
    RECIPROCITY_WARN,
    SI_SUFFIXES,
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
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
    build_terminations_rows,
    compute_z,
    compute_z_matrix,
    eval_capacitor_model,
    eval_inductor_model,
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    inert_lumped_messages,
    parse_custom_termination_text,
    parse_kv_rlc_params,
    parse_mport_spec,
    parse_port_range,
    parse_short_pairs,
    parse_si,
    parse_touchstone,
    resolve_meas_ports,
    rows_to_dsl_text,
    dsl_text_to_rows,
    s_to_y,
    y_series_rlc,
    diagnose_touchstone,
    format_si,
    TouchstoneParseError,
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
        # The frequency span is here because it is the first thing anyone
        # checks against what they simulated, and the list line was the one
        # place it could have been seen without being shown at all.  It goes
        # ahead of M and Z0 on purpose: a Listbox has no horizontal scrollbar,
        # so a long file name clips the TAIL of this line (measured: a 37-char
        # name needs 476 px against a 444 px list), and of the four facts the
        # span is the one worth keeping.
        return (f"{self.label}  "
                f"(N={self.ts.nports}, {self.ts.freq_span_str()}, "
                f"M={len(self.ts.freqs)}, Z0={self.ts.z0:g}Ω)")


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
    # RETIRED: the free-text Mode 5 spec.  Kept only so an older config still
    # loads (see migrate_legacy_custom_text) and never written again -- the two
    # tables below are the live storage and the DSL text is a DERIVED view,
    # computed on demand.  Storing both would give the migration guard two
    # states it cannot tell apart.
    custom_text: str = ""
    # --- Mode 6 (+/- measurement ports / coupling) ---
    # `mports` is the live storage: one MeasPortRow per measurement port, port
    # specs kept as typed so '5:1:8' stays a range.  The mp1_*/mp2_*/mp_more
    # fields below are RETIRED -- Mode 6 used to offer two hard-coded ports and
    # a free-text box for the third onward -- and are kept only so an older
    # config still loads (see migrate_legacy_mports).
    mports: list = field(default_factory=list)   # list[MeasPortRow]
    mp1_name: str = ""
    mp1_plus: str = ""
    mp1_minus: str = ""
    mp2_name: str = ""
    mp2_plus: str = ""
    mp2_minus: str = ""
    mp_more: str = ""
    # --- Mode 5 (Custom): the connection table ---
    # conn_rows + extra_lines are the LIVE storage, exactly as `mports` is for
    # the measurement-port table.  `extra_lines` holds whatever the table
    # cannot represent (comments, hand-written directives, and a whole spec
    # whose meaning the table would change -- see migrate_legacy_custom_text).
    conn_rows: list = field(default_factory=list)   # list[ConnectionRow]
    extra_lines: str = ""
    plot_self: bool = True
    plot_mutual: bool = True
    # Drawn or not.  This gates the PLOT ONLY: a hidden trace is still
    # computed, still in the results table and still in the CSV export -- the
    # user asked to stop drawing it, not to stop measuring it.  Toggling it
    # replots from the cached Z / Zmat below, without re-running the reduction.
    enabled: bool = True
    label: str = ""
    color_idx: int = 0
    ls_idx: int = 0
    # Computed (filled in after Calculate)
    # `stale` says the config has been edited since Z was computed, so the
    # curve on screen is not what this row now describes.  Without it the
    # replot-from-cache path would quietly redraw last run's numbers under the
    # new port spec -- and with auto-apply, EVERY keystroke makes Z stale.
    stale: bool = False
    Z: Optional[np.ndarray] = None
    rlc: Optional[object] = None
    fit_kind: str = ""
    fit: Optional[object] = None
    # The evaluated fit overlay, cached so the curves can be rebuilt from this
    # trace alone.  Everything the plot needs has to live here, or toggling
    # visibility would silently drop the fit overlay off the traces that stay.
    fit_freqs: Optional[np.ndarray] = None
    fit_Z: Optional[np.ndarray] = None
    # Computed, mode 6 only
    Zmat: Optional[np.ndarray] = None          # (nfreqs, G, G) complex
    mport_names: Optional[list[str]] = None    # length G
    coupling: Optional[object] = None          # CouplingResult at marker freq

    MODE_NAMES = {1: "GND", 2: "A↔B", 3: "A↔B+Short",
                  4: "A↔B+VDD (retired)", 5: "Custom", 6: "+/- Coupling"}

    def info_str(self) -> str:
        # The ☑/☐ prefix is what makes visibility readable at a glance without
        # selecting the trace.  Measured (Microsoft YaHei UI 9): both glyphs are
        # 12 px, 16 px with the trailing space, so toggling does NOT shift the
        # rest of the line -- '✓' vs a space would have jittered by 8 px.  Cost
        # against the 444 px list: a typical entry goes 356 -> 372 px.
        # A trailing '*' means the drawn curve is older than this spec.
        return (f"{'☑' if self.enabled else '☐'} "
                f"[{self.id}] {self.label}  |  "
                f"{self.file_label}  {self.MODE_NAMES.get(self.mode, '?')}"
                f"{' *' if self.stale else ''}")

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

    def migrate_legacy_mports(self) -> bool:
        """
        Fold the retired mp1_*/mp2_*/mp_more fields into the `mports` table.

        The old Mode 6 editor had two hard-coded measurement ports plus a
        free-text box for the third onward; the table replaces all three.  The
        'name = +ports / -ports' lines are split textually rather than through
        parse_mport_spec, so port RANGES survive as ranges ('5:1:8' stays one
        cell) and a malformed old line migrates instead of raising during load.
        It will fail later, at Calculate, with a message that names it.

        Returns True when a migration actually happened.
        """
        if self.mports:
            return False
        rows: list = []
        for name, plus, minus in ((self.mp1_name, self.mp1_plus, self.mp1_minus),
                                  (self.mp2_name, self.mp2_plus, self.mp2_minus)):
            if (plus or "").strip() or (minus or "").strip():
                rows.append(MeasPortRow(name=(name or "").strip(),
                                        plus=(plus or "").strip(),
                                        minus=(minus or "").strip()))
        for line in _mport_more_lines(self.mp_more):
            name, sep, rest = line.partition("=")
            if not sep:
                name, rest = "", line
            plus, _slash, minus = rest.partition("/")
            rows.append(MeasPortRow(name=name.strip(), plus=plus.strip(),
                                    minus=minus.strip()))
        if not rows:
            return False
        self.mports = rows
        self.mp1_name = self.mp1_plus = self.mp1_minus = ""
        self.mp2_name = self.mp2_plus = self.mp2_minus = ""
        self.mp_more = ""
        return True

    def migrate_legacy_custom_text(self) -> bool:
        """
        Fold the retired free-text Mode 5 spec into the two tables.

        Guarded on THREE fields, not one: unlike `mports` there is no single
        field that proves the conversion happened -- a Mode 5 trace can
        legitimately have rows in one table and none in the other.

        MEANING IS NEVER CHANGED.  dsl_text_to_rows discards line order and
        rows_to_dsl_text re-emits every probe BEFORE every connection, so a
        spec whose 'signal' line follows a 'ground' on the same port would come
        back meaning something else ('3 ground / 3 signal A / 4 signal B'
        resolves port 3 as Signal directly and as Ground after one pass, and
        then resolve_meas_ports raises because group A is left with only a
        minus side).  When the round trip is not meaning-preserving the WHOLE
        spec is parked verbatim in extra_lines, which rows_to_dsl_text appends
        unchanged -- bit-identical to what the trace computed before.

        Returns True when a migration actually happened.
        """
        if self.mports or self.conn_rows or self.extra_lines:
            return False
        text = (self.custom_text or "").strip()
        if not text:
            return False
        mports, conn, extra, changed = _import_dsl_text(self.custom_text)
        if changed:
            self.mports, self.conn_rows = [], []
            self.extra_lines = self.custom_text.rstrip()
        else:
            self.mports, self.conn_rows, self.extra_lines = mports, conn, extra
        self.custom_text = ""
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
        tc.migrate_legacy_mports()
        parts = [_fmt_mport(r.name, r.plus, r.minus) for r in tc.mports
                 if r.plus.strip() or r.minus.strip()]
        body = " ".join(parts[:3]) if parts else "(empty)"
        if len(parts) > 3:
            body += f" +{len(parts) - 3}"
        return f"M6: {body} G:{_fmt_port_set(tc.gnd_ports)}"
    if tc.mode == 5:
        # No side effects here: unlike the mode-6 branch above this does NOT
        # call the migration, because that would consume it silently and the
        # user would never see the Results-pane message explaining what moved.
        #
        # With both tables empty the spec lives entirely in extra_lines (a
        # migration that kept an order-dependent spec verbatim, or an import
        # through 'Edit as text…').  Reporting '(no probe) C:0' for that is a
        # positive false claim in the very column the user reads to confirm
        # what was computed -- so fall back to showing the text, exactly as the
        # unmigrated custom_text case below it does.
        if not (tc.mports or tc.conn_rows):
            text = (tc.extra_lines or tc.custom_text or "").strip()
            if text:
                text = " ".join(text.split())
                return f"M5: {text[:25]}..." if len(text) > 28 else f"M5: {text}"
        parts = [_fmt_mport(r.name, r.plus, r.minus) for r in tc.mports
                 if r.plus.strip() or r.minus.strip()]
        body = " ".join(parts[:2]) if parts else "(no probe)"
        if len(parts) > 2:
            body += f" +{len(parts) - 2}"
        desc = f"M5: {body} C:{len(tc.conn_rows)}"
        # Rows AND kept text: the text is in force too and is emitted last, so
        # it wins.  Say it is there rather than describe the rows as the whole
        # spec.
        if (tc.extra_lines or "").strip():
            desc += "+txt"
        return desc
    return f"M?: mode={tc.mode}"


# ============================================================================
# Mode 5 helpers (the connection table <-> the DSL text)
# ============================================================================

def _import_dsl_text(text: str) -> tuple[list, list, str, bool]:
    """
    DSL text -> (mport_rows, conn_rows, extra_lines, meaning_changed).

    `meaning_changed` is True when routing the text through the tables would
    not compute the same thing, and the caller must then keep `text` verbatim
    in extra_lines instead of using the rows.  It is decided by comparing the
    RESOLVED TerminationSet before and after the round trip -- per-port
    termination types, couplings, and the measurement ports resolve_meas_ports
    produces -- because dsl_text_to_rows discards line order while the DSL is
    last-assignment-wins.

    Never raises.  On any internal failure it returns the same safe fallback
    ([], [], text, True), which is what makes "a malformed old spec migrates
    instead of raising during load" hold by construction rather than by the
    accident of dsl_text_to_rows happening to be total today.
    """
    try:
        mports, conn, extra = dsl_text_to_rows(text)
        if _dsl_meaning(text) == _dsl_meaning(
                rows_to_dsl_text(mports, conn, extra)):
            return mports, conn, extra, False
    except Exception:
        pass
    return [], [], text, True


def _dsl_meaning(text: str):
    """
    A comparable fingerprint of what a DSL spec computes, or None if it does
    not parse.  Used only to decide whether text may become rows.

    The port count handed to the resolver is one past the largest port the spec
    mentions: resolve_meas_ports only scans 0..n-1, so a smaller window would
    hide a difference at the far end of the spec.
    """
    try:
        term = parse_custom_termination_text(text)
    except Exception:
        return None
    ports = set(term.per_port)
    for cpl in term.couplings:
        ports.add(cpl.port_i)
        ports.add(cpl.port_j)
    n = (max(ports) + 1) if ports else 0
    try:
        mports = [(mp.name, tuple(mp.plus), tuple(mp.minus))
                  for mp in resolve_meas_ports(term, n)]
    except Exception:
        # A spec whose probes do not resolve still has a meaning -- "it fails"
        # -- and one side failing while the other does not IS a change.
        mports = None
    return (
        {p: type(t).__name__ for p, t in term.per_port.items()},
        [(type(c).__name__, c.port_i, c.port_j) for c in term.couplings],
        mports,
    )


def _ordering_diff_summary(text: str) -> str:
    """
    Name the ports whose termination changes when `text` goes through the
    tables.  Used only to explain why a spec was kept verbatim.
    """
    before = _dsl_meaning(text)
    try:
        after = _dsl_meaning(rows_to_dsl_text(*dsl_text_to_rows(text)))
    except Exception:
        after = None
    if before is None or after is None:
        return ""
    lines = []
    for port in sorted(set(before[0]) | set(after[0])):
        was = before[0].get(port, "Open")
        now = after[0].get(port, "Open")
        if was != now:
            lines.append(f"  port {port + 1}: {was} → {now}")
    return "\n".join(lines)


def _scan_count(term: TerminationSet, nports: Optional[int]) -> int:
    """
    How many ports to scan when the file's port count is unknown.

    resolve_meas_ports and the overview only look at 0..n-1, so with no file
    loaded the window has to reach the largest port the spec mentions -- but
    that number is NOT reported as a port count anywhere, because it is not one.
    """
    if nports is not None:
        return int(nports)
    ports = set(term.per_port)
    for cpl in term.couplings:
        ports.add(cpl.port_i)
        ports.add(cpl.port_j)
    return (max(ports) + 1) if ports else 0


# Bucket order in the port-overview strip.
_OVERVIEW_BUCKETS = ("probe", "ground", "vdd", "element", "shorted", "open")


def _port_bucket(term: TerminationSet, port0: int,
                 elem_ports: set, short_ports: set) -> str:
    """Which overview bucket one 0-based port falls into."""
    t = term.termination_of(port0)
    if isinstance(t, Signal):
        return "probe"
    if isinstance(t, Vdd):
        return "vdd"
    if isinstance(t, Ground):
        return "ground"
    if isinstance(t, LumpedToGnd) or port0 in elem_ports:
        return "element"
    if port0 in short_ports:
        return "shorted"
    return "open"


def _port_overview_text(term: Optional[TerminationSet],
                        nports: Optional[int]) -> str:
    """
    'Ports (45): 4 probe · 8 ground · 1 element · 32 open'.

    With no file loaded the port count is unknown, so only the ports the rows
    mention are counted and the 'open' bucket is dropped entirely -- an open
    port is one the file has and the spec did not name, which cannot be known
    without the file.  Guessing nports from the largest port mentioned would
    invent a number that looks authoritative.
    """
    header = (f"Ports ({nports})" if nports is not None
              else "Ports (no file selected)")
    if term is None:
        return f"{header}: —"

    elem_ports: set = set()
    short_ports: set = set()
    for cpl in term.couplings:
        target = elem_ports if isinstance(cpl, LumpedBetween) else short_ports
        target.add(cpl.port_i)
        target.add(cpl.port_j)

    n = _scan_count(term, nports)
    scan = range(n) if nports is not None else sorted(
        set(term.per_port) | elem_ports | short_ports)
    counts = dict.fromkeys(_OVERVIEW_BUCKETS, 0)
    for i in scan:
        counts[_port_bucket(term, i, elem_ports, short_ports)] += 1
    if nports is None:
        counts["open"] = 0

    parts = [f"{counts[b]} {b}" for b in _OVERVIEW_BUCKETS if counts[b]]
    return f"{header}: " + (" · ".join(parts) if parts else "(no rows yet)")


def _rlc_echo(row: ConnectionRow) -> str:
    """
    'port 13 → GND: 5 mΩ + 500 pH + 1 uF' for one element row, or "".

    Design §2 wanted this per row; a static column costs ~140 px the 431 px
    editor does not have, so it lands in the validation strip instead.  It
    catches the same error: '5m' and '5M' are one shift key and nine orders of
    magnitude apart, and only the parsed value shows which one was typed.
    """
    if row.kind not in CONN_KINDS_WITH_RLC or not row.ports.strip():
        return ""
    vals = {k: getattr(row, k).strip() for k in ("R", "L", "C")}
    if any(any(ch.isspace() for ch in v) for v in vals.values()):
        # rows_to_dsl_text refuses these (see _rlc_tokens): the DSL is
        # whitespace-tokenised, so 'R=5 m' would compute 5 Ω while this
        # function -- which re-parses the raw cell as ONE token -- would echo
        # '5 mΩ' beside it.  Say nothing rather than say something else.
        return ""
    try:
        params = parse_kv_rlc_params(
            [f"{k}={v}" for k, v in vals.items() if v])
    except Exception:
        return ""
    bits = []
    if row.R.strip():
        bits.append(format_si(params["R"], "Ω"))
    if row.L.strip():
        bits.append(format_si(params["L"], "H"))
    if row.C.strip():
        bits.append(format_si(params["C"], "F"))
    if not bits:
        return ""
    to = row.to.strip() if row.kind == "rlc_between" else "GND"
    return f"port {row.ports.strip()} → {to or '?'}: " + " + ".join(bits)


def _validation_messages(mport_rows: Sequence, conn_rows: Sequence,
                         extra_lines: str = "",
                         nports: Optional[int] = None) -> list[str]:
    """
    Everything worth saying about the two tables, worst first.

    MUST NOT RAISE.  It runs from a Tk variable trace on every keystroke, where
    a raised exception does not reach a handler we control -- Tk prints it to
    stderr and the GUI carries on showing a stale, wrong strip.  Half-typed
    cells raise routinely: parse_port_range rejects '5:', '5:1:' and '-'.
    """
    msgs: list[str] = []
    term: Optional[TerminationSet] = None
    try:
        term = build_terminations_rows(mport_rows, conn_rows, extra_lines,
                                       nports=nports)
    except Exception as e:
        msgs.append(f"⚠ {e}")

    # Rows that are not blank but contribute nothing. rows_to_dsl_text skips a
    # connection row with an empty Port silently -- no error, no line, no hint
    # that the R=50 sitting next to it was thrown away.
    for i, row in enumerate(conn_rows, start=1):
        if row.is_blank():
            continue
        if not row.ports.strip():
            msgs.append(f"⚠ connection row {i} has values but no Port "
                        "-- it does nothing.")
        elif (row.kind in CONN_KINDS_WITH_RLC
                and not (row.R.strip() or row.L.strip() or row.C.strip())):
            # The mirror image of the check above, and the one that hurts:
            # y_series_rlc(R=0, L=0, C=inf) is 1/0, so the element is an
            # infinite-admittance short and Z comes out NaN at EVERY frequency
            # -- with a warning that blames the measurement port's return path
            # rather than the empty cells.
            msgs.append(f"⚠ connection row {i} ({row.kind}) has no R, L or C "
                        "-- a lumped element with no value is a 0 Ω short and "
                        "the result is NaN everywhere.")
    for i, row in enumerate(mport_rows, start=1):
        if row.is_blank() or row.plus.strip():
            continue
        if row.minus.strip():
            msgs.append(f"⚠ measurement port row {i} has a '−' side but no "
                        "'+' side -- it does nothing.")
        else:
            # Name typed, ports never filled in. is_blank() is False, so
            # neither branch used to see it and the row vanished silently.
            msgs.append(f"⚠ measurement port row {i} has a name but no ports "
                        "-- it does nothing.")

    if term is not None:
        # Overlaps first: grounding a probe is what CAUSES 'no measurement
        # port defined', so naming the cause above the consequence.
        msgs.extend(_probe_ground_messages(mport_rows, term))
        msgs.extend(_measured_port_messages(mport_rows, term, nports))
        # An element the reduction annihilates (shorted out / both ends
        # grounded). Without this the strip showed the ✓ ECHO for it -- a green
        # tick reading '✓ port 5 → 6: 20 Ω' next to an answer that does not
        # depend on the 20 at all. The echoes below are only reached when msgs
        # is empty, so appending here is what suppresses that.
        msgs.extend(inert_lumped_messages(term))

    if msgs:
        return msgs

    # One message per element row, not one line naming the first and counting
    # the rest: the echo exists to catch '5m' typed as '5M', which is a
    # property of the row it is on. _validation_strip_text caps the strip;
    # Calculate prints the full list to the Results pane.
    echoes = ["✓ " + e for e in (_rlc_echo(r) for r in conn_rows) if e]
    return echoes or ["✓ no problems found"]


def _measured_port_messages(mport_rows: Sequence, term: TerminationSet,
                            nports: Optional[int]) -> list[str]:
    """
    Every way the measurement ports that will be MEASURED differ from the rows.

    Comparing the row count to len(resolve_meas_ports(...)) catches all of the
    merges at once without duplicating build_terminations_coupling's rule list:
    'A' + 'B' collapse (B is the legacy minus side of A) and two rows sharing a
    name do too.  Mode 6's identical-looking table RAISES on both; the Mode 5
    table keeps the DSL's permissive behaviour, and this strip is where that
    difference becomes visible instead of silent.

    It also catches the two directions the row count cannot show at all:
    NOTHING resolves (Calculate would raise), and MORE resolve than the table
    has rows -- which can only come from the lines kept as text, and is how a
    trace silently acquires a second probe and routes to the coupling path.
    """
    rows = [r for r in mport_rows if not r.is_blank() and r.plus.strip()]
    try:
        resolved = resolve_meas_ports(term, _scan_count(term, nports))
    except Exception as e:
        return [f"⚠ {e}"]
    if not resolved:
        return ["⚠ no measurement port defined -- add a row to the "
                "measurement-port table and fill in its '+' side."]
    if len(resolved) > len(rows):
        hidden = [mp.name for mp in resolved
                  if mp.name not in {r.name.strip() for r in rows}]
        extra_n = len(resolved) - len(rows)
        named = f" ('{hidden[0]}')" if len(hidden) == 1 else ""
        head = ("1 measurement port is" if len(resolved) == 1
                else f"{len(resolved)} measurement ports are")
        return [f"⚠ {head} measured but the measurement-port table has "
                f"{len(rows)} row(s): {extra_n} more{named} from the lines "
                "kept as text. Open 'Edit as text…' to see them."]
    if len(rows) < 2 or len(resolved) >= len(rows):
        return []
    head = (f"⚠ {len(rows)} measurement-port rows define only "
            f"{len(resolved)} measurement port(s)")
    names = [r.name.strip() for r in rows]
    upper = {n.upper() for n in names}
    if "A" in upper and "B" in upper:
        return [f"{head}: 'B' is the legacy minus side of 'A'. "
                "Rename one of them."]
    dupes = sorted({n for n in names if n and names.count(n) > 1})
    if dupes:
        return [f"{head}: the name '{dupes[0]}' is used twice, so both rows "
                "feed one measurement port. Rename one."]
    return [f"{head}."]


def _probe_ground_messages(mport_rows: Sequence,
                           term: TerminationSet) -> list[str]:
    """
    Ports listed as a probe that a later connection row grounds.

    This is legal and pinned: the rows path emits probes before connections, so
    ground wins, exactly as build_terminations_mode1/2/3 always have.
    build_terminations_coupling raises on the same overlap.  Do not unify them
    -- just say which one happened.
    """
    probe_ports: set[int] = set()
    for row in mport_rows:
        for spec in (row.plus, row.minus):
            try:
                probe_ports.update(parse_port_range(spec))
            except Exception:
                continue
    hit = sorted(p for p in probe_ports
                 if isinstance(term.termination_of(p - 1), (Ground, Vdd)))
    if not hit:
        return []
    listed = ", ".join(str(p) for p in hit)
    noun = "port" if len(hit) == 1 else "ports"
    verb = "is" if len(hit) == 1 else "are"
    return [f"⚠ {noun} {listed} {verb} both a probe and a ground row "
            "-- the ground row wins."]


def _extra_lines_indicator(extra_lines: str) -> str:
    """
    '(+2 lines kept as text)' for the Connections caption, or "".

    extra_lines is the one part of the spec with no widget of its own, and
    rows_to_dsl_text emits it LAST -- so it wins over everything in the two
    tables.  After a verbatim-kept import the tables can be empty while a
    hidden block of DSL decides the whole answer; this is what says so without
    costing a row of the form.
    """
    n = len([ln for ln in (extra_lines or "").splitlines() if ln.strip()])
    if not n:
        return ""
    return f"(+{n} line{'' if n == 1 else 's'} kept as text)"


# How many messages the strip shows before it defers to the Results pane.
# _on_calculate uses the same number to decide what to print there, so the
# "… +N more (see Results)" pointer names something that is actually written.
VALIDATION_STRIP_LINES = 2


def _validation_strip_text(msgs: Sequence[str],
                           limit: int = VALIDATION_STRIP_LINES) -> str:
    """
    Cap the strip. Measured uncapped: 21 / 38 / 55 / 89 / 140 px at 1 / 2 / 3 /
    5 / 8 lines, and 140 px is 41% of the editor canvas.  The overflow goes to
    the Results pane, which scrolls -- _on_calculate writes the full list there.
    """
    msgs = list(msgs)
    if len(msgs) <= limit:
        return "\n".join(msgs)
    return "\n".join(msgs[:limit]
                     + [f"… +{len(msgs) - limit} more (see Results)"])


# ============================================================================
# Mode 6 helpers (+/- measurement ports, coupling)
# ============================================================================

def _duplicate_trace_config(src: "TraceConfig", new_id: int) -> "TraceConfig":
    """
    Copy a trace for the Duplicate button, dropping last run's results.

    `mports` and `conn_rows` are LISTS of dataclasses, so both are copied
    element-wise.  A plain `TraceConfig(**src.__dict__)` hands both traces the
    same list object, and editing the copy's measurement ports or connections
    then silently edits the original's -- a bug with no visible symptom until
    two curves quietly agree.
    """
    return TraceConfig(**{**src.__dict__,
                          "id": new_id,
                          "label": src.label + "_copy",
                          "mports": [replace(r) for r in src.mports],
                          "conn_rows": [replace(r) for r in src.conn_rows],
                          "Z": None, "rlc": None, "fit": None, "fit_kind": "",
                          "fit_freqs": None, "fit_Z": None,
                          "Zmat": None, "mport_names": None,
                          "coupling": None, "stale": False})


def _config_signature(tc: "TraceConfig") -> tuple:
    """
    Everything that feeds _build_termination, as a comparable value.

    Auto-apply writes the editor into the trace on every keystroke, so "did
    this edit invalidate the computed curve?" has to be answerable cheaply.
    Only fields that change the ANSWER belong here -- colour, linestyle and the
    plot checkboxes change the picture and are handled by _draw_signature,
    which triggers a replot from cache rather than marking anything stale.
    """
    return (tc.file_label, tc.mode, tc.port_a, tc.port_b, tc.short_pairs,
            tc.gnd_ports, tc.extra_lines,
            tuple((r.name, r.plus, r.minus) for r in tc.mports),
            tuple(astuple(r) for r in tc.conn_rows))


def _draw_signature(tc: "TraceConfig") -> tuple:
    """
    What changes the picture without changing the numbers.

    `label` is deliberately absent: it reaches the plot only as a legend name,
    and including it would re-render every subplot on every keystroke of the
    Label field.  These five all change discretely (a click), so a replot per
    change is free.
    """
    return (tc.enabled, tc.color_idx, tc.ls_idx, tc.plot_self, tc.plot_mutual)


def _collect_mports(tc: "TraceConfig") -> list[tuple[str, list[int], list[int]]]:
    """
    Measurement-port table -> the (name, plus_1based, minus_1based) triples
    that build_terminations_coupling expects.  Ports stay 1-based here; the
    core builder is the 1-based/0-based boundary.
    """
    tc.migrate_legacy_mports()
    out: list[tuple[str, list[int], list[int]]] = []
    for idx, row in enumerate(tc.mports, start=1):
        plus = row.plus.strip()
        minus = row.minus.strip()
        if not plus:
            if minus:
                label = f"'{row.name.strip()}'" if row.name.strip() else f"row {idx}"
                raise ValueError(
                    f"Measurement port {label} has a '-' side but no '+' side; "
                    "the red probe must touch at least one port.")
            continue
        out.append((row.name.strip(),
                    parse_port_range(plus), parse_port_range(minus)))

    if not out:
        raise ValueError(
            "No measurement ports defined: add a row to the measurement-port "
            "table and fill in its '+' side.")
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
            # Every row here IS on the plot: _render_results filters the hidden
            # traces out before calling, and names them on one line under the
            # table instead.  A row for a curve that is not drawn reads as a
            # duplicate of the one that is -- which on two similar traces (the
            # normal way a hidden one comes about, via Duplicate) is exactly
            # what it looks like.
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
                     "add a second measurement-port row to get M and k)")
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

    def on_change(self, callback) -> None:
        """
        Call `callback` whenever the contents change.

        Deliberately a method and not a constructor argument: __init__ ends
        with _show_if_empty(), which SETS the variable, so a callback attached
        during construction fires once before the caller has finished building
        itself.  Registering afterwards makes that impossible rather than
        merely unlikely.

        The callback still sees the placeholder being written and erased -- it
        must read get_value(), never _var.get(), and must not act synchronously
        on what it reads (_show_if_empty writes the variable BEFORE it sets
        _showing, so a synchronous reader sees the hint as user input).
        """
        self._var.trace_add("write", lambda *_a: callback())


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


# ============================================================================
# StylePicker -- the colour / linestyle chooser
# ============================================================================
#
# Replaces two integer Spinboxes ("Color idx: 7", "LS idx: 2") that gave the
# user no way to know what 7 looked like.  Three design points:
#
#  * The palette EXPANDS IN PLACE, it is not a popup Toplevel.  A grab_set
#    window that outlives its opener blocks event delivery, and update() then
#    never returns -- the exact failure mode already recorded for the editor's
#    scrollbar limit cycle, i.e. the GUI and the test suite hang together.
#    Expanding in place reuses the form's existing Canvas scrolling instead
#    (_refresh_editor_scrollregion(preserve=True)) and needs no grab, no
#    focus_force, no off-screen placement clamp.
#  * The stored value stays an INDEX into COLORS / LINESTYLES.  A free colour
#    chooser looks like an upgrade but _coupling_plot_traces derives the colours
#    of a mode-6 trace's expanded curves as (color_idx + n) % len(COLORS): an
#    arbitrary RGB has no "next colour", so all six curves would come out the
#    same.  This is a picker change only -- pkg_rlc_plot is untouched.
#  * All sizes are in units of the default font's linespace, never in pixels.
#    The editor's column budget is already documented as a 100%-font number
#    that does not survive 150% DPI; a pixel-sized Canvas would repeat that.


class StylePicker(ttk.Frame):
    """
    A line preview that expands into a 12-colour + 4-linestyle palette.

    The preview draws the real colour and the real dash pattern, so what is on
    the button is what the plot draws -- with one honest exception it labels
    itself: a mode 5/6 trace with G measurement ports expands into several
    curves consuming CONSECUTIVE palette slots, so `set_span(n)` makes the
    preview show the whole run and the '×n'.  Without that, picking a
    distinct-looking colour for a coupling trace tells you nothing about the
    five curves either side of it, and two traces can collide invisibly.
    """

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, **kw)
        self._color_idx = 0
        self._ls_idx = 0
        self._span = 1
        self._on_change = on_change
        self._expanded = False
        self._cells: dict = {}

        u = self._u = max(12, tkfont.Font(font="TkDefaultFont")
                          .metrics("linespace"))
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="w")
        self._preview = tk.Canvas(head, width=5 * u, height=2 * u,
                                  highlightthickness=1,
                                  highlightbackground="#a0a0a0",
                                  # A bare Canvas has takefocus='' and Tk's
                                  # traversal heuristic skips a widget with no
                                  # key bindings, so without this the style
                                  # control drops out of the Tab order that the
                                  # two Spinboxes were in.
                                  takefocus=True)
        self._preview.pack(side=tk.LEFT)
        self._preview.bind("<Button-1>", lambda e: self.toggle())
        self._preview.bind("<Return>", lambda e: self.toggle())
        self._preview.bind("<space>", lambda e: self.toggle())
        self._preview.bind("<Down>", lambda e: self.expand())
        self._preview.bind("<Escape>", lambda e: self.collapse())
        self._preview.bind("<FocusIn>", lambda e: self._preview.configure(
            highlightbackground="#0078d7"))
        self._preview.bind("<FocusOut>", lambda e: self._preview.configure(
            highlightbackground="#a0a0a0"))
        self._arrow = ttk.Label(head, text="▸")
        self._arrow.pack(side=tk.LEFT, padx=(4, 0))
        self._arrow.bind("<Button-1>", lambda e: self.toggle())

        self._palette = ttk.Frame(self)
        self._build_palette()
        self._palette.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self._palette.grid_remove()
        self._redraw_preview()

    # -------- palette --------

    def _build_palette(self) -> None:
        u = self._u
        colors = ttk.Frame(self._palette)
        colors.grid(row=0, column=0, sticky="w")
        # 2 rows of 6 rather than one row of 12: 12 x 2u would be ~390 px and
        # the editor canvas viewport is 431 px before the label column.
        for i, c in enumerate(COLORS):
            cv = tk.Canvas(colors, width=2 * u, height=u, highlightthickness=1,
                           highlightbackground="#d0d0d0", background=c,
                           cursor="hand2")
            cv.grid(row=i // 6, column=i % 6, padx=1, pady=1)
            cv.bind("<Button-1>", lambda e, k=i: self._choose(color=k))
            self._cells[("c", i)] = cv

        styles = ttk.Frame(self._palette)
        styles.grid(row=1, column=0, sticky="w", pady=(3, 0))
        for i, ls in enumerate(LINESTYLES):
            cv = tk.Canvas(styles, width=3 * u, height=u, highlightthickness=1,
                           highlightbackground="#d0d0d0", background="white",
                           cursor="hand2")
            cv.grid(row=0, column=i, padx=1)
            cv.create_line(3, u // 2, 3 * u - 3, u // 2, width=2, fill="black",
                           dash=_tk_dash(ls, 2))
            cv.bind("<Button-1>", lambda e, k=i: self._choose(ls=k))
            self._cells[("l", i)] = cv

    def _choose(self, color: int | None = None, ls: int | None = None) -> None:
        if color is not None:
            self._color_idx = color
        if ls is not None:
            self._ls_idx = ls
        self._redraw_preview()
        self._mark_selection()
        if self._on_change is not None:
            self._on_change()

    def _mark_selection(self) -> None:
        for (kind, i), cv in self._cells.items():
            sel = (self._color_idx if kind == "c" else self._ls_idx) == i
            cv.configure(highlightbackground="#000000" if sel else "#d0d0d0",
                         highlightthickness=2 if sel else 1)

    # -------- preview --------

    def _redraw_preview(self) -> None:
        u = self._u
        cv = self._preview
        cv.delete("all")
        w = 5 * u
        color = COLORS[self._color_idx % len(COLORS)]
        dash = _tk_dash(LINESTYLES[self._ls_idx % len(LINESTYLES)], 2)
        cv.create_line(4, u * 0.55, w - 4, u * 0.55, width=2, fill=color,
                       dash=dash)
        if self._span > 1:
            # The consecutive slots this trace will actually occupy, so the
            # preview cannot claim a coupling trace is one colour.
            shown = min(self._span, 6)
            cw = (w - 8) / shown
            for j in range(shown):
                x0 = 4 + j * cw
                cv.create_rectangle(x0, u * 1.05, x0 + cw - 2, u * 1.55,
                                    fill=COLORS[(self._color_idx + j)
                                                % len(COLORS)],
                                    outline="")
            cv.create_text(w - 4, u * 1.75, anchor="se",
                           text=f"×{self._span}", font=("TkDefaultFont", 7),
                           fill="#606060")

    # -------- public API --------

    def set_span(self, n: int) -> None:
        """How many curves this trace expands into (mode 5/6). 1 = plain."""
        n = max(1, int(n))
        if n != self._span:
            self._span = n
            self._redraw_preview()

    def get(self) -> tuple[int, int]:
        return self._color_idx, self._ls_idx

    def set(self, color_idx: int, ls_idx: int) -> None:
        self._color_idx = int(color_idx) % len(COLORS)
        self._ls_idx = int(ls_idx) % len(LINESTYLES)
        self._redraw_preview()
        self._mark_selection()

    def expand(self) -> None:
        if not self._expanded:
            self.toggle()

    def collapse(self) -> None:
        if self._expanded:
            self.toggle()

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._arrow.configure(text="▾" if self._expanded else "▸")
        if self._expanded:
            self._mark_selection()
            self._palette.grid()
        else:
            self._palette.grid_remove()
        # The form got taller or shorter; the editor's scrollregion is measured,
        # not inferred, so it has to be told.  preserve=True -- collapsing must
        # not scroll the user away from the row they just used.
        app = self.winfo_toplevel()
        refresh = getattr(app, "_refresh_editor_scrollregion", None)
        if callable(refresh):
            refresh(preserve=True)


def _tk_dash(mpl_ls: str, width: int = 1):
    """
    A matplotlib linestyle as a Tk canvas dash pattern.

    Approximate on purpose: Tk's Win32 dash rendering is not matplotlib's, and
    the preview only has to make the four styles TELL APART, not match pixel
    for pixel.  Scaled by line width because Tk multiplies dash segments by it.
    """
    w = max(1, int(width))
    return {"-": (), "--": (6 * w, 3 * w), "-.": (6 * w, 2 * w, 1, 2 * w),
            ":": (1, 3 * w)}.get(mpl_ls, ())


# ============================================================================
# RowTable -- scrollable table of editable rows with a '+' button
# ============================================================================
#
# The replacement for the free-text boxes in Mode 5 and Mode 6.  Deliberately
# built from a Canvas plus a grid of real widgets rather than ttk.Treeview:
# Treeview has no cell editors, so it means floating Entry/Combobox widgets
# over cells and hand-managing placement, tab order and scroll offset, and the
# overlays misalign under Win11 DPI scaling.  A grid of real widgets is less
# code and behaves correctly.


@dataclass(frozen=True)
class ColumnSpec:
    """One column of a RowTable.  `key` is the row dataclass's field name."""
    key: str
    title: str
    width: int                      # in characters
    kind: str = "entry"             # "entry" | "combo" | "static"
    values: tuple = ()              # choices, for kind="combo"
    placeholder: str = ""
    readonly_combo: bool = False    # combo that rejects typed-in values
    # Value a '+ Add' row starts with.  The Add button is bound as
    # command=self.add_row, which Tk calls with NO arguments, so without this a
    # new connection row arrives with kind='' -- and rows_to_dsl_text raises on
    # that rather than treating it as blank.
    default: str = ""


class RowTable(ttk.Frame):
    """
    A '+ Add' button over a scrollable grid of editable rows.

    get_rows() / set_rows() speak lists of the dataclass `row_factory` makes,
    so the caller never touches a widget.  Blank trailing rows are kept in the
    UI (somewhere to type) but dropped by get_rows() via the row's own
    is_blank(), which is what lets the table start with an empty row without
    that empty row meaning anything.
    """

    def __init__(self, master, columns: Sequence[ColumnSpec], row_factory,
                 on_change=None, min_rows: int = 1, max_visible: int = 6,
                 add_text: str = "+ Add", **kwargs):
        super().__init__(master, **kwargs)
        self._columns = list(columns)
        self._row_factory = row_factory
        self._on_change = on_change
        self._min_rows = max(0, int(min_rows))
        self._max_visible = max(1, int(max_visible))
        self._rows: list[dict] = []      # per row: {key: tk.StringVar} + widgets
        self._resize_pending = False

        # --- add button (outside the scroll area) ---
        head = ttk.Frame(self)
        head.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(head, text=add_text, width=8, command=self.add_row
                   ).pack(side=tk.RIGHT, padx=1)

        # --- scrollable body ---
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(body, highlightthickness=0, height=1)
        self._vsb = ttk.Scrollbar(body, orient="vertical",
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self._inner,
                                                  anchor="nw")

        # Both bindings are needed: the inner frame drives the scrollregion,
        # the canvas drives the inner frame's width (without it the columns
        # do not stretch).
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # Wheel handling is NOT bound here.  The App installs one pointer-based
        # router and calls register_wheel() below; a per-widget bind_all /
        # unbind_all pair does not compose once there is more than one
        # scrollable region (unbind_all drops every binding on the `all` tag).

        # Column headers live in the SAME grid as the cells (grid row 0, cells
        # from row 1), so they line up exactly.  A separate header frame packed
        # above cannot: its labels measure in characters of a different font
        # than the Entry widgets below, and the last title ends up clipped.
        for c, col in enumerate(self._columns):
            ttk.Label(self._inner, text=col.title, anchor="w",
                      font=("TkDefaultFont", 8)
                      ).grid(row=0, column=c, sticky="w", padx=1)

        for _ in range(self._min_rows):
            self.add_row(notify=False)

    # ------------------------------------------------------------------ scroll

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._schedule_resize()

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _schedule_resize(self) -> None:
        """
        Queue _resize_to_content for the next idle moment, coalescing repeats.

        Never call update_idletasks() here.  It flushes geometry for the WHOLE
        application, and this widget is built while the rest of the window still
        is: one such flush during construction froze the Results pane's
        PanedWindow sash at 2px and made the pane vanish.  after_idle runs after
        Tk's own geometry pass, so reqheight is valid without forcing anything.
        """
        if self._resize_pending:
            return
        self._resize_pending = True
        self.after_idle(self._resize_to_content)

    def _resize_to_content(self) -> None:
        """Grow with the rows up to max_visible, then show the scrollbar."""
        self._resize_pending = False
        if not self.winfo_exists():
            return
        # A frame in create_window contributes NOTHING to the canvas's requested
        # size, so without this the canvas keeps Tk's default 378px forever and
        # a 6-column table is silently squashed with no way to reach the rest.
        self._canvas.configure(width=self._inner.winfo_reqwidth())
        total = max(1, self._inner.winfo_reqheight())
        n = len(self._rows)
        if n > self._max_visible:
            per = total / (n + 1)          # +1 for the header row
            self._canvas.configure(height=int(per * (self._max_visible + 1)))
            self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            self._canvas.configure(height=total)
            self._vsb.pack_forget()

    def register_wheel(self, register) -> None:
        """Hand the App's router this table's canvas and handler."""
        register(self._canvas, self._on_wheel)

    def _on_wheel(self, event) -> bool:
        """Scroll this table. Returns False when it fits, so the event bubbles."""
        first, last = self._canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self._canvas.yview_scroll(int(-event.delta / 120), "units")
        return True

    # ------------------------------------------------------------------- rows

    def add_row(self, values: dict | None = None, notify: bool = True) -> None:
        r = len(self._rows) + 1          # grid row 0 holds the column headers
        entry: dict = {"_vars": {}, "_widgets": []}
        for c, col in enumerate(self._columns):
            var = tk.StringVar(value=(values or {}).get(col.key, col.default))
            entry["_vars"][col.key] = var
            if col.kind == "combo":
                w = ttk.Combobox(
                    self._inner, textvariable=var, width=col.width,
                    values=list(col.values),
                    state="readonly" if col.readonly_combo else "normal")
            elif col.kind == "static":
                w = ttk.Label(self._inner, textvariable=var, width=col.width,
                              anchor="w")
            else:
                w = ttk.Entry(self._inner, textvariable=var, width=col.width)
            w.grid(row=r, column=c, sticky="we", padx=1, pady=1)
            entry["_widgets"].append(w)
            if self._on_change is not None:
                var.trace_add("write", lambda *_a: self._on_change())
        btn = ttk.Button(self._inner, text="✕", width=2,
                         command=lambda: self._delete_row(entry))
        btn.grid(row=r, column=len(self._columns), padx=1, pady=1)
        entry["_widgets"].append(btn)
        self._rows.append(entry)
        for c, col in enumerate(self._columns):
            self._inner.columnconfigure(c, weight=1 if col.kind != "static" else 0)
        self._schedule_resize()
        if notify and self._on_change is not None:
            self._on_change()

    def _delete_row(self, entry: dict) -> None:
        if entry not in self._rows:
            return
        for w in entry["_widgets"]:
            w.destroy()
        self._rows.remove(entry)
        self._regrid()
        if len(self._rows) < self._min_rows:
            self.add_row(notify=False)
        self._schedule_resize()
        if self._on_change is not None:
            self._on_change()

    def _regrid(self) -> None:
        for r, entry in enumerate(self._rows, start=1):   # 0 is the header row
            for c, w in enumerate(entry["_widgets"]):
                w.grid_configure(row=r, column=c)

    def clear(self) -> None:
        for entry in list(self._rows):
            for w in entry["_widgets"]:
                w.destroy()
        self._rows.clear()

    # ------------------------------------------------------------ get / set

    def get_rows(self) -> list:
        """Row dataclasses, blanks dropped (the row type decides what blank is)."""
        out = []
        for entry in self._rows:
            row = self._row_factory()
            for col in self._columns:
                setattr(row, col.key, entry["_vars"][col.key].get().strip())
            if not row.is_blank():
                out.append(row)
        return out

    def set_rows(self, rows: Sequence) -> None:
        self.clear()
        for row in rows:
            self.add_row({col.key: str(getattr(row, col.key, "") or "")
                          for col in self._columns}, notify=False)
        while len(self._rows) < self._min_rows:
            self.add_row(notify=False)
        self._schedule_resize()

    def set_column_values(self, key: str, values: Sequence[str]) -> None:
        """Repopulate a combo column's choices (e.g. after a file change)."""
        idx = next((i for i, c in enumerate(self._columns) if c.key == key), None)
        if idx is None:
            return
        for entry in self._rows:
            w = entry["_widgets"][idx]
            if isinstance(w, ttk.Combobox):
                w.configure(values=list(values))
        self._columns[idx] = replace(self._columns[idx], values=tuple(values))


# Per-mode placeholder hints for the remaining PlaceholderEntry fields, keyed
# by (field, mode) -> hint text.  A table-based mode registers NOTHING here: a
# table cell cannot hold a hint (PlaceholderEntry deletes it on <FocusIn>), so
# its hint is a _CollapsibleHint label under the table instead.
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
}

LABEL_PLACEHOLDER = "trace name shown in plot legend (optional)"

# Shown under the measurement-port table. This has to carry everything
# the six retired placeholder hints used to say, because a plain ttk.Entry in
# a table cell has no room for a hint of its own -- so it is long, and it lives
# behind a disclosure triangle (collapsed by default) rather than costing 125px
# of a form that already does not fit. Unlike a PlaceholderEntry's hint, focus
# cannot destroy it: it is a Label, and it is still there after you click away.
MP_TABLE_HINT_SHORT = "'+' = red probe, '−' = black (empty = vs GND)"
MP_TABLE_HINT = (
    "One row per measurement port. '+' is the red probe, '−' the black one; "
    "leave '−' empty to measure against GND. Ports listed on the same side are "
    "tied together, and ranges work (5,7 or 5:1:8). Two or more rows give you "
    "the coupling (M, k) between them. Names are optional (P1, P2, … if blank) "
    "but 'A' and 'B' are reserved. Port NUMBERS are what these cells take -- "
    "for the file's port names use 'Show Ports' at the top of this panel; it "
    "lists them in the Results pane."
)


class _CollapsibleHint(ttk.Frame):
    """
    A one-line grey summary plus a disclosure triangle for the full text.

    Collapsed by default: the long hints measured 182px in mode 6, more than
    twice the table they explain, on a form that already overflowed. The full
    text stays one click away and the expanded/collapsed state is remembered
    for the session, so a user who needs it once keeps it, and a user who has
    internalised it never sees it again.
    """

    _expanded = False        # class-level: shared across every hint instance

    def __init__(self, master, short: str, long: str, **kwargs):
        super().__init__(master, **kwargs)
        self._long_text = long
        self._btn = ttk.Label(self, foreground=PLACEHOLDER_FG, cursor="hand2")
        self._btn.pack(side=tk.TOP, anchor="w")
        self._btn.bind("<Button-1>", self._toggle)
        self._short = short
        self._body = ttk.Label(self, text=long, foreground=PLACEHOLDER_FG,
                               justify=tk.LEFT, wraplength=320)
        self._render()

    def _render(self) -> None:
        arrow = "▾" if _CollapsibleHint._expanded else "▸"
        self._btn.configure(text=f"{arrow} {self._short}")
        if _CollapsibleHint._expanded:
            self._body.pack(side=tk.TOP, anchor="w", pady=(1, 0))
        else:
            self._body.pack_forget()

    def _toggle(self, _event=None) -> None:
        _CollapsibleHint._expanded = not _CollapsibleHint._expanded
        for w in self.master.winfo_children():
            if isinstance(w, _CollapsibleHint):
                w._render()
        self.event_generate("<<HintToggled>>")

# Shown under the mode-6 plot checkboxes: the subplot grid is shared with the
# self curves, so the axis titles need reinterpreting on a mutual curve.
MUTUAL_CURVE_HINT_SHORT = "on a mutual curve, L(nH) reads as M and C(pF) as C_c"
MUTUAL_CURVE_HINT = (
    "On a mutual curve the L(nH) subplot IS M in nH and C(pF) IS the coupling "
    "capacitance C_c; the k subplot is filled in for mutual curves only."
)

# --- Mode 5 connections table -------------------------------------------
#
# Column widths are a MEASURED budget, not a preference. The editor canvas is
# 431 px wide once its vertical scrollbar is showing, which in mode 5 it always
# is; the label column costs 91 px, which is why this table gets a caption
# ABOVE it and spans all four form columns instead of sitting beside a label.
# At these widths the table asks for 405 px and the whole mode-5 form for 418,
# so the headroom under the 431 px viewport is 13 px, not 22. Measure it again
# before adding a column -- CLAUDE.md carries the same two numbers.
#
# Type is a readonly combo -- a kind that is not in CONN_KINDS raises at build
# time, so there is nothing useful to type. Port and To are NOT readonly: a
# range ('6-14', '35:1:45') has to be typeable, and a readonly combo cannot be
# typed into at all. Their values are the file's bare port numbers, filled in
# by _refresh_port_choices; there is deliberately no 'GND' entry, because "to
# ground" is a KIND here (ground / rlc_gnd) and 'short_to GND' is a parser
# error the user could not connect to what they clicked.
CONN_TABLE_COLUMNS = (
    ColumnSpec("kind", "Type", 11, kind="combo", values=CONN_KINDS,
               readonly_combo=True, default="ground"),
    ColumnSpec("ports", "Port", 7, kind="combo"),
    ColumnSpec("to", "To", 7, kind="combo"),
    ColumnSpec("R", "R Ω", 5),
    ColumnSpec("L", "L H", 5),
    ColumnSpec("C", "C F", 5),
)

CONN_TABLE_HINT_SHORT = "one row per connection; Port takes a range (6-14, 35:1:45)"
CONN_TABLE_HINT = (
    "One row per connection. Type picks what is attached: ground / vdd (both "
    "are V=0 for AC), open, short (ties Port to To), rlc_gnd (a series R-L-C "
    "from Port to ground) or rlc_between (the same element from Port to To). "
    "Port and To take ranges -- 6-14 or 35:1:45 -- so a package's ground balls "
    "are one row. R/L/C hold the bare value with SI suffixes and the unit is "
    "in the header: 5m is 5 milli, 5M is 5 Mega, and the value must be ONE "
    "word -- '5 m' and '1 uF' are rejected. A blank R/L/C means OMITTED, "
    "which is not zero -- an omitted C is no capacitor, C=0 would be an open "
    "circuit. 'To' is ignored by ground/vdd/open/rlc_gnd, which are always to "
    "ground; rlc_between takes exactly ONE partner port. The dropdowns list "
    "port NUMBERS; for the file's port names click 'Show Ports' at the top of "
    "this panel and read them in the Results pane."
)

# What the "Edit as text…" dialog promises, verbatim. The round trip is
# deliberately lossy in known ways and saying so is the difference between a
# normalisation and a surprise.
TEXT_DIALOG_NOTE = (
    "This is the spec that will be computed. Your text is rewritten into "
    "canonical form: 'gnd'→'ground', 'signal a'→'signal A', 'r='→'R=', R/L/C "
    "reordered to R,L,C, and every measurement port emitted before every "
    "connection (which is what makes a later 'ground' win). Blank lines and "
    "end-of-line comments are dropped; anything the table cannot represent is "
    "kept verbatim."
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
        # Shadow of the selected trace's extra_lines: whatever the connections
        # table cannot represent (comments, hand-written directives, a spec
        # parked verbatim by the migration). The editor has no widget for it --
        # it is edited through "Edit as text…" -- but it has to survive a load
        # / apply cycle, so it lives here alongside the widgets.
        self._ed_extra_lines: str = ""
        self._ed_strips_pending = False
        # Auto-apply state.  The target is the TraceConfig OBJECT, never a
        # Listbox index -- see _schedule_editor_sync.
        self._ed_sync_after: object = None
        self._ed_sync_target: Optional[TraceConfig] = None
        self._trace_list_shown: list[str] = []
        self._scrollables: dict[str, object] = {}

        self._install_wheel_router()
        self._build_ui()
        self._bind_events()
        self._clamp_to_screen()

    # ------------------------------------------------------- wheel routing

    # Widget classes that scroll (or otherwise act on) the wheel themselves.
    # The router must not double-handle for these.
    #
    # TCombobox is deliberately NOT here.  Its value-changing class binding is
    # removed in _install_wheel_router below, so it no longer owns the wheel --
    # and half the connections table (Type / Port / To) is a combobox, which
    # made those three columns a dead zone the table could not be scrolled from.
    # An OPEN dropdown is a Listbox, which is still in this set and still
    # scrolls itself.
    _WHEEL_OWNERS = frozenset({"Text", "Listbox", "Treeview",
                               "TSpinbox", "Spinbox", "TScrollbar", "Scrollbar"})

    def _install_wheel_router(self) -> None:
        """
        One pointer-based wheel router for the whole window.

        Replaces the per-widget <Enter>/<Leave> + bind_all/unbind_all dance,
        which does not compose: unbind_all deletes EVERY binding on the `all`
        tag, so a second scrollable region silently disables the first, and a
        table that cannot scroll swallowed the event instead of letting the
        form behind it scroll.  Here each registered handler returns False when
        its content already fits, and the event bubbles to the next scrollable
        ancestor -- innermost wins, with fall-through.

        Matplotlib is unaffected: its wheel binding is on the figure canvas
        widget itself, which fires before the `all` tag, and the router finds
        nothing registered above it. The plot panel's <Enter> -> focus_set()
        for the M / V / Delete keys is untouched -- this keys off the POINTER,
        not focus.
        """
        self.bind_all("<MouseWheel>", self._route_wheel, add="+")
        # A ttk Combobox CHANGES ITS VALUE on the wheel (class binding
        # 'ttk::combobox::Scroll'). On a form where one of those comboboxes
        # selects the Touchstone file, an accidental scroll silently rebinds
        # the trace to a different file and every number changes with no
        # warning. Nothing here wants that behaviour.
        self.unbind_class("TCombobox", "<MouseWheel>")

    def _register_scrollable(self, widget, handler) -> None:
        """handler(event) -> True if it consumed the wheel, False to bubble."""
        self._scrollables[str(widget)] = handler

    def _route_wheel(self, event):
        w = self.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            try:
                if w.winfo_class() in self._WHEEL_OWNERS:
                    return None          # it handles itself; don't double-scroll
                handler = self._scrollables.get(str(w))
                if handler is not None and handler(event):
                    return "break"
                w = w.master
            except Exception:
                return None
        return None

    def _clamp_to_screen(self) -> None:
        """
        Never open taller than the desktop.

        The hardcoded 1500x900 is fine on a large monitor and is born partly
        off-screen on a 1920x1080 laptop at 150% scaling (1280x680 logical),
        which puts the bottom of the window -- Calculate, Apply -- beyond the
        desktop before layout is even involved.
        """
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1500, sw - 80)}x{min(900, sh - 140)}+40+20")
        self.minsize(1040, 600)

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
        ttk.Button(btn_row, text="Check File", command=self._on_check_file
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
        # FOURTH button in this row, and pack unmaps from the end, so this was
        # measured before it was added: at the 1040x600 minsize the row is
        # 448 px and four buttons ask 364 (three ask 273).  Re-measure before a
        # fifth.  It duplicates the editor's "Plot: this trace" checkbox on
        # purpose -- the checkbox needs the trace selected first, and the
        # keyboard route (space) is invisible.
        ttk.Button(tr_btn_row, text="Show/Hide", command=self._on_toggle_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.traces_lb = tk.Listbox(traces_frame, height=8, exportselection=False,
                                    activestyle="dotbox")
        self.traces_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        # --- Global controls ---
        # PACKED BEFORE THE EDITOR, AND side=BOTTOM.  pack allocates in call
        # order and simply UNMAPS whatever no longer fits, starting from the
        # end -- so a fixed-size section packed after an expand=True sibling
        # vanishes entirely once the sibling outgrows the panel.  Measured: in
        # mode 6 at 1500x900 this whole frame came back winfo_ismapped() == 0,
        # i.e. Calculate / Export CSV / Help were not on screen at all. Claiming
        # the bottom first is what makes them unconditional.
        gc = ttk.LabelFrame(parent, text="Global Controls")
        gc.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=2)
        self._build_global_controls(gc)

        # --- Editor section ---
        ed = ttk.LabelFrame(parent, text="Edit Selected Trace")
        ed.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._build_editor(ed)

    def _build_editor(self, parent: ttk.LabelFrame) -> None:
        """
        Editor = a pinned footer + a scrollable form.

        The form is mode-dependent and in mode 5 it is taller than any laptop
        screen, so it lives in a Canvas.  The footer button does NOT: it is
        packed side=BOTTOM *first*, outside the scroll region, so the form can
        clip or scroll all it likes and the button stays reachable.  Packing it
        after the expanding body is what made it fall off the bottom.

        That button used to be "Apply to Trace".  The editor now applies itself
        as you type (see _schedule_editor_sync), so the slot went to the thing
        this GUI had no way to do at all: recompute ONE trace.  The only compute
        path was "Calculate All & Plot", which on four traces over a large
        package file does four times the work the user asked for on every pass
        of the edit / compute / read-the-cursor loop.  The footer is also not
        allowed to be empty -- Tk does not reissue a geometry request when a
        master's last slave is removed, so an emptied footer would keep its
        requested height forever.
        """
        foot = ttk.Frame(parent)
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(foot, text="Calculate This Trace",
                   command=self._on_calculate_selected
                   ).pack(side=tk.RIGHT, padx=6, pady=3)

        self._ed_body = body = ttk.Frame(parent)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._ed_vsb = ttk.Scrollbar(body, orient="vertical")
        self._ed_canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0,
                                    yscrollcommand=self._ed_scroll_set,
                                    xscrollcommand=self._ed_hscroll_set)
        self._ed_vsb.configure(command=self._ed_canvas.yview)
        # The horizontal scrollbar is the safety net under DPI scaling: the
        # column-width budget the tables fit into is a 100%-font number, and at
        # 150% no column set fits.
        #
        # Both scrollbars are DIRECT children of `body` and neither is packed
        # here: _apply_editor_scrollbars decides both together and packs them
        # with `before=self._ed_canvas`, because pack unmaps from the END and a
        # fixed-size slave after an expand=True one disappears rather than
        # clipping.  The horizontal one used to live in a permanently packed
        # host frame instead -- which is NOT "0 px tall while empty": Tk does
        # not reissue a geometry request when a master's LAST slave is removed,
        # so once the first layout pass had packed and unpacked the scrollbar
        # the empty host kept a 17 px requested height forever, in EVERY mode,
        # including 1/2/3 which never raise the scrollbar at all (measured at
        # 1040x600: 45 px of editor viewport became 28).
        self._ed_hsb = ttk.Scrollbar(body, orient="horizontal",
                                     command=self._ed_canvas.xview)
        self._ed_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._ed_form = ttk.Frame(self._ed_canvas)
        self._ed_win = self._ed_canvas.create_window(
            (0, 0), window=self._ed_form, anchor="nw")
        self._ed_canvas.bind("<Configure>", self._on_editor_canvas_configure)
        # The form's own <Configure> is needed as well as the canvas's, for the
        # same reason RowTable needs both: a table that grows changes the form's
        # size one idle pass AFTER the row was added, so a scrollregion measured
        # from the row-add callback alone is one row short and the new row
        # cannot be scrolled to.
        self._ed_form.bind(
            "<Configure>",
            lambda e: self._refresh_editor_scrollregion(preserve=True))
        self._register_scrollable(self._ed_canvas, self._ed_wheel)

        self._build_editor_form(self._ed_form)

    def _ed_scroll_set(self, first: str, last: str) -> None:
        """Move the thumb only.  Whether the bar is SHOWN is decided in one
        place -- see _apply_editor_scrollbars."""
        self._ed_vsb.set(first, last)

    def _on_editor_canvas_configure(self, event) -> None:
        """
        Keep the form at least as wide as it asked for, and re-measure.

        Never let it be narrower: a squashed 6-column table with no horizontal
        scrollbar is unreachable content.  The scrollregion has to be recomputed
        here too, not only on a mode change -- when the vertical scrollbar
        appears the canvas narrows 448 -> 431, and a scrollregion still 448 wide
        raises a horizontal scrollbar for 17 px of nothing.
        """
        self._ed_canvas.itemconfigure(
            self._ed_win, width=max(event.width,
                                    self._ed_form.winfo_reqwidth()))
        self._refresh_editor_scrollregion(preserve=True)

    def _ed_hscroll_set(self, first: str, last: str) -> None:
        """Thumb only; same reason as _ed_scroll_set."""
        self._ed_hsb.set(first, last)

    # Both editor scrollbars autohide.  Deciding each one from its own
    # scrollcommand is a LIMIT CYCLE, not a race: hiding the horizontal bar
    # gives the canvas 17 px of height back, which can hide the vertical bar,
    # which gives 17 px of width back, which brings the horizontal bar back.
    # Measured with the two decisions split: the editor flipped 431x245 <->
    # 448x228 forever and update() never returned -- the whole GUI hangs.
    #
    # So both are decided HERE, in one pass, from inputs that a scrollbar
    # cannot change: `body`'s size (set by its master, not by its slaves) and
    # the form's REQUESTED size.  Same inputs -> same answer -> it converges.
    def _apply_editor_scrollbars(self) -> None:
        body = self._ed_body
        avail_w, avail_h = body.winfo_width(), body.winfo_height()
        form_w = self._ed_form.winfo_reqwidth()
        form_h = self._ed_form.winfo_reqheight()
        vsb_w = max(self._ed_vsb.winfo_reqwidth(), 1)
        hsb_h = max(self._ed_hsb.winfo_reqheight(), 1)

        need_v = form_h > avail_h
        need_h = form_w > avail_w - (vsb_w if need_v else 0)
        if need_h and not need_v:
            # The bar we just decided to show eats the height. Re-check the
            # vertical one ONCE and never re-check the horizontal one: a
            # second round trip is how the cycle above got started.
            need_v = form_h > avail_h - hsb_h

        # Pinned ahead of the canvas: pack unmaps from the end, and an
        # expand=True canvas packed first would leave nothing for either bar.
        if need_v:
            self._ed_vsb.pack(side=tk.RIGHT, fill=tk.Y,
                              before=self._ed_canvas)
        else:
            self._ed_vsb.pack_forget()
        if need_h:
            self._ed_hsb.pack(side=tk.BOTTOM, fill=tk.X,
                              before=self._ed_canvas)
        else:
            self._ed_hsb.pack_forget()

    def _ed_wheel(self, event) -> bool:
        """Scroll the editor form. Returns False when it has nowhere to go."""
        first, last = self._ed_canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self._ed_canvas.yview_scroll(int(-event.delta / 120), "units")
        return True

    def _refresh_editor_scrollregion(self, preserve: bool = False) -> None:
        """
        Re-measure after grid()/grid_remove() or a row add changed the form.

        The <Configure> binding on the inner frame is NOT sufficient: measured,
        after hiding 17 rows the scrollregion stayed at its old height and the
        view stayed scrolled down, leaving a short form parked out of sight;
        and after six '+ Add' clicks in mode 6 the form grew 357 -> 476 px
        while the scrollregion stayed at 357, so the new rows could not be
        reached by scrolling at all.

        `preserve` says what to do with the scroll offset.  A MODE CHANGE must
        reset it (preserve=False) or a now-short form stays parked out of
        sight; a ROW ADD must keep it (preserve=True) or the row the user just
        created scrolls away and the view jumps back to the File combobox.

        Deferred to after_idle, NEVER update_idletasks(). This runs during
        construction too (_build_editor_form ends by calling
        _update_mode_visibility), and forcing a geometry pass there is exactly
        what collapsed the Results pane's PanedWindow sash to 2px --
        tests/test_row_table.py::TestResultsPaneVisible caught this very edit.
        """
        if getattr(self, "_ed_scroll_pending", False):
            # Coalescing a reset with a preserve: the reset wins, because the
            # reason for it (the form is a different shape now) has not gone
            # away.  Only within one pending batch -- the flag is re-armed
            # below, or a stale False would swallow every later row add.
            self._ed_scroll_preserve = self._ed_scroll_preserve and preserve
            return
        self._ed_scroll_preserve = preserve
        self._ed_scroll_pending = True
        self.after_idle(self._apply_editor_scrollregion)

    def _apply_editor_scrollregion(self) -> None:
        self._ed_scroll_pending = False
        if not self._ed_canvas.winfo_exists():
            return
        self._apply_editor_scrollbars()
        first = self._ed_canvas.yview()[0]
        # Re-apply the window item's width here as well as in the canvas's
        # <Configure>: that binding fires only when the CANVAS resizes, so
        # after a table grows the item width is stale and the form is clipped.
        self._ed_canvas.itemconfigure(
            self._ed_win,
            width=max(self._ed_canvas.winfo_width(),
                      self._ed_form.winfo_reqwidth()))
        self._ed_canvas.configure(scrollregion=self._ed_canvas.bbox("all"))
        if self._ed_scroll_preserve:
            self._ed_canvas.yview_moveto(first)
        else:
            self._ed_canvas.yview_moveto(0)
            self._ed_canvas.xview_moveto(0)

    def _build_editor_form(self, parent: ttk.Frame) -> None:
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

        # --- Modes 5 and 6: measurement ports (probe pairs) ---
        # One table row per measurement port, added with the '+' button. This
        # replaces two hard-coded ports plus a free-text box for the third
        # onward -- that cliff (ports 1-2 get fields, port 3+ gets syntax) was
        # the same disease as the Mode 5 text box, just less obvious.  Mode 5
        # shows the same table plus the connections table below it, so the
        # superset relationship is in the layout rather than hidden as a trap.
        self.ed_mp_lbl = ttk.Label(parent, text="Measurement\nports:",
                                   justify=tk.RIGHT)
        self.ed_mp_lbl.grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        self.ed_mp_table = RowTable(
            parent,
            columns=(ColumnSpec("name", "Name", 9),
                     ColumnSpec("plus", "+ ports (red)", 13),
                     ColumnSpec("minus", "− ports (black)", 13)),
            row_factory=MeasPortRow,
            on_change=self._on_editor_rows_changed,
            min_rows=1, max_visible=5,
        )
        self.ed_mp_table.grid(row=row, column=1, columnspan=3, sticky="we",
                              padx=2, pady=1)
        self.ed_mp_table.register_wheel(self._register_scrollable)
        row += 1

        self.ed_mp_hint = _CollapsibleHint(parent, MP_TABLE_HINT_SHORT,
                                           MP_TABLE_HINT)
        self.ed_mp_hint.grid(row=row, column=1, columnspan=3, sticky="we",
                             padx=2, pady=(0, 2))
        row += 1

        # GND / VDD ports (VDD merged in: for AC small-signal they are the same)
        self.ed_gnd_lbl = ttk.Label(parent, text="GND / VDD (AC gnd):")
        self.ed_gnd_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_gnd = PlaceholderEntry(parent, width=42,
                                       placeholder=MODE_PLACEHOLDERS["gnd"][1])
        self.ed_gnd.grid(row=row, column=1, columnspan=3, sticky="we",
                         padx=2, pady=1)
        row += 1

        # What to plot.  "this trace" is the per-trace visibility switch and is
        # shown in EVERY mode; "self" / "mutual" pick which of a coupling
        # trace's expanded curves are drawn and stay gated to modes 5/6.  They
        # share one row because they answer the same question at two scales,
        # and because the row costs nothing: measured, adding "this trace"
        # takes the frame from 113 px to 189 px against a 437 px row.
        # The children are GRIDDED, not packed: grid_remove()/grid() puts a
        # widget back in the same column, whereas re-packing appends it to the
        # end and would silently reorder the row.
        self.ed_plot_lbl = ttk.Label(parent, text="Plot:")
        self.ed_plot_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_plot_frame = ttk.Frame(parent)
        self.ed_plot_frame.grid(row=row, column=1, columnspan=3, sticky="w",
                                padx=2, pady=1)
        self.ed_enabled_var = tk.BooleanVar(value=True)
        self.ed_plot_self_var = tk.BooleanVar(value=True)
        self.ed_plot_mutual_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.ed_plot_frame, text="this trace",
                        variable=self.ed_enabled_var,
                        command=self._on_enabled_toggled
                        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.ed_plot_self_cb = ttk.Checkbutton(
            self.ed_plot_frame, text="self", variable=self.ed_plot_self_var)
        self.ed_plot_self_cb.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.ed_plot_mutual_cb = ttk.Checkbutton(
            self.ed_plot_frame, text="mutual", variable=self.ed_plot_mutual_var)
        self.ed_plot_mutual_cb.grid(row=0, column=2, sticky="w")
        row += 1

        self.ed_mutual_hint = _CollapsibleHint(parent, MUTUAL_CURVE_HINT_SHORT,
                                               MUTUAL_CURVE_HINT)
        self.ed_mutual_hint.grid(row=row, column=1, columnspan=3, sticky="we",
                                 padx=2, pady=(0, 2))
        row += 1

        # --- Mode 5: connections table ---
        # The caption and the 'Edit as text…' button share ONE sub-frame across
        # all four columns, so the button's width cannot influence the grid
        # column widths the table needs.  The table itself spans columns 0-3
        # rather than sitting beside a label: the label column costs 91 px and
        # the budget is 431.
        self.ed_conn_head = ttk.Frame(parent)
        self.ed_conn_head.grid(row=row, column=0, columnspan=4, sticky="we",
                               padx=2, pady=(4, 0))
        ttk.Label(self.ed_conn_head, text="Connections:").pack(side=tk.LEFT)
        ttk.Button(self.ed_conn_head, text="Edit as text…",
                   command=self._on_edit_as_text).pack(side=tk.RIGHT)
        # extra_lines has no widget of its own, so without this the two tables
        # can be EMPTY while a hidden block of DSL is in force -- and, being
        # emitted last, winning over anything typed into them afterwards.  The
        # count rides on the caption row, so it costs no vertical space.
        self.ed_extra_lbl = ttk.Label(self.ed_conn_head,
                                      foreground=PLACEHOLDER_FG)
        self.ed_extra_lbl.pack(side=tk.LEFT, padx=(6, 0))
        row += 1

        self.ed_conn_table = RowTable(
            parent, columns=CONN_TABLE_COLUMNS, row_factory=ConnectionRow,
            on_change=self._on_editor_rows_changed,
            min_rows=1, max_visible=6,
        )
        self.ed_conn_table.grid(row=row, column=0, columnspan=4, sticky="we",
                                padx=2, pady=1)
        self.ed_conn_table.register_wheel(self._register_scrollable)
        row += 1

        # Direct child of the form, NOT of a sub-frame: _CollapsibleHint._toggle
        # re-renders by walking self.master.winfo_children(), and the expanded
        # flag is class-level, so a hint one level down desynchronises its arrow
        # from the shared state.
        self.ed_conn_hint = _CollapsibleHint(parent, CONN_TABLE_HINT_SHORT,
                                             CONN_TABLE_HINT)
        self.ed_conn_hint.grid(row=row, column=0, columnspan=4, sticky="we",
                               padx=2, pady=(0, 2))
        row += 1

        self.ed_overview = ttk.Label(parent, anchor="w",
                                     foreground=PLACEHOLDER_FG)
        self.ed_overview.grid(row=row, column=0, columnspan=4, sticky="we",
                              padx=2)
        row += 1
        self.ed_validation = ttk.Label(parent, anchor="w", justify=tk.LEFT,
                                       wraplength=400)
        self.ed_validation.grid(row=row, column=0, columnspan=4, sticky="we",
                                padx=2)
        row += 1

        # Label
        ttk.Label(parent, text="Label:").grid(row=row, column=0, sticky="e",
                                              padx=2, pady=1)
        self.ed_label = PlaceholderEntry(parent, width=42,
                                         placeholder=LABEL_PLACEHOLDER)
        self.ed_label.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Colour / linestyle.  One preview that expands into a palette, in place
        # of the two "Color idx / LS idx" Spinboxes -- an integer 0..11 told the
        # user nothing about what would be drawn.  Dropping the two tk.IntVars
        # also removes the only place _sync_editor_to_trace could raise, which
        # matters now that it runs from a variable trace on every keystroke: an
        # IntVar raises TclError on the empty string a normal select-all-retype
        # passes through.
        ttk.Label(parent, text="Style:").grid(row=row, column=0, sticky="ne",
                                              padx=2, pady=1)
        self.ed_style = StylePicker(parent, on_change=self._on_style_changed)
        self.ed_style.grid(row=row, column=1, columnspan=3, sticky="w",
                           padx=2, pady=1)
        row += 1

        # No button here: the footer holds "Calculate This Trace", pinned
        # outside the scroll region so it survives however long this form gets.
        # And there is nothing to apply -- the editor writes itself into the
        # selected trace as you type (see _schedule_editor_sync).

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
        # POPULATE BEFORE add().  A ttk.PanedWindow sizes a new pane from its
        # requested size AT THE MOMENT IT IS ADDED, and never recomputes. Adding
        # an empty frame and filling it afterwards therefore works only by luck:
        # it depends on no geometry pass running in between, so any widget built
        # earlier that calls update_idletasks() silently pins the sash at ~2px
        # and the whole Results pane disappears. Build the children first and the
        # sash position stops depending on timing.
        results_frame = ttk.Frame(parent, height=180)
        plot_frame = ttk.Frame(parent)

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

        parent.add(results_frame, weight=0)
        parent.add(plot_frame, weight=1)

    def _bind_events(self) -> None:
        self.files_lb.bind("<<ListboxSelect>>", lambda e: self._on_file_selected())
        self.traces_lb.bind("<<ListboxSelect>>", lambda e: self._on_trace_selected())
        # A different file means a different port count: the Port / To
        # dropdowns and the overview strip both key off it.
        self.ed_file_cbo.bind("<<ComboboxSelected>>",
                              lambda e: self._on_editor_file_changed())
        # Expanding or collapsing a hint changes the form's height.
        self.bind("<<HintToggled>>",
                  lambda e: self._refresh_editor_scrollregion(preserve=True),
                  add="+")
        # Space toggles the selected trace's visibility -- the sweep gesture for
        # "hide these four".  Returning "break" stops the Listbox class binding
        # from also treating space as select-activate-item.
        self.traces_lb.bind("<space>", self._on_toggle_trace_key)

        # ---- auto-apply -------------------------------------------------
        # Registered HERE, after _build_ui, and never in a widget constructor:
        # PlaceholderEntry.__init__ ends with _show_if_empty(), which writes its
        # variable, so a callback attached during construction would fire a sync
        # before self.traces exists.
        for pe in (self.ed_porta, self.ed_portb, self.ed_short, self.ed_gnd,
                   self.ed_label):
            pe.on_change(self._schedule_editor_sync)
        for var in (self.ed_file_var, self.ed_mode_var, self.ed_plot_self_var,
                    self.ed_plot_mutual_var):
            var.trace_add("write", self._schedule_editor_sync)

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
        paths = filedialog.askopenfilenames(
            title="Select Touchstone file(s)",
            filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                       ("All files", "*.*")],
        )
        for p in paths:
            ts = self._load_one_file(p)
            if ts is None:
                continue
            fe = FileEntry(ts)
            self.files.append(fe)
            self._append_result("")
            for line in ts.summary_lines():
                self._append_result(line)
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
        """
        List the selected file's port names in the Results pane.

        This is the ONLY place the file's port names are reachable -- the Port
        and To dropdowns carry bare numbers, for the measured width reason in
        docs/design_connection_table.md §5a -- so it must not silently do
        nothing.  With no selection in the Files list it falls back to the file
        the editor is pointing at, which is the one the user is describing
        ports for, and says so if there is no file at all.
        """
        idx = self._sel_idx(self.files_lb)
        fe = self.files[idx] if idx is not None else None
        if fe is None:
            fe = self._file_by_label(self.ed_file_var.get())
        if fe is None:
            messagebox.showinfo("No file", "Add a file first.")
            return
        self._append_result(f"\nPorts of {fe.label}:")
        for i, name in enumerate(fe.ts.port_names, 1):
            self._append_result(f"  {i:3d}: {name or '(unnamed)'}")

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
        idx = self._sel_idx(self.files_lb)
        fe = self.files[idx] if idx is not None else None
        if fe is not None:
            path = fe.ts.source_path
        else:
            path = filedialog.askopenfilename(
                title="Check which Touchstone file?",
                filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                           ("All files", "*.*")])
            if not path:
                return
        self._append_result("")
        for line in diagnose_touchstone(path).splitlines():
            self._append_result(line)

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
        self._replot_from_cache()

    def _on_toggle_trace_key(self, _event=None) -> str:
        self._on_toggle_trace()
        return "break"      # or the Listbox also select-activates on space

    def _on_toggle_trace(self) -> None:
        """
        Show / hide the selected trace without deleting it.

        Replots from the cached Z instead of recomputing: the whole point is
        that taking a curve off the plot should not cost a Schur reduction of a
        153-port file to arrive at numbers that have not changed.
        """
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = self.traces[idx]
        tc.enabled = not tc.enabled
        # The editor is showing this trace, so its checkbox has to follow --
        # suppressed, or the write trace schedules a sync that would just write
        # the same value back.
        self._suppress_editor_sync = True
        try:
            self.ed_enabled_var.set(tc.enabled)
        finally:
            self._suppress_editor_sync = False
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)
        self._replot_from_cache()

    def _on_duplicate_trace(self) -> None:
        # Without the flush the copy is taken from the trace as it was BEFORE
        # the edit still queued in the editor -- i.e. Duplicate would silently
        # copy something the user cannot see any more.
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        new = _duplicate_trace_config(self.traces[idx], self._next_trace_id)
        self._next_trace_id += 1
        self.traces.append(new)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        self._on_trace_selected()

    def _on_trace_selected(self) -> None:
        # Land any queued edit on the trace it was typed into, BEFORE the
        # editor is reloaded from a different one.  Deferring the sync is what
        # makes it safe (see _schedule_editor_sync); flushing here is what
        # keeps it from landing on the wrong trace.
        self._flush_editor_sync()
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
            self.ed_mp_table.set_rows(tc.mports)
            self.ed_enabled_var.set(bool(tc.enabled))
            self.ed_plot_self_var.set(bool(tc.plot_self))
            self.ed_plot_mutual_var.set(bool(tc.plot_mutual))
            self.ed_label.set_value(tc.label)
            self.ed_style.set(tc.color_idx, tc.ls_idx)
            self.ed_conn_table.set_rows(tc.conn_rows)
            self._ed_extra_lines = tc.extra_lines
            self._refresh_port_choices()
            # INSIDE the guard.  _update_mode_visibility calls set_placeholder
            # on four PlaceholderEntries, and each of those writes its variable
            # -- four unguarded write traces per selection if it runs after the
            # finally.  They usually write the same value back, but
            # _sync_editor_to_trace turns an empty Label into 'trace_<id>', so a
            # sync fired from there can rename a trace nobody touched.
            self._update_mode_visibility()
        finally:
            self._suppress_editor_sync = False
        self._refresh_editor_strips()

    def _migrate_trace(self, tc: TraceConfig) -> None:
        """
        Fold retired shapes forward: mode 4 -> 2, mp1/mp2/mp_more -> table,
        custom_text -> the two tables.

        The custom-text block runs LAST on purpose: a legacy config carrying
        both mp1_* and a stale custom_text must fill `mports` first, so the
        custom-text guard declines rather than merging two unrelated specs.
        """
        if tc.migrate_legacy_mode():
            self._append_result(
                f"  [{tc.id}] {tc.label}: mode 4 (A↔B + VDD/GND) is retired; "
                f"migrated to mode 2 with VDD folded into GND "
                f"(GND = {tc.gnd_ports or '(none)'})")
            self._refresh_trace_list()
        if tc.migrate_legacy_mports():
            self._append_result(
                f"  [{tc.id}] {tc.label}: the Port 1 / Port 2 / 'More ports' "
                f"fields are retired; migrated to {len(tc.mports)} row(s) of "
                "the measurement-port table")
            self._refresh_trace_list()
        legacy_custom = tc.custom_text
        if tc.migrate_legacy_custom_text():
            # Ask _import_dsl_text again rather than inferring the verbatim
            # fallback from empty tables: a spec that is nothing but comments
            # also leaves both empty, and telling that user their precedence
            # changed would be a lie.
            if _import_dsl_text(legacy_custom)[3]:
                self._append_result(
                    f"  [{tc.id}] {tc.label}: the free-text Custom spec is kept "
                    "verbatim -- moving it into the table would have changed "
                    "which port wins (a 'signal' line follows a 'ground' on the "
                    "same port). Open 'Edit as text…' to convert it by hand.")
            else:
                self._append_result(
                    f"  [{tc.id}] {tc.label}: the free-text Custom spec is "
                    f"retired; imported into {len(tc.mports)} measurement "
                    f"port(s) and {len(tc.conn_rows)} connection row(s)")
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

        # Modes 5 and 6 both replace the structured fields with tables: mode 6
        # is the measurement-port table alone, mode 5 is that table plus the
        # connections table and the two strips under it.
        ab_modes = mode in (1, 2, 3)
        coupling = mode == 6
        rows_mode = mode in (5, 6)
        custom = mode == 5
        show(self.ed_porta_lbl, ab_modes)
        show(self.ed_porta, ab_modes)
        show(self.ed_portb_lbl, mode in (2, 3))
        show(self.ed_portb, mode in (2, 3))
        show(self.ed_short_lbl, mode == 3)
        show(self.ed_short, mode == 3)
        show(self.ed_mp_lbl, rows_mode)
        show(self.ed_mp_table, rows_mode)
        show(self.ed_mp_hint, rows_mode)
        # No GND field in mode 5: grounding there is a connection row, and two
        # ways to say the same thing is what the table is trying to remove.
        show(self.ed_gnd_lbl, ab_modes or coupling)
        show(self.ed_gnd, ab_modes or coupling)
        # The Plot row itself is unconditional -- "this trace" is the
        # visibility switch and every mode has one.  Only the self/mutual pair
        # is mode-gated. They are shown in mode 5 too: _coupling_plot_traces
        # already READS tc.plot_self / tc.plot_mutual for any trace routed to
        # the coupling path, mode 5 included, so hiding them left a Mode 5 user
        # unable to turn either off -- and never shown the hint explaining that
        # on a mutual curve L(nH) is M and C(pF) is C_c.
        show(self.ed_plot_self_cb, rows_mode)
        show(self.ed_plot_mutual_cb, rows_mode)
        show(self.ed_mutual_hint, rows_mode)
        show(self.ed_conn_head, custom)
        show(self.ed_conn_table, custom)
        show(self.ed_conn_hint, custom)
        show(self.ed_overview, custom)
        show(self.ed_validation, custom)

        # Update placeholders to match the active mode
        self.ed_porta.set_placeholder(
            MODE_PLACEHOLDERS["port_a"].get(mode, ""))
        self.ed_portb.set_placeholder(
            MODE_PLACEHOLDERS["port_b"].get(mode, ""))
        self.ed_short.set_placeholder(
            MODE_PLACEHOLDERS["short_pairs"].get(mode, ""))
        self.ed_gnd.set_placeholder(
            MODE_PLACEHOLDERS["gnd"].get(mode, ""))
        # Neither table needs per-mode placeholders: their hints live
        # permanently under them (MP_TABLE_HINT / CONN_TABLE_HINT), where --
        # unlike a PlaceholderEntry -- focus cannot delete them.

        # Which rows exist just changed, so the scroll region is stale. The
        # inner frame's <Configure> does NOT cover this -- see the docstring.
        # preserve=False: a now-short form must not stay scrolled out of sight.
        self._refresh_editor_scrollregion(preserve=False)
        if custom:
            # The tables' on_change does not fire on set_rows, so the strips
            # would otherwise still show the previous mode's spec.
            self._refresh_editor_strips()

    # ------------------------------------------------- Mode 5 editor plumbing

    def _editor_nports(self) -> Optional[int]:
        """Port count of the file the editor currently points at, or None."""
        fe = self._file_by_label(self.ed_file_var.get())
        return fe.ts.nports if fe is not None else None

    def _refresh_port_choices(self) -> None:
        """
        Fill the Port / To dropdowns with the current file's port numbers.

        Numbers, not names: measured, a ttk Combobox's popdown is only as wide
        as the widget, so a 7-char Port cell shows '12: VDD_bal…' truncated in
        the list as well as in the cell.  A name-bearing dropdown needs ~105 px
        the editor does not have; the names stay reachable through Show Ports.
        """
        n = self._editor_nports() or 0
        values = [str(i) for i in range(1, n + 1)]
        self.ed_conn_table.set_column_values("ports", values)
        self.ed_conn_table.set_column_values("to", values)

    def _on_editor_file_changed(self) -> None:
        self._refresh_port_choices()
        if self.ed_mode_var.get() == 5:
            self._refresh_editor_strips()

    def _on_editor_rows_changed(self) -> None:
        """RowTable on_change: fires on EVERY keystroke in EVERY cell."""
        if self._suppress_editor_sync:
            return
        self._refresh_editor_scrollregion(preserve=True)
        self._schedule_editor_sync()    # also refreshes the strips
        if self.ed_mode_var.get() == 6:
            self._refresh_editor_strips()   # for the style preview's span

    def _refresh_editor_strips(self) -> None:
        """Queue a strip refresh for the next idle moment, coalescing repeats."""
        if self._ed_strips_pending:
            return
        self._ed_strips_pending = True
        self.after_idle(self._apply_editor_strips)

    def _apply_editor_strips(self) -> None:
        """
        Recompute the port-overview strip, the validation strip, the
        kept-as-text indicator on the Connections caption, and the style
        preview's curve-count.

        Writes to nothing but those Labels and the preview -- _sync_editor_to_trace
        stays the only writer to a TraceConfig -- and never lets an exception
        escape. This is reached from a Tcl variable trace, where a raised error
        does not reach a handler we control: Tk prints it to stderr and the GUI
        carries on showing a stale strip that says the spec is fine.
        """
        self._ed_strips_pending = False
        if not self.ed_overview.winfo_exists():
            return
        try:
            mports = self.ed_mp_table.get_rows()
            conn = self.ed_conn_table.get_rows()
            extra = self._ed_extra_lines
            nports = self._editor_nports()
            try:
                term = build_terminations_rows(mports, conn, extra,
                                               nports=nports)
            except Exception:
                term = None
            self.ed_style.set_span(self._editor_curve_span(term))
            self.ed_overview.configure(
                text=_port_overview_text(term, nports))
            self.ed_validation.configure(text=_validation_strip_text(
                _validation_messages(mports, conn, extra, nports)))
            self.ed_extra_lbl.configure(text=_extra_lines_indicator(extra))
        except Exception as e:
            # Belt and braces: _validation_messages already swallows its own
            # errors, but this is the last frame before Tcl and nothing beyond
            # it can report a failure.
            self.ed_overview.configure(text="")
            self.ed_validation.configure(text=f"⚠ {e}")

    def _editor_curve_span(self, term) -> int:
        """
        How many consecutive palette slots the trace being edited will use.

        A coupling trace with G measurement ports draws G self curves and
        G*(G-1)/2 mutual ones, each taking the NEXT colour
        (_coupling_plot_traces), so the style preview would otherwise show one
        line for something that arrives as six. Returns 1 whenever that cannot
        be answered -- an unresolvable spec is not the preview's problem.
        """
        mode = self.ed_mode_var.get()
        if mode == 6:
            # Mode 6 is one measurement port per table row, so count the rows.
            # NOT through build_terminations_rows: that goes via the DSL, where
            # 'b' is the legacy alias for the minus side of 'A', so two rows
            # named a/b resolve to ONE measurement port and the preview would
            # show a span of 1 for a trace Calculate refuses outright.
            G = len([r for r in self.ed_mp_table.get_rows()
                     if r.plus.strip() or r.minus.strip()])
        elif mode == 5 and term is not None:
            try:
                G = len(resolve_meas_ports(term, self._editor_nports() or 0))
            except Exception:
                return 1
        else:
            return 1
        n = (G if self.ed_plot_self_var.get() else 0)
        if self.ed_plot_mutual_var.get() and G >= 2:
            n += G * (G - 1) // 2
        return max(1, n)

    def _editor_dsl_text(self) -> str:
        """
        The DSL the two tables currently serialise to -- what the text dialog
        shows.  Built from the LIVE tables, not from the selected trace, so an
        edit that has not been applied yet is visible there.
        """
        return rows_to_dsl_text(self.ed_mp_table.get_rows(),
                                self.ed_conn_table.get_rows(),
                                self._ed_extra_lines)

    def _on_edit_as_text(self) -> None:
        """
        The escape hatch: show the DSL the tables serialise to, and take it back.

        MODAL on purpose.  A non-modal editing surface is not part of the editor
        form, so Calculate's auto-sync would push the tables and silently ignore
        whatever is typed in the dialog -- which is the exact opposite of what
        the button promises.
        """
        try:
            initial = self._editor_dsl_text()
        except Exception as e:
            # rows_to_dsl_text refuses a cell it cannot serialise (an R/L/C
            # value with a space in it).  There is no text to show, and letting
            # this escape would be an unhandled Tk traceback.
            messagebox.showerror(
                "Cannot show the text",
                f"{e}\n\nFix the cell in the connections table first; the "
                "validation strip names it.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Edit as text")
        dlg.transient(self)

        ttk.Label(dlg, text=TEXT_DIALOG_NOTE, wraplength=560, justify=tk.LEFT,
                  foreground=PLACEHOLDER_FG).pack(side=tk.TOP, anchor="w",
                                                  padx=8, pady=(8, 4))

        def _ok() -> None:
            text = box.get("1.0", "end-1c")
            dlg.destroy()
            self._import_text_into_tables(text)

        # THE FOOTER IS PACKED FIRST, side=BOTTOM.  pack unmaps what does not
        # fit starting from the END, so packing it after the expand=True Text
        # made both buttons winfo_ismapped() == 0 the moment the dialog was
        # dragged shorter than its natural height -- and with grab_set() and no
        # keyboard commit there was then no way to apply the edit at all.
        # Same rule as Global Controls and the editor footer in the main window.
        foot = ttk.Frame(dlg)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Cancel", command=dlg.destroy
                   ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(foot, text="OK", command=_ok).pack(side=tk.RIGHT, padx=2)

        # Plain tk.Text, not PlaceholderText: a placeholder here would be a
        # get_value() trap, and the hint above is a Label that focus cannot
        # delete.  It gets a scrollbar because a spec long enough to want this
        # dialog is long enough to overflow it.
        wrap = ttk.Frame(dlg)
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        vsb = ttk.Scrollbar(wrap, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        box = tk.Text(wrap, width=80, height=24, font=("Consolas", 9),
                      yscrollcommand=vsb.set)
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=box.yview)
        box.insert("1.0", initial)

        # Escape cancels.  Return is deliberately NOT bound: this is a
        # multi-line editor and Return has to insert a newline.
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.grab_set()
        box.focus_set()
        self.wait_window(dlg)

    def _import_text_into_tables(self, text: str) -> None:
        """
        OK on the text dialog: import the text and re-render both tables
        IMMEDIATELY, so the user sees the canonical rewrite rather than
        discovering it later.
        """
        mports, conn, extra, changed = _import_dsl_text(text)
        if changed:
            self.ed_mp_table.set_rows([])
            self.ed_conn_table.set_rows([])
            self._ed_extra_lines = text.rstrip()
            messagebox.showwarning(
                "Kept as text",
                "This spec is kept verbatim instead of being moved into the "
                "table.\n\nThe table emits every measurement port before every "
                "connection, which is what makes a later 'ground' win. Your "
                "spec depends on the order it is written in, so importing it "
                "would change which port wins:\n\n"
                + (_ordering_diff_summary(text) or "(the resolved spec differs)")
                + "\n\nIt still computes exactly what it computed before.")
        else:
            self.ed_mp_table.set_rows(mports)
            self.ed_conn_table.set_rows(conn)
            self._ed_extra_lines = extra
        self._refresh_port_choices()
        self._refresh_editor_strips()
        self._refresh_editor_scrollregion(preserve=True)

    # ------------------------------------------------------------- auto-apply
    #
    # The editor writes itself into the selected trace as you type; there is no
    # "Apply" step any more.  Three properties make that safe, and none of them
    # is optional:
    #
    #  1. It is DEFERRED to after_idle, never run straight from the variable
    #     trace.  PlaceholderEntry._show_if_empty() sets the variable BEFORE it
    #     sets _showing (see that method), and Tcl runs write traces
    #     synchronously inside .set() -- so a synchronous handler reads
    #     get_value() while the flag still says "not showing" and stores the
    #     grey hint text as if the user had typed it.  By the idle pass the flag
    #     is right.  This is the reason for the deferral, not performance.
    #  2. It captures the TraceConfig OBJECT, not the Listbox index, and any
    #     pending sync is FLUSHED before the selection changes.  Resolving the
    #     target when the callback finally runs would let "type in A, click B"
    #     write B's freshly loaded editor content into A -- silently losing the
    #     edit that scheduled the callback, which is the exact failure this
    #     whole feature exists to remove.
    #  3. It never raises and never opens a dialog.  It runs from a Tcl
    #     variable trace, where an exception reaches no handler we control (Tk
    #     prints it and carries on), and a modal messagebox raised while the
    #     user is typing is a hang.  Calculate keeps its error dialog.

    def _schedule_editor_sync(self, *_args) -> None:
        """Queue a push of the editor into the selected trace."""
        if self._suppress_editor_sync:
            return
        idx = self._sel_idx(self.traces_lb)
        if idx is None or idx >= len(self.traces):
            return
        self._ed_sync_target = self.traces[idx]
        if self._ed_sync_after is None:
            self._ed_sync_after = self.after_idle(self._apply_editor_sync)

    def _flush_editor_sync(self) -> None:
        """Run any queued sync NOW, against the trace it was queued for."""
        if self._ed_sync_after is None:
            return
        try:
            self.after_cancel(self._ed_sync_after)
        except Exception:
            pass
        self._ed_sync_after = None
        self._apply_editor_sync()

    def _apply_editor_sync(self) -> None:
        self._ed_sync_after = None
        tc = self._ed_sync_target
        self._ed_sync_target = None
        # A trace deleted between scheduling and running is not an error.
        # Identity, not `in`: TraceConfig is an eq=True dataclass holding numpy
        # arrays, so `tc not in self.traces` compares field by field and raises
        # "truth value of an array is ambiguous" the moment it reaches a Z that
        # is not the same object.
        if tc is None or not any(t is tc for t in self.traces):
            return
        before_spec = _config_signature(tc)
        before_draw = _draw_signature(tc)
        try:
            self._sync_editor_to_trace(tc)
        except Exception:
            return          # see (3) above -- Calculate will report it properly
        if _config_signature(tc) != before_spec and tc.Z is not None:
            # The curve on screen is older than the spec that now describes it.
            tc.stale = True
        self._refresh_trace_list()
        if _draw_signature(tc) != before_draw:
            self._replot_from_cache()
        if self.ed_mode_var.get() == 5:
            self._refresh_editor_strips()

    def _on_style_changed(self) -> None:
        self._schedule_editor_sync()

    def _on_enabled_toggled(self) -> None:
        self._schedule_editor_sync()

    def _sync_editor_to_trace(self, tc: TraceConfig) -> None:
        tc.enabled = bool(self.ed_enabled_var.get())
        tc.color_idx, tc.ls_idx = self.ed_style.get()
        tc.file_label = self.ed_file_var.get()
        tc.mode = int(self.ed_mode_var.get())
        tc.port_a = self.ed_porta.get_value()
        tc.port_b = self.ed_portb.get_value()
        tc.short_pairs = self.ed_short.get_value()
        tc.gnd_ports = self.ed_gnd.get_value()
        tc.mports = self.ed_mp_table.get_rows()
        tc.plot_self = bool(self.ed_plot_self_var.get())
        tc.plot_mutual = bool(self.ed_plot_mutual_var.get())
        # custom_text is deliberately NOT written: the two tables are the
        # storage and the DSL text is derived from them.  Writing both would
        # leave migrate_legacy_custom_text unable to tell a legacy trace from a
        # freshly synced one, and the text would overwrite the rows on the next
        # selection.
        tc.conn_rows = self.ed_conn_table.get_rows()
        tc.extra_lines = self._ed_extra_lines
        tc.label = self.ed_label.get_value() or f"trace_{tc.id}"

    # --------------------------------------------------------------- Calculate

    def _on_calculate_selected(self) -> None:
        """Recompute only the selected trace (the editor footer's button)."""
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            messagebox.showinfo("No trace", "Select a trace first.")
            return
        self._on_calculate(only=self.traces[idx])

    def _on_calculate(self, only: Optional[TraceConfig] = None) -> None:
        # Auto-sync editor for the currently selected trace before calculating.
        # Users routinely edit a field and press Calculate without applying;
        # with auto-apply that edit is usually already in, but a keystroke in
        # the same event burst as the click would still be sitting in the idle
        # queue, so the flush is what makes "what I see is what is computed"
        # unconditional.
        idx = self._sel_idx(self.traces_lb)
        if idx is not None:
            self._flush_editor_sync()
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

        scope = "" if only is None else f" [{only.id}] {only.label} only"
        self._append_result("\n=== Calculate @ {:.4g} GHz{} ==="
                            .format(f_rlc_hz / 1e9, scope))

        # First pass: compute Z and per-freq RLC; collect rows + fit_lines.
        result_rows: list[tuple] = []   # (tc, file_label, res)
        # (tc, line): post-table fit summaries.  The trace travels with the
        # line because _render_results drops the hidden ones, and a fit summary
        # for a row that is not in the table is an orphan.
        fit_lines: list[tuple] = []
        coupling_blocks: list[tuple] = []   # (tc, file_label, CouplingResult)
        for tc in self.traces:
            fe = self._file_by_label(tc.file_label)
            if fe is None:
                self._append_result(f"  [{tc.id}] {tc.label}: file '{tc.file_label}' not loaded")
                continue

            if only is not None and tc is not only:
                # Not recomputed -- but its last numbers still go in the table,
                # so "Calculate This Trace" narrows the WORK, not the report.
                # A table that shrank to one row would make the fast path look
                # like it had thrown the other traces away.
                if tc.coupling is not None:
                    coupling_blocks.append((tc, fe.label, tc.coupling))
                elif tc.rlc is not None:
                    result_rows.append((tc, fe.label, tc.rlc))
                continue

            # Drop last run's matrix so a failed or re-moded trace can never
            # export stale coupling data.
            tc.Zmat = None
            tc.mport_names = None
            tc.coupling = None
            tc.fit_freqs = None
            tc.fit_Z = None
            # About to be recomputed from the current spec, so whatever the
            # editor did since the last run is now accounted for.
            tc.stale = False

            # The validation strip is capped at two lines and points here for
            # the rest; this is what makes that pointer true. Only the OVERFLOW
            # is printed -- the first two are already on screen, and repeating
            # them for every clean trace would be noise.
            if tc.mode == 5:
                notes = _validation_messages(tc.mports, tc.conn_rows,
                                             tc.extra_lines, fe.ts.nports)
                if len(notes) > VALIDATION_STRIP_LINES:
                    self._append_result(f"  [{tc.id}] {tc.label}: spec notes")
                    for note in notes:
                        self._append_result(f"      {note}")

            try:
                term = self._build_termination(tc, nports=fe.ts.nports)
                n_mports = len(resolve_meas_ports(term, fe.ts.nports))
            except Exception as e:
                tc.Z = None
                self._append_result(f"  [{tc.id}] {tc.label}: ERROR {e}")
                self._append_result(traceback.format_exc())
                continue

            # Mode 6 -- and ANY spec that defines more than one measurement
            # port -- produces a G x G Z matrix, not one curve; it gets its own
            # results block and expands into several plot curves.
            #
            # Routing on the measurement-port count rather than on the mode is
            # what stops Mode 5 from silently reporting only the first port.
            # compute_z returns Zmat[:, 0, 0] and warns about the rest, which
            # is a wrong number with no visible difference -- and once the two
            # modes share an editor, "I defined two probes here" has to mean
            # the same thing in both.  A single-measurement-port spec still
            # takes the compute_z path, so every pre-existing trace stays
            # bit-identical (golden regression).
            if tc.mode == 6 or n_mports > 1:
                if tc.mode != 6:
                    self._append_result(
                        f"    [{tc.id}] {n_mports} measurement ports defined -- "
                        "reporting the full coupling matrix (M, k), same as "
                        "Mode 6.")
                try:
                    cres = self._calculate_coupling_trace(
                        tc, fe, f_rlc_hz, term=term)
                except Exception as e:
                    tc.Z = None
                    self._append_result(f"  [{tc.id}] {tc.label}: ERROR {e}")
                    self._append_result(traceback.format_exc())
                    continue
                coupling_blocks.append((tc, fe.label, cres))
                if do_fit:
                    fit_lines.append((
                        tc,
                        f"  fit[{tc.id}]: skipped -- a band fit applies to one Z "
                        "curve, and a +/- coupling trace expands into several."))
                continue

            try:
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
                        fit_lines.append((
                            tc,
                            f"  fit[{tc.id} {which}]: "
                            f"L={format_si(fit.L_henry, 'H')}, "
                            f"R_dc={format_si(fit.R_dc_ohm, 'Ω')}, "
                            f"R_ac={fit.R_ac_ohm_per_sqrtHz:.3g}Ω/√Hz, "
                            f"Q@center={fit.Q_at_center:.3g}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}"))
                    else:
                        fit_Z = eval_capacitor_model(fit, fit_freqs)
                        srf_str = ("nan" if math.isnan(fit.SRF_hz)
                                   else format_si(fit.SRF_hz, 'Hz'))
                        fit_lines.append((
                            tc,
                            f"  fit[{tc.id} {which}]: "
                            f"C={format_si(fit.C_farad, 'F')}, "
                            f"R_esr={format_si(fit.R_esr_ohm, 'Ω')}, "
                            f"L_esl={format_si(fit.L_esl_henry, 'H')}, "
                            f"SRF={srf_str}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}"))
                except Exception as e:
                    fit_lines.append((tc, f"  fit[{tc.id}] ERROR: {e}"))

            tc.fit_freqs = fit_freqs
            tc.fit_Z = fit_Z

        # Second pass: render the table, fit lines and coupling blocks.
        self._last_result_rows = result_rows
        self._last_fit_lines = fit_lines
        self._last_coupling_blocks = coupling_blocks
        self._render_results(result_rows, fit_lines, coupling_blocks)

        # Curves are built in ONE place, from the cache, so that Calculate and
        # a visibility toggle cannot drift apart in what they draw.  A full
        # Calculate does not keep the cursors -- every number on the plot is
        # new -- but a single-trace recompute does: that is the fast iteration
        # loop, and throwing away the cursors the user is reading would undo
        # most of what makes it fast.
        self._replot_from_cache(keep_cursors=only is not None)
        self.plot.set_marker_freq(f_rlc_hz)
        if self.traces and not any(tc.enabled for tc in self.traces):
            # The table lists plotted traces only, so with everything hidden it
            # is empty too -- do not claim "the numbers above are still
            # current" when there are no numbers above.
            self._append_result(
                "  (every trace has 'Plot: this trace' unchecked -- the plot is "
                "empty on purpose, and so is the table; they were measured, "
                "show one again or use Export CSV to read the numbers)")

    def _replot_from_cache(self, keep_cursors: bool = True) -> None:
        """
        Rebuild the plot from each trace's cached Z / Zmat.

        This is what makes the visibility checkbox worth having: hiding a curve
        must not re-run the reduction, which on a 153-port package file is
        seconds of work to produce numerically identical results.  Colour,
        linestyle and the self/mutual choice are read fresh every time, so they
        follow the editor without a Calculate too.

        `keep_cursors` is on by default: a toggle is a change of view, and the
        V lines the user placed are part of what they were looking at.
        """
        plot_traces: list[PlotTrace] = []
        for tc in self.traces:
            if not tc.enabled:
                continue
            fe = self._file_by_label(tc.file_label)
            if fe is None:
                continue
            if tc.Zmat is not None and tc.mport_names:
                plot_traces.extend(
                    self._coupling_plot_traces(tc, fe, tc.Zmat, tc.mport_names))
            elif tc.Z is not None:
                plot_traces.append(PlotTrace(
                    label=tc.label,
                    freqs=fe.ts.freqs,
                    Z=tc.Z,
                    color_idx=tc.color_idx,
                    ls_idx=tc.ls_idx,
                    fit_freqs=tc.fit_freqs,
                    fit_Z=tc.fit_Z,
                ))
        self.plot.set_traces(plot_traces, keep_cursors=keep_cursors)

    def _calculate_coupling_trace(self, tc: TraceConfig, fe: FileEntry,
                                  f_rlc_hz: float,
                                  term: TerminationSet | None = None) -> object:
        """
        Reduce to the G x G measurement-port Z matrix and extract the coupling
        result at the marker frequency.  Returns the CouplingResult.

        Caches Zmat / mport_names on the trace; the curves themselves are built
        later by _replot_from_cache, which is the single place that turns a
        computed trace into plot curves.

        Used by Mode 6 and by any Mode 5 spec that defines more than one
        measurement port.  `term` may be passed in when the caller has already
        built it, which is the normal path -- the caller has to build it anyway
        to count the measurement ports.
        """
        if term is None:
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

        # The emptiness CONDITION of _coupling_plot_traces, not a call to it:
        # building the curves here just to test the list would recompute
        # _coupling_k_array over every frequency for every pair, and then throw
        # it away -- _replot_from_cache builds them for real a moment later.
        if not (tc.plot_self or (tc.plot_mutual and len(names) >= 2)):
            self._append_result(
                f"    [{tc.id}] both 'self' and 'mutual' are unchecked -- "
                "nothing plotted for this trace")
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
        """
        Print the table, the fit summaries and the coupling blocks -- for the
        traces that are ON THE PLOT.

        A hidden trace is filtered out here rather than at collection time, so
        `_last_result_rows` still holds everything and a units-mode re-render
        follows the visibility as it stands then.  Its numbers are not lost:
        they stay cached on the trace (re-showing it needs no Calculate), the
        line under the table names it, and Export CSV still writes it out with
        a `Plotted: no` comment.
        """
        units = self.units_mode_var.get()
        shown_rows = [r for r in rows if r[0].enabled]
        shown_blocks = [b for b in coupling_blocks if b[0].enabled]
        hidden = [r[0] for r in rows if not r[0].enabled]
        hidden += [b[0] for b in coupling_blocks if not b[0].enabled]

        if shown_rows:
            self._append_result(_format_results_table(shown_rows, units))
            for tc, fl in fit_lines:
                if tc.enabled:
                    self._append_result(fl)
        for tc, file_label, cres in shown_blocks:
            self._append_result("")
            self._append_result(
                _format_coupling_block(tc, file_label, cres, units))
        if hidden:
            # Named, not silently dropped: Calculate still measured them, and
            # the CSV still carries them, so the report has to say where they
            # went -- otherwise re-reading it later, a trace is simply missing.
            self._append_result(
                "  hidden (measured, not plotted, still in Export CSV): "
                + ", ".join(f"[{tc.id}] {_trunc_str(tc.label, 18)}"
                            for tc in hidden))

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
            # Through the rows, never through tc.custom_text: the tables are
            # the storage and the DSL text is derived from them.  nports lets
            # the builder reject a port the file does not have -- Mode 5 used
            # to pass none, so '3 / 5' on a 4-port file became a plausible
            # wrong number until compute_z_matrix's backstop caught it.
            return build_terminations_rows(tc.mports, tc.conn_rows,
                                           tc.extra_lines, nports=nports)
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
                    # Hidden traces ARE exported -- visibility gates the plot,
                    # not the measurement -- so the file has to say which ones
                    # were not on screen, or a CSV and a screenshot of the same
                    # session disagree with nothing to explain it.
                    hidden = "" if tc.enabled else ", Plotted: no"
                    fh.write(f"# File: {fe.label}, Mode: {tc.mode_name()}"
                             f"{hidden}\n")
                    # Gate on the DATA, not the mode -- _on_calculate routes on
                    # the measurement-port count, so a Mode 5 spec with two
                    # probes has a full Zmat too.  Gating on `mode == 6` used to
                    # export that trace's scalar Zmat[:, 0, 0] table instead:
                    # well-formed, headed '# Mode: Custom', and missing every
                    # mutual term, every M and every k.
                    if tc.Zmat is not None:
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
        """
        Re-render the trace list, but only when it would actually look
        different.

        Auto-apply calls this on every keystroke, and rebuilding a Listbox
        resets its scroll position -- so a user editing trace 9 of 12 would be
        yanked back to the top on each character.  Comparing the rendered
        strings costs nothing and is what makes the live list usable.
        (Programmatic delete / insert / selection_set do NOT fire
        <<ListboxSelect>>, verified on Tk 8.6, so this cannot re-enter
        _on_trace_selected and reload the editor mid-typing.)
        """
        lines = [tc.info_str() for tc in self.traces]
        if lines == self._trace_list_shown:
            return
        self._trace_list_shown = lines
        sel = self._sel_idx(self.traces_lb)
        self.traces_lb.delete(0, tk.END)
        for i, (tc, line) in enumerate(zip(self.traces, lines)):
            self.traces_lb.insert(tk.END, line)
            if not tc.enabled:
                # itemconfig does not survive delete(), so it is re-applied
                # here every time rather than at the point of the toggle.
                self.traces_lb.itemconfig(i, foreground="#909090")
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
