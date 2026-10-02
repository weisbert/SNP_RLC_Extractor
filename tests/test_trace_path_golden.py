"""
The one row model computes what the old modes computed -- bit for bit.

`tests/fixtures/golden_trace_paths.npz` was captured by
`tests/_trace_path_capture.py` from the path the GUI ran BEFORE the editor's
modes were merged (TraceConfig -> the legacy migrations ->
run._build_termination -> compute_z_matrix), on 2026-10-02, for every old
mode on every fixture file.  Here every case is migrated into the two tables
(`TraceConfig.migrate_to_rows`) and solved through today's path, and each
case's EXPECTATION is asserted (docs/design_workspaces.md § 3.5):

    SAME      np.array_equal, NaN in the same places
    REFUSED   refused then, refused now
    CHANGED   the one intended change (a '-' side only partly grounded):
              the old number moved, and the new one equals the physical
              reading of the spec ('-' side wholly at GND) bit for bit
    ACCEPTED  Mode 6 refused a grounded '-' side; it is solved now, as the
              same physical reading

No Tk.  In the fast set.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import _trace_path_capture as cap  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow, MeasPortRow, build_terminations_mode2, compute_z_matrix,
    parse_touchstone)
from pkg_rlc.model.trace import FileEntry, TraceConfig  # noqa: E402
from pkg_rlc.services import run  # noqa: E402


def _solve(tc: TraceConfig, files):
    sn = run._trace_network(tc, files, {})
    term = run._build_termination(tc, nports=sn.nports, sn=sn)
    Zmat, names, _w = compute_z_matrix(sn.Y, sn.freqs, term)
    return Zmat, names


class TestEveryOldTraceComputesWhatItDid(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.files = cap.load_files()
        cls.npz = np.load(cap.TRACE_NPZ)
        cls.meta = json.loads(str(cls.npz["__meta__"]))

    def test_the_reference_covers_every_registered_case(self):
        keys = {c.key for c in cap.all_cases()}
        stored = set(self.meta) - {"__env__"}
        self.assertEqual(keys, stored)
        self.assertGreater(len(keys), 300)

    def test_each_case_meets_its_expectation(self):
        for case in cap.all_cases():
            with self.subTest(case=case.key, expect=case.expect):
                tc = cap.make_tc(case.tc, case.files)
                tc.migrate_to_rows()
                self.assertEqual(tc.mode, 5)
                old = self.meta[case.key]
                try:
                    Z, names = _solve(tc, self.files)
                    err = None
                except Exception as e:                  # noqa: BLE001
                    Z, err = None, str(e)
                if case.expect == cap.REFUSED:
                    self.assertIn("error", old)
                    self.assertIsNotNone(err, "refused before, solved now")
                    continue
                self.assertIsNone(err, err)
                if case.expect == cap.SAME:
                    self.assertNotIn("error", old)
                    self.assertTrue(
                        np.array_equal(Z, self.npz[f"z__{case.key}"],
                                       equal_nan=True),
                        "the migrated trace computes a different Zmat")
                    continue
                ptc = cap.make_tc(case.physics, case.files)
                ptc.migrate_to_rows()
                PZ, _n = _solve(ptc, self.files)
                self.assertTrue(np.array_equal(Z, PZ, equal_nan=True),
                                "not the physical reading of the spec")
                if case.expect == cap.CHANGED:
                    self.assertFalse(np.array_equal(
                        Z, self.npz[f"z__{case.key}"], equal_nan=True),
                        "the partial-ground fix did not move the number")
                else:
                    self.assertIn("error", old)

    def test_migration_is_idempotent_and_always_says_what_moved(self):
        for case in cap.all_cases():
            with self.subTest(case=case.key):
                tc = cap.make_tc(case.tc, case.files)
                was_old = tc.mode != 5 or case.tc.get("custom_text")
                notes = tc.migrate_to_rows()
                if was_old:
                    self.assertTrue(notes, "an old trace moved in silence")
                self.assertEqual(tc.migrate_to_rows(), [])
                self.assertEqual(
                    (tc.port_a, tc.port_b, tc.gnd_ports, tc.short_pairs,
                     tc.vdd_ports, tc.custom_text), ("",) * 6)


class TestThePartialGroundNoteNamesWhatChanged(unittest.TestCase):

    def test_old_mode2_partial_ground_says_the_number_moved(self):
        tc = TraceConfig(mode=2, port_a="1", port_b="3,4", gnd_ports="3")
        notes = tc.migrate_to_rows()
        moved = [n for n in notes if "port 4 is at GND too" in n
                 and "different number" in n]
        self.assertEqual(len(moved), 1, notes)
        self.assertEqual(moved[0].level, "warn")   # a number moved: look

    def test_a_wholly_grounded_minus_side_says_the_number_did_not(self):
        tc = TraceConfig(mode=2, port_a="1", port_b="3,4", gnd_ports="3,4")
        notes = tc.migrate_to_rows()
        same = [n for n in notes if "the same number as before" in n]
        self.assertEqual(len(same), 1, notes)
        self.assertEqual(same[0].level, "info")    # a record, not an alarm

    def test_a_plus_port_the_ground_won_is_dropped_explicitly(self):
        tc = TraceConfig(mode=1, port_a="1,2", gnd_ports="2,3")
        tc.migrate_to_rows()
        self.assertEqual(tc.mports[0].plus, "1")

    def test_a_plus_side_wholly_grounded_is_left_for_the_rules_to_refuse(self):
        tc = TraceConfig(mode=1, port_a="1", gnd_ports="1")
        tc.migrate_to_rows()
        self.assertEqual(tc.mports[0].plus, "1")
        with self.assertRaisesRegex(ValueError, "'\\+' side.*ground row"):
            run._build_termination(tc, nports=4)


class TestDifferentialIsSolvedDirectly(unittest.TestCase):
    """§ 3.5 item 5: the three spellings of a differential probe are the same
    array; and the 'two single-ended probes, combined by hand' spelling --
    which this tool deliberately does not offer -- is not."""

    FILES = ("diff_pair_4port.s4p", "decap_4port.s4p",
             "coupled_4port_float.s4p")

    def _fe(self, name):
        return FileEntry(parse_touchstone(str(cap.FIXTURE_DIR / name)))

    def test_mode2_rows_and_coupling_agree_bit_for_bit(self):
        for name in self.FILES:
            with self.subTest(file=name):
                fe = self._fe(name)
                gnd = [3, 4] if name.startswith("decap") else []
                old = compute_z_matrix(
                    fe.Y, fe.ts.freqs,
                    build_terminations_mode2([1], [2], gnd))[0]
                tc = TraceConfig(
                    mode=5, table_version=1,
                    mports=[MeasPortRow("P1", "1", "2")],
                    conn_rows=([ConnectionRow("ground", "3,4")]
                               if gnd else []))
                new = compute_z_matrix(
                    fe.Y, fe.ts.freqs,
                    run._build_termination(tc, nports=4))[0]
                self.assertTrue(np.array_equal(old, new, equal_nan=True))

    def test_combining_two_single_ended_probes_loses_precision(self):
        fe = self._fe("decap_4port.s4p")
        tc = TraceConfig(mode=5, table_version=1,
                         mports=[MeasPortRow("pp", "1", ""),
                                 MeasPortRow("mm", "2", "")],
                         conn_rows=[ConnectionRow("ground", "3,4")])
        Z2 = compute_z_matrix(fe.Y, fe.ts.freqs,
                              run._build_termination(tc, nports=4))[0]
        combined = Z2[:, 0, 0] + Z2[:, 1, 1] - Z2[:, 0, 1] - Z2[:, 1, 0]
        tc.mports = [MeasPortRow("d", "1", "2")]
        direct = compute_z_matrix(fe.Y, fe.ts.freqs,
                                  run._build_termination(tc, nports=4))[0]
        direct = direct[:, 0, 0]
        rel = np.nanmax(np.abs(combined - direct) / np.abs(direct))
        self.assertGreater(rel, 1e-10)       # measured 1.5e-8
        self.assertLess(rel, 1e-5)


if __name__ == "__main__":
    unittest.main()
