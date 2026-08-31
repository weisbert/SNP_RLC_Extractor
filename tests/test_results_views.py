"""
The Results pane's THREE VIEWS, and the slimming that is common to all of them.

WHY THIS EXISTS.  The report had exactly one shape and it was the widest one.
Measured on the run that prompted the change -- two composed mode-6 traces --
it was 40 lines and 3538 characters against a Results pane that shows a
MEASURED 144 columns at the default 1500x900 window, 102 at 1200x800 and 79 at
the 1040x600 minsize, with `wrap=tk.NONE` so the tail of a long line is
reachable only by a horizontal scroll that takes the Port column off the left
edge at the same time.  Twelve of the forty lines were over 90 columns and the
widest was 272.

Worse than the width was the REPETITION: the 272-column coupling legend and the
262-column reference-node verdict were each printed once per trace, verbatim,
which is 1068 of those 3538 characters -- 30% of the report was one of two
sentences said twice.

  * `detail`  -- what the tool always had, minus those two paragraphs, minus
                 the Z matrix in the case where it is provably redundant.
  * `summary` -- the whole run as two tables, so comparing traces is reading
                 down a column instead of paging between blocks.
  * `compare` -- traces as COLUMNS with a delta, which is the question a run
                 holding two revisions of one structure exists to answer.

The pure formatters are tested with no display; the wiring is tested against a
real App.  Every guard here was mutation-checked and the defeating mutation is
named in the test that catches it.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

from pkg_rlc.physics.core import (  # noqa: E402
    CouplingResult,
    MeasPortRow,
    PairCoupling,
    PortRLC,
    RLCResult,
    parse_touchstone,
)
from pkg_rlc.frontend.app import (  # noqa: E402
    COMPARE_STACK_LINES_MAX,
    COUPLING_LEGEND_LINES,
    RESULTS_PANE_COLS,
    RESULTS_SWATCH,
    RESULTS_VIEWS,
    SUMMARY_LABEL_MAX,
    VIEW_COMPARE,
    VIEW_DETAIL,
    VIEW_SUMMARY,
    App,
    CouplingSnapshot,
    FileEntry,
    RowSnapshot,
    RunSnapshot,
    TraceConfig,
    _compare_head_cells,
    _delta_cell,
    _format_compare,
    _format_coupling_block,
    _format_results_table,
    _format_summary_coupling,
    _format_summary_self,
    _render_columns,
    _wrap_name,
    digits_sig,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"

#: The pane's measured width in characters at the default 1500x900 window
#: (1014 px of Consolas 9, whose every glyph this report emits is 7 px).  It is
#: the budget a line has to stay inside to be readable without scrolling.
PANE_COLS = 144


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

W = 2.0 * math.pi * 5.55e9


def _port(name: str, Z: complex) -> PortRLC:
    return PortRLC(name=name, Z=Z, R_ohm=Z.real, L_henry=Z.imag / W,
                   C_farad=-1.0 / (W * Z.imag), Q=Z.imag / Z.real)


def _db(ratio: float) -> float:
    if not math.isfinite(ratio) or ratio == 0.0:
        return float("nan")
    return 20.0 * math.log10(abs(ratio))


def _pair(a: str, b: str, Z_ab: complex, La: float, Lb: float,
          notes=()) -> PairCoupling:
    M = Z_ab.imag / W
    ra, rb = M / La, M / Lb
    return PairCoupling(name_a=a, name_b=b, Z_ab=Z_ab, M_henry=M,
                        C_c_farad=-1.0 / (W * Z_ab.imag),
                        k=M / math.sqrt(abs(La * Lb)),
                        M_over_La=ra, M_over_Lb=rb,
                        M_over_La_dB=_db(ra), M_over_Lb_dB=_db(rb),
                        notes=list(notes))


def _block(bid: int, label: str, Zs: dict, pairs=(), color_idx: int = 0,
           recip: float = 2.1e-10, file_label: str = "em.s4p",
           **kw) -> CouplingSnapshot:
    """A coupling snapshot from {port name: Z} and a list of PairCoupling."""
    names = list(Zs)
    ports = [_port(n, Zs[n]) for n in names]
    G = len(names)
    Zk = np.zeros((G, G), dtype=complex)
    for i, n in enumerate(names):
        Zk[i, i] = Zs[n]
    for p in pairs:
        i, j = names.index(p.name_a), names.index(p.name_b)
        Zk[i, j] = Zk[j, i] = p.Z_ab
    cres = CouplingResult(freq_hz=5.55e9, Z_matrix=Zk, names=names,
                          ports=ports, pairs=list(pairs),
                          reciprocity_error=recip)
    return CouplingSnapshot(id=bid, label=label, port_desc=f"M6: {G} mports",
                            enabled=True, color_idx=color_idx,
                            file_label=file_label, cres=cres, **kw)


def _two_port_block(bid=1, label="WUR_EM", zaa=9.924 + 112.6j,
                    zbb=4.831 + 49.40j, zab=-0.04322 - 0.01799j,
                    color_idx=0, **kw) -> CouplingSnapshot:
    """The reported run's own numbers, which is what every width claim here
    was measured on."""
    Zs = {"VCO": zaa, "RX": zbb}
    p = _pair("VCO", "RX", zab, zaa.imag / W, zbb.imag / W)
    return _block(bid, label, Zs, [p], color_idx=color_idx, **kw)


def _three_port_block(bid=7, label="osc") -> CouplingSnapshot:
    Zs = {"L1": 1.5 + 126.0j, "L2": 1.6 + 130.0j, "L3": 1.7 + 140.0j}
    pairs = [_pair("L1", "L2", 0.01 + 13.0j, 126.0 / W, 130.0 / W),
             _pair("L1", "L3", 0.01 + 1.3j, 126.0 / W, 140.0 / W),
             _pair("L2", "L3", 0.01 + 4.0j, 130.0 / W, 140.0 / W)]
    return _block(bid, label, Zs, pairs)


def _row(rid=2, label="tank", R=1.5, L=2.0e-9, C=-1.2e-12, Q=0.84,
         color_idx=1, file_label="em.s4p", **kw) -> RowSnapshot:
    res = RLCResult(freq_hz=5.55e9, Z=complex(R, L * W), R_ohm=R, L_henry=L,
                    C_farad=C, Q=Q)
    return RowSnapshot(id=rid, label=label, port_desc="M1: 1 -> GND",
                       enabled=True, color_idx=color_idx,
                       file_label=file_label, res=res, **kw)


def _run(blocks=(), rows=(), number=5) -> RunSnapshot:
    return RunSnapshot(number=number, when=None, marker_freq_hz=5.55e9,
                       rows=tuple(rows), blocks=tuple(blocks))


def _compare_split(text: str, n_body: int):
    """
    (header lines, body lines) of a rendered compare table.

    The body row count is passed rather than sniffed on purpose.  A compare
    header is now as many lines deep as the stacked trace name needs, and every
    rule that could tell a header line from a body row by looking at it is
    wrong on real data: a name line carries digits ('0812EM'), a group label
    carries none, and an id cell carries them inside brackets.  The fixture
    knows how many rows it produces -- 4 quantities per measurement port plus 4
    per pair -- so stating it here is both exact and self-documenting.
    """
    lines = text.split("\n")
    assert len(lines) > n_body, f"no header left: {len(lines)} lines"
    return lines[:-n_body], lines[-n_body:]


def _duplicates_for_compare(app, tc, labels):
    """
    One extra mode-6 trace per label, so `compare` has that many more columns.

    Returns them, so the caller can take them back out: these run against one
    App per test method, and a leftover trace would change the header shape the
    next subTest measures -- which is exactly the thing being measured.
    """
    made = []
    for i, label in enumerate(labels):
        dup = TraceConfig(id=tc.id + 1 + i, file_label=tc.file_label, mode=6,
                          label=label, color_idx=(1 + i) % 12,
                          mports=[MeasPortRow(name="c1", plus="1"),
                                  MeasPortRow(name="c2", plus="2")])
        app.traces.append(dup)
        made.append(dup)
    app._refresh_trace_list()
    return made


def _looks_like_the_legend_shape(lines) -> bool:
    """
    True when the table put the names on their own lines above it.

    A legend line carries exactly ONE swatch and nothing after the name; the
    stacked shape's id line carries one per column.  Sniffing the shape rather
    than being told it is deliberate -- these are the App's own rendered lines,
    and the point is what a reader sees.
    """
    return any(ln.count(RESULTS_SWATCH) == 1 and "]" in ln
               and len(ln.split("] ", 1)) == 2
               and " " not in ln.split("] ", 1)[1].strip()
               for ln in lines[:12])


# ============================================================================
# The slimming (common to every view)
# ============================================================================

class TestTheLegendLeftTheBlock(unittest.TestCase):
    """
    It was 272 columns, printed once per block.  Two blocks put 544 characters
    of ONE sentence into a pane that shows 144 of them.
    """

    def test_no_block_carries_a_legend_any_more(self):
        """Mutation: put the legend line back in _format_coupling_block."""
        for name, block in (("2-port", _two_port_block()),
                            ("3-port", _three_port_block())):
            with self.subTest(name):
                text = _format_coupling_block(block, "smart")
                self.assertNotIn("legend:", text)
                self.assertNotIn("Norton", text)

    def test_every_legend_line_fits_the_pane_without_scrolling(self):
        """
        The whole complaint about the old one was that it could not be read.
        Mutation: fold the three lines back into one -- 297 columns.
        """
        for line in COUPLING_LEGEND_LINES:
            with self.subTest(line[:40]):
                self.assertLessEqual(
                    len(line), PANE_COLS,
                    f"{len(line)} columns against a {PANE_COLS}-column pane")

    def test_the_load_bearing_sentence_survived_the_shortening(self):
        """
        'M/L is the Norton injection ratio, NOT the exact current ratio' is one
        of the six places that claim has to agree (core docstring, CLI, this
        legend, Help, README, theory.md).  Shortening the legend may not drop
        it.
        """
        text = "\n".join(COUPLING_LEGEND_LINES)
        self.assertIn("Norton injection ratio", text)
        self.assertIn("NOT the exact current ratio", text)
        self.assertIn("|Z_ab/Z_aa|", text)
        self.assertIn("signs are physical", text)
        self.assertIn("never clipped", text)


class TestTheReciprocityLineIsAVerdict(unittest.TestCase):
    """Verdict and number; the DEFINITION of the metric is not a reading."""

    def _line(self, recip, pairs=None):
        blk = _two_port_block(recip=recip)
        if pairs is not None:
            blk = _block(1, "x", {"VCO": 1 + 1j}, pairs, recip=recip)
        text = _format_coupling_block(blk, "smart")
        return next(ln for ln in text.split("\n")
                    if "reciprocal" in ln or "reciprocity" in ln
                    or "RECIPROCITY" in ln)

    def test_a_healthy_file_is_a_tick_and_a_number(self):
        """Mutation: restore the parenthetical definition -- 140 columns."""
        line = self._line(2.14e-10)
        self.assertIn("✓ reciprocal", line)
        self.assertIn("2.14e-10", line)
        self.assertNotIn("max|Z_ab-Z_ba|", line)
        self.assertLessEqual(len(line), PANE_COLS)

    def test_the_alarm_keeps_its_sentence_because_there_it_IS_the_reading(self):
        line = self._line(4e-3)
        self.assertIn("⚠ RECIPROCITY", line)
        self.assertIn("4e-03", line.replace("0.004", "4e-03"))
        self.assertIn("suspect", line)

    def test_nothing_to_check_says_so(self):
        line = self._line(0.0, pairs=[])
        self.assertIn("nothing to check", line)

    def test_the_definition_moved_to_the_legend_not_into_thin_air(self):
        """A definition dropped from one place and added to none is a
        deletion.  Mutation: remove the Help pointer from the legend."""
        text = "\n".join(COUPLING_LEGEND_LINES)
        self.assertIn("Help", text)


class TestTheRedundantZMatrixIsFolded(unittest.TestCase):
    """
    At two measurement ports the matrix is [[Z_aa, Z_ab], [Z_ab, Z_bb]] and
    every entry of it is printed again in the two tables underneath.  The claim
    is REDUNDANCY, so the test checks the numbers, not the shape.
    """

    def test_at_two_ports_the_matrix_rows_are_gone(self):
        """Mutation: make `matrix_block` unconditionally True."""
        text = _format_coupling_block(_two_port_block(), "smart")
        self.assertNotIn("self impedance (diagonal):", text)
        # The matrix's own header row is a line of nothing but port names.
        for line in text.split("\n"):
            self.assertNotEqual(line.split(), ["VCO", "RX"],
                                f"the matrix header survived: {line!r}")

    def test_at_three_ports_the_matrix_block_is_untouched(self):
        """It earns its place there: G(G-1)/2 off-diagonals, and a pair list
        cannot show them as a matrix.  Mutation: fold at every G."""
        text = _format_coupling_block(_three_port_block(), "smart")
        self.assertIn("self impedance (diagonal):", text)
        self.assertIn("Z (Ω)", text.replace("Z (Ω)", "", 0) or text) \
            if False else None
        self.assertNotIn("Z (Ω)", text)
        self.assertNotIn("Z_ab =", text)

    def test_every_number_the_matrix_carried_is_still_on_screen(self):
        """
        THE REDUNDANCY CLAIM ITSELF.  Mutation: drop the Z column, or drop
        Z_ab from the pair line -- either loses a number the matrix used to
        show and this goes red.
        """
        zaa, zbb, zab = 9.924 + 112.6j, 4.831 + 49.40j, -0.04322 - 0.01799j
        text = _format_coupling_block(
            _two_port_block(zaa=zaa, zbb=zbb, zab=zab), "smart")
        for z in (zaa, zbb, zab):
            cell = f"{z.real:.4g}{z.imag:+.4g}j"
            self.assertIn(cell, text, f"{cell} is not on screen any more")

    def test_the_frequency_line_survives_at_every_port_count(self):
        """
        It is this block's frequency PROVENANCE -- tests/test_freq_label.py
        pins that the Calculate banner and this line name one frequency, and
        folding the matrix must not take the frequency with it.

        Mutation: emit the 'Z matrix @' line only when matrix_block.
        """
        for name, blk in (("1-port", _block(1, "x", {"L1": 1.5 + 126.0j})),
                          ("2-port", _two_port_block()),
                          ("3-port", _three_port_block())):
            with self.subTest(name):
                head = _format_coupling_block(blk, "smart").split("\n")[1]
                self.assertIn("Z matrix @", head)
                self.assertIn("5.55 GHz", head)

    def test_the_fold_pays_for_itself_in_lines(self):
        """Four lines of matrix become one column.  Mutation: none -- this is
        the measurement the change was made on, kept as a number."""
        n = len(_format_coupling_block(_two_port_block(), "smart").split("\n"))
        self.assertLessEqual(n, 8, "the two-port block grew back")


class TestTheSelfTableStaysCleanAtThreePorts(unittest.TestCase):
    """The Z column is added ONLY where the matrix block is not printed, so
    the G >= 3 rendering has to come out byte-identical to what it was."""

    def test_no_row_gained_trailing_whitespace(self):
        """
        'Sign' is the last cell there, and padding a last cell puts trailing
        spaces on every row of a table that gets copied into a mail.

        Mutation: pad Sign unconditionally.
        """
        text = _format_coupling_block(_three_port_block(), "smart")
        for line in text.split("\n"):
            self.assertEqual(line, line.rstrip(),
                             f"trailing whitespace: {line!r}")


# ============================================================================
# The footer, once per run
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheFooterSaysEachThingOnce(unittest.TestCase):

    def setUp(self):
        self.app = App()
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()

    def _segs(self, run, view=VIEW_DETAIL):
        self.app.results_view_var.set(view)
        return self.app._run_report_segments(run)

    def _text(self, run, view=VIEW_DETAIL):
        return "\n".join(t for t, _c, _s in self._segs(run, view))

    def test_the_legend_is_printed_once_for_a_two_block_run(self):
        """
        Mutation: move the legend back inside _format_coupling_block -- it
        then appears twice and this goes red.
        """
        run = _run([_two_port_block(1, "WUR_EM"),
                    _two_port_block(4, "0812EM")])
        text = self._text(run)
        self.assertEqual(text.count(COUPLING_LEGEND_LINES[0]), 1)

    def test_a_run_with_no_coupling_gets_no_coupling_legend(self):
        """There is no ind/cap column and no M/L on a scalar table, so the
        legend would be qualifying nothing.  Mutation: drop the `if
        shown_blocks` gate."""
        self.assertNotIn("Norton", self._text(_run(rows=[_row()])))

    def test_identical_reference_verdicts_collapse_and_name_every_trace(self):
        """
        524 of the reported run's 3538 characters were this sentence, twice.

        Mutation: go back to one segment per record -- the count becomes 2.
        """
        strip = "Reference-node check: F1, F2 declares no ground port, so …"
        run = _run([_two_port_block(1, "WUR_EM", ref_strip=strip),
                    _two_port_block(4, "0812EM", ref_strip=strip)])
        text = self._text(run)
        self.assertEqual(text.count(strip), 1)
        self.assertIn(f"[1][4] {strip}", text)

    def test_verdicts_that_DIFFER_are_never_collapsed(self):
        """
        The id list is what makes the collapsed line a statement about those
        traces.  Two different checks under one id list would put one trace's
        ids on another trace's verdict.

        Mutation: key the grouping on ref_strip alone, then give the two
        records different ref_lines -- they merge and this goes red.
        """
        run = _run([_two_port_block(1, "a", ref_strip="check A",
                                    ref_warn=True, ref_lines=("why A",)),
                    _two_port_block(4, "b", ref_strip="check A",
                                    ref_warn=True, ref_lines=("why B",))])
        text = self._text(run)
        self.assertIn("why A", text)
        self.assertIn("why B", text)
        self.assertNotIn("[1][4]", text)

    def test_a_collapsed_warning_keeps_its_WARN_severity(self):
        """The Log badge counts warnings, and a deduplicated warning is still
        a warning.  Mutation: emit LOG_INFO for the grouped line."""
        import pkg_rlc.frontend.app as gui
        run = _run([_two_port_block(1, "a", ref_strip="s", ref_warn=True,
                                    ref_lines=("d",)),
                    _two_port_block(4, "b", ref_strip="s", ref_warn=True,
                                    ref_lines=("d",))])
        sev = {s for t, _c, s in self._segs(run) if "[1][4]" in t}
        self.assertEqual(sev, {gui.LOG_WARN})

    def test_the_attribution_pointer_names_both_routes_and_fits(self):
        """
        Both phrases are pinned elsewhere too; what is pinned HERE is that the
        shortening kept the route on screen.  Mutation: restore the 147-column
        wording -- three columns past the pane.
        """
        run = _run([_two_port_block()])
        line = next(ln for ln in self._text(run).split("\n")
                    if "where each M above comes from" in ln)
        self.assertIn("right-click menu", line)
        self.assertLessEqual(len(line), PANE_COLS)

    def test_the_footer_is_the_same_in_every_view(self):
        """
        It qualifies the RUN, not the rendering.  Mutation: gate the legend on
        `view == VIEW_DETAIL` -- the compact views then print ind/cap and M/L
        with nothing that says what they mean.
        """
        run = _run([_two_port_block(1, "a"), _two_port_block(4, "b")],
                   rows=[_row()])
        for view in RESULTS_VIEWS:
            with self.subTest(view):
                text = self._text(run, view)
                self.assertEqual(text.count(COUPLING_LEGEND_LINES[0]), 1)
                self.assertIn("where each M above comes from", text)


# ============================================================================
# The summary view
# ============================================================================

class TestTheSummaryTables(unittest.TestCase):

    def test_one_row_per_measurement_port_and_one_per_pair(self):
        blocks = [_two_port_block(1, "WUR_EM"), _two_port_block(4, "0812EM")]
        self_text, self_colors = _format_summary_self([], blocks, "smart")
        coup_text, coup_colors = _format_summary_coupling(blocks, "smart")
        # 1 header + 2 ports x 2 traces
        self.assertEqual(len([ln for ln in self_text.split("\n")
                              if ln.lstrip().startswith(RESULTS_SWATCH)]), 4)
        self.assertEqual(len(self_colors), 4)
        self.assertEqual(len([ln for ln in coup_text.split("\n")
                              if ln.lstrip().startswith(RESULTS_SWATCH)]), 2)
        self.assertEqual(len(coup_colors), 2)

    def test_a_scalar_row_and_a_coupling_port_share_the_table(self):
        """That is the whole point of the self table: one row per SELF
        measurement, whatever mode produced it."""
        text, colors = _format_summary_self([_row(2, "tank")],
                                            [_two_port_block(1, "coils")],
                                            "smart")
        self.assertIn("tank", text)
        self.assertIn("VCO", text)
        self.assertEqual(len(colors), 3)

    def test_the_colours_are_in_the_order_the_swatched_lines_appear(self):
        """
        _tag_swatch_rows consumes the list in order, so a mismatch silently
        gives a row another trace's colour.

        Mutation: append rec.id instead of rec.color_idx.
        """
        blocks = [_two_port_block(1, "a", color_idx=5),
                  _two_port_block(4, "b", color_idx=9)]
        _text, colors = _format_summary_self([], blocks, "smart")
        self.assertEqual(colors, (5, 5, 9, 9))

    def test_the_ranking_and_the_floor_are_the_detail_views_own(self):
        """
        Two views disagreeing about which coupling matters is worse than
        either being wrong on its own.

        Mutation: sort the pairs here instead of calling rank_coupling_pairs.
        """
        Zs = {"L1": 1.5 + 126j, "L2": 1.6 + 130j, "L3": 1.7 + 140j}
        strong = _pair("L1", "L2", 0.01 + 13.0j, 126 / W, 130 / W)
        weak = _pair("L1", "L3", 0.01 + 1e-9j, 126 / W, 140 / W)
        text, _c = _format_summary_coupling(
            [_block(1, "pkg", Zs, [weak, strong])], "smart")
        rows = [ln for ln in text.split("\n")
                if ln.lstrip().startswith(RESULTS_SWATCH)]
        self.assertEqual(len(rows), 1, text)
        self.assertIn("L1 x L2", rows[0])
        self.assertIn("+1 pair below -60 dB", text)

    def test_k_is_not_given_an_SI_prefix(self):
        """
        k = -2.412e-4 through format_si is '-241 u' -- a micro-nothing.

        Mutation: format k with the value formatter like M and C_c.
        """
        text, _c = _format_summary_coupling([_two_port_block()], "smart")
        self.assertNotIn(" u ", text)
        self.assertIn("-0.0002412", text)

    def test_every_summary_line_fits_the_pane(self):
        blocks = [_two_port_block(1, "WUR_EM"), _two_port_block(4, "0812EM")]
        for text, _c in (_format_summary_self([], blocks, "smart"),
                         _format_summary_coupling(blocks, "smart")):
            for line in text.split("\n"):
                self.assertLessEqual(len(line), PANE_COLS, line)

    def test_the_file_column_appears_only_for_more_than_one_file(self):
        one = _format_summary_self([], [_two_port_block(1, "a")], "smart")[0]
        two = _format_summary_self(
            [], [_two_port_block(1, "a", file_label="one.s4p"),
                 _two_port_block(4, "b", file_label="two.s4p")], "smart")[0]
        self.assertNotIn("File", one)
        self.assertIn("F1=one.s4p", two)
        self.assertIn("F2=two.s4p", two)


# ============================================================================
# The compare view
# ============================================================================

class TestTheDeltaCell(unittest.TestCase):
    """How a change is expressed.  Pure arithmetic, no widgets."""

    def test_a_readable_change_is_a_percentage(self):
        self.assertEqual(_delta_cell(1.0, 1.1, "H"), "+10 %")
        self.assertEqual(_delta_cell(4.831, 10.58, "Ω"), "+119 %")

    def test_a_big_change_becomes_a_FACTOR(self):
        """
        M went -516 fH -> -7.19 pH on the reported run.  As a percentage that
        is -1293%, which is not a sentence anybody says out loud.

        Mutation: drop the crossover and always print a percentage.
        """
        self.assertEqual(_delta_cell(-516e-15, -7.19e-12, "H"), "+13.93 ×")

    def test_the_crossover_is_a_factor_of_ten_either_way(self):
        self.assertIn("%", _delta_cell(1.0, 9.5, "H"))
        self.assertIn("×", _delta_cell(1.0, 11.0, "H"))
        self.assertIn("%", _delta_cell(1.0, 0.5, "H"))

    def test_dB_gets_a_dB_DIFFERENCE_and_never_a_percentage(self):
        """
        dB is already a ratio; a percentage of decibels means nothing.

        Mutation: treat 'dB' like any other unit -- -68.77 -> -52.36 comes out
        as '+23.9 %', which is a number with no meaning at all.
        """
        self.assertEqual(_delta_cell(-68.77, -52.36, "dB"), "+16.41 dB")

    def test_a_sign_change_falls_out_of_the_same_expression(self):
        self.assertEqual(_delta_cell(1.0, -1.0, "H"), "-200 %")

    def test_a_missing_or_undefined_value_is_a_dash_not_a_zero(self):
        self.assertEqual(_delta_cell(float("nan"), 1.0, "H"), "—")
        self.assertEqual(_delta_cell(1.0, float("inf"), "H"), "—")
        self.assertEqual(_delta_cell(0.0, 1.0, "H"), "—")
        self.assertEqual(_delta_cell(0.0, 0.0, "H"), "0")


class TestTheCompareTable(unittest.TestCase):

    def _two(self):
        return [_two_port_block(1, "WUR_EM"),
                _two_port_block(4, "0812EM", zaa=9.812 + 112.7j,
                                zbb=10.58 + 104.0j, zab=0.01344 - 0.2506j,
                                color_idx=3)]

    def test_one_column_per_trace_and_a_delta_at_exactly_two(self):
        """The header is SEVERAL lines deep now (the name is stacked, not
        elided), so nothing here may assume the table starts at line 1."""
        text, colors, refusal = _format_compare([], self._two(), "smart")
        self.assertEqual(refusal, "")
        head, _body = _compare_split(text, 12)
        joined = "\n".join(head)
        self.assertIn("[1]", joined)
        self.assertIn("WUR_EM", joined)
        self.assertIn("[4]", joined)
        self.assertIn("0812EM", joined)
        # The ids are on ONE line, and it is the first: _tag_swatch_rows walks
        # lines and consumes one colour per swatch, so ids spread over several
        # header lines would colour the columns in the wrong order.
        self.assertEqual(head[0].count(RESULTS_SWATCH), 2, head[0])
        self.assertTrue(head[0].rstrip().endswith("Δ"))
        self.assertEqual(colors, (0, 3))

    def test_three_traces_get_no_delta_column(self):
        """
        A Δ against 'whichever trace sorted first' is a reference chosen in
        silence, which this tool refuses everywhere else.

        Mutation: compute the delta against records[0] whatever the count.
        """
        blocks = self._two() + [_two_port_block(7, "third")]
        text, _c, refusal = _format_compare([], blocks, "smart")
        self.assertEqual(refusal, "")
        self.assertNotIn("Δ", text)

    def test_fewer_than_two_traces_is_a_refusal_with_a_reason(self):
        """Mutation: return an empty table instead -- the pane goes blank and
        says nothing."""
        _t, _c, refusal = _format_compare([], [_two_port_block()], "smart")
        self.assertIn("at least two traces", refusal)
        self.assertIn("summary", refusal)

    def test_a_group_one_trace_does_not_have_leaves_an_EMPTY_cell(self):
        """
        'this trace has no port called L3' and 'L3 measured 0' are different
        statements, and only one of them is a measurement.

        Mutation: `vals.get(r.id, 0.0)` instead of `vals.get(r.id)` -- the
        empty cell becomes '0 Ω', which reads as a reading, and the delta
        column then invents a change against it.
        """
        text, _c, _r = _format_compare(
            [], [_two_port_block(1, "a"), _three_port_block()], "smart")
        row = next(ln for ln in text.split("\n")
                   if ln.split()[:2] == ["L3", "R"])
        # Two trace columns, and only the second trace has an L3 at all.
        self.assertEqual(row.count("Ω"), 1, f"a value was invented: {row!r}")
        self.assertIn("1.7 Ω", row)

    def test_the_quantities_are_grouped_by_port_then_by_pair(self):
        text, _c, _r = _format_compare([], self._two(), "smart")
        _head, body = _compare_split(text, 12)
        labels = [ln.split()[0] for ln in body if ln.strip()]
        self.assertEqual(labels[0], "VCO")
        self.assertIn("RX", labels)
        self.assertEqual(labels[-4], "VCO")      # the 'VCO x RX' group

    def test_worst_M_over_L_is_carried_and_stays_in_dB(self):
        text, _c, _r = _format_compare([], self._two(), "smart")
        row = next(ln for ln in text.split("\n") if "worst M/L" in ln)
        self.assertIn("-68.77 dB", row)
        self.assertIn("-52.36 dB", row)
        self.assertIn("+16.41 dB", row)

    def test_every_compare_line_fits_the_pane(self):
        text, _c, _r = _format_compare([], self._two(), "smart")
        for line in text.split("\n"):
            self.assertLessEqual(len(line), PANE_COLS, line)


#: Four realistic EM revision names.  [1]/[2] differ only at the HEAD
#: (0731 vs 0812) and [3]/[4] only at the TAIL (open vs short), which is what
#: makes them un-truncatable by any single rule -- see the class below.
REVISIONS = ["VCO_EM_0731_ideal_ground_ref", "VCO_EM_0812_ideal_ground_ref",
             "VCO_EM_0812_RDL_shield_open", "VCO_EM_0812_RDL_shield_short"]


def _revision_blocks(labels):
    return [_two_port_block(i + 1, lab, color_idx=i)
            for i, lab in enumerate(labels)]


def _column_names(records):
    """
    What each compare column says its trace is called, reassembled.

    Goes through `_compare_head_cells` rather than through the rendered text
    because a stacked name is spread down several lines of one column, and
    slicing columns back out of a monospace table by eye is exactly the kind of
    arithmetic that makes a test agree with a bug.
    """
    base = [max(len(f"{RESULTS_SWATCH} [{r.id}]"), 10) for r in records]
    cells, legend, repeats = _compare_head_cells(records, base, 23)
    if legend:
        return [ln.split("] ", 1)[1] for ln in legend], "legend", repeats
    return ["".join(c[1:]) for c in cells], "stacked", repeats


class TestTheTraceNameIsNeverElided(unittest.TestCase):
    """
    The reported complaint, and it was worse than it looked: the 14-character
    head-cut did not merely hide the tail of a name, it made two DIFFERENT
    traces share one heading.

    Measured on REVISIONS, head-cut at 14: [3] and [4] both render as
    'VCO_EM_0812_RD…'.  Two columns of the table whose entire purpose is telling
    those two apart, headed byte-identically.  That is `freeze_label`'s defect
    arriving in the Results pane.

    And no better truncation rule exists, which is why none was chosen -- at the
    15 characters each column gets with five traces, head-cut, tail-cut AND
    middle-elision all collide on this set (the first pair differs only at the
    head, the second only at the tail).  So the name is shown whole, stacked
    down the heading or moved to a legend.
    """

    def test_the_shipped_head_cut_really_did_collide(self):
        """The precondition.  Without it every assertion below could pass on a
        table that never had a problem, and the class would prove nothing."""
        cut = [s[:13] + "…" for s in REVISIONS]
        self.assertEqual(cut[2], cut[3],
                         "the fixture no longer reproduces the defect")
        self.assertEqual(len(set(cut)), 3, cut)

    def test_no_two_columns_are_headed_the_same(self):
        """Mutation: head the column with _trunc_str(r.label, 14) again."""
        for n in range(2, len(REVISIONS) + 1):
            with self.subTest(traces=n):
                names, shape, _r = _column_names(
                    _revision_blocks(REVISIONS[:n]))
                self.assertEqual(len(set(names)), n,
                                 f"{shape}: two columns agree: {names}")

    def test_every_column_carries_its_WHOLE_name(self):
        """
        Whichever shape is chosen, the label must come back character for
        character -- that is the entire point.

        Mutation: truncate in either branch of _compare_head_cells.
        """
        for n in (2, 3, 4, 5, 6, 8, 10):
            labels = [f"VCO_EM_0812_RDL_shield_variant_{i}" for i in range(n)]
            with self.subTest(traces=n):
                names, shape, _r = _column_names(_revision_blocks(labels))
                self.assertEqual(names, labels, shape)

    def test_the_ids_are_all_on_the_FIRST_header_line(self):
        """
        _tag_swatch_rows walks lines and consumes ONE colour per swatch it
        finds, so a swatch on a lower header line would colour the wrong
        column.

        Mutation: bottom-align the whole cell (id included) instead of pinning
        the id to line 0 -- a short name then drags its id down a line.

        THE COLUMNS MUST NEED DIFFERENT DEPTHS or this proves nothing: with two
        traces the share is wide enough for both names to land on one line, the
        padding is empty, and bottom-aligning the whole cell is a NO-OP.  Six
        traces squeeze the share to 10 characters, so the long names wrap to
        four lines while 'x' still takes one -- measured, and asserted below
        before anything else.
        """
        labels = ["x"] + [f"VCO_EM_0812_RDL_shield_variant_{i}"
                          for i in range(5)]
        blocks = _revision_blocks(labels)
        _names, shape, _r = _column_names(blocks)
        self.assertEqual(shape, "stacked")
        text, _c, _r = _format_compare([], blocks, "smart")
        head, _body = _compare_split(text, 12)
        # The precondition: the header really is several lines deep and the
        # short name really does sit on the LAST of them, not the first.
        self.assertGreater(len(head), 2, head)
        self.assertIn("x", head[-1].split())
        self.assertEqual(head[0].count(RESULTS_SWATCH), len(labels), head[0])
        for line in head[1:]:
            self.assertEqual(line.count(RESULTS_SWATCH), 0, line)

    def test_a_swatch_is_emitted_for_every_colour_and_no_more(self):
        """
        The colour tuple is consumed one swatch at a time, so the two counts
        have to agree in BOTH shapes -- the legend shape emits a swatch per
        legend line AND per column, which is what `repeats` is for.

        Mutation: return 1 from the legend branch -- the header's swatches then
        run past the end of the colour list.
        """
        cases = {"stacked": REVISIONS[:3],
                 "legend": ["x" * 50, "y" * 50, "z" * 50]}
        for shape, labels in cases.items():
            with self.subTest(shape):
                blocks = _revision_blocks(labels)
                text, colors, _r = _format_compare([], blocks, "smart")
                _n, got, _rep = _column_names(blocks)
                self.assertEqual(got, shape)
                self.assertEqual(text.count(RESULTS_SWATCH), len(colors),
                                 f"{shape}: swatches != colours")

    def test_a_name_with_no_separator_goes_to_the_LEGEND(self):
        """
        Wrapping it would cut mid-token, and a hard-wrapped name reads as
        corruption rather than as a wrap.

        Mutation: drop the `hard` test in _compare_head_cells -- the six names
        come back sliced into arbitrary 15-character pieces.
        """
        labels = [c * 60 for c in "abcdef"]
        names, shape, _r = _column_names(_revision_blocks(labels))
        self.assertEqual(shape, "legend")
        self.assertEqual(names, labels)

    def test_a_name_too_deep_to_stack_goes_to_the_LEGEND(self):
        """
        Mutation: remove the COMPARE_STACK_LINES_MAX test -- the header grows
        taller than the block of numbers it labels.
        """
        deep = "_".join(f"seg{i}" for i in range(12))     # 12 breakable tokens
        labels = [f"{deep}_{c}" for c in "abcdefghij"]    # 10 traces
        names, shape, _r = _column_names(_revision_blocks(labels))
        self.assertEqual(shape, "legend")
        self.assertEqual(names, labels)

    def test_a_stacked_header_stays_inside_the_line_cap(self):
        """Mutation: raise the depth the stacked branch accepts."""
        for n in (2, 3, 4, 5, 6, 8, 10):
            labels = [f"VCO_EM_0812_RDL_shield_variant_{i}" for i in range(n)]
            blocks = _revision_blocks(labels)
            _names, shape, _r = _column_names(blocks)
            if shape != "stacked":
                continue
            text, _c, _r2 = _format_compare([], blocks, "smart")
            head, _body = _compare_split(text, 12)
            with self.subTest(traces=n):
                # id line + at most COMPARE_STACK_LINES_MAX name lines
                self.assertLessEqual(len(head), COMPARE_STACK_LINES_MAX + 1,
                                     f"{len(head)} header lines")

    def test_showing_the_whole_name_did_not_cost_the_pane_budget(self):
        """
        The names got LONGER and the table got no wider than the pane: the name
        no longer sets the column width on its own, it wraps instead.

        Mutation: spend the whole spare width per column with no cap (`share`
        without the min against the name length is fine, but dropping the wrap
        and letting the column take the full name is not) -- 10 long names then
        run past 144.
        """
        for n in (2, 3, 4, 5, 6, 8, 10):
            labels = [f"VCO_EM_0812_RDL_shield_variant_{i}" for i in range(n)]
            text, _c, _r = _format_compare(
                [], _revision_blocks(labels), "smart")
            for line in text.split("\n"):
                with self.subTest(traces=n):
                    self.assertLessEqual(len(line), RESULTS_PANE_COLS, line)


class TestTheNameWrapper(unittest.TestCase):

    def test_it_breaks_at_a_separator_and_keeps_it_on_the_LEFT(self):
        """
        The separator staying with the segment it ends is what tells the reader
        the break is a wrap and not a character the name lacks.

        Mutation: put the separator at the head of the next segment.
        """
        self.assertEqual(_wrap_name("aaa_bbb_ccc", 8), ["aaa_bbb_", "ccc"])
        self.assertEqual(_wrap_name("a.b-c_d", 4), ["a.b-", "c_d"])

    def test_a_name_that_fits_is_one_line_and_unchanged(self):
        self.assertEqual(_wrap_name("short", 10), ["short"])

    def test_an_unbreakable_token_is_hard_wrapped_not_truncated(self):
        """
        The wrapper never drops characters; refusing the shape is
        _compare_head_cells' job, not this function's.

        Mutation: return [s[:w]] for a token past the budget.
        """
        got = _wrap_name("abcdefghij", 4)
        self.assertEqual("".join(got), "abcdefghij")
        self.assertTrue(all(len(x) <= 4 for x in got), got)

    def test_an_empty_label_keeps_its_place(self):
        """Mutation: return [] -- the column loses a header line and every
        name below it shifts up one."""
        self.assertEqual(_wrap_name("", 8), [""])


class TestTheSummaryLabelColumn(unittest.TestCase):
    """
    The same eliding, one table over.  The 18-character cap bought NOTHING --
    `_render_columns` already sizes that column to its widest cell -- and cost
    the two rows a reader is comparing their identity: measured, both
    '..._RDL_shield_open' and '..._RDL_shield_short' rendered as
    'VCO_EM_0812_RDL_s…'.  The full names cost 10 columns of 144.
    """

    def test_the_old_cap_really_did_collide(self):
        """The precondition, as above."""
        cut = [s[:17] + "…" for s in REVISIONS[2:]]
        self.assertEqual(cut[0], cut[1], "fixture no longer shows the defect")

    def test_both_summary_tables_carry_the_whole_label(self):
        """Mutation: put the 18 back in either _format_summary_self or
        _format_summary_coupling."""
        blocks = _revision_blocks(REVISIONS)
        for name, fn in (("self", lambda: _format_summary_self(
                [], blocks, "smart")),
                ("coupling", lambda: _format_summary_coupling(
                    blocks, "smart"))):
            text, _c = fn()
            for lab in REVISIONS:
                with self.subTest(f"{name}/{lab}"):
                    self.assertIn(lab, text)
            self.assertNotIn("…", text)

    def test_it_still_fits_the_pane(self):
        blocks = _revision_blocks(REVISIONS)
        for text, _c in (_format_summary_self([], blocks, "smart"),
                         _format_summary_coupling(blocks, "smart")):
            for line in text.split("\n"):
                self.assertLessEqual(len(line), RESULTS_PANE_COLS, line)

    def test_a_pathological_label_is_STILL_capped(self):
        """
        SUMMARY_LABEL_MAX is a backstop against a pasted file path arriving as
        a label, not a width budget.

        Mutation: remove the cap entirely -- one label takes the table past the
        pane on its own.
        """
        blocks = _revision_blocks(["p" * 400])
        text, _c = _format_summary_self([], blocks, "smart")
        self.assertIn("…", text)
        for line in text.split("\n"):
            self.assertLessEqual(len(line), RESULTS_PANE_COLS, line)
        self.assertLessEqual(max(len(x) for x in text.split("\n")),
                             SUMMARY_LABEL_MAX + 60)


class TestTheColumnRenderer(unittest.TestCase):

    def test_a_column_is_as_wide_as_its_widest_cell_OR_its_header(self):
        """
        Sizing on the values alone puts a 7-character value under a 5-character
        heading and throws the heading one place off the numbers it names.

        Mutation: drop `len(h)` from the width expression.
        """
        out = _render_columns(["quantity", "x"], ["<", ">"], [["a", "1"]])
        self.assertEqual(out[0].split(), ["quantity", "x"])
        self.assertTrue(out[1].startswith("a       "))

    def test_no_line_carries_trailing_whitespace(self):
        out = _render_columns(["a", "bbbb"], ["<", "<"], [["x", "y"]])
        for line in out:
            self.assertEqual(line, line.rstrip())

    def test_a_STRING_header_is_still_exactly_one_line(self):
        """
        Every caller but compare passes strings, and the two reference-pinned
        renderers are among them.

        Mutation: always render COMPARE_STACK_LINES_MAX header lines -- the
        summary tables grow blank rows and render_reference.json moves.
        """
        out = _render_columns(["a", "b"], ["<", "<"], [["x", "y"], ["z", "w"]])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].split(), ["a", "b"])

    def test_a_multi_line_header_is_rendered_where_the_CALLER_put_it(self):
        """
        The placement is the caller's decision (see _compare_head_cells: the id
        pinned to line 0, the name bottom-aligned), so this function must pad
        depth and change nothing else.

        Mutation: bottom- or top-align the cells here instead of honouring the
        list as given.
        """
        out = _render_columns([["id1", "", "nm"], ["id2", "aa", "bb"]],
                              ["<", "<"], [["v1", "v2"]])
        self.assertEqual(len(out), 4)             # 3 header lines + 1 row
        self.assertEqual(out[0].split(), ["id1", "id2"])
        self.assertEqual(out[1].split(), ["aa"])  # column 0 is blank here
        self.assertEqual(out[2].split(), ["nm", "bb"])
        self.assertEqual(out[3].split(), ["v1", "v2"])

    def test_a_multi_line_header_counts_towards_the_COLUMN_WIDTH(self):
        """
        Every line of it, not just the first -- otherwise a long name segment
        on line 3 is drawn through its neighbour.

        Mutation: measure only heads[i][0].
        """
        out = _render_columns([["a", "wwwwwwww"], ["b", "c"]],
                              ["<", "<"], [["x", "y"]])
        self.assertTrue(out[-1].startswith("x" + " " * 8), repr(out[-1]))


# ============================================================================
# The wiring: the selector, the session, the re-render
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheViewSelector(unittest.TestCase):

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
        self.tc = TraceConfig(
            id=1, file_label=self.fe.label, mode=6, label="coils",
            mports=[MeasPortRow(name="c1", plus="1"),
                    MeasPortRow(name="c2", plus="2")])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _log(self):
        return self.app.results_text.get("1.0", tk.END)

    def test_the_default_is_the_view_the_tool_always_had(self):
        """A new control may not change what an existing user sees on
        startup."""
        self.assertEqual(self.app.results_view_var.get(), VIEW_DETAIL)

    def test_every_view_renders_the_run_without_raising(self):
        self.app._on_calculate()
        self._settle()
        for view in RESULTS_VIEWS:
            with self.subTest(view):
                self.app.results_view_var.set(view)
                self.app._on_results_view_changed()
                self._settle()
                self.assertIn("re-rendered with view=" + view, self._log())

    def test_a_LONG_trace_name_reaches_the_pane_whole_and_stays_coloured(self):
        """
        The join between the formatter and the widget, which no pure test
        reaches: `_tag_swatch_rows` walks the inserted lines and pops ONE
        colour per swatch it finds, so a shape that emits a different number of
        swatches than the formatter declared colours for either mis-colours the
        columns or runs off the end of the list.  The legend shape emits a
        swatch per legend line AND per column, which is the case that would
        break it.

        Mutation: return `tuple(...)` instead of `tuple(...) * repeats` from
        _format_compare -- the legend shape then leaves the header's swatches
        untagged, and the colour a reader maps back to the plot is gone.
        """
        # Two traces leave enough width for a long name to sit on one line, so
        # reaching the LEGEND shape needs both an unbreakable name and enough
        # columns to squeeze the per-column share below it -- with two traces
        # BOTH of these render stacked, and this test then never touches the
        # branch `repeats` exists for.  Measured, hence six.
        cases = (("stacked", ["VCO_EM_0812_RDL_shield_variant_open"], 1),
                 ("legend", ["n" * 60] * 6, 6))
        for shape, labels, n in cases:
            with self.subTest(shape):
                self.tc.label = labels[0]
                dups = _duplicates_for_compare(
                    self.app, self.tc,
                    [f"{labels[0][:40]}_{i}" for i in range(n)])
                self.app.results_view_var.set(VIEW_COMPARE)
                self.app._on_calculate()
                self._settle()
                # Cleared so the two counts below are about THIS render only;
                # the Log accumulates every previous one otherwise.
                self.app.results_text.delete("1.0", tk.END)
                self.app._on_results_view_changed()
                self._settle()
                log = self._log()
                lines = log.split("\n")
                # The precondition: this really is the shape being claimed.
                self.assertEqual(_looks_like_the_legend_shape(lines),
                                 shape == "legend", f"{shape}: wrong shape")
                # The whole name is on screen: contiguous in the legend shape,
                # and as its own wrapped segments in the stacked one.
                for seg in _wrap_name(self.tc.label, 12):
                    self.assertIn(seg, log, f"{shape}: '{seg}' missing")
                # EVERY swatch is tagged, not merely some.  `> 0` would pass
                # the mutation this test exists for: dropping `* repeats`
                # leaves the legend lines tagged and the HEADER bare.
                txt = self.app.results_text
                tagged = sum(len(txt.tag_ranges(f"c{i}")) // 2
                             for i in range(12))
                self.assertEqual(tagged, log.count(RESULTS_SWATCH),
                                 f"{shape}: a swatch went untagged")
                # One per trace column at the very least, or the equality above
                # could be satisfied by a table with no swatches in it at all.
                self.assertGreaterEqual(tagged, 2, f"{shape}: none emitted")
                for d in dups:
                    self.app.traces.remove(d)

    def test_switching_the_view_creates_no_run_tab(self):
        """Choosing a view measures nothing, so it is not a run.  Mutation:
        call _add_run_tab from _on_results_view_changed."""
        self.app._on_calculate()
        self._settle()
        before = len(self.app._run_tabs)
        n_before = self.app._run_counter
        self.app.results_view_var.set(VIEW_SUMMARY)
        self.app._on_results_view_changed()
        self._settle()
        self.assertEqual(len(self.app._run_tabs), before)
        self.assertEqual(self.app._run_counter, n_before)

    def test_EVERY_run_page_is_repainted_not_just_the_newest(self):
        """
        Leaving one page in the previous layout is the 'one screen, two
        formattings, then a silent flip' failure the units switch is written
        from.

        Mutation: repaint only self._newest_run_tab().
        """
        self.app._on_calculate()
        self._settle()
        self.app._on_calculate()
        self._settle()
        self.assertGreaterEqual(len(self.app._run_tabs), 2)
        self.app.results_view_var.set(VIEW_SUMMARY)
        self.app._on_results_view_changed()
        self._settle()
        for rt in self.app._run_tabs:
            page = rt.text.get("1.0", tk.END)
            self.assertIn("self impedance @", page)
            self.assertNotIn("self impedance (diagonal)", page)

    def test_a_view_with_no_run_yet_does_not_raise(self):
        self.app.results_view_var.set(VIEW_COMPARE)
        self.app._on_results_view_changed()
        self._settle()

    def test_compare_falls_back_to_the_summary_and_says_why(self):
        """
        One trace cannot be compared, and the view stays chosen -- so a run
        that cannot be compared must still print its numbers.

        Mutation: return the refusal alone.
        """
        self.app._on_calculate()
        self._settle()
        self.app.results_view_var.set(VIEW_COMPARE)
        self.app._on_results_view_changed()
        self._settle()
        log = self._log()
        self.assertIn("compare: compare needs at least two traces", log)
        self.assertIn("self impedance @", log)

    def _reload(self, mangle=None):
        """Save, WIPE, reload.  The wipe is not tidiness: _load_session_file
        asks for confirmation when there are traces to replace, and an
        unanswered modal hangs the test process rather than failing it."""
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "s.json")
            self.app._write_session(path, d)
            if mangle is not None:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                mangle(data)
                Path(path).write_text(json.dumps(data), encoding="utf-8")
            self.app.files = []
            self.app.traces = []
            self.app._trace_list_shown = []
            self.app._refresh_file_list()
            self.app._refresh_trace_list()
            self.app._refresh_file_combobox()
            self.app.results_view_var.set(VIEW_DETAIL)
            ok = self.app._load_session_file(path, "test")
            self._settle()
            return ok

    def test_the_view_survives_a_session_round_trip(self):
        """Mutation: drop 'results_view' from _CONTROL_KEYS."""
        self.app.results_view_var.set(VIEW_COMPARE)
        self.assertEqual(
            self.app._session_dict(None)["controls"]["results_view"],
            VIEW_COMPARE)
        self.assertTrue(self._reload())
        self.assertEqual(self.app.results_view_var.get(), VIEW_COMPARE)

    def test_a_view_the_build_does_not_know_is_dropped_with_a_note(self):
        """
        Both comboboxes are state='readonly', so a value from outside the list
        would sit there unselectable with no way back except editing the file.

        Mutation: take 'results_view' out of _CONTROL_CHOICES.
        """
        self.app.results_view_var.set(VIEW_SUMMARY)

        def mangle(data):
            data["controls"]["results_view"] = "kaleidoscope"

        self._reload(mangle)
        self.assertIn(self.app.results_view_var.get(), RESULTS_VIEWS)
        self.assertIn("results_view", self._log())


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheResultsHeaderLayout(unittest.TestCase):
    """
    The header was five packed controls asking 667 px against the 575 it gets
    at the 1040x600 minsize at 150% font scaling, and `pack` unmaps from the
    END -- so the Keep button was already the one being squeezed.  A View
    label plus a readonly combobox is a further 127 px at 100% and 240 px at
    150%.  It is a ReflowRow now, which wraps instead.

    Same assertion as tests/test_plot_controls.py, for the same reason: a
    PLACED widget stays mapped while it hangs off the right edge, so
    winfo_ismapped() cannot see this failure.
    """

    def _app(self, scaling: float):
        import tkinter.font as tkfont
        app = App()
        if scaling != 1.0:
            app.tk.call("tk", "scaling", 2.0)
            for name in tkfont.names(app):
                f = tkfont.nametofont(name, app)
                try:
                    size = f.cget("size")
                    f.configure(size=int(round(abs(size) * 1.5))
                                * (1 if size > 0 else -1))
                except Exception:
                    pass
        return app

    def _check(self, scaling, geo):
        app = self._app(scaling)
        try:
            app.geometry(geo)
            app.deiconify()
            for _ in range(4):
                app.update_idletasks()
                app.update()
            strip = app._results_header
            sw, sh = strip.winfo_width(), strip.winfo_height()
            for w in strip.winfo_children():
                self.assertLessEqual(
                    w.winfo_x() + w.winfo_width(), sw,
                    f"{w.winfo_class()} hangs off the right edge at "
                    f"{scaling}x {geo}")
                self.assertLessEqual(
                    w.winfo_y() + w.winfo_height(), sh,
                    f"{w.winfo_class()} hangs off the bottom at "
                    f"{scaling}x {geo}")
        finally:
            app.destroy()

    def test_every_control_is_wholly_inside_the_strip(self):
        for scaling in (1.0, 1.5):
            for geo in ("1500x900", "1040x600"):
                with self.subTest(scaling=scaling, geo=geo):
                    self._check(scaling, geo)

    def test_the_strip_cannot_force_the_pane_wider(self):
        """
        ReflowRow lays out by `place`, and place does not propagate.  That is
        what keeps the strip's 667 px out of the PanedWindow sash.

        Mutation: pack the controls instead.
        """
        app = self._app(1.0)
        try:
            app.geometry("1040x600")
            app.deiconify()
            for _ in range(3):
                app.update_idletasks()
                app.update()
            self.assertLessEqual(app._results_header.winfo_reqwidth(), 2)
        finally:
            app.destroy()

    def test_the_default_window_still_pays_nothing_for_the_new_control(self):
        """One row at 100%, as before.  Mutation: none -- this is the
        measurement that justified adding the control to this strip."""
        app = self._app(1.0)
        try:
            app.geometry("1500x900")
            app.deiconify()
            for _ in range(3):
                app.update_idletasks()
                app.update()
            strip = app._results_header
            tallest = max(w.winfo_height() for w in strip.winfo_children())
            self.assertLessEqual(strip.winfo_height(), tallest + 4,
                                 "the Results header wrapped at 1500x900")
        finally:
            app.destroy()


# ============================================================================
# The Digits control (how many significant digits a value is printed to)
# ============================================================================


class TestTheDigitsControl(unittest.TestCase):
    """
    `Digits:` on the Results header answers "three significant figures is not
    enough to see what changed", which is a real reading problem: two EM
    revisions of one coil both print `2.01 nH` at three digits and differ in
    the fourth.

    TWO PROPERTIES MATTER AND THEY PULL AGAINST EACH OTHER.  `default` must
    render byte-for-byte what the tool has always rendered -- that is what
    `tests/fixtures/render_reference.json` pins, and it is why the control's
    first value is a word rather than the number 3.  And an override must
    widen the COLUMN and not merely the cell: `f"{s:>10}"` does not clip an
    11-character string, it prints all eleven, so a table laid out against a
    written-down width loses its alignment one row at a time.

    Pure -- no display.  Every mutation named below was applied.
    """

    #: Digits that survive rounding.  `%g` strips trailing zeros, so a round
    #: 2.0 nH prints `2 nH` at three digits and at eight alike, and a test
    #: built on one would pass against a control that did nothing at all.
    L_SIX = 2.0123456e-9
    R_SIX = 1.5987654
    C_SIX = -1.2098765e-12

    def _rows(self):
        return [_row(2, "tank", R=self.R_SIX, L=self.L_SIX, C=self.C_SIX,
                     Q=0.8412345),
                _row(3, "tank2", R=self.R_SIX * 1000.0, L=self.L_SIX / 900.0,
                     C=self.C_SIX * 77.0, Q=1.2345678)]

    @staticmethod
    def _data_rows(text):
        return [ln for ln in text.split("\n")
                if ln.lstrip().startswith(RESULTS_SWATCH)]

    # ---- default changes nothing --------------------------------------

    def test_default_renders_exactly_what_no_digits_argument_does(self):
        """
        The control's `default` reaches the renderers as None, and None is the
        argument every existing caller does not pass.  Mutation: make
        `digits_sig` return 3 for `default` -- the aligned table drops a digit
        and this fails.
        """
        rows = self._rows()
        for mode in ("smart", "aligned"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    _format_results_table(rows, mode),
                    _format_results_table(rows, mode, None,
                                          digits_sig("default")))

    def test_the_vocabulary_refuses_anything_it_cannot_render(self):
        """
        `digits_sig` is read live off a Tk variable and out of hand-edited
        session files, so it is total.  Mutation: drop the range test -- `2`
        becomes a one-digit table and `40` a table of noise.
        """
        self.assertEqual(digits_sig("5"), 5)
        self.assertEqual(digits_sig(8), 8)
        for junk in ("default", "", "abc", None, "2", "9", "40", 0, -3, 1.5):
            with self.subTest(junk=junk):
                self.assertIsNone(digits_sig(junk))

    # ---- an override reaches the numbers -------------------------------

    def test_more_digits_show_more_of_the_number(self):
        """Mutation: ignore `sig` in `_fmt_smart` / `_fmt_plain`."""
        rows = self._rows()
        three = _format_results_table(rows, "smart")
        six = _format_results_table(rows, "smart", None, 6)
        self.assertIn("2.01 nH", three)
        self.assertNotIn("2.01235 nH", three)
        self.assertIn("2.01235 nH", six)
        # ... and the dimensionless column with them.
        self.assertIn("0.841", three)
        self.assertIn("0.841234", six)

    def test_the_aligned_mode_follows_too_and_keeps_its_own_prefix(self):
        """
        The SI prefix is picked from the DATA, never from the digit count:
        widening a column must not also rescale it.  Mutation: derive the
        prefix from `sig` -- the header stops saying nH and every value in it
        changes meaning.
        """
        rows = self._rows()
        head_default = [ln for ln in
                        _format_results_table(rows, "aligned").split("\n")
                        if "L[" in ln][0]
        head_eight = [ln for ln in
                      _format_results_table(rows, "aligned", None,
                                            8).split("\n")
                      if "L[" in ln][0]
        self.assertIn("L[nH]", head_default)
        self.assertIn("L[nH]", head_eight)
        self.assertNotEqual(_format_results_table(rows, "aligned"),
                            _format_results_table(rows, "aligned", None, 8))

    # ---- and the column follows the numbers ----------------------------

    def test_the_detail_table_stays_aligned_at_every_digit_count(self):
        """
        THE MUTATION THIS EXISTS FOR: pin NUM_W at its historical 10 / 9
        instead of `max(that, widest cell)`.  Every row then keeps its own
        width and the table shears -- not a crash, not an exception, just a
        report nobody can read down.

        Both rows carry the same Sign flag, so "equal line lengths" is
        exactly "every numeric column is the same width on every row".
        """
        rows = self._rows()
        for mode in ("smart", "aligned"):
            for sig in (None, 3, 5, 8):
                with self.subTest(mode=mode, sig=sig):
                    text = _format_results_table(rows, mode, None, sig)
                    data = self._data_rows(text)
                    self.assertEqual(len(data), 2)
                    self.assertEqual(len(data[0]), len(data[1]),
                                     "the rows sheared:\n" + text)
                    # The header has to move with them, or every column is
                    # labelled with its neighbour's name.
                    header = [ln for ln in text.split("\n")
                              if ln.strip().startswith("ID")][0]
                    flag = data[0].rstrip().split("  ")[-1]
                    self.assertEqual(header.index("Sign"),
                                     len(data[0]) - len(flag),
                                     "the header and the rows disagree:\n"
                                     + text)

    def test_the_coupling_block_stays_aligned_and_its_Z_column_follows(self):
        """
        The same mutation in the second table -- `_format_coupling_block`'s
        NUM_W (11) and Z_W (18).  The Z entries are the numbers R, L and C
        were extracted FROM, so leaving them at four digits while the
        extracted values print eight is two rules on one page.
        """
        # Values carrying more than eight digits.  The shipped fixture's
        # 9.924+112.6j has exactly four, and `%g` cannot show a digit the
        # number does not have -- an assertion built on it would pass against
        # a Z cell that ignored `sig` entirely.  (It did, at first: this test
        # is what said so.)
        block = _two_port_block(zaa=9.9241234567 + 112.61234567j,
                                zbb=4.8312345678 + 49.401234567j)
        for sig in (None, 4, 8):
            with self.subTest(sig=sig):
                text = _format_coupling_block(block, "smart", sig)
                # The two rows UNDER the table header, taken by position:
                # `VCO x RX:` also starts with the first port's name, so any
                # prefix test picks up the pair line and then compares the
                # wrong pair of lines.
                lines = text.split("\n")
                head = [i for i, ln in enumerate(lines)
                        if ln.strip().startswith("Port")][0]
                ports = lines[head + 1:head + 3]
                self.assertTrue(ports[0].startswith("      VCO"), text)
                self.assertTrue(ports[1].startswith("      RX"), text)
                self.assertEqual(len(ports[0]), len(ports[1]),
                                 "the self table sheared:\n" + text)
                # AGAINST THE HEADER, not just row against row.  Both rows
                # here are the same width even when the column is too narrow
                # for them -- 9.9241235 and 4.8312346 are both nine
                # characters -- so they shear together and equal lengths
                # cannot see it.  The header does not shear with them: every
                # cell of it is padded to the declared width, so the last
                # column ends where the rows' last column ends only while
                # that width is honest.  (Measured: with NUM_W pinned at 11
                # and Z_W at 18 the rows-only check passes and this one
                # fails, which is why both are here.)
                self.assertEqual(len(lines[head]), len(ports[0]),
                                 "the header and the rows disagree:\n" + text)
        # The Z column is the reading the digits were asked for.
        self.assertIn("9.924+112.6j", _format_coupling_block(block, "smart"))
        self.assertIn("9.9241235+112.61235j",
                      _format_coupling_block(block, "smart", 8))

    def test_the_Z_matrix_follows_the_digits(self):
        """
        At three measurement ports the matrix is printed instead of the Z
        column, and it is the same number under the same control.  Mutation:
        drop the `cell=` argument at the `_format_z_matrix` call -- the
        matrix stays at four digits while the table under it does not.
        """
        Zs = {"L1": 1.5123456789 + 126.98765432j,
              "L2": 1.6123456789 + 130.98765432j,
              "L3": 1.7123456789 + 140.98765432j}
        block = _block(7, "osc", Zs,
                       [_pair("L1", "L2", 0.01 + 13.0j,
                              Zs["L1"].imag / W, Zs["L2"].imag / W)])
        self.assertIn("1.512+127j", _format_coupling_block(block, "smart"))
        self.assertIn("1.5123457+126.98765j",
                      _format_coupling_block(block, "smart", 8))

    def test_the_other_two_views_follow_the_same_control(self):
        """
        A control that reached `detail` alone would print two precisions on
        one screen the moment the view was switched.  Mutation: drop `sig`
        from any one of the three `_format_summary_*` / `_format_compare`
        call sites.
        """
        blocks = [_two_port_block(1, "a"), _two_port_block(4, "b")]
        rows = self._rows()
        self.assertIn("0.841234",
                      _format_summary_self(rows, [], "smart", 6)[0])
        self.assertNotIn("0.841234",
                         _format_summary_self(rows, [], "smart")[0])
        self.assertNotEqual(_format_summary_coupling(blocks, "smart")[0],
                            _format_summary_coupling(blocks, "smart", 6)[0])
        self.assertNotEqual(_format_compare(rows, [], "smart")[0],
                            _format_compare(rows, [], "smart", 6)[0])
        # The delta is a reading too, and its default is its own.
        self.assertEqual(_delta_cell(1.0, 1.23456789, ""),
                         _delta_cell(1.0, 1.23456789, "", None))
        self.assertNotEqual(_delta_cell(1.0, 1.23456789, ""),
                            _delta_cell(1.0, 1.23456789, "", 7))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheDigitsControlIsWiredUp(unittest.TestCase):
    """
    The join no pure test reaches: the control on the header strip, the run
    pages it repaints, and the plot it tells.

    `_run_report_segments` reads the digits LIVE, exactly as it reads the
    units mode and the view -- how precisely a value is printed is a
    rendering choice, not a recorded fact, so it is not frozen onto a
    RunSnapshot and every page follows the control as it stands now.
    """

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
        self.tc = TraceConfig(
            id=1, file_label=self.fe.label, mode=6, label="coils",
            mports=[MeasPortRow(name="c1", plus="1"),
                    MeasPortRow(name="c2", plus="2")])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _log(self):
        return self.app.results_text.get("1.0", tk.END)

    def _page(self):
        return self.app._run_tabs[0].text.get("1.0", tk.END)

    def test_the_default_is_the_page_the_tool_always_had(self):
        """A new control may not change what an existing user sees on
        startup -- which is also what render_reference.json pins."""
        self.assertEqual(self.app.sig_digits_var.get(), "default")
        self.assertIsNone(self.app.plot.view.sig_digits)

    def test_choosing_digits_repaints_the_log_the_page_and_the_plot(self):
        """
        Mutation: repaint only the newest page, or forget the
        `plot.set_sig_digits` call -- one screen then shows two precisions,
        which is the failure the units switch's own rule is written from.
        """
        self.app._on_calculate()
        self._settle()
        before_page = self._page()
        self.app.sig_digits_var.set("7")
        self.app._on_digits_changed()
        self._settle()
        self.assertIn("re-rendered with digits=7", self._log())
        self.assertNotEqual(self._page(), before_page,
                            "the run page kept the old precision")
        self.assertEqual(self.app.plot.view.sig_digits, 7)
        # ... and choosing a view or a unit afterwards keeps the digits,
        # because all three are read live off the same header.
        self.app.results_view_var.set(VIEW_SUMMARY)
        self.app._on_results_view_changed()
        self._settle()
        self.assertEqual(self.app.plot.view.sig_digits, 7)

    def test_it_creates_no_run(self):
        """Choosing a precision measures nothing, so it is not a run --
        the units switch's rule, and the same code path."""
        self.app._on_calculate()
        self._settle()
        n_tabs, n_runs = len(self.app._run_tabs), self.app._run_counter
        self.app.sig_digits_var.set("5")
        self.app._on_digits_changed()
        self._settle()
        self.assertEqual(len(self.app._run_tabs), n_tabs)
        self.assertEqual(self.app._run_counter, n_runs)

    def test_it_survives_having_nothing_to_repaint(self):
        """The plot is told even when there is no run: `_rerender_every_page`
        returns early with none, and the control must still not raise from a
        Tk callback, where nothing catches it."""
        self.app.sig_digits_var.set("6")
        self.app._on_digits_changed()
        self._settle()
        self.assertEqual(self.app.plot.view.sig_digits, 6)


if __name__ == "__main__":
    unittest.main()
