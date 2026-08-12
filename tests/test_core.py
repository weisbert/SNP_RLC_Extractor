"""
Tests for the rest of pkg_rlc_core: S<->Y conversion, compute_z with various
terminations, RLC extraction, broadband fitting, VDD<->Ground equivalence,
and Schur singularity fallback.
"""

from __future__ import annotations

import math
import sys
import unittest
import warnings
from pathlib import Path

# Make pkg_rlc_core importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from pkg_rlc_core import (  # noqa: E402
    parse_touchstone,
    s_to_y, y_to_s,
    compute_z,
    extract_rlc_at_freq,
    fit_inductor, fit_capacitor, fit_auto,
    build_terminations_mode1, build_terminations_mode2,
    build_terminations_mode3, build_terminations_mode4,
    build_terminations_coupling,
    parse_custom_termination_text,
    parse_short_pairs,
    TerminationSet, Signal, Ground, Open, Vdd, LumpedToGnd,
    y_capacitor, y_series_rlc,
    compute_z_matrix,
    format_si,
    DEFAULT_Z0,
)


FIX = Path(__file__).resolve().parent / "fixtures"


def _ensure_fixtures() -> None:
    needed = [
        "shunt_rl_1port.s1p",
        "shunt_c_1port.s1p",
        "pi_2port.s2p",
        "diff_pair_4port.s4p",
    ]
    if all((FIX / n).exists() for n in needed):
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


class TestSYConversion(unittest.TestCase):
    def test_round_trip(self):
        """y_to_s(s_to_y(S)) should equal S to within 1e-10."""
        rng = np.random.default_rng(0xC0FFEE)
        n = 4
        nfreqs = 5
        # Construct random S with |S| < 1 to ensure invertibility on Y side.
        # We make S = 0.5*(M + M.T) scaled so spectrum stays inside unit disk.
        S = np.zeros((nfreqs, n, n), dtype=complex)
        for k in range(nfreqs):
            M = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
            # Symmetrize and scale.
            M = 0.4 * (M + M.T) / np.linalg.norm(M)
            S[k] = M
        Y = s_to_y(S, z0=DEFAULT_Z0)
        S_back = y_to_s(Y, z0=DEFAULT_Z0)
        max_err = float(np.max(np.abs(S - S_back)))
        self.assertLess(max_err, 1e-10,
                        f"Round-trip mismatch: max|dS| = {max_err:.3e}")


class TestFixtureBasedZ(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_shunt_rl_1port_mode1(self):
        ts = parse_touchstone(FIX / "shunt_rl_1port.s1p")
        Y = s_to_y(ts.s, ts.z0)
        term = build_terminations_mode1(signal_ports=[1], gnd_ports=[])
        Z, _ = compute_z(Y, ts.freqs, term)
        res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
        # Expect R=0.5, L=1nH within 0.1%
        self.assertAlmostEqual(res.R_ohm, 0.5, delta=0.5 * 1e-3)
        self.assertAlmostEqual(res.L_henry, 1e-9, delta=1e-9 * 1e-3)

    def test_pi_2port_mode2(self):
        ts = parse_touchstone(FIX / "pi_2port.s2p")
        Y = s_to_y(ts.s, ts.z0)
        term = build_terminations_mode2(port_a=[1], port_b=[2], gnd_ports=[])
        Z, _ = compute_z(Y, ts.freqs, term)
        res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
        # Expect R~1, L~1nH within 1%
        self.assertAlmostEqual(res.R_ohm, 1.0, delta=1.0 * 1e-2)
        self.assertAlmostEqual(res.L_henry, 1e-9, delta=1e-9 * 1e-2)

    def test_diff_pair_4port_mode3(self):
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        term = build_terminations_mode3(port_a=[1], port_b=[2], gnd_ports=[],
                                        short_pairs=[(3, 4)])
        Z, _ = compute_z(Y, ts.freqs, term)
        res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
        # Expected L_loop = 2*(L_self - M) = 2*(5-1)=8nH within 1%
        self.assertAlmostEqual(res.L_henry, 8e-9, delta=8e-9 * 1e-2)


class TestModeEquivalence(unittest.TestCase):
    """build_terminations_mode1 == hand-built TerminationSet of Signal+Ground."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_mode1_matches_hand_built(self):
        # Use a 4-port file so we have multiple ground ports to wire.
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)

        # Builder version: signal=1, gnd=[2,3,4]
        term_builder = build_terminations_mode1(signal_ports=[1],
                                                gnd_ports=[2, 3, 4])
        # Hand-built version (0-based dict).
        term_hand = TerminationSet(per_port={
            0: Signal("A"),
            1: Ground(),
            2: Ground(),
            3: Ground(),
        })

        Z_builder, _ = compute_z(Y, ts.freqs, term_builder)
        Z_hand, _ = compute_z(Y, ts.freqs, term_hand)
        max_err = float(np.max(np.abs(Z_builder - Z_hand)))
        self.assertLess(max_err, 1e-12,
                        f"Mode1 builder vs hand-built mismatch: {max_err:.3e}")


class TestTerminationPrecedence(unittest.TestCase):
    """
    Pin what happens when one port is claimed twice -- the named modes and the
    probe model deliberately DISAGREE, and nothing used to test it.

    build_terminations_mode1/2/3 assign Signal first and Ground second into the
    same dict, so GROUND SILENTLY WINS.  build_terminations_coupling RAISES on
    the same overlap instead, because under the probe model the ports on one
    side are tied together: grounding one of them grounds the whole side, and
    reporting a plausible non-zero impedance for a node the tool believes is at
    0 V is worse than refusing.

    Both behaviours are intended and neither may drift.  The reason this class
    exists is that the golden reference does NOT guard it: tests/_golden_capture
    calls build_terminations_modeN directly, so any NEW path to a TerminationSet
    -- the GUI connection table, a preset that seeds it, a row->TerminationSet
    builder -- bypasses the golden cases entirely and could diverge here without
    a single test going red.  Anything that claims to reproduce a named mode
    must reproduce THIS, including the overlap cases.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    # ---- named modes: ground wins ------------------------------------------

    def test_mode1_ground_wins_over_signal(self):
        term = build_terminations_mode1(signal_ports=[1, 2], gnd_ports=[2, 3])
        self.assertIsInstance(term.per_port[0], Signal)   # port 1: signal only
        self.assertIsInstance(term.per_port[1], Ground)   # port 2: BOTH -> gnd
        self.assertIsInstance(term.per_port[2], Ground)   # port 3: gnd only

    def test_mode2_ground_wins_over_both_probe_sides(self):
        term = build_terminations_mode2(port_a=[1, 2], port_b=[3, 4],
                                        gnd_ports=[2, 4])
        self.assertIsInstance(term.per_port[0], Signal)
        self.assertIsInstance(term.per_port[1], Ground)   # was in A
        self.assertIsInstance(term.per_port[2], Signal)
        self.assertIsInstance(term.per_port[3], Ground)   # was in B

    def test_mode3_inherits_mode2_precedence_and_keeps_shorts(self):
        term = build_terminations_mode3(port_a=[1, 2], port_b=[3], gnd_ports=[2],
                                        short_pairs=parse_short_pairs("3-4"))
        self.assertIsInstance(term.per_port[1], Ground)
        self.assertEqual([(c.port_i, c.port_j) for c in term.couplings],
                         [(2, 3)])

    def test_mode4_vdd_wins_over_ground(self):
        """VDD is applied last. Numerically identical (Vdd evaluates as Ground)."""
        term = build_terminations_mode4(port_a=[1], port_b=[2], gnd_ports=[3],
                                        vdd_ports=[3])
        self.assertIsInstance(term.per_port[2], Vdd)

    def test_ground_wins_is_visible_in_the_answer(self):
        """
        Not just a dict-shape assertion: the overlap changes the number.

        A=[1,2] with port 2 also grounded must equal A=[1] with gnd=[2] --
        i.e. port 2 really did leave the probe.
        """
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        Z_overlap, _ = compute_z(
            Y, ts.freqs, build_terminations_mode1([1, 2], [2, 3]))
        Z_explicit, _ = compute_z(
            Y, ts.freqs, build_terminations_mode1([1], [2, 3]))
        self.assertLess(float(np.max(np.abs(Z_overlap - Z_explicit))), 1e-15)

    # ---- probe model: the same overlap raises ------------------------------

    def test_coupling_builder_raises_on_probe_ground_overlap(self):
        with self.assertRaises(ValueError) as cm:
            build_terminations_coupling([("tank", [1, 2], [3])], gnd_ports=[2])
        msg = str(cm.exception)
        self.assertIn("2", msg)
        self.assertIn("ground", msg.lower())

    def test_coupling_builder_raises_on_minus_side_overlap_too(self):
        with self.assertRaises(ValueError):
            build_terminations_coupling([("tank", [1], [2, 3])], gnd_ports=[3])

    def test_named_modes_and_probe_model_disagree_on_purpose(self):
        """The divergence itself, stated as one assertion."""
        overlap = dict(signal=[1, 2], gnd=[2])
        # Named mode: accepted, ground wins.
        term = build_terminations_mode1(overlap["signal"], overlap["gnd"])
        self.assertIsInstance(term.per_port[1], Ground)
        # Probe model: refused.
        with self.assertRaises(ValueError):
            build_terminations_coupling([("m1", overlap["signal"], [])],
                                        gnd_ports=overlap["gnd"])

    # ---- the DSL is a third path; pin it against the named modes -----------

    def test_dsl_last_line_wins_within_a_spec(self):
        """
        The DSL has no precedence rule -- it is last-assignment-wins, line by
        line.  A connection table serialising to DSL text therefore has to emit
        ground AFTER the probe to reproduce a named mode's overlap behaviour.
        """
        gnd_last = parse_custom_termination_text("1 signal A\n2 signal A\n2 ground\n")
        self.assertIsInstance(gnd_last.per_port[1], Ground)
        sig_last = parse_custom_termination_text("2 ground\n1 signal A\n2 signal A\n")
        self.assertIsInstance(sig_last.per_port[1], Signal)

    def test_dsl_reproduces_mode1_including_the_overlap(self):
        """A DSL spec written ground-last is numerically identical to mode 1."""
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        Z_mode1, _ = compute_z(
            Y, ts.freqs, build_terminations_mode1([1, 2], [2, 3]))
        Z_dsl, _ = compute_z(Y, ts.freqs, parse_custom_termination_text(
            "1,2 signal A\n"
            "2,3 ground\n"
        ))
        self.assertLess(float(np.max(np.abs(Z_mode1 - Z_dsl))), 1e-15)


class TestLumpedTermination(unittest.TestCase):
    """LumpedToGnd(C) on a port should equal physically adding jwC to Y[port,port]
    and treating that port as Open."""

    def test_lumped_to_gnd_equivalence(self):
        # Build a small synthetic 3-port admittance matrix inline.
        # Use port 1 as Signal-A (driving point), port 2 as Open,
        # and port 3 as the "lumped C-to-gnd" port we test.
        rng = np.random.default_rng(42)
        nfreqs = 7
        freqs = np.linspace(1e8, 1e10, nfreqs)
        omega = 2.0 * np.pi * freqs
        n = 3

        # Build a deterministic well-conditioned Y(f).
        # Take a complex symmetric random base matrix + a frequency-dependent
        # diagonal so it's never singular.
        base = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
        base = 0.5 * (base + base.T)  # symmetric (passive-ish)
        Y = np.zeros((nfreqs, n, n), dtype=complex)
        for k in range(nfreqs):
            Y[k] = base.copy()
            # Add freq-dependent diagonal (some shunt-Cs) for invertibility.
            for i in range(n):
                Y[k, i, i] += 1j * omega[k] * (1e-12 * (i + 1)) + 0.01

        C_test = 2e-12  # 2 pF lumped on port 3

        # ---- Approach 1: LumpedToGnd on port 3 (0-based index 2), Open elsewhere
        term_lumped = TerminationSet(per_port={
            0: Signal("A"),
            1: Open(),
            2: LumpedToGnd(y_capacitor(C_test)),
        })
        Z_lumped, _ = compute_z(Y, freqs, term_lumped)

        # ---- Approach 2: physically add jwC to Y[2,2], then treat port 3 as Open
        Y_mod = Y.copy()
        for k in range(nfreqs):
            Y_mod[k, 2, 2] += 1j * omega[k] * C_test
        term_physical = TerminationSet(per_port={
            0: Signal("A"),
            1: Open(),
            2: Open(),
        })
        Z_physical, _ = compute_z(Y_mod, freqs, term_physical)

        max_err = float(np.max(np.abs(Z_lumped - Z_physical)))
        # Use a tolerance relative to typical |Z|.
        scale = float(np.max(np.abs(Z_physical)))
        self.assertLess(max_err, 1e-9 * max(scale, 1.0),
                        f"LumpedToGnd not equivalent to physical Y mod: "
                        f"max_err={max_err:.3e}, |Z|~{scale:.3e}")


class TestFitInductor(unittest.TestCase):
    def test_fit_recovers_L_and_Rdc(self):
        """Synthetic Z = 0.5 + jw*1nH on [1e8,1e10] -> L within 0.1%, Rdc within 1%."""
        L = 1e-9
        R = 0.5
        freqs = np.linspace(1e8, 1e10, 401)
        omega = 2.0 * np.pi * freqs
        Z = R + 1j * omega * L
        fit = fit_inductor(freqs, Z, 1e8, 1e10)
        self.assertAlmostEqual(fit.L_henry, L, delta=L * 1e-3,
                               msg=f"L recovered={fit.L_henry:.6e}")
        self.assertAlmostEqual(fit.R_dc_ohm, R, delta=R * 1e-2,
                               msg=f"R_dc recovered={fit.R_dc_ohm:.6e}")


class TestFitCapacitor(unittest.TestCase):
    def test_fit_recovers_C_and_Resr(self):
        """Synthetic Z = 0.1 + 1/(jw*1pF) on [1e8,1e10] -> C within 1%, R_esr within 5%."""
        C = 1e-12
        R = 0.1
        freqs = np.linspace(1e8, 1e10, 401)
        omega = 2.0 * np.pi * freqs
        Z = R + 1.0 / (1j * omega * C)
        fit = fit_capacitor(freqs, Z, 1e8, 1e10)
        self.assertAlmostEqual(fit.C_farad, C, delta=C * 1e-2,
                               msg=f"C recovered={fit.C_farad:.6e}")
        self.assertAlmostEqual(fit.R_esr_ohm, R, delta=R * 5e-2,
                               msg=f"R_esr recovered={fit.R_esr_ohm:.6e}")


class TestFitAuto(unittest.TestCase):
    def test_auto_picks_inductor(self):
        L = 1e-9
        R = 0.5
        freqs = np.linspace(1e8, 1e10, 401)
        omega = 2.0 * np.pi * freqs
        Z = R + 1j * omega * L
        which, _ = fit_auto(freqs, Z, 1e8, 1e10)
        self.assertEqual(which, "inductor")

    def test_auto_picks_capacitor(self):
        C = 1e-12
        R = 0.1
        freqs = np.linspace(1e8, 1e10, 401)
        omega = 2.0 * np.pi * freqs
        Z = R + 1.0 / (1j * omega * C)
        which, _ = fit_auto(freqs, Z, 1e8, 1e10)
        self.assertEqual(which, "capacitor")


class TestVddEqualsGround(unittest.TestCase):
    """Substituting Vdd for Ground in any termination must not change Z(f)."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_vdd_ground_equivalence(self):
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        # All-Vdd (port 3 vdd) vs all-Ground (port 3 gnd) for mode 4 setup.
        t_vdd = build_terminations_mode4(port_a=[1], port_b=[2],
                                         gnd_ports=[], vdd_ports=[3, 4])
        t_gnd = build_terminations_mode4(port_a=[1], port_b=[2],
                                         gnd_ports=[3, 4], vdd_ports=[])
        Zv, _ = compute_z(Y, ts.freqs, t_vdd)
        Zg, _ = compute_z(Y, ts.freqs, t_gnd)
        max_err = float(np.max(np.abs(Zv - Zg)))
        self.assertLess(max_err, 1e-9,
                        f"VDD vs Ground produced different Z: max_err={max_err:.3e}")

    def test_vdd_ground_equivalence_handbuilt(self):
        """Same check but on a hand-built TerminationSet swapping Ground<->Vdd."""
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        t_g = TerminationSet(per_port={
            0: Signal("A"),
            1: Signal("B"),
            2: Ground(),
            3: Ground(),
        })
        t_v = TerminationSet(per_port={
            0: Signal("A"),
            1: Signal("B"),
            2: Vdd(),
            3: Vdd(),
        })
        Zg, _ = compute_z(Y, ts.freqs, t_g)
        Zv, _ = compute_z(Y, ts.freqs, t_v)
        max_err = float(np.max(np.abs(Zv - Zg)))
        self.assertLess(max_err, 1e-9)


class TestSchurSingularityFallback(unittest.TestCase):
    """When the open port has zero admittance, compute_z must either succeed
    (with lstsq-fallback warning) or raise a clear error - not return silent NaN.
    """

    def test_singular_open_port(self):
        # Build a 3-port Y where port 3 (idx=2) is "isolated" (Y row/col all zero).
        # This makes the Schur Y_oo block exactly zero -> singular.
        nfreqs = 5
        freqs = np.linspace(1e8, 1e10, nfreqs)
        omega = 2.0 * np.pi * freqs
        Y = np.zeros((nfreqs, 3, 3), dtype=complex)
        # Coupled 2x2 block in upper-left with shunts to ground.
        for k in range(nfreqs):
            yc = 1j * omega[k] * 1e-12
            ys = 1.0 / (1.0 + 1j * omega[k] * 1e-9)
            Y[k, 0, 0] = yc + ys
            Y[k, 0, 1] = -ys
            Y[k, 1, 0] = -ys
            Y[k, 1, 1] = yc + ys
            # Port 3 is all zeros - degenerate "open" with zero admittance.

        term = TerminationSet(per_port={
            0: Signal("A"),
            1: Ground(),
            2: Open(),  # the degenerate port
        })

        # Acceptable behaviour: either succeeds without NaN (possibly with
        # warnings), OR raises an exception. Silent NaN is NOT acceptable.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("always")
                Z, ws = compute_z(Y, freqs, term)
            # If it succeeded, the result must not be silent NaN.
            self.assertFalse(np.any(np.isnan(Z.real)) and np.any(np.isnan(Z.imag)) and not ws,
                             "compute_z returned NaN with no warning - silent failure")
            # If NaN appears, there must be at least one warning explaining why.
            if np.any(np.isnan(Z)):
                self.assertGreater(
                    len(ws), 0,
                    "Got NaN result with no diagnostic warnings"
                )
        except (ValueError, np.linalg.LinAlgError):
            # Clear error is also acceptable.
            pass


# Values shared between the fixture and the preconditions below.
_DC_FREQS = np.array([0.0, 1e9, 5.55e9, 10e9])
_DC_GND_LEAD_L = 50e-12
_DC_OPEN_LIKE = [4, 5, 6]   # 0-based, in the order compute_z_matrix eliminates


def _dc_degenerate_network(freqs: np.ndarray) -> np.ndarray:
    """
    A network that is BOTH exactly singular and non-finite in its Schur block
    at 0 Hz -- the two conditions the user's 61+37-port composed run met.

    Ports 0..3 are two probe pairs on a DC-connected R+L chain.  Port 4 reaches
    the rest of the network through a capacitor and NOTHING else, so its row
    and column of Y are exactly zero at DC; it is deliberately the FIRST
    open-like port, because LAPACK's partial pivoting has to meet the exact
    zero before the inductor's inf poisons the column it would pivot on.
    Port 6 carries the ground-lead inductance (stamped by compute_z_matrix).
    """
    n = 7
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    Y = np.zeros((len(w), n, n), dtype=complex)

    for k in range(len(w)):
        for i, j in [(0, 1), (1, 2), (2, 3), (3, 5), (5, 6)]:
            y = 1.0 / (0.5 + 1j * w[k] * 1e-9)
            Y[k, i, i] += y
            Y[k, j, j] += y
            Y[k, i, j] -= y
            Y[k, j, i] -= y
        for i in range(n):
            Y[k, i, i] += 1j * w[k] * 50e-15
        yc = 1j * w[k] * 100e-15
        Y[k, 4, 4] += yc
        Y[k, 2, 2] += yc
        Y[k, 4, 2] -= yc
        Y[k, 2, 4] -= yc
    return Y


def _dc_degenerate_term() -> TerminationSet:
    return TerminationSet(per_port={
        0: Signal("AGG", +1), 1: Signal("AGG", -1),
        2: Signal("VIC", +1), 3: Signal("VIC", -1),
        4: Open(),                      # DC-isolated -> exact zero row at DC
        5: Open(),
        6: LumpedToGnd(y_series_rlc(0.0, _DC_GND_LEAD_L, math.inf)),
    })


class TestOneBadFrequencyDoesNotAbortTheSweep(unittest.TestCase):
    """
    A lumped L to ground is y = 1/(jwL), which is inf+nanj at w == 0, and a
    file that carries a DC point (every composed sweep keeps 0 Hz) also has
    ports that reach the network only capacitively, whose Y row is exactly
    zero there.  Both are ORDINARY.  Together they make Y_oo singular AND
    non-finite at index 0, so np.linalg.solve raises "Singular matrix" and the
    lstsq fallback then raises "SVD did not converge in Linear Least Squares".

    The defeating mutation is deleting the try/except around
    `np.linalg.lstsq` in compute_z_matrix's per-frequency Schur fallback:
    every test in this class then dies with an uncaught LinAlgError, which is
    what a 98-port package run did at index 0 while all 2000 other frequencies
    were healthy.  Same rule and same reason as _probe_impedance's guard on
    its own SVD -- one bad frequency must NaN that frequency, never the sweep.

    TestSchurSingularityFallback above does NOT cover this: its Y_oo is
    singular but FINITE, so lstsq succeeds and the guarded line is never
    reached -- and it accepts a raise as correct behaviour anyway.
    """

    def _dc_schur_block(self) -> np.ndarray:
        """The Y_oo LAPACK is actually handed at 0 Hz, inductor stamp included."""
        Y = _dc_degenerate_network(_DC_FREQS)
        ix = np.array(_DC_OPEN_LIKE)
        Y_oo = Y[0][np.ix_(ix, ix)].copy()
        with np.errstate(divide="ignore", invalid="ignore"):
            Y_oo[2, 2] += y_series_rlc(0.0, _DC_GND_LEAD_L, math.inf)(
                np.array([0.0]))[0]
        return Y_oo

    def test_the_fixture_really_reaches_the_guarded_line(self):
        # Precondition, not a result: without it every assertion below would
        # pass vacuously on a network that never enters the fallback at all.
        Y_oo = self._dc_schur_block()
        rhs = np.ones((3, 2), dtype=complex)

        self.assertTrue(np.all(Y_oo[0, :] == 0) and np.all(Y_oo[:, 0] == 0),
                        "port 4 must be exactly DC-isolated")
        self.assertFalse(np.all(np.isfinite(Y_oo)),
                         "the ground-lead inductor must be non-finite at DC")
        with self.assertRaises(np.linalg.LinAlgError):
            np.linalg.solve(Y_oo, rhs)
        with self.assertRaises(np.linalg.LinAlgError):
            np.linalg.lstsq(Y_oo, rhs, rcond=None)

    def test_the_sweep_survives_and_only_the_bad_frequency_is_undefined(self):
        with np.errstate(divide="ignore", invalid="ignore"):
            Zmat, names, _ws = compute_z_matrix(
                _dc_degenerate_network(_DC_FREQS), _DC_FREQS,
                _dc_degenerate_term())

        self.assertEqual(names, ["AGG", "VIC"])
        self.assertTrue(np.all(np.isnan(Zmat[0])), "0 Hz must be undefined")
        self.assertTrue(np.all(np.isfinite(Zmat[1:])),
                        "every other frequency must be a real measurement")

    def test_the_undefined_frequency_is_NAMED(self):
        with np.errstate(divide="ignore", invalid="ignore"):
            _Z, _names, ws = compute_z_matrix(
                _dc_degenerate_network(_DC_FREQS), _DC_FREQS,
                _dc_degenerate_term())

        hit = [w for w in ws if "freq[0]" in w and "Schur" in w]
        self.assertTrue(hit, f"no warning names the frequency: {ws}")
        # The index alone is unactionable -- the reader needs the frequency and
        # the reason, because 0 Hz is kept silently by every composed sweep.
        self.assertIn("0 Hz", hit[0])
        self.assertIn("singular", hit[0].lower())

    def test_the_undefined_entry_is_COMPLEX_NaN(self):
        # A real NaN assigned into a complex array leaves imag == 0, and
        # L = Im(Z)/omega would then read as a perfectly plausible 0 H.
        with np.errstate(divide="ignore", invalid="ignore"):
            Zmat, _names, _ws = compute_z_matrix(
                _dc_degenerate_network(_DC_FREQS), _DC_FREQS,
                _dc_degenerate_term())

        self.assertTrue(np.isnan(Zmat[0, 0, 1].real))
        self.assertTrue(np.isnan(Zmat[0, 0, 1].imag))

    def test_the_healthy_frequencies_are_BIT_IDENTICAL_without_the_DC_point(self):
        # The guard must divert the one bad frequency and touch nothing else:
        # recovering from index 0 may not perturb the answer the user came for.
        with np.errstate(divide="ignore", invalid="ignore"):
            with_dc, _n, _w = compute_z_matrix(
                _dc_degenerate_network(_DC_FREQS), _DC_FREQS,
                _dc_degenerate_term())
        clean = _DC_FREQS[1:]
        without_dc, _n, _w = compute_z_matrix(
            _dc_degenerate_network(clean), clean, _dc_degenerate_term())

        self.assertTrue(np.array_equal(with_dc[1:], without_dc))

    def test_a_finite_series_R_on_the_ground_lead_recovers_a_real_number(self):
        # The workaround available with no code change: 1 uOhm beside the
        # 50 pH makes y finite at DC, lstsq converges, and the cost at the
        # marker frequency is roundoff.
        term = TerminationSet(per_port={
            0: Signal("AGG", +1), 1: Signal("AGG", -1),
            2: Signal("VIC", +1), 3: Signal("VIC", -1),
            4: Open(), 5: Open(),
            6: LumpedToGnd(y_series_rlc(1e-6, _DC_GND_LEAD_L, math.inf)),
        })
        Y = _dc_degenerate_network(_DC_FREQS)
        Zr, _n, ws = compute_z_matrix(Y, _DC_FREQS, term)
        with np.errstate(divide="ignore", invalid="ignore"):
            Zi, _n, _w = compute_z_matrix(Y, _DC_FREQS, _dc_degenerate_term())

        self.assertTrue(np.all(np.isfinite(Zr)))
        self.assertTrue(any("lstsq" in w for w in ws),
                        f"expected the ordinary lstsq fallback: {ws}")
        # 1 uOhm against 1.74 Ohm of reactance at 5.55 GHz is free.
        rel = abs(Zi[2, 0, 1] - Zr[2, 0, 1]) / abs(Zr[2, 0, 1])
        self.assertLess(rel, 1e-6)


class TestFormatSI(unittest.TestCase):
    def test_picosecond_range(self):
        self.assertEqual(format_si(345e-12, "H"), "345 pH")

    def test_microvolts(self):
        # 0.000345 H = 345 uH
        self.assertEqual(format_si(0.000345, "H"), "345 uH")

    def test_negative(self):
        self.assertEqual(format_si(-1.234e-9, "H"), "-1.23 nH")

    def test_unitless_no_trailing_space(self):
        self.assertEqual(format_si(2.5e3), "2.5 k")

    def test_zero(self):
        self.assertEqual(format_si(0.0, "Ω"), "0.00 Ω")

    def test_nan_inf(self):
        self.assertEqual(format_si(float("nan"), "H"), "nan")
        self.assertEqual(format_si(float("inf"), "H"), "inf")
        self.assertEqual(format_si(float("-inf"), "H"), "-inf")

    def test_below_femto_clamps(self):
        self.assertEqual(format_si(1e-18, "F"), "0.001 fF")


class TestParserLineBreaks(unittest.TestCase):
    """
    The parser streams the file line by line, but `str.splitlines()` -- what it
    used before -- also breaks on \\x0b \\x0c \\x1c-\\x1e \\x85 \\u2028 \\u2029.
    A comment or option line terminated by one of those (older EDA flows page-
    break their headers with a form feed) must not swallow the data record that
    follows it.
    """

    def _parse(self, text: str):
        import tempfile
        d = Path(tempfile.mkdtemp())
        p = d / "brk.s1p"
        p.write_text(text, encoding="utf-8", newline="")
        return parse_touchstone(p)

    def test_form_feed_after_comment_keeps_the_data_row(self):
        ts = self._parse("# HZ S RI R 50\n"
                         "1 0.1 0.2\n"
                         "! page break follows\x0c2 0.3 0.4\n"
                         "3 0.5 0.6\n")
        np.testing.assert_array_equal(ts.freqs, [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(
            ts.s.ravel(), [0.1 + 0.2j, 0.3 + 0.4j, 0.5 + 0.6j])

    def test_form_feed_after_option_line_keeps_the_data_row(self):
        ts = self._parse("# GHZ S RI R 50\x0c1 0.1 0.2\n2 0.3 0.4\n")
        np.testing.assert_array_equal(ts.freqs, [1e9, 2e9])
        self.assertEqual(ts.z0, 50.0)

    def test_next_line_char_after_comment(self):
        ts = self._parse("# HZ S RI R 50\n1 0.1 0.2\n! note\x852 0.3 0.4\n")
        np.testing.assert_array_equal(ts.freqs, [1.0, 2.0])

    def test_form_feed_inside_a_mid_line_comment(self):
        ts = self._parse("# HZ S RI R 50\n1 0.1 0.2 ! note\x0c2 0.3 0.4\n")
        np.testing.assert_array_equal(ts.freqs, [1.0, 2.0])
        np.testing.assert_array_equal(ts.s.ravel(), [0.1 + 0.2j, 0.3 + 0.4j])

    def test_port_name_comment_still_parsed_when_split(self):
        ts = self._parse("# HZ S RI R 50\n! Port 1 = VDD\x0c1 0.1 0.2\n")
        self.assertEqual(list(ts.port_names), ["VDD"])
        np.testing.assert_array_equal(ts.freqs, [1.0])

    def test_crlf_and_plain_lf_are_unaffected(self):
        a = self._parse("# HZ S RI R 50\r\n1 0.1 0.2\r\n2 0.3 0.4\r\n")
        b = self._parse("# HZ S RI R 50\n1 0.1 0.2\n2 0.3 0.4\n")
        np.testing.assert_array_equal(a.s, b.s)
        np.testing.assert_array_equal(a.freqs, b.freqs)


class TestParserSignedZero(unittest.TestCase):
    """
    Real EDA exports write '-0.000000e+00'.  The historical
    `body[...,0] + 1j*body[...,1]` normalised those to +0.0; the streaming
    in-place fill must keep doing so, or L/Q print as '-0'.  Note that
    np.testing.assert_array_equal cannot catch this (-0.0 == 0.0), so the
    golden regression would never have flagged it.
    """

    def _parse(self, text: str):
        import tempfile
        d = Path(tempfile.mkdtemp())
        p = d / "negzero.s1p"
        p.write_text(text, encoding="utf-8", newline="")
        return parse_touchstone(p)

    def test_ri_negative_zero_is_normalised(self):
        ts = self._parse("# HZ S RI R 50\n1 -0 -0\n2 0.5 -0.0\n")
        flat = ts.s.ravel()
        for z in flat:
            self.assertFalse(bool(np.signbit(z.real)), "-0.0 leaked into Re")
            self.assertFalse(bool(np.signbit(z.imag)), "-0.0 leaked into Im")

    def test_matches_the_historical_expression(self):
        """
        Same signed zeros as `body[...,0] + 1j*body[...,1]` everywhere except
        the one corner documented in parse_touchstone: a record with BOTH parts
        written '-0' used to keep a -0.0 real part, because Re(1j * -0.0) is
        -0.0.  Every other combination must agree bit for bit.
        """
        rows = [(0.5, -0.0), (-0.0, 0.0), (-0.0, 2.0), (0.0, -0.0),
                (-1.5, 2.25)]
        text = "# HZ S RI R 50\n" + "".join(
            f"{i + 1} {re!r} {im!r}\n" for i, (re, im) in enumerate(rows))
        ts = self._parse(text)
        body = np.array(rows)
        old = (body[:, 0] + 1j * body[:, 1]).reshape(ts.s.shape)
        np.testing.assert_array_equal(
            ts.s.view(np.float64), old.view(np.float64))
        np.testing.assert_array_equal(
            np.signbit(ts.s.view(np.float64)), np.signbit(old.view(np.float64)))

    def test_ma_and_db_paths_are_unchanged(self):
        for fmt in ("MA", "DB"):
            with self.subTest(fmt=fmt):
                ts = self._parse(f"# HZ S {fmt} R 50\n1 0.5 -0.0\n2 0.25 30\n")
                self.assertEqual(ts.s.shape, (2, 1, 1))
                self.assertFalse(bool(np.signbit(ts.s.ravel()[0].imag)))


if __name__ == "__main__":
    unittest.main()
