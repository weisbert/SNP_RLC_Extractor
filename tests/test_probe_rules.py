"""
The probe rules of the one row model (docs/design_workspaces.md § 3.3).

Before the merge there were two rules for "a probe port is also grounded":
Mode 6 refused it and Modes 1/2/3/5 let the ground win.  There is one rule
now, decided by the SIDE the port is on, and it lives in ONE place --
`pkg_rlc.physics.spec.probe_rule_issues` -- which the build raises from, the
editor's cells are coloured from, and the validation strip quotes:

  * a '+' port in a ground / vdd row        -> ERROR   (column "plus")
  * a '-' port in a ground / vdd row        -> WARNING (column "minus"), and
    the WHOLE '-' side is folded into ground (it is one tied node), so the
    trace measures '+' to GND
  * the refusals the old Mode 6 always had: a reserved name (A / B), a
    repeated name, a '-' side with no '+' side, a port on both sides, a port
    claimed by two measurement ports, a port past the file's port count.

What is pinned here:

  * every rule's table / row / column / severity, with `row` counting BLANK
    rows (the editor holds the same list and finds the widget by it);
  * that the build refuses on the first error and folds on the warning;
  * the § 3.3 table, MEASURED at 1 GHz on the three 4-port fixtures: the old
    "ground wins" value of a partly grounded '-' side, the new value, and
    that a fully grounded '-' side is `np.array_equal` to '+' alone;
  * that the validation strip and the Ports & Roles flags say the new rule
    in the L0 checker's own words, and nothing says "the ground row wins".

No Tk.  In the fast set.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    Ground,
    ISSUE_ERROR,
    ISSUE_WARNING,
    MeasPortRow,
    ROLE_GROUND,
    ROLE_PROBE_PLUS,
    build_terminations_rows,
    compute_z,
    extract_rlc_at_freq,
    fold_grounded_minus,
    parse_touchstone,
    port_roles,
    probe_rule_issues,
    s_to_y,
)
from pkg_rlc.model.trace import TraceConfig  # noqa: E402
from pkg_rlc.model.validate import (  # noqa: E402
    V_NO_RESULT,
    V_ROW_INERT,
    WARN_MINUS_GROUNDED,
    WARN_PLUS_GROUNDED,
    WARN_PROBE_AND_GROUND,
    WARN_PROBE_AND_GROUND_COUPLING,
    _role_warnings,
    _trace_role_rows,
    _validation_messages,
    _validation_report,
)

FIXTURES = _HERE / "fixtures"

M = MeasPortRow


def gnd(ports: str, **kw) -> ConnectionRow:
    return ConnectionRow(kind="ground", ports=ports, **kw)


def cells(issues) -> list:
    """(table, row, column, severity) of every issue, in order."""
    return [(i.table, i.row, i.column, i.severity) for i in issues]


# ============================================================================
# Every rule: which cell, and how bad
# ============================================================================

class TestEachRuleNamesItsCell(unittest.TestCase):

    def test_a_clean_spec_has_nothing_to_say(self):
        self.assertEqual(
            probe_rule_issues([M("in", "1", "2"), M("out", "3", "4")],
                              [gnd("5,6")], "", nports=6), [])

    # -- the ground rule, by side ---------------------------------------

    def test_a_plus_port_in_a_ground_row_is_an_error_on_the_plus_cell(self):
        issues = probe_rule_issues([M("m1", "3", "")], [gnd("3")])
        self.assertEqual(cells(issues),
                         [("mports", 0, "plus", ISSUE_ERROR)])
        self.assertIn("Port 3", issues[0].message)
        self.assertIn("'+' side", issues[0].message)
        self.assertIn("nothing to measure", issues[0].message)
        self.assertTrue(issues[0].is_error)

    def test_a_vdd_row_is_ground_for_this_rule(self):
        issues = probe_rule_issues([M("m1", "3", "")],
                                   [ConnectionRow(kind="vdd", ports="3")])
        self.assertEqual(cells(issues),
                         [("mports", 0, "plus", ISSUE_ERROR)])

    def test_a_minus_port_in_a_ground_row_is_a_WARNING_on_the_minus_cell(self):
        issues = probe_rule_issues([M("", "1", "3,4")], [gnd("3")])
        self.assertEqual(cells(issues),
                         [("mports", 0, "minus", ISSUE_WARNING)])
        self.assertFalse(issues[0].is_error)
        # The design's wording, verbatim: which port is grounded, what is
        # measured instead (by the name the row is SOLVED under -- a blank
        # name is P1), and that the whole side is at ground.
        self.assertEqual(
            issues[0].message,
            "'-' side is grounded (port 3), so this measures P1 to GND; "
            "ports 3,4 are all at GND.")

    def test_both_sides_grounded_is_the_error_alone(self):
        """A refused trace is not ALSO 'measured to GND'."""
        issues = probe_rule_issues([M("m", "1", "3")], [gnd("1,3")])
        self.assertEqual(cells(issues), [("mports", 0, "plus", ISSUE_ERROR)])

    def test_a_switched_off_ground_row_grounds_nothing(self):
        self.assertEqual(
            probe_rule_issues([M("m", "3", "")], [gnd("3", enabled=False)]),
            [])

    def test_the_kept_text_is_not_second_guessed(self):
        """extra_lines is emitted last and is the user's explicit word."""
        self.assertEqual(
            probe_rule_issues([M("m", "3", "")], [], "3 ground\n"), [])

    # -- the refusals the old Mode 6 had ---------------------------------

    def test_A_and_B_are_reserved_names_in_either_case(self):
        issues = probe_rule_issues([M("A", "1", ""), M("b", "2", "")])
        self.assertEqual(cells(issues),
                         [("mports", 0, "name", ISSUE_ERROR),
                          ("mports", 1, "name", ISSUE_ERROR)])
        self.assertIn("'A' is reserved", issues[0].message)
        self.assertIn("'b' is reserved", issues[1].message)

    def test_a_repeated_name_flags_the_SECOND_row(self):
        issues = probe_rule_issues([M("tank", "1", ""), M("tank", "2", "")])
        self.assertEqual(cells(issues), [("mports", 1, "name", ISSUE_ERROR)])
        self.assertIn("'tank'", issues[0].message)

    def test_a_minus_side_with_no_plus_side(self):
        issues = probe_rule_issues([M("m", "", "2")])
        self.assertEqual(cells(issues), [("mports", 0, "plus", ISSUE_ERROR)])
        self.assertIn("no '+' side", issues[0].message)

    def test_a_name_with_no_ports_is_not_a_probe_rule(self):
        """The strip says that one ('has a name but no ports'); the rules
        have nothing to measure and say nothing."""
        self.assertEqual(probe_rule_issues([M("m", "", "")]), [])

    def test_a_port_on_both_sides(self):
        issues = probe_rule_issues([M("m", "1,2", "2,3")])
        self.assertEqual(cells(issues), [("mports", 0, "minus", ISSUE_ERROR)])
        self.assertIn("port 2", issues[0].message)
        self.assertIn("both", issues[0].message)

    def test_a_port_claimed_by_two_measurement_ports(self):
        issues = probe_rule_issues([M("x", "1", "2"), M("y", "3", "2")])
        self.assertEqual(cells(issues), [("mports", 1, "minus", ISSUE_ERROR)])
        self.assertIn("'x'", issues[0].message)

    def test_out_of_range_only_when_the_port_count_is_known(self):
        rows = [M("m", "1", "9")]
        self.assertEqual(probe_rule_issues(rows), [])
        issues = probe_rule_issues(rows, nports=4)
        self.assertEqual(cells(issues), [("mports", 0, "minus", ISSUE_ERROR)])
        self.assertIn("(4 ports)", issues[0].message)
        plus = probe_rule_issues([M("m", "0,5", "")], nports=4)
        self.assertEqual(cells(plus), [("mports", 0, "plus", ISSUE_ERROR)])

    # -- the index contract ----------------------------------------------

    def test_row_counts_BLANK_rows(self):
        """`row` indexes the list passed in, so the editor -- which holds the
        same list, blanks and all -- finds the widget by it."""
        issues = probe_rule_issues([M(), M("m", "3", ""), M()], [gnd("3")])
        self.assertEqual(cells(issues), [("mports", 1, "plus", ISSUE_ERROR)])

    def test_it_never_raises_on_a_half_typed_spec(self):
        for plus, minus, g in (("5:", "", "3"), ("-", "1", ""),
                               ("1", "abc", "1-"), ("1", "2", "5:1:")):
            with self.subTest(plus=plus, minus=minus, g=g):
                probe_rule_issues([M("m", plus, minus)], [gnd(g)], nports=4)


# ============================================================================
# The build obeys the rules
# ============================================================================

class TestTheBuildObeysTheRules(unittest.TestCase):

    def test_the_build_raises_the_FIRST_error_in_its_own_words(self):
        rows, conn = [M("A", "1", ""), M("m", "3", "")], [gnd("3")]
        first = [i for i in probe_rule_issues(rows, conn) if i.is_error][0]
        with self.assertRaises(ValueError) as cm:
            build_terminations_rows(rows, conn)
        self.assertEqual(str(cm.exception), first.message)

    def test_a_grounded_minus_side_is_folded_WHOLE(self):
        ts = build_terminations_rows([M("", "1", "3,4")], [gnd("3")])
        self.assertIsInstance(ts.termination_of(2), Ground)
        self.assertIsInstance(ts.termination_of(3), Ground,
                              "port 4 shares the '-' node with grounded "
                              "port 3, so it is at GND too")

    def test_fold_hands_back_the_same_rows_when_nothing_folds(self):
        """So a spec without the case is the same DSL text, byte for byte."""
        mports, conn = [M("m", "1", "2")], [gnd("3")]
        m2, c2 = fold_grounded_minus(mports, conn)
        self.assertEqual(len(m2), 1)
        self.assertIs(m2[0], mports[0])
        self.assertEqual(len(c2), 1)
        self.assertIs(c2[0], conn[0])

    def test_fold_clears_the_minus_cell_and_grounds_the_whole_side(self):
        m2, c2 = fold_grounded_minus([M("m", "1", "3,4")], [gnd("3")])
        self.assertEqual((m2[0].plus, m2[0].minus), ("1", ""))
        self.assertEqual([(r.kind, r.ports) for r in c2],
                         [("ground", "3"), ("ground", "3,4")])


# ============================================================================
# design § 3.3, measured: 1 GHz, '+1 -3,4', on the three 4-port fixtures
# ============================================================================

def _l_nh(fixture: str, mports, conn) -> tuple:
    """(Z over the sweep, L in nH at the point nearest 1 GHz)."""
    ts = parse_touchstone(str(FIXTURES / fixture))
    Y = s_to_y(ts.s, ts.z0)
    term = build_terminations_rows(mports, conn, "", nports=ts.nports)
    Z, _w = compute_z(Y, ts.freqs, term)
    return Z, extract_rlc_at_freq(ts.freqs, Z, 1e9).L_henry * 1e9


class TestTheDesignTable(unittest.TestCase):
    """
    The table in docs/design_workspaces.md § 3.3, as numbers.

        spec                              diff_pair   decap        float
        '+1', ground 3,4 (reference)      5.001 nH    -12642 nH    0 nH
        '+1 -3,4', ground 3,4             identical   identical    identical
        '+1 -3,4', ground 3 -- OLD        -12635 nH   -12667 nH    NaN
        '+1 -3,4', ground 3 -- NOW        = reference, bit for bit

    The OLD row is what "ground wins" did: it dropped the grounded port 3
    from the probe and solved '+1 -4' with port 3 at ground, i.e. a probe
    whose '-' terminal is a port the spec itself says is tied to ground.
    It is computed here as exactly that spec, so the number it pins is the
    one the old path reported (tests/test_trace_path_golden.py pins the old
    path itself against the captured fixture).

    Mutation: make `fold_grounded_minus` return its input unchanged -- the
    partial case falls through to the parser, which lets ground win again,
    and `test_a_partly_grounded_minus_side_now_reads_as_the_reference` reads
    -12635 nH where 5.001 was expected.
    """

    FIXTURES_ = {
        # fixture: (reference nH, old partial-ground nH or None for NaN)
        "diff_pair_4port.s4p": (5.001, -12635.131),
        "decap_4port.s4p": (-12642.131, -12667.391),
        "coupled_4port_float.s4p": (0.0, None),
    }
    PLUS1 = [M("P1", "1", "")]
    PLUS1_MINUS34 = [M("P1", "1", "3,4")]

    def test_the_reference(self):
        for fx, (ref, _old) in self.FIXTURES_.items():
            with self.subTest(fx):
                _z, l_nh = _l_nh(fx, self.PLUS1, [gnd("3,4")])
                self.assertAlmostEqual(l_nh, ref, places=3)

    def test_a_wholly_grounded_minus_side_is_the_reference_bit_for_bit(self):
        """'+1 -3,4 ground 3,4' is array_equal to '+1, ground 3,4'."""
        for fx in self.FIXTURES_:
            with self.subTest(fx):
                ref, _ = _l_nh(fx, self.PLUS1, [gnd("3,4")])
                full, _ = _l_nh(fx, self.PLUS1_MINUS34, [gnd("3,4")])
                np.testing.assert_array_equal(full, ref)

    def test_the_old_ground_wins_value_was_wrong(self):
        for fx, (ref, old) in self.FIXTURES_.items():
            with self.subTest(fx):
                _z, l_nh = _l_nh(fx, [M("P1", "1", "4")], [gnd("3")])
                if old is None:
                    self.assertTrue(np.isnan(l_nh), l_nh)
                else:
                    self.assertAlmostEqual(l_nh, old, places=3)
                    self.assertGreater(abs(l_nh - ref), 20.0)

    def test_a_partly_grounded_minus_side_now_reads_as_the_reference(self):
        """The one intended change of the merge, as a bug fix."""
        for fx, (ref, _old) in self.FIXTURES_.items():
            with self.subTest(fx):
                want, _ = _l_nh(fx, self.PLUS1, [gnd("3,4")])
                got, l_nh = _l_nh(fx, self.PLUS1_MINUS34, [gnd("3")])
                np.testing.assert_array_equal(got, want)
                self.assertAlmostEqual(l_nh, ref, places=3)

    def test_and_that_is_what_the_spec_MEANS(self):
        """'+1', ground 3, short 3-4: the physical reading, written out."""
        for fx in self.FIXTURES_:
            with self.subTest(fx):
                meant, _ = _l_nh(fx, self.PLUS1,
                                 [gnd("3"),
                                  ConnectionRow(kind="short", ports="3,4")])
                got, _ = _l_nh(fx, self.PLUS1_MINUS34, [gnd("3")])
                np.testing.assert_array_equal(got, meant)

    def test_a_grounded_plus_side_is_refused_not_solved(self):
        with self.assertRaises(ValueError) as cm:
            _l_nh("diff_pair_4port.s4p", [M("P1", "1,3", "")], [gnd("3")])
        self.assertIn("nothing to measure", str(cm.exception))


# ============================================================================
# The messages: one source of truth, and the new rule's words
# ============================================================================

class TestTheStripQuotesTheRules(unittest.TestCase):

    def test_every_rule_message_reaches_the_strip_verbatim(self):
        cases = [
            ([M("m1", "3", "")], [gnd("3")], 4),
            ([M("", "1", "3,4")], [gnd("3")], 4),
            ([M("A", "1", "")], [], 4),
            ([M("t", "1", ""), M("t", "2", "")], [], 4),
            ([M("m", "", "2")], [], 4),
            ([M("m", "1,2", "2")], [], 4),
            ([M("x", "1", "2"), M("y", "2", "")], [], 4),
            ([M("m", "1", "9")], [], 4),
        ]
        for mports, conn, n in cases:
            with self.subTest(mports=mports, conn=conn):
                want = ["⚠ " + i.message
                        for i in probe_rule_issues(mports, conn, "", n)]
                self.assertTrue(want)
                msgs = _validation_messages(mports, conn, "", n)
                for w in want:
                    self.assertIn(w, msgs)

    def test_the_same_problem_is_said_ONCE(self):
        """The build raises the first rule error; the strip already has it
        in the cell's words, so the build's copy is not added again.  Nor is
        the file-size refusal for a port the rule already flagged."""
        for mports, conn in (([M("m1", "3", "")], [gnd("3")]),
                             ([M("m", "1", "9")], [])):
            with self.subTest(mports=mports):
                msgs = _validation_messages(mports, conn, "", 4)
                self.assertEqual(len(msgs), 1, msgs)

    def test_a_connection_port_out_of_range_still_gets_the_build_message(self):
        msgs = _validation_messages([M("m", "1", "")], [gnd("9")], "", 4)
        self.assertTrue(any("outside this file's 4 ports" in m for m in msgs),
                        msgs)

    def test_tiers_and_anchors(self):
        """An error stops the trace (V_NO_RESULT); the folded '-' side
        computes (V_ROW_INERT).  The anchor counts NON-BLANK rows, the way
        the footer route does -- `SpecIssue.row` counts blanks."""
        rep = _validation_report([M(), M("m1", "3", "")], [gnd("3")], "", 4)
        self.assertEqual([(m.tier, m.anchor) for m in rep],
                         [(V_NO_RESULT, ("mport", 0))])
        rep = _validation_report([M("", "1", "3,4")], [gnd("3")], "", 4)
        self.assertEqual([(m.tier, m.anchor) for m in rep],
                         [(V_ROW_INERT, ("mport", 0))])

    def test_nothing_says_the_ground_row_wins_any_more(self):
        msgs = (_validation_messages([M("m1", "3", "")], [gnd("3")], "", 4)
                + _validation_messages([M("m1", "1", "3")], [gnd("3")], "", 4))
        for text in msgs + [WARN_PROBE_AND_GROUND,
                            WARN_PROBE_AND_GROUND_COUPLING]:
            self.assertNotIn("wins", text)
            self.assertNotIn("Mode", text)

    def test_a_probe_the_KEPT_TEXT_grounds_is_still_said(self):
        """The rules leave extra_lines alone; the strip does not."""
        msgs = _validation_messages([M("m1", "3", "")], [], "3 ground", 4)
        self.assertIn("kept as text", msgs[0])
        self.assertIn("no measurement port defined", msgs[1])

    def test_a_blank_name_colliding_with_a_typed_P1_is_named(self):
        """'' is solved as P1, so '' + 'P1' is one measurement port -- a
        collision the rules (typed names only) cannot see."""
        msgs = _validation_messages([M("", "1", ""), M("P1", "2", "")],
                                    [], "", 4)
        self.assertEqual(len(msgs), 1)
        self.assertIn("'P1'", msgs[0])


class TestPortsAndRolesSaysTheSide(unittest.TestCase):

    def _roles(self, mports, conn, n=4):
        term = build_terminations_rows(mports, conn, "", nports=n)
        return port_roles(term, n)

    def test_the_minus_side_is_flagged_as_measured_to_gnd(self):
        mports = [M("", "1", "3,4")]
        roles = self._roles(mports, [gnd("3")])
        self.assertEqual(roles[0].role, ROLE_PROBE_PLUS)
        self.assertEqual([roles[2].role, roles[3].role],
                         [ROLE_GROUND, ROLE_GROUND])
        warn = _role_warnings(roles, mports)
        self.assertEqual(warn, {3: WARN_MINUS_GROUNDED,
                                4: WARN_MINUS_GROUNDED})
        self.assertIn("measures '+' to GND", WARN_MINUS_GROUNDED)

    def test_the_plus_side_is_flagged_as_refused(self):
        """Only reachable through a permissive build -- the real one refuses,
        and the window then has no roles at all -- but the flag must state
        the rule, not 'ground wins'."""
        class R:
            def __init__(self, i, role):
                self.index, self.role, self.source = i, role, "conn row 1"
                self.name = ""
        warn = _role_warnings([R(1, ROLE_GROUND)], [M("m", "1", "")])
        self.assertEqual(warn, {1: WARN_PLUS_GROUNDED})
        self.assertIn("refused", WARN_PLUS_GROUNDED)

    def test_the_coupling_flag_no_longer_changes_the_answer(self):
        mports = [M("", "1", "3")]
        roles = self._roles(mports, [gnd("3")])
        self.assertEqual(_role_warnings(roles, mports, coupling=True),
                         _role_warnings(roles, mports, coupling=False))


class TestTraceRoleRowsAreTheMigratedRows(unittest.TestCase):

    def test_an_old_trace_is_read_through_a_migrated_COPY(self):
        tc = TraceConfig(mode=3, port_a="1", port_b="2", gnd_ports="5",
                         short_pairs="3-4")
        mports, conn, extra, src = _trace_role_rows(tc)
        self.assertEqual([(r.name, r.plus, r.minus) for r in mports],
                         [("P1", "1", "2")])
        self.assertEqual([(r.kind, r.ports, r.to) for r in conn],
                         [("ground", "5", ""), ("short", "3", "4")])
        # The labels name rows the user can SEE in the editor now.
        self.assertEqual(src[1], "probe row 1 (+)")
        self.assertEqual(src[2], "probe row 1 (−)")
        self.assertEqual(src[5], "conn row 1")
        # ...and the trace itself was not touched.
        self.assertEqual((tc.mode, tc.port_a, tc.mports), (3, "1", []))

    def test_a_migrated_trace_is_its_tables_verbatim(self):
        tc = TraceConfig(mode=5, table_version=1,
                         mports=[M("t", "1", "2")], conn_rows=[gnd("3")],
                         extra_lines="4 vdd\n")
        mports, conn, extra, src = _trace_role_rows(tc)
        self.assertEqual(mports, tc.mports)
        self.assertEqual(conn, tc.conn_rows)
        self.assertEqual(extra, "4 vdd\n")
        self.assertEqual(src[4], "text line 1")


class TestTheReviewFindings(unittest.TestCase):
    """Added after stage 2's adversarial review (2026-10-02)."""

    def test_every_issue_carries_the_code_of_its_rule(self):
        iss = probe_rule_issues([MeasPortRow("P1", "1", "3,4")],
                                [ConnectionRow("ground", "3")])
        self.assertEqual([i.code for i in iss], ["minus_grounded"])

    def test_the_fold_keys_on_the_code_not_on_any_minus_warning(self):
        # A '-' port an OPEN row takes off the probe is a warning on the
        # minus cell too -- and must NOT be folded into ground.
        mp = [MeasPortRow("P1", "1", "2,3")]
        conn = [ConnectionRow("open", "3")]
        iss = probe_rule_issues(mp, conn)
        self.assertEqual([(i.column, i.code) for i in iss],
                         [("minus", "probe_overridden")])
        self.assertEqual(fold_grounded_minus(mp, conn), (mp, conn))

    def test_an_open_or_element_row_on_a_probe_port_is_said(self):
        for kind, extra, says in (("open", {}, "an open row"),
                                  ("rlc_gnd", {"R": "50"},
                                   "an element-to-GND row")):
            with self.subTest(kind=kind):
                iss = probe_rule_issues(
                    [MeasPortRow("P1", "1,2", "")],
                    [ConnectionRow(kind, "2", **extra)])
                self.assertEqual(len(iss), 1)
                self.assertEqual(iss[0].severity, ISSUE_WARNING)
                self.assertIn(says, iss[0].message)

    def test_a_vdd_row_is_named_as_one(self):
        iss = probe_rule_issues([MeasPortRow("P1", "1", "")],
                                [ConnectionRow("vdd", "1")])
        self.assertIn("vdd row", iss[0].message)

    def test_a_one_port_minus_side_reads_as_english(self):
        iss = probe_rule_issues([MeasPortRow("P1", "1", "2")],
                                [ConnectionRow("ground", "2")])
        self.assertNotIn("ports 2 are", iss[0].message)
        self.assertTrue(iss[0].message.endswith("measures P1 to GND."))

    def test_the_build_raises_the_error_the_strip_leads_with(self):
        # An out-of-range port ahead of a grounded '+' port: the build and
        # the strip (both with nports) must lead with the same complaint.
        mp = [MeasPortRow("", "9", ""), MeasPortRow("x", "2", "")]
        conn = [ConnectionRow("ground", "2")]
        first = [i for i in probe_rule_issues(mp, conn, nports=4)
                 if i.is_error][0]
        with self.assertRaises(ValueError) as cm:
            build_terminations_rows(mp, conn, nports=4)
        self.assertEqual(str(cm.exception), first.message)

    def test_a_short_to_a_grounded_port_grounds_the_probe_port(self):
        # Stage-3 review: '+1; short 1,3; ground 3' was solved as '+1' alone,
        # the ground silently dropped.  On '+' it is refused; on '-' it is
        # the whole '-' side at ground, folded like a direct ground row.
        iss = probe_rule_issues([MeasPortRow("P1", "1", "")],
                                [ConnectionRow("short", "1,3"),
                                 ConnectionRow("ground", "3")])
        self.assertEqual([(i.column, i.code) for i in iss],
                         [("plus", "plus_grounded")])
        self.assertIn("short ties it to grounded port 3", iss[0].message)
        mp = [MeasPortRow("P1", "1", "2")]
        conn = [ConnectionRow("short", "2", "4"), ConnectionRow("ground", "4")]
        iss = probe_rule_issues(mp, conn)
        self.assertEqual([i.code for i in iss], ["minus_grounded"])
        fmp, fconn = fold_grounded_minus(mp, conn)
        self.assertEqual(fmp[0].minus, "")
        self.assertEqual((fconn[-1].kind, fconn[-1].ports), ("ground", "2"))


class TestNoTk(unittest.TestCase):

    def test_nothing_here_imports_tkinter(self):
        self.assertNotIn("tkinter", sys.modules)
        self.assertNotIn("pkg_rlc.frontend.app", sys.modules)


if __name__ == "__main__":
    unittest.main()
