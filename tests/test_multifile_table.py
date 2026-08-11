"""
R3-2 / R3-3 / R3-5: the two-file table, the file UI, and the weld on screen.

`pkg_rlc_compose` (round 2) already stacks the files, aligns the grids, owns
the `ALIAS.port` namespace and detects the weld.  This round is what a GUI user
touches: which file a bare port number means, which files a trace is made of,
and the reference-node verdict arriving where the number is read instead of in
a CLI report nobody ran.

Three kinds of test, the split this repo uses everywhere:

  * PURE -- the scope rules, the alias rules, the cross-file row report and the
    reference-check renderers are module-level functions with no widget in
    sight, so they are pinned exactly.
  * TK WIRING -- what the windows actually build, what they refuse and what
    they say when they refuse it.
  * LAYOUT -- MEASURED off a MAPPED window at 100% and at 150% (`tk scaling
    2.0` plus every named font x1.5, this repo's definition), never eyeballed.

MEASURED ON THIS BOX (Tk 8.6, vista theme, TkDefaultFont = Microsoft YaHei
UI 9, tk scaling 1.333), with the REAL connections table at its natural width
inside the editor's scrolling canvas -- the canvas SCROLLS rather than
squeezes, so the cell the user sees is the widget's own requested width:

    tightest Port cell (an `rlc_between` row, the only Kind with two port
    fields):

        scale    cell    visible    text budget    one digit
        100%     72 px   7 chars    49 px           7 px
        150%    135 px   7 chars   112 px          16 px

    what a file tag costs of that budget, and the digits it leaves:

        F1. / F2.    16 / 38 px    33% / 34%    4 digits
        F10.         23 / 54 px    47% / 48%    3 digits
        EM.          22 / 49 px    45% / 44%    3 digits
        PKG.         27 / 63 px    55% / 56%    3 digits
        ABCD.        36 / 85 px    73% / 76%    1 digit

    and the pair that decides the whole design:

        '23,24,25'      48 px   FITS the 49 px budget
        'F2.23,24,25'   64 px   131% of it -- scrolls, in a cell with no
                                scrollbar and no overflow indicator

The reviewer's figure for the last one was "70 px against a ~55 px visible
combo area", measured on `EM:23,24,25`; re-measured here against the real
widget the budget is 49 px, i.e. tighter than reported, and `EM:23,24,25`
reproduces at exactly 70 px.

Every guard below was mutation-checked and the mutation that defeats it is
named in the test's docstring.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402
from tkinter import ttk  # noqa: E402

import pkg_rlc_compose as comp  # noqa: E402
import pkg_rlc_files_gui as fg  # noqa: E402
from pkg_rlc_compose import ComposeInput, compose  # noqa: E402
from pkg_rlc_core import ConnectionRow, parse_touchstone  # noqa: E402
from pkg_rlc_gui import (  # noqa: E402
    CONN_TABLE_COLUMNS,
    App,
    FileEntry,
    RowTable,
    TraceConfig,
    _format_results_table,
    conn_table_layout,
)

# The weld fixture is IMPORTED, not rebuilt.  It is the construction that
# provably welds -- grounded / open / through 1 nH bit-identical, spread
# 0.000e+00 -- and a second copy here could stop welding without anyone
# noticing, at which point every R3-5 test would pass for the wrong reason.
from test_compose import MARK_HZ, weld_files  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = "coupled_4port_diff.s4p"
#: A SECOND, different file, so "add a file" has something to add and the two
#: rows of the list cannot be told apart only by their alias.
SECOND_FIXTURE = "pi_2port.s2p"

#: The measured budget, repeated here so a change to the module constant has to
#: face a test that names the measurement rather than importing it.
PORT_CELL_CHARS_100 = 7
PORT_CELL_CHARS_150 = 7
TAG_FITS_PX = 49            # the 100% text budget of the tightest Port cell


def _ensure_fixtures() -> None:
    if (FIXTURES / FIXTURE).exists():
        return
    import generate_test_snp                                 # type: ignore
    generate_test_snp.main()


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


# ===========================================================================
# Helpers
# ===========================================================================

def _slots(*specs) -> list:
    """FileSlot list from (label, nports) pairs; aliases F1, F2, ... in order."""
    return [fg.FileSlot(alias=comp.default_alias(i), label=lbl, nports=n,
                        z0=50.0, npoints=91, span="1-10 GHz",
                        local_ports=tuple(range(1, n + 1)))
            for i, (lbl, n) in enumerate(specs)]


def _rows(*specs) -> list:
    """ConnectionRows from (kind, ports, to) triples."""
    out = []
    for kind, ports, to in specs:
        out.append(ConnectionRow(kind=kind, ports=ports, to=to))
    return out


class _FakeTrace:
    """The three attributes every function here reads off a trace."""

    def __init__(self, *, id=1, label="coil", file_label="em.s2p",
                 conn_rows=(), **extra):
        self.id = id
        self.label = label
        self.file_label = file_label
        self.conn_rows = list(conn_rows)
        for k, v in extra.items():
            setattr(self, k, v)


class _FakeApp:
    def __init__(self, by_label=None):
        self._by = dict(by_label or {})

    def _file_by_label(self, label):
        return self._by.get(label)


# ===========================================================================
# PURE: the port cell's budget, and what it decides
# ===========================================================================

class TestPortCellBudget(unittest.TestCase):
    """
    The constants are a MEASUREMENT, and this is where the measurement lives.

    Mutation: raise `PORT_CELL_CHARS` to 11 (the width a file column would
    need) and `test_a_tagged_group_does_not_fit_where_a_bare_one_does` goes
    green while the cell on screen has not moved a pixel.
    """

    def test_the_constants_match_what_was_measured(self):
        self.assertEqual(fg.PORT_CELL_CHARS, PORT_CELL_CHARS_100)
        self.assertEqual(fg.PORT_CELL_TEXT_PX, TAG_FITS_PX)

    def test_a_two_character_alias_costs_three_of_the_seven_characters(self):
        """`F2.` is alias + separator, and the separator is not free."""
        self.assertEqual(fg.alias_cost("F2"), 3)
        self.assertEqual(fg.alias_cost("PKG"), 4)

    def test_the_default_aliases_are_the_repo_s_own_F1_F2_idiom(self):
        """
        Not a preference: `_format_results_table` already labels a multi-file
        results table F1/F2, and a second alphabet on another screen is how two
        surfaces come to disagree about which file F2 is.
        """
        self.assertEqual([comp.default_alias(i) for i in range(3)],
                         ["F1", "F2", "F3"])
        self.assertTrue(all(len(comp.default_alias(i)) <= fg.ALIAS_MAX_CHARS
                            for i in range(9)))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestPortCellBudgetMeasured(unittest.TestCase):
    """
    The same numbers, taken off the REAL table rather than trusted.

    Mutation: change `ColumnSpec("ports", "Port", 7, ...)` in `pkg_rlc_gui` to
    any other width and both tests below fail with the new number in the
    message, which is what makes them a re-measurement rather than a copy.
    """

    def _tightest_cell(self, root, scale=1.0):
        """The Port cell of an `rlc_between` row, at its natural width."""
        if scale != 1.0:
            root.tk.call("tk", "scaling", 2.0)
            for name in tkfont.names(root):
                try:
                    fo = tkfont.nametofont(name, root=root)
                except Exception:
                    continue
                size = fo.cget("size")
                if size:
                    fo.configure(size=int(round(abs(size) * scale))
                                 * (1 if size > 0 else -1))
        host = ttk.Frame(root)
        host.pack(side=tk.TOP, anchor="nw")     # natural size: no fill/expand
        table = RowTable(host, columns=CONN_TABLE_COLUMNS,
                         row_factory=ConnectionRow,
                         layout_fn=conn_table_layout)
        table.pack(side=tk.TOP, anchor="nw")
        table.set_rows([ConnectionRow(kind="rlc_between", ports="1", to="2")])
        root.update()
        cell = [w for c, w in zip(CONN_TABLE_COLUMNS,
                                  table._rows[0]["_widgets"])
                if c.key == "ports"][0]
        return cell

    @staticmethod
    def _visible_chars(root, cell) -> int:
        """How many '0's the cell shows before it starts scrolling."""
        n = 0
        for k in range(1, 40):
            cell.delete(0, tk.END)
            cell.insert(0, "0" * k)
            cell.xview_moveto(0.0)
            root.update()
            if cell.xview()[1] < 1.0:
                break
            n = k
        cell.delete(0, tk.END)
        return n

    def test_the_cell_shows_seven_characters_at_100_and_at_150(self):
        """
        The CHARACTER count is what is stable across DPI, which is why every
        rule in `pkg_rlc_files_gui` is written in characters: measured, the
        cell is 72 px at 100% and 135 px at 150%, and both show seven.
        """
        for scale, want in ((1.0, PORT_CELL_CHARS_100),
                            (1.5, PORT_CELL_CHARS_150)):
            root = tk.Tk()
            try:
                root.geometry("1600x700")
                root.update()
                cell = self._tightest_cell(root, scale)
                got = self._visible_chars(root, cell)
                self.assertEqual(
                    got, want,
                    f"at {scale:.0%} the Port cell shows {got} characters, "
                    f"not {want} -- re-measure PORT_CELL_CHARS")
                self.assertEqual(fg.PORT_CELL_CHARS, got)
            finally:
                root.destroy()

    def test_a_tagged_group_does_not_fit_where_a_bare_one_does(self):
        """
        THE measurement R3-2 turns on, and it is a pair, not a number.

        A three-port group of two-digit ports is the ordinary content of a port
        cell (`23,24,25` -- a coil tap, a bump row).  Bare it fits the measured
        budget exactly; tagged it needs 131% of it, in a widget with no
        scrollbar and no overflow indicator, so the reader sees `F2.23,24` and
        nothing says a character was dropped.  That is why the tag is on the
        CROSSING endpoint only and never on every cell.
        """
        root = tk.Tk()
        try:
            root.geometry("1600x700")
            root.update()
            cell = self._tightest_cell(root)
            budget = tkfont.nametofont("TkDefaultFont", root=root).measure(
                "0" * self._visible_chars(root, cell))
            f = tkfont.nametofont("TkDefaultFont", root=root)
            self.assertEqual(budget, TAG_FITS_PX)
            self.assertLessEqual(f.measure("23,24,25"), budget,
                                 "a bare three-port group no longer fits")
            self.assertGreater(f.measure("F2.23,24,25"), budget,
                               "a tagged group now fits -- re-derive R3-2")
            # And the reviewer's own string, reproduced to the pixel.
            self.assertEqual(f.measure("EM:23,24,25"), 70)
        finally:
            root.destroy()

    def test_alias_digits_left_is_MEASURED_not_counted(self):
        """
        Characters and pixels do not agree, and the difference is a whole digit.

        `EM.` is two characters plus a separator, so the character count says
        four digits are left of seven; measured it is 22 px of the 49 px budget
        and THREE digits fit, because E and M are wider than a digit.  A window
        quoting the character count would overstate every letter-heavy alias by
        one -- which is the direction that hides the problem.

        Mutation: drop the `measure` argument from `alias_digits_left` and this
        goes red on `EM` while staying green on `F2`.
        """
        root = tk.Tk()
        try:
            root.update()
            m = tkfont.nametofont("TkDefaultFont", root=root).measure
            self.assertEqual(fg.alias_digits_left("EM"), 4)      # counted
            self.assertEqual(fg.alias_digits_left("EM", m), 3)   # measured
            self.assertEqual(fg.alias_digits_left("F2"), 4)
            self.assertEqual(fg.alias_digits_left("F2", m), 4)
        finally:
            root.destroy()


# ===========================================================================
# PURE: default file scope (R3-2)
# ===========================================================================

class TestDefaultScope(unittest.TestCase):
    """
    A bare number means the HOME file.  That one rule is what keeps every
    existing spec, every golden case and every saved session unchanged.
    """

    def test_a_home_port_renders_BARE(self):
        self.assertEqual(fg.render_port_cell("F1", [12], "F1"), "12")
        self.assertEqual(fg.render_port_cell("F1", [23, 24, 25], "F1"), "23-25")

    def test_a_foreign_port_renders_TAGGED(self):
        self.assertEqual(fg.render_port_cell("F2", [12], "F1"), "F2.12")
        self.assertEqual(fg.render_port_cell("F2", [40, 41, 42], "F1"),
                         "F2.40-42")

    def test_the_home_comparison_is_case_insensitive(self):
        """`ComposedNetwork.block_of_alias` lowercases, so this must too, or
        a cell typed `f2.` would be rendered back tagged as if it were foreign
        and the two spellings would disagree about the same port."""
        self.assertEqual(fg.render_port_cell("f2", [3], "F2"), "3")
        self.assertFalse(fg.cell_is_foreign("f2.3", "F2", ["F2"]))

    def test_a_bare_cell_means_the_home_file(self):
        self.assertEqual(fg.cell_scope("40-42", "F1", ["F1", "F2"]),
                         ("F1", "40-42"))
        self.assertFalse(fg.cell_is_foreign("40-42", "F1", ["F1", "F2"]))

    def test_a_tagged_cell_names_its_file(self):
        self.assertEqual(fg.cell_scope("F2.40-42", "F1", ["F1", "F2"]),
                         ("F2", "40-42"))
        self.assertTrue(fg.cell_is_foreign("F2.40-42", "F1", ["F1", "F2"]))

    def test_an_UNKNOWN_tag_is_distinguishable_from_no_tag_at_all(self):
        """
        Two different mistakes wanting two different messages: 'you named a
        file that is not in this trace' and 'you named no file, so I used the
        home one'.  Folding them together is how a typo'd tag becomes a silent
        home-file reference -- the exact silent-wrong-answer shape this feature
        exists to remove.

        Mutation: make `cell_scope` fall back to `home` for an unrecognised
        tag and this is the only test that notices.
        """
        alias, body = fg.cell_scope("PKG.4", "F1", ["F1", "F2"])
        self.assertEqual((alias, body), ("PKG", "4"))
        self.assertNotIn(alias, ["F1", "F2"])

    def test_a_tag_that_cannot_be_an_alias_is_NOT_read_as_a_tag(self):
        """
        `comp._split_tag` returns ('', token) for a head that fails `_ALIAS_RE`,
        which is what makes `1.5` a malformed port and not file `1`'s port 5.
        This module reaches for that function by name rather than re-deriving
        it, so the cell and the parser cannot disagree.
        """
        self.assertEqual(fg.cell_scope("1.5", "F1", ["F1"]), ("F1", "1.5"))

    def test_render_NEVER_emits_a_space(self):
        """
        `collapse_ports`'s rule, and the reason for it: the DSL is
        whitespace-tokenised and the port field is `parts[0]`, so `F2.40, 41`
        parses as the field `F2.40,` with a stray `41` where the keyword
        belongs.  Every rendered cell must round-trip through the parser.
        """
        for locals_ in ([1], [1, 2, 3], [40, 41, 42], [1, 3, 5, 9]):
            for home, alias in (("F1", "F1"), ("F1", "F2")):
                cell = fg.render_port_cell(alias, locals_, home)
                self.assertNotIn(" ", cell, f"{cell!r} carries a space")

    def test_ports_spanning_two_files_render_as_TWO_CELLS_not_one(self):
        """
        A port field carries ONE scope, and that is the parser's rule, not a
        convenience: `parse_scoped_ports` refuses a tag on any comma token
        after the first because `F1.1,F2.3` could mean 'one field, two scopes'
        or 'F1 scopes the lot' and the two answers differ in silence.  So a
        renderer returning a single string for a two-file set would emit
        exactly the spelling the parser refuses.

        Mutation: make `render_port_cells` join with ',' and this is the only
        test that fails -- the string looks perfectly reasonable.
        """
        cells = fg.render_port_cells([("F1", [1]), ("F2", [3])], "F1")
        self.assertEqual(cells, ["1", "F2.3"])
        self.assertEqual(len(cells), 2)

    def test_an_empty_port_list_renders_to_an_empty_cell(self):
        self.assertEqual(fg.render_port_cell("F2", [], "F1"), "")
        self.assertEqual(fg.render_port_cells([("F2", [])], "F1"), [])


class TestScopeAgainstTheRealParser(unittest.TestCase):
    """
    The rendering is only worth anything if `parse_scoped_ports` reads it back.

    A real `ComposedNetwork` throughout: the round trip has to survive the
    global renumbering, which is the whole thing a hand-built stub would fake.
    """

    @classmethod
    def setUpClass(cls):
        em_a, _em_b, pkg = weld_files()
        cls.net = compose([ComposeInput(em_a, "F1"), ComposeInput(pkg, "F2")],
                          marker_hz=MARK_HZ)

    def test_a_bare_cell_resolves_against_the_HOME_file(self):
        self.assertEqual(fg.resolve_cell("1", self.net, "F1"),
                         [self.net.gport("F1", 1)])
        self.assertEqual(fg.resolve_cell("1", self.net, "F2"),
                         [self.net.gport("F2", 1)])

    def test_the_same_cell_text_means_a_DIFFERENT_PORT_in_a_different_home(self):
        """
        Which is why the home file is on screen and not implicit: `1` is global
        port 1 with F1 as home and global port 3 with F2 as home, and nothing
        about the text says so.  The window's job is to make the home visible;
        this is the failure it is visible against.
        """
        a = fg.resolve_cell("1", self.net, "F1")
        b = fg.resolve_cell("1", self.net, "F2")
        self.assertNotEqual(a, b)

    def test_every_port_of_every_file_round_trips_through_a_cell(self):
        for home in ("F1", "F2"):
            for block in self.net.blocks:
                for local in block.local_ports:
                    cell = fg.render_port_cell(block.alias, [local], home)
                    with self.subTest(home=home, cell=cell):
                        self.assertEqual(
                            fg.resolve_cell(cell, self.net, home),
                            [self.net.gport(block.alias, local)])

    def test_a_rendered_RANGE_round_trips(self):
        cell = fg.render_port_cell("F2", [1, 2, 3], "F1")
        self.assertEqual(cell, "F2.1-3")
        self.assertEqual(fg.resolve_cell(cell, self.net, "F1"),
                         [self.net.gport("F2", p) for p in (1, 2, 3)])

    def test_cell_round_trip_ok_agrees_with_the_parser(self):
        self.assertTrue(fg.cell_round_trip_ok("1", self.net, "F1"))
        self.assertTrue(fg.cell_round_trip_ok("F2.1-3", self.net, "F1"))
        self.assertTrue(fg.cell_round_trip_ok("", self.net, "F1"))
        self.assertFalse(fg.cell_round_trip_ok("F9.1", self.net, "F1"))
        self.assertFalse(fg.cell_round_trip_ok("not-a-port", self.net, "F1"))


# ===========================================================================
# PURE: the alias rules
# ===========================================================================

class TestAliasBudgetRule(unittest.TestCase):
    """
    THE ONE alias rule that lives here, and the reason it lives here.

    Whether a tag is spellable, and whether two files claim the same one, are
    `pkg_rlc_gui.compose_spec_problems`'s -- it owns the file set.  What it
    cannot know is how wide a port cell is, which is a pixel measurement.  The
    split is deliberate: two half-answers to "is this tag ok" is how the GUI
    and the CLI came to give one file opposite verdicts on reciprocity.
    """

    def test_a_tag_that_fits_is_accepted(self):
        for a in ("F1", "F2", "EM", "PKG", "d1", ""):
            self.assertEqual(fg.alias_budget_refusal(a), "", a)

    def test_an_alias_LONGER_than_the_cell_can_carry_is_refused(self):
        """
        The measurement, enforced: at four characters the tag is 73% of the
        49 px budget at 100% and 76% at 150%, leaving one digit of port number
        in a cell with no scrollbar and no overflow marker.

        Mutation: raise `ALIAS_MAX_CHARS` to 4 and this goes green while
        `PKG.101` starts rendering as `PKG.10` on screen.
        """
        self.assertEqual(fg.ALIAS_MAX_CHARS, 3)
        self.assertEqual(fg.alias_budget_refusal("PKG"), "")
        msg = fg.alias_budget_refusal("ABCD")
        self.assertTrue(msg)
        self.assertIn("4 characters", msg)

    def test_it_does_NOT_re_check_what_compose_spec_problems_owns(self):
        """
        Mutation: add the regex and duplicate checks back here and this goes
        red -- which is the point.  An alias this function accepts may still be
        refused by the schema; they are different questions.
        """
        self.assertEqual(fg.alias_budget_refusal("1x"), "")
        self.assertEqual(fg.alias_budget_refusal("F2"), "")

    def test_the_refusal_does_not_quote_a_digit_count_it_did_not_measure(self):
        """
        The character count and the pixel measurement disagree for a
        letter-heavy tag ('ABCD.' is 5 characters of 7 but 36 px of 49, i.e.
        one digit and not two).  The refusal therefore quotes the measured
        fraction, not the arithmetic.
        """
        msg = fg.alias_budget_refusal("ABCD")
        self.assertNotIn("leave 2", msg)
        self.assertIn("three quarters", msg)


class TestAliasLegend(unittest.TestCase):
    def test_the_legend_uses_the_results_table_s_own_idiom(self):
        """
        Not 'a similar format': `_format_results_table` writes `F1=<label>`
        joined by spaces when a table spans files, and this window has to write
        the same thing or two screens disagree about which file F2 is.
        """
        slots = _slots(("coil.s16p", 16), ("package.s60p", 60))
        legend = fg.alias_legend(slots, "F1")
        self.assertIn("F1=coil.s16p", legend)
        self.assertIn("F2=package.s60p", legend)

    def test_the_home_file_is_MARKED(self):
        """'which one do I not have to type' is the question this exists for."""
        slots = _slots(("coil.s16p", 16), ("package.s60p", 60))
        self.assertIn("home", fg.alias_legend(slots, "F1").split("F2=")[0])
        self.assertNotIn("home", fg.alias_legend(slots, "F1").split("F2=")[1])

    def test_one_file_says_a_port_cell_is_unchanged(self):
        hint = fg.scope_hint(_slots(("coil.s16p", 16)), "F1")
        self.assertIn("bare port number", hint)
        self.assertIn("always was", hint)

    def test_two_files_spell_out_the_tag(self):
        hint = fg.scope_hint(_slots(("a.s2p", 2), ("b.s3p", 3)), "F1")
        self.assertIn("BARE", hint)
        self.assertIn("F2.", hint)


class TestPortChoices(unittest.TestCase):
    """
    The dropdown's ORDER is the affordance -- the cheap gesture has to be the
    right one, which is the same rule that puts merged nodes at the top.
    """

    def test_merged_nodes_stay_at_the_very_top(self):
        """
        `_refresh_port_choices` puts a net name above the port numbers because
        referring to a merged node by name is the gesture that does NOT
        multiply a lumped element by N.  Adding a second file must not push it
        down.
        """
        got = fg.port_choices(_slots(("a.s2p", 2), ("b.s2p", 2)), "F1",
                              extra=["coil_tap"])
        self.assertEqual(got[0], "coil_tap")

    def test_the_home_file_comes_first_and_BARE(self):
        got = fg.port_choices(_slots(("a.s2p", 2), ("b.s2p", 2)), "F1")
        self.assertEqual(got, ["1", "2", "F2.1", "F2.2"])

    def test_the_home_file_comes_first_even_when_it_is_not_slot_zero(self):
        """Home is a CHOICE, not a position: a user who makes the package the
        home file types package ports bare from then on."""
        got = fg.port_choices(_slots(("a.s2p", 2), ("b.s2p", 2)), "F2")
        self.assertEqual(got, ["1", "2", "F1.1", "F1.2"])

    def test_one_file_offers_exactly_what_it_always_did(self):
        """A single-file user must not be able to tell this round happened."""
        self.assertEqual(fg.port_choices(_slots(("a.s4p", 4)), "F1"),
                         ["1", "2", "3", "4"])


# ===========================================================================
# PURE: what crosses between the files
# ===========================================================================

class TestCrossFileRows(unittest.TestCase):
    """
    Section 0's requirement in one function: "what you built is what you
    measure" makes showing what was BUILT the first duty, and on a two-file
    trace the invisible thing is which rows join the files.
    """

    def test_a_crossing_row_is_reported_with_its_cell_and_its_file(self):
        rows = _rows(("rlc_between", "1", "F2.12"), ("ground", "3", ""))
        got = fg.cross_file_rows(rows, "F1", ["F1", "F2"])
        self.assertEqual(len(got), 1)
        idx, kind, hits = got[0]
        self.assertEqual((idx, kind), (0, "rlc_between"))
        self.assertEqual(hits, [("to", "F2", "12")])

    def test_a_row_wholly_inside_the_FAR_file_is_reported_too(self):
        """
        It is not a link -- both ends are in the package -- but it is not a home
        row either, and a summary that dropped it would say a package-internal
        element does not exist.  The `alias` in the result is what tells them
        apart, which is why it is returned rather than a bool.
        """
        rows = _rows(("rlc_gnd", "F2.40-42", ""))
        got = fg.cross_file_rows(rows, "F1", ["F1", "F2"])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][2], [("ports", "F2", "40-42")])

    def test_a_purely_home_table_reports_nothing(self):
        rows = _rows(("short", "1,2,3", ""), ("ground", "4", ""))
        self.assertEqual(fg.cross_file_rows(rows, "F1", ["F1", "F2"]), [])

    def test_the_NET_cell_is_not_treated_as_a_port_field(self):
        """
        A net is a NAME, and core's net rules forbid whitespace and `:,-#` --
        but NOT a dot.  So `F2.tap` is a legal node name that a scope reader
        would happily report as a reference to file F2, and the summary would
        claim a row crosses between files when it does nothing of the kind.

        The name has to CARRY A DOT for this to be a test at all: a bare `F2`
        has no tag for `_split_tag` to find, so it resolves to the home file
        and the mutation goes undetected.  (It did: the first version of this
        test used `net="F2"` and the mutation survived.)

        Mutation: add 'net' to `_PORT_KEYS` and this goes red.
        """
        rows = [ConnectionRow(kind="short", ports="1,2,3", to="")]
        setattr(rows[0], "net", "F2.tap")
        self.assertEqual(fg.cross_file_rows(rows, "F1", ["F1", "F2"]), [])

    def test_an_RLC_value_is_not_treated_as_a_port_field_either(self):
        """A lumped value can carry a dot too (`0.5`, and `F2.5` is not a
        plausible value but `_PORT_KEYS` is what stops it being read as one)."""
        rows = [ConnectionRow(kind="rlc_gnd", ports="1", to="", R="0.5")]
        setattr(rows[0], "L", "F2.5")
        self.assertEqual(fg.cross_file_rows(rows, "F1", ["F1", "F2"]), [])

    def test_it_never_raises_on_half_typed_input(self):
        """It runs from the strips' path, on every keystroke."""
        for text in ("F2.", "F2", ".", "F2.,", "-", "1-", ",,,", "F2.1-"):
            with self.subTest(text=text):
                fg.cross_file_rows(_rows(("ground", text, "")), "F1",
                                   ["F1", "F2"])

    def test_the_summary_says_so_when_NOTHING_crosses(self):
        """
        Two files stacked and never connected is a real and silent mistake:
        `block_diag` welds their references, so they are two networks sharing a
        ground and the answer is the near file's alone.  A blank line would
        read as 'nothing to report'.
        """
        lines = fg.cross_file_summary(_rows(("ground", "1", "")), "F1",
                                      ["F1", "F2"])
        self.assertTrue(lines)
        self.assertIn("NOT", lines[0])

    def test_the_summary_counts_the_rows_it_names(self):
        rows = _rows(("rlc_between", "1", "F2.1"), ("ground", "2", ""),
                     ("rlc_between", "3", "F2.2"))
        lines = fg.cross_file_summary(rows, "F1", ["F1", "F2"])
        self.assertIn("2 of 3", lines[0])
        self.assertEqual(len(lines), 3)

    def test_an_empty_table_says_nothing_at_all(self):
        self.assertEqual(fg.cross_file_summary([], "F1", ["F1"]), [])


# ===========================================================================
# PURE: R3-5, the reference-node check as text
# ===========================================================================

def _check(alias, verdict, label="pkg.s3p", message=""):
    return comp.ReferenceCheck(
        alias=alias, label=label, ports=[1], verdict=verdict,
        max_delta=0.0, rel_delta=0.0, freq_hz=5e9, probe_l=(1e-9, 1e-8),
        message=message or f"{alias} ({label}): {verdict}")


class TestReferenceRendering(unittest.TestCase):
    """
    The weld raises nothing and makes no number look wrong -- measured, the
    package ground pad grounded / open / through 1 nH give L_eff = 2.1454 nH,
    bit-identical, spread 0.000e+00 -- so what it changes is how the number
    must be READ, and it has to arrive where the number is.
    """

    def test_no_composition_produces_NOTHING(self):
        """
        Zero pixels and zero report lines for a single-file trace, which is
        every trace that exists today.  This is the property that makes the
        whole of R3-5 free for everyone who is not composing.
        """
        self.assertEqual(fg.reference_strip_text([]), ("", False))
        self.assertEqual(fg.reference_report_lines([]), [])
        self.assertEqual(fg.reference_provenance([]), ((), ()))

    def test_a_WELD_outranks_every_other_verdict(self):
        """
        Mutation: order the branches the other way round and a composition with
        one welded file and one healthy one reports the healthy one.  A weld is
        the only verdict that makes the numbers under it mean something other
        than they appear to, so it is the one that must survive a clip.
        """
        checks = [_check("F1", comp.REF_LIVE),
                  _check("F2", comp.REF_WELDED),
                  _check("F3", comp.REF_NO_GROUND)]
        text, warn = fg.reference_strip_text(checks)
        self.assertTrue(warn)
        self.assertTrue(text.startswith("WELD:"))
        self.assertIn("F2", text)
        self.assertNotIn("F1", text)

    def test_the_weld_line_says_what_to_DO(self):
        """A verdict with no action is a bug report, not a diagnosis."""
        text, _ = fg.reference_strip_text([_check("F2", comp.REF_WELDED)])
        self.assertIn("PORT", text)
        self.assertIn("connect it", text)

    def test_an_all_healthy_composition_still_SAYS_SO(self):
        """
        Present rather than blank on purpose: a mandatory check that shows
        nothing when it passes cannot be told from one that did not run, and
        this one costs two solves per file precisely so it can be trusted.
        """
        text, warn = fg.reference_strip_text([_check("F1", comp.REF_LIVE)])
        self.assertTrue(text)
        self.assertFalse(warn)

    def test_NO_GROUND_is_not_a_warning_and_is_not_folded_into_the_weld(self):
        """
        `REF_NO_GROUND` is deliberately not `welded` in core: the CORRECT
        die-return-as-a-port configuration declares no package ground, so
        colouring it as a fault cries wolf on exactly the composition this
        feature exists to make work.
        """
        text, warn = fg.reference_strip_text([_check("F2", comp.REF_NO_GROUND)])
        self.assertFalse(warn)
        self.assertNotIn("WELD", text)
        self.assertIn("no ground port", text)

    def test_UNKNOWN_is_a_warning_because_the_check_did_not_answer(self):
        text, warn = fg.reference_strip_text([_check("F2", comp.REF_UNKNOWN)])
        self.assertTrue(warn)
        self.assertIn("could not run", text)

    def test_the_report_carries_the_headline_and_every_verdict(self):
        checks = [_check("F1", comp.REF_LIVE), _check("F2", comp.REF_WELDED)]
        lines = fg.reference_report_lines(checks)
        self.assertIn("REFERENCE-NODE CHECK", lines[0])
        body = "\n".join(lines)
        for c in checks:
            self.assertIn(c.message, body)
        self.assertIn("WELD", body)

    def test_the_strip_and_the_report_come_from_ONE_call(self):
        """
        Mutation: build the two separately at the call site and they can drift
        -- a strip announcing a weld over a report that does not mention it is
        the same class of defect as two definitions of `_config_signature`.
        """
        checks = [_check("F2", comp.REF_WELDED)]
        strip, notes = fg.reference_provenance(checks)
        self.assertEqual(strip, fg.reference_strip_text(checks))
        self.assertEqual(list(notes), fg.reference_report_lines(checks))


class TestReferenceChecksOf(unittest.TestCase):
    """
    The seam.  `reference_checks_of` is the one place that knows where a
    computed composition is cached, and [] is what it must answer for
    everything else.
    """

    def test_a_trace_with_no_composition_gives_an_empty_list(self):
        self.assertEqual(fg.reference_checks_of(_FakeTrace()), [])

    def test_it_reads_a_ComposedSolution(self):
        checks = [_check("F2", comp.REF_WELDED)]

        class _Sol:
            reference = checks

        self.assertEqual(fg.reference_checks_of(_FakeTrace(composed=_Sol())),
                         checks)

    def test_it_reads_a_bare_list_too(self):
        checks = [_check("F2", comp.REF_WELDED)]
        self.assertEqual(
            fg.reference_checks_of(_FakeTrace(reference_checks=checks)),
            checks)

    def test_it_does_not_raise_on_a_defective_cache(self):
        """It is called from a path a decomposition has already been paid for;
        a broken cache must cost the strip, not the window."""
        self.assertEqual(fg.reference_checks_of(_FakeTrace(composed=object())),
                         [])


# ===========================================================================
# PURE: the trace's file list
# ===========================================================================

class TestSlots(unittest.TestCase):
    def test_a_trace_with_one_file_has_one_slot_and_it_is_HOME(self):
        """
        Which is every trace in every session that exists today, so this is the
        shipping behaviour and not a fallback.
        """
        trace = _FakeTrace(file_label="coil.s4p")
        slots = fg.slots_of(_FakeApp(), trace)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].alias, "F1")
        self.assertTrue(slots[0].is_home(fg.home_alias(slots)))

    def test_the_home_file_is_first_and_is_never_listed_twice(self):
        trace = _FakeTrace(file_label="a.s2p")
        setattr(trace, fg.TRACE_FILES_FIELD, ["b.s2p", "a.s2p", "c.s2p"])
        self.assertEqual(fg.trace_file_labels(trace),
                         ["a.s2p", "b.s2p", "c.s2p"])
        self.assertEqual([s.alias for s in fg.slots_of(_FakeApp(), trace)],
                         ["F1", "F2", "F3"])

    def test_a_file_that_is_not_loaded_is_KEPT_and_marked(self):
        """
        A session whose folder moved keeps its traces (`_on_calculate` already
        says `file '…' not loaded`), and a file silently missing from THIS list
        is a composition the user cannot see is broken.
        """
        slots = fg.slots_of(_FakeApp(), _FakeTrace(file_label="gone.s4p"))
        self.assertEqual(len(slots), 1)
        self.assertFalse(slots[0].loaded)
        self.assertEqual(slots[0].label, "gone.s4p")

    def test_slots_of_never_raises(self):
        for trace in (None, object(), _FakeTrace(file_label="")):
            with self.subTest(trace=trace):
                fg.slots_of(_FakeApp(), trace)

    def test_home_alias_of_nothing_is_still_F1(self):
        self.assertEqual(fg.home_alias([]), "F1")


class TestFileListsAgree(unittest.TestCase):
    """
    The mirror cannot drift, because this runs both halves over the same
    battery.

    `pkg_rlc_gui.trace_file_labels` is the LIVE definition -- `_config_signature`,
    the port descriptor, the CSV header and the plot legend all read it -- and
    `pkg_rlc_files_gui` keeps a fallback only so it can answer on a build
    without the schema (it is imported by `pkg_rlc_attrib_gui`, and the two
    land independently).  Two answers to "which files is this trace made of" is
    the drift this repo has been bitten by; this is what makes it impossible to
    ship silently.

    Mutation: sort the fallback's output, or let it keep a repeat, and the
    corresponding case below goes red.
    """

    CASES = (
        ("a.s2p", []),
        ("a.s2p", ["b.s2p"]),
        ("a.s2p", ["b.s2p", "c.s2p"]),
        ("a.s2p", ["a.s2p"]),                 # the home file, repeated
        ("a.s2p", ["b.s2p", "b.s2p"]),        # a repeat among the extras
        ("a.s2p", ["", "b.s2p"]),             # a half-filled entry
        ("", ["b.s2p"]),                      # no home file at all
        ("", []),
    )

    def test_the_two_definitions_answer_identically(self):
        import pkg_rlc_gui as g
        if not fg.trace_files_supported():                   # pragma: no cover
            self.skipTest("this build stores one file per trace")
        for home, extra in self.CASES:
            with self.subTest(home=home, extra=extra):
                tc = TraceConfig(id=1, file_label=home)
                setattr(tc, fg.TRACE_FILES_FIELD, list(extra))
                self.assertEqual(fg._trace_file_labels_fallback(tc),
                                 g.trace_file_labels(tc))

    def test_the_public_function_really_delegates(self):
        """
        Mutation: make `trace_file_labels` call the fallback directly and this
        goes red -- which is the point, because then the test above would be
        comparing the fallback with itself.
        """
        import pkg_rlc_gui as g
        if not fg.trace_files_supported():                   # pragma: no cover
            self.skipTest("this build stores one file per trace")
        tc = TraceConfig(id=1, file_label="a.s2p")
        sentinel = ["sentinel.s1p"]
        real = g.trace_file_labels
        g.trace_file_labels = lambda _tc: list(sentinel)
        try:
            self.assertEqual(fg.trace_file_labels(tc), sentinel)
        finally:
            g.trace_file_labels = real

    def test_the_aliases_agree_too(self):
        """`trace_file_aliases` is the tag every other surface prints."""
        import pkg_rlc_gui as g
        if not fg.trace_files_supported():                   # pragma: no cover
            self.skipTest("this build stores one file per trace")
        if not hasattr(g, "trace_file_aliases"):             # pragma: no cover
            self.skipTest("this build has no trace_file_aliases")
        tc = TraceConfig(id=1, file_label="a.s2p")
        setattr(tc, fg.TRACE_FILES_FIELD, ["b.s2p", "c.s2p"])
        self.assertEqual(
            [(s.alias, s.label) for s in fg.slots_of(_FakeApp(), tc)],
            list(g.trace_file_aliases(tc)))


# ===========================================================================
# TK: the file-pair window (R3-3)
# ===========================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _WindowCase(unittest.TestCase):
    """A real App with a real file behind it.  Not shared: these mutate it."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()
        cls.ts = parse_touchstone(FIXTURES / FIXTURE)

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(self.ts)
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        # Mode 1, not 5: an empty mode-5 spec has no measurement port, so
        # Calculate refuses it and `tc.Z` stays None -- and a test that then
        # asserts "editing the file set marks the trace stale" passes for the
        # wrong reason, because the stale rule only bites on a trace that has
        # numbers.  (It did: the first version of this harness used mode 5.)
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                              port_a="1", label="coil")
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _settle(self, rounds=6):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _open(self, mapped=False):
        if mapped:
            self.app.deiconify()
        win = fg.open_files_window(self.app, self.tc)
        self.assertIsNotNone(win)
        if mapped:
            win.deiconify()
        self._settle(8)
        return win


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFilePairWindow(_WindowCase):
    def test_it_opens_and_registers_itself(self):
        win = self._open()
        self.assertIsInstance(win, fg.FilePairWindow)
        self.assertEqual(fg.live_windows(self.app), [win])

    def test_a_second_open_RAISES_the_same_window(self):
        """One per trace, not one per click: a second copy of a read-only panel
        is two things to keep in sync and two things to close."""
        first = self._open()
        second = fg.open_files_window(self.app, self.tc)
        self.assertIs(first, second)
        self.assertEqual(len(fg.live_windows(self.app)), 1)

    def test_it_is_MODELESS(self):
        """No grab_set anywhere: a modal Toplevel that outlives its opener
        blocks event delivery and update() never returns, which takes the GUI
        and the test suite down together."""
        self._open()
        self.assertIsNone(self.app.grab_current())

    def test_it_lists_the_trace_s_one_file_as_the_home_file(self):
        win = self._open()
        rows = win.tree.get_children()
        self.assertEqual(len(rows), 1)
        values = win.tree.item(rows[0], "values")
        self.assertIn("F1", values[0])
        self.assertIn("home", values[0])
        self.assertEqual(values[1], self.fe.label)

    def test_the_legend_names_the_file_and_says_it_is_home(self):
        win = self._open()
        self.assertIn(self.fe.label, win.legend.cget("text"))
        self.assertIn("home", win.legend.cget("text"))

    def test_a_single_file_trace_is_told_its_cells_have_not_changed(self):
        """The one thing a user of the shipping tool needs from this window:
        nothing about their port cells is different."""
        win = self._open()
        self.assertIn("always was", win.scope.cget("text"))

    def test_a_missing_file_is_shown_rather_than_dropped(self):
        self.tc.file_label = "gone.s4p"
        win = self._open()
        rows = win.tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertIn("warn", win.tree.item(rows[0], "tags"))
        self.assertIn("not loaded", win.tree.item(rows[0], "values"))

    def test_refresh_never_raises_on_a_broken_trace(self):
        """The `_apply_editor_strips` contract: an error raised there reaches
        no handler anyone controls and the window carries on showing a stale
        file list, which is the one thing it must not do."""
        win = self._open()
        win._trace = object()
        win.refresh()
        fg.refresh_files_windows(self.app)

    def test_refresh_files_windows_drops_a_destroyed_window(self):
        win = self._open()
        win.destroy()
        self._settle()
        fg.refresh_files_windows(self.app)
        self.assertEqual(fg.live_windows(self.app), [])

    def test_escape_closes_it(self):
        """It is a read-only panel that rebuilds itself from live state on
        reopen -- the PortRolesWindow case, not the Attribution one, which
        holds a result that cost a Recompute."""
        win = self._open(mapped=True)
        # focus_force + when="now" is what actually delivers a synthetic key to
        # a Toplevel: without focus the event is queued against a window the WM
        # is not directing input at, and `winfo_exists()` reads 1 with nothing
        # to say the key went nowhere.
        win.focus_force()
        self._settle()
        win.event_generate("<Escape>", when="now")
        self._settle()
        self.assertFalse(win.winfo_exists())

    def test_set_home_refuses_BY_NAME_when_nothing_is_selected(self):
        """A button that silently does nothing is a bug report."""
        win = self._open()
        win._on_set_home()
        self.assertIn("Select a file", win.foot_note.cget("text"))

    def test_set_home_refuses_BY_NAME_on_a_one_file_build(self):
        """
        The schema seam, stated on screen.  While `TraceConfig` carries one
        file, 'make this the home file' has no second file to be about, and the
        refusal names the field it would need -- so the message is actionable
        by whoever wires the schema, not just by the user.

        Mutation: return silently instead and the button becomes dead with no
        explanation, which is the failure the disabled-Keep-button rule exists
        to prevent.
        """
        win = self._open()
        win.tree.selection_set(win.tree.get_children()[0])
        win._on_set_home()
        text = win.foot_note.cget("text")
        self.assertTrue(text)
        self.assertIn("already the home file", text)

    def test_report_lines_carry_everything_the_labels_clip(self):
        """Every strip in this application clips (`wraplength=0`); the report
        is what makes that safe."""
        win = self._open()
        text = "\n".join(win.report_lines())
        self.assertIn(self.fe.label, text)
        self.assertIn("F1=", text)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFileSetEditing(_WindowCase):
    """
    R3-3: which files this trace uses.

    Add / Remove are on a RIGHT-CLICK MENU and that is MEASURED, not taste.
    At the 520x300 minimum this window's footer has 504 px of usable width,
    and Close + Set as home + Remove + Add + a file combobox ask:

        100%   87 + 87 + 87 + 87 + 149 = 517 px  (and the status label was
               already unmapped, winfo_ismapped 0)
        150%   186 x4 + 311 = 1075 px -- `Add` and the combobox unmapped,
               `Remove` clipped to 120 of the 186 it asked for

    i.e. the Files-row / Traces-row overflow this window sits on the menubar
    to avoid, one level down.  A menu costs zero pixels.
    """

    def setUp(self):
        super().setUp()
        # A second loaded file, so there is something to add.
        self.fe2 = FileEntry(parse_touchstone(FIXTURES / SECOND_FIXTURE))
        self.app.files.append(self.fe2)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self._settle()

    def test_a_file_can_be_ADDED_and_the_list_shows_it_as_F2(self):
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        rows = win.tree.get_children()
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows), ["F1", "F2"])
        self.assertEqual(win.tree.item("F2", "values")[1], self.fe2.label)

    def test_adding_a_file_makes_the_trace_STALE(self):
        """
        The spec moved, so the drawn curve is older than the trace describing
        it -- the `_apply_editor_sync` rule.  Without it the plot keeps last
        run's single-file curve under a two-file spec with no marker.

        Mutation: drop the `tc.stale = True` branch and this goes red.
        """
        self.app._on_calculate()
        self._settle()
        self.assertFalse(self.tc.stale)
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        self.assertTrue(self.tc.stale)

    def test_a_file_can_be_REMOVED_again(self):
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        slot = [s for s in win._slots if s.alias == "F2"][0]
        win._on_remove_file(slot)
        self._settle()
        self.assertEqual(len(win.tree.get_children()), 1)
        self.assertEqual(fg.trace_file_labels(self.tc), [self.fe.label])

    def test_the_user_is_told_the_TAGS_RENUMBERED(self):
        """
        A tag is a POSITION, so removing a file renumbers every tag after it
        and a `F3.<port>` already typed now names a different file.  Nothing
        can rewrite those cells -- they are the user's text -- so the only
        honest thing is to say so, in the pane where results are read as well
        as on the window.

        Mutation: drop the `_append_result` call and this goes red.
        """
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        # The WHOLE Log, not a slice from a saved mark: the pane already has
        # content at startup and Text index arithmetic across an append is not
        # what is being tested here.
        logged = self.app.results_text.get("1.0", "end")
        self.assertIn(self.fe2.label, logged)
        self.assertIn("POSITION", logged)
        self.assertIn("POSITION", win.foot_note.cget("text"))

    def test_a_FROZEN_trace_refuses_by_name(self):
        """
        A snapshot's numbers and the spec printed beside them have to keep
        describing each other, and changing its file set would change what
        every bare port number in it meant.  Same refusal, same reason, as
        `set_trace_home_file` and `_sync_editor_to_trace`.
        """
        self.tc.frozen = True
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        self.assertEqual(fg.trace_file_labels(self.tc), [self.fe.label])
        self.assertIn("frozen", win.foot_note.cget("text"))

    def test_the_menu_offers_only_files_that_are_not_already_here(self):
        win = self._open()
        self.assertEqual(win._candidate_files(), [self.fe2.label])
        win._on_add_file(self.fe2.label)
        self._settle()
        self.assertEqual(win._candidate_files(), [])

    def test_the_HOME_file_cannot_be_removed_from_the_menu(self):
        """
        The editor's File field owns the home file, and a trace with no home
        file has no meaning for a bare port number.  Change the home first.
        """
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        home = [s for s in win._slots if s.is_home(win._home)][0]
        win._on_remove_file(home)
        self._settle()
        self.assertIn(self.fe.label, fg.trace_file_labels(self.tc))

    def test_set_as_home_goes_through_the_App_s_own_hook(self):
        """
        Never a direct write to `file_label`: the editor owns that combobox, so
        a poked value is overwritten by the very next `_sync_editor_to_trace`.

        Mutation: call `setattr(trace, 'file_label', ...)` here instead and the
        editor's File field disagrees with the window.
        """
        win = self._open()
        win._on_add_file(self.fe2.label)
        self._settle()
        win.tree.selection_set("F2")
        win._on_set_home()
        self._settle()
        self.assertEqual(self.tc.file_label, self.fe2.label)
        self.assertEqual(self.app.ed_file_var.get(), self.fe2.label)
        # ...and the tags swapped, which is what the message has to say.
        self.assertEqual([s.label for s in win._slots],
                         [self.fe2.label, self.fe.label])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFilePairWindowReference(_WindowCase):
    """R3-5 inside the files window."""

    def test_the_strip_is_NOT_MANAGED_without_a_composition(self):
        """
        Zero pixels, and `winfo_manager` is the only thing that can say so: a
        ttk.Label reports `winfo_reqheight() == 21` whether or not it is
        managed (measured), so an empty one packed here would cost every
        single-file session a line for a check with nothing to say.

        Mutation: pack it unconditionally with empty text and this goes red
        while nothing on screen looks wrong.
        """
        win = self._open(mapped=True)
        self.assertEqual(win.ref_strip.winfo_manager(), "")
        self.assertFalse(win.ref_strip.winfo_ismapped())

    def test_a_WELD_appears_in_the_window(self):
        win = self._open(mapped=True)
        setattr(self.tc, "reference_checks",
                [_check("F2", comp.REF_WELDED, "package.s3p")])
        win.refresh()
        self._settle()
        self.assertEqual(win.ref_strip.winfo_manager(), "pack")
        self.assertTrue(win.ref_strip.cget("text").startswith("WELD:"))
        self.assertEqual(str(win.ref_strip.cget("foreground")),
                         str(win._warn_fg))

    def test_the_strip_goes_away_again_when_the_composition_does(self):
        win = self._open(mapped=True)
        setattr(self.tc, "reference_checks", [_check("F2", comp.REF_WELDED)])
        win.refresh()
        self._settle()
        self.assertEqual(win.ref_strip.winfo_manager(), "pack")
        setattr(self.tc, "reference_checks", [])
        win.refresh()
        self._settle()
        self.assertEqual(win.ref_strip.winfo_manager(), "")

    def test_the_report_carries_the_whole_check_not_the_one_line(self):
        win = self._open()
        setattr(self.tc, "reference_checks",
                [_check("F1", comp.REF_LIVE), _check("F2", comp.REF_WELDED)])
        win.refresh()
        text = "\n".join(win.report_lines())
        self.assertIn("REFERENCE-NODE CHECK", text)
        self.assertIn("F1", text)
        self.assertIn("F2", text)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFilePairWindowLayout(_WindowCase):
    """
    MEASURED off a MAPPED window.  A withdrawn root answers 0 to every geometry
    query, which is exactly the wrong answer being ruled out.
    """

    def _at(self, win, w, h):
        win.geometry(f"{w}x{h}")
        for _ in range(8):
            win.update_idletasks()
            win.update()

    def test_close_and_set_home_survive_the_declared_minimum(self):
        win = self._open(mapped=True)
        self._at(win, fg.FILES_MIN_W, fg.FILES_MIN_H)
        self.assertTrue(win.home_btn.winfo_ismapped(),
                        "Set as home fell off the window at the minimum size")
        self.assertTrue(win.tree.winfo_ismapped())

    def test_the_LIST_is_what_gives_up_height_and_never_the_footer(self):
        """
        pack allocates in call order and UNMAPS FROM THE END, so the footer is
        packed FIRST (side=BOTTOM) and the Treeview LAST with expand=True.  Get
        that order wrong and the buttons vanish outright when the window is
        short -- `winfo_ismapped()` reads 0, nothing raises, and there is no
        scrollbar and no other route to them.  That is the measured Global
        Controls failure ("Calculate All & Plot / Export CSV / Help were not on
        screen") arriving in this window.

        The declared minimum is NOT short enough to show it -- measured, at
        520x300 everything fits either way, and an earlier version of this test
        asserted at the minimum and the mutation walked straight through.  So
        the floor is lifted for the duration and the window really is dragged
        too short for its content.

        MEASURED here, window height against what is on screen:

            300 px (the declared floor)   tree mapped, Set as home mapped
             90 px                        tree mapped, Set as home mapped
             40 px                        tree UNMAPPED, Set as home mapped

        `winfo_ismapped` is the only query that sees it: `winfo_height()`
        answers 161 for the Treeview at all three sizes, because pack does not
        shrink a slave it has stopped mapping.

        Mutation: `body.pack(..., before=foot)`, i.e. the expanding body ahead
        of the footer in the packing list, and `home_btn.winfo_ismapped()` goes
        to 0 at 40 px.  Note that flipping the footer's `side=tk.BOTTOM` to
        `tk.TOP` is NOT this bug and does not defeat the guard: `side` decides
        the position, the CALL ORDER decides who gives up space.
        """
        win = self._open(mapped=True)
        win.minsize(1, 1)
        self._at(win, fg.FILES_MIN_W, fg.FILES_MIN_H)   # let the floor drop
        self._at(win, fg.FILES_MIN_W, 40)
        # Precondition, asserted rather than assumed: a window the WM refused
        # to shrink would make everything below pass for the wrong reason.
        self.assertLess(win.winfo_height(), fg.FILES_MIN_H,
                        "the window did not actually get short")
        self.assertTrue(
            win.home_btn.winfo_ismapped(),
            "the footer was unmapped before the list gave up its space")
        self.assertFalse(
            win.tree.winfo_ismapped(),
            "the list was still mapped, so this size proves nothing")

    def test_the_list_keeps_three_rows_at_the_minimum(self):
        """Two files is the case this exists for; the third row is what shows
        there is room for one more."""
        win = self._open(mapped=True)
        self._at(win, fg.FILES_MIN_W, fg.FILES_MIN_H)
        self.assertGreaterEqual(int(win.tree.cget("height")), 3)

    def test_every_header_strip_is_on_screen_at_the_minimum(self):
        win = self._open(mapped=True)
        self._at(win, fg.FILES_MIN_W, fg.FILES_MIN_H)
        for name in ("header", "legend", "scope"):
            self.assertTrue(getattr(win, name).winfo_ismapped(),
                            f"{name} is not on screen at the minimum size")

    def test_it_still_lays_out_at_150_percent_font_scaling(self):
        """
        The supported 150% DPI, this repo's definition (`tk scaling 2.0` plus
        every named font x1.5).  What is asserted is that the fixed sections
        survive -- a Treeview's rowheight is frozen at 20 px whatever the font,
        which is why `_install_style` derives it from the font's own metrics on
        a DERIVED style name.
        """
        win = self._open(mapped=True)
        f = tkfont.nametofont("TkDefaultFont", root=win)
        base = f.cget("size")
        try:
            f.configure(size=int(round(abs(base) * 1.5))
                        * (1 if base > 0 else -1))
            fg.FilePairWindow._install_style(win)
            self._at(win, fg.FILES_MIN_W, fg.FILES_MIN_H)
            self.assertTrue(win.home_btn.winfo_ismapped())
            self.assertTrue(win.tree.winfo_ismapped())
            style_h = ttk.Style(win).lookup(fg._FILES_STYLE, "rowheight")
            self.assertGreaterEqual(
                int(style_h), f.metrics("linespace"),
                "the Treeview rowheight did not follow the font -- rows clip")
        finally:
            f.configure(size=base)
            # Destroyed here rather than re-styled: `style.configure` broadcasts
            # <<ThemeChanged>> to every widget of the interpreter, and doing
            # that on a window whose App tearDown is about to destroy makes Tcl
            # print `can't invoke "event" command: application has been
            # destroyed` -- console noise in a test run, invisible in a
            # double-clicked GUI, and exactly the class of leak
            # `_stability_after` is cancelled to avoid.  The next test builds a
            # fresh App, i.e. a fresh interpreter with fresh styles.
            win.destroy()

    def test_the_derived_style_does_not_touch_the_global_Treeview(self):
        """
        Reconfiguring `Treeview` itself would reach every Treeview in the
        process, including the Ports & Roles list.  A dotted name inherits the
        layout and overrides nothing else.

        Mutation: configure "Treeview" instead and this goes red.
        """
        win = self._open()
        self.assertTrue(fg._FILES_STYLE.endswith(".Treeview"))
        style = ttk.Style(win)
        # The derived name carries the rowheight...
        self.assertNotEqual(style.lookup(fg._FILES_STYLE, "rowheight"), "")
        # ...and the global one still carries NOTHING, which is the claim.
        # Measured: a fresh interpreter answers '' for Treeview/rowheight, and
        # configuring a dotted child leaves it ''.
        #
        # Mutation: `style.configure("Treeview", rowheight=...)` in
        # `_install_style` and this second assertion goes red, while the window
        # itself looks exactly the same -- the damage is to every OTHER
        # Treeview in the process, starting with Ports & Roles.
        self.assertEqual(style.lookup("Treeview", "rowheight"), "")


# ===========================================================================
# TK: R3-5 in the Attribution window -- where the number is read
# ===========================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestAttributionReferenceStrip(unittest.TestCase):
    """
    The weld has to arrive where a number is READ, which in this application is
    the Attribution window: every contribution attributed to an element in a
    welded file reads as exactly 0 with a healthy residual beside it, and a
    reader looking at a table of zeroes needs the reason on the same screen.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()
        import pkg_rlc_attrib_gui as ag
        cls.ag = ag
        cls.ts = parse_touchstone(FIXTURES / FIXTURE)

    def setUp(self):
        from pkg_rlc_core import MeasPortRow
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(self.ts)
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=6,
                              label="coil", gnd_ports="2,4",
                              mports=[MeasPortRow("vic", "1", ""),
                                      MeasPortRow("agg", "3", "")])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app.rlc_freq_var.set("5.1")
        self.app._on_calculate()
        self._settle()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _settle(self, rounds=6):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _open(self, mapped=True):
        if mapped:
            self.app.deiconify()
        win = self.ag.open_attribution_window(self.app, self.tc)
        self.assertIsNotNone(win, "the Attribution window refused to open")
        if mapped:
            win.deiconify()
        self._settle(10)
        return win

    def test_a_single_file_trace_pays_NOTHING_for_this(self):
        """
        Not managed, so not a pixel -- and `winfo_manager` is the only query
        that can see it: an unmanaged ttk.Label still answers
        `winfo_reqheight() == 21`.

        Mutation: pack the strip unconditionally and this goes red while the
        window still looks fine, because the cost is 21 px of the 168 px the
        split has at the 720x420 minimum.
        """
        win = self._open()
        self.assertEqual(win.ref_strip.winfo_manager(), "")
        self.assertEqual(win._res.prov.reference_strip, ())
        self.assertEqual(win._res.prov.reference_notes, ())

    def test_chrome_height_does_not_count_a_strip_that_is_not_there(self):
        """
        The mutation this exists for: drop the `winfo_manager()` guard from
        `_chrome_height` and it grows by the unmanaged label's 21 px plus its
        2 px of padding, raising the enforced minimum height of every
        single-file window for a widget nobody can see.
        """
        win = self._open()
        before = win._chrome_height()
        win.ref_strip.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 0),
                           before=win._badge_row)
        self._settle()
        after = win._chrome_height()
        self.assertGreater(after, before,
                           "a packed strip must be counted")
        win.ref_strip.pack_forget()
        self._settle()
        self.assertEqual(win._chrome_height(), before,
                         "an unpacked strip must NOT be counted")

    def test_a_weld_reaches_the_strip_and_the_copied_report(self):
        """
        Both surfaces from ONE `reference_provenance` call, so they cannot
        describe different compositions.
        """
        setattr(self.tc, "reference_checks",
                [_check("F2", comp.REF_WELDED, "package.s3p")])
        win = self._open()
        win._on_recompute()
        self._settle(10)
        self.assertEqual(win.ref_strip.winfo_manager(), "pack")
        self.assertTrue(win.ref_strip.cget("text").startswith("WELD:"))
        report = self.ag.report_text(win._res.prov, win._res.dec)
        self.assertIn("REFERENCE-NODE CHECK", report)
        self.assertIn("F2", report)

    def test_the_strip_sits_between_reconciliation_and_the_badge(self):
        """
        Placement is the claim: it is a precondition on trusting everything
        below it, like Reconciliation, and it must not be pushed off the bottom
        by a long across-frequency verdict.

        Mutation: drop `before=self._badge_row` and it lands after the badge,
        which `winfo_ismapped` cannot see.
        """
        setattr(self.tc, "reference_checks", [_check("F2", comp.REF_WELDED)])
        win = self._open()
        win._on_recompute()
        self._settle(10)
        order = [str(w) for w in win.pack_slaves()]
        self.assertIn(str(win.ref_strip), order)
        self.assertLess(order.index(str(win.recon)),
                        order.index(str(win.ref_strip)))
        self.assertLess(order.index(str(win.ref_strip)),
                        order.index(str(win._badge_row)))

    def test_the_provenance_is_FROZEN_not_read_live(self):
        """
        The run-snapshot rule: a window kept open across a re-compose must not
        print THIS run's contributions under the NEXT composition's verdict.

        Mutation: read `reference_checks_of(self._trace)` inside
        `_apply_reference_strip` and this goes red -- and nothing raises, and
        the numbers stay real, which is what makes it the worst kind of bug.
        """
        setattr(self.tc, "reference_checks", [_check("F2", comp.REF_WELDED)])
        win = self._open()
        win._on_recompute()
        self._settle(10)
        frozen = win._res.prov.reference_strip
        self.assertTrue(frozen)
        # The trace's composition changes; the window's must not.
        setattr(self.tc, "reference_checks", [_check("F2", comp.REF_LIVE)])
        win._render()
        self._settle()
        self.assertEqual(win._res.prov.reference_strip, frozen)
        self.assertTrue(win.ref_strip.cget("text").startswith("WELD:"))

    def test_an_existing_report_is_UNCHANGED_without_a_composition(self):
        """
        Every report ever produced by this window must be untouched, or R3-5
        changed a surface it had no business touching.

        The assertion is a DELTA, not a self-comparison: the same Provenance
        with and without the notes must differ by exactly one separator plus
        the notes themselves.  Emitting the block unconditionally adds a blank
        line in the no-notes case, which makes the delta one SHORT -- and an
        earlier version of this test compared `provenance_lines(prov)` with
        itself and was a tautology that the mutation walked straight through.

        Mutation: drop the `if prov.reference_notes:` guard in
        `provenance_lines` and this goes red.
        """
        from dataclasses import replace
        win = self._open()
        bare = win._res.prov
        self.assertEqual(bare.reference_notes, ())
        lines_bare = self.ag.provenance_lines(bare)
        self.assertNotIn("REFERENCE-NODE CHECK", "\n".join(lines_bare))

        notes = ("line one", "line two")
        lines_full = self.ag.provenance_lines(
            replace(bare, reference_notes=notes))
        self.assertEqual(len(lines_full) - len(lines_bare), 1 + len(notes))
        for n in notes:
            self.assertIn(n, lines_full)


# ===========================================================================
# The results table's alias idiom -- the thing this must not diverge from
# ===========================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestAliasIdiomMatchesTheResultsTable(unittest.TestCase):
    """
    `_format_results_table` has labelled a multi-file table F1/F2 since before
    this round.  If the two ever disagree, `F2` means one file in a port cell
    and another in the table under the plot.
    """

    def test_the_results_table_still_labels_files_F1_and_F2(self):
        class _Res:
            R_ohm = L_henry = C_farad = 1.0
            Q = 1.0

        class _Row:
            def __init__(self, fl, i):
                self.file_label = fl
                self.id = i
                self.label = f"t{i}"
                self.port_desc = "M1: S:[1] G:[]"
                self.res = _Res()
                self.enabled = True
                self.color_idx = 0

        text = _format_results_table([_Row("coil.s16p", 1),
                                      _Row("package.s60p", 2)], "smart")
        self.assertIn("F1=coil.s16p", text)
        self.assertIn("F2=package.s60p", text)
        # ... and this module hands out the same two.
        self.assertEqual(comp.default_alias(0), "F1")
        self.assertEqual(comp.default_alias(1), "F2")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
