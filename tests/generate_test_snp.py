"""
generate_test_snp.py  --  Synthesize Touchstone fixtures with known impedance.

Each fixture has analytically known R/L/C so tests can verify extraction
correctness to within numerical precision.

Run as a script to (re)generate all fixtures into ./fixtures/.

The COUPLED_* module constants below are the single source of truth for the
mutual-coupling fixtures: the same numbers go into the file header comments and
into tests/test_coupling.py, so an expected value never lives in two places.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

# Make the package importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pkg_rlc_core import y_to_s, DEFAULT_Z0  # noqa: E402


# ============================================================================
# Touchstone writer
# ============================================================================

def write_touchstone(out_path: str | Path, freqs: np.ndarray, S: np.ndarray,
                     z0: float = DEFAULT_Z0,
                     port_names: list[str] | None = None,
                     comment: str = "",
                     digits: int = 9) -> None:
    """
    Write a Touchstone file (RI format, Hz units).

    Layout:
      n=1: each freq on one line: freq Re(S11) Im(S11)
      n=2: each freq on one line, Touchstone v1 column-major: S11 S21 S12 S22
      n>2: each freq spans n lines: row-major; freq sits on line 0

    `digits` is the number of digits after the decimal point in the %e fields.
    The default 9 (== 10 significant digits) is what every pre-existing fixture
    was written with -- do NOT change it, the golden regression fixtures are
    byte-compared.  The coupled-inductor fixtures pass digits=17 because they
    have to survive a pinv with rcond=1e-12: at 10 significant digits the
    round-trip noise floor of an exactly-singular Y sits *above* that cutoff and
    the pseudo-inverse would amplify quantisation noise instead of truncating it.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = S.shape[-1]
    d = digits
    lines: list[str] = []
    if comment:
        for c in comment.splitlines():
            lines.append("! " + c)
    if port_names:
        for i, name in enumerate(port_names):
            if name:
                lines.append(f"! Port[{i+1}] = {name}")
    lines.append(f"# HZ S RI R {z0:g}")

    for k in range(len(freqs)):
        Sk = S[k]
        if n == 1:
            lines.append(f"{freqs[k]:.{d}e}  {Sk[0,0].real:.{d}e} {Sk[0,0].imag:.{d}e}")
        elif n == 2:
            lines.append(
                f"{freqs[k]:.{d}e}  "
                f"{Sk[0,0].real:.{d}e} {Sk[0,0].imag:.{d}e}  "
                f"{Sk[1,0].real:.{d}e} {Sk[1,0].imag:.{d}e}  "
                f"{Sk[0,1].real:.{d}e} {Sk[0,1].imag:.{d}e}  "
                f"{Sk[1,1].real:.{d}e} {Sk[1,1].imag:.{d}e}"
            )
        else:
            for i in range(n):
                vals = []
                for j in range(n):
                    vals.append(f"{Sk[i,j].real:.{d}e}")
                    vals.append(f"{Sk[i,j].imag:.{d}e}")
                row_str = " ".join(vals)
                if i == 0:
                    lines.append(f"{freqs[k]:.{d}e}  {row_str}")
                else:
                    lines.append(f"               {row_str}")

    out_path.write_text("\n".join(lines) + "\n")


# ============================================================================
# Fixture builders -- each returns (freqs, Y, port_names, doc) so we can
# both write the file and remember the analytical answer for tests.
# ============================================================================

def fixture_shunt_rl_1port(R: float = 0.5, L: float = 1e-9,
                           f_start: float = 1e6, f_stop: float = 10e9,
                           n_pts: int = 401) -> dict:
    """
    1-port: shunt R+jwL to GND.
      Z = R + jwL,  Y = 1/Z
    Mode 1 (signal=1, no gnd) should recover this Z directly.
    """
    f = np.linspace(f_start, f_stop, n_pts)
    omega = 2 * np.pi * f
    Z = R + 1j * omega * L
    Y = (1.0 / Z).reshape(-1, 1, 1)
    return {
        "freqs": f, "Y": Y,
        "port_names": ["pin"],
        "expect": {"R": R, "L": L},
        "comment": f"Synthetic 1-port shunt R+L\nR = {R} Ohm\nL = {L} H",
    }


def fixture_shunt_c_1port(C: float = 1e-12,
                          f_start: float = 1e6, f_stop: float = 10e9,
                          n_pts: int = 401) -> dict:
    """1-port: shunt C to GND.  Y = jwC."""
    f = np.linspace(f_start, f_stop, n_pts)
    omega = 2 * np.pi * f
    Y = (1j * omega * C).reshape(-1, 1, 1)
    return {
        "freqs": f, "Y": Y,
        "port_names": ["cap_top"],
        "expect": {"C": C},
        "comment": f"Synthetic 1-port shunt C\nC = {C} F",
    }


def fixture_pi_2port(R: float = 1.0, L: float = 1e-9, C_shunt: float = 1e-15,
                     f_start: float = 1e6, f_stop: float = 10e9,
                     n_pts: int = 401) -> dict:
    """
    2-port pi network:
        port1 --[Y2 = 1/(R+jwL)]-- port2
          |                            |
        [Y1=jwC]                  [Y3=jwC]
          |                            |
         GND                          GND

    Y[0,0] = Y1 + Y2,  Y[0,1] = -Y2,  Y[1,0] = -Y2,  Y[1,1] = Y2 + Y3
    Mode 2 measurement (port1 <-> port2) gives:
        Z = (Y1+Y3) / (Y1*Y3 + Y2*(Y1+Y3)) = 2 / (Y1 + 2*Y2)   [Y1==Y3==jwC]
        For tiny C: Z ~= 1/Y2 = R + jwL
    """
    f = np.linspace(f_start, f_stop, n_pts)
    omega = 2 * np.pi * f
    Y1 = 1j * omega * C_shunt
    Y2 = 1.0 / (R + 1j * omega * L)
    Y3 = Y1
    Y = np.zeros((n_pts, 2, 2), dtype=complex)
    Y[:, 0, 0] = Y1 + Y2
    Y[:, 0, 1] = -Y2
    Y[:, 1, 0] = -Y2
    Y[:, 1, 1] = Y2 + Y3
    return {
        "freqs": f, "Y": Y,
        "port_names": ["in", "out"],
        "expect": {"R": R, "L": L, "C_shunt": C_shunt},
        "comment": f"Synthetic 2-port pi network\nR_series = {R} Ohm\n"
                   f"L_series = {L} H\nC_shunt_each_port = {C_shunt} F",
    }


def fixture_diff_pair_4port(L_self: float = 5e-9, M: float = 1e-9,
                            C_shunt: float = 1e-15,
                            f_start: float = 1e6, f_stop: float = 10e9,
                            n_pts: int = 401) -> dict:
    """
    4-port differential pair: two coupled inductors.
      port1 (in_p) --[L_self, M]-- port3 (out_p)
      port2 (in_n) --[L_self, M]-- port4 (out_n)
    With small shunt C on every port to ground (otherwise Y is singular).

    Mode 3 with A=1, B=2, ShortPair=(3,4) yields the differential loop
    inductance:  L_loop = 2 * (L_self - M).
    """
    f = np.linspace(f_start, f_stop, n_pts)
    omega = 2 * np.pi * f
    # alpha, beta from inductor pair Z-matrix inversion
    denom = 1j * omega * (L_self ** 2 - M ** 2)
    alpha = L_self / denom
    beta = M / denom
    Y = np.zeros((n_pts, 4, 4), dtype=complex)
    # Stamp coupled-inductor Y
    for k in range(n_pts):
        a = alpha[k]
        b = beta[k]
        Y[k] = np.array([
            [ a, -b, -a,  b],
            [-b,  a,  b, -a],
            [-a,  b,  a, -b],
            [ b, -a, -b,  a],
        ], dtype=complex)
        # Add shunt-C at each port
        for i in range(4):
            Y[k, i, i] += 1j * omega[k] * C_shunt
    return {
        "freqs": f, "Y": Y,
        "port_names": ["in_p", "in_n", "out_p", "out_n"],
        "expect": {"L_self": L_self, "M": M, "L_loop": 2 * (L_self - M),
                   "C_shunt": C_shunt},
        "comment": f"Synthetic 4-port differential pair\n"
                   f"L_self = {L_self} H\nM = {M} H\n"
                   f"C_shunt_each_port = {C_shunt} F\n"
                   f"Expected L_loop (Mode 3 with shorted far-end) = "
                   f"{2*(L_self-M)} H",
    }


def fixture_decap_4port(R_series: float = 1.0, L_series: float = 1e-9,
                        C_decap: float = 1e-12, C_shunt: float = 1e-15,
                        f_start: float = 1e6, f_stop: float = 10e9,
                        n_pts: int = 401) -> dict:
    """
    4-port for decap-style mode-3 testing.

      port1 --[Y2=1/(R+jwL)]-- port2
        |                          |
       Y1                         Y3       (small shunt to GND)
        |                          |
       GND                        GND

      port3 --[Y_decap = jwC_decap]-- port4    (a decap between two pads)
        |                          |
       Y1                         Y3
        |                          |
       GND                        GND

    Two independent 2-port pi sub-networks share the same 4-port Y as a
    block-diagonal arrangement. Used for testing mode 3 short-pair
    handling and to verify that signal-side measurement is independent
    of the (separately shorted) decap branch.
    """
    f = np.linspace(f_start, f_stop, n_pts)
    omega = 2 * np.pi * f
    Y_shunt = 1j * omega * C_shunt
    Y_series = 1.0 / (R_series + 1j * omega * L_series)
    Y_decap = 1j * omega * C_decap

    Y = np.zeros((n_pts, 4, 4), dtype=complex)
    # Top half (ports 1, 2): pi with R+L series
    Y[:, 0, 0] = Y_shunt + Y_series
    Y[:, 0, 1] = -Y_series
    Y[:, 1, 0] = -Y_series
    Y[:, 1, 1] = Y_shunt + Y_series
    # Bottom half (ports 3, 4): pi with C_decap series
    Y[:, 2, 2] = Y_shunt + Y_decap
    Y[:, 2, 3] = -Y_decap
    Y[:, 3, 2] = -Y_decap
    Y[:, 3, 3] = Y_shunt + Y_decap
    return {
        "freqs": f, "Y": Y,
        "port_names": ["sig_in", "sig_out", "decap_a", "decap_b"],
        "expect": {"R_series": R_series, "L_series": L_series,
                   "C_decap": C_decap, "C_shunt": C_shunt},
        "comment": "Synthetic 4-port: signal pi (ports 1-2) + decap pi (ports 3-4)\n"
                   f"R_series = {R_series} Ohm, L_series = {L_series} H\n"
                   f"C_decap = {C_decap} F\nC_shunt = {C_shunt} F",
    }


# ============================================================================
# Mutual-coupling fixtures (two coupled coils)
# ============================================================================
#
# SINGLE SOURCE OF TRUTH for the coupled-coil physics.  tests/test_coupling.py
# imports these names; the numbers are also stamped into each file's header
# comment so a fixture is self-describing on disk.
#
# Loop-level (mesh) impedance of two magnetically coupled coils:
#
#     Z_loop = [[R1 + j*w*L1,  j*w*M   ],
#               [j*w*M,        R2 + j*w*L2]]
#
# Y_loop = inv(Z_loop).  An incidence matrix A maps loop currents onto node
# currents -- column g of A holds +1 at the '+' terminal of coil g and -1 at its
# '-' terminal -- so the node-level admittance seen at the Touchstone ports is
#
#     Y_node = A @ Y_loop @ A.T
#
# which is exactly the +/- probe model the measurement-port API implements.
# ----------------------------------------------------------------------------

COUPLED_R1 = 0.6          # Ohm, series resistance of coil 1
COUPLED_R2 = 0.9          # Ohm, series resistance of coil 2
COUPLED_L1 = 2.0e-9       # H,   self inductance of coil 1
COUPLED_L2 = 3.0e-9       # H,   self inductance of coil 2
COUPLED_M = 0.8e-9        # H,   mutual inductance (positive-polarity fixtures)
COUPLED_M_NEG = -0.8e-9   # H,   mutual inductance of the reversed-polarity fixture
COUPLED_K = COUPLED_M / math.sqrt(COUPLED_L1 * COUPLED_L2)          # ~0.32660
COUPLED_K_NEG = COUPLED_M_NEG / math.sqrt(COUPLED_L1 * COUPLED_L2)  # ~-0.32660

# Terminal-to-ground capacitance used by coupled_4port_diff.  Small enough that
# it perturbs the differential inductance by <1e-3 relative at 1 GHz, large
# enough to lift Y out of exact singularity.
COUPLED_C_SHUNT = 5e-15   # F

# 0.1 GHz .. 10.0 GHz in exact 0.1 GHz steps, so 1e9 Hz is on the grid exactly.
COUPLED_F_START = 0.1e9
COUPLED_F_STOP = 10.0e9
COUPLED_N_PTS = 100

# The coupled fixtures are written at full double precision; see write_touchstone.
COUPLED_DIGITS = 17


def coupled_loop_y(freqs: np.ndarray,
                   R1: float = COUPLED_R1, L1: float = COUPLED_L1,
                   R2: float = COUPLED_R2, L2: float = COUPLED_L2,
                   M: float = COUPLED_M) -> np.ndarray:
    """(F, 2, 2) loop admittance Y_loop = inv(Z_loop) of two coupled coils."""
    omega = 2 * np.pi * np.asarray(freqs, dtype=float)
    Z = np.zeros((len(omega), 2, 2), dtype=complex)
    Z[:, 0, 0] = R1 + 1j * omega * L1
    Z[:, 1, 1] = R2 + 1j * omega * L2
    Z[:, 0, 1] = 1j * omega * M
    Z[:, 1, 0] = 1j * omega * M
    return np.linalg.inv(Z)


def coupled_node_y(freqs: np.ndarray, A: np.ndarray,
                   C_shunt: float = 0.0, **loop_kw) -> np.ndarray:
    """Y_node = A @ Y_loop @ A.T, plus an optional shunt C on every node."""
    Y_loop = coupled_loop_y(freqs, **loop_kw)
    A = np.asarray(A, dtype=complex)
    Y = np.einsum("ni,fij,mj->fnm", A, Y_loop, A)
    if C_shunt:
        omega = 2 * np.pi * np.asarray(freqs, dtype=float)
        idx = np.arange(A.shape[0])
        Y[:, idx, idx] += (1j * omega * C_shunt)[:, None]
    return Y


def _coupled_comment(title: str, M: float, C_shunt: float, wiring: str) -> str:
    k = M / math.sqrt(COUPLED_L1 * COUPLED_L2)
    return (
        f"{title}\n"
        f"Two magnetically coupled coils, Z_loop = [[R1+jwL1, jwM], [jwM, R2+jwL2]]\n"
        f"R1 = {COUPLED_R1!r} Ohm\n"
        f"R2 = {COUPLED_R2!r} Ohm\n"
        f"L1 = {COUPLED_L1!r} H\n"
        f"L2 = {COUPLED_L2!r} H\n"
        f"M  = {M!r} H\n"
        f"k  = M / sqrt(L1*L2) = {k!r}\n"
        f"C_shunt_each_terminal = {C_shunt!r} F\n"
        f"{wiring}\n"
        f"Values above are exact by construction; see tests/generate_test_snp.py."
    )


def _coupled_freqs() -> np.ndarray:
    return np.linspace(COUPLED_F_START, COUPLED_F_STOP, COUPLED_N_PTS)


def fixture_coupled_2port_gndref(M: float = COUPLED_M) -> dict:
    """
    2-port: each coil has one terminal grounded, so each coil is one
    ground-referenced measurement port.

        port1 --[coil 1]-- GND
        port2 --[coil 2]-- GND

    A = I(2), hence Y_node = Y_loop and the port impedance matrix is exactly
    Z_loop.  Two ground-referenced measurement ports must therefore recover
    L1, L2 and M to numerical precision -- no approximation anywhere.
    """
    f = _coupled_freqs()
    A = np.eye(2)
    Y = coupled_node_y(f, A, C_shunt=0.0, M=M)
    return {
        "freqs": f, "Y": Y,
        "port_names": ["coil1", "coil2"],
        "digits": COUPLED_DIGITS,
        "expect": {"R1": COUPLED_R1, "R2": COUPLED_R2,
                   "L1": COUPLED_L1, "L2": COUPLED_L2, "M": M,
                   "k": M / math.sqrt(COUPLED_L1 * COUPLED_L2)},
        "comment": _coupled_comment(
            "Synthetic 2-port coupled coils, ground-referenced", M, 0.0,
            "Wiring: port1 = coil1 '+' (coil1 '-' grounded), "
            "port2 = coil2 '+' (coil2 '-' grounded)"),
    }


def fixture_coupled_4port_diff(C_shunt: float = COUPLED_C_SHUNT,
                               M: float = COUPLED_M) -> dict:
    """
    4-port: both coils fully floating, with a small shunt C from every terminal
    to ground so Y stays non-singular.

        port1 --[coil 1]-- port2
        port3 --[coil 2]-- port4

    Measurement ports ('c1', +1/-2) and ('c2', +3/-4) see Z_loop perturbed only
    by the shunt capacitance.  Tying ports 1 and 2 to the SAME probe side
    instead gives the common mode, which is exactly 2*C_shunt to ground (the
    coil carries no current when both its terminals are at the same potential).
    """
    f = _coupled_freqs()
    A = np.array([[1.0, 0.0],
                  [-1.0, 0.0],
                  [0.0, 1.0],
                  [0.0, -1.0]])
    Y = coupled_node_y(f, A, C_shunt=C_shunt, M=M)
    return {
        "freqs": f, "Y": Y,
        "port_names": ["c1_p", "c1_n", "c2_p", "c2_n"],
        "digits": COUPLED_DIGITS,
        "expect": {"R1": COUPLED_R1, "R2": COUPLED_R2,
                   "L1": COUPLED_L1, "L2": COUPLED_L2, "M": M,
                   "k": M / math.sqrt(COUPLED_L1 * COUPLED_L2),
                   "C_shunt": C_shunt, "C_common_mode": 2 * C_shunt},
        "comment": _coupled_comment(
            "Synthetic 4-port coupled coils, floating (differential)", M,
            C_shunt,
            "Wiring: coil1 between port1(+) and port2(-), "
            "coil2 between port3(+) and port4(-)"),
    }


def fixture_coupled_4port_float(M: float = COUPLED_M) -> dict:
    """
    Same wiring as fixture_coupled_4port_diff but with NO shunt capacitance, so
    Y_node = A @ Y_loop @ A.T is EXACTLY singular (rank 2 of 4; the null space
    is the two common-mode directions).  This is the fixture that exercises the
    pinv path in compute_z_matrix and its rank-deficiency warning.

    Analytically, with W == A and A.T @ A == 2*I,
        W.T @ pinv(A Y A.T) @ W  ==  inv(Y_loop)  ==  Z_loop
    so the differential answer is exact despite the singularity.
    """
    f = _coupled_freqs()
    A = np.array([[1.0, 0.0],
                  [-1.0, 0.0],
                  [0.0, 1.0],
                  [0.0, -1.0]])
    Y = coupled_node_y(f, A, C_shunt=0.0, M=M)
    return {
        "freqs": f, "Y": Y,
        "port_names": ["c1_p", "c1_n", "c2_p", "c2_n"],
        "digits": COUPLED_DIGITS,
        "expect": {"R1": COUPLED_R1, "R2": COUPLED_R2,
                   "L1": COUPLED_L1, "L2": COUPLED_L2, "M": M,
                   "k": M / math.sqrt(COUPLED_L1 * COUPLED_L2)},
        "comment": _coupled_comment(
            "Synthetic 4-port coupled coils, fully floating (singular Y)", M,
            0.0,
            "Wiring: coil1 between port1(+) and port2(-), "
            "coil2 between port3(+) and port4(-); no shunt C -> Y is singular"),
    }


def fixture_coupled_2port_negM() -> dict:
    """Identical to fixture_coupled_2port_gndref but with M < 0 (reversed
    winding polarity).  M and k must come back NEGATIVE, never abs()-ed."""
    fx = fixture_coupled_2port_gndref(M=COUPLED_M_NEG)
    fx["comment"] = _coupled_comment(
        "Synthetic 2-port coupled coils, ground-referenced, NEGATIVE M",
        COUPLED_M_NEG, 0.0,
        "Wiring: port1 = coil1 '+' (coil1 '-' grounded), "
        "port2 = coil2 '+' (coil2 '-' grounded); coil 2 is wound backwards")
    return fx


# ============================================================================
# Generation harness
# ============================================================================

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def write_fixture(name: str, fx: dict, ext: str = None) -> Path:
    """Convert Y to S, write to FIXTURES_DIR/name.ext, return path."""
    Y = fx["Y"]
    n = Y.shape[-1]
    if ext is None:
        ext = f"s{n}p"
    S = y_to_s(Y, z0=DEFAULT_Z0)
    out = FIXTURES_DIR / f"{name}.{ext}"
    write_touchstone(out, fx["freqs"], S,
                     z0=DEFAULT_Z0,
                     port_names=fx["port_names"],
                     comment=fx["comment"],
                     digits=fx.get("digits", 9))
    return out


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing fixtures to {FIXTURES_DIR}")
    written = []
    written.append(write_fixture("shunt_rl_1port", fixture_shunt_rl_1port()))
    written.append(write_fixture("shunt_c_1port", fixture_shunt_c_1port()))
    written.append(write_fixture("pi_2port", fixture_pi_2port()))
    written.append(write_fixture("diff_pair_4port", fixture_diff_pair_4port()))
    written.append(write_fixture("decap_4port", fixture_decap_4port()))
    # Renamed-extension variants for content-sniffer tests
    written.append(write_fixture("pi_2port_renamed", fixture_pi_2port(), ext="txt"))
    written.append(write_fixture("diff_pair_4port_renamed", fixture_diff_pair_4port(),
                                 ext="dat"))
    # Mutual-coupling fixtures (see tests/test_coupling.py)
    written.append(write_fixture("coupled_2port_gndref",
                                 fixture_coupled_2port_gndref()))
    written.append(write_fixture("coupled_4port_diff",
                                 fixture_coupled_4port_diff()))
    written.append(write_fixture("coupled_4port_float",
                                 fixture_coupled_4port_float()))
    written.append(write_fixture("coupled_2port_negM",
                                 fixture_coupled_2port_negM()))
    for p in written:
        print("  ", p.name)


if __name__ == "__main__":
    main()
