"""
_golden_capture.py  --  Capture a bit-exact golden reference of the CURRENT
behaviour of pkg_rlc_core.

This is a SCRIPT plus a case registry, NOT a unittest module: the leading
underscore keeps it out of `unittest discover` (same convention as _smoke.py).

What it records, for every fixture in tests/fixtures/ and for a hand-written
list of representative termination cases:

    * parse_touchstone(...).s      -- the parsed S array, exactly
    * s_to_y(s, z0)                -- the Y array, exactly
    * compute_z(Y, freqs, term)    -- the Z array, exactly
    * the freqs arrays, plus parser/compute warnings and file metadata

Everything is written to tests/fixtures/golden_legacy.npz (np.savez_compressed,
so the arrays round-trip bit-for-bit).

Run it to (re)generate the reference:

    python tests/_golden_capture.py

tests/test_golden_regression.py imports the case registry from this module and
replays every captured case through the current public API, asserting EXACT
equality. Regenerate the .npz only when a numeric behaviour change is intended
and reviewed -- the whole point of the file is that it does not move.

Adding a new fixture file to tests/fixtures/ does not break the regression test
(the test iterates the cases stored in the .npz); it simply is not covered until
the reference is regenerated. Deleting or renaming an existing fixture, or
removing a Z_CASES entry, is a loud failure by design.
"""

from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pkg_rlc.physics.core import (  # noqa: E402
    LumpedBetween,
    LumpedToGnd,
    Open,
    ShortPair,
    Signal,
    TerminationSet,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    compute_z,
    parse_short_pairs,
    parse_touchstone,
    s_to_y,
    y_capacitor,
    y_series_rlc,
)

# ============================================================================
# Locations
# ============================================================================

FIXTURE_DIR = _HERE / "fixtures"
GOLDEN_NPZ = FIXTURE_DIR / "golden_legacy.npz"

# Files in tests/fixtures/ that are not Touchstone data.
_SKIP_SUFFIXES = {".npz", ".npy"}

# Fixtures that tests/generate_test_snp.py knows how to (re)create.
GENERATED_FIXTURES = [
    "shunt_rl_1port.s1p",
    "shunt_c_1port.s1p",
    "pi_2port.s2p",
    "diff_pair_4port.s4p",
    "decap_4port.s4p",
    "pi_2port_renamed.txt",
    "diff_pair_4port_renamed.dat",
]


# ============================================================================
# Key naming (npz keys must be plain ASCII so the zip members stay portable)
# ============================================================================

def sanitize(name: str) -> str:
    """'pi_2port.s2p' -> 'pi_2port_s2p' (npz-key-safe)."""
    return re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")


def fixture_case_id(filename: str) -> str:
    return "fx_" + sanitize(filename)


def fx_key(case_id: str, field: str) -> str:
    return f"fx__{case_id}__{field}"


def z_key(case_id: str, field: str) -> str:
    return f"z__{case_id}__{field}"


FIXTURE_IDS_KEY = "__fixture_case_ids__"
Z_IDS_KEY = "__z_case_ids__"
ENV_KEY = "__env__"


def discover_fixture_files() -> list[Path]:
    """All Touchstone-ish files in tests/fixtures/, sorted by name."""
    if not FIXTURE_DIR.is_dir():
        return []
    return sorted(
        (p for p in FIXTURE_DIR.iterdir()
         if p.is_file()
         and not p.name.startswith(".")
         and p.suffix.lower() not in _SKIP_SUFFIXES),
        key=lambda p: p.name,
    )


def ensure_fixtures() -> None:
    """Regenerate the synthetic fixtures if any of them is missing."""
    if all((FIXTURE_DIR / n).exists() for n in GENERATED_FIXTURES):
        return
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import generate_test_snp  # type: ignore  # noqa: E402
    generate_test_snp.main()


# ============================================================================
# compute_z cases  (1-based port numbers, exactly as a user would type them)
# ============================================================================

@dataclass(frozen=True)
class ZCase:
    case_id: str
    fixture: str                       # filename inside tests/fixtures/
    describe: str
    build: Callable[[], TerminationSet]


def _m5_decap_lumped() -> TerminationSet:
    """Mode 5 (custom): lumped_to_gnd on port 3 + lumped_between 3 and 4.

    Mirrors the Help -> Mode 5 worked example:
        1 signal A
        2 signal B
        3 lumped_to_gnd R=50
        3 lumped_between 4 R=0.01 L=0.1n C=1p
        4 open
    Port indices below are 0-based because this is the core-side API.
    """
    return TerminationSet(
        per_port={
            0: Signal("A"),
            1: Signal("B"),
            2: LumpedToGnd(y_series_rlc(R=50.0)),
            3: Open(),
        },
        couplings=[
            LumpedBetween(2, 3, y_series_rlc(R=0.01, L=0.1e-9, C=1e-12)),
        ],
    )


def _m5_diff_lumped_short() -> TerminationSet:
    """Mode 5 (custom): two lumped_to_gnd ports tied by a short + a
    lumped_between across the two signal ports.

    Exercises the merge_terms() branch that combines several LumpedToGnd
    admittances into one merged port, and the LumpedBetween stamp on the
    original (pre-merge) port indices.
    """
    return TerminationSet(
        per_port={
            0: Signal("A"),
            1: Signal("B"),
            2: LumpedToGnd(y_series_rlc(R=0.5, L=1.5e-9)),
            3: LumpedToGnd(y_capacitor(2e-12)),
        },
        couplings=[
            ShortPair(2, 3),
            LumpedBetween(0, 1, y_series_rlc(C=1e-12)),
        ],
    )


Z_CASES: list[ZCase] = [
    # ---- Mode 1: signal -> GND (driving point) -----------------------------
    ZCase("m1_shunt_rl_p1", "shunt_rl_1port.s1p",
          "mode1 A=[1], no gnd (1-port, no reduction)",
          lambda: build_terminations_mode1([1], [])),
    ZCase("m1_shunt_c_p1", "shunt_c_1port.s1p",
          "mode1 A=[1], no gnd (1-port capacitive)",
          lambda: build_terminations_mode1([1], [])),
    ZCase("m1_pi_p1_open2", "pi_2port.s2p",
          "mode1 A=[1], port 2 left open -> Schur eliminates 1 port",
          lambda: build_terminations_mode1([1], [])),
    ZCase("m1_pi_p1_gnd2", "pi_2port.s2p",
          "mode1 A=[1], gnd=[2] -> ground row/col dropped",
          lambda: build_terminations_mode1([1], [2])),
    ZCase("m1_diff_p1_gnd234", "diff_pair_4port.s4p",
          "mode1 A=[1], gnd=[2,3,4]",
          lambda: build_terminations_mode1([1], [2, 3, 4])),
    ZCase("m1_diff_a12_open34", "diff_pair_4port.s4p",
          "mode1 A=[1,2] (multi-port signal group), 3/4 open -> 2-port Schur",
          lambda: build_terminations_mode1([1, 2], [])),
    ZCase("m1_decap_a12_gnd3", "decap_4port.s4p",
          "mode1 A=[1,2], gnd=[3], port 4 open (ground drop + Schur)",
          lambda: build_terminations_mode1([1, 2], [3])),

    # ---- Mode 2: A <-> B ---------------------------------------------------
    ZCase("m2_pi_a1_b2", "pi_2port.s2p",
          "mode2 A=[1], B=[2], no gnd",
          lambda: build_terminations_mode2([1], [2], [])),
    ZCase("m2_diff_a1_b2_gnd34", "diff_pair_4port.s4p",
          "mode2 A=[1], B=[2], gnd=[3,4]",
          lambda: build_terminations_mode2([1], [2], [3, 4])),
    ZCase("m2_diff_a1_b2_open34", "diff_pair_4port.s4p",
          "mode2 A=[1], B=[2], 3/4 open -> Schur",
          lambda: build_terminations_mode2([1], [2], [])),
    ZCase("m2_diff_a12_b34", "diff_pair_4port.s4p",
          "mode2 A=[1,2], B=[3,4] (both groups multi-port)",
          lambda: build_terminations_mode2([1, 2], [3, 4], [])),
    ZCase("m2_decap_a1_b2_open34", "decap_4port.s4p",
          "mode2 A=[1], B=[2], decap branch 3/4 left open",
          lambda: build_terminations_mode2([1], [2], [])),

    # ---- Mode 3: A <-> B + short groups ------------------------------------
    ZCase("m3_diff_a1_b2_short34", "diff_pair_4port.s4p",
          "mode3 A=[1], B=[2], short '3-4' -> differential loop L",
          lambda: build_terminations_mode3([1], [2], [],
                                           parse_short_pairs("3-4"))),
    ZCase("m3_diff_a1_b4_short123", "diff_pair_4port.s4p",
          "mode3 A=[1], B=[4], short '1-2-3' (3-port group, chained pairs)",
          lambda: build_terminations_mode3([1], [4], [],
                                           parse_short_pairs("1-2-3"))),
    ZCase("m3_decap_a1_b2_gnd4_short34", "decap_4port.s4p",
          "mode3 A=[1], B=[2], gnd=[4], short '3-4' -> merged group is Ground",
          lambda: build_terminations_mode3([1], [2], [4],
                                           parse_short_pairs("3-4"))),
    ZCase("m3_decap_a1_b2_short234", "decap_4port.s4p",
          "mode3 A=[1], B=[2], short '2-3-4' -> merged group inherits Signal B",
          lambda: build_terminations_mode3([1], [2], [],
                                           parse_short_pairs("2-3-4"))),

    # ---- Mode 4: A <-> B + VDD ---------------------------------------------
    ZCase("m4_diff_a1_b2_vdd34", "diff_pair_4port.s4p",
          "mode4 A=[1], B=[2], vdd=[3,4] (AC ground)",
          lambda: build_terminations_mode4([1], [2], [], [3, 4])),
    ZCase("m4_decap_a1_b2_gnd3_vdd4", "decap_4port.s4p",
          "mode4 A=[1], B=[2], gnd=[3], vdd=[4]",
          lambda: build_terminations_mode4([1], [2], [3], [4])),

    # ---- Mode 5: custom termination sets -----------------------------------
    ZCase("m5_decap_lumped", "decap_4port.s4p",
          "mode5 custom: lumped_to_gnd R=50 on port 3 + "
          "lumped_between 3-4 R=0.01 L=0.1n C=1p",
          _m5_decap_lumped),
    ZCase("m5_diff_lumped_short", "diff_pair_4port.s4p",
          "mode5 custom: two lumped_to_gnd ports shorted together + "
          "lumped_between across the signal ports",
          _m5_diff_lumped_short),
]


def z_case_by_id() -> dict[str, ZCase]:
    return {c.case_id: c for c in Z_CASES}


# ============================================================================
# Capture
# ============================================================================

def _meta_json(ts, filename: str) -> str:
    return json.dumps(
        {
            "filename": filename,
            "nports": int(ts.nports),
            "z0": float(ts.z0),
            "port_names": list(ts.port_names),
            "parser_warnings": list(ts.parser_warnings),
        },
        sort_keys=True,
    )


def build_golden() -> dict[str, np.ndarray]:
    """Recompute everything and return the dict that goes into the .npz."""
    out: dict[str, np.ndarray] = {}

    fixture_ids: list[str] = []
    parsed: dict[str, object] = {}
    for path in discover_fixture_files():
        case_id = fixture_case_id(path.name)
        ts = parse_touchstone(path)
        Y = s_to_y(ts.s, ts.z0)
        parsed[path.name] = (ts, Y)
        out[fx_key(case_id, "s")] = ts.s
        out[fx_key(case_id, "freqs")] = ts.freqs
        out[fx_key(case_id, "y")] = Y
        out[fx_key(case_id, "meta")] = np.array(_meta_json(ts, path.name))
        fixture_ids.append(case_id)

    z_ids: list[str] = []
    for case in Z_CASES:
        if case.fixture not in parsed:
            raise FileNotFoundError(
                f"Z case '{case.case_id}' needs fixture '{case.fixture}', "
                f"which is not in {FIXTURE_DIR}"
            )
        ts, Y = parsed[case.fixture]  # type: ignore[misc]
        Z, warns = compute_z(Y, ts.freqs, case.build())
        out[z_key(case.case_id, "Z")] = Z
        out[z_key(case.case_id, "freqs")] = ts.freqs
        out[z_key(case.case_id, "warnings")] = np.array(json.dumps(list(warns)))
        out[z_key(case.case_id, "describe")] = np.array(case.describe)
        out[z_key(case.case_id, "fixture")] = np.array(case.fixture)
        z_ids.append(case.case_id)

    out[FIXTURE_IDS_KEY] = np.array(fixture_ids)
    out[Z_IDS_KEY] = np.array(z_ids)
    out[ENV_KEY] = np.array(json.dumps({
        "numpy": np.__version__,
        "python": platform.python_version(),
        "platform": platform.system(),
    }, sort_keys=True))
    return out


def main() -> int:
    ensure_fixtures()
    files = discover_fixture_files()
    if not files:
        print(f"ERROR: no fixture files found in {FIXTURE_DIR}", file=sys.stderr)
        return 1

    payload = build_golden()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(GOLDEN_NPZ, **payload)

    print(f"Wrote {GOLDEN_NPZ}  ({GOLDEN_NPZ.stat().st_size / 1024:.1f} KiB)")
    print(f"\nParse / s_to_y cases ({len(files)}):")
    for path in files:
        case_id = fixture_case_id(path.name)
        s = payload[fx_key(case_id, "s")]
        print(f"  {case_id:<34} {path.name}  s{s.shape}")
    print(f"\ncompute_z cases ({len(Z_CASES)}):")
    for case in Z_CASES:
        Z = payload[z_key(case.case_id, "Z")]
        bad = int(np.count_nonzero(~np.isfinite(Z)))
        flag = f"  [WARNING: {bad} non-finite]" if bad else ""
        print(f"  {case.case_id:<34} {case.fixture:<28} "
              f"|Z|[0]={abs(Z[0]):.6g}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
