"""
Named merged nodes ("nets"), and the refusal that makes them worth having.

Two halves of one problem, both measured on the 5-port probe network below
(every port to ground through 1 kOhm, no port-to-port path, so any Im(Z) can
only come from a lumped element in the spec):

  * a `lumped_between` / `lumped_to_gnd` row whose LEFT side lists N ports that
    a short has ALREADY tied into one node stamps N identical elements in
    PARALLEL.  Measured: `1 short_to 2,3` + `1,2,3 lumped_between 4 L=10f` reads
    3.333 fH where 10.000 fH was meant (ratio exactly 3.000), and
    `1 short_to 2,3,4` + `1,2,3 lumped_to_gnd R=50` reads 15.625 Ohm where
    41.667 Ohm was meant (250 || 16.7 instead of 250 || 50).  Nothing raised,
    nothing warned, and inert_lumped_messages -- the check next to this one --
    said nothing either, because the elements are not worth ZERO, they are
    worth N times too much.  parallel_stamp_messages is the refusal.

  * referring to the merged node by ONE member port always worked and still
    does; what was missing was a way to SAY the node.  A short row may now name
    it (`1,2,3 short as tap`) and any port field may use the name, which is
    pure sugar: a name resolves to one representative member, so the DSL, the
    reduction and the golden reference all see a spec that could have been
    typed by hand.

Every guard here was mutation-checked -- reverting the behaviour it describes
turns the test red.  The mutations are named in the docstrings.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from pkg_rlc_core import (  # noqa: E402
    CONN_KINDS_WITH_NET,
    NET_KEYWORD,
    NET_RESERVED_NAMES,
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
    MergedNode,
    ShortPair,
    Signal,
    compute_z_matrix,
    dsl_text_to_rows,
    inert_lumped_messages,
    merged_nodes,
    parallel_stamp_messages,
    parse_custom_termination_text,
    row_sources,
    rows_to_dsl_text,
    short_group_spec,
    validate_net_name,
    y_series_rlc,
)

# --- the probe network ------------------------------------------------------
# 5 ports, each to ground through 1 kOhm, nothing between ports.  A lumped
# element in the spec is therefore the ONLY thing that can put a reactance on
# the answer, which is what makes "10.000 fH" and "3.333 fH" readable directly
# off Im(Z)/omega instead of through a fit.
PROBE_FREQ_HZ = 5.0e9
PROBE_NPORTS = 5
_FREQS = np.array([PROBE_FREQ_HZ])
_OMEGA = 2 * math.pi * PROBE_FREQ_HZ


def _probe_Y() -> np.ndarray:
    Y = np.zeros((1, PROBE_NPORTS, PROBE_NPORTS), dtype=complex)
    for i in range(PROBE_NPORTS):
        Y[0, i, i] = 1e-3
    return Y


def _z_of(text: str) -> complex:
    """Z of measurement port 1 for a DSL spec, on the probe network."""
    ts = parse_custom_termination_text(text)
    Zmat, _names, _warn = compute_z_matrix(_probe_Y(), _FREQS, ts)
    return complex(Zmat[0, 0, 0])


def _L_fH(text: str) -> float:
    return _z_of(text).imag / _OMEGA * 1e15


# ============================================================================
# R1-3: the merged-node parallel stamp
# ============================================================================

class TestTheParallelStampIsReal(unittest.TestCase):
    """The measurements the refusal exists for.  No refusal is tested here --
    these pin that the WRONG NUMBER is real, so a later 'simplification' of the
    check cannot be justified by 'it never happens'."""

    SHORT = "1 short_to 2,3\n"
    PROBE = "1 signal A +\n4 signal A -\n"

    def test_one_member_on_the_element_gives_the_value_that_was_typed(self):
        L = _L_fH(self.SHORT + "1 lumped_between 4 L=10f\n" + self.PROBE)
        self.assertAlmostEqual(L, 10.0, places=3)

    def test_the_whole_merged_group_on_the_element_gives_it_divided_by_three(self):
        L = _L_fH(self.SHORT + "1,2,3 lumped_between 4 L=10f\n" + self.PROBE)
        self.assertAlmostEqual(L, 10.0 / 3.0, places=3)

    def test_the_ratio_is_exactly_the_number_of_listed_ports(self):
        one = _L_fH(self.SHORT + "1 lumped_between 4 L=10f\n" + self.PROBE)
        three = _L_fH(self.SHORT + "1,2,3 lumped_between 4 L=10f\n" + self.PROBE)
        self.assertAlmostEqual(one / three, 3.0, places=6)

    def test_ports_that_are_NOT_merged_are_three_separate_elements_and_correct(self):
        # 54 VSS bumps each with its own 20 pH is the normal flip-chip
        # connection: N separate elements is documented and intended.
        L = _L_fH("1,2,3 lumped_between 4 L=10f\n" + self.PROBE)
        self.assertAlmostEqual(L, 10.0, places=3)

    def test_the_same_thing_happens_to_ground(self):
        # Probe port 4 rather than a member, so the probe does not overwrite an
        # element (the DSL is last-assignment-wins).
        base = "1 short_to 2,3,4\n"
        one = _z_of(base + "1 lumped_to_gnd R=50\n4 signal A +\n").real
        three = _z_of(base + "1,2,3 lumped_to_gnd R=50\n4 signal A +\n").real
        self.assertAlmostEqual(one, 1 / (1 / 250 + 1 / 50), places=6)
        self.assertAlmostEqual(three, 1 / (1 / 250 + 3 / 50), places=6)

    def test_inert_lumped_messages_does_not_and_should_not_catch_it(self):
        # The neighbouring check reports elements worth EXACTLY zero.  These are
        # worth N times too much, which is a different verdict and needs its own
        # function -- this pins that the two are not interchangeable.
        ts = parse_custom_termination_text(
            self.SHORT + "1,2,3 lumped_between 4 L=10f\n" + self.PROBE)
        self.assertEqual(inert_lumped_messages(ts), [])


class TestParallelStampMessages(unittest.TestCase):
    """MUTATION: drop the `if n < 2: continue` guard and every ordinary spec
    reports itself; group by node pair without _params_key and an R || L pair
    is refused; use `find` on nothing (no merge view) and the bump array is
    refused."""

    def msgs(self, text: str) -> list:
        return parallel_stamp_messages(parse_custom_termination_text(text))

    # ---- fires exactly when the ports are already one node -----------------
    def test_it_fires_when_the_listed_ports_are_already_one_node(self):
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_between 4 L=10f\n")
        self.assertEqual(len(m), 1)
        self.assertIn("ALREADY ONE NODE", m[0])

    def test_it_is_SILENT_when_the_listed_ports_are_not_merged(self):
        self.assertEqual(self.msgs("1,2,3 lumped_between 4 L=10f\n"), [])

    def test_it_is_silent_for_a_bump_array_to_ground(self):
        # `5-12 lumped_to_gnd R=50` is eight bumps each with its own resistor.
        self.assertEqual(self.msgs("5-12 lumped_to_gnd R=50\n"), [])

    def test_it_fires_for_a_merged_group_to_ground(self):
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_to_gnd R=50\n")
        self.assertEqual(len(m), 1)
        self.assertIn("to GND", m[0])

    def test_a_partly_merged_group_names_only_the_merged_part(self):
        # ports 1,2 tied; port 3 on its own -> two stamps in parallel, one alone
        m = self.msgs("1 short_to 2\n1,2,3 lumped_between 4 L=10f\n")
        self.assertEqual(len(m), 1)
        self.assertIn("ports 1-2 ", m[0])
        self.assertNotIn("1-3", m[0])

    def test_an_R_and_an_L_between_the_same_pair_is_not_a_repeat(self):
        # A deliberate R || L is two DIFFERENT elements, not one stamped twice.
        self.assertEqual(
            self.msgs("1 lumped_between 4 R=50\n1 lumped_between 4 L=1n\n"), [])

    def test_two_rows_with_the_SAME_value_on_one_node_do_fire(self):
        # Same arithmetic as one row listing both ports: two identical elements
        # between the same pair of nodes are 2 in parallel either way.
        m = self.msgs("1 short_to 2\n1 lumped_between 4 R=50\n"
                      "2 lumped_between 4 R=50\n")
        self.assertEqual(len(m), 1)
        self.assertIn("2 times", m[0])

    def test_the_two_ends_are_sorted_by_NODE_not_by_the_slot_they_were_typed_in(self):
        # `1 lumped_between 4` and `4 lumped_between 2` share a node pair but
        # name it in opposite orders.  Taking port_i for both would report
        # "ports 1,4 are ALREADY ONE NODE", which is false -- 1 and 4 are the
        # two ENDS.
        m = self.msgs("1 short_to 2\n1 lumped_between 4 R=50\n"
                      "4 lumped_between 2 R=50\n")
        self.assertEqual(len(m), 1)
        self.assertIn("ports 1-2 ", m[0])
        self.assertIn("to port 4 ", m[0])

    def test_the_same_port_pair_written_twice_is_NOT_called_one_node(self):
        # One port on each side repeated is the same line typed twice, which is
        # visible on its own row; claiming "ports 1 are ALREADY ONE NODE" would
        # be a false statement about a spec with no short in it at all.
        self.assertEqual(
            self.msgs("1 lumped_between 4 R=50\n1 lumped_between 4 R=50\n"), [])
        self.assertEqual(
            self.msgs("1 lumped_between 4 R=50\n4 lumped_between 1 R=50\n"), [])

    def test_the_refusal_does_not_depend_on_the_ports_being_low_numbered(self):
        """
        The merged side is found from the PORT LISTS, never from `lo`/`hi`.

        Those are Union-Find roots -- arbitrary integers whose order falls out
        of which port won its union -- and testing only the `lo` side made the
        refusal depend on the user's port numbering.  Measured on the probe
        network before this was fixed: the low-numbered spelling was refused
        and the high-numbered one, the SAME network with the SAME 3.3333 fH
        against a typed 10 fH, was silent.

        MUTATION: `if len(merged) < 2` -> `if len(lo_ports) < 2` and the
        second subTest goes red while the first stays green.
        """
        for label, text in (
            ("group low  (1,2,3 -> 10)",
             "1,2,3 short\n1,2,3 lumped_between 10 L=10f\n"),
            ("group high (21,22,23 -> 1)",
             "21,22,23 short\n21,22,23 lumped_between 1 L=10f\n"),
        ):
            with self.subTest(label):
                m = self.msgs(text)
                self.assertEqual(len(m), 1)
                self.assertIn("ALREADY ONE NODE", m[0])
                self.assertIn("L 10 fH becomes 3.33 fH", m[0])

    def test_the_merged_side_is_named_whichever_slot_it_was_typed_in(self):
        """`1 lumped_between 21` three times says the same thing as
        `21,22,23 lumped_between 1` -- the group is the group either way."""
        m = self.msgs("21,22,23 short\n1 lumped_between 21 L=10f\n"
                      "1 lumped_between 22 L=10f\n1 lumped_between 23 L=10f\n")
        self.assertEqual(len(m), 1)
        self.assertIn("ports 21-23 are ALREADY ONE NODE", m[0])
        self.assertIn("to port 1 ", m[0])

    def test_the_message_ORDER_is_decided_by_port_numbers(self):
        """
        Two problems, two lines, and VALIDATION_STRIP_LINES is 2 -- so which
        one is first is what gets read.  Sorting the dict keys sorted by
        Union-Find root, which is what this used to do, picks that by
        something no reader can see.

        MUTATION: drop the final `found.sort(...)` and this goes red.
        """
        m = self.msgs("21,22 short\n21,22 lumped_to_gnd R=50\n"
                      "1,2 short\n1,2 lumped_to_gnd R=50\n")
        self.assertEqual(len(m), 2)
        self.assertIn("ports 1-2 ", m[0])
        self.assertIn("ports 21-22 ", m[1])

    def test_an_element_with_BOTH_ends_on_one_node_belongs_to_the_other_check(self):
        # Worth exactly zero, not N times too much: inert_lumped_messages owns
        # it, and two messages about one element would contradict each other.
        # The shape matters -- `1,2 lumped_between 4` with 1, 2 and 4 ALL tied
        # together reaches the multi-port branch (two stamps, two distinct left
        # ports) and is only kept out by the "both ends on one node" guard.
        text = "1 short_to 2,4\n1,2 lumped_between 4 R=50\n"
        ts = parse_custom_termination_text(text)
        self.assertEqual(parallel_stamp_messages(ts), [])
        self.assertEqual(len(inert_lumped_messages(ts)), 2)

    # ---- the number in the message ----------------------------------------
    def test_the_effective_value_is_per_element_type(self):
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_to_gnd R=30 L=3n C=2p\n")
        self.assertEqual(len(m), 1)
        # R and L divide by N; C MULTIPLIES by N.  A single template would get
        # one of the three wrong whichever rule it picked.
        self.assertIn("R 30 Ω becomes 10 Ω", m[0])
        self.assertIn("L 3 nH becomes 1 nH", m[0])
        self.assertIn("C 2 pF becomes 6 pF", m[0])

    def test_a_value_the_user_did_not_type_is_not_reported(self):
        # An omitted R is 0 and an omitted C is inf; neither is a number anybody
        # wrote down, and "R 0 Ω becomes 0 Ω" is noise on a 2-line strip.
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_between 4 L=10f\n")
        self.assertIn("L 10 fH becomes 3.33 fH", m[0])
        self.assertNotIn("R ", m[0])
        self.assertNotIn("C ", m[0])

    def test_the_effective_value_matches_what_the_engine_computes(self):
        # The message is only worth printing if its number is the real one.
        base = "1 short_to 2,3,4\n"
        z = _z_of(base + "1,2,3 lumped_to_gnd R=30\n4 signal A +\n").real
        self.assertAlmostEqual(z, 1 / (1 / 250 + 1 / 10), places=6)   # 30/3
        self.assertIn("R 30 Ω becomes 10 Ω",
                      self.msgs(base + "1,2,3 lumped_to_gnd R=30\n")[0])

    def test_the_message_carries_the_repair(self):
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_between 4 L=10f\n")[0]
        self.assertIn("ONE member port", m)
        self.assertIn(f"'{NET_KEYWORD} <name>'", m)

    def test_it_never_raises_on_a_set_built_in_code(self):
        # `params` is None on a hand-built TerminationSet (the golden capture,
        # the attribution tests).  The check must degrade, not explode.
        from pkg_rlc_core import TerminationSet
        y = y_series_rlc(R=50.0)
        ts = TerminationSet(per_port={0: LumpedToGnd(y), 1: LumpedToGnd(y)},
                            couplings=[ShortPair(0, 1)])
        m = parallel_stamp_messages(ts)
        self.assertEqual(len(m), 1)
        self.assertIn("2 identical elements", m[0])

    def test_messages_are_one_based(self):
        # Port indices are 0-based inside core and 1-based at every boundary a
        # user reads; a message is a boundary.
        m = self.msgs("1 short_to 2,3\n1,2,3 lumped_between 4 L=1n\n")
        self.assertIn("ports 1-3", m[0])
        self.assertIn("port 4", m[0])
        self.assertNotIn("ports 0-2", m[0])


# ============================================================================
# R1-2: net names
# ============================================================================

class TestNetNameRules(unittest.TestCase):
    """MUTATION: drop the parse_port_range check and '1,2' becomes a legal net
    name that nothing can tell from a port list; drop the reserved list and
    'GND' shadows the keyword; drop the character set and 'a-b' parses as a
    range on the next row that uses it."""

    def test_a_plain_name_is_accepted(self):
        for name in ("tap", "coil_tap", "vss_tie", "3v3", "_x", "NetA"):
            validate_net_name(name)     # must not raise

    def test_a_name_parse_port_range_would_accept_is_refused(self):
        # The port field is the one slot where a number and a name share a
        # token, so a numeric name is unresolvable by construction.
        for name in ("1", "1,2", "5-7", "3:1:9", "-4"):
            with self.assertRaises(ValueError, msg=name):
                validate_net_name(name)

    def test_the_reserved_names_are_refused_case_insensitively(self):
        for name in ("A", "B", "GND", "VDD", "a", "b", "gnd", "vdd"):
            with self.assertRaises(ValueError, msg=name):
                validate_net_name(name)

    def test_A_and_B_are_reserved_because_Signal_already_owns_them(self):
        self.assertIn("A", NET_RESERVED_NAMES)
        self.assertIn("B", NET_RESERVED_NAMES)

    def test_the_forbidden_characters_are_refused(self):
        for name in ("a:b", "a,b", "a-b", "a#b", "my net", "a\tb"):
            with self.assertRaises(ValueError, msg=name):
                validate_net_name(name)

    def test_an_empty_name_is_refused(self):
        for name in ("", "   ", None):
            with self.assertRaises(ValueError):
                validate_net_name(name)

    def test_the_colon_is_refused_because_a_range_already_owns_it(self):
        # parse_port_range('PKG:12') raises "Range must be start:step:stop", so
        # a name carrying a colon can never be told from a malformed range.
        with self.assertRaises(ValueError):
            validate_net_name("PKG:12")


class TestNetsInTheDSL(unittest.TestCase):
    """MUTATION: resolve a name to EVERY member instead of one and the sugar
    reintroduces the 3x bug it exists to remove; treat an unknown name as a new
    node and the element hangs off nothing with no symptom."""

    def test_a_name_resolves_to_ONE_member_not_to_the_whole_group(self):
        named = _L_fH("1,2,3 short as tap\ntap lumped_between 4 L=10f\n"
                      "1 signal A +\n4 signal A -\n")
        byhand = _L_fH("1 short_to 2,3\n1 lumped_between 4 L=10f\n"
                       "1 signal A +\n4 signal A -\n")
        self.assertAlmostEqual(named, 10.0, places=3)
        self.assertAlmostEqual(named, byhand, places=9)

    def test_the_named_spec_is_bit_identical_to_the_hand_written_one(self):
        # Sugar means SUGAR: the reduction must see the same numbers.
        a = _z_of("1,2,3 short as tap\ntap lumped_between 4 L=10f\n"
                  "1 signal A +\n4 signal A -\n")
        b = _z_of("1,2,3 short\n1 lumped_between 4 L=10f\n"
                  "1 signal A +\n4 signal A -\n")
        self.assertEqual(a, b)

    def test_a_name_works_in_a_probe_field(self):
        ts = parse_custom_termination_text("tap signal A +\n1,2,3 short as tap\n")
        self.assertIsInstance(ts.per_port[0], Signal)

    def test_a_name_may_be_used_ABOVE_the_row_that_defines_it(self):
        # rows_to_dsl_text emits every measurement port before every connection,
        # so a probe on a named node is ALWAYS a forward reference.
        ts = parse_custom_termination_text("tap ground\n1,2 short as tap\n")
        self.assertIsInstance(ts.per_port[0], Ground)

    def test_a_name_is_matched_case_insensitively_and_stored_as_typed(self):
        ts = parse_custom_termination_text("1,2 short as Tap\nTAP ground\n")
        self.assertIsInstance(ts.per_port[0], Ground)
        rows = dsl_text_to_rows("1,2 short as Tap\n")[1]
        self.assertEqual(rows[0].net, "Tap")

    def test_a_name_may_stand_on_the_right_of_lumped_between(self):
        ts = parse_custom_termination_text(
            "1,2 short as tap\n4 lumped_between tap L=1n\n")
        cpl = [c for c in ts.couplings if isinstance(c, LumpedBetween)]
        self.assertEqual([(c.port_i, c.port_j) for c in cpl], [(3, 0)])

    def test_a_name_may_stand_on_the_right_of_short_to(self):
        ts = parse_custom_termination_text("1,2 short as tap\n5 short_to tap\n")
        self.assertIn(ShortPair(4, 0), ts.couplings)

    def test_a_name_may_be_defined_in_terms_of_another_name(self):
        ts = parse_custom_termination_text(
            "1,2 short as a1\na1 lumped_to_gnd R=9\n")
        self.assertIsInstance(ts.per_port[0], LumpedToGnd)

    # ---- refusals ----------------------------------------------------------
    def test_an_unknown_name_is_a_HARD_refusal_naming_the_defined_nets(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text(
                "1,2 short as tap\nfoo lumped_between 4 L=1n\n")
        self.assertIn("no node is named that", str(cm.exception))
        self.assertIn("tap", str(cm.exception))

    def test_an_unknown_name_with_no_nets_defined_says_how_to_define_one(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("foo lumped_between 4 L=1n\n")
        self.assertIn(NET_KEYWORD, str(cm.exception))

    def test_two_names_on_ONE_merged_node_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1,2 short as a1\n2,3 short as b1\n")
        self.assertIn("same merged node", str(cm.exception))

    def test_two_names_on_two_SEPARATE_nodes_is_fine(self):
        ts = parse_custom_termination_text("1,2 short as a1\n4,5 short as b1\n")
        self.assertEqual(len(ts.couplings), 2)

    def test_the_same_name_twice_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1,2 short as a1\n4,5 short as a1\n")
        self.assertIn("already used", str(cm.exception))

    def test_a_bad_name_is_refused_with_its_line_number(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1 open\n1,2 short as 7\n")
        self.assertIn("Line 2", str(cm.exception))

    def test_as_on_a_row_that_creates_no_node_is_refused(self):
        # ground/vdd/open ignore their tail and parse_kv_rlc_params drops any
        # token without an '=', so the name would vanish in silence and the
        # user would go on referring to a node that was never named.
        for line in ("1,2 ground as foo\n", "1,2 lumped_to_gnd R=50 as foo\n",
                     "1 lumped_between 2 R=50 as foo\n", "1,2 open as foo\n"):
            with self.assertRaises(ValueError, msg=line) as cm:
                parse_custom_termination_text(line)
            self.assertIn("only a short row can name a node", str(cm.exception))

    def test_signal_is_exempt_because_its_first_token_is_a_free_form_name(self):
        ts = parse_custom_termination_text("1 signal as\n")
        self.assertEqual(ts.per_port[0], Signal("as", +1))

    def test_as_with_no_name_or_two_names_is_refused(self):
        for line in ("1,2 short as\n", "1,2 short as a b\n"):
            with self.assertRaises(ValueError, msg=line):
                parse_custom_termination_text(line)

    def test_a_name_whose_row_has_no_port_number_is_refused_by_name(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text(
                "n1 short_to 3 as n2\nn2 short_to 4 as n1\n")
        self.assertIn("has no port number of its own", str(cm.exception))


class TestSingleFieldShort(unittest.TestCase):
    """The one-cell short: `5,6,7,8 short`.  MUTATION: let `short` accept a
    trailing port and the single-field row silently becomes a two-field one."""

    def test_a_bare_short_ties_the_whole_listed_group(self):
        ts = parse_custom_termination_text("1,2,3,4 short\n")
        self.assertEqual([(c.port_i, c.port_j) for c in ts.couplings],
                         [(0, 1), (1, 2), (2, 3)])

    def test_it_is_identical_to_the_two_field_spelling(self):
        one = parse_custom_termination_text("1,2,3,4 short\n")
        two = parse_custom_termination_text("1 short_to 2,3,4\n")
        self.assertEqual([(c.port_i, c.port_j) for c in one.couplings],
                         [(c.port_i, c.port_j) for c in two.couplings])

    def test_a_short_of_one_port_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("5 short\n")
        self.assertIn("at least two ports", str(cm.exception))

    def test_short_with_a_stray_partner_token_is_refused_with_the_repair(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1,2 short 3\n")
        self.assertIn("short_to 3", str(cm.exception))

    def test_the_legacy_two_field_spelling_still_parses_unchanged(self):
        ts = parse_custom_termination_text("45 short_to 46\n")
        self.assertEqual(ts.couplings, [ShortPair(44, 45)])


class TestRowModel(unittest.TestCase):
    """MUTATION: emit the net before the ports, or drop it from
    rows_to_dsl_text, and the name is lost on every save/load cycle; make
    short_group_spec return only `ports` and a legacy row loses its partner."""

    def test_a_short_row_carries_a_net_name(self):
        row = ConnectionRow(kind="short", ports="1,2,3", net="tap")
        self.assertEqual(rows_to_dsl_text((), [row]), "1,2,3 short as tap\n")

    def test_a_short_row_without_a_net_emits_the_bare_form(self):
        row = ConnectionRow(kind="short", ports="1,2,3")
        self.assertEqual(rows_to_dsl_text((), [row]), "1,2,3 short\n")

    def test_a_legacy_two_field_short_row_emits_short_to_unchanged(self):
        # A row saved by a build that predates nets must produce byte-identical
        # DSL text, or its answer moves on load.
        row = ConnectionRow(kind="short", ports="5", to="6,7")
        self.assertEqual(rows_to_dsl_text((), [row]), "5 short_to 6,7\n")

    def test_the_net_field_defaults_to_empty_so_old_sessions_load(self):
        self.assertEqual(ConnectionRow().net, "")
        self.assertEqual(ConnectionRow(kind="short", ports="1,2").net, "")

    def test_a_row_with_only_a_net_name_is_not_blank(self):
        # is_blank() decides whether a row is skipped; a name-only row is a
        # half-typed row, not an empty one.
        self.assertFalse(ConnectionRow(kind="short", net="tap").is_blank())
        self.assertTrue(ConnectionRow(kind="short").is_blank())

    def test_short_group_spec_joins_the_legacy_two_fields(self):
        self.assertEqual(
            short_group_spec(ConnectionRow(kind="short", ports="5", to="6,7")),
            "5,6,7")

    def test_short_group_spec_never_emits_a_space(self):
        # The DSL is whitespace-tokenised and the port field is parts[0], so a
        # space would put a stray token where the keyword belongs.
        spec = short_group_spec(ConnectionRow(kind="short", ports="5", to="6,7"))
        self.assertNotIn(" ", spec)

    def test_short_group_spec_of_a_single_field_row_is_that_field(self):
        self.assertEqual(
            short_group_spec(ConnectionRow(kind="short", ports="5,6,7")), "5,6,7")

    def test_only_short_is_declared_able_to_name_a_node(self):
        self.assertEqual(CONN_KINDS_WITH_NET, ("short",))


class TestRoundTrip(unittest.TestCase):
    """rows -> text -> rows is idempotent, with and without a net."""

    CASES = (
        "1,2,3 short as tap\ntap lumped_between 4 L=10f\n",
        "5 short_to 6,7 as n1\n",
        "1,2,3 short\n",
        "1 short_to 2\n",
        "1,2 short as tap\ntap signal A +\n3 signal A -\n",
    )

    def test_text_to_rows_to_text_is_idempotent(self):
        for text in self.CASES:
            back = rows_to_dsl_text(*dsl_text_to_rows(text))
            again = rows_to_dsl_text(*dsl_text_to_rows(back))
            self.assertEqual(back, again, msg=text)

    def test_the_round_trip_preserves_the_meaning(self):
        for text in self.CASES:
            before = parse_custom_termination_text(text)
            after = parse_custom_termination_text(
                rows_to_dsl_text(*dsl_text_to_rows(text)))
            self.assertEqual(
                [(type(c).__name__, c.port_i, c.port_j) for c in before.couplings],
                [(type(c).__name__, c.port_i, c.port_j) for c in after.couplings],
                msg=text)
            self.assertEqual(
                {p: type(t).__name__ for p, t in before.per_port.items()},
                {p: type(t).__name__ for p, t in after.per_port.items()},
                msg=text)

    def test_the_net_name_survives_the_round_trip(self):
        rows = dsl_text_to_rows("1,2,3 short as tap\n")[1]
        self.assertEqual(rows[0].net, "tap")
        self.assertEqual(rows[0].ports, "1,2,3")
        self.assertEqual(rows[0].to, "")

    def test_a_malformed_as_tail_is_kept_verbatim_rather_than_dropped(self):
        # dsl_text_to_rows must stay TOTAL: nothing the user typed is lost, and
        # the parser -- not this function -- is what complains about it.
        _m, conn, extra = dsl_text_to_rows("1,2 short as a b\n")
        self.assertEqual(conn, [])
        self.assertIn("1,2 short as a b", extra)


class TestMergedNodesAndSources(unittest.TestCase):
    """MUTATION: make merged_nodes' ref the whole group and the editor's
    dropdown starts handing users the exact spelling that multiplies by N; let
    row_sources ignore names and the Ports & Roles 'From' column goes blank for
    every named row."""

    ROWS = [ConnectionRow(kind="short", ports="1,2,3", net="tap"),
            ConnectionRow(kind="rlc_between", ports="tap", to="4", L="10f")]

    def test_a_named_node_is_listed_with_its_name_as_the_reference(self):
        nodes = merged_nodes((), self.ROWS)
        self.assertEqual(nodes, [MergedNode(ports=(1, 2, 3), name="tap",
                                            ref="tap")])

    def test_an_unnamed_node_is_referenced_by_ONE_member_not_by_the_group(self):
        nodes = merged_nodes((), [ConnectionRow(kind="short", ports="1,2,3")])
        self.assertEqual(nodes[0].ref, "1")
        self.assertEqual(nodes[0].ports, (1, 2, 3))

    def test_a_port_that_is_not_merged_is_not_a_node(self):
        self.assertEqual(merged_nodes((), [ConnectionRow(kind="ground",
                                                         ports="1,2,3")]), [])

    def test_merged_nodes_never_raises_on_a_half_typed_row(self):
        self.assertEqual(merged_nodes((), [ConnectionRow(kind="short",
                                                         ports="5:")]), [])

    def test_row_sources_resolves_a_name_to_the_port_the_spec_uses(self):
        src = row_sources((), self.ROWS)
        self.assertEqual(src[1], "conn row 2")     # `tap` -> port 1

    def test_row_sources_never_raises_on_an_unknown_name(self):
        self.assertEqual(
            row_sources((), [ConnectionRow(kind="rlc_gnd", ports="nope",
                                           R="50")]), {})


class TestNothingOldMoved(unittest.TestCase):
    """The specs that existed before nets did must parse to exactly what they
    always did.  MUTATION: any change to the short_to chaining, the
    lumped_between partner rule or the port-field parser turns these red."""

    def test_a_plain_spec_is_unchanged(self):
        ts = parse_custom_termination_text(
            "1 signal A\n2 signal B\n3 ground\n4 vdd\n"
            "5 lumped_to_gnd R=50\n6:1:8 ground\n")
        self.assertEqual(ts.per_port[0], Signal("A", +1))
        self.assertEqual(ts.per_port[1], Signal("B", +1))
        self.assertEqual(ts.per_port[2], Ground())
        self.assertIsInstance(ts.per_port[4], LumpedToGnd)
        self.assertEqual(sorted(ts.per_port), [0, 1, 2, 3, 4, 5, 6, 7])

    def test_lumped_between_still_refuses_a_range_on_its_right(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1 lumped_between 2,3 R=50\n")
        self.assertIn("exactly ONE partner port", str(cm.exception))

    def test_lumped_between_still_takes_a_range_on_its_LEFT(self):
        ts = parse_custom_termination_text("1,2,3 lumped_between 4 R=50\n")
        self.assertEqual(len(ts.couplings), 3)

    def test_a_bad_port_field_still_reports_the_range_error(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("5: ground\n")
        self.assertIn("start:step:stop", str(cm.exception))

    def test_the_element_params_are_metadata_and_do_not_reach_y_func(self):
        ts = parse_custom_termination_text("1 lumped_to_gnd R=50\n")
        term = ts.per_port[0]
        self.assertEqual(term.params["R"], 50.0)
        expect = y_series_rlc(R=50.0)(np.array([_OMEGA]))
        np.testing.assert_array_equal(term.y_func(np.array([_OMEGA])), expect)

    def test_a_lumped_class_still_builds_with_three_positional_args(self):
        # tests/_golden_capture.py and the attribution tests do exactly this.
        y = y_series_rlc(R=1.0)
        self.assertIsNone(LumpedBetween(0, 1, y).params)
        self.assertIsNone(LumpedToGnd(y).params)

    def test_a_measurement_port_row_still_serialises_first(self):
        # The "ground wins" precedence depends on probes being emitted above
        # connections; nets must not have reordered anything.
        text = rows_to_dsl_text([MeasPortRow(name="p", plus="1")],
                                [ConnectionRow(kind="ground", ports="1")])
        self.assertEqual(text, "1 signal p +\n1 ground\n")


if __name__ == "__main__":
    unittest.main()
