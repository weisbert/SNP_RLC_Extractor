"""
Golden regression: the CURRENT numeric behaviour of pkg_rlc_core, pinned
bit-for-bit.

tests/_golden_capture.py recorded, for every fixture in tests/fixtures/ and for
a list of representative Mode 1/2/3/4/5 termination cases:

    parse_touchstone(...).s   ->  s_to_y(s, z0)  ->  compute_z(Y, freqs, term)

into tests/fixtures/golden_legacy.npz. This module replays every recorded case
through the current public API and demands EXACT equality.

Why assert_array_equal and not assert_allclose: the acceptance criterion for the
work this reference guards is "existing modes stay bit-identical". A tolerance
would let a silent numeric drift through. assert_array_equal already treats
same-position NaNs as equal, which is the only leniency we want.

If this test fails, do NOT regenerate the .npz to make it pass. A failure means
the reduction path changed; either the change was unintended (fix it) or it was
deliberate and reviewed (then, and only then, re-run
`python tests/_golden_capture.py` in the same commit that justifies it).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import _golden_capture as gc  # noqa: E402

from pkg_rlc_core import compute_z, parse_touchstone, s_to_y  # noqa: E402


_REGEN_HINT = ("Run `python tests/_golden_capture.py` from the repo root to "
               "(re)create it -- but only if you intend to move the reference.")


def _env_now() -> dict[str, str]:
    """The same three fields _golden_capture stamps into the .npz."""
    import platform
    return {
        "numpy": np.__version__,
        "python": platform.python_version(),
        "platform": platform.system(),
    }


class TestGoldenRegression(unittest.TestCase):
    """Every captured case must reproduce bit-exactly."""

    @classmethod
    def setUpClass(cls) -> None:
        if not gc.GOLDEN_NPZ.exists():
            raise unittest.SkipTest(
                f"Golden reference {gc.GOLDEN_NPZ} is missing. {_REGEN_HINT}"
            )
        cls.data = np.load(gc.GOLDEN_NPZ, allow_pickle=False)
        cls.fixture_ids = [str(x) for x in cls.data[gc.FIXTURE_IDS_KEY]]
        cls.z_ids = [str(x) for x in cls.data[gc.Z_IDS_KEY]]
        try:
            cls.env = json.loads(str(cls.data[gc.ENV_KEY].item()))
        except (KeyError, ValueError):
            cls.env = {}

    @classmethod
    def tearDownClass(cls) -> None:
        close = getattr(cls, "data", None)
        if close is not None:
            close.close()

    # -- helpers ---------------------------------------------------------

    def _context(self, case_id: str) -> str:
        env = ", ".join(f"{k}={v}" for k, v in sorted(self.env.items()))
        here = ", ".join(f"{k}={v}" for k, v in sorted(_env_now().items()))
        drift = [k for k, v in sorted(self.env.items())
                 if _env_now().get(k) != v]
        if drift:
            hint = (
                f" NOTE: this machine [{here}] differs from the capture "
                f"environment in {', '.join(drift)}. These assertions compare "
                "the output of np.linalg.solve / np.linalg.inv, whose last "
                "bits depend on the LAPACK/BLAS build, so a mismatch of a few "
                "ulp here is a LAPACK difference and NOT necessarily a code "
                "regression. Diff the values before concluding anything, and "
                "do NOT regenerate the .npz on a machine that is not the one "
                "the reference belongs to."
            )
        else:
            hint = (" The capture environment matches this machine, so this "
                    "is a real behaviour change in pkg_rlc_core.")
        return (f"GOLDEN MISMATCH in case '{case_id}'. "
                f"Reference captured with [{env}] from {gc.GOLDEN_NPZ.name}."
                f"{hint}")

    def _fixture_path(self, case_id: str, filename: str) -> Path:
        path = gc.FIXTURE_DIR / filename
        if not path.exists():
            gc.ensure_fixtures()
        if not path.exists():
            self.fail(
                f"Case '{case_id}': fixture file '{filename}' is missing from "
                f"{gc.FIXTURE_DIR} and could not be regenerated."
            )
        return path

    # -- the tests -------------------------------------------------------

    def test_parse_touchstone_and_s_to_y_bit_exact(self) -> None:
        """Parsed S, freqs, metadata and Y must match the golden reference."""
        self.assertTrue(self.fixture_ids,
                        f"No fixture cases in {gc.GOLDEN_NPZ}. {_REGEN_HINT}")
        for case_id in self.fixture_ids:
            with self.subTest(case=case_id):
                meta = json.loads(str(self.data[gc.fx_key(case_id, "meta")].item()))
                path = self._fixture_path(case_id, meta["filename"])
                ctx = self._context(case_id)

                ts = parse_touchstone(path)
                self.assertEqual(ts.nports, meta["nports"], f"{ctx} (nports)")
                self.assertEqual(ts.z0, meta["z0"], f"{ctx} (z0)")
                self.assertEqual(list(ts.port_names), meta["port_names"],
                                 f"{ctx} (port_names)")
                self.assertEqual(list(ts.parser_warnings), meta["parser_warnings"],
                                 f"{ctx} (parser_warnings)")

                np.testing.assert_array_equal(
                    ts.freqs, self.data[gc.fx_key(case_id, "freqs")],
                    err_msg=f"{ctx} (freqs)")
                np.testing.assert_array_equal(
                    ts.s, self.data[gc.fx_key(case_id, "s")],
                    err_msg=f"{ctx} (parsed S)")

                Y = s_to_y(ts.s, ts.z0)
                np.testing.assert_array_equal(
                    Y, self.data[gc.fx_key(case_id, "y")],
                    err_msg=f"{ctx} (s_to_y)")

    def test_compute_z_bit_exact(self) -> None:
        """Z(f) for every recorded Mode 1/2/3/4/5 case must match exactly."""
        self.assertTrue(self.z_ids,
                        f"No compute_z cases in {gc.GOLDEN_NPZ}. {_REGEN_HINT}")
        registry = gc.z_case_by_id()
        for case_id in self.z_ids:
            with self.subTest(case=case_id):
                case = registry.get(case_id)
                if case is None:
                    self.fail(
                        f"Case '{case_id}' is in {gc.GOLDEN_NPZ.name} but no "
                        f"longer defined in _golden_capture.Z_CASES -- a golden "
                        f"case was deleted or renamed."
                    )
                ctx = self._context(case_id)
                path = self._fixture_path(case_id, case.fixture)

                ts = parse_touchstone(path)
                Y = s_to_y(ts.s, ts.z0)
                Z, warns = compute_z(Y, ts.freqs, case.build())

                np.testing.assert_array_equal(
                    ts.freqs, self.data[gc.z_key(case_id, "freqs")],
                    err_msg=f"{ctx} (freqs) [{case.describe}]")
                np.testing.assert_array_equal(
                    Z, self.data[gc.z_key(case_id, "Z")],
                    err_msg=f"{ctx} (Z) [{case.describe}]")
                self.assertEqual(
                    list(warns),
                    json.loads(str(self.data[gc.z_key(case_id, "warnings")].item())),
                    f"{ctx} (compute_z warnings) [{case.describe}]")

    def test_every_declared_case_is_captured(self) -> None:
        """Guards against a Z_CASES entry that was never written to the npz."""
        missing = [c.case_id for c in gc.Z_CASES if c.case_id not in self.z_ids]
        self.assertFalse(
            missing,
            f"compute_z cases declared in _golden_capture.Z_CASES but absent "
            f"from {gc.GOLDEN_NPZ.name}: {missing}. {_REGEN_HINT}"
        )

    def test_all_five_modes_are_covered(self) -> None:
        """The reference is only a safety net if it spans every named mode."""
        for prefix, label in [("m1_", "Mode 1 (signal->GND)"),
                              ("m2_", "Mode 2 (A<->B)"),
                              ("m3_", "Mode 3 (A<->B + short groups)"),
                              ("m4_", "Mode 4 (A<->B + VDD)"),
                              ("m5_", "Mode 5 (custom)")]:
            with self.subTest(mode=label):
                self.assertTrue(
                    any(cid.startswith(prefix) for cid in self.z_ids),
                    f"No golden case covers {label}."
                )


if __name__ == "__main__":
    unittest.main()
