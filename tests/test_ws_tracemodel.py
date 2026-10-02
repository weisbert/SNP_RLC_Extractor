"""
The Trace model workspace: `pkg_rlc.panels.ws_tracemodel` in a real `App`.

`docs/design_workspaces.md` § 2, stage 1.  Two halves:

  PURE -- the helpers that need no display: the port dropdown entries and
  their normalisation, the issue lines, the CSV rows off a `NetResult` the
  engine solved.  They import no App.

  TK -- a real `App` with `diff_pair_4port.s4p` loaded, switched to the
  workspace.  The nets are filled THROUGH THE ROWTABLE (`set_rows`, or a
  keystroke into a cell widget) and every assertion is about what is on
  screen: the Summary's lines, a cell's colour, the canvas's text items,
  the legend's labels.  `tkinter.messagebox` is patched on every validation
  path and asserted NOT called -- the owner's original complaint was a
  dialog.

Not in `FAST_MODULES`: it imports tkinter.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
import tempfile
import tkinter as tk
import unittest
from tkinter import messagebox
from unittest import mock

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pkg_rlc.panels.ws_tracemodel as wsm  # noqa: E402
import pkg_rlc.services.tracenets as tn  # noqa: E402
from pkg_rlc.panels.ws_tracemodel import (  # noqa: E402
    DEFAULT_FREQ_GHZ, ERROR_FG, WARN_CELL_FG, csv_rows, issue_lines,
    port_choices, port_text_from_choice,
)
from pkg_rlc.present.tracemodel_report import (  # noqa: E402
    RESPONSE_DB_CEIL, branch_value_lines, summary_table_lines,
)
from pkg_rlc.services.tracenets import NetRow, solve_net, validate_nets  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "diff_pair_4port.s4p"
SECOND = FIXTURES / "decap_4port.s4p"
F_HZ = float(DEFAULT_FREQ_GHZ) * 1e9

P = NetRow("p", "1", "", "3", "")
N = NetRow("n", "2", "", "4", "")


def _ensure_fixtures():
    if not FIXTURE.exists():
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import generate_test_snp
        generate_test_snp.main()


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                       # noqa: BLE001
        return False


TK_OK = _tk_ok()


def _load(name):
    from pkg_rlc.model.trace import FileEntry
    from pkg_rlc.physics.core import parse_touchstone
    return FileEntry(parse_touchstone(FIXTURES / name))


# ============================================================================
# Pure
# ============================================================================

class TestNoAppImport(unittest.TestCase):

    def test_the_panel_never_imports_the_app(self):
        src = pathlib.Path(wsm.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(import|from)\s+pkg_rlc\.frontend")
        self.assertNotIn("messagebox", src.split('"""', 2)[2],
                         "no dialog on any path of this panel")


class TestPortChoices(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_named_ports_show_number_and_name(self):
        fe = _load("diff_pair_4port.s4p")
        self.assertEqual(port_choices(fe.ts),
                         ["1  in_p", "2  in_n", "3  out_p", "4  out_n"])

    def test_unnamed_ports_show_the_number_alone(self):
        class _TS:
            nports = 3
            port_names = ["", "", ""]
        self.assertEqual(port_choices(_TS()), ["1", "2", "3"])

    def test_a_picked_entry_becomes_its_number_and_typed_text_is_kept(self):
        self.assertEqual(port_text_from_choice("3  out_p"), "3")
        self.assertEqual(port_text_from_choice("12  DQ 0"), "12")
        self.assertEqual(port_text_from_choice("1,3"), "1,3")
        self.assertEqual(port_text_from_choice(" 6-14 "), "6-14")
        self.assertEqual(port_text_from_choice(""), "")
        # One space is a typed value, not a pick.
        self.assertEqual(port_text_from_choice("3 4"), "3 4")


class TestIssueLines(unittest.TestCase):

    def test_errors_first_and_rows_named(self):
        rows = [NetRow("x", "1", "", "2", ""), NetRow("x", "1", "", "", "")]
        issues = validate_nets(rows, "", 4)
        lines = issue_lines(issues, rows)
        self.assertTrue(all(l.startswith("Row 2 'x'") for l in lines), lines)
        self.assertTrue(any("Duplicate net name" in l for l in lines))
        self.assertTrue(any("OUT+" in l for l in lines))

    def test_a_warning_is_labelled_and_sorted_after_errors(self):
        rows = [NetRow("d", "1,2", "3", "4", "5"), NetRow("", "6", "", "7", "")]
        issues = validate_nets(rows, "", 8)
        lines = issue_lines(issues, rows)
        self.assertTrue(lines[0].startswith("Row 2, Name: Name required"))
        self.assertTrue(lines[-1].startswith("Row 1 'd', IN+: warning:"))

    def test_the_gnd_field_is_named(self):
        lines = issue_lines(validate_nets([], "x", 4), [])
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("GND: GND 'x' is not a port spec"))


class TestCsvRows(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()
        fe = _load("diff_pair_4port.s4p")
        cls.res = solve_net(fe.ts.freqs, fe.Y, 4, P, [], F_HZ, fe.label)
        cls.err = solve_net(fe.ts.freqs, fe.Y, 4, NetRow("q", "", "", "3", ""),
                            [], F_HZ, fe.label)

    def test_three_blocks_at_full_precision(self):
        rows = csv_rows([self.res, self.err])
        self.assertEqual(rows[0][0], "net")
        self.assertEqual(rows[1][0], "p")
        self.assertEqual(float(rows[1][4]), self.res.model.series.R_ohm)
        self.assertEqual(float(rows[1][5]), self.res.model.series.L_henry)
        self.assertEqual(rows[2][:2], ["q", "error"])
        self.assertTrue(rows[2][-1].startswith("IN+ is required"))
        blanks = [i for i, r in enumerate(rows) if r == []]
        self.assertEqual(len(blanks), 2)
        branches = rows[blanks[0] + 1:blanks[1]]
        self.assertEqual(branches[0][1], "branch")
        self.assertEqual([r[1] for r in branches[1:]],
                         [b.name for b in self.res.model.branches])
        self.assertEqual(float(branches[1][2]), self.res.model.series.R_ohm)
        bws = rows[blanks[1] + 1:]
        self.assertEqual(bws[0][0], "net")
        self.assertEqual(len(bws) - 1, len(self.res.bw_table))
        self.assertEqual(float(bws[1][2]), self.res.bw_table[0].c_load_farad)


# ============================================================================
# Tk: a real App, switched to the workspace
# ============================================================================

@unittest.skipUnless(TK_OK, "no display")
class _WsCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        from pkg_rlc.frontend.app import App
        self.app = App()
        self.app.withdraw()
        self.fe = _load("diff_pair_4port.s4p")
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.app.show_workspace("trace")
        self._settle()
        self.ws = self.app.trace_ws
        self._dialogs = []
        for name in ("showinfo", "showwarning", "showerror", "askyesno"):
            p = mock.patch.object(messagebox, name)
            self._dialogs.append(p.start())
            self.addCleanup(p.stop)

    def tearDown(self):
        for d in self._dialogs:
            d.assert_not_called()
        try:
            self.app.destroy()
        except Exception:                                   # noqa: BLE001
            pass

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _map(self):
        self.app.geometry("1040x600")
        self.app.deiconify()
        self._settle()

    def _two_nets(self):
        self.ws.set_rows([P, N])
        self.ws.calculate_all()
        self._settle()

    def _expected(self, row, gnd=(), f=F_HZ, src=1.0, load=0.0):
        return solve_net(self.fe.ts.freqs, self.fe.Y, 4, row, list(gnd), f,
                         self.fe.label, src, load)

    def _canvas_texts(self):
        cv = self.ws.canvas
        return [cv.itemcget(i, "text") for i in cv.find_all()
                if cv.type(i) == "text"]

    def _type(self, row, key, text):
        w = self.ws.table.data_row_widget(row, key)
        w.delete(0, "end")
        w.insert(0, text)
        self._settle(1)


class TestTheFileAndTheTable(_WsCase):

    def test_the_file_defaults_to_the_loaded_one_and_offers_its_ports(self):
        self.assertEqual(self.ws.file_var.get(), self.fe.label)
        self.assertEqual(tuple(self.ws.file_cbo.cget("values")),
                         (self.fe.label,))
        for key in ("in_p", "in_n", "out_p", "out_n"):
            self.assertEqual(self.ws.table.column_values(key),
                             ("1  in_p", "2  in_n", "3  out_p", "4  out_n"))

    def test_the_fields_start_at_their_defaults(self):
        self.assertEqual(self.ws.freq_var.get(), DEFAULT_FREQ_GHZ)
        self.assertEqual(self.ws.src_var.get(), "1")
        self.assertEqual(self.ws.load_var.get(), "0")
        self.assertEqual(self.ws.gnd_var.get(), "")
        self.assertEqual(self.ws.rows(), [])

    def test_a_picked_dropdown_entry_is_stored_as_its_port_number(self):
        self.ws.set_rows([NetRow("p", "", "", "", "")])
        self._type(0, "in_p", "1  in_p")
        self._type(0, "out_p", "3  out_p")
        self.assertEqual(self.ws.rows(), [P])

    def test_duplicate_and_remove_row(self):
        self.ws.set_rows([P, N])
        self.ws.duplicate_row(0)
        self.assertEqual(self.ws.rows(), [P, P, N])
        self.ws.remove_row(1)
        self.assertEqual(self.ws.rows(), [P, N])
        self.ws.remove_row()
        self.assertEqual(self.ws.rows(), [P])

    def test_the_add_button_says_add_net(self):
        self.assertEqual(self.ws.table._add_btn.cget("text"), "+ Add net")


class TestCalculateAll(_WsCase):

    def test_two_nets_give_the_engines_numbers_line_for_line(self):
        self._two_nets()
        exp_p, exp_n = self._expected(P), self._expected(N)
        self.assertEqual(self.ws.summary_lines(),
                         summary_table_lines([exp_p, exp_n]))
        got_p, got_n = self.ws.results
        self.assertEqual(got_p.status, "ok")
        self.assertEqual(got_p.model, exp_p.model)
        self.assertEqual(got_n.model, exp_n.model)
        self.assertTrue(np.array_equal(got_p.Z2, exp_p.Z2))
        self.assertEqual(self.ws.status_lbl.cget("text"), "2 solved.")

    def test_a_row_with_an_error_is_skipped_and_says_why(self):
        self.ws.set_rows([P, NetRow("p", "2", "", "4", "")])
        self.ws.calculate_all()
        lines = self.ws.summary_lines()
        self.assertEqual(lines[1], summary_table_lines([self._expected(P)])[1])
        self.assertTrue(lines[2].startswith("p               error: Duplicate "
                                            "net name 'p'"), lines[2])
        self.assertEqual(self.ws.status_lbl.cget("text"),
                         "1 solved, 1 skipped (see the Summary).")

    def test_a_bad_gnd_entry_fails_every_row_in_the_summary(self):
        self.ws.set_rows([P, N])
        self.ws.gnd_var.set("x")
        self.ws.calculate_all()
        for line in self.ws.summary_lines()[1:]:
            self.assertIn("error: GND 'x' is not a port spec", line)
        self.assertEqual(str(self.ws.gnd_entry.cget("foreground")), ERROR_FG)

    def test_without_a_file_or_a_net_the_status_line_says_so(self):
        self.ws.calculate_all()
        self.assertIn("Add a net first", self.ws.status_lbl.cget("text"))
        self.ws.file_var.set("")
        self.ws.set_rows([P])
        self.ws.calculate_all()
        self.assertIn("Load a Touchstone file", self.ws.status_lbl.cget("text"))

    def test_a_non_numeric_freq_is_refused_in_the_status_line(self):
        self.ws.set_rows([P])
        self.ws.freq_var.set("abc")
        self.ws.calculate_all()
        self.assertIn("Freq is not a number", self.ws.status_lbl.cget("text"))

    def test_differential_net_and_gnd_reach_the_engine(self):
        D = NetRow("d", "1", "2", "3", "4")
        self.ws.set_rows([D])
        self.ws.calculate_all()
        exp = self._expected(D)
        self.assertEqual(self.ws.results[0].model, exp.model)
        self.assertEqual(self.ws.results[0].mode_conversion,
                         exp.mode_conversion)
        self.assertTrue(self.ws.results[0].differential)

    def test_the_first_net_with_a_model_is_selected_after_calculate(self):
        self._two_nets()
        self.assertEqual(self.ws.selected_name, "p")


class TestValidationIsInTheTableNotADialog(_WsCase):

    def test_a_duplicate_name_paints_the_second_name_cell_red(self):
        self.ws.set_rows([P, NetRow("p", "2", "", "4", "")])
        self.assertEqual(str(self.ws.table.data_row_widget(1, "name").cget(
            "foreground")), ERROR_FG)
        self.assertEqual(str(self.ws.table.data_row_widget(0, "name").cget(
            "foreground")), "")
        self.assertIn("Duplicate net name 'p'", self.ws.issues_lbl.cget("text"))
        self.assertEqual(str(self.ws.issues_lbl.cget("foreground")), ERROR_FG)

    def test_a_port_in_gnd_paints_that_port_cell_red(self):
        self.ws.set_rows([P, N])
        self.ws.gnd_var.set("3")
        self._settle(1)
        self.assertEqual(str(self.ws.table.data_row_widget(0, "out_p").cget(
            "foreground")), ERROR_FG)
        self.assertEqual(str(self.ws.table.data_row_widget(1, "out_p").cget(
            "foreground")), "")
        self.assertIn("Port 3 is also in GND", self.ws.issues_lbl.cget("text"))

    def test_fixing_the_cell_clears_the_colour(self):
        self.ws.set_rows([P, NetRow("p", "2", "", "4", "")])
        self._type(1, "name", "n")
        self.assertEqual(str(self.ws.table.data_row_widget(1, "name").cget(
            "foreground")), "")
        self.assertEqual(self.ws.issues_lbl.cget("text"), "")

    def test_a_warning_is_amber(self):
        # A tied side on a differential net needs five ports; with no file
        # picked the range check is off, which is what this case is about.
        self.ws.file_var.set("")
        self.ws.set_rows([NetRow("d", "1,2", "3", "4", "")])
        self._settle(1)
        # out_n empty with in_n filled is the error; the tied IN+ side is
        # the warning once both minus cells are there.
        self.assertEqual(str(self.ws.table.data_row_widget(0, "out_n").cget(
            "foreground")), ERROR_FG)
        self._type(0, "out_n", "5")
        self.assertEqual(str(self.ws.table.data_row_widget(0, "in_p").cget(
            "foreground")), WARN_CELL_FG)
        self.assertEqual(str(self.ws.issues_lbl.cget("foreground")), WARN_CELL_FG)

    def test_a_keystroke_validates_live(self):
        self.ws.set_rows([P])
        self._type(0, "out_p", "9")
        self.assertEqual(str(self.ws.table.data_row_widget(0, "out_p").cget(
            "foreground")), ERROR_FG)
        self.assertIn("beyond this file's 4 ports",
                      self.ws.issues_lbl.cget("text"))

    def test_a_port_beyond_the_file_is_red_on_calculate_too(self):
        self.ws.set_rows([NetRow("p", "1", "", "9", "")])
        self.ws.calculate_all()
        self.assertIn("error: Port 9 is beyond", self.ws.summary_lines()[1])


class TestTheSummary(_WsCase):

    def _click(self, index):
        bb = self.ws.summary.bbox(index)
        self.assertIsNotNone(bb, f"{index} is not on screen")
        self.ws.summary.event_generate("<Button-1>", x=bb[0] + 1, y=bb[1] + 1)
        self._settle(1)

    def test_clicking_a_header_sorts_and_again_reverses(self):
        self._map()
        self._two_nets()
        # Give the two nets different R so the order is observable: n's
        # OUT+ on port 4 and p's on 3 read the same symmetric pair, so
        # sort on the name column, which they do differ on.
        self._click("1.1")                         # "Net"
        self.assertEqual(self.ws.sort_state, ("net", False))
        self.assertTrue(self.ws.summary_lines()[0].startswith("Net ^"))
        self.assertEqual([l.split()[0] for l in self.ws.summary_lines()[1:]],
                         ["n", "p"])
        self._click("1.1")
        self.assertEqual(self.ws.sort_state, ("net", True))
        self.assertEqual([l.split()[0] for l in self.ws.summary_lines()[1:]],
                         ["p", "n"])
        self._click("1.17")                        # "R_ser"
        self.assertEqual(self.ws.sort_state, ("r_ser", False))
        self.assertIn("R_ser ^", self.ws.summary_lines()[0])

    def test_clicking_a_row_selects_that_net_and_draws_it(self):
        self._map()
        self._two_nets()
        self.ws.sort_by("net")
        self.assertEqual(self.ws.summary_lines()[1].split()[0], "n")
        self._click("2.3")
        self.assertEqual(self.ws.selected_name, "n")
        self.assertIn("n  (single-ended)", self._canvas_texts())
        self._click("3.3")
        self.assertEqual(self.ws.selected_name, "p")

    def test_the_selected_row_carries_the_sel_tag(self):
        self._two_nets()
        self.ws.select_net("n")
        ranges = self.ws.summary.tag_ranges(wsm.SUMMARY_SEL_TAG)
        self.assertEqual(len(ranges), 2)
        self.assertTrue(str(ranges[0]).startswith("3."))

    def test_the_band_is_visible_without_keyboard_focus(self):
        """Tk's built-in "sel" paints only while the Text has focus, and the
        Summary never takes it: the band showed nothing at all."""
        self._two_nets()
        self.ws.select_net("n")
        self.assertNotEqual(wsm.SUMMARY_SEL_TAG, "sel")
        self.assertEqual(self.ws.summary.tag_ranges("sel"), ())
        self.assertTrue(self.ws.summary.tag_cget(wsm.SUMMARY_SEL_TAG,
                                                 "background"))

    def test_an_empty_table_says_so(self):
        self.assertIn("(no nets", self.ws.summary_lines()[1])


class TestTheSchematic(_WsCase):

    def test_selecting_a_net_puts_its_values_on_the_canvas(self):
        self._two_nets()
        self.ws.select_net("p")
        texts = self._canvas_texts()
        m = self.ws.results[0].model
        for txt in branch_value_lines(m, m.series)[:2]:
            self.assertIn(txt, texts)
        for txt in branch_value_lines(m, m.shunt_in)[:2]:
            self.assertIn(txt, texts)
        self.assertIn("IN", texts)
        self.assertIn("OUT", texts)
        self.assertIn("reference", texts)
        self.assertIn("p  (single-ended)", texts)

    def test_a_differential_net_draws_no_reference(self):
        self.ws.set_rows([NetRow("d", "1", "2", "3", "4")])
        self.ws.calculate_all()
        texts = self._canvas_texts()
        self.assertNotIn("reference", texts)
        self.assertIn("across the pair -- no reference node", texts)

    def test_an_error_row_explains_itself_on_the_canvas(self):
        self.ws.set_rows([NetRow("p", "1", "", "9", "")])
        self.ws.calculate_all()
        self.ws.select_net("p")
        self.assertTrue(any("error: Port 9" in t for t in self._canvas_texts()))

    def test_the_canvas_redraws_on_resize(self):
        self._map()
        self._two_nets()
        def extent():
            cv = self.ws.canvas
            return (len(cv.find_all()),
                    max(cv.coords(i)[0] for i in cv.find_all()),
                    max(cv.coords(i)[1] for i in cv.find_all()))
        n0, x0, y0 = extent()
        self.app.geometry("1300x800")
        self._settle()
        n1, x1, y1 = extent()
        self.assertEqual(n1, n0)
        self.assertGreater(x1, x0 + 50, "the drawing did not follow the width")
        self.assertGreater(y1, y0 + 20, "the drawing did not follow the height")


class TestStaleness(_WsCase):

    def test_editing_one_row_marks_only_that_row_stale(self):
        self._two_nets()
        self._type(0, "out_p", "3  out_p")     # a pick of the same port
        self.assertNotIn("stale", "".join(self.ws.summary_lines()))
        self._type(0, "in_p", "1,2")
        lines = self.ws.summary_lines()
        self.assertTrue(lines[1].endswith("stale"), lines[1])
        self.assertTrue(lines[2].endswith("ok"), lines[2])
        self.assertEqual(self.ws.results[0].status, "stale")
        self.assertEqual(self.ws.results[1].status, "ok")
        self.assertIn("STALE -- press Calculate all", self._canvas_texts())

    def test_gnd_and_file_mark_every_row_stale(self):
        self._two_nets()
        self.ws.gnd_var.set("4")
        self.assertEqual([r.status for r in self.ws.results], ["stale", "stale"])
        self.ws.gnd_var.set("")
        self.assertEqual([r.status for r in self.ws.results], ["ok", "ok"])
        self.ws.file_var.set("other.s4p")
        self.ws._on_file_changed()
        self.assertEqual([r.status for r in self.ws.results], ["stale", "stale"])

    def test_a_new_freq_is_answered_at_once_without_a_sweep_solve(self):
        """The marker is not part of the SOLVE: every row is re-read from its
        cached sweep, and equals a fresh solve at the new frequency."""
        self._two_nets()
        with mock.patch.object(tn, "compute_z_matrix",
                               wraps=tn.compute_z_matrix) as cz:
            self.ws.freq_var.set("2")
        self.assertEqual([r.status for r in self.ws.results], ["ok", "ok"])
        for call in cz.call_args_list:              # imbalance checks only
            self.assertEqual(len(call.args[1]), 1)
        for res, row in zip(self.ws.results, (P, N)):
            exp = self._expected(row, f=2e9)
            self.assertEqual(repr(res.model), repr(exp.model))
            self.assertEqual(repr(res.bw_table), repr(exp.bw_table))
        self.assertNotIn("stale", "".join(self.ws.summary_lines()))

    def test_calculate_all_solves_only_the_stale_rows(self):
        self._two_nets()
        self._type(0, "name", "q")             # a new signature, no conflict
        self.assertEqual([r.status for r in self.ws.results],
                         ["stale", "ok"])
        with mock.patch.object(wsm, "solve_net", wraps=solve_net) as sn:
            self.ws.calculate_all()
        self.assertEqual(sn.call_count, 1)
        self.assertEqual(sn.call_args[0][3].name, "q")
        self.assertEqual([r.status for r in self.ws.results], ["ok", "ok"])
        with mock.patch.object(wsm, "solve_net", wraps=solve_net) as sn:
            self.ws.calculate_all()
        self.assertEqual(sn.call_count, 0)
        self.assertEqual(self.ws.status_lbl.cget("text"), "0 solved, 2 unchanged.")

    def test_a_new_row_reads_not_calculated_yet(self):
        self.ws.set_rows([P])
        self.ws.calculate_all()
        self.ws.set_rows([P, N])
        self.assertTrue(self.ws.summary_lines()[2].startswith(
            "n               stale: not calculated yet"))
        # Only the new row is solved (P was already solved under the same
        # signature, and `set_rows` does not change that).
        with mock.patch.object(wsm, "solve_net", wraps=solve_net) as sn:
            self.ws.calculate_all()
        self.assertEqual(sn.call_count, 1)
        self.assertEqual(sn.call_args[0][3].name, "n")

    def test_source_and_load_rebandwidth_without_a_solve(self):
        self._two_nets()
        with mock.patch.object(tn, "compute_z_matrix",
                               side_effect=AssertionError("re-solved")):
            self.ws.load_var.set("75")
            self.ws.src_var.set("10")
            self.ws._on_terminations_changed()     # what <Return> / <FocusOut> call
        res = self.ws.results[0]
        self.assertEqual(res.status, "ok")
        load_f = 75.0 * 1e-15                  # the panel's own arithmetic
        self.assertIn(load_f, [b.c_load_farad for b in res.bw_table])
        self.assertTrue(all(b.z_src_ohm == 10.0 for b in res.bw_table))
        exp = self._expected(P, src=10.0, load=load_f)
        # repr, because a row that never crosses carries NaN, and NaN != NaN.
        self.assertEqual(repr(res.bw_table), repr(exp.bw_table))
        self.assertIn("75 fF", self.ws.legend_texts())


class TestTheResponse(_WsCase):

    def test_the_load_labels_are_a_legend_beside_the_plot(self):
        self._map()
        self._two_nets()
        self.assertEqual(self.ws.legend_texts(),
                         ["open", "20 fF", "50 fF", "200 fF"])
        cv = self.ws.resp
        plot_w = cv.winfo_width() - wsm._LEGEND_W
        for i in cv.find_all():
            if cv.type(i) == "text" and cv.itemcget(i, "text") in (
                    "open", "20 fF", "50 fF", "200 fF"):
                self.assertGreater(cv.coords(i)[0], plot_w,
                                   "a load label sits over the curves")

    def test_the_curves_are_clipped_to_the_window(self):
        self._two_nets()
        res = self.ws.results[0]
        huge = np.full(len(res.freqs), 1e6, dtype=complex)
        with mock.patch.object(wsm.tmod, "transfer_function",
                               return_value=huge * np.linspace(1, 50, len(res.freqs))):
            db = wsm.TraceModelWorkspace._db_curve(res, 1.0, 0.0)
        self.assertLessEqual(float(np.nanmax(db)), RESPONSE_DB_CEIL)

    def test_no_curve_point_is_above_the_frame(self):
        self._map()
        self._two_nets()
        cv = self.ws.resp
        axis_lines = [cv.coords(i) for i in cv.find_all()
                      if cv.type(i) == "line" and cv.itemcget(i, "width") in ("1.0", "1")
                      and not cv.itemcget(i, "dash")]
        y0 = min(c[1] for c in axis_lines)
        curves = [cv.coords(i) for i in cv.find_all()
                  if cv.type(i) == "line" and cv.itemcget(i, "width") == "2.0"]
        self.assertTrue(curves)
        for c in curves:
            self.assertGreaterEqual(min(c[1::2]), y0 - 0.5)

    def test_dragging_the_marker_moves_freq_to_a_sweep_point(self):
        self._map()
        self._two_nets()
        cv = self.ws.resp
        before = self.ws.freq_var.get()
        x = (cv.winfo_width() - wsm._LEGEND_W) * 0.7
        cv.event_generate("<ButtonPress-1>", x=int(x), y=40)
        cv.event_generate("<B1-Motion>", x=int(x), y=40)
        self.assertTrue(cv.find_withtag("drag"), "no ghost marker while dragging")
        cv.event_generate("<ButtonRelease-1>", x=int(x), y=40)
        self._settle(1)
        self.assertFalse(cv.find_withtag("drag"))
        after = self.ws.freq_var.get()
        self.assertNotEqual(after, before)
        hz = float(after) * 1e9
        freqs = np.asarray(self.fe.ts.freqs, dtype=float)
        self.assertLess(float(np.min(np.abs(freqs - hz))) / hz, 1e-5)
        self.assertEqual([r.status for r in self.ws.results], ["ok", "ok"])
        self.assertEqual(self.ws.results[0].model.freq_hz,
                         self._expected(P, f=hz).model.freq_hz)

    def test_ticking_a_net_overlays_it(self):
        self._map()
        self._two_nets()
        self.assertEqual([b.cget("text") for b in self.ws._overlay_btns],
                         ["p", "n"])
        self.ws.set_overlay("n", True)
        self.assertEqual(self.ws.legend_texts()[-1], "n")
        self.assertEqual(self.ws.overlay_names(), ["n"])
        self.ws.set_overlay("n", False)
        self.assertNotIn("n", self.ws.legend_texts())
        # The selected net is never its own overlay.
        self.ws.set_overlay("p", True)
        self.assertEqual(self.ws.overlay_names(), [])


class TestTheDetails(_WsCase):

    def test_collapsed_by_default_and_toggles(self):
        self._map()
        self.assertFalse(self.ws.details_open)
        self.assertFalse(self.ws.details_text.winfo_ismapped())
        self.assertEqual(self.ws.details_btn.cget("text"), "▸ Details")
        self.ws.toggle_details()
        self._settle()
        self.assertTrue(self.ws.details_text.winfo_ismapped())
        self.assertEqual(self.ws.details_btn.cget("text"), "▾ Details")
        self.ws.toggle_details()
        self._settle()
        self.assertFalse(self.ws.details_text.winfo_ismapped())

    def test_the_selected_nets_report_and_bandwidth_block(self):
        self._two_nets()
        lines = self.ws.details_lines()
        self.assertIn("Trace model (single-ended):  IN -> OUT", lines)
        self.assertTrue(any("-3 dB" in l for l in lines))
        self.assertEqual(self.ws.details_text.get("1.0", "end-1c"),
                         "\n".join(lines))
        self._type(0, "in_p", "1,2")
        self.assertTrue(self.ws.details_lines()[0].startswith("STALE:"))


class TestSession(_WsCase):

    def test_defaults_write_nothing(self):
        self.assertEqual(self.ws.state_get(), {})
        self.assertNotIn("trace", self.app._workspaces_state())

    def test_state_shape_and_round_trip(self):
        self.ws.set_rows([P, NetRow("d", "1", "2", "3", "4")])
        self.ws.gnd_var.set("4")
        self.ws.freq_var.set("0.25")
        self.ws.src_var.set("5")
        self.ws.load_var.set("30")
        self.ws.calculate_all()
        state = self.ws.state_get()
        self.assertEqual(state, {
            "file": self.fe.label,
            "rows": [{"name": "p", "in_p": "1", "in_n": "", "out_p": "3",
                      "out_n": ""},
                     {"name": "d", "in_p": "1", "in_n": "2", "out_p": "3",
                      "out_n": "4"}],
            "gnd": "4", "freq_ghz": "0.25", "src_ohm": "5", "load_ff": "30"})
        self.assertNotIn("results", json.dumps(state))

        from pkg_rlc.services.session import session_from_dict, session_to_dict
        d = session_to_dict(self.app.files, self.app.traces, {}, {},
                            workspaces=self.app._workspaces_state())
        self.assertEqual(d["workspaces"]["active"], "trace")
        self.assertEqual(d["workspaces"]["trace"], state)
        sess = session_from_dict(json.loads(json.dumps(d)))

        self.ws.state_set({})
        self.assertEqual(self.ws.rows(), [])
        from pkg_rlc.panels.workspaces import apply_workspaces_session_state
        notes = apply_workspaces_session_state(self.app.workspaces,
                                               sess.workspaces)
        self.assertEqual(notes, [])
        self.assertEqual(self.ws.state_get(), state)
        self.assertEqual(self.ws.file_var.get(), self.fe.label)
        # Results are not saved: every row reads "not calculated yet".
        for line in self.ws.summary_lines()[1:]:
            self.assertIn("stale: not calculated yet", line)

    def test_a_garbled_rows_list_costs_only_the_rows(self):
        self.ws.state_set({"file": self.fe.label, "rows": "nope",
                           "gnd": "2", "freq_ghz": "0.3"})
        self.assertEqual(self.ws.rows(), [])
        self.assertEqual(self.ws.gnd_var.get(), "2")
        self.assertEqual(self.ws.freq_var.get(), "0.3")
        self.ws.state_set({"rows": [{"name": "p", "in_p": "1", "out_p": "3"},
                                    "junk"]})
        self.assertEqual(self.ws.rows(), [P])

    def test_the_apps_session_writer_carries_the_block(self):
        self.ws.set_rows([P])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            self.app._write_session(path, None)
            with open(path, encoding="utf-8") as fh:
                block = json.load(fh)["workspaces"]
        self.assertEqual(block["active"], "trace")
        self.assertEqual(block["trace"]["rows"], self.ws.state_get()["rows"])


class TestFileChanges(_WsCase):

    def test_removing_the_file_empties_the_combobox_and_marks_rows_stale(self):
        self._two_nets()
        self.app.files_lb.selection_set(0)
        self.app._files_panel._on_remove_file()
        self._settle()
        self.assertEqual(self.ws.file_var.get(), "")
        self.assertEqual(tuple(self.ws.file_cbo.cget("values") or ()), ())
        self.assertEqual([r.status for r in self.ws.results], ["stale", "stale"])
        self.assertEqual(self.ws.rows(), [P, N], "the table is kept")
        # The range check is off with no file; the cells are not red.
        self.assertEqual(str(self.ws.table.data_row_widget(0, "out_p").cget(
            "foreground")), "")
        self.ws.calculate_all()
        self.assertIn("Load a Touchstone file", self.ws.status_lbl.cget("text"))

    def test_removing_another_file_keeps_the_chosen_one(self):
        second = _load("decap_4port.s4p")
        self.app.files.append(second)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.assertEqual(tuple(self.ws.file_cbo.cget("values")),
                         (self.fe.label, second.label))
        self.ws.file_var.set(second.label)
        self.ws._on_file_changed()
        self._two_nets()
        exp = solve_net(second.ts.freqs, second.Y, 4, P, [], F_HZ, second.label)
        self.assertTrue(np.array_equal(self.ws.results[0].Z2, exp.Z2,
                                       equal_nan=True))
        self.assertEqual(self.ws.results[0].signature, exp.signature)
        self.app.files_lb.selection_set(0)
        self.app._files_panel._on_remove_file()
        self._settle()
        self.assertEqual(self.ws.file_var.get(), second.label)
        self.assertEqual([r.status for r in self.ws.results], ["ok", "ok"])

    def test_a_file_added_later_shows_up_and_the_files_list_selection_is_the_default(self):
        self.app.files_lb.selection_set(0)
        self.app._files_panel._on_remove_file()
        self._settle()
        self.assertEqual(self.ws.file_var.get(), "")
        second = _load("decap_4port.s4p")
        self.app.files.append(_load("diff_pair_4port.s4p"))
        self.app.files.append(second)
        self.app._refresh_file_list()
        self.app.files_lb.selection_set(1)
        self.app._refresh_file_combobox()
        self.assertEqual(self.ws.file_var.get(), second.label)
        self.assertEqual(self.ws.table.column_values("in_p"),
                         tuple(port_choices(second.ts)))

    def test_clear_all_does_not_crash_the_workspace(self):
        self._two_nets()
        with mock.patch.object(messagebox, "askyesno", return_value=True):
            self.app._on_clear_all()
        self._settle()
        self.assertEqual(self.ws.file_var.get(), "")
        self.assertEqual([r.status for r in self.ws.results], ["stale", "stale"])


class TestExport(_WsCase):

    def test_export_writes_the_csv_rows(self):
        self._two_nets()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nets.csv")
            self.assertEqual(self.ws.export_csv(path), path)
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        exp = csv_rows(self.ws.results)
        self.assertEqual(rows[0], exp[0])
        self.assertEqual(rows[1], [str(v) for v in exp[1]])
        self.assertEqual(float(rows[1][4]), self.ws.results[0].model.series.R_ohm)
        self.assertIn("Exported 2 net(s)", self.ws.status_lbl.cget("text"))


class TestTheSwitchStillHandsThePlotItsKeys(_WsCase):

    def test_a_round_trip_through_the_workspace_focuses_the_plot_canvas(self):
        self._map()
        self.ws.gnd_entry.focus_set()
        self._settle()
        self.app.show_workspace("rlc")
        self._settle()
        self.assertEqual(str(self.app.tk.call("focus", "-lastfor", self.app)),
                         str(self.app.plot.canvas.get_tk_widget()))


if __name__ == "__main__":
    unittest.main()
