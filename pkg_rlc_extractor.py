"""
pkg_rlc_extractor.py  --  Entry point.

Default: launch GUI.
With --cli: run a one-shot extraction from the command line.

Examples:
    python pkg_rlc_extractor.py

    # self impedance to ground
    python pkg_rlc_extractor.py --cli file.s45p --mode gnd --porta "1" \\
        --gnd "6:1:14" --freq 0.1

    # port-to-port (differential) impedance
    python pkg_rlc_extractor.py --cli file.s45p --mode p2p --porta "1,2" \\
        --portb "3,4" --gnd "5:1:10" --freq 0.1 --csv output.csv
    python pkg_rlc_extractor.py --cli file.s4p --mode p2p --porta 1 --portb 2 \\
        --short "3-4" --fit auto --fmin 0.1 --fmax 5.0

    # self + mutual coupling: one --mport per measurement port
    #   "name = <red-probe ports> / <black-probe ports>"
    #   the name and the '/' side are both optional (no '-' side = to ground)
    python pkg_rlc_extractor.py --cli tank_rx.s8p --mode coupling \\
        --mport "tank = 1 / 2" --mport "rx = 3 / 4" --gnd "5:1:8" --freq 5.0
    python pkg_rlc_extractor.py --cli xfmr.s4p --mode coupling \\
        --mport "w1 = 1 / 3" --mport "w2 = 2 / 4" --freq 1.0 --csv coupling.csv
    python pkg_rlc_extractor.py --cli bus.s16p --mode coupling \\
        --mport "vic = 1" --mport "agg = 2" --gnd "3:1:16" --freq 1.0

Note: --vdd is deprecated.  For AC small-signal analysis VDD is identical to
ground, so any --vdd ports are simply unioned into the --gnd list.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

from pkg_rlc_core import (
    RECIPROCITY_WARN,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    compute_z,
    compute_z_matrix,
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    format_si,
    parse_mport_spec,
    parse_port_range,
    parse_short_pairs,
    parse_touchstone,
    s_to_y,
)


def _make_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pkg_rlc_extractor",
        description="Extract R/L/C/Q (and mutual M / k) from Touchstone files "
                    "using Y-parameter Schur complement.",
    )
    p.add_argument("file", nargs="?", help="Touchstone file (any extension; "
                                           "content-sniffed)")
    p.add_argument("--cli", action="store_true",
                   help="Run in command-line mode instead of launching the GUI")
    p.add_argument("--mode", choices=["gnd", "p2p", "coupling"], default="gnd",
                   help="Measurement mode: 'gnd' = signal->GND, "
                        "'p2p' = port-to-port, 'coupling' = N measurement "
                        "ports with the full self + mutual Z matrix "
                        "(default: gnd)")
    p.add_argument("--porta", default="",
                   help="Port A specification, e.g. '1' or '1,3,5' or '35:1:45'")
    p.add_argument("--portb", default="",
                   help="Port B specification (required for p2p mode)")
    p.add_argument("--mport", action="append", default=None, metavar="SPEC",
                   help="Measurement port for --mode coupling; repeatable. "
                        "Syntax: '<name> = <+ ports> / <- ports>', e.g. "
                        "'tank = 1,3 / 2,4'. The '+' side is the red probe, "
                        "the '-' side the black probe; both sides accept "
                        "ranges ('6-14', '35:1:45'). The name is optional "
                        "(auto: P1, P2, ...) and an omitted '-' side means "
                        "the port is referenced to ground. 'A' and 'B' are "
                        "reserved names.")
    p.add_argument("--gnd", default="",
                   help="Ground ports specification")
    p.add_argument("--vdd", default="",
                   help="DEPRECATED alias for --gnd: for AC small-signal "
                        "analysis VDD is identical to ground, so these ports "
                        "are unioned into the ground list")
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
                   help="Write per-frequency Z + R/L/C/Q to this CSV path "
                        "(coupling mode: Re/Im of every Z_ij plus M and k)")
    p.add_argument("--force-nports", type=int, default=None,
                   help="Bypass content-based detection and force the port count")
    return p


# ============================================================================
# Formatting helpers (coupling report)
# ============================================================================

_NAME_W = 16          # measurement-port names are truncated to this for tables


def _trunc(s: str, w: int) -> str:
    return s if len(s) <= w else s[: w - 1] + "~"


def _fmt_complex(z: complex, sig: int = 4) -> str:
    """'a + jb' / 'a - jb' with `sig` significant digits."""
    re = z.real
    im = z.imag
    sign = "-" if (im < 0.0) else "+"
    return f"{re:.{sig}g} {sign} j{abs(im):.{sig}g}"


def _fmt_num(value: float, sig: int = 4) -> str:
    return "nan" if not math.isfinite(value) else f"{value:.{sig}g}"


def _fmt_db(value: float, sig: int = 4) -> str:
    return "nan dB" if not math.isfinite(value) else f"{value:.{sig}g} dB"


def _sign_flag_port(port) -> str:
    """Compact flag: 'cap' or 'ind' per Im(Z) sign, plus 'R<0' if non-passive.

    Same wording as the GUI results pane, so the two agree.
    """
    flags = []
    if math.isfinite(port.L_henry):
        if port.L_henry < 0:
            flags.append("cap")
        elif port.L_henry > 0:
            flags.append("ind")
    if math.isfinite(port.R_ohm) and port.R_ohm < 0:
        flags.append("R<0")
    return ",".join(flags)


def _print_table(headers: list[str], rows: list[list[str]],
                 aligns: list[str], indent: str = "  ") -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt_row(cells: list[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            if aligns[i] == "<":
                out.append(f"{cell:<{widths[i]}}")
            else:
                out.append(f"{cell:>{widths[i]}}")
        return indent + "  ".join(out).rstrip()

    print(_fmt_row(headers))
    print(indent + "  ".join("-" * w for w in widths))
    for row in rows:
        print(_fmt_row(row))


def _print_coupling_report(res) -> None:
    """Print the Z matrix, the self table, every pair, and the reciprocity check."""
    G = len(res.names)
    disp = [_trunc(n, _NAME_W) for n in res.names]

    # --- 1. The G x G Z matrix -------------------------------------------
    print(f"\n@ {res.freq_hz / 1e9:.4g} GHz  --  Z matrix (Ω), open-circuit: "
          "every other measurement port carries no current")
    cells = [[_fmt_complex(complex(res.Z_matrix[i, j])) for j in range(G)]
             for i in range(G)]
    cell_w = max([len(c) for row in cells for c in row] + [len(n) for n in disp])
    lbl_w = max(len(n) for n in disp)
    print("  " + " " * lbl_w + "  " + "  ".join(f"{n:>{cell_w}}" for n in disp))
    for i in range(G):
        print(f"  {disp[i]:<{lbl_w}}  "
              + "  ".join(f"{c:>{cell_w}}" for c in cells[i]))

    # --- 2. Self impedance table -----------------------------------------
    print("\nSelf impedance (diagonal). Signs are physical (Cadence convention):")
    rows = []
    for p in res.ports:
        rows.append([
            _trunc(p.name, _NAME_W),
            format_si(p.R_ohm, "Ω"),
            format_si(p.L_henry, "H"),
            format_si(p.C_farad, "F"),
            _fmt_num(p.Q, 4),
            _sign_flag_port(p),
        ])
    _print_table(["Port", "R", "L", "C", "Q", "Sign"], rows,
                 ["<", ">", ">", ">", ">", "<"])
    print("  legend: ind = Im(Z)>0 (inductive) | "
          "cap = Im(Z)<0 (capacitive; past SRF for an inductor) | "
          "R<0 = non-passive")

    # --- 3. Pairwise coupling --------------------------------------------
    if not res.pairs:
        print("\nOnly one measurement port defined; no mutual terms. "
              "Add a second --mport to get M / k.")
    else:
        print("\nMutual coupling (per unordered pair):")
        for pr in res.pairs:
            na = _trunc(pr.name_a, _NAME_W)
            nb = _trunc(pr.name_b, _NAME_W)
            print(f"\n  {na} <-> {nb}")

            m_note = ("negative: sign kept -- opposite probe polarity, or "
                      "capacitive coupling (see note)"
                      if (math.isfinite(pr.M_henry) and pr.M_henry < 0) else "")
            c_note = ("negative: Im(Z_ab)>0, the coupling is inductive here "
                      "-- read M" if (math.isfinite(pr.C_c_farad)
                                      and pr.C_c_farad < 0) else "")
            k_note = ("negative: the two probe pairs are anti-aligned"
                      if (math.isfinite(pr.k) and pr.k < 0) else "")

            lines: list[tuple[str, str, str, str]] = [
                ("Z_ab", f"{_fmt_complex(pr.Z_ab)} Ω", "", ""),
                ("M", format_si(pr.M_henry, "H"), "Im(Z_ab)/ω", m_note),
                ("C_c", format_si(pr.C_c_farad, "F"), "-1/(ω*Im(Z_ab))", c_note),
                ("k", _fmt_num(pr.k), f"M/sqrt(L_{na} * L_{nb})", k_note),
                (f"M/L_{na}",
                 f"{_fmt_num(pr.M_over_La)}  ({_fmt_db(pr.M_over_La_dB)})",
                 f"coupling ratio into {na}", ""),
                (f"M/L_{nb}",
                 f"{_fmt_num(pr.M_over_Lb)}  ({_fmt_db(pr.M_over_Lb_dB)})",
                 f"coupling ratio into {nb}", ""),
            ]
            lw = max(len(l[0]) for l in lines)
            vw = max(len(l[1]) for l in lines[1:])
            for label, value, formula, note in lines:
                tail = f"   {formula}" if formula else ""
                if note:
                    tail += f"   <- {note}"
                print(f"    {label:<{lw}} = {value:<{vw}}{tail}".rstrip())
            for note in pr.notes:
                print(f"    note: {note}")
        print("\n  M/L_x is the first-order Norton injection ratio into x -- "
              "frequency-independent, and the number a spur / pulling budget "
              "is written against. It is not the exact current-transfer ratio "
              "|Z_ab/Z_aa|, which it matches only where omega*L_x >> R_x.")

    # --- 4. Reciprocity ---------------------------------------------------
    if not res.pairs:
        return          # nothing off-diagonal to check
    print(f"\nReciprocity error = {res.reciprocity_error:.3g}   "
          "(max|Z_ab - Z_ba| / max|Z_ab| over the finite off-diagonal entries)")
    if not any(math.isfinite(p.Z_ab.real) and math.isfinite(p.Z_ab.imag)
               for p in res.pairs):
        print("  (nothing to check -- every mutual term is undefined; fix the "
              "port setup first)")
    elif res.reciprocity_error > RECIPROCITY_WARN:
        print(f"  WARN: above {RECIPROCITY_WARN:g}. Z_ab and Z_ba disagree; "
              "the input S-parameters are suspect (non-reciprocal or "
              "under-converged EM solve) -- suspect the EM/de-embedding "
              "setup, not this tool.")
    else:
        print(f"  (data looks reciprocal; the alarm threshold is "
              f"{RECIPROCITY_WARN:g}. A clean EM solve lands at 1e-16..1e-9, "
              "so a few 1e-9s here are normal, not a defect.)")


def _print_fit(which: str, fit) -> None:
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


def _run_fit(freqs: np.ndarray, Z: np.ndarray, args):
    """Return (which, fit). Raises on failure; caller reports."""
    fmin_hz, fmax_hz = args.fmin * 1e9, args.fmax * 1e9
    if args.fit == "auto":
        return fit_auto(freqs, Z, fmin_hz, fmax_hz)
    if args.fit == "inductor":
        return "inductor", fit_inductor(freqs, Z, fmin_hz, fmax_hz)
    return "capacitor", fit_capacitor(freqs, Z, fmin_hz, fmax_hz)


def _write_coupling_csv(path: str, ts, args, Zmat: np.ndarray,
                        names: list[str]) -> None:
    """Per frequency: Re/Im of every Z_ij, plus M_nH and k for every pair."""
    G = len(names)
    freqs = ts.freqs
    omega = 2 * np.pi * freqs
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        fh.write(f"# File: {ts.source_path}\n")
        fh.write("# Mode: coupling, mport=" + " | ".join(args.mport or [])
                 + f", gnd={args.gnd}, vdd={args.vdd}, short={args.short}\n")
        fh.write("# Z_ij in ohms (open-circuit). M_nH = Im(Z_ij)/omega, "
                 "k = M/sqrt(L_i*L_j) with L = Im(Z_ii)/omega.\n")
        fh.write("# All values keep their physical sign (Cadence convention); "
                 "k is nan where L_i <= 0 or L_j <= 0.\n")
        head = ["Freq_GHz"]
        for i in range(G):
            for j in range(G):
                head.append(f"Re_Z_{names[i]}_{names[j]}")
                head.append(f"Im_Z_{names[i]}_{names[j]}")
        for i in range(G):
            for j in range(i + 1, G):
                head.append(f"M_nH_{names[i]}_{names[j]}")
                head.append(f"k_{names[i]}_{names[j]}")
        w.writerow(head)

        for kf in range(len(freqs)):
            om = float(omega[kf])
            Zk = Zmat[kf]
            row = [f"{freqs[kf]/1e9:.6g}"]
            for i in range(G):
                for j in range(G):
                    z = complex(Zk[i, j])
                    row.append(f"{z.real:.6e}")
                    row.append(f"{z.imag:.6e}")
            if om != 0.0:
                Ls = [float(Zk[g, g].imag) / om for g in range(G)]
            else:
                Ls = [float("nan")] * G
            for i in range(G):
                for j in range(i + 1, G):
                    M = float(Zk[i, j].imag) / om if om != 0.0 else float("nan")
                    La, Lb = Ls[i], Ls[j]
                    if (math.isfinite(M) and math.isfinite(La)
                            and math.isfinite(Lb) and La > 0.0 and Lb > 0.0):
                        kk = M / math.sqrt(La * Lb)
                    else:
                        kk = float("nan")
                    row.append(f"{M*1e9:.6e}")
                    row.append(f"{kk:.6e}")
            w.writerow(row)


# ============================================================================
# CLI driver
# ============================================================================

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

    # VDD is an AC ground for small-signal analysis: merge and move on.
    if v:
        print("NOTE: --vdd is deprecated. For AC small-signal analysis VDD is "
              "identical to ground, so these ports are merged into --gnd.")
        g = sorted(set(g) | set(v))

    if args.mode == "coupling":
        if a or b:
            print("ERROR: coupling mode uses --mport, not --porta/--portb "
                  "(e.g. --mport \"tank = 1 / 2\")", file=sys.stderr)
            return 2
        specs = args.mport or []
        if not specs:
            print("ERROR: coupling mode needs at least one --mport, e.g. "
                  "--mport \"tank = 1 / 2\"", file=sys.stderr)
            return 2
        try:
            mports = [parse_mport_spec(s) for s in specs]
            term = build_terminations_coupling(mports, g, sp,
                                               nports=ts.nports)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    else:
        if args.mport:
            print("ERROR: --mport is only valid with --mode coupling",
                  file=sys.stderr)
            return 2
        if args.mode == "gnd":
            if not a:
                print("ERROR: --porta required", file=sys.stderr)
                return 2
            term = build_terminations_mode1(a, g)
        else:  # p2p
            if not a or not b:
                print("ERROR: p2p mode needs both --porta and --portb",
                      file=sys.stderr)
                return 2
            if sp:
                term = build_terminations_mode3(a, b, g, sp)
            else:
                term = build_terminations_mode2(a, b, g)

    f_target_hz = args.freq * 1e9

    # ---------------- coupling mode ----------------
    if args.mode == "coupling":
        try:
            Zmat, names, warns = compute_z_matrix(Y, ts.freqs, term)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        for w in warns:
            print(f"  WARN: {w}")
        if any("Rank-deficient" in w for w in warns):
            print("  (rank-deficient node admittance is informational: a fully "
                  "floating differential structure trips it at every "
                  "frequency and pinv still gives the right answer)")
        if any("row and column of Z are NaN" in w for w in warns):
            print("  (a 'no return path' warning is NOT informational: those "
                  "measurement ports are reported as nan because the reading "
                  "is undefined, not small. Give the port a '-' side or add "
                  "the missing --gnd ports.)")
        if any("cancelled to roundoff" in w for w in warns):
            print("  (a 'cancelled to roundoff' warning is NOT informational "
                  "either: the numbers below are printed, but they are "
                  "roundoff noise. Fix the port setup before reading them.)")

        print("\nMeasurement ports: "
              + ", ".join(f"{n} [{i}]" for i, n in enumerate(names)))
        res = extract_coupling_at_freq(ts.freqs, Zmat, names, f_target_hz)
        _print_coupling_report(res)

        if args.fit != "none":
            if args.fmin is None or args.fmax is None:
                print("ERROR: --fit requires --fmin and --fmax (in GHz)",
                      file=sys.stderr)
                return 2
            for gi, name in enumerate(names):
                try:
                    which, fit = _run_fit(ts.freqs, Zmat[:, gi, gi], args)
                except Exception as e:
                    print(f"FIT ERROR ({name}): {e}", file=sys.stderr)
                    return 1
                print(f"\nBroadband fit ({which}) of the self impedance of "
                      f"'{name}' over [{args.fmin:g}, {args.fmax:g}] GHz:")
                _print_fit(which, fit)

        if args.csv:
            _write_coupling_csv(args.csv, ts, args, Zmat, names)
            print(f"\nWrote CSV: {args.csv}")
        return 0

    # ---------------- legacy single-measurement modes ----------------
    try:
        Z, warns = compute_z(Y, ts.freqs, term)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    for w in warns:
        print(f"  WARN: {w}")

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
        try:
            which, fit = _run_fit(ts.freqs, Z, args)
        except Exception as e:
            print(f"FIT ERROR: {e}", file=sys.stderr)
            return 1
        print(f"\nBroadband fit ({which}) over "
              f"[{args.fmin:g}, {args.fmax:g}] GHz:")
        _print_fit(which, fit)

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
                L = im / omega[k] * 1e9 if omega[k] != 0.0 else float("nan")
                C = (-1.0 / (omega[k] * im) * 1e12) if (omega[k] != 0.0 and im != 0.0) else float("nan")
                Q = im / r if r != 0.0 else float("nan")
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
