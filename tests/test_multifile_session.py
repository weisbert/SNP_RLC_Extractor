"""
One trace, several files: the SCHEMA half (requirement R3-1).

A trace used to be bound to exactly one file through `TraceConfig.file_label`,
a single `str` that ~20 places read -- the staleness signature, both run
snapshots, the port descriptor, the replot, the file-removal sweep, the CSV
block headers, the plot legend, the freeze snapshot and the session file's
per-file path recording.  Composition needs a trace to name SEVERAL files, and
this file is the guard on doing that without moving anything that already
worked.

Five properties, and the first is the whole reason the schema is shaped the way
it is:

  * A HOME FILE PLUS EXTRAS, not one list of files.  `file_label` keeps its
    meaning -- a bare port number means a port of the home file, in every mode
    -- so every pre-existing spec, every golden case and every saved session
    reads exactly as before, and a single-file user never sees a file tag.  It
    is also the only layout that FITS: measured, a per-row file column costs
    451 px against the editor's 431 px viewport.
  * ONE TAG AUTHORITY.  A file's tag is its POSITION (F1 is the home file), and
    `pkg_rlc_files_gui` resolves the same list the same way.  The two rules are
    pinned against each other here, because a tag that meant one file in a port
    cell and another in the engine is a silent wrong answer.
  * BYTE IDENTITY.  A trace with no extra file serialises to exactly the bytes
    the previous build wrote -- no 'file_labels' key at all.  The reference
    below was captured by running the build immediately before this change.
  * THE LIST-ALIASING TRAP, third field.  Duplicate and Freeze must COPY
    `file_labels`, or two traces silently share one file set.
  * NOTHING SILENTLY SINGLE-FILE.  Calculate now COMBINES the files (the engine
    landed), so the guard moved from "it is refused by name" to the two things
    that must be true of the answer: the row is built from every file the trace
    names, and its numbers live on the COMPOSED frequency axis rather than on
    the home file's -- computing the home file alone would produce a
    well-formed number of the right order from a network with the package
    missing, which is the exact failure composition exists to end.

Every guard here was mutation-checked.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

import numpy as np  # noqa: E402

import pkg_rlc.panels.files_gui as pkg_rlc_files_gui  # noqa: E402
import pkg_rlc.frontend.app as pkg_rlc_gui  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    ConnectionRow,
    CouplingResult,
    MeasPortRow,
    PortRLC,
    parse_touchstone,
)
from pkg_rlc.frontend.app import (  # noqa: E402
    App,
    FileEntry,
    LoadedSession,
    SessionError,
    TraceConfig,
    _config_signature,
    _duplicate_trace_config,
    _format_coupling_block,
    _format_results_table,
    _freeze_trace_config,
    _snapshot_files,
    compose_spec_problems,
    session_from_dict,
    session_to_dict,
    trace_file_aliases,
    trace_file_labels,
    trace_file_legend,
    trace_file_scope,
    trace_from_dict,
    trace_is_composed,
    trace_signature_fields,
    trace_to_dict,
)

FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIX / "diff_pair_4port.s4p"
# A SECOND GRID, on purpose.  pi_2port.s2p is 401 points on exactly the same
# axis as FIXTURE, so a composition of the two takes align_frequencies'
# identical-grid fast path and the composed axis IS the home file's -- which
# makes "these numbers are on the composed axis" untestable, because the two
# answers are the same array.  coupled_2port_gndref.s2p is 100 points over
# 100 MHz - 10 GHz against FIXTURE's 401 over 1 MHz - 10 GHz, so the
# intersection resampled onto the finer grid is 397 points: an axis NEITHER
# file has, and the only kind that can tell a real composition from a
# home-file-only one.
FIXTURE2 = FIX / "coupled_2port_gndref.s2p"


def _ensure_fixtures():
    if not FIXTURE.exists() or not FIXTURE2.exists():
        import generate_test_snp
        generate_test_snp.main()


try:
    _root = tk.Tk()
    _root.destroy()
    TK_OK = True
except Exception:                                   # pragma: no cover
    TK_OK = False


def _plain() -> TraceConfig:
    """The trace the byte reference below was captured from."""
    return TraceConfig(id=1, file_label="coil.s4p", mode=1, port_a="1",
                       gnd_ports="2-4", label="t1")


def _composed() -> TraceConfig:
    return TraceConfig(
        id=2, file_label="die.s6p", file_labels=["package.s4p"], mode=5,
        label="osc",
        conn_rows=[ConnectionRow(kind="short", ports="2", to="3,4"),
                   ConnectionRow(kind="rlc_between", ports="2",
                                 to="F2.1", L="10f")],
        mports=[MeasPortRow(name="coil", plus="1", minus="5")])


# The exact `trace_to_dict` output of `_plain()`, captured by running the build
# IMMEDIATELY BEFORE the multi-file field was added:
#
#   python -c "import pkg_rlc_gui as g, json; print(json.dumps(
#              g.trace_to_dict(<_plain()>)))"
#
# Written out in full rather than derived from _config_trace_fields(), which
# would be a tautology: a reference that recomputes itself from the code it
# guards cannot notice that code changing.
BYTES_BEFORE = (
    '{"id": 1, "file_label": "coil.s4p", "mode": 1, "port_a": "1", '
    '"port_b": "", "short_pairs": "", "gnd_ports": "2-4", "mports": [], '
    '"conn_rows": [], "extra_lines": "", "plot_self": true, '
    '"plot_mutual": true, "enabled": true, "frozen": false, "label": "t1", '
    '"color_idx": 0, "ls_idx": 0}')


def _dump_load(data: dict) -> dict:
    return json.loads(json.dumps(data))


# ============================================================================
# Byte identity for the single-file case
# ============================================================================

class TestSingleFileIsUntouched(unittest.TestCase):

    def test_a_single_file_trace_serialises_to_exactly_the_old_bytes(self):
        """
        Key ORDER included: the assertion is on the JSON string, not on a dict
        comparison, because dicts compare equal however the keys are ordered
        and the point of _OPTIONAL_TRACE_FIELDS is that the file does not move.
        """
        self.assertEqual(json.dumps(trace_to_dict(_plain())), BYTES_BEFORE)

    def test_the_new_field_appears_nowhere_in_an_uncomposed_session(self):
        text = json.dumps(session_to_dict([], [_plain()], {}, {},
                                          saved_utc="X"))
        self.assertNotIn("file_labels", text)

    def test_a_session_written_before_this_build_loads_with_no_file_set(self):
        old = {"format": pkg_rlc_gui.SESSION_FORMAT, "version": 1,
               "files": [], "traces": [json.loads(BYTES_BEFORE)]}
        sess = session_from_dict(old)
        tc = sess.traces[0]
        self.assertEqual(tc.file_labels, [])
        self.assertFalse(trace_is_composed(tc))
        self.assertEqual(trace_file_labels(tc), ["coil.s4p"])
        self.assertEqual(sess.warnings, [],
                         "an old file must not warn about its own contents")

    def test_an_old_trace_re_saves_to_the_bytes_it_arrived_as(self):
        back = trace_from_dict(json.loads(BYTES_BEFORE), lambda m: None)
        self.assertEqual(json.dumps(trace_to_dict(back)), BYTES_BEFORE)


class TestSessionVersion(unittest.TestCase):

    def test_the_written_version_is_this_build_s(self):
        data = session_to_dict([], [_plain()], {}, {}, saved_utc="X")
        self.assertEqual(data["version"], pkg_rlc_gui.SESSION_VERSION)

    def test_a_composed_session_is_refused_by_a_build_that_reads_only_v1(self):
        """
        The version is what stops an older build loading a composed trace,
        dropping `file_labels` with a note, and then computing the HOME FILE
        ALONE -- a well-formed number from a network missing the other file.
        From inside one build that can only be expressed one way: the file's
        version is above the 1 an older build reads up to.
        """
        data = session_to_dict([], [_composed()], {}, {}, saved_utc="X")
        self.assertGreater(data["version"], 1)

    def test_a_file_from_a_newer_build_still_names_both_numbers(self):
        future = pkg_rlc_gui.SESSION_VERSION + 1
        with self.assertRaises(SessionError) as cm:
            session_from_dict({"format": pkg_rlc_gui.SESSION_FORMAT,
                               "version": future})
        msg = str(cm.exception)
        self.assertIn(str(future), msg)
        self.assertIn(str(pkg_rlc_gui.SESSION_VERSION), msg)


# ============================================================================
# Resolving the file set -- and agreeing with the module that draws it
# ============================================================================

class TestFileSet(unittest.TestCase):

    def test_the_home_file_is_first_and_is_not_in_the_list(self):
        tc = _composed()
        self.assertEqual(trace_file_labels(tc), ["die.s6p", "package.s4p"])
        self.assertEqual(tc.file_labels, ["package.s4p"])

    def test_the_tag_is_the_POSITION(self):
        tc = TraceConfig(file_label="a.s2p",
                         file_labels=["b.s2p", "c.s2p"])
        self.assertEqual(trace_file_aliases(tc),
                         [("F1", "a.s2p"), ("F2", "b.s2p"), ("F3", "c.s2p")])

    def test_a_repeat_of_the_home_file_counts_once(self):
        """
        A label resolves through _file_by_label to ONE FileEntry, so a second
        copy of it could only ever be the same block again -- and the tag it
        would have had is a tag no port cell could use.
        """
        tc = TraceConfig(file_label="a.s2p", file_labels=["a.s2p", "b.s2p"])
        self.assertEqual(trace_file_labels(tc), ["a.s2p", "b.s2p"])

    def test_a_trace_with_no_home_file_still_lists_its_extras(self):
        tc = TraceConfig(file_label="", file_labels=["b.s2p"])
        self.assertEqual(trace_file_labels(tc), ["b.s2p"])

    def test_the_legend_names_every_file_with_its_tag(self):
        self.assertEqual(trace_file_legend(_composed()),
                         "F1=die.s6p + F2=package.s4p")


class TestTheFilesWindowReadsTheSameFileSet(unittest.TestCase):
    """
    pkg_rlc_files_gui reads a trace through its own `TRACE_FILES_FIELD` so it
    can degrade gracefully on a build without this schema.  That flexibility is
    exactly what makes a silent disagreement possible: if it read a field this
    module does not write, its window and its port-cell scope hints would
    describe a one-file trace while Calculate refused a two-file one.
    """

    def test_it_reads_the_field_this_module_writes(self):
        declared = {f.name for f in fields(TraceConfig)}
        self.assertIn(pkg_rlc_files_gui.TRACE_FILES_FIELD, declared)
        self.assertIn(pkg_rlc_files_gui.TRACE_HOME_FIELD, declared)
        self.assertTrue(pkg_rlc_files_gui.trace_files_supported())

    def test_the_two_resolvers_agree_on_every_shape(self):
        """
        Against the FALLBACK, not against `trace_file_labels` -- that one
        delegates here, so comparing with it would be a tautology.  The
        fallback is what runs on a build without this schema, and it is the
        copy that can drift when this side changes.
        """
        cases = [
            TraceConfig(file_label="a.s2p"),
            TraceConfig(file_label="a.s2p", file_labels=["b.s2p"]),
            TraceConfig(file_label="a.s2p", file_labels=["b.s2p", "c.s2p"]),
            TraceConfig(file_label="a.s2p", file_labels=["a.s2p", "b.s2p"]),
            TraceConfig(file_label="", file_labels=["b.s2p"]),
            TraceConfig(file_label="a.s2p", file_labels=[]),
        ]
        for tc in cases:
            with self.subTest(files=(tc.file_label, tc.file_labels)):
                self.assertEqual(
                    trace_file_labels(tc),
                    pkg_rlc_files_gui._trace_file_labels_fallback(tc))

    def test_both_number_the_tags_from_the_same_function(self):
        tc = TraceConfig(file_label="a.s2p", file_labels=["b.s2p"])
        mine = [a for a, _ in trace_file_aliases(tc)]
        theirs = [pkg_rlc_files_gui.default_alias(i)
                  for i in range(len(trace_file_labels(tc)))]
        self.assertEqual(mine, theirs)


class TestComposeSpecProblems(unittest.TestCase):

    def test_a_clean_pair_has_nothing_to_say(self):
        self.assertEqual(
            compose_spec_problems(_composed(), ["die.s6p", "package.s4p"]), [])

    def test_an_empty_entry_is_reported(self):
        tc = TraceConfig(file_label="a.s2p", file_labels=["", "b.s2p"])
        self.assertTrue(any("empty" in m for m in compose_spec_problems(tc)))

    def test_a_repeat_is_reported_rather_than_silently_collapsed(self):
        tc = TraceConfig(file_label="a.s2p", file_labels=["a.s2p"])
        msgs = compose_spec_problems(tc)
        self.assertTrue(any("counts once" in m for m in msgs), msgs)

    def test_a_file_that_is_not_loaded_is_reported_when_the_list_is_given(self):
        msgs = compose_spec_problems(_composed(), ["die.s6p"])
        self.assertTrue(any("package.s4p" in m and "not loaded" in m
                            for m in msgs))
        # ... and NOT when it is not: the pure half has to work with no App.
        self.assertEqual(compose_spec_problems(_composed()), [])

    def test_it_never_raises_on_junk(self):
        """
        It is written to be callable from the editor strips, which run inside
        Tk variable traces -- a raise there reaches no handler you control.
        """
        junk = TraceConfig(file_label="a.s2p", file_labels=[None, 7, object()])
        try:
            compose_spec_problems(junk, [])
        except Exception as e:                      # pragma: no cover
            self.fail(f"compose_spec_problems raised {e!r}")


# ============================================================================
# Field coverage and the round trip
# ============================================================================

class TestFieldCoverage(unittest.TestCase):

    def test_the_new_field_is_config_not_computed(self):
        config = set(pkg_rlc_gui._config_trace_fields())
        computed = set(pkg_rlc_gui._COMPUTED_TRACE_FIELDS)
        declared = {f.name for f in fields(TraceConfig)}
        self.assertEqual(config | computed, declared)
        self.assertEqual(config & computed, set())
        self.assertIn("file_labels", config)

    def test_it_is_coerced_as_a_list_of_names(self):
        """
        Without an entry in _TRACE_STRLIST_FIELDS the default branch `str()`s
        the whole list and stores its REPR -- a field that round-trips into
        garbage rather than failing.
        """
        self.assertIn("file_labels", pkg_rlc_gui._TRACE_STRLIST_FIELDS)

    def test_the_optional_list_names_real_fields(self):
        declared = {f.name for f in fields(TraceConfig)}
        self.assertLessEqual(set(pkg_rlc_gui._OPTIONAL_TRACE_FIELDS), declared)


class TestComposedRoundTrip(unittest.TestCase):

    def test_a_composed_trace_comes_back_unchanged(self):
        src = _composed()
        back = trace_from_dict(_dump_load(trace_to_dict(src)), lambda m: None)
        self.assertEqual(back.file_labels, ["package.s4p"])
        self.assertEqual(trace_file_legend(back), trace_file_legend(src))

    def test_the_restored_list_is_a_new_object_of_plain_strings(self):
        src = _composed()
        back = trace_from_dict(_dump_load(trace_to_dict(src)), lambda m: None)
        self.assertIsNot(back.file_labels, src.file_labels)
        for lbl in back.file_labels:
            self.assertIsInstance(lbl, str)

    def test_a_hand_edited_entry_costs_itself_and_not_the_file(self):
        notes = []
        data = json.loads(BYTES_BEFORE)
        data["file_labels"] = ["  pkg.s4p  ", "", {"path": "x"}]
        tc = trace_from_dict(data, notes.append)
        self.assertEqual(tc.file_labels, ["pkg.s4p"],
                         "a padded name must be normalised on the way IN, "
                         "where both resolvers see the same thing")
        self.assertTrue(any("not a name" in n for n in notes), notes)

    def test_a_field_that_is_not_a_list_is_dropped_with_a_note(self):
        notes = []
        data = json.loads(BYTES_BEFORE)
        data["file_labels"] = "pkg.s4p"
        tc = trace_from_dict(data, notes.append)
        self.assertEqual(tc.file_labels, [])
        self.assertTrue(any("not a list" in n for n in notes), notes)


# ============================================================================
# The staleness signature and the run diff
# ============================================================================

class TestSignature(unittest.TestCase):

    def test_the_named_signature_still_matches_config_signature_one_for_one(self):
        tc = _plain()
        self.assertEqual(len(trace_signature_fields(tc)),
                         len(_config_signature(tc)))

    def test_adding_a_file_makes_the_curve_stale_and_the_run_page_say_so(self):
        a, b = _plain(), _plain()
        b.file_labels = ["pkg.s4p"]
        self.assertNotEqual(_config_signature(a), _config_signature(b))
        self.assertNotEqual(trace_signature_fields(a),
                            trace_signature_fields(b))

    def test_swapping_the_second_file_is_a_change_too(self):
        """
        The home file is already watched by `file_label`; without the file-set
        element, changing only the SECOND file would move the numbers with
        nothing marking the curve stale.
        """
        a = _composed()
        b = _composed()
        b.file_labels = ["other_package.s4p"]
        self.assertNotEqual(_config_signature(a), _config_signature(b))
        self.assertNotEqual(trace_signature_fields(a),
                            trace_signature_fields(b))

    def test_a_trace_that_predates_composition_reports_an_EMPTY_scope(self):
        """
        Which keeps every existing run page's diff exactly as it was -- a new
        signature field that always had a value would add a line to the first
        run after the upgrade and to no other.
        """
        self.assertEqual(trace_file_scope(_plain()), "")
        self.assertIn(("files", ""), trace_signature_fields(_plain()))

    def test_the_diff_names_the_file_set_in_words(self):
        prev = pkg_rlc_gui.run_signatures([_plain()])
        b = _plain()
        b.file_labels = ["pkg.s4p"]
        cur = pkg_rlc_gui.run_signatures([b])
        lines = pkg_rlc_gui.describe_run_change(prev, cur)
        self.assertTrue(any(line.startswith("[1] files ") for line in lines),
                        lines)


# ============================================================================
# The list-aliasing trap, third field
# ============================================================================

class TestCopyingATraceCopiesItsFileSet(unittest.TestCase):

    def test_duplicate_does_not_share_the_file_list(self):
        src = _composed()
        copy = _duplicate_trace_config(src, new_id=9)
        self.assertIsNot(copy.file_labels, src.file_labels)
        copy.file_labels.append("third.s4p")
        self.assertEqual(src.file_labels, ["package.s4p"])

    def test_freeze_does_not_share_the_file_list(self):
        src = _composed()
        snap = _freeze_trace_config(src, new_id=9, stamp="21:36")
        self.assertIsNot(snap.file_labels, src.file_labels)
        snap.file_labels.append("third.s4p")
        self.assertEqual(src.file_labels, ["package.s4p"])

    def test_a_frozen_snapshot_keeps_the_file_set_it_was_measured_from(self):
        src = _composed()
        snap = _freeze_trace_config(src, new_id=9, stamp="21:36")
        self.assertEqual(trace_file_legend(snap), trace_file_legend(src))


# ============================================================================
# What a run record says about where its numbers came from
# ============================================================================

class _Res:
    R_ohm = 1.5
    L_henry = 2e-9
    C_farad = -1.2e-12
    Q = 4.2
    freq_hz = 5e9


def _row(tc, file_label):
    return pkg_rlc_gui._snapshot_row(tc, file_label, _Res())


class TestSnapshotProvenance(unittest.TestCase):

    def test_a_single_file_snapshot_carries_no_file_list_at_all(self):
        """
        Which is what keeps every renderer -- and
        tests/fixtures/render_reference.json -- byte-identical.
        """
        self.assertEqual(_snapshot_files(_plain()), ())
        self.assertEqual(_row(_plain(), "coil.s4p").files, ())

    def test_a_composed_snapshot_carries_the_tags_and_the_order(self):
        self.assertEqual(_snapshot_files(_composed()),
                         (("F1", "die.s6p"), ("F2", "package.s4p")))

    def test_the_snapshot_resolves_the_file_set_at_snapshot_time(self):
        """
        Same hazard, and the same fix, as `label` and `port_desc`: the live
        trace can be re-pointed at other files after the run, and a record that
        re-read it would print the new provenance beside the old numbers.
        """
        tc = _composed()
        rec = _row(tc, tc.file_label)
        tc.file_labels = ["something_else.s4p"]
        tc.file_label = "elsewhere.s6p"
        self.assertEqual(rec.files,
                         (("F1", "die.s6p"), ("F2", "package.s4p")))

    def test_the_record_holds_no_arrays(self):
        rec = _row(_composed(), "die.s6p")
        for tag, label in rec.files:
            self.assertIsInstance(tag, str)
            self.assertIsInstance(label, str)


class TestResultsTableProvenance(unittest.TestCase):

    def test_one_file_renders_exactly_as_before(self):
        rows = [_row(_plain(), "coil.s4p")]
        text = _format_results_table(rows, "smart")
        self.assertIn("file: coil.s4p", text)
        self.assertNotIn("File", text.splitlines()[1])

    def test_two_single_file_traces_still_get_a_four_wide_file_column(self):
        """
        The File column only ever widens for a COMPOSED row: 'F1'/'F2' against
        a 4-character header is what it always was.
        """
        a = _row(_plain(), "coil.s4p")
        b = _row(_plain(), "pkg.s4p")
        lines = _format_results_table([a, b], "smart").splitlines()
        self.assertIn("F1=coil.s4p", lines[0])
        self.assertIn("F2=pkg.s4p", lines[0])
        self.assertIn("File  ", lines[1])
        self.assertIn("F1    ", lines[2])

    def test_a_composed_row_names_every_file_it_came_from(self):
        rows = [_row(_composed(), "die.s6p")]
        lines = _format_results_table(rows, "smart").splitlines()
        self.assertIn("F1=die.s6p", lines[0])
        self.assertIn("F2=package.s4p", lines[0])
        self.assertIn("F1+F2", lines[2])

    def test_the_column_grows_to_fit_the_composed_cell(self):
        """
        'F1+F2' is 5 characters against a 4-wide header, and a cell wider than
        its column throws every column to the right of it out of line.
        """
        rows = [_row(_composed(), "die.s6p"), _row(_plain(), "coil.s4p")]
        lines = _format_results_table(rows, "smart").splitlines()
        header, first, second = lines[1], lines[2], lines[3]
        self.assertEqual(header.index("Ports"), first.index("M5"))
        self.assertEqual(first.index("M5"), second.index("M1"))

    def test_a_record_written_before_the_field_existed_still_renders(self):
        """
        tests/_render_capture.py and several test helpers build these records
        directly; a missing `files` has to read as "one file", not raise.
        """
        rec = _row(_plain(), "coil.s4p")
        stripped = type("Rec", (), {k: getattr(rec, k) for k in
                                    ("id", "label", "port_desc", "enabled",
                                     "color_idx", "file_label", "res")})()
        self.assertIn("file: coil.s4p",
                      _format_results_table([stripped], "smart"))


class TestCouplingBlockProvenance(unittest.TestCase):

    def _block(self, tc):
        names = ["L1", "L2"]
        cres = CouplingResult(
            freq_hz=1e8, Z_matrix=np.eye(2, dtype=complex) * complex(1.5, 1.26),
            names=names,
            ports=[PortRLC(name=n, Z=complex(1.5, 1.26), R_ohm=1.5,
                           L_henry=2e-9, C_farad=-1e-12, Q=0.84)
                   for n in names],
            pairs=[], reciprocity_error=1e-9)
        return pkg_rlc_gui._snapshot_block(tc, tc.file_label, cres)

    def test_one_file_heads_the_block_as_it_always_did(self):
        text = _format_coupling_block(self._block(_plain()), "smart")
        self.assertIn("|  file: coil.s4p  |", text)

    def test_a_composed_block_names_them_all_with_their_tags(self):
        text = _format_coupling_block(self._block(_composed()), "smart")
        self.assertIn("|  files: F1=die.s6p + F2=package.s4p  |", text)




# ============================================================================
# The real App
# ============================================================================

@unittest.skipUnless(TK_OK, "no display")
class _AppCase(unittest.TestCase):
    """An App with two files loaded, one plain trace and one composed."""

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(FIXTURE))
        self.fe2 = FileEntry(parse_touchstone(FIXTURE2))
        self.app.files.extend([self.fe, self.fe2])
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.plain = TraceConfig(id=1, file_label=self.fe.label, mode=1,
                                 port_a="1", gnd_ports="2-4", label="solo")
        self.comp = TraceConfig(id=2, file_label=self.fe.label, mode=1,
                                port_a="1", gnd_ports="2-4", label="pair",
                                file_labels=[self.fe2.label])
        self.app.traces.extend([self.plain, self.comp])
        self.app._refresh_trace_list()
        self._settle()

    def tearDown(self):
        self.app.destroy()
        self._tmp.cleanup()

    def _settle(self, rounds=3):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _log(self) -> str:
        return self.app.results_text.get("1.0", tk.END)

    def _select_trace(self, idx):
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(idx)
        self.app._on_trace_selected()
        self._settle()


class TestTracesListShowsTheFileSet(_AppCase):

    def test_a_composed_trace_counts_its_extra_files(self):
        """
        Measured (Microsoft YaHei UI 9, 444 px of list): a representative entry
        is 388 px bare and 408 px with ' +1' -- and 408 px with ' +2' and
        ' +9', so the count cannot jitter the rest of the line.  ' +1 file' is
        429 px, 15 px from the edge.
        """
        self.assertNotIn(" +", self.plain.info_str())
        self.assertIn(f"{self.fe.label} +1", self.comp.info_str())


class TestRemovingAFile(_AppCase):

    def _select_file(self, idx):
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(idx)

    def test_removing_a_file_used_ONLY_as_an_extra_removes_its_trace(self):
        """
        A composed trace whose second file is gone is not a single-file trace
        -- it is one that cannot be computed at all.  Leaving it bound to a
        file that is not loaded is how the plot and the Traces list come to
        disagree about which measurements exist.
        """
        self._select_file(1)                    # the file only `comp` uses
        self.app._on_remove_file()
        self._settle()
        self.assertEqual([tc.id for tc in self.app.traces], [1])

    def test_it_says_which_traces_went_and_why(self):
        self._select_file(1)
        self.app._on_remove_file()
        self._settle()
        log = self._log()
        self.assertIn("composed with it", log)
        self.assertIn("[2] pair", log)

    def test_removing_the_home_file_still_removes_both(self):
        self._select_file(0)
        self.app._on_remove_file()
        self._settle()
        self.assertEqual(self.app.traces, [])


class TestCalculateComposesTheFiles(_AppCase):
    """
    The engine half of "nothing silently single-file".

    `self.comp` names diff_pair_4port.s4p (4 ports, 100 points, 100 MHz -
    1 MHz - 10 GHz) and coupled_2port_gndref.s2p (2 ports, 100 points,
    100 MHz - 10 GHz), so the composed network is 6 ports on 397 points -- the
    intersection of the two spans resampled onto the finer grid, an axis
    NEITHER file has.  That is what makes every assertion below able to tell a
    real composition from a home-file-only one; see the note on FIXTURE2.
    """

    def test_a_composed_trace_produces_a_row_like_any_other(self):
        self.app._on_calculate()
        self._settle()
        run = self.app._last_run
        self.assertEqual(sorted(r.id for r in run.rows), [1, 2])
        self.assertIsNotNone(self.comp.Z)

    def test_its_numbers_are_on_the_COMPOSED_axis_not_the_home_file_s(self):
        """
        The one measurement that separates "composed" from "home file alone".
        Both files are on their own grid, so the composed axis has neither
        file's point count; a Z of len(home.freqs) would be the wrong answer
        with a plausible shape.
        """
        self.app._on_calculate()
        self._settle()
        self.assertIsNotNone(self.comp.net_freqs)
        n = len(self.comp.net_freqs)
        self.assertEqual(len(self.comp.Z), n)
        self.assertNotEqual(n, len(self.fe.ts.freqs))
        self.assertNotEqual(n, len(self.fe2.ts.freqs))

    def test_the_row_names_every_file_it_was_built_from(self):
        self.app._on_calculate()
        self._settle()
        rec = next(r for r in self.app._last_run.rows if r.id == 2)
        self.assertEqual([lbl for _a, lbl in rec.files],
                         [self.fe.label, self.fe2.label])

    def test_the_reference_node_check_ran_and_is_frozen_on_the_row(self):
        """R3-5.  A weld raises nothing and makes no number look wrong, so the
        verdict has to arrive where the number is read."""
        self.app._on_calculate()
        self._settle()
        self.assertEqual(len(self.comp.reference_checks or []), 2)
        rec = next(r for r in self.app._last_run.rows if r.id == 2)
        self.assertIn("Reference-node check", rec.ref_strip)
        self.assertIn(rec.ref_strip, self._log())

    def test_a_single_file_trace_carries_no_composed_axis_and_no_verdict(self):
        """Every field this round added is EMPTY on the case that is almost
        always the case, which is what keeps the rendered page byte-identical."""
        self.app._on_calculate()
        self._settle()
        self.assertIsNone(self.plain.net_freqs)
        self.assertFalse(self.plain.reference_checks)
        rec = next(r for r in self.app._last_run.rows if r.id == 1)
        self.assertEqual((rec.files, rec.ref_strip, rec.ref_lines),
                         ((), "", ()))

    def test_the_composed_axis_is_filed_under_the_file_LEGEND(self):
        """
        Not under either file's label.  The marker landed on an axis neither
        file has, so a header line naming one of them beside that number would
        be the disagreement `run.freqs` exists to remove.
        """
        self.app._on_calculate()
        self._settle()
        keys = [lbl for lbl, _ in self.app._last_run.freqs]
        self.assertIn(f"F1={self.fe.label} + F2={self.fe2.label}", keys)

    def test_a_missing_second_file_is_reported_as_MISSING(self):
        """
        A trace whose second file is not loaded has a problem the user can act
        on, and the missing-file check runs over EVERY file the trace names --
        naming only the home one sends them to fix half of it.
        """
        self.comp.file_labels = ["gone.s4p"]
        self.app._on_calculate()
        self._settle()
        log = self._log()
        self.assertIn("'gone.s4p'", log)
        self.assertIn("not loaded", log)
        self.assertIsNone(self.comp.Z)

    def test_a_bare_port_past_the_home_file_is_REFUSED_not_reassigned(self):
        """
        The silent-wrong-answer this namespace exists to prevent: the home file
        has 4 ports, so a bare '5' would otherwise address the SECOND file's
        first port -- a plausible number from the wrong network, with nothing
        on screen to say so.
        """
        self.comp.port_a = "5"
        self.app._on_calculate()
        self._settle()
        self.assertIsNone(self.comp.Z)
        self.assertIn("F1.5 does not exist", self._log())

    def test_a_tagged_port_reaches_the_second_file(self):
        self.comp.port_a = "1"
        self.comp.gnd_ports = "F2.1"
        self.app._on_calculate()
        self._settle()
        self.assertIsNotNone(self.comp.Z)
        self.assertIn("[2] pair", "".join(
            f"[{r.id}] {r.label}" for r in self.app._last_run.rows))


class TestSetTraceHomeFile(_AppCase):
    """The hook pkg_rlc_files_gui's 'Set home' button looks up by name."""

    def test_the_hook_exists_under_the_name_that_window_looks_for(self):
        self.assertTrue(callable(getattr(self.app, "set_trace_home_file",
                                         None)))

    def test_it_swaps_rather_than_dropping_or_adding_a_file(self):
        self.app.set_trace_home_file(self.comp, self.fe2.label)
        self._settle()
        self.assertEqual(self.comp.file_label, self.fe2.label)
        self.assertEqual(trace_file_labels(self.comp),
                         [self.fe2.label, self.fe.label])

    def test_it_goes_through_the_editor_for_the_selected_trace(self):
        """
        The editor owns the File combobox: a label poked straight onto the
        trace is overwritten by the very next _sync_editor_to_trace, which
        runs on the next keystroke.
        """
        self._select_trace(1)
        self.app.set_trace_home_file(self.comp, self.fe2.label)
        self._settle()
        self.assertEqual(self.app.ed_file_var.get(), self.fe2.label)
        self.app._flush_editor_sync()
        self.app._sync_editor_to_trace(self.comp)
        self.assertEqual(self.comp.file_label, self.fe2.label)

    def test_it_says_that_the_tags_renumbered(self):
        """
        A tag is a POSITION, so making F2 the home makes the old home F2 and
        every 'F2.<port>' already typed now names the other file.  Nothing can
        rewrite those cells, so the swap has to be reported where results are
        read.
        """
        self.app.set_trace_home_file(self.comp, self.fe2.label)
        self._settle()
        log = self._log()
        self.assertIn("renumbered", log)
        self.assertIn(self.fe.label, log)
        self.assertIn(self.fe2.label, log)

    def test_a_frozen_trace_refuses_by_name(self):
        self.comp.frozen = True
        self.app.set_trace_home_file(self.comp, self.fe2.label)
        self._settle()
        self.assertEqual(self.comp.file_label, self.fe.label)
        self.assertIn("frozen snapshot", self._log())

    def test_re_homing_a_computed_trace_marks_it_stale(self):
        self.comp.Z = np.zeros(3, dtype=complex)
        self.app.set_trace_home_file(self.comp, self.fe2.label)
        self._settle()
        self.assertTrue(self.comp.stale)


class TestSessionRebindsEveryFile(_AppCase):

    def test_an_extra_file_that_resolved_to_another_name_is_re_bound(self):
        """
        The only route a hand-edited config has to re-point at moved data.
        Re-binding the home file alone would leave a composed trace half
        resolved, naming a file that IS loaded under another name.
        """
        moved = self.tmp / "renamed_pkg.s2p"
        shutil.copyfile(FIXTURE2, moved)
        tc = TraceConfig(id=5, file_label="stale.s4p", mode=1, label="t",
                         file_labels=["old_name.s2p"])
        sess = LoadedSession(files=[("old_name.s2p", str(moved), True)],
                             traces=[tc])
        self.app._apply_session(sess, "test")
        self._settle()
        self.assertEqual(self.app.traces[0].file_labels, ["renamed_pkg.s2p"])
        self.assertIn("re-bound", self._log())


if __name__ == "__main__":
    unittest.main()
