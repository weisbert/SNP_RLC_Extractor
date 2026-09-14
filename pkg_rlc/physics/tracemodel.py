"""
pkg_rlc/physics/tracemodel.py -- a routed trace as a pi model, read EXACTLY.

This module answers one question: "what lumped circuit IS this trace, and what
are its element values?"  It is a LAYER OVER `compute_z_matrix`, not a mode --
the same shape as `pkg_rlc.physics.attrib`.  It adds no numerics to the solve
path, declares no new mode integer, and `compute_z_matrix` never learns it
exists.  It imports `pkg_rlc.physics.core` ONLY (acyclic), like `attrib` and
`compose`.

WHY THERE IS NO FIT HERE
------------------------
A two-port's admittance matrix and a pi circuit are THE SAME OBJECT, not an
approximation of one another.  For any two-port with short-circuit matrix Y:

    Y_series   = -Ym                 Ym = (Y12 + Y21) / 2
    Y_shunt_in  = Y11 + Ym
    Y_shunt_out = Y22 + Ym

is an identity -- substitute the pi's own Y and the three expressions return
its three branches unchanged.  So the elements come out EXACT at every
frequency, with no least-squares, no symmetry assumption and no second
measurement.  `tests/test_tracemodel.py::TestPiIsExact` builds a deliberately
ASYMMETRIC pi (Cp_in 40 fF, Cp_out 26.4 fF) and recovers all six element
values to 0 relative error, while the two-measurement symmetric fit this
module replaces misses Ls by 4.9 % and Rp by 3x on the same network.

`Zmat` from `compute_z_matrix` is the OPEN-CIRCUIT matrix, so the inverse of
its 2x2 IS the short-circuit Y this identity wants.  That is the whole bridge:
every termination, short, lumped element, merged node and multi-file link
already applied by the reduction comes along for free, because it is already
baked into the Z that is handed here.

DIFFERENTIAL COSTS NO NEW MATHEMATICS
-------------------------------------
The probe model already ties a '+' side and a '-' side into one measurement
port, so declaring

    mport IN  = 1 / 2        mport OUT = 3 / 4

makes `compute_z_matrix` return the 2x2 DIFFERENTIAL open-circuit matrix
directly, and the identity above then reads off the differential pi.  There is
no mixed-mode transform on this path and there is no fifth port to special-case
either: a file with a separate GND pin simply adds `gnd 5` to the same spec.

WHAT THE DIFFERENTIAL PI ASSUMES, AND WHY `mode_conversion_ratio` EXISTS
-----------------------------------------------------------------------
A probed differential port carries i_plus = -i_minus, so its COMMON-MODE
current is zero: the pi extracted above is the differential circuit with
common mode OPEN at both ends.  That is the right boundary condition for a
pair driven differentially into a high-impedance receiver, and it is exact --
but it is only the WHOLE story when the two lines are symmetric.  An
asymmetric pair converts differential energy into common mode, and a
differential two-port cannot represent where it went.

So `mode_conversion_ratio` measures it rather than leaving it unsaid: it takes
the 4x4 SINGLE-ENDED open-circuit matrix of the same four ports, inverts it,
transforms to mixed mode and returns max|Ydc| / max|Ydd|.  Zero means the pair
is symmetric and the differential pi is complete; the report prints the number
either way.  This module reports the assumption instead of burying it for the
same reason `attrib` exists -- an unstated "everything else is OPEN" once moved
a real answer by 6.07 dB with nothing on screen saying so.

SIGNS
-----
R / L / C / Q carry their physical sign, exactly as `extract_rlc_at_freq` does,
and for the same reason: a branch past its SRF has Im(Z) < 0 and reads as a
negative L and a positive C.  Nothing here clips, abs()es or hides a sign.
`_branch` uses the identical four expressions, and
`tests/test_tracemodel.py::TestBranchMatchesExtractRlc` pins that it agrees
with `extract_rlc_at_freq` on the same complex number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pkg_rlc.physics import core

__all__ = [
    "Branch", "PiModel",
    "extract_pi", "extract_pi_at", "mode_conversion_ratio", "lumped_drift",
    "LUMPED_DRIFT_WARN",
]


# A branch whose element value moves more than this between the marker
# frequency and the bottom of the sweep is not behaving as one lumped element
# over that span.  Advisory only -- the values are still returned and printed;
# what the threshold buys is that the report SAYS so.  0.10 is a tenth, chosen
# to sit well above the 2.5 % that a well-behaved on-chip line drifts across a
# 50x span and well below the order-of-magnitude a distributed line shows.
LUMPED_DRIFT_WARN = 0.10


@dataclass(frozen=True)
class Branch:
    """
    One of the pi's three branches, read four ways.

    Z is the branch impedance; R / L / C / Q are the SAME complex number seen
    through the four expressions `extract_rlc_at_freq` uses.  L and C are
    mutually exclusive readings -- Im(Z) > 0 means read L (C comes out
    negative), Im(Z) < 0 means read C (L comes out negative) -- and `reads_as`
    says which one, so a caller never has to re-derive it from a sign.

    |Q| << 1 means the branch is a resistor whose reactive residue is too small
    to interpret; `is_resistive` is that verdict, and it is the one thing a
    reader most often gets wrong when handed a bare C value.
    """
    name: str
    Z: complex
    R_ohm: float
    L_henry: float
    C_farad: float
    Q: float

    @property
    def reads_as(self) -> str:
        """'L', 'C', or 'R' when the reactance is too small to mean anything."""
        if not math.isfinite(self.Q):
            return "R"
        if abs(self.Q) < 1e-2:
            return "R"
        return "L" if self.Z.imag > 0.0 else "C"

    @property
    def is_resistive(self) -> bool:
        return self.reads_as == "R"


@dataclass(frozen=True)
class PiModel:
    """
    The exact pi of one two-port, at ONE frequency.

    `differential` records which kind of measurement port produced it, because
    the two answer different questions and the shunt branch is a DIFFERENT
    capacitance in each.

    Single-ended: the shunt is each end's capacitance to the reference, and
    that is the whole of it.

    Differential: the shunt is the capacitance ACROSS THE PAIR at that end --
    the line-to-line term plus the two line-to-reference terms in SERIES
    through the reference, C_diff = C12 + C1g/2.  That is the loading a
    differential driver actually sees, which is why it is the primary number.
    It is HALF the per-line odd-mode capacitance an EM tool usually quotes
    (C_odd = C1g + 2*C12 = 2*C_diff), so `odd_mode_farads` below carries the
    other convention rather than leaving a reader to wonder which one this is.
    Measured both ways in `tests/test_tracemodel.py::TestDifferentialPi`.
    """
    freq_hz: float
    requested_hz: float
    in_name: str
    out_name: str
    differential: bool
    series: Branch
    shunt_in: Branch
    shunt_out: Branch
    reciprocity: float
    warnings: list[str] = field(default_factory=list)

    @property
    def branches(self) -> tuple[Branch, Branch, Branch]:
        return (self.series, self.shunt_in, self.shunt_out)

    def odd_mode_farads(self, branch: Branch) -> float:
        """
        One shunt branch's capacitance in the PER-LINE odd-mode convention.

        C_odd = 2 * C_diff, because the pair's across-capacitance is two
        per-line capacitances in series.  NaN on a single-ended model, where
        the convention does not apply and doubling the number would invent a
        second line that is not there.
        """
        if not self.differential:
            return float("nan")
        return 2.0 * branch.C_farad


def _branch(name: str, Y: complex, omega: float,
            warnings_out: list[str]) -> Branch:
    """
    One branch admittance -> its impedance, read four ways.

    The four expressions are `extract_rlc_at_freq`'s, character for character,
    so the two surfaces cannot drift.  A branch with EXACTLY zero admittance is
    an open circuit: R is +inf and there is no L, C or Q to report, which is
    said in a warning rather than printed as a plausible 0 H.
    """
    if Y == 0:
        warnings_out.append(
            f"Branch '{name}' has zero admittance -- it is an open circuit. "
            "R is reported as inf and L / C / Q as nan.")
        nan = float("nan")
        return Branch(name, complex(float("inf"), 0.0),
                      float("inf"), nan, nan, nan)

    z = complex(1.0 / Y)
    r = z.real
    im = z.imag
    L = im / omega if omega != 0.0 else float("nan")
    C = -1.0 / (omega * im) if (omega != 0.0 and im != 0.0) else float("nan")
    Q = im / r if r != 0.0 else float("nan")
    return Branch(name, z, r, L, C, Q)


def extract_pi(Z2: np.ndarray, freq_hz: float,
               in_name: str = "IN", out_name: str = "OUT",
               differential: bool = False,
               requested_hz: float | None = None) -> PiModel:
    """
    The exact pi of one 2x2 open-circuit impedance matrix.

    `Z2` is `Zmat[k]` for one frequency -- two measurement ports, all others
    open, which is what `compute_z_matrix` returns.  Port 0 is the IN end and
    port 1 the OUT end; which physical ports those are was decided when the
    measurement ports were declared, and this function neither knows nor needs
    to know.

    The mutual term is SYMMETRISED -- Ym = (Y12 + Y21) / 2 -- because a pi has
    one series branch and cannot represent a non-reciprocal two-port at all.
    The asymmetry is not swallowed: `PiModel.reciprocity` carries
    |Y12 - Y21| / max(|Y12|, |Y21|) and anything past `RECIPROCITY_WARN`
    (1e-3, the solver's own threshold) adds a warning naming the frequency.

    A singular Z2 NaNs this frequency and says which one, rather than aborting:
    the same rule the solver follows for a lumped L to ground at w == 0.
    """
    warnings_out: list[str] = []
    omega = 2.0 * math.pi * float(freq_hz)
    Z2 = np.asarray(Z2, dtype=complex)
    if Z2.shape != (2, 2):
        raise ValueError(
            f"extract_pi needs a 2x2 open-circuit Z matrix, got {Z2.shape}. "
            "Declare exactly two measurement ports -- the IN end and the OUT "
            "end of the trace.")

    req = float(freq_hz if requested_hz is None else requested_hz)

    def _dead(why: str) -> PiModel:
        """Every branch nan, the reason named, the sweep unharmed."""
        warnings_out.append(why)
        nan = float("nan")
        return PiModel(
            freq_hz=float(freq_hz), requested_hz=req,
            in_name=in_name, out_name=out_name, differential=differential,
            series=Branch("series", complex(nan, nan), nan, nan, nan, nan),
            shunt_in=Branch("shunt_in", complex(nan, nan), nan, nan, nan, nan),
            shunt_out=Branch("shunt_out", complex(nan, nan),
                             nan, nan, nan, nan),
            reciprocity=nan, warnings=warnings_out)

    if not np.all(np.isfinite(Z2)):
        return _dead(
            f"The Z matrix at {core.format_freq(freq_hz)} is not finite, so "
            "no pi can be read there. A measurement port with no return path "
            "is the usual cause -- give it a '-' side or add the missing "
            "ground ports.")

    try:
        Y2 = np.linalg.inv(Z2)
    except np.linalg.LinAlgError:
        return _dead(
            f"The Z matrix at {core.format_freq(freq_hz)} is singular, so no "
            "pi exists there. This frequency is reported as nan; the rest of "
            "the sweep is unaffected.")

    y12, y21 = complex(Y2[0, 1]), complex(Y2[1, 0])
    scale = max(abs(y12), abs(y21))
    recip = abs(y12 - y21) / scale if scale > 0.0 else 0.0
    if recip > core.RECIPROCITY_WARN:
        warnings_out.append(
            f"Y12 and Y21 differ by {recip:.2e} at "
            f"{core.format_freq(freq_hz)} (threshold "
            f"{core.RECIPROCITY_WARN:.0e}). A pi has ONE series branch and "
            "cannot represent a non-reciprocal two-port, so the mutual term "
            "below is the average of the two. Check the file before reading "
            "the series branch.")

    Ym = 0.5 * (y12 + y21)
    series = _branch("series", -Ym, omega, warnings_out)
    shunt_in = _branch("shunt_in", complex(Y2[0, 0]) + Ym, omega, warnings_out)
    shunt_out = _branch("shunt_out", complex(Y2[1, 1]) + Ym, omega,
                        warnings_out)

    return PiModel(
        freq_hz=float(freq_hz), requested_hz=req,
        in_name=in_name, out_name=out_name, differential=differential,
        series=series, shunt_in=shunt_in, shunt_out=shunt_out,
        reciprocity=recip, warnings=warnings_out)


def extract_pi_at(freqs: np.ndarray, Zmat: np.ndarray, target_freq_hz: float,
                  in_name: str = "IN", out_name: str = "OUT",
                  differential: bool = False) -> PiModel:
    """
    Pick the swept point nearest `target_freq_hz` and read the pi there.

    `argmin(|freqs - target|)` is `extract_rlc_at_freq`'s own rule, verbatim,
    so a marker resolves to the same data point on both surfaces.  The
    requested frequency is carried on the model so the report can say when the
    two differ -- a request below the bottom of the sweep is answered with the
    bottom of the sweep, and the reader has to be told.
    """
    freqs = np.asarray(freqs, dtype=float)
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    Zmat = np.asarray(Zmat, dtype=complex)
    if Zmat.ndim != 3 or Zmat.shape[1:] != (2, 2):
        raise ValueError(
            f"extract_pi_at needs Zmat of shape (nfreqs, 2, 2), got "
            f"{Zmat.shape}. Declare exactly two measurement ports.")
    idx = int(np.argmin(np.abs(freqs - float(target_freq_hz))))
    return extract_pi(Zmat[idx], float(freqs[idx]), in_name, out_name,
                      differential, requested_hz=float(target_freq_hz))


# Mixed-mode transform, in the VOLTAGE/CURRENT convention rather than the
# power-wave one, because what is being transformed here is a Y matrix and not
# an S matrix.  Port order is (IN+, IN-, OUT+, OUT-).
#
#   v_d = v+ - v-          i_d = (i+ - i-) / 2
#   v_c = (v+ + v-) / 2    i_c = i+ + i-
#
# so with I = Y V, the mixed-mode matrix is Ti @ Y @ inv(Tv).  Both matrices
# are real and constant; they are built once here rather than per call.
_TV = np.array([[1.0, -1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, -1.0],
                [0.5, 0.5, 0.0, 0.0],
                [0.0, 0.0, 0.5, 0.5]], dtype=complex)
_TI = np.array([[0.5, -0.5, 0.0, 0.0],
                [0.0, 0.0, 0.5, -0.5],
                [1.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 1.0]], dtype=complex)
_TV_INV = np.linalg.inv(_TV)


def mode_conversion_ratio(Z4: np.ndarray) -> float:
    """
    How much of this pair's differential drive leaks into common mode.

    `Z4` is the 4x4 SINGLE-ENDED open-circuit matrix of (IN+, IN-, OUT+, OUT-)
    at one frequency.  Returns max|Ydc| / max|Ydd| -- 0.0 for a symmetric pair,
    growing with the imbalance.  NaN if Z4 is singular or not finite, which the
    caller reports rather than treating as zero.

    This is the VALIDITY QUALIFIER on a differential pi, not a second model:
    the pi from a probed differential port is exact under common-mode-open, and
    this number says how much of the answer that boundary condition is hiding.
    """
    Z4 = np.asarray(Z4, dtype=complex)
    if Z4.shape != (4, 4):
        raise ValueError(
            f"mode_conversion_ratio needs a 4x4 single-ended Z matrix in the "
            f"order (IN+, IN-, OUT+, OUT-), got {Z4.shape}.")
    if not np.all(np.isfinite(Z4)):
        return float("nan")
    try:
        Y4 = np.linalg.inv(Z4)
    except np.linalg.LinAlgError:
        return float("nan")
    Ymm = _TI @ Y4 @ _TV_INV
    dd = float(np.max(np.abs(Ymm[:2, :2])))
    dc = float(np.max(np.abs(Ymm[:2, 2:])))
    if dd == 0.0:
        return float("nan")
    return dc / dd


def lumped_drift(model: PiModel, reference: PiModel) -> dict[str, float]:
    """
    Relative movement of each branch's element value between two frequencies.

    The key is the branch name; the value is (x_model - x_ref) / |x_ref| on
    whichever of R / L / C that branch READS AS at the reference frequency, so
    an inductive branch is compared on L and a capacitive one on C rather than
    on whichever happened to be finite.  NaN where the reference value is zero
    or the branch reads as a bare resistor on one side and not the other.

    This is the cheap version of "is this thing lumped over my band" -- two
    points out of a sweep already solved, no extra solve, no curve.  Anything
    past `LUMPED_DRIFT_WARN` means the element is not one lumped component
    across that span and the single-frequency value should not be reused
    elsewhere in the band.
    """
    out: dict[str, float] = {}
    for b, rb in zip(model.branches, reference.branches):
        kind = rb.reads_as
        if kind == "R":
            a, r = b.R_ohm, rb.R_ohm
        elif kind == "L":
            a, r = b.L_henry, rb.L_henry
        else:
            a, r = b.C_farad, rb.C_farad
        if not (math.isfinite(a) and math.isfinite(r)) or r == 0.0:
            out[b.name] = float("nan")
        else:
            out[b.name] = (a - r) / abs(r)
    return out
