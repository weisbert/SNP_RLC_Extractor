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

THE IMBALANCE CHECK CARRIES THE GND PORTS
-----------------------------------------
The differential pi assumes common mode OPEN at both ends; the four-port
imbalance check says how much that assumption hides.  The old Trace model
window ran that check with NO ground ports while the CLI ran it with the
declared ones, so the two surfaces could print different mode-conversion
numbers for one file.  This module follows the CLI: the four single-ended
probes are solved under the same `gnd_ports` the net itself was, and
`tests/test_tracenets.py::TestImbalanceCarriesGnd` has a pair that reads
balanced with its reference ports open and imbalanced with them grounded.

Imports: `pkg_rlc.physics.core`, `pkg_rlc.physics.tracemodel` (L0) and
`pkg_rlc.model.trace.snap_to_grid` (L1).  No tkinter, no matplotlib, no App.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional, Sequence

import numpy as np

from pkg_rlc.physics import tracemodel as tmod
from pkg_rlc.physics.core import (
    LEGACY_GROUP_NAMES, build_terminations_coupling, compute_z_matrix,
    parse_port_range,
)
from pkg_rlc.model.trace import snap_to_grid

__all__ = [
    "NetRow", "CellIssue", "NetResult",
    "NET_COLUMNS", "PORT_COLUMNS", "IN_NAME", "OUT_NAME",
    "validate_nets", "solve_net", "solve_nets", "rebandwidth", "retarget",
    "net_signature", "gnd_ports_of",
]

#: The table's columns, in the order the panel shows them.  `CellIssue.column`
#: is one of these or 'gnd'.
NET_COLUMNS = ("name", "in_p", "in_n", "out_p", "out_n")
PORT_COLUMNS = ("in_p", "in_n", "out_p", "out_n")
_COLUMN_LABEL = {"in_p": "IN+", "in_n": "IN-", "out_p": "OUT+", "out_n": "OUT-"}

#: The internal measurement-port names every net is solved under.  They are
#: what `PiModel.in_name` / `out_name` carry, and what the imbalance check's
#: four single-ended probes are derived from (`IN_p`, `IN_n`, ...).  Neither
#: is in `LEGACY_GROUP_NAMES`, which `build_terminations_coupling` refuses.
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
    One complaint about one cell.  `row` is the index into the `rows` the
    validator was handed (blank rows keep their index, so the panel maps it
    straight onto its table); -1 is a field that is not a row, i.e. the GND
    entry.  `column` is one of `NET_COLUMNS` or 'gnd'.  `message` is English
    and names the fix.
    """
    row: int
    column: str
    severity: str           # 'error' | 'warning'
    message: str

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

def gnd_ports_of(gnd_text: str) -> list[int]:
    """The GND entry as 1-based ports.  Raises ValueError like the parser."""
    return parse_port_range(str(gnd_text or ""))


def _parse_cell(text: str) -> list[int]:
    return parse_port_range(str(text or ""))


def _cell_spec_error(column: str, text: str) -> str:
    return (f"{_COLUMN_LABEL.get(column, column)} '{text.strip()}' is not a "
            f"port spec -- use a port number, a list (1,3) or a range (6-14).")


def validate_nets(rows: Sequence[NetRow], gnd_text: str,
                  nports: int) -> list[CellIssue]:
    """
    Every cell's complaint, errors and warnings, in table order.

    The rules are `build_terminations_coupling`'s own refusals -- reserved and
    duplicate names, a port on both sides, a port claimed twice, a probe port
    that is also ground, 1-based numbering -- restated per CELL so the table
    can mark them, plus the table-level ones a per-net solve cannot see: a
    name is REQUIRED (no default is invented), and a differential net needs
    BOTH minus cells or neither.  Two ROWS may share a port (see the loop).  Blank rows are
    skipped.  `nports <= 0` means no file is loaded, and the range check is
    then not made rather than failing every cell.
    """
    issues: list[CellIssue] = []
    nports = int(nports or 0)

    # ---- the GND field
    gnd: set[int] = set()
    try:
        gnd_list = gnd_ports_of(gnd_text)
    except ValueError:
        issues.append(CellIssue(
            -1, "gnd", "error",
            f"GND '{str(gnd_text).strip()}' is not a port spec -- use port "
            "numbers, a list (1,3) or a range (6-14)."))
        gnd_list = []
    for p in gnd_list:
        if p < 1:
            issues.append(CellIssue(-1, "gnd", "error",
                                    f"GND port {p}: port numbers are 1-based."))
        elif nports > 0 and p > nports:
            issues.append(CellIssue(
                -1, "gnd", "error",
                f"GND port {p} is beyond this file's {nports} ports."))
        else:
            gnd.add(p)

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
        label = name or f"row {r + 1}"

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
                continue
            parsed[col] = ports
            for p in ports:
                if p < 1:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p}: port numbers are 1-based."))
                    continue
                if nports > 0 and p > nports:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p} is beyond this file's {nports} ports."))
                    continue
                if p in in_row:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p} appears twice in this net ({_COLUMN_LABEL[in_row[p]]} "
                        f"and {_COLUMN_LABEL[col]}) -- a port can be on one "
                        "side only."))
                    continue
                in_row[p] = col
                # Two ROWS may share a port: every net is its own solve, so
                # DQ_P (1->3), DQ_N (2->4) and the pair DQ (1,2->3,4) sit side
                # by side in one table -- the comparison the workspace is for.
                # (The first build refused it, carried over from the coupling
                # solve, where one port cannot belong to two probes AT ONCE.)
                if p in gnd:
                    issues.append(CellIssue(
                        r, col, "error",
                        f"Port {p} is also in GND -- drop it from one or the "
                        "other."))

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

def _normalise_gnd(gnd) -> tuple:
    if isinstance(gnd, str):
        try:
            return tuple(sorted(set(parse_port_range(gnd))))
        except ValueError:
            return ("?", gnd.strip())
    return tuple(sorted({int(p) for p in gnd}))


def net_signature(row: NetRow, gnd, file_label: str = "",
                  freq_hz: float = float("nan")) -> tuple:
    """
    What a result is a result OF: the row's five cells (stripped), the GND
    ports normalised (sorted, deduplicated -- '3,4' and '4-3' are one
    setting), the file's label and the marker frequency.  A result whose
    `signature` differs from the live row's is stale; the source impedance
    and load capacitance are deliberately NOT in it, because they change the
    bandwidth block only and `rebandwidth` answers them without a re-solve.
    """
    return (str(row.name).strip(), str(row.in_p).strip(),
            str(row.in_n).strip(), str(row.out_p).strip(),
            str(row.out_n).strip(), _normalise_gnd(gnd), str(file_label),
            float(freq_hz))


def _loads(c_load_extra_f: float) -> tuple:
    """The sensitivity table's loads: the defaults plus the user's own."""
    extra = float(c_load_extra_f)
    if not (math.isfinite(extra) and extra >= 0.0):
        extra = DEFAULT_LOAD_EXTRA_F
    return tuple(sorted(set(tmod.DEFAULT_LOADS_F) | {extra}))


def _error_result(row: NetRow, message: str, gnd_ports, file_label: str,
                  freq_hz: float, z_src_ohm: float,
                  c_load_extra_f: float) -> NetResult:
    return NetResult(
        name=str(row.name).strip(), status="error", error=str(message),
        differential=row.differential, model=None, reference=None,
        mode_conversion=None, mc_note="", freq_snap=None, freqs=None,
        Z2=None, bw_table=(), corners=(), model_band=(float("nan"), ""),
        z_src_ohm=float(z_src_ohm), c_load_extra_f=float(c_load_extra_f),
        signature=net_signature(row, gnd_ports, file_label, freq_hz))


def _single_port(ports: Sequence[int]) -> Optional[int]:
    """The one port of a side, or None when the side ties several."""
    return int(ports[0]) if len(ports) == 1 else None


def _imbalance(Y, freqs, nports, gnd_ports, k: int,
               in_p, in_n, out_p, out_n) -> tuple[Optional[float], str]:
    """
    The four-port mode conversion at ONE frequency, WITH the ground ports.

    Mirrors `cli._run_trace_model`: one solve, one frequency wide, the four
    single-ended probes under the same `gnd_ports` as the net.  A side of
    several ports has no four-port form and is skipped by name.
    """
    sides = [_single_port(s) for s in (in_p, in_n, out_p, out_n)]
    if None in sides:
        return (None, "imbalance check skipped -- a probe side ties more "
                      "than one port, which has no four-port form.")
    pi_p, pi_n, po_p, po_n = sides
    four = [(f"{IN_NAME}_p", [pi_p], []), (f"{IN_NAME}_n", [pi_n], []),
            (f"{OUT_NAME}_p", [po_p], []), (f"{OUT_NAME}_n", [po_n], [])]
    try:
        term4 = build_terminations_coupling(four, list(gnd_ports), (),
                                            nports=nports)
        Z4, _n4, _w4 = compute_z_matrix(Y[k:k + 1], freqs[k:k + 1], term4)
        mc = tmod.mode_conversion_ratio(Z4[0])
    except ValueError as e:
        return (None, f"imbalance check skipped -- {e}")
    return (mc, f"differential nodes: {IN_NAME} = ({pi_p})-({pi_n}), "
                f"{OUT_NAME} = ({po_p})-({po_n})")


def solve_net(freqs, Y, nports: int, row: NetRow, gnd_ports: Sequence[int],
              freq_hz: float, file_label: str = "",
              z_src_ohm: float = DEFAULT_SRC_OHM,
              c_load_extra_f: float = DEFAULT_LOAD_EXTRA_F) -> NetResult:
    """
    One net: its own `build_terminations_coupling` + `compute_z_matrix`, the
    pi at the marker and at the bottom of the sweep, the imbalance check on a
    differential net, and the bandwidth block.  NEVER raises -- every failure
    is a `status='error'` result whose `error` names the cause.

    `Y` is the file's admittance, `(nf, nports, nports)`, i.e. `s_to_y` of
    its S; `freqs` its sweep in Hz; `gnd_ports` 1-based.  The arithmetic is
    the CLI's `--mode coupling --mport IN --mport OUT --trace-model IN,OUT`
    path, call for call, and `tests/test_tracenets.py` holds the two to
    `np.array_equal`.
    """
    gnd_list = [int(p) for p in gnd_ports]
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
        differential = bool(cells["in_n"])

        mports = [(IN_NAME, cells["in_p"], cells["in_n"]),
                  (OUT_NAME, cells["out_p"], cells["out_n"])]
        term = build_terminations_coupling(mports, gnd_list, (),
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
            mc, note = _imbalance(Y, freqs, int(nports), gnd_list, k,
                                  cells["in_p"], cells["in_n"],
                                  cells["out_p"], cells["out_n"])

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
            signature=net_signature(row, gnd_list, file_label, freq_hz),
            warnings=tuple(warns or ()))
    except Exception as e:                                  # noqa: BLE001
        return _error_result(row, str(e) or type(e).__name__, gnd_list,
                             file_label, freq_hz, z_src_ohm, c_load_extra_f)


def solve_nets(freqs, Y, nports: int, rows: Sequence[NetRow], gnd_text: str,
               freq_hz: float, file_label: str = "",
               z_src_ohm: float = DEFAULT_SRC_OHM,
               c_load_extra_f: float = DEFAULT_LOAD_EXTRA_F) -> list[NetResult]:
    """
    One `NetResult` per NON-BLANK row, in row order.  A row the table rules
    reject (duplicate name, port used twice, a bad GND entry, ...) becomes an
    error result carrying the first of its messages, and every other row is
    solved regardless -- one bad row does not stop the others.
    """
    issues = validate_nets(rows, gnd_text, nports)
    table_errors: list[str] = [i.message for i in issues
                               if i.row == -1 and i.is_error]
    row_errors: dict[int, str] = {}
    for i in issues:
        if i.row >= 0 and i.is_error and i.row not in row_errors:
            row_errors[i.row] = i.message
    try:
        gnd_list = gnd_ports_of(gnd_text)
    except ValueError:
        gnd_list = []
    gnd_for_sig = gnd_text if table_errors else gnd_list

    out: list[NetResult] = []
    for r, row in enumerate(rows):
        if row is None or row.is_blank():
            continue
        why = table_errors[0] if table_errors else row_errors.get(r)
        if why:
            out.append(_error_result(row, why, gnd_for_sig, file_label,
                                     freq_hz, z_src_ohm, c_load_extra_f))
            continue
        out.append(solve_net(freqs, Y, nports, row, gnd_list, freq_hz,
                             file_label, z_src_ohm, c_load_extra_f))
    return out


def retarget(res: NetResult, freq_hz: float, Y, nports: int, row: NetRow,
             gnd_ports: Sequence[int], file_label: str = "") -> NetResult:
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
        gnd_list = [int(p) for p in gnd_ports]
        model = tmod.extract_pi_at(freqs, res.Z2, f_hz, IN_NAME, OUT_NAME,
                                   res.differential)
        mc, note = res.mode_conversion, res.mc_note
        if res.differential:
            cells = {col: _parse_cell(str(getattr(row, col)).strip())
                     for col in PORT_COLUMNS}
            k = int(np.argmin(np.abs(freqs - f_hz)))
            mc, note = _imbalance(Y, freqs, int(nports), gnd_list, k,
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
                       signature=net_signature(row, gnd_list, file_label,
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
