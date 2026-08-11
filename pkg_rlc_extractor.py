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

    # where that mutual coupling COMES FROM, and what would move it
    #   --attribute names a victim and an aggressor; every --attribute-* flag
    #   below is inert without it.  Candidate terminations are YOURS to supply:
    #   with none given the scan is limited to 'open' and 'ideal'.
    python pkg_rlc_extractor.py --cli bus.s16p --mode coupling \\
        --mport "vic = 1" --mport "agg = 2" --gnd "3:1:16" --freq 1.0 \\
        --attribute vic,agg
    python pkg_rlc_extractor.py --cli pkg.s45p --mode coupling \\
        --mport "dco = 1" --mport "rx = 2" --gnd "6:1:14" --freq 5.0 \\
        --attribute dco,rx --attribute-alt "L=0.3n" --attribute-alt "R=0.5,L=1n" \\
        --attribute-ground-model "shared:L=1n" --attribute-freqs "1,5,10" \\
        --attribute-csv attribution.csv

Note: --vdd is deprecated.  For AC small-signal analysis VDD is identical to
ground, so any --vdd ports are simply unioned into the --gnd list.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import textwrap
from pathlib import Path

import numpy as np

import pkg_rlc_attrib as attrib
from pkg_rlc_core import (
    FAULT_NONE,
    RECIPROCITY_WARN,
    TouchstoneParseError,
    build_terminations_coupling,
    check_touchstone,
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
    format_freq,
    format_si,
    name_prefix,
    parse_kv_rlc_params,
    parse_mport_spec,
    parse_port_range,
    parse_short_pairs,
    parse_touchstone,
    s_to_y,
    y_series_rlc,
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
    p.add_argument("--diagnose", action="store_true",
                   help="Report what the file contains and whether it hangs "
                        "together (size, encoding, option line, sweep, record "
                        "grid), then exit. Works on files that fail to load; "
                        "needs no other flag")
    p.add_argument("--lenient", action="store_true",
                   help="Skip values that do not parse instead of refusing "
                        "the file. Off by default: Touchstone is a positional "
                        "stream, so a dropped value shifts every value after "
                        "it. Check the result")

    # ---- attribution (--mode coupling only) ------------------------------
    # Kept in their own argparse group so `--help` does not read as if these
    # belonged to the ordinary extraction flags: every one of them is inert
    # without --attribute, and _run_cli refuses them by name when it is absent.
    at_grp = p.add_argument_group(
        "attribution (--mode coupling only)",
        "Where a mutual impedance comes from, and what would move it. "
        "Exact, signed and additive: the per-element terms sum to the total, "
        "and every what-if is a full re-solve, not a first-order slope.")
    at_grp.add_argument("--attribute", default=None, metavar="VICTIM,AGGRESSOR",
                        help="Decompose Z(victim, aggressor) into the bare EM "
                             "coupling plus one term per declared termination, "
                             "and scan what would move it. Both sides are "
                             "measurement-port NAMES as given to --mport "
                             "(e.g. --attribute vic,agg); a plain integer is "
                             "read as a 1-BASED position in the --mport list. "
                             "The two must differ -- Z_ab is a mutual "
                             "impedance")
    at_grp.add_argument("--attribute-alt", action="append", default=None,
                        metavar="SPEC",
                        help="Candidate termination for the sensitivity scan; "
                             "repeatable. 'open' (the element is not there), "
                             "'ideal' (a perfect short to the reference), or a "
                             "series R/L/C in the same spelling the Mode 5 DSL "
                             "uses: 'R=50', 'L=0.3n', 'R=0.5,L=1n', 'C=100p'. "
                             "YOU supply these -- this tool will not guess what "
                             "your ground ball's inductance ought to be. With "
                             "none given the scan is limited to the two "
                             "STRUCTURAL candidates, open and ideal, which need "
                             "no such judgement. Any candidate with a finite "
                             "impedance is also used as a victim load in the "
                             "exact current-transfer ratio")
    at_grp.add_argument("--attribute-ground-model", default="diag",
                        metavar="MODEL",
                        help="How the declared ground/shunt leads are modelled. "
                             "'diag' (DEFAULT) = exactly as declared, i.e. every "
                             "--gnd port is a perfect 0 ohm ground on its own "
                             "independent lead. 'diag:SPEC' = every shunt lead "
                             "gets SPEC as its own independent series impedance. "
                             "'shared:SPEC' = every shunt lead keeps what it "
                             "declares and they all ALSO share SPEC back to the "
                             "reference (a dense element-impedance matrix). "
                             "The default is the obvious model and on a real "
                             "package it is the wrong one: ground balls share a "
                             "return plane, so independent leads understate the "
                             "effective common-mode return inductance by "
                             "(1 + (n-1)k). Measured on "
                             "tests/fixtures/diff_pair_4port.s4p at 5 GHz with "
                             "ports 3 and 4 grounded, 'diag:L=1n' gives "
                             "M = 1.012 nH and 'shared:L=1n' gives 2.026 nH -- "
                             "6.03 dB apart on two balls, and it grows with the "
                             "ball count")
    at_grp.add_argument("--attribute-freqs", default="", metavar="LIST",
                        help="Extra frequencies in GHz (comma separated) at "
                             "which to re-rank the contributions, so a ranking "
                             "read off one frequency can be checked for "
                             "stability across the band. --freq is always the "
                             "first column")
    at_grp.add_argument("--attribute-group", default="row",
                        choices=["row", "flat", "name"],
                        help="How elements are grouped for the joint-effect "
                             "section. 'row' (default) = by the flag that "
                             "declared the port, which is this CLI's equivalent "
                             "of the GUI's connection-table provenance. 'flat' = "
                             "no grouping, one element per group. 'name' = by "
                             "the file's port NAMES with the trailing index "
                             "stripped -- that is a NAMING HEURISTIC, not a "
                             "fact about the network: it reads 'VSS_ball_31' and "
                             "'VSS_ball_32' as one family because they are "
                             "spelled alike, and it is wrong exactly as often "
                             "as the naming is")
    at_grp.add_argument("--attribute-csv", default=None, metavar="PATH",
                        help="Write every attribution record to this CSV -- "
                             "terms, the full UNCAPPED sensitivity scan, joint "
                             "effects, the cumulative curve, the sweeps and the "
                             "cross-frequency ranks. One row per record, tagged "
                             "by a 'section' column")
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
# Attribution (--attribute):  where a mutual impedance comes from
# ============================================================================
#
# `pkg_rlc_attrib` does every piece of arithmetic below.  Everything in this
# section is argument parsing, ranking and printing -- if a number here is not
# straight off one of that module's dataclasses it is a bug, because a second
# implementation of the same quantity is exactly how the CLI and the GUI would
# start disagreeing about a 6 dB dispute.

#: Rows of the sensitivity ranking printed to the terminal.  The CSV has NO
#: cap, which is the only thing that makes the "(see --attribute-csv)" pointer
#: true -- the same split the coupling report's ranked pair list makes, where
#: `_write_coupling_csv` enumerates every pair and the printed list is floored.
ATTR_RANK_ROWS = 20

#: Element groups given a Mobius sweep.  With --attribute-group row this CLI
#: can only ever produce three groups (--mport / --gnd / --short), so the cap
#: bites only in flat or name mode, where a 60-ball package would otherwise
#: print 60 sweeps of six lines each.
ATTR_SWEEP_GROUPS = 3

#: Elements entered into the PAIRWISE non-additivity scan, strongest first.
#: Unlike ATTR_RANK_ROWS this cap is computational rather than cosmetic -- the
#: scan is O(k^2) full re-solves -- so it applies to the CSV too and both say
#: so.  8 elements is 28 pairs.
ATTR_PAIR_POOL = 8

#: Group-level joint effects printed.  Same reason as ATTR_SWEEP_GROUPS.
ATTR_GROUP_ROWS = 8

# There is deliberately NO cap on the cumulative curve: its k = 1, 2, 4, ...
# schedule is the engine's default and is logarithmic in the element count, so
# a 60-ball package is seven rows.

_ATTR_LINE = "=" * 74
_ATTR_RULE = "-" * 74


def _attr_section(title: str, subtitle: str = "") -> None:
    """A numbered section head, with its explanatory line UNDER the rule."""
    print("\n" + _ATTR_RULE)
    print(title)
    print(_ATTR_RULE)
    if subtitle:
        for line in _attr_wrap(subtitle):
            print(line)
        print()


def _attr_wrap(text: str, indent: str = "  ", width: int = 78,
               hang: str | None = None) -> list[str]:
    """
    Wrap one of pkg_rlc_attrib's sentence-long notes to terminal width.

    `hang` is the continuation indent when it differs from the first line's --
    a bulleted caveat whose second line starts under the '*' reads as a second
    bullet, which is the one thing a list of three must not do.
    """
    # break_on_hyphens=False: textwrap's default splits "last-assignment-wins"
    # across two lines, and a hyphenated technical term broken at the margin
    # reads as two different words.
    return textwrap.wrap(text, width=width, initial_indent=indent,
                         subsequent_indent=(indent if hang is None else hang),
                         break_on_hyphens=False) or [indent.rstrip()]


def _attr_series_impedance(spec: str, omega: float) -> tuple[complex | None, str]:
    """
    'R=0.5,L=1n' -> (its series impedance at `omega`, a label).  None is OPEN.

    Deliberately the SAME R/L/C spelling the Mode 5 DSL uses
    (`parse_kv_rlc_params` + `y_series_rlc`), so one syntax covers a lumped
    termination inside a spec and a what-if candidate here, and in particular
    so that `M` keeps meaning Mega on both sides of the tool.

    OPEN comes back as None rather than as a very large number: pkg_rlc_attrib
    removes the element from the network entirely, which is a different -- and
    exact -- thing from stamping 1e12 ohms.

    A comma-separated field with no '=' is REFUSED.  `parse_kv_rlc_params`
    silently DROPS such a token, so 'R=5,m' would compute 5 ohm where 5
    milliohm was meant: the same factor-of-1000 trap core's `_rlc_tokens`
    refuses, arriving here through a different door.  A bare number is refused
    for the neighbouring reason -- '50' does not say whether it is ohms,
    henries or farads, and guessing would be silently wrong rather than loudly
    wrong.
    """
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("empty termination spec")
    low = raw.lower()
    if low == "open":
        return None, "open"
    if low in ("gnd", "ground", "ideal", "short"):
        return 0j, "ideal"
    fields = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in fields if "=" not in t]
    if bad:
        raise ValueError(
            f"'{raw}': field(s) {', '.join(repr(b) for b in bad)} carry no "
            "'='. A candidate termination is 'open', 'ideal', or R=/L=/C= "
            "fields separated by commas -- 'R=50', 'L=0.3n', 'R=0.5,L=1n', "
            "'C=100p'. A bare number is refused because it does not say "
            "whether it means ohms, henries or farads")
    try:
        params = parse_kv_rlc_params(fields)
    except ValueError as e:
        # parse_kv_rlc_params names the offending KEY but not the spec it came
        # from, and a repeatable flag can carry six of them.
        raise ValueError(f"'{raw}': {e}") from None
    with np.errstate(divide="ignore", invalid="ignore"):
        y = complex(np.asarray(y_series_rlc(**params)(np.array([omega])))[0])
    label = ",".join(fields)
    if y == 0:
        return None, label                      # infinite impedance == open
    if not (math.isfinite(y.real) and math.isfinite(y.imag)):
        return 0j, label                        # R=L=0, no C: a perfect short
    return 1.0 / y, label


def _attr_alternative(spec: str, omega: float) -> attrib.Alternative:
    z, label = _attr_series_impedance(spec, omega)
    return attrib.Alternative(label, z)


def _attr_ground_model(spec: str,
                       omega: float) -> tuple[str, complex | None, str]:
    """'diag' | 'diag:SPEC' | 'shared:SPEC'  ->  (kind, impedance, label)."""
    raw = (spec or "").strip()
    low = raw.lower()
    if low == "diag":
        return "diag", None, "diag (as declared)"
    for kind in ("diag", "shared"):
        if low.startswith(kind + ":"):
            body = raw[len(kind) + 1:].strip()
            if not body:
                raise ValueError(
                    f"'{raw}': '{kind}:' needs an impedance after the colon, "
                    f"e.g. '{kind}:L=1n'")
            z, label = _attr_series_impedance(body, omega)
            if z is None:
                raise ValueError(
                    f"'{raw}': an OPEN lead is not a ground model -- an "
                    "element that is not in the network has no impedance to "
                    "give itself or to share. Drop the port from --gnd "
                    "instead")
            return kind, z, f"{kind}:{label}"
    raise ValueError(
        f"'{raw}' is not one of 'diag', 'diag:SPEC' or 'shared:SPEC' (SPEC "
        "being an R/L/C termination such as L=1n)")


def _attr_zt(ctx, kind: str,
             z: complex | None) -> tuple[np.ndarray | None, list[str]]:
    """
    The (m, m) element impedance matrix this ground model asks for, or None to
    keep the one the spec itself declares.

    Only the SHUNT elements are touched.  `termination_impedance_shared_return`
    assumes every element it is given is a ball sharing the return plane, so
    handing it the whole matrix would give a `short_to` a return impedance and
    quietly stop it being a short; the dense block is therefore built on the
    shunt sub-block and scattered back in.  The builder is still the one place
    the dense form is written down.
    """
    notes: list[str] = []
    if z is None:
        return None, notes
    shunts = [i for i, e in enumerate(ctx.elements) if e.is_shunt]
    if not shunts:
        notes.append(
            "The ground model was ignored: this spec declares no shunt "
            "element (no --gnd port, no lumped termination to ground), so "
            "there is no ground lead to model.")
        return None, notes
    if kind == "shared" and len(shunts) < 2:
        notes.append(
            "'shared' with a single shunt element is the same network as "
            "'diag' with the same impedance -- there is nothing for it to "
            "share the return with.")
    Zt = np.array(ctx.Zt, dtype=complex)
    if kind == "diag":
        for i in shunts:
            Zt[i, i] = z
        return Zt, notes
    sub = attrib.termination_impedance_shared_return(
        [complex(Zt[i, i]) for i in shunts], z)
    Zt[np.ix_(shunts, shunts)] = sub
    return Zt, notes


def _attr_parse_pair(spec: str) -> tuple[str, str]:
    parts = [p.strip() for p in (spec or "").split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"expected exactly two measurement ports separated by ONE comma "
            f"(VICTIM,AGGRESSOR), got {len(parts)} field(s) in "
            f"'{spec}'. A measurement-port name may not contain a comma")
    return parts[0], parts[1]


def _attr_resolve_port(token: str, names: list[str]) -> tuple[int, list[str]]:
    """
    A measurement-port name, or a 1-BASED position in the --mport list.

    Names win.  A user who names a measurement port '1' gets that port and a
    note saying so, rather than a silent off-by-one against a position they
    never asked for.
    """
    listing = ", ".join(f"{i + 1} '{n}'" for i, n in enumerate(names))
    if token in names:
        notes = []
        if token.lstrip("+-").isdigit():
            notes.append(
                f"'{token}' was resolved to the measurement port NAMED "
                f"'{token}', not to position {token} in the --mport list: a "
                "name always wins over a position.")
        return names.index(token), notes
    try:
        i = int(token)
    except ValueError:
        raise ValueError(
            f"no measurement port is named '{token}', and it is not an "
            f"integer position either. This spec has: {listing}") from None
    if not 1 <= i <= len(names):
        raise ValueError(
            f"measurement-port position {i} is outside 1..{len(names)}. "
            f"This spec has: {listing}")
    return i - 1, []


def _attr_parse_freqs(text: str) -> list[float]:
    """'1,5,10' (GHz) -> [1e9, 5e9, 10e9]."""
    out: list[float] = []
    for tok in (text or "").split(","):
        t = tok.strip()
        if not t:
            continue
        try:
            out.append(float(t) * 1e9)
        except ValueError:
            raise ValueError(
                f"'{t}' is not a frequency in GHz. --attribute-freqs takes a "
                "comma-separated list, e.g. '1,5,10'") from None
    return out


def _attr_snap(freqs: np.ndarray, f_hz: float) -> float:
    """
    The grid point `build_context` will land on -- the same argmin.

    Used so that a candidate termination is EVALUATED at the omega it is later
    APPLIED at.  Off by one grid point they would differ by the sweep step,
    which on a coarse file is not small.
    """
    fa = np.asarray(freqs, dtype=float)
    return float(fa[int(np.argmin(np.abs(fa - float(f_hz))))])


def _attr_sources(group_mode: str, args: argparse.Namespace,
                  specs: list[str], gnd: list[int],
                  shorts: list[tuple[int, int]], port_names: list[str],
                  nports: int) -> tuple[dict[int, str], list[str]]:
    """
    1-based port -> the label of the group its declaration belongs to.

    This is the CLI's `row_sources`.  Core's version reads the GUI's connection
    table; here the "rows" are the flags the user typed, so a package's ground
    balls written as one `--gnd 6:1:14` are ONE group exactly as they would be
    one table row.  The walk order -- measurement ports, then --gnd, then
    --short -- mirrors both `rows_to_dsl_text` and
    `build_terminations_coupling`, and the LAST writer wins, because the DSL
    both of those agree with is last-assignment-wins.
    """
    notes: list[str] = []
    gnd_label = f"--gnd {args.gnd.strip()}" if args.gnd.strip() else "--gnd"
    if args.vdd.strip():
        gnd_label += f" (+ --vdd {args.vdd.strip()})"
    short_label = (f"--short {args.short.strip()}" if args.short.strip()
                   else "--short")

    row: dict[int, str] = {}
    for spec in specs:
        _name, plus, minus = parse_mport_spec(spec)
        label = f'--mport "{spec.strip()}"'
        for p in list(plus) + list(minus):
            row[p] = label
    overlap = sorted(set(gnd) & {p for pair in shorts for p in pair})
    for p in gnd:
        row[p] = gnd_label
    for pa, pb in shorts:
        row[pa] = short_label
        row[pb] = short_label
    if overlap:
        notes.append(
            "Port(s) " + ", ".join(str(p) for p in overlap) + " are named by "
            "both --gnd and --short. Provenance is last-assignment-wins (the "
            "same rule the connection table follows), so their ground element "
            f"is grouped under '{short_label}' as well. Use "
            "--attribute-group flat to see each element on its own.")

    if group_mode == "row":
        return row, notes

    if group_mode == "flat":
        src = {p: f"port {p}" for p in range(1, nports + 1)}
        firsts = [pa for pa, _pb in shorts]
        dup = sorted({p for p in firsts if firsts.count(p) > 1})
        if dup:
            notes.append(
                "flat groups an element under its FIRST port, and port(s) "
                + ", ".join(str(p) for p in dup)
                + " start more than one short, so those shorts share a group.")
        return src, notes

    # 'name': a NAMING HEURISTIC.  core's name_prefix strips only a TRAILING
    # run of digits, which is what keeps 'c1_p' and 'c2_p' two families rather
    # than one -- but it is still spelling, not topology, and a file that names
    # nothing degenerates to the row grouping without saying anything unless we
    # do.
    src = {}
    named = 0
    for p in range(1, nports + 1):
        nm = port_names[p - 1] if p - 1 < len(port_names) else ""
        pfx = name_prefix(nm)
        if pfx:
            named += 1
            src[p] = f"name '{pfx}*'"
        else:
            src[p] = row.get(p, "(unnamed port)")
    if named == 0:
        notes.append(
            "--attribute-group name found no port names in this file, so "
            "every group fell back to the flag that declared it, i.e. this is "
            "the 'row' grouping.")
    else:
        notes.append(
            f"--attribute-group name is a NAMING HEURISTIC: {named} of "
            f"{nports} ports carry a name, and ports whose names differ only "
            "in a trailing number are read as one family. That is a statement "
            "about spelling, not about the network -- check it against the "
            "layout before believing a family total.")
    return src, notes


# ---------------------------------------------------------------------------
# The CSV.  One long table with a `section` column: the records have genuinely
# different shapes (a term, a swap, a sweep interval), and a single header that
# csv.DictReader round-trips beats five tables nobody can join.
# ---------------------------------------------------------------------------

_ATTR_CSV_FIELDS = [
    "section", "freq_GHz", "victim", "aggressor", "quantity", "unit",
    "group", "element", "alternative", "value_re", "value_im",
    "delta_re", "delta_im", "delta_dB", "share_inline", "share_quad",
    "current_re", "current_im", "extra",
]


def _e(x: float) -> str:
    """A float for the CSV: %.6e, and nan/inf spelled out rather than blank."""
    v = float(x)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return f"{v:.6e}"


def _attr_row(section: str, **kw) -> dict:
    """One CSV record.  An unknown field name RAISES rather than vanishing."""
    row = {f: "" for f in _ATTR_CSV_FIELDS}
    for k in kw:
        if k not in row:
            raise KeyError(f"unknown attribution CSV field '{k}'")
    row["section"] = section
    row.update(kw)
    return row


def _write_attrib_csv(path: str, ts, args, rows: list[dict],
                      header_notes: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# File: {ts.source_path}\n")
        fh.write(f"# Attribution: {args.attribute}, mport="
                 + " | ".join(args.mport or [])
                 + f", gnd={args.gnd}, vdd={args.vdd}, short={args.short}\n")
        fh.write(f"# Ground model: {args.attribute_ground_model}   "
                 f"grouping: {args.attribute_group}\n")
        for line in header_notes:
            fh.write("# " + line + "\n")
        # Requirement 11: the sign convention travels with EVERY export, not
        # only with the terminal report, because a CSV outlives the terminal.
        for line in textwrap.wrap(attrib.SIGN_CONVENTION_TEXT, 100):
            fh.write("# " + line + "\n")
        fh.write("# Sections: term (per-element split of Z_ab and of M), "
                 "total, return_budget, ground_model, sensitivity (every element x every "
                 "candidate, UNCAPPED), leave_one_out, group_joint, "
                 "pair_joint, cumulative, sweep, transfer, rank.\n")
        w = csv.DictWriter(fh, fieldnames=_ATTR_CSV_FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _attr_print_context(ctx, model_label: str, group_mode: str,
                        notes: list[str]) -> None:
    """The header: what was measured, how well conditioned it is, what moved."""
    print(f"  frequency        : {format_freq(ctx.freq_hz)}"
          + ("" if ctx.freq_hz == ctx.requested_hz
             else f"   (requested {format_freq(ctx.requested_hz)}; snapped to "
                  "the file's grid)"))
    print("  measurement ports: "
          + "   ".join(f"{i + 1} '{n}'" for i, n in enumerate(ctx.port_names))
          + "      <- --attribute takes either the name or the number")
    print(f"  ground model     : {model_label}")
    print(f"  element grouping : {group_mode}")
    print(f"  elements         : {ctx.n_elements} declared, "
          f"{len(ctx.groups)} group(s)")
    print(f"  conditioning     : cond(Ybase) {ctx.cond_Ybase:.3g}   "
          f"cond(G) {ctx.cond_G:.3g}   "
          f"reciprocity |r_a - p_a|/|p_a| {ctx.reciprocity_rel:.3g}")
    if ctx.reciprocity_rel > RECIPROCITY_WARN:
        for line in _attr_wrap(
                "WARN: the baseline is not reciprocal to within "
                f"{RECIPROCITY_WARN:g}. r_a is solved separately from p_a so "
                "the numbers below are still right, but a non-reciprocal EM "
                "solve is worth explaining before a budget is written against "
                "it.", "    "):
            print(line)
    for el, why in ctx.dropped:
        for line in _attr_wrap(f"dropped: {el.describe()} -- {why}", "    ",
                               hang="      "):
            print(line)
    for n in list(notes) + list(ctx.notes):
        for line in _attr_wrap("note: " + n, "    ", hang="      "):
            print(line)
    for w in ctx.warnings:
        for line in _attr_wrap("WARN: " + w, "    ", hang="      "):
            print(line)
    for w in ctx.core_warnings:
        for line in _attr_wrap("WARN (compute_z_matrix): " + w, "    ",
                               hang="      "):
            print(line)


def _attr_print_terms(decZ, decM, csv_rows: list[dict]) -> None:
    """
    The decomposition table, grouped by provenance and ordered by group weight.

    Both quantities are printed because they answer different halves of the
    question.  Z_ab is what actually decomposes -- the complex terms are what
    add up -- and the share/quadrature pair (requirement 7) only means anything
    there: a term at 90 degrees to the total inflates every magnitude-based
    cancellation measure while being harmless.  M is the number the budget is
    written in, and for it the quadrature share is identically zero because
    M is real by construction.
    """
    if not decZ.terms:
        print("  (per-element split WITHHELD -- see the reconciliation below)")
        return
    if len(decZ.terms) != len(decM.terms):        # pragma: no cover
        print("  (per-element split unavailable: the Z and M decompositions "
              "disagree about the element list)")
        return

    # group -> [(term_Z, term_M)], with the bare EM term in its own pseudo-group
    # because it belongs to no declaration: it is what is left when every
    # non-probe port is open.
    buckets: dict[str, list[tuple]] = {}
    for tz, tm in zip(decZ.terms, decM.terms):
        key = "(baseline: bare EM)" if tz.element is None else tz.element.source
        buckets.setdefault(key, []).append((tz, tm))

    def weight(item) -> float:
        return abs(sum(tz.contribution for tz, _ in item[1]))

    order = sorted(buckets.items(), key=weight, reverse=True)

    rows: list[list[str]] = []
    for label, items in order:
        for tz, tm in items:
            rows.append([
                label,
                tz.label,
                "--" if not math.isfinite(abs(tz.current))
                else format_si(abs(tz.current), "A"),
                _fmt_complex(tz.contribution, 4),
                format_si(tm.contribution.real, decM.unit),
                "--" if not math.isfinite(tz.share_inline)
                else f"{100 * tz.share_inline:.2f}%",
                "--" if not math.isfinite(tz.share_quad)
                else f"{100 * tz.share_quad:.2f}%",
            ])
    _print_table(["group", "element", "|I_e|", "Z term (Ω)", "M term",
                  "share", "quad"], rows,
                 ["<", "<", ">", ">", ">", ">", ">"])

    if len(order) > 1:
        print("\n  By group:")
        grows = []
        for label, items in order:
            zt = sum(tz.contribution for tz, _ in items)
            mt = sum(tm.contribution for _, tm in items)
            sh = sum(tz.share_inline for tz, _ in items)
            grows.append([label, str(len(items)), _fmt_complex(zt, 4),
                          format_si(mt.real, decM.unit),
                          "--" if not math.isfinite(sh) else f"{100 * sh:.2f}%"])
        _print_table(["group", "n", "Z total (Ω)", "M total", "share"], grows,
                     ["<", ">", ">", ">", ">"])

    for tz, tm in zip(decZ.terms, decM.terms):
        csv_rows.append(_attr_row(
            "term", freq_GHz=_e(decZ.freq_hz / 1e9),
            victim=decZ.victim, aggressor=decZ.aggressor,
            quantity="Z", unit="Ohm",
            group=("" if tz.element is None else tz.element.source),
            element=tz.label,
            value_re=_e(tz.contribution.real), value_im=_e(tz.contribution.imag),
            share_inline=_e(tz.share_inline), share_quad=_e(tz.share_quad),
            current_re=_e(tz.current.real), current_im=_e(tz.current.imag),
            extra=f"trans_z={tz.trans_z}"))
        csv_rows.append(_attr_row(
            "term", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity="M", unit="H",
            group=("" if tm.element is None else tm.element.source),
            element=tm.label,
            value_re=_e(tm.contribution.real), value_im=_e(tm.contribution.imag),
            share_inline=_e(tm.share_inline), share_quad=_e(tm.share_quad)))


def _attr_print_reconciliation(decZ, decM) -> None:
    print(f"  Z_ab  total (compute_z_matrix) : "
          f"{_fmt_complex(decZ.total_reference, 6)} Ω")
    print(f"  Z_ab  total (sum of the terms) : "
          f"{_fmt_complex(decZ.total_sum, 6)} Ω")
    print(f"  M     total (compute_z_matrix) : "
          f"{format_si(decM.total_reference.real, decM.unit)}")
    print(f"  residual {decZ.residual_rel:.3g} relative, against an achievable "
          f"floor of {decZ.residual_floor:.3g}")
    print(f"    (the floor is what cond(Ybase)={decZ.cond_Ybase:.3g} and "
          f"cond(H)={decZ.cond_H:.3g} allow in double precision; it is not a "
          "quality target)")
    if decZ.residual_rel <= decZ.residual_floor:
        print("    the two algorithms agree to within it -- the split above "
              "may be read as exact")
    # C_c is a first-class reading of this tool whenever the coupling is
    # capacitive, and it is NOT decomposable (it is a reciprocal of the
    # decomposed quantity).  Print the total and quote the engine's own reason
    # rather than leaving a hole where the number the user came for should be.
    im = decZ.total_reference.imag
    om = 2.0 * math.pi * decZ.freq_hz
    if om != 0.0 and im != 0.0:
        c_c = -1.0 / (om * im)
        if im < 0.0:
            print(f"\n  Im(Z_ab) < 0: the coupling is CAPACITIVE here and "
                  f"C_c = {format_si(c_c, 'F')} is the reading, not M "
                  f"({format_si(decM.total_reference.real, decM.unit)}).")
        else:
            print(f"\n  C_c = {format_si(c_c, 'F')} (negative: Im(Z_ab) > 0, "
                  "so the coupling is inductive here and M is the reading).")
        for line in _attr_wrap(
                "C_c has NO per-element split: "
                + attrib.NON_DECOMPOSABLE["C_c"] + ".", "    "):
            print(line)


def _attr_print_sensitivity(rows: list, csv_rows: list[dict], dec) -> None:
    """
    The ranking: every element against every candidate, strongest first.

    An UNDEFINED delta sorts last rather than in the middle, the same rule
    `rank_coupling_pairs` follows: NaN is a missing measurement, not a small
    number, and letting it float to wherever 0.0 lands would bury a real
    result under a row that says nothing.
    """
    if not rows:                                             # pragma: no cover
        return
    ranked = sorted(rows, key=lambda r: (0 if math.isfinite(r.abs_delta) else 1,
                                         -r.abs_delta
                                         if math.isfinite(r.abs_delta) else 0.0))
    shown = ranked[:ATTR_RANK_ROWS]
    trows = []
    for r in shown:
        trows.append([
            r.label, r.alternative,
            format_si(r.new_value.real, r.unit),
            format_si(r.delta.real, r.unit),
            _fmt_db(r.delta_db),
        ])
    _print_table(["element", "candidate", f"{rows[0].quantity} after",
                  "Δ", "Δ (dB)"], trows, ["<", "<", ">", ">", ">"])
    if len(ranked) > len(shown):
        print(f"  ... {len(ranked) - len(shown)} more rows "
              "(all of them are in --attribute-csv, which has no cap)")
    for r in rows:
        csv_rows.append(_attr_row(
            "sensitivity", freq_GHz=_e(dec.freq_hz / 1e9),
            victim=dec.victim, aggressor=dec.aggressor,
            quantity=r.quantity, unit=r.unit, element=r.label,
            alternative=r.alternative,
            value_re=_e(r.new_value.real), value_im=_e(r.new_value.imag),
            delta_re=_e(r.delta.real), delta_im=_e(r.delta.imag),
            delta_dB=_e(r.delta_db)))


def _attr_dependent_flags(args: argparse.Namespace) -> list[str]:
    """The --attribute-* flags the user set.  Inert without --attribute."""
    given: list[str] = []
    if args.attribute_alt:
        given.append("--attribute-alt")
    if (args.attribute_ground_model or "diag") != "diag":
        given.append("--attribute-ground-model")
    if (args.attribute_freqs or "").strip():
        given.append("--attribute-freqs")
    if (args.attribute_group or "row") != "row":
        given.append("--attribute-group")
    if args.attribute_csv:
        given.append("--attribute-csv")
    return given


def _run_attribution(args: argparse.Namespace, ts, Y: np.ndarray, term,
                     specs: list[str], gnd: list[int],
                     shorts: list[tuple[int, int]],
                     names: list[str]) -> int:
    """
    The whole --attribute report.  Returns a process exit code.

    Everything that can be wrong about the COMMAND LINE is checked before any
    linear algebra runs, so a typo costs a message rather than a minute of
    Schur reductions on a 153-port file.
    """
    # ---- 1. the command line -------------------------------------------
    try:
        vic_tok, agg_tok = _attr_parse_pair(args.attribute)
        a, notes_a = _attr_resolve_port(vic_tok, names)
        b, notes_b = _attr_resolve_port(agg_tok, names)
    except ValueError as e:
        print(f"ERROR: --attribute: {e}", file=sys.stderr)
        return 2
    if a == b:
        print(f"ERROR: --attribute: victim and aggressor are the same "
              f"measurement port '{names[a]}'. Z_ab is a MUTUAL impedance -- "
              f"name two different --mport entries.", file=sys.stderr)
        return 2
    # No separate "you only defined one measurement port" branch: with one
    # --mport the only reachable pair is 1,1, which the a == b refusal above
    # already answers by name, and any other token is out of range.

    f_primary = _attr_snap(ts.freqs, args.freq * 1e9)
    omega = 2.0 * math.pi * f_primary
    try:
        alts = [_attr_alternative(s, omega) for s in (args.attribute_alt or [])]
    except ValueError as e:
        print(f"ERROR: --attribute-alt: {e}", file=sys.stderr)
        return 2
    user_supplied_alts = bool(alts)
    if not alts:
        # Requirement's second concern: never infer what a port "should" be.
        # open and ideal need no engineering judgement -- they are "the element
        # is not there" and "the element is perfect" -- so they are the only
        # two this tool will assume on the user's behalf.
        alts = [attrib.alt_open(), attrib.alt_ideal()]
    try:
        gm_kind, gm_z, gm_label = _attr_ground_model(
            args.attribute_ground_model, omega)
    except ValueError as e:
        print(f"ERROR: --attribute-ground-model: {e}", file=sys.stderr)
        return 2
    try:
        extra_freqs = _attr_parse_freqs(args.attribute_freqs)
    except ValueError as e:
        print(f"ERROR: --attribute-freqs: {e}", file=sys.stderr)
        return 2

    src, src_notes = _attr_sources(args.attribute_group, args, specs, gnd,
                                   shorts, ts.port_names, ts.nports)

    # ---- 2. the two contexts --------------------------------------------
    #
    # `ctx_ref` is the spec AS DECLARED.  It is the only configuration
    # `compute_z_matrix` can be asked about, so it is the only one whose split
    # can be reconciled -- and requirement 5 makes the engine's value the
    # authoritative total.  Sections 1, 2, 3, 8 and 9 therefore run on it, and
    # so do the numbers the coupling report above already printed.
    #
    # `ctx` adds the ground model.  That is a DIFFERENT NETWORK (a dense
    # element-impedance matrix is not expressible as a TerminationSet: it is a
    # mutual impedance between ground leads, and the DSL has no node to hang
    # one on), so it has no independent reference and its what-ifs are the
    # honest place for it.  Sections 4 to 7 run on it.
    def build(f_hz: float):
        """
        (declared context, modelled context, notes) at one frequency.

        The modelled one is a SECOND build, because the element impedance
        matrix is sized by the element list and the element list is what the
        first build discovers.  Measured on tests/fixtures/diff_pair_4port.s4p:
        0.25 ms per build, and it is O(N^3) in the PORT count rather than in
        the sweep length -- build_context slices ONE frequency out before it
        does anything, so a 5000-point file costs the same as a 200-point one.
        """
        c0 = attrib.build_context(Y, ts.freqs, term, f_hz, sources=src)
        zt, zt_notes = _attr_zt(c0, gm_kind, gm_z)
        if zt is None:
            return c0, c0, zt_notes
        return c0, attrib.build_context(Y, ts.freqs, term, f_hz, zt=zt,
                                        sources=src), zt_notes

    try:
        ctx_ref, ctx, zt_notes = build(f_primary)
    except (attrib.AttribError, ValueError) as e:
        print(f"ERROR: --attribute: {e}", file=sys.stderr)
        return 2
    modelled = ctx is not ctx_ref

    csv_rows: list[dict] = []
    header_notes = list(src_notes) + list(zt_notes) + notes_a + notes_b
    if args.attribute_group == "flat":
        header_notes.append(
            "flat groups each element under its FIRST port, so a ground and a "
            "short that both start at the same port share a group.")

    # ---- 3. the report ---------------------------------------------------
    print("\n" + _ATTR_LINE)
    print(f"ATTRIBUTION of the mutual impedance:  victim '{names[a]}'  <-  "
          f"aggressor '{names[b]}'")
    print(_ATTR_LINE)
    _attr_print_context(ctx_ref, gm_label, args.attribute_group, header_notes)

    # -- requirement 11: the sign convention, before any signed number.
    _attr_section("0. Sign convention")
    for line in _attr_wrap(attrib.SIGN_CONVENTION_TEXT):
        print(line)

    try:
        decZ = attrib.decompose(ctx_ref, a, b, "Z")
        decM = attrib.decompose(ctx_ref, a, b, "M")
    except attrib.AttribError as e:
        print(f"ERROR: --attribute: {e}", file=sys.stderr)
        return 2

    _attr_section(
        "1. Decomposition of Z_ab, grouped by what declared each port",
        "The bare EM coupling -- what the file already has with every "
        "non-probe port OPEN -- plus one exact, signed term per declared "
        "element. The terms ADD UP; this is superposition, not a "
        "linearisation.")
    _attr_print_terms(decZ, decM, csv_rows)

    _attr_section("2. Reconciliation against compute_z_matrix")
    _attr_print_reconciliation(decZ, decM)
    csv_rows.append(_attr_row(
        "total", freq_GHz=_e(decZ.freq_hz / 1e9), victim=decZ.victim,
        aggressor=decZ.aggressor, quantity="Z", unit="Ohm",
        value_re=_e(decZ.total_reference.real),
        value_im=_e(decZ.total_reference.imag),
        extra=f"sum={decZ.total_sum};residual={decZ.residual_rel:.6g};"
              f"floor={decZ.residual_floor:.6g};"
              f"trustworthy={decZ.split_trustworthy}"))
    csv_rows.append(_attr_row(
        "total", freq_GHz=_e(decM.freq_hz / 1e9), victim=decM.victim,
        aggressor=decM.aggressor, quantity="M", unit="H",
        value_re=_e(decM.total_reference.real),
        value_im=_e(decM.total_reference.imag)))
    # decompose() seeds its notes from ctx.notes, which the header already
    # printed; only what it ADDED belongs here.  Those are the ones about this
    # pair -- an exactly-zero Z_ab renormalising the residual, a suppressed
    # share column, an undefined L_a -- and they are exactly the notes that
    # explain a '--' in the table above.
    seen = set(ctx_ref.notes)
    for n in decZ.notes:
        # decompose() always appends the blind-to-open-ports / spelling note,
        # which is caveat 1 and 2 at the foot of this report; printing it here
        # as well would say the same paragraph twice on one screen.
        if n in seen or n.startswith("This is a decomposition of the "):
            continue
        for line in _attr_wrap("note: " + n, "  ", hang="        "):
            print(line)
    for w in decZ.warnings:
        for line in _attr_wrap("WARN: " + w, "  ", hang="        "):
            print(line)

    _attr_section("3. Return-path budget")
    rb = decZ.return_budget
    for line in _attr_wrap(rb.note):
        print(line)
    csv_rows.append(_attr_row(
        "return_budget", freq_GHz=_e(decZ.freq_hz / 1e9), victim=decZ.victim,
        aggressor=decZ.aggressor, quantity="I", unit="A",
        value_re=_e(rb.declared),
        extra=f"em_reference={_e(rb.em_reference)};"
              f"declared_all={_e(rb.declared_all)};"
              f"em_fraction={_e(rb.em_fraction)};dominant={rb.dominant}"))

    if modelled:
        _attr_print_ground_model(ctx, a, b, gm_label, decM, csv_rows)

    # Sections 4 to 7 all need at least one element to change.  8 and 9 do
    # not -- the cross-frequency ranking and the exact transfer ratio are
    # about the pair, not about the terminations -- so they still run, and
    # skipping them here would have withheld the one thing an all-open spec
    # can still be told.
    if ctx.n_elements == 0:
        print("\n" + _ATTR_RULE)
        # Two different situations, and telling them apart is the whole
        # message: "you declared nothing" is a next step, while "everything
        # you declared was annihilated by the reduction" is a bug in the spec
        # that the header has already named element by element.
        if ctx.dropped:
            body = (
                "Every non-probe termination this spec declares was dropped "
                "before the split (see the 'dropped:' lines above), so there "
                "is nothing to attribute and nothing to sweep: Z_ab is the "
                "bare EM coupling with every other port open. That is almost "
                "certainly not what the spec meant.")
        else:
            body = (
                "This spec declares no non-probe termination at all, so there "
                "is nothing to attribute and nothing to sweep: Z_ab is the "
                "bare EM coupling with every other port open. Declare the "
                "ports you are unsure about (--gnd / --short) and the "
                "sensitivity scan's 'open' row will tell you what each one "
                "does.")
        for line in _attr_wrap(body):
            print(line)

    if ctx.n_elements:
        _attr_print_whatifs(ctx, a, b, alts, user_supplied_alts,
                            modelled, omega, decM, csv_rows)

    # -- 8. cross-frequency rank stability.  On the DECLARED context, so it
    # re-ranks exactly the table section 1 printed.
    _attr_section("8. Cross-frequency rank stability",
                  "The same ranking, by |contribution to Z_ab|, re-run at "
                  "each frequency on the spec AS DECLARED. A ranking that "
                  "moves is a ranking that belongs to one frequency.")
    _attr_print_stability(ts, Y, term, src, a, b, f_primary, decZ,
                          extra_freqs, csv_rows)

    # -- 9. the exact current-transfer ratio
    _attr_section("9. Exact current-transfer ratio (docs/theory.md 8.8)",
                  "M/L_a is only the FIRST-ORDER Norton approximation to the "
                  "current a shorted victim actually draws. They agree where "
                  "omega*L_a >> R_a and part company below that corner.")
    # ctx_ref: transfer_ratio reads Zref, i.e. compute_z_matrix's own matrix,
    # which the ground model does not and cannot move.
    tr = attrib.transfer_ratio(ctx_ref, a, b)
    print(f"  -Z_ab / Z_aa       = {_fmt_complex(tr.ratio, 4)}   "
          f"({_fmt_db(tr.ratio_db)})   <- exact, victim shorted")
    print(f"  M / L_a            = {_fmt_num(tr.norton)}   "
          f"({_fmt_db(tr.norton_db)})   <- the first-order Norton "
          "approximation, i.e. the 'coupling ratio' the report above prints")
    print(f"  difference         = {_fmt_db(tr.error_db)}")
    csv_rows.append(_attr_row(
        "transfer", freq_GHz=_e(tr.freq_hz / 1e9), victim=tr.victim,
        aggressor=tr.aggressor, quantity="-Z_ab/Z_aa", unit="",
        value_re=_e(tr.ratio.real), value_im=_e(tr.ratio.imag),
        delta_dB=_e(tr.error_db),
        extra=f"norton={tr.norton:.6e};ratio_dB={tr.ratio_db:.6e};"
              f"norton_dB={tr.norton_db:.6e}"))
    loads = [alt for alt in alts
             if alt.z is not None and alt.z != 0 and user_supplied_alts]
    if loads:
        print("\n  Loaded, -Z_ab / (Z_aa + Z_load), with each finite "
              "impedance you named in --attribute-alt used as the victim "
              "load:")
        lrows2 = []
        for alt in loads:
            trl = attrib.transfer_ratio(ctx_ref, a, b, z_load=alt.z)
            lrows2.append([alt.name, _fmt_complex(complex(trl.loaded), 4),
                           _fmt_db(trl.loaded_db)])
            csv_rows.append(_attr_row(
                "transfer", freq_GHz=_e(trl.freq_hz / 1e9), victim=trl.victim,
                aggressor=trl.aggressor, quantity="-Z_ab/(Z_aa+Z_load)",
                unit="", alternative=alt.name,
                value_re=_e(complex(trl.loaded).real),
                value_im=_e(complex(trl.loaded).imag),
                delta_dB=_e(trl.loaded_db),
                extra=f"z_load={trl.z_load}"))
        _print_table(["victim load", "-Z_ab/(Z_aa+Z_load)", "dB"], lrows2,
                     ["<", ">", ">"])

    _attr_print_caveats()

    if args.attribute_csv:
        _write_attrib_csv(args.attribute_csv, ts, args, csv_rows, header_notes)
        print(f"\nWrote attribution CSV: {args.attribute_csv}")
    return 0


def _attr_print_whatifs(ctx, a: int, b: int, alts, user_supplied_alts: bool,
                        modelled: bool, omega: float, decM,
                        csv_rows: list[dict]) -> None:
    """
    Sections 4 to 7: every what-if, measured against `ctx`.

    Split out because these four are the only ones that need an element to
    change.  Sections 8 and 9 -- the cross-frequency ranking and the exact
    transfer ratio -- are about the PAIR, so an all-open spec still gets them,
    and returning early from the report would have withheld the one thing such
    a spec can still be told.

    `ctx` is the GROUND-MODELLED context when there is one: a what-if measured
    against the declared ideal grounds would answer a question about a network
    the user has just said is not theirs.
    """
    # -- 4. sensitivity ranking
    if ctx.n_elements:
     _attr_section(
        "4. Sensitivity: every element against every candidate",
        "Every row is a full re-solve of the network with that one element "
        "replaced, so a 60 dB change is reported as 60 dB and not as a "
        "first-order slope that stopped being true two decades ago."
        + (" The baseline is the GROUND-MODELLED network of section 3b, not "
           "the declared one." if modelled else ""))
    if not user_supplied_alts:
        for line in _attr_wrap(
                "No --attribute-alt was given, so the scan is limited to the "
                "two STRUCTURAL candidates: 'open' (the element is not there) "
                "and 'ideal' (a perfect short to the reference). Those two "
                "need no judgement about your package. For a real candidate "
                "-- a ball's lead inductance, a 50 ohm terminator -- supply "
                "it yourself: --attribute-alt L=0.3n --attribute-alt R=50. "
                "This tool will not guess."):
            print(line)
        print()
    srows = attrib.sensitivity(ctx, a, b, alts, "M")
    _attr_print_sensitivity(srows, csv_rows, decM)

    print("\n  Leave-one-out, starting from ALL elements ideal "
          "(the number that moves is the one carrying something):")
    loo = attrib.leave_one_out(ctx, a, b, "M")
    lrows = []
    for r in sorted(loo, key=lambda r: -r.abs_delta):
        lrows.append([r.label, format_si(r.baseline_value.real, r.unit),
                      format_si(r.new_value.real, r.unit),
                      format_si(r.delta.real, r.unit), _fmt_db(r.delta_db)])
        csv_rows.append(_attr_row(
            "leave_one_out", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=r.quantity, unit=r.unit, element=r.label,
            alternative=r.alternative,
            value_re=_e(r.new_value.real), value_im=_e(r.new_value.imag),
            delta_re=_e(r.delta.real), delta_im=_e(r.delta.imag),
            delta_dB=_e(r.delta_db)))
    _print_table(["element", "M (all ideal)", "M without it", "Δ", "Δ (dB)"],
                 lrows[:ATTR_RANK_ROWS], ["<", ">", ">", ">", ">"])

    # -- 5. group joint effects and non-additivity
    primary = alts[0]
    _attr_section(
        f"5. Joint effects: a whole group changed at once to '{primary.name}'",
        "This is the case per-element numbers cannot see. With 60 ground "
        "balls every single-ball delta is about zero -- the other 59 already "
        "carry the return -- and so is every pairwise second difference: the "
        "effect is order-60, not order-2. 'non-additivity' is the joint "
        "change minus the sum of the one-at-a-time changes.")
    grs = []
    for label in ctx.groups:
        try:
            grs.append(attrib.group_joint(ctx, a, b, label, primary, "M"))
        except attrib.AttribError as e:                      # pragma: no cover
            print(f"  ({label}: {e})")
    grs.sort(key=lambda g: -abs(g.joint_delta))
    grows = []
    for g in grs[:ATTR_GROUP_ROWS]:
        grows.append([g.label, str(len(g.elements)),
                      format_si(g.joint_value.real, g.unit),
                      format_si(g.joint_delta.real, g.unit),
                      format_si(g.sum_individual.real, g.unit),
                      format_si(g.non_additivity.real, g.unit)])
    for g in grs:
        csv_rows.append(_attr_row(
            "group_joint", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=g.quantity, unit=g.unit, group=g.label,
            alternative=g.alternative,
            value_re=_e(g.joint_value.real), value_im=_e(g.joint_value.imag),
            delta_re=_e(g.joint_delta.real), delta_im=_e(g.joint_delta.imag),
            extra=f"n={len(g.elements)};sum_individual={g.sum_individual};"
                  f"non_additivity={g.non_additivity}"))
    _print_table(["group", "n", "M joint", "Δ joint", "Σ Δ individual",
                  "non-additivity"], grows, ["<", ">", ">", ">", ">", ">"])
    if len(grs) > ATTR_GROUP_ROWS:
        print(f"  ... {len(grs) - ATTR_GROUP_ROWS} more groups "
              "(all in --attribute-csv)")

    # Pairwise non-additivity, over the strongest few elements only: the scan
    # is O(k^2) full re-solves, so this cap is computational and applies to the
    # CSV as well -- unlike the display cap on the ranking above.
    pool = [r.elements[0] for r in
            sorted([r for r in srows if r.alternative == primary.name],
                   key=lambda r: -r.abs_delta)][:ATTR_PAIR_POOL]
    seen: list[int] = []
    for e in pool:
        if e not in seen:
            seen.append(e)
    pool = seen
    if len(pool) >= 2:
        print(f"\n  Pairwise non-additivity over the {len(pool)} strongest "
              f"elements ({len(pool) * (len(pool) - 1) // 2} pairs; this cap "
              "is computational and applies to the CSV too):")
        prs = []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                pr = attrib.group_joint(ctx, a, b, [pool[i], pool[j]],
                                        primary, "M")
                prs.append(pr)
                csv_rows.append(_attr_row(
                    "pair_joint", freq_GHz=_e(decM.freq_hz / 1e9),
                    victim=decM.victim, aggressor=decM.aggressor,
                    quantity=pr.quantity, unit=pr.unit, group=pr.label,
                    alternative=pr.alternative,
                    value_re=_e(pr.joint_value.real),
                    value_im=_e(pr.joint_value.imag),
                    delta_re=_e(pr.joint_delta.real),
                    delta_im=_e(pr.joint_delta.imag),
                    extra=f"sum_individual={pr.sum_individual};"
                          f"non_additivity={pr.non_additivity}"))
        prs.sort(key=lambda p: -abs(p.non_additivity))
        _print_table(
            ["pair", "Δ joint", "Σ Δ individual", "non-additivity"],
            [[p.label, format_si(p.joint_delta.real, p.unit),
              format_si(p.sum_individual.real, p.unit),
              format_si(p.non_additivity.real, p.unit)]
             for p in prs[:ATTR_RANK_ROWS]],
            ["<", ">", ">", ">"])

    # -- 6. cumulative curve
    _attr_section(
        f"6. Cumulative: change the top k elements TOGETHER to "
        f"'{primary.name}'",
        "Greedy by the one-at-a-time ranking. The point is the "
        "order-of-the-group effect that per-element numbers hide, not an "
        "optimal subset -- that is combinatorial and this is not it.")
    cc = attrib.cumulative_curve(ctx, a, b, primary, "M")
    crows = []
    for k, v, dv, sv, na in zip(cc.k, cc.values, cc.deltas,
                                cc.sum_individual, cc.non_additivity):
        crows.append([str(k), format_si(v.real, cc.unit),
                      format_si(dv.real, cc.unit),
                      format_si(sv.real, cc.unit),
                      format_si(na.real, cc.unit)])
        csv_rows.append(_attr_row(
            "cumulative", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=cc.quantity, unit=cc.unit, alternative=cc.alternative,
            value_re=_e(v.real), value_im=_e(v.imag),
            delta_re=_e(dv.real), delta_im=_e(dv.imag),
            extra=f"k={k};sum_individual={sv};non_additivity={na}"))
    print(f"  baseline M = {format_si(cc.baseline_value.real, cc.unit)}   "
          f"order: "
          + ", ".join(ctx.elements[i].describe() for i in cc.order[:6])
          + (" ..." if len(cc.order) > 6 else ""))
    _print_table(["k", "M with top k changed", "Δ", "Σ Δ individual",
                  "non-additivity"], crows, [">", ">", ">", ">", ">"])

    # -- 7. the Mobius interval
    _attr_section(
        "7. Series-inductance sweep of each group, in closed form",
        "Z_ab as a function of one termination impedance is a Mobius map, so "
        "both endpoints and the whole interval between them are analytic -- "
        "there is no loop and no sampling here. The interval is the headline: "
        "'M lies in [x, y] over any ground inductance' is a statement a "
        "budget can be written against, in a way that one number at one "
        "guessed L is not.")
    l_max = None
    for alt in alts:
        if alt.z is None or omega == 0.0:
            continue
        li = complex(alt.z).imag / omega
        if li > 0 and math.isfinite(li):
            l_max = li if l_max is None else max(l_max, li)
    sweep_labels = [g.label for g in grs[:ATTR_SWEEP_GROUPS]] or \
        list(ctx.groups)[:ATTR_SWEEP_GROUPS]
    for label in sweep_labels:
        try:
            # np.errstate for the same reason core wraps its open-probe divide:
            # a RuntimeWarning on fd 2 names a line of pkg_rlc_attrib and reads
            # as a crash, and a double-clicked GUI has no fd 2 at all.  The
            # sweep no longer expands its rational function -- it evaluates
            # from the partial fractions, so a 38-ball group is as finite as a
            # 2-ball one -- but a near-pole still divides by something very
            # small and the guard costs nothing.
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                sw = attrib.sweep_mobius(ctx, a, b, label, "M", "L")
        except attrib.AttribError as e:                      # pragma: no cover
            print(f"  ({label}: {e})")
            continue
        # No extra quotes around the label: in --attribute-group name it
        # already carries its own ("name 'c2_n*'") and nesting them reads as
        # a typo.
        print(f"\n  group {label} ({len(sw.elements)} element(s)), every "
              "member tied to the same L:")
        print(f"    M(ideal, L=0)      = "
              f"{format_si(sw.value_ideal.real, sw.unit, 4)}")
        print(f"    M(open,  L=inf)    = "
              f"{format_si(sw.value_open.real, sw.unit, 4)}")
        # 4 significant digits here, 3 everywhere else: the interval is the
        # headline scalar requirement 10 asks for, and on a well-grounded part
        # the whole span sits inside the third digit -- "[801 pH, 801 pH]"
        # reads as "the lead inductance does not matter" when the measured
        # span is 47 fH.
        print(f"    M over L in [0, inf) lies in "
              f"[{format_si(sw.interval[0], sw.unit, 4)}, "
              f"{format_si(sw.interval[1], sw.unit, 4)}]"
              f"   (min at L = {format_si(sw.arg_min, sw.param_unit)}, "
              f"max at L = {format_si(sw.arg_max, sw.param_unit)})")
        if not (math.isfinite(sw.value_ideal.real)
                and math.isfinite(sw.value_open.real)):      # pragma: no cover
            # This used to fire on any group past ~34 elements: the sweep
            # expanded its degree-|S| rational function and the product of |S|
            # eigenvalues of order 1e-9 underflowed, so BOTH endpoints came
            # back NaN while the interval still printed a confident span.
            # Measured on the synthetic 40-port part in test_attrib_cli, the
            # same 38-ball group now reports M(ideal) = -3.386 nH and
            # M(open) = -286.7 pH, because the partial-fraction form has no
            # product to underflow.  The branch stays as a backstop -- a NaN in
            # the data still reaches here -- but it is no longer the normal
            # fate of a large group.
            for line in _attr_wrap(
                    "WARN: an endpoint of this sweep is UNDEFINED, so the "
                    "interval above is taken over the parameter values that "
                    "stayed finite: read it as a lower bound on the span, not "
                    "as a bound on M.", "    ", hang="      "):
                print(line)
        csv_rows.append(_attr_row(
            "sweep", freq_GHz=_e(decM.freq_hz / 1e9), victim=decM.victim,
            aggressor=decM.aggressor, quantity=sw.quantity, unit=sw.unit,
            group=label, alternative="L in [0, inf)",
            value_re=_e(sw.interval[0]), value_im=_e(sw.interval[1]),
            extra=f"ideal={sw.value_ideal.real:.6e};"
                  f"open={sw.value_open.real:.6e};"
                  f"arg_min={sw.arg_min:.6e};arg_max={sw.arg_max:.6e};"
                  f"leaves_bracket={sw.leaves_bracket}"))
        if l_max is not None:
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                swb = attrib.sweep_mobius(ctx, a, b, label, "M", "L",
                                          t_max=l_max)
            print(f"    M over L in [0, {format_si(l_max, 'H')}] lies in "
                  f"[{format_si(swb.interval[0], swb.unit, 4)}, "
                  f"{format_si(swb.interval[1], swb.unit, 4)}]"
                  f"   <- bounded by the largest inductance YOU named in "
                  "--attribute-alt")
            csv_rows.append(_attr_row(
                "sweep", freq_GHz=_e(decM.freq_hz / 1e9), victim=decM.victim,
                aggressor=decM.aggressor, quantity=swb.quantity,
                unit=swb.unit, group=label,
                alternative=f"L in [0, {l_max:.6e}]",
                value_re=_e(swb.interval[0]), value_im=_e(swb.interval[1]),
                extra=f"arg_min={swb.arg_min:.6e};arg_max={swb.arg_max:.6e};"
                      f"leaves_bracket={swb.leaves_bracket}"))
        for n in sw.notes:
            for line in _attr_wrap("note: " + n, "    ", hang="      "):
                print(line)
    if len(grs) > len(sweep_labels):
        print(f"\n  ... {len(grs) - len(sweep_labels)} more group(s) not "
              "swept (cap: the strongest "
              f"{ATTR_SWEEP_GROUPS}). Narrow the spec, or read "
              "--attribute-csv.")


def _attr_print_ground_model(ctx, a: int, b: int, gm_label: str,
                             decM_ref, csv_rows: list[dict]) -> None:
    """
    What the declared ideal grounds are hiding (requirement 2).

    A ground model is a WHAT-IF, and it is placed here rather than folded into
    section 1 for a reason that is not cosmetic: it describes a network
    `compute_z_matrix` cannot be handed.  The dense case is not expressible as
    a TerminationSet at all -- a shared return is a mutual impedance between
    ground leads and the DSL has no node to hang one on -- so there is no
    second OPINION on the modelled total, only on the arithmetic that produced
    it.  `Decomposition` reconciles the DECLARED configuration against the
    engine whatever `zt` is in force, which is what makes that distinction
    real: the machinery is checked, the model's answer is this module's alone,
    and `reference_applicable` is False to say so.
    """
    _attr_section(
        f"3b. Ground model '{gm_label}': what the declared grounds are hiding",
        "The declared network is the one every other section reconciles "
        "against. This one is a what-if about the leads themselves, and no "
        "second opinion on it exists: a dense element-impedance matrix cannot "
        "be written as a TerminationSet, so compute_z_matrix cannot be asked "
        "about this network at all. What IS checked is the arithmetic -- the "
        "reconciliation in section 2 is of the declared configuration through "
        "this same machinery. Sections 4 to 7 below are measured against THIS "
        "baseline.")
    m_ref = complex(decM_ref.total_reference).real
    om = ctx.omega
    m_mod = float(np.imag(ctx.Zop[a, b])) / om if om else float("nan")
    # 4 significant digits, not the usual 3: 'diag:L=1n' moves M on a 2-ball
    # fixture from 1.010 nH to 1.012 nH, and at 3 digits both read "1.01 nH",
    # i.e. the section would print the dB of a difference it had just hidden.
    print(f"  M as declared          = {format_si(m_ref, 'H', 4)}")
    print(f"  M under '{gm_label}'".ljust(25)
          + f"= {format_si(m_mod, 'H', 4)}")
    if m_ref != 0.0 and math.isfinite(m_mod) and m_mod != 0.0:
        print(f"  difference             = "
              f"{20 * math.log10(abs(m_mod / m_ref)):.3g} dB")
    for line in _attr_wrap(
            "Independent leads understate the effective common-mode return "
            "inductance by (1 + (n-1)k), so 'diag' and 'shared' are not a "
            "refinement of each other -- they are different answers. Compare "
            "them by running the same command twice with "
            "--attribute-ground-model diag:SPEC and shared:SPEC."):
        print(line)
    csv_rows.append(_attr_row(
        "ground_model", freq_GHz=_e(ctx.freq_hz / 1e9),
        victim=ctx.port_names[a], aggressor=ctx.port_names[b],
        quantity="M", unit="H", alternative=gm_label,
        value_re=_e(m_mod),
        extra=f"declared={_e(m_ref)};note=no reference: not expressible as a "
              f"TerminationSet"))

    # The split UNDER the model.  This used to be a hole: the engine compared
    # its own sum against compute_z_matrix's value for the DECLARED spec, so a
    # shared return -- which doubles M, i.e. moves the answer by 100% -- read
    # as a catastrophic algorithm disagreement and the split vanished at
    # exactly the setting the section exists for.  The reconciliation is now of
    # the declared configuration whatever model is in force, so the split
    # survives; the fallback below stays for the cases that really are broken
    # (an ill-conditioned baseline does not stop being ill-conditioned because
    # a model was applied).
    try:
        dz = attrib.decompose(ctx, a, b, "Z")
        dm = attrib.decompose(ctx, a, b, "M")
    except attrib.AttribError as e:                          # pragma: no cover
        print(f"  (no split under this model: {e})")
        return
    print()
    if dz.terms:
        _attr_print_terms(dz, dm, csv_rows)
    else:                                                    # pragma: no cover
        for line in _attr_wrap(
                "The per-element split under this model is not available: the "
                "declared configuration does not reconcile against "
                f"compute_z_matrix ({100 * dz.residual_rel:.1f}% relative), so "
                "the arithmetic this model's totals came out of is not trusted "
                "either. That is a property of the file and the spec, not of "
                "the model. The totals above are this module's own."):
            print(line)


def _attr_print_stability(ts, Y, term, src, a, b, f_primary, decZ0,
                          extra_freqs, csv_rows: list[dict]) -> None:
    """
    The same ranking at several frequencies, side by side.

    --freq is always the first column, so the ranking every other section of
    this report is built from is the one being checked.  Duplicates are dropped
    AFTER snapping to the file's grid: two requested frequencies inside one
    sweep step are one column, and pretending otherwise would show a ranking
    "confirmed" against itself.
    """
    freqs = [f_primary]
    for f in extra_freqs:
        snapped = _attr_snap(ts.freqs, f)
        if snapped not in freqs:
            freqs.append(snapped)

    # element description -> [rank per column]; the description is the key
    # because a lumped element whose admittance vanishes at one frequency is
    # dropped there, so the element LISTS can legitimately differ.
    ranks: list[dict[str, int]] = []
    totals: list[complex] = []
    for f in freqs:
        if f == f_primary:
            dec = decZ0
        else:
            dec = attrib.decompose(
                attrib.build_context(Y, ts.freqs, term, f, sources=src),
                a, b, "Z")
        totals.append(dec.total_reference)
        order = sorted([t for t in dec.terms if t.element is not None],
                       key=lambda t: -abs(t.contribution))
        col = {t.label: i + 1 for i, t in enumerate(order)}
        ranks.append(col)
        for label, r in col.items():
            csv_rows.append(_attr_row(
                "rank", freq_GHz=_e(f / 1e9), victim=dec.victim,
                aggressor=dec.aggressor, quantity="Z", unit="Ohm",
                element=label, value_re=_e(r), extra="rank"))

    print("  Z_ab per frequency: " + "   ".join(
        f"{format_freq(f)} {_fmt_complex(z, 3)} Ω" for f, z in zip(freqs, totals)))
    if len(freqs) == 1:
        for line in _attr_wrap(
                "Only one frequency was evaluated. Pass "
                "--attribute-freqs 1,5,10 to re-rank across the band; a "
                "ranking read off one frequency is a statement about that "
                "frequency only."):
            print(line)
        return

    labels: list[str] = []
    for col in ranks:
        for lab in col:
            if lab not in labels:
                labels.append(lab)
    if not labels:
        # Every column's split was withheld, so there is nothing to rank.  An
        # empty table under a "rank is stable" verdict would be a claim about
        # a comparison that never happened.
        print("  No ranking is available at any of these frequencies -- the "
              "per-element split was withheld (see the reconciliation above).")
        return
    labels.sort(key=lambda l: ranks[0].get(l, 10 ** 6))
    rows = [[lab] + [(str(col[lab]) if lab in col else "--") for col in ranks]
            for lab in labels]
    _print_table(["element"] + [format_freq(f) for f in freqs], rows,
                 ["<"] + [">"] * len(freqs))
    moved = [lab for lab in labels
             if len({col.get(lab) for col in ranks}) > 1]
    if moved:
        for line in _attr_wrap(
                "RANK IS NOT STABLE: "
                + ", ".join(f"'{m}'" for m in moved[:6])
                + (" ..." if len(moved) > 6 else "")
                + " change places across the band, so a ranking read off one "
                  "frequency is a statement about that frequency only."):
            print(line)
    else:
        print("  Rank is stable across every frequency evaluated.")


def _attr_print_caveats() -> None:
    print("\n" + _ATTR_RULE)
    print("Caveats -- three things this report cannot do")
    print(_ATTR_RULE)
    for text in (
        "BLIND TO OPEN PORTS. A port you did not name in --gnd or --short "
        "contributes no element and therefore no term, so the table above "
        "ranks the DECLARATIONS in your spec, not the ports of your package. "
        "To ask about a port you have not decided on, declare it and read its "
        "'open' row in the sensitivity scan: that row is exactly 'what if "
        "this port were not connected'.",
        "THE SPLIT DEPENDS ON HOW THE SPEC IS SPELLED, not only on the "
        "network it describes. '--gnd 6:1:14' (9 ground elements) and "
        "'--gnd 6 --short 6-7,6-8,...' (1 ground and 8 shorts) are the same "
        "network, give the same total, and decompose differently. Both are "
        "right; they answer different questions.",
        "IT CANNOT EVALUATE NEW METAL. Every what-if here changes a "
        "TERMINATION on a port the S-parameter file already has. Moving a "
        "trace, adding a shield, or adding a ball that is not already a port "
        "is a different EM solve, and nothing in this report predicts it.",
    ):
        for line in _attr_wrap("* " + text, "  ", hang="    "):
            print(line)
        print()


def _run_cli(args: argparse.Namespace) -> int:
    if not args.file:
        print("ERROR: --cli requires a file argument", file=sys.stderr)
        return 2

    # Purely a command-line consistency check, so it runs BEFORE the file is
    # read: a typo in the flag set should not need a loadable Touchstone file
    # to be reported.  Same shape as the --mport / --porta rejections below.
    dependents = _attr_dependent_flags(args)
    if dependents and not args.attribute:
        print("ERROR: " + ", ".join(dependents)
              + (" only means" if len(dependents) == 1 else " only mean")
              + " something with --attribute VICTIM,AGGRESSOR "
                "(e.g. --attribute vic,agg)", file=sys.stderr)
        return 2

    try:
        ts = parse_touchstone(args.file, force_nports=args.force_nports,
                              lenient=args.lenient)
    except TouchstoneParseError as e:
        # The report already names a line, a verdict and a next step; printing
        # it beats the bare traceback this used to raise, which told nobody
        # whether the file or this tool was at fault.
        print(str(e), file=sys.stderr)
        return 1
    print(f"Loaded {ts.source_path}")
    for line in ts.summary_lines()[1:]:
        print(line)
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
        if args.attribute:
            print("ERROR: --attribute is only valid with --mode coupling: it "
                  "names a VICTIM and an AGGRESSOR measurement port, and "
                  "those exist only where --mport defines them (e.g. --mode "
                  "coupling --mport \"vic = 1\" --mport \"agg = 2\" "
                  "--attribute vic,agg)", file=sys.stderr)
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

        if args.attribute:
            # After the coupling report on purpose: the attribution explains
            # the M and k printed above, so it has to come after the numbers
            # it is explaining rather than before them.
            rc = _run_attribution(args, ts, Y, term, specs, g, sp, names)
            if rc:
                return rc

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

    if args.diagnose:
        # Deliberately not gated on --cli: someone whose file will not open
        # reaches for this first, and making them also remember --cli is a
        # pointless second failure.
        if not args.file:
            print("ERROR: --diagnose needs a file argument", file=sys.stderr)
            return 2
        kind, report = check_touchstone(args.file,
                                        force_nports=args.force_nports)
        print(report)
        return 0 if kind == FAULT_NONE else 1

    if args.cli:
        return _run_cli(args)

    # Launch GUI
    from pkg_rlc_gui import App
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
