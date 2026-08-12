"""
R1: the connection table stops pretending every Kind is the same row.

The complaint, verbatim: "不同的连接，出现的表格都是一样的，比如多个pin连接到
一起的时候，我很自然的感觉就是一个blank，输入我要短接的PIN就行，但是现在有两
个blank".  The shorting example is the instance; the defect is that a ground
row spends 203 px -- half the 405 px table -- on cells that mean nothing for
it, and that a group of shorted pins has no natural from/to to be split into.

Three kinds of test, the split this repo already uses:

  * PURE -- conn_table_layout, the short-group cell pair, editor_scroll_fraction
    and the validation ORDER are module-level functions with no Tk, so they are
    pinned exactly and combinatorially.
  * TK WIRING -- what the table actually grids, what the header says, what a
    Kind change does to the values in the cells it hides.
  * LAYOUT -- measured off a MAPPED window, never eyeballed.  The number that
    matters is that the worst case still FITS the 431 px editor canvas.

Measured on this machine (Microsoft YaHei UI 9, the repo's reference font),
inner frame reqwidth of a standalone connections table.  "R1" is the
six-column table; "now" adds the per-row on/off cell:

    kinds present        old     R1    now     delta vs old
    ground only          405    202    207     -198   (To+R+L+C recovered)
    short only           405    273    271     -134
    rlc_gnd only         405    331    336      -69   (the To column recovered)
    rlc_between only     405    405    410       +5
    every kind at once   405    405    410       +5   <- the guard

The on/off column costs 5 px, not the 14 the cell itself takes: the delete
button went from width=2 (24 px) to width=1 (17 px), which still shows the
whole 12 px glyph.  The mode-5 FORM is 417 px against the 431 px canvas,
i.e. one pixel narrower than before this column existed.
"""

from __future__ import annotations

import sys
import unittest
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from pkg_rlc_core import (  # noqa: E402
    CONN_KINDS,
    ConnectionRow,
    MeasPortRow,
    build_terminations_rows,
    parse_touchstone,
    rows_to_dsl_text,
)
from pkg_rlc_gui import (  # noqa: E402
    CONN_NET_KEY,
    CONN_NET_SUPPORTED,
    CONN_OFF_GLYPH,
    CONN_ON_GLYPH,
    CONN_TABLE_COLUMNS,
    App,
    ColumnSpec,
    FileEntry,
    RowTable,
    TraceConfig,
    V_NO_RESULT,
    V_OK,
    V_ROW_INERT,
    V_WRONG_NUMBER,
    _CONN_COL_C as COL_C,
    _CONN_COL_L as COL_L,
    _CONN_COL_ON as COL_ON,
    _CONN_COL_PORT as COL_PORT,
    _CONN_COL_R as COL_R,
    _CONN_COL_SECOND as COL_SECOND,
    _CONN_COL_TYPE as COL_TYPE,
    _CONN_NCOLS as NCOLS,
    _validation_report,
    _validation_strip_text,
    conn_cells_from_row,
    conn_row_from_cells,
    conn_table_layout,
    editor_scroll_fraction,
    identity_layout,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"

# The connections table's grid columns are IMPORTED, not re-declared.
#
# They used to be `range(6)` written out here, which is a second copy of a
# layout decision -- and when the on/off column was added it became a second
# copy that DISAGREED: the tests went on checking that 'ports' is grid column
# 1 while the implementation had moved it to 2, so what they pinned was the
# old table rather than the current one.  Importing means a column added later
# moves both at once, and the tests that care about the COUNT (the grid is
# NCOLS wide, the ✕ is at NCOLS) keep working unchanged.

#: Every cell key a row can carry, for the "which cells does this Kind show"
#: assertions.  The on/off cell is on every row whatever the Kind, so it is
#: factored out here rather than repeated in each expected set.
ALWAYS = {"enabled", "kind"}


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


def _vals(*kinds) -> list:
    """Cell dicts for a table of rows with these kinds."""
    return [{"kind": k, "ports": "1", "to": "2", "R": "", "L": "", "C": "",
             CONN_NET_KEY: ""} for k in kinds]


def _cells(layout, row: int) -> dict:
    """{column key: (grid column, span)} for one row of a layout."""
    return {k: (c, s) for k, c, s in layout.rows[row]}


# ============================================================================
# PURE: the layout
# ============================================================================

class TestPerKindShape(unittest.TestCase):
    """R1-1: which fields a row shows is decided by its Kind."""

    def test_ground_vdd_and_open_show_one_port_field_and_nothing_else(self):
        for kind in ("ground", "vdd", "open"):
            with self.subTest(kind=kind):
                cells = _cells(conn_table_layout(_vals(kind)), 0)
                self.assertEqual(set(cells), ALWAYS | {"ports"})

    def test_rlc_gnd_shows_one_port_field_plus_rlc(self):
        cells = _cells(conn_table_layout(_vals("rlc_gnd")), 0)
        self.assertEqual(set(cells), ALWAYS | {"ports", "R", "L", "C"})
        self.assertNotIn("to", cells)

    def test_rlc_between_is_the_only_kind_with_two_port_fields(self):
        """A two-terminal element really has two ends; nothing else does."""
        cells = _cells(conn_table_layout(_vals("rlc_between")), 0)
        self.assertEqual(set(cells), ALWAYS | {"ports", "to", "R", "L", "C"})
        two_port = [k for k in CONN_KINDS
                    if "to" in _cells(conn_table_layout(_vals(k)), 0)]
        self.assertEqual(two_port, ["rlc_between"])

    def test_short_is_one_port_field_plus_the_net_name(self):
        cells = _cells(conn_table_layout(_vals("short"), net=True), 0)
        self.assertEqual(set(cells), ALWAYS | {"ports", CONN_NET_KEY})
        self.assertNotIn("to", cells)
        self.assertNotIn("R", cells)

    def test_short_without_net_storage_is_one_port_field_alone(self):
        """The Net cell is feature-detected off ConnectionRow (CONN_NET_
        SUPPORTED); with no field to store it, no cell is drawn."""
        cells = _cells(conn_table_layout(_vals("short"), net=False), 0)
        self.assertEqual(set(cells), ALWAYS | {"ports"})

    def test_an_unknown_kind_keeps_every_cell(self):
        """Retired-but-loading: a session hand-edited to a Kind this build does
        not know must not have its values hidden -- and therefore invisible --
        with no symptom."""
        cells = _cells(conn_table_layout(_vals("connect")), 0)
        self.assertEqual(set(cells),
                         ALWAYS | {"ports", "to", "R", "L", "C"})

    def test_every_key_a_row_shows_is_a_real_column(self):
        keys = {c.key for c in CONN_TABLE_COLUMNS}
        for kind in list(CONN_KINDS) + ["", "connect"]:
            for net in (True, False):
                with self.subTest(kind=kind, net=net):
                    for k, _c, _s in conn_table_layout(_vals(kind),
                                                       net=net).rows[0]:
                        self.assertIn(k, keys)


class TestHeaderFollowsTheRows(unittest.TestCase):
    """
    R1-1's other half: a shared grid header states a column's meaning ONCE,
    and "To" was a lie on a short row even with the cell hidden.  Grid column
    2 carries To (rlc_between) or the net Name (short), so its title is
    derived from what the table actually puts there.
    """

    def _second(self, *kinds) -> str:
        return conn_table_layout(_vals(*kinds)).headers[COL_SECOND]

    def test_the_second_port_column_is_titled_by_what_is_in_it(self):
        self.assertEqual(self._second("ground"), "")
        self.assertEqual(self._second("rlc_between"), "To")
        self.assertEqual(self._second("short"), "Net")
        self.assertEqual(self._second("short", "rlc_between"), "To / Net")

    def test_rlc_titles_appear_only_when_a_row_carries_rlc(self):
        bare = conn_table_layout(_vals("ground", "short")).headers
        self.assertEqual(bare[COL_R:], ("", "", ""))
        full = conn_table_layout(_vals("ground", "rlc_gnd")).headers
        self.assertEqual(full[COL_R:], ("R Ω", "L H", "C F"))

    def test_a_cell_never_spreads_under_someone_elses_heading(self):
        """
        The rule that makes the header honest AND the ground row wide, checked
        over every subset of the six kinds (63 tables): a cell may only occupy
        grid columns whose title is blank, apart from the one it starts in.
        """
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                layout = conn_table_layout(_vals(*kinds))
                for r in range(len(kinds)):
                    for key, col, span in layout.rows[r]:
                        for c in range(col + 1, col + span):
                            self.assertEqual(
                                layout.headers[c], "",
                                f"{kinds}: {key} spans grid column {c}, "
                                f"titled {layout.headers[c]!r}")

    def test_a_used_column_always_has_a_title(self):
        """The converse, and what stops the rule being satisfied by blanking
        every header: a column a row starts a cell in is always titled.

        The on/off column is the ONE exemption, and it is exempt because of
        what the rule is FOR.  A title exists so a cell cannot sit under a
        heading that describes something else; that can only happen to a cell
        that SPREADS, and the on/off cell never does -- which is asserted
        below rather than asserted here in prose.  It has no title because the
        two glyphs are their own legend and the cell is 12 px, which fits no
        word.
        """
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                layout = conn_table_layout(_vals(*kinds))
                for r in range(len(kinds)):
                    for key, col, _span in layout.rows[r]:
                        if col == COL_ON:
                            continue
                        self.assertNotEqual(
                            layout.headers[col], "",
                            f"{kinds}: {key} starts in untitled column {col}")

    def test_the_untitled_on_off_column_NEVER_spreads(self):
        """
        The precondition of the exemption above.  A one-column cell cannot
        reach another column's heading; the moment it spans two it can, and
        then an untitled column is exactly the "blank every header" cheat the
        test above exists to refuse.

        MUTATION: give the on/off cell a span of 2 and this goes red while
        every other layout test stays green.
        """
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                layout = conn_table_layout(_vals(*kinds))
                for r in range(len(kinds)):
                    cells = _cells(layout, r)
                    self.assertIn("enabled", cells,
                                  f"{kinds}: row {r} has no on/off cell")
                    self.assertEqual(cells["enabled"], (COL_ON, 1),
                                     f"{kinds}: the on/off cell moved or spread")


class TestLayoutGeometryIsSane(unittest.TestCase):
    def test_the_grid_is_always_six_columns_wide(self):
        """The ✕ is gridded at layout.ncols, so a table whose width moved with
        its Kinds would move every row's delete button with it."""
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                self.assertEqual(conn_table_layout(_vals(*kinds)).ncols, NCOLS)

    def test_cells_never_overlap_and_never_run_off_the_end(self):
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                layout = conn_table_layout(_vals(*kinds))
                for r in range(len(kinds)):
                    used: set = set()
                    for key, col, span in layout.rows[r]:
                        self.assertGreaterEqual(span, 1)
                        cols = set(range(col, col + span))
                        self.assertFalse(cols & used,
                                         f"{kinds}: {key} overlaps another cell")
                        self.assertLessEqual(col + span, layout.ncols)
                        used |= cols

    def test_a_table_of_nothing_but_ground_rows_is_one_wide_field(self):
        """The user's mental model: one blank, put the pins in it."""
        layout = conn_table_layout(_vals("ground", "ground", "ground"))
        for r in range(3):
            self.assertEqual(_cells(layout, r)["ports"],
                             (COL_PORT, NCOLS - COL_PORT))

    def test_the_rlc_gnd_row_takes_the_To_column_back_when_it_can(self):
        alone = _cells(conn_table_layout(_vals("rlc_gnd")), 0)
        self.assertEqual(alone["ports"], (COL_PORT, 2))
        # ...and gives it up the moment another row needs it.
        shared = _cells(conn_table_layout(_vals("rlc_gnd", "rlc_between")), 0)
        self.assertEqual(shared["ports"], (COL_PORT, 1))

    def test_identity_layout_is_the_historical_fixed_grid(self):
        """What every table with no layout_fn still gets -- the measurement
        port table is one, and it must not move."""
        cols = (ColumnSpec("a", "A", 4), ColumnSpec("b", "B", 4))
        layout = identity_layout(cols, [{}, {}])
        self.assertEqual(layout.ncols, 2)
        self.assertEqual(layout.headers, ("A", "B"))
        self.assertEqual(layout.weights, (1, 1))
        self.assertEqual(layout.rows, ((("a", 0, 1), ("b", 1, 1)),) * 2)


# ============================================================================
# PURE: the short row's one cell over two stored fields
# ============================================================================

class TestShortGroupCell(unittest.TestCase):
    """
    A short row's tied group is ONE cell.  Storage is `ports` alone
    (`5,6,7,8 short`); `to` stays live as the LEGACY spelling, which is what a
    session saved before this round and _trace_role_rows's mode-3 rows carry.
    """

    def test_a_legacy_two_field_short_merges_into_one_cell(self):
        cells = conn_cells_from_row(
            ConnectionRow(kind="short", ports="5", to="6,7,8"))
        self.assertEqual(cells["ports"], "5,6,7,8")
        self.assertEqual(cells["to"], "")

    def test_the_merged_cell_writes_back_as_one_field(self):
        row = conn_row_from_cells({"kind": "short", "ports": "5,6,7,8",
                                   "to": "", "R": "", "L": "", "C": "",
                                   CONN_NET_KEY: "tap"})
        self.assertEqual(row.ports, "5,6,7,8")
        self.assertEqual(row.to, "")

    def test_the_merge_never_emits_a_space(self):
        """collapse_ports's rule, for the same reason: the DSL is whitespace-
        tokenised and the port field is parts[0], so '5, 6' would parse as the
        field '5,' with a stray '6' where the keyword belongs."""
        cells = conn_cells_from_row(
            ConnectionRow(kind="short", ports=" 5 ", to=" 6,7 "))
        self.assertNotIn(" ", cells["ports"])
        self.assertEqual(cells["ports"], "5,6,7")

    def test_the_legacy_and_merged_spellings_compute_the_SAME_network(self):
        """
        The guard that matters.  Merging the two cells is only safe if the row
        it writes back reaches an identical TerminationSet -- otherwise a
        session saved last week silently answers a different question the first
        time it is opened.
        """
        legacy = [ConnectionRow(kind="short", ports="5", to="6,7"),
                  ConnectionRow(kind="rlc_gnd", ports="5", R="20")]
        merged = [conn_row_from_cells(conn_cells_from_row(r)) for r in legacy]
        mports = [MeasPortRow("m", "1", "2")]
        a = build_terminations_rows(mports, legacy, "", nports=8)
        b = build_terminations_rows(mports, merged, "", nports=8)
        self.assertEqual(sorted((c.port_i, c.port_j) for c in a.couplings),
                         sorted((c.port_i, c.port_j) for c in b.couplings))
        self.assertEqual({p: type(t) for p, t in a.per_port.items()},
                         {p: type(t) for p, t in b.per_port.items()})

    def test_the_round_trip_is_idempotent(self):
        row = ConnectionRow(kind="short", ports="5", to="6,7,8")
        once = conn_row_from_cells(conn_cells_from_row(row))
        twice = conn_row_from_cells(conn_cells_from_row(once))
        self.assertEqual((once.ports, once.to), (twice.ports, twice.to))

    def test_other_kinds_are_untouched(self):
        for kind in ("ground", "vdd", "open", "rlc_gnd", "rlc_between"):
            with self.subTest(kind=kind):
                row = ConnectionRow(kind=kind, ports="5", to="6", R="20")
                back = conn_row_from_cells(conn_cells_from_row(row))
                self.assertEqual((back.kind, back.ports, back.to, back.R),
                                 (kind, "5", "6", "20"))

    @unittest.skipUnless(CONN_NET_SUPPORTED, "core has no net field yet")
    def test_the_net_name_round_trips(self):
        row = ConnectionRow(kind="short", ports="1,2,3", net="coil_tap")
        back = conn_row_from_cells(conn_cells_from_row(row))
        self.assertEqual(getattr(back, CONN_NET_KEY), "coil_tap")


# ============================================================================
# PURE: the per-row on/off switch
# ============================================================================

class TestARowCanBeSwitchedOff(unittest.TestCase):
    """
    The debug switch, and what makes it different from the two gestures it
    replaces.

    Deleting the row loses its R/L/C and its ports (on a package ground row
    that is a range someone worked out).  Setting its Kind to `open` is a
    DIFFERENT SPEC rather than an absent one -- `open` is a declaration, it
    survives into port_roles, and on an rlc_gnd row it silently drops the
    element too.  Off means the row is not in the spec at all.
    """

    def _dsl(self, rows):
        return rows_to_dsl_text([MeasPortRow("vic", "1", "2")], rows, "")

    def test_a_disabled_row_is_not_in_the_spec_AT_ALL(self):
        on = [ConnectionRow(kind="ground", ports="3,4"),
              ConnectionRow(kind="rlc_gnd", ports="5", R="50")]
        off = [ConnectionRow(kind="ground", ports="3,4"),
               ConnectionRow(kind="rlc_gnd", ports="5", R="50",
                             enabled=False)]
        # ... and byte-identical to the row simply not being there, which is
        # the whole claim: "as if deleted", not "some other termination".
        self.assertEqual(self._dsl(off), self._dsl(on[:1]))
        self.assertNotIn("lumped_to_gnd", self._dsl(off))
        self.assertIn("lumped_to_gnd", self._dsl(on))

    def test_OFF_is_not_the_same_as_kind_open(self):
        """
        The gesture this replaces, and why it is not equivalent.  `open` is a
        DECLARATION -- it reaches the TerminationSet and port_roles -- while
        off is an absence.  On an rlc_gnd row it also throws the element away.
        """
        as_open = self._dsl([ConnectionRow(kind="open", ports="5")])
        as_off = self._dsl([ConnectionRow(kind="rlc_gnd", ports="5", R="50",
                                          enabled=False)])
        self.assertIn("open", as_open)
        self.assertNotIn("open", as_off)
        self.assertEqual(as_off.strip().splitlines()[-1], "2 signal vic -")

    def test_switching_a_row_off_KEEPS_every_value_in_it(self):
        row = ConnectionRow(kind="rlc_gnd", ports="21:1:25", R="0.5", L="80p",
                            enabled=False)
        self.assertEqual((row.ports, row.R, row.L), ("21:1:25", "0.5", "80p"))
        # and it is still a row: `is_blank` is about what is TYPED in it, so a
        # disabled row does not fall into the "blank rows are dropped" path
        # and lose its values on the next set_rows().
        self.assertFalse(row.is_blank())

    def test_an_EMPTY_row_switched_off_is_still_blank(self):
        """Otherwise clicking the toggle on the spare row at the bottom of the
        table would turn it into a row, and the table always has one."""
        self.assertTrue(ConnectionRow(enabled=False).is_blank())

    def test_the_default_is_ON_so_no_saved_spec_moves(self):
        self.assertTrue(ConnectionRow().enabled)

    def test_the_cell_round_trips_through_the_glyph(self):
        for enabled, glyph in ((True, CONN_ON_GLYPH), (False, CONN_OFF_GLYPH)):
            with self.subTest(enabled=enabled):
                cells = conn_cells_from_row(
                    ConnectionRow(kind="ground", ports="3", enabled=enabled))
                self.assertEqual(cells["enabled"], glyph)
                self.assertIs(conn_row_from_cells(cells).enabled, enabled)

    def test_an_UNEXPECTED_cell_value_reads_as_ENABLED(self):
        """
        Tested against the OFF glyph, not the ON one, so anything else -- an
        empty string from a row built in code, a value from a build that
        spelled the pair differently -- means the row is IN the spec.  A row
        that silently vanishes from the spec is the failure to avoid; a row
        that is unexpectedly present is visible in the answer.
        """
        for text in ("", "?", "x", CONN_ON_GLYPH):
            with self.subTest(text=text):
                self.assertTrue(
                    conn_row_from_cells({"kind": "ground", "ports": "3",
                                         "enabled": text}).enabled)


# ============================================================================
# PURE: R1-5, order by consequence
# ============================================================================

class TestMessageOrder(unittest.TestCase):
    """
    VALIDATION_STRIP_LINES is 2, so the first two messages ARE what is read.
    "Your number is wrong and nothing else will tell you" must outrank "this
    row does nothing", which is visible on its own row anyway.
    """

    def _report(self, mports, conn, extra="", nports=None, names=None):
        return _validation_report(mports, conn, extra, nports, names)

    def test_a_parallel_stamp_outranks_a_row_that_does_nothing(self):
        """
        The measured case: '1,2,3 lumped_between 4 L=10f' after
        '1 short_to 2,3' is 3.333 fH where 10 fH was typed -- three inductors
        in parallel, nothing raised, nothing warned.  An empty Port cell three
        rows down is visible on the row itself; this is not visible anywhere.
        """
        rows = [ConnectionRow(kind="rlc_gnd", ports="", R="50"),
                ConnectionRow(kind="short", ports="1,2,3"),
                ConnectionRow(kind="rlc_between", ports="1,2,3", to="4",
                              L="10f")]
        rep = self._report([MeasPortRow("m", "5", "")], rows, "", 5)
        tiers = [m.tier for m in rep]
        self.assertEqual(tiers, sorted(tiers), [m.text for m in rep])
        self.assertEqual(rep[0].tier, V_WRONG_NUMBER)
        self.assertIn("parallel", rep[0].text.lower())
        self.assertTrue(any(m.tier == V_ROW_INERT and "no Port" in m.text
                            for m in rep), [m.text for m in rep])

    def test_an_annihilated_element_outranks_a_row_that_does_nothing(self):
        rows = [ConnectionRow(kind="rlc_gnd", ports="", R="50"),
                ConnectionRow(kind="short", ports="5,6"),
                ConnectionRow(kind="rlc_between", ports="5", to="6", R="20")]
        rep = self._report([MeasPortRow("m", "1", "2")], rows, "", 6)
        self.assertEqual(rep[0].tier, V_WRONG_NUMBER)
        self.assertIn("SHORTED OUT", rep[0].text)

    def test_an_open_port_remnant_outranks_a_row_that_does_nothing(self):
        names = [f"VSS_ball_{i}" for i in range(1, 9)] + ["sig_p", "sig_n"]
        rows = [ConnectionRow(kind="ground", ports="1-7"),
                ConnectionRow(kind="rlc_gnd", ports="", R="50")]
        rep = self._report([MeasPortRow("sig", "9", "10")], rows, "",
                           len(names), names)
        self.assertEqual(rep[0].tier, V_WRONG_NUMBER)
        self.assertIn("left OPEN", rep[0].text)

    def test_the_strip_shows_the_wrong_number_ones_not_the_inert_ones(self):
        """The consequence of the order, at the width that is actually read."""
        rows = [ConnectionRow(kind="rlc_gnd", ports="", R=str(i))
                for i in range(1, 5)]
        rows += [ConnectionRow(kind="short", ports="1,2,3"),
                 ConnectionRow(kind="rlc_between", ports="1,2,3", to="4",
                               L="10f")]
        msgs = [m.text for m in
                self._report([MeasPortRow("m", "5", "")], rows, "", 5)]
        shown = _validation_strip_text(msgs).splitlines()
        self.assertIn("parallel", shown[0].lower())
        self.assertNotIn("no Port", shown[0])

    def test_row_order_survives_inside_a_tier(self):
        """
        A stable sort, because '5m' typed for '5M' is a property of the row it
        is on -- messages that swapped rows around would be unreadable next to
        the table they describe.
        """
        rows = [ConnectionRow(kind="rlc_gnd", ports="", R="1"),
                ConnectionRow(kind="rlc_gnd", ports="", R="2"),
                ConnectionRow(kind="rlc_gnd", ports="", R="3")]
        rep = self._report([MeasPortRow("m", "1", "")], rows, "", 4)
        inert = [m.text for m in rep if m.tier == V_ROW_INERT]
        self.assertEqual([t.split()[3] for t in inert], ["1", "2", "3"])

    def test_a_missing_measurement_port_outranks_an_inert_element_row(self):
        """
        A measurement-port row that resolves to nothing is not "a row that
        does nothing": with one row it is the CAUSE of a Calculate that
        raises, and the repo already pins cause-above-consequence for the
        probe/ground overlap.
        """
        rep = self._report([MeasPortRow("tank", "", "")],
                           [ConnectionRow(kind="rlc_gnd", ports="", R="1")],
                           "", 8)
        self.assertEqual(rep[0].tier, V_NO_RESULT)
        self.assertIn("has a name but no ports", rep[0].text)

    def test_the_echoes_are_their_own_tier_and_never_mixed_in(self):
        rep = self._report([MeasPortRow("m", "1", "2")],
                           [ConnectionRow(kind="rlc_gnd", ports="3", R="5m")],
                           "", 4)
        self.assertEqual([m.tier for m in rep], [V_OK])
        self.assertIn("5 mΩ", rep[0].text)


class TestMessageAnchors(unittest.TestCase):
    """What the footer route (R1-4) steers by."""

    def test_a_row_message_carries_its_row(self):
        rep = _validation_report(
            [MeasPortRow("m", "1", "2")],
            [ConnectionRow(kind="ground", ports="3"),
             ConnectionRow(kind="rlc_gnd", ports="", R="50")], "", 4)
        hit = [m for m in rep if "no Port" in m.text]
        self.assertEqual(hit[0].anchor, ("conn", 1))

    def test_the_anchor_counts_LIVE_rows_the_way_the_message_does(self):
        """get_rows drops blanks, so row 2 of the message is row index 1 of the
        non-blank rows -- not of the widget rows."""
        rep = _validation_report(
            [MeasPortRow("m", "1", "2")],
            [ConnectionRow(), ConnectionRow(kind="ground", ports="3"),
             ConnectionRow(), ConnectionRow(kind="rlc_gnd", ports="", R="5")],
            "", 4)
        hit = [m for m in rep if "no Port" in m.text][0]
        self.assertIn("connection row 2", hit.text)
        self.assertEqual(hit.anchor, ("conn", 1))

    def test_a_spec_level_message_has_no_anchor(self):
        """The builder speaks in DSL line numbers; mapping one back to a table
        row would be a second copy of rows_to_dsl_text's emission order."""
        rep = _validation_report([], [ConnectionRow(kind="ground", ports="5:")])
        self.assertIsNone(rep[0].anchor)


class TestEditorScrollFraction(unittest.TestCase):
    """R1-4's arithmetic.  At the 1040x600 minsize the mode-5 form is 516 px
    against a 45 px viewport, so a few pixels out is as good as no scroll."""

    def test_a_form_that_fits_never_scrolls(self):
        self.assertEqual(editor_scroll_fraction(300, 20, 500, 400), 0.0)

    def test_the_target_lands_near_the_top_of_the_viewport(self):
        frac = editor_scroll_fraction(300, 20, 45, 516, margin=6)
        self.assertAlmostEqual(frac, (300 - 6) / 516)

    def test_it_clamps_at_both_ends(self):
        self.assertEqual(editor_scroll_fraction(2, 20, 45, 516, margin=6), 0.0)
        self.assertAlmostEqual(
            editor_scroll_fraction(510, 20, 45, 516), (516 - 45) / 516)

    def test_a_degenerate_geometry_is_answered_not_divided_by(self):
        for args in ((0, 0, 0, 0), (10, 5, 0, 100), (10, 5, 45, 0)):
            with self.subTest(args=args):
                self.assertEqual(editor_scroll_fraction(*args), 0.0)


# ============================================================================
# Tk-driven
# ============================================================================

class _TableCase(unittest.TestCase):
    """A bare connections table on a mapped Toplevel."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("600x400")
        self.root.deiconify()
        self.table = RowTable(
            self.root, columns=CONN_TABLE_COLUMNS, row_factory=ConnectionRow,
            min_rows=1, max_visible=6,
            layout_fn=conn_table_layout,
            to_cells=conn_cells_from_row, from_cells=conn_row_from_cells)
        self.table.pack(fill=tk.BOTH, expand=True)
        self._settle()

    def tearDown(self):
        self.root.destroy()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.root.update_idletasks()
            self.root.update()

    def _widget(self, row: int, key: str):
        idx = [c.key for c in CONN_TABLE_COLUMNS].index(key)
        return self.table._rows[row]["_widgets"][idx]

    def _gridded(self, row: int, key: str) -> bool:
        return bool(self._widget(row, key).grid_info())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestClickingTheToggleReachesTheSpec(_TableCase):
    """
    The switch through a REAL click, which is the half the pure tests cannot
    see: the cell is a bare `tk.Label`, so nothing about it is a Tk control --
    what makes it work is that the click writes the row's StringVar, which is
    the same path every other cell takes to `_on_cell_write` -> the layout ->
    the owner's `on_change`.
    """

    def _click(self, row: int):
        self._widget(row, "enabled").event_generate("<Button-1>")
        self._settle()

    def test_a_click_takes_the_row_out_of_the_spec_and_a_second_puts_it_back(self):
        self.table.set_rows([ConnectionRow(kind="ground", ports="3,4"),
                             ConnectionRow(kind="rlc_gnd", ports="5", R="50")])
        self._settle()
        self.assertIn("lumped_to_gnd", rows_to_dsl_text([], self.table.get_rows(), ""))
        self._click(1)
        rows = self.table.get_rows()
        self.assertFalse(rows[1].enabled)
        self.assertNotIn("lumped_to_gnd", rows_to_dsl_text([], rows, ""))
        # ... and the values are still there, which is the point of the switch
        self.assertEqual((rows[1].ports, rows[1].R), ("5", "50"))
        self._click(1)
        self.assertTrue(self.table.get_rows()[1].enabled)

    def test_the_click_NOTIFIES_the_owner(self):
        """
        Auto-apply, the strips and the stale marker all hang off `on_change`.
        Without it the row would go quiet in the table and the trace would keep
        the old spec until some other keystroke happened to sync it.
        """
        seen = []
        self.table._on_change = lambda: seen.append(1)
        self.table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self._settle()
        seen.clear()
        self._click(0)
        self.assertTrue(seen, "clicking the toggle notified nobody")

    def test_the_cell_is_on_every_row_whatever_the_kind(self):
        self.table.set_rows([ConnectionRow(kind=k, ports="1", to="2", R="1",
                                           L="1", C="1") for k in CONN_KINDS])
        self._settle()
        for r, kind in enumerate(CONN_KINDS):
            with self.subTest(kind=kind):
                self.assertTrue(self._gridded(r, "enabled"),
                                f"a {kind} row cannot be switched off")

    def test_a_FROZEN_table_refuses_the_click(self):
        """
        A frozen trace's spec may not move.  Unlike the ttk cells there is no
        state flag to grey a tk.Label out (ttk state does not cascade -- the
        StylePicker precedent), so the HANDLER is what refuses; without that
        the one cell in the table that still worked would be this one.
        """
        self.table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self.table.set_editable(False)
        self._settle()
        self._click(0)
        self.assertTrue(self.table.get_rows()[0].enabled)
        self.table.set_editable(True)
        self._click(0)
        self.assertFalse(self.table.get_rows()[0].enabled)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTableGridsThePerKindShape(_TableCase):
    def test_a_ground_row_grids_only_its_port_cell(self):
        self.table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self._settle()
        self.assertTrue(self._gridded(0, "ports"))
        for key in ("to", "R", "L", "C"):
            self.assertFalse(self._gridded(0, key), key)

    def test_an_rlc_between_row_grids_everything(self):
        self.table.set_rows([ConnectionRow(kind="rlc_between", ports="3",
                                           to="4", R="5")])
        self._settle()
        for key in ("ports", "to", "R", "L", "C"):
            self.assertTrue(self._gridded(0, key), key)

    def test_changing_the_Kind_reshapes_the_row_in_place(self):
        self.table.set_rows([ConnectionRow(kind="rlc_between", ports="3",
                                           to="4", R="5")])
        self._settle()
        self.assertTrue(self._gridded(0, "to"))
        self.table._rows[0]["_vars"]["kind"].set("ground")
        self._settle()
        self.assertFalse(self._gridded(0, "to"))
        self.assertTrue(self._gridded(0, "ports"))

    def test_a_hidden_cell_KEEPS_its_value(self):
        """
        Switching Kind to look at something and switching back must not wipe
        the R the user typed.  The cells are hidden, not destroyed, and
        get_rows reads every variable whatever the shape -- so nothing is lost
        and nothing is silently computed either (rows_to_dsl_text ignores R on
        a ground row).
        """
        self.table.set_rows([ConnectionRow(kind="rlc_between", ports="3",
                                           to="4", R="20")])
        self._settle()
        self.table._rows[0]["_vars"]["kind"].set("ground")
        self._settle()
        self.table._rows[0]["_vars"]["kind"].set("rlc_between")
        self._settle()
        row = self.table.get_rows()[0]
        self.assertEqual((row.to, row.R), ("4", "20"))

    @unittest.skipUnless(CONN_NET_SUPPORTED, "core has no net field yet")
    def test_a_hidden_net_name_is_kept_and_not_computed(self):
        """
        The same rule the other way round, and it is what makes hiding a cell
        safe rather than destructive: rows_to_dsl_text emits `as <name>` only
        for a short, so a Net left over from a Kind the user changed away from
        is carried but never reaches the spec.
        """
        self.table.set_rows([ConnectionRow(kind="short", ports="1,2",
                                           **{CONN_NET_KEY: "coil_tap"})])
        self._settle()
        self.table._rows[0]["_vars"]["kind"].set("ground")
        self._settle()
        row = self.table.get_rows()[0]
        self.assertEqual(getattr(row, CONN_NET_KEY), "coil_tap")
        self.assertNotIn("as", rows_to_dsl_text((), [row]).split())

    def test_the_delete_button_is_in_the_same_column_on_every_row(self):
        """A ✕ that moved with the row's Kind would be a moving target on a
        table the user is editing."""
        self.table.set_rows([ConnectionRow(kind=k, ports="1,2", to="3", R="1")
                             for k in CONN_KINDS])
        self._settle()
        cols = {entry["_widgets"][-1].grid_info()["column"]
                for entry in self.table._rows}
        self.assertEqual(cols, {NCOLS})

    def test_the_header_follows_the_table(self):
        titles = lambda: [str(l.cget("text"))          # noqa: E731
                          for l in self.table._header_lbls]
        self.table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self._settle()
        self.assertEqual(titles()[COL_SECOND], "")
        self.table.set_rows([ConnectionRow(kind="rlc_between", ports="3",
                                           to="4", R="1")])
        self._settle()
        self.assertEqual(titles()[COL_SECOND], "To")
        self.table.set_rows([ConnectionRow(kind="short", ports="1,2")])
        self._settle()
        self.assertEqual(titles()[COL_SECOND],
                         "Net" if CONN_NET_SUPPORTED else "")

    def test_a_short_row_shows_ONE_port_cell_holding_the_whole_group(self):
        """The complaint, directly: one blank, put the pins in it."""
        self.table.set_rows([ConnectionRow(kind="short", ports="5",
                                           to="6,7,8")])
        self._settle()
        self.assertEqual(self._widget(0, "ports").get(), "5,6,7,8")
        self.assertFalse(self._gridded(0, "to"))

    def test_deleting_a_row_keeps_the_survivors_shaped_correctly(self):
        """The hazard R1-1 had to design around: RowTable's old _regrid walked
        entry["_widgets"] and called grid_configure(column=c) BY LIST POSITION,
        so a row whose cells are not one-per-column came back in the wrong
        places (and a grid_remove()d cell came back visible, since grid
        configure re-manages a removed slave)."""
        self.table.set_rows([ConnectionRow(kind="rlc_between", ports="1",
                                           to="2", R="1"),
                             ConnectionRow(kind="ground", ports="3")])
        self._settle()
        self.table._delete_row(self.table._rows[0])
        self._settle()
        self.assertEqual(self.table.get_rows()[0].kind, "ground")
        self.assertTrue(self._gridded(0, "ports"))
        self.assertFalse(self._gridded(0, "to"))
        self.assertEqual(self.table._rows[0]["_widgets"][0].grid_info()["row"],
                         1)

    def test_frozen_state_survives_a_reshape(self):
        """RowTable.set_editable walks every widget, hidden ones included, so a
        cell that comes back must come back greyed."""
        self.table.set_rows([ConnectionRow(kind="ground", ports="3")])
        self.table.set_editable(False)
        self._settle()
        self.table._rows[0]["_vars"]["kind"].set("rlc_between")
        self._settle()
        self.assertIn("disabled", self._widget(0, "R").state())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTableWidth(_TableCase):
    """
    Measured, not eyeballed.  The point of R1-1 is to give width back; the
    guard is that the WORST case still FITS.

    The bound used to be "no wider than the six-column table this replaced"
    (405 px), which was the right invariant while the table was doing the same
    job in fewer pixels.  It stopped being the right one when the on/off column
    was added: that is a new CAPABILITY, so "not wider than before" would
    refuse it on principle, and the constraint it actually has to satisfy is
    the one that was behind 405 all along -- the form has to fit the 431 px
    editor canvas.  So the bound is now the VIEWPORT, with the form's overhead
    over the table measured rather than assumed.

    What the column cost, measured on this machine: worst case 405 -> 410 px,
    i.e. 5 px, because the 12 px glyph cell (padx=0) was paid for partly by
    the delete button going from width=2 (24 px) to width=1 (17 px), which
    still shows the whole 12 px glyph.
    """

    # The six-column table this replaced, measured on the same machine.  Kept
    # as the reference for what the SHAPE rules give back, not as a cap.
    BASELINE = 405
    #: The editor canvas once its vertical scrollbar shows, which in mode 5 it
    #: always is.  This is the number that actually binds.
    VIEWPORT = 431
    #: The mode-5 form's requested width minus the table's, measured: the label
    #: column and the cell padding around the table.
    FORM_OVERHEAD = 13

    def _width(self, rows) -> int:
        self.table.set_rows(rows)
        self._settle()
        return self.table._inner.winfo_reqwidth()

    def test_the_worst_case_still_fits_the_editor_canvas(self):
        """Every Kind at once, so every column is in use and the second one is
        titled 'To / Net'.  410 px + 13 px of form overhead = 423 against a
        431 px viewport, i.e. 8 px spare."""
        w = self._width([ConnectionRow(kind=k, ports="1,2", to="3", R="1",
                                       L="1", C="1") for k in CONN_KINDS])
        self.assertEqual(w, 410)
        self.assertLessEqual(w + self.FORM_OVERHEAD, self.VIEWPORT)

    def test_the_on_off_column_cost_five_pixels(self):
        """
        MUTATION: give the toggle cell padx=1 like every other cell (+2 px), or
        leave the delete button at width=2 (+7 px).  Either takes the worst
        case past the viewport, which is the whole reason both are measured.
        """
        w = self._width([ConnectionRow(kind=k, ports="1,2", to="3", R="1",
                                       L="1", C="1") for k in CONN_KINDS])
        self.assertEqual(w - self.BASELINE, 5)

    def test_a_ground_only_table_gives_back_half_its_width(self):
        """Measured: 405 -> 207 px.  A ground row's To + R + L + C were 203 px
        of dead cells, 50% of the table."""
        self.assertEqual(self._width([ConnectionRow(kind="ground", ports="3")]),
                         207)

    def test_an_rlc_gnd_only_table_gives_back_the_To_column(self):
        """Measured: 405 -> 336 px, the 74 px of a 7-character combobox."""
        self.assertEqual(
            self._width([ConnectionRow(kind="rlc_gnd", ports="3", R="1")]),
            336)

    def test_no_kind_mix_ever_overflows_the_editor_canvas(self):
        for n in range(1, len(CONN_KINDS) + 1):
            for kinds in combinations(CONN_KINDS, n):
                with self.subTest(kinds=kinds):
                    w = self._width([ConnectionRow(kind=k, ports="1,2",
                                                   to="3", R="1", L="1", C="1")
                                     for k in kinds])
                    self.assertLessEqual(w + self.FORM_OVERHEAD, self.VIEWPORT)


class _EditorCase(unittest.TestCase):
    """An App with one file and one mode-5 trace, mapped."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.deiconify()
        self.app.geometry("1040x600")
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=5,
                              label="t1")
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=5):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _rows(self, rows):
        self.app.ed_conn_table.set_rows(rows)
        self._settle()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestEditorStillFits(_EditorCase):
    """
    Nothing may overflow that was not overflowing before.  Modes 1/2/3 have no
    table and must simply fit (the horizontal scrollbar costs 17 px of a 45 px
    viewport at this size); mode 5's form is 417 px against a 431 px canvas
    whatever the Kinds are.
    """

    MIXES = {
        "ground": [ConnectionRow(kind="ground", ports="3")],
        "short": [ConnectionRow(kind="short", ports="1,2,3")],
        "rlc_gnd": [ConnectionRow(kind="rlc_gnd", ports="3", R="5m")],
        "every kind": [ConnectionRow(kind=k, ports="1,2", to="3", R="1")
                       for k in CONN_KINDS],
    }

    def test_mode5_fits_the_canvas_for_every_kind_mix(self):
        for geom in ("1040x600", "1500x900"):
            self.app.geometry(geom)
            for name, rows in self.MIXES.items():
                with self.subTest(geom=geom, mix=name):
                    self._rows(rows)
                    form = self.app._ed_form.winfo_reqwidth()
                    canvas = self.app._ed_canvas.winfo_width()
                    self.assertTrue(
                        form <= canvas
                        or self.app._ed_hsb.winfo_ismapped() == 1,
                        f"{name} at {geom}: form asks {form} px of a {canvas} "
                        "px canvas with no horizontal scrollbar")

    def test_the_table_never_drives_the_mode5_form_wider(self):
        widths = set()
        for rows in self.MIXES.values():
            self._rows(rows)
            widths.add(self.app._ed_form.winfo_reqwidth())
        self.assertEqual(len(widths), 1, widths)
        self.assertLessEqual(max(widths), self.app._ed_canvas.winfo_width())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFooterIsARoute(_EditorCase):
    """
    R1-4.  The footer verdict is the only always-visible pixel of the editor,
    and measured at this size the messages it counts sit 366 and 387 px below
    the fold of a 45 px viewport with every mode change scrolling back to the
    top.  Clicking it goes there.  Zero pixels: the affordance is the hand
    cursor and a hover underline.
    """

    def test_the_underline_affordance_costs_no_pixels(self):
        """Measured: 191x21 px with and without the underline (291x29 at
        150% font scaling).  A hover that reflowed the footer would push
        'Calculate This Trace' around under the pointer."""
        strip = self.app.ed_footer_strip
        strip.configure(text="Ports (4): 1 probe · 1 gnd  ✓ ok")
        self.app.update_idletasks()
        plain = (strip.winfo_reqwidth(), strip.winfo_reqheight())
        strip.configure(font=self.app._ed_footer_font_u)
        self.app.update_idletasks()
        under = (strip.winfo_reqwidth(), strip.winfo_reqheight())
        strip.configure(font=self.app._ed_footer_font)
        self.assertEqual(plain, under)

    def test_it_looks_clickable(self):
        self.assertEqual(str(self.app.ed_footer_strip.cget("cursor")), "hand2")

    def test_clicking_scrolls_the_offending_row_into_view_and_focuses_it(self):
        """
        The precondition is asserted first: without it a test that happens to
        start with the row already on screen passes with no route at all.
        """
        # Ports 3/4, never 1/2: grounding a probe is a V_WRONG_NUMBER message
        # with no row to anchor to, and the route would (correctly) go to the
        # strip instead -- which is a different test than this one.
        self._rows([ConnectionRow(kind="ground", ports="3" if i % 2 else "4")
                    for i in range(6)]
                   + [ConnectionRow(kind="rlc_gnd", ports="", R="50")])
        canvas = self.app._ed_canvas
        canvas.yview_moveto(0.0)
        self._settle()
        target = self.app.ed_conn_table.data_row_widget(6, "ports")
        self.assertIsNotNone(target)
        self.assertFalse(self._on_screen(target),
                         "the row is already visible; this test proves nothing")
        self.app.ed_footer_strip.event_generate("<Button-1>", x=3, y=3)
        self._settle()
        self.assertGreater(canvas.yview()[0], 0.0)
        self.assertTrue(self._on_screen(target))
        # focus_lastfor, not focus_get: focus_get() answers None whenever the
        # whole application is not the OS's focused window, which on a test
        # runner it usually is not -- and that would be a green-looking
        # assertion that never tests anything.
        self.assertEqual(str(self.app.focus_lastfor()), str(target))

    def test_it_reaches_a_row_past_the_tables_own_scroll(self):
        """
        TWO nested scrollable regions.  The connections table shows
        max_visible=6 rows and clips the rest inside its OWN canvas, so
        scrolling the editor canvas alone cannot bring row 7 on screen --
        measured, it landed 37 px ABOVE the editor viewport.
        """
        self._rows([ConnectionRow(kind="ground", ports="3" if i % 2 else "4")
                    for i in range(8)]
                   + [ConnectionRow(kind="rlc_gnd", ports="", R="50")])
        table = self.app.ed_conn_table
        table._canvas.yview_moveto(0.0)
        self.app._ed_canvas.yview_moveto(0.0)
        self._settle()
        target = table.data_row_widget(8, "ports")
        self.assertIsNotNone(target)
        self.assertLess(table._canvas.yview()[1], 1.0,
                        "the table is not scrolling; this test proves nothing")
        self.assertFalse(self._on_screen(target))
        self.app.ed_footer_strip.event_generate("<Button-1>", x=3, y=3)
        self._settle()
        self.assertGreater(table._canvas.yview()[0], 0.0)
        self.assertTrue(self._on_screen(target))

    def test_a_spec_level_verdict_routes_to_the_validation_strip(self):
        """
        'no measurement port defined' is about the spec, not a row.  It must
        not follow some LOWER-priority message's anchor -- that would answer a
        different question than the one the footer is counting.
        """
        self.app.ed_mp_table.set_rows([])
        self._rows([ConnectionRow(kind="rlc_gnd", ports="", R="50")])
        self.app._ed_canvas.yview_moveto(0.0)
        self._settle()
        self.app.ed_footer_strip.event_generate("<Button-1>", x=3, y=3)
        self._settle()
        self.assertTrue(self._on_screen(self.app.ed_validation))

    def test_the_route_never_raises_on_a_half_typed_spec(self):
        """Same contract as _apply_editor_strips: this is a Tk binding and an
        exception here reaches no handler we control."""
        for spec in ("5:", "5:1:", "-", "1,,2"):
            with self.subTest(spec=spec):
                self._rows([ConnectionRow(kind="ground", ports=spec)])
                self.app.ed_footer_strip.event_generate("<Button-1>", x=3, y=3)
                self._settle()

    def _on_screen(self, widget) -> bool:
        canvas = self.app._ed_canvas
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        return 0 <= top and top + widget.winfo_height() <= canvas.winfo_height()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestDataRowWidget(_EditorCase):
    """The mapping a validation message's row number goes through."""

    def test_it_counts_past_a_blank_row(self):
        self._rows([ConnectionRow(kind="ground", ports="3"),
                    ConnectionRow(),
                    ConnectionRow(kind="rlc_gnd", ports="", R="50")])
        w = self.app.ed_conn_table.data_row_widget(1, "ports")
        self.assertIsNotNone(w)
        self.assertEqual(w.grid_info()["row"], 3)

    def test_it_falls_back_when_the_column_is_hidden(self):
        self._rows([ConnectionRow(kind="ground", ports="3")])
        w = self.app.ed_conn_table.data_row_widget(0, "to")
        self.assertIsNotNone(w)
        self.assertTrue(bool(w.grid_info()))

    def test_an_out_of_range_index_answers_None(self):
        self._rows([ConnectionRow(kind="ground", ports="3")])
        self.assertIsNone(self.app.ed_conn_table.data_row_widget(7, "ports"))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestMergedNodesLeadTheDropdown(_EditorCase):
    """
    R1-2's editor half.  Referring to a merged node by ONE member already
    works; listing every member is the spelling that multiplies an element by
    N.  So the node's ref sits at the TOP of the Port / To lists.
    """

    def test_an_unnamed_node_leads_with_its_first_member(self):
        """
        Ports 2 and 3, so the node's ref is '2' -- which is NOT where a plain
        1..n list would put it.  A node of 1,2 would pass this with no feature
        at all, which is the tautology being avoided.
        """
        self._rows([ConnectionRow(kind="short", ports="2,3")])
        self.app._refresh_editor_strips()
        self._settle()
        values = self.app.ed_conn_table.column_values("ports")
        self.assertEqual(values, ("2", "1", "3", "4"))
        self.assertEqual(values.count("2"), 1)

    @unittest.skipUnless(CONN_NET_SUPPORTED, "core has no net field yet")
    def test_a_named_node_leads_with_its_name(self):
        self._rows([ConnectionRow(kind="short", ports="1,2,3",
                                  **{CONN_NET_KEY: "coil_tap"})])
        self.app._refresh_editor_strips()
        self._settle()
        values = self.app.ed_conn_table.column_values("ports")
        self.assertEqual(values[0], "coil_tap")
        self.assertEqual(values[1:], ("1", "2", "3", "4"))

    def test_no_short_row_leaves_the_plain_port_numbers(self):
        self._rows([ConnectionRow(kind="ground", ports="3")])
        self.app._refresh_editor_strips()
        self._settle()
        self.assertEqual(self.app.ed_conn_table.column_values("ports"),
                         ("1", "2", "3", "4"))


if __name__ == "__main__":
    unittest.main()
