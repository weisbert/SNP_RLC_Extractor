"""
generate_test_snp.py  --  Synthesize Touchstone fixtures with known impedance.

Each fixture has analytically known R/L/C so tests can verify extraction
correctness to within numerical precision.

Run as a script to (re)generate all fixtures into ./fixtures/.
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
                     comment: str = "") -> None:
    """
    Write a Touchstone file (RI format, Hz units).

    Layout:
      n=1: each freq on one line: freq Re(S11) Im(S11)
      n=2: each freq on one line, Touchstone v1 column-major: S11 S21 S12 S22
      n>2: each freq spans n lines: row-major; freq sits on line 0
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = S.shape[-1]
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
            lines.append(f"{freqs[k]:.9e}  {Sk[0,0].real:.9e} {Sk[0,0].imag:.9e}")
        elif n == 2:
            lines.append(
                f"{freqs[k]:.9e}  "
                f"{Sk[0,0].real:.9e} {Sk[0,0].imag:.9e}  "
                f"{Sk[1,0].real:.9e} {Sk[1,0].imag:.9e}  "
                f"{Sk[0,1].real:.9e} {Sk[0,1].imag:.9e}  "
                f"{Sk[1,1].real:.9e} {Sk[1,1].imag:.9e}"
            )
        else:
            for i in range(n):
                vals = []
                for j in range(n):
                    vals.append(f"{Sk[i,j].real:.9e}")
                    vals.append(f"{Sk[i,j].imag:.9e}")
                row_str = " ".join(vals)
                if i == 0:
                    lines.append(f"{freqs[k]:.9e}  {row_str}")
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
                     comment=fx["comment"])
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
    for p in written:
        print("  ", p.name)


if __name__ == "__main__":
    main()
