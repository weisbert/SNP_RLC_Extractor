#!/usr/bin/env python3
"""
Touchstone S-parameter Port Reduction Tool
===========================================
Reduces a large .sNp Touchstone file to keep only the ports you care about.

Every port in the original file falls into exactly one of three buckets:

  KEEP    -- listed in a normal group in the port config; becomes a port of the
             output file.
  GND     -- listed under a group named `GND` / `GROUND` / `SHORT`; shorted to
             the Touchstone reference node. In Y-domain this is simply deleting
             that row and column (V = 0).
  UNUSED  -- everything else; eliminated by a Schur complement. What "eliminated"
             means depends on --method:
               open    (default) floating pin, I = 0. This is what Cadence
                       Spectre does with an unconnected nport pin.
               matched pin terminated in Z0 to ground (adds Y0 to the diagonal
                       before elimination). With no GND ports this is exactly
                       the S-parameter sub-matrix.

Usage:
    python3 reduce_snp.py input.s153p --ports ports.txt -o reduced.s46p
    python3 reduce_snp.py input.s153p --ports ports.txt --method matched
    python3 reduce_snp.py input.s153p --ports ports.txt --order config --check-passivity
    python3 reduce_snp.py input.s153p --keep RX=1,2,3 --keep 4:1:17,80 --gnd 100:1:153

Port config file format (`#` lines = group headers, entries = 1-indexed port
numbers OR port names taken from the `! Port[n] = name` comments):

    # GND
    5, 6, 7, 88
    # DTC
    11, 141, 70, 71
    # RX
    VDD_RX_1   VDD_RX_2

A port entry may also be a numeric RANGE, so a package's ground balls fit on one
line:

    1, 2, 3, 4:1:17, 80        # `start:step:stop`, inclusive (MATLAB style)
    6-14                       # `start-stop`, inclusive

Ranges are recognised only when the whole token is numeric, so a port *named*
`VDD-1` or `I0:VDD` is still resolved as a name. A token that is both a valid
range and an exact port name is refused rather than guessed.

The file is read the way a hand-written file has to be read: the encoding is
sniffed (a BOM, or Notepad's UTF-16 "Unicode", or GBK), full-width punctuation
from a CJK input method is accepted (`31：1：52` is `31:1:52`), and a `#` after
the ports on a line starts a comment.

Standalone by design: numpy + stdlib only, no imports from this repo, so it can
be dropped onto a simulation server on its own.
"""

import argparse
import array
import codecs
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

MAX_SNIFF_NPORTS = 512
GND_GROUP_NAMES = {"GND", "GROUND", "SHORT", "SHORTED", "AGND", "DGND"}
FREQ_UNITS = ("HZ", "KHZ", "MHZ", "GHZ", "THZ")

# `! Port[3] = name`, `! Port 3 = name`, `! Port3: name` -- HFSS / Q3D / SIwave all differ.
_PORT_NAME_RES = (
    re.compile(r"!\s*Port\s*\[\s*(\d+)\s*\]\s*[=:]\s*(\S.*)", re.I),
    re.compile(r"!\s*Port\s*(\d+)\s*[=:]\s*(\S.*)", re.I),
)


# ============================================================
# Port Config Parser
# ============================================================
# A range token must be numeric END TO END, because `-` and `:` are ordinary
# characters in a net name (`VDD-1`, `I0:VDD`) and those must keep resolving as
# names. `start:step:stop` mirrors the GUI's `parse_port_range` syntax.
_RANGE_COLON_RE = re.compile(r"^(\d+):([+-]?\d+):(\d+)$")
_RANGE_DASH_RE = re.compile(r"^(\d+)-(\d+)$")
# `4 : 1 : 17` is one range, not three tokens. Joining only between digits keeps
# a name-bearing colon (`Port1: foo`) out of it.
_RANGE_SPACE_RE = re.compile(r"(?<=\d)\s*:\s*(?=[+-]?\d)")
_TOKEN_SPLIT_RE = re.compile(r"[,;\s]+")
# `#` starts a comment only at the start of a token, so a port *named* `NET#3`
# survives. At the start of a LINE it is a group header and never reaches here.
_HASH_COMMENT_RE = re.compile(r"(?:^|(?<=\s))#.*$", re.M)

# A CJK input method produces the full-width form of every character this syntax
# is built from, and the two are INDISTINGUISHABLE on screen: `31：1：52` renders
# exactly like `31:1:52` and was refused as "not a port range, nor a known port
# name". Normalise instead of refusing -- none of these is legal in a port name.
#
# Full-width DIGITS and the ideographic space are deliberately absent: `\d`,
# `\s` and `int()` are Unicode-aware in Python 3 and already accept them, so an
# entry here would be dead code -- and would also stop the tests noticing if
# this file's regexes were ever narrowed to `[0-9]`.
_FULLWIDTH_MAP = {
    ord("："): ":", ord("，"): ",", ord("、"): ",",
    ord("；"): ";", ord("－"): "-", ord("–"): "-",
    ord("—"): "-", ord("＃"): "#",
    ord("！"): "!", ord("＝"): "=",
    0xFEFF: "",                     # a BOM left mid-stream; written as a code
    0x00A0: " ",                    # point because both are invisible in source
}


def normalise_config_line(text):
    """Fold full-width punctuation and a stray BOM into their ASCII spellings."""
    return text.translate(_FULLWIDTH_MAP)


def split_config_tokens(text):
    """Split one config line (or one `--keep` / `--gnd` spec) into raw tokens."""
    text = normalise_config_line(text)
    text = text.split("!", 1)[0]
    text = _HASH_COMMENT_RE.sub("", text)
    text = _RANGE_SPACE_RE.sub(":", text)
    return [tok for tok in _TOKEN_SPLIT_RE.split(text.strip()) if tok]


def _dedup(ports):
    """Drop repeats, keep first-seen order. Ranges make overlaps easy to write."""
    seen = set()
    return [p for p in ports if not (p in seen or seen.add(p))]


def _fmt_ports(ports):
    """Collapse consecutive runs for display: [1,2,3,7] -> '1-3, 7'."""
    if not ports:
        return "(none)"
    runs, start, prev = [], ports[0], ports[0]
    for p in list(ports[1:]) + [None]:
        if p == prev + 1:
            prev = p
            continue
        runs.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = p
    return ", ".join(runs)


def expand_port_range(token):
    """
    Expand `start:step:stop` or `start-stop` into a list of 1-indexed ports.

    Returns None when `token` is not a numeric range -- the caller then treats
    it as a port name. Raises ValueError for a well-formed range that expands to
    nothing (`17:1:4`), which would otherwise drop ports with no symptom.
    """
    m = _RANGE_COLON_RE.match(token)
    if m:
        start, step, stop = (int(g) for g in m.groups())
        if step == 0:
            raise ValueError(f"step cannot be zero in range '{token}'")
        out = list(range(start, stop + 1, step) if step > 0
                   else range(start, stop - 1, step))
    else:
        m = _RANGE_DASH_RE.match(token)
        if not m:
            return None
        a, b = (int(g) for g in m.groups())
        out = list(range(a, b + 1) if a <= b else range(a, b - 1, -1))
    if not out:
        raise ValueError(f"range '{token}' expands to no ports "
                         f"(check the sign of the step)")
    return out


def describe_bad_token(tok):
    """
    Say WHY a token is not a port, when it was plainly meant to be one.

    "neither an integer, a port range, nor a known port name" is unactionable
    for exactly the failures that reach it: the character that broke a range is
    normally one the editor renders identically to the right one.
    """
    exotic = _dedup([ch for ch in tok if ord(ch) > 127])
    if exotic:
        shown = ", ".join(f"'{ch}' (U+{ord(ch):04X})" for ch in exotic)
        # A token with no digits was never a port number, so the likely mistake
        # is an unmarked comment; with digits in it, it is punctuation.
        fix = ("Start a comment with '#' or '!'." if not any(c.isdigit() for c in tok)
               else "Retype the punctuation on an ASCII keyboard.")
        return (f"token '{tok}' contains the non-ASCII character(s) {shown}. {fix}")

    parts = tok.split(":")
    if len(parts) > 1 and all(p.strip().lstrip("+-").isdigit() for p in parts):
        a, b = parts[0].strip(), parts[-1].strip()
        step = 1 if int(a) <= int(b) else -1
        return (f"token '{tok}' is not a range: a colon range is "
                f"start:step:stop. For ports {a} through {b} write "
                f"'{a}:{step}:{b}' or '{a}-{b}'.")

    return (f"token '{tok}' is neither an integer, a port range, nor a known "
            f"port name.")


def read_config_text(filepath):
    """
    Read a hand-written config file as text, sniffing the encoding.

    Notepad's "Unicode" is UTF-16 and its "UTF-8" writes a BOM. The old
    `encoding="utf-8", errors="ignore"` read the first as `' 3 1 : 1 : 5 2 '`
    and glued the second's BOM onto the leading `#`, so the group header was
    parsed as data -- and both files look perfect in an editor.
    """
    raw = Path(filepath).read_bytes()
    # UTF-32's BOM starts with UTF-16's, so it has to be tested first.
    for bom, enc in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
                     (codecs.BOM_UTF8, "utf-8-sig"),
                     (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if raw.startswith(bom):
            return raw.decode(enc)
    if b"\x00" in raw[:512]:                 # UTF-16 written without a BOM
        return raw.decode("utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be")
    for enc in ("utf-8", "gbk", "latin-1"):  # latin-1 cannot fail
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue


def parse_port_config(filepath):
    """
    Parse a port configuration file into ordered groups of raw tokens.

    Tokens stay unresolved here: a token may be a 1-indexed port number or a
    port name, and names can only be resolved once the Touchstone header has
    been read. See `resolve_port_config`.

    Returns:
        groups (OrderedDict): {group_name: [token, ...]} in file order.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        sys.exit(f"[ERROR] Port config file not found: {filepath}")

    groups = OrderedDict()
    current_group = "Ungrouped"
    groups[current_group] = []

    for line in read_config_text(filepath).splitlines():
        stripped = normalise_config_line(line).strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            current_group = stripped.lstrip("#").split("!", 1)[0].strip() or "Ungrouped"
            groups.setdefault(current_group, [])
            continue

        groups[current_group].extend(split_config_tokens(stripped))

    groups = OrderedDict((k, v) for k, v in groups.items() if v)
    if not groups:
        sys.exit(f"[ERROR] No ports found in {filepath}")
    return groups


def groups_from_cli(keep_specs, gnd_specs):
    """
    Build the same `{group: [token, ...]}` mapping from `--keep` / `--gnd`.

    A `--keep` spec may carry a group name (`RX=1,2,3`); without one it is named
    after its position, so repeating the flag gives you several KEEP groups the
    same way several `#` headers do in a config file.
    """
    groups = OrderedDict()
    for i, spec in enumerate(keep_specs or [], 1):
        name, sep, body = spec.partition("=")
        if not sep:
            name, body = f"Keep{i}", spec
        name = name.strip() or f"Keep{i}"
        if name.upper() in GND_GROUP_NAMES:
            sys.exit(f"[ERROR] --keep group '{name}' uses a reserved ground name; "
                     f"use --gnd for ports shorted to the reference node.")
        tokens = split_config_tokens(body)
        if not tokens:
            sys.exit(f"[ERROR] --keep {spec!r} lists no ports.")
        groups.setdefault(name, []).extend(tokens)

    for spec in gnd_specs or []:
        tokens = split_config_tokens(spec)
        if not tokens:
            sys.exit(f"[ERROR] --gnd {spec!r} lists no ports.")
        groups.setdefault("GND", []).extend(tokens)

    return groups


def resolve_port_config(groups, n_ports, port_names, order="sorted"):
    """
    Resolve config tokens to 1-indexed port numbers and split KEEP from GND.

    Returns:
        keep_groups (OrderedDict): {group_name: [port_1idx, ...]} -- KEEP only
        keep_1idx (list): output port order (see `order`)
        gnd_1idx (list): sorted, unique, ports to short to reference
    """
    # name -> 1-indexed port, for name lookups. Later duplicates mark ambiguity.
    by_name = {}
    for i, nm in enumerate(port_names):
        if nm:
            by_name.setdefault(nm.strip().upper(), []).append(i + 1)

    def resolve(tok, group):
        """Resolve one config token -> list of 1-indexed ports (a range gives many)."""
        def check(p, what):
            if not (1 <= p <= n_ports):
                sys.exit(f"[ERROR] Group '{group}': {what} out of range [1, {n_ports}]")
            return p

        try:
            p = int(tok)
        except ValueError:
            pass
        else:
            return [check(p, f"port {p}")]

        try:
            ports = expand_port_range(tok)
        except ValueError as exc:
            sys.exit(f"[ERROR] Group '{group}': {exc}")
        if ports is not None:
            shadowed = by_name.get(tok.strip().upper())
            if shadowed:
                sys.exit(f"[ERROR] Group '{group}': '{tok}' is both a port range and "
                         f"the name of port(s) {shadowed}. List the numbers "
                         f"explicitly to say which you mean.")
            return [check(p, f"range '{tok}' -> port {p}") for p in ports]

        hits = by_name.get(tok.strip().upper())
        if hits is None:
            # fall back to unique substring match
            key = tok.strip().upper()
            hits = sorted({p for nm, ps in by_name.items() if key in nm for p in ps})
        if not hits:
            sys.exit(f"[ERROR] Group '{group}': {describe_bad_token(tok)}")
        if len(hits) > 1:
            sys.exit(f"[ERROR] Group '{group}': port name '{tok}' is ambiguous, "
                     f"matches ports {hits}.")
        return [hits[0]]

    keep_groups = OrderedDict()
    keep_order = []          # config order, de-duplicated
    gnd = []
    for group, tokens in groups.items():
        resolved = _dedup([p for t in tokens for p in resolve(t, group)])
        if group.strip().upper() in GND_GROUP_NAMES:
            gnd.extend(resolved)
            continue
        keep_groups[group] = resolved
        for p in resolved:
            if p not in keep_order:
                keep_order.append(p)

    gnd_1idx = sorted(set(gnd))
    clash = sorted(set(keep_order) & set(gnd_1idx))
    if clash:
        sys.exit(f"[ERROR] Ports {clash} appear in both a KEEP group and the GND group.")

    keep_1idx = keep_order if order == "config" else sorted(keep_order)

    n_unused = n_ports - len(keep_1idx) - len(gnd_1idx)
    print(f"[INFO] Port config: {len(keep_groups)} keep-groups, {len(keep_1idx)} kept, "
          f"{len(gnd_1idx)} grounded, {n_unused} unused")
    for group, ports in keep_groups.items():
        print(f"       KEEP  {group}: {_fmt_ports(ports)}")
    if gnd_1idx:
        print(f"       GND   : {_fmt_ports(gnd_1idx)}")

    return keep_groups, keep_1idx, gnd_1idx


# ============================================================
# Touchstone Parser
# ============================================================
class Touchstone(object):
    """Container for a parsed Touchstone v1 file."""

    def __init__(self, n_ports, freq_unit, param_type, data_format, z0,
                 port_names, freqs, s):
        self.n_ports = n_ports
        self.freq_unit = freq_unit
        self.param_type = param_type
        self.data_format = data_format
        self.z0 = z0
        self.port_names = port_names
        self.freqs = freqs          # in `freq_unit`, unscaled -- written back verbatim
        self.s = s                  # complex, (n_freq, n_ports, n_ports)


def _sniff_nports(freq_col_source, total, warn):
    """Smallest N whose record size divides the token count with increasing freqs."""
    candidates = []
    for n in range(1, MAX_SNIFF_NPORTS + 1):
        rec = 1 + 2 * n * n
        if total % rec != 0:
            continue
        freqs = freq_col_source[0::rec]
        if freqs.size > 1 and not np.all(np.diff(freqs) > 0):
            continue
        candidates.append(n)
        if len(candidates) >= 3:
            break
    if not candidates:
        sys.exit(f"[ERROR] Could not infer port count from {total} data values. "
                 f"Pass --nports.")
    if len(candidates) > 1:
        warn(f"Port count ambiguous: candidates {candidates}. Using N={candidates[0]}.")
    return candidates[0]


def parse_touchstone(filepath, force_nports=None, flush_values=250000):
    """
    Parse a Touchstone v1 .sNp file.

    Values land in an `array.array('d')` (8 bytes each) via a small bounded
    staging list, and the result is a zero-copy numpy view of that buffer.
    Measured on a 59 MB / 3.6M-value file, the whole parse costs ~16% more wall
    time than appending every float to one big Python list, but peaks at 2.5x
    less memory (58 MB vs 146 MB): boxed floats cost ~32 B each and the final
    np.array() copy is built while that list is still alive. On a multi-GB
    package file that ratio is the difference between running and swapping.

    Note: np.fromstring(sep=' ') looks like the obvious fast path but is ~9x
    SLOWER on numpy 2.x (deprecated, unoptimized) and truncates silently on a
    bad token. Don't switch to it.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        sys.exit(f"[ERROR] File not found: {filepath}")

    n_ports_hint = force_nports
    if n_ports_hint is None:
        m = re.match(r"\.s(\d+)p$", filepath.suffix, re.I)
        if m:
            n_ports_hint = int(m.group(1))

    size = filepath.stat().st_size
    print(f"[INFO] Parsing {filepath.name} "
          f"({size / 1e6:.1f} MB, extension suggests {n_ports_hint or 'unknown'} ports)...")

    freq_unit, param_type, data_format, z0 = "GHZ", "S", "MA", 50.0
    seen_option_line = False
    port_names_map = {}
    warnings_out = []
    v2_keywords = []

    def warn(msg):
        warnings_out.append(msg)
        print(f"[WARN] {msg}")

    store = array.array("d")
    staging = []
    push = staging.append
    line_count = 0
    t0 = time.time()

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line_count += 1
            c = line[:1]

            if c == "!":
                for rx in _PORT_NAME_RES:
                    m = rx.match(line.strip())
                    if m:
                        port_names_map[int(m.group(1))] = m.group(2).strip()
                        break
                continue

            if c == "#":
                if not seen_option_line:
                    seen_option_line = True
                    tokens = line[1:].split("!", 1)[0].upper().split()
                    for i, tok in enumerate(tokens):
                        if tok in FREQ_UNITS:
                            freq_unit = tok
                        elif tok in ("S", "Y", "Z", "G", "H"):
                            param_type = tok
                        elif tok in ("DB", "MA", "RI"):
                            data_format = tok
                        elif tok == "R" and i + 1 < len(tokens):
                            try:
                                z0 = float(tokens[i + 1])
                            except ValueError:
                                pass
                continue

            if c == "[":
                v2_keywords.append(line.strip())
                kw = line.strip().upper()
                if kw.startswith("[NUMBER OF PORTS]"):
                    try:
                        n_ports_hint = int(kw.split("]", 1)[1].strip())
                    except (ValueError, IndexError):
                        pass
                continue

            stripped = line.strip()
            if not stripped:
                continue
            if "!" in stripped:                     # mid-line comment
                stripped = stripped.split("!", 1)[0].strip()
                if not stripped:
                    continue

            try:
                for tok in stripped.split():
                    push(float(tok))
            except ValueError:
                raise ValueError(
                    f"{filepath.name} line {line_count}: unparseable numeric token "
                    f"in {stripped[:80]!r}")

            if len(staging) >= flush_values:
                store.fromlist(staging)
                del staging[:]
                print(f"  ... read {line_count:,} lines, {len(store):,} values "
                      f"({time.time() - t0:.1f}s)")
    if staging:
        store.fromlist(staging)
        del staging[:]

    if v2_keywords:
        warn("Touchstone v2 keywords present (" + ", ".join(v2_keywords[:3]) +
             "). Only the v1 full-matrix layout is supported; verify the result.")
    if not seen_option_line:
        warn("No option line ('#') found; assuming '# GHZ S MA R 50'.")
    if param_type != "S":
        sys.exit(f"[ERROR] Option line declares '{param_type}'-parameters, not S. "
                 f"This tool only reduces S-parameter files.")

    # Zero-copy view over the array('d') buffer -- numpy keeps `store` alive.
    values = np.frombuffer(store, dtype=np.float64)
    if values.size == 0:
        sys.exit("[ERROR] No numeric data found in file.")

    print(f"[INFO] Read {line_count:,} lines, {values.size:,} values in "
          f"{time.time() - t0:.1f}s")
    print(f"[INFO] Format: {freq_unit} {param_type} {data_format} R {z0}")

    n_ports = n_ports_hint
    if n_ports is None or values.size % (1 + 2 * n_ports * n_ports) != 0:
        if n_ports is not None:
            warn(f"{values.size} values is not a whole number of records for "
                 f"N={n_ports}; re-inferring port count from the data.")
        n_ports = _sniff_nports(values, values.size, warn)
        print(f"[INFO] Inferred port count: {n_ports}")

    values_per_freq = 1 + 2 * n_ports * n_ports
    n_freq = values.size // values_per_freq
    blocks = values[:n_freq * values_per_freq].reshape(n_freq, values_per_freq)
    freqs = blocks[:, 0].copy()
    raw = blocks[:, 1:].reshape(n_freq, n_ports, n_ports, 2)

    # Fill s.real / s.imag in place. `raw[..., 0] + 1j * raw[..., 1]` would be
    # shorter but allocates two full-size complex temporaries, which on a
    # multi-GB file doubles peak memory for no reason.
    s = np.empty(raw.shape[:-1], dtype=complex)
    if data_format == "RI":
        s.real = raw[..., 0]
        s.imag = raw[..., 1]
    elif data_format in ("MA", "DB"):
        ang = np.deg2rad(raw[..., 1])
        np.cos(ang, out=s.real)
        np.sin(ang, out=s.imag)
        del ang
        if data_format == "MA":
            mag = raw[..., 0]
        else:
            mag = 10.0 ** (np.clip(raw[..., 0], -400.0, 400.0) / 20.0)
        s.real *= mag
        s.imag *= mag
    else:
        sys.exit(f"[ERROR] Unknown data format: {data_format}")

    # Touchstone v1 quirk: 2-port files are written S11 S21 S12 S22; N>=3 is
    # row-major. Only N==2 needs the transpose (mirrored in write_touchstone).
    if n_ports == 2:
        s = s.transpose(0, 2, 1)

    port_names = [port_names_map.get(i + 1, "") for i in range(n_ports)]
    print(f"[INFO] Parsed {n_freq} frequency points, "
          f"{freqs[0]:.6g} - {freqs[-1]:.6g} {freq_unit}")

    return Touchstone(n_ports, freq_unit, param_type, data_format, z0,
                      port_names, freqs, s)


# ============================================================
# S <-> Y Conversion (batched over frequency, scalar Z0)
# ============================================================
def _solve_batch(A, B, what=""):
    """Batched A \\ B with an lstsq fallback on singular/pathological blocks."""
    try:
        return np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        pass
    out = np.empty_like(B)
    bad = []
    for i in range(A.shape[0]):
        try:
            out[i] = np.linalg.solve(A[i], B[i])
        except np.linalg.LinAlgError:
            out[i] = np.linalg.lstsq(A[i], B[i], rcond=None)[0]
            bad.append(i)
    if bad:
        print(f"[WARN] {what}: singular matrix at {len(bad)} frequency "
              f"index(es) (first: {bad[0]}); used least-squares there.")
    return out


def s_to_y(S, z0=50.0):
    """S -> Y for a stack of matrices. Y = (1/z0)(I-S)(I+S)^-1, solved not inverted."""
    I = np.eye(S.shape[-1], dtype=complex)
    A = ((I - S) / z0).swapaxes(-1, -2)
    B = (I + S).swapaxes(-1, -2)
    return _solve_batch(B, A, "s_to_y").swapaxes(-1, -2)


def y_to_s(Y, z0=50.0):
    """Y -> S for a stack of matrices. S = (I-z0*Y)(I+z0*Y)^-1, solved not inverted."""
    I = np.eye(Y.shape[-1], dtype=complex)
    A = (I - z0 * Y).swapaxes(-1, -2)
    B = (I + z0 * Y).swapaxes(-1, -2)
    return _solve_batch(B, A, "y_to_s").swapaxes(-1, -2)


# ============================================================
# Port Reduction
# ============================================================
def reduce_block(S, z0, keep_0idx, gnd_0idx, method):
    """
    Reduce a stack of S-matrices, shape (F, N, N) -> (F, K, K).

    GND ports are shorted to the reference node (delete row+column in Y).
    Remaining unused ports are Schur-eliminated, either floating (`open`) or
    with a Y0 = 1/z0 shunt to ground first (`matched`).
    """
    N = S.shape[-1]
    keep = list(keep_0idx)
    gnd = set(gnd_0idx)

    # Fast path: plain sub-matrix extraction is exactly `matched` with no GND.
    if method == "matched" and not gnd:
        return S[:, keep][:, :, keep]

    Y = s_to_y(S, z0)

    if gnd:
        alive = [i for i in range(N) if i not in gnd]
        Y = Y[:, alive][:, :, alive]
        remap = {orig: new for new, orig in enumerate(alive)}
        keep = [remap[p] for p in keep]
        N = len(alive)

    keep_set = set(keep)
    unused = [i for i in range(N) if i not in keep_set]

    Y_kk = Y[:, keep][:, :, keep]
    if unused:
        Y_uu = Y[:, unused][:, :, unused]
        if method == "matched":
            Y_uu = Y_uu + np.eye(len(unused), dtype=complex) / z0
        Y_ku = Y[:, keep][:, :, unused]
        Y_uk = Y[:, unused][:, :, keep]
        Y_red = Y_kk - Y_ku @ _solve_batch(Y_uu, Y_uk, "Schur complement")
    else:
        Y_red = Y_kk

    return y_to_s(Y_red, z0)


def reduce_all(S, z0, keep_0idx, gnd_0idx, method, batch=256):
    """Run `reduce_block` over all frequencies in slices, to bound peak memory."""
    n_freq = S.shape[0]
    n_keep = len(keep_0idx)
    out = np.empty((n_freq, n_keep, n_keep), dtype=complex)
    t0 = time.time()
    for start in range(0, n_freq, batch):
        stop = min(start + batch, n_freq)
        out[start:stop] = reduce_block(S[start:stop], z0, keep_0idx, gnd_0idx, method)
        print(f"  ... reduced {stop}/{n_freq} frequencies ({time.time() - t0:.1f}s)")
    return out


# ============================================================
# Touchstone Writer
# ============================================================
_FMT_CACHE = {}


def _line(vals, prec):
    key = (len(vals), prec)
    fmt = _FMT_CACHE.get(key)
    if fmt is None:
        fmt = "\t".join(["%." + str(prec) + "g"] * len(vals))
        _FMT_CACHE[key] = fmt
    return fmt % tuple(vals)


def write_touchstone(filepath, freq_unit, z0, port_names, freqs, S,
                     data_format="RI", precision=12):
    """
    Write a Touchstone v1 .sNp file: one matrix row per line group,
    4 value-pairs per line, frequency leading the first line of each block.
    """
    filepath = Path(filepath)
    n = S.shape[-1]
    print(f"[INFO] Writing {filepath.name} ({n} ports, {len(freqs)} freq points, "
          f"{data_format} format)...")
    t0 = time.time()

    if data_format == "RI":
        v1_all, v2_all = S.real, S.imag
    elif data_format == "MA":
        v1_all, v2_all = np.abs(S), np.rad2deg(np.angle(S))
    elif data_format == "DB":
        v1_all = 20.0 * np.log10(np.maximum(np.abs(S), 1e-20))
        v2_all = np.rad2deg(np.angle(S))
    else:
        sys.exit(f"[ERROR] Unknown output format: {data_format}")

    # Same v1 quirk as the parser: a 2-port file is written S11 S21 S12 S22.
    if n == 2:
        v1_all = v1_all.transpose(0, 2, 1)
        v2_all = v2_all.transpose(0, 2, 1)

    # Interleave (v1, v2) per element -> flat row-major stream of 2*n*n values.
    inter = np.empty((len(freqs), n, 2 * n), dtype=np.float64)
    inter[:, :, 0::2] = v1_all
    inter[:, :, 1::2] = v2_all

    with open(filepath, "w") as f:
        f.write("! Reduced Touchstone file\n")
        f.write(f"! Ports: {n}, Frequencies: {len(freqs)}\n")
        f.write("! Generated by reduce_snp.py (port reduction tool)\n")
        f.write("!\n")
        for i, name in enumerate(port_names):
            f.write(f"! Port[{i + 1}] = {name}\n")
        f.write("!\n")
        f.write(f"# {freq_unit} S {data_format} R {z0:.6f}\n")

        freq_list = freqs.tolist()
        for fi in range(len(freq_list)):
            rows = inter[fi].tolist()
            lines = []
            for r, row in enumerate(rows):
                # first line of the block carries the frequency
                prefix = [freq_list[fi]] if r == 0 else []
                for c in range(0, len(row), 8):
                    lines.append(_line(prefix + row[c:c + 8], precision))
                    prefix = []
            f.write("\n".join(lines))
            f.write("\n")

    size_mb = filepath.stat().st_size / 1e6
    print(f"[INFO] Written {filepath.name} ({size_mb:.1f} MB) in {time.time() - t0:.1f}s")


# ============================================================
# Port Mapping Report
# ============================================================
def build_mapping_report(keep_1idx, keep_groups, gnd_1idx, port_names_orig,
                         n_ports_orig, method, order):
    n_keep = len(keep_1idx)
    port_to_group = {}
    for group, ports in keep_groups.items():
        for p in ports:
            port_to_group.setdefault(p, group)

    lines = ["=" * 78,
             f"PORT MAPPING: original .s{n_ports_orig}p  ->  reduced .s{n_keep}p",
             f"method={method}   output order={order}   "
             f"grounded={len(gnd_1idx)}   "
             f"open/unused={n_ports_orig - n_keep - len(gnd_1idx)}",
             "=" * 78,
             f"{'New Port':<10}{'Old Port':<10}{'Group':<18}Name",
             "-" * 78]
    for new_idx, old in enumerate(keep_1idx, start=1):
        name = port_names_orig[old - 1] if old - 1 < len(port_names_orig) else ""
        lines.append(f"{new_idx:<10}{old:<10}{port_to_group.get(old, '?'):<18}{name}")
    if gnd_1idx:
        lines.append("-" * 78)
        lines.append("SHORTED TO REFERENCE GROUND (removed, not a port):")
        for old in gnd_1idx:
            name = port_names_orig[old - 1] if old - 1 < len(port_names_orig) else ""
            lines.append(f"{'--':<10}{old:<10}{'GND':<18}{name}")
    lines.append("=" * 78)
    return "\n".join(lines)


# ============================================================
# Passivity Check (optional diagnostic)
# ============================================================
def check_passivity(S, label="", batch=256):
    """Passive iff every singular value of S is <= 1 at every frequency."""
    n_freq = S.shape[0]
    max_sv = 0.0
    violations = 0
    worst_i = -1
    for start in range(0, n_freq, batch):
        sv = np.linalg.svd(S[start:start + batch], compute_uv=False)
        block_max = sv.max(axis=1)
        violations += int(np.count_nonzero(block_max > 1.0 + 1e-6))
        i = int(np.argmax(block_max))
        if block_max[i] > max_sv:
            max_sv = float(block_max[i])
            worst_i = start + i
    if violations:
        print(f"[WARN] {label} passivity violated at {violations}/{n_freq} points "
              f"(max singular value {max_sv:.6f} at freq index {worst_i})")
    else:
        print(f"[INFO] {label} passivity OK (max singular value {max_sv:.6f})")


# ============================================================
# Main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description="Reduce a Touchstone .sNp file to fewer ports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Port config: '# GND' group is shorted to reference; other groups\n"
               "are kept as ports; unlisted ports are eliminated per --method.\n"
               "A port entry is a number, a port name, or a range -- '4:1:17'\n"
               "(start:step:stop) or '6-14', both inclusive.")
    p.add_argument("input", help="Input Touchstone file (e.g. xxx.s153p)")
    p.add_argument("--ports", default=None,
                   help="Port config file defining which ports to keep "
                        "(or give the ports inline with --keep / --gnd)")
    p.add_argument("--keep", action="append", metavar="[NAME=]SPEC",
                   help="Ports to keep, inline: '1,2,3,4:1:17,80'. Repeatable; "
                        "each occurrence is one group, optionally named 'RX=1,2'")
    p.add_argument("--gnd", action="append", metavar="SPEC",
                   help="Ports shorted to the reference node, inline. Repeatable")
    p.add_argument("-o", "--output", default=None,
                   help="Output Touchstone file (default: auto-named)")
    p.add_argument("--method", choices=["open", "matched"], default="open",
                   help="Unused-port treatment: 'open' = floating pin, matches "
                        "Spectre (default); 'matched' = Z0-terminated")
    p.add_argument("--order", choices=["sorted", "config"], default="sorted",
                   help="Output port order: ascending original index (default) "
                        "or the order groups appear in the config file")
    p.add_argument("--format", dest="out_format", default="ri",
                   choices=["ri", "ma", "db", "same"],
                   help="Output data format. 'ri' (default) is lossless-ish; "
                        "'same' reuses the input format")
    p.add_argument("--precision", type=int, default=12,
                   help="Significant digits per value in the output (default 12)")
    p.add_argument("--nports", type=int, default=None,
                   help="Override the port count (bypasses extension/sniffing)")
    p.add_argument("--batch", type=int, default=256,
                   help="Frequency points processed per batch (default 256)")
    p.add_argument("--check-passivity", action="store_true",
                   help="Report max singular value before and after reduction")
    p.add_argument("--mapping", default=None,
                   help="Where to save the port mapping (default: auto-named)")
    args = p.parse_args()

    if not args.ports and not args.keep and not args.gnd:
        p.error("one of --ports (config file) or --keep / --gnd (inline) is required")
    groups = parse_port_config(args.ports) if args.ports else OrderedDict()
    for name, tokens in groups_from_cli(args.keep, args.gnd).items():
        groups.setdefault(name, []).extend(tokens)
    if not groups:
        sys.exit("[ERROR] No ports given.")

    try:
        ts = parse_touchstone(args.input, force_nports=args.nports)
    except ValueError as exc:
        sys.exit(f"[ERROR] {exc}")

    keep_groups, keep_1idx, gnd_1idx = resolve_port_config(
        groups, ts.n_ports, ts.port_names, order=args.order)

    n_keep = len(keep_1idx)
    if n_keep == 0:
        sys.exit("[ERROR] No ports to keep (config had only a GND group?).")
    if n_keep >= ts.n_ports:
        sys.exit(f"[ERROR] Keeping {n_keep} of {ts.n_ports} ports -- nothing to reduce.")

    input_path = Path(args.input)
    output = args.output or f"reduced_{input_path.stem}.s{n_keep}p"
    mapping = args.mapping or str(Path(output).with_suffix(".port_mapping.txt"))
    out_format = ts.data_format if args.out_format == "same" else args.out_format.upper()

    if args.check_passivity:
        check_passivity(ts.s, label="[Original]", batch=args.batch)

    print(f"\n[INFO] Reducing {ts.n_ports} -> {n_keep} ports "
          f"(method={args.method}, {len(gnd_1idx)} grounded)")
    keep_0idx = [p - 1 for p in keep_1idx]
    gnd_0idx = [p - 1 for p in gnd_1idx]
    t0 = time.time()
    S_red = reduce_all(ts.s, ts.z0, keep_0idx, gnd_0idx, args.method, batch=args.batch)
    print(f"[INFO] Reduction complete in {time.time() - t0:.1f}s")

    if args.check_passivity:
        check_passivity(S_red, label="[Reduced]", batch=args.batch)

    port_names_new = [ts.port_names[p - 1] or f"Port_{p}" for p in keep_1idx]
    write_touchstone(output, ts.freq_unit, ts.z0, port_names_new, ts.freqs, S_red,
                     data_format=out_format, precision=args.precision)

    report = build_mapping_report(keep_1idx, keep_groups, gnd_1idx, ts.port_names,
                                  ts.n_ports, args.method, args.order)
    print()
    print(report)
    with open(mapping, "w") as f:
        f.write(report + "\n")

    print(f"\n[DONE] Output : {output}")
    print(f"[DONE] Mapping: {mapping}")
    print("\n[TIP] In Cadence: swap the nport model file, then re-connect pins per")
    print(f"      the mapping above. New port numbering is 1-{n_keep}.")


if __name__ == "__main__":
    main()
