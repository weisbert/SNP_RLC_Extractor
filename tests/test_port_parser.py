"""
Tests for parse_port_range and parse_short_pairs in pkg_rlc_core.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make pkg_rlc_core importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pkg_rlc_core import parse_port_range, parse_short_pairs  # noqa: E402


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
        from pkg_rlc_core import (build_terminations_mode3, compute_z,
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


if __name__ == "__main__":
    unittest.main()
