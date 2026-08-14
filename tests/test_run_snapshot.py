"""
The immutable run snapshot.

THE BUG.  `_on_calculate` writes its results onto the LIVE `TraceConfig`
objects, and the render collections used to hold that live object --
`result_rows.append((tc, fe.label, res))` and friends.  So anything that kept a
run's rows and re-rendered them later printed the NEXT run's id, label and port
descriptor beside THIS run's numbers.  Nothing raises, the numbers are real,
and the reader has no way to tell which spec produced them.

The blast radius is four fields -- `id`, `label`, `port_descriptor()` and
`enabled` (the shown/hidden filter) -- plus `color_idx`, which the swatch is
tagged from.  A `RowSnapshot` / `CouplingSnapshot` / `FitSnapshot` resolves all
of them at snapshot time; a `RunSnapshot` collects them under a monotonic run
number and a wall clock.

Three properties are pinned here:

  1. THE PAGE DID NOT MOVE.  tests/fixtures/render_reference.json was generated
     from the code as it stood BEFORE the snapshot types existed; every case is
     replayed through the current renderers and must match byte for byte.
  2. A SNAPSHOT IS IMMUNE TO ITS TRACE.  Mutating the source `TraceConfig` --
     id, label, spec fields, visibility, colour -- and re-rendering produces
     identical text.  The tests that pin this also DEMONSTRATE the hazard: the
     same case rendered through the live object does move.
  3. A SNAPSHOT CARRIES NO BULK ARRAYS.  Its reachable ndarray size is the same
     at 200 frequencies as at 2000, and none of `Z` / `Zmat` / `fit_freqs` /
     `fit_Z` is reachable from it at all.  Measured envelope: 10 runs x 6
     traces of text and rows is ~0.43 MB; the arrays would be 173 MB for a
     mode-6 run at 5000 frequencies and 6 measurement ports.

Pure functions get pure tests; the Tk ones skip cleanly with no display.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from datetime import datetime
from dataclasses import fields as dc_fields
from dataclasses import is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import _render_capture as rc  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    parse_touchstone,
)
from pkg_rlc.frontend.app import (  # noqa: E402
    App,
    CouplingSnapshot,
    FileEntry,
    FitSnapshot,
    RowSnapshot,
    RESULTS_SWATCH,
    RunSnapshot,
    TraceConfig,
    _format_coupling_block,
    _format_results_table,
    _snapshot_block,
    _snapshot_fit,
    _snapshot_row,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "coupled_4port_float.s4p"


def _ensure_fixtures() -> None:
    if FIXTURE.exists():
        return
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


def _has_display() -> bool:
    try:
        r = tk.Tk()
    except Exception:
        return False
    r.destroy()
    return True


# ============================================================================
# 1 -- the rendered page did not move
# ============================================================================

class TestRenderedPageDidNotMove(unittest.TestCase):
    """
    The acceptance criterion for the whole refactor, and the only one that can
    be checked against code that no longer exists.

    tests/fixtures/render_reference.json was captured from the PRE-refactor
    renderers, which took `(tc, file_label, res)` tuples and a live
    `TraceConfig`.  _render_capture.render_case is the one place that knows the
    current signature; the reference does not move with it.
    """

    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads(
            rc.REFERENCE_PATH.read_text(encoding="utf-8"))

    def test_every_case_is_byte_identical(self):
        for case in rc.CASES:
            with self.subTest(case=case["name"]):
                self.assertIn(case["name"], self.reference,
                              "the reference has no entry for this case")
                self.assertEqual(rc.render_case(case),
                                 self.reference[case["name"]])

    def test_the_reference_has_no_orphan_cases(self):
        """
        A case deleted from the registry would silently stop being checked,
        and the reference would keep claiming coverage it no longer has.
        """
        self.assertEqual(sorted(c["name"] for c in rc.CASES),
                         sorted(self.reference))

    def test_the_reference_covers_both_renderers_and_both_unit_modes(self):
        kinds = {(c["kind"], c["units"]) for c in rc.CASES}
        for want in (("table", "smart"), ("table", "aligned"),
                     ("block", "smart"), ("block", "aligned")):
            self.assertIn(want, kinds)


# ============================================================================
# 2 -- a snapshot is immune to the trace it came from
# ============================================================================

def _tc(**kw) -> TraceConfig:
    base = dict(id=1, label="tank", file_label="coil.s4p", mode=1,
                port_a="1", gnd_ports="2", color_idx=0)
    base.update(kw)
    return TraceConfig(**base)


class _Res:
    """Enough of an RLCResult for the table; a fresh object per run anyway."""
    R_ohm, L_henry, C_farad, Q = 1.5, 2e-9, -1.2e-12, 0.84


class _LiveRow:
    """
    The OLD row shape, kept here on purpose.

    The tests below do not merely assert that the snapshot holds still -- they
    render the same case through this live-object stand-in and assert that it
    does NOT.  Without that half, a renderer that had stopped printing the
    label at all would pass every "byte-identical" assertion in this file.
    """

    def __init__(self, tc, file_label, res):
        self.tc, self.file_label, self.res = tc, file_label, res
        self.enabled, self.color_idx = True, 0

    @property
    def id(self):
        return self.tc.id

    @property
    def label(self):
        return self.tc.label

    @property
    def port_desc(self):
        return self.tc.port_descriptor()


class TestRowSnapshotIsImmuneToItsTrace(unittest.TestCase):
    def setUp(self):
        self.tc = _tc()
        self.res = _Res()
        self.snap = _snapshot_row(self.tc, "coil.s4p", self.res)
        self.live = _LiveRow(self.tc, "coil.s4p", self.res)
        self.before = _format_results_table([self.snap], "smart")

    def _after(self):
        return _format_results_table([self.snap], "smart")

    def _live_after(self):
        return _format_results_table([self.live], "smart")

    def test_relabelling_the_trace_does_not_move_the_row(self):
        self.tc.label = "renamed_after_the_run"
        self.assertEqual(self._after(), self.before)
        self.assertNotEqual(self._live_after(), self.before,
                            "the hazard being guarded is not reproducible")

    def test_renumbering_the_trace_does_not_move_the_row(self):
        self.tc.id = 99
        self.assertEqual(self._after(), self.before)
        self.assertNotEqual(self._live_after(), self.before)

    def test_re_porting_the_trace_does_not_move_the_row(self):
        """
        port_descriptor() is a METHOD that recomputes from the live spec
        fields, so it has to be resolved to a string at snapshot time.
        Storing the callable, or the trace it is bound to, reopens the hazard
        in a form that is harder to see.
        """
        self.tc.mode = 2
        self.tc.port_a, self.tc.port_b = "7", "8"
        self.assertIn("M1: S:[1] G:[2]", self.before)
        self.assertEqual(self._after(), self.before)
        self.assertNotEqual(self._live_after(), self.before)

    def test_the_port_descriptor_is_a_string_not_a_callable(self):
        self.assertIsInstance(self.snap.port_desc, str)
        self.assertFalse(callable(self.snap.port_desc))

    def test_the_record_itself_cannot_be_written(self):
        for name, value in (("id", 2), ("label", "x"), ("port_desc", "y"),
                            ("enabled", False), ("color_idx", 3)):
            with self.subTest(field=name):
                with self.assertRaises(Exception):
                    setattr(self.snap, name, value)

    def test_the_trace_is_not_reachable_from_the_snapshot(self):
        """The decisive structural check: no field holds the TraceConfig."""
        for f in dc_fields(self.snap):
            self.assertIsNot(getattr(self.snap, f.name), self.tc)

    def test_visibility_and_colour_are_captured_too(self):
        tc = _tc(enabled=False, color_idx=7)
        snap = _snapshot_row(tc, "coil.s4p", _Res())
        self.assertFalse(snap.enabled)
        self.assertEqual(snap.color_idx, 7)
        tc.enabled, tc.color_idx = True, 0
        self.assertFalse(snap.enabled)
        self.assertEqual(snap.color_idx, 7)


class TestCouplingSnapshotIsImmuneToItsTrace(unittest.TestCase):
    def setUp(self):
        self.tc = _tc(mode=6, label="osc")
        case = next(c for c in rc.CASES if c["name"] == "block_three_pairs_smart")
        self.cres = rc.build_cres(case["block"])
        self.snap = _snapshot_block(self.tc, "coil.s4p", self.cres)
        self.before = _format_coupling_block(self.snap, "smart")

    def test_the_heading_is_the_identity_as_measured(self):
        head = self.before.split("\n")[0]
        self.assertIn("osc", head)
        self.tc.label = "renamed"
        self.tc.id = 42
        self.assertEqual(_format_coupling_block(self.snap, "smart"),
                         self.before)

    def test_the_matrix_comes_from_the_result_not_from_the_trace(self):
        """
        _format_coupling_block reads cres.Z_matrix, never tc.Zmat -- which is
        what lets a snapshot keep the printed matrix without keeping the
        whole per-frequency array.
        """
        self.tc.Zmat = None
        self.tc.mport_names = None
        self.assertEqual(_format_coupling_block(self.snap, "smart"),
                         self.before)


class TestFitSnapshot(unittest.TestCase):
    def test_it_keeps_the_visibility_it_was_taken_with(self):
        tc = _tc(enabled=True)
        f = _snapshot_fit(tc, "  fit[1 inductor]: L=2 nH")
        tc.enabled = False
        self.assertTrue(f.enabled)
        self.assertEqual(f.text, "  fit[1 inductor]: L=2 nH")


# ============================================================================
# 3 -- no bulk arrays are retained
# ============================================================================

def _reachable_arrays(obj, seen=None) -> list:
    """Every ndarray reachable from `obj` through dataclasses / containers."""
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return []
    seen.add(id(obj))
    if isinstance(obj, np.ndarray):
        return [obj]
    out = []
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in dc_fields(obj):
            out += _reachable_arrays(getattr(obj, f.name), seen)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += _reachable_arrays(k, seen) + _reachable_arrays(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            out += _reachable_arrays(v, seen)
    elif hasattr(obj, "__dict__") and not isinstance(
            obj, (str, bytes, int, float, complex, bool, type(None))):
        for v in vars(obj).values():
            out += _reachable_arrays(v, seen)
    return out


def _now():
    return datetime.now()


def _run_for(nfreqs: int):
    """
    A run snapshot built the way _on_calculate builds one, over a synthetic
    sweep of `nfreqs` points, plus the trace it was taken from.
    """
    freqs = np.linspace(1e8, 1e10, nfreqs)
    G = 3
    Zmat = np.zeros((nfreqs, G, G), dtype=complex)
    for g in range(G):
        Zmat[:, g, g] = 1.5 + 1j * 2e-9 * 2 * math.pi * freqs
    Zmat[:, 0, 1] = Zmat[:, 1, 0] = 1j * 2e-10 * 2 * math.pi * freqs
    names = ["L1", "L2", "L3"]

    tc = _tc(mode=6, label="osc")
    tc.Zmat = Zmat
    tc.mport_names = names
    tc.Z = np.ascontiguousarray(Zmat[:, 0, 0])
    tc.fit_freqs = freqs
    tc.fit_Z = np.ascontiguousarray(Zmat[:, 1, 1])
    tc.rlc = extract_rlc_at_freq(freqs, tc.Z, 1e9)
    tc.coupling = extract_coupling_at_freq(freqs, Zmat, names, 1e9)

    run = RunSnapshot(
        number=1, when=_now(),
        marker_freq_hz=1e9,
        rows=(_snapshot_row(tc, "coil.s4p", tc.rlc),),
        blocks=(_snapshot_block(tc, "coil.s4p", tc.coupling),),
        fits=(_snapshot_fit(tc, "  fit[1]: skipped"),))
    return run, tc, Zmat, freqs


class TestNoBulkArraysAreRetained(unittest.TestCase):
    def test_the_snapshot_size_does_not_grow_with_the_sweep(self):
        """
        THE property, stated the way it actually matters.  A snapshot may hold
        the G x G matrix at the marker frequency -- that is what the block
        prints -- but nothing whose size is a function of the frequency count.
        """
        small, _, _, _ = _run_for(200)
        big, _, _, _ = _run_for(2000)
        n_small = sum(a.size for a in _reachable_arrays(small))
        n_big = sum(a.size for a in _reachable_arrays(big))
        self.assertEqual(n_small, n_big,
                         f"snapshot grew with the sweep: {n_small} -> {n_big}")
        self.assertLessEqual(n_big, 64, "that is bigger than a G x G matrix")

    def test_none_of_the_per_frequency_arrays_is_reachable(self):
        run, tc, Zmat, freqs = _run_for(400)
        reachable = _reachable_arrays(run)
        for name in ("Z", "Zmat", "fit_freqs", "fit_Z"):
            arr = getattr(tc, name)
            with self.subTest(field=name):
                self.assertFalse(any(a is arr for a in reachable))
                self.assertFalse(
                    any(np.shares_memory(a, arr) for a in reachable),
                    f"a snapshot holds a VIEW of {name}, which keeps the "
                    f"whole base array alive")

    def test_the_only_array_it_holds_is_the_printed_matrix(self):
        run, _, _, _ = _run_for(400)
        arrays = _reachable_arrays(run)
        self.assertEqual(len(arrays), 1)
        self.assertEqual(arrays[0].shape, (3, 3))
        self.assertIs(arrays[0], run.blocks[0].cres.Z_matrix)

    def test_the_trace_itself_is_not_reachable(self):
        run, tc, _, _ = _run_for(200)
        seen = set()

        def walk(o):
            if id(o) in seen:
                return False
            seen.add(id(o))
            if o is tc:
                return True
            if is_dataclass(o) and not isinstance(o, type):
                return any(walk(getattr(o, f.name)) for f in dc_fields(o))
            if isinstance(o, (list, tuple, set, frozenset)):
                return any(walk(v) for v in o)
            if isinstance(o, dict):
                return any(walk(v) for v in o.values())
            if hasattr(o, "__dict__"):
                return any(walk(v) for v in vars(o).values())
            return False

        self.assertFalse(walk(run))


# ============================================================================
# 4 -- the run record itself
# ============================================================================

class TestRunRecord(unittest.TestCase):
    def test_a_run_is_identified_by_a_counter_not_by_its_value(self):
        """
        Two runs of an unchanged spec are equal in every field and are still
        two different runs.  Nothing may key a run by equality -- no sets, no
        value-keyed dicts.
        """
        when = _now()
        a = RunSnapshot(number=1, when=when, marker_freq_hz=1e9)
        b = RunSnapshot(number=2, when=when, marker_freq_hz=1e9)
        self.assertNotEqual(a, b)
        self.assertEqual(len({a.number, b.number}), 2)

    def test_the_visibility_of_a_past_run_is_frozen(self):
        """
        A run record is a record of what was MEASURED.  Toggling a trace's
        visibility afterwards must not retroactively rewrite it;
        _replot_from_cache stays the owner of 'what is on the plot now'.
        """
        tc = _tc()
        run = RunSnapshot(number=1, when=_now(), marker_freq_hz=1e9,
                          rows=(_snapshot_row(tc, "coil.s4p", _Res()),))
        tc.enabled = False
        self.assertTrue(run.rows[0].enabled)

    def test_with_visibility_re_reads_the_flag_by_trace_id(self):
        tc = _tc()
        run = RunSnapshot(number=1, when=_now(), marker_freq_hz=1e9,
                          rows=(_snapshot_row(tc, "coil.s4p", _Res()),),
                          fits=(_snapshot_fit(tc, "fit"),))
        tc.enabled = False
        fresh = run.with_visibility([tc])
        self.assertFalse(fresh.rows[0].enabled)
        self.assertFalse(fresh.fits[0].enabled)
        self.assertTrue(run.rows[0].enabled, "it mutated the original run")

    def test_with_visibility_changes_nothing_but_the_flag(self):
        """
        It is a VISIBILITY refresh, not a re-snapshot.  The trace is edited
        here WITHOUT changing its id, so the record is still matched -- a test
        that renumbered the trace too would pass with no guard at all, because
        nothing would match.
        """
        tc = _tc()
        run = RunSnapshot(number=1, when=_now(), marker_freq_hz=1e9,
                          rows=(_snapshot_row(tc, "coil.s4p", _Res()),))
        tc.label, tc.color_idx = "renamed", 5
        tc.mode, tc.port_a = 2, "9"
        tc.enabled = False
        fresh = run.with_visibility([tc])
        self.assertEqual(tc.id, fresh.rows[0].id, "precondition: still matched")
        self.assertFalse(fresh.rows[0].enabled)
        self.assertEqual(fresh.rows[0].label, "tank")
        self.assertEqual(fresh.rows[0].color_idx, 0)
        self.assertEqual(fresh.rows[0].port_desc, run.rows[0].port_desc)
        self.assertEqual(fresh.number, run.number)

    def test_a_renumbered_trace_no_longer_matches(self):
        """The match is by id; nothing else could tie a record to a trace."""
        tc = _tc()
        run = RunSnapshot(number=1, when=_now(), marker_freq_hz=1e9,
                          rows=(_snapshot_row(tc, "coil.s4p", _Res()),))
        tc.id, tc.enabled = 99, False
        self.assertTrue(run.with_visibility([tc]).rows[0].enabled)

    def test_a_record_whose_trace_is_gone_keeps_its_own_flag(self):
        """Removing a trace must not resurrect or hide a row it left behind."""
        tc = _tc(enabled=False)
        run = RunSnapshot(number=1, when=_now(), marker_freq_hz=1e9,
                          rows=(_snapshot_row(tc, "coil.s4p", _Res()),))
        self.assertFalse(run.with_visibility([]).rows[0].enabled)


# ============================================================================
# 5 -- the App: a run survives the run after it
# ============================================================================

@unittest.skipUnless(_has_display(), "no display")
class TestAppRunsAreIndependent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        fe = FileEntry(parse_touchstone(str(FIXTURE)))
        self.app.files.append(fe)
        self.app._refresh_file_list()
        self.tc = TraceConfig(id=1, label="tank", file_label=fe.label,
                              mode=1, port_a="1", gnd_ports="2")
        self.app.traces.append(self.tc)
        self.app._next_trace_id = 2
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _settle(self):
        for _ in range(4):
            self.app.update()
            self.app.update_idletasks()

    def test_run_numbers_are_monotonic(self):
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.app._last_run.number, 1)
        self.app._on_calculate()
        self._settle()
        self.assertEqual(self.app._last_run.number, 2)

    def test_an_earlier_runs_report_survives_a_later_run(self):
        """
        THE bug, at the level it would have been met.  Re-rendering run 1
        after run 2 relabelled and re-ported the trace used to print run 2's
        identity beside run 1's numbers.
        """
        self.app._on_calculate()
        self._settle()
        run1 = self.app._last_run
        before = _format_results_table([r for r in run1.rows if r.enabled],
                                       "smart")
        self.assertIn("tank", before)

        # Deselect first: _on_calculate flushes the editor into the SELECTED
        # trace, which would write the old label straight back.
        self.app.traces_lb.selection_clear(0, tk.END)
        self.tc.label = "renamed"
        self.tc.mode = 2
        self.tc.port_a, self.tc.port_b = "3", "4"
        self.app._refresh_trace_list()
        self.app._on_calculate()
        self._settle()

        after = _format_results_table([r for r in run1.rows if r.enabled],
                                      "smart")
        self.assertEqual(after, before)
        self.assertIn("renamed", _format_results_table(
            [r for r in self.app._last_run.rows if r.enabled], "smart"),
            "the NEW run should show the new label")

    def test_the_numbers_of_an_earlier_run_are_its_own(self):
        """`res` is a fresh object per run, which is what makes the snapshot's
        reference (rather than a copy) safe."""
        self.app._on_calculate()
        self._settle()
        first = self.app._last_run.rows[0].res
        self.app._on_calculate()
        self._settle()
        second = self.app._last_run.rows[0].res
        self.assertIsNot(first, second)

    def test_a_units_re_render_follows_the_visibility_as_it_stands_then(self):
        """
        The one deliberate exception to the freeze, and it is only ever
        applied to the CURRENT run: `enabled` gates the results table as well
        as the plot, so a row for a curve that is no longer drawn would read
        as a duplicate of one that is.
        """
        self.app._on_calculate()
        self._settle()
        self.tc.enabled = False
        mark = self.app.results_text.index(tk.END)
        self.app.units_mode_var.set("aligned")
        self.app._on_units_mode_changed()
        self._settle()
        body = self.app.results_text.get(mark, tk.END)
        self.assertNotIn(f"{RESULTS_SWATCH} [ 1] tank", body,
                         "the hidden trace kept its table row")
        self.assertIn("hidden (measured", body)

    def test_freezing_joins_the_current_run_and_does_not_number_a_new_one(self):
        self.app._on_calculate()
        self._settle()
        n_before = len(self.app._last_run.rows)
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_freeze_trace()
        self._settle()
        self.assertEqual(self.app._last_run.number, 1)
        self.assertEqual(len(self.app._last_run.rows), n_before + 1)


if __name__ == "__main__":
    unittest.main()
