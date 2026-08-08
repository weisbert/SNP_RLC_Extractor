"""
Four small changes to what the tool SHOWS, none of which touch a number.

  * RANKED COUPLING.  Six measurement ports make 15 pairs and they were
    printed in nested-loop (a, b) order, which says nothing about which of
    them matter.  They are now strongest-first by max(|M/L_a|, |M/L_b|) -- the
    Norton injection ratio, the quantity a spur budget is written against --
    with everything under -60 dB folded into one line that points at the CSV.
    Magnitude is used for the ORDER and the FLOOR and nowhere else: M, C_c and
    k keep their physical sign in every cell, and a NaN ratio sorts last and
    is never folded away.
  * COLOURED TRACE LIST.  A Listbox row takes the colour its curve is drawn
    in, re-applied on every rebuild because itemconfig does not survive
    delete().
  * SWATCHED RESULTS TABLE.  A width-stable one-character swatch at the head
    of each row, coloured with a Text tag (the "flag" tag's precedent), so a
    row can be tied to a curve without opening the editor.
  * FOOTER SUMMARY.  The port-overview and validation strips sit 366 and 387
    px below the fold of a 45 px viewport at the 1040x600 minsize.  A one-line
    summary of both now shares the footer button's row, which is measured at
    +0 px of vertical cost -- the only budget that does not unmap the editor
    canvas.

Pure functions get pure tests; the Tk ones skip cleanly with no display and
read their layout numbers off a MAPPED window.
"""

from __future__ import annotations

import csv
import io
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_gui  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    ConnectionRow,
    CouplingResult,
    MeasPortRow,
    PairCoupling,
    PortRLC,
    build_terminations_rows,
    parse_touchstone,
)
from pkg_rlc_gui import (  # noqa: E402
    COUPLING_FLOOR_DB,
    FOOTER_STRIP_CHARS,
    RESULTS_SWATCH,
    App,
    FileEntry,
    TraceConfig,
    _footer_strip_text,
    _format_coupling_block,
    _format_results_table,
    _write_coupling_csv,
    rank_coupling_pairs,
)
from pkg_rlc_plot import COLORS  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"


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
# A1 -- ranking the coupling list (pure)
# ============================================================================

def _pair(a: str, b: str, mla: float, mlb: float,
          M: float = 1e-12, k: float = 0.01) -> PairCoupling:
    """A PairCoupling with the two injection ratios set explicitly."""
    return PairCoupling(
        name_a=a, name_b=b, Z_ab=complex(0.1, 0.2),
        M_henry=M, C_c_farad=-1e-12, k=k,
        M_over_La=mla, M_over_Lb=mlb,
        M_over_La_dB=float("nan"), M_over_Lb_dB=float("nan"),
    )


def _names(pairs):
    return [f"{p.name_a}x{p.name_b}" for p in pairs]


NAN = float("nan")


class TestCouplingRanking(unittest.TestCase):
    """Pure: the order, the key, the floor and the two things never hidden."""

    def test_ranked_strongest_first_by_the_worst_injection_ratio(self):
        pairs = [_pair("a", "b", 0.01, 0.02),
                 _pair("c", "d", 0.30, 0.10),
                 _pair("e", "f", 0.05, 0.04)]
        shown, hidden = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["cxd", "exf", "axb"])
        self.assertEqual(hidden, [])

    def test_the_key_is_the_ratio_and_not_k(self):
        """
        |k| = 0.02 between two 2 nH coils and between a 2 nH coil and a 500 pH
        one are DIFFERENT problems: M is the same and the injection into the
        small coil is four times larger.  Ranking on |k| cannot tell them
        apart, so both pairs here carry the same k and only the ratios differ.
        """
        weak_k = _pair("big", "big2", 0.02, 0.02, k=0.02)
        strong_k = _pair("big", "small", 0.02, 0.08, k=0.02)
        shown, _ = rank_coupling_pairs([weak_k, strong_k])
        self.assertEqual(_names(shown), ["bigxsmall", "bigxbig2"])

    def test_a_nan_ratio_sorts_last_and_is_never_hidden(self):
        """
        NaN is not a small number: it is a missing measurement (a probe with
        no return path, a port past its SRF), which is the one thing the
        reader most needs to see.  It sorts after every finite pair and never
        goes under the floor.
        """
        pairs = [_pair("u", "v", NAN, NAN),
                 _pair("a", "b", 1e-9, 1e-9),        # ~ -180 dB, folded away
                 _pair("c", "d", 0.5, 0.5)]
        shown, hidden = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["cxd", "uxv"])
        self.assertEqual(_names(hidden), ["axb"])

    def test_one_defined_ratio_is_enough_to_rank_on(self):
        pairs = [_pair("a", "b", 0.01, 0.01),
                 _pair("c", "d", NAN, 0.4)]
        shown, _ = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["cxd", "axb"])

    def test_a_negative_ratio_ranks_by_magnitude(self):
        """Only the ORDER may take an abs(); see the sign test below."""
        pairs = [_pair("a", "b", 0.01, 0.01),
                 _pair("c", "d", -0.5, -0.4)]
        shown, _ = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["cxd", "axb"])

    def test_equal_strength_keeps_the_index_order(self):
        pairs = [_pair("a", "b", 0.1, 0.1),
                 _pair("c", "d", 0.1, 0.1),
                 _pair("e", "f", 0.1, 0.1)]
        shown, _ = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["axb", "cxd", "exf"])

    def test_pairs_below_the_floor_are_split_off(self):
        floor = 10.0 ** (COUPLING_FLOOR_DB / 20.0)      # -60 dB -> 1e-3
        pairs = [_pair("a", "b", 0.5, 0.5),
                 _pair("c", "d", floor * 0.99, 0.0),
                 _pair("e", "f", floor * 1.01, 0.0)]
        shown, hidden = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["axb", "exf"])
        self.assertEqual(_names(hidden), ["cxd"])

    def test_an_exactly_zero_ratio_is_below_the_floor(self):
        """
        _ratio_db maps a zero ratio to NaN, so reading the key off the *_dB
        fields would make the weakest pair there is sort and print as an
        undefined one.  The key is computed linearly for exactly this case.
        """
        pairs = [_pair("a", "b", 0.5, 0.5), _pair("c", "d", 0.0, 0.0)]
        shown, hidden = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["axb"])
        self.assertEqual(_names(hidden), ["cxd"])

    def test_the_strongest_pair_is_never_hidden(self):
        """
        A block whose whole content is "3 pairs were too weak to list" answers
        no question.  "How much coupling is there" has an answer even when the
        answer is "none worth the name".
        """
        pairs = [_pair("a", "b", 1e-6, 1e-6),
                 _pair("c", "d", 1e-5, 1e-5),
                 _pair("e", "f", 1e-7, 1e-7)]
        shown, hidden = rank_coupling_pairs(pairs)
        self.assertEqual(_names(shown), ["cxd"])
        self.assertEqual(_names(hidden), ["axb", "exf"])

    def test_no_floor_hides_nothing(self):
        pairs = [_pair("a", "b", 1e-9, 1e-9), _pair("c", "d", 0.5, 0.5)]
        shown, hidden = rank_coupling_pairs(pairs, floor_db=None)
        self.assertEqual(_names(shown), ["cxd", "axb"])
        self.assertEqual(hidden, [])


def _cres(pairs, names=("L1", "L2", "L3")) -> CouplingResult:
    names = list(names)
    G = len(names)
    Zk = np.eye(G, dtype=complex) * complex(1.5, 1.26)
    ports = [PortRLC(name=n, Z=complex(1.5, 1.26), R_ohm=1.5,
                     L_henry=2e-9, C_farad=-1e-12, Q=0.84) for n in names]
    return CouplingResult(freq_hz=1e8, Z_matrix=Zk, names=names, ports=ports,
                          pairs=list(pairs), reciprocity_error=1e-9)


class _TC:
    id = 1
    label = "osc"

    def port_descriptor(self):
        return "M6: 3 mports"


class TestCouplingBlockRendering(unittest.TestCase):
    """Pure: what the ranked list looks like on the page."""

    def _block(self, pairs, names=("L1", "L2", "L3")):
        return _format_coupling_block(_TC(), "coil.s4p", _cres(pairs, names),
                                      "smart")

    def test_the_db_is_on_the_first_line_beside_M_and_k(self):
        """
        It used to be on a second line, per port, and nowhere on the headline
        -- so scanning fifteen pairs for the loud one meant reading thirty
        lines.  The value is the rank key: max(|M/L_a|, |M/L_b|).
        """
        block = self._block([_pair("L1", "L2", 0.1, 0.05)])
        head = [ln for ln in block.split("\n") if "L1 x L2:" in ln]
        self.assertEqual(len(head), 1, block)
        line = head[0]
        self.assertIn("M = ", line)
        self.assertIn("k = ", line)
        self.assertIn("dB", line)
        self.assertAlmostEqual(
            float(line.split("worst M/L = ")[1].split(" dB")[0]),
            20.0 * math.log10(0.1), places=2)

    def test_the_folded_tail_is_one_line_that_names_the_floor(self):
        pairs = [_pair("L1", "L2", 0.5, 0.5),
                 _pair("L1", "L3", 1e-6, 1e-6),
                 _pair("L2", "L3", 1e-7, 1e-7)]
        block = self._block(pairs)
        self.assertIn("… +2 pairs below -60 dB (see Export CSV)", block)
        self.assertNotIn("L1 x L3:", block)
        self.assertIn("L1 x L2:", block)

    def test_one_folded_pair_is_singular(self):
        block = self._block([_pair("L1", "L2", 0.5, 0.5),
                             _pair("L1", "L3", 1e-6, 1e-6)])
        self.assertIn("… +1 pair below", block)

    def test_no_folded_line_when_nothing_is_folded(self):
        block = self._block([_pair("L1", "L2", 0.5, 0.5)])
        self.assertNotIn("below -60 dB", block)

    def test_the_rows_are_printed_in_rank_order(self):
        pairs = [_pair("L1", "L2", 0.01, 0.01),
                 _pair("L1", "L3", 0.50, 0.10),
                 _pair("L2", "L3", 0.05, 0.05)]
        block = self._block(pairs)
        order = [ln.strip().split(":")[0] for ln in block.split("\n")
                 if " x " in ln and ln.strip().startswith("L")]
        self.assertEqual(order, ["L1 x L3", "L2 x L3", "L1 x L2"])

    def test_M_C_c_and_k_keep_their_sign(self):
        """
        The invariant the ranking must not touch: only the ORDER and the FLOOR
        take a magnitude.  Every printed cell stays signed.
        """
        p = _pair("L1", "L2", -0.4, -0.3, M=-2.1e-10, k=-0.105)
        p.C_c_farad = -5e-13
        block = self._block([p])
        line = [ln for ln in block.split("\n") if "L1 x L2:" in ln][0]
        self.assertIn("M = -210 pH", line)
        self.assertIn("k = -0.105", line)
        self.assertIn("C_c = -500 fF", line)

    def test_an_undefined_pair_still_prints(self):
        pairs = [_pair("L1", "L2", 0.5, 0.5),
                 _pair("L1", "L3", NAN, NAN, M=NAN, k=NAN)]
        block = self._block(pairs)
        self.assertIn("L1 x L3:", block)
        self.assertNotIn("below -60 dB", block)

    def test_a_single_measurement_port_still_says_so(self):
        block = _format_coupling_block(_TC(), "coil.s4p", _cres([], ["L1"]),
                                       "smart")
        self.assertIn("only one measurement port", block)

    def test_the_csv_really_contains_the_folded_pairs(self):
        """
        The '(see Export CSV)' pointer has to be TRUE.  _write_coupling_csv
        enumerates every unordered pair straight off the Z matrix and knows
        nothing about the display floor -- this is what pins that.
        """
        freqs = np.array([1e8, 2e8])
        G = 3
        Zmat = np.zeros((2, G, G), dtype=complex)
        for f in range(2):
            for i in range(G):
                Zmat[f, i, i] = complex(1.5, 1.26)
            Zmat[f, 0, 2] = Zmat[f, 2, 0] = complex(0.0, 1e-9)   # ~ -180 dB
        names = ["L1", "L2", "L3"]
        tc = SimpleNamespace(Zmat=Zmat, mport_names=names)
        fe = SimpleNamespace(ts=SimpleNamespace(freqs=freqs))
        buf = io.StringIO()
        _write_coupling_csv(buf, csv.writer(buf), tc, fe)
        header = [ln for ln in buf.getvalue().splitlines()
                  if ln.startswith("Freq_GHz")][0]
        self.assertIn("M_nH_L1_L3", header)
        self.assertIn("k_L1_L3", header)


# ============================================================================
# A3 -- the results-table swatch (pure half)
# ============================================================================

class _Row:
    def __init__(self, i, label, color_idx):
        self.id, self.label, self.color_idx = i, label, color_idx

    def port_descriptor(self):
        return "M1: 1 -> GND"


class _Res:
    R_ohm, L_henry, C_farad, Q = 1.5, 2e-9, -1.2e-12, 0.84


def _rows(n=2):
    return [(_Row(i + 1, f"t{i + 1}", i), "f.s4p", _Res()) for i in range(n)]


class TestResultsTableSwatch(unittest.TestCase):
    def test_every_data_row_starts_with_the_swatch(self):
        text = _format_results_table(_rows(3), "smart")
        data = [ln for ln in text.split("\n") if "M1: 1 -> GND" in ln]
        self.assertEqual(len(data), 3)
        for ln in data:
            self.assertTrue(ln.startswith(RESULTS_SWATCH), repr(ln))

    def test_no_other_line_starts_with_the_swatch(self):
        """
        _append_swatched finds the rows by that prefix and consumes the colour
        list in order, so a header line wearing one would shift every swatch
        by one trace.
        """
        for units in ("smart", "aligned"):
            for n in (1, 3):
                text = _format_results_table(_rows(n), units)
                lines = text.split("\n")
                swatched = [ln for ln in lines
                            if ln.startswith(RESULTS_SWATCH)]
                self.assertEqual(len(swatched), n, f"{units}/{n}: {text}")

    def test_the_swatch_column_does_not_shift_the_table(self):
        """
        The header and the legend carry a same-width run of spaces, so the
        columns under the header line up with it.
        """
        text = _format_results_table(_rows(2), "smart")
        lines = text.split("\n")
        header = [ln for ln in lines if "Label" in ln][0]
        data = [ln for ln in lines if ln.startswith(RESULTS_SWATCH)][0]
        self.assertEqual(header.index("Label"), data.index("t1"))
        self.assertEqual(len(RESULTS_SWATCH), 1)

    def test_an_empty_table_is_still_empty(self):
        self.assertEqual(_format_results_table([], "smart"), "")


# ============================================================================
# A4 -- the footer summary line (pure half)
# ============================================================================

def _term(nports=8):
    return build_terminations_rows(
        [MeasPortRow("tank", "1", "2")],
        [ConnectionRow(kind="ground", ports="3,4"),
         ConnectionRow(kind="rlc_gnd", ports="5", R="50")],
        nports=nports)


class TestFooterStripText(unittest.TestCase):
    def test_clean_spec_reads_ok(self):
        got = _footer_strip_text(_term(), 8, [])
        self.assertEqual(
            got, "Ports (8): 2 probe · 2 gnd · 1 elem · 3 open  ✓ ok")

    def test_problems_are_counted_not_quoted(self):
        self.assertTrue(
            _footer_strip_text(_term(), 8, ["⚠ a", "⚠ b"]).endswith(
                "⚠ 2 problems"))
        self.assertTrue(
            _footer_strip_text(_term(), 8, ["⚠ a"]).endswith("⚠ 1 problem"))

    def test_the_tick_messages_are_not_problems(self):
        """
        _validation_messages never returns an empty list -- a clean spec comes
        back as the '✓ port 5 → GND: 5 mΩ' echoes, or '✓ no problems found'.
        Counting the list length reports a clean two-element spec as "2
        problems", which is the one false alarm this line must never raise.
        """
        self.assertTrue(
            _footer_strip_text(_term(), 8, ["✓ no problems found"])
            .endswith("✓ ok"))
        self.assertTrue(
            _footer_strip_text(_term(), 8,
                               ["✓ port 3 → GND: 5 Ω", "✓ port 4 → GND: 5 Ω"])
            .endswith("✓ ok"))
        self.assertTrue(
            _footer_strip_text(_term(), 8, ["✓ port 3 → GND: 5 Ω", "⚠ bad"])
            .endswith("⚠ 1 problem"))

    def test_it_is_always_one_line(self):
        """
        The measured ceiling: a second line in the footer costs 9 px, a third
        26 and a fourth 43 -- and 43 px unmaps the editor canvas entirely at
        the 1040x600 minsize.
        """
        for msgs in ([], ["⚠ a"], ["⚠ a"] * 9):
            got = _footer_strip_text(_term(), 8, msgs)
            self.assertNotIn("\n", got)
            self.assertLessEqual(len(got), FOOTER_STRIP_CHARS)

    def test_the_verdict_survives_a_long_overview(self):
        """The ports half gives up characters first: half a tick means nothing."""
        term = build_terminations_rows(
            [MeasPortRow("tank", "1", "2"), MeasPortRow("t2", "3", "4"),
             MeasPortRow("t3", "5", "6")],
            [ConnectionRow(kind="ground", ports="7:1:60"),
             ConnectionRow(kind="rlc_gnd", ports="61", R="50"),
             ConnectionRow(kind="short", ports="62", to="63")],
            nports=153)
        got = _footer_strip_text(term, 153, ["⚠ a", "⚠ b", "⚠ c"])
        self.assertTrue(got.endswith("⚠ 3 problems"), got)
        self.assertLessEqual(len(got), FOOTER_STRIP_CHARS)
        self.assertTrue(got.startswith("Ports (153):"), got)

    def test_it_cannot_tick_a_spec_that_did_not_parse(self):
        """A green tick has to mean 'Calculate will work'."""
        self.assertNotIn("✓", _footer_strip_text(None, 8, []))


# ============================================================================
# Tk tier
# ============================================================================

class _AppCase(unittest.TestCase):
    """An App with one file and two traces, selected on the first."""

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
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                              label="coil_a", port_a="1", color_idx=0)
        self.tc2 = TraceConfig(id=2, file_label=self.fe.label, mode=1,
                               label="coil_b", port_a="2", color_idx=3)
        self.app.traces.extend([self.tc, self.tc2])
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTraceListColour(_AppCase):
    """A2: the Listbox row wears the colour its curve is drawn in."""

    def _fg(self, i):
        return str(self.app.traces_lb.itemcget(i, "foreground"))

    def test_each_row_takes_its_curve_colour(self):
        self.assertEqual(self._fg(0), COLORS[0])
        self.assertEqual(self._fg(1), COLORS[3])

    def test_a_hidden_trace_stays_grey(self):
        """Grey is the state, not the style: it has no curve to be tied to."""
        self.tc2.enabled = False
        self.app._refresh_trace_list()
        self.assertEqual(self._fg(1), "#909090")
        self.assertEqual(self._fg(0), COLORS[0])

    def test_the_colour_survives_a_rebuild(self):
        """itemconfig does not survive delete(), which is why it is re-applied."""
        self.tc.label = "renamed"
        self.app._refresh_trace_list()
        self.assertIn("renamed", self.app.traces_lb.get(0))
        self.assertEqual(self._fg(0), COLORS[0])

    def test_a_colour_change_alone_repaints_the_list(self):
        """
        info_str() carries no colour, so the "unchanged lines -> return early"
        optimisation would otherwise leave the old foreground on screen with
        the plot already redrawn in the new one.
        """
        self.tc2.color_idx = 7
        self.app._refresh_trace_list()
        self.assertEqual(self._fg(1), COLORS[7])

    def test_identical_lines_still_return_early(self):
        """
        The optimisation itself: _refresh_trace_list runs on every keystroke
        and rebuilding a Listbox resets yview, which would yank a user editing
        trace 9 of 12 back to the top on every character.
        """
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app._refresh_trace_list()
        self.assertEqual(self.app.traces_lb.curselection(), ())
        self.app.traces_lb.selection_set(1)
        self.app._refresh_trace_list()      # no change -> no rebuild
        self.assertEqual(self.app.traces_lb.curselection(), (1,))

    def test_the_palette_wraps(self):
        self.tc.color_idx = len(COLORS) + 2
        self.app._refresh_trace_list()
        self.assertEqual(self._fg(0), COLORS[2])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestResultsSwatchTagging(_AppCase):
    """A3: the swatch is tagged with the trace's palette slot."""

    def _tags_of_swatches(self):
        text = self.app.results_text
        out = []
        n = int(text.index("end-1c").split(".")[0])
        for ln in range(1, n + 1):
            if text.get(f"{ln}.0", f"{ln}.1") != RESULTS_SWATCH:
                continue
            out.append([t for t in text.tag_names(f"{ln}.0")
                        if t.startswith("c")])
        return out

    def test_each_row_is_tagged_with_its_colour(self):
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self._tags_of_swatches(), [["c0"], ["c3"]])

    def test_the_tag_really_carries_the_palette_colour(self):
        self.assertEqual(str(self.app.results_text.tag_cget("c3", "foreground")),
                         COLORS[3])

    def test_a_hidden_trace_takes_its_swatch_with_it(self):
        """
        The FIRST trace is the one hidden, on purpose: with the second hidden
        instead, a swatch list built from the unfiltered rows would produce the
        same c0 and the test could not fail.  (Through _on_toggle_trace, not by
        poking tc.enabled -- the editor owns the selected trace and Calculate's
        pre-sync would write the checkbox straight back over it.)
        """
        self.app._on_toggle_trace()         # trace 0 is selected
        self._settle()
        self.assertFalse(self.tc.enabled)
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self._tags_of_swatches(), [["c3"]])

    def test_the_text_still_reads_the_way_the_other_tests_expect(self):
        """
        Six existing tests mark END, calculate, and assert on get(mark, END).
        The swatch is a prefix, so those substrings are untouched.
        """
        mark = self.app.results_text.index(tk.END)
        self.app._on_calculate()
        self._settle()
        body = self.app.results_text.get(mark, tk.END)
        self.assertIn(f"[{self.tc.id:>2}] {self.tc.label}", body)
        self.assertIn(f"[{self.tc2.id:>2}] {self.tc2.label}", body)

    def test_swatch_is_width_stable_in_the_results_font(self):
        """
        Measured with tkinter.font in the pane's own font, the way the ☑/☐
        prefix was measured for the trace list.  '█' and ' ' are both exactly
        one monospace cell, so the header and legend line up with the rows.
        Rejected on the same measurement: '▇' (12 px) and '▰' (10 px).
        """
        import tkinter.font as tkfont
        f = tkfont.Font(self.app,
                        font=self.app.results_text.cget("font"))
        self.assertEqual(f.measure(RESULTS_SWATCH), f.measure(" "))
        self.assertEqual(f.measure(RESULTS_SWATCH), f.measure("0"))
        self.assertNotEqual(f.measure("▇"), f.measure(" "))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestFooterStripLayout(_AppCase):
    """
    A4, measured off a MAPPED window at the 1040x600 minsize.

    The premise: both detail strips are hundreds of pixels below the fold of a
    45 px viewport.  The constraint: the footer summary must cost NOTHING,
    because a footer four lines deep unmaps the editor canvas outright.
    """

    def setUp(self):
        super().setUp()
        self.tc.mode = 5
        self.tc.mports = [MeasPortRow("tank", "1", "2")]
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="3")]
        # Re-select so the editor loads the spec just written onto the trace.
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app.deiconify()
        self._settle()

    def _mode(self, mode, geom="1040x600"):
        self.app.geometry(geom)
        self.app.ed_mode_var.set(mode)
        self.app._on_mode_changed()
        self._settle()

    def _footer(self):
        return self.app.ed_footer_strip

    def _button(self):
        for w in self.app._ed_foot.winfo_children():
            if w.winfo_class() == "TButton":
                return w
        self.fail("the editor footer lost its button")

    def test_the_strip_and_the_button_are_both_on_screen_in_mode_5(self):
        self._mode(5)
        self.assertEqual(self._footer().winfo_ismapped(), 1,
                         "the footer summary is not on screen")
        self.assertEqual(self._button().winfo_ismapped(), 1,
                         "Calculate This Trace is not on screen")

    def test_the_editor_canvas_survives_in_every_mode(self):
        """
        The measured failure this replaces: two strips moved into the footer
        verbatim render up to four lines, which is +43 px, at which point the
        editor canvas reports ismapped() == 0 in modes 1/2/3/6.
        """
        for mode in (1, 2, 3, 5, 6):
            with self.subTest(mode=mode):
                self._mode(mode)
                self.assertEqual(self.app._ed_canvas.winfo_ismapped(), 1,
                                 f"mode {mode}: the editor form disappeared")
                self.assertGreater(self.app._ed_canvas.winfo_height(), 0)
                self.assertEqual(self._button().winfo_ismapped(), 1)

    def test_the_summary_costs_no_vertical_space(self):
        """
        It SHARES the button's 33 px row.  Anything above the button's own
        requested height means a second line has appeared, which is 9 px off
        the editor viewport and the first step towards losing it.
        """
        self._mode(5)
        foot = self.app._ed_foot
        self.assertLessEqual(foot.winfo_reqheight(),
                             self._button().winfo_reqheight() + 6,
                             "the footer grew a second row")
        self.assertLessEqual(self._footer().winfo_reqheight(), 24,
                             "the footer strip is more than one line tall")

    def test_it_says_the_same_thing_the_strips_below_the_fold_do(self):
        self._mode(5)
        self.app._apply_editor_strips()
        self._settle()
        text = str(self._footer().cget("text"))
        self.assertTrue(text.startswith("Ports ("), text)
        overview = str(self.app.ed_overview.cget("text"))
        self.assertEqual(text.split(":")[0], overview.split(":")[0])
        # 1 probe from the mport row, 1 ground, and the spec is complete.
        self.assertIn("probe", text)
        self.assertIn("✓ ok", text)

    def test_it_is_hidden_outside_mode_5(self):
        """
        Outside mode 5 the connections table is hidden but its rows still
        exist, so an overview built from them would count rows the running
        spec does not use.  The footer is never left empty -- the button is
        always its first slave.
        """
        for mode in (1, 2, 3, 6):
            with self.subTest(mode=mode):
                self._mode(mode)
                self.assertEqual(self._footer().winfo_ismapped(), 0)
                self.assertEqual(self._button().winfo_ismapped(), 1)
        self._mode(5)
        self.assertEqual(self._footer().winfo_ismapped(), 1)

    def test_the_detail_strips_really_are_below_the_fold(self):
        """
        The premise of this change, asserted rather than assumed.  Note that
        winfo_ismapped() is NOT the test -- it reads 1 for a widget parked
        hundreds of pixels past the bottom of the canvas.
        """
        self._mode(5)
        canvas = self.app._ed_canvas
        top = canvas.canvasy(0)
        bottom = top + canvas.winfo_height()
        for name in ("ed_overview", "ed_validation"):
            y = getattr(self.app, name).winfo_y()
            self.assertGreater(
                y, bottom,
                f"{name} is on screen at the minsize; the footer summary "
                "would be redundant")

    def test_the_strip_never_shows_a_wrapped_second_line(self):
        """wraplength 0 -- clipping costs 0 px, wrapping costs 26."""
        self._mode(5)
        self.assertEqual(int(self._footer().cget("wraplength")), 0)
        self.app.ed_footer_strip.configure(text="x" * 400)
        self._settle()
        self.assertLessEqual(self._footer().winfo_height(), 24)
        self.assertEqual(self.app._ed_canvas.winfo_ismapped(), 1)


if __name__ == "__main__":
    unittest.main()
