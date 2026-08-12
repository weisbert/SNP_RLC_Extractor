"""
Two files, ONE measurement: the ENGINE half of round 3.

`tests/test_multifile_session.py` is the guard on the schema (which files a
trace names, and that naming several moves nothing about naming one).  This
file is the guard on what Calculate then DOES with them, and on the surfaces
that have to say so:

  * THE NAMESPACE.  A bare port number means the HOME file, in every mode --
    which is what keeps every pre-existing spec, golden case and saved session
    meaning what it meant (R3-2).  A tagged one (`F2.13`) names the file it
    says.  A bare one PAST the home file's port count is REFUSED rather than
    quietly addressing the next file's ports, which is the same shape of
    silent wrong answer as the weld.
  * ONE NAMESPACE, TWO BUILDERS.  The strips and the Ports & Roles window can
    afford `_namespace_network` (a list comprehension) and cannot afford
    `comp.compose` (measured at 10.5 s for 16 + 153 ports at 401 points, on a
    path that runs once per keystroke).  So there are two builders of "what
    does F2.13 mean", and they are pinned against each other here.
  * THE COMPOSED AXIS.  A composed Z lives on the intersection of the spans,
    resampled onto the finer grid -- an axis NEITHER file has.  The plot, the
    CSV and the marker snap all have to use it; the home file's would put the
    right values at the wrong frequencies, with nothing on screen to say so.
  * R3-5, WHERE THE NUMBER IS READ.  The reference-node verdict reaches the
    Log and every run page, frozen onto the snapshot.
  * R2-8 IN THE WINDOW.  An attribution of a composition decomposes against a
    baseline that has the cross-file links IN it.  Without that gauge the
    files are disconnected islands, every package element contributes exactly
    0, and the reconciliation reports perfect health.

Every guard here was mutation-checked.
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc_compose as comp  # noqa: E402
import pkg_rlc_files_gui as files_gui  # noqa: E402
import pkg_rlc_gui as G  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    parse_touchstone,
)
from pkg_rlc_gui import (  # noqa: E402
    App,
    ComposeSpecError,
    FileEntry,
    TraceConfig,
    _namespace_network,
    _plot_trace_label,
    _scope_conn_rows,
    _scope_dsl_text,
    _scope_mport_rows,
    _scope_port_field,
    _trace_plot_freqs,
)
from pkg_rlc_plot import MAX_LABEL_LEN  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
#: 4 ports, 401 points, 1 MHz - 10 GHz.
DIE = FIX / "diff_pair_4port.s4p"
#: 2 ports, 100 points, 100 MHz - 10 GHz.  A DIFFERENT GRID on purpose -- see
#: the note on FIXTURE2 in tests/test_multifile_session.py: with two files on
#: one grid the composed axis IS the home file's, and "these numbers are on the
#: composed axis" becomes untestable because the two answers are one array.
PKG = FIX / "coupled_2port_gndref.s2p"


def _ensure_fixtures():
    if not DIE.exists() or not PKG.exists():
        import generate_test_snp
        generate_test_snp.main()


def _tk_ok() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


TK_OK = _tk_ok()


class _Ts:
    """The three attributes `_namespace_network` reads off a FileEntry.ts."""

    def __init__(self, n, names=None, z0=50.0):
        self.nports = n
        self.port_names = names if names is not None else [""] * n
        self.z0 = z0


class _Fe:
    def __init__(self, label, n, names=None):
        self.label = label
        self.ts = _Ts(n, names)


def _net(*sizes):
    return _namespace_network([_Fe(f"f{i}.s{n}p", n)
                               for i, n in enumerate(sizes)])


# ============================================================================
# The namespace (pure)
# ============================================================================

class TestScopePortField(unittest.TestCase):

    def setUp(self):
        self.net = _net(4, 2)

    def test_a_bare_number_is_the_home_file_and_comes_back_unchanged(self):
        """
        The whole of R3-2 on the read side, and the reason default scope is
        FREE rather than a translation layer: the home file is block 0 at
        offset 0 with every port kept, so its local numbering IS the global
        one.  Every pre-existing spec keeps its meaning byte for byte.
        """
        for spec in ("1", "2", "1,3", "2-4"):
            with self.subTest(spec=spec):
                self.assertEqual(
                    G.parse_port_range(_scope_port_field(spec, self.net, "F1")),
                    G.parse_port_range(spec))

    def test_a_tagged_field_reaches_the_second_file(self):
        self.assertEqual(_scope_port_field("F2.1", self.net, "F1"), "5")
        self.assertEqual(_scope_port_field("F2.1-2", self.net, "F1"), "5-6")

    def test_a_bare_number_past_the_home_file_is_REFUSED(self):
        """
        NOT reassigned to the second file.  '5' on a 4-port home would
        otherwise be F2.1 -- a plausible number from a port the user never
        named, which is the silent wrong answer this namespace exists for.
        """
        with self.assertRaises(ComposeSpecError) as cm:
            _scope_port_field("5", self.net, "F1")
        self.assertIn("F1.5 does not exist", str(cm.exception))

    def test_a_comma_token_may_carry_its_own_tag(self):
        """
        The single-cell SHORT group forces this: a short row stores its whole
        tied group in one cell (`_join_short_group`), so '2,F2.1' -- tie die
        port 2 to package port 1 -- has no other spelling in the connection
        table.  The tag is PER-TOKEN in `parse_scoped_ports` itself, so this
        needs no rule of its own.
        """
        self.assertEqual(_scope_port_field("2,F2.1", self.net, "F1"), "2,5")
        self.assertEqual(_scope_port_field("F1.1,F2.2", self.net, "F1"), "1,6")

    def test_a_BARE_token_after_a_tag_is_still_the_HOME_file(self):
        """
        The tag is NOT sticky, and this is the case it was changed for.

        MUTATION: carry the last tag forward as the scope of the tokens after
        it -- the rule this function used to hold -- and 'F2.1,2' becomes
        package ports 1 and 2, i.e. '5-6'.

        A short group is stored in ONE cell, so the user's own spelling of "tie
        die 25 and 26 to package 15" is a three-token field; write it in the
        other order and a sticky tag silently re-points the two bare tokens at
        the PACKAGE.  It does not raise -- it only needs the package to HAVE
        those ports -- and it contradicts the rule stated in the Help, the
        README and this module's own header: a bare number is a port of the
        HOME file, in every mode.  Both orders must name one network.
        """
        self.assertEqual(_scope_port_field("F2.1,2", self.net, "F1"), "2,5")
        self.assertEqual(_scope_port_field("2,F2.1", self.net, "F1"), "2,5")
        # ... and the two spellings are the SAME set, not merely both legal.
        self.assertEqual(
            comp.parse_scoped_ports("F2.1,2", self.net, default="F1"),
            list(reversed(
                comp.parse_scoped_ports("2,F2.1", self.net, default="F1"))))

    def test_it_agrees_with_parse_scoped_ports_on_all_that_one_accepts(self):
        """
        It is now ONE call to that function, so this pins that it stayed one:
        a second copy of the scope rule here is exactly the drift that put
        'F2.1,2' and the CLI's reading of it at odds.
        """
        for spec in ("1", "2-4", "1,3", "F2.1", "F2.1,2", "F2.1-2",
                     "F1.2:1:4", "2,F2.1", "F1.1,F2.2", "F2.2,1,F1.3"):
            with self.subTest(spec=spec):
                self.assertEqual(
                    G.parse_port_range(
                        _scope_port_field(spec, self.net, "F1")),
                    sorted(comp.parse_scoped_ports(spec, self.net,
                                                   default="F1")))

    def test_a_node_NAME_passes_through_untouched(self):
        """A port field may name a merged node, and a name is not a port."""
        self.assertEqual(_scope_port_field("tap", self.net, "F1", {"tap"}),
                         "tap")

    def test_an_empty_field_stays_empty(self):
        self.assertEqual(_scope_port_field("", self.net, "F1"), "")
        self.assertEqual(_scope_port_field("   ", self.net, "F1"), "")

    def test_an_unknown_tag_is_refused_by_name(self):
        with self.assertRaises(ComposeSpecError) as cm:
            _scope_port_field("PKG.1", self.net, "F1")
        self.assertIn("PKG", str(cm.exception))


class TestScopeDslText(unittest.TestCase):

    def setUp(self):
        self.net = _net(4, 2)

    def scope(self, text):
        return _scope_dsl_text(text, self.net, "F1")

    def test_the_leading_port_field_is_scoped(self):
        self.assertEqual(self.scope("F2.2 ground"), "6 ground")

    def test_the_PARTNER_field_of_short_to_and_lumped_between_is_scoped(self):
        self.assertEqual(self.scope("1 short_to F2.1"), "1 short_to 5")
        self.assertEqual(self.scope("1 lumped_between F2.1 L=10f"),
                         "1 lumped_between 5 L=10f")

    def test_a_dotted_RLC_VALUE_is_not_mistaken_for_a_tagged_port(self):
        """
        The reason this rewrites FIELD POSITIONS and not every token that
        happens to contain a '.'.  `_split_tag('C=1.5p')` reads the head as
        'C=1', which fails the alias pattern -- but a signal group named
        'F1.something' would not, and a rewrite that depends on a group name is
        a silent re-pointing.
        """
        self.assertEqual(self.scope("1 lumped_to_gnd C=1.5p R=0.5"),
                         "1 lumped_to_gnd C=1.5p R=0.5")

    def test_a_signal_group_is_never_touched(self):
        self.assertEqual(self.scope("F2.1 signal F1.odd -"),
                         "5 signal F1.odd -")

    def test_a_node_name_survives_on_both_sides(self):
        text = "1,2 short as tap\ntap lumped_between F2.1 L=10f"
        self.assertEqual(self.scope(text),
                         "1-2 short as tap\ntap lumped_between 5 L=10f")

    def test_comments_indent_and_blank_lines_are_preserved(self):
        """
        As close to identity as a field rewrite allows.  `extra_lines` is shown
        verbatim in "Edit as text…", and a block that reflowed itself every
        time it was scoped would look like the tool had edited the user's text.
        """
        text = "# a note\n\n  F2.1 ground   # why\n"
        self.assertEqual(self.scope(text),
                         "# a note\n\n  5 ground   # why\n")

    def test_an_untagged_block_keeps_every_line_and_every_meaning(self):
        """
        Only a COMPOSED trace is ever scoped, so this is not a claim about
        single-file text -- that never reaches here.  What it pins is that a
        block with no tags in it comes back as the SAME SPEC: the ports are
        renormalised by `collapse_ports` ('2,3' -> '2-3', which is the same
        two ports), and nothing else on the line is touched.
        """
        text = "1 signal A\n2,3 ground\n4 lumped_to_gnd R=50   # term\n"
        out = self.scope(text)
        self.assertEqual(out.splitlines()[0], "1 signal A")
        self.assertEqual(out.splitlines()[2],
                         "4 lumped_to_gnd R=50   # term")
        # Compared by TYPE per port, not by object: `y_series_rlc` returns a
        # fresh closure per call, so two identical LumpedToGnd stamps never
        # compare equal.
        def kinds(t):
            return {p: type(v).__name__ for p, v in t.per_port.items()}
        self.assertEqual(kinds(G.build_terminations_rows(extra_lines=out)),
                         kinds(G.build_terminations_rows(extra_lines=text)))

    def test_a_bare_port_past_the_home_file_raises_here_too(self):
        with self.assertRaises(ComposeSpecError):
            self.scope("5 ground")


class TestScopeRows(unittest.TestCase):

    def setUp(self):
        self.net = _net(4, 2)

    def test_connection_rows_are_COPIED_not_edited(self):
        rows = [ConnectionRow(kind="short", ports="1", to="F2.1")]
        out = _scope_conn_rows(rows, self.net, "F1")
        self.assertEqual((out[0].ports, out[0].to), ("1", "5"))
        self.assertEqual((rows[0].ports, rows[0].to), ("1", "F2.1"),
                         "the caller's rows were mutated")

    def test_measurement_port_rows_are_COPIED_not_edited(self):
        rows = [MeasPortRow(name="die", plus="F2.1", minus="2")]
        out = _scope_mport_rows(rows, self.net, "F1")
        self.assertEqual((out[0].plus, out[0].minus), ("5", "2"))
        self.assertEqual(rows[0].plus, "F2.1")

    def test_a_short_row_s_node_name_is_not_scoped_as_a_port(self):
        rows = [ConnectionRow(kind="short", ports="1,2", net="tap"),
                ConnectionRow(kind="rlc_between", ports="tap", to="F2.1",
                              L="10f")]
        out = _scope_conn_rows(rows, self.net, "F1")
        self.assertEqual((out[1].ports, out[1].to), ("tap", "5"))


class TestTheScopeEchoSaysWhatAFieldRESOLVEDTo(unittest.TestCase):
    """
    The other half of the per-token change: what a field means is now always
    literal, and the strip SAYS what it worked out.

    The echo exists because the reading of a mixed field is the one thing in
    this namespace that a user cannot verify from the screen: the port cell is
    seven characters wide, the validation echo under it prints GLOBAL indices
    ("port 42,110"), and neither answers "which file is that".  It is built
    from the resolver the solve itself uses and rendered by the compose
    module's own `describe_ports`, so an echo that disagrees with the computed
    network is not expressible.
    """

    def setUp(self):
        self.net = _net(70, 100)

    def echo(self, mp, conn, extra=""):
        return G.scope_echo_messages(mp, conn, extra, self.net, "F1")

    def test_a_TAGGED_field_is_echoed_with_the_file_of_every_port(self):
        got = self.echo([], [ConnectionRow(kind="short",
                                           ports="25,26,F2.15")])
        self.assertEqual(len(got), 1)
        text, anchor = got[0]
        self.assertEqual(anchor, ("conn", 0))
        self.assertIn("25,26,F2.15 = F1.25-26, F2.15", text)
        self.assertTrue(text.startswith("✓"),
                        "an echo is not a problem, and _footer_strip_text "
                        "counts the messages that do NOT start with a tick")

    def test_the_ONE_spelling_that_still_looks_like_something_else(self):
        """
        `F2.40,42` is package 40 and HOME 42 -- the tag scopes its own token.
        That is the reading the sticky rule got wrong in the other direction,
        and it is the reason this echo is worth two lines of strip: it is the
        only field here whose correct reading is not obvious from looking at
        it.

        MUTATION: drop the echo and nothing on screen distinguishes this from
        two package ports.
        """
        got = self.echo([], [ConnectionRow(kind="rlc_gnd", ports="F2.40,42",
                                           R="0.5")])
        self.assertIn("F2.40,42 = F2.40, F1.42", got[0][0])

    def test_a_BARE_field_is_NOT_echoed(self):
        """
        A bare field is the home file by a rule with no exception left in it,
        so echoing `25 = F1.25` on every row would spend the strip's two lines
        saying nothing.  Only a field that TAGS a file is echoed.
        """
        self.assertEqual(self.echo([MeasPortRow("vic", "1", "2")],
                                   [ConnectionRow(kind="ground",
                                                  ports="30,31")]), [])

    def test_both_probe_sides_and_a_To_cell_are_echoed(self):
        got = dict(self.echo(
            [MeasPortRow("vic", "F2.1", "2"), MeasPortRow("agg", "3", "F2.2")],
            [ConnectionRow(kind="rlc_between", ports="3", to="F2.7", L="1n")]))
        joined = " | ".join(got)
        self.assertIn("measurement port row 1 '+': F2.1 = F2.1", joined)
        self.assertIn("measurement port row 2 '−': F2.2 = F2.2", joined)
        self.assertIn("connection row 1 To: F2.7 = F2.7", joined)

    def test_a_node_NAME_is_not_echoed_as_a_port(self):
        rows = [ConnectionRow(kind="short", ports="1,2", net="F2.tap")]
        self.assertEqual(self.echo([], rows), [])

    def test_it_NEVER_RAISES_and_a_broken_field_gets_NO_echo(self):
        """
        It runs from a Tk variable trace on every keystroke, where a raised
        exception reaches no handler we control.  A half-typed or impossible
        field gets no echo at all rather than a second message about it: the
        ordinary validation already reports it, and a green tick beside a
        refusal is worse than one message.
        """
        for spec in ("F2.", "F2.9999", "NOPE.1", "F2.5:", "F2.-"):
            with self.subTest(spec=spec):
                rows = [ConnectionRow(kind="ground", ports=spec)]
                self.assertEqual(self.echo([], rows), [],
                                 f"{spec!r} produced an echo")

    def test_an_EMPTY_comma_token_is_skipped_and_the_echo_shows_that(self):
        """
        `parse_port_range` skips an empty token, so a trailing comma is legal
        and always was.  The echo is the only thing on screen that says the
        half-typed `F2.1,` currently means one port -- which is exactly what an
        echo is for, so this is not the no-echo case above.
        """
        got = self.echo([], [ConnectionRow(kind="ground", ports="F2.1,")])
        self.assertIn("F2.1, = F2.1", got[0][0])

    def test_the_echo_SURVIVES_a_problem_instead_of_being_suppressed(self):
        """
        Unlike the R/L/C echoes, which a problem replaces.  "Which file is that
        port of" is at its MOST useful when something else is wrong -- a bare
        token past the home file's port count is refused by naming F1, and the
        echo beside it is what shows the reader why it was read as F1.

        It is still LAST, because V_OK is the last tier, so the two-line strip
        shows the problem.
        """
        rows = [ConnectionRow(kind="short", ports="25,26,F2.15"),
                # values but no Port -> V_ROW_INERT
                ConnectionRow(kind="rlc_gnd", ports="", R="50")]
        echoes = self.echo([], rows)
        msgs = G._validation_messages(
            [], _scope_conn_rows(rows, self.net, "F1"), "",
            self.net.nports, None, echoes)
        self.assertTrue(any("has values but no Port" in m for m in msgs))
        self.assertTrue(any("F1.25-26, F2.15" in m for m in msgs),
                        "the scope echo was suppressed by the problem")
        self.assertGreater([i for i, m in enumerate(msgs)
                            if "F1.25-26" in m][0],
                           [i for i, m in enumerate(msgs)
                            if "no Port" in m][0],
                           "the echo must sort BELOW the problem")


class TestTheTwoNamespaceBuildersAgree(unittest.TestCase):
    """
    `_namespace_network` and `comp.compose` must answer identically about what
    a port reference MEANS.

    They exist separately for a measured reason: composing 16 + 153 ports at
    401 points is 10.5 s (10780 / 10346 / 10521 ms, three runs) and the strips
    run once per keystroke.  A namespace that disagreed with the composition
    would validate a spec that then addresses different ports -- which is the
    drift `trace_file_labels` already has to be kept mirrored against.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_blocks_offsets_and_gport_match_the_real_composition(self):
        die = parse_touchstone(str(DIE))
        pkg = parse_touchstone(str(PKG))
        real = comp.compose([comp.ComposeInput(data=die, alias="F1"),
                             comp.ComposeInput(data=pkg, alias="F2")])
        fake = _namespace_network([FileEntry(die), FileEntry(pkg)])
        self.assertEqual(real.nports, fake.nports)
        self.assertEqual([(b.alias, b.offset, b.nports) for b in real.blocks],
                         [(b.alias, b.offset, b.nports) for b in fake.blocks])
        for spec in ("1", "4", "F2.1", "F2.2", "1-4"):
            with self.subTest(spec=spec):
                self.assertEqual(
                    comp.parse_scoped_ports(spec, real, default="F1"),
                    comp.parse_scoped_ports(spec, fake, default="F1"))

    def test_the_home_file_is_block_zero_with_every_port_kept(self):
        """The property the bare-number rule rests on, stated directly."""
        fake = _net(6, 3)
        b = fake.blocks[0]
        self.assertEqual((b.alias, b.offset, b.local_ports),
                         ("F1", 0, [1, 2, 3, 4, 5, 6]))


# ============================================================================
# The plot label and the plot axis (pure)
# ============================================================================

class TestPlotLabel(unittest.TestCase):

    def test_a_single_file_trace_legends_exactly_its_label(self):
        tc = TraceConfig(id=1, file_label="a.s2p", label="coil")
        self.assertEqual(_plot_trace_label(tc), "coil")

    def test_a_composed_trace_carries_the_COUNT(self):
        tc = TraceConfig(id=1, file_label="a.s2p", file_labels=["b.s2p"],
                         label="coil")
        self.assertEqual(_plot_trace_label(tc), "coil +1")

    def test_the_marker_survives_the_legend_truncation(self):
        """
        `pkg_rlc_plot` truncates a legend entry to the FIRST MAX_LABEL_LEN
        characters, and the tool's own default label already overflows it for
        any file name of 20 characters.  Appending without trimming the base
        puts the one thing that says "this is the composed one" exactly where
        head-truncation deletes it -- `freeze_label`'s rule, and the reason for
        it, applied to the same budget.
        """
        long = "coupled_2port_gndref.s2p_p1_to_gnd"
        tc = TraceConfig(id=1, file_label="a.s2p", file_labels=["b.s2p"],
                         label=long)
        out = _plot_trace_label(tc)
        self.assertLessEqual(len(out), MAX_LABEL_LEN)
        self.assertTrue(out.endswith(" +1"), out)
        self.assertNotEqual(out[:MAX_LABEL_LEN], long[:MAX_LABEL_LEN])


class TestPlotFreqs(unittest.TestCase):

    def _fe(self, n=5):
        class _F:
            ts = type("t", (), {"freqs": np.linspace(1e9, 2e9, n)})()
        return _F()

    def test_a_single_file_trace_uses_the_file_s_own_sweep(self):
        tc = TraceConfig(id=1, file_label="a.s2p")
        fe = self._fe()
        self.assertIs(_trace_plot_freqs(tc, fe), fe.ts.freqs)

    def test_a_composed_trace_uses_its_cached_axis(self):
        tc = TraceConfig(id=1, file_label="a.s2p", file_labels=["b.s2p"])
        tc.net_freqs = np.linspace(1e9, 2e9, 7)
        self.assertIs(_trace_plot_freqs(tc, self._fe()), tc.net_freqs)

    def test_a_composed_trace_with_numbers_and_NO_axis_gets_None(self):
        """
        Not the home file's sweep.  The two are different lengths, so falling
        back would either raise halfway through a redraw or -- worse -- put the
        right values at the wrong frequencies and look like a plausible curve.
        """
        tc = TraceConfig(id=1, file_label="a.s2p", file_labels=["b.s2p"])
        tc.Z = np.zeros(9, dtype=complex)
        self.assertIsNone(_trace_plot_freqs(tc, self._fe()))


# ============================================================================
# The real App
# ============================================================================

@unittest.skipUnless(TK_OK, "no display")
class _AppCase(unittest.TestCase):
    """Two files loaded; one composed mode-5 trace hanging PKG off the die."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.die = FileEntry(parse_touchstone(str(DIE)))
        self.pkg = FileEntry(parse_touchstone(str(PKG)))
        self.app.files.extend([self.die, self.pkg])
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.die.label,
                              file_labels=[self.pkg.label], mode=5,
                              label="die+pkg", enabled=True)
        self.tc.mports = [MeasPortRow(name="vic", plus="1", minus="")]
        self.tc.conn_rows = [
            ConnectionRow(kind="short", ports="2", to="F2.1"),
            ConnectionRow(kind="ground", ports="F2.2"),
            ConnectionRow(kind="ground", ports="3,4"),
        ]
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.rlc_freq_var.set("5")
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _log(self) -> str:
        return self.app.results_text.get("1.0", tk.END)

    def _select(self, idx=0):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _calc(self):
        self.app._on_calculate()
        self._settle()


class TestTheComposedRun(_AppCase):

    def test_the_cross_file_connection_changes_the_answer(self):
        """
        The measurement this whole feature exists to produce.  The same probe
        on the same die, with and without the package hanging off port 2, must
        not give the same number -- if it did, the package would not be in the
        circuit and the composition would be decoration.
        """
        self._calc()
        with_pkg = complex(self.tc.rlc.Z)
        solo = TraceConfig(id=2, file_label=self.die.label, mode=5,
                           label="die", enabled=True)
        solo.mports = [MeasPortRow(name="vic", plus="1", minus="")]
        solo.conn_rows = [ConnectionRow(kind="ground", ports="3,4")]
        self.app.traces.append(solo)
        self._calc()
        self.assertNotEqual(with_pkg, complex(solo.rlc.Z))

    def test_the_axis_is_the_composed_one_everywhere_it_is_used(self):
        self._calc()
        n = len(self.tc.net_freqs)
        self.assertNotIn(n, (len(self.die.ts.freqs), len(self.pkg.ts.freqs)))
        self.assertEqual(len(self.tc.Z), n)
        curves = [t for t in self.app.plot.view.traces
                  if t.label.startswith("die+pkg")]
        self.assertEqual([len(c.freqs) for c in curves], [n])

    def test_the_composed_network_is_CACHED_across_runs(self):
        """
        Stacking is the expensive half -- measured at 10.5 s for 16 + 153 ports
        -- and the edit/recompute loop re-solves constantly.  The cache is
        validated by FileEntry IDENTITY, so this also pins that a second run
        does not silently rebuild.
        """
        self._calc()
        key = (self.die.label, self.pkg.label)
        self.assertIn(key, self.app._compose_cache)
        first = self.app._compose_cache[key][1]
        self._calc()
        self.assertIs(self.app._compose_cache[key][1], first)

    def test_reloading_a_file_under_the_same_name_invalidates_the_cache(self):
        """
        A label is re-used when a file is reloaded and the arrays behind it are
        then different objects.  A label-only key would have kept serving the
        previous parse's stack.
        """
        self._calc()
        key = (self.die.label, self.pkg.label)
        first = self.app._compose_cache[key][1]
        self.app.files[0] = self.die = FileEntry(parse_touchstone(str(DIE)))
        self.tc.file_label = self.die.label
        self._calc()
        self.assertIsNot(self.app._compose_cache[key][1], first)

    def test_the_run_row_names_both_files(self):
        self._calc()
        rec = self.app._last_run.rows[0]
        self.assertEqual([lbl for _a, lbl in rec.files],
                         [self.die.label, self.pkg.label])
        self.assertIn("F1+F2", self._log())

    def test_the_composition_notes_are_printed_with_the_number(self):
        """
        The weld note, the grid that was adopted, what was dropped and how much
        phase an interpolation invented are the ASSUMPTIONS the number rests
        on, so they belong beside it and not in a report nobody opens.
        """
        self._calc()
        log = self._log()
        self.assertIn("identifies their reference nodes at ZERO impedance", log)
        self.assertIn("common grid taken from", log)


class TestReferenceNodeCheckReachesTheReader(_AppCase):
    """R3-5."""

    def test_it_runs_and_is_frozen_on_the_row(self):
        self._calc()
        self.assertEqual([c.alias for c in self.tc.reference_checks],
                         ["F1", "F2"])
        rec = self.app._last_run.rows[0]
        self.assertIn("Reference-node check", rec.ref_strip)

    def test_the_verdict_is_in_the_LOG_under_the_table(self):
        self._calc()
        self.assertIn(self.app._last_run.rows[0].ref_strip, self._log())

    def test_it_is_on_the_run_PAGE_too(self):
        """
        `_run_report_segments` is the ONE builder, so the page and the Log
        cannot come to disagree about a run's contents.
        """
        self._calc()
        tab = self.app._run_tabs[0]
        page = tab.text.get("1.0", tk.END)
        self.assertIn(self.app._last_run.rows[0].ref_strip, page)

    def test_it_is_printed_exactly_once(self):
        """
        Mutation: reinstate the compute-time printer that `_run_report_segments`
        replaced.  Two copies of one verdict are two things that can disagree.
        """
        self._calc()
        strip = self.app._last_run.rows[0].ref_strip
        self.assertEqual(self._log().count(strip), 1)

    def test_a_single_file_trace_says_nothing_at_all(self):
        self.app.traces[:] = [TraceConfig(id=9, file_label=self.die.label,
                                          mode=1, port_a="1", gnd_ports="2-4",
                                          label="solo", enabled=True)]
        self.app._refresh_trace_list()
        self._calc()
        rec = self.app._last_run.rows[0]
        self.assertEqual((rec.ref_strip, rec.ref_lines), ("", ()))
        self.assertNotIn("Reference-node check", self._log())


class TestTheEditorUnderstandsATagBeforeAnyCalculate(_AppCase):
    """
    The namespace is free, so a tagged cell validates on the keystroke that
    types it rather than after a Calculate.  With the real composition there
    instead, that would be up to 10.5 s per character.
    """

    def test_the_strips_do_not_report_a_tagged_cell_as_unparseable(self):
        """
        Asserted in the POSITIVE form: the strip names the tagged field and
        says what it resolved to.  It used to be `assertNotIn("F2.2", ...)`,
        on the reasoning that a refusal would quote the cell it could not
        read -- a proxy that a strip saying nothing at all also passes, and
        that the scope echo (which quotes the cell precisely BECAUSE it
        resolved) turned red while the behaviour got better.
        """
        self._select(0)
        text = self.app.ed_validation.cget("text")
        self.assertNotIn("⚠", text)
        self.assertNotIn("⚠", self.app.ed_footer_strip.cget("text"))
        self.assertIn("F2.2 = F2.2", text)

    def test_they_DO_report_a_bare_port_past_the_home_file(self):
        self.tc.conn_rows[1] = ConnectionRow(kind="ground", ports="9")
        self._select(0)
        self._settle()
        self.assertIn("⚠", self.app.ed_footer_strip.cget("text"))

    def test_no_composition_was_built_to_do_it(self):
        """Mutation: build the real network in `_editor_spec_inputs`."""
        self._select(0)
        self.assertEqual(self.app._compose_cache, {})


class TestPortsAndRolesShowsTheComposedNamespace(_AppCase):

    def test_the_header_names_the_file_SET_and_the_composed_port_count(self):
        self._select(0)
        header, roles, _warn = self.app._port_roles_data()
        self.assertIn("F1=", header)
        self.assertIn("F2=", header)
        self.assertEqual(len(roles), 6)

    def test_a_package_port_is_listed_with_its_tag(self):
        self._select(0)
        _h, roles, _w = self.app._port_roles_data()
        self.assertTrue(any(r.name.startswith("F2.") for r in roles),
                        [r.name for r in roles])


class TestExportCsv(_AppCase):

    def setUp(self):
        super().setUp()
        # `_on_export_csv` reports a failure through messagebox.showerror,
        # which is MODAL -- in a headless run it never returns and the test
        # HANGS rather than failing.  Measured: a mutation that writes the
        # composed Z against the home file's 401-point sweep raises, and this
        # class then ran for 900 s and was killed.  A dialog that cannot be
        # answered has to become a value the test can read.
        self._save = (G.filedialog.asksaveasfilename, G.messagebox.showerror)
        self.errors: list = []
        G.messagebox.showerror = lambda *a, **k: self.errors.append(a)

    def tearDown(self):
        G.filedialog.asksaveasfilename, G.messagebox.showerror = self._save
        super().tearDown()

    def _export(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.csv"
            G.filedialog.asksaveasfilename = lambda **k: str(path)
            self.app._on_export_csv()
            self.assertEqual(self.errors, [], "the export raised")
            return path.read_text(encoding="utf-8")

    def test_it_heads_the_block_with_every_file(self):
        self._calc()
        text = self._export()
        self.assertIn(f"# Files: F1={self.die.label} + F2={self.pkg.label}",
                      text)

    def test_the_rows_are_the_COMPOSED_sweep(self):
        """
        Mutation: take the axis from the home file.  It is a different length,
        so the file either stops short or lines every value up against the
        wrong frequency.
        """
        self._calc()
        rows = [r for r in csv.reader(io.StringIO(self._export()))
                if r and r[0] not in ("Freq_GHz",) and not r[0].startswith("#")]
        self.assertEqual(len(rows), len(self.tc.net_freqs))


@unittest.skipUnless(TK_OK, "no display")
class TestTheFilesWindowIsReachable(unittest.TestCase):
    """R3-3.  Not a fifth button on the Files or Traces rows -- both are
    measured at 448 px with four buttons already asking 364."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(str(DIE)))
        self.app.files.append(self.fe)
        self.app.traces.append(TraceConfig(id=1, file_label=self.fe.label,
                                           mode=1, port_a="1", label="t"))
        self.app._refresh_file_list()
        self.app._refresh_trace_list()
        for _ in range(3):
            self.app.update_idletasks()
            self.app.update()

    def tearDown(self):
        self.app.destroy()

    def _labels(self, menu):
        return [menu.entrycget(i, "label")
                for i in range(menu.index("end") + 1)]

    def test_it_is_on_the_analyze_cascade(self):
        self.assertIn(files_gui.FILES_MENU_LABEL,
                      self._labels(self.app._analyze_menu))

    def test_it_is_on_the_traces_right_click_menu(self):
        self.assertIn(files_gui.FILES_MENU_LABEL,
                      self._labels(self.app._trace_menu))

    def test_it_is_on_the_FILES_right_click_menu_too(self):
        """The question is asked while looking at the Files list."""
        self.assertIn(files_gui.FILES_MENU_LABEL,
                      self._labels(self.app._files_menu))

    def test_no_fifth_button_was_added_to_either_row(self):
        """
        The measured budget: 448 px per row, four buttons ask 364.  A fifth is
        not clipped -- pack UNMAPS from the end, so it simply is not there.
        """
        for lb in (self.app.files_lb, self.app.traces_lb):
            row = lb.master.winfo_children()[0]
            btns = [w for w in row.winfo_children()
                    if w.winfo_class() == "TButton"]
            self.assertEqual(len(btns), 4,
                             [b.cget("text") for b in btns])

    def test_opening_it_registers_a_live_window_on_the_selected_trace(self):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app._on_files_window()
        for _ in range(3):
            self.app.update_idletasks()
            self.app.update()
        wins = files_gui.live_windows(self.app)
        self.assertEqual(len(wins), 1)
        self.assertIs(wins[0]._trace, self.app.traces[0])

    def test_the_editor_strips_refresh_it(self):
        """
        Mutation: drop the `refresh_files_windows` call from
        `_apply_editor_strips`.  The window then keeps showing the file set
        from before the edit, with no error anywhere.
        """
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        win = self.app._on_files_window()
        seen = []
        win.refresh = lambda: seen.append(1)
        self.app._apply_editor_strips()
        self.assertTrue(seen)


@unittest.skipUnless(TK_OK, "no display")
class TestAttributionOfAComposition(_AppCase):
    """
    R2-8 in the window.  An all-open baseline on a composition leaves the files
    as disconnected islands: measured with the real engine on a 12-port
    combined network, the EM-vs-PKG off-diagonal block of Ybase is 0.000e+00,
    every package-only element contributes EXACTLY 0, and the reconciliation
    residual reads 6.49e-15 -- perfect health, wrong answer.
    """

    def setUp(self):
        super().setUp()
        # Two measurement ports, so there is a mutual impedance to attribute.
        self.tc.mports = [MeasPortRow(name="vic", plus="1", minus=""),
                          MeasPortRow(name="agg", plus="3", minus="")]
        self.tc.conn_rows = [
            ConnectionRow(kind="short", ports="2", to="F2.1"),
            ConnectionRow(kind="rlc_gnd", ports="F2.2", R="0.5"),
            ConnectionRow(kind="ground", ports="4"),
        ]
        self._calc()

    def test_the_window_opens_and_the_baseline_carries_the_cross_file_link(self):
        import pkg_rlc_attrib_gui as ag
        res = ag.compute_attribution(self.app, self.tc, self.die,
                                     "vic", "agg", "M", 5e9)
        self.assertTrue(res.ctx.baseline_note,
                        "the composed-network gauge was not applied")
        self.assertTrue(res.ctx.structural,
                        "the cross-file link is not in the baseline")

    def test_a_single_file_attribution_still_has_NO_gauge(self):
        """Every field this round added is empty on the one-file case."""
        import pkg_rlc_attrib_gui as ag
        solo = TraceConfig(id=2, file_label=self.die.label, mode=6,
                           label="solo", enabled=True)
        solo.mports = [MeasPortRow(name="vic", plus="1", minus="2"),
                       MeasPortRow(name="agg", plus="3", minus="4")]
        self.app.traces.append(solo)
        self._calc()
        res = ag.compute_attribution(self.app, solo, self.die,
                                     "vic", "agg", "M", 5e9)
        self.assertEqual(res.ctx.baseline_note, "")
        self.assertFalse(res.ctx.structural)

    def test_the_package_element_is_not_reported_as_contributing_zero(self):
        """
        The measured failure, stated as the property that rules it out: with
        the files as islands the package's own resistor contributes EXACTLY 0.
        """
        import pkg_rlc_attrib_gui as ag
        res = ag.compute_attribution(self.app, self.tc, self.die,
                                     "vic", "agg", "M", 5e9)
        terms = [t for t in res.dec.terms if t.element is not None]
        self.assertTrue(terms, "no elements were decomposed at all")
        self.assertTrue(any(abs(t.contribution) > 0.0 for t in terms),
                        [(t.label, t.contribution) for t in terms])


if __name__ == "__main__":
    unittest.main()
