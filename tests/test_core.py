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
    TerminationSet, Signal, Ground, Open, Vdd, LumpedToGnd,
    y_capacitor,
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
