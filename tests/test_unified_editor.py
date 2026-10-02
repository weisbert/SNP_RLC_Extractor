"""
The ONE editor (docs/design_workspaces.md § 3, stage 2): no modes.

Every trace is a measurement-port table and a connections table.  The five
mode radios became the Template combobox, which FILLS those two tables and is
then forgotten; the Port A / Port B / Short Pairs / GND fields are gone, so
grounding is a connections row and nothing else; the probe rules
(`probe_rule_issues`) colour the cell they are about -- red for a refusal,
amber for "computed, but read this" -- and say why in the validation strip.

Driven through the real App and its real widgets, the way the other editor
tests are.  Every test here was mutation-checked: the mutation that makes it
fail is named in its docstring.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc.frontend.app as pkg_rlc_gui  # noqa: E402
import pkg_rlc.panels.panels_editor as panels_editor  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    parse_touchstone,
)
from pkg_rlc.frontend.app import App, FileEntry, TraceConfig  # noqa: E402
from pkg_rlc.present.conntable import (  # noqa: E402
    CONN_OPEN_NOTE,
    EDITOR_TEMPLATES,
    TEMPLATE_BETWEEN,
    TEMPLATE_COUPLING,
    TEMPLATE_PORT_TO_GND,
    TEMPLATE_PROMPT,
    TEMPLATE_SHORTED_LOOP,
    template_rows,
)
from pkg_rlc.services.session import (  # noqa: E402
    session_from_dict,
    session_to_dict,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"
FIXTURE_2PORT = FIX / "coupled_2port_gndref.s2p"


def _ensure_fixtures() -> None:
    if FIXTURE.exists():
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


def _mp(table) -> list:
    return [(r.name, r.plus, r.minus) for r in table.get_rows()]


def _conn(table) -> list:
    return [(r.kind, r.ports) for r in table.get_rows()]


# ============================================================================
# Pure: what each template writes
# ============================================================================

class TestTemplateRows(unittest.TestCase):
    """`template_rows` is the whole of what a template IS -- no Tk needed."""

    def test_the_four_templates_in_the_order_the_combobox_lists_them(self):
        self.assertEqual(EDITOR_TEMPLATES,
                         (TEMPLATE_PORT_TO_GND, TEMPLATE_BETWEEN,
                          TEMPLATE_SHORTED_LOOP, TEMPLATE_COUPLING))

    def test_a_port_the_file_does_not_have_is_left_blank(self):
        """Mutation: drop the nports filter in _ports_that_exist -> a 2-port
        file gets a short row over ports 3,4 it does not have."""
        mports, conn = template_rows(TEMPLATE_SHORTED_LOOP, 2)
        self.assertEqual([(r.plus, r.minus) for r in mports], [("1", "2")])
        self.assertEqual([(r.kind, r.ports) for r in conn], [("short", "")])
        mports, _ = template_rows(TEMPLATE_BETWEEN, 1)
        self.assertEqual([(r.plus, r.minus) for r in mports], [("1", "")])

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(KeyError):
            template_rows("Custom (advanced)", 4)


# ============================================================================
# The editor
# ============================================================================

class _Case(unittest.TestCase):
    """An App with the 4-port fixture and one migrated trace, selected."""

    FILE = FIXTURE

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(self.FILE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = self.app._make_default_trace(self.fe)
        self.tc.label = "t1"
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self._select(0)

    def tearDown(self):
        self.app.destroy()

    def _select(self, idx=0):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _load(self, mports, conn=()):
        """Put rows on the selected trace and reload the editor from it."""
        self.tc.mports = list(mports)
        self.tc.conn_rows = list(conn)
        self._select(0)

    def _log(self) -> str:
        return self.app.results_text.get("1.0", tk.END)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestNoModeWidgetsExist(_Case):
    def test_the_mode_radios_and_the_four_fields_are_gone(self):
        """Mutation: put back `self.ed_gnd = PlaceholderEntry(...)` (or any
        Radiobutton) in _build_editor_form -> fails."""
        for name in ("ed_mode_var", "_ed_mode_buttons", "ed_porta",
                     "ed_portb", "ed_short", "ed_gnd", "ed_porta_lbl",
                     "ed_gnd_lbl", "_on_mode_changed",
                     "_update_mode_visibility", "_strips_wanted"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.app, name), name)
                self.assertFalse(hasattr(self.app._editor_panel, name), name)
        self.assertFalse(hasattr(pkg_rlc_gui, "MODE_PLACEHOLDERS"))
        self.assertFalse(hasattr(panels_editor, "MODE_PLACEHOLDERS"))

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)
        radios = [w for w in walk(self.app._ed_form)
                  if w.winfo_class() == "TRadiobutton"]
        self.assertEqual(radios, [])

    def test_both_tables_and_every_strip_are_always_shown(self):
        """Mutation: grid_remove() the connections table (the old mode-5
        gate) -> fails, on a one-probe trace and on a two-probe one."""
        for mports in ([MeasPortRow("P1", "1", "")],
                       [MeasPortRow("P1", "1", ""),
                        MeasPortRow("P2", "2", "")]):
            with self.subTest(n=len(mports)):
                self._load(mports)
                for name in ("ed_template_cbo", "ed_mp_table", "ed_mp_hint",
                             "ed_conn_head", "ed_conn_table", "ed_conn_hint",
                             "ed_open_note", "ed_overview", "ed_validation"):
                    self.assertTrue(getattr(self.app, name).grid_info(),
                                    name)
                self.assertEqual(
                    self.app.ed_footer_strip.winfo_manager(), "pack")
        self.assertEqual(self.app.ed_open_note.cget("text"), CONN_OPEN_NOTE)
        self.assertEqual(CONN_OPEN_NOTE, "Ports not listed anywhere are OPEN.")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTemplates(_Case):
    EXPECTED = {
        TEMPLATE_PORT_TO_GND: ([("P1", "1", "")], []),
        TEMPLATE_BETWEEN: ([("P1", "1", "2")], []),
        TEMPLATE_SHORTED_LOOP: ([("P1", "1", "2")], [("short", "3,4")]),
        TEMPLATE_COUPLING: ([("P1", "1", ""), ("P2", "2", "")], []),
    }

    def _choose(self, name):
        self.app.ed_template_var.set(name)
        self.app.ed_template_cbo.event_generate("<<ComboboxSelected>>")
        self._settle()

    def test_each_template_fills_the_expected_rows(self):
        """Mutation: swap the minus side of TEMPLATE_BETWEEN to '' in
        template_rows -> fails; route _on_template_selected to nothing ->
        fails."""
        for name, (mp, conn) in self.EXPECTED.items():
            with self.subTest(template=name):
                # Empty the tables first, so no confirmation is involved.
                self.app.ed_mp_table.set_rows([])
                self.app.ed_conn_table.set_rows([])
                self._choose(name)
                self.assertEqual(_mp(self.app.ed_mp_table), mp)
                self.assertEqual(_conn(self.app.ed_conn_table), conn)
                # What you then see IS the table: it reached the trace.
                self.assertEqual([(r.name, r.plus, r.minus)
                                  for r in self.tc.mports], mp)
                # And the combobox is back at its prompt.
                self.assertEqual(self.app.ed_template_var.get(),
                                 TEMPLATE_PROMPT)

    def test_the_tools_label_follows_the_template_a_typed_one_does_not(self):
        from pkg_rlc.model.trace import default_trace_label
        self.tc.label = default_trace_label(self.fe.label)
        self._select(0)
        with mock.patch.object(panels_editor.messagebox, "askyesno",
                               return_value=True):
            self._choose(TEMPLATE_BETWEEN)
            self.app._flush_editor_sync()
            self.assertEqual(self.tc.label,
                             default_trace_label(self.fe.label, "p1_vs_p2"))
            self.app.ed_label.set_value("mine")
            self.app._flush_editor_sync()
            self._choose(TEMPLATE_PORT_TO_GND)
            self.app._flush_editor_sync()
        self.assertEqual(self.tc.label, "mine")

    def test_an_empty_table_is_filled_without_asking(self):
        """Mutation: drop the `self._tables_have_rows() and` guard so it
        always asks -> fails."""
        self.app.ed_mp_table.set_rows([])
        self.app.ed_conn_table.set_rows([])
        with mock.patch.object(panels_editor.messagebox, "askyesno",
                               side_effect=AssertionError("asked")):
            self._choose(TEMPLATE_BETWEEN)
        self.assertEqual(_mp(self.app.ed_mp_table), [("P1", "1", "2")])

    def test_a_non_empty_table_asks_and_no_leaves_it_untouched(self):
        """Mutation: ignore askyesno's answer -> 'no' overwrites -> fails."""
        self._load([MeasPortRow("tank", "3", "4")],
                   [ConnectionRow(kind="ground", ports="1")])
        with mock.patch.object(panels_editor.messagebox, "askyesno",
                               return_value=False) as ask:
            self._choose(TEMPLATE_COUPLING)
        self.assertEqual(ask.call_count, 1)
        self.assertIn("Several nets (coupling)", ask.call_args[0][1])
        self.assertEqual(_mp(self.app.ed_mp_table), [("tank", "3", "4")])
        self.assertEqual(_conn(self.app.ed_conn_table), [("ground", "1")])
        self.assertEqual([(r.name, r.plus) for r in self.tc.mports],
                         [("tank", "3")])
        self.assertEqual(self.app.ed_template_var.get(), TEMPLATE_PROMPT)

        with mock.patch.object(panels_editor.messagebox, "askyesno",
                               return_value=True):
            self._choose(TEMPLATE_COUPLING)
        self.assertEqual(_mp(self.app.ed_mp_table),
                         [("P1", "1", ""), ("P2", "2", "")])
        self.assertEqual(_conn(self.app.ed_conn_table), [])

    def test_a_two_port_file_gets_a_blank_cell_not_a_port_it_lacks(self):
        """The short row is there (its Kind says what goes in it) with its
        port cell empty.  Mutation: as TestTemplateRows."""
        fe2 = FileEntry(parse_touchstone(FIXTURE_2PORT))
        self.app.files.append(fe2)
        self.app._refresh_file_combobox()
        self.app.ed_file_var.set(fe2.label)
        self.app.ed_mp_table.set_rows([])
        self.app.ed_conn_table.set_rows([])
        self._choose(TEMPLATE_SHORTED_LOOP)
        self.assertEqual(_mp(self.app.ed_mp_table), [("P1", "1", "2")])
        self.assertEqual(_conn(self.app.ed_conn_table), [])
        kinds = [e["_vars"]["kind"].get()
                 for e in self.app.ed_conn_table._rows]
        self.assertEqual(kinds, ["short"])

    def test_a_frozen_trace_refuses_a_template(self):
        """Mutation: drop the frozen guard in apply_template -> fails.  The
        confirmation is answered YES, so the guard is the only thing in the
        way (a 'no' would hide its absence -- caught by that mutation)."""
        self.tc.frozen = True
        self._load([MeasPortRow("tank", "3", "4")])
        with mock.patch.object(panels_editor.messagebox, "askyesno",
                               return_value=True):
            self.assertFalse(self.app.apply_template(TEMPLATE_BETWEEN))
        self.assertEqual(_mp(self.app.ed_mp_table), [("tank", "3", "4")])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestProbeRulesInTheCells(_Case):
    def test_a_plus_port_in_a_ground_row_is_red_and_skipped(self):
        """
        '+1' and 'ground 1': the node is at 0 V, nothing to measure.  The plus
        cell is red, the strip says why, and Calculate skips the trace with
        the reason in the Log.  Mutation: skip _paint_probe_issues in
        _apply_editor_strips -> the cell stays black -> fails.
        """
        self._load([MeasPortRow("P1", "1", "")],
                   [ConnectionRow(kind="ground", ports="1")])
        cell = self.app.ed_mp_table.data_row_widget(0, "plus")
        self.assertEqual(str(cell.cget("foreground")),
                         panels_editor.ERROR_CELL_FG)
        self.assertEqual(panels_editor.ERROR_CELL_FG, "#b00020")
        strip = self.app.ed_validation.cget("text")
        self.assertIn("Port 1 is on the '+' side", strip)

        self.app.results_text.delete("1.0", tk.END)
        self.app._on_calculate()
        self._settle()
        self.assertIsNone(self.tc.Z)
        self.assertIsNone(self.tc.Zmat)
        log = self._log()
        self.assertIn("[1] t1: ERROR", log)
        self.assertIn("Port 1 is on the '+' side of 'P1'", log)

    def test_fixing_the_cell_takes_the_colour_back(self):
        """Mutation: do not reset self._ed_painted at the start of the pass
        -> the fixed cell stays red -> fails."""
        self._load([MeasPortRow("P1", "1", "")],
                   [ConnectionRow(kind="ground", ports="1")])
        self.app.ed_conn_table.set_rows([ConnectionRow(kind="ground",
                                                       ports="3")])
        self.app._refresh_editor_strips()
        self._settle()
        cell = self.app.ed_mp_table.data_row_widget(0, "plus")
        self.assertNotEqual(str(cell.cget("foreground")),
                            panels_editor.ERROR_CELL_FG)

    def test_a_minus_port_in_a_ground_row_is_amber_and_means_the_whole_side(
            self):
        """
        '+1 -3,4' with 'ground 3': the minus side is ONE node, so grounding
        port 3 grounds port 4 too -- this is P1 to GND, solved as such, and
        bit-identical to writing '+1' with 'ground 3,4'.  Mutation: drop
        fold_grounded_minus from build_terminations_rows -> the two Z differ
        (the old "ground wins" answer drops port 3 and leaves 4 floating).
        """
        self._load([MeasPortRow("P1", "1", "3,4")],
                   [ConnectionRow(kind="ground", ports="3")])
        cell = self.app.ed_mp_table.data_row_widget(0, "minus")
        self.assertEqual(str(cell.cget("foreground")),
                         panels_editor.WARN_CELL_FG)
        self.assertNotEqual(panels_editor.WARN_CELL_FG,
                            panels_editor.ERROR_CELL_FG)
        self.assertIn("side is grounded (port 3)",
                      self.app.ed_validation.cget("text"))

        ref = self.app._make_default_trace(self.fe)
        ref.mports = [MeasPortRow("P1", "1", "")]
        ref.conn_rows = [ConnectionRow(kind="ground", ports="3,4")]
        self.app.traces.append(ref)
        self.app._refresh_trace_list()
        self.app._on_calculate()
        self._settle()
        self.assertIsNotNone(self.tc.Z)
        self.assertTrue(np.array_equal(self.tc.Z, ref.Z, equal_nan=True))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSelfMutualOnlyWithTwoProbes(_Case):
    def _shown(self) -> dict:
        return {name: bool(getattr(self.app, name).grid_info())
                for name in ("ed_plot_self_cb", "ed_plot_mutual_cb",
                             "ed_mutual_hint")}

    def test_hidden_with_one_probe_shown_with_two(self):
        """Mutation: make _update_plot_choices compare `>= 1` -> fails on
        one probe; make it never grid() -> fails on two."""
        self._load([MeasPortRow("P1", "1", "")])
        self.assertEqual(set(self._shown().values()), {False})
        self.assertTrue(self.app.ed_enabled_cb.grid_info(), "this trace")

        # Typed, not loaded: the table's own on_change has to get there.
        self.app.ed_mp_table.add_row({"name": "P2", "plus": "2"})
        self._settle()
        self.assertEqual(set(self._shown().values()), {True})

        # A '-' side alone measures nothing, so it is not a second probe.
        self._load([MeasPortRow("P1", "1", ""), MeasPortRow("P2", "", "2")])
        self.assertEqual(set(self._shown().values()), {False})


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestOldSessionsLandInTheTables(_Case):
    """
    A session saved by a build that still had modes: every trace is moved
    into the two tables on load, and the editor shows the rows.  Mutation:
    drop the `_migrate_trace` loop in _apply_session AND the one in
    _on_trace_selected -> the tables come up empty -> fails.
    """

    OLD = [
        (dict(mode=1, port_a="1", gnd_ports="2-4"),
         [("P1", "1", "")], [("ground", "2-4")]),
        (dict(mode=2, port_a="1", port_b="2", gnd_ports="3"),
         [("P1", "1", "2")], [("ground", "3")]),
        (dict(mode=3, port_a="1", port_b="2", short_pairs="3-4"),
         [("P1", "1", "2")], [("short", "3,4")]),
        (dict(mode=6, mports=[MeasPortRow("pri", "1", "2"),
                              MeasPortRow("sec", "3", "4")]),
         [("pri", "1", "2"), ("sec", "3", "4")], []),
    ]

    def test_each_old_mode_shows_its_migrated_rows(self):
        old = [TraceConfig(id=i + 1, file_label=self.fe.label,
                           label=f"old{i + 1}", **kw)
               for i, (kw, _mp_, _cn) in enumerate(self.OLD)]
        for tc in old:
            self.assertEqual(tc.table_version, 0)
        data = session_to_dict(files=[self.fe], traces=old, controls={},
                               plot_state={})
        sess = session_from_dict(data)
        self.app._apply_session(sess, "old session")
        self._settle()
        self.assertEqual(len(self.app.traces), len(self.OLD))
        for i, (_kw, mp, conn) in enumerate(self.OLD):
            with self.subTest(mode=_kw["mode"]):
                self._select(i)
                self.assertEqual(_mp(self.app.ed_mp_table), mp)
                self.assertEqual(_conn(self.app.ed_conn_table), conn)
                self.assertEqual(self.app.traces[i].mode, 5)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheWindowWritesRows(_Case):
    def test_send_to_ground_and_probe_always_add_table_rows(self):
        """Ports & Roles' 'send to ground' / 'set as probe +' have one place
        to write now.  Mutation: route 'ground' anywhere but the connections
        table -> fails."""
        self.app.ed_mp_table.set_rows([])
        self.app.ed_conn_table.set_rows([])
        note = self.app.apply_ports_as("ground", [5, 6, 7])
        self.assertIn("connections row", note)
        note = self.app.apply_ports_as("probe+", [1, 2])
        self.assertIn("measurement-port row", note)
        self._settle()
        self.assertEqual(_conn(self.app.ed_conn_table), [("ground", "5-7")])
        self.assertEqual([(r.plus,) for r in self.app.ed_mp_table.get_rows()],
                         [("1-2",)])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTextImportRenamesLegacyA(_Case):
    def test_an_A_row_from_the_text_becomes_the_first_free_P_name(self):
        """'signal A / signal B' is the old two-group spelling; the table
        refuses 'A' as a name, so the import renames it -- meaning unchanged.
        Mutation: set_rows(mports) without _rename_legacy_a_rows -> fails."""
        self.app._import_text_into_tables(
            "1 signal A\n2 signal B\n3 ground\n")
        self._settle()
        self.assertEqual(_mp(self.app.ed_mp_table), [("P1", "1", "2")])
        self.assertEqual(_conn(self.app.ed_conn_table), [("ground", "3")])

        self.app._import_text_into_tables("1 signal P1 +\n2 signal A +\n")
        self._settle()
        self.assertEqual(_mp(self.app.ed_mp_table),
                         [("P1", "1", ""), ("P2", "2", "")])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestNoModeNumberInTheGui(_Case):
    def test_the_csv_header_names_the_setup_not_a_mode(self):
        """Mutation: put back `Mode: {tc.mode_name()}` -> fails."""
        import tempfile
        self._load([MeasPortRow("P1", "1", "")],
                   [ConnectionRow(kind="ground", ports="3,4")])
        self.app._on_calculate()
        self._settle()
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "out.csv")
            with mock.patch.object(pkg_rlc_gui.filedialog,
                                   "asksaveasfilename", return_value=path):
                self.app._on_export_csv()
            text = Path(path).read_text(encoding="utf-8")
        head = [ln for ln in text.splitlines() if ln.startswith("# File:")]
        self.assertEqual(len(head), 1, text[:300])
        self.assertIn(f"Setup: {self.tc.port_descriptor()}", head[0])
        self.assertNotIn("Mode", head[0])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheMinsizeViewport(_Case):
    """
    At the 1040x600 minsize the editor viewport was 20 px against a 23 px
    table row once the workspace strip landed: nothing of the form could be
    wholly on screen.  Measured after this stage: 53 px (see
    panels_editor.EditorPanel._build_editor).  Mutation: put Fit Model back
    on a row of its own in _build_global_controls -> 30 px... and the
    button/footer padding back too -> 22 px -> fails.
    """

    def test_the_viewport_holds_at_least_one_table_row(self):
        self.app.deiconify()
        self.app.geometry("1040x600")
        self._settle(6)
        row = self.app.ed_mp_table.data_row_widget(0, "plus")
        self.assertIsNotNone(row)
        need = max(row.winfo_height(), row.winfo_reqheight())
        self.assertGreaterEqual(need, 20)        # a real row, not 1 px
        self.assertGreaterEqual(self.app._ed_canvas.winfo_height(), need)


if __name__ == "__main__":
    unittest.main()


class TestTheToolsLabelFollowsTheTemplate(unittest.TestCase):
    """Stage-3 review: a new trace kept '<file>_p1_to_gnd' after a template
    made it a loop, so the Traces list, the legend and the results named a
    setup it no longer had.  The TOOL's label follows; a typed one stays."""

    def test_pure(self):
        from pkg_rlc.model.trace import (default_trace_label,
                                         is_default_trace_label,
                                         rebind_file_labels, TraceConfig)
        self.assertTrue(is_default_trace_label("a.s4p_p1_vs_p2", "a.s4p"))
        self.assertFalse(is_default_trace_label("mine", "a.s4p"))
        tc = TraceConfig(file_label="a.s4p",
                         label=default_trace_label("a.s4p", "coupling"))
        rebind_file_labels([tc], {"a.s4p": "b.s4p"})
        self.assertEqual(tc.label, "b.s4p_coupling")
