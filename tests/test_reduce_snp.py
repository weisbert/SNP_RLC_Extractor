"""
Tests for reduce_snp.py -- the standalone Touchstone port-reduction tool.

Reduction results are checked against independent references (physical limits,
sub-matrix identities), and file I/O is checked by reading the output back with
`pkg_rlc_core.parse_touchstone`, which is a completely separate parser.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import reduce_snp as rs  # noqa: E402
from pkg_rlc_core import parse_touchstone as core_parse  # noqa: E402

Z0 = 50.0


def make_network(n_ports, n_freq=7, seed=0, reciprocal=True):
    """A random, well-conditioned Y network -> S, shape (n_freq, n, n)."""
    rng = np.random.default_rng(seed)
    S = np.empty((n_freq, n_ports, n_ports), dtype=complex)
    for f in range(n_freq):
        A = rng.normal(size=(n_ports, n_ports)) + 1j * rng.normal(size=(n_ports, n_ports))
        Y = A * 0.01
        if reciprocal:
            Y = 0.5 * (Y + Y.T)
        Y = Y + np.eye(n_ports) * (0.05 + 0.01 * f)   # keep it invertible
        S[f] = rs.y_to_s(Y[None, :, :], Z0)[0]
    return S


def schur_open(Y, keep, unused):
    """Reference open-circuit elimination for a single Y matrix."""
    return (Y[np.ix_(keep, keep)]
            - Y[np.ix_(keep, unused)]
            @ np.linalg.solve(Y[np.ix_(unused, unused)], Y[np.ix_(unused, keep)]))


class TestPortConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, text):
        p = self.tmp / "ports.txt"
        p.write_text(text)
        return rs.parse_port_config(p)

    def test_groups_and_separators(self):
        groups = self._cfg("# DTC\n11, 141 70;71\n# RX\n26 77\n")
        self.assertEqual(list(groups), ["DTC", "RX"])
        self.assertEqual(groups["DTC"], ["11", "141", "70", "71"])
        self.assertEqual(groups["RX"], ["26", "77"])

    def test_ungrouped_and_blank_lines(self):
        groups = self._cfg("5\n\n# A\n6\n")
        self.assertEqual(groups["Ungrouped"], ["5"])
        self.assertEqual(groups["A"], ["6"])

    def test_trailing_comment_stripped(self):
        groups = self._cfg("# A\n1 2 ! keep these\n")
        self.assertEqual(groups["A"], ["1", "2"])

    def test_gnd_group_is_split_out(self):
        groups = self._cfg("# GND\n3 4\n# SIG\n1 2\n")
        keep_groups, keep, gnd = rs.resolve_port_config(groups, 6, [""] * 6)
        self.assertEqual(keep, [1, 2])
        self.assertEqual(gnd, [3, 4])
        self.assertEqual(list(keep_groups), ["SIG"])

    def test_gnd_aliases_are_case_insensitive(self):
        for alias in ("gnd", "Ground", "AGND", "dgnd"):
            groups = self._cfg(f"# {alias}\n3\n# SIG\n1\n")
            _, keep, gnd = rs.resolve_port_config(groups, 4, [""] * 4)
            self.assertEqual((keep, gnd), ([1], [3]), msg=alias)

    def test_short_is_no_longer_a_silent_alias_for_ground(self):
        # `# SHORT` grounded its ports. It is the word for "tie these pins to
        # each other", and reading it as "tie them to the reference node" is a
        # plausible wrong answer that raises nothing -- so it names both routes.
        for alias in ("SHORT", "shorted", "Shorts"):
            with self.assertRaises(SystemExit) as cm:
                rs.resolve_port_config(self._cfg(f"# {alias}\n3\n# SIG\n1\n"),
                                       4, [""] * 4)
            msg = str(cm.exception)
            self.assertIn("# GND", msg, msg=alias)
            self.assertIn("TIE:", msg, msg=alias)

    def test_order_sorted_vs_config(self):
        groups = self._cfg("# B\n5 3\n# A\n1\n")
        _, keep_sorted, _ = rs.resolve_port_config(groups, 6, [""] * 6, order="sorted")
        _, keep_config, _ = rs.resolve_port_config(groups, 6, [""] * 6, order="config")
        self.assertEqual(keep_sorted, [1, 3, 5])
        self.assertEqual(keep_config, [5, 3, 1])

    def test_duplicate_ports_deduplicated(self):
        groups = self._cfg("# A\n1 2\n# B\n2 3\n")
        _, keep, _ = rs.resolve_port_config(groups, 4, [""] * 4)
        self.assertEqual(keep, [1, 2, 3])

    def test_port_names_resolve(self):
        names = ["in_p", "in_n", "VDD_RX_1", "gnd_ball"]
        groups = self._cfg("# RX\nVDD_RX_1\n# IN\nin_p, in_n\n")
        _, keep, _ = rs.resolve_port_config(groups, 4, names)
        self.assertEqual(keep, [1, 2, 3])

    def test_ambiguous_port_name_errors(self):
        names = ["vdd_a", "vdd_b", "x", "y"]
        groups = self._cfg("# A\nvdd\n")
        with self.assertRaises(SystemExit):
            rs.resolve_port_config(groups, 4, names)

    def test_unknown_token_errors(self):
        groups = self._cfg("# A\nnot_a_port\n")
        with self.assertRaises(SystemExit):
            rs.resolve_port_config(groups, 4, [""] * 4)

    def test_out_of_range_port_errors(self):
        groups = self._cfg("# A\n99\n")
        with self.assertRaises(SystemExit):
            rs.resolve_port_config(groups, 4, [""] * 4)

    def test_keep_and_gnd_clash_errors(self):
        groups = self._cfg("# GND\n2\n# SIG\n1 2\n")
        with self.assertRaises(SystemExit):
            rs.resolve_port_config(groups, 4, [""] * 4)


class TestPortRanges(unittest.TestCase):
    """`1,2,3, 4:1:17, 80` and `6-14` in a port config."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, text, n_ports=20, names=None):
        p = self.tmp / "ports.txt"
        p.write_text(text)
        groups = rs.parse_port_config(p)
        return rs.resolve_port_config(groups, n_ports, names or [""] * n_ports)

    def test_colon_range_expands_inclusive(self):
        _, keep, _ = self._resolve("# A\n1,2,3, 4:1:17, 80\n", n_ports=100)
        self.assertEqual(keep, [1, 2, 3] + list(range(4, 18)) + [80])

    def test_colon_range_honours_step(self):
        _, keep, _ = self._resolve("# A\n1:3:10\n")
        self.assertEqual(keep, [1, 4, 7, 10])

    def test_negative_step_counts_down(self):
        _, keep, _ = self._resolve("# A\n9:-2:3\n", n_ports=10)
        self.assertEqual(keep, [3, 5, 7, 9])          # sorted output order

    def test_dash_range_expands_inclusive(self):
        _, keep, _ = self._resolve("# A\n6-9\n")
        self.assertEqual(keep, [6, 7, 8, 9])

    def test_spaces_around_colons_still_one_range(self):
        _, keep, _ = self._resolve("# A\n4 : 1 : 7\n")
        self.assertEqual(keep, [4, 5, 6, 7])

    def test_range_in_gnd_group(self):
        _, keep, gnd = self._resolve("# GND\n10:1:14\n# SIG\n1 2\n")
        self.assertEqual((keep, gnd), ([1, 2], [10, 11, 12, 13, 14]))

    def test_overlapping_ranges_deduplicate(self):
        keep_groups, keep, _ = self._resolve("# A\n1-5, 3:1:7\n")
        self.assertEqual(keep, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(keep_groups["A"], [1, 2, 3, 4, 5, 6, 7])

    # --- the collision cases: a name is not a range -------------------------
    def test_port_name_with_dash_is_not_a_range(self):
        names = ["VDD-1", "VDD-2", "x", "y"]
        _, keep, _ = self._resolve("# A\nVDD-2\n", n_ports=4, names=names)
        self.assertEqual(keep, [2])

    def test_port_name_with_colon_is_not_a_range(self):
        names = ["I0:VDD", "b", "c", "d"]
        _, keep, _ = self._resolve("# A\nI0:VDD\n", n_ports=4, names=names)
        self.assertEqual(keep, [1])

    def test_range_shadowing_an_exact_port_name_is_refused(self):
        names = ["a", "b", "2-3", "d"]
        with self.assertRaises(SystemExit) as cm:
            self._resolve("# A\n2-3\n", n_ports=4, names=names)
        self.assertIn("both a port range and", str(cm.exception))

    # --- refusals -----------------------------------------------------------
    def test_empty_range_is_refused_not_silently_dropped(self):
        with self.assertRaises(SystemExit) as cm:
            self._resolve("# A\n17:1:4\n", n_ports=20)
        self.assertIn("expands to no ports", str(cm.exception))

    def test_zero_step_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self._resolve("# A\n1:0:5\n")
        self.assertIn("step cannot be zero", str(cm.exception))

    def test_out_of_range_message_names_the_range(self):
        with self.assertRaises(SystemExit) as cm:
            self._resolve("# A\n1:1:99\n", n_ports=20)
        msg = str(cm.exception)
        self.assertIn("1:1:99", msg)
        self.assertIn("port 21", msg)

    def test_expand_port_range_returns_none_for_a_non_range(self):
        for tok in ("VDD_RX_1", "VDD-A", "A-1", "1-", "1:2", "1:2:3:4", ""):
            self.assertIsNone(rs.expand_port_range(tok), msg=tok)

    def test_fmt_ports_collapses_runs(self):
        self.assertEqual(rs._fmt_ports([1, 2, 3, 7, 9, 10]), "1-3, 7, 9-10")
        self.assertEqual(rs._fmt_ports([5]), "5")
        self.assertEqual(rs._fmt_ports([]), "(none)")


class TestConfigFileIsReadAsWritten(unittest.TestCase):
    """
    A hand-written config file whose ports LOOK right must BE right.

    Every case here rendered as `31:1:52` in an editor and was refused as
    "neither an integer, a port range, nor a known port name": the encoding
    Notepad picked, the BOM it wrote, and the full-width punctuation a CJK input
    method produces are all invisible on screen. That is what made the report
    unactionable -- the user is looking at a line that is already correct.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, text, encoding="utf-8", n_ports=60, names=None):
        p = self.tmp / "ports.txt"
        p.write_bytes(text.encode(encoding))         # never write_text: it uses
        groups = rs.parse_port_config(p)             # the platform's codepage
        return rs.resolve_port_config(groups, n_ports, names or [""] * n_ports)

    CFG = "# GND\n31:1:52\n# RX\n1,2\n"

    def _assert_read(self, text, encoding="utf-8"):
        _, keep, gnd = self._resolve(text, encoding)
        self.assertEqual(keep, [1, 2])
        self.assertEqual(gnd, list(range(31, 53)))

    # --- the encoding a text editor chose -----------------------------------
    def test_plain_utf8(self):
        self._assert_read(self.CFG)

    def test_utf8_with_a_bom_does_not_swallow_the_first_group_header(self):
        # The BOM glued itself to the leading '#', so `# GND` parsed as data and
        # every ground ball landed in the keep group.
        self._assert_read(self.CFG, "utf-8-sig")

    def test_utf16_le_with_a_bom(self):
        self._assert_read(self.CFG, "utf-16")

    def test_utf16_be_with_a_bom(self):
        self._assert_read(self.CFG, "utf-16-be")

    def test_utf16_without_a_bom(self):
        self._assert_read(self.CFG, "utf-16-le")

    def test_a_gbk_comment_does_not_take_the_ports_with_it(self):
        self._assert_read("# GND\n31:1:52  # 接地球\n# RX\n1,2\n", "gbk")

    # --- the punctuation a CJK input method produced -------------------------
    def test_fullwidth_colon_is_a_colon(self):
        self._assert_read("# GND\n31：1：52\n# RX\n1,2\n")

    def test_fullwidth_comma_separates_tokens(self):
        _, keep, gnd = self._resolve("# GND\n31:1:52，55\n# RX\n1，2\n")
        self.assertEqual(keep, [1, 2])
        self.assertEqual(gnd, list(range(31, 53)) + [55])

    def test_fullwidth_dash_range(self):
        _, _, gnd = self._resolve("# GND\n31－52\n# RX\n1,2\n")
        self.assertEqual(gnd, list(range(31, 53)))

    # These two need no entry in `_FULLWIDTH_MAP` -- `\d`, `\s` and `int()` are
    # Unicode-aware. They are here so that narrowing a regex to `[0-9]` or to
    # `[ \t]` shows up as a failure rather than as a refused config file.
    def test_fullwidth_digits_are_digits(self):
        self._assert_read("# GND\n３１:1:５２\n# RX\n1,2\n")

    def test_an_ideographic_space_separates_tokens(self):
        _, keep, _ = self._resolve("# RX\n1　2\n")
        self.assertEqual(keep, [1, 2])

    # --- comments ------------------------------------------------------------
    def test_a_hash_after_the_ports_is_a_comment(self):
        self._assert_read("# GND\n31:1:52  # ground balls\n# RX\n1,2\n")

    def test_a_hash_inside_a_port_name_is_not_a_comment(self):
        # Two names sharing the head, or truncating `NET#3` to `NET` still
        # resolves through the substring fallback and the test proves nothing.
        names = [""] * 60
        names[6], names[7] = "NET#3", "NET#4"
        _, keep, _ = self._resolve("# RX\nNET#3\n", names=names)
        self.assertEqual(keep, [7])

    def test_a_bang_comment_still_works(self):
        self._assert_read("# GND\n31:1:52  ! ground balls\n# RX\n1,2\n")

    def test_a_comment_on_the_group_header_line(self):
        groups = rs.parse_port_config(self._write("# GND  ! the balls\n31:1:52\n"))
        self.assertEqual(list(groups), ["GND"])

    def _write(self, text, encoding="utf-8"):
        p = self.tmp / "hdr.txt"
        p.write_bytes(text.encode(encoding))
        return p

    # --- what it still refuses, and what it now says -------------------------
    def test_a_two_part_colon_range_is_refused_with_the_spelling_to_use(self):
        with self.assertRaises(SystemExit) as cm:
            self._resolve("# GND\n31:52\n")
        msg = str(cm.exception)
        self.assertIn("start:step:stop", msg)
        self.assertIn("'31:1:52'", msg)              # the exact fix, not a rule
        self.assertIn("'31-52'", msg)

    def test_a_descending_two_part_range_suggests_a_negative_step(self):
        self.assertIn("'52:-1:31'", rs.describe_bad_token("52:31"))

    def test_an_unmarked_comment_is_named_by_code_point(self):
        msg = rs.describe_bad_token("接地")
        self.assertIn("U+63A5", msg)                 # invisible on screen; the
        self.assertIn("'#' or '!'", msg)             # code point is not

    def test_a_leftover_exotic_character_says_which_one(self):
        msg = rs.describe_bad_token("31–1–52")   # en dashes
        self.assertIn("U+2013", msg)
        self.assertIn("ASCII", msg)

    def test_the_plain_message_survives_for_a_plain_typo(self):
        self.assertIn("neither an integer", rs.describe_bad_token("VDD_RXX"))


def stamp(Y, p, q, y):
    """Series admittance between nodes p,q on a stacked Y (q=None -> reference)."""
    if q is None:
        Y[:, p, p] += y
    else:
        Y[:, p, p] += y
        Y[:, q, q] += y
        Y[:, p, q] -= y
        Y[:, q, p] -= y


def tie_demo_network(n_freq=5):
    """
    Port1 --C-- port3   port4 --C-- port2,  every port lightly shunted.

    The ONLY path from port 1 to port 2 runs through pins 3 and 4, so opening
    them one by one and tying them together are visibly different answers --
    which is the whole point of the feature and is what makes the fixture worth
    constructing rather than reusing a random network.
    """
    w = 2 * np.pi * np.linspace(1e9, 2e9, n_freq)
    Y = np.zeros((n_freq, 4, 4), dtype=complex)
    stamp(Y, 0, 2, 1j * w * 1e-12)
    stamp(Y, 3, 1, 1j * w * 1e-12)
    for p in range(4):
        stamp(Y, p, None, 1j * w * 0.01e-12)
    return rs.y_to_s(Y, Z0), w


class TestTiedPorts(unittest.TestCase):
    """
    `# TIE:<name>` -- pins shorted TO EACH OTHER, the node then floating.

    Open means I = 0 at each pin separately; a floating wire means the pins
    share one voltage and their currents sum to zero. Both are ordinary
    reductions that raise nothing, so the only thing that can tell them apart
    is a number.
    """

    def test_the_merge_agrees_with_a_network_rebuilt_with_one_node(self):
        # The honest reference: build the SAME circuit with pins 3 and 4 as one
        # node from the start, and eliminate that node as an ordinary open.
        # Checking the merge against itself would pass with T transposed wrong.
        S, w = tie_demo_network()
        got = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3]])

        Yh = np.zeros((len(w), 3, 3), dtype=complex)
        stamp(Yh, 0, 2, 1j * w * 1e-12)
        stamp(Yh, 2, 1, 1j * w * 1e-12)
        for p in (0, 1):
            stamp(Yh, p, None, 1j * w * 0.01e-12)
        stamp(Yh, 2, None, 2 * 1j * w * 0.01e-12)      # two pins' worth of shunt
        want = rs.reduce_block(rs.y_to_s(Yh, Z0), Z0, [0, 1], [], "open")

        np.testing.assert_allclose(got, want, rtol=1e-10, atol=1e-14)

    def test_tying_is_not_the_same_as_opening_each(self):
        S, _ = tie_demo_network()
        opened = rs.reduce_block(S, Z0, [0, 1], [], "open")
        tied = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3]])
        self.assertLess(abs(opened[0, 1, 0]), 1e-15)          # no path at all
        self.assertGreater(abs(tied[0, 1, 0]), 0.1)           # -10.6 dB of path

    def test_tying_is_not_the_same_as_grounding_each(self):
        S, _ = tie_demo_network()
        grounded = rs.reduce_block(S, Z0, [0, 1], [2, 3], "open")
        tied = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3]])
        self.assertGreater(abs(tied[0, 1, 0] - grounded[0, 1, 0]), 0.1)

    def test_a_tie_group_of_one_changes_nothing(self):
        S, _ = tie_demo_network()
        plain = rs.reduce_block(S, Z0, [0, 1], [], "open")
        got = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2]])
        np.testing.assert_array_equal(got, plain)

    def test_no_tie_is_bit_identical_to_the_old_path(self):
        # The merge is an identity when nothing is tied, and `matched` moved its
        # Y0 stamp from Y_uu to the original diagonal -- neither may move a bit.
        S = make_network(6, seed=3)
        for method in ("open", "matched"):
            a = rs.reduce_block(S, Z0, [0, 1], [4], method)
            b = rs.reduce_block(S, Z0, [0, 1], [4], method, tie_0idx=[])
            np.testing.assert_array_equal(a, b, err_msg=method)

    def test_a_tie_is_transitive_across_groups(self):
        # Two wires sharing a pin are one node -- 1-2 and 2-3 means 1-2-3.
        S = make_network(6, seed=5)
        chained = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3], [3, 4]])
        one = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3, 4]])
        np.testing.assert_allclose(chained, one, rtol=1e-12)

    def test_a_tied_node_can_be_kept_as_one_output_port(self):
        # Keeping any one member keeps the whole node, as ONE port.
        S = make_network(6, seed=7)
        got = rs.reduce_block(S, Z0, [0, 1, 2], [], "open", tie_0idx=[[2, 3, 4]])
        self.assertEqual(got.shape[-1], 3)

        Y = rs.s_to_y(S, Z0)
        node_of, n_nodes = rs.merge_node_index(6, [[2, 3, 4]])
        Ym = rs.merge_tied_nodes(Y, node_of, n_nodes)
        keep = [node_of[p] for p in (0, 1, 2)]
        unused = [i for i in range(n_nodes) if i not in keep]
        want = rs.y_to_s(np.stack([schur_open(Ym[f], keep, unused)
                                   for f in range(Ym.shape[0])]), Z0)
        np.testing.assert_allclose(got, want, rtol=1e-10)

    def test_a_tied_node_touching_gnd_is_grounded_whole(self):
        # Grounding one pin of a wire grounds every pin on it.
        S = make_network(6, seed=9)
        got = rs.reduce_block(S, Z0, [0, 1], [2], "open", tie_0idx=[[2, 3, 4]])
        want = rs.reduce_block(S, Z0, [0, 1], [2, 3, 4], "open")
        np.testing.assert_allclose(got, want, rtol=1e-10)

    def test_matched_terminates_each_PIN_not_the_node(self):
        # Four tied pins each with their own 50 ohm load is 12.5 ohm on the
        # node. Stamping after the merge would put 50 ohm there instead.
        S = make_network(6, seed=11)
        got = rs.reduce_block(S, Z0, [0, 1], [], "matched", tie_0idx=[[2, 3, 4, 5]])
        Y = rs.s_to_y(S, Z0)
        Y[:, [2, 3, 4, 5], [2, 3, 4, 5]] += 1.0 / Z0        # per pin, then wire
        node_of, n_nodes = rs.merge_node_index(6, [[2, 3, 4, 5]])
        Ym = rs.merge_tied_nodes(Y, node_of, n_nodes)
        keep, unused = [0, 1], [2]
        want = rs.y_to_s(np.stack([schur_open(Ym[f], keep, unused)
                                   for f in range(Ym.shape[0])]), Z0)
        np.testing.assert_allclose(got, want, rtol=1e-10)

    def test_matched_with_a_tie_does_not_take_the_submatrix_fast_path(self):
        # `matched` with no GND is the plain S sub-matrix -- but a wire changes
        # the network, so that shortcut is wrong the moment a tie exists.
        S = make_network(6, seed=13)
        sub = S[:, [0, 1]][:, :, [0, 1]]
        got = rs.reduce_block(S, Z0, [0, 1], [], "matched", tie_0idx=[[2, 3]])
        self.assertGreater(np.abs(got - sub).max(), 1e-6)

    def test_merge_is_a_congruence_so_passivity_survives(self):
        S, _ = tie_demo_network(n_freq=9)
        tied = rs.reduce_block(S, Z0, [0, 1], [], "open", tie_0idx=[[2, 3]])
        self.assertLessEqual(np.linalg.svd(tied, compute_uv=False).max(), 1.0 + 1e-9)

    def test_merge_node_index_is_the_identity_with_no_ties(self):
        node_of, n = rs.merge_node_index(5, [])
        self.assertEqual((node_of, n), ([0, 1, 2, 3, 4], 5))

    def test_merge_node_index_renumbers_the_survivors_in_order(self):
        node_of, n = rs.merge_node_index(5, [[1, 3]])
        self.assertEqual((node_of, n), ([0, 1, 2, 1, 3], 4))


class TestTieConfig(unittest.TestCase):
    """The `# TIE:<name>` group, and what it refuses."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _both(self, text, n_ports=60, names=None):
        p = self.tmp / "ports.txt"
        p.write_bytes(text.encode("utf-8"))
        groups = rs.parse_port_config(p)
        names = names or [""] * n_ports
        ties = rs.resolve_tie_config(groups, n_ports, names)
        keep_groups, keep, gnd = rs.resolve_port_config(
            groups, n_ports, names, tie_groups=ties)
        return ties, keep_groups, keep, gnd

    def test_a_tie_group_is_not_a_keep_group(self):
        ties, keep_groups, keep, gnd = self._both(
            "# RX\n1,2\n# TIE:shield\n23,24,25\n")
        self.assertEqual(dict(ties), {"shield": [23, 24, 25]})
        self.assertEqual(list(keep_groups), ["RX"])      # not "TIE:shield"
        self.assertEqual((keep, gnd), ([1, 2], []))

    def test_the_label_carries_through_from_the_header(self):
        ties, *_ = self._both("# RX\n1\n# TIE:vss_island\n40:1:52\n")
        self.assertEqual(list(ties), ["vss_island"])
        self.assertEqual(ties["vss_island"], list(range(40, 53)))

    def test_a_tie_group_takes_names_and_ranges_like_every_other_group(self):
        names = [""] * 60
        names[22], names[23] = "SHIELD_A", "SHIELD_B"
        ties, *_ = self._both("# RX\n1\n# TIE:s\nSHIELD_A, SHIELD_B\n", names=names)
        self.assertEqual(ties["s"], [23, 24])

    def test_an_unnamed_tie_group_is_refused(self):
        # Two `# TIE` headers merge into one OrderedDict entry, i.e. two wires
        # drawn separately would silently become one node.
        with self.assertRaises(SystemExit) as cm:
            self._both("# RX\n1\n# TIE\n23,24\n")
        self.assertIn("must be named", str(cm.exception))

    def test_two_output_ports_tied_together_are_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self._both("# RX\n1,2\n# TIE:w\n1,2\n")
        msg = str(cm.exception)
        self.assertIn("one node can only be one port", msg)
        self.assertIn("1", msg)

    def test_a_kept_port_tied_to_ground_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self._both("# RX\n1\n# GND\n31\n# TIE:w\n1,31\n")
        self.assertIn("grounds it", str(cm.exception))

    def test_a_tie_label_defined_twice_is_refused(self):
        p = self.tmp / "ports.txt"
        p.write_bytes(b"# RX\n1\n# TIE:w\n23,24\n")
        groups = rs.parse_port_config(p)
        groups["TIE: w"] = ["25", "26"]          # same label, different spelling
        with self.assertRaises(SystemExit) as cm:
            rs.resolve_tie_config(groups, 60, [""] * 60)
        self.assertIn("defined twice", str(cm.exception))

    def test_tying_two_grounded_ports_is_fine(self):
        ties, _, keep, gnd = self._both("# RX\n1\n# GND\n31,32\n# TIE:w\n31,32\n")
        self.assertEqual((keep, gnd), ([1], [31, 32]))

    # --- the fate of a node, which the report and the console both read ------
    def test_fate_open(self):
        fates = rs.tie_node_fates(OrderedDict([("s", [23, 24])]), [1, 2], [], 60)
        self.assertEqual(fates, [("s", [23, 24], "open", None)])

    def test_fate_keep_names_the_port_that_keeps_it(self):
        fates = rs.tie_node_fates(OrderedDict([("s", [23, 24])]), [1, 24], [], 60)
        self.assertEqual(fates, [("s", [23, 24], "keep", 24)])

    def test_fate_gnd_wins_over_keep(self):
        # Ground beats a probe everywhere else in this repo; a wire is no
        # exception -- and `_validate_ties` refuses that spec before it runs.
        fates = rs.tie_node_fates(OrderedDict([("s", [23, 24])]), [24], [23], 60)
        self.assertEqual(fates[0][2], "gnd")

    def test_a_fate_follows_transitivity_not_the_group_it_was_typed_in(self):
        ties = OrderedDict([("a", [23, 24]), ("b", [24, 25])])
        fates = rs.tie_node_fates(ties, [25], [], 60)
        self.assertEqual([f[2] for f in fates], ["keep", "keep"])

    # --- the inline flag -----------------------------------------------------
    def test_tie_flag_builds_a_tie_group(self):
        groups = rs.groups_from_cli(["1,2"], None, ["shield=23,24,25"])
        self.assertEqual(groups["TIE:shield"], ["23", "24", "25"])

    def test_two_bare_tie_flags_stay_two_wires(self):
        groups = rs.groups_from_cli(["1,2"], None, ["23,24", "40,41"])
        self.assertEqual(list(groups), ["Keep1", "TIE:Tie1", "TIE:Tie2"])

    def test_tie_flag_with_no_ports_is_refused(self):
        with self.assertRaises(SystemExit):
            rs.groups_from_cli(["1,2"], None, ["  "])

    def test_a_keep_group_may_not_be_called_TIE(self):
        with self.assertRaises(SystemExit) as cm:
            rs.groups_from_cli(["TIE:x=1,2"], None, None)
        self.assertIn("--tie", str(cm.exception))


class TestInlinePortSpecs(unittest.TestCase):
    """`--keep` / `--gnd` build the same group mapping a config file does."""

    def test_keep_and_gnd(self):
        groups = rs.groups_from_cli(["1,2,3, 4:1:6"], ["10-12"])
        self.assertEqual(groups["Keep1"], ["1", "2", "3", "4:1:6"])
        self.assertEqual(groups["GND"], ["10-12"])

    def test_repeated_keep_makes_separate_groups(self):
        groups = rs.groups_from_cli(["RX=1,2", "3,4"], None)
        self.assertEqual(list(groups), ["RX", "Keep2"])

    def test_reserved_ground_name_on_keep_is_refused(self):
        with self.assertRaises(SystemExit):
            rs.groups_from_cli(["GND=1,2"], None)

    def test_empty_spec_is_refused(self):
        with self.assertRaises(SystemExit):
            rs.groups_from_cli(["  "], None)

    def test_resolves_through_the_same_path_as_a_file(self):
        groups = rs.groups_from_cli(["1,2", "5:1:7"], ["10-11"])
        _, keep, gnd = rs.resolve_port_config(groups, 20, [""] * 20)
        self.assertEqual((keep, gnd), ([1, 2, 5, 6, 7], [10, 11]))


class TestReduction(unittest.TestCase):
    def test_open_matches_hand_coded_schur(self):
        S = make_network(5, seed=1)
        got = rs.reduce_block(S, Z0, [0, 1, 2], [], "open")
        Y = rs.s_to_y(S, Z0)
        for f in range(S.shape[0]):
            ref = rs.y_to_s(schur_open(Y[f], [0, 1, 2], [3, 4])[None], Z0)[0]
            np.testing.assert_allclose(got[f], ref, rtol=1e-10, atol=1e-12)

    def test_matched_equals_submatrix(self):
        """Z0-terminating the unused ports must equal plain sub-matrix extraction."""
        S = make_network(5, seed=2)
        keep, unused = [0, 2], [1, 3, 4]
        got = rs.reduce_block(S, Z0, keep, [], "matched")
        Y = rs.s_to_y(S, Z0)
        for f in range(S.shape[0]):
            Yuu = Y[f][np.ix_(unused, unused)] + np.eye(len(unused)) / Z0
            Yred = (Y[f][np.ix_(keep, keep)]
                    - Y[f][np.ix_(keep, unused)]
                    @ np.linalg.solve(Yuu, Y[f][np.ix_(unused, keep)]))
            np.testing.assert_allclose(rs.y_to_s(Yred[None], Z0)[0],
                                       S[f][np.ix_(keep, keep)],
                                       rtol=1e-9, atol=1e-11)
        np.testing.assert_allclose(got, S[:, keep][:, :, keep], rtol=1e-12)

    def test_open_and_matched_differ(self):
        """Guard against the two methods silently collapsing into one."""
        S = make_network(4, seed=3)
        a = rs.reduce_block(S, Z0, [0, 1], [], "open")
        b = rs.reduce_block(S, Z0, [0, 1], [], "matched")
        self.assertGreater(np.abs(a - b).max(), 1e-3)

    def test_ground_equals_heavy_shunt_limit(self):
        """
        Grounding port g (delete row+col in Y) must equal the limit of hanging a
        very large shunt admittance on g and then eliminating it as an open.
        """
        S = make_network(5, seed=4)
        keep, gnd = [0, 1], [4]
        got = rs.reduce_block(S, Z0, keep, gnd, "open")

        Y = rs.s_to_y(S, Z0)
        unused = [2, 3, 4]
        for f in range(S.shape[0]):
            Ym = Y[f].copy()
            Ym[4, 4] += 1e12                       # near-ideal short to reference
            Yred = (Ym[np.ix_(keep, keep)]
                    - Ym[np.ix_(keep, unused)]
                    @ np.linalg.solve(Ym[np.ix_(unused, unused)], Ym[np.ix_(unused, keep)]))
            np.testing.assert_allclose(rs.y_to_s(Yred[None], Z0)[0], got[f],
                                       rtol=1e-6, atol=1e-9)

    def test_ground_differs_from_open(self):
        S = make_network(5, seed=5)
        opened = rs.reduce_block(S, Z0, [0, 1], [], "open")
        grounded = rs.reduce_block(S, Z0, [0, 1], [4], "open")
        self.assertGreater(np.abs(opened - grounded).max(), 1e-3)

    def test_no_unused_ports_is_identity(self):
        """keep + gnd covering every port must skip the Schur step cleanly."""
        S = make_network(3, seed=6)
        got = rs.reduce_block(S, Z0, [0, 1, 2], [], "open")
        np.testing.assert_allclose(got, S, rtol=1e-10, atol=1e-12)

    def test_keep_order_permutes_output(self):
        S = make_network(4, seed=7)
        a = rs.reduce_block(S, Z0, [0, 2], [], "open")
        b = rs.reduce_block(S, Z0, [2, 0], [], "open")
        # keep=[2,0] is keep=[0,2] with both axes reversed
        np.testing.assert_allclose(b, a[:, ::-1, ::-1], rtol=1e-10, atol=1e-14)

    def test_batching_does_not_change_result(self):
        S = make_network(5, n_freq=11, seed=8)
        whole = rs.reduce_all(S, Z0, [0, 1], [], "open", batch=1000)
        chunked = rs.reduce_all(S, Z0, [0, 1], [], "open", batch=3)
        np.testing.assert_allclose(whole, chunked, rtol=1e-12, atol=1e-14)

    def test_s_y_roundtrip(self):
        S = make_network(6, seed=9)
        np.testing.assert_allclose(rs.y_to_s(rs.s_to_y(S, Z0), Z0), S,
                                   rtol=1e-9, atol=1e-12)


class TestFileIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roundtrip(self, S, freqs, fmt="RI", names=None, ext=None):
        n = S.shape[-1]
        path = self.tmp / f"net{ext or ('.s%dp' % n)}"
        rs.write_touchstone(path, "HZ", Z0, names or [f"p{i}" for i in range(n)],
                            freqs, S, data_format=fmt)
        return path

    def test_roundtrip_via_independent_parser(self):
        for n in (1, 2, 3, 5):
            S = make_network(n, n_freq=5, seed=10 + n)
            freqs = np.linspace(1e6, 1e9, S.shape[0])
            path = self._roundtrip(S, freqs)
            net = core_parse(path)
            self.assertEqual(net.nports, n)
            np.testing.assert_allclose(net.freqs, freqs, rtol=1e-10)
            np.testing.assert_allclose(net.s, S, rtol=1e-9, atol=1e-14,
                                       err_msg=f"n={n}")

    def test_two_port_column_order_non_reciprocal(self):
        """
        Touchstone v1 writes a 2-port as S11 S21 S12 S22. A reciprocal network
        hides a transpose bug, so this uses a deliberately non-reciprocal one.
        """
        S = make_network(2, n_freq=4, seed=42, reciprocal=False)
        self.assertGreater(np.abs(S[:, 0, 1] - S[:, 1, 0]).max(), 1e-3,
                           "fixture must be non-reciprocal for this test to bite")
        freqs = np.linspace(1e6, 1e9, S.shape[0])
        net = core_parse(self._roundtrip(S, freqs))
        np.testing.assert_allclose(net.s[:, 0, 1], S[:, 0, 1], rtol=1e-9, atol=1e-14)
        np.testing.assert_allclose(net.s[:, 1, 0], S[:, 1, 0], rtol=1e-9, atol=1e-14)

    def test_own_parser_roundtrip_all_formats(self):
        S = make_network(3, n_freq=4, seed=11)
        freqs = np.linspace(1e6, 1e9, S.shape[0])
        for fmt, rtol in (("RI", 1e-10), ("MA", 1e-9), ("DB", 1e-8)):
            path = self.tmp / f"net_{fmt}.s3p"
            rs.write_touchstone(path, "HZ", Z0, ["a", "b", "c"], freqs, S,
                                data_format=fmt)
            ts = rs.parse_touchstone(path)
            self.assertEqual(ts.data_format, fmt)
            np.testing.assert_allclose(ts.s, S, rtol=rtol, atol=1e-13,
                                       err_msg=f"fmt={fmt}")

    def test_port_names_survive_roundtrip(self):
        S = make_network(3, n_freq=3, seed=12)
        freqs = np.linspace(1e6, 1e9, 3)
        path = self._roundtrip(S, freqs, names=["in_p", "VDD_RX", "gnd_ball"])
        ts = rs.parse_touchstone(path)
        self.assertEqual(ts.port_names, ["in_p", "VDD_RX", "gnd_ball"])

    def test_precision_flag_tightens_error(self):
        S = make_network(3, n_freq=3, seed=13)
        freqs = np.linspace(1e6, 1e9, 3)
        errs = []
        for prec in (6, 14):
            path = self.tmp / f"p{prec}.s3p"
            rs.write_touchstone(path, "HZ", Z0, ["a", "b", "c"], freqs, S,
                                data_format="RI", precision=prec)
            errs.append(np.abs(rs.parse_touchstone(path).s - S).max())
        self.assertGreater(errs[0], errs[1])


class TestParserRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        p = self.tmp / name
        p.write_text(text)
        return p

    def test_q3d_style_header(self):
        path = self._write("q3d.s2p", "\n".join([
            "! Touchstone file exported from Q3D Extractor 2019.1.0",
            "! Terminal data exported",
            "! Port[1] = Net_A",
            "! Port [2] : Net_B",
            "# Hz S RI R 50",
            "1e6 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8",
        ]))
        ts = rs.parse_touchstone(path)
        self.assertEqual(ts.n_ports, 2)
        self.assertEqual(ts.freq_unit, "HZ")
        self.assertEqual(ts.port_names, ["Net_A", "Net_B"])
        # file order S11 S21 S12 S22 -> matrix must be transposed on read
        self.assertAlmostEqual(ts.s[0, 1, 0], 0.3 + 0.4j)
        self.assertAlmostEqual(ts.s[0, 0, 1], 0.5 + 0.6j)

    def test_alternate_port_name_comment(self):
        path = self._write("alt.s1p", "! Port 1 = only_one\n# Hz S RI R 50\n1e6 0.1 0.2\n")
        self.assertEqual(rs.parse_touchstone(path).port_names, ["only_one"])

    def test_mid_line_comment_stripped(self):
        path = self._write("mid.s1p", "# Hz S RI R 50\n1e6 0.1 0.2 ! trailing junk\n"
                                      "2e6 0.3 0.4\n")
        ts = rs.parse_touchstone(path)
        self.assertEqual(len(ts.freqs), 2)
        self.assertAlmostEqual(ts.s[1, 0, 0], 0.3 + 0.4j)

    def test_wrong_extension_infers_port_count(self):
        """A 2-port file misnamed .s4p must be re-sniffed, not silently mangled."""
        body = "\n".join(f"{i}e6 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8" for i in range(1, 9))
        path = self._write("wrong.s4p", "# Hz S RI R 50\n" + body + "\n")
        ts = rs.parse_touchstone(path)
        self.assertEqual(ts.n_ports, 2)
        self.assertEqual(len(ts.freqs), 8)

    def test_nports_override(self):
        body = "\n".join(f"{i}e6 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8" for i in range(1, 9))
        path = self._write("amb.dat", "# Hz S RI R 50\n" + body + "\n")
        self.assertEqual(rs.parse_touchstone(path, force_nports=2).n_ports, 2)

    def test_y_parameter_file_rejected(self):
        path = self._write("y.s1p", "# Hz Y RI R 50\n1e6 0.1 0.2\n")
        with self.assertRaises(SystemExit):
            rs.parse_touchstone(path)

    def test_missing_option_line_defaults(self):
        path = self._write("noopt.s1p", "1e6 0.5 30\n2e6 0.5 30\n")
        ts = rs.parse_touchstone(path)
        self.assertEqual((ts.freq_unit, ts.data_format, ts.z0), ("GHZ", "MA", 50.0))

    def test_chunked_read_matches_single_chunk(self):
        body = "\n".join(f"{i}e6 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8" for i in range(1, 51))
        path = self._write("chunk.s2p", "# Hz S RI R 50\n" + body + "\n")
        a = rs.parse_touchstone(path, flush_values=3)
        b = rs.parse_touchstone(path, flush_values=10 ** 6)
        np.testing.assert_allclose(a.s, b.s)
        np.testing.assert_allclose(a.freqs, b.freqs)

    def test_bad_token_raises_not_truncates(self):
        path = self._write("bad.s1p", "# Hz S RI R 50\n1e6 0.1 0.2\n2e6 oops 0.4\n")
        with self.assertRaises(ValueError):
            rs.parse_touchstone(path)

    def test_z0_other_than_50(self):
        path = self._write("z75.s1p", "# Hz S RI R 75\n1e6 0.1 0.2\n")
        self.assertEqual(rs.parse_touchstone(path).z0, 75.0)


class TestEndToEndCLI(unittest.TestCase):
    """Drive the actual command line, then read the output with the other parser."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.S = make_network(6, n_freq=9, seed=77)
        self.freqs = np.linspace(1e6, 1e9, self.S.shape[0])
        self.src = self.tmp / "src.s6p"
        rs.write_touchstone(self.src, "HZ", Z0,
                            ["sig_a", "sig_b", "gnd1", "gnd2", "spare1", "spare2"],
                            self.freqs, self.S, data_format="RI", precision=15)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, cfg_text, *extra):
        import subprocess
        cfg = self.tmp / "ports.txt"
        cfg.write_text(cfg_text)
        out = self.tmp / "out.snp"
        script = Path(__file__).resolve().parent.parent / "reduce_snp.py"
        r = subprocess.run([sys.executable, str(script), str(self.src),
                            "--ports", str(cfg), "-o", str(out), *extra],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return out, r.stdout

    def test_cli_open_with_gnd_group(self):
        out, log = self._run("# GND\ngnd1, gnd2\n# SIG\n1 2\n")
        got = core_parse(out)
        ref = rs.reduce_block(self.S, Z0, [0, 1], [2, 3], "open")
        self.assertEqual(got.nports, 2)
        np.testing.assert_allclose(got.s, ref, rtol=1e-8, atol=1e-12)
        self.assertIn("2 grounded", log)

    def test_cli_matched_equals_submatrix(self):
        out, _ = self._run("# SIG\n1 3\n", "--method", "matched")
        got = core_parse(out)
        np.testing.assert_allclose(got.s, self.S[:, [0, 2]][:, :, [0, 2]],
                                   rtol=1e-8, atol=1e-12)

    def test_cli_config_order(self):
        out, _ = self._run("# SIG\n5 1\n", "--order", "config")
        got = core_parse(out)
        ref = rs.reduce_block(self.S, Z0, [4, 0], [], "open")
        np.testing.assert_allclose(got.s, ref, rtol=1e-8, atol=1e-12)
        self.assertEqual(got.port_names, ["spare1", "sig_a"])

    def test_cli_writes_mapping_file(self):
        out, _ = self._run("# GND\n3\n# SIG\n1 2\n")
        mapping = Path(out).with_suffix(".port_mapping.txt")
        self.assertTrue(mapping.exists())
        text = mapping.read_text()
        self.assertIn("SHORTED TO REFERENCE GROUND", text)
        self.assertIn("sig_a", text)

    def test_cli_refuses_when_nothing_to_reduce(self):
        import subprocess
        cfg = self.tmp / "all.txt"
        cfg.write_text("# ALL\n1 2 3 4 5 6\n")
        script = Path(__file__).resolve().parent.parent / "reduce_snp.py"
        r = subprocess.run([sys.executable, str(script), str(self.src),
                            "--ports", str(cfg)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


class TestPassivity(unittest.TestCase):
    def test_passive_network_reported_ok(self):
        S = np.zeros((5, 2, 2), dtype=complex)
        S[:, 0, 1] = S[:, 1, 0] = 0.5
        check = rs.check_passivity
        check(S, label="[t]")   # must not raise

    def test_active_network_detected(self):
        S = np.eye(2, dtype=complex)[None].repeat(4, axis=0) * 2.0
        rs.check_passivity(S, label="[t]")  # prints a warning, must not raise


if __name__ == "__main__":
    unittest.main()
