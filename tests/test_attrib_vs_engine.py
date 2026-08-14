"""
Cross-validation: does `pkg_rlc_attrib` reduce the SAME NETWORK as
`compute_z_matrix`?

`tests/test_attrib_core.py` is the module author's acceptance suite.  This file
is deliberately a second, independent opinion on the one claim everything else
rests on -- that the node-space decomposition and the engine's Schur reduction
are two routes to the same number -- and it differs from that suite in four
ways on purpose:

  * it walks the case registry in `tests/_golden_capture.py` rather than a
    hand-picked list of fixtures.  That registry is the single place in the
    repo that knows every mode/fixture combination the golden reference pins,
    so "every mode is covered" is a property of the walk instead of a claim in
    a docstring.  It anchors on the BIT-EXACT array in
    `tests/fixtures/golden_legacy.npz`, not on the `compute_z_matrix` call
    build_context makes for itself;

  * its tolerance is computed HERE, from the file's own admittance slice, and
    never read off the Decomposition.  Comparing the module's residual against
    the module's own floor proves only that the module is self-consistent --
    which it would still be with both of them wrong;

  * it pins requirement 12 (the engine's probe/ground precedence) as a
    STRUCTURAL fact -- which declaration became an element, which one was
    thrown away -- and not only as a number that happens to agree;

  * it fuzzes.  4000 random specs across six fixtures, with one two-sided
    contract: either the decomposition agrees with the engine inside the
    condition-aware budget, or the Decomposition says so out loud.  Never
    silently wrong.

WHAT THIS FILE FOUND (both recorded as tests below, neither is a defect):

  1. "Decomposing a<-b and b<-a must agree term by term" is FALSE, and not
     because of an implementation slip -- reciprocity is a statement about the
     TOTAL, not about a superposition split.  See TestReciprocity.

  2. Two of the golden registry's cases sit at a frequency where floating point
     guarantees nothing at all (`m1_pi_p1_gnd2` at 1 MHz disagrees by 70%), and
     the module handles it exactly as requirement 5 asks: it withholds the
     split and says why.  See `test_where_arithmetic_guarantees_nothing_...`.

Every assertion below was mutation-checked -- fifteen mutations of
`pkg_rlc_attrib.py` and four of this file, each applied and reverted -- and the
mutation that turns each test red is named in its own docstring.  Nothing here
imports anything private from the module under test, and nothing here writes to
`tests/fixtures/`.
"""

from __future__ import annotations

import io
import itertools
import math
import random
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

import numpy as np  # noqa: E402

import _golden_capture as gcap  # noqa: E402
import pkg_rlc.physics.attrib as at  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
    Open,
    ShortPair,
    Signal,
    TerminationSet,
    Vdd,
    PINV_RCOND,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    build_terminations_rows,
    format_freq,
    parse_custom_termination_text,
    parse_short_pairs,
    parse_touchstone,
    resolve_meas_ports,
    s_to_y,
    y_capacitor,
    y_series_rlc,
)

FIXTURES = _HERE / "fixtures"
_EPS = float(np.finfo(float).eps)


# ---------------------------------------------------------------------------
# The independent error budget
# ---------------------------------------------------------------------------
#
# `Decomposition.residual_floor` is the module's own gate and is therefore
# useless as a test oracle: a module whose arithmetic and whose error bound are
# both wrong passes that comparison every time.  The budget below is built from
# the ONE object both algorithms are handed -- Y at the frequency being asked
# about -- and from nothing else.
#
# Two factors, and both are needed.  `kappa` is the effective condition number
# of the admittance (effective = the spread pinv actually keeps, because
# coupled_4port_float.s4p is singular BY CONSTRUCTION at cond 2.5e16 and a plain
# cond() there would switch the gate off).  `amp` is the ratio between the
# largest entry of the open-circuit impedance and the answer: an inverse is
# accurate relative to its LARGEST entry, so on diff_pair_4port.s4p at 1 MHz --
# where the largest entry of Z is the 1 fF port capacitance's 159 kOhm and the
# answer is a 6 mOhm mutual -- every small entry carries the big entry's
# absolute error.  Dropping either factor makes the budget an order of
# magnitude too tight on the low-frequency end of every fixture in the repo.
#
# SAFETY is measured, not chosen: over the whole golden registry at the eight
# frequencies below, the worst residual/budget ratio is 1.99e-2
# (m1_shunt_rl_p1 at 2 GHz), then 1.29e-2 and 1.19e-2 -- so the budget has 50x
# of headroom on the cases it is asked to pass.  That headroom does not blunt
# it: every mutation checked against this file overruns the budget by many
# orders of magnitude, because a wrong network is wrong at the 1e0 level, not
# at the 1e-14 level.
TOL_SAFETY = 64.0


def condition_aware_budget(Yk: np.ndarray, denom: float) -> tuple[float, str]:
    """
    (absolute error allowed on |Z_attrib - Z_engine|, a human-readable detail).

    `denom` is the scale the comparison is relative to: |Z_ab| normally, and
    the largest entry of Z where Z_ab is exactly zero (decap_4port.s4p's two pi
    networks are uncoupled BY CONSTRUCTION, so its mutual is 0 and not small).

    The budget is capped at `denom` -- one whole answer.  Past that point there
    is nothing left to assert numerically, and
    `test_where_arithmetic_guarantees_nothing_the_module_says_so` takes over.
    """
    s = np.linalg.svd(np.asarray(Yk, dtype=complex), compute_uv=False)
    keep = s > PINV_RCOND * s[0] if s.size else s
    kappa = float(s[0] / s[keep][-1]) if np.any(keep) else float("inf")
    Zopen = np.linalg.pinv(np.asarray(Yk, dtype=complex), rcond=PINV_RCOND)
    biggest = float(np.max(np.abs(Zopen)))
    amp = max(biggest / denom, 1.0) if denom > 0 else float("inf")
    rel = TOL_SAFETY * _EPS * kappa * amp
    rel = min(1.0, max(TOL_SAFETY * _EPS, rel))
    return rel * denom, (f"cond_eff(Y)={kappa:.3g}, largest |Z_open| entry="
                         f"{biggest:.3g} Ohm, scale={denom:.3g} Ohm, "
                         f"budget={rel:.3g} relative")


def _load(name: str):
    d = parse_touchstone(FIXTURES / name)
    return d, s_to_y(d.s, d.z0)


_CACHE: dict[str, tuple] = {}


def load(name: str):
    if name not in _CACHE:
        _CACHE[name] = _load(name)
    return _CACHE[name]


def sum_of_terms(dec: at.Decomposition) -> complex:
    """The decomposition's own arithmetic, added up by THIS file.

    `Decomposition.total_sum` is a value the module computed; adding the terms
    up here is the only way to check that the rows a reader sees are the rows
    that produced it.
    """
    return sum((t.contribution for t in dec.terms), start=complex(0.0))


# ===========================================================================
# 1. The golden case registry
# ===========================================================================

class TestGoldenRegistryReconciles(unittest.TestCase):
    """
    Every mode/fixture combination the repo pins, put through the attribution
    layer and compared against the BIT-EXACT array in golden_legacy.npz.

    `tests/_golden_capture.py`'s Z_CASES is the single place that knows the
    full set (modes 1, 2, 3, retired 4 and 5 across five fixtures); iterating
    it rather than restating it is what makes a future case automatically
    covered here.  Mode 6 has no golden case -- the registry predates the probe
    model -- and is covered by TestNamedModesReconcile below.
    """

    FREQ_FRACTIONS = (0.0, 0.02, 0.2, 1.0 / 3.0, 0.5, 2.0 / 3.0, 0.8, 1.0)

    @classmethod
    def setUpClass(cls):
        gcap.ensure_fixtures()
        cls.gold = np.load(gcap.GOLDEN_NPZ, allow_pickle=False)

    def _indices(self, nf: int) -> list[int]:
        return sorted({min(nf - 1, int(round(x * (nf - 1))))
                       for x in self.FREQ_FRACTIONS})

    def _walk(self):
        """Yield (case, index, ctx, golden_Z_at_index) for the whole registry.

        A case with no measurement port would be skipped, which is why
        test_the_walk_covers_the_whole_registry counts what came out.
        """
        for case in gcap.Z_CASES:
            d, Y = load(case.fixture)
            term = case.build()
            if not resolve_meas_ports(term, int(d.s.shape[1])):
                continue                                 # pragma: no cover
            Zg = self.gold[gcap.z_key(case.case_id, "Z")]
            fg = self.gold[gcap.z_key(case.case_id, "freqs")]
            self.assertTrue(
                np.array_equal(fg, d.freqs),
                f"{case.case_id}: the golden frequency axis is not the "
                f"fixture's, so indices do not line up")
            for i in self._indices(len(d.freqs)):
                ctx = at.build_context(Y, d.freqs, term, float(d.freqs[i]))
                yield case, i, ctx, complex(Zg[i])

    # ---- the walk itself is not allowed to be empty ------------------------

    def test_the_walk_covers_the_whole_registry(self):
        """
        Without this, a filter bug that skipped every case would leave the
        reconciliation test below green and vacuous.

        Mutation: `continue` unconditionally in _walk -> red here, silent
        everywhere else.
        """
        seen = {case.case_id for case, _i, _c, _z in self._walk()}
        expected = {c.case_id for c in gcap.Z_CASES}
        self.assertEqual(seen, expected,
                         "the walk did not reach every registry case")
        # 20 cases as this was written: 7 mode-1, 5 mode-2, 4 mode-3,
        # 2 mode-4 and 2 mode-5, over five fixtures.
        self.assertGreaterEqual(len(expected), 20,
                                "the registry shrank; a mode lost its coverage")

    def test_every_registry_case_reconciles_with_the_golden_array(self):
        """
        THE test in this file.  The sum of the decomposition's own terms
        against `golden_legacy.npz`, case by case and frequency by frequency,
        inside a budget computed from Y alone.

        The reference is the golden array rather than `ctx.Zref` on purpose:
        build_context calls compute_z_matrix itself, so comparing against
        `ctx.Zref` cannot notice the two of them being fed different networks.

        Mutations that turn this red: `Z = ctx.Dmat - Rm.T @ I` -> `+` in
        _z_matrix; the element stamp indexing `elem.ports[-1]` instead of
        `[0]`; dropping the bare-EM row from `raw` in decompose.

        Half of the 160 (case, frequency) comparisons -- exactly 80 -- have at
        least one element; the rest are all-open specs whose whole answer is
        the direct term, which is worth knowing about the fixture set and is
        why the mode-6 and precedence classes below carry the element-heavy
        cases.
        """
        checked = 0
        skipped_unbounded = 0
        for case, i, ctx, zgold in self._walk():
            G = len(ctx.port_names)
            for a, b in itertools.product(range(G), repeat=2):
                ref = zgold if (a == 0 and b == 0) else complex(ctx.Zref[a, b])
                dec = at.decompose(ctx, a, b, "Z")
                got = sum_of_terms(dec) if dec.terms else dec.total_sum
                finite = np.abs(ctx.Zref)[np.isfinite(np.abs(ctx.Zref))]
                denom = abs(ref) if abs(ref) > 0 else (
                    float(finite.max()) if finite.size else 1.0)
                if denom == 0.0:                         # pragma: no cover
                    continue
                allow, detail = condition_aware_budget(
                    np.asarray(load(case.fixture)[1][i]), denom)
                err = abs(got - ref)
                if allow >= denom:
                    # The budget exceeded one whole answer: there is no numeric
                    # claim left to make here, and the companion test below
                    # asserts the module refuses to pretend otherwise.
                    skipped_unbounded += 1
                    continue
                checked += 1
                self.assertLessEqual(
                    err, allow,
                    f"\n{case.case_id} ({case.describe})"
                    f"\n  fixture {case.fixture}, index {i} = "
                    f"{format_freq(ctx.freq_hz)}, pair ({a},{b})"
                    f"\n  golden / engine : {ref!r}"
                    f"\n  sum of terms    : {got!r}"
                    f"\n  |difference|    : {err:.6e}   "
                    f"({err / denom:.3e} relative)"
                    f"\n  allowed         : {allow:.6e}   [{detail}]"
                    f"\n  module said     : residual {dec.residual_rel:.3e} "
                    f"vs its own floor {dec.residual_floor:.3e}, "
                    f"cond(Ybase)={dec.cond_Ybase:.3g}, "
                    f"cond(H)={dec.cond_H:.3g}")
        # Measured: 20 cases x 8 frequencies = 160 comparisons, of which 146
        # carry a numeric claim.
        self.assertGreaterEqual(
            checked, 140,
            f"only {checked} (case, frequency, pair) comparisons carried a "
            "numeric claim; the budget has gone slack")
        # The other 14 are unbounded, and every one of them is at the 1 MHz end
        # of pi_2port / diff_pair_4port, where the 1 fF port capacitance's
        # 159 kOhm sits over a milliohm answer.
        self.assertLessEqual(
            skipped_unbounded, 20,
            f"{skipped_unbounded} comparisons had no usable budget; the "
            "condition-aware budget has become too loose to test anything")

    def test_the_engine_value_the_module_reconciles_against_is_the_golden_one(self):
        """
        `ctx.Zref` must be bit-identical to golden_legacy.npz.

        This is what makes the reconciliation line mean "against the pinned
        engine" rather than "against whatever build_context computed for
        itself".  Bit-exact, not toleranced: compute_z_matrix on a one-frequency
        slice is documented to return exactly what the full sweep puts at that
        index, and if that ever stops being true the decomposition is being
        reconciled against a different number from the one the results pane
        prints.

        Mutation: slice `Y[max(0, idx-1):idx+1]` in build_context -> red on
        every case at every index but 0.
        """
        n = 0
        for case, i, ctx, zgold in self._walk():
            self.assertEqual(
                complex(ctx.Zref[0, 0]), zgold,
                f"{case.case_id} @ index {i}: build_context's engine value "
                f"{complex(ctx.Zref[0, 0])!r} is not the golden "
                f"{zgold!r} (difference "
                f"{abs(complex(ctx.Zref[0, 0]) - zgold):.3e})")
            n += 1
        self.assertGreater(n, 0)

    def test_the_terms_are_the_total_they_are_printed_under(self):
        """
        Superposition is exact, so the rows a reader adds up by hand must give
        `total_sum` to the last few ulps -- this is arithmetic on numbers the
        module already has, not a second reduction.

        Mutation: drop the bare-EM row from `raw` in decompose -> red on every
        case with a non-trivial direct term while the reconciliation test above
        still passes on the element-free cases.
        """
        n = 0
        for case, i, ctx, _zgold in self._walk():
            dec = at.decompose(ctx, 0, 0, "Z")
            if not dec.terms:
                continue
            got = sum_of_terms(dec)
            scale = max([abs(dec.total_sum)]
                        + [abs(t.contribution) for t in dec.terms])
            self.assertLessEqual(
                abs(got - dec.total_sum), 1e-12 * scale,
                f"{case.case_id} @ index {i}: the {len(dec.terms)} printed "
                f"terms sum to {got!r} but total_sum says {dec.total_sum!r}")
            n += 1
        # Measured: 159 of the 160 comparisons print a split.  The one that
        # does not is m1_pi_p1_gnd2 at 1 MHz, which has its own test above.
        self.assertGreaterEqual(n, 150,
                                f"only {n} registry cases produced a split; "
                                "the check above is testing nothing")

    def test_where_arithmetic_guarantees_nothing_the_module_says_so(self):
        """
        `m1_pi_p1_gnd2` at 1 MHz: the two algorithms disagree by 70% and BOTH
        are right to.  The largest entry of the open-circuit Z there is the
        159 kOhm of the 1 fF port capacitance and the answer is 1 Ohm, so the
        answer is built entirely out of the big entry's rounding error.

        The requirement is not that the module get it right -- nothing can --
        but that it not report the roundoff as a measurement.  Measured:
        residual 7.03e-01, split withheld, one warning naming the noise floor.

        Mutation: `trustworthy = False` -> `True` in decompose's catastrophic
        branch, or dropping the warning, turns this red while every other test
        in the file stays green.
        """
        d, Y = load("pi_2port.s2p")
        term = build_terminations_mode1([1], [2])
        ctx = at.build_context(Y, d.freqs, term, float(d.freqs[0]))
        dec = at.decompose(ctx, 0, 0, "Z")

        err = abs(dec.total_sum - complex(ctx.Zref[0, 0]))
        self.assertGreater(
            err / abs(ctx.Zref[0, 0]), 1e-2,
            "pi_2port.s2p at 1 MHz has become well conditioned; this test's "
            "premise is gone and the numbers in its docstring are stale")
        self.assertFalse(
            dec.split_trustworthy,
            f"the split was offered at a residual of {dec.residual_rel:.3g}")
        self.assertEqual(dec.terms, [],
                         "the per-element rows must be withheld, not merely "
                         "flagged: a reader adds up what is printed")
        self.assertTrue(dec.warnings, "nothing was said out loud")
        # The TOTAL is never withheld -- the engine's value is authoritative
        # and is what the results pane prints.
        self.assertEqual(dec.total_reference, complex(ctx.Zref[0, 0]))


# ===========================================================================
# 2. The named modes, including the one the registry cannot reach
# ===========================================================================

# Mode 6 (the probe model) postdates golden_legacy.npz, so its cases are spelled
# out here.  Every one of them has two measurement ports, which is also what
# makes the a<-b pair -- the whole reason this module exists -- reachable at all:
# every case in the golden registry collapses to G == 1.
MODE6_CASES = [
    ("m6 diff pair, both lines differential", "diff_pair_4port.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], [3]), ("c2", [2], [4])], nports=4)),
    ("m6 diff pair, ground referenced, far ends grounded",
     "diff_pair_4port.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], []), ("c2", [2], [])], gnd_ports=[3, 4], nports=4)),
    ("m6 diff pair, one differential probe + one ground",
     "diff_pair_4port.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], [3]), ("c2", [2], [])], gnd_ports=[4], nports=4)),
    ("m6 coupled coils, both differential", "coupled_4port_diff.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], [2]), ("c2", [3], [4])], nports=4)),
    ("m6 coupled coils, FLOATING (singular Y)", "coupled_4port_float.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], [2]), ("c2", [3], [4])], nports=4)),
    ("m6 coupled coils, ground referenced", "coupled_2port_gndref.s2p",
     lambda: build_terminations_coupling(
         [("c1", [1], []), ("c2", [2], [])], nports=2)),
    ("m6 negative M", "coupled_2port_negM.s2p",
     lambda: build_terminations_coupling(
         [("c1", [1], []), ("c2", [2], [])], nports=2)),
    ("m6 two UNCOUPLED pi networks (Z_ab is exactly 0)", "decap_4port.s4p",
     lambda: build_terminations_coupling(
         [("c1", [1], [2]), ("c2", [3], [])], gnd_ports=[4], nports=4)),
    ("m5 DSL, two probes + a lumped ground", "diff_pair_4port.s4p",
     lambda: parse_custom_termination_text(
         "1 signal p +\n3 signal p -\n2 signal q +\n"
         "4 lumped_to_gnd R=0.5 L=1n\n")),
    ("m5 DSL, two probes + a ground + a lumped ground", "diff_pair_4port.s4p",
     lambda: parse_custom_termination_text(
         "1 signal p +\n2 signal q +\n3 ground\n4 lumped_to_gnd R=2\n")),
]


class TestNamedModesReconcile(unittest.TestCase):
    """
    Modes 1, 2, 3, retired-4, 5 and 6, each as a user spells it, reconciled at
    four frequencies.

    Modes 1..5 are already walked through the registry; what is added here is
    mode 6 -- and with it the only cases in the repo where the victim and the
    aggressor are DIFFERENT measurement ports, which is the pair the whole
    module is about.
    """

    NAMED = [
        ("mode 1", "diff_pair_4port.s4p",
         lambda: build_terminations_mode1([1], [2, 3, 4])),
        ("mode 2", "diff_pair_4port.s4p",
         lambda: build_terminations_mode2([1], [2], [3, 4])),
        ("mode 3", "diff_pair_4port.s4p",
         lambda: build_terminations_mode3([1], [2], [],
                                          parse_short_pairs("3-4"))),
        ("mode 4 (retired; VDD evaluates as ground)", "diff_pair_4port.s4p",
         lambda: build_terminations_mode4([1], [2], [], [3, 4])),
        ("mode 5 (DSL)", "decap_4port.s4p",
         lambda: parse_custom_termination_text(
             "1 signal A\n2 signal B\n3 lumped_to_gnd R=50\n"
             "3 lumped_between 4 R=0.01 L=0.1n C=1p\n")),
        ("rows (the connection table)", "diff_pair_4port.s4p",
         lambda: build_terminations_rows(
             [MeasPortRow("A", "1", "2")],
             [ConnectionRow(kind="ground", ports="3,4")])),
    ]

    def _reconcile(self, label, fixture, term, a, b, i):
        d, Y = load(fixture)
        ctx = at.build_context(Y, d.freqs, term, float(d.freqs[i]))
        dec = at.decompose(ctx, a, b, "Z")
        ref = complex(ctx.Zref[a, b])
        got = sum_of_terms(dec) if dec.terms else dec.total_sum
        finite = np.abs(ctx.Zref)[np.isfinite(np.abs(ctx.Zref))]
        denom = abs(ref) if abs(ref) > 0 else (
            float(finite.max()) if finite.size else 1.0)
        allow, detail = condition_aware_budget(np.asarray(Y[i]), denom)
        self.assertLessEqual(
            abs(got - ref), allow,
            f"\n{label} on {fixture}, index {i} = {format_freq(ctx.freq_hz)}, "
            f"pair ({a},{b})"
            f"\n  engine       : {ref!r}"
            f"\n  sum of terms : {got!r}"
            f"\n  |difference| : {abs(got - ref):.6e} "
            f"({abs(got - ref) / denom:.3e} relative)"
            f"\n  allowed      : {allow:.6e}   [{detail}]")
        return ctx, dec

    def test_every_named_mode_reconciles(self):
        """
        Mutation: any change to _probe_side_of_port's merge_terms replication
        (for instance keying only on a direct `isinstance(t, Signal)` instead
        of on the shorted group) -> mode 3 goes red here and in the registry
        walk.
        """
        for label, fixture, build in self.NAMED:
            for i in (len(load(fixture)[0].freqs) // 4,
                      len(load(fixture)[0].freqs) // 2,
                      len(load(fixture)[0].freqs) - 1):
                with self.subTest(mode=label, index=i):
                    self._reconcile(label, fixture, build(), 0, 0, i)

    def test_mode_6_reconciles_on_every_pair_of_every_case(self):
        """
        The victim/aggressor pair, which is what the registry cannot reach:
        every one of its cases has a single measurement port.

        Mutation: `ZtW = np.linalg.solve(Ybase.T, W)` ->
        `np.linalg.solve(Ybase.conj().T, W)` (Y is complex SYMMETRIC, not
        Hermitian -- the easy numpy slip the module's docstring names) turns
        this red with residuals of order 1 on every mode-6 case.
        """
        checked = 0
        for label, fixture, build in MODE6_CASES:
            d, _Y = load(fixture)
            term = build()
            nf = len(d.freqs)
            for i in (nf // 4, nf // 2, 3 * nf // 4, nf - 1):
                ctx = at.build_context(load(fixture)[1], d.freqs, term,
                                       float(d.freqs[i]))
                self.assertEqual(
                    len(ctx.port_names), 2,
                    f"{label} was meant to define two measurement ports, got "
                    f"{ctx.port_names}")
                for a, b in itertools.product(range(2), repeat=2):
                    with self.subTest(case=label, index=i, pair=(a, b)):
                        self._reconcile(label, fixture, term, a, b, i)
                        checked += 1
        self.assertEqual(checked, len(MODE6_CASES) * 4 * 4)

    #: Cases whose terms are not all the same sign, so that "the derived
    #: quantity is the Z decomposition times a constant" is a claim about signs
    #: as well as magnitudes.  coupled_2port_negM.s2p is the whole reason the
    #: fixture exists: M = -800 pH there and +800 pH in coupled_2port_gndref.
    QUANTITY_CASES = [
        ("negative M", "coupled_2port_negM.s2p",
         lambda: build_terminations_coupling(
             [("c1", [1], []), ("c2", [2], [])], nports=2), -800e-12),
        ("positive M", "coupled_2port_gndref.s2p",
         lambda: build_terminations_coupling(
             [("c1", [1], []), ("c2", [2], [])], nports=2), +800e-12),
        ("three terms, real parts of both signs", "diff_pair_4port.s4p",
         lambda: build_terminations_coupling(
             [("c1", [1], []), ("c2", [2], [])], gnd_ports=[3, 4], nports=4),
         None),
    ]

    def test_the_derived_quantities_are_the_same_answer_times_a_constant(self):
        """
        M, ImZ, ReZ, M/L_a and k must be the SAME decomposition scaled, not a
        second one: requirement 8's whole basis is that a decomposable quantity
        is a fixed real scalar times an R-linear functional of Z_ab.

        Mutations: `complex(scale * z.imag, 0.0)` ->
        `complex(scale * abs(z.imag), 0.0)` in _map_value -> red on the
        negative-M case; recomputing `scale` per term instead of once per
        decomposition -> red on the three-term case.
        """
        for label, fixture, build, _expect_M in self.QUANTITY_CASES:
            d, Y = load(fixture)
            ctx = at.build_context(Y, d.freqs, build(),
                                   float(d.freqs[len(d.freqs) // 2]))
            dz = at.decompose(ctx, 0, 1, "Z")
            takers = {
                "ReZ": lambda z: z.real,
                "ImZ": lambda z: z.imag,
                "M": lambda z: z.imag / ctx.omega,
            }
            for quantity, take in takers.items():
                with self.subTest(case=label, quantity=quantity):
                    dq = at.decompose(ctx, 0, 1, quantity)
                    self.assertEqual(len(dq.terms), len(dz.terms))
                    biggest = max(abs(take(t.contribution)) for t in dz.terms)
                    for tz, tq in zip(dz.terms, dq.terms):
                        want = take(tz.contribution)
                        self.assertLessEqual(
                            abs(tq.contribution.real - want),
                            1e-12 * max(biggest, 1e-300),
                            f"{label}/{quantity}: term '{tq.label}' is "
                            f"{tq.contribution!r}, not {want!r} taken off the "
                            f"Z decomposition")
                        self.assertEqual(tq.contribution.imag, 0.0)
            # M/L_a and k are the same map with a different constant; check
            # them as a RATIO against M so a wrong constant is visible without
            # restating extract_coupling_at_freq's formula here.
            dm = at.decompose(ctx, 0, 1, "M")
            for quantity in ("M/L_a", "k"):
                with self.subTest(case=label, quantity=quantity):
                    dq = at.decompose(ctx, 0, 1, quantity)
                    ratios = [tq.contribution.real / tm.contribution.real
                              for tq, tm in zip(dq.terms, dm.terms)
                              if tm.contribution.real != 0.0]
                    self.assertTrue(ratios)
                    self.assertLessEqual(
                        max(ratios) - min(ratios),
                        1e-9 * abs(max(ratios, key=abs)),
                        f"{label}/{quantity}: the per-term scale factors "
                        f"differ ({ratios!r}); a decomposable quantity is ONE "
                        f"constant times the Z split")

    def test_a_negative_mutual_stays_negative_in_every_term(self):
        """
        The repo-wide signed-value invariant, at the attribution layer: "M /
        C_c / k are signed and are never clipped, abs()-ed or hidden."
        coupled_2port_negM.s2p is the fixture that exists to catch an abs().

        Mutation: `complex(scale * z.imag, 0.0)` ->
        `complex(scale * abs(z.imag), 0.0)` in _map_value -> red.
        """
        for label, fixture, build, expect_M in self.QUANTITY_CASES:
            if expect_M is None:
                continue
            with self.subTest(case=label):
                d, Y = load(fixture)
                ctx = at.build_context(Y, d.freqs, build(),
                                       float(d.freqs[len(d.freqs) // 2]))
                dm = at.decompose(ctx, 0, 1, "M")
                self.assertAlmostEqual(
                    dm.total_sum.real, expect_M, delta=abs(expect_M) * 1e-6,
                    msg=f"{label}: M is {dm.total_sum.real!r}, expected "
                        f"{expect_M!r}")
                for t in dm.terms:
                    self.assertEqual(
                        math.copysign(1.0, t.contribution.real),
                        math.copysign(1.0, expect_M),
                        f"{label}: term '{t.label}' = {t.contribution.real!r} "
                        f"has the wrong sign for an M of {expect_M!r}")
                dk = at.decompose(ctx, 0, 1, "k")
                self.assertEqual(math.copysign(1.0, dk.total_sum.real),
                                 math.copysign(1.0, expect_M),
                                 "k lost the sign of M")


# ===========================================================================
# 3. Requirement 12 -- the engine's precedence, structurally
# ===========================================================================

class TestPrecedenceIsTheEnginesPrecedence(unittest.TestCase):
    """
    "Ground wins over a probe" in modes 1/2/3, "the Signal wins inside a
    shorted group" in merge_terms, and the coupling builder refusing the same
    overlap outright.  All three are pinned by
    test_core.py::TestTerminationPrecedence and
    test_connection_rows.py::TestRowsReproduceNamedModes, and requirement 12
    says the attribution layer must reproduce them EXACTLY.

    A number that agrees is not enough here.  If the precedence were wrong in a
    way the fixture happens not to notice, the reconciliation would stay green
    and the contribution table would attribute the answer to a declaration that
    never entered the network.  So each case below asserts WHICH declarations
    became elements and which were thrown away, as (kind, ports) data.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load("diff_pair_4port.s4p")
        cls.i = len(cls.d.freqs) // 2

    def _ctx(self, term):
        return at.build_context(self.Y, self.d.freqs, term,
                                float(self.d.freqs[self.i]))

    @staticmethod
    def _kinds(elements):
        return [(e.kind, e.ports) for e in elements]

    def _assert_reconciles(self, ctx, label, a=0, b=0):
        dec = at.decompose(ctx, a, b, "Z")
        ref = complex(ctx.Zref[a, b])
        got = sum_of_terms(dec) if dec.terms else dec.total_sum
        # An exactly-zero mutual has no relative error; normalise by the
        # largest entry of Z instead.  None of the specs in this class produce
        # one today, but a budget of zero would be a trap for the next one
        # added -- it turns the assertion into "must be bit-exact".
        finite = np.abs(ctx.Zref)[np.isfinite(np.abs(ctx.Zref))]
        denom = abs(ref) if abs(ref) > 0 else (
            float(finite.max()) if finite.size else 1.0)
        allow, detail = condition_aware_budget(
            np.asarray(self.Y[self.i]), denom)
        self.assertLessEqual(
            abs(got - ref), allow,
            f"{label}: engine {ref!r} vs terms {got!r}, "
            f"|d| = {abs(got - ref):.3e} > {allow:.3e} [{detail}]")

    # ---- ground wins over a probe (modes 1/2/3 and the rows path) ----------

    def test_ground_wins_over_a_probe_and_becomes_an_element(self):
        """
        mode1 A=[1,2] gnd=[2,3].  build_terminations_mode1 assigns Signal
        first and Ground second into the same dict, so port 2 leaves the probe
        and becomes a ground ELEMENT -- it must appear in the contribution
        table, because it is carrying current the answer depends on.

        Mutation: make the port-2 declaration a probe side instead (drop the
        Ground) -> the element list loses ('ground', (1,)) and the answer moves
        by 0.8% at 5 GHz, which the reconciliation catches too.
        """
        overlap = self._ctx(build_terminations_mode1([1, 2], [2, 3]))
        explicit = self._ctx(build_terminations_mode1([1], [2, 3]))
        self.assertEqual(self._kinds(overlap.elements),
                         [("ground", (1,)), ("ground", (2,))])
        self.assertEqual(self._kinds(overlap.elements),
                         self._kinds(explicit.elements),
                         "the overlap spec must reduce to the explicit one")
        self.assertEqual(overlap.port_names, ["A"])
        self._assert_reconciles(overlap, "mode1 ground-wins overlap")
        # ... and the two really are the same measurement, as
        # test_core.py::test_ground_wins_is_visible_in_the_answer pins.
        self.assertLessEqual(
            abs(complex(overlap.Zop[0, 0]) - complex(explicit.Zop[0, 0])),
            1e-9 * abs(complex(explicit.Zop[0, 0])))

    def test_ground_wins_on_both_probe_sides_in_mode_2(self):
        """mode2 A=[1,2] B=[3] gnd=[2,4]: port 2 leaves the PLUS side."""
        ctx = self._ctx(build_terminations_mode2([1, 2], [3], [2, 4]))
        self.assertEqual(self._kinds(ctx.elements),
                         [("ground", (1,)), ("ground", (3,))])
        self._assert_reconciles(ctx, "mode2 ground-wins overlap")

    def test_the_rows_path_gets_the_same_elements_as_the_named_builder(self):
        """
        The connection table is a THIRD route to a TerminationSet and
        test_connection_rows.py pins that it reproduces the named modes
        including their overlaps.  It must therefore also produce the same
        attribution -- not merely the same total.

        Mutation: reverse rows_to_dsl_text's "measurement ports before
        connections" order (a documented invariant in core) -> the ground row
        stops winning, the element lists diverge, and this goes red while a
        totals-only comparison stays green on the fixtures where the two
        answers happen to be close.
        """
        pairs = [
            ("mode1", build_terminations_mode1([1], [2, 3, 4]),
             build_terminations_rows(
                 [MeasPortRow("A", "1", "")],
                 [ConnectionRow(kind="ground", ports="2-4")])),
            ("mode1 overlap", build_terminations_mode1([1, 2], [2, 3]),
             build_terminations_rows(
                 [MeasPortRow("A", "1,2", "")],
                 [ConnectionRow(kind="ground", ports="2,3")])),
            ("mode2 overlap", build_terminations_mode2([1, 2], [3], [2, 4]),
             build_terminations_rows(
                 [MeasPortRow("A", "1,2", "3")],
                 [ConnectionRow(kind="ground", ports="2,4")])),
            ("mode3", build_terminations_mode3([1], [2], [],
                                               parse_short_pairs("3-4")),
             build_terminations_rows(
                 [MeasPortRow("A", "1", "2")],
                 [ConnectionRow(kind="short", ports="3", to="4")])),
            ("mode3 chained short",
             build_terminations_mode3([1], [4], [], parse_short_pairs("1-2-3")),
             build_terminations_rows(
                 [MeasPortRow("A", "1", "4")],
                 [ConnectionRow(kind="short", ports="1", to="2,3")])),
        ]
        for label, named, rows in pairs:
            with self.subTest(case=label):
                cn, cr = self._ctx(named), self._ctx(rows)
                self.assertEqual(self._kinds(cn.elements),
                                 self._kinds(cr.elements),
                                 f"{label}: the rows path built different "
                                 f"elements")
                self.assertEqual(cn.port_names, cr.port_names)
                self.assertEqual(
                    [(e.kind, e.ports) for e, _ in cn.dropped],
                    [(e.kind, e.ports) for e, _ in cr.dropped],
                    f"{label}: the rows path discarded different declarations")
                self.assertLessEqual(
                    abs(complex(cn.Zop[0, 0]) - complex(cr.Zop[0, 0])),
                    1e-12 * abs(complex(cn.Zop[0, 0])))
                self._assert_reconciles(cr, f"rows/{label}")

    # ---- the Signal wins inside a shorted group ----------------------------

    def test_a_ground_shorted_onto_a_probe_is_thrown_away_not_applied(self):
        """
        merge_terms: a shorted group carrying any Signal IS that Signal, so a
        Ground shorted onto a probe port is discarded and the probe keeps the
        node.  Applying it instead would tie the probe to 0 V and report a
        plausible impedance for a node the engine believes is grounded.

        Both halves are asserted: that the ground is in `dropped` rather than
        `elements`, and that the spec is numerically identical to the same spec
        with the ground deleted.

        Measured at 5 GHz: Z_aa = 159.17 Ohm with the ground discarded, which
        is what compute_z_matrix says for both spellings, bit for bit.

        Mutation: disable the `on_probe and elem.kind in _SHUNT_KINDS` branch
        in build_context -> the ground becomes an element that pulls the probe
        node towards 0 V, and this is the ONLY test in the file that notices
        (mutation-checked: it went red alone).
        """
        with_gnd = self._ctx(parse_custom_termination_text(
            "1 signal A\n2 ground\n1 short_to 2\n4 ground\n"))
        without = self._ctx(parse_custom_termination_text(
            "1 signal A\n1 short_to 2\n4 ground\n"))
        self.assertEqual(self._kinds(with_gnd.elements), [("ground", (3,))])
        self.assertIn(("ground", (1,)),
                      [(e.kind, e.ports) for e, _ in with_gnd.dropped],
                      "the shorted-onto-a-probe ground vanished without being "
                      "accounted for")
        self.assertEqual(complex(with_gnd.Zref[0, 0]),
                         complex(without.Zref[0, 0]),
                         "core itself disagrees; this test's premise is wrong")
        self.assertLessEqual(
            abs(complex(with_gnd.Zop[0, 0]) - complex(without.Zop[0, 0])),
            1e-12 * abs(complex(without.Zop[0, 0])),
            "the attribution layer applied a ground the engine discarded")
        self._assert_reconciles(with_gnd, "ground shorted onto a probe")

    def test_a_short_group_that_inherits_a_signal_contributes_no_element(self):
        """
        mode3 A=[1] B=[2] short '2-3-4': ports 2, 3 and 4 merge and the group
        inherits Signal B, so both shorts land inside one probe side and are
        annihilated (u == 0).  An element list that still held them would
        invite the reader to attribute the answer to a stamp that summed to
        exactly zero.
        """
        ctx = self._ctx(build_terminations_mode3([1], [2], [],
                                                 parse_short_pairs("2-3-4")))
        self.assertEqual(self._kinds(ctx.elements), [])
        self.assertEqual([(e.kind, e.ports) for e, _ in ctx.dropped],
                         [("short", (1, 2)), ("short", (2, 3))])
        self._assert_reconciles(ctx, "mode3 short group inherits Signal B")

    # ---- the DSL is last-assignment-wins, and that changes the elements ----

    def test_the_dsl_order_decides_which_declaration_becomes_an_element(self):
        """
        `2 signal A / 2 ground` and `2 ground / 2 signal A` are DIFFERENT
        networks (test_core.py::test_dsl_last_line_wins_within_a_spec), and the
        attribution must follow the winner, not the loser.

        The two answers really do differ -- asserted, because a test that
        compared two identical numbers would pass with no precedence handling
        at all.
        """
        gnd_last = self._ctx(parse_custom_termination_text(
            "1 signal A\n2 signal A\n2 ground\n3 ground\n"))
        sig_last = self._ctx(parse_custom_termination_text(
            "2 ground\n3 ground\n1 signal A\n2 signal A\n"))
        self.assertEqual(self._kinds(gnd_last.elements),
                         [("ground", (1,)), ("ground", (2,))])
        self.assertEqual(self._kinds(sig_last.elements), [("ground", (2,))])
        self.assertGreater(
            abs(complex(gnd_last.Zref[0, 0]) - complex(sig_last.Zref[0, 0]))
            / abs(complex(sig_last.Zref[0, 0])), 1e-3,
            "the two orderings produced the same answer, so this test cannot "
            "tell the precedence apart")
        self._assert_reconciles(gnd_last, "DSL ground last")
        self._assert_reconciles(sig_last, "DSL signal last")

    # ---- and the coupling builder refuses the overlap, in core -------------

    def test_the_probe_model_refuses_the_overlap_before_attribution_sees_it(self):
        """
        build_terminations_coupling raises on probe-and-ground, and
        build_context calls compute_z_matrix FIRST precisely so the message the
        user reads is core's own rather than a second, near-identical one.

        Mutation: catch and swallow that ValueError anywhere in build_context
        -> red.
        """
        with self.assertRaises(ValueError) as cm:
            build_terminations_coupling([("tank", [1, 2], [3])], gnd_ports=[2],
                                        nports=4)
        self.assertIn("ground", str(cm.exception).lower())

        # A TerminationSet hand-built with the same conflict (two signal groups
        # merged by a short) is refused by compute_z_matrix, and build_context
        # must let that through untouched.
        bad = TerminationSet(
            per_port={0: Signal("P", +1), 1: Signal("Q", +1)},
            couplings=[ShortPair(0, 1)])
        with self.assertRaises(ValueError) as cm:
            at.build_context(self.Y, self.d.freqs, bad,
                             float(self.d.freqs[self.i]))
        self.assertIn("conflicting", str(cm.exception).lower())


# ===========================================================================
# 4. Reciprocity
# ===========================================================================

class TestReciprocity(unittest.TestCase):
    """
    Decomposing a<-b against decomposing b<-a.

    FINDING, and the reason this class is shaped the way it is.  The obvious
    thing to demand of a reciprocal file -- and what this suite was originally
    asked to assert -- is that the two directions agree TERM BY TERM.  They do
    not, and not because of an implementation slip: it is false in the
    mathematics, for any correct implementation.  With

        term_e(a<-b) = -[H^-1 p_b]_e * r_a[e]
        term_e(b<-a) = -[H^-1 p_a]_e * r_b[e]

    reciprocity (H symmetric, r == p) gives p_a^T H^-1 p_b == p_b^T H^-1 p_a,
    i.e. the SUMS agree.  The individual products do not, and they should not:
    driving b and driving a are two different physical situations in which the
    elements carry different currents.

    Measured on diff_pair_4port.s4p at 5 GHz with probes on 1 and 2 and grounds
    on 3 and 4: the two ground terms read (15.9, 7.93) Ohm one way and
    (7.93, 15.9) Ohm the other -- 50% apart term by term, identical summed to
    1.3e-12.  What the fixture's own 1<->2 / 3<->4 symmetry buys is that they
    are exactly PERMUTED, which is asserted below and is a far sharper check
    than "they are equal" would have been.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load("diff_pair_4port.s4p")
        # 5 GHz.  The 1 MHz end of this fixture is where the 1 fF port
        # capacitance's 159 kOhm sits over a 6 mOhm mutual and the reciprocity
        # of the TOTAL is only 3.1e-3 -- true of the arithmetic, not of the
        # module, and nothing to learn from.
        cls.i = 200
        cls.f = float(cls.d.freqs[cls.i])
        cls.two_grounds = build_terminations_coupling(
            [("c1", [1], []), ("c2", [2], [])], gnd_ports=[3, 4], nports=4)
        cls.one_ground = build_terminations_coupling(
            [("c1", [1], [3]), ("c2", [2], [])], gnd_ports=[4], nports=4)

    def _both(self, term):
        ctx = at.build_context(self.Y, self.d.freqs, term, self.f)
        return ctx, at.decompose(ctx, 0, 1, "Z"), at.decompose(ctx, 1, 0, "Z")

    def test_the_totals_agree_in_both_directions(self):
        """
        The property reciprocity actually gives, on both a one-element and a
        two-element spec.  Measured: 1.27e-12 relative on the two-ground spec
        and 3.93e-15 on the one-ground one, against engine-side asymmetries of
        8.05e-16 and 1.12e-16 on the same two pairs.

        Mutation: `r_a = ctx.Rmat[:, a]` -> `ctx.Rmat[:, b]` in decompose (the
        transimpedance taken to the wrong measurement port) -> red, and the
        mode-6 reconciliation and the fuzz go red with it.
        """
        for label, term in (("two grounds", self.two_grounds),
                            ("one ground", self.one_ground)):
            with self.subTest(spec=label):
                ctx, d01, d10 = self._both(term)
                s01, s10 = sum_of_terms(d01), sum_of_terms(d10)
                self.assertTrue(d01.terms and d10.terms)
                rel = abs(s01 - s10) / abs(s01)
                self.assertLessEqual(
                    rel, 1e-9,
                    f"{label}: sum(a<-b) = {s01!r} but sum(b<-a) = {s10!r}, "
                    f"{rel:.3e} relative; the engine's own asymmetry on this "
                    f"pair is "
                    f"{abs(ctx.Zref[0, 1] - ctx.Zref[1, 0]) / abs(ctx.Zref[0, 1]):.3e}")

    def test_a_single_element_split_IS_forced_to_agree_term_by_term(self):
        """
        With one element there is only one way to apportion a total both
        directions agree on, so here the term-by-term claim does hold -- and
        checking it is what makes the negative result below a statement about
        the mathematics rather than about a broken module.

        Measured: 4.4e-14 relative, against the 5.0e-01 the two-element spec
        gives.
        """
        ctx, d01, d10 = self._both(self.one_ground)
        self.assertEqual(len(ctx.elements), 1,
                         f"expected exactly one element, got "
                         f"{[e.describe() for e in ctx.elements]}")
        scale = max(abs(t.contribution) for t in d01.terms)
        for t1, t2 in zip(d01.terms, d10.terms):
            self.assertEqual(t1.label, t2.label)
            self.assertLessEqual(
                abs(t1.contribution - t2.contribution), 1e-9 * scale,
                f"'{t1.label}': {t1.contribution!r} vs {t2.contribution!r}")

    def test_with_two_elements_the_split_is_direction_dependent_by_design(self):
        """
        The finding, stated as an assertion so nobody "fixes" it: with two
        elements the per-element rows are 50% apart between the two directions
        while their sums agree to 1.3e-12.

        This is asserted as a LOWER bound on the disagreement.  If a future
        change made the two directions agree term by term it would be reporting
        something that is not superposition, and this goes red on purpose.
        """
        ctx, d01, d10 = self._both(self.two_grounds)
        self.assertEqual(len(ctx.elements), 2)
        scale = max(abs(t.contribution) for t in d01.terms)
        worst = max(abs(t1.contribution - t2.contribution)
                    for t1, t2 in zip(d01.terms, d10.terms))
        self.assertGreater(
            worst / scale, 1e-2,
            "the two directions now agree term by term. That is not what "
            "superposition gives: driving b and driving a put different "
            "currents through the same elements. Check what changed before "
            "relaxing this.")

    def test_and_on_a_symmetric_fixture_the_two_splits_are_a_permutation(self):
        """
        diff_pair_4port.s4p is two identical coupled lines, so relabelling
        (1<->2, 3<->4) is a symmetry of the network.  Under it, decomposing
        1<-2 maps onto decomposing 2<-1 with the ground on port 3 and the
        ground on port 4 exchanged -- which is exactly what the module
        produces, to 5.8e-12.

        That is the sharp version of the reciprocity check: it pins the
        currents AND the transimpedances, not just their sum.  The symmetry
        premise is asserted first, so the test cannot quietly become a
        tautology if the fixture is regenerated differently.

        Mutation: `I = Iall[:, b]` -> `Iall[:, a]` in decompose -> red here
        (0.5 relative) while the totals test above still passes for a == b.
        """
        Yk = np.asarray(self.Y[self.i])
        swap = np.array([[0, 1, 0, 0],
                         [1, 0, 0, 0],
                         [0, 0, 0, 1],
                         [0, 0, 1, 0]], dtype=float)
        asym = float(np.max(np.abs(swap @ Yk @ swap - Yk)))
        self.assertLessEqual(
            asym / float(np.max(np.abs(Yk))), 1e-12,
            "diff_pair_4port.s4p is no longer symmetric under (1<->2, 3<->4); "
            "the permutation this test asserts is a property of that symmetry")

        ctx, d01, d10 = self._both(self.two_grounds)
        self.assertEqual([t.label for t in d01.terms],
                         ["bare EM coupling", "ground port 3", "ground port 4"])
        permuted = [d10.terms[0], d10.terms[2], d10.terms[1]]
        scale = max(abs(t.contribution) for t in d01.terms)
        for t1, t2 in zip(d01.terms, permuted):
            self.assertLessEqual(
                abs(t1.contribution - t2.contribution), 1e-9 * scale,
                f"'{t1.label}' is {t1.contribution!r} one way and "
                f"'{t2.label}' is {t2.contribution!r} the other; the two "
                f"splits are not each other's port permutation")

    def test_the_reciprocity_diagnostic_is_reported_not_assumed(self):
        """
        Requirement 1: r_a is its own solve and |r_a - p_a| / |p_a| is reported.
        The fixtures are reciprocal to a few ulps, so what can be checked here
        is that the number exists, is finite, and is of the order the file
        really is -- not that it is large.
        """
        ctx, _d01, _d10 = self._both(self.two_grounds)
        self.assertTrue(math.isfinite(ctx.reciprocity_rel))
        engine = float(abs(ctx.Zref[0, 1] - ctx.Zref[1, 0])
                       / abs(ctx.Zref[0, 1]))
        self.assertLessEqual(
            ctx.reciprocity_rel, 1e-9,
            f"the module reports a reciprocity error of "
            f"{ctx.reciprocity_rel:.3e} on a fixture whose engine-side "
            f"asymmetry is {engine:.3e}")


# ===========================================================================
# 5. Flipping a measurement port's +/-
# ===========================================================================

class TestProbeSignSwap(unittest.TestCase):
    """
    SIGN_CONVENTION_TEXT: "Flipping either measurement port's +/- assignment
    flips every term together."  Swapping the VICTIM's sides negates w_a, hence
    r_a, hence every contribution -- while the aggressor's currents and both
    self impedances are untouched.

    This is a TOLERANCE test at 1e-9 and it has to be.  The engine side is NOT
    bit-identical under the swap: the probe sides are enumerated in declaration
    order, so swapping them permutes the node order, and the SVD inside
    _probe_impedance factors a permuted matrix differently.  Measured on
    coupled_4port_diff.s4p over the whole sweep: 8.09e-12 on Z_ab and 4.18e-11
    on Z_aa.  A bit-exact assertion here ships red on day one.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load("coupled_4port_diff.s4p")
        cls.dp, cls.Ydp = load("diff_pair_4port.s4p")

    def test_the_engine_itself_is_not_bit_identical_under_the_swap(self):
        """
        The evidence for the tolerance, asserted rather than asserted-about-in
        -a-comment: the disagreement is non-zero AND small.

        If this ever fails on the assertGreater it is not a regression -- it
        means the engine became exactly symmetric under the swap and the
        tolerances in this class may be tightened. The assertLessEqual is the
        real guard.
        """
        worst_ab = worst_aa = 0.0
        for i in range(0, len(self.d.freqs), 5):
            f = float(self.d.freqs[i])
            cn = at.build_context(self.Y, self.d.freqs, build_terminations_coupling(
                [("c1", [1], [2]), ("c2", [3], [4])], nports=4), f)
            cs = at.build_context(self.Y, self.d.freqs, build_terminations_coupling(
                [("c1", [2], [1]), ("c2", [3], [4])], nports=4), f)
            worst_ab = max(worst_ab, abs(cn.Zref[0, 1] + cs.Zref[0, 1])
                           / abs(cn.Zref[0, 1]))
            worst_aa = max(worst_aa, abs(cn.Zref[0, 0] - cs.Zref[0, 0])
                           / abs(cn.Zref[0, 0]))
        self.assertGreater(
            worst_ab, 0.0,
            "compute_z_matrix is now bit-exact under a probe swap; the 1e-9 "
            "tolerances in this class could be tightened (this is not a "
            "regression)")
        self.assertLessEqual(worst_ab, 1e-9, f"Z_ab moved by {worst_ab:.3e}")
        self.assertLessEqual(worst_aa, 1e-9, f"Z_aa moved by {worst_aa:.3e}")

    def test_every_term_negates_and_nothing_else_moves(self):
        """
        The spec has to have at least one element for this to say anything:
        a probe pair with nothing declared decomposes to the bare EM term
        alone, and "one term negated" is a much weaker statement than "every
        term negated together".  `c1 = 1/3, c2 = 2, gnd = 4` on the diff pair
        gives the bare term plus one ground.

        Mutation: `r_a = ctx.Rmat[:, a]` -> `ctx.Rmat[:, b]` in decompose ->
        the terms stop tracking the victim's sides and this goes red at 1e0.
        """
        nf = len(self.dp.freqs)
        for i in (nf // 4, nf // 2, nf - 1):
            with self.subTest(index=i):
                f = float(self.dp.freqs[i])
                normal = at.build_context(
                    self.Ydp, self.dp.freqs, build_terminations_coupling(
                        [("c1", [1], [3]), ("c2", [2], [])],
                        gnd_ports=[4], nports=4), f)
                swapped = at.build_context(
                    self.Ydp, self.dp.freqs, build_terminations_coupling(
                        [("c1", [3], [1]), ("c2", [2], [])],
                        gnd_ports=[4], nports=4), f)
                dn = at.decompose(normal, 0, 1, "Z")
                ds = at.decompose(swapped, 0, 1, "Z")

                self.assertEqual([t.label for t in dn.terms],
                                 ["bare EM coupling", "ground port 4"])
                self.assertEqual([t.label for t in ds.terms],
                                 [t.label for t in dn.terms])
                scale = max(abs(t.contribution) for t in dn.terms)
                for t1, t2 in zip(dn.terms, ds.terms):
                    self.assertLessEqual(
                        abs(t1.contribution + t2.contribution), 1e-9 * scale,
                        f"'{t1.label}' did not negate: {t1.contribution!r} "
                        f"-> {t2.contribution!r} "
                        f"(sum {abs(t1.contribution + t2.contribution):.3e}, "
                        f"scale {scale:.3e})")
                # ... and the self impedances, which do not involve w_a's sign
                # twice over, are unchanged.
                for who in (0, 1):
                    self.assertLessEqual(
                        abs(normal.Zop[who, who] - swapped.Zop[who, who]),
                        1e-9 * abs(normal.Zop[who, who]),
                        f"Z[{who},{who}] moved under a victim-side swap")

    def test_swapping_BOTH_measurement_ports_restores_every_term(self):
        """
        Two negations compose back to the identity -- the second half of "the
        relative signs are physical, the absolute signs are a labelling
        choice".  A test that only ever swapped one side could not tell a
        genuine sign convention from a sign error applied twice.
        """
        f = float(self.dp.freqs[len(self.dp.freqs) // 2])
        both = []
        for mports in ([("c1", [1], [3]), ("c2", [2], [4])],
                       [("c1", [3], [1]), ("c2", [4], [2])]):
            ctx = at.build_context(
                self.Ydp, self.dp.freqs,
                build_terminations_coupling(mports, nports=4), f)
            both.append(at.decompose(ctx, 0, 1, "Z"))
        scale = max(abs(t.contribution) for t in both[0].terms)
        for t1, t2 in zip(*[d.terms for d in both]):
            self.assertLessEqual(
                abs(t1.contribution - t2.contribution), 1e-9 * scale,
                f"'{t1.label}' did not come back: {t1.contribution!r} vs "
                f"{t2.contribution!r}")


# ===========================================================================
# 6. The fuzz: never silently wrong
# ===========================================================================

class TestNeverSilentlyWrong(unittest.TestCase):
    """
    Requirement 5 says the reconciliation DEGRADES rather than refusing.  The
    property that makes that acceptable is the two-sided one: for ANY spec, the
    decomposition either agrees with the engine inside the condition-aware
    budget, or it says out loud that it does not.  A wrong number with a
    warning on it is a usable tool; a wrong number without one is not.

    Random specs are the only way to reach this.  The curated cases above are
    all things a person would type; the interesting failures -- a probe whose
    every port ends up on one node of a floating structure, a ground shorted to
    a ground shorted to a probe -- are things a person types by accident.
    """

    #: Measured on this box: the whole class costs 1.8 s of the file's 2.1 s
    #: (the fixtures are 1- to 4-port, so a build_context is ~25 us).  Seeded,
    #: so a failure is reproducible from the trial number in the message.
    TRIALS = 4000
    SEED = 20260811

    FILES = ("diff_pair_4port.s4p", "decap_4port.s4p",
             "coupled_4port_diff.s4p", "coupled_4port_float.s4p",
             "pi_2port.s2p", "coupled_2port_gndref.s2p")

    CHOICES = ("open", "gnd", "vdd", "rl", "c",
               "sigA+", "sigA-", "sigP+", "sigQ+")

    def _termination(self, n: int, rng: random.Random) -> TerminationSet:
        per_port = {}
        for p in range(n):
            per_port[p] = {
                "open": Open(),
                "gnd": Ground(),
                "vdd": Vdd(),
                "rl": LumpedToGnd(y_series_rlc(R=0.5, L=1e-9)),
                "c": LumpedToGnd(y_capacitor(2e-12)),
                "sigA+": Signal("A", +1),
                "sigA-": Signal("A", -1),
                "sigP+": Signal("P", +1),
                "sigQ+": Signal("Q", +1),
            }[rng.choice(self.CHOICES)]
        couplings = []
        for _ in range(rng.randint(0, 2)):
            i, j = rng.sample(range(n), 2)
            couplings.append(
                ShortPair(i, j) if rng.random() < 0.6
                else LumpedBetween(i, j, y_series_rlc(R=1.0, L=0.5e-9)))
        return TerminationSet(per_port=per_port, couplings=couplings)

    def test_a_disagreement_with_the_engine_is_never_silent(self):
        """
        Measured over 4000 trials / 4490 (spec, pair) evaluations: 3550 inside
        the budget, 641 outside it and EVERY ONE of those loud (the split
        withheld, a warning, or both), 299 where the engine's own value is not
        finite, 0 silent.

        Mutation: `trustworthy = False` -> `True` in decompose's catastrophic
        branch AND dropping the "Reconciliation:" warning -> 641 silent
        failures, each printed with its spec.  Mutating only one of the two
        leaves the other as the loud channel, which is the point of accepting
        either.
        """
        rng = random.Random(self.SEED)
        clean = flagged = nonfinite = 0
        silent: list[str] = []
        for trial in range(self.TRIALS):
            name = rng.choice(self.FILES)
            d, Y = load(name)
            term = self._termination(int(d.s.shape[1]), rng)
            # The bottom eighth of every sweep is the 1 MHz corner where the
            # port capacitance's 159 kOhm sits over a milliohm answer; those
            # points are covered deliberately and by name in
            # test_where_arithmetic_guarantees_nothing_the_module_says_so, and
            # including them here would make 90% of the trials unbounded.
            i = rng.randrange(len(d.freqs) // 8, len(d.freqs))
            try:
                ctx = at.build_context(Y, d.freqs, term, float(d.freqs[i]))
            except ValueError:
                continue          # core refused the spec; that is not silence
            G = len(ctx.port_names)
            for a, b in itertools.product(range(G), repeat=2):
                dec = at.decompose(ctx, a, b, "Z")
                ref = complex(ctx.Zref[a, b])
                got = sum_of_terms(dec) if dec.terms else dec.total_sum
                loud = bool(dec.warnings) or (not dec.split_trustworthy)
                if not (math.isfinite(abs(ref)) and math.isfinite(abs(got))):
                    nonfinite += 1
                    if math.isfinite(abs(ref)) != math.isfinite(abs(got)) \
                            and not loud:
                        silent.append(
                            f"trial {trial} {name} index {i} pair ({a},{b}): "
                            f"engine {ref!r} but decomposition {got!r}, "
                            f"no warning")
                    continue
                finite = np.abs(ctx.Zref)[np.isfinite(np.abs(ctx.Zref))]
                denom = abs(ref) if abs(ref) > 0 else (
                    float(finite.max()) if finite.size else 0.0)
                if denom == 0.0:
                    continue
                allow, detail = condition_aware_budget(np.asarray(Y[i]), denom)
                err = abs(got - ref)
                if err <= allow:
                    clean += 1
                elif loud:
                    flagged += 1
                else:
                    silent.append(
                        f"trial {trial} {name} index {i} "
                        f"({format_freq(ctx.freq_hz)}) pair ({a},{b}): "
                        f"engine {ref!r} vs terms {got!r}, |d| = {err:.3e} > "
                        f"{allow:.3e} [{detail}]; module reported residual "
                        f"{dec.residual_rel:.3e} against floor "
                        f"{dec.residual_floor:.3e} with no warning. "
                        f"Elements: {[e.describe() for e in ctx.elements]}")
        self.assertEqual(
            silent, [],
            f"{len(silent)} of {clean + flagged + nonfinite} evaluations "
            f"disagreed with the engine WITHOUT saying so:\n  "
            + "\n  ".join(silent[:10]))
        # The two counts below are what stop this from being a test that passes
        # because nothing was exercised.
        self.assertGreater(clean, 1000,
                           f"only {clean} evaluations were inside the budget; "
                           "the fuzz stopped generating usable specs")
        self.assertGreater(flagged, 20,
                           f"only {flagged} evaluations exercised the "
                           "degradation path; requirement 5's behaviour is no "
                           "longer being tested")

    def test_the_fuzz_never_escapes_as_a_bare_traceback(self):
        """
        Same contract TouchstoneParseError has: a refusal is a ValueError with
        the whole verdict in str(e).  `AttribError` subclasses ValueError, so
        one `except ValueError` covers the module and core alike -- and
        anything else (a KeyError from a dict of terminations, an IndexError
        from a port index) is a bug, not a refusal.

        Mutation: replace any `raise AttribError(...)` with a bare `assert` ->
        red.
        """
        rng = random.Random(self.SEED + 1)
        escaped: list[str] = []
        for trial in range(600):
            name = rng.choice(self.FILES)
            d, Y = load(name)
            term = self._termination(int(d.s.shape[1]), rng)
            i = rng.randrange(len(d.freqs))
            try:
                ctx = at.build_context(Y, d.freqs, term, float(d.freqs[i]))
                for a, b in itertools.product(
                        range(len(ctx.port_names)), repeat=2):
                    at.decompose(ctx, a, b, "M")
                    at.transfer_ratio(ctx, a, b)
            except ValueError:
                continue
            except Exception as exc:                     # pragma: no cover
                escaped.append(f"trial {trial} {name} index {i}: "
                               f"{type(exc).__name__}: {exc}")
        self.assertEqual(escaped, [], "\n  ".join(escaped))


# ===========================================================================
# 7. The golden reference itself
# ===========================================================================

class TestGoldenReferenceUntouched(unittest.TestCase):
    """
    CLAUDE.md: "tests/fixtures/golden_legacy.npz is the guard for all of the
    above... If it fails, the reduction path changed: fix the change, do not
    regenerate the reference to make the test pass."

    A new module that reduces the same network is exactly the kind of work that
    tempts someone to regenerate it, so both halves of that sentence are
    asserted here, next to the suite that leans on the file.
    """

    def test_the_golden_regression_is_green(self):
        """
        Run in-process (0.3 s for its four tests) rather than as a subprocess:
        the point is that this suite cannot be read as green while the
        reference it anchors on is red.
        """
        suite = unittest.TestLoader().loadTestsFromName("test_golden_regression")
        buf = io.StringIO()
        result = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
        self.assertGreaterEqual(result.testsRun, 1,
                                "test_golden_regression loaded no tests")
        self.assertTrue(result.wasSuccessful(),
                        "the golden regression is RED:\n" + buf.getvalue())

    def test_golden_legacy_npz_is_unmodified_in_git(self):
        """
        `git status --porcelain` on the one file, not on the tree: other files
        are legitimately dirty while a feature is being built, and a test that
        demanded a clean tree would be noise every single run.

        Mutation: `np.savez_compressed(GOLDEN_NPZ, ...)` anywhere -> red.
        """
        rel = "tests/fixtures/golden_legacy.npz"
        try:
            out = subprocess.run(
                ["git", "-C", str(_ROOT), "status", "--porcelain", "--", rel],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            self.skipTest(f"git is not usable here: {exc}")
        if out.returncode != 0:                          # pragma: no cover
            self.skipTest(f"git status failed: {out.stderr.strip()}")
        self.assertEqual(
            out.stdout.strip(), "",
            f"{rel} has been modified. It is the bit-exact reference for the "
            "whole reduction path and must not be regenerated to make a test "
            "pass:\n" + out.stdout)

    def test_the_reference_still_contains_every_case_this_file_walks(self):
        """
        A regenerated-and-shrunk reference would make
        TestGoldenRegistryReconciles silently narrower rather than red.
        """
        gcap.ensure_fixtures()
        gold = np.load(gcap.GOLDEN_NPZ, allow_pickle=False)
        stored = {str(x) for x in gold[gcap.Z_IDS_KEY]}
        registry = {c.case_id for c in gcap.Z_CASES}
        self.assertEqual(
            registry - stored, set(),
            "the case registry has cases the golden reference does not: "
            f"{sorted(registry - stored)}")


if __name__ == "__main__":                               # pragma: no cover
    unittest.main(verbosity=2)
