"""
Acceptance tests for `pkg_rlc_attrib` -- the attribution / sensitivity engine.

The whole module rests on ONE claim: that its node-space decomposition is the
same network compute_z_matrix reduces, so its terms may be added up and its
what-ifs believed.  Almost every test below is therefore a comparison against
the engine, not against a hand-written number:

  * `decompose()`'s sum against compute_z_matrix on four fixtures;
  * every FAST sensitivity result against an honest REBUILD of the
    TerminationSet put through compute_z_matrix.  That is the single most
    important test in this file -- the fast path reuses one factorisation of
    the baseline and touches only an m x m system, and if it ever stopped
    meaning the same thing as "change the spec and press Calculate" the whole
    feature would be confidently wrong;
  * the closed-form Mobius sweep against direct evaluation at both endpoints
    and in between.

Where a number IS hard-coded it was MEASURED in this session and the
measurement is written next to it, because these are properties of the
fixtures, not of arithmetic.

Every guard here was mutation-checked: the mutation that would defeat it is
named in the test's own docstring or comment.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import numpy as np  # noqa: E402

import pkg_rlc_attrib as at  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    Ground,
    LumpedBetween,
    LumpedToGnd,
    ShortPair,
    TerminationSet,
    build_terminations_mode2,
    compute_z_matrix,
    parse_custom_termination_text,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
)

FIXTURES = _HERE / "fixtures"

# Fixtures and the port configuration each one is exercised with.  These are
# not arbitrary: they are the four the module's contract names, plus the
# floating one that used to be red on day one.
DIFF_PAIR = "diff_pair_4port.s4p"          # lines 1->3 and 2->4, L=5n, M=1n
COUPLED_DIFF = "coupled_4port_diff.s4p"    # coils 1-2 and 3-4, M=800 pH
COUPLED_FLOAT = "coupled_4port_float.s4p"  # the same, no shunt C -> singular Y
COUPLED_GNDREF = "coupled_2port_gndref.s2p"
DECAP = "decap_4port.s4p"                  # two UNCOUPLED pi networks


def load(name: str):
    d = parse_touchstone(FIXTURES / name)
    return d, s_to_y(d.s, d.z0)


def mid_freq(d) -> float:
    return float(d.freqs[len(d.freqs) // 2])


def honest_zab(Y, freqs, ts: TerminationSet, freq_hz: float,
               a: int = 0, b: int = 1) -> complex:
    """
    The answer you get by editing the spec and pressing Calculate.

    Deliberately the SLOW route: a whole new TerminationSet through
    compute_z_matrix over the whole sweep, with no reuse of anything.  This is
    the reference every fast path in pkg_rlc_attrib is measured against.
    """
    Z, _names, _w = compute_z_matrix(Y, freqs, ts)
    idx = int(np.argmin(np.abs(np.asarray(freqs) - freq_hz)))
    return complex(Z[idx][a, b])


def clone(ts: TerminationSet) -> TerminationSet:
    return TerminationSet(per_port=dict(ts.per_port),
                          couplings=list(ts.couplings))


# ---------------------------------------------------------------------------


class TestReconciliation(unittest.TestCase):
    """
    decompose()'s own sum against compute_z_matrix, on the four fixtures the
    contract names, with the condition-aware tolerance.
    """

    CASES = [
        ("coupled_4port_diff, differential probes", COUPLED_DIFF,
         "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n"),
        ("coupled_4port_diff, single-ended + 2 grounds", COUPLED_DIFF,
         "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n"),
        ("diff_pair, near ends probed, far ends grounded", DIFF_PAIR,
         "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n"),
        ("diff_pair, lumped grounds", DIFF_PAIR,
         "1 signal c1 +\n2 signal c2 +\n"
         "3 lumped_to_gnd R=0.1 L=1n\n4 lumped_to_gnd R=0.1 L=1n\n"),
        ("diff_pair, short + ground", DIFF_PAIR,
         "1 signal c1 +\n2 signal c2 +\n3 short_to 4\n4 ground\n"),
        ("decap, two uncoupled pi networks", DECAP,
         "1 signal s +\n3 signal c +\n2 ground\n4 ground\n"),
        ("coupled_2port_gndref", COUPLED_GNDREF,
         "1 signal c1 +\n2 signal c2 +\n"),
    ]

    def test_the_sum_matches_the_engine_within_the_condition_aware_floor(self):
        """
        A FIXED tolerance is what this must not have.  With a fixed 1e-9 gate
        the two ill-conditioned cases below fail; with a fixed 1e-2 gate the
        wrong-node-index bug this suite caught during development (measured:
        0.75 relative on coupled_4port_float with two grounds) walks straight
        through.
        """
        for label, fixture, spec in self.CASES:
            with self.subTest(label):
                d, Y = load(fixture)
                ts = parse_custom_termination_text(spec)
                f0 = mid_freq(d)
                ctx = at.build_context(Y, d.freqs, ts, f0)
                dec = at.decompose(ctx, 0, 1, "Z")
                self.assertTrue(
                    math.isfinite(dec.residual_rel),
                    f"{label}: residual is {dec.residual_rel}")
                self.assertLessEqual(
                    dec.residual_rel, dec.residual_floor,
                    f"{label}: residual {dec.residual_rel:.3g} over floor "
                    f"{dec.residual_floor:.3g}")
                self.assertTrue(dec.split_trustworthy, label)

    def test_the_engine_is_the_authority_and_the_two_agree_to_1e_12(self):
        """
        Not the same assertion as the one above: this one pins the ABSOLUTE
        level of agreement on well-conditioned specs, so a change that quietly
        widens the floor cannot hide a real drift behind it.
        """
        for label, fixture, spec in self.CASES:
            with self.subTest(label):
                d, Y = load(fixture)
                ts = parse_custom_termination_text(spec)
                f0 = mid_freq(d)
                ctx = at.build_context(Y, d.freqs, ts, f0)
                ref = honest_zab(Y, d.freqs, ts, f0)
                got = complex(ctx.Zop[0, 1])
                scale = max(abs(ref), float(np.max(np.abs(ctx.Zref))))
                self.assertLess(abs(got - ref) / scale, 1e-12, label)

    def test_the_terms_sum_to_the_total(self):
        """
        The decomposition is superposition, not a fit: the sum is an identity,
        so it holds to roundoff and not to 'about right'.  Mutating the sign of
        the per-element term (`+I*r` instead of `-I*r`) is what this catches.
        """
        for label, fixture, spec in self.CASES:
            with self.subTest(label):
                d, Y = load(fixture)
                ts = parse_custom_termination_text(spec)
                ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
                dec = at.decompose(ctx, 0, 1, "Z")
                total = sum((t.contribution for t in dec.terms),
                            start=complex(0.0))
                # decap's two pi networks are uncoupled BY CONSTRUCTION, so
                # every term is exactly 0 and so is the scale -- assertLess
                # would fail on 0 < 0 for a decomposition that is perfect.
                scale = max(abs(dec.total_sum),
                            max(abs(t.contribution) for t in dec.terms))
                self.assertLessEqual(abs(total - dec.total_sum),
                                     1e-12 * scale, label)

    def test_a_real_quantity_maps_the_whole_decomposition_by_one_scalar(self):
        """M is Im(Z)/omega term by term, and the terms still add up."""
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        ctx = at.build_context(Y, d.freqs, ts, f0)
        dz = at.decompose(ctx, 0, 1, "Z")
        dm = at.decompose(ctx, 0, 1, "M")
        om = 2.0 * math.pi * ctx.freq_hz
        for tz, tm in zip(dz.terms, dm.terms):
            self.assertAlmostEqual(tm.contribution.real, tz.contribution.imag / om,
                                   delta=1e-18)
            self.assertEqual(tm.contribution.imag, 0.0)
        self.assertAlmostEqual(
            sum(t.contribution.real for t in dm.terms),
            dm.total_sum.real, delta=1e-21)

    def test_the_frequency_is_the_nearest_grid_point_and_is_reported(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        want = float(d.freqs[7]) + 0.13 * float(d.freqs[7] - d.freqs[6])
        ctx = at.build_context(Y, d.freqs, ts, want)
        self.assertEqual(ctx.freq_hz, float(d.freqs[7]))
        self.assertEqual(ctx.requested_hz, want)


class TestSingularBaseline(unittest.TestCase):
    """
    Requirement 3.  coupled_4port_float.s4p is the repo's flagship Mode 6
    example (theory.md and the README both use it) and its Y is singular by
    construction: MEASURED cond(Y) = 2.48e16 at 5.1 GHz.  A naive
    inv(Ybase) is red on this file on day one.
    """

    def test_the_flagship_floating_fixture_is_singular(self):
        """The precondition, asserted rather than assumed."""
        d, Y = load(COUPLED_FLOAT)
        idx = len(d.freqs) // 2
        self.assertGreater(float(np.linalg.cond(Y[idx])), 1e15)

    def test_the_floating_fixture_decomposes_exactly(self):
        """
        No element to fold here -- every port carries a probe -- so this is the
        pinv half of requirement 3: the balanced +/- injection is orthogonal to
        the common-mode null direction, exactly as _probe_impedance relies on.
        Reverting the pinv branch to np.linalg.solve turns the answer to noise.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertTrue(ctx.baseline_singular)
        dec = at.decompose(ctx, "c1", "c2", "M")
        # M = 800 pH exactly by construction (tests/generate_test_snp.py).
        self.assertAlmostEqual(dec.total_sum.real, 8e-10, delta=1e-15)
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_the_error_bound_uses_the_RETAINED_spectrum_not_plain_cond(self):
        """
        cond(Ybase) on this fixture is 2.5e16 by design.  Feeding that into the
        reconciliation floor gives 64 * 2.5e16 * eps = 350, the floor clamps to
        1, and the gate is switched off for every floating structure in the
        tool -- silently, because nothing fails.  The floor must instead be
        built from the singular values pinv actually keeps, which here leaves
        it below 1e-12 while the residual sits at 1e-16.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertGreater(ctx.cond_Ybase, 1e15)
        self.assertLess(ctx.cond_Ybase_eff, 1e3)
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertLess(dec.residual_floor, 1e-12)
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_a_ground_that_the_structure_has_no_reference_without_is_folded(self):
        """
        The fold half of requirement 3.  With the two coils' far ends grounded
        the all-open baseline is rank-deficient by two, and the two grounds are
        exactly the out-of-range elements.  They go INTO the baseline, lose
        their term, and the context says so by port number.

        This is the test that caught the reduced-vs-original node index bug in
        the fold loop: before the fix M came back 4.00e-10 H against the
        engine's 8.00e-10 -- a clean factor of two, nothing raised.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(len(ctx.folded), 2)
        self.assertEqual(ctx.n_elements, 0)
        note = " ".join(ctx.notes)
        self.assertIn("2,4", note)
        self.assertIn("no reference without them", note)
        # Folding made it well conditioned again -- that is the point.
        self.assertLess(ctx.cond_Ybase, 1e6)
        dec = at.decompose(ctx, 0, 1, "M")
        self.assertAlmostEqual(dec.total_sum.real, 8e-10, delta=1e-15)
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_a_live_element_survives_alongside_a_pseudo_inverted_baseline(self):
        """
        Singular baseline AND a real element in the split, which is the only
        configuration in which the pinv branch's `Zb.T @ W` is used at all --
        with no elements, r_a is never touched and a conjugate transpose there
        cannot be seen.  MEASURED: cond(Ybase) = 1.1e16, the ground on port 4
        is folded, and the surviving element carries 2.63 - 25.3j Ohm of a
        25.6j Ohm total, so a wrong transpose is a 100% error.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 ground\n"
            "3 lumped_between 4 R=10\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertTrue(ctx.baseline_singular)
        self.assertEqual(len(ctx.folded), 1)
        self.assertEqual(ctx.n_elements, 1)
        dec = at.decompose(ctx, 0, 1, "Z")
        elem_term = next(t for t in dec.terms if t.element is not None)
        self.assertGreater(abs(elem_term.contribution),
                           0.5 * abs(dec.total_sum))
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_folding_is_minimal_when_one_reference_is_enough(self):
        """
        A structure that floats in ONE direction must lose ONE element to the
        baseline, not all of them.  Folding every out-of-range element at once
        would leave a spec with 60 ground balls with no terms at all.
        """
        d, Y = load(COUPLED_FLOAT)
        # coil 2 is probed differentially (its own return), coil 1 is
        # ground-referenced through two declared grounds.  Only coil 1's
        # subnetwork lacks a reference, so exactly one fold is needed.
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 ground\n3 signal c2 +\n4 signal c2 -\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(len(ctx.folded), 1)
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)


class TestEnginePrecedence(unittest.TestCase):
    """
    Requirement 12.  The attribution layer has to build its probe nodes the way
    merge_terms does, or the reconciliation fails on exactly the specs it
    exists to guard.
    """

    def test_a_ground_shorted_onto_a_probe_is_discarded_not_applied(self):
        """
        merge_terms lets the Signal win over the Ground on a merged node, so
        the probe is NOT pulled to 0 V.  Treating the ground as a live element
        (the obvious reading of the TerminationSet) grounds the probe and the
        self impedance collapses from 16 kOhm to nothing.

        The strongest form of the claim is the last assertion: with the ground
        line and without it must be the SAME network, bit for bit.
        """
        d, Y = load(DIFF_PAIR)
        f0 = mid_freq(d)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n3 short_to 1\n")
        ctx = at.build_context(Y, d.freqs, ts, f0)
        kinds = {e.kind for e in ctx.elements}
        self.assertNotIn("ground", kinds)
        why = " ".join(w for _e, w in ctx.dropped)
        self.assertIn("Signal", why)

        # The self impedance is what tells whether the ground was applied.
        dec = at.decompose(ctx, 0, 0, "Z")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        self.assertGreater(abs(dec.total_sum), 1e3)

        no_ground = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 short_to 1\n")
        other = at.build_context(Y, d.freqs, no_ground, f0)
        self.assertEqual(complex(ctx.Zref[0, 0]), complex(other.Zref[0, 0]))
        self.assertLess(abs(ctx.Zop[0, 0] - other.Zop[0, 0]),
                        1e-12 * abs(ctx.Zop[0, 0]))

    def test_the_named_modes_reconcile_too(self):
        """
        build_terminations_mode2 lets ground win over a probe at BUILD time
        (last assignment into per_port), which is a different mechanism from
        merge_terms.  Both have to land in the same node space.
        """
        d, Y = load(DIFF_PAIR)
        ts = build_terminations_mode2([1], [2], [3, 4])
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(ctx.port_names, ["A"])
        dec = at.decompose(ctx, 0, 0, "Z")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_a_lumped_element_in_parallel_with_a_short_carries_no_current(self):
        """
        `3 short_to 4` next to `3 lumped_between 4 R=20` is the case
        inert_lumped_messages exists for: the engine merges the two ports first
        and the resistor's stamp sums to exactly zero.

        Here a short is an ELEMENT, not a node merge, so the resistor survives
        into the table -- and says the same thing more usefully by carrying no
        current.  MEASURED against the 1 A drive: 9.1e-14 A through the 20 Ohm
        resistor, and the whole answer is unchanged when R goes 20 -> 2000,
        which is exactly the symptom CLAUDE.md records for this spec.  The
        structural rank check flags the pair on top of that.
        """
        d, Y = load(DIFF_PAIR)
        f0 = mid_freq(d)
        base = ("1 signal c1 +\n2 signal c2 +\n3 short_to 4\n"
                "3 lumped_between 4 R=%s\n4 ground\n")
        ts = parse_custom_termination_text(base % "20")
        ctx = at.build_context(Y, d.freqs, ts, f0)
        described = [e.describe() for e in ctx.elements]
        self.assertIn("port 3-4", described)
        self.assertEqual(ctx.dependent, [2])
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        res = next(t for t in dec.terms
                   if t.element is not None
                   and t.element.kind == "lumped_between")
        self.assertLess(abs(res.current), 1e-10)

        big = at.build_context(
            Y, d.freqs, parse_custom_termination_text(base % "2000"), f0)
        other = at.decompose(big, 0, 1, "Z")
        self.assertLess(abs(other.total_sum - dec.total_sum),
                        1e-11 * abs(dec.total_sum))

    def test_a_probe_side_short_really_is_annihilated(self):
        """
        The one case that IS inert here: both ends of the short land on the
        same probe node, so u == 0 and the stamp is nothing at all.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c1 +\n2 signal c2 +\n1 short_to 3\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(ctx.n_elements, 0)
        dropped = {e.describe(): w for e, w in ctx.dropped}
        self.assertIn("short 1-3", dropped)
        self.assertIn("same node", dropped["short 1-3"])


class TestStructuralRank(unittest.TestCase):
    """
    Requirement 4: a redundant SPEC is a spec bug, reported structurally and by
    name, before anyone looks at a condition number.
    """

    def test_a_short_between_two_grounded_ports_is_named(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(len(ctx.elements), 3)
        self.assertEqual(ctx.dependent, [2])
        note = " ".join(ctx.notes)
        self.assertIn("REDUNDANT", note)
        self.assertIn("short 3-4", note)

    def test_the_total_survives_a_redundant_spec(self):
        """
        The ambiguity is confined to null(U), and r_a^T n = w_a^T Zbase U n = 0
        for every n in it -- so the victim cannot see it and the total is still
        exact.  Only the SPLIT is one of many, and the warning says so.
        """
        d, Y = load(DIFF_PAIR)
        good = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        bad = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        f0 = mid_freq(d)
        a = at.decompose(at.build_context(Y, d.freqs, good, f0), 0, 1, "Z")
        b = at.decompose(at.build_context(Y, d.freqs, bad, f0), 0, 1, "Z")
        self.assertLess(abs(a.total_sum - b.total_sum),
                        1e-10 * abs(a.total_sum))
        self.assertTrue(any("MINIMUM-NORM" in w for w in b.warnings),
                        b.warnings)

    def test_a_singular_H_does_not_switch_the_reconciliation_gate_off(self):
        """
        The redundant spec makes H exactly singular -- MEASURED cond(H) =
        1.19e16.  Building the error bound from that puts the floor at
        64 * 506 * 1.19e16 * eps = 4e4, it clamps to 1, and the gate is dead
        for every redundantly-spelled spec with nothing failing to say so.
        Built from the RETAINED spectrum (cond 3.0) the floor is 7.2e-9 while
        the residual is 3.5e-13, i.e. the gate still bites.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertGreater(dec.cond_H, 1e15)
        self.assertLess(dec.residual_floor, 1e-6)
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)

    def test_a_healthy_spec_is_not_accused(self):
        """
        The false-alarm case.  A numeric rank test on U would flag any spec
        whose elements happen to be nearly dependent; the exact integer test
        must not.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(ctx.dependent, [])
        self.assertFalse(any("REDUNDANT" in n for n in ctx.notes))

    def test_the_exact_column_test_is_exact(self):
        """
        Pure, no Tk, no data: e_p, e_q, e_p - e_q is rank 2 and the THIRD
        column is the dependent one.  A float rank test with a loose tolerance
        would also pass this; the point is that the reported INDEX is right.
        """
        U = np.array([[1, 0, 1], [0, 1, -1], [0, 0, 0]], dtype=np.int64)
        self.assertEqual(at._dependent_columns(U), [2])
        self.assertEqual(
            at._dependent_columns(np.array([[1, 0], [0, 1]], dtype=np.int64)),
            [])


class TestReciprocityIsNotAssumed(unittest.TestCase):
    """
    Requirement 1.  r_a is its own solve against Ybase.T -- never p_a reused,
    and never a conjugate transpose.
    """

    @staticmethod
    def _non_reciprocal(Y: np.ndarray, level: float) -> np.ndarray:
        """Y plus an antisymmetric perturbation, i.e. a gyrator-ish network."""
        Yn = np.array(Y, dtype=complex, copy=True)
        scale = float(np.max(np.abs(Y)))
        K = np.zeros(Y.shape[1:], dtype=complex)
        K[0, 1] = level * scale
        K[1, 0] = -level * scale
        return Yn + K[None, :, :]

    def test_r_and_p_differ_on_a_non_reciprocal_network(self):
        d, Y = load(DIFF_PAIR)
        Yn = self._non_reciprocal(Y, 1e-3)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Yn, d.freqs, ts, mid_freq(d))
        self.assertGreater(ctx.reciprocity_rel, 1e-6)

    def test_the_decomposition_still_matches_the_engine(self):
        """
        THE guard on requirement 1.  Reusing p_a for r_a passes on every
        reciprocal fixture in the repo and fails here; so does swapping the
        transpose for a conjugate transpose, which is the easy numpy slip on a
        complex-SYMMETRIC (not Hermitian) Y.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        for level in (1e-3, 1e-1):
            with self.subTest(level=level):
                Yn = self._non_reciprocal(Y, level)
                ctx = at.build_context(Yn, d.freqs, ts, f0)
                dec = at.decompose(ctx, 0, 1, "Z")
                self.assertLessEqual(dec.residual_rel, dec.residual_floor)
                ref = honest_zab(Yn, d.freqs, ts, f0, 0, 1)
                self.assertLess(abs(dec.total_sum - ref) / abs(ref), 1e-10)

    def test_the_victim_and_the_aggressor_are_not_interchangeable(self):
        """
        Z_ab != Z_ba on a non-reciprocal network, and the decomposition must
        report the asymmetry rather than smooth it away.
        """
        d, Y = load(DIFF_PAIR)
        Yn = self._non_reciprocal(Y, 1e-1)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Yn, d.freqs, ts, mid_freq(d))
        ab = at.decompose(ctx, 0, 1, "Z").total_sum
        ba = at.decompose(ctx, 1, 0, "Z").total_sum
        self.assertGreater(abs(ab - ba) / abs(ab), 1e-6)


class TestDenseTerminationImpedance(unittest.TestCase):
    """
    Requirement 2.  H = Zt + G takes a dense Zt with no change to the maths, and
    the difference between "each ball has its own 1 nH" and "the balls share one
    1 nH return" is not a rounding detail.
    """

    def test_the_builders_build_what_they_say(self):
        d = at.termination_impedance_diagonal([1j, 2j, 3j])
        self.assertTrue(np.array_equal(d, np.diag([1j, 2j, 3j])))
        s = at.termination_impedance_shared_return(1j, 10j, 3)
        self.assertEqual(s.shape, (3, 3))
        self.assertEqual(s[0, 0], 11j)
        self.assertEqual(s[0, 1], 10j)
        self.assertEqual(s[2, 1], 10j)

    def test_shared_return_moves_M_by_6_dB_on_a_multi_ground_fixture(self):
        """
        MEASURED here, on diff_pair_4port.s4p with probes on ports 1 and 2 and
        the far ends 3 and 4 declared as grounds, at 5.0 GHz:

            independent 1 nH per ball : M = 1.012 nH
            one shared 1 nH return    : M = 2.032 nH     -> +6.06 dB

        That is larger than the 6 dB dispute this feature exists to settle, and
        it is why an independent-ground model is not a safe default.  Both
        numbers come from the same H = Zt + G with the same cost.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = float(d.freqs[np.argmin(np.abs(d.freqs - 5e9))])
        om = 2.0 * math.pi * f0
        m = 2
        zd = at.termination_impedance_diagonal([1j * om * 1e-9] * m)
        zs = at.termination_impedance_shared_return(1j * om * 1e-9,
                                                    1j * om * 1e-9, m)
        md = at.decompose(at.build_context(Y, d.freqs, ts, f0, zt=zd),
                          0, 1, "M").total_sum.real
        ms = at.decompose(at.build_context(Y, d.freqs, ts, f0, zt=zs),
                          0, 1, "M").total_sum.real
        self.assertAlmostEqual(md, 1.012e-9, delta=0.005e-9)
        self.assertAlmostEqual(ms, 2.032e-9, delta=0.005e-9)
        self.assertAlmostEqual(20.0 * math.log10(abs(ms / md)), 6.06,
                               delta=0.05)

    def test_a_diagonal_ground_model_says_so(self):
        """
        The note is the whole safety net for a default that is right for the
        spec and wrong for a package.  It must NOT fire on an all-ideal spec --
        an ideal ground is ideal, shared or not.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        om = 2.0 * math.pi * f0
        ideal = at.build_context(Y, d.freqs, ts, f0)
        self.assertFalse(any("DIAGONAL" in n for n in ideal.notes))
        lumped = at.build_context(
            Y, d.freqs, ts, f0,
            zt=at.termination_impedance_diagonal([1j * om * 1e-9] * 2))
        self.assertTrue(any("DIAGONAL" in n for n in lumped.notes))
        shared = at.build_context(
            Y, d.freqs, ts, f0,
            zt=at.termination_impedance_shared_return(1j * om * 1e-9,
                                                      1j * om * 1e-9, 2))
        self.assertFalse(any("DIAGONAL" in n for n in shared.notes))

    def test_a_what_if_swap_replaces_the_element_mutuals_too(self):
        """
        The documented rule: swapping one element to an alternative makes it an
        UNCOUPLED two-terminal component -- its whole Zt row and column go, not
        just the diagonal.  Leaving the mutuals behind models an ideal short
        that is still magnetically coupled to its neighbours, which is not what
        'what if I ground this ball properly' means.

        MEASURED on the shared-return Zt below: the documented reading gives
        Z_ab = 31.79j Ohm and the mutuals-kept reading 63.65j -- a factor of
        two, i.e. 6 dB, silently.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        om = 2.0 * math.pi * f0
        shared = at.termination_impedance_shared_return(1j * om * 1e-9,
                                                        1j * om * 1e-9, 2)
        ctx = at.build_context(Y, d.freqs, ts, f0, zt=shared)
        swapped = at.sensitivity(ctx, 0, 1, [at.alt_ideal()], "Z", [0])[0]

        uncoupled = shared.copy()
        uncoupled[0, :] = 0.0
        uncoupled[:, 0] = 0.0
        expect = at.build_context(Y, d.freqs, ts, f0, zt=uncoupled)
        self.assertLess(abs(swapped.new_value - complex(expect.Zop[0, 1])),
                        1e-12 * abs(swapped.new_value))

        kept = shared.copy()
        kept[0, 0] = 0.0
        other = at.build_context(Y, d.freqs, ts, f0, zt=kept)
        self.assertGreater(
            abs(complex(other.Zop[0, 1]) - swapped.new_value),
            0.5 * abs(swapped.new_value))

    def test_a_wrongly_sized_zt_is_refused_with_the_element_list(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(Y, d.freqs, ts, mid_freq(d),
                             zt=np.zeros((3, 3), dtype=complex))
        self.assertIn("ground port 3", str(cm.exception))


class TestReturnPathBudget(unittest.TestCase):
    """Requirement 6: say where the return current actually goes."""

    def test_the_em_reference_carries_99_95_percent_on_diff_pair(self):
        """
        MEASURED on diff_pair_4port.s4p at 5.0 GHz with probes on ports 1 and 2
        and ONE declared ground on port 3: the declared ground carries 0.05% of
        the aggressor's return current and the EM model's own reference 99.95%.

        The consequence is the point of the requirement -- with the return path
        inside the S-parameters, this decomposition cannot confirm or refute a
        'forward path minus return path' explanation, and it must SAY that
        rather than let the reader assume the terms cover everything.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n")
        f0 = float(d.freqs[np.argmin(np.abs(d.freqs - 5e9))])
        ctx = at.build_context(Y, d.freqs, ts, f0)
        dec = at.decompose(ctx, 0, 1, "M")
        rb = dec.return_budget
        self.assertAlmostEqual(100.0 * rb.em_fraction, 99.95, delta=0.02)
        self.assertTrue(rb.dominant)
        self.assertIn("INSIDE the EM model", rb.note)
        self.assertIn("cannot separate it", rb.note)

    def test_declared_grounds_can_dominate_instead(self):
        """
        The other direction, so the note is not a constant.  decap_4port.s4p
        with both pi networks' far sides grounded: MEASURED 99.9% of the return
        goes through the declared grounds.
        """
        d, Y = load(DECAP)
        ts = parse_custom_termination_text(
            "1 signal s +\n3 signal c +\n2 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        rb = at.decompose(ctx, 0, 1, "Z").return_budget
        self.assertLess(rb.em_fraction, 0.01)
        self.assertFalse(rb.dominant)
        self.assertNotIn("INSIDE the EM model", rb.note)

    def test_a_balanced_aggressor_has_no_net_return_to_apportion(self):
        """
        1^T w_b == 0 for a differential drive, so both sides of the budget are
        roundoff.  Reporting '100% through the EM reference' of 0.1 fA would be
        arithmetically true and completely misleading.
        """
        d, Y = load(COUPLED_DIFF)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        rb = at.decompose(ctx, 0, 1, "M").return_budget
        self.assertFalse(rb.dominant)
        self.assertIn("BALANCED", rb.note)
        self.assertLess(rb.em_reference, 1e-9)

    def test_a_series_element_is_not_a_return_path(self):
        """
        `declared` counts the SHUNT elements only.  A short_to between two
        ports carries current sideways, not back to the reference, and folding
        it into the return budget makes the declared share look bigger than it
        is -- on exactly the number the reader is being asked to act on.
        MEASURED on `3 short_to 4` + `4 ground`: the ground carries 1.00596 A
        and the short 0.001 A, so declared and declared_all differ.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 short_to 4\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual([e.is_shunt for e in ctx.elements], [True, False])
        dec = at.decompose(ctx, 0, 1, "Z")
        rb = dec.return_budget
        gnd = next(t for t in dec.terms
                   if t.element is not None and t.element.kind == "ground")
        self.assertAlmostEqual(rb.declared, abs(gnd.current), delta=1e-12)
        self.assertGreater(rb.declared_all, rb.declared)

    def test_a_folded_ground_still_counts_as_a_declared_return(self):
        """
        A ground the baseline absorbed has no term, but it is still a declared
        return path.  Leaving its current out inflates the EM reference's share
        -- which is precisely the number the reader is being asked to act on.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual(len(ctx.folded), 2)
        rb = at.decompose(ctx, 0, 1, "M").return_budget
        self.assertAlmostEqual(rb.declared, 1.0, delta=1e-6)
        self.assertFalse(rb.dominant)


class TestShares(unittest.TestCase):
    """Requirement 7: a share is a projection, not a complex ratio."""

    def test_the_shares_of_a_complex_total_sum_to_one_and_zero(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n"
            "3 lumped_to_gnd R=2 L=1n\n4 lumped_to_gnd R=5 L=2n\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertFalse(dec.share_suppressed)
        self.assertAlmostEqual(sum(t.share_inline for t in dec.terms), 1.0,
                               places=9)
        self.assertAlmostEqual(sum(t.share_quad for t in dec.terms), 0.0,
                               places=9)

    def test_a_quadrature_term_is_reported_separately(self):
        """
        A term at right angles to the total contributes nothing to it and would
        inflate any magnitude-based cancellation measure.  Its INLINE share must
        be near zero while its magnitude is not.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n"
            "3 lumped_to_gnd R=2 L=1n\n4 lumped_to_gnd R=5 L=2n\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        dec = at.decompose(ctx, 0, 1, "Z")
        for t in dec.terms:
            self.assertTrue(math.isfinite(t.share_inline))
            self.assertTrue(math.isfinite(t.share_quad))
        # The identity that makes the split meaningful at all.
        for t in dec.terms:
            recon = (t.share_inline + 1j * t.share_quad) * dec.total_sum
            self.assertLess(abs(recon - t.contribution),
                            1e-9 * abs(dec.total_sum))

    def test_the_share_column_is_suppressed_when_the_total_is_zero(self):
        """
        decap_4port.s4p's two pi networks are uncoupled BY CONSTRUCTION, so
        Z_ab is exactly 0 -- not small.  A percentage of it is noise divided by
        noise, and the reason has to be named rather than printed as 'nan%'.
        """
        d, Y = load(DECAP)
        ts = parse_custom_termination_text(
            "1 signal s +\n3 signal c +\n2 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        dec = at.decompose(ctx, 0, 1, "Z")
        self.assertTrue(dec.share_suppressed)
        self.assertTrue(all(math.isnan(t.share_inline) for t in dec.terms))
        self.assertTrue(any("suppressed" in n for n in dec.notes))


class TestDecomposableQuantities(unittest.TestCase):
    """Requirement 8: refuse the reciprocals, by name."""

    def setUp(self):
        d, Y = load(COUPLED_DIFF)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n")
        self.ctx = at.build_context(Y, d.freqs, self.ts, mid_freq(d))

    def test_C_c_is_refused_by_name_and_the_reason_is_the_reciprocal(self):
        with self.assertRaises(at.AttribError) as cm:
            at.decompose(self.ctx, 0, 1, "C_c")
        msg = str(cm.exception)
        self.assertIn("C_c", msg)
        self.assertIn("RECIPROCAL", msg)
        # ... and it must point at what CAN be asked for instead.
        self.assertIn("ImZ", msg)

    def test_the_other_refusals(self):
        for name, needle in (("Q", "ratio of two decomposable"),
                             ("|Z|", "not R-linear"),
                             ("absZ", "not R-linear"),
                             ("k_dB", "logarithm")):
            with self.subTest(name):
                with self.assertRaises(at.AttribError) as cm:
                    at.decompose(self.ctx, 0, 1, name)
                self.assertIn(needle, str(cm.exception))

    def test_an_unknown_quantity_lists_both_sets(self):
        with self.assertRaises(at.AttribError) as cm:
            at.decompose(self.ctx, 0, 1, "wibble")
        self.assertIn("Decomposable", str(cm.exception))
        self.assertIn("C_c", str(cm.exception))

    def test_every_decomposable_quantity_actually_decomposes(self):
        for name in sorted(at.DECOMPOSABLE):
            with self.subTest(name):
                dec = at.decompose(self.ctx, 0, 1, name)
                total = sum((t.contribution for t in dec.terms),
                            start=complex(0.0))
                self.assertLess(abs(total - dec.total_sum),
                                1e-12 * max(1e-30, abs(dec.total_sum)))

    def test_M_and_k_agree_with_extract_coupling_at_freq(self):
        """
        The fixed scalar is read off compute_z_matrix's own Z, so M and k here
        must be the numbers the results pane already prints -- not a second,
        slightly different self inductance.
        """
        from pkg_rlc_core import extract_coupling_at_freq
        d, Y = load(COUPLED_DIFF)
        Zm, names, _w = compute_z_matrix(Y, d.freqs, self.ts)
        cres = extract_coupling_at_freq(d.freqs, Zm, names, self.ctx.freq_hz)
        pair = cres.pairs[0]
        self.assertAlmostEqual(
            at.decompose(self.ctx, 0, 1, "M").total_reference.real,
            pair.M_henry, delta=1e-18)
        self.assertAlmostEqual(
            at.decompose(self.ctx, 0, 1, "k").total_reference.real,
            pair.k, delta=1e-12)
        self.assertAlmostEqual(
            at.decompose(self.ctx, 0, 1, "M/L_a").total_reference.real,
            pair.M_over_La, delta=1e-12)


class TestSensitivityAgainstHonestRecompute(unittest.TestCase):
    """
    Requirement 9a, and THE most important test in this file.

    Every fast result is checked against a genuine rebuild of the
    TerminationSet handed to compute_z_matrix -- i.e. against what the user
    would get by editing the connection table and pressing Calculate.
    """

    #: (Alternative factory, the core termination that spells the same thing)
    def _shunt_cases(self, om):
        return [
            (at.alt_open(), None),
            (at.alt_ideal(), Ground()),
            (at.alt_resistor(50.0), LumpedToGnd(y_series_rlc(R=50.0))),
            (at.alt_inductor(1e-9, om), LumpedToGnd(y_series_rlc(L=1e-9))),
            (at.alt_series_rl(0.1, 1e-9, om),
             LumpedToGnd(y_series_rlc(R=0.1, L=1e-9))),
            (at.alt_capacitor(100e-12, om),
             LumpedToGnd(y_series_rlc(C=100e-12))),
        ]

    def test_every_shunt_alternative_matches_a_rebuilt_spec(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        om = 2.0 * math.pi * f0
        ctx = at.build_context(Y, d.freqs, ts, f0)
        cases = self._shunt_cases(om)
        alts = [a for a, _ in cases]
        core_of = {a.name: t for a, t in cases}
        worst = 0.0
        for r in at.sensitivity(ctx, 0, 1, alts, "Z"):
            elem = ctx.elements[r.elements[0]]
            port = elem.ports[0]
            t2 = clone(ts)
            term = core_of[r.alternative]
            if term is None:
                t2.per_port.pop(port, None)
            else:
                t2.per_port[port] = term
            ref = honest_zab(Y, d.freqs, t2, f0)
            rel = abs(r.new_value - ref) / abs(ref)
            worst = max(worst, rel)
            self.assertLess(rel, 1e-10,
                            f"{elem.describe()} -> {r.alternative}")
        # MEASURED: the worst disagreement over all 12 (element, alternative)
        # pairs on this spec is 1.33e-12 relative.  The assertion in the loop
        # is at 1e-10 so a genuine algebra change is caught while BLAS
        # reassociation is not; this one pins the level so a future change
        # cannot quietly lose two decades inside that margin.
        self.assertLess(worst, 1e-11)

    def test_a_series_element_matches_a_rebuilt_spec_too(self):
        """
        A short_to is a two-node stamp, not a shunt one, and its sign
        convention (current from the first port to the second) is where a
        transposition bug hides.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 short_to 4\n4 ground\n")
        f0 = mid_freq(d)
        ctx = at.build_context(Y, d.freqs, ts, f0)
        short_idx = next(e.index for e in ctx.elements if e.kind == "short")

        # open: drop the ShortPair entirely
        t_open = clone(ts)
        t_open.couplings = [c for c in t_open.couplings
                            if not isinstance(c, ShortPair)]
        # 20 ohm: replace it with a lumped_between
        t_r = clone(ts)
        t_r.couplings = [LumpedBetween(2, 3, y_series_rlc(R=20.0))]

        got_open = at.sensitivity(ctx, 0, 1, [at.alt_open()], "Z",
                                  [short_idx])[0].new_value
        got_r = at.sensitivity(ctx, 0, 1, [at.alt_resistor(20.0)], "Z",
                               [short_idx])[0].new_value
        for got, t2, label in ((got_open, t_open, "open"),
                               (got_r, t_r, "R=20")):
            ref = honest_zab(Y, d.freqs, t2, f0)
            self.assertLess(abs(got - ref) / abs(ref), 1e-10, label)

    def test_the_baseline_value_is_the_declared_configuration(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = mid_freq(d)
        ctx = at.build_context(Y, d.freqs, ts, f0)
        ref = honest_zab(Y, d.freqs, ts, f0)
        for r in at.sensitivity(ctx, 0, 1, [at.alt_open()], "Z"):
            self.assertLess(abs(r.baseline_value - ref) / abs(ref), 1e-10)
            self.assertAlmostEqual(abs(r.delta),
                                   abs(r.new_value - r.baseline_value),
                                   delta=1e-18)


class TestGroupAndCumulative(unittest.TestCase):
    """Requirements 9b-9e: the effects a per-element table cannot show."""

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = mid_freq(self.d)
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0)

    def test_a_group_change_matches_a_rebuilt_spec(self):
        gj = at.group_joint(self.ctx, 0, 1, "ground", at.alt_open(), "Z")
        self.assertEqual(gj.elements, (0, 1))
        t2 = clone(self.ts)
        t2.per_port.pop(2, None)
        t2.per_port.pop(3, None)
        ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
        self.assertLess(abs(gj.joint_value - ref) / abs(ref), 1e-10)

    def test_the_joint_effect_is_not_the_sum_of_the_individual_ones(self):
        """
        The reason requirement 9b exists.  MEASURED on this spec at 5.0005 GHz:
        opening BOTH grounds moves M by -758.7 pH, while the two one-at-a-time
        deltas are -506.2 pH each and sum to -1012.4 pH.  The non-additivity is
        +253.7 pH -- a THIRD of the joint effect, from only two elements.  With
        60 ground balls the individual deltas go to zero and only the joint
        number means anything at all.
        """
        gj = at.group_joint(self.ctx, 0, 1, "ground", at.alt_open(), "M")
        self.assertAlmostEqual(gj.joint_delta.real, -758.7e-12, delta=1e-12)
        self.assertAlmostEqual(gj.sum_individual.real, -1012.4e-12,
                               delta=1e-12)
        self.assertAlmostEqual(gj.non_additivity.real, 253.7e-12, delta=1e-12)
        self.assertGreater(abs(gj.non_additivity.real),
                           0.3 * abs(gj.joint_delta.real))
        self.assertAlmostEqual(
            gj.non_additivity.real,
            gj.joint_delta.real - gj.sum_individual.real, delta=1e-20)

    def test_the_cumulative_curve_is_greedy_and_exact_at_every_k(self):
        cc = at.cumulative_curve(self.ctx, 0, 1, at.alt_open(), "Z")
        self.assertEqual(cc.k[-1], self.ctx.n_elements)
        for k, val in zip(cc.k, cc.values):
            t2 = clone(self.ts)
            for e in cc.order[:k]:
                t2.per_port.pop(self.ctx.elements[e].ports[0], None)
            ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
            self.assertLess(abs(val - ref) / abs(ref), 1e-10, f"k={k}")

    def test_leave_one_out_starts_from_all_ideal(self):
        """
        Not the same starting point as `sensitivity`, and that is the point:
        from all-open the first ground you add changes everything, from
        all-ideal the number that moves is the one carrying something.

        The DECLARED spec here is two 1 nH lumped grounds, not two ideal ones,
        so "all ideal" and "as declared" are different networks -- otherwise
        the distinction this test exists for is untestable and a version that
        started from the declared config would pass.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n"
            "3 lumped_to_gnd R=0.5 L=1n\n4 lumped_to_gnd R=0.5 L=1n\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, self.f0)
        loo = at.leave_one_out(ctx, 0, 1, "Z")
        self.assertEqual(len(loo), ctx.n_elements)

        t_ideal = clone(ts)
        t_ideal.per_port[2] = Ground()
        t_ideal.per_port[3] = Ground()
        base_ref = honest_zab(self.Y, self.d.freqs, t_ideal, self.f0)
        self.assertLess(abs(loo[0].baseline_value - base_ref) / abs(base_ref),
                        1e-10)
        # ... and that really is a different starting point from the declared
        # one, or the assertion above proves nothing.
        declared = honest_zab(self.Y, self.d.freqs, ts, self.f0)
        self.assertGreater(abs(declared - base_ref) / abs(base_ref), 1e-6)

        for r in loo:
            t2 = clone(t_ideal)
            t2.per_port.pop(ctx.elements[r.elements[0]].ports[0], None)
            ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
            self.assertLess(abs(r.new_value - ref) / abs(ref), 1e-10, r.label)

    def test_an_open_alternative_removes_the_element_and_is_not_an_ideal_one(self):
        """
        `z is None` means the element leaves the network.  A version that read
        it as z = 0 would report the OPPOSITE termination under the label
        'open', which is the worst kind of wrong answer this API can give.
        """
        r_open = at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "Z", [0])[0]
        r_ideal = at.sensitivity(self.ctx, 0, 1, [at.alt_ideal()], "Z", [0])[0]
        self.assertGreater(abs(r_open.new_value - r_ideal.new_value),
                           1e-6 * abs(r_ideal.new_value))
        t2 = clone(self.ts)
        t2.per_port.pop(2, None)
        ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
        self.assertLess(abs(r_open.new_value - ref) / abs(ref), 1e-10)

    def test_a_group_label_comes_from_the_rows_when_they_are_supplied(self):
        """
        A TerminationSet carries no provenance, so without `sources` every
        ground lands in one group named after the kind.  With row_sources'
        output each row is its own group -- which is what makes "change this
        whole connection-table row at once" mean anything.
        """
        from pkg_rlc_core import ConnectionRow, MeasPortRow, row_sources
        mrows = [MeasPortRow("c1", "1", ""), MeasPortRow("c2", "2", "")]
        crows = [ConnectionRow(kind="ground", ports="3"),
                 ConnectionRow(kind="ground", ports="4")]
        src = row_sources(mrows, crows)
        ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0,
                               sources=src)
        self.assertEqual(sorted(ctx.groups), ["conn row 1", "conn row 2"])
        self.assertEqual(len(ctx.groups["conn row 1"]), 1)


class TestMobiusSweep(unittest.TestCase):
    """Requirement 10: closed form, both endpoints exact, no loop."""

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = mid_freq(self.d)
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0)
        self.om = 2.0 * math.pi * self.ctx.freq_hz

    def test_the_endpoints_match_direct_evaluation(self):
        """
        t = 0 must be the IDEAL termination and t -> inf the OPEN one, and both
        are read straight off the coefficients rather than from "a very small"
        and "a very large" number.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L")
        ideal = at.sensitivity(self.ctx, 0, 1, [at.alt_ideal()], "M", [0])[0]
        open_ = at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "M", [0])[0]
        self.assertAlmostEqual(sw.value_ideal.real, ideal.new_value.real,
                               delta=1e-22)
        self.assertAlmostEqual(sw.value_open.real, open_.new_value.real,
                               delta=1e-22)
        # ... and t -> inf really is the open limit, not just the label.
        self.assertAlmostEqual(sw.quantity_at(1e12).real, open_.new_value.real,
                               delta=1e-18)

    def test_the_sweep_agrees_with_a_rebuilt_spec_in_between(self):
        """The closed form is not just self-consistent -- it is the network."""
        for L in (1e-12, 1e-10, 1e-9, 1e-8):
            with self.subTest(L=L):
                sw = at.sweep_mobius(self.ctx, 0, 1, 0, "Z", param="L")
                t2 = clone(self.ts)
                t2.per_port[2] = LumpedToGnd(y_series_rlc(L=L))
                ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
                self.assertLess(abs(sw.value_at(L) - ref) / abs(ref), 1e-10)

    def test_a_single_element_sweep_is_a_mobius_map(self):
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L")
        self.assertEqual(len(sw.num), 2)
        self.assertEqual(len(sw.den), 2)
        for t in (0.0, 1e-12, 3.3e-10, 7e-9):
            direct = ((sw.alpha + sw.beta * t) / (sw.gamma + sw.delta * t))
            self.assertLess(abs(direct - sw.value_at(t)),
                            1e-12 * abs(sw.value_at(t)), f"t={t}")

    def test_the_interval_brackets_every_sampled_point(self):
        """
        The headline is the INTERVAL, so it has to actually contain the curve.
        A max found by sampling would miss a narrow resonance; this one is a
        root of the exact derivative polynomial.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L")
        lo, hi = sw.interval
        span = max(abs(lo), abs(hi), 1e-30)
        for t in np.concatenate([[0.0], np.logspace(-15, -4, 200)]):
            v = sw.quantity_at(float(t)).real
            self.assertGreaterEqual(v, lo - 1e-9 * span)
            self.assertLessEqual(v, hi + 1e-9 * span)

    def test_a_group_sweep_ties_every_member_to_the_same_value(self):
        sw = at.sweep_mobius(self.ctx, 0, 1, "ground", "Z", param="L")
        self.assertEqual(sw.elements, (0, 1))
        self.assertEqual(len(sw.num) - 1, 2)      # degree |S|
        for L in (1e-11, 1e-9):
            t2 = clone(self.ts)
            t2.per_port[2] = LumpedToGnd(y_series_rlc(L=L))
            t2.per_port[3] = LumpedToGnd(y_series_rlc(L=L))
            ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
            self.assertLess(abs(sw.value_at(L) - ref) / abs(ref), 1e-10)

    def test_leaving_the_ideal_open_bracket_is_detected(self):
        """
        A series L resonates with the structure's shunt C and the quantity can
        leave the [ideal, open] bracket entirely, which is exactly what a
        two-point best/worst-case estimate gets wrong.  Constructed here on
        coupled_4port_diff (5 fF per terminal) -- MEASURED: the interval is
        wider than the bracket and `leaves_bracket` is True.
        """
        d, Y = load(COUPLED_DIFF)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n")
        found = False
        for fi in (len(d.freqs) // 3, len(d.freqs) // 2, len(d.freqs) - 1):
            ctx = at.build_context(Y, d.freqs, ts, float(d.freqs[fi]))
            sw = at.sweep_mobius(ctx, 0, 1, 0, "M", param="L")
            lo, hi = sw.interval
            b_lo, b_hi = sw.bracket
            if sw.leaves_bracket:
                found = True
                self.assertTrue(lo < b_lo or hi > b_hi)
                self.assertTrue(any("LEAVES" in n for n in sw.notes))
                # and the extremum is at a finite, positive inductance
                self.assertTrue(math.isfinite(sw.arg_max)
                                or math.isfinite(sw.arg_min))
        self.assertTrue(found,
                        "no frequency produced a non-monotone sweep -- the "
                        "detector cannot be exercised by this fixture any more")

    def test_an_unbounded_sweep_names_the_near_pole_instead_of_reporting_it(self):
        """
        Over the WHOLE half-line a Mobius map generally passes close to a pole.
        MEASURED here: the extremum of M is 9 mH at L = 505 nH, where the
        ground inductance anti-resonates with the 1 fF port capacitance --
        8.9e6 times the [504 pH, 1.01 nH] bracket, at a value no ground ball
        has.  Reporting that as "M lies in [-9 mH, +9 mH]" and stopping is the
        failure this note exists to prevent.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L")
        self.assertGreater(max(abs(sw.interval[0]), abs(sw.interval[1])),
                           1e3 * max(abs(sw.bracket[0]), abs(sw.bracket[1])))
        note = " ".join(sw.notes)
        self.assertIn("near-POLE", note)
        self.assertIn("505 nH", note)
        self.assertIn("t_max", note)

    def test_a_bounded_sweep_is_the_usable_headline(self):
        """
        With a bound the interval becomes a number a budget can be written
        against.  MEASURED: over any ground inductance up to 10 nH, M on this
        spec lies in [1.0099 nH, 1.0202 nH] -- and the maximum is at the TOP of
        the range, not at either of the two endpoints a best/worst-case
        estimate would have used.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L", t_max=1e-8)
        lo, hi = sw.interval
        self.assertAlmostEqual(lo, 1.0099e-9, delta=1e-12)
        self.assertAlmostEqual(hi, 1.0202e-9, delta=1e-12)
        self.assertAlmostEqual(sw.arg_max, 1e-8, delta=1e-12)
        self.assertFalse(any("near-POLE" in n for n in sw.notes))
        # every point in the bounded range really is inside it
        for t in np.linspace(0.0, 1e-8, 101):
            v = sw.quantity_at(float(t)).real
            self.assertGreaterEqual(v, lo - 1e-9 * abs(hi))
            self.assertLessEqual(v, hi + 1e-9 * abs(hi))

    def test_the_leaves_bracket_tolerance_is_relative_not_absolute(self):
        """
        The values here are henries.  A tolerance floored at 1.0 -- which is
        what `1e-9 * max(1.0, ...)` gives -- is larger than every number in the
        comparison, so the detector answers 'no' to everything and the flag is
        dead.  MEASURED: the bounded sweep above overshoots its bracket by
        10.2 pH, which is 1.0e-2 relative and 1.0e-11 absolute.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L", t_max=1e-8)
        overshoot = sw.interval[1] - sw.bracket[1]
        self.assertGreater(overshoot, 0.0)
        self.assertLess(overshoot, 1e-9)          # absolute: tiny
        self.assertGreater(overshoot / sw.bracket[1], 1e-3)   # relative: real
        self.assertTrue(sw.leaves_bracket)

    def test_the_resistance_parameterisation_is_the_same_map(self):
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "Z", param="R")
        for R in (1.0, 50.0, 1e4):
            t2 = clone(self.ts)
            t2.per_port[2] = LumpedToGnd(y_series_rlc(R=R))
            ref = honest_zab(self.Y, self.d.freqs, t2, self.f0)
            self.assertLess(abs(sw.value_at(R) - ref) / abs(ref), 1e-10)


class TestTransferRatio(unittest.TestCase):
    """
    -Z_ab/Z_aa, the exact short-circuit current transfer, against M/L_a's
    first-order approximation to it (theory.md 8.8).
    """

    def test_the_exact_ratio_and_the_norton_approximation_part_company(self):
        """
        They agree where omega*L_a >> R_a and not below it.  MEASURED on
        coupled_2port_gndref.s2p (R1 = 0.6 Ohm, L1 = 2 nH, so the corner is at
        48 MHz): at 5.1 GHz the two are within 0.001 dB, at 100 MHz they differ
        by 0.02 dB, and the gap grows as the frequency falls.
        """
        d, Y = load(COUPLED_GNDREF)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        hi = at.build_context(Y, d.freqs, ts, float(d.freqs[-1]))
        lo = at.build_context(Y, d.freqs, ts, float(d.freqs[0]))
        t_hi = at.transfer_ratio(hi, 0, 1)
        t_lo = at.transfer_ratio(lo, 0, 1)
        self.assertLess(abs(t_hi.error_db), 0.01)
        self.assertGreater(abs(t_lo.error_db), abs(t_hi.error_db))
        self.assertIn("Norton", t_lo.note)

    def test_the_minus_sign_is_part_of_the_definition(self):
        """
        I_a/I_b = -Z_ab/Z_aa: the induced current opposes the flux that made
        it.  Dropping the sign leaves every magnitude and every dB figure
        untouched and inverts the phase of the injected spur, which is exactly
        the kind of error a magnitude-only test cannot see.
        """
        d, Y = load(COUPLED_GNDREF)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        tr = at.transfer_ratio(ctx, 0, 1)
        expect = -complex(ctx.Zref[0, 1]) / complex(ctx.Zref[0, 0])
        self.assertLess(abs(tr.ratio - expect), 1e-15 * abs(expect))

    def test_the_loaded_form_is_offered_and_differs(self):
        d, Y = load(COUPLED_GNDREF)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        tr = at.transfer_ratio(ctx, 0, 1, z_load=50.0)
        self.assertIsNotNone(tr.loaded)
        self.assertLess(abs(tr.loaded), abs(tr.ratio))
        self.assertIsNone(at.transfer_ratio(ctx, 0, 1).loaded)


class TestSignConventionAndReporting(unittest.TestCase):
    """Requirement 11: declared globally and in every export."""

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts,
                                    mid_freq(self.d))

    def test_the_convention_names_all_three_choices(self):
        text = at.SIGN_CONVENTION_TEXT
        for needle in ("V(+) - V(-)", "+1 A", "OUT of the structure",
                       "first port to the second", "RELATIVE signs"):
            self.assertIn(needle, text)

    def test_every_rendered_report_carries_it(self):
        lines = at.format_decomposition(
            at.decompose(self.ctx, 0, 1, "M"))
        self.assertTrue(any(at.SIGN_CONVENTION_TEXT in ln for ln in lines))

    def test_flipping_the_victim_probe_flips_every_term_together(self):
        """
        The claim the convention makes: absolute signs are a labelling choice,
        relative signs are physical.  Swapping the victim's + and - sides must
        negate every term and leave every ratio between terms untouched.
        """
        d, Y = load(COUPLED_DIFF)
        a = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n4 signal c2 -\n"
            "")
        b = parse_custom_termination_text(
            "1 signal c1 -\n2 signal c1 +\n3 signal c2 +\n4 signal c2 -\n"
            "")
        f0 = mid_freq(d)
        da = at.decompose(at.build_context(Y, d.freqs, a, f0), 0, 1, "Z")
        db = at.decompose(at.build_context(Y, d.freqs, b, f0), 0, 1, "Z")
        self.assertLess(abs(da.total_sum + db.total_sum),
                        1e-12 * abs(da.total_sum))

    def test_the_report_says_it_is_not_a_ranking_of_ports(self):
        dec = at.decompose(self.ctx, 0, 1, "M")
        joined = " ".join(dec.notes)
        self.assertIn("not a ranking of ports", joined)
        self.assertIn("how the spec is spelled", joined)


class TestElementsAndContext(unittest.TestCase):
    """The bookkeeping around the linear algebra."""

    def test_ports_are_zero_based_inside_and_one_based_on_screen(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 short_to 3\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        gnd = next(e for e in ctx.elements if e.kind == "ground")
        self.assertEqual(gnd.ports, (2,))
        self.assertEqual(gnd.describe(), "ground port 3")
        sh = next(e for e in ctx.elements if e.kind == "short")
        self.assertEqual(sh.ports, (3, 2))
        self.assertEqual(sh.describe(), "short 4-3")

    def test_the_element_order_is_the_documented_reading_order(self):
        """
        A TerminationSet is a dict plus a list, so "the order of the elements"
        is a promise this module makes, not one it inherits: every per-port
        declaration by ASCENDING PORT, then every coupling in DECLARATION
        order.  The report, the group indices, the Zt the caller has to build
        and every element index in every result are all keyed to it, so a
        reshuffle silently repoints a hand-built Zt at the wrong elements.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "4 ground\n1 signal c1 +\n2 signal c2 +\n"
            "3 lumped_to_gnd R=1\n4 short_to 3\n3 lumped_between 4 R=7\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertEqual([e.describe() for e in ctx.elements],
                         ["port 3 -> gnd", "ground port 4",
                          "short 4-3", "port 3-4"])
        self.assertEqual([e.index for e in ctx.elements], [0, 1, 2, 3])

    def test_an_unknown_measurement_port_is_refused_with_the_list(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        with self.assertRaises(at.AttribError) as cm:
            at.decompose(ctx, "nope", 0)
        self.assertIn("'c1'", str(cm.exception))
        self.assertIn("'c2'", str(cm.exception))

    def test_a_bad_Y_shape_is_refused(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text("1 signal c1 +\n2 signal c2 +\n")
        with self.assertRaises(at.AttribError):
            at.build_context(Y[0], d.freqs, ts, 1e9)
        with self.assertRaises(at.AttribError):
            at.build_context(Y, d.freqs[:3], ts, 1e9)

    def test_the_context_does_not_mutate_the_termination_set(self):
        """
        The GUI hands over the trace's live TerminationSet; a what-if that
        edited it would silently change the next Calculate.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        before = (dict(ts.per_port), list(ts.couplings))
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        at.decompose(ctx, 0, 1, "M")
        at.sensitivity(ctx, 0, 1, [at.alt_open(), at.alt_ideal()], "M")
        at.leave_one_out(ctx, 0, 1, "M")
        at.sweep_mobius(ctx, 0, 1, 0, "M")
        self.assertEqual(ts.per_port, before[0])
        self.assertEqual(ts.couplings, before[1])

    def test_a_port_number_outside_the_file_is_refused_by_core(self):
        """
        build_context calls compute_z_matrix FIRST precisely so that this
        message is core's and not a second near-identical one.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text("1 signal c1 +\n7 signal c2 +\n")
        with self.assertRaises(ValueError) as cm:
            at.build_context(Y, d.freqs, ts, mid_freq(d))
        self.assertIn("outside this file's 4 ports", str(cm.exception))

    def test_the_bare_EM_term_reports_complex_nan_not_a_real_nan(self):
        """
        The bare term carries no current and no transimpedance.  A REAL NaN
        assigned into a complex slot leaves imag == 0, and everything
        downstream that reads Im() -- an M column, an inductance -- then prints
        a perfectly plausible zero for a quantity that does not exist.  Same
        rule and same reason as _probe_impedance's complex(nan, nan) in core.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        bare = at.decompose(ctx, 0, 1, "Z").direct_term
        self.assertIsNotNone(bare)
        self.assertIsNone(bare.element)
        for value in (bare.current, bare.trans_z):
            self.assertTrue(math.isnan(value.real))
            self.assertTrue(math.isnan(value.imag))
        # ... and a real element's current is a real number, not a hole.
        live = [t for t in at.decompose(ctx, 0, 1, "Z").terms
                if t.element is not None]
        self.assertTrue(all(math.isfinite(abs(t.current)) for t in live))

    def test_a_self_impedance_decomposes_too(self):
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, mid_freq(d))
        dec = at.decompose(ctx, 0, 0, "Z")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        total = sum((t.contribution for t in dec.terms), start=complex(0.0))
        self.assertLess(abs(total - dec.total_sum), 1e-12 * abs(dec.total_sum))


class TestIllConditionedDegrades(unittest.TestCase):
    """
    Requirement 5's other half: degrade, never refuse outright, and never claim
    a total that the engine did not produce.
    """

    def test_a_catastrophic_residual_withholds_the_split_but_not_the_total(self):
        """
        MEASURED on diff_pair_4port.s4p at 1 MHz with the far ends grounded:
        the 1 fF port capacitance makes the all-open baseline cond(Ybase) =
        1.3e10, and the decomposition's own sum lands 25% away from the
        engine's.  The engine's number is the authoritative one and is still
        reported; the per-element split is withheld with the residual named.
        """
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(Y, d.freqs, ts, 1e6)
        self.assertGreater(ctx.cond_Ybase, 1e9)
        dec = at.decompose(ctx, 0, 1, "M")
        self.assertGreater(dec.residual_rel, at.RESIDUAL_CATASTROPHIC)
        self.assertFalse(dec.split_trustworthy)
        self.assertEqual(dec.terms, [])
        self.assertTrue(any("WITHHELD" in w for w in dec.warnings))
        # The total is compute_z_matrix's, and it is the right answer:
        # M = 1 nH exactly by construction.
        self.assertAlmostEqual(dec.total_reference.real, 1e-9, delta=1e-12)

    def test_the_floor_never_cries_wolf_on_a_healthy_spec(self):
        """
        104 (spec, frequency) combinations across every fixture in the repo,
        measured: not one residual exceeds its floor.  A fixed tolerance
        anywhere in that grid fails -- the residuals span 3e-16 to 1.5e-3.
        """
        specs = [
            (DIFF_PAIR, "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n"),
            (DIFF_PAIR, "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n"),
            (DIFF_PAIR, "1 signal c1 +\n2 signal c2 +\n3 ground\n"),
            (DIFF_PAIR, "1 signal c1 +\n2 signal c2 +\n"
                        "3 lumped_to_gnd R=0.1 L=1n\n"
                        "4 lumped_to_gnd R=0.1 L=1n\n"),
            (DIFF_PAIR, "1 signal c1 +\n2 signal c2 +\n3 short_to 4\n"
                        "4 ground\n"),
            (DECAP, "1 signal s +\n3 signal c +\n2 ground\n4 ground\n"),
            (COUPLED_DIFF, "1 signal c1 +\n3 signal c2 +\n2 ground\n"
                           "4 ground\n"),
            (COUPLED_DIFF, "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n"
                           "4 signal c2 -\n"),
            (COUPLED_FLOAT, "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n"
                            "4 signal c2 -\n"),
            (COUPLED_FLOAT, "1 signal c1 +\n3 signal c2 +\n2 ground\n"
                            "4 ground\n"),
            (COUPLED_GNDREF, "1 signal c1 +\n2 signal c2 +\n"),
        ]
        checked = 0
        for fixture, spec in specs:
            d, Y = load(fixture)
            ts = parse_custom_termination_text(spec)
            n = len(d.freqs)
            for fi in (1, 2, 5, 10, n // 4, n // 2, n - 1):
                f0 = float(d.freqs[fi])
                dec = at.decompose(
                    at.build_context(Y, d.freqs, ts, f0), 0, 1, "Z")
                checked += 1
                if not math.isfinite(dec.residual_rel):
                    continue
                self.assertLessEqual(
                    dec.residual_rel, dec.residual_floor,
                    f"{fixture} @ {f0:.4g} Hz: {dec.residual_rel:.3g} > "
                    f"{dec.residual_floor:.3g}")
        self.assertGreaterEqual(checked, 70)


# ---------------------------------------------------------------------------
# The integration pass: eight defects found by review, each with the
# measurement that found it.  Every guard here was mutation-checked and the
# mutation that kills it is named in the docstring.
# ---------------------------------------------------------------------------


class TestAWhatIfIsStillReconciled(unittest.TestCase):
    """
    A dense `zt` is requirement 2's whole point, and it used to cost the split.

    The reconciliation is a CROSS-ALGORITHM check: two routes to one network.
    It was taken between this module's answer for the WHAT-IF network and
    compute_z_matrix's answer for the DECLARED one, which is not that -- it is
    two networks, and the difference is one the caller asked for.  A shared
    return doubles M, so the residual read 1.01, sailed past
    RESIDUAL_CATASTROPHIC and emptied the table, with two warnings saying the
    algorithms disagreed about a number that was right.

    `ctx.Zop_declared` is the fix: the declared configuration evaluated through
    the same machinery, which is what the check was always about.
    """

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = float(self.d.freqs[np.argmin(np.abs(self.d.freqs - 5e9))])
        self.om = 2.0 * math.pi * self.f0

    def _ctx(self, zt=None):
        return at.build_context(self.Y, self.d.freqs, self.ts, self.f0, zt=zt)

    def test_a_shared_return_keeps_its_split_and_the_terms_add_up(self):
        """
        MEASURED at 5.0005 GHz, probes 1/2, grounds 3/4, shared 1 nH return:
        M = 2.0258 nH against 1.0099 nH as declared, and the residual is
        6.25e-13 -- the DECLARED spec's, unchanged from the zt=None run,
        because that is what is being checked.

        Mutation: `resid = abs(z_ab - ref) / denom` (i.e. compare the what-if
        against the declared reference) -> residual 1.01, terms == [].
        """
        zt = at.termination_impedance_shared_return(0j, 1j * self.om * 1e-9, 2)
        plain = at.decompose(self._ctx(), 0, 1, "M")
        dec = at.decompose(self._ctx(zt), 0, 1, "M")

        self.assertTrue(dec.split_trustworthy)
        self.assertEqual(len(dec.terms), 3)          # 2 grounds + bare EM
        self.assertAlmostEqual(dec.residual_rel, plain.residual_rel,
                               delta=1e-15)
        self.assertLess(dec.residual_rel, 1e-9)
        total = sum(t.contribution for t in dec.terms)
        self.assertAlmostEqual(total.real, dec.total_sum.real,
                               delta=1e-9 * abs(dec.total_sum.real))
        self.assertAlmostEqual(dec.total_sum.real * 1e12, 2025.8, delta=1.0)

    def test_the_reference_is_labelled_as_a_DIFFERENT_network_not_hidden(self):
        """
        The engine's number is still worth printing -- it is the declared
        spec's answer and the reader wants both -- but under its own heading.
        Dropping it, or leaving it under "total (compute_z_matrix)" beside a
        total that disagrees with it by 100%, are both ways of lying.

        Mutation: `reference_applicable=True` unconditionally -> the rendered
        report puts 1.01 nH and 2.03 nH under headings that claim to be the
        same measurement.
        """
        zt = at.termination_impedance_shared_return(0j, 1j * self.om * 1e-9, 2)
        dec = at.decompose(self._ctx(zt), 0, 1, "M")
        self.assertFalse(dec.reference_applicable)
        self.assertTrue(at.decompose(self._ctx(), 0, 1, "M")
                        .reference_applicable)
        text = "\n".join(at.format_decomposition(dec))
        self.assertIn("a DIFFERENT network", text)
        self.assertTrue(any("NEVER BEEN ASKED ABOUT THIS NETWORK" in n
                            for n in dec.notes), dec.notes)

    def test_a_scaled_quantitys_reference_is_not_a_MONGREL_of_two_networks(self):
        """
        `total_reference` is the DECLARED network's answer, so it takes the
        DECLARED network's divisor.  Scaling Zref[a, b] by the what-if's
        sqrt(L_a L_b) produces a number belonging to neither: MEASURED under a
        shared 1 nH return, `k`'s reference read 0.16716 -- the declared mutual
        over the modelled self inductances -- where the declared spec's own k
        is 0.200952.

        Same rule for `transfer_ratio`, from the other side: it takes BOTH
        Z values from one matrix, and for a what-if context that matrix is
        `ctx.Zop`, because answering with the declared ratio would describe a
        network the caller did not ask about.

        Mutation: `_map_value(spec, scale, Zref[a, b])` for total_reference, or
        `Zsrc = ctx.Zref` unconditionally in transfer_ratio.
        """
        zt = at.termination_impedance_shared_return(0j, 1j * self.om * 1e-9, 2)
        declared, whatif = self._ctx(), self._ctx(zt)
        for q in ("k", "M/L_a", "M"):
            with self.subTest(q=q):
                self.assertAlmostEqual(
                    at.decompose(whatif, 0, 1, q).total_reference.real,
                    at.decompose(declared, 0, 1, q).total_reference.real,
                    delta=1e-15)
        self.assertAlmostEqual(
            at.decompose(whatif, 0, 1, "k").total_reference.real,
            0.200952, delta=1e-6)
        tw = at.transfer_ratio(whatif, 0, 1)
        want = -complex(whatif.Zop[0, 1]) / complex(whatif.Zop[0, 0])
        self.assertAlmostEqual(abs(tw.ratio - want), 0.0, delta=1e-15)
        self.assertAlmostEqual(abs(tw.ratio), 0.335319, delta=1e-6)
        # ... and an ordinary context still reads the ENGINE's matrix
        t0 = at.transfer_ratio(declared, 0, 1)
        self.assertEqual(t0.ratio,
                         -complex(declared.Zref[0, 1])
                         / complex(declared.Zref[0, 0]))

    def test_a_diagonal_what_if_no_longer_warns_that_the_algorithms_disagree(self):
        """
        The quieter half of the same bug: `diag:L=1n` moves M by only 0.2%, so
        it stayed under RESIDUAL_CATASTROPHIC and kept its table -- while
        printing "this decomposition sums to ... while compute_z_matrix says
        ..." about a difference the caller had just asked for.  MEASURED: the
        old residual was 1.99e-3 against a 3.6e-9 floor.
        """
        zt = at.termination_impedance_diagonal([1j * self.om * 1e-9] * 2)
        dec = at.decompose(self._ctx(zt), 0, 1, "M")
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        self.assertFalse(any("Reconciliation:" in w for w in dec.warnings),
                         dec.warnings)

    def test_zt_may_not_carry_an_infinity(self):
        """
        Zt = D^-1 exists so no infinity enters the arithmetic (contract
        priority 4).  A caller-supplied matrix is the only route round it, and
        an inf there does not fail loudly: it becomes a NaN several solves
        later, under a message naming a solve.
        """
        zt = at.termination_impedance_diagonal([complex("inf"), 0j])
        with self.assertRaises(at.AttribError) as cm:
            self._ctx(zt)
        self.assertIn("OPEN element is spelled by leaving it out",
                      str(cm.exception))


class TestTheScaleBelongsToTheConfiguration(unittest.TestCase):
    """
    `M/L_a` and `k` divide by L_a, and L_a is a property of the NETWORK.

    Requirement 8's "fixed real scalar ... evaluated at ONE configuration"
    means fixed WITHIN one evaluation -- that is what keeps the terms additive
    -- not frozen at the declared spec while every sensitivity row changes the
    network underneath it.
    """

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = float(self.d.freqs[np.argmin(np.abs(self.d.freqs - 5e9))])
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0)
        self.om = self.ctx.omega

    def _truth(self, changed, alt, quantity):
        """L_a, L_b and M read off the (G, G) matrix of THAT configuration."""
        if alt.is_open:
            live = [e for e in range(self.ctx.n_elements)
                    if e not in set(changed)]
            Zt = self.ctx.Zt
        else:
            live = list(range(self.ctx.n_elements))
            Zt = at._zt_with(self.ctx, changed, complex(alt.z))
        Z = at._z_matrix(self.ctx, Zt, live)[0]
        La, Lb = Z[0, 0].imag / self.om, Z[1, 1].imag / self.om
        M = Z[0, 1].imag / self.om
        if quantity == "M/L_a":
            return M / La if La != 0 else float("nan")
        return M / math.sqrt(La * Lb) if (La > 0 and Lb > 0) else float("nan")

    def test_opening_a_ground_flips_the_sign_of_M_over_L_a_and_is_reported(self):
        """
        The measurement that found it.  On this spec, opening `ground port 3`
        takes L_a from +5.026 nH to -505.3 nH, so:

            reported, frozen at the declared L_a : +0.100227
            true value of that configuration    : -0.000996976

        Sign flipped and a hundred times too big -- on the FIRST row of the
        default sensitivity scan.

        Mutation: `_evaluate` taking a precomputed `scale` argument again.
        """
        got = at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "M/L_a", [0])[0]
        want = self._truth([0], at.alt_open(), "M/L_a")
        self.assertAlmostEqual(got.new_value.real, want, delta=1e-12)
        self.assertLess(got.new_value.real, 0.0)     # the sign is the point

    def test_k_is_NaN_where_the_configuration_makes_it_undefined(self):
        """
        Same configuration: L_a < 0, so extract_coupling_at_freq's own rule
        (k needs both self inductances > 0) makes k undefined -- and the frozen
        scale printed a plausible +0.100227 instead.  NaN is a missing
        measurement, not a small number.
        """
        got = at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "k", [0])[0]
        self.assertTrue(math.isnan(got.new_value.real), got.new_value)

    def test_every_family_uses_its_own_configurations_L_a(self):
        """sensitivity / group_joint / cumulative_curve / leave_one_out."""
        alt = at.alt_inductor(1e-9, self.om)
        for q in ("M/L_a", "k"):
            with self.subTest(q=q):
                s = at.sensitivity(self.ctx, 0, 1, [alt], q, [0])[0]
                self.assertAlmostEqual(s.new_value.real,
                                       self._truth([0], alt, q), delta=1e-12)
                g = at.group_joint(self.ctx, 0, 1, "ground", alt, q)
                self.assertAlmostEqual(g.joint_value.real,
                                       self._truth([0, 1], alt, q),
                                       delta=1e-12)
                c = at.cumulative_curve(self.ctx, 0, 1, alt, q)
                self.assertAlmostEqual(c.values[-1].real,
                                       self._truth([0, 1], alt, q),
                                       delta=1e-12)

    def test_a_decomposition_of_k_still_matches_the_results_pane(self):
        """
        The other side of the rule.  For the DECLARED spec the scale must stay
        compute_z_matrix's, so `k` here is byte-for-byte the `k` the results
        pane and the CSV already print -- a second, slightly different self
        inductance would be a silent disagreement between two screens.
        """
        Z = self.ctx.Zref
        La, Lb = Z[0, 0].imag / self.om, Z[1, 1].imag / self.om
        want = (Z[0, 1].imag / self.om) / math.sqrt(La * Lb)
        self.assertAlmostEqual(
            at.decompose(self.ctx, 0, 1, "k").total_reference.real,
            want, delta=1e-15)

    def test_the_sweep_refuses_the_two_quantities_it_cannot_carry(self):
        """
        A sweep is a CURVE, so there is no single configuration to take the
        scale from: L_a moves with t.  Refuse by name rather than deliver the
        frozen-scale bug as a curve.

        Mutation: drop `_SWEEP_REFUSED` -> `sweep_mobius(..., "k")` returns a
        confident interval of Z_ab(t) / the declared sqrt(L_a L_b).
        """
        for q in ("k", "M/L_a"):
            with self.subTest(q=q):
                with self.assertRaises(at.AttribError) as cm:
                    at.sweep_mobius(self.ctx, 0, 1, 0, q, param="L")
                self.assertIn(q, str(cm.exception))
                self.assertIn("property of the NETWORK"
                              if q == "M/L_a" else "undefined rather than "
                              "small", str(cm.exception))
        # ... and the ones it CAN carry still work
        for q in ("M", "ImZ", "ReZ", "Z"):
            at.sweep_mobius(self.ctx, 0, 1, 0, q, param="L")


class TestTheSweepIsEvaluatedFromItsPartialFractions(unittest.TestCase):
    """
    Requirement 10 at requirement 9's size: 60 ground balls in one group.

    The expanded polynomial cannot survive it.  With param="L" every eigenvalue
    of K/z_unit is of order 1e-9, so `den`'s constant term is a product of |S|
    of them -- and `value_ideal` was num[-1]/den[-1].
    """

    @classmethod
    def setUpClass(cls):
        cls.f0 = 5e9
        cls.om = 2.0 * math.pi * cls.f0

    def _synthetic(self, nball: int):
        """n ground balls + two coupled coils, built directly in Y."""
        n = nball + 2
        w = self.om
        Ym = np.zeros((1, n, n), dtype=complex)
        Lm = np.array([[1e-9, 0.4e-9], [0.4e-9, 1e-9]])
        Yc = np.linalg.inv(1j * w * Lm + np.eye(2) * 0.5)
        Ym[0, :2, :2] = Yc
        for i in range(2, n):
            Ym[0, i, i] += 1j * w * 50e-15 + 1.0 / (1j * w * 2e-9 + 0.1) + 0.001
            Ym[0, 0, i] -= 0.001
            Ym[0, i, 0] -= 0.001
            Ym[0, 0, 0] += 0.001
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n"
            + "\n".join(f"{p} ground" for p in range(3, n + 1)))
        return at.build_context(Ym, np.array([self.f0]), ts, self.f0)

    def test_sixty_balls_in_one_group_still_have_both_endpoints(self):
        """
        MEASURED with the expanded form: den[-1] = 5.98e-273 at 30 balls,
        3.70e-309 at 34, exactly 0 at 36 -- at which point `value_ideal` read
        +inf, then NaN at 38, then NaN for the whole curve at 60, with `method`
        still saying "closed-form" and `notes` empty.

        Mutation: read the endpoints off num[-1]/den[-1] again.
        """
        for nball in (30, 36, 38, 60):
            with self.subTest(nball=nball):
                ctx = self._synthetic(nball)
                sw = at.sweep_mobius(ctx, 0, 1, list(range(ctx.n_elements)),
                                     "M", "L")
                self.assertTrue(math.isfinite(sw.value_ideal.real),
                                f"{nball}: {sw.value_ideal}")
                self.assertTrue(math.isfinite(sw.value_open.real))
                self.assertTrue(all(math.isfinite(v) for v in sw.interval))
                # and the curve is the network, at a value in between
                direct = at._zab(
                    ctx, 0, 1,
                    at._zt_with(ctx, range(ctx.n_elements), 1j * self.om * 1e-9),
                    range(ctx.n_elements)).imag / self.om
                self.assertAlmostEqual(sw.quantity_at(1e-9).real, direct,
                                       delta=1e-9 * abs(direct))

    def test_the_expanded_coefficients_are_EMPTIED_not_left_as_garbage(self):
        """
        `num` / `den` are diagnostic only, and a caller reading them has to be
        able to tell "gone" from "small": coefficients whose constant term is 0
        because 38 factors of 1e-9 met each other describe a DIFFERENT rational
        function, and nothing about them looks wrong.

        Mutation: return the underflowed arrays -> len(sw.den) == 39 with
        den[-1] == 0 and no note.
        """
        ctx = self._synthetic(38)
        sw = at.sweep_mobius(ctx, 0, 1, list(range(ctx.n_elements)), "M", "L")
        self.assertEqual(sw.num, ())
        self.assertEqual(sw.den, ())
        self.assertTrue(any("NOT available" in n for n in sw.notes), sw.notes)
        # ... while a small group keeps them, so the Mobius spelling survives
        small = at.sweep_mobius(self._synthetic(2), 0, 1, 0, "M", "L")
        self.assertEqual(len(small.num), 2)
        self.assertNotEqual(small.den[-1], 0)


class TestTheGroupSweepFindsItsClusteredExtrema(unittest.TestCase):
    """
    The interval is requirement 10's headline scalar, and on |S| >= 2 it was
    wrong by 2.4e3x with the wrong sign on the other end.
    """

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = float(self.d.freqs[np.argmin(np.abs(self.d.freqs - 5e9))])
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0)
        self.om = self.ctx.omega

    def _direct(self, t: float) -> float:
        Zt = at._zt_with(self.ctx, [0, 1], t * 1j * self.om)
        return at._zab(self.ctx, 0, 1, Zt,
                       range(self.ctx.n_elements)).imag / self.om

    def test_two_poles_a_tenth_of_a_percent_apart_are_both_found(self):
        """
        MEASURED on diff_pair_4port.s4p at 5 GHz, sweeping BOTH grounds as one
        group over L.  The poles sit at t = 5.05000e-7 and 5.05503e-7 -- 0.1%
        apart, both on the positive real axis -- and `np.roots` on the expanded
        degree-4 critical polynomial found neither:

            reported : (+7.46e-21, +2.138e-3) H
            true     : (-5.187,    +5.187   ) H

        i.e. the maximum 2.4e3 times too small and the minimum the WRONG SIGN.
        The single-element sweep on the same file was correct throughout, which
        is why nothing caught it -- the defect needs |S| >= 2, which is exactly
        requirement 9b's "change a whole connection-table row".

        Mutation: delete the pole seeds from `_rational_extrema`, or the Newton
        polish, and the interval collapses back towards (0, 2e-3).
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, [0, 1], "M", "L")
        lo, hi = sw.interval
        self.assertLess(lo, -1.0)          # was +7.5e-21
        self.assertGreater(hi, 1.0)        # was +2.1e-3
        # both extremes are ACHIEVED -- a re-solve at the reported argument
        # reproduces the reported value.  This is the property that makes the
        # candidate set safe to extend: every candidate is a real point.
        self.assertAlmostEqual(self._direct(sw.arg_max), hi,
                               delta=1e-6 * abs(hi))
        self.assertAlmostEqual(self._direct(sw.arg_min), lo,
                               delta=1e-6 * abs(lo))
        # and they really are at the poles
        poles = sorted((-x).real for x in sw.lam)
        self.assertLess(abs(sw.arg_max - poles[0]) / poles[0], 1e-3)

    def test_the_single_element_sweep_did_not_move(self):
        """
        It was already right, and the new candidate set must not disturb it.
        MEASURED, bounded at 10 nH: M in [1.0099 nH, 1.0202 nH] with the
        maximum at the TOP of the range -- unchanged.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L", t_max=1e-8)
        self.assertAlmostEqual(sw.interval[0], 1.0099e-9, delta=1e-12)
        self.assertAlmostEqual(sw.interval[1], 1.0202e-9, delta=1e-12)
        self.assertAlmostEqual(sw.arg_max, 1e-8, delta=1e-12)

    def test_the_polish_reaches_an_extremum_that_no_seed_sits_on(self):
        """
        The pole seeds are `Re(p) + c*|Im p|` for a small fixed set of c, which
        finds a sharp Lorentzian peak because that IS where a sharp peak lives.
        A BROAD, heavily damped extremum is somewhere else entirely: with two
        well-separated poles and opposite-sign residues the derivative's zero
        sits between them, nowhere near either.

        Pure -- no fixture, no network -- because the property is about the
        root finder, and the numbers are MEASURED against a 2-million-point
        grid of the same rational function:

            dense grid              min = -0.059260702  at t = 32.444
            seeds only, no polish   min = -0.058970588  at t = 30      (0.49% narrow)
            2 Newton steps          min = -0.059260702  at t = 32.444

        Half a percent is small; missing the mechanism is not, and the failure
        is silent -- a too-narrow interval reads exactly like a true one.

        Mutation: `_POLISH_STEPS = 0`.
        """
        spec = at.DECOMPOSABLE["ImZ"]
        lam = [complex(-1.0, 3.0), complex(-30.0, 40.0)]
        res = [1.0 + 0j, -2.5 + 0j]
        lo, hi, t_lo, _t_hi, _n = at._rational_extrema(
            0j, lam, res, spec, 1.0, None)
        self.assertAlmostEqual(lo, -0.05926070204, places=10)
        self.assertAlmostEqual(t_lo, 32.444, delta=0.01)
        # ... and it really is the minimum of the curve, not just a number
        for t in np.logspace(-3, 3, 4001):
            self.assertGreaterEqual(
                at._pf_value(0j, lam, res, float(t)).imag, lo - 1e-12)

    def test_a_complex_quantitys_bracket_is_a_magnitude_like_its_interval(self):
        """
        `interval` is of |Z| for a complex quantity (there being no order on
        C), and `bracket` was of the REAL PART -- so `leaves_bracket` and the
        near-pole ratio compared two different quantities.

        MEASURED with quantity='Z', t_max=20 nH: bracket read
        (-2.49 nOhm, 376 pOhm) against an interval of (31.7 Ohm, 32.4 Ohm), and
        the report announced an extremum "1.3e+10 times the bracket" -- a
        near-pole that does not exist.

        Mutation: `b_lo, b_hi = min/max(_real(...))` for part == 'complex'.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, [0, 1], "Z", "L", t_max=20e-9)
        b_lo, b_hi = sw.bracket
        self.assertAlmostEqual(b_lo, min(abs(sw.value_ideal),
                                         abs(sw.value_open)), delta=1e-12)
        self.assertAlmostEqual(b_hi, max(abs(sw.value_ideal),
                                         abs(sw.value_open)), delta=1e-12)
        self.assertGreater(b_hi, 1.0)      # ohms, not nano-ohms
        self.assertFalse(any("near-POLE" in n for n in sw.notes), sw.notes)


class TestAWhatIfSaysHowItModelledTheGroup(unittest.TestCase):
    """
    Requirement 2 on the what-if side.  `group_joint(..., alt_inductor(1nH))`
    is the "what if I put 1 nH on the whole ground row" question requirement 9b
    exists for, and it silently modelled the leads as INDEPENDENT -- precisely
    the model requirement 2 exists to warn against, with no note anywhere.
    """

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.f0 = float(self.d.freqs[np.argmin(np.abs(self.d.freqs - 5e9))])
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts, self.f0)
        self.om = self.ctx.omega

    def test_an_independent_multi_element_what_if_says_so(self):
        """
        MEASURED: independent 1 nH per lead gives M = 1.012 nH and one shared
        1 nH gives 2.032 nH -- 6.06 dB apart, from the same question asked two
        ways, with the API able to express only one of them.

        Mutation: drop the note -> the default answer is 6 dB out and silent.
        """
        alt = at.alt_inductor(1e-9, self.om)
        g = at.group_joint(self.ctx, 0, 1, "ground", alt, "M")
        self.assertTrue(any("INDEPENDENT" in n for n in g.notes), g.notes)
        c = at.cumulative_curve(self.ctx, 0, 1, alt, "M")
        self.assertTrue(any("INDEPENDENT" in n for n in c.notes), c.notes)
        # a SINGLE element has no return to share, so no note
        s = at.group_joint(self.ctx, 0, 1, [0], alt, "M")
        self.assertEqual(s.notes, ())

    def test_z_ret_reproduces_the_dense_builder_exactly(self):
        """
        The note has to be actionable, so the shared form is expressible: a
        group tied through z_ret is the same network
        termination_impedance_shared_return builds, and MEASURED it agrees
        BIT-IDENTICALLY (rel 0.0), 6.06 dB from the independent answer.

        Mutation: apply z_ret to the diagonal too, or to unchanged elements.
        """
        alt = at.alt_inductor(1e-9, self.om)
        z = 1j * self.om * 1e-9
        g = at.group_joint(self.ctx, 0, 1, "ground", alt, "M", z_ret=z)
        dense = at.build_context(
            self.Y, self.d.freqs, self.ts, self.f0,
            zt=at.termination_impedance_shared_return(z, z, 2))
        want = float(np.imag(dense.Zop[0, 1])) / self.om
        self.assertEqual(g.joint_value.real, want)
        self.assertEqual(g.notes, ())      # it was asked properly
        indep = at.group_joint(self.ctx, 0, 1, "ground", alt, "M")
        self.assertAlmostEqual(
            20 * math.log10(abs(g.joint_value.real / indep.joint_value.real)),
            6.06, delta=0.05)


class TestAnUncheckedSplitIsNotACheckedOne(unittest.TestCase):
    """
    `split_trustworthy` was True when the residual was NaN -- i.e. when nothing
    had been checked at all.
    """

    def test_a_NaN_reference_withholds_the_split(self):
        """
        MEASURED on coupled_4port_float.s4p with only one of the two coils
        referenced: compute_z_matrix says NaN ("'c2' has no return path") and
        this module folds the single ground in and reports 400.000 pH --
        exactly half the fixture's real 800 pH, which is the most convincing
        kind of wrong number.  A caller gating on `split_trustworthy` got a
        green light for it.

        The TOTAL is still reported: it is what the user has.  What is withheld
        is the apportionment, and the warning now says why.

        Mutation: `trustworthy = True` before the isfinite test.
        """
        d, Y = load(COUPLED_FLOAT)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c2 +\n2 ground\n")
        dec = at.decompose(
            at.build_context(Y, d.freqs, ts, mid_freq(d)), 0, 1, "M")
        self.assertTrue(math.isnan(dec.residual_rel))
        self.assertFalse(dec.split_trustworthy)
        self.assertEqual(dec.terms, [])
        self.assertAlmostEqual(dec.total_sum.real, 400e-12, delta=1e-15)
        self.assertTrue(any("WITHHELD" in w for w in dec.warnings),
                        dec.warnings)

    def test_a_healthy_spec_is_still_trustworthy(self):
        """The gate must not have become 'always withhold'."""
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        dec = at.decompose(
            at.build_context(Y, d.freqs, ts, mid_freq(d)), 0, 1, "M")
        self.assertTrue(dec.split_trustworthy)
        self.assertEqual(len(dec.terms), 3)


class TestNonFiniteDataIsRefusedByName(unittest.TestCase):
    """
    A NaN at the analysed frequency escaped as numpy's bare
    LinAlgError("SVD did not converge") from build_context -- no verdict, no
    frequency, no file, in a repo whose TouchstoneParseError contract exists to
    answer exactly that question.
    """

    def test_the_verdict_names_the_frequency_and_the_port(self):
        """Mutation: delete the isfinite check -> LinAlgError, unnamed."""
        d, Y = load(DIFF_PAIR)
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        f0 = float(d.freqs[np.argmin(np.abs(d.freqs - 5e9))])
        Yb = np.array(Y, copy=True)
        Yb[int(np.argmin(np.abs(d.freqs - f0))), 0, 0] = complex(
            float("nan"), float("nan"))
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(Yb, d.freqs, ts, f0)
        msg = str(cm.exception)
        self.assertIn("non-finite", msg)
        self.assertIn("5 GHz", msg)
        self.assertIn("port row(s) 1", msg)
        # a NEIGHBOURING frequency is untouched
        at.build_context(Yb, d.freqs, ts, float(d.freqs[0]))


class TestCcIsReportedAsATotal(unittest.TestCase):
    """
    NON_DECOMPOSABLE['C_c'] promises "C_c is still reported as a TOTAL".  It
    was not: no field on Decomposition, nothing in format_decomposition, and
    `grep C_c` found only the docstring and the promise itself.
    """

    def setUp(self):
        self.d, self.Y = load(DIFF_PAIR)
        self.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        self.ctx = at.build_context(self.Y, self.d.freqs, self.ts,
                                    mid_freq(self.d))

    def test_the_total_is_there_and_is_minus_one_over_omega_Im_Z(self):
        """Mutation: drop C_c_total -> the refusal points at nothing."""
        dec = at.decompose(self.ctx, 0, 1, "M")
        want = -1.0 / (self.ctx.omega * dec.Z_ab.imag)
        self.assertAlmostEqual(dec.C_c_total.real, want,
                               delta=1e-12 * abs(want))
        self.assertIn("C_c (total only)", "\n".join(
            at.format_decomposition(dec)))

    def test_it_is_still_refused_PER_TERM(self):
        """The reason it is a total is that its terms would not add."""
        with self.assertRaises(at.AttribError) as cm:
            at.decompose(self.ctx, 0, 1, "C_c")
        self.assertIn("RECIPROCAL", str(cm.exception))
        self.assertFalse(hasattr(at.Term, "C_c"))


if __name__ == "__main__":
    unittest.main()
