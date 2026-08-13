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

    # WHICH of the other 149 ports matter at all -- the question that comes
    #   BEFORE --attribute, when the only two ports you are sure of are the
    #   victim and the aggressor.  Declare nothing: --cold-start starts from
    #   all-open on purpose and says so.
    python pkg_rlc_extractor.py --cli pkg.s153p --mode coupling \\
        --mport "dco = 1" --mport "rx = 2" --freq 5.0 --cold-start dco,rx
    python pkg_rlc_extractor.py --cli pkg.s153p --mode coupling \\
        --mport "dco = 1" --mport "rx = 2" --freq 5.0 --cold-start dco,rx \\
        --cold-start-top 12 --cold-start-cumulative 20 \\
        --cold-start-csv coldstart.csv

    # SEVERAL FILES AS ONE NETWORK -- an EM block hung on a package block.
    #   Every port then carries its file's tag ('PKG.12'), the cross-file
    #   links are ordinary shorts / lumped elements, and the reference-node
    #   self-check runs whether you ask for it or not.
    python pkg_rlc_extractor.py --cli coil.s2p --compose-alias EM \\
        --compose "PKG=package.s3p" \\
        --compose-link "EM.2 short_to PKG.1" \\
        --compose-link "EM.1 lumped_between PKG.3 L=0.3n" \\
        --gnd "PKG.2" --mode gnd --porta "EM.1" --freq 5

    # the same, with the package pre-reduced to the ports the spec uses
    #   (its ground balls go in --compose-gnd, NOT --gnd: they are shorted to
    #   that file's own reference before the stack, which is what makes the
    #   reduction cheap and correct)
    python pkg_rlc_extractor.py --cli coil.s16p --compose "PKG=pkg.s300p" \\
        --compose-keep "PKG.10-12,40-42" --compose-gnd "PKG.100:1:153" \\
        --compose-link "F1.1,2,3 short_to PKG.10,11,12" \\
        --mode coupling --mport "dco = F1.1 / F1.2" --freq 5 \\
        --compose-export combined.s22p

    # a port correspondence PROPOSED from the two files' own port names.
    #   It prints and stops -- a proposal is not a commitment.  Review the CSV,
    #   edit it, then commit it with --compose-map.
    python pkg_rlc_extractor.py --cli die.s16p --compose "PKG=pkg.s60p" \\
        --compose-propose F1,PKG --compose-propose-csv map.csv
    python pkg_rlc_extractor.py --cli die.s16p --compose "PKG=pkg.s60p" \\
        --compose-map map.csv --mode gnd --porta "F1.1" --freq 5

    # WHICH PACKAGE PIN COSTS YOU THE dB -- attribution on the composition.
    #   The cross-file links go INTO the attribution baseline: without that,
    #   all-open leaves the files as disconnected islands and every
    #   package-only element reads EXACTLY 0 with a healthy residual.  The
    #   report names the gauge; there is no flag to turn it off.
    python pkg_rlc_extractor.py --cli coil.s2p --compose "PKG=package.s3p" \\
        --compose-link "F1.2 short_to PKG.1" --mode coupling \\
        --mport "vic = F1.1" --mport "agg = PKG.2" --freq 5 \\
        --attribute vic,agg --cold-start vic,agg

Note: --vdd is deprecated.  For AC small-signal analysis VDD is identical to
ground, so any --vdd ports are simply unioned into the --gnd list.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

import numpy as np

import pkg_rlc_attrib as attrib
import pkg_rlc_compose as comp
from pkg_rlc_core import (
    DEFAULT_Z0,
    FAULT_NONE,
    RECIPROCITY_WARN,
    TouchstoneParseError,
    build_terminations_coupling,
    check_touchstone,
    collapse_ports,
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
from pkg_rlc_csv import write_coupling_table
from pkg_rlc_report import (
    _format_z_matrix,
    _sign_flag,
)
# The attribution and cold-start REPORT.  Every one of these was defined in
# this file and moved out whole; the import is what keeps
# `pkg_rlc_extractor.X` resolving for the tests, for the Attribution window
# and for the three design notes that name these sections -- the same
# re-export the DSL helpers get in `pkg_rlc_gui`.  The `_attr_print_*` /
# `_cold_print_*` sections RETURN their lines now and `_emit` prints them.
from pkg_rlc_attrib_report import (
    ATTR_GROUP_ROWS,
    ATTR_PAIR_POOL,
    ATTR_RANK_ROWS,
    ATTR_SWEEP_GROUPS,
    COLD_MIRROR_ROWS,
    COLD_PAIR_ROWS,
    COLD_QUANTITY,
    COLD_RANK_ROWS,
    _ATTR_CSV_FIELDS,
    _ATTR_LINE,
    _ATTR_RULE,
    _COLD_CSV_FIELDS,
    _NAME_W,
    _attr_alternative,
    _attr_ground_model,
    _attr_print_caveats,
    _attr_print_context,
    _attr_print_ground_model,
    _attr_print_reconciliation,
    _attr_print_sensitivity,
    _attr_print_stability,
    _attr_print_terms,
    _attr_print_whatifs,
    _attr_row,
    _attr_section,
    _attr_series_impedance,
    _attr_snap,
    _attr_wrap,
    _attr_zt,
    _cold_abs,
    _cold_print_bracket,
    _cold_print_curve,
    _cold_print_families,
    _cold_print_header,
    _cold_print_pairs,
    _cold_print_screen,
    _cold_q,
    _cold_row,
    _e,
    _fmt_complex,
    _fmt_db,
    _fmt_num,
    _table_lines,
    _trunc,
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

    # ---- cold start (--mode coupling only) -------------------------------
    # Its own group for the same reason the attribution has one: every flag
    # here is inert without --cold-start, and _run_cli refuses them by name
    # when it is absent.  The two groups are deliberately shaped alike -- one
    # naming flag, some caps, a CSV -- because they are one family: --attribute
    # explains the number a spec produced, --cold-start asks which ports the
    # spec should have mentioned at all.
    n_pairs_default = (attrib.COLD_START_TOP_K
                       * (attrib.COLD_START_TOP_K - 1) // 2)
    cs_grp = p.add_argument_group(
        "cold start (--mode coupling only)",
        "WHICH ports matter, before a spec exists. Every step is exact (a "
        "closed form verified against a full re-solve to 1.5e-11), not a "
        "first-order slope, and every one of them starts from ALL-OPEN -- the "
        "configuration that produced the disputed number in the first place.")
    cs_grp.add_argument("--cold-start", default=None,
                        metavar="VICTIM,AGGRESSOR",
                        help="Run the four-step port screen for the mutual "
                             "impedance between these two measurement ports "
                             "instead of decomposing a spec: the "
                             "open..grounded BRACKET, a ranked screen of every "
                             "non-probe port with its coupling to BOTH sides, "
                             "a pair scan plus the mirror leave-one-out from "
                             "all-grounded, and the greedy cumulative curve. "
                             "Both sides are named exactly as --attribute "
                             "names them -- a measurement-port NAME from "
                             "--mport, or a 1-BASED position in that list. Any "
                             "--gnd / --short your spec declares is NOT in "
                             "force here, and the report names every one it "
                             "set aside. Measured cost at 151 candidates (a "
                             "153-port package): 9.5 s for the four steps, of "
                             "which 9.3 s is the mirror direction; at 38 "
                             "candidates the same four steps are 17.6 ms")
    cs_grp.add_argument("--cold-start-top", type=int, default=None,
                        metavar="K",
                        help=f"How many ports of the step-1 ranking enter the "
                             f"pair scan (default: "
                             f"{attrib.COLD_START_TOP_K}, i.e. "
                             f"{n_pairs_default} "
                             "pairs). The cap is about how much table a person "
                             "reads, not about cost: the scan is K*(K-1)/2 "
                             "rank-2 updates and measured 0.5 ms at K=8, "
                             "3.4 ms at K=20 and 8.2 ms at K=30 on a 153-port "
                             "file. Refused below 2 -- the pair scan is not "
                             "optional, see the shield measurement the report "
                             "prints")
    cs_grp.add_argument("--cold-start-cumulative", type=int, default=None,
                        metavar="K",
                        help=f"Depth of the greedy cumulative curve, which is "
                             f"step 3 and is ALWAYS run (default: "
                             f"{attrib.COLD_START_MAX_K}). It is not opt-in "
                             "because it is the only step that answers 'how "
                             "many ports actually matter', and at 151 "
                             "candidates it is 132 ms of a 9.5 s report. 0 "
                             "means every candidate, which is the one "
                             "expensive setting here: 54.9 s at 151 "
                             "candidates, against 132 ms at K=12 and 237 ms "
                             "at K=24")
    cs_grp.add_argument("--cold-start-csv", default=None, metavar="PATH",
                        help="Write every cold-start record to this CSV -- the "
                             "bracket, the full UNCAPPED screen (every "
                             "candidate port, with the COMPLEX coupling "
                             "columns the report shows as magnitudes), every "
                             "scanned pair whether flagged or not, the whole "
                             "mirror, the curve and the name-family "
                             "suggestions. One row per record, tagged by a "
                             "'section' column")

    # ---- composition (several files as one network) -----------------------
    # Its own group for the same reason the two above have one: every flag
    # here is inert without --compose and _run_cli refuses them by name when it
    # is absent.  The family shape is deliberately the same -- one parent flag,
    # some inputs, an export -- because this is the third member of it.
    cm_grp = p.add_argument_group(
        "composition (several files as ONE network)",
        "Hang an EM block on a package block and measure the assembled thing. "
        "The maths is the existing pipeline: each file goes S -> Y with its "
        "OWN reference impedance (Y does not depend on it -- measured "
        "1.049e-17 between z0 = 50 and z0 = 75), the blocks are stacked, and "
        "every cross-file link is an ordinary short or lumped element handed "
        "to the same compute_z_matrix as everything else. What is NOT free is "
        "the reference node: a block-diagonal stack welds the files' "
        "references together at zero impedance, so a ground network that "
        "exists only inside one file can be silently out of the circuit. That "
        "is why the reference-node check below is mandatory output. "
        "--attribute and --cold-start work here and put the cross-file links "
        "INTO their baseline, which is a deliberate gauge change the report "
        "names: without it all-open leaves the files as disconnected islands "
        "and every package-only element contributes EXACTLY 0 while the "
        "reconciliation residual still reads healthy.")
    cm_grp.add_argument("--compose", action="append", default=None,
                        metavar="[ALIAS=]FILE",
                        help="Another Touchstone file to compose with the "
                             "positional one; repeatable. The optional "
                             "'ALIAS=' prefix names the file everywhere it "
                             "appears -- in port references ('PKG.12'), in "
                             "the port map and in every warning. Without it "
                             "the files are F1, F2, ... , the same idiom the "
                             "GUI's results table already uses. A tag is "
                             "letters, digits and underscores, and it is what "
                             "you type in front of a port, so keep it short")
    cm_grp.add_argument("--compose-alias", default="", metavar="ALIAS",
                        help="Tag for the POSITIONAL file (default: F1). The "
                             "other files take theirs from --compose")
    cm_grp.add_argument("--compose-keep", action="append", default=None,
                        metavar="ALIAS.PORTS",
                        help="Pre-reduce one file to these ports before "
                             "stacking, e.g. 'PKG.10-12,40-42'; repeatable, "
                             "and the tag is REQUIRED (it says which file). "
                             "Kept ports keep their ORIGINAL numbers, because "
                             "renumbering a 60-port package to 1..6 is "
                             "exactly what makes an off-by-one in a mapping "
                             "invisible. The reduction is exact (a Schur "
                             "complement over ports nothing connects to) and "
                             "it pays on the SOLVE, not on a one-shot run. "
                             "Measured on this box, 16-port die + 120-port "
                             "package over 201 frequencies: the solve goes "
                             "3113 ms -> 14.4 ms, i.e. 216x, and the two "
                             "answers agree to 7.4e-16 -- but the reduction "
                             "itself costs 2.5 s, so ONE end-to-end run only "
                             "goes 7378 ms -> 6754 ms (1.09x). Use it when "
                             "you are going to solve the same network several "
                             "times, or write the reduced stack out once with "
                             "--compose-export and load that instead")
    cm_grp.add_argument("--compose-gnd", action="append", default=None,
                        metavar="ALIAS.PORTS",
                        help="Ports shorted to THAT FILE's own reference "
                             "before stacking (its ground balls), e.g. "
                             "'PKG.100:1:153'; repeatable, tag required. This "
                             "is NOT --gnd: --gnd is a termination applied to "
                             "the assembled network and keeps the port, this "
                             "one deletes the row and column during the "
                             "pre-reduction. Grounding is not the same as "
                             "eliminating open -- a package's ground balls "
                             "need this bucket or the reduced block is wrong. "
                             "Note that a file whose whole ground set is "
                             "folded in this way has no port left for the "
                             "reference-node check to perturb, and the report "
                             "says so")
    cm_grp.add_argument("--compose-link", action="append", default=None,
                        metavar="SPEC",
                        help="One cross-file (or same-file) connection; "
                             "repeatable. The spelling is the connection "
                             "table's own: '<ports> short_to <ports>' or "
                             "'<ports> lumped_between <ports> R=.. L=.. "
                             "C=..', with 'connect' accepted for either (it "
                             "is a short when no R/L/C follows and an element "
                             "when one does). Both sides take ranges and are "
                             "paired ELEMENTWISE -- 'EM.1,2,3 short_to "
                             "PKG.10,11,12' is three separate wires -- with a "
                             "single port on one side fanning out to all of "
                             "the other. A length mismatch is refused, not "
                             "guessed, and the end pairs are echoed either "
                             "way: an off-by-one in one file's numbering "
                             "shifts every pair and produces a plausible "
                             "wrong answer with nothing pointing at it")
    cm_grp.add_argument("--compose-map", default=None, metavar="PATH",
                        help="Read the connections from a CSV instead of "
                             "(or as well as) --compose-link. Columns: 'a' "
                             "and 'b' (or from/to, left/right) carry the two "
                             "port fields, an optional 'element' column "
                             "carries R/L/C for a lumped link and 'short' or "
                             "nothing for a wire, and an optional 'note' "
                             "column is ignored. Blank lines and lines "
                             "starting with '#' are skipped, so the file "
                             "--compose-propose-csv writes -- which comments "
                             "out the ports it could not match -- can be "
                             "edited and fed straight back")
    cm_grp.add_argument("--compose-propose", default=None, metavar="A,B",
                        help="PROPOSE a port correspondence between two of "
                             "the composed files from the port NAMES both "
                             "files already carry, print it with the "
                             "unmatched ports on BOTH sides, and STOP. "
                             "Nothing is connected and nothing is measured: a "
                             "proposal you review and accept is a reviewed "
                             "input, a proposal applied on your behalf is a "
                             "guess. Names are compared after trimming and "
                             "case folding and nothing else is inferred; "
                             "many-to-one is normal (54 ground balls onto one "
                             "die pad), but N-to-M with N and M both above 1 "
                             "has no defensible pairing and is listed as "
                             "ambiguous rather than paired")
    cm_grp.add_argument("--compose-propose-csv", default=None, metavar="PATH",
                        help="Write the proposal to this CSV in "
                             "--compose-map's format, with every unmatched "
                             "port present as a COMMENTED row so the file is "
                             "a work list rather than only an answer")
    cm_grp.add_argument("--compose-export", default=None, metavar="PATH",
                        help="Write the STACKED network out as a Touchstone "
                             "file: the files on one common frequency axis "
                             "sharing ONE reference node (the composition's "
                             "own weld), with the port names carrying their "
                             "file tags. The cross-file links are NOT in "
                             "it -- "
                             "a short merges two nodes and changes the port "
                             "count, so they stay in the termination set and "
                             "are applied at solve time; the report says so "
                             "and names them. That is the point of the export "
                             "rather than a hole in it: the links are wires "
                             "and two-terminal elements, so leaving them out "
                             "keeps them editable in a schematic, and reading "
                             "the file back here with the same --compose-link "
                             "is the independent round trip a feature with no "
                             "golden reference otherwise has no way to check. "
                             f"The declared reference impedance is "
                             f"{DEFAULT_Z0:g} ohm, and that costs nothing: Y "
                             "does not depend on it")
    cm_grp.add_argument("--compose-export-ports", default="", metavar="SPEC",
                        help="Bring only these ports out of --compose-export "
                             "(scoped, e.g. 'EM.1;PKG.40-42'); the rest are "
                             "Schur-eliminated open. Default: every port, "
                             "which on a 316-port composition is a file "
                             "nothing will open")
    return p


# ============================================================================
# Formatting helpers (coupling report)
# ============================================================================
#
# NONE OF THE ARITHMETIC BELOW IS THIS FILE'S ANY MORE.  Truncation, the
# plain-number format, the sign flag, the monospace table and the Z matrix
# were each written twice -- once here for the terminal and once in
# `pkg_rlc_report` for the results pane -- and two renderings of one number
# are two things that can come to disagree.  The CLI's own SPELLING of each of
# them (a '~' rather than U+2026, a rule under the header, 'a + jb' rather
# than 'a+bj') now lives in `pkg_rlc_attrib_report`, one import above, because
# the `_attr_print_*` / `_cold_print_*` sections that call them by name moved
# there whole.  What is left here is the two things that PRINT.


def _emit(lines) -> None:
    """Print what a `pkg_rlc_attrib_report` section returned.

    Every section of the attribution and cold-start reports RETURNS its lines
    now, so that `tests/fixtures/cli_reference/` can pin them with no stream
    to capture.  This is the one place that turns them back into stdout.
    """
    for line in lines:
        print(line)


def _print_table(headers: list[str], rows: list[list[str]],
                 aligns: list[str], indent: str = "  ") -> None:
    """`_table_lines`, printed.  The compose report's seven call sites."""
    _emit(_table_lines(headers, rows, aligns, indent))


def _print_section(title: str, subtitle: str = "") -> None:
    """`_attr_section`, printed.  The two report drivers' own call sites."""
    _emit(_attr_section(title, subtitle))


def _cli_z_name(n) -> str:
    """The Z matrix's row/column label on the terminal."""
    return _trunc(n, _NAME_W)


def _cli_z_cell(z) -> str:
    """One Z matrix entry on the terminal: 'a + jb', not the pane's 'a+bj'."""
    return _fmt_complex(complex(z))


def _print_coupling_report(res) -> None:
    """Print the Z matrix, the self table, every pair, and the reciprocity check."""
    # --- 1. The G x G Z matrix -------------------------------------------
    print(f"\n@ {res.freq_hz / 1e9:.4g} GHz  --  Z matrix (Ω), open-circuit: "
          "every other measurement port carries no current")
    print(_format_z_matrix(res.names, res.Z_matrix, indent="  ",
                           name=_cli_z_name, cell=_cli_z_cell))

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
            _sign_flag(p),
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


def _compose_csv_header(fh, net) -> None:
    """
    The port map, into an export.

    A composed CSV's provenance line carries the port fields the user typed,
    which are scoped ('PKG.40-42') and therefore meaningless without the map
    from a tag to a file.  A CSV outlives the terminal it was printed in, which
    is the same reason the attribution CSV carries its sign convention.
    """
    if net is None:
        return
    for line in net.port_map_lines():
        fh.write("# " + line + "\n")


def _write_coupling_csv(path: str, ts, args, Zmat: np.ndarray,
                        names: list[str], net=None) -> None:
    """Per frequency: Re/Im of every Z_ij, plus M_nH and k for every pair.

    The comment block is the CLI's -- it names the file, the port map and the
    spec that was run, none of which the GUI's export has to say because the
    session does.  The TABLE under it is `pkg_rlc_csv.write_coupling_table`,
    the same one the GUI writes: this file used to carry a second,
    independent implementation of it, under the same name.
    """
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        fh.write(f"# File: {ts.source_path}\n")
        _compose_csv_header(fh, net)
        fh.write("# Mode: coupling, mport=" + " | ".join(args.mport or [])
                 + f", gnd={args.gnd}, vdd={args.vdd}, short={args.short}\n")
        fh.write("# Z_ij in ohms (open-circuit). M_nH = Im(Z_ij)/omega, "
                 "k = M/sqrt(L_i*L_j) with L = Im(Z_ii)/omega.\n")
        fh.write("# All values keep their physical sign (Cadence convention); "
                 "k is nan where L_i <= 0 or L_j <= 0.\n")
        write_coupling_table(w, Zmat, names, ts.freqs)


# ============================================================================
# Attribution (--attribute):  where a mutual impedance comes from
# ============================================================================
#
# `pkg_rlc_attrib` does every piece of arithmetic below and
# `pkg_rlc_attrib_report` does every piece of rendering.  What is left in this
# section is argument parsing, the two CSV WRITERS (a path, the flags and the
# header comments -- the RECORDS are shaped in the report module) and the
# driver that puts the sections in order and prints them.  If a number here is
# not straight off one of those modules' dataclasses it is a bug, because a
# second implementation of the same quantity is exactly how the CLI and the
# GUI would start disagreeing about a 6 dB dispute.


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

def _attr_sources(group_mode: str, args: argparse.Namespace,
                  specs: list[str], gnd: list[int],
                  shorts: list[tuple[int, int]], port_names: list[str],
                  nports: int,
                  spec_labels: Sequence[str] | None = None,
                  port_tags: Sequence[str] | None = None,
                  link_sources: dict[int, str] | None = None,
                  ) -> tuple[dict[int, str], list[str]]:
    """
    1-based port -> the label of the group its declaration belongs to.

    This is the CLI's `row_sources`.  Core's version reads the GUI's connection
    table; here the "rows" are the flags the user typed, so a package's ground
    balls written as one `--gnd 6:1:14` are ONE group exactly as they would be
    one table row.  The walk order -- measurement ports, then --gnd, then
    --short -- mirrors both `rows_to_dsl_text` and
    `build_terminations_coupling`, and the LAST writer wins, because the DSL
    both of those agree with is last-assignment-wins.

    `specs` are PARSED and `spec_labels` are DISPLAYED, and on a composed
    network they are two different strings: the parsed form has been rewritten
    into global numbering ('vic = 17,18 / 19') because that is the only
    numbering `row` is keyed on, while the label has to stay the text the user
    typed ('vic = EM.1,2 / EM.3') or the group names an index nobody wrote.
    They are the same list on one file.
    """
    labels = list(spec_labels) if spec_labels is not None else list(specs)
    if len(labels) != len(specs):
        raise ValueError(
            f"{len(labels)} --mport label(s) for {len(specs)} spec(s)")
    notes: list[str] = []
    gnd_label = f"--gnd {args.gnd.strip()}" if args.gnd.strip() else "--gnd"
    if args.vdd.strip():
        gnd_label += f" (+ --vdd {args.vdd.strip()})"
    short_label = (f"--short {args.short.strip()}" if args.short.strip()
                   else "--short")

    row: dict[int, str] = {}
    for spec, shown in zip(specs, labels):
        _name, plus, minus = parse_mport_spec(spec)
        label = f'--mport "{shown.strip()}"'
        for p in list(plus) + list(minus):
            row[p] = label
    overlap = sorted(set(gnd) & {p for pair in shorts for p in pair})
    for p in gnd:
        row[p] = gnd_label
    for pa, pb in shorts:
        row[pa] = short_label
        row[pb] = short_label
    # The cross-file links go LAST, because that is the order they were added
    # to `term.couplings` and this map is last-assignment-wins.
    for p, lbl in (link_sources or {}).items():
        row[p] = lbl
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
        # `port_tags` on a composed network: 'EM.2', not 'port 2'.  A global
        # index names nothing anyone can act on across several files (R2-4),
        # and this is the one grouping whose label IS a port.
        src = {p: (port_tags[p - 1] if port_tags and p - 1 < len(port_tags)
                   else f"port {p}")
               for p in range(1, nports + 1)}
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
                     names: list[str],
                     baseline: "attrib.BaselineLinks | None" = None,
                     extra_notes: Sequence[str] = (),
                     spec_labels: Sequence[str] | None = None,
                     net: "comp.ComposedNetwork | None" = None,
                     link_sources: dict[int, str] | None = None) -> int:
    """
    The whole --attribute report.  Returns a process exit code.

    Everything that can be wrong about the COMMAND LINE is checked before any
    linear algebra runs, so a typo costs a message rather than a minute of
    Schur reductions on a 153-port file.

    `baseline` is the composed-network gauge (`_compose_baseline`) and is None
    on a single file, where there is nothing to gauge: one file's ports are one
    block, no declared link crosses a boundary, and `BaselineLinks.selects`
    would answer False for every element anyway.  Passing it unconditionally
    would therefore be harmless and is still not done -- the report HEADER
    changes when a policy is in force, and a single-file report must not grow a
    line about files it does not have.
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

    src, src_notes = _attr_sources(
        args.attribute_group, args, specs, gnd, shorts,
        _attr_family_names(net, ts), ts.nports,
        spec_labels=spec_labels,
        port_tags=_attr_port_tags(net),
        link_sources=link_sources)

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
        c0 = attrib.build_context(Y, ts.freqs, term, f_hz, sources=src,
                                  baseline=baseline)
        zt, zt_notes = _attr_zt(c0, gm_kind, gm_z)
        if zt is None:
            return c0, c0, zt_notes
        return c0, attrib.build_context(Y, ts.freqs, term, f_hz, zt=zt,
                                        sources=src,
                                        baseline=baseline), zt_notes

    try:
        ctx_ref, ctx, zt_notes = build(f_primary)
    except (attrib.AttribError, ValueError) as e:
        print(f"ERROR: --attribute: {e}", file=sys.stderr)
        return 2
    modelled = ctx is not ctx_ref

    csv_rows: list[dict] = []
    header_notes = (list(extra_notes) + list(src_notes) + list(zt_notes)
                    + notes_a + notes_b)
    if args.attribute_group == "flat":
        header_notes.append(
            "flat groups each element under its FIRST port, so a ground and a "
            "short that both start at the same port share a group.")

    # ---- 3. the report ---------------------------------------------------
    print("\n" + _ATTR_LINE)
    print(f"ATTRIBUTION of the mutual impedance:  victim '{names[a]}'  <-  "
          f"aggressor '{names[b]}'")
    print(_ATTR_LINE)
    _emit(_attr_print_context(ctx_ref, gm_label, args.attribute_group, header_notes))

    # -- requirement 11: the sign convention, before any signed number.
    _print_section("0. Sign convention")
    for line in _attr_wrap(attrib.SIGN_CONVENTION_TEXT):
        print(line)

    try:
        decZ = attrib.decompose(ctx_ref, a, b, "Z")
        decM = attrib.decompose(ctx_ref, a, b, "M")
    except attrib.AttribError as e:
        print(f"ERROR: --attribute: {e}", file=sys.stderr)
        return 2

    _print_section(
        "1. Decomposition of Z_ab, grouped by what declared each port",
        "The bare EM coupling -- what the file already has with every "
        "non-probe port OPEN -- plus one exact, signed term per declared "
        "element. The terms ADD UP; this is superposition, not a "
        "linearisation.")
    _emit(_attr_print_terms(decZ, decM, csv_rows))

    _print_section("2. Reconciliation against compute_z_matrix")
    _emit(_attr_print_reconciliation(decZ, decM))
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

    _print_section("3. Return-path budget")
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
        _emit(_attr_print_ground_model(ctx, a, b, gm_label, decM, csv_rows))

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
        _emit(_attr_print_whatifs(ctx, a, b, alts, user_supplied_alts,
                                  modelled, omega, decM, csv_rows))

    # -- 8. cross-frequency rank stability.  On the DECLARED context, so it
    # re-ranks exactly the table section 1 printed.
    _print_section("8. Cross-frequency rank stability",
                   "The same ranking, by |contribution to Z_ab|, re-run at "
                   "each frequency on the spec AS DECLARED. A ranking that "
                   "moves is a ranking that belongs to one frequency.")
    _emit(_attr_print_stability(ts, Y, term, src, a, b, f_primary, decZ,
                                extra_freqs, csv_rows))

    # -- 9. the exact current-transfer ratio
    _print_section("9. Exact current-transfer ratio (docs/theory.md 8.8)",
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

    _emit(_attr_print_caveats())

    if args.attribute_csv:
        _write_attrib_csv(args.attribute_csv, ts, args, csv_rows, header_notes)
        print(f"\nWrote attribution CSV: {args.attribute_csv}")
    return 0


# ============================================================================
# Cold start (--cold-start):  WHICH ports matter, before a spec exists
# ============================================================================
#
# Same rule as the attribution above, and for the same reason: `pkg_rlc_attrib`
# does every piece of arithmetic, `pkg_rlc_attrib_report` every piece of
# rendering, and what is in this section is argument parsing, the CSV writer
# and the driver.  If a number here is not straight off one of those modules'
# dataclasses it is a bug.
#
# The two reports are one FAMILY -- one naming flag, some caps, a CSV with a
# 'section' column, refusals that name the offending token -- but they answer
# different questions from DIFFERENT BASELINES.  --attribute decomposes the
# network the spec declares; --cold-start rewrites that spec to all-open,
# because "which of these 149 ports matter" is a question about ports the spec
# has not mentioned yet.  Every header below says which baseline is in force,
# and the engine names every declaration it set aside.

def _cold_dependent_flags(args: argparse.Namespace) -> list[str]:
    """
    The --cold-start-* flags the user set.  Inert without --cold-start.

    All three default to None rather than to their real defaults precisely so
    this check can be exact: `--cold-start-top 8` is indistinguishable from not
    passing it once the default has been substituted, and a user who spells the
    default out loud and forgets --cold-start deserves the same message as one
    who does not.  (`_attr_dependent_flags` compares against the default value
    instead and cannot make that distinction; it is not worth changing there,
    but it is worth not repeating.)
    """
    given: list[str] = []
    if args.cold_start_top is not None:
        given.append("--cold-start-top")
    if args.cold_start_cumulative is not None:
        given.append("--cold-start-cumulative")
    if args.cold_start_csv:
        given.append("--cold-start-csv")
    return given


def _cold_cap(value: int | None, default: int, flag: str, minimum: int,
              why: str) -> int:
    """A cap flag: its default when unset, itself when sane, a refusal below."""
    if value is None:
        return default
    if value < minimum:
        raise ValueError(f"{flag} {value}: {why}")
    return value


def _write_cold_csv(path: str, ts, args, cs, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# File: {ts.source_path}\n")
        fh.write(f"# Cold start: {args.cold_start}, mport="
                 + " | ".join(args.mport or [])
                 + f", freq={args.freq} GHz\n")
        fh.write("# Baseline: " + cs.bracket.baseline_note + "\n")
        fh.write("# The spec's own --gnd / --short are NOT in force here; see "
                 "the 'note' rows and the report.\n")
        # value / delta mean different things per section, so the file says so
        # rather than leaving a reader to infer it from a column name that is
        # right in five sections out of six.
        fh.write("# Sections: bracket (value=open end, delta=grounded-open, "
                 "delta_dB=the span), screen (one row per candidate, UNCAPPED; "
                 "value=quantity with that port grounded, delta=change from "
                 "the baseline, z_*=the COMPLEX coupling columns the report "
                 "prints as magnitudes), pair (every scanned pair; "
                 "value=change with BOTH grounded, delta=non-additivity), "
                 "mirror (leave-one-out from all-grounded, UNCAPPED), "
                 "cumulative (the greedy curve, one row per k), family "
                 "(name-family suggestions, which changed no number above; "
                 "port_name is the name PREFIX and extra carries the member "
                 "ports).\n")
        # Requirement 11's rule, and the same one the attribution CSV follows:
        # the sign convention, the bracket's honesty clause and the blind spot
        # travel with EVERY export, because a CSV outlives the terminal it was
        # printed in.
        for text in (attrib.SIGN_CONVENTION_TEXT, cs.bracket.caveat,
                     cs.blind_spot):
            for line in textwrap.wrap(text, 100):
                fh.write("# " + line + "\n")
        w = csv.DictWriter(fh, fieldnames=_COLD_CSV_FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _run_cold_start(args: argparse.Namespace, ts, Y: np.ndarray, term,
                    names: list[str],
                    baseline: "attrib.BaselineLinks | None" = None,
                    extra_notes: Sequence[str] = (),
                    net: "comp.ComposedNetwork | None" = None) -> int:
    """
    The whole --cold-start report.  Returns a process exit code.

    Everything that can be wrong about the COMMAND LINE is checked before any
    linear algebra runs, the same rule `_run_attribution` follows: a typo
    should cost a message, not 9.5 s of Woodbury updates on a 153-port file.

    `baseline` is the composed-network gauge and matters MORE here than it does
    for --attribute, because this report rewrites the spec: it keeps the
    probes, drops every other declaration and grounds candidates one at a time.
    Without the policy the cross-file links are dropped along with everything
    else, so the screen measures a network in which the far file is not
    attached -- measured on the 12-port construction in
    tests/test_attrib_composed.py, ALL SIX package ports come back with delta
    exactly 0.0 and `defined = True`, i.e. a screen confidently reporting that
    the package cannot matter.  With it, the links are STRUCTURE and survive
    the rewrite.
    """
    # ---- 1. the command line --------------------------------------------
    try:
        vic_tok, agg_tok = _attr_parse_pair(args.cold_start)
        a, notes_a = _attr_resolve_port(vic_tok, names)
        b, notes_b = _attr_resolve_port(agg_tok, names)
    except ValueError as e:
        print(f"ERROR: --cold-start: {e}", file=sys.stderr)
        return 2
    if a == b:
        # The engine allows victim == aggressor (its own fixture walk screens
        # self terms on 2-port files), and it is refused here anyway: with
        # a == b the quantity called M is L_a, so the whole page would be
        # headed 'M' for a self inductance, and --attribute -- the other half
        # of this flag family -- already refuses the same pair by name. Two
        # flags in one family disagreeing about what a legal victim/aggressor
        # pair is would be worse than the missing capability.
        print(f"ERROR: --cold-start: victim and aggressor are the same "
              f"measurement port '{names[a]}'. This screen ranks ports by what "
              f"they do to a MUTUAL impedance -- name two different --mport "
              f"entries.", file=sys.stderr)
        return 2
    try:
        top_k = _cold_cap(
            args.cold_start_top, attrib.COLD_START_TOP_K, "--cold-start-top",
            2, "a pair scan needs at least two ports. The step is not "
               "optional: a shield brought out as two ports reads +9.689 pH "
               "for either end alone and -870.268 pH for both, 90x the "
               "largest single-port effect with the opposite sign")
        max_k = _cold_cap(
            args.cold_start_cumulative, attrib.COLD_START_MAX_K,
            "--cold-start-cumulative", 0,
            "the greedy curve's depth cannot be negative. 0 means every "
            "candidate port, which is the deepest setting there is (measured: "
            "54.9 s at 151 candidates, against 132 ms at the default 12)")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    f_target_hz = args.freq * 1e9

    # ---- 2. one context, four steps --------------------------------------
    #
    # ONE context, built here and handed to `cold_start_report` as `context=`.
    # It is the only O(N^3) piece -- measured 274.5 ms at 153 ports against
    # 2.7 ms for the whole screen off it -- and building four of them is the
    # one expensive mistake available in this section.  It is also the object
    # that carries `element_of_port`, which is what lets the mirror table name
    # a PORT rather than the element label 'ground port 5'.
    try:
        csc = attrib.cold_start_context(Y, ts.freqs, term, f_target_hz,
                                        port_names=_attr_family_names(net, ts),
                                        baseline=baseline)
        cs = attrib.cold_start_report(Y, ts.freqs, term, a, b, f_target_hz,
                                      COLD_QUANTITY, top_k, max_k,
                                      context=csc, baseline=baseline)
    except (attrib.AttribError, ValueError) as e:
        print(f"ERROR: --cold-start: {e}", file=sys.stderr)
        return 2

    csv_rows: list[dict] = []

    # ---- 3. the report ---------------------------------------------------
    print("\n" + _ATTR_LINE)
    print(f"COLD START -- which ports matter:  victim '{names[a]}'  <-  "
          f"aggressor '{names[b]}'")
    print(_ATTR_LINE)
    _emit(_cold_print_header(csc, cs.bracket, names,
                             list(extra_notes) + list(notes_a) + list(notes_b)
                             + list(cs.notes)))
    for w in cs.warnings:
        for line in _attr_wrap("WARN: " + w, "    ", hang="      "):
            print(line)

    # Requirement 11 again, and the ORDER is the requirement: a reader who
    # meets the first signed number before the convention has already guessed.
    # A run with BOTH --attribute and --cold-start prints this paragraph twice,
    # which is deliberate -- each report is separately readable and separately
    # copy-pasteable, and the alternative is one of them silently losing its
    # sign convention depending on which other flag was on the command line.
    _print_section("Sign convention")
    for line in _attr_wrap(attrib.SIGN_CONVENTION_TEXT):
        print(line)

    _print_section(
        "STEP 0. The bracket: is any of this worth your time?",
        "The quantity with every non-probe port OPEN against every one of "
        "them at IDEAL GROUND. This is first because it is the number that "
        "decides whether the other three steps are worth reading -- measured "
        "at 25.67 dB on a planted 12-port case and at exactly 0 dB on a file "
        "where nothing else is connected to anything.")
    _emit(_cold_print_bracket(cs.bracket, list(cs.notes), csv_rows))

    _print_section(
        "STEP 1. Every candidate port, by the exact effect of grounding it",
        "|Z_ap| and |Z_pb| are SEPARATE columns on purpose and must not be "
        "read as their product: a port has to talk to BOTH sides to be a "
        "path. Measured on the planted case, the port with the largest |Z_ap| "
        "in the whole file (34.777 ohm, 67% more than the real path's) has "
        "|Z_pb| = 0.038 and moves the answer by -0.378 pH against the real "
        "path's -395.369 pH -- ranking on coupling to the victim alone puts "
        "it first and it is worthless. Δ is exact: the closed form agrees "
        "with a full re-solve through compute_z_matrix to 1.5e-11.")
    _emit(_cold_print_screen(cs, csv_rows))

    _print_section(
        "STEP 2. Two ports at once, and the mirror from all-grounded",
        "This is the case a single-port ranking is structurally blind to. "
        "Measured: a shield brought out as two ports reads +9.689 pH for "
        "either end grounded alone and -870.268 pH for both -- 90x the "
        "largest single-port effect in the file, with the OPPOSITE SIGN. The "
        "mechanism is the closed LOOP and not the grounding, which is why "
        "'5 short_to 6' with no ground anywhere gives the identical "
        "-870.268 pH.")
    _emit(_cold_print_pairs(cs, csc, top_k, csv_rows))

    _print_section(
        "STEP 3. The greedy cumulative curve: how many ports actually matter",
        "Ground the best port, RE-RANK, ground the next best. Neither a "
        "ranking nor a pair scan answers 'how many of them matter'; this "
        "does. Greedy and not optimal -- the best-k subset is combinatorial "
        "-- but re-ranking is what lets the curve walk into the pair effects "
        "step 2 exists for.")
    _emit(_cold_print_curve(cs, csc, csv_rows))

    _print_section(
        "Name-family suggestions -- a proposal this tool TESTED, never an "
        "assumption",
        "Grouping ports by name WOULD have caught the shield above, because "
        "the two ends of a guard ring normally share a name family. Which "
        "ports are one structure is a semantic judgement about the layout, so "
        "the numbers are computed and shown and the grouping stays a "
        "sentence.")
    _emit(_cold_print_families(cs, csv_rows))

    print("\n" + _ATTR_RULE)
    print("What this screen cannot find")
    print(_ATTR_RULE)
    for line in _attr_wrap(cs.blind_spot):
        print(line)
    print()
    for line in _attr_wrap(
            "* IT CANNOT EVALUATE NEW METAL. Every port here is one the "
            "S-parameter file already has. Moving a trace, adding a shield or "
            "adding a ball that is not already a port is a different EM "
            "solve, and nothing in this report predicts it.", "  ",
            hang="    "):
        print(line)

    if args.cold_start_csv:
        _write_cold_csv(args.cold_start_csv, ts, args, cs, csv_rows)
        print(f"\nWrote cold-start CSV: {args.cold_start_csv}")
    return 0


# ============================================================================
# Composition (--compose):  several files measured as ONE network
# ============================================================================
#
# Same rule as the two sections above: `pkg_rlc_compose` does every piece of
# arithmetic and everything here is argument parsing, echoing and printing.  If
# a number in this section is not straight off one of that module's dataclasses
# it is a bug.
#
# What this section adds that neither of the other two needs is a NAMESPACE.
# On a combined network "port 305" names nothing anyone can act on, so every
# port reference in and out of this CLI carries its file's tag ('PKG.12') and
# every message names the file.  The separator is '.' and NOT ':' --
# parse_port_range('PKG:12') raises "Range must be start:step:stop" today,
# because ':' is already its own start:step:stop separator.
#
# The one thing worth reading twice is --attribute / --cold-start, which run on
# a composition only because `_compose_baseline` puts the cross-file links INTO
# the attribution baseline (R2-8).  Without that gauge both reports decompose
# against all-open, which on a composition leaves the files as disconnected
# islands: measured on a 12-port combined network, every package-only element
# contributes EXACTLY 0 while the reconciliation residual reads 6.49e-15, i.e.
# perfect health.  There is no flag to turn the gauge off.

#: Rows of a proposal listed on the terminal, per section (matched, ambiguous,
#: unmatched-A, unmatched-B).  The same display/export split the two reports
#: above make: --compose-propose-csv has NO cap, and carries the unmatched
#: ports as commented rows, which is what makes the "(see the CSV)" pointer
#: true rather than decorative.
COMPOSE_PROPOSE_ROWS = 20

#: End pairs echoed for a multi-wire link.  Two -- the first and the last --
#: because an off-by-one in one file's numbering shifts EVERY pair by one, and
#: the two ends are where that is visible without reading N lines.
COMPOSE_LINK_ECHO_ENDS = 2

# A tag looks like this.  pkg_rlc_compose validates it for real (and refuses a
# duplicate); this copy exists only to decide whether 'PKG=file.s2p' is a
# tagged path or a path with an '=' in it, and whether 'PKG.1,2' carries a tag
# at all.
_COMPOSE_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Port numbers inside a core message, for translation back into the composed
# namespace.  Core says "Port(s) 1, 2 are listed both as a probe ...", "Port
# number(s) 5 are outside this file's 4 ports" and "Port 3 is claimed by
# both ...", and every one of those numbers is a GLOBAL index on a composed
# network -- i.e. unactionable until it names a file.  Digits that FOLLOW the
# word ("this file's 4 ports") are deliberately not matched.
_COMPOSE_PORTNUM_RE = re.compile(
    r"(?i)\bport(?:\s+number)?\(?s?\)?\s+(\d+(?:\s*,\s*\d+)*)")

# The ONE core message whose port numbers are 0-BASED, and it needs its own
# pattern for exactly that reason.  `merge_terms` raises "Ports [1, 4] merged
# via short, but assigned to conflicting signal groups" with the Union-Find
# MEMBER list, which is internal 0-based indices; every other message core
# raises at this boundary is 1-based.  Measured: EM.2 (global 2) shorted to
# PKG.3 (global 5) reports "Ports [1, 4]".  It is also the message a composed
# network hits FIRST -- a cross-file link is precisely what merges two probes
# -- so translating it with the wrong offset would name two real ports that
# have nothing to do with the mistake, which is worse than not translating.
# Core is not touched here: that file is not this change's to edit, and the
# fix would move a message other tests pin.
_COMPOSE_MERGED_RE = re.compile(
    r"^Ports \[(?P<ports>[\d, ]+)\] merged via short")

#: --compose-link keywords.  Two PRIMITIVES, because an ideal short cannot be a
#: stamped element: y_series_rlc(R=0, L=0, C=inf) is inf+nanj (measured).  One
#: surface word, 'connect', reaches whichever of them the R/L/C fields select,
#: which is what the requirement's own DSL sketch spells.
_COMPOSE_KW_SHORT = ("short_to", "short")
_COMPOSE_KW_LUMPED = ("lumped_between", "lumped")
_COMPOSE_KW_EITHER = ("connect",)

#: --compose-map column names, lower-cased.  Several spellings of each side
#: because this file is written by hand and by a spreadsheet, and refusing
#: 'from'/'to' would be a refusal about vocabulary rather than about content.
#: Deliberately NOT domain words ('die', 'pkg'): those are the tags the user
#: chose for their files, and a column called 'pkg' meaning "the second
#: endpoint" in a file whose tags are EM and PKG reads two ways.
_COMPOSE_MAP_A = ("a", "from", "left", "port_a", "porta")
_COMPOSE_MAP_B = ("b", "to", "right", "port_b", "portb")
_COMPOSE_MAP_EL = ("element", "rlc", "value", "kind")
_COMPOSE_MAP_NOTE = ("note", "comment", "why")


class _ComposedView:
    """
    The bits of `TouchstoneData` the shared report path reads, for a network
    that came out of several files and therefore has no single one.

    Deliberately NOT a TouchstoneData: building one would need `s`, i.e.
    y_to_s over the whole composed network (a 316-port, 1601-point stack), for
    the sake of two attributes nothing on this path reads.
    """

    def __init__(self, net: "comp.ComposedNetwork"):
        self.net = net
        self.freqs = net.freqs
        self.nports = net.nports
        self.port_names = net.port_labels()
        self.source_path = " + ".join(f"{b.alias}={b.label}"
                                      for b in net.blocks)


def _compose_dependent_flags(args: argparse.Namespace) -> list[str]:
    """The --compose-* flags the user set.  Inert without --compose."""
    given: list[str] = []
    if (args.compose_alias or "").strip():
        given.append("--compose-alias")
    if args.compose_keep:
        given.append("--compose-keep")
    if args.compose_gnd:
        given.append("--compose-gnd")
    if args.compose_link:
        given.append("--compose-link")
    if args.compose_map:
        given.append("--compose-map")
    if args.compose_propose:
        given.append("--compose-propose")
    if args.compose_propose_csv:
        given.append("--compose-propose-csv")
    if args.compose_export:
        given.append("--compose-export")
    if (args.compose_export_ports or "").strip():
        given.append("--compose-export-ports")
    return given


def _compose_file_arg(spec: str) -> tuple[str, str]:
    """
    'PKG=package.s60p' -> ('PKG', 'package.s60p');  'x.s2p' -> ('', 'x.s2p').

    The split happens only when what is in front of the FIRST '=' looks like a
    tag, so a path that contains one ('runs/sweep=3/pkg.s4p') is still a path.
    """
    text = (spec or "").strip()
    head, sep, tail = text.partition("=")
    if sep and _COMPOSE_ALIAS_RE.match(head.strip()) and tail.strip():
        return head.strip(), tail.strip()
    return "", text


def _compose_local_ports(spec: str, aliases: list[str],
                         flag: str) -> tuple[str, list[int]]:
    """
    'PKG.10-12' -> ('PKG', [10, 11, 12]).  The tag is REQUIRED.

    These two flags run BEFORE the composed network exists, so there is no
    global numbering to fall back on and no default scope that would mean
    anything -- a bare '10-12' would have to pick a file, and picking one is
    how a package's keep list silently lands on the die.
    """
    text = (spec or "").strip()
    head, sep, rest = text.partition(comp.COMPOSE_TAG_SEP)
    known = ", ".join(aliases)
    if not sep or not _COMPOSE_ALIAS_RE.match(head.strip()):
        raise ValueError(
            f"{flag} '{text}' does not say which file it belongs to. Write "
            f"<file>{comp.COMPOSE_TAG_SEP}<ports>, e.g. "
            f"{aliases[-1]}{comp.COMPOSE_TAG_SEP}10-12. Known files: {known}")
    alias = head.strip()
    match = [a for a in aliases if a.lower() == alias.lower()]
    if not match:
        raise ValueError(
            f"{flag} '{text}': '{alias}' is not one of the composed files. "
            f"Known files: {known}")
    try:
        ports = parse_port_range(rest)
    except ValueError as e:
        raise ValueError(f"{flag} '{text}': {e}") from None
    if not ports:
        raise ValueError(
            f"{flag} '{text}' names the file {match[0]} but no port in it")
    return match[0], ports


def _compose_load(args: argparse.Namespace) -> comp.ComposedNetwork:
    """
    Parse every file, apply the per-file pre-reduction, stack the blocks.

    The files are parsed one at a time and each summary is printed as it
    lands, so a failure on the third file arrives with the first two already
    accounted for rather than after a silent minute.
    """
    entries_spec: list[tuple[str, str]] = [
        ((args.compose_alias or "").strip(), args.file)]
    for spec in (args.compose or []):
        entries_spec.append(_compose_file_arg(spec))
    aliases = [a or comp.default_alias(i)
               for i, (a, _p) in enumerate(entries_spec)]

    keep_by: dict[str, list[int]] = {}
    gnd_by: dict[str, list[int]] = {}
    for flag, store in (("--compose-keep", keep_by),
                        ("--compose-gnd", gnd_by)):
        for spec in (getattr(args, flag[2:].replace("-", "_")) or []):
            alias, ports = _compose_local_ports(spec, aliases, flag)
            store.setdefault(alias, []).extend(ports)

    entries: list[comp.ComposeInput] = []
    for i, (_a, path) in enumerate(entries_spec):
        d = parse_touchstone(path, force_nports=args.force_nports,
                             lenient=args.lenient)
        print(f"Loaded {d.source_path}   [{aliases[i]}]")
        for line in d.summary_lines()[1:]:
            print(line)
        entries.append(comp.ComposeInput(
            data=d, alias=aliases[i],
            keep=keep_by.get(aliases[i]),
            gnd=gnd_by.get(aliases[i], ())))
    # marker_hz is passed so that a composition whose common span does not
    # reach --freq is REFUSED here rather than answered at whatever frequency
    # happened to survive the intersection.  Not for a PROPOSAL: that is a
    # question about port NAMES, and refusing it over a frequency marker it
    # never uses would send the user to fix the wrong thing.  A genuinely empty
    # intersection is refused either way -- that check is separate.
    marker = None if args.compose_propose else args.freq * 1e9
    return comp.compose(entries, marker_hz=marker)


def _compose_ports(net: comp.ComposedNetwork, spec: str,
                   default: str) -> list[int]:
    """
    A scoped port field -> 1-based GLOBAL ports, order preserved, deduped.

    ';' separates FIELDS.  It exists because a file tag USED TO scope a whole
    field: `parse_scoped_ports` refused 'PKG.40,41,EM.5' rather than guess
    whether the tag covered the list or only the token it sat on, and one
    ground list touching two files is an ordinary spec, so it needed a
    separator the port grammar does not already use (',' ':' and '-' are all
    taken).  That tag is now per-token, so 'PKG.40,41,EM.5' is an ordinary
    field and ';' buys nothing new -- it stays because it is in shipped specs
    and scripts, and because a field per ground group still reads better than
    one long list.
    """
    out: list[int] = []
    for part in (spec or "").split(";"):
        if not part.strip():
            continue
        for p in comp.parse_scoped_ports(part, net, default):
            if p not in out:
                out.append(p)
    return out


def _compose_mport_global(net: comp.ComposedNetwork, spec: str,
                          default: str) -> str:
    """
    'vic = EM.1,2 / EM.3'  ->  'vic = 17,18 / 19' in the composed numbering.

    Rewritten as TEXT and handed back to parse_mport_spec rather than built
    into a triple here.  That is the connection table's own rule -- rows reach
    a TerminationSet through the DSL text, never by building one directly --
    and it is what keeps the reserved-name check, the both-sides check and
    every message in ONE place.
    """
    text = (spec or "").strip()
    if not text:
        raise ValueError("Empty measurement-port spec")
    head, eq, body = text.partition("=")
    if not eq:
        body = text
    if body.count("/") > 1:
        # Let parse_mport_spec produce its own message, about the text the
        # user actually typed rather than about a half-rewritten copy.
        parse_mport_spec(text)
    plus_txt, slash, minus_txt = body.partition("/")
    plus = _compose_ports(net, plus_txt, default)
    minus = _compose_ports(net, minus_txt, default) if slash else []
    out = f"{head.strip()} = " if eq else ""
    out += ",".join(str(p) for p in plus)
    if slash:
        out += " / " + ",".join(str(p) for p in minus)
    return out


def _compose_scope_error(net: comp.ComposedNetwork | None,
                         exc: Exception) -> str:
    """
    A core refusal, with every port number it names translated back to a file.

    "Port(s) 1 are listed both as a probe ... and as ground" is actionable on
    one file and unactionable on a composed one, where port 1 is whichever
    file happened to be stacked first.  The port map is appended whether or not
    a number was found, because a refusal on a composed network that does not
    say what the numbering is leaves the reader with nothing to check against.
    """
    text = str(exc)
    if net is None:
        return text
    seen: list[int] = []
    merged = _COMPOSE_MERGED_RE.match(text)
    if merged:
        # 0-based, see _COMPOSE_MERGED_RE.
        for tok in merged.group("ports").split(","):
            tok = tok.strip()
            if tok.isdigit() and int(tok) + 1 not in seen:
                seen.append(int(tok) + 1)
    else:
        for m in _COMPOSE_PORTNUM_RE.finditer(text):
            for tok in m.group(1).split(","):
                tok = tok.strip()
                if tok.isdigit() and int(tok) not in seen:
                    seen.append(int(tok))
    lines = [text]
    for p in seen:
        if 1 <= p <= net.nports:
            lines.append(f"  port {p} is {net.describe_port(p)}")
    lines.extend("  " + s for s in net.port_map_lines())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def _compose_rlc_params(tokens: list[str], where: str) -> dict:
    """
    R/L/C fields of a link.  A token without an '=' is REFUSED.

    parse_kv_rlc_params silently DROPS such a token, so 'L=5 n' would build a
    5 henry element where 5 nH was meant -- core's `_rlc_tokens` refuses the
    identical trap in a table cell and `_attr_series_impedance` refuses it in a
    candidate termination. There is no way to quote a value here either, so
    refusing is the only answer that cannot be silently wrong.
    """
    bad = [t for t in tokens if "=" not in t]
    if bad:
        raise ValueError(
            f"{where}: field(s) {', '.join(repr(b) for b in bad)} carry no "
            "'='. An element is written as whitespace-separated R=/L=/C= "
            "fields -- 'L=0.3n', 'R=0.5 L=1n'. A value and its unit are ONE "
            "token: 'L=5 n' would be read as 5 henry, not 5 nH")
    try:
        return parse_kv_rlc_params(tokens)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


def _compose_parse_link(net: comp.ComposedNetwork, spec: str, default: str,
                        where: str) -> tuple[list, list[str]]:
    """
    One --compose-link / --compose-map row -> (couplings, echo lines).

    The pairing itself is `link_short` / `link_lumped`, i.e. the compose
    module's, so a length mismatch is refused there with the end pairs in the
    message.  The echo below prints those same end pairs on SUCCESS, which is
    the case that has no other symptom: every pair off by one is a complete,
    plausible, wrong answer.
    """
    parts = (spec or "").split()
    kws = _COMPOSE_KW_SHORT + _COMPOSE_KW_LUMPED + _COMPOSE_KW_EITHER
    if len(parts) < 3:
        raise ValueError(
            f"{where}: '{spec}' is not a connection. Write "
            f"'<ports> <keyword> <ports> [R=.. L=.. C=..]', e.g. "
            f"'{net.blocks[0].alias}{comp.COMPOSE_TAG_SEP}1 short_to "
            f"{net.blocks[-1].alias}{comp.COMPOSE_TAG_SEP}12'. Keywords: "
            + ", ".join(kws))
    a_spec, kw, b_spec = parts[0], parts[1].lower(), parts[2]
    rest = parts[3:]
    if kw not in kws:
        raise ValueError(
            f"{where}: '{parts[1]}' is not a connection keyword. Use "
            + ", ".join(kws)
            + " -- 'connect' is a short when no R/L/C follows it and an "
              "element when one does")

    if kw in _COMPOSE_KW_SHORT and rest:
        raise ValueError(
            f"{where}: '{parts[1]}' is an ideal short and cannot carry "
            f"{' '.join(rest)}. A short is a MERGED NODE, not a stamped "
            "element -- y_series_rlc(R=0, L=0, C=inf) is inf+nanj (measured). "
            "Write 'lumped_between' for an element")
    if kw in _COMPOSE_KW_LUMPED and not rest:
        raise ValueError(
            f"{where}: '{parts[1]}' needs at least one of R=, L=, C=. For a "
            "wire with no element in it, write 'short_to'")

    if rest:
        params = _compose_rlc_params(rest, where)
        links = comp.link_lumped(net, a_spec, b_spec,
                                 y_series_rlc(**params), params,
                                 default=default)
        what = "element"
    else:
        links = comp.link_short(net, a_spec, b_spec, default=default)
        what = "wire"

    pairs = [(c.port_i + 1, c.port_j + 1) for c in links]
    n = len(pairs)
    plural = "" if n == 1 else "s"
    if n > 1 and len({p for p, _q in pairs}) == 1:
        how = f"fan-out from {net.port_label(pairs[0][0])}"
    elif n > 1 and len({q for _p, q in pairs}) == 1:
        how = f"fan-in to {net.port_label(pairs[0][1])}"
    elif n > 1:
        how = "elementwise, one per pair"
    else:
        how = "one pair"
    head = f"  {where}: {spec}"
    detail = f"      {n} {what}{plural}, {how}"
    if what == "element" and n > 1:
        # The measured 3x trap, at the one moment it can still be caught: N
        # elements between the SAME two nodes are N in parallel (measured
        # 3.333 fH for three 10 fH, ratio exactly 3.000), and the spelling
        # that produces them looks identical to the spelling that produces one.
        detail += (f" -- {n} SEPARATE elements, not one. In parallel between "
                   f"the same two nodes they would read {n}x smaller")
    def wire(i: int, tag: str) -> str:
        p, q = pairs[i]
        return (f"      {tag:<6} {net.describe_port(p)}  --  "
                f"{net.describe_port(q)}")

    echo = [head, detail]
    if n == 1:
        echo.append(wire(0, ""))
    else:
        echo.append(wire(0, "first"))
        if n > COMPOSE_LINK_ECHO_ENDS:
            echo.append(f"      {'...':<6} {n - COMPOSE_LINK_ECHO_ENDS} more")
        echo.append(wire(n - 1, "last"))
    return links, echo


def _compose_read_map(path: str) -> list[tuple[int, str]]:
    """
    A --compose-map CSV -> [(line number, link spec)] in --compose-link syntax.

    One parser, one set of error messages: the rows are turned back into the
    text spelling and handed to _compose_parse_link, so a CSV and a flag cannot
    disagree about what a connection means.

    Read as utf-8-sig: a spreadsheet writes a BOM, and a BOM glued to the first
    header name would make the 'a' column unfindable -- the same failure the
    Touchstone parser's encoding sniffer exists for.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError as e:
        raise ValueError(f"--compose-map '{path}': {e}") from None
    kept = [(i + 1, line) for i, line in enumerate(raw)
            if line.strip() and not line.lstrip().startswith("#")]
    if not kept:
        raise ValueError(
            f"--compose-map '{path}' has no rows (every line is blank or "
            "commented out). A proposal CSV comments out the ports it could "
            "not match -- uncomment the ones you accept and fill in the other "
            "side")
    reader = csv.DictReader([line for _n, line in kept])
    cols = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}

    def pick(names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    col_a, col_b = pick(_COMPOSE_MAP_A), pick(_COMPOSE_MAP_B)
    col_el, col_note = pick(_COMPOSE_MAP_EL), pick(_COMPOSE_MAP_NOTE)
    if col_a is None or col_b is None:
        missing = ([] if col_a is not None else ["'a'"]) + \
            ([] if col_b is not None else ["'b'"])
        raise ValueError(
            f"--compose-map '{path}': line {kept[0][0]} is the header and it "
            f"has no " + " column and no ".join(missing)
            + " column. The two endpoint columns may be named "
            + " / ".join(_COMPOSE_MAP_A) + " and "
            + " / ".join(_COMPOSE_MAP_B)
            + f"; this file has: {', '.join(reader.fieldnames or ['(none)'])}")
    out: list[tuple[int, str]] = []
    for i, row in enumerate(reader):
        lineno = kept[i + 1][0] if i + 1 < len(kept) else kept[-1][0]
        a = (row.get(col_a) or "").strip()
        b = (row.get(col_b) or "").strip()
        el = (row.get(col_el) or "").strip() if col_el else ""
        if not a and not b and not el:
            continue
        if not a or not b:
            note = (row.get(col_note) or "").strip() if col_note else ""
            raise ValueError(
                f"--compose-map '{path}' line {lineno}: one side of the "
                f"connection is empty (a='{a}', b='{b}'"
                + (f", note='{note}'" if note else "") + "). Fill it in, or "
                "comment the row out with a leading '#' until you have "
                "decided")
        if el.lower() in ("", "short", "wire", "0"):
            out.append((lineno, f"{a} short_to {b}"))
        else:
            out.append((lineno, f"{a} lumped_between {b} {el}"))
    if not out:
        raise ValueError(
            f"--compose-map '{path}' has a header and no connections")
    return out


def _compose_links(args: argparse.Namespace, net: comp.ComposedNetwork,
                   default: str) -> tuple[list, list[str], list[str],
                                          dict[int, str]]:
    """
    Every --compose-link and every --compose-map row, in that order.

    Returns (couplings, echo lines, the specs as text, port -> source label).
    The third is what the export's caveat lists: a file that does not contain
    the links has to say which links it does not contain.  The fourth is the
    attribution's `sources=` for these ports -- without it every link lands in
    a group called after its KIND ('lumped_between'), which on a spec with
    several links is one group holding elements the user wrote on separate
    lines, i.e. exactly the provenance `_attr_sources` exists to keep.  The
    walk is in declaration order and the LAST writer wins, the same rule the
    DSL and `row_sources` follow.
    """
    specs: list[tuple[str, str]] = [
        (f"link {i + 1}", s)
        for i, s in enumerate(args.compose_link or [])]
    if args.compose_map:
        for lineno, s in _compose_read_map(args.compose_map):
            specs.append((f"{Path(args.compose_map).name} line {lineno}", s))
    links: list = []
    echo: list[str] = []
    src: dict[int, str] = {}
    for where, spec in specs:
        c, e = _compose_parse_link(net, spec, default, where)
        label = (f'--compose-link "{spec.strip()}"'
                 if where.startswith("link ") else f"{where}: {spec.strip()}")
        for cc in c:
            src[cc.port_i + 1] = label
            src[cc.port_j + 1] = label
        links.extend(c)
        echo.extend(e)
    return links, echo, [s for _w, s in specs], src


# ---------------------------------------------------------------------------
# The proposal (requirement R2-5):  propose, never commit
# ---------------------------------------------------------------------------

def _compose_name_index(net: comp.ComposedNetwork,
                        alias: str) -> tuple[dict[str, list[int]], list[int]]:
    """(normalised name -> global ports, ports with no name) for a file."""
    block = net.block_of_alias(alias)
    named: dict[str, list[int]] = {}
    unnamed: list[int] = []
    for i in range(block.nports):
        g = block.offset + i + 1
        key = (block.port_names[i] or "").strip().casefold()
        if key:
            named.setdefault(key, []).append(g)
        else:
            unnamed.append(g)
    return named, unnamed


def _compose_propose(net: comp.ComposedNetwork, a_alias: str,
                     b_alias: str) -> dict:
    """
    Match two files' ports by the names the files already carry.

    Exact, after trimming and case folding, and NOTHING else is inferred.  A
    family heuristic ('VSS_1' and 'VSS_A1' share a prefix) was considered and
    left out: on a real package it pairs 54 balls against 54 pads as a 54x54
    cross product, which is not a mapping, and the repo's own experience with
    name_prefix is that spelling is a statement about spelling.

    Many-to-one is normal and is paired as a fan-out (54 ground balls onto one
    die pad).  N-to-M with both above 1 has no defensible order, so it is
    reported as ambiguous rather than paired in whatever order the files happen
    to list their ports.
    """
    a_named, a_unnamed = _compose_name_index(net, a_alias)
    b_named, b_unnamed = _compose_name_index(net, b_alias)
    pairs: list[tuple[str, list[tuple[int, int]], str]] = []
    ambiguous: list[tuple[str, list[int], list[int]]] = []
    matched_a: set[int] = set()
    matched_b: set[int] = set()
    for key in sorted(set(a_named) & set(b_named)):
        ga, gb = a_named[key], b_named[key]
        if len(ga) == len(gb):
            how = "one pair" if len(ga) == 1 else "elementwise"
            these = list(zip(ga, gb))
        elif len(ga) == 1:
            how = "fan-out"
            these = [(ga[0], q) for q in gb]
        elif len(gb) == 1:
            how = "fan-in"
            these = [(p, gb[0]) for p in ga]
        else:
            ambiguous.append((key, ga, gb))
            continue
        pairs.append((key, these, how))
        matched_a.update(p for p, _q in these)
        matched_b.update(q for _p, q in these)
    a_block = net.block_of_alias(a_alias)
    b_block = net.block_of_alias(b_alias)
    unmatched_a = [g for g in range(a_block.offset + 1,
                                    a_block.offset + a_block.nports + 1)
                   if g not in matched_a]
    unmatched_b = [g for g in range(b_block.offset + 1,
                                    b_block.offset + b_block.nports + 1)
                   if g not in matched_b]
    return {
        "a_alias": a_block.alias, "b_alias": b_block.alias,
        "pairs": pairs, "ambiguous": ambiguous,
        "unmatched_a": unmatched_a, "unmatched_b": unmatched_b,
        "unnamed_a": a_unnamed, "unnamed_b": b_unnamed,
    }


def _compose_print_proposal(net: comp.ComposedNetwork, prop: dict) -> None:
    n_pairs = sum(len(p) for _k, p, _h in prop["pairs"])
    print("\n" + _ATTR_LINE)
    print(f"PROPOSED port correspondence:  {prop['a_alias']}  <->  "
          f"{prop['b_alias']}")
    print(_ATTR_LINE)
    for line in _attr_wrap(
            "This is a PROPOSAL and nothing has been connected. Port "
            "names are "
            "compared after trimming and case folding; nothing else is "
            "inferred, in particular no name-family guess. Read it, edit it, "
            "then commit what you accept with --compose-map."):
        print(line)
    print()
    if n_pairs:
        rows = []
        for key, these, how in prop["pairs"]:
            for p, q in these:
                rows.append([key, net.port_label(p), net.port_label(q), how])
        _print_table(["port name", prop["a_alias"], prop["b_alias"], "why"],
                     rows[:COMPOSE_PROPOSE_ROWS], ["<", "<", "<", "<"])
        if len(rows) > COMPOSE_PROPOSE_ROWS:
            print(f"  ... {len(rows) - COMPOSE_PROPOSE_ROWS} more matched "
                  "pair(s) (--compose-propose-csv has no cap)")
    else:
        for line in _attr_wrap(
                "NOTHING MATCHED. No port name is shared by these two files. "
                "That is the normal case when the two exports were named by "
                "different people or when one of them carries no port names "
                "at all -- write the correspondence by hand into a "
                "--compose-map CSV, or state it directly with --compose-link, "
                "which takes ranges on both sides.", "  "):
            print(line)
    for key, ga, gb in prop["ambiguous"][:COMPOSE_PROPOSE_ROWS]:
        for line in _attr_wrap(
                f"ambiguous: {len(ga)} port(s) of {prop['a_alias']} and "
                f"{len(gb)} of {prop['b_alias']} are all named '{key}' "
                f"({net.describe_ports(ga)} / {net.describe_ports(gb)}). "
                f"{len(ga)}-to-{len(gb)} has no defensible pairing order, so "
                "these are NOT proposed -- say which goes to which.",
                "  ", hang="    "):
            print(line)
    for side, key in ((prop["a_alias"], "unmatched_a"),
                      (prop["b_alias"], "unmatched_b")):
        rest = prop[key]
        print(f"\n  unmatched in {side}: {len(rest)} port(s)"
              + (f" -- {net.describe_ports(rest[:COMPOSE_PROPOSE_ROWS])}"
                 if rest else ""))
        if len(rest) > COMPOSE_PROPOSE_ROWS:
            print(f"    ... and {len(rest) - COMPOSE_PROPOSE_ROWS} more "
                  "(every one of them is in --compose-propose-csv, as a "
                  "commented row)")
    print()
    for line in _attr_wrap(
            "Nothing was measured: --compose-propose stops here on purpose. "
            "A proposal you have reviewed and accepted is a reviewed input; "
            "one applied on your behalf is a guess, and this tool does not "
            "guess a port correspondence."):
        print(line)


_COMPOSE_MAP_FIELDS = ["a", "b", "element", "note"]


def _compose_write_proposal_csv(path: str, net: comp.ComposedNetwork,
                                prop: dict) -> None:
    """
    The proposal as a --compose-map CSV, unmatched ports included as COMMENTS.

    That is what makes the file a work list rather than only an answer: the
    reader uncomments the row, fills in the other side, and feeds the same file
    straight back to --compose-map, which skips '#' lines.
    """
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Proposed correspondence {prop['a_alias']} <-> "
                 f"{prop['b_alias']}, from the files' own port names\n")
        for line in net.port_map_lines():
            fh.write("# " + line + "\n")
        fh.write("# Names were compared after trimming and case folding; "
                 "nothing else was inferred.\n")
        fh.write("# 'element' empty or 'short' = a wire; otherwise R/L/C, "
                 "e.g. L=0.3n\n")
        fh.write("# Rows below starting with '#' are ports that did NOT "
                 "match. Uncomment and complete the ones you want.\n")
        w = csv.DictWriter(fh, fieldnames=_COMPOSE_MAP_FIELDS)
        w.writeheader()
        for key, these, how in prop["pairs"]:
            for p, q in these:
                w.writerow({"a": net.port_label(p), "b": net.port_label(q),
                            "element": "short",
                            "note": f"name '{key}' ({how})"})
        for key, ga, gb in prop["ambiguous"]:
            fh.write(f"# AMBIGUOUS name '{key}': {net.describe_ports(ga)} "
                     f"({len(ga)}) against {net.describe_ports(gb)} "
                     f"({len(gb)})\n")
            for p in ga:
                fh.write(f"#{net.port_label(p)},,short,ambiguous name "
                         f"'{key}'\n")
        for side, key in ((prop["a_alias"], "unmatched_a"),
                          (prop["b_alias"], "unmatched_b")):
            for g in prop[key]:
                nm = net.describe_port(g)
                if side == prop["a_alias"]:
                    fh.write(f"#{net.port_label(g)},,short,unmatched in "
                             f"{side}: {nm}\n")
                else:
                    fh.write(f"#,{net.port_label(g)},short,unmatched in "
                             f"{side}: {nm}\n")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _compose_print_network(net: comp.ComposedNetwork) -> None:
    """The port map, the frequency plan and everything compose() noticed."""
    print("\n" + _ATTR_LINE)
    print(f"COMPOSED NETWORK:  {len(net.blocks)} files, {net.nports} ports, "
          f"{len(net.freqs)} frequency points")
    print(_ATTR_LINE)
    for line in net.port_map_lines():
        print("  " + line)

    plan = net.plan
    if plan is not None:
        print(f"\n  common span      : {format_freq(plan.lo)} - "
              f"{format_freq(plan.hi)}")
        print(f"  effective step   : {format_freq(plan.effective_step)}"
              "   <- the COARSEST file's largest step; resampling onto a "
              "finer grid recovers nothing")
        rows = []
        for i, b in enumerate(net.blocks):
            deg = plan.phase_step_deg[i]
            rows.append([
                b.alias,
                "interpolated" if plan.interpolated[i]
                else ("grid" if i == plan.grid_from else "as sampled"),
                str(plan.dropped[i]),
                format_freq(plan.max_step[i]),
                plan.spacing[i],
                ("--" if not plan.interpolated[i]
                 else (">= " if plan.phase_aliased[i] else "")
                 + f"{deg:.1f}"),
                ("--" if not plan.interpolated[i]
                 else f"{plan.fake_loss_db(i):.3f}"),
            ])
        print()
        _print_table(["file", "grid", "dropped", "max step", "sweep",
                      "phase/step (deg)", "invented loss (dB)"], rows,
                     ["<", "<", ">", ">", "<", ">", ">"])

    for n in net.notes:
        for line in _attr_wrap("note: " + n, "  ", hang="        "):
            print(line)
    for w in net.warnings:
        for line in _attr_wrap("WARN: " + w, "  ", hang="        "):
            print(line)


def _compose_print_reference(sol, gnd_folded: set[str]) -> None:
    """
    The mandatory reference-node check.  BEFORE the numbers, on purpose.

    A weld does not make anything raise and does not make the number look
    wrong: measured on the module's own fixture, the package ground pad
    GROUNDED, OPEN and through 1 nH all give L_eff = 2.1454 nH, bit-identical,
    spread 0.000e+00. What it changes is how the number must be read, so it has
    to arrive before the number and not in a footnote under it.
    """
    print("\n" + _ATTR_RULE)
    print("REFERENCE-NODE CHECK (mandatory) -- is each file's ground network "
          "in the circuit?")
    print(_ATTR_RULE)
    for line in _attr_wrap(
            "An n-port Touchstone Y already has its own reference eliminated, "
            "so stacking the files identifies their references at ZERO "
            "impedance. Each file's declared ground set is therefore "
            "perturbed "
            "with a series inductor and the network re-solved: if the answer "
            "does not move AT ALL, that file's ground network is not in the "
            "circuit and grounded / open / through-an-inductor are the same "
            "number."):
        print(line)
    print()
    tag = {comp.REF_WELDED: "WELD: ", comp.REF_LIVE: "ok:   ",
           comp.REF_NO_GROUND: "note: ", comp.REF_UNKNOWN: "?:    "}
    for r in sol.reference:
        for line in _attr_wrap(tag.get(r.verdict, "?:    ") + r.message,
                               "  ", hang="        "):
            print(line)
        if r.verdict == comp.REF_NO_GROUND and r.alias in gnd_folded:
            for line in _attr_wrap(
                    f"--compose-gnd folded {r.alias}'s ground ports into its "
                    "pre-reduction, so they are shorted to that file's own "
                    "reference and no port survives for this check to "
                    "perturb. That is the intended cheap spelling, and it "
                    "means this check has nothing to say about that file -- "
                    "keep one ground port out of --compose-gnd and put it in "
                    "--gnd instead if you want the verdict.", "        "):
                print(line)


def _compose_export(args: argparse.Namespace, net: comp.ComposedNetwork,
                    default: str, links: list[str]) -> None:
    """
    Write the stacked network out, and say exactly what is NOT in it.

    What the file contains is the files stacked on one common frequency axis
    with ONE shared reference node -- which is the composition's own weld, so
    instantiating this file reproduces it exactly.  What it does NOT contain is
    the links: they are ShortPair / LumpedBetween in the TerminationSet, and
    compute_z_matrix applies them at solve time (merging shorted nodes changes
    the port count).  Stamping them into Y here would mean a second
    implementation of the merge the golden reference exists to pin, in the
    wrong layer.

    That is not a hole in the file: the links are wires and two-terminal
    elements, i.e. the part a schematic is FOR, and leaving them out is what
    keeps them editable.  It is only a hole in a description, so the note below
    names every one of them.
    """
    ports = None
    if (args.compose_export_ports or "").strip():
        ports = _compose_ports(net, args.compose_export_ports, default)
        if not ports:
            raise ValueError(
                "--compose-export-ports resolved to no port at all")
    comment = "\n".join(
        ["The cross-file links are NOT in this file -- add them outside:"]
        + ["   " + s for s in links]) if links else ""
    out = comp.write_composed_touchstone(
        args.compose_export, net, ports=ports, z0=DEFAULT_Z0, comment=comment)
    n = len(ports) if ports else net.nports
    print(f"\nWrote stacked network: {out}   ({n} ports, "
          f"{len(net.freqs)} points, Z0 = {DEFAULT_Z0:g})")
    if ports:
        print(f"  ports brought out: {net.describe_ports(ports)}  "
              "(the rest were eliminated open)")
    for line in _attr_wrap(
            "This file is the files STACKED -- one common frequency axis, "
            "one shared reference node, port names carrying their file tags. "
            "The " + (f"{len(links)} connection(s) above are" if links
                      else "connections are")
            + " NOT stamped into it: a short MERGES two nodes and changes the "
              "port count, so the links stay in the termination set and "
              "compute_z_matrix applies them at solve time. Wire them up in "
              "your simulator, or read this file back here and pass the same "
              "--compose-link -- that round trip is the independent check the "
              "export exists for.", "  "):
        print(line)
    ext = re.match(r"^\.s(\d+)p$", out.suffix.lower())
    if ext and int(ext.group(1)) != n:
        print(f"  NOTE: the name says .s{ext.group(1)}p but the network has "
              f"{n} ports. This tool content-sniffs and will read it back "
              f"either way; other tools trust the extension. '.s{n}p' is the "
              "conventional name")
    elif not ext:
        print(f"  NOTE: the conventional name for an {n}-port file is "
              f"'.s{n}p'. This tool content-sniffs, so it reads back as it "
              "is; other tools trust the extension")


def _compose_baseline(net: comp.ComposedNetwork) -> "attrib.BaselineLinks":
    """
    The R2-8 GAUGE for a composed network: the cross-file links are IN the
    baseline, not elements on top of it.

    `--attribute` and `--cold-start` both decompose against an ALL-OPEN
    baseline, and all-open on a composition leaves the files as disconnected
    islands -- Ybase is then exactly block diagonal.  Measured with the real
    engine on a 12-port combined network: the EM-vs-PKG off-diagonal block is
    0.000e+00, every package-only element's contribution comes out EXACTLY 0,
    and the reconciliation residual reads 6.49e-15, i.e. perfect health.  A
    confident, exactly-zero, perfectly-reconciled wrong answer is worse than no
    answer, so this is not optional and there is no flag to turn it off.

    `blocks=`, not an explicit link list: a `PortBlocks` says "every declared
    link whose two ports come from different files is structure", which CANNOT
    miss a link.  An enumerated list can, and a missed link is precisely the
    silent zero above.  A link INSIDE one file stays an ordinary element,
    because it is one -- the gauge is about the stack, not about the spec.
    """
    # `b.nports` (the SURVIVING count), never `b.nports_original`: after a
    # --compose-keep pre-reduction the block in `net.Y` is the reduced one, and
    # a block map sized from the original would put every later file's ports at
    # the wrong global index -- silently, because PortBlocks only checks that
    # the total matches Y.
    return attrib.BaselineLinks(
        blocks=attrib.PortBlocks.from_sizes(
            [b.nports for b in net.blocks],
            [b.alias for b in net.blocks]))


def _attr_port_tags(net: "comp.ComposedNetwork | None") -> list[str] | None:
    """`['EM.1', ..., 'PKG.4']`, or None on a single file."""
    if net is None:
        return None
    return [net.port_label(g) for g in range(1, net.nports + 1)]


def _attr_family_names(net: "comp.ComposedNetwork | None", ts) -> list[str]:
    """
    The port names the NAMING HEURISTICS see (`--attribute-group name`, and the
    cold start's family suggestions).  Tag plus name, and NO local number.

    `name_prefix` strips only a TRAILING run of digits, so the label this CLI
    prints elsewhere -- `PKG.100 VSS_1` -- is exactly the wrong input for it:
    the prefixes come out `PKG.100 VSS_`, `PKG.101 VSS_`, ... one family per
    port, on the file where a family is the whole point (a package's 54 VSS
    balls).  `PKG.VSS_1` gives `PKG.VSS_` for all 54, and keeps two files'
    identically named nets apart, which a bare `VSS_1` would not.  A port whose
    file gave it no name stays "", so a file that names nothing is still silent
    by construction.
    """
    if net is None:
        return list(ts.port_names)
    out: list[str] = []
    for b in net.blocks:
        for i in range(b.nports):
            nm = b.port_names[i] if i < len(b.port_names) else ""
            out.append(f"{b.alias}{comp.COMPOSE_TAG_SEP}{nm}" if nm else "")
    return out


def _compose_gauge_notes(net: comp.ComposedNetwork,
                         term) -> list[str]:
    """
    What the reader has to be told about the gauge, beyond `COMPOSED_BASELINE_TEXT`.

    The gauge statement itself is `pkg_rlc_attrib`'s and is printed by the
    report header (`Decomposition.baseline_gauge`, `ColdStart` /
    `format_cold_start`).  What that string cannot know is whether this
    particular command line declared any cross-file link at all: with none, the
    policy selects nothing, the baseline IS all-open, and the report is back to
    the exactly-zero failure above -- with the added twist that the gauge line
    on the header claims otherwise.  The island warning inside `build_context`
    fires in that case, but only for elements, and a composition with no
    elements in the far file has nothing for it to name.

    The other note is the port NUMBERING, and it is unconditional.  Everything
    in this section that names a group names it by the flag the user typed
    ('--gnd PKG.4'), which is already scoped; but `pkg_rlc_attrib.Element`
    describes itself as 'ground port 10' -- a GLOBAL index, because an Element
    is a stamp on the combined Y and knows nothing about files.  Rather than
    thread a labeller through every construction site in that module, the map
    is stated here, once, next to the report that uses it.
    """
    out = [
        "port numbers inside an ELEMENT description ('ground port 10', "
        "'short 2-7') are GLOBAL indices into the combined network, because an "
        "element is a stamp on the combined Y. The map is "
        + "; ".join(f"{b.alias} = global "
                    + collapse_ports(list(range(b.offset + 1,
                                                b.offset + b.nports + 1)))
                    for b in net.blocks)
        + ". Group labels and --attribute-group flat use the scoped form "
          "instead."]
    cross = [c for c in term.couplings
             if net.block_of_port(c.port_i + 1).alias
             != net.block_of_port(c.port_j + 1).alias]
    if cross:
        return out
    return out + [
        "no --compose-link and no --compose-map, so NO cross-file link exists "
        "for the composed-network baseline to absorb: the files are stacked "
        "but not connected, the baseline is back to all-open, and every "
        "element inside "
        + ", ".join(b.alias for b in net.blocks[1:])
        + " will contribute EXACTLY ZERO with the reconciliation residual "
          "still reading healthy. Connect them before reading anything below."]


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
    # The same check for the cold-start family, kept separate so the message
    # names the parent flag the user actually forgot: "--cold-start-csv only
    # means something with --attribute" would send them to the wrong report.
    cs_dependents = _cold_dependent_flags(args)
    if cs_dependents and not args.cold_start:
        print("ERROR: " + ", ".join(cs_dependents)
              + (" only means" if len(cs_dependents) == 1 else " only mean")
              + " something with --cold-start VICTIM,AGGRESSOR "
                "(e.g. --cold-start vic,agg)", file=sys.stderr)
        return 2
    # And the third member of the family.  Same shape, same reason.
    cm_dependents = _compose_dependent_flags(args)
    if cm_dependents and not args.compose:
        print("ERROR: " + ", ".join(cm_dependents)
              + (" only means" if len(cm_dependents) == 1 else " only mean")
              + " something with --compose [ALIAS=]FILE, which is what puts a "
                "second file in the network (e.g. --compose PKG=package.s60p)",
              file=sys.stderr)
        return 2
    if args.compose and args.short.strip():
        # --short is 'a-b,c-d' and both separators are already spoken for on a
        # composed network: '-' is parse_port_range's dash range and a file tag
        # would have to sit inside the group.  --compose-link says the same
        # thing unambiguously and scopes both sides, so there is nothing to
        # extend the old grammar for.
        print("ERROR: --short does not work on a composed network -- 'a-b' "
              "cannot say which file each side belongs to, and '-' is already "
              "a port range. Write --compose-link \"EM.3 short_to EM.4\" "
              "instead; it takes a tag on each side and ranges on both.",
              file=sys.stderr)
        return 2

    net: comp.ComposedNetwork | None = None
    if args.compose:
        try:
            net = _compose_load(args)
        except TouchstoneParseError as e:
            print(str(e), file=sys.stderr)
            return 1
        except comp.ComposeError as e:
            print(str(e), file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        ts = _ComposedView(net)
        Y = net.Y
        _compose_print_network(net)
    else:
        try:
            ts = parse_touchstone(args.file, force_nports=args.force_nports,
                                  lenient=args.lenient)
        except TouchstoneParseError as e:
            # The report already names a line, a verdict and a next step;
            # printing it beats the bare traceback this used to raise, which
            # told nobody whether the file or this tool was at fault.
            print(str(e), file=sys.stderr)
            return 1
        print(f"Loaded {ts.source_path}")
        for line in ts.summary_lines()[1:]:
            print(line)
        Y = s_to_y(ts.s, ts.z0)

    # The default scope for every port field: the file the positional argument
    # named.  That is the measured affordable option -- a per-row file column
    # costs 451 px against a 431 px editor viewport and two cost 497 px -- so
    # only the CROSSING endpoint carries a tag, and the near file's ports keep
    # reading exactly as they did on one file.
    scope = net.blocks[0].alias if net is not None else ""

    # Which files had their ground set folded into the pre-reduction.  Those
    # come back from the reference check as "declares no ground port", which is
    # true and would otherwise read as an accusation; the check's own message
    # cannot know, because by then the ports are gone.
    gnd_folded: set[str] = set()
    if net is not None:
        for spec in (args.compose_gnd or []):
            head = (spec or "").strip().partition(
                comp.COMPOSE_TAG_SEP)[0].strip().lower()
            gnd_folded.update(b.alias for b in net.blocks
                              if b.alias.lower() == head)

    if net is not None and args.compose_propose:
        try:
            who = [t.strip() for t in args.compose_propose.split(",")]
            if len(who) != 2 or not who[0] or not who[1]:
                raise ValueError(
                    f"expected exactly two file tags separated by ONE comma, "
                    f"got {len(who)} field(s) in '{args.compose_propose}'. "
                    "The tags are the ones in the port map above")
            prop = _compose_propose(net, who[0], who[1])
        except (comp.ComposeError, ValueError) as e:
            print(f"ERROR: --compose-propose: {e}", file=sys.stderr)
            return 2
        _compose_print_proposal(net, prop)
        if args.compose_propose_csv:
            try:
                _compose_write_proposal_csv(args.compose_propose_csv, net,
                                            prop)
            except OSError as e:
                print(f"ERROR: --compose-propose-csv: {e}", file=sys.stderr)
                return 1
            print(f"\nWrote proposal: {args.compose_propose_csv}   "
                  "(review it, then commit what you accept with "
                  "--compose-map)")
        # A proposal stops before anything is connected, so any flag that
        # would have connected or exported something did NOT run.  Saying so
        # is the difference between "it stopped" and "it ignored me".
        idle = [f for f, on in (("--compose-link", args.compose_link),
                                ("--compose-map", args.compose_map),
                                ("--compose-export", args.compose_export))
                if on]
        if idle:
            for line in _attr_wrap(
                    "NOTE: " + ", ".join(idle) + " did NOT run -- "
                    "--compose-propose stops before anything is connected. "
                    "Drop --compose-propose to act on them."):
                print(line)
        return 0

    try:
        if net is not None:
            a = _compose_ports(net, args.porta, scope)
            b = _compose_ports(net, args.portb, scope)
            g = _compose_ports(net, args.gnd, scope)
            v = _compose_ports(net, args.vdd, scope)
            sp: list[tuple[int, int]] = []      # --short refused above
        else:
            a = parse_port_range(args.porta)
            b = parse_port_range(args.portb)
            g = parse_port_range(args.gnd)
            v = parse_port_range(args.vdd)
            sp = parse_short_pairs(args.short)
    except comp.ComposeError as e:
        print(str(e), file=sys.stderr)
        return 2

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
            gspecs = ([_compose_mport_global(net, s, scope) for s in specs]
                      if net is not None else specs)
            mports = [parse_mport_spec(s) for s in gspecs]
            term = build_terminations_coupling(mports, g, sp,
                                               nports=ts.nports)
        except comp.ComposeError as e:
            print(str(e), file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {_compose_scope_error(net, e)}", file=sys.stderr)
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
        if args.cold_start:
            print("ERROR: --cold-start is only valid with --mode coupling: it "
                  "names a VICTIM and an AGGRESSOR measurement port, and "
                  "those exist only where --mport defines them (e.g. --mode "
                  "coupling --mport \"vic = 1\" --mport \"agg = 2\" "
                  "--cold-start vic,agg)", file=sys.stderr)
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

    # The composed-network gauge for --attribute / --cold-start (R2-8), and the
    # notes that go with it.  None on a single file, where there is nothing to
    # gauge.  Both are set BELOW, once the links are in `term`: the "no link
    # declared" note is a property of the assembled spec, not of the flags.
    attr_baseline: "attrib.BaselineLinks | None" = None
    attr_notes: list[str] = []
    attr_link_src: dict[int, str] = {}

    # The cross-file links, for EVERY mode.  They are ShortPair /
    # LumpedBetween on the composed indices and go into the same
    # TerminationSet as everything else, which is what lets modes 1, 2 and the
    # coupling path all work on a composition without a line of their own.
    if net is not None:
        try:
            links, echo, link_specs, link_src = _compose_links(args, net, scope)
        except comp.ComposeError as e:
            print(str(e), file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        term.couplings.extend(links)
        attr_baseline = _compose_baseline(net)
        attr_notes = _compose_gauge_notes(net, term)
        attr_link_src = link_src
        if echo:
            print("\nConnections:")
            for line in echo:
                print(line)
        else:
            for line in _attr_wrap(
                    "NOTE: no --compose-link and no --compose-map, so these "
                    "files are stacked but NOT connected to each other. The "
                    "only thing they share is the reference node the stack "
                    "welded, which is exactly the configuration the "
                    "reference-node check below reports on."):
                print(line)
        if args.compose_export:
            try:
                _compose_export(args, net, scope, link_specs)
            except comp.ComposeError as e:
                print(str(e), file=sys.stderr)
                return 2
            except ValueError as e:
                # A bad port spec is a usage error (2); a file that will not
                # open is not (1).  Same split as the rest of this CLI.
                print(f"ERROR: --compose-export: {e}", file=sys.stderr)
                return 2
            except OSError as e:
                print(f"ERROR: --compose-export: {e}", file=sys.stderr)
                return 1

    f_target_hz = args.freq * 1e9

    # ---------------- coupling mode ----------------
    if args.mode == "coupling":
        try:
            if net is not None:
                # solve_composed is compute_z_matrix plus the reference-node
                # check, and there is deliberately no way to turn the check
                # off.  It costs two single-frequency solves per file.
                sol = comp.solve_composed(net, term, marker_hz=f_target_hz)
                Zmat, names, warns = sol.Zmat, sol.port_names, sol.warnings
                _compose_print_reference(sol, gnd_folded)
            else:
                Zmat, names, warns = compute_z_matrix(Y, ts.freqs, term)
        except comp.ComposeError as e:
            print(str(e), file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"ERROR: {_compose_scope_error(net, e)}", file=sys.stderr)
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
            # `gspecs`, not `specs`: on a composed network the --mport text
            # carries file tags ('vic = EM.1') and `_attr_sources` PARSES what
            # it is given with parse_mport_spec, which reads bare integers.
            # Measured before this line existed: ValueError "invalid literal
            # for int() with base 10: 'EM.1'" -- a traceback out of a report
            # the user had already paid the coupling solve for.  The label
            # stays the text they typed.
            rc = _run_attribution(args, ts, Y, term, gspecs, g, sp, names,
                                  baseline=attr_baseline,
                                  extra_notes=attr_notes,
                                  spec_labels=specs, net=net,
                                  link_sources=attr_link_src)
            if rc:
                return rc

        if args.cold_start:
            # AFTER the attribution, on the same principle: --attribute
            # explains the M and k printed above and has to stay next to them,
            # while --cold-start reports on a DIFFERENT network (all-open, with
            # every declaration set aside) and would otherwise sit between the
            # coupling report and its own explanation.  The two are not
            # mutually exclusive -- they answer different questions and each
            # names its own baseline -- and refusing the combination would only
            # force the file to be read and inverted twice (measured: 132 ms to
            # parse and 675 ms for s_to_y on a 16 MB, 153-port file).
            rc = _run_cold_start(args, ts, Y, term, names,
                                 baseline=attr_baseline,
                                 extra_notes=attr_notes, net=net)
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
            _write_coupling_csv(args.csv, ts, args, Zmat, names, net=net)
            print(f"\nWrote CSV: {args.csv}")
        return 0

    # ---------------- legacy single-measurement modes ----------------
    try:
        if net is not None:
            # Zmat[:, 0, 0] IS compute_z -- it is a thin wrapper over
            # compute_z_matrix returning the first measurement port's self
            # impedance, and the named builders can only ever produce one
            # measurement port.  Going through solve_composed instead is what
            # puts the mandatory reference-node check on modes 1, 2 and 3 as
            # well as on the coupling path.
            sol = comp.solve_composed(net, term, marker_hz=f_target_hz)
            Z, warns = sol.Zmat[:, 0, 0], sol.warnings
            _compose_print_reference(sol, gnd_folded)
        else:
            Z, warns = compute_z(Y, ts.freqs, term)
    except comp.ComposeError as e:
        print(str(e), file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"ERROR: {_compose_scope_error(net, e)}", file=sys.stderr)
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
            _compose_csv_header(fh, net)
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
        # Every file in the composition, not just the positional one: a
        # diagnosis that silently covered one of three files would answer
        # "your files are fine" about a set it never opened, which is the
        # exact shape of wrong answer this flag exists to prevent.
        paths = [args.file] + [_compose_file_arg(s)[1]
                               for s in (args.compose or [])]
        worst = FAULT_NONE
        for i, path in enumerate(paths):
            if len(paths) > 1:
                print(("" if i == 0 else "\n") + "=" * 74)
                print(f"[{i + 1}/{len(paths)}] {path}")
                print("=" * 74)
            kind, report = check_touchstone(path,
                                            force_nports=args.force_nports)
            print(report)
            if kind != FAULT_NONE:
                worst = kind
        return 0 if worst == FAULT_NONE else 1

    if args.cli:
        return _run_cli(args)

    if args.compose or _compose_dependent_flags(args):
        # Every other flag on this parser is silently ignored without --cli,
        # which is a pre-existing wart.  This one is not allowed to join it:
        # the GUI has no multi-file composition, so opening it here would
        # discard the second file and every link with it and then show a
        # perfectly ordinary single-file window -- the same shape of silent
        # wrong answer the whole feature exists to remove.
        print("ERROR: --compose needs --cli. The GUI cannot compose several "
              "files yet, so launching it here would silently drop every file "
              "but the first, and every connection with them.",
              file=sys.stderr)
        return 2

    # Launch GUI
    from pkg_rlc_gui import App
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
