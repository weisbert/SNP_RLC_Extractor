"""
How big a Touchstone file this tool will read, and what it says when it won't.

Three things are pinned here, all of them measured rather than asserted from
taste (the numbers are in the comments beside the constants in pkg_rlc_core):

  * THE PORT-COUNT CEILING.  The content sniffer used to stop at
    MAX_SNIFF_NPORTS = 256 and then fall back on the '.sNp' extension, so a
    300-port package export that had been renamed -- which is the normal fate of
    these files, and the whole reason the sniffer is content-based -- had no
    route into the tool at all.  It now escalates: cheap sweep -> the file name
    (checked against the content) -> a wide sweep to SNIFF_HARD_CAP -> refuse.
    Each rung says in a WARN line that it was reached, and the order matters:
    a file whose NAME fits must still be answered by the name, or the historical
    message disappears from under
    test_parse_diagnostics.py::test_extension_rescues_a_port_count_over_the_sniff_cap.

  * THE REFUSAL.  "Too big" has to come out as a TouchstoneParseError with a
    kind and a verdict naming what was exceeded and how to get past it
    (force_nports), never as a MemoryError traceback and never as a silently
    wrong port count.

  * THE TWO THINGS THAT MADE SIZE EXPENSIVE.  `np.all(np.diff(x) > 0)` in the
    sniffer walked a third of every number in the file to reject N=1, and the
    diagnosis pass's line index stopped at DIAGNOSE_MAX_LINES and then reported
    the LAST line it had recorded as the truncation point -- a flatly wrong line
    number in the one report whose job is naming the line.

The big fixture is 300 ports x 3 frequencies (540 003 numbers, ~2.4 MB), built
once per class in the system temp dir and deleted with it.  Three frequencies is
enough: the sniffer's question is about the record size, not the sweep length.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import pkg_rlc.physics.core as pkg_rlc_core  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    FAULT_ACCESS,
    FAULT_FILE,
    FAULT_NONE,
    MAX_SNIFF_NPORTS,
    SNIFF_HARD_CAP,
    TouchstoneParseError,
    check_touchstone,
    parse_touchstone,
)

BIG_N = 300          # > MAX_SNIFF_NPORTS: unreachable by content before this
BIG_F = 3


def _s_entry(i: int, j: int) -> complex:
    """The value written into slot (i, j) of every record of the big file."""
    return complex((i * BIG_N + j) % 97 + 0.5, 0.25)


class _BigFileCase(unittest.TestCase):
    """One 300-port file, written under several names, shared by the class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._dir.name)
        # Every record carries the same body, so the 90 000-pair join happens
        # once.  The values are position-dependent: a port count that is wrong,
        # or a record boundary that is off by one slot, moves them.
        body = " ".join(
            f"{_s_entry(i, j).real:g} {_s_entry(i, j).imag:g}"
            for i in range(BIG_N) for j in range(BIG_N))
        cls.text = "# HZ S RI R 50\n" + "".join(
            f"{(k + 1) * 1e6:.6e} {body}\n" for k in range(BIG_F))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._dir.cleanup()

    @classmethod
    def big(cls, name: str) -> Path:
        p = cls.tmp / name
        if not p.exists():
            p.write_text(cls.text, encoding="utf-8")
        return p


# ============================================================================
# The port-count ceiling
# ============================================================================

class TestPortCountCeiling(_BigFileCase):

    def test_the_cheap_sweep_really_cannot_reach_this_file(self) -> None:
        """Precondition, stated mechanically: without it every test in this
        class is a test of the cheap sweep and passes for free.

        Raising MAX_SNIFF_NPORTS past 300 later would do exactly that silently,
        so this reads the sweep rather than the constant.
        """
        self.assertGreater(BIG_N, MAX_SNIFF_NPORTS)
        self.assertGreater(SNIFF_HARD_CAP, BIG_N)
        # The value stream the parser builds: every record shares a body and
        # differs only in its frequency.
        one = np.array([float(t) for t in self.text.splitlines()[1].split()])
        vals = np.tile(one, BIG_F)
        vals[0::one.size] = np.arange(1, BIG_F + 1) * 1e6
        self.assertEqual(
            pkg_rlc_core._sniff_range(vals, 1, MAX_SNIFF_NPORTS), [],
            "the 1..MAX_SNIFF_NPORTS sweep can already answer this file")
        self.assertEqual(
            pkg_rlc_core._sniff_range(vals, MAX_SNIFF_NPORTS + 1,
                                      SNIFF_HARD_CAP), [BIG_N])

    def test_a_renamed_300_port_file_now_parses(self) -> None:
        """The gap this raise exists to close.

        Before: the sweep stopped at 256, '.txt' said nothing, and the only way
        in was force_nports -- for a file whose port count is sitting right
        there in the arithmetic.
        """
        ts = parse_touchstone(self.big("pkg_renamed.txt"))
        self.assertEqual(ts.nports, BIG_N)
        self.assertEqual(ts.s.shape, (BIG_F, BIG_N, BIG_N))

    def test_the_wide_search_says_it_was_reached_and_names_the_way_out(self) -> None:
        """Nothing corroborates a count found this way, so it may not be silent.

        A wide sweep can in principle land on a coincidence, and the user is the
        only one who can tell.  The escape hatch has to be named, not implied.
        """
        ts = parse_touchstone(self.big("pkg_renamed.txt"))
        hit = [w for w in ts.parser_warnings if "searching past" in w]
        self.assertEqual(len(hit), 1, ts.parser_warnings)
        self.assertIn(f"N={MAX_SNIFF_NPORTS}", hit[0])
        self.assertIn("force_nports", hit[0])
        self.assertIn("--force-nports", hit[0])

    def test_the_file_name_is_still_consulted_before_the_wide_search(self) -> None:
        """The rungs are ordered, and merging them into one sweep is the
        tempting simplification that breaks it.

        With the name available the answer must come FROM the name, with the
        historical wording -- that message is what
        test_parse_diagnostics.py::test_extension_rescues_a_port_count_over_the_
        sniff_cap reads.  One sweep to SNIFF_HARD_CAP would answer 300 with no
        warning at all and quietly delete it.
        """
        ts = parse_touchstone(self.big(f"pkg.s{BIG_N}p"))
        self.assertEqual(ts.nports, BIG_N)
        self.assertTrue(any(f"file name says N={BIG_N}" in w
                            for w in ts.parser_warnings), ts.parser_warnings)
        self.assertFalse(any("searching past" in w for w in ts.parser_warnings),
                         ts.parser_warnings)

    def test_the_numbers_land_in_the_right_slots(self) -> None:
        """A wrong port count is not an error, it is a plausible wrong answer.

        Checking only `nports` would pass on a file read with the right count
        and the wrong record boundary, so this reads the corners back out.
        """
        ts = parse_touchstone(self.big("pkg_renamed.txt"))
        np.testing.assert_allclose(ts.freqs, [1e6, 2e6, 3e6])
        for i, j in ((0, 0), (0, 1), (1, 0), (7, 123), (BIG_N - 1, BIG_N - 1)):
            for k in range(BIG_F):
                self.assertEqual(ts.s[k, i, j], _s_entry(i, j), (k, i, j))

    def test_force_nports_still_overrides_everything(self) -> None:
        ts = parse_touchstone(self.big("pkg_renamed.txt"), force_nports=BIG_N)
        self.assertEqual(ts.nports, BIG_N)
        self.assertFalse(any("searching past" in w for w in ts.parser_warnings),
                         ts.parser_warnings)

    def test_the_sweep_stops_at_what_the_file_could_hold(self) -> None:
        """A record longer than the file cannot divide it.

        This is what keeps SNIFF_HARD_CAP free on ordinary files, so it must be
        exact rather than approximately right: at the boundary, N whose record
        is exactly the file length is still reachable and N+1 is not.
        """
        for n in (1, 2, 4, 42, 300, 4096):
            rec = 1 + 2 * n * n
            self.assertEqual(pkg_rlc_core._max_possible_nports(rec), n)
            self.assertEqual(pkg_rlc_core._max_possible_nports(rec - 1), n - 1)

    def test_the_bound_never_hides_a_real_port_count(self) -> None:
        """It is an optimisation, so it has to be invisible in the answer."""
        for n in (1, 2, 3, 5, 8, 17, 64, 257, 400):
            for nf in (1, 2, 5):
                rec = 1 + 2 * n * n
                v = np.zeros(rec * nf)
                v[0::rec] = np.arange(1, nf + 1) * 1e6
                self.assertEqual(pkg_rlc_core._sniff_nports(v, [], None), n,
                                 f"N={n}, {nf} frequencies")


# ============================================================================
# Refusal: what happens when the file really is out of reach
# ============================================================================

class TestRefusal(unittest.TestCase):
    """The caps are patched down so the branches are reachable in milliseconds.

    Reaching the real SNIFF_HARD_CAP needs a file of 33.6M numbers (~270 MB of
    text); the branch is the same one either way, and a test that costs a minute
    is a test that gets skipped.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _write(self, name: str, n: int, nf: int, *, per_line: int = 0) -> Path:
        rec = 2 * n * n
        vals = ["0.1"] * rec
        rows = []
        for k in range(nf):
            row = [f"{(k + 1) * 1e6:.6e}"] + vals
            if per_line:
                rows.extend(" ".join(row[i:i + per_line])
                            for i in range(0, len(row), per_line))
            else:
                rows.append(" ".join(row))
        p = self.tmp / name
        p.write_text("# HZ S RI R 50\n" + "\n".join(rows) + "\n",
                     encoding="utf-8")
        return p

    def test_over_the_cap_is_a_parse_error_with_a_verdict_naming_the_cap(self) -> None:
        """It must not read as "your file is broken" -- it may not be.

        Nothing in a single number can separate a 5000-port export from a
        corrupt file, so the verdict states both readings and force_nports is
        what settles it.  The file here is spread over several lines so the
        diagnosis's one-record-per-line heuristic cannot answer either.
        """
        p = self._write("wide.dat", 6, 3, per_line=8)
        with mock.patch.object(pkg_rlc_core, "MAX_SNIFF_NPORTS", 2), \
                mock.patch.object(pkg_rlc_core, "SNIFF_HARD_CAP", 4):
            with self.assertRaises(TouchstoneParseError) as cm:
                parse_touchstone(p)
        e = cm.exception
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("N=4", e.verdict)
        self.assertIn("more ports", e.verdict)
        self.assertIn("--force-nports", e.report())
        # ...and the hatch it names actually works.
        self.assertEqual(parse_touchstone(p, force_nports=6).nports, 6)

    def test_a_file_that_reads_cleanly_over_the_cap_is_not_called_a_parser_bug(self) -> None:
        """FAULT_NONE says the file is fine, and here it IS fine.

        One record per line, so the diagnosis works the port count out from the
        line length.  The generic wording for that branch ends "that is a PARSER
        problem ... please report it", which for a documented cap with a
        documented way past it sends the user to open an issue instead of typing
        --force-nports.
        """
        p = self._write("clean.dat", 6, 3)
        with mock.patch.object(pkg_rlc_core, "MAX_SNIFF_NPORTS", 2), \
                mock.patch.object(pkg_rlc_core, "SNIFF_HARD_CAP", 4):
            kind, report = check_touchstone(p)
            with self.assertRaises(TouchstoneParseError) as cm:
                parse_touchstone(p)
        self.assertEqual(kind, FAULT_NONE)          # the file is not at fault
        self.assertIn("--force-nports 6", report)
        self.assertNotIn("PARSER problem", report)
        self.assertIn("looks fine", cm.exception.verdict)
        self.assertIn("N=4", cm.exception.verdict)

    def test_a_small_inconsistent_file_is_still_the_files_fault(self) -> None:
        """And it must NOT be told about a cap it never came near.

        Naming SNIFF_HARD_CAP here would send someone hunting for a setting when
        what they have is 17 numbers that divide into nothing.
        """
        p = self.tmp / "junk.dat"
        p.write_text("# HZ S RI R 50\n" + " ".join(["1.0"] * 17) + "\n",
                     encoding="utf-8")
        with self.assertRaises(TouchstoneParseError) as cm:
            parse_touchstone(p)
        self.assertEqual(cm.exception.kind, FAULT_FILE)
        self.assertNotIn(f"N={SNIFF_HARD_CAP}", cm.exception.report())
        self.assertIn("force_nports", cm.exception.report())

    def test_running_out_of_memory_is_a_verdict_not_a_traceback(self) -> None:
        """The other face of "too big", and the one with no cap in front of it.

        numpy's own _ArrayMemoryError subclasses MemoryError, so the guard in
        parse_touchstone catches a failed (F, N, N) allocation too; what this
        pins is that it comes out as a report with a next step rather than as a
        raw traceback in a GUI that has no console.
        """
        p = self._write("ok.s2p", 2, 3)
        self.assertEqual(parse_touchstone(p).nports, 2)      # precondition
        with mock.patch("numpy.empty", side_effect=MemoryError("boom")):
            with self.assertRaises(TouchstoneParseError) as cm:
                parse_touchstone(p)
        self.assertEqual(cm.exception.kind, FAULT_ACCESS)
        self.assertIn("reduce_snp.py", cm.exception.report())
        self.assertNotIn("Traceback", cm.exception.report())


# ============================================================================
# The monotonicity check the sniffer selects on
# ============================================================================

class TestStrictlyIncreasing(unittest.TestCase):

    @staticmethod
    def _replaced(x: np.ndarray) -> bool:
        """The predicate this replaced, verbatim."""
        with np.errstate(invalid="ignore", over="ignore"):
            return bool(x.size < 2 or np.all(np.diff(x) > 0))

    def test_it_agrees_with_the_predicate_it_replaced(self) -> None:
        """`a - b > 0` and `a > b` have to answer alike for every pair.

        The interesting inputs are the ones where subtraction does something
        comparison does not: nan and inf (a difference that is nan), values that
        overflow when subtracted, signed zeros, and denormals.
        """
        pool = [0.0, -0.0, 1.0, -1.0, np.nan, np.inf, -np.inf,
                1e308, -1e308, 5e-324, 1e-320, 1.0, 1.0000000000000002]
        rng = np.random.default_rng(20260811)
        for _ in range(3000):
            x = rng.choice(pool, size=int(rng.integers(0, 8)))
            self.assertEqual(pkg_rlc_core._strictly_increasing(x),
                             self._replaced(x), list(x))
        for _ in range(200):
            x = np.sort(rng.random(int(rng.integers(2, 50))))
            self.assertEqual(pkg_rlc_core._strictly_increasing(x),
                             self._replaced(x))

    def test_a_break_on_the_chunk_boundary_is_seen(self) -> None:
        """The chunks overlap by one element for exactly this pair.

        Without the overlap the pair straddling the boundary is never compared,
        and a file whose frequency column steps backwards there sniffs as a
        valid port count -- a wrong answer with no symptom.
        """
        ch = pkg_rlc_core._MONO_CHUNK
        for k in (ch - 1, ch, ch + 1, 2 * ch, 2 * ch + 1):
            x = np.arange(2 * ch + 4, dtype=float)
            x[k] = x[k - 1]              # equal, so not strictly increasing
            self.assertFalse(pkg_rlc_core._strictly_increasing(x), k)
            self.assertEqual(pkg_rlc_core._strictly_increasing(x),
                             self._replaced(x), k)

    def test_it_stops_as_soon_as_the_answer_is_settled(self) -> None:
        """The point of the rewrite: the candidates that cost the most are the
        WRONG ones, where the strided view is enormous and the first pair
        settles it.

        Measured on the N=1 view of a 153-port, 600-frequency array (9.36M
        elements): 85.1 ms and 84 MB of temporaries the old way, one 64 KB chunk
        this way.  The assertion is relative so it does not encode this machine.
        """
        x = np.arange(5_000_000, dtype=float)
        x[1] = 0.0                        # the very first pair fails
        t0 = time.perf_counter()
        self.assertFalse(self._replaced(x))
        old = time.perf_counter() - t0
        t0 = time.perf_counter()
        self.assertFalse(pkg_rlc_core._strictly_increasing(x))
        new = time.perf_counter() - t0
        self.assertLess(new * 10, old, f"old {old * 1e3:.2f} ms, "
                                       f"new {new * 1e3:.2f} ms")


# ============================================================================
# The diagnosis pass's line index
# ============================================================================

class TestDiagnosisLineIndex(unittest.TestCase):
    """DIAGNOSE_MAX_LINES is patched down instead of writing 2M lines.

    The cap bites on real files -- a 153-port sweep written 4 pairs to the line
    is 5967 data lines per frequency, so at 3000 frequencies the index stops 11%
    of the way in -- and a truncated file breaks at its END, past the head.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _truncated(self, name: str, whole: int, short: int) -> Path:
        """`whole` complete 2-port records, then `short` one-number lines.

        One number per trailing line so the leftover can be made as many lines
        long as the test needs: it is always within the last record, so with
        fatter lines it lands within a line or two of the end and no tail window
        small enough to miss it is also big enough to be honest about.
        """
        rows = [" ".join([f"{(k + 1) * 1e6:.6e}"] + ["0.1"] * 8)
                for k in range(whole)]
        rows += [f"{(whole + k + 1) * 1e6:.6e}" for k in range(short)]
        p = self.tmp / name
        p.write_text("# HZ S RI R 50\n" + "\n".join(rows) + "\n",
                     encoding="utf-8")
        return p

    def test_the_head_index_still_names_the_line(self) -> None:
        p = self._truncated("head.s2p", 40, 1)
        kind, report = check_touchstone(p)
        self.assertEqual(kind, FAULT_FILE)
        self.assertIn("the leftover starts at line 42", report)

    def test_the_tail_ring_names_a_line_past_the_head(self) -> None:
        """This is the whole point of the ring.

        With the index capped at 10 lines the old code answered from the last
        head entry and reported line 11 for a break at line 42 -- confidently,
        with no sign that it had run off the end of what it knew.
        """
        p = self._truncated("tail.s2p", 40, 1)
        with mock.patch.object(pkg_rlc_core, "DIAGNOSE_MAX_LINES", 10):
            kind, report = check_touchstone(p)
        self.assertEqual(kind, FAULT_FILE)
        self.assertIn("the leftover starts at line 42", report)
        self.assertNotIn("line 11", report)

    def test_out_of_reach_of_both_indexes_says_so_instead_of_guessing(self) -> None:
        """No line number beats a wrong one, but silence is not the answer
        either -- an omitted line reads as "the tool did not look"."""
        p = self._truncated("gap.s2p", 40, 8)
        with mock.patch.object(pkg_rlc_core, "DIAGNOSE_MAX_LINES", 10), \
                mock.patch.object(pkg_rlc_core, "DIAGNOSE_TAIL_LINES", 2):
            kind, report = check_touchstone(p)
        self.assertEqual(kind, FAULT_FILE)
        self.assertNotIn("starts at line", report)
        self.assertIn("not recorded", report)
        self.assertIn("tail window", report)

    def test_the_index_reports_what_it_covers(self) -> None:
        p = self._truncated("note.s2p", 40, 1)
        with mock.patch.object(pkg_rlc_core, "DIAGNOSE_MAX_LINES", 10), \
                mock.patch.object(pkg_rlc_core, "DIAGNOSE_TAIL_LINES", 4):
            _kind, report = check_touchstone(p)
        self.assertIn("first 10 data lines and the last 4", report)


if __name__ == "__main__":
    unittest.main()
