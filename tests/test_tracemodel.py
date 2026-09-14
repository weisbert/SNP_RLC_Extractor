"""
The trace pi model: `pkg_rlc.physics.tracemodel` + `pkg_rlc.present.tracemodel_report`.

No Tk root, no tkinter import -- this module belongs in `FAST_MODULES`.

WHAT THESE TESTS ARE FOR
------------------------
The claim the whole feature rests on is that a two-port's Y matrix and a pi
circuit are THE SAME OBJECT, so the elements come out exact rather than
fitted.  `TestPiIsExact` is that claim, checked against a network whose
element values are known by construction and deliberately ASYMMETRIC -- the
one case a symmetric two-measurement fit cannot get right, and which the same
test measures so the reason this module exists stays written down.

`TestDifferentialIsExact` does the same for a pair, and adds a cross-check
that matters more than it looks: the differential pi is read two completely
independent ways -- off the probe model's 2x2, and off a mixed-mode transform
of the full 4x4 -- and the two must agree.  Neither path can be quietly wrong
without the other moving.
"""

from __future__ import annotations

import contextlib
import io
import math
import unittest

import numpy as np

from pkg_rlc.physics import tracemodel as tm
from pkg_rlc.physics.core import extract_rlc_at_freq
from pkg_rlc.present import tracemodel_report as tmr

FIX = "tests/fixtures"


def pi_Z(Rs, Ls, C1, R1, C2, R2, f):
    """The 2x2 open-circuit Z of a pi built from known element values."""
    w = 2 * math.pi * f
    ys = 1.0 / complex(Rs, w * Ls)
    y1 = 1.0 / complex(R1, -1.0 / (w * C1))
    y2 = 1.0 / complex(R2, -1.0 / (w * C2))
    return np.linalg.inv(np.array([[ys + y1, -ys], [-ys, ys + y2]],
                                  dtype=complex))


def coupled_pair_Z4(Zs1, Zs2, C1g, C2g, C12, f):
    """
    The 4x4 single-ended open-circuit Z of a coupled pair.

    Node order is (IN+, IN-, OUT+, OUT-), which is the order
    `mode_conversion_ratio` documents.
    """
    w = 2 * math.pi * f
    Y = np.zeros((4, 4), dtype=complex)
    for a, b, y in [(0, 2, 1 / Zs1), (1, 3, 1 / Zs2),
                    (0, 1, 1j * w * C12), (2, 3, 1j * w * C12)]:
        Y[a, a] += y
        Y[b, b] += y
        Y[a, b] -= y
        Y[b, a] -= y
    for n, g in [(0, 1j * w * C1g), (1, 1j * w * C2g),
                 (2, 1j * w * C1g), (3, 1j * w * C2g)]:
        Y[n, n] += g
    return np.linalg.inv(Y)


class TestPiIsExact(unittest.TestCase):
    """
    The identity, on a network that a symmetric fit provably cannot solve.

    Cp_in 40 fF against Cp_out 26.4 fF is a 1.5:1 imbalance -- the shape a
    real trace has whenever its two ends sit over different metal.
    """

    F = 1e8
    TRUE = dict(Rs=344.0, Ls=1.69e-9,
                C1=40.0e-15, R1=90.0, C2=26.4e-15, R2=91.5)

    def setUp(self):
        t = self.TRUE
        self.Z2 = pi_Z(t["Rs"], t["Ls"], t["C1"], t["R1"],
                       t["C2"], t["R2"], self.F)
        self.m = tm.extract_pi(self.Z2, self.F)

    def test_every_element_comes_back_exact(self):
        t = self.TRUE
        for got, want in [(self.m.series.R_ohm, t["Rs"]),
                          (self.m.series.L_henry, t["Ls"]),
                          (self.m.shunt_in.R_ohm, t["R1"]),
                          (self.m.shunt_in.C_farad, t["C1"]),
                          (self.m.shunt_out.R_ohm, t["R2"]),
                          (self.m.shunt_out.C_farad, t["C2"])]:
            self.assertAlmostEqual(got / want, 1.0, places=9)

    def test_no_warnings_and_reciprocal(self):
        self.assertEqual(self.m.warnings, [])
        self.assertLess(self.m.reciprocity, 1e-12)

    def test_the_symmetric_two_measurement_fit_is_the_one_that_misses(self):
        """
        Why this module exists, kept as a MEASUREMENT rather than a comment.

        The fit it replaces reads two scalars off the same network -- port 1
        to ground with port 2 open, and port 1 to port 2 -- and solves them
        for a SYMMETRIC pi.  On an asymmetric network it gets Rs right, Cp
        right only as the mean of the two, and Ls wrong.
        """
        Zr = self.Z2
        M1 = Zr[0, 0]
        wv = np.array([1, -1], dtype=complex)
        M2 = wv @ Zr @ wv
        r = 2 * M1 / M2 - 1
        Zs_fit = M2 * (1 + 2 * r) / (2 * r)
        Zp_fit = Zs_fit * r

        w = 2 * math.pi * self.F
        Ls_fit = Zs_fit.imag / w
        Cp_fit = -1.0 / (w * Zp_fit.imag)
        t = self.TRUE

        # It lands Rs and the MEAN capacitance to parts per million ...
        self.assertLess(abs(Zs_fit.real / t["Rs"] - 1.0), 1e-4)
        self.assertLess(abs(Cp_fit / ((t["C1"] + t["C2"]) / 2) - 1.0), 1e-4)
        # ... and misses Ls by percent, which is the whole point.
        err = abs(Ls_fit / t["Ls"] - 1.0)
        self.assertGreater(err, 0.02, "the fit is supposed to MISS here")
        self.assertLess(err, 0.20)
        # while the identity has no error at all.
        self.assertAlmostEqual(self.m.series.L_henry / t["Ls"], 1.0, places=9)


class TestBranchMatchesExtractRlc(unittest.TestCase):
    """
    The four expressions are `extract_rlc_at_freq`'s, so they must not drift.

    A second spelling of R / L / C / Q is how two surfaces of one tool come to
    print different numbers for one measurement.
    """

    def test_agrees_on_the_same_complex_number(self):
        f = 2.5e9
        for z in [complex(344.0, 51.7), complex(0.5, -900.0),
                  complex(-3.0, 12.0), complex(1e6, -1e-3)]:
            with self.subTest(z=z):
                b = tm._branch("x", 1.0 / z, 2 * math.pi * f, [])
                ref = extract_rlc_at_freq(np.array([f]), np.array([z]), f)
                self.assertAlmostEqual(b.R_ohm, ref.R_ohm, places=9)
                self.assertAlmostEqual(b.L_henry / ref.L_henry, 1.0, places=9)
                self.assertAlmostEqual(b.C_farad / ref.C_farad, 1.0, places=9)
                self.assertAlmostEqual(b.Q / ref.Q, 1.0, places=9)

    def test_signs_are_not_clipped(self):
        """A capacitive branch reports a NEGATIVE L, per the Cadence rule."""
        b = tm._branch("x", 1.0 / complex(10.0, -500.0), 2 * math.pi * 1e9, [])
        self.assertLess(b.L_henry, 0.0)
        self.assertGreater(b.C_farad, 0.0)
        self.assertLess(b.Q, 0.0)
        self.assertEqual(b.reads_as, "C")


class TestReadsAs(unittest.TestCase):
    """|Q| << 1 is a resistor, and saying so is the point of the field."""

    def test_small_q_is_a_resistor_whichever_way_the_residue_points(self):
        w = 2 * math.pi * 1e8
        for z in [complex(344.0, 1.06), complex(344.0, -0.17)]:
            with self.subTest(z=z):
                b = tm._branch("x", 1.0 / z, w, [])
                self.assertEqual(b.reads_as, "R")
                self.assertTrue(b.is_resistive)

    def test_a_real_capacitor_and_a_real_inductor_read_as_themselves(self):
        w = 2 * math.pi * 1e8
        self.assertEqual(tm._branch("c", 1.0 / complex(108, -481), w,
                                    []).reads_as, "C")
        self.assertEqual(tm._branch("l", 1.0 / complex(2, 60), w,
                                    []).reads_as, "L")


class TestDifferentialIsExact(unittest.TestCase):
    """The pair, read two independent ways, against values known by hand."""

    F = 1e9
    C1G = 30e-15
    C12 = 50e-15

    def setUp(self):
        w = 2 * math.pi * self.F
        self.Zs = complex(2.0, w * 0.5e-9)
        self.Z4 = coupled_pair_Z4(self.Zs, self.Zs, self.C1G, self.C1G,
                                  self.C12, self.F)
        W = np.array([[1, -1, 0, 0], [0, 0, 1, -1]], dtype=complex).T
        self.m = tm.extract_pi(W.T @ self.Z4 @ W, self.F, differential=True)

    def test_series_is_the_loop_impedance(self):
        """Both conductors: Zs_dd = 2 * Zs."""
        w = 2 * math.pi * self.F
        self.assertAlmostEqual(self.m.series.R_ohm / (2 * self.Zs.real), 1.0,
                               places=9)
        self.assertAlmostEqual(
            self.m.series.L_henry / (2 * self.Zs.imag / w), 1.0, places=9)

    def test_shunt_is_the_capacitance_ACROSS_the_pair(self):
        """
        C_diff = C12 + C1g/2, NOT C1g + 2*C12.

        The second is the per-line odd-mode number an EM tool quotes, and it
        is exactly twice this one; `odd_mode_farads` carries it so a reader
        never has to guess which convention a bare 'C' is in.
        """
        want = self.C12 + self.C1G / 2
        self.assertAlmostEqual(self.m.shunt_in.C_farad / want, 1.0, places=9)
        odd = self.m.odd_mode_farads(self.m.shunt_in)
        self.assertAlmostEqual(odd / (self.C1G + 2 * self.C12), 1.0, places=9)
        self.assertAlmostEqual(odd / (2 * self.m.shunt_in.C_farad), 1.0,
                               places=12)

    def test_odd_mode_is_nan_on_a_single_ended_model(self):
        se = tm.extract_pi(pi_Z(1, 1e-9, 1e-15, 1, 1e-15, 1, 1e9), 1e9)
        self.assertTrue(math.isnan(se.odd_mode_farads(se.shunt_in)))

    def test_probe_model_and_mixed_mode_transform_agree(self):
        """
        Two independent routes to the same differential pi.

        They coincide only where the pair is balanced -- which is exactly what
        `mode_conversion_ratio` measures -- so this test and
        `TestModeConversion` are two halves of one statement.
        """
        Ymm = tm._TI @ np.linalg.inv(self.Z4) @ tm._TV_INV
        other = tm.extract_pi(np.linalg.inv(Ymm[:2, :2]), self.F,
                              differential=True)
        for a, b in [(self.m.series, other.series),
                     (self.m.shunt_in, other.shunt_in),
                     (self.m.shunt_out, other.shunt_out)]:
            # Relative error on the COMPLEX value, not a per-part ratio: this
            # pair is lossless in the shunt, so both real parts are ~1e-17 and
            # their ratio is -1 as readily as +1.  |dZ|/|Z| is well-conditioned
            # wherever the branch itself is.
            self.assertLess(abs(a.Z - b.Z) / abs(b.Z), 1e-9)


class TestModeConversion(unittest.TestCase):
    """The validity qualifier on a differential pi."""

    F = 1e9

    def _pair(self, scale, c2g):
        w = 2 * math.pi * self.F
        Zs = complex(2.0, w * 0.5e-9)
        return coupled_pair_Z4(Zs, Zs * scale, 30e-15, c2g, 50e-15, self.F)

    def test_a_symmetric_pair_reads_numerical_zero(self):
        r = tm.mode_conversion_ratio(self._pair(1.0, 30e-15))
        self.assertLess(r, 1e-9)
        self.assertLess(r, tmr.MODE_CONVERSION_WARN)

    def test_an_imbalanced_pair_trips_the_threshold(self):
        r = tm.mode_conversion_ratio(self._pair(1.30, 39e-15))
        self.assertGreater(r, tmr.MODE_CONVERSION_WARN)
        self.assertLess(r, 1.0)

    def test_it_grows_with_the_imbalance(self):
        prev = -1.0
        for scale in (1.02, 1.10, 1.30, 1.60):
            r = tm.mode_conversion_ratio(self._pair(scale, 30e-15))
            self.assertGreater(r, prev)
            prev = r

    def test_a_wrong_shape_is_refused_by_name(self):
        with self.assertRaises(ValueError) as cm:
            tm.mode_conversion_ratio(np.eye(3, dtype=complex))
        self.assertIn("4x4", str(cm.exception))

    def test_non_finite_is_nan_not_zero(self):
        Z = np.full((4, 4), complex(float("nan"), float("nan")))
        self.assertTrue(math.isnan(tm.mode_conversion_ratio(Z)))


class TestDegenerate(unittest.TestCase):
    """One bad frequency NaNs that frequency and names it -- never raises."""

    def test_singular_z_is_nan_with_a_warning(self):
        m = tm.extract_pi(np.ones((2, 2), dtype=complex), 1e9)
        self.assertTrue(math.isnan(m.series.R_ohm))
        self.assertTrue(any("singular" in w for w in m.warnings))

    def test_non_finite_z_is_nan_with_a_warning(self):
        Z = np.array([[complex(float("nan"), 0), 0], [0, 1]], dtype=complex)
        m = tm.extract_pi(Z, 1e9)
        self.assertTrue(math.isnan(m.shunt_in.R_ohm))
        self.assertTrue(any("not finite" in w for w in m.warnings))

    def test_a_wrong_shape_is_refused_by_name(self):
        with self.assertRaises(ValueError) as cm:
            tm.extract_pi(np.eye(3, dtype=complex), 1e9)
        self.assertIn("2x2", str(cm.exception))

    def test_a_zero_admittance_branch_is_an_open_not_a_zero_henry(self):
        """
        An ideal line with no shunt: R = inf, and L / C say nan.

        The alternative -- Im(Z) == 0 read through L = Im(Z)/omega -- prints
        0 H, which is a plausible number for a branch that is not there.
        """
        w = 2 * math.pi * 1e9
        Y2 = np.array([[1 / complex(1, w * 1e-9), -1 / complex(1, w * 1e-9)],
                       [-1 / complex(1, w * 1e-9), 1 / complex(1, w * 1e-9)]],
                      dtype=complex)
        warns: list[str] = []
        b = tm._branch("shunt_in", complex(0.0, 0.0), w, warns)
        self.assertEqual(b.R_ohm, float("inf"))
        self.assertTrue(math.isnan(b.L_henry))
        self.assertTrue(any("open circuit" in x for x in warns))
        self.assertEqual(Y2.shape, (2, 2))      # the pi above is well-formed

    def test_a_non_reciprocal_two_port_warns_and_averages(self):
        Z = np.array([[100 + 10j, 5 + 1j], [50 + 9j, 120 + 11j]],
                     dtype=complex)
        m = tm.extract_pi(Z, 1e9)
        self.assertGreater(m.reciprocity, 1e-3)
        self.assertTrue(any("Y12 and Y21 differ" in w for w in m.warnings))


class TestExtractPiAt(unittest.TestCase):
    """Frequency selection is `extract_rlc_at_freq`'s argmin, verbatim."""

    def setUp(self):
        self.freqs = np.array([1e8, 1e9, 5e9])
        self.Zm = np.stack([pi_Z(344, 1.69e-9, 33.2e-15, 90,
                                 33.2e-15, 90, f) for f in self.freqs])

    def test_it_snaps_to_the_nearest_point_and_remembers_the_request(self):
        m = tm.extract_pi_at(self.freqs, self.Zm, 76.8e6)
        self.assertEqual(m.freq_hz, 1e8)
        self.assertEqual(m.requested_hz, 76.8e6)

    def test_a_wrong_shape_is_refused_by_name(self):
        with self.assertRaises(ValueError) as cm:
            tm.extract_pi_at(self.freqs, self.Zm[:, :1, :1], 1e9)
        self.assertIn("(nfreqs, 2, 2)", str(cm.exception))

    def test_an_empty_axis_is_refused(self):
        with self.assertRaises(ValueError):
            tm.extract_pi_at(np.array([]), self.Zm[:0], 1e9)


class TestLumpedDrift(unittest.TestCase):
    """Two points out of a solved sweep, as 'is this one lumped element'."""

    def test_a_truly_lumped_pi_does_not_drift(self):
        freqs = np.array([1e8, 5e9])
        Zm = np.stack([pi_Z(344, 1.69e-9, 33.2e-15, 90, 33.2e-15, 90, f)
                       for f in freqs])
        a = tm.extract_pi_at(freqs, Zm, 5e9)
        b = tm.extract_pi_at(freqs, Zm, 1e8)
        for v in tm.lumped_drift(a, b).values():
            self.assertLess(abs(v), 1e-6)

    def test_a_frequency_dependent_element_is_flagged(self):
        """Skin effect: Rs doubling across the band has to show up."""
        freqs = np.array([1e8, 5e9])
        Zm = np.stack([pi_Z(344 * (1 + 1.0 * (f > 1e9)), 1.69e-9,
                            33.2e-15, 90, 33.2e-15, 90, f) for f in freqs])
        a = tm.extract_pi_at(freqs, Zm, 5e9)
        b = tm.extract_pi_at(freqs, Zm, 1e8)
        d = tm.lumped_drift(a, b)
        self.assertGreater(abs(d["series"]), tm.LUMPED_DRIFT_WARN)
        lines = tmr.pi_report_lines(a, reference=b)
        self.assertTrue(any("NOT one lumped element" in x for x in lines))


class TestReportRendering(unittest.TestCase):
    """The drawing is a drawing: its columns have to line up."""

    def _lines(self, **kw):
        Z = pi_Z(kw.pop("Rs", 344), kw.pop("Ls", 1.69e-9),
                 kw.pop("C1", 33.2e-15), kw.pop("R1", 90.0),
                 kw.pop("C2", 33.2e-15), kw.pop("R2", 90.0), 1e9)
        return tmr.pi_schematic_lines(
            tm.extract_pi(Z, 1e9, "IN(1)", "OUT(2)", **kw))

    def test_both_legs_stay_in_one_column_at_any_value_width(self):
        for kw in [{}, dict(Rs=1.23e6, Ls=50e-9, C1=4e-12, R1=0.5,
                            C2=7e-15, R2=1e3),
                   dict(Rs=0.002, Ls=1e-12, C1=1e-15, R1=1e-6,
                        C2=900e-12, R2=12345.0)]:
            with self.subTest(kw=kw):
                lines = self._lines(**kw)
                cols = [[i for i, c in enumerate(ln) if c in "*|+"]
                        for ln in lines]
                left = {c[0] for c in cols if c}
                right = {c[-1] for c in cols if c}
                self.assertEqual(len(left), 1, f"left legs wander: {left}")
                self.assertEqual(len(right), 1, f"right legs wander: {right}")

    def test_the_single_ended_drawing_has_a_reference_rail(self):
        self.assertTrue(any("reference" in x for x in self._lines()))

    def test_the_differential_drawing_has_NO_reference_rail(self):
        """
        A differential pi's shunt goes across the pair, not to ground.

        Drawing a ground rail under it would be a lie that reads as a diagram,
        which is the one thing a schematic must not be.
        """
        lines = self._lines(differential=True)
        # The word appears in "no reference node"; what must be absent is the
        # RAIL LABEL that the single-ended drawing puts under its bottom rail.
        self.assertFalse(any(x.strip() == "reference" for x in lines))
        self.assertTrue(any("across the pair" in x for x in lines))
        self.assertTrue(any("no reference node" in x for x in lines))

    def test_every_branch_prints_its_own_verdict(self):
        Z = pi_Z(344, 1.69e-9, 33.2e-15, 90, 33.2e-15, 90, 1e8)
        lines = tmr.pi_report_lines(tm.extract_pi(Z, 1e8))
        body = "\n".join(lines)
        self.assertIn("do not read L or C", body)      # the series, |Q| << 1
        self.assertIn("capacitive -- read C", body)    # the two shunts

    def test_ohms_are_spelled_the_way_every_other_surface_spells_them(self):
        self.assertEqual(tmr.OHM, "Ω")
        self.assertTrue(any(tmr.OHM in x for x in self._lines()))


class TestCliEndToEnd(unittest.TestCase):
    """
    Against fixtures whose element values are written in their own headers.

    `pi_2port.s2p` says R_series = 1.0, L_series = 1e-9, C_shunt_each = 1e-15;
    `diff_pair_4port.s4p` says L_loop = 8e-9 and C_shunt_each_port = 1e-15.
    Those comments are the truth these two tests check the whole stack
    against -- parser, s_to_y, compute_z_matrix, the pi identity and the
    report, in one pass.
    """

    def _run(self, argv):
        import pkg_rlc.frontend.cli as cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(argv)
            except SystemExit as e:
                rc = 0 if e.code is None else e.code
        return rc, out.getvalue(), err.getvalue()

    def test_single_ended_recovers_the_fixture_header(self):
        rc, out, err = self._run([
            "--cli", f"{FIX}/pi_2port.s2p", "--mode", "coupling",
            "--mport", "in = 1", "--mport", "out = 2",
            "--freq", "1.0", "--trace-model", "in,out"])
        self.assertEqual(rc, 0, err)
        body = out[out.index("Trace model"):]
        self.assertIn("single-ended", body)
        self.assertIn("R = 1 ", body)          # R_series = 1.0 Ohm
        self.assertIn("L = 1 nH", body)        # L_series = 1e-9 H
        self.assertEqual(body.count("C = 1 fF"), 2)   # one shunt per end

    def test_differential_recovers_the_fixture_header(self):
        rc, out, err = self._run([
            "--cli", f"{FIX}/diff_pair_4port.s4p", "--mode", "coupling",
            "--mport", "in = 1 / 2", "--mport", "out = 3 / 4",
            "--freq", "1.0", "--trace-model", "in,out"])
        self.assertEqual(rc, 0, err)
        body = out[out.index("Trace model"):]
        self.assertIn("differential", body)
        self.assertIn("L = 8 nH", body)               # L_loop = 8e-9 H
        self.assertIn("C = 0.5 fF", body)             # across the pair
        self.assertIn("(odd 1 fF)", body)             # per line, = 2 * C_diff
        self.assertIn("mode conversion", body)
        self.assertIn("the pair is balanced", body)

    def test_it_is_refused_outside_coupling_mode(self):
        rc, _out, err = self._run([
            "--cli", f"{FIX}/pi_2port.s2p", "--mode", "gnd", "--porta", "1",
            "--freq", "1.0", "--trace-model", "in,out"])
        self.assertEqual(rc, 2)
        self.assertIn("--trace-model is only valid with --mode coupling", err)

    def test_both_ends_the_same_port_is_refused_by_name(self):
        rc, _out, err = self._run([
            "--cli", f"{FIX}/pi_2port.s2p", "--mode", "coupling",
            "--mport", "in = 1", "--mport", "out = 2",
            "--freq", "1.0", "--trace-model", "in,in"])
        self.assertEqual(rc, 2)
        self.assertIn("two nodes", err)

    def test_an_unknown_port_name_is_refused_by_name(self):
        rc, _out, err = self._run([
            "--cli", f"{FIX}/pi_2port.s2p", "--mode", "coupling",
            "--mport", "in = 1", "--mport", "out = 2",
            "--freq", "1.0", "--trace-model", "in,nope"])
        self.assertEqual(rc, 2)
        self.assertIn("nope", err)

    def test_a_frequency_the_file_cannot_answer_is_labelled(self):
        """
        The report never answers silently with a different frequency.

        Which WORDS it uses is `FreqSnap.off_grid`'s call, not this module's:
        off_grid is |delta| > 0.5 * local_step, so a request just below a band
        with a wide step is a note ("nearest point") and one further out is a
        warning ("outside the swept band").  What this asserts is the part
        that is always true -- the resolved point AND the request are both on
        screen.  The warning wording is pinned separately below.
        """
        rc, out, err = self._run([
            "--cli", f"{FIX}/pi_2port.s2p", "--mode", "coupling",
            "--mport", "in = 1", "--mport", "out = 2",
            "--freq", "0.0000001", "--trace-model", "in,out"])
        self.assertEqual(rc, 0, err)
        body = out[out.index("Trace model"):]
        self.assertIn("requested 1e-07 GHz", body)
        self.assertIn("@ 0.001 GHz", body)

    def test_the_outside_band_wording_reaches_the_report(self):
        """A genuinely off-grid snap renders through `marker_freq_text`."""
        from pkg_rlc.model.trace import FreqSnap
        snap = FreqSnap(requested_hz=76.8e6, actual_hz=100e6,
                        step_hz=1e6, local_step_hz=1e6)
        self.assertTrue(snap.off_grid)
        Z = pi_Z(344, 1.69e-9, 33.2e-15, 90, 33.2e-15, 90, 1e8)
        lines = tmr.pi_report_lines(tm.extract_pi(Z, 1e8), freq_snap=snap)
        self.assertTrue(any("outside the swept band" in x for x in lines))


if __name__ == "__main__":
    unittest.main()
