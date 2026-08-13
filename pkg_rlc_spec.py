"""
pkg_rlc_spec.py  --  What the user DECLARED: terminations, rows, roles.

Split out of `pkg_rlc_core.py` verbatim: the unified `TerminationSet` model
and the `PortTermination` / `Coupling` unions it is built from, the port-range
and measurement-port parsers, the four named-mode builders, the Mode 5 DSL
that turns text into a `TerminationSet`, the named merged nodes ("nets"), the
connection-table row model the GUI editor writes, and the checks that read a
finished spec back and say what it will actually DO (`port_roles`,
`inert_lumped_messages`, `parallel_stamp_messages`,
`open_port_name_messages`).

Nothing here computes a number from a network.  This module says what was
asked for; `pkg_rlc_solve` answers it.  The one symbol that looks like it
belongs over there and does not is `_validate_port_indices`: it is called by
`build_terminations_coupling` and `build_terminations_rows` as well as by
`compute_z_matrix`, and a file's port count is part of the declaration rather
than part of the arithmetic -- see the note above it at the foot of this file.

User-facing port indices are 1-based; internal computation is 0-based.  The
boundary between the two is the `build_terminations_*` helpers here (input is
1-based) and the GUI/CLI layer.

`pkg_rlc_core` re-exports every name defined here, so
`from pkg_rlc_core import parse_custom_termination_text` -- and
`from pkg_rlc_core import _collect_nets`, which `pkg_rlc_gui` really does
write -- keep resolving unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence, Union

import numpy as np

from pkg_rlc_touchstone import format_si

# ============================================================================
# Constants
# ============================================================================

# Group names reserved for the legacy A / B modes; see Signal.
LEGACY_GROUP_NAMES = ("A", "B")

# SI multiplier suffixes for lumped values.  NOTE: 'M' is Mega (1e6) and 'm' is
# milli (1e-3) -- do not merge these two entries.
SI_SUFFIXES = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}


# ============================================================================
# Port range / short-pair parsers
# ============================================================================

def parse_port_range(spec: str) -> list[int]:
    """
    Parse port range syntax -> 1-based port list (deduped, order-preserved).

    Supported forms:
        "1"             -> [1]
        "1,3,5"         -> [1, 3, 5]
        "35:1:45"       -> [35, 36, ..., 45]   (MATLAB start:step:stop, inclusive)
        "6-14"          -> [6, 7, ..., 14]
        "1,3,35:1:45"   -> mixed
        ""              -> []
    """
    if not spec or not spec.strip():
        return []
    out: list[int] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if ":" in token:
            parts = [p.strip() for p in token.split(":")]
            if len(parts) != 3:
                raise ValueError(f"Range must be start:step:stop, got '{token}'")
            start, step, stop = (int(p) for p in parts)
            if step == 0:
                raise ValueError(f"Step cannot be zero in '{token}'")
            if step > 0:
                out.extend(range(start, stop + 1, step))
            else:
                out.extend(range(start, stop - 1, step))
        elif "-" in token[1:]:  # leading '-' is a sign, not a range
            a_str, b_str = token.split("-", 1)
            a, b = int(a_str.strip()), int(b_str.strip())
            if a <= b:
                out.extend(range(a, b + 1))
            else:
                out.extend(range(a, b - 1, -1))
        else:
            out.append(int(token))
    seen: set[int] = set()
    result: list[int] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def parse_short_pairs(spec: str) -> list[tuple[int, int]]:
    """
    Parse short-pair / short-group syntax (1-based) -> list of binary (i, j) pairs.

    Each comma-separated group joins any number of ports with dashes; all
    ports in a group are tied together (V_i = V_j = ...). The function
    emits a chain of binary pairs because the core API uses ShortPair(i, j)
    plus Union-Find -- chaining (1,2)+(2,3)+(3,4) puts all four into one
    merged group.

        "45-46"             -> [(45, 46)]
        "45-46, 47-48"      -> [(45, 46), (47, 48)]
        "1-2-3-4"           -> [(1, 2), (2, 3), (3, 4)]   (all four shorted)
        "1-2-3, 4-5"        -> [(1, 2), (2, 3), (4, 5)]
    """
    if not spec or not spec.strip():
        return []
    pairs: list[tuple[int, int]] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" not in token:
            raise ValueError(
                f"Short group must use 'a-b' or 'a-b-c-...' syntax, got '{token}'"
            )
        parts = [p.strip() for p in token.split("-")]
        try:
            ports = [int(p) for p in parts]
        except ValueError as e:
            raise ValueError(f"Invalid short group '{token}': {e}") from e
        if len(ports) < 2:
            raise ValueError(f"Short group '{token}' needs at least two ports")
        for a, b in zip(ports, ports[1:]):
            if a == b:
                raise ValueError(
                    f"Short group '{token}' has adjacent duplicate port {a}"
                )
            pairs.append((a, b))
    return pairs


def parse_mport_spec(spec: str) -> tuple[str, list[int], list[int]]:
    """
    Parse one measurement-port spec -> (name, plus_1based, minus_1based).

    A measurement port is a pair of probes: the RED probe touches every port on
    the plus side, the BLACK probe every port on the minus side.  '/' separates
    the two sides and an optional 'name =' prefix names the port.  Both sides go
    through parse_port_range, so ranges keep working.

        "tank = 1,3 / 2,4"  -> ("tank", [1, 3], [2, 4])
        "1 / 2"             -> ("",     [1],    [2])
        "rx = 5:1:9 /"      -> ("rx",   [5, 6, 7, 8, 9], [])   (ground-referenced)
        "3,4"               -> ("",     [3, 4], [])            (ground-referenced)

    An empty minus side means the port is referenced to ground.

    Raises ValueError on: an empty plus side, a port listed on both sides,
    more than one '/', or the reserved names "A" / "B".
    """
    if spec is None:
        raise ValueError("Empty measurement-port spec")
    text = spec.strip()
    if not text:
        raise ValueError("Empty measurement-port spec")

    name = ""
    if "=" in text:
        name_part, text = text.split("=", 1)
        name = name_part.strip()
        if not name:
            raise ValueError(
                f"Measurement port '{spec.strip()}' has an empty name before '='"
            )
        if name.upper() in LEGACY_GROUP_NAMES:
            raise ValueError(
                f"Measurement-port name '{name}' is reserved for the legacy "
                "A/B modes; pick another name."
            )

    parts = text.split("/")
    if len(parts) > 2:
        raise ValueError(
            f"Measurement port '{spec.strip()}' has more than one '/'; the "
            "syntax is '<plus ports> / <minus ports>'"
        )
    plus = parse_port_range(parts[0])
    minus = parse_port_range(parts[1]) if len(parts) == 2 else []
    if not plus:
        raise ValueError(
            f"Measurement port '{spec.strip()}' has an empty '+' side; at "
            "least one port must carry the red probe."
        )
    both = sorted(set(plus) & set(minus))
    if both:
        raise ValueError(
            f"Measurement port '{spec.strip()}': port(s) "
            f"{', '.join(str(p) for p in both)} appear on both the '+' and "
            "'-' side."
        )
    return name, plus, minus


def parse_si(s: str) -> float:
    """Parse '50', '1e-9', '1n', '0.5p', etc. -> float. Empty/0 raises ValueError."""
    s = s.strip()
    if not s:
        raise ValueError("Empty value")
    if s[-1] in SI_SUFFIXES:
        return float(s[:-1]) * SI_SUFFIXES[s[-1]]
    return float(s)


def parse_kv_rlc_params(tokens: list[str]) -> dict:
    """Parse 'R=50 L=1n C=1p' tokens into kwargs for y_series_rlc."""
    out = {"R": 0.0, "L": 0.0, "C": math.inf}
    for t in tokens:
        if "=" not in t:
            continue
        k, v = t.split("=", 1)
        k = k.strip().upper()
        if k not in out:
            raise ValueError(f"Unknown lumped param '{k}' (expected R, L, or C)")
        out[k] = parse_si(v)
    return out


# ============================================================================
# Lumped-element admittance helpers
# ============================================================================

YFunc = Callable[[np.ndarray], np.ndarray]
"""Function omega (rad/s, real ndarray) -> complex admittance ndarray."""


def y_resistor(R: float) -> YFunc:
    if R <= 0:
        raise ValueError(f"Resistor R must be > 0, got {R}")
    return lambda omega: np.full(np.asarray(omega).shape, 1.0 / R, dtype=complex)


def y_inductor(L: float) -> YFunc:
    if L <= 0:
        raise ValueError(f"Inductor L must be > 0, got {L}")
    return lambda omega: 1.0 / (1j * np.asarray(omega) * L)


def y_capacitor(C: float) -> YFunc:
    if C <= 0:
        raise ValueError(f"Capacitor C must be > 0, got {C}")
    return lambda omega: 1j * np.asarray(omega) * C


def y_series_rlc(R: float = 0.0, L: float = 0.0, C: float = math.inf) -> YFunc:
    """
    Z(jw) = R + jwL + 1/(jwC). Y = 1/Z.
    Use L=0 to skip inductor, C=inf to skip capacitor.
    """
    def f(omega: np.ndarray) -> np.ndarray:
        omega = np.asarray(omega, dtype=float)
        Z = np.full(omega.shape, R, dtype=complex)
        if L != 0:
            Z = Z + 1j * omega * L
        if math.isfinite(C) and C != 0:
            Z = Z + 1.0 / (1j * omega * C)
        return 1.0 / Z
    return f


# ============================================================================
# Unified port-termination model
# ============================================================================

@dataclass(frozen=True)
class Open:
    pass


@dataclass(frozen=True)
class Ground:
    pass


@dataclass(frozen=True)
class Vdd:
    """AC-grounded ideal supply; identical to Ground for AC small-signal."""
    pass


@dataclass(frozen=True)
class Signal:
    """
    A port carrying a measurement probe.

    `group` names the measurement port; it is an arbitrary string.  `sign` says
    which probe touches this port: +1 is the plus / red side, -1 is the minus /
    black side.  Ports sharing a (group, sign) are tied together in parallel;
    there are no fractional weights.

    LEGACY ALIAS: group "B" is the historical name for "the minus side of group
    A".  `resolve_meas_ports` normalises Signal("B", +1) to Signal("A", -1) (and
    Signal("B", -1) to Signal("A", +1)), which is what keeps every pre-existing
    mode, test and saved session working unchanged.  "A" and "B" are therefore
    reserved names -- `parse_mport_spec` rejects them for new measurement ports.
    """
    group: str = "A"
    sign: int = +1     # +1 = plus/red side, -1 = minus/black side


# `params` on the two lumped classes is the R/L/C the element was WRITTEN with,
# exactly as parse_kv_rlc_params returned it ({"R": .., "L": .., "C": ..}).  It
# is metadata: nothing in the reduction reads it, y_func stays the only thing
# that is evaluated, and a set built in code (the golden capture, the
# attribution tests) leaves it None.
#
# It exists because y_func is an opaque closure.  Without it a checker can say
# "three identical elements in parallel" but not "50 Ohm becomes 16.7 Ohm", and
# the number is the whole point -- measured on the 5-port probe network in
# tests/test_conn_nets.py, a merged node turns `R=50` into 16.667 Ohm and
# `L=10f` into 3.333 fH with nothing on screen saying so.
@dataclass
class LumpedToGnd:
    y_func: YFunc
    params: dict | None = None


@dataclass
class ShortPair:
    port_i: int   # 0-based
    port_j: int   # 0-based


@dataclass
class LumpedBetween:
    port_i: int
    port_j: int
    y_func: YFunc
    params: dict | None = None


PortTermination = Union[Open, Ground, Vdd, Signal, LumpedToGnd]
Coupling = Union[ShortPair, LumpedBetween]


@dataclass
class TerminationSet:
    """Per-port terminations (default Open) + inter-port couplings."""
    per_port: dict[int, PortTermination] = field(default_factory=dict)
    couplings: list[Coupling] = field(default_factory=list)

    def termination_of(self, port: int) -> PortTermination:
        return self.per_port.get(port, Open())


# ============================================================================
# Measurement ports (probe pairs)
# ============================================================================

@dataclass
class MeasPort:
    """One pair of probes. Port indices are 0-based (core-side)."""
    name: str
    plus: list[int]
    minus: list[int]


def _normalize_signal(sig: Signal) -> tuple[str, int]:
    """
    Signal -> (group, sign) with the legacy "B" alias applied.

    Signal("B", +1) is the historical spelling of "minus side of group A", so it
    resolves to ("A", -1).  Everything else passes through with its sign
    coerced to +-1.
    """
    sign = -1 if sig.sign < 0 else +1
    if sig.group == "B":
        return "A", -sign
    return sig.group, sign


def resolve_meas_ports(terminations: TerminationSet, n: int) -> list[MeasPort]:
    """
    Collect the Signal-terminated ports of `terminations` into measurement ports.

    Ordering is by first appearance of the group name while scanning ports
    0..n-1 ascending, so the result is deterministic and independent of dict
    insertion order.  The legacy "B" group is folded into the minus side of "A"
    (see Signal), which is what makes the old A<->B modes a plain
    single-measurement-port case.

    Raises ValueError if a group ends up with an empty plus side (e.g. only
    legacy "B" ports were declared, or every port of a group carries the black
    probe).
    """
    order: list[str] = []
    plus: dict[str, list[int]] = {}
    minus: dict[str, list[int]] = {}
    for i in range(n):
        t = terminations.termination_of(i)
        if not isinstance(t, Signal):
            continue
        group, sign = _normalize_signal(t)
        if group not in plus:
            order.append(group)
            plus[group] = []
            minus[group] = []
        (plus if sign > 0 else minus)[group].append(i)

    out: list[MeasPort] = []
    for group in order:
        if not plus[group]:
            if group == "A":
                raise ValueError(
                    "No Signal-group-A ports defined; nothing to measure."
                )
            raise ValueError(
                f"Measurement port '{group}' has an empty '+' side; at least "
                "one port must carry the red probe."
            )
        out.append(MeasPort(name=group, plus=plus[group], minus=minus[group]))
    return out


# ----- Convenience builders for the four named modes (1-based input) -------

def build_terminations_mode1(signal_ports: Sequence[int],
                             gnd_ports: Sequence[int]) -> TerminationSet:
    pp: dict[int, PortTermination] = {}
    for p in signal_ports:
        pp[p - 1] = Signal("A")
    for p in gnd_ports:
        pp[p - 1] = Ground()
    return TerminationSet(per_port=pp)


def build_terminations_mode2(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int]) -> TerminationSet:
    pp: dict[int, PortTermination] = {}
    for p in port_a:
        pp[p - 1] = Signal("A")
    for p in port_b:
        pp[p - 1] = Signal("B")
    for p in gnd_ports:
        pp[p - 1] = Ground()
    return TerminationSet(per_port=pp)


def build_terminations_mode3(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int],
                             short_pairs: Sequence[tuple[int, int]]) -> TerminationSet:
    ts = build_terminations_mode2(port_a, port_b, gnd_ports)
    ts.couplings.extend(ShortPair(a - 1, b - 1) for a, b in short_pairs)
    return ts


def build_terminations_mode4(port_a: Sequence[int], port_b: Sequence[int],
                             gnd_ports: Sequence[int],
                             vdd_ports: Sequence[int]) -> TerminationSet:
    ts = build_terminations_mode2(port_a, port_b, gnd_ports)
    for p in vdd_ports:
        ts.per_port[p - 1] = Vdd()
    return ts


def build_terminations_coupling(
    mports: Sequence[tuple[str, Sequence[int], Sequence[int]]],
    gnd_ports: Sequence[int] = (),
    short_pairs: Sequence[tuple[int, int]] = (),
    nports: int | None = None,
) -> TerminationSet:
    """
    Build a TerminationSet for an arbitrary number of measurement ports.

    `mports` is a sequence of (name, plus_1based, minus_1based) triples -- the
    output shape of parse_mport_spec.  Unnamed entries get the default names
    P1, P2, ...  All port indices are 1-based on the way in and 0-based on the
    way out: this function is the GUI/CLI boundary.

    Pass `nports` (the file's port count) to have out-of-range port numbers
    rejected here, with a message that names the file size; compute_z_matrix
    rejects them again as a backstop.

    Unlike build_terminations_mode1/2 -- where ground simply wins over a
    probe -- listing a probe port in `gnd_ports` raises.  Under the probe model
    the ports on one side are tied together, so grounding one of them grounds
    the whole side; silently untying it instead would report a plausible
    non-zero impedance for a node the tool believes is at 0 V.
    """
    pp: dict[int, PortTermination] = {}
    owner: dict[int, str] = {}
    used_names: set[str] = set()
    auto = 0

    for entry in mports:
        name, plus_1b, minus_1b = entry
        name = (name or "").strip()
        if not name:
            while True:
                auto += 1
                name = f"P{auto}"
                if name not in used_names:
                    break
        if name.upper() in LEGACY_GROUP_NAMES:
            raise ValueError(
                f"Measurement-port name '{name}' is reserved for the legacy "
                "A/B modes; pick another name."
            )
        if name in used_names:
            raise ValueError(f"Duplicate measurement-port name '{name}'")
        used_names.add(name)

        plus = list(plus_1b)
        minus = list(minus_1b)
        if not plus:
            raise ValueError(
                f"Measurement port '{name}' has an empty '+' side; at least "
                "one port must carry the red probe."
            )
        both = sorted(set(plus) & set(minus))
        if both:
            raise ValueError(
                f"Measurement port '{name}': port(s) "
                f"{', '.join(str(p) for p in both)} appear on both the '+' "
                "and '-' side."
            )
        for p, sign in [(p, +1) for p in plus] + [(p, -1) for p in minus]:
            if p < 1:
                raise ValueError(f"Port numbers are 1-based, got {p}")
            if p in owner and owner[p] != name:
                raise ValueError(
                    f"Port {p} is claimed by both measurement port "
                    f"'{owner[p]}' and '{name}'"
                )
            owner[p] = name
            pp[p - 1] = Signal(name, sign)

    if not pp:
        raise ValueError("No measurement ports defined; nothing to measure.")

    clash = sorted(p for p in gnd_ports if p in owner)
    if clash:
        raise ValueError(
            f"Port(s) {', '.join(str(p) for p in clash)} are listed both as a "
            f"probe (measurement port "
            f"'{owner[clash[0]]}') and as ground. A probe side is tied "
            "together, so grounding one of its ports grounds the whole side "
            "-- drop it from one list or the other."
        )
    for p in gnd_ports:
        if p < 1:
            raise ValueError(f"Port numbers are 1-based, got {p}")
        pp[p - 1] = Ground()

    ts = TerminationSet(per_port=pp)
    ts.couplings.extend(ShortPair(a - 1, b - 1) for a, b in short_pairs)
    if nports is not None:
        _validate_port_indices(ts, int(nports))
    return ts


# ============================================================================
# Named merged nodes ("nets")
# ============================================================================
#
# A short row creates a NODE.  Until now that node had no name, so the only way
# to hang an element off it was to name one of its member ports -- which works
# (measured: after `1 short_to 2,3`, `1 lumped_between 4 L=10f` gives 10.000 fH)
# but reads as arbitrary, and the natural spelling `1,2,3 lumped_between 4
# L=10f` silently computes THREE elements in parallel (measured: 3.333 fH,
# ratio exactly 3.000).  Naming the node is the affordance that makes the right
# gesture the obvious one; parallel_stamp_messages is the refusal that catches
# the wrong one.
#
# A net is pure SUGAR.  It resolves to ONE representative member port, so
# everything downstream -- the DSL, compute_z_matrix, the golden reference --
# sees exactly the spec that could always have been typed by hand.

# The keyword that names a node: `1,2,3 short as coil_tap`.
NET_KEYWORD = "as"

# Names a net may not take.  "A"/"B" are already reserved by Signal (group "B"
# is the legacy alias for the minus side of "A").  The rest are the DSL's own
# vocabulary: a net called `ground` would make the line `ground ground` read
# two ways, and the leading field is where a net name and a keyword meet.
NET_RESERVED_NAMES = (
    "A", "B",
    "GND", "GROUND", "VDD", "OPEN", "SIGNAL",
    "SHORT", "SHORT_TO", "LUMPED_TO_GND", "LUMPED_BETWEEN",
    NET_KEYWORD.upper(),
)

# Characters a net name may not contain, each because of a parser that would
# then read it as something else:
#   ':' and '-'  are parse_port_range's range separators (and ':' is the one
#                a future file prefix must not use -- parse_port_range('PKG:12')
#                already raises "Range must be start:step:stop");
#   ','          separates ports in the same field;
#   '#'          starts a comment;
#   whitespace   is the DSL's token separator, so 'my net' is two tokens and the
#                second one lands where a keyword belongs.
NET_BAD_CHARS = ":,-#"


def validate_net_name(name: str) -> None:
    """
    Raise ValueError unless `name` can name a merged node.

    The rules are not taste.  A name that parse_port_range accepts is refused
    because the port field is the one slot in this DSL where a number and a name
    share a token -- the same number-vs-name collision reduce_snp.py documents
    for its port groups, and guessing there is a silent wrong answer.  The
    reserved list and the character set close the other ways a name can be read
    as something else; see NET_RESERVED_NAMES / NET_BAD_CHARS.

    Names are matched case-insensitively and stored as typed.
    """
    n = (name or "").strip()
    if not n:
        raise ValueError("a node name cannot be empty")
    try:
        ports = parse_port_range(n)
    except ValueError:
        ports = []
    if ports:
        raise ValueError(
            f"node name '{n}' is a port number or range, so nothing could tell "
            f"it from the ports it names. Pick a name that is not a number."
        )
    bad = sorted({ch for ch in n if ch.isspace() or ch in NET_BAD_CHARS})
    if bad:
        shown = ", ".join("space" if ch.isspace() else f"'{ch}'" for ch in bad)
        raise ValueError(
            f"node name '{n}' contains {shown}. A node name may not contain "
            f"whitespace or any of {NET_BAD_CHARS} -- those are how this "
            f"syntax separates ports, ranges and comments."
        )
    if n.upper() in NET_RESERVED_NAMES:
        raise ValueError(
            f"node name '{n}' is one of this syntax's own keywords "
            f"({', '.join(NET_RESERVED_NAMES)}). Pick another name."
        )


@dataclass(frozen=True)
class NetDef:
    """
    One named merged node.

    `port` is a 1-BASED representative member -- any member port identifies the
    whole node once the shorts are merged, which is why a net can be pure sugar.
    It is 0 when the defining row has no port number of its own yet.
    """
    name: str          # as typed
    port: int = 0      # 1-based representative member, 0 = unresolved
    line: int = 0      # DSL line that defined it, 1-based (0 = unknown)


def _split_net_tail(parts: Sequence[str]) -> tuple[list[str], str, bool]:
    """
    Split a trailing `as <name>` off a directive's tokens.

    -> (tokens_before_as, name, well_formed).  Pure and TOTAL: a malformed tail
    comes back as well_formed=False so dsl_text_to_rows can park the line in
    `extra` while parse_custom_termination_text raises about it.
    """
    toks = list(parts)
    lowered = [t.lower() for t in toks]
    if NET_KEYWORD not in lowered:
        return toks, "", True
    i = lowered.index(NET_KEYWORD)
    tail = toks[i + 1:]
    if len(tail) != 1:
        return toks[:i], "", False
    return toks[:i], tail[0], True


def _net_key(spec: str) -> str:
    return (spec or "").strip().lower()


def _looks_like_a_name(spec: str) -> bool:
    """Could this token only ever have been meant as a name, not a port spec?"""
    return any(ch.isalpha() or ch == "_" for ch in spec or "")


def _defined_nets_phrase(nets: dict) -> str:
    if not nets:
        return ("No node names are defined -- a short row names its node with "
                f"'{NET_KEYWORD} <name>'.")
    names = ", ".join(sorted(d.name for d in nets.values()))
    return f"Defined node names: {names}."


def _first_port_of(spec: str, nets: dict, resolved: dict) -> int | None:
    """
    First literal port of `spec`, following net references.  None if not yet.

    Deliberately reads ONLY the row's own port field, never its partner: a name
    stands for the node its row builds, and falling back to the partner side
    lets a self-referential pair (`n1 short_to 3 as n2` / `n2 short_to 4 as n1`)
    quietly resolve BOTH names to port 3 and then fail with a message about
    chain length.  Left unresolved, the main pass names the actual problem.
    """
    s = (spec or "").strip()
    if not s:
        return None
    key = _net_key(s)
    if key in nets:
        return resolved.get(key)      # None while that net is itself unresolved
    try:
        ports = parse_port_range(s)
    except ValueError:
        return None
    return ports[0] if ports else None


def _collect_nets(text: str) -> dict[str, NetDef]:
    """
    Pre-pass: every `as <name>` in `text` -> {lowercase name: NetDef}.

    A SEPARATE PASS, so a net may be referenced above the row that defines it.
    That is not a nicety: rows_to_dsl_text emits every measurement port BEFORE
    every connection, so a probe on a named node would otherwise refuse to
    resolve purely because of where the table puts it.

    Resolution is a fixed point rather than a recursion so that a net defined in
    terms of another net works and a cycle simply stops (the names left
    unresolved are reported by the main pass, which has the line numbers).
    Never raises for a malformed line -- the main pass raises, with the same
    message it would have given anyway.
    """
    pending: dict[str, str] = {}
    defs: dict[str, NetDef] = {}
    for ln_no, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        parts = body.split()
        if len(parts) < 2 or parts[1].lower() not in ("short", "short_to"):
            continue
        _rest, name, ok = _split_net_tail(parts[2:])
        if not ok or not name:
            continue
        try:
            validate_net_name(name)
        except ValueError as e:
            raise ValueError(f"Line {ln_no}: {e}") from e
        key = _net_key(name)
        if key in defs:
            raise ValueError(
                f"Line {ln_no}: node name '{name}' is already used on line "
                f"{defs[key].line}. A name belongs to one node; use it to refer "
                f"to that node instead of defining it twice."
            )
        defs[key] = NetDef(name=name, line=ln_no)
        pending[key] = parts[0]

    resolved: dict[str, int] = {}
    for _ in range(len(pending) + 1):
        progress = False
        for key, ports_text in pending.items():
            if key in resolved:
                continue
            p = _first_port_of(ports_text, defs, resolved)
            if p is not None:
                resolved[key] = p
                progress = True
        if not progress:
            break
    return {k: NetDef(name=d.name, port=resolved.get(k, 0), line=d.line)
            for k, d in defs.items()}


def _resolve_port_field(spec: str, nets: dict, ln_no: int, what: str) -> list[int]:
    """
    One port field -> 1-based ports, resolving a node name to ONE member.

    A net stands for a node, and the node's members are already tied together,
    so ONE representative is the whole of it -- expanding to every member is
    exactly the N-parallel-elements bug this feature exists to remove.
    """
    s = (spec or "").strip()
    key = _net_key(s)
    if key in nets:
        d = nets[key]
        if not d.port:
            raise ValueError(
                f"Line {ln_no}: node '{d.name}' (named on line {d.line}) has no "
                f"port number of its own to stand for; give that row at least "
                f"one port."
            )
        return [d.port]
    try:
        return parse_port_range(s)
    except ValueError as e:
        if _looks_like_a_name(s):
            raise ValueError(
                f"Line {ln_no}: {what} '{s}' is not a port number or range, and "
                f"no node is named that. {_defined_nets_phrase(nets)}"
            ) from e
        raise ValueError(
            f"Line {ln_no}: {what} must be a port number, a range "
            f"(e.g. '3', '1,2', '5:1:12', '6-14') or a node name, got "
            f"'{s}': {e}"
        ) from e


def _check_one_name_per_node(ts: TerminationSet, nets: dict) -> None:
    """
    Two names on ONE merged node is a spec error, not a synonym.

    A name belongs to the node, so `1,2 short as a` next to `2,3 short as b`
    leaves ports 1-3 one node answering to two names -- and every message,
    every echo and every Ports & Roles row then has to pick one arbitrarily.
    Refusing is the only reading that cannot be silently wrong.
    """
    if len(nets) < 2:
        return
    find, _members = _merge_view(ts)
    by_root: dict[int, NetDef] = {}
    for d in nets.values():
        if not d.port:
            continue
        root = find(d.port - 1)
        first = by_root.get(root)
        if first is not None:
            a, b = sorted((first, d), key=lambda x: x.line)
            raise ValueError(
                f"Line {b.line}: node name '{b.name}' names the same merged "
                f"node as '{a.name}' (line {a.line}) -- a short ties them "
                f"together. One node, one name: put all of those ports on ONE "
                f"short row, or drop one of the two names."
            )
        by_root[root] = d


# ============================================================================
# Mode 5 (Custom) termination DSL
# ============================================================================

def parse_custom_termination_text(text: str) -> TerminationSet:
    """
    Parse a text spec for Mode 5 (Custom).  One directive per line, e.g.
        1 signal A
        2 signal B
        3 ground
        4 vdd
        5 lumped_to_gnd R=50
        6 short_to 7
        8 lumped_between 9 R=1 L=1n
        10 open
        6:1:14 ground             <- the port field takes a range
        23,24,25 short            <- tie a whole group into one node
        23,24,25 short as tap     <- ...and name that node
        tap lumped_between 30 L=10f   <- any port field may name a node

    `signal <groupname> [+|-]` declares a probe: <groupname> names the
    measurement port and the optional sign picks the plus (red, default) or
    minus (black) side.  Group names are arbitrary strings; "A" and "B" keep
    their legacy meaning, where "signal B" == "signal A -".

    PORT RANGES: the leading port field accepts the full `parse_port_range`
    syntax ('3', '1,2', '5:1:12', '6-14'), so one line can terminate a whole
    group of ports -- a package's ground balls, an inductor's shield taps.  The
    directive is applied to each listed port independently, and a single port
    number parses to a one-element list, so every pre-existing spec is
    unaffected.  This is what lets the GUI's connection table hold "ports 5-12,
    ground" as ONE row instead of eight identical ones.

    `short_to` also takes a range on its right-hand side: every port on both
    sides is tied into one node, emitted as chained binary pairs exactly the way
    `parse_short_pairs` spells '1-2-3-4' (Union-Find inside compute_z merges
    them).  `short` with NO partner is the same thing written as one field:
    '23,24,25 short' ties everything listed into one node, which is what a
    single-cell connection row serialises to.  `lumped_between` deliberately
    does NOT take a range on the right -- an N-to-M lumped element is ambiguous
    (star? mesh?), so its partner must be a single port.  A range on its LEFT is
    unambiguous (one element from each listed port to the one partner) and is
    allowed -- but see parallel_stamp_messages for the case where those ports
    are already ONE node, which is N elements in parallel and almost never what
    was meant.

    NODE NAMES: a short line may name the node it creates with
    '<ports> short as <name>' (or '<ports> short_to <ports> as <name>'), and any
    port field may then use that name.  A name resolves to ONE representative
    member port -- the node is merged, so one member IS the node -- which makes
    the whole feature sugar over a spec that could always have been typed by
    hand.  See validate_net_name for what a name may be; an unknown name is
    refused rather than treated as a new empty node, which would hang the
    element off a dangling node and change the answer with nothing on screen.

    Blank lines and lines starting with '#' are ignored.
    """
    ts = TerminationSet()
    nets = _collect_nets(text)
    for ln_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        ports = _resolve_port_field(parts[0], nets, ln_no, "first token")
        if not ports:
            raise ValueError(
                f"Line {ln_no}: port specification '{parts[0]}' selects no ports"
            )
        for port in ports:
            if port < 1:
                raise ValueError(f"Line {ln_no}: port must be >= 1, got {port}")
        if len(parts) < 2:
            raise ValueError(f"Line {ln_no}: missing termination kind")
        kind = parts[1].lower()
        rest = parts[2:]
        if kind in ("short", "short_to"):
            rest, net_name, ok = _split_net_tail(rest)
            if not ok:
                raise ValueError(
                    f"Line {ln_no}: '{NET_KEYWORD}' takes exactly one node "
                    f"name, and a node name cannot contain spaces."
                )
            if net_name:
                try:
                    validate_net_name(net_name)
                except ValueError as e:
                    raise ValueError(f"Line {ln_no}: {e}") from e
        elif kind != "signal" and any(t.lower() == NET_KEYWORD for t in rest):
            # `1,2,3 lumped_to_gnd R=50 as foo` would otherwise drop the name in
            # silence (ground/vdd/open ignore their tail, parse_kv_rlc_params
            # drops any token without an '='), and the user would go on
            # referring to a node that was never named.  `signal` is exempt: its
            # first token is a free-form group name, so 'as' there is a name.
            raise ValueError(
                f"Line {ln_no}: only a short row can name a node. Move the "
                f"'{NET_KEYWORD} ...' onto the short that creates it."
            )
        if kind in ("open",):
            for port in ports:
                ts.per_port[port - 1] = Open()
        elif kind in ("ground", "gnd"):
            for port in ports:
                ts.per_port[port - 1] = Ground()
        elif kind == "vdd":
            for port in ports:
                ts.per_port[port - 1] = Vdd()
        elif kind == "signal":
            grp = rest[0] if rest else "A"
            if grp.upper() in LEGACY_GROUP_NAMES:
                grp = grp.upper()   # legacy A / B are case-insensitive
            sign = +1
            if len(rest) >= 2:
                if rest[1] == "+":
                    sign = +1
                elif rest[1] == "-":
                    sign = -1
                else:
                    raise ValueError(
                        f"Line {ln_no}: signal sign must be '+' or '-', "
                        f"got '{rest[1]}'"
                    )
            if len(rest) > 2:
                raise ValueError(
                    f"Line {ln_no}: signal takes at most a group name and a sign"
                )
            for port in ports:
                ts.per_port[port - 1] = Signal(grp, sign)
        elif kind in ("short", "short_to"):
            others: list[int] = []
            if kind == "short_to":
                if not rest:
                    raise ValueError(
                        f"Line {ln_no}: short_to needs a partner port")
                others = _resolve_port_field(rest[0], nets, ln_no,
                                             "short_to partner")
                if not others:
                    raise ValueError(
                        f"Line {ln_no}: short_to partner '{rest[0]}' selects "
                        f"no ports"
                    )
            elif rest:
                raise ValueError(
                    f"Line {ln_no}: 'short' takes the whole tied group in its "
                    f"port field and nothing else (got '{' '.join(rest)}'). "
                    f"Write '{parts[0]} short_to {rest[0]}' to keep two fields."
                )
            # Chain the whole node: (p0,p1), (p1,p2), ...  One port on each side
            # reduces to the historical single ShortPair(port-1, other-1), which
            # is what keeps every pre-existing `short_to` spec bit-identical.
            chain = list(ports) + [p for p in others if p not in ports]
            if len(chain) < 2:
                raise ValueError(
                    f"Line {ln_no}: a short needs at least two ports to tie "
                    f"together, '{parts[0]}' names one."
                )
            for a, b in zip(chain, chain[1:]):
                ts.couplings.append(ShortPair(a - 1, b - 1))
        elif kind == "lumped_to_gnd":
            params = parse_kv_rlc_params(rest)
            y = y_series_rlc(**params)   # shared: a pure function of frequency
            for port in ports:
                ts.per_port[port - 1] = LumpedToGnd(y, params)
        elif kind == "lumped_between":
            if not rest:
                raise ValueError(f"Line {ln_no}: lumped_between needs a partner port")
            others = _resolve_port_field(rest[0], nets, ln_no,
                                         "lumped_between partner")
            if len(others) != 1:
                raise ValueError(
                    f"Line {ln_no}: lumped_between takes exactly ONE partner "
                    f"port, but '{rest[0]}' selects {len(others)}. An N-to-M "
                    "lumped element is ambiguous -- write one line per element."
                )
            other = others[0]
            params = parse_kv_rlc_params(rest[1:])
            y = y_series_rlc(**params)   # shared: a pure function of frequency
            for port in ports:
                ts.couplings.append(LumpedBetween(port - 1, other - 1, y, params))
        else:
            raise ValueError(f"Line {ln_no}: unknown termination kind '{kind}'")
    _check_one_name_per_node(ts, nets)
    return ts


# ============================================================================
# Connection-table row model (the GUI's Mode 5 / Mode 6 editor)
# ============================================================================
#
# The editor is two tables: measurement ports (what am I measuring) and
# connections (what else is attached).  A row of either is a *statement over a
# set of ports*, not a single directive -- the port field takes a range, so a
# package's ground balls are one row rather than one row per ball.
#
# Rows are the GUI's storage.  They reach a TerminationSet by being serialised
# to DSL text and handed to parse_custom_termination_text, NOT by building a
# TerminationSet directly.  That is deliberate: it keeps one parser, one set of
# error messages, and one thing for the tests to pin, and it makes the "edit as
# text" escape hatch show exactly what is computed rather than an approximation
# of it.  See docs/design_connection_table.md.

# Connection-row kinds.  Probes are NOT here -- they live in the measurement
# port table, which is what keeps this table's "To" column single-domain (a
# port number or GND, never a measurement-port name).
CONN_KINDS = ("ground", "vdd", "open", "short", "rlc_gnd", "rlc_between")

# Kinds whose "To" field names one or more partner ports.  'short' is in the
# list for the LEGACY two-field spelling only: a short row created today puts
# the whole tied group in `ports` and leaves `to` empty (see short_group_spec),
# because a group of tied ports has no natural "from" and "to".  Rows saved by
# an older build still carry the split and must keep working.
CONN_KINDS_WITH_PARTNER = ("short", "rlc_between")

# Kinds that carry R / L / C values.
CONN_KINDS_WITH_RLC = ("rlc_gnd", "rlc_between")

# Kinds that can name the node they create.  Only a short creates one.
CONN_KINDS_WITH_NET = ("short",)


@dataclass
class MeasPortRow:
    """One row of the measurement-port table.  Port specs are 1-based text."""
    name: str = ""
    plus: str = ""
    minus: str = ""

    def is_blank(self) -> bool:
        return not (self.name.strip() or self.plus.strip() or self.minus.strip())


@dataclass
class ConnectionRow:
    """
    One row of the connection table.  All fields are text exactly as typed, so
    '5:12' round-trips as a range instead of expanding into eight rows.

    `to` is the partner port spec for 'rlc_between', and for the LEGACY
    two-field spelling of 'short'; it is ignored otherwise ('ground'/'rlc_gnd'
    are implicitly to GND).  Blank R/L/C mean OMITTED, which is not the same as
    zero: an omitted C is C=inf (no capacitor in the series branch), while C=0
    would be an open circuit.

    `net` is the name a 'short' row gives the node it creates, and is what lets
    a later row say `coil_tap` instead of picking one of its member ports.  It
    is ignored on every other kind (parse_custom_termination_text refuses `as`
    there rather than dropping it in silence).  A row loaded from a session
    saved before nets existed simply has net="" -- the field defaults, so the
    session format needs no migration.

    `enabled` is the debug switch: a disabled row keeps every cell it has and
    contributes NOTHING to the spec -- `rows_to_dsl_text` does not emit it, so
    it is exactly as if the row were deleted.  It exists because the two ways
    of asking "what is this connection worth?" without it are both destructive:
    deleting the row loses its R/L/C (and the ports, which on a package ground
    row is a range someone worked out), and switching the Kind to `open` is a
    DIFFERENT SPEC rather than an absent one -- `open` is a declaration, so it
    survives into `port_roles`, and on a row that was `rlc_gnd` it silently
    discards the element as well.  Defaults True, so a row from any older
    session is enabled and nothing about a saved spec moves.
    """
    kind: str = "ground"
    ports: str = ""
    to: str = ""
    R: str = ""
    L: str = ""
    C: str = ""
    net: str = ""
    enabled: bool = True

    def is_blank(self) -> bool:
        # `enabled` is deliberately NOT part of this: a blank row is one with
        # nothing typed in it, and switching an empty row off must not make it
        # count as a row.  It is also what keeps a disabled row out of the
        # tables' "blanks are dropped" path with its values intact.
        return not (self.ports.strip() or self.to.strip() or self.net.strip()
                    or self.R.strip() or self.L.strip() or self.C.strip())


def short_group_spec(row: ConnectionRow) -> str:
    """
    A 'short' row's tied group as ONE port field.

    The single-field shape is the point of R1-1: a group of tied ports has no
    natural "from" and "to", and being forced to split '5,6,7,8' across two
    cells is the complaint this round exists to fix.  Rows saved by an older
    build carry the split, so the two fields are joined here rather than
    migrated in place -- nothing has to rewrite a stored row to render it.

    NO SPACES, for collapse_ports' reason: the DSL is whitespace-tokenised and
    the port field is parts[0].
    """
    ports = (row.ports or "").strip()
    to = (row.to or "").strip()
    if not to:
        return ports
    return f"{ports},{to}" if ports else to


def _rlc_tokens(row: ConnectionRow) -> list[str]:
    """
    Non-blank R/L/C fields -> ['R=50', 'L=1n'] in canonical R, L, C order.

    A value containing WHITESPACE is rejected rather than emitted.  The DSL is
    whitespace-tokenised and parse_kv_rlc_params drops any token without an
    '=', so 'R=5 m' silently computes R = 5 ohm -- a factor of 1000 -- and
    'C=1 uF' silently computes C = 1 farad.  The cell UI invites exactly that
    (the unit is in the column header, so 'uF' looks like it belongs in the
    cell), and the validation strip re-parses the raw cell as ONE token, so it
    would cheerfully echo '5 mOhm' next to a computed 5 ohm.  There is no way
    to quote a value in the DSL, so the only answer that cannot be silently
    wrong is to refuse.
    """
    out = []
    for key in ("R", "L", "C"):
        val = getattr(row, key).strip()
        if not val:
            continue
        if any(ch.isspace() for ch in val):
            raise ValueError(
                f"{key} value '{val}' contains a space. Write it as one token "
                f"-- 5m, 0.5n, 1u -- with no unit ('{key}={val}' would be read "
                f"as '{key}={val.split()[0]}' and the rest thrown away). The "
                "unit belongs in the column header, not in the cell."
            )
        out.append(f"{key}={val}")
    return out


def rows_to_dsl_text(mport_rows: Sequence[MeasPortRow] = (),
                     conn_rows: Sequence[ConnectionRow] = (),
                     extra_lines: str = "") -> str:
    """
    Serialise the two tables to Mode 5 DSL text.

    ORDER IS LOAD-BEARING.  The DSL is last-assignment-wins, and measurement
    ports are emitted FIRST so that a later 'ground' row wins over a probe on
    the same port -- which is exactly the "ground wins" precedence that
    build_terminations_mode1/2/3 have always had.  Emitting them the other way
    round would make a table seeded from a named mode answer a different
    question.  tests/test_core.py::TestTerminationPrecedence pins this.

    Blank rows are skipped, and so are DISABLED connection rows -- a row with
    `enabled=False` contributes nothing at all, which is what makes the switch
    mean "as if this row were deleted" rather than "some other spec".  It is
    dropped HERE, in the one place rows become a spec, so every caller (the
    solve, the validation strip, the text hatch, the port roles, the run
    report) sees the same thing without any of them knowing about the flag.

    `extra_lines` is appended verbatim and is how comments and hand-written
    lines survive a round trip through the table.
    """
    lines: list[str] = []

    auto = 0
    used: set[str] = set()
    for row in mport_rows:
        if row.is_blank():
            continue
        name = row.name.strip()
        if not name:
            while True:
                auto += 1
                name = f"P{auto}"
                if name not in used:
                    break
        used.add(name)
        if row.plus.strip():
            lines.append(f"{row.plus.strip()} signal {name} +")
        if row.minus.strip():
            lines.append(f"{row.minus.strip()} signal {name} -")

    for row in conn_rows:
        if row.is_blank() or not getattr(row, "enabled", True):
            continue
        ports = row.ports.strip()
        if not ports:
            continue
        kind = row.kind
        if kind == "ground":
            lines.append(f"{ports} ground")
        elif kind == "vdd":
            lines.append(f"{ports} vdd")
        elif kind == "open":
            lines.append(f"{ports} open")
        elif kind == "short":
            # One field or two: a row written today puts the whole tied group in
            # `ports` and emits the single-field `short`; a row loaded from an
            # older session still carries the split and emits the `short_to` it
            # always did, so its DSL text -- and therefore its answer -- is
            # byte-identical to what that build produced.
            to = row.to.strip()
            head = f"{ports} short_to {to}" if to else f"{ports} short"
            net = row.net.strip()
            lines.append(f"{head} {NET_KEYWORD} {net}" if net else head)
        elif kind == "rlc_gnd":
            lines.append(" ".join([f"{ports} lumped_to_gnd", *_rlc_tokens(row)]))
        elif kind == "rlc_between":
            lines.append(" ".join([f"{ports} lumped_between {row.to.strip()}",
                                   *_rlc_tokens(row)]))
        else:
            raise ValueError(
                f"Unknown connection-row kind '{kind}' "
                f"(expected one of {', '.join(CONN_KINDS)})"
            )

    text = "\n".join(lines)
    extra = extra_lines.strip("\n")
    if extra:
        text = f"{text}\n{extra}" if text else extra
    return text + "\n" if text else ""


def dsl_text_to_rows(text: str) -> tuple[list[MeasPortRow], list[ConnectionRow], str]:
    """
    Parse DSL text back into the two tables -> (mport_rows, conn_rows, extra).

    Round-trip contract: this is NOT byte-exact, it is idempotent after one
    pass.  `rows_to_dsl_text(*dsl_text_to_rows(t))` re-parses to the same
    TerminationSet as `t`, and running the pair again changes nothing further.
    Comment lines land in `extra` so hand-written notes survive.

    Legacy 'signal B' is normalised here the same way resolve_meas_ports does
    it -- group "B" is the minus side of group "A" -- so an old spec imports as
    ONE measurement port with a plus and a minus side rather than two.

    Lines the table cannot represent are passed through in `extra` rather than
    dropped; nothing the user typed is ever silently lost.
    """
    order: list[str] = []
    plus: dict[str, list[str]] = {}
    minus: dict[str, list[str]] = {}
    conn: list[ConnectionRow] = []
    extra: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if line:
                extra.append(raw.rstrip())
            continue
        body = line.split("#", 1)[0].strip()
        parts = body.split()
        if len(parts) < 2:
            extra.append(raw.rstrip())
            continue
        ports, kind, rest = parts[0], parts[1].lower(), parts[2:]

        if kind == "signal":
            grp = rest[0] if rest else "A"
            if grp.upper() in LEGACY_GROUP_NAMES:
                grp = grp.upper()
            sign = +1
            if len(rest) >= 2 and rest[1] == "-":
                sign = -1
            elif len(rest) >= 2 and rest[1] != "+":
                extra.append(raw.rstrip())   # malformed; let the parser complain
                continue
            grp, sign = _normalize_signal(Signal(grp, sign))
            if grp not in plus:
                order.append(grp)
                plus[grp], minus[grp] = [], []
            (plus if sign > 0 else minus)[grp].append(ports)
        elif kind in ("ground", "gnd"):
            conn.append(ConnectionRow(kind="ground", ports=ports))
        elif kind == "vdd":
            conn.append(ConnectionRow(kind="vdd", ports=ports))
        elif kind == "open":
            conn.append(ConnectionRow(kind="open", ports=ports))
        elif kind in ("short", "short_to"):
            body_toks, net, ok = _split_net_tail(rest)
            if not ok or (kind == "short_to" and not body_toks) \
                    or (kind == "short" and body_toks):
                extra.append(raw.rstrip())   # malformed; let the parser complain
                continue
            conn.append(ConnectionRow(kind="short", ports=ports,
                                      to=body_toks[0] if body_toks else "",
                                      net=net))
        elif kind == "lumped_to_gnd":
            conn.append(_conn_with_rlc("rlc_gnd", ports, "", rest))
        elif kind == "lumped_between" and rest:
            conn.append(_conn_with_rlc("rlc_between", ports, rest[0], rest[1:]))
        else:
            extra.append(raw.rstrip())

    mports = [MeasPortRow(name=g,
                          plus=",".join(plus[g]),
                          minus=",".join(minus[g]))
              for g in order]
    return mports, conn, "\n".join(extra)


def _conn_with_rlc(kind: str, ports: str, to: str,
                   tokens: Sequence[str]) -> ConnectionRow:
    """Build an R/L/C-bearing connection row from 'R=50 L=1n' tokens."""
    row = ConnectionRow(kind=kind, ports=ports, to=to)
    for tok in tokens:
        if "=" not in tok:
            continue
        key, val = tok.split("=", 1)
        key = key.strip().upper()
        if key in ("R", "L", "C"):
            setattr(row, key, val.strip())
    return row


def build_terminations_rows(mport_rows: Sequence[MeasPortRow] = (),
                            conn_rows: Sequence[ConnectionRow] = (),
                            extra_lines: str = "",
                            nports: int | None = None) -> TerminationSet:
    """
    Connection-table rows -> TerminationSet, via the DSL.

    Going through the text keeps parse_custom_termination_text as the single
    validation authority: one parser, one set of error messages, and the "edit
    as text" view shows literally what gets computed.

    Pass `nports` (the file's port count) to reject out-of-range ports here,
    with a message naming the file size, instead of letting a one-digit typo
    become a plausible wrong answer.
    """
    ts = parse_custom_termination_text(
        rows_to_dsl_text(mport_rows, conn_rows, extra_lines))
    if nports is not None:
        _validate_port_indices(ts, int(nports))
    return ts


def _merge_view(terminations: TerminationSet):
    """
    Union-Find over the ShortPairs -> (find, members).

    `find(port0)` is the merged node's representative and `members[root]` lists
    its 0-based ports.  Shared by inert_lumped_messages, parallel_stamp_messages
    and _check_one_name_per_node so the three can never disagree about what is
    one node -- they are three verdicts on the same question.

    It duplicates compute_z_matrix's Union-Find -- deliberately.  That one is
    inside the bit-exact reduction path and is fused with the index plan it
    builds; factoring it out to share it would mean editing the one function
    the golden regression exists to pin, to buy 8 lines.
    """
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            union(cpl.port_i, cpl.port_j)

    members: dict[int, list[int]] = {}
    for p in sorted(set(parent) | set(terminations.per_port)):
        members.setdefault(find(p), []).append(p)
    return find, members


def _params_key(elem) -> tuple:
    """
    What makes two lumped stamps IDENTICAL, for the parallel check.

    Two elements are only "the same element repeated" when they carry the same
    R, L and C.  Grouping by value and not merely by node pair is what keeps
    `1 lumped_between 4 R=50` next to `1 lumped_between 4 L=1n` -- a perfectly
    ordinary R || L -- out of the refusal, while catching the one-row-N-ports
    case the refusal exists for.

    `params` is None on a TerminationSet built in code rather than parsed, so
    the fallback keys on the y_func OBJECT: one DSL directive shares one closure
    across all the ports it names (see parse_custom_termination_text), which is
    exactly the "same element repeated" relation, just without the numbers.
    """
    p = getattr(elem, "params", None)
    if p is None:
        return ("y", id(elem.y_func))
    return ("v", p.get("R", 0.0), p.get("L", 0.0), p.get("C", math.inf))


def _effective_parallel(params: dict | None, n: int) -> str:
    """
    'R 50 Ω becomes 16.7 Ω' for n identical elements in parallel, or "".

    PER ELEMENT TYPE, not a template: n identical impedances in parallel divide
    R by n and L by n and MULTIPLY C by n, and printing one rule for all three
    would be wrong for whichever type the user actually typed.  Only the values
    the user gave are shown -- an omitted R is 0 and an omitted C is inf, and
    neither is a value anybody wrote down.
    """
    if not params or n < 2:
        return ""
    bits: list[str] = []
    R, L, C = params.get("R", 0.0), params.get("L", 0.0), params.get("C", math.inf)
    if R:
        bits.append(f"R {format_si(R, 'Ω')} becomes {format_si(R / n, 'Ω')}")
    if L:
        bits.append(f"L {format_si(L, 'H')} becomes {format_si(L / n, 'H')}")
    if math.isfinite(C):
        bits.append(f"C {format_si(C, 'F')} becomes {format_si(C * n, 'F')}")
    return "; ".join(bits)


def parallel_stamp_messages(terminations: TerminationSet) -> list[str]:
    """
    Identical lumped elements stamped N times across ONE merged node.

    `lumped_between` and `lumped_to_gnd` take a range on their left and apply
    the element to each listed port INDEPENDENTLY.  That is right and it is the
    normal flip-chip case -- 54 VSS bumps each with its own 20 pH is 54 separate
    elements -- so a range there is never refused on its own.

    It is wrong the moment those ports are ALREADY ONE NODE, because then the N
    stamps land between the same two nodes and are N identical elements in
    parallel.  Measured on the 5-port probe network in tests/test_conn_nets.py:

        1 short_to 2,3 ; 1     lumped_between 4 L=10f  ->  10.000 fH   (meant)
        1 short_to 2,3 ; 1,2,3 lumped_between 4 L=10f  ->   3.333 fH   (typed)
        1 short_to 2,3,4 ; 1     lumped_to_gnd R=50    ->  41.667 Ohm  (250||50)
        1 short_to 2,3,4 ; 1,2,3 lumped_to_gnd R=50    ->  15.625 Ohm  (250||16.7)

    Nothing raised, nothing warned, and inert_lumped_messages -- the check next
    to this one -- said nothing either: it only reports elements worth EXACTLY
    zero.  The number is wrong by a factor of N and looks entirely plausible,
    which is the failure class this whole round exists to close.

    A pure function of the TerminationSet, 1-BASED in its messages, empty when
    there is nothing to say.  The repair is in the message because the fix is
    not obvious from the symptom: name ONE member port (any of them -- the node
    is merged), or name the node with `short ... as <name>` and use that.
    """
    find, _members = _merge_view(terminations)
    # (sort key, message).  Sorted at the end by the PORT NUMBERS the message
    # names, never by the dict keys: those carry Union-Find roots, which are
    # arbitrary integers -- see the note below.  The strip shows two lines, so
    # which message is first has to be decided by something the reader can see.
    found: list[tuple] = []

    # --- elements between two nodes -----------------------------------------
    groups: dict[tuple, list[LumpedBetween]] = {}
    for cpl in terminations.couplings:
        if not isinstance(cpl, LumpedBetween):
            continue
        ra, rb = find(cpl.port_i), find(cpl.port_j)
        key = (min(ra, rb), max(ra, rb), _params_key(cpl))
        groups.setdefault(key, []).append(cpl)
    for key, same in groups.items():
        lo, hi, _pk = key
        n = len(same)
        if n < 2 or lo == hi:
            # lo == hi is an element with BOTH ends on one node: worth exactly
            # zero, not N times too much.  inert_lumped_messages owns that one.
            continue
        # Sort each coupling's two ends by which NODE they land on, not by which
        # slot they were typed in: `1 lumped_between 4` and `4 lumped_between 1`
        # share a key, and taking port_i for both would name ports 1 and 4 as
        # "one node" when they are the two ends.
        lo_ports = sorted({(c.port_i if find(c.port_i) == lo else c.port_j) + 1
                           for c in same})
        hi_ports = sorted({(c.port_j if find(c.port_j) == hi else c.port_i) + 1
                           for c in same})
        # WHICH of the two is the merged side is decided by the port lists, and
        # never by `lo` / `hi`.  Those are Union-Find ROOTS -- arbitrary
        # integers whose order falls out of which port happened to win its
        # union -- so testing only the `lo` side made the refusal depend on the
        # user's port numbering.  Measured before this was fixed, on the 5-port
        # probe network with the group moved to high port numbers:
        #     1,2,3    short + 1,2,3    lumped_between 1  -> 3.3333 fH REFUSED
        #     21,22,23 short + 21,22,23 lumped_between 1  -> 3.3333 fH SILENT
        # The same network, the same factor-of-3 error against a typed 10 fH,
        # and the only difference was that port 1's root was the smaller
        # number.  A user cannot see a Union-Find root.
        merged, other = ((lo_ports, hi_ports) if len(lo_ports) >= len(hi_ports)
                         else (hi_ports, lo_ports))
        if len(merged) < 2:
            # One port on each side repeated N times is the same line typed
            # twice, not a range over a merged node -- it is visible on its own
            # row and saying "ports 1 are ALREADY ONE NODE" would be false.
            continue
        left = collapse_ports(merged)
        right = collapse_ports(other)
        eff = _effective_parallel(getattr(same[0], "params", None), n)
        found.append((
            (merged[0], other[0]),
            f"⚠ ports {left} are ALREADY ONE NODE (a short ties them "
            f"together), so the element to port {right} is stamped {n} times "
            f"in PARALLEL, not once"
            + (f" -- {eff}. " if eff else f" ({n} identical elements). ")
            + f"Put ONE member port in that row, or name the node with "
            f"'{NET_KEYWORD} <name>' on the short and use the name."
        ))

    # --- elements to ground -------------------------------------------------
    gnd_groups: dict[tuple, list[int]] = {}
    for port, term in terminations.per_port.items():
        if isinstance(term, LumpedToGnd):
            gnd_groups.setdefault((find(port), _params_key(term)), []).append(port)
    for key, ports_here in gnd_groups.items():
        same_ports = sorted(ports_here)
        n = len(same_ports)
        if n < 2:
            continue
        left = collapse_ports([p + 1 for p in same_ports])
        term = terminations.termination_of(same_ports[0])
        eff = _effective_parallel(getattr(term, "params", None), n)
        found.append((
            (same_ports[0] + 1, 0),
            f"⚠ ports {left} are ALREADY ONE NODE (a short ties them "
            f"together), so the element to GND is stamped {n} times in "
            f"PARALLEL, not once"
            + (f" -- {eff}. " if eff else f" ({n} identical elements). ")
            + f"Put ONE member port in that row, or name the node with "
            f"'{NET_KEYWORD} <name>' on the short and use the name."
        ))
    found.sort(key=lambda item: item[0])
    return [text for _key, text in found]


def inert_lumped_messages(terminations: TerminationSet) -> list[str]:
    """
    Lumped elements that are in the set but contribute EXACTLY nothing.

    compute_z_matrix stamps every lumped element onto Y and only THEN merges
    shorted ports and drops grounded ones.  Both of those later steps can
    annihilate a stamp completely:

      * a LumpedBetween whose two ports land on the same merged node.  The
        stamp is +y, +y, -y, -y over that node's 2x2 block, so summing the
        rows and columns cancels it to zero.  Measured on a real spec:
        `5 short_to 6` next to `5 lumped_between 6 R=...` gave answers for
        R=20 and R=2000 differing by 5e-12 relative -- i.e. roundoff, with
        the ideal-short answer reported both times;
      * a LumpedBetween with BOTH ends grounded, or a LumpedToGnd on a port
        that a short ties to a grounded one: the row and column carrying the
        stamp are deleted outright (measured: bit-identical, 0.0 relative).

    Neither case raises and neither changes the number on screen, so the only
    symptom is that editing R/L/C does nothing -- which reads as "the tool
    ignores my resistor" rather than "my spec shorts it out".  Worse, the
    validation strip used to show `✓ port 5 → 6: 20 Ω` for exactly this, a
    green tick asserting the element was applied.

    A pure function of the TerminationSet, so the GUI strip, Calculate and the
    CLI can all report the same thing.  Messages are 1-BASED, empty when every
    lumped element contributes something.

    This is "the element is worth EXACTLY zero"; parallel_stamp_messages beside
    it is "the element is worth N times what you typed".  Both read the same
    _merge_view, so they cannot disagree about what one node is.
    """
    find, members = _merge_view(terminations)

    def node_is_ground(port: int) -> bool:
        """Does this port's MERGED node evaluate to ground? Mirrors merge_terms."""
        grp = members.get(find(port), [port])
        terms = [terminations.termination_of(p) for p in grp]
        if any(isinstance(t, Signal) for t in terms):
            return False        # a probe on the node wins over ground
        return any(isinstance(t, (Ground, Vdd)) for t in terms)

    msgs: list[str] = []
    for cpl in terminations.couplings:
        if not isinstance(cpl, LumpedBetween):
            continue
        i, j = cpl.port_i + 1, cpl.port_j + 1
        if find(cpl.port_i) == find(cpl.port_j):
            msgs.append(
                f"⚠ the R/L/C element between ports {i} and {j} is SHORTED OUT "
                f"-- a short ties those ports together, so its value has no "
                f"effect at all (changing it will not change the answer). "
                f"Delete the short, or delete the element."
            )
        elif node_is_ground(cpl.port_i) and node_is_ground(cpl.port_j):
            msgs.append(
                f"⚠ the R/L/C element between ports {i} and {j} has BOTH ends "
                f"grounded, so its value has no effect at all."
            )
    for port in sorted(terminations.per_port):
        if (isinstance(terminations.termination_of(port), LumpedToGnd)
                and node_is_ground(port)):
            msgs.append(
                f"⚠ the R/L/C element from port {port + 1} to ground sits on a "
                f"node that a short already ties to ground, so its value has "
                f"no effect at all."
            )
    return msgs


# ============================================================================
# Port roles (what every port of the file is actually doing)
# ============================================================================
#
# The tool already harvests "! Port[12] = VDD_ball_2" into TouchstoneData.
# port_names and then does almost nothing with it.  On a 153-port package
# export nobody has memorised the ball map, and the failure that costs real
# time is not a crash -- it is "I grounded 51 of the 54 ground balls, the
# number was plausible, and I found out three weeks later".  Everything below
# is pure so the GUI's overview strip, its validation strip and the Ports &
# Roles window can never disagree about what a port is doing.

ROLE_PROBE_PLUS = "probe +"
ROLE_PROBE_MINUS = "probe −"
ROLE_GROUND = "ground"
ROLE_VDD = "vdd"
ROLE_ELEMENT = "element"
ROLE_SHORTED = "shorted"
ROLE_OPEN = "open"

# Display order of the overview buckets.  A role is finer-grained than a bucket
# (the two probe sides collapse into one "probe" count) because the strip has
# to stay short while the window has to say which probe touched the port.
OVERVIEW_BUCKETS = ("probe", "ground", "vdd", "element", "shorted", "open")

ROLE_TO_BUCKET = {
    ROLE_PROBE_PLUS: "probe",
    ROLE_PROBE_MINUS: "probe",
    ROLE_GROUND: "ground",
    ROLE_VDD: "vdd",
    ROLE_ELEMENT: "element",
    ROLE_SHORTED: "shorted",
    ROLE_OPEN: "open",
}


@dataclass(frozen=True)
class PortRole:
    """What one port of the file is doing under a given TerminationSet."""
    index: int          # 1-BASED, the number the user types
    name: str           # from the file's "! Port[n] = ..." comments, "" if none
    role: str           # one of the ROLE_* constants
    source: str = ""    # which row / kept-as-text line put it there ("" = none)
    group: str = ""     # measurement-port name, for the two probe roles

    @property
    def bucket(self) -> str:
        return ROLE_TO_BUCKET[self.role]


def _role_of(term: TerminationSet, port0: int,
             elem_ports: set, short_ports: set) -> tuple[str, str]:
    """(role, measurement-port name) for one 0-based port."""
    t = term.termination_of(port0)
    if isinstance(t, Signal):
        group, sign = _normalize_signal(t)
        return (ROLE_PROBE_PLUS if sign > 0 else ROLE_PROBE_MINUS), group
    if isinstance(t, Vdd):
        return ROLE_VDD, ""
    if isinstance(t, Ground):
        return ROLE_GROUND, ""
    if isinstance(t, LumpedToGnd) or port0 in elem_ports:
        return ROLE_ELEMENT, ""
    if port0 in short_ports:
        return ROLE_SHORTED, ""
    return ROLE_OPEN, ""


def port_roles(term: TerminationSet | None,
               nports: int | None = None,
               port_names: Sequence[str] | None = None,
               sources: dict | None = None) -> list[PortRole]:
    """
    One record per port, in port order.  THE single classifier.

    `nports` is the file's port count.  With it, every port of the file gets a
    record and the ones the spec never mentioned come back as `open` -- which is
    the whole point, because an unmentioned port is exactly the one nobody
    checked.  WITHOUT it (no file selected) the open ports are not merely
    unknown in number, they are unknowable: only the ports the rows mention are
    listed, and an explicit `open` row is dropped too, so a caller can never
    render an "open" count that was invented from the largest port typed.

    `sources` maps a 1-BASED port to the row that last assigned it (see
    row_sources).  It is passed in rather than derived here because a
    TerminationSet carries no provenance -- by design; it is what the reduction
    consumes, and the rows are one of several ways to build one.
    """
    if term is None:
        return []
    elem_ports: set = set()
    short_ports: set = set()
    for cpl in term.couplings:
        target = elem_ports if isinstance(cpl, LumpedBetween) else short_ports
        target.add(cpl.port_i)
        target.add(cpl.port_j)

    if nports is not None:
        scan: Sequence[int] = range(int(nports))
    else:
        scan = sorted(set(term.per_port) | elem_ports | short_ports)

    names = list(port_names or [])
    src = sources or {}
    out: list[PortRole] = []
    for i in scan:
        role, group = _role_of(term, i, elem_ports, short_ports)
        if nports is None and role == ROLE_OPEN:
            continue
        out.append(PortRole(index=i + 1,
                            name=names[i] if i < len(names) else "",
                            role=role,
                            source=src.get(i + 1, ""),
                            group=group))
    return out


def row_sources(mport_rows: Sequence[MeasPortRow] = (),
                conn_rows: Sequence[ConnectionRow] = (),
                extra_lines: str = "") -> dict[int, str]:
    """
    1-based port -> the row that LAST assigned it.

    Walked in exactly the order rows_to_dsl_text emits (every measurement port,
    then every connection, then the kept-as-text block), because the DSL is
    last-assignment-wins: the row that decides a port is the last one to name
    it, which is the same rule that makes a `ground` row beat a probe.

    A port field naming a NODE resolves the same way the DSL resolves it -- to
    one representative member -- so the "From" column of Ports & Roles and the
    spec cannot disagree about which row decided a port.  A name nothing defines
    contributes nothing, exactly as a half-typed range does.  Measured on a
    package-sized spec (54 ground balls, 8 named shorts, 8 elements, 6 probes,
    29 DSL lines): rows_to_dsl_text 0.010 ms + _collect_nets 0.043 ms on top of
    a 0.090 ms total, which is nothing against the keystroke this runs from.

    Never raises -- a half-typed range simply contributes nothing, the same way
    it contributes nothing to the spec.
    """
    src: dict[int, str] = {}
    try:
        nets = _collect_nets(rows_to_dsl_text(mport_rows, conn_rows, extra_lines))
    except Exception:
        nets = {}

    def mark(spec: str, label: str) -> None:
        key = _net_key(spec)
        if key in nets:
            if nets[key].port:
                src[nets[key].port] = label
            return
        try:
            ports = parse_port_range(spec)
        except Exception:
            return
        for p in ports:
            src[p] = label

    for i, row in enumerate(mport_rows, start=1):
        if row.is_blank():
            continue
        if row.plus.strip():
            mark(row.plus, f"probe row {i} (+)")
        if row.minus.strip():
            mark(row.minus, f"probe row {i} (−)")

    for i, row in enumerate(conn_rows, start=1):
        if row.is_blank() or not row.ports.strip():
            continue
        mark(row.ports, f"conn row {i}")
        # The partner side of a short / rlc_between is assigned by the same row.
        if row.kind in ("short", "rlc_between") and row.to.strip():
            mark(row.to, f"conn row {i}")

    for n, raw in enumerate((extra_lines or "").splitlines(), start=1):
        body = raw.split("#", 1)[0].strip()
        parts = body.split()
        if len(parts) < 2:
            continue
        mark(parts[0], f"text line {n}")
        if parts[1].lower() in ("short_to", "lumped_between") and len(parts) > 2:
            mark(parts[2], f"text line {n}")
    return src


def collapse_ports(ports: Sequence[int]) -> str:
    """
    [1, 2, 3, 7] -> '1-3,7'.

    NO SPACES, ever.  The DSL is whitespace-tokenised and the leading port
    field is parts[0], so '1-3, 7' would parse as the port field '1-3,' with a
    stray '7' where the keyword belongs.  parse_port_range accepts the comma
    form, so this round-trips.
    """
    vals = sorted({int(p) for p in ports})
    if not vals:
        return ""
    runs: list[str] = []
    start = prev = vals[0]
    for p in list(vals[1:]) + [None]:
        if p == prev + 1:
            prev = p
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    return ",".join(runs)


@dataclass(frozen=True)
class MergedNode:
    """
    One node a short created, and the token that refers to it.

    `ref` is what a port field must contain to mean this node: the net name when
    it has one, otherwise its first member port -- because referring to a merged
    node by any ONE member already works and is the whole reason nets are sugar.
    It is never the whole group: that spelling is the N-parallel-elements bug.
    """
    ports: tuple = ()     # 1-based members, ascending, always >= 2
    name: str = ""        # net name as typed, "" when the node is unnamed
    ref: str = ""


def merged_nodes(mport_rows: Sequence[MeasPortRow] = (),
                 conn_rows: Sequence[ConnectionRow] = (),
                 extra_lines: str = "") -> list[MergedNode]:
    """
    Every merged node the rows create, ascending by first member.

    For the editor's port dropdowns: R1-2 wants merged nodes at the TOP of them,
    so that referring to a node is the cheap gesture and listing its members --
    which silently multiplies an element by N -- is the awkward one.

    Pure and NEVER raises: it runs from the same variable traces the strips do,
    where a raise reaches no handler we control.  A half-typed spec simply
    contributes fewer nodes.

    It re-serialises and re-parses rather than taking a TerminationSet, so a
    caller cannot hand it a set built some other way and get node names that do
    not match the rows on screen.  Measured on a package-sized spec (54 ground
    balls, 8 named shorts, 8 elements, 6 probes): 0.331 ms, of which the parse
    is 0.149 ms.
    """
    try:
        text = rows_to_dsl_text(mport_rows, conn_rows, extra_lines)
        ts = parse_custom_termination_text(text)
        nets = _collect_nets(text)
    except Exception:
        return []
    find, members = _merge_view(ts)
    by_root_name: dict[int, str] = {}
    for d in nets.values():
        if d.port:
            by_root_name.setdefault(find(d.port - 1), d.name)
    out: list[MergedNode] = []
    for root, group in members.items():
        if len(group) < 2:
            continue
        ports = tuple(p + 1 for p in sorted(group))
        name = by_root_name.get(root, "")
        out.append(MergedNode(ports=ports, name=name,
                              ref=name or str(ports[0])))
    out.sort(key=lambda m: m.ports[0])
    return out


# ---- open ports that look like they were meant to be terminated ------------
#
# A family is a set of ports whose names share a prefix once a trailing ball
# NUMBER is stripped: VSS_ball_31 / VSS_ball_44 belong to 'VSS_ball'.  The check
# fires when a family is overwhelmingly terminated and a small remnant is not.
#
# The thresholds exist to stop it crying wolf, and each was picked against a
# real fixture rather than by taste:
#   * MIN_FAMILY = 4 keeps 'coil1' / 'coil2' out (tests/fixtures/
#     coupled_2port_gndref.s2p).  Probing one coil and leaving the other open is
#     the ordinary way to use that file, and a 2-member family is not evidence
#     of anything;
#   * MIN_TERMINATED = 3 means "a set", not "the one next to it";
#   * MAX_OPEN_FRACTION = 0.25 is what makes this a REMNANT check.  Grounding 5
#     of a 10-ball family and leaving 5 open is a deliberate split; grounding 54
#     and leaving 3 is a typo.  It is also what keeps a file whose ports are all
#     named port1..port153 silent -- one family, most of it open.
OPEN_CLUSTER_MIN_FAMILY = 4
OPEN_CLUSTER_MIN_TERMINATED = 3
OPEN_CLUSTER_MAX_OPEN_FRACTION = 0.25

# How many of the offending names the message spells out before it says "…".
OPEN_CLUSTER_NAMES_SHOWN = 3

_TERMINATED_ROLES = (ROLE_GROUND, ROLE_VDD, ROLE_PROBE_PLUS, ROLE_PROBE_MINUS)


def name_prefix(name: str) -> str:
    """
    'VSS_ball_31' -> 'VSS_ball'; 'coil1' -> 'coil'; 'in_p' -> 'in_p'; '' -> ''.

    Only a TRAILING run of digits is stripped, and only then are the separators
    in front of it.  Stripping digits anywhere would make 'c1_p' and 'c2_p'
    (tests/fixtures/coupled_4port_float.s4p) one family, which is precisely the
    false alarm this whole check has to avoid: those are two different coils.
    A name that does not end in a digit is its own family of one.
    """
    s = (name or "").strip()
    if not s:
        return ""
    i = len(s)
    while i > 0 and s[i - 1].isdigit():
        i -= 1
    if i == len(s):
        return s
    while i > 0 and s[i - 1] in "_-.: []":
        i -= 1
    return s[:i]


@dataclass(frozen=True)
class OpenNameCluster:
    """A family of same-named ports that is mostly terminated and partly not."""
    prefix: str
    kind: str                    # "grounded" or "probed"
    open_ports: tuple            # 1-based
    open_names: tuple
    terminated: int


def open_name_clusters(roles: Sequence[PortRole]) -> list[OpenNameCluster]:
    """
    Open ports whose NAMES say they belong to a set the user terminated.

    Pure, and silent on a file with no port names at all (every prefix is "",
    which is skipped) -- that file has no evidence to offer and a warning
    derived from nothing is worse than no warning.
    """
    fams: dict[str, list[PortRole]] = {}
    for r in roles:
        p = name_prefix(r.name)
        if p:
            fams.setdefault(p, []).append(r)

    out: list[OpenNameCluster] = []
    for prefix in sorted(fams):
        members = fams[prefix]
        if len(members) < OPEN_CLUSTER_MIN_FAMILY:
            continue
        opens = [m for m in members if m.role == ROLE_OPEN]
        if not opens:
            continue
        if len(opens) > OPEN_CLUSTER_MAX_OPEN_FRACTION * len(members):
            continue
        term_roles = [m.role for m in members if m.role in _TERMINATED_ROLES]
        if len(term_roles) < OPEN_CLUSTER_MIN_TERMINATED:
            continue
        grounded = sum(1 for r in term_roles if r in (ROLE_GROUND, ROLE_VDD))
        kind = "grounded" if grounded * 2 >= len(term_roles) else "probed"
        opens.sort(key=lambda m: m.index)
        out.append(OpenNameCluster(
            prefix=prefix, kind=kind,
            open_ports=tuple(m.index for m in opens),
            open_names=tuple(m.name for m in opens),
            terminated=len(term_roles)))
    return out


def open_port_name_messages(roles: Sequence[PortRole]) -> list[str]:
    """One '⚠ …' line per cluster, ready for the validation strip."""
    msgs: list[str] = []
    for c in open_name_clusters(roles):
        n = len(c.open_ports)
        shown = list(c.open_names[:OPEN_CLUSTER_NAMES_SHOWN])
        if n > OPEN_CLUSTER_NAMES_SHOWN:
            shown.append("…")
        msgs.append(
            f"⚠ {n} port{'' if n == 1 else 's'} left OPEN whose "
            f"name{'' if n == 1 else 's'} match the {c.terminated} "
            f"'{c.prefix}' ports you {c.kind} ({', '.join(shown)}). "
            "Check 'Ports & Roles'.")
    return msgs



# ============================================================================
# Port-index validation
# ============================================================================
#
# This lives with the DECLARATION rather than with the arithmetic because
# three callers need it and only one of them is the solver:
# `build_terminations_coupling` and `build_terminations_rows` above both call
# it when they are given a port count, and `compute_z_matrix` calls it as its
# backstop.  A file's port count is a property of what was declared against
# it.

def _validate_port_indices(terminations: TerminationSet, n: int) -> None:
    """
    Reject port indices that do not exist in this file.

    Without this an out-of-range probe port is silently dropped (the resolver
    only scans 0..n-1), which turns a one-digit typo -- '3 / 5' on a 4-port
    file -- into a ground-referenced measurement that reports a plausible-
    looking wrong number instead of an error.
    """
    bad: set[int] = {p for p in terminations.per_port if p < 0 or p >= n}
    for cpl in terminations.couplings:
        for p in (cpl.port_i, cpl.port_j):
            if p < 0 or p >= n:
                bad.add(p)
    if bad:
        listed = ", ".join(str(p + 1) for p in sorted(bad))
        raise ValueError(
            f"Port number(s) {listed} are outside this file's {n} ports "
            "(port numbers are 1-based)."
        )
