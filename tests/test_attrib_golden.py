"""
Golden regression: the Attribution window's PURE formatters, pinned byte for
byte.

tests/_attrib_capture.py recorded, into tests/fixtures/attrib_reference/, what
`pkg_rlc_attrib_gui`'s text formatters produce for a registry of cases spanning
every output SHAPE the window has: each quantity that decomposes and each one
refused by name, a healthy reconciliation and a withheld split, the diagonal
and the shared-return ground models, a composed-network baseline with and
without the gauge, a sweep with a pole and one without, and both units modes.
This module replays every recorded case through the current API and demands an
EXACT string match.

WHY IT EXISTS.  A later phase unifies the attribution report: the ~1400 lines
of `_attr_print_*` / `_cold_print_*` in `pkg_rlc_extractor.py` become
text-returning functions in a shared module and these formatters consume the
same module.  "The window's text did not move" is that phase's whole acceptance
criterion, and without a byte-exact BEFORE it is unverifiable.

WHY EXACT AND NOT "CLOSE ENOUGH".  Every failure this reference exists to catch
is a formatting change nobody intended -- a column that grew, a sentence that
lost its second half, a sign that stopped being forced, a share that started
being suppressed.  None of those is visible to a tolerance, and all of them are
visible to `==`.

IF THIS FAILS, do NOT re-capture to make it pass.  A failure means the rendered
attribution text changed.  Either the change was unintended (fix it) or it was
deliberate and reviewed -- and then, and only then, run

    python tests/_attrib_capture.py

in the SAME COMMIT that justifies moving the reference, with the diff this test
prints quoted in the commit message.  That is the same rule
`tests/fixtures/golden_legacy.npz` and `tests/fixtures/render_reference.json`
carry, and it is the reason those two have moved once between them.

TKINTER.  MEASURED on this box: `import pkg_rlc_attrib_gui` succeeds with no
display and creates no Tk root -- `tkinter._default_root` is None afterwards,
which `test_rendering_creates_no_Tk_root` asserts -- but the module DOES pull
in `tkinter`, `_tkinter`, `matplotlib` and the TkAgg backend at import time
(it subclasses `tk.Toplevel` and holds a `FigureCanvasTkAgg`), and
`contributions_table` reaches `pkg_rlc_gui._value_formatter` through `_gui()`,
which pulls in the rest of the GUI.  So this module is DISPLAY-free but not
tkinter-free, and it therefore does NOT satisfy `run_parallel.FAST_MODULES`'
one property ("it imports no tkinter").  Every import here is deferred into
`setUpClass` so that collecting this file costs nothing; joining `--fast`
would need that criterion relaxed from "imports no tkinter" to "creates no Tk
root", which is a decision for whoever owns that list and not for this file.
"""

from __future__ import annotations

import difflib
import json
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _attrib_capture as ac  # noqa: E402


_REGEN_HINT = (
    "Run `python tests/_attrib_capture.py` from the repo root to (re)create "
    "it -- but ONLY in the same commit that justifies moving the reference.")

#: The shapes the registry must keep covering.  Each entry is
#: (what it is, a predicate over the case names).  This is the analogue of
#: `test_golden_regression.test_all_five_modes_are_covered`: a reference is
#: only a safety net while it spans the thing it guards, and a case quietly
#: deleted from the registry would otherwise take its coverage with it and
#: leave every remaining assertion green.
_COVERAGE = [
    ("each decomposable quantity",
     lambda names: all(f"contrib_coupled_{q}" in names
                       for q in ("M", "ImZ", "ReZ", "Z", "M_over_L_a", "k"))),
    ("the quantities refused BY NAME", lambda n: "quantity_refusals" in n),
    ("both units modes",
     lambda n: any(x.endswith("_aligned") for x in n)),
    ("a healthy reconciliation and a WITHHELD split",
     lambda n: "reconciliation_all_states" in n
     and "contrib_fake_withheld" in n),
    ("the DIAGONAL and SHARED-RETURN ground models",
     lambda n: "contrib_ground_model_diag" in n
     and "contrib_ground_model_shared" in n),
    ("a composed-network baseline, with and without the gauge",
     lambda n: "contrib_composed_gauge" in n
     and "contrib_composed_no_gauge" in n),
    ("a sweep WITH a pole and one WITHOUT",
     lambda n: "sweep_with_pole" in n and "sweep_no_pole_flat" in n),
    ("the exported report and the CSV",
     lambda n: "report_full" in n and "csv_records_full" in n),
]


class _Base(unittest.TestCase):
    """Renders the whole registry ONCE.

    Every import is here rather than at module scope: the registry reaches
    `pkg_rlc_attrib_gui` and, through `_gui()`, `pkg_rlc_gui`, and neither is
    wanted merely to collect this file.  `build_context` is `O(N^3)` and the
    capture module caches one context per spec, so rendering all of it costs
    one pass whatever the test count.
    """

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        if not ac.REFERENCE_DIR.is_dir():
            raise unittest.SkipTest(
                f"Reference directory {ac.REFERENCE_DIR} is missing. "
                + _REGEN_HINT)
        try:
            cls.manifest = ac.read_manifest()
        except (OSError, ValueError) as e:
            raise unittest.SkipTest(
                f"Cannot read {ac.REFERENCE_DIR / ac.MANIFEST_NAME}: {e}. "
                + _REGEN_HINT)
        cls.entries = list(cls.manifest.get("cases", ()))
        cls.rendered = ac.render_all()
        cls.now = {case.name: text for case, text in cls.rendered}

    # -- helpers ---------------------------------------------------------

    def _context(self) -> str:
        """The capture environment against this one, for a failure message."""
        was = dict(self.manifest.get("__env__", {}))
        here = ac.env_now()
        drift = [k for k, v in sorted(was.items()) if here.get(k) != v]
        head = ("Reference captured with ["
                + ", ".join(f"{k}={v}" for k, v in sorted(was.items())) + "]")
        if not drift:
            return (head + "; this machine matches, so this is a real change "
                    "in the rendered attribution text.")
        return (head + f"; this machine differs in {', '.join(drift)} "
                "([" + ", ".join(f"{k}={v}" for k, v in sorted(here.items()))
                + "]). A few last digits of a formatted number can move with "
                "the LAPACK/BLAS build; a changed WORD cannot. Read the diff "
                "before concluding anything.")

    def _diff(self, name: str, want: str, got: str) -> str:
        lines = list(difflib.unified_diff(
            want.splitlines(keepends=True), got.splitlines(keepends=True),
            fromfile=f"reference/{name}.txt", tofile=f"rendered/{name}",
            n=2))
        # The whole diff, not a head: a truncated golden diff sends the reader
        # back to the terminal to run the capture by hand, which is exactly the
        # gesture that ends in the reference being regenerated by accident.
        return "".join(lines).rstrip("\n")


class TestAttributionTextIsByteIdentical(_Base):

    def test_every_recorded_case_reproduces_exactly(self) -> None:
        """The reference, character for character."""
        self.assertTrue(self.entries,
                        f"No cases in {ac.MANIFEST_NAME}. {_REGEN_HINT}")
        for entry in self.entries:
            name = entry["name"]
            with self.subTest(case=name):
                if name not in self.now:
                    self.fail(
                        f"Case '{name}' is in {ac.MANIFEST_NAME} but no "
                        f"longer produced by _attrib_capture.build_cases() -- "
                        f"a golden case was renamed or deleted. {_REGEN_HINT}")
                try:
                    want = ac.read_case(name)
                except OSError as e:
                    self.fail(f"Case '{name}': {e}. {_REGEN_HINT}")
                got = self.now[name]
                if want != got:
                    self.fail(
                        f"ATTRIBUTION TEXT MOVED in case '{name}' "
                        f"({entry.get('describe', '')}).\n{self._context()}\n"
                        f"\n{self._diff(name, want, got)}\n")

    def test_every_declared_case_is_in_the_reference(self) -> None:
        """A registry entry that was never captured is silent coverage loss."""
        recorded = {e["name"] for e in self.entries}
        missing = [c.name for c, _t in self.rendered if c.name not in recorded]
        self.assertFalse(
            missing,
            f"Cases declared in _attrib_capture.build_cases() but absent from "
            f"{ac.MANIFEST_NAME}: {missing}. {_REGEN_HINT}")

    def test_the_reference_directory_has_no_stray_files(self) -> None:
        """A leftover .txt from a renamed case is a reference to nothing.

        It reads as coverage in a directory listing and is compared against
        nothing at all, which is the shape of failure a golden reference is
        least able to notice about itself.
        """
        expected = {ac.MANIFEST_NAME}
        expected |= {f"{e['name']}.txt" for e in self.entries}
        actual = {p.name for p in ac.REFERENCE_DIR.iterdir() if p.is_file()}
        self.assertFalse(
            sorted(actual - expected),
            f"Files in {ac.REFERENCE_DIR} that no case claims: "
            f"{sorted(actual - expected)}. Delete them in the same commit "
            f"that renamed or removed the case.")

    def test_the_manifest_agrees_with_the_files(self) -> None:
        """The sha256 catches a reference file edited by hand.

        Without it, "fix the test by editing the .txt" is one keystroke away
        and leaves no trace -- the replay would then pass against a reference
        the capture script never produced.
        """
        for entry in self.entries:
            name = entry["name"]
            with self.subTest(case=name):
                text = ac.read_case(name)
                self.assertEqual(
                    ac.sha256(text), entry["sha256"],
                    f"{name}.txt does not match the sha256 in "
                    f"{ac.MANIFEST_NAME} -- it was edited by hand, or the "
                    f"manifest was written without it. {_REGEN_HINT}")
                self.assertEqual(len(text), entry["chars"], name)


class TestTheReferenceStillSpansWhatItGuards(_Base):
    """A golden reference is only a safety net while it covers the shapes."""

    def test_the_registry_covers_every_required_shape(self) -> None:
        names = {c.name for c, _t in self.rendered}
        for what, ok in _COVERAGE:
            with self.subTest(shape=what):
                self.assertTrue(
                    ok(names),
                    f"No case in _attrib_capture covers {what} any more.")

    def test_no_two_cases_render_the_same_bytes(self) -> None:
        """Two identical captures are one case's worth of coverage in two files.

        Not a style rule: it is how a case silently stops testing anything --
        the second one was written to exercise a different branch and, if it
        renders identically, it is not reaching that branch.  A case that
        genuinely wants the same text should be deleted, not duplicated.
        """
        seen: dict[str, str] = {}
        clashes = []
        for case, text in self.rendered:
            first = seen.setdefault(text, case.name)
            if first != case.name:
                clashes.append(f"{first} == {case.name}")
        self.assertFalse(clashes,
                         "cases rendering byte-identical text: " + str(clashes))

    def test_every_case_renders_something(self) -> None:
        for case, text in self.rendered:
            with self.subTest(case=case.name):
                self.assertTrue(text.strip(),
                                f"case '{case.name}' rendered nothing")

    def test_every_case_carries_a_describe(self) -> None:
        """The describe line is what tells a later reader WHY a case is here.

        It is in the manifest, so a diff of the reference says what moved in
        words as well as in bytes.
        """
        for case, _t in self.rendered:
            with self.subTest(case=case.name):
                self.assertTrue(case.describe.strip(),
                                f"case '{case.name}' has no describe")


class TestNoDisplayIsTouched(_Base):
    """The pure half is pure, and that is the property that makes this file
    runnable at all."""

    def test_rendering_creates_no_Tk_root(self) -> None:
        """MEASURED: `tkinter._default_root` is None after the whole registry.

        `setUpClass` has already rendered every case by the time this runs, so
        the assertion is about work that has happened.  A formatter that
        started needing a widget -- a font measurement, a `winfo_reqwidth` --
        would create one, and it would then be a formatter this reference
        cannot capture and the CLI cannot share.
        """
        import tkinter as tk
        self.assertIsNone(
            tk._default_root,
            "something in pkg_rlc_attrib_gui's pure formatters created a Tk "
            "root. The pure half must stay renderable with no display.")

    def test_the_manifest_records_the_capture_environment(self) -> None:
        env = self.manifest.get("__env__", {})
        for key in ("numpy", "python", "platform"):
            self.assertIn(key, env,
                          f"{ac.MANIFEST_NAME} has no '{key}' in __env__")


class TestTheReferenceFilesAreReadable(unittest.TestCase):
    """Properties of the files themselves, with nothing rendered.

    Cheap, and they run even when the formatters cannot: a reference that is
    unreadable, CRLF-translated or not valid JSON is a different failure from
    a reference that disagrees, and it must not be reported as the second one.
    """

    def test_the_manifest_is_valid_json_with_unique_case_names(self) -> None:
        if not (ac.REFERENCE_DIR / ac.MANIFEST_NAME).exists():
            self.skipTest(f"{ac.REFERENCE_DIR} is missing. {_REGEN_HINT}")
        data = json.loads(
            (ac.REFERENCE_DIR / ac.MANIFEST_NAME).read_text(encoding="utf-8"))
        names = [e["name"] for e in data["cases"]]
        self.assertEqual(len(names), len(set(names)),
                         "duplicate case names in the manifest")

    def test_no_reference_file_carries_a_carriage_return(self) -> None:
        """LF, on every platform.

        The text lives in the file as itself rather than inside a JSON string
        escape, so nothing may translate its line endings -- `_attrib_capture`
        opens both directions with `newline=""` for exactly this, and a CRLF
        that crept in would make every case fail on the next machine with a
        diff showing no visible difference at all.
        """
        if not ac.REFERENCE_DIR.is_dir():
            self.skipTest(f"{ac.REFERENCE_DIR} is missing. {_REGEN_HINT}")
        for path in sorted(ac.REFERENCE_DIR.glob("*.txt")):
            with self.subTest(file=path.name):
                self.assertNotIn(b"\r", path.read_bytes(),
                                 f"{path.name} contains a carriage return")


if __name__ == "__main__":
    unittest.main()
