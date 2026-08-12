"""
pkg_rlc_compose.py -- assemble several Touchstone files into ONE network.

The user's framing (requirement section 0) is that the connection table is a
quick TESTBENCH: they hang an EM block on a package block, add a few elements,
and the tool reports what the assembled thing measures.  The deliverable is the
ABSOLUTE number.  A before/after delta already exists as freeze-as-trace --
Calculate unconnected, freeze, connect, Calculate again, two rows side by side
-- so nothing here rebuilds it.

The maths is the existing pipeline and not a new one:

    Y_combined = block_diag(Y_A, Y_B, ...)       (Na + Nb + ...) square
    a cross-file link          = ShortPair / LumpedBetween on offset indices
    everything after that      = compute_z_matrix, unchanged

Each file goes S -> Y with ITS OWN z0 and the blocks are then stacked.  There is
no renormalisation step: Y is z0-invariant, measured at 1.049e-17 max absolute
difference between Y computed from z0 = 50 and from z0 = 75 on the same S.  z0
only comes back for an export, where a reference impedance has to be declared
again (see write_composed_touchstone).

WHAT IS NOT FREE, AND WHY THIS MODULE IS MORE THAN A CALL TO block_diag
======================================================================

1.  block_diag WELDS THE REFERENCE NODES.  An n-port Touchstone Y is the matrix
    with its own reference already eliminated, so stacking two of them
    identifies ref_A with ref_B at zero impedance.  Measured here on a 2 nH die
    coil + a 100 pH package trace + a 100 pH package ground lead, read at the
    5.2 GHz sample nearest a 5.205 GHz marker (tests/test_compose.py
    TestReferenceCheck builds exactly this network):

        die return brought out as a PORT, tied to the package ground pad
                                                    L_eff = 2.2501 nH
        die return IS the EM reference:
            package ground pad GROUNDED             L_eff = 2.1454 nH
            package ground pad OPEN                 L_eff = 2.1454 nH
            package ground pad through 1 nH         L_eff = 2.1454 nH
            -> spread 0.000e+00, BIT-IDENTICAL

    In the second configuration the package's whole ground network is
    unreachable and nothing in the tool said so.  That is the same failure shape
    the feature exists to settle, arriving through the door the feature opened.
    `reference_check` is therefore MANDATORY output, not an option: it perturbs
    each file's declared ground set with a series inductor and re-solves, and if
    the answer does not move AT ALL it names the file whose ground network is
    not in the circuit.  See ReferenceCheck.

2.  THE FREQUENCY GRIDS ALMOST NEVER MATCH, and getting that wrong is a wrong
    answer with a plausible shape.  See align_frequencies -- span intersection,
    no extrapolation, an identical-grid fast path with a RELATIVE tolerance
    (a file in GHz and one in Hz never compare equal as floats), interpolation
    of S rather than Y or Z, and a phase-step check instead of the |S| check
    that cannot fire.

3.  A COMBINED NETWORK IS BIG.  _freq_batch collapses exactly where it is first
    needed: 16 ports gives 64 frequencies per chunk, 60 gives 4, 76 gives 2,
    153 gives 1, 316 gives 1.  Pre-reduction is therefore step zero, not an
    optimisation.  Measured on this box, 16-port EM + 300-port package, 101
    frequencies, 6 cross links:

        full solve       4486 ms   (44.4 ms/freq)
        pre-reduce once  3565 ms   (35.3 ms/freq)
        reduced solve       2.6 ms ( 0.026 ms/freq)   -> 1714x per re-solve
        answers agree to 4.290e-15 relative

    and at the smaller 16 + 60 = 76 ports, 601 frequencies: 76.5 ms full,
    12.4 ms reduced, 6.2x, agreeing to 3.211e-15.  The edit/recompute loop is
    where this pays: the reduction is done once at load, every later spec change
    re-solves the small network.  See ComposeInput.keep / reduce_block_y.

4.  "port 305" IS UNACTIONABLE on a 316-port combined network.  Every port here
    carries a file tag (`F2.13`), every message this module emits names the
    file, and ComposedNetwork.describe_port turns a global index back into the
    file, the local port number and the port's own name.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================

* It does not renormalise z0 (point 1 above -- there is nothing to do).
* It does not re-check max |S| after interpolating.  The set of S with
  sigma_max <= 1 is convex, so any convex combination of passive samples is
  still passive; a post-interpolation max |S| check is structurally incapable of
  firing.  It is not a passivity test either -- all off-diagonals at 0.6 gives
  max |S_ij| = 0.6 and sigma_max = 1.80.  What interpolation DOES break is
  phase, and that is what is checked.
* It does not guess a port correspondence.  Proposal/acceptance helpers are
  requirement R2-5 and live elsewhere; nothing here infers a mapping.

Imports pkg_rlc_core and nothing else in this repo, on purpose: the reduction,
the DSL and the termination model all live there and this is a layer above them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from pkg_rlc_core import (
    DEFAULT_Z0,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    Open,
    ShortPair,
    TerminationSet,
    TouchstoneData,
    Vdd,
    collapse_ports,
    compute_z_matrix,
    format_freq,
    format_si,
    parse_port_range,
    s_to_y,
    y_series_rlc,
    y_to_s,
)

__all__ = [
    "ComposeError",
    "ComposeInput",
    "ComposedNetwork",
    "ComposedSolution",
    "FileBlock",
    "FreqPlan",
    "LimitCase",
    "ReferenceCheck",
    "COMPOSE_TAG_SEP",
    "EXPORT_DIGITS",
    "FAULT_GRID",
    "FAULT_INPUT",
    "FAULT_PORT",
    "FAULT_SPAN",
    "GRID_RTOL",
    "PHASE_STEP_WARN_DEG",
    "PHASE_STEP_REFUSE_DEG",
    "PHASE_ALIAS_DEG",
    "REF_PROBE_L",
    "REF_LIVE",
    "REF_WELDED",
    "REF_NO_GROUND",
    "REF_UNKNOWN",
    "align_frequencies",
    "compose",
    "default_alias",
    "format_scoped_port",
    "interpolate_s",
    "limit_case_check",
    "link_lumped",
    "link_short",
    "parse_scoped_ports",
    "reduce_block_y",
    "reference_check",
    "solve_composed",
    "write_composed_touchstone",
]


# ============================================================================
# Constants
# ============================================================================

# The file tag separator.  NOT ':' -- that is parse_port_range's start:step:stop
# separator, and parse_port_range('PKG:12') already raises "Range must be
# start:step:stop" today, so a colon prefix would collide with the one syntax
# every port field in this repo goes through.  '.' is free: parse_port_range
# never accepts it (int('F1.3') raises), and it is not in NET_BAD_CHARS, so a
# tagged port and a net name stay distinguishable by the tag being a KNOWN
# alias rather than by spelling.
COMPOSE_TAG_SEP = "."

# An alias is what appears on screen in a cell measured at ~55 px of visible
# combo area, where the string 'EM:23,24,25' renders 70 px -- so the default is
# the repo's own F1/F2 idiom from _format_results_table, two characters.
_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Two frequency grids count as "the same grid" within this RELATIVE tolerance.
# It must be relative: parse_touchstone multiplies by FREQ_UNIT_SCALE, so a file
# written in GHz and one written in Hz describing the same sweep differ in the
# last ulp and never compare equal as floats.  Interpolating a grid onto itself
# is not neutral -- it is a chord across every interval, i.e. the phase error
# below, applied to a file that did not need it.
GRID_RTOL = 1e-9

# Interpolating S across a step of df on a network with group delay tau draws a
# chord across an arc of dphi = 2*pi*df*tau radians.  The chord is short by
# 1 - cos(dphi/2) in amplitude, which reads as insertion loss that is not there
# and corrupts R and Q -- the two quantities this tool exists to report.  Below
# ~20 degrees the error is under 1.5% and sits in the noise; past ~60 degrees
# (13.4%) it is a wrong answer with a plausible shape.
PHASE_STEP_WARN_DEG = 20.0
PHASE_STEP_REFUSE_DEG = 60.0

# np.unwrap cannot recover a step above 180 degrees -- it always takes the
# smaller branch, so a real 288-degree step comes back as 72 and no test on the
# samples can tell.  A RECOVERED step at or above this is therefore reported as
# a LOWER BOUND ("at least N degrees"), because a number quoted off an unwrap
# that may already have folded is not a measurement.  It is also why the refusal
# threshold is 60 and not 170: the useful limit sits well clear of the fold.
PHASE_ALIAS_DEG = 150.0

# The series inductance used to perturb a file's declared ground set in
# reference_check.  Large enough that a real ground path moves visibly at RF
# (1 nH is ~33 ohm at 5 GHz, the same order as a package ground lead), small
# enough not to be a different circuit.  Two values are used, a decade apart:
# a single perturbation could in principle land on a frequency/value where the
# sensitivity happens to vanish, and two cannot both land there unless the
# sensitivity is identically zero -- which is exactly the weld being detected.
REF_PROBE_L = (1e-9, 1e-8)

# Digits after the decimal point in the exported %e fields (so 17 is 18
# significant).  Measured round-tripping a composed 3-port through
# write_composed_touchstone -> parse_touchstone -> s_to_y, worst relative error
# against the composed Y:
#
#       digits   9   ->  9.210e-11        digits  15  ->  1.777e-16
#       digits  12   ->  7.235e-14        digits  17  ->  S EXACT (0.000e+00)
#
# 12 would match reduce_snp.py's default and is not enough HERE for the same
# reason tests/generate_test_snp.py writes its coupled fixtures at 17: the
# reduction's pseudo-inverse cuts at PINV_RCOND = 1e-12, and a composed
# network's condition number is the worst over the union of its blocks
# (cond(block_diag(A, B)) >= max(cond A, cond B), always).  A file carrying
# 1e-13 of write noise in S therefore hands the reduction noise ABOVE the level
# at which it truncates, which is the one thing an export meant for independent
# validation must not do.
EXPORT_DIGITS = 17


# ============================================================================
# Errors
# ============================================================================

FAULT_INPUT = "input"          # the files/aliases/ports handed in do not work
FAULT_SPAN = "span"            # frequency spans do not overlap usefully
FAULT_GRID = "grid"            # they overlap but cannot be resampled honestly
FAULT_PORT = "port"            # a port reference names nothing

_VERDICTS = {
    FAULT_INPUT: "CANNOT COMPOSE -- the inputs do not describe one network",
    FAULT_SPAN: "CANNOT COMPOSE -- the files do not share a usable frequency span",
    FAULT_GRID: "CANNOT COMPOSE -- the grids cannot be aligned without inventing data",
    FAULT_PORT: "CANNOT COMPOSE -- a port reference names nothing",
}


class ComposeError(ValueError):
    """
    A composition that must not proceed.  `str(e)` IS the whole report.

    Same contract as TouchstoneParseError: it subclasses ValueError (what the
    callers already catch), the verdict line answers "is my input bad or is your
    tool bad?" first, and `kind` is machine-readable.
    """

    def __init__(self, kind: str, headline: str,
                 detail: Sequence[str] = (), hint: str = ""):
        self.kind = kind
        self.headline = headline
        self.detail = list(detail)
        self.hint = hint
        super().__init__(self.render())

    def render(self) -> str:
        out = [_VERDICTS.get(self.kind, "CANNOT COMPOSE"), "", self.headline]
        if self.detail:
            out.append("")
            out.extend("  " + d for d in self.detail)
        if self.hint:
            out.append("")
            out.append("Try: " + self.hint)
        return "\n".join(out)


# ============================================================================
# The port namespace
# ============================================================================

def default_alias(index: int) -> str:
    """F1, F2, ... -- the idiom _format_results_table already uses for files."""
    return f"F{index + 1}"


@dataclass
class FileBlock:
    """One file's slot in the composed port numbering."""

    alias: str
    label: str                  # file name, for humans
    offset: int                 # 0-based offset of this block in the global Y
    local_ports: list[int]      # 1-based ORIGINAL port numbers, in block order
    z0: float
    port_names: list[str]       # one per surviving port, "" if the file had none
    nports_original: int        # before any pre-reduction

    @property
    def nports(self) -> int:
        return len(self.local_ports)

    def index_of(self, local_1based: int) -> int:
        """Position of a local port within this block, or -1 if not present."""
        try:
            return self.local_ports.index(local_1based)
        except ValueError:
            return -1


@dataclass
class ComposedNetwork:
    """
    k files stacked into one Y, plus the map back to where each port came from.

    `Y` is block diagonal.  It is NOT the answer to anything on its own: the
    cross-file links are terminations (ShortPair / LumpedBetween on global
    indices) and are applied by compute_z_matrix, which is also where grounds,
    lumped elements and probes are applied.  Keeping the links out of Y is what
    lets every existing mode, the Mode 5 DSL and the coupling path work on a
    composed network without changing a line of them.
    """

    freqs: np.ndarray
    Y: np.ndarray
    blocks: list[FileBlock]
    plan: "FreqPlan | None" = None
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -------------------------------------------------------------- geometry

    @property
    def nports(self) -> int:
        return int(self.Y.shape[-1])

    def block_of_alias(self, alias: str) -> FileBlock:
        key = (alias or "").strip().lower()
        for b in self.blocks:
            if b.alias.lower() == key:
                return b
        known = ", ".join(b.alias for b in self.blocks)
        raise ComposeError(
            FAULT_PORT,
            f"'{alias}' is not one of the files in this composition.",
            [f"known files: {known}"],
            f"write one of {known} in front of the '{COMPOSE_TAG_SEP}', e.g. "
            f"{self.blocks[0].alias}{COMPOSE_TAG_SEP}1" if self.blocks else "",
        )

    def block_of_port(self, gport_1based: int) -> FileBlock:
        g = gport_1based - 1
        if g < 0 or g >= self.nports:
            raise ComposeError(
                FAULT_PORT,
                f"port {gport_1based} is outside the composed network's "
                f"{self.nports} ports.",
                self.port_map_lines(),
            )
        for b in self.blocks:
            if b.offset <= g < b.offset + b.nports:
                return b
        raise ComposeError(FAULT_PORT,
                           f"port {gport_1based} belongs to no file block.")

    def gport(self, alias: str, local_1based: int) -> int:
        """(alias, local port) -> 1-based GLOBAL port number."""
        b = self.block_of_alias(alias)
        i = b.index_of(local_1based)
        if i < 0:
            if 1 <= local_1based <= b.nports_original:
                raise ComposeError(
                    FAULT_PORT,
                    f"port {local_1based} of {b.alias} ({b.label}) was removed "
                    f"by the pre-reduction of that file, so nothing can be "
                    f"connected to it.",
                    [f"{b.alias} kept ports "
                     f"{collapse_ports(b.local_ports)} of "
                     f"{b.nports_original}"],
                    "add it to that file's keep list, or connect to a port "
                    "that survived",
                )
            raise ComposeError(
                FAULT_PORT,
                f"{b.alias}{COMPOSE_TAG_SEP}{local_1based} does not exist: "
                f"{b.label} has {b.nports_original} ports.",
                [f"{b.alias} kept ports {collapse_ports(b.local_ports)}"],
            )
        return b.offset + i + 1

    def local_of(self, gport_1based: int) -> tuple[FileBlock, int]:
        b = self.block_of_port(gport_1based)
        return b, b.local_ports[gport_1based - 1 - b.offset]

    # -------------------------------------------------------------- naming

    def port_label(self, gport_1based: int) -> str:
        """'F2.13' -- short enough for a table cell."""
        b, local = self.local_of(gport_1based)
        return f"{b.alias}{COMPOSE_TAG_SEP}{local}"

    def describe_port(self, gport_1based: int) -> str:
        """'F2.13 (package.s60p port 13, VSS_1)' -- for a message, not a cell."""
        b, local = self.local_of(gport_1based)
        name = b.port_names[gport_1based - 1 - b.offset]
        inner = f"{b.label} port {local}"
        if name:
            inner += f", {name}"
        return f"{b.alias}{COMPOSE_TAG_SEP}{local} ({inner})"

    def port_labels(self) -> list[str]:
        """One scoped label per global port, in order (for an export header)."""
        out = []
        for b in self.blocks:
            for i, local in enumerate(b.local_ports):
                name = b.port_names[i]
                lbl = f"{b.alias}{COMPOSE_TAG_SEP}{local}"
                out.append(f"{lbl} {name}" if name else lbl)
        return out

    def describe_ports(self, gports_1based: Sequence[int]) -> str:
        """
        'F1.2, F2.40-42' -- grouped by file, ranges collapsed.

        collapse_ports never emits a space (the DSL is whitespace-tokenised and
        the port field is parts[0]), so each group here round-trips through
        parse_scoped_ports.
        """
        by_block: dict[str, list[int]] = {}
        for g in gports_1based:
            b, local = self.local_of(g)
            by_block.setdefault(b.alias, []).append(local)
        return ", ".join(
            f"{alias}{COMPOSE_TAG_SEP}{collapse_ports(sorted(locals_))}"
            for alias, locals_ in by_block.items()
        )

    def port_map_lines(self) -> list[str]:
        out = []
        for b in self.blocks:
            lo = b.offset + 1
            hi = b.offset + b.nports
            kept = ""
            if b.nports != b.nports_original:
                kept = (f", kept {collapse_ports(b.local_ports)} of "
                        f"{b.nports_original}")
            out.append(f"{b.alias} = {b.label}: global ports {lo}-{hi} "
                       f"({b.nports} ports{kept}), Z0 = {b.z0:g}")
        return out

    # -------------------------------------------------------------- report

    def report_lines(self) -> list[str]:
        out = ["Composed network: "
               f"{len(self.blocks)} files, {self.nports} ports, "
               f"{len(self.freqs)} frequency points"]
        out.extend("  " + s for s in self.port_map_lines())
        out.extend("  Note: " + n for n in self.notes)
        out.extend("  WARN: " + w for w in self.warnings)
        return out


# ============================================================================
# Port specs:  'F2.40,41,42'  /  '40,41,42'
# ============================================================================

def parse_scoped_ports(spec: str, net: ComposedNetwork,
                       default: str | None = None) -> list[int]:
    """
    'F2.40,41,42' -> 1-based GLOBAL port numbers.

    EVERY COMMA TOKEN CARRIES ITS OWN SCOPE, and a BARE TOKEN IS ALWAYS
    `default`.  A `<alias>.` tag scopes the token it is written on and nothing
    after it, so the two rules a reader is told everywhere else in this tool
    hold literally, in every field, with no ordering condition:

        a bare port number is a port of the home file
        a tagged port number is a port of that file

        '2,F2.1'    -> die port 2, package port 1
        'F2.1,2'    -> package port 1, DIE port 2
        'F1.1,F2.3' -> die port 1, package port 3
        'F2.6-14'   -> a range of the package (a range is ONE token)

    The tag used to be STICKY -- it scoped the whole field, and every bare
    token after it inherited it.  That was chosen to keep 'F2.1,2' meaning
    what it means when a tag can only appear once, and it is why 'F1.1,F2.3'
    had to be refused: with a sticky tag the field has two readings ("one
    field, two scopes" / "the first tag scopes the lot") that differ in
    silence.  What it cost is the same silence one step further on.  The
    connection table stores a whole short group in ONE cell (a group of
    shorted pins has no from/to), so a die-to-package tie is written
    '25,26,F2.15' there and has no other spelling; write the same group in
    the other order, 'F2.15,25,26', and the sticky rule quietly re-pointed 25
    and 26 at the PACKAGE.  That contradicts the rule this tool states
    everywhere -- a bare number is the home file, in every mode -- and it does
    not raise: it only needs the package to HAVE ports 25 and 26, and then it
    is a plausible wrong answer.  Per-token has one reading, needs no ordering
    rule, and makes 'F1.1,F2.3' say exactly what it looks like.

    Ports are deduped over the whole field, order preserved.  The dedup is
    GLOBAL rather than per token because two files' local port 1 are two
    different ports and only the global index can tell them apart.
    """
    text = (spec or "").strip()
    if not text:
        return []
    out: list[int] = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        out.extend(_scoped_token_ports(tok, text, net, default))
    seen: set[int] = set()
    result: list[int] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _scoped_token_ports(tok: str, field: str, net: ComposedNetwork,
                        default: str | None) -> list[int]:
    """
    ONE comma token of a port field -> 1-based global ports.

    `field` is the whole field the token came from, and it is carried purely
    so a message about a token can show where it was written: '2' on its own
    is not enough to find in a table cell that reads '1,2,F2.3'.
    """
    lead, rest = _split_tag(tok)
    alias = lead or default
    where = [] if tok == field else [f"in the port field '{field}'"]
    if alias is None:
        known = ", ".join(b.alias for b in net.blocks)
        raise ComposeError(
            FAULT_PORT,
            f"'{tok}' does not say which file its ports belong to.",
            where + [f"known files: {known}"],
            f"write e.g. {net.blocks[0].alias}{COMPOSE_TAG_SEP}{tok}"
            if net.blocks else "",
        )
    body = rest if lead else tok
    if lead and not body.strip():
        # 'PKG.' is a typed tag with nothing after it.  An EMPTY field means
        # "nothing here" and returns []; this one means "I meant to name a port
        # and did not", and returning [] for it is a silently empty spec.
        raise ComposeError(
            FAULT_PORT,
            f"'{tok}' names the file {lead} but no port in it.",
            where,
            hint=f"write {lead}{COMPOSE_TAG_SEP}<port>, e.g. "
                 f"{lead}{COMPOSE_TAG_SEP}1")
    try:
        locals_ = parse_port_range(body)
    except ValueError as e:
        detail = list(where) + [str(e)]
        known = {b.alias.lower() for b in net.blocks}
        if tok.lower() in known:
            detail.append(f"'{tok}' is the name of a FILE, not a port in it")
        raise ComposeError(
            FAULT_PORT,
            f"'{tok}' is not a port or a range of ports for file {alias}.",
            detail,
            f"a port of {alias} is written "
            f"{alias}{COMPOSE_TAG_SEP}<port>; a port field is a number, a list "
            f"(1,3,5), a dash range (6-14) or a start:step:stop range "
            f"(35:1:45)",
        ) from None
    return [net.gport(alias, p) for p in locals_]


def _split_tag(token: str) -> tuple[str, str]:
    """'F2.40' -> ('F2', '40');  '40' -> ('', '40')."""
    t = (token or "").strip()
    if COMPOSE_TAG_SEP not in t:
        return "", t
    head, _, tail = t.partition(COMPOSE_TAG_SEP)
    head = head.strip()
    if not _ALIAS_RE.match(head):
        return "", t
    return head, tail.strip()


def format_scoped_port(net: ComposedNetwork, gport_1based: int) -> str:
    return net.port_label(gport_1based)


# ============================================================================
# Frequency alignment
# ============================================================================

@dataclass
class FreqPlan:
    """What align_frequencies decided, and everything it noticed on the way."""

    freqs: np.ndarray                 # the common axis (Hz)
    lo: float                         # intersection, Hz
    hi: float
    grid_from: int                    # index of the file whose grid was adopted
    identical: bool                   # every file was already on this grid
    interpolated: list[bool]          # per file
    dropped: list[int]                # per file: samples outside [lo, hi]
    max_step: list[float]             # per file, over intervals touching [lo,hi]
    spacing: list[str]                # 'linear' / 'logarithmic' / 'irregular'
    phase_step_deg: list[float]       # per file, worst chord across one interval
    phase_aliased: list[bool]         # per file: the step is a LOWER bound
    effective_step: float             # the coarsest file's max step
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fake_loss_frac(self, index: int) -> float:
        """1 - cos(dphi/2): the amplitude the chord loses, as a fraction."""
        return _fake_loss_frac(self.phase_step_deg[index])

    def fake_loss_db(self, index: int) -> float:
        """The same thing in dB, which is the unit the dispute is argued in."""
        return _fake_loss_db(self.phase_step_deg[index])


def _fake_loss_frac(phase_step_deg: float) -> float:
    """
    A chord across an arc of dphi is short by 1 - cos(dphi/2) in amplitude.

    36 degrees -> 4.9%; 60 degrees -> 13.4%; 72 degrees -> 19.1%.
    """
    if not math.isfinite(phase_step_deg) or phase_step_deg <= 0:
        return 0.0
    return 1.0 - math.cos(math.radians(phase_step_deg) / 2.0)


def _fake_loss_db(phase_step_deg: float) -> float:
    """
    A chord across an arc of dphi is short by 1 - cos(dphi/2) in amplitude.

    Returned as dB so it can be compared with the number the user cares about
    (a 6 dB dispute is what started all of this).  Positive = loss invented.
    """
    if not math.isfinite(phase_step_deg) or phase_step_deg <= 0:
        return 0.0
    c = math.cos(math.radians(phase_step_deg) / 2.0)
    if c <= 0:
        return float("inf")
    return -20.0 * math.log10(c)


def _spacing_of(f: np.ndarray) -> str:
    if len(f) < 3:
        return "linear"
    d = np.diff(f)
    if np.all(d > 0) and (d.max() - d.min()) <= 1e-6 * d.max():
        return "linear"
    if f[0] > 0:
        r = np.diff(np.log(f))
        if np.all(r > 0) and (r.max() - r.min()) <= 1e-6 * r.max():
            return "logarithmic"
    return "irregular"


def _touching_intervals(f: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """
    Indices k where the interval [f[k], f[k+1]] overlaps [lo, hi].

    Not "samples inside [lo, hi]": a file sampled at 2 GHz and 10 GHz supplies
    the whole of a 2-3 GHz intersection from ONE 8 GHz-wide interval, and that
    interval -- not the absent samples -- is what an interpolation chords
    across.
    """
    if len(f) < 2:
        return np.zeros(0, dtype=int)
    k = np.arange(len(f) - 1)
    return k[(f[1:] > lo) & (f[:-1] < hi)]


def _largest_offdiag(s: np.ndarray) -> tuple[int, int]:
    """
    (i, j) of the entry whose mean |S| is largest, off-diagonal if there is one.

    The off-diagonal is where the group delay lives: S11 of a well-matched
    through is small and noisy, and its phase slope says nothing about the
    delay the interpolation has to resolve.
    """
    n = s.shape[-1]
    mag = np.abs(s).mean(axis=0)
    if n > 1:
        m = mag.copy()
        np.fill_diagonal(m, -1.0)
        idx = int(np.argmax(m))
    else:
        idx = 0
    return idx // n, idx % n


def _phase_step(f: np.ndarray, s: np.ndarray,
                lo: float, hi: float) -> tuple[float, bool, float]:
    """
    Worst phase turn across ONE of this file's sampling intervals, in degrees.

    -> (degrees, aliased, implied_tau_seconds)

    Measured directly from the samples rather than through a fitted slope: the
    contract's dphi = 2*pi*df*tau is exactly the turn across one interval, and
    the interval that actually bites is the worst one, not the average.  The
    implied tau is reported back for the interval that produced it, so the two
    descriptions agree.

    `aliased` says the recovered step is close enough to 180 degrees that the
    real one could be larger: np.unwrap always takes the branch under 180, so a
    genuine 288-degree step comes back as 72 and NOTHING in the samples can
    tell -- that is a limit of the data, not of this check, and the reason the
    refusal threshold sits at 60 degrees, well clear of the fold.  A DC sample
    takes no part: S at 0 Hz is real for a passive reciprocal network and its
    "phase slope" is an artefact.
    """
    if len(f) < 2:
        return 0.0, False, 0.0
    i, j = _largest_offdiag(s)
    d = np.abs(np.diff(np.unwrap(np.angle(s[:, i, j]))))
    df = np.diff(f)
    keep = _touching_intervals(f, lo, hi)
    if f[0] == 0.0:
        keep = keep[keep != 0]
    if len(keep) == 0:
        return 0.0, False, 0.0
    k = int(keep[np.argmax(d[keep])])
    deg = math.degrees(float(d[k]))
    aliased = bool(deg >= PHASE_ALIAS_DEG)
    tau = float(d[k]) / (2.0 * math.pi * float(df[k])) if df[k] > 0 else 0.0
    return deg, aliased, tau


def align_frequencies(freq_axes: Sequence[np.ndarray],
                      s_blocks: Sequence[np.ndarray] | None = None,
                      *,
                      aliases: Sequence[str] | None = None,
                      labels: Sequence[str] | None = None,
                      marker_hz: float | None = None,
                      phase_warn_deg: float = PHASE_STEP_WARN_DEG,
                      phase_refuse_deg: float | None = PHASE_STEP_REFUSE_DEG,
                      ) -> FreqPlan:
    """
    Decide the common frequency axis for a set of files.

    Rules, in order:

      * INTERSECT the spans.  Extrapolation is refused, not clamped: a value
        outside a file's span is not a measurement of anything.
      * REFUSE LOUDLY when the intersection is empty, and when it excludes the
        marker frequency -- the marker is the one frequency the user is reading
        a number at, so a composition that cannot serve it is not a smaller
        composition, it is the wrong one.
      * Detect an ALREADY-IDENTICAL grid with a RELATIVE tolerance and skip
        interpolation entirely.  A file in GHz and one in Hz never compare equal
        as floats, so the common same-flow case would otherwise be interpolated
        onto itself and pay the chord error for nothing.
      * Adopt the FINEST file's grid inside the intersection, and interpolate
        the others onto it.  Upsampling recovers no information, so the
        COARSEST file's max step is reported as the effective resolution.
      * Interpolate S (the caller does the interpolating; this decides).  Not Y
        and not Z: for a passive network S is bounded at every real frequency so
        it has no poles on the axis, while Y blows up at a series resonance and
        Z at a parallel one, and a chord across a pole is unbounded nonsense.
      * Check the PHASE STEP, not max |S|.  See the module docstring.

    `s_blocks` may be omitted when the caller only wants the axis decision; the
    phase check is then skipped and said to be skipped.
    """
    n = len(freq_axes)
    if n == 0:
        raise ComposeError(FAULT_INPUT, "No files to compose.")
    names = _names_for(n, aliases, labels)

    axes = []
    for i, f in enumerate(freq_axes):
        a = np.asarray(f, dtype=float).ravel()
        if a.size == 0:
            raise ComposeError(
                FAULT_INPUT, f"{names[i]} has no frequency points.")
        if a.size > 1 and not np.all(np.diff(a) > 0):
            raise ComposeError(
                FAULT_INPUT,
                f"{names[i]} has a frequency axis that does not increase.",
                hint="re-export the file, or load it and let the parser "
                     "report what is wrong with it")
        axes.append(a)

    notes: list[str] = []
    warnings: list[str] = []

    lo = max(float(a[0]) for a in axes)
    hi = min(float(a[-1]) for a in axes)
    if lo > hi:
        detail = [f"{names[i]}: {format_freq(float(a[0]))} - "
                  f"{format_freq(float(a[-1]))}" for i, a in enumerate(axes)]
        raise ComposeError(
            FAULT_SPAN,
            "the files' frequency spans do not overlap at all, so there is no "
            "frequency at which the combined network exists.",
            detail,
            "re-simulate one of them over the other's band")

    if marker_hz is not None and not (lo <= float(marker_hz) <= hi):
        who = [names[i] for i, a in enumerate(axes)
               if float(marker_hz) < float(a[0]) or float(marker_hz) > float(a[-1])]
        detail = [f"{names[i]}: {format_freq(float(a[0]))} - "
                  f"{format_freq(float(a[-1]))}" for i, a in enumerate(axes)]
        detail.append(f"common span: {format_freq(lo)} - {format_freq(hi)}")
        raise ComposeError(
            FAULT_SPAN,
            f"the marker frequency {format_freq(float(marker_hz))} is outside "
            f"the span shared by these files "
            f"({', '.join(who)} {'does' if len(who) == 1 else 'do'} not "
            f"reach it).",
            detail,
            "move the marker inside the common span, or re-simulate the file "
            "that stops short")

    if lo == hi:
        notes.append(
            f"the files share exactly one frequency, {format_freq(lo)}; the "
            "composition is a single-point sweep")

    # --- identical grid?  relative tolerance, see GRID_RTOL
    identical = all(
        a.size == axes[0].size and np.allclose(a, axes[0], rtol=GRID_RTOL, atol=0.0)
        for a in axes[1:]
    )

    spacing = [_spacing_of(a) for a in axes]
    max_step = []
    for a in axes:
        k = _touching_intervals(a, lo, hi)
        max_step.append(float(np.diff(a)[k].max()) if len(k) else 0.0)

    if identical:
        target = axes[0]
        grid_from = 0
        notes.append(
            f"every file is already on the same {len(target)}-point grid "
            f"(within {GRID_RTOL:g} relative); no interpolation was done")
    else:
        inside = [int(np.count_nonzero((a >= lo) & (a <= hi))) for a in axes]
        best = max(range(n), key=lambda i: (inside[i], -max_step[i], -i))
        if inside[best] < 2 and lo < hi:
            raise ComposeError(
                FAULT_GRID,
                "no file has two samples inside the common span, so there is "
                "no grid to compose onto.",
                [f"{names[i]}: {inside[i]} sample(s) in "
                 f"{format_freq(lo)} - {format_freq(hi)}" for i in range(n)],
                "widen the overlap, or re-simulate on a common grid")
        a = axes[best]
        target = a[(a >= lo) & (a <= hi)]
        grid_from = best
        notes.append(
            f"common grid taken from {names[best]}: {len(target)} points, "
            f"{format_freq(float(target[0]))} - "
            f"{format_freq(float(target[-1]))} ({spacing[best]})")

    interpolated = [
        not (identical or i == grid_from) for i in range(n)
    ]
    # On an identical grid nothing is dropped, by construction -- and the ulp
    # of difference that GRID_RTOL exists to forgive can otherwise push one
    # file's endpoint a hair outside [lo, hi] and produce a "1 of 91 points
    # dropped" warning about a point that is still in the answer.
    dropped = ([0] * n if identical
               else [int(np.count_nonzero((a < lo) | (a > hi))) for a in axes])
    for i in range(n):
        if dropped[i]:
            warnings.append(
                f"{names[i]}: {dropped[i]} of {len(axes[i])} points fall "
                f"outside the common span {format_freq(lo)} - "
                f"{format_freq(hi)} and are dropped (no extrapolation)")

    # --- the resolution the answer actually has
    effective_step = max(max_step) if max_step else 0.0
    coarsest = int(np.argmax(max_step)) if max_step else 0
    if not identical and effective_step > 0:
        notes.append(
            f"effective resolution is {format_freq(effective_step)}, the "
            f"largest step in {names[coarsest]} -- resampling onto a finer "
            f"grid does not recover what that file did not sample")

    if len(set(spacing)) > 1:
        notes.append(
            "sweeps differ in kind (" +
            ", ".join(f"{names[i]} {spacing[i]}" for i in range(n)) +
            "); a log-sampled file resampled onto a linear grid is dense at "
            "the top of the band and sparse at the bottom, which is where the "
            "phase-step check below bites")

    # --- DC policy, stated rather than implied
    has_dc = [bool(a[0] == 0.0) for a in axes]
    if any(has_dc):
        if all(has_dc):
            notes.append(
                "0 Hz is present in every file and is kept; it takes no part "
                "in the phase-step check, because S at DC is real for a "
                "passive reciprocal network and has no phase slope")
        else:
            missing = [names[i] for i in range(n) if not has_dc[i]]
            notes.append(
                "0 Hz is in " +
                ", ".join(names[i] for i in range(n) if has_dc[i]) +
                " but not in " + ", ".join(missing) +
                ", so it is outside the common span and is dropped")

    # --- the marker landing inside a wide interval of the coarsest file
    if marker_hz is not None and max_step and effective_step > 0:
        a = axes[coarsest]
        hit = np.isclose(a, float(marker_hz), rtol=GRID_RTOL, atol=0.0)
        if not hit.any() and len(a) >= 2:
            k = int(np.searchsorted(a, float(marker_hz)) - 1)
            k = max(0, min(k, len(a) - 2))
            width = float(a[k + 1] - a[k])
            # With one shared grid there is no "coarsest file" to name -- every
            # file is equally coarse and naming one of them reads as an
            # accusation against that file.
            whose = "the common grid" if identical else names[coarsest]
            notes.append(
                f"the marker {format_freq(float(marker_hz))} is not a sample "
                f"of {whose}: it sits inside the "
                f"{format_freq(float(a[k]))} - {format_freq(float(a[k + 1]))} "
                f"interval ({format_freq(width)} wide), so the number there is "
                f"interpolated, not measured")

    # --- the phase step, per interpolated file
    phase_step_deg = [0.0] * n
    phase_aliased = [False] * n
    if s_blocks is None:
        if not identical:
            notes.append("phase-step check skipped: no S data was supplied")
    else:
        for i in range(n):
            if not interpolated[i]:
                continue
            deg, aliased, tau = _phase_step(axes[i], np.asarray(s_blocks[i]),
                                            lo, hi)
            phase_step_deg[i] = deg
            phase_aliased[i] = aliased
            loss = _fake_loss_db(deg)
            bound = ">= " if aliased else ""
            msg = (f"{names[i]}: interpolating it turns its largest "
                   f"off-diagonal by {bound}{deg:.1f} deg across one of its "
                   f"own sampling intervals (group delay ~"
                   f"{format_si(tau, 's')}), which invents "
                   f"{loss:.3f} dB of insertion loss that is not in the file")
            if aliased:
                msg += (" -- and np.unwrap cannot see past 180 deg, so that "
                        "is a lower bound")
            if phase_refuse_deg is not None and deg > phase_refuse_deg:
                raise ComposeError(
                    FAULT_GRID,
                    f"{names[i]} is sampled too coarsely to be resampled: "
                    f"its phase turns {bound}{deg:.1f} deg between samples "
                    f"(the limit is {phase_refuse_deg:g} deg).",
                    [msg,
                     "linear interpolation draws a chord across that arc; the "
                     "amplitude it loses reads as insertion loss and corrupts "
                     "R and Q"],
                    "re-export that file on a finer grid, or pass "
                    "phase_refuse_deg=None if you are asserting it is smooth "
                    "between its samples")
            if deg > phase_warn_deg:
                warnings.append(msg)
            elif deg > 0:
                notes.append(
                    f"{names[i]}: phase turns {deg:.1f} deg per interval when "
                    f"interpolated ({loss:.4f} dB of invented loss) -- under "
                    f"the {phase_warn_deg:g} deg noise floor")

    return FreqPlan(
        freqs=np.asarray(target, dtype=float),
        lo=lo, hi=hi, grid_from=grid_from, identical=identical,
        interpolated=interpolated, dropped=dropped, max_step=max_step,
        spacing=spacing, phase_step_deg=phase_step_deg,
        phase_aliased=phase_aliased, effective_step=effective_step,
        notes=notes, warnings=warnings,
    )


def _names_for(n: int, aliases: Sequence[str] | None,
               labels: Sequence[str] | None) -> list[str]:
    out = []
    for i in range(n):
        a = aliases[i] if aliases and i < len(aliases) else default_alias(i)
        lb = labels[i] if labels and i < len(labels) else ""
        out.append(f"{a} ({lb})" if lb else a)
    return out


def interpolate_s(f_src: np.ndarray, s_src: np.ndarray,
                  f_dst: np.ndarray) -> np.ndarray:
    """
    Linear interpolation of S onto `f_dst`.  No extrapolation happens here --
    align_frequencies has already clipped f_dst into every file's span.

    Complex linear interpolation IS Re/Im linear interpolation, done in one
    vectorised blend rather than 2*n*n calls to np.interp (measured need: a
    300-port file is 90000 entries).  Coincident points are then written back
    EXACTLY: s[k] + (s[k+1] - s[k]) * 1.0 is not bit-identical to s[k+1], and a
    grid whose endpoint coincides is the normal case.
    """
    f_src = np.asarray(f_src, dtype=float)
    f_dst = np.asarray(f_dst, dtype=float)
    s_src = np.asarray(s_src)
    if f_src.size == 1:
        out = np.repeat(s_src, len(f_dst), axis=0)
        return out
    idx = np.searchsorted(f_src, f_dst, side="right") - 1
    idx = np.clip(idx, 0, len(f_src) - 2)
    span = f_src[idx + 1] - f_src[idx]
    t = (f_dst - f_src[idx]) / span
    out = s_src[idx] + (s_src[idx + 1] - s_src[idx]) * t[:, None, None]
    exact = np.searchsorted(f_src, f_dst, side="left")
    ok = exact < len(f_src)
    hit = np.zeros(len(f_dst), dtype=bool)
    hit[ok] = f_src[exact[ok]] == f_dst[ok]
    if hit.any():
        out[hit] = s_src[exact[hit]]
    return out


# ============================================================================
# Pre-reduction (requirement R2-6)
# ============================================================================

def reduce_block_y(Y: np.ndarray, keep_0idx: Sequence[int],
                   gnd_0idx: Sequence[int] = (), *,
                   method: str = "open",
                   z0: float = DEFAULT_Z0) -> np.ndarray:
    """
    Shrink one file's Y to its kept ports.  (F, N, N) -> (F, K, K).

    Three port buckets, the same three reduce_snp.py has and for the same
    reason: a KEEP port survives; a GND port is shorted to the file's OWN
    reference, which means DELETING its row and column (V = 0), not opening it;
    everything else is Schur-eliminated, floating (`open`) or through a Y0 = 1/z0
    shunt (`matched`).  Grounding is NOT the same as opening -- a package's
    ground balls need the GND bucket or the answer is wrong.

    This duplicates reduce_snp.py's reduction rather than importing it, because
    reduce_snp.py is deliberately standalone (it gets copied to simulation
    servers on its own and imports nothing from this repo).  Working on Y
    directly rather than S -> Y -> S also skips two matrix inversions per
    frequency that a composition does not need: the composed network is
    assembled in Y.

    Why it is step zero and not an optimisation: _freq_batch collapses to 1 at
    153 ports and above.  Measured on this box (16-port EM + 300-port package,
    101 frequencies, 6 cross links): 4486 ms for the full solve against 2.6 ms
    once the package is reduced to its 6 used ports, i.e. 1714x per re-solve,
    with the two answers agreeing to 4.290e-15 relative.  The reduction itself
    costs 3565 ms and is paid ONCE at load.
    """
    Y = np.asarray(Y)
    n = Y.shape[-1]
    gnd = set(int(p) for p in gnd_0idx)
    keep = [int(p) for p in keep_0idx]
    bad = [p for p in keep if p in gnd]
    if bad:
        raise ComposeError(
            FAULT_INPUT,
            f"port(s) {', '.join(str(p + 1) for p in sorted(bad))} are in both "
            f"the keep list and the ground list of the same file.",
            hint="a kept port is brought out; a grounded one is shorted to the "
                 "file's reference and cannot also be a terminal")
    alive = [i for i in range(n) if i not in gnd]
    remap = {o: i for i, o in enumerate(alive)}
    Yr = Y[:, alive][:, :, alive] if gnd else Y
    keep_r = [remap[p] for p in keep] if gnd else keep
    keep_set = set(keep_r)
    drop = [i for i in range(len(alive)) if i not in keep_set]
    if not drop:
        return np.ascontiguousarray(Yr[:, keep_r][:, :, keep_r])
    Ykk = Yr[:, keep_r][:, :, keep_r]
    Yko = Yr[:, keep_r][:, :, drop]
    Yok = Yr[:, drop][:, :, keep_r]
    Yoo = Yr[:, drop][:, :, drop]
    if method == "matched":
        Yoo = Yoo + np.eye(len(drop), dtype=complex) / z0
    elif method != "open":
        raise ComposeError(FAULT_INPUT,
                           f"unknown reduction method '{method}'",
                           hint="use 'open' or 'matched'")
    return np.ascontiguousarray(Ykk - Yko @ _solve_batch(Yoo, Yok))


def _solve_batch(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """
    Stacked A \\ B, falling back to lstsq PER FREQUENCY on a singular block.

    np.linalg.solve raises for the whole stack when any one matrix in it is
    singular, so the fallback has to redo the stack one frequency at a time --
    reshaping (F, m, m) into (F*m, m) and least-squaring that once is a
    different (and wrong) problem.  Same shape as reduce_snp.py's _solve_batch
    and as s_to_y's chunk fallback in pkg_rlc_core.
    """
    try:
        return np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        pass
    out = np.empty_like(B)
    for i in range(A.shape[0]):
        try:
            out[i] = np.linalg.solve(A[i], B[i])
        except np.linalg.LinAlgError:
            out[i] = np.linalg.lstsq(A[i], B[i], rcond=None)[0]
    return out


# ============================================================================
# Composition
# ============================================================================

@dataclass
class ComposeInput:
    """
    One file's contribution to a composition.

    `keep` / `gnd` are 1-BASED LOCAL port numbers and drive the pre-reduction.
    Kept ports keep their ORIGINAL local numbers in the composed namespace --
    reducing a 60-port package to 6 ports and then calling them 1..6 is exactly
    the silent renumbering that makes an off-by-one in a mapping invisible.

    `keep=None` means "every port that is not in `gnd`", so a file whose only
    reduction is a set of ground balls needs one list, not two.  `method` is
    'open' (the eliminated ports float) or 'matched' (they see Y0 = 1/z0 first).
    """

    data: TouchstoneData
    alias: str = ""
    keep: Sequence[int] | None = None
    gnd: Sequence[int] = ()
    method: str = "open"


def compose(inputs: Sequence[ComposeInput | TouchstoneData], *,
            marker_hz: float | None = None,
            phase_warn_deg: float = PHASE_STEP_WARN_DEG,
            phase_refuse_deg: float | None = PHASE_STEP_REFUSE_DEG,
            ) -> ComposedNetwork:
    """
    k Touchstone files -> one block-diagonal Y on a common frequency axis.

    Each file goes S -> Y with ITS OWN z0 and the blocks are stacked; there is
    no renormalisation (Y is z0-invariant, measured 1.049e-17).  Cross-file
    links are NOT applied here -- they are ShortPair / LumpedBetween on the
    global indices and go through compute_z_matrix with everything else, which
    is what keeps every existing mode working on a composed network.

    The block-diagonal stack WELDS the files' reference nodes at zero
    impedance.  That is stated in `notes` on every composition, without
    exception, and `reference_check` is what turns the statement into a
    measurement.
    """
    entries = [i if isinstance(i, ComposeInput) else ComposeInput(data=i)
               for i in inputs]
    if not entries:
        raise ComposeError(FAULT_INPUT, "No files to compose.")

    aliases = _assign_aliases(entries)
    labels = [Path(e.data.source_path).name or e.data.source_path
              for e in entries]

    plan = align_frequencies(
        [e.data.freqs for e in entries],
        [e.data.s for e in entries],
        aliases=aliases, labels=labels, marker_hz=marker_hz,
        phase_warn_deg=phase_warn_deg, phase_refuse_deg=phase_refuse_deg,
    )

    notes = list(plan.notes)
    warnings = list(plan.warnings)

    # Validate every port list BEFORE converting anything: a typo in a keep
    # list should not cost an S -> Y pass over a 300-port file first.
    plans: list[tuple[list[int], list[int]]] = []
    for i, e in enumerate(entries):
        gnd = [int(p) for p in e.gnd]
        if e.keep is None:
            # No keep list with a ground list means "keep everything that is
            # not grounded" -- the alternative reading (keep them too) is the
            # one combination reduce_block_y refuses, so it can only be a
            # confusing way to spell an error.
            keep = [p for p in range(1, e.data.nports + 1) if p not in set(gnd)]
        else:
            keep = [int(p) for p in e.keep]
        _check_local_ports(aliases[i], labels[i], e.data.nports, keep, gnd)
        plans.append((keep, gnd))

    blocks: list[FileBlock] = []
    y_blocks: list[np.ndarray] = []
    offset = 0
    for i, e in enumerate(entries):
        d = e.data
        keep, gnd = plans[i]
        s = d.s if not plan.interpolated[i] else interpolate_s(
            d.freqs, d.s, plan.freqs)
        if not plan.interpolated[i] and len(plan.freqs) != len(d.freqs):
            # same grid, narrower span: take the slice, do not interpolate
            sel = np.searchsorted(d.freqs, plan.freqs)
            sel = np.clip(sel, 0, len(d.freqs) - 1)
            s = d.s[sel]
        Y = s_to_y(s, d.z0)

        if len(keep) != d.nports or gnd:
            Y = reduce_block_y(Y, [p - 1 for p in keep], [p - 1 for p in gnd],
                               method=e.method, z0=d.z0)
            notes.append(
                f"{aliases[i]} ({labels[i]}) pre-reduced "
                f"{d.nports} -> {len(keep)} ports: kept "
                f"{collapse_ports(keep)}" +
                (f", grounded {collapse_ports(gnd)}" if gnd else "") +
                f", the rest eliminated ({e.method})")
        names = [d.port_names[p - 1] if p - 1 < len(d.port_names) else ""
                 for p in keep]
        blocks.append(FileBlock(alias=aliases[i], label=labels[i],
                                offset=offset, local_ports=keep, z0=d.z0,
                                port_names=names, nports_original=d.nports))
        y_blocks.append(Y)
        offset += len(keep)

    total = offset
    Yc = np.zeros((len(plan.freqs), total, total), dtype=complex)
    for b, Yb in zip(blocks, y_blocks):
        Yc[:, b.offset:b.offset + b.nports, b.offset:b.offset + b.nports] = Yb

    notes.insert(0, _WELD_NOTE.format(
        files=", ".join(f"{b.alias} ({b.label})" for b in blocks)))
    if len({b.z0 for b in blocks}) > 1:
        notes.append(
            "the files declare different reference impedances (" +
            ", ".join(f"{b.alias} {b.z0:g}" for b in blocks) +
            "); each was converted to Y with its own, and Y does not depend on "
            "it (measured 1.049e-17 between z0 = 50 and z0 = 75), so there is "
            "nothing to renormalise")

    return ComposedNetwork(freqs=plan.freqs, Y=Yc, blocks=blocks, plan=plan,
                           notes=notes, warnings=warnings)


_WELD_NOTE = (
    "stacking these files identifies their reference nodes at ZERO impedance "
    "({files}). An n-port Y already has its own reference eliminated, so a "
    "block-diagonal stack is not 'two networks side by side' -- it is one "
    "network whose ground is shared. Any ground path that exists only inside "
    "one file is then not in the circuit at all. reference_check measures "
    "whether that happened here."
)


def _assign_aliases(entries: Sequence[ComposeInput]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        a = (e.alias or "").strip() or default_alias(i)
        if not _ALIAS_RE.match(a):
            raise ComposeError(
                FAULT_INPUT,
                f"'{a}' cannot be a file tag.",
                ["a tag starts with a letter and contains only letters, "
                 "digits and underscores"],
                "a tag is what appears in a port cell, so keep it short -- F1, "
                "EM, PKG")
        if a.lower() in seen:
            raise ComposeError(
                FAULT_INPUT,
                f"two files are both tagged '{a}'.",
                [f"file {seen[a.lower()] + 1} and file {i + 1}"],
                "give each file its own tag; a duplicate makes every port "
                "reference ambiguous")
        seen[a.lower()] = i
        out.append(a)
    return out


def _check_local_ports(alias: str, label: str, nports: int,
                       keep: Sequence[int], gnd: Sequence[int]) -> None:
    bad = sorted({p for p in list(keep) + list(gnd)
                  if p < 1 or p > nports})
    if bad:
        raise ComposeError(
            FAULT_PORT,
            f"{alias} ({label}) has {nports} ports; "
            f"{collapse_ports(bad)} " +
            ("is" if len(bad) == 1 else "are") + " outside that.",
            hint="port numbers are 1-based and local to their file")
    for what, seq in (("keep", keep), ("ground", gnd)):
        dupes = sorted({p for p in seq if list(seq).count(p) > 1})
        if dupes:
            # A repeated keep would put the same port in two columns of the
            # reduced block, which is a singular matrix and a port namespace
            # with two names for one thing.
            raise ComposeError(
                FAULT_PORT,
                f"{alias} ({label}) lists port(s) {collapse_ports(dupes)} "
                f"more than once in its {what} list.",
                hint="each port belongs in one bucket, once")
    if not keep:
        raise ComposeError(
            FAULT_PORT,
            f"{alias} ({label}) would keep no ports at all.",
            hint="a file with no surviving port contributes nothing but its "
                 "reference node -- drop it from the composition instead")


# ============================================================================
# The reference-node self-check (requirement R2-2) -- MANDATORY output
# ============================================================================

# The three things a reference check can find, plus the one it can refuse to
# answer.  They are kept apart because only ONE of them is a defect: a file
# that declares no ground port is the ordinary case when that file's own
# reference IS the system ground, and calling it a weld would cry wolf on every
# correct package composition (measured: the die-return-as-a-port network in
# tests/test_compose.py is correct and declares no package ground at all).
REF_LIVE = "live"            # perturbing the ground set moved the answer
REF_WELDED = "welded"        # it moved it by EXACTLY zero -- the defect
REF_NO_GROUND = "no-ground"  # nothing declared; reference is welded by default
REF_UNKNOWN = "unknown"      # the baseline is not finite, so nothing can be said


@dataclass
class ReferenceCheck:
    """One file's verdict: is its ground network in the circuit at all?"""

    alias: str
    label: str
    ports: list[int]                  # global 1-based ports perturbed
    verdict: str                      # REF_LIVE / REF_WELDED / ...
    max_delta: float                  # max |Z_perturbed - Z_base|
    rel_delta: float
    freq_hz: float
    probe_l: tuple[float, ...]
    message: str

    @property
    def welded(self) -> bool:
        """True only for the defect -- see REF_NO_GROUND."""
        return self.verdict == REF_WELDED

    @property
    def checkable(self) -> bool:
        return self.verdict in (REF_LIVE, REF_WELDED)

    def __bool__(self) -> bool:       # truthy == nothing to report
        return self.verdict == REF_LIVE


def reference_check(net: ComposedNetwork, terminations: TerminationSet, *,
                    freq_hz: float | None = None,
                    probe_l: Sequence[float] = REF_PROBE_L,
                    ) -> list[ReferenceCheck]:
    """
    Perturb each file's declared ground set and see whether the answer moves.

    Why this is mandatory rather than optional: block_diag welds the files'
    reference nodes, and when the near file's return current uses its OWN
    reference (the ordinary on-die case) the far file's whole ground network is
    unreachable.  Measured on the network in
    tests/test_compose.py::TestReferenceCheck -- package ground pad GROUNDED,
    OPEN, and through 1 nH all give L_eff = 2.1454 nH, spread 0.000e+00,
    BIT-IDENTICAL -- while the same structure with the die return brought out
    as a port gives 2.2501 nH and moves when the ground path changes.  Nothing
    raises in the welded case and the number is plausible, which is what makes
    it the worst kind of wrong answer.

    Method: for each file that declares at least one Ground/Vdd port, replace
    THAT FILE's ground set with a series inductor to ground and re-solve.  Two
    inductance values a decade apart, because a single perturbation could in
    principle sit where the sensitivity vanishes and two cannot unless it is
    identically zero.  A file that declares NO ground port is reported too: its
    reference is reachable only through the weld, which is the same finding
    arriving earlier.

    Cost: two extra solves per file with a ground set, AT ONE FREQUENCY.  The
    question is topological, not frequency-dependent, so one frequency answers
    it -- measured on the 76-port network above, a full 601-point sweep is
    76.5 ms and one frequency is ~0.13 ms, so the mandatory check is under 1%
    of the run it guards.
    """
    freqs = np.asarray(net.freqs, dtype=float)
    if freq_hz is None:
        pick = int(len(freqs) // 2)
    else:
        pick = int(np.argmin(np.abs(freqs - float(freq_hz))))
    if freqs[pick] == 0.0 and len(freqs) > 1:
        pick = 1  # a series L is a short at DC; the probe would say nothing
    if freqs[pick] == 0.0:
        # A DC-only sweep: jwL is identically zero, so the perturbation is a
        # wire and "the answer did not move" would be true of every network,
        # welded or not.  Say that, rather than return a verdict that is
        # correct for the wrong reason.
        return [ReferenceCheck(
            alias=b.alias, label=b.label, ports=[], verdict=REF_UNKNOWN,
            max_delta=0.0, rel_delta=0.0, freq_hz=0.0,
            probe_l=tuple(probe_l),
            message=(f"{b.alias} ({b.label}): the reference-node check needs a "
                     f"frequency above DC -- a series inductor is a short at "
                     f"0 Hz, so it would perturb nothing on ANY network and "
                     f"every file would read as welded."))
            for b in net.blocks]
    f1 = freqs[pick:pick + 1]
    Y1 = np.ascontiguousarray(net.Y[pick:pick + 1])

    Zbase, _, _ = compute_z_matrix(Y1, f1, terminations)
    out: list[ReferenceCheck] = []

    for b in net.blocks:
        ports = [g for g in range(b.offset + 1, b.offset + b.nports + 1)
                 if isinstance(terminations.termination_of(g - 1), (Ground, Vdd))]
        if not ports:
            out.append(ReferenceCheck(
                alias=b.alias, label=b.label, ports=[], verdict=REF_NO_GROUND,
                max_delta=0.0, rel_delta=0.0,
                freq_hz=float(f1[0]), probe_l=tuple(probe_l),
                message=(
                    f"{b.alias} ({b.label}) declares no ground port, so its "
                    f"reference node is at the composed network's ground by "
                    f"construction -- there is nothing to perturb and this "
                    f"check has nothing to say about it. That is right if that "
                    f"file's own reference IS the system ground (a package "
                    f"referenced to the board plane). It is wrong if its "
                    f"ground is supposed to reach the system through one of "
                    f"its ports: declare that port as ground, or connect it, "
                    f"and run this again."),
            ))
            continue

        deltas = []
        for L in probe_l:
            pert = TerminationSet(
                per_port=dict(terminations.per_port),
                couplings=list(terminations.couplings))
            for g in ports:
                pert.per_port[g - 1] = LumpedToGnd(y_series_rlc(L=float(L)),
                                                   {"L": float(L)})
            Zp, _, _ = compute_z_matrix(Y1, f1, pert)
            d = Zp - Zbase
            finite = np.isfinite(d)
            deltas.append(float(np.max(np.abs(d[finite]))) if finite.any()
                          else float("nan"))
        max_delta = max(deltas) if deltas else 0.0
        scale = float(np.max(np.abs(Zbase[np.isfinite(Zbase)]))) \
            if np.isfinite(Zbase).any() else 0.0
        rel = (max_delta / scale) if scale > 0 else 0.0

        where = net.describe_ports(ports)
        if not math.isfinite(max_delta):
            verdict = REF_UNKNOWN
            msg = (
                f"{b.alias} ({b.label}): the check could not run -- the "
                f"baseline measurement at {format_freq(float(f1[0]))} is not "
                f"finite, so 'did the answer move' has no answer. Fix the "
                f"measurement port's return path first; the warnings above "
                f"name it.")
        elif max_delta == 0.0:
            verdict = REF_WELDED
            msg = (
                f"{b.alias} ({b.label}): putting a series inductor "
                f"({' and '.join(format_si(L, 'H') for L in probe_l)}) in "
                f"front of its ground set {where} changed the answer by "
                f"EXACTLY zero at {format_freq(float(f1[0]))}. That ground "
                f"network is not in the circuit: the current returns through "
                f"the reference node the block-diagonal stack welded to the "
                f"other file(s), so grounded, open and through-an-inductor are "
                f"the same number. Bring the return path out as a PORT in the "
                f"file that owns it, and connect it here.")
        else:
            verdict = REF_LIVE
            msg = (
                f"{b.alias} ({b.label}): its ground set {where} is in the "
                f"circuit -- a series {format_si(probe_l[-1], 'H')} in front "
                f"of it moves the answer by {format_si(max_delta, 'Ω')} "
                f"({rel * 100:.2g}% of |Z|) at {format_freq(float(f1[0]))}.")
        out.append(ReferenceCheck(
            alias=b.alias, label=b.label, ports=ports, verdict=verdict,
            max_delta=max_delta, rel_delta=rel,
            freq_hz=float(f1[0]), probe_l=tuple(probe_l), message=msg))
    return out


# ============================================================================
# The limit case (requirement R2-9)
# ============================================================================

@dataclass
class LimitCase:
    """Replace the interconnect with ideal wire; the standalone number must return."""

    Z: np.ndarray
    reference: np.ndarray
    max_rel_error: float
    ok: bool
    opened: list[int]                 # far ports the check had to open
    message: str

    def __bool__(self) -> bool:
        return self.ok


def limit_case_check(net: ComposedNetwork, terminations: TerminationSet,
                     reference_z: np.ndarray, *,
                     ideal_aliases: Sequence[str],
                     shorts: Sequence[Sequence[int]] = (),
                     grounds: Sequence[int] = (),
                     freq_hz: float | None = None,
                     rtol: float = 1e-9) -> LimitCase:
    """
    Replace one or more files with ideal interconnect and check the answer.

    The idea is the cheapest validation there is for a feature with no golden
    reference: if the package were a perfect wire, the composed measurement must
    reproduce the number the device file gives on its own.  One extra solve, and
    it catches nearly every mapping error -- a swapped pair, an off-by-one in
    one file's numbering, a link written to the wrong side.

    `ideal_aliases` name the files replaced.  Their Y block is ZEROED (an ideal
    wire has no admittance of its own) and the ideal topology is stated
    explicitly, because it is exactly the thing the user has to be sure of:

        shorts   groups of GLOBAL 1-based ports the ideal part ties together
                 -- 'this trace becomes a wire from its die pad to its ball'
        grounds  GLOBAL 1-based ports the ideal part ties to the reference
                 -- 'this ground lead becomes a wire to the board plane'

    Nothing is inferred.  A far port that appears nowhere -- not in `shorts`,
    not in `grounds`, and not in `terminations` -- is grounded so its all-zero
    row and column leave the matrix; grounding deletes the row and column, and a
    row that is already zero cannot affect anything else, so this is exactly
    "delete it" and is listed in `opened` rather than done silently.

    `reference_z` is the standalone answer, supplied by the caller: this
    function deliberately does not rebuild the device's own spec, because
    guessing what the standalone measurement should have been is the same class
    of guess the whole module refuses to make.
    """
    ideal = {a.strip().lower() for a in ideal_aliases}
    shown = ", ".join(b.alias for b in net.blocks if b.alias.lower() in ideal)
    unknown = [a for a in ideal_aliases
               if a.strip().lower() not in {b.alias.lower() for b in net.blocks}]
    if unknown:
        raise ComposeError(
            FAULT_INPUT,
            f"{', '.join(unknown)} is not a file in this composition.",
            net.port_map_lines())

    freqs = np.asarray(net.freqs, dtype=float)
    if freq_hz is None:
        pick = int(len(freqs) // 2)
    else:
        pick = int(np.argmin(np.abs(freqs - float(freq_hz))))
    f1 = freqs[pick:pick + 1]

    Yl = np.array(net.Y[pick:pick + 1], copy=True)
    far_ports: list[int] = []
    for b in net.blocks:
        if b.alias.lower() not in ideal:
            continue
        Yl[:, b.offset:b.offset + b.nports, b.offset:b.offset + b.nports] = 0.0
        far_ports.extend(range(b.offset + 1, b.offset + b.nports + 1))

    named: set[int] = set(int(g) for g in grounds)
    for grp in shorts:
        named.update(int(g) for g in grp)
    for p0 in terminations.per_port:
        if not isinstance(terminations.per_port[p0], Open):
            named.add(p0 + 1)
    for c in terminations.couplings:
        named.add(c.port_i + 1)
        named.add(c.port_j + 1)

    opened = [g for g in far_ports if g not in named]

    pp = dict(terminations.per_port)
    for g in list(grounds) + opened:
        pp[int(g) - 1] = Ground()
    cpl = list(terminations.couplings)
    for grp in shorts:
        g = [int(p) for p in grp]
        for a, b_ in zip(g, g[1:]):
            cpl.append(ShortPair(a - 1, b_ - 1))
    ts = TerminationSet(per_port=pp, couplings=cpl)

    Z, _, _ = compute_z_matrix(np.ascontiguousarray(Yl), f1, ts)
    ref = np.asarray(reference_z)
    if ref.ndim == 0:
        ref = ref.reshape(1, 1, 1)
    elif ref.ndim == 2:
        ref = ref[None, :, :]
    if ref.shape[-2:] != Z.shape[-2:]:
        raise ComposeError(
            FAULT_INPUT,
            f"the standalone reference is {ref.shape[-2]}x{ref.shape[-1]} but "
            f"the limit case measures {Z.shape[-2]}x{Z.shape[-1]} measurement "
            f"port(s).",
            hint="measure the standalone file with the same probes the "
                 "composed spec uses")
    if ref.shape[0] not in (1, len(freqs)):
        # The slice below indexes `ref` with an index into the COMPOSED axis,
        # so the two have to be the same axis.  Guessing a correspondence
        # between a 401-point standalone sweep and a 901-point composed one is
        # the same guess this module refuses everywhere else.
        raise ComposeError(
            FAULT_INPUT,
            f"the standalone reference has {ref.shape[0]} frequency points and "
            f"the composed network has {len(freqs)}.",
            hint="measure the standalone file on the composed network's "
                 "frequency axis, or pass just the value at the frequency "
                 "being checked")
    if ref.shape[0] > 1:
        ref = ref[pick:pick + 1]

    denom = np.maximum(np.abs(ref), np.finfo(float).tiny)
    err = float(np.max(np.abs(Z - ref) / denom))
    ok = err <= rtol
    opened_txt = (f"; {len(opened)} unconnected port(s) of the ideal "
                  f"file(s) were opened ({net.describe_ports(opened)})"
                  if opened else "")
    if ok:
        msg = (f"limit case passes at {format_freq(float(f1[0]))}: with "
               f"{shown} replaced by ideal wire the "
               f"composed measurement reproduces the standalone number to "
               f"{err:.2e} relative{opened_txt}.")
    else:
        msg = (f"LIMIT CASE FAILS at {format_freq(float(f1[0]))}: with "
               f"{shown} replaced by ideal wire the "
               f"composed measurement is {err:.3e} away from the standalone "
               f"number (limit {rtol:.0e}). The port mapping, not the "
               f"interconnect, is what this measures -- check which port of "
               f"each file is connected to which{opened_txt}.")
    return LimitCase(Z=Z, reference=ref, max_rel_error=err, ok=ok,
                     opened=opened, message=msg)


# ============================================================================
# Solving
# ============================================================================

_PORTS_OUTSIDE_RE = re.compile(
    r"^Port number\(s\) (?P<ports>[\d, ]+) are outside this file's")


@dataclass
class ComposedSolution:
    Zmat: np.ndarray
    port_names: list[str]
    freqs: np.ndarray
    warnings: list[str]
    reference: list[ReferenceCheck]
    notes: list[str] = field(default_factory=list)

    @property
    def welded(self) -> list[ReferenceCheck]:
        return [r for r in self.reference if r.welded]

    def report_lines(self) -> list[str]:
        out = [f"Measurement ports: {', '.join(self.port_names)}"]
        tag = {REF_WELDED: "  WELD: ", REF_LIVE: "  ok:   ",
               REF_NO_GROUND: "  note: ", REF_UNKNOWN: "  ?:    "}
        for r in self.reference:
            out.append(tag.get(r.verdict, "  ?:    ") + r.message)
        out.extend("  WARN: " + w for w in self.warnings)
        out.extend("  Note: " + n for n in self.notes)
        return out


def solve_composed(net: ComposedNetwork, terminations: TerminationSet, *,
                   marker_hz: float | None = None,
                   probe_l: Sequence[float] = REF_PROBE_L) -> ComposedSolution:
    """
    compute_z_matrix on a composed network, with the reference-node check
    attached.  There is deliberately no way to turn the check off: it is the
    thing that makes "what you assembled is what you measured" an honest claim
    rather than a hope, and it costs two single-frequency solves per file.

    Port-index errors from the core are re-raised with the file tags restored --
    "port 305 is outside this file's 316 ports" is unactionable on a combined
    network, and this is the one core message that names a bare port number.
    """
    try:
        Zmat, names, warns = compute_z_matrix(net.Y, net.freqs, terminations)
    except ValueError as e:
        raise _scope_port_error(net, e) from None
    ref = reference_check(net, terminations, freq_hz=marker_hz,
                          probe_l=probe_l)
    return ComposedSolution(Zmat=Zmat, port_names=names, freqs=net.freqs,
                            warnings=list(warns), reference=ref,
                            notes=list(net.notes))


def _scope_port_error(net: ComposedNetwork, exc: ValueError) -> Exception:
    m = _PORTS_OUTSIDE_RE.match(str(exc))
    if m is None:
        return exc
    ports = [int(p) for p in m.group("ports").replace(" ", "").split(",") if p]
    detail = []
    for p in ports:
        if 1 <= p <= net.nports:
            detail.append(f"port {p} is {net.describe_port(p)}")
        else:
            detail.append(f"port {p} is past the end of the composed network")
    return ComposeError(
        FAULT_PORT,
        f"port number(s) {', '.join(str(p) for p in ports)} are outside the "
        f"composed network's {net.nports} ports.",
        detail + net.port_map_lines(),
        f"a port of a particular file is written "
        f"{net.blocks[0].alias}{COMPOSE_TAG_SEP}<port>" if net.blocks else "")


# ============================================================================
# Export (requirement R2-7)
# ============================================================================

def write_composed_touchstone(out_path: str | Path, net: ComposedNetwork, *,
                              ports: Sequence[int] | None = None,
                              z0: float = DEFAULT_Z0,
                              digits: int = EXPORT_DIGITS,
                              comment: str = "") -> Path:
    """
    Write the composed network out as a Touchstone file.

    This is the only route to INDEPENDENT validation of a feature that has no
    golden reference: the file can go back into this tool, into a simulator, or
    into anyone else's parser, and the answers can be compared.  It is also what
    a circuit designer actually wants -- the assembled network as a model.

    `ports` selects the global 1-based ports to bring out (default: all); the
    rest are Schur-eliminated open, through the same reduce_block_y the
    pre-reduction uses.  `z0` is declared in the option line and is a free
    choice: Y does not depend on it (measured 1.049e-17), so this is the one
    place a reference impedance has to be picked again.

    The port names carry the file tags, so a reader can still tell which file a
    port came from.  The n = 2 column order is Touchstone v1's column-major
    quirk (S11 S21 S12 S22) and n >= 3 is row-major -- do not "fix" one side
    without the other.
    """
    out_path = Path(out_path)
    Y = net.Y
    labels = net.port_labels()
    if ports is not None:
        sel = [int(p) - 1 for p in ports]
        bad = [p + 1 for p in sel if p < 0 or p >= net.nports]
        if bad:
            raise ComposeError(
                FAULT_PORT,
                f"cannot export port(s) {', '.join(str(p) for p in bad)}: the "
                f"composed network has {net.nports}.",
                net.port_map_lines())
        Y = reduce_block_y(Y, sel, (), method="open", z0=z0)
        labels = [labels[p] for p in sel]

    S = y_to_s(Y, z0)
    n = S.shape[-1]
    d = int(digits)
    lines: list[str] = []
    lines.append("! Composed network written by pkg_rlc_compose")
    for s in net.port_map_lines():
        lines.append("! " + s)
    lines.append("! NOTE: the source files' reference nodes are welded "
                 "together in this network (block-diagonal stack).")
    for c in (comment or "").splitlines():
        lines.append("! " + c)
    for i, lbl in enumerate(labels):
        lines.append(f"! Port[{i + 1}] = {lbl}")
    lines.append(f"# HZ S RI R {z0:g}")

    freqs = np.asarray(net.freqs, dtype=float)
    for k in range(len(freqs)):
        Sk = S[k]
        if n == 1:
            lines.append(f"{freqs[k]:.{d}e}  "
                         f"{Sk[0, 0].real:.{d}e} {Sk[0, 0].imag:.{d}e}")
        elif n == 2:
            # Touchstone v1 quirk: n == 2 is COLUMN-major (S11 S21 S12 S22).
            lines.append(
                f"{freqs[k]:.{d}e}  "
                f"{Sk[0, 0].real:.{d}e} {Sk[0, 0].imag:.{d}e}  "
                f"{Sk[1, 0].real:.{d}e} {Sk[1, 0].imag:.{d}e}  "
                f"{Sk[0, 1].real:.{d}e} {Sk[0, 1].imag:.{d}e}  "
                f"{Sk[1, 1].real:.{d}e} {Sk[1, 1].imag:.{d}e}")
        else:
            for i in range(n):
                vals: list[str] = []
                for j in range(n):
                    vals.append(f"{Sk[i, j].real:.{d}e}")
                    vals.append(f"{Sk[i, j].imag:.{d}e}")
                row = " ".join(vals)
                lines.append(f"{freqs[k]:.{d}e}  {row}" if i == 0
                             else " " * 15 + row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


# ============================================================================
# Cross-file links -- sugar over ShortPair / LumpedBetween
# ============================================================================

def link_short(net: ComposedNetwork, a_spec: str, b_spec: str, *,
               default: str | None = None) -> list[ShortPair]:
    """
    'EM.1' + 'PKG.12' -> the ShortPair chain that ties them into one node.

    Elementwise when both sides list several ports and the lengths match --
    'EM.1-3' + 'PKG.10-12' is three separate wires, which is what a bond
    diagram means.  (A tag scopes ONE token, so a list of tagged ports is
    'PKG.10,PKG.11,PKG.12'; a range is one token and takes one tag.  A bare
    token takes `default`, which the CLI always passes.)  A length mismatch is
    a HARD ERROR and echoes the END pairs,
    because an off-by-one in one file's numbering shifts every pair and the
    result is a plausible wrong answer with nothing pointing at it.  A single
    port on one side fans out to all of the other side (54 VSS balls onto one
    die pad is the ordinary flip-chip connection).
    """
    a = parse_scoped_ports(a_spec, net, default)
    b = parse_scoped_ports(b_spec, net, default)
    pairs = _pair_up(net, a, b, a_spec, b_spec)
    return [ShortPair(p - 1, q - 1) for p, q in pairs]


def link_lumped(net: ComposedNetwork, a_spec: str, b_spec: str,
                y_func, params: dict | None = None, *,
                default: str | None = None) -> list[LumpedBetween]:
    """The same pairing, with an element in each wire instead of a short."""
    a = parse_scoped_ports(a_spec, net, default)
    b = parse_scoped_ports(b_spec, net, default)
    pairs = _pair_up(net, a, b, a_spec, b_spec)
    return [LumpedBetween(p - 1, q - 1, y_func, params) for p, q in pairs]


def _pair_up(net: ComposedNetwork, a: Sequence[int], b: Sequence[int],
             a_spec: str, b_spec: str) -> list[tuple[int, int]]:
    if not a or not b:
        raise ComposeError(
            FAULT_PORT,
            f"a link needs a port on both sides; got '{a_spec}' and '{b_spec}'.")
    if len(a) == 1:
        return [(a[0], q) for q in b]
    if len(b) == 1:
        return [(p, b[0]) for p in a]
    if len(a) != len(b):
        raise ComposeError(
            FAULT_PORT,
            f"'{a_spec}' lists {len(a)} ports and '{b_spec}' lists {len(b)}, "
            f"so they cannot be paired one for one.",
            [f"first pair would be {net.port_label(a[0])} - "
             f"{net.port_label(b[0])}",
             f"last  pair would be {net.port_label(a[-1])} - "
             f"{net.port_label(b[-1])}"],
            "use the same number of ports on both sides, or one port on one "
            "side to fan out to all of the other")
    return list(zip(a, b))
