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

# Frequencies per chunk in the batched linear algebra (s_to_y / y_to_s /
# compute_z_matrix).  Everything is stacked into (F, N, N) arrays and handed to
# one np.linalg.solve so numpy loops in C instead of Python; the chunk keeps
# peak memory proportional to the batch, not to the file, which is what lets a
# 153-port x 5000-frequency EM export run at all.  Mirrors reduce_snp.py's
# --batch default.  numpy dispatches a stacked solve to the same LAPACK routine
# per matrix as the 2-D call, so batching it is bit-exact -- see
# tests/test_golden_regression.py, which pins that.  (Stacked *matmul* is a
# different story; see the note in compute_z_matrix.)
COMPUTE_BATCH = 256

# ...but the chunk is also capped so its working arrays stay roughly L2-sized.
# A full 256-frequency chunk of a 60-port file is ~24 MB per temporary, and
# every chunk then faults in fresh pages: measured 25% SLOWER than the old
# per-frequency loop, which kept one 28 KB buffer hot.  Below ~5 ports the cap
# never binds and the batch wins 15-25x; above ~100 ports the sweep is
# LAPACK-bound and a batch of 1 costs nothing.  Bit-exactness does not depend on
# the chunk size (verified over batches 1..1e5 against the pre-batch code).
COMPUTE_CHUNK_BYTES = 256 * 1024


def _freq_batch(n: int) -> int:
    """Frequencies per chunk for an (F, n, n) complex sweep."""
    per_freq = max(1, n * n * 16)          # complex128
    return max(1, min(COMPUTE_BATCH, COMPUTE_CHUNK_BYTES // per_freq))

# Touchstone values are staged in a small Python list and flushed into an
# array.array('d') every this many values, so peak memory tracks the block
# size and not the file size.  See parse_touchstone.
PARSE_FLUSH_VALUES = 250_000

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

    Memory: the file is streamed line by line and the numbers land in an
    `array.array('d')` (8 bytes per value) fed by a small bounded staging list;
    the result is a zero-copy `np.frombuffer` view of that buffer.  Appending
    every value to one big Python list instead costs ~32 bytes per boxed float
    plus a full second copy at `np.asarray()` time -- roughly 5x the payload,
    which is the difference between opening and not opening an unreduced
    153-port EM export.  Same technique as `reduce_snp.py` (deliberately
    duplicated: that script must stay standalone).

    Note: `np.fromstring(sep=' ')` looks like the obvious fast path but is ~9x
    SLOWER on numpy 2.x (deprecated, unoptimized) and truncates silently on a
    bad token.  Don't switch to it.
    """
    path = Path(filepath)

    opt_line: str | None = None
    port_names: dict[int, str] = {}
    warnings_out: list[str] = []

    store = array.array("d")
    staging: list[float] = []
    stage = staging.extend

    # Lines split off a comment / option line by an exotic break character; see
    # the note below.  Kept as a small stack so the ordinary path stays a plain
    # `for raw in fh` with no per-line splitlines() cost.
    pending: list[str] = []

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        line_iter = iter(fh)
        while True:
            if pending:
                line = pending.pop().strip()
            else:
                raw = next(line_iter, None)
                if raw is None:
                    break
                line = raw.strip()
            if not line:
                continue
            if line[0] in "#!":
                # str.splitlines() -- what this parser used before it streamed
                # the file -- also breaks on \x0b \x0c \x1c-\x1e \x85
                #  , while iterating a text-mode file breaks only on \n.
                # A comment or option line terminated by a form feed (older EDA
                # dumps page-break their headers) would otherwise swallow the
                # data record that follows it, dropping frequency points with
                # no warning.  Only comment/option lines need the check: every
                # one of those characters is whitespace to str.split(), so a
                # data line containing one still tokenises correctly.
                head, *tail = line.splitlines()
                if tail:
                    pending.extend(reversed(tail))
                    line = head
                if line.startswith("#"):
                    if opt_line is None:
                        opt_line = line[1:].strip()
                    continue
                m = _PORT_NAME_RE.match(line)
                if m:
                    port_names[int(m.group(1))] = m.group(2).strip()
                continue
            if "!" in line:  # strip mid-line comment
                line, _, comment = line.partition("!")
                rest = comment.splitlines()
                if len(rest) > 1:
                    pending.extend(reversed(rest[1:]))
            toks = line.split()
            try:
                # The comprehension is fully evaluated before anything is
                # staged, so the token-by-token retry below cannot double-count
                # the values that preceded the bad token.
                stage([float(t) for t in toks])
            except ValueError:
                for tok in toks:
                    try:
                        staging.append(float(tok))
                    except ValueError:
                        warnings_out.append(f"Skipping unparseable token '{tok}'")
            if len(staging) >= PARSE_FLUSH_VALUES:
                store.fromlist(staging)
                del staging[:]
    if staging:
        store.fromlist(staging)
        del staging[:]

    # Zero-copy view over the array('d') buffer; numpy keeps `store` alive.
    # `store` must not be appended to past this point.
    data_values = np.frombuffer(store, dtype=np.float64)

    if opt_line is None:
        warnings_out.append("No option line ('#') found; assuming '# GHZ S MA R 50'")
        freq_unit, ptype, fmt, z0 = "GHZ", "S", "MA", DEFAULT_Z0
    else:
        freq_unit, ptype, fmt, z0 = _decode_options(opt_line)

    if ptype != "S":
        warnings_out.append(
            f"Parameter type '{ptype}' is not S; treating data as S-parameters anyway."
        )

    nports = force_nports if force_nports is not None else _sniff_nports(data_values, warnings_out)
    record_size = 1 + 2 * nports * nports
    n_values = int(data_values.size)
    if n_values % record_size != 0:
        raise ValueError(
            f"Token count {n_values} not divisible by record size "
            f"{record_size} (for N={nports}); file likely corrupt or wrong N."
        )

    arr = data_values.reshape(-1, record_size)
    freqs_raw = arr[:, 0]
    # Stays a strided view: splitting the trailing axis of a row-contiguous
    # slice never needs a copy.
    body = arr[:, 1:].reshape(-1, nports, nports, 2)

    if fmt not in ("RI", "MA", "DB"):
        raise ValueError(f"Unknown data format: {fmt}")

    # Fill s.real / s.imag in place.  `body[..., 0] + 1j * body[..., 1]` is
    # shorter but allocates two full-size complex temporaries, which doubles
    # peak memory on a multi-GB file for no reason.  For MA/DB the magnitude is
    # applied with an in-place complex *= real (numpy buffers the cast in
    # chunks), which is bit-for-bit the same arithmetic as the old
    # `mag * (cos + 1j*sin)` -- verified byte-identical, signed zeros included.
    #
    # RI adds 0.0 rather than assigning straight through.  A plain assignment
    # keeps a "-0" in the file as -0.0, while the old complex expression
    # normalised it to +0.0 ((-0.0) + (+0.0) == +0.0 in IEEE round-to-nearest),
    # and real EDA exports do write '-0.000000e+00'.  np.testing.assert_array_
    # equal cannot see the difference (-0.0 == 0.0), so the golden regression
    # would not have caught the drift.  Measured cost of the fused add: +2%
    # on a 20M-value array, still 4x faster than the old expression.
    #
    # This normalises EVERY signed zero to +0.0, which matches the old
    # expression in all but one corner: a record with BOTH parts written as
    # "-0" used to come out with a -0.0 real part, because Re(1j * -0.0) is
    # -0.0 and (-0.0) + (-0.0) == -0.0.  That asymmetry (the sign of the real
    # zero depending on the sign of the imaginary zero) is not worth
    # reproducing; +0.0 in every case is the sane reading of "this entry is
    # zero".  See tests/test_core.py:TestParserSignedZero.
    s = np.empty(body.shape[:-1], dtype=complex)
    if fmt == "RI":
        np.add(body[..., 0], 0.0, out=s.real)
        np.add(body[..., 1], 0.0, out=s.imag)
    else:
        ang = np.deg2rad(body[..., 1])
        cos_a = np.cos(ang)
        sin_a = np.sin(ang)
        del ang
        s.real = cos_a
        del cos_a
        s.imag = sin_a
        del sin_a
        if fmt == "MA":
            s *= body[..., 0]
        else:  # DB
            s *= 10.0 ** (body[..., 0] / 20.0)

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


def _sniff_nports(values: np.ndarray, warnings_out: list[str]) -> int:
    """Find smallest N such that token-count fits and freqs are strictly increasing."""
    T = int(values.size)
    if T == 0:
        raise ValueError("No data tokens found in file")
    candidates: list[int] = []
    for n in range(1, MAX_SNIFF_NPORTS + 1):
        rec = 1 + 2 * n * n
        if T % rec != 0:
            continue
        freqs = values[0::rec]
        ok = freqs.size < 2 or bool(np.all(np.diff(freqs) > 0))
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
        6:1:14 ground          <- the port field takes a range

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
    them).  `lumped_between` deliberately does NOT take a range on the right --
    an N-to-M lumped element is ambiguous (star? mesh?), so its partner must be
    a single port.  A range on its LEFT is unambiguous (one element from each
    listed port to the one partner) and is allowed.

    Blank lines and lines starting with '#' are ignored.
    """
    ts = TerminationSet()
    for ln_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        try:
            ports = parse_port_range(parts[0])  # 1-based; '3' -> [3]
        except ValueError as e:
            raise ValueError(
                f"Line {ln_no}: first token must be a port number or range "
                f"(e.g. '3', '1,2', '5:1:12', '6-14'), got '{parts[0]}': {e}"
            ) from e
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
        elif kind == "short_to":
            if not rest:
                raise ValueError(f"Line {ln_no}: short_to needs a partner port")
            try:
                others = parse_port_range(rest[0])
            except ValueError as e:
                raise ValueError(
                    f"Line {ln_no}: short_to partner must be a port number or "
                    f"range, got '{rest[0]}': {e}"
                ) from e
            if not others:
                raise ValueError(
                    f"Line {ln_no}: short_to partner '{rest[0]}' selects no ports"
                )
            # Chain the whole node: (p0,p1), (p1,p2), ...  One port on each side
            # reduces to the historical single ShortPair(port-1, other-1).
            chain = list(ports) + [p for p in others if p not in ports]
            for a, b in zip(chain, chain[1:]):
                ts.couplings.append(ShortPair(a - 1, b - 1))
        elif kind == "lumped_to_gnd":
            params = parse_kv_rlc_params(rest)
            y = y_series_rlc(**params)   # shared: a pure function of frequency
            for port in ports:
                ts.per_port[port - 1] = LumpedToGnd(y)
        elif kind == "lumped_between":
            if not rest:
                raise ValueError(f"Line {ln_no}: lumped_between needs a partner port")
            try:
                others = parse_port_range(rest[0])
            except ValueError as e:
                raise ValueError(
                    f"Line {ln_no}: lumped_between partner must be a single "
                    f"port number, got '{rest[0]}': {e}"
                ) from e
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
                ts.couplings.append(LumpedBetween(port - 1, other - 1, y))
        else:
            raise ValueError(f"Line {ln_no}: unknown termination kind '{kind}'")
    return ts


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
                    except np.linalg.LinAlgError:
                        X[i], *_ = np.linalg.lstsq(A_oo, B_ok,
                                                   rcond=SCHUR_LSTSQ_RCOND)
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
                Zmat[k, 0, 0] = 1.0 / y_eff
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
# Display helpers
# ============================================================================

# (exponent, prefix) pairs covering 1e-15 .. 1e12. Note 'u' is used in place of
# 'µ' because Tk Text in some Windows fonts mis-renders the multibyte char and
# breaks column alignment.
_SI_PREFIXES = [
    (-15, "f"), (-12, "p"), (-9, "n"), (-6, "u"), (-3, "m"),
    (0, ""), (3, "k"), (6, "M"), (9, "G"), (12, "T"),
]


def format_si(value: float, unit: str = "", sig: int = 3) -> str:
    """
    Format a number with an SI prefix and `sig` significant digits.

    Examples:
        format_si(0.000345, "H")   -> "345 uH"     (because 3.45e-4 H = 345 uH)
        format_si(345e-12, "H")    -> "345 pH"
        format_si(-1.234e-9, "H")  -> "-1.23 nH"
        format_si(0.0, "Ω")        -> "0.00 Ω"
        format_si(float('nan'))    -> "nan"
        format_si(float('inf'))    -> "inf"

    The chosen prefix is the largest one whose scaled value has |x| >= 1
    (with 'f' as the floor). Sig-fig rounding is applied to the scaled
    value, so the textual length is bounded.
    """
    if not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if value == 0.0:
        return f"{0.0:.{sig - 1}f}" + (f" {unit}" if unit else "")

    abs_v = abs(value)
    log10 = math.log10(abs_v)
    # Pick the largest prefix exponent <= log10, clamped to the table range.
    chosen = _SI_PREFIXES[0]
    for exp, pfx in _SI_PREFIXES:
        if log10 >= exp:
            chosen = (exp, pfx)
        else:
            break
    exp, pfx = chosen
    scaled = value / (10 ** exp)
    # `sig` significant digits via %g, then strip a trailing '.' if any.
    text = f"{scaled:.{sig}g}"
    suffix = pfx + unit
    return f"{text} {suffix}" if suffix else text
