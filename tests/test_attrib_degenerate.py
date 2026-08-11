"""
Degenerate and adversarial tests for `pkg_rlc_attrib`.

`tests/test_attrib_core.py` asks whether the engine is right on specs that
work.  This file asks what it does when the spec, the network or the data is
BROKEN -- which is the only interesting question, because every failure mode
below produces a plausible number rather than an exception:

  * a structure with no DC reference (cond(Y) = 2.5e16) inverts to garbage;
  * a redundant spec makes H exactly singular, and calling that "unattributable
    physics" sends the user hunting for a coupling mechanism that is not there;
  * an ill-conditioned baseline makes the decomposition's own sum disagree with
    the engine by 100%, with both numbers finite and neither flagged;
  * an independent-per-ball ground model reads 9.6 dB low against the shared
    return real package balls have;
  * with eight ground balls every one-at-a-time and every pairwise measurement
    reads ~0 while the collective effect is 600 times larger and the OTHER SIGN;
  * a ground inductance resonating with a package capacitance puts M outside
    the [ideal ground, open] bracket that a two-point estimate assumes bounds it;
  * one NaN in one S entry poisons that whole frequency.

Two things every test here does that the acceptance suite does not.  It
CONSTRUCTS the degeneracy rather than hoping a fixture contains one -- the
repo's .sNp fixtures are 2- and 4-port and cannot express a package with eight
ground balls or a resonant return path -- and it checks the answer against an
HONEST rebuild through `compute_z_matrix`, so both sides of every comparison
come from shipped code.

Every guard here was mutation-checked; the mutation that defeats it is named in
the test's own docstring.  The synthetic network builder is the one measured in
the session that produced this module's contract (`check_shared_return.py`),
parametrised: the 9.60 dB shared-return figure and the 0.000 dB
two-spellings-of-one-network figure below are that script's numbers,
reproduced here through the shipped code.
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
    LumpedToGnd,
    TerminationSet,
    compute_z_matrix,
    parse_custom_termination_text,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
)

FIXTURES = _HERE / "fixtures"

DIFF_PAIR = "diff_pair_4port.s4p"
COUPLED_FLOAT = "coupled_4port_float.s4p"   # cond(Y) = 2.48e16 by construction


def load(name: str):
    d = parse_touchstone(FIXTURES / name)
    return d, s_to_y(d.s, d.z0)


def mid_freq(d) -> float:
    return float(d.freqs[len(d.freqs) // 2])


def clone(ts: TerminationSet) -> TerminationSet:
    return TerminationSet(per_port=dict(ts.per_port),
                          couplings=list(ts.couplings))


def honest_zab(Y, freqs, ts: TerminationSet, freq_hz: float,
               a: int = 0, b: int = 1) -> complex:
    """
    The answer you get by editing the spec and pressing Calculate: a fresh
    TerminationSet through compute_z_matrix over the WHOLE sweep, reusing
    nothing.  Every "the fast path really is the same network" claim below is
    measured against this and not against pkg_rlc_attrib's own numbers.
    """
    Z, _names, _w = compute_z_matrix(Y, freqs, ts)
    idx = int(np.argmin(np.abs(np.asarray(freqs) - freq_hz)))
    return complex(Z[idx][a, b])


# ---------------------------------------------------------------------------
# The synthetic package-ish network
# ---------------------------------------------------------------------------
#
# Nothing in tests/fixtures can express what most of this file is about: a
# victim and an aggressor sharing a ground return through several balls, with
# the ball count, the per-ball inductance and the ball-node capacitance as
# knobs.  So the network is built as a nodal Y and handed to the SHIPPED
# compute_z_matrix -- the code under test is still only pkg_rlc_attrib.
#
#   node A  : victim coil top      -> port 1
#   node B  : aggressor coil top   -> port 2
#   node Gi : the shared internal ground node (eliminated, not a port)
#   node Pk : ball k               -> ports 3 .. 2+n_balls
#
# The two coils run from their own node down to Gi and are mutually coupled by
# M0 = 5 pH; each ball hangs off Gi through l_via (+ r_via); every node carries
# c_sub to the EM reference, which is what makes Y non-singular the way a real
# EM export is.  L1 / L2 / M0 are the values the contract's own measurements
# used.

L1_COIL, L2_COIL, M0_COIL = 3.16e-9, 1.44e-9, 5.0e-12
C_SUB = 30e-15
F_TEST = np.array([5.205e9])          # one frequency: these are all @-a-point
F0 = float(F_TEST[0])
OMEGA = 2.0 * math.pi * F0


def ball_network(freqs, n_balls=4, l_via=1.0e-9, r_via=0.0, c_sub=C_SUB,
                 c_ball=None, tie_first_two=None, r_coil=0.0) -> np.ndarray:
    """
    (nfreqs, 2 + n_balls, 2 + n_balls) port Y of the network above.

    c_ball        extra shunt C from each BALL node to the EM reference; this
                  is what a ground inductance resonates against.
    tie_first_two a resistance tying balls 1 and 2 together INSIDE the network
                  -- physics, not spec, which is the false-alarm case for the
                  structural rank check.
    """
    names = ["A", "B", "Gi"] + [f"P{i}" for i in range(n_balls)]
    ix = {nm: i for i, nm in enumerate(names)}
    nn = len(names)
    Y = np.zeros((len(freqs), nn, nn), dtype=complex)
    for k, f in enumerate(freqs):
        jw = 2j * np.pi * f
        # the coupled pair, as a 2x2 branch impedance inverted to a branch
        # admittance and stamped through its incidence
        Zc = np.array([[r_coil + jw * L1_COIL, jw * M0_COIL],
                       [jw * M0_COIL, r_coil + jw * L2_COIL]], dtype=complex)
        C = np.zeros((2, nn), dtype=complex)
        C[0, ix["A"]] = 1.0
        C[0, ix["Gi"]] = -1.0
        C[1, ix["B"]] = 1.0
        C[1, ix["Gi"]] = -1.0
        Y[k] += C.T @ np.linalg.inv(Zc) @ C
        for i in range(n_balls):
            y = 1.0 / (r_via + jw * l_via)
            a, b = ix["Gi"], ix[f"P{i}"]
            Y[k, a, a] += y
            Y[k, b, b] += y
            Y[k, a, b] -= y
            Y[k, b, a] -= y
        for nd in range(nn):
            Y[k, nd, nd] += jw * c_sub
        if c_ball:
            for i in range(n_balls):
                Y[k, ix[f"P{i}"], ix[f"P{i}"]] += jw * c_ball
        if tie_first_two is not None:
            y = 1.0 / tie_first_two
            a, b = ix["P0"], ix["P1"]
            Y[k, a, a] += y
            Y[k, b, b] += y
            Y[k, a, b] -= y
            Y[k, b, a] -= y
    keep = [ix["A"], ix["B"]] + [ix[f"P{i}"] for i in range(n_balls)]
    elim = [ix["Gi"]]
    out = np.empty((len(freqs), len(keep), len(keep)), dtype=complex)
    for k in range(len(freqs)):
        Ykk = Y[k][np.ix_(keep, keep)]
        Yke = Y[k][np.ix_(keep, elim)]
        Yek = Y[k][np.ix_(elim, keep)]
        Yee = Y[k][np.ix_(elim, elim)]
        out[k] = Ykk - Yke @ np.linalg.solve(Yee, Yek)
    return out


def ball_spec(n_balls: int) -> str:
    """Victim on port 1, aggressor on port 2, every ball an ideal ground."""
    return f"1 signal V\n2 signal A2\n3:1:{2 + n_balls} ground\n"


def M_of(Y, freqs, spec_or_ts, freq_hz=F0) -> float:
    """M = Im(Z_ab)/omega straight out of compute_z_matrix. The honest route."""
    ts = (parse_custom_termination_text(spec_or_ts)
          if isinstance(spec_or_ts, str) else spec_or_ts)
    z = honest_zab(Y, freqs, ts, freq_hz)
    return float(z.imag) / (2.0 * math.pi * freq_hz)


# ---------------------------------------------------------------------------


class TestSingularBaselineRecovers(unittest.TestCase):
    """
    Requirement 3.  `coupled_4port_float.s4p` is the repo's flagship Mode 6
    example -- theory.md and the README both use it -- and it has no DC
    reference at all: MEASURED cond(Y) = 2.48e16.  inv(Ybase) does not exist,
    so an implementation that forms one is wrong on the first file a user is
    told to try, and wrong with a finite plausible number rather than an error.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load(COUPLED_FLOAT)
        cls.f0 = mid_freq(cls.d)
        # far ends grounded: the all-open baseline is then rank-deficient by
        # two and those two grounds are exactly the out-of-range elements.
        cls.spec = "1 signal c1 +\n3 signal c2 +\n2 ground\n4 ground\n"
        cls.ts = parse_custom_termination_text(cls.spec)
        cls.ctx = at.build_context(cls.Y, cls.d.freqs, cls.ts, cls.f0)
        cls.dec = at.decompose(cls.ctx, 0, 1, "M")

    def test_the_precondition_that_makes_this_file_the_hard_one(self):
        """
        Asserted, not assumed.  If the fixture is ever regenerated with a
        shunt C to ground, every test in this class silently stops testing
        the recovery path and starts testing the ordinary one.
        """
        idx = int(np.argmin(np.abs(self.d.freqs - self.f0)))
        self.assertGreater(float(np.linalg.cond(self.Y[idx])), 1e15)
        self.assertTrue(self.ctx.baseline_singular)

    def test_the_fold_names_the_ports_it_absorbed(self):
        """
        Requirement 3's reporting half.  A user whose two grounds have no terms
        must be told WHY, by port number: "your grounds vanished" and "ports
        2 and 4 are in the baseline because the structure has no reference
        without them" are the same fact and only one of them is actionable.

        Mutation: drop the `notes.append(...)` in the fold block -- the numbers
        do not move at all and this is the only test that goes red.
        """
        self.assertEqual(len(self.ctx.folded), 2)
        self.assertEqual({e.describe() for e in self.ctx.folded},
                         {"ground port 2", "ground port 4"})
        note = " ".join(self.ctx.notes)
        self.assertIn("2,4", note)          # collapse_ports, never "2, 4"
        self.assertIn("no reference without them", note)

    def test_the_RENDERED_report_carries_the_naming_too(self):
        """
        `ctx.notes` is not what a user reads.  Two rendered channels are, and
        they are assembled in different places: the note list that
        format_decomposition prints, and `reference_note` -- the line that says
        what the baseline IS, printed directly under the table beside the
        return-path budget.  A reader who is told "your two grounds have no
        terms" needs the explanation on the line that describes the baseline,
        not only in a note further down.

        Mutation: delete the `if ctx.folded:` branch in decompose() that
        appends the fold note to `ref_note` -- `ctx.notes` still carries it, the
        rendered text still contains it, and only the `reference_note`
        assertion below goes red.
        """
        text = "\n".join(at.format_decomposition(self.dec))
        self.assertIn("no reference without them", text)
        self.assertIn("2,4", text)
        self.assertIn("no reference without them", self.dec.reference_note)
        self.assertIn("2,4", self.dec.reference_note)

    def test_the_total_matches_an_INDEPENDENT_engine_run(self):
        """
        The residual check inside decompose() compares against `ctx.Zref`,
        which build_context computed itself from a ONE-FREQUENCY slice.  This
        one runs compute_z_matrix over the whole 401-point sweep from a freshly
        parsed TerminationSet and compares to that, so a mistake shared by both
        halves of the internal check cannot hide.

        MEASURED: M = 800.000000 pH both ways -- the fixture's construction
        value -- at 8.4e-16 relative.
        """
        ref = honest_zab(self.Y, self.d.freqs, self.ts, self.f0)
        got = complex(self.ctx.Zop[0, 1])
        self.assertLess(abs(got - ref) / abs(ref), 1e-12)
        self.assertAlmostEqual(self.dec.total_sum.real, 800e-12, delta=1e-15)
        self.assertLessEqual(self.dec.residual_rel, self.dec.residual_floor)

    def test_a_folded_element_never_reappears_as_a_term(self):
        """
        A folded ground is INSIDE the baseline: its effect is in the bare EM
        term and it has no term of its own.  Listing it as well would
        double-count it in every share column.

        Mutation: build the element list from `alive` instead of `active` (i.e.
        forget to remove the folded ones) -- the totals are untouched, the
        residual is untouched, and the split silently gains two rows carrying
        the currents of elements that are not there any more.
        """
        self.assertEqual(self.ctx.n_elements, 0)
        folded_desc = {e.describe() for e in self.ctx.folded}
        live_desc = {e.describe() for e in self.ctx.elements}
        self.assertEqual(folded_desc & live_desc, set())
        self.assertEqual([t.label for t in self.dec.terms],
                         ["bare EM coupling"])

    def test_a_half_referenced_structure_refuses_to_claim_agreement(self):
        """
        The adversarial one.  With only ONE of the two coils grounded the
        engine reports Z_ab = nan (measurement port 'c2' has no return path)
        while this module folds the single ground in and produces a perfectly
        plausible 400.000000 pH -- exactly half of the fixture's real 800 pH,
        which is the most convincing kind of wrong number.

        So the requirement is not "agree" but "do not claim to": the
        authoritative total stays NaN, and the reconciliation says it could not
        be measured rather than reporting a residual of 0.

        Mutation: drop the `if not math.isfinite(resid)` warning branch -- the
        decomposition then reports 400 pH with an empty warning list and a
        residual of NaN that no caller is obliged to look at.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n3 signal c2 +\n2 ground\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, self.f0)
        dec = at.decompose(ctx, 0, 1, "M")

        self.assertEqual(len(ctx.folded), 1)
        self.assertTrue(math.isnan(dec.total_reference.real))
        self.assertAlmostEqual(dec.total_sum.real, 400e-12, delta=1e-15)
        self.assertTrue(math.isnan(dec.residual_rel))
        self.assertTrue(
            any("could not be measured" in w for w in dec.warnings),
            dec.warnings)
        # ... and the reason is named, in this module's words and in core's.
        self.assertEqual(ctx.bad_probes, [1])
        self.assertTrue(any("no return path" in w for w in dec.warnings))


class TestStructuralRankIsASpecBugNotPhysics(unittest.TestCase):
    """
    Requirement 4.  A redundant spec -- the same constraint written twice --
    makes H = Zt + G exactly singular, and the split between the elements
    involved stops being unique.  Reporting that as "the physics is
    unattributable" is the worst available outcome: it is a typo, it is
    visible in the integer incidence alone, and the total is not affected at
    all.  Structure first, condition numbers afterwards.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load(DIFF_PAIR)
        cls.f0 = mid_freq(cls.d)
        cls.Y4 = ball_network(F_TEST, n_balls=4)
        cls.Y8 = ball_network(F_TEST, n_balls=8)

    # -- the three ways a real spec becomes redundant ----------------------

    def test_overlapping_ranges_that_short_the_same_pair_twice(self):
        """
        The realistic typo: two ranges that overlap.  `3 short_to 4:1:5` and
        `4 short_to 5:1:6` chain to (3,4),(4,5) and (4,5),(5,6) -- the pair
        (4,5) is declared twice and nothing in the DSL notices.

        MEASURED: five elements, the duplicate is index 3, and the answer is
        identical to the non-overlapping spelling to 2.9e-15.

        Mutation: make `_dependent_columns` return [] whenever the float rank
        gate is passed a full-rank-looking matrix (i.e. delete the exact
        Fraction pass) -- the note disappears and the user is left with a
        singular H and no explanation.
        """
        spec = ("1 signal V\n2 signal A2\n"
                "3 short_to 4:1:5\n4 short_to 5:1:6\n6 ground\n")
        ctx = at.build_context(self.Y4, F_TEST,
                               parse_custom_termination_text(spec), F0)
        described = [e.describe() for e in ctx.elements]
        self.assertEqual(described.count("short 4-5"), 2)
        self.assertEqual(ctx.dependent, [3])
        note = " ".join(ctx.notes)
        self.assertIn("REDUNDANT", note)
        self.assertIn("short 4-5", note)

        clean = ("1 signal V\n2 signal A2\n3 short_to 4:1:6\n6 ground\n")
        c2 = at.build_context(self.Y4, F_TEST,
                              parse_custom_termination_text(clean), F0)
        self.assertEqual(c2.dependent, [])
        a = at.decompose(ctx, 0, 1, "Z").total_sum
        b = at.decompose(c2, 0, 1, "Z").total_sum
        self.assertLess(abs(a - b) / abs(b), 1e-10)

    def test_a_shorted_RING_although_no_two_columns_are_alike(self):
        """
        The case a cheap duplicate-detector misses.  Shorting eight ground
        balls into a RING -- the chain 3-4-...-10 plus a closing 10-3 -- makes
        the last column an exact combination of the other seven, but no two
        columns are equal, and none is the negative of another either.  Only a
        real elimination finds it.

        MEASURED: nine elements, `short 10-3` named as index 8, and the total
        equal to the ring-free spelling to 2.5e-14.

        Mutation: replace the elimination in `_dependent_columns` with a
        pairwise "have I seen this column (or its negative) before" scan --
        `test_overlapping_ranges...` above still passes and this goes red.
        """
        spec = ("1 signal V\n2 signal A2\n"
                "3 short_to 4:1:10\n10 short_to 3\n3 ground\n")
        ctx = at.build_context(self.Y8, F_TEST,
                               parse_custom_termination_text(spec), F0)
        self.assertEqual(ctx.dependent, [8])
        self.assertEqual(ctx.elements[8].describe(), "short 10-3")

        U = np.rint(ctx.U.real).astype(np.int64)
        cols = [tuple(int(x) for x in U[:, i]) for i in range(U.shape[1])]
        self.assertEqual(len(set(cols)), len(cols),
                         "a duplicate column would make this test trivial")
        for i in range(U.shape[1]):
            for j in range(U.shape[1]):
                if i != j:
                    self.assertFalse(np.array_equal(U[:, i], -U[:, j]))

        ring_free = "1 signal V\n2 signal A2\n3 short_to 4:1:10\n3 ground\n"
        c2 = at.build_context(self.Y8, F_TEST,
                              parse_custom_termination_text(ring_free), F0)
        self.assertEqual(c2.dependent, [])
        a = at.decompose(ctx, 0, 1, "M").total_sum
        b = at.decompose(c2, 0, 1, "M").total_sum
        self.assertLess(abs(a - b) / abs(b), 1e-10)

    def test_a_short_between_two_grounded_ports_is_a_SPEC_problem(self):
        """
        The wording matters as much as the detection.  This is a typo, so the
        report has to say the constraint was written twice and that the TOTAL
        is unaffected; the only thing at risk is which of the three elements
        is said to carry the current.

        Mutation: reword the note to blame the physics (drop "REDUNDANT" /
        "written twice"), or set `split_trustworthy = False` here -- the second
        is the exact "unattributable physics" outcome requirement 4 forbids,
        and it turns a spec typo into an unfixable-looking result.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, self.f0)
        dec = at.decompose(ctx, 0, 1, "Z")

        self.assertEqual(ctx.dependent, [2])
        note = " ".join(ctx.notes)
        self.assertIn("REDUNDANT", note)
        self.assertIn("short 3-4", note)
        self.assertIn("written twice", note)
        # It is NOT reported as a failure of the physics: the total stands, it
        # reconciles, and the split is offered with a caveat about the SPLIT.
        self.assertTrue(dec.split_trustworthy)
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        self.assertEqual(len(dec.terms), 4)
        self.assertTrue(any("MINIMUM-NORM" in w for w in dec.warnings))
        self.assertTrue(any("total is still exact" in w for w in dec.warnings))

    # -- the elements whose u collapses to zero ----------------------------

    def test_an_element_across_ONE_probe_side_is_named_and_really_is_nothing(self):
        """
        Requirement 4's third case: a stamp that sums to exactly zero once the
        probe sides are merged.  Ports 1 and 3 are both `signal V`, so they are
        ONE node and a resistor between them has u = 0.

        Two claims, and the second is the one that matters: it is reported by
        name with a reason, and deleting the declaration from the spec produces
        a BIT-IDENTICAL answer (measured: relative difference exactly 0.0), so
        "contributes exactly nothing" is a measurement and not a hope.

        Mutation: keep the zero column in `active` instead of dropping it --
        U loses rank, the case is then reported as a redundant SPEC and the
        real reason (it is shorted out by the probe) never reaches the user.
        """
        with_it = ("1 signal V\n3 signal V\n2 signal A2\n"
                   "1 lumped_between 3 R=10\n4 ground\n")
        without = "1 signal V\n3 signal V\n2 signal A2\n4 ground\n"
        ca = at.build_context(self.Y8, F_TEST,
                              parse_custom_termination_text(with_it), F0)
        cb = at.build_context(self.Y8, F_TEST,
                              parse_custom_termination_text(without), F0)

        dropped = {e.describe(): why for e, why in ca.dropped}
        self.assertIn("port 1-3", dropped)
        self.assertIn("same node", dropped["port 1-3"])
        self.assertEqual([e.describe() for e in ca.elements],
                         [e.describe() for e in cb.elements])
        self.assertEqual(ca.dependent, [])

        da = at.decompose(ca, 0, 1, "M")
        db = at.decompose(cb, 0, 1, "M")
        self.assertEqual(complex(da.total_sum), complex(db.total_sum))
        self.assertIn("NOT in the split", " ".join(da.notes))

    def test_a_ground_shorted_onto_a_probe_is_named_as_the_ENGINES_choice(self):
        """
        The other collapse: `5 ground` + `5 short_to 2` puts port 5 on the
        aggressor's probe node, where merge_terms lets the Signal win and
        throws the Ground away.  The element must be reported as discarded BY
        THE ENGINE -- not silently applied (which would ground the probe and
        change the answer) and not silently dropped.

        MEASURED: with and without the `5 ground` line the answer is
        bit-identical (1737.874389906 pH) and Zref is `array_equal`.

        Mutation: drop the `on_probe and elem.kind in _SHUNT_KINDS` branch so
        the ground becomes a live element -- the probe is pulled to 0 V and the
        decomposition stops describing the network the engine reduced.
        """
        with_it = ("1 signal V\n2 signal A2\n5 ground\n5 short_to 2\n"
                   "4 ground\n")
        without = "1 signal V\n2 signal A2\n5 short_to 2\n4 ground\n"
        ca = at.build_context(self.Y8, F_TEST,
                              parse_custom_termination_text(with_it), F0)
        cb = at.build_context(self.Y8, F_TEST,
                              parse_custom_termination_text(without), F0)
        why = {e.describe(): w for e, w in ca.dropped}
        self.assertIn("ground port 5", why)
        self.assertIn("Signal", why["ground port 5"])
        self.assertEqual([e.describe() for e in ca.elements], ["ground port 4"])
        self.assertTrue(np.array_equal(ca.Zref, cb.Zref))
        self.assertEqual(complex(at.decompose(ca, 0, 1, "M").total_sum),
                         complex(at.decompose(cb, 0, 1, "M").total_sum))

    def test_overlapping_GROUND_ranges_cannot_be_redundant_at_all(self):
        """
        The contract's first example -- "a port grounded twice through
        overlapping ranges" -- is UNREACHABLE through the DSL, and that is
        worth pinning rather than leaving as folklore: `per_port` is a dict
        keyed by port, so `3:1:4 ground` followed by `4:1:4 ground` is the
        same single Ground on port 4, not two.

        What must not happen is a phantom element: one declaration per PORT,
        and no redundancy accusation for a spec that has none.

        Mutation: build the element list by iterating the DSL lines instead of
        `range(n)` over per_port -- port 4 gains a second ground element, U
        loses rank, and a perfectly ordinary spec is reported as a spec bug.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3:1:4 ground\n4:1:4 ground\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, self.f0)
        self.assertEqual([e.describe() for e in ctx.elements],
                         ["ground port 3", "ground port 4"])
        self.assertEqual(ctx.dependent, [])
        self.assertFalse(any("REDUNDANT" in n for n in ctx.notes))

    # -- and the proof that the test is structural -------------------------

    def test_the_verdict_does_not_move_with_the_conditioning(self):
        """
        Requirement 4 says the rank test must be STRUCTURAL, i.e. a function of
        the integer incidence and of nothing else.  The direct proof: the same
        spec at three frequencies where MEASURED cond(Ybase) spans 505 to
        1.27e10 and cond(G) spans 1.19e16 to 4.45e16 gives the identical
        verdict [2] every time, and the healthy spec gives [] every time.

        Mutation: replace `_dependent_columns(U)` with a numerical test on G
        (`cond(G) > 1e12`) -- it agrees on all six of these points and fails
        the two tests below, which is exactly why both of them are here.
        """
        bad = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        good = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        conds = []
        for f in (1e6, 1e8, 5e9):
            cb = at.build_context(self.Y, self.d.freqs, clone(bad), f)
            cg = at.build_context(self.Y, self.d.freqs, clone(good), f)
            self.assertEqual(cb.dependent, [2], f"redundant spec @ {f:g} Hz")
            self.assertEqual(cg.dependent, [], f"healthy spec @ {f:g} Hz")
            conds.append((cb.cond_Ybase, cb.cond_G))
        # the precondition: the numbers really did move a lot underneath
        self.assertGreater(max(c[0] for c in conds)
                           / min(c[0] for c in conds), 1e6)

    def test_ill_conditioning_is_not_evidence_of_a_redundant_spec(self):
        """
        The false-alarm direction, and the reason a cond() test cannot stand in
        for the structural one.  Here the NETWORK ties two ground balls
        together through 1 nOhm: electrically those two declarations are the
        same node, MEASURED cond(Ybase) = 5.8e11 and cond(G) = 3.3e11 -- worse
        than the perfectly-conditioned baseline (cond 505) of the genuinely
        redundant spec above -- and yet the spec is not redundant at all.  The
        user wrote four distinct ground balls; the file made two of them
        equivalent.

        Mutation: any conditioning-threshold implementation of the rank check.
        Set it low enough to catch a real duplicate and it accuses this spec;
        set it high enough to spare this spec and the two-frequency test above
        picks it up.  Only the integer test satisfies both.
        """
        Yt = ball_network(F_TEST, n_balls=4, tie_first_two=1e-9)
        ctx = at.build_context(Yt, F_TEST,
                               parse_custom_termination_text(ball_spec(4)), F0)
        self.assertEqual(ctx.dependent, [])
        self.assertFalse(any("REDUNDANT" in n for n in ctx.notes))
        self.assertGreater(ctx.cond_Ybase, 1e10)
        self.assertGreater(ctx.cond_G, 1e10)
        # and the answer is still right: shorting two ideally-grounded balls
        # together changes nothing, so it must equal the untied network.
        dec = at.decompose(ctx, 0, 1, "M")
        untied = M_of(ball_network(F_TEST, n_balls=4), F_TEST, ball_spec(4))
        self.assertAlmostEqual(dec.total_sum.real, untied, delta=1e-15)
        self.assertAlmostEqual(untied * 1e12, 305.2133, delta=0.001)

    def test_the_spec_bug_is_named_even_when_the_numerics_have_given_up(self):
        """
        The ordering claim, stated the only way it can be observed from
        outside: at 1 MHz the same redundant spec is so ill-conditioned that
        the reconciliation withholds the per-element split entirely
        (MEASURED residual 0.25) -- and the REDUNDANT note is there anyway.

        A structural check that ran after, or instead of, the conditioning
        verdict would have nothing to say about exactly the file where the user
        most needs to be told that one of their three declarations is a typo.

        Mutation: move the `_dependent_columns` call inside a
        `if resid <= floor:` guard, or into decompose() after the residual is
        known -- every other test in this class still passes.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n3 short_to 4\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, 1e6)
        dec = at.decompose(ctx, 0, 1, "M")
        self.assertFalse(dec.split_trustworthy)
        self.assertEqual(dec.terms, [])
        self.assertEqual(ctx.dependent, [2])
        self.assertIn("REDUNDANT", " ".join(ctx.notes))
        self.assertIn("short 3-4", " ".join(ctx.notes))


class TestReconciliationDegradesNeverRefuses(unittest.TestCase):
    """
    Requirement 5.  compute_z_matrix's value is the authority; this module's
    own sum is the check on itself.  When the two disagree the answer is never
    to refuse -- the engine's total is still the right number and is still what
    the user came for -- but it is also never to stay quiet.
    """

    @classmethod
    def setUpClass(cls):
        cls.d, cls.Y = load(DIFF_PAIR)
        # 1 pOhm between two ground balls: physically a short, numerically a
        # 1e16 condition number, which is where the node-space solve dies.
        cls.Y_collapse = ball_network(F_TEST, n_balls=4, tie_first_two=1e-14)
        cls.ts_balls = parse_custom_termination_text(ball_spec(4))

    def test_a_collapsed_node_space_keeps_the_total_and_drops_only_the_split(self):
        """
        The worst real case I could build.  MEASURED: cond(Ybase) = 1.26e16, so
        pinv discards the direction the answer lives in and this module's own
        Z_ab comes back as EXACTLY 0 -- while compute_z_matrix, which reduces
        the same network by a different route, still gets 305.2133 pH.

        Everything about the required behaviour is visible here: the total is
        the engine's and is right (it equals the un-tied network's M, computed
        independently, because shorting two ideally-grounded balls changes
        nothing); the split is withheld rather than apportioned; the residual
        and its floor are both reported; and nothing raises.

        Mutation: let the catastrophic branch raise, or let it clear
        `total_reference` -- either turns the one case where the user still has
        a usable answer into a case where they have nothing.
        """
        ctx = at.build_context(self.Y_collapse, F_TEST, self.ts_balls, F0)
        dec = at.decompose(ctx, 0, 1, "M")

        self.assertEqual(complex(ctx.Zop[0, 1]), 0j)       # the collapse
        self.assertGreater(dec.residual_rel, at.RESIDUAL_CATASTROPHIC)
        self.assertFalse(dec.split_trustworthy)
        self.assertEqual(dec.terms, [])
        self.assertTrue(any("WITHHELD" in w for w in dec.warnings))
        self.assertTrue(math.isfinite(dec.residual_floor))

        untied = M_of(ball_network(F_TEST, n_balls=4), F_TEST, ball_spec(4))
        self.assertAlmostEqual(dec.total_reference.real, untied, delta=1e-15)
        self.assertAlmostEqual(dec.total_reference.real * 1e12, 305.2133,
                               delta=0.001)
        # the rendered report says so instead of printing an empty table
        text = "\n".join(at.format_decomposition(dec))
        self.assertIn("per-element split withheld", text)

    def test_the_decision_table_between_the_floor_and_the_catastrophic_gate(self):
        """
        Requirement 5 has three bands and the middle one -- "warn loudly, keep
        the split" -- is the one no fixture in this repo reaches: MEASURED
        across 401 frequencies of diff_pair the residual is either under the
        floor (healthy) or over 1e-2 (dead), with nothing in between.

        So the band is exercised by perturbing the AUTHORITATIVE reference
        directly, which is precisely what "the two algorithms disagree by X"
        means.  `ctx.Zref` is a public attribute of a plain dataclass and
        decompose() reads it fresh, so a scaled copy is a legitimate way to ask
        the question -- and it is the only way to ask it without editing the
        module under test.

        Mutation: raise instead of warning when `resid > floor` (the "refuse
        outright" behaviour requirement 5 forbids), or widen
        RESIDUAL_CATASTROPHIC to 1 so the 5% case keeps its split.
        """
        for relerr, want_warn, want_terms in ((0.0, False, True),
                                              (1e-6, True, True),
                                              (1e-4, True, True),
                                              (1e-3, True, True),
                                              (5e-2, True, False),
                                              (5e-1, True, False)):
            with self.subTest(relerr=relerr):
                ctx = at.build_context(ball_network(F_TEST, n_balls=4),
                                       F_TEST, clone(self.ts_balls), F0)
                ctx.Zref = ctx.Zref.copy()
                ctx.Zref[0, 1] *= (1.0 + relerr)
                dec = at.decompose(ctx, 0, 1, "M")   # must never raise

                warned = any("Reconciliation:" in w for w in dec.warnings)
                self.assertEqual(warned, want_warn)
                self.assertEqual(bool(dec.terms), want_terms)
                self.assertEqual(dec.split_trustworthy, want_terms)
                if want_warn:
                    # the warning carries BOTH numbers: what was measured and
                    # what was achievable.  "they disagree" on its own is not
                    # actionable -- the user cannot tell a bug from arithmetic.
                    w = next(w for w in dec.warnings if "Reconciliation:" in w)
                    self.assertIn("achievable floor", w)
                    self.assertIn("cond(Ybase)", w)
                # the totals are never withheld, in any band
                self.assertTrue(math.isfinite(abs(dec.total_reference)))
                self.assertTrue(math.isfinite(abs(dec.total_sum)))

    def test_an_error_bar_wider_than_the_answer_says_so_instead_of_hiding(self):
        """
        diff_pair at 1 MHz: the 1 fF port capacitance makes the largest entry
        of Z 1.6e5 Ohm while the mutual being extracted is 6 mOhm, so every
        digit of the answer is built from the big entry's rounding error.
        MEASURED: the condition-aware floor comes out ABOVE 1 and is clamped
        there -- i.e. floating point guarantees nothing at all here.

        The requirement is that this is said out loud.  A silently clamped
        floor is a gate that passes everything: the residual is 0.25 and
        `resid <= floor` is True, so without the note (and without the separate
        catastrophic gate, which is what actually fires here) this reads as a
        clean bill of health.

        Mutation: delete the `if floor >= 1.0` note -- the numbers do not move
        and the report loses the only sign that its own tolerance is vacuous.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")
        ctx = at.build_context(self.Y, self.d.freqs, ts, 1e6)
        dec = at.decompose(ctx, 0, 1, "M")
        self.assertEqual(dec.residual_floor, 1.0)
        self.assertGreater(dec.residual_rel, at.RESIDUAL_CATASTROPHIC)
        self.assertTrue(any("guarantees NOTHING" in n for n in dec.notes),
                        dec.notes)
        # and the catastrophic gate still bites even though resid <= floor
        self.assertLessEqual(dec.residual_rel, dec.residual_floor)
        self.assertFalse(dec.split_trustworthy)
        # the engine's total is right: M = 1 nH by construction on this fixture
        self.assertAlmostEqual(dec.total_reference.real, 1e-9, delta=1e-12)


class TestSharedReturnIsNotTheDiagonalModel(unittest.TestCase):
    """
    Requirement 2 -- the finding that motivated the whole revision.  Real
    package ground balls share a return plane, so their return impedances are
    mutually coupled; an independent series L per ball understates the
    effective common-mode return inductance by (1 + (n-1)k).

    The network here is the one that measurement was made on: four balls, each
    reaching the shared internal ground node through its own 1 nH via, with the
    victim and aggressor coils returning through the same node.
    """

    @classmethod
    def setUpClass(cls):
        cls.Y = ball_network(F_TEST, n_balls=4, l_via=1.0e-9)
        cls.ts = parse_custom_termination_text(ball_spec(4))
        cls.zL = 1j * OMEGA * 1e-9                 # one nanohenry, at F0
        cls.diag1 = at.termination_impedance_diagonal([cls.zL] * 4)
        cls.diag4 = at.termination_impedance_diagonal([4.0 * cls.zL] * 4)
        # z_self = 0, z_ret = 1 nH: four balls tied together and taken to the
        # reference through ONE shared inductance, which is what a return plane
        # is.  H = Zt + G takes it with no change to the maths and no change to
        # the cost.
        cls.shared = at.termination_impedance_shared_return(0j, cls.zL, 4)

    def _M(self, zt):
        ctx = at.build_context(self.Y, F_TEST, clone(self.ts), F0, zt=zt)
        return float(np.imag(ctx.Zop[0, 1])) / OMEGA

    def test_the_shared_return_moves_M_by_9_6_dB(self):
        """
        MEASURED, and this is the number the contract cites: four independent
        1 nH ball inductances give M = 631.333 pH, the same four balls sharing
        one 1 nH return give M = 1905.958 pH -- 9.597 dB, larger than the 6 dB
        dispute this feature exists to settle.

        Mutation: have `termination_impedance_shared_return` return
        `diag(z_self + z_ret)` (the "surely that is the same thing" reading) --
        the two models then agree to 0 dB and the entire finding disappears.
        """
        m_diag = self._M(self.diag1)
        m_shared = self._M(self.shared)
        self.assertAlmostEqual(m_diag * 1e12, 631.333, delta=0.01)
        self.assertAlmostEqual(m_shared * 1e12, 1905.958, delta=0.01)
        self.assertAlmostEqual(20.0 * math.log10(abs(m_diag / m_shared)),
                               -9.597, delta=0.01)

    def test_each_Zt_reproduces_the_ENGINE_spelling_of_the_SAME_network(self):
        """
        The claim that makes the number above worth anything: a dense Zt is not
        a new model, it is a network the shipped tool can already describe, and
        the two routes must agree.

          diag(1 nH)            == `3:1:6 lumped_to_gnd L=1n`
          shared(0, 1 nH)       == `3 short_to 4:1:6` + `3 lumped_to_gnd L=1n`

        MEASURED: 2.6e-15 and 1.7e-15 relative -- roundoff, through two
        completely different reductions.

        Mutation: drop the off-diagonal of Zt anywhere in the H = Zt + G path
        (e.g. `H = np.diag(np.diag(Zt)) + G`) -- the shared-return case then
        answers 631 pH against the engine's 1906 pH, i.e. the 9.6 dB reappears
        as a 9.6 dB ERROR with nothing raised.
        """
        eng_diag = M_of(self.Y, F_TEST,
                        "1 signal V\n2 signal A2\n3:1:6 lumped_to_gnd L=1n\n")
        eng_shared = M_of(self.Y, F_TEST,
                          "1 signal V\n2 signal A2\n3 short_to 4:1:6\n"
                          "3 lumped_to_gnd L=1n\n")
        self.assertLess(abs(self._M(self.diag1) - eng_diag) / abs(eng_diag),
                        1e-12)
        self.assertLess(abs(self._M(self.shared) - eng_shared)
                        / abs(eng_shared), 1e-12)

    def test_two_SPELLINGS_of_one_network_agree_to_0_000_dB(self):
        """
        The consistency check that keeps the 9.6 dB honest.  Four balls with
        4 nH each and four balls sharing one 1 nH are the SAME network -- the
        network is symmetric, so the four ball nodes sit at the same potential
        and four 4 nH paths in parallel are one 1 nH path.  If the dense model
        were simply "bigger numbers", this comparison would move too.

        MEASURED: 4.3e-16 relative through pkg_rlc_attrib and 7.7e-15 dB
        through compute_z_matrix -- 0.000 dB both ways.

        Mutation: any scaling slip in the shared builder (z_ret * n instead of
        z_ret, or z_ret added to the diagonal as well) breaks THIS test while
        leaving the 9.6 dB test green, because a wrong shared model is still a
        different model.
        """
        m_diag4 = self._M(self.diag4)
        m_shared = self._M(self.shared)
        self.assertLess(abs(m_diag4 - m_shared) / abs(m_shared), 1e-12)

        eng_diag4 = M_of(self.Y, F_TEST,
                         "1 signal V\n2 signal A2\n3:1:6 lumped_to_gnd L=4n\n")
        eng_shared = M_of(self.Y, F_TEST,
                          "1 signal V\n2 signal A2\n3 short_to 4:1:6\n"
                          "3 lumped_to_gnd L=1n\n")
        self.assertLess(abs(20.0 * math.log10(abs(eng_diag4 / eng_shared))),
                        1e-9)

    def test_the_builders_disagree_about_the_same_per_lead_value(self):
        """
        The comparison a user actually makes: "1 nH per ball", entered both
        ways.  `termination_impedance_diagonal([z]*4)` and
        `termination_impedance_shared_return(z, z, 4)` differ ONLY by the dense
        block -- same per-lead value, same four elements -- and MEASURED here
        that block is worth 11.85 dB (631.333 pH against 2470.029 pH).  The
        choice of builder is a first-order modelling decision, not a
        formatting one.

        Mutation: make the shared builder ignore `z_self` (return
        `z_ret * ones` alone) -- the answer collapses onto the 1905.958 pH of
        the test above, which is a DIFFERENT network, and only this test
        notices.
        """
        both = at.termination_impedance_shared_return(self.zL, self.zL, 4)
        m_diag = self._M(self.diag1)
        m_both = self._M(both)
        self.assertAlmostEqual(m_both * 1e12, 2470.03, delta=0.05)
        db = 20.0 * math.log10(abs(m_both / m_diag))
        self.assertAlmostEqual(db, 11.849, delta=0.01)


class TestGroupLevelSeesWhatPerElementCannot(unittest.TestCase):
    """
    Requirement 9's blind spot, in miniature.  The real case is 60 ground balls
    where every single-port delta is ~0 because the other 59 already carry the
    return, and every pairwise second difference is ~0 for the same reason: the
    effect is order-60, not order-2.

    Eight balls of 0.25 nH each is small enough to run in milliseconds and
    already shows the whole phenomenon.
    """

    @classmethod
    def setUpClass(cls):
        cls.N = 8
        cls.Y = ball_network(F_TEST, n_balls=cls.N, l_via=0.25e-9)
        cls.spec = ball_spec(cls.N)
        cls.ts = parse_custom_termination_text(cls.spec)
        cls.ctx = at.build_context(cls.Y, F_TEST, cls.ts, F0)

    def _honest_open(self, ports0):
        """M with the given 0-based ports left OPEN, through the engine."""
        t2 = clone(self.ts)
        for p in ports0:
            t2.per_port.pop(p, None)
        return M_of(self.Y, F_TEST, t2)

    def test_no_single_element_delta_hints_at_the_collective_effect(self):
        """
        MEASURED on this network: M = 42.423 pH with all eight balls grounded.
        Opening any ONE of them moves it by +5.295 pH.  Opening all eight moves
        it by -3276.7 pH -- 619 times larger, and the other sign.

        A table of per-element deltas is therefore not a ranking of what
        matters; it is eight readings of what does not.

        Mutation: implement `group_joint` as the sum of the individual deltas
        (the plausible-looking shortcut) -- it would report -3276.7 pH as
        +42.4 pH, i.e. 1.3% of the size and the wrong sign.
        """
        singles = at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "M")
        self.assertEqual(len(singles), self.N)
        base = singles[0].baseline_value.real
        self.assertAlmostEqual(base * 1e12, 42.423, delta=0.01)
        worst = max(abs(s.delta.real) for s in singles)
        self.assertAlmostEqual(worst * 1e12, 5.295, delta=0.01)

        gj = at.group_joint(self.ctx, 0, 1, "ground", at.alt_open(), "M")
        self.assertAlmostEqual(gj.joint_delta.real * 1e12, -3276.74, delta=0.05)
        self.assertGreater(abs(gj.joint_delta.real) / worst, 500.0)
        self.assertLess(gj.joint_delta.real, 0.0)
        self.assertGreater(max(s.delta.real for s in singles), 0.0)
        # and the non-additivity IS the effect, not a correction to it
        self.assertAlmostEqual(gj.sum_individual.real * 1e12, 42.36, delta=0.05)
        self.assertGreater(abs(gj.non_additivity.real),
                           0.99 * abs(gj.joint_delta.real))

    def test_the_pairwise_second_difference_misses_it_too(self):
        """
        The reason requirement 9b asks for GROUP level and not just pairs.
        MEASURED: the two-element joint change is +12.381 pH and its
        non-additivity is +1.792 pH -- smaller than a single element's own
        delta, and 0.05% of the eight-element non-additivity.

        So no amount of pairwise probing finds this: the second difference is
        not small because the elements are independent, it is small because
        two out of eight is still nearly all the return path.

        Mutation: cap `group_joint` at pairs (or have the caller approximate a
        group by summing its pairs) -- the recovered figure would be 1.79 pH
        against the true 3319 pH.
        """
        pair = at.group_joint(self.ctx, 0, 1, [0, 1], at.alt_open(), "M")
        gj = at.group_joint(self.ctx, 0, 1, "ground", at.alt_open(), "M")
        self.assertAlmostEqual(pair.joint_delta.real * 1e12, 12.381, delta=0.01)
        self.assertAlmostEqual(pair.non_additivity.real * 1e12, 1.792,
                               delta=0.01)
        self.assertLess(abs(pair.non_additivity.real),
                        1e-3 * abs(gj.non_additivity.real))
        # the pairwise result is exact -- it is small, not wrong
        ref = self._honest_open([2, 3])
        self.assertLess(abs(pair.joint_value.real - ref) / abs(ref), 1e-10)

    def test_the_group_and_cumulative_numbers_are_exact_against_a_rebuild(self):
        """
        The single most important property of the fast path: a low-rank update
        that is fast and slightly wrong is worse than no feature at all, and on
        a network like this one nothing about the numbers would look off.

        Every value below is compared against a REBUILT TerminationSet through
        compute_z_matrix.  MEASURED worst case over the 8 singles, the joint
        and the 4 cumulative points: 1.35e-14 relative.

        Mutation: reuse `Pmat_b` for `Rmat` (assume reciprocity), or drop the
        Schur term in `_z_matrix` -- both stay plausible and both go red here.
        """
        for s in at.sensitivity(self.ctx, 0, 1, [at.alt_open()], "M"):
            port0 = self.ctx.elements[s.elements[0]].ports[0]
            ref = self._honest_open([port0])
            self.assertLess(abs(s.new_value.real - ref) / abs(ref), 1e-10,
                            s.label)
        gj = at.group_joint(self.ctx, 0, 1, "ground", at.alt_open(), "M")
        ref_all = self._honest_open(range(2, 2 + self.N))
        self.assertLess(abs(gj.joint_value.real - ref_all) / abs(ref_all),
                        1e-10)

        cc = at.cumulative_curve(self.ctx, 0, 1, at.alt_open(), "M")
        self.assertEqual(cc.k, (1, 2, 4, 8))
        for k, val in zip(cc.k, cc.values):
            ports = [self.ctx.elements[e].ports[0] for e in cc.order[:k]]
            ref = self._honest_open(ports)
            self.assertLess(abs(val.real - ref) / abs(ref), 1e-10, f"k={k}")

    def test_the_cumulative_curve_turns_over_only_at_the_last_element(self):
        """
        What the curve is FOR.  MEASURED deltas at k = 1, 2, 4, 8:
        +5.29, +12.38, +37.43, -3276.74 pH.  The first three are a straight
        line through the origin -- extrapolating it to k = 8 predicts about
        +75 pH -- and the truth is -3277 pH.  There is no k below the last one
        at which the collapse is visible, which is why the curve has to be
        evaluated and not modelled.

        Mutation: evaluate the cumulative points by summing the individual
        deltas of the top-k instead of re-solving with all k changed together
        -- k = 1 and 2 barely move and k = 8 reports +42 pH instead of
        -3277 pH.
        """
        cc = at.cumulative_curve(self.ctx, 0, 1, at.alt_open(), "M")
        d = [x.real * 1e12 for x in cc.deltas]
        self.assertAlmostEqual(d[0], 5.29, delta=0.02)
        self.assertAlmostEqual(d[1], 12.38, delta=0.02)
        self.assertAlmostEqual(d[2], 37.43, delta=0.02)
        self.assertAlmostEqual(d[3], -3276.74, delta=0.05)
        linear_guess = d[0] * 8.0
        self.assertGreater(abs(d[3] - linear_guess), 50.0 * abs(linear_guess))
        # the non-additivity column is where that shows up
        self.assertLess(abs(cc.non_additivity[0].real), 1e-13)
        self.assertGreater(abs(cc.non_additivity[3].real), 3e-9)


class TestResonantGroundLeavesTheBracket(unittest.TestCase):
    """
    Requirement 10.  "M is somewhere between the ideal-ground value and the
    open value" is the estimate everybody makes by hand, and a series ground
    inductance resonating with a package capacitance breaks it: M leaves the
    bracket entirely, and it does so at an inductance a real ground ball HAS.

    One ball, 1 pF from its node to the reference, 5 Ohm of via resistance so
    the resonance is damped rather than infinite.  MEASURED anti-resonance at
    L = 822 pH, against 1/(omega^2 C) = 935 pH for the isolated pair.
    """

    @classmethod
    def setUpClass(cls):
        cls.Y = ball_network(F_TEST, n_balls=1, l_via=1e-9, r_via=5.0,
                             c_ball=1e-12, r_coil=1.0)
        cls.spec = "1 signal V\n2 signal A2\n3 ground\n"
        cls.ts = parse_custom_termination_text(cls.spec)
        cls.ctx = at.build_context(cls.Y, F_TEST, cls.ts, F0)
        cls.sw = at.sweep_mobius(cls.ctx, 0, 1, 0, "M", param="L")

    def _honest_M_at(self, L: float) -> float:
        t2 = clone(self.ts)
        t2.per_port[2] = LumpedToGnd(y_series_rlc(L=L))
        return M_of(self.Y, F_TEST, t2)

    def test_M_is_not_monotone_in_the_ground_inductance(self):
        """
        MEASURED along the closed form: M = 1300 pH at L = 0 (ideal ground),
        1682 pH at 200 pH, 3139 pH at 500 pH, 41.8 nH at 800 pH, then -5434 pH
        at 1 nH, -721 pH at 2 nH and back to +111 pH as L -> infinity (open).

        Both endpoints are positive and of order 100-1300 pH; the interior
        reaches hundreds of nanohenries of apparent M and changes sign.  A
        two-point estimate is not conservative here, it is simply unrelated to
        the answer.

        Mutation: evaluate the sweep by sampling the two endpoints and
        interpolating -- every assertion below fails, and a version that
        samples a coarse grid without the analytic critical points reports
        whatever it happened to land on.
        """
        v = {t: self.sw.quantity_at(t).real for t in
             (0.0, 0.2e-9, 0.5e-9, 0.8e-9, 1.0e-9, 2.0e-9)}
        self.assertAlmostEqual(v[0.0] * 1e12, 1300.43, delta=0.5)
        self.assertAlmostEqual(v[0.2e-9] * 1e12, 1681.91, delta=0.5)
        self.assertAlmostEqual(v[0.8e-9] * 1e12, 41759.6, delta=5.0)
        self.assertLess(v[1.0e-9], 0.0)          # the sign has flipped
        self.assertGreater(v[0.5e-9], 0.0)
        self.assertAlmostEqual(self.sw.value_open.real * 1e12, 111.30,
                               delta=0.5)
        self.assertTrue(self.sw.leaves_bracket)
        self.assertTrue(any("LEAVES" in n for n in self.sw.notes),
                        self.sw.notes)

    def test_the_two_endpoints_bracket_neither_the_size_nor_the_sign(self):
        """
        The bracket is [111 pH, 1300 pH]; the true range over the half-line is
        [-366 nH, +343 nH].  MEASURED overshoot: 263x the bracket's upper end,
        and the lower end of the range is NEGATIVE while both endpoints are
        positive.

        Mutation: compute `bracket` from the interval instead of from the two
        endpoints (or vice versa) -- `leaves_bracket` becomes structurally
        impossible and every resonance in every file goes unreported.
        """
        b_lo, b_hi = self.sw.bracket
        lo, hi = self.sw.interval
        self.assertAlmostEqual(b_lo * 1e12, 111.30, delta=0.5)
        self.assertAlmostEqual(b_hi * 1e12, 1300.43, delta=0.5)
        self.assertGreater(hi, 100.0 * b_hi)
        self.assertLess(lo, 0.0)
        self.assertGreater(b_lo, 0.0)

    def test_the_interior_extremum_is_exact_against_a_rebuilt_spec(self):
        """
        The extremum is analytic -- a Mobius map takes the real line to a
        circular arc, so the maximum of Im(Z) along it is a root of a real
        polynomial, not the best of a sampled set.  It has to be checked
        against the engine, at the value it claims, or "closed form" means
        nothing.

        MEASURED: arg_max = 821.95 pH, M there = 342.583 nH, and rebuilding
        the spec as `3 lumped_to_gnd L=821.95p` and running compute_z_matrix
        agrees to 1.2e-14 relative.  The same check at 200 pH and 2 nH agrees
        to 1.2e-16 and 2.9e-16.

        Mutation: use `abs(rt.imag) < 1e-9` without the `max(1, |Re|)` scaling
        in the root filter, or drop the `rt.real > 0` test -- the reported
        extremum moves to a root that is not on the physical half-line and this
        comparison fails immediately.
        """
        self.assertTrue(math.isfinite(self.sw.arg_max))
        self.assertAlmostEqual(self.sw.arg_max * 1e12, 821.95, delta=1.0)
        self.assertGreater(self.sw.arg_max, 0.0)
        for L in (0.2e-9, self.sw.arg_max, 2.0e-9):
            ref = self._honest_M_at(L)
            got = self.sw.quantity_at(L).real
            self.assertLess(abs(got - ref) / abs(ref), 1e-9, f"L={L:g}")
        # the extremum really is the extremum: no sampled point beats it
        for t in np.linspace(0.0, 5e-9, 501):
            self.assertLessEqual(self.sw.quantity_at(float(t)).real,
                                 self.sw.interval[1] * (1 + 1e-9))

    def test_a_bound_below_the_resonance_restores_a_usable_headline(self):
        """
        What the user does with the finding.  MEASURED: told that any ground
        inductance up to 500 pH is reachable, the interval becomes
        [1300 pH, 3139 pH] -- still outside the [ideal, open] bracket (so the
        flag stays up), but now a number a budget can be written against.

        Mutation: fold the t -> infinity limit into a BOUNDED sweep (the
        `raise ZeroDivisionError` guard in `_rational_extrema`) -- the interval
        would silently widen to include the open-circuit value the caller has
        just said is out of range.
        """
        sw = at.sweep_mobius(self.ctx, 0, 1, 0, "M", param="L", t_max=5e-10)
        lo, hi = sw.interval
        self.assertAlmostEqual(lo * 1e12, 1300.43, delta=0.5)
        self.assertAlmostEqual(hi * 1e12, 3138.70, delta=0.5)
        self.assertAlmostEqual(sw.arg_max, 5e-10, delta=1e-13)
        self.assertTrue(sw.leaves_bracket)
        for t in np.linspace(0.0, 5e-10, 101):
            v = sw.quantity_at(float(t)).real
            self.assertGreaterEqual(v, lo - 1e-9 * abs(hi))
            self.assertLessEqual(v, hi + 1e-9 * abs(hi))


class TestNonFiniteInput(unittest.TestCase):
    """
    One NaN in one S entry.  Real exports contain them -- a solver that failed
    to converge at one frequency writes a NaN and carries on -- and `s_to_y`
    spreads that one entry across the whole matrix at that frequency, so the
    question is only ever "does the rest of the sweep survive".
    """

    @classmethod
    def setUpClass(cls):
        cls.d = parse_touchstone(FIXTURES / DIFF_PAIR)
        cls.Y_clean = s_to_y(cls.d.s, cls.d.z0)
        s = cls.d.s.copy()
        cls.bad = 5
        s[cls.bad, 1, 0] = complex(float("nan"), float("nan"))
        cls.Y_nan = s_to_y(s, cls.d.z0)
        cls.ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 ground\n4 ground\n")

    def test_the_precondition_one_entry_poisons_one_whole_frequency(self):
        """Asserted so the tests below are known to be testing something."""
        self.assertTrue(np.isnan(self.Y_nan[self.bad]).all())
        self.assertTrue(np.isfinite(self.Y_nan[self.bad + 1]).all())
        self.assertTrue(np.array_equal(
            self.Y_nan[self.bad + 1], self.Y_clean[self.bad + 1]))

    def test_a_NaN_frequency_leaves_its_NEIGHBOURS_bit_identical(self):
        """
        The requirement: a NaN at one frequency must not abort the sweep.  The
        strongest form of that is bit-identity -- the neighbouring points must
        not merely still work, they must produce the same bits as the clean
        file, or something has quietly averaged, interpolated or clipped.

        MEASURED: `Zop` and `Zref` are `array_equal` on both sides of the bad
        point, and the closed-form sweep returns the identical interval.

        Mutation: any "clean the data first" step -- an interpolation over
        non-finite frequencies, or a global `np.nan_to_num` -- changes the
        neighbours and this goes red while a looser "is finite" assertion
        would not notice.
        """
        for idx in (self.bad - 1, self.bad + 1):
            f = float(self.d.freqs[idx])
            c_nan = at.build_context(self.Y_nan, self.d.freqs, clone(self.ts), f)
            c_ok = at.build_context(self.Y_clean, self.d.freqs, clone(self.ts), f)
            self.assertTrue(np.array_equal(c_nan.Zop, c_ok.Zop), f"@{f:g}")
            self.assertTrue(np.array_equal(c_nan.Zref, c_ok.Zref), f"@{f:g}")
            s_nan = at.sweep_mobius(c_nan, 0, 1, 0, "M", param="L", t_max=1e-8)
            s_ok = at.sweep_mobius(c_ok, 0, 1, 0, "M", param="L", t_max=1e-8)
            self.assertEqual(s_nan.interval, s_ok.interval)

    def test_the_whole_frequency_sweep_still_runs_around_the_bad_point(self):
        """
        Not just the immediate neighbours: a caller stepping the marker across
        the file must get an answer everywhere else.  21 points spanning
        1 MHz to 10 GHz, all finite, all reconciling.

        Mutation: hoist the frequency-independent work out of build_context and
        compute it once over the whole Y (a tempting optimisation) -- one NaN
        frequency then poisons every point in the file.
        """
        checked = 0
        for fi in range(0, len(self.d.freqs), 20):
            if fi == self.bad:
                continue
            f = float(self.d.freqs[fi])
            ctx = at.build_context(self.Y_nan, self.d.freqs, clone(self.ts), f)
            self.assertTrue(np.all(np.isfinite(ctx.Zop)), f"@{f:g}")
            checked += 1
        self.assertGreaterEqual(checked, 20)

    def test_asking_AT_the_NaN_frequency_fails_loudly_instead_of_answering(self):
        """
        The one thing that must NOT happen is a number.  compute_z_matrix
        itself survives -- it returns Z_ab = nan and one warning, which is the
        honest answer -- and this module must not turn that into anything else.

        The assertion is on ValueError, the base `AttribError` and numpy's
        `LinAlgError` share, deliberately: today this raises numpy's bare
        `LinAlgError("SVD did not converge")` from the pinv branch, with no
        verdict, no frequency and no file named (reported as a defect
        alongside this suite).  Wrapping it in an AttribError that says which
        frequency is unreadable would keep this test green, which is the point
        -- the test pins "loud, not plausible", not the current message.

        Mutation: catch the LinAlgError inside build_context and fall back to
        zeros -- the caller then gets a finite, entirely fictional Z.
        """
        f_bad = float(self.d.freqs[self.bad])
        Z, _names, warns = compute_z_matrix(
            self.Y_nan[self.bad:self.bad + 1],
            self.d.freqs[self.bad:self.bad + 1], clone(self.ts))
        self.assertTrue(np.isnan(Z[0][0, 1]))       # core's honest answer

        with self.assertRaises(ValueError):
            at.build_context(self.Y_nan, self.d.freqs, clone(self.ts), f_bad)

    def test_an_element_with_infinite_admittance_is_named(self):
        """
        The other non-finite input, and it comes from the spec rather than the
        file: `lumped_to_gnd R=0` with no L and no C is y = 1/0.  The engine
        stamps the infinity and returns NaN; this module treats it as an ideal
        short and gets a perfectly reasonable 1.0099 nH.

        Neither is wrong, but the disagreement must be visible: the element is
        named, the difference in treatment is spelled out, and the
        reconciliation reports that it could not be measured rather than
        claiming agreement.

        Mutation: drop the `not math.isfinite(yv)` branch -- z_declared becomes
        1/inf = 0 silently, which is the same answer with no warning at all,
        and the user never learns the engine disagrees.
        """
        ts = parse_custom_termination_text(
            "1 signal c1 +\n2 signal c2 +\n3 lumped_to_gnd R=0\n4 ground\n")
        # core's y_series_rlc really does evaluate 1/0 here; the RuntimeWarning
        # is the expected route to the infinity and is not the thing under test.
        with np.errstate(divide="ignore", invalid="ignore"):
            ctx = at.build_context(self.Y_clean, self.d.freqs, ts,
                                   float(self.d.freqs[200]))
        joined = " ".join(ctx.warnings)
        self.assertIn("INFINITE admittance", joined)
        self.assertIn("port 3 -> gnd", joined)
        self.assertIn("compute_z_matrix", joined)

        dec = at.decompose(ctx, 0, 1, "M")
        self.assertTrue(math.isnan(dec.total_reference.real))
        self.assertTrue(math.isfinite(dec.total_sum.real))
        self.assertTrue(any("could not be measured" in w
                            for w in dec.warnings), dec.warnings)


if __name__ == "__main__":
    unittest.main()
