"""
pkg_rlc_touchstone.py  --  Reading a Touchstone file, and saying what is wrong
with it.

Split out of `pkg_rlc_core.py` verbatim: the universal content-based parser
(it ignores the file extension on purpose), the encoding sniffer, the
port-count sniffer, the descriptive checks, and the slow second diagnosis pass
that turns "token count 3603 not divisible by 9" into "the file ends
mid-record at line 408".  `TouchstoneParseError` and its FAULT_* verdicts live
here because the verdict is this module's whole contract: "is my file bad or
is your tool bad?" is the first question a parse failure has to answer.

This module imports nothing from this repo -- it is the bottom of the L0
numerics layer.  That is also why the two display helpers (`format_si` /
`format_freq`) sit here: `TouchstoneData.freq_span` and `_check_freq_axis`
need them to describe a sweep, and so do `pkg_rlc_spec` and `pkg_rlc_solve`
above.  Anywhere else they would need an import reaching back DOWN into this
module from a layer below it, which is a cycle.

`pkg_rlc_core` re-exports every name defined here, so
`from pkg_rlc_core import parse_touchstone` keeps resolving unchanged.
"""

from __future__ import annotations

import array
import bisect
import codecs
import math
import re
import textwrap
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

# ============================================================================
# Constants
# ============================================================================

DEFAULT_Z0 = 50.0
FREQ_UNIT_SCALE = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9, "THZ": 1e12}

# Port counts the content sniffer sweeps before it will consult the file name.
# The body is one modulo plus -- only for the few N whose record size actually
# divides the value count -- a monotonicity walk of the frequency column.
# Measured at 38 ns per N, i.e. 9.5 us for a full 1..256 sweep, and a file big
# enough to reach 256 pays it on every parse: the loop runs to the end unless
# three candidates turn up.  (Smaller files stop at _max_possible_nports.)
# This is deliberately NOT the ceiling on how many ports a file may have; see
# SNIFF_HARD_CAP.  It is the point past which a port count inferred from
# divisibility alone stops being the best evidence available.
MAX_SNIFF_NPORTS = 256

# ...and this is the ceiling on a port count inferred from the numbers ALONE,
# reached only after the cheap sweep AND the file name have both failed.  A
# 300-port package export renamed to .txt -- the normal fate of these files, and
# the case the content sniffer exists for -- had no route into this tool at all
# before this: the sweep stopped at 256, the name said nothing, and force_nports
# was the only way in.
#
# 4096 is set where the arithmetic stops being the binding constraint.  One
# frequency point of a 4096-port file is 1 + 2*4096^2 = 33.6M numbers: 268 MB of
# parsed doubles plus another 268 MB of complex S, so a file with more ports than
# this cannot be held in memory on any machine this tool runs on, and refusing it
# with a verdict beats spending an afternoon on it.  The sweep is self-limiting
# besides -- _max_possible_nports stops it at isqrt((T-1)/2), because a record
# longer than the whole file cannot divide it -- so this cap can only ever bind
# on a file of >= 33.6M values.  Measured: a full 1..4096 sweep is 157 us
# (against 9.5 us for 1..256), which on a file big enough to reach it is
# invisible next to the tens of seconds spent reading it.  On
# tests/fixtures/pi_2port.s2p (3609 values) it costs exactly nothing: the sweep
# cannot run past N=42 there, so the wide range 257..42 is empty, and the whole
# sniff went 0.030 ms -> 0.012 ms against the single-sweep version it replaced.
SNIFF_HARD_CAP = 4096

# Frequencies per chunk in the batched linear algebra (s_to_y / y_to_s /
# compute_z_matrix).  Everything is stacked into (F, N, N) arrays and handed to
# one np.linalg.solve so numpy loops in C instead of Python; the chunk keeps
# peak memory proportional to the batch, not to the file, which is what lets a
# 153-port x 5000-frequency EM export run at all.  Mirrors reduce_snp.py's
# --batch default.  numpy dispatches a stacked solve to the same LAPACK routine
# per matrix as the 2-D call, so batching it is bit-exact -- see
# tests/test_golden_regression.py, which pins that.  (Stacked *matmul* is a
# different story; see the note in compute_z_matrix.)
COMPUTE_BATCH = 256

# ...but the chunk is also capped so its working arrays stay roughly L2-sized.
# A full 256-frequency chunk of a 60-port file is ~24 MB per temporary, and
# every chunk then faults in fresh pages: measured 25% SLOWER than the old
# per-frequency loop, which kept one 28 KB buffer hot.  Below ~5 ports the cap
# never binds and the batch wins 15-25x; above ~100 ports the sweep is
# LAPACK-bound and a batch of 1 costs nothing.  Bit-exactness does not depend on
# the chunk size (verified over batches 1..1e5 against the pre-batch code).
COMPUTE_CHUNK_BYTES = 256 * 1024


def _freq_batch(n: int) -> int:
    """Frequencies per chunk for an (F, n, n) complex sweep."""
    per_freq = max(1, n * n * 16)          # complex128
    return max(1, min(COMPUTE_BATCH, COMPUTE_CHUNK_BYTES // per_freq))

# Touchstone values are staged in a small Python list and flushed into an
# array.array('d') every this many values, so peak memory tracks the block
# size and not the file size.  See parse_touchstone.
PARSE_FLUSH_VALUES = 250_000


# ============================================================================
# Touchstone parser (universal content-based; ignores extension)
# ============================================================================

# Repetitive parse warnings (the old code emitted one per bad token) are
# collapsed after this many examples.  A corrupt file can produce millions of
# them: unbounded, that is both a memory hazard and an unreadable Results pane.
# Same cap as the Schur-fallback and rank-deficiency warnings further down.
PARSE_WARN_CAP = 3

# Bytes read from the head of a file to pick its encoding and rule out binary.
ENCODING_SNIFF_BYTES = 4096

# Data lines whose (line_no, value offset) the diagnosis pass remembers, so a
# value index can be turned back into a line number.  16 bytes per line (two
# array('q')); the cap stops a pathological 50M-line file from costing more than
# the parse did.  It is not generous: a 153-port sweep written 4 pairs to the
# line -- what the Touchstone spec asks for -- is 5967 data lines per frequency,
# so at 3000 frequencies the index stops recording 11% of the way in.
#
# Raising it was measured and rejected: the head index is already ~50% on top of
# scan.values (8 bytes per value, uncapped, 4 values to the line), and 20M lines
# would be 320 MB on an ERROR path.  What the cap actually cost was the one fact
# the report exists to state -- a truncated file breaks at its END, past the head
# index -- so the fix is the tail ring below, not a bigger head.
DIAGNOSE_MAX_LINES = 2_000_000

# ...and the last N data lines, kept in a ring once the head index is full, so
# the end of a big file stays nameable.  Costs ~4.7 MB (two deques of boxed
# ints), against 2.4 GB for indexing every line of the 3000-frequency 153-port
# file above.  65536 lines is ~5 whole records of that file at 4 pairs to the
# line, which is what it takes to reach back over the truncated last record.
DIAGNOSE_TAIL_LINES = 1 << 16

# Fault classes carried by TouchstoneParseError.  The whole point of the class
# is that nobody should have to guess which one they are looking at: "is my
# file bad, or is your tool bad?" is the question a parse failure has to answer
# before anything else.
FAULT_FILE = "file"                  # the content is inconsistent / corrupt
FAULT_UNSUPPORTED = "unsupported"    # valid, but a format this tool cannot read
FAULT_ACCESS = "access"              # could not be opened / read at all
FAULT_INTERNAL = "internal"          # content looks fine -- this tool's bug
FAULT_NONE = "none"                  # diagnosis only: nothing wrong found

_VERDICT = {
    FAULT_FILE:
        "THE FILE is inconsistent. Everything before the point named above "
        "was read correctly.",
    FAULT_UNSUPPORTED:
        "THE FILE looks valid, but it is in a format this tool does not read.",
    FAULT_ACCESS:
        "THE FILE could not be read at all; nothing was parsed.",
    FAULT_INTERNAL:
        "THE PARSER gave up on a file whose structure looks consistent. That "
        "is a bug in this tool, not a bad file -- please report it with the "
        "details above.",
}


def _clip(text: str, width: int = 72) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


class TouchstoneParseError(ValueError):
    """
    A parse failure that says whose fault it is.

    `kind` is one of FAULT_FILE / FAULT_UNSUPPORTED / FAULT_ACCESS /
    FAULT_INTERNAL and drives the "Verdict" line of the report: a user who
    cannot open their file needs to know whether to re-export it, convert it,
    or file a bug -- and no amount of "invalid literal for float()" tells them
    that.  `str(e)` IS the full report, so every existing `except Exception as
    e: show(e)` call site upgrades for free.

    Subclasses ValueError because that is what the parser raised before this
    existed, and callers (including the test suite) catch it that way.
    """

    def __init__(self, what: str, *, path, kind: str = FAULT_FILE,
                 line_no: int | None = None, line_text: str | None = None,
                 hint: str | None = None, context: Sequence[str] = (),
                 retry_lenient: bool = False, traceback_text: str = "",
                 verdict: str | None = None):
        self.what = what
        self.path = str(path)
        self.kind = kind
        self.line_no = line_no
        self.line_text = line_text
        self.hint = hint
        self._verdict = verdict
        self.context = list(context)
        # True when `lenient=True` would get the file open anyway.  The GUI
        # offers it as a button; anything else would be a dead end for a user
        # who just wants to look at a file with one bad line in it.
        self.retry_lenient = retry_lenient
        self.traceback_text = traceback_text
        super().__init__(self.report())

    @property
    def verdict(self) -> str:
        return self._verdict or _VERDICT.get(self.kind, _VERDICT[FAULT_FILE])

    def report(self) -> str:
        rows = [("Path", self.path), ("Problem", self.what)]
        if self.line_no is not None:
            where = f"line {self.line_no}"
            if self.line_text:
                where += f":  {_clip(self.line_text)}"
            rows.append(("Where", where))
        rows.append(("Verdict", self.verdict))
        if self.hint:
            rows.append(("Try", self.hint))
        w = max(len(k) for k, _ in rows)
        out = [f"Cannot read {Path(self.path).name}"]
        for key, value in rows:
            out.append(textwrap.fill(
                str(value), width=88,
                initial_indent=f"  {key:<{w}} : ",
                subsequent_indent=" " * (w + 5)))
        if self.context:
            out.append("")
            out.extend(self.context)
        if self.traceback_text:
            out.append("")
            out.append(self.traceback_text.rstrip())
        return "\n".join(out)


@dataclass
class TouchstoneData:
    nports: int
    freqs: np.ndarray            # Hz, shape (nfreqs,)
    s: np.ndarray                # complex, shape (nfreqs, nports, nports)
    z0: float
    port_names: list[str]        # one per port; "" if none
    source_path: str
    parser_warnings: list[str] = field(default_factory=list)
    # `parser_warnings` means "I had to guess, or I threw something away".
    # `data_notes` means "the file is fine; here is what is in it that you want
    # to know before reading the numbers" -- a DC point, |S| > 1, an irregular
    # sweep.  Keeping the two apart is also what keeps every new descriptive
    # check clear of tests/fixtures/golden_legacy.npz, which pins
    # parser_warnings element-for-element.
    data_notes: list[str] = field(default_factory=list)
    freq_unit: str = "HZ"        # as declared in the option line
    param_type: str = "S"
    data_format: str = "MA"
    option_line: str = ""        # normalised effective option line
    freq_spacing: str = ""       # 'linear, step 25 MHz' / 'logarithmic' / ...
    s_max: float = float("nan")  # max |S| over the whole file

    # ------------------------------------------------------------- reporting

    def freq_span_str(self) -> str:
        """'1 MHz - 10 GHz', '1 GHz (single point)', '(no points)'."""
        n = len(self.freqs)
        if n == 0:
            return "(no points)"
        lo = format_freq(float(self.freqs[0]))
        if n == 1:
            return f"{lo} (single point)"
        return f"{lo} - {format_freq(float(self.freqs[-1]))}"

    def summary_lines(self) -> list[str]:
        """
        The block printed on load by both the GUI and the CLI.

        Leads with the frequency span because that is the first thing anyone
        checks against what they simulated, and it is the one property the
        file list used to omit entirely.
        """
        plural = "" if self.nports == 1 else "s"
        out = [
            f"{Path(self.source_path).name}",
            f"  {self.nports} port{plural}, {len(self.freqs)} points, "
            f"Z0 = {self.z0:g}Ω, read as '# {self.option_line}'",
        ]
        span = f"  Frequency: {self.freq_span_str()}"
        if self.freq_spacing:
            span += f"  ({self.freq_spacing})"
        out.append(span)
        if math.isfinite(self.s_max):
            out.append(f"  max |S| = {self.s_max:.4g}")
        out.extend(f"  WARN: {w}" for w in self.parser_warnings)
        out.extend(f"  Note: {n}" for n in self.data_notes)
        return out

    def summary(self) -> str:
        return "\n".join(self.summary_lines())


_PORT_NAME_RE = re.compile(r"!\s*[Pp]ort\s*\[?(\d+)\]?\s*[=:]\s*(.+?)\s*$")

# A data line starting with '[' is a Touchstone 2.0 keyword: [Version],
# [Number of Ports], [Network Data], [End], ...  v2 is a different grammar, not
# a superset -- read as v1 the keyword words are skipped as unparseable tokens
# and the numbers that follow them ('[Number of Ports] 4') are injected into
# the data stream, shifting every later value by one slot.  Refused in lenient
# mode too, because "skip the bad tokens" is precisely the wrong answer here.
_V2_KEYWORD_RE = re.compile(r"^\[\s*[A-Za-z][A-Za-z0-9 _]*\s*\]")

# '.s4p' -> 4.  Only the sniffer's tie-break/fallback and the diagnosis pass
# use this.  The parser stays content-based on purpose (EDA tools rename these
# files constantly), but when the content is ambiguous or exceeds
# MAX_SNIFF_NPORTS, the name is the best evidence left about what the file was
# meant to be -- and reporting "the name says 4 ports" is far more use than
# "could not infer port count".
_SNP_EXT_RE = re.compile(r"^\.s(\d+)p$", re.IGNORECASE)

# Head-of-file signatures.  Every one of these used to read as a wall of
# "Skipping unparseable token" warnings followed by a confusing token-count
# error; a compressed or binary file deserves to be named as such.
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_BINARY_MAGIC = (
    (b"\x1f\x8b", "gzip-compressed", "gunzip it first"),
    (b"PK\x03\x04", "a zip archive", "unzip it and load the .sNp inside"),
    (b"BZh", "bzip2-compressed", "bunzip2 it first"),
    (b"\xfd7zXZ", "xz-compressed", "unxz it first"),
    (b"%PDF", "a PDF", "this is not a Touchstone file"),
    (b"\x89PNG", "a PNG image", "this is not a Touchstone file"),
)


def _sniff_encoding(path: Path) -> str:
    """
    Decide the text encoding, or refuse the file as binary.

    Opening everything as UTF-8 with errors='replace' (what this parser did)
    turns a UTF-16 export -- which some EDA tools do write -- into a wall of
    replacement characters, i.e. thousands of skipped tokens and a garbage
    read.  A UTF-8 BOM was just as bad in a subtler way: the BOM glues itself
    to the leading '#', the option line stops being recognised, and the file
    silently parses as '# GHZ S MA R 50' no matter what it actually says.
    """
    with open(path, "rb") as fh:
        head = fh.read(ENCODING_SNIFF_BYTES)
    if not head:
        raise TouchstoneParseError(
            "the file is empty (0 bytes)", path=path, kind=FAULT_FILE,
            verdict="THE FILE is empty; there is nothing to read.",
            hint="check that the export actually wrote something")
    for magic, what, fix in _BINARY_MAGIC:
        if head.startswith(magic):
            raise TouchstoneParseError(
                f"the file is {what}, not text", path=path,
                kind=FAULT_UNSUPPORTED, hint=fix)
    for bom, enc in _BOMS:
        if head.startswith(bom):
            return enc
    if b"\x00" in head:
        # UTF-16 with no BOM writes ASCII as 'x\x00' (LE) or '\x00x' (BE).
        half = max(1, len(head) // 2)
        if head[1::2].count(0) > 0.8 * half:
            return "utf-16-le"
        if head[0::2].count(0) > 0.8 * half:
            return "utf-16-be"
        raise TouchstoneParseError(
            "the file contains NUL bytes, so it is binary, not text",
            path=path, kind=FAULT_FILE,
            hint="check that this is the file you meant, and that the export "
                 "completed")
    return "utf-8"


def _ext_nports(path: Path) -> int | None:
    """Port count implied by a '.sNp' extension, or None."""
    m = _SNP_EXT_RE.match(path.suffix)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if n >= 1 else None


def _decode_options(opt_line: str) -> tuple[str, str, str, float, list[str]]:
    """
    Parse an option line body -> (freq_unit, ptype, fmt, z0, unknown_tokens).

    Unrecognised tokens are returned rather than dropped.  A misspelt format
    keyword used to fall back to the MA default in silence, which reads RI data
    as magnitude/angle and yields a well-formed, completely wrong file.
    """
    tokens = opt_line.upper().split()
    freq_unit, ptype, fmt, z0 = "GHZ", "S", "MA", DEFAULT_Z0
    unknown: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in FREQ_UNIT_SCALE:
            freq_unit = t
        elif t in ("S", "Y", "Z", "H", "G"):
            ptype = t
        elif t in ("RI", "MA", "DB"):
            fmt = t
        elif t == "R" and i + 1 < len(tokens):
            try:
                z0 = float(tokens[i + 1])
                i += 1
            except ValueError:
                unknown.append(tokens[i + 1])
                i += 1
        else:
            unknown.append(t)
        i += 1
    return freq_unit, ptype, fmt, z0, unknown


def parse_touchstone(filepath: str | Path,
                     force_nports: int | None = None,
                     *, lenient: bool = False) -> TouchstoneData:
    """
    Parse a Touchstone file regardless of extension.

    Port count is inferred from file content unless `force_nports` is given.

    Every failure comes out as a `TouchstoneParseError` (a ValueError) whose
    report names a line, a verdict and a next step; nothing escapes as a bare
    traceback, and an unexpected internal failure says so instead of looking
    like a bad file.

    `lenient=True` restores the historical behaviour for a data line that does
    not parse: drop the offending token and carry on.  It is NOT the default,
    because Touchstone is a positional stream -- dropping one number shifts
    every number after it by one slot, so the frequency column starts reading
    S-parameters and the file either fails a divisibility check with a
    meaningless message or, worse, still divides evenly and yields a plausible
    wrong answer.  Refusing is the only response that cannot be silently wrong.

    Memory: the file is streamed line by line and the numbers land in an
    `array.array('d')` (8 bytes per value) fed by a small bounded staging list;
    the result is a zero-copy `np.frombuffer` view of that buffer.  Appending
    every value to one big Python list instead costs ~32 bytes per boxed float
    plus a full second copy at `np.asarray()` time -- roughly 5x the payload,
    which is the difference between opening and not opening an unreduced
    153-port EM export.  Same technique as `reduce_snp.py` (deliberately
    duplicated: that script must stay standalone).

    Note: `np.fromstring(sep=' ')` looks like the obvious fast path but is ~9x
    SLOWER on numpy 2.x (deprecated, unoptimized) and truncates silently on a
    bad token.  Don't switch to it.
    """
    path = Path(filepath)
    try:
        return _parse_touchstone(path, force_nports, lenient)
    except TouchstoneParseError:
        raise
    except MemoryError:
        raise TouchstoneParseError(
            "ran out of memory while reading the file", path=path,
            kind=FAULT_ACCESS,
            hint="shrink it first with reduce_snp.py on a machine that can "
                 "hold it, or close other applications") from None
    except OSError as e:
        raise TouchstoneParseError(
            f"cannot open the file ({e.strerror or e})", path=path,
            kind=FAULT_ACCESS,
            hint="check the path, and that the file is not open in another "
                 "tool that locks it") from e
    except Exception as e:
        # Anything that reaches here is unexpected.  Run the diagnosis pass
        # before blaming either side: if it finds a real defect in the file,
        # say so; if the file looks consistent, this is our bug and the report
        # says that in as many words, with a traceback to paste into the issue.
        diag = _safe_diagnose(path, force_nports)
        kind = (diag.kind if diag.kind in (FAULT_FILE, FAULT_UNSUPPORTED)
                else FAULT_INTERNAL)
        raise TouchstoneParseError(
            f"unexpected {type(e).__name__} inside the parser: {e}",
            path=path, kind=kind, context=diag.lines,
            traceback_text=traceback.format_exc()) from e


def _parse_touchstone(path: Path, force_nports: int | None,
                      lenient: bool) -> TouchstoneData:
    opt_line: str | None = None
    opt_line_no: int | None = None
    extra_opt_lines: list[int] = []
    port_names: dict[int, str] = {}
    warnings_out: list[str] = []
    # Error-path state for _recover_data_line: examples kept, total skipped,
    # and the one-shot notes about separator / exponent spellings.
    rec_state: dict = {"examples": [], "skipped": 0, "notes": []}

    store = array.array("d")
    staging: list[float] = []
    stage = staging.extend

    # Lines split off a comment / option line by an exotic break character; see
    # the note below.  Kept as a small stack so the ordinary path stays a plain
    # `for raw in fh` with no per-line splitlines() cost.
    pending: list[str] = []

    encoding = _sniff_encoding(path)
    with open(path, "r", encoding=encoding, errors="replace") as fh:
        line_iter = iter(fh)
        line_no = 0
        while True:
            if pending:
                line = pending.pop().strip()
            else:
                raw = next(line_iter, None)
                if raw is None:
                    break
                line_no += 1
                line = raw.strip()
            if not line:
                continue
            if line[0] in "#!":
                # str.splitlines() -- what this parser used before it streamed
                # the file -- also breaks on \x0b \x0c \x1c-\x1e \x85
                #  , while iterating a text-mode file breaks only on \n.
                # A comment or option line terminated by a form feed (older EDA
                # dumps page-break their headers) would otherwise swallow the
                # data record that follows it, dropping frequency points with
                # no warning.  Only comment/option lines need the check: every
                # one of those characters is whitespace to str.split(), so a
                # data line containing one still tokenises correctly.
                head, *tail = line.splitlines()
                if tail:
                    pending.extend(reversed(tail))
                    line = head
                if line.startswith("#"):
                    if opt_line is None:
                        opt_line = line[1:].strip()
                        opt_line_no = line_no
                    else:
                        extra_opt_lines.append(line_no)
                    continue
                m = _PORT_NAME_RE.match(line)
                if m:
                    port_names[int(m.group(1))] = m.group(2).strip()
                continue
            if "!" in line:  # strip mid-line comment
                line, _, comment = line.partition("!")
                rest = comment.splitlines()
                if len(rest) > 1:
                    pending.extend(reversed(rest[1:]))
            toks = line.split()
            try:
                # The comprehension is fully evaluated before anything is
                # staged, so the recovery path below cannot double-count the
                # values that preceded the bad token.
                stage([float(t) for t in toks])
            except ValueError:
                stage(_recover_data_line(line, toks, path, line_no,
                                         lenient, rec_state))
            if len(staging) >= PARSE_FLUSH_VALUES:
                store.fromlist(staging)
                del staging[:]
    if staging:
        store.fromlist(staging)
        del staging[:]

    # Zero-copy view over the array('d') buffer; numpy keeps `store` alive.
    # `store` must not be appended to past this point.
    data_values = np.frombuffer(store, dtype=np.float64)

    warnings_out.extend(rec_state["notes"])
    if rec_state["skipped"]:
        warnings_out.extend(rec_state["examples"])
        left = rec_state["skipped"] - len(rec_state["examples"])
        if left > 0:
            warnings_out.append(f"... and {left} more unparseable tokens skipped.")
        warnings_out.append(
            "Every skipped token shifts all following values by one slot: "
            "treat this result as suspect until you have checked the file.")
    if extra_opt_lines:
        shown = ", ".join(str(n) for n in extra_opt_lines[:PARSE_WARN_CAP])
        more = "" if len(extra_opt_lines) <= PARSE_WARN_CAP else ", ..."
        warnings_out.append(
            f"Touchstone v1 allows one option line; the one at line "
            f"{opt_line_no} was used and {len(extra_opt_lines)} later "
            f"'#' line(s) (line {shown}{more}) were ignored.")

    if opt_line is None:
        warnings_out.append("No option line ('#') found; assuming '# GHZ S MA R 50'")
        freq_unit, ptype, fmt, z0 = "GHZ", "S", "MA", DEFAULT_Z0
    else:
        freq_unit, ptype, fmt, z0, unknown_opts = _decode_options(opt_line)
        if unknown_opts:
            warnings_out.append(
                f"Option line token(s) {', '.join(repr(t) for t in unknown_opts)} "
                f"not recognised and ignored; the file is being read as "
                f"'# {freq_unit} {ptype} {fmt} R {z0:g}'. Expected a frequency "
                f"unit (HZ/KHZ/MHZ/GHZ/THZ), S/Y/Z/H/G, RI/MA/DB, or 'R <z0>'.")

    if ptype != "S":
        warnings_out.append(
            f"Parameter type '{ptype}' is not S; treating data as S-parameters anyway."
        )

    if fmt not in ("RI", "MA", "DB"):
        raise TouchstoneParseError(
            f"the option line declares data format '{fmt}', which is not one "
            f"of RI / MA / DB", path=path, kind=FAULT_FILE,
            line_no=opt_line_no, line_text=f"# {opt_line}",
            hint="fix the option line, or delete it to fall back on "
                 "'# GHZ S MA R 50'")

    n_values = int(data_values.size)
    if n_values == 0:
        raise TouchstoneParseError(
            "the file has no numeric data at all -- every line is blank, a "
            "comment, or the option line", path=path, kind=FAULT_FILE,
            context=_safe_diagnose(path, force_nports).lines,
            hint="check that the export wrote its data section, and that this "
                 "is not just a header file")

    if force_nports is not None:
        nports = int(force_nports)
        if nports < 1:
            raise TouchstoneParseError(
                f"forced port count {force_nports} is not >= 1", path=path,
                kind=FAULT_ACCESS, hint="drop the setting to let the port "
                                        "count be detected from the content")
    else:
        try:
            nports = _sniff_nports(data_values, warnings_out, path)
        except ValueError as e:
            # "Could not infer port count" is true but usually not the point:
            # the commonest cause by far is a file truncated mid-record, and
            # sending the user off to force a port count they already have
            # right wastes their afternoon.  The diagnosis pass has looked at
            # the line numbers, so let it write the headline.
            diag = _safe_diagnose(path, None)
            raise TouchstoneParseError(
                diag.headline or str(e), path=path,
                kind=(diag.kind if diag.kind in (FAULT_FILE, FAULT_UNSUPPORTED,
                                                 FAULT_ACCESS)
                      else FAULT_FILE),
                context=diag.lines, verdict=diag.verdict,
                hint=diag.hint or ("if you know the port count, force it "
                                   "(--force-nports N on the CLI)")) from e

    record_size = 1 + 2 * nports * nports
    if n_values % record_size != 0:
        q, r = divmod(n_values, record_size)
        src = ("forced" if force_nports is not None
               else "detected from the content")
        raise TouchstoneParseError(
            f"the data does not divide into whole records: {n_values} numbers "
            f"is {q} complete records of {record_size} plus {r} left over "
            f"(N={nports}, {src})",
            path=path, kind=FAULT_FILE,
            context=_safe_diagnose(path, nports).lines,
            hint="the file is usually truncated -- re-export it; if the port "
                 "count above is wrong, force the right one instead")

    arr = data_values.reshape(-1, record_size)
    freqs_raw = arr[:, 0]
    # Stays a strided view: splitting the trailing axis of a row-contiguous
    # slice never needs a copy.
    body = arr[:, 1:].reshape(-1, nports, nports, 2)

    # Fill s.real / s.imag in place.  `body[..., 0] + 1j * body[..., 1]` is
    # shorter but allocates two full-size complex temporaries, which doubles
    # peak memory on a multi-GB file for no reason.  For MA/DB the magnitude is
    # applied with an in-place complex *= real (numpy buffers the cast in
    # chunks), which is bit-for-bit the same arithmetic as the old
    # `mag * (cos + 1j*sin)` -- verified byte-identical, signed zeros included.
    #
    # RI adds 0.0 rather than assigning straight through.  A plain assignment
    # keeps a "-0" in the file as -0.0, while the old complex expression
    # normalised it to +0.0 ((-0.0) + (+0.0) == +0.0 in IEEE round-to-nearest),
    # and real EDA exports do write '-0.000000e+00'.  np.testing.assert_array_
    # equal cannot see the difference (-0.0 == 0.0), so the golden regression
    # would not have caught the drift.  Measured cost of the fused add: +2%
    # on a 20M-value array, still 4x faster than the old expression.
    #
    # This normalises EVERY signed zero to +0.0, which matches the old
    # expression in all but one corner: a record with BOTH parts written as
    # "-0" used to come out with a -0.0 real part, because Re(1j * -0.0) is
    # -0.0 and (-0.0) + (-0.0) == -0.0.  That asymmetry (the sign of the real
    # zero depending on the sign of the imaginary zero) is not worth
    # reproducing; +0.0 in every case is the sane reading of "this entry is
    # zero".  See tests/test_core.py:TestParserSignedZero.
    #
    # The MA/DB branch is where this parser's peak memory lives, and it is NOT
    # chunked over frequency.  Measured for a 153-port, 5000-frequency export:
    # values 1.87 GB + s 1.87 GB + the three float64 temporaries below 2.81 GB
    # = 6.55 GB peak, against 3.75 GB for the same file written as RI.  Chunking
    # would recover that 2.81 GB, and it was deliberately not done: numpy's
    # sin/cos take a different inner loop depending on where a strided view
    # starts and how the buffered blocks split, so a chunked evaluation is not
    # guaranteed bit-identical to a whole-array one -- and EVERY fixture in
    # tests/fixtures is RI, so golden_legacy.npz would not catch the drift.  An
    # unguarded 1-ULP change on the format most EDA tools export is a worse
    # trade than the memory.  Chunk it only together with an MA and a DB fixture
    # in the golden reference.
    s = np.empty(body.shape[:-1], dtype=complex)
    if fmt == "RI":
        np.add(body[..., 0], 0.0, out=s.real)
        np.add(body[..., 1], 0.0, out=s.imag)
    else:
        ang = np.deg2rad(body[..., 1])
        cos_a = np.cos(ang)
        sin_a = np.sin(ang)
        del ang
        s.real = cos_a
        del cos_a
        s.imag = sin_a
        del sin_a
        if fmt == "MA":
            s *= body[..., 0]
        else:  # DB
            s *= 10.0 ** (body[..., 0] / 20.0)

    # Touchstone v1 quirk: 2-port files write S11 S21 S12 S22 (column-major-ish).
    # For n>2 the layout is row-major. So only n==2 needs a transpose.
    if nports == 2:
        s = s.transpose(0, 2, 1)

    freqs = freqs_raw * FREQ_UNIT_SCALE[freq_unit]

    pn_list = [port_names.get(i + 1, "") for i in range(nports)]

    notes: list[str] = []
    spacing = _check_freq_axis(freqs, freq_unit, warnings_out, notes,
                               forced=force_nports is not None)
    s_max = _check_s_values(s, warnings_out, notes)

    return TouchstoneData(
        nports=nports,
        freqs=freqs,
        s=s,
        z0=z0,
        port_names=pn_list,
        source_path=str(path),
        parser_warnings=warnings_out,
        data_notes=notes,
        freq_unit=freq_unit,
        param_type=ptype,
        data_format=fmt,
        option_line=f"{freq_unit} {ptype} {fmt} R {z0:g}",
        freq_spacing=spacing,
        s_max=s_max,
    )


def _recover_data_line(line: str, toks: list[str], path: Path, line_no: int,
                       lenient: bool, state: dict) -> list[float]:
    """
    Error path for a data line that did not parse as plain floats.

    Never runs on a healthy file, so it can afford to be thorough: it names
    Touchstone 2.0 keyword lines for what they are, retries the two separator /
    exponent spellings real tools emit, and only then gives up.

    Returns the line's values.  Raises unless `lenient`, in which case it falls
    back to the historical token-by-token skip -- see parse_touchstone on why
    that is not the default.
    """
    if _V2_KEYWORD_RE.match(line):
        raise TouchstoneParseError(
            "this is a Touchstone 2.0 file (keyword lines in [brackets]); "
            "this tool reads Touchstone 1.x", path=path,
            kind=FAULT_UNSUPPORTED, line_no=line_no, line_text=line,
            hint="re-export as Touchstone 1.x (.sNp). Reading a v2 file as v1 "
                 "would inject the keywords' own numbers into the data, so "
                 "skipping the bad tokens is not an option here")

    # Commas or semicolons used as separators ('1e9, 0.5, -0.5').
    if "," in line or ";" in line:
        vals = _floats_or_none(line.replace(",", " ").replace(";", " ").split())
        if vals is not None:
            if "comma" not in state:
                state["comma"] = True
                state["notes"].append(
                    "Commas/semicolons in data lines were treated as value "
                    "separators.")
            return vals

    # Fortran-style D exponents ('1.0D+09'), still emitted by older tools.
    if "d" in line or "D" in line:
        vals = _floats_or_none([t.replace("D", "E").replace("d", "e")
                                for t in toks])
        if vals is not None:
            if "dexp" not in state:
                state["dexp"] = True
                state["notes"].append(
                    "Fortran-style 'D' exponents were read as 'E' exponents.")
            return vals

    bad = next((t for t in toks if _to_float(t) is None), toks[0] if toks else "")
    if not lenient:
        raise TouchstoneParseError(
            f"'{_clip(bad, 30)}' is not a number, and a Touchstone data line "
            f"is nothing but numbers", path=path, kind=FAULT_FILE,
            line_no=line_no, line_text=line, retry_lenient=True,
            hint="if the rest of the file is known good, load it again with "
                 "'skip bad values' (--lenient) -- but check the result: "
                 "dropping one number shifts every number after it by one slot")

    out: list[float] = []
    for tok in toks:
        val = _to_float(tok)
        if val is None:
            state["skipped"] += 1
            if len(state["examples"]) < PARSE_WARN_CAP:
                state["examples"].append(
                    f"Skipping unparseable token '{_clip(tok, 30)}' "
                    f"(line {line_no})")
        else:
            out.append(val)
    return out


def _to_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _floats_or_none(tokens: Sequence[str]) -> list[float] | None:
    """All-or-nothing float conversion: a partial success is not a success."""
    out = []
    for t in tokens:
        try:
            out.append(float(t))
        except ValueError:
            return None
    return out


def _check_freq_axis(freqs: np.ndarray, freq_unit: str,
                     warnings_out: list[str], notes: list[str],
                     forced: bool) -> str:
    """
    Describe the frequency axis and flag what will bite downstream.

    Returns the spacing description for the file summary.  The monotonicity
    check matters only when the port count was forced -- when it is sniffed,
    strictly-increasing frequencies are one of the two conditions the sniffer
    selects on, so it cannot fail here.  A forced port count skips that
    entirely, and a wrong one shows up first as a frequency column that jumps
    around.
    """
    n = freqs.size
    if n == 0:
        return ""
    if n == 1:
        spacing = "single point"
    else:
        d = np.diff(freqs)
        bad = int(np.count_nonzero(d <= 0))
        if bad:
            first = int(np.argmax(d <= 0))
            warnings_out.append(
                f"Frequencies are not strictly increasing: point {first + 2} "
                f"({format_freq(float(freqs[first + 1]))}) does not exceed "
                f"point {first + 1} ({format_freq(float(freqs[first]))}), and "
                f"{bad} step(s) in total go backwards or repeat."
                + (" A forced port count that is wrong looks exactly like "
                   "this." if forced else ""))
            spacing = "NOT monotonic"
        elif np.allclose(d, d[0], rtol=1e-6, atol=0.0):
            spacing = f"linear, step {format_freq(float(d[0]))}"
        elif freqs[0] > 0 and np.allclose(freqs[1:] / freqs[:-1],
                                          freqs[1] / freqs[0], rtol=1e-6):
            per_dec = math.log(10.0) / math.log(float(freqs[1] / freqs[0]))
            spacing = f"logarithmic, {per_dec:.0f} points/decade"
        else:
            spacing = "irregular spacing"

    if freqs[0] == 0.0:
        notes.append(
            "The sweep starts at DC (0 Hz). L = Im(Z)/ω, C = -1/(ω·Im(Z)) and "
            "Q are undefined at that point and will read as nan/inf; pick any "
            "other frequency for the R/L/C extraction.")
    return spacing


def _check_s_values(s: np.ndarray, warnings_out: list[str],
                    notes: list[str]) -> float:
    """
    max |S| over the file, plus a flag for the two ways S data goes wrong.

    Chunked over frequency for the same reason as the linear algebra: np.abs
    on a whole (5000, 153, 153) array allocates a ~1 GB float temporary.
    """
    if s.size == 0:
        return float("nan")
    batch = _freq_batch(s.shape[-1])
    s_max = 0.0
    n_bad = 0
    for start in range(0, s.shape[0], batch):
        chunk = s[start:start + batch]
        mag = np.abs(chunk)
        finite = np.isfinite(mag)
        if not finite.all():
            n_bad += int(finite.size - np.count_nonzero(finite))
            mag = np.where(finite, mag, 0.0)
        if mag.size:
            s_max = max(s_max, float(mag.max()))
    if n_bad:
        warnings_out.append(
            f"{n_bad} S-parameter entries are nan or inf; every result at "
            f"those frequencies will be nan.")
    if s_max > 1.05:
        notes.append(
            f"max |S| = {s_max:.4g} > 1. That is correct for an active or "
            f"gain structure, but on a passive one it usually means the "
            f"option line's format (RI / MA / DB) does not match how the "
            f"numbers were actually written.")
    return s_max


# Elements of the frequency column compared per pass in _strictly_increasing.
# The bool temporary is one byte per element, so this is a 64 KB working set
# whatever the file size -- see the function.
_MONO_CHUNK = 1 << 16


def _strictly_increasing(x: np.ndarray) -> bool:
    """
    Is every element greater than the one before it?  Chunked, with early exit.

    `np.all(np.diff(x) > 0)` says the same thing and was what the sniffer used,
    but it says it by materialising the whole difference -- and the candidates
    that cost the most are the WRONG ones, where the strided view is enormous
    (N=1 on a 153-port file is a third of every number in it) and the answer is
    settled by the first pair.  Measured on the N=1 view of a 153-port,
    600-frequency array (9.36M elements): 85.1 ms and 84 MB of temporaries for
    the np.diff form, against 64 KB and one chunk here.  Whole-sniff A/B on the
    same arrays: N=153/F=900 59.7 ms -> 0.18 ms, N=153/F=600 27.1 ms -> 0.06 ms,
    N=60/F=4000 7.8 ms -> 0.19 ms.  That cost was proportional to the file: the
    sniffer walked ~T/3 + T/9 + ... values before it answered.
    It also stopped numpy printing "invalid value encountered in subtract" to
    fd 2 on a file carrying inf/nan, where a double-clicked GUI has no fd 2.

    `a - b > 0` and `a > b` agree for every pair of IEEE doubles -- two distinct
    finite doubles never subtract to zero (gradual underflow makes the
    subtraction exact when they are close), and nan/inf pairs answer False both
    ways -- so this is the same predicate, not a looser one.  Fuzz-checked over
    2000 random arrays drawn from {0, -0, +-1, +-inf, nan, +-1e308, denormals}.
    """
    n = int(x.size)
    if n < 2:
        return True
    for start in range(0, n - 1, _MONO_CHUNK):
        # +1 so the pair straddling the chunk boundary is tested too.
        chunk = x[start:min(start + _MONO_CHUNK + 1, n)]
        if not bool(np.all(chunk[1:] > chunk[:-1])):
            return False
    return True


def _sniff_range(values: np.ndarray, lo: int, hi: int) -> list[int]:
    """Port counts in [lo, hi] whose record size fits; at most three of them."""
    T = int(values.size)
    out: list[int] = []
    for n in range(lo, hi + 1):
        rec = 1 + 2 * n * n
        if T % rec != 0:
            continue
        if _strictly_increasing(values[0::rec]):
            out.append(n)
            if len(out) >= 3:
                break
    return out


def _sniff_nports(values: np.ndarray, warnings_out: list[str],
                  path: Path | None = None) -> int:
    """
    Find the smallest N whose record size fits and whose frequencies increase.

    Four steps, each more speculative than the last, and each one says in a WARN
    line that it was reached:

      1. the content, N = 1..MAX_SNIFF_NPORTS.  Content first, always: EDA tools
         rename these files constantly, so the extension is not evidence of
         anything on its own.  Silent when it gives one answer.
      2. the file name, when the content gave none -- but only after
         `_nports_fits` has checked the name against the content, so it is
         corroborated evidence, not a label.  It stays AHEAD of step 3 because
         at N > 256 a bare divisibility hit rests on the arithmetic of one huge
         number with nothing else agreeing with it, while the name is what the
         exporter said the file was.  When both agree the answer is identical;
         when they disagree the name is the only external evidence there is.
      3. the content again, N up to SNIFF_HARD_CAP.  This is the renamed 300-port
         package export -- the one case where the tool used to have nothing to
         offer but force_nports.
      4. refuse, naming the cap that was exceeded and the way past it.

    The extension also breaks a tie inside step 1: picking the smallest
    candidate silently is how a 2-port file gets read as a 1-port one.
    """
    T = int(values.size)
    if T == 0:
        raise ValueError("No data tokens found in file")
    ext_n = _ext_nports(path) if path is not None else None

    n_possible = _max_possible_nports(T)
    candidates = _sniff_range(values, 1, min(MAX_SNIFF_NPORTS, n_possible))
    if candidates:
        if len(candidates) > 1:
            if ext_n in candidates:
                warnings_out.append(
                    f"Port count ambiguous: candidates {candidates}. The file "
                    f"name says N={ext_n}, which is one of them, so that is "
                    f"what was used.")
                return ext_n
            warnings_out.append(
                f"Port count ambiguous: candidates {candidates}. "
                f"Using N={candidates[0]}.")
        return candidates[0]

    if ext_n is not None and _nports_fits(values, ext_n):
        warnings_out.append(
            f"Port count could not be detected from the content "
            f"(nothing up to N={MAX_SNIFF_NPORTS} fits {T} numbers); the "
            f"file name says N={ext_n}, which does fit, so that is what "
            f"was used.")
        return ext_n

    wide = _sniff_range(values, MAX_SNIFF_NPORTS + 1,
                        min(SNIFF_HARD_CAP, n_possible))
    if wide:
        also = (f" ({len(wide) - 1} other port count(s) also fit: "
                f"{wide[1:]})" if len(wide) > 1 else "")
        warnings_out.append(
            f"Port count N={wide[0]} was found only by searching past "
            f"N={MAX_SNIFF_NPORTS}: nothing at or below that fits {T} numbers "
            f"and the file name says nothing usable{also}. Nothing corroborates "
            f"it, so check the port count on the numbers you get -- and if it "
            f"is wrong, force the right one (force_nports=N, --force-nports N "
            f"on the CLI).")
        return wide[0]

    raise ValueError(
        f"Could not infer port count from {T} tokens: no N from 1 to "
        f"{_sniff_reach(T)} divides them into whole records with an increasing "
        f"frequency column. Pass force_nports if you know it.")


def _max_possible_nports(n_values: int) -> int:
    """
    Largest N whose record could divide `n_values` at all.

    rec = 1 + 2N^2 > T > 0 gives T % rec == T != 0, so no larger N can ever be a
    candidate.  Bounding the sweeps by this is what keeps SNIFF_HARD_CAP free on
    ordinary files -- the 2-port fixture's 3609 values stop it at N=42.
    """
    return math.isqrt(max(0, (n_values - 1) // 2))


def _sniff_reach(n_values: int) -> int:
    """Largest N _sniff_nports could have tried for a file of `n_values`."""
    return min(SNIFF_HARD_CAP, _max_possible_nports(n_values))


def _nports_fits(values: np.ndarray, n: int) -> bool:
    """The two tests _sniff_nports selects on, for one candidate N."""
    rec = 1 + 2 * n * n
    if int(values.size) % rec != 0:
        return False
    return _strictly_increasing(values[0::rec])


# ============================================================================
# File diagnosis -- the slow second pass, run only when something is wrong
# ============================================================================
#
# The fast path above is written for a multi-GB file: it streams, it stages
# into an array('d'), it keeps no per-line state.  That is exactly why its
# errors could only ever be global ("token count 8241 not divisible by 33"),
# which tells a user nothing about whether their file is truncated, their port
# count is wrong, or this tool is broken.
#
# So the bookkeeping lives here instead, in a second pass that runs ONLY after
# a failure (or when the user asks for it).  It can afford a line number per
# data line and a token count per line, and it is what turns "not divisible"
# into "the file ends mid-record at line 4831".  Nothing here is on the hot
# path; nothing here may raise.

@dataclass
class _Diagnosis:
    kind: str
    lines: list[str]
    # One-line statement of what is actually wrong, for a caller that has a
    # worse headline of its own.  The sniffer, for instance, can only ever
    # report "could not infer port count" -- true, but on a truncated file it
    # points the user at the wrong thing entirely.
    headline: str | None = None
    hint: str | None = None
    # Overrides the _VERDICT text for `kind` when the diagnosis can say
    # something more specific than the fault class can.  "Your file may simply
    # have more ports than this tool will guess at" is not "THE FILE is
    # inconsistent", and printing the latter sends the user to re-export a file
    # that was never broken.
    verdict: str | None = None


@dataclass
class _LineScan:
    encoding: str = "utf-8"
    n_lines: int = 0
    n_comment: int = 0
    n_data: int = 0
    option_lines: list[tuple[int, str]] = field(default_factory=list)
    v2_keywords: list[tuple[int, str]] = field(default_factory=list)
    bad_tokens: list[tuple[int, str, str]] = field(default_factory=list)
    n_bad: int = 0
    counts: dict[int, int] = field(default_factory=dict)
    first_of_count: dict[int, int] = field(default_factory=dict)
    values: array.array = field(default_factory=lambda: array.array("d"))
    # Parallel arrays: value offset at the start of each data line, and that
    # line's number.  bisect over `starts` maps a value index back to a line.
    starts: array.array = field(default_factory=lambda: array.array("q"))
    dlines: array.array = field(default_factory=lambda: array.array("q"))
    truncated: bool = False
    # Value offset of the first data line the head index above did NOT record,
    # and a ring of the last DIAGNOSE_TAIL_LINES lines.  A truncated file breaks
    # at its END, which on anything big is past the head -- see _value_line.
    head_end: int = -1
    tail_starts: deque = field(
        default_factory=lambda: deque(maxlen=DIAGNOSE_TAIL_LINES))
    tail_dlines: deque = field(
        default_factory=lambda: deque(maxlen=DIAGNOSE_TAIL_LINES))


def _scan_lines(path: Path, encoding: str) -> _LineScan:
    """Line-by-line scan with the bookkeeping the fast path cannot afford."""
    scan = _LineScan(encoding=encoding)
    pending: list[str] = []
    with open(path, "r", encoding=encoding, errors="replace") as fh:
        line_iter = iter(fh)
        line_no = 0
        while True:
            if pending:
                line = pending.pop().strip()
            else:
                raw = next(line_iter, None)
                if raw is None:
                    break
                line_no += 1
                scan.n_lines += 1
                line = raw.strip()
            if not line:
                continue
            # Same exotic-line-break rule as the parser, so the report
            # describes what the parser actually saw.
            if line[0] in "#!":
                head, *tail = line.splitlines()
                if tail:
                    pending.extend(reversed(tail))
                    line = head
                scan.n_comment += 1
                if line.startswith("#"):
                    scan.option_lines.append((line_no, line))
                continue
            if "!" in line:
                line, _, comment = line.partition("!")
                rest = comment.splitlines()
                if len(rest) > 1:
                    pending.extend(reversed(rest[1:]))
                line = line.strip()
                if not line:
                    continue
            if _V2_KEYWORD_RE.match(line):
                scan.v2_keywords.append((line_no, line))
                continue
            toks = line.split()
            if not toks:
                continue
            scan.n_data += 1
            if len(scan.dlines) < DIAGNOSE_MAX_LINES:
                scan.starts.append(len(scan.values))
                scan.dlines.append(line_no)
            else:
                if not scan.truncated:
                    scan.truncated = True
                    scan.head_end = len(scan.values)
                scan.tail_starts.append(len(scan.values))
                scan.tail_dlines.append(line_no)
            good = 0
            for tok in toks:
                val = _to_float(tok)
                if val is None:
                    scan.n_bad += 1
                    if len(scan.bad_tokens) < PARSE_WARN_CAP:
                        scan.bad_tokens.append((line_no, tok, line))
                else:
                    scan.values.append(val)
                    good += 1
            scan.counts[good] = scan.counts.get(good, 0) + 1
            scan.first_of_count.setdefault(good, line_no)
    return scan


def _value_line(scan: _LineScan, index: int) -> int | None:
    """
    Physical line number holding value `index`, or None if not recorded.

    Two indexes, because DIAGNOSE_MAX_LINES stops recording at the head of a big
    file and a truncated file breaks at its end.  Falling off the head used to
    return the LAST recorded line -- bisect_right lands past the array and the
    clamp reads back its final entry -- so on a file with more data lines than
    the cap the report said "the leftover starts at line 2000000" about a break
    at line 17000000.  Demonstrated with the cap patched down to 10 on a
    41-data-line file: it named line 11 for a break at line 42.  A wrong line
    number in the one report whose whole job is naming the line is worse than no
    line number, so past the head this answers from the tail ring or not at all.
    """
    if index < 0:
        return None
    if not scan.truncated or index < scan.head_end:
        if not scan.dlines:
            return None
        i = bisect.bisect_right(scan.starts, index) - 1
        if i < 0 or i >= len(scan.dlines):
            return None
        return int(scan.dlines[i])
    if scan.tail_starts and index >= scan.tail_starts[0]:
        # Materialised because bisect indexes a deque in O(n); one 64K-element
        # list per candidate, on an error path, is not worth a second structure.
        starts = list(scan.tail_starts)
        i = bisect.bisect_right(starts, index) - 1
        if 0 <= i < len(scan.tail_dlines):
            return int(scan.tail_dlines[i])
    return None


def _nports_from_record(n_values: int) -> int | None:
    """N such that 1 + 2N^2 == n_values, if that N is a whole number."""
    if n_values < 3 or n_values % 2 != 1:
        return None
    sq = (n_values - 1) // 2
    n = int(round(math.sqrt(sq)))
    return n if n >= 1 and n * n == sq else None


def _diag_candidate(scan: _LineScan, values: np.ndarray, n: int, source: str,
                    out: list[str]) -> bool:
    """Report how candidate port count `n` fits the data. True == consistent."""
    rec = 1 + 2 * n * n
    total = int(values.size)
    q, r = divmod(total, rec)
    head = f"  N={n} ({source}): {rec} numbers per record"
    if r:
        out.append(head)
        out.append(f"      {total} numbers = {q} whole records + {r} left over")
        line = _value_line(scan, q * rec)
        if line is not None:
            out.append(f"      the leftover starts at line {line} -- the file "
                       f"ends mid-record there")
        elif scan.truncated:
            # Say why the line is missing.  Omitting it silently reads as "the
            # tool did not look", which is the wrong complaint to send upstream.
            out.append(f"      (the leftover is past the first "
                       f"{DIAGNOSE_MAX_LINES} data lines and further back than "
                       f"the {DIAGNOSE_TAIL_LINES}-line tail window, so its "
                       f"line number was not recorded)")
        return False
    freqs = values[0::rec] if rec <= total else values[:0]
    if freqs.size >= 2:
        d = np.diff(freqs)
        bad = np.nonzero(d <= 0)[0]
        if bad.size:
            k = int(bad[0]) + 1
            line = _value_line(scan, k * rec)
            where = f" (line {line})" if line is not None else ""
            out.append(head)
            out.append(f"      {q} whole records, but record {k + 1}{where} "
                       f"has frequency {freqs[k]:.6g}, which does not exceed "
                       f"record {k}'s {freqs[k - 1]:.6g}")
            return False
    out.append(f"{head} -> {q} whole records, frequencies strictly "
               f"increasing: CONSISTENT")
    return True


def _diagnose(path: Path, force_nports: int | None = None) -> _Diagnosis:
    out = [f"File check: {path}"]
    try:
        out.append(f"  size       : {path.stat().st_size:,} bytes")
    except OSError as e:
        return _Diagnosis(FAULT_ACCESS, out + [f"  cannot stat the file: {e}"])
    try:
        encoding = _sniff_encoding(path)
    except TouchstoneParseError as e:
        return _Diagnosis(e.kind, out + [f"  encoding   : {e.what}"])
    out.append(f"  encoding   : {encoding}")

    scan = _scan_lines(path, encoding)
    out.append(f"  lines      : {scan.n_lines} total, {scan.n_comment} "
               f"comment/option, {scan.n_data} data")
    if scan.option_lines:
        ln, text = scan.option_lines[0]
        out.append(f"  option line: line {ln}: {_clip(text)}")
        if len(scan.option_lines) > 1:
            out.append(f"               plus {len(scan.option_lines) - 1} "
                       f"later '#' line(s), which v1 ignores")
    else:
        out.append("  option line: MISSING -- '# GHZ S MA R 50' assumed")

    if scan.counts:
        ranked = sorted(scan.counts.items(), key=lambda kv: (-kv[1], kv[0]))
        modal, how_many = ranked[0]
        out.append(f"  data lines : {how_many} line(s) carry {modal} numbers")
        for cnt, lines_with in ranked[1:1 + PARSE_WARN_CAP]:
            out.append(f"               {lines_with} line(s) carry {cnt} "
                       f"(first at line {scan.first_of_count.get(cnt, 0)})")
    if scan.truncated:
        out.append(f"  (line numbers were recorded for the first "
                   f"{DIAGNOSE_MAX_LINES} data lines and the last "
                   f"{DIAGNOSE_TAIL_LINES}; the middle of the file was not "
                   f"indexed)")

    for ln, text in scan.v2_keywords[:PARSE_WARN_CAP]:
        out.append(f"  v2 keyword : line {ln}: {_clip(text)}")
    for ln, tok, text in scan.bad_tokens:
        out.append(f"  not a number: line {ln}: '{_clip(tok, 30)}' in "
                   f"{_clip(text, 56)}")
    if scan.n_bad > len(scan.bad_tokens):
        out.append(f"                ... and {scan.n_bad - len(scan.bad_tokens)} "
                   f"more")

    total = len(scan.values)
    out.append(f"  numbers    : {total}")

    candidates: list[tuple[int, str]] = []

    def _add(n: int | None, source: str) -> None:
        if n and all(n != c for c, _ in candidates):
            candidates.append((n, source))

    _add(force_nports, "forced")
    _add(_ext_nports(path), f"the file name '{path.suffix}'")
    if scan.counts:
        modal = max(scan.counts.items(), key=lambda kv: kv[1])[0]
        _add(_nports_from_record(modal), "one record per line")
    values = (np.frombuffer(scan.values, dtype=np.float64) if total
              else np.zeros(0))
    if total:
        try:
            _add(_sniff_nports(values, [], path), "content sniffing")
        except ValueError:
            out.append(f"  port count : nothing from N=1 to "
                       f"N={_sniff_reach(total)} fits {total} numbers with an "
                       f"increasing frequency column")

    consistent = [n for n, src in candidates
                  if _diag_candidate(scan, values, n, src, out)]

    headline: str | None = None
    hint: str | None = None
    verdict: str | None = None
    if scan.v2_keywords:
        kind = FAULT_UNSUPPORTED
        ln = scan.v2_keywords[0][0]
        headline = (f"this is a Touchstone 2.0 file (keyword lines in "
                    f"[brackets], first at line {ln}); this tool reads "
                    f"Touchstone 1.x")
        hint = "re-export as Touchstone 1.x (.sNp)"
        out.append("  VERDICT    : this is a Touchstone 2.0 file (keyword "
                   "lines in [brackets]). This tool reads Touchstone 1.x -- "
                   "re-export as .sNp.")
    elif scan.n_bad:
        kind = FAULT_FILE
        ln, tok, _text = scan.bad_tokens[0]
        headline = (f"the data section contains non-numeric text "
                    f"('{_clip(tok, 30)}' at line {ln}, {scan.n_bad} token(s) "
                    f"in total)")
        out.append("  VERDICT    : THE FILE contains non-numeric text in its "
                   "data section, at the lines named above.")
    elif total == 0:
        kind = FAULT_FILE
        headline = "the file has no numeric data at all"
        out.append("  VERDICT    : THE FILE has no numeric data at all. If it "
                   "is not empty, it is not a Touchstone file.")
    elif consistent and consistent[0] > _sniff_reach(total):
        # The file is fine and the parser would still not open it: N is past the
        # cap on what the sniffer will infer, and the file name did not say.
        # The "please report a parser bug" wording below must NOT stand for this
        # -- it is a documented limit with a documented way out, and sending the
        # user to file an issue instead of typing --force-nports wastes both
        # their time and ours.  The kind stays FAULT_NONE because it is true:
        # nothing is wrong with the file, so `--diagnose` still exits 0.
        kind = FAULT_NONE
        n_ok = consistent[0]
        headline = (f"the data reads cleanly as N={n_ok}, which is past the "
                    f"N={SNIFF_HARD_CAP} this tool will infer from the numbers "
                    f"alone")
        hint = (f"force the port count (force_nports={n_ok} in the API, "
                f"--force-nports {n_ok} on the CLI)")
        verdict = (f"THE FILE looks fine. THE PARSER will not guess a port "
                   f"count above N={SNIFF_HARD_CAP}, so a file this wide has to "
                   f"be opened with its port count given explicitly.")
        out.append(f"  VERDICT    : the data reads cleanly as N={n_ok}, but the "
                   f"port-count search stops at N={SNIFF_HARD_CAP}. Nothing is "
                   f"wrong with the file -- open it with --force-nports {n_ok}.")
    elif consistent:
        kind = FAULT_NONE
        out.append(f"  VERDICT    : no inconsistency found -- the data reads "
                   f"cleanly as N={consistent[0]}. If this tool still refuses "
                   f"the file, or the numbers look wrong, that is a PARSER "
                   f"problem, not a file problem: please report it with this "
                   f"block.")
    elif candidates:
        kind = FAULT_FILE
        n, src = candidates[0]
        rec = 1 + 2 * n * n
        left = total % rec
        headline = (f"the data does not divide into whole records for any "
                    f"plausible port count -- at N={n} ({src}) it is "
                    f"{total // rec} records of {rec} plus {left} left over")
        hint = ("the file is usually truncated -- re-export it; if the port "
                "count above is wrong, force the right one instead")
        out.append("  VERDICT    : THE FILE does not divide into whole "
                   "records for any plausible port count -- see above. It is "
                   "usually truncated.")
    else:
        kind = FAULT_FILE
        reach = _sniff_reach(total)
        headline = (f"the port count could not be established: no N from 1 to "
                    f"{reach} divides {total} numbers into whole records with "
                    f"an increasing frequency column")
        hint = ("pass the port count explicitly (force_nports=N in the API, "
                "--force-nports N on the CLI)")
        if reach >= SNIFF_HARD_CAP:
            # Naming the cap only belongs here.  On a small file the search
            # stopped because no record size could divide the numbers, not
            # because it ran out of road, and mentioning a cap it never came
            # near just invites the user to go looking for a setting.
            hint += (f"; nothing above N={SNIFF_HARD_CAP} is inferred from the "
                     f"numbers alone")
            # The search stopped at the cap rather than at what the file could
            # hold, so "too many ports" is genuinely on the table.  Say both
            # readings: no test on a single number can separate a 5000-port
            # export from a corrupt file, and claiming the file is broken sends
            # the user to re-export something that may be perfectly good.
            verdict = (
                f"EITHER THE FILE has more ports than this tool will infer "
                f"(the search stopped at N={SNIFF_HARD_CAP}) OR its data "
                f"section is inconsistent. The numbers alone cannot tell those "
                f"apart -- forcing the port count is what settles it.")
            out.append(f"  VERDICT    : THE FILE's port count could not be "
                       f"established at all, and the search stopped at the "
                       f"N={SNIFF_HARD_CAP} cap -- so it may simply have more "
                       f"ports than that. Force it if you know it.")
        else:
            out.append("  VERDICT    : THE FILE's port count could not be "
                       "established at all. Force it if you know it.")
    return _Diagnosis(kind, out, headline, hint, verdict)


def _safe_diagnose(path: Path, force_nports: int | None = None) -> _Diagnosis:
    """_diagnose, guaranteed not to raise -- it runs inside error paths."""
    try:
        return _diagnose(path, force_nports)
    except Exception as e:                                  # pragma: no cover
        return _Diagnosis(FAULT_INTERNAL,
                          [f"File check failed: {type(e).__name__}: {e}"])


def check_touchstone(filepath: str | Path,
                     force_nports: int | None = None) -> tuple[str, str]:
    """
    (fault_kind, report) for a Touchstone file.  Never raises.

    `fault_kind` is FAULT_NONE when nothing is wrong with the file; the CLI
    turns it into an exit code, which is what makes --diagnose usable from a
    script.
    """
    diag = _safe_diagnose(Path(filepath), force_nports)
    return diag.kind, "\n".join(diag.lines)


def diagnose_touchstone(filepath: str | Path,
                        force_nports: int | None = None) -> str:
    """
    Human-readable report on what a Touchstone file contains and whether it
    hangs together.  Never raises; the last line is always a VERDICT naming
    the file or this tool as the problem.
    """
    return check_touchstone(filepath, force_nports)[1]


# ============================================================================
# Display helpers
# ============================================================================

# (exponent, prefix) pairs covering 1e-15 .. 1e12. Note 'u' is used in place of
# 'µ' because Tk Text in some Windows fonts mis-renders the multibyte char and
# breaks column alignment.
_SI_PREFIXES = [
    (-15, "f"), (-12, "p"), (-9, "n"), (-6, "u"), (-3, "m"),
    (0, ""), (3, "k"), (6, "M"), (9, "G"), (12, "T"),
]


def format_si(value: float, unit: str = "", sig: int = 3) -> str:
    """
    Format a number with an SI prefix and `sig` significant digits.

    Examples:
        format_si(0.000345, "H")   -> "345 uH"     (because 3.45e-4 H = 345 uH)
        format_si(345e-12, "H")    -> "345 pH"
        format_si(-1.234e-9, "H")  -> "-1.23 nH"
        format_si(0.0, "Ω")        -> "0.00 Ω"
        format_si(float('nan'))    -> "nan"
        format_si(float('inf'))    -> "inf"

    The chosen prefix is the largest one whose scaled value has |x| >= 1
    (with 'f' as the floor). Sig-fig rounding is applied to the scaled
    value, so the textual length is bounded.
    """
    if not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if value == 0.0:
        return f"{0.0:.{sig - 1}f}" + (f" {unit}" if unit else "")

    abs_v = abs(value)
    log10 = math.log10(abs_v)
    # Pick the largest prefix exponent <= log10, clamped to the table range.
    chosen = _SI_PREFIXES[0]
    for exp, pfx in _SI_PREFIXES:
        if log10 >= exp:
            chosen = (exp, pfx)
        else:
            break
    exp, pfx = chosen
    scaled = value / (10 ** exp)
    # `sig` significant digits via %g, then strip a trailing '.' if any.
    text = f"{scaled:.{sig}g}"
    suffix = pfx + unit
    return f"{text} {suffix}" if suffix else text


def format_freq(value: float, sig: int = 3) -> str:
    """'0 Hz', '1 MHz', '2.44 GHz' -- format_si with a Hz unit.

    DC is special-cased so a sweep starting at zero reads '0 Hz' and not
    '0.00 Hz'; it is the one frequency people read as a label rather than as a
    measurement.
    """
    if value == 0.0:
        return "0 Hz"
    return format_si(value, "Hz", sig)
