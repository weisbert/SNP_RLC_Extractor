"""
Tests for the robust-reading work: what a Touchstone file says about itself,
and what happens when it cannot be read.

Three groups:

  * DESCRIBE -- the frequency span, the sweep description and the data notes.
    These are the "what am I actually looking at" facts that used to be
    nowhere on screen.
  * REFUSE -- every failure comes out as a TouchstoneParseError whose `kind`
    says whose fault it is (file / unsupported / access / internal) and whose
    report names a line.  A test that only asserts "raises ValueError" would
    have passed before any of this existed, so each one pins the verdict and
    the line number too.
  * RECOVER -- the spellings a real EDA export uses that the parser now reads
    instead of silently corrupting: UTF-16, a UTF-8 BOM, comma separators,
    Fortran D exponents, and an over-cap port count backed by the file name.

The Tk group at the bottom guards the two GUI affordances: the Check File
button has to be ON SCREEN at the minimum window size (pack unmaps from the
end, so a fourth button in that row is not free), and a parse failure has to
reach a dialog rather than a traceback.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import pkg_rlc.physics.core as pkg_rlc_core  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    FAULT_ACCESS,
    FAULT_FILE,
    FAULT_INTERNAL,
    FAULT_NONE,
    FAULT_UNSUPPORTED,
    PARSE_WARN_CAP,
    TouchstoneParseError,
    check_touchstone,
    diagnose_touchstone,
    format_freq,
    parse_touchstone,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "pi_2port.s2p"


def _ensure_fixtures() -> None:
    if FIXTURE.exists():
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


class _TmpFileCase(unittest.TestCase):
    """Writes throwaway files next to each other in one temp dir."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_fixtures()
        cls.src = FIXTURE.read_text(encoding="utf-8")

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def write(self, name: str, content) -> Path:
        p = self.tmp / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def body_lines(self) -> list[str]:
        return self.src.splitlines()


# ============================================================================
# DESCRIBE
# ============================================================================

class TestFrequencyDescription(_TmpFileCase):
    def test_format_freq(self) -> None:
        self.assertEqual(format_freq(0.0), "0 Hz")
        self.assertEqual(format_freq(1e6), "1 MHz")
        self.assertEqual(format_freq(2.44e9), "2.44 GHz")
        self.assertEqual(format_freq(1e3), "1 kHz")

    def test_span_and_spacing_on_a_real_file(self) -> None:
        ts = parse_touchstone(FIXTURE)
        self.assertEqual(ts.freq_span_str(), "1 MHz - 10 GHz")
        self.assertEqual(ts.freq_spacing, "linear, step 25 MHz")
        self.assertEqual(ts.option_line, "HZ S RI R 50")
        text = ts.summary()
        self.assertIn("1 MHz - 10 GHz", text)
        self.assertIn("2 ports, 401 points", text)

    def test_single_point_span(self) -> None:
        head = [ln for ln in self.body_lines() if ln.startswith(("!", "#"))]
        data = [ln for ln in self.body_lines()
                if not ln.startswith(("!", "#")) and ln.strip()]
        p = self.write("one.s2p", "\n".join(head + data[:1]) + "\n")
        ts = parse_touchstone(p)
        self.assertIn("(single point)", ts.freq_span_str())
        self.assertEqual(ts.freq_spacing, "single point")

    def test_dc_point_is_noted_not_warned(self) -> None:
        """A sweep starting at 0 Hz makes L/C/Q undefined at that point.

        It goes in data_notes, not parser_warnings: nothing was guessed or
        thrown away, and parser_warnings is pinned element-for-element by the
        golden reference.
        """
        lines = self.body_lines()
        out = []
        for ln in lines:
            if ln.startswith("1.000000000e+06"):
                out.append("0.0" + ln[len("1.000000000e+06"):])
            else:
                out.append(ln)
        ts = parse_touchstone(self.write("dc.s2p", "\n".join(out) + "\n"))
        self.assertEqual(ts.freqs[0], 0.0)
        self.assertEqual(ts.parser_warnings, [])
        self.assertTrue(any("DC" in n for n in ts.data_notes), ts.data_notes)

    def test_log_sweep_is_described(self) -> None:
        head = "! log sweep\n# HZ S RI R 50\n"
        rows = []
        for k in range(21):                      # 1e6 .. 1e8, 10 per decade
            f = 1e6 * (10.0 ** (k / 10.0))
            rows.append(f"{f:.10e} 0.1 0.0 0.2 0.0 0.2 0.0 0.1 0.0")
        ts = parse_touchstone(self.write("log.s2p", head + "\n".join(rows)))
        self.assertIn("logarithmic", ts.freq_spacing)
        self.assertIn("10 points/decade", ts.freq_spacing)

    def test_irregular_sweep_is_described(self) -> None:
        head = "# HZ S RI R 50\n"
        rows = [f"{f:.6e} 0.1 0.0 0.2 0.0 0.2 0.0 0.1 0.0"
                for f in (1e6, 2e6, 9e6, 1e7)]
        ts = parse_touchstone(self.write("irr.s2p", head + "\n".join(rows)))
        self.assertEqual(ts.freq_spacing, "irregular spacing")

    def test_s_greater_than_one_is_noted(self) -> None:
        head = "# HZ S RI R 50\n"
        rows = [f"{f:.6e} 3.0 0.0 0.2 0.0 0.2 0.0 0.1 0.0"
                for f in (1e6, 2e6, 3e6)]
        ts = parse_touchstone(self.write("act.s2p", head + "\n".join(rows)))
        self.assertAlmostEqual(ts.s_max, 3.0)
        self.assertTrue(any("max |S|" in n for n in ts.data_notes),
                        ts.data_notes)

    def test_nonfinite_s_is_warned(self) -> None:
        head = "# HZ S RI R 50\n"
        rows = [f"{f:.6e} nan 0.0 0.2 0.0 0.2 0.0 0.1 0.0"
                for f in (1e6, 2e6, 3e6)]
        ts = parse_touchstone(self.write("nan.s2p", head + "\n".join(rows)))
        self.assertTrue(any("nan or inf" in w for w in ts.parser_warnings),
                        ts.parser_warnings)


# ============================================================================
# REFUSE
# ============================================================================

class TestRefusal(_TmpFileCase):
    def _raises(self, path, **kw) -> TouchstoneParseError:
        with self.assertRaises(TouchstoneParseError) as cm:
            parse_touchstone(path, **kw)
        return cm.exception

    def test_error_is_still_a_valueerror(self) -> None:
        """Callers (and the pre-existing tests) catch ValueError."""
        self.assertTrue(issubclass(TouchstoneParseError, ValueError))

    def test_truncated_file_names_the_line_not_the_port_count(self) -> None:
        """The sniffer can only say 'could not infer port count'.

        On a file truncated mid-record that is true and useless: it sends the
        user off to force a port count that was never wrong.  The diagnosis
        pass owns the headline instead.
        """
        text = "\n".join(self.body_lines()[:-1] + ["1.0e10  0.28  0.44"]) + "\n"
        e = self._raises(self.write("trunc.s2p", text))
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("does not divide into whole records", e.what)
        report = str(e)
        self.assertIn("ends mid-record", report)
        self.assertIn("line 408", report)          # the short last line
        self.assertIn("THE FILE", report)

    def test_truncated_file_with_forced_nports(self) -> None:
        text = "\n".join(self.body_lines()[:-1] + ["1.0e10  0.28  0.44"]) + "\n"
        e = self._raises(self.write("trunc2.s2p", text), force_nports=2)
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("400 complete records of 9 plus 3 left over", e.what)

    def test_touchstone_v2_is_named_as_such(self) -> None:
        """Read as v1, '[Number of Ports] 4' injects a 4 into the data."""
        text = ("!c\n[Version] 2.0\n# GHZ S RI R 50\n[Number of Ports] 2\n"
                "[Network Data]\n1 .1 .2 .3 .4 .5 .6 .7 .8\n[End]\n")
        e = self._raises(self.write("v2.s2p", text))
        self.assertEqual(e.kind, FAULT_UNSUPPORTED)
        self.assertEqual(e.line_no, 2)
        self.assertIn("Touchstone 2.0", e.what)

    def test_touchstone_v2_is_refused_in_lenient_mode_too(self) -> None:
        """'Skip the bad tokens' is exactly the wrong answer for a v2 file."""
        text = ("[Version] 2.0\n# GHZ S RI R 50\n[Number of Ports] 2\n"
                "[Network Data]\n1 .1 .2 .3 .4 .5 .6 .7 .8\n[End]\n")
        e = self._raises(self.write("v2b.s2p", text), lenient=True)
        self.assertEqual(e.kind, FAULT_UNSUPPORTED)

    def test_bad_token_is_refused_and_offers_the_lenient_retry(self) -> None:
        text = self.src.replace("2.599750000e+07", "2.599750000e+07 OOPS", 1)
        e = self._raises(self.write("tok.s2p", text))
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertEqual(e.line_no, 9)
        self.assertTrue(e.retry_lenient)
        self.assertIn("OOPS", e.what)

    def test_lenient_reads_it_and_says_the_result_is_suspect(self) -> None:
        text = self.src.replace("2.599750000e+07", "2.599750000e+07 OOPS", 1)
        ts = parse_touchstone(self.write("tok2.s2p", text), lenient=True)
        self.assertEqual(ts.nports, 2)
        joined = " ".join(ts.parser_warnings)
        self.assertIn("OOPS", joined)
        self.assertIn("line 9", joined)
        self.assertIn("shifts all following values", joined)

    def test_lenient_warnings_are_capped(self) -> None:
        """A corrupt file used to emit one warning per token, unbounded."""
        rows = [f"{f:.6e} X 0.1 0.0 Y 0.2 0.0 0.2 0.0 0.1 0.0 Z"
                for f in (1e6, 2e6, 3e6, 4e6, 5e6)]
        p = self.write("many.s2p", "# HZ S RI R 50\n" + "\n".join(rows))
        ts = parse_touchstone(p, force_nports=2, lenient=True)
        examples = [w for w in ts.parser_warnings if w.startswith("Skipping")]
        self.assertEqual(len(examples), PARSE_WARN_CAP)
        self.assertTrue(any("more unparseable tokens skipped" in w
                            for w in ts.parser_warnings), ts.parser_warnings)

    def test_empty_file(self) -> None:
        e = self._raises(self.write("empty.s2p", ""))
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("empty", e.what)
        self.assertIn("nothing to read", e.verdict)

    def test_header_only_file_has_no_data(self) -> None:
        e = self._raises(self.write("head.s2p", "! only comments\n# HZ S RI R 50\n"))
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("no numeric data", e.what)

    def test_compressed_file_is_named(self) -> None:
        e = self._raises(self.write("z.s2p", b"\x1f\x8b\x08\x00rest"))
        self.assertEqual(e.kind, FAULT_UNSUPPORTED)
        self.assertIn("gzip", e.what)
        self.assertIn("gunzip", str(e))

    def test_binary_file_is_named(self) -> None:
        e = self._raises(self.write("bin.s2p", bytes(range(256)) * 8))
        self.assertEqual(e.kind, FAULT_FILE)
        self.assertIn("NUL", e.what)

    def test_missing_file_is_an_access_fault(self) -> None:
        e = self._raises(self.tmp / "nope.s2p")
        self.assertEqual(e.kind, FAULT_ACCESS)
        self.assertIn("cannot open", e.what)

    def test_unrecognised_option_token_is_reported(self) -> None:
        """A misspelt format keyword fell back to the MA default in silence,
        which reads RI data as magnitude/angle: a well-formed wrong file."""
        text = self.src.replace("# HZ S RI R 50", "# HZ S XX R 50", 1)
        ts = parse_touchstone(self.write("fmt.s2p", text))
        self.assertEqual(ts.data_format, "MA")
        joined = " ".join(ts.parser_warnings)
        self.assertIn("'XX'", joined)
        self.assertIn("not recognised", joined)
        self.assertIn("HZ S MA R 50", joined)

    def test_unexpected_internal_failure_blames_the_parser(self) -> None:
        """The other half of the promise: not every failure is the file's.

        Anything unexpected inside the parser is reported as OUR bug, with a
        traceback to paste -- but only after the diagnosis pass has confirmed
        the file itself hangs together.
        """
        boom = mock.patch.object(pkg_rlc_core, "_check_s_values",
                                 side_effect=RuntimeError("boom"))
        with boom:
            e = self._raises(FIXTURE)
        self.assertEqual(e.kind, FAULT_INTERNAL)
        self.assertIn("RuntimeError", e.what)
        report = str(e)
        self.assertIn("THE PARSER", report)
        self.assertIn("no inconsistency found", report)
        self.assertIn("Traceback", report)

    def test_non_monotonic_frequency_is_warned_when_nports_is_forced(self) -> None:
        """The sniffer selects on increasing frequencies; forcing N skips that."""
        rows = [f"{f:.6e} 0.1 0.0 0.2 0.0 0.2 0.0 0.1 0.0"
                for f in (1e6, 3e6, 2e6, 4e6)]
        p = self.write("nonmono.s2p", "# HZ S RI R 50\n" + "\n".join(rows))
        ts = parse_touchstone(p, force_nports=2)
        joined = " ".join(ts.parser_warnings)
        self.assertIn("not strictly increasing", joined)
        self.assertIn("forced port count", joined)
        # The warning is the verdict and it is unchanged; the SORT is the new
        # part, and it must not silence the reading the warning carries.
        self.assertIn("reordered by frequency", joined)
        self.assertIn("reordered by frequency", ts.freq_spacing)
        np.testing.assert_array_equal(ts.freqs,
                                      np.array([1e6, 2e6, 3e6, 4e6]))

    def test_second_option_line_is_reported(self) -> None:
        text = self.src.replace("# HZ S RI R 50",
                                "# HZ S RI R 50\n# GHZ S MA R 75", 1)
        ts = parse_touchstone(self.write("two.s2p", text))
        self.assertEqual(ts.z0, 50.0)
        self.assertTrue(any("one option line" in w for w in ts.parser_warnings),
                        ts.parser_warnings)


# ============================================================================
# OUT OF SWEEP ORDER
# ============================================================================

class TestFrequenciesWrittenOutOfOrder(_TmpFileCase):
    """
    A real 19-port export the tool refused, and the three separate defects it
    turned up.

    The file: 9399 numbers, 13 records of 723 at N=19, NOTHING left over, and
    record 3 at 15 GHz behind record 2's 30 GHz.  HFSS and ADS adaptive sweeps
    write their points in SOLVE order -- endpoints first, then bisect -- so
    the frequency column of a perfectly good export is not sorted.  Every
    number in it was correct.

    What the tool did with it:

      1. `_sniff_nports` selects on a strictly-increasing frequency column, so
         nothing fitted and the file could not be opened at all.  In the GUI
         that is a dead end: `force_nports` is CLI-only and the error carries
         no `retry_lenient`, so no button appears.
      2. `_diagnose`'s verdict assumed the only way a candidate fails is
         divisibility, and printed "the data does not divide into whole
         records ... plus 0 LEFT OVER" -- a headline contradicted by its own
         number -- with a hint saying the file was "usually truncated".  It
         was not truncated, and following that advice means asking whoever ran
         the simulation to re-export a file with nothing wrong with it.
      3. `freq_span_str` read the ENDS of the array, so the summary said
         "1 GHz - 17 GHz" for a file covering 1-30 GHz.

    Every test below names the mutation that defeats it.
    """

    #: solve order: the two endpoints, then bisect -- what HFSS writes.
    ADAPTIVE = (1e9, 30e9, 15e9, 8e9, 22e9, 4e9, 11e9)

    def _write(self, name: str, freqs, nports: int):
        """One record per line, with every S entry carrying its own frequency.

        That is what makes a mis-applied permutation VISIBLE: sorting `freqs`
        and forgetting `s` leaves the file readable and every number wrong,
        which is exactly the failure this feature could introduce.
        """
        lines = ["# HZ S RI R 50"]
        for f in freqs:
            row = [f"{f:.10e}"]
            for _ in range(nports * nports):
                row += [f"{f:.10e}", f"{-f:.10e}"]
            lines.append(" ".join(row))
        return self.write(name, "\n".join(lines) + "\n")

    # ------------------------------------------------------------- it opens
    def test_it_opens_with_no_forcing_at_all(self) -> None:
        """Mutation: drop step 2b from _sniff_nports -> TouchstoneParseError.

        The whole point.  The GUI has no force-nports control, so a file the
        sniffer refuses is a file the GUI user cannot open by any route.
        """
        p = self._write("adaptive.s4p", self.ADAPTIVE, 4)
        ts = parse_touchstone(p)
        self.assertEqual(ts.nports, 4)
        self.assertEqual(len(ts.freqs), len(self.ADAPTIVE))

    def test_the_name_is_what_lets_it_in_and_the_warning_says_so(self) -> None:
        """Mutation: make step 2b unconditional -> a wrong N sails through.

        The relaxed test is corroborated evidence, not a free pass: it runs
        only when the file NAME already says N.  Renaming the same bytes to an
        extension that does not fit puts it back on the refusal path.
        """
        p = self._write("adaptive.s4p", self.ADAPTIVE, 4)
        joined = " ".join(parse_touchstone(p).parser_warnings)
        self.assertIn("taken from the file name", joined)
        self.assertIn("OUT OF ORDER", joined)
        self.assertIn("Check the port count", joined)

        renamed = self.write("adaptive.s7p", p.read_text(encoding="utf-8"))
        with self.assertRaises(TouchstoneParseError):
            parse_touchstone(renamed)

    # --------------------------------------------------- the data is intact
    def test_the_S_data_travels_with_its_frequency(self) -> None:
        """Mutation: sort `freqs` and not `s` -> every number lands at the
        wrong frequency, and NOTHING else in this file would notice.

        This is the one way the fix could be worse than the bug: a readable
        file whose every value is misfiled.  Each S entry is written equal to
        its own record's frequency, so the check is exact.
        """
        for name, n in (("carry.s2p", 2), ("carry.s3p", 3)):
            with self.subTest(name):
                # n == 2 takes the column-major transpose and n > 2 does not,
                # so both layouts are exercised.
                p = self._write(name, self.ADAPTIVE, n)
                ts = parse_touchstone(p)
                np.testing.assert_array_equal(ts.freqs,
                                              np.sort(np.array(self.ADAPTIVE)))
                for k, f in enumerate(ts.freqs):
                    np.testing.assert_allclose(ts.s[k],
                                               np.full((n, n), f * (1 - 1j)))

    #: numpy's introsort really does reorder ties at this shape -- three
    #: elements does NOT (it falls back to a stable insertion sort), so a
    #: three-record fixture pins nothing.  Found by search, not by guessing.
    TIE_PATTERN = (2, 1, 1, 0, 0, 0, 0, 0)

    def test_the_sort_is_stable_so_a_repeated_frequency_keeps_file_order(self) -> None:
        """Mutation: kind="quicksort" -> records at one frequency swap.

        Nothing downstream can tell two records at the same frequency apart
        afterwards, so the only honest order is the one the file wrote.  Each
        record is stamped with its own line number in S11, which is what makes
        a swap visible.
        """
        self.assertNotEqual(
            np.argsort(np.array(self.TIE_PATTERN, dtype=float),
                       kind="quicksort").tolist(),
            np.argsort(np.array(self.TIE_PATTERN, dtype=float),
                       kind="stable").tolist(),
            "precondition: this pattern must distinguish the two sorts")

        lines = ["# HZ S RI R 50"]
        for k, f in enumerate(self.TIE_PATTERN):
            lines.append(f"{f * 1e9:.6e} {k:d}.0 0 0 0 0 0 0 0")
        p = self.write("tie.s2p", "\n".join(lines) + "\n")
        ts = parse_touchstone(p, force_nports=2)

        np.testing.assert_array_equal(
            ts.freqs, np.sort(np.array(self.TIE_PATTERN, dtype=float)) * 1e9)
        # within each frequency, the stamps must still ascend: that is file
        # order, and it is exactly what an unstable sort loses.
        stamps = [c[0][0].real for c in ts.s]
        for f in sorted(set(self.TIE_PATTERN)):
            got = [s for s, g in zip(stamps, ts.freqs) if g == f * 1e9]
            self.assertEqual(got, sorted(got), f"ties at {f} GHz reordered")

    def test_a_repeated_frequency_is_not_a_sweep_and_is_still_refused(self) -> None:
        """Mutation: drop the uniqueness test from _freq_column_plausible.

        Distinctness is what keeps the relaxed path from swallowing a wrong
        port count -- S data repeats, a frequency axis does not.  Without it
        the name alone would open anything that divides.
        """
        p = self._write("dup.s2p", (1e9, 3e9, 3e9, 2e9), 2)
        with self.assertRaises(TouchstoneParseError) as cm:
            parse_touchstone(p)
        self.assertIn("does not read as a frequency axis", str(cm.exception))

    def test_a_negative_leading_column_is_not_a_sweep(self) -> None:
        """Mutation: drop the `f < 0` test -> S data reads as frequencies.

        A wrong port count slices S values into the leading column, and S data
        goes negative where a frequency axis cannot.
        """
        p = self._write("neg.s2p", (1e9, -2e9, 3e9), 2)
        with self.assertRaises(TouchstoneParseError):
            parse_touchstone(p)

    # ------------------------------------------------------- what it reports
    def test_the_span_is_min_to_max_not_first_to_last(self) -> None:
        """Mutation: back to freqs[0] / freqs[-1].

        Held on an unsorted array directly, because parse_touchstone sorts and
        would hide the defect: this method is also handed arrays built
        elsewhere, and the reported file read "1 GHz - 17 GHz" over 1-30 GHz.
        """
        ts = parse_touchstone(self._write("span.s4p", self.ADAPTIVE, 4))
        self.assertEqual(ts.freq_span_str(), "1 GHz - 30 GHz")

        # Neither end may be read off the array: this permutation starts at
        # 15 GHz and ends at 8 GHz, so [0] and [-1] are both wrong.  ADAPTIVE
        # itself would not do -- it happens to start at its minimum, and the
        # `.min()` half of the fix would go unpinned.
        out_of_order = np.array([15e9, 1e9, 30e9, 8e9])
        scrambled = type(ts)(**{**ts.__dict__, "freqs": out_of_order})
        self.assertEqual(scrambled.freq_span_str(), "1 GHz - 30 GHz")

    def test_the_verdict_does_not_claim_a_file_that_divides_is_truncated(self) -> None:
        """Mutation: restore the single `elif candidates:` verdict.

        The reported headline said "does not divide into whole records ...
        plus 0 left over" and sent the user to re-export.  Both halves are
        checked, because either one alone would have been reported.
        """
        p = self._write("adaptive.s4p", self.ADAPTIVE, 4)
        kind, text = check_touchstone(p)
        self.assertEqual(kind, FAULT_NONE, text)
        self.assertNotIn("plus 0 left over", text)
        self.assertNotIn("usually truncated", text)
        self.assertIn("Nothing needs re-exporting", text)

        # The other file that divides exactly and is still refused.  It is a
        # FAULT_FILE, so it reaches the `elif candidates:` branch the reported
        # headline came from -- and there too, "does not divide ... plus 0
        # left over" must not be what it says.  Without this case the branch
        # is never entered by this test and `if left:` could be `if True:`.
        bad = self._write("dup.s2p", (1e9, 3e9, 3e9, 2e9), 2)
        kind, text = check_touchstone(bad)
        self.assertEqual(kind, FAULT_FILE, text)
        self.assertNotIn("plus 0 left over", text)
        self.assertNotIn("usually truncated", text)
        self.assertIn("does not read as a frequency axis", text)

    def test_the_diagnosis_and_the_reader_agree(self) -> None:
        """Mutation: give _diag_candidate its own plausibility rule.

        A diagnosis that calls a file broken while the reader opens it is the
        disagreement this module exists to prevent, so both sides go through
        `_freq_column_plausible`.
        """
        p = self._write("adaptive.s4p", self.ADAPTIVE, 4)
        self.assertEqual(check_touchstone(p)[0], FAULT_NONE)
        parse_touchstone(p)                       # must not raise

        bad = self._write("dup.s2p", (1e9, 3e9, 3e9, 2e9), 2)
        self.assertEqual(check_touchstone(bad)[0], FAULT_FILE)
        with self.assertRaises(TouchstoneParseError):
            parse_touchstone(bad)

    def test_a_genuinely_truncated_file_keeps_the_truncation_wording(self) -> None:
        """Mutation: route every candidate failure to the new branch.

        Truncation is still by far the commonest cause, and its advice is
        right -- splitting the verdict must not cost it.
        """
        p = self.write("trunc.s2p",
                       "# HZ S RI R 50\n"
                       "1e9 .1 0 .2 0 .2 0 .1 0\n"
                       "2e9 .1 0 .2 0 .2 0\n")
        kind, text = check_touchstone(p)
        self.assertEqual(kind, FAULT_FILE)
        self.assertIn("usually truncated", text)
        self.assertIn("left over", text)

    def test_an_already_sorted_file_is_untouched(self) -> None:
        """Mutation: sort unconditionally.

        Every fixture in the repo is sorted, and golden_legacy.npz pins
        parser_warnings element-for-element -- a warning or a copy on the
        normal path would be a change to every file anyone has ever opened.
        """
        ts = parse_touchstone(FIXTURE)
        self.assertEqual(ts.freq_spacing, "linear, step 25 MHz")
        self.assertFalse([w for w in ts.parser_warnings if "reorder" in w],
                         ts.parser_warnings)

    def test_the_DC_note_does_not_depend_on_DC_being_written_first(self) -> None:
        """DELIBERATE REDUNDANCY: two things have to be reverted to break this.

        The note is what warns that L, C and Q are undefined at 0 Hz, and on a
        file written out of sweep order the DC record is not the first one.
        Reverting `np.any(freqs == 0.0)` to `freqs[0] == 0.0` ALONE does not
        turn this red, and that is not the test being weak -- by then the sort
        has already run, so the two spellings genuinely agree.  Measured: the
        single mutation leaves it green; dropping the in-function sort as well
        turns it red.  `np.any` is kept so this check does not silently depend
        on an ordering established four lines above it.

        Against the code as it stood before this fix the test is decisive: it
        read `freqs[0]`, with no sort anywhere, and said nothing about a file
        whose 0 Hz point was written second.
        """
        p = self._write("dc.s2p", (2e9, 0.0, 1e9), 2)
        ts = parse_touchstone(p, force_nports=2)
        self.assertTrue(any("DC (0 Hz)" in n for n in ts.data_notes),
                        ts.data_notes)


# ============================================================================
# RECOVER
# ============================================================================

class TestRecovery(_TmpFileCase):
    def _same_as_fixture(self, path) -> None:
        ref = parse_touchstone(FIXTURE)
        got = parse_touchstone(path)
        self.assertEqual(got.nports, ref.nports)
        np.testing.assert_array_equal(got.freqs, ref.freqs)
        np.testing.assert_array_equal(got.s, ref.s)
        self.assertEqual(got.z0, ref.z0)

    def test_utf16_file(self) -> None:
        """Some EDA tools export UTF-16; it used to read as replacement chars."""
        self._same_as_fixture(self.write("u16.s2p", self.src.encode("utf-16")))

    def test_utf16_without_a_bom(self) -> None:
        self._same_as_fixture(
            self.write("u16le.s2p", self.src.encode("utf-16-le")))

    def test_utf8_bom(self) -> None:
        """A BOM glued to '#' hid the option line: the file then parsed as
        '# GHZ S MA R 50' with no complaint at all."""
        self._same_as_fixture(
            self.write("bom.s2p", b"\xef\xbb\xbf" + self.src.encode("utf-8")))

    def test_comma_separated_values(self) -> None:
        text = "# HZ S RI R 50\n1.0e6, 0.1, 0.0, 0.2, 0.0, 0.2, 0.0, 0.1, 0.0\n"
        ts = parse_touchstone(self.write("csvish.s2p", text), force_nports=2)
        self.assertEqual(ts.freqs[0], 1e6)
        self.assertTrue(any("Comma" in w for w in ts.parser_warnings),
                        ts.parser_warnings)

    def test_fortran_d_exponents(self) -> None:
        text = "# HZ S RI R 50\n1.0D+06 0.1 0.0 0.2 0.0 0.2 0.0 0.1 0.0\n"
        ts = parse_touchstone(self.write("dexp.s2p", text), force_nports=2)
        self.assertEqual(ts.freqs[0], 1e6)
        self.assertTrue(any("'D' exponents" in w for w in ts.parser_warnings),
                        ts.parser_warnings)

    def test_extension_breaks_a_tie(self) -> None:
        """Picking the smallest candidate silently reads a 2-port as 1-port."""
        # 9 numbers per record for N=2, 3 for N=1.  Every number increasing
        # across the whole file makes BOTH readings pass the sniffer's
        # strictly-increasing test, which is what creates the tie.
        rows = []
        for k in range(4):
            rows.append(" ".join(f"{100 * k + j:.6e}" for j in range(1, 10)))
        body = "# HZ S RI R 50\n" + "\n".join(rows) + "\n"
        as_s2p = parse_touchstone(self.write("tie.s2p", body))
        as_txt = parse_touchstone(self.write("tie.txt", body))
        self.assertEqual(as_s2p.nports, 2)
        self.assertTrue(any("file name says N=2" in w
                            for w in as_s2p.parser_warnings),
                        as_s2p.parser_warnings)
        # With no extension to go on the historical answer stands.
        self.assertEqual(as_txt.nports, 1)

    def test_extension_rescues_a_port_count_over_the_sniff_cap(self) -> None:
        """MAX_SNIFF_NPORTS is 256; package exports go well past that."""
        n = 300
        rec = [f"{1e6:.6e}"] + ["0.0"] * (2 * n * n)
        rec2 = [f"{2e6:.6e}"] + ["0.0"] * (2 * n * n)
        text = ("# HZ S RI R 50\n" + " ".join(rec) + "\n"
                + " ".join(rec2) + "\n")
        ts = parse_touchstone(self.write(f"big.s{n}p", text))
        self.assertEqual(ts.nports, n)
        self.assertTrue(any("file name says N=300" in w
                            for w in ts.parser_warnings), ts.parser_warnings)


# ============================================================================
# The diagnosis pass on its own
# ============================================================================

class TestDiagnose(_TmpFileCase):
    def test_healthy_file_reports_no_fault(self) -> None:
        kind, report = check_touchstone(FIXTURE)
        self.assertEqual(kind, FAULT_NONE)
        self.assertIn("CONSISTENT", report)
        self.assertIn("no inconsistency found", report)
        self.assertIn("401 line(s) carry 9 numbers", report)

    def test_truncated_file_locates_the_break(self) -> None:
        text = "\n".join(self.body_lines()[:-1] + ["1.0e10  0.28  0.44"]) + "\n"
        kind, report = check_touchstone(self.write("t.s2p", text))
        self.assertEqual(kind, FAULT_FILE)
        self.assertIn("the leftover starts at line 408", report)

    def test_v2_file(self) -> None:
        text = "[Version] 2.0\n# GHZ S RI R 50\n[End]\n"
        kind, report = check_touchstone(self.write("v2.s2p", text))
        self.assertEqual(kind, FAULT_UNSUPPORTED)
        self.assertIn("Touchstone 2.0", report)

    def test_never_raises_on_a_missing_path(self) -> None:
        report = diagnose_touchstone(self.tmp / "not_here.s2p")
        self.assertIsInstance(report, str)
        self.assertTrue(report)

    def test_never_raises_on_a_directory(self) -> None:
        report = diagnose_touchstone(self.tmp)
        self.assertIsInstance(report, str)
        self.assertTrue(report)

    def test_missing_option_line_is_called_out(self) -> None:
        text = "\n".join(ln for ln in self.body_lines()
                         if not ln.startswith("#"))
        report = diagnose_touchstone(self.write("noopt.s2p", text))
        self.assertIn("option line: MISSING", report)


# ============================================================================
# GUI wiring (skips cleanly with no display)
# ============================================================================

import tkinter as tk  # noqa: E402


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestGuiFileChecking(_TmpFileCase):
    def setUp(self) -> None:
        super().setUp()
        from pkg_rlc.frontend.app import App
        self.app = App()
        self.app.geometry("1040x600")        # the documented minsize
        self.app.deiconify()
        self.app.update()

    def tearDown(self) -> None:
        self.app.destroy()
        super().tearDown()

    def _results(self) -> str:
        return self.app.results_text.get("1.0", tk.END)

    def _btn_row(self):
        return self.app.files_lb.master.winfo_children()[0]

    def test_check_file_button_is_on_screen_at_minsize(self) -> None:
        """pack unmaps from the END: a fourth button in that row is not free.

        Measured at 1040x600 the row needs 364 px and has 448, so all four fit
        -- but `winfo_ismapped()` is the only thing that actually proves it,
        and a button nobody can see is the same as a button that is not there.
        """
        row = self._btn_row()
        labels = [b.cget("text") for b in row.winfo_children()]
        self.assertIn("Check File", labels)
        for b in row.winfo_children():
            self.assertEqual(b.winfo_ismapped(), 1, b.cget("text"))
        self.assertLessEqual(row.winfo_reqwidth(), row.winfo_width())

    def test_check_file_reports_on_the_selected_file(self) -> None:
        from pkg_rlc.frontend.app import FileEntry
        fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(fe)
        self.app._refresh_file_list()
        self.app.files_lb.selection_set(0)
        self.app._on_check_file()
        text = self._results()
        self.assertIn("File check:", text)
        self.assertIn("CONSISTENT", text)

    def test_file_list_line_carries_the_frequency_span(self) -> None:
        from pkg_rlc.frontend.app import FileEntry
        fe = FileEntry(parse_touchstone(FIXTURE))
        self.assertIn("1 MHz - 10 GHz", fe.info_str())

    def test_a_bad_file_reaches_a_dialog_not_a_traceback(self) -> None:
        p = self.write("bad.s2p", "\x00\x01\x02" * 100)
        with mock.patch("pkg_rlc.frontend.app.messagebox.showerror") as err:
            self.assertIsNone(self.app._load_one_file(str(p)))
        self.assertEqual(err.call_count, 1)
        self.assertIn("Verdict", err.call_args[0][1])

    def test_a_skippable_file_offers_the_lenient_retry(self) -> None:
        text = self.src.replace("2.599750000e+07", "2.599750000e+07 OOPS", 1)
        p = self.write("tok.s2p", text)
        with mock.patch("pkg_rlc.frontend.app.messagebox.askyesno",
                        return_value=True) as ask:
            ts = self.app._load_one_file(str(p))
        self.assertEqual(ask.call_count, 1)
        self.assertIsNotNone(ts)
        self.assertEqual(ts.nports, 2)
        # Declining leaves the file unloaded, with no exception.
        with mock.patch("pkg_rlc.frontend.app.messagebox.askyesno",
                        return_value=False):
            self.assertIsNone(self.app._load_one_file(str(p)))

    def test_loading_prints_the_summary_block(self) -> None:
        with mock.patch("pkg_rlc.frontend.app.filedialog.askopenfilenames",
                        return_value=(str(FIXTURE),)):
            self.app._on_add_file()
        text = self._results()
        self.assertIn("1 MHz - 10 GHz", text)
        self.assertIn("linear, step 25 MHz", text)
        self.assertEqual(len(self.app.files), 1)


if __name__ == "__main__":
    unittest.main()
