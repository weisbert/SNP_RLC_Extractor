"""
pkg_rlc/services/tracenets.py -- the Trace model workspace's engine (L2, no Tk).

One NET is one routed trace, named by the user, with an IN end and an OUT end
given as 1-based port text exactly as typed into the workspace's table.  This
module does three things with a table of them and nothing else:

    validate_nets   every cell's complaint, as (row, column, message), so the
                    panel can mark the cell and say why IN the table rather
                    than in a dialog;
    solve_nets      one `compute_z_matrix` per net, each on its own, so one
                    bad row never costs the others their answer;
    rebandwidth     the -3 dB table again for a new source / load, off the
                    arrays a solve left behind -- no re-solve.

WHY EACH NET IS SOLVED SEPARATELY
---------------------------------
A pi has two nodes, so each net is a two-measurement-port solve: declaring
all the nets' ends in ONE `TerminationSet` would make every other net's ports
measurement ports too, and `compute_z_matrix` leaves measurement ports OPEN
-- which is what "everything unlisted is OPEN" already means, so the numbers
would in fact agree.  They are solved separately anyway because the failure
modes must not be shared: a port spec that does not parse, a name that is
reserved, a side with no return path, each belongs to ONE row and is reported
on that row, while the rows beside it are answered.

THE PORT-ORDER CAVEAT
---------------------
`compute_z_matrix` returns its measurement ports in `resolve_meas_ports`
order -- by LOWEST PORT NUMBER, not by declaration order.  A net whose OUT
end sits on a lower port than its IN end comes back as (OUT, IN), and reading
the matrix positionally would swap the two ends with no symptom at all on a
symmetric trace and a wrong shunt assignment on an asymmetric one.  So IN and
OUT are picked by the RETURNED `port_names`, exactly as
`pkg_rlc.frontend.cli._run_trace_model` does, and
`tests/test_tracenets.py::TestPortOrder` declares OUT below IN to pin it.

THE OTHER PORTS ARE CONNECTION ROWS (stage 3, design § 8 item 2)
-----------------------------------------------------------------
Stage 1 had one GND field and "everything else OPEN".  The other ports are
now the editor's own connection rows (`ConnectionRow`: ground, vdd, open,
short, R/L/C to ground or between two ports), and every net is solved
through the ONE row path the RLC editor uses:

    build_terminations_rows([MeasPortRow(IN, in+, in-),
                             MeasPortRow(OUT, out+, out-)], conn_rows, "",
                            nports=...)  +  compute_z_matrix

so the unified probe rules (`probe_rule_issues`, design § 3.3) hold here
per net: an IN+ / OUT+ port in a ground row is refused on that cell, and a
grounded '-' side is accepted in amber and folded into ground -- that end
is then measured single-ended, and the imbalance check is skipped.

GROUND-ONLY ROWS ARE BIT-IDENTICAL TO THE OLD GND FIELD.  Measured before
the switch: every 2-port and 4-port fixture, every IN/OUT assignment
(single-ended and differential) under every subset of the remaining ports
grounded -- 368 cases -- gives an `np.array_equal` Zmat through
`build_terminations_coupling(..., gnd)` and through one ground row here.
`tests/test_tracenets.py::TestGroundRowIsTheOldGndField` keeps that
measurement as a test, and the CLI-equality tests pass their GND as a
ground row with their assertions unchanged.  An old session's `gnd` string
is read as ONE ground row (`ground_rows`).

THE IMBALANCE CHECK CARRIES THE CONNECTION ROWS
-----------------------------------------------
The differential pi assumes common mode OPEN at both ends; the four-port
imbalance check says how much that assumption hides.  The old Trace model
window ran that check with NO ground ports while the CLI ran it with the
declared ones, so the two surfaces could print different mode-conversion
numbers for one file.  This module follows the CLI: the four single-ended
probes are solved under the same connection rows the net itself was, and
`tests/test_tracenets.py::TestImbalanceCarriesGnd` has a pair that reads
balanced with its reference ports open and imbalanced with them grounded.

Imports: `pkg_rlc.physics.core`, `pkg_rlc.physics.tracemodel` (L0) and
`pkg_rlc.model.trace.snap_to_grid` (L1).  No tkinter, no matplotlib, no App.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Optional, Sequence

import numpy as np

from pkg_rlc.physics import tracemodel as tmod
from pkg_rlc.physics.core import (
    CONN_KINDS_WITH_RLC, ISSUE_MINUS_GROUNDED, ISSUE_PLUS_GROUNDED,
    ISSUE_PROBE_OVERRIDDEN, LEGACY_GROUP_NAMES, ConnectionRow, MeasPortRow,
    build_terminations_rows, collapse_ports, compute_z_matrix,
    merged_nodes, parse_port_range, parse_si,
    probe_rule_issues,
)
from pkg_rlc.model.trace import snap_to_grid

__all__ = [
    "NetRow", "CellIssue", "NetResult",
    "NET_COLUMNS", "PORT_COLUMNS", "IN_NAME", "OUT_NAME",
    "validate_nets", "solve_net", "solve_nets", "rebandwidth", "retarget",
    "net_signature", "ground_rows", "conn_row_issues", "net_mport_rows",
]

#: The table's columns, in the order the panel shows them.  A `CellIssue` of
#: the nets table has one of these as its `column`; one of the connections
#: table has a `ConnectionRow` field name ('ports', 'to', 'R', ...).
NET_COLUMNS = ("name", "in_p", "in_n", "out_p", "out_n")
PORT_COLUMNS = ("in_p", "in_n", "out_p", "out_n")
_COLUMN_LABEL = {"in_p": "IN+", "in_n": "IN-", "out_p": "OUT+", "out_n": "OUT-"}

#: The internal measurement-port names every net is solved under.  They are
#: what `PiModel.in_name` / `out_name` carry, and what the imbalance check's
#: four single-ended probes are derived from (`IN_p`, `IN_n`, ...).  Neither
#: is in `LEGACY_GROUP_NAMES`, which the probe rules refuse as a name.
IN_NAME = "IN"
OUT_NAME = "OUT"

DEFAULT_SRC_OHM = 1.0
DEFAULT_LOAD_EXTRA_F = 0.0


@dataclass
class NetRow:
    """One table row, as typed: 1-based port text, not parsed."""
    name: str = ""
    in_p: str = ""
    in_n: str = ""
    out_p: str = ""
    out_n: str = ""

    def is_blank(self) -> bool:
        return not any(str(v).strip() for v in
                       (self.name, self.in_p, self.in_n, self.out_p, self.out_n))

    @property
    def differential(self) -> bool:
        """True when either minus cell is filled (validation wants both)."""
        return bool(str(self.in_n).strip()) or bool(str(self.out_n).strip())


@dataclass(frozen=True)
class CellIssue:
    """
    One complaint about one cell.  `table` says which table: 'nets' (the
    net rows) or 'conn' (the connection rows).  `row` is the index into the
    sequence of that table the validator was handed (blank rows keep their
    index, so the panel maps it straight onto its table).  `column` is one
    of `NET_COLUMNS` on a net row and a `ConnectionRow` field ('ports',
    'to', 'R', 'L', 'C', 'kind') on a connection row.  `message` is English
    and names the fix.

    Duck-typed like the L0 `SpecIssue` (`table` / `row` / `column` /
    `is_error` / `message`), so one painter colours both.
    """
    row: int
    column: str
    severity: str           # 'error' | 'warning'
    message: str
    table: str = "nets"     # 'nets' | 'conn'

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True, eq=False)
class NetResult:
    """
    One solved net, frozen.  Plain arrays and a `PiModel`; no TraceConfig, no
    FileEntry -- identity is the `signature`, so the panel can tell a result
    that still describes its row from one that does not.

    `status` is 'ok' (the numbers are current), 'error' (`error` says why and
    `model` is None) or 'stale' (the row, GND, file or frequency changed since
    this was solved; the numbers are the ones it had).  `freqs` / `Z2` are
    kept so `rebandwidth` can answer a new source / load with no re-solve.
    """
    name: str
    status: str
    error: str
    differential: bool
    model: Optional[tmod.PiModel]
    reference: Optional[tmod.PiModel]
    mode_conversion: Optional[float]
    mc_note: str
    freq_snap: object
    freqs: object
    Z2: object
    bw_table: tuple
    corners: tuple
    model_band: tuple
    z_src_ohm: float
    c_load_extra_f: float
    signature: tuple
    warnings: tuple = ()


# ============================================================================
# Validation -- every complaint on the cell it belongs to
# ============================================================================

def ground_rows(gnd) -> list[ConnectionRow]:
    """
    The stage-1 GND field as connection rows: ONE ground row holding every
    port, or [] for a blank field.  `gnd` is the field's text (an old
    session's `gnd`) or an iterable of 1-based ports.

    Text is kept as typed when it is one token ('4-3' stays '4-3', so the
    cell shows what the user wrote); text with spaces in it ('3, 4') is
    rewritten as its collapsed range, because the DSL is whitespace-
    tokenised and '3, 4 ground' would not parse.  Text that does not parse
    at all is kept, so its error lands on the cell of the row it became.
    """
    if gnd is None:
        return []
    if isinstance(gnd, str):
        text = gnd.strip()
        if not text:
            return []
        if any(ch.isspace() for ch in text):
            try:
                text = collapse_ports(parse_port_range(text))
            except ValueError:
                pass
        return [ConnectionRow(kind="ground", ports=text)]
    ports = [int(p) for p in gnd]
    if not ports:
        return []
    return [ConnectionRow(kind="ground", ports=",".join(str(p) for p in ports))]


def net_mport_rows(row: "NetRow") -> list[MeasPortRow]:
    """
    The two measurement-port rows a net is solved as: IN = in+ / in-, OUT =
    out+ / out-, the cell text as typed.  Named `IN_NAME` / `OUT_NAME`.
    """
    return [MeasPortRow(IN_NAME, str(row.in_p).strip(), str(row.in_n).strip()),
            MeasPortRow(OUT_NAME, str(row.out_p).strip(),
                        str(row.out_n).strip())]


def _parse_cell(text: str) -> list[int]:
    return parse_port_range(str(text or ""))


def _cell_spec_error(column: str, text: str) -> str:
    return (f"{_COLUMN_LABEL.get(column, column)} '{text.strip()}' is not a "
            f"port spec -- use a port number, a list (1,3) or a range (6-14).")


_LINE_PREFIX = re.compile(r"^Line \d+:\s*")


def conn_row_issues(conn_rows: Sequence[ConnectionRow],
                    nports: int = 0) -> list[CellIssue]:
    """
    Every connection row's complaint, as a `CellIssue` with table='conn'
    and `row` the index into `conn_rows` (blank rows keep theirs).

    The parser is the authority, and it is asked ONE ROW AT A TIME on top of
    the rows before it that parsed: the builder speaks in DSL line numbers
    and stops at the first bad line, while a cell has to know which row it
    is.  Rows before it are included so a row naming a node an earlier
    short row created (`as coil_tap`) parses as it will in the solve.  The
    `Line N:` prefix is dropped -- the cell is the location.

    R / L / C cells are checked first with `parse_si` and the whitespace
    rule, so a bad value is reported on ITS cell rather than as a float
    conversion error on the port.  Two warnings the editor's strip also
    gives: a row with values but no Port does nothing, and an element row
    with no R, L or C is a 0-ohm short (NaN at every frequency).

    `nports <= 0` means no file: the range check is not made.  A disabled
    row contributes nothing and is not checked.  Never raises.
    """
    issues: list[CellIssue] = []
    limit = int(nports) if nports and int(nports) > 0 else None
    good: list[ConnectionRow] = []
    for i, row in enumerate(conn_rows or ()):
        if row is None or row.is_blank() or not getattr(row, "enabled", True):
            continue
        if not str(row.ports).strip():
            issues.append(CellIssue(
                i, "ports", "warning",
                "This row has values but no Port -- it does nothing.", "conn"))
            continue
        bad_value = False
        if row.kind in CONN_KINDS_WITH_RLC:
            for key in ("R", "L", "C"):
                val = str(getattr(row, key, "") or "").strip()
                if not val:
                    continue
                if any(ch.isspace() for ch in val):
                    msg = (f"{key} '{val}' contains a space -- write it as one "
                           f"word with no unit (5m, 0.5n, 1u).")
                else:
                    try:
                        parse_si(val)
                        continue
                    except (TypeError, ValueError):
                        msg = (f"{key} '{val}' is not a value -- use a number "
                               f"with an optional SI suffix (5m, 0.5n, 1u).")
                issues.append(CellIssue(i, key, "error", msg, "conn"))
                bad_value = True
        if bad_value:
            continue
        try:
            build_terminations_rows((), good + [row], "", nports=limit)
        except Exception as e:                              # noqa: BLE001
            msg = _LINE_PREFIX.sub("", str(e) or type(e).__name__)
            col = "to" if (row.kind == "rlc_between"
                           and "partner" in msg) else "ports"
            issues.append(CellIssue(i, col, "error", msg, "conn"))
            continue
        good.append(row)
        if row.kind in CONN_KINDS_WITH_RLC and not any(
                str(getattr(row, k, "") or "").strip() for k in ("R", "L", "C")):
            issues.append(CellIssue(
                i, "R", "warning",
                f"A {row.kind} element with no R, L or C is a 0-ohm short -- "
                "the result is NaN at every frequency.  Give it a value.",
                "conn"))
    return issues


#: The probe rules a NET takes from `probe_rule_issues`.  The others it
#: returns (reserved / duplicate names, a '-' with no '+', a port twice, out
#: of range) are the net table's own rules, said in its own words below.
_NET_PROBE_CODES = (ISSUE_PLUS_GROUNDED, ISSUE_MINUS_GROUNDED,
                    ISSUE_PROBE_OVERRIDDEN)

#: (measurement-port row, side) -> the net cell it came from.
_NET_CELL = {(0, "plus"): "in_p", (0, "minus"): "in_n",
             (1, "plus"): "out_p", (1, "minus"): "out_n"}


def _net_probe_issues(r: int, row: "NetRow",
                      conn_rows: Sequence[ConnectionRow]) -> list[CellIssue]:
    """The unified probe rules (design § 3.3) for ONE net, on its cells."""
    out: list[CellIssue] = []
    for iss in probe_rule_issues(net_mport_rows(row), conn_rows, ""):
        if iss.code not in _NET_PROBE_CODES:
            continue
        col = _NET_CELL.get((iss.row, iss.column))
        if col is None:
            continue
        msg = iss.message
        if iss.code == ISSUE_MINUS_GROUNDED:
            msg += (" This end is solved single-ended, and the imbalance "
                    "check is skipped.")
        out.append(CellIssue(r, col, iss.severity, msg))
    return out


def _net_merge_issues(r: int, row: "NetRow",
                      conn_rows: Sequence[ConnectionRow]) -> list[CellIssue]:
    """
    What a SHORT row does to a net's probe ends, said before Calculate.

    A short that ties two different ends of the net (IN- to OUT+, say):
    `compute_z_matrix` refuses it at solve time ("merged via short, but
    assigned to conflicting signal groups"), naming 0-based ports -- so it
    is said here, on the cell, 1-based.  (A short tying a probe port to a
    GROUNDED port is the L0 probe rules' now -- `probe_rule_issues` -- so
    the RLC editor and the Compare workspace refuse it too.)
    """
    out: list[CellIssue] = []
    try:
        nodes = merged_nodes(net_mport_rows(row), conn_rows, "")
    except Exception:                                       # noqa: BLE001
        return out
    where: dict[int, str] = {}
    for col in PORT_COLUMNS:
        try:
            for p in _parse_cell(str(getattr(row, col)).strip()):
                where.setdefault(p, col)
        except ValueError:
            continue
    for node in nodes:
        mine = [(p, where[p]) for p in node.ports if p in where]
        cols = sorted({c for _p, c in mine}, key=PORT_COLUMNS.index)
        tied = ",".join(str(p) for p in node.ports)
        if len(cols) > 1:
            out.append(CellIssue(
                r, cols[-1], "error",
                f"A short row ties ports {tied} into one node, but they are "
                f"{' and '.join(_COLUMN_LABEL[c] for c in cols)} of this net "
                "-- one node cannot be two probe ends.  Drop the short or "
                "change the net."))
            continue
    return out


def validate_nets(rows: Sequence[NetRow],
                  conn_rows: Sequence[ConnectionRow] = (),
                  nports: int = 0) -> list[CellIssue]:
    """
    Every cell's complaint, errors and warnings: the connection rows' first
    (table='conn', `conn_row_issues`), then the nets' in table order.

    The net rules are the probe rules restated per CELL so the table can
    mark them -- reserved and duplicate names, a port on both sides, a
    port twice in one net, 1-based numbering -- plus the table-level ones a
    per-net solve cannot see: a name is REQUIRED (no default is invented),
    and a differential net needs BOTH minus cells or neither.  Then, while
    the connection rows parse, the unified probe rules against them
    (`probe_rule_issues`): an IN+ / OUT+ port in a ground row is an error,
    a grounded '-' side a warning (solved, single-ended at that end), a
    probe port an open / element-to-GND row takes over a warning.  Two ROWS
    may share a port (see the loop).  Blank rows are skipped.  `nports <= 0`
    means no file is loaded, and the range check is then not made rather
    than failing every cell.
    """
    nports = int(nports or 0)
    conn_rows = list(conn_rows or ())
    issues: list[CellIssue] = conn_row_issues(conn_rows, nports)
    conn_ok = not any(i.is_error for i in issues)

    # ---- the rows
    names_seen: dict[str, int] = {}
    for r, row in enumerate(rows):
        if row is None or row.is_blank():
            continue
        name = str(row.name).strip()
        if not name:
            issues.append(CellIssue(r, "name", "error",
                                    "Name required -- give this net a name."))
        elif name.upper() in LEGACY_GROUP_NAMES:
            issues.append(CellIssue(
                r, "name", "error",
                f"'{name}' is reserved for the legacy A/B modes -- pick "
                "another name."))
        elif name in names_seen:
            issues.append(CellIssue(
                r, "name", "error",
                f"Duplicate net name '{name}' -- names must be unique."))
        else:
            names_seen[name] = r

        in_n = str(row.in_n).strip()
        out_n = str(row.out_n).strip()
        if bool(in_n) != bool(out_n):
            empty = "out_n" if in_n else "in_n"
            issues.append(CellIssue(
                r, empty, "error",
                "Differential needs both IN- and OUT- -- fill this one or "
                "clear the other."))

        in_row: dict[int, str] = {}
        parsed: dict[str, list[int]] = {}
        cells_ok = True
        for col in PORT_COLUMNS:
            text = str(getattr(row, col)).strip()
            if not text:
                if col in ("in_p", "out_p"):
                    issues.append(CellIssue(
                        r, col, "error",
                        f"{_COLUMN_LABEL[col]} is required -- the port the "
                        f"trace {'starts' if col == 'in_p' else 'ends'} at."))
                continue
            try:
                ports = _parse_cell(text)
            except ValueError:
                issues.append(CellIssue(r, col, "error",
                                        _cell_spec_error(col, text)))
                cells_ok = False
                continue
            parsed[col] = ports
            for p in ports:
                if p < 1:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p}: port numbers are 1-based."))
                    cells_ok = False
                    continue
                if nports > 0 and p > nports:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p} is beyond this file's {nports} ports."))
                    cells_ok = False
                    continue
                if p in in_row:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p} appears twice in this net ({_COLUMN_LABEL[in_row[p]]} "
                        f"and {_COLUMN_LABEL[col]}) -- a port can be on one "
                        "side only."))
                    cells_ok = False
                    continue
                in_row[p] = col
                # Two ROWS may share a port: every net is its own solve, so
                # DQ_P (1->3), DQ_N (2->4) and the pair DQ (1,2->3,4) sit side
                # by side in one table -- the comparison the workspace is for.
                # (The first build refused it, carried over from the coupling
                # solve, where one port cannot belong to two probes AT ONCE.)

        # The probe rules against the connection rows, once both parse: a
        # cell that does not is already red with its own reason, and a
        # connection table that does not parse grounds nothing yet.
        if conn_ok and cells_ok:
            issues.extend(_net_probe_issues(r, row, conn_rows))
            issues.extend(_net_merge_issues(r, row, conn_rows))

        if in_n and out_n:
            for col in ("in_p", "in_n", "out_p", "out_n"):
                if len(parsed.get(col, [])) > 1:
                    issues.append(CellIssue(
                        r, col, "warning",
                        f"{_COLUMN_LABEL[col]} ties several ports -- the pair "
                        "is solved, but the imbalance check is skipped (a "
                        "side of several ports has no four-port form)."))
    return issues


# ============================================================================
# Solving -- one net at a time
# ============================================================================

def _conn_key(conn_rows) -> tuple:
    """
    The connection rows as a signature component.  Only the rows that are
    IN the spec count (a blank or switched-off row changes no number).  A
    table of nothing but ground rows -- the stage-1 GND field's case -- is
    its grounded ports sorted and deduplicated, so '3,4', '4-3' and two
    rows '3' / '4' are one setting; anything else is the rows' stripped
    cells, in order, because row order can matter (last assignment wins).
    """
    live = [r for r in (conn_rows or ())
            if r is not None and not r.is_blank()
            and getattr(r, "enabled", True)]
    if all(str(r.kind).strip() == "ground" for r in live):
        try:
            return tuple(sorted({p for r in live
                                 for p in parse_port_range(str(r.ports))}))
        except ValueError:
            pass
    return tuple((str(r.kind).strip(), str(r.ports).strip(),
                  str(r.to).strip(), str(r.R).strip(), str(r.L).strip(),
                  str(r.C).strip(), str(getattr(r, "net", "")).strip())
                 for r in live)


def net_signature(row: NetRow, conn_rows=(), file_label: str = "",
                  freq_hz: float = float("nan")) -> tuple:
    """
    What a result is a result OF: the row's five cells (stripped), the
    connection rows (`_conn_key`: a ground-only table is its sorted port set
    -- '3,4' and '4-3' are one setting), the file's label and the marker
    frequency.  A result whose `signature` differs from the live row's is
    stale; the source impedance and load capacitance are deliberately NOT in
    it, because they change the bandwidth block only and `rebandwidth`
    answers them without a re-solve.
    """
    return (str(row.name).strip(), str(row.in_p).strip(),
            str(row.in_n).strip(), str(row.out_p).strip(),
            str(row.out_n).strip(), _conn_key(conn_rows), str(file_label),
            float(freq_hz))


def _loads(c_load_extra_f: float) -> tuple:
    """The sensitivity table's loads: the defaults plus the user's own."""
    extra = float(c_load_extra_f)
    if not (math.isfinite(extra) and extra >= 0.0):
        extra = DEFAULT_LOAD_EXTRA_F
    return tuple(sorted(set(tmod.DEFAULT_LOADS_F) | {extra}))


def _error_result(row: NetRow, message: str, conn_rows, file_label: str,
                  freq_hz: float, z_src_ohm: float,
                  c_load_extra_f: float) -> NetResult:
    return NetResult(
        name=str(row.name).strip(), status="error", error=str(message),
        differential=row.differential, model=None, reference=None,
        mode_conversion=None, mc_note="", freq_snap=None, freqs=None,
        Z2=None, bw_table=(), corners=(), model_band=(float("nan"), ""),
        z_src_ohm=float(z_src_ohm), c_load_extra_f=float(c_load_extra_f),
        signature=net_signature(row, conn_rows, file_label, freq_hz))


def _single_port(ports: Sequence[int]) -> Optional[int]:
    """The one port of a side, or None when the side ties several."""
    return int(ports[0]) if len(ports) == 1 else None


def _imbalance(Y, freqs, nports, conn_rows, k: int,
               in_p, in_n, out_p, out_n) -> tuple[Optional[float], str]:
    """
    The four-port mode conversion at ONE frequency, WITH the connection rows.

    Mirrors `cli._run_trace_model`: one solve, one frequency wide, the four
    single-ended probes under the same connection rows as the net (with
    ground-only rows the TerminationSet is the CLI's `--gnd` one, bit for
    bit -- `TestGroundRowIsTheOldGndField`).  A side of several ports has no
    four-port form and is skipped by name.
    """
    sides = [_single_port(s) for s in (in_p, in_n, out_p, out_n)]
    if None in sides:
        return (None, "imbalance check skipped -- a probe side ties more "
                      "than one port, which has no four-port form.")
    pi_p, pi_n, po_p, po_n = sides
    four = [MeasPortRow(f"{IN_NAME}_p", str(pi_p)),
            MeasPortRow(f"{IN_NAME}_n", str(pi_n)),
            MeasPortRow(f"{OUT_NAME}_p", str(po_p)),
            MeasPortRow(f"{OUT_NAME}_n", str(po_n))]
    try:
        term4 = build_terminations_rows(four, list(conn_rows), "",
                                        nports=nports)
        Z4, _n4, _w4 = compute_z_matrix(Y[k:k + 1], freqs[k:k + 1], term4)
        mc = tmod.mode_conversion_ratio(Z4[0])
    except ValueError as e:
        return (None, f"imbalance check skipped -- {e}")
    return (mc, f"differential nodes: {IN_NAME} = ({pi_p})-({pi_n}), "
                f"{OUT_NAME} = ({po_p})-({po_n})")


def _grounded_minus(row: NetRow, conn_rows) -> bool:
    """Does a connection row ground either '-' side (the amber probe rule)?
    Such an end is measured single-ended, so the net is not a pair."""
    return any(i.code == ISSUE_MINUS_GROUNDED
               for i in probe_rule_issues(net_mport_rows(row), conn_rows, ""))


def solve_net(freqs, Y, nports: int, row: NetRow,
              conn_rows: Sequence[ConnectionRow] = (),
              freq_hz: float = float("nan"), file_label: str = "",
              z_src_ohm: float = DEFAULT_SRC_OHM,
              c_load_extra_f: float = DEFAULT_LOAD_EXTRA_F) -> NetResult:
    """
    One net: its own `build_terminations_rows` + `compute_z_matrix`, the pi
    at the marker and at the bottom of the sweep, the imbalance check on a
    differential net, and the bandwidth block.  NEVER raises -- every failure
    is a `status='error'` result whose `error` names the cause.

    `Y` is the file's admittance, `(nf, nports, nports)`, i.e. `s_to_y` of
    its S; `freqs` its sweep in Hz; `conn_rows` the connection rows (port
    text 1-based, as typed).  With ground-only rows the arithmetic is the
    CLI's `--mode coupling --mport IN --mport OUT --trace-model IN,OUT
    --gnd ...` path, call for call, and `tests/test_tracenets.py` holds the
    two to `np.array_equal`.

    A '-' side a connection row grounds is folded into ground by the row
    path (the amber probe rule): that end is measured to GND, the net is
    solved as single-ended (`differential` False) and the imbalance check
    is not run.  An IN+ / OUT+ port in a ground row is refused with the
    probe rule's own message.
    """
    conn_rows = list(conn_rows or ())
    try:
        freqs = np.asarray(freqs, dtype=float)
        if freqs.ndim != 1 or freqs.size == 0:
            raise ValueError("The file has no frequency points.")
        name = str(row.name).strip()
        if not name:
            raise ValueError("Name required -- give this net a name.")
        if name.upper() in LEGACY_GROUP_NAMES:
            raise ValueError(f"'{name}' is reserved for the legacy A/B modes "
                             "-- pick another name.")
        cells: dict[str, list[int]] = {}
        for col in PORT_COLUMNS:
            text = str(getattr(row, col)).strip()
            try:
                cells[col] = _parse_cell(text)
            except ValueError:
                raise ValueError(_cell_spec_error(col, text)) from None
        if not cells["in_p"]:
            raise ValueError("IN+ is required -- the port the trace starts at.")
        if not cells["out_p"]:
            raise ValueError("OUT+ is required -- the port the trace ends at.")
        if bool(cells["in_n"]) != bool(cells["out_n"]):
            raise ValueError("Differential needs both IN- and OUT- -- fill "
                             "the empty one or clear the other.")
        # Port numbers before the builder sees them, in the net table's own
        # words (the builder would say the same thing about 'IN' / 'OUT').
        for col in PORT_COLUMNS:
            for p in cells[col]:
                if p < 1:
                    raise ValueError(f"Port {p}: port numbers are 1-based.")
        seen: dict[int, str] = {}
        for col in PORT_COLUMNS:
            for p in cells[col]:
                if p in seen:
                    raise ValueError(
                        f"Port {p} appears twice in this net "
                        f"({_COLUMN_LABEL[seen[p]]} and {_COLUMN_LABEL[col]}) "
                        "-- a port can be on one side only.")
                seen[p] = col
        # A short tying two ends, or a probe end to ground: refused in the
        # net's words before the solver can (0-based) or cannot (silently).
        merge = [i for i in _net_merge_issues(-1, row, conn_rows)
                 if i.is_error]
        if merge:
            raise ValueError(merge[0].message)
        differential = (bool(cells["in_n"])
                        and not _grounded_minus(row, conn_rows))

        term = build_terminations_rows(net_mport_rows(row), conn_rows, "",
                                       nports=int(nports))
        Zmat, names, warns = compute_z_matrix(Y, freqs, term)
        # By NAME, never by position: `resolve_meas_ports` orders the
        # measurement ports by lowest port number, so a net whose OUT end is
        # on the lower port comes back as (OUT, IN).
        i, j = names.index(IN_NAME), names.index(OUT_NAME)
        sel = np.array([i, j])
        Z2 = Zmat[:, sel[:, None], sel[None, :]]

        model = tmod.extract_pi_at(freqs, Z2, float(freq_hz), IN_NAME,
                                   OUT_NAME, differential)
        reference = tmod.extract_pi_at(freqs, Z2, float(freqs[0]), IN_NAME,
                                       OUT_NAME, differential)

        mc: Optional[float] = None
        note = ""
        if differential:
            k = int(np.argmin(np.abs(freqs - float(freq_hz))))
            mc, note = _imbalance(Y, freqs, int(nports), conn_rows, k,
                                  cells["in_p"], cells["in_n"],
                                  cells["out_p"], cells["out_n"])
        elif cells["in_n"]:
            note = ("imbalance check skipped -- a '-' side is in a ground "
                    "row, so that end is measured single-ended.")

        # The same two lines as the CLI's `_cli_marker`: the extractor's own
        # `freq_hz` overrides the re-derived point so one number is never
        # printed under two frequencies.
        snap = snap_to_grid(freqs, float(freq_hz))
        if snap.resolved:
            snap = replace(snap, actual_hz=float(model.freq_hz))

        bw_table = tuple(tmod.bandwidth_table(freqs, Z2, float(freq_hz),
                                              float(z_src_ohm),
                                              _loads(c_load_extra_f)))
        return NetResult(
            name=name, status="ok", error="", differential=differential,
            model=model, reference=reference, mode_conversion=mc,
            mc_note=note, freq_snap=snap, freqs=freqs, Z2=Z2,
            bw_table=bw_table, corners=tuple(tmod.branch_corners(model)),
            model_band=tmod.model_band_hz(freqs, Z2, IN_NAME, OUT_NAME,
                                          differential),
            z_src_ohm=float(z_src_ohm), c_load_extra_f=float(c_load_extra_f),
            signature=net_signature(row, conn_rows, file_label, freq_hz),
            warnings=tuple(warns or ()))
    except Exception as e:                                  # noqa: BLE001
        return _error_result(row, str(e) or type(e).__name__, conn_rows,
                             file_label, freq_hz, z_src_ohm, c_load_extra_f)


def solve_nets(freqs, Y, nports: int, rows: Sequence[NetRow],
               conn_rows: Sequence[ConnectionRow] = (),
               freq_hz: float = float("nan"), file_label: str = "",
               z_src_ohm: float = DEFAULT_SRC_OHM,
               c_load_extra_f: float = DEFAULT_LOAD_EXTRA_F) -> list[NetResult]:
    """
    One `NetResult` per NON-BLANK row, in row order.  A row the table rules
    reject (duplicate name, port used twice, an IN+ in a ground row, ...)
    becomes an error result carrying the first of its messages, and every
    other row is solved regardless -- one bad row does not stop the others.
    A connection row that does not parse is every net's error: none of them
    can be solved under a spec that is not one.
    """
    conn_rows = list(conn_rows or ())
    issues = validate_nets(rows, conn_rows, nports)
    table_errors: list[str] = [i.message for i in issues
                               if i.table == "conn" and i.is_error]
    row_errors: dict[int, str] = {}
    for i in issues:
        if i.table == "nets" and i.is_error and i.row not in row_errors:
            row_errors[i.row] = i.message

    out: list[NetResult] = []
    for r, row in enumerate(rows):
        if row is None or row.is_blank():
            continue
        why = table_errors[0] if table_errors else row_errors.get(r)
        if why:
            out.append(_error_result(row, why, conn_rows, file_label,
                                     freq_hz, z_src_ohm, c_load_extra_f))
            continue
        out.append(solve_net(freqs, Y, nports, row, conn_rows, freq_hz,
                             file_label, z_src_ohm, c_load_extra_f))
    return out


def retarget(res: NetResult, freq_hz: float, Y, nports: int, row: NetRow,
             conn_rows: Sequence[ConnectionRow] = (),
             file_label: str = "") -> NetResult:
    """
    The same net read at a NEW marker frequency, with NO sweep re-solve.

    `res.Z2` is the whole open-circuit sweep and does not depend on the
    marker, so the pi there is `extract_pi_at` on the cached matrix.  The one
    solve left is the differential imbalance check, which is one frequency
    wide by definition.  `tests/test_tracenets.py::TestRetarget` holds the
    result EQUAL to a fresh `solve_net` at the new frequency -- that is what
    lets the panel move the marker (field or drag) without a Calculate.
    Anything but an `ok` result comes back unchanged.
    """
    if res.status != "ok" or res.Z2 is None or res.freqs is None:
        return res
    try:
        f_hz = float(freq_hz)
        if not (math.isfinite(f_hz) and f_hz > 0.0):
            return res
        freqs = res.freqs
        conn_rows = list(conn_rows or ())
        model = tmod.extract_pi_at(freqs, res.Z2, f_hz, IN_NAME, OUT_NAME,
                                   res.differential)
        mc, note = res.mode_conversion, res.mc_note
        if res.differential:
            cells = {col: _parse_cell(str(getattr(row, col)).strip())
                     for col in PORT_COLUMNS}
            k = int(np.argmin(np.abs(freqs - f_hz)))
            mc, note = _imbalance(Y, freqs, int(nports), conn_rows, k,
                                  cells["in_p"], cells["in_n"],
                                  cells["out_p"], cells["out_n"])
        snap = snap_to_grid(freqs, f_hz)
        if snap.resolved:
            snap = replace(snap, actual_hz=float(model.freq_hz))
        bw_table = tuple(tmod.bandwidth_table(freqs, res.Z2, f_hz,
                                              res.z_src_ohm,
                                              _loads(res.c_load_extra_f)))
        return replace(res, model=model, mode_conversion=mc, mc_note=note,
                       freq_snap=snap, bw_table=bw_table,
                       corners=tuple(tmod.branch_corners(model)),
                       signature=net_signature(row, conn_rows, file_label,
                                               f_hz))
    except Exception:                                       # noqa: BLE001
        return res


def rebandwidth(res: NetResult, z_src_ohm: float,
                c_load_extra_f: float) -> NetResult:
    """
    The bandwidth block for a new source / load, from the SAME `freqs` / `Z2`
    the pi was read from.  NO re-solve: `compute_z_matrix` is not called, and
    `tests/test_tracenets.py::TestRebandwidth` patches it to prove that.  A
    result with no matrix (an error) only takes the new terminations.
    """
    z_src = float(z_src_ohm)
    if not (math.isfinite(z_src) and z_src >= 0.0):
        z_src = DEFAULT_SRC_OHM
    extra = float(c_load_extra_f)
    if not (math.isfinite(extra) and extra >= 0.0):
        extra = DEFAULT_LOAD_EXTRA_F
    if res.Z2 is None or res.freqs is None or res.model is None:
        return replace(res, z_src_ohm=z_src, c_load_extra_f=extra)
    table = tuple(tmod.bandwidth_table(res.freqs, res.Z2,
                                       res.model.requested_hz, z_src,
                                       _loads(extra)))
    return replace(res, bw_table=table, z_src_ohm=z_src, c_load_extra_f=extra)
