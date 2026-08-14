"""
Port attribution on a COMPOSED network: the cross-file links live IN the
baseline (R2-8), and what happens when they do not.

WHY THIS FILE EXISTS.  `block_diag(Y_A, Y_B)` plus one link per bond is an
ordinary `TerminationSet`, so every rule in `pkg_rlc_attrib` applies to it
unchanged -- except one, and that one produces the worst output this module
can: a confident, EXACTLY ZERO, perfectly reconciled wrong answer.  The
baseline is "probe sides merged, every other port OPEN", and with the
cross-file links left as ELEMENTS on top of it the baseline is the two files as
DISCONNECTED ISLANDS.  `Ybase` is then exactly block diagonal, so `Zbase` is
too, and `r_a[e] = w_a^T Zbase u_e` is zero TO THE LAST BIT for every element
inside the far file.

Measured here, on the 12-port construction below, at 5 GHz:

    ground port 11    0.0000000000e+00        exactly, `== 0` is True
    ground port 12    0.0000000000e+00        exactly
    residual_rel      5.645e-15               floor 1.044e-08, trustworthy

while those same two package ground balls are worth a FACTOR OF 1.538 in M --
704.702 pH grounded against 1.0837047531 nH open, i.e. -3.7381 dB, measured
through `compute_z_matrix` and not through this module.  No residual can see
it, because the totals are right: the whole answer has been attributed to the
two links.

THE FIX IS A GAUGE CHANGE, NOT A BUG FIX (`docs/theory.md` 13.10).  The links
move into the baseline, so a composed network's baseline is "the files
CONNECTED, everything else open".  Every term moves; the network, the total and
the element currents do not.  That is why it is requested
(`baseline=BaselineLinks(...)`) and named on the report header rather than
switched on quietly -- two attribution reports are comparable only when their
baselines match.

THE CHECK THAT MAKES IT MORE THAN SELF-CONSISTENT.  With the links in the
baseline the "bare EM coupling" term must equal what the ENGINE says for the
same spec with the ground balls removed -- because that is literally what the
baseline is.  Measured: 1.0837047531e-09 both ways, 2.481e-15 relative, one
number out of `decompose` and the other out of `compute_z_matrix`.  The same
rule runs on the cold-start side: every screen delta is checked against an
honest re-solve through `compute_z_matrix` with a rebuilt `TerminationSet`.

Nothing here changes single-file behaviour: `baseline=` defaults to None and
`TestNothingMovesWithoutAPolicy` pins that a repo fixture decomposes to the
same bytes with and without the parameter present.

Every guard here was mutation-checked; the mutation that defeats it is named in
the test's own docstring.
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

import pkg_rlc.physics.attrib as at  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    Ground,
    LumpedBetween,
    ShortPair,
    Signal,
    TerminationSet,
    compute_z_matrix,
    parse_port_range,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
)

FIXTURES = _HERE / "fixtures"

#: Five points around the marker.  Everything here is an @-a-point question;
#: the axis exists only because `build_context` takes one.
FREQS = np.linspace(4.5e9, 5.5e9, 5)
MARKER = 5.0e9
IDX = int(np.argmin(np.abs(FREQS - MARKER)))
OMEGA = 2.0 * math.pi * float(FREQS[IDX])


# ---------------------------------------------------------------------------
# The construction
# ---------------------------------------------------------------------------
#
# It is BUILT rather than loaded, for the same reason the cold-start suite
# builds its planted case: no fixture in this repo is a two-file composition,
# and the defect needs one.  It is also built from a NETLIST rather than from
# `pkg_rlc_compose`, so that the two halves of this feature cannot agree with
# each other and with nothing else.
#
# The physics is the one the requirement measured (its "config A"): the die's
# return is brought out as PORTS, so the return current has to travel through
# the package to get home, and the package's ground balls are what decide
# whether it can.  A note on why the probes are the shape they are: a fully
# differential port injects zero NET current (`1^T w_b == 0`, which
# `_return_budget` already documents), so a package reachable only through the
# common mode would barely be excited by one.  Here the two coils SHARE their
# return, that shared return leaves the die at tap 5 and comes back at tap 6,
# and the package sits in between -- so both differential loops run through it
# and the ground balls change the answer by 3.74 dB.


class Net:
    """A tiny nodal builder.  'gnd' is the reference and is never a row."""

    def __init__(self) -> None:
        self.nodes: dict[str, int] = {}
        self.branches: list[tuple[int, int, object]] = []
        self.coupled: list[tuple[list[tuple[int, int]], object]] = []

    def n(self, name: str) -> int:
        if name == "gnd":
            return -1
        return self.nodes.setdefault(name, len(self.nodes))

    def rl(self, a: str, b: str, R: float, L: float) -> None:
        i, j = self.n(a), self.n(b)
        self.branches.append((i, j, lambda w, R=R, L=L: 1.0 / (R + 1j * w * L)))

    def c(self, a: str, b: str, C: float) -> None:
        i, j = self.n(a), self.n(b)
        self.branches.append((i, j, lambda w, C=C: 1j * w * C))

    def coupled_rl(self, a1: str, b1: str, R1: float, L1: float,
                   a2: str, b2: str, R2: float, L2: float, M: float) -> None:
        edges = [(self.n(a1), self.n(b1)), (self.n(a2), self.n(b2))]
        self.coupled.append((edges, lambda w: np.array(
            [[R1 + 1j * w * L1, 1j * w * M],
             [1j * w * M, R2 + 1j * w * L2]], dtype=complex)))

    def Y(self, w: float) -> np.ndarray:
        N = len(self.nodes)
        out = np.zeros((N, N), dtype=complex)

        def stamp(i: int, j: int, y: complex) -> None:
            if i >= 0:
                out[i, i] += y
            if j >= 0:
                out[j, j] += y
            if i >= 0 and j >= 0:
                out[i, j] -= y
                out[j, i] -= y

        for i, j, f in self.branches:
            stamp(i, j, f(w))
        for edges, zf in self.coupled:
            Yb = np.linalg.inv(zf(w))
            A = np.zeros((len(edges), N), dtype=complex)
            for k, (i, j) in enumerate(edges):
                if i >= 0:
                    A[k, i] = 1.0
                if j >= 0:
                    A[k, j] = -1.0
            out = out + A.T @ Yb @ A
        return out

    def port_Y(self, w: float, ports: list[str]) -> np.ndarray:
        """The Y seen at `ports`, every other node Schur-eliminated."""
        Ymat = self.Y(w)
        pi = [self.nodes[p] for p in ports]
        ii = [k for k in range(len(self.nodes)) if k not in pi]
        Ypp = Ymat[np.ix_(pi, pi)]
        if not ii:
            return Ypp
        return Ypp - (Ymat[np.ix_(pi, ii)]
                      @ np.linalg.solve(Ymat[np.ix_(ii, ii)],
                                        Ymat[np.ix_(ii, pi)]))


def em_block(pad_caps: bool = True) -> tuple[Net, list[str]]:
    """
    6 ports: victim coil 1/2, aggressor coil 3/4, ground-bus taps 5/6.

    The two coils are mutually coupled (300 pH) and share their return stub
    `nS`; the return leaves at tap 5 and comes back at tap 6, with only a POOR
    on-die path (2 ohm + 2 nH) between the two halves of the bus.  That last
    branch is what keeps the EM block ONE connected piece -- without it its
    own 6-port Y is exactly block diagonal too (`nS` and `nP` have no internal
    path, so the Schur term vanishes), which would make the component count
    this file asserts on an artefact of the fixture instead of a fact about
    the composition.
    """
    net = Net()
    net.coupled_rl("e0", "nS", 0.6, 2.0e-9, "e2", "nS", 0.9, 3.0e-9, 0.30e-9)
    net.rl("e1", "nP", 0.05, 10e-12)
    net.rl("e3", "nP", 0.05, 15e-12)
    net.rl("nS", "e4", 0.05, 50e-12)
    net.rl("nP", "e5", 0.05, 60e-12)
    net.rl("nS", "nP", 2.0, 2.0e-9)
    if pad_caps:
        for name, C in (("e0", 2e-15), ("e1", 8e-15), ("e2", 3e-15),
                        ("e3", 5e-15), ("e4", 1.5e-15), ("e5", 2.5e-15),
                        ("nS", 4e-15), ("nP", 6e-15)):
            net.c(name, "gnd", C)
    return net, ["e0", "e1", "e2", "e3", "e4", "e5"]


def pkg_block(pad_caps: bool = True) -> tuple[Net, list[str]]:
    """
    6 ports: bond pads 1/2 (global 7/8), spare pins 3/4, ground balls 5/6.

    The two pads reach the plane through their traces; the direct plane path
    between the two halves is deliberately WEAK (0.5 ohm + 800 pH = 25 ohm at
    5 GHz) so that the low-impedance route from pad to pad is the one through
    the balls and the reference -- which is exactly why grounding them or not
    decides the answer.
    """
    net = Net()
    net.rl("p0", "nQ1", 0.02, 100e-12)      # bond pad IN
    net.rl("p1", "nQ2", 0.02, 120e-12)      # bond pad OUT
    net.rl("nQ1", "nQ2", 0.5, 800e-12)      # weak direct plane path
    net.rl("p2", "nQ1", 0.03, 200e-12)      # spare pin
    net.rl("p3", "nQ2", 0.03, 250e-12)      # spare pin
    net.rl("p4", "nQ1", 0.02, 90e-12)       # ground ball 1
    net.rl("p5", "nQ2", 0.02, 110e-12)      # ground ball 2
    if pad_caps:
        for name, C in (("p0", 20e-15), ("p1", 25e-15), ("p2", 30e-15),
                        ("p3", 35e-15), ("p4", 40e-15), ("p5", 45e-15),
                        ("nQ1", 200e-15), ("nQ2", 250e-15)):
            net.c(name, "gnd", C)
    return net, ["p0", "p1", "p2", "p3", "p4", "p5"]


def combined_Y(pad_caps: bool = True) -> np.ndarray:
    """`block_diag(Y_EM, Y_PKG)` -- which is what a composer produces."""
    em, ep = em_block(pad_caps)
    pk, pp = pkg_block(pad_caps)
    out = np.zeros((len(FREQS), 12, 12), dtype=complex)
    for i, f in enumerate(FREQS):
        w = 2.0 * math.pi * float(f)
        out[i, :6, :6] = em.port_Y(w, ep)
        out[i, 6:, 6:] = pk.port_Y(w, pp)
    return out


#: probes on 1/2 and 3/4 (0-based 0/1 and 2/3); taps 5/6 bond to package pads
#: 7/8; ground balls on 11/12.  'A' and 'B' are reserved names in core, so the
#: measurement ports are 'vic' and 'agg'.
PROBES: dict[int, object] = {0: Signal("vic", +1), 1: Signal("vic", -1),
                             2: Signal("agg", +1), 3: Signal("agg", -1)}
LINKS = [ShortPair(4, 6), ShortPair(5, 7)]
BLOCKS = at.PortBlocks.from_sizes([6, 6])
POLICY = at.BaselineLinks(blocks=BLOCKS)


def spec(grounds: tuple[int, ...] = (10, 11),
         links: list | None = None) -> TerminationSet:
    pp = dict(PROBES)
    for p in grounds:
        pp[p] = Ground()
    return TerminationSet(per_port=pp,
                          couplings=list(LINKS if links is None else links))


def engine_M(Y: np.ndarray, terms: TerminationSet) -> float:
    """`compute_z_matrix`'s M for the victim/aggressor pair -- the second opinion."""
    Z, names, _ = compute_z_matrix(Y, FREQS, terms)
    a, b = names.index("vic"), names.index("agg")
    return float(Z[IDX, a, b].imag) / OMEGA


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.Y = combined_Y()


# ---------------------------------------------------------------------------
# The construction itself -- before anything is asserted about attribution
# ---------------------------------------------------------------------------

class TestTheConstructionIsHonest(_Base):
    """
    The fixture has to earn the right to be used, and every claim here is made
    with `compute_z_matrix` alone.  If the package did not matter, or if the
    combined Y were not block diagonal, every test below would pass for the
    wrong reason.
    """

    def test_the_combined_Y_is_EXACTLY_block_diagonal(self):
        """
        Mutation: composing with any cross term (a stray coupling between the
        blocks) makes `Zbase` non-block-diagonal and the exactly-zero
        contributions below become merely small -- which is a different, much
        less interesting bug.  `assertEqual` on 0.0, not `assertAlmostEqual`.
        """
        cross = self.Y[IDX, :6, 6:]
        self.assertEqual(float(np.max(np.abs(cross))), 0.0)
        self.assertEqual(float(np.max(np.abs(self.Y[IDX, 6:, :6]))), 0.0)

    def test_the_package_ground_balls_are_worth_3_74_dB_of_M(self):
        """
        The whole file rests on this: the two balls DO decide the answer.
        Measured through the engine: 704.70176729 pH grounded against
        1.0837047531 nH open, ratio 0.650271, -3.7381 dB.

        Mutation: strengthening the direct plane path (`nQ1`-`nQ2`) until the
        balls stop mattering leaves every later assertion technically true and
        completely uninteresting.
        """
        m_gnd = engine_M(self.Y, spec())
        m_open = engine_M(self.Y, spec(grounds=()))
        self.assertAlmostEqual(m_gnd, 7.0470176729e-10, delta=1e-19)
        self.assertAlmostEqual(m_open, 1.0837047531e-09, delta=1e-19)
        db = 20.0 * math.log10(abs(m_gnd / m_open))
        self.assertAlmostEqual(db, -3.7381, places=3)

    def test_each_block_on_its_own_is_ONE_connected_piece(self):
        """
        The disconnection this file is about must come from the COMPOSITION,
        not from a fixture that is already in pieces.

        Mutation: dropping the poor on-die return (`nS`-`nP`, 2 ohm + 2 nH)
        splits the EM block's own 6-port Y into two islands -- `nS` and `nP`
        then have no internal path, so the Schur term between the even and odd
        ports is exactly zero -- and `build_context` reports THREE components
        instead of two.  Measured before that branch was added.
        """
        for lo, hi in ((0, 6), (6, 12)):
            blk = self.Y[IDX, lo:hi, lo:hi]
            adj = blk != 0
            seen = {0}
            frontier = [0]
            while frontier:
                x = frontier.pop()
                for y in np.nonzero(adj[x])[0]:
                    if int(y) not in seen:
                        seen.add(int(y))
                        frontier.append(int(y))
            self.assertEqual(len(seen), 6, f"block {lo}:{hi} is in pieces")


# ---------------------------------------------------------------------------
# The defect, with no policy in force
# ---------------------------------------------------------------------------

class TestTheDefectWithoutTheGauge(_Base):
    """
    What the module does today on a composed network when nobody asks for the
    gauge.  These tests pin the DEFECT deliberately: it is what the warning
    below has to be able to see, and it is what `docs/theory.md` 13.10's gauge
    argument is about.
    """

    def test_every_package_element_contributes_EXACTLY_zero(self):
        """
        Not "small": `contribution == 0` is True, both parts, to the last bit,
        because `Zbase` is block diagonal and LAPACK's partial pivoting cannot
        mix rows across an exactly-zero off-block.

        Mutation: none is needed to make this pass -- it is the bug.  It is
        here so that the WITH-gauge test cannot pass by accident on a build
        where the gauge silently does nothing.
        """
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER)
        dec = at.decompose(ctx, "vic", "agg", "M")
        by = {t.label: t for t in dec.terms}
        for lab in ("ground port 11", "ground port 12"):
            self.assertIn(lab, by)
            self.assertEqual(by[lab].contribution, 0)
            self.assertEqual(by[lab].contribution.real, 0.0)
            self.assertEqual(by[lab].contribution.imag, 0.0)

    def test_and_the_reconciliation_reports_perfect_health_anyway(self):
        """
        The totals ARE right -- the whole answer has been attributed to the two
        links -- so no residual gate can catch this.  Measured 5.645e-15
        against a floor of 1.044e-08, and `split_trustworthy` is True.

        That is the entire argument for `_island_elements` being a STRUCTURAL
        test rather than a magnitude one.
        """
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER)
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertLess(dec.residual_rel, 1e-13)
        self.assertTrue(dec.split_trustworthy)
        self.assertAlmostEqual(dec.total_reference.real, 7.0470176729e-10,
                               delta=1e-19)

    def test_build_context_WARNS_about_it_without_being_asked(self):
        """
        The caller who does not know the word "composed" still has to be told.
        The warning names both elements, says the zero is a property of the
        BASELINE, and points at `baseline=BaselineLinks(...)`.

        Mutation: `_island_elements` returning `[]` unconditionally -- the
        warning disappears and this goes red while every number stays right,
        which is exactly the failure mode.
        """
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER)
        hit = [w for w in ctx.warnings if "EXACTLY ZERO" in w]
        self.assertEqual(len(hit), 1, ctx.warnings)
        w = hit[0]
        self.assertIn("ground port 11", w)
        self.assertIn("ground port 12", w)
        self.assertIn("2 disconnected parts", w)
        self.assertIn("BaselineLinks", w)

    def test_the_warning_reaches_the_decomposition_and_the_printed_report(self):
        """
        `ctx.warnings` -> `Decomposition.warnings` -> a `WARN:` line.  A
        warning that only exists on the context is a warning nobody reads.

        Mutation: appending it to `notes` instead of `warnings` -- the printed
        line becomes `note:` and this goes red.
        """
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER)
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertTrue(any("EXACTLY ZERO" in w for w in dec.warnings))
        lines = at.format_decomposition(dec)
        self.assertTrue(any(l.strip().startswith("WARN:") and "EXACTLY ZERO" in l
                            for l in lines), lines)


# ---------------------------------------------------------------------------
# The gauge
# ---------------------------------------------------------------------------

class TestTheGauge(_Base):
    """
    With `baseline=BaselineLinks(blocks=...)` the links are STRUCTURE: they are
    absorbed into the baseline, they have no term of their own, and the package
    elements get real signed contributions.
    """

    def setUp(self) -> None:
        self.ctx = at.build_context(self.Y, FREQS, spec(), MARKER,
                                    baseline=POLICY)
        self.dec = at.decompose(self.ctx, "vic", "agg", "M")

    def test_the_links_become_structure_and_leave_the_element_list(self):
        """
        Mutation: selecting nothing (an empty `structural_idx`) leaves the two
        shorts in `ctx.elements` and the whole gauge is a no-op.
        """
        self.assertEqual([e.describe() for e in self.ctx.structural],
                         ["short 5-7", "short 6-8"])
        self.assertEqual([e.describe() for e in self.ctx.elements],
                         ["ground port 11", "ground port 12"])
        self.assertEqual([t.label for t in self.dec.terms],
                         ["bare EM coupling", "ground port 11",
                          "ground port 12"])

    def test_the_package_ground_balls_now_carry_REAL_signed_contributions(self):
        """
        -207.65150602 pH and -171.35147981 pH, both negative -- the balls
        shorten the shared return, so they REDUCE the coupling, which is the
        physics.

        Mutation: any of them clipped, abs()-ed or hidden (the repo's
        signed-value invariant) -- both are negative here, so a magnitude
        would be caught by the sign assertion alone.
        """
        by = {t.label: t.contribution for t in self.dec.terms}
        self.assertAlmostEqual(by["ground port 11"].real, -2.0765150602e-10,
                               delta=1e-19)
        self.assertAlmostEqual(by["ground port 12"].real, -1.7135147981e-10,
                               delta=1e-19)
        self.assertNotEqual(by["ground port 11"], 0)
        self.assertNotEqual(by["ground port 12"], 0)
        self.assertLess(by["ground port 11"].real, 0.0)
        self.assertLess(by["ground port 12"].real, 0.0)

    def test_the_bare_term_IS_the_engines_answer_with_the_balls_removed(self):
        """
        THE LOAD-BEARING TEST OF THIS FILE, and the only one whose two sides
        come from different algorithms.  The composed baseline is by definition
        "the files connected, everything else open", so the bare EM term must
        equal what `compute_z_matrix` says for exactly that spec.  Measured
        1.0837047531e-09 both ways, 2.481e-15 relative.

        Mutation: folding the links WITHOUT stamping them (dropping the
        `folded_lumped` branch, or merging the wrong node pair) moves the bare
        term while leaving the sum right, because the element solve absorbs the
        difference.  This is the only assertion here that notices.
        """
        bare = self.dec.direct_term
        self.assertIsNotNone(bare)
        m_open = engine_M(self.Y, spec(grounds=()))
        self.assertLess(abs(bare.contribution.real - m_open) / abs(m_open),
                        1e-12)

    def test_the_terms_still_sum_to_the_engines_total(self):
        """
        A gauge change moves the split and NOTHING else.  Measured 4.402e-16
        relative against `compute_z_matrix`, and the reported residual is
        2.244e-15 against a floor of 8.868e-11.

        Mutation: `_absorb` comparing REDUCED node indices against ORIGINAL
        ones (`node_of_port[p]` instead of `node_map[node_of_port[p]]`) -- the
        historical bug the surrounding comment already records, and one that
        needs TWO folds to show itself, which is exactly what a two-bond
        composition is.  The second link then merges the wrong pair, the
        network is not the declared one, and the residual is what says so.

        Checked and NOT a mutation of this test: leaving the structural
        elements in `alive` as well as folding them.  `active` is
        `alive - folded`, so they never come back, and even forced back in, an
        ideal link's stamp is the zero vector once its two ends are one node
        and the existing `still` check drops it as annihilated.  A LUMPED link
        genuinely would double-count, and that is
        `test_a_LUMPED_cross_file_link_is_stamped_not_merged`.
        """
        total = sum(t.contribution.real for t in self.dec.terms)
        engine = engine_M(self.Y, spec())
        self.assertLess(abs(total - engine) / abs(engine), 1e-12)
        self.assertLess(self.dec.residual_rel, 1e-13)
        self.assertTrue(self.dec.split_trustworthy)

    def test_the_island_warning_is_GONE_because_the_baseline_is_connected(self):
        """
        The warning and the gauge are two halves of one statement, so with the
        gauge in force the baseline is one component and there is nothing to
        warn about.

        Mutation: firing the warning off the DECLARED spec instead of off the
        final `Ybase` -- it would keep firing here, where it is now wrong.
        """
        self.assertFalse([w for w in self.ctx.warnings if "EXACTLY ZERO" in w],
                         self.ctx.warnings)
        self.assertEqual(at._island_elements(self.ctx.Ybase, self.ctx.U,
                                             self.ctx.W), ([], 1))

    def test_a_policy_that_did_not_reach_a_part_says_THAT_not_pass_a_policy(self):
        """
        The realistic user error: the block map is set up and the bonds are
        never declared.  A policy IS in force, the files are still islands, and
        telling the caller to "pass baseline=BaselineLinks(...)" would be
        telling them to do what they have just done -- a bug report, not a
        warning.

        Mutation: one unconditional message.  Everything still fires, the
        numbers are unchanged, and the only sentence a stuck user can act on is
        the one that is wrong.
        """
        terms = spec(links=[])          # block map, no bonds declared
        ctx = at.build_context(self.Y, FREQS, terms, MARKER, baseline=POLICY)
        self.assertEqual(ctx.structural, [])
        hit = [w for w in ctx.warnings if "EXACTLY ZERO" in w]
        self.assertEqual(len(hit), 1, ctx.warnings)
        self.assertIn("A baseline policy IS in force", hit[0])
        self.assertIn("absorbed 0 link(s)", hit[0])
        self.assertIn("F2: ports 7-12", hit[0])
        self.assertNotIn("pass baseline=", hit[0])

    def test_an_explicit_links_policy_reaches_the_same_numbers(self):
        """
        `links=((4, 6), (5, 7))` with no block map at all must select the same
        two elements: a caller who knows their bonds but has no composer
        metadata is not a second code path.

        Mutation: `selects` testing `blocks` FIRST and returning False when
        there are none -- the explicit form becomes a no-op.
        """
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER,
                               baseline=at.BaselineLinks(links=((4, 6), (5, 7))))
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertEqual([e.describe() for e in ctx.structural],
                         ["short 5-7", "short 6-8"])
        got = [t.contribution.real for t in dec.terms]
        want = [t.contribution.real for t in self.dec.terms]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=1e-22)

    def test_a_LUMPED_cross_file_link_is_stamped_not_merged(self):
        """
        A bond wire has inductance, so `connect` is a `lumped_between` and not
        only a `short_to` -- the requirement's own "one primitive, not two".
        An ideal link merges two nodes; a lumped one adds `y * u u^T` to the
        baseline, which is a different branch of `_absorb`.

        Measured with 30 pH / 35 pH bond wires: M = 745.95611923 pH, residual
        1.331e-15, and the two balls come out -198.66681604 pH and
        -163.66322965 pH.

        Mutation: sending a non-ideal element down the node-merge branch (or
        forgetting to append it to `folded_lumped`) leaves the wire out of the
        baseline entirely; the residual then reports the disagreement, which is
        what the assertion on it is for.
        """
        links = [LumpedBetween(4, 6, y_series_rlc(0.05, 30e-12, 0.0)),
                 LumpedBetween(5, 7, y_series_rlc(0.05, 35e-12, 0.0))]
        terms = spec(links=links)
        ctx = at.build_context(self.Y, FREQS, terms, MARKER, baseline=POLICY)
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertEqual([e.describe() for e in ctx.structural],
                         ["port 5-7", "port 6-8"])
        self.assertEqual([e.describe() for e in ctx.elements],
                         ["ground port 11", "ground port 12"])
        self.assertLess(dec.residual_rel, 1e-12)
        self.assertAlmostEqual(dec.total_reference.real, 7.4595611923e-10,
                               delta=1e-19)
        by = {t.label: t.contribution.real for t in dec.terms}
        self.assertAlmostEqual(by["ground port 11"], -1.9866681604e-10,
                               delta=1e-19)
        self.assertAlmostEqual(by["ground port 12"], -1.6366322965e-10,
                               delta=1e-19)


# ---------------------------------------------------------------------------
# Saying it out loud
# ---------------------------------------------------------------------------

class TestTheGaugeIsNamed(_Base):
    """
    A gauge change that is not on the report is a wrong answer waiting to be
    compared against a right one.  `docs/theory.md` 13.10 and the module's own
    rule: two reports are comparable only when their baselines match.
    """

    def setUp(self) -> None:
        self.ctx = at.build_context(self.Y, FREQS, spec(), MARKER,
                                    baseline=POLICY)
        self.dec = at.decompose(self.ctx, "vic", "agg", "M")

    def test_the_context_carries_the_gauge_sentence_and_the_block_map(self):
        """
        Mutation: leaving `ctx.baseline_note` empty -- the sentence survives in
        `notes`, so a test that only looked there would still pass while the
        report header lost it.
        """
        self.assertIn("GAUGE CHANGE", self.ctx.baseline_note)
        self.assertIn(at.COMPOSED_BASELINE_TEXT, self.ctx.baseline_note)
        self.assertIn("F1: ports 1-6", self.ctx.baseline_note)
        self.assertIn("F2: ports 7-12", self.ctx.baseline_note)

    def test_it_is_a_HEADER_line_of_the_printed_report_not_a_trailing_note(self):
        """
        It decides what every number under it means, so it goes above the
        totals.  Measured: line index 1, immediately under the title.

        Mutation: appending it to `dec.notes` instead -- it prints as one more
        `note:` at the bottom, after the table it qualifies.
        """
        lines = at.format_decomposition(self.dec)
        self.assertTrue(lines[0].startswith("Attribution of M"))
        self.assertTrue(lines[1].strip().startswith("baseline:"), lines[:4])
        self.assertIn("GAUGE CHANGE", lines[1])
        head = next(i for i, l in enumerate(lines) if "total (sum of terms)" in l)
        self.assertLess(1, head)

    def test_a_STRUCTURE_fold_and_a_RANK_fold_are_two_different_sentences(self):
        """
        `folded` means two things and the report must not blur them.  The
        capless variant produces BOTH at once: the links go in as structure and
        `ground port 12` goes in because the baseline has no reference without
        it.  Measured: two distinct `Port(s) ...` notes, residual 1.593e-15.

        Mutation: building the rank note from `folded` rather than from
        `folded - structural` -- it then claims the two links were folded
        because the structure has no reference without them, which is false and
        is the sentence a reader would act on.
        """
        Yc = combined_Y(pad_caps=False)
        ctx = at.build_context(Yc, FREQS, spec(), MARKER, baseline=POLICY)
        struct = [n for n in ctx.notes if "as STRUCTURE" in n]
        rank = [n for n in ctx.notes if "no reference without them" in n]
        self.assertEqual(len(struct), 1, ctx.notes)
        self.assertEqual(len(rank), 1, ctx.notes)
        self.assertIn("short 5-7", struct[0])
        self.assertNotIn("short 5-7", rank[0])
        self.assertIn("ground port 12", rank[0])
        self.assertNotIn("ground port 12", struct[0])

    def test_the_return_path_note_carries_BOTH_baseline_sentences(self):
        """
        `decompose` appends every `Port(s) ...` note to the return-budget note,
        because a folded element is still a declared return path.  With two
        such notes in force it must carry both.

        Mutation: `next(...)` instead of `" ".join(...)` -- the historical
        code, which is byte-identical while only one note can exist and drops
        the rank sentence the moment the gauge adds a second.
        """
        Yc = combined_Y(pad_caps=False)
        ctx = at.build_context(Yc, FREQS, spec(), MARKER, baseline=POLICY)
        dec = at.decompose(ctx, "vic", "agg", "M")
        self.assertIn("as STRUCTURE", dec.reference_note)
        self.assertIn("no reference without them", dec.reference_note)

    def test_COMPOSED_BASELINE_TEXT_is_ONE_string_every_surface_reuses(self):
        """
        The `SIGN_CONVENTION_TEXT` rule: one string, so an export cannot
        paraphrase it.  It must reach the context note, the decomposition's
        header field and the cold-start notes verbatim.

        Mutation: re-wording it at any one of the three call sites.
        """
        self.assertIn(at.COMPOSED_BASELINE_TEXT, self.dec.baseline_gauge)
        csc = at.cold_start_context(self.Y, FREQS, spec(), MARKER,
                                    baseline=POLICY)
        self.assertTrue(any(at.COMPOSED_BASELINE_TEXT in n for n in csc.notes),
                        csc.notes)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

class TestBaselineLinksRefusals(_Base):
    """
    Every refusal here exists because the quiet alternative -- doing nothing --
    leaves the far file's elements reading exactly zero again, i.e. leaves the
    caller with the bug they asked to be rid of and no way to tell.
    """

    def test_a_link_that_the_spec_does_not_declare_is_refused_BY_NAME(self):
        """
        Mutation: silently ignoring an unmatched pair.  The gauge then applies
        to nothing, `structural` is empty, the numbers are the broken ones and
        the report says nothing at all.
        """
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(self.Y, FREQS, spec(), MARKER,
                             baseline=at.BaselineLinks(links=((4, 9),)))
        msg = str(cm.exception)
        self.assertIn("(5, 10)", msg)
        self.assertIn("not declared", msg)
        self.assertIn("(5, 7)", msg)        # it lists what IS declared

    def test_a_port_outside_the_network_is_refused_BY_NAME(self):
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(self.Y, FREQS, spec(), MARKER,
                             baseline=at.BaselineLinks(links=((4, 99),)))
        self.assertIn("100", str(cm.exception))
        self.assertIn("outside 1..12", str(cm.exception))

    def test_a_block_map_of_the_wrong_size_is_refused_BY_NAME(self):
        """
        The commonest composed-network mistake: a block map built from one
        file's port count.  It would silently mark the wrong links structural.

        Mutation: dropping the length check -- `PortBlocks.block_of` then
        raises deep inside `selects` with a message about a port index.
        """
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(self.Y, FREQS, spec(), MARKER,
                             baseline=at.BaselineLinks(
                                 blocks=at.PortBlocks.from_sizes([6, 5])))
        self.assertIn("11 port(s)", str(cm.exception))
        self.assertIn("this Y has 12", str(cm.exception))

    def test_a_link_from_a_port_to_itself_is_refused(self):
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(self.Y, FREQS, spec(), MARKER,
                             baseline=at.BaselineLinks(links=((4, 4),)))
        self.assertIn("port to itself", str(cm.exception))

    def test_something_that_is_not_a_BaselineLinks_is_refused(self):
        with self.assertRaises(at.AttribError) as cm:
            at.build_context(self.Y, FREQS, spec(), MARKER,
                             baseline=[(4, 6)])
        self.assertIn("BaselineLinks", str(cm.exception))

    def test_a_one_port_termination_can_never_be_structural(self):
        """
        `ground` / `vdd` / `lumped_to_gnd` have one port, so they can neither
        cross a file boundary nor be a link.  Putting one in the baseline would
        be the much larger claim that the TERMINATION is part of the structure.

        Mutation: `selects` accepting any length -- a ground on a package ball
        would be absorbed and would then have no term at all, which is the
        opposite of the point.
        """
        self.assertFalse(POLICY.selects((10,)))
        self.assertFalse(POLICY.selects(()))
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER, baseline=POLICY)
        self.assertTrue(all(len(e.ports) == 2 for e in ctx.structural))


# ---------------------------------------------------------------------------
# PortBlocks
# ---------------------------------------------------------------------------

class TestPortBlocks(unittest.TestCase):

    def test_from_sizes_lays_the_blocks_out_in_stacking_order(self):
        b = at.PortBlocks.from_sizes([6, 6])
        self.assertEqual(b.n_ports, 12)
        self.assertEqual(b.n_blocks, 2)
        self.assertEqual([b.label_of(p) for p in (0, 5, 6, 11)],
                         ["F1", "F1", "F2", "F2"])

    def test_describe_carries_the_FILE_LOCAL_index_and_the_global_one(self):
        """
        "port 305 has no return path" is unactionable on a 316-port combined
        network (R2-4).  Both halves are needed: the local index is what the
        user's own file calls it, the global one is what every message and
        every port field in this repo uses.

        Mutation: printing only the local index -- the number no longer matches
        anything the caller can type into a port cell.
        """
        b = at.PortBlocks.from_sizes([6, 6])
        self.assertEqual(b.describe(0), "F1.1 (port 1)")
        self.assertEqual(b.describe(6), "F2.1 (port 7)")
        self.assertEqual(b.describe(11), "F2.6 (port 12)")

    def test_the_local_index_is_COUNTED_not_subtracted(self):
        """
        `port0 - block_of_port.index(bi)` is right only while a block is a
        contiguous run.  `from_sizes` and `block_diag` guarantee that; a
        hand-built map does not, and a silently wrong file-local index on the
        one input nobody checks is the bug the label exists to prevent.

        Mutation: the subtraction -- port 4 of an interleaved map reads
        "B.4 (port 5)" instead of "B.2 (port 5)".
        """
        b = at.PortBlocks(block_of_port=(0, 1, 0, 1), labels=("A", "B"))
        self.assertEqual([b.describe(p) for p in range(4)],
                         ["A.1 (port 1)", "B.1 (port 2)",
                          "A.2 (port 3)", "B.2 (port 4)"])

    def test_describe_port_degrades_to_the_bare_index_with_no_block_map(self):
        """
        R2-4: every warning names the file.  A policy built from explicit links
        alone has no map to name it with, so the label degrades to what every
        other message in this repo says rather than inventing a tag.
        """
        self.assertEqual(at.BaselineLinks(links=((0, 6),)).describe_port(6),
                         "port 7")
        self.assertEqual(
            at.BaselineLinks(blocks=at.PortBlocks.from_sizes([6, 6])
                             ).describe_port(6), "F2.1 (port 7)")
        # out of the map's range: the bare index, never an exception
        self.assertEqual(
            at.BaselineLinks(blocks=at.PortBlocks.from_sizes([6, 6])
                             ).describe_port(99), "port 100")

    def test_crosses_is_what_selects_a_cross_file_link(self):
        b = at.PortBlocks.from_sizes([6, 6])
        self.assertTrue(b.crosses((4, 6)))
        self.assertFalse(b.crosses((4, 5)))
        self.assertFalse(b.crosses((6, 11)))
        self.assertFalse(b.crosses((3,)))

    def test_the_separator_is_a_DOT_because_a_colon_is_the_range_separator(self):
        """
        MEASURED, and it is why the tag looks the way it does:
        `parse_port_range("PKG:12")` RAISES "Range must be start:step:stop" --
        `:` is already taken in every port field in this repo.  A dotted tag
        parses as a name, not as a broken range.

        Mutation: using `:` in `describe()` -- nothing in this module would
        notice, and the first user to paste the label into a port cell gets a
        parse error about a range they did not write.
        """
        with self.assertRaises(ValueError) as cm:
            parse_port_range("PKG:12")
        self.assertIn("start:step:stop", str(cm.exception))
        b = at.PortBlocks.from_sizes([6, 6])
        tag = b.describe(6).split(" (port")[0]
        self.assertEqual(tag, "F2.1")
        self.assertNotIn(":", tag)
        # and the half of the label a user WOULD type into a port cell -- the
        # global index -- parses as itself (parse_port_range is 1-based in and
        # 1-based out; the 0-based conversion happens in the builders).
        self.assertEqual(parse_port_range("7"), [7])

    def test_summary_collapses_runs_so_a_316_port_map_is_one_line(self):
        b = at.PortBlocks.from_sizes([16, 300], labels=["EM", "PKG"])
        self.assertEqual(b.summary(), "EM: ports 1-16; PKG: ports 17-316")

    def test_a_label_count_that_does_not_match_is_refused(self):
        with self.assertRaises(at.AttribError):
            at.PortBlocks.from_sizes([6, 6], labels=["only one"])


# ---------------------------------------------------------------------------
# The cold-start screen
# ---------------------------------------------------------------------------

class TestColdStartWithoutTheGauge(_Base):
    """
    The screen rewrites the spec to "probes plus one ideal ground per
    candidate", and that rewrite DROPS the cross-file links along with every
    other decision.  On a composed network the result is the two files as
    islands: the far file cannot move the answer, so every one of its ports
    reads a delta of exactly zero -- and, because a zero IS an answer, reads it
    with `defined = True`.
    """

    def test_every_package_pin_reports_delta_0_with_defined_True(self):
        """
        This is the exact claim R2-8 names, pinned as a claim about the CURRENT
        behaviour so the with-gauge test below cannot pass vacuously.  All six
        package ports (7-12), including the two ground balls that are worth
        3.74 dB, come back `delta == 0` and `defined is True`.
        """
        cs = at.cold_start_report(self.Y, FREQS, spec(), "vic", "agg",
                                  MARKER, "M")
        rows = {r.port: r for r in cs.screen}
        for p in range(6, 12):
            self.assertIn(p, rows, f"port {p + 1} missing from the screen")
            self.assertTrue(rows[p].defined, f"port {p + 1}")
            self.assertEqual(rows[p].delta, 0, f"port {p + 1}")
            self.assertEqual(rows[p].delta.real, 0.0)

    def test_and_the_two_EM_taps_are_the_only_ports_that_move_anything(self):
        """
        The mirror of the above: the screen is not simply dead, it confidently
        reports that only ports 5 and 6 matter.  Measured 42.30960294 fH and
        25.41805777 fH -- both three orders of magnitude below what the same
        two ports read once the package is in the baseline.
        """
        cs = at.cold_start_report(self.Y, FREQS, spec(), "vic", "agg",
                                  MARKER, "M")
        rows = {r.port: r for r in cs.screen}
        self.assertAlmostEqual(rows[4].delta.real, 4.230960294e-11, delta=1e-20)
        self.assertAlmostEqual(rows[5].delta.real, 2.541805777e-11, delta=1e-20)


class TestColdStartWithTheGauge(_Base):
    """
    With the policy the links stay in the rewritten spec AND go into the
    baseline, so the screen measures from "the files connected, everything else
    open" and every package pin gets a real number.
    """

    def setUp(self) -> None:
        self.cs = at.cold_start_report(self.Y, FREQS, spec(), "vic", "agg",
                                       MARKER, "M", baseline=POLICY)
        self.rows = {r.port: r for r in self.cs.screen}

    def test_the_package_ground_balls_are_no_longer_zero(self):
        """
        The headline of R2-8's second half.  Measured 79.587468915 pH (port 11)
        and 54.204607285 pH (port 12), against exactly 0.0 without the policy.

        Mutation: dropping the structural couplings from `keep_cpl` (the
        historical behaviour) -- the deltas go back to exactly zero.
        """
        for p in (10, 11):
            self.assertTrue(self.rows[p].defined)
            self.assertNotEqual(self.rows[p].delta, 0)
        self.assertAlmostEqual(self.rows[10].delta.real, 7.9587468915e-11,
                               delta=1e-20)
        self.assertAlmostEqual(self.rows[11].delta.real, 5.4204607285e-11,
                               delta=1e-20)

    def test_every_delta_agrees_with_an_HONEST_re_solve_through_the_engine(self):
        """
        The rule the whole cold-start suite is built on, applied to the new
        baseline: a Woodbury update that agrees with itself and with nothing
        else is this module's characteristic failure.  Each screened port is
        re-solved through `compute_z_matrix` with a rebuilt `TerminationSet`
        that carries the links and that ONE ground.  Measured agreement is
        exact to the printed 10 digits on both balls.

        Mutation: folding the links into the baseline but NOT keeping them in
        the rewritten spec -- the screen's own numbers stay self-consistent and
        the engine disagrees with every one of them.
        """
        m_base = engine_M(self.Y, spec(grounds=()))
        for p, row in sorted(self.rows.items()):
            if not row.defined:
                continue
            got = engine_M(self.Y, spec(grounds=(p,))) - m_base
            self.assertLess(abs(row.delta.real - got),
                            1e-10 * max(abs(got), 1e-13),
                            f"port {p + 1}: screen {row.delta.real!r} vs "
                            f"engine {got!r}")

    def test_the_far_end_of_each_link_is_ONE_node_and_says_so(self):
        """
        Ports 5 and 7 are the same node once the link is in the baseline, so
        grounding either is the same hypothesis.  The far end is kept ON the
        screen -- a table of "which ports matter" must not silently omit a port
        -- with `defined = False` and a note naming the row that carries the
        number.

        Mutation: removing the dedup -- both ends get a ground element, `U`
        becomes rank-deficient, the context reports a REDUNDANT spec that the
        screen invented itself, and two rows carry the identical delta for one
        node.
        """
        for near, far in ((4, 6), (5, 7)):
            self.assertTrue(self.rows[near].defined)
            self.assertFalse(self.rows[far].defined)
            self.assertIn(f"port {near + 1}", self.rows[far].note)
            self.assertIn("same NODE", self.rows[far].note)
        ctx = at.build_context(self.Y, FREQS, spec(), MARKER, baseline=POLICY)
        self.assertFalse([n for n in ctx.notes if "REDUNDANT" in n], ctx.notes)

    def test_the_bracket_reports_the_composed_baseline_not_all_open(self):
        """
        The low end of the bracket is "the files connected, everything else
        open" -- 1.0837047531 nH, which is the engine's answer with the balls
        removed -- and the printed label must say so.  Under the all-open
        heading the same number reads as "the package is irrelevant", which is
        precisely the claim this gauge exists to stop being made by accident.

        Measured: -8.4653 dB of span with the gauge against -15.1903 dB
        without it, from two different low ends.

        Mutation: dropping the `composed_note` branch in `format_cold_start`
        -- the label reverts to "every non-probe port OPEN" over a number that
        is not that network's.
        """
        br = self.cs.bracket
        self.assertAlmostEqual(br.value_open.real, 1.0837047531e-09,
                               delta=1e-19)
        self.assertAlmostEqual(br.span_db, -8.4653, places=3)
        self.assertIn("files CONNECTED", br.baseline_note)
        # and it names WHICH links, which is the half a caller can act on.
        # Mutation: `baseline_description()` returning `base` alone -- "files
        # CONNECTED" survives in the generic half and only this line notices.
        self.assertIn("5-7", br.baseline_note)
        self.assertIn("6-8", br.baseline_note)
        lines = at.format_cold_start(self.cs)
        lo = [l for l in lines if "files LINKED" in l]
        self.assertEqual(len(lo), 1, lines[:20])
        self.assertFalse(any("every non-probe port OPEN " in l for l in lines))

    def test_the_report_header_names_the_baseline_every_number_came_from(self):
        lines = at.format_cold_start(self.cs)
        head = [l for l in lines if l.startswith("  Every number below")]
        self.assertEqual(len(head), 1)
        self.assertIn("files CONNECTED", head[0])


# ---------------------------------------------------------------------------
# Nothing moves for a single file
# ---------------------------------------------------------------------------

class TestNothingMovesWithoutAPolicy(unittest.TestCase):
    """
    `baseline=` defaults to None, and with it absent every existing caller must
    get the bytes it got before.  This is the guard on that promise.
    """

    @classmethod
    def setUpClass(cls) -> None:
        d = parse_touchstone(str(FIXTURES / "diff_pair_4port.s4p"))
        cls.d = d
        cls.Yf = s_to_y(d.s, d.z0)
        cls.terms = TerminationSet(
            per_port={0: Signal("vic", +1), 1: Signal("agg", +1),
                      2: Ground(), 3: Ground()},
            couplings=[])
        cls.f = 5.0e9

    def test_a_single_file_decomposition_is_byte_identical(self):
        """
        Mutation: making `baseline=None` mean "blocks of one port each" -- the
        whole repo's attribution output changes and the rest of the attrib
        suite goes red, but a test that only exercised composed networks would
        not notice.
        """
        a = at.format_decomposition(at.decompose(
            at.build_context(self.Yf, self.d.freqs, self.terms, self.f),
            "vic", "agg", "M"))
        b = at.format_decomposition(at.decompose(
            at.build_context(self.Yf, self.d.freqs, self.terms, self.f,
                             baseline=None),
            "vic", "agg", "M"))
        self.assertEqual(a, b)
        self.assertFalse(any(l.strip().startswith("baseline:") for l in a), a)

    def test_a_connected_file_never_trips_the_island_warning(self):
        """
        MEASURED, not assumed: the detector was fuzzed over 2993 random specs
        across every fixture in this repo (two probes plus a random mix of
        grounds, lumped-to-gnd, shorts and lumped-between on the rest), and the
        ONLY file it ever fires on is `decap_4port.s4p`, which is genuinely two
        uncoupled pi networks.

        Mutation: dropping the `sup & probe_comps` intersection so that any
        multi-component baseline flags everything -- this goes red on the first
        fixture with a shunt-only port.
        """
        ctx = at.build_context(self.Yf, self.d.freqs, self.terms, self.f)
        self.assertEqual(at._island_elements(ctx.Ybase, ctx.U, ctx.W)[1], 1)
        self.assertFalse([w for w in ctx.warnings if "EXACTLY ZERO" in w])

    def test_a_disconnected_baseline_alone_is_NOT_enough_to_warn(self):
        """
        THE FALSE-ALARM GUARD, and the reason the criterion is "a part that
        carries NO measurement port" rather than "more than one part".

        `decap_4port.s4p` with one probe on each pi network is a TWO-COMPONENT
        baseline in which every element is reachable from a probe -- it is the
        configuration `test_attrib_cli.py`'s exactly-zero-mutual test drives,
        i.e. a shipped, documented, correct use.  Nothing may be warned about
        here.

        Mutation: dropping the `sup & probe_comps` intersection so that any
        multi-component baseline flags all its elements.  Every fixture in the
        repo has ONE component, so no other test in this file or anywhere else
        would notice; this is the only one that does.
        """
        d = parse_touchstone(str(FIXTURES / "decap_4port.s4p"))
        Y = s_to_y(d.s, d.z0)
        terms = TerminationSet(
            per_port={0: Signal("s", +1), 2: Signal("c", +1),
                      1: Ground(), 3: Ground()},
            couplings=[])
        ctx = at.build_context(Y, d.freqs, terms, 5.0e9)
        self.assertEqual(at._island_elements(ctx.Ybase, ctx.U, ctx.W),
                         ([], 2), "two components, both with a probe")
        self.assertFalse([w for w in ctx.warnings if "EXACTLY ZERO" in w],
                         ctx.warnings)

    def test_a_genuinely_disconnected_FILE_is_a_true_positive(self):
        """
        `decap_4port.s4p` is two uncoupled pi networks by construction, so with
        both probes on one of them a ground on the other really does contribute
        exactly zero.  The warning is right there too, and it says both things:
        "if this is a composed network, pass baseline=", and "if it is one
        file, the file itself says these parts are not connected".

        Mutation: wording it as a composed-network warning only -- it would
        then be crying wolf on the one fixture where it fires legitimately.
        """
        d = parse_touchstone(str(FIXTURES / "decap_4port.s4p"))
        Y = s_to_y(d.s, d.z0)
        terms = TerminationSet(
            per_port={0: Signal("vic", +1), 1: Signal("agg", +1),
                      2: Ground(), 3: Ground()},
            couplings=[])
        ctx = at.build_context(Y, d.freqs, terms, 5.0e9)
        hit = [w for w in ctx.warnings if "EXACTLY ZERO" in w]
        self.assertEqual(len(hit), 1, ctx.warnings)
        self.assertIn("the file itself says these parts are not connected",
                      hit[0])
        dec = at.decompose(ctx, "vic", "agg", "Z")
        named = {t.label: t for t in dec.terms if t.element is not None}
        for lab in ("ground port 3", "ground port 4"):
            if lab in named:
                self.assertEqual(named[lab].contribution, 0)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
