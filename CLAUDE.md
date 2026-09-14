# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

**The per-area rules are in `docs/conventions/`, and they are as binding as the
ones in this file.** What is HERE is what applies to a change ANYWHERE: the
layer map, the module map, the cross-cutting invariants, the import gate, the
bit-exactness rules `golden_legacy.npz` pins, and the rejected-proposal list.
The deep account of one window, one panel, one report moved out on 2026-08-31,
verbatim, when this file passed the 150k characters a session can hold — the
pointer table is under "The rest of the rules live in `docs/conventions/`"
below and the index is `docs/conventions/README.md`. Before changing
anything in an area, check that area's dossier for the rules that bind it
(its section titles are listed below).

**How the tree got its current shape:** `docs/REFACTOR_REPORT.md` is the account
of the 2026-08-13 layering refactor — the before/after line counts, the places
the CLI and the GUI told users different things about the same data (one fixed,
six open), and what is still owed. Read it before starting anything that moves a
symbol between modules; the sections below are the rules, that file is the state.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction — and, with more than one measurement port defined, the mutual coupling between them (M, k, M/L, C_c). Used for IC packages, EMX layout traces, DCO inductors, decap, and inductor-to-inductor pulling / spur budgeting.

`pkg_rlc/physics/attrib.py` is a layer on top of that, not a mode: it takes one extracted `Z_ab` apart into the bare EM coupling plus one exact signed term per declared termination, and answers the exact what-if. It exists because the reduction assumption — everything unlisted is OPEN — moved a real answer by 6.07 dB with nothing on screen saying so.

## Where the modules live: the folders ARE the layers

The 25 modules were flat at the repo root, every one of them starting with the
same nine characters `pkg_rlc_`. They are a package now, and **the subpackage
is the layer** — the same L0..L6 map `tests/test_layering.py` enforces, made
visible in the tree instead of living only in that file:

```
pkg_rlc/physics/    L0   touchstone  spec  solve  core  compose  attrib
pkg_rlc/model/      L1   trace  validate
pkg_rlc/services/   L2   session  run
pkg_rlc/present/    L3   report  csv  attrib_report  conntable  help
pkg_rlc/widgets/    L4   widgets  plot
pkg_rlc/panels/     L5   panels_files  panels_traces  panels_results
                         panels_editor  files_gui  attrib_gui
pkg_rlc/frontend/   L6   app  cli
```

Four things about it are worth knowing before you move anything:

- **`validate` is in `model/`, not `services/`.** It is L1 and it has to be:
  `pkg_rlc.model.trace` imports it (`port_descriptor`, `info_str`, all three
  legacy migrations, `_config_signature`). Filing it with the session file
  would make the tree say L2 where the import graph says L1.
- **`pkg_rlc_extractor.py` is still at the repo root and is a 41-line SHIM.**
  The name is the published entry point — README, every Help tab, the CLI's own
  `--help` examples, `doctor.sh`, and the SENTINEL `deploy.sh` and `pack.ps1`
  both check. The CLI itself is `pkg_rlc/frontend/cli.py`. **Where the prose
  below says `pkg_rlc_extractor`, read it as the CLI module** unless it is
  quoting a command line.
- **Every `__init__.py` is a docstring and nothing else**, so a package import
  drags in no tkinter and adds no edge to the graph. `scan_tree` skips them and
  `test_no_package_init_imports_anything` holds them to it.
- **`reduce_snp.py` did not move and must not.** It is copied to simulation
  servers on its own and imports nothing from this repo.

`import pkg_rlc_gui` is now `import pkg_rlc.frontend.app as pkg_rlc_gui` at the
four call sites that used the bare form — the alias is what keeps ~1000
`pkg_rlc_gui.X` attribute references, and `mock.patch.object(pkg_rlc_core, …)`,
pointing at exactly the module they always did.

The move itself is `tests/_repackage.py`, committed so it reads as a diff.

**`tests/test_layering.py` DERIVES the layer from the folder, and there is no
hand-written module-to-layer table any more.** `layer_of()` reads the second
component of the dotted name, and the only declaration left is
`LAYER_OF_FOLDER` — the seven names above against their numbers, i.e. what a
folder MEANS, not where a module is. So the tree and the gate cannot come to
disagree: moving a file IS moving its layer, a new panel needs no entry, and a
module in a folder nobody has declared FAILS rather than defaulting to
something. **Do not reintroduce a hand-written map**; if a module is in the
wrong layer, move the FILE. The rules the gate enforces are unchanged and are
listed under "The import layering gate" below.

## Module map

**One table per layer, in the order of the folders**, so this document and the
tree tell one story: a row's layer is the heading it is under, which is the
directory it is in, which is what `tests/test_layering.py` reads. A module that
moves to another folder moves to another table here, and nothing else about its
row changes.

### L0 — `pkg_rlc/physics/` (arrays and physics; no Tk, no App, no widgets)

| File | Responsibility |
|---|---|
| `pkg_rlc/physics/core.py` | **A FACADE, 169 lines.** Re-exports `touchstone`, `spec` and `solve` BY NAME (not `import *`); still the module to import from unless you are inside one of the three. Imports those three only. |
| `pkg_rlc/physics/touchstone.py` | **Reading a file and saying what is wrong with it**: the parser, both sniffers, the diagnosis, the descriptive checks, `TouchstoneData` and the `FAULT_*` verdicts. Imports NOTHING from this repo — the bottom of L0. |
| `pkg_rlc/physics/spec.py` | **What the user DECLARED**: `TerminationSet`, the lumped-admittance helpers, every port/spec-string parser, the merged nodes, the Mode 5 DSL, the row model, the port roles. Imports `format_si` from `touchstone` only. |
| `pkg_rlc/physics/solve.py` | **The arithmetic**: `s_to_y` / `y_to_s`, `compute_z_matrix` / `compute_z` / `_probe_impedance`, the extractors, the fit models, the tolerances. Imports `spec` + two names from `touchstone`; nothing imports it back. |
| `pkg_rlc/physics/attrib.py` | **Port attribution**: the exact signed decomposition of `Z_ab`, the exact what-if, the cold-start screen (CLI-only), the composed-network gauge. Imports `pkg_rlc.physics.core` ONLY (acyclic), no scipy. |
| `pkg_rlc/physics/compose.py` | **Several Touchstone files measured as ONE network**: k files stacked into one `Y`, every cross-file link an ordinary `ShortPair` / `LumpedBetween` handed to the SAME `compute_z_matrix`. Imports `pkg_rlc.physics.core` ONLY. |
| `pkg_rlc/physics/tracemodel.py` | **A routed trace as a pi model, read EXACTLY**: `Y_series = -Ym`, `Y_shunt = Yii + Ym` off the inverse of a 2x2 `Zmat` block — an identity, not a fit — plus the differential imbalance check. Imports `pkg_rlc.physics.core` ONLY (acyclic). |

### L1 — `pkg_rlc/model/` (the shared data model, and the spec logic over it)

| File | Responsibility |
|---|---|
| `pkg_rlc/model/trace.py` | **The shared data model every layer above passes around** (L1): `FileEntry`, `TraceConfig`, the signatures, the frequency snap, the run record. Imports `core` and `validate` only — no Tk, no matplotlib, no `App`. |
| `pkg_rlc/model/validate.py` | **What a spec SAYS, what it will DO, and what is wrong with it** (L1). Imports `core` and `compose` ONLY, and duck-types the trace rather than importing it. |

### L2 — `pkg_rlc/services/` (services over the model)

| File | Responsibility |
|---|---|
| `pkg_rlc/services/session.py` | **The session file** (L2): Save / Load / on-exit autosave as a pure dict <-> model round trip, with no Tk in it and never any. Imports `core` and `trace` only. |
| `pkg_rlc/services/run.py` | **What a Calculate actually RUNS** (L2): the network, the spec, and the checks and reductions over both. No Tk — `log` / `files` / `cache` are INJECTED rather than reached for. |

### L3 — `pkg_rlc/present/` (turning a result into text)

| File | Responsibility |
|---|---|
| `pkg_rlc/present/report.py` | **Turning a finished run into TEXT** (L3): the three views and every formatter under them, the tab labels, the run diff, the width budgets, the digit count, the frequency provenance. Imports `core` and `trace` only. |
| `pkg_rlc/present/csv.py` | **The CSV export blocks** (L3). Beside `report.py` rather than inside it: the pane is a measured 144-column budget, the CSV is every value at full precision. Imports `core` and `trace` only. |
| `pkg_rlc/present/attrib_report.py` | **The attribution report as TEXT**: the thirteen `_attr_print_*` / `_cold_print_*` sections, RETURNING `list[str]`. Imports `attrib`, `core` and `report` and nothing else — no tkinter, no matplotlib. |
| `pkg_rlc/present/tracemodel_report.py` | **The trace pi model as TEXT**: the drawing (whose layout is computed from the value strings), the per-branch `\|Q\|` verdict, the lumped check and the imbalance qualifier, RETURNING `list[str]`. Imports `tracemodel`, `core` and `report` — no tkinter, no matplotlib. |
| `pkg_rlc/present/conntable.py` | **The connections table's SHAPE, and the RowTable vocabulary it is spoken in** (L3), including `ColumnSpec` / `TableLayout` / `identity_layout`. Imports `pkg_rlc.physics.core` only. |
| `pkg_rlc/present/help.py` | In-app Help window content — 140 lines, because the 2648 lines of prose are ten plain-text files under `docs/help/`, read at import time. May reach no further than L1. |

### L4 — `pkg_rlc/widgets/` (generic Tk widgets, which know nothing about this app)

| File | Responsibility |
|---|---|
| `pkg_rlc/widgets/widgets.py` | **The generic Tk widgets, which know nothing about this app** (L4): the placeholder widgets, `RowTable`, `ReflowRow` / `reflow_rows`, and THE WHOLE PALETTE. Imports `conntable` and seven `ROLE_*` names only. |
| `pkg_rlc/widgets/plot.py` | Matplotlib plot panel: the multi-subplot grid, the draggable freq marker, the M / V / Delete keys, the fullscreen window and the control strip. Imports `widgets.py`, `conntable` and `core`. |

### L5 — `pkg_rlc/panels/` (app-specific windows and panels)

| File | Responsibility |
|---|---|
| `pkg_rlc/panels/panels_files.py` | **The Loaded Files section** (L5): `FilesPanel` — the frame, its four buttons, the Listbox, the right-click menu and the load / add / remove / check / clear handlers. Imports L0–L4 only. |
| `pkg_rlc/panels/panels_traces.py` | **The Traces section** (L5): `TracesPanel` — add / remove / duplicate / toggle / freeze / unfreeze / clear all, and the three menu labels that moved with the menu they label. Imports L0–L4 only. |
| `pkg_rlc/panels/panels_results.py` | **The Results pane** (L5): `ResultsPanel` — the header strip, the notebook, the Log tab and its badge, the run pages with keep / evict, both menus, `_tag_swatch_rows`. Imports L0–L4 only. |
| `pkg_rlc/panels/panels_editor.py` | **The editor** (L5): `EditorPanel` — the pinned footer, the mode-aware form, both `RowTable`s, the strips, the text hatch, the auto-apply sync chain, and `StylePicker`. Imports L0–L4 only. |
| `pkg_rlc/panels/files_gui.py` | **Which FILES a trace is made of** (round 3): the `Files in this trace…` window, the port-cell scope rules and the GUI rendering of the reference-node check. **It imports `pkg_rlc.frontend.app` NOT AT ALL.** |
| `pkg_rlc/panels/attrib_gui.py` | **The Attribution window** — a modeless `Toplevel` over `pkg_rlc.physics.attrib` — plus the pure formatters it is testable through with no display. **It imports NOTHING back and has no deferred imports left.** |

### L6 — `pkg_rlc/frontend/` (the App itself and the argv entry point)

| File | Responsibility |
|---|---|
| `pkg_rlc/frontend/app.py` | Tkinter GUI: files, traces, the editor, the results pane, the session format, the run record, the Ports & Roles window, the Attribution hooks, the multi-file engine. Imports every layer below it; **every symbol split out of it is RE-EXPORTED at the top of the file**. |
| `pkg_rlc/frontend/cli.py` (and the root `pkg_rlc_extractor.py` shim) | Entry point: dispatches GUI vs CLI from argv. `--mode gnd \| p2p \| coupling`, `--mport` repeatable. Imports every layer below it, plus `pkg_rlc.frontend.app` lazily inside `main()`'s GUI branch (the one declared back-import). |

### Outside the package

| File | Responsibility |
|---|---|
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |

### `tests/` — where to look

The one-line-per-file index moved to the TOP of
`docs/conventions/test_suite_map.md` on 2026-09-12, above the full account it
indexes. The runner commands are under "How to run tests" below; **"what do I
run after changing X" is answered by that index**, and "why does that test
exist" by the rows under it.

## The rest of the rules live in `docs/conventions/`

On **2026-08-31** the per-area dossiers moved out of this file to
`docs/conventions/`, **VERBATIM**, when it was **428.7k characters**, nearly
three times the 150k a session can hold. Nothing was deleted, nothing was
reworded, and one duplicated pair of bullets became one.

**They are exactly as binding as the rules that stayed here.** What stayed is
what applies to a change ANYWHERE: the layer map, the module map, the
cross-cutting invariants, the import gate, the bit-exactness rules that
`golden_legacy.npz` pins, and the rejected-proposal list. What moved is the deep
account of ONE area — which you read *before* you touch that area, not after.

**Every heading in those files is the section title it had here**, so a
cross-reference of the form ``CLAUDE.md § <title>`` — there are several, in
`docs/design_connection_table.md`, `docs/design_port_attribution.md`,
`docs/REFACTOR_REPORT.md` and in source comments — resolves through this table.

| Read this before touching… | Sections it holds (their titles are unchanged) |
|---|---|
| [`architecture.md`](docs/conventions/architecture.md) | The four panels of the main window (`pkg_rlc/panels/panels_*.py`) · Clearing the lists (`Clear all files` / `Clear all traces` / `Clear All`) · One formatter, two spellings (the CLI and the results pane) · The run module (`pkg_rlc/services/run.py`) — the SOLVE landed, the ORCHESTRATION did not · How the run record got its home — READ THIS BEFORE MOVING ANY OF IT · How to add a new measurement mode · How to add a new fit model |
| [`attribution_core.md`](docs/conventions/attribution_core.md) | Port attribution (`pkg_rlc/physics/attrib.py`) · The cold-start screen (`--cold-start`, CLI only) |
| [`attribution_gui.md`](docs/conventions/attribution_gui.md) | The Attribution window (`pkg_rlc/panels/attrib_gui.py`) · The two attribution reports (`pkg_rlc/present/attrib_report.py`) |
| [`cli_report.md`](docs/conventions/cli_report.md) | The CLI's printed report (`tests/fixtures/cli_reference/`) |
| [`editor_and_tables.md`](docs/conventions/editor_and_tables.md) | Connection table (the Mode 5 / Mode 6 row editor) · Per-kind row shape, nets, and the parallel stamp (round 1) · Auto-apply, the style picker, plot visibility · Port names, roles, and the Ports & Roles window |
| [`multifile.md`](docs/conventions/multifile.md) | Composition — several files as ONE network (`pkg_rlc/physics/compose.py`, round 2) · The two-file GUI — schema, namespace, engine (round 3) |
| [`plot_panel.md`](docs/conventions/plot_panel.md) | The plot panel's axes (what range they show, what unit they say) · The plot panel's control strip · Cursor readout (the plot's marker / V-line labels) |
| [`reading_files.md`](docs/conventions/reading_files.md) | Reading files (robustness, diagnosis, refusal) |
| [`results_pane.md`](docs/conventions/results_pane.md) | Freeze as trace (the before/after comparison) · The run snapshot (what a finished Calculate leaves behind) · The Results pane notebook (the Log tab and its badge) · The three results views (`detail` / `summary` / `compare`) · The Digits control (how many significant digits a value is printed to) · Run history (the run tabs after the Log) |
| [`session_and_help.md`](docs/conventions/session_and_help.md) | The session file (Save Config / Load Config / autosave) · The Help window's prose lives in `docs/help/`, not in Python |
| [`trace_model.md`](docs/conventions/trace_model.md) | The trace pi model (`pkg_rlc/physics/tracemodel.py`, `pkg_rlc/present/tracemodel_report.py`) · Why the schematic is TEXT, and what would let it be a Canvas · What the CLI surface owes |
| [`standalone_and_deploy.md`](docs/conventions/standalone_and_deploy.md) | `reduce_snp.py` specifics · `deploy/` specifics (red-zone pipeline) · Hiding the GUI tests (`tests/_isolated_desktop.py`) |
| [`test_suite_map.md`](docs/conventions/test_suite_map.md) | `tests/` — the suite, in the order it grew · Index — one line per file (**the one-liners live at the top of that file now**) · Shard priority |
| [`rejected_ui.md`](docs/conventions/rejected_ui.md) | Rejected UI proposals (do not re-propose these) |
| [`KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) (one level up) | Known, not fixed — defects recorded rather than patched, and what a real fix would need |
| [`solve_numerics.md`](docs/conventions/solve_numerics.md) | The solver's failure paths (the long form of five invariants below) · Measurement ports / coupling (Mode 6) — bit-exactness rules |

Two things about them, so the split does not rot:

- **A new rule goes in the file that owns its AREA, not here.** This file is for
  what a session must know without being told to look. If it is about one
  window, one panel or one report, it belongs in its dossier — and the dossier
  is where the next session will look, because this table sends them there.
- **They ship to the red zone.** `.gitattributes` is a blacklist and
  `docs/conventions/` is not on it, which is the same treatment
  `docs/theory.md` and the three design notes already get. `CLAUDE.md` itself is
  `export-ignore`d; that asymmetry is deliberate and was left alone.

## Critical invariants (do not regress these)

- **All Listboxes set `exportselection=False`.** Without it, clicking an Entry/Spinbox steals the X selection and clears the highlight. The editor resolves its auto-apply target from that selection, so a cleared highlight means every keystroke is silently discarded.
- **Auto-sync editor on Calculate.** Before computing, flush any queued sync and push current editor fields into the selected trace. Auto-apply usually got there first, but a keystroke in the same event burst as the click is still in the idle queue.
- **Truncate trace labels to 30 chars** in plot legends, or subplots squeeze.
- **Log-scale drag tolerance.** The freq-marker drag detector must use log-space distance when the x-axis is log.
- **Canvas focus.** After every `FigureCanvasTkAgg`, call `canvas.get_tk_widget().focus_set()` so M / V / Delete keys are received (also in the fullscreen `Toplevel`).
- **Port indices: 1-based at the GUI/CLI boundary, 0-based inside core.** Convert in the `build_terminations_*` builders, never deeper.
- **Schur reduction uses `np.linalg.solve`** (not explicit inverse). On `LinAlgError` or pathological condition, fall back to `np.linalg.lstsq` and emit a warning naming the offending frequency.
- **`lstsq` IS THE LAST RESORT AND IT CAN FAIL TOO — one bad frequency must
  NaN that frequency, never the sweep.** A lumped `L` to ground is `inf+nanj`
  at `w == 0` and a DC-isolated port is exactly singular there, so LAPACK's
  SVD fails at index 0 of an otherwise healthy sweep; the guard is
  `complex(nan, nan)` for that frequency plus a warning naming it, because a
  real NaN would leave `imag == 0` and `L = Im(Z)/omega` would read as a
  plausible 0 H. The guard test is
  `tests/test_core.py::TestOneBadFrequencyDoesNotAbortTheSweep`; the
  user-side workaround needing no redeploy is a finite series `R` on the lead
  (`R=1u` beside `L=50p`). Full account, with the measurements and the order
  that reproduces it: `docs/conventions/solve_numerics.md`.
- **Auto-create a default trace on file load.** Don't make users hit "Add Trace" for the basic workflow.
- **Y-axis log uses `symlog` with `linthresh=1e-6`** to handle data crossing zero.
- **R / L / C / Q are reported with their physical sign (Cadence convention).** `extract_rlc_at_freq`, the plot's `trace_y_values`, and both CSV exporters must NOT clip negative values to NaN. Q is `Im(Z)/Re(Z)`, not `|Im(Z)|/Re(Z)`; `L = Im(Z)/ω` and `C = -1/(ωIm(Z))` go negative past/below SRF respectively. The GUI results pane appends a brief annotation when a value is negative — keep that in sync if formulas change.
- **Multi-file comparison.** Each `TraceConfig` independently selects its file and port config — two traces can reference different files and plot together.
- **`PlaceholderEntry.get_value()` returns `""` when the placeholder is showing.** Never read `_var.get()` directly to fetch user input — placeholder text would leak in. Same rule for `PlaceholderText`.
- **The parser must split comment / option lines on the exotic line breaks too.** `str.splitlines()` — what the parser used before it streamed the file — breaks on `  -     `; iterating a text-mode file object breaks only on `
`. A header page-broken with a form feed would otherwise swallow the data record that follows it, silently dropping frequency points. Only `#`/`!` lines (and the tail of a mid-line `!` comment) need the check — every one of those characters is whitespace to `str.split()`, so data lines tokenise correctly either way. That is also why the hot path stays free of a per-line `splitlines()`.
- **The RI fill normalises signed zeros.** `np.add(body[...,0], 0.0, out=s.real)` rather than a plain assignment: real EDA exports write `-0.000000e+00`, and the historical `body[...,0] + 1j*body[...,1]` turned those into `+0.0`. `assert_array_equal` cannot see the difference (`-0.0 == 0.0`), so the golden reference does not guard it — `tests/test_core.py:TestParserSignedZero` does. Measured cost of the fused add: +2%.
- **`pkg_rlc.physics.core` IS A FACADE AND ITS WRITE-THROUGH IS
  LOAD-BEARING.** Its `__setattr__` / `__delattr__` forward to whichever split
  module defines the name; `tests/test_large_files.py` and
  `tests/test_parse_diagnostics.py` `mock.patch.object` five names on this
  module and then call `parse_touchstone`, so a re-export-only facade would
  rebind a copy and leave **five tests asserting nothing**, with no failure to
  notice. If you would rather delete the machinery, the replacement is to
  repoint those five call sites at `pkg_rlc.physics.touchstone` — one line
  each — in the same commit, never to drop the subclass and leave the patches
  pointing here. Full account: `docs/conventions/solve_numerics.md`.
- **`format_si` / `format_freq` live in `pkg_rlc.physics.touchstone`, not with
  the arithmetic, and that is forced.** Both callers — `TouchstoneData.freq_span`
  / `_check_freq_axis` here, and `_effective_parallel` in `pkg_rlc.physics.spec`
  — are at or below the solver, so the helpers have to be in the LOWEST of the
  three or the module holding them gets imported from underneath itself;
  `pkg_rlc.physics.solve` re-exports them, and `COMPUTE_BATCH` /
  `COMPUTE_CHUNK_BYTES` are there for the same shape of reason. Full account:
  `docs/conventions/solve_numerics.md`.
- **`_validate_port_indices` is in `pkg_rlc.physics.spec`, not
  `pkg_rlc.physics.solve`.** `build_terminations_coupling` /
  `build_terminations_rows` and `compute_z_matrix`'s backstop all call it, and
  putting it with the arithmetic makes spec import solve and solve import
  spec. Full account: `docs/conventions/solve_numerics.md`.
- **`pkg_rlc/physics/touchstone.py` is CRLF and contains a literal U+2029**
  (the parser's own exotic-line-break handling, moved with the parser that
  owns it). `str.splitlines()` breaks on U+2029 too, so every line number
  after it is off by one and a slice cuts in the wrong place **silently**.
  Anything that slices these files by line number must split raw bytes on
  `b"\r\n"`. Full account: `docs/conventions/solve_numerics.md`.
- **`StylePicker` STAYS IN `pkg_rlc.frontend.app`, and that is a CYCLE, not an
  oversight.** It draws from `COLORS` / `LINESTYLES`, which live in
  `pkg_rlc.widgets.plot`, and `pkg_rlc.widgets.plot` imports `ReflowRow` from
  `pkg_rlc.widgets.widgets` — so a `pkg_rlc.widgets.widgets` that reached back for the palettes
  would be a module-level import cycle, which
  `tests/test_layering.py::test_the_module_import_graph_is_acyclic` refuses
  outright. Do not "finish the job" by moving it there. It moves when `COLORS` /
  `LINESTYLES` have a home below both, and the same is true of the reason
  `_tag_swatch_rows` stayed behind while every other results-pane formatter went
  to `pkg_rlc.present.report`: it WRITES INTO A Tk TEXT and it reaches `COLORS`.

### The import layering gate (`tests/test_layering.py`)

- **A module may import from its own layer or a LOWER one; UPWARD is the
  failure.** Not "strictly lower" — that rule is red on arrival, because
  `pkg_rlc.physics.attrib` -> `pkg_rlc.physics.core` are both L0 and `pkg_rlc.panels.attrib_gui` ->
  `pkg_rlc.panels.files_gui` are both L5, and both are correct today. Same-layer is
  legal on purpose and what pins the order INSIDE a layer is the ACYCLICITY
  assertion, not a sub-layer number: `pkg_rlc.physics.core` importing nothing back is
  the real guarantee. Making it strictly-lower means splitting L0 into at
  least `core < {compose, attrib}` and L5 into two, which buys nothing the
  cycle check does not already give.
- **THE LAYER IS THE FOLDER, AND THERE IS NO MODULE-TO-LAYER TABLE LEFT.**
  `layer_of()` reads the second component of the dotted name —
  `pkg_rlc.present.report` is L3 because it is in `present/` — and the whole
  declaration is `LAYER_OF_FOLDER`, seven folder names against their numbers,
  which says what a folder MEANS rather than where a module is. There used to
  be a `LAYERS` dict naming all 25 modules and a `LAYER_PREFIXES` tuple for the
  panels; both are gone, and so is the failure they carried — a module moved
  without its entry moving kept its old layer, silently, while every rule in
  the file went on checking the wrong thing. **Do not reintroduce a
  hand-written map**; if a module is in the wrong layer, move the FILE.
- **An UNKNOWN FOLDER FAILS rather than defaulting to anything**, and so does a
  module loose in `pkg_rlc/` and any root `pkg_rlc_*.py` that is not the
  entry-point shim (`ROOT_MODULES`, one name, because that name is a published
  contract). That is what stops a new module slipping in unlayered and
  therefore unchecked by every rule in the file. A folder named in
  `LAYER_OF_FOLDER` that does NOT exist yet is fine and costs nothing — that is
  how the next split declares its target before writing it, which is what the
  old "modules named in `LAYERS` that do not exist are SKIPPED" branch was for.
  A deeper folder INSIDE a layer (`pkg_rlc/physics/parse/x.py`) is that layer:
  a subdivision of L0 is still L0, and every rule here is about crossings.
- **`pkg_rlc.present.help` may reach no further than L1, and `reduce_snp` may import
  NOTHING from this repo.** The second is asserted rather than assumed — it is
  copied to simulation servers on its own, and duplicating the Touchstone
  parser there is the intended cost.
- **`KNOWN_BACK_IMPORTS` is asserted in BOTH directions, and the second
  direction is the point.** Adding a lazy `import pkg_rlc.frontend.app` fails;
  REMOVING one also fails until the same commit updates the declaration. That
  is what makes the phase which claims to have removed a dodge prove it.
  Pairs alone are not sufficient — `pkg_rlc.panels.files_gui` could go from eight
  lazy imports to one and the pair set would not move — so
  `KNOWN_BACK_IMPORT_COUNTS` is declared beside it and asserted separately.
  Measured, both halves mutation-checked against copies of the real tree.
- **Today's set is ONE statement over ONE pair, and it is not a dodge.**
  `pkg_rlc.frontend.cli -> pkg_rlc.frontend.app` x1, inside `main()`'s GUI-launch branch.
  Both modules are **L6, so a module-level import there would be LEGAL** — this
  pair never dodged a cycle. It is deferred because of COST: measured in three
  fresh processes, importing the CLI (`python pkg_rlc_extractor.py`'s whole
  import cost) is 95 / 98 / 99 ms and `import
  pkg_rlc.frontend.app` on top of it is a further **265 / 251 / 251 ms** of tkinter and
  matplotlib, which every `--diagnose`, `--compose` and `--attribute` run from
  a script would pay for a window it never opens. And what it reaches for is
  `App` itself, a real Tk class: there is nothing to move down, because it IS
  the frontend. The reason is written beside the statement and beside the
  declaration, and a second entry has to meet the same standard — a measured
  cost, and a symbol that genuinely cannot live below its caller.
- **A CLASS-BODY import counts as module-level; only a `def` body defers.**
  An import written in a class body runs at import time, so it is part of the
  real graph and cannot be a dodge. Both cases are pinned.
- **The fix is always to MOVE THE SYMBOL, not the import**, and every failure
  message in the file says so by name. A lazy import hides the cycle from the
  interpreter and leaves it in the design.

### Measurement ports / coupling (Mode 6)

**The twenty-one bit-exactness rules moved to
`docs/conventions/solve_numerics.md` § "Measurement ports / coupling (Mode 6)
— bit-exactness rules" on 2026-09-12, VERBATIM. They are exactly as binding
as they were here, and `tests/fixtures/golden_legacy.npz` is still the guard
on all of them.** Read that section BEFORE touching `pkg_rlc/physics/solve.py`
or anything it calls. What is left here is the index — one line per rule, so a
session can see whether the change it is about to make is governed by one:

- **The Z matrix is the OPEN-CIRCUIT matrix.**
- **M / C_c / k are signed and are never clipped, `abs()`-ed or hidden.**
- **Group `"B"` is a legacy alias for the minus side of group `"A"`.**
- **The `G == 1` branches in `compute_z_matrix` keep the legacy floating-point expressions verbatim.**
- **Step 4c/5c (the shorted-port merge) must use `np.add.at`, never a matmul.**
- **The per-frequency contraction in 5f must stay per-frequency,**
- **`pinv(Y_node, rcond=PINV_RCOND)`, not `inv`.**
- **`np.linalg.inv` does NOT raise on a numerically singular matrix**
- **`pinv` is only valid for probes orthogonal to `null(Y_node)`.**
- **The `G == 1, no minus side` branch deliberately has NO degeneracy check.**
- **`SCHUR_COLLAPSE_TOL` is advisory only — it must never produce a NaN.**
- **Port indices are validated against the file's port count**
- **A probe port may not also be a GND port (Mode 6 only).**
- **`compute_z` warns when `G > 1`.**
- **`RECIPROCITY_WARN = 1e-3` lives in `pkg_rlc.physics.solve`**
- **`M/L` is the Norton injection ratio, NOT the current-transfer ratio.**
- **BOTH surfaces' pair lists are RANKED by `max(|M/L_a|, |M/L_b|)` and floored at `COUPLING_FLOOR_DB = -60`, through the one `rank_coupling_pairs`**
- **`compute_z` is a thin wrapper returning `Zmat[:, 0, 0]`**
- **`tests/fixtures/golden_legacy.npz` is the guard for all of the above.**
- **The Mode 5 DSL and its helpers live in `pkg_rlc/physics/spec.py`**
- **DSL signal syntax is `<port> signal <groupname> [+|-]`.**

### Rejected UI proposals (do not re-propose these)

Seven: a matplotlib connection SCHEMATIC beside the plot; a `ttk.Treeview` for
the MAIN results table; a unicode bar chart of `|k|`; a large-type KPI strip
above the plot; a FOUR-TAB notebook inside the Attribution window; a
`ttk.Treeview` for the Attribution window's contribution table; an eleventh
Help tab.
**Each was designed, measured and turned
down; the reasons are in `docs/conventions/rejected_ui.md`, VERBATIM — do not
re-propose them.** They keep coming back because they sound good in one
sentence, which is why the measurements are written down rather than left in a
commit message nobody will find.

## How to run tests

```bash
python tests/run_parallel.py            # the whole suite -- use this
python tests/run_parallel.py --fast     # 4.5 s, 1044 tests, the eighteen no-Tk modules
python tests/run_parallel.py -m attrib coupling core    # substring on module name
```

**Re-measured on this box after the package move and the layering-gate rewrite:
2618 tests / 465 shards in 442.2 s at `-j 4` (the agreed budget while the user is on the
box). `--fast` is unmoved at 1044 tests, and re-ran in 4.8 s against the 4.5 s recorded
below — same eighteen modules, same count, wall-clock noise.** (The historical figures the runner's docstring
opens with — 293 s serial against 108 s parallel over 906 tests — are what justified the
runner and are kept as such.) The full number tracks CONTENTION as much as anything: 120 s
on an idle box and 339 s with another agent competing for the same cores have both been
measured on the same tree, so **read the exit code, not the clock**. The runner shards by
test CLASS (not module: `test_run_history` alone was 86 s of that 293 s), longest-first, and
exit code 0 means every shard passed; failing shards print their full output.

`python -m unittest discover -s tests` still works and is still the ground truth, but it is
**2.7x slower for the same tests**.

While iterating, run the **narrowest set that can still catch your mistake**: `--fast` for
numeric-only changes, `-m <your modules>` otherwise. **87 % of the suite is Tk GUI tests**
that a change to `pkg_rlc.physics.core` numerics or to `pkg_rlc.physics.attrib` cannot affect. Run the full
parallel suite once before reporting — never the serial `discover`.
`test_attrib_golden` does **not** qualify for `--fast` — it creates no Tk root
but it does import tkinter.

Shards spawn at BelowNormal on Windows so the suite does not fight the user
for cores; the measurement is in `tests/run_parallel.py`'s docstring and
`docs/conventions/test_suite_map.md`.

## How to add a new measurement mode, or a new fit model

Both step-by-step recipes moved to the end of
`docs/conventions/architecture.md` on 2026-09-12, VERBATIM — "How to add a new
measurement mode" (six steps, L0 to the golden regression) and "How to add a
new fit model" (five). Read the first before picking a mode number: **the next
unused integer, never a renumbering** (4 is retired, not free — saved trace
configs carry the integer), and the files it names are named by their PATH,
because the path is the layer.

## Don'ts

- **Do not pull in `scikit-rf`.** The custom parser is deliberate — it must handle EDA-tool quirks (renamed extensions, missing option lines, ambiguous port count) that scikit-rf does not.
- **Do not add `pandas`** for CSV writing; stdlib is sufficient.
- **Do not switch GUI frameworks.** Tkinter is required.
- `scipy.optimize` and `scipy.linalg` are acceptable; gratuitous deps are not.
