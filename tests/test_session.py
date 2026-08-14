"""
Saving and reloading the configuration (File → Save / Load / Restore).

A session file is the CONFIG, not the results, and the tests here are mostly
about what that sentence has to mean in practice:

  * COVERAGE.  Every field of TraceConfig is classified as config or computed,
    so adding a field to the dataclass and forgetting about the session file
    turns a test red instead of silently not round-tripping.
  * FIDELITY.  What comes back is what went in -- including the two row tables,
    the verbatim `extra_lines`, and the retired fields a never-selected trace
    still carries.  The restored rows must be NEW objects; sharing them would
    be the `Duplicate` aliasing bug one file further away.
  * REFUSAL.  A file that is not ours, or is from a newer build, is refused
    with a verdict a user can act on -- the TouchstoneParseError contract.
  * SURVIVING A HAND EDIT.  A session file is readable text, so it will be
    edited.  A bad value costs its own field and a note, never the whole file.
  * PATHS.  Relative first, absolute as the fallback: that is what makes a
    session survive the folder being copied to another machine.

The Tk half drives the real App: save, wipe, load, and check the trace list,
the editor, the globals and the plot checkboxes came back -- plus that a file
which has gone missing is reported rather than fatal.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc.frontend.app as pkg_rlc_gui  # noqa: E402
from pkg_rlc.physics.core import ConnectionRow, MeasPortRow, parse_touchstone  # noqa: E402
from pkg_rlc.frontend.app import (  # noqa: E402
    App,
    FileEntry,
    LoadedSession,
    SessionError,
    TraceConfig,
    resolve_session_file,
    session_from_dict,
    session_to_dict,
    trace_from_dict,
    trace_to_dict,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"


def _ensure_fixtures():
    if not FIXTURE.exists():
        import generate_test_snp
        generate_test_snp.main()


try:
    _root = tk.Tk()
    _root.destroy()
    TK_OK = True
except Exception:                                   # pragma: no cover
    TK_OK = False


def _rich_trace() -> TraceConfig:
    """One trace using every shape the file has to carry."""
    return TraceConfig(
        id=7, file_label="coil.s4p", mode=5,
        port_a="1", port_b="2", short_pairs="3-4", gnd_ports="5:1:9",
        mports=[MeasPortRow(name="pri", plus="1", minus="2"),
                MeasPortRow(name="sec", plus="3", minus="4")],
        conn_rows=[ConnectionRow(kind="ground", ports="6-14"),
                   ConnectionRow(kind="rlc_between", ports="1", to="2",
                                 R="50", L="1n", C="")],
        extra_lines="# hand written\n5 open",
        plot_self=True, plot_mutual=False, enabled=False,
        label="osc_primary", color_idx=3, ls_idx=2,
    )


def _dump_load(data: dict) -> dict:
    """Force everything through real JSON, as the file would."""
    return json.loads(json.dumps(data))


# ============================================================================
# Pure round trip -- no Tk
# ============================================================================


class TestFieldCoverage(unittest.TestCase):
    def test_every_traceconfig_field_is_classified(self):
        """
        The saved set is "all fields minus the computed ones", so a new CONFIG
        field is carried without anyone remembering it.  The risk that buys is
        a new COMPUTED field nobody blacklists, which would try to JSON-encode
        a numpy array -- this test is the cheap half of that guard and
        test_computed_results_are_not_written is the expensive half.
        """
        declared = {f.name for f in fields(TraceConfig)}
        config = set(pkg_rlc_gui._config_trace_fields())
        computed = set(pkg_rlc_gui._COMPUTED_TRACE_FIELDS)
        self.assertEqual(config | computed, declared)
        self.assertEqual(config & computed, set())

    def test_the_legacy_list_names_real_fields(self):
        declared = {f.name for f in fields(TraceConfig)}
        self.assertLessEqual(set(pkg_rlc_gui._LEGACY_TRACE_FIELDS), declared)


class TestTraceRoundTrip(unittest.TestCase):
    def test_a_full_trace_comes_back_unchanged(self):
        src = _rich_trace()
        back = trace_from_dict(_dump_load(trace_to_dict(src)), lambda m: None)
        for name in pkg_rlc_gui._config_trace_fields():
            self.assertEqual(getattr(back, name), getattr(src, name),
                             f"{name} did not survive the round trip")

    def test_the_restored_rows_are_new_objects(self):
        """
        Same hazard as Duplicate: two TraceConfigs sharing one list of rows
        edit each other with no symptom.  Here the shared object would be the
        dict that came out of json, so the assertion is on the row TYPE and on
        identity against the source.
        """
        src = _rich_trace()
        back = trace_from_dict(_dump_load(trace_to_dict(src)), lambda m: None)
        self.assertIsNot(back.mports, src.mports)
        self.assertIsNot(back.conn_rows, src.conn_rows)
        for row in back.mports:
            self.assertIsInstance(row, MeasPortRow)
        for row in back.conn_rows:
            self.assertIsInstance(row, ConnectionRow)
        for a, b in zip(back.mports, src.mports):
            self.assertIsNot(a, b)

    def test_computed_results_are_not_written(self):
        """
        The file is the setup, not the numbers -- and a numpy array in there
        would not JSON-encode at all, so this doubles as the guard on a new
        computed field being forgotten in _COMPUTED_TRACE_FIELDS.
        """
        tc = _rich_trace()
        tc.Z = np.array([1 + 2j, 3 + 4j])
        tc.Zmat = np.zeros((2, 2, 2), dtype=complex)
        tc.fit_freqs = np.array([1e9])
        tc.fit_Z = np.array([1 + 0j])
        tc.mport_names = ["pri", "sec"]
        tc.rlc = object()
        tc.stale = True
        data = trace_to_dict(tc)
        for name in pkg_rlc_gui._COMPUTED_TRACE_FIELDS:
            self.assertNotIn(name, data)
        json.dumps(data)        # would raise on a leaked array

    def test_empty_legacy_fields_are_left_out_but_a_used_one_is_kept(self):
        plain = trace_to_dict(TraceConfig(id=1))
        self.assertNotIn("custom_text", plain)
        self.assertNotIn("mp1_plus", plain)

        legacy = TraceConfig(id=1, custom_text="1 signal A\n2 signal B")
        data = trace_to_dict(legacy)
        self.assertEqual(data["custom_text"], "1 signal A\n2 signal B")
        back = trace_from_dict(_dump_load(data), lambda m: None)
        self.assertEqual(back.custom_text, legacy.custom_text)

    def test_the_whole_session_is_json_encodable(self):
        data = session_to_dict(
            files=[], traces=[_rich_trace()],
            controls={"rlc_freq_ghz": "0.1"},
            plot_state={"x_log": True, "types": ["L(nH)"]},
            saved_utc="2026-01-01 00:00:00 UTC")
        text = json.dumps(data, indent=2)
        self.assertIn(pkg_rlc_gui.SESSION_FORMAT, text)
        sess = session_from_dict(json.loads(text))
        self.assertEqual(len(sess.traces), 1)
        self.assertEqual(sess.traces[0].label, "osc_primary")
        self.assertEqual(sess.controls["rlc_freq_ghz"], "0.1")
        self.assertEqual(sess.plot["types"], ["L(nH)"])
        self.assertEqual(sess.warnings, [])


class TestSessionRefusal(unittest.TestCase):
    """str(e) IS the report -- so assert on what it actually says."""

    def test_a_foreign_json_file_is_named_as_such(self):
        with self.assertRaises(SessionError) as cm:
            session_from_dict({"hello": "world"})
        self.assertIn("not a PKG RLC Extractor session file", str(cm.exception))
        self.assertIn("Save Config", str(cm.exception))

    def test_a_json_list_is_refused_before_anything_else(self):
        with self.assertRaises(SessionError) as cm:
            session_from_dict([1, 2, 3])
        self.assertIn("not a JSON object", str(cm.exception))

    def test_a_future_version_names_both_numbers(self):
        data = {"format": pkg_rlc_gui.SESSION_FORMAT,
                "version": pkg_rlc_gui.SESSION_VERSION + 1}
        with self.assertRaises(SessionError) as cm:
            session_from_dict(data)
        msg = str(cm.exception)
        self.assertIn(str(pkg_rlc_gui.SESSION_VERSION + 1), msg)
        self.assertIn(str(pkg_rlc_gui.SESSION_VERSION), msg)

    def test_a_missing_version_is_refused(self):
        with self.assertRaises(SessionError) as cm:
            session_from_dict({"format": pkg_rlc_gui.SESSION_FORMAT})
        self.assertIn("version", str(cm.exception))

    def test_the_current_version_is_accepted(self):
        sess = session_from_dict({"format": pkg_rlc_gui.SESSION_FORMAT,
                                  "version": pkg_rlc_gui.SESSION_VERSION})
        self.assertIsInstance(sess, LoadedSession)
        self.assertEqual(sess.traces, [])


class TestSurvivingAHandEdit(unittest.TestCase):
    """One bad value costs its own field, never the port map beside it."""

    def _load(self, trace: dict) -> tuple:
        data = {"format": pkg_rlc_gui.SESSION_FORMAT,
                "version": pkg_rlc_gui.SESSION_VERSION,
                "traces": [trace]}
        sess = session_from_dict(data)
        return sess.traces[0], sess.warnings

    def test_an_unknown_field_is_named_and_ignored(self):
        tc, warns = self._load({"label": "keep me", "port_z": "9"})
        self.assertEqual(tc.label, "keep me")
        self.assertTrue(any("port_z" in w for w in warns), warns)

    def test_a_non_numeric_int_keeps_the_default_and_says_so(self):
        tc, warns = self._load({"label": "keep me", "color_idx": "blue"})
        self.assertEqual(tc.color_idx, TraceConfig().color_idx)
        self.assertEqual(tc.label, "keep me")
        self.assertTrue(any("color_idx" in w for w in warns), warns)

    def test_the_string_false_means_false(self):
        """`bool("false")` is True, which would silently invert the checkbox."""
        tc, _ = self._load({"enabled": "false"})
        self.assertFalse(tc.enabled)
        tc, _ = self._load({"enabled": "true"})
        self.assertTrue(tc.enabled)

    def test_a_switched_off_connection_row_comes_back_switched_off(self):
        """
        The same `bool("false")` trap as the test above, one level down and
        with no checkbox to notice it on.

        `_rows_from_list` `str()`-es every field, and `str(False)` is the
        NON-EMPTY string "False" -- so a row saved with enabled=False came back
        truthy and the spec silently grew a connection the user had switched
        off.  Silently: nothing raises, the row looks right in the table
        because the glyph is derived from the value, and only the number moves.

        MUTATION: drop the bool branch in `_rows_from_list` and this is the
        only test that goes red.
        """
        tc, warns = self._load({"conn_rows": [
            {"kind": "ground", "ports": "1", "enabled": False},
            {"kind": "ground", "ports": "2", "enabled": True},
            {"kind": "ground", "ports": "3"},
        ]})
        self.assertEqual([r.enabled for r in tc.conn_rows],
                         [False, True, True])
        self.assertEqual(warns, [], "a well-formed row warned")
        # ... and the row really is out of the spec, not merely flagged.
        text = pkg_rlc_gui.rows_to_dsl_text([], tc.conn_rows, "")
        self.assertNotIn("1 ground", text)
        self.assertIn("2 ground", text)

    def test_a_hand_edited_enabled_that_is_not_a_bool_keeps_the_default(self):
        """A bad value costs its own field and a note, never the file."""
        tc, warns = self._load({"conn_rows": [
            {"kind": "ground", "ports": "1", "enabled": "perhaps"},
        ]})
        self.assertTrue(tc.conn_rows[0].enabled)
        self.assertTrue(any("enabled" in w for w in warns), warns)
        # the spellings a hand-edit really produces still work
        for text, want in (("false", False), ("no", False), ("0", False),
                           ("true", True), ("yes", True), ("1", True)):
            with self.subTest(text=text):
                tc, _ = self._load({"conn_rows": [
                    {"kind": "ground", "ports": "1", "enabled": text}]})
                self.assertIs(tc.conn_rows[0].enabled, want)

    def test_a_malformed_row_is_dropped_and_the_good_ones_stay(self):
        tc, warns = self._load({"conn_rows": [
            {"kind": "ground", "ports": "1"},
            "not a row",
            {"kind": "short", "ports": "2", "to": "3"},
        ]})
        self.assertEqual([r.ports for r in tc.conn_rows], ["1", "2"])
        self.assertTrue(any("row" in w for w in warns), warns)

    def test_a_combobox_value_outside_its_list_is_refused(self):
        """
        Both comboboxes are state="readonly": a value from outside the list
        would sit there unselectable, with no way back except re-editing the
        file by hand.
        """
        data = {"format": pkg_rlc_gui.SESSION_FORMAT,
                "version": pkg_rlc_gui.SESSION_VERSION,
                "controls": {"fit_model": "wishful", "units_mode": "smart",
                             "rlc_freq_ghz": "2.5"}}
        sess = session_from_dict(data)
        self.assertNotIn("fit_model", sess.controls)
        self.assertEqual(sess.controls["units_mode"], "smart")
        self.assertEqual(sess.controls["rlc_freq_ghz"], "2.5")
        self.assertTrue(any("fit_model" in w for w in sess.warnings))


class TestPathResolution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.target = self.data_dir / "coil.s4p"
        self.target.write_text("! placeholder\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_relative_path_wins_so_a_copied_folder_still_opens(self):
        """
        The absolute path here points at nothing -- which is exactly the state
        after the folder was copied to another machine.
        """
        ref = {"label": "coil.s4p", "rel_path": "data/coil.s4p",
               "path": "/nowhere/that/exists/coil.s4p"}
        path, found = resolve_session_file(ref, str(self.root))
        self.assertTrue(found)
        self.assertEqual(Path(path).resolve(), self.target.resolve())

    def test_the_relative_path_wins_when_BOTH_exist(self):
        """
        The test above only proves the relative path is tried -- reverse the
        two candidates and it still passes, because the absolute one points at
        nothing.  This is the precedence itself: a folder copied next to a
        stale original must read the copy, or the session silently reports on
        the file the user thought they had left behind.
        """
        other = self.root / "elsewhere"
        other.mkdir()
        stale = other / "coil.s4p"
        stale.write_text("! the one that must NOT win\n", encoding="utf-8")
        ref = {"label": "coil.s4p", "rel_path": "data/coil.s4p",
               "path": str(stale)}
        path, found = resolve_session_file(ref, str(self.root))
        self.assertTrue(found)
        self.assertEqual(Path(path).resolve(), self.target.resolve())

    def test_the_absolute_path_is_the_fallback(self):
        ref = {"label": "coil.s4p", "rel_path": "moved/away/coil.s4p",
               "path": str(self.target)}
        path, found = resolve_session_file(ref, str(self.root))
        self.assertTrue(found)
        self.assertEqual(Path(path).resolve(), self.target.resolve())

    def test_a_relative_path_is_written_only_when_it_describes_a_tree(self):
        """
        'data/coil.s4p' names a folder that can be copied as a unit.  A config
        saved somewhere unrelated to the data gives a ten-deep '../../..' chain
        that is longer than the absolute path, resolves on this machine and
        nowhere else, and is only noise in the file.
        """
        class _FakeEntry:
            def __init__(self, path):
                self.label = os.path.basename(path)
                self.ts = type("ts", (), {"source_path": path})()

        near = pkg_rlc_gui._file_ref(_FakeEntry(str(self.target)),
                                     str(self.root))
        self.assertEqual(near["rel_path"], "data/coil.s4p")

        far = pkg_rlc_gui._file_ref(
            _FakeEntry(str(self.target)),
            str(Path(self.root.anchor) / "a" / "b" / "c" / "d" / "e" / "f"))
        self.assertNotIn("rel_path", far)
        self.assertTrue(os.path.isabs(far["path"]))

    def test_a_file_that_is_gone_reports_where_it_looked(self):
        ref = {"label": "coil.s4p", "rel_path": "data/gone.s4p",
               "path": "/nowhere/coil.s4p"}
        path, found = resolve_session_file(ref, str(self.root))
        self.assertFalse(found)
        self.assertIn("gone.s4p", path)


# ============================================================================
# The real App
# ============================================================================


class _AppCase(unittest.TestCase):
    """An App with one file and two traces, plus a scratch directory."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                              port_a="1", gnd_ports="2-4", label="t1")
        # port_a is deliberately blank (the dataclass default is "1"): several
        # tests below use this trace as the "editor is showing something else"
        # state, which only proves anything if its fields differ from t1's.
        self.tc2 = TraceConfig(id=2, file_label=self.fe.label, mode=6,
                               port_a="", gnd_ports="",
                               mports=[MeasPortRow("pri", "1", "2"),
                                       MeasPortRow("sec", "3", "4")],
                               label="t2", color_idx=1, enabled=False)
        self.app.traces.extend([self.tc, self.tc2])
        self.app._refresh_trace_list()
        self._select(0)

    def tearDown(self):
        self.app.destroy()
        self._tmp.cleanup()

    def _select(self, idx):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _patch(self, obj, name, value):
        """Swap an attribute for the length of one test, and put it back."""
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, original)

    def _clear_results(self) -> None:
        """
        Empty the Results pane so what is read back afterwards is only what the
        action under test wrote.

        Marking `index(END)` and reading from there is off by one LINE: Tk
        inserts at "end" before the trailing newline that index("end") sits
        after, so the first appended line lands above the mark and is missed.
        """
        self.app.results_text.delete("1.0", tk.END)

    def _results(self) -> str:
        return self.app.results_text.get("1.0", tk.END)

    def _save(self, name="s.json") -> str:
        path = str(self.tmp / name)
        self.app._write_session(path, str(self.tmp))
        return path

    def _wipe(self):
        self.app.files = []
        self.app.traces = []
        self.app._trace_list_shown = []
        self.app._refresh_file_list()
        self.app._refresh_trace_list()
        self.app._refresh_file_combobox()


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestSaveLoad(_AppCase):
    def test_a_saved_session_reloads_into_the_same_traces(self):
        self.app.rlc_freq_var.set("2.5")
        self.app.fit_model_var.set("inductor")
        self.app.units_mode_var.set("aligned")
        path = self._save()
        # Move them away first, or the assertions below pass on a load that
        # restored nothing at all.
        self.app.rlc_freq_var.set("0.1")
        self.app.fit_model_var.set("none")
        self.app.units_mode_var.set("smart")
        self._wipe()
        self.assertTrue(self.app._load_session_file(path, "test"))
        self._settle()

        self.assertEqual([fe.label for fe in self.app.files], [self.fe.label])
        self.assertEqual([tc.label for tc in self.app.traces], ["t1", "t2"])
        t1, t2 = self.app.traces
        self.assertEqual((t1.mode, t1.port_a, t1.gnd_ports), (1, "1", "2-4"))
        self.assertEqual(t2.mode, 6)
        self.assertEqual([(r.name, r.plus, r.minus) for r in t2.mports],
                         [("pri", "1", "2"), ("sec", "3", "4")])
        self.assertFalse(t2.enabled)
        self.assertEqual(t2.color_idx, 1)
        self.assertEqual(self.app.rlc_freq_var.get(), "2.5")
        self.assertEqual(self.app.fit_model_var.get(), "inductor")
        self.assertEqual(self.app.units_mode_var.get(), "aligned")

    def test_the_editor_shows_the_first_restored_trace(self):
        path = self._save()
        # Park the editor on the OTHER trace, whose fields are empty, so a load
        # that forgot to reload the editor leaves those empty fields on screen.
        self._select(1)
        self.assertEqual(self.app.ed_porta.get_value(), "")
        self._wipe()
        self.app._load_session_file(path, "test")
        self._settle()
        self.assertEqual(self.app.ed_porta.get_value(), "1")
        self.assertEqual(self.app.ed_gnd.get_value(), "2-4")
        self.assertEqual(self.app.ed_label.get_value(), "t1")

    def test_new_trace_ids_continue_past_the_restored_ones(self):
        """Two traces with the same id would be two rows the user cannot tell
        apart in the results table."""
        path = self._save()
        self._wipe()
        self.app._load_session_file(path, "test")
        self._settle()
        self.app.files_lb.selection_set(0)
        self.app._on_add_trace()
        self._settle()
        ids = [tc.id for tc in self.app.traces]
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertEqual(max(ids), 3)

    def test_the_plot_view_comes_back(self):
        self.app.plot.y_log_var.set(True)
        self.app.plot.x_log_var.set(False)
        for name, var in self.app.plot.type_vars.items():
            var.set(name in ("L(nH)", "Q"))
        self.app.plot.view.marker_freq_hz = 2.5e9
        path = self._save()

        self.app.plot.y_log_var.set(False)
        self.app.plot.x_log_var.set(True)
        for var in self.app.plot.type_vars.values():
            var.set(False)
        self.app.plot.type_vars["R(mOhm)"].set(True)
        self.app.plot.view.marker_freq_hz = 1e9
        self._wipe()

        self.app._load_session_file(path, "test")
        self._settle()
        self.assertTrue(self.app.plot.y_log_var.get())
        self.assertFalse(self.app.plot.x_log_var.get())
        self.assertEqual(sorted(self.app.plot._active_types()),
                         ["L(nH)", "Q"])
        self.assertEqual(self.app.plot.view.marker_freq_hz, 2.5e9)
        self.assertTrue(self.app.plot.view.y_log)

    def test_the_last_keystroke_is_in_the_file(self):
        """
        Auto-apply defers to after_idle, so a Ctrl+S in the same event burst as
        the keystroke would otherwise save the value from before it -- the same
        flush Calculate does, for the same reason.
        """
        self.app.ed_porta.set_value("3")        # queued, not applied yet
        path = self._save()                     # no _settle() on purpose
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(data["traces"][0]["port_a"], "3")

    def test_the_file_is_readable_json_naming_itself(self):
        path = self._save()
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
        self.assertEqual(data["format"], pkg_rlc_gui.SESSION_FORMAT)
        self.assertEqual(data["version"], pkg_rlc_gui.SESSION_VERSION)
        self.assertIn("saved_utc", data)
        self.assertIn("\n  ", text, "the file is not indented")

    def test_a_config_saved_beside_its_data_records_both_paths(self):
        """The copy-the-whole-folder case, end to end through the real App."""
        import shutil
        data_dir = self.tmp / "data"
        data_dir.mkdir()
        local = data_dir / FIXTURE.name
        shutil.copyfile(FIXTURE, local)
        self.app.files = [FileEntry(parse_touchstone(str(local)))]
        self.app._refresh_file_list()

        path = self._save()
        ref = json.loads(Path(path).read_text(encoding="utf-8"))["files"][0]
        self.assertEqual(ref["label"], FIXTURE.name)
        self.assertTrue(os.path.isabs(ref["path"]), ref["path"])
        self.assertEqual(ref["rel_path"], f"data/{FIXTURE.name}")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestLoadFailuresAreSurvivable(_AppCase):
    def test_a_missing_file_is_reported_and_the_traces_stay(self):
        path = self._save()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["files"][0]["path"] = "/nowhere/gone.s4p"
        data["files"][0]["rel_path"] = "gone.s4p"
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        self._wipe()

        self._clear_results()
        self.app._load_session_file(path, "test")
        self._settle()
        body = self._results()
        self.assertIn("MISSING", body)
        self.assertIn("gone.s4p", body)
        self.assertEqual(self.app.files, [])
        self.assertEqual(len(self.app.traces), 2,
                         "the traces went down with the file")

    def _capture_errors(self) -> list:
        errors: list = []
        self._patch(pkg_rlc_gui.messagebox, "showerror",
                    lambda t, m: errors.append((t, m)))
        return errors

    def test_a_broken_file_leaves_the_session_untouched(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        errors = self._capture_errors()
        ok = self.app._load_session_file(str(bad), "test")
        self.assertFalse(ok)
        self.assertEqual(len(self.app.traces), 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("not valid JSON", errors[0][1])

    def test_a_foreign_file_is_refused_by_name(self):
        alien = self.tmp / "alien.json"
        alien.write_text('{"format": "something else"}', encoding="utf-8")
        errors = self._capture_errors()
        ok = self.app._load_session_file(str(alien), "test")
        self.assertFalse(ok)
        self.assertIn("not a PKG RLC Extractor session file", errors[0][1])

    def test_loading_over_live_work_asks_first_and_no_means_no(self):
        path = self._save()
        asked: list = []
        self._patch(pkg_rlc_gui.messagebox, "askyesno",
                    lambda t, m: (asked.append(m), False)[1])
        ok = self.app._load_session_file(path, "test")
        self.assertFalse(ok)
        self.assertEqual(len(asked), 1)
        self.assertIs(self.app.traces[0], self.tc, "the traces were replaced")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestAutosave(_AppCase):
    def _redirect_autosave(self, target=None):
        target = target or (self.tmp / "auto" / "last_session.json")
        self._patch(pkg_rlc_gui, "autosave_path", lambda: target)
        return target

    def test_closing_writes_the_session(self):
        target = self._redirect_autosave()
        self.app._autosave_session()
        self.assertTrue(target.is_file())
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(data["traces"]), 2)

    def test_an_empty_session_does_not_erase_the_last_one(self):
        """
        Open the tool, change nothing, close it: that must not throw away the
        session the previous run left behind.
        """
        target = self._redirect_autosave()
        self.app._autosave_session()
        self.assertTrue(target.is_file())
        stamp = target.read_text(encoding="utf-8")
        self._wipe()
        self.app._autosave_session()
        self.assertEqual(target.read_text(encoding="utf-8"), stamp)

    def test_an_unwritable_location_does_not_stop_the_app_closing(self):
        """
        This runs inside WM_DELETE_WINDOW.  A raise there is an application
        that cannot be closed, which is a worse outcome than a lost autosave.
        """
        self._redirect_autosave(Path("/:/nope/last_session.json"))
        self.app._autosave_session()        # must not raise

    def test_the_startup_notice_names_the_counts_and_loads_nothing(self):
        target = self._redirect_autosave()
        self.app._autosave_session()
        self._wipe()
        self._clear_results()
        self.app._announce_last_session()
        body = self._results()
        self.assertIn("Last session", body)
        self.assertIn("2 trace(s)", body)
        self.assertIn("Restore Last Session", body)
        self.assertEqual(self.app.traces, [],
                         "the notice loaded the session instead of naming it")

    def test_a_corrupt_autosave_is_silent(self):
        target = self._redirect_autosave()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{{{", encoding="utf-8")
        self._clear_results()
        self.app._announce_last_session()    # must not raise
        self.assertEqual(self._results().strip(), "")

    def test_the_window_close_button_reaches_the_autosave(self):
        """
        The link the whole feature hangs on: without WM_DELETE_WINDOW wired to
        _on_close, closing the window destroys the app directly and the
        autosave never runs -- which is the original complaint, unchanged.
        """
        # Not `assertTrue`: with no handler registered Tk reports its own
        # built-in "…destroy", which is truthy and is precisely the broken
        # state.  The registered command name is what has to be checked.
        self.assertIn("_on_close", self.app.protocol("WM_DELETE_WINDOW"))
        target = self._redirect_autosave()
        # _on_close ends in destroy(); tearDown must not destroy it twice.
        self.app._on_close()
        self.app = App()
        self.app.withdraw()
        self.assertTrue(target.is_file())
        self.assertEqual(
            len(json.loads(target.read_text(encoding="utf-8"))["traces"]), 2)

    def test_restore_reads_the_autosave(self):
        self._redirect_autosave()
        self.app._autosave_session()
        self._wipe()
        self.app._on_restore_last_session()
        self._settle()
        self.assertEqual([tc.label for tc in self.app.traces], ["t1", "t2"])


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestTheMenuIsReachable(_AppCase):
    """
    The affordance, not the plumbing: a save feature nobody can find is a save
    feature nobody uses.
    """

    def _labels(self) -> list[str]:
        menu = self.app._file_menu
        out = []
        for i in range(menu.index("end") + 1):
            if menu.type(i) == "command":
                out.append(str(menu.entrycget(i, "label")))
        return out

    def test_the_file_menu_offers_save_load_and_restore(self):
        labels = self._labels()
        self.assertTrue(any(l.startswith("Save Config") for l in labels), labels)
        self.assertTrue(any(l.startswith("Load Config") for l in labels), labels)
        self.assertIn("Restore Last Session", labels)

    def test_the_window_really_has_that_menu(self):
        self.assertTrue(str(self.app.cget("menu")))

    def test_control_o_does_not_also_scribble_in_the_results_pane(self):
        """
        Tk's Text class binds <Control-o> to "insert a newline", and a bind_all
        handler runs AFTER the class binding -- so without the unbind, Ctrl+O
        would open the dialog and edit the pane behind it.
        """
        self.assertEqual(self.app.bind_class("Text", "<Control-o>"), "")
        # The accelerator itself is still bound and would open a modal file
        # dialog, which in a test is a hang, not a failure.
        opened: list = []
        self._patch(pkg_rlc_gui.filedialog, "askopenfilename",
                    lambda **kw: opened.append(kw) or "")
        # A withdrawn window receives no synthesised key events at all, so this
        # one has to be on screen or the test proves nothing either way.
        self.app.deiconify()        # tearDown destroys it; no need to re-hide
        self._settle()
        before = self._results()
        self.app.results_text.focus_force()
        self._settle()
        self.app.results_text.event_generate("<Control-o>")
        self._settle()
        self.assertEqual(self._results(), before, "Ctrl+O edited the pane")
        self.assertEqual(len(opened), 1, "Ctrl+O did not reach Load Config")

    def test_the_accelerators_are_bound(self):
        self.assertTrue(self.app.bind_all("<Control-s>"))
        self.assertTrue(self.app.bind_all("<Control-o>"))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestHelpTabsAllFit(unittest.TestCase):
    """
    The Help window grew a tenth tab, and a ttk.Notebook CLIPS a tab strip it
    cannot fit -- no wrapping, no scrolling.  The tab that disappears is the
    last one, "Worked examples", and nothing about the window says it is gone.
    """

    def test_the_tab_strip_fits_the_help_window(self):
        import pkg_rlc.present.help as pkg_rlc_help
        from tkinter import ttk

        root = tk.Tk()
        root.withdraw()
        try:
            probe = ttk.Notebook(root)
            for title, _text in pkg_rlc_help.HELP_TOPICS:
                probe.add(ttk.Frame(probe, width=1, height=1), text=title)
            probe.pack()
            root.update_idletasks()
            needed = probe.winfo_reqwidth()
        finally:
            root.destroy()
        window = pkg_rlc_help.HELP_WINDOW_WIDTH
        self.assertLessEqual(
            needed, window,
            f"the Help tab strip needs {needed} px and the window is "
            f"{window} px wide, so the last tab is unreachable")

    def test_the_window_really_opens_at_that_width(self):
        """The measurement above only guards anything if it tracks reality."""
        import pkg_rlc.present.help as pkg_rlc_help
        root = tk.Tk()
        root.withdraw()
        try:
            win = pkg_rlc_help.HelpWindow(root)
            win.update_idletasks()
            geometry = win.geometry()
        finally:
            root.destroy()
        self.assertTrue(
            geometry.startswith(f"{pkg_rlc_help.HELP_WINDOW_WIDTH}x"), geometry)


if __name__ == "__main__":
    unittest.main()
