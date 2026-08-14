"""
Tests for the content-based Touchstone parser in pkg_rlc_core.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make pkg_rlc_core importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from pkg_rlc.physics.core import parse_touchstone  # noqa: E402


FIX = Path(__file__).resolve().parent / "fixtures"


def _ensure_fixtures() -> None:
    """Run the fixture generator if any expected file is missing."""
    needed = [
        "shunt_rl_1port.s1p",
        "shunt_c_1port.s1p",
        "pi_2port.s2p",
        "diff_pair_4port.s4p",
        "pi_2port_renamed.txt",
        "diff_pair_4port_renamed.dat",
    ]
    if all((FIX / n).exists() for n in needed):
        return
    # Import and run the generator (it's idempotent)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


class TestContentSniffer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def test_pi_2port_basic(self):
        ts = parse_touchstone(FIX / "pi_2port.s2p")
        self.assertEqual(ts.nports, 2)
        self.assertEqual(ts.s.shape[1], 2)
        self.assertEqual(ts.s.shape[2], 2)
        self.assertEqual(ts.s.shape[0], len(ts.freqs))
        self.assertAlmostEqual(ts.z0, 50.0, places=6)

    def test_pi_2port_renamed_txt(self):
        """Renamed .txt extension still parses as 2-port via content sniffing."""
        ts = parse_touchstone(FIX / "pi_2port_renamed.txt")
        self.assertEqual(ts.nports, 2)
        self.assertEqual(ts.s.shape[1:], (2, 2))

    def test_diff_pair_4port_renamed_dat(self):
        """Renamed .dat extension still parses as 4-port via content sniffing."""
        ts = parse_touchstone(FIX / "diff_pair_4port_renamed.dat")
        self.assertEqual(ts.nports, 4)
        self.assertEqual(ts.s.shape[1:], (4, 4))

    def test_force_nports(self):
        """Forced override is honoured."""
        ts = parse_touchstone(FIX / "pi_2port.s2p", force_nports=2)
        self.assertEqual(ts.nports, 2)
        self.assertEqual(ts.s.shape[1:], (2, 2))

    def test_no_option_line_warns_and_parses(self):
        """File without the # option line should still parse with default options
        and produce a warning."""
        src = (FIX / "pi_2port.s2p").read_text(encoding="utf-8")
        # Remove the option line.
        kept = []
        for line in src.splitlines():
            if line.strip().startswith("#"):
                continue
            kept.append(line)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".s2p", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("\n".join(kept) + "\n")
            tmp = fh.name
        try:
            ts = parse_touchstone(tmp)
            self.assertEqual(ts.nports, 2)
            # A parser warning about the missing option line should be emitted.
            self.assertTrue(
                any("option line" in w.lower() or "no option" in w.lower()
                    for w in ts.parser_warnings),
                f"Expected warning about missing option line; got: "
                f"{ts.parser_warnings}",
            )
            # S values must be finite and bounded.
            self.assertTrue(np.all(np.isfinite(ts.s)))
            # Default assumption is GHZ MA; the original file used HZ RI.
            # We don't compare values numerically (different unit assumptions),
            # but we do check we got *some* sensible finite output.
            self.assertGreater(np.max(np.abs(ts.s)), 0.0)
        finally:
            os.unlink(tmp)

    def test_no_option_line_no_extension(self):
        """File with neither an option line nor a meaningful extension still parses."""
        src = (FIX / "pi_2port.s2p").read_text(encoding="utf-8")
        kept = []
        for line in src.splitlines():
            if line.strip().startswith("#"):
                continue
            kept.append(line)
        # Use a file with no extension at all.
        tmpdir = tempfile.mkdtemp()
        tmp = os.path.join(tmpdir, "no_extension_file")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + "\n")
            ts = parse_touchstone(tmp)
            self.assertEqual(ts.nports, 2)
            self.assertTrue(np.all(np.isfinite(ts.s)))
        finally:
            try:
                os.unlink(tmp)
                os.rmdir(tmpdir)
            except OSError:
                pass

    def test_garbage_raises(self):
        """A file containing only random text must raise a clear ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".s2p", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("this is not a touchstone file\n"
                     "lorem ipsum dolor sit amet\n"
                     "the quick brown fox jumps over the lazy dog\n")
            tmp = fh.name
        try:
            with self.assertRaises(ValueError):
                parse_touchstone(tmp)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
