"""
pkg_rlc_core.py  --  Core computation for PKG RLC Extractor.

Touchstone parser (universal content-based, ignores extension), S<->Y
conversion, unified port-termination model, Schur-complement reduction,
single-frequency RLC extraction, and broadband fitting.

User-facing port indices are 1-based; internal computation is 0-based.
The boundary between the two is the build_terminations_mode* helpers
(input is 1-based) and the GUI/CLI layer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence, Union

import numpy as np

# ============================================================================
# Constants
# ============================================================================

DEFAULT_Z0 = 50.0
FREQ_UNIT_SCALE = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9, "THZ": 1e12}
SCHUR_LSTSQ_RCOND = 1e-15
MAX_SNIFF_NPORTS = 256


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


# ============================================================================
# Touchstone parser (universal content-based; ignores extension)
# ============================================================================

@dataclass
class TouchstoneData:
    nports: int
    freqs: np.ndarray            # Hz, shape (nfreqs,)
    s: np.ndarray                # complex, shape (nfreqs, nports, nports)
    z0: float
    port_names: list[str]        # one per port; "" if none
    source_path: str
    parser_warnings: list[str] = field(default_factory=list)


_PORT_NAME_RE = re.compile(r"!\s*[Pp]ort\s*\[?(\d+)\]?\s*[=:]\s*(.+?)\s*$")


def _decode_options(opt_line: str) -> tuple[str, str, str, float]:
    """Parse an option line body -> (freq_unit, ptype, fmt, z0)."""
    tokens = opt_line.upper().split()
    freq_unit, ptype, fmt, z0 = "GHZ", "S", "MA", DEFAULT_Z0
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in FREQ_UNIT_SCALE:
            freq_unit = t
        elif t in ("S", "Y", "Z", "H", "G"):
            ptype = t
        elif t in ("RI", "MA", "DB"):
            fmt = t
        elif t == "R" and i + 1 < len(tokens):
            try:
                z0 = float(tokens[i + 1])
                i += 1
            except ValueError:
                pass
        i += 1
    return freq_unit, ptype, fmt, z0


def parse_touchstone(filepath: str | Path,
                     force_nports: int | None = None) -> TouchstoneData:
    """
    Parse a Touchstone file regardless of extension.

    Port count is inferred from file content unless `force_nports` is given.
    """
    path = Path(filepath)
    text = path.read_text(encoding="utf-8", errors="replace")

    opt_line: str | None = None
    port_names: dict[int, str] = {}
    data_tokens: list[float] = []
    warnings_out: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if opt_line is None:
                opt_line = line[1:].strip()
            continue
        if line.startswith("!"):
            m = _PORT_NAME_RE.match(line)
            if m:
                port_names[int(m.group(1))] = m.group(2).strip()
            continue
        if "!" in line:  # strip mid-line comment
            line = line.split("!", 1)[0]
        for tok in line.split():
            try:
                data_tokens.append(float(tok))
            except ValueError:
                warnings_out.append(f"Skipping unparseable token '{tok}'")

    if opt_line is None:
        warnings_out.append("No option line ('#') found; assuming '# GHZ S MA R 50'")
        freq_unit, ptype, fmt, z0 = "GHZ", "S", "MA", DEFAULT_Z0
    else:
        freq_unit, ptype, fmt, z0 = _decode_options(opt_line)

    if ptype != "S":
        warnings_out.append(
            f"Parameter type '{ptype}' is not S; treating data as S-parameters anyway."
        )

    nports = force_nports if force_nports is not None else _sniff_nports(data_tokens, warnings_out)
    record_size = 1 + 2 * nports * nports
    if len(data_tokens) % record_size != 0:
        raise ValueError(
            f"Token count {len(data_tokens)} not divisible by record size "
            f"{record_size} (for N={nports}); file likely corrupt or wrong N."
        )

    arr = np.asarray(data_tokens, dtype=float).reshape(-1, record_size)
    freqs_raw = arr[:, 0]
    body = arr[:, 1:].reshape(-1, nports, nports, 2)

    if fmt == "RI":
        s = body[..., 0] + 1j * body[..., 1]
    elif fmt == "MA":
        mag = body[..., 0]
        ang = np.deg2rad(body[..., 1])
        s = mag * (np.cos(ang) + 1j * np.sin(ang))
    elif fmt == "DB":
        mag = 10.0 ** (body[..., 0] / 20.0)
        ang = np.deg2rad(body[..., 1])
        s = mag * (np.cos(ang) + 1j * np.sin(ang))
    else:
        raise ValueError(f"Unknown data format: {fmt}")

    # Touchstone v1 quirk: 2-port files write S11 S21 S12 S22 (column-major-ish).
    # For n>2 the layout is row-major. So only n==2 needs a transpose.
    if nports == 2:
        s = s.transpose(0, 2, 1)

    freqs = freqs_raw * FREQ_UNIT_SCALE[freq_unit]

    pn_list = [port_names.get(i + 1, "") for i in range(nports)]

    return TouchstoneData(
        nports=nports,
        freqs=freqs,
        s=s,
        z0=z0,
        port_names=pn_list,
        source_path=str(path),
        parser_warnings=warnings_out,
    )


def _sniff_nports(tokens: list[float], warnings_out: list[str]) -> int:
    """Find smallest N such that token-count fits and freqs are strictly increasing."""
    T = len(tokens)
    if T == 0:
        raise ValueError("No data tokens found in file")
    candidates: list[int] = []
    for n in range(1, MAX_SNIFF_NPORTS + 1):
        rec = 1 + 2 * n * n
        if T % rec != 0:
            continue
        freqs = tokens[0::rec]
        ok = all(freqs[i + 1] > freqs[i] for i in range(len(freqs) - 1))
        if ok:
            candidates.append(n)
            if len(candidates) >= 3:
                break
    if not candidates:
        raise ValueError(
            f"Could not infer port count from {T} tokens. "
            "Pass force_nports if you know it."
        )
    if len(candidates) > 1:
        warnings_out.append(
            f"Port count ambiguous: candidates {candidates}. Using N={candidates[0]}."
        )
    return candidates[0]


# ============================================================================
# S <-> Y conversion
# ============================================================================

def s_to_y(s: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """
    Y = y0 * (I - S) @ inv(I + S) per frequency, where y0 = 1/z0.
    Input s shape: (nfreqs, n, n). Returns same shape.
    """
    y0 = 1.0 / z0
    n = s.shape[-1]
    I = np.eye(n)
    Y = np.empty_like(s)
    for k in range(s.shape[0]):
        Sk = s[k]
        A = I + Sk
        B = I - Sk
        # Y = y0 * B @ inv(A); avoid explicit inverse:
        # Y.T = y0 * inv(A.T) @ B.T  =>  A.T @ (Y.T) = y0 * B.T
        try:
            Y[k] = y0 * np.linalg.solve(A.T, B.T).T
        except np.linalg.LinAlgError:
            Y[k] = y0 * (B @ np.linalg.pinv(A))
    return Y


def y_to_s(y: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """Inverse of s_to_y; convenient for synthesizing test fixtures."""
    n = y.shape[-1]
    I = np.eye(n)
    S = np.empty_like(y)
    for k in range(y.shape[0]):
        Yk = y[k]
        # S = (I - z0*Y) @ inv(I + z0*Y)
        A = I + z0 * Yk
        B = I - z0 * Yk
        try:
            S[k] = np.linalg.solve(A.T, B.T).T
        except np.linalg.LinAlgError:
            S[k] = B @ np.linalg.pinv(A)
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
    group: str = "A"   # "A" or "B"


@dataclass
class LumpedToGnd:
    y_func: YFunc


@dataclass
class ShortPair:
    port_i: int   # 0-based
    port_j: int   # 0-based


@dataclass
class LumpedBetween:
    port_i: int
    port_j: int
    y_func: YFunc


PortTermination = Union[Open, Ground, Vdd, Signal, LumpedToGnd]
Coupling = Union[ShortPair, LumpedBetween]


@dataclass
class TerminationSet:
    """Per-port terminations (default Open) + inter-port couplings."""
    per_port: dict[int, PortTermination] = field(default_factory=dict)
    couplings: list[Coupling] = field(default_factory=list)

    def termination_of(self, port: int) -> PortTermination:
        return self.per_port.get(port, Open())


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


# ============================================================================
# Z computation: unified termination -> Z(f)
# ============================================================================

def compute_z(Y_full: np.ndarray, freqs: np.ndarray,
              terminations: TerminationSet) -> tuple[np.ndarray, list[str]]:
    """
    Apply terminations to Y(f) and return Z(f) for the measurement defined
    by Signal-group ports.

    - One Signal group ("A"): driving-point.  Z = 1 / (1^T * Y_red * 1)
    - Two Signal groups ("A","B"): port-to-port.  Collapse to 2x2,
      Z = Z11 + Z22 - Z12 - Z21.

    Returns (Z, warnings_list); Z is shape (nfreqs,) complex.
    """
    nfreqs, n, _ = Y_full.shape
    omega = 2.0 * np.pi * freqs
    warnings_out: list[str] = []

    # --- 1. Validate signal grouping
    sig_a = [i for i in range(n)
             if isinstance(terminations.termination_of(i), Signal)
             and terminations.termination_of(i).group == "A"]
    sig_b = [i for i in range(n)
             if isinstance(terminations.termination_of(i), Signal)
             and terminations.termination_of(i).group == "B"]
    if not sig_a:
        raise ValueError("No Signal-group-A ports defined; nothing to measure.")
    has_b = bool(sig_b)
    for i in range(n):
        t = terminations.termination_of(i)
        if isinstance(t, Signal) and t.group not in ("A", "B"):
            raise ValueError(f"Signal group must be 'A' or 'B', got '{t.group}'")

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
        sig_groups = {t.group for t in terms if isinstance(t, Signal)}
        if len(sig_groups) > 1:
            raise ValueError(
                f"Ports {members} merged via short, but assigned to "
                f"conflicting signal groups {sig_groups}"
            )
        if sig_groups:
            return Signal(next(iter(sig_groups)))
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

    new_sig_a = [i for i, t in enumerate(new_terms)
                 if isinstance(t, Signal) and t.group == "A"]
    new_sig_b = [i for i, t in enumerate(new_terms)
                 if isinstance(t, Signal) and t.group == "B"]
    new_gnd = [i for i, t in enumerate(new_terms) if isinstance(t, (Ground, Vdd))]

    # --- 4. Per-frequency reduction
    Z = np.empty(nfreqs, dtype=complex)
    fallback_warnings = 0

    for k in range(nfreqs):
        Yk = Y_full[k].astype(complex, copy=True)

        # 4a. Apply lumped_between (acts on original-port indices, before merge).
        for cpl in terminations.couplings:
            if isinstance(cpl, LumpedBetween):
                yval = complex(cpl.y_func(np.array([omega[k]]))[0])
                i, j = cpl.port_i, cpl.port_j
                Yk[i, i] += yval
                Yk[j, j] += yval
                Yk[i, j] -= yval
                Yk[j, i] -= yval

        # 4b. Apply lumped_to_gnd from per-port (also on original-port indices).
        for port in range(n):
            t = terminations.termination_of(port)
            if isinstance(t, LumpedToGnd):
                Yk[port, port] += complex(t.y_func(np.array([omega[k]]))[0])

        # 4c. Merge shorted ports (sum rows + cols).
        if new_n < n:
            Y_new = np.zeros((new_n, new_n), dtype=complex)
            for i_old in range(n):
                ni = old_to_new[i_old]
                for j_old in range(n):
                    nj = old_to_new[j_old]
                    Y_new[ni, nj] += Yk[i_old, j_old]
            Yk = Y_new

        # 4d. Drop ground rows/cols.
        if new_gnd:
            keep = [i for i in range(new_n) if i not in set(new_gnd)]
            Yk = Yk[np.ix_(keep, keep)]
            remap = {old: ki for ki, old in enumerate(keep)}
            sa = [remap[i] for i in new_sig_a]
            sb = [remap[i] for i in new_sig_b]
            other_open_post = [remap[i] for i in keep if i in keep
                               and i not in new_sig_a and i not in new_sig_b]
        else:
            sa = list(new_sig_a)
            sb = list(new_sig_b)
            other_open_post = [i for i in range(new_n)
                               if i not in new_sig_a and i not in new_sig_b
                               and i not in new_gnd]

        # 4e. Schur-eliminate the remaining "open-like" ports.
        if other_open_post:
            sig_idx = sorted(sa + sb)
            kk = np.array(sig_idx, dtype=int)
            oo = np.array(other_open_post, dtype=int)
            Y_kk = Yk[np.ix_(kk, kk)]
            Y_ko = Yk[np.ix_(kk, oo)]
            Y_ok = Yk[np.ix_(oo, kk)]
            Y_oo = Yk[np.ix_(oo, oo)]
            try:
                X = np.linalg.solve(Y_oo, Y_ok)
            except np.linalg.LinAlgError:
                X, *_ = np.linalg.lstsq(Y_oo, Y_ok, rcond=SCHUR_LSTSQ_RCOND)
                if fallback_warnings < 3:
                    warnings_out.append(
                        f"Schur fallback to lstsq at freq[{k}]={freqs[k]:.4g} Hz "
                        "(Y_oo singular)"
                    )
                    fallback_warnings += 1
            Y_red = Y_kk - Y_ko @ X
            kept_pos = {old: pos for pos, old in enumerate(sig_idx)}
            sa_pos = [kept_pos[i] for i in sa]
            sb_pos = [kept_pos[i] for i in sb]
        else:
            Y_red = Yk
            sa_pos = sa
            sb_pos = sb

        # 4f. Compute Z.
        if not has_b:
            ones = np.ones(len(sa_pos), dtype=complex)
            y_eff = ones @ Y_red[np.ix_(sa_pos, sa_pos)] @ ones
            Z[k] = 1.0 / y_eff
        else:
            Y2 = np.empty((2, 2), dtype=complex)
            Y2[0, 0] = Y_red[np.ix_(sa_pos, sa_pos)].sum()
            Y2[0, 1] = Y_red[np.ix_(sa_pos, sb_pos)].sum()
            Y2[1, 0] = Y_red[np.ix_(sb_pos, sa_pos)].sum()
            Y2[1, 1] = Y_red[np.ix_(sb_pos, sb_pos)].sum()
            try:
                Z2 = np.linalg.inv(Y2)
            except np.linalg.LinAlgError:
                Z2 = np.linalg.pinv(Y2)
            Z[k] = Z2[0, 0] + Z2[1, 1] - Z2[0, 1] - Z2[1, 0]

    return Z, warnings_out


# ============================================================================
# RLC extraction (single frequency)
# ============================================================================

@dataclass
class RLCResult:
    freq_hz: float
    Z: complex
    R_ohm: float
    L_henry: float       # NaN if Im(Z) <= 0
    C_farad: float       # NaN if Im(Z) >= 0
    Q: float             # NaN if Re(Z) <= 0


def extract_rlc_at_freq(freqs: np.ndarray, Z: np.ndarray, target_freq_hz: float) -> RLCResult:
    """
    Pick the data point closest to target_freq_hz and report R, L, C, Q.
    """
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    idx = int(np.argmin(np.abs(freqs - target_freq_hz)))
    f = float(freqs[idx])
    z = complex(Z[idx])
    r = z.real
    im = z.imag
    omega = 2.0 * math.pi * f
    L = im / omega if im > 0 else float("nan")
    C = -1.0 / (omega * im) if im < 0 else float("nan")
    Q = abs(im) / r if r > 0 else float("nan")
    return RLCResult(freq_hz=f, Z=z, R_ohm=r, L_henry=L, C_farad=C, Q=Q)


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
