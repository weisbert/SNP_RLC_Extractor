"""
Tests for the connection-table row model in pkg_rlc_core.

The GUI's Mode 5 / Mode 6 editor stores two tables of rows; rows reach a
TerminationSet by being serialised to DSL text and handed to the existing
parser.  These tests pin the two properties that makes that safe:

  * ROUND TRIP -- rows -> text -> rows is idempotent, ranges survive as ranges,
    and nothing the user typed is silently dropped.
  * EQUIVALENCE -- rows reproduce build_terminations_mode1/2/3 exactly,
    INCLUDING the "ground wins" overlap case that the golden reference does not
    cover (see tests/test_core.py::TestTerminationPrecedence for why).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
    ShortPair,
    Signal,
    Vdd,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_rows,
    compute_z,
    dsl_text_to_rows,
    parse_custom_termination_text,
    parse_short_pairs,
    parse_touchstone,
    resolve_meas_ports,
    rows_to_dsl_text,
    s_to_y,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _ensure_fixtures() -> None:
    needed = ["diff_pair_4port.s4p", "decap_4port.s4p", "pi_2port.s2p"]
    if all((FIX / n).exists() for n in needed):
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


class TestRowsToText(unittest.TestCase):
    def test_measurement_port_emits_both_sides(self):
        text = rows_to_dsl_text([MeasPortRow("tank", "1", "2")], [])
        self.assertEqual(text, "1 signal tank +\n2 signal tank -\n")

    def test_ground_referenced_port_emits_plus_only(self):
        text = rows_to_dsl_text([MeasPortRow("rx", "5,7", "")], [])
        self.assertEqual(text, "5,7 signal rx +\n")

    def test_ranges_are_not_expanded(self):
        """'5:12' stays one row and one line -- that is the whole point."""
        text = rows_to_dsl_text([], [ConnectionRow(kind="ground", ports="5:1:12")])
        self.assertEqual(text, "5:1:12 ground\n")

    def test_blank_rlc_fields_are_omitted_not_zeroed(self):
        """Omitted C means C=inf (no capacitor), never C=0 (an open)."""
        row = ConnectionRow(kind="rlc_gnd", ports="3", R="50", L="", C="")
        self.assertEqual(rows_to_dsl_text([], [row]), "3 lumped_to_gnd R=50\n")

    def test_rlc_order_is_canonical(self):
        row = ConnectionRow(kind="rlc_between", ports="3", to="4",
                            C="1p", R="0.01", L="0.1n")
        self.assertEqual(rows_to_dsl_text([], [row]),
                         "3 lumped_between 4 R=0.01 L=0.1n C=1p\n")

    def test_blank_rows_are_skipped(self):
        rows = [ConnectionRow(kind="ground", ports=""),
                ConnectionRow(kind="ground", ports="3")]
        self.assertEqual(rows_to_dsl_text([], rows), "3 ground\n")
        self.assertEqual(rows_to_dsl_text([MeasPortRow()], []), "")

    def test_unnamed_measurement_ports_get_p1_p2(self):
        text = rows_to_dsl_text([MeasPortRow("", "1", "2"),
                                 MeasPortRow("", "3", "4")], [])
        self.assertIn("signal P1 +", text)
        self.assertIn("signal P2 +", text)

    def test_extra_lines_are_appended_verbatim(self):
        text = rows_to_dsl_text([], [ConnectionRow(kind="ground", ports="3")],
                                extra_lines="# hand-written note")
        self.assertEqual(text, "3 ground\n# hand-written note\n")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError) as cm:
            rows_to_dsl_text([], [ConnectionRow(kind="teleport", ports="1")])
        self.assertIn("teleport", str(cm.exception))

    def test_measurement_ports_are_emitted_before_connections(self):
        """
        Order is load-bearing: the DSL is last-assignment-wins, so ground must
        come after the probe to reproduce the named modes' 'ground wins'.
        """
        text = rows_to_dsl_text([MeasPortRow("m1", "1,2", "")],
                                [ConnectionRow(kind="ground", ports="2")])
        self.assertEqual(text, "1,2 signal m1 +\n2 ground\n")
        ts = parse_custom_termination_text(text)
        self.assertIsInstance(ts.per_port[1], Ground)


class TestTextToRows(unittest.TestCase):
    def test_signal_lines_group_into_one_measurement_port(self):
        mports, conn, extra = dsl_text_to_rows(
            "1 signal tank +\n2 signal tank -\n")
        self.assertEqual(len(mports), 1)
        self.assertEqual((mports[0].name, mports[0].plus, mports[0].minus),
                         ("tank", "1", "2"))
        self.assertEqual(conn, [])
        self.assertEqual(extra, "")

    def test_legacy_a_b_normalises_to_one_measurement_port(self):
        """'signal B' is the minus side of A -- import it as one port, not two."""
        mports, _conn, _extra = dsl_text_to_rows("1 signal A\n2 signal B\n")
        self.assertEqual(len(mports), 1)
        self.assertEqual((mports[0].name, mports[0].plus, mports[0].minus),
                         ("A", "1", "2"))

    def test_repeated_group_merges_port_specs(self):
        mports, _c, _e = dsl_text_to_rows(
            "1 signal tank +\n5:1:8 signal tank +\n2 signal tank -\n")
        self.assertEqual(mports[0].plus, "1,5:1:8")
        self.assertEqual(mports[0].minus, "2")

    def test_connection_kinds_round_trip(self):
        _m, conn, _e = dsl_text_to_rows(
            "3 ground\n4 vdd\n12 open\n5 short_to 6\n"
            "7 lumped_to_gnd R=50\n8 lumped_between 9 R=1 L=1n C=2p\n")
        self.assertEqual([c.kind for c in conn],
                         ["ground", "vdd", "open", "short",
                          "rlc_gnd", "rlc_between"])
        self.assertEqual(conn[3].to, "6")
        self.assertEqual(conn[4].R, "50")
        self.assertEqual((conn[5].to, conn[5].R, conn[5].L, conn[5].C),
                         ("9", "1", "1n", "2p"))

    def test_comments_survive_in_extra(self):
        _m, conn, extra = dsl_text_to_rows("# my note\n3 ground\n")
        self.assertEqual(extra, "# my note")
        self.assertEqual(len(conn), 1)

    def test_unrepresentable_line_is_passed_through_not_dropped(self):
        _m, conn, extra = dsl_text_to_rows("3 ground\n9 wat_is_this\n")
        self.assertEqual(len(conn), 1)
        self.assertIn("wat_is_this", extra)

    def test_extra_kinds_are_exactly_the_kinds_the_parser_rejects(self):
        """
        `extra` is re-emitted at the END of a last-assignment-wins language.

        That is only safe because every DIRECTIVE line routed there is one the
        parser refuses outright -- so it can never quietly override an earlier
        row.  The two kind lists are maintained independently; this pins that
        they agree.
        """
        for line in ("9 wat_is_this",
                     "5 short_to",
                     "8 lumped_between",
                     "1 signal A x",
                     "3"):
            with self.subTest(line=line):
                mports, conn, extra = dsl_text_to_rows(line + "\n")
                self.assertEqual((mports, conn), ([], []))
                self.assertEqual(extra, line)
                with self.assertRaises(ValueError):
                    parse_custom_termination_text(line + "\n")

    def test_tail_comment_is_dropped(self):
        """
        A documented lossy normalisation: the 'Edit as text' dialog promises
        end-of-line comments are dropped, so it must not silently start
        keeping them (or start dropping the directive with them).
        """
        _m, conn, extra = dsl_text_to_rows("3 lumped_to_gnd r=50   # far end\n")
        self.assertEqual((conn[0].kind, conn[0].ports, conn[0].R),
                         ("rlc_gnd", "3", "50"))
        self.assertEqual(extra, "")


class TestRoundTripIdempotent(unittest.TestCase):
    """text -> rows -> text is idempotent after one pass, and preserves meaning."""

    SPECS = [
        "1 signal A\n2 signal B\n3 ground\n",
        "1 signal tank +\n2 signal tank -\n5:1:8 ground\n",
        "1 signal m1 +\n3 lumped_to_gnd R=50\n4 lumped_between 2 R=0.01 L=0.1n C=1p\n",
        "1 signal m1 +\n2 signal m1 -\n3 short_to 4\n12 open\n",
        "1,2 signal m1 +\n3,4 signal m1 -\n6-9 ground\n",
        # A connection BEFORE the signals, on ports that do not overlap. Every
        # other spec here puts the signals first, which is exactly why the
        # reordering hazard below stayed invisible.
        "3 ground\n1 signal m1 +\n2 signal m1 -\n",
    ]

    # The hazard the round trip cannot survive: a probe that overrides an
    # earlier ground on the SAME port. dsl_text_to_rows discards line order and
    # rows_to_dsl_text hoists every probe above every connection, so the ground
    # ends up last and wins instead of losing.
    FLIPPING_SPEC = "3 ground\n3 signal A\n4 signal B\n"

    def test_probe_after_ground_changes_meaning_on_import(self):
        direct = parse_custom_termination_text(self.FLIPPING_SPEC)
        self.assertIsInstance(direct.per_port[2], Signal)
        self.assertEqual([(mp.name, mp.plus, mp.minus)
                          for mp in resolve_meas_ports(direct, 4)],
                         [("A", [2], [3])])

        once = parse_custom_termination_text(
            rows_to_dsl_text(*dsl_text_to_rows(self.FLIPPING_SPEC)))
        self.assertIsInstance(once.per_port[2], Ground)
        with self.assertRaises(ValueError) as cm:
            resolve_meas_ports(once, 4)
        self.assertIn("No Signal-group-A ports", str(cm.exception))

    def test_whole_spec_as_extra_lines_is_bit_identical(self):
        """
        The correctness proof of the fallback: a spec the table would change is
        parked verbatim in extra_lines, and extra_lines is appended unchanged.
        """
        direct = parse_custom_termination_text(self.FLIPPING_SPEC)
        parked = build_terminations_rows([], [], self.FLIPPING_SPEC)
        self.assertEqual(direct.per_port.keys(), parked.per_port.keys())
        for port, term in direct.per_port.items():
            self.assertIs(type(term), type(parked.per_port[port]))
        self.assertEqual([(c.port_i, c.port_j) for c in direct.couplings],
                         [(c.port_i, c.port_j) for c in parked.couplings])
        self.assertEqual(
            [(mp.name, mp.plus, mp.minus)
             for mp in resolve_meas_ports(direct, 4)],
            [(mp.name, mp.plus, mp.minus)
             for mp in resolve_meas_ports(parked, 4)])

    def test_second_pass_changes_nothing(self):
        for spec in self.SPECS:
            with self.subTest(spec=spec):
                once = rows_to_dsl_text(*dsl_text_to_rows(spec))
                twice = rows_to_dsl_text(*dsl_text_to_rows(once))
                self.assertEqual(once, twice)

    def test_round_trip_preserves_the_termination_set(self):
        """
        Compare RESOLVED measurement ports, not the raw Signal objects.

        A legacy 'signal B' is stored as Signal("B", +1) but imports as the
        minus side of "A" -- Signal("A", -1).  Those are the same measurement
        by definition (resolve_meas_ports applies exactly that alias), so the
        raw dataclasses differ while the meaning does not.  Asserting on the
        resolved ports is the comparison that means something.
        """
        for spec in self.SPECS:
            with self.subTest(spec=spec):
                direct = parse_custom_termination_text(spec)
                viarows = build_terminations_rows(*dsl_text_to_rows(spec))
                self.assertEqual(direct.per_port.keys(), viarows.per_port.keys())
                for port, term in direct.per_port.items():
                    other = viarows.per_port[port]
                    self.assertIs(type(term), type(other), f"port {port}")
                self.assertEqual(
                    [(mp.name, mp.plus, mp.minus)
                     for mp in resolve_meas_ports(direct, 16)],
                    [(mp.name, mp.plus, mp.minus)
                     for mp in resolve_meas_ports(viarows, 16)])
                self.assertEqual(
                    [(c.port_i, c.port_j) for c in direct.couplings],
                    [(c.port_i, c.port_j) for c in viarows.couplings])


class TestRowsReproduceNamedModes(unittest.TestCase):
    """
    The equivalence test CLAUDE.md asks for on any new path to a TerminationSet.

    These are the cases the golden reference cannot see: it calls the builders
    directly, so a table that diverges here would leave every golden case green.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()
        cls.ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        cls.Y = s_to_y(cls.ts.s, cls.ts.z0)

    def _assert_same_Z(self, term_a, term_b, msg=""):
        Za, _ = compute_z(self.Y, self.ts.freqs, term_a)
        Zb, _ = compute_z(self.Y, self.ts.freqs, term_b)
        err = float(np.max(np.abs(Za - Zb)))
        self.assertLess(err, 1e-15, f"{msg}: max|dZ| = {err:.3e}")

    def test_reproduces_mode1(self):
        self._assert_same_Z(
            build_terminations_mode1([1], [2, 3, 4]),
            build_terminations_rows([MeasPortRow("A", "1", "")],
                                    [ConnectionRow(kind="ground", ports="2,3,4")]),
            "mode1")

    def test_reproduces_mode1_with_a_range(self):
        self._assert_same_Z(
            build_terminations_mode1([1], [2, 3, 4]),
            build_terminations_rows([MeasPortRow("A", "1", "")],
                                    [ConnectionRow(kind="ground", ports="2-4")]),
            "mode1 range")

    def test_reproduces_mode1_ground_wins_overlap(self):
        """Port 2 is in BOTH the signal group and GND. Ground must win."""
        self._assert_same_Z(
            build_terminations_mode1([1, 2], [2, 3]),
            build_terminations_rows([MeasPortRow("A", "1,2", "")],
                                    [ConnectionRow(kind="ground", ports="2,3")]),
            "mode1 overlap")

    def test_reproduces_mode2(self):
        self._assert_same_Z(
            build_terminations_mode2([1, 2], [3, 4], []),
            build_terminations_rows([MeasPortRow("A", "1,2", "3,4")], []),
            "mode2")

    def test_reproduces_mode2_ground_wins_overlap(self):
        self._assert_same_Z(
            build_terminations_mode2([1, 2], [3], [2, 4]),
            build_terminations_rows([MeasPortRow("A", "1,2", "3")],
                                    [ConnectionRow(kind="ground", ports="2,4")]),
            "mode2 overlap")

    def test_reproduces_mode3(self):
        self._assert_same_Z(
            build_terminations_mode3([1], [2], [], parse_short_pairs("3-4")),
            build_terminations_rows(
                [MeasPortRow("A", "1", "2")],
                [ConnectionRow(kind="short", ports="3", to="4")]),
            "mode3")

    def test_reproduces_mode3_multiport_short_group(self):
        self._assert_same_Z(
            build_terminations_mode3([1], [4], [], parse_short_pairs("1-2-3")),
            build_terminations_rows(
                [MeasPortRow("A", "1", "4")],
                [ConnectionRow(kind="short", ports="1", to="2,3")]),
            "mode3 chained short")

    def test_reproduces_lumped_case_on_decap_fixture(self):
        ts = parse_touchstone(FIX / "decap_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        hand = parse_custom_termination_text(
            "1 signal A\n2 signal B\n3 lumped_to_gnd R=50\n"
            "3 lumped_between 4 R=0.01 L=0.1n C=1p\n")
        rows = build_terminations_rows(
            [MeasPortRow("A", "1", "2")],
            [ConnectionRow(kind="rlc_gnd", ports="3", R="50"),
             ConnectionRow(kind="rlc_between", ports="3", to="4",
                           R="0.01", L="0.1n", C="1p")])
        Za, _ = compute_z(Y, ts.freqs, hand)
        Zb, _ = compute_z(Y, ts.freqs, rows)
        self.assertLess(float(np.max(np.abs(Za - Zb))), 1e-15)


class TestRowsValidation(unittest.TestCase):
    def test_out_of_range_port_is_rejected_when_nports_given(self):
        with self.assertRaises(ValueError) as cm:
            build_terminations_rows([MeasPortRow("m1", "1", "2")],
                                    [ConnectionRow(kind="ground", ports="9")],
                                    nports=4)
        self.assertIn("9", str(cm.exception))

    def test_in_range_ports_pass(self):
        term = build_terminations_rows([MeasPortRow("m1", "1", "2")],
                                       [ConnectionRow(kind="ground", ports="3,4")],
                                       nports=4)
        self.assertIsInstance(term.per_port[2], Ground)

    def test_vdd_row_builds_a_vdd_termination(self):
        term = build_terminations_rows([MeasPortRow("m1", "1", "2")],
                                       [ConnectionRow(kind="vdd", ports="3")])
        self.assertIsInstance(term.per_port[2], Vdd)

    def test_short_row_builds_shortpairs(self):
        term = build_terminations_rows(
            [], [ConnectionRow(kind="short", ports="1", to="2")])
        self.assertEqual(term.couplings, [ShortPair(0, 1)])

    def test_rlc_rows_build_the_right_coupling_types(self):
        term = build_terminations_rows(
            [], [ConnectionRow(kind="rlc_gnd", ports="1", R="50"),
                 ConnectionRow(kind="rlc_between", ports="2", to="3", L="1n")])
        self.assertIsInstance(term.per_port[0], LumpedToGnd)
        self.assertEqual(len(term.couplings), 1)
        self.assertIsInstance(term.couplings[0], LumpedBetween)


class TestRlcValuesMustBeOneToken(unittest.TestCase):
    """
    A cell value with a space in it must be refused, not serialised.

    The DSL is whitespace-tokenised and parse_kv_rlc_params drops any token
    without an '=', so before this 'R=5 m' computed R = 5 Ω -- a factor of
    1000 from the 5 mΩ that was typed -- and 'C=1 uF' computed C = 1 farad,
    a factor of 1e6. Nothing anywhere said so: the GUI's echo re-parses the
    raw cell as ONE token, so it printed '5 mΩ' next to the computed 5 Ω, and
    for '1 uF' / '0.5 nH' the echo failed silently and the strip fell through
    to '✓ no problems found'. The column header carries the unit, which is
    exactly what invites 'uF' into the cell.
    """

    SPACED = ("5 m", "1 uF", "0.5 nH", "50 Ohm", "1 000")

    def _row(self, key, value):
        return ConnectionRow(**{"kind": "rlc_gnd", "ports": "3", key: value})

    def test_every_rlc_column_rejects_a_value_with_a_space(self):
        for key in ("R", "L", "C"):
            for value in self.SPACED:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as cm:
                        rows_to_dsl_text([], [self._row(key, value)])
                    msg = str(cm.exception)
                    self.assertIn(value, msg)
                    self.assertIn(key, msg)

    def test_the_builder_reports_it_rather_than_computing_something_else(self):
        with self.assertRaises(ValueError) as cm:
            build_terminations_rows([MeasPortRow("m1", "1", "2")],
                                    [self._row("R", "5 m")], nports=4)
        self.assertIn("5 m", str(cm.exception))

    def test_one_token_values_are_untouched(self):
        """The whole point: '5m' still means 5 mΩ, and leading/trailing space
        is still stripped rather than being an error."""
        for value, expect in (("5m", "R=5m"), (" 5m ", "R=5m"),
                              ("1e-3", "R=1e-3"), ("5M", "R=5M")):
            with self.subTest(value=value):
                text = rows_to_dsl_text([], [self._row("R", value)])
                self.assertEqual(text.strip(), f"3 lumped_to_gnd {expect}")


class TestMeasurementPortRouting(unittest.TestCase):
    """
    The GUI routes a trace to the coupling path on the MEASUREMENT PORT COUNT,
    not on the mode number.  These pin the predicate it routes on.

    Before this branch, a Mode 5 spec defining two measurement ports went to
    compute_z, which returns Zmat[:, 0, 0] and appends a warning that the rest
    were ignored -- a wrong number with no visible difference.  Once Mode 5 and
    Mode 6 share an editor, "I defined two probes" has to mean the same thing
    in both, so the count decides.

    The single-port cases matter just as much in the other direction: they must
    keep resolving to exactly ONE port so they stay on the compute_z path and
    stay bit-identical (the golden reference pins that path).
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def _count(self, text, n=4):
        return len(resolve_meas_ports(parse_custom_termination_text(text), n))

    def test_legacy_a_b_is_one_measurement_port(self):
        self.assertEqual(self._count("1 signal A\n2 signal B\n"), 1)

    def test_signed_single_group_is_one_measurement_port(self):
        self.assertEqual(self._count("1 signal tank +\n2 signal tank -\n"), 1)

    def test_ground_referenced_probe_is_one_measurement_port(self):
        self.assertEqual(self._count("1 signal m1 +\n2 ground\n"), 1)

    def test_two_named_groups_are_two_measurement_ports(self):
        self.assertEqual(self._count(
            "1 signal tank +\n2 signal tank -\n"
            "3 signal vco +\n4 signal vco -\n"), 2)

    def test_two_measurement_port_rows_are_two_measurement_ports(self):
        term = build_terminations_rows(
            [MeasPortRow("tank", "1", "2"), MeasPortRow("vco", "3", "4")], [])
        self.assertEqual(len(resolve_meas_ports(term, 4)), 2)

    def test_compute_z_on_two_ports_is_the_lossy_path_being_avoided(self):
        """Documents exactly what the routing change stops happening."""
        ts = parse_touchstone(FIX / "diff_pair_4port.s4p")
        Y = s_to_y(ts.s, ts.z0)
        term = build_terminations_rows(
            [MeasPortRow("tank", "1", "2"), MeasPortRow("vco", "3", "4")], [])
        _Z, warns = compute_z(Y, ts.freqs, term)
        self.assertTrue(any("are ignored here" in w for w in warns),
                        f"expected the 'ignored' warning, got {warns}")


if __name__ == "__main__":
    unittest.main()
