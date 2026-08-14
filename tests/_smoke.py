"""
Smoke check (not part of the formal test suite -- run once to validate
core math against the known answers in synth fixtures).
"""

from __future__ import annotations
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from pkg_rlc.physics.core import (
    parse_touchstone, s_to_y, compute_z, extract_rlc_at_freq,
    fit_inductor, fit_capacitor, fit_auto,
    build_terminations_mode1, build_terminations_mode2,
    build_terminations_mode3,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def near(a, b, rtol=1e-3):
    return abs(a - b) <= rtol * max(abs(a), abs(b), 1e-30)


def banner(title):
    print("\n=== " + title + " ===")


# ---------- Test 1: shunt R+L 1-port, mode 1 ----------
banner("Mode 1 on shunt_rl_1port.s1p")
ts = parse_touchstone(FIX / "shunt_rl_1port.s1p")
print(f"  parsed N={ts.nports}, nfreqs={len(ts.freqs)}, Z0={ts.z0}, "
      f"warns={ts.parser_warnings}")
Y = s_to_y(ts.s, ts.z0)
term = build_terminations_mode1(signal_ports=[1], gnd_ports=[])
Z, w = compute_z(Y, ts.freqs, term)
# At 1 GHz, expected R=0.5, L=1nH -> Z = 0.5 + j*2pi*1e9*1e-9 = 0.5 + j*6.2832
res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
print(f"  @ 1 GHz: R={res.R_ohm:.4g}, L={res.L_henry*1e9:.4g} nH (expect 0.5, 1.0)")
assert near(res.R_ohm, 0.5, 1e-3), f"R mismatch: {res.R_ohm}"
assert near(res.L_henry, 1e-9, 1e-3), f"L mismatch: {res.L_henry}"

# Broadband fit
fit = fit_inductor(ts.freqs, Z, 1e8, 5e9)
print(f"  fit: L={fit.L_henry*1e9:.4g} nH, R_dc={fit.R_dc_ohm:.4g}, "
      f"R_ac={fit.R_ac_ohm_per_sqrtHz:.3g}, RMSE={fit.rmse_ohm:.3g}")
assert near(fit.L_henry, 1e-9, 1e-3)
assert near(fit.R_dc_ohm, 0.5, 1e-2)


# ---------- Test 2: shunt C 1-port, mode 1 ----------
banner("Mode 1 on shunt_c_1port.s1p")
ts = parse_touchstone(FIX / "shunt_c_1port.s1p")
Y = s_to_y(ts.s, ts.z0)
term = build_terminations_mode1(signal_ports=[1], gnd_ports=[])
Z, w = compute_z(Y, ts.freqs, term)
# At 1 GHz, C=1pF: Z = 1/(j*2pi*1e9*1e-12) = -j*159.15
res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
print(f"  @ 1 GHz: C={res.C_farad*1e12:.4g} pF (expect 1.0)")
assert near(res.C_farad, 1e-12, 1e-3)
fit = fit_capacitor(ts.freqs, Z, 1e8, 5e9)
print(f"  fit: C={fit.C_farad*1e12:.4g} pF, RMSE={fit.rmse_ohm:.3g}")
assert near(fit.C_farad, 1e-12, 1e-2)


# ---------- Test 3: 2-port pi, mode 2 ----------
banner("Mode 2 on pi_2port.s2p")
ts = parse_touchstone(FIX / "pi_2port.s2p")
Y = s_to_y(ts.s, ts.z0)
term = build_terminations_mode2(port_a=[1], port_b=[2], gnd_ports=[])
Z, w = compute_z(Y, ts.freqs, term)
# Z ~= R + jwL  (with C_shunt small)
res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
print(f"  @ 1 GHz: R={res.R_ohm:.4g}, L={res.L_henry*1e9:.4g} nH "
      f"(expect ~1.0, ~1.0)")
assert near(res.R_ohm, 1.0, 1e-2)
assert near(res.L_henry, 1e-9, 1e-2)


# ---------- Test 4: 4-port diff pair, mode 3 ----------
banner("Mode 3 on diff_pair_4port.s4p")
ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
Y = s_to_y(ts.s, ts.z0)
term = build_terminations_mode3(port_a=[1], port_b=[2], gnd_ports=[],
                                short_pairs=[(3, 4)])
Z, w = compute_z(Y, ts.freqs, term)
# Expected L_loop = 2*(L_self - M) = 2*(5 - 1) = 8 nH
res = extract_rlc_at_freq(ts.freqs, Z, 1e9)
print(f"  @ 1 GHz: L_loop={res.L_henry*1e9:.4g} nH (expect 8.0)")
assert near(res.L_henry, 8e-9, 1e-2)
fit = fit_inductor(ts.freqs, Z, 1e8, 3e9)
print(f"  fit L={fit.L_henry*1e9:.4g} nH, RMSE={fit.rmse_ohm:.3g}")
assert near(fit.L_henry, 8e-9, 1e-2)


# ---------- Test 5: content-sniffer on renamed files ----------
banner("Content sniffer on renamed files")
ts1 = parse_touchstone(FIX / "pi_2port_renamed.txt")
print(f"  pi_2port_renamed.txt -> N={ts1.nports} (expect 2)")
assert ts1.nports == 2
ts2 = parse_touchstone(FIX / "diff_pair_4port_renamed.dat")
print(f"  diff_pair_4port_renamed.dat -> N={ts2.nports} (expect 4)")
assert ts2.nports == 4


# ---------- Test 6: auto fit chooses correct model ----------
banner("Auto fit selection")
ts = parse_touchstone(FIX / "shunt_c_1port.s1p")
Y = s_to_y(ts.s, ts.z0)
Z, _ = compute_z(Y, ts.freqs, build_terminations_mode1([1], []))
which, fit = fit_auto(ts.freqs, Z, 1e8, 5e9)
print(f"  shunt_c -> {which} (expect capacitor)")
assert which == "capacitor"

ts = parse_touchstone(FIX / "shunt_rl_1port.s1p")
Y = s_to_y(ts.s, ts.z0)
Z, _ = compute_z(Y, ts.freqs, build_terminations_mode1([1], []))
which, fit = fit_auto(ts.freqs, Z, 1e8, 5e9)
print(f"  shunt_rl -> {which} (expect inductor)")
assert which == "inductor"


# ---------- Test 7: VDD == Ground equivalence ----------
banner("VDD treated as Ground (mode 4)")
from pkg_rlc.physics.core import build_terminations_mode4, Vdd, Ground
# Use diff_pair: pretend port 4 is VDD instead of part of differential
ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
Y = s_to_y(ts.s, ts.z0)
t_v = build_terminations_mode4(port_a=[1], port_b=[2], gnd_ports=[3], vdd_ports=[4])
t_g = build_terminations_mode4(port_a=[1], port_b=[2], gnd_ports=[3, 4], vdd_ports=[])
Zv, _ = compute_z(Y, ts.freqs, t_v)
Zg, _ = compute_z(Y, ts.freqs, t_g)
maxdiff = float(np.max(np.abs(Zv - Zg)))
print(f"  max |Z_vdd - Z_gnd| = {maxdiff:.3e} (expect ~0)")
assert maxdiff < 1e-9


print("\nAll smoke checks PASSED.")
