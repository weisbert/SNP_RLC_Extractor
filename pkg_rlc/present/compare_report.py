"""
pkg_rlc/present/compare_report.py  --  Compare files as TEXT, and the
computation of one (reference, compared file) pair with no App.

"I have the inductor extracted to 30 GHz and again to 50 and 80 GHz: inside
the band they share, are the others the 30 GHz one?"  ONE reference against N
files.  Every (A, B_i) pair is computed on its own by `compare_pair` -- its own
overlap band, its own grid, through the pairwise `similarity.compare_s` /
`compare_z` -- and read out at two levels:

  * the FILE -- the largest |S_B - S_A| over every entry;
  * what was EXTRACTED -- the setup the user DEFINED (measurement ports +
    connection rows, the one row model) solved on both files, and the signed
    difference of L, Q and R, B against A.  There is NO default setup: with
    none defined the extracted level says "No setup defined" and only S is
    judged.

What it returns is text: `verdict_strip_lines` (one line per compared file, a
mark and one sentence) and `compare_summary_lines` (the full reading of one
pair, written for someone DECIDING: IN SHORT, WHAT TO DO NEXT, then the
evidence).  The rules those follow are in docs/conventions/compare_files.md.

L3: imports L0 (`similarity`, `core`) and L1 (`validate`, `trace`) only.  No
tkinter, no matplotlib.
"""

from __future__ import annotations

import copy
import math
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from pkg_rlc.physics import similarity as sim
from pkg_rlc.physics.core import (build_terminations_rows, compute_z_matrix,
                                  format_freq, probe_display_names)
from pkg_rlc.model.validate import trace_is_composed

__all__ = [
    "SAME", "DIFFERENT", "NO_SETUP", "MARK_SAME", "MARK_DIFFERENT",
    "MARK_NOT_COMPARED", "S_LIMIT_PCT_DEFAULT", "Limits", "PortCompare",
    "CompareResult", "PairVerdict", "verdict", "compare_pair",
    "compare_against", "setup_is_empty", "setup_words", "pair_verdict",
    "verdict_strip_lines", "compare_summary_lines", "s_matrix_lines",
    "line_tags", "pct_view_span", "not_judged_spans", "trace_setup_choices",
    "copy_trace_setup", "default_reference", "WRAP_AT",
]

SAME = "SAME"
DIFFERENT = "DIFFERENT"

#: What the extracted level says when the user has defined no setup.  Said,
#: not papered over with "port 1 to ground": a default nobody chose decided
#: the big verdict of the first Compare window (design_workspaces.md § 0).
NO_SETUP = "No setup defined"

#: The verdict strip's marks.  "?" is neither: nothing could be compared.
MARK_SAME = "✓"          # check mark
MARK_DIFFERENT = "✗"     # ballot x
MARK_NOT_COMPARED = "?"

#: The S limit the workspace opens with, in percent of full scale (-40 dB).
S_LIMIT_PCT_DEFAULT = float(sim.db_to_pct(sim.DEFAULT_S_LIMIT_DB))

#: The reading is wrapped here, a little inside a pane's default width.
WRAP_AT = 96

#: Above this many ports the full difference table is not printed -- it
#: would be wider than any pane -- and the ranked list stands in for it.
S_MATRIX_MAX_PORTS = 32
#: How many port pairs the ranked list names.
S_RANK_SHOWN = 5
#: Below this, an entry is the same to printing precision (identical data
#: floors at -300 dB) and reads '.' rather than a number nobody can use.
S_IDENTICAL_DB = -200.0


# ============================================================================
# The result of one pair
# ============================================================================

@dataclass(frozen=True)
class Limits:
    """The reader's limits, all in PERCENT ("1 %" is what a reader means by
    close; a dB box for S was the first thing the owner could not read)."""
    s_pct: float = S_LIMIT_PCT_DEFAULT
    l_pct: float = sim.DEFAULT_L_LIMIT_PCT
    q_pct: float = sim.DEFAULT_Q_LIMIT_PCT

    @classmethod
    def of(cls, limits) -> "Limits":
        """A Limits, or an (s, l, q) tuple, as a Limits."""
        if isinstance(limits, Limits):
            return limits
        s, l, q = limits
        return cls(float(s), float(l), float(q))


@dataclass
class PortCompare:
    name: str
    z: sim.ZCompare


@dataclass
class CompareResult:
    """Everything one (reference A, compared file B) pair produced.  Each
    level fails on its own: `s` is None with `s_why` set, `ports` is empty
    with `z_why` set."""
    label_a: str
    label_b: str
    setup: str                           # the setup, in words ("" = none)
    s: Optional[sim.SCompare] = None
    s_why: str = ""                      # why S was not compared
    ports: list = field(default_factory=list)   # [PortCompare]
    z_why: str = ""                      # why L/Q/R were not compared
    notes: list = field(default_factory=list)
    marker_hz: float = float("nan")      # the workspace's marker
    span_a: str = ""                     # each file's own sweep, in words
    span_b: str = ""
    has_setup: bool = True               # False: "No setup defined"


def verdict(value: float, limit: float, *, db: bool) -> str:
    """SAME / DIFFERENT / "" (nothing comparable).  A dB error is compared as
    is (lower is better); a percentage by its magnitude."""
    if not (isinstance(value, float) and math.isfinite(value)):
        return ""
    if db:
        return SAME if value <= limit else DIFFERENT
    return SAME if abs(value) <= abs(limit) else DIFFERENT


# ============================================================================
# The computation -- no App
# ============================================================================

def setup_is_empty(mports: Sequence, conn_rows: Sequence) -> bool:
    """True when the setup tables hold nothing to measure: no non-blank
    measurement-port row.  Connection rows alone measure nothing."""
    return not any(not r.is_blank() for r in (mports or ()))


def setup_words(mports: Sequence, conn_rows: Sequence) -> str:
    """The setup in one line: 'P1 (+1 -2); short 3,4; every other port
    open'.  "" for an empty setup."""
    if setup_is_empty(mports, conn_rows):
        return ""
    names = probe_display_names(mports)
    parts = []
    for name, r in zip(names, mports):
        if r.is_blank():
            continue
        side = f"+{r.plus.strip()}" if r.plus.strip() else "+?"
        if r.minus.strip():
            side += f" -{r.minus.strip()}"
        parts.append(f"{name} ({side})")
    for r in conn_rows or ():
        if r.is_blank() or not getattr(r, "enabled", True):
            continue
        txt = f"{r.kind} {r.ports.strip()}"
        if r.to.strip():
            txt += f" to {r.to.strip()}"
        rlc = " ".join(f"{k}={getattr(r, k).strip()}" for k in ("R", "L", "C")
                       if getattr(r, k).strip())
        if rlc and r.kind in ("rlc_gnd", "rlc_between"):
            txt += f" {rlc}"
        parts.append(txt)
    parts.append("every port not listed open")
    return "; ".join(parts)


def _extracted_z(fe, mports, conn_rows) -> list:
    """[(port name, Z(f))] for this setup on this file, on its own grid.
    The one row model's rules apply: a refused setup raises its reason."""
    term = build_terminations_rows(mports, conn_rows, "",
                                   nports=fe.ts.nports)
    Zmat, names, _warns = compute_z_matrix(fe.Y, fe.ts.freqs, term)
    names = list(names) or ["Z"]
    return [(names[g] if g < len(names) else f"port {g + 1}", Zmat[:, g, g])
            for g in range(Zmat.shape[1])]


def _span(fe) -> str:
    try:
        return fe.ts.freq_span_str()
    except Exception:                                       # noqa: BLE001
        return ""


def compare_pair(fe_a, fe_b, mports: Sequence = (), conn_rows: Sequence = (),
                 marker_hz: float = float("nan")) -> CompareResult:
    """
    One reference `fe_a` against one compared file `fe_b` (anything with
    `.label`, `.ts` and `.Y` -- a FileEntry).

    Both levels, each failing on its own: a port-count mismatch stops the S
    comparison, not the L / Q one, and a setup that one file cannot hold stops
    L / Q, not S.  An empty setup is "No setup defined" -- S only.
    """
    words = setup_words(mports, conn_rows)
    res = CompareResult(
        label_a=fe_a.label, label_b=fe_b.label, setup=words,
        marker_hz=marker_hz, span_a=_span(fe_a), span_b=_span(fe_b),
        has_setup=bool(words))
    if fe_a is fe_b:
        res.notes.append("A and B are the same loaded file.")
    try:
        res.s = sim.compare_s(fe_a.ts.freqs, fe_a.ts.s, fe_a.ts.z0,
                              fe_b.ts.freqs, fe_b.ts.s, fe_b.ts.z0)
    except Exception as e:                                  # noqa: BLE001
        res.s_why = str(e)
    if not words:
        res.z_why = (f"{NO_SETUP}. Define the measurement ports (and any "
                     f"connections) to compare L, Q and R; only the raw "
                     f"S-parameters are judged.")
        return res
    try:
        try:
            za = _extracted_z(fe_a, mports, conn_rows)
        except Exception as e:                              # noqa: BLE001
            raise ValueError(f"on {fe_a.label}: {e}") from e
        try:
            zb = _extracted_z(fe_b, mports, conn_rows)
        except Exception as e:                              # noqa: BLE001
            raise ValueError(f"on {fe_b.label}: {e}") from e
        if len(za) != len(zb):
            raise ValueError("the setup gives a different number of "
                             "measurement ports on the two files")
        for (name, a), (_n, b) in zip(za, zb):
            res.ports.append(PortCompare(
                name, sim.compare_z(fe_a.ts.freqs, a, fe_b.ts.freqs, b)))
    except Exception as e:                                  # noqa: BLE001
        res.ports = []
        res.z_why = f"The setup could not be solved {e}" \
            if str(e).startswith("on ") else str(e)
    return res


def compare_against(fe_ref, fe_others: Sequence, mports: Sequence = (),
                    conn_rows: Sequence = (),
                    marker_hz: float = float("nan")) -> list:
    """[CompareResult] -- the reference against each compared file, in
    order, each pair on its own (own band, own grid)."""
    return [compare_pair(fe_ref, fe, mports, conn_rows, marker_hz)
            for fe in fe_others]


def default_reference(files: Sequence):
    """The file with the LOWEST top frequency -- the one whose whole sweep is
    inside the others'.  "Is the 80 GHz run the 30 GHz run, below 30 GHz?"
    reads the 80 GHz one against the 30 GHz one.  First wins a tie; None for
    no files."""
    best = None
    for fe in files:
        if best is None or fe.ts.freqs[-1] < best.ts.freqs[-1]:
            best = fe
    return best


def trace_setup_choices(traces: Sequence) -> list:
    """(display text, trace) for "Copy setup from trace…": every single-file
    trace.  A composed trace is left out -- its port numbers name ports of
    several files, which no one file has alone."""
    return [(f"[{tc.id}] {tc.label}", tc) for tc in traces
            if not trace_is_composed(tc)]


def copy_trace_setup(tc) -> tuple:
    """(mports, conn_rows, note): a one-shot DEEP copy of the trace's setup,
    migrated into the one row model on a copy (the trace is not touched), so
    nothing links the two afterwards.  `note` is non-empty when the trace has
    text lines the tables cannot hold, which are not copied."""
    t = copy.deepcopy(tc)
    try:
        t.migrate_to_rows()
    except Exception:                                       # noqa: BLE001
        pass
    note = ""
    if (getattr(t, "extra_lines", "") or "").strip():
        n = len([ln for ln in t.extra_lines.splitlines() if ln.strip()])
        note = (f"{n} text line{'s' if n != 1 else ''} of this trace's setup "
                f"cannot be shown in the tables and {'were' if n != 1 else 'was'}"
                f" not copied.")
    return list(t.mports), list(t.conn_rows), note


# ============================================================================
# Numbers in words
# ============================================================================

def _num(p: float) -> str:
    """A positive percentage's number, never in scientific notation:
    '280', '97', '9.7', '0.12', '<0.001'.  From 10 up it is a whole number --
    '%.2g' printed 99.7 as '1e+02'."""
    if p < 0.001:
        return "<0.001"
    if p >= 10:
        return f"{p:.0f}"
    # Three significant digits below 10: two printed 1.04 % as '1' beside a
    # 1 % limit and then called it OVER (stage-3 review).
    return f"{p:.3g}"


def _factor(x: float) -> str:
    """A multiple, two significant digits, never scientific: 3.8, 12, 140."""
    a = abs(x)
    txt = f"{a:.0f}" if a >= 10 else f"{a:.2g}"
    return ("-" if x < 0 else "") + txt


def _rel(v: float) -> str:
    """How B relates to A, from a signed percentage, WITHOUT 'B is':
    '0.42 % higher than A', '9.7 % lower than A', '3.8 × A' (from +100 %
    up a percentage is not a sentence anybody says -- `report._delta_cell`'s
    rule), '-1 × A (the sign flipped)', 'identical to A'."""
    if not math.isfinite(v):
        return "not comparable"
    if v == 0:
        return "identical to A"
    if abs(v) < 100:
        return f"{_num(abs(v))} % {'higher' if v > 0 else 'lower'} than A"
    f = 1.0 + v / 100.0
    if abs(f) < 0.01:
        # -100.0004 % is B ~ 0, not '-4e-06 × A'.
        return "about zero where A is not"
    tail = " (the sign flipped)" if f < 0 else ""
    return f"{_factor(f)} × A{tail}"


def _b_is(v: float) -> str:
    """'B is 0.42 % higher than A' / 'B is 3.8 × A'."""
    return "B is " + _rel(v)


def _pct_str(p: float) -> str:
    """A positive percentage of full scale (S), as many digits as it needs."""
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


def _freq_or_none(f: float) -> str:
    return format_freq(f) if math.isfinite(f) else "none in range"


def _wrap(text: str, width: int, indent: str = "") -> list:
    hang = "  " if text.startswith("- ") else ""     # a bullet hangs
    return textwrap.wrap(text, width=width, initial_indent=indent,
                         subsequent_indent=indent + hang) or [""]


def _columns(rows: Sequence, indent: str = "  ", gap: int = 2,
             right: Sequence = ()) -> list:
    """Rows of cells -> lines, every column as wide as its widest cell (the
    first version's fixed 24 ran 'B is 280 % higher than A' into the next
    column).  Columns in `right` are right-aligned."""
    n = max((len(r) for r in rows), default=0)
    w = [max((len(r[c]) for r in rows if c < len(r)), default=0)
         for c in range(n)]
    out = []
    for r in rows:
        cells = []
        for c, txt in enumerate(r):
            last = c == len(r) - 1
            if c in right:
                cells.append(txt.rjust(w[c]))
            else:
                cells.append(txt if last else txt.ljust(w[c]))
        out.append((indent + (" " * gap).join(cells)).rstrip())
    return out


# ============================================================================
# Judging one pair against the limits
# ============================================================================

@dataclass
class _Over:
    kind: str           # "S", "Z", or "LOSS" (B lossy where A is lossless)
    key: str            # "S" / "L" / "Q"
    tag: str            # " [port]" or ""
    value: float        # S: % of full scale; Z: signed %; LOSS: nan
    freq: float
    span: str           # " below 9.44 GHz" or ""
    entry: tuple = ()   # S only: (i, j)
    count: int = 0      # LOSS only: how many points


@dataclass
class PairVerdict:
    """What one pair comes to under the limits -- the strip line's facts."""
    mark: str                     # MARK_SAME / MARK_DIFFERENT / MARK_NOT_COMPARED
    sentence: str
    overs: list = field(default_factory=list)      # [_Over]
    within: list = field(default_factory=list)     # names judged and within
    band: str = ""


def _band(res: CompareResult) -> str:
    axis = res.s.axis if res.s is not None else (
        res.ports[0].z.axis if res.ports else None)
    if axis is None:
        return ""
    return f"{format_freq(axis.lo)} - {format_freq(axis.hi)}"


def _judge(res: CompareResult, lim: Limits) -> tuple:
    """(overs, within) -- what is over its limit and what was judged and is
    within.  The one place the verdict is decided, so the strip and the
    reading cannot disagree."""
    over: list = []
    within: list = []
    sc = res.s
    s_lim = abs(lim.s_pct)
    if sc is not None:
        worst = float(sim.db_to_pct(sc.worst_db))
        if worst > s_lim:
            over.append(_Over("S", "S", "", worst, sc.worst_f, "",
                              tuple(sc.worst_entry)))
        else:
            within.append("the raw S-parameters")
    multi = len(res.ports) > 1
    for pc in res.ports:
        z = pc.z
        tag = f" [{pc.name}]" if multi else ""
        if getattr(z, "n_b_lossy", 0):
            k = int(np.argmax(z.b_lossy))
            over.append(_Over("LOSS", "Q", tag, float("nan"),
                              float(z.axis.freqs[k]), "", (),
                              z.n_b_lossy))
        top = z.usable_limit()
        resonant = math.isfinite(top)
        for key, limit in (("L", lim.l_pct), ("Q", lim.q_pct)):
            w = z.worst_in(key, z.axis.lo, top) if resonant else \
                {"L": z.l, "Q": z.q}[key]
            if not math.isfinite(w.value):
                continue
            if abs(w.value) > abs(limit):
                span = f" below {format_freq(top)}" if resonant else ""
                over.append(_Over("Z", key, tag, w.value, w.freq, span))
            else:
                within.append(f"{key}{tag}")
    return over, within


def _over_sentence(o: _Over, who: str = "B", ref: str = "A") -> str:
    """One thing over its limit, in words."""
    if o.kind == "LOSS":
        subj = "B" if who == "B" else who
        refw = "A" if ref == "A" else ref
        return (f"{subj}{o.tag} has loss where {refw} is lossless "
                f"(Q below about 100 at {o.count} points, first at "
                f"{format_freq(o.freq)}) -- Q and R cannot be compared as a "
                f"ratio there")
    if o.kind == "S":
        i, j = o.entry
        return (f"the raw S-parameters differ by up to {_pct_str(o.value)} "
                f"-- worst is {_pair(i, j)}, at {format_freq(o.freq)}")
    where = format_freq(o.freq) if math.isfinite(o.freq) else "-"
    rel = _rel(o.value)
    if ref != "A":
        rel = rel.replace("than A", f"than {ref}").replace("× A",
                                                            f"× {ref}")
    if who == "B":
        return (f"{_QUANTITY[o.key]}{o.tag}: B is {rel}{o.span} "
                f"(worst at {where})")
    return (f"{who} {_QUANTITY[o.key]}{o.tag} is {rel}{o.span} "
            f"(worst at {where})")


def _setup_failure(res: CompareResult) -> str:
    """Why the setup gave no L / Q / R, SHORT -- it is repeated on every
    strip line, and the full refusal is in the red cell and in Details."""
    why = (res.z_why or "").strip().rstrip(".")
    if not why:
        return "the setup could not be solved"
    first = why.split(". ")[0].split(": ")[0]
    if len(first) > 70:
        first = first[:67].rstrip() + "..."
    return (f"the setup is refused ({first[:1].lower()}{first[1:]}; "
            f"see the red cell)")


def pair_verdict(res: CompareResult, limits) -> PairVerdict:
    """The pair's mark and ONE sentence, for the verdict strip."""
    lim = Limits.of(limits)
    over, within = _judge(res, lim)
    band = _band(res)
    if not over and not within:
        why = res.s_why or res.z_why or "nothing in common to compare"
        return PairVerdict(MARK_NOT_COMPARED,
                           f"could not be compared -- {why.rstrip('.')}.",
                           over, within, band)
    if over:
        first = _over_sentence(over[0], who="its", ref="the reference")
        more = f" (and {len(over) - 1} more)" if len(over) > 1 else ""
        # Say what was NOT looked at too: a ✗ on S alone must not read as if
        # L / Q / R had been compared.
        tail = ""
        if not res.has_setup:
            tail = " L, Q and R were not compared: no setup defined."
        elif not res.ports:
            tail = f" L, Q and R were not compared: {_setup_failure(res)}."
        return PairVerdict(MARK_DIFFERENT,
                           f"NOT the same in {band}: {first}{more}.{tail}",
                           over, within, band)
    short = {"the raw S-parameters": "raw S"}
    what = "judged: " + ", ".join(short.get(w, w) for w in within)
    nj = sum(getattr(pc.z, "n_not_judged", 0) for pc in res.ports)
    if nj:
        what += (f"; Q and R not judged at {nj} points where the reference "
                 f"is lossless")
    if not res.has_setup:
        what = "raw S-parameters only -- no setup defined"
    elif not res.ports:
        what = f"raw S-parameters only -- {_setup_failure(res)}"
    return PairVerdict(MARK_SAME,
                       f"the same as the reference within your limits in "
                       f"{band} ({what}).", over, within, band)


def verdict_strip_lines(results: Sequence, limits,
                        stale: bool = False) -> list:
    """
    One line per compared file: a mark (✓ the same, ✗ not, ? not compared)
    and one sentence in words.  With `stale`, a first line says the results
    are out of date -- they are never recomputed behind the reader's back.
    """
    out = []
    if stale:
        out.append("Out of date: the files, the reference or the setup "
                   "changed since this was computed -- press Compare.")
    if not results:
        out.append("Nothing compared yet: pick a reference and at least one "
                   "file to compare, then press Compare.")
        return out
    for res in results:
        v = pair_verdict(res, limits)
        out.append(f"{v.mark} {res.label_b}: {v.sentence}")
    return out


# ============================================================================
# The full reading of one pair
# ============================================================================

def compare_summary_lines(res: CompareResult, s_limit_pct: float,
                          l_limit_pct: float, q_limit_pct: float) -> list:
    """
    The reading of ONE pair, in the order a reader wants it: the ANSWER, what
    it means, what to do next -- and only then the evidence.

    Written for someone deciding, not auditing (the owner, on the first
    version: "根本看不懂").  So: no dB anywhere (a percentage of full scale
    instead), ports in words, "B is 9.7 % lower" instead of a signed ratio,
    from +100 % a multiple ("3.8 × A"), the working frequency's numbers up
    front, and when the worst L sits beside a self-resonance, a sentence
    saying that is what it is.
    """
    lim = Limits(s_limit_pct, l_limit_pct, q_limit_pct)
    s_lim = abs(s_limit_pct)
    over, within = _judge(res, lim)
    lines_s: list = []
    lines_z: list = []
    srf_hint = ""
    marker_rows: list = []
    marker_f = float("nan")
    not_judged_total = 0
    q_never_judged = False

    # ---- the raw file -------------------------------------------------
    sc = res.s
    if sc is not None:
        worst = float(sim.db_to_pct(sc.worst_db))
        i, j = sc.worst_entry
        ranked = [e for e in sc.ranked_entries() if e[2] > S_IDENTICAL_DB]
        n_pairs = 0 if sc.entry_db is None else sc.entry_db.size
        n_over = sum(1 for e in ranked if sim.db_to_pct(e[2]) > s_lim)
        is_over = worst > s_lim
        lines_s.append(f"  Largest difference: {_pct_str(worst)} -- "
                       f"{_pair(i, j)}, at {format_freq(sc.worst_f)}.")
        lines_s.append(f"  Your limit is {s_lim:g} %  ->  "
                       + ("OVER the limit." if is_over else "within it."))
        if n_pairs > 1:
            lines_s.append(f"  {n_over} of the {n_pairs} port pairs differ by "
                           f"more than {s_lim:g} %."
                           + (" The largest:" if ranked else
                              " Every pair is identical."))
        rows = []
        for ii, jj, db, f in (ranked[:S_RANK_SHOWN] if n_pairs > 1 else []):
            p = float(sim.db_to_pct(db))
            rows.append([f"S({ii},{jj})", _pct_str(p), f"at {format_freq(f)}"]
                        + (["over"] if p > s_lim else []))
        lines_s += _columns(rows, indent="     ", right=(1,))
        lines_s.append("  (1 % means the two S values are 0.01 apart; "
                       "|S| is never more than 1.)")
    else:
        lines_s.append(f"  Not compared: {res.s_why}")

    # ---- the inductor -------------------------------------------------
    multi = len(res.ports) > 1
    for pc in res.ports:
        z = pc.z
        tag = f" [{pc.name}]" if multi else ""
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
        nj = z.n_not_judged
        not_judged_total += nj
        rows = [["", head, "at", "your limit"]]
        for key, limit in (("L", l_limit_pct), ("Q", q_limit_pct),
                           ("R", None)):
            w = z.worst_in(key, lo, top) if resonant else \
                {"L": z.l, "Q": z.q, "R": z.r}[key]
            where = format_freq(w.freq) if math.isfinite(w.freq) else "-"
            if limit is None:
                judged = "(none)"
            elif not math.isfinite(w.value):
                judged = "-"
            elif abs(w.value) > abs(limit):
                judged = f"{abs(limit):g} %  OVER"
            else:
                judged = f"{abs(limit):g} %  ok"
            if key in ("Q", "R") and nj and not math.isfinite(w.value):
                said = "not judged (A is lossless)"
                if key == "Q":
                    q_never_judged = True
            else:
                said = _b_is(w.value)
            rows.append([_QUANTITY[key], said, where, judged])
        lines_z += _columns(rows)
        if resonant:
            wl = z.worst_in("L", top, z.axis.hi + 1.0)
            wq = z.worst_in("Q", top, z.axis.hi + 1.0)
            if math.isfinite(wl.value) or math.isfinite(wq.value):
                parts = []
                if math.isfinite(wl.value):
                    parts.append(f"L: {_b_is(wl.value)}")
                if math.isfinite(wq.value):
                    parts.append(f"Q: {_b_is(wq.value)}")
                lines_z += _wrap("Near and past the resonance (not judged), "
                                 "at worst -- " + "; ".join(parts) + ".",
                                 WRAP_AT, indent="  ")
            if not srf_hint and math.isfinite(z.srf_a) and \
                    math.isfinite(z.srf_b):
                shift = 100.0 * (z.srf_b - z.srf_a) / z.srf_a
                moved = ("not at all" if shift == 0 else
                         f"{_num(abs(shift))} % {'up' if shift > 0 else 'down'}")
                srf_hint = (
                    f"The self-resonance moved from {format_freq(z.srf_a)} "
                    f"(A) to {format_freq(z.srf_b)} (B), {moved}. "
                    f"Near it L and Q change very fast, which is why they "
                    f"differ much more there than below it.")
        excl = [(k, w.excluded) for k, w in (("L", z.l), ("Q", z.q),
                                              ("R", z.r)) if w.excluded]
        if excl:
            lines_z += _wrap("(" + ", ".join(
                f"{n} {k} point{'s' if n > 1 else ''}" for k, n in excl)
                + " skipped where A's value is almost zero -- a percentage "
                  "of nearly nothing means nothing.)", WRAP_AT, indent="  ")
        if nj:
            n_all = len(z.axis.freqs)
            lines_z += _wrap(
                f"({nj} of {n_all} points: Q and R not judged -- A is almost "
                f"lossless there, its resistance under "
                f"{sim.RE_JUDGE_FRAC * 100:g} % of |Z| (Q above about "
                f"{1 / sim.RE_JUDGE_FRAC:.0f}). That little loss is the "
                f"file's rounding and the solver's noise, and the percentage "
                f"of one noise against another is not a difference.)",
                WRAP_AT, indent="  ")
        at = z.at(res.marker_hz) if math.isfinite(res.marker_hz) else None
        if at is not None:
            dl, dq, dr, marker_f = at
            k = int(np.argmin(np.abs(z.axis.freqs - marker_f)))
            lossless_here = (z.qr_not_judged is not None
                             and bool(z.qr_not_judged[k]))
            qtxt = "not judged (A is lossless)" if lossless_here \
                else _b_is(dq)
            rtxt = "not judged" if lossless_here else _b_is(dr)
            marker_rows.append(
                f"    {tag.strip() + '  ' if tag else ''}"
                f"L: {_b_is(dl)}    Q: {qtxt}    R: {rtxt}")
    if not res.ports:
        lines_z.append(f"  Not compared: {res.z_why}")

    # ---- the answer, first --------------------------------------------
    band = _band(res)
    out = ["IN SHORT"]
    if not over and not within:
        out.append("  Nothing could be compared -- the reasons are below.")
    elif not over:
        out.append(f"  THE SAME within your limits, everywhere in {band}.")
    else:
        out.append(f"  NOT THE SAME in {band}:")
        for o in over:
            out += _wrap(f"- {_over_sentence(o)}", WRAP_AT, indent="    ")
        if within:
            out.append(f"  Within your limits: {', '.join(within)}.")
    if not res.has_setup:
        out += _wrap(f"{NO_SETUP}: only the raw S-parameters are judged. "
                     f"Define measurement ports to compare L, Q and R.",
                     WRAP_AT, indent="  ")
    elif not res.ports:
        out += _wrap(f"L, Q and R were not compared: {res.z_why}",
                     WRAP_AT, indent="  ")
    if q_never_judged:
        out += _wrap("Q and R are not judged anywhere: the reference is "
                     "almost lossless over the whole band.", WRAP_AT,
                     indent="  ")
    if srf_hint:
        out.append("")
        out += _wrap(srf_hint, WRAP_AT, indent="  ")
    if marker_rows:
        out.append("")
        out.append(f"  At the marker frequency, {format_freq(marker_f)}:")
        out += marker_rows
    elif res.ports and math.isfinite(res.marker_hz):
        out.append("")
        out.append(f"  The marker frequency ({format_freq(res.marker_hz)}) "
                   f"is outside the compared range -- no reading there.")

    out += ["", "WHAT TO DO NEXT"]
    out += _next_steps(res, over, srf_hint, band)

    out += ["", "WHAT WAS COMPARED"]
    out += _columns([["A (the reference):", res.label_a]
                     + ([f"({res.span_a})"] if res.span_a else []),
                     ["B:", res.label_b]
                     + ([f"({res.span_b})"] if res.span_b else [])])
    axis = sc.axis if sc is not None else (
        res.ports[0].z.axis if res.ports else None)
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
        out += _wrap(f"Note: {n}", WRAP_AT, indent="  ")

    out += ["", "1. THE RAW FILE -- every S-parameter of every port pair"]
    out += lines_s
    out += ["", "2. THE INDUCTOR -- "
            + (f"the setup ({res.setup}), solved on both files"
               if res.has_setup else NO_SETUP)]
    out += lines_z
    if sc is not None:
        table = s_matrix_lines(sc, s_lim)
        if table:
            out += [""] + table
    return out


def _next_steps(res: CompareResult, over: list, srf_hint: str,
                band: str) -> list:
    s_over = any(o.kind == "S" for o in over)
    z_over = any(o.kind in ("Z", "LOSS") for o in over)
    if not over and not res.ports and res.s is None:
        return ["  Fix what is named below, then press Compare again."]
    if not over:
        steps = [f"Nothing to do: B can stand in for A over {band} within "
                 f"your limits.",
                 "If your margin is tighter, lower the limits."]
        if not res.has_setup:
            steps.insert(1, "To compare the extracted L, Q and R as well, "
                            "define the measurement ports (or copy them from "
                            "a trace) and press Compare again.")
        if srf_hint:
            steps.insert(1, "If the circuit relies on the self-resonance "
                            "itself, look at the dotted lines on the L curve: "
                            "that is where the two differ.")
    else:
        steps = []
        if z_over:
            steps.append("The inductor itself differs. The L and Q curves "
                         "show over which frequencies; move the marker to "
                         "your operating frequency to read it there.")
        if s_over and not z_over and res.ports:
            steps.append("The raw files differ a little, but not in what "
                         "this port setup measures. If this setup is how the "
                         "part is used, the two files are equivalent for it.")
        elif s_over and res.s.entry_db is not None and \
                res.s.entry_db.shape[0] > 1:
            steps.append("The table at the end shows which port pairs "
                         "moved; the S curve shows at which frequencies.")
        steps.append("If your margin is looser than the limits, change them "
                     "-- the answer updates at once.")
    return [ln for st in steps for ln in _wrap(f"- {st}", WRAP_AT, "  ")]


def s_matrix_lines(sc, s_limit_pct: float) -> list:
    """Every port pair's largest difference over the band, as a table --
    WHICH ports moved, at a glance.  Up to S_MATRIX_MAX_PORTS.  Every column
    is as wide as the widest cell in the table, '*' marks over the limit."""
    n = 0 if sc.entry_db is None else sc.entry_db.shape[0]
    if n <= 1:
        return []
    if n > S_MATRIX_MAX_PORTS:
        return [f"ALL PORT PAIRS -- {n} ports are too many for a table; the "
                f"list in section 1 is ranked over all {n * n}."]
    cells = []
    for r in range(n):
        row = []
        for c in range(n):
            db = float(sc.entry_db[r, c])
            if db <= S_IDENTICAL_DB:
                row.append((".", False))
                continue
            p = float(sim.db_to_pct(db))
            row.append((_num(p), p > s_limit_pct))
        cells.append(row)
    w = max([len(str(n))] + [len(t) for row in cells for t, _o in row])
    rw = len(str(n))
    out = ["ALL PORT PAIRS -- the largest difference of each, in %",
           "  (row i, column j = S(i,j);   * = over your limit,   "
           ". = identical)",
           "  " + " " * rw + " " + "".join(f"{c:>{w}}  "
                                           for c in range(1, n + 1)).rstrip()]
    for r, row in enumerate(cells):
        txt = "".join(f"{t:>{w}}" + ("* " if o else "  ") for t, o in row)
        out.append(f"  {r + 1:>{rw}} " + txt.rstrip())
    return out


_HEADS = ("IN SHORT", "WHAT TO DO NEXT", "WHAT WAS COMPARED", "1. ", "2. ",
          "ALL PORT PAIRS")


def line_tags(ln: str) -> tuple:
    """Section headings bold; what is over a limit red -- in the reading and
    in the verdict strip (a ✗ line)."""
    if ln.startswith(_HEADS):
        return ("head",)
    if (ln.startswith(MARK_DIFFERENT + " ") or "NOT THE SAME" in ln
            or re.search(r"\bOVER\b", ln) or ln.endswith("  over")
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
    leaves out is COUNTED on the plot, the worst value is in the text.
    """
    v = np.asarray(abs_pct, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None, 0
    span = max(float(np.percentile(v, 98)) * 1.1, 2.0 * abs(limit))
    if float(v.max()) <= span:
        return None, 0
    return span, int(np.count_nonzero(v > span))


def not_judged_spans(freqs, mask) -> list:
    """[(f_lo, f_hi)] Hz: the runs of True in `mask` (a ZCompare's
    `qr_not_judged`), each widened to the midpoints to its neighbours so a
    single point is a visible band -- what the plot shades grey."""
    f = np.asarray(freqs, dtype=float)
    if mask is None or len(f) == 0:
        return []
    m = np.asarray(mask, dtype=bool)
    out = []
    k = 0
    n = len(f)
    while k < n:
        if not m[k]:
            k += 1
            continue
        j = k
        while j + 1 < n and m[j + 1]:
            j += 1
        lo = f[k] if k == 0 else 0.5 * (f[k - 1] + f[k])
        hi = f[j] if j == n - 1 else 0.5 * (f[j] + f[j + 1])
        out.append((float(lo), float(hi)))
        k = j + 1
    return out
