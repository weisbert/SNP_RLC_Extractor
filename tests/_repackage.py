"""
_repackage.py  --  The one-shot move of the 25 flat `pkg_rlc_*.py` modules into
a `pkg_rlc/` package whose FOLDERS ARE THE LAYERS.

This is a SCRIPT, not a unittest module: the leading underscore keeps it out of
`unittest discover` and out of `run_parallel.discover_shards`, which globs
`test_*.py` (same convention as `_golden_capture.py`, `_cli_capture.py`,
`_render_capture.py` and `_smoke.py`).

WHY IT EXISTS AT ALL.  The move touches ~370 references: 100 import statements
in the modules themselves, 140 in `tests/`, and every path mention in the docs.
Done by hand that is 370 chances to typo one, and a typo in an import is a
module that silently resolves to something else.  Done by a committed script it
is reproducible, reviewable as a diff, and countable -- it reports how many
substitutions it made in each file.

WHAT IT DOES NOT DO.  It does not change one expression of behaviour.  It moves
files with `git mv` (so `git log --follow` survives), rewrites the module name
inside `import` / `from ... import` statements, and rewrites the module names
mentioned in the docs.  Nothing else.

THE ONE PLACE THE RULE BENDS.  `import pkg_rlc_gui` (no `as`) becomes
`import pkg_rlc.frontend.app as pkg_rlc_gui`, and likewise for the three other
bare-`import` modules.  The alias keeps ~1000 attribute references
(`pkg_rlc_gui.TraceConfig`, `pkg_rlc_core.MAX_SNIFF_NPORTS`, ...) resolving
without being touched, which is the difference between rewriting 100 lines and
rewriting 1100.  `mock.patch.object(pkg_rlc_core, ...)` keeps working for the
same reason: an alias is the same module object.

Usage:

    python tests/_repackage.py plan       # print the map, touch nothing
    python tests/_repackage.py move       # mkdir + __init__ + git mv
    python tests/_repackage.py imports    # rewrite imports in every .py
    python tests/_repackage.py docs       # rewrite module names in the docs
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "pkg_rlc"

# --------------------------------------------------------------------------
# The map.  One line per module: old flat name -> package-relative path.
#
# The folder is the LAYER, exactly as tests/test_layering.py declares it.  Two
# entries are worth reading twice:
#
#   * `pkg_rlc_validate` goes in `model/`, NOT in `services/`.  It is L1 in
#     LAYERS and it has to be: `pkg_rlc_model` imports it (`port_descriptor`,
#     `info_str`, all three legacy migrations, `_config_signature`), so a
#     validate one layer ABOVE the model would be an upward import.  Putting it
#     in `services/` would make the folders lie about the layers, which is the
#     one thing this move exists to stop.
#
#   * `pkg_rlc_gui` -> `frontend/app.py` and `pkg_rlc_extractor` ->
#     `frontend/cli.py` are the only two renames beyond dropping the prefix.
#     A thin `pkg_rlc_extractor.py` stays at the repo root as the entry point.
# --------------------------------------------------------------------------

MOVES: list[tuple[str, str]] = [
    # L0 -- physics
    ("pkg_rlc_touchstone",     "physics/touchstone.py"),
    ("pkg_rlc_spec",           "physics/spec.py"),
    ("pkg_rlc_solve",          "physics/solve.py"),
    ("pkg_rlc_core",           "physics/core.py"),
    ("pkg_rlc_compose",        "physics/compose.py"),
    ("pkg_rlc_attrib",         "physics/attrib.py"),
    # L1 -- model
    ("pkg_rlc_model",          "model/trace.py"),
    ("pkg_rlc_validate",       "model/validate.py"),
    # L2 -- services
    ("pkg_rlc_session",        "services/session.py"),
    ("pkg_rlc_run",            "services/run.py"),
    # L3 -- presentation
    ("pkg_rlc_report",         "present/report.py"),
    ("pkg_rlc_csv",            "present/csv.py"),
    ("pkg_rlc_attrib_report",  "present/attrib_report.py"),
    ("pkg_rlc_conntable",      "present/conntable.py"),
    ("pkg_rlc_help",           "present/help.py"),
    # L4 -- widgets
    ("pkg_rlc_widgets",        "widgets/widgets.py"),
    ("pkg_rlc_plot",           "widgets/plot.py"),
    # L5 -- panels
    ("pkg_rlc_panels_files",   "panels/panels_files.py"),
    ("pkg_rlc_panels_traces",  "panels/panels_traces.py"),
    ("pkg_rlc_panels_results", "panels/panels_results.py"),
    ("pkg_rlc_panels_editor",  "panels/panels_editor.py"),
    ("pkg_rlc_files_gui",      "panels/files_gui.py"),
    ("pkg_rlc_attrib_gui",     "panels/attrib_gui.py"),
    # L6 -- frontend
    ("pkg_rlc_gui",            "frontend/app.py"),
    ("pkg_rlc_extractor",      "frontend/cli.py"),
]

LAYER_DOCS = {
    "physics":  "L0 -- arrays and physics.  No Tk, no App, no widgets.",
    "model":    "L1 -- the shared data model, and the spec logic over it.",
    "services": "L2 -- services over the model: the session file, a run.",
    "present":  "L3 -- turning a result into text: reports, CSV, help prose.",
    "widgets":  "L4 -- generic Tk widgets that know nothing about this app.",
    "panels":   "L5 -- app-specific windows and panels.",
    "frontend": "L6 -- the App itself and the argv entry point.",
}


def dotted(rel: str) -> str:
    return "pkg_rlc." + rel[:-3].replace("/", ".")


# old flat name -> new dotted name, LONGEST FIRST so that `pkg_rlc_attrib_gui`
# is matched before `pkg_rlc_attrib`.  (The word-boundary regex below makes
# that safe anyway; the ordering is belt and braces.)
NAME_MAP: dict[str, str] = {old: dotted(rel) for old, rel in MOVES}
OLD_NAMES = sorted(NAME_MAP, key=len, reverse=True)

# The four modules imported as `import X` with no `as`.  They keep the old
# spelling as an alias so their attribute references do not have to move.
_NAME_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in OLD_NAMES) + r")(?![\w])")


def _run(*args: str) -> None:
    subprocess.run(args, cwd=str(REPO), check=True)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

def cmd_plan() -> None:
    width = max(len(o) for o in NAME_MAP)
    for old, rel in MOVES:
        print(f"  {old:<{width}}  ->  pkg_rlc/{rel:<26}  ({dotted(rel)})")


# --------------------------------------------------------------------------
# move
# --------------------------------------------------------------------------

def cmd_move() -> None:
    PKG.mkdir(exist_ok=True)
    init = PKG / "__init__.py"
    if not init.exists():
        init.write_text(
            '"""The PKG RLC Extractor package.\n'
            "\n"
            "One SUBPACKAGE PER LAYER, in the order tests/test_layering.py\n"
            "declares.  A module may import from its own layer or a lower one;\n"
            "upward is the failure, and now it is also visible in the tree:\n"
            "\n"
            "    physics/   L0  arrays and physics\n"
            "    model/     L1  the shared data model and the spec logic on it\n"
            "    services/  L2  the session file, a run\n"
            "    present/   L3  turning a result into text\n"
            "    widgets/   L4  generic Tk widgets\n"
            "    panels/    L5  app-specific windows and panels\n"
            "    frontend/  L6  the App and the argv entry point\n"
            "\n"
            "Every __init__.py in here is EMPTY of imports on purpose.  A\n"
            "package that imported its own modules would make\n"
            "`import pkg_rlc.physics.core` drag in tkinter, and would put an\n"
            "edge in the import graph that the layering gate cannot see.\n"
            '"""\n',
            encoding="utf-8", newline="\n",
        )
        _run("git", "add", "pkg_rlc/__init__.py")

    for layer, doc in LAYER_DOCS.items():
        d = PKG / layer
        d.mkdir(exist_ok=True)
        f = d / "__init__.py"
        if not f.exists():
            f.write_text(f'"""{doc}"""\n', encoding="utf-8", newline="\n")
            _run("git", "add", f"pkg_rlc/{layer}/__init__.py")

    for old, rel in MOVES:
        src = REPO / (old + ".py")
        dst = PKG / rel
        if not src.exists():
            print(f"  SKIP (already moved): {old}")
            continue
        _run("git", "mv", old + ".py", f"pkg_rlc/{rel}")
        print(f"  moved {old}.py -> pkg_rlc/{rel}")


# --------------------------------------------------------------------------
# imports
# --------------------------------------------------------------------------

_IMPORT_LINE = re.compile(r"^\s*(from|import)\s")
_BARE_IMPORT = re.compile(r"^(\s*)import\s+(pkg_rlc(?:\.\w+)+)\s*$")


def _rewrite_py(text: str, old_name_for_alias: dict[str, str]) -> tuple[str, int]:
    """Rewrite repo module names, but ONLY inside import statements."""
    out: list[str] = []
    n = 0
    for line in text.split("\n"):
        if not _IMPORT_LINE.match(line):
            out.append(line)
            continue
        # Split off a trailing comment so `# noqa: F401` is never rewritten.
        code, sep, comment = line.partition("#")
        new_code, hits = _NAME_RE.subn(lambda m: NAME_MAP[m.group(1)], code)
        if hits:
            # `import pkg_rlc.frontend.app` with no `as` would bind the name
            # `pkg_rlc`, not `pkg_rlc_gui`.  Restore the old spelling as an
            # alias so every `pkg_rlc_gui.X` in the file keeps resolving.
            stripped = new_code.rstrip()
            m = _BARE_IMPORT.match(stripped)
            if m:
                alias = old_name_for_alias.get(m.group(2))
                if alias:
                    # Keep the run of spaces that lined the trailing `# noqa`
                    # up.  This is a move; a reflowed comment column would be a
                    # diff line that says nothing.
                    tail = new_code[len(stripped):]
                    new_code = f"{m.group(1)}import {m.group(2)} as {alias}{tail}"
            n += hits
        out.append(new_code + sep + comment)
    return "\n".join(out), n


def cmd_imports() -> None:
    reverse = {v: k for k, v in NAME_MAP.items()}
    targets: list[Path] = []
    targets += sorted(PKG.rglob("*.py"))
    targets += sorted((REPO / "tests").glob("*.py"))
    targets += sorted((REPO / "deploy").glob("*.py"))
    total = 0
    for path in targets:
        if path.name == "_repackage.py":
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        crlf = b"\r\n" in raw
        if crlf:
            text = text.replace("\r\n", "\n")
        new, n = _rewrite_py(text, reverse)
        if n:
            if crlf:
                new = new.replace("\n", "\r\n")
            path.write_bytes(new.encode("utf-8"))
            print(f"  {n:>4}  {path.relative_to(REPO).as_posix()}")
            total += n
    print(f"  ---- {total} import-statement substitutions")


# --------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------
#
# `pkg_rlc_extractor` is deliberately NOT rewritten in the docs.  Every
# `python pkg_rlc_extractor.py ...` in README.md, docs/help/ and doctor.sh is
# the ENTRY POINT, which still lives at the repo root and still works; a
# blanket rewrite would turn a working command line into a broken one.  The
# module map's own row for it is corrected by hand instead.

DOC_SKIP = {"pkg_rlc_extractor"}


def cmd_docs() -> None:
    names = [n for n in OLD_NAMES if n not in DOC_SKIP]
    file_re = re.compile(
        r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")\.py(?![\w])")
    bare_re = re.compile(
        r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")(?![\w])")

    targets = sorted(REPO.glob("*.md")) + sorted((REPO / "docs").rglob("*.md"))
    total = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        new, a = file_re.subn(
            lambda m: "pkg_rlc/" + dict(MOVES)[m.group(1)], text)
        new, b = bare_re.subn(lambda m: NAME_MAP[m.group(1)], new)
        if a + b:
            path.write_text(new, encoding="utf-8", newline="\n")
            print(f"  {a:>4} paths + {b:>4} names  {path.relative_to(REPO).as_posix()}")
            total += a + b
    print(f"  ---- {total} doc substitutions")


COMMANDS = {
    "plan": cmd_plan,
    "move": cmd_move,
    "imports": cmd_imports,
    "docs": cmd_docs,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
