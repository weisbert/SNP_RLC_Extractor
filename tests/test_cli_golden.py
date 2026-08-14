"""
The command line's OUTPUT, byte for byte.

`tests/fixtures/golden_legacy.npz` pins the numbers, `render_reference.json`
pins the GUI's results pane, and until this file existed nothing at all pinned
the third large rendered surface in the repo: the ~4400 lines of
`pkg_rlc_extractor.py`, most of which is print statements.  A refactor that
moves `_print_coupling_report`, the nine `_attr_print_*` sections, the five
`_cold_print_*` sections or any of the four CSV writers into a shared module
produces plausible text whether or not it is the text it produced yesterday --
and "plausible text" is precisely the failure the rest of this repo's
references exist to refuse.

So: `tests/_cli_capture.py` holds the case registry and the capture, this file
replays it.  143 invocations of `main(argv)` -- every mode, every flag in the
attribution, cold-start and composition groups, the CSV writers, and every
documented refusal -- compared line for line against the reference, with the
exit code and stderr pinned as hard as stdout.  A refusal test that only
asserted "it failed" would pass on the wrong message and on the wrong exit
code, which is the same objection `tests/test_compose_cli.py` already makes
about `exit 2` on its own.

WHAT A FAILURE HERE MEANS.  The CLI's output changed.  The assertion prints a
unified diff naming the case and its argv, so the first thing on screen is the
line that moved.  If the change was intended, regenerate in the SAME commit
that justifies it:

    python tests/_cli_capture.py

If it was not, fix the change -- do not regenerate the reference to make the
test pass.  That rule is the whole value of the file.

THE CAPTURE IS TAKEN ONCE, for the whole module, in registry order.  Two
reasons, both load-bearing: the reference was captured that way, so any
order-dependent state (a `warnings` registry that fires once per process, a
module-level cache) is exercised identically; and 143 invocations cost about
one second in total, which is only true because they run in-process rather
than as subprocesses.

This module imports no tkinter and QUALIFIES for the runner's `FAST_MODULES`
set on the one property that list has; adding it there is a one-line edit to
`tests/run_parallel.py` that this file deliberately does not make on its own.
`test_the_capture_never_reaches_the_gui` is the guard on the property.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _cli_capture as cap                                        # noqa: E402
import pkg_rlc.frontend.cli as ex                                    # noqa: E402


# ---------------------------------------------------------------------------
# The one capture, shared by every test in the module.
# ---------------------------------------------------------------------------

_CAPTURED: dict[str, dict] | None = None


def captured() -> dict[str, dict]:
    global _CAPTURED
    if _CAPTURED is None:
        _CAPTURED = cap.capture_all()
    return _CAPTURED


def _diff(name: str, argv: list[str], want: dict, got: dict) -> str:
    """A unified diff of the two records, headed by the case and its argv."""
    a = cap.dumps(want).splitlines()
    b = cap.dumps(got).splitlines()
    body = "\n".join(difflib.unified_diff(
        a, b, fromfile=f"reference/{name}.json",
        tofile=f"captured/{name}.json", lineterm="", n=3))
    return (
        f"\n\nThe CLI's output moved for case '{name}'.\n"
        f"  argv: {' '.join(argv)}\n"
        f"  describe: {want.get('describe', '')}\n"
        f"  reference: tests/fixtures/cli_reference/{name}.json\n"
        f"\n{body}\n\n"
        "If this change was INTENDED, regenerate the reference in the same "
        "commit that justifies it:\n"
        "    python tests/_cli_capture.py\n"
        "If it was not, fix the change -- do not regenerate the reference.\n")


class TestTheReferenceIsIntact(unittest.TestCase):
    """The reference on disk and the registry in the capture script agree."""

    def test_every_case_in_the_registry_has_a_reference_file(self):
        missing = [c.name for c in cap.CASES
                   if not cap.reference_path(c.name).exists()]
        self.assertEqual([], missing,
                         "cases with no reference file -- run "
                         "`python tests/_cli_capture.py`")

    def test_every_reference_file_is_in_the_registry(self):
        # A stale file is a case someone deleted, and a deleted case is
        # coverage that went away in silence.
        known = {c.name for c in cap.CASES}
        stale = sorted(p.stem for p in cap.REFERENCE_DIR.iterdir()
                       if p.suffix == ".json" and p.name != cap.INDEX_NAME
                       and p.stem not in known)
        self.assertEqual([], stale)

    def test_the_index_lists_the_registry_in_order(self):
        index = json.loads(
            (cap.REFERENCE_DIR / cap.INDEX_NAME).read_text(encoding="utf-8"))
        self.assertEqual([c.name for c in cap.CASES], index["cases"])

    def test_case_names_are_unique(self):
        names = [c.name for c in cap.CASES]
        self.assertEqual(len(names), len(set(names)))

    def test_the_reference_is_not_trivially_small(self):
        # The precondition on every assertion below: a reference of empty
        # records would compare equal to a CLI that printed nothing at all.
        ref = cap.load_reference()
        lines = sum(len(r["stdout"]) + len(r["stderr"]) for r in ref.values())
        self.assertGreater(len(ref), 100)
        self.assertGreater(lines, 5000)


class TestTheOutputHasNotMoved(unittest.TestCase):
    """Every case, replayed and compared byte for byte."""

    @classmethod
    def setUpClass(cls):
        cls.reference = cap.load_reference()
        cls.got = captured()

    def test_every_case_matches_the_reference(self):
        for case in cap.CASES:
            with self.subTest(case=case.name):
                want = self.reference[case.name]
                got = self.got[case.name]
                if cap.dumps(want) != cap.dumps(got):
                    self.fail(_diff(case.name, list(case.argv), want, got))

    def test_the_exit_codes_match(self):
        # Stated separately from the byte compare above so a changed exit code
        # is reported as a changed exit code, not as one line of a 300-line
        # diff.  A CLI that prints the right refusal and returns 0 is a CLI
        # that no script can react to.
        for case in cap.CASES:
            with self.subTest(case=case.name):
                self.assertEqual(self.reference[case.name]["returncode"],
                                 self.got[case.name]["returncode"])

    def test_nothing_escaped_as_an_uncaught_exception(self):
        # returncode -1 is _cli_capture's marker for a traceback out of
        # main().  It is RECORDED rather than raised (so one crash cannot stop
        # the capture), which means something has to look at it.
        crashed = sorted(n for n, r in self.got.items()
                         if r["returncode"] == -1)
        self.assertEqual([], crashed)


class TestTheCaptureIsDeterministic(unittest.TestCase):
    """
    The property a golden reference cannot recover from.

    A flaky golden is worse than a missing one: it trains its reader to
    re-capture on failure, which is exactly the gesture this reference exists
    to make expensive.  Nothing here pins PYTHONHASHSEED -- if any of the CLI's
    output were ever driven by the iteration order of a set of strings, this is
    what has to notice.
    """

    def test_two_captures_in_one_process_are_byte_identical(self):
        first = captured()
        second = cap.capture_all()
        differing = [n for n in first
                     if cap.dumps(first[n]) != cap.dumps(second[n])]
        self.assertEqual([], differing)

    def test_the_scratch_directory_is_normalised_out(self):
        # Every case runs in a temp directory whose name changes on every run,
        # so a reference holding one is a reference that can only ever match
        # the machine that wrote it.
        import tempfile
        tmp_root = str(Path(tempfile.gettempdir()).resolve())
        for name, rec in captured().items():
            with self.subTest(case=name):
                blob = json.dumps(rec)
                self.assertNotIn(tmp_root.replace("\\", "\\\\"), blob)
                self.assertNotIn(tmp_root.replace("\\", "/"), blob)

    def test_the_repo_root_is_normalised_out(self):
        root = str(Path(__file__).resolve().parent.parent)
        for name, rec in captured().items():
            with self.subTest(case=name):
                blob = json.dumps(rec)
                self.assertNotIn(root.replace("\\", "\\\\"), blob)
                self.assertNotIn(root.replace("\\", "/"), blob)

    def test_no_captured_line_carries_a_carriage_return(self):
        for name, rec in captured().items():
            with self.subTest(case=name):
                for line in rec["stdout"] + rec["stderr"]:
                    self.assertNotIn("\r", line)


class TestTheMatrixCoversTheFlags(unittest.TestCase):
    """
    A flag with no case is a formatter with no reference.

    This is the guard that keeps the registry honest as the CLI grows: a new
    `--attribute-something` added without a case here would be invisible to the
    refactor this whole file exists to make safe, and nothing else in the repo
    would notice.
    """

    @classmethod
    def setUpClass(cls):
        cls.flags = set()
        for case in cap.CASES:
            for arg in case.argv:
                if arg.startswith("--"):
                    cls.flags.add(arg.split("=", 1)[0])

    def _parser_flags(self) -> set[str]:
        out = set()
        for action in ex._make_arg_parser()._actions:
            out.update(s for s in action.option_strings if s.startswith("--"))
        return out

    def test_every_flag_the_parser_defines_appears_in_a_case(self):
        missing = sorted(self._parser_flags() - self.flags)
        self.assertEqual(
            [], missing,
            "these flags have no golden case -- add one to "
            "tests/_cli_capture.CASES and regenerate")

    def test_the_precondition_that_the_parser_really_has_flags(self):
        # Without this the test above passes vacuously if _make_arg_parser
        # ever stops being reachable the way it is reached here.
        self.assertGreater(len(self._parser_flags()), 25)

    def test_every_exit_code_the_cli_can_return_is_represented(self):
        codes = {r["returncode"] for r in cap.load_reference().values()}
        self.assertEqual({0, 1, 2}, codes)

    def test_all_three_modes_are_covered(self):
        modes = set()
        for case in cap.CASES:
            argv = list(case.argv)
            if "--mode" in argv:
                modes.add(argv[argv.index("--mode") + 1])
        # A subset, not an equality: one case deliberately passes a mode the
        # parser does not accept, to pin the refusal.
        self.assertLessEqual({"gnd", "p2p", "coupling"}, modes)

    def test_all_four_fit_models_are_covered(self):
        fits = set()
        for case in cap.CASES:
            argv = list(case.argv)
            if "--fit" in argv:
                fits.add(argv[argv.index("--fit") + 1])
        self.assertEqual({"auto", "inductor", "capacitor"}, fits - {"none"})

    def test_both_ground_model_spellings_are_covered(self):
        models = set()
        for case in cap.CASES:
            argv = list(case.argv)
            if "--attribute-ground-model" in argv:
                models.add(argv[argv.index("--attribute-ground-model") + 1]
                           .split(":", 1)[0])
        self.assertIn("diag", models)
        self.assertIn("shared", models)

    def test_every_csv_writing_flag_has_a_case_that_captures_the_file(self):
        # A --csv case that does not read the file back pins only the "Wrote
        # CSV:" line, and the CSV writers are four of the formatters the
        # refactor touches.
        want = {"--csv", "--attribute-csv", "--cold-start-csv",
                "--compose-propose-csv", "--compose-export"}
        with_artifacts = set()
        for case in cap.CASES:
            if not case.artifacts:
                continue
            with_artifacts.update(a for a in case.argv if a in want)
        self.assertEqual(want, with_artifacts)


class TestTheRefusalsAreNamed(unittest.TestCase):
    """
    Every non-zero case says something, and says it on stderr.

    `tests/test_compose_cli.py` makes this point about the composition flags
    and it holds for the whole surface: exit 2 with an empty stderr is
    argparse's answer to an unrelated typo as well, so a refusal is only
    pinned when the TOKEN is.
    """

    @classmethod
    def setUpClass(cls):
        cls.reference = cap.load_reference()

    def test_a_non_zero_exit_always_wrote_to_stderr(self):
        # --diagnose is the ONE exception and it is deliberate: its report is
        # the product, not an error, so it goes to stdout and the exit code is
        # the VERDICT (0 = FAULT_NONE, 1 = the file has something wrong with
        # it).  Exempting it here rather than loosening the rule for everyone
        # is what keeps the rule able to catch a silent refusal.
        for name, rec in self.reference.items():
            if rec["returncode"] == 0 or "--diagnose" in rec["argv"]:
                continue
            with self.subTest(case=name):
                self.assertTrue(
                    any(line.strip() for line in rec["stderr"]),
                    f"{name} exited {rec['returncode']} in silence")

    def test_a_failing_diagnose_says_so_on_STDOUT_and_exits_1(self):
        for name in ("diagnose_truncated", "diagnose_junk_token",
                     "diagnose_version2", "diagnose_missing"):
            with self.subTest(case=name):
                rec = self.reference[name]
                self.assertEqual(1, rec["returncode"])
                self.assertEqual([], [ln for ln in rec["stderr"] if ln.strip()])
                self.assertTrue(any(ln.strip() for ln in rec["stdout"]))

    def test_a_clean_diagnose_exits_0(self):
        for name in ("diagnose_ok", "diagnose_ok_4port",
                     "diagnose_composition"):
            with self.subTest(case=name):
                self.assertEqual(0, self.reference[name]["returncode"])

    def test_a_zero_exit_wrote_nothing_to_stderr(self):
        # The converse, and the one that would have caught a diagnostic
        # leaking onto stderr on the success path.
        for name, rec in self.reference.items():
            if rec["returncode"] != 0:
                continue
            with self.subTest(case=name):
                self.assertEqual([""], rec["stderr"] or [""])

    def test_an_argparse_refusal_names_the_program_and_the_token(self):
        for name in ("bad_flag", "bad_mode", "attr_group_bad"):
            with self.subTest(case=name):
                err = "\n".join(self.reference[name]["stderr"])
                self.assertIn("usage: pkg_rlc_extractor", err)
                self.assertEqual(2, self.reference[name]["returncode"])

    def test_a_file_that_cannot_be_read_is_exit_1_not_exit_2(self):
        # The split this CLI keeps everywhere: 2 is "your command line is
        # wrong", 1 is "your file is".
        for name in ("missing_file", "junk_token_refused", "truncated_refused",
                     "version2_refused", "compose_missing_file"):
            with self.subTest(case=name):
                self.assertEqual(1, self.reference[name]["returncode"])

    def test_a_dependent_flag_without_its_parent_names_the_parent(self):
        pairs = [("attr_dependents_without_parent", "--attribute"),
                 ("cold_dependents_without_parent", "--cold-start"),
                 ("compose_dependents_without_parent", "--compose")]
        for name, parent in pairs:
            with self.subTest(case=name):
                err = "\n".join(self.reference[name]["stderr"])
                self.assertIn(parent, err)
                self.assertEqual(2, self.reference[name]["returncode"])


class TestTheNormaliser(unittest.TestCase):
    """
    The capture's own rules, tested without running the CLI.

    Every one of these is a way the reference could quietly become a property
    of the machine that wrote it rather than of the code it describes.
    """

    def test_crlf_and_cr_both_become_lf(self):
        self.assertEqual(["a", "b", "c"],
                         cap.normalise("a\r\nb\rc", []))

    def test_a_path_is_substituted_in_both_spellings(self):
        subs = cap.substitutions(("--cli", "tests/fixtures/pi_2port.s2p"),
                                 Path("/tmp/x"))
        text = "Loaded " + str(Path("tests/fixtures/pi_2port.s2p"))
        self.assertEqual(["Loaded tests/fixtures/pi_2port.s2p"],
                         cap.normalise(text, subs))

    def test_a_repr_escaped_path_is_substituted_too(self):
        # str(FileNotFoundError) embeds the file name through repr, so on
        # Windows every separator arrives doubled.
        subs = cap.substitutions(("--cli", "tests/fixtures/pi_2port.s2p"),
                                 Path("/tmp/x"))
        doubled = str(Path("tests/fixtures/pi_2port.s2p")).replace("\\", "\\\\")
        self.assertEqual(["tests/fixtures/pi_2port.s2p"],
                         cap.normalise(doubled, subs))

    def test_the_os_error_sentence_is_replaced_but_the_path_survives(self):
        text = ("cannot stat the file: [WinError 2] "
                "the operating system says something localised: '<OUT>/a.s2p'")
        self.assertEqual(
            ["cannot stat the file: [OS-ERROR] '<OUT>/a.s2p'"],
            cap.normalise(text, []))
        self.assertEqual(
            ["[OS-ERROR] '<OUT>/a.csv'"],
            cap.normalise("[Errno 2] No such file or directory: "
                          "'<OUT>/a.csv'", []))

    def test_the_placeholder_keeps_a_forward_slash_after_it(self):
        subs = cap.substitutions((), Path("/tmp/scratch"))
        self.assertEqual(["<OUT>/a/b.csv"],
                         cap.normalise("/tmp/scratch/a/b.csv", subs))

    def test_a_long_artifact_is_elided_with_its_line_count(self):
        lines = [f"row {i}" for i in range(500)]
        capped = cap.cap_artifact(lines)
        self.assertEqual(cap.ARTIFACT_HEAD_LINES + 1 + cap.ARTIFACT_TAIL_LINES,
                         len(capped))
        self.assertEqual("row 0", capped[0])
        self.assertEqual("row 499", capped[-1])
        self.assertIn(f"{500 - cap.ARTIFACT_HEAD_LINES - cap.ARTIFACT_TAIL_LINES}"
                      " lines elided", capped[cap.ARTIFACT_HEAD_LINES])

    def test_a_short_artifact_is_untouched(self):
        lines = [f"row {i}" for i in range(cap.ARTIFACT_MAX_LINES)]
        self.assertEqual(lines, cap.cap_artifact(lines))

    def test_the_elision_marker_carries_the_count_so_a_row_change_fails(self):
        a = cap.cap_artifact([f"row {i}" for i in range(500)])
        b = cap.cap_artifact([f"row {i}" for i in range(501)])
        self.assertNotEqual(a, b)


class TestTheCaptureRunsTheRealEntryPoint(unittest.TestCase):
    """
    The precondition the whole file rests on.

    If the capture were ever refactored into calling something other than
    `pkg_rlc_extractor.main`, every assertion above would go on passing while
    pinning nothing at all.
    """

    def test_a_case_reaches_main_and_its_stdout_is_what_is_recorded(self):
        rec = captured()["gnd_pi_grounded"]
        self.assertEqual(0, rec["returncode"])
        self.assertTrue(any(line.startswith("Loaded ") for line in
                            rec["stdout"]))
        self.assertTrue(any(re.match(r"^  L      = ", line) for line in
                            rec["stdout"]))

    def test_the_capture_never_reaches_the_gui(self):
        """
        MUTATION: a case with no --cli and no --diagnose. `main` would then
        fall through to `from pkg_rlc_gui import App` and open a window in the
        middle of the test suite -- and this module would stop being Tk-free,
        which is the property that lets it join FAST_MODULES.
        """
        captured()
        self.assertNotIn("pkg_rlc.frontend.app", sys.modules)
        self.assertNotIn("tkinter", sys.modules)

    def test_the_capture_would_notice_a_changed_line(self):
        # The mutation this file is defending against, applied by hand: one
        # character of one line of one case.  Without the byte compare the
        # suite would not see it.
        ref = cap.load_reference()["gnd_pi_grounded"]
        mutated = json.loads(json.dumps(ref))
        mutated["stdout"][6] = mutated["stdout"][6].replace("Z", "Zed")
        self.assertNotEqual(cap.dumps(ref), cap.dumps(mutated))


if __name__ == "__main__":
    unittest.main()
