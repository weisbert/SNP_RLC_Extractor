"""
pkg_rlc_attrib.py -- where a mutual impedance comes from, and what would move it.

This module answers two questions about ONE frequency of ONE spec:

  Q2  an EXACT additive, signed decomposition of a mutual impedance Z_ab into
      "the bare EM coupling" plus one term per declared termination element;
  Q1  exact (not first-order) sensitivity of Z_ab to changing any of those
      terminations.

It imports `pkg_rlc_core` and nothing else from this repo -- acyclic, the same
rule `pkg_rlc_plot` follows.  It never mutates anything it is handed.

WHAT THE DECOMPOSITION IS
-------------------------
Write the node-space network as a baseline plus a set of two-terminal element
stamps.  The BASELINE is the file's own Y with every measurement-port SIDE
merged into one node and EVERY OTHER PORT OPEN.  Nothing else is in it: no
ground, no short, no lumped element.  Each declared non-probe termination is
then an element attached between two nodes (or one node and the reference):

    ground / vdd     u = e_p           impedance 0 (ideal)
    lumped_to_gnd    u = e_p           impedance 1 / y_series_rlc(omega)
    short_to         u = e_p - e_q     impedance 0 (ideal)
    lumped_between   u = e_p - e_q     impedance 1 / y_series_rlc(omega)

With U the matrix of those u vectors, Zt the element IMPEDANCE matrix (so an
ideal element is a plain 0 and NO INFINITY EVER ENTERS THE ARITHMETIC),
Zbase = Ybase^-1, w_g the +1/-1 injection vector of measurement port g:

    G   = U^T Zbase U           H = Zt + G
    p_b = U^T Zbase w_b         r_a = U^T Zbase^T w_a        (its own solve)
    I   = H^-1 p_b              the physical current in each element
    Z_ab = w_a^T Zbase w_b  -  sum_e  I_e * r_a[e]

That is superposition, not a linearisation: the per-element terms sum to the
total exactly, and each one is a real current times a real baseline
transimpedance.  `r_a` is computed from its own solve against Ybase^T rather
than reused from `p_a`, because real EM data is not exactly reciprocal (the
user's own file: 3.4e-10, which is a thousand times the residual this feature
advertises) and `.conj().T` -- the easy numpy slip here -- is simply the wrong
operator for a complex-symmetric Y.

WHAT IT IS BLIND TO
-------------------
A port that is OPEN contributes no element and therefore no term.  The
contribution table is NOT a ranking of ports: it ranks the DECLARATIONS in the
spec.  A port you have not decided about yet appears only on the sensitivity
side (`sensitivity`, `sweep_mobius`), which is exactly what that side is for.

The split also depends on how the spec is SPELLED, not only on the network it
describes.  `6:1:14 ground` (9 elements) and `6 short_to 7:1:14` + `6 ground`
(8 shorts + 1 ground) are the same network, give the same total, and decompose
differently.  Both are right; they answer different questions.

SIGN CONVENTION
---------------
See SIGN_CONVENTION_TEXT.  It is one string so that every export can carry it
verbatim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Sequence

import numpy as np

# `_normalize_signal` is private to core and is imported ON PURPOSE.  Requirement
# 12 of this module's contract is that the attribution layer reproduces
# compute_z_matrix's probe/ground precedence EXACTLY -- reimplementing the legacy
# "B == minus side of A" alias here is precisely how the two would drift apart,
# and the symptom would be a reconciliation failure on the specs the
# reconciliation exists to guard.  Everything else below is public API.
from pkg_rlc_core import (  # noqa: F401  (Vdd/Signal used in isinstance checks)
    PINV_RCOND,
    PROBE_RANGE_TOL,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    ShortPair,
    Signal,
    TerminationSet,
    Vdd,
    _normalize_signal,
    collapse_ports,
    compute_z_matrix,
    format_freq,
    format_si,
    resolve_meas_ports,
)

__all__ = [
    "AttribError",
    "SIGN_CONVENTION_TEXT",
    "Element",
    "Term",
    "ReturnBudget",
    "Decomposition",
    "AttribContext",
    "Alternative",
    "SensitivityResult",
    "GroupResult",
    "CumulativeCurve",
    "Sweep",
    "TransferRatio",
    "DECOMPOSABLE",
    "NON_DECOMPOSABLE",
    "build_context",
    "decompose",
    "sensitivity",
    "group_joint",
    "cumulative_curve",
    "leave_one_out",
    "sweep_mobius",
    "transfer_ratio",
    "termination_impedance_diagonal",
    "termination_impedance_shared_return",
    "alt_open",
    "alt_ideal",
    "alt_resistor",
    "alt_inductor",
    "alt_series_rl",
    "alt_capacitor",
    "alt_impedance",
    "default_alternatives",
    "format_decomposition",
]


class AttribError(ValueError):
    """
    Anything this module refuses to answer.

    Subclasses ValueError so the existing `except Exception as e: show(e)` call
    sites in the GUI and the CLI upgrade for free, the same contract
    TouchstoneParseError has.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EPS = float(np.finfo(float).eps)

#: complex(nan, nan), NOT complex("nan").  A real NaN assigned into a
#: complex slot leaves imag == 0, and "L = Im(Z)/omega" then reads a
#: perfectly plausible 0 H instead of "undefined" -- the same trap
#: _probe_impedance documents in core.
_UNDEFINED = complex(float("nan"), float("nan"))

#: Reconciliation gate.  The residual between this module's sum and
#: compute_z_matrix's value is compared against
#:
#:     RESIDUAL_SAFETY * (cond(Ybase) + cond(H)) * eps * (S / |Z_ab|)
#:
#: A FIXED gate is wrong here: measured cross-algorithm agreement on a trivial
#: 4-port fixture is 3e-16, but a 153-port package with condition numbers
#: around 1e7..1e9 cannot do better than ~1e-7 relative, and a 1e-9 gate would
#: refuse exactly the files this feature exists for.
#:
#: The `S / |Z_ab|` factor is not decoration and the condition numbers alone
#: are NOT enough.  Measured on diff_pair_4port.s4p (probes on 1 and 2, ports
#: 3 and 4 grounded) at 1 MHz: cond(Ybase) = 1.3e10 and cond(H) = 1, so a
#: cond-only bound reads 4.5e-5 -- while the actual disagreement is 0.25.  The
#: gap is that an inverse is accurate relative to its LARGEST entry, and at
#: 1 MHz the largest entry of Zbase is the 1 fF port capacitance's 159 kOhm
#: while the answer is a 6 mOhm mutual: every small entry the answer is built
#: from carries the big entry's absolute error.  S is that largest
#: intermediate magnitude.
RESIDUAL_SAFETY = 64.0

#: Never claim a floor tighter than this -- the two algorithms take genuinely
#: different routes to the same number (Schur elimination + pinv contraction
#: versus a node-space inverse plus a Woodbury update), so a handful of ulps of
#: disagreement is expected even on a perfectly conditioned 2-port.
RESIDUAL_FLOOR_MIN = 64.0 * _EPS

#: Above this the per-element split is WITHHELD (the totals never are).  A
#: residual of 1% means the two algorithms disagree about the answer itself, at
#: which point apportioning it between elements is fiction.
RESIDUAL_CATASTROPHIC = 1e-2

#: cond(H) above which H is solved by minimum-norm lstsq instead of solve.
#: np.linalg.solve does NOT raise on a numerically singular matrix (the same
#: trap _is_singular_2x2 documents in core), so the check has to come first.
H_SINGULAR_COND = 1.0 / _EPS

#: |total| below this fraction of the largest term means the total is pure
#: cancellation and a "share of the total" column is meaningless -- see
#: requirement 7.  Also floored by the reconciliation residual: a total smaller
#: than our own error bar is not a total.
SHARE_ZERO_REL = 1e-9

#: The EM model's internal reference carries more than this fraction of the
#: return current -> the decomposition cannot separate the return path and says
#: so.  Measured on diff_pair_4port.s4p with both far-end ports grounded: the
#: declared ground elements carry 0.05% and the EM reference 99.95%.
RETURN_EM_DOMINANT = 0.5

#: Total return current (in amps, against a 1 A drive) below which there is no
#: net return to apportion at all -- the balanced-aggressor case, where
#: 1^T w_b == 0 by construction and both sides of the budget are roundoff.
RETURN_NO_NET_CURRENT = 1e-9

#: An UNBOUNDED sweep whose extremum exceeds the [ideal, open] bracket by this
#: much has found a near-pole of the Mobius map, not a design margin.  Measured
#: on diff_pair_4port.s4p: sweeping one ground's series L over the whole
#: half-line peaks at 9 mH of apparent M at L = 505 nH, where that inductance
#: anti-resonates with the 1 fF port capacitance -- seven orders of magnitude
#: over the 1.01 nH bracket, and at a value no ground ball has.
NEAR_POLE_RATIO = 1e3

_SHUNT_KINDS = ("ground", "vdd", "lumped_to_gnd")
_SERIES_KINDS = ("short", "lumped_between")

SIGN_CONVENTION_TEXT = (
    "Sign convention. The victim reading is V(+) - V(-) of the victim "
    "measurement port and the aggressor is driven with +1 A into its (+) side "
    "and out of its (-) side, so every term below is signed the way "
    "Z_ab = V_a / I_b is. An element current I_e > 0 flows OUT of the "
    "structure into ground for a shunt element (ground / vdd / lumped_to_gnd) "
    "and from the first port to the second for a series element (short_to / "
    "lumped_between). Flipping either measurement port's +/- assignment flips "
    "every term together: the RELATIVE signs between terms are physical, the "
    "absolute signs are a labelling choice."
)


# ---------------------------------------------------------------------------
# Quantities: what can and cannot be decomposed (requirement 8)
# ---------------------------------------------------------------------------
#
# A quantity decomposes iff it is (fixed real scalar) x (R-linear functional of
# Z_ab) evaluated at ONE configuration.  Multiplying an exact additive
# decomposition of Z_ab by a constant, or taking Re/Im of it term by term, is
# still exact.  Anything with Z_ab in a denominator, an absolute value or a
# logarithm is not: the terms of a reciprocal do not add up, and a "share of
# C_c" would be a number with no meaning at all.

@dataclass(frozen=True)
class _QuantitySpec:
    name: str
    part: str          # 'complex' | 're' | 'im'
    unit: str
    scale_kind: str    # 'one' | 'inv_omega' | 'inv_ImZaa' | 'inv_sqrt_LaLb'
    doc: str


DECOMPOSABLE: dict[str, _QuantitySpec] = {
    q.name: q for q in (
        _QuantitySpec("Z", "complex", "Ohm", "one",
                      "the mutual impedance itself"),
        _QuantitySpec("ReZ", "re", "Ohm", "one",
                      "the resistive part of the mutual impedance"),
        _QuantitySpec("ImZ", "im", "Ohm", "one",
                      "the reactive part of the mutual impedance"),
        _QuantitySpec("M", "im", "H", "inv_omega",
                      "mutual inductance, Im(Z_ab)/omega"),
        _QuantitySpec("M/L_a", "im", "", "inv_ImZaa",
                      "coupling ratio into the victim (Norton injection "
                      "ratio; see transfer_ratio for the exact one)"),
        _QuantitySpec("k", "im", "", "inv_sqrt_LaLb",
                      "coupling coefficient, M / sqrt(L_a L_b)"),
    )
}

#: Names this module refuses per term, each with the reason it refuses.  The
#: refusal is by NAME because the caller asked for something specific and
#: "unsupported quantity" would send them looking for a typo.
NON_DECOMPOSABLE: dict[str, str] = {
    "C_c": (
        "C_c = -1/(omega*Im Z_ab) is a RECIPROCAL of the decomposed quantity. "
        "Superposition adds impedances, not their inverses, so the per-element "
        "terms of C_c do not sum to C_c and any 'share' would be meaningless. "
        "C_c is still reported as a TOTAL -- it is the right reading whenever "
        "Im(Z_ab) < 0 -- just never per term. Decompose 'ImZ' or 'Z' instead"
    ),
    "Q": (
        "Q = Im(Z)/Re(Z) is a ratio of two decomposable quantities, which is "
        "not itself decomposable. Decompose 'ReZ' and 'ImZ' separately"
    ),
    "|Z|": (
        "|Z_ab| is not R-linear in Z_ab (it is a norm), so the terms of |Z| "
        "do not add. Decompose 'Z' and read the per-term magnitudes off the "
        "complex terms, which DO add"
    ),
    "dB": (
        "a dB value is a logarithm of a magnitude and is neither linear nor "
        "signed. Decompose the underlying linear quantity"
    ),
}
NON_DECOMPOSABLE["Cc"] = NON_DECOMPOSABLE["C_c"]
NON_DECOMPOSABLE["absZ"] = NON_DECOMPOSABLE["|Z|"]
NON_DECOMPOSABLE["M/L_a_dB"] = NON_DECOMPOSABLE["dB"]
NON_DECOMPOSABLE["k_dB"] = NON_DECOMPOSABLE["dB"]

_QUANTITY_ALIASES = {
    "z": "Z", "z_ab": "Z", "zab": "Z",
    "re": "ReZ", "rez": "ReZ", "re(z)": "ReZ", "r": "ReZ",
    "im": "ImZ", "imz": "ImZ", "im(z)": "ImZ", "x": "ImZ",
    "m": "M", "m_henry": "M",
    "m/l": "M/L_a", "m/l_a": "M/L_a", "m_over_la": "M/L_a",
    "k": "k",
}


def _resolve_quantity(quantity: str) -> _QuantitySpec:
    """Canonicalise a quantity name, refusing the non-decomposable ones by name."""
    raw = str(quantity).strip()
    if raw in DECOMPOSABLE:
        return DECOMPOSABLE[raw]
    key = raw.lower()
    if key in _QUANTITY_ALIASES:
        return DECOMPOSABLE[_QUANTITY_ALIASES[key]]
    for bad, why in NON_DECOMPOSABLE.items():
        if key == bad.lower():
            raise AttribError(
                f"'{raw}' cannot be decomposed per element: {why}."
            )
    raise AttribError(
        f"Unknown quantity '{raw}'. Decomposable: "
        + ", ".join(sorted(DECOMPOSABLE))
        + ". Refused by name: " + ", ".join(sorted(NON_DECOMPOSABLE))
    )


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Element:
    """
    One declared, non-probe termination, as a two-terminal stamp.

    `ports` is 0-BASED (core-side).  `describe()` renders it 1-based, which is
    the only form that may reach a user.  `source` is the connection-table row
    that declared it when the caller passed `sources=` to build_context, and
    the element KIND otherwise -- a TerminationSet carries no provenance, so
    without the rows there is no way to tell nine ports written as one range
    from nine separate rows.
    """
    kind: str
    ports: tuple[int, ...]
    source: str
    ideal: bool
    index: int = -1

    def describe(self) -> str:
        p = [str(x + 1) for x in self.ports]
        if self.kind in ("ground", "vdd"):
            return f"{self.kind} port {p[0]}"
        if self.kind == "lumped_to_gnd":
            return f"port {p[0]} -> gnd"
        if self.kind == "short":
            return f"short {p[0]}-{p[1]}"
        return f"port {p[0]}-{p[1]}"

    @property
    def is_shunt(self) -> bool:
        return self.kind in _SHUNT_KINDS


@dataclass(frozen=True)
class Term:
    """
    One row of the decomposition.

    `element is None` marks the BARE EM term -- the coupling the file already
    has with every non-probe port open.  It carries no current and no
    transimpedance, so both read NaN rather than a plausible zero.

    `share_inline` / `share_quad` are the signed projection of the term onto
    the total and the part at right angles to it (requirement 7): a complex
    ratio would let a term that is 90 degrees away from the total inflate any
    magnitude-based cancellation measure while being harmless.  Both are NaN
    when the total is indistinguishable from zero.
    """
    element: Element | None
    contribution: complex
    current: complex
    trans_z: complex
    share_inline: float
    share_quad: float

    @property
    def label(self) -> str:
        return "bare EM coupling" if self.element is None \
            else self.element.describe()


@dataclass(frozen=True)
class ReturnBudget:
    """
    Where the aggressor's drive current comes back (requirement 6).

    `em_reference` is |1^T Ybase V|: the net current entering the structure
    from outside, which is by KCL exactly what leaves through the EM model's
    own internal reference (the ground plane inside the S-parameters).
    `declared` is sum|I_e| over the shunt elements this spec declares.

    When the EM reference dominates, a "forward path minus return path" story
    CANNOT be confirmed or refuted from the declared elements, and `note` says
    so instead of letting the reader assume otherwise.
    """
    em_reference: float
    declared: float
    declared_all: float
    em_fraction: float
    dominant: bool
    note: str


@dataclass
class Decomposition:
    """
    One split, plus everything needed to decide how much of it to believe.

    `total_reference` is compute_z_matrix's value FOR THE SPEC AS DECLARED, and
    `reference_applicable` says whether that is the same network this
    decomposition describes.  It is not, whenever the caller passed a what-if
    `zt` (requirement 2's whole point): a dense element-impedance matrix cannot
    be written as a TerminationSet, so the engine has no way to be asked about
    it.  `residual_rel` is therefore ALWAYS measured on the declared
    configuration -- which is the cross-ALGORITHM check it was always meant to
    be -- and never on a difference between two different networks.  See
    `decompose`.

    `C_c_total` is the coupling capacitance, present as a TOTAL and never per
    term: it is a reciprocal of the decomposed quantity, so its terms would not
    add (see NON_DECOMPOSABLE['C_c']).  It is still the right reading whenever
    Im(Z_ab) < 0, which is why the number is here rather than absent.
    """
    victim: str
    aggressor: str
    freq_hz: float
    requested_hz: float
    quantity: str
    unit: str
    total_reference: complex
    total_sum: complex
    residual_rel: float
    residual_floor: float
    terms: list[Term]
    return_budget: ReturnBudget
    reference_note: str
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # --- diagnostics the report header prints; extra to the contract's list.
    split_trustworthy: bool = True
    share_suppressed: bool = False
    cond_Ybase: float = float("nan")
    cond_H: float = float("nan")
    reciprocity_rel: float = float("nan")
    Z_ab: complex = _UNDEFINED
    reference_applicable: bool = True
    C_c_total: complex = _UNDEFINED

    @property
    def direct_term(self) -> Term | None:
        for t in self.terms:
            if t.element is None:
                return t
        return None


# ---------------------------------------------------------------------------
# Termination-impedance builders (requirement 2)
# ---------------------------------------------------------------------------

def termination_impedance_diagonal(values: Sequence[complex]) -> np.ndarray:
    """
    Independent two-terminal impedances, one per element -- diag(values).

    This is the OBVIOUS ground model and on a real package it is the WRONG one.
    Ground balls share a return plane, so their return inductances are mutually
    coupled; modelling them as independent series inductors understates the
    effective common-mode return inductance by a factor (1 + (n-1)k).  Use
    termination_impedance_shared_return unless you know the balls really are
    independent.
    """
    v = np.asarray(list(values), dtype=complex)
    if v.ndim != 1:
        raise AttribError("termination_impedance_diagonal takes a 1-D sequence")
    return np.diag(v)


def termination_impedance_shared_return(z_self: Sequence[complex] | complex,
                                        z_ret: complex,
                                        m: int | None = None) -> np.ndarray:
    """
    diag(z_self) + z_ret * ones(m, m) -- n balls each with their own series
    impedance, all sharing ONE return impedance back to the reference.

    That single dense term is the whole point.  H = Zt + G accepts a dense Zt
    with zero change to the maths and zero change to the cost, and the
    difference it makes is not subtle: measured on decap_4port.s4p with four
    declared ground elements, 1 nH per ball independently versus the same four
    balls tied through one shared 1 nH moved |M| by 9.6 dB -- larger than the
    6 dB dispute this feature exists to settle.

    `z_self` may be a scalar, in which case `m` must be given.

    It builds the block for ALL m elements, and "a shared return plane" is a
    statement about SHUNT elements (ground / vdd / lumped_to_gnd) only.  Handed
    the full m for a spec that also contains a `short_to`, it gives that short a
    return impedance too -- which quietly stops it being a short.  Build the
    dense block over the shunt sub-block and leave the series elements on their
    declared diagonal; `ctx.elements[i].is_shunt` is the predicate.
    """
    if np.isscalar(z_self):
        if m is None:
            raise AttribError(
                "termination_impedance_shared_return needs m when z_self is a "
                "scalar")
        diag = np.full(int(m), complex(z_self), dtype=complex)
    else:
        diag = np.asarray(list(z_self), dtype=complex)
        if m is not None and int(m) != diag.size:
            raise AttribError(
                f"z_self has {diag.size} entries but m={int(m)}")
    n = diag.size
    return np.diag(diag) + complex(z_ret) * np.ones((n, n), dtype=complex)


# ---------------------------------------------------------------------------
# Candidate alternative terminations (requirement 9a)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Alternative:
    """
    A what-if termination for one element, as its impedance at THIS frequency.

    `z is None` means OPEN -- the element is removed from the network entirely,
    which is not the same as a very large impedance and is handled by dropping
    its row and column rather than by a large number.
    """
    name: str
    z: complex | None

    @property
    def is_open(self) -> bool:
        return self.z is None

    @property
    def is_ideal(self) -> bool:
        return self.z is not None and self.z == 0


def alt_open() -> Alternative:
    return Alternative("open", None)


def alt_ideal() -> Alternative:
    return Alternative("ideal", 0j)


def alt_resistor(R: float, name: str | None = None) -> Alternative:
    return Alternative(name or f"R={format_si(R, 'Ohm')}", complex(R, 0.0))


def alt_inductor(L: float, omega: float, name: str | None = None) -> Alternative:
    return Alternative(name or f"L={format_si(L, 'H')}", 1j * omega * L)


def alt_series_rl(R: float, L: float, omega: float,
                  name: str | None = None) -> Alternative:
    return Alternative(
        name or f"R={format_si(R, 'Ohm')}+L={format_si(L, 'H')}",
        complex(R, 0.0) + 1j * omega * L)


def alt_capacitor(C: float, omega: float, name: str | None = None) -> Alternative:
    if omega == 0.0 or C == 0.0:
        return Alternative(name or f"C={format_si(C, 'F')}", None)
    return Alternative(name or f"C={format_si(C, 'F')}",
                       1.0 / (1j * omega * C))


def alt_impedance(z: complex, name: str) -> Alternative:
    return Alternative(name, complex(z))


def default_alternatives(omega: float, z0: float = 50.0,
                         l_gnd: float = 1e-9,
                         r_gnd: float = 0.1,
                         c_shunt: float = 100e-12) -> list[Alternative]:
    """The six candidate terminations requirement 9a names, in a fixed order."""
    return [
        alt_open(),
        alt_ideal(),
        alt_resistor(z0),
        alt_inductor(l_gnd, omega),
        alt_series_rl(r_gnd, l_gnd, omega),
        alt_capacitor(c_shunt, omega),
    ]


# ---------------------------------------------------------------------------
# Node space (requirement 12: the engine's precedence, replicated)
# ---------------------------------------------------------------------------

def _shorted_groups(terminations: TerminationSet, n: int) -> list[list[int]]:
    """
    Union-Find over the ShortPair couplings -- the same merge compute_z_matrix
    step 2 does, and deliberately a second copy of it.  Sharing the one inside
    compute_z_matrix would mean editing the single function the golden
    regression exists to pin, and the cost of the copy is nine lines.
    """
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            ra, rb = find(cpl.port_i), find(cpl.port_j)
            if ra != rb:
                parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(v) for v in groups.values()]


def _probe_side_of_port(terminations: TerminationSet,
                        n: int) -> dict[int, tuple[str, int]]:
    """
    port (0-based) -> the (group, sign) probe side it belongs to, if any.

    Mirrors compute_z_matrix's `merge_terms` exactly: a shorted group carrying
    ANY Signal is that Signal's side in its entirety, so a ground shorted onto
    a probe port does NOT ground the probe -- the Signal wins and the Ground is
    discarded.  That precedence is pinned by tests/test_core.py's
    TestTerminationPrecedence and it is the reason this function exists rather
    than a simple `isinstance(t, Signal)` scan: getting it wrong would make the
    reconciliation line fail on exactly the specs it is there to guard.
    """
    out: dict[int, tuple[str, int]] = {}
    for members in _shorted_groups(terminations, n):
        keys = {_normalize_signal(t)
                for t in (terminations.termination_of(p) for p in members)
                if isinstance(t, Signal)}
        if len(keys) == 1:
            key = next(iter(keys))
            for p in members:
                out[p] = key
        # len(keys) > 1 is a conflicting-group spec; compute_z_matrix has
        # already raised on it by the time we get here (build_context calls it
        # first, on purpose, so the message is core's).
    return out


# ---------------------------------------------------------------------------
# Exact structural rank of U (requirement 4)
# ---------------------------------------------------------------------------

def _dependent_columns(U_int: np.ndarray) -> list[int]:
    """
    Indices of the columns of U that are exactly linear combinations of the
    columns before them.

    EXACT arithmetic on the integer incidence, not a numerical rank.  A
    redundant spec -- a `short_to` between two ports that are already grounded,
    the same port grounded twice through overlapping ranges -- is a SPEC BUG,
    and reporting it as "genuinely unattributable physics" is the worst
    available outcome.  Structure first; cond(G) only afterwards.

    The float rank is used as a gate so the Fraction pass runs only when
    something is actually wrong: entries are 0/+-1, so a float rank of m is
    trustworthy, and on a 60-ball spec the exact pass is ~0.5 s that nobody
    should pay for a healthy file.
    """
    rows, cols = U_int.shape
    if cols == 0:
        return []
    if np.linalg.matrix_rank(U_int.astype(float)) == cols:
        return []

    basis: list[list[Fraction]] = []
    pivots: list[int] = []
    dep: list[int] = []
    for c in range(cols):
        v = [Fraction(int(x)) for x in U_int[:, c]]
        for row, piv in zip(basis, pivots):
            if v[piv]:
                f = v[piv] / row[piv]
                v = [vi - f * ri for vi, ri in zip(v, row)]
        nz = next((i for i in range(rows) if v[i]), None)
        if nz is None:
            dep.append(c)
        else:
            basis.append(v)
            pivots.append(nz)
    return dep


# ---------------------------------------------------------------------------
# The context
# ---------------------------------------------------------------------------

@dataclass
class AttribContext:
    """
    Everything that depends on (Y, terminations, frequency) but not on which
    pair is being asked about.  Build it once, ask it many questions.

    Public attributes a caller may read:
        freq_hz, requested_hz, omega, port_names, elements, groups
        Zref        (G, G)  compute_z_matrix's authoritative Z at freq_hz,
                            for the spec AS DECLARED
        Zop         (G, G)  this module's own Z with `Zt` in force
        Zop_declared (G, G) this module's own Z at the DECLARED element
                            impedances -- identical to Zop unless a what-if
                            `zt` was passed, and the only thing that may be
                            reconciled against Zref (see `is_whatif`)
        Zt          (m, m)  the element impedance matrix in force
        is_whatif           True when `zt` was supplied, i.e. Zop describes a
                            network compute_z_matrix was never asked about
        cond_Ybase, cond_G, reciprocity_rel
        notes, warnings

    cond(G) is a DIAGNOSTIC, not a trust signal, and neither is cond(Ybase).
    Measured: a node space collapsed by a 1 pOhm tie reports cond(G) = 1.0 --
    Gm has underflowed to ~1e-14 times the identity and its condition number is
    perfect -- while Zop[a, b] is exactly 0 against the engine's 305 pH.  The
    RECONCILIATION RESIDUAL is what catches that; the condition numbers only
    explain it afterwards.
    """
    freq_hz: float
    requested_hz: float
    omega: float
    port_names: list[str]
    elements: list[Element]
    groups: dict[str, tuple[int, ...]]

    # --- linear algebra, node space
    Ybase: np.ndarray          # (nr, nr) reduced baseline admittance
    Ybase0: np.ndarray         # (n0, n0) baseline before any fold
    Pmat: np.ndarray           # (n0, nr) reduced -> original node map
    U: np.ndarray              # (nr, m) element incidence, ACTIVE elements
    U0: np.ndarray             # (n0, m) the same columns, original nodes
    U0_folded: np.ndarray      # (n0, n_folded) the elements in the baseline
    W: np.ndarray              # (nr, G) probe injection
    W0: np.ndarray             # (n0, G)
    Gm: np.ndarray             # (m, m) U^T Zbase U
    Pmat_b: np.ndarray         # (m, G) U^T Zbase W        -> p_b is column b
    Rmat: np.ndarray           # (m, G) U^T Zbase^T W      -> r_a is column a
    Dmat: np.ndarray           # (G, G) W^T Zbase W
    Zt: np.ndarray             # (m, m) element impedances
    z_declared: np.ndarray     # (m,) the declared diagonal, for what-ifs

    # --- bookkeeping
    folded: list[Element]
    dropped: list[tuple[Element, str]]
    dependent: list[int]
    baseline_singular: bool
    bad_probes: list[int]
    cond_Ybase: float
    cond_Ybase_eff: float
    cond_G: float
    reciprocity_rel: float
    Zref: np.ndarray
    Zop: np.ndarray
    core_warnings: list[str]
    notes: list[str]
    warnings: list[str]
    is_whatif: bool = False
    Zop_declared: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 0), dtype=complex))

    # ---- small helpers -------------------------------------------------

    @property
    def n_elements(self) -> int:
        return len(self.elements)

    def port_index(self, who: int | str) -> int:
        """Measurement port name or index -> index, with a message that lists."""
        if isinstance(who, str):
            if who in self.port_names:
                return self.port_names.index(who)
            raise AttribError(
                f"No measurement port named '{who}'. This spec has: "
                + ", ".join(f"'{n}'" for n in self.port_names))
        i = int(who)
        if not 0 <= i < len(self.port_names):
            raise AttribError(
                f"Measurement port index {i} is outside 0..{len(self.port_names)-1}")
        return i

    def element_index(self, who: int | str | Element) -> int:
        if isinstance(who, Element):
            return who.index
        if isinstance(who, str):
            for e in self.elements:
                if e.describe() == who:
                    return e.index
            raise AttribError(f"No element described as '{who}'")
        i = int(who)
        if not 0 <= i < len(self.elements):
            raise AttribError(
                f"Element index {i} is outside 0..{len(self.elements)-1}")
        return i

    def group_indices(self, group: str | Sequence) -> tuple[int, ...]:
        """A group label, or an explicit sequence of elements, -> indices."""
        if isinstance(group, str):
            if group not in self.groups:
                raise AttribError(
                    f"No element group '{group}'. This spec has: "
                    + ", ".join(f"'{g}'" for g in self.groups))
            return self.groups[group]
        return tuple(self.element_index(g) for g in group)


def _incidence(node_of_port: Sequence[int], n_nodes: int) -> np.ndarray:
    """(N, n_nodes) 0/1 matrix; a port mapped to -1 contributes nothing."""
    A = np.zeros((len(node_of_port), n_nodes), dtype=complex)
    for p, nd in enumerate(node_of_port):
        if nd >= 0:
            A[p, nd] = 1.0
    return A


def _cond(M: np.ndarray) -> float:
    """np.linalg.cond that answers 1.0 for the empty matrix instead of raising."""
    if M.size == 0:
        return 1.0
    try:
        c = float(np.linalg.cond(M))
    except np.linalg.LinAlgError:
        return float("inf")
    return c if math.isfinite(c) else float("inf")


def _cond_effective(M: np.ndarray) -> float:
    """
    The condition number of the part of M a pseudo-inverse actually KEEPS.

    The plain cond() of a deliberately singular matrix is the wrong number to
    build an error bound from: coupled_4port_float.s4p measures
    cond(Ybase) = 2.48e16, which would put the reconciliation floor at 88 and
    turn the gate off, while the answer along the retained directions is exact
    to 1e-18.  The cutoff mirrors what lstsq(rcond=None) and pinv use.
    """
    if M.size == 0:
        return 1.0
    try:
        s = np.linalg.svd(M, compute_uv=False)
    except np.linalg.LinAlgError:                        # pragma: no cover
        return float("inf")
    if s.size == 0 or s[0] == 0:
        return 1.0
    keep = s > _EPS * max(M.shape) * s[0]
    if not np.any(keep):                                 # pragma: no cover
        return float("inf")
    return float(s[0] / s[keep][-1])


def _solve_or_lstsq(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, bool]:
    """
    solve(A, B), falling back to the minimum-norm lstsq when A is singular.

    np.linalg.solve does NOT raise on a numerically singular matrix -- the same
    LAPACK behaviour _is_singular_2x2 exists for in core -- so the condition
    number is checked FIRST.  When the fallback fires the TOTAL is still exact:
    the null directions of H = Zt + G are exactly the null directions of U (for
    ideal elements), and r_a^T n = w_a^T Zbase U n = 0 for any such n.  Only
    the split between the dependent elements is ambiguous, and the caller says
    so by name.
    """
    if A.size == 0:
        return np.zeros((0,) + B.shape[1:], dtype=complex), False
    if _cond(A) < H_SINGULAR_COND:
        try:
            return np.linalg.solve(A, B), False
        except np.linalg.LinAlgError:
            pass
    X, *_ = np.linalg.lstsq(A, B, rcond=None)
    return X, True


def build_context(Y: np.ndarray,
                  freqs: np.ndarray,
                  terminations: TerminationSet,
                  freq_hz: float,
                  zt: np.ndarray | None = None,
                  sources: dict[int, str] | None = None) -> AttribContext:
    """
    Build the attribution context for one frequency of one spec.

    `Y` is (nfreqs, N, N) as `s_to_y` returns it and `freqs` its axis; the data
    point CLOSEST to `freq_hz` is used, the same argmin
    `extract_coupling_at_freq` does.

    `zt` is the (m, m) element impedance matrix.  The default is the one the
    spec itself declares -- 0 for ideal elements, 1/y_series_rlc(omega) for
    lumped ones -- which is what makes the reconciliation against
    compute_z_matrix meaningful.  Pass a dense one (see
    termination_impedance_shared_return) to ask a what-if.

    `sources` is `pkg_rlc_core.row_sources(...)`'s output: 1-based port -> the
    connection-table row that declared it.  Without it every element of a kind
    lands in one group named after the kind, because a TerminationSet carries
    no provenance and nine ports written as one range are indistinguishable
    from nine rows.
    """
    Y = np.asarray(Y)
    freqs = np.asarray(freqs, dtype=float)
    if Y.ndim != 3 or Y.shape[1] != Y.shape[2]:
        raise AttribError(f"Y must have shape (nfreqs, N, N), got {Y.shape}")
    if freqs.size == 0:
        raise AttribError("Empty frequency array")
    if freqs.size != Y.shape[0]:
        raise AttribError(
            f"Y has {Y.shape[0]} frequencies but freqs has {freqs.size}")

    idx = int(np.argmin(np.abs(freqs - float(freq_hz))))
    f = float(freqs[idx])
    omega = 2.0 * math.pi * f
    n = int(Y.shape[1])
    notes: list[str] = []
    warnings: list[str] = []

    # --- 0. The authoritative answer, from the engine itself.
    #
    # A one-frequency slice, not the whole sweep: compute_z_matrix's Schur solve
    # is a gufunc (per-matrix, so batch size cannot change it) and its
    # contraction is already per-frequency, so the slice returns exactly the
    # numbers a full sweep would put at this index -- for a few microseconds
    # instead of a few hundred milliseconds on a 5000-point file.
    #
    # Calling it FIRST is deliberate: it validates the port indices, resolves
    # the measurement ports and raises on conflicting signal groups, all with
    # core's own messages rather than a second set of near-identical ones.
    Zmat_ref, port_names, core_warnings = compute_z_matrix(
        Y[idx:idx + 1], freqs[idx:idx + 1], terminations)
    Zref = np.array(Zmat_ref[0], dtype=complex)
    G = len(port_names)

    # --- 1. Node space: one node per probe SIDE, one per every other port.
    mports = resolve_meas_ports(terminations, n)
    side_of = _probe_side_of_port(terminations, n)

    side_keys: list[tuple[str, int]] = []
    node_of_side: dict[tuple[str, int], int] = {}
    node_pairs: list[tuple[int, int | None]] = []
    for mp in mports:
        plus_node = len(side_keys)
        side_keys.append((mp.name, +1))
        node_of_side[(mp.name, +1)] = plus_node
        minus_node: int | None = None
        if mp.minus:
            minus_node = len(side_keys)
            side_keys.append((mp.name, -1))
            node_of_side[(mp.name, -1)] = minus_node
        node_pairs.append((plus_node, minus_node))

    node_of_port: list[int] = [-1] * n
    for p in range(n):
        key = side_of.get(p)
        if key is not None and key in node_of_side:
            node_of_port[p] = node_of_side[key]
    next_node = len(side_keys)
    for p in range(n):
        if node_of_port[p] < 0:
            node_of_port[p] = next_node
            next_node += 1
    n0 = next_node

    A0 = _incidence(node_of_port, n0)
    Yk = np.asarray(Y[idx], dtype=complex)
    # A NaN or an inf anywhere in this frequency's Y poisons every solve below,
    # and the first thing it reaches is np.linalg.svd, which raises
    # LinAlgError("SVD did not converge") -- no verdict, no frequency, no file,
    # in a repo whose TouchstoneParseError contract exists precisely to answer
    # "is my data bad or is your tool bad?".  Refuse HERE, by name.
    #
    # compute_z_matrix survives the same input and returns NaN with a warning,
    # and it has already run above, so the caller still has the engine's
    # reading.  This module cannot follow it: a Woodbury update around a
    # pseudo-inverse of a NaN baseline has no meaningful minimum-norm solution
    # to fall back to, and answering with a plausible number would be worse
    # than refusing.
    n_bad = int(np.count_nonzero(~np.isfinite(Yk)))
    if n_bad:
        rows = sorted({int(i) + 1 for i in np.nonzero(~np.isfinite(Yk))[0]})
        raise AttribError(
            f"Y has {n_bad} non-finite entr"
            + ("y" if n_bad == 1 else "ies")
            + f" at {format_freq(f)} (port row(s) {collapse_ports(rows)}), so "
              "no attribution is possible at this frequency. compute_z_matrix "
              "reports NaN for the same data; attribute at a neighbouring "
              "frequency, or fix the file.")
    # .T, never .conj().T: Y is complex SYMMETRIC, not Hermitian, and the
    # conjugate transpose is the single easiest numpy bug to write here.
    Ybase0 = A0.T @ Yk @ A0

    W0 = np.zeros((n0, G), dtype=complex)
    for g, (plus_node, minus_node) in enumerate(node_pairs):
        W0[plus_node, g] = 1.0
        if minus_node is not None:
            W0[minus_node, g] = -1.0

    # --- 2. Elements, in a deterministic reading order: every per-port
    # declaration by ascending port, then every coupling in declaration order.
    all_elements: list[Element] = []
    y_of: list[complex | None] = []      # None == ideal
    dropped: list[tuple[Element, str]] = []

    def _src(port0: int, kind: str) -> str:
        if sources:
            lab = sources.get(port0 + 1)
            if lab:
                return lab
        return kind

    for p in range(n):
        t = terminations.termination_of(p)
        if isinstance(t, (Ground, Vdd)):
            kind = "vdd" if isinstance(t, Vdd) else "ground"
            all_elements.append(Element(kind, (p,), _src(p, kind), True))
            y_of.append(None)
        elif isinstance(t, LumpedToGnd):
            all_elements.append(
                Element("lumped_to_gnd", (p,), _src(p, "lumped_to_gnd"), False))
            y_of.append(complex(np.asarray(t.y_func(np.array([omega])))[0]))
    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            all_elements.append(Element(
                "short", (cpl.port_i, cpl.port_j),
                _src(cpl.port_i, "short"), True))
            y_of.append(None)
        elif isinstance(cpl, LumpedBetween):
            all_elements.append(Element(
                "lumped_between", (cpl.port_i, cpl.port_j),
                _src(cpl.port_i, "lumped_between"), False))
            y_of.append(complex(np.asarray(cpl.y_func(np.array([omega])))[0]))

    m_all = len(all_elements)
    U0_all = np.zeros((n0, m_all), dtype=complex)
    for e, elem in enumerate(all_elements):
        U0_all[node_of_port[elem.ports[0]], e] += 1.0
        if len(elem.ports) == 2:
            U0_all[node_of_port[elem.ports[1]], e] -= 1.0

    # Elements the ENGINE discards: anything landing on a probe node.  A short
    # whose two ends are the same probe side gives u == 0; a ground or a lumped
    # element on a probe port is thrown away by merge_terms (the Signal wins).
    #
    # This set is SMALLER than inert_lumped_messages' and deliberately so.  A
    # short_to is an ELEMENT here, not a node merge, so a `lumped_between` in
    # parallel with one is not annihilated -- it stays in the table and simply
    # carries no current (measured on `3 short_to 4` + `3 lumped_between 4
    # R=20`: I = 2.7e-21 A against 1.0 A through the short).  Naming the
    # resistor and showing its zero is strictly more informative than deleting
    # it, and the structural rank check flags the pair as redundant anyway.
    alive: list[int] = []
    for e, elem in enumerate(all_elements):
        on_probe = any(side_of.get(p) is not None for p in elem.ports)
        if not np.any(U0_all[:, e]):
            dropped.append((elem, "annihilated by the reduction: both ends "
                                  "land on the same node, so the stamp sums "
                                  "to exactly zero"))
        elif on_probe and elem.kind in _SHUNT_KINDS:
            dropped.append((elem, "discarded by the engine: the port carries "
                                  "a probe, and merge_terms lets the Signal "
                                  "win over a ground / lumped termination"))
        elif y_of[e] is not None and y_of[e] == 0:
            dropped.append((elem, "zero admittance at this frequency, i.e. an "
                                  "open circuit -- it is not in the network"))
        else:
            alive.append(e)

    # --- 3. Fold whatever the baseline has no reference without (requirement 3)
    Pmat = np.eye(n0, dtype=complex)
    node_map = list(range(n0))          # original node -> reduced node, -1 gone
    folded: list[int] = []
    folded_lumped: list[int] = []
    baseline_singular = False

    def _rebuild() -> tuple[np.ndarray, np.ndarray]:
        P = np.zeros((n0, max(node_map) + 1 if node_map and max(node_map) >= 0
                      else 0), dtype=complex)
        for orig, red in enumerate(node_map):
            if red >= 0:
                P[orig, red] = 1.0
        Yb = P.T @ Ybase0 @ P
        for e in folded_lumped:
            u = P.T @ U0_all[:, e]
            Yb = Yb + y_of[e] * np.outer(u, u)
        return P, Yb

    Pmat, Ybase = _rebuild()
    for _ in range(n0 + 1):
        nr = Ybase.shape[0]
        if nr == 0:
            break
        try:
            usv_u, usv_s, _vh = np.linalg.svd(Ybase, full_matrices=False)
        except np.linalg.LinAlgError:
            baseline_singular = True
            break
        smax = float(usv_s[0]) if usv_s.size else 0.0
        rank = int(np.count_nonzero(usv_s > PINV_RCOND * smax)) if smax > 0 else 0
        if rank == nr:
            break
        baseline_singular = True
        # Which remaining elements are NOT reachable from the baseline?  Those
        # are the ones whose stamp is what gives the structure a reference at
        # all; a Woodbury update through a pseudo-inverse is not valid for them.
        left_null = usv_u[:, rank:]
        worst = -1
        worst_resid = PROBE_RANGE_TOL
        for e in alive:
            if e in folded:
                continue
            u = Pmat.T @ U0_all[:, e]
            nu = float(np.linalg.norm(u))
            if nu == 0.0:
                continue
            resid = float(np.linalg.norm(left_null.conj().T @ u)) / nu
            if resid > worst_resid:
                worst_resid = resid
                worst = e
        if worst < 0:
            break
        folded.append(worst)
        elem = all_elements[worst]
        if elem.ideal:
            # node_map holds REDUCED indices; node_of_port holds ORIGINAL ones.
            # Comparing the two directly worked on the first fold (the map is
            # the identity then) and quietly deleted the wrong node on the
            # second -- measured on coupled_4port_float.s4p with ports 2 and 4
            # grounded: M came back 4.00e-10 H against the engine's 8.00e-10,
            # a clean factor of two with nothing raised.
            red = [node_map[node_of_port[p]] for p in elem.ports]
            if len(red) == 1 or -1 in red:
                # A shunt element, or a series element whose far end is already
                # at 0 V -- either way this node goes to ground.
                gone = red[0] if red[0] >= 0 else red[1]
                node_map = [-1 if nd == gone else nd for nd in node_map]
            else:
                ra, rb = red
                node_map = [ra if nd == rb else nd for nd in node_map]
            # renumber so the surviving reduced nodes are 0..nr-1 again
            live_nodes = sorted({nd for nd in node_map if nd >= 0})
            relabel = {old: i for i, old in enumerate(live_nodes)}
            node_map = [relabel[nd] if nd >= 0 else -1 for nd in node_map]
        else:
            folded_lumped.append(worst)
        Pmat, Ybase = _rebuild()

    active = [e for e in alive if e not in folded]
    # A fold can annihilate a survivor (two shorts between the same node pair,
    # a ground on a node another fold merged away).  Same class as the inert
    # elements above, just discovered one step later.
    still: list[int] = []
    for e in active:
        if np.any(Pmat.T @ U0_all[:, e]):
            still.append(e)
        else:
            dropped.append((all_elements[e],
                            "annihilated once the baseline absorbed the "
                            "elements that give this structure a reference"))
    active = still

    elements: list[Element] = []
    for i, e in enumerate(active):
        src = all_elements[e]
        elements.append(Element(src.kind, src.ports, src.source, src.ideal, i))
    folded_elements = [all_elements[e] for e in folded]

    nr = Ybase.shape[0]
    U0 = U0_all[:, active] if active else np.zeros((n0, 0), dtype=complex)
    U0_folded = (U0_all[:, folded] if folded
                 else np.zeros((n0, 0), dtype=complex))
    U = Pmat.T @ U0
    W = Pmat.T @ W0

    if folded_elements:
        ports = sorted({p + 1 for el in folded_elements for p in el.ports})
        notes.append(
            "Port(s) " + collapse_ports(ports) + " are IN THE BASELINE because "
            "the structure has no reference without them: with every non-probe "
            "port open the node admittance is singular, so "
            + ", ".join(el.describe() for el in folded_elements)
            + " was folded in and has no term of its own.")

    # --- 4. The two solves.  np.linalg.solve with a multi-column right-hand
    # side is ONE LU factorisation and k triangular solves, which is exactly
    # what scipy's lu_factor / lu_solve would buy -- and scipy stays out of the
    # import list because deploy/doctor.sh's tiers assume numpy is this repo's
    # only hard dependency.  An explicit inv is never formed.
    rhs = np.concatenate([U, W], axis=1) if nr else np.zeros((0, U.shape[1] + G),
                                                             dtype=complex)
    if nr == 0:
        ZU = np.zeros((0, U.shape[1]), dtype=complex)
        ZW = np.zeros((0, G), dtype=complex)
        ZtW = np.zeros((0, G), dtype=complex)
        cond_Y = cond_Y_eff = 1.0
    else:
        cond_Y = cond_Y_eff = _cond(Ybase)
        if cond_Y < H_SINGULAR_COND:
            sol = np.linalg.solve(Ybase, rhs)
            # REQUIREMENT 1: r_a is its OWN solve against Ybase^T.  Reusing
            # p_a assumes reciprocity, and the user's real file misses it by
            # 3.4e-10 -- a thousand times the residual this feature advertises.
            ZtW = np.linalg.solve(Ybase.T, W)
        else:
            baseline_singular = True
            Zb = np.linalg.pinv(Ybase, rcond=PINV_RCOND)
            sol = Zb @ rhs
            ZtW = Zb.T @ W
            # The error bound must use the EFFECTIVE condition number -- the
            # spread of the singular values pinv actually KEEPS.  The true
            # cond() of a fully floating structure is 2.48e16 (measured on
            # coupled_4port_float.s4p), which would put the reconciliation
            # floor at 88 and make the gate meaningless, while the answer
            # along the retained directions is exact to 1e-18.
            ss = np.linalg.svd(Ybase, compute_uv=False)
            keep = ss > PINV_RCOND * ss[0] if ss.size else ss
            cond_Y_eff = (float(ss[0] / ss[keep][-1])
                          if np.any(keep) else float("inf"))
            warnings.append(
                "The baseline node admittance is still singular after folding "
                "(cond = %.3g); Zbase is a pseudo-inverse and only probes and "
                "elements orthogonal to its null space are meaningful."
                % cond_Y)
        ZU = sol[:, :U.shape[1]]
        ZW = sol[:, U.shape[1]:]

    Gm = U.T @ ZU
    Pb = U.T @ ZW
    Rm = U.T @ ZtW
    Dm = W.T @ ZW

    # Reciprocity diagnostic: how far r_a is from p_a, reported not assumed.
    if Pb.size:
        denom = float(np.max(np.abs(Pb)))
        recip = (float(np.max(np.abs(Rm - Pb))) / denom) if denom > 0 else 0.0
    else:
        denom = float(np.max(np.abs(Dm))) if Dm.size else 0.0
        recip = (float(np.max(np.abs(Dm - Dm.T))) / denom) if denom > 0 else 0.0

    # --- 5. Probes with no return path, same test core's _probe_impedance runs.
    bad_probes: list[int] = []
    if baseline_singular and nr:
        try:
            uu, ss, _ = np.linalg.svd(Ybase, full_matrices=False)
            smax = float(ss[0]) if ss.size else 0.0
            rank = int(np.count_nonzero(ss > PINV_RCOND * smax)) if smax else 0
            norms = np.linalg.norm(W, axis=0)
            resid = np.linalg.norm(uu[:, rank:].conj().T @ W, axis=0)
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = np.where(norms > 0.0, resid / norms, 0.0)
            bad_probes = [int(g) for g in np.nonzero(rel > PROBE_RANGE_TOL)[0]]
        except np.linalg.LinAlgError:
            bad_probes = list(range(G))
        if bad_probes:
            warnings.append(
                "Measurement port(s) "
                + ", ".join(f"'{port_names[g]}'" for g in bad_probes)
                + " have no return path for the injected current; their row "
                  "and column are undefined, exactly as compute_z_matrix "
                  "reports them.")

    # --- 6. Zt
    m = len(elements)
    z_declared = np.zeros(m, dtype=complex)
    for i, e in enumerate(active):
        if y_of[e] is not None:
            yv = y_of[e]
            # No `if yv == 0: z = inf` branch, and that is not an omission.  A
            # zero-admittance element IS an open circuit and was dropped as
            # inert above (it never reaches `active`), so the branch was
            # unreachable -- and had it ever been reached it would have put an
            # infinity into Zt, which is the one thing the Zt = D^-1 formulation
            # exists to guarantee never happens.  The drop rule is the single
            # place that decision is taken.
            if not (math.isfinite(yv.real) and math.isfinite(yv.imag)):
                z_declared[i] = 0j
                warnings.append(
                    f"{elements[i].describe()} has an INFINITE admittance at "
                    "this frequency (R=L=0 and no C); it is treated as an "
                    "ideal short here, while compute_z_matrix stamps an "
                    "infinity and returns NaN. Give the element an R, an L or "
                    "a C.")
            else:
                z_declared[i] = 1.0 / yv
    Zt_declared = np.diag(z_declared)
    is_whatif = zt is not None
    if zt is None:
        Zt = Zt_declared
    else:
        Zt = np.asarray(zt, dtype=complex)
        if Zt.shape != (m, m):
            raise AttribError(
                f"zt must be ({m}, {m}) -- one row per ACTIVE element -- got "
                f"{Zt.shape}. The active elements are: "
                + "; ".join(e.describe() for e in elements))
        # Zt = D^-1 exists so that an ideal element is a plain 0 and NO
        # INFINITY EVER ENTERS THE ARITHMETIC.  A caller-supplied matrix is the
        # one route round that, and an inf there does not fail loudly -- it
        # propagates into H = Zt + G and comes back out as NaN somewhere the
        # message would name a solve rather than the input.
        if not np.all(np.isfinite(Zt)):
            raise AttribError(
                "zt has non-finite entries. An OPEN element is spelled by "
                "leaving it out of the spec (or by alt_open() on the "
                "sensitivity side), never by an infinite impedance: this "
                "module's whole Zt = D^-1 formulation exists so that no "
                "infinity enters the arithmetic.")
        notes.append(
            "This context carries a CALLER-SUPPLIED element impedance matrix, "
            "so compute_z_matrix has NEVER BEEN ASKED ABOUT THIS NETWORK -- a "
            "dense Zt cannot be written as a TerminationSet. Any "
            "'compute_z_matrix' total shown alongside is the DECLARED spec's "
            "answer, for comparison only, and the reconciliation residual is "
            "the declared configuration's: it checks the arithmetic these "
            "totals came out of, which is the second opinion that IS "
            "available here.")

    shunts = [i for i, e in enumerate(elements) if e.is_shunt]
    if (len(shunts) >= 2 and np.count_nonzero(Zt - np.diag(np.diag(Zt))) == 0
            and np.count_nonzero(np.diag(Zt)[shunts]) >= 2):
        notes.append(
            "The element impedance matrix is DIAGONAL over "
            f"{len(shunts)} shunt elements, i.e. every ground path is modelled "
            "as independent. Real ground balls share a return plane; an "
            "independent model understates the effective common-mode return "
            "inductance by (1 + (n-1)k). See "
            "termination_impedance_shared_return.")

    # --- 7. Structural rank of U (requirement 4), BEFORE any cond(G) talk.
    #
    # On the REDUCED node space, because that is the U that H = Zt + G is built
    # from: entries there are still exactly 0 / +-1 (a stamp whose two ends
    # collapsed onto one node was dropped as inert above), so the integer test
    # is exact.
    dependent = _dependent_columns(np.rint(U.real).astype(np.int64))
    if dependent:
        who = ", ".join(elements[c].describe() for c in dependent)
        notes.append(
            f"The spec is REDUNDANT: {who} " + ("is" if len(dependent) == 1
                                                else "are")
            + " an exact combination of the elements declared before "
              + ("it" if len(dependent) == 1 else "them")
            + " (the same constraint written twice). The total is unaffected; "
              "the split between the elements involved is only unique if they "
              "carry different impedances.")

    cond_G = _cond(Gm)

    ctx = AttribContext(
        freq_hz=f, requested_hz=float(freq_hz), omega=omega,
        port_names=list(port_names), elements=elements, groups={},
        Ybase=Ybase, Ybase0=Ybase0, Pmat=Pmat, U=U, U0=U0,
        U0_folded=U0_folded, W=W, W0=W0,
        Gm=Gm, Pmat_b=Pb, Rmat=Rm, Dmat=Dm, Zt=Zt, z_declared=z_declared,
        folded=folded_elements, dropped=dropped, dependent=dependent,
        baseline_singular=baseline_singular, bad_probes=bad_probes,
        cond_Ybase=cond_Y, cond_Ybase_eff=cond_Y_eff, cond_G=cond_G,
        reciprocity_rel=recip,
        Zref=Zref, Zop=np.zeros((G, G), dtype=complex),
        core_warnings=list(core_warnings), notes=notes, warnings=warnings,
        is_whatif=is_whatif,
    )

    groups: dict[str, list[int]] = {}
    for e in elements:
        groups.setdefault(e.source, []).append(e.index)
    ctx.groups = {label: tuple(v) for label, v in groups.items()}

    # --- 8. Two evaluations, and BOTH are needed.
    #
    # Zop is the configuration the caller asked about.  Zop_declared is the one
    # the spec declares, and it is the ONLY thing that may be reconciled
    # against Zref: with a what-if `zt` the two matrices describe different
    # networks, and comparing them measures a difference the caller designed in
    # and calls it an algorithm disagreement.  Measured on diff_pair_4port.s4p
    # (probes 1/2, grounds 3/4, 5 GHz): a shared 1 nH return doubles M, so the
    # residual read 1.01, the split was WITHHELD, and requirement 2's headline
    # feature could never produce one -- while the total it printed, 2.026 nH,
    # was right all along.
    #
    # The declared evaluation costs one extra O(m^3 + m^2 G) solve, which on a
    # 60-ball spec is ~7e4 flops, i.e. nothing next to the O(N^3) baseline
    # already built above.
    live_all = tuple(range(m))
    ctx.Zop = _z_matrix(ctx, ctx.Zt, live_all)[0]
    ctx.Zop_declared = (ctx.Zop if not is_whatif
                        else _z_matrix(ctx, Zt_declared, live_all)[0])
    return ctx


# ---------------------------------------------------------------------------
# The solve, for an arbitrary Zt and an arbitrary subset of live elements
# ---------------------------------------------------------------------------

def _z_matrix(ctx: AttribContext, Zt: np.ndarray,
              live: Sequence[int]) -> tuple[np.ndarray, np.ndarray, bool]:
    """
    (Z, currents, used_lstsq) for the configuration (Zt restricted to `live`).

    `currents` is (len(live), G): one column per possible aggressor, so a
    caller that drives port b reads column b.

    `live` names the elements that are PRESENT; an element left out is OPEN,
    which is handled by dropping its row and column rather than by a large
    number.  This is the one place a configuration is evaluated: the
    decomposition, every sensitivity and every sweep endpoint go through it, so
    they cannot drift apart in what they mean by "the network".

    Reusing ctx's Zbase products makes this O(m^3 + m^2 G) -- a 60-ball spec is
    ~7e4 flops -- against O(N^3) to rebuild the baseline.  That is the whole
    of the "fast path"; the tests check it against an honest rebuild of the
    TerminationSet through compute_z_matrix.
    """
    live = np.asarray(list(live), dtype=np.intp)
    G = len(ctx.port_names)
    if live.size == 0:
        return ctx.Dmat.copy(), np.zeros((0, G), dtype=complex), False
    ix = np.ix_(live, live)
    H = Zt[ix] + ctx.Gm[ix]
    Pb = ctx.Pmat_b[live, :]
    Rm = ctx.Rmat[live, :]
    I, used = _solve_or_lstsq(H, Pb)
    Z = ctx.Dmat - Rm.T @ I
    return Z, I, used


def _zab(ctx: AttribContext, a: int, b: int, Zt: np.ndarray,
         live: Sequence[int]) -> complex:
    return complex(_z_matrix(ctx, Zt, live)[0][a, b])


def _live_all(ctx: AttribContext) -> tuple[int, ...]:
    return tuple(range(ctx.n_elements))


def _zt_with(ctx: AttribContext, changed: Sequence[int],
             z: complex, z_ret: complex = 0j) -> np.ndarray:
    """
    ctx.Zt with `changed` replaced by an impedance z, sharing a return z_ret.

    Both the diagonal and the off-diagonals of the changed rows/columns are
    rewritten: a what-if replaces the element with a two-terminal component of
    impedance z, and leaving its mutual terms to some other element behind
    would model an ideal short that is still magnetically coupled to its
    neighbours -- which is not what "what if I ground this ball properly"
    means.  Pass a full `zt` to build_context if you want the mutuals kept.

    `z_ret` is the requirement-2 escape from that default.  With it 0 -- which
    is what every caller got before it existed -- the replaced elements are
    modelled as INDEPENDENT, and that is precisely the model requirement 2
    exists to warn against: measured on diff_pair_4port.s4p at 5 GHz with both
    far-end ports grounded, "what if each of these two leads were 1 nH" answers
    1.012 nH while "what if the two leads shared one 1 nH return" answers
    2.026 nH -- 6.03 dB, from the same question asked two ways.  Non-zero
    z_ret puts z_ret on every changed-changed pair, i.e. the same
    diag(z) + z_ret*ones block termination_impedance_shared_return builds.

    It is applied only among the CHANGED elements: an element the caller did
    not touch keeps its declared impedance and shares nothing, because the
    question asked was about the ones named.
    """
    Zt = ctx.Zt.copy()
    ch = list(changed)
    for e in ch:
        Zt[e, :] = 0.0
        Zt[:, e] = 0.0
    for e in ch:
        Zt[e, e] = z
    if z_ret != 0 and ch:
        # The whole changed block, diagonal included -- exactly the
        # diag(z) + z_ret*ones(k, k) that termination_impedance_shared_return
        # builds, so the two spellings of one network agree bit for bit.  No
        # `len(ch) > 1` guard: a one-element "shared return" is z + z_ret in
        # the dense builder too, and silently ignoring it for k == 1 would make
        # the two disagree in the one case a caller can least easily check.
        ix = np.ix_(np.asarray(ch, dtype=np.intp), np.asarray(ch, dtype=np.intp))
        Zt[ix] = Zt[ix] + complex(z_ret)
    return Zt


def _shared_return_note(ctx: AttribContext, changed: Sequence[int],
                        z_ret: complex) -> list[str]:
    """
    The independent-leads warning, for a what-if that changed several elements.

    build_context's DIAGONAL note only ever inspects `ctx.Zt`, so it cannot see
    a what-if -- and a what-if is exactly where the model is chosen rather than
    inherited.  Same physics, same factor: n independent leads understate the
    effective common-mode return inductance by (1 + (n-1)k).
    """
    if z_ret != 0:
        return []
    shunts = [e for e in changed if ctx.elements[e].is_shunt]
    if len(shunts) < 2:
        return []
    return ["This what-if replaced %d shunt elements with INDEPENDENT "
            "impedances (no shared return). Real ground balls share a return "
            "plane, which understates the common-mode return inductance by "
            "(1 + (n-1)k) -- measured at 6.03 dB on a two-ball spec. Ask the "
            "shared-return question too before believing this one."
            % len(shunts)]


# ---------------------------------------------------------------------------
# Quantity mapping
# ---------------------------------------------------------------------------

def _scale_from(ctx: AttribContext, spec: _QuantitySpec, a: int, b: int,
                Z: np.ndarray) -> tuple[float, list[str]]:
    """
    The quantity's fixed real scalar, taken from the matrix `Z` OF THE
    CONFIGURATION BEING EVALUATED.

    Requirement 8 asks for a fixed real scalar times an R-linear functional of
    Z_ab "evaluated at ONE configuration".  Fixed means fixed WITHIN one
    evaluation -- it is what keeps the per-element terms additive -- and it
    does NOT mean frozen at the declared spec while the configuration changes
    underneath it.  L_a and L_b are properties of the network, and every
    sensitivity, group and leave-one-out row is a DIFFERENT network.

    Measured on diff_pair_4port.s4p at 5 GHz (probes 1/2, grounds 3/4), the
    element being `ground port 3` opened:

        M/L_a  frozen at the declared L_a : +0.100227
        M/L_a  of the network being asked : -0.000997     <- sign flipped, 100x
        k      frozen at the declared L_a : +0.100227
        k      of the network being asked : NaN (L_a = -505 nH)

    The `open` row is the one that matters: opening that ground takes L_a
    negative, so extract_coupling_at_freq's own rule makes k undefined -- and
    the frozen scale printed a plausible positive number instead.
    """
    notes: list[str] = []
    om = ctx.omega
    if spec.scale_kind == "one":
        return 1.0, notes
    if om == 0.0:
        notes.append("omega == 0: this quantity is undefined at DC.")
        return float("nan"), notes
    if spec.scale_kind == "inv_omega":
        return 1.0 / om, notes
    La = float(np.imag(Z[a, a])) / om
    if spec.scale_kind == "inv_ImZaa":
        if not math.isfinite(La) or La == 0.0:
            notes.append(
                f"L_a = {La!r} H for '{ctx.port_names[a]}': M/L_a is "
                "undefined here.")
            return float("nan"), notes
        return 1.0 / (om * La), notes
    Lb = float(np.imag(Z[b, b])) / om
    if not (math.isfinite(La) and La > 0.0 and math.isfinite(Lb) and Lb > 0.0):
        notes.append(
            "k is undefined here: L_a = %s, L_b = %s (k needs both > 0, the "
            "same rule extract_coupling_at_freq applies)."
            % (format_si(La, "H"), format_si(Lb, "H")))
        return float("nan"), notes
    return 1.0 / (om * math.sqrt(La * Lb)), notes


def _quantity_scale(ctx: AttribContext, spec: _QuantitySpec,
                    a: int, b: int) -> tuple[float, list[str]]:
    """
    The scale for the configuration the CONTEXT describes.

    That is `ctx.Zref` -- compute_z_matrix's authoritative matrix -- so that
    M/L_a and k mean exactly what the results pane and the CSV already print,
    rather than a second, slightly different self inductance.  With a what-if
    `zt` in force there is no engine matrix for the network in question, so
    this module's own evaluation of it is the only honest source.
    """
    return _scale_from(ctx, spec, a, b,
                       ctx.Zop if ctx.is_whatif else ctx.Zref)


def _map_value(spec: _QuantitySpec, scale: float, z: complex) -> complex:
    if spec.part == "complex":
        return scale * z
    if spec.part == "re":
        return complex(scale * z.real, 0.0)
    return complex(scale * z.imag, 0.0)


# ---------------------------------------------------------------------------
# decompose
# ---------------------------------------------------------------------------

def decompose(ctx: AttribContext, victim: int | str, aggressor: int | str,
              quantity: str = "M") -> Decomposition:
    """
    Split `quantity` of Z(victim, aggressor) into the bare EM coupling plus one
    signed term per declared element.

    Exact, not first-order: the terms sum to the total, and the sum is checked
    against compute_z_matrix's own value with a condition-aware tolerance.
    """
    spec = _resolve_quantity(quantity)
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    notes: list[str] = list(ctx.notes)
    warns: list[str] = list(ctx.warnings)

    scale, scale_notes = _quantity_scale(ctx, spec, a, b)
    notes.extend(scale_notes)

    live = _live_all(ctx)
    Z, Iall, used_lstsq = _z_matrix(ctx, ctx.Zt, live)
    # Iall is (m, G) -- one column per possible aggressor.  Only the column of
    # the port actually being driven is a physical current.
    I = Iall[:, b] if Iall.size else np.zeros(0, dtype=complex)
    z_ab = complex(Z[a, b])
    direct = complex(ctx.Dmat[a, b])
    r_a = ctx.Rmat[:, a]

    if used_lstsq:
        warns.append(
            "H = Zt + G is singular, so the element currents are the "
            "MINIMUM-NORM solution: the total is still exact (the ambiguity "
            "lies in null(U), which the victim cannot see), but the split "
            "between the redundant elements is one of many.")

    # --- the terms
    raw: list[tuple[Element | None, complex, complex, complex]] = [
        (None, direct, _UNDEFINED, _UNDEFINED)]
    for e, elem in enumerate(ctx.elements):
        raw.append((elem, -complex(I[e]) * complex(r_a[e]),
                    complex(I[e]), complex(r_a[e])))

    mapped = [(el, _map_value(spec, scale, c), cur, tz)
              for el, c, cur, tz in raw]
    total_sum = _map_value(spec, scale, z_ab)
    # The reference is the DECLARED network's answer, so it takes the DECLARED
    # network's scale -- Zref[a, b] over a what-if's sqrt(L_a L_b) belongs to
    # neither.  Measured under a shared 1 nH return: `k`'s reference printed
    # 0.16716, which is the declared mutual divided by the modelled self
    # inductances, where the declared spec's own k is 0.200952.  Identical to
    # `scale` for every quantity whose divisor is 1 or 1/omega, and for every
    # context that is not a what-if.
    ref_scale, _ = (_scale_from(ctx, spec, a, b, ctx.Zref) if ctx.is_whatif
                    else (scale, None))
    total_ref = _map_value(spec, ref_scale, complex(ctx.Zref[a, b]))

    # --- reconciliation (requirement 5), measured on the complex Z_ab, which
    # is the object both algorithms actually compute.
    #
    # It compares TWO ALGORITHMS ON ONE NETWORK, and `ctx.Zop_declared` is
    # therefore the left-hand side, not `z_ab`.  The two are the same object
    # unless the caller passed a what-if `zt`, in which case `z_ab` is a
    # different NETWORK and the difference is one the caller designed in --
    # reporting it as an algorithm disagreement is how requirement 2's headline
    # feature came to withhold its own split (measured: a shared 1 nH return
    # doubles M, residual 1.01, terms gone, while the 2.026 nH it printed was
    # correct).  What is genuinely lost under a what-if is the second OPINION,
    # not the check: the arithmetic is validated on the declared spec, and
    # `reference_applicable` says the engine was never asked about this one.
    H_full = ctx.Zt + ctx.Gm
    cond_H = _cond(H_full)
    ref = complex(ctx.Zref[a, b])
    z_check = complex(ctx.Zop_declared[a, b])
    denom = abs(ref)
    if denom == 0.0 or not math.isfinite(denom):
        # An exactly-zero mutual has no relative error.  Normalise by the
        # largest entry of Z instead -- that is the scale the arithmetic was
        # done at -- and say so, rather than reporting NaN for a pair the two
        # algorithms agree about perfectly (decap_4port.s4p's two pi networks
        # are uncoupled by construction: Z_ab is 0, not small).
        finite = np.abs(ctx.Zref)[np.isfinite(np.abs(ctx.Zref))]
        mscale = float(finite.max()) if finite.size else 0.0
        if math.isfinite(denom) and mscale > 0.0:
            denom = mscale
            resid = abs(z_check - ref) / mscale
            notes.append(
                "Z_ab is exactly zero, so the residual below is normalised by "
                f"the largest entry of Z ({format_si(mscale, 'Ohm')}) instead "
                "of by the answer.")
        else:
            resid = float("nan")
    else:
        resid = abs(z_check - ref) / denom

    # cond_*_EFFECTIVE, not cond: a redundantly-spelled spec makes H exactly
    # singular (measured: cond(H) = 1.3e17 for `2 ground / 4 ground /
    # 2 short_to 4`) and the minimum-norm solve is still exact along everything
    # the victim can see, so the bound must be built from the retained
    # spectrum.  Likewise for a deliberately floating baseline.
    S = max([float(np.max(np.abs(M))) if M.size else 0.0
             for M in (ctx.Dmat, ctx.Gm, ctx.Pmat_b, ctx.Rmat)] + [denom])
    amp = (S / denom) if denom > 0 else float("inf")
    floor = max(RESIDUAL_FLOOR_MIN,
                RESIDUAL_SAFETY * (ctx.cond_Ybase_eff
                                   + _cond_effective(H_full)) * _EPS * amp)
    if floor >= 1.0:
        floor = 1.0
        notes.append(
            "The condition numbers and the 1:%.3g scale ratio between the "
            "largest intermediate quantity and this answer mean floating "
            "point guarantees NOTHING here: the error bound exceeds the "
            "answer. The measured residual is the only evidence, and it is "
            "evidence about this run, not a bound." % amp)
    # A NaN residual is NOT a pass.  It means nothing was checked at all, and
    # the two cases that produce one are exactly the cases where this module is
    # most convincing and most wrong: measured on coupled_4port_float.s4p with
    # only one of the two coils referenced, compute_z_matrix says NaN ("'c2'
    # has no return path") while this module folds the single ground in and
    # reports 400.000 pH -- exactly half the fixture's real 800 pH.  A caller
    # gating on `split_trustworthy` got a green light for it.
    trustworthy = math.isfinite(resid) and resid <= RESIDUAL_CATASTROPHIC
    if math.isfinite(resid) and resid > floor:
        warns.append(
            "Reconciliation: this decomposition sums to %.6g%+.6gj Ohm while "
            "compute_z_matrix says %.6g%+.6gj Ohm -- %.3g relative, against an "
            "achievable floor of %.3g for cond(Ybase)=%.3g and cond(H)=%.3g. "
            "The engine's value is the authoritative one."
            % (z_check.real, z_check.imag, ref.real, ref.imag, resid, floor,
               ctx.cond_Ybase, cond_H))
    if not math.isfinite(resid):
        warns.append(
            "Reconciliation could not be measured: compute_z_matrix reports "
            f"Z_ab = {ref} for this pair. Nothing here has been checked "
            "against a second algorithm, so the per-element split is WITHHELD "
            "-- an unchecked split is not a checked one that happened to "
            "pass.")
    if math.isfinite(resid) and resid > RESIDUAL_CATASTROPHIC:
        if amp > 1.0 / math.sqrt(_EPS):
            # "The two algorithms disagree" is the wrong story when both
            # numbers are roundoff.  Measured on diff_pair_4port.s4p with
            # port 3 shorted onto the probe at port 1: Z_ab reads -1.7e-11 Ohm
            # against a 1.6e4 Ohm diagonal, the two algorithms differ by 400%,
            # and neither value means anything.
            warns.append(
                "Z_ab is %.3g times smaller than the largest entry of Z, i.e. "
                "at the arithmetic noise floor: the two algorithms differ by "
                "%.3g relative and NEITHER value is a measurement. The "
                "per-element split is WITHHELD; there is nothing to "
                "attribute." % (amp, resid))
        else:
            warns.append(
                "The per-element split is WITHHELD: at %.3g relative the two "
                "algorithms disagree about the answer itself, so apportioning "
                "it between elements would be fiction. The totals above "
                "stand." % resid)

    # --- shares (requirement 7)
    term_scale = max([abs(total_sum)] + [abs(c) for _, c, _, _ in mapped])
    abs_resid = abs(total_sum) * resid if math.isfinite(resid) else 0.0
    suppress = abs(total_sum) <= max(SHARE_ZERO_REL * term_scale, abs_resid)
    terms: list[Term] = []
    for el, c, cur, tz in mapped:
        if suppress or total_sum == 0:
            si = sq = float("nan")
        else:
            denom2 = abs(total_sum) ** 2
            si = float((c * total_sum.conjugate()).real) / denom2
            sq = float((c * total_sum.conjugate()).imag) / denom2
        terms.append(Term(el, c, cur, tz, si, sq))
    if suppress:
        notes.append(
            "The share column is suppressed: |total| = %.3g is at or below the "
            "level of the terms' own cancellation (largest term %.3g, "
            "reconciliation residual %.3g), so a percentage of it would be "
            "noise divided by noise."
            % (abs(total_sum), term_scale, abs_resid))
    if not trustworthy:
        terms = []

    budget = _return_budget(ctx, b, I, live)
    ref_note = budget.note
    if ctx.folded:
        ref_note += " " + next(
            (nn for nn in ctx.notes if nn.startswith("Port(s)")), "")

    if ctx.dropped:
        notes.append(
            "Declared but NOT in the split (they contribute exactly nothing): "
            + "; ".join(f"{el.describe()} -- {why}" for el, why in ctx.dropped))
    notes.append(
        "This is a decomposition of the DECLARATIONS in the spec, not a "
        "ranking of ports: an open port contributes no element and no term. "
        "It also depends on how the spec is spelled -- '6:1:14 ground' and "
        "'6 short_to 7:1:14' + '6 ground' are the same network and decompose "
        "differently.")
    for w in ctx.core_warnings:
        warns.append("compute_z_matrix: " + w)

    # C_c, as a TOTAL and never per term.  The refusal message in
    # NON_DECOMPOSABLE promises this number exists, and for a while it did not:
    # a reader following "C_c is still reported as a TOTAL" found nothing on
    # the object and nothing in the report.  It is computed from the ANSWERED
    # network (z_ab), not from Zref, so a what-if reads the capacitance of the
    # network it just described.
    c_c = _UNDEFINED
    if ctx.omega != 0.0 and z_ab.imag != 0.0 and math.isfinite(z_ab.imag):
        c_c = complex(-1.0 / (ctx.omega * z_ab.imag), 0.0)

    return Decomposition(
        victim=ctx.port_names[a], aggressor=ctx.port_names[b],
        freq_hz=ctx.freq_hz, requested_hz=ctx.requested_hz,
        quantity=spec.name, unit=spec.unit,
        total_reference=total_ref, total_sum=total_sum,
        residual_rel=resid, residual_floor=floor,
        terms=terms, return_budget=budget, reference_note=ref_note,
        notes=notes, warnings=warns,
        split_trustworthy=trustworthy, share_suppressed=suppress,
        cond_Ybase=ctx.cond_Ybase, cond_H=cond_H,
        reciprocity_rel=ctx.reciprocity_rel, Z_ab=z_ab,
        reference_applicable=not ctx.is_whatif, C_c_total=c_c,
    )


def _return_budget(ctx: AttribContext, b: int, I: np.ndarray,
                   live: Sequence[int]) -> ReturnBudget:
    """
    Requirement 6, computed from the identity Ybase V = w_b - U I rather than
    from a second solve.

    The folded elements' currents are recovered by least squares from the same
    identity in the ORIGINAL node space, so a ground the baseline absorbed
    still counts as a declared return path instead of silently inflating the
    EM reference's share.
    """
    live = list(live)
    nr = ctx.Ybase.shape[0]
    if nr:
        rhs = ctx.W[:, b].copy()
        if len(live):
            rhs = rhs - ctx.U[:, live] @ I
        Zb_rhs, _ = _solve_or_lstsq(ctx.Ybase, rhs.reshape(-1, 1))
        V_red = Zb_rhs[:, 0]
    else:
        V_red = np.zeros(0, dtype=complex)
    V_full = ctx.Pmat @ V_red
    inj = ctx.Ybase0 @ V_full
    em = float(abs(np.sum(inj)))

    shunt_currents = [abs(I[j]) for j, e in enumerate(live)
                      if ctx.elements[e].is_shunt]
    declared = float(sum(shunt_currents))
    declared_all = float(np.sum(np.abs(I))) if len(live) else 0.0
    if ctx.folded:
        # A folded element is inside the baseline and has no term, but it is
        # still a DECLARED return path -- counting its current as part of the
        # EM reference's share would overstate exactly the thing this budget
        # exists to measure.  Recover it from the same KCL identity in the
        # ORIGINAL node space: Ybase0 V = w_b - U_active I - U_folded I_folded.
        resid_rhs = ctx.W0[:, b] - inj
        if len(live):
            resid_rhs = resid_rhs - ctx.U0[:, live] @ I
        I_f, *_ = np.linalg.lstsq(ctx.U0_folded, resid_rhs, rcond=None)
        for el, cur in zip(ctx.folded, I_f):
            declared_all += float(abs(cur))
            if el.kind in _SHUNT_KINDS:
                declared += float(abs(cur))

    tot = em + declared
    frac = (em / tot) if tot > 0 else float("nan")
    dominant = bool(math.isfinite(frac) and frac > RETURN_EM_DOMINANT)
    if tot <= RETURN_NO_NET_CURRENT:
        # A fully differential aggressor injects +1 A and takes 1 A back out of
        # the SAME measurement port, so 1^T w_b == 0: there is no net current
        # to apportion and both sides of the budget are roundoff.  Saying "the
        # EM reference carries 100%" of 0.1 fA would be arithmetically true and
        # completely misleading.
        return ReturnBudget(
            em, declared, declared_all, float("nan"), False,
            "Return-path budget: the aggressor drive is BALANCED (+1 A into "
            "its + side and back out of its - side) and no declared element "
            "carries a net current, so there is no return current to "
            f"apportion (both sides measure {format_si(tot, 'A')}).")
    if dominant:
        note = (
            "Return-path budget: the EM model's own reference carries "
            f"{100.0 * frac:.2f}% of the aggressor's return current "
            f"({format_si(em, 'A')}) and the declared elements "
            f"{100.0 * (1 - frac):.2f}% ({format_si(declared, 'A')}). "
            "The return path is INSIDE the EM model; this decomposition "
            "cannot separate it, and cannot by itself confirm or refute a "
            "'forward path minus return path' explanation.")
    else:
        note = (
            "Return-path budget: the declared elements carry "
            f"{100.0 * (1 - frac):.2f}% of the aggressor's return current "
            f"({format_si(declared, 'A')}) and the EM model's own reference "
            f"{100.0 * frac:.2f}% ({format_si(em, 'A')}).")
    return ReturnBudget(em, declared, declared_all, frac, dominant, note)


# ---------------------------------------------------------------------------
# Sensitivity (requirement 9)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SensitivityResult:
    kind: str
    label: str
    elements: tuple[int, ...]
    alternative: str
    quantity: str
    unit: str
    baseline_value: complex
    new_value: complex
    delta: complex
    delta_db: float

    @property
    def abs_delta(self) -> float:
        return float(abs(self.delta))


def _delta_db(base: complex, new: complex) -> float:
    if base == 0 or not math.isfinite(abs(base)) or not math.isfinite(abs(new)):
        return float("nan")
    if new == 0:
        return float("-inf")
    return 20.0 * math.log10(abs(new) / abs(base))


def _evaluate(ctx: AttribContext, a: int, b: int, spec: _QuantitySpec,
              changed: Sequence[int], alt: Alternative,
              z_ret: complex = 0j) -> complex:
    """
    Exact value of the quantity with `changed` replaced by `alt`.

    The scale is recomputed from THIS configuration's own (G, G) matrix -- see
    `_scale_from`.  L_a and L_b belong to the network, and every row here is a
    different network; freezing them at the declared spec made `M/L_a` come out
    sign-flipped and 100x wrong on the very first alternative (`open`).
    """
    changed = list(changed)
    if alt.is_open:
        live = [e for e in range(ctx.n_elements) if e not in set(changed)]
        Zt = ctx.Zt
    else:
        live = list(range(ctx.n_elements))
        Zt = _zt_with(ctx, changed, complex(alt.z), z_ret)
    Z = _z_matrix(ctx, Zt, live)[0]
    scale, _ = _scale_from(ctx, spec, a, b, Z)
    return _map_value(spec, scale, complex(Z[a, b]))


def _baseline_value(ctx: AttribContext, a: int, b: int,
                    spec: _QuantitySpec) -> complex:
    """
    The quantity at the configuration in force, scaled by that configuration.

    `ctx.Zop`, never `ctx.Zref`: a what-if context's baseline is the what-if,
    and a delta measured from the wrong baseline is wrong twice.
    """
    scale, _ = _scale_from(ctx, spec, a, b, ctx.Zop)
    return _map_value(spec, scale, complex(ctx.Zop[a, b]))


def sensitivity(ctx: AttribContext, victim: int | str, aggressor: int | str,
                alternatives: Sequence[Alternative] | None = None,
                quantity: str = "M",
                elements: Sequence[int] | None = None
                ) -> list[SensitivityResult]:
    """
    Exact per-element swap to each candidate termination.

    Not a derivative: every row is a full re-solve of the network with that one
    element replaced, so a 60 dB change is reported as 60 dB and not as a
    first-order slope that stopped being true two decades ago.

    No `z_ret` here on purpose: one element has no return to share, and a
    shared-return model is a statement about a GROUP.  See `group_joint`.
    """
    spec = _resolve_quantity(quantity)
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    alts = list(alternatives) if alternatives is not None \
        else default_alternatives(ctx.omega)
    idxs = list(range(ctx.n_elements)) if elements is None \
        else [ctx.element_index(e) for e in elements]

    base = _baseline_value(ctx, a, b, spec)
    out: list[SensitivityResult] = []
    for e in idxs:
        for alt in alts:
            new = _evaluate(ctx, a, b, spec, [e], alt)
            out.append(SensitivityResult(
                "element", ctx.elements[e].describe(), (e,), alt.name,
                spec.name, spec.unit, base, new, new - base,
                _delta_db(base, new)))
    return out


@dataclass(frozen=True)
class GroupResult:
    label: str
    elements: tuple[int, ...]
    alternative: str
    quantity: str
    unit: str
    baseline_value: complex
    joint_value: complex
    joint_delta: complex
    sum_individual: complex
    non_additivity: complex
    individual: tuple[SensitivityResult, ...]
    notes: tuple[str, ...] = ()


def group_joint(ctx: AttribContext, victim: int | str, aggressor: int | str,
                group: str | Sequence, alternative: Alternative,
                quantity: str = "M", z_ret: complex = 0j) -> GroupResult:
    """
    Change a WHOLE group at once, exactly, and report how far that is from the
    sum of the one-at-a-time changes.

    This is the case single-element sensitivity cannot see.  With 60 ground
    balls every individual delta is about zero -- the other 59 already carry
    the return -- and so is every pairwise second difference: the effect is
    order-60, not order-2.  The connection-table rows already define the
    groups, so the joint evaluation costs one extra solve.

    `z_ret` is requirement 2 on the what-if side.  With it left at 0 the whole
    group is replaced by INDEPENDENT impedances, which is the model requirement
    2 exists to warn against; `notes` says so whenever two or more shunt
    elements were changed that way, because the default answer is 6.03 dB
    (measured, two balls, 1 nH) away from the shared one.
    """
    spec = _resolve_quantity(quantity)
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    idxs = ctx.group_indices(group)
    label = group if isinstance(group, str) else \
        "+".join(ctx.elements[i].describe() for i in idxs)

    base = _baseline_value(ctx, a, b, spec)
    joint = _evaluate(ctx, a, b, spec, idxs, alternative, z_ret)
    singles = sensitivity(ctx, a, b, [alternative], quantity, idxs)
    ssum = sum((s.delta for s in singles), start=complex(0.0))
    return GroupResult(
        label, tuple(idxs), alternative.name, spec.name, spec.unit,
        base, joint, joint - base, ssum, (joint - base) - ssum, tuple(singles),
        tuple(_shared_return_note(ctx, idxs, z_ret)))


@dataclass(frozen=True)
class CumulativeCurve:
    quantity: str
    unit: str
    alternative: str
    baseline_value: complex
    order: tuple[int, ...]
    k: tuple[int, ...]
    values: tuple[complex, ...]
    deltas: tuple[complex, ...]
    sum_individual: tuple[complex, ...]
    non_additivity: tuple[complex, ...]
    notes: tuple[str, ...] = ()


def cumulative_curve(ctx: AttribContext, victim: int | str,
                     aggressor: int | str, alternative: Alternative,
                     quantity: str = "M",
                     ks: Sequence[int] | None = None,
                     z_ret: complex = 0j) -> CumulativeCurve:
    """
    Rank the elements by their single-element delta, then evaluate with the top
    k = 1, 2, 4, 8, 16, ... changed TOGETHER.

    Greedy by the one-at-a-time ranking on purpose: the true optimal-k subset is
    combinatorial, and the point of the curve is to show the order-of-the-group
    effect that per-element numbers hide, not to find an optimum.
    """
    spec = _resolve_quantity(quantity)
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    base = _baseline_value(ctx, a, b, spec)

    singles = sensitivity(ctx, a, b, [alternative], quantity)
    order = [s.elements[0] for s in
             sorted(singles, key=lambda s: -s.abs_delta)]
    m = len(order)
    if ks is None:
        seq: list[int] = []
        k = 1
        while k < m:
            seq.append(k)
            k *= 2
        if m:
            seq.append(m)
    else:
        seq = [int(k) for k in ks if 0 < int(k) <= m]

    by_elem = {s.elements[0]: s.delta for s in singles}
    vals: list[complex] = []
    dels: list[complex] = []
    sums: list[complex] = []
    nonadd: list[complex] = []
    note_at: list[str] = []
    for k in seq:
        subset = order[:k]
        v = _evaluate(ctx, a, b, spec, subset, alternative, z_ret)
        s = sum((by_elem[e] for e in subset), start=complex(0.0))
        vals.append(v)
        dels.append(v - base)
        sums.append(s)
        nonadd.append((v - base) - s)
        if not note_at:
            note_at = _shared_return_note(ctx, subset, z_ret)
    return CumulativeCurve(
        spec.name, spec.unit, alternative.name, base, tuple(order),
        tuple(seq), tuple(vals), tuple(dels), tuple(sums), tuple(nonadd),
        tuple(note_at))


def leave_one_out(ctx: AttribContext, victim: int | str,
                  aggressor: int | str, quantity: str = "M"
                  ) -> list[SensitivityResult]:
    """
    Start from EVERY element ideal (every ground a perfect ground, every short
    a perfect short) and remove one at a time.

    Often more informative than one-at-a-time from all-open: from all-open the
    first ground you add changes everything and the rest change nothing, while
    from all-ideal the number that moves is the one that was actually carrying
    something.
    """
    spec = _resolve_quantity(quantity)
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    m = ctx.n_elements
    Zt_ideal = np.zeros((m, m), dtype=complex)

    def _val(live: Sequence[int]) -> complex:
        Z = _z_matrix(ctx, Zt_ideal, live)[0]
        sc, _ = _scale_from(ctx, spec, a, b, Z)
        return _map_value(spec, sc, complex(Z[a, b]))

    base = _val(tuple(range(m)))
    out: list[SensitivityResult] = []
    for e in range(m):
        live = [i for i in range(m) if i != e]
        new = _val(live)
        out.append(SensitivityResult(
            "leave_one_out", ctx.elements[e].describe(), (e,),
            "removed (from all-ideal)", spec.name, spec.unit,
            base, new, new - base, _delta_db(base, new)))
    return out


# ---------------------------------------------------------------------------
# The impedance sweep, in closed form (requirement 10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sweep:
    """
    Z_ab as an exact rational function of ONE real parameter t >= 0, with the
    element(s) set to z = t * z_unit.

    For a single element that is a Mobius map (alpha + beta t)/(gamma + delta t)
    and the whole business is closed form: both endpoints, the interval, and
    the extremum.  A Mobius map takes the real line to a circular arc, so the
    extremum of any R-linear functional along it is analytic.

    THE CANONICAL FORM IS THE PARTIAL FRACTION, NOT THE POLYNOMIAL.

        Z(t) = c0 - sum_j  residues[j] / (lam[j] + t)

    `lam` are the eigenvalues of K / z_unit, so the poles sit at t = -lam[j].
    Everything -- `value_at`, both endpoints, every extremum candidate -- is
    evaluated from this form, and NOTHING goes through `num` / `den`.  That is
    not a style preference.  The expanded coefficients are products of |S|
    eigenvalues, and with param="L" every lam is of order 1e-9, so the constant
    term is ~1e-9^|S|: measured on a synthetic package sweeping one ground
    group, den[-1] hits 5.98e-273 at 30 balls, 3.7e-309 at 34, and exactly 0 at
    36 -- at which point `value_ideal` printed +inf, then NaN at 38, then NaN
    for the whole curve at 60, with `method` still reading "closed-form" and
    `notes` empty.  Requirement 9 is written around 60 ground balls.

    In the partial-fraction form both endpoints are trivially exact and
    overflow-free: t = 0 gives c0 - sum(residues / lam) and t -> inf gives
    exactly c0.

    `num` / `den` are kept because a degree-|S| rational function is a useful
    thing to be handed, but they are DIAGNOSTIC ONLY, and they are emptied
    rather than left holding underflowed garbage when the expansion cannot
    survive (see `notes`).

    `leaves_bracket` is not a curiosity: a series L resonates with the
    package's shunt C and the quantity can leave the [ideal, open] bracket
    entirely, which is exactly the case a two-point "best case / worst case"
    estimate gets wrong.
    """
    elements: tuple[int, ...]
    quantity: str
    unit: str
    param_name: str
    param_unit: str
    z_unit: complex
    num: tuple[complex, ...]      # numerator coefficients, highest power first
    den: tuple[complex, ...]
    alpha: complex
    beta: complex
    gamma: complex
    delta: complex
    value_ideal: complex
    value_open: complex
    interval: tuple[float, float]
    arg_min: float
    arg_max: float
    bracket: tuple[float, float]
    leaves_bracket: bool
    samples: tuple[np.ndarray, np.ndarray] | None
    method: str
    scale: float
    part: str
    notes: tuple[str, ...]
    c0: complex = 0j
    lam: tuple[complex, ...] = ()
    residues: tuple[complex, ...] = ()

    def value_at(self, t: float) -> complex:
        """The raw complex Z_ab at parameter t, from the partial fractions."""
        return _pf_value(self.c0, self.lam, self.residues, t)

    def quantity_at(self, t: float) -> complex:
        """`quantity` at parameter t, in `unit` -- what `interval` bounds."""
        z = self.value_at(t)
        if self.part == "re":
            return complex(self.scale * z.real, 0.0)
        if self.part == "im":
            return complex(self.scale * z.imag, 0.0)
        return self.scale * z


def _pf_value(c0: complex, lam: Sequence[complex],
              res: Sequence[complex], t: float) -> complex:
    """Z(t) = c0 - sum_j res_j / (lam_j + t), the overflow-free evaluation."""
    v = complex(c0)
    for lj, cj in zip(lam, res):
        d = complex(lj) + t
        if d == 0:
            return complex(float("inf"), float("inf"))
        v -= complex(cj) / d
    return v


def _pf_deriv(lam: Sequence[complex], res: Sequence[complex],
              t: float, order: int) -> complex:
    """
    d^order Z / dt^order, also from the partial fractions.

    d/dt of -c/(l+t) is +c/(l+t)^2 and d2/dt2 is -2c/(l+t)^3, which is all
    Newton needs and none of which touches an expanded coefficient.
    """
    v = 0j
    for lj, cj in zip(lam, res):
        d = complex(lj) + t
        if d == 0:
            return complex(float("inf"), float("inf"))
        v += (complex(cj) / d ** 2) if order == 1 else (-2.0 * complex(cj) / d ** 3)
    return v


_PARAM_UNITS = {"L": ("series inductance", "H"),
                "R": ("series resistance", "Ohm"),
                "1/C": ("inverse shunt capacitance", "1/F"),
                "Z": ("impedance", "Ohm")}


#: Quantities whose SCALE is itself a function of the swept parameter, and
#: which this sweep therefore refuses.  `M/L_a` and `k` divide by L_a (and
#: L_b), and L_a is a property of the network: changing a ground lead changes
#: it too.  The closed form is a rational function of t for Z_ab alone, so a
#: swept `k` could only ever be Z_ab(t) divided by the DECLARED L_a -- which is
#: the frozen-scale bug `_scale_from` exists to document, delivered as a curve.
#: Refuse it by name, the same way `_resolve_quantity` refuses C_c.
_SWEEP_REFUSED = {
    "M/L_a": "M/L_a divides by L_a, and L_a is a property of the NETWORK -- "
             "sweeping the termination changes it too. A curve of M(t) over a "
             "fixed L_a is not M/L_a(t); measured on diff_pair_4port.s4p, "
             "opening one ground takes L_a from +5.03 nH to -505 nH. Sweep 'M' "
             "or 'ImZ', or ask sensitivity() for exact M/L_a at named "
             "alternatives",
    "k": "k divides by sqrt(L_a L_b), both of which the swept termination "
         "changes; and where the sweep drives L_a negative, k is undefined "
         "rather than small (the same rule extract_coupling_at_freq applies). "
         "Sweep 'M' or 'ImZ', or ask sensitivity() for exact k at named "
         "alternatives",
}


def sweep_mobius(ctx: AttribContext, victim: int | str, aggressor: int | str,
                 element_or_group: int | str | Element | Sequence,
                 quantity: str = "M", param: str = "L",
                 z_unit: complex | None = None,
                 t_max: float | None = None,
                 samples: int = 0, z_ret: complex = 0j) -> Sweep:
    """
    Closed-form sweep of one element (or one whole group, tied to the same
    value) over t in [0, t_max], default [0, inf).

    t = 0 is the IDEAL termination and t -> inf is OPEN, and both are evaluated
    exactly rather than at "a very small" and "a very large" number.  The
    headline is `interval`: "M lies in [1.71, 3.44] pH over any physical ground
    inductance" is a statement a budget can be written against, in a way that a
    single number at one guessed L is not.

    `t_max` is what makes "physical" mean something, and it is the caller's
    call, not this module's.  Over the WHOLE half-line a Mobius map generally
    passes close to a pole, and the extremum then sits on a resonance that may
    be nowhere near a real component: measured on diff_pair_4port.s4p, sweeping
    one ground's series L over [0, inf) puts |M| at 9 mH at L = 505 nH, where
    the ground inductance anti-resonates with the 1 fF port capacitance.  That
    is arithmetically correct and useless as a budget number, so the unbounded
    result carries a note naming the parameter value and inviting a bound
    rather than quietly reporting it as the answer.
    """
    spec = _resolve_quantity(quantity)
    if spec.name in _SWEEP_REFUSED:
        raise AttribError(
            f"sweep_mobius cannot sweep '{spec.name}': "
            + _SWEEP_REFUSED[spec.name] + ".")
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    # Constant across the sweep by construction now that M/L_a and k are
    # refused: the survivors scale by 1 or by 1/omega, neither of which the
    # termination can move.
    scale, _ = _quantity_scale(ctx, spec, a, b)
    # A group LABEL wins over an element description when both would match --
    # the group is the coarser and therefore the safer reading, and an element
    # can always be named by its integer index.
    if isinstance(element_or_group, str) and element_or_group in ctx.groups:
        S = ctx.group_indices(element_or_group)
    elif isinstance(element_or_group, (int, np.integer, str, Element)):
        S = (ctx.element_index(element_or_group),)
    else:
        S = ctx.group_indices(element_or_group)
    S = tuple(sorted(set(S)))
    if not S:
        raise AttribError("sweep_mobius needs at least one element")

    if z_unit is None:
        if param == "L":
            z_unit = 1j * ctx.omega
        elif param == "R":
            z_unit = complex(1.0)
        elif param == "1/C":
            z_unit = 1.0 / (1j * ctx.omega) if ctx.omega else complex(1.0)
        else:
            z_unit = complex(1.0)
    z_unit = complex(z_unit)
    pname, punit = _PARAM_UNITS.get(param, ("parameter", ""))
    notes: list[str] = []

    m = ctx.n_elements
    T = [i for i in range(m) if i not in set(S)]
    # H0: the swept elements at z = 0, everything else exactly as declared.
    # `z_ret` is the constant part of the swept block, so it belongs in H0 and
    # the sweep stays affine in t: Zt_S(t) = t*z_unit*I + z_ret*ones.
    H0 = _zt_with(ctx, S, 0j, z_ret) + ctx.Gm
    notes.extend(_shared_return_note(ctx, S, z_ret))
    Sx = np.asarray(S, dtype=np.intp)
    Tx = np.asarray(T, dtype=np.intp)
    p = ctx.Pmat_b[:, b]
    r = ctx.Rmat[:, a]
    d = complex(ctx.Dmat[a, b])

    if Tx.size:
        Dtt = H0[np.ix_(Tx, Tx)]
        Bst = H0[np.ix_(Sx, Tx)]
        Cts = H0[np.ix_(Tx, Sx)]
        DinvC, _ = _solve_or_lstsq(Dtt, Cts)
        Dinvp, _ = _solve_or_lstsq(Dtt, p[Tx].reshape(-1, 1))
        DTinvr, _ = _solve_or_lstsq(Dtt.T, r[Tx].reshape(-1, 1))
        K = H0[np.ix_(Sx, Sx)] - Bst @ DinvC
        pt = p[Sx] - Bst @ Dinvp[:, 0]
        rt = r[Sx] - Cts.T @ DTinvr[:, 0]
        c0 = d - complex(r[Tx] @ Dinvp[:, 0])
    else:
        K = H0[np.ix_(Sx, Sx)]
        pt = p[Sx]
        rt = r[Sx]
        c0 = d

    # Z_ab(t) = c0 - rt^T (K + t*z_unit I)^-1 pt
    #         = c0 - (1/z_unit) rt^T (K/z_unit + t I)^-1 pt
    Kz = K / z_unit
    rt_z = rt / z_unit
    lam, Vv = np.linalg.eig(Kz)
    try:
        Vinv = np.linalg.inv(Vv)
        cj = (rt_z @ Vv) * (Vinv @ pt)
        ok = True
    except np.linalg.LinAlgError:                        # pragma: no cover
        cj = np.zeros(0, dtype=complex)
        ok = False

    if not ok:                                           # pragma: no cover
        lam = np.zeros(0, dtype=complex)
        cj = np.zeros(0, dtype=complex)
        method = "degenerate"
        notes.append("K is defective; the closed form fell back to a constant.")
    else:
        method = "closed-form" if len(S) == 1 else "partial-fraction"

    lam_t = tuple(complex(x) for x in lam)
    cj_t = tuple(complex(x) for x in cj)

    # Endpoints, exact and overflow-free.  t -> inf kills every 1/(lam + t)
    # term, so the OPEN value is simply c0; t = 0 is one sum.  The old spelling
    # read them off the expanded polynomial's leading and trailing
    # coefficients, which is where the 36-ball +inf and the 38-ball NaN came
    # from -- both endpoints of a curve whose interior evaluated perfectly.
    v_ideal = _map_value(spec, scale, _pf_value(c0, lam_t, cj_t, 0.0))
    v_open = _map_value(spec, scale, complex(c0))

    # num / den, DIAGNOSTIC ONLY -- nothing above or below evaluates through
    # them.  Emptied rather than left holding underflowed garbage: a caller
    # handed coefficients whose constant term is 0 because 34 factors of 1e-9
    # multiplied out would get a plausible-looking curve that is wrong at one
    # end, which is exactly the failure this whole rewrite is about.
    num, den = _expand_rational(c0, lam, cj)
    if num.size == 0:
        notes.append(
            "The expanded polynomial coefficients (`num` / `den`) are NOT "
            f"available: a degree-{len(S)} product of eigenvalues of order "
            f"{abs(lam).mean() if lam.size else 0:.3g} underflows double "
            "precision. Nothing here needs them -- the sweep is evaluated from "
            "its partial fractions (`c0`, `lam`, `residues`) -- but a caller "
            "reading them directly should know they are gone rather than "
            "small.")

    # Mobius coefficients for the rank-1 case, in the contract's spelling.
    if len(S) == 1 and num.size == 2:
        beta, alpha = complex(num[0]), complex(num[1])
        delta, gamma = complex(den[0]), complex(den[1])
    else:
        alpha = beta = gamma = delta = _UNDEFINED

    lo, hi, t_lo, t_hi, extra = _rational_extrema(
        c0, lam_t, cj_t, spec, scale, t_max)
    notes.extend(extra)
    # The bracket must be in the SAME quantity as the interval.  For a complex
    # `quantity` the interval is of |Z| (there being no order on C), so the
    # bracket has to be of |Z| too: measured on diff_pair_4port.s4p with
    # quantity='Z', the real-part spelling gave bracket (-2.49 nOhm, 376 pOhm)
    # against an interval of (31.7 Ohm, 32.4 Ohm) and then announced a
    # "1.3e+10 times the bracket" near-pole that does not exist.
    if spec.part == "complex":
        b_lo = min(abs(v_ideal), abs(v_open))
        b_hi = max(abs(v_ideal), abs(v_open))
    else:
        b_lo = min(_real(v_ideal), _real(v_open))
        b_hi = max(_real(v_ideal), _real(v_open))
    bracket_scale = max(abs(b_lo), abs(b_hi), 1e-300)
    if max(abs(lo), abs(hi)) > NEAR_POLE_RATIO * bracket_scale:
        worst_t = t_hi if abs(hi) >= abs(lo) else t_lo
        notes.append(
            "The extremum is %.3g times the [ideal, open] bracket and sits at "
            "%s = %s: that is a near-POLE of the Mobius map, i.e. this "
            "termination anti-resonating with the structure. Check that value "
            "is reachable by a real component -- if it is not, pass t_max and "
            "get the interval over a range you can build."
            % (max(abs(lo), abs(hi)) / bracket_scale, pname,
               format_si(worst_t, punit)))
    # RELATIVE to the values, never floored at 1.0: these quantities are in
    # henries, so a 1e-9 absolute tolerance is larger than every number in the
    # comparison and the detector would answer "no" to everything.
    tol = 1e-9 * max(abs(b_hi), abs(b_lo), abs(lo), abs(hi), 1e-300)
    leaves = bool(lo < b_lo - tol or hi > b_hi + tol)
    if leaves:
        span = (f"[{format_si(b_lo, spec.unit)}, "
                f"{format_si(b_hi, spec.unit)}]")
        notes.append(
            f"The sweep LEAVES the [ideal, open] bracket {span} -- a series L "
            "resonates with the structure's shunt C, so the two endpoints are "
            "not a bound."
            + ("" if t_max is None else
               f" (Only t <= {format_si(float(t_max), punit)} was swept, so "
               "the open endpoint is outside the range reported.)"))

    samp = None
    if samples and samples > 0:
        scale_t = abs(lam).max() if lam.size else 1.0
        if not math.isfinite(scale_t) or scale_t == 0:
            scale_t = 1.0
        ts = np.concatenate([[0.0], np.logspace(
            math.log10(scale_t) - 4, math.log10(scale_t) + 4, int(samples) - 1)])
        vals = np.array([_map_value(spec, scale,
                                    _pf_value(c0, lam_t, cj_t, float(t)))
                         for t in ts])
        samp = (ts, vals)

    return Sweep(
        tuple(S), spec.name, spec.unit, pname, punit, z_unit,
        tuple(complex(x) for x in num), tuple(complex(x) for x in den),
        alpha, beta, gamma, delta, v_ideal, v_open,
        (lo, hi), t_lo, t_hi, (b_lo, b_hi), leaves, samp, method,
        scale, spec.part, tuple(notes),
        complex(c0), lam_t, cj_t)


def _real(v: complex) -> float:
    return float(v.real)


#: Above this many swept elements the expanded polynomial is not even
#: attempted.  Measured on a synthetic package with param="L" (so every
#: eigenvalue is of order 1e-9): the constant term of `den` is 5.98e-273 at 30
#: elements and 0 at 36, and it is only ever a diagnostic.  30 keeps every
#: expansion this repo's own fixtures and tests produce.
_EXPAND_MAX_DEGREE = 30


def _expand_rational(c0: complex, lam: np.ndarray,
                     cj: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    The partial fractions multiplied out into (num, den), or two EMPTY arrays.

    Diagnostic only.  Empty is the honest answer whenever the product of the
    eigenvalues has underflowed -- a coefficient vector whose constant term is
    0 because 34 factors of 1e-9 met each other describes a different rational
    function from the one being swept, and the caller cannot tell by looking.
    """
    n = int(lam.size)
    if n == 0 or n > _EXPAND_MAX_DEGREE:
        return np.zeros(0, dtype=complex), np.zeros(0, dtype=complex)
    den = np.array([1.0 + 0j])
    for lj in lam:
        den = np.convolve(den, np.array([1.0 + 0j, lj]))
    num = c0 * den
    for j in range(n):
        rest = np.array([1.0 + 0j])
        for i2 in range(n):
            if i2 != j:
                rest = np.convolve(rest, np.array([1.0 + 0j, lam[i2]]))
        num = num - cj[j] * np.concatenate(
            [np.zeros(len(den) - len(rest), dtype=complex), rest])
    if not (np.all(np.isfinite(num)) and np.all(np.isfinite(den))):
        return np.zeros(0, dtype=complex), np.zeros(0, dtype=complex)
    # den[-1] is prod(lam); 0 there means the product underflowed, unless a
    # lam really is 0 (an element whose contracted H has a null direction),
    # which is a genuine coefficient and not a loss.
    if den[-1] == 0 and np.all(lam != 0):
        return np.zeros(0, dtype=complex), np.zeros(0, dtype=complex)
    return num, den


#: Multiples of a pole's distance from the real axis, used as extremum seeds.
#: A pole at t = p contributes a Lorentzian of half-width |Im p| to the curve,
#: so its two extremes sit about |Im p| either side of Re(p) and the whole
#: feature is invisible to anything sampling further out than that.  The set is
#: deliberately wider than {1} because the peak of a SUM of such terms is
#: displaced by its neighbours -- and every seed is polished afterwards.
_POLE_SEED_OFFSETS = (0.0, 0.5, 1.0, 2.0, 5.0)

#: Newton steps used to polish a seed onto a stationary point.  Each is O(|S|)
#: partial-fraction terms; 40 is far past convergence for a well-separated root
#: and costs 60 x 40 x 2 flops on the 60-ball case the contract is written for.
_POLISH_STEPS = 40


def _rational_extrema(c0: complex, lam: Sequence[complex],
                      res: Sequence[complex], spec: _QuantitySpec,
                      scale: float, t_max: float | None = None
                      ) -> tuple[float, float, float, float, list[str]]:
    """
    min / max of the functional of Z(t) = c0 - sum res_j/(lam_j + t) over
    t in [0, t_max].

    SEED, THEN POLISH -- and every candidate is a point the curve really
    passes through, so the reported interval is always ACHIEVED and can only
    ever be too narrow, never too wide.  That one-sidedness is what makes it
    safe to add candidates: more seeds can only move the answer towards the
    truth.

    The old spelling multiplied the partial fractions out and took
    `np.roots(P'Q - PQ')` on the expanded degree-2|S| critical polynomial.
    That is exact on paper and loses clustered roots in practice: measured on
    diff_pair_4port.s4p at 5 GHz, sweeping BOTH grounds as one group over L,
    the two poles sit at t = 5.05000e-7 and 5.05503e-7 -- 0.1% apart, both on
    the positive real axis -- and the reported interval came back
    (+7.46e-21, +2.138e-3) H against a true (-5.177, +5.176) H.  The maximum
    was 2.4e3 times too small and the minimum had the WRONG SIGN.  The
    single-element sweep on the same file was correct throughout, which is why
    it went unnoticed: the defect needs |S| >= 2, i.e. exactly requirement 9b's
    "change a whole connection-table row" case.

    Seeds: the two endpoints, the |lam| scales, and each pole's real part
    offset by multiples of its distance from the real axis (a pole is where a
    Mobius-like map's extremum lives).  Polish: Newton on Z'(t) using Z''(t),
    both evaluated from the partial fractions -- no expanded coefficient is
    touched anywhere in here.
    """
    notes: list[str] = []
    lam = tuple(lam)
    res = tuple(res)

    def f(t: float) -> float:
        v = _pf_value(c0, lam, res, t)
        if spec.part == "im":
            return scale * v.imag
        if spec.part == "re":
            return scale * v.real
        return scale * abs(v)

    if spec.part == "complex":
        # |Z| is not R-linear, so the "interval" of the complex quantity is
        # reported on its magnitude and the caller is told.
        notes.append(
            "quantity 'Z' is complex: the interval below is of |Z|, which is "
            "not an additive quantity -- use 'ReZ' / 'ImZ' / 'M' for a signed "
            "interval.")

    def _in_range(t: float) -> bool:
        return (math.isfinite(t) and t > 0.0
                and (t_max is None or t <= t_max))

    # --- seeds
    cands: list[float] = [0.0]
    if t_max is not None and math.isfinite(t_max) and t_max > 0.0:
        cands.append(float(t_max))
    for lj in lam:
        p = -lj                      # the pole in t
        if abs(lj) > 0 and _in_range(abs(lj)):
            cands.append(float(abs(lj)))
        if p.real <= 0.0:
            continue
        # A lossy network never puts a pole exactly on the real axis, but the
        # imaginary part can be 1e-8 of the real one; the floor keeps the
        # offsets from all collapsing onto Re(p) in that case.
        w = max(abs(p.imag), 1e-12 * abs(p.real))
        for c in _POLE_SEED_OFFSETS:
            for sgn in (1.0, -1.0):
                t = p.real + sgn * c * w
                if _in_range(t):
                    cands.append(float(t))

    # --- polish: Newton on Z'(t) = 0, in the partial-fraction form.
    #
    # For part 're' / 'im' the stationary points of the functional are exactly
    # the real roots of part(Z'(t)).  For the magnitude there is no such
    # identity, so its seeds are polished onto the stationary points of the
    # real and imaginary parts, which bracket |Z|'s extremes along the arc --
    # the same reasoning the polynomial spelling used, minus the polynomial.
    parts = ("re", "im") if spec.part == "complex" else (spec.part,)
    polished: list[float] = []
    for t0 in list(cands):
        if t0 <= 0.0:
            continue
        for pt in parts:
            t = t0
            for _ in range(_POLISH_STEPS):
                d1 = _pf_deriv(lam, res, t, 1)
                d2 = _pf_deriv(lam, res, t, 2)
                g1 = d1.real if pt == "re" else d1.imag
                g2 = d2.real if pt == "re" else d2.imag
                if not (math.isfinite(g1) and math.isfinite(g2)) or g2 == 0.0:
                    break
                step = g1 / g2
                nt = t - step
                if not math.isfinite(nt) or nt <= 0.0:
                    break
                if abs(nt - t) <= 1e-15 * abs(t):
                    t = nt
                    break
                t = nt
            if _in_range(t):
                polished.append(float(t))
    cands.extend(polished)

    vals = []
    for t in cands:
        try:
            v = f(t)
        except (ZeroDivisionError, FloatingPointError):  # pragma: no cover
            continue
        if math.isfinite(v):
            vals.append((v, t))
    # t -> infinity, exactly: every 1/(lam + t) vanishes, so Z(inf) = c0.
    # Included only for an UNBOUNDED sweep -- with a t_max the open termination
    # is outside the range being asked about, and folding it in would widen the
    # interval by a value the caller has just said is out of reach.
    if t_max is None:
        vinf = complex(c0)
        v = scale * (vinf.imag if spec.part == "im"
                     else vinf.real if spec.part == "re" else abs(vinf))
        if math.isfinite(v):
            vals.append((v, float("inf")))
    if not vals:                                         # pragma: no cover
        return float("nan"), float("nan"), float("nan"), float("nan"), notes
    lo_v, lo_t = min(vals, key=lambda vt: vt[0])
    hi_v, hi_t = max(vals, key=lambda vt: vt[0])
    return lo_v, hi_v, lo_t, hi_t, notes


# ---------------------------------------------------------------------------
# The exact current-transfer ratio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransferRatio:
    victim: str
    aggressor: str
    freq_hz: float
    ratio: complex          # -Z_ab / Z_aa, the shorted-victim transfer
    ratio_db: float
    loaded: complex | None  # -Z_ab / (Z_aa + Z_load)
    loaded_db: float
    z_load: complex | None
    norton: float           # M / L_a, the first-order approximation
    norton_db: float
    error_db: float         # how far M/L_a is from the exact ratio
    note: str


def transfer_ratio(ctx: AttribContext, victim: int | str,
                   aggressor: int | str,
                   z_load: complex | None = None) -> TransferRatio:
    """
    -Z_ab / Z_aa, the EXACT current a shorted victim draws per amp in the
    aggressor, plus the loaded form -Z_ab / (Z_aa + Z_load).

    docs/theory.md 8.8 records that M/L_a is only the first-order Norton
    approximation to this: they agree where omega*L_a >> R_a and part company
    below that corner.  `error_db` is the difference, which is the number worth
    printing -- the user measured 0.87 dB of it by hand on their own file.

    Read off `ctx.Zref` -- compute_z_matrix's own matrix -- for an ordinary
    context, so the ratio and the M/L_a beside it mean what the results pane
    prints.  For a WHAT-IF context there is no engine matrix for the network in
    question, and answering with the declared spec's ratio would describe a
    network the caller did not ask about, so `ctx.Zop` is used instead.  Both Z
    values come from the SAME matrix either way: a mutual from one network over
    a self impedance from another is not a transfer ratio at all.
    """
    a = ctx.port_index(victim)
    b = ctx.port_index(aggressor)
    Zsrc = ctx.Zop if ctx.is_whatif else ctx.Zref
    Zab = complex(Zsrc[a, b])
    Zaa = complex(Zsrc[a, a])
    ratio = -Zab / Zaa if Zaa != 0 else _UNDEFINED
    loaded = None
    loaded_db = float("nan")
    if z_load is not None:
        den = Zaa + complex(z_load)
        loaded = -Zab / den if den != 0 else _UNDEFINED
        loaded_db = 20.0 * math.log10(abs(loaded)) if loaded and abs(loaded) > 0 \
            else float("nan")
    om = ctx.omega
    La = Zaa.imag / om if om else float("nan")
    M = Zab.imag / om if om else float("nan")
    norton = M / La if (math.isfinite(La) and La != 0.0) else float("nan")
    r_db = 20.0 * math.log10(abs(ratio)) if abs(ratio) > 0 else float("nan")
    n_db = 20.0 * math.log10(abs(norton)) if (math.isfinite(norton)
                                              and norton != 0) else float("nan")
    err = (n_db - r_db) if (math.isfinite(n_db) and math.isfinite(r_db)) \
        else float("nan")
    return TransferRatio(
        ctx.port_names[a], ctx.port_names[b], ctx.freq_hz,
        ratio, r_db, loaded, loaded_db,
        None if z_load is None else complex(z_load),
        norton, n_db, err,
        "M/L_a is the Norton injection ratio; -Z_ab/Z_aa is the exact "
        "short-circuit current transfer. They differ by "
        f"{err:.2f} dB here.")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_decomposition(dec: Decomposition) -> list[str]:
    """
    The decomposition as plain lines, so the GUI, the CLI and any export all
    print the same thing (and all carry the sign convention).
    """
    out: list[str] = []
    out.append(f"Attribution of {dec.quantity} "
               f"({dec.victim} <- {dec.aggressor}) at "
               f"{format_freq(dec.freq_hz)}")
    # The label changes with `reference_applicable`, because the number does
    # not mean the same thing: under a what-if `zt` the engine was asked about
    # the DECLARED spec, which is a different network, and printing it under
    # the same heading beside a total that disagrees with it by 100% is how a
    # correct answer came to read as a bug.
    if dec.reference_applicable:
        out.append(f"  total (compute_z_matrix) : "
                   f"{_fmt_q(dec.total_reference, dec.unit)}")
    else:
        out.append(f"  total (this module)      : "
                   f"{_fmt_q(dec.total_sum, dec.unit)}")
        out.append(f"  total (compute_z_matrix, DECLARED spec -- a DIFFERENT "
                   f"network) : {_fmt_q(dec.total_reference, dec.unit)}")
    out.append(f"  total (sum of terms)     : "
               f"{_fmt_q(dec.total_sum, dec.unit)}"
               f"   residual {dec.residual_rel:.3g} "
               f"(floor {dec.residual_floor:.3g}"
               + ("" if dec.reference_applicable else ", declared spec")
               + ")")
    # C_c is not decomposable and is still the reading whenever the coupling is
    # capacitive, so it is here as a TOTAL.  NON_DECOMPOSABLE['C_c'] promises
    # exactly this line.
    if not _is_undefined(dec.C_c_total):
        lead = ("Im(Z_ab) < 0, so the coupling is CAPACITIVE and this is the "
                "reading" if dec.Z_ab.imag < 0 else
                "negative: the coupling is inductive here, so M is the reading")
        out.append(f"  C_c (total only)         : "
                   f"{format_si(dec.C_c_total.real, 'F')}   ({lead})")
    out.append(f"  cond(Ybase) {dec.cond_Ybase:.3g}   cond(H) {dec.cond_H:.3g}"
               f"   reciprocity {dec.reciprocity_rel:.3g}")
    out.append("")
    if not dec.terms:
        out.append("  (per-element split withheld)")
    else:
        head = f"  {'element':<28} {'contribution':>16} {'share':>9} {'quad':>9}"
        out.append(head)
        for t in dec.terms:
            share = "  --  " if not math.isfinite(t.share_inline) \
                else f"{100 * t.share_inline:8.2f}%"
            quad = "  --  " if not math.isfinite(t.share_quad) \
                else f"{100 * t.share_quad:8.2f}%"
            out.append(f"  {t.label:<28} "
                       f"{_fmt_q(t.contribution, dec.unit):>16} "
                       f"{share:>9} {quad:>9}")
    out.append("")
    out.append("  " + dec.reference_note)
    for n in dec.notes:
        out.append("  note: " + n)
    for w in dec.warnings:
        out.append("  WARN: " + w)
    out.append("  " + SIGN_CONVENTION_TEXT)
    return out


def _is_undefined(v: complex) -> bool:
    return not (math.isfinite(v.real) and math.isfinite(v.imag))


def _fmt_q(v: complex, unit: str) -> str:
    if abs(v.imag) > 0:
        return f"{format_si(v.real, unit)} {v.imag:+.4g}j"
    return format_si(v.real, unit)
