"""
Tests for the Mode 5 editor (stage 3 of docs/design_connection_table.md).

Mode 5 stopped being one free-text box and became the measurement-port table
plus a connections table, a port-overview strip, a validation strip and an
"Edit as text…" escape hatch.  Three kinds of test live here:

  * PURE -- the text<->rows import decision and the two strip renderers are
    module-level functions with no Tk, so they can be pinned exactly.
  * WIRING -- what the editor loads, stores and computes, driven through real
    widgets (skips cleanly where no display is available).
  * LAYOUT -- measured, never eyeballed. winfo_ismapped(), winfo_reqwidth(),
    canvas xview/yview/scrollregion, PanedWindow.sashpos(). A widget that looks
    fine but reports ismapped() == 0 is broken, and a screenshot cannot tell.

NOTE for anyone extending the layout class: winfo_ismapped() is NOT a
visibility test for anything INSIDE the editor canvas -- it returns 1 for
widgets well past the right edge. Use the form's requested width against the
canvas width, plus the horizontal scrollbar, the way the tests below do.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_gui  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    build_terminations_rows,
    parse_touchstone,
    resolve_meas_ports,
    rows_to_dsl_text,
)
from pkg_rlc_gui import (  # noqa: E402
    App,
    FileEntry,
    TraceConfig,
    _import_dsl_text,
    _port_overview_text,
    _validation_messages,
    _validation_strip_text,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"

FLIPPING = "3 ground\n3 signal A\n4 signal B\n"


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


# ============================================================================
# Pure helpers (no Tk)
# ============================================================================

class TestImportDslText(unittest.TestCase):
    """Whether a spec may become rows at all."""

    def test_meaning_preserving_import_reports_unchanged(self):
        mports, conn, extra, changed = _import_dsl_text(
            "1 signal tank +\n2 signal tank -\n5:1:8 ground\n")
        self.assertFalse(changed)
        self.assertEqual([(r.name, r.plus, r.minus) for r in mports],
                         [("tank", "1", "2")])
        self.assertEqual([(r.kind, r.ports) for r in conn],
                         [("ground", "5:1:8")])
        self.assertEqual(extra, "")

    def test_meaning_changing_import_is_flagged(self):
        mports, conn, extra, changed = _import_dsl_text(FLIPPING)
        self.assertTrue(changed)
        self.assertEqual((mports, conn), ([], []))
        self.assertEqual(extra, FLIPPING)

    def test_never_raises_on_garbage(self):
        for text in ("", "   ", "this is not a spec\n", "5: ground\n",
                     "1 signal A\n1 signal A x\n", "9 lumped_between 1,2 R=1\n"):
            with self.subTest(text=text):
                got = _import_dsl_text(text)
                self.assertEqual(len(got), 4)
                self.assertIsInstance(got[3], bool)


class TestPortOverviewText(unittest.TestCase):
    def _term(self):
        return build_terminations_rows(
            [MeasPortRow("tank", "1", "2")],
            [ConnectionRow(kind="ground", ports="3,4"),
             ConnectionRow(kind="rlc_gnd", ports="5", R="50"),
             ConnectionRow(kind="short", ports="6", to="7")],
            nports=8)

    def test_counts_every_bucket(self):
        self.assertEqual(
            _port_overview_text(self._term(), 8),
            "Ports (8): 2 probe · 2 ground · 1 element · 2 shorted · 1 open")

    def test_omits_the_open_count_when_no_file(self):
        """
        An open port is one the file has and the spec did not name, so without
        the file it cannot be known -- and inventing a port count from the
        largest port mentioned would look authoritative and be wrong.
        """
        text = _port_overview_text(self._term(), None)
        self.assertEqual(
            text,
            "Ports (no file selected): 2 probe · 2 ground · 1 element · 2 shorted")
        self.assertNotIn("open", text)

    def test_unbuildable_spec_renders_a_dash(self):
        self.assertEqual(_port_overview_text(None, 45), "Ports (45): —")


class TestMode5PortDescriptor(unittest.TestCase):
    """
    The Results table's port-config column is what the user reads to confirm
    what was computed, so it must not describe a spec the trace does not have.
    """

    def test_rows_are_summarised(self):
        tc = TraceConfig(mode=5, mports=[MeasPortRow("tank", "1", "2")],
                         conn_rows=[ConnectionRow(kind="ground", ports="3")])
        self.assertEqual(tc.port_descriptor(), "M5: tank:1/2 C:1")

    def test_a_spec_that_lives_entirely_in_extra_lines_is_shown(self):
        """
        migrate_legacy_custom_text parks an order-dependent spec verbatim, and
        the trace then computes from text with both tables empty. Reporting
        '(no probe) C:0' for it is a positive false claim.
        """
        tc = TraceConfig(mode=5, custom_text=FLIPPING)
        tc.migrate_legacy_custom_text()
        self.assertEqual(tc.mports, [])
        desc = tc.port_descriptor()
        self.assertNotIn("no probe", desc)
        self.assertIn("3 ground", desc)

    def test_rows_plus_kept_text_says_the_text_is_there(self):
        tc = TraceConfig(mode=5, mports=[MeasPortRow("tank", "1", "2")],
                         extra_lines="4 signal B")
        self.assertTrue(tc.port_descriptor().endswith("+txt"),
                        tc.port_descriptor())


class TestValidationMessages(unittest.TestCase):
    def test_reports_the_builder_error_instead_of_raising(self):
        msgs = _validation_messages(
            [], [ConnectionRow(kind="rlc_between", ports="1", to="")])
        self.assertTrue(any("lumped_between" in m for m in msgs), msgs)

    def test_flags_a_partially_typed_range_without_raising(self):
        """Both of these are typed mid-keystroke; parse_port_range rejects them."""
        for spec in ("5:", "5:1:", "-"):
            with self.subTest(spec=spec):
                msgs = _validation_messages(
                    [], [ConnectionRow(kind="ground", ports=spec)])
                self.assertTrue(msgs[0].startswith("⚠"), msgs)

    def test_flags_a_row_with_values_but_no_port(self):
        """rows_to_dsl_text emits nothing for it, with no error at all."""
        msgs = _validation_messages(
            [MeasPortRow("m1", "1", "")],
            [ConnectionRow(kind="rlc_gnd", ports="", R="50")])
        self.assertEqual(msgs,
                         ["⚠ connection row 1 has values but no Port "
                          "-- it does nothing."])

    def test_flags_a_measurement_port_row_with_no_plus_side(self):
        msgs = _validation_messages([MeasPortRow("m1", "", "2")], [])
        self.assertTrue(any("no '+' side" in m for m in msgs), msgs)

    def test_flags_two_rows_that_collapse_into_one_measurement_port(self):
        legacy = _validation_messages(
            [MeasPortRow("A", "1", ""), MeasPortRow("B", "3", "")], [])
        self.assertEqual(len(legacy), 1)
        self.assertIn("'A'", legacy[0])
        self.assertIn("'B'", legacy[0])

        dupe = _validation_messages(
            [MeasPortRow("tank", "1", ""), MeasPortRow("tank", "3", "")], [])
        self.assertEqual(len(dupe), 1)
        self.assertIn("'tank'", dupe[0])

    def test_flags_a_probe_port_that_is_also_a_ground_row(self):
        """
        Legal and pinned -- the rows path lets ground win, exactly as
        build_terminations_mode1/2/3 always have. The strip is where Mode 5
        makes that visible; it must not raise the way Mode 6's builder does.
        """
        msgs = _validation_messages([MeasPortRow("m1", "3", "")],
                                    [ConnectionRow(kind="ground", ports="3")])
        self.assertEqual(len(msgs), 2)
        self.assertIn("port 3", msgs[0])
        self.assertIn("ground row wins", msgs[0])
        # ...and the consequence, under the cause: the only probe was grounded,
        # so nothing is measured and Calculate would raise.
        self.assertIn("no measurement port defined", msgs[1])

    def test_echoes_parsed_rlc_values(self):
        """'5m' and '5M' are one shift key and nine orders of magnitude apart."""
        milli = _validation_messages(
            [MeasPortRow("m1", "1", "")],
            [ConnectionRow(kind="rlc_gnd", ports="13",
                           R="5m", L="0.5n", C="1u")])
        self.assertEqual(len(milli), 1)
        self.assertIn("5 mΩ", milli[0])
        self.assertIn("500 pH", milli[0])
        self.assertIn("1 uF", milli[0])

        mega = _validation_messages(
            [MeasPortRow("m1", "1", "")],
            [ConnectionRow(kind="rlc_gnd", ports="13", R="5M")])
        self.assertIn("5 MΩ", mega[0])

    def test_every_element_row_echoes_not_just_the_first(self):
        """
        The echo catches '5M' typed for '5m', which is a property of the ROW it
        is on. Summarising rows 2..N as '(+N more)' left that typo invisible on
        every row after the first.
        """
        msgs = _validation_messages(
            [MeasPortRow("m1", "1", "")],
            [ConnectionRow(kind="rlc_gnd", ports="20", R="5m", C="1u"),
             ConnectionRow(kind="rlc_gnd", ports="21", R="7M")])
        self.assertEqual(len(msgs), 2)
        self.assertIn("5 mΩ", msgs[0])
        self.assertIn("7 MΩ", msgs[1])

    def test_flags_an_element_row_with_a_port_but_no_value(self):
        """
        The mirror of 'values but no Port', and the one that hurts:
        y_series_rlc(R=0, L=0, C=inf) is 1/0, so Z is NaN at every frequency
        and the emitted warning blames the measurement port's return path.
        """
        for row in (ConnectionRow(kind="rlc_gnd", ports="3"),
                    ConnectionRow(kind="rlc_between", ports="3", to="4")):
            with self.subTest(kind=row.kind):
                msgs = _validation_messages([MeasPortRow("m1", "1", "2")],
                                            [row], "", 4)
                self.assertEqual(len(msgs), 1)
                self.assertIn("no R, L or C", msgs[0])

    def test_flags_a_measurement_port_row_with_a_name_but_no_ports(self):
        """is_blank() is False once the Name cell is typed, so neither the
        blank-row skip nor the minus-without-plus check used to see it."""
        msgs = _validation_messages(
            [MeasPortRow("tank", "", "")],
            [ConnectionRow(kind="ground", ports="3")], "", 8)
        self.assertIn("has a name but no ports", msgs[0])

    def test_flags_a_spec_that_measures_nothing(self):
        """An empty Mode 5 spec used to read '✓ no problems found' and then
        raise at Calculate."""
        self.assertIn("no measurement port defined",
                      _validation_messages([], [], "")[0])

    def test_flags_measurement_ports_that_come_only_from_the_kept_text(self):
        """
        extra_lines has no widget, is emitted LAST and therefore wins. After a
        verbatim-kept import the tables can be empty while the kept text adds a
        probe -- which silently routes the trace to the coupling path.
        """
        msgs = _validation_messages([MeasPortRow("tank", "1", "2")], [],
                                    FLIPPING, 4)
        self.assertEqual(len(msgs), 1)
        self.assertIn("kept as text", msgs[0])
        self.assertIn("'A'", msgs[0])

    def test_says_nothing_about_kept_text_that_adds_no_measurement_port(self):
        """A kept comment must not be reported as a hidden probe."""
        self.assertEqual(
            _validation_messages([MeasPortRow("m1", "1", "2")], [],
                                 "# just a note", 4),
            ["✓ no problems found"])

    def test_a_spaced_rlc_value_is_reported_not_echoed(self):
        """
        The strip used to affirm '✓ port 3 → GND: 5 mΩ' for a row that computed
        5 Ω, because it re-parses the raw cell as one token while the DSL
        splits it into two. Now the row is refused and the strip says why.
        """
        msgs = _validation_messages(
            [MeasPortRow("m1", "1", "2")],
            [ConnectionRow(kind="rlc_gnd", ports="3", R="5 m")], "", 4)
        self.assertTrue(msgs[0].startswith("⚠"), msgs)
        self.assertIn("5 m", msgs[0])
        self.assertNotIn("✓", " ".join(msgs))

    def test_clean_spec_says_so(self):
        self.assertEqual(
            _validation_messages([MeasPortRow("m1", "1", "2")],
                                 [ConnectionRow(kind="ground", ports="3")]),
            ["✓ no problems found"])

    def test_is_capped_at_two_lines(self):
        """Measured uncapped: 140 px at 8 lines, 41% of the editor canvas."""
        rows = [ConnectionRow(kind="rlc_gnd", ports="", R="50")
                for _ in range(8)]
        msgs = _validation_messages([MeasPortRow("m1", "1", "")], rows)
        self.assertEqual(len(msgs), 8)
        lines = _validation_strip_text(msgs).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("+6 more", lines[-1])


# ============================================================================
# Tk-driven: wiring, visibility, layout
# ============================================================================

def _find(widget, pred, out=None):
    """Every descendant of `widget` matching `pred`, depth-first."""
    out = [] if out is None else out
    for child in widget.winfo_children():
        if pred(child):
            out.append(child)
        _find(child, pred, out)
    return out


def _by_text(app, cls, text):
    hits = _find(app, lambda w: w.winfo_class() == cls
                 and str(w.cget("text")) == text)
    assert hits, f"no {cls} labelled {text!r}"
    return hits[0]


class _EditorCase(unittest.TestCase):
    """An App with one file loaded and one selected trace."""

    MODE = 5

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=self.MODE,
                              label="t1")
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self._select()

    def tearDown(self):
        self.app.destroy()

    def _select(self, idx=0):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self.app.update()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestMode5EditorWiring(_EditorCase):
    def test_selecting_a_trace_loads_both_tables(self):
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="3"),
                             ConnectionRow(kind="rlc_gnd", ports="4", R="50")]
        self.tc.extra_lines = "# hand-written note"
        self._select()
        self.assertEqual(
            [(r.name, r.plus, r.minus) for r in self.app.ed_mp_table.get_rows()],
            [("tank", "1", "2")])
        self.assertEqual(
            [(r.kind, r.ports, r.R) for r in self.app.ed_conn_table.get_rows()],
            [("ground", "3", ""), ("rlc_gnd", "4", "50")])
        self.assertEqual(self.app._ed_extra_lines, "# hand-written note")

    def test_sync_writes_both_tables_and_never_writes_custom_text(self):
        self.app.ed_mp_table.set_rows([MeasPortRow("tank", "1", "2")])
        self.app.ed_conn_table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self.app._ed_extra_lines = "# note"
        self.app._sync_editor_to_trace(self.tc)
        self.assertEqual([(r.kind, r.ports) for r in self.tc.conn_rows],
                         [("ground", "3")])
        self.assertEqual(self.tc.extra_lines, "# note")
        # custom_text is retired storage: writing it as well would leave the
        # migration guard unable to tell a legacy trace from a synced one.
        self.assertEqual(self.tc.custom_text, "")

    def test_build_termination_uses_the_rows_not_custom_text(self):
        self.tc.custom_text = "1 signal A\n2 ground\n"
        self.tc.mports = [MeasPortRow("tank", "3", "4")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="1")]
        term = self.app._build_termination(self.tc, nports=4)
        expected = build_terminations_rows(self.tc.mports, self.tc.conn_rows,
                                           self.tc.extra_lines, nports=4)
        self.assertEqual({p: type(t) for p, t in term.per_port.items()},
                         {p: type(t) for p, t in expected.per_port.items()})

    def test_build_termination_validates_ports_against_the_file(self):
        """New guarantee: Mode 5 used to pass no nports at all."""
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="9")]
        with self.assertRaises(ValueError) as cm:
            self.app._build_termination(self.tc, nports=4)
        self.assertIn("9", str(cm.exception))

    def test_two_measurement_port_rows_route_to_the_coupling_path(self):
        """_on_calculate routes on the count, not on the mode number."""
        self.tc.mports = [MeasPortRow("tank", "1", "2"),
                          MeasPortRow("vco", "3", "4")]
        term = self.app._build_termination(self.tc, nports=4)
        self.assertEqual(len(resolve_meas_ports(term, 4)), 2)

    def test_kept_text_shows_a_permanent_marker_on_the_caption(self):
        """
        extra_lines is the only part of the spec with no widget, and it is
        emitted LAST so it wins. After a verbatim-kept import both tables are
        empty while it decides the whole answer.
        """
        self.app._import_text_into_tables("1 signal tank +\n2 signal tank -\n")
        self._settle()
        self.assertEqual(self.app.ed_extra_lbl.cget("text"), "")

        warned = []
        real = pkg_rlc_gui.messagebox.showwarning
        pkg_rlc_gui.messagebox.showwarning = lambda *a, **k: warned.append(a)
        try:
            self.app._import_text_into_tables(FLIPPING)
        finally:
            pkg_rlc_gui.messagebox.showwarning = real
        self._settle()
        self.assertEqual(self.app.ed_mp_table.get_rows(), [])
        self.assertIn("kept as text", self.app.ed_extra_lbl.cget("text"))
        self.assertIn("3", self.app.ed_extra_lbl.cget("text"))

    def test_show_ports_falls_back_to_the_editor_file(self):
        """
        'Show Ports' is the ONLY route to the file's port names -- the Port /
        To dropdowns carry bare numbers. With nothing selected in the Files
        listbox it used to return silently.
        """
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.results_text.delete("1.0", tk.END)
        self.app._on_show_ports()
        body = self.app.results_text.get("1.0", tk.END)
        self.assertIn(f"Ports of {self.fe.label}", body)

    def test_calculate_writes_the_capped_off_validation_lines_to_results(self):
        """
        The strip shows two lines and points at the Results pane for the rest.
        Nothing used to write them there.
        """
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [
            ConnectionRow(kind="rlc_gnd", ports="", R=str(i))
            for i in range(1, 6)]
        self._select()
        self.app.results_text.delete("1.0", tk.END)
        self.app._on_calculate()
        body = self.app.results_text.get("1.0", tk.END)
        self.assertIn("spec notes", body)
        self.assertEqual(body.count("has values but no Port"), 5)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestModeVisibility(_EditorCase):
    CONN_WIDGETS = ("ed_conn_head", "ed_conn_table", "ed_conn_hint",
                    "ed_overview", "ed_validation")

    def _gridded(self, name) -> bool:
        return bool(getattr(self.app, name).grid_info())

    def _set_mode(self, mode):
        self.app.ed_mode_var.set(mode)
        self.app._on_mode_changed()
        self._settle()

    def test_mode5_shows_both_tables_and_the_strips(self):
        self._set_mode(5)
        for name in ("ed_mp_lbl", "ed_mp_table", "ed_mp_hint") + self.CONN_WIDGETS:
            self.assertTrue(self._gridded(name), name)
        # Grounding in mode 5 is a connection row; a second way to say it is
        # exactly what the table is removing.
        self.assertFalse(self._gridded("ed_gnd"))
        self.assertFalse(self._gridded("ed_porta"))

    def test_mode6_is_unchanged(self):
        """Mode 6 shipped and is on main. It must not move."""
        self._set_mode(6)
        for name in ("ed_mp_lbl", "ed_mp_table", "ed_mp_hint", "ed_gnd",
                     "ed_gnd_lbl", "ed_plot_frame", "ed_mutual_hint"):
            self.assertTrue(self._gridded(name), name)
        for name in self.CONN_WIDGETS:
            self.assertFalse(self._gridded(name), name)

    def test_mode1_shows_none_of_the_tables(self):
        self._set_mode(1)
        for name in ("ed_mp_table", "ed_mp_hint") + self.CONN_WIDGETS:
            self.assertFalse(self._gridded(name), name)
        self.assertTrue(self._gridded("ed_porta"))

    def test_switching_from_mode5_to_mode1_resets_the_scroll(self):
        self.app.deiconify()
        self.app.geometry("1200x800")
        self._set_mode(5)
        self.app._ed_canvas.yview_moveto(1.0)
        self._settle()
        self.assertGreater(self.app._ed_canvas.yview()[0], 0.0)
        self._set_mode(1)
        self.assertEqual(self.app._ed_canvas.yview()[0], 0.0)
        _x0, _y0, _x1, y1 = [int(v) for v in
                             self.app._ed_canvas.cget("scrollregion").split()]
        self.assertGreaterEqual(y1, self.app._ed_form.winfo_reqheight())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEditorLayout(_EditorCase):
    """
    Measured, not eyeballed.

    Every assertion here is a number read back off a MAPPED window: a widget
    that looks right and reports ismapped() == 0 is broken, and this project
    has already been misled once by a screenshot of stale pixels.
    """

    def setUp(self):
        super().setUp()
        self.app.deiconify()
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="3"),
                             ConnectionRow(kind="rlc_gnd", ports="4", R="5m")]

    def _mode(self, mode, geom="1500x900"):
        self.app.geometry(geom)
        self.app.ed_mode_var.set(mode)
        self.app._on_mode_changed()
        self._settle()

    def _fits_or_scrolls(self, mode, geom):
        self._mode(mode, geom)
        form = self.app._ed_form.winfo_reqwidth()
        canvas = self.app._ed_canvas.winfo_width()
        self.assertTrue(
            form <= canvas or self.app._ed_hsb.winfo_ismapped() == 1,
            f"mode {mode} at {geom}: form asks {form}px of a {canvas}px canvas "
            "and there is no horizontal scrollbar, so the overflow is "
            "unreachable")

    def test_connections_table_fits_or_scrolls_horizontally(self):
        for geom in ("1500x900", "1200x800"):
            with self.subTest(geom=geom):
                self._fits_or_scrolls(5, geom)

    def test_mode6_no_longer_clips_horizontally(self):
        """
        Pre-existing defect, fixed by the same scrollbar: mode 6's form asked
        463px of a 431px canvas, xview (0.0, 0.962) -- 32px of the ✕ column
        unreachable with no way to scroll to it.
        """
        for geom in ("1500x900", "1200x800"):
            with self.subTest(geom=geom):
                self._fits_or_scrolls(6, geom)

    def test_global_controls_and_apply_stay_mapped_in_mode_5(self):
        """
        pack UNMAPS what does not fit, starting from the END. This is what left
        Calculate All & Plot / Export CSV / Help off screen in mode 6.
        """
        gc = _by_text(self.app, "TLabelframe", "Global Controls")
        apply_btn = _by_text(self.app, "TButton", "Apply to Trace")
        for geom in ("1500x900", "1040x600"):
            with self.subTest(geom=geom):
                self._mode(5, geom)
                self.assertEqual(gc.winfo_ismapped(), 1, "Global Controls")
                self.assertEqual(apply_btn.winfo_ismapped(), 1, "Apply to Trace")

    def test_nothing_invisible_reserves_height_under_the_canvas(self):
        """
        The horizontal scrollbar used to live in a host frame packed once and
        left in place. Tk does not reissue a geometry request when a master's
        LAST slave is removed, so the empty host kept a 17 px requested height
        FOREVER once the first layout pass had packed and unpacked the
        scrollbar -- 17 px off the editor viewport in every mode, including
        1/2/3 which never raise the scrollbar. Measured at 1040x600: 45 px of
        viewport became 28.
        """
        for geom in ("1500x900", "1200x800", "1040x600"):
            for mode in (1, 5):
                with self.subTest(geom=geom, mode=mode):
                    self._mode(mode, geom)
                    if self.app._ed_hsb.winfo_ismapped():
                        continue        # it is really there; it may cost 17 px
                    self.assertEqual(
                        self.app._ed_canvas.winfo_height(),
                        self.app._ed_body.winfo_height(),
                        "something invisible is reserving height below the "
                        "editor canvas")

    def test_the_two_scrollbars_settle_instead_of_flip_flopping(self):
        """
        Autohiding both bars off their own scrollcommands is a LIMIT CYCLE:
        hiding the horizontal bar gives the canvas 17 px of height, which can
        hide the vertical bar, which gives 17 px of width, which brings the
        horizontal bar back. Measured with the decisions split: 431x245 <->
        448x228 forever, and update() never returns -- the GUI hangs. One
        decision from inputs a scrollbar cannot change is what makes it a
        fixed point.
        """
        for geom in ("1500x900", "1200x800", "1040x600"):
            for mode in (1, 5, 6):
                with self.subTest(geom=geom, mode=mode):
                    self._mode(mode, geom)
                    seen = set()
                    for _ in range(4):
                        self.app._apply_editor_scrollbars()
                        self.app.update_idletasks()
                        seen.add((self.app._ed_vsb.winfo_ismapped(),
                                  self.app._ed_hsb.winfo_ismapped()))
                    self.assertEqual(len(seen), 1,
                                     f"scrollbars never settle: {seen}")

    def test_row_add_extends_the_scrollregion_without_jumping_to_the_top(self):
        self._mode(5, "1200x800")
        self.app._ed_canvas.yview_moveto(1.0)
        self._settle()
        before = self.app._ed_canvas.yview()[0]
        self.assertGreater(before, 0.0)

        self.app.ed_conn_table.add_row()
        self._settle()

        _x0, _y0, _x1, y1 = [int(v) for v in
                             self.app._ed_canvas.cget("scrollregion").split()]
        self.assertGreaterEqual(
            y1, self.app._ed_form.winfo_reqheight(),
            "the new row is outside the scrollregion, so it cannot be reached")
        # Not exactly equal: moveto quantises to whole pixels of a scrollregion
        # that just grew. What matters is that the view did not jump to the top.
        self.assertAlmostEqual(self.app._ed_canvas.yview()[0], before, delta=0.02)

    def test_results_pane_still_usable_in_mode_5(self):
        """
        TestResultsPaneVisible's assertion, repeated with a SECOND RowTable
        built. update_idletasks() during construction is what collapsed this
        sash to 2px last time, and stage 3 doubles the number of tables.

        It builds its OWN App instead of using _EditorCase's. That matters:
        _EditorCase does App() -> withdraw() -> ... -> update() -> deiconify(),
        and the update() while WITHDRAWN lets ttk re-lay the paned window, so
        the sash reads a healthy 167px even with the regression reintroduced.
        Measured, by putting update_idletasks() back into
        RowTable._schedule_resize: this test passed at 167px and only
        TestResultsPaneVisible failed at 2px. Same construction order as that
        test -- geometry, deiconify, update, and no update() before the map.
        """
        self.app.destroy()
        self.app = app = App()          # tearDown destroys this one
        app.geometry("1200x800")
        app.deiconify()
        app.update()
        app.ed_mode_var.set(5)
        app._on_mode_changed()
        for _ in range(3):
            app.update_idletasks()
            app.update()
        w = app.results_text
        while w is not None and w.winfo_class() != "TPanedwindow":
            w = w.master
        self.assertIsNotNone(w)
        self.assertGreater(
            w.sashpos(0), 100,
            f"Results pane collapsed: sash at {w.sashpos(0)}px")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTextDialog(_EditorCase):
    """The 'Edit as text…' escape hatch, through the two methods it is made of."""

    def test_round_trips_rows_through_text(self):
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="3"),
                             ConnectionRow(kind="rlc_gnd", ports="4", R="50")]
        self._select()
        text = self.app._editor_dsl_text()
        self.assertEqual(text, rows_to_dsl_text(self.tc.mports,
                                                self.tc.conn_rows, ""))
        self.app._import_text_into_tables(text)
        self.assertEqual(
            [(r.name, r.plus, r.minus) for r in self.app.ed_mp_table.get_rows()],
            [("tank", "1", "2")])
        self.assertEqual(
            [(r.kind, r.ports, r.R) for r in self.app.ed_conn_table.get_rows()],
            [("ground", "3", ""), ("rlc_gnd", "4", "50")])
        self.assertEqual(self.app._editor_dsl_text(), text)

    def test_unrepresentable_lines_survive_in_extra_lines(self):
        text = "# a note\n1 signal tank +\n2 signal tank -\n9 wat_is_this\n"
        self.app._import_text_into_tables(text)
        self.assertIn("# a note", self.app._ed_extra_lines)
        self.assertIn("wat_is_this", self.app._ed_extra_lines)
        out = self.app._editor_dsl_text()
        self.assertIn("# a note", out)
        self.assertIn("wat_is_this", out)

    def _open_dialog_and(self, inspect):
        """
        Drive the real modal _on_edit_as_text() and inspect it from an after().

        The dialog grab_set()s and wait_window()s, so there is no other way in;
        the callback ALWAYS destroys it, or the suite hangs here.
        """
        out = {}

        def _run():
            try:
                dlg = [w for w in self.app.winfo_children()
                       if isinstance(w, tk.Toplevel)][-1]
                dlg.update_idletasks()
                dlg.update()
                inspect(dlg, out)
            except Exception as e:               # pragma: no cover - diagnostic
                out["error"] = repr(e)
            finally:
                for w in self.app.winfo_children():
                    if isinstance(w, tk.Toplevel):
                        w.destroy()

        self.app.deiconify()
        self.app.after(120, _run)
        self.app._on_edit_as_text()
        self.app.update()
        self.assertNotIn("error", out, out.get("error"))
        return out

    def test_dialog_buttons_survive_the_dialog_being_made_shorter(self):
        """
        pack UNMAPS what does not fit, starting from the END. With the footer
        packed after the expand=True Text, dragging the dialog's bottom edge up
        left OK and Cancel at winfo_ismapped() == 0 -- and since the dialog
        grabs and Return/Escape were unbound, there was then no way to commit
        the edit at all.
        """
        def inspect(dlg, out):
            btns = [w for w in _find(dlg, lambda w: w.winfo_class() == "TButton")]
            out["labels"] = sorted(str(b.cget("text")) for b in btns)
            out["states"] = {}
            for geom in ("640x400", "640x300", "640x200"):
                dlg.geometry(geom)
                dlg.update_idletasks()
                dlg.update()
                out["states"][geom] = [b.winfo_ismapped() for b in btns]
            out["escape"] = bool(dlg.bind("<Escape>"))

        out = self._open_dialog_and(inspect)
        self.assertEqual(out["labels"], ["Cancel", "OK"])
        for geom, states in out["states"].items():
            self.assertEqual(states, [1, 1], f"buttons unmapped at {geom}")
        self.assertTrue(out["escape"], "Escape does not close the dialog")

    def test_dialog_refuses_to_open_on_a_cell_it_cannot_serialise(self):
        """
        _editor_dsl_text() raises for an R/L/C cell with a space in it, and an
        unhandled raise here is a Tk traceback with the dialog half-built.
        """
        self.app.ed_conn_table.set_rows(
            [ConnectionRow(kind="rlc_gnd", ports="3", R="5 m")])
        shown = []
        real = pkg_rlc_gui.messagebox.showerror
        pkg_rlc_gui.messagebox.showerror = lambda *a, **k: shown.append(a)
        try:
            self.app._on_edit_as_text()
        finally:
            pkg_rlc_gui.messagebox.showerror = real
        self.assertTrue(shown)
        self.assertNotIn(
            tk.Toplevel,
            [type(w) for w in self.app.winfo_children()])

    def test_meaning_changing_text_is_kept_verbatim(self):
        warned = []
        real = pkg_rlc_gui.messagebox.showwarning
        pkg_rlc_gui.messagebox.showwarning = lambda *a, **k: warned.append(a)
        try:
            self.app._import_text_into_tables(FLIPPING)
        finally:
            pkg_rlc_gui.messagebox.showwarning = real
        self.assertTrue(warned)
        self.assertEqual(self.app.ed_mp_table.get_rows(), [])
        self.assertEqual(self.app.ed_conn_table.get_rows(), [])
        self.assertEqual(self.app._ed_extra_lines, FLIPPING.rstrip())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestCsvExport(_EditorCase):
    def test_two_probe_mode5_trace_exports_the_coupling_block(self):
        """
        The CSV gate is on tc.Zmat, not on tc.mode == 6.

        Gated on the mode, a Mode 5 trace with two probes exported a complete,
        well-formed R/L/C/Q table for measurement port 1's self impedance only,
        headed '# Mode: Custom', with every mutual term, every M and every k
        silently absent.
        """
        self.tc.mports = [MeasPortRow("tank", "1", "2"),
                          MeasPortRow("vco", "3", "4")]
        self._select()
        self.app._on_calculate()
        self.assertIsNotNone(self.tc.Zmat, "trace did not reach the Zmat path")

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.csv")
            real = pkg_rlc_gui.filedialog.asksaveasfilename
            pkg_rlc_gui.filedialog.asksaveasfilename = lambda *a, **k: path
            try:
                self.app._on_export_csv()
            finally:
                pkg_rlc_gui.filedialog.asksaveasfilename = real
            body = Path(path).read_text(encoding="utf-8")

        header = next(r for r in csv.reader(body.splitlines())
                      if r and r[0] == "Freq_GHz")
        self.assertTrue(any(c.startswith("M_nH_") for c in header), header)
        self.assertTrue(any(c.startswith("k_") for c in header), header)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestGroundReferencedProbeThroughTheEditor(_EditorCase):
    """
    The ports->GND shape (Mode 1), driven through the real widgets.

    A ground-referenced probe is a measurement-port row whose MINUS side is
    BLANK -- the return path is the reference node -- and it is the difference
    between Mode 1 and Mode 2 in the table: one column, filled or not.  Every
    other Tk-driven test in this file happens to use a two-sided probe
    ("tank", "1", "2"), so the whole ground-referenced path from cells to a
    computed number was covered only at the row-model layer
    (test_connection_rows.py::TestRowsReproduceNamedModes, which never touches
    a widget).  A blank cell that silently became something else -- an empty
    string reaching parse_port_range, a RowTable default, a strip that reads
    the minus column -- would have shown up nowhere.
    """

    def _fill(self, mport, conns):
        """
        Fill the cells the way a user does, then Apply.

        set_rows() deliberately does NOT notify -- that is what keeps loading a
        trace from re-running the strips once per row -- so the explicit
        _on_editor_rows_changed() is what a keystroke would have done.  The
        load path is covered separately by _load(), below.
        """
        self.app.ed_mp_table.set_rows([mport])
        self.app.ed_conn_table.set_rows(conns)
        self.app._on_editor_rows_changed()
        self.app._on_apply_editor()
        self._settle()

    def _load(self, mport, conns):
        """The other way in: a saved trace selected in the list."""
        self.tc.mports = [mport]
        self.tc.conn_rows = list(conns)
        self._select()
        self._settle()

    def test_typed_cells_reach_the_same_Z_as_build_terminations_mode1(self):
        self._fill(MeasPortRow("tank", "1", ""),
                   [ConnectionRow(kind="ground", ports="2-4")])
        self.app._on_calculate()
        self.assertIsNotNone(self.tc.Z, "trace did not compute")

        from pkg_rlc_core import build_terminations_mode1, compute_z, s_to_y
        Y = s_to_y(self.fe.ts.s, self.fe.ts.z0)
        want, _ = compute_z(Y, self.fe.ts.freqs,
                            build_terminations_mode1([1], [2, 3, 4]))
        self.assertLess(float(np.max(np.abs(np.asarray(self.tc.Z) - want))),
                        1e-15)

    def test_it_takes_the_single_probe_path_not_the_coupling_path(self):
        """One measurement port -> compute_z, so no Zmat and no k/M columns."""
        self._fill(MeasPortRow("tank", "1", ""),
                   [ConnectionRow(kind="ground", ports="2-4")])
        self.app._on_calculate()
        self.assertIsNone(self.tc.Zmat)
        self.assertIsNotNone(self.tc.rlc)

    def test_the_blank_minus_side_is_legal_and_not_flagged(self):
        """
        A ground-referenced probe must warn at most, never error -- it is the
        single most common thing anyone measures.  Checked on BOTH ways into
        the tables, because they refresh the strips through different paths:
        typing goes via _on_editor_rows_changed, selecting a trace via
        _update_mode_visibility.
        """
        for how in (self._fill, self._load):
            with self.subTest(how=how.__name__):
                how(MeasPortRow("tank", "1", ""),
                    [ConnectionRow(kind="ground", ports="2-4")])
                strip = self.app.ed_validation.cget("text")
                self.assertNotIn("⚠", strip, strip)
                self.assertIn("1 probe", self.app.ed_overview.cget("text"))

    def test_the_generated_dsl_says_what_it_computes(self):
        self._fill(MeasPortRow("tank", "1", ""),
                   [ConnectionRow(kind="ground", ports="2-4")])
        self.assertEqual(self.app._editor_dsl_text().split("\n")[:2],
                         ["1 signal tank +", "2-4 ground"])

    def test_it_exports_an_ordinary_rlc_csv_with_no_coupling_columns(self):
        self._fill(MeasPortRow("tank", "1", ""),
                   [ConnectionRow(kind="ground", ports="2-4")])
        self.app._on_calculate()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.csv")
            real = pkg_rlc_gui.filedialog.asksaveasfilename
            pkg_rlc_gui.filedialog.asksaveasfilename = lambda *a, **k: path
            try:
                self.app._on_export_csv()
            finally:
                pkg_rlc_gui.filedialog.asksaveasfilename = real
            body = Path(path).read_text(encoding="utf-8")
        header = next(r for r in csv.reader(body.splitlines())
                      if r and r[0] == "Freq_GHz")
        self.assertFalse([c for c in header
                          if c.startswith(("M_nH_", "k_"))], header)
        self.assertTrue([c for c in header if c.startswith("L_nH")], header)

    def test_adding_a_minus_side_turns_it_into_the_A_to_B_measurement(self):
        """
        Mode 1 and Mode 2 differ by exactly one cell, so pin that the cell is
        what decides -- with everything else held fixed the answer must move
        to build_terminations_mode2's.
        """
        from pkg_rlc_core import (build_terminations_mode2, compute_z, s_to_y)
        Y = s_to_y(self.fe.ts.s, self.fe.ts.z0)

        self._fill(MeasPortRow("tank", "1", ""), [])
        self.app._on_calculate()
        Z_gnd = np.asarray(self.tc.Z).copy()

        self._fill(MeasPortRow("tank", "1", "2"), [])
        self.app._on_calculate()
        Z_ab = np.asarray(self.tc.Z)

        want, _ = compute_z(Y, self.fe.ts.freqs,
                            build_terminations_mode2([1], [2], []))
        self.assertLess(float(np.max(np.abs(Z_ab - want))), 1e-15)
        self.assertGreater(float(np.max(np.abs(Z_ab - Z_gnd))), 1e-12,
                           "the minus cell changed nothing")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWheelRouting(_EditorCase):
    """
    Three of the six connection columns are comboboxes.

    With TCombobox still in _WHEEL_OWNERS the router bailed out over any of
    them and NOTHING scrolled -- half the table was a wheel dead zone.
    """

    def _overflowing_table(self):
        self.app.deiconify()
        self.app.geometry("1200x800")
        self.app.ed_mode_var.set(5)
        self.app._on_mode_changed()
        self.app.ed_conn_table.set_rows(
            [ConnectionRow(kind="ground", ports=str(i)) for i in range(1, 11)])
        self._settle()
        return self.app.ed_conn_table

    def test_pointer_over_a_combo_cell_scrolls_the_table(self):
        table = self._overflowing_table()
        combo = next(w for w in table._rows[0]["_widgets"]
                     if isinstance(w, ttk.Combobox))
        self.assertEqual(combo.winfo_class(), "TCombobox")

        self.app.winfo_containing = lambda *_a, **_k: combo
        ed_before = self.app._ed_canvas.yview()[0]
        before = table._canvas.yview()[0]
        result = self.app._route_wheel(
            SimpleNamespace(delta=-120, x_root=0, y_root=0))
        self._settle(1)
        self.assertEqual(result, "break")
        self.assertGreater(table._canvas.yview()[0], before)
        self.assertEqual(self.app._ed_canvas.yview()[0], ed_before)

    def test_wheel_does_not_change_a_combobox_value(self):
        """What makes removing TCombobox from _WHEEL_OWNERS safe."""
        table = self._overflowing_table()
        combo = next(w for w in table._rows[0]["_widgets"]
                     if isinstance(w, ttk.Combobox))
        combo.configure(values=["ground", "vdd", "open"])
        combo.set("ground")
        combo.event_generate("<MouseWheel>", delta=-120)
        self._settle(1)
        self.assertEqual(combo.get(), "ground")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestListboxes(_EditorCase):
    def test_every_listbox_sets_exportselection_false(self):
        """
        Without exportselection=False, clicking an Entry steals the X selection
        and clears the highlight, and 'Apply to Trace' then silently fails.
        Stage 3 multiplies the number of Entry widgets clicked between selecting
        a trace and applying, so the invariant finally gets a test.
        """
        boxes = _find(self.app, lambda w: isinstance(w, tk.Listbox))
        self.assertGreaterEqual(len(boxes), 2)
        for lb in boxes:
            self.assertEqual(int(lb.cget("exportselection")), 0, str(lb))


if __name__ == "__main__":
    unittest.main()
