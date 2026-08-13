"""
_cli_capture.py  --  Capture the CURRENT stdout, stderr and exit code of the
command line, byte for byte, for every documented invocation of it.

This is a SCRIPT plus a case registry, NOT a unittest module: the leading
underscore keeps it out of `unittest discover` (same convention as
_golden_capture.py, _render_capture.py and _smoke.py).

WHY IT EXISTS.  `pkg_rlc_extractor.py` is ~4400 lines of which the large
majority is print statements -- `_print_coupling_report`, the nine
`_attr_print_*` sections, the five `_cold_print_*` sections, the six
`_compose_print_*` / `_compose_*_csv` writers -- and until this file existed
NOTHING pinned a single character of any of it.  The golden reference pins the
numbers (`golden_legacy.npz`) and the render reference pins the GUI's results
pane (`render_reference.json`); the CLI's own report was the one large rendered
surface in the repo with no reference at all.  A later refactor deletes the
CLI's duplicate formatters and routes them through a shared module, and without
this capture that is blind surgery: every one of those functions produces
plausible text whether or not it is the text it produced yesterday.

WHAT IS RECORDED, per case:

    * the argv, verbatim (with {OUT} still in it -- see below)
    * the exit code
    * stdout, as a list of lines
    * stderr, as a list of lines
    * the full text of any file the case wrote (CSV, exported Touchstone),
      also as a list of lines

Lines, not one string, on purpose: the reference files are read by a human
looking at a unified diff to find out what a refactor broke, and a JSON string
holding 300 `\\n` escapes is not readable.  `"\\n".join(lines)` is the exact
text, so nothing is lost.

DETERMINISM IS THE WHOLE JOB.  A flaky golden is worse than a missing one, so
everything that can vary between two runs is normalised HERE, in the capture,
rather than tolerated in the comparison:

    * absolute paths -- the repo root becomes `<ROOT>`, the scratch directory
      becomes `<OUT>`.  Every path that reaches the output does so through
      argv, so the substitution list is built from argv itself and is exact
      rather than a guess at what a path looks like.
    * `\\` vs `/`.  `parse_touchstone` records `str(Path(filepath))`, so
      `tests/fixtures/pi_2port.s2p` is echoed back as `tests\\fixtures\\...` on
      Windows and unchanged on POSIX.  Both spellings of every argv path are in
      the substitution list, mapping to the forward-slash one.
    * CRLF.  Everything is normalised to `\\n` before it is split.
    * the terminal width.  argparse wraps its usage and `--help` output to
      `shutil.get_terminal_size().columns`, which is the console the capture
      happened to run in.  `COLUMNS` is pinned to 80 for the duration.

One more is normalised and it is worth naming separately: the `[WinError 2]
<a localised sentence>` inside an OSError.  That sentence belongs to the
operating system and this box answers in Chinese, so the CLI's own words around
it are kept and the OS's are replaced by `[OS-ERROR]`.  The path inside it
survives.

Two things are deliberately NOT normalised, because normalising them would
throw away the reference's whole value: the numbers, and the order of anything.
If a set of strings were ever iterated into the output, PYTHONHASHSEED would
make two capture runs differ -- which is why `main()` below captures everything
TWICE and refuses to write the reference unless the two agree, and why nothing
here pins the hash seed.

Run it to (re)generate the reference:

    python tests/_cli_capture.py

and to check determinism without writing anything:

    python tests/_cli_capture.py --verify

Both forms capture everything twice in the same process and compare; the
reference is only written when they agree byte for byte.  `--verify` then stops
without writing.  The second half of the determinism proof is
`tests/test_cli_golden.py`, which replays every case in a FRESH process and
compares against what this one wrote.

Regenerate ONLY in the same commit that justifies moving the reference.  A
failure in tests/test_cli_golden.py means the CLI's output changed.  If that
was not intended, fix the change -- do not regenerate the reference to make the
test pass.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ============================================================================
# Locations
# ============================================================================

REFERENCE_DIR = _HERE / "fixtures" / "cli_reference"
INDEX_NAME = "index.json"

#: Fixture paths are written with FORWARD SLASHES and relative to the repo
#: root, and the capture chdirs there.  That is what keeps the argv itself
#: portable; the OS spelling the CLI echoes back is normalised out below.
FIX = "tests/fixtures"

#: The scratch directory placeholder.  Every output path a case asks for, and
#: every input file a case needs that is not a shipped fixture, lives under it.
OUT = "{OUT}"

#: Fixtures that tests/generate_test_snp.py knows how to (re)create.  Same list
#: and same reason as _golden_capture.GENERATED_FIXTURES.
GENERATED_FIXTURES = [
    "shunt_rl_1port.s1p",
    "shunt_c_1port.s1p",
    "pi_2port.s2p",
    "diff_pair_4port.s4p",
    "decap_4port.s4p",
    "pi_2port_renamed.txt",
    "diff_pair_4port_renamed.dat",
]


# ============================================================================
# Scratch inputs
#
# Files a case needs that the repo does not ship, written into {OUT} before the
# case runs.  They are LITERAL TEXT here rather than generated, because a
# reference is only as reproducible as its inputs: a fixture built by running
# code is a fixture that moves when that code moves.
# ============================================================================

SCRATCH_INPUTS: dict[str, str] = {
    # A data line with a non-numeric token.  The default is a hard refusal
    # (Touchstone is a positional stream); --lenient drops the token and says
    # the result is suspect.
    "junk_token.s2p": (
        "! Synthetic 2-port with one non-numeric token on line 4\n"
        "# HZ S RI R 50\n"
        "1.000000000e+06  0.10 0.00  0.20 0.00  0.20 0.00  0.10 0.00\n"
        "2.000000000e+06  0.10 0.00  0.20 XXXX  0.20 0.00  0.10 0.00\n"
        "3.000000000e+06  0.10 0.00  0.20 0.00  0.20 0.00  0.10 0.00\n"
    ),
    # Ends mid-record: the token count is not divisible by the record length,
    # which is what the second-pass diagnosis turns into a line number.
    "truncated.s2p": (
        "! Synthetic 2-port truncated mid-record\n"
        "# HZ S RI R 50\n"
        "1.000000000e+06  0.10 0.00  0.20 0.00  0.20 0.00  0.10 0.00\n"
        "2.000000000e+06  0.10 0.00  0.20 0.00  0.20\n"
    ),
    # Touchstone 2.0, which is refused in lenient mode too: read as v1 the
    # numbers inside [Number of Ports] land in the data stream.
    "version2.s2p": (
        "! Synthetic Touchstone 2.0 header\n"
        "[Version] 2.0\n"
        "# GHZ S MA R 50\n"
        "[Number of Ports] 2\n"
        "[Network Data]\n"
        "1.0  0.1 0.0  0.2 0.0  0.2 0.0  0.1 0.0\n"
    ),
    # A --compose-map in the format --compose-propose-csv writes.
    "links.csv": (
        "a,b,element,note\n"
        "F1.1,PKG.1,short,the die pad onto the package pad\n"
        "F1.2,PKG.2,R=0.5 L=1n,the return lead\n"
        "#F1.2,,,unmatched -- left commented out on purpose\n"
    ),
    # A --compose-map whose header has no 'b' column.
    "links_no_b.csv": (
        "a,element\n"
        "F1.1,short\n"
    ),
}


# ============================================================================
# The case registry
# ============================================================================

@dataclass(frozen=True)
class CliCase:
    """One invocation of `pkg_rlc_extractor.main(argv)`.

    `argv` carries `{OUT}` verbatim; the scratch directory is substituted in
    at run time and normalised back out of the captured text, so the reference
    is the same on every box and in every temp directory.

    `artifacts` names files under {OUT} whose full text is captured too.  A CSV
    writer is a formatter like any other and the refactor that motivates this
    file touches four of them.
    """
    name: str
    describe: str
    argv: tuple[str, ...]
    artifacts: tuple[str, ...] = field(default=())


def _c(name: str, describe: str, *argv: str,
       artifacts: tuple[str, ...] = ()) -> CliCase:
    return CliCase(name=name, describe=describe, argv=tuple(argv),
                   artifacts=artifacts)


# The four fixtures the matrix leans on, named once so a case reads as a
# sentence rather than as a path.
PI = f"{FIX}/pi_2port.s2p"
DIFF = f"{FIX}/diff_pair_4port.s4p"
DECAP = f"{FIX}/decap_4port.s4p"
RL1 = f"{FIX}/shunt_rl_1port.s1p"
C1 = f"{FIX}/shunt_c_1port.s1p"
G2 = f"{FIX}/coupled_2port_gndref.s2p"
NEG2 = f"{FIX}/coupled_2port_negM.s2p"
D4 = f"{FIX}/coupled_4port_diff.s4p"
F4 = f"{FIX}/coupled_4port_float.s4p"
RENAMED = f"{FIX}/pi_2port_renamed.txt"


CASES: list[CliCase] = [

    # ---------------------------------------------------------------- help
    _c("help", "--help: the whole flag surface, wrapped at COLUMNS=80",
       "--help"),
    _c("no_args_needs_cli", "--cli with no file at all",
       "--cli"),
    _c("bad_flag", "an unrecognised flag is argparse's exit 2",
       "--cli", PI, "--not-a-flag"),
    _c("bad_mode", "--mode takes three values and says which",
       "--cli", PI, "--mode", "differential"),

    # ---------------------------------------------------- mode gnd (mode 1)
    _c("gnd_1port_rl", "mode gnd on a 1-port shunt R+L, default 0.1 GHz",
       "--cli", RL1, "--mode", "gnd", "--porta", "1"),
    _c("gnd_1port_c", "mode gnd on a 1-port shunt C (negative L, sign kept)",
       "--cli", C1, "--mode", "gnd", "--porta", "1"),
    _c("gnd_pi_open", "mode gnd, port 2 left open -> one port Schur'd away",
       "--cli", PI, "--mode", "gnd", "--porta", "1", "--freq", "1.0"),
    _c("gnd_pi_grounded", "mode gnd with --gnd, i.e. the row/col dropped",
       "--cli", PI, "--mode", "gnd", "--porta", "1", "--gnd", "2",
       "--freq", "1.0"),
    _c("gnd_diff_range", "mode gnd with a start:step:stop ground range",
       "--cli", DIFF, "--mode", "gnd", "--porta", "1", "--gnd", "2:1:4",
       "--freq", "5.0"),
    _c("gnd_diff_multiport_a", "mode gnd with a multi-port signal group",
       "--cli", DIFF, "--mode", "gnd", "--porta", "1,2", "--freq", "5.0"),
    _c("gnd_vdd_deprecated", "--vdd prints its deprecation note and unions",
       "--cli", DIFF, "--mode", "gnd", "--porta", "1", "--gnd", "3",
       "--vdd", "4", "--freq", "5.0"),
    _c("gnd_missing_porta", "mode gnd with no --porta",
       "--cli", PI, "--mode", "gnd"),
    _c("gnd_port_out_of_range", "a port past the file's port count",
       "--cli", PI, "--mode", "gnd", "--porta", "5"),
    _c("gnd_renamed_extension", "content sniffing beats the .txt extension",
       "--cli", RENAMED, "--mode", "gnd", "--porta", "1", "--freq", "1.0"),
    _c("gnd_force_nports", "--force-nports bypasses the sniffer",
       "--cli", PI, "--mode", "gnd", "--porta", "1", "--force-nports", "2",
       "--freq", "1.0"),
    _c("gnd_force_nports_wrong", "--force-nports with the wrong count",
       "--cli", PI, "--mode", "gnd", "--porta", "1", "--force-nports", "3"),

    # ---------------------------------------------------- mode p2p (2 and 3)
    _c("p2p_pi", "mode p2p across a pi network",
       "--cli", PI, "--mode", "p2p", "--porta", "1", "--portb", "2",
       "--freq", "1.0"),
    _c("p2p_diff_gnd", "mode p2p with the far end grounded",
       "--cli", DIFF, "--mode", "p2p", "--porta", "1", "--portb", "2",
       "--gnd", "3,4", "--freq", "5.0"),
    _c("p2p_diff_short", "mode p2p + --short, i.e. the mode-3 builder",
       "--cli", DIFF, "--mode", "p2p", "--porta", "1", "--portb", "2",
       "--short", "3-4", "--freq", "5.0"),
    _c("p2p_short_group", "a three-port short group ('1-2-3'), chained pairs",
       "--cli", DIFF, "--mode", "p2p", "--porta", "1", "--portb", "4",
       "--short", "1-2-3", "--freq", "5.0"),
    _c("p2p_missing_portb", "mode p2p with only --porta",
       "--cli", PI, "--mode", "p2p", "--porta", "1"),
    _c("p2p_mport_refused", "--mport is coupling-only",
       "--cli", PI, "--mode", "p2p", "--porta", "1", "--portb", "2",
       "--mport", "x = 1"),

    # ------------------------------------------------------------- the fits
    _c("fit_inductor", "--fit inductor over a band",
       "--cli", RL1, "--mode", "gnd", "--porta", "1", "--freq", "1.0",
       "--fit", "inductor", "--fmin", "0.1", "--fmax", "2.0"),
    _c("fit_capacitor", "--fit capacitor (the _scaled_lstsq path)",
       "--cli", C1, "--mode", "gnd", "--porta", "1", "--freq", "1.0",
       "--fit", "capacitor", "--fmin", "0.1", "--fmax", "2.0"),
    _c("fit_auto_inductive", "--fit auto choosing on an inductive port",
       "--cli", RL1, "--mode", "gnd", "--porta", "1", "--freq", "1.0",
       "--fit", "auto", "--fmin", "0.1", "--fmax", "2.0"),
    _c("fit_auto_capacitive", "--fit auto choosing on a capacitive port",
       "--cli", C1, "--mode", "gnd", "--porta", "1", "--freq", "1.0",
       "--fit", "auto", "--fmin", "0.1", "--fmax", "2.0"),
    _c("fit_without_band", "--fit with no --fmin / --fmax",
       "--cli", RL1, "--mode", "gnd", "--porta", "1", "--fit", "auto"),
    _c("fit_empty_band", "--fit over a band with no data points in it",
       "--cli", RL1, "--mode", "gnd", "--porta", "1", "--fit", "inductor",
       "--fmin", "20.0", "--fmax", "30.0"),
    _c("fit_coupling_selfz", "--fit in coupling mode fits each self impedance",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 2", "--freq", "1.0", "--fit", "inductor",
       "--fmin", "0.1", "--fmax", "2.0"),

    # ------------------------------------------------------------- the CSVs
    _c("csv_gnd", "--csv in a single-measurement mode",
       "--cli", PI, "--mode", "gnd", "--porta", "1", "--freq", "1.0",
       "--csv", f"{OUT}/gnd.csv", artifacts=("gnd.csv",)),
    _c("csv_coupling", "--csv in coupling mode: every Z_ij plus M and k",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 2", "--freq", "5.0",
       "--csv", f"{OUT}/coupling.csv", artifacts=("coupling.csv",)),

    # -------------------------------------------------------- mode coupling
    _c("coupling_two_mports", "two ground-referenced measurement ports",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 2", "--freq", "5.0"),
    _c("coupling_negative_M", "the same fixture with M < 0: the sign is kept",
       "--cli", NEG2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 2", "--freq", "5.0"),
    _c("coupling_differential", "two differential probes ('+ / -')",
       "--cli", D4, "--mode", "coupling", "--mport", "c1 = 1 / 2",
       "--mport", "c2 = 3 / 4", "--freq", "5.0"),
    _c("coupling_three_mports", "three measurement ports -> a 3x3 Z matrix",
       "--cli", DIFF, "--mode", "coupling", "--mport", "p1 = 1",
       "--mport", "p2 = 2", "--mport", "p3 = 3", "--gnd", "4",
       "--freq", "5.0"),
    _c("coupling_single_mport", "one measurement port: self impedance only",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--freq", "5.0"),
    _c("coupling_unnamed_mports", "the auto names P1, P2, ...",
       "--cli", G2, "--mode", "coupling", "--mport", "1", "--mport", "2",
       "--freq", "5.0"),
    _c("coupling_rank_deficient", "a fully floating structure: pinv + a note",
       "--cli", F4, "--mode", "coupling", "--mport", "c1 = 1 / 2",
       "--mport", "c2 = 3 / 4", "--freq", "5.0"),
    _c("coupling_no_return_path", "a probe with no return path reads NaN",
       "--cli", F4, "--mode", "coupling", "--mport", "c1 = 1 / 2",
       "--mport", "c2 = 3", "--freq", "5.0"),
    _c("coupling_gnd_range", "coupling mode with a ground range and a short",
       "--cli", DECAP, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3", "--short", "3-4",
       "--freq", "2.0"),
    _c("coupling_off_grid_freq", "a --freq between two grid points snaps",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 2", "--freq", "5.05"),
    _c("coupling_no_mport", "coupling mode with no --mport at all",
       "--cli", G2, "--mode", "coupling"),
    _c("coupling_porta_refused", "coupling mode uses --mport, not --porta",
       "--cli", G2, "--mode", "coupling", "--porta", "1",
       "--mport", "c1 = 1"),
    _c("coupling_reserved_name_A", "'A' is a reserved measurement-port name",
       "--cli", G2, "--mode", "coupling", "--mport", "A = 1",
       "--mport", "c2 = 2"),
    _c("coupling_probe_is_also_gnd", "a probe port may not also be --gnd",
       "--cli", DIFF, "--mode", "coupling", "--mport", "p1 = 1",
       "--mport", "p2 = 2", "--gnd", "1,3"),
    _c("coupling_port_out_of_range", "a measurement port past the port count",
       "--cli", G2, "--mode", "coupling", "--mport", "c1 = 1",
       "--mport", "c2 = 9"),

    # ------------------------------------------------- reading files badly
    _c("missing_file", "a path that is not there at all",
       "--cli", f"{OUT}/nope.s2p", "--mode", "gnd", "--porta", "1"),
    _c("junk_token_refused", "a non-numeric token is a hard error by default",
       "--cli", f"{OUT}/junk_token.s2p", "--mode", "gnd", "--porta", "1"),
    _c("junk_token_lenient", "--lenient drops it and says so",
       "--cli", f"{OUT}/junk_token.s2p", "--mode", "gnd", "--porta", "1",
       "--lenient", "--freq", "0.002"),
    _c("truncated_refused", "a file that ends mid-record",
       "--cli", f"{OUT}/truncated.s2p", "--mode", "gnd", "--porta", "1"),
    _c("truncated_lenient", "--lenient does not rescue a short record either",
       "--cli", f"{OUT}/truncated.s2p", "--mode", "gnd", "--porta", "1",
       "--lenient"),
    _c("version2_refused", "Touchstone 2.0 is refused",
       "--cli", f"{OUT}/version2.s2p", "--mode", "gnd", "--porta", "1"),
    _c("version2_refused_lenient", "... in lenient mode too",
       "--cli", f"{OUT}/version2.s2p", "--mode", "gnd", "--porta", "1",
       "--lenient"),

    # ------------------------------------------------------------ --diagnose
    _c("diagnose_ok", "--diagnose on a healthy file, exit 0",
       "--diagnose", PI),
    _c("diagnose_ok_4port", "--diagnose on a 4-port file",
       "--diagnose", DIFF),
    _c("diagnose_truncated", "--diagnose names the line it ends on",
       "--diagnose", f"{OUT}/truncated.s2p"),
    _c("diagnose_junk_token", "--diagnose on the non-numeric token",
       "--diagnose", f"{OUT}/junk_token.s2p"),
    _c("diagnose_version2", "--diagnose on a v2 file",
       "--diagnose", f"{OUT}/version2.s2p"),
    _c("diagnose_missing", "--diagnose on a file that is not there",
       "--diagnose", f"{OUT}/nope.s2p"),
    _c("diagnose_no_file", "--diagnose with no file argument",
       "--diagnose"),
    _c("diagnose_composition", "--diagnose covers every composed file",
       "--diagnose", G2, "--compose", f"PKG={D4}"),

    # ----------------------------------------------------------- --attribute
    _c("attr_basic", "the whole nine-section report, two ground balls",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg"),
    _c("attr_by_position", "a 1-based position in the --mport list",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "1,2"),
    _c("attr_short_element", "a --short declaration is an element too",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3", "--short", "3-4",
       "--freq", "5.0", "--attribute", "vic,agg"),
    _c("attr_alts", "candidate terminations for the sensitivity scan",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-alt", "L=0.3n",
       "--attribute-alt", "R=0.5,L=1n", "--attribute-alt", "open"),
    _c("attr_alt_structural_only", "'open' and 'ideal' spelled out explicitly",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-alt", "open",
       "--attribute-alt", "ideal"),
    _c("attr_alt_bad_spacing", "'R=5 m' would silently mean 5 ohm",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-alt", "R=5 m"),
    _c("attr_gm_diag_spec", "--attribute-ground-model diag:L=1n",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-ground-model", "diag:L=1n"),
    _c("attr_gm_shared", "--attribute-ground-model shared:L=1n (the dense Zt)",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-ground-model", "shared:L=1n"),
    _c("attr_gm_shared_r", "a shared resistive return",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-ground-model", "shared:R=0.2"),
    _c("attr_gm_not_applied", "a ground model with no shunt element to model",
       "--cli", D4, "--mode", "coupling", "--mport", "vic = 1 / 2",
       "--mport", "agg = 3 / 4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-ground-model", "shared:L=1n"),
    _c("attr_gm_bad", "an unparseable ground model",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-ground-model", "sideways:L=1n"),
    _c("attr_freqs", "--attribute-freqs re-ranks across the band",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-freqs", "1,5,10"),
    _c("attr_freqs_bad", "--attribute-freqs with a non-numeric entry",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-freqs", "1,five"),
    _c("attr_group_flat", "--attribute-group flat: one element per group",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-group", "flat"),
    _c("attr_group_name", "--attribute-group name: the naming HEURISTIC",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-group", "name"),
    _c("attr_group_bad", "--attribute-group takes three values",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--attribute", "vic,agg",
       "--attribute-group", "column"),
    _c("attr_csv", "--attribute-csv: every record, uncapped",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--attribute-alt", "L=0.3n",
       "--attribute-csv", f"{OUT}/attr.csv", artifacts=("attr.csv",)),
    _c("attr_singular_baseline", "a singular baseline folds and says which",
       "--cli", F4, "--mode", "coupling", "--mport", "vic = 1 / 2",
       "--mport", "agg = 3 / 4", "--freq", "5.0",
       "--attribute", "vic,agg"),
    _c("attr_no_elements", "nothing declared: the bare EM term on its own",
       "--cli", D4, "--mode", "coupling", "--mport", "vic = 1 / 2",
       "--mport", "agg = 3 / 4", "--freq", "5.0", "--attribute", "vic,agg"),
    _c("attr_low_frequency", "1 MHz, where the residual floor bites",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "0.001",
       "--attribute", "vic,agg"),
    _c("attr_wrong_mode", "--attribute outside coupling mode",
       "--cli", DIFF, "--mode", "gnd", "--porta", "1",
       "--attribute", "vic,agg"),
    _c("attr_dependents_without_parent", "--attribute-* without --attribute",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--attribute-alt", "L=1n", "--attribute-csv", f"{OUT}/x.csv"),
    _c("attr_same_port", "victim and aggressor must differ",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--attribute", "vic,vic"),
    _c("attr_unknown_name", "a name that is not a measurement port",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--attribute", "vic,ghost"),
    _c("attr_bad_pair", "--attribute wants exactly two comma-separated names",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--attribute", "vic"),
    _c("attr_one_mport", "--attribute needs two measurement ports to exist",
       "--cli", G2, "--mode", "coupling", "--mport", "vic = 1",
       "--attribute", "vic,agg"),

    # ---------------------------------------------------------- --cold-start
    _c("cold_basic", "the four-step screen from all-open",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg"),
    _c("cold_ignores_spec", "every declaration is set aside, and named",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3", "--short", "3-4",
       "--freq", "5.0", "--cold-start", "vic,agg"),
    _c("cold_by_position", "positions instead of names",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "1,2"),
    _c("cold_top_2", "--cold-start-top caps the pair scan",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-top", "2"),
    _c("cold_top_1", "--cold-start-top below 2 is refused",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-top", "1"),
    _c("cold_cumulative_1", "--cold-start-cumulative caps the greedy curve",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-cumulative", "1"),
    _c("cold_cumulative_all", "--cold-start-cumulative 0 means every candidate",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-cumulative", "0"),
    _c("cold_cumulative_negative", "a negative cap is refused",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-cumulative", "-3"),
    _c("cold_csv", "--cold-start-csv: the full uncapped screen",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg",
       "--cold-start-csv", f"{OUT}/cold.csv", artifacts=("cold.csv",)),
    _c("cold_named_family", "two candidates sharing a name family",
       "--cli", DECAP, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--freq", "5.0", "--cold-start", "vic,agg"),
    _c("cold_differential_probes", "a differential probe pair, no candidates",
       "--cli", D4, "--mode", "coupling", "--mport", "vic = 1 / 2",
       "--mport", "agg = 3 / 4", "--freq", "5.0",
       "--cold-start", "vic,agg"),
    _c("cold_with_attribute", "--attribute and --cold-start, cold start LAST",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--gnd", "3,4", "--freq", "5.0",
       "--attribute", "vic,agg", "--cold-start", "vic,agg"),
    _c("cold_wrong_mode", "--cold-start outside coupling mode",
       "--cli", DIFF, "--mode", "gnd", "--porta", "1",
       "--cold-start", "vic,agg"),
    _c("cold_dependents_without_parent", "--cold-start-* without --cold-start",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--cold-start-top", "4", "--cold-start-csv", f"{OUT}/x.csv"),
    _c("cold_same_port", "victim and aggressor must differ here too",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--cold-start", "agg,agg"),
    _c("cold_unknown_name", "a name that is not a measurement port",
       "--cli", DIFF, "--mode", "coupling", "--mport", "vic = 1",
       "--mport", "agg = 2", "--cold-start", "vic,ghost"),

    # ------------------------------------------------------------- --compose
    _c("compose_no_cli", "--compose without --cli would drop every extra file",
       "--compose", f"PKG={D4}", G2),
    _c("compose_dependents_without_parent", "--compose-* without --compose",
       "--cli", G2, "--compose-link", "F1.1 short_to PKG.1"),
    _c("compose_same_grid_no_link", "two files, identical grid, NOT connected",
       "--cli", G2, "--compose", f"PKG={D4}", "--mode", "gnd",
       "--porta", "1", "--freq", "5.0"),
    _c("compose_short_link", "one cross-file short",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--gnd", "PKG.3", "--freq", "5.0"),
    _c("compose_lumped_link", "one cross-file lumped element",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 lumped_between PKG.1 R=0.5 L=0.3n",
       "--mode", "gnd", "--porta", "F1.1", "--gnd", "PKG.3", "--freq", "5.0"),
    _c("compose_elementwise_pairs", "a range on both sides pairs elementwise",
       "--cli", D4, "--compose", f"PKG={F4}",
       "--compose-link", "F1.3,4 short_to PKG.1,2", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_length_mismatch", "a length mismatch is refused, not guessed",
       "--cli", D4, "--compose", f"PKG={F4}",
       "--compose-link", "F1.1,2,3 short_to PKG.1,2", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_alias", "--compose-alias renames the positional file",
       "--cli", G2, "--compose-alias", "EM", "--compose", f"PKG={D4}",
       "--compose-link", "EM.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "EM.1", "--freq", "5.0"),
    _c("compose_interpolated_grid", "two different sweeps -> the frequency plan",
       "--cli", G2, "--compose", f"PKG={PI}", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_coupling", "coupling mode across two files",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "coupling",
       "--mport", "vic = F1.1", "--mport", "agg = PKG.2", "--freq", "5.0"),
    _c("compose_p2p", "p2p mode across two files",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "p2p",
       "--porta", "F1.1", "--portb", "PKG.2", "--freq", "5.0"),
    _c("compose_attribute", "R2-8: the cross-file links are IN the baseline",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "coupling",
       "--mport", "vic = F1.1", "--mport", "agg = PKG.2", "--freq", "5.0",
       "--gnd", "PKG.4", "--attribute", "vic,agg"),
    _c("compose_cold_start", "the cold start needs the gauge more, not less",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "coupling",
       "--mport", "vic = F1.1", "--mport", "agg = PKG.2", "--freq", "5.0",
       "--cold-start", "vic,agg"),
    _c("compose_keep", "--compose-keep pre-reduces one file",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose-keep", "PKG.1-2",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_keep_no_tag", "--compose-keep without a file tag",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose-keep", "1-2",
       "--mode", "gnd", "--porta", "F1.1"),
    _c("compose_gnd", "--compose-gnd folds a file's ground balls in first",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose-gnd", "PKG.3,4",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_short_refused", "--short cannot say which file a side is in",
       "--cli", G2, "--compose", f"PKG={D4}", "--short", "1-2",
       "--mode", "gnd", "--porta", "F1.1"),
    _c("compose_unknown_tag", "a tag no file carries",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.1 short_to DIE.1", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_bare_port_past_home", "a bare index past the home file's count",
       "--cli", G2, "--compose", f"PKG={D4}", "--mode", "gnd",
       "--porta", "5"),
    _c("compose_link_bad_keyword", "a link with no recognised keyword",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.1 wired_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_propose", "--compose-propose prints and STOPS",
       "--cli", G2, "--compose", f"PKG={NEG2}", "--compose-propose", "F1,PKG"),
    _c("compose_propose_unmatched", "two files whose names do not correspond",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose-propose", "F1,PKG"),
    _c("compose_propose_idle_flags", "it names what it therefore did not run",
       "--cli", G2, "--compose", f"PKG={NEG2}", "--compose-propose", "F1,PKG",
       "--compose-link", "F1.1 short_to PKG.1",
       "--compose-export", f"{OUT}/never_written.s4p"),
    _c("compose_propose_csv", "--compose-propose-csv is a work list",
       "--cli", G2, "--compose", f"PKG={NEG2}", "--compose-propose", "F1,PKG",
       "--compose-propose-csv", f"{OUT}/propose.csv",
       artifacts=("propose.csv",)),
    _c("compose_propose_csv_unmatched",
       "the unmatched ports are COMMENTED rows, i.e. a work list",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose-propose", "F1,PKG",
       "--compose-propose-csv", f"{OUT}/propose_unmatched.csv",
       artifacts=("propose_unmatched.csv",)),
    _c("compose_propose_bad_arg", "--compose-propose wants exactly two tags",
       "--cli", G2, "--compose", f"PKG={NEG2}", "--compose-propose", "F1"),
    _c("compose_propose_unknown_tag", "--compose-propose naming no such file",
       "--cli", G2, "--compose", f"PKG={NEG2}", "--compose-propose", "F1,DIE"),
    _c("compose_map", "--compose-map reads the links from a CSV",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-map", f"{OUT}/links.csv", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_map_and_link", "--compose-link first, then every --compose-map row",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.1 lumped_between PKG.4 C=1p",
       "--compose-map", f"{OUT}/links.csv", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_same_file_link", "a link with both ends in ONE file",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "PKG.3 short_to PKG.4", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
    _c("compose_map_no_b_column", "a --compose-map with no 'b' column",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-map", f"{OUT}/links_no_b.csv", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_map_missing", "a --compose-map path that is not there",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-map", f"{OUT}/no_such_map.csv", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_export", "--compose-export writes the STACKED network",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0",
       "--compose-export", f"{OUT}/stacked.s6p",
       artifacts=("stacked.s6p",)),
    _c("compose_export_ports", "--compose-export-ports brings a subset out",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0",
       "--compose-export", f"{OUT}/subset.s3p",
       "--compose-export-ports", "F1.1;PKG.2-3",
       artifacts=("subset.s3p",)),
    _c("compose_export_ports_bare_after_tag",
       "a bare token after a tag is the HOME file, so this names F1.3",
       "--cli", G2, "--compose", f"PKG={D4}", "--mode", "gnd",
       "--porta", "F1.1", "--compose-export", f"{OUT}/never.s2p",
       "--compose-export-ports", "F1.1;PKG.2,3"),
    _c("compose_export_bad_ports", "an unparseable --compose-export-ports",
       "--cli", G2, "--compose", f"PKG={D4}", "--mode", "gnd",
       "--porta", "F1.1", "--compose-export", f"{OUT}/never.s2p",
       "--compose-export-ports", "PKG.99"),
    _c("compose_missing_file", "a --compose file that is not there",
       "--cli", G2, "--compose", f"PKG={OUT}/nope.s4p", "--mode", "gnd",
       "--porta", "F1.1"),
    _c("compose_csv", "--csv on a composed network carries the block map",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0", "--csv", f"{OUT}/composed.csv",
       artifacts=("composed.csv",)),
    _c("compose_coupling_csv", "the coupling CSV on a composed network",
       "--cli", G2, "--compose", f"PKG={D4}",
       "--compose-link", "F1.2 short_to PKG.1", "--mode", "coupling",
       "--mport", "vic = F1.1", "--mport", "agg = PKG.2", "--freq", "5.0",
       "--csv", f"{OUT}/composed_coupling.csv",
       artifacts=("composed_coupling.csv",)),
    _c("compose_three_files", "three files in one stack",
       "--cli", G2, "--compose", f"PKG={D4}", "--compose", f"LID={NEG2}",
       "--compose-link", "F1.2 short_to PKG.1",
       "--compose-link", "PKG.4 short_to LID.1", "--mode", "gnd",
       "--porta", "F1.1", "--freq", "5.0"),
]


def case_by_name() -> dict[str, CliCase]:
    return {c.name: c for c in CASES}


# ============================================================================
# Normalisation
#
# Everything that varies between two runs is dealt with here.  The substitution
# list is built from the case's OWN argv, so it is exact: there is no attempt
# to recognise "a path" in arbitrary text.
# ============================================================================

#: `[WinError 2] <a localised sentence>: '<path>'` out of an OSError, and its
#: POSIX `[Errno 2] ...` twin.  The number and the sentence are the operating
#: system's and the sentence is LOCALISED -- this box answers in Chinese -- so
#: pinning them would make the reference a property of the machine that
#: captured it rather than of the CLI.  What the CLI owns is the sentence
#: AROUND it and the path inside it, and both survive.
_OS_ERROR_RE = re.compile(r"\[(?:WinError|Errno) -?\d+\][^']*'")


def _both_spellings(p: str) -> list[str]:
    """
    A path as written, as the OS spells it, forward-slashed -- and REPR'd.

    The last one is not decoration: `str(FileNotFoundError)` embeds the file
    name through `repr`, so a Windows path arrives in the output with every
    separator doubled and the plain spelling does not match it.
    """
    out = [p, str(Path(p)), p.replace("\\", "/"), p.replace("/", os.sep)]
    out += [s.replace("\\", "\\\\") for s in list(out) if "\\" in s]
    seen: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.append(s)
    return seen


def substitutions(argv: tuple[str, ...], out_dir: Path) -> list[tuple[str, str]]:
    """
    (needle, replacement) pairs, longest needle first.

    Order matters twice over: the scratch directory is (usually) inside the
    system temp directory and never inside the repo, but the repo root is a
    prefix of every fixture path, so the two absolute roots go first and the
    per-argument paths after them.
    """
    subs: list[tuple[str, str]] = []
    for d, token in ((out_dir, "<OUT>"), (_ROOT, "<ROOT>")):
        for spelling in _both_spellings(str(d)):
            subs.append((spelling, token))
        # A resolved temp directory differs from the one tempfile handed back
        # on macOS (/var -> /private/var) and behind a Windows 8.3 short path.
        for spelling in _both_spellings(str(Path(d).resolve())):
            subs.append((spelling, token))
    for arg in argv:
        if "/" not in arg and "\\" not in arg:
            continue
        # The argument may be 'PKG=path' or 'a;b' -- take every slash-bearing
        # field of it rather than the whole token.
        for field_ in _split_arg_fields(arg):
            canonical = field_.replace(os.sep, "/")
            for spelling in _both_spellings(field_):
                if spelling != canonical:
                    subs.append((spelling, canonical))
    subs.sort(key=lambda kv: len(kv[0]), reverse=True)
    # Dedupe, keeping the first (longest) occurrence.
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for needle, repl in subs:
        if needle in seen:
            continue
        seen.add(needle)
        out.append((needle, repl))
    return out


def _split_arg_fields(arg: str) -> list[str]:
    fields = [arg]
    for sep in ("=", ";", ","):
        nxt: list[str] = []
        for f in fields:
            nxt.extend(f.split(sep))
        fields = nxt
    return [f for f in fields if "/" in f or "\\" in f]


def normalise(text: str, subs: list[tuple[str, str]]) -> list[str]:
    """Text as captured -> the list of lines that goes into the reference."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for needle, repl in subs:
        text = text.replace(needle, repl)
    text = _OS_ERROR_RE.sub("[OS-ERROR] '", text)
    # `<OUT>\name.csv` -> `<OUT>/name.csv`: the placeholder swallowed the
    # directory but not the separator that follows it.
    for token in ("<OUT>", "<ROOT>"):
        while token + "\\" in text:
            text = text.replace(token + "\\", token + "/")
    return text.split("\n")


# ============================================================================
# Running one case
# ============================================================================

#: An artifact longer than this keeps its head and its tail and says how many
#: lines it dropped.  stdout and stderr are NEVER elided -- they are the whole
#: subject -- but a written file can be 401 uniform data rows (a --csv sweep)
#: or 500 of them (a --compose-export), and pinning the 380 in the middle costs
#: 150 KiB of reference to guard a row format the first 120 already show.  The
#: MARKER CARRIES THE COUNT, so a change in how many rows are written is still
#: a failure; and the tail is kept because the last row is where a trailing
#: newline or an off-by-one in the loop shows up.
ARTIFACT_MAX_LINES = 140
ARTIFACT_HEAD_LINES = 120
ARTIFACT_TAIL_LINES = 20


def cap_artifact(lines: list[str]) -> list[str]:
    if len(lines) <= ARTIFACT_MAX_LINES:
        return lines
    dropped = len(lines) - ARTIFACT_HEAD_LINES - ARTIFACT_TAIL_LINES
    return (lines[:ARTIFACT_HEAD_LINES]
            + [f"<<{dropped} lines elided by _cli_capture.cap_artifact>>"]
            + lines[-ARTIFACT_TAIL_LINES:])


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    """`main(argv)` with stdout / stderr captured and SystemExit absorbed."""
    import pkg_rlc_extractor as ex

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = ex.main(argv)
        except SystemExit as e:                       # argparse's exit(2)
            code = e.code
            rc = 0 if code is None else (code if isinstance(code, int) else 1)
        except BaseException as e:                    # noqa: BLE001
            # A traceback out of the CLI is a defect, not a case -- but it is
            # a defect the reference should RECORD rather than hide, or the
            # capture run would die on the first one and pin nothing at all.
            rc = -1
            print(f"<<UNCAUGHT {type(e).__name__}: {e}>>", file=err)
    return rc, out.getvalue(), err.getvalue()


def run_case(case: CliCase, out_dir: Path) -> dict:
    """One case -> the dict that becomes its reference file."""
    argv = [a.replace(OUT, str(out_dir)) for a in case.argv]
    subs = substitutions(tuple(argv), out_dir)

    for name in case.artifacts:
        target = out_dir / name
        if target.exists():
            target.unlink()

    rc, stdout, stderr = _invoke(argv)

    artifacts: dict[str, list[str]] = {}
    for name in case.artifacts:
        target = out_dir / name
        if not target.exists():
            artifacts[name] = ["<<NOT WRITTEN>>"]
            continue
        artifacts[name] = cap_artifact(normalise(
            target.read_text(encoding="utf-8", errors="replace"), subs))

    return {
        "name": case.name,
        "describe": case.describe,
        "argv": list(case.argv),
        "returncode": rc,
        "stdout": normalise(stdout, subs),
        "stderr": normalise(stderr, subs),
        "artifacts": artifacts,
    }


@contextlib.contextmanager
def capture_environment():
    """
    cwd at the repo root, COLUMNS pinned, and a fresh scratch directory.

    cwd: every fixture path in the registry is repo-relative, which is what
    keeps the argv itself the same on every box.
    COLUMNS: argparse wraps to the terminal it finds, so without this the
    reference records the width of whoever ran the capture.
    """
    prev_cwd = Path.cwd()
    prev_cols = os.environ.get("COLUMNS")
    tmp = Path(tempfile.mkdtemp(prefix="cli_capture_"))
    try:
        os.chdir(_ROOT)
        os.environ["COLUMNS"] = "80"
        for name, text in SCRATCH_INPUTS.items():
            (tmp / name).write_text(text, encoding="utf-8", newline="\n")
        yield tmp
    finally:
        os.chdir(prev_cwd)
        if prev_cols is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = prev_cols
        shutil.rmtree(tmp, ignore_errors=True)


def capture_all() -> dict[str, dict]:
    """Every case, in registry order, in one scratch directory."""
    with capture_environment() as tmp:
        return {c.name: run_case(c, tmp) for c in CASES}


# ============================================================================
# Reading and writing the reference
# ============================================================================

def reference_path(name: str) -> Path:
    return REFERENCE_DIR / f"{name}.json"


def dumps(record: dict) -> str:
    """The exact bytes a reference file holds, for a byte-for-byte compare."""
    return json.dumps(record, indent=1, ensure_ascii=False, sort_keys=False) \
        + "\n"


def load_reference() -> dict[str, dict]:
    index = json.loads((REFERENCE_DIR / INDEX_NAME).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for name in index["cases"]:
        out[name] = json.loads(
            reference_path(name).read_text(encoding="utf-8"))
    return out


def write_reference(records: dict[str, dict]) -> int:
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    written = set()
    for name, rec in records.items():
        path = reference_path(name)
        path.write_text(dumps(rec), encoding="utf-8", newline="\n")
        written.add(path.name)
    index = {
        "cases": list(records),
        "note": "Regenerate with `python tests/_cli_capture.py`, and ONLY in "
                "the same commit that justifies moving the reference. "
                "tests/test_cli_golden.py replays every case listed here.",
    }
    (REFERENCE_DIR / INDEX_NAME).write_text(
        json.dumps(index, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    written.add(INDEX_NAME)
    stale = [p for p in REFERENCE_DIR.iterdir()
             if p.is_file() and p.name not in written]
    for p in stale:
        p.unlink()
    return len(stale)


# ============================================================================
# Script
# ============================================================================

def ensure_fixtures() -> None:
    if all((_HERE / "fixtures" / n).exists() for n in GENERATED_FIXTURES):
        return
    import generate_test_snp                                   # noqa: PLC0415
    generate_test_snp.main()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verify_only = "--verify" in args

    ensure_fixtures()

    print(f"capturing {len(CASES)} cases (twice, to prove determinism) ...")
    records = capture_all()
    again = capture_all()
    unstable = [n for n in records if dumps(records[n]) != dumps(again[n])]
    if unstable:
        print("\nNOT DETERMINISTIC -- refusing to write the reference:",
              file=sys.stderr)
        for n in unstable:
            print(f"  {n}", file=sys.stderr)
        return 1
    print("determinism: the two captures are byte-identical")

    crashed = [n for n, r in records.items() if r["returncode"] == -1]
    if crashed:
        print("\nNOTE: these cases left an UNCAUGHT exception (recorded as "
              "returncode -1):", file=sys.stderr)
        for n in crashed:
            print(f"  {n}", file=sys.stderr)

    if verify_only:
        print("--verify: nothing written")
        return 0

    stale = write_reference(records)
    total = sum(len(dumps(r).encode("utf-8")) for r in records.values())
    print(f"wrote {len(records)} cases to {REFERENCE_DIR} "
          f"({total / 1024:.1f} KiB)"
          + (f", removed {stale} stale file(s)" if stale else ""))
    by_rc: dict[int, int] = {}
    for r in records.values():
        by_rc[r["returncode"]] = by_rc.get(r["returncode"], 0) + 1
    print("exit codes: " + ", ".join(f"{k}: {v}" for k, v in
                                     sorted(by_rc.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
