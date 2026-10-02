"""
_trace_path_capture.py  --  a bit-exact reference of what every OLD-SHAPE
trace computed through the GUI's own path, captured BEFORE the editor's modes
were merged (docs/design_workspaces.md §3.5).

This is a SCRIPT plus a case registry, NOT a unittest module: the leading
underscore keeps it out of `unittest discover`, the same convention as
`_golden_capture.py`.

Why a second golden file.  `golden_legacy.npz` pins the L0 builders called
directly -- 20 cases, no `TraceConfig`, no Mode 6.  The merge rewrites the
path ABOVE them: a `TraceConfig` in mode 1 / 2 / 3 / 4 / 5 / 6 is migrated
into one row model and built by one builder.  Nothing pinned that path, and
the only test that compared the named modes with the rows used `< 1e-15`, not
equality.  So the reference is the path itself, as the GUI ran it on
2026-10-02:

    TraceConfig --(the three legacy migrations App._migrate_trace runs)-->
    run._build_termination(tc, nports=, sn=) --> compute_z_matrix

and what is stored per case is the WHOLE `Zmat` (the single-port GUI path is
`compute_z`, which is `Zmat[:, 0, 0]`), the measurement-port names, and -- for
a case the old code refused -- the refusal text.

Each case carries an EXPECTATION, which is what the post-merge replay
(`tests/test_trace_path_golden.py`) asserts:

    SAME      the migrated trace computes an `np.array_equal` Zmat (NaN in the
              same places).  Every case is this unless it says otherwise.
    REFUSED   the old path refused it and the new one must refuse it too.
    CHANGED   the one intended numeric change: a '-' side only PARTLY in the
              ground list.  The old code dropped the grounded port from the
              probe ("ground wins"); the new rule grounds the whole tied side.
              The stored array is the OLD (wrong) number, so the replay can
              assert it moved, and `physics` names a SAME-path spec the new
              number must equal bit for bit.
    ACCEPTED  the old path refused it (Mode 6 refused any probe port in GND)
              and the new rule accepts it, because the port is on the '-'
              side.  `physics` names the spec it must equal.

THE CAPTURE MUST RUN AGAINST THE PRE-MERGE CODE, and it refuses otherwise.
After the merge `run._build_termination` IS the new path, so a capture run
on today's tree would quietly store the new numbers as the "old" ones and
the CHANGED cases would compare the fix with itself.  Point it at an export
of the last pre-merge commit (29b4774):

    git archive 29b4774 pkg_rlc | tar -x -C <dir>
    TRACE_CAPTURE_TREE=<dir> python tests/_trace_path_capture.py

The replay (`test_trace_path_golden`) imports only the registry from here
and never calls `old_path`.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

import os

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if __name__ == "__main__" and os.environ.get("TRACE_CAPTURE_TREE"):
    # The pre-merge package, ahead of this checkout's -- see the docstring.
    sys.path.insert(0, os.environ["TRACE_CAPTURE_TREE"])

from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow, MeasPortRow, compute_z_matrix, parse_touchstone)
from pkg_rlc.model.trace import FileEntry, TraceConfig  # noqa: E402

FIXTURE_DIR = _HERE / "fixtures"
TRACE_NPZ = FIXTURE_DIR / "golden_trace_paths.npz"

SAME, REFUSED, CHANGED, ACCEPTED = "same", "refused", "changed", "accepted"

ONE_PORT = ("shunt_rl_1port.s1p", "shunt_c_1port.s1p")
TWO_PORT = ("pi_2port.s2p", "pi_2port_renamed.txt",
            "coupled_2port_gndref.s2p", "coupled_2port_negM.s2p")
FOUR_PORT = ("diff_pair_4port.s4p", "diff_pair_4port_renamed.dat",
             "decap_4port.s4p", "coupled_4port_diff.s4p",
             "coupled_4port_float.s4p")


@dataclass
class Case:
    """One old-shape trace on one file set.

    `files` is HOME FIRST; more than one makes it a composed trace.  `tc` is
    the TraceConfig keyword set exactly as an old session would carry it,
    with `mports` / `conn_rows` as plain tuples so the registry stays data.
    """
    key: str
    files: tuple
    tc: dict
    expect: str = SAME
    physics: dict = field(default_factory=dict)   # CHANGED / ACCEPTED only
    note: str = ""


def _mp(*rows):
    return [MeasPortRow(*r) for r in rows]


def _cr(*rows):
    return [replace(r) if isinstance(r, ConnectionRow) else ConnectionRow(*r)
            for r in rows]


def make_tc(spec: dict, files: tuple) -> TraceConfig:
    """A fresh TraceConfig from a registry dict (rows rebuilt every call, so
    no two replays share a list)."""
    kw = dict(spec)
    if "mports" in kw:
        kw["mports"] = _mp(*kw["mports"])
    if "conn_rows" in kw:
        kw["conn_rows"] = _cr(*kw["conn_rows"])
    tc = TraceConfig(id=1, label="golden", file_label=files[0], **kw)
    if len(files) > 1:
        tc.file_labels = list(files)
    return tc


def _cases_for(fname: str, nports: int) -> list[Case]:
    f = (fname,)
    s = fname.replace(".", "_")
    out: list[Case] = []

    def add(tag, tc, **kw):
        out.append(Case(f"{s}__{tag}", f, tc, **kw))

    # Every trace below 5 / 6 carries mports=[] and the dataclass default
    # port_a="1": the stale-field cases are the ones that set the OTHER
    # mode's fields on purpose.
    add("m1_p1", dict(mode=1, port_a="1"))
    add("m5_p1", dict(mode=5, mports=[("P1", "1", "")]))
    add("m6_p1", dict(mode=6, mports=[("P1", "1", "")]))
    add("m1_gnd_own", dict(mode=1, port_a="1", gnd_ports="1"),
        expect=REFUSED, note="the only + port is grounded")
    if nports == 1:
        return out

    add("m1_gnd2", dict(mode=1, port_a="1", gnd_ports="2"))
    add("m1_spaces", dict(mode=1, port_a=" 1 ", gnd_ports=" 2 "))
    add("m2_12", dict(mode=2, port_a="1", port_b="2"))
    add("m2_21", dict(mode=2, port_a="2", port_b="1"))
    add("m2_minus_all_gnd", dict(mode=2, port_a="1", port_b="2",
                                 gnd_ports="2"),
        note="the whole - side is in GND: the new rule is the same spec")
    add("m3_short_only", dict(mode=3, port_a="1", port_b="", short_pairs="1-2"),
        expect=SAME, note="+1 tied to 2, the far side open")
    add("m6_two", dict(mode=6, mports=[("c1", "1", ""), ("c2", "2", "")]))
    add("m6_diff", dict(mode=6, mports=[("d", "1", "2")]))
    add("m6_plus_in_gnd", dict(mode=6, mports=[("c1", "1", "")],
                               gnd_ports="1"), expect=REFUSED)
    add("m6_minus_in_gnd", dict(mode=6, mports=[("d", "1", "2")],
                                gnd_ports="2"),
        expect=ACCEPTED, physics=dict(mode=1, port_a="1", gnd_ports="2"),
        note="Mode 6 refused it; the - side is grounded, so it is P1 to GND")
    add("m5_rows_gnd", dict(mode=5, mports=[("P1", "1", "")],
                            conn_rows=[("ground", "2")]))
    add("m5_stale_fields", dict(mode=5, mports=[("P1", "2", "")],
                                port_a="1", port_b="2", gnd_ports="2",
                                short_pairs="1-2"),
        note="hidden mode-1/2/3 fields must not leak into the rows")
    add("m6_stale_fields", dict(mode=6, mports=[("c1", "2", "")],
                                port_a="1", port_b="2", short_pairs="1-2"),
        note="hidden fields; GND is LIVE in mode 6 and is empty here")
    add("m1_stale_mports", dict(mode=1, port_a="2",
                                mports=[("x", "1", "")],
                                conn_rows=[("ground", "1")]),
        note="hidden table rows must not leak into a mode-1 trace")
    add("m5_custom_text", dict(mode=5, custom_text="1 signal A\n2 ground\n"))
    add("m6_legacy_mp1", dict(mode=6, mp1_name="c1", mp1_plus="1",
                              mp2_name="c2", mp2_plus="2"))
    if nports == 2:
        return out

    add("m1_gnd34", dict(mode=1, port_a="1", gnd_ports="3,4"))
    add("m1_gnd_range_spaces", dict(mode=1, port_a="1", gnd_ports="3 - 4"))
    add("m1_gnd_list_spaces", dict(mode=1, port_a="1, 2", gnd_ports="3, 4"))
    add("m1_plus_partly_gnd", dict(mode=1, port_a="1,2", gnd_ports="2,3"),
        note="old 'ground wins' dropped port 2 from the probe; the migration "
             "drops it explicitly, so the number is the same")
    add("m2_12_gnd34", dict(mode=2, port_a="1", port_b="2", gnd_ports="3,4"))
    add("m2_13", dict(mode=2, port_a="1", port_b="3"))
    add("m2_pair", dict(mode=2, port_a="1,2", port_b="3,4"))
    add("m2_minus_all_gnd34", dict(mode=2, port_a="1", port_b="3,4",
                                   gnd_ports="3,4"))
    add("m2_minus_part_gnd", dict(mode=2, port_a="1", port_b="3,4",
                                  gnd_ports="3"),
        expect=CHANGED,
        physics=dict(mode=5, mports=[("P1", "1", "")],
                     conn_rows=[("ground", "3"), ("short", "3", "4")]),
        note="the - side 3,4 is tied; grounding 3 grounds 4 too")
    add("m2_plus_gnd_empty", dict(mode=2, port_a="1", port_b="2",
                                  gnd_ports="1"), expect=REFUSED)
    add("m3_12_s34", dict(mode=3, port_a="1", port_b="2", short_pairs="3-4"))
    add("m3_spaces", dict(mode=3, port_a="1", port_b="2",
                          short_pairs="3 - 4", gnd_ports=""))
    add("m3_group", dict(mode=3, port_a="1", port_b="", short_pairs="2-3-4"))
    add("m3_two_pairs", dict(mode=3, port_a="1", port_b="", gnd_ports="",
                             short_pairs="1-3, 2-4"))
    add("m3_gnd", dict(mode=3, port_a="1", port_b="2", gnd_ports="4",
                       short_pairs="3-4"))
    add("m4_vdd", dict(mode=4, port_a="1", port_b="2", gnd_ports="3",
                       vdd_ports="4"))
    add("m6_two_diff", dict(mode=6, mports=[("dq", "1", "2"),
                                            ("ck", "3", "4")]))
    add("m6_in_out_gnd", dict(mode=6, mports=[("in", "1", ""),
                                              ("out", "3", "")],
                              gnd_ports="2,4"))
    add("m6_out_first", dict(mode=6, mports=[("out", "3", ""),
                                             ("in", "1", "")]))
    add("m6_range", dict(mode=6, mports=[("p", "1:1:2", "3-4")]))
    add("m6_unnamed", dict(mode=6, mports=[("", "1", ""), ("", "2", "")]))
    add("m6_shared_port", dict(mode=6, mports=[("a1", "1", ""),
                                               ("a2", "1", "2")]),
        expect=REFUSED)
    add("m6_minus_part_gnd", dict(mode=6, mports=[("d", "1", "3,4")],
                                  gnd_ports="3"),
        expect=ACCEPTED,
        physics=dict(mode=5, mports=[("P1", "1", "")],
                     conn_rows=[("ground", "3"), ("short", "3", "4")]))
    add("m5_rows_full", dict(
        mode=5, mports=[("P1", "1", "2")],
        conn_rows=[("ground", "4"), ("short", "3", "4"),
                   ("rlc_gnd", "3", "", "50", "", "")]))
    add("m5_rlc_between", dict(
        mode=5, mports=[("P1", "1", "")],
        conn_rows=[("rlc_between", "2", "3", "", "1n", ""),
                   ("ground", "3,4")]))
    add("m5_net", dict(
        mode=5, mports=[("P1", "1", "")],
        conn_rows=[ConnectionRow("short", "2,3", "", "", "", "", "tap"),
                   ("rlc_between", "tap", "4", "1", "", "")]))
    add("m5_disabled_row", dict(
        mode=5, mports=[("P1", "1", "")],
        conn_rows=[ConnectionRow("ground", "3,4", enabled=False)]))
    add("m5_extra_lines", dict(mode=5, mports=[("P1", "1", "")],
                               extra_lines="# a note\n3 ground"))
    add("m5_two_probes", dict(mode=5, mports=[("in", "1", ""),
                                              ("out", "3", "")],
                              conn_rows=[("ground", "2,4")]))
    add("m5_plus_in_gnd_row", dict(mode=5, mports=[("P1", "1,2", "")],
                                   conn_rows=[("ground", "2")]),
        note="Mode 5 let the ground row win; the migration drops 2 from +")
    add("m5_minus_all_gnd", dict(mode=5, mports=[("P1", "1", "3,4")],
                                 conn_rows=[("ground", "3,4")]))
    add("m5_minus_part_gnd", dict(mode=5, mports=[("P1", "1", "3,4")],
                                  conn_rows=[("ground", "3")]),
        expect=CHANGED,
        physics=dict(mode=5, mports=[("P1", "1", "")],
                     conn_rows=[("ground", "3"), ("short", "3", "4")]))
    add("m5_custom_text_order", dict(
        mode=5, custom_text="3 ground\n3 signal A\n4 signal B\n"),
        note="order-dependent free text is kept verbatim as extra_lines")
    add("m5_out_of_range", dict(mode=5, mports=[("P1", "9", "")]),
        expect=REFUSED)
    # Added after the merge's adversarial review (2026-10-02), captured from
    # 29b4774 like everything else here.
    add("m2_a_and_b_overlap", dict(mode=2, port_a="1,2", port_b="2"),
        note="the old builder let B win: +1 -2")
    add("m2_a_and_b_overlap_gnd", dict(mode=2, port_a="1,2", port_b="2",
                                       gnd_ports="3,4"))
    add("m5_rows_both_sides", dict(mode=5, mports=[("P1", "1,2", "2")]),
        note="the DSL's later '-' line won")
    add("m5_vdd_minus_part", dict(mode=5, mports=[("P1", "1", "3,4")],
                                  conn_rows=[("vdd", "3")]),
        expect=CHANGED, physics=dict(mode=1, port_a="1", gnd_ports="3,4"))
    add("m5_legacy_a_b_rows", dict(mode=5, mports=[("A", "1", ""),
                                                   ("B", "2", "")]),
        note="hand-typed A and B rows were one probe")
    add("m6_name_a", dict(mode=6, mports=[("a", "1", "")]), expect=REFUSED)
    add("m6_names_with_space", dict(mode=6, mports=[("my port", "1", ""),
                                                    ("myport", "2", "")]))
    add("m1_out_of_range", dict(mode=1, port_a="9"), expect=REFUSED)
    return out


def _composed_cases() -> list[Case]:
    files = ("pi_2port.s2p", "pi_2port_renamed.txt")
    return [
        Case("composed__m1", files, dict(mode=1, port_a="1",
                                         gnd_ports="F2.2")),
        Case("composed__m2_tagged", files, dict(mode=2, port_a="1",
                                                port_b="F2.1")),
        Case("composed__m5_short", files, dict(
            mode=5, mports=[("P1", "1", "")],
            conn_rows=[("short", "2", "F2.1"), ("ground", "F2.2")])),
        Case("composed__m6", files, dict(
            mode=6, mports=[("in", "1", ""), ("out", "F2.2", "")],
            gnd_ports="")),
        Case("composed__m1_tagged_plus_grounded", files, dict(
            mode=1, port_a="1,F2.2", gnd_ports="F2.2")),
        Case("composed__m5_tagged_plus_grounded", files, dict(
            mode=5, mports=[("P1", "1,F2.2", "")],
            conn_rows=[("ground", "F2.2")])),
        Case("composed__m2_tagged_minus_part", files, dict(
            mode=2, port_a="1", port_b="2,F2.1", gnd_ports="F2.1"),
            expect=CHANGED,
            physics=dict(mode=1, port_a="1", gnd_ports="2,F2.1")),
        Case("composed__m3_bare_past_home", files, dict(
            mode=3, port_a="1", port_b="", short_pairs="2-3"),
            expect=REFUSED,
            note="a bare port past the home file is refused, not the next "
                 "file's port"),
    ]


def all_cases() -> list[Case]:
    out: list[Case] = []
    for group, n in ((ONE_PORT, 1), (TWO_PORT, 2), (FOUR_PORT, 4)):
        for fname in group:
            out.extend(_cases_for(fname, n))
    out.extend(_composed_cases())
    return out


def load_files() -> list:
    return [FileEntry(parse_touchstone(str(FIXTURE_DIR / name)))
            for name in sorted({f for c in all_cases() for f in c.files})]


# ---------------------------------------------------------------------------
# The OLD path, frozen here so the capture does not depend on App
# ---------------------------------------------------------------------------

def old_path(tc: TraceConfig, files: list):
    """(Zmat, names) through the path the GUI ran on 2026-10-02, or raises.

    The three migrations are App._migrate_trace's, minus its log lines; the
    build and the solve are run._build_termination and compute_z_matrix.
    """
    from pkg_rlc.services import run
    tc.migrate_legacy_mode()
    tc.migrate_legacy_mports()
    tc.migrate_legacy_custom_text()
    sn = run._trace_network(tc, files, {})
    term = run._build_termination(tc, nports=sn.nports, sn=sn)
    Zmat, names, _w = compute_z_matrix(sn.Y, sn.freqs, term)
    return Zmat, list(names)


def capture() -> None:
    from dataclasses import fields as _fields
    if any(f.name == "table_version" for f in _fields(TraceConfig)):
        raise SystemExit(
            "refusing to capture: this pkg_rlc is the merged one (TraceConfig "
            "has table_version), so 'old_path' would record the NEW numbers. "
            "Set TRACE_CAPTURE_TREE to an export of 29b4774 -- see the "
            "module docstring.")
    files = load_files()
    arrays: dict = {}
    meta: dict = {}
    for case in all_cases():
        try:
            Zmat, names = old_path(make_tc(case.tc, case.files), files)
        except Exception as e:                      # noqa: BLE001
            meta[case.key] = {"error": f"{type(e).__name__}: {e}"}
            continue
        arrays[f"z__{case.key}"] = Zmat
        meta[case.key] = {"names": names}
    meta["__env__"] = {"numpy": np.__version__,
                       "python": platform.python_version(),
                       "platform": platform.platform()}
    arrays["__meta__"] = np.array(json.dumps(meta, sort_keys=True))
    np.savez_compressed(TRACE_NPZ, **arrays)

    n_err = sum(1 for k, v in meta.items() if k != "__env__" and "error" in v)
    print(f"{len(meta) - 1} cases -> {TRACE_NPZ.name}: "
          f"{len(meta) - 1 - n_err} solved, {n_err} refused")
    for case in all_cases():
        got = meta[case.key]
        refused = "error" in got
        if (case.expect == REFUSED) != refused and case.expect != ACCEPTED:
            print(f"  UNEXPECTED {case.key}: expect={case.expect} got={got}")
        if case.expect == ACCEPTED and not refused:
            print(f"  UNEXPECTED {case.key}: old path accepted it")


if __name__ == "__main__":
    capture()
