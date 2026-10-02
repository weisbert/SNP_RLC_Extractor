"""
The Trace model workspace's engine: `pkg_rlc.services.tracenets` and the
summary table in `pkg_rlc.present.tracemodel_report`.

No Tk root, no tkinter import -- this module belongs in `FAST_MODULES`, and
`TestNoTk` pins the property the list is about.

WHAT THESE TESTS ARE FOR
------------------------
The engine claims to be the CLI's `--trace-model` path, call for call, so a
net typed into the workspace's table and the same ports typed on a command
line come out as ONE set of numbers.  `TestMatchesTheCli` holds the two to
`np.array_equal` on `Z2` and on every branch of the `PiModel`, and to text
equality on the printed report, for a single-ended net, a differential net
and a net solved with ground ports.

Two of the tests exist because of a measured divergence:

  * `TestPortOrder` -- `compute_z_matrix` orders its measurement ports by
    LOWEST PORT NUMBER, so a net whose OUT end sits on the lower port comes
    back as (OUT, IN).  Reading the matrix positionally swaps the ends; on a
    symmetric trace nothing shows.  The test uses an ASYMMETRIC setup (one
    end's neighbour grounded) and declares OUT below IN.
  * `TestImbalanceCarriesGnd` -- the old Trace model window ran the four-port
    imbalance check with NO ground ports while the CLI ran it with them.  A
    pair whose two lines see different capacitance to a reference node is
    balanced with that node OPEN and imbalanced with it GROUNDED, so the
    number itself says which of the two the engine did.

Both were mutation-checked: with `names.index(IN_NAME)` replaced by `0` the
first fails, and with `gnd_ports` dropped from the four-port solve the second
fails.

STAGE 3: THE GND FIELD IS CONNECTION ROWS.  The engine now takes the RLC
editor's `ConnectionRow`s for the other ports.  Every CLI-equality test above
keeps its assertions and passes its GND as `ground_rows(...)`, and
`TestGroundRowIsTheOldGndField` is the measurement that made the switch safe:
over every fixture, every IN/OUT assignment and every grounded subset (368
cases) the old `build_terminations_coupling(..., gnd)` and a ground row give
`np.array_equal` matrices.  The per-net probe rules (an IN+ / OUT+ in a ground
row is red, a grounded '-' side amber and solved single-ended), the
connection rows' own cells, and the two short-row cases the probe rules
cannot see are under `TestValidation`.
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import unittest
from dataclasses import astuple, replace
from unittest import mock

import numpy as np

from pkg_rlc.physics import tracemodel as tm
from pkg_rlc.physics.core import (
    build_terminations_coupling, compute_z_matrix, parse_touchstone, s_to_y,
)
from pkg_rlc.present import tracemodel_report as tmr
from pkg_rlc.services import tracenets as tn
from pkg_rlc.physics.core import ConnectionRow, build_terminations_rows, MeasPortRow
from pkg_rlc.services.tracenets import CellIssue, NetRow, ground_rows

FIX = "tests/fixtures"
F_TARGET = 1e9


def _load(name):
    ts = parse_touchstone(f"{FIX}/{name}")
    return ts, s_to_y(ts.s, ts.z0)


def _cli_z2(ts, Y, in_p, in_n, out_p, out_n, gnd=()):
    """
    The CLI's own arithmetic for `--mport IN --mport OUT --trace-model IN,OUT`:
    one `build_terminations_coupling`, one `compute_z_matrix`, the 2x2 picked
    by NAME.  Written out here rather than imported so the test does not
    share the code it is checking.
    """
    term = build_terminations_coupling(
        [("IN", list(in_p), list(in_n)), ("OUT", list(out_p), list(out_n))],
        list(gnd), (), nports=ts.nports)
    Zmat, names, _w = compute_z_matrix(Y, ts.freqs, term)
    i, j = names.index("IN"), names.index("OUT")
    sel = np.array([i, j])
    return Zmat[:, sel[:, None], sel[None, :]]


def _branch_tuple(b):
    return (b.name, b.Z, b.R_ohm, b.L_henry, b.C_farad, b.Q)


def _same(a, b) -> bool:
    """Field-wise equality that treats NaN as equal to NaN (dataclass == cannot)."""
    ta, tb = astuple(a), astuple(b)
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if isinstance(x, float) and isinstance(y, float):
            if math.isnan(x) and math.isnan(y):
                continue
        if x != y:
            return False
    return True


def _same_list(a, b) -> bool:
    return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))


def _errors(issues, column=None, row=None):
    return [i for i in issues if i.severity == "error"
            and (column is None or i.column == column)
            and (row is None or i.row == row)]


def _ok(name="n", in_p="1", in_n="", out_p="2", out_n=""):
    return NetRow(name, in_p, in_n, out_p, out_n)


# ============================================================================
# Validation -- rule by rule
# ============================================================================

class TestValidation(unittest.TestCase):

    def test_a_clean_table_has_no_issues(self):
        rows = [_ok("x", "1", "", "2", ""), _ok("y", "3", "", "4", "")]
        self.assertEqual(tn.validate_nets(rows, [], 4), [])

    def test_blank_rows_are_ignored(self):
        rows = [NetRow(), _ok("x", "1", "", "2", ""), NetRow("", "  ", "", "", "")]
        self.assertEqual(tn.validate_nets(rows, [], 4), [])
        self.assertTrue(NetRow().is_blank())
        self.assertFalse(NetRow(name="x").is_blank())

    def test_name_is_required_and_no_default_is_invented(self):
        issues = tn.validate_nets([_ok("", "1", "", "2", "")], [], 4)
        errs = _errors(issues, "name", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("Name required", errs[0].message)

    def test_duplicate_names(self):
        rows = [_ok("dq", "1", "", "2", ""), _ok("dq", "3", "", "4", "")]
        issues = tn.validate_nets(rows, [], 4)
        self.assertEqual(_errors(issues, "name", 0), [])
        errs = _errors(issues, "name", 1)
        self.assertEqual(len(errs), 1)
        self.assertIn("Duplicate", errs[0].message)

    def test_a_and_b_are_reserved_case_insensitively(self):
        for name in ("A", "b", "a", "B"):
            issues = tn.validate_nets([_ok(name, "1", "", "2", "")], [], 4)
            errs = _errors(issues, "name", 0)
            self.assertEqual(len(errs), 1, name)
            self.assertIn("reserved", errs[0].message)

    def test_a_port_spec_that_does_not_parse(self):
        issues = tn.validate_nets([_ok("n", "x", "", "2", "")], [], 4)
        errs = _errors(issues, "in_p", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("not a port spec", errs[0].message)
        issues = tn.validate_nets([_ok("n", "1", "", "2:3", "")], [], 4)
        self.assertEqual(len(_errors(issues, "out_p", 0)), 1)

    def test_a_port_beyond_nports(self):
        issues = tn.validate_nets([_ok("n", "1", "", "7", "")], [], 4)
        errs = _errors(issues, "out_p", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("beyond this file's 4 ports", errs[0].message)

    def test_no_file_loaded_skips_the_range_check(self):
        self.assertEqual(tn.validate_nets([_ok("n", "1", "", "70", "")], [], 0), [])

    def test_port_numbers_are_one_based(self):
        issues = tn.validate_nets([_ok("n", "0", "", "2", "")], [], 4)
        self.assertEqual(len(_errors(issues, "in_p", 0)), 1)

    def test_a_port_repeated_inside_a_row(self):
        issues = tn.validate_nets([_ok("n", "1", "", "1", "")], [], 4)
        errs = _errors(issues, "out_p", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("twice in this net", errs[0].message)
        self.assertEqual(_errors(issues, "in_p", 0), [])

    def test_rows_may_share_ports(self):
        """Each net is its own solve: the two single-ended halves and the
        differential pair of one PN signal belong in one table."""
        rows = [_ok("DQ_P", "1", "", "3", ""), _ok("DQ_N", "2", "", "4", ""),
                _ok("DQ", "1", "2", "3", "4")]
        self.assertEqual(tn.validate_nets(rows, [], 4), [])
        ts, Y = _load("diff_pair_4port.s4p")
        out = tn.solve_nets(ts.freqs, Y, 4, rows, [], F_TARGET)
        self.assertEqual([r.status for r in out], ["ok", "ok", "ok"])
        for res, row in zip(out, rows):
            alone = tn.solve_net(ts.freqs, Y, 4, row, [], F_TARGET)
            self.assertTrue(np.array_equal(res.Z2, alone.Z2))

    def test_a_plus_port_in_a_ground_row_is_red_on_that_cell(self):
        """The unified probe rule (design 3.3): a '+' port at GND is refused."""
        issues = tn.validate_nets([_ok("n", "1", "", "2", "")],
                                  ground_rows("2,3"), 4)
        errs = _errors(issues, "out_p", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("ground row", errs[0].message)
        self.assertEqual(errs[0].table, "nets")
        issues = tn.validate_nets([_ok("n", "1", "", "2", "")],
                                  [ConnectionRow("vdd", "1")], 4)
        self.assertEqual(len(_errors(issues, "in_p", 0)), 1)

    def test_a_grounded_minus_side_is_amber_and_solved_single_ended(self):
        row = _ok("d", "1", "2", "3", "4")
        issues = tn.validate_nets([row], ground_rows("2"), 4)
        self.assertEqual(_errors(issues), [])
        self.assertEqual([(i.column, i.severity) for i in issues],
                         [("in_n", "warning")])
        self.assertIn("single-ended", issues[0].message)
        ts, Y = _load("diff_pair_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, 4, row, ground_rows("2"), F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        self.assertFalse(res.differential)
        self.assertIsNone(res.mode_conversion)
        self.assertIn("single-ended", res.mc_note)
        # What it computes is what the row path says it means: IN's '-' side
        # folded into ground.
        term = build_terminations_rows(
            [MeasPortRow("IN", "1"), MeasPortRow("OUT", "3", "4")],
            ground_rows("2"), "", nports=4)
        from pkg_rlc.physics.core import compute_z_matrix as czm
        Zmat, names, _w = czm(Y, ts.freqs, term)
        i, j = names.index("IN"), names.index("OUT")
        self.assertTrue(np.array_equal(
            res.Z2, Zmat[:, np.array([i, j])[:, None], np.array([i, j])[None, :]]))

    def test_an_open_row_over_a_probe_port_is_amber(self):
        issues = tn.validate_nets([_ok("n", "1", "", "2", "")],
                                  [ConnectionRow("open", "2")], 4)
        self.assertEqual([(i.column, i.severity) for i in issues],
                         [("out_p", "warning")])

    def test_a_short_tying_two_ends_is_refused_on_the_cell_1_based(self):
        """The solver refuses this too, but at solve time and naming
        0-based ports ('Ports [1, 2] merged via short'); the cell says it
        first, 1-based."""
        row = _ok("d", "1", "2", "3", "4")
        conn = [ConnectionRow("short", "2,3")]
        errs = _errors(tn.validate_nets([row], conn, 4))
        self.assertEqual([(e.column, e.row) for e in errs], [("out_p", 0)])
        self.assertIn("ties ports 2,3", errs[0].message)
        ts, Y = _load("diff_pair_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, 4, row, conn, F_TARGET)
        self.assertEqual(res.status, "error")
        self.assertIn("ties ports 2,3", res.error)

    def test_a_probe_shorted_to_a_grounded_port_is_refused(self):
        """The solver's merge keeps the probe and drops the ground with no
        word; the node is at 0 V, the same case as a '+' port in a ground
        row."""
        row = _ok("p", "1", "", "3", "")
        conn = [ConnectionRow("short", "1,4"), ConnectionRow("ground", "4")]
        errs = _errors(tn.validate_nets([row], conn, 4))
        self.assertEqual([e.column for e in errs], ["in_p"])
        self.assertIn("grounded port 4", errs[0].message)
        ts, Y = _load("diff_pair_4port.s4p")
        self.assertEqual(tn.solve_net(ts.freqs, Y, 4, row, conn,
                                      F_TARGET).status, "error")
        # A short among ports the net does not probe is fine.
        self.assertEqual(tn.validate_nets(
            [row], [ConnectionRow("short", "2,4"),
                    ConnectionRow("ground", "4")], 4), [])

    def test_connection_rows_are_checked_on_their_own_cells(self):
        rows = [_ok()]
        issues = tn.validate_nets(rows, ground_rows("x"), 4)
        self.assertEqual([(i.table, i.row, i.column) for i in issues],
                         [("conn", 0, "ports")])
        self.assertNotIn("Line 1", issues[0].message)
        issues = tn.validate_nets(rows, [ConnectionRow("ground", "3"),
                                         ConnectionRow("ground", "9")], 4)
        self.assertEqual([(i.table, i.row) for i in _errors(issues)],
                         [("conn", 1)])
        self.assertIn("outside this file's 4 ports", issues[0].message)
        self.assertEqual(tn.validate_nets(rows, ground_rows("3-4"), 4), [])
        # A bad VALUE is red on its own cell, not on the port.
        issues = tn.conn_row_issues([ConnectionRow("rlc_gnd", "3", R="5 m"),
                                     ConnectionRow("rlc_gnd", "4", L="abc")], 4)
        self.assertEqual([(i.row, i.column) for i in issues],
                         [(0, "R"), (1, "L")])
        # A row naming a node an EARLIER row created parses, as in the solve.
        self.assertEqual(tn.conn_row_issues(
            [ConnectionRow("short", "3,4", net="tap"),
             ConnectionRow("ground", "tap")], 4), [])
        # The two strip warnings: no Port, and an element with no value.
        issues = tn.conn_row_issues([ConnectionRow("rlc_gnd", "", R="5"),
                                     ConnectionRow("rlc_gnd", "3")], 4)
        self.assertEqual([(i.row, i.column, i.severity) for i in issues],
                         [(0, "ports", "warning"), (1, "R", "warning")])
        # A switched-off row is not in the spec and is not checked; no file
        # (nports 0) skips the range check.
        self.assertEqual(tn.conn_row_issues(
            [ConnectionRow("ground", "x", enabled=False)], 4), [])
        self.assertEqual(tn.conn_row_issues([ConnectionRow("ground", "70")], 0),
                         [])

    def test_ground_rows_reads_the_old_gnd_field(self):
        self.assertEqual(ground_rows(""), [])
        self.assertEqual(ground_rows("  "), [])
        self.assertEqual(ground_rows([]), [])
        self.assertEqual(ground_rows("4-3"), [ConnectionRow("ground", "4-3")])
        self.assertEqual(ground_rows("3, 4"), [ConnectionRow("ground", "3-4")])
        self.assertEqual(ground_rows([5, 6]), [ConnectionRow("ground", "5,6")])
        self.assertEqual(ground_rows("x y"), [ConnectionRow("ground", "x y")])

    def test_differential_needs_both_minus_cells_or_neither(self):
        issues = tn.validate_nets([_ok("n", "1", "2", "3", "")], [], 4)
        errs = _errors(issues, "out_n", 0)
        self.assertEqual(len(errs), 1)
        self.assertIn("both IN- and OUT-", errs[0].message)
        self.assertEqual(_errors(issues, "in_n", 0), [])
        issues = tn.validate_nets([_ok("n", "1", "", "3", "4")], [], 4)
        self.assertEqual(len(_errors(issues, "in_n", 0)), 1)
        self.assertEqual(_errors(issues, "out_n", 0), [])
        self.assertEqual(tn.validate_nets([_ok("n", "1", "2", "3", "4")], [], 4), [])

    def test_in_plus_and_out_plus_are_required(self):
        issues = tn.validate_nets([_ok("n", "", "", "", "")], [], 4)
        self.assertEqual(sorted(i.column for i in _errors(issues)),
                         ["in_p", "out_p"])

    def test_a_multi_port_side_on_a_pair_is_a_warning_not_an_error(self):
        issues = tn.validate_nets([_ok("n", "1,2", "3", "4", "5")], [], 6)
        self.assertEqual(_errors(issues), [])
        self.assertEqual([(i.column, i.severity) for i in issues],
                         [("in_p", "warning")])
        self.assertIn("imbalance check is skipped", issues[0].message)

    def test_every_message_is_english_and_names_the_fix(self):
        rows = [_ok("", "x", "9", "1", ""), _ok("A", "1", "", "1", "")]
        issues = tn.validate_nets(rows, ground_rows("q"), 4)
        self.assertGreaterEqual(len(issues), 5)
        for i in issues:
            self.assertIsInstance(i, CellIssue)
            self.assertTrue(i.message.isascii(), i.message)
            self.assertIn(i.severity, ("error", "warning"))


# ============================================================================
# The numbers are the CLI's
# ============================================================================

class TestMatchesTheCli(unittest.TestCase):

    def _cli_text(self, path, mports, gnd=""):
        import pkg_rlc.frontend.cli as cli
        argv = ["--cli", path, "--mode", "coupling", "--freq", "1.0",
                "--trace-model", "IN,OUT"]
        for m in mports:
            argv += ["--mport", m]
        if gnd:
            argv += ["--gnd", gnd]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        self.assertEqual(rc, 0, err.getvalue())
        return out.getvalue()

    def _my_text(self, res):
        lines = tmr.pi_report_lines(
            res.model, freq_snap=res.freq_snap, reference=res.reference,
            mode_conversion=res.mode_conversion, port_note=res.mc_note)
        lines += tmr.bandwidth_lines(list(res.bw_table), list(res.corners),
                                     res.model_band, float(res.freqs[-1]))
        return lines

    def _assert_same_report(self, res, cli_out):
        mine = self._my_text(res)
        cli_lines = cli_out.split("\n")
        k = cli_lines.index(mine[1])          # 'Trace model (...)'
        self.assertEqual(cli_lines[k:k + len(mine) - 1], mine[1:])

    def _assert_same_model(self, res, Z2_cli, differential):
        self.assertEqual(res.status, "ok", res.error)
        self.assertTrue(np.array_equal(res.Z2, Z2_cli))
        ts_freqs = res.freqs
        m_cli = tm.extract_pi_at(ts_freqs, Z2_cli, F_TARGET, "IN", "OUT",
                                 differential)
        for a, b in zip(res.model.branches, m_cli.branches):
            self.assertEqual(_branch_tuple(a), _branch_tuple(b))
        self.assertEqual(res.model.freq_hz, m_cli.freq_hz)
        self.assertEqual(res.model.reciprocity, m_cli.reciprocity)
        ref_cli = tm.extract_pi_at(ts_freqs, Z2_cli, float(ts_freqs[0]),
                                   "IN", "OUT", differential)
        for a, b in zip(res.reference.branches, ref_cli.branches):
            self.assertEqual(_branch_tuple(a), _branch_tuple(b))

    def test_single_ended_on_pi_2port(self):
        ts, Y = _load("pi_2port.s2p")
        res = tn.solve_net(ts.freqs, Y, ts.nports, _ok("sig", "1", "", "2", ""),
                           [], F_TARGET, "pi_2port.s2p")
        self._assert_same_model(res, _cli_z2(ts, Y, [1], [], [2], []), False)
        self.assertFalse(res.differential)
        self.assertIsNone(res.mode_conversion)
        self._assert_same_report(res, self._cli_text(
            f"{FIX}/pi_2port.s2p", ["IN = 1", "OUT = 2"]))
        # The fixture's own header: R_series 1 ohm, L_series 1 nH, 1 fF/end.
        self.assertAlmostEqual(res.model.series.R_ohm, 1.0, places=6)
        self.assertAlmostEqual(res.model.series.L_henry / 1e-9, 1.0, places=5)
        self.assertAlmostEqual(res.model.shunt_in.C_farad / 1e-15, 1.0, places=4)

    def test_differential_on_diff_pair_4port(self):
        ts, Y = _load("diff_pair_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, ts.nports,
                           _ok("pair", "1", "2", "3", "4"), [], F_TARGET,
                           "diff_pair_4port.s4p")
        self._assert_same_model(res, _cli_z2(ts, Y, [1], [2], [3], [4]), True)
        self.assertTrue(res.differential)
        self.assertIsNotNone(res.mode_conversion)
        self.assertLess(res.mode_conversion, tmr.MODE_CONVERSION_WARN)
        self.assertEqual(res.mc_note,
                         "differential nodes: IN = (1)-(2), OUT = (3)-(4)")
        self._assert_same_report(res, self._cli_text(
            f"{FIX}/diff_pair_4port.s4p", ["IN = 1 / 2", "OUT = 3 / 4"]))
        self.assertAlmostEqual(res.model.series.L_henry / 1e-9, 8.0, places=4)

    def test_mode_conversion_is_the_clis_number_exactly(self):
        ts, Y = _load("diff_pair_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, ts.nports,
                           _ok("pair", "1", "2", "3", "4"), [], F_TARGET)
        k = int(np.argmin(np.abs(ts.freqs - F_TARGET)))
        four = [("IN_p", [1], []), ("IN_n", [2], []),
                ("OUT_p", [3], []), ("OUT_n", [4], [])]
        term4 = build_terminations_coupling(four, [], (), nports=ts.nports)
        Z4, _n, _w = compute_z_matrix(Y[k:k + 1], ts.freqs[k:k + 1], term4)
        self.assertEqual(res.mode_conversion, tm.mode_conversion_ratio(Z4[0]))

    def test_single_ended_with_gnd_on_decap_4port(self):
        ts, Y = _load("decap_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, ts.nports, _ok("sig", "1", "", "2", ""),
                           ground_rows([3, 4]), F_TARGET, "decap_4port.s4p")
        self._assert_same_model(res, _cli_z2(ts, Y, [1], [], [2], [], gnd=[3, 4]),
                                False)
        self._assert_same_report(res, self._cli_text(
            f"{FIX}/decap_4port.s4p", ["IN = 1", "OUT = 2"], gnd="3,4"))
        # And the ground ports changed something: solved OPEN, the same net
        # is a different matrix.
        open_res = tn.solve_net(ts.freqs, Y, ts.nports,
                                _ok("sig", "1", "", "2", ""), [], F_TARGET)
        self.assertEqual(open_res.status, "ok")
        self.assertEqual(res.signature[5], (3, 4))
        self.assertEqual(open_res.signature[5], ())

    def test_solve_nets_goes_through_the_same_path(self):
        ts, Y = _load("decap_4port.s4p")
        rows = [NetRow(), _ok("sig", "1", "", "2", "")]
        out = tn.solve_nets(ts.freqs, Y, ts.nports, rows, ground_rows("4-3"), F_TARGET,
                            "decap_4port.s4p")
        self.assertEqual(len(out), 1)
        self.assertTrue(np.array_equal(
            out[0].Z2, _cli_z2(ts, Y, [1], [], [2], [], gnd=[3, 4])))
        # '4-3' and '3,4' are one GND setting.
        self.assertEqual(out[0].signature[5], (3, 4))

    def test_the_bandwidth_table_is_the_clis(self):
        ts, Y = _load("pi_2port.s2p")
        res = tn.solve_net(ts.freqs, Y, ts.nports, _ok("sig", "1", "", "2", ""),
                           [], F_TARGET)
        Z2 = _cli_z2(ts, Y, [1], [], [2], [])
        table = tm.bandwidth_table(ts.freqs, Z2, F_TARGET, 1.0,
                                   tuple(sorted(set(tm.DEFAULT_LOADS_F))))
        self.assertTrue(_same_list(res.bw_table, table))
        self.assertTrue(_same_list(res.corners, tm.branch_corners(res.model)))
        self.assertEqual(res.model_band,
                         tm.model_band_hz(ts.freqs, Z2, "IN", "OUT", False))

    def test_the_marker_snap_is_the_clis(self):
        ts, Y = _load("pi_2port.s2p")
        off = 1.0123e9                       # between two grid points
        res = tn.solve_net(ts.freqs, Y, ts.nports, _ok(), [], off)
        self.assertEqual(res.freq_snap.requested_hz, off)
        self.assertEqual(res.freq_snap.actual_hz, res.model.freq_hz)
        self.assertEqual(res.model.requested_hz, off)


# ============================================================================
# A ground row IS the old GND field, bit for bit
# ============================================================================

def _touchstone_fixtures():
    import glob
    import os
    out = []
    for path in sorted(glob.glob(f"{FIX}/*")):
        if os.path.isdir(path) or path.endswith((".npz", ".json")):
            continue
        out.append(path)
    return out


def _nets_of(nports):
    """Every IN/OUT assignment: single-ended on every ordered port pair,
    and differential on every ordered quadruple."""
    import itertools
    ports = range(1, nports + 1)
    nets = [([a], [], [b], []) for a, b in itertools.permutations(ports, 2)]
    if nports >= 4:
        nets += [([q[0]], [q[1]], [q[2]], [q[3]])
                 for q in itertools.permutations(ports, 4)]
    return nets


def _subsets(ports):
    import itertools
    ports = list(ports)
    return [list(c) for k in range(len(ports) + 1)
            for c in itertools.combinations(ports, k)]


class TestGroundRowIsTheOldGndField(unittest.TestCase):
    """
    Stage 3 replaced the GND field with connection rows.  The promise: a
    table of ground rows computes EXACTLY what the GND field did.  Measured
    over every Touchstone fixture with two or more ports, every IN/OUT
    assignment (single-ended on each ordered pair, differential on each
    ordered quadruple), and every subset of the remaining ports grounded:
    the old arithmetic (`build_terminations_coupling(..., gnd)`, the CLI's
    `--gnd`) and the new (`ground_rows(gnd)` through the row path) give
    `np.array_equal` matrices.  368 cases.  Mutation-checked: with the
    ground row's ports emitted one short (`ports[:-1]`) the matrix test
    fails.
    """

    @classmethod
    def setUpClass(cls):
        cls.files = []
        for path in _touchstone_fixtures():
            ts = parse_touchstone(path)
            if ts.nports >= 2:
                cls.files.append((path, ts, s_to_y(ts.s, ts.z0)))

    def test_there_is_something_to_compare(self):
        self.assertGreaterEqual(len(self.files), 6)
        self.assertTrue(any(ts.nports >= 4 for _p, ts, _y in self.files))

    def test_every_fixture_every_net_every_gnd_set(self):
        n_cases = 0
        for path, ts, Y in self.files:
            n = ts.nports
            # Every 20th point (5 to 21 per file): the solve is per
            # frequency, so a subsample is the same comparison made fewer
            # times -- 9.7 s on the whole sweeps, too slow for FAST_MODULES.
            # The full sweeps are compared in the test below.
            fs, Ys = ts.freqs[::20], Y[::20]
            for ip, im, op, om in _nets_of(n):
                used = set(ip + im + op + om)
                for gnd in _subsets(p for p in range(1, n + 1)
                                    if p not in used):
                    old = build_terminations_coupling(
                        [("IN", ip, im), ("OUT", op, om)], gnd, (), nports=n)
                    new = build_terminations_rows(
                        tn.net_mport_rows(_ok("n", *(",".join(map(str, s))
                                                     for s in (ip, im, op, om)))),
                        ground_rows(gnd), "", nports=n)
                    Z_old, n_old, _w = compute_z_matrix(Ys, fs, old)
                    Z_new, n_new, _w = compute_z_matrix(Ys, fs, new)
                    with self.subTest(path=path, net=(ip, im, op, om), gnd=gnd):
                        self.assertEqual(n_old, n_new)
                        self.assertTrue(np.array_equal(Z_old, Z_new,
                                                       equal_nan=True))
                    n_cases += 1
        self.assertEqual(n_cases, 368)

    def test_the_old_gnd_string_through_solve_net(self):
        """The engine end to end: an old session's GND STRING read as a
        ground row gives the CLI's 2x2 and its imbalance number exactly."""
        for path, ts, Y in self.files:
            n = ts.nports
            nets = [_ok("se", "1", "", "2", "")]
            if n >= 4:
                nets.append(_ok("df", "1", "2", "3", "4"))
            for row in nets:
                used = {int(c) for c in (row.in_p, row.in_n, row.out_p,
                                         row.out_n) if c}
                rest = [p for p in range(1, n + 1) if p not in used]
                for gnd in ([], rest[:1], rest):
                    text = ",".join(map(str, gnd))
                    with self.subTest(path=path, net=row.name, gnd=text):
                        res = tn.solve_net(ts.freqs, Y, n, row,
                                           ground_rows(text), F_TARGET)
                        self.assertEqual(res.status, "ok", res.error)
                        cells = [[int(c)] if c else [] for c in
                                 (row.in_p, row.in_n, row.out_p, row.out_n)]
                        self.assertTrue(np.array_equal(
                            res.Z2, _cli_z2(ts, Y, *cells, gnd=gnd),
                            equal_nan=True))
                        if row.in_n:
                            k = int(np.argmin(np.abs(ts.freqs - F_TARGET)))
                            four = [("IN_p", [1], []), ("IN_n", [2], []),
                                    ("OUT_p", [3], []), ("OUT_n", [4], [])]
                            t4 = build_terminations_coupling(four, gnd, (),
                                                             nports=n)
                            Z4, _n, _w = compute_z_matrix(
                                Y[k:k + 1], ts.freqs[k:k + 1], t4)
                            mc = tm.mode_conversion_ratio(Z4[0])
                            self.assertTrue(
                                res.mode_conversion == mc
                                or (math.isnan(mc)
                                    and math.isnan(res.mode_conversion)))


# ============================================================================
# The port-order caveat
# ============================================================================

def _asym_pi_Y(freqs):
    """A 2-port pi with UNEQUAL shunts: 40 fF at port 1, 100 fF at port 2."""
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    ys = 1.0 / (50.0 + 1j * w * 2e-9)
    Y = np.zeros((len(w), 2, 2), dtype=complex)
    Y[:, 0, 0] = ys + 1j * w * 40e-15 + 1e-9
    Y[:, 1, 1] = ys + 1j * w * 100e-15 + 1e-9
    Y[:, 0, 1] = Y[:, 1, 0] = -ys
    return Y


class TestPortOrder(unittest.TestCase):
    """
    `resolve_meas_ports` orders by lowest port number.  With OUT on port 1
    and IN on port 3, the matrix comes back (OUT, IN); the engine must still
    put IN first.  Port 2 grounded makes the two ends DIFFERENT, so a swap
    is visible -- on the symmetric fixtures it would not be.
    """

    def setUp(self):
        self.freqs = np.logspace(8, 10, 41)
        self.Y = _asym_pi_Y(self.freqs)

    def _solve(self, in_p, out_p):
        res = tn.solve_net(self.freqs, self.Y, 2,
                           _ok("n", in_p, "", out_p, ""), [], F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        return res

    def test_the_setup_is_asymmetric(self):
        a = self._solve("1", "2")
        self.assertFalse(np.allclose(a.Z2[:, 0, 0], a.Z2[:, 1, 1]))
        # 40 fF at the IN end, 100 fF at the OUT end, as built.
        self.assertAlmostEqual(a.model.shunt_in.C_farad / 1e-15, 40.0, places=3)
        self.assertAlmostEqual(a.model.shunt_out.C_farad / 1e-15, 100.0, places=3)

    def test_out_on_the_lower_port_is_not_swapped(self):
        a = self._solve("1", "2")           # IN on the lower port
        b = self._solve("2", "1")           # the matrix comes back (OUT, IN)
        self.assertEqual(b.model.in_name, tn.IN_NAME)
        self.assertEqual(b.model.out_name, tn.OUT_NAME)
        # b's IN is a's OUT: the 2x2 is a's with both axes reversed, exactly.
        self.assertTrue(np.array_equal(b.Z2, a.Z2[:, ::-1, ::-1]))
        # The pi is read off inv(Z2), which is not bit-symmetric under the
        # axis swap, so the branches agree to round-off rather than exactly.
        self.assertTrue(np.isclose(b.model.shunt_in.Z, a.model.shunt_out.Z,
                                   rtol=1e-9, atol=0.0))
        self.assertTrue(np.isclose(b.model.shunt_out.Z, a.model.shunt_in.Z,
                                   rtol=1e-9, atol=0.0))
        self.assertAlmostEqual(b.model.shunt_in.C_farad / 1e-15, 100.0, places=3)

    def test_against_the_cli_arithmetic_directly(self):
        ts, Y = _load("diff_pair_4port.s4p")
        res = tn.solve_net(ts.freqs, Y, ts.nports,
                           _ok("n", "3", "", "1", ""), ground_rows([2]), F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        self.assertTrue(np.array_equal(
            res.Z2, _cli_z2(ts, Y, [3], [], [1], [], gnd=[2])))


# ============================================================================
# The imbalance check carries the ground ports (the CLI's rule)
# ============================================================================

def _six_port_pair_Y(freqs):
    """
    A coupled pair (1,2) -> (3,4) with two reference ports at the IN end:
    line + sees 1 pF to port 5, line - sees 100 fF to port 6.  Ports 5 / 6
    OPEN: each capacitor dangles into an isolated node and the pair is
    symmetric (measured 1.6e-13).  GROUNDED: the two lines see very different
    capacitance to the reference and the pair is imbalanced (measured 0.67).
    Which of the two the engine computed is therefore readable off the
    mode-conversion number alone.  The series branch is 100 ohm + 10 nH so the
    imbalance is comparable to the series admittance rather than lost under
    it.
    """
    n = 6
    w = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    Y = np.zeros((len(w), n, n), dtype=complex)

    def stamp(a, b, y):
        Y[:, a, a] += y
        Y[:, b, b] += y
        Y[:, a, b] -= y
        Y[:, b, a] -= y

    def shunt(a, y):
        Y[:, a, a] += y

    zs = 100.0 + 1j * w * 10e-9                  # each line's series branch
    stamp(0, 2, 1.0 / zs)
    stamp(1, 3, 1.0 / zs)
    stamp(0, 1, 1j * w * 20e-15)                 # line-to-line at each end
    stamp(2, 3, 1j * w * 20e-15)
    for p in range(4):
        shunt(p, 1j * w * 5e-15 + 1e-9)          # each end to true ground
    stamp(0, 4, 1j * w * 1e-12)                  # IN+ to its reference port
    stamp(1, 5, 1j * w * 100e-15)                # IN- to its reference port
    shunt(4, 1e-9)
    shunt(5, 1e-9)
    return Y


class TestImbalanceCarriesGnd(unittest.TestCase):

    def setUp(self):
        self.freqs = np.logspace(8, 10, 41)
        self.Y = _six_port_pair_Y(self.freqs)
        self.row = _ok("pair", "1", "2", "3", "4")

    def test_open_reference_reads_balanced(self):
        res = tn.solve_net(self.freqs, self.Y, 6, self.row, [], F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        self.assertLess(res.mode_conversion, 1e-9)

    def test_grounded_reference_reads_imbalanced(self):
        """The GND ports reach the four-port solve.  Mutation-checked."""
        res = tn.solve_net(self.freqs, self.Y, 6, self.row, ground_rows([5, 6]),
                           F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        self.assertGreater(res.mode_conversion, tmr.MODE_CONVERSION_WARN)
        self.assertEqual(res.mc_note,
                         "differential nodes: IN = (1)-(2), OUT = (3)-(4)")

    def test_it_is_one_frequency_of_the_clis_four_port_solve(self):
        res = tn.solve_net(self.freqs, self.Y, 6, self.row, ground_rows([5, 6]),
                           F_TARGET)
        k = int(np.argmin(np.abs(self.freqs - F_TARGET)))
        four = [("IN_p", [1], []), ("IN_n", [2], []),
                ("OUT_p", [3], []), ("OUT_n", [4], [])]
        term4 = build_terminations_coupling(four, [5, 6], (), nports=6)
        Z4, _n, _w = compute_z_matrix(self.Y[k:k + 1], self.freqs[k:k + 1],
                                      term4)
        self.assertEqual(res.mode_conversion, tm.mode_conversion_ratio(Z4[0]))

    def test_a_multi_port_side_skips_the_check_by_name(self):
        row = _ok("pair", "1", "2", "3", "4,6")
        res = tn.solve_net(self.freqs, self.Y, 6, row, [], F_TARGET)
        self.assertEqual(res.status, "ok", res.error)
        self.assertIsNone(res.mode_conversion)
        self.assertIn("ties more than one port", res.mc_note)


# ============================================================================
# One bad row does not affect another; errors never raise
# ============================================================================

class TestIsolation(unittest.TestCase):

    def setUp(self):
        self.ts, self.Y = _load("decap_4port.s4p")

    def test_a_bad_row_beside_a_good_one(self):
        rows = [_ok("good", "1", "", "2", ""), _ok("bad", "3", "", "x", "")]
        out = tn.solve_nets(self.ts.freqs, self.Y, self.ts.nports, rows, [],
                            F_TARGET)
        self.assertEqual([r.status for r in out], ["ok", "error"])
        self.assertIn("not a port spec", out[1].error)
        alone = tn.solve_net(self.ts.freqs, self.Y, self.ts.nports, rows[0],
                             [], F_TARGET)
        self.assertTrue(np.array_equal(out[0].Z2, alone.Z2))

    def test_a_table_rule_marks_only_its_row(self):
        rows = [_ok("x", "1", "", "2", ""), _ok("x", "3", "", "4", "")]
        out = tn.solve_nets(self.ts.freqs, self.Y, self.ts.nports, rows, [],
                            F_TARGET)
        self.assertEqual([r.status for r in out], ["ok", "error"])
        self.assertIn("Duplicate net name", out[1].error)

    def test_a_bad_connection_row_errors_every_row(self):
        rows = [_ok("x", "1", "", "2", ""), _ok("y", "3", "", "4", "")]
        out = tn.solve_nets(self.ts.freqs, self.Y, self.ts.nports, rows,
                            ground_rows("zz"), F_TARGET)
        self.assertEqual([r.status for r in out], ["error", "error"])
        self.assertIn("'zz' is not a port number", out[0].error)

    def test_solve_net_never_raises(self):
        cases = [
            _ok("", "1", "", "2", ""),
            _ok("A", "1", "", "2", ""),
            _ok("n", "", "", "2", ""),
            _ok("n", "1", "", "", ""),
            _ok("n", "1", "2", "3", ""),
            _ok("n", "1", "", "9", ""),
            _ok("n", "1", "", "1", ""),
            _ok("n", "1-", "", "2", ""),
        ]
        for row in cases:
            res = tn.solve_net(self.ts.freqs, self.Y, self.ts.nports, row,
                               [], F_TARGET)
            self.assertEqual(res.status, "error", row)
            self.assertTrue(res.error, row)
            self.assertIsNone(res.model)
        res = tn.solve_net(self.ts.freqs, self.Y, self.ts.nports,
                           _ok("n", "1", "", "2", ""), ground_rows([2]), F_TARGET)
        self.assertEqual(res.status, "error")
        self.assertIn("ground", res.error)
        # Garbage arrays are an error result too, not a traceback.
        res = tn.solve_net(np.array([]), self.Y, 4, _ok(), [], F_TARGET)
        self.assertEqual(res.status, "error")
        res = tn.solve_net(self.ts.freqs, None, 4, _ok(), [], F_TARGET)
        self.assertEqual(res.status, "error")

    def test_the_error_result_still_carries_its_signature(self):
        row = _ok("n", "1", "", "x", "")
        res = tn.solve_net(self.ts.freqs, self.Y, 4, row, ground_rows([3]),
                           F_TARGET, "f")
        self.assertEqual(res.signature,
                         tn.net_signature(row, ground_rows([3]), "f", F_TARGET))

    def test_signature_normalises_what_does_not_change_the_answer(self):
        a = tn.net_signature(_ok(" n ", "1 ", "", " 2", ""),
                             ground_rows("4,3"), "f", 1e9)
        b = tn.net_signature(_ok("n", "1", "", "2", ""),
                             ground_rows([3, 4, 3]), "f", 1e9)
        self.assertEqual(a, b)
        # Two ground rows are the same setting as one with both ports, and a
        # blank or switched-off row changes nothing.
        c = tn.net_signature(_ok("n", "1", "", "2", ""),
                             [ConnectionRow("ground", "4"), ConnectionRow(),
                              ConnectionRow("ground", "3"),
                              ConnectionRow("short", "1,2", enabled=False)],
                             "f", 1e9)
        self.assertEqual(a, c)
        self.assertNotEqual(a, tn.net_signature(_ok("n", "1", "", "2", ""),
                                                ground_rows([3]), "f", 1e9))
        self.assertNotEqual(a, tn.net_signature(_ok("n", "1", "", "2", ""),
                                                ground_rows([3, 4]), "g", 1e9))
        self.assertNotEqual(a, tn.net_signature(_ok("n", "1", "", "2", ""),
                                                ground_rows([3, 4]), "f", 2e9))
        # Any other kind keys on the rows themselves.
        d = tn.net_signature(_ok("n", "1", "", "2", ""),
                             [ConnectionRow("short", "3,4")], "f", 1e9)
        self.assertNotEqual(a, d)
        self.assertNotEqual(d, tn.net_signature(
            _ok("n", "1", "", "2", ""),
            [ConnectionRow("rlc_gnd", "3", R="50")], "f", 1e9))


# ============================================================================
# rebandwidth: the table again, no re-solve
# ============================================================================

class TestRetarget(unittest.TestCase):
    """
    Moving the marker re-reads the CACHED sweep: the result must EQUAL a fresh
    `solve_net` at the new frequency, and the full sweep must not be solved
    again.  This is what lets the panel answer the Freq field and the marker
    drag without Calculate all.
    """

    CASES = (
        ("pi_2port.s2p", _ok("se", "1", "", "2", ""), []),
        ("diff_pair_4port.s4p", _ok("df", "1", "2", "3", "4"), []),
        ("decap_4port.s4p", _ok("dg", "1", "", "2", ""), ground_rows([3, 4])),
    )
    F1, F2 = 1e8, 3e9

    def _pair(self, name, row, gnd):
        ts, Y = _load(name)
        first = tn.solve_net(ts.freqs, Y, ts.nports, row, gnd, self.F1, "f")
        fresh = tn.solve_net(ts.freqs, Y, ts.nports, row, gnd, self.F2, "f")
        return ts, Y, first, fresh

    def test_it_equals_a_fresh_solve_at_the_new_frequency(self):
        for name, row, gnd in self.CASES:
            with self.subTest(name=name):
                ts, Y, first, fresh = self._pair(name, row, gnd)
                self.assertEqual(first.status, "ok")
                got = tn.retarget(first, self.F2, Y, ts.nports, row, gnd, "f")
                self.assertTrue(_same(got.model, fresh.model))
                self.assertTrue(_same_list(got.bw_table, fresh.bw_table))
                self.assertTrue(_same_list(got.corners, fresh.corners))
                self.assertEqual(got.signature, fresh.signature)
                self.assertEqual(got.freq_snap, fresh.freq_snap)
                if fresh.mode_conversion is None:
                    self.assertIsNone(got.mode_conversion)
                else:
                    self.assertEqual(got.mode_conversion, fresh.mode_conversion)
                self.assertTrue(np.array_equal(got.Z2, fresh.Z2))

    def test_the_sweep_is_not_solved_again(self):
        for name, row, gnd in self.CASES:
            with self.subTest(name=name):
                ts, Y, first, _fresh = self._pair(name, row, gnd)
                with mock.patch.object(tn, "compute_z_matrix",
                                       wraps=compute_z_matrix) as cz:
                    tn.retarget(first, self.F2, Y, ts.nports, row, gnd, "f")
                # A differential net solves its one-frequency imbalance check;
                # nothing ever solves more than one frequency here.
                for call in cz.call_args_list:
                    self.assertEqual(len(call.args[1]), 1)
                self.assertEqual(cz.call_count, 1 if row.in_n else 0)

    def test_an_error_result_comes_back_unchanged(self):
        ts, Y = _load("pi_2port.s2p")
        row = _ok("n", "1", "", "x", "")
        err = tn.solve_net(ts.freqs, Y, 2, row, [], self.F1)
        self.assertIs(tn.retarget(err, self.F2, Y, 2, row, [], ""), err)


class TestRebandwidth(unittest.TestCase):

    def setUp(self):
        self.ts, self.Y = _load("pi_2port.s2p")
        self.res = tn.solve_net(self.ts.freqs, self.Y, 2, _ok(), [], F_TARGET)

    def test_it_does_not_call_the_solver(self):
        with mock.patch.object(tn, "compute_z_matrix",
                               side_effect=AssertionError("re-solved")) as cz:
            out = tn.rebandwidth(self.res, 200.0, 150e-15)
        self.assertEqual(cz.call_count, 0)
        self.assertIs(out.Z2, self.res.Z2)
        self.assertIs(out.model, self.res.model)
        self.assertEqual(out.z_src_ohm, 200.0)
        self.assertEqual(out.c_load_extra_f, 150e-15)

    def test_it_is_the_table_the_old_window_computed(self):
        out = tn.rebandwidth(self.res, 200.0, 150e-15)
        loads = tuple(sorted(set(tm.DEFAULT_LOADS_F) | {150e-15}))
        want = tm.bandwidth_table(self.res.freqs, self.res.Z2,
                                  self.res.model.requested_hz, 200.0, loads)
        self.assertTrue(_same_list(out.bw_table, want))
        self.assertEqual(len(out.bw_table), len(tm.DEFAULT_LOADS_F) + 1)
        self.assertTrue(all(b.z_src_ohm == 200.0 for b in out.bw_table))

    def test_a_bad_entry_falls_back_to_the_default(self):
        out = tn.rebandwidth(self.res, float("nan"), -1.0)
        self.assertEqual(out.z_src_ohm, tn.DEFAULT_SRC_OHM)
        self.assertEqual(out.c_load_extra_f, tn.DEFAULT_LOAD_EXTRA_F)

    def test_an_error_result_only_takes_the_terminations(self):
        err = tn.solve_net(self.ts.freqs, self.Y, 2, _ok("n", "1", "", "x", ""),
                           [], F_TARGET)
        out = tn.rebandwidth(err, 50.0, 1e-15)
        self.assertEqual(out.status, "error")
        self.assertEqual(out.bw_table, ())
        self.assertEqual(out.z_src_ohm, 50.0)


# ============================================================================
# The summary table
# ============================================================================

class TestSummaryTable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        ts, Y = _load("decap_4port.s4p")
        cls.sig = tn.solve_net(ts.freqs, Y, 4, _ok("sig", "1", "", "2", ""),
                               [], F_TARGET)
        cls.decap = tn.solve_net(ts.freqs, Y, 4, _ok("decap", "3", "", "4", ""),
                                 [], F_TARGET)
        cls.bad = tn.solve_net(ts.freqs, Y, 4, _ok("bad", "1", "", "x", ""),
                               [], F_TARGET)
        cls.stale = replace(cls.sig, name="old", status="stale")
        cls.never = replace(cls.bad, name="new", status="stale", error="")

    def test_header_and_columns_agree(self):
        lines = tmr.summary_table_lines([self.sig])
        head = lines[0]
        for key, header, start, end in tmr.SUMMARY_COLUMNS:
            self.assertIn(header, head[start:end], key)
        self.assertEqual([c[0] for c in tmr.SUMMARY_COLUMNS],
                         ["net", "r_ser", "l_ser", "c_in", "c_out", "f_3db",
                          "lumped"])
        self.assertEqual(head[:3], "Net")

    def test_a_value_row_lands_in_its_columns(self):
        line = tmr.summary_table_lines([self.sig])[1]
        cols = {c[0]: line[c[2]:c[3]].strip() for c in tmr.SUMMARY_COLUMNS}
        self.assertEqual(cols["net"], "sig")
        self.assertEqual(cols["r_ser"], "1 " + tmr.OHM)
        self.assertEqual(cols["l_ser"], "1 nH")
        self.assertEqual(cols["c_in"], "1 fF")
        self.assertEqual(cols["c_out"], "1 fF")
        self.assertTrue(cols["f_3db"].endswith("Hz"), cols["f_3db"])
        self.assertEqual(cols["lumped"], "ok")

    def test_status_rows_say_so_instead_of_blanks(self):
        lines = tmr.summary_table_lines([self.bad, self.stale, self.never])
        self.assertTrue(lines[1].startswith("bad"))
        self.assertIn("error: ", lines[1])
        self.assertIn("not a port spec", lines[1])
        self.assertTrue(lines[2].startswith("old"))
        cols = {c[0]: lines[2][c[2]:c[3]].strip() for c in tmr.SUMMARY_COLUMNS}
        self.assertEqual(cols["lumped"], "stale")
        self.assertEqual(cols["l_ser"], "1 nH")       # the numbers it had
        self.assertTrue(lines[3].startswith("new"))
        self.assertIn("stale: not calculated yet", lines[3])

    def test_sorting_and_the_order_it_reports(self):
        results = [self.sig, self.bad, self.decap]
        order = tmr.summary_order(results, "l_ser")
        # decap's series branch reads as a NEGATIVE L (-25.3 nH, a capacitor
        # past resonance), sig's as +1 nH; the error row goes last.
        self.assertLess(self.decap.model.series.L_henry, 0.0)
        self.assertEqual(order, [2, 0, 1])
        self.assertEqual(tmr.summary_order(results, "l_ser", descending=True),
                         [0, 2, 1])
        self.assertEqual(tmr.summary_order(results, "net"), [2, 0, 1])
        self.assertEqual(tmr.summary_order(results, "net", True), [0, 2, 1])
        self.assertEqual(tmr.summary_order(results), [0, 1, 2])
        lines = tmr.summary_table_lines(results, "l_ser", descending=True)
        self.assertTrue(lines[1].startswith("sig"))
        self.assertTrue(lines[2].startswith("decap"))
        self.assertTrue(lines[3].startswith("bad"))
        self.assertIn("L_ser v", lines[0])
        self.assertIn("L_ser ^", tmr.summary_table_lines(results, "l_ser")[0])

    def test_an_empty_cell_sorts_after_values_whichever_way(self):
        no_bw = replace(self.decap, bw_table=())
        results = [no_bw, self.sig]
        self.assertEqual(tmr.summary_order(results, "f_3db"), [1, 0])
        self.assertEqual(tmr.summary_order(results, "f_3db", True), [1, 0])
        line = tmr.summary_table_lines([no_bw])[1]
        cols = {c[0]: line[c[2]:c[3]].strip() for c in tmr.SUMMARY_COLUMNS}
        self.assertEqual(cols["f_3db"], "--")

    def test_the_lumped_column_warns_with_the_drift(self):
        ref = self.sig.reference
        # A reference whose series R is 30 % off the marker's.  R, because at
        # 1 MHz the fixture's series branch has |Q| = 0.006 and READS AS a
        # resistor, so that is the value `lumped_drift` compares.
        s = ref.series
        self.assertEqual(s.reads_as, "R")
        moved = replace(ref, series=replace(s, R_ohm=s.R_ohm / 1.3))
        res = replace(self.sig, reference=moved)
        line = tmr.summary_table_lines([res])[1]
        cols = {c[0]: line[c[2]:c[3]].strip() for c in tmr.SUMMARY_COLUMNS}
        self.assertEqual(cols["lumped"], "warn 30 %")
        self.assertAlmostEqual(tmr.summary_sort_value(res, "lumped"), 0.3, places=6)

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            tmr.summary_sort_value(self.sig, "q")

    def test_every_line_is_ascii_but_for_the_ohm(self):
        for line in tmr.summary_table_lines([self.sig, self.bad]):
            self.assertTrue(line.replace(tmr.OHM, "").isascii(), line)


class TestNoTk(unittest.TestCase):

    def test_the_engine_imports_no_tkinter(self):
        self.assertNotIn("tkinter", sys.modules)
        self.assertNotIn("matplotlib", sys.modules)
        self.assertNotIn("pkg_rlc.frontend.app", sys.modules)


if __name__ == "__main__":
    unittest.main()
