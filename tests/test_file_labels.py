"""
Two loaded files with the same NAME are two files, not one.

THE BUG.  `FileEntry.label` was the basename, and the label is the KEY every
trace binds its file by (`tc.file_label`, `_file_by_label`, the session file).
Load `ind_30G/L.s1p` (a 30 GHz sweep) and `ind_80G/L.s1p` (an 80 GHz one) and
both got the label `L.s1p`; every lookup returned the FIRST, so the trace made
for the 80 GHz file was computed and drawn from the 30 GHz one.  Reported by
the owner as "with a 30G and an 80G file loaded, the plot only reaches 30G",
and separately as "two files of the same name cannot be told apart in the
traces" -- one defect, seen from two sides.

What is pinned:

  * `distinct_file_labels`: a basename unless another loaded file shares it;
    then the fewest trailing folders that separate the group, the same depth
    for the whole group; the same path twice is numbered.
  * `file_relabels` never moves the traces of a label some file KEEPS, and a
    clash resolves to the FIRST file -- what `_file_by_label` always returned.
  * Through the real Add File handler: the second same-named file draws ITS
    sweep (the plot reaches 80 GHz), both are labelled with their folder, the
    first file's traces follow the rename, removing one gives the other its
    plain name back, and a session reloads under the labels it saved.
  * The editor is reloaded after a relabel, so the next auto-apply cannot
    write the old label back into the trace.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from pkg_rlc.model.trace import (  # noqa: E402
    TraceConfig,
    default_trace_label,
    distinct_file_labels,
    file_relabels,
    rebind_file_labels,
)


# ============================================================================
# Pure
# ============================================================================


class TestDistinctFileLabels(unittest.TestCase):

    def test_distinct_basenames_are_untouched(self):
        self.assertEqual(
            distinct_file_labels(["C:/a/L30.s1p", "C:/b/L80.s1p"]),
            ["L30.s1p", "L80.s1p"])

    def test_a_clash_takes_one_folder_on_every_member(self):
        self.assertEqual(
            distinct_file_labels(["C:/emx/ind_30G/L.s1p",
                                  "C:/emx/ind_80G/L.s1p",
                                  "C:/emx/other.s2p"]),
            ["ind_30G/L.s1p", "ind_80G/L.s1p", "other.s2p"])

    def test_the_depth_is_what_separates_them_and_is_shared(self):
        # Two share the parent folder name 'out', so one folder does not
        # separate the group; the third file takes the same depth.
        got = distinct_file_labels(["/r/run1/out/L.s1p", "/r/run2/out/L.s1p",
                                    "/q/x/L.s1p"])
        self.assertEqual(got, ["run1/out/L.s1p", "run2/out/L.s1p",
                               "q/x/L.s1p"])
        self.assertEqual(len(set(got)), 3)

    def test_both_separators_name_the_same_folders(self):
        self.assertEqual(
            distinct_file_labels(["C:\\d\\a\\L.s1p", "/home/u/b/L.s1p"]),
            ["a/L.s1p", "b/L.s1p"])

    def test_the_same_path_twice_is_numbered(self):
        self.assertEqual(
            distinct_file_labels(["C:/a/L.s1p", "C:/a/L.s1p", "C:/b/L.s1p"]),
            ["a/L.s1p", "a/L.s1p (2)", "b/L.s1p"])
        self.assertEqual(distinct_file_labels(["C:/a/L.s1p", "C:/a/L.s1p"]),
                         ["L.s1p", "L.s1p (2)"])


class TestFileRelabels(unittest.TestCase):

    def test_a_clash_hands_the_old_label_to_the_FIRST_file(self):
        new, mapping = file_relabels([("L.s1p", "C:/a/L.s1p"),
                                      ("L.s1p", "C:/b/L.s1p")])
        self.assertEqual(new, ["a/L.s1p", "b/L.s1p"])
        self.assertEqual(mapping, {"L.s1p": "a/L.s1p"})

    def test_a_kept_label_is_never_remapped(self):
        # The same file twice: the first copy keeps 'L.s1p', and its traces
        # must not move to the second.
        new, mapping = file_relabels([("L.s1p", "C:/a/L.s1p"),
                                      ("L.s1p", "C:/a/L.s1p")])
        self.assertEqual(new, ["L.s1p", "L.s1p (2)"])
        self.assertEqual(mapping, {})

    def test_nothing_to_do_is_an_empty_mapping(self):
        self.assertEqual(file_relabels([("x.s2p", "/a/x.s2p")]),
                         (["x.s2p"], {}))

    def test_rebind_moves_home_extras_and_only_the_default_label(self):
        own = TraceConfig(id=1, file_label="L.s1p",
                          label=default_trace_label("L.s1p"),
                          file_labels=["L.s1p", "z.s2p"])
        typed = TraceConfig(id=2, file_label="L.s1p", label="my coil")
        other = TraceConfig(id=3, file_label="z.s2p",
                            label=default_trace_label("z.s2p"))
        rebind_file_labels([own, typed, other], {"L.s1p": "a/L.s1p"})
        self.assertEqual(own.file_label, "a/L.s1p")
        self.assertEqual(own.label, "a/L.s1p_p1_to_gnd")
        self.assertEqual(own.file_labels, ["a/L.s1p", "z.s2p"])
        self.assertEqual((typed.file_label, typed.label),
                         ("a/L.s1p", "my coil"))
        self.assertEqual((other.file_label, other.label),
                         ("z.s2p", "z.s2p_p1_to_gnd"))


# ============================================================================
# Tk-driven, through the real Add File handler
# ============================================================================

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    TK_OK = True
except Exception:                                   # pragma: no cover
    TK_OK = False


def _write_inductor(path: Path, fmax: float) -> None:
    from generate_test_snp import write_touchstone
    f = np.linspace(1e8, fmax, 201)
    z = 1.0 + 1j * 2 * np.pi * f * 1e-9
    write_touchstone(path, f, ((z - 50) / (z + 50)).reshape(-1, 1, 1))


@unittest.skipUnless(TK_OK, "no display")
class TestSameNameFilesInTheApp(unittest.TestCase):

    def setUp(self):
        import pkg_rlc.frontend.app as pkg_rlc_gui
        import pkg_rlc.panels.panels_files as panels_files
        self.gui = pkg_rlc_gui
        self.panels_files = panels_files
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.p30 = tmp / "ind_30G" / "L.s1p"
        self.p80 = tmp / "ind_80G" / "L.s1p"
        _write_inductor(self.p30, 30e9)
        _write_inductor(self.p80, 80e9)
        self.app = pkg_rlc_gui.App()
        self.app.withdraw()

    def tearDown(self):
        self.app.destroy()
        self._tmp.cleanup()

    def _settle(self):
        for _ in range(3):
            self.app.update_idletasks()
            self.app.update()

    def _add(self, *paths):
        with mock.patch.object(self.panels_files.filedialog,
                               "askopenfilenames",
                               return_value=tuple(str(p) for p in paths)):
            self.app._on_add_file()
        self._settle()

    def _log(self):
        return self.app.results_text.get("1.0", tk.END)

    def test_the_80G_trace_draws_the_80G_file(self):
        """The owner's report, measured on the plot the user looks at."""
        self._add(self.p30)
        self.app._on_calculate()
        self._add(self.p80)
        self.app._on_calculate()
        self._settle()
        fmax = sorted(float(t.freqs.max()) for t in self.app.plot.view.traces)
        self.assertEqual(fmax, [30e9, 80e9])
        self.assertAlmostEqual(self.app.plot.view.axes[0].get_xlim()[1] / 1e9,
                               80.0, places=6)

    def test_both_files_and_both_traces_can_be_told_apart(self):
        self._add(self.p30)
        self._add(self.p80)
        self.assertEqual([fe.label for fe in self.app.files],
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertEqual([t.file_label for t in self.app.traces],
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertEqual([t.label for t in self.app.traces],
                         ["ind_30G/L.s1p_p1_to_gnd",
                          "ind_80G/L.s1p_p1_to_gnd"])
        self.assertEqual(list(self.app.ed_file_cbo["values"]),
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertTrue(self.app.files_lb.get(0).startswith("ind_30G/L.s1p"))
        self.assertIn("'L.s1p' is now 'ind_30G/L.s1p'", self._log())
        self.assertIn("shown as 'ind_80G/L.s1p'", self._log())

    def test_the_editor_cannot_write_the_old_label_back(self):
        # First trace selected in the editor, THEN the clash renames it.
        self._add(self.p30)
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()
        self._add(self.p80)
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()
        # Any auto-apply now pushes every editor field into the trace.
        self.app.ed_label.set_value("renamed by hand")
        self.app._flush_editor_sync()
        self._settle()
        tc = self.app.traces[0]
        self.assertEqual(tc.file_label, "ind_30G/L.s1p")
        self.assertEqual(tc.label, "renamed by hand")

    def test_removing_one_gives_the_other_its_plain_name_back(self):
        self._add(self.p30, self.p80)
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(1)
        self.app._on_remove_file()
        self._settle()
        self.assertEqual([fe.label for fe in self.app.files], ["L.s1p"])
        self.assertEqual([(t.file_label, t.label) for t in self.app.traces],
                         [("L.s1p", "L.s1p_p1_to_gnd")])

    def test_removing_the_other_file_under_the_editor_keeps_the_binding(self):
        """The survivor's trace is on screen when its label changes back; the
        editor still held 'ind_30G/L.s1p' and the next auto-apply wrote it
        into the trace, binding it to a label no file has any more."""
        self._add(self.p30, self.p80)
        self.app.traces_lb.selection_clear(0, tk.END)
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()
        self.app.files_lb.selection_clear(0, tk.END)
        self.app.files_lb.selection_set(1)
        self.app._on_remove_file()
        self._settle()
        self.app.ed_label.set_value("renamed by hand")
        self.app._flush_editor_sync()
        self._settle()
        tc = self.app.traces[0]
        self.assertEqual(tc.file_label, "L.s1p")
        self.assertIsNotNone(self.app._file_by_label(tc.file_label))

    def test_a_session_reloads_under_the_labels_it_saved(self):
        self._add(self.p30, self.p80)
        data = json.loads(json.dumps(self.app._session_dict(None)))
        sess = self.gui.session_from_dict(data)
        self.app._apply_session(sess, "test")
        self._settle()
        self.assertEqual([fe.label for fe in self.app.files],
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertEqual([t.file_label for t in self.app.traces],
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertNotIn("re-bound", self._log().split("=== test ===")[-1])

    def test_an_old_session_with_one_label_for_two_files_binds_the_first(self):
        """Saved before the fix: both files 'L.s1p', both traces resolved to
        the first.  They still do -- nothing silently changes which data a
        saved trace draws -- and the log says the name moved."""
        tcs = [TraceConfig(id=i, file_label="L.s1p", mode=1, port_a="1",
                           label=f"t{i}") for i in (1, 2)]
        sess = self.gui.LoadedSession(
            files=[("L.s1p", str(self.p30), True),
                   ("L.s1p", str(self.p80), True)],
            traces=tcs)
        self.app._apply_session(sess, "test")
        self._settle()
        self.assertEqual([fe.label for fe in self.app.files],
                         ["ind_30G/L.s1p", "ind_80G/L.s1p"])
        self.assertEqual([t.file_label for t in self.app.traces],
                         ["ind_30G/L.s1p", "ind_30G/L.s1p"])
        self.assertIn("re-bound", self._log())


if __name__ == "__main__":
    unittest.main()
