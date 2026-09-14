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
    "Bandwidth", "BranchCorner", "transfer_function", "bandwidth_3db",
    "branch_corners", "model_band_hz", "bandwidth_table", "DEFAULT_LOADS_F",
    "LUMPED_DRIFT_WARN", "BW_DROP_DB",
]


# A branch whose element value moves more than this between the marker
# frequency and the bottom of the sweep is not behaving as one lumped element
# over that span.  Advisory only -- the values are still returned and printed;
# what the threshold buys is that the report SAYS so.  0.10 is a tenth, chosen
# to sit well above the 2.5 % that a well-behaved on-chip line drifts across a
# 50x span and well below the order-of-magnitude a distributed line shows.
LUMPED_DRIFT_WARN = 0.10

#: Above this |Q|, a branch's RESISTANCE is numerical noise and must not be
#: reasoned about.  Measured on `tests/fixtures/pi_2port.s2p`, a synthetic
#: LOSSLESS pi: its shunt reads R = 3.15 mOhm against 160 kOhm of reactance
#: (|Q| = 5e7) purely from the S-to-Y round trip, and the differential fixture
#: reads R = -2.87 nOhm -- NEGATIVE, because this tool never clips a sign.
#: Comparing either of those to itself across frequency, or dividing by it to
#: get an RC corner, produces pure nonsense: a model band of "1 MHz" for a
#: network that is a constant R, L and C by construction, and corners of
#: "5.05e+04 THz" and "-57 mHz".  1e3 keeps a real on-chip shunt (|Q| ~ 500,
#: where R is 0.2 % of the reactance and genuinely lossy) and drops those.
R_MEANINGFUL_Q = 1e3


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


# ============================================================================
# Bandwidth -- THREE different numbers, and conflating them is the trap
# ============================================================================
#
# "The bandwidth of this trace" names three unrelated quantities, and only two
# of them are properties of the trace at all:
#
#   1. MODEL BAND  (`model_band_hz`)  -- how high the extracted pi is still ONE
#      lumped pi.  Trace-only, assumption-free, and free to compute: the pi is
#      already exact at every frequency, so this is just asking where the
#      element values stop holding still.
#
#   2. BRANCH CORNERS (`branch_corners`) -- f_RL = R/(2*pi*L) on the series
#      branch and f_RC = 1/(2*pi*R*C) on each shunt, i.e. where the reactance
#      overtakes the resistance.  Trace-only.  They are what EXPLAINS the third
#      number rather than competing with it.
#
#   3. THE -3 dB BANDWIDTH (`bandwidth_3db`) -- what everyone means by the
#      word, and NOT A PROPERTY OF THE TRACE.  It is a property of trace plus
#      source plus load.  Measured on a real routed line (Rs 344 ohm, Ls
#      1.69 nH, Cp 33.2 fF/end): the answer moves from 15.31 GHz at C_load = 0
#      to 2.09 GHz at C_load = 200 fF -- a factor of 7.3 from the load alone,
#      and another 1.9x from a 200 ohm source.  Printing one number for that
#      without naming the terminations is exactly the mistake `attrib` exists
#      because of, where an unstated "everything else is OPEN" moved a real
#      answer by 6.07 dB.  So the report prints a SENSITIVITY TABLE over
#      `DEFAULT_LOADS_F`, and the caller may name its own pair.
#
# The transfer function is computed from the RAW 2x2 Z at every frequency, not
# from the pi: the pi is for understanding, and H(f) has to stay right even
# where the structure has stopped being lumped.


#: -3 dB, spelled once.  The half-power point, and the only drop this tool
#: reports, because a "-1 dB bandwidth" and a "-3 dB bandwidth" printed in the
#: same column with no label is how two numbers become one wrong one.
BW_DROP_DB = -3.0

#: The load capacitances the sensitivity table sweeps, in farads.  0 is an
#: OPEN far end -- the trace's own shunt is then the whole load, which is the
#: upper bound on any real answer and worth having as the first row.
DEFAULT_LOADS_F = (0.0, 20e-15, 50e-15, 200e-15)


@dataclass(frozen=True)
class Bandwidth:
    """
    One -3 dB answer, WITH the terminations that produced it.

    `crossed` is the honest half: when |H| never falls `BW_DROP_DB` inside the
    swept band there is no -3 dB frequency in the data, and `f_3db_hz` is NaN
    while `droop_top_db` says how far down the top of the sweep actually is.
    Extrapolating past the file would be inventing a measurement.

    `passband_ok` is False when |H| is not flat at the bottom of the sweep --
    then there is no passband for anything to be 3 dB down FROM, and the
    number, if one were printed, would be measured against a slope.
    """
    z_src_ohm: float
    c_load_farad: float
    reference_hz: float
    f_3db_hz: float
    crossed: bool
    droop_top_db: float
    droop_marker_db: float
    marker_hz: float
    passband_ok: bool
    #: The highest point of |H| relative to the reference, and where.  A
    #: series L into a load C PEAKS before it rolls off -- measured on a real
    #: fixture at +13.64 dB -- and a "-3 dB bandwidth" quoted across a peak is
    #: measured from the wrong baseline.  The report says so instead.
    peak_db: float = 0.0
    peak_hz: float = float("nan")

    @property
    def ratio_to_marker(self) -> float:
        """How many times the marker frequency the -3 dB point sits at."""
        if not (self.crossed and self.marker_hz > 0.0):
            return float("nan")
        return self.f_3db_hz / self.marker_hz


@dataclass(frozen=True)
class BranchCorner:
    """Where one branch's reactance overtakes its resistance."""
    name: str
    kind: str            # 'RL' or 'RC'
    f_hz: float


def transfer_function(Z2: np.ndarray, freqs: np.ndarray,
                      z_src_ohm: float = 1.0,
                      c_load_farad: float = 0.0) -> np.ndarray:
    """
    |V_load / V_src| as a complex array over the whole sweep.  EXACT.

    For a two-port with open-circuit matrix Z, a source impedance Zs on port 1
    and a load ZL on port 2:

        H = Z21 * ZL / [ (Z11 + Zs)(Z22 + ZL) - Z12 * Z21 ]

    Straight from the definition, per frequency, out of the SAME `Zmat` block
    the pi is read from.  Deliberately NOT computed from the pi: the pi is the
    picture, and this has to stay right at frequencies where the structure has
    stopped being one lumped pi.

    `c_load_farad == 0` means an OPEN far end, the upper bound on any real
    answer.  `z_src_ohm` is a plain resistance; 1 ohm stands in for a
    near-ideal voltage source without the infinity that a literal 0 would put
    into the arithmetic.
    """
    Z2 = np.asarray(Z2, dtype=complex)
    freqs = np.asarray(freqs, dtype=float)
    if Z2.ndim != 3 or Z2.shape[1:] != (2, 2):
        raise ValueError(
            f"transfer_function needs Zmat of shape (nfreqs, 2, 2), got "
            f"{Z2.shape}. Declare exactly two measurement ports.")
    if len(freqs) != Z2.shape[0]:
        raise ValueError(
            f"{len(freqs)} frequencies against {Z2.shape[0]} matrices.")

    omega = 2.0 * np.pi * freqs
    if c_load_farad > 0.0:
        with np.errstate(divide="ignore", invalid="ignore"):
            zl = 1.0 / (1j * omega * c_load_farad)
    else:
        # An open far end.  A large finite number rather than inf, so the
        # product below stays a number: inf * 0 in the denominator would make
        # the whole sweep NaN instead of the flat response it actually is.
        zl = np.full(freqs.shape, complex(1e18, 0.0))

    Z11, Z12 = Z2[:, 0, 0], Z2[:, 0, 1]
    Z21, Z22 = Z2[:, 1, 0], Z2[:, 1, 1]
    den = (Z11 + z_src_ohm) * (Z22 + zl) - Z12 * Z21
    with np.errstate(divide="ignore", invalid="ignore"):
        return Z21 * zl / den


def bandwidth_3db(freqs: np.ndarray, H: np.ndarray, marker_hz: float,
                  z_src_ohm: float = 1.0,
                  c_load_farad: float = 0.0) -> Bandwidth:
    """
    The first frequency where |H| is `BW_DROP_DB` below the reference.

    The REFERENCE is the bottom of the sweep, not DC -- a Touchstone file that
    starts at 0.1 GHz cannot say what DC does, and quietly calling the lowest
    swept point "DC" is how a reader ends up believing a number the file never
    measured.  The reference frequency is carried on the result so the report
    can name it.

    Linear interpolation between the two points that straddle the crossing, in
    dB against log frequency -- the grid is what it is, and reporting the
    nearest sample instead would quantise the answer to the sweep step.

    NOTHING IS EXTRAPOLATED.  If |H| never falls that far inside the band,
    `crossed` is False and `droop_top_db` reports where the top of the sweep
    actually sits.
    """
    freqs = np.asarray(freqs, dtype=float)
    H = np.asarray(H, dtype=complex)
    mag = np.abs(H)
    nan = float("nan")

    ref_hz = float(freqs[0])
    ref = float(mag[0])
    if not np.isfinite(ref) or ref <= 0.0:
        return Bandwidth(z_src_ohm, c_load_farad, ref_hz, nan, False, nan,
                         nan, float(marker_hz), False)

    with np.errstate(divide="ignore", invalid="ignore"):
        db = 20.0 * np.log10(mag / ref)

    # Is there a passband at all?  If the response has already sagged at the
    # bottom of the sweep, "3 dB down from the reference" is measured against
    # a slope rather than a plateau, and the answer would be an artefact of
    # where the file happens to start.
    low = db[:max(2, len(db) // 20)]
    passband_ok = bool(np.all(np.isfinite(low))
                       and float(np.min(low)) > BW_DROP_DB / 2.0)

    k = int(np.argmin(np.abs(freqs - float(marker_hz))))
    droop_marker = float(db[k]) if np.isfinite(db[k]) else nan
    droop_top = float(db[-1]) if np.isfinite(db[-1]) else nan

    finite = np.isfinite(db)
    if np.any(finite):
        pk = int(np.nanargmax(np.where(finite, db, -np.inf)))
        peak_db, peak_hz = float(db[pk]), float(freqs[pk])
    else:
        peak_db, peak_hz = 0.0, nan

    below = np.nonzero(np.isfinite(db) & (db <= BW_DROP_DB))[0]
    if len(below) == 0 or below[0] == 0:
        return Bandwidth(z_src_ohm, c_load_farad, ref_hz, nan, False,
                         droop_top, droop_marker, float(marker_hz),
                         passband_ok, peak_db, peak_hz)

    i = int(below[0])
    f0, f1 = freqs[i - 1], freqs[i]
    d0, d1 = db[i - 1], db[i]
    if d1 == d0 or f0 <= 0.0:
        f3 = float(f1)
    else:
        t = (BW_DROP_DB - d0) / (d1 - d0)
        f3 = float(math.exp(math.log(f0) + t * (math.log(f1) - math.log(f0))))
    return Bandwidth(z_src_ohm, c_load_farad, ref_hz, f3, True, droop_top,
                     droop_marker, float(marker_hz), passband_ok,
                     peak_db, peak_hz)


def branch_corners(model: PiModel) -> list[BranchCorner]:
    """
    Where each branch's reactance overtakes its resistance.

    `f_RL = R / (2*pi*L)` on an inductive branch and `f_RC = 1 / (2*pi*R*C)`
    on a capacitive one.  Both are read off the branch's OWN R and whichever
    of L / C it reads as, so a branch the tool calls a bare resistor
    (`|Q| < 0.01`) gets a corner too -- that is precisely the branch whose
    corner a reader wants, because it says how far above the marker the
    reactance starts to matter.

    A corner needs a MEANINGFUL resistance, and on a low-loss branch there is
    not one: `R_MEANINGFUL_Q` is the guard, and past it the corner is NaN and
    the report prints '--'.  Without it a lossless fixture produces
    `1/(2*pi*R*C)` with R = 3.15 mOhm of round-off -- "5.05e+04 THz" -- or,
    where the round-off is negative, a corner at "-57 mHz".  Both are worse
    than no number, because both look like measurements.
    """
    out: list[BranchCorner] = []
    for b in model.branches:
        r = b.R_ohm
        if (not math.isfinite(r) or r <= 0.0
                or (math.isfinite(b.Q) and abs(b.Q) > R_MEANINGFUL_Q)):
            out.append(BranchCorner(b.name, "--", float("nan")))
            continue
        if b.Z.imag > 0.0:
            L = b.L_henry
            f = r / (2.0 * math.pi * L) if (math.isfinite(L) and L > 0)                 else float("nan")
            out.append(BranchCorner(b.name, "RL", f))
        else:
            C = b.C_farad
            f = 1.0 / (2.0 * math.pi * r * C) if (math.isfinite(C) and C > 0)                 else float("nan")
            out.append(BranchCorner(b.name, "RC", f))
    return out


def model_band_hz(freqs: np.ndarray, Zmat: np.ndarray,
                  in_name: str = "IN", out_name: str = "OUT",
                  differential: bool = False,
                  tol: float = LUMPED_DRIFT_WARN) -> tuple[float, str]:
    """
    How high the extracted pi is still ONE lumped pi.  Trace-only.

    Walks the sweep, reads the pi at every point, and returns the highest
    frequency at which every branch still holds BOTH its resistance and its
    reactive element within `tol` of the bottom-of-sweep value -- plus a
    one-word reason for where it stopped:

        'sweep'      every point held; the band is limited by the FILE rather
                     than by the physics, and the answer is the top of the
                     sweep.  Not "the model fails above here" -- "the file
                     stops here";
        'drift'      R, L or C moved more than tol;
        'resonance'  a branch's reactance changed SIGN: the capacitor became
                     an inductor or the reverse.  That is not drift, it is a
                     different element, and it ends the band outright.

    WHAT THIS MUST NOT USE, AND WHY.  An earlier version keyed on
    `Branch.reads_as` and got a perfect lumped pi wrong by two decades,
    reporting 0.32 GHz for a network that is a constant R, L and C by
    construction.  `reads_as` answers "which of L or C should a reader take
    from this branch", and its `|Q| < 0.01` threshold is a DISPLAY verdict: a
    constant series R + jwL necessarily crosses it as frequency rises, because
    that is what a constant R and a constant L DO.  Element identity is the
    SIGN of Im(Z), not the size of Q.  The check is on the values themselves.

    This is the generalisation of `lumped_drift`'s two-point check, and it is
    the assumption-free member of the three bandwidth numbers: no source, no
    load, nothing declared.
    """
    freqs = np.asarray(freqs, dtype=float)
    Zmat = np.asarray(Zmat, dtype=complex)
    if len(freqs) < 2:
        return (float(freqs[0]) if len(freqs) else float("nan"), "sweep")

    def _reactive(branch) -> tuple[int, float]:
        """(sign of the reactance, the element value on that side)."""
        im = branch.Z.imag
        if im > 0.0:
            return (1, branch.L_henry)
        if im < 0.0:
            return (-1, branch.C_farad)
        return (0, float("nan"))

    base = extract_pi(Zmat[0], float(freqs[0]), in_name, out_name,
                      differential)
    refs = [(_reactive(b)[0], _reactive(b)[1], b.R_ohm, abs(b.Z))
            for b in base.branches]

    def _moved(v: float, ref: float) -> bool:
        if not (math.isfinite(v) and math.isfinite(ref)) or ref == 0.0:
            return True
        return abs((v - ref) / abs(ref)) > tol

    def _r_moved(r: float, r_ref: float, z_ref: float) -> bool:
        """
        R is judged against the branch's whole |Z|, not against itself.

        On a low-loss branch R is round-off -- 3.15 mOhm against 160 kOhm of
        reactance on a lossless fixture -- and a RELATIVE comparison on it
        swings by orders of magnitude while the element it belongs to has not
        moved at all.  That is what once reported "the pi holds to 1 MHz" for
        a network built from constants, two lines under a lumped check saying
        every branch was within 10 %.  Normalising by |Z| asks the question
        that was meant: has this branch's IMPEDANCE left what a constant
        element predicts?  On a resistive branch |Z| is R and the test reduces
        to the relative one.
        """
        if not (math.isfinite(r) and math.isfinite(r_ref)) or z_ref <= 0.0:
            return True
        return abs(r - r_ref) / z_ref > tol

    last_ok = float(freqs[0])
    for i in range(1, len(freqs)):
        m = extract_pi(Zmat[i], float(freqs[i]), in_name, out_name,
                       differential)
        for b, (sgn_ref, val_ref, r_ref, z_ref) in zip(m.branches, refs):
            sgn, val = _reactive(b)
            if sgn != sgn_ref:
                return (last_ok, "resonance")
            if _moved(val, val_ref) or _r_moved(b.R_ohm, r_ref, z_ref):
                return (last_ok, "drift")
        last_ok = float(freqs[i])
    return (last_ok, "sweep")


def bandwidth_table(freqs: np.ndarray, Z2: np.ndarray, marker_hz: float,
                    z_src_ohm: float = 1.0,
                    loads: "tuple[float, ...]" = DEFAULT_LOADS_F
                    ) -> list[Bandwidth]:
    """
    One -3 dB answer per load capacitance -- the SENSITIVITY, not a number.

    The -3 dB bandwidth is a property of trace plus source plus load, and on a
    real routed line the load alone moves it by 7.3x (15.31 GHz at C_load = 0
    against 2.09 GHz at 200 fF).  A single printed number would therefore be
    one arbitrary point on that curve, presented as a fact about the trace.
    The table is the fix, and it is the same answer `attrib` gives to the same
    shape of question: show the what-if rather than bury the assumption.
    """
    return [bandwidth_3db(freqs,
                          transfer_function(Z2, freqs, z_src_ohm, cl),
                          marker_hz, z_src_ohm, cl)
            for cl in loads]
