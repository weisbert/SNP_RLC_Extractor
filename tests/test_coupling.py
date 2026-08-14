"""
Acceptance tests for the self + mutual coupling probe model.

These tests are the physics contract for `compute_z_matrix` / `MeasPort` /
`extract_coupling_at_freq`:

  * a measurement port is a probe pair (plus side / minus side), and the reduced
    group-level Z matrix is the first-class output;
  * the diagonal is the self impedance, the off-diagonal is the open-circuit
    mutual impedance, so M = Im(Z_ab)/omega is the textbook mutual inductance;
  * M, C_c, k and the M/L coupling ratios keep their physical sign
    (Cadence convention) and are never abs()-ed or clipped to NaN;
  * the legacy A/B modes are exactly the single-measurement-port case and must
    stay bit-identical.

Every expected number comes from `tests/generate_test_snp.py` -- the COUPLED_*
constants there are the single source of truth and are also stamped into each
fixture's header comment.

Reference impedances used below are derived by hand, independent of anything in
pkg_rlc_core:

  Z_loop = [[R1 + jwL1, jwM], [jwM, R2 + jwL2]]                       (mesh form)

  Ground-referenced fixture: incidence A = I, so Y_node = Y_loop and the port
  impedance matrix IS Z_loop.

  Floating fixture, incidence A = [[1,0],[-1,0],[0,1],[0,-1]], shunt g = jwC on
  every terminal.  A.T @ A == 2I, and the probe matrix W equals A, so Woodbury
  gives the closed form

      Z_meas = (2/g) I - (4/g^2) (Z_loop + (2/g) I)^-1

  which tends to Z_loop as C -> 0 (the C == 0 fixture).
"""

from __future__ import annotations

import math
import re
import sys
import unittest
import warnings
from pathlib import Path

# Make pkg_rlc_core and the fixture generator importable when run directly.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import numpy as np  # noqa: E402
from numpy.testing import assert_allclose, assert_array_equal  # noqa: E402

import generate_test_snp as gen  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    RECIPROCITY_WARN,
    MeasPort,
    Signal,
    TerminationSet,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    compute_z,
    compute_z_matrix,
    extract_coupling_at_freq,
    parse_custom_termination_text,
    parse_mport_spec,
    parse_touchstone,
    resolve_meas_ports,
    s_to_y,
)

FIX = _HERE / "fixtures"

# The frequency every point-value assertion is made at; it is exactly on the
# fixture grid (0.1 GHz .. 10 GHz in 0.1 GHz steps).
F_TEST = 1.0e9

COUPLED_FILES = (
    "coupled_2port_gndref.s2p",
    "coupled_4port_diff.s4p",
    "coupled_4port_float.s4p",
    "coupled_2port_negM.s2p",
)


def _ensure_fixtures() -> None:
    if all((FIX / n).exists() for n in COUPLED_FILES):
        return
    gen.main()


def _load(name: str):
    """Parse a fixture and return (freqs, Y)."""
    ts = parse_touchstone(FIX / name)
    return ts.freqs, s_to_y(ts.s, ts.z0)


def _z_loop(freqs: np.ndarray, M: float = gen.COUPLED_M) -> np.ndarray:
    """Hand-written (F, 2, 2) mesh impedance of the two coupled coils."""
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    Z = np.zeros((len(w), 2, 2), dtype=complex)
    Z[:, 0, 0] = gen.COUPLED_R1 + 1j * w * gen.COUPLED_L1
    Z[:, 1, 1] = gen.COUPLED_R2 + 1j * w * gen.COUPLED_L2
    Z[:, 0, 1] = 1j * w * M
    Z[:, 1, 0] = 1j * w * M
    return Z


def _z_diff_closed_form(freqs: np.ndarray, C_shunt: float,
                        M: float = gen.COUPLED_M) -> np.ndarray:
    """
    Closed-form (F, 2, 2) differential Z of the floating fixture with a shunt C
    on every terminal.  Woodbury on Y = A Y_loop A.T + g I with A.T A = 2I:

        Z_meas = (2/g) I - (4/g^2) (Z_loop + (2/g) I)^-1
    """
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    g = 1j * w * C_shunt
    eye = np.eye(2)
    two_over_g = (2.0 / g)[:, None, None]
    Q = _z_loop(freqs, M=M) + two_over_g * eye
    return two_over_g * eye - (4.0 / g ** 2)[:, None, None] * np.linalg.inv(Q)


def _rel(actual: np.ndarray, expected: np.ndarray) -> float:
    """max|actual - expected| / max|expected| over the whole array."""
    scale = float(np.max(np.abs(expected)))
    return float(np.max(np.abs(np.asarray(actual) - np.asarray(expected)))) / scale


def _idx_at(freqs: np.ndarray, f: float) -> int:
    return int(np.argmin(np.abs(np.asarray(freqs) - f)))


class _CouplingTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def assertNoNaN(self, arr, what: str = "array"):
        a = np.asarray(arr)
        self.assertFalse(bool(np.any(np.isnan(a.real))), f"{what} has NaN in Re")
        self.assertFalse(bool(np.any(np.isnan(a.imag))), f"{what} has NaN in Im")


# ============================================================================
# 1 / 3 / 4 -- ground-referenced coupled coils (exact by construction)
# ============================================================================

class TestGroundReferencedCoupling(_CouplingTestCase):
    """Fixture (a): each coil has one terminal grounded -> two ground-referenced
    measurement ports.  Y_node == Y_loop, so the answer is exact."""

    def _run(self, fname: str, M: float):
        freqs, Y = _load(fname)
        term = build_terminations_coupling([("c1", [1], []), ("c2", [2], [])])
        Zmat, names, warns = compute_z_matrix(Y, freqs, term)
        self.assertEqual(names, ["c1", "c2"])
        self.assertEqual(Zmat.shape, (len(freqs), 2, 2))
        self.assertNoNaN(Zmat, "Zmat")
        return freqs, Zmat, names, warns

    def test_recovers_L_M_k(self):
        """Self and mutual inductance and k come back to numerical precision."""
        freqs, Zmat, names, warns = self._run("coupled_2port_gndref.s2p",
                                              gen.COUPLED_M)
        # No pinv rank warning: this Y is non-singular.
        self.assertEqual(warns, [])
        # Whole-band agreement with the hand-written mesh impedance.
        self.assertLess(_rel(Zmat, _z_loop(freqs)), 1e-11)

        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        self.assertEqual(res.freq_hz, F_TEST)
        c1, c2 = res.ports
        assert_allclose(c1.L_henry, gen.COUPLED_L1, rtol=1e-11)
        assert_allclose(c2.L_henry, gen.COUPLED_L2, rtol=1e-11)
        assert_allclose(c1.R_ohm, gen.COUPLED_R1, rtol=1e-10)
        assert_allclose(c2.R_ohm, gen.COUPLED_R2, rtol=1e-10)

        self.assertEqual(len(res.pairs), 1)
        pair = res.pairs[0]
        self.assertEqual((pair.name_a, pair.name_b), ("c1", "c2"))
        assert_allclose(pair.M_henry, gen.COUPLED_M, rtol=1e-11)
        assert_allclose(pair.k, gen.COUPLED_K, rtol=1e-11)
        # k must also be self-consistent with the extracted self inductances.
        assert_allclose(pair.k,
                        pair.M_henry / math.sqrt(c1.L_henry * c2.L_henry),
                        rtol=1e-12)
        # Positive M at a frequency well below SRF -> no advisory notes at all.
        self.assertEqual(pair.notes, [])

    def test_current_transfer_ratios(self):
        """M/L_a, M/L_b and their dB magnitudes are consistent and finite."""
        freqs, Zmat, names, _ = self._run("coupled_2port_gndref.s2p",
                                          gen.COUPLED_M)
        pair = extract_coupling_at_freq(freqs, Zmat, names, F_TEST).pairs[0]
        assert_allclose(pair.M_over_La, gen.COUPLED_M / gen.COUPLED_L1, rtol=1e-11)
        assert_allclose(pair.M_over_Lb, gen.COUPLED_M / gen.COUPLED_L2, rtol=1e-11)
        assert_allclose(pair.M_over_La_dB,
                        20.0 * math.log10(abs(gen.COUPLED_M / gen.COUPLED_L1)),
                        rtol=1e-11)
        assert_allclose(pair.M_over_Lb_dB,
                        20.0 * math.log10(abs(gen.COUPLED_M / gen.COUPLED_L2)),
                        rtol=1e-11)

    def test_reciprocity_ground_referenced(self):
        """Z_ab == Z_ba at every frequency, i.e. M_AB == M_BA."""
        freqs, Zmat, names, _ = self._run("coupled_2port_gndref.s2p",
                                          gen.COUPLED_M)
        scale = float(np.max(np.abs(Zmat[:, 0, 1])))
        self.assertGreater(scale, 0.0)
        err = float(np.max(np.abs(Zmat[:, 0, 1] - Zmat[:, 1, 0]))) / scale
        self.assertLess(err, 1e-12, f"reciprocity broken: {err:.3e}")
        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        self.assertLess(res.reciprocity_error, 1e-12)

    def test_negative_M_keeps_its_sign(self):
        """Fixture (d): M < 0 must come back negative, and so must k."""
        freqs, Zmat, names, warns = self._run("coupled_2port_negM.s2p",
                                              gen.COUPLED_M_NEG)
        self.assertLess(_rel(Zmat, _z_loop(freqs, M=gen.COUPLED_M_NEG)), 1e-11)

        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        pair = res.pairs[0]
        # Nothing clipped, nothing abs()-ed.
        self.assertFalse(math.isnan(pair.M_henry))
        self.assertFalse(math.isnan(pair.k))
        self.assertLess(pair.M_henry, 0.0, "M was abs()-ed or clipped")
        self.assertLess(pair.k, 0.0, "k lost its sign")
        assert_allclose(pair.M_henry, gen.COUPLED_M_NEG, rtol=1e-11)
        assert_allclose(pair.k, gen.COUPLED_K_NEG, rtol=1e-11)
        # C_c is always computed too; with Im(Z_ab) < 0 it is the positive one.
        self.assertFalse(math.isnan(pair.C_c_farad))
        self.assertGreater(pair.C_c_farad, 0.0)
        # The self inductances are unaffected by the winding reversal.
        assert_allclose(res.ports[0].L_henry, gen.COUPLED_L1, rtol=1e-11)
        assert_allclose(res.ports[1].L_henry, gen.COUPLED_L2, rtol=1e-11)
        # ... and the sign flip is flagged, not hidden.
        self.assertIn("Im(Z_ab) < 0: coupling is capacitive here, "
                      "read C_c instead of M", pair.notes)
        # Ratios are signed as well.
        self.assertLess(pair.M_over_La, 0.0)
        self.assertLess(pair.M_over_Lb, 0.0)
        # The dB magnitude is unsigned by definition and must match |M|/L.
        assert_allclose(pair.M_over_La_dB,
                        20.0 * math.log10(abs(gen.COUPLED_M / gen.COUPLED_L1)),
                        rtol=1e-11)

    def test_sign_flip_only_changes_the_mutual_term(self):
        """+M and -M fixtures differ in Z_ab only -- the diagonal is identical."""
        f_pos, Z_pos, _, _ = self._run("coupled_2port_gndref.s2p", gen.COUPLED_M)
        f_neg, Z_neg, _, _ = self._run("coupled_2port_negM.s2p", gen.COUPLED_M_NEG)
        assert_array_equal(f_pos, f_neg)
        d_scale = float(np.max(np.abs(Z_pos[:, 0, 0])))
        self.assertLess(float(np.max(np.abs(Z_pos[:, 0, 0] - Z_neg[:, 0, 0])))
                        / d_scale, 1e-11)
        m_scale = float(np.max(np.abs(Z_pos[:, 0, 1])))
        self.assertLess(float(np.max(np.abs(Z_pos[:, 0, 1] + Z_neg[:, 0, 1])))
                        / m_scale, 1e-11)


# ============================================================================
# 2 / 3 / 5 -- floating (differential) coupled coils with shunt C
# ============================================================================

class TestDifferentialCoupling(_CouplingTestCase):
    """Fixture (b): both coils floating, probed with +/- sides."""

    def _run(self, plus_minus=(("c1", [1], [2]), ("c2", [3], [4]))):
        freqs, Y = _load("coupled_4port_diff.s4p")
        term = build_terminations_coupling(list(plus_minus))
        Zmat, names, warns = compute_z_matrix(Y, freqs, term)
        return freqs, Zmat, names, warns

    def test_differential_probes_recover_L_M_k(self):
        freqs, Zmat, names, warns = self._run()
        self.assertEqual(names, ["c1", "c2"])
        self.assertNoNaN(Zmat, "Zmat")
        # The shunt C keeps Y non-singular, so no rank warning here.
        self.assertEqual(warns, [])

        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        c1, c2 = res.ports
        pair = res.pairs[0]
        # The shunt capacitance is the only perturbation -> ~0.1%.
        assert_allclose(c1.L_henry, gen.COUPLED_L1, rtol=1e-3)
        assert_allclose(c2.L_henry, gen.COUPLED_L2, rtol=1e-3)
        assert_allclose(pair.M_henry, gen.COUPLED_M, rtol=1e-3)
        assert_allclose(pair.k, gen.COUPLED_K, rtol=1e-3)
        assert_allclose(c1.R_ohm, gen.COUPLED_R1, rtol=1e-3)
        assert_allclose(c2.R_ohm, gen.COUPLED_R2, rtol=1e-3)
        self.assertEqual(pair.notes, [])

    def test_matches_closed_form_over_the_whole_band(self):
        """Not just 'close to the ideal coil' -- exactly the Woodbury answer."""
        freqs, Zmat, _, _ = self._run()
        ref = _z_diff_closed_form(freqs, gen.COUPLED_C_SHUNT)
        self.assertLess(_rel(Zmat, ref), 1e-9)

    def test_reciprocity_differential(self):
        freqs, Zmat, names, _ = self._run()
        scale = float(np.max(np.abs(Zmat[:, 0, 1])))
        err = float(np.max(np.abs(Zmat[:, 0, 1] - Zmat[:, 1, 0]))) / scale
        self.assertLess(err, 1e-11, f"reciprocity broken: {err:.3e}")
        self.assertLess(
            extract_coupling_at_freq(freqs, Zmat, names, F_TEST).reciprocity_error,
            1e-11)

    def test_polarity_flip_flips_M_and_k_only(self):
        """
        Swapping the + and - lists of one measurement port must flip the sign of
        M (and of k) while leaving both self impedances and |M| untouched.
        """
        freqs, Z_ref, names_ref, _ = self._run()
        _, Z_flip, names_flip, _ = self._run(
            (("c1", [1], [2]), ("c2", [4], [3])))
        self.assertEqual(names_ref, names_flip)

        for g in (0, 1):
            scale = float(np.max(np.abs(Z_ref[:, g, g])))
            err = float(np.max(np.abs(Z_ref[:, g, g] - Z_flip[:, g, g]))) / scale
            self.assertLess(err, 1e-10,
                            f"self impedance of port {g} moved: {err:.3e}")

        scale = float(np.max(np.abs(Z_ref[:, 0, 1])))
        err = float(np.max(np.abs(Z_ref[:, 0, 1] + Z_flip[:, 0, 1]))) / scale
        self.assertLess(err, 1e-10, f"mutual did not simply negate: {err:.3e}")

        p_ref = extract_coupling_at_freq(freqs, Z_ref, names_ref, F_TEST).pairs[0]
        p_flip = extract_coupling_at_freq(freqs, Z_flip, names_flip, F_TEST).pairs[0]
        self.assertGreater(p_ref.M_henry, 0.0)
        self.assertLess(p_flip.M_henry, 0.0)
        self.assertGreater(p_ref.k, 0.0)
        self.assertLess(p_flip.k, 0.0)
        assert_allclose(abs(p_flip.M_henry), abs(p_ref.M_henry), rtol=1e-10)
        assert_allclose(abs(p_flip.k), abs(p_ref.k), rtol=1e-10)


# ============================================================================
# 6 -- the exactly-singular floating fixture exercises the pinv path
# ============================================================================

class TestFullyFloatingPinvPath(_CouplingTestCase):
    """Fixture (c): no shunt C, so Y is exactly singular (rank 2 of 4).
    pinv must still produce the right differential answer, and the code must
    say so with a warning that names a frequency."""

    def test_pinv_result_is_exact_and_warns(self):
        freqs, Y = _load("coupled_4port_float.s4p")
        term = build_terminations_coupling([("c1", [1], [2]), ("c2", [3], [4])])
        Zmat, names, warns = compute_z_matrix(Y, freqs, term)
        self.assertNoNaN(Zmat, "Zmat")

        # With C -> 0 the closed form collapses to Z_loop exactly.
        self.assertLess(_rel(Zmat, _z_loop(freqs)), 1e-11)

        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        assert_allclose(res.ports[0].L_henry, gen.COUPLED_L1, rtol=1e-11)
        assert_allclose(res.ports[1].L_henry, gen.COUPLED_L2, rtol=1e-11)
        assert_allclose(res.pairs[0].M_henry, gen.COUPLED_M, rtol=1e-11)
        assert_allclose(res.pairs[0].k, gen.COUPLED_K, rtol=1e-11)

        # The rank-deficiency advisory: present, capped at 3, names a frequency.
        self.assertTrue(warns, "singular Y produced no warning at all")
        self.assertLessEqual(len(warns), 3, "warning cap of 3 was exceeded")
        pat = re.compile(r"freq\[\d+\]\s*=\s*[-+0-9.eE]+\s*Hz")
        for w in warns:
            self.assertRegex(w, r"[Rr]ank-deficient")
            self.assertRegex(w, pat)
        # The two fixtures differ only in the shunt C, so the warning is
        # attributable to the singularity and nothing else.
        _, Y_ok = _load("coupled_4port_diff.s4p")
        _, _, warns_ok = compute_z_matrix(Y_ok, freqs, term)
        self.assertEqual(warns_ok, [])


# ============================================================================
# 7 -- differential vs common-mode self impedance of one measurement port
# ============================================================================

class TestDifferentialVsCommonMode(_CouplingTestCase):
    """
    One measurement port on the balanced 4-port fixture, probed two ways:

      plus=[1], minus=[2]  -> differential: the coil itself, L_diff ~= L1
      plus=[1,2], minus=[] -> common mode: both terminals tied, the coil carries
                              no current, so only the two shunt caps are left,
                              C_cm == 2 * C_shunt exactly.
    """

    def setUp(self):
        self.freqs, self.Y = _load("coupled_4port_diff.s4p")
        self.k = _idx_at(self.freqs, F_TEST)
        self.w = 2.0 * math.pi * float(self.freqs[self.k])

    def test_differential_self_inductance(self):
        Z, warns = compute_z(self.Y, self.freqs,
                             build_terminations_coupling([("c1", [1], [2])]))
        self.assertEqual(warns, [])
        # Hand-computed reference: the [0,0] entry of the Woodbury closed form.
        ref = _z_diff_closed_form(self.freqs, gen.COUPLED_C_SHUNT)[:, 0, 0]
        self.assertLess(_rel(Z, ref), 1e-9)
        L_diff = float(Z[self.k].imag) / self.w
        assert_allclose(L_diff, gen.COUPLED_L1, rtol=1e-3)
        self.assertGreater(L_diff, 0.0)

    def test_common_mode_is_the_two_shunt_caps(self):
        Z, warns = compute_z(self.Y, self.freqs,
                             build_terminations_coupling([("c1", [1, 2], [])]))
        self.assertEqual(warns, [])
        # Hand-computed: [1,1,0,0] is in the null space of A Y_loop A.T, so the
        # common-mode admittance is exactly jw * 2 * C_shunt.
        ref = 1.0 / (1j * 2.0 * np.pi * self.freqs * 2.0 * gen.COUPLED_C_SHUNT)
        self.assertLess(_rel(Z, ref), 1e-9)
        C_cm = -1.0 / (self.w * float(Z[self.k].imag))
        assert_allclose(C_cm, 2.0 * gen.COUPLED_C_SHUNT, rtol=1e-9)

    def test_differential_and_common_mode_really_differ(self):
        Z_d, _ = compute_z(self.Y, self.freqs,
                           build_terminations_coupling([("c1", [1], [2])]))
        Z_c, _ = compute_z(self.Y, self.freqs,
                           build_terminations_coupling([("c1", [1, 2], [])]))
        # Opposite reactance sign: inductive vs capacitive.
        self.assertGreater(Z_d[self.k].imag, 0.0)
        self.assertLess(Z_c[self.k].imag, 0.0)
        # ... and orders of magnitude apart, so this cannot be a rounding story.
        self.assertGreater(abs(Z_c[self.k]) / abs(Z_d[self.k]), 100.0)
        # Putting both terminals on the same side is NOT the differential probe.
        self.assertFalse(np.allclose(Z_d, Z_c, rtol=1e-3))


# ============================================================================
# 8 -- legacy A/B equivalence, stated explicitly
# ============================================================================

class TestLegacyEquivalence(_CouplingTestCase):
    """A hand-built Signal("A")/Signal("B") set is bit-identical to mode 2, and
    Signal("A", -1) is the same thing as Signal("B")."""

    def setUp(self):
        _ensure_fixtures()
        self.freqs, self.Y = _load("pi_2port.s2p")

    def _z(self, term) -> np.ndarray:
        z, _ = compute_z(self.Y, self.freqs, term)
        return z

    def test_hand_built_AB_equals_mode2(self):
        hand = TerminationSet(per_port={0: Signal("A"), 1: Signal("B")})
        assert_array_equal(self._z(hand),
                           self._z(build_terminations_mode2([1], [2], [])))

    def test_signal_A_minus_equals_signal_B(self):
        b_style = TerminationSet(per_port={0: Signal("A"), 1: Signal("B")})
        signed = TerminationSet(per_port={0: Signal("A"), 1: Signal("A", -1)})
        assert_array_equal(self._z(b_style), self._z(signed))

    def test_legacy_B_resolves_to_the_minus_side_of_A(self):
        mports = resolve_meas_ports(
            TerminationSet(per_port={0: Signal("A"), 1: Signal("B")}), 2)
        self.assertEqual(mports, [MeasPort(name="A", plus=[0], minus=[1])])
        # Signal("B", -1) is the plus side of A (double negation).
        mports2 = resolve_meas_ports(
            TerminationSet(per_port={0: Signal("B", -1), 1: Signal("B")}), 2)
        self.assertEqual(mports2, [MeasPort(name="A", plus=[0], minus=[1])])

    def test_compute_z_is_the_first_ports_self_impedance(self):
        """compute_z stays the G == 1 scalar path and equals Zmat[:, 0, 0]."""
        term = build_terminations_mode2([1], [2], [])
        Zmat, names, _ = compute_z_matrix(self.Y, self.freqs, term)
        self.assertEqual(names, ["A"])
        self.assertEqual(Zmat.shape, (len(self.freqs), 1, 1))
        assert_array_equal(self._z(term), Zmat[:, 0, 0])

    def test_multi_port_measurement_ordering_is_deterministic(self):
        """Group order follows first appearance scanning ports ascending, not
        dict insertion order."""
        ts = TerminationSet(per_port={2: Signal("z"), 0: Signal("y"),
                                      1: Signal("z", -1)})
        self.assertEqual([mp.name for mp in resolve_meas_ports(ts, 3)],
                         ["y", "z"])


# ============================================================================
# 9 -- parse_mport_spec
# ============================================================================

class TestParseMportSpec(unittest.TestCase):
    def test_accepted_forms(self):
        cases = [
            ("tank = 1,3 / 2,4", ("tank", [1, 3], [2, 4])),
            ("1 / 2", ("", [1], [2])),
            ("rx = 5:1:9 /", ("rx", [5, 6, 7, 8, 9], [])),
            ("3,4", ("", [3, 4], [])),
            # Ranges keep working on both sides.
            ("nw = 6-14 / 35:1:37",
             ("nw", [6, 7, 8, 9, 10, 11, 12, 13, 14], [35, 36, 37])),
            # Surrounding whitespace is irrelevant.
            ("   spam   =  2 /  7   ", ("spam", [2], [7])),
        ]
        for spec, expected in cases:
            with self.subTest(spec=spec):
                self.assertEqual(parse_mport_spec(spec), expected)

    def test_empty_plus_side_rejected(self):
        for spec in ("/ 2", " / 1,2", ""):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    parse_mport_spec(spec)

    def test_port_on_both_sides_rejected(self):
        with self.assertRaises(ValueError) as cm:
            parse_mport_spec("1,2 / 2,3")
        self.assertIn("both", str(cm.exception))

    def test_two_slashes_rejected(self):
        with self.assertRaises(ValueError) as cm:
            parse_mport_spec("1/2/3")
        self.assertIn("'/'", str(cm.exception))

    def test_reserved_names_rejected(self):
        for name in ("A", "a", "B", "b"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as cm:
                    parse_mport_spec(f"{name} = 1 / 2")
                self.assertIn("reserved", str(cm.exception))

    def test_builder_round_trips_the_parsed_spec(self):
        specs = ["tank = 1,3 / 2,4", "rx = 5 /"]
        term = build_terminations_coupling(
            [parse_mport_spec(s) for s in specs])
        self.assertEqual(term.per_port[0], Signal("tank", +1))
        self.assertEqual(term.per_port[2], Signal("tank", +1))
        self.assertEqual(term.per_port[1], Signal("tank", -1))
        self.assertEqual(term.per_port[3], Signal("tank", -1))
        self.assertEqual(term.per_port[4], Signal("rx", +1))

    def test_builder_rejects_conflicts_and_auto_names(self):
        with self.assertRaises(ValueError):
            build_terminations_coupling([])
        with self.assertRaises(ValueError):
            build_terminations_coupling([("x", [1], [2]), ("y", [2], [3])])
        with self.assertRaises(ValueError):
            build_terminations_coupling([("x", [1], []), ("x", [2], [])])
        with self.assertRaises(ValueError):
            build_terminations_coupling([("A", [1], [2])])
        # Auto-naming skips names already taken by an explicit entry.
        term = build_terminations_coupling([("P1", [1], []), ("", [2], [])])
        self.assertEqual(term.per_port[0], Signal("P1", +1))
        self.assertEqual(term.per_port[1], Signal("P2", +1))


# ============================================================================
# 10 -- extract_coupling_at_freq edge cases
# ============================================================================

class TestExtractCouplingEdgeCases(unittest.TestCase):
    F = 1.0e9

    @property
    def W(self) -> float:
        return 2.0 * math.pi * self.F

    def _zmat(self, z00: complex, z11: complex, z01: complex) -> np.ndarray:
        Z = np.zeros((1, 2, 2), dtype=complex)
        Z[0, 0, 0] = z00
        Z[0, 1, 1] = z11
        Z[0, 0, 1] = Z[0, 1, 0] = z01
        return Z

    def test_k_is_nan_and_annotated_when_a_self_L_is_negative(self):
        """Past SRF the port is capacitive; k has no meaning there."""
        Z = self._zmat(1.0 - 10.0j, 1.0 + 20.0j, 5.0j)
        res = extract_coupling_at_freq(np.array([self.F]), Z, ["a", "b"], self.F)
        pair = res.pairs[0]
        self.assertTrue(math.isnan(pair.k))
        self.assertIn("L_a <= 0 at this frequency (past SRF): k undefined",
                      pair.notes)
        # L is reported signed, not clipped away.
        self.assertLess(res.ports[0].L_henry, 0.0)
        # M itself is still perfectly well defined.
        self.assertFalse(math.isnan(pair.M_henry))
        assert_allclose(pair.M_henry, 5.0 / self.W, rtol=1e-12)

    def test_note_names_the_offending_side(self):
        """L_b <= 0 must blame b, not a."""
        Z = self._zmat(1.0 + 20.0j, 1.0 - 10.0j, 5.0j)
        pair = extract_coupling_at_freq(np.array([self.F]), Z,
                                        ["a", "b"], self.F).pairs[0]
        self.assertIn("L_b <= 0 at this frequency (past SRF): k undefined",
                      pair.notes)
        self.assertNotIn("L_a <= 0 at this frequency (past SRF): k undefined",
                         pair.notes)

    def test_k_greater_than_one_warns_but_does_not_clamp(self):
        La, Lb = 2e-9, 3e-9
        M = 1.5 * math.sqrt(La * Lb)
        Z = self._zmat(1.0 + 1j * self.W * La, 1.0 + 1j * self.W * Lb,
                       1j * self.W * M)
        pair = extract_coupling_at_freq(np.array([self.F]), Z,
                                        ["a", "b"], self.F).pairs[0]
        assert_allclose(pair.k, 1.5, rtol=1e-9)
        self.assertIn("|k| > 1: check the input S-parameters", pair.notes)

    def test_single_measurement_port_has_zero_reciprocity_error(self):
        Z = np.zeros((3, 1, 1), dtype=complex)
        Z[:, 0, 0] = 1.0 + 2.0j
        res = extract_coupling_at_freq(np.array([1e9, 2e9, 3e9]), Z,
                                       ["only"], 2.1e9)
        self.assertEqual(res.reciprocity_error, 0.0)
        self.assertEqual(res.pairs, [])
        self.assertEqual(len(res.ports), 1)
        self.assertEqual(res.freq_hz, 2e9)     # nearest frequency, not 2.1e9

    def test_shape_validation(self):
        Z = self._zmat(1.0j, 1.0j, 0.5j)
        with self.assertRaises(ValueError):
            extract_coupling_at_freq(np.array([self.F]), Z, ["only"], self.F)
        with self.assertRaises(ValueError):
            extract_coupling_at_freq(np.array([self.F]), Z[0], ["a", "b"], self.F)
        with self.assertRaises(ValueError):
            extract_coupling_at_freq(np.array([]), Z, ["a", "b"], self.F)

    def test_result_matrix_is_a_copy_not_a_view(self):
        Z = self._zmat(1.0j, 2.0j, 0.5j)
        res = extract_coupling_at_freq(np.array([self.F]), Z, ["a", "b"], self.F)
        res.Z_matrix[0, 0] = 999.0
        self.assertEqual(Z[0, 0, 0], 1.0j)


# ============================================================================
# 11 -- the extended custom (Mode 5) DSL
# ============================================================================

class TestCouplingDSL(_CouplingTestCase):
    def test_signed_groups_equal_the_builder(self):
        dsl = parse_custom_termination_text(
            "1 signal tank +\n"
            "2 signal tank -\n"
            "3 signal rx +\n"
            "4 signal rx -\n"
        )
        built = build_terminations_coupling([("tank", [1], [2]),
                                            ("rx", [3], [4])])
        self.assertEqual(dsl.per_port, built.per_port)
        self.assertEqual(dsl.couplings, built.couplings)

        freqs, Y = _load("coupled_4port_diff.s4p")
        Z_dsl, names_dsl, _ = compute_z_matrix(Y, freqs, dsl)
        Z_built, names_built, _ = compute_z_matrix(Y, freqs, built)
        self.assertEqual(names_dsl, ["tank", "rx"])
        self.assertEqual(names_dsl, names_built)
        assert_array_equal(Z_dsl, Z_built)

    def test_default_sign_is_plus(self):
        dsl = parse_custom_termination_text("1 signal rx\n2 signal rx +\n")
        self.assertEqual(dsl.per_port[0], Signal("rx", +1))
        self.assertEqual(dsl.per_port[1], Signal("rx", +1))

    def test_legacy_signal_B_still_means_minus_side_of_A(self):
        freqs, Y = _load("pi_2port.s2p")
        legacy = parse_custom_termination_text("1 signal A\n2 signal B\n")
        signed = parse_custom_termination_text("1 signal A\n2 signal A -\n")
        z_legacy, _ = compute_z(Y, freqs, legacy)
        z_signed, _ = compute_z(Y, freqs, signed)
        z_mode2, _ = compute_z(Y, freqs, build_terminations_mode2([1], [2], []))
        assert_array_equal(z_legacy, z_mode2)
        assert_array_equal(z_signed, z_mode2)
        # Lower case legacy names are folded to the canonical A / B.
        lower = parse_custom_termination_text("1 signal a\n2 signal b\n")
        self.assertEqual(lower.per_port, legacy.per_port)

    def test_bad_sign_token_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_custom_termination_text("1 signal tank *\n")
        with self.assertRaises(ValueError):
            parse_custom_termination_text("1 signal tank + extra\n")


# ============================================================================
# 12 -- one measurement port on the EXACTLY singular floating fixture
# ============================================================================

class TestFloatingSingleMeasurementPort(_CouplingTestCase):
    """
    G == 1 on the fully floating fixture.  Y2 = [[y, -y], [-y, y]] is
    mathematically singular, but LAPACK's LU gives it det ~ 1e-19 rather than
    exactly 0, so np.linalg.inv returns a ~1e16 garbage matrix instead of
    raising -- and Z2[0,0] + Z2[1,1] - Z2[0,1] - Z2[1,0] is then the difference
    of four huge numbers.  The result used to wander 1.27 / 4.77 / 2.12 / 3.18
    / 2.23 nH across the band instead of sitting flat at L1 = 2 nH.
    """

    def setUp(self):
        self.freqs, self.Y = _load("coupled_4port_float.s4p")
        self.w = 2.0 * math.pi * np.asarray(self.freqs, dtype=float)

    def test_single_differential_port_is_flat_and_exact(self):
        Z, warns = compute_z(
            self.Y, self.freqs,
            build_terminations_coupling([("w1", [1], [2])]))
        self.assertFalse(bool(np.any(np.isnan(Z))), "Z went NaN")
        ref = gen.COUPLED_R1 + 1j * self.w * gen.COUPLED_L1
        self.assertLess(_rel(Z, ref), 1e-11)
        L = Z.imag / self.w
        assert_allclose(L, gen.COUPLED_L1, rtol=1e-11)
        assert_allclose(Z.real, gen.COUPLED_R1, rtol=1e-11)
        # The singularity is reported, not swallowed.
        self.assertTrue(any("Rank-deficient" in w for w in warns), warns)

    def test_legacy_mode2_gets_the_same_answer(self):
        """The A/B builder is the same measurement, so it must agree exactly."""
        Z_new, _ = compute_z(
            self.Y, self.freqs,
            build_terminations_coupling([("w1", [1], [2])]))
        Z_legacy, _ = compute_z(self.Y, self.freqs,
                                build_terminations_mode2([1], [2], []))
        assert_array_equal(Z_new, Z_legacy)

    def test_agrees_with_the_two_port_pinv_branch(self):
        """
        The G == 1 shortcut and the general G >= 2 contraction are two code
        paths for the same physical quantity; adding a second measurement port
        must not change the first one's self impedance.
        """
        Z1, _ = compute_z(self.Y, self.freqs,
                          build_terminations_coupling([("w1", [1], [2])]))
        Zmat, names, _ = compute_z_matrix(
            self.Y, self.freqs,
            build_terminations_coupling([("w1", [1], [2]),
                                         ("w2", [3], [4])]))
        self.assertEqual(names[0], "w1")
        self.assertLess(_rel(Z1, Zmat[:, 0, 0]), 1e-11)

    def test_healthy_two_port_still_takes_the_legacy_inv_path(self):
        """
        The cheap 2x2 singularity test must not fire on a non-degenerate
        network: the diff fixture keeps a shunt C, so Y2 is invertible and the
        historical expression stays in charge (this is what the golden
        regression pins).
        """
        freqs, Y = _load("coupled_4port_diff.s4p")
        Z, warns = compute_z(
            Y, freqs, build_terminations_coupling([("c1", [1], [2])]))
        self.assertEqual(warns, [])
        self.assertFalse(bool(np.any(np.isnan(Z))))


# ============================================================================
# 13 -- probes with no return path
# ============================================================================

class TestNoReturnPath(_CouplingTestCase):
    """
    pinv is the right inverse only for probe vectors that lie in
    range(Y_node).  For anything else it invents a finite minimum-norm answer:
    Z_series/4 for a floating pair probed single-ended, a flat 0 ohm for a
    floating series element.  Those must come back NaN with a warning that does
    not read as reassurance.
    """

    F = 1.0e9

    def _floating_two_port(self):
        """Y = [[y, -y], [-y, y]]: a series element with no path to ground."""
        y = 1.0 / (5.0 + 1j * 2.0 * math.pi * self.F * 1e-9)
        return np.array([1.0e9]), np.array([[[y, -y], [-y, y]]])

    def test_single_ended_probes_on_a_floating_pair_are_nan(self):
        freqs, Y = self._floating_two_port()
        Zmat, names, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("p1", [1], []),
                                                   ("p2", [2], [])]))
        self.assertTrue(bool(np.all(np.isnan(Zmat.real))), Zmat)
        self.assertTrue(bool(np.all(np.isnan(Zmat.imag))), Zmat)
        self.assertTrue(any("no return path" in w for w in warns), warns)
        for w in warns:
            if "no return path" in w:
                self.assertIn("'p1'", w)
                self.assertIn("'p2'", w)

    def test_a_valid_probe_keeps_its_exact_value_beside_a_bad_one(self):
        """
        A common-mode probe on a floating structure has no return path, but the
        differential probe next to it is a perfectly good measurement and must
        come back exact.  Tying ports 1 and 2 together SHORTS winding 1, so the
        reference is the shorted-primary impedance of winding 2 -- not its
        open-primary impedance.
        """
        freqs, Y = _load("coupled_4port_float.s4p")
        Zmat, names, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("cm", [1, 2], []),
                                                   ("w2", [3], [4])]))
        self.assertEqual(names, ["cm", "w2"])
        k = _idx_at(freqs, F_TEST)
        w = 2.0 * math.pi * float(freqs[k])
        z1 = gen.COUPLED_R1 + 1j * w * gen.COUPLED_L1
        z2 = gen.COUPLED_R2 + 1j * w * gen.COUPLED_L2
        ref = z2 + (w * gen.COUPLED_M) ** 2 / z1
        assert_allclose(Zmat[k, 1, 1].real, ref.real, rtol=1e-11)
        assert_allclose(Zmat[k, 1, 1].imag, ref.imag, rtol=1e-11)
        # ...and the undefined port is NaN in both parts, not 0.
        self.assertTrue(math.isnan(Zmat[k, 0, 0].real))
        self.assertTrue(math.isnan(Zmat[k, 0, 0].imag))
        self.assertTrue(any("no return path" in x and "'cm'" in x
                            for x in warns), warns)

    def test_reciprocity_is_not_poisoned_by_the_nan_row(self):
        """
        The fabricated 'cm' row used to give reciprocity_error = 0.587, which
        the GUI renders as 'the input S-parameters are suspect' -- blaming the
        data for a numerical artefact.
        """
        freqs, Y = _load("coupled_4port_float.s4p")
        Zmat, names, _ = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("cm", [1, 2], []),
                                                   ("w2", [3], [4])]))
        res = extract_coupling_at_freq(freqs, Zmat, names, F_TEST)
        self.assertFalse(math.isnan(res.reciprocity_error))
        self.assertLessEqual(res.reciprocity_error, RECIPROCITY_WARN)

    def test_undefined_port_is_annotated_not_blamed_on_srf(self):
        freqs, Y = _load("coupled_4port_float.s4p")
        Zmat, names, _ = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("cm", [1, 2], []),
                                                   ("w2", [3], [4])]))
        pair = extract_coupling_at_freq(freqs, Zmat, names, F_TEST).pairs[0]
        self.assertTrue(any("no return path" in n for n in pair.notes),
                        pair.notes)
        self.assertFalse(any("past SRF" in n for n in pair.notes), pair.notes)

    def test_infinite_impedance_is_not_reported_as_a_short(self):
        """
        3-port: a floating series element on 1-2 plus a 250 ohm resistor to
        ground on port 3.  Probing port 1 single-ended used to report a perfect
        0 ohm short; port 3 is unaffected and must still read 250 ohm.
        """
        y = 1.0 / (5.0 + 1j * 2.0 * math.pi * self.F * 1e-9)
        Y = np.zeros((1, 3, 3), dtype=complex)
        Y[0, 0, 0] = Y[0, 1, 1] = y
        Y[0, 0, 1] = Y[0, 1, 0] = -y
        Y[0, 2, 2] = 1.0 / 250.0
        Zmat, names, warns = compute_z_matrix(
            np.array([self.F]) * 0 + Y, np.array([self.F]),
            build_terminations_coupling([("flo", [1], []), ("gref", [3], [])]))
        self.assertTrue(math.isnan(Zmat[0, 0, 0].real))
        assert_allclose(Zmat[0, 1, 1].real, 250.0, rtol=1e-12)
        assert_allclose(Zmat[0, 1, 1].imag, 0.0, atol=1e-9)
        self.assertTrue(any("no return path" in w and "'flo'" in w
                            for w in warns), warns)

    def test_balanced_probes_are_never_flagged(self):
        """The normal floating coupled-inductor case must stay clean."""
        freqs, Y = _load("coupled_4port_float.s4p")
        _, _, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("c1", [1], [2]),
                                                   ("c2", [3], [4])]))
        self.assertFalse(any("no return path" in w for w in warns), warns)
        self.assertTrue(any("Rank-deficient" in w for w in warns), warns)

    def test_schur_collapse_is_reported(self):
        """
        Single-ended probes on the floating fixture cancel the Schur step down
        to roundoff (|Y_red| ~ 1e-16 of the terms that built it).  The values
        that come out are noise; the tool must say so.  Advisory only -- the
        numbers are still returned.
        """
        freqs, Y = _load("coupled_4port_float.s4p")
        _, _, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("c1", [1], []),
                                                   ("c2", [3], [])]))
        self.assertTrue(any("cancelled to roundoff" in w for w in warns), warns)

    def test_a_non_finite_frequency_point_does_not_abort_the_sweep(self):
        """
        One bad data point in a source file must NaN its own frequency, not
        raise LinAlgError out of the whole reduction.
        """
        F = 5
        freqs = np.linspace(1e9, 5e9, F)
        rng = np.random.default_rng(3)
        Y = (rng.normal(size=(F, 4, 4)) + 1j * rng.normal(size=(F, 4, 4))) * 0.02
        Y = Y + np.swapaxes(Y, 1, 2)
        Y[2, 1, 1] = np.nan
        for spec in ([("w1", [1], [2])],
                     [("w1", [1], [2]), ("w2", [3], [4])]):
            with self.subTest(n_mports=len(spec)):
                Zmat, _, _ = compute_z_matrix(
                    Y, freqs, build_terminations_coupling(spec))
                finite = [bool(np.isfinite(Zmat[k]).all()) for k in range(F)]
                self.assertEqual(finite, [True, True, False, True, True])

    def test_an_open_reading_on_the_unchecked_branch_reaches_the_user(self):
        """
        The G == 1 / no-minus-side branch deliberately has no magnitude test --
        y_eff also crosses zero at a genuine parallel anti-resonance, and no
        single-frequency test can tell that apart from a probe with no return
        path.  That is about the NUMBERS, and it stands.

        What did not stand is where the one diagnostic it produced went:
        `1.0 / y_eff` raised numpy's own "divide by zero" / "invalid value"
        RuntimeWarning on stderr, which a double-clicked GUI discards, while
        the results pane printed the roundoff as a formatted measurement
        ("3.6e+03 TOhm  -11.5 MH  2.21e-10 fF") with no annotation at all.
        warnings_out is the channel the GUI prints under "Calculate @ ..." and
        the CLI reports.
        """
        freqs, Y = _load("coupled_4port_float.s4p")
        term = build_terminations_mode1([1], [])       # the reported case
        with warnings.catch_warnings():
            # A numpy RuntimeWarning escaping here IS the old behaviour: it
            # goes to fd 2, and a double-clicked GUI has no fd 2.
            warnings.simplefilter("error")
            Z, warns = compute_z(Y, freqs, term)
        self.assertEqual(int(np.sum(~np.isfinite(Z))), 1,
                         "the reading is supposed to stay infinite")
        hit = [w for w in warns if "open circuit" in w]
        self.assertTrue(hit, warns)
        self.assertIn("'A'", hit[0])                    # names the probe
        self.assertIn("freq[", hit[0])                  # ...and the frequency
        # It names BOTH readings, because this branch deliberately has no
        # magnitude test and so genuinely cannot tell them apart.
        self.assertIn("anti-resonance", hit[0])
        self.assertIn("no return path", hit[0])

    def test_the_numbers_on_that_branch_did_not_move(self):
        """errstate and a warning, not a threshold. The invariant stands: a
        huge Z is the honest reading of a parallel anti-resonance."""
        freqs, Y = _load("coupled_4port_float.s4p")
        Z, _ = compute_z(Y, freqs, build_terminations_mode1([1], []))
        finite = Z[np.isfinite(Z)]
        self.assertGreater(float(np.abs(finite).min()), 1e15)

    def test_a_finite_reading_on_that_branch_says_nothing(self):
        """A ground-referenced probe on a healthy file must stay silent."""
        freqs, Y = _load("diff_pair_4port.s4p")
        _, _, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("p1", [1], [])]))
        self.assertFalse(any("open circuit" in w for w in warns), warns)

    def test_the_open_warning_is_capped_like_the_others(self):
        """One line per frequency on a 5000-point sweep is not a report."""
        freqs = np.linspace(1e9, 2e9, 40)
        Y = np.zeros((len(freqs), 1, 1), dtype=complex)     # y_eff == 0 always
        _, _, warns = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("solo", [1], [])]))
        n = len([w for w in warns if "open circuit" in w])
        self.assertGreaterEqual(n, 1)
        self.assertLessEqual(n, 3)

    def test_healthy_files_never_report_a_collapse(self):
        for fname, spec in (
            ("coupled_4port_diff.s4p", [("c1", [1], [2]), ("c2", [3], [4])]),
            ("coupled_4port_float.s4p", [("c1", [1], [2]), ("c2", [3], [4])]),
            ("diff_pair_4port.s4p", [("c1", [1], [2])]),
        ):
            with self.subTest(fixture=fname):
                freqs, Y = _load(fname)
                _, _, warns = compute_z_matrix(
                    Y, freqs, build_terminations_coupling(spec))
                self.assertFalse(any("cancelled to roundoff" in w
                                     for w in warns), warns)


# ============================================================================
# 14 -- port-index and ground-clash validation
# ============================================================================

class TestPortIndexValidation(_CouplingTestCase):
    """
    A port number the file does not have used to be silently dropped by the
    resolver, quietly demoting '3 / 5' on a 4-port file to a ground-referenced
    probe and reporting a plausible wrong number.
    """

    def test_compute_z_matrix_rejects_an_out_of_range_probe(self):
        freqs, Y = _load("coupled_4port_diff.s4p")
        term = build_terminations_coupling([("w1", [1], [2]),
                                            ("w2", [3], [5])])
        with self.assertRaises(ValueError) as cm:
            compute_z_matrix(Y, freqs, term)
        self.assertIn("5", str(cm.exception))
        self.assertIn("4 ports", str(cm.exception))

    def test_builder_rejects_it_up_front_when_given_nports(self):
        with self.assertRaises(ValueError) as cm:
            build_terminations_coupling([("w1", [1], [2]),
                                         ("w2", [3], [5])], nports=4)
        self.assertIn("5", str(cm.exception))

    def test_out_of_range_gnd_and_short_ports_are_rejected_too(self):
        freqs, Y = _load("coupled_4port_diff.s4p")
        with self.assertRaises(ValueError):
            compute_z_matrix(Y, freqs, build_terminations_mode1([1], [9]))
        with self.assertRaises(ValueError):
            compute_z_matrix(
                Y, freqs,
                build_terminations_coupling([("w", [1], [2])],
                                            short_pairs=[(3, 7)]))

    def test_in_range_ports_still_work(self):
        freqs, Y = _load("coupled_4port_diff.s4p")
        Zmat, names, _ = compute_z_matrix(
            Y, freqs, build_terminations_coupling([("w1", [1], [2]),
                                                   ("w2", [3], [4])]))
        self.assertEqual(names, ["w1", "w2"])
        self.assertEqual(Zmat.shape, (len(freqs), 2, 2))

    def test_probe_port_listed_as_ground_is_rejected(self):
        """
        Grounding one port of a probe side grounds the whole side (the side is
        tied together), so the old 'ground silently wins' precedence reported a
        non-zero impedance for a node the tool believes is at 0 V.
        """
        with self.assertRaises(ValueError) as cm:
            build_terminations_coupling([("p", [1, 2], []), ("q", [3], [])],
                                        gnd_ports=[2])
        msg = str(cm.exception)
        self.assertIn("2", msg)
        self.assertIn("'p'", msg)

    def test_ground_ports_that_are_not_probes_are_fine(self):
        term = build_terminations_coupling([("p", [1], [2])], gnd_ports=[3, 4])
        self.assertEqual(term.per_port[0], Signal("p", +1))
        self.assertEqual(term.per_port[1], Signal("p", -1))
        self.assertEqual(type(term.per_port[2]).__name__, "Ground")


# ============================================================================
# 15 -- compute_z must not silently drop measurement ports 2..G
# ============================================================================

class TestComputeZMultiPortWarning(_CouplingTestCase):
    """
    Only Mode 5 can define more than one measurement port and still call
    compute_z.  'signal V' instead of 'signal B' used to raise; now it is a
    legal (second) measurement port, and compute_z returns only port 1.
    """

    def test_extra_measurement_port_produces_a_warning(self):
        freqs, Y = _load("diff_pair_4port.s4p")
        good = parse_custom_termination_text(
            "1 signal A\n2 signal B\n3 gnd\n4 gnd\n")
        typo = parse_custom_termination_text(
            "1 signal A\n2 signal V\n3 gnd\n4 gnd\n")
        z_good, w_good = compute_z(Y, freqs, good)
        z_typo, w_typo = compute_z(Y, freqs, typo)
        self.assertEqual(w_good, [])
        self.assertTrue(w_typo, "a second measurement port went unreported")
        joined = " ".join(w_typo)
        self.assertIn("'A'", joined)
        self.assertIn("'V'", joined)
        # ...and the numbers really do differ, which is why it needs saying.
        self.assertFalse(np.allclose(z_good, z_typo, rtol=1e-3))

    def test_single_measurement_port_is_still_warning_free(self):
        freqs, Y = _load("diff_pair_4port.s4p")
        _, warns = compute_z(Y, freqs, build_terminations_mode2([1], [2], [3, 4]))
        self.assertEqual(warns, [])


class TestThreeMeasurementPorts(unittest.TestCase):
    """
    G >= 3.  Nothing in the pipeline hardcodes two measurement ports, but until
    the connection table gave the GUI a '+ Add' button nobody could easily make
    a third, and EVERY other test in this file uses one or two -- so the G >= 3
    path had zero coverage.

    Three ground-referenced probes on a 4-port fixture, with the fourth port
    grounded, is the cheapest honest G = 3 case.
    """

    THREE = [("p1", [1], []), ("p2", [2], []), ("p3", [3], [])]
    FIXTURES = ("coupled_4port_diff.s4p", "decap_4port.s4p",
                "diff_pair_4port.s4p")

    def _z(self, fixture, mports):
        ts = parse_touchstone(FIX / fixture)
        Y = s_to_y(ts.s, ts.z0)
        Zmat, names, warns = compute_z_matrix(
            Y, ts.freqs, build_terminations_coupling(mports, gnd_ports=[4]))
        return ts, Zmat, names, warns

    def test_three_ports_produce_a_3x3_matrix(self):
        ts, Zmat, names, _w = self._z(self.FIXTURES[0], self.THREE)
        self.assertEqual(Zmat.shape, (len(ts.freqs), 3, 3))
        self.assertEqual(names, ["p1", "p2", "p3"])

    def test_matrix_is_symmetric(self):
        for fx in self.FIXTURES:
            with self.subTest(fixture=fx):
                _ts, Zmat, _n, _w = self._z(fx, self.THREE)
                scale = float(np.max(np.abs(Zmat)))
                asym = float(np.max(np.abs(Zmat - np.transpose(Zmat, (0, 2, 1)))))
                self.assertLess(asym / scale, 1e-12,
                                f"Z is not symmetric: rel {asym / scale:.2e}")

    def test_subblock_equals_the_standalone_pair(self):
        """
        The defining property of the OPEN-CIRCUIT matrix.

        Z[a, b] is defined with every OTHER measurement port carrying no
        current, so the 2x2 sub-block of a G = 3 matrix must equal the G = 2
        matrix computed with that third port simply left open.  If the G >= 3
        contraction were wrong, this is what would catch it.

        Tolerance is per-frequency and relative.  Measured: the median relative
        error is ~3e-14 and p99 is ~9e-11, i.e. floating point.  ONE frequency
        of 401 on diff_pair_4port.s4p reaches 8e-7 -- the 1 MHz point, where
        the structure is essentially an open (|Z| ~ 8e7 ohm), the node
        admittance is near-singular, and pinv's rcond truncation therefore
        depends on the size of the matrix being inverted, which is exactly what
        differs between the G = 3 and G = 2 problems.  The disagreement is in
        the REAL part, of a point whose R is already negative and flagged
        non-passive.  Hence: p99 pinned tight, max pinned loose but finite.
        """
        for fx in self.FIXTURES:
            with self.subTest(fixture=fx):
                _ts, Z3, _n, _w = self._z(fx, self.THREE)
                _ts2, Z2, _n2, _w2 = self._z(fx, self.THREE[:2])
                mag = np.maximum(np.abs(Z2).max(axis=(1, 2)), 1e-30)
                rel = np.abs(Z3[:, :2, :2] - Z2).max(axis=(1, 2)) / mag
                self.assertLess(float(np.percentile(rel, 99)), 1e-9,
                                f"p99 rel err {np.percentile(rel, 99):.2e}")
                self.assertLess(float(rel.max()), 1e-5,
                                f"max rel err {rel.max():.2e}")

    def test_three_ports_give_three_pairs(self):
        ts, Zmat, names, _w = self._z(self.FIXTURES[0], self.THREE)
        cres = extract_coupling_at_freq(ts.freqs, Zmat, names, F_TEST)
        self.assertEqual([p.name for p in cres.ports], ["p1", "p2", "p3"])
        self.assertEqual([(p.name_a, p.name_b) for p in cres.pairs],
                         [("p1", "p2"), ("p1", "p3"), ("p2", "p3")])

    def test_reciprocity_is_reported_and_finite(self):
        ts, Zmat, names, _w = self._z(self.FIXTURES[0], self.THREE)
        cres = extract_coupling_at_freq(ts.freqs, Zmat, names, F_TEST)
        self.assertTrue(math.isfinite(cres.reciprocity_error))
        self.assertLess(cres.reciprocity_error, RECIPROCITY_WARN)

    def test_a_fourth_port_still_works(self):
        """G = 4 on a 4-port file: nothing caps the count at three either."""
        ts = parse_touchstone(FIX / "decap_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        four = [("p1", [1], []), ("p2", [2], []),
                ("p3", [3], []), ("p4", [4], [])]
        Zmat, names, _w = compute_z_matrix(
            Y, ts.freqs, build_terminations_coupling(four))
        self.assertEqual(Zmat.shape, (len(ts.freqs), 4, 4))
        cres = extract_coupling_at_freq(ts.freqs, Zmat, names, F_TEST)
        self.assertEqual(len(cres.pairs), 6)      # 4*3/2


if __name__ == "__main__":
    unittest.main()
