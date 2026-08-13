"""
pkg_rlc_core.py  --  Core computation for PKG RLC Extractor.

Touchstone parser (universal content-based, ignores extension), S<->Y
conversion, unified port-termination model, Schur-complement reduction,
single-frequency RLC extraction, mutual-coupling extraction, the Mode 5
custom-termination DSL, and broadband fitting.

User-facing port indices are 1-based; internal computation is 0-based.
The boundary between the two is the build_terminations_* helpers
(input is 1-based) and the GUI/CLI layer.

Measurement model
-----------------
A measurement is a pair of multimeter probes: a RED probe on the "+" side and
a BLACK probe on the "-" side.  A measurement port is therefore a named triple
(name, plus_ports, minus_ports); ports on the same side are tied together
(parallel, unsigned) and there are no fractional weights -- only membership.
An empty minus side means the port is referenced to ground.

Any number of measurement ports may be defined.  `compute_z_matrix` returns the
G x G open-circuit impedance matrix Z(f): the diagonal is the self impedance of
each measurement port and the off-diagonal entries are the mutual impedances
(all other measurement ports open, which is the textbook definition of M).
`compute_z` is the single-measurement-port special case kept for backward
compatibility, and its G == 1 code paths are deliberately written with the
historical floating-point expressions so that existing modes stay bit-exact.
"""

from __future__ import annotations

import array
import bisect
import codecs
import math
import re
import textwrap
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, Union

import numpy as np
import sys as _sys
import types as _types

import pkg_rlc_touchstone as _pkg_rlc_touchstone

# ----------------------------------------------------------------------------
# Reading a file lives in pkg_rlc_touchstone now; re-exported here so that
# `from pkg_rlc_core import parse_touchstone` (and every other name that used
# to be defined in this file) keeps resolving for the GUI, the CLI,
# pkg_rlc_attrib, pkg_rlc_compose and all 45 test modules.  Same precedent as
# the Mode 5 DSL helpers, which are re-exported by pkg_rlc_gui for the same
# reason.
# ----------------------------------------------------------------------------
from pkg_rlc_touchstone import (        # noqa: F401  (re-export, not a use)
    DEFAULT_Z0, FREQ_UNIT_SCALE, MAX_SNIFF_NPORTS, SNIFF_HARD_CAP,
    COMPUTE_BATCH, COMPUTE_CHUNK_BYTES, _freq_batch, PARSE_FLUSH_VALUES,
    PARSE_WARN_CAP, ENCODING_SNIFF_BYTES, DIAGNOSE_MAX_LINES,
    DIAGNOSE_TAIL_LINES, FAULT_FILE, FAULT_UNSUPPORTED, FAULT_ACCESS,
    FAULT_INTERNAL, FAULT_NONE, _VERDICT, _clip, TouchstoneParseError,
    TouchstoneData, _PORT_NAME_RE, _V2_KEYWORD_RE, _SNP_EXT_RE, _BOMS,
    _BINARY_MAGIC, _sniff_encoding, _ext_nports, _decode_options,
    parse_touchstone, _parse_touchstone, _recover_data_line, _to_float,
    _floats_or_none, _check_freq_axis, _check_s_values, _MONO_CHUNK,
    _strictly_increasing, _sniff_range, _sniff_nports, _max_possible_nports,
    _sniff_reach, _nports_fits, _Diagnosis, _LineScan, _scan_lines,
    _value_line, _nports_from_record, _diag_candidate, _diagnose,
    _safe_diagnose, check_touchstone, diagnose_touchstone, _SI_PREFIXES,
    format_si, format_freq,
)

# ============================================================================
# Constants
# ============================================================================

SCHUR_LSTSQ_RCOND = 1e-15

# rcond for the pseudo-inverse of the node admittance matrix in
# compute_z_matrix.  pinv (not inv) is required: a fully floating differential
# structure has a singular Y whose null direction is common mode, and the
# balanced +/- injection is orthogonal to that direction.
PINV_RCOND = 1e-12

# How much of a probe vector may lie in the null space of the node admittance
# before the measurement is declared undefined (no return path for the injected
# current).  pinv is only the right answer for probes that are orthogonal to
# that null space; for any other probe it silently fabricates a finite
# minimum-norm number.  The cutoff is sqrt(PINV_RCOND) because a null component
# `alpha` perturbs Z by ~alpha^2 / (s_min/s_max) relative to the kept part, so
# alpha below sqrt(rcond) is already under the truncation error pinv commits
# anyway -- which keeps a *nearly* floating structure (real ground path, just a
# very weak one) out of the error path.
PROBE_RANGE_TOL = math.sqrt(PINV_RCOND)

# `Y_kk - Y_ko @ X` below this fraction of the magnitude of its own two terms
# is pure cancellation noise: the Schur step has nothing left to say and every
# digit downstream is roundoff amplified to ~1e16 ohms.  ADVISORY ONLY (a
# warning, never a NaN) -- unlike the rank test this is a magnitude heuristic,
# and on the repo's fixtures the healthy minimum is 3.8e-10 against 7e-16 for
# the genuinely degenerate case, so the margin is real but finite.
SCHUR_COLLAPSE_TOL = 1e-12

# Above this, Z_ab and Z_ba disagree enough that the input S-parameters, not
# the maths, are the likely problem.  Shared by the GUI results pane and the
# CLI report so the same file never gets two different verdicts.  A real EM
# solver's S12/S21 mismatch routinely sits at 1e-9..1e-6, so a machine-epsilon
# threshold here would cry wolf on essentially every measured file.
RECIPROCITY_WARN = 1e-3

# Group names reserved for the legacy A / B modes; see Signal.
LEGACY_GROUP_NAMES = ("A", "B")

# SI multiplier suffixes for lumped values.  NOTE: 'M' is Mega (1e6) and 'm' is
# milli (1e-3) -- do not merge these two entries.
SI_SUFFIXES = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}


# ============================================================================
# Port range / short-pair parsers
# ============================================================================

def parse_port_range(spec: str) -> list[int]:
    """
    Parse port range syntax -> 1-based port list (deduped, order-preserved).

    Supported forms:
        "1"             -> [1]
        "1,3,5"         -> [1, 3, 5]
        "35:1:45"       -> [35, 36, ..., 45]   (MATLAB start:step:stop, inclusive)
        "6-14"          -> [6, 7, ..., 14]
        "1,3,35:1:45"   -> mixed
        ""              -> []
    """
    if not spec or not spec.strip():
        return []
    out: list[int] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if ":" in token:
            parts = [p.strip() for p in token.split(":")]
            if len(parts) != 3:
                raise ValueError(f"Range must be start:step:stop, got '{token}'")
            start, step, stop = (int(p) for p in parts)
            if step == 0:
                raise ValueError(f"Step cannot be zero in '{token}'")
            if step > 0:
                out.extend(range(start, stop + 1, step))
            else:
                out.extend(range(start, stop - 1, step))
        elif "-" in token[1:]:  # leading '-' is a sign, not a range
            a_str, b_str = token.split("-", 1)
            a, b = int(a_str.strip()), int(b_str.strip())
            if a <= b:
                out.extend(range(a, b + 1))
            else:
                out.extend(range(a, b - 1, -1))
        else:
            out.append(int(token))
    seen: set[int] = set()
    result: list[int] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def parse_short_pairs(spec: str) -> list[tuple[int, int]]:
    """
    Parse short-pair / short-group syntax (1-based) -> list of binary (i, j) pairs.

    Each comma-separated group joins any number of ports with dashes; all
    ports in a group are tied together (V_i = V_j = ...). The function
    emits a chain of binary pairs because the core API uses ShortPair(i, j)
    plus Union-Find -- chaining (1,2)+(2,3)+(3,4) puts all four into one
    merged group.

        "45-46"             -> [(45, 46)]
        "45-46, 47-48"      -> [(45, 46), (47, 48)]
        "1-2-3-4"           -> [(1, 2), (2, 3), (3, 4)]   (all four shorted)
        "1-2-3, 4-5"        -> [(1, 2), (2, 3), (4, 5)]
    """
    if not spec or not spec.strip():
        return []
    pairs: list[tuple[int, int]] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" not in token:
            raise ValueError(
                f"Short group must use 'a-b' or 'a-b-c-...' syntax, got '{token}'"
            )
        parts = [p.strip() for p in token.split("-")]
        try:
            ports = [int(p) for p in parts]
        except ValueError as e:
            raise ValueError(f"Invalid short group '{token}': {e}") from e
        if len(ports) < 2:
            raise ValueError(f"Short group '{token}' needs at least two ports")
        for a, b in zip(ports, ports[1:]):
            if a == b:
                raise ValueError(
                    f"Short group '{token}' has adjacent duplicate port {a}"
                )
            pairs.append((a, b))
    return pairs


def parse_mport_spec(spec: str) -> tuple[str, list[int], list[int]]:
    """
    Parse one measurement-port spec -> (name, plus_1based, minus_1based).

    A measurement port is a pair of probes: the RED probe touches every port on
    the plus side, the BLACK probe every port on the minus side.  '/' separates
    the two sides and an optional 'name =' prefix names the port.  Both sides go
    through parse_port_range, so ranges keep working.

        "tank = 1,3 / 2,4"  -> ("tank", [1, 3], [2, 4])
        "1 / 2"             -> ("",     [1],    [2])
        "rx = 5:1:9 /"      -> ("rx",   [5, 6, 7, 8, 9], [])   (ground-referenced)
        "3,4"               -> ("",     [3, 4], [])            (ground-referenced)

    An empty minus side means the port is referenced to ground.

    Raises ValueError on: an empty plus side, a port listed on both sides,
    more than one '/', or the reserved names "A" / "B".
    """
    if spec is None:
        raise ValueError("Empty measurement-port spec")
    text = spec.strip()
    if not text:
        raise ValueError("Empty measurement-port spec")

    name = ""
    if "=" in text:
        name_part, text = text.split("=", 1)
        name = name_part.strip()
        if not name:
            raise ValueError(
                f"Measurement port '{spec.strip()}' has an empty name before '='"
            )
        if name.upper() in LEGACY_GROUP_NAMES:
            raise ValueError(
                f"Measurement-port name '{name}' is reserved for the legacy "
                "A/B modes; pick another name."
            )

    parts = text.split("/")
    if len(parts) > 2:
        raise ValueError(
            f"Measurement port '{spec.strip()}' has more than one '/'; the "
            "syntax is '<plus ports> / <minus ports>'"
        )
    plus = parse_port_range(parts[0])
    minus = parse_port_range(parts[1]) if len(parts) == 2 else []
    if not plus:
        raise ValueError(
            f"Measurement port '{spec.strip()}' has an empty '+' side; at "
            "least one port must carry the red probe."
        )
    both = sorted(set(plus) & set(minus))
    if both:
        raise ValueError(
            f"Measurement port '{spec.strip()}': port(s) "
            f"{', '.join(str(p) for p in both)} appear on both the '+' and "
            "'-' side."
        )
    return name, plus, minus


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


# ============================================================================
# S <-> Y conversion
# ============================================================================

def s_to_y(s: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """
    Y = y0 * (I - S) @ inv(I + S) per frequency, where y0 = 1/z0.
    Input s shape: (nfreqs, n, n). Returns same shape.

    Frequencies are processed COMPUTE_BATCH at a time through a single stacked
    np.linalg.solve.  If any matrix in a chunk is singular the whole chunk is
    redone one frequency at a time so the pinv fallback stays per-frequency,
    exactly as it was when the loop was written in Python.
    """
    y0 = 1.0 / z0
    n = s.shape[-1]
    I = np.eye(n)
    Y = np.empty_like(s)
    batch = _freq_batch(n)
    for start in range(0, s.shape[0], batch):
        stop = min(start + batch, s.shape[0])
        Sb = s[start:stop]
        A = I + Sb
        B = I - Sb
        # Y = y0 * B @ inv(A); avoid explicit inverse:
        # Y.T = y0 * inv(A.T) @ B.T  =>  A.T @ (Y.T) = y0 * B.T
        # (.swapaxes, not .T -- .T would reverse the frequency axis too.)
        try:
            Y[start:stop] = y0 * np.linalg.solve(
                A.swapaxes(-1, -2), B.swapaxes(-1, -2)).swapaxes(-1, -2)
        except np.linalg.LinAlgError:
            for i in range(stop - start):
                try:
                    Y[start + i] = y0 * np.linalg.solve(A[i].T, B[i].T).T
                except np.linalg.LinAlgError:
                    Y[start + i] = y0 * (B[i] @ np.linalg.pinv(A[i]))
    return Y


def y_to_s(y: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """Inverse of s_to_y; convenient for synthesizing test fixtures."""
    n = y.shape[-1]
    I = np.eye(n)
    S = np.empty_like(y)
    batch = _freq_batch(n)
    for start in range(0, y.shape[0], batch):
        stop = min(start + batch, y.shape[0])
        Yb = y[start:stop]
        # S = (I - z0*Y) @ inv(I + z0*Y)
        A = I + z0 * Yb
        B = I - z0 * Yb
        try:
            S[start:stop] = np.linalg.solve(
                A.swapaxes(-1, -2), B.swapaxes(-1, -2)).swapaxes(-1, -2)
        except np.linalg.LinAlgError:
            for i in range(stop - start):
                try:
                    S[start + i] = np.linalg.solve(A[i].T, B[i].T).T
                except np.linalg.LinAlgError:
                    S[start + i] = B[i] @ np.linalg.pinv(A[i])
    return S


# ============================================================================
# Lumped-element admittance helpers
# ============================================================================

YFunc = Callable[[np.ndarray], np.ndarray]
"""Function omega (rad/s, real ndarray) -> complex admittance ndarray."""


def y_resistor(R: float) -> YFunc:
    if R <= 0:
        raise ValueError(f"Resistor R must be > 0, got {R}")
    return lambda omega: np.full(np.asarray(omega).shape, 1.0 / R, dtype=complex)


def y_inductor(L: float) -> YFunc:
    if L <= 0:
        raise ValueError(f"Inductor L must be > 0, got {L}")
    return lambda omega: 1.0 / (1j * np.asarray(omega) * L)


def y_capacitor(C: float) -> YFunc:
    if C <= 0:
        raise ValueError(f"Capacitor C must be > 0, got {C}")
    return lambda omega: 1j * np.asarray(omega) * C


def y_series_rlc(R: float = 0.0, L: float = 0.0, C: float = math.inf) -> YFunc:
    """
    Z(jw) = R + jwL + 1/(jwC). Y = 1/Z.
    Use L=0 to skip inductor, C=inf to skip capacitor.
    """
    def f(omega: np.ndarray) -> np.ndarray:
        omega = np.asarray(omega, dtype=float)
        Z = np.full(omega.shape, R, dtype=complex)
        if L != 0:
            Z = Z + 1j * omega * L
        if math.isfinite(C) and C != 0:
            Z = Z + 1.0 / (1j * omega * C)
        return 1.0 / Z
    return f


# ============================================================================
# Unified port-termination model
# ============================================================================

@dataclass(frozen=True)
class Open:
    pass


@dataclass(frozen=True)
class Ground:
    pass


@dataclass(frozen=True)
class Vdd:
    """AC-grounded ideal supply; identical to Ground for AC small-signal."""
    pass


@dataclass(frozen=True)
class Signal:
    """
    A port carrying a measurement probe.

    `group` names the measurement port; it is an arbitrary string.  `sign` says
    which probe touches this port: +1 is the plus / red side, -1 is the minus /
    black side.  Ports sharing a (group, sign) are tied together in parallel;
    there are no fractional weights.

    LEGACY ALIAS: group "B" is the historical name for "the minus side of group
    A".  `resolve_meas_ports` normalises Signal("B", +1) to Signal("A", -1) (and
    Signal("B", -1) to Signal("A", +1)), which is what keeps every pre-existing
    mode, test and saved session working unchanged.  "A" and "B" are therefore
    reserved names -- `parse_mport_spec` rejects them for new measurement ports.
    """
    group: str = "A"
    sign: int = +1     # +1 = plus/red side, -1 = minus/black side


# `params` on the two lumped classes is the R/L/C the element was WRITTEN with,
# exactly as parse_kv_rlc_params returned it ({"R": .., "L": .., "C": ..}).  It
# is metadata: nothing in the reduction reads it, y_func stays the only thing
# that is evaluated, and a set built in code (the golden capture, the
# attribution tests) leaves it None.
#
# It exists because y_func is an opaque closure.  Without it a checker can say
# "three identical elements in parallel" but not "50 Ohm becomes 16.7 Ohm", and
# the number is the whole point -- measured on the 5-port probe network in
# tests/test_conn_nets.py, a merged node turns `R=50` into 16.667 Ohm and
# `L=10f` into 3.333 fH with nothing on screen saying so.
@dataclass
class LumpedToGnd:
    y_func: YFunc
    params: dict | None = None


@dataclass
class ShortPair:
    port_i: int   # 0-based
    port_j: int   # 0-based


@dataclass
class LumpedBetween:
    port_i: int
    port_j: int
    y_func: YFunc
    params: dict | None = None


PortTermination = Union[Open, Ground, Vdd, Signal, LumpedToGnd]
Coupling = Union[ShortPair, LumpedBetween]


@dataclass
class TerminationSet:
    """Per-port terminations (default Open) + inter-port couplings."""
    per_port: dict[int, PortTermination] = field(default_factory=dict)
    couplings: list[Coupling] = field(default_factory=list)

    def termination_of(self, port: int) -> PortTermination:
        return self.per_port.get(port, Open())


# ============================================================================
# Measurement ports (probe pairs)
# ============================================================================

@dataclass
class MeasPort:
    """One pair of probes. Port indices are 0-based (core-side)."""
    name: str
    plus: list[int]
    minus: list[int]


def _normalize_signal(sig: Signal) -> tuple[str, int]:
    """
    Signal -> (group, sign) with the legacy "B" alias applied.

    Signal("B", +1) is the historical spelling of "minus side of group A", so it
    resolves to ("A", -1).  Everything else passes through with its sign
    coerced to +-1.
    """
    sign = -1 if sig.sign < 0 else +1
    if sig.group == "B":
        return "A", -sign
    return sig.group, sign


def resolve_meas_ports(terminations: TerminationSet, n: int) -> list[MeasPort]:
    """
    Collect the Signal-terminated ports of `terminations` into measurement ports.

    Ordering is by first appearance of the group name while scanning ports
    0..n-1 ascending, so the result is deterministic and independent of dict
    insertion order.  The legacy "B" group is folded into the minus side of "A"
    (see Signal), which is what makes the old A<->B modes a plain
    single-measurement-port case.

    Raises ValueError if a group ends up with an empty plus side (e.g. only
    legacy "B" ports were declared, or every port of a group carries the black
    probe).
    """
    order: list[str] = []
    plus: dict[str, list[int]] = {}
    minus: dict[str, list[int]] = {}
    for i in range(n):
        t = terminations.termination_of(i)
        if not isinstance(t, Signal):
            continue
        group, sign = _normalize_signal(t)
        if group not in plus:
            order.append(group)
            plus[group] = []
            minus[group] = []
        (plus if sign > 0 else minus)[group].append(i)

    out: list[MeasPort] = []
    for group in order:
        if not plus[group]:
            if group == "A":
                raise ValueError(
                    "No Signal-group-A ports defined; nothing to measure."
                )
            raise ValueError(
                f"Measurement port '{group}' has an empty '+' side; at least "
                "one port must carry the red probe."
            )
        out.append(MeasPort(name=group, plus=plus[group], minus=minus[group]))
    return out


# ----- Convenience builders for the four named modes (1-based input) -------

def build_terminations_mode1(signal_ports: Sequence[int],
                             gnd_ports: Sequence[int]) -> TerminationSet:
    pp: dict[int, PortTermination] = {}
    for p in signal_ports:
        pp[p - 1] = Signal("A")
    for p in gnd_ports:
        pp[p - 1] = Ground()
    return TerminationSet(per_port=pp)


def build_terminations_mode2(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int]) -> TerminationSet:
    pp: dict[int, PortTermination] = {}
    for p in port_a:
        pp[p - 1] = Signal("A")
    for p in port_b:
        pp[p - 1] = Signal("B")
    for p in gnd_ports:
        pp[p - 1] = Ground()
    return TerminationSet(per_port=pp)


def build_terminations_mode3(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int],
                             short_pairs: Sequence[tuple[int, int]]) -> TerminationSet:
    ts = build_terminations_mode2(port_a, port_b, gnd_ports)
    ts.couplings.extend(ShortPair(a - 1, b - 1) for a, b in short_pairs)
    return ts


def build_terminations_mode4(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int],
                             vdd_ports: Sequence[int]) -> TerminationSet:
    ts = build_terminations_mode2(port_a, port_b, gnd_ports)
    for p in vdd_ports:
        ts.per_port[p - 1] = Vdd()
    return ts


def build_terminations_coupling(
    mports: Sequence[tuple[str, Sequence[int], Sequence[int]]],
    gnd_ports: Sequence[int] = (),
    short_pairs: Sequence[tuple[int, int]] = (),
    nports: int | None = None,
) -> TerminationSet:
    """
    Build a TerminationSet for an arbitrary number of measurement ports.

    `mports` is a sequence of (name, plus_1based, minus_1based) triples -- the
    output shape of parse_mport_spec.  Unnamed entries get the default names
    P1, P2, ...  All port indices are 1-based on the way in and 0-based on the
    way out: this function is the GUI/CLI boundary.

    Pass `nports` (the file's port count) to have out-of-range port numbers
    rejected here, with a message that names the file size; compute_z_matrix
    rejects them again as a backstop.

    Unlike build_terminations_mode1/2 -- where ground simply wins over a
    probe -- listing a probe port in `gnd_ports` raises.  Under the probe model
    the ports on one side are tied together, so grounding one of them grounds
    the whole side; silently untying it instead would report a plausible
    non-zero impedance for a node the tool believes is at 0 V.
    """
    pp: dict[int, PortTermination] = {}
    owner: dict[int, str] = {}
    used_names: set[str] = set()
    auto = 0

    for entry in mports:
        name, plus_1b, minus_1b = entry
        name = (name or "").strip()
        if not name:
            while True:
                auto += 1
                name = f"P{auto}"
                if name not in used_names:
                    break
        if name.upper() in LEGACY_GROUP_NAMES:
            raise ValueError(
                f"Measurement-port name '{name}' is reserved for the legacy "
                "A/B modes; pick another name."
            )
        if name in used_names:
            raise ValueError(f"Duplicate measurement-port name '{name}'")
        used_names.add(name)

        plus = list(plus_1b)
        minus = list(minus_1b)
        if not plus:
            raise ValueError(
                f"Measurement port '{name}' has an empty '+' side; at least "
                "one port must carry the red probe."
            )
        both = sorted(set(plus) & set(minus))
        if both:
            raise ValueError(
                f"Measurement port '{name}': port(s) "
                f"{', '.join(str(p) for p in both)} appear on both the '+' "
                "and '-' side."
            )
        for p, sign in [(p, +1) for p in plus] + [(p, -1) for p in minus]:
            if p < 1:
                raise ValueError(f"Port numbers are 1-based, got {p}")
            if p in owner and owner[p] != name:
                raise ValueError(
                    f"Port {p} is claimed by both measurement port "
                    f"'{owner[p]}' and '{name}'"
                )
            owner[p] = name
            pp[p - 1] = Signal(name, sign)

    if not pp:
        raise ValueError("No measurement ports defined; nothing to measure.")

    clash = sorted(p for p in gnd_ports if p in owner)
    if clash:
        raise ValueError(
            f"Port(s) {', '.join(str(p) for p in clash)} are listed both as a "
            f"probe (measurement port "
            f"'{owner[clash[0]]}') and as ground. A probe side is tied "
            "together, so grounding one of its ports grounds the whole side "
            "-- drop it from one list or the other."
        )
    for p in gnd_ports:
        if p < 1:
            raise ValueError(f"Port numbers are 1-based, got {p}")
        pp[p - 1] = Ground()

    ts = TerminationSet(per_port=pp)
    ts.couplings.extend(ShortPair(a - 1, b - 1) for a, b in short_pairs)
    if nports is not None:
        _validate_port_indices(ts, int(nports))
    return ts


# ============================================================================
# Named merged nodes ("nets")
# ============================================================================
#
# A short row creates a NODE.  Until now that node had no name, so the only way
# to hang an element off it was to name one of its member ports -- which works
# (measured: after `1 short_to 2,3`, `1 lumped_between 4 L=10f` gives 10.000 fH)
# but reads as arbitrary, and the natural spelling `1,2,3 lumped_between 4
# L=10f` silently computes THREE elements in parallel (measured: 3.333 fH,
# ratio exactly 3.000).  Naming the node is the affordance that makes the right
# gesture the obvious one; parallel_stamp_messages is the refusal that catches
# the wrong one.
#
# A net is pure SUGAR.  It resolves to ONE representative member port, so
# everything downstream -- the DSL, compute_z_matrix, the golden reference --
# sees exactly the spec that could always have been typed by hand.

# The keyword that names a node: `1,2,3 short as coil_tap`.
NET_KEYWORD = "as"

# Names a net may not take.  "A"/"B" are already reserved by Signal (group "B"
# is the legacy alias for the minus side of "A").  The rest are the DSL's own
# vocabulary: a net called `ground` would make the line `ground ground` read
# two ways, and the leading field is where a net name and a keyword meet.
NET_RESERVED_NAMES = (
    "A", "B",
    "GND", "GROUND", "VDD", "OPEN", "SIGNAL",
    "SHORT", "SHORT_TO", "LUMPED_TO_GND", "LUMPED_BETWEEN",
    NET_KEYWORD.upper(),
)

# Characters a net name may not contain, each because of a parser that would
# then read it as something else:
#   ':' and '-'  are parse_port_range's range separators (and ':' is the one
#                a future file prefix must not use -- parse_port_range('PKG:12')
#                already raises "Range must be start:step:stop");
#   ','          separates ports in the same field;
#   '#'          starts a comment;
#   whitespace   is the DSL's token separator, so 'my net' is two tokens and the
#                second one lands where a keyword belongs.
NET_BAD_CHARS = ":,-#"


def validate_net_name(name: str) -> None:
    """
    Raise ValueError unless `name` can name a merged node.

    The rules are not taste.  A name that parse_port_range accepts is refused
    because the port field is the one slot in this DSL where a number and a name
    share a token -- the same number-vs-name collision reduce_snp.py documents
    for its port groups, and guessing there is a silent wrong answer.  The
    reserved list and the character set close the other ways a name can be read
    as something else; see NET_RESERVED_NAMES / NET_BAD_CHARS.

    Names are matched case-insensitively and stored as typed.
    """
    n = (name or "").strip()
    if not n:
        raise ValueError("a node name cannot be empty")
    try:
        ports = parse_port_range(n)
    except ValueError:
        ports = []
    if ports:
        raise ValueError(
            f"node name '{n}' is a port number or range, so nothing could tell "
            f"it from the ports it names. Pick a name that is not a number."
        )
    bad = sorted({ch for ch in n if ch.isspace() or ch in NET_BAD_CHARS})
    if bad:
        shown = ", ".join("space" if ch.isspace() else f"'{ch}'" for ch in bad)
        raise ValueError(
            f"node name '{n}' contains {shown}. A node name may not contain "
            f"whitespace or any of {NET_BAD_CHARS} -- those are how this "
            f"syntax separates ports, ranges and comments."
        )
    if n.upper() in NET_RESERVED_NAMES:
        raise ValueError(
            f"node name '{n}' is one of this syntax's own keywords "
            f"({', '.join(NET_RESERVED_NAMES)}). Pick another name."
        )


@dataclass(frozen=True)
class NetDef:
    """
    One named merged node.

    `port` is a 1-BASED representative member -- any member port identifies the
    whole node once the shorts are merged, which is why a net can be pure sugar.
    It is 0 when the defining row has no port number of its own yet.
    """
    name: str          # as typed
    port: int = 0      # 1-based representative member, 0 = unresolved
    line: int = 0      # DSL line that defined it, 1-based (0 = unknown)


def _split_net_tail(parts: Sequence[str]) -> tuple[list[str], str, bool]:
    """
    Split a trailing `as <name>` off a directive's tokens.

    -> (tokens_before_as, name, well_formed).  Pure and TOTAL: a malformed tail
    comes back as well_formed=False so dsl_text_to_rows can park the line in
    `extra` while parse_custom_termination_text raises about it.
    """
    toks = list(parts)
    lowered = [t.lower() for t in toks]
    if NET_KEYWORD not in lowered:
        return toks, "", True
    i = lowered.index(NET_KEYWORD)
    tail = toks[i + 1:]
    if len(tail) != 1:
        return toks[:i], "", False
    return toks[:i], tail[0], True


def _net_key(spec: str) -> str:
    return (spec or "").strip().lower()


def _looks_like_a_name(spec: str) -> bool:
    """Could this token only ever have been meant as a name, not a port spec?"""
    return any(ch.isalpha() or ch == "_" for ch in spec or "")


def _defined_nets_phrase(nets: dict) -> str:
    if not nets:
        return ("No node names are defined -- a short row names its node with "
                f"'{NET_KEYWORD} <name>'.")
    names = ", ".join(sorted(d.name for d in nets.values()))
    return f"Defined node names: {names}."


def _first_port_of(spec: str, nets: dict, resolved: dict) -> int | None:
    """
    First literal port of `spec`, following net references.  None if not yet.

    Deliberately reads ONLY the row's own port field, never its partner: a name
    stands for the node its row builds, and falling back to the partner side
    lets a self-referential pair (`n1 short_to 3 as n2` / `n2 short_to 4 as n1`)
    quietly resolve BOTH names to port 3 and then fail with a message about
    chain length.  Left unresolved, the main pass names the actual problem.
    """
    s = (spec or "").strip()
    if not s:
        return None
    key = _net_key(s)
    if key in nets:
        return resolved.get(key)      # None while that net is itself unresolved
    try:
        ports = parse_port_range(s)
    except ValueError:
        return None
    return ports[0] if ports else None


def _collect_nets(text: str) -> dict[str, NetDef]:
    """
    Pre-pass: every `as <name>` in `text` -> {lowercase name: NetDef}.

    A SEPARATE PASS, so a net may be referenced above the row that defines it.
    That is not a nicety: rows_to_dsl_text emits every measurement port BEFORE
    every connection, so a probe on a named node would otherwise refuse to
    resolve purely because of where the table puts it.

    Resolution is a fixed point rather than a recursion so that a net defined in
    terms of another net works and a cycle simply stops (the names left
    unresolved are reported by the main pass, which has the line numbers).
    Never raises for a malformed line -- the main pass raises, with the same
    message it would have given anyway.
    """
    pending: dict[str, str] = {}
    defs: dict[str, NetDef] = {}
    for ln_no, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        parts = body.split()
        if len(parts) < 2 or parts[1].lower() not in ("short", "short_to"):
            continue
        _rest, name, ok = _split_net_tail(parts[2:])
        if not ok or not name:
            continue
        try:
            validate_net_name(name)
        except ValueError as e:
            raise ValueError(f"Line {ln_no}: {e}") from e
        key = _net_key(name)
        if key in defs:
            raise ValueError(
                f"Line {ln_no}: node name '{name}' is already used on line "
                f"{defs[key].line}. A name belongs to one node; use it to refer "
                f"to that node instead of defining it twice."
            )
        defs[key] = NetDef(name=name, line=ln_no)
        pending[key] = parts[0]

    resolved: dict[str, int] = {}
    for _ in range(len(pending) + 1):
        progress = False
        for key, ports_text in pending.items():
            if key in resolved:
                continue
            p = _first_port_of(ports_text, defs, resolved)
            if p is not None:
                resolved[key] = p
                progress = True
        if not progress:
            break
    return {k: NetDef(name=d.name, port=resolved.get(k, 0), line=d.line)
            for k, d in defs.items()}


def _resolve_port_field(spec: str, nets: dict, ln_no: int, what: str) -> list[int]:
    """
    One port field -> 1-based ports, resolving a node name to ONE member.

    A net stands for a node, and the node's members are already tied together,
    so ONE representative is the whole of it -- expanding to every member is
    exactly the N-parallel-elements bug this feature exists to remove.
    """
    s = (spec or "").strip()
    key = _net_key(s)
    if key in nets:
        d = nets[key]
        if not d.port:
            raise ValueError(
                f"Line {ln_no}: node '{d.name}' (named on line {d.line}) has no "
                f"port number of its own to stand for; give that row at least "
                f"one port."
            )
        return [d.port]
    try:
        return parse_port_range(s)
    except ValueError as e:
        if _looks_like_a_name(s):
            raise ValueError(
                f"Line {ln_no}: {what} '{s}' is not a port number or range, and "
                f"no node is named that. {_defined_nets_phrase(nets)}"
            ) from e
        raise ValueError(
            f"Line {ln_no}: {what} must be a port number, a range "
            f"(e.g. '3', '1,2', '5:1:12', '6-14') or a node name, got "
            f"'{s}': {e}"
        ) from e


def _check_one_name_per_node(ts: TerminationSet, nets: dict) -> None:
    """
    Two names on ONE merged node is a spec error, not a synonym.

    A name belongs to the node, so `1,2 short as a` next to `2,3 short as b`
    leaves ports 1-3 one node answering to two names -- and every message,
    every echo and every Ports & Roles row then has to pick one arbitrarily.
    Refusing is the only reading that cannot be silently wrong.
    """
    if len(nets) < 2:
        return
    find, _members = _merge_view(ts)
    by_root: dict[int, NetDef] = {}
    for d in nets.values():
        if not d.port:
            continue
        root = find(d.port - 1)
        first = by_root.get(root)
        if first is not None:
            a, b = sorted((first, d), key=lambda x: x.line)
            raise ValueError(
                f"Line {b.line}: node name '{b.name}' names the same merged "
                f"node as '{a.name}' (line {a.line}) -- a short ties them "
                f"together. One node, one name: put all of those ports on ONE "
                f"short row, or drop one of the two names."
            )
        by_root[root] = d


# ============================================================================
# Mode 5 (Custom) termination DSL
# ============================================================================

def parse_custom_termination_text(text: str) -> TerminationSet:
    """
    Parse a text spec for Mode 5 (Custom).  One directive per line, e.g.
        1 signal A
        2 signal B
        3 ground
        4 vdd
        5 lumped_to_gnd R=50
        6 short_to 7
        8 lumped_between 9 R=1 L=1n
        10 open
        6:1:14 ground             <- the port field takes a range
        23,24,25 short            <- tie a whole group into one node
        23,24,25 short as tap     <- ...and name that node
        tap lumped_between 30 L=10f   <- any port field may name a node

    `signal <groupname> [+|-]` declares a probe: <groupname> names the
    measurement port and the optional sign picks the plus (red, default) or
    minus (black) side.  Group names are arbitrary strings; "A" and "B" keep
    their legacy meaning, where "signal B" == "signal A -".

    PORT RANGES: the leading port field accepts the full `parse_port_range`
    syntax ('3', '1,2', '5:1:12', '6-14'), so one line can terminate a whole
    group of ports -- a package's ground balls, an inductor's shield taps.  The
    directive is applied to each listed port independently, and a single port
    number parses to a one-element list, so every pre-existing spec is
    unaffected.  This is what lets the GUI's connection table hold "ports 5-12,
    ground" as ONE row instead of eight identical ones.

    `short_to` also takes a range on its right-hand side: every port on both
    sides is tied into one node, emitted as chained binary pairs exactly the way
    `parse_short_pairs` spells '1-2-3-4' (Union-Find inside compute_z merges
    them).  `short` with NO partner is the same thing written as one field:
    '23,24,25 short' ties everything listed into one node, which is what a
    single-cell connection row serialises to.  `lumped_between` deliberately
    does NOT take a range on the right -- an N-to-M lumped element is ambiguous
    (star? mesh?), so its partner must be a single port.  A range on its LEFT is
    unambiguous (one element from each listed port to the one partner) and is
    allowed -- but see parallel_stamp_messages for the case where those ports
    are already ONE node, which is N elements in parallel and almost never what
    was meant.

    NODE NAMES: a short line may name the node it creates with
    '<ports> short as <name>' (or '<ports> short_to <ports> as <name>'), and any
    port field may then use that name.  A name resolves to ONE representative
    member port -- the node is merged, so one member IS the node -- which makes
    the whole feature sugar over a spec that could always have been typed by
    hand.  See validate_net_name for what a name may be; an unknown name is
    refused rather than treated as a new empty node, which would hang the
    element off a dangling node and change the answer with nothing on screen.

    Blank lines and lines starting with '#' are ignored.
    """
    ts = TerminationSet()
    nets = _collect_nets(text)
    for ln_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        ports = _resolve_port_field(parts[0], nets, ln_no, "first token")
        if not ports:
            raise ValueError(
                f"Line {ln_no}: port specification '{parts[0]}' selects no ports"
            )
        for port in ports:
            if port < 1:
                raise ValueError(f"Line {ln_no}: port must be >= 1, got {port}")
        if len(parts) < 2:
            raise ValueError(f"Line {ln_no}: missing termination kind")
        kind = parts[1].lower()
        rest = parts[2:]
        if kind in ("short", "short_to"):
            rest, net_name, ok = _split_net_tail(rest)
            if not ok:
                raise ValueError(
                    f"Line {ln_no}: '{NET_KEYWORD}' takes exactly one node "
                    f"name, and a node name cannot contain spaces."
                )
            if net_name:
                try:
                    validate_net_name(net_name)
                except ValueError as e:
                    raise ValueError(f"Line {ln_no}: {e}") from e
        elif kind != "signal" and any(t.lower() == NET_KEYWORD for t in rest):
            # `1,2,3 lumped_to_gnd R=50 as foo` would otherwise drop the name in
            # silence (ground/vdd/open ignore their tail, parse_kv_rlc_params
            # drops any token without an '='), and the user would go on
            # referring to a node that was never named.  `signal` is exempt: its
            # first token is a free-form group name, so 'as' there is a name.
            raise ValueError(
                f"Line {ln_no}: only a short row can name a node. Move the "
                f"'{NET_KEYWORD} ...' onto the short that creates it."
            )
        if kind in ("open",):
            for port in ports:
                ts.per_port[port - 1] = Open()
        elif kind in ("ground", "gnd"):
            for port in ports:
                ts.per_port[port - 1] = Ground()
        elif kind == "vdd":
            for port in ports:
                ts.per_port[port - 1] = Vdd()
        elif kind == "signal":
            grp = rest[0] if rest else "A"
            if grp.upper() in LEGACY_GROUP_NAMES:
                grp = grp.upper()   # legacy A / B are case-insensitive
            sign = +1
            if len(rest) >= 2:
                if rest[1] == "+":
                    sign = +1
                elif rest[1] == "-":
                    sign = -1
                else:
                    raise ValueError(
                        f"Line {ln_no}: signal sign must be '+' or '-', "
                        f"got '{rest[1]}'"
                    )
            if len(rest) > 2:
                raise ValueError(
                    f"Line {ln_no}: signal takes at most a group name and a sign"
                )
            for port in ports:
                ts.per_port[port - 1] = Signal(grp, sign)
        elif kind in ("short", "short_to"):
            others: list[int] = []
            if kind == "short_to":
                if not rest:
                    raise ValueError(
                        f"Line {ln_no}: short_to needs a partner port")
                others = _resolve_port_field(rest[0], nets, ln_no,
                                             "short_to partner")
                if not others:
                    raise ValueError(
                        f"Line {ln_no}: short_to partner '{rest[0]}' selects "
                        f"no ports"
                    )
            elif rest:
                raise ValueError(
                    f"Line {ln_no}: 'short' takes the whole tied group in its "
                    f"port field and nothing else (got '{' '.join(rest)}'). "
                    f"Write '{parts[0]} short_to {rest[0]}' to keep two fields."
                )
            # Chain the whole node: (p0,p1), (p1,p2), ...  One port on each side
            # reduces to the historical single ShortPair(port-1, other-1), which
            # is what keeps every pre-existing `short_to` spec bit-identical.
            chain = list(ports) + [p for p in others if p not in ports]
            if len(chain) < 2:
                raise ValueError(
                    f"Line {ln_no}: a short needs at least two ports to tie "
                    f"together, '{parts[0]}' names one."
                )
            for a, b in zip(chain, chain[1:]):
                ts.couplings.append(ShortPair(a - 1, b - 1))
        elif kind == "lumped_to_gnd":
            params = parse_kv_rlc_params(rest)
            y = y_series_rlc(**params)   # shared: a pure function of frequency
            for port in ports:
                ts.per_port[port - 1] = LumpedToGnd(y, params)
        elif kind == "lumped_between":
            if not rest:
                raise ValueError(f"Line {ln_no}: lumped_between needs a partner port")
            others = _resolve_port_field(rest[0], nets, ln_no,
                                         "lumped_between partner")
            if len(others) != 1:
                raise ValueError(
                    f"Line {ln_no}: lumped_between takes exactly ONE partner "
                    f"port, but '{rest[0]}' selects {len(others)}. An N-to-M "
                    "lumped element is ambiguous -- write one line per element."
                )
            other = others[0]
            params = parse_kv_rlc_params(rest[1:])
            y = y_series_rlc(**params)   # shared: a pure function of frequency
            for port in ports:
                ts.couplings.append(LumpedBetween(port - 1, other - 1, y, params))
        else:
            raise ValueError(f"Line {ln_no}: unknown termination kind '{kind}'")
    _check_one_name_per_node(ts, nets)
    return ts


# ============================================================================
# Connection-table row model (the GUI's Mode 5 / Mode 6 editor)
# ============================================================================
#
# The editor is two tables: measurement ports (what am I measuring) and
# connections (what else is attached).  A row of either is a *statement over a
# set of ports*, not a single directive -- the port field takes a range, so a
# package's ground balls are one row rather than one row per ball.
#
# Rows are the GUI's storage.  They reach a TerminationSet by being serialised
# to DSL text and handed to parse_custom_termination_text, NOT by building a
# TerminationSet directly.  That is deliberate: it keeps one parser, one set of
# error messages, and one thing for the tests to pin, and it makes the "edit as
# text" escape hatch show exactly what is computed rather than an approximation
# of it.  See docs/design_connection_table.md.

# Connection-row kinds.  Probes are NOT here -- they live in the measurement
# port table, which is what keeps this table's "To" column single-domain (a
# port number or GND, never a measurement-port name).
CONN_KINDS = ("ground", "vdd", "open", "short", "rlc_gnd", "rlc_between")

# Kinds whose "To" field names one or more partner ports.  'short' is in the
# list for the LEGACY two-field spelling only: a short row created today puts
# the whole tied group in `ports` and leaves `to` empty (see short_group_spec),
# because a group of tied ports has no natural "from" and "to".  Rows saved by
# an older build still carry the split and must keep working.
CONN_KINDS_WITH_PARTNER = ("short", "rlc_between")

# Kinds that carry R / L / C values.
CONN_KINDS_WITH_RLC = ("rlc_gnd", "rlc_between")

# Kinds that can name the node they create.  Only a short creates one.
CONN_KINDS_WITH_NET = ("short",)


@dataclass
class MeasPortRow:
    """One row of the measurement-port table.  Port specs are 1-based text."""
    name: str = ""
    plus: str = ""
    minus: str = ""

    def is_blank(self) -> bool:
        return not (self.name.strip() or self.plus.strip() or self.minus.strip())


@dataclass
class ConnectionRow:
    """
    One row of the connection table.  All fields are text exactly as typed, so
    '5:12' round-trips as a range instead of expanding into eight rows.

    `to` is the partner port spec for 'rlc_between', and for the LEGACY
    two-field spelling of 'short'; it is ignored otherwise ('ground'/'rlc_gnd'
    are implicitly to GND).  Blank R/L/C mean OMITTED, which is not the same as
    zero: an omitted C is C=inf (no capacitor in the series branch), while C=0
    would be an open circuit.

    `net` is the name a 'short' row gives the node it creates, and is what lets
    a later row say `coil_tap` instead of picking one of its member ports.  It
    is ignored on every other kind (parse_custom_termination_text refuses `as`
    there rather than dropping it in silence).  A row loaded from a session
    saved before nets existed simply has net="" -- the field defaults, so the
    session format needs no migration.

    `enabled` is the debug switch: a disabled row keeps every cell it has and
    contributes NOTHING to the spec -- `rows_to_dsl_text` does not emit it, so
    it is exactly as if the row were deleted.  It exists because the two ways
    of asking "what is this connection worth?" without it are both destructive:
    deleting the row loses its R/L/C (and the ports, which on a package ground
    row is a range someone worked out), and switching the Kind to `open` is a
    DIFFERENT SPEC rather than an absent one -- `open` is a declaration, so it
    survives into `port_roles`, and on a row that was `rlc_gnd` it silently
    discards the element as well.  Defaults True, so a row from any older
    session is enabled and nothing about a saved spec moves.
    """
    kind: str = "ground"
    ports: str = ""
    to: str = ""
    R: str = ""
    L: str = ""
    C: str = ""
    net: str = ""
    enabled: bool = True

    def is_blank(self) -> bool:
        # `enabled` is deliberately NOT part of this: a blank row is one with
        # nothing typed in it, and switching an empty row off must not make it
        # count as a row.  It is also what keeps a disabled row out of the
        # tables' "blanks are dropped" path with its values intact.
        return not (self.ports.strip() or self.to.strip() or self.net.strip()
                    or self.R.strip() or self.L.strip() or self.C.strip())


def short_group_spec(row: ConnectionRow) -> str:
    """
    A 'short' row's tied group as ONE port field.

    The single-field shape is the point of R1-1: a group of tied ports has no
    natural "from" and "to", and being forced to split '5,6,7,8' across two
    cells is the complaint this round exists to fix.  Rows saved by an older
    build carry the split, so the two fields are joined here rather than
    migrated in place -- nothing has to rewrite a stored row to render it.

    NO SPACES, for collapse_ports' reason: the DSL is whitespace-tokenised and
    the port field is parts[0].
    """
    ports = (row.ports or "").strip()
    to = (row.to or "").strip()
    if not to:
        return ports
    return f"{ports},{to}" if ports else to


def _rlc_tokens(row: ConnectionRow) -> list[str]:
    """
    Non-blank R/L/C fields -> ['R=50', 'L=1n'] in canonical R, L, C order.

    A value containing WHITESPACE is rejected rather than emitted.  The DSL is
    whitespace-tokenised and parse_kv_rlc_params drops any token without an
    '=', so 'R=5 m' silently computes R = 5 ohm -- a factor of 1000 -- and
    'C=1 uF' silently computes C = 1 farad.  The cell UI invites exactly that
    (the unit is in the column header, so 'uF' looks like it belongs in the
    cell), and the validation strip re-parses the raw cell as ONE token, so it
    would cheerfully echo '5 mOhm' next to a computed 5 ohm.  There is no way
    to quote a value in the DSL, so the only answer that cannot be silently
    wrong is to refuse.
    """
    out = []
    for key in ("R", "L", "C"):
        val = getattr(row, key).strip()
        if not val:
            continue
        if any(ch.isspace() for ch in val):
            raise ValueError(
                f"{key} value '{val}' contains a space. Write it as one token "
                f"-- 5m, 0.5n, 1u -- with no unit ('{key}={val}' would be read "
                f"as '{key}={val.split()[0]}' and the rest thrown away). The "
                "unit belongs in the column header, not in the cell."
            )
        out.append(f"{key}={val}")
    return out


def rows_to_dsl_text(mport_rows: Sequence[MeasPortRow] = (),
                     conn_rows: Sequence[ConnectionRow] = (),
                     extra_lines: str = "") -> str:
    """
    Serialise the two tables to Mode 5 DSL text.

    ORDER IS LOAD-BEARING.  The DSL is last-assignment-wins, and measurement
    ports are emitted FIRST so that a later 'ground' row wins over a probe on
    the same port -- which is exactly the "ground wins" precedence that
    build_terminations_mode1/2/3 have always had.  Emitting them the other way
    round would make a table seeded from a named mode answer a different
    question.  tests/test_core.py::TestTerminationPrecedence pins this.

    Blank rows are skipped, and so are DISABLED connection rows -- a row with
    `enabled=False` contributes nothing at all, which is what makes the switch
    mean "as if this row were deleted" rather than "some other spec".  It is
    dropped HERE, in the one place rows become a spec, so every caller (the
    solve, the validation strip, the text hatch, the port roles, the run
    report) sees the same thing without any of them knowing about the flag.

    `extra_lines` is appended verbatim and is how comments and hand-written
    lines survive a round trip through the table.
    """
    lines: list[str] = []

    auto = 0
    used: set[str] = set()
    for row in mport_rows:
        if row.is_blank():
            continue
        name = row.name.strip()
        if not name:
            while True:
                auto += 1
                name = f"P{auto}"
                if name not in used:
                    break
        used.add(name)
        if row.plus.strip():
            lines.append(f"{row.plus.strip()} signal {name} +")
        if row.minus.strip():
            lines.append(f"{row.minus.strip()} signal {name} -")

    for row in conn_rows:
        if row.is_blank() or not getattr(row, "enabled", True):
            continue
        ports = row.ports.strip()
        if not ports:
            continue
        kind = row.kind
        if kind == "ground":
            lines.append(f"{ports} ground")
        elif kind == "vdd":
            lines.append(f"{ports} vdd")
        elif kind == "open":
            lines.append(f"{ports} open")
        elif kind == "short":
            # One field or two: a row written today puts the whole tied group in
            # `ports` and emits the single-field `short`; a row loaded from an
            # older session still carries the split and emits the `short_to` it
            # always did, so its DSL text -- and therefore its answer -- is
            # byte-identical to what that build produced.
            to = row.to.strip()
            head = f"{ports} short_to {to}" if to else f"{ports} short"
            net = row.net.strip()
            lines.append(f"{head} {NET_KEYWORD} {net}" if net else head)
        elif kind == "rlc_gnd":
            lines.append(" ".join([f"{ports} lumped_to_gnd", *_rlc_tokens(row)]))
        elif kind == "rlc_between":
            lines.append(" ".join([f"{ports} lumped_between {row.to.strip()}",
                                   *_rlc_tokens(row)]))
        else:
            raise ValueError(
                f"Unknown connection-row kind '{kind}' "
                f"(expected one of {', '.join(CONN_KINDS)})"
            )

    text = "\n".join(lines)
    extra = extra_lines.strip("\n")
    if extra:
        text = f"{text}\n{extra}" if text else extra
    return text + "\n" if text else ""


def dsl_text_to_rows(text: str) -> tuple[list[MeasPortRow], list[ConnectionRow], str]:
    """
    Parse DSL text back into the two tables -> (mport_rows, conn_rows, extra).

    Round-trip contract: this is NOT byte-exact, it is idempotent after one
    pass.  `rows_to_dsl_text(*dsl_text_to_rows(t))` re-parses to the same
    TerminationSet as `t`, and running the pair again changes nothing further.
    Comment lines land in `extra` so hand-written notes survive.

    Legacy 'signal B' is normalised here the same way resolve_meas_ports does
    it -- group "B" is the minus side of group "A" -- so an old spec imports as
    ONE measurement port with a plus and a minus side rather than two.

    Lines the table cannot represent are passed through in `extra` rather than
    dropped; nothing the user typed is ever silently lost.
    """
    order: list[str] = []
    plus: dict[str, list[str]] = {}
    minus: dict[str, list[str]] = {}
    conn: list[ConnectionRow] = []
    extra: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if line:
                extra.append(raw.rstrip())
            continue
        body = line.split("#", 1)[0].strip()
        parts = body.split()
        if len(parts) < 2:
            extra.append(raw.rstrip())
            continue
        ports, kind, rest = parts[0], parts[1].lower(), parts[2:]

        if kind == "signal":
            grp = rest[0] if rest else "A"
            if grp.upper() in LEGACY_GROUP_NAMES:
                grp = grp.upper()
            sign = +1
            if len(rest) >= 2 and rest[1] == "-":
                sign = -1
            elif len(rest) >= 2 and rest[1] != "+":
                extra.append(raw.rstrip())   # malformed; let the parser complain
                continue
            grp, sign = _normalize_signal(Signal(grp, sign))
            if grp not in plus:
                order.append(grp)
                plus[grp], minus[grp] = [], []
            (plus if sign > 0 else minus)[grp].append(ports)
        elif kind in ("ground", "gnd"):
            conn.append(ConnectionRow(kind="ground", ports=ports))
        elif kind == "vdd":
            conn.append(ConnectionRow(kind="vdd", ports=ports))
        elif kind == "open":
            conn.append(ConnectionRow(kind="open", ports=ports))
        elif kind in ("short", "short_to"):
            body_toks, net, ok = _split_net_tail(rest)
            if not ok or (kind == "short_to" and not body_toks) \
                    or (kind == "short" and body_toks):
                extra.append(raw.rstrip())   # malformed; let the parser complain
                continue
            conn.append(ConnectionRow(kind="short", ports=ports,
                                      to=body_toks[0] if body_toks else "",
                                      net=net))
        elif kind == "lumped_to_gnd":
            conn.append(_conn_with_rlc("rlc_gnd", ports, "", rest))
        elif kind == "lumped_between" and rest:
            conn.append(_conn_with_rlc("rlc_between", ports, rest[0], rest[1:]))
        else:
            extra.append(raw.rstrip())

    mports = [MeasPortRow(name=g,
                          plus=",".join(plus[g]),
                          minus=",".join(minus[g]))
              for g in order]
    return mports, conn, "\n".join(extra)


def _conn_with_rlc(kind: str, ports: str, to: str,
                   tokens: Sequence[str]) -> ConnectionRow:
    """Build an R/L/C-bearing connection row from 'R=50 L=1n' tokens."""
    row = ConnectionRow(kind=kind, ports=ports, to=to)
    for tok in tokens:
        if "=" not in tok:
            continue
        key, val = tok.split("=", 1)
        key = key.strip().upper()
        if key in ("R", "L", "C"):
            setattr(row, key, val.strip())
    return row


def build_terminations_rows(mport_rows: Sequence[MeasPortRow] = (),
                            conn_rows: Sequence[ConnectionRow] = (),
                            extra_lines: str = "",
                            nports: int | None = None) -> TerminationSet:
    """
    Connection-table rows -> TerminationSet, via the DSL.

    Going through the text keeps parse_custom_termination_text as the single
    validation authority: one parser, one set of error messages, and the "edit
    as text" view shows literally what gets computed.

    Pass `nports` (the file's port count) to reject out-of-range ports here,
    with a message naming the file size, instead of letting a one-digit typo
    become a plausible wrong answer.
    """
    ts = parse_custom_termination_text(
        rows_to_dsl_text(mport_rows, conn_rows, extra_lines))
    if nports is not None:
        _validate_port_indices(ts, int(nports))
    return ts


def _merge_view(terminations: TerminationSet):
    """
    Union-Find over the ShortPairs -> (find, members).

    `find(port0)` is the merged node's representative and `members[root]` lists
    its 0-based ports.  Shared by inert_lumped_messages, parallel_stamp_messages
    and _check_one_name_per_node so the three can never disagree about what is
    one node -- they are three verdicts on the same question.

    It duplicates compute_z_matrix's Union-Find -- deliberately.  That one is
    inside the bit-exact reduction path and is fused with the index plan it
    builds; factoring it out to share it would mean editing the one function
    the golden regression exists to pin, to buy 8 lines.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            union(cpl.port_i, cpl.port_j)

    members: dict[int, list[int]] = {}
    for p in sorted(set(parent) | set(terminations.per_port)):
        members.setdefault(find(p), []).append(p)
    return find, members


def _params_key(elem) -> tuple:
    """
    What makes two lumped stamps IDENTICAL, for the parallel check.

    Two elements are only "the same element repeated" when they carry the same
    R, L and C.  Grouping by value and not merely by node pair is what keeps
    `1 lumped_between 4 R=50` next to `1 lumped_between 4 L=1n` -- a perfectly
    ordinary R || L -- out of the refusal, while catching the one-row-N-ports
    case the refusal exists for.

    `params` is None on a TerminationSet built in code rather than parsed, so
    the fallback keys on the y_func OBJECT: one DSL directive shares one closure
    across all the ports it names (see parse_custom_termination_text), which is
    exactly the "same element repeated" relation, just without the numbers.
    """
    p = getattr(elem, "params", None)
    if p is None:
        return ("y", id(elem.y_func))
    return ("v", p.get("R", 0.0), p.get("L", 0.0), p.get("C", math.inf))


def _effective_parallel(params: dict | None, n: int) -> str:
    """
    'R 50 Ω becomes 16.7 Ω' for n identical elements in parallel, or "".

    PER ELEMENT TYPE, not a template: n identical impedances in parallel divide
    R by n and L by n and MULTIPLY C by n, and printing one rule for all three
    would be wrong for whichever type the user actually typed.  Only the values
    the user gave are shown -- an omitted R is 0 and an omitted C is inf, and
    neither is a value anybody wrote down.
    """
    if not params or n < 2:
        return ""
    bits: list[str] = []
    R, L, C = params.get("R", 0.0), params.get("L", 0.0), params.get("C", math.inf)
    if R:
        bits.append(f"R {format_si(R, 'Ω')} becomes {format_si(R / n, 'Ω')}")
    if L:
        bits.append(f"L {format_si(L, 'H')} becomes {format_si(L / n, 'H')}")
    if math.isfinite(C):
        bits.append(f"C {format_si(C, 'F')} becomes {format_si(C * n, 'F')}")
    return "; ".join(bits)


def parallel_stamp_messages(terminations: TerminationSet) -> list[str]:
    """
    Identical lumped elements stamped N times across ONE merged node.

    `lumped_between` and `lumped_to_gnd` take a range on their left and apply
    the element to each listed port INDEPENDENTLY.  That is right and it is the
    normal flip-chip case -- 54 VSS bumps each with its own 20 pH is 54 separate
    elements -- so a range there is never refused on its own.

    It is wrong the moment those ports are ALREADY ONE NODE, because then the N
    stamps land between the same two nodes and are N identical elements in
    parallel.  Measured on the 5-port probe network in tests/test_conn_nets.py:

        1 short_to 2,3 ; 1     lumped_between 4 L=10f  ->  10.000 fH   (meant)
        1 short_to 2,3 ; 1,2,3 lumped_between 4 L=10f  ->   3.333 fH   (typed)
        1 short_to 2,3,4 ; 1     lumped_to_gnd R=50    ->  41.667 Ohm  (250||50)
        1 short_to 2,3,4 ; 1,2,3 lumped_to_gnd R=50    ->  15.625 Ohm  (250||16.7)

    Nothing raised, nothing warned, and inert_lumped_messages -- the check next
    to this one -- said nothing either: it only reports elements worth EXACTLY
    zero.  The number is wrong by a factor of N and looks entirely plausible,
    which is the failure class this whole round exists to close.

    A pure function of the TerminationSet, 1-BASED in its messages, empty when
    there is nothing to say.  The repair is in the message because the fix is
    not obvious from the symptom: name ONE member port (any of them -- the node
    is merged), or name the node with `short ... as <name>` and use that.
    """
    find, _members = _merge_view(terminations)
    # (sort key, message).  Sorted at the end by the PORT NUMBERS the message
    # names, never by the dict keys: those carry Union-Find roots, which are
    # arbitrary integers -- see the note below.  The strip shows two lines, so
    # which message is first has to be decided by something the reader can see.
    found: list[tuple] = []

    # --- elements between two nodes -----------------------------------------
    groups: dict[tuple, list[LumpedBetween]] = {}
    for cpl in terminations.couplings:
        if not isinstance(cpl, LumpedBetween):
            continue
        ra, rb = find(cpl.port_i), find(cpl.port_j)
        key = (min(ra, rb), max(ra, rb), _params_key(cpl))
        groups.setdefault(key, []).append(cpl)
    for key, same in groups.items():
        lo, hi, _pk = key
        n = len(same)
        if n < 2 or lo == hi:
            # lo == hi is an element with BOTH ends on one node: worth exactly
            # zero, not N times too much.  inert_lumped_messages owns that one.
            continue
        # Sort each coupling's two ends by which NODE they land on, not by which
        # slot they were typed in: `1 lumped_between 4` and `4 lumped_between 1`
        # share a key, and taking port_i for both would name ports 1 and 4 as
        # "one node" when they are the two ends.
        lo_ports = sorted({(c.port_i if find(c.port_i) == lo else c.port_j) + 1
                           for c in same})
        hi_ports = sorted({(c.port_j if find(c.port_j) == hi else c.port_i) + 1
                           for c in same})
        # WHICH of the two is the merged side is decided by the port lists, and
        # never by `lo` / `hi`.  Those are Union-Find ROOTS -- arbitrary
        # integers whose order falls out of which port happened to win its
        # union -- so testing only the `lo` side made the refusal depend on the
        # user's port numbering.  Measured before this was fixed, on the 5-port
        # probe network with the group moved to high port numbers:
        #     1,2,3    short + 1,2,3    lumped_between 1  -> 3.3333 fH REFUSED
        #     21,22,23 short + 21,22,23 lumped_between 1  -> 3.3333 fH SILENT
        # The same network, the same factor-of-3 error against a typed 10 fH,
        # and the only difference was that port 1's root was the smaller
        # number.  A user cannot see a Union-Find root.
        merged, other = ((lo_ports, hi_ports) if len(lo_ports) >= len(hi_ports)
                         else (hi_ports, lo_ports))
        if len(merged) < 2:
            # One port on each side repeated N times is the same line typed
            # twice, not a range over a merged node -- it is visible on its own
            # row and saying "ports 1 are ALREADY ONE NODE" would be false.
            continue
        left = collapse_ports(merged)
        right = collapse_ports(other)
        eff = _effective_parallel(getattr(same[0], "params", None), n)
        found.append((
            (merged[0], other[0]),
            f"⚠ ports {left} are ALREADY ONE NODE (a short ties them "
            f"together), so the element to port {right} is stamped {n} times "
            f"in PARALLEL, not once"
            + (f" -- {eff}. " if eff else f" ({n} identical elements). ")
            + f"Put ONE member port in that row, or name the node with "
            f"'{NET_KEYWORD} <name>' on the short and use the name."
        ))

    # --- elements to ground -------------------------------------------------
    gnd_groups: dict[tuple, list[int]] = {}
    for port, term in terminations.per_port.items():
        if isinstance(term, LumpedToGnd):
            gnd_groups.setdefault((find(port), _params_key(term)), []).append(port)
    for key, ports_here in gnd_groups.items():
        same_ports = sorted(ports_here)
        n = len(same_ports)
        if n < 2:
            continue
        left = collapse_ports([p + 1 for p in same_ports])
        term = terminations.termination_of(same_ports[0])
        eff = _effective_parallel(getattr(term, "params", None), n)
        found.append((
            (same_ports[0] + 1, 0),
            f"⚠ ports {left} are ALREADY ONE NODE (a short ties them "
            f"together), so the element to GND is stamped {n} times in "
            f"PARALLEL, not once"
            + (f" -- {eff}. " if eff else f" ({n} identical elements). ")
            + f"Put ONE member port in that row, or name the node with "
            f"'{NET_KEYWORD} <name>' on the short and use the name."
        ))
    found.sort(key=lambda item: item[0])
    return [text for _key, text in found]


def inert_lumped_messages(terminations: TerminationSet) -> list[str]:
    """
    Lumped elements that are in the set but contribute EXACTLY nothing.

    compute_z_matrix stamps every lumped element onto Y and only THEN merges
    shorted ports and drops grounded ones.  Both of those later steps can
    annihilate a stamp completely:

      * a LumpedBetween whose two ports land on the same merged node.  The
        stamp is +y, +y, -y, -y over that node's 2x2 block, so summing the
        rows and columns cancels it to zero.  Measured on a real spec:
        `5 short_to 6` next to `5 lumped_between 6 R=...` gave answers for
        R=20 and R=2000 differing by 5e-12 relative -- i.e. roundoff, with
        the ideal-short answer reported both times;
      * a LumpedBetween with BOTH ends grounded, or a LumpedToGnd on a port
        that a short ties to a grounded one: the row and column carrying the
        stamp are deleted outright (measured: bit-identical, 0.0 relative).

    Neither case raises and neither changes the number on screen, so the only
    symptom is that editing R/L/C does nothing -- which reads as "the tool
    ignores my resistor" rather than "my spec shorts it out".  Worse, the
    validation strip used to show `✓ port 5 → 6: 20 Ω` for exactly this, a
    green tick asserting the element was applied.

    A pure function of the TerminationSet, so the GUI strip, Calculate and the
    CLI can all report the same thing.  Messages are 1-BASED, empty when every
    lumped element contributes something.

    This is "the element is worth EXACTLY zero"; parallel_stamp_messages beside
    it is "the element is worth N times what you typed".  Both read the same
    _merge_view, so they cannot disagree about what one node is.
    """
    find, members = _merge_view(terminations)

    def node_is_ground(port: int) -> bool:
        """Does this port's MERGED node evaluate to ground? Mirrors merge_terms."""
        grp = members.get(find(port), [port])
        terms = [terminations.termination_of(p) for p in grp]
        if any(isinstance(t, Signal) for t in terms):
            return False        # a probe on the node wins over ground
        return any(isinstance(t, (Ground, Vdd)) for t in terms)

    msgs: list[str] = []
    for cpl in terminations.couplings:
        if not isinstance(cpl, LumpedBetween):
            continue
        i, j = cpl.port_i + 1, cpl.port_j + 1
        if find(cpl.port_i) == find(cpl.port_j):
            msgs.append(
                f"⚠ the R/L/C element between ports {i} and {j} is SHORTED OUT "
                f"-- a short ties those ports together, so its value has no "
                f"effect at all (changing it will not change the answer). "
                f"Delete the short, or delete the element."
            )
        elif node_is_ground(cpl.port_i) and node_is_ground(cpl.port_j):
            msgs.append(
                f"⚠ the R/L/C element between ports {i} and {j} has BOTH ends "
                f"grounded, so its value has no effect at all."
            )
    for port in sorted(terminations.per_port):
        if (isinstance(terminations.termination_of(port), LumpedToGnd)
                and node_is_ground(port)):
            msgs.append(
                f"⚠ the R/L/C element from port {port + 1} to ground sits on a "
                f"node that a short already ties to ground, so its value has "
                f"no effect at all."
            )
    return msgs


# ============================================================================
# Port roles (what every port of the file is actually doing)
# ============================================================================
#
# The tool already harvests "! Port[12] = VDD_ball_2" into TouchstoneData.
# port_names and then does almost nothing with it.  On a 153-port package
# export nobody has memorised the ball map, and the failure that costs real
# time is not a crash -- it is "I grounded 51 of the 54 ground balls, the
# number was plausible, and I found out three weeks later".  Everything below
# is pure so the GUI's overview strip, its validation strip and the Ports &
# Roles window can never disagree about what a port is doing.

ROLE_PROBE_PLUS = "probe +"
ROLE_PROBE_MINUS = "probe −"
ROLE_GROUND = "ground"
ROLE_VDD = "vdd"
ROLE_ELEMENT = "element"
ROLE_SHORTED = "shorted"
ROLE_OPEN = "open"

# Display order of the overview buckets.  A role is finer-grained than a bucket
# (the two probe sides collapse into one "probe" count) because the strip has
# to stay short while the window has to say which probe touched the port.
OVERVIEW_BUCKETS = ("probe", "ground", "vdd", "element", "shorted", "open")

ROLE_TO_BUCKET = {
    ROLE_PROBE_PLUS: "probe",
    ROLE_PROBE_MINUS: "probe",
    ROLE_GROUND: "ground",
    ROLE_VDD: "vdd",
    ROLE_ELEMENT: "element",
    ROLE_SHORTED: "shorted",
    ROLE_OPEN: "open",
}


@dataclass(frozen=True)
class PortRole:
    """What one port of the file is doing under a given TerminationSet."""
    index: int          # 1-BASED, the number the user types
    name: str           # from the file's "! Port[n] = ..." comments, "" if none
    role: str           # one of the ROLE_* constants
    source: str = ""    # which row / kept-as-text line put it there ("" = none)
    group: str = ""     # measurement-port name, for the two probe roles

    @property
    def bucket(self) -> str:
        return ROLE_TO_BUCKET[self.role]


def _role_of(term: TerminationSet, port0: int,
             elem_ports: set, short_ports: set) -> tuple[str, str]:
    """(role, measurement-port name) for one 0-based port."""
    t = term.termination_of(port0)
    if isinstance(t, Signal):
        group, sign = _normalize_signal(t)
        return (ROLE_PROBE_PLUS if sign > 0 else ROLE_PROBE_MINUS), group
    if isinstance(t, Vdd):
        return ROLE_VDD, ""
    if isinstance(t, Ground):
        return ROLE_GROUND, ""
    if isinstance(t, LumpedToGnd) or port0 in elem_ports:
        return ROLE_ELEMENT, ""
    if port0 in short_ports:
        return ROLE_SHORTED, ""
    return ROLE_OPEN, ""


def port_roles(term: TerminationSet | None,
               nports: int | None = None,
               port_names: Sequence[str] | None = None,
               sources: dict | None = None) -> list[PortRole]:
    """
    One record per port, in port order.  THE single classifier.

    `nports` is the file's port count.  With it, every port of the file gets a
    record and the ones the spec never mentioned come back as `open` -- which is
    the whole point, because an unmentioned port is exactly the one nobody
    checked.  WITHOUT it (no file selected) the open ports are not merely
    unknown in number, they are unknowable: only the ports the rows mention are
    listed, and an explicit `open` row is dropped too, so a caller can never
    render an "open" count that was invented from the largest port typed.

    `sources` maps a 1-BASED port to the row that last assigned it (see
    row_sources).  It is passed in rather than derived here because a
    TerminationSet carries no provenance -- by design; it is what the reduction
    consumes, and the rows are one of several ways to build one.
    """
    if term is None:
        return []
    elem_ports: set = set()
    short_ports: set = set()
    for cpl in term.couplings:
        target = elem_ports if isinstance(cpl, LumpedBetween) else short_ports
        target.add(cpl.port_i)
        target.add(cpl.port_j)

    if nports is not None:
        scan: Sequence[int] = range(int(nports))
    else:
        scan = sorted(set(term.per_port) | elem_ports | short_ports)

    names = list(port_names or [])
    src = sources or {}
    out: list[PortRole] = []
    for i in scan:
        role, group = _role_of(term, i, elem_ports, short_ports)
        if nports is None and role == ROLE_OPEN:
            continue
        out.append(PortRole(index=i + 1,
                            name=names[i] if i < len(names) else "",
                            role=role,
                            source=src.get(i + 1, ""),
                            group=group))
    return out


def row_sources(mport_rows: Sequence[MeasPortRow] = (),
                conn_rows: Sequence[ConnectionRow] = (),
                extra_lines: str = "") -> dict[int, str]:
    """
    1-based port -> the row that LAST assigned it.

    Walked in exactly the order rows_to_dsl_text emits (every measurement port,
    then every connection, then the kept-as-text block), because the DSL is
    last-assignment-wins: the row that decides a port is the last one to name
    it, which is the same rule that makes a `ground` row beat a probe.

    A port field naming a NODE resolves the same way the DSL resolves it -- to
    one representative member -- so the "From" column of Ports & Roles and the
    spec cannot disagree about which row decided a port.  A name nothing defines
    contributes nothing, exactly as a half-typed range does.  Measured on a
    package-sized spec (54 ground balls, 8 named shorts, 8 elements, 6 probes,
    29 DSL lines): rows_to_dsl_text 0.010 ms + _collect_nets 0.043 ms on top of
    a 0.090 ms total, which is nothing against the keystroke this runs from.

    Never raises -- a half-typed range simply contributes nothing, the same way
    it contributes nothing to the spec.
    """
    src: dict[int, str] = {}
    try:
        nets = _collect_nets(rows_to_dsl_text(mport_rows, conn_rows, extra_lines))
    except Exception:
        nets = {}

    def mark(spec: str, label: str) -> None:
        key = _net_key(spec)
        if key in nets:
            if nets[key].port:
                src[nets[key].port] = label
            return
        try:
            ports = parse_port_range(spec)
        except Exception:
            return
        for p in ports:
            src[p] = label

    for i, row in enumerate(mport_rows, start=1):
        if row.is_blank():
            continue
        if row.plus.strip():
            mark(row.plus, f"probe row {i} (+)")
        if row.minus.strip():
            mark(row.minus, f"probe row {i} (−)")

    for i, row in enumerate(conn_rows, start=1):
        if row.is_blank() or not row.ports.strip():
            continue
        mark(row.ports, f"conn row {i}")
        # The partner side of a short / rlc_between is assigned by the same row.
        if row.kind in ("short", "rlc_between") and row.to.strip():
            mark(row.to, f"conn row {i}")

    for n, raw in enumerate((extra_lines or "").splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        parts = body.split()
        if len(parts) < 2:
            continue
        mark(parts[0], f"text line {n}")
        if parts[1].lower() in ("short_to", "lumped_between") and len(parts) > 2:
            mark(parts[2], f"text line {n}")
    return src


def collapse_ports(ports: Sequence[int]) -> str:
    """
    [1, 2, 3, 7] -> '1-3,7'.

    NO SPACES, ever.  The DSL is whitespace-tokenised and the leading port
    field is parts[0], so '1-3, 7' would parse as the port field '1-3,' with a
    stray '7' where the keyword belongs.  parse_port_range accepts the comma
    form, so this round-trips.
    """
    vals = sorted({int(p) for p in ports})
    if not vals:
        return ""
    runs: list[str] = []
    start = prev = vals[0]
    for p in list(vals[1:]) + [None]:
        if p == prev + 1:
            prev = p
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    return ",".join(runs)


@dataclass(frozen=True)
class MergedNode:
    """
    One node a short created, and the token that refers to it.

    `ref` is what a port field must contain to mean this node: the net name when
    it has one, otherwise its first member port -- because referring to a merged
    node by any ONE member already works and is the whole reason nets are sugar.
    It is never the whole group: that spelling is the N-parallel-elements bug.
    """
    ports: tuple = ()     # 1-based members, ascending, always >= 2
    name: str = ""        # net name as typed, "" when the node is unnamed
    ref: str = ""


def merged_nodes(mport_rows: Sequence[MeasPortRow] = (),
                 conn_rows: Sequence[ConnectionRow] = (),
                 extra_lines: str = "") -> list[MergedNode]:
    """
    Every merged node the rows create, ascending by first member.

    For the editor's port dropdowns: R1-2 wants merged nodes at the TOP of them,
    so that referring to a node is the cheap gesture and listing its members --
    which silently multiplies an element by N -- is the awkward one.

    Pure and NEVER raises: it runs from the same variable traces the strips do,
    where a raise reaches no handler we control.  A half-typed spec simply
    contributes fewer nodes.

    It re-serialises and re-parses rather than taking a TerminationSet, so a
    caller cannot hand it a set built some other way and get node names that do
    not match the rows on screen.  Measured on a package-sized spec (54 ground
    balls, 8 named shorts, 8 elements, 6 probes): 0.331 ms, of which the parse
    is 0.149 ms.
    """
    try:
        text = rows_to_dsl_text(mport_rows, conn_rows, extra_lines)
        ts = parse_custom_termination_text(text)
        nets = _collect_nets(text)
    except Exception:
        return []
    find, members = _merge_view(ts)
    by_root_name: dict[int, str] = {}
    for d in nets.values():
        if d.port:
            by_root_name.setdefault(find(d.port - 1), d.name)
    out: list[MergedNode] = []
    for root, group in members.items():
        if len(group) < 2:
            continue
        ports = tuple(p + 1 for p in sorted(group))
        name = by_root_name.get(root, "")
        out.append(MergedNode(ports=ports, name=name,
                              ref=name or str(ports[0])))
    out.sort(key=lambda m: m.ports[0])
    return out


# ---- open ports that look like they were meant to be terminated ------------
#
# A family is a set of ports whose names share a prefix once a trailing ball
# NUMBER is stripped: VSS_ball_31 / VSS_ball_44 belong to 'VSS_ball'.  The check
# fires when a family is overwhelmingly terminated and a small remnant is not.
#
# The thresholds exist to stop it crying wolf, and each was picked against a
# real fixture rather than by taste:
#   * MIN_FAMILY = 4 keeps 'coil1' / 'coil2' out (tests/fixtures/
#     coupled_2port_gndref.s2p).  Probing one coil and leaving the other open is
#     the ordinary way to use that file, and a 2-member family is not evidence
#     of anything;
#   * MIN_TERMINATED = 3 means "a set", not "the one next to it";
#   * MAX_OPEN_FRACTION = 0.25 is what makes this a REMNANT check.  Grounding 5
#     of a 10-ball family and leaving 5 open is a deliberate split; grounding 54
#     and leaving 3 is a typo.  It is also what keeps a file whose ports are all
#     named port1..port153 silent -- one family, most of it open.
OPEN_CLUSTER_MIN_FAMILY = 4
OPEN_CLUSTER_MIN_TERMINATED = 3
OPEN_CLUSTER_MAX_OPEN_FRACTION = 0.25

# How many of the offending names the message spells out before it says "…".
OPEN_CLUSTER_NAMES_SHOWN = 3

_TERMINATED_ROLES = (ROLE_GROUND, ROLE_VDD, ROLE_PROBE_PLUS, ROLE_PROBE_MINUS)


def name_prefix(name: str) -> str:
    """
    'VSS_ball_31' -> 'VSS_ball'; 'coil1' -> 'coil'; 'in_p' -> 'in_p'; '' -> ''.

    Only a TRAILING run of digits is stripped, and only then are the separators
    in front of it.  Stripping digits anywhere would make 'c1_p' and 'c2_p'
    (tests/fixtures/coupled_4port_float.s4p) one family, which is precisely the
    false alarm this whole check has to avoid: those are two different coils.
    A name that does not end in a digit is its own family of one.
    """
    s = (name or "").strip()
    if not s:
        return ""
    i = len(s)
    while i > 0 and s[i - 1].isdigit():
        i -= 1
    if i == len(s):
        return s
    while i > 0 and s[i - 1] in "_-.: []":
        i -= 1
    return s[:i]


@dataclass(frozen=True)
class OpenNameCluster:
    """A family of same-named ports that is mostly terminated and partly not."""
    prefix: str
    kind: str                    # "grounded" or "probed"
    open_ports: tuple            # 1-based
    open_names: tuple
    terminated: int


def open_name_clusters(roles: Sequence[PortRole]) -> list[OpenNameCluster]:
    """
    Open ports whose NAMES say they belong to a set the user terminated.

    Pure, and silent on a file with no port names at all (every prefix is "",
    which is skipped) -- that file has no evidence to offer and a warning
    derived from nothing is worse than no warning.
    """
    fams: dict[str, list[PortRole]] = {}
    for r in roles:
        p = name_prefix(r.name)
        if p:
            fams.setdefault(p, []).append(r)

    out: list[OpenNameCluster] = []
    for prefix in sorted(fams):
        members = fams[prefix]
        if len(members) < OPEN_CLUSTER_MIN_FAMILY:
            continue
        opens = [m for m in members if m.role == ROLE_OPEN]
        if not opens:
            continue
        if len(opens) > OPEN_CLUSTER_MAX_OPEN_FRACTION * len(members):
            continue
        term_roles = [m.role for m in members if m.role in _TERMINATED_ROLES]
        if len(term_roles) < OPEN_CLUSTER_MIN_TERMINATED:
            continue
        grounded = sum(1 for r in term_roles if r in (ROLE_GROUND, ROLE_VDD))
        kind = "grounded" if grounded * 2 >= len(term_roles) else "probed"
        opens.sort(key=lambda m: m.index)
        out.append(OpenNameCluster(
            prefix=prefix, kind=kind,
            open_ports=tuple(m.index for m in opens),
            open_names=tuple(m.name for m in opens),
            terminated=len(term_roles)))
    return out


def open_port_name_messages(roles: Sequence[PortRole]) -> list[str]:
    """One '⚠ …' line per cluster, ready for the validation strip."""
    msgs: list[str] = []
    for c in open_name_clusters(roles):
        n = len(c.open_ports)
        shown = list(c.open_names[:OPEN_CLUSTER_NAMES_SHOWN])
        if n > OPEN_CLUSTER_NAMES_SHOWN:
            shown.append("…")
        msgs.append(
            f"⚠ {n} port{'' if n == 1 else 's'} left OPEN whose "
            f"name{'' if n == 1 else 's'} match the {c.terminated} "
            f"'{c.prefix}' ports you {c.kind} ({', '.join(shown)}). "
            "Check 'Ports & Roles'.")
    return msgs


# ============================================================================
# Z computation: unified termination -> Z(f)
# ============================================================================

def _rank_deficient_warning(k: int, freq_hz: float) -> str:
    """Informational: singular node admittance that pinv handles correctly."""
    return (f"Rank-deficient node admittance at freq[{k}]={freq_hz:.4g} Hz "
            "(pinv used; expected for a fully floating structure)")


def _schur_collapse_warning(k: int, freq_hz: float) -> str:
    """The Schur contraction cancelled to roundoff -- the result is noise."""
    return (f"Schur contraction cancelled to roundoff at freq[{k}]="
            f"{freq_hz:.4g} Hz: the reduced admittance is under "
            f"{SCHUR_COLLAPSE_TOL:g} of the terms that produced it, so every "
            "digit below is numerical noise (expect ~1e16 ohm values). The "
            "usual cause is a floating structure probed with no return path "
            "-- give each measurement port a '-' side, or add the ground "
            "ports the structure needs.")


def _open_probe_warning(k: int, freq_hz: float, name: str) -> str:
    """
    The single-node probe read an open circuit (1/y_eff was not finite).

    Deliberately NOT a claim that the file or the spec is wrong: y_eff also
    passes through zero at a genuine parallel anti-resonance, where a huge Z is
    the answer the user came for, and no single-frequency magnitude test can
    tell that apart from a probe with no return path.  So this reports the
    reading and names both readings of it.
    """
    return (f"Measurement port '{name}' reads an open circuit at freq[{k}]="
            f"{freq_hz:.4g} Hz (its net admittance to the reference node is "
            "zero to the last bit, so Z is infinite). That is either a genuine "
            "parallel anti-resonance or a probe with no return path -- if the "
            "structure is floating, give the measurement port a '-' side or "
            "add the ground ports it needs.")


def _no_return_path_warning(k: int, freq_hz: float, names: Sequence[str]) -> str:
    """
    Escalation of the above: pinv is NOT valid for these measurement ports.

    Deliberately worded as a problem, not as reassurance -- the injected
    current has nowhere to return, so the reading is undefined and the affected
    row and column of Z are NaN rather than a fabricated minimum-norm number.
    """
    who = ", ".join(f"'{n}'" for n in names)
    return (f"Measurement port(s) {who} have no return path for the injected "
            f"current at freq[{k}]={freq_hz:.4g} Hz (their probes are not "
            "orthogonal to the null space of the node admittance). Their row "
            "and column of Z are NaN. Add the missing '-' side or a ground "
            "port.")


def _validate_port_indices(terminations: TerminationSet, n: int) -> None:
    """
    Reject port indices that do not exist in this file.

    Without this an out-of-range probe port is silently dropped (the resolver
    only scans 0..n-1), which turns a one-digit typo -- '3 / 5' on a 4-port
    file -- into a ground-referenced measurement that reports a plausible-
    looking wrong number instead of an error.
    """
    bad: set[int] = {p for p in terminations.per_port if p < 0 or p >= n}
    for cpl in terminations.couplings:
        for p in (cpl.port_i, cpl.port_j):
            if p < 0 or p >= n:
                bad.add(p)
    if bad:
        listed = ", ".join(str(p + 1) for p in sorted(bad))
        raise ValueError(
            f"Port number(s) {listed} are outside this file's {n} ports "
            "(port numbers are 1-based)."
        )


def compute_z_matrix(Y_full: np.ndarray, freqs: np.ndarray,
                     terminations: TerminationSet
                     ) -> tuple[np.ndarray, list[str], list[str]]:
    """
    Apply terminations to Y(f) and return the open-circuit impedance matrix
    Z(f) of every measurement port defined by the Signal ports.

    Returns (Zmat, port_names, warnings_list) with Zmat of shape
    (nfreqs, G, G) complex, where G is the number of measurement ports:

        Zmat[k, g, g]   self impedance of measurement port g
        Zmat[k, a, b]   mutual impedance (all other measurement ports open)

    The reduction pipeline is:
        lumped_between -> lumped_to_gnd -> short merge (Union-Find)
        -> ground/vdd row+col deletion -> Schur elimination of open ports
        -> contraction onto the probe nodes.

    Contraction: one node per non-empty probe side (sides are disjoint because
    each port carries at most one probe), Y_node[i, j] = sum of the Y_red block
    between side i and side j.  With W[node, g] = +1 on port g's plus node and
    -1 on its minus node, Z = W^T @ pinv(Y_node) @ W.  pinv (not inv) is
    required: a fully floating differential structure has a singular Y_node
    whose null direction is common mode, and the balanced +/- injection is
    orthogonal to that direction, so pinv gives the right answer where inv
    returns garbage.

    G == 1 keeps the historical scalar expressions verbatim so that every
    pre-existing mode stays bit-identical -- see compute_z -- except at
    frequencies where they are provably producing noise (_is_singular_2x2),
    which are diverted to the same SVD path as G >= 2.

    Degenerate networks (see _probe_impedance and _is_singular_2x2):

        * singular Y_node, every probe still in range  -> pinv is exact,
          "Rank-deficient node admittance" is emitted as INFORMATION;
        * singular Y_node, some probe out of range     -> that measurement port
          has no return path.  Its whole row and column of Z is NaN and the
          warning names it.  Other ports keep their exact values;
        * Schur contraction cancelled to roundoff      -> advisory warning only
          (magnitude heuristic; the values are still returned).

    Port indices are validated against n up front: an out-of-range probe would
    otherwise be dropped by the resolver and silently demote a differential
    measurement to a ground-referenced one.

    Performance: every step of the plan that does not depend on frequency (the
    Union-Find merge, the merged terminations, the ground/open/signal index
    sets, the Schur keep/eliminate arrays, the node structure and W, and the
    lumped-element admittances) is built once up front; the frequency axis is
    then swept COMPUTE_BATCH at a time and the O(n_open^3) Schur solve runs as
    one stacked np.linalg.solve.  The two products that follow it -- the Schur
    contraction and the contraction onto the probe nodes -- deliberately stay
    per-frequency: numpy routes a dot-shaped 2-D matmul and a stacked one
    through different BLAS calls, so batching them would change the summation
    order.  They are small.  Everything here is bit-exact by construction --
    see the comments on each step and tests/test_golden_regression.py.
    """
    nfreqs, n, _ = Y_full.shape
    omega = 2.0 * np.pi * freqs
    warnings_out: list[str] = []

    # --- 1. Resolve the measurement ports (probe pairs)
    _validate_port_indices(terminations, n)
    mports = resolve_meas_ports(terminations, n)
    if not mports:
        raise ValueError("No Signal-group-A ports defined; nothing to measure.")
    port_names = [mp.name for mp in mports]
    G = len(mports)

    # Node layout: plus side of port 0, minus side of port 0 (if any),
    # plus side of port 1, ...  side_keys[node] is the (group, sign) that
    # selects the member ports of that node.
    side_keys: list[tuple[str, int]] = []
    node_of: list[tuple[int, int | None]] = []
    for mp in mports:
        plus_node = len(side_keys)
        side_keys.append((mp.name, +1))
        minus_node: int | None = None
        if mp.minus:
            minus_node = len(side_keys)
            side_keys.append((mp.name, -1))
        node_of.append((plus_node, minus_node))
    n_nodes = len(side_keys)
    has_b = (G == 1 and n_nodes == 2)

    W = np.zeros((n_nodes, G), dtype=complex)
    for g, (plus_node, minus_node) in enumerate(node_of):
        W[plus_node, g] = 1.0
        if minus_node is not None:
            W[minus_node, g] = -1.0

    # --- 2. Resolve short pairs via Union-Find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            union(cpl.port_i, cpl.port_j)

    root_to_members: dict[int, list[int]] = {}
    for i in range(n):
        root_to_members.setdefault(find(i), []).append(i)

    # Map from old port index -> new merged-port index (sorted by smallest member)
    merged_reps = sorted({min(members) for members in root_to_members.values()})
    rep_to_new_idx = {rep: ni for ni, rep in enumerate(merged_reps)}
    old_to_new = {old: rep_to_new_idx[min(root_to_members[find(old)])] for old in range(n)}
    new_n = len(merged_reps)

    # --- 3. Determine post-merge per-port termination
    def merge_terms(members: list[int]) -> PortTermination:
        terms = [terminations.termination_of(p) for p in members]
        sig_groups = {_normalize_signal(t) for t in terms if isinstance(t, Signal)}
        if len(sig_groups) > 1:
            shown = {f"{grp}{'+' if sgn > 0 else '-'}" for grp, sgn in sig_groups}
            raise ValueError(
                f"Ports {members} merged via short, but assigned to "
                f"conflicting signal groups {shown}"
            )
        if sig_groups:
            return Signal(*next(iter(sig_groups)))
        if any(isinstance(t, (Ground, Vdd)) for t in terms):
            return Ground()
        lumped = [t for t in terms if isinstance(t, LumpedToGnd)]
        if lumped:
            funcs = [t.y_func for t in lumped]

            def combined(om: np.ndarray, _funcs=funcs) -> np.ndarray:
                total = np.zeros(np.asarray(om).shape, dtype=complex)
                for f in _funcs:
                    total = total + f(om)
                return total
            return LumpedToGnd(combined)
        return Open()

    new_terms: list[PortTermination] = [Open()] * new_n
    for rep, members in root_to_members.items():
        ni = rep_to_new_idx[min(members)]
        new_terms[ni] = merge_terms(members)

    # Member ports of each probe node, in the merged index space.
    new_sides: list[list[int]] = [
        [i for i, t in enumerate(new_terms)
         if isinstance(t, Signal) and _normalize_signal(t) == key]
        for key in side_keys
    ]
    new_sig_set = {i for side in new_sides for i in side}
    new_gnd = [i for i, t in enumerate(new_terms) if isinstance(t, (Ground, Vdd))]

    # --- 4. Frequency-invariant reduction plan
    #
    # Everything below used to be rebuilt inside the frequency loop.  None of it
    # depends on k, and on a 153-port x 5000-frequency file rebuilding it cost
    # more than the linear algebra did.

    # 4a/4b plan: evaluate each lumped y_func ONCE over the whole omega axis
    # instead of on a one-element array per frequency.  The y_func contract is
    # elementwise, so element k of the full evaluation is bit-identical to the
    # old single-point call (verified for every y_* helper, including the
    # merge_terms "combined" closure).
    lumped_between_plan: list[tuple[int, int, np.ndarray]] = [
        (cpl.port_i, cpl.port_j, np.asarray(cpl.y_func(omega), dtype=complex))
        for cpl in terminations.couplings
        if isinstance(cpl, LumpedBetween)
    ]
    lumped_gnd_plan: list[tuple[int, np.ndarray]] = []
    for port in range(n):
        t = terminations.termination_of(port)
        if isinstance(t, LumpedToGnd):
            lumped_gnd_plan.append(
                (port, np.asarray(t.y_func(omega), dtype=complex)))
    stamps_lumped = bool(lumped_between_plan or lumped_gnd_plan)

    # 4c plan: index arrays for the shorted-port merge.
    #
    # The old code walked all n^2 (i, j) pairs in Python.  Split them instead:
    # a cell of the merged matrix whose row group AND column group are both
    # singletons receives exactly one contribution, so that whole block is a
    # single vectorised gather-add (still "0 + x", so still bit-identical).
    # Only cells fed by a multi-member group need order-preserving accumulation,
    # and those go through np.add.at, which applies duplicate indices
    # sequentially in the order given -- feed it the same row-major order the
    # Python loop used and the floating-point result is unchanged.  A matmul
    # would sum in a different order and must not be used here.
    merge_single_dst = merge_single_src = None
    merge_acc_dst = merge_acc_src = None
    if new_n < n:
        o2n = np.array([old_to_new[i] for i in range(n)], dtype=np.intp)
        port_grp_size = np.bincount(o2n, minlength=new_n)[o2n]
        singles = np.nonzero(port_grp_size == 1)[0]
        multis = np.nonzero(port_grp_size > 1)[0]
        if singles.size:
            merge_single_src = (singles[:, None], singles[None, :])
            merge_single_dst = (o2n[singles][:, None], o2n[singles][None, :])
        acc_i_parts: list[np.ndarray] = []
        acc_j_parts: list[np.ndarray] = []
        for rows, cols in ((singles, multis), (multis, singles), (multis, multis)):
            if rows.size and cols.size:
                acc_i_parts.append(np.repeat(rows, cols.size))
                acc_j_parts.append(np.tile(cols, rows.size))
        if acc_i_parts:
            acc_i = np.concatenate(acc_i_parts)
            acc_j = np.concatenate(acc_j_parts)
            merge_acc_src = (acc_i, acc_j)
            merge_acc_dst = (o2n[acc_i], o2n[acc_j])

    # 4d/4e plan: the ground drop was a relabelling of the merged matrix, so
    # composing it into the Schur index arrays selects exactly the same entries
    # in exactly the same order -- and saves materialising the dropped matrix.
    gnd_set = set(new_gnd)
    oo_g = np.array([i for i in range(new_n)
                     if i not in new_sig_set and i not in gnd_set],
                    dtype=np.intp)
    sig_idx_g = sorted(i for side in new_sides for i in side)
    kk_g = np.array(sig_idx_g, dtype=np.intp)
    if oo_g.size:
        kept_pos = {old: pos for pos, old in enumerate(sig_idx_g)}
        sides_pos = [[kept_pos[i] for i in side] for side in new_sides]
    else:
        sides_pos = [list(side) for side in new_sides]

    # 4f plan: the fancy-index tuples and the all-ones vector are constant.
    node_ix = [[np.ix_(sides_pos[i], sides_pos[j]) for j in range(n_nodes)]
               for i in range(n_nodes)]
    ones_a = np.ones(len(sides_pos[0]), dtype=complex)

    # --- 5. Frequency sweep, a chunk of frequencies at a time
    Zmat = np.empty((nfreqs, G, G), dtype=complex)
    fallback_warnings = 0
    rank_warnings = 0
    probe_warnings = 0
    collapse_warnings = 0
    check_collapse = bool(oo_g.size) and int(kk_g.size) >= 2

    # Size the chunk from the widest array a chunk actually materialises, not
    # from the port count: a 153-port file whose ports are nearly all grounded
    # reduces to a tiny Schur block and should still get a big batch.  The
    # candidates are the stamped copy of Y (only when lumped elements exist),
    # the merged matrix (only when ports are shorted) and the Schur Y_oo block.
    batch = _freq_batch(max(
        int(oo_g.size),
        new_n if new_n < n else 0,
        n if stamps_lumped else 0,
        1,
    ))
    for start in range(0, nfreqs, batch):
        stop = min(start + batch, nfreqs)
        nb = stop - start

        if stamps_lumped:
            Yb = Y_full[start:stop].astype(complex, copy=True)
            # 5a. lumped_between, then 5b. lumped_to_gnd -- same order as
            # before, both on original (pre-merge) port indices.
            for i, j, yv in lumped_between_plan:
                yk = yv[start:stop]
                Yb[:, i, i] += yk
                Yb[:, j, j] += yk
                Yb[:, i, j] -= yk
                Yb[:, j, i] -= yk
            for port, yv in lumped_gnd_plan:
                Yb[:, port, port] += yv[start:stop]
        else:
            # Nothing is stamped in place, so a view is enough (no-op when
            # Y_full is already complex128).
            Yb = np.asarray(Y_full[start:stop], dtype=complex)

        # 5c. Merge shorted ports (sum rows + cols).
        if new_n < n:
            Ym = np.zeros((nb, new_n, new_n), dtype=complex)
            for i in range(nb):
                dst, src = Ym[i], Yb[i]
                if merge_single_dst is not None:
                    dst[merge_single_dst] += src[merge_single_src]
                if merge_acc_dst is not None:
                    np.add.at(dst, merge_acc_dst, src[merge_acc_src])
            Yb = Ym

        # 5d/5e. Ground drop (folded into the indices) + Schur elimination of
        # the remaining "open-like" ports.  The O(n_open^3) solve is batched
        # over the chunk; the cheap `Y_kk - Y_ko @ X` contraction is NOT.
        #
        # Batching that matmul is not bit-exact: when only one port survives the
        # reduction (Mode 1 with a single signal port -- the most common case of
        # all) the per-matrix product is (1, n_open) @ (n_open, 1), and numpy
        # routes a 2-D dot-shaped matmul and a stacked one through different
        # BLAS calls, which sum in different orders.  Measured drift on a
        # 16-port fixture: 2.7e-16 relative -- numerically irrelevant, but the
        # acceptance criterion here is bit-identical, so it stays per-frequency
        # in 5f below.  It costs one small matmul per frequency and the solve,
        # which is where the time actually goes, is still batched.
        #
        # np.ascontiguousarray on the two matmul operands is load-bearing for
        # the same reason: 3-D advanced indexing with a leading slice hands back
        # a Fortran-ish layout, and a strided operand makes BLAS pick a
        # different kernel (and a different summation order) than the
        # C-contiguous 2-D block the old code built.  Y_oo / Y_ok are left alone
        # -- the LAPACK gufunc linearises its inputs, so solve is
        # stride-insensitive, and Y_oo is the one array here big enough for an
        # extra copy to hurt.
        if oo_g.size:
            Y_kk = np.ascontiguousarray(Yb[:, kk_g[:, None], kk_g[None, :]])
            Y_ko = np.ascontiguousarray(Yb[:, kk_g[:, None], oo_g[None, :]])
            Y_ok = Yb[:, oo_g[:, None], kk_g[None, :]]
            Y_oo = Yb[:, oo_g[:, None], oo_g[None, :]]
            try:
                X = np.ascontiguousarray(np.linalg.solve(Y_oo, Y_ok))
            except np.linalg.LinAlgError:
                # One singular matrix poisons the whole stacked solve, so redo
                # the chunk one frequency at a time on C-contiguous blocks --
                # i.e. exactly the arrays the 2-D code path used to build.  The
                # healthy frequencies get the same np.linalg.solve they always
                # did and only the singular ones fall back to lstsq (still
                # capped at 3 warnings).
                X = np.empty((nb,) + Y_ok.shape[1:], dtype=complex)
                for i in range(nb):
                    A_oo = np.ascontiguousarray(Y_oo[i])
                    B_ok = np.ascontiguousarray(Y_ok[i])
                    try:
                        X[i] = np.linalg.solve(A_oo, B_ok)
                        continue
                    except np.linalg.LinAlgError:
                        pass
                    try:
                        X[i], *_ = np.linalg.lstsq(A_oo, B_ok,
                                                   rcond=SCHUR_LSTSQ_RCOND)
                    except np.linalg.LinAlgError:
                        # lstsq is the LAST resort and it can fail too: LAPACK's
                        # SVD does not converge on a non-finite A_oo, and a
                        # non-finite A_oo is ORDINARY here rather than exotic.
                        # A lumped L to ground is y = 1/(jwL), which numpy
                        # evaluates to inf+nanj at w == 0 -- so any spec with a
                        # ground-lead inductance, read off a file that carries a
                        # DC point (every composed sweep keeps 0 Hz), puts a NaN
                        # in Y_oo at exactly the frequency where a DC-isolated
                        # port also makes it exactly singular.  Both conditions
                        # are needed and both are normal, which is why this
                        # aborted a 98-port package run at index 0 while every
                        # other frequency in the sweep was healthy.
                        #
                        # Same rule and same reason as _probe_impedance's guard
                        # on its own SVD: one bad frequency must NaN that
                        # frequency, never the sweep.  complex(nan, nan), not
                        # np.nan -- a real NaN in a complex array leaves
                        # imag == 0 and L = Im(Z)/omega would read as a
                        # perfectly plausible 0 H instead of "undefined".
                        X[i] = complex(float("nan"), float("nan"))
                        if fallback_warnings < 3:
                            k = start + i
                            warnings_out.append(
                                f"Schur reduction is undefined at freq[{k}]="
                                f"{freqs[k]:.4g} Hz (Y_oo is singular AND "
                                f"non-finite -- at 0 Hz an ideal series L to "
                                f"ground is 1/(jwL) = inf). Z is NaN at that "
                                f"frequency only; the rest of the sweep is "
                                f"unaffected."
                            )
                            fallback_warnings += 1
                        continue
                    if fallback_warnings < 3:
                        k = start + i
                        warnings_out.append(
                            f"Schur fallback to lstsq at freq[{k}]="
                            f"{freqs[k]:.4g} Hz (Y_oo singular)"
                        )
                        fallback_warnings += 1
        else:
            Y_kk = Y_ko = X = None

        # 5f. Contract onto the probe nodes, one frequency at a time.
        #
        # The two G == 1 branches below are the historical expressions, kept
        # character-for-character so that every pre-existing mode reproduces
        # bit-for-bit. Do not "unify" them with the general branch, and do not
        # batch them: `ones @ M @ ones` is a gemv+dotu pair whose summation
        # order a stacked gemm would not reproduce.  The matrices here are
        # probe-sized (a handful of rows), so the Python loop is cheap.
        #
        # The degeneracy guards added around them are all one-way: they only
        # divert a frequency away from the historical expression when that
        # expression is provably producing noise.  A non-degenerate network
        # never notices them, which is what keeps the golden reference green.
        for i in range(nb):
            k = start + i
            # Y_ko[i] / X[i] are (ns, n_open) / (n_open, ns) C-contiguous views,
            # i.e. exactly the arrays the 2-D code path used to build.
            if X is None:
                Y_red = Yb[i]
            else:
                prod = Y_ko[i] @ X[i]
                Y_red = Y_kk[i] - prod
                if check_collapse and i == 0 and collapse_warnings < 3:
                    # Did the Schur contraction cancel down to pure roundoff?
                    # That is what a floating structure probed with no return
                    # path looks like: Y_kk and Y_ko @ X are equal to the last
                    # bit, Y_red is 1e-16 of either, and everything downstream
                    # is noise amplified to ~1e16 ohms.  Checked once per chunk
                    # (the condition is topological, so it does not vary with
                    # frequency) and only when at least two ports survive:
                    # with a single kept port Y_red IS the scalar y_eff, which
                    # also passes through zero at a perfectly legitimate
                    # parallel anti-resonance.
                    #
                    # Advisory only -- unlike the rank/range test below, this
                    # is a magnitude heuristic with a few decades of margin on
                    # real data (measured: healthy fixtures bottom out at
                    # 3.8e-10, the degenerate one at 7e-16), so it must not
                    # turn a legitimate near-cancellation into NaN.
                    scale = max(float(np.abs(Y_kk[i]).max()),
                                float(np.abs(prod).max()))
                    if (scale > 0.0
                            and float(np.abs(Y_red).max())
                            <= SCHUR_COLLAPSE_TOL * scale):
                        warnings_out.append(
                            _schur_collapse_warning(k, freqs[k]))
                        collapse_warnings += 1
            if G == 1 and not has_b:
                # A single node: "rank-deficient" here means y_eff == 0, and
                # 1/0 -> inf is already the honest reading (a probe with no
                # return path measures an open circuit).  Deliberately NOT
                # thresholded: y_eff also passes through zero at a genuine
                # parallel anti-resonance, where a huge Z is the answer the
                # user came for, and no magnitude test can tell the two apart
                # at a single frequency.
                y_eff = ones_a @ Y_red[node_ix[0][0]] @ ones_a
                # errstate, not a threshold: the divide is ALLOWED to produce
                # inf/nan here, and the numbers are unchanged.  What changes is
                # where the diagnostic goes.  numpy's own "divide by zero" /
                # "invalid value" warning is written to stderr, which a
                # double-clicked GUI discards -- so the only notice the one
                # branch with no other guard produced was invisible to every
                # GUI user, while the results pane printed the roundoff as a
                # formatted measurement ("3.6e+03 TOhm  -11.5 MH") with no
                # annotation.  warnings_out is the channel the GUI already
                # prints under "Calculate @ ..." and the CLI already reports.
                with np.errstate(divide="ignore", invalid="ignore"):
                    z_eff = 1.0 / y_eff
                Zmat[k, 0, 0] = z_eff
                if not np.isfinite(z_eff) and probe_warnings < 3:
                    warnings_out.append(_open_probe_warning(
                        k, freqs[k], port_names[0]))
                    probe_warnings += 1
            elif G == 1:
                Y2 = np.empty((2, 2), dtype=complex)
                Y2[0, 0] = Y_red[node_ix[0][0]].sum()
                Y2[0, 1] = Y_red[node_ix[0][1]].sum()
                Y2[1, 0] = Y_red[node_ix[1][0]].sum()
                Y2[1, 1] = Y_red[node_ix[1][1]].sum()
                if _is_singular_2x2(Y2, PINV_RCOND):
                    # np.linalg.inv would NOT raise here (see _is_singular_2x2)
                    # and Z2[0,0]+Z2[1,1]-Z2[0,1]-Z2[1,0] would be the
                    # difference of four ~1e16 numbers.  Take the same SVD path
                    # the G >= 2 branch uses instead.
                    Z2v, _deficient, bad = _probe_impedance(Y2, W, PINV_RCOND)
                    Zmat[k, 0, 0] = Z2v[0, 0]
                    if bad and probe_warnings < 3:
                        warnings_out.append(_no_return_path_warning(
                            k, freqs[k], [port_names[g] for g in bad]))
                        probe_warnings += 1
                    elif not bad and rank_warnings < 3:
                        warnings_out.append(
                            _rank_deficient_warning(k, freqs[k]))
                        rank_warnings += 1
                    continue
                try:
                    Z2 = np.linalg.inv(Y2)
                except np.linalg.LinAlgError:
                    Z2 = np.linalg.pinv(Y2)
                Zmat[k, 0, 0] = Z2[0, 0] + Z2[1, 1] - Z2[0, 1] - Z2[1, 0]
            else:
                Y_node = np.empty((n_nodes, n_nodes), dtype=complex)
                for a in range(n_nodes):
                    row_ix = node_ix[a]
                    for b in range(n_nodes):
                        Y_node[a, b] = Y_red[row_ix[b]].sum()
                Zk, deficient, bad = _probe_impedance(Y_node, W, PINV_RCOND)
                Zmat[k] = Zk
                if bad and probe_warnings < 3:
                    warnings_out.append(_no_return_path_warning(
                        k, freqs[k], [port_names[g] for g in bad]))
                    probe_warnings += 1
                elif deficient and not bad and rank_warnings < 3:
                    warnings_out.append(_rank_deficient_warning(k, freqs[k]))
                    rank_warnings += 1

    return Zmat, port_names, warnings_out


def _is_singular_2x2(Y2: np.ndarray, rcond: float) -> bool:
    """
    Cheap O(1) rank check for the 2x2 probe-node matrix, no SVD.

    For a 2x2, s1*s2 == |det| and |Y2|_max <= s1 <= 2*|Y2|_max, so
    |det| <= rcond * |Y2|_max^2 brackets `s2 <= rcond * s1` to within a factor
    of 4 -- plenty, because the gap this has to straddle is enormous: on the
    repo's fixtures a healthy 2x2 lands at |det|/|Y2|_max^2 >= 1e-8 while the
    exactly-floating one sits at <= 3e-15.

    Why this exists: LAPACK's LU gives a mathematically singular
    Y2 = [[y, -y], [-y, y]] a determinant of ~1e-19 rather than exactly 0, so
    np.linalg.inv returns a ~1e16-magnitude garbage matrix instead of raising,
    and the `except LinAlgError -> pinv` guard is dead in exactly the case it
    was written for.  Z = Z2[0,0] + Z2[1,1] - Z2[0,1] - Z2[1,0] is then the
    difference of four ~1e16 numbers.  This test routes those frequencies to
    the SVD path *before* inv gets the chance.
    """
    # .tolist() hands back plain Python complex objects; pure-Python abs/mul on
    # four of them measures 0.27 us against 0.39 us for the same expression on
    # numpy complex128 scalars, which matters because this sits in the
    # per-frequency loop of the most common mode in the tool.
    (a, b), (c, d) = Y2.tolist()
    scale = max(abs(a), abs(b), abs(c), abs(d))
    if not math.isfinite(scale) or scale <= 0.0:
        return True
    return abs(a * d - b * c) <= rcond * scale * scale


def _probe_impedance(Y_node: np.ndarray, W: np.ndarray,
                     rcond: float) -> tuple[np.ndarray, bool, list[int]]:
    """
    Z = W^T @ pinv(Y_node) @ W, plus an explicit "does this probe have a return
    path?" check.  Returns (Z, rank_deficient, bad_probe_columns).

    One SVD does all three jobs (pinv, the rank flag and the null-space test);
    the pinv is assembled with numpy's own expression so the result is
    bit-identical to `np.linalg.pinv(Y_node, rcond=rcond)` -- verified by
    np.array_equal.

    Why the null-space test is not optional
    ---------------------------------------
    pinv is the correct inverse only for probe vectors that lie in
    range(Y_node).  That holds for a balanced +/- probe on a floating structure
    (the case pinv is here for: the null direction is the common mode and the
    balanced injection is orthogonal to it).  It does NOT hold for, say, a
    ground-referenced probe on a structure with no path to the reference node.
    For those, pinv happily returns a finite, plausible-looking minimum-norm
    number -- 1.25 + j1.57 ohm (exactly Z_series/4) for a floating pair probed
    single-ended, or a flat 0 ohm for a floating series element, where the true
    answer is "infinite / undefined".  Such probes get NaN in their whole row
    and column instead, and the caller warns.  Measurement ports whose probes
    *are* in range keep their exact values: Z[b, c] = w_b^T Y^+ w_c only
    involves those two columns.
    """
    G = W.shape[1]
    try:
        u, s, vh = np.linalg.svd(Y_node, full_matrices=False)
    except np.linalg.LinAlgError:
        # A non-finite entry at one frequency (a bad data point in the source
        # file) must NaN that frequency, not abort the whole sweep.
        undefined = complex(float("nan"), float("nan"))
        return np.full((G, G), undefined), True, list(range(G))
    smax = float(s[0]) if s.size else 0.0
    large = s > rcond * smax
    rank = int(np.count_nonzero(large))
    sinv = np.zeros_like(s)
    np.divide(1.0, s, out=sinv, where=large)
    # numpy's own pinv expression, term for term, so the product below is the
    # same arithmetic in the same order as `W.T @ pinv(Y_node, rcond) @ W`.
    Z_node = vh.conj().T @ (sinv[:, None] * u.conj().T)
    Z = W.T @ Z_node @ W

    deficient = rank < Y_node.shape[0]
    bad: list[int] = []
    if deficient:
        # Injection at probe g is solvable only if W[:, g] is in range(Y_node)
        # = span(u[:, :rank]); the reading at probe g is single-valued only if
        # W[:, g] is orthogonal to null(Y_node) = span(vh[rank:].conj()).  For a
        # reciprocal (symmetric) Y_node the two coincide, but check both so a
        # mildly non-reciprocal input cannot slip through.
        norms = np.linalg.norm(W, axis=0)
        out_of_range = np.linalg.norm(u[:, rank:].conj().T @ W, axis=0)
        not_measurable = np.linalg.norm(vh[rank:] @ W, axis=0)
        resid = np.maximum(out_of_range, not_measurable)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(norms > 0.0, resid / norms, 0.0)
        bad = [int(g) for g in np.nonzero(rel > PROBE_RANGE_TOL)[0]]
        if bad:
            # complex(nan, nan), not np.nan: assigning a real NaN to a complex
            # array leaves imag == 0, and L = Im(Z)/omega would then read as a
            # perfectly plausible 0 H instead of "undefined".
            undefined = complex(float("nan"), float("nan"))
            Z = Z.copy()
            Z[bad, :] = undefined
            Z[:, bad] = undefined
    return Z, deficient, bad


def compute_z(Y_full: np.ndarray, freqs: np.ndarray,
              terminations: TerminationSet) -> tuple[np.ndarray, list[str]]:
    """
    Apply terminations to Y(f) and return Z(f) for the measurement defined
    by Signal-group ports.

    - One Signal group ("A"): driving-point.  Z = 1 / (1^T * Y_red * 1)
    - Two Signal groups ("A","B"): port-to-port.  Collapse to 2x2,
      Z = Z11 + Z22 - Z12 - Z21.  ("B" is the minus side of "A".)

    Returns (Z, warnings_list); Z is shape (nfreqs,) complex.

    This is a thin wrapper over compute_z_matrix that returns the self
    impedance of the FIRST measurement port.  Because of the G == 1 branches
    in compute_z_matrix it is bit-identical to the historical implementation
    for every mode that defines a single measurement port -- which is all four
    named modes and every legacy Mode 5 spec.  Use compute_z_matrix directly
    when more than one measurement port is defined.
    """
    Zmat, _names, warnings_out = compute_z_matrix(Y_full, freqs, terminations)
    if len(_names) > 1:
        # Only Mode 5 can get here (the named builders can only ever produce
        # one measurement port), and Mode 5 is exactly the free-text mode where
        # a typo -- 'signal V' for 'signal B' -- silently defines a second
        # measurement port.  Returning port 1's self impedance without saying
        # so is a wrong number with no visible difference.
        others = ", ".join(f"'{nm}'" for nm in _names[1:])
        warnings_out = list(warnings_out) + [
            f"{len(_names)} measurement ports are defined "
            f"({', '.join(_names)}), but this result is the self impedance of "
            f"'{_names[0]}' alone; {others} are ignored here. Use Mode 6 / "
            "compute_z_matrix for the mutual terms, or check the signal group "
            "names for a typo."
        ]
    return Zmat[:, 0, 0], warnings_out


# ============================================================================
# RLC extraction (single frequency)
# ============================================================================

@dataclass
class RLCResult:
    freq_hz: float
    Z: complex
    R_ohm: float
    L_henry: float       # signed: Im(Z)/omega; negative past SRF (capacitive)
    C_farad: float       # signed: -1/(omega*Im(Z)); negative below SRF (inductive)
    Q: float             # signed: Im(Z)/Re(Z); negative when capacitive


def extract_rlc_at_freq(freqs: np.ndarray, Z: np.ndarray, target_freq_hz: float) -> RLCResult:
    """
    Pick the data point closest to target_freq_hz and report R, L, C, Q.

    L, C, Q are signed (Cadence convention). The caller is responsible for
    interpreting the sign — see RLCResult docstring.
    """
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    idx = int(np.argmin(np.abs(freqs - target_freq_hz)))
    f = float(freqs[idx])
    z = complex(Z[idx])
    r = z.real
    im = z.imag
    omega = 2.0 * math.pi * f
    L = im / omega if omega != 0.0 else float("nan")
    C = -1.0 / (omega * im) if (omega != 0.0 and im != 0.0) else float("nan")
    Q = im / r if r != 0.0 else float("nan")
    return RLCResult(freq_hz=f, Z=z, R_ohm=r, L_henry=L, C_farad=C, Q=Q)


# ============================================================================
# Mutual-coupling extraction (single frequency, G x G Z matrix)
# ============================================================================

@dataclass
class PortRLC:
    """Self impedance of one measurement port, decomposed. All values signed."""
    name: str
    Z: complex
    R_ohm: float
    L_henry: float       # Im(Z)/omega; negative past SRF (capacitive)
    C_farad: float       # -1/(omega*Im(Z)); negative below SRF (inductive)
    Q: float             # Im(Z)/Re(Z); negative when capacitive


@dataclass
class PairCoupling:
    """
    Mutual impedance between two measurement ports (all others open).

    M_henry and C_c_farad are SIGNED, exactly like L and C on the diagonal:
    Im(Z_ab) > 0 means inductive coupling (read M), Im(Z_ab) < 0 means
    capacitive coupling (read C_c).  Never take abs() of these.

    M_over_La is the COUPLING RATIO into port a (and M_over_Lb into port b):
    the first-order Norton injection ratio M/L_a, frequency-independent by
    construction, which is the number a spur / pulling budget is written
    against.  It is NOT the exact current-transfer ratio: the current a shorted
    port a draws when port b is driven is I_a/I_b = -Z_ab/Z_aa, and M/L_a
    equals |Z_ab/Z_aa| only where omega*L_a >> R_a (they differ by 1000%+ a
    decade below the R = omega*L corner).  The *_dB fields are 20*log10 of the
    magnitude of the ratio.
    """
    name_a: str
    name_b: str
    Z_ab: complex
    M_henry: float
    C_c_farad: float
    k: float                 # M / sqrt(L_a * L_b); NaN if L_a <= 0 or L_b <= 0
    M_over_La: float
    M_over_Lb: float
    M_over_La_dB: float
    M_over_Lb_dB: float
    notes: list[str] = field(default_factory=list)


@dataclass
class CouplingResult:
    freq_hz: float
    Z_matrix: np.ndarray     # (G, G) complex, at freq_hz
    names: list[str]
    ports: list[PortRLC]
    pairs: list[PairCoupling]
    reciprocity_error: float  # max|Z_ab - Z_ba| / max|Z_ab| off-diagonal


def _ratio_db(ratio: float) -> float:
    """20*log10(|ratio|); NaN when the ratio is zero or non-finite."""
    if not math.isfinite(ratio) or ratio == 0.0:
        return float("nan")
    return 20.0 * math.log10(abs(ratio))


def extract_coupling_at_freq(freqs: np.ndarray, Zmat: np.ndarray,
                             names: Sequence[str],
                             target_freq_hz: float) -> CouplingResult:
    """
    Pick the data point closest to target_freq_hz and report, for the G x G
    Z matrix produced by compute_z_matrix:

        * per measurement port: R, L, C, Q of the self impedance Z[g][g]
        * per unordered pair a<b: M = Im(Z_ab)/omega, C_c = -1/(omega*Im(Z_ab)),
          k = M/sqrt(L_a*L_b), the coupling ratios M/L_a and M/L_b, and their
          dB magnitudes (see PairCoupling: M/L is the Norton injection ratio,
          not the exact current-transfer ratio)
        * reciprocity_error, a numeric-health check on the input data

    Every R/L/C/Q/M/C_c value keeps its physical sign (Cadence convention) and
    is never clipped to NaN; NaN appears only where a quantity is genuinely
    undefined (division by zero, or k with a non-inductive port).
    """
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    Zmat = np.asarray(Zmat)
    if Zmat.ndim != 3 or Zmat.shape[1] != Zmat.shape[2]:
        raise ValueError(
            f"Zmat must have shape (nfreqs, G, G), got {Zmat.shape}"
        )
    G = Zmat.shape[1]
    names = list(names)
    if len(names) != G:
        raise ValueError(
            f"names has {len(names)} entries but Zmat has {G} measurement ports"
        )

    idx = int(np.argmin(np.abs(freqs - target_freq_hz)))
    f = float(freqs[idx])
    omega = 2.0 * math.pi * f
    Zk = np.array(Zmat[idx], dtype=complex)

    ports: list[PortRLC] = []
    for g in range(G):
        z = complex(Zk[g, g])
        r = z.real
        im = z.imag
        L = im / omega if omega != 0.0 else float("nan")
        C = -1.0 / (omega * im) if (omega != 0.0 and im != 0.0) else float("nan")
        Q = im / r if r != 0.0 else float("nan")
        ports.append(PortRLC(name=names[g], Z=z, R_ohm=r,
                             L_henry=L, C_farad=C, Q=Q))

    pairs: list[PairCoupling] = []
    for a in range(G):
        for b in range(a + 1, G):
            z_ab = complex(Zk[a, b])
            im = z_ab.imag
            M = im / omega if omega != 0.0 else float("nan")
            C_c = (-1.0 / (omega * im)
                   if (omega != 0.0 and im != 0.0) else float("nan"))
            La = ports[a].L_henry
            Lb = ports[b].L_henry
            notes: list[str] = []

            # A NaN Z means the measurement itself is undefined (no return path
            # for the probe current), NOT that the port is past its SRF.  Say
            # so instead of emitting the past-SRF advice, which would send the
            # user looking for a resonance that is not there.
            undefined = [nm for nm, z in ((names[a], ports[a].Z),
                                          (names[b], ports[b].Z),
                                          (f"{names[a]}<->{names[b]}", z_ab))
                         if not (math.isfinite(z.real) and math.isfinite(z.imag))]
            if undefined:
                notes.append(
                    "Z is undefined for " + ", ".join(undefined)
                    + ": the probe current has no return path -- see the "
                      "warnings, not a resonance")
            if math.isfinite(im) and im < 0.0:
                notes.append("Im(Z_ab) < 0: coupling is capacitive here, "
                             "read C_c instead of M")
            if not undefined and not (math.isfinite(La) and La > 0.0):
                notes.append("L_a <= 0 at this frequency (past SRF): "
                             "k undefined")
            if not undefined and not (math.isfinite(Lb) and Lb > 0.0):
                notes.append("L_b <= 0 at this frequency (past SRF): "
                             "k undefined")

            if (math.isfinite(La) and La > 0.0
                    and math.isfinite(Lb) and Lb > 0.0 and math.isfinite(M)):
                k = M / math.sqrt(La * Lb)
            else:
                k = float("nan")
            if math.isfinite(k) and abs(k) > 1.0:
                notes.append("|k| > 1: check the input S-parameters")

            m_over_la = (M / La if (math.isfinite(M) and math.isfinite(La)
                                    and La != 0.0) else float("nan"))
            m_over_lb = (M / Lb if (math.isfinite(M) and math.isfinite(Lb)
                                    and Lb != 0.0) else float("nan"))

            pairs.append(PairCoupling(
                name_a=names[a], name_b=names[b], Z_ab=z_ab,
                M_henry=M, C_c_farad=C_c, k=k,
                M_over_La=m_over_la, M_over_Lb=m_over_lb,
                M_over_La_dB=_ratio_db(m_over_la),
                M_over_Lb_dB=_ratio_db(m_over_lb),
                notes=notes,
            ))

    if G < 2:
        recip = 0.0
    else:
        # Off-diagonal entries only, and only the finite ones: a measurement
        # port with no return path NaNs its whole row and column, and letting
        # that poison the metric would report `nan` for a matrix whose other
        # pairs are perfectly reciprocal.
        off = ~np.eye(G, dtype=bool)
        mag = np.abs(Zk)
        delta = np.abs(Zk - Zk.T)
        usable = off & np.isfinite(mag) & np.isfinite(delta)
        scale = float(np.max(mag[usable])) if np.any(usable) else 0.0
        if scale > 0.0 and math.isfinite(scale):
            recip = float(np.max(delta[usable])) / scale
        else:
            recip = 0.0

    return CouplingResult(freq_hz=f, Z_matrix=Zk, names=names,
                          ports=ports, pairs=pairs,
                          reciprocity_error=recip)


# ============================================================================
# Broadband fitting
# ============================================================================

@dataclass
class InductorFit:
    L_henry: float
    R_dc_ohm: float
    R_ac_ohm_per_sqrtHz: float    # coefficient on sqrt(f) skin-effect term
    Q_at_center: float
    SRF_hz: float                 # NaN -- pure-inductor model has no SRF
    rmse_ohm: float
    f_min_hz: float
    f_max_hz: float


@dataclass
class CapacitorFit:
    C_farad: float
    R_esr_ohm: float
    L_esl_henry: float
    SRF_hz: float                 # NaN if outside band
    rmse_ohm: float
    f_min_hz: float
    f_max_hz: float


def _select_band(freqs: np.ndarray, Z: np.ndarray, f_min: float, f_max: float):
    mask = (freqs >= f_min) & (freqs <= f_max)
    if mask.sum() < 3:
        raise ValueError(
            f"Band [{f_min:.4g}, {f_max:.4g}] Hz contains "
            f"only {int(mask.sum())} points; need >= 3"
        )
    return freqs[mask], Z[mask]


def _scaled_lstsq(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Linear least-squares with per-column scaling to reduce condition number.
    Necessary when columns have wildly different magnitudes
    (e.g. omega ~ 1e10 vs 1/omega ~ 1e-10 in capacitor fit).
    """
    col_norms = np.linalg.norm(A, axis=0)
    col_norms = np.where(col_norms > 0, col_norms, 1.0)
    A_s = A / col_norms
    sol_s, *_ = np.linalg.lstsq(A_s, b, rcond=None)
    return sol_s / col_norms


def fit_inductor(freqs: np.ndarray, Z: np.ndarray,
                 f_min: float, f_max: float) -> InductorFit:
    """
    Fit Z(f) = R_dc + R_ac*sqrt(f) + j*2*pi*f*L  (linear in [R_dc, R_ac, L]).
    Solved as one real least-squares over stacked Re/Im rows.
    """
    f, z = _select_band(freqs, Z, f_min, f_max)
    omega = 2.0 * np.pi * f
    A_re = np.column_stack([np.ones_like(f), np.sqrt(f), np.zeros_like(f)])
    A_im = np.column_stack([np.zeros_like(f), np.zeros_like(f), omega])
    A = np.vstack([A_re, A_im])
    b = np.concatenate([z.real, z.imag])
    sol = _scaled_lstsq(A, b)
    R_dc, R_ac, L = float(sol[0]), float(sol[1]), float(sol[2])
    z_fit = (R_dc + R_ac * np.sqrt(f)) + 1j * omega * L
    rmse = float(np.sqrt(np.mean(np.abs(z - z_fit) ** 2)))
    f_center = math.sqrt(max(f_min, 1e-30) * max(f_max, 1e-30))
    omega_c = 2.0 * math.pi * f_center
    R_at_c = R_dc + R_ac * math.sqrt(f_center)
    Q = (omega_c * L) / R_at_c if R_at_c > 0 else float("nan")
    return InductorFit(
        L_henry=L, R_dc_ohm=R_dc, R_ac_ohm_per_sqrtHz=R_ac,
        Q_at_center=Q, SRF_hz=float("nan"),
        rmse_ohm=rmse, f_min_hz=float(f.min()), f_max_hz=float(f.max()),
    )


def fit_capacitor(freqs: np.ndarray, Z: np.ndarray,
                  f_min: float, f_max: float) -> CapacitorFit:
    """
    Fit Z(f) = R_esr + j*2*pi*f*L_esl + 1/(j*2*pi*f*C)
            = R_esr + j*(omega*L_esl - 1/(omega*C))
    Linear in [R_esr, L_esl, 1/C].
    """
    f, z = _select_band(freqs, Z, f_min, f_max)
    omega = 2.0 * np.pi * f
    A_re = np.column_stack([np.ones_like(f), np.zeros_like(f), np.zeros_like(f)])
    A_im = np.column_stack([np.zeros_like(f), omega, -1.0 / omega])
    A = np.vstack([A_re, A_im])
    b = np.concatenate([z.real, z.imag])
    sol = _scaled_lstsq(A, b)
    R_esr, L_esl, inv_C = float(sol[0]), float(sol[1]), float(sol[2])
    C = 1.0 / inv_C if inv_C > 0 else float("nan")
    if not math.isnan(C):
        z_fit = R_esr + 1j * (omega * L_esl - 1.0 / (omega * C))
    else:
        z_fit = R_esr + 1j * omega * L_esl
    rmse = float(np.sqrt(np.mean(np.abs(z - z_fit) ** 2)))
    if L_esl > 0 and not math.isnan(C) and C > 0:
        srf = 1.0 / (2.0 * math.pi * math.sqrt(L_esl * C))
        if not (f_min <= srf <= f_max):
            srf = float("nan")
    else:
        srf = float("nan")
    return CapacitorFit(
        C_farad=C, R_esr_ohm=R_esr, L_esl_henry=L_esl,
        SRF_hz=srf, rmse_ohm=rmse,
        f_min_hz=float(f.min()), f_max_hz=float(f.max()),
    )


def fit_auto(freqs: np.ndarray, Z: np.ndarray,
             f_min: float, f_max: float
             ) -> tuple[str, Union[InductorFit, CapacitorFit]]:
    """
    Pick inductor vs capacitor model based on Im(Z) sign distribution; if mixed,
    fit both and return the lower-RMSE one.
    """
    f_band, z_band = _select_band(freqs, Z, f_min, f_max)
    pos_frac = float(np.mean(z_band.imag > 0))
    if pos_frac > 0.85:
        return "inductor", fit_inductor(freqs, Z, f_min, f_max)
    if pos_frac < 0.15:
        return "capacitor", fit_capacitor(freqs, Z, f_min, f_max)
    ind = fit_inductor(freqs, Z, f_min, f_max)
    cap = fit_capacitor(freqs, Z, f_min, f_max)
    if ind.rmse_ohm <= cap.rmse_ohm:
        return "inductor", ind
    return "capacitor", cap


# ============================================================================
# Convenience evaluator for full Z(f) at many frequencies (for fit overlay etc.)
# ============================================================================

def eval_inductor_model(fit: InductorFit, freqs: np.ndarray) -> np.ndarray:
    omega = 2.0 * np.pi * freqs
    return (fit.R_dc_ohm + fit.R_ac_ohm_per_sqrtHz * np.sqrt(freqs)) \
        + 1j * omega * fit.L_henry


def eval_capacitor_model(fit: CapacitorFit, freqs: np.ndarray) -> np.ndarray:
    omega = 2.0 * np.pi * freqs
    Z = np.full(freqs.shape, fit.R_esr_ohm, dtype=complex)
    if fit.L_esl_henry != 0:
        Z = Z + 1j * omega * fit.L_esl_henry
    if not math.isnan(fit.C_farad) and fit.C_farad != 0:
        Z = Z + 1.0 / (1j * omega * fit.C_farad)
    return Z

# ============================================================================
# The facade's write-through
# ============================================================================
#
# An attribute WRITE on this module is forwarded to whichever split module
# defines the name, as well as being applied here.
#
# `mock.patch.object(pkg_rlc_core, "MAX_SNIFF_NPORTS", 2)` -- which
# tests/test_large_files.py and tests/test_parse_diagnostics.py both do, and
# which is how the port-count cap and the diagnosis line budget are tested at
# all -- has to keep changing what the PARSER sees.  A plain re-export cannot:
# the parser reads its own module global, so a patch applied only here would
# rebind this module's copy and silently turn those tests into assertions
# about nothing.  Reads need no help; the `from ... import ...` block above
# has already bound every name in this namespace.
#
# Dunders are left alone: `hasattr(mod, "__name__")` is true of every module,
# and writing one through would rename the module it was forwarded to.

_SPLIT_MODULES = (_pkg_rlc_touchstone,)


class _CoreFacade(_types.ModuleType):
    """`pkg_rlc_core`'s own type -- see the note above."""

    def __setattr__(self, name, value):
        if not name.startswith("__"):
            for _mod in _SPLIT_MODULES:
                if hasattr(_mod, name):
                    setattr(_mod, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if not name.startswith("__"):
            for _mod in _SPLIT_MODULES:
                if hasattr(_mod, name):
                    delattr(_mod, name)
        super().__delattr__(name)


_sys.modules[__name__].__class__ = _CoreFacade

