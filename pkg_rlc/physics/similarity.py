"""
pkg_rlc/physics/similarity.py  --  are two Touchstone files the same network
over the band they share?

THE QUESTION IT ANSWERS.  An inductor extracted to 30 GHz and the same inductor
extracted to 80 GHz: inside 0-30 GHz, is the 80 GHz file the 30 GHz one?  It
need not be -- an EM solver sizes its mesh from the highest frequency it is
asked for, so the two runs are two different discretisations of one layout --
and "overlay the two curves and squint" is not an answer anyone can write in a
review.  This module turns it into numbers, at two levels:

  * THE FILE: the largest |S_B - S_A| over every entry, per frequency, in dB.
    -40 dB is a 1% vector error.  Needs nothing but the two files.
  * WHAT WAS EXTRACTED: the same port setup applied to both, and the signed
    relative difference of L, Q and R per frequency.  That is what a designer
    of the inductor actually signs off on.

THE BAND is the OVERLAP of the two sweeps, never more: a value outside a file's
span is not a measurement of anything, so nothing here extrapolates.

THE GRID is the COARSER file's points inside the overlap, and the finer file is
interpolated onto it.  The other way round -- what `compose.align_frequencies`
does, because a composition wants every point it can get -- would chord the
coarse file across its own gaps, and that chord error shows up here as a
DIFFERENCE between the files that neither file contains.  Identical grids are
detected with a relative tolerance and not interpolated at all.

Imports `pkg_rlc.physics.core` and `pkg_rlc.physics.compose` only (both L0,
acyclic).  No Tk, no matplotlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from pkg_rlc.physics.compose import interpolate_s
from pkg_rlc.physics.core import format_freq, s_to_y, y_to_s

__all__ = [
    "SimilarityError", "CommonAxis", "SCompare", "ZCompare",
    "common_axis", "compare_s", "compare_z", "err_db",
    "DEFAULT_S_LIMIT_DB", "DEFAULT_L_LIMIT_PCT", "DEFAULT_Q_LIMIT_PCT",
    "NEAR_ZERO_FRAC", "GRID_RTOL",
]

#: The default "same" limits.  Defaults only -- the window lets the reader set
#: their own, because how close is close enough is a property of the design
#: margin, not of the data.  -40 dB is a 1% vector error on S.
DEFAULT_S_LIMIT_DB = -40.0
DEFAULT_L_LIMIT_PCT = 1.0
DEFAULT_Q_LIMIT_PCT = 5.0

#: A relative difference is meaningless where the reference crosses zero --
#: L goes through zero at the self-resonance, Q does too.  A point where
#: |ref| is below this fraction of its MEDIAN |value| in the band is EXCLUDED
#: from the worst-case, and the count is reported rather than hidden.  The
#: median, not the maximum: R climbs by four decades into a self-resonance,
#: and 1% of THAT peak threw away every ordinary low-frequency R point.
NEAR_ZERO_FRAC = 0.01

#: Two grid points closer than this, relative, are the same frequency.  A file
#: written in GHz and one in Hz never compare equal as floats.
GRID_RTOL = 1e-9

#: Floor for the dB conversion: bit-identical files give |dS| = 0.
_DB_FLOOR = 1e-15


class SimilarityError(ValueError):
    """The two inputs cannot be compared at all (no overlap, no data)."""


def err_db(x) -> np.ndarray:
    """20 log10 of a magnitude, floored so identical data reads -300 dB."""
    return 20.0 * np.log10(np.maximum(np.abs(np.asarray(x, dtype=float)),
                                      _DB_FLOOR))


# ============================================================================
# The common axis
# ============================================================================

@dataclass
class CommonAxis:
    freqs: np.ndarray            # the grid everything is compared on
    lo: float                    # the overlap, as the two sweeps give it
    hi: float
    grid_of: str                 # "A", "B" or "both" (identical grids)
    interpolated: str            # "" / "A" / "B" -- which one was moved
    idx_a: Optional[np.ndarray]  # A's own indices when A is the grid
    idx_b: Optional[np.ndarray]

    def describe(self) -> str:
        span = f"{format_freq(self.lo)} - {format_freq(self.hi)}"
        n = len(self.freqs)
        if self.grid_of == "both":
            return f"{span}, {n} points (same grid in both files)"
        return (f"{span}, {n} points on {self.grid_of}'s grid "
                f"({self.interpolated} interpolated onto it)")


def _inside(f: np.ndarray, lo: float, hi: float) -> np.ndarray:
    tol = GRID_RTOL * max(abs(lo), abs(hi), 1.0)
    return np.nonzero((f >= lo - tol) & (f <= hi + tol))[0]


def common_axis(fa: np.ndarray, fb: np.ndarray) -> CommonAxis:
    """
    The overlap of two sweeps and the grid to compare on (see the module
    docstring for why it is the COARSER one).  Raises `SimilarityError` when
    the sweeps do not overlap in at least two points.
    """
    fa = np.asarray(fa, dtype=float)
    fb = np.asarray(fb, dtype=float)
    if len(fa) == 0 or len(fb) == 0:
        raise SimilarityError("One of the files has no frequency points.")
    lo = max(fa[0], fb[0])
    hi = min(fa[-1], fb[-1])
    if not hi > lo:
        raise SimilarityError(
            f"The two sweeps do not overlap: A is {format_freq(fa[0])} - "
            f"{format_freq(fa[-1])}, B is {format_freq(fb[0])} - "
            f"{format_freq(fb[-1])}.")
    ia = _inside(fa, lo, hi)
    ib = _inside(fb, lo, hi)
    if len(ia) == len(ib) and len(ia) and np.allclose(
            fa[ia], fb[ib], rtol=GRID_RTOL, atol=0.0):
        return CommonAxis(fa[ia], lo, hi, "both", "", ia, ib)
    # Fewer points inside the overlap is the coarser grid.  A tie goes to A,
    # the reference, so B is the one that is moved.
    if len(ia) <= len(ib) and len(ia) >= 2:
        return CommonAxis(np.clip(fa[ia], lo, hi), lo, hi, "A", "B", ia, None)
    if len(ib) >= 2:
        return CommonAxis(np.clip(fb[ib], lo, hi), lo, hi, "B", "A", None, ib)
    raise SimilarityError(
        f"The overlap {format_freq(lo)} - {format_freq(hi)} holds fewer than "
        f"two points of either file.")


def _on_axis(ax: CommonAxis, which: str, f: np.ndarray, data: np.ndarray):
    """`data` (first axis = frequency) of file `which`, on the common grid."""
    idx = ax.idx_a if which == "A" else ax.idx_b
    if idx is not None:
        return data[idx]
    if data.ndim == 1:
        return (np.interp(ax.freqs, f, data.real)
                + 1j * np.interp(ax.freqs, f, data.imag))
    return interpolate_s(f, data, ax.freqs)


# ============================================================================
# Level 1 -- the file
# ============================================================================

@dataclass
class SCompare:
    axis: CommonAxis
    err_db: np.ndarray           # per frequency: max over entries of |dS|, dB
    worst_db: float
    worst_f: float
    worst_entry: tuple           # (i, j), 1-based -- what the reader types
    notes: list = field(default_factory=list)
    # EVERY entry, not just the worst one: the reader's question is "how do
    # the two MATRICES differ", and one number for a 15x15 matrix hides which
    # ports moved.  (n, n): each entry's worst |dS| over the band, in dB, and
    # the frequency it happens at.
    entry_db: Optional[np.ndarray] = None
    entry_f: Optional[np.ndarray] = None

    def ranked_entries(self) -> list:
        """[(i, j, dB, f)] 1-based, largest difference first."""
        if self.entry_db is None:
            return []
        n = self.entry_db.shape[0]
        order = np.argsort(-self.entry_db, axis=None, kind="stable")
        return [(int(k // n) + 1, int(k % n) + 1,
                 float(self.entry_db.flat[k]), float(self.entry_f.flat[k]))
                for k in order]


def compare_s(fa, sa, z0a: float, fb, sb, z0b: float) -> SCompare:
    """
    Largest |S_B - S_A| over every entry, per frequency.

    Different reference impedances are not a difference in the network: B is
    renormalised to A's Z0 through Y (which does not depend on it) before
    anything is subtracted, and a note says so.  Different port counts ARE a
    difference in the network and are refused.
    """
    sa = np.asarray(sa)
    sb = np.asarray(sb)
    if sa.shape[1:] != sb.shape[1:]:
        raise SimilarityError(
            f"A has {sa.shape[-1]} ports and B has {sb.shape[-1]}: "
            f"S-parameters of different networks cannot be compared entry by "
            f"entry.")
    notes = []
    if not np.isclose(z0a, z0b, rtol=1e-12, atol=0.0):
        sb = y_to_s(s_to_y(sb, z0b), z0a)
        notes.append(f"B renormalised from Z0={z0b:g} to A's Z0={z0a:g} "
                     f"before comparing.")
    ax = common_axis(fa, fb)
    a = _on_axis(ax, "A", np.asarray(fa, float), sa)
    b = _on_axis(ax, "B", np.asarray(fb, float), sb)
    d = np.abs(b - a)                                  # (nf, n, n)
    per_f = d.reshape(len(ax.freqs), -1).max(axis=1)
    k = int(np.nanargmax(per_f))
    n = sa.shape[-1]
    ij = int(np.nanargmax(d[k].reshape(-1)))
    kf = np.nanargmax(d, axis=0)                       # (n, n) worst index
    return SCompare(axis=ax, err_db=err_db(per_f), worst_db=float(err_db(per_f[k])),
                    worst_f=float(ax.freqs[k]),
                    worst_entry=(ij // n + 1, ij % n + 1), notes=notes,
                    entry_db=err_db(np.nanmax(d, axis=0)),
                    entry_f=ax.freqs[kf])


# ============================================================================
# Level 2 -- what was extracted from it
# ============================================================================

@dataclass
class Worst:
    value: float                 # signed; nan when nothing was comparable
    freq: float
    excluded: int                # points dropped as near a zero of the reference


@dataclass
class ZCompare:
    axis: CommonAxis
    dl_pct: np.ndarray           # signed, per frequency; nan where excluded
    dq_pct: np.ndarray
    dr_pct: np.ndarray
    l: Worst
    q: Worst
    r: Worst


def _rel_pct(ref: np.ndarray, other: np.ndarray) -> tuple:
    """Signed 100*(other-ref)/|ref|, nan where |ref| is near its own zero."""
    ref = np.asarray(ref, dtype=float)
    other = np.asarray(other, dtype=float)
    finite = np.isfinite(ref) & np.isfinite(other)
    scale = np.median(np.abs(ref[finite])) if finite.any() else 0.0
    keep = finite & (np.abs(ref) > NEAR_ZERO_FRAC * scale)
    out = np.full(ref.shape, np.nan)
    out[keep] = 100.0 * (other[keep] - ref[keep]) / np.abs(ref[keep])
    return out, int(np.count_nonzero(finite & ~keep))


def _worst(pct: np.ndarray, freqs: np.ndarray, excluded: int) -> Worst:
    if not np.isfinite(pct).any():
        return Worst(float("nan"), float("nan"), excluded)
    k = int(np.nanargmax(np.abs(pct)))
    return Worst(float(pct[k]), float(freqs[k]), excluded)


def compare_z(fa, za, fb, zb) -> ZCompare:
    """
    L, Q and R of one extracted impedance, B against A (the reference).

    Signed, so "B reads 0.4% MORE inductance" survives to the reader; the
    worst case is the largest MAGNITUDE.  DC is dropped: L and Q are not
    defined at w = 0.
    """
    ax = common_axis(fa, fb)
    a = _on_axis(ax, "A", np.asarray(fa, float), np.asarray(za))
    b = _on_axis(ax, "B", np.asarray(fb, float), np.asarray(zb))
    f = ax.freqs
    w = 2.0 * np.pi * f
    ok = f > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        la = np.where(ok, a.imag / w, np.nan)
        lb = np.where(ok, b.imag / w, np.nan)
        qa = np.where(ok, a.imag / a.real, np.nan)
        qb = np.where(ok, b.imag / b.real, np.nan)
        ra = np.where(ok, a.real, np.nan)
        rb = np.where(ok, b.real, np.nan)
    dl, xl = _rel_pct(la, lb)
    dq, xq = _rel_pct(qa, qb)
    dr, xr = _rel_pct(ra, rb)
    return ZCompare(axis=ax, dl_pct=dl, dq_pct=dq, dr_pct=dr,
                    l=_worst(dl, f, xl), q=_worst(dq, f, xq),
                    r=_worst(dr, f, xr))
