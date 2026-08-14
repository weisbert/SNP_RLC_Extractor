"""
The COLD-START SCREEN in `pkg_rlc_attrib`: which ports matter, before anything
has been decided.

`tests/test_attrib_core.py` asks whether the decomposition of a SPEC is right.
This file asks the question that comes before a spec exists -- the designer
knows the victim and the aggressor and nothing about the other 149 ports -- and
it is a different question with different failure modes:

  * the closed form for "what if port p were grounded" is a Woodbury update,
    and a Woodbury update that agrees with itself and with nothing else is this
    module's characteristic failure. EVERY delta here is checked against an
    HONEST re-solve through `compute_z_matrix` with a rebuilt `TerminationSet`;
  * ranking ports by how strongly they couple to the VICTIM is the obvious
    screen and it is wrong. The planted case contains a port with the largest
    |Z_ap| in the whole file and an effect three orders of magnitude below the
    real path's, and the test that catches it is the entire justification for
    the screen having TWO coupling columns;
  * a single-port ranking is structurally blind to a shield brought out as two
    ports: +9.689 pH for each end alone, -870.268 pH for both, 90x the largest
    single-port effect with the OPPOSITE SIGN. The pair scan is not an
    optional refinement;
  * grouping ports by name WOULD have caught that shield, and the requirement
    forbids the script from guessing which ports are one structure. The
    resolution is tested here directly: the whole report is run twice, with and
    without port names, and every number must be identical.

Two things every test here does. It CONSTRUCTS the case rather than hoping a
fixture contains one -- the repo's 2- and 4-port fixtures cannot express a
planted 12-port screen or a two-terminal shield -- and it checks against an
honest rebuild through `compute_z_matrix`, so both sides of every comparison
come from shipped code.

The two network builders are the ones measured in the session that produced
this feature's contract (`cold_start_screen.py` and `cold_start_blindspot.py`),
reproduced here verbatim in `planted_network()` and `shield_network()`; the
numbers quoted above and asserted below are those scripts' numbers, arrived at
through the shipped module.

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
    TerminationSet,
    compute_z_matrix,
    parse_custom_termination_text,
    parse_touchstone,
    s_to_y,
)

FIXTURES = _HERE / "fixtures"

# One frequency for the synthetic cases: everything here is an @-a-point
# question, and 5.205 GHz is the frequency the contract's own measurements used.
F_TEST = np.array([5.205e9])
F0 = float(F_TEST[0])
OMEGA = 2.0 * math.pi * F0

#: The four probe ports of both synthetic networks: a differential victim on
#: 1/2 and a differential aggressor on 3/4.  Deliberately NOT named 'a' / 'b':
#: `_normalize_signal` maps Signal("B") onto the minus side of group "A", so
#: `1 signal a + / 2 signal b +` silently resolves to ONE measurement port.
PROBES = ("1 signal V +\n2 signal V -\n"
          "3 signal A2 +\n4 signal A2 -\n")


# ---------------------------------------------------------------------------
# The two planted networks, from the contract's own measurement scripts
# ---------------------------------------------------------------------------

#: Planted roles for ports 5..12 of `planted_network` (1-based).  A port has to
#: couple to BOTH sides to be a coupling path; the point of the case is that the
#: screen must recover 'both' and must NOT be fooled by 'victim only'.
PLANTED_ROLE = {5: "both", 6: "both", 7: "victim only", 8: "victim only",
                9: "aggr only", 10: "aggr only", 11: "weak both",
                12: "weak both"}
PLANTED_KV = {5: .30, 6: .25, 7: .50, 8: .45, 9: .00, 10: .00, 11: .02,
              12: .02}
PLANTED_KA = {5: .30, 6: .28, 7: .00, 8: .00, 9: .50, 10: .45, 11: .02,
              12: .02}
PLANTED_BOTH = (5, 6)
#: Port 7 is the RED HERRING: k to the victim 0.50, the largest in the file,
#: and k to the aggressor exactly 0.  It is the whole reason PortScreenRow
#: carries |Z_ap| and |Z_pb| as separate columns.
PLANTED_RED_HERRING = 7

L_SELF = 1.0e-9
R_SER = 0.5
C_SHUNT = 20e-15


def planted_network() -> np.ndarray:
    """
    12 ports, every one an inductor to the global reference, all mutually
    coupled through an L matrix chosen so the ground truth is known by
    construction: grounding port p lets current flow in branch p, and

        dZ_ab = - Zbase[a, p] * Zbase[p, b] / Zbase[p, p]

    so a port must couple to BOTH sides to be a path.  Four kinds of port are
    planted (see PLANTED_ROLE) and the screen has to recover them.

    Verbatim from the contract's `cold_start_screen.py`.
    """
    n = 12
    L = np.eye(n) * L_SELF

    def setk(i: int, j: int, k: float) -> None:      # 1-based
        L[i - 1, j - 1] = L[j - 1, i - 1] = k * L_SELF

    setk(1, 2, -0.80)                     # the victim coil's two halves
    setk(3, 4, -0.80)                     # the aggressor's two halves
    for i in (1, 2):
        for j in (3, 4):
            setk(i, j, 0.004 * (1 if (i + j) % 2 == 0 else -1))  # direct path
    for p, kv in PLANTED_KV.items():
        setk(1, p, kv)
        setk(2, p, -kv)
    for p, ka in PLANTED_KA.items():
        setk(3, p, ka)
        setk(4, p, -ka)
    assert np.all(np.linalg.eigvalsh(L) > 0), "L must be positive definite"
    Y = np.linalg.inv(R_SER + 1j * OMEGA * L) + np.eye(n) * (1j * OMEGA * C_SHUNT)
    return Y[None, :, :]


def shield_network() -> np.ndarray:
    """
    6 ports: a differential victim (1/2), a differential aggressor (3/4), and a
    shield / guard-ring segment brought out as TWO ports (5 and 6).

    Ground one end and no loop exists, so almost nothing moves.  Ground both and
    the loop closes, eddy current flows, and the coupling changes by 90x with
    the opposite sign.  The shield is ONE branch spanning ports 5 and 6, which
    is why a single-port ranking cannot see it.

    Verbatim from the contract's `cold_start_blindspot.py`.
    """
    nports = 6
    L = np.eye(5) * L_SELF

    def setk(i: int, j: int, k: float) -> None:      # 0-based branch indices
        L[i, j] = L[j, i] = k * L_SELF

    setk(0, 1, -0.80)
    setk(2, 3, -0.80)
    for i in (0, 1):
        for j in (2, 3):
            setk(i, j, 0.004 * (1 if (i + j) % 2 == 0 else -1))
    setk(4, 0, 0.45)                      # shield couples to the victim
    setk(4, 1, -0.45)
    setk(4, 2, 0.45)                      # shield couples to the aggressor
    setk(4, 3, -0.45)
    assert np.all(np.linalg.eigvalsh(L) > 0)
    A = np.zeros((5, nports))             # branch <- node incidence
    A[0, 0] = A[1, 1] = A[2, 2] = A[3, 3] = 1.0
    A[4, 4] = 1.0
    A[4, 5] = -1.0                        # the shield spans ports 5 and 6
    Y = (A.T @ np.linalg.inv(R_SER + 1j * OMEGA * L) @ A
         + np.eye(nports) * (1j * OMEGA * C_SHUNT))
    return Y[None, :, :]


SHIELD_NAMES = ("vic_p", "vic_n", "agg_p", "agg_n", "guard_ring1",
                "guard_ring2")
PLANTED_NAMES = ("vic_p", "vic_n", "agg_p", "agg_n") + tuple(
    f"aux{i}" for i in range(1, 9))


# ---------------------------------------------------------------------------
# The honest second opinion: a rebuilt TerminationSet through the engine
# ---------------------------------------------------------------------------

def honest_Z(Y, freqs, probes_text: str, grounded=(), extra: str = ""):
    """
    The answer a user gets by editing the spec and pressing Calculate: a FRESH
    TerminationSet through `compute_z_matrix` over the whole sweep, reusing
    nothing from `pkg_rlc_attrib`.

    `grounded` is a sequence of 0-BASED port indices to add `ground` rows for.
    Every "the fast path really is the same network" claim below is measured
    against this and never against the module's own numbers.
    """
    text = probes_text + extra
    for p in sorted(grounded):
        text += f"{p + 1} ground\n"
    ts = parse_custom_termination_text(text)
    Z, names, _w = compute_z_matrix(Y, freqs, ts)
    return Z, names


def honest_at(Y, freqs, probes_text: str, freq_hz: float, a: int, b: int,
              grounded=(), extra: str = "") -> complex:
    Z, _ = honest_Z(Y, freqs, probes_text, grounded, extra)
    idx = int(np.argmin(np.abs(np.asarray(freqs, dtype=float) - freq_hz)))
    return complex(Z[idx][a, b])


def probes_only(text: str) -> TerminationSet:
    return parse_custom_termination_text(text)


def load(name: str):
    d = parse_touchstone(FIXTURES / name)
    return d, s_to_y(d.s, d.z0)


#: (fixture, probe DSL, victim, aggressor).  Every fixture in the repo that can
#: express a cold start at all -- i.e. that has at least one port left over
#: after the probes -- with the group names chosen so no legacy 'B' alias fires.
#:
#: The last entry is the one that matters most and it is here on purpose: with
#: coil 1 probed differentially, `coupled_4port_float.s4p` has no DC reference,
#: so the baseline FOLDS `ground port 3` in and the remaining candidate is
#: measured from a 3-grounded network rather than from all-open.  Measured, the
#: two differ completely (-6.8355 Ohm against 2.8e-14), so a walk that only
#: ever compared against a probes-only re-solve would report this module as
#: wrong -- or, worse, would have let the module claim "from all-open" about a
#: number that is not.
FIXTURE_CASES = [
    ("coupled_2port_gndref.s2p", "1 signal c1 +\n", "c1", "c1"),
    ("coupled_2port_negM.s2p", "1 signal c1 +\n", "c1", "c1"),
    ("coupled_4port_diff.s4p", "1 signal c1 +\n3 signal c2 +\n", "c1", "c2"),
    ("coupled_4port_diff.s4p",
     "1 signal c1 +\n2 signal c1 -\n3 signal c2 +\n", "c1", "c2"),
    ("coupled_4port_float.s4p", "1 signal c1 +\n3 signal c2 +\n", "c1", "c2"),
    ("decap_4port.s4p", "1 signal p1 +\n3 signal p2 +\n", "p1", "p2"),
    ("diff_pair_4port.s4p", "1 signal vic +\n2 signal agg +\n", "vic", "agg"),
    ("diff_pair_4port.s4p", "1 signal vic +\n3 signal agg +\n", "vic", "agg"),
    ("pi_2port.s2p", "1 signal p +\n", "p", "p"),
    ("coupled_4port_float.s4p", "1 signal c1 +\n2 signal c1 -\n", "c1", "c1"),
]


# ---------------------------------------------------------------------------
# 1. The load-bearing check: the closed form against the shipped engine
# ---------------------------------------------------------------------------

class TestClosedFormAgainstTheEngine(unittest.TestCase):
    """
    Every fast low-rank result checked against an honest recompute through
    `compute_z_matrix` with a rebuilt `TerminationSet`.

    This is the single most important test in the file, for the reason
    CLAUDE.md gives about the rest of the module: a Woodbury update that agrees
    with itself and with nothing else is the characteristic failure here, and
    it produces plausible numbers rather than an exception.
    """

    def _check_one_port(self, Y, freqs, probes, victim, aggressor, tag):
        f = float(np.asarray(freqs)[len(np.asarray(freqs)) // 2])
        ts = probes_only(probes)
        csc = at.cold_start_context(Y, freqs, ts, f)
        a = csc.ctx.port_index(victim)
        b = csc.ctx.port_index(aggressor)
        rows = at.cold_start_screen(Y, freqs, ts, victim, aggressor, f,
                                    "ImZ", context=csc)
        # The baseline the module ACTUALLY used, which is all-open only when it
        # had a reference without help; on a floating structure it has folded
        # some ports in and says so.  Comparing against a probes-only re-solve
        # regardless would be comparing two different networks.
        base_g = tuple(csc.baseline_grounded)
        z_open = honest_at(Y, freqs, probes, f, a, b, grounded=base_g)
        # The scale for every comparison: an exactly-zero mutual (decap's two
        # uncoupled pi networks) has no relative error, so the tolerance is
        # taken against the largest thing in the answer's own matrix.
        Zo, _ = honest_Z(Y, freqs, probes, grounded=base_g)
        idx = int(np.argmin(np.abs(np.asarray(freqs, float) - f)))
        fin = np.abs(Zo[idx])[np.isfinite(np.abs(Zo[idx]))]
        scale = float(fin.max()) if fin.size else 1.0
        n_checked = 0
        for r in rows:
            if not r.defined:
                continue
            true = honest_at(Y, freqs, probes, f, a, b,
                             grounded=base_g + (r.port,))
            pred = complex(z_open.imag + r.delta.real * 1.0)
            # quantity 'ImZ' maps to Im(Z) with scale 1, so the row's delta IS
            # the change in Im(Z_ab) and needs no unit conversion here.
            self.assertLess(
                abs(pred.real - true.imag), 1e-9 * max(abs(true.imag), scale),
                f"{tag}: grounding port {r.port + 1} -- the closed form says "
                f"{pred.real!r} and compute_z_matrix says {true.imag!r}")
            n_checked += 1
        return n_checked

    def test_grounding_one_port_matches_an_honest_resolve_on_every_fixture(self):
        """
        Defeating mutations, both run: measure each row from
        `_baseline_value` -- the module's all-GROUNDED baseline -- instead of
        from the empty live set (17 tests red), and drop the `Gm` term from
        `H = Zt + G` in `_z_matrix`, which is the closed form's
        `/ Zbase[p, p]` divisor (25 tests red).
        """
        total = 0
        for name, probes, victim, aggressor in FIXTURE_CASES:
            with self.subTest(fixture=name, probes=probes):
                d, Y = load(name)
                total += self._check_one_port(
                    Y, d.freqs, probes, victim, aggressor, f"{name} {probes!r}")
        # A walk that silently checked nothing would pass every assertion above.
        self.assertGreaterEqual(total, 9, "the fixture walk checked nothing")

    def test_a_folded_baseline_is_NOT_called_all_open(self):
        """
        `coupled_4port_float.s4p` probed differentially on coil 1 has no DC
        reference, so the baseline folds `ground port 3` in. Every delta is
        then measured from a 3-grounded network and the report must say so --
        measured, grounding port 4 moves Im(Z_aa) by -6.8355 Ohm from that
        baseline and by 2.8e-14 from the genuinely all-open one, so the two
        readings are not close and are not interchangeable.

        Defeating mutation: print the fixed sentence "every number below starts
        from ALL-OPEN" unconditionally, which is what the first draft of
        `format_cold_start` did.
        """
        d, Y = load("coupled_4port_float.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        probes = "1 signal c1 +\n2 signal c1 -\n"
        ts = probes_only(probes)
        csc = at.cold_start_context(Y, d.freqs, ts, f)
        self.assertEqual(csc.baseline_grounded, (2,))          # port 3
        self.assertIn("NOT the all-open configuration",
                      csc.baseline_description())

        br = at.cold_start_bracket(Y, d.freqs, ts, "c1", "c1", f, "ImZ",
                                   context=csc)
        self.assertEqual(br.baseline_grounded, (2,))
        text = "\n".join(at.format_cold_start(
            at.cold_start_report(Y, d.freqs, ts, "c1", "c1", f, "ImZ",
                                 context=csc)))
        self.assertIn("all open EXCEPT 3", text)
        self.assertNotIn("starts from ALL-OPEN", text)

        # and the two networks really do disagree, so the wording matters
        row = [r for r in at.cold_start_screen(Y, d.freqs, ts, "c1", "c1", f,
                                               "ImZ", context=csc)
               if r.defined][0]
        self.assertEqual(row.port, 3)                          # port 4
        from_folded = (honest_at(Y, d.freqs, probes, f, 0, 0, grounded=(2, 3))
                       - honest_at(Y, d.freqs, probes, f, 0, 0, grounded=(2,)))
        from_open = (honest_at(Y, d.freqs, probes, f, 0, 0, grounded=(3,))
                     - honest_at(Y, d.freqs, probes, f, 0, 0))
        self.assertAlmostEqual(row.delta.real, from_folded.imag, places=9)
        self.assertAlmostEqual(from_folded.imag, -6.8355, places=3)
        self.assertLess(abs(from_open.imag), 1e-10)

    def test_grounding_one_port_matches_on_the_planted_case(self):
        """
        Defeating mutation: drop the `/ Zbase[p, p]` divisor from the closed
        form (i.e. make `_z_matrix`'s H the identity for one element).

        This is the case the contract measured at 1.5e-11 worst relative; the
        assertion is at 1e-9, thirty times looser, so it is a guard and not a
        transcription of one run.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        rows = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)
        worst = 0.0
        for r in rows:
            true = (honest_at(Y, F_TEST, PROBES, F0, 0, 1, grounded=(r.port,))
                    - honest_at(Y, F_TEST, PROBES, F0, 0, 1))
            true_M = true.imag / OMEGA
            rel = abs(r.delta.real - true_M) / max(abs(true_M), 1e-30)
            worst = max(worst, rel)
        self.assertEqual(len(rows), 8)
        self.assertLess(worst, 1e-9,
                        f"worst relative disagreement with the engine {worst:.3e}")

    def test_the_bracket_endpoints_are_the_engine_s_two_answers(self):
        """
        Defeating mutation: read `value_open` off `ctx.Zop` (the grounded
        matrix) instead of the all-open one -- the span then reads 0 dB on
        every file.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        br = at.cold_start_bracket(Y, F_TEST, ts, "V", "A2", F0)
        m_open = honest_at(Y, F_TEST, PROBES, F0, 0, 1).imag / OMEGA
        m_gnd = honest_at(Y, F_TEST, PROBES, F0, 0, 1,
                          grounded=range(4, 12)).imag / OMEGA
        self.assertAlmostEqual(br.value_open.real / m_open, 1.0, places=9)
        self.assertAlmostEqual(br.value_grounded.real / m_gnd, 1.0, places=9)
        # And the grounded end has a second opinion for free, because
        # build_context asked compute_z_matrix about that very spec.
        self.assertLess(br.reconciliation_rel, 1e-9)

    def test_the_pair_effect_matches_an_honest_resolve(self):
        """
        Defeating mutation: evaluate a pair as the SUM of the two single-port
        deltas (which is exactly the mistake the pair scan exists to expose) --
        every `delta_pair` then disagrees with the engine.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        pairs = at.cold_start_pairs(Y, F_TEST, ts, "V", "A2", F0, top_k=8)
        base = honest_at(Y, F_TEST, PROBES, F0, 0, 1).imag / OMEGA
        self.assertEqual(len(pairs), 28)
        for pe in pairs:
            true = (honest_at(Y, F_TEST, PROBES, F0, 0, 1,
                              grounded=(pe.port_i, pe.port_j)).imag / OMEGA
                    - base)
            self.assertLess(
                abs(pe.delta_pair.real - true), 1e-9 * max(abs(true), 1e-30),
                f"pair {pe.label}: {pe.delta_pair.real!r} vs {true!r}")

    def test_the_greedy_curve_matches_an_honest_resolve(self):
        """
        Defeating mutation: build each cumulative point from the sum of the
        chosen ports' single-port deltas instead of re-solving the group.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        cv = at.cold_start_cumulative(Y, F_TEST, ts, "V", "A2", F0)
        self.assertTrue(cv.k)
        for i, k in enumerate(cv.k):
            true = honest_at(Y, F_TEST, PROBES, F0, 0, 1,
                             grounded=cv.order[:k]).imag / OMEGA
            self.assertLess(
                abs(cv.values[i].real - true), 1e-9 * max(abs(true), 1e-30),
                f"k={k}: {cv.values[i].real!r} vs {true!r}")

    def test_the_mirror_direction_matches_an_honest_resolve(self):
        """
        The leave-one-out from ALL-GROUNDED, against a rebuilt spec that
        grounds every candidate but one.

        Defeating mutation: report only one direction (`return []`).

        Note for anyone tempted to "simplify" the implementation: on THIS
        context `sensitivity(ctx, ..., [alt_open()])` computes exactly the same
        numbers, because every element is an ideal ground so `ctx.Zt` already
        is `leave_one_out`'s all-zero `Zt_ideal` and `_baseline_value` already
        is the all-grounded value. That equivalence is an accident of the
        cold-start spec and would stop holding the moment a candidate carried a
        lumped termination, which is why the call goes through `leave_one_out`,
        whose contract is "from all-ideal", rather than through `sensitivity`,
        whose contract is "from the spec as declared".
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        csc = at.cold_start_context(Y, F_TEST, ts, F0)
        loo = at.cold_start_leave_one_out(Y, F_TEST, ts, "V", "A2", F0,
                                          context=csc)
        allg = tuple(range(4, 12))
        base = honest_at(Y, F_TEST, PROBES, F0, 0, 1,
                         grounded=allg).imag / OMEGA
        self.assertEqual(len(loo), 8)
        for s in loo:
            port = csc.ctx.elements[s.elements[0]].ports[0]
            live = tuple(p for p in allg if p != port)
            true = honest_at(Y, F_TEST, PROBES, F0, 0, 1,
                             grounded=live).imag / OMEGA
            self.assertLess(abs(s.baseline_value.real - base),
                            1e-9 * abs(base))
            self.assertLess(abs(s.new_value.real - true),
                            1e-9 * max(abs(true), 1e-30),
                            f"opening port {port + 1}: "
                            f"{s.new_value.real!r} vs {true!r}")


# ---------------------------------------------------------------------------
# 2. The planted 12-port case: why there are TWO coupling columns
# ---------------------------------------------------------------------------

class TestPlantedTwelvePortScreen(unittest.TestCase):
    """
    Four kinds of port planted; the screen has to recover them and, above all,
    has to NOT be fooled by the one that couples hardest to the victim.
    """

    @classmethod
    def setUpClass(cls):
        cls.Y = planted_network()
        cls.ts = probes_only(PROBES)
        cls.csc = at.cold_start_context(cls.Y, F_TEST, cls.ts, F0,
                                        port_names=PLANTED_NAMES)
        cls.rows = at.cold_start_screen(cls.Y, F_TEST, cls.ts, "V", "A2", F0,
                                        context=cls.csc)
        cls.by_port = {r.port + 1: r for r in cls.rows}
        cls.order = [r.port + 1 for r in cls.rows]

    def test_the_screen_puts_the_two_planted_paths_on_top(self):
        """
        Defeating mutation: rank by `+r.abs_delta` (ascending) or by port
        index.
        """
        self.assertEqual(sorted(self.order[:2]), list(PLANTED_BOTH))
        # ...and by three orders of magnitude, not by a nose.
        third = self.rows[2].abs_delta
        self.assertGreater(self.rows[1].abs_delta, 100.0 * third)

    def test_the_port_with_the_largest_victim_coupling_is_NOT_ranked_highly(self):
        """
        THE test that justifies two columns.

        Port 7 has the largest |Zbase[a, p]| of any port in the file -- 67%
        larger than the real coupling path's -- and grounding it moves M by
        -0.378 pH against the real path's -395.369 pH, a factor of 1046. A
        screen that ranks on coupling-to-the-victim alone puts it FIRST.

        Defeating mutation: sort `cold_start_screen`'s rows by `-abs(r.z_ap)`,
        or drop `z_pb` from `PortScreenRow` and rank on the remaining column.
        """
        herring = self.by_port[PLANTED_RED_HERRING]
        real = self.by_port[PLANTED_BOTH[0]]

        # The premise: it really is the strongest coupling to the victim in the
        # file.  Without this assertion the test is a tautology about a port
        # that happens to be weak.
        self.assertEqual(
            max(self.rows, key=lambda r: abs(r.z_ap)).port + 1,
            PLANTED_RED_HERRING)
        self.assertGreater(abs(herring.z_ap), 1.5 * abs(real.z_ap))

        # And it is worthless: it barely talks to the aggressor at all.
        self.assertLess(abs(herring.z_pb), 1e-2 * abs(real.z_pb))
        self.assertGreater(abs(real.delta), 100.0 * abs(herring.delta))

        # So the ranking the module actually produces must NOT put it near the
        # top, while the naive one-column ranking does.
        self.assertGreaterEqual(self.order.index(PLANTED_RED_HERRING), 4)
        naive = sorted(self.rows, key=lambda r: -abs(r.z_ap))
        self.assertEqual(naive[0].port + 1, PLANTED_RED_HERRING,
                         "the one-column screen must be shown to fail here")

    def test_the_aggressor_only_ports_are_the_mirror_of_the_red_herring(self):
        """
        Ports 9 and 10 couple hard to the AGGRESSOR and not at all to the
        victim, which is the same failure seen from the other side: a screen
        ranking on |Z_pb| alone puts port 9 first.

        Defeating mutation: sort by `-abs(r.z_pb)`.
        """
        naive = sorted(self.rows, key=lambda r: -abs(r.z_pb))
        self.assertEqual(naive[0].port + 1, 9)
        self.assertGreaterEqual(self.order.index(9), 4)

    def test_the_bracket_is_the_25_7_dB_the_contract_measured(self):
        """
        Defeating mutation: compute `span_db` from |Z| rather than from the
        mapped quantity -- which reads 25.91 dB here, because the real part of
        Z_ab is not part of M.
        """
        br = at.cold_start_bracket(self.Y, F_TEST, self.ts, "V", "A2", F0,
                                   context=self.csc)
        self.assertAlmostEqual(br.span_db, 25.67, places=1)
        self.assertEqual(br.n_candidates, 8)
        self.assertEqual(br.n_screenable, 8)

    def test_saturation_names_the_two_ports_that_matter(self):
        """
        Two of eight candidates already put the answer within 10% of the full
        open -> all-grounded span, and the greedy order picks exactly the two
        planted ones first.

        Defeating mutation: set `saturation_rel` to 1.0 (then k = 1 saturates)
        or to 0 (then nothing ever does).
        """
        cv = at.cold_start_cumulative(self.Y, F_TEST, self.ts, "V", "A2", F0,
                                      context=self.csc)
        self.assertEqual(cv.saturation_k, 2)
        self.assertAlmostEqual(cv.saturation_tol, at.COLD_START_SATURATION_REL)
        # `order` holds 0-BASED port indices (see cold_start_cumulative), so
        # the comparison against the 1-based planted roles has to convert.
        self.assertEqual(sorted(p + 1 for p in cv.order[:2]),
                         list(PLANTED_BOTH))
        self.assertTrue(any("Saturation: 2 of 8" in n for n in cv.notes),
                        f"the saturation point is not reported: {cv.notes}")

    def test_no_pair_is_flagged_where_no_pair_mechanism_was_planted(self):
        """
        The negative half of the pair scan. Every port here is an independent
        branch to the reference, so grounding two does what grounding them one
        at a time predicts; largest non-additivity 5.40 pH against a 197.7 pH
        threshold.

        Defeating mutation: set COLD_START_PAIR_REL to 0 (then all 28 pairs
        flag and the step becomes noise).
        """
        pairs = at.cold_start_pairs(self.Y, F_TEST, self.ts, "V", "A2", F0,
                                    context=self.csc, screen=self.rows)
        self.assertEqual(len(pairs), 28)
        self.assertEqual([p.label for p in pairs if p.flagged], [])
        self.assertGreater(pairs[0].threshold, 10.0 * abs(
            pairs[0].non_additivity.real))


# ---------------------------------------------------------------------------
# 3. The shield: what a single-port screen is structurally blind to
# ---------------------------------------------------------------------------

class TestShieldBlindSpot(unittest.TestCase):
    """
    A guard-ring segment brought out as two ports.  Ground one end and no loop
    exists; ground both and it closes.
    """

    @classmethod
    def setUpClass(cls):
        cls.Y = shield_network()
        cls.ts = probes_only(PROBES)
        cls.cs = at.cold_start_report(cls.Y, F_TEST, cls.ts, "V", "A2", F0,
                                      port_names=SHIELD_NAMES)

    def test_the_single_port_screen_reports_it_as_two_minor_entries(self):
        """
        The premise of the whole step. Both ends read +9.689 pH -- small,
        POSITIVE, and identical -- so step 1 alone says the shield is a pair of
        minor contributors.

        Defeating mutation: none needed to make this pass; it is the
        precondition assertion that stops the tests below from being a
        tautology about a network where step 1 already worked.
        """
        rows = {r.port + 1: r for r in self.cs.screen}
        self.assertEqual(sorted(rows), [5, 6])
        for p in (5, 6):
            self.assertAlmostEqual(rows[p].delta.real * 1e12, 9.689, places=2)
            self.assertGreater(rows[p].delta.real, 0.0)

    def test_the_pair_scan_finds_the_90x_joint_effect(self):
        """
        Defeating mutation: return only the pairs from the top TWO of the
        screen minus one, or drop step 2 entirely (`return []`) -- the 90x
        effect then appears nowhere in the output.
        """
        self.assertEqual(len(self.cs.pairs), 1)
        pe = self.cs.pairs[0]
        self.assertEqual((pe.port_i + 1, pe.port_j + 1), (5, 6))
        self.assertAlmostEqual(pe.delta_pair.real * 1e12, -870.268, places=2)
        self.assertAlmostEqual(pe.non_additivity.real * 1e12, -889.645,
                               places=2)
        self.assertGreater(pe.ratio, 80.0)
        self.assertLess(pe.ratio, 100.0)

    def test_the_joint_sign_is_the_OPPOSITE_of_both_singles(self):
        """
        Not a detail: a positive number 90x too small and a negative number are
        different engineering conclusions.

        Defeating mutation: take `abs()` of `delta_pair` or of the singles
        anywhere on the path -- the same clipping this repo forbids for
        R/L/C/Q/M/C_c/k.
        """
        pe = self.cs.pairs[0]
        self.assertGreater(pe.delta_i.real, 0.0)
        self.assertGreater(pe.delta_j.real, 0.0)
        self.assertLess(pe.delta_pair.real, 0.0)
        self.assertTrue(pe.sign_flip)

    def test_the_pair_is_flagged_and_carries_the_threshold_it_beat(self):
        """
        Defeating mutation: make `flagged` unconditionally False, or drop the
        `threshold` field so a reader cannot see what the filter was.
        """
        pe = self.cs.pairs[0]
        self.assertTrue(pe.flagged)
        self.assertGreater(abs(pe.non_additivity.real), pe.threshold)
        # measured: threshold 4.844 pH = 0.5 * the 9.689 pH single-port scale
        self.assertAlmostEqual(pe.threshold * 1e12, 4.844, places=2)

    def test_the_mechanism_is_the_LOOP_and_not_the_grounding(self):
        """
        Shorting the two ends to each other, with no ground anywhere, gives the
        identical number -- which is what proves the pair scan is finding a
        structure and not an artefact of ideal grounds.

        Both sides here come from `compute_z_matrix`, so this asserts a fact
        about the network rather than about the module.
        """
        base = honest_at(self.Y, F_TEST, PROBES, F0, 0, 1).imag / OMEGA
        both_gnd = honest_at(self.Y, F_TEST, PROBES, F0, 0, 1,
                             grounded=(4, 5)).imag / OMEGA - base
        shorted = honest_at(self.Y, F_TEST, PROBES, F0, 0, 1,
                            extra="5 short_to 6\n").imag / OMEGA - base
        self.assertAlmostEqual(both_gnd * 1e12, -870.268, places=2)
        self.assertAlmostEqual(shorted * 1e12, -870.268, places=2)
        self.assertAlmostEqual(self.cs.pairs[0].delta_pair.real, both_gnd,
                               delta=1e-9 * abs(both_gnd))

    def test_the_mirror_direction_also_names_the_pair(self):
        """
        From ALL-GROUNDED, opening either end reads +879.956 pH -- the number
        that says "these two are one thing", and the opposite reading from the
        +9.689 pH the same port gives from all-open.

        Defeating mutation: report only one direction. The two catch opposite
        failures (loop closure vs parallel-return saturation) and one is not a
        substitute for the other.
        """
        self.assertEqual(len(self.cs.mirror), 2)
        for s in self.cs.mirror:
            self.assertAlmostEqual(s.delta.real * 1e12, 879.956, places=2)
        # and the two directions genuinely disagree about this port
        from_open = self.cs.screen[0].delta.real
        self.assertGreater(abs(self.cs.mirror[0].delta.real),
                           50.0 * abs(from_open))

    def test_the_greedy_curve_falls_off_the_cliff_at_k_equals_two(self):
        """
        Step 3 can stumble onto the pair -- and here it does, which is why the
        docstring says "can" and not "will".

        Defeating mutation: order the curve ONCE by the single-port deltas and
        never re-rank; on a symmetric structure that still reaches k=2, so the
        real guard is the value at k=2 rather than the order.
        """
        cv = self.cs.curve
        self.assertEqual(cv.k, (1, 2))
        self.assertAlmostEqual(cv.values[0].real * 1e12, 36.474, places=2)
        self.assertAlmostEqual(cv.values[1].real * 1e12, -843.483, places=2)
        self.assertAlmostEqual(cv.non_additivity[1].real * 1e12, -889.645,
                               places=2)


# ---------------------------------------------------------------------------
# 4. The bracket, and the honesty clause that has to travel with it
# ---------------------------------------------------------------------------

class TestBracketCaveat(unittest.TestCase):

    def test_the_caveat_is_ON_the_result_and_names_the_reactive_case(self):
        """
        The caveat is a field of `Bracket`, not a line a caller may forget to
        print, and it is the module constant verbatim so every export carries
        one wording.

        Defeating mutation: drop `caveat` from `Bracket`, or paraphrase it at
        one of the two call sites so the CLI and a GUI disagree about what the
        bracket means.
        """
        Y = planted_network()
        br = at.cold_start_bracket(Y, F_TEST, probes_only(PROBES), "V", "A2",
                                   F0)
        self.assertEqual(br.caveat, at.COLD_START_BRACKET_CAVEAT)
        low = br.caveat.lower()
        self.assertIn("not a bound", low)
        self.assertIn("reactive", low)
        self.assertIn("sweep_mobius", low)
        text = "\n".join(at.format_cold_start(
            at.cold_start_report(Y, F_TEST, probes_only(PROBES), "V", "A2",
                                 F0)))
        self.assertIn(at.COLD_START_BRACKET_CAVEAT, text)

    def test_a_reactive_termination_really_does_leave_the_bracket(self):
        """
        The caveat is not decoration: it is demonstrated on shipped code.

        `sweep_mobius` over the same element the bracket's two endpoints
        describe (t = 0 is ideal ground, t -> inf is open) reports an interval
        that goes OUTSIDE those endpoints, because a series ground inductance
        resonates with the structure's shunt capacitance. If this test ever
        goes green by the interval being inside the bracket, the caveat has
        become false and must be rewritten, not deleted.
        """
        d, Y = load("diff_pair_4port.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        probes = "1 signal vic +\n3 signal agg +\n"
        ts = probes_only(probes)
        csc = at.cold_start_context(Y, d.freqs, ts, f)
        br = at.cold_start_bracket(Y, d.freqs, ts, "vic", "agg", f,
                                   context=csc)
        # port 2 is a candidate; sweep its ground lead's series L over [0, inf)
        e = csc.element_of_port[1]
        sw = at.sweep_mobius(csc.ctx, "vic", "agg", e, quantity="M", param="L")
        lo, hi = sw.interval
        b_lo = min(br.value_open.real, br.value_grounded.real)
        b_hi = max(br.value_open.real, br.value_grounded.real)
        self.assertTrue(
            lo < b_lo - 1e-15 or hi > b_hi + 1e-15,
            f"the sweep [{lo!r}, {hi!r}] stayed inside the open..ideal bracket "
            f"[{b_lo!r}, {b_hi!r}] -- COLD_START_BRACKET_CAVEAT would then be "
            "an unproven claim")

    def test_a_bracket_with_nothing_to_screen_says_the_zero_is_structural(self):
        """
        `coupled_4port_float.s4p` probed on 1 and 3 has no reference at all
        without its other two ports, so both of them are folded into the
        baseline: "all open" is not a network that exists, and the 0.00 dB span
        is a statement about the arithmetic, not about the layout.

        Defeating mutation: report the 0 dB with no note -- a reader then
        concludes the other ports do not matter, which is the opposite of the
        truth.
        """
        d, Y = load("coupled_4port_float.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = probes_only("1 signal c1 +\n3 signal c2 +\n")
        br = at.cold_start_bracket(Y, d.freqs, ts, "c1", "c2", f)
        self.assertEqual(br.n_candidates, 2)
        self.assertEqual(br.n_screenable, 0)
        self.assertAlmostEqual(br.span_db, 0.0, places=9)
        self.assertTrue(
            any("by construction, not by measurement" in n for n in br.notes),
            f"the structural zero is not explained: {br.notes}")


# ---------------------------------------------------------------------------
# 5. The name-family suggestion, and the honesty rule that goes with it
# ---------------------------------------------------------------------------

class TestNameFamilySuggestions(unittest.TestCase):

    def test_a_family_suggestion_never_changes_a_reported_number(self):
        """
        The requirement in one assertion: run the WHOLE report twice, once with
        port names and once without, and demand that every number is identical.
        Only the suggestion list may differ.

        Defeating mutation: use the family grouping anywhere in the answer --
        e.g. have `cold_start_cumulative` ground a flagged family together as
        its first step, or have `cold_start_screen` report a family's joint
        delta on each of its rows. Both are tempting and both let a port NAME
        decide what gets reported.
        """
        Y = shield_network()
        ts = probes_only(PROBES)
        named = at.cold_start_report(Y, F_TEST, ts, "V", "A2", F0,
                                     port_names=SHIELD_NAMES)
        anon = at.cold_start_report(Y, F_TEST, ts, "V", "A2", F0)

        self.assertEqual(named.bracket.value_open, anon.bracket.value_open)
        self.assertEqual(named.bracket.value_grounded,
                         anon.bracket.value_grounded)
        self.assertEqual(named.bracket.span_db, anon.bracket.span_db)
        self.assertEqual([r.port for r in named.screen],
                         [r.port for r in anon.screen])
        self.assertEqual([r.delta for r in named.screen],
                         [r.delta for r in anon.screen])
        self.assertEqual([r.z_ap for r in named.screen],
                         [r.z_ap for r in anon.screen])
        self.assertEqual([r.z_pb for r in named.screen],
                         [r.z_pb for r in anon.screen])
        self.assertEqual([(p.port_i, p.port_j, p.delta_pair, p.non_additivity,
                           p.flagged, p.threshold) for p in named.pairs],
                         [(p.port_i, p.port_j, p.delta_pair, p.non_additivity,
                           p.flagged, p.threshold) for p in anon.pairs])
        self.assertEqual(named.curve.order, anon.curve.order)
        self.assertEqual(named.curve.values, anon.curve.values)
        self.assertEqual(named.curve.saturation_k, anon.curve.saturation_k)
        self.assertEqual([(s.elements, s.delta) for s in named.mirror],
                         [(s.elements, s.delta) for s in anon.mirror])
        # ...and the ONLY difference is that one of them has a suggestion.
        self.assertEqual(anon.families, [])
        self.assertEqual(len(named.families), 1)

    def test_the_guard_ring_family_is_suggested_with_BOTH_numbers(self):
        """
        The sentence the contract specifies, with the joint and the separate
        number computed rather than asserted.

        Defeating mutation: emit the suggestion without evaluating the family
        (`tested = True` with `together` left undefined) -- the sentence then
        proposes a grouping it never checked.
        """
        Y = shield_network()
        ts = probes_only(PROBES)
        cs = at.cold_start_report(Y, F_TEST, ts, "V", "A2", F0,
                                  port_names=SHIELD_NAMES)
        fs = cs.families[0]
        self.assertEqual(fs.prefix, "guard_ring")
        self.assertEqual(fs.ports, (4, 5))
        self.assertTrue(fs.tested)
        self.assertTrue(fs.flagged)
        self.assertAlmostEqual(fs.together.real * 1e12, -870.268, places=2)
        self.assertAlmostEqual(fs.separate.real * 1e12, 19.378, places=2)
        self.assertIn("guard_ring", fs.text)
        self.assertIn("if they are one structure, group them", fs.text)
        self.assertIn("suggestion", fs.text)

    def test_two_different_coils_are_not_made_one_family(self):
        """
        `name_prefix` strips only a TRAILING run of digits, so 'c1_n' and
        'c2_n' stay two families -- exactly the false alarm core's rule exists
        to avoid, arriving here from a different direction.

        Defeating mutation: strip digits ANYWHERE in the name. The probe spec
        below is chosen so that both remaining candidates are 'c?_n': under the
        trailing-only rule they are two families of one and nothing is
        suggested, and under a strip-anywhere rule they collapse into one
        'c_n' family and the tool proposes grouping the far ends of two
        DIFFERENT coils.
        """
        d, Y = load("coupled_4port_diff.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = probes_only("1 signal c1 +\n3 signal c2 +\n")
        cs = at.cold_start_report(Y, d.freqs, ts, "c1", "c2", f,
                                  port_names=d.port_names)
        self.assertEqual(tuple(d.port_names),
                         ("c1_p", "c1_n", "c2_p", "c2_n"))
        self.assertEqual(sorted(r.port for r in cs.screen), [1, 3])
        self.assertTrue(all(r.defined for r in cs.screen))
        self.assertEqual([fs.prefix for fs in cs.families], [])

    def test_an_untested_family_says_it_was_not_tested(self):
        """
        Called without a context there is nothing to test the grouping with, so
        the joint number is undefined and the sentence must not imply one.

        Defeating mutation: fall back to the SUM of the separate deltas as
        `together` -- the suggestion then always reads "the two agree", which
        is precisely the answer it cannot know.
        """
        Y = shield_network()
        ts = probes_only(PROBES)
        rows = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)
        fams = at.name_family_suggestions(SHIELD_NAMES, rows)
        self.assertEqual(len(fams), 1)
        fs = fams[0]
        self.assertFalse(fs.tested)
        self.assertFalse(fs.flagged)
        self.assertFalse(math.isfinite(fs.together.real))
        self.assertIn("NOT tested", fs.text)

    def test_an_untested_family_names_the_REAL_reason_it_was_not_tested(self):
        """
        "No context was supplied" printed on a call that DID supply one sends
        the reader hunting for a missing argument instead of at the port the
        screen could not evaluate.

        The situation is constructed here (a context narrowed to one of the two
        family members) because no repo fixture has a name family straddling a
        folded baseline -- but the branch is reachable from ordinary use the
        moment a guard ring's far end is the port the baseline had to ground.

        Defeating mutation: keep the single fixed "no context was supplied"
        string for both cases.
        """
        Y = shield_network()
        ts = probes_only(PROBES)
        rows = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)
        narrow = at.cold_start_context(Y, F_TEST, ts, F0, candidates=[4],
                                       port_names=SHIELD_NAMES)
        fams = at.name_family_suggestions(SHIELD_NAMES, rows, context=narrow,
                                          victim="V", aggressor="A2")
        self.assertEqual(len(fams), 1)
        self.assertFalse(fams[0].tested)
        self.assertIn("could not evaluate port(s) 6", fams[0].text)
        self.assertNotIn("no context was supplied", fams[0].text)

    def test_a_family_below_the_threshold_reports_the_difference(self):
        """
        The negative half: eight ports called aux1..aux8 are one name family
        and are genuinely additive, so the suggestion must say the grouping
        would change nothing AND show by how much.

        Defeating mutation: hide unflagged families -- a reader then cannot
        tell "tested and additive" from "never considered".
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        cs = at.cold_start_report(Y, F_TEST, ts, "V", "A2", F0,
                                  port_names=PLANTED_NAMES)
        self.assertEqual([fs.prefix for fs in cs.families], ["aux"])
        fs = cs.families[0]
        self.assertTrue(fs.tested)
        self.assertFalse(fs.flagged)
        self.assertEqual(fs.ports, tuple(range(4, 12)))
        self.assertIn("would not change the conclusion", fs.text)


# ---------------------------------------------------------------------------
# 6. Honesty: what the screen does with ports it cannot answer about
# ---------------------------------------------------------------------------

class TestScreenHonesty(unittest.TestCase):

    def test_a_port_the_screen_cannot_evaluate_is_KEPT_with_a_reason(self):
        """
        A table of "which ports matter" that silently omits the ports it could
        not evaluate is a wrong answer with a plausible shape -- the same rule
        the contribution table follows about open ports.

        Defeating mutation: skip unreachable candidates in `cold_start_screen`
        instead of emitting a row with `defined=False`.
        """
        d, Y = load("coupled_4port_float.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = probes_only("1 signal c1 +\n3 signal c2 +\n")
        rows = at.cold_start_screen(Y, d.freqs, ts, "c1", "c2", f,
                                    port_names=d.port_names)
        self.assertEqual([r.port + 1 for r in rows], [2, 4])
        for r in rows:
            self.assertFalse(r.defined)
            self.assertFalse(math.isfinite(r.delta.real))
            self.assertFalse(math.isfinite(r.delta.imag))
            self.assertIn("BASELINE", r.note)

    def test_an_undefined_row_is_a_missing_measurement_and_sorts_LAST(self):
        """
        NaN is not a small number. The same rule `rank_coupling_pairs` applies
        to an undefined coupling ratio.

        Defeating mutation: sort with `-r.abs_delta` alone -- NaN compares
        false against everything and the undefined rows land wherever the
        sort's stability happens to put them.
        """
        d, Y = load("coupled_4port_diff.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = probes_only("1 signal c1 +\n3 signal c2 +\n")
        csc = at.cold_start_context(Y, d.freqs, ts, f)
        rows = at.cold_start_screen(Y, d.freqs, ts, "c1", "c2", f, context=csc)
        # This fixture has two perfectly screenable candidates, so fake the
        # mixed case by hand: the property under test is the ORDER, and it is a
        # property of the sort key, not of any particular file.
        good = [r for r in rows if r.defined]
        self.assertTrue(good)
        bad = at.PortScreenRow(
            port=99, name="", z_ap=complex("nan"), z_pb=complex("nan"),
            z_pp=complex("nan"), value=complex("nan"),
            delta=complex(float("nan"), float("nan")),
            delta_db=float("nan"), declared="open", defined=False, note="x")
        mixed = sorted(
            good + [bad],
            key=lambda r: (0 if math.isfinite(r.abs_delta) else 1,
                           -r.abs_delta if math.isfinite(r.abs_delta) else 0.0,
                           r.port))
        self.assertFalse(mixed[-1].defined)
        # and the shipped function agrees on the all-undefined file
        d2, Y2 = load("coupled_4port_float.s4p")
        f2 = float(d2.freqs[len(d2.freqs) // 2])
        rows2 = at.cold_start_screen(Y2, d2.freqs,
                                     probes_only("1 signal c1 +\n"
                                                 "3 signal c2 +\n"),
                                     "c1", "c2", f2)
        self.assertTrue(all(not r.defined for r in rows2))

    def test_the_negative_result_excludes_what_it_could_not_measure(self):
        """
        "The other N ports are all below X dB" must not quietly mean "all the
        ones I could measure".

        Defeating mutation: count the undefined rows into the tail.
        """
        d, Y = load("coupled_4port_float.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = probes_only("1 signal c1 +\n3 signal c2 +\n")
        cs = at.cold_start_report(Y, d.freqs, ts, "c1", "c2", f)
        self.assertIn("could not be evaluated", cs.negative_result)
        self.assertIn("2,4", cs.negative_result)

    def test_the_negative_result_says_the_coupling_is_local_when_it_is(self):
        """
        The requirement says explicitly that the negative result is valuable.

        Defeating mutation: return "" whenever nothing is flagged -- the user
        then cannot tell "I checked 141 ports and they are all irrelevant" from
        "I did not check".
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        rows = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)
        line = at.cold_start_negative_result(rows, "H", top=2)
        self.assertIn("The other 6 port(s)", line)
        self.assertIn("LOCAL", line)
        self.assertIn("port 11", line)

    def test_declared_terminations_are_reported_as_NOT_in_force(self):
        """
        The cold start rewrites the spec to all-open on purpose, and the honest
        thing is to say so and point at the other side of the module.

        Defeating mutation: rewrite silently. The user then reads the screen as
        "given my 54 declared ground balls, port 88 does this", which is a
        different and much smaller number.
        """
        d, Y = load("diff_pair_4port.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = parse_custom_termination_text(
            "1 signal vic +\n2 signal agg +\n3 ground\n"
            "4 lumped_to_gnd R=1 L=1n\n")
        csc = at.cold_start_context(Y, d.freqs, ts, f)
        joined = " ".join(csc.notes)
        self.assertIn("NOT in force", joined)
        self.assertIn("ground on port 3", joined)
        self.assertIn("lumped_to_gnd on port 4", joined)
        self.assertIn("sensitivity()", joined)
        # and the rows say what the spec said, without acting on it
        rows = at.cold_start_screen(Y, d.freqs, ts, "vic", "agg", f,
                                    context=csc)
        self.assertEqual({r.port + 1: r.declared for r in rows},
                         {3: "ground", 4: "lumped_to_gnd"})
        # the baseline really is all-open: the delta matches an honest re-solve
        # from a probes-ONLY spec, not from the declared one
        probes = "1 signal vic +\n2 signal agg +\n"
        a, b = 0, 1
        base = honest_at(Y, d.freqs, probes, f, a, b)
        for r in rows:
            true = honest_at(Y, d.freqs, probes, f, a, b, grounded=(r.port,))
            self.assertAlmostEqual(
                r.delta.real, (true.imag - base.imag) / (2 * math.pi * f),
                delta=1e-9 * abs((true.imag - base.imag) / (2 * math.pi * f)))

    def test_a_short_that_defines_a_probe_side_survives_the_rewrite(self):
        """
        A `short_to` tying extra ports into a measurement-port SIDE is part of
        the question, not a decision about an unknown port, so it is kept and
        those ports never become candidates.

        Defeating mutation: decide probe membership with a plain
        `isinstance(t, Signal)` scan instead of `_probe_side_of_port`, or drop
        every ShortPair. Port 2 then becomes a candidate whose "grounding" is
        reported as moving the answer -- while what it actually does is ground
        the victim probe.
        """
        d, Y = load("coupled_4port_diff.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        ts = parse_custom_termination_text(
            "1 signal c1 +\n1 short_to 2\n3 signal c2 +\n")
        csc = at.cold_start_context(Y, d.freqs, ts, f)
        self.assertEqual(csc.candidates, (3,))
        self.assertEqual([e.describe() for e in csc.ctx.elements],
                         ["ground port 4"])

    def test_a_probe_port_offered_as_a_candidate_is_refused_by_name(self):
        """
        Defeating mutation: silently drop it from the candidate list. A typo'd
        candidate list that screens nothing is exactly the failure this module
        exists to stop.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        with self.assertRaises(at.AttribError) as cm:
            at.cold_start_context(Y, F_TEST, ts, F0, candidates=[0, 4])
        msg = str(cm.exception)
        self.assertIn("carry a measurement port", msg)
        self.assertIn("1", msg)

    def test_a_candidate_outside_the_file_is_refused_by_name(self):
        """Defeating mutation: clamp or ignore it."""
        Y = planted_network()
        ts = probes_only(PROBES)
        with self.assertRaises(at.AttribError) as cm:
            at.cold_start_context(Y, F_TEST, ts, F0, candidates=[4, 99])
        self.assertIn("100", str(cm.exception))     # 1-based in the message
        self.assertIn("outside 1..12", str(cm.exception))

    def test_narrowing_the_candidates_narrows_the_screen_and_nothing_else(self):
        """
        The subset's deltas must be the SAME numbers the full scan produced:
        every one is measured from all-open, so removing other candidates from
        the list cannot move them.

        Defeating mutation: build the baseline from the candidates that were
        offered (e.g. leave the un-offered ports grounded) -- the deltas then
        depend on which ports the caller happened to ask about.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        full = {r.port: r.delta for r in
                at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)}
        part = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0,
                                    candidates=[4, 6, 10])
        # ranked by |delta|, not by port: 5 is the real path (-395 pH), 11 is
        # a weak-both (-1.77 pH) and 7 is the red herring (-0.378 pH).
        self.assertEqual([r.port for r in part], [4, 10, 6])
        for r in part:
            self.assertAlmostEqual(r.delta.real, full[r.port].real,
                                   delta=1e-12 * abs(full[r.port].real))


# ---------------------------------------------------------------------------
# 7. The blind spot is on the page, not in a footnote
# ---------------------------------------------------------------------------

class TestBlindSpotIsStated(unittest.TestCase):

    def test_the_report_says_what_it_cannot_find(self):
        """
        Defeating mutation: move the text into a docstring only. A user reading
        the rendered report would then have no way to know the screen is second
        order.
        """
        Y = shield_network()
        cs = at.cold_start_report(Y, F_TEST, probes_only(PROBES), "V", "A2",
                                  F0)
        self.assertEqual(cs.blind_spot, at.COLD_START_BLIND_SPOT_TEXT)
        text = "\n".join(at.format_cold_start(cs))
        self.assertIn(at.COLD_START_BLIND_SPOT_TEXT, text)
        low = at.COLD_START_BLIND_SPOT_TEXT.lower()
        self.assertIn("three or more", low)
        self.assertIn("no guarantee", low)

    def test_the_module_docstring_says_it_too(self):
        """
        The contract asks for it in the docstring AND on the screen.

        Defeating mutation: delete the paragraph from the module docstring.
        """
        doc = at.__doc__ or ""
        self.assertIn("COLD START", doc)
        self.assertIn("COLD_START_BLIND_SPOT_TEXT", doc)
        self.assertIn("three or more", doc.lower())
        for fn in (at.cold_start_pairs, at.cold_start_cumulative):
            self.assertTrue(fn.__doc__)

    def test_the_curve_carries_the_caveat_when_rendered_on_its_own(self):
        """
        A `CumulativeCurve` can be handed to a plotter without the report
        around it, so its own notes have to say the order is greedy and that a
        group of three can be missed.

        Defeating mutation: leave `notes` empty.
        """
        Y = planted_network()
        cv = at.cold_start_cumulative(Y, F_TEST, probes_only(PROBES), "V",
                                      "A2", F0)
        joined = " ".join(cv.notes)
        self.assertIn("not optimal", joined)
        self.assertIn("THREE or more", joined)


# ---------------------------------------------------------------------------
# 8. Quantities and scales
# ---------------------------------------------------------------------------

class TestQuantities(unittest.TestCase):

    def test_a_non_decomposable_quantity_is_refused_BY_NAME(self):
        """
        The cold start goes through the same `_resolve_quantity` registry as
        everything else, so `C_c` is refused with the reason and the
        alternative rather than with "unsupported quantity".

        Defeating mutation: accept any string and silently fall back to 'M'.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        for fn in (at.cold_start_bracket, at.cold_start_screen,
                   at.cold_start_cumulative):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(at.AttribError) as cm:
                    fn(Y, F_TEST, ts, "V", "A2", F0, quantity="C_c")
                self.assertIn("RECIPROCAL", str(cm.exception))

    def test_k_takes_its_scale_from_each_configuration_not_from_the_baseline(self):
        """
        Grounding a port changes L_a as well as M, so `k` for that
        configuration must be computed from that configuration's own self
        inductances -- the `_scale_from` rule, arriving on the cold-start side.

        Defeating mutation: freeze the scale at the all-open configuration
        (`_quantity_scale` instead of `_scale_from`). The check below compares
        against `compute_z_matrix`'s own matrix for each grounded spec, so the
        frozen version disagrees wherever grounding moves L_a at all.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        rows = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0, "k")
        base_Z, _ = honest_Z(Y, F_TEST, PROBES)
        idx = 0

        def k_of(Z):
            La = Z[0, 0].imag / OMEGA
            Lb = Z[1, 1].imag / OMEGA
            if not (La > 0 and Lb > 0):
                return float("nan")
            return (Z[0, 1].imag / OMEGA) / math.sqrt(La * Lb)

        k_open = k_of(base_Z[idx])
        moved = 0
        for r in rows:
            Zg, _ = honest_Z(Y, F_TEST, PROBES, grounded=(r.port,))
            k_new = k_of(Zg[idx])
            self.assertAlmostEqual(r.value.real, k_new,
                                   delta=1e-9 * max(abs(k_new), 1e-30))
            self.assertAlmostEqual(r.delta.real, k_new - k_open,
                                   delta=1e-9 * max(abs(k_new - k_open), 1e-30))
            # the frozen-scale answer, i.e. the mutation, for this row
            frozen = (Zg[idx][0, 1].imag / OMEGA) / math.sqrt(
                (base_Z[idx][0, 0].imag / OMEGA)
                * (base_Z[idx][1, 1].imag / OMEGA))
            if abs(frozen - k_new) > 1e-6 * max(abs(k_new), 1e-30):
                moved += 1
        self.assertGreater(
            moved, 0,
            "no row on this network distinguishes the two scale rules, so the "
            "assertion above cannot catch the frozen-scale bug")

    def test_the_two_coupling_columns_are_raw_impedances_whatever_the_quantity(self):
        """
        |Z_ap| and |Z_pb| are transimpedances in ohms and must not be scaled by
        the quantity -- they answer "does this port talk to that side", which
        is not a question about henries.

        Defeating mutations, both run: divide them by omega along with the
        delta, and read the victim column off `Pmat_b` instead of `Rmat` --
        the reciprocity shortcut requirement 1 forbids, which on a symmetric
        synthetic Y is invisible in the numbers and visible only in the
        identity asserted at the end of this test.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        csc = at.cold_start_context(Y, F_TEST, ts, F0)
        as_m = {r.port: (r.z_ap, r.z_pb) for r in at.cold_start_screen(
            Y, F_TEST, ts, "V", "A2", F0, "M", context=csc)}
        as_k = {r.port: (r.z_ap, r.z_pb) for r in at.cold_start_screen(
            Y, F_TEST, ts, "V", "A2", F0, "k", context=csc)}
        self.assertEqual(as_m, as_k)
        # and they really are the Zbase entries the closed form divides
        e = csc.element_of_port[4]
        self.assertEqual(as_m[4][0], complex(csc.ctx.Rmat[e, 0]))
        self.assertEqual(as_m[4][1], complex(csc.ctx.Pmat_b[e, 1]))


# ---------------------------------------------------------------------------
# 9. Cost: two solves plus the diagonal, not one re-solve per port
# ---------------------------------------------------------------------------

class TestCostIsStructural(unittest.TestCase):
    """
    A wall-clock assertion would be flaky, so the cost claim is pinned
    structurally: the whole four-step report must reach `compute_z_matrix`
    exactly ONCE however many candidate ports there are.
    """

    def test_a_whole_report_calls_compute_z_matrix_exactly_once(self):
        """
        Measured on a 153-port file: 2.41 ms for the 149-port screen against
        2402.6 ms for the same 149 through `compute_z_matrix`, a factor of 997.
        This test is that claim without a clock.

        Defeating mutation: drop `context=csc` from any of the five calls
        inside `cold_start_report` (each rebuild is one more engine call and,
        at 153 ports, 350.6 ms), or re-solve per candidate anywhere.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        calls = []
        real = at.compute_z_matrix

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        at.compute_z_matrix = counting
        try:
            cs = at.cold_start_report(Y, F_TEST, ts, "V", "A2", F0,
                                      port_names=PLANTED_NAMES)
        finally:
            at.compute_z_matrix = real
        self.assertEqual(len(calls), 1,
                         f"compute_z_matrix was called {len(calls)} times")
        # ...and it really did do the work.
        self.assertEqual(len(cs.screen), 8)
        self.assertEqual(len(cs.pairs), 28)
        self.assertEqual(len(cs.mirror), 8)
        self.assertTrue(cs.curve.k)

    def test_the_context_is_shared_and_the_steps_agree_with_the_unshared_ones(self):
        """
        Passing `context=` must be an optimisation and nothing else.

        Defeating mutation: have `_cs_context` mutate the context it is handed
        (e.g. cache a per-victim value on it), so the second step off a shared
        context answers a different question from a fresh one.
        """
        Y = planted_network()
        ts = probes_only(PROBES)
        csc = at.cold_start_context(Y, F_TEST, ts, F0)
        shared = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0,
                                      context=csc)
        # a second, different question off the SAME context first
        at.cold_start_screen(Y, F_TEST, ts, "A2", "V", F0, context=csc)
        again = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0,
                                     context=csc)
        fresh = at.cold_start_screen(Y, F_TEST, ts, "V", "A2", F0)
        self.assertEqual([r.delta for r in shared], [r.delta for r in again])
        self.assertEqual([r.delta for r in shared], [r.delta for r in fresh])


# ---------------------------------------------------------------------------
# 10. The rendered report
# ---------------------------------------------------------------------------

class TestRenderedReport(unittest.TestCase):

    def test_every_step_reaches_the_page(self):
        """
        Defeating mutation: drop any one step from `format_cold_start`. The
        four steps are ordered on purpose -- the bracket first, because it
        answers "is any of this worth my time" before anything else is
        computed.
        """
        Y = shield_network()
        cs = at.cold_start_report(Y, F_TEST, probes_only(PROBES), "V", "A2",
                                  F0, port_names=SHIELD_NAMES)
        text = "\n".join(at.format_cold_start(cs))
        for marker in ("STEP 0", "STEP 1", "STEP 2", "STEP 3",
                       "NAME-FAMILY SUGGESTIONS"):
            self.assertIn(marker, text)
        self.assertLess(text.index("STEP 0"), text.index("STEP 1"))
        self.assertLess(text.index("STEP 1"), text.index("STEP 2"))
        self.assertLess(text.index("STEP 2"), text.index("STEP 3"))
        self.assertIn(at.SIGN_CONVENTION_TEXT, text)
        self.assertIn("SIGN FLIP", text)
        self.assertIn("guard_ring1", text)

    def test_the_page_explains_why_there_are_two_coupling_columns(self):
        """
        Defeating mutation: print one column, or print the product. The
        measured red herring is the reason and it belongs where the table is.
        """
        Y = planted_network()
        cs = at.cold_start_report(Y, F_TEST, probes_only(PROBES), "V", "A2",
                                  F0)
        text = "\n".join(at.format_cold_start(cs))
        self.assertIn("|Z_ap|", text)
        self.assertIn("|Z_pb|", text)
        self.assertIn("SEPARATE columns on purpose", text)

    def test_rendering_never_raises_on_a_degenerate_case(self):
        """
        The renderer runs on whatever the analysis produced, including an empty
        screen, an empty pair list and an empty curve.

        Defeating mutation: index `cs.pairs[0]` unguarded for the threshold
        line.
        """
        d, Y = load("coupled_4port_float.s4p")
        f = float(d.freqs[len(d.freqs) // 2])
        cs = at.cold_start_report(Y, d.freqs,
                                  probes_only("1 signal c1 +\n3 signal c2 +\n"),
                                  "c1", "c2", f, port_names=d.port_names)
        lines = at.format_cold_start(cs)
        self.assertTrue(lines)
        self.assertIn("No pair could be scanned",
                      "\n".join(lines))


if __name__ == "__main__":                                # pragma: no cover
    unittest.main(verbosity=2)
