"""
Composing several Touchstone files into one network.

The two findings that shape this whole feature, and the two things most of this
file is here to guard:

  * block_diag WELDS the two files' reference nodes.  An n-port Touchstone Y is
    the matrix with its own reference already eliminated, so stacking two of
    them identifies ref_A with ref_B at zero impedance.  Measured on the network
    `weld_files()` builds below (2 nH die coil, 100 pH package trace, 100 pH
    package ground lead, read at the 5.2 GHz sample nearest the 5.205 GHz
    marker): with the die return brought out as a PORT and tied to the package
    ground pad, L_eff = 2.2501 nH and perturbing the ground path moves it; with
    the die return being the EM reference, the package ground pad GROUNDED,
    OPEN and through 1 nH all give L_eff = 2.1454 nH -- BIT-IDENTICAL, spread
    0.000e+00.  The package's whole ground network is unreachable and nothing
    raised.  `reference_check` is the measurement that names it.

  * the frequency grids almost never match, and an honest resampling is most of
    the work.  Refuse extrapolation, skip interpolation when the grids already
    agree (RELATIVE tolerance -- a file in GHz and one in Hz never compare equal
    as floats), interpolate S rather than Y or Z, and check the PHASE STEP
    rather than max |S|, which is provably incapable of firing (the set of S
    with sigma_max <= 1 is convex, so a convex combination of passive samples is
    still passive) and is not a passivity test in the first place.

Every guard here was mutation-checked -- the mutation that turns it red is named
in each docstring.  The network builders are the ones from the scratchpad probes
(check_shared_return.py / cold_start_screen.py): a nodal Y assembled from
branches, then internal nodes eliminated, so every fixture has an analytically
known answer and nothing under test is used to build it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import pkg_rlc_compose as pc  # noqa: E402
from pkg_rlc_compose import (  # noqa: E402
    ComposeError,
    ComposeInput,
    REF_LIVE,
    REF_NO_GROUND,
    REF_WELDED,
    align_frequencies,
    compose,
    interpolate_s,
    limit_case_check,
    link_short,
    parse_scoped_ports,
    reduce_block_y,
    reference_check,
    solve_composed,
    write_composed_touchstone,
)
from pkg_rlc_core import (  # noqa: E402
    Ground,
    ShortPair,
    Signal,
    TerminationSet,
    TouchstoneData,
    compute_z_matrix,
    parse_touchstone,
    s_to_y,
    y_to_s,
)


# ============================================================================
# Network builders (branches -> nodal Y -> port Y), from the scratchpad probes
# ============================================================================

def SER(r, l):
    return lambda w: 1.0 / (r + 1j * np.asarray(w) * l)


def CAP(c):
    return lambda w: 1j * np.asarray(w) * c


def node_y(n_nodes, elements, omega):
    """elements: (n1, n2, yfunc) with node 0 == this network's reference."""
    Y = np.zeros((len(omega), n_nodes, n_nodes), dtype=complex)
    for n1, n2, yf in elements:
        yy = yf(omega)
        if n1:
            Y[:, n1 - 1, n1 - 1] += yy
        if n2:
            Y[:, n2 - 1, n2 - 1] += yy
        if n1 and n2:
            Y[:, n1 - 1, n2 - 1] -= yy
            Y[:, n2 - 1, n1 - 1] -= yy
    return Y


def port_y(n_nodes, elements, port_nodes, omega):
    """Eliminate the internal nodes -> the file's port admittance matrix."""
    Yn = node_y(n_nodes, elements, omega)
    p = [n - 1 for n in port_nodes]
    i = [k for k in range(n_nodes) if k not in p]
    Ypp = Yn[:, p][:, :, p]
    if not i:
        return Ypp
    return Ypp - Yn[:, p][:, :, i] @ np.linalg.solve(
        Yn[:, i][:, :, i], Yn[:, i][:, :, p])


def as_file(freqs, Y, z0, name, port_names=None):
    """Wrap a port-Y as the TouchstoneData a parsed file would have produced."""
    n = Y.shape[-1]
    return TouchstoneData(nports=n, freqs=np.asarray(freqs, dtype=float),
                          s=y_to_s(Y, z0), z0=z0,
                          port_names=list(port_names or [""] * n),
                          source_path=name)


# --- the reference-node fixture --------------------------------------------
#
# Physical assembly, and the reason the two configurations differ:
#
#   board ball --[100 pH pkg trace]-- sig pad == die pad --[2 nH coil]-- die
#   return, and the die return reaches the board through a 100 pH package
#   GROUND LEAD.  Probe at the board ball, reference = board ground.
#   L should read 100 + 2000 + 100 = 2200 pH plus the pads' loading.
#
#   Configuration A: the EM file brings the die return out as PORT 2, so the
#   ground lead carries the return and is in the circuit.
#   Configuration B: the EM file's die return IS its reference node, which
#   block_diag welds straight to the board plane -- the ground lead is bypassed
#   and 100 pH of it vanishes with nothing said.
#
# The two die pads are DELIBERATELY unequal (2 fF on the coil top, 8 fF on the
# return).  With equal pads the EM block is symmetric in its two ports, and a
# limit case that swaps the package's signal and ground pads then reproduces the
# standalone number exactly -- i.e. the check that exists to catch a swapped
# mapping cannot catch one, and passes for the wrong reason.  Measured: with
# equal pads the swapped mapping is 0.0 relative error; with these, 1.30e-2.

MARK_HZ = 5.205e9
_WELD_F = np.linspace(1e9, 10e9, 91)     # 5.2 GHz is the sample nearest MARK_HZ
L_COIL, R_COIL = 2.0e-9, 1.5
L_TRACE, R_TRACE = 100e-12, 0.2
L_GLEAD, R_GLEAD = 100e-12, 0.05
C_DIE_TOP, C_DIE_RET, C_PKG_PAD = 2e-15, 8e-15, 4e-15


def weld_files(freqs=_WELD_F):
    """(EM-with-return-port, EM-without, PKG) as three TouchstoneData."""
    w = 2 * np.pi * np.asarray(freqs, dtype=float)
    em_a = port_y(2, [(1, 2, SER(R_COIL, L_COIL)),
                      (1, 0, CAP(C_DIE_TOP)), (2, 0, CAP(C_DIE_RET))],
                  [1, 2], w)
    em_b = port_y(1, [(1, 0, SER(R_COIL, L_COIL)), (1, 0, CAP(C_DIE_TOP))],
                  [1], w)
    pkg = port_y(3, [(1, 2, SER(R_TRACE, L_TRACE)),
                     (3, 0, SER(R_GLEAD, L_GLEAD)),
                     (1, 0, CAP(C_PKG_PAD)), (2, 0, CAP(C_PKG_PAD)),
                     (3, 0, CAP(C_PKG_PAD))], [1, 2, 3], w)
    return (as_file(freqs, em_a, 50.0, "coil_with_return.s2p",
                    ["coil_top", "die_return"]),
            as_file(freqs, em_b, 50.0, "coil_no_return.s1p", ["coil_top"]),
            as_file(freqs, pkg, 50.0, "package.s3p",
                    ["sig_pad", "board_ball", "gnd_pad"]))


def _L_at(net, Z, freq_hz=MARK_HZ):
    k = int(np.argmin(np.abs(net.freqs - freq_hz)))
    return float(Z[k, 0, 0].imag / (2 * np.pi * net.freqs[k]))


# --- a matched delay line, for the phase-step tests -------------------------

def delay_file(freqs, tau, name, mag=0.9, z0=50.0):
    """
    A 2-port whose only property is a known group delay.

    A LOSSLESS matched delay has det(I + S) = 1 - exp(-2j*theta), which is
    singular whenever theta is a multiple of pi -- so `mag` is under 1 and the
    file stays convertible.  The phase step across one sample interval is then
    exactly 2*pi*df*tau, which is the quantity align_frequencies claims to
    measure.
    """
    f = np.asarray(freqs, dtype=float)
    th = 2 * np.pi * f * tau
    S = np.zeros((len(f), 2, 2), dtype=complex)
    S[:, 0, 1] = S[:, 1, 0] = mag * np.exp(-1j * th)
    S[:, 0, 0] = S[:, 1, 1] = 0.05
    return TouchstoneData(nports=2, freqs=f, s=S, z0=z0,
                          port_names=["in", "out"], source_path=name)


# ============================================================================
# The reference-node self-check (requirement R2-2)
# ============================================================================

class TestReferenceCheck(unittest.TestCase):
    """
    The mandatory output that makes "what you assembled is what you measured"
    an honest claim.
    """

    def setUp(self):
        self.em_a, self.em_b, self.pkg = weld_files()

    # ---- the measurement this whole module exists because of --------------

    def test_a_welded_reference_makes_the_package_ground_irrelevant(self):
        """
        The finding itself, reproduced through the shipped path.

        Grounded / open / through 1 nH must be BIT-IDENTICAL when the die
        return is the EM file's own reference: not "close", identical, because
        the package ground network is not in the circuit at all.  If this ever
        starts differing, block_diag stopped welding and every claim in the
        module docstring needs re-measuring.
        """
        net = compose([ComposeInput(self.em_b, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        link = ShortPair(net.gport("EM", 1) - 1, net.gport("PKG", 1) - 1)
        probe = net.gport("PKG", 2) - 1
        gnd_pad = net.gport("PKG", 3) - 1

        specs = {
            "grounded": TerminationSet(per_port={probe: Signal("A"),
                                                 gnd_pad: Ground()},
                                       couplings=[link]),
            "open": TerminationSet(per_port={probe: Signal("A")},
                                   couplings=[link]),
            "through 1 nH": TerminationSet(
                per_port={probe: Signal("A"),
                          gnd_pad: pc.LumpedToGnd(pc.y_series_rlc(L=1e-9))},
                couplings=[link]),
        }
        Zs = {}
        for label, ts in specs.items():
            Z, _, _ = compute_z_matrix(net.Y, net.freqs, ts)
            Zs[label] = Z
        ref = Zs["grounded"]
        for label, Z in Zs.items():
            self.assertTrue(np.array_equal(Z, ref),
                            f"{label} is not bit-identical to grounded")
        self.assertAlmostEqual(_L_at(net, ref) * 1e9, 2.1454, places=3)

    def test_the_welded_case_is_detected_and_names_the_file(self):
        """
        MUTATION: drop the file name from the message; or gut the weld wording.
        Both turn this red.  (Replacing the exact-zero test with a TOLERANCE is
        the more tempting mutation and is guarded next door, by
        test_a_weak_but_real_ground_path_still_reads_LIVE -- a purely absolute
        `< 1e-30` is unobservable and is NOT guarded, because no network
        produces a nonzero delta down there: the two live cases measured
        3.389e+00 and 3.198e-05 ohm.)

        The verdict has to name the file AND the ports, because on a 316-port
        combined network "the ground is welded" is not something anyone can act
        on.
        """
        net = compose([ComposeInput(self.em_b, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        checks = {c.alias: c for c in
                  reference_check(net, ts, freq_hz=MARK_HZ)}
        self.assertEqual(checks["PKG"].verdict, REF_WELDED)
        self.assertEqual(checks["PKG"].max_delta, 0.0)
        msg = checks["PKG"].message
        self.assertIn("PKG", msg)
        self.assertIn("package.s3p", msg)
        self.assertIn("PKG.3", msg)
        self.assertIn("EXACTLY zero", msg)
        # and it is surfaced by the solve, not only by the helper
        sol = solve_composed(net, ts, marker_hz=MARK_HZ)
        self.assertEqual([c.alias for c in sol.welded], ["PKG"])
        self.assertTrue(any("WELD" in ln for ln in sol.report_lines()))

    def test_the_same_package_reads_LIVE_when_the_return_is_a_port(self):
        """
        The other half, without which the check could just always say "welded".

        MUTATION: make reference_check return REF_WELDED unconditionally --
        the test above still passes and this one fails.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1),
                       ShortPair(net.gport("EM", 2) - 1,
                                 net.gport("PKG", 3) - 1)])
        checks = {c.alias: c for c in reference_check(net, ts, freq_hz=MARK_HZ)}
        self.assertEqual(checks["PKG"].verdict, REF_LIVE)
        self.assertGreater(checks["PKG"].max_delta, 0.0)
        self.assertIn("is in the circuit", checks["PKG"].message)

    def test_the_two_configurations_differ_by_the_ground_lead(self):
        """
        The number the weld costs, so a regression in the reduction shows up as
        a number and not only as a verdict.

        Configuration A (die return as a port, ground lead carrying it) reads
        2.2501 nH; configuration B (die return welded to the board plane) reads
        2.1454 nH.  The difference is the 100 pH ground lead plus its loading.
        """
        net_a = compose([ComposeInput(self.em_a, "EM"),
                         ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts_a = TerminationSet(
            per_port={net_a.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(net_a.gport("EM", 1) - 1,
                                 net_a.gport("PKG", 1) - 1),
                       ShortPair(net_a.gport("EM", 2) - 1,
                                 net_a.gport("PKG", 3) - 1)])
        Za, _, _ = compute_z_matrix(net_a.Y, net_a.freqs, ts_a)

        net_b = compose([ComposeInput(self.em_b, "EM"),
                         ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts_b = TerminationSet(
            per_port={net_b.gport("PKG", 2) - 1: Signal("A"),
                      net_b.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net_b.gport("EM", 1) - 1,
                                 net_b.gport("PKG", 1) - 1)])
        Zb, _, _ = compute_z_matrix(net_b.Y, net_b.freqs, ts_b)

        self.assertAlmostEqual(_L_at(net_a, Za) * 1e9, 2.2501, places=3)
        self.assertAlmostEqual(_L_at(net_b, Zb) * 1e9, 2.1454, places=3)

    def test_a_file_with_no_declared_ground_is_a_note_and_not_an_accusation(self):
        """
        MUTATION: fold REF_NO_GROUND into REF_WELDED.  Configuration A is
        CORRECT and declares no package ground at all, so that mutation cries
        wolf on the composition the feature exists to make work.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1),
                       ShortPair(net.gport("EM", 2) - 1,
                                 net.gport("PKG", 3) - 1)])
        checks = {c.alias: c for c in reference_check(net, ts, freq_hz=MARK_HZ)}
        for alias in ("EM", "PKG"):
            self.assertEqual(checks[alias].verdict, REF_NO_GROUND)
            self.assertFalse(checks[alias].welded)
            self.assertIn("nothing to perturb", checks[alias].message)

    def test_a_weak_but_real_ground_path_still_reads_LIVE(self):
        """
        MUTATION: replace `max_delta == 0.0` with a tolerance -- `rel < 1e-6` is
        the one a reviewer suggests, because exact float comparison looks like a
        bug.  It is not one here.  A weld is EXACTLY zero because there is no
        path at all; anything else is a measurement, however small.

        Measured with a 1 uF board decap across the package ground lead, which
        is an ordinary thing to find there: the lead is almost entirely
        bypassed and the perturbation moves the answer by 3.198e-05 ohm,
        4.562e-07 of |Z| -- real, and under that tolerance.
        """
        w = 2 * np.pi * _WELD_F
        pkg = as_file(_WELD_F, port_y(3, [
            (1, 2, SER(R_TRACE, L_TRACE)), (3, 0, SER(R_GLEAD, L_GLEAD)),
            (1, 0, CAP(C_PKG_PAD)), (2, 0, CAP(C_PKG_PAD)),
            (3, 0, CAP(C_PKG_PAD)), (3, 0, CAP(1e-6)),
        ], [1, 2, 3], w), 50.0, "package_decap.s3p",
            ["sig_pad", "board_ball", "gnd_pad"])
        net = compose([ComposeInput(self.em_a, "EM"), ComposeInput(pkg, "PKG")])
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1),
                       ShortPair(net.gport("EM", 2) - 1,
                                 net.gport("PKG", 3) - 1)])
        c = {r.alias: r for r in reference_check(net, ts, freq_hz=MARK_HZ)}["PKG"]
        self.assertEqual(c.verdict, REF_LIVE)
        self.assertGreater(c.max_delta, 0.0)
        self.assertLess(c.rel_delta, 1e-6)

    def test_a_dc_only_sweep_says_the_check_cannot_run(self):
        """
        MUTATION: run the check at 0 Hz anyway.  jwL is identically zero there,
        so the perturbation is a WIRE: the answer does not move on any network,
        welded or not, and every file reads as welded -- a verdict that is
        right for the wrong reason on the half of the cases where it is right.
        """
        dc = np.array([0.0])
        em_a, _, pkg = weld_files(dc)
        net = compose([ComposeInput(em_a, "EM"), ComposeInput(pkg, "PKG")])
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        checks = reference_check(net, ts)
        self.assertEqual([c.verdict for c in checks], ["unknown", "unknown"])
        self.assertFalse(any(c.welded for c in checks))
        self.assertIn("above DC", checks[0].message)

    def test_the_check_uses_two_probe_values(self):
        """
        MUTATION: use a single probe inductance.  A one-value probe can sit
        where the sensitivity happens to vanish; two a decade apart cannot,
        unless it is identically zero -- which is the weld.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        c = reference_check(net, ts, freq_hz=MARK_HZ)[1]
        self.assertEqual(len(c.probe_l), 2)
        self.assertNotEqual(c.probe_l[0], c.probe_l[1])
        self.assertGreaterEqual(c.probe_l[1] / c.probe_l[0], 5.0)

    def test_every_composition_says_the_references_are_welded(self):
        """
        MUTATION: drop the note.  It is the one fact about block_diag that a
        reader has no other way to learn, and it is unconditional.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")])
        joined = " ".join(net.notes)
        self.assertIn("ZERO impedance", joined)
        self.assertIn("coil_with_return.s2p", joined)
        self.assertIn("package.s3p", joined)

    def test_the_reference_check_is_not_optional(self):
        """
        MUTATION: add a `check_reference=False` escape to solve_composed.  R2-2
        says mandatory; a check nobody runs is the same as no check.
        """
        import inspect
        sig = inspect.signature(solve_composed)
        self.assertNotIn("check_reference", sig.parameters)
        net = compose([ComposeInput(self.em_b, "EM"),
                       ComposeInput(self.pkg, "PKG")])
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        self.assertEqual(len(solve_composed(net, ts).reference), 2)


# ============================================================================
# The limit case (requirement R2-9)
# ============================================================================

class TestLimitCase(unittest.TestCase):
    """Replace the package with ideal wire; the standalone number must return."""

    def setUp(self):
        self.em_a, _, self.pkg = weld_files()
        self.net = compose([ComposeInput(self.em_a, "EM"),
                            ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        self.ts = TerminationSet(
            per_port={self.net.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(self.net.gport("EM", 1) - 1,
                                 self.net.gport("PKG", 1) - 1),
                       ShortPair(self.net.gport("EM", 2) - 1,
                                 self.net.gport("PKG", 3) - 1)])
        # the standalone EM measurement the composed one must reproduce:
        # probe the coil top, ground the die return.
        self.standalone, _, _ = compute_z_matrix(
            s_to_y(self.em_a.s, self.em_a.z0), self.em_a.freqs,
            TerminationSet(per_port={0: Signal("A"), 1: Ground()}))

    def _run(self, shorts, grounds):
        return limit_case_check(
            self.net, self.ts, self.standalone, ideal_aliases=["PKG"],
            shorts=shorts, grounds=grounds, freq_hz=MARK_HZ)

    def test_an_ideal_package_gives_the_standalone_number_back(self):
        """
        The trace becomes a wire from its die pad to its ball; the ground lead
        becomes a wire to the board plane.  Then the probe sits directly on the
        coil top with the die return at ground, which IS the standalone spec.
        Measured: exact, 0.00e+00 relative.
        """
        lc = self._run([[self.net.gport("PKG", 1), self.net.gport("PKG", 2)]],
                       [self.net.gport("PKG", 3)])
        self.assertTrue(lc.ok, lc.message)
        self.assertLess(lc.max_rel_error, 1e-12)
        self.assertIn("limit case passes", lc.message)

    def test_a_swapped_mapping_is_caught(self):
        """
        MUTATION: compare with a loose rtol (say 1e-2) instead of 1e-9.  The
        whole value of this check is that it catches a mapping error, and a
        swapped pair is the commonest one -- here the ideal package ties the
        signal ball to the GROUND pad instead of to the signal pad.  Measured
        error with that swap: 1.30e-2 relative, against 0.0 for the right one.
        """
        lc = self._run([[self.net.gport("PKG", 2), self.net.gport("PKG", 3)]],
                       [self.net.gport("PKG", 1)])
        self.assertFalse(lc.ok)
        self.assertIn("LIMIT CASE FAILS", lc.message)
        self.assertGreater(lc.max_rel_error, 1e-3)

    def test_it_names_the_ports_it_had_to_open(self):
        """
        MUTATION: open the dangling ports silently.  Zeroing a file's Y block
        leaves an all-zero row and column for any of its ports the ideal
        topology does not mention; grounding such a port is exactly deleting
        it, but doing that without saying so hides half of what was assumed.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")], marker_hz=MARK_HZ)
        ts = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        standalone, _, _ = compute_z_matrix(
            s_to_y(self.em_a.s, self.em_a.z0), self.em_a.freqs,
            TerminationSet(per_port={0: Signal("A"), 1: Ground()}))
        lc = limit_case_check(
            net, ts, standalone, ideal_aliases=["PKG"],
            shorts=[[net.gport("PKG", 1), net.gport("PKG", 2)]],
            grounds=[], freq_hz=MARK_HZ)
        self.assertEqual(lc.opened, [net.gport("PKG", 3)])
        self.assertIn("PKG.3", lc.message)

    def test_an_unknown_alias_is_refused(self):
        with self.assertRaises(ComposeError) as cm:
            limit_case_check(self.net, self.ts, self.standalone,
                             ideal_aliases=["NOPE"])
        self.assertIn("NOPE", str(cm.exception))

    def test_a_reference_on_a_different_axis_is_refused(self):
        """
        MUTATION: index the reference with the composed axis's index anyway.
        A 401-point standalone sweep beside a 901-point composed one would then
        be compared at whatever frequency index 450 happens to be in each --
        two different frequencies, reported as a limit-case failure or, worse,
        as a pass.
        """
        with self.assertRaises(ComposeError) as cm:
            limit_case_check(
                self.net, self.ts, self.standalone[:10],
                ideal_aliases=["PKG"],
                shorts=[[self.net.gport("PKG", 1), self.net.gport("PKG", 2)]],
                grounds=[self.net.gport("PKG", 3)])
        self.assertIn("frequency points", str(cm.exception))

    def test_a_single_value_reference_is_accepted(self):
        """The natural thing to have to hand: one number at the marker."""
        k = int(np.argmin(np.abs(self.net.freqs - MARK_HZ)))
        lc = limit_case_check(
            self.net, self.ts, self.standalone[k],
            ideal_aliases=["PKG"],
            shorts=[[self.net.gport("PKG", 1), self.net.gport("PKG", 2)]],
            grounds=[self.net.gport("PKG", 3)], freq_hz=MARK_HZ)
        self.assertTrue(lc.ok, lc.message)


# ============================================================================
# Frequency alignment (requirement R2-3)
# ============================================================================

class TestFrequencyAlignment(unittest.TestCase):

    FINE = np.linspace(1e9, 10e9, 901)      # 10 MHz step
    COARSE = np.linspace(1e9, 10e9, 91)     # 100 MHz step

    def test_an_identical_grid_skips_interpolation(self):
        """
        MUTATION: compare the axes with `np.array_equal` (or with atol instead
        of rtol).  Measured: `np.linspace(1.0, 10.0, 91) * 1e9` -- a file
        written in GHz -- and `np.linspace(1e9, 10e9, 91)` -- the same sweep
        written in Hz -- are NOT equal (max relative difference 2.218e-16), so
        the commonest case of all would be interpolated onto itself and pay the
        chord error for nothing.
        """
        ghz = np.linspace(1.0, 10.0, 91) * 1e9
        self.assertFalse(np.array_equal(ghz, self.COARSE))
        self.assertLess(float(np.max(np.abs(ghz - self.COARSE) / self.COARSE)),
                        1e-15)
        plan = align_frequencies([self.COARSE, ghz])
        self.assertTrue(plan.identical)
        self.assertEqual(plan.interpolated, [False, False])
        self.assertIn("no interpolation", " ".join(plan.notes))

    def test_an_identical_grid_drops_nothing_and_says_so(self):
        """
        MUTATION: count `dropped` from the span test on an identical grid too.

        The ulp GRID_RTOL exists to forgive lands on the ENDPOINTS as well.
        Measured with the two spellings of one 1.001 - 1.091 GHz sweep -- a Hz
        file listing 1001000000.0 and a GHz file listing 1.001, which parses to
        1000999999.9999999 -- the intersection's lower edge is the Hz file's
        first point, and the GHz file's first sample counts as outside it.  The
        report then says "1 of 91 points fall outside the common span and are
        dropped" about a point that is in the answer.
        """
        hz = np.array([1001000000.0 + 1e6 * i for i in range(91)])
        ghz = np.array([1.001 + 0.001 * i for i in range(91)]) * 1e9
        self.assertNotEqual(hz[0], ghz[0])          # the precondition
        plan = align_frequencies([hz, ghz], aliases=["EM", "PKG"])
        self.assertTrue(plan.identical)
        self.assertEqual(plan.dropped, [0, 0])
        self.assertEqual(plan.warnings, [])
        self.assertEqual(len(plan.freqs), 91)

    def test_a_grid_apart_by_more_than_the_tolerance_is_not_identical(self):
        """The tolerance must not be so wide that a real regrid slips through."""
        shifted = self.COARSE * (1.0 + 1e-6)
        plan = align_frequencies([self.COARSE, shifted])
        self.assertFalse(plan.identical)

    def test_the_finer_grid_is_adopted_and_the_coarser_step_is_reported(self):
        """
        MUTATION: report the adopted grid's step as the resolution.  Upsampling
        recovers no information: the answer's real resolution is the 100 MHz of
        the coarse file, not the 10 MHz of the grid it was resampled onto.
        """
        plan = align_frequencies(
            [self.FINE, self.COARSE],
            [delay_file(self.FINE, 1e-10, "a").s,
             delay_file(self.COARSE, 1e-10, "b").s],
            aliases=["EM", "PKG"])
        self.assertEqual(plan.grid_from, 0)
        self.assertEqual(len(plan.freqs), 901)
        self.assertEqual(plan.interpolated, [False, True])
        self.assertAlmostEqual(plan.effective_step, 100e6, delta=1e3)
        self.assertIn("largest step in PKG", " ".join(plan.notes))

    def test_no_extrapolation_and_the_dropped_points_are_counted(self):
        """
        MUTATION: clamp instead of dropping.  A value outside a file's span is
        not a measurement of anything, and silently clamping produces a number
        for a frequency the file never saw.
        """
        wide = np.linspace(1e9, 20e9, 191)
        plan = align_frequencies([wide, self.COARSE],
                                 aliases=["EM", "PKG"])
        self.assertAlmostEqual(plan.hi, 10e9, delta=1.0)
        self.assertLessEqual(float(plan.freqs[-1]), 10e9 + 1.0)
        self.assertGreater(plan.dropped[0], 0)
        self.assertIn("no extrapolation", " ".join(plan.warnings))

    def test_a_span_that_excludes_the_marker_refuses_loudly(self):
        """
        MUTATION: warn instead of raising, or silently move the marker.  The
        marker is the one frequency the user is reading a number at; a
        composition that cannot serve it is the wrong composition, not a
        smaller one.  The refusal has to name the file that stops short --
        "outside the common span" alone does not say which file to re-simulate.
        """
        em = np.linspace(1e9, 8e9, 71)
        pkg = np.linspace(2e9, 5e9, 31)
        with self.assertRaises(ComposeError) as cm:
            align_frequencies([em, pkg], aliases=["EM", "PKG"],
                              labels=["coil.s2p", "package.s3p"],
                              marker_hz=6e9)
        text = str(cm.exception)
        self.assertEqual(cm.exception.kind, pc.FAULT_SPAN)
        self.assertIn("6 GHz", text)
        self.assertIn("PKG", text)
        self.assertIn("package.s3p", text)
        self.assertIn("2 GHz - 5 GHz", text)
        # and the marker INSIDE the intersection is fine
        plan = align_frequencies([em, pkg], aliases=["EM", "PKG"],
                                 marker_hz=4e9)
        self.assertLessEqual(plan.lo, 4e9)
        self.assertGreaterEqual(plan.hi, 4e9)

    def test_spans_that_do_not_overlap_at_all_refuse(self):
        with self.assertRaises(ComposeError) as cm:
            align_frequencies([np.linspace(1e9, 5e9, 11),
                               np.linspace(6e9, 9e9, 11)],
                              aliases=["EM", "PKG"])
        self.assertEqual(cm.exception.kind, pc.FAULT_SPAN)
        self.assertIn("do not overlap", str(cm.exception))

    # ---- the phase step ---------------------------------------------------

    def test_a_coarse_file_warns_about_the_loss_the_chord_invents(self):
        """
        MUTATION: check max |S| after interpolating instead.  That check is
        structurally incapable of firing (the passive set is convex, so any
        convex combination stays inside it) and is not a passivity test either.

        Measured on a matched 1 ns delay line sampled every 100 MHz: the phase
        turns 2*pi*1e8*1e-9 = 36.0 degrees per interval, and the chord across
        that arc is short by 1 - cos(18 deg) = 4.9% -- 0.436 dB of insertion
        loss that is not in the file, straight into R and Q.
        """
        plan = align_frequencies(
            [self.FINE, self.COARSE],
            [delay_file(self.FINE, 1e-9, "a").s,
             delay_file(self.COARSE, 1e-9, "b").s],
            aliases=["EM", "PKG"])
        self.assertAlmostEqual(plan.phase_step_deg[1], 36.0, places=3)
        self.assertAlmostEqual(plan.fake_loss_frac(1), 0.0489, places=4)
        self.assertAlmostEqual(plan.fake_loss_db(1), 0.4359, places=3)
        joined = " ".join(plan.warnings)
        self.assertIn("PKG", joined)
        self.assertIn("36.0 deg", joined)
        self.assertIn("0.436 dB", joined)

    def test_a_step_past_60_degrees_is_refused(self):
        """
        MUTATION: warn instead of raising.  At 72 degrees the chord loses 19.1%
        of the amplitude (1.841 dB); the curve still looks like an inductor and
        every R and Q read off it is wrong.
        """
        with self.assertRaises(ComposeError) as cm:
            align_frequencies(
                [self.FINE, self.COARSE],
                [delay_file(self.FINE, 2e-9, "a").s,
                 delay_file(self.COARSE, 2e-9, "b").s],
                aliases=["EM", "PKG"], labels=["coil.s2p", "package.s2p"])
        text = str(cm.exception)
        self.assertEqual(cm.exception.kind, pc.FAULT_GRID)
        self.assertIn("PKG", text)
        self.assertIn("package.s2p", text)
        self.assertIn("72.0 deg", text)
        self.assertIn("1.841 dB", text)

    def test_the_refusal_can_be_overridden_but_not_by_accident(self):
        """The escape hatch exists (a user may know their file is smooth) and
        is not the default -- the same shape as the parser's `lenient`."""
        plan = align_frequencies(
            [self.FINE, self.COARSE],
            [delay_file(self.FINE, 2e-9, "a").s,
             delay_file(self.COARSE, 2e-9, "b").s],
            aliases=["EM", "PKG"], phase_refuse_deg=None)
        self.assertAlmostEqual(plan.phase_step_deg[1], 72.0, places=3)
        self.assertTrue(plan.warnings)

    def test_a_small_step_says_it_is_under_the_noise_floor(self):
        plan = align_frequencies(
            [self.FINE, self.COARSE],
            [delay_file(self.FINE, 1e-10, "a").s,
             delay_file(self.COARSE, 1e-10, "b").s],
            aliases=["EM", "PKG"])
        self.assertAlmostEqual(plan.phase_step_deg[1], 3.6, places=3)
        self.assertEqual(plan.warnings, [])
        self.assertIn("noise floor", " ".join(plan.notes))

    def test_a_recovered_step_near_the_fold_is_a_lower_bound(self):
        """
        np.unwrap always takes the branch under 180 degrees, so a recovered
        step anywhere near it could be the folded image of a larger one.  The
        flag is what makes the report say ">= N deg" rather than "N deg".
        """
        coarse = np.linspace(1e9, 10e9, 91)
        fine = np.linspace(1e9, 10e9, 3601)
        plan = align_frequencies(
            [fine, coarse],
            [delay_file(fine, 4.4e-9, "a").s, delay_file(coarse, 4.4e-9, "b").s],
            aliases=["EM", "PKG"], phase_refuse_deg=None)
        self.assertGreaterEqual(plan.phase_step_deg[1], pc.PHASE_ALIAS_DEG)
        self.assertTrue(plan.phase_aliased[1])
        self.assertIn("lower bound", " ".join(plan.warnings))

    # ---- sweep kinds and DC ----------------------------------------------

    def test_log_and_linear_sweeps_are_named(self):
        log = np.geomspace(1e9, 10e9, 91)
        plan = align_frequencies(
            [self.COARSE, log],
            [delay_file(self.COARSE, 1e-11, "a").s,
             delay_file(log, 1e-11, "b").s], aliases=["LIN", "LOG"])
        self.assertEqual(plan.spacing, ["linear", "logarithmic"])
        self.assertIn("differ in kind", " ".join(plan.notes))

    def test_dc_present_in_one_file_only_is_named_and_dropped(self):
        """
        MUTATION: drop the DC note.  0 Hz is the one frequency people read as a
        label rather than a measurement, and losing it silently is how a DC
        resistance disappears from a report.
        """
        with_dc = np.concatenate([[0.0], self.COARSE])
        plan = align_frequencies([with_dc, self.COARSE],
                                 aliases=["EM", "PKG"])
        joined = " ".join(plan.notes)
        self.assertIn("0 Hz", joined)
        self.assertIn("EM", joined)
        self.assertGreater(plan.lo, 0.0)

    def test_dc_in_every_file_is_kept(self):
        with_dc = np.concatenate([[0.0], self.COARSE])
        plan = align_frequencies([with_dc, with_dc], aliases=["EM", "PKG"])
        self.assertEqual(float(plan.freqs[0]), 0.0)
        self.assertIn("takes no part", " ".join(plan.notes))

    def test_a_marker_inside_a_wide_coarse_interval_is_flagged(self):
        plan = align_frequencies([self.FINE, self.COARSE],
                                 aliases=["EM", "PKG"], marker_hz=5.205e9)
        self.assertIn("interpolated, not measured", " ".join(plan.notes))

    # ---- the interpolation itself ----------------------------------------

    def test_interpolation_is_exact_at_coincident_points(self):
        """
        MUTATION: drop the exact-match write-back.  s[k] + (s[k+1]-s[k])*1.0 is
        not bit-identical to s[k+1], and a target grid whose points coincide
        with the source's is the normal case -- the endpoint always does.
        """
        f_src = self.COARSE
        s_src = delay_file(f_src, 1e-10, "a").s
        out = interpolate_s(f_src, s_src, self.FINE)
        hits = np.searchsorted(self.FINE, f_src)
        self.assertTrue(np.array_equal(out[hits], s_src))

    def test_interpolation_is_linear_in_S(self):
        """Halfway between two samples is the mean of them, exactly."""
        f_src = np.array([1e9, 2e9])
        s_src = np.zeros((2, 2, 2), dtype=complex)
        s_src[0] = 0.1 + 0.2j
        s_src[1] = 0.5 - 0.4j
        out = interpolate_s(f_src, s_src, np.array([1.5e9]))
        np.testing.assert_allclose(out[0], (s_src[0] + s_src[1]) / 2,
                                   rtol=1e-15, atol=0)

    def test_a_single_point_file_broadcasts(self):
        s = np.full((1, 2, 2), 0.3 + 0.1j)
        out = interpolate_s(np.array([2e9]), s, np.array([2e9]))
        self.assertTrue(np.array_equal(out, s))

    def test_a_non_monotonic_axis_is_refused_by_name(self):
        with self.assertRaises(ComposeError) as cm:
            align_frequencies([np.array([1e9, 3e9, 2e9]), self.COARSE],
                              aliases=["EM", "PKG"], labels=["bad.s2p", "ok"])
        self.assertIn("bad.s2p", str(cm.exception))


# ============================================================================
# The port namespace (requirement R2-4)
# ============================================================================

class TestPortNamespace(unittest.TestCase):

    def setUp(self):
        self.em_a, _, self.pkg = weld_files()
        self.net = compose([ComposeInput(self.em_a, "EM"),
                            ComposeInput(self.pkg, "PKG")])

    def test_the_tag_separator_is_not_a_colon(self):
        """
        MUTATION: use ':'.  parse_port_range('PKG:12') RAISES "Range must be
        start:step:stop" today -- the colon is its start:step:stop separator, so
        a colon-tagged port collides with the one syntax every port field in
        this repo goes through.
        """
        from pkg_rlc_core import parse_port_range
        self.assertNotEqual(pc.COMPOSE_TAG_SEP, ":")
        with self.assertRaises(ValueError):
            parse_port_range("PKG:12")
        # and the separator this module DID pick does not collide
        with self.assertRaises(ValueError):
            parse_port_range(f"PKG{pc.COMPOSE_TAG_SEP}12")

    def test_a_tag_scopes_the_TOKEN_it_is_written_on(self):
        """
        A RANGE is one token, so every range spelling still takes one tag --
        which is what the Help and the README show ('F2.40-42').  A list is
        several tokens, and the bare ones take the default scope; see
        test_a_BARE_token_after_a_tag_takes_the_DEFAULT_not_the_tag.
        """
        self.assertEqual(parse_scoped_ports("PKG.1-3", self.net), [3, 4, 5])
        self.assertEqual(parse_scoped_ports("PKG.1:1:3", self.net), [3, 4, 5])
        self.assertEqual(parse_scoped_ports("EM.2", self.net), [2])
        self.assertEqual(parse_scoped_ports("PKG.1,PKG.2,PKG.3", self.net),
                         [3, 4, 5])

    def test_an_untagged_field_takes_the_default_scope(self):
        self.assertEqual(parse_scoped_ports("1,2", self.net, "EM"), [1, 2])
        self.assertEqual(parse_scoped_ports("1,2", self.net, "PKG"), [3, 4])

    def test_an_untagged_field_with_no_default_is_refused(self):
        with self.assertRaises(ComposeError) as cm:
            parse_scoped_ports("1,2", self.net)
        self.assertIn("does not say which file", str(cm.exception))

    def test_a_tag_on_a_LATER_token_scopes_THAT_token(self):
        """
        This used to be REFUSED.  With a sticky tag 'EM.1,PKG.3' really does
        have two readings -- one field with two scopes, or EM scoping the lot
        -- and they differ by which port the second endpoint is, so refusing
        was right for a sticky tag.  Per-token has one reading, and the
        connection table needs it to exist: a short group is stored in ONE
        cell, so this is how a die-to-package tie is spelled there.

        MUTATION: restore the refusal and the single-cell short group loses
        its only spelling.
        """
        self.assertEqual(parse_scoped_ports("EM.1,PKG.3", self.net), [1, 5])

    def test_a_BARE_token_after_a_tag_takes_the_DEFAULT_not_the_tag(self):
        """
        The rule the whole change is for, and the one a reader is told
        everywhere else: a bare number is a port of the HOME file, in every
        mode, with no ordering condition on it.

        MUTATION: carry the last tag forward as the scope of the tokens after
        it.  Then 'PKG.1,2' reads as PKG ports 1 and 2 ([3, 4]) instead of PKG
        1 and EM 2 ([3, 2]) -- and it does not raise, so on a real file it is
        a plausible wrong answer.  The two orders below must therefore name
        the same two ports.
        """
        self.assertEqual(parse_scoped_ports("PKG.1,2", self.net, "EM"), [3, 2])
        self.assertEqual(parse_scoped_ports("2,PKG.1", self.net, "EM"), [2, 3])

    def test_ports_are_deduped_across_the_WHOLE_field(self):
        """
        parse_port_range dedupes within its own call, which was the whole
        field before and is now ONE TOKEN, so the dedup has to be redone here
        or '1,1' stops collapsing.  It is done on the GLOBAL index because two
        files' local port 1 are two different ports.
        """
        self.assertEqual(parse_scoped_ports("1,1,2", self.net, "EM"), [1, 2])
        self.assertEqual(parse_scoped_ports("EM.1,1", self.net, "EM"), [1])
        self.assertEqual(parse_scoped_ports("EM.1,PKG.1", self.net), [1, 3])

    def test_a_tag_with_no_port_after_it_is_refused(self):
        """
        MUTATION: return [] for it.  An EMPTY field legitimately means "nothing
        here"; 'PKG.' means "I meant to name a port and did not", and a
        silently empty port list is a spec that computes something other than
        what it says.
        """
        with self.assertRaises(ComposeError) as cm:
            parse_scoped_ports("PKG.", self.net)
        self.assertIn("no port in it", str(cm.exception))
        self.assertEqual(parse_scoped_ports("", self.net), [])
        self.assertEqual(parse_scoped_ports("   ", self.net), [])

    def test_a_bare_file_name_says_it_is_a_file(self):
        with self.assertRaises(ComposeError) as cm:
            parse_scoped_ports("PKG", self.net, "EM")
        self.assertIn("is the name of a FILE", str(cm.exception))

    def test_an_unknown_tag_lists_the_files_it_could_have_been(self):
        with self.assertRaises(ComposeError) as cm:
            parse_scoped_ports("NOPE.1", self.net)
        self.assertIn("NOPE", str(cm.exception))
        self.assertIn("EM", str(cm.exception))
        self.assertIn("PKG", str(cm.exception))

    def test_a_port_past_the_end_of_its_file_names_the_file(self):
        with self.assertRaises(ComposeError) as cm:
            parse_scoped_ports("PKG.9", self.net)
        text = str(cm.exception)
        self.assertIn("PKG.9", text)
        self.assertIn("package.s3p", text)
        self.assertIn("3 ports", text)

    def test_labels_and_descriptions_carry_the_file(self):
        """
        MUTATION: return a bare port number.  "port 305 has no return path" is
        unactionable on a 316-port combined network, which is the whole reason
        the namespace exists.
        """
        self.assertEqual(self.net.port_label(4), "PKG.2")
        d = self.net.describe_port(4)
        self.assertIn("PKG.2", d)
        self.assertIn("package.s3p", d)
        self.assertIn("board_ball", d)

    def test_describe_ports_groups_by_file_and_round_trips(self):
        """
        collapse_ports must never emit a space -- the DSL is whitespace-
        tokenised and the port field is parts[0], so '1-3, 7' would parse as the
        port field '1-3,' with a stray '7' where the keyword belongs.
        """
        text = self.net.describe_ports([1, 2, 3, 4, 5])
        self.assertEqual(text, "EM.1-2, PKG.1-3")
        for part in text.split(", "):
            self.assertNotIn(" ", part)
        self.assertEqual(parse_scoped_ports("PKG.1-3", self.net), [3, 4, 5])

    def test_a_core_port_index_error_comes_back_scoped(self):
        """
        MUTATION: let the core ValueError through.  _validate_port_indices is
        the ONE core message that names a bare port number, and on a composed
        network that number belongs to no file the user can see.
        """
        ts = TerminationSet(per_port={99: Signal("A")})
        with self.assertRaises(ComposeError) as cm:
            solve_composed(self.net, ts)
        text = str(cm.exception)
        self.assertEqual(cm.exception.kind, pc.FAULT_PORT)
        self.assertIn("100", text)
        self.assertIn("EM", text)
        self.assertIn("PKG", text)

    def test_duplicate_aliases_are_refused(self):
        with self.assertRaises(ComposeError) as cm:
            compose([ComposeInput(self.em_a, "X"), ComposeInput(self.pkg, "X")])
        self.assertIn("both tagged 'X'", str(cm.exception))

    def test_the_default_alias_is_the_repos_own_F1_F2_idiom(self):
        net = compose([self.em_a, self.pkg])
        self.assertEqual([b.alias for b in net.blocks], ["F1", "F2"])


# ============================================================================
# Cross-file links
# ============================================================================

class TestLinks(unittest.TestCase):

    def setUp(self):
        self.em_a, _, self.pkg = weld_files()
        self.net = compose([ComposeInput(self.em_a, "EM"),
                            ComposeInput(self.pkg, "PKG")])

    def test_elementwise_pairing(self):
        pairs = link_short(self.net, "EM.1,EM.2", "PKG.1,PKG.3")
        self.assertEqual([(p.port_i, p.port_j) for p in pairs],
                         [(0, 2), (1, 4)])

    def test_one_port_fans_out(self):
        """54 VSS balls onto one die pad is the ordinary flip-chip connection."""
        pairs = link_short(self.net, "EM.1", "PKG.1-3")
        self.assertEqual([(p.port_i, p.port_j) for p in pairs],
                         [(0, 2), (0, 3), (0, 4)])

    def test_a_BARE_token_on_a_link_side_takes_the_DEFAULT_scope(self):
        """
        `default` is the only thing that gives a bare token a file, and the CLI
        always passes one (`net.blocks[0].alias`, the first positional file),
        so this is the shape every real --compose-link takes.  The tag is
        per-token, so a bare token after a tag is the DEFAULT file and not the
        tagged one -- which is why the tests around this one spell both sides
        out rather than relying on a tag to carry.
        """
        pairs = link_short(self.net, "1,PKG.1", "PKG.2", default="EM")
        self.assertEqual([(p.port_i, p.port_j) for p in pairs],
                         [(0, 3), (2, 3)])
        with self.assertRaises(ComposeError) as cm:
            link_short(self.net, "PKG.1,2", "EM.1")
        self.assertIn("does not say which file", str(cm.exception))

    def test_a_length_mismatch_is_a_hard_error_that_echoes_the_end_pairs(self):
        """
        MUTATION: zip() the two lists (which truncates), or drop the echo.  An
        off-by-one in one file's numbering shifts EVERY pair; the end pairs are
        what makes that visible, because the first pair still looks right.
        """
        with self.assertRaises(ComposeError) as cm:
            link_short(self.net, "EM.1-2", "PKG.1-3")
        text = str(cm.exception)
        self.assertIn("EM.1 - PKG.1", text)
        self.assertIn("EM.2 - PKG.3", text)

    def test_an_empty_side_is_refused(self):
        with self.assertRaises(ComposeError):
            link_short(self.net, "EM.1", "")


# ============================================================================
# Pre-reduction (requirement R2-6)
# ============================================================================

class TestPreReduction(unittest.TestCase):

    def _pkg6(self, freqs):
        w = 2 * np.pi * np.asarray(freqs, dtype=float)
        els = [(1, 0, CAP(50e-15)), (2, 3, SER(0.2, 100e-12)),
               (3, 0, CAP(30e-15)), (4, 0, SER(0.4, 300e-12)),
               (5, 0, SER(0.05, 100e-12)), (6, 0, CAP(70e-15)),
               (2, 4, CAP(10e-15)), (3, 6, CAP(12e-15)), (1, 5, CAP(9e-15))]
        return as_file(freqs, port_y(6, els, [1, 2, 3, 4, 5, 6], w), 50.0,
                       "pkg6.s6p", [f"ball{i}" for i in range(1, 7)])

    def setUp(self):
        self.freqs = np.linspace(1e9, 10e9, 51)
        self.em, _, _ = weld_files(self.freqs)
        self.pkg = self._pkg6(self.freqs)

    def test_a_kept_port_keeps_its_ORIGINAL_local_number(self):
        """
        MUTATION: renumber the survivors 1..K.  Reducing a 60-port package and
        then calling its 6 survivors 1..6 is exactly the silent renumbering that
        makes an off-by-one in a mapping invisible -- the user is reading port
        numbers off an EM tool, not off this reduction.
        """
        net = compose([ComposeInput(self.em, "EM"),
                       ComposeInput(self.pkg, "PKG", keep=[2, 3, 5],
                                    gnd=[1, 6])])
        self.assertEqual(net.gport("PKG", 2), 3)
        self.assertEqual(net.gport("PKG", 3), 4)
        self.assertEqual(net.gport("PKG", 5), 5)
        self.assertEqual(net.port_label(5), "PKG.5")

    def test_a_removed_port_says_it_was_removed(self):
        """
        MUTATION: report it as "does not exist".  It DOES exist in the file;
        the composition threw it away, and the fix is different (add it to the
        keep list, not fix the port number).
        """
        net = compose([ComposeInput(self.em, "EM"),
                       ComposeInput(self.pkg, "PKG", keep=[2, 3, 5])])
        with self.assertRaises(ComposeError) as cm:
            net.gport("PKG", 4)
        text = str(cm.exception)
        self.assertIn("removed by the pre-reduction", text)
        self.assertIn("PKG", text)
        self.assertIn("keep list", text)

    def test_the_reduced_network_answers_what_the_full_one_answers(self):
        """
        The point of R2-6: it is a shortcut, not a different model.  Ports 1 and
        6 of the package are grounded, 4 is eliminated open, and the composed
        measurement must not move.
        """
        red = compose([ComposeInput(self.em, "EM"),
                       ComposeInput(self.pkg, "PKG", keep=[2, 3, 5],
                                    gnd=[1, 6])])
        full = compose([ComposeInput(self.em, "EM"),
                        ComposeInput(self.pkg, "PKG")])
        ts_r = TerminationSet(
            per_port={red.gport("PKG", 3) - 1: Signal("A"),
                      red.gport("PKG", 5) - 1: Ground()},
            couplings=[ShortPair(red.gport("EM", 1) - 1,
                                 red.gport("PKG", 2) - 1)])
        ts_f = TerminationSet(
            per_port={full.gport("PKG", 3) - 1: Signal("A"),
                      full.gport("PKG", 5) - 1: Ground(),
                      full.gport("PKG", 1) - 1: Ground(),
                      full.gport("PKG", 6) - 1: Ground()},
            couplings=[ShortPair(full.gport("EM", 1) - 1,
                                 full.gport("PKG", 2) - 1)])
        Zr, _, _ = compute_z_matrix(red.Y, red.freqs, ts_r)
        Zf, _, _ = compute_z_matrix(full.Y, full.freqs, ts_f)
        rel = float(np.max(np.abs(Zr - Zf)) / np.max(np.abs(Zf)))
        self.assertLess(rel, 1e-10, f"reduced vs full disagree by {rel:.2e}")

    def test_grounding_is_not_opening(self):
        """
        MUTATION: Schur-eliminate the gnd bucket instead of deleting its rows
        and columns.  A package's ground balls need the GND bucket or the answer
        is wrong -- the same rule reduce_snp.py documents.
        """
        w = 2 * np.pi * self.freqs
        Y = port_y(6, [(1, 0, CAP(50e-15)), (2, 3, SER(0.2, 100e-12)),
                       (3, 0, CAP(30e-15)), (4, 0, SER(0.4, 300e-12)),
                       (5, 0, SER(0.05, 100e-12)), (6, 0, CAP(70e-15)),
                       (2, 4, CAP(10e-15)), (3, 6, CAP(12e-15)),
                       (1, 5, CAP(9e-15))], [1, 2, 3, 4, 5, 6], w)
        grounded = reduce_block_y(Y, [1, 2], [3])       # 0-based: keep 2,3 gnd 4
        opened = reduce_block_y(Y, [1, 2], [])
        self.assertEqual(grounded.shape, opened.shape)
        self.assertFalse(np.allclose(grounded, opened))

    def test_a_port_in_both_buckets_is_refused(self):
        Y = np.eye(3, dtype=complex)[None, :, :].repeat(4, axis=0)
        with self.assertRaises(ComposeError) as cm:
            reduce_block_y(Y, [0, 1], [1])
        self.assertIn("both", str(cm.exception))

    def test_matched_elimination_differs_from_open(self):
        w = 2 * np.pi * self.freqs
        Y = port_y(4, [(1, 2, SER(0.5, 1e-9)), (2, 3, SER(0.5, 1e-9)),
                       (3, 4, SER(0.5, 1e-9)), (1, 0, CAP(20e-15)),
                       (4, 0, CAP(20e-15))], [1, 2, 3, 4], w)
        a = reduce_block_y(Y, [0, 3], method="open")
        b = reduce_block_y(Y, [0, 3], method="matched")
        self.assertFalse(np.allclose(a, b))

    def test_a_keep_list_naming_a_port_the_file_does_not_have_is_refused(self):
        with self.assertRaises(ComposeError) as cm:
            compose([ComposeInput(self.em, "EM"),
                     ComposeInput(self.pkg, "PKG", keep=[2, 99])])
        self.assertIn("pkg6.s6p", str(cm.exception))
        self.assertIn("6 ports", str(cm.exception))

    def test_a_ground_list_alone_keeps_everything_else(self):
        """
        MUTATION: default `keep` to every port INCLUDING the grounded ones.
        That is the one combination reduce_block_y refuses outright, so the
        natural spelling of "these four are ground balls" would raise "port 1 is
        in both the keep list and the ground list" -- an error about a list the
        user never wrote.
        """
        net = compose([ComposeInput(self.em, "EM"),
                       ComposeInput(self.pkg, "PKG", gnd=[1, 6])])
        block = net.blocks[1]
        self.assertEqual(block.local_ports, [2, 3, 4, 5])
        self.assertEqual(net.gport("PKG", 5), 2 + 4)

    def test_a_port_listed_twice_is_refused(self):
        """
        A repeated keep puts one port in two columns of the reduced block: a
        singular matrix, and a namespace with two names for one thing.
        """
        with self.assertRaises(ComposeError) as cm:
            compose([ComposeInput(self.pkg, "PKG", keep=[2, 3, 3])])
        self.assertIn("more than once", str(cm.exception))

    def test_a_singular_block_falls_back_per_frequency(self):
        """
        MUTATION: least-square the whole (F*m, m) stack at once.  np.linalg
        .solve raises for the ENTIRE stack when any one frequency's block is
        singular, and reshaping the stack into one tall system solves a
        different problem -- every frequency would get the same answer.
        """
        Y = np.zeros((4, 3, 3), dtype=complex)
        Y[:] = np.array([[2 + 1j, .3, .1], [.3, 3 + 2j, .2], [.1, .2, 1 + 1j]])
        Y[2, 2, :] = 0.0            # frequency 2: port 3 is an isolated node
        Y[2, :, 2] = 0.0
        out = reduce_block_y(Y, [0, 1])
        self.assertEqual(out.shape, (4, 2, 2))
        self.assertTrue(np.all(np.isfinite(out)))
        # the healthy frequencies are untouched by the sick one
        np.testing.assert_allclose(out[0], out[1], rtol=0, atol=0)
        np.testing.assert_allclose(out[0], out[3], rtol=0, atol=0)


# ============================================================================
# Export (requirement R2-7)
# ============================================================================

class TestExport(unittest.TestCase):
    """The only route to INDEPENDENT validation of a feature with no golden
    reference: the composed network goes out as a file anyone else can read."""

    def setUp(self):
        self.freqs = np.linspace(1e9, 6e9, 31)
        self.em_a, _, self.pkg = weld_files(self.freqs)
        self.net = compose([ComposeInput(self.em_a, "EM"),
                            ComposeInput(self.pkg, "PKG")])

    def test_the_composed_network_round_trips_through_parse_touchstone(self):
        """
        MUTATION: write n == 2 row-major.  Touchstone v1's 2-port column order
        is COLUMN-major (S11 S21 S12 S22) and n >= 3 is row-major; getting one
        side wrong transposes the file and nothing raises.

        Measured at EXPORT_DIGITS = 17: S comes back EXACTLY (0.000e+00), and
        the composed Y is recovered to ~1e-16 relative.  At 12 digits it is
        7.2e-14 -- above the PINV_RCOND = 1e-12 the reduction truncates at,
        which is why the default is 17.
        """
        with tempfile.TemporaryDirectory() as tmp:
            for sel, ext in (([1], ".s1p"), ([1, 2], ".s2p"),
                             ([1, 2, 3], ".s3p"), (None, ".s5p")):
                path = Path(tmp) / f"combo{ext}"
                write_composed_touchstone(path, self.net, ports=sel)
                back = parse_touchstone(path)
                want_Y = self.net.Y if sel is None else reduce_block_y(
                    self.net.Y, [s - 1 for s in sel])
                want_S = y_to_s(want_Y, 50.0)
                self.assertEqual(back.nports, want_Y.shape[-1])
                self.assertEqual(len(back.freqs), len(self.net.freqs))
                np.testing.assert_allclose(back.freqs, self.net.freqs, rtol=0,
                                           atol=1e-6)
                rel = float(np.max(np.abs(back.s - want_S)) /
                            np.max(np.abs(want_S)))
                self.assertLess(rel, 1e-14, f"{ext} round trip is {rel:.2e}")

    def test_the_exported_port_names_carry_the_file_tags(self):
        """
        MUTATION: write bare port numbers.  Once the file leaves this tool the
        only record of which file a port came from is what is written in it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "combo.s5p"
            write_composed_touchstone(path, self.net)
            back = parse_touchstone(path)
        self.assertEqual(back.port_names[0], "EM.1 coil_top")
        self.assertEqual(back.port_names[3], "PKG.2 board_ball")

    def test_the_exported_file_says_the_references_are_welded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "combo.s5p"
            write_composed_touchstone(path, self.net)
            text = path.read_text()
        self.assertIn("welded", text)
        self.assertIn("EM = coil_with_return.s2p", text)

    def test_a_reimported_composition_measures_what_the_composition_did(self):
        """
        The validation the export exists for, done end to end: build the
        network, measure it, write it, read it back with the SHIPPED parser,
        and measure the file the same way.  Nothing in the second measurement
        touches pkg_rlc_compose except the port numbering.
        """
        ts = TerminationSet(
            per_port={self.net.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(self.net.gport("EM", 1) - 1,
                                 self.net.gport("PKG", 1) - 1),
                       ShortPair(self.net.gport("EM", 2) - 1,
                                 self.net.gport("PKG", 3) - 1)])
        Z_before, _, _ = compute_z_matrix(self.net.Y, self.net.freqs, ts)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "combo.s5p"
            write_composed_touchstone(path, self.net)
            back = parse_touchstone(path)
        Z_after, _, _ = compute_z_matrix(s_to_y(back.s, back.z0), back.freqs, ts)
        rel = float(np.max(np.abs(Z_after - Z_before)) /
                    np.max(np.abs(Z_before)))
        self.assertLess(rel, 1e-10, f"re-imported network differs by {rel:.2e}")

    def test_a_non_reciprocal_two_port_survives_the_column_major_quirk(self):
        """
        Touchstone v1 writes a 2-port COLUMN-major (S11 S21 S12 S22) and n >= 3
        row-major.  Getting one side wrong transposes the file and nothing
        raises.

        It needs a deliberately NON-RECIPROCAL fixture, and that is the point:
        every passive network has S12 == S21, so on any physical composition the
        transpose is INVISIBLE and a test built from one passes with the bug in
        place.  Here S21 = 0.6 and S12 = 0.1, so a transpose swaps them.
        """
        freqs = np.linspace(1e9, 3e9, 5)
        S = np.zeros((len(freqs), 2, 2), dtype=complex)
        S[:, 0, 0] = 0.20 + 0.05j
        S[:, 1, 0] = 0.60 - 0.10j          # S21, the forward path
        S[:, 0, 1] = 0.10 + 0.02j          # S12, the reverse path -- different
        S[:, 1, 1] = 0.30 - 0.04j
        amp = TouchstoneData(nports=2, freqs=freqs, s=S, z0=50.0,
                             port_names=["in", "out"], source_path="amp.s2p")
        net = compose([ComposeInput(amp, "AMP")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "amp_out.s2p"
            write_composed_touchstone(path, net)
            back = parse_touchstone(path)
        np.testing.assert_allclose(back.s, S, rtol=1e-12, atol=1e-14)
        self.assertNotAlmostEqual(abs(back.s[0, 1, 0]), abs(back.s[0, 0, 1]))

    def test_exporting_a_port_that_does_not_exist_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ComposeError):
                write_composed_touchstone(Path(tmp) / "x.s2p", self.net,
                                          ports=[1, 99])


# ============================================================================
# Composition mechanics
# ============================================================================

class TestCompose(unittest.TestCase):

    def setUp(self):
        self.em_a, _, self.pkg = weld_files()

    def test_the_block_layout_is_offsets_in_file_order(self):
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")])
        self.assertEqual(net.nports, 5)
        self.assertEqual([b.offset for b in net.blocks], [0, 2])
        self.assertEqual(net.Y.shape, (len(net.freqs), 5, 5))

    def test_Y_is_exactly_block_diagonal(self):
        """
        The cross blocks are EXACTLY zero -- the links are terminations, not
        entries in Y.  That is also the fact requirement R2-8 turns into a note
        about the attribution baseline: on an all-open baseline the two files
        are disconnected islands, so a package-only element's contribution is
        exactly 0 while the residual reconciles perfectly.
        """
        net = compose([ComposeInput(self.em_a, "EM"),
                       ComposeInput(self.pkg, "PKG")])
        cross = net.Y[:, :2, 2:]
        self.assertEqual(float(np.max(np.abs(cross))), 0.0)

    def test_each_file_is_converted_with_its_OWN_z0(self):
        """
        MUTATION: convert both with one z0.  Y is z0-invariant (measured
        1.049e-17 between z0 = 50 and z0 = 75) but S is NOT, so reading a 75-ohm
        file as if it were 50-ohm is a different network.
        """
        freqs = np.linspace(1e9, 5e9, 21)
        w = 2 * np.pi * freqs
        Y1 = port_y(2, [(1, 2, SER(1.0, 1e-9)), (1, 0, CAP(10e-15))],
                    [1, 2], w)
        a = as_file(freqs, Y1, 75.0, "a.s2p")
        net = compose([ComposeInput(a, "A")])
        np.testing.assert_allclose(net.Y, Y1, rtol=1e-9, atol=0)

    def test_differing_z0_is_noted_and_nothing_is_renormalised(self):
        freqs = np.linspace(1e9, 5e9, 21)
        w = 2 * np.pi * freqs
        a = as_file(freqs, port_y(1, [(1, 0, CAP(10e-15))], [1], w), 75.0,
                    "a.s1p")
        b = as_file(freqs, port_y(1, [(1, 0, CAP(20e-15))], [1], w), 50.0,
                    "b.s1p")
        net = compose([ComposeInput(a, "A"), ComposeInput(b, "B")])
        joined = " ".join(net.notes)
        self.assertIn("nothing to renormalise", joined)
        self.assertIn("1.049e-17", joined)

    def test_a_composition_of_one_file_is_that_file(self):
        net = compose([self.em_a])
        np.testing.assert_allclose(net.Y, s_to_y(self.em_a.s, self.em_a.z0),
                                   rtol=0, atol=0)

    def test_composing_nothing_is_refused(self):
        with self.assertRaises(ComposeError):
            compose([])

    def test_a_bad_alias_is_refused(self):
        for bad in ("1F", "E M", "EM.1", ""):
            with self.subTest(alias=bad):
                if bad == "":
                    continue        # empty means "use the default"
                with self.assertRaises(ComposeError):
                    compose([ComposeInput(self.em_a, bad)])

    def test_files_on_different_grids_are_resampled_onto_the_finer(self):
        fine = np.linspace(1e9, 10e9, 181)
        coarse = np.linspace(1e9, 10e9, 91)
        em, _, _ = weld_files(fine)
        _, _, pkg = weld_files(coarse)
        net = compose([ComposeInput(em, "EM"), ComposeInput(pkg, "PKG")])
        self.assertEqual(len(net.freqs), 181)
        self.assertFalse(net.plan.identical)
        self.assertEqual(net.plan.interpolated, [False, True])
        # the coarse file's own samples come back exactly
        k_fine = np.searchsorted(net.freqs, coarse)
        Y_pkg = net.Y[k_fine, 2:, 2:]
        np.testing.assert_allclose(Y_pkg, s_to_y(pkg.s, pkg.z0),
                                   rtol=1e-12, atol=0)


class TestComposeErrorContract(unittest.TestCase):
    """Same contract as TouchstoneParseError: str(e) IS the whole report."""

    def test_the_verdict_line_comes_first(self):
        e = ComposeError(pc.FAULT_SPAN, "headline", ["a", "b"], "do this")
        text = str(e)
        self.assertTrue(text.startswith("CANNOT COMPOSE"))
        self.assertIn("headline", text)
        self.assertIn("  a", text)
        self.assertIn("Try: do this", text)

    def test_it_subclasses_ValueError(self):
        self.assertTrue(issubclass(ComposeError, ValueError))


if __name__ == "__main__":
    unittest.main()
