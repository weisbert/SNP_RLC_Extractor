"""
Tests for parse_port_range and parse_short_pairs in pkg_rlc_core, plus the
port-range support in the Mode 5 DSL's leading port field.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make pkg_rlc_core importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pkg_rlc.physics.core import (  # noqa: E402
    Ground,
    LumpedBetween,
    LumpedToGnd,
    Open,
    ShortPair,
    Signal,
    Vdd,
    parse_custom_termination_text,
    parse_port_range,
    parse_short_pairs,
)


class TestParsePortRange(unittest.TestCase):
    def test_single_port(self):
        self.assertEqual(parse_port_range("1"), [1])

    def test_comma_list(self):
        self.assertEqual(parse_port_range("1,3,5"), [1, 3, 5])

    def test_matlab_range(self):
        self.assertEqual(parse_port_range("35:1:45"), list(range(35, 46)))

    def test_matlab_range_step_two(self):
        self.assertEqual(parse_port_range("1:2:9"), [1, 3, 5, 7, 9])

    def test_dash_range(self):
        self.assertEqual(parse_port_range("6-14"), list(range(6, 15)))

    def test_mixed(self):
        expected = [1, 3] + list(range(35, 46)) + list(range(50, 56))
        self.assertEqual(parse_port_range("1,3,35:1:45,50-55"), expected)

    def test_empty(self):
        self.assertEqual(parse_port_range(""), [])

    def test_whitespace_only(self):
        self.assertEqual(parse_port_range("   "), [])

    def test_whitespace_tolerance(self):
        self.assertEqual(parse_port_range(" 1 , 3 , 5 "), [1, 3, 5])
        self.assertEqual(parse_port_range("  6 - 14  "), list(range(6, 15)))
        self.assertEqual(parse_port_range(" 35 : 1 : 45 "), list(range(35, 46)))

    def test_trailing_commas(self):
        self.assertEqual(parse_port_range("1,2,3,"), [1, 2, 3])
        self.assertEqual(parse_port_range(",1,,2,,"), [1, 2])

    def test_dedupe(self):
        self.assertEqual(parse_port_range("1,1,2"), [1, 2])

    def test_dedupe_preserves_order(self):
        self.assertEqual(parse_port_range("3,1,3,2,1"), [3, 1, 2])

    def test_reverse_matlab_range(self):
        self.assertEqual(parse_port_range("5:-1:1"), [5, 4, 3, 2, 1])

    def test_reverse_dash(self):
        self.assertEqual(parse_port_range("5-1"), [5, 4, 3, 2, 1])

    def test_step_zero_raises(self):
        with self.assertRaises(ValueError):
            parse_port_range("1:0:5")

    def test_bad_range_syntax_raises(self):
        with self.assertRaises(ValueError):
            parse_port_range("1:2")

    def test_non_int_raises(self):
        with self.assertRaises(ValueError):
            parse_port_range("a,b,c")


class TestParseShortPairs(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_short_pairs("45-46, 47-48"),
                         [(45, 46), (47, 48)])

    def test_single_pair(self):
        self.assertEqual(parse_short_pairs("1-2"), [(1, 2)])

    def test_whitespace(self):
        self.assertEqual(parse_short_pairs("  45 - 46 ,  47 - 48 "),
                         [(45, 46), (47, 48)])

    def test_empty(self):
        self.assertEqual(parse_short_pairs(""), [])

    def test_whitespace_only(self):
        self.assertEqual(parse_short_pairs("   "), [])

    def test_trailing_commas(self):
        self.assertEqual(parse_short_pairs("45-46, 47-48,"),
                         [(45, 46), (47, 48)])
        self.assertEqual(parse_short_pairs("45-46,,47-48,,"),
                         [(45, 46), (47, 48)])

    def test_no_dash_raises(self):
        with self.assertRaises(ValueError):
            parse_short_pairs("45,46")

    def test_same_port_raises(self):
        with self.assertRaises(ValueError):
            parse_short_pairs("45-45")

    def test_non_int_raises(self):
        with self.assertRaises(ValueError):
            parse_short_pairs("a-b")

    # ---- Multi-port short groups (chained dash syntax) ----

    def test_chain_three(self):
        self.assertEqual(parse_short_pairs("1-2-3"), [(1, 2), (2, 3)])

    def test_chain_four(self):
        self.assertEqual(parse_short_pairs("1-2-3-4"),
                         [(1, 2), (2, 3), (3, 4)])

    def test_mixed_chain_and_pair(self):
        self.assertEqual(parse_short_pairs("1-2-3, 4-5"),
                         [(1, 2), (2, 3), (4, 5)])

    def test_chain_with_whitespace(self):
        self.assertEqual(parse_short_pairs(" 1 - 2 - 3 "),
                         [(1, 2), (2, 3)])

    def test_chain_adjacent_duplicate_raises(self):
        with self.assertRaises(ValueError):
            parse_short_pairs("1-2-2-3")


class TestShortGroupIntegration(unittest.TestCase):
    """Verify chained shorts merge into one group via Union-Find in compute_z."""

    def test_chain_equivalent_to_pairwise(self):
        from pkg_rlc.physics.core import (build_terminations_mode3, compute_z,
                                  parse_touchstone, s_to_y)
        import numpy as np
        fix = (Path(__file__).resolve().parent
               / "fixtures" / "diff_pair_4port.s4p")
        ts = parse_touchstone(fix)
        Y = s_to_y(ts.s, ts.z0)
        # Chain syntax 3-4 (only two ports here, but tests round-trip)
        t_chain = build_terminations_mode3([1], [2], [],
                                           parse_short_pairs("3-4"))
        t_pair = build_terminations_mode3([1], [2], [], [(3, 4)])
        Z_chain, _ = compute_z(Y, ts.freqs, t_chain)
        Z_pair, _ = compute_z(Y, ts.freqs, t_pair)
        self.assertLess(float(np.max(np.abs(Z_chain - Z_pair))), 1e-15)


def parse_short_pairs_as_couplings(spec: str) -> list:
    """parse_short_pairs output (1-based) -> ShortPair list (0-based)."""
    return [ShortPair(a - 1, b - 1) for a, b in parse_short_pairs(spec)]


class TestCustomDSLPortRanges(unittest.TestCase):
    """
    The Mode 5 DSL's leading port field takes the full parse_port_range syntax.

    This is what lets the GUI connection table hold "ports 5-12, ground" as ONE
    row instead of eight identical ones -- a 45-port package otherwise needs a
    row per ground ball, which is the shape that made the old free-text box
    unusable on real files.

    BACKWARD COMPATIBILITY is the load-bearing property here: a single port
    number must parse to exactly what it parsed to before ranges existed, or
    every saved spec and every golden case shifts.  test_single_port_unchanged
    and the golden regression together pin that.
    """

    # ---- backward compatibility -------------------------------------------

    def test_single_port_unchanged(self):
        """One port per line behaves exactly as before ranges were added."""
        ts = parse_custom_termination_text(
            "1 signal A\n2 signal B\n3 ground\n4 open\n"
        )
        self.assertIsInstance(ts.per_port[0], Signal)
        self.assertIsInstance(ts.per_port[1], Signal)
        self.assertIsInstance(ts.per_port[2], Ground)
        self.assertIsInstance(ts.per_port[3], Open)
        self.assertEqual(ts.couplings, [])

    def test_single_port_short_to_emits_one_pair(self):
        """'5 short_to 6' still emits exactly ShortPair(4, 5) and nothing else."""
        ts = parse_custom_termination_text("5 short_to 6\n")
        self.assertEqual(ts.couplings, [ShortPair(4, 5)])

    # ---- ranges on the left-hand port field --------------------------------

    def test_range_ground(self):
        """'5:1:8 ground' grounds every port in the range, 1-based -> 0-based."""
        ts = parse_custom_termination_text("5:1:8 ground\n")
        self.assertEqual(sorted(ts.per_port), [4, 5, 6, 7])
        for p in (4, 5, 6, 7):
            self.assertIsInstance(ts.per_port[p], Ground)

    def test_dash_range_and_comma_list(self):
        ts = parse_custom_termination_text("6-9 ground\n1,3 vdd\n")
        self.assertEqual(sorted(p for p in ts.per_port
                                if isinstance(ts.per_port[p], Ground)),
                         [5, 6, 7, 8])
        self.assertEqual(sorted(p for p in ts.per_port
                                if isinstance(ts.per_port[p], Vdd)),
                         [0, 2])

    def test_range_signal_ties_whole_group_to_one_probe(self):
        """A shield tapped at 4..7 is one probe side, not four groups."""
        ts = parse_custom_termination_text("1 signal tank +\n4-7 signal tank -\n")
        minus = [p for p, t in ts.per_port.items()
                 if isinstance(t, Signal) and t.sign < 0]
        self.assertEqual(sorted(minus), [3, 4, 5, 6])
        for p in minus:
            self.assertEqual(ts.per_port[p].group, "tank")

    def test_range_lumped_to_gnd_shares_one_yfunc(self):
        """Each port gets its own termination; the y_func object is shared."""
        ts = parse_custom_termination_text("2-4 lumped_to_gnd R=50\n")
        terms = [ts.per_port[p] for p in (1, 2, 3)]
        for t in terms:
            self.assertIsInstance(t, LumpedToGnd)
        self.assertIs(terms[0].y_func, terms[1].y_func)
        self.assertIs(terms[1].y_func, terms[2].y_func)

    # ---- short_to takes a range on BOTH sides ------------------------------

    def test_short_to_range_chains_one_node(self):
        """
        '1 short_to 2,3' ties 1-2-3 into a single node, spelled as the chained
        binary pairs parse_short_pairs also emits for '1-2-3'.
        """
        ts = parse_custom_termination_text("1 short_to 2,3\n")
        self.assertEqual(ts.couplings, [ShortPair(0, 1), ShortPair(1, 2)])
        self.assertEqual(ts.couplings, parse_short_pairs_as_couplings("1-2-3"))

    def test_short_to_dedupes_overlapping_sides(self):
        """A port named on both sides must not produce a self-short."""
        ts = parse_custom_termination_text("1,2 short_to 2,3\n")
        self.assertEqual(ts.couplings, [ShortPair(0, 1), ShortPair(1, 2)])
        for c in ts.couplings:
            self.assertNotEqual(c.port_i, c.port_j)

    # ---- lumped_between deliberately refuses a range on the right ----------

    def test_lumped_between_rejects_range_partner(self):
        """N-to-M lumped elements are ambiguous (star? mesh?) -- refuse them."""
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("1 lumped_between 2,3 R=1\n")
        self.assertIn("exactly ONE partner", str(cm.exception))

    def test_lumped_between_allows_range_on_the_left(self):
        """N-to-one IS unambiguous: one element from each listed port."""
        ts = parse_custom_termination_text("1-3 lumped_between 4 R=1\n")
        self.assertEqual([(c.port_i, c.port_j) for c in ts.couplings],
                         [(0, 3), (1, 3), (2, 3)])
        for c in ts.couplings:
            self.assertIsInstance(c, LumpedBetween)

    # ---- error messages ----------------------------------------------------

    def test_bad_port_token_names_the_syntax(self):
        with self.assertRaises(ValueError) as cm:
            parse_custom_termination_text("abc ground\n")
        msg = str(cm.exception)
        self.assertIn("Line 1", msg)
        self.assertIn("port number or range", msg)

    def test_zero_and_negative_ports_still_rejected(self):
        for spec in ("0 ground\n", "-3 ground\n", "0-2 ground\n"):
            with self.assertRaises(ValueError, msg=f"accepted {spec!r}"):
                parse_custom_termination_text(spec)


if __name__ == "__main__":
    unittest.main()
