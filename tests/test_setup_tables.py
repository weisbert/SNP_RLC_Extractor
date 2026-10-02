"""
The shared SETUP COMPONENT: `pkg_rlc.panels.setup_tables`.

`docs/design_workspaces.md` § 4.1 / § 8 item 2, stage 3.  `ConnectionsTable`
replaces the Trace model workspace's GND field and `SetupTables` is the
Compare files workspace's port setup, so both are tested here on their own,
in a bare Tk root with no App:

  * get / set round trip, including the fields a careless copy loses (the
    on/off switch, a short's net name, R/L/C), and that `set` is not an edit
    (no `on_change`) while a keystroke is;
  * the cells are PAINTED from the probe rules (`probe_rule_issues`) and the
    connection rows' own parse -- red for refused, amber for computed-but-
    read-this -- and a fixed cell goes back to normal; never a dialog;
  * the Template box: fills an empty table without asking, asks
    (`messagebox.askyesno`) before replacing typed rows, and does nothing
    when the answer is no;
  * `set_editable(False)` greys every cell, both '+ Add' buttons and the
    Template box, refuses a template, and `True` puts the box back READONLY.

A few pure helpers (choice normalisation, the issue lines, the session
rows) are at the top and need no display.  Not in `FAST_MODULES`: it
imports tkinter.
"""

from __future__ import annotations

import pathlib
import sys
import tkinter as tk
import unittest
from tkinter import messagebox
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pkg_rlc.panels.setup_tables as st  # noqa: E402
from pkg_rlc.panels.setup_tables import (  # noqa: E402
    ERROR_FG, WARN_CELL_FG, ConnectionsTable, SetupTables,
    conn_rows_from_json, issue_text, mports_from_json, port_text_from_choice,
    rows_to_json,
)
from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow, MeasPortRow, SpecIssue, probe_rule_issues,
)
from pkg_rlc.present.conntable import (  # noqa: E402
    CONN_OPEN_NOTE, TEMPLATE_BETWEEN, TEMPLATE_COUPLING, TEMPLATE_PROMPT,
    TEMPLATE_SHORTED_LOOP, template_rows,
)


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:                                       # noqa: BLE001
        return False


TK_OK = _tk_ok()


# ============================================================================
# Pure
# ============================================================================

class TestPure(unittest.TestCase):

    def test_the_panel_never_imports_the_app(self):
        src = pathlib.Path(st.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(import|from)\s+pkg_rlc\.frontend")

    def test_a_picked_choice_is_its_number(self):
        self.assertEqual(port_text_from_choice("12  VDD_bal"), "12")
        self.assertEqual(port_text_from_choice("6-14"), "6-14")
        self.assertEqual(port_text_from_choice(" 3 "), "3")

    def test_issue_text_names_table_row_and_cell_errors_first(self):
        issues = [SpecIssue("conn", 1, "R", "warning", "w1"),
                  SpecIssue("mports", 0, "plus", "error", "e1"),
                  SpecIssue("conn", 0, "ports", "error", "e2")]
        self.assertEqual(issue_text(issues).split("\n"), [
            "Measurement port row 1, + ports: e1",
            "Connection row 1, Port: e2",
            "Connection row 2, R: warning: w1"])

    def test_session_rows_round_trip_and_keep_the_switch(self):
        conn = [ConnectionRow("short", "3,4", net="tap", enabled=False),
                ConnectionRow("rlc_gnd", "5", R="1", L="2n", C="3p")]
        mp = [MeasPortRow("P1", "1", "2")]
        self.assertEqual(conn_rows_from_json(rows_to_json(conn)), conn)
        self.assertEqual(mports_from_json(rows_to_json(mp)), mp)
        # The session reader's rule: "false" is False, not a truthy string.
        got = conn_rows_from_json([{"kind": "ground", "ports": "2",
                                    "enabled": "false"}])
        self.assertFalse(got[0].enabled)
        notes = []
        self.assertEqual(conn_rows_from_json("nope", notes.append), [])
        self.assertEqual(len(notes), 1)
        self.assertEqual(conn_rows_from_json(None), [])


# ============================================================================
# Tk
# ============================================================================

@unittest.skipUnless(TK_OK, "no display")
class _TkCase(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.changes = 0
        self._dialogs = {}
        for name in ("showinfo", "showwarning", "showerror"):
            p = mock.patch.object(messagebox, name)
            self._dialogs[name] = p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        for d in self._dialogs.values():
            d.assert_not_called()
        self.root.destroy()

    def _count(self):
        self.changes += 1

    def _settle(self):
        for _ in range(2):
            self.root.update_idletasks()
            self.root.update()

    @staticmethod
    def _fg(w) -> str:
        return str(w.cget("foreground"))

    @staticmethod
    def _type(w, text):
        w.delete(0, "end")
        w.insert(0, text)


class TestConnectionsTable(_TkCase):

    def setUp(self):
        super().setUp()
        self.ct = ConnectionsTable(self.root, on_change=self._count,
                                   caption="Other ports:")
        self.ct.pack()

    def test_get_set_round_trip_keeps_every_field(self):
        rows = [ConnectionRow("ground", "6-14"),
                ConnectionRow("short", "3,4", net="tap"),
                ConnectionRow("rlc_between", "1", "2", R="50", L="1n"),
                ConnectionRow("rlc_gnd", "5", C="2p", enabled=False),
                ConnectionRow("vdd", "7"), ConnectionRow("open", "8")]
        self.ct.set_rows(rows)
        self.assertEqual(self.ct.get_rows(), rows)
        self.assertEqual(self.changes, 0, "set_rows is not an edit")

    def test_a_legacy_two_field_short_reads_back_as_one_group(self):
        self.ct.set_rows([ConnectionRow("short", "5", "6,7")])
        self.assertEqual(self.ct.get_rows(),
                         [ConnectionRow("short", "5,6,7", "")])

    def test_a_keystroke_is_an_edit_and_a_picked_name_is_a_number(self):
        self.ct.set_rows([ConnectionRow("ground", "1")])
        self._type(self.ct.data_row_widget(0, "ports"), "4  out_n")
        self.assertGreater(self.changes, 0)
        self.assertEqual(self.ct.get_rows(), [ConnectionRow("ground", "4")])

    def test_the_caption_and_the_open_line_are_fixed_text(self):
        self.assertEqual(self.ct.note_lbl.cget("text"), CONN_OPEN_NOTE)
        self.assertEqual(self.ct.caption_lbl.cget("text"), "Other ports:")
        self.assertEqual(self.ct.note_lbl.winfo_manager(), "pack")
        # They ride in the '+ Add' row: no height of their own.
        self.assertIs(self.ct.note_lbl.master, st.table_head(self.ct.table))

    def test_port_choices_put_merged_nodes_first(self):
        self.ct.set_port_choices(["1", "2", "3", "4"])
        self.assertEqual(self.ct.column_values("ports"), ("1", "2", "3", "4"))
        self.ct.set_rows([ConnectionRow("short", "3,4", net="tap")])
        self.assertEqual(self.ct.column_values("ports"),
                         ("tap", "1", "2", "3", "4"))
        self.assertEqual(self.ct.column_values("to"),
                         ("tap", "1", "2", "3", "4"))

    def test_paint_red_amber_and_back(self):
        self.ct.set_rows([ConnectionRow("ground", "x"),
                          ConnectionRow("rlc_gnd", "2")])
        self.ct.paint([SpecIssue("conn", 0, "ports", "error", "bad"),
                       SpecIssue("conn", 1, "R", "warning", "no value"),
                       SpecIssue("mports", 0, "ports", "error", "not mine")])
        self.assertEqual(self._fg(self.ct.data_row_widget(0, "ports")),
                         ERROR_FG)
        self.assertEqual(self._fg(self.ct.data_row_widget(1, "R")),
                         WARN_CELL_FG)
        self.ct.paint([])
        self.assertEqual(self._fg(self.ct.data_row_widget(0, "ports")), "")
        self.assertEqual(self._fg(self.ct.data_row_widget(1, "R")), "")

    def test_red_wins_over_amber_and_a_hidden_cell_falls_back_to_port(self):
        self.ct.set_rows([ConnectionRow("ground", "2")])
        self.ct.paint([SpecIssue("conn", 0, "ports", "error", "e"),
                       SpecIssue("conn", 0, "ports", "warning", "w"),
                       # A ground row has no To cell on screen.
                       SpecIssue("conn", 0, "to", "warning", "w")])
        self.assertEqual(self._fg(self.ct.data_row_widget(0, "ports")),
                         ERROR_FG)
        # ... and NOT the row's first cell, the on/off glyph.
        toggle = self.ct.data_row_widget(0, "enabled")
        self.assertNotIn(self._fg(toggle), (ERROR_FG, WARN_CELL_FG))

    def test_set_editable(self):
        self.ct.set_rows([ConnectionRow("ground", "2")])
        self.ct.set_editable(False)
        self.assertTrue(self.ct.data_row_widget(0, "ports").instate(
            ["disabled"]))
        self.assertTrue(self.ct.table._add_btn.instate(["disabled"]))
        self.ct.set_editable(True)
        self.assertFalse(self.ct.data_row_widget(0, "ports").instate(
            ["disabled"]))


class TestSetupTables(_TkCase):

    def setUp(self):
        super().setUp()
        self.st = SetupTables(self.root, on_change=self._count)
        self.st.pack()
        self.st.set_nports(4)

    def _mp(self, i, col):
        return self.st.mp_table.data_row_widget(i, col)

    def _cn(self, i, col):
        return self.st.conn.data_row_widget(i, col)

    # ---------------------------------------------------------- get / set

    def test_get_set_round_trip(self):
        mports = [MeasPortRow("tank", "1", "2"), MeasPortRow("vco", "3", "")]
        conn = [ConnectionRow("short", "3,4", net="t", enabled=False),
                ConnectionRow("rlc_gnd", "2", R="5m")]
        self.st.set(mports, conn)
        self.assertEqual(self.st.get(), (mports, conn))
        self.assertEqual(self.changes, 0, "set is not an edit")
        self.assertFalse(self.st.is_empty())
        self.st.set([], [])
        self.assertEqual(self.st.get(), ([], []))
        self.assertTrue(self.st.is_empty())

    def test_a_keystroke_fires_on_change_once_per_write(self):
        self.st.set([MeasPortRow("P1", "1", "")], [])
        self._type(self._mp(0, "plus"), "2")
        self.assertGreaterEqual(self.changes, 1)
        self.assertEqual(self.st.get()[0], [MeasPortRow("P1", "2", "")])

    # ------------------------------------------------------------ painting

    def test_the_cells_are_painted_from_probe_rule_issues(self):
        mports = [MeasPortRow("P1", "1", "2"), MeasPortRow("P2", "3", "4")]
        conn = [ConnectionRow("ground", "1,4")]
        self.st.set(mports, conn)
        # The L0 checker is the source: one red '+' cell, one amber '-'.
        rules = probe_rule_issues(mports, conn, "", nports=4)
        self.assertEqual(sorted((i.row, i.column, i.severity) for i in rules),
                         [(0, "plus", "error"), (1, "minus", "warning")])
        self.assertEqual(self._fg(self._mp(0, "plus")), ERROR_FG)
        self.assertEqual(self._fg(self._mp(1, "minus")), WARN_CELL_FG)
        self.assertEqual(self._fg(self._mp(0, "minus")), "")
        self.assertEqual(self._fg(self._mp(1, "plus")), "")
        self.assertEqual([(i.table, i.row, i.column) for i in self.st.issues()],
                         [(i.table, i.row, i.column) for i in rules])
        self.assertTrue(self.st.has_errors())
        text = self.st.issues_lbl.cget("text")
        self.assertTrue(text.startswith("Measurement port row 1, + ports: "
                                        "Port 1 is on the '+' side"), text)
        self.assertEqual(str(self.st.issues_lbl.cget("foreground")), ERROR_FG)

    def test_fixing_a_cell_by_typing_puts_its_colour_back(self):
        self.st.set([MeasPortRow("P1", "1", "")], [ConnectionRow("ground", "1")])
        self.assertEqual(self._fg(self._mp(0, "plus")), ERROR_FG)
        self._type(self._cn(0, "ports"), "3")
        self.assertEqual(self._fg(self._mp(0, "plus")), "")
        self.assertEqual(self.st.issues(), [])
        self.assertEqual(self.st.issues_lbl.cget("text"), "")

    def test_a_connection_row_that_does_not_parse_is_red_on_its_cell(self):
        self.st.set([MeasPortRow("P1", "1", "")],
                    [ConnectionRow("ground", "3"),
                     ConnectionRow("rlc_between", "2", "3,4", L="1n")])
        self.assertEqual(self._fg(self._cn(1, "to")), ERROR_FG)
        self.assertEqual(self._fg(self._cn(0, "ports")), "")
        self.assertIn("Connection row 2, To: lumped_between takes exactly ONE "
                      "partner port", self.st.issues_lbl.cget("text"))

    def test_a_measurement_cell_that_does_not_parse_is_red(self):
        self.st.set([MeasPortRow("P1", "x", "")], [])
        self.assertEqual(self._fg(self._mp(0, "plus")), ERROR_FG)
        self.assertIn("first token 'x'", self.st.issues_lbl.cget("text"))
        # A node an earlier short row names is not a false alarm.
        self.st.set([MeasPortRow("P1", "tap", "")],
                    [ConnectionRow("short", "3,4", net="tap")])
        self.assertEqual(self.st.issues(), [])

    def test_set_nports_moves_the_range_check(self):
        self.st.set([MeasPortRow("P1", "9", "")], [])
        self.assertEqual(self._fg(self._mp(0, "plus")), ERROR_FG)
        self.assertIn("not in this file (4 ports)",
                      self.st.issues_lbl.cget("text"))
        self.st.set_nports(None)
        self.assertEqual(self._fg(self._mp(0, "plus")), "")
        self.st.set_nports(12, ["1  a", "2  b"])
        self.assertEqual(self.st.conn.column_values("ports"), ("1  a", "2  b"))
        self.assertEqual(self.st.nports, 12)

    def test_a_warning_alone_is_amber(self):
        self.st.set([MeasPortRow("P1", "1", "2")], [ConnectionRow("ground", "2")])
        self.assertFalse(self.st.has_errors())
        self.assertEqual(str(self.st.issues_lbl.cget("foreground")),
                         WARN_CELL_FG)

    # ------------------------------------------------------------ template

    def test_a_template_fills_an_empty_table_without_asking(self):
        with mock.patch.object(messagebox, "askyesno") as ask:
            self.assertTrue(self.st.apply_template(TEMPLATE_SHORTED_LOOP))
        ask.assert_not_called()
        self.assertEqual(self.st.get(), template_rows(TEMPLATE_SHORTED_LOOP, 4))
        self.assertEqual(self.changes, 1, "a template is an edit")

    def test_the_combobox_route_goes_back_to_the_prompt(self):
        self.st.template_var.set(TEMPLATE_COUPLING)
        with mock.patch.object(messagebox, "askyesno") as ask:
            self.st._on_template_selected()
        ask.assert_not_called()
        self.assertEqual(self.st.template_var.get(), TEMPLATE_PROMPT)
        self.assertEqual(self.st.get(), template_rows(TEMPLATE_COUPLING, 4))

    def test_over_typed_rows_it_asks_and_no_means_no(self):
        typed = ([MeasPortRow("mine", "3", "")], [ConnectionRow("ground", "4")])
        self.st.set(*typed)
        with mock.patch.object(messagebox, "askyesno",
                               return_value=False) as ask:
            self.assertFalse(self.st.apply_template(TEMPLATE_BETWEEN))
        ask.assert_called_once()
        self.assertIn("'Between two ports'", ask.call_args[0][1])
        self.assertEqual(self.st.get(), typed)
        self.assertEqual(self.changes, 0)
        with mock.patch.object(messagebox, "askyesno", return_value=True):
            self.assertTrue(self.st.apply_template(TEMPLATE_BETWEEN))
        self.assertEqual(self.st.get(), template_rows(TEMPLATE_BETWEEN, 4))

    def test_the_template_respects_the_file_size(self):
        self.st.set_nports(2)
        self.st.apply_template(TEMPLATE_SHORTED_LOOP)
        mports, conn = self.st.get()
        self.assertEqual(mports, [MeasPortRow("P1", "1", "2")])
        # A one-port short ties nothing, so its cell is left to fill; an
        # all-blank row is dropped by get().
        self.assertEqual(conn, [])

    # ------------------------------------------------------------ editable

    def test_set_editable_greys_everything_and_refuses_a_template(self):
        self.st.set([MeasPortRow("P1", "1", "")], [ConnectionRow("ground", "3")])
        self.st.set_editable(False)
        self.assertTrue(self._mp(0, "plus").instate(["disabled"]))
        self.assertTrue(self._cn(0, "ports").instate(["disabled"]))
        self.assertTrue(self.st.mp_table._add_btn.instate(["disabled"]))
        self.assertTrue(self.st.conn.table._add_btn.instate(["disabled"]))
        self.assertTrue(self.st.template_cbo.instate(["disabled"]))
        with mock.patch.object(messagebox, "askyesno") as ask:
            self.assertFalse(self.st.apply_template(TEMPLATE_BETWEEN))
        ask.assert_not_called()
        self.assertEqual(self.st.get()[0], [MeasPortRow("P1", "1", "")])
        self.st.set_editable(True)
        self.assertFalse(self._mp(0, "plus").instate(["disabled"]))
        self.assertFalse(self.st.template_cbo.instate(["disabled"]))
        self.assertTrue(self.st.template_cbo.instate(["readonly"]),
                        "the Template box comes back READONLY")


if __name__ == "__main__":
    unittest.main()
