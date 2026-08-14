"""The import layering gate.

This file is a FENCE, not a measurement.  It exists because the repo's import
graph is currently held together by ten `import pkg_rlc_gui` statements
hidden inside function bodies, whose only purpose is to dodge a cycle: a shared
data model (`TraceConfig`) and a handful of colour constants live in the same
module as the Tk `App`, so every panel that needs either has to reach UP into
the frontend, and it can only do that after import time.  A later phase splits
those out.  This file is what stops the dodge coming back afterwards, and what
stops an ELEVENTH one being added in the meantime.

Three properties are asserted:

  1. THE LAYER MAP HOLDS.  `LAYERS` below is the target architecture, declared
     as data.  A module may import from its own layer or a LOWER one; importing
     UPWARD is the failure.  Same-layer is allowed on purpose -- a layer is a
     bag of peers, and the order INSIDE it is pinned by property 2 rather than
     by inventing sub-layers (`pkg_rlc_attrib` -> `pkg_rlc_core` is the case:
     both are L0 numerics, and what makes it legal is that core imports nothing
     back).  Modules named in `LAYERS` that do not exist yet are simply skipped,
     so this test is valid TODAY and after the refactor lands.  A pkg_rlc_*.py
     that exists and is NOT in the map fails too, or a new module could slip in
     unlayered and unchecked.

  2. THE GRAPH IS ACYCLIC, counting module-level imports only, with the cycle
     printed if one exists.

  3. THE FUNCTION-LEVEL BACK-IMPORTS ARE EXACTLY THE KNOWN SET.  Both
     directions: adding a dodge fails, and REMOVING one fails too until
     somebody updates `KNOWN_BACK_IMPORTS` / `KNOWN_BACK_IMPORT_COUNTS` in the
     same commit.  The second half is the point -- it is what forces the phase
     that claims to have removed them to prove it here.

It is built with `ast` and never imports the modules it checks.  Importing
`pkg_rlc_gui` costs ~548 ms and pulls in tkinter; this file parses ten source
files, needs no display, and runs in milliseconds.

MUTATION-CHECKED: adding a module-level `import pkg_rlc_gui` to
`pkg_rlc_core.py` turns `test_no_module_imports_from_a_higher_layer`,
`test_the_module_import_graph_is_acyclic` and (for a function-level one)
`test_the_function_level_back_imports_are_exactly_the_known_set` red, and
`TestTheGateHasTeeth` pins that permanently against synthetic sources so the
proof does not depend on anybody re-running the experiment.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------
# The layer map.  Data, deliberately: a rule you can read in one screen.
# --------------------------------------------------------------------------

L0_NUMERICS = 0      # arrays and physics.  No Tk, no App, no widgets.
L1_MODEL = 1         # the shared data model, and the spec logic over it.
L2_SERVICES = 2      # the session file and the run record.
L3_PRESENTATION = 3  # turning a result into text: reports, CSV, help prose.
L4_WIDGETS = 4       # generic Tk widgets that know nothing about this app.
L5_PANELS = 5        # app-specific windows and panels.
L6_FRONTEND = 6      # the App itself and the argv entry point.

LAYERS = {
    # L0 -- numerics
    "pkg_rlc.physics.core": L0_NUMERICS,
    "pkg_rlc.physics.touchstone": L0_NUMERICS,
    "pkg_rlc.physics.spec": L0_NUMERICS,
    "pkg_rlc.physics.solve": L0_NUMERICS,
    "pkg_rlc.physics.compose": L0_NUMERICS,
    "pkg_rlc.physics.attrib": L0_NUMERICS,
    # L1 -- model
    "pkg_rlc.model.trace": L1_MODEL,
    # `pkg_rlc_validate` was declared L2 when this map was written, before
    # `pkg_rlc_model` existed.  IT MOVED DOWN, and the reason is a real edge
    # rather than a tidier name: `TraceConfig.port_descriptor()` is one line
    # (`return _port_descriptor(self)`), `info_str()` counts the file set
    # through `trace_file_labels`, all three legacy migrations call into it
    # (`_union_port_specs`, `_mport_more_lines`, `_import_dsl_text`) and
    # `_config_signature` ends on `trace_file_scope`.  So the model imports
    # validate, and validate therefore has to sit at or below the model.
    #
    # It sits there honestly rather than by fiat: `pkg_rlc_validate` imports
    # `pkg_rlc_core` and `pkg_rlc_compose` and NOTHING ELSE, and it is
    # duck-typed over the trace -- it never imports `TraceConfig`, it just
    # reads attributes off whatever it is handed.  That is what keeps the edge
    # ONE-DIRECTIONAL, and it makes this the same case the docstring above
    # names for `pkg_rlc_attrib` -> `pkg_rlc_core`: two peers in one layer,
    # legal because the one being imported imports nothing back.
    #
    # What L1 means now is "the shared data model, and the pure spec logic
    # duck-typed over it" -- what a spec SAYS, DOES and gets WRONG, which is
    # knowledge about the model and not a service built on top of it.  The
    # session file and the run record, which really are services over the
    # model, stay at L2.
    #
    # AND THAT IS WHY IT SITS IN `pkg_rlc/model/`, NOT `pkg_rlc/services/`.
    # Now that the folders ARE the layers, putting it beside the session file
    # and the run record would make the tree say L2 while this map says L1 --
    # and the map is the one the import graph obeys.
    "pkg_rlc.model.validate": L1_MODEL,
    # L2 -- services
    "pkg_rlc.services.session": L2_SERVICES,
    "pkg_rlc.services.run": L2_SERVICES,
    # L3 -- presentation
    "pkg_rlc.present.report": L3_PRESENTATION,
    "pkg_rlc.present.csv": L3_PRESENTATION,
    "pkg_rlc.present.attrib_report": L3_PRESENTATION,
    "pkg_rlc.present.conntable": L3_PRESENTATION,
    "pkg_rlc.present.help": L3_PRESENTATION,
    # L4 -- widgets
    "pkg_rlc.widgets.widgets": L4_WIDGETS,
    "pkg_rlc.widgets.plot": L4_WIDGETS,
    # L5 -- panels
    "pkg_rlc.panels.files_gui": L5_PANELS,
    "pkg_rlc.panels.attrib_gui": L5_PANELS,
    # L6 -- frontend
    "pkg_rlc.frontend.app": L6_FRONTEND,
    "pkg_rlc.frontend.cli": L6_FRONTEND,
    # The root shim.  `pkg_rlc_extractor.py` is the published entry point and
    # stays where every README, Help tab, `doctor.sh` line and deploy SENTINEL
    # expects it; all it does is import `pkg_rlc.frontend.cli`.
    "pkg_rlc_extractor": L6_FRONTEND,
}

# The flat spellings, for `TestTheGateHasTeeth` ONLY.  Those tests build
# synthetic one-file-per-module trees in a temp dir to prove this gate can go
# red, and writing them as packages would make them harder to read than the
# rule they check.  M2 rewrites this file to derive the layer FROM THE PATH,
# at which point this table and the duplicated LAYER_PREFIXES entry below go.
_LEGACY_LAYERS = {
    "pkg_rlc_core": L0_NUMERICS,
    "pkg_rlc_touchstone": L0_NUMERICS,
    "pkg_rlc_spec": L0_NUMERICS,
    "pkg_rlc_solve": L0_NUMERICS,
    "pkg_rlc_compose": L0_NUMERICS,
    "pkg_rlc_attrib": L0_NUMERICS,
    "pkg_rlc_model": L1_MODEL,
    "pkg_rlc_validate": L1_MODEL,
    "pkg_rlc_session": L2_SERVICES,
    "pkg_rlc_run": L2_SERVICES,
    "pkg_rlc_report": L3_PRESENTATION,
    "pkg_rlc_csv": L3_PRESENTATION,
    "pkg_rlc_attrib_report": L3_PRESENTATION,
    "pkg_rlc_conntable": L3_PRESENTATION,
    "pkg_rlc_help": L3_PRESENTATION,
    "pkg_rlc_widgets": L4_WIDGETS,
    "pkg_rlc_plot": L4_WIDGETS,
    "pkg_rlc_files_gui": L5_PANELS,
    "pkg_rlc_attrib_gui": L5_PANELS,
    "pkg_rlc_gui": L6_FRONTEND,
}

# Every `pkg_rlc.panels.panels_*` module is L5.  A prefix rather than an
# enumeration because that group is expected to grow one panel at a time.
# The flat spelling is the synthetic trees' again.
LAYER_PREFIXES = (
    ("pkg_rlc.panels.panels_", L5_PANELS),
    ("pkg_rlc_panels_", L5_PANELS),
)

LAYER_NAMES = {
    L0_NUMERICS: "L0 numerics",
    L1_MODEL: "L1 model",
    L2_SERVICES: "L2 services",
    L3_PRESENTATION: "L3 presentation",
    L4_WIDGETS: "L4 widgets",
    L5_PANELS: "L5 panels",
    L6_FRONTEND: "L6 frontend",
}

# `reduce_snp.py` is not in the layer map at all.  It gets copied to simulation
# servers on its own, so it may import NOTHING from this repo -- see CLAUDE.md,
# "`reduce_snp.py` specifics".  Duplicating the Touchstone parser there is the
# intended cost of that.
STANDALONE = "reduce_snp"

# `pkg_rlc_help` is presentation-only prose.  It sits at L3 so the frontend can
# import it, but it may not reach past the shared model: a Help tab that needs a
# solver or a report formatter has turned into a second renderer of something
# that is already rendered somewhere else.
HELP_MODULE = "pkg_rlc.present.help"
HELP_MAX_LAYER = L1_MODEL


# --------------------------------------------------------------------------
# The dodges.  There is ONE left, and it is not a dodge.
#
# There were TEN: `pkg_rlc_files_gui -> pkg_rlc_gui` x8 and
# `pkg_rlc_attrib_gui -> pkg_rlc_gui` x1 (through a `_gui()` shim), plus the
# one below.  Every one of the nine was the same defect -- `pkg_rlc_gui` held
# `TraceConfig`, the colour palette and the App in one file, so a panel that
# wanted the data model had to reach UP into the frontend, and an
# `import pkg_rlc_gui` inside a function body was the only way to do it
# without a cycle.
#
# They are gone, and they were removed the way this file demands: BY MOVING
# THE SYMBOL DOWN, never by adding an exception here.  `FileEntry`,
# `TraceConfig`, `SolveNetwork` and the signature functions went to the new
# `pkg_rlc_model` (L1); `WARN_FG`, `PORT_ROLE_FG` and `_fixed_map_filter` went
# to `pkg_rlc_widgets` (L4) beside `PLACEHOLDER_FG`, so the application has one
# palette in one module.  The rest of what those functions reached for --
# `compose_spec_problems`, `trace_file_labels`, `_trace_role_rows`, the three
# port-field scopers, `_value_formatter`, `LOG_WARN` -- had ALREADY moved down
# in earlier waves, so those lazy imports were reaching through `pkg_rlc_gui`
# for symbols that were no longer its.
#
# An earlier pair, `pkg_rlc_attrib_gui -> pkg_rlc_extractor` x2, went the same
# way when the ground-model parser moved into `pkg_rlc_attrib_report`.
#
# WHY THE LAST ONE STAYS, and why it is a different thing.  `main()` in
# `pkg_rlc_extractor` imports `App` inside the GUI-launch branch.  Both modules
# are L6, so a module-level import would be LEGAL here -- this pair is not
# dodging a cycle and never was.  It is deferred because of COST: measured in
# three fresh processes, `import pkg_rlc_extractor` is 95 / 98 / 99 ms and
# `import pkg_rlc_gui` on top is a further 265 / 251 / 251 ms of tkinter and
# matplotlib, which every --diagnose, --compose and --attribute run in a script
# would pay for a window it never opens.  And what it reaches for is `App`
# itself, a real Tk class: there is nothing to move down, because it IS the
# frontend.  The reason is written beside the statement too.
#
# So: this set is now a whitelist of ONE justified deferral, not a list of
# defects.  Adding to it needs the same standard -- a measured cost, and a
# symbol that genuinely cannot live below its caller.
# --------------------------------------------------------------------------

KNOWN_BACK_IMPORTS = frozenset({
    ("pkg_rlc.frontend.cli", "pkg_rlc.frontend.app"),
})

# How many statements per pair, so that removing SOME of a module's dodges is
# visible here too.  Without this, a module could go from eight to one and the
# pair set above would not move -- which is exactly the half that made the
# removal above provable.
KNOWN_BACK_IMPORT_COUNTS = {
    ("pkg_rlc.frontend.cli", "pkg_rlc.frontend.app"): 1,
}

_FIX = (
    "The fix is to MOVE THE SYMBOL, not the import: put whatever "
    "{low} needs out of {high} into a module BELOW {low} "
    "(the shared data model belongs at L1, colour constants at L3/L4) and "
    "import it normally at the top of the file."
)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

class ModuleImports:
    """What one source file imports from this repo, split by where it is."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.module_level: set[str] = set()
        # (imported module, qualified function name, line number)
        self.function_level: list[tuple[str, str, int]] = []

    def sorted_module_level(self) -> list[str]:
        return sorted(self.module_level)


class _ImportVisitor(ast.NodeVisitor):
    """Collects repo imports, remembering whether it is inside a function.

    A `class` body is NOT a function body -- an import written there still runs
    at import time, so it counts towards the module-level graph.  Only a
    `def` / `async def` defers execution, which is exactly what a dodge relies
    on.
    """

    def __init__(self, record: ModuleImports, repo_modules: set[str]):
        self._record = record
        self._repo = repo_modules
        self._stack: list[str] = []
        self._fn_depth = 0

    def visit_FunctionDef(self, node):  # noqa: N802 (ast API)
        self._stack.append(node.name)
        self._fn_depth += 1
        self.generic_visit(node)
        self._fn_depth -= 1
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):  # noqa: N802 (ast API)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_Import(self, node):  # noqa: N802 (ast API)
        for alias in node.names:
            self._record_name(alias.name, node.lineno)

    def visit_ImportFrom(self, node):  # noqa: N802 (ast API)
        if node.level:            # a relative import; nothing here writes one
            return
        if not node.module:
            return
        # `from pkg_rlc.physics import core` names the module in the ALIAS, not
        # in `node.module`.  Nothing in this repo is written that way, but a
        # gate that cannot see a form is a gate with a hole in it.
        for alias in node.names:
            if self._resolve(node.module + "." + alias.name):
                self._record_name(node.module + "." + alias.name, node.lineno)
                return
        self._record_name(node.module, node.lineno)

    def _resolve(self, dotted: str) -> str | None:
        """The repo module `dotted` names, or None.

        A dotted import may name a module (`pkg_rlc.physics.core`) or an
        attribute of one; walk up until something in the tree matches, so
        `import pkg_rlc.physics.core` and `from pkg_rlc.physics.core import X`
        both land on the same node of the graph.
        """
        parts = dotted.split(".")
        while parts:
            candidate = ".".join(parts)
            if candidate in self._repo:
                return candidate
            parts.pop()
        return None

    def _record_name(self, dotted: str, lineno: int) -> None:
        target = self._resolve(dotted)
        if target is None or target == self._record.name:
            return
        if self._fn_depth:
            self._record.function_level.append(
                (target, ".".join(self._stack), lineno)
            )
        else:
            self._record.module_level.add(target)


_SCAN_CACHE: dict[Path, dict[str, ModuleImports]] = {}


def module_name_of(path: Path, root: Path) -> str:
    """The importable name of a source file: flat stem, or dotted in `pkg_rlc/`."""
    rel = path.relative_to(root)
    if len(rel.parts) == 1:
        return path.stem
    return ".".join(rel.with_suffix("").parts)


def scan_tree(root: Path) -> dict[str, ModuleImports]:
    """Parse the package under `pkg_rlc/`, plus the root scripts.

    Two shapes, because two shapes exist.  The real tree is the package;
    `TestTheGateHasTeeth` builds flat one-file trees in a temp dir, and
    `reduce_snp.py` and the `pkg_rlc_extractor.py` entry-point shim are flat at
    the root for reasons of their own.  `__init__.py` files are skipped: every
    one of them is a docstring and nothing else, which is what keeps the
    packages out of the import graph entirely -- and
    `test_no_package_init_imports_anything` is what holds them to it.
    """
    root = Path(root)
    cached = _SCAN_CACHE.get(root)
    if cached is not None:
        return cached

    paths = sorted(root.glob("pkg_rlc_*.py"))
    paths += sorted(
        p for p in (root / "pkg_rlc").rglob("*.py")
        if p.name != "__init__.py"
    )
    standalone = root / (STANDALONE + ".py")
    if standalone.exists():
        paths.append(standalone)

    names = {p: module_name_of(p, root) for p in paths}
    repo_modules = set(names.values())
    out: dict[str, ModuleImports] = {}
    for path in paths:
        name = names[path]
        record = ModuleImports(name, path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _ImportVisitor(record, repo_modules).visit(tree)
        out[name] = record
    _SCAN_CACHE[root] = out
    return out


def layer_of(module: str) -> int | None:
    """The declared layer of `module`, or None if it has none."""
    if module in LAYERS:
        return LAYERS[module]
    for prefix, level in LAYER_PREFIXES:
        if module.startswith(prefix):
            return level
    return _LEGACY_LAYERS.get(module)


def back_import_pairs(scanned: dict[str, ModuleImports]) -> set[tuple[str, str]]:
    return {
        (rec.name, imported)
        for rec in scanned.values()
        for imported, _fn, _line in rec.function_level
    }


def back_import_counts(scanned: dict[str, ModuleImports]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for rec in scanned.values():
        for imported, _fn, _line in rec.function_level:
            key = (rec.name, imported)
            counts[key] = counts.get(key, 0) + 1
    return counts


def find_cycle(scanned: dict[str, ModuleImports]) -> list[str] | None:
    """Return one module-level import cycle as a path, or None.

    Plain colouring DFS.  The path is returned rather than a bare bool because
    "there is a cycle somewhere in ten modules" is not an actionable message.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {name: WHITE for name in scanned}
    stack: list[str] = []

    def walk(name: str) -> list[str] | None:
        colour[name] = GREY
        stack.append(name)
        for target in sorted(scanned[name].module_level):
            if target not in scanned:
                continue
            if colour[target] == GREY:
                return stack[stack.index(target):] + [target]
            if colour[target] == WHITE:
                found = walk(target)
                if found is not None:
                    return found
        stack.pop()
        colour[name] = BLACK
        return None

    for name in sorted(scanned):
        if colour[name] == WHITE:
            found = walk(name)
            if found is not None:
                return found
    return None


def upward_imports(scanned: dict[str, ModuleImports]) -> list[str]:
    """Module-level imports that reach from a lower layer to a higher one."""
    problems: list[str] = []
    for name in sorted(scanned):
        if name == STANDALONE:
            continue                      # its own, stricter rule
        here = layer_of(name)
        if here is None:
            continue                      # unlayered: its own test reports it
        for target in scanned[name].sorted_module_level():
            there = layer_of(target)
            if there is None:
                if target == STANDALONE:
                    problems.append(
                        f"{name} imports the standalone {target}. "
                        f"{STANDALONE}.py is copied to simulation servers on "
                        f"its own and is not part of this import graph -- "
                        f"move the shared symbol into a pkg_rlc_* module below "
                        f"{name} instead."
                    )
                continue
            if there > here:
                problems.append(
                    f"{name} ({LAYER_NAMES[here]}) imports {target} "
                    f"({LAYER_NAMES[there]}) at module level -- that is "
                    f"UPWARD. " + _FIX.format(low=name, high=target)
                )
    return problems


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

class TestTheLayerMapHolds(unittest.TestCase):
    """Property 1: nothing imports from a higher layer."""

    def test_no_module_imports_from_a_higher_layer(self):
        problems = upward_imports(scan_tree(REPO_ROOT))
        self.assertEqual(
            [], problems,
            "Upward import(s) found:\n  " + "\n  ".join(problems),
        )

    def test_every_root_module_has_a_declared_layer(self):
        """A new pkg_rlc_*.py must join the map, or it escapes every rule here."""
        scanned = scan_tree(REPO_ROOT)
        unlayered = sorted(
            name for name in scanned
            if name != STANDALONE and layer_of(name) is None
        )
        self.assertEqual(
            [], unlayered,
            "These root modules exist but are not in LAYERS:\n  "
            + "\n  ".join(unlayered)
            + "\nAdd each to LAYERS in this file, at the LOWEST layer that can "
              "hold it -- an unlayered module is checked by nothing.",
        )

    def test_the_help_module_reaches_no_further_than_the_model(self):
        scanned = scan_tree(REPO_ROOT)
        if HELP_MODULE not in scanned:
            self.skipTest(f"{HELP_MODULE} does not exist")
        too_high = [
            target
            for target in scanned[HELP_MODULE].sorted_module_level()
            if (layer_of(target) or 0) > HELP_MAX_LAYER
        ]
        self.assertEqual(
            [], too_high,
            f"{HELP_MODULE} imports {too_high} -- it is presentation-only "
            f"prose and may reach no further than "
            f"{LAYER_NAMES[HELP_MAX_LAYER]}. If a Help tab needs a computed "
            f"value, have the CALLER pass it in; a second renderer of "
            f"something already rendered elsewhere is two things that can come "
            f"to disagree.",
        )

    def test_no_package_init_imports_anything(self):
        """`scan_tree` skips `__init__.py`; this is why that is safe.

        A package `__init__` that imported its own modules would put an edge in
        the real import graph that every rule in this file is blind to -- and
        it would make `import pkg_rlc.physics.core` drag in tkinter through
        `pkg_rlc/__init__.py`. Keep them prose-only.
        """
        pkg = REPO_ROOT / "pkg_rlc"
        if not pkg.is_dir():
            self.skipTest("pkg_rlc/ does not exist")
        offenders: list[str] = []
        for path in sorted(pkg.rglob("__init__.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
                    )
        self.assertEqual(
            [], offenders,
            "These package __init__.py files contain an import:\n  "
            + "\n  ".join(offenders)
            + "\nEvery __init__.py in pkg_rlc/ is a docstring and nothing "
              "else, on purpose: scan_tree() skips them, so an import written "
              "there is an edge in the import graph that this gate cannot see.",
        )

    def test_reduce_snp_imports_nothing_from_this_repo(self):
        scanned = scan_tree(REPO_ROOT)
        if STANDALONE not in scanned:
            self.skipTest(f"{STANDALONE}.py does not exist")
        rec = scanned[STANDALONE]
        found = rec.sorted_module_level() + sorted(
            {imported for imported, _fn, _line in rec.function_level}
        )
        self.assertEqual(
            [], found,
            f"{STANDALONE}.py imports {found} from this repo. It is "
            f"DELIBERATELY standalone -- it gets copied to simulation servers "
            f"on its own, with numpy and the stdlib as its only dependencies. "
            f"Duplicating code there (the Touchstone parser, the n==2 "
            f"column-order quirk) is the intended cost; keep both copies in "
            f"step by hand.",
        )


class TestTheGraphIsAcyclic(unittest.TestCase):
    """Property 2: no import cycle among module-level imports."""

    def test_the_module_import_graph_is_acyclic(self):
        cycle = find_cycle(scan_tree(REPO_ROOT))
        self.assertIsNone(
            cycle,
            "Module-level import cycle: "
            + (" -> ".join(cycle) if cycle else "")
            + "\nBreak it by MOVING A SYMBOL down into a module both sides can "
              "import -- NOT by pushing one of these imports inside a function. "
              "A lazy import hides the cycle from the interpreter and leaves it "
              "in the design.",
        )


class TestTheBackImportsAreExactlyKnown(unittest.TestCase):
    """Property 3: the function-level dodges are the known set, both ways."""

    def test_the_function_level_back_imports_are_exactly_the_known_set(self):
        found = back_import_pairs(scan_tree(REPO_ROOT))

        added = sorted(found - KNOWN_BACK_IMPORTS)
        self.assertEqual(
            [], added,
            "NEW function-level import(s) of a repo module:\n  "
            + "\n  ".join(
                f"{low} imports {high} inside a function body. "
                + _FIX.format(low=low, high=high)
                for low, high in added
            ),
        )

        gone = sorted(KNOWN_BACK_IMPORTS - found)
        self.assertEqual(
            [], gone,
            "These function-level back-imports are listed in "
            "KNOWN_BACK_IMPORTS but no longer exist:\n  "
            + "\n  ".join(f"{low} -> {high}" for low, high in gone)
            + "\nIf you removed them, delete them from KNOWN_BACK_IMPORTS (and "
              "KNOWN_BACK_IMPORT_COUNTS) in THIS COMMIT. This half of the "
              "assertion is deliberate: it is what makes a phase that claims "
              "to have removed a dodge prove it here.",
        )

    def test_the_back_import_COUNTS_are_exactly_the_known_set(self):
        """Partial removal has to be visible too.

        Without this, `pkg_rlc_files_gui` could go from eight lazy imports to
        one and the pair set above would not move.
        """
        found = back_import_counts(scan_tree(REPO_ROOT))
        moved = sorted(
            (pair, KNOWN_BACK_IMPORT_COUNTS.get(pair, 0), found.get(pair, 0))
            for pair in set(found) | set(KNOWN_BACK_IMPORT_COUNTS)
            if KNOWN_BACK_IMPORT_COUNTS.get(pair, 0) != found.get(pair, 0)
        )
        self.assertEqual(
            [], moved,
            "The number of function-level back-imports moved:\n  "
            + "\n  ".join(
                f"{low} -> {high}: expected {want}, found {got}"
                for (low, high), want, got in moved
            )
            + "\nIf you REMOVED some, update KNOWN_BACK_IMPORT_COUNTS in this "
              "commit. If you ADDED one, move the symbol instead -- see the "
              "comment above KNOWN_BACK_IMPORTS.",
        )

    def test_the_two_declarations_agree_with_each_other(self):
        """A guard on this file, not on the tree."""
        self.assertEqual(
            KNOWN_BACK_IMPORTS,
            frozenset(KNOWN_BACK_IMPORT_COUNTS),
            "KNOWN_BACK_IMPORTS and KNOWN_BACK_IMPORT_COUNTS list different "
            "pairs -- update both together.",
        )


class TestTheGateHasTeeth(unittest.TestCase):
    """The gate itself, driven against synthetic sources.

    Every check above is only as good as its ability to go red, and the honest
    way to show that is to feed it a tree that violates the rule.  Writing the
    files into a temp dir rather than editing the real ones keeps this runnable
    in a shared working tree, and keeps the proof in the suite instead of in a
    commit message nobody will find.
    """

    def _tree(self, files: dict[str, str]) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="layering_"))
        self.addCleanup(lambda: _SCAN_CACHE.pop(tmp, None))
        for name, body in files.items():
            (tmp / name).write_text(body, encoding="utf-8")
        return tmp

    def test_an_upward_module_import_is_caught(self):
        root = self._tree({
            "pkg_rlc_core.py": "import pkg_rlc_gui\n",
            "pkg_rlc_gui.py": "import pkg_rlc_core\n",
        })
        problems = upward_imports(scan_tree(root))
        self.assertTrue(problems)
        self.assertIn("pkg_rlc_core", problems[0])
        self.assertIn("pkg_rlc_gui", problems[0])
        self.assertIn("UPWARD", problems[0])

    def test_a_legal_downward_import_is_not_caught(self):
        root = self._tree({
            "pkg_rlc_core.py": "import numpy as np\n",
            "pkg_rlc_gui.py": "import pkg_rlc_core\n",
        })
        self.assertEqual([], upward_imports(scan_tree(root)))

    def test_a_same_layer_import_is_allowed(self):
        """`pkg_rlc_attrib` -> `pkg_rlc_core` is the real instance of this."""
        root = self._tree({
            "pkg_rlc_core.py": "",
            "pkg_rlc_attrib.py": "import pkg_rlc_core\n",
        })
        self.assertEqual([], upward_imports(scan_tree(root)))

    def test_a_cycle_is_found_and_named(self):
        root = self._tree({
            "pkg_rlc_core.py": "import pkg_rlc_plot\n",
            "pkg_rlc_plot.py": "import pkg_rlc_core\n",
        })
        cycle = find_cycle(scan_tree(root))
        self.assertIsNotNone(cycle)
        self.assertIn("pkg_rlc_core", cycle)
        self.assertIn("pkg_rlc_plot", cycle)

    def test_an_acyclic_tree_reports_no_cycle(self):
        root = self._tree({
            "pkg_rlc_core.py": "",
            "pkg_rlc_plot.py": "import pkg_rlc_core\n",
            "pkg_rlc_gui.py": "import pkg_rlc_core\nimport pkg_rlc_plot\n",
        })
        self.assertIsNone(find_cycle(scan_tree(root)))

    def test_a_function_level_import_is_not_counted_in_the_module_graph(self):
        """The dodge's whole mechanism: deferred, so the interpreter is happy."""
        root = self._tree({
            "pkg_rlc_core.py": "",
            "pkg_rlc_gui.py": "import pkg_rlc_core\n",
            "pkg_rlc_plot.py": (
                "import pkg_rlc_core\n"
                "def _gui():\n"
                "    import pkg_rlc_gui\n"
                "    return pkg_rlc_gui\n"
            ),
        })
        scanned = scan_tree(root)
        self.assertIsNone(find_cycle(scanned))
        self.assertEqual([], upward_imports(scanned))
        self.assertEqual(
            {("pkg_rlc_plot", "pkg_rlc_gui")}, back_import_pairs(scanned)
        )

    def test_a_class_body_import_counts_as_module_level(self):
        """It runs at import time, so it is part of the real graph."""
        root = self._tree({
            "pkg_rlc_core.py": "",
            "pkg_rlc_gui.py": "import pkg_rlc_core\n",
            "pkg_rlc_plot.py": (
                "class Thing:\n"
                "    import pkg_rlc_gui\n"
            ),
        })
        scanned = scan_tree(root)
        self.assertEqual(set(), back_import_pairs(scanned))
        self.assertTrue(upward_imports(scanned))

    def test_a_method_body_import_counts_as_function_level(self):
        root = self._tree({
            "pkg_rlc_core.py": "",
            "pkg_rlc_gui.py": "",
            "pkg_rlc_plot.py": (
                "class Thing:\n"
                "    def go(self):\n"
                "        import pkg_rlc_gui\n"
            ),
        })
        scanned = scan_tree(root)
        self.assertEqual(
            {("pkg_rlc_plot", "pkg_rlc_gui")}, back_import_pairs(scanned)
        )
        self.assertEqual(
            {("pkg_rlc_plot", "pkg_rlc_gui"): 1}, back_import_counts(scanned)
        )
        self.assertEqual([], upward_imports(scanned))

    def test_from_imports_count_too(self):
        root = self._tree({
            "pkg_rlc_core.py": "from pkg_rlc_gui import App\n",
            "pkg_rlc_gui.py": "",
        })
        self.assertTrue(upward_imports(scan_tree(root)))

    def test_a_repo_import_in_reduce_snp_is_caught(self):
        root = self._tree({
            "pkg_rlc_core.py": "",
            "reduce_snp.py": "import pkg_rlc_core\n",
        })
        scanned = scan_tree(root)
        self.assertEqual(["pkg_rlc_core"], scanned["reduce_snp"].sorted_module_level())

    def test_a_stdlib_or_numpy_import_is_never_a_finding(self):
        root = self._tree({
            "pkg_rlc_core.py": (
                "import math\n"
                "import numpy as np\n"
                "import tkinter as tk\n"
                "from matplotlib.figure import Figure\n"
            ),
        })
        scanned = scan_tree(root)
        self.assertEqual([], scanned["pkg_rlc_core"].sorted_module_level())
        self.assertEqual([], upward_imports(scanned))

    def test_an_unlayered_module_is_reported(self):
        root = self._tree({"pkg_rlc_brand_new.py": ""})
        scanned = scan_tree(root)
        self.assertIsNone(layer_of("pkg_rlc_brand_new"))
        self.assertIn("pkg_rlc_brand_new", scanned)

    def test_a_panels_module_is_layered_by_its_prefix(self):
        self.assertEqual(L5_PANELS, layer_of("pkg_rlc_panels_editor"))


if __name__ == "__main__":
    unittest.main()
