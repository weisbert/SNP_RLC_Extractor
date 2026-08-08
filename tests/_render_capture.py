"""
_render_capture.py  --  Capture the CURRENT rendered text of the results table
and the mode-6 coupling block, byte for byte.

This is a SCRIPT plus a case registry, NOT a unittest module: the leading
underscore keeps it out of `unittest discover` (same convention as
_golden_capture.py and _smoke.py).

WHY IT EXISTS.  `_format_results_table` / `_format_coupling_block` used to read
their id / label / port descriptor off the LIVE `TraceConfig`, which is the
object `_on_calculate` overwrites on the next run.  They now read a frozen
`RowSnapshot` / `CouplingSnapshot` instead.  That refactor's whole acceptance
criterion is "the page does not move", and this file is what makes that claim
checkable rather than a promise: the reference in

    tests/fixtures/render_reference.json

was generated from the code as it stood BEFORE the snapshot types existed, and
tests/test_run_snapshot.py replays every case through the current API and
demands an exact string match.

Regenerate ONLY in the same commit that justifies moving the reference:

    python tests/_render_capture.py

A failure here means the rendered report changed.  If that was not intended,
fix the change -- do not regenerate the reference to make the test pass.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from pkg_rlc_core import (  # noqa: E402
    CouplingResult,
    PairCoupling,
    PortRLC,
    RLCResult,
)

REFERENCE_PATH = _ROOT / "tests" / "fixtures" / "render_reference.json"

NAN = float("nan")


# ---------------------------------------------------------------- case data
#
# Everything below is PLAIN DATA on purpose.  The registry must not import the
# snapshot dataclasses, or a change to them could quietly change what the
# reference is a reference to.

def _row(i, label, port_desc, file_label, R, L, C, Q, color_idx=0):
    return dict(id=i, label=label, port_desc=port_desc, file_label=file_label,
                color_idx=color_idx, R=R, L=L, C=C, Q=Q)


def _pair(a, b, ra, rb, M=2.1e-10, k=0.105, Cc=-5.0e-13, im=1.26,
          notes=()):
    return dict(name_a=a, name_b=b, M=M, k=k, Cc=Cc, im=im,
                M_over_La=ra, M_over_Lb=rb, notes=list(notes))


CASES = [
    # ---- results table --------------------------------------------------
    {
        "name": "table_single_smart",
        "kind": "table",
        "units": "smart",
        "rows": [_row(1, "tank", "M1: 1 -> GND", "coil.s4p",
                      1.5, 2.0e-9, -1.2e-12, 0.84)],
    },
    {
        "name": "table_single_aligned",
        "kind": "table",
        "units": "aligned",
        "rows": [_row(1, "tank", "M1: 1 -> GND", "coil.s4p",
                      1.5, 2.0e-9, -1.2e-12, 0.84)],
    },
    {
        "name": "table_three_smart",
        "kind": "table",
        "units": "smart",
        "rows": [
            _row(1, "tank", "M1: 1 -> GND", "coil.s4p",
                 1.5, 2.0e-9, -1.2e-12, 0.84, color_idx=0),
            _row(2, "a_very_long_trace_label_indeed",
                 "M5: probes 2 C:14 +txt", "coil.s4p",
                 -0.25, -3.3e-12, 4.7e-13, -12.5, color_idx=3),
            _row(17, "decap", "M2: 3 <-> 4  GND 5:1:9", "coil.s4p",
                 0.012, NAN, 1.0e-9, NAN, color_idx=11),
        ],
    },
    {
        "name": "table_three_aligned",
        "kind": "table",
        "units": "aligned",
        "rows": [
            _row(1, "tank", "M1: 1 -> GND", "coil.s4p",
                 1.5, 2.0e-9, -1.2e-12, 0.84, color_idx=0),
            _row(2, "a_very_long_trace_label_indeed",
                 "M5: probes 2 C:14 +txt", "coil.s4p",
                 -0.25, -3.3e-12, 4.7e-13, -12.5, color_idx=3),
            _row(17, "decap", "M2: 3 <-> 4  GND 5:1:9", "coil.s4p",
                 0.012, NAN, 1.0e-9, NAN, color_idx=11),
        ],
    },
    {
        "name": "table_multifile_smart",
        "kind": "table",
        "units": "smart",
        "rows": [
            _row(1, "before", "M1: 1 -> GND", "old.s4p",
                 1.5, 2.0e-9, -1.2e-12, 0.84),
            _row(2, "after", "M1: 1 -> GND", "new.s4p",
                 1.4, 2.1e-9, -1.1e-12, 0.91, color_idx=1),
        ],
    },
    {
        "name": "table_multifile_aligned",
        "kind": "table",
        "units": "aligned",
        "rows": [
            _row(1, "before", "M1: 1 -> GND", "old.s4p",
                 1.5, 2.0e-9, -1.2e-12, 0.84),
            _row(2, "after", "M1: 1 -> GND", "new.s4p",
                 1.4, 2.1e-9, -1.1e-12, 0.91, color_idx=1),
        ],
    },
    # ---- coupling block -------------------------------------------------
    {
        "name": "block_three_pairs_smart",
        "kind": "block",
        "units": "smart",
        "block": dict(
            id=4, label="osc", port_desc="M6: 3 mports",
            file_label="coil.s4p", names=["L1", "L2", "L3"],
            recip=1e-9,
            pairs=[_pair("L1", "L2", 0.10, 0.05),
                   _pair("L1", "L3", 0.50, 0.10, M=-4.2e-10, k=-0.21),
                   _pair("L2", "L3", 0.02, 0.02, im=-3.0)],
        ),
    },
    {
        "name": "block_three_pairs_aligned",
        "kind": "block",
        "units": "aligned",
        "block": dict(
            id=4, label="osc", port_desc="M6: 3 mports",
            file_label="coil.s4p", names=["L1", "L2", "L3"],
            recip=1e-9,
            pairs=[_pair("L1", "L2", 0.10, 0.05),
                   _pair("L1", "L3", 0.50, 0.10, M=-4.2e-10, k=-0.21),
                   _pair("L2", "L3", 0.02, 0.02, im=-3.0)],
        ),
    },
    {
        "name": "block_single_mport",
        "kind": "block",
        "units": "smart",
        "block": dict(
            id=4, label="osc", port_desc="M6: 1 mport",
            file_label="coil.s4p", names=["L1"], recip=0.0, pairs=[],
        ),
    },
    {
        "name": "block_folded_tail",
        "kind": "block",
        "units": "smart",
        "block": dict(
            id=9, label="pkg", port_desc="M6: 3 mports",
            file_label="pkg.s153p", names=["L1", "L2", "L3"],
            recip=4e-3,
            pairs=[_pair("L1", "L2", 0.50, 0.50),
                   _pair("L1", "L3", 1e-6, 1e-6),
                   _pair("L2", "L3", 1e-7, 1e-7)],
        ),
    },
    {
        "name": "block_nan_pair_and_notes",
        "kind": "block",
        "units": "smart",
        "block": dict(
            id=9, label="pkg", port_desc="M6: 3 mports",
            file_label="pkg.s153p", names=["L1", "L2", "L3"],
            recip=NAN,
            pairs=[_pair("L1", "L2", 0.5, 0.5, k=1.4,
                         notes=["|k| > 1: check the port setup"]),
                   _pair("L1", "L3", NAN, NAN, M=NAN, k=NAN, Cc=NAN, im=NAN)],
        ),
    },
]


# ------------------------------------------------------- building the inputs

def build_rlc(row) -> RLCResult:
    im = row["L"] * 2.0 * math.pi * 1e8 if math.isfinite(row["L"]) else NAN
    return RLCResult(freq_hz=1e8, Z=complex(row["R"], im), R_ohm=row["R"],
                     L_henry=row["L"], C_farad=row["C"], Q=row["Q"])


def build_cres(block) -> CouplingResult:
    names = list(block["names"])
    G = len(names)
    Zk = np.eye(G, dtype=complex) * complex(1.5, 1.26)
    ports = [PortRLC(name=n, Z=complex(1.5, 1.26), R_ohm=1.5, L_henry=2e-9,
                     C_farad=-1e-12, Q=0.84) for n in names]
    pairs = []
    for p in block["pairs"]:
        pairs.append(PairCoupling(
            name_a=p["name_a"], name_b=p["name_b"],
            Z_ab=complex(0.001, p["im"]),
            M_henry=p["M"], C_c_farad=p["Cc"], k=p["k"],
            M_over_La=p["M_over_La"], M_over_Lb=p["M_over_Lb"],
            M_over_La_dB=_db(p["M_over_La"]), M_over_Lb_dB=_db(p["M_over_Lb"]),
            notes=list(p["notes"])))
    return CouplingResult(freq_hz=1e8, Z_matrix=Zk, names=names, ports=ports,
                          pairs=pairs, reciprocity_error=block["recip"])


def _db(ratio: float) -> float:
    if not math.isfinite(ratio) or ratio == 0.0:
        return NAN
    return 20.0 * math.log10(abs(ratio))


# --------------------------------------------------------------- rendering
#
# THE ONE PLACE THAT KNOWS THE RENDERER'S SIGNATURE.  When the signature
# changes, this function changes and the reference file does not -- which is
# exactly what makes "the page did not move" a testable statement.

def render_case(case) -> str:
    import pkg_rlc_gui as gui

    if case["kind"] == "table":
        rows = [gui.RowSnapshot(id=r["id"], label=r["label"],
                                port_desc=r["port_desc"], enabled=True,
                                color_idx=r["color_idx"],
                                file_label=r["file_label"], res=build_rlc(r))
                for r in case["rows"]]
        return gui._format_results_table(rows, case["units"])

    b = case["block"]
    snap = gui.CouplingSnapshot(id=b["id"], label=b["label"],
                                port_desc=b["port_desc"], enabled=True,
                                color_idx=0, file_label=b["file_label"],
                                cres=build_cres(b))
    return gui._format_coupling_block(snap, case["units"])


def main() -> int:
    out = {c["name"]: render_case(c) for c in CASES}
    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the JSON container's own line breaks stay LF on every
    # platform.  The rendered text itself lives inside JSON string escapes and
    # is unaffected either way, but a reference file that flips its line
    # endings per machine is a diff nobody asked for.
    REFERENCE_PATH.write_text(
        json.dumps(out, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {len(out)} cases to {REFERENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
