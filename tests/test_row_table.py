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

from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    build_terminations_rows,
    parse_custom_termination_text,
    resolve_meas_ports,
)
from pkg_rlc.frontend.app import (  # noqa: E402
    App,
    ColumnSpec,
    RowTable,
    TraceConfig,
    _collect_mports,
    _duplicate_trace_config,
    _import_dsl_text,
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
        """The widget is shared with the connection table."""
        table = self._conn_table()
        table.set_rows([ConnectionRow(kind="ground", ports="5-12")])
        got = table.get_rows()
        self.assertEqual((got[0].kind, got[0].ports), ("ground", "5-12"))

    def _conn_table(self, **kwargs) -> RowTable:
        return RowTable(
            self.root,
            columns=(ColumnSpec("kind", "Type", 10, kind="combo",
                                values=("ground", "short"),
                                readonly_combo=True, default="ground"),
                     ColumnSpec("ports", "Port", 8),
                     ColumnSpec("to", "To", 8)),
            row_factory=ConnectionRow,
            min_rows=1,
            **kwargs,
        )

    def test_add_row_uses_the_column_default(self):
        """
        The '+ Add' button is bound as command=self.add_row, which Tk calls
        with NO arguments. Without the per-column default the new row's Type is
        '', and rows_to_dsl_text raises on that instead of skipping it.
        """
        table = self._conn_table()
        table.add_row()                       # exactly what the button does
        table._rows[-1]["_vars"]["ports"].set("7")
        self.assertEqual(table.get_rows()[-1].kind, "ground")

    def test_explicit_values_beat_the_column_default(self):
        table = self._conn_table()
        table.add_row({"kind": "short", "ports": "3", "to": "4"})
        self.assertEqual(table.get_rows()[-1].kind, "short")
        # set_rows passes every column explicitly, so a default can never leak
        # into a load and silently re-type someone's row.
        table.set_rows([ConnectionRow(kind="short", ports="1", to="2")])
        self.assertEqual([r.kind for r in table.get_rows()], ["short"])

    def test_set_rows_does_not_notify_but_editing_does(self):
        """
        What makes an on_change-driven validation strip safe during a trace
        selection: loading rows is silent, typing in one is not.
        """
        calls = []
        table = self._conn_table(on_change=lambda: calls.append(1))
        table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self.assertEqual(calls, [])
        table._rows[0]["_vars"]["ports"].set("4")
        self.assertEqual(len(calls), 1)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestResultsPaneVisible(unittest.TestCase):
    """
    The Results pane must not collapse to a sliver.

    A ttk.PanedWindow sizes a pane from its requested size AT THE MOMENT IT IS
    ADDED and never recomputes. _build_right_panel used to add an empty frame
    and populate it afterwards, which works only as long as no geometry pass
    runs in between -- so adding a widget on the OTHER side of the window that
    called update_idletasks() during construction pinned the sash at 2px and the
    entire Results pane disappeared. Nothing in that commit's diff touched
    _build_right_panel, which is why this needs a test rather than review.
    """

    @staticmethod
    def _pane_and_paned(app):
        """Walk up from results_text to the pane frame and its PanedWindow."""
        w, prev = app.results_text, app.results_text
        while w is not None and w.winfo_class() != "TPanedwindow":
            prev, w = w, w.master
        return prev, w

    def test_sash_leaves_the_results_pane_usable(self):
        # The window has to be MAPPED for Tk to assign sash positions at all --
        # on a withdrawn root sashpos() reads 0 whether the layout is right or
        # wrong. So this test briefly shows a window; that is the only way to
        # measure the thing that actually broke.
        app = App()
        try:
            app.geometry("1200x800")
            app.deiconify()
            app.update()
            pane, paned = self._pane_and_paned(app)
            self.assertIsNotNone(paned, "results_text is not inside a PanedWindow")
            self.assertGreater(
                pane.winfo_reqheight(), 100,
                "the Results pane does not even request a usable height")
            self.assertGreater(
                paned.sashpos(0), 100,
                f"Results pane collapsed: sash at {paned.sashpos(0)}px. A pane "
                "was added to the PanedWindow before its children existed.")
        finally:
            app.destroy()


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


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEveryModeKeepsItsFurniture(unittest.TestCase):
    """
    pack UNMAPS what does not fit, starting from the END.

    A fixed-size section packed after an expand=True sibling does not get
    clipped -- it disappears, winfo_ismapped() == 0. That is how mode 6 once
    shipped with Calculate All & Plot / Export CSV / Help entirely off screen
    while modes 1/2/3/5 looked fine, which made it read as flaky rather than
    broken. Stage 3 makes the mode-5 form the tallest of the lot, so every mode
    gets checked, on a MAPPED window (ismapped() is meaningless otherwise).

    BOTH geometries are needed. Measured, by moving the Global Controls pack()
    back after the editor's: at 1200x800 the left panel is still tall enough
    for the frame to survive being packed last and this test stayed green --
    only 1040x600, the documented minsize, caught it.
    """

    GEOMETRIES = ("1200x800", "1040x600")

    @staticmethod
    def _find(widget, pred, out=None):
        out = [] if out is None else out
        for child in widget.winfo_children():
            if pred(child):
                out.append(child)
            TestEveryModeKeepsItsFurniture._find(child, pred, out)
        return out

    def test_every_mode_keeps_global_controls_apply_and_its_tables(self):
        app = App()
        try:
            app.deiconify()
            app.update()
            gc = self._find(app, lambda w: w.winfo_class() == "TLabelframe"
                            and str(w.cget("text")) == "Global Controls")[0]
            # The editor footer's button ("Apply to Trace" before the editor
            # started applying itself).  What is pinned here is the FOOTER --
            # packed side=BOTTOM before the scrolling body -- so the button's
            # job changed but the layout property under test did not.
            foot_btn = self._find(app, lambda w: w.winfo_class() == "TButton"
                                  and str(w.cget("text")) ==
                                  "Calculate This Trace")[0]
            for geom in self.GEOMETRIES:
                for mode in (1, 2, 3, 5, 6):
                    with self.subTest(geom=geom, mode=mode):
                        app.geometry(geom)
                        app.ed_mode_var.set(mode)
                        app._on_mode_changed()
                        for _ in range(3):
                            app.update_idletasks()
                            app.update()
                        self.assertEqual(gc.winfo_ismapped(), 1,
                                         "Global Controls")
                        self.assertEqual(foot_btn.winfo_ismapped(), 1,
                                         "editor footer button")
                        self.assertEqual(app.ed_mp_table.winfo_ismapped(),
                                         1 if mode in (5, 6) else 0,
                                         "measurement-port table")
                        self.assertEqual(app.ed_conn_table.winfo_ismapped(),
                                         1 if mode == 5 else 0,
                                         "connections table")
        finally:
            app.destroy()


class TestLegacyCustomTextMigration(unittest.TestCase):
    """
    The retired free-text Mode 5 spec folds into the two tables on load.

    Same contract as the mp1_*/mp_more migration, with one extra rule: it must
    never change what the trace computes. dsl_text_to_rows discards line order
    and rows_to_dsl_text hoists every probe above every connection, so a spec
    that depends on its own order comes back meaning something else. Those are
    parked verbatim in extra_lines instead.
    """

    FLIPPING = "3 ground\n3 signal A\n4 signal B\n"

    def test_custom_text_migrates_into_two_tables(self):
        tc = TraceConfig(mode=5, custom_text="1 signal A\n2 signal B\n3 ground\n")
        self.assertTrue(tc.migrate_legacy_custom_text())
        self.assertEqual([(r.name, r.plus, r.minus) for r in tc.mports],
                         [("A", "1", "2")])
        self.assertEqual([(r.kind, r.ports) for r in tc.conn_rows],
                         [("ground", "3")])
        self.assertEqual(tc.custom_text, "")

    def test_migration_is_idempotent(self):
        tc = TraceConfig(mode=5, custom_text="1 signal A\n2 ground\n")
        self.assertTrue(tc.migrate_legacy_custom_text())
        before = [(r.kind, r.ports) for r in tc.conn_rows]
        self.assertFalse(tc.migrate_legacy_custom_text())
        self.assertEqual([(r.kind, r.ports) for r in tc.conn_rows], before)

    def test_declines_when_rows_already_exist(self):
        for kwargs in ({"conn_rows": [ConnectionRow(kind="ground", ports="9")]},
                       {"mports": [MeasPortRow("m1", "1", "")]},
                       {"extra_lines": "# note"}):
            with self.subTest(**kwargs):
                tc = TraceConfig(mode=5, custom_text="1 signal A\n", **kwargs)
                self.assertFalse(tc.migrate_legacy_custom_text())
                self.assertEqual(tc.custom_text, "1 signal A\n")

    def test_empty_custom_text_does_not_migrate(self):
        self.assertFalse(TraceConfig(mode=5).migrate_legacy_custom_text())
        self.assertFalse(
            TraceConfig(mode=5, custom_text="   \n").migrate_legacy_custom_text())

    def test_malformed_spec_migrates_into_extra_lines_instead_of_raising(self):
        """A bad old spec must not break loading; it fails later, by name."""
        tc = TraceConfig(mode=5, custom_text="this is not a spec\n")
        self.assertTrue(tc.migrate_legacy_custom_text())
        self.assertIn("this is not a spec", tc.extra_lines)

    def test_comment_only_spec_is_not_a_meaning_change(self):
        """
        It also lands with both tables empty, which is why _migrate_trace asks
        _import_dsl_text rather than inferring the fallback from that. Telling
        this user their precedence changed would be a lie.
        """
        tc = TraceConfig(mode=5, custom_text="# just a note\n")
        self.assertTrue(tc.migrate_legacy_custom_text())
        self.assertEqual(tc.extra_lines, "# just a note")
        self.assertFalse(_import_dsl_text("# just a note\n")[3])

    def test_meaning_changing_spec_is_kept_verbatim(self):
        tc = TraceConfig(mode=5, custom_text=self.FLIPPING)
        self.assertTrue(tc.migrate_legacy_custom_text())
        self.assertEqual(tc.mports, [])
        self.assertEqual(tc.conn_rows, [])
        self.assertEqual(tc.extra_lines, self.FLIPPING.rstrip())

    def test_migration_never_changes_what_is_computed(self):
        """
        The one that matters: whatever route the text takes into the tables,
        the resulting TerminationSet is the same one it produced before.
        """
        specs = [
            self.FLIPPING,
            "1 signal A\n2 signal B\n3 ground\n",
            "1 signal tank +\n2 signal tank -\n5:1:8 ground\n",
            "1 signal m1 +\n3 lumped_to_gnd R=50\n"
            "4 lumped_between 2 R=0.01 L=0.1n C=1p\n",
            "1 signal m1 +\n2 signal m1 -\n3 short_to 4\n12 open\n",
            "1,2 signal m1 +\n3,4 signal m1 -\n6-9 ground\n",
            "3 ground\n1 signal m1 +\n2 signal m1 -\n",
            "# a note\n1 signal A\n2 ground\n",
        ]
        for spec in specs:
            with self.subTest(spec=spec):
                direct = parse_custom_termination_text(spec)
                tc = TraceConfig(mode=5, custom_text=spec)
                tc.migrate_legacy_custom_text()
                viarows = build_terminations_rows(tc.mports, tc.conn_rows,
                                                  tc.extra_lines)
                self.assertEqual(direct.per_port.keys(), viarows.per_port.keys())
                for port, term in direct.per_port.items():
                    self.assertIs(type(term), type(viarows.per_port[port]),
                                  f"port {port}")
                self.assertEqual(
                    [(c.port_i, c.port_j) for c in direct.couplings],
                    [(c.port_i, c.port_j) for c in viarows.couplings])
                self.assertEqual(
                    [(mp.name, mp.plus, mp.minus)
                     for mp in resolve_meas_ports(direct, 16)],
                    [(mp.name, mp.plus, mp.minus)
                     for mp in resolve_meas_ports(viarows, 16)])

    def test_mport_migration_runs_first_so_a_stale_custom_text_is_not_merged(self):
        """
        App._migrate_trace's ORDER. A config carrying both mp1_* and a stray
        custom_text must fill `mports` first, so the custom-text guard declines
        rather than merging two unrelated specs into one trace.
        """
        tc = TraceConfig(mode=6, mp1_name="tank", mp1_plus="1", mp1_minus="2",
                         custom_text="7 signal other +\n")
        tc.migrate_legacy_mports()
        self.assertFalse(tc.migrate_legacy_custom_text())
        self.assertEqual([(r.name, r.plus, r.minus) for r in tc.mports],
                         [("tank", "1", "2")])
        self.assertEqual(tc.conn_rows, [])


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
                                   MeasPortRow("vco", "3", "4")],
                           conn_rows=[ConnectionRow(kind="ground", ports="5"),
                                      ConnectionRow(kind="short", ports="6",
                                                    to="7")])

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

    def test_conn_row_list_is_not_shared(self):
        src = self._src()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNot(dup.conn_rows, src.conn_rows)
        dup.conn_rows.append(ConnectionRow(kind="open", ports="9"))
        self.assertEqual(len(src.conn_rows), 2)

    def test_individual_conn_rows_are_not_shared(self):
        src = self._src()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNot(dup.conn_rows[0], src.conn_rows[0])
        dup.conn_rows[0].ports = "99"
        self.assertEqual(src.conn_rows[0].ports, "5")

    def test_computed_results_are_dropped(self):
        src = self._src()
        src.Zmat, src.mport_names, src.coupling = object(), ["x"], object()
        dup = _duplicate_trace_config(src, 2)
        self.assertIsNone(dup.Zmat)
        self.assertIsNone(dup.mport_names)
        self.assertIsNone(dup.coupling)


if __name__ == "__main__":
    unittest.main()
