"""
Tests for the RowTable widget and the Mode 6 measurement-port table.

These drive real Tk widgets, so they skip cleanly where no display is
available (a headless CI box, an ssh session without X). What they can check
is the wiring -- add/delete/get/set, blank-row handling, the legacy field
migration, and that duplicating a trace does not share the row list. What they
cannot check is whether the thing looks right, which is why stage 3 of
docs/design_connection_table.md waits for a human.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from pkg_rlc_core import ConnectionRow, MeasPortRow  # noqa: E402
from pkg_rlc_gui import (  # noqa: E402
    ColumnSpec,
    RowTable,
    TraceConfig,
    _collect_mports,
    _duplicate_trace_config,
)


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestRowTable(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.table = RowTable(
            self.root,
            columns=(ColumnSpec("name", "Name", 9),
                     ColumnSpec("plus", "+ ports", 12),
                     ColumnSpec("minus", "− ports", 12)),
            row_factory=MeasPortRow,
            min_rows=1,
        )

    def tearDown(self):
        self.root.destroy()

    def test_starts_with_one_blank_row_that_means_nothing(self):
        """Somewhere to type, but an untouched table is empty."""
        self.assertEqual(self.table.get_rows(), [])

    def test_set_then_get_round_trips(self):
        rows = [MeasPortRow("tank", "1", "2"), MeasPortRow("vco", "5,7", "6,8")]
        self.table.set_rows(rows)
        got = self.table.get_rows()
        self.assertEqual([(r.name, r.plus, r.minus) for r in got],
                         [("tank", "1", "2"), ("vco", "5,7", "6,8")])

    def test_ranges_survive_as_typed(self):
        self.table.set_rows([MeasPortRow("shield", "5:1:12", "")])
        self.assertEqual(self.table.get_rows()[0].plus, "5:1:12")

    def test_add_row_appends(self):
        self.table.set_rows([MeasPortRow("tank", "1", "2")])
        self.table.add_row({"name": "vco", "plus": "3", "minus": "4"})
        self.assertEqual([r.name for r in self.table.get_rows()],
                         ["tank", "vco"])

    def test_blank_rows_are_dropped_from_get_rows(self):
        self.table.set_rows([MeasPortRow("tank", "1", "2")])
        self.table.add_row()
        self.assertEqual(len(self.table.get_rows()), 1)

    def test_delete_removes_only_that_row(self):
        self.table.set_rows([MeasPortRow("a", "1", "2"),
                             MeasPortRow("b", "3", "4"),
                             MeasPortRow("c", "5", "6")])
        # Delete the middle row through the same path the ✕ button uses.
        self.table._delete_row(self.table._rows[1])
        self.assertEqual([r.name for r in self.table.get_rows()], ["a", "c"])

    def test_deleting_everything_leaves_a_blank_row_to_type_into(self):
        self.table.set_rows([MeasPortRow("a", "1", "2")])
        self.table._delete_row(self.table._rows[0])
        self.assertEqual(self.table.get_rows(), [])
        self.assertEqual(len(self.table._rows), 1)

    def test_set_rows_replaces_rather_than_appends(self):
        self.table.set_rows([MeasPortRow("a", "1", "2")])
        self.table.set_rows([MeasPortRow("b", "3", "4")])
        self.assertEqual([r.name for r in self.table.get_rows()], ["b"])

    def test_connection_row_factory_works_too(self):
        """The widget is shared with the stage-3 connection table."""
        table = RowTable(
            self.root,
            columns=(ColumnSpec("kind", "Type", 10, kind="combo",
                                values=("ground", "short"), readonly_combo=True),
                     ColumnSpec("ports", "Port", 8),
                     ColumnSpec("to", "To", 8)),
            row_factory=ConnectionRow,
            min_rows=1,
        )
        table.set_rows([ConnectionRow(kind="ground", ports="5:12")])
        got = table.get_rows()
        self.assertEqual((got[0].kind, got[0].ports), ("ground", "5:12"))


class TestLegacyMportMigration(unittest.TestCase):
    """The retired mp1_*/mp2_*/mp_more fields fold into the table on load."""

    def test_two_structured_ports_migrate(self):
        tc = TraceConfig(mode=6, mp1_name="tank", mp1_plus="1", mp1_minus="2",
                         mp2_name="vco", mp2_plus="3", mp2_minus="4")
        self.assertTrue(tc.migrate_legacy_mports())
        self.assertEqual([(r.name, r.plus, r.minus) for r in tc.mports],
                         [("tank", "1", "2"), ("vco", "3", "4")])
        self.assertEqual(tc.mp1_plus, "")

    def test_more_ports_lines_migrate_and_keep_ranges(self):
        tc = TraceConfig(mode=6, mp1_plus="1", mp1_minus="2",
                         mp_more="vco = 5:1:8 / 9\nsense = 11 /\n")
        self.assertTrue(tc.migrate_legacy_mports())
        self.assertEqual([(r.name, r.plus, r.minus) for r in tc.mports],
                         [("", "1", "2"), ("vco", "5:1:8", "9"),
                          ("sense", "11", "")])

    def test_unnamed_more_line_migrates(self):
        tc = TraceConfig(mode=6, mp_more="3,4 / 5\n")
        self.assertTrue(tc.migrate_legacy_mports())
        self.assertEqual((tc.mports[0].name, tc.mports[0].plus,
                          tc.mports[0].minus), ("", "3,4", "5"))

    def test_malformed_legacy_line_migrates_instead_of_raising(self):
        """A bad old line must not break loading; it fails later, by name."""
        tc = TraceConfig(mode=6, mp_more="this is not a spec\n")
        tc.migrate_legacy_mports()
        self.assertEqual(len(tc.mports), 1)

    def test_migration_is_idempotent(self):
        tc = TraceConfig(mode=6, mp1_plus="1", mp1_minus="2")
        self.assertTrue(tc.migrate_legacy_mports())
        self.assertFalse(tc.migrate_legacy_mports())
        self.assertEqual(len(tc.mports), 1)

    def test_empty_trace_does_not_migrate(self):
        self.assertFalse(TraceConfig(mode=6).migrate_legacy_mports())

    def test_collect_mports_reads_the_table(self):
        tc = TraceConfig(mode=6, mports=[MeasPortRow("tank", "1", "2"),
                                         MeasPortRow("vco", "5:1:6", "")])
        self.assertEqual(_collect_mports(tc),
                         [("tank", [1], [2]), ("vco", [5, 6], [])])

    def test_collect_mports_rejects_minus_without_plus_by_name(self):
        tc = TraceConfig(mode=6, mports=[MeasPortRow("tank", "", "2")])
        with self.assertRaises(ValueError) as cm:
            _collect_mports(tc)
        self.assertIn("tank", str(cm.exception))

    def test_collect_mports_rejects_an_empty_table(self):
        with self.assertRaises(ValueError) as cm:
            _collect_mports(TraceConfig(mode=6))
        self.assertIn("No measurement ports", str(cm.exception))


class TestDuplicateTrace(unittest.TestCase):
    """
    Duplicate must not hand both traces the same `mports` list.

    TraceConfig(**src.__dict__) is a shallow splat, so before the element-wise
    copy the two traces shared one list object: editing the copy's measurement
    ports silently edited the original's, and the only symptom was two curves
    quietly agreeing.
    """

    def _src(self):
        return TraceConfig(id=1, mode=6, label="tank",
                           mports=[MeasPortRow("tank", "1", "2"),
                                   MeasPortRow("vco", "3", "4")])

    def test_copy_has_equal_rows(self):
        src = self._src()
        dup = _duplicate_trace_config(src, 2)
        self.assertEqual([(r.name, r.plus, r.minus) for r in dup.mports],
                         [(r.name, r.plus, r.minus) for r in src.mports])
        self.assertEqual(dup.id, 2)
        self.assertEqual(dup.label, "tank_copy")

    def test_row_list_is_not_shared(self):
        src = self._src()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNot(dup.mports, src.mports)
        dup.mports.append(MeasPortRow("extra", "9", ""))
        self.assertEqual(len(src.mports), 2)

    def test_individual_rows_are_not_shared(self):
        src = self._src()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNot(dup.mports[0], src.mports[0])
        dup.mports[0].plus = "99"
        self.assertEqual(src.mports[0].plus, "1")

    def test_computed_results_are_dropped(self):
        src = self._src()
        src.Zmat, src.mport_names, src.coupling = object(), ["x"], object()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNone(dup.Zmat)
        self.assertIsNone(dup.mport_names)
        self.assertIsNone(dup.coupling)


if __name__ == "__main__":
    unittest.main()
