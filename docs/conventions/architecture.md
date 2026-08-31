# Architecture — the panels, the run module, and the two front ends

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### The four panels of the main window (`pkg_rlc/panels/panels_*.py`)

`pkg_rlc/frontend/app.py` was 6985 lines with a ~4600-line `App` in it. The four
sections of the window are now four classes in four modules, and `App` keeps
`__init__`, the wheel router, `_build_ui`, the menubar, the PanedWindows, the
session methods, Calculate, and the wiring between panels. `pkg_rlc/frontend/app.py`
is 4407 lines.

- **HAS-A, NOT IS-A, and the reason is ORDER.** Each panel is a plain class
  that OWNS its widgets and is CONSTRUCTED BY `App` with the app injected.
  Mixins were rejected: almost every rule these sections have to keep is a
  rule about order — pack order, build order, what is populated before
  `PanedWindow.add()`, when `update_idletasks()` may not be called, which
  button is fourth in a row that `pack` unmaps from the end — and a mixin
  hides exactly that. `_build_left_panel` still shows Global Controls being
  packed `side=BOTTOM` BEFORE the editor; `_build_right_panel` still creates
  BOTH frames, populates the results one, builds the plot, and `add()`s both
  last, which is the 2 px sash rule
  `tests/test_row_table.py::TestResultsPaneVisible` exists for.
- **A binding that had a position keeps it: the panel exposes a hook and
  `_bind_events` calls it there.** `FilesPanel` and `TracesPanel` each have
  `bind_selection()` and `bind_context_menu()` rather than one `bind_events()`,
  because `_bind_events` interleaves the two lists' bindings and the order in
  which two `tk.Menu(app)` widgets are created is the order of their Tcl
  pathnames. Nothing is known to depend on it; keeping the calls where the
  lines were is cheaper than finding out.
- **THE COMPATIBILITY SURFACE IS THE RE-EXPORT RULE, ONE LEVEL DOWN.** Widgets
  are ALIASED onto App right after the panel is built (`self.files_lb =
  self._files_panel.files_lb`, and 45 more for the editor), and every moved
  method keeps a one-line DELEGATOR on App. That is what leaves 21 test
  modules and every call site unchanged, and it is the same property as
  `from N import X` in the module a symbol moved out of. An alias is safe
  because every one of those widgets is created once during the build and
  never reassigned. Two verifications, both cheap and both worth re-running
  after any further split: an AST sweep that every `from pkg_rlc.frontend.app import X`
  and `pkg_rlc.frontend.app.X` in `tests/` still resolves, and a live headless `App()`
  proving every `app.<attr>` the tests touch still exists.
  `_build_editor` / `_build_editor_form` deliberately get NO delegator: the
  panel builds itself, and a forward that would build a SECOND editor into a
  fresh parent is a trap rather than a re-export.
- **PANELS OWN WIDGETS; APP STILL OWNS THE MUTABLE STATE.** `_run_tabs`,
  `_last_run`, `_run_counter`, `_log_unseen`, `_log_forced`, the two run caps
  and their IntVars, `_run_tab_menu_target`, `_suppress_editor_sync`,
  `_ed_extra_lines`, `_ed_strips_pending`, `_ed_sync_after` /
  `_ed_sync_target`, `_ed_shown_mode` and the two `_ed_scroll_*` flags stay on
  `App`; the panels read and write them through the injected app. Those are
  REASSIGNED at runtime and are read straight off `app` by the tests, so
  moving them would need forwarding properties — two places one value can be
  read from, and one more pair that can drift. Do not "finish the job" by
  moving them without also moving what reads them.
- **A PANEL MAY NOT IMPORT `pkg_rlc.frontend.app`, at module level OR inside a
  function.** The panels are L5 and the frontend is L6
  (`tests/test_layering.py`), and the function-level back-import counts are
  pinned, so the lazy dodge is not available either. They are L5 because they
  are IN `pkg_rlc/panels/` — the gate reads the folder, so the group grows one
  panel at a time with no declaration to update, and a module under `pkg_rlc/`
  in a folder nobody has declared is unlayered and fails outright. (They used
  to be named `panelS_*` so a `LAYER_PREFIXES` entry could find them; the
  prefix is now just their name.)
- **What a panel needed and could not import came down WITH it.**
  `FREEZE_MENU_LABEL` / `UNFREEZE_MENU_LABEL` moved with the menu they label;
  `RunTab` and `_tag_swatch_rows` with the notebook they belong to
  (`_tag_swatch_rows` is the one results-pane renderer that is NOT a formatter
  — it WRITES INTO a Tk Text — which is why it never went to
  `pkg_rlc.present.report`); `StylePicker` and `MODE_PLACEHOLDERS` /
  `LABEL_PLACEHOLDER` / `EDITOR_FIELD_CHARS` / `FROZEN_EDITOR_NOTE` / both
  `MP_TABLE_HINT`s / both `MUTUAL_CURVE_HINT`s / `TEXT_DIALOG_NOTE` with the
  form. All are re-exported from `pkg_rlc.frontend.app`. `FROZEN_EDITOR_NOTE` went with
  the EDITOR and not with its two former neighbours: it names the editor's
  state, not a menu entry.
- **`App`'s STATICMETHOD ALIAS BLOCK IS THE REMAINING DEBT, AND IT IS THE
  CHECKLIST FOR THE MODEL PHASE.** `pkg_rlc.model.trace` (L1) now EXISTS and holds
  `TraceConfig`, `FileEntry`, `SolveNetwork`, `_duplicate_trace_config`,
  `_config_signature` / `_draw_signature`, the signature-field renderers, the
  frequency snap and the RUN RECORD (`RowSnapshot` / `CouplingSnapshot` /
  `FitSnapshot` / `RunSnapshot` and their builders). `pkg_rlc.services.run` (L2) still
  does not exist, and what blocks it is no longer a data-model question — see
  "The run module" above. **The alias block itself has not shrunk**, and that
  is on purpose: `app._snapshot_row` / `app._snapshot_block` must keep resolving
  to `pkg_rlc.frontend.app`'s WRAPPERS, which are what supply `reference_provenance`, so
  a panel importing `pkg_rlc.model.trace._snapshot_row` directly would silently lose
  the reference-node verdict off every row. Emptying the block is a job for
  whoever moves that render, not a tidy-up. Seven pure functions are carried on `App` as
  plain `staticmethod(...)` aliases — `_duplicate_trace_config`,
  `_freeze_trace_config`, `freeze_refusal`, `_snapshot_row`, `_snapshot_block`,
  `_config_signature`, `_draw_signature` — so a panel reaches them through
  the app it already holds. ALIASES, NOT WRAPPERS: `pkg_rlc.frontend.app.X` and
  `app.X` are the same object, so there is nothing that can disagree with the
  module-level name every test imports. The one factory beside them is
  `_make_file_entry`, which mirrors the `_make_default_trace` that was already
  there. WHEN THAT BLOCK IS EMPTY, the panels import their model directly and
  this bullet goes.
- **`RunSnapshot` and `TraceConfig` survive in the panels only as unevaluated
  annotations** (`from __future__ import annotations`). They are inert today
  and will NameError the moment anything calls `get_type_hints` on those
  modules. `_empty_run`, the one place a `RunSnapshot` is CONSTRUCTED, stayed
  on `App` for that reason.
- **THE FAILURE MODE THIS SPLIT HAS is a bare `self` where the App was meant**,
  and it is invisible to the eye and mostly invisible to the tests. Five of
  them were in the first draft: `dlg.transient(self)`, three window-refresh
  calls (`refresh_attribution_windows`, `refresh_files_windows`,
  `attribution_windows`) and one `refresh_attribution_windows(self,
  rerender=True)`. Only the first raised; the other four silently did nothing.
  Grep does not find them reliably (`(self)\b` never matches — there is no
  word boundary after `)`), and neither does reading. THE CHECK IS AN AST
  PASS: walk each panel module for a `Name` load of `self` that is not the
  `value` of an `Attribute`, excluding the classes that really are widgets
  (`StylePicker`). Run it after moving anything else into a panel. Its
  companion is a `symtable` pass for free names, which is what catches a moved
  body whose import did not come with it.

### One formatter, two spellings (the CLI and the results pane)

`pkg_rlc_extractor` used to carry its own copy of six things `pkg_rlc.present.report`
and `pkg_rlc.present.csv` already had: truncation, the plain-number format, the sign
flag, the monospace table, the Z matrix, and — under the same name on both
sides — the coupling CSV. The copies are gone. What is left in the CLI is its
own SPELLING of each, handed to the shared formatter as an argument, because
`tests/fixtures/cli_reference/` pins its stdout byte for byte and
`render_reference.json` pins the pane's.

- **Three parameters exist ONLY so the two front ends can share the arithmetic,
  and every one of them defaults to the pane's behaviour.** `_trunc_str(ell=)`
  — `'~'` on the terminal, U+2026 in the pane. `_render_columns(rule=)` — the
  run of dashes the CLI has always drawn under a header; the rule line is
  deliberately NOT right-stripped, which is what the CLI's own printer did.
  `_format_z_matrix(name=, cell=)` — the CLI cuts names at 16 and writes
  `0.003017 + j25.45`, the pane cuts at 12 and writes `0.003017+25.45j`. Do not
  "tidy" any of these into one spelling: changing the default moves
  `render_reference.json` and changing the CLI's argument moves 143 reference
  cases, and neither is a decision a refactor gets to take.
- **`_fmt_num` IS `_fmt_plain`, `_trunc` and `_print_table` are one line each,
  and the names survive because the `_attr_print_*` / `_cold_print_*` sections
  call them.** `_sign_flag_port` had no caller anywhere in the repo and was
  simply deleted.
- **The two coupling CSVs were arrived at independently and agreed anyway** —
  numpy-vectorised `k` in the GUI, per-frequency `math.sqrt` with `isfinite`
  guards in the CLI, same columns, same order, same `%.6e`. They were checked
  branch by branch (`omega == 0`, `L <= 0`, non-finite `M`, `-0.0`) before being
  collapsed onto `write_coupling_table`. That they agreed is luck, not design:
  two spellings of one file format are two things that can come to disagree
  about a number.

**ALL FOUR DIVERGENCES ARE NOW CLOSED, and the rule that decided the last three
is not "make the two surfaces the same".** The first was fixed on the older
rule — *where the two surfaces disagree AND the repo has a documented position
(a CLAUDE.md entry, a test file's stated purpose, `docs/theory.md`), the surface
matching that position wins and the other is fixed; where there is none, it is
a product choice and is listed rather than decided.* The other three had no
such position, and the user supplied one: **`deploy/doctor.sh` exits 0 when at
least one interpreter reaches TIER 2, i.e. a CLI-only install is a SUCCESSFUL
install.** On a headless red-zone box with `$DISPLAY` unset the CLI is the ONLY
surface and its reader has no pane to cross-check against, so:

> **The CLI may be TERSER than the pane, but it must never omit a diagnostic or
> a decisive number.** Where the CLI was MISSING something, it was added. Where
> it merely says something at greater LENGTH than the pane's measured
> 144-column budget allowed, the length was LEFT ALONE — a headless reader
> benefits from it. Convergence for tidiness is not a reason to touch this
> surface.

That is why the three fixes below are not symmetrical: two of them make the CLI
a SUPERSET of the pane rather than a copy of it.

- **FIXED — the CLI's frequency provenance line used to ROUND, and to carry no
  snap note at all.** `_print_coupling_report` printed
  `res.freq_hz / 1e9:.4g` where `_format_coupling_block` printed
  `marker_freq_text(freq, '{:.6g}')`. Measured on `diff_pair_4port.s4p` at
  `--freq 0.11`, which resolves to 100 990 000 Hz: the CLI said
  **`@ 0.101 GHz`** — a frequency that **is not in the file** — while the pane
  said `Z matrix @ 0.10099 GHz`, and the coupling path said nothing anywhere
  about the 990 kHz snap (`snapped to` existed only under `--attribute` and
  `--cold-start`), so that rounded number was its ONLY statement of where the
  numbers came from. Both CLI marker lines — the coupling Z-matrix line and
  the scalar `@ <f>:` line above R/L/C/Q, which had the identical defect — now
  go through `marker_freq_text`, so **the provenance arrives WITH the number
  rather than beside it**: a second, separately-worded note next to a shared
  renderer that already carries one would be two renderings of one fact, which
  is what this section is about. `_cli_marker` is the one-line helper that
  builds the `FreqSnap`, and it re-uses `_format_coupling_block`'s own rule —
  the EXTRACTOR's `freq_hz` overrides the re-derived point, because that is
  where the numbers came from. **Precision is per line and deliberately not
  uniform**: the coupling line takes the pane's `{:.6g}` (at `{:.4g}` an exact
  5.0005 GHz point renders `5 GHz`), the scalar line keeps its historical
  `{:.4g}`, and `marker_freq_text` widens BOTH numbers to `FREQ_WIDE_FMT`
  itself the moment there are two to tell apart — so a marker that IS a data
  point renders byte-for-byte what it always did at both sites. Fixing it moved
  **55 lines of `tests/fixtures/cli_reference/`, every one of them a `@ <freq>`
  marker line** — no number, no table, no exit code and no CSV cell — and
  `render_reference.json` and `golden_legacy.npz` did not move at all, which is
  the shape the change had to have: the GUI was already right.
- **FIXED — the CLI's pair list is RANKED and FLOORED, and it prints the rank
  key.** It was `for pr in res.pairs:` — nested-loop `(a, b)` order, every pair,
  and `worst M/L`, which IS the rank key, printed **nowhere at all**. That last
  part was the real defect: six measurement ports make 15 pairs, index order
  says nothing about which of them matter, and `worst M/L` is the quantity a
  spur / pulling budget is written against, so on a headless box there was no
  way to get it. `_print_coupling_report` now CALLS
  `pkg_rlc.present.report.rank_coupling_pairs` — not a second implementation of
  the key, because its three rules are exactly the ones a copy gets subtly
  wrong: the strength computed **linearly** (`_ratio_db(0)` is NaN and a pair
  with `M = 0` is the weakest there is, not an undefined one), an **undefined**
  ratio sorting LAST and never folded away, and the **strongest** pair never
  folded away either. `COUPLING_FLOOR_DB = -60` applies, the folded count is
  printed, and it points at `--csv` — a pointer that stays TRUE only because
  `_write_coupling_csv` enumerates every unordered pair straight off the Z
  matrix and has no floor. **Do not give it one.** `worst M/L` goes on the
  pair's HEADLINE, beside the flag, because a fifteen-pair report is scanned by
  its headlines; the pane learned that the hard way when the same number was
  moved off its headline during the results-views slimming and a test caught it
  inside the hour. The heading says `strongest first by worst-case M/L` only
  when there is more than one pair — ranking one pair means nothing, the same
  omission the pane makes.
- **FIXED — reciprocity now leads with the VERDICT on the CLI too, and keeps
  the metric and the paragraph UNDER it.** The pane prints
  `✓ reciprocal (5.76e-15)` and nothing else; the CLI printed
  `Reciprocity error = 5.76e-15   (max|Z_ab − Z_ba| / max|Z_ab| …)` plus a
  paragraph and **never said the word**. It is a SUPERSET now, not a copy:
  `Reciprocity: OK -- reciprocal (…)` / `Reciprocity: WARN -- Z_ab and Z_ba
  disagree (…)` / `Reciprocity: NOT CHECKED -- every mutual term is undefined`
  as the headline, then the metric line and the paragraph exactly as before.
  **The paragraph was NOT deleted**: the pane dropped it to a measured
  144-column budget a terminal does not have, and a headless reader has no
  other source for what the metric means. **No tick glyph** — nothing in the
  143 pinned CLI cases uses one (the non-ASCII in that reference is `Ω Δ ω Σ √`
  and nothing else), the CLI already says `WARN:` in words, and `✓` is 12 px
  against 7 in the pane's own font, which is why the pane confines it to
  standalone sentences. `reciprocity_verdict` in `pkg_rlc/present/report.py` is
  the ONE classifier both surfaces call, so which of the three readings a given
  number gets cannot differ between them; the alarm keeps its whole sentence on
  both, because there the sentence IS the reading.
- **FIXED — `_pair_flag` reaches the CLI, and the legend is ONE block at the
  foot of the report.** The missing `|k|>1` prompt was the loss that mattered:
  `|k| > 1` means the port setup is probably wrong, core's rule is that it adds
  a note rather than clamping, and with `_pair_flag` pane-only a CLI user was
  told **nowhere**. The pane's own `_pair_flag` is called and its `[ind]` /
  `[cap]` / `[|k|>1]` goes on the pair headline. The legend was two fragments
  in two places — the sign key under the self table, the M/L caveat under the
  pairs — and is now one block printed once at the foot, the shape the pane
  settled on with `COUPLING_LEGEND_LINES`. It is **not** that constant and must
  not become it: the CLI keeps its own longer wording, because length is not a
  defect on a surface with no column budget. The M/L caveat is one of the six
  homes of that sentence and it moved POSITION, not a character.

**What is left of `_print_coupling_report` is genuinely two products, and
merging it into `_format_coupling_block` is still not a parameterisation job.**
The CLI has no `units_mode`, does not do the pane's `G <= 2` matrix fold, and
deliberately prints a metric, a definition and a paragraph the pane's
144-column budget refuses. What the two now SHARE is the arithmetic and the
classification — `rank_coupling_pairs`, `_pair_strength_db`, `_pair_flag`,
`reciprocity_verdict`, `_format_z_matrix`, `_trunc_str`, `_render_columns` —
which is the rule (share the computation, not the rendering) and the whole of
what was ever at risk of disagreeing about a number.

### The run module (`pkg_rlc/services/run.py`) — the SOLVE landed, the ORCHESTRATION did not

`pkg_rlc.services.run` exists and is L2. It holds what a Calculate RUNS: the network a
trace is solved against, the spec it is solved with, the reference-node check
and the coupling reduction. What it does NOT hold is `App._on_calculate`'s own
body, and that is a measured decision rather than an unfinished one.

- **Three App couplings are INJECTED, and they are the whole seam.** `log`
  (what `_append_result` does), `files` (in place of `_file_by_label`) and
  `cache` (the composed stack). `log` is not optional politeness: the Schur /
  lstsq / one-bad-frequency-NaN guard NaNs one frequency and says so in a
  warning, and a caller that swallowed that line would leave a plausible 0 H on
  screen with nothing to explain it. `cache` is passed in rather than owned
  because `comp.compose` is 100 ms at 76 ports and **10.5 SECONDS** at 169, and
  the editor strips call in once per keystroke.
- **`_migrate_trace` did not come, and `run._build_termination` does not call
  it.** Folding a retired spec forward LOGS a line and REFRESHES THE TRACES
  LIST, so it is an App action, not arithmetic. `App._build_termination`
  migrates and then calls the module one — the order it always had — so every
  caller, including `pkg_rlc.panels.attrib_gui`'s `app._build_termination(...)`, is
  unchanged. Do not "tidy" the migration back down; it would put a Tk refresh
  at L2.
- **THE FOUR PLOT-CURVE HELPERS CAN NEVER GO TO L2**, and this is the third
  finding of the earlier refusal, which still stands. `_make_plot_trace`,
  `_compose_curve_label`, `_plot_trace_label` and `_coupling_plot_traces` are
  defined by `PlotTrace`, `COLORS`, `LINESTYLES` and `MAX_LABEL_LEN`, all L4. A
  curve is a drawing instruction, not a measurement.
- **WHY `_on_calculate`'s BODY STAYED, measured rather than argued.** After the
  solve moved out, the body's remaining couplings above L2 are exactly five,
  and every one of them is presentation or an App action: `marker_freq_text`
  (L3 — it takes a format string and returns the `=== Calculate @ … ===`
  sentence, and `pkg_rlc.model.trace` left it in `pkg_rlc.present.report` for that reason);
  `describe_run_change` (L3 — the run-to-run diff, stored RENDERED on the
  snapshot); `reference_provenance` (L5, reached through the `_snapshot_row` /
  `_snapshot_block` wrappers); `UNFREEZE_MENU_LABEL` (L5 — a menu label, which
  belongs with the menu it labels); and `_migrate_trace` (the App action
  above). Moving the body would mean a `calculate()` taking **three injected
  callables, one injected string, and leaving the header line and the diff to
  its caller** — i.e. the report's ORDER split across two modules, which is the
  "two things that can come to disagree" failure this repo names everywhere
  else, arriving inside the fix for it.
- **So the split is: `pkg_rlc.services.run` answers "what is the number", `_on_calculate`
  answers "what does the reader see, in what order, at what severity".** The
  second is frontend work. If a later session moves it anyway, the honest
  version is to move `marker_freq_text` and `describe_run_change` DOWN first
  with a written reason — not to inject six things.
- **P3A's PREREQUISITE IS DONE** and both L2 modules now exist.
  `pkg_rlc.model.trace` (L1) carries `TraceConfig` (+ its three `migrate_legacy_*`),
  `FileEntry`, `SolveNetwork` / `_composed_solve_network`,
  `_duplicate_trace_config`, `_config_signature` / `_draw_signature`,
  `trace_signature_fields` / `run_signatures`, the frequency snap, the whole
  run record, the three `LOG_*` severities and the four `VIEW_*` /
  `RESULTS_VIEWS` names.


### How the run record got its home — READ THIS BEFORE MOVING ANY OF IT

The four snapshot types and their builders were the last thing in `pkg_rlc.frontend.app`
that every layer below had to reach UP for. They are in `pkg_rlc.model.trace` now.
Two things blocked the move and each was resolved rather than worked around;
both are about what a snapshot STORES, not about where the class file sits, so
they are the two places a later session will be tempted to undo it.

- **`FreqSnap` MOVED DOWN, and that is the answer to "is it a fact or a
  rendering".** `CouplingSnapshot.freq` and `RunSnapshot.freqs` store one, so
  while it sat in `pkg_rlc.present.report` (L3) the snapshot types could not go below
  L3 — a model type would have had a field whose type lives two layers above
  it. It is a FACT ABOUT THE MEASUREMENT: `requested_hz`, `actual_hz`,
  `delta_hz`, `exact`, `off_grid` and `agreed` say where a value was read and
  how far that is from what was asked for, and nothing at all about how to
  print it. So `FreqSnap`, `freq_grid_step`, `snap_to_grid`,
  `combine_freq_snaps` and the three `FREQ_EXACT_*` / `FREQ_UNIFORM_TOL`
  tolerances are `pkg_rlc.model.trace`'s. **`marker_freq_text` did NOT go with them**
  and the split is the deliberate one the old note warned against making
  carelessly: it takes a FORMAT STRING and returns a sentence, every one of its
  callers is a surface, and nothing in the model needs it — so it and its
  `FREQ_WIDE_FMT` stayed in `pkg_rlc.present.report`. `snap_to_grid` builds the record
  and `marker_freq_text` prints it; they are on opposite sides of that call and
  they are now on opposite sides of the layer line for the same reason. All
  four moved names are re-exported from `pkg_rlc.present.report`, so
  `pkg_rlc_extractor`, `pkg_rlc.frontend.app`, `pkg_rlc.panels.panels_results` and
  `tests/test_freq_label.py` are untouched.
- **`reference_provenance` is INJECTED, which was option (ii) of the two the
  earlier brief named.** `_snapshot_reference` freezes `ref_strip` /
  `ref_warn` / `ref_lines` onto every row by calling it, and it lives in
  `pkg_rlc.panels.files_gui` (L5) — presentation, and it stays there, because
  rendering a composition's verdict for a reader belongs beside the window that
  shows it. Rendering it ONCE at snapshot time is R3-5 and is not negotiable
  (two copies of one verdict are two things that can disagree), so the call
  could not be dropped either. So the three builders take a `provenance=`
  keyword — a CALLABLE, so nothing at L1 names an L5 module — the model stores
  the text it is handed, and `pkg_rlc.frontend.app` keeps three wrappers of the same
  names that supply `reference_provenance`. **This one signature is the whole
  of the exception to the pure-move rule**, and it is what let all ~10 call
  sites (six in `App`, two in `pkg_rlc.panels.panels_traces` through the
  `staticmethod` aliases, and the tests) stay exactly as they were.
  `from pkg_rlc.frontend.app import _snapshot_row` still resolves, and still resolves to
  the WRAPPER — the version every caller in the repo has always had. With no
  renderer supplied the three fields come back empty, which is the same answer
  a single-file trace already got.
- **`_row_file_labels` and `_snapshot_file_legend` were on the move list and
  stayed in `pkg_rlc.present.report` on purpose.** `_snapshot_file_legend` returns
  `files: F1=die.s6p + F2=pkg.s4p` — a display string — and `_row_file_labels`
  exists to feed it and the two file-column helpers beside it. Nothing outside
  `pkg_rlc.present.report` calls either. Moving them would have put presentation into
  the model, against this file's own rule that *a rendering of the data model
  is `pkg_rlc.present.report`'s*. Both are still re-exported from `pkg_rlc.frontend.app`.
- **`tests/fixtures/render_reference.json` was the guard and it was NOT
  regenerated.** It pins the rendered page byte-for-byte, and a single-file
  trace's snapshot carries no reference text and no file list — which is
  exactly what makes it byte-identical across this move.
- **The plot-curve helpers can NEVER be part of `pkg_rlc.services.run`.**
  `_make_plot_trace`, `_compose_curve_label`, `_plot_trace_label` and
  `_coupling_plot_traces` are defined by `PlotTrace` / `COLORS` / `LINESTYLES` /
  `MAX_LABEL_LEN` (L4) and `_coupling_k_array` (L3). They belong beside the
  palettes at L4/L5. This is the `StylePicker` wall from the other side — that
  one stayed in `pkg_rlc.frontend.app` because `COLORS` lives ABOVE it, and the same
  measurement decides these four. Split the list before re-issuing the phase:
  solve + snapshot to L2, curve building to L4/L5.
- **Two App couplings that `log=` does not cover, and that a re-plan must
  decide first.** `_build_termination` opens with `self._migrate_trace(tc)`,
  which logs at `LOG_WARN` *and* calls `self._refresh_trace_list()` — building a
  termination has a GUI-visible side effect on every call. And
  `_calculate_coupling_trace` / `_reference_checks` classify their own
  severities, including the deliberate `LOG_INFO` on the rank-deficiency
  annotation (it exists to say the warning above it is not a fault, so badging
  it would contradict it). Re-deriving those in the wrapper from the message
  text is the drift this repo is repeatedly bitten by; the severities travel
  with the code or the phase does not happen.
- **A partial extraction was considered and rejected.** What survives the L2
  constraint is five leaf helpers — `_trace_network`, `_cached_trace_network`,
  `_trace_namespace`, `_collect_mports`, `_trace_plot_freqs` — and none of the
  calculate body. That is a module named `pkg_rlc.services.run` containing no run logic,
  squatting on the L2 slot with the wrong contents, while the two front ends
  can still compute differently. Nothing is better.
