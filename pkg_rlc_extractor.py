"""
pkg_rlc_extractor.py  --  Entry point.

Default: launch GUI.
With --cli: run a one-shot extraction from the command line.

Examples:
    python pkg_rlc_extractor.py
    python pkg_rlc_extractor.py --cli file.s45p --mode gnd --porta "1" \\
        --gnd "6:1:14" --freq 0.1
    python pkg_rlc_extractor.py --cli file.s45p --mode p2p --porta "1,2" \\
        --portb "3,4" --gnd "5:1:10" --freq 0.1 --csv output.csv
    python pkg_rlc_extractor.py --cli file.s4p --mode p2p --porta 1 --portb 2 \\
        --short "3-4" --fit auto --fmin 0.1 --fmax 5.0
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

from pkg_rlc_core import (
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    compute_z,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    parse_port_range,
    parse_short_pairs,
    parse_touchstone,
    s_to_y,
)


def _make_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pkg_rlc_extractor",
        description="Extract R/L/C/Q from Touchstone files using "
                    "Y-parameter Schur complement.",
    )
    p.add_argument("file", nargs="?", help="Touchstone file (any extension; "
                                           "content-sniffed)")
    p.add_argument("--cli", action="store_true",
                   help="Run in command-line mode instead of launching the GUI")
    p.add_argument("--mode", choices=["gnd", "p2p"], default="gnd",
                   help="Measurement mode: 'gnd' = signal->GND, "
                        "'p2p' = port-to-port (default: gnd)")
    p.add_argument("--porta", default="",
                   help="Port A specification, e.g. '1' or '1,3,5' or '35:1:45'")
    p.add_argument("--portb", default="",
                   help="Port B specification (required for p2p mode)")
    p.add_argument("--gnd", default="",
                   help="Ground ports specification")
    p.add_argument("--vdd", default="",
                   help="VDD ports (treated as AC ground)")
    p.add_argument("--short", default="",
                   help="Short pairs, e.g. '3-4,5-6'")
    p.add_argument("--freq", type=float, default=0.1,
                   help="Extraction frequency in GHz for single-point R/L/C/Q "
                        "(default: 0.1)")
    p.add_argument("--fit", choices=["none", "auto", "inductor", "capacitor"],
                   default="none",
                   help="Broadband fit model (default: none)")
    p.add_argument("--fmin", type=float, default=None,
                   help="Lower band edge for --fit (GHz)")
    p.add_argument("--fmax", type=float, default=None,
                   help="Upper band edge for --fit (GHz)")
    p.add_argument("--csv", default=None,
                   help="Write per-frequency Z + R/L/C/Q to this CSV path")
    p.add_argument("--force-nports", type=int, default=None,
                   help="Bypass content-based detection and force the port count")
    return p


def _run_cli(args: argparse.Namespace) -> int:
    if not args.file:
        print("ERROR: --cli requires a file argument", file=sys.stderr)
        return 2

    ts = parse_touchstone(args.file, force_nports=args.force_nports)
    print(f"Loaded {ts.source_path}: N={ts.nports}, M={len(ts.freqs)}, "
          f"Z0={ts.z0:g}Ω")
    for w in ts.parser_warnings:
        print(f"  WARN: {w}")
    Y = s_to_y(ts.s, ts.z0)

    a = parse_port_range(args.porta)
    b = parse_port_range(args.portb)
    g = parse_port_range(args.gnd)
    v = parse_port_range(args.vdd)
    sp = parse_short_pairs(args.short)

    if args.mode == "gnd":
        if not a:
            print("ERROR: --porta required", file=sys.stderr)
            return 2
        term = build_terminations_mode1(a, g)
    else:  # p2p
        if not a or not b:
            print("ERROR: p2p mode needs both --porta and --portb", file=sys.stderr)
            return 2
        if sp and v:
            print("ERROR: cannot combine --short and --vdd "
                  "(use Mode 3 OR Mode 4, not both)", file=sys.stderr)
            return 2
        if sp:
            term = build_terminations_mode3(a, b, g, sp)
        elif v:
            term = build_terminations_mode4(a, b, g, v)
        else:
            term = build_terminations_mode2(a, b, g)

    Z, warns = compute_z(Y, ts.freqs, term)
    for w in warns:
        print(f"  WARN: {w}")

    f_target_hz = args.freq * 1e9
    res = extract_rlc_at_freq(ts.freqs, Z, f_target_hz)
    print(f"\n@ {res.freq_hz/1e9:.4g} GHz:")
    print(f"  Z      = {res.Z.real:.4g} + j{res.Z.imag:.4g} Ω")
    print(f"  R      = {res.R_ohm*1000:.4g} mΩ")
    print(f"  L      = {res.L_henry*1e9:.4g} nH")
    print(f"  C      = {res.C_farad*1e12:.4g} pF")
    print(f"  Q      = {res.Q:.4g}")

    if args.fit != "none":
        if args.fmin is None or args.fmax is None:
            print("ERROR: --fit requires --fmin and --fmax (in GHz)",
                  file=sys.stderr)
            return 2
        fmin_hz, fmax_hz = args.fmin * 1e9, args.fmax * 1e9
        try:
            if args.fit == "auto":
                which, fit = fit_auto(ts.freqs, Z, fmin_hz, fmax_hz)
            elif args.fit == "inductor":
                which, fit = "inductor", fit_inductor(ts.freqs, Z, fmin_hz, fmax_hz)
            else:
                which, fit = "capacitor", fit_capacitor(ts.freqs, Z, fmin_hz, fmax_hz)
        except Exception as e:
            print(f"FIT ERROR: {e}", file=sys.stderr)
            return 1
        print(f"\nBroadband fit ({which}) over "
              f"[{args.fmin:g}, {args.fmax:g}] GHz:")
        if which == "inductor":
            print(f"  L          = {fit.L_henry*1e9:.4g} nH")
            print(f"  R_dc       = {fit.R_dc_ohm:.4g} Ω")
            print(f"  R_ac       = {fit.R_ac_ohm_per_sqrtHz:.3g} Ω/√Hz")
            print(f"  Q@center   = {fit.Q_at_center:.4g}")
        else:
            srf = (f"{fit.SRF_hz/1e9:.4g} GHz"
                   if not math.isnan(fit.SRF_hz) else "n/a (outside band)")
            print(f"  C          = {fit.C_farad*1e12:.4g} pF")
            print(f"  R_esr      = {fit.R_esr_ohm:.4g} Ω")
            print(f"  L_esl      = {fit.L_esl_henry*1e9:.4g} nH")
            print(f"  SRF        = {srf}")
        print(f"  RMSE       = {fit.rmse_ohm:.3g} Ω")

    if args.csv:
        omega = 2 * np.pi * ts.freqs
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            fh.write(f"# File: {ts.source_path}\n")
            fh.write(f"# Mode: {args.mode}, porta={args.porta}, "
                     f"portb={args.portb}, gnd={args.gnd}, vdd={args.vdd}, "
                     f"short={args.short}\n")
            w.writerow(["Freq_GHz", "Re_Z", "Im_Z", "abs_Z",
                        "R_mOhm", "L_nH", "C_pF", "Q"])
            for k in range(len(ts.freqs)):
                z = Z[k]
                f = ts.freqs[k]
                r = z.real
                im = z.imag
                L = im / omega[k] * 1e9 if im > 0 else float("nan")
                C = (-1.0 / (omega[k] * im) * 1e12) if im < 0 else float("nan")
                Q = abs(im) / r if r > 0 else float("nan")
                w.writerow([f"{f/1e9:.6g}",
                            f"{r:.6e}", f"{im:.6e}", f"{abs(z):.6e}",
                            f"{r*1000:.6e}", f"{L:.6e}",
                            f"{C:.6e}", f"{Q:.6e}"])
        print(f"\nWrote CSV: {args.csv}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _make_arg_parser()
    args = parser.parse_args(argv)

    if args.cli:
        return _run_cli(args)

    # Launch GUI
    from pkg_rlc_gui import App
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
