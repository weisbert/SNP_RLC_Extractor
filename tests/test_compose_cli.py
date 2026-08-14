"""
The composition command line: several Touchstone files driven end to end.

`tests/test_compose.py` pins the arithmetic of `pkg_rlc_compose`.  This file
pins the CLI on top of it, and its job is the five things a wrapper can get
wrong on its own:

  * a bad flag is REFUSED with a message that names the offending token.  Every
    refusal test asserts on the token AND on the way out -- "raises SystemExit"
    or "exit 2" alone would have passed before any of this existed, because
    exit 2 is also what argparse produces for an unrelated typo;

  * the PORT NAMESPACE survives the round trip.  "port 305" is unactionable on
    a 316-port combined network, so every port that goes in carries a file tag
    and every port that comes out -- including the ones inside a refusal
    written by pkg_rlc_core, which knows nothing about files -- names its file;

  * the numbers printed are the ENGINE'S numbers.  Nothing here re-derives an
    impedance: the assertions compare against `pkg_rlc_compose` and, through
    it, against `compute_z_matrix`.  A CLI that quietly composed its own
    network would be the worst available failure, because it would look right;

  * the reference-node check is MANDATORY OUTPUT.  A weld makes nothing raise
    and makes no number look wrong -- measured on the fixture below, the
    package ground pad grounded, open and through 1 nH give L_eff = 2.1454 nH,
    bit-identical -- so a report that omits it is a plausible wrong answer;

  * a PROPOSAL is not a commitment.  --compose-propose prints and stops.

The network builders are duplicated from the scratchpad probes rather than
imported from tests/test_compose.py: that file belongs to the compose module
and is edited on its own schedule, and twenty lines of nodal assembly is a
smaller cost than a test that fails for a reason in another test file.  Every
fixture has an analytically known answer and nothing under test is used to
build one.

Numbers that are hard-coded were measured in this session and the measurement
is written beside them.  Every guard was mutation-checked; the mutation that
defeats it is named in the test.

The whole module is Tk-free: `main(["--cli", ...])` never reaches pkg_rlc_gui,
and the one test that runs without --cli asserts that it did not import it.
"""

from __future__ import annotations

import contextlib
import csv
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import numpy as np  # noqa: E402

import pkg_rlc.physics.attrib as at  # noqa: E402
import pkg_rlc.physics.compose as pc  # noqa: E402
import pkg_rlc.frontend.cli as ex  # noqa: E402
from generate_test_snp import write_touchstone  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    Ground,
    ShortPair,
    Signal,
    TerminationSet,
    compute_z_matrix,
    parse_kv_rlc_params,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
    y_to_s,
)


# ============================================================================
# Network builders:  branches -> nodal Y -> port Y
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


# --- the reference-node fixture, written to disk ---------------------------
#
#   board ball --[100 pH pkg trace]-- sig pad == die pad --[2 nH coil]-- die
#   return, and the die return reaches the board through a 100 pH package
#   GROUND LEAD.  Probe at the board ball, reference = board ground.
#
#   EM_A brings the die return out as PORT 2, so the ground lead carries it.
#   EM_B's die return IS its reference node, which the block-diagonal stack
#   welds straight to the board plane: the ground lead is bypassed and 100 pH
#   of it vanishes with nothing said.
#
# The two die pads are deliberately unequal (2 fF / 8 fF) for the reason
# test_compose.py records: with equal pads the EM block is port-symmetric and a
# swapped mapping reproduces the standalone number exactly.

MARK_GHZ = 5.205
MARK_HZ = MARK_GHZ * 1e9
_F = np.linspace(1e9, 10e9, 91)          # 5.2 GHz is the sample nearest MARK
L_COIL, R_COIL = 2.0e-9, 1.5
L_TRACE, R_TRACE = 100e-12, 0.2
L_GLEAD, R_GLEAD = 100e-12, 0.05
C_DIE_TOP, C_DIE_RET, C_PKG_PAD = 2e-15, 8e-15, 4e-15

_TMP: Path = Path()
EM_A = EM_B = PKG = DIE_N = PKG_N = COARSE = ""


def _write(name, freqs, Y, z0, port_names):
    write_touchstone(_TMP / name, freqs, y_to_s(Y, z0), z0, port_names,
                     digits=17)
    return str(_TMP / name)


def setUpModule():
    global _TMP, EM_A, EM_B, PKG, DIE_N, PKG_N, COARSE
    _TMP = Path(tempfile.mkdtemp(prefix="compose_cli_"))
    w = 2 * np.pi * _F

    em_a = port_y(2, [(1, 2, SER(R_COIL, L_COIL)),
                      (1, 0, CAP(C_DIE_TOP)), (2, 0, CAP(C_DIE_RET))],
                  [1, 2], w)
    em_b = port_y(1, [(1, 0, SER(R_COIL, L_COIL)), (1, 0, CAP(C_DIE_TOP))],
                  [1], w)
    pkg = port_y(3, [(1, 2, SER(R_TRACE, L_TRACE)),
                     (3, 0, SER(R_GLEAD, L_GLEAD)),
                     (1, 0, CAP(C_PKG_PAD)), (2, 0, CAP(C_PKG_PAD)),
                     (3, 0, CAP(C_PKG_PAD))], [1, 2, 3], w)
    EM_A = _write("coil_with_return.s2p", _F, em_a, 50.0,
                  ["coil_top", "die_return"])
    EM_B = _write("coil_no_return.s1p", _F, em_b, 50.0, ["coil_top"])
    PKG = _write("package.s3p", _F, pkg, 50.0,
                 ["sig_pad", "board_ball", "gnd_pad"])

    # The same package on a COARSER grid, for the frequency-plan report.  Half
    # the span and a fifth of the points, so it is the one that gets
    # interpolated and the one whose max step becomes the effective resolution.
    fc = np.linspace(2e9, 8e9, 13)
    pkg_c = port_y(3, [(1, 2, SER(R_TRACE, L_TRACE)),
                       (3, 0, SER(R_GLEAD, L_GLEAD)),
                       (1, 0, CAP(C_PKG_PAD)), (2, 0, CAP(C_PKG_PAD)),
                       (3, 0, CAP(C_PKG_PAD))], [1, 2, 3],
                   2 * np.pi * fc)
    COARSE = _write("package_coarse.s3p", fc, pkg_c, 50.0,
                    ["sig_pad", "board_ball", "gnd_pad"])

    # A NAMED pair for the proposal.  Every one of the four cases the matcher
    # distinguishes is present: one-to-one (with a trailing space and a
    # different case, so trim + casefold are exercised), one-to-many, an
    # ambiguous 2-to-3, and unmatched ports on BOTH sides.
    die = port_y(5, [(k, 0, CAP(1e-15 * (k + 1))) for k in range(1, 6)],
                 [1, 2, 3, 4, 5], w)
    pkgn = port_y(7, [(k, 0, CAP(2e-15 * (k + 1))) for k in range(1, 8)],
                  [1, 2, 3, 4, 5, 6, 7], w)
    DIE_N = _write("die_named.s5p", _F, die, 50.0,
                   ["vdd", "vss", "sig", "sig", "spare"])
    PKG_N = _write("pkg_named.s7p", _F, pkgn, 50.0,
                   ["VDD ", "vss", "vss", "sig", "sig", "sig", "other"])


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


# ============================================================================
# Harness
# ============================================================================

def run(argv: list[str]) -> tuple[int, str, str]:
    """main() in-process, with stdout and stderr captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ex.main(argv)
    return rc, out.getvalue(), err.getvalue()


def flat(text: str) -> str:
    """Message text with the line wrapping taken out, for `assertIn`."""
    return " ".join(text.split())


def engine_net(*inputs, marker=MARK_HZ):
    """
    The composed network, built WITHOUT going through the CLI.

    Deliberately not by calling into pkg_rlc_extractor: a test that got its
    reference from the code under test would pass whatever that code did.
    """
    entries = [pc.ComposeInput(parse_touchstone(p), alias=a)
               for a, p in inputs]
    return pc.compose(entries, marker_hz=marker)


def L_at(freqs, Z, freq_hz=MARK_HZ):
    k = int(np.argmin(np.abs(np.asarray(freqs) - freq_hz)))
    return float(np.asarray(Z)[k].imag / (2 * np.pi * freqs[k]))


# The base command: configuration A, both ends of the coil wired to the
# package, probed at the board ball.  Measured through this CLI at the 5.2 GHz
# sample nearest MARK: L = 2.2501 nH (the coil plus both package leads).
BASE_A = ["--cli", EM_A, "--compose-alias", "EM",
          "--compose-link", "EM.1 short_to PKG.1",
          "--compose-link", "EM.2 short_to PKG.3",
          "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)]


def base_a() -> list[str]:
    """BASE_A with the package path filled in (module globals are late)."""
    return ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
            "--compose-link", "EM.1 short_to PKG.1",
            "--compose-link", "EM.2 short_to PKG.3",
            "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)]


def base_b() -> list[str]:
    """Configuration B: the welded one."""
    return ["--cli", EM_B, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
            "--compose-link", "EM.1 short_to PKG.1",
            "--gnd", "PKG.3",
            "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)]


# ============================================================================
# Refusals
# ============================================================================

class TestComposeRefusals(unittest.TestCase):
    """
    Every bad flag exits 2 and says what was wrong, by name.

    Asserting only on the exit code is what these must NOT do: argparse exits 2
    for an unrelated typo, so the offending token has to be in the message or
    the test proves nothing.
    """

    def refuse(self, argv: list[str], *must_contain: str) -> str:
        rc, out, err = run(argv)
        self.assertEqual(rc, 2, f"expected exit 2, got {rc}\n{out}\n{err}")
        for frag in must_contain:
            self.assertIn(frag, flat(err))
        return err

    def test_a_compose_flag_without_compose_names_itself_and_the_parent(self):
        # MUTATION: drop --compose-link from _compose_dependent_flags -- the
        # flag then does nothing at all, in silence, on a single-file run.
        self.refuse(["--cli", EM_A, "--mode", "gnd", "--porta", "1",
                     "--compose-link", "EM.1 short_to PKG.1"],
                    "--compose-link", "--compose [ALIAS=]FILE")

    def test_every_compose_flag_is_in_the_dependency_list(self):
        """
        The list is what makes an inert flag loud, so it has to be complete.

        MUTATION: forget ONE of them when adding a flag. The forgotten flag is
        then silently ignored on a single-file run, which is exactly the
        failure mode the check exists for.
        """
        parser = ex._make_arg_parser()
        declared = {a for a in vars(parser.parse_args(["x"]))
                    if a.startswith("compose_")}
        args = parser.parse_args(["--cli", "x"])
        for attr in sorted(declared):
            fresh = parser.parse_args(["--cli", "x"])
            cur = getattr(fresh, attr)
            setattr(fresh, attr, ["EM.1"] if isinstance(cur, list) or cur is None
                    else "EM.1")
            self.assertTrue(
                ex._compose_dependent_flags(fresh),
                f"{attr} is not in _compose_dependent_flags, so setting it "
                f"without --compose does nothing and says nothing")
        self.assertFalse(ex._compose_dependent_flags(args))

    def test_short_is_refused_and_points_at_compose_link(self):
        """
        MUTATION: pass --short through parse_short_pairs on a composed network.
        '3-4' is then read as global ports 3 and 4, which belong to whichever
        file was stacked first -- a silent wrong answer, and the user has no
        way to spell what they meant.
        """
        self.refuse(base_a() + ["--short", "1-2"],
                    "--short does not work on a composed network",
                    "--compose-link")

    def test_compose_without_cli_refuses_instead_of_opening_the_gui(self):
        """
        MUTATION: drop the guard in main(). The GUI opens on ONE file, every
        other file and every link silently discarded, and the window looks
        completely ordinary.
        """
        rc, _out, err = run([EM_A, "--compose", f"PKG={PKG}"])
        self.assertEqual(rc, 2)
        self.assertIn("--compose needs --cli", err)
        self.assertNotIn("pkg_rlc.frontend.app", sys.modules)

    def test_diagnose_covers_EVERY_file_of_the_composition(self):
        """
        MUTATION: diagnose only the positional file. The command then reports
        "no inconsistency found" about a set of files it opened one of --
        which is the exact shape of wrong answer --diagnose exists to prevent.
        """
        rc, out, err = run(["--diagnose", EM_A, "--compose", f"PKG={PKG}"])
        self.assertEqual(rc, 0, err)
        self.assertIn("[1/2]", out)
        self.assertIn("[2/2]", out)
        self.assertIn("coil_with_return.s2p", out)
        self.assertIn("package.s3p", out)
        # ONE bad file anywhere in the set decides the exit code, and it has
        # to be checked in BOTH positions.
        # MUTATION: `worst = kind` unconditionally. With the bad file last the
        # exit code is still 1 and the test passes; with it FIRST, the good
        # file that follows resets the verdict and --diagnose exits 0 on a set
        # containing a file it has just declared broken.
        bad = _TMP / "broken.s3p"
        bad.write_text("# HZ S RI R 50\n1e9 0.1 0.2 0.3\n")
        rc, out, _err = run(["--diagnose", EM_A, "--compose", f"PKG={bad}"])
        self.assertEqual(rc, 1, out)
        rc, out, _err = run(["--diagnose", str(bad), "--compose", f"EM={EM_A}"])
        self.assertEqual(rc, 1, out)

    def test_a_keep_list_with_no_file_tag_is_refused(self):
        # MUTATION: default the tag to the first file. A package's keep list
        # then lands on the die, which has those port numbers too.
        self.refuse(base_a() + ["--compose-keep", "1-3"],
                    "--compose-keep '1-3' does not say which file",
                    "Known files: EM, PKG")

    def test_a_keep_list_naming_an_unknown_file_is_refused(self):
        self.refuse(base_a() + ["--compose-keep", "NOPE.1-3"],
                    "'NOPE' is not one of the composed files",
                    "Known files: EM, PKG")

    def test_an_unknown_tag_in_a_port_field_names_the_known_files(self):
        rc, _out, err = run(base_a()[:-2] + ["--porta", "NOPE.1", "--freq",
                                             str(MARK_GHZ)])
        self.assertEqual(rc, 2)
        self.assertIn("'NOPE' is not one of the files", flat(err))
        self.assertIn("known files: EM, PKG", flat(err))

    def test_a_marker_outside_the_common_span_is_refused_by_name(self):
        """
        The CLI passes --freq to compose() as the marker, so a composition that
        cannot serve the frequency being read is refused at load.

        MUTATION: stop passing marker_hz. The run then succeeds and reports a
        number at whatever grid point argmin lands on -- 1 GHz for a --freq of
        0.1 -- with nothing saying the requested frequency was not in the file.
        """
        argv = [x if x != str(MARK_GHZ) else "0.1" for x in base_a()]
        rc, _out, err = run(argv)
        self.assertEqual(rc, 2)
        self.assertIn("marker frequency 100 MHz is outside", flat(err))
        self.assertIn("common span: 1 GHz - 10 GHz", flat(err))


# ============================================================================
# The port namespace on the command line
# ============================================================================

class TestPortNamespace(unittest.TestCase):

    def gnd_run(self, extra: list[str]) -> tuple[int, str, str]:
        return run(base_a()[:-4] + extra + ["--freq", str(MARK_GHZ)])

    def test_a_scoped_probe_measures_the_port_the_tag_names(self):
        """
        The whole point of the namespace, checked against the engine.

        MUTATION: resolve every port field against the FIRST file. --porta
        PKG.2 then measures EM port 2, which exists, and reports a plausible
        number for the wrong node.
        """
        rc, out, err = run(base_a())
        self.assertEqual(rc, 0, err)

        net = engine_net(("EM", EM_A), ("PKG", PKG))
        term = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A")},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1),
                       ShortPair(net.gport("EM", 2) - 1,
                                 net.gport("PKG", 3) - 1)])
        Z, _n, _w = compute_z_matrix(net.Y, net.freqs, term)
        want = L_at(net.freqs, Z[:, 0, 0])
        # 2.2501 nH: coil + package trace + package ground lead, measured
        # through this CLI and independently through compute_z_matrix.
        self.assertAlmostEqual(want * 1e9, 2.2501, places=3)
        self.assertIn(f"L      = {want * 1e9:.4g} nH", out)

    def test_an_untagged_port_field_scopes_to_the_positional_file(self):
        """
        The default scope is the measured affordable option (a per-row file
        column costs 451 px against a 431 px viewport), so it has to be exact.

        MUTATION: default to the LAST file. '--porta 1' then means the
        package's first port, so BOTH assertions below have to flip -- which is
        why this test runs with NO --compose-link at all. With the base spec's
        links in force, EM.1 and PKG.1 are SHORTED, i.e. the same node, and
        every comparison between them passes whatever the scope resolves to:
        the first version of this test read green under the mutation for
        exactly that reason.
        """
        base = ["--cli", EM_A, "--compose-alias", "EM",
                "--compose", f"PKG={PKG}", "--mode", "gnd",
                "--freq", str(MARK_GHZ)]
        rc_bare, out_bare, err = run(base + ["--porta", "1"])
        self.assertEqual(rc_bare, 0, err)
        rc_em, out_em, err = run(base + ["--porta", "EM.1"])
        self.assertEqual(rc_em, 0, err)
        rc_pkg, out_pkg, err = run(base + ["--porta", "PKG.1"])
        self.assertEqual(rc_pkg, 0, err)

        def z_line(text):
            return [ln for ln in text.splitlines()
                    if ln.strip().startswith("Z      =")][0]

        # precondition: the two candidates really are distinguishable here,
        # or the assertions below are a tautology (see the docstring)
        self.assertNotEqual(z_line(out_em), z_line(out_pkg))
        self.assertEqual(z_line(out_bare), z_line(out_em))
        self.assertNotEqual(z_line(out_bare), z_line(out_pkg))

    def test_a_semicolon_grounds_ports_in_two_files_at_once(self):
        """
        ';' is the field separator because a tag scopes a WHOLE field --
        parse_scoped_ports refuses 'PKG.3,EM.2' rather than guess -- and one
        ground list touching two files is an ordinary spec.

        MUTATION: split on ',' instead. 'PKG.3;EM.2' then reaches
        parse_port_range as one token and raises about a range.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-link", "EM.1 short_to PKG.1",
             "--mode", "gnd", "--porta", "PKG.2", "--gnd", "PKG.3;EM.2",
             "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)

        net = engine_net(("EM", EM_A), ("PKG", PKG))
        term = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A"),
                      net.gport("PKG", 3) - 1: Ground(),
                      net.gport("EM", 2) - 1: Ground()},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        Z, _n, _w = compute_z_matrix(net.Y, net.freqs, term)
        self.assertIn(f"L      = {L_at(net.freqs, Z[:, 0, 0]) * 1e9:.4g} nH",
                      out)

    def test_a_scoped_mport_reaches_the_coupling_path(self):
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-link", "EM.1 short_to PKG.1",
             "--mode", "coupling",
             "--mport", "coil = EM.1 / EM.2",
             "--mport", "ball = PKG.2",
             "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        net = engine_net(("EM", EM_A), ("PKG", PKG))
        term = TerminationSet(
            per_port={net.gport("EM", 1) - 1: Signal("coil", +1),
                      net.gport("EM", 2) - 1: Signal("coil", -1),
                      net.gport("PKG", 2) - 1: Signal("ball", +1)},
            couplings=[ShortPair(net.gport("EM", 1) - 1,
                                 net.gport("PKG", 1) - 1)])
        Zmat, _n, _w = compute_z_matrix(net.Y, net.freqs, term)
        k = int(np.argmin(np.abs(net.freqs - MARK_HZ)))
        self.assertIn(f"{Zmat[k, 0, 1].real:.4g}", out)
        self.assertIn(f"{Zmat[k, 0, 0].imag:.4g}", out)

    def test_a_short_that_merges_two_probes_names_BOTH_files(self):
        """
        Core's conflicting-signal-groups refusal is the message a composed
        network hits FIRST -- a cross-file link is precisely what merges two
        probes -- and it is the ONE core message whose port numbers are
        0-BASED (`merge_terms` prints the Union-Find member list).

        MUTATION 1: do not translate it. Two bare global indices survive and
        the reader counts blocks by hand.
        MUTATION 2: translate it as 1-based, like every other core message.
        Measured: EM.2 (global 2) shorted to PKG.3 (global 5) reports
        "Ports [1, 4]", which then reads as EM.1 and PKG.2 -- two real ports
        that have nothing to do with the mistake, named with total confidence.
        """
        rc, _out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-link", "EM.2 short_to PKG.3",
             "--mode", "coupling",
             "--mport", "coil = EM.1 / EM.2",
             "--mport", "ball = PKG.2 / PKG.3",
             "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("conflicting signal groups", f)
        self.assertIn("port 2 is EM.2 (coil_with_return.s2p port 2, "
                      "die_return)", f)
        self.assertIn("port 5 is PKG.3 (package.s3p port 3, gnd_pad)", f)

    def test_a_core_refusal_comes_back_with_the_port_TRANSLATED(self):
        """
        pkg_rlc_core knows nothing about files, so its "Port(s) 4 are listed
        both as a probe ... and as ground" names a global index.  On a composed
        network that is unactionable until it says which file port 4 is.

        MUTATION: print str(e) unchanged. The message survives, the exit code
        survives, and the user is told about a port number that appears nowhere
        on their command line.
        """
        rc, _out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--mode", "coupling", "--mport", "ball = PKG.2",
             "--gnd", "PKG.2", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("listed both as a probe", f)
        self.assertIn("PKG.2 (package.s3p port 2, board_ball)", f)
        self.assertIn("EM = coil_with_return.s2p: global ports 1-2", f)

    def test_the_port_map_names_every_file_and_its_global_range(self):
        rc, out, err = run(base_a())
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("EM = coil_with_return.s2p: global ports 1-2 (2 ports)",
                      f)
        self.assertIn("PKG = package.s3p: global ports 3-5 (3 ports)", f)


# ============================================================================
# Links
# ============================================================================

class TestLinks(unittest.TestCase):

    def link_run(self, *links: str, extra: list[str] = ()) -> tuple:
        argv = ["--cli", DIE_N, "--compose-alias", "D",
                "--compose", f"P={PKG_N}",
                "--mode", "gnd", "--porta", "D.1", "--freq", str(MARK_GHZ)]
        for s in links:
            argv += ["--compose-link", s]
        return run(argv + list(extra))

    def test_an_elementwise_link_echoes_the_FIRST_and_LAST_pair(self):
        """
        The echo is the only symptom an off-by-one in one file's numbering has:
        every pair shifts, and the answer stays plausible.

        MUTATION: echo only the count. 'D.1-3 short_to P.4-6' and
        'D.1-3 short_to P.5-7' then print the identical line.

        Ranges rather than lists because a file tag scopes ONE token: 'P.4,5,6'
        is P.4 and then two BARE tokens, which take the default scope (the
        first positional file, D) -- see
        test_a_BARE_token_on_a_link_side_is_the_DEFAULT_file.
        """
        rc, out, err = self.link_run("D.1-3 short_to P.4-6")
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("3 wires, elementwise, one per pair", f)
        self.assertIn("first D.1 (die_named.s5p port 1, vdd) -- "
                      "P.4 (pkg_named.s7p port 4, sig)", f)
        self.assertIn("last D.3 (die_named.s5p port 3, sig) -- "
                      "P.6 (pkg_named.s7p port 6, sig)", f)

    def test_a_length_mismatch_is_a_HARD_ERROR_showing_both_ends(self):
        """
        MUTATION: zip() the two sides. Python truncates to the shorter one, so
        'D.1-3 short_to P.4-5' silently makes two wires and drops the third.
        """
        rc, _out, err = self.link_run("D.1-3 short_to P.4-5")
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("lists 3 ports", f)
        self.assertIn("lists 2", f)
        self.assertIn("first pair would be D.1 - P.4", f)
        self.assertIn("last pair would be D.3 - P.5", f)

    def test_a_BARE_token_on_a_link_side_is_the_DEFAULT_file(self):
        """
        The CLI's default scope is the FIRST POSITIONAL file (`--compose-alias`
        D here), the same "home file" the GUI's bare port numbers mean, and a
        file tag scopes the ONE token it is written on.  So the second token of
        'P.4,5' is D.5, not P.5.

        That is a change: the tag used to be sticky over the whole field.  It
        is not silent -- the pairing echo prints the file of every port it
        made a wire to, which is what this pins.  Writing 'P.4-5' (one token)
        or 'P.4,P.5' says P both times.

        MUTATION: make the tag sticky again and the echo says P.5.
        """
        rc, out, err = self.link_run("D.1-2 short_to P.4,5")
        self.assertEqual(rc, 0, err)
        self.assertIn("-- D.5 (die_named.s5p port 5", flat(out))
        rc2, out2, err2 = self.link_run("D.1-2 short_to P.4,P.5")
        self.assertEqual(rc2, 0, err2)
        self.assertIn("-- P.5 (pkg_named.s7p port 5", flat(out2))

    def test_one_port_against_many_fans_out_and_says_so(self):
        # 54 VSS balls onto one die pad is the ordinary flip-chip connection,
        # so this is not an error -- but it must not read as elementwise.
        rc, out, err = self.link_run("D.2 short_to P.2,3")
        self.assertEqual(rc, 0, err)
        self.assertIn("2 wires, fan-out from D.2", flat(out))

    def test_a_short_carrying_an_element_is_refused_with_the_reason(self):
        """
        One surface syntax, TWO primitives: an ideal short cannot be a stamped
        element -- y_series_rlc(R=0, L=0, C=inf) is inf+nanj (measured).

        MUTATION: ignore the trailing fields. 'short_to ... L=1n' then computes
        an ideal short and the inductance the user typed is silently gone.
        """
        rc, _out, err = self.link_run("D.1 short_to P.1 L=1n")
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("cannot carry L=1n", f)
        self.assertIn("inf+nanj", f)
        self.assertIn("lumped_between", f)

    def test_a_lumped_link_with_no_element_is_refused(self):
        rc, _out, err = self.link_run("D.1 lumped_between P.1")
        self.assertEqual(rc, 2)
        self.assertIn("needs at least one of R=, L=, C=", flat(err))

    def test_connect_is_a_short_or_an_element_depending_on_the_fields(self):
        """
        The requirement's own DSL sketch spells the cross-file link 'connect',
        and section 4b asks for ONE surface word. It must reach the right
        primitive both ways, and the two must be different networks.

        MUTATION: make 'connect' always a short. The L=1n run then returns the
        ideal-short answer and nothing says the element was dropped.
        """
        rc_c, out_c, err = self.link_run("D.1 connect P.1")
        self.assertEqual(rc_c, 0, err)
        rc_s, out_s, err = self.link_run("D.1 short_to P.1")
        self.assertEqual(rc_s, 0, err)
        rc_l, out_l, err = self.link_run("D.1 connect P.1 L=1n")
        self.assertEqual(rc_l, 0, err)
        rc_e, out_e, err = self.link_run("D.1 lumped_between P.1 L=1n")
        self.assertEqual(rc_e, 0, err)

        def z(text):
            return [ln for ln in text.splitlines()
                    if ln.strip().startswith("Z      =")][0]

        self.assertEqual(z(out_c), z(out_s))
        self.assertEqual(z(out_l), z(out_e))
        self.assertNotEqual(z(out_c), z(out_l))
        self.assertIn("1 element, one pair", flat(out_l))

    def test_several_elements_are_announced_as_SEPARATE_not_one(self):
        """
        The measured 3x trap: N elements between the same two nodes are N in
        parallel (three 10 fH read 3.333 fH, ratio exactly 3.000), and the
        spelling that produces them looks identical to the one that produces
        one element.

        MUTATION: drop the sentence. Nothing else on screen distinguishes the
        two spellings.
        """
        rc, out, err = self.link_run("D.1,2 lumped_between P.1,2 L=1n")
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("2 elements, elementwise, one per pair", f)
        self.assertIn("2 SEPARATE elements, not one", f)
        self.assertIn("2x smaller", f)

    def test_a_value_split_across_two_tokens_is_refused(self):
        """
        parse_kv_rlc_params DROPS a token with no '=', so 'L=5 n' would build a
        5 henry element where 5 nH was meant. Core's _rlc_tokens refuses the
        same trap in a table cell; there is no way to quote a value here
        either.

        MUTATION: pass the tokens straight to parse_kv_rlc_params. The run then
        succeeds with an inductance a billion times too large.
        """
        rc, _out, err = self.link_run("D.1 lumped_between P.1 L=5 n")
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("'n'", f)
        self.assertIn("carry no '='", f)
        self.assertIn("5 henry, not 5 nH", f)

    def test_an_unknown_keyword_lists_the_ones_that_work(self):
        rc, _out, err = self.link_run("D.1 bond_to P.1")
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("'bond_to' is not a connection keyword", f)
        self.assertIn("short_to", f)
        self.assertIn("lumped_between", f)
        self.assertIn("connect", f)

    def test_no_link_at_all_says_the_files_are_not_connected(self):
        """
        MUTATION: print nothing. Two stacked files with no link between them
        produce a perfectly ordinary report about a network in which the die
        and the package touch only through the welded reference.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose", f"PKG={PKG}", "--mode", "gnd",
             "--porta", "F1.1", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        self.assertIn("stacked but NOT connected to each other", flat(out))

    def test_a_lumped_link_is_the_engines_number(self):
        """The CLI computes nothing: same spec, same arrays, through both."""
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-link", "EM.1 lumped_between PKG.1 R=0.5 L=0.3n",
             "--compose-link", "EM.2 short_to PKG.3",
             "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        net = engine_net(("EM", EM_A), ("PKG", PKG))
        links = pc.link_lumped(net, "EM.1", "PKG.1",
                               pc.y_series_rlc(R=0.5, L=0.3e-9),
                               {"R": 0.5, "L": 0.3e-9})
        term = TerminationSet(
            per_port={net.gport("PKG", 2) - 1: Signal("A")},
            couplings=list(links) + [ShortPair(net.gport("EM", 2) - 1,
                                               net.gport("PKG", 3) - 1)])
        Z, _n, _w = compute_z_matrix(net.Y, net.freqs, term)
        self.assertIn(f"L      = {L_at(net.freqs, Z[:, 0, 0]) * 1e9:.4g} nH",
                      out)


# ============================================================================
# The CSV mapping import
# ============================================================================

class TestComposeMap(unittest.TestCase):

    def write_map(self, text: str, name: str = "map.csv",
                  encoding: str = "utf-8") -> str:
        p = _TMP / name
        p.write_text(text, encoding=encoding)
        return str(p)

    def map_run(self, path: str, extra: list[str] = ()) -> tuple:
        return run(["--cli", EM_A, "--compose-alias", "EM",
                    "--compose", f"PKG={PKG}", "--compose-map", path,
                    "--mode", "gnd", "--porta", "PKG.2",
                    "--freq", str(MARK_GHZ)] + list(extra))

    def test_a_csv_and_the_equivalent_flags_give_the_same_answer(self):
        path = self.write_map(
            "a,b,element,note\n"
            "EM.1,PKG.1,short,coil top\n"
            "EM.2,PKG.3,,die return\n")
        rc, out_map, err = self.map_run(path)
        self.assertEqual(rc, 0, err)
        rc, out_flag, err = run(base_a())
        self.assertEqual(rc, 0, err)

        def z(text):
            return [ln for ln in text.splitlines()
                    if ln.strip().startswith("Z      =")][0]

        self.assertEqual(z(out_map), z(out_flag))
        self.assertIn("map.csv line 2", flat(out_map))

    def test_an_element_column_makes_it_a_lumped_link(self):
        # MUTATION: ignore the element column. Both runs then agree, and the
        # 0.3 nH the CSV asked for is gone with nothing said.
        wire = self.write_map("a,b,element\nEM.1,PKG.1,short\n"
                              "EM.2,PKG.3,short\n", "wire.csv")
        elem = self.write_map("a,b,element\nEM.1,PKG.1,R=0.5 L=0.3n\n"
                              "EM.2,PKG.3,short\n", "elem.csv")
        _rc, out_w, _e = self.map_run(wire)
        rc, out_e, err = self.map_run(elem)
        self.assertEqual(rc, 0, err)

        def z(text):
            return [ln for ln in text.splitlines()
                    if ln.strip().startswith("Z      =")][0]

        self.assertNotEqual(z(out_w), z(out_e))
        self.assertIn("1 element, one pair", flat(out_e))

    def test_from_and_to_are_accepted_as_column_names(self):
        path = self.write_map("from,to\nEM.1,PKG.1\nEM.2,PKG.3\n", "ft.csv")
        rc, _out, err = self.map_run(path)
        self.assertEqual(rc, 0, err)

    def test_a_header_with_no_endpoint_column_lists_what_would_work(self):
        """
        MUTATION: fall back to the first two columns by position. A file whose
        columns are (note, a, b) then wires notes to ports.
        """
        path = self.write_map("die,package\nEM.1,PKG.1\n", "bad.csv")
        rc, _out, err = self.map_run(path)
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("has no 'a' column and no 'b' column", f)
        self.assertIn("this file has: die, package", f)
        # and a file missing only ONE of them says which one
        half = self.write_map("a,package\nEM.1,PKG.1\n", "bad2.csv")
        rc, _out, err = self.map_run(half)
        self.assertEqual(rc, 2)
        self.assertIn("has no 'b' column", flat(err))
        self.assertNotIn("no 'a' column", flat(err))

    def test_a_half_empty_row_names_the_LINE_NUMBER(self):
        """
        The line number counts lines in the FILE, not rows in the reader --
        comments and blanks are skipped before parsing and a reader's own
        row index would point at the wrong line.

        MUTATION: report the DictReader's index. The message then names line 2
        for a row that is on line 5.
        """
        path = self.write_map(
            "# a proposal, edited\n"
            "\n"
            "a,b,element,note\n"
            "EM.1,PKG.1,short,ok\n"
            "EM.2,,short,not decided yet\n", "half.csv")
        rc, _out, err = self.map_run(path)
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("line 5", f)
        self.assertIn("not decided yet", f)
        self.assertIn("comment the row out", f)

    def test_comments_and_blank_lines_are_skipped(self):
        path = self.write_map(
            "# header comment\n"
            "a,b\n"
            "\n"
            "EM.1,PKG.1\n"
            "# EM.9,PKG.9   <- not accepted yet\n"
            "EM.2,PKG.3\n", "cmt.csv")
        rc, out, err = self.map_run(path)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("EM.9", out)

    def test_a_spreadsheet_BOM_does_not_hide_the_first_column(self):
        """
        Excel writes a UTF-8 BOM, and a BOM glued to the first header name
        makes the 'a' column unfindable -- the same failure the Touchstone
        parser's encoding sniffer exists for.

        MUTATION: open with plain utf-8. The header reads '\\ufeffa' and the
        file is refused for having no 'a' column.
        """
        path = self.write_map("a,b\nEM.1,PKG.1\nEM.2,PKG.3\n", "bom.csv",
                              encoding="utf-8-sig")
        rc, _out, err = self.map_run(path)
        self.assertEqual(rc, 0, err)

    def test_a_file_that_is_all_comments_says_what_to_do(self):
        path = self.write_map(
            "# nothing accepted yet\n#EM.1,,short,unmatched\n", "empty.csv")
        rc, _out, err = self.map_run(path)
        self.assertEqual(rc, 2)
        self.assertIn("uncomment the ones you accept", flat(err).lower())

    def test_a_missing_file_is_refused_by_name(self):
        rc, _out, err = self.map_run(str(_TMP / "nope.csv"))
        self.assertEqual(rc, 2)
        self.assertIn("nope.csv", err)


# ============================================================================
# The proposal
# ============================================================================

class TestProposal(unittest.TestCase):

    def propose(self, extra: list[str] = ()) -> tuple:
        return run(["--cli", DIE_N, "--compose-alias", "D",
                    "--compose", f"P={PKG_N}",
                    "--compose-propose", "D,P"] + list(extra))

    def test_names_match_after_trimming_and_case_folding(self):
        """
        MUTATION: compare the raw strings. 'vdd' and 'VDD ' stop matching and
        the one pair a user would call obvious disappears.
        """
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("vdd D.1 P.1 one pair", f)

    def test_many_to_one_is_a_fan_out_and_not_an_error(self):
        # 54 ground balls onto one die pad is the ordinary connection.
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("vss D.2 P.2 fan-out", f)
        self.assertIn("vss D.2 P.3 fan-out", f)

    def test_N_to_M_is_reported_AMBIGUOUS_and_is_not_paired(self):
        """
        2 die ports and 3 package ports all named 'sig' have no defensible
        pairing order.

        MUTATION: pair them in file order and drop the ambiguity. Two of the
        three get wired, the third silently does not, and which is which
        depends on the order the files happen to list their ports.
        """
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("ambiguous: 2 port(s) of D and 3 of P are all named "
                      "'sig'", f)
        self.assertIn("D.3-4 / P.4-6", f)
        self.assertNotIn("sig D.3 P.4", f)

    def test_unmatched_ports_are_listed_on_BOTH_sides(self):
        """
        MUTATION: list only the near side. The package's 4 unmatched ports --
        the ones a user has to go and decide about -- vanish.
        """
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("unmatched in D: 3 port(s) -- D.3-5", f)
        self.assertIn("unmatched in P: 4 port(s) -- P.4-7", f)

    def test_a_proposal_STOPS_and_measures_nothing(self):
        """
        A proposal you review and accept is a reviewed input; one applied on
        your behalf is a guess.

        MUTATION: fall through to the measurement. The run then connects a
        correspondence nobody approved and prints an impedance for it.
        """
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Z      =", out)
        self.assertNotIn("REFERENCE-NODE CHECK", out)
        self.assertIn("stops here on purpose", flat(out))

    def test_a_proposal_needs_neither_a_probe_nor_a_marker_in_span(self):
        """
        It is a question about port NAMES.

        MUTATION: keep passing marker_hz for a proposal. The default --freq of
        0.1 GHz is outside these files' 1-10 GHz span, so every proposal on a
        realistic pair of files is refused for a reason that has nothing to do
        with what was asked.
        """
        rc, out, err = self.propose()
        self.assertEqual(rc, 0, err)          # no --mport, no --porta, no --freq
        self.assertIn("PROPOSED port correspondence", out)

    def test_a_flag_the_proposal_skipped_is_named_rather_than_ignored(self):
        """
        A proposal stops before anything is connected, so --compose-link and
        --compose-export do not run.

        MUTATION: stop silently. The difference between "it stopped" and "it
        ignored me" is exactly this line, and the second reading is the one a
        user reaches for when the export they asked for is not on disk.
        """
        rc, out, err = self.propose(
            ["--compose-link", "D.1 short_to P.1",
             "--compose-export", str(_TMP / "never.s12p")])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("--compose-link, --compose-export did NOT run", f)
        self.assertFalse((_TMP / "never.s12p").exists())

    def test_nothing_in_common_says_so_instead_of_printing_an_empty_table(self):
        rc, out, err = run(["--cli", EM_A, "--compose-alias", "EM",
                            "--compose", f"PKG={PKG}",
                            "--compose-propose", "EM,PKG"])
        self.assertEqual(rc, 0, err)
        self.assertIn("NOTHING MATCHED", flat(out))

    def test_an_unknown_tag_is_refused_and_a_bad_pair_names_the_count(self):
        rc, _out, err = self.propose_bad("D")
        self.assertIn("got 1 field(s)", flat(err))
        rc, _out, err = self.propose_bad("D,NOPE")
        self.assertIn("'NOPE' is not one of the files", flat(err))

    def propose_bad(self, spec: str):
        rc, out, err = run(["--cli", DIE_N, "--compose-alias", "D",
                            "--compose", f"P={PKG_N}",
                            "--compose-propose", spec])
        self.assertEqual(rc, 2, out)
        return rc, out, err

    # ---- the CSV -----------------------------------------------------------

    def test_the_proposal_csv_is_accepted_by_compose_map(self):
        """
        The round trip is the whole point: propose, review, commit.

        MUTATION: write the port labels without their tags. --compose-map then
        reads them as bare port numbers in the default scope, i.e. every
        package port becomes a die port.
        """
        out_csv = _TMP / "prop.csv"
        rc, _out, err = self.propose(["--compose-propose-csv", str(out_csv)])
        self.assertEqual(rc, 0, err)
        rc, out, err = run(["--cli", DIE_N, "--compose-alias", "D",
                            "--compose", f"P={PKG_N}",
                            "--compose-map", str(out_csv),
                            "--mode", "gnd", "--porta", "D.1",
                            "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("D.1 (die_named.s5p port 1, vdd) -- "
                      "P.1 (pkg_named.s7p port 1, VDD)", f)

    def test_the_csv_carries_every_unmatched_port_as_a_COMMENTED_row(self):
        """
        That is what makes the file a work list rather than only an answer, and
        it is what makes the terminal's "(see the CSV)" pointer true.

        MUTATION: write only the matched rows. The 7 ports that need a decision
        are then in neither output.
        """
        out_csv = _TMP / "prop2.csv"
        rc, _out, err = self.propose(["--compose-propose-csv", str(out_csv)])
        self.assertEqual(rc, 0, err)
        text = out_csv.read_text(encoding="utf-8")
        for tag in ("#D.5,", "#,P.7,"):
            self.assertIn(tag, text)
        self.assertIn("AMBIGUOUS name 'sig'", text)
        # and the commented rows really are inert
        body = [ln for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        rows = list(csv.DictReader(body))
        self.assertTrue(all(r["a"] and r["b"] for r in rows))
        self.assertEqual(len(rows), 3)     # vdd 1 + vss 2; 'sig' is ambiguous


# ============================================================================
# Attribution and cold start ON a composed network (requirement R2-8)
# ============================================================================

def _attr_spec(net):
    """
    The spec the two commands below assemble, built WITHOUT the CLI.

    EM.2 (the die return) is shorted to PKG.1, which is the CROSS-FILE link and
    therefore the structure; PKG.1 -> PKG.2 through 5 ohm is an element INSIDE
    the far file, which is the thing that reads exactly zero without the gauge.
    """
    ports = {net.gport("EM", 1) - 1: Signal("vic", +1),
             net.gport("PKG", 2) - 1: Signal("agg", +1)}
    links = list(pc.link_short(net, "EM.2", "PKG.1"))
    links += list(pc.link_lumped(net, "PKG.1", "PKG.2",
                                 y_series_rlc(**parse_kv_rlc_params(["R=5"]))))
    return TerminationSet(per_port=ports, couplings=links)


def _far_file_term(net, baseline):
    """M contributed by the package-internal element, at MARK, in henries."""
    ctx = at.build_context(net.Y, net.freqs, _attr_spec(net), MARK_HZ,
                           baseline=baseline)
    dec = at.decompose(ctx, 0, 1, "M")
    far = net.gport("PKG", 1) - 1, net.gport("PKG", 2) - 1
    for t in dec.terms:
        if t.element is not None and tuple(t.element.ports) == far:
            return t.contribution, dec.residual_rel
    raise AssertionError(
        "the package-internal element is not in the decomposition at all: "
        + ", ".join(t.label for t in dec.terms))


class TestComposedAttributionBaseline(unittest.TestCase):
    """
    R2-8, as a capability rather than a refusal.

    --attribute and --cold-start decompose against an ALL-OPEN baseline, and
    all-open on a composition leaves the files as disconnected islands: Ybase
    is then exactly block diagonal, every element inside the far file gets a
    contribution of EXACTLY 0, and the reconciliation residual still reads
    healthy -- a confident, perfectly reconciled wrong answer that no test of
    the attribution arithmetic can see, because the arithmetic is right.

    `pkg_rlc_attrib.BaselineLinks` is the gauge that fixes it, and the CLI is
    what has to APPLY it.  These tests measure the broken value directly (with
    `baseline=None`) so the number the mutation produces is in the file, not
    just in a commit message.
    """

    def base(self, *extra: str) -> list[str]:
        return ["--cli", EM_A, "--compose-alias", "EM", "--compose",
                f"PKG={PKG}",
                "--compose-link", "EM.2 short_to PKG.1",
                "--compose-link", "PKG.1 lumped_between PKG.2 R=5",
                "--mode", "coupling",
                "--mport", "vic = EM.1", "--mport", "agg = PKG.2",
                "--freq", str(MARK_GHZ)] + list(extra)

    def test_without_the_gauge_the_far_file_element_is_EXACTLY_zero(self):
        """
        The precondition every test below rests on, asserted rather than
        assumed: with no baseline policy the package-internal element's term is
        not small, it is exactly 0.0, and the residual says the split is fine.

        Measured on this box: 0j against 1.70e-13 relative residual.  A test
        that only checked "the CLI prints a number" would pass in exactly that
        state, because 0.000 H is a number.
        """
        net = engine_net(("EM", EM_A), ("PKG", PKG))
        value, residual = _far_file_term(net, None)
        self.assertEqual(value, 0j, "not a threshold: EXACTLY zero")
        self.assertLess(residual, 1e-9,
                        "and the residual reports perfect health beside it")

    def test_the_cross_file_links_go_INTO_the_attribution_baseline(self):
        """
        MUTATION: drop `baseline=` from `_run_attribution`'s build(). The report
        still runs, still reconciles to 1.7e-13, and prints 0.00 H for the
        package element -- the failure above, on screen, with no warning.
        """
        net = engine_net(("EM", EM_A), ("PKG", PKG))
        gauged, _ = _far_file_term(
            net, at.BaselineLinks(blocks=at.PortBlocks.from_sizes(
                [b.nports for b in net.blocks],
                [b.alias for b in net.blocks])))
        self.assertNotEqual(gauged, 0j)

        rc, out, err = run(self.base("--attribute", "vic,agg"))
        self.assertEqual(rc, 0, err)
        # The gauge is NAMED on the report, not applied quietly: two
        # attribution reports are comparable only when their baselines match.
        self.assertIn("COMPOSED-NETWORK BASELINE", flat(out))
        self.assertIn("the files CONNECTED, everything else open", flat(out))
        # And the far-file element carries the engine's own number.  5.86 pH at
        # 5.2 GHz on this fixture; the assertion is against `gauged`, not
        # against the literal, so the fixture may move.
        self.assertIn(f"{gauged.real * 1e12:.2f} pH".replace("0.00", "0"),
                      flat(out).replace("  ", " "))

    def test_a_link_is_grouped_under_the_flag_that_declared_it(self):
        """
        MUTATION: drop `link_sources` from `_attr_sources`. Every cross-file
        link then falls back to its KIND, so two links written on two
        --compose-link lines land in one group called 'lumped_between' -- the
        provenance this function exists to keep.
        """
        rc, out, err = run(self.base("--attribute", "vic,agg"))
        self.assertEqual(rc, 0, err)
        self.assertIn('--compose-link "PKG.1 lumped_between PKG.2 R=5"',
                      flat(out))

    def test_an_mport_with_a_file_tag_reaches_the_attribution(self):
        """
        The --mport text carries file tags ('vic = EM.1') and `_attr_sources`
        PARSES what it is handed with parse_mport_spec, which reads bare
        integers.

        MUTATION: hand `_run_attribution` the RAW specs instead of the ones
        rewritten into global numbering. Measured: ValueError "invalid literal
        for int() with base 10: 'EM.1'" -- an unhandled traceback out of a
        report the coupling solve has already been paid for.

        The LABEL must still be the text the user typed. That half cannot be
        seen from the report on this fixture -- a probe declares no element, so
        its group is never printed unless some element lands on a probe port --
        so it is asserted directly off `_attr_sources`, which is where the two
        lists part company. A test that only ran the CLI would pass with the
        labels swapped.
        """
        rc, out, err = run(self.base("--attribute", "vic,agg"))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Traceback", err)

        args = ex._make_arg_parser().parse_args(["--cli", "x"])
        src, _notes = ex._attr_sources(
            "row", args, ["vic = 1,2"], [], [], [], 5,
            spec_labels=["vic = EM.1,2"])
        self.assertEqual(src[1], '--mport "vic = EM.1,2"')
        self.assertEqual(src[2], '--mport "vic = EM.1,2"')
        with self.assertRaises(ValueError):
            ex._attr_sources("row", args, ["vic = 1"], [], [], [], 5,
                             spec_labels=["a", "b"])

    def test_cold_start_keeps_the_links_as_STRUCTURE(self):
        """
        The cold start REWRITES the spec -- probes kept, every other
        declaration dropped -- so without the policy the cross-file links go
        with them and the far file is simply not attached.

        MUTATION: drop `baseline=` from `cold_start_context`. Every package
        port then reads delta exactly 0.0 with `defined = True`, i.e. a screen
        confidently reporting that the package cannot matter.

        The SAME mutation applied to `cold_start_report`'s own `baseline=` is
        GREEN, and that is stated here rather than claimed otherwise: the CLI
        always passes `context=csc`, and `_cs_context` returns that context
        untouched, so the argument is dead unless the `context=` is ever
        dropped. It is kept as the safety net for exactly that edit.
        """
        rc, out, err = run(self.base("--cold-start", "vic,agg"))
        self.assertEqual(rc, 0, err)
        flat_out = flat(out)
        self.assertIn("IN this baseline and were NOT dropped", flat_out)
        # PKG.1 is the port the cross-file link lands on. Under the mutation
        # its row reads 0.00 H; here it has to move the answer.
        row = [ln for ln in out.splitlines() if "PKG.sig_pad" in ln]
        self.assertTrue(row, f"no PKG.1 row in the screen:\n{out}")
        self.assertNotIn(" 0.00 H ", row[0])

    def test_a_composition_with_no_link_says_the_baseline_is_all_open_again(self):
        """
        The gauge selects DECLARED links, so with none declared it selects
        nothing and the baseline is back to all-open -- while the header still
        carries a paragraph saying the files are connected.

        MUTATION: return [] from `_compose_gauge_notes` when there are no
        cross-file links. The contradiction is then unremarked.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose", f"PKG={PKG}", "--mode", "coupling",
             "--mport", "vic = F1.1", "--mport", "agg = PKG.2",
             "--freq", str(MARK_GHZ), "--attribute", "vic,agg"])
        self.assertEqual(rc, 0, err)
        self.assertIn("NO cross-file link exists", flat(out))
        self.assertIn("EXACTLY ZERO", flat(out))

    def test_the_block_map_is_sized_from_the_SURVIVING_ports(self):
        """
        After a --compose-keep pre-reduction the block in `net.Y` is the
        REDUCED one.

        MUTATION: size the block map from `nports_original`. The totals then
        disagree with Y and `_validate_baseline` refuses the whole report --
        "baseline blocks describe 5 port(s) but this Y has 4" -- on a flag
        combination that has nothing wrong with it. (It refuses rather than
        mis-maps only because PortBlocks checks the total; a map that happened
        to add up would put every later file's ports at the wrong index.)
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-keep", "PKG.1-2",
             "--compose-link", "EM.2 short_to PKG.1",
             "--compose-link", "PKG.1 lumped_between PKG.2 R=5",
             "--mode", "coupling",
             "--mport", "vic = EM.1", "--mport", "agg = PKG.2",
             "--freq", str(MARK_GHZ), "--attribute", "vic,agg"])
        self.assertEqual(rc, 0, err)
        self.assertIn("EM = global 1-2; PKG = global 3-4", flat(out))

    def test_the_element_numbering_note_names_every_block(self):
        """
        `pkg_rlc_attrib.Element.describe()` renders GLOBAL indices ('ground
        port 10'), because an element is a stamp on the combined Y and knows
        nothing about files. That is unactionable on a 316-port network unless
        the map is on the same screen.

        MUTATION: drop the note. The element column then names numbers that
        appear nowhere else in the report.
        """
        rc, out, err = run(self.base("--attribute", "vic,agg"))
        self.assertEqual(rc, 0, err)
        self.assertIn("EM = global 1-2; PKG = global 3-5", flat(out))


# ============================================================================
# The reference-node check -- mandatory output
# ============================================================================

class TestReferenceCheckIsMandatory(unittest.TestCase):
    """
    The finding the whole feature exists to survive: block_diag welds the two
    files' reference nodes, and when the near file's return current uses its
    OWN reference the far file's ground network is unreachable.  Measured on
    this fixture: grounded / open / through 1 nH all give L_eff = 2.1454 nH,
    bit-identical.  Nothing raises and the number is plausible.
    """

    def test_a_welded_ground_is_reported_by_name_at_exit_zero(self):
        """
        MUTATION: drop _compose_print_reference from the mode-1 path. The run
        prints 2.145 nH -- 100 pH of package ground lead missing -- and reads
        exactly like a correct one.
        """
        rc, out, err = run(base_b())
        self.assertEqual(rc, 0, err)          # a finding, not a CLI failure
        f = flat(out)
        self.assertIn("WELD: PKG (package.s3p)", f)
        self.assertIn("PKG.3", f)
        self.assertIn("EXACTLY zero", f)
        self.assertIn("L      = 2.145 nH", out)

    def test_the_same_package_reads_ok_when_the_return_is_a_port(self):
        """
        Without this the check could just always say 'welded'.

        MUTATION: make the CLI print WELD unconditionally -- the test above
        still passes and this one fails.
        """
        rc, out, err = run(base_a() + ["--gnd", "PKG.3"])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("ok: PKG (package.s3p): its ground set PKG.3 is in the "
                      "circuit", f)
        self.assertNotIn("WELD:", f)

    def test_the_check_runs_on_the_coupling_path_too(self):
        """
        MUTATION: attach it only to --mode coupling (or only to the legacy
        modes). Half the users never see it.
        """
        rc, out, err = run(
            ["--cli", EM_B, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-link", "EM.1 short_to PKG.1", "--gnd", "PKG.3",
             "--mode", "coupling", "--mport", "ball = PKG.2",
             "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        self.assertIn("WELD: PKG (package.s3p)", flat(out))

    def test_it_arrives_BEFORE_the_numbers(self):
        """
        A weld changes how the number must be read, so a footnote under it is
        the wrong place.

        MUTATION: print the check after the report. The reader meets 2.145 nH
        first and has already believed it.
        """
        rc, out, err = run(base_b())
        self.assertEqual(rc, 0, err)
        self.assertLess(out.index("REFERENCE-NODE CHECK"),
                        out.index("L      ="))

    def test_a_file_with_no_declared_ground_says_what_that_means(self):
        rc, out, err = run(base_a())
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("EM (coil_with_return.s2p) declares no ground port", f)
        self.assertIn("nothing to perturb", f)

    def test_a_ground_set_folded_into_the_pre_reduction_is_explained(self):
        """
        --compose-gnd deletes those rows before the stack, so no port survives
        for the check to perturb and it reports "declares no ground port" --
        true, and reads as an accusation unless the CLI says why.

        MUTATION: drop the extra note. The user is told their package has no
        ground on the same screen where they typed --compose-gnd.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-gnd", "PKG.3",
             "--compose-link", "EM.1 short_to PKG.1",
             "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("--compose-gnd folded PKG's ground ports into its "
                      "pre-reduction", f)
        self.assertIn("PKG (package.s3p) pre-reduced 3 -> 2 ports", f)


# ============================================================================
# The frequency plan
# ============================================================================

class TestFrequencyPlan(unittest.TestCase):

    def test_the_coarse_file_is_named_interpolated_with_its_invented_loss(self):
        """
        R2-3's numbers reach the screen: which grid was adopted, how many
        points each file lost, the effective resolution, and the phase step
        with the insertion loss it invents.

        MUTATION: report the resolution from the ADOPTED grid instead of the
        coarsest file's. It then claims 100 MHz on a composition whose package
        was sampled every 500 MHz -- resampling onto a finer grid recovers
        nothing.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM",
             "--compose", f"PKG={COARSE}",
             "--compose-link", "EM.1 short_to PKG.1",
             "--compose-link", "EM.2 short_to PKG.3",
             "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("common span : 2 GHz - 8 GHz", f)
        self.assertIn("effective step : 500 MHz", f)
        self.assertIn("EM grid", f)
        self.assertIn("PKG interpolated", f)
        # every point of EM outside 2-8 GHz is dropped, and the report says how
        # many rather than leaving the span to be inferred
        self.assertIn("EM (coil_with_return.s2p): 30 of 91 points fall "
                      "outside the common span", f)
        # and the phase step it costs, with the loss it invents (0.2 deg here,
        # i.e. 0.000 dB -- well under the 20 deg noise floor)
        self.assertIn("PKG interpolated 0 500 MHz linear 0.2 0.000", f)

    def test_an_identical_grid_is_not_interpolated_onto_itself(self):
        rc, out, err = run(base_a())
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("every file is already on the same 91-point grid", f)
        self.assertIn("no interpolation was done", f)


# ============================================================================
# Export
# ============================================================================

class TestExport(unittest.TestCase):

    def test_the_written_file_reads_back_as_the_stacked_network(self):
        """
        The export is the only INDEPENDENT check a feature with no golden
        reference has: the file goes back through the parser and the Y it
        yields must be the Y that was composed.

        MUTATION: export S from the wrong z0 (or lose digits). The round trip
        stops agreeing and this fails; nothing else in the suite would.
        """
        path = _TMP / "stacked.s5p"
        rc, out, err = run(base_a() + ["--compose-export", str(path)])
        self.assertEqual(rc, 0, err)
        back = parse_touchstone(str(path))
        net = engine_net(("EM", EM_A), ("PKG", PKG))
        self.assertEqual(back.nports, net.nports)
        np.testing.assert_allclose(back.freqs, net.freqs, rtol=1e-12)
        Yb = s_to_y(back.s, back.z0)
        err_rel = np.max(np.abs(Yb - net.Y)) / np.max(np.abs(net.Y))
        self.assertLess(err_rel, 1e-12, f"round trip is {err_rel:.3e}")

    def test_the_report_says_the_links_are_NOT_in_the_file_and_names_them(self):
        """
        The file is the files STACKED; a short merges nodes and changes the
        port count, so the links stay in the termination set.

        MUTATION: call it "the assembled network" and drop the caveat. A reader
        instantiates a 5-port block with no connection between the two halves
        and believes it is the circuit that produced the number above it.
        """
        path = _TMP / "stacked2.s5p"
        rc, out, err = run(base_a() + ["--compose-export", str(path)])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("This file is the files STACKED", f)
        self.assertIn("2 connection(s) above are NOT stamped into it", f)
        head = path.read_text().splitlines()
        self.assertTrue(any("EM.1 short_to PKG.1" in ln for ln in head))
        self.assertTrue(any("Port[1] = EM.1 coil_top" in ln for ln in head))

    def test_selected_ports_are_brought_out_and_the_rest_eliminated(self):
        path = _TMP / "two.s2p"
        rc, out, err = run(base_a() + ["--compose-export", str(path),
                                       "--compose-export-ports",
                                       "EM.1;PKG.2"])
        self.assertEqual(rc, 0, err)
        back = parse_touchstone(str(path))
        self.assertEqual(back.nports, 2)
        self.assertIn("ports brought out: EM.1, PKG.2", flat(out))

    def test_a_composed_CSV_carries_the_port_map(self):
        """
        The provenance line of a composed export carries the port fields the
        user typed, and those are scoped ('PKG.3') -- meaningless without the
        map from a tag to a file. A CSV outlives the terminal it was printed
        in, the same reason the attribution CSV carries its sign convention.

        MUTATION: drop _compose_csv_header. Both files still open, both still
        say '# File: EM=... + PKG=...', and neither says which ports are which.
        """
        for extra, name in ((["--mode", "gnd", "--porta", "PKG.2"], "g.csv"),
                            (["--mode", "coupling", "--mport", "ball = PKG.2"],
                             "c.csv")):
            with self.subTest(name):
                path = _TMP / name
                argv = ["--cli", EM_A, "--compose-alias", "EM",
                        "--compose", f"PKG={PKG}",
                        "--compose-link", "EM.1 short_to PKG.1",
                        "--freq", str(MARK_GHZ), "--csv", str(path)] + extra
                rc, _out, err = run(argv)
                self.assertEqual(rc, 0, err)
                head = "".join(ln for ln in path.read_text().splitlines(True)
                               if ln.startswith("#"))
                self.assertIn("EM = coil_with_return.s2p: global ports 1-2",
                              head)
                self.assertIn("PKG = package.s3p: global ports 3-5", head)

    def test_an_extension_that_disagrees_with_the_port_count_is_flagged(self):
        """
        This tool content-sniffs and the extension is a TIEBREAK, so the file
        reads back either way -- other tools trust the extension.

        MUTATION: drop the note. A 5-port network called '.s2p' is refused by
        the next tool it reaches, with no clue where it came from.
        """
        path = _TMP / "wrong.s2p"
        rc, out, err = run(base_a() + ["--compose-export", str(path)])
        self.assertEqual(rc, 0, err)
        self.assertIn("the name says .s2p but the network has 5 ports",
                      flat(out))


# ============================================================================
# Pre-reduction
# ============================================================================

class TestPreReduction(unittest.TestCase):

    def test_a_kept_port_keeps_its_ORIGINAL_number(self):
        """
        Renumbering a reduced block to 1..k is exactly what makes an off-by-one
        in a mapping invisible.

        MUTATION: renumber. 'PKG.3' then names the second surviving port and
        the answer changes with nothing on screen to notice.
        """
        rc, out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-keep", "PKG.1,3",
             "--compose-link", "EM.1 short_to PKG.1",
             "--compose-link", "EM.2 short_to PKG.3",
             "--mode", "gnd", "--porta", "PKG.1", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 0, err)
        f = flat(out)
        self.assertIn("kept 1,3 of 3", f)
        self.assertIn("PKG.3 (package.s3p port 3, gnd_pad)", f)

    def test_a_port_the_reduction_removed_is_refused_by_name(self):
        """
        MUTATION: let it through as "does not exist". The user is told their
        3-port package has no port 2, which is false and sends them to the
        wrong file.
        """
        rc, _out, err = run(
            ["--cli", EM_A, "--compose-alias", "EM", "--compose", f"PKG={PKG}",
             "--compose-keep", "PKG.1,3",
             "--mode", "gnd", "--porta", "PKG.2", "--freq", str(MARK_GHZ)])
        self.assertEqual(rc, 2)
        f = flat(err)
        self.assertIn("was removed by the pre-reduction", f)
        self.assertIn("kept ports 1,3 of 3", f)

    def test_the_reduced_network_answers_what_the_full_one_answers(self):
        """
        The reduction is exact for the ports it keeps: eliminating a port that
        nothing connects to is a Schur complement, not an approximation.

        MUTATION: eliminate the ground bucket open instead of deleting its row
        (grounding is NOT opening). The two answers part company.
        """
        common = ["--cli", EM_A, "--compose-alias", "EM",
                  "--compose", f"PKG={PKG}",
                  "--compose-link", "EM.1 short_to PKG.1",
                  "--compose-link", "EM.2 short_to PKG.3",
                  "--mode", "gnd", "--porta", "PKG.2",
                  "--freq", str(MARK_GHZ)]
        rc, full, err = run(common)
        self.assertEqual(rc, 0, err)
        rc, cut, err = run(common + ["--compose-keep", "PKG.1,2,3"])
        self.assertEqual(rc, 0, err)

        def z(text):
            return [ln for ln in text.splitlines()
                    if ln.strip().startswith("Z      =")][0]

        self.assertEqual(z(full), z(cut))


if __name__ == "__main__":
    unittest.main()
