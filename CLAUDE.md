# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

**The per-area rules are in `docs/conventions/`, and they are as binding as the
ones in this file.** What is HERE is what applies to a change ANYWHERE: the
layer map, the module map, the cross-cutting invariants, the import gate, the
bit-exactness rules `golden_legacy.npz` pins, and the rejected-proposal list.
The deep account of one window, one panel, one report moved out on 2026-08-31,
verbatim, when this file passed the 150k characters a session can hold — the
pointer table is under "The rest of the rules live in `docs/conventions/`"
below and the index is `docs/conventions/README.md`. **Read the one that covers
the area you are about to touch, BEFORE you touch it.**

**How the tree got its current shape:** `docs/REFACTOR_REPORT.md` is the account
of the 2026-08-13 layering refactor — the before/after line counts, the places
the CLI and the GUI told users different things about the same data (one fixed,
six open), and what is still owed. Read it before starting anything that moves a
symbol between modules; the sections below are the rules, that file is the state.
**Two things in it are out of date, deliberately — it is an account of that
night, not a live document.** `pkg_rlc.model.trace` is no longer "reverted,
recoverable from history": the three reverts were reverted and the module was
finished, so it holds the data model, the frequency snap and the whole run
record. `pkg_rlc.services.session` and `pkg_rlc.services.run` are still absent, and only
`pkg_rlc.services.run` is still blocked — on the L3/L4 symbols listed under "The run
module" in `docs/conventions/architecture.md`, not on the model.

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

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/physics/core.py`       | **A FACADE, 169 lines.** Re-exports every name of `pkg_rlc.physics.touchstone`, `pkg_rlc.physics.spec` and `pkg_rlc.physics.solve`, so `from pkg_rlc.physics.core import <anything>` means what it always meant and no caller, no test and no fixture changed when the split landed. It is still the module to import from unless you are inside one of the three. Two things about it are not decoration: the re-export lists are written out BY NAME rather than `import *`, because star skips underscore names and `pkg_rlc.frontend.app` writes `from pkg_rlc.physics.core import _collect_nets`, `pkg_rlc.physics.attrib` uses `_normalize_signal`, and the parser tests reach for `_sniff_nports` / `_strictly_increasing` / `_max_possible_nports` / `_MONO_CHUNK`; and the module is a `types.ModuleType` SUBCLASS whose `__setattr__` / `__delattr__` write through to whichever split module owns the name — see the invariant below. |
| `pkg_rlc/physics/touchstone.py` | **Reading a file and saying what is wrong with it.** The universal content-based parser, the encoding sniffer, the port-count sniffer, `TouchstoneParseError` / `diagnose_touchstone` / `check_touchstone` / `_diagnose` / `_safe_diagnose`, `_decode_options`, `_recover_data_line`, the descriptive checks (`_check_freq_axis` / `_check_s_values`), `TouchstoneData`, the `FAULT_*` verdicts, `MAX_SNIFF_NPORTS` / `SNIFF_HARD_CAP`, `DEFAULT_Z0`, `FREQ_UNIT_SCALE`, and `_freq_batch` with `COMPUTE_BATCH` / `COMPUTE_CHUNK_BYTES`. Imports NOTHING from this repo — it is the bottom of L0. That is also why `format_si` / `format_freq` / `_SI_PREFIXES` sit here rather than with the arithmetic; see the invariant below. |
| `pkg_rlc/physics/spec.py`       | **What the user DECLARED.** `TerminationSet` and the `PortTermination` / `Coupling` unions, the lumped-admittance helpers (`y_resistor` … `y_series_rlc`, `YFunc`), `parse_port_range` / `parse_short_pairs` / `parse_mport_spec` / `parse_si` / `parse_kv_rlc_params` / `SI_SUFFIXES`, `resolve_meas_ports` / `MeasPort` / `_normalize_signal`, every `build_terminations_mode*` and `build_terminations_coupling`, the named merged nodes (`_collect_nets`, `NetDef`, `MergedNode`, `merged_nodes`, `validate_net_name`), the Mode 5 DSL (`parse_custom_termination_text`), the connection-table row model (`MeasPortRow` / `ConnectionRow` / `rows_to_dsl_text` / `dsl_text_to_rows` / `build_terminations_rows`), the port roles (`port_roles` / `PortRole` / `ROLE_*` / `row_sources` / `collapse_ports` / `name_prefix` / `open_name_clusters`), and the three "your spec does not do what it looks like" checks (`inert_lumped_messages`, `parallel_stamp_messages`, `open_port_name_messages`). Nothing here computes a number from a network. Imports `format_si` from `pkg_rlc.physics.touchstone` and nothing else from the repo. |
| `pkg_rlc/physics/solve.py`      | **The arithmetic.** `s_to_y` / `y_to_s`, `compute_z_matrix` (and its nested `merge_terms`) / `compute_z` / `_probe_impedance` / `_is_singular_2x2`, the four Schur/probe warning builders, `extract_rlc_at_freq` / `extract_coupling_at_freq` and their result dataclasses, `fit_inductor` / `fit_capacitor` / `fit_auto` / `_scaled_lstsq` / the `eval_*_model` helpers, and `PINV_RCOND` / `PROBE_RANGE_TOL` / `SCHUR_COLLAPSE_TOL` / `SCHUR_LSTSQ_RCOND` / `RECIPROCITY_WARN`. Imports `pkg_rlc.physics.spec` and (`DEFAULT_Z0`, `_freq_batch`) from `pkg_rlc.physics.touchstone`; nothing imports it back. **Every bit-exactness rule in "Measurement ports / coupling (Mode 6)" below applies to THIS file now** — the `G == 1` verbatim branches, the `np.add.at` merge, the per-frequency 5f contraction. |
| `pkg_rlc/physics/attrib.py`     | **Port attribution.** Given `Y(f)` and a `TerminationSet`, answers three questions at one frequency. (a) An EXACT additive signed decomposition of `Z_ab` into the bare EM coupling plus one term per declared termination (`build_context` / `decompose` / `format_decomposition`). (b) The EXACT what-if of changing any of them (`sensitivity`, `group_joint`, `cumulative_curve`, `leave_one_out`, `sweep_mobius`, `transfer_ratio`). (c) The **cold-start screen** — which ports matter BEFORE a spec exists, from all-open: `cold_start_context` / `cold_start_bracket` / `cold_start_screen` / `cold_start_pairs` / `cold_start_leave_one_out` / `cold_start_cumulative` / `name_family_suggestions` / `cold_start_negative_result` / `cold_start_report` / `format_cold_start`, with `Bracket` / `PortScreenRow` / `PairEffect` / `FamilySuggestion` / `ColdStartContext` / `ColdStart` and the `COLD_START_*` constants. Plus `Element` / `Term` / `ReturnBudget` / `Decomposition` / `AttribContext`, the `DECOMPOSABLE` / `NON_DECOMPOSABLE` registries, the `Alternative` builders, `termination_impedance_diagonal` / `termination_impedance_shared_return`, `SIGN_CONVENTION_TEXT` and `AttribError`. Plus the **composed-network gauge** — `COMPOSED_BASELINE_TEXT`, `PortBlocks`, `BaselineLinks`, the `baseline=` argument every entry point takes, and `_island_elements`, the ungated structural warning for an element whose whole support sits in a probe-free component of the baseline. Imports `pkg_rlc.physics.core` ONLY (acyclic, the `pkg_rlc.widgets.plot` rule), no scipy. `pkg_rlc_extractor.py` drives it from the `--attribute*` and `--cold-start*` flag groups (`--mode coupling` only); the GUI surface for (a) and (b) is `pkg_rlc/panels/attrib_gui.py`, and (c) is CLI-only. |
| `pkg_rlc/physics/compose.py`    | **Several Touchstone files measured as ONE network.** `ComposeInput` / `compose` / `ComposedNetwork` / `FileBlock` stack k files into one `Y` on a common frequency axis; every cross-file link is an ordinary `ShortPair` / `LumpedBetween` on the global indices handed to the SAME `compute_z_matrix`, so every mode, the Mode 5 DSL and the coupling path work on a composition without a line of their own. Plus the mandatory reference-node check (`reference_check`, `REF_LIVE` / `REF_WELDED` / `REF_NO_GROUND` / `REF_UNKNOWN`), the frequency plan (`align_frequencies` / `FreqPlan` / `interpolate_s`), the namespace (`COMPOSE_TAG_SEP`, `parse_scoped_ports`, `format_scoped_port`, `default_alias`, `link_short` / `link_lumped`), the pre-reduction (`reduce_block_y`), the Touchstone writer (`write_composed_touchstone`), the limit case (`limit_case_check`), `solve_composed` and `ComposeError`. Imports `pkg_rlc.physics.core` ONLY. |

### L1 — `pkg_rlc/model/` (the shared data model, and the spec logic over it)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/model/trace.py`      | **The shared data model every layer above passes around** (L1): `FileEntry`; `TraceConfig` with `migrate_legacy_mode` / `migrate_legacy_mports` / `migrate_legacy_custom_text`; `SolveNetwork` / `_composed_solve_network`; `_duplicate_trace_config`; `_config_signature` / `_draw_signature`; `_SIGNATURE_FIELDS` / `trace_signature_fields` / `run_signatures`; the **frequency snap** (`FreqSnap`, `freq_grid_step`, `snap_to_grid`, `combine_freq_snaps` and the `FREQ_EXACT_FRAC` / `FREQ_EXACT_REL` / `FREQ_UNIFORM_TOL` tolerances — where a measurement LANDED is a fact about the measurement, and `marker_freq_text`, which is the rendering of it, stayed in `pkg_rlc.present.report`); and the whole **run record** (`RowSnapshot` / `CouplingSnapshot` / `FitSnapshot` / `RunSnapshot`, `_snapshot_files`, `_snapshot_reference` / `_snapshot_row` / `_snapshot_block` / `_snapshot_fit`), which sits here because a record of one Calculate is the data model with the live trace resolved out of it. The three builders take a `provenance=` CALLABLE so the L5 reference-node render can be injected without this file naming an L5 module — see "How the run record got its home" in `docs/conventions/architecture.md`. It exists because all of that used to sit in the same file as the Tk `App`, so every panel and every window that needed the data model had to reach UP into the frontend — and the only way to do that without a cycle was an `import pkg_rlc.frontend.app` inside a function body. **There were ten; nine are gone and this module is why.** Imports `pkg_rlc.physics.core` and `pkg_rlc.model.validate` and nothing else: no Tk, no matplotlib, no `App`. A COLOUR is not part of the data model and lives in `pkg_rlc.widgets.widgets`; a RENDERING of it is `pkg_rlc.present.report`'s. Everything here is RE-EXPORTED from `pkg_rlc.frontend.app`, so `pkg_rlc.frontend.app.TraceConfig` and friends keep resolving for every call site and all 45 test modules. |
| `pkg_rlc/model/validate.py`   | **What a spec SAYS, what it will DO, and what is wrong with it** (L1, moved down from L2 when `pkg_rlc.model.trace` landed — `TraceConfig.port_descriptor()` is one call into it, `info_str()` counts the file set through it, all three legacy migrations use it and `_config_signature` ends on it, so the model imports this and this therefore has to sit at or below the model; it does so honestly, importing only L0 and duck-typing the trace rather than importing it, which is the same two-peers-in-one-layer case as `pkg_rlc.physics.attrib` -> `pkg_rlc.physics.core`). The file set and the composed port namespace (`trace_file_labels` / `trace_file_aliases` / `trace_is_composed` / `trace_file_legend` / `trace_file_scope`, `compose_spec_problems`, `ComposeSpecError`, `_scope_port_field` / `_scope_dsl_text` / `_scope_conn_rows` / `_scope_mport_rows`, `_field_has_tag`, `scope_echo_messages`, `_collect_nets_safe`, `_check_bare_ports`, `_namespace_network`); what the spec says (`_port_descriptor` and the `_fmt_*` renderers under it, `_port_overview_text` / `_bucket_counts`, `_import_dsl_text` / `_dsl_meaning` / `_ordering_diff_summary`, `_scan_count`); and what is wrong with it (`_VMsg`, the `V_WRONG_NUMBER` / `V_NO_RESULT` / `V_ROW_INERT` / `V_OK` tiers, `_validation_report` / `_validation_messages`, `_measured_port_messages`, `_probe_ground_messages`, `_rlc_echo`, `_extra_lines_indicator`, `_trace_role_rows`, `_role_warnings` and the `WARN_*` texts, `_roles_header`, `_append_port_spec`, `_validation_strip_text`, `_footer_strip_text`). Imports `pkg_rlc.physics.core` and `pkg_rlc.physics.compose` ONLY. **Every entry point is pure and NONE may raise** — the editor strips call them from inside Tk variable traces, once per keystroke, where a raised exception reaches no handler we control and the GUI carries on showing a stale verdict. A `TraceConfig` is read by duck typing and never imported. |

### L2 — `pkg_rlc/services/` (services over the model)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/services/session.py`   | **The session file** (L2): Save Config, Load Config and the on-exit autosave as a pure dict <-> model round trip — `SESSION_FORMAT` / `SESSION_VERSION` / `SESSION_FILETYPES`, `SessionError`, `LoadedSession`, `session_to_dict` / `session_from_dict`, `trace_to_dict` / `trace_from_dict`, `_config_trace_fields`, `_file_ref` / `resolve_session_file`, `autosave_path`, the four coercers (`_coerce_bool`, `_rows_from_list`, `_strings_from_list`) and the five field sets (`_COMPUTED_TRACE_FIELDS`, `_LEGACY_TRACE_FIELDS`, `_OPTIONAL_TRACE_FIELDS`, `_TRACE_*`, `_CONTROL_KEYS` / `_CONTROL_CHOICES`). It is a SERVICE OVER THE MODEL — it takes the `FileEntry` and `TraceConfig` lists and returns a dict, or takes a dict and returns them — and there is no Tk in it and never was, which is what has always made the round trip testable with no display. What stayed in `pkg_rlc.frontend.app` is the half that needs the App: the dialogs, `_session_dict` (which flushes the editor first, the Calculate rule), `_load_session_file`, `_apply_session` and the autosave on `WM_DELETE_WINDOW`. Imports `pkg_rlc.physics.core` and `pkg_rlc.model.trace` only. Everything is re-exported from `pkg_rlc.frontend.app`, including the private names, because tests reach for them by module attribute (`pkg_rlc.frontend.app._TRACE_STRLIST_FIELDS`) and that is what pins the field classification. |
| `pkg_rlc/services/run.py`       | **What a Calculate actually RUNS** (L2): the network a trace is solved against (`_trace_network`, `_cached_trace_network`, `_trace_namespace`, `_file_by_label`), the spec it is solved with (`_collect_mports`, `_build_termination`), and the checks and reductions over both (`_reference_checks`, `_calculate_coupling_trace`, `_trace_plot_freqs`, `_empty_run`). No Tk. Three App couplings are INJECTED rather than reached for: `log` (what `App._append_result` does — the Schur / lstsq / one-bad-frequency-NaN warning has to reach the reader, and a caller that swallowed it would leave a plausible 0 H on screen), `files` (in place of `App._file_by_label`) and `cache` (the composed stack, which stays the App's because `comp.compose` is 10.5 s at 169 ports and the strips call in once per keystroke). **`_migrate_trace` is deliberately NOT here and `run._build_termination` does not call it** — folding a retired spec forward logs a line and refreshes the Traces list, so it is an App action; `App._build_termination` migrates and then calls this one, in the order it always did. **The four plot-curve helpers are not here either** (`_make_plot_trace`, `_compose_curve_label`, `_plot_trace_label`, `_coupling_plot_traces`): they are defined by `PlotTrace`, `COLORS`, `LINESTYLES` and `MAX_LABEL_LEN`, which are L4, and a curve is a drawing instruction rather than a measurement. **`_on_calculate`'s own body is not here either — see "Why the Calculate ORCHESTRATION stayed in the App" in `docs/conventions/architecture.md`.** Re-exported from `pkg_rlc.frontend.app`, where most of these survive as one-line methods. |

### L3 — `pkg_rlc/present/` (turning a result into text)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/present/report.py`     | **Turning a finished run into TEXT** (L3). The three results views and every formatter under them (`_format_results_table`, `_format_coupling_block`, `_format_summary_self` / `_format_summary_coupling`, `_format_compare` and `_wrap_name` / `_compare_head_cells` / `_compare_groups` / `_delta_cell`, `_render_columns`, `_value_formatter` / `_fmt_plain` / `_fmt_aligned` / `_aligned_prefix_for`, `_sign_flag`, `_trunc_str`, `_file_alias_map` / `_file_cell` / `_row_file_labels` / `_snapshot_file_legend`, `_format_z_matrix`, `rank_coupling_pairs` and `COUPLING_FLOOR_DB` / `COUPLING_LEGEND_LINES`), the tab labels and the run-to-run diff (`log_tab_label`, `run_tab_label`, `run_headline`, `run_stale_banner`, `keep_button_label`, `describe_run_change`, `run_change_line`, the `LOG_*` and `RUN_*` constants), the view and width budgets (`RESULTS_VIEWS` / `VIEW_*`, `RESULTS_PANE_COLS`, `COMPARE_*`, `SUMMARY_LABEL_MAX`, `RESULTS_SWATCH`), the **digit count** every formatter here takes as `sig` (`DIGITS_DEFAULT` / `RESULTS_DIGITS` / `digits_sig`, `pkg_rlc.model.trace`'s and re-exported here for the reason `RESULTS_VIEWS` is — `sig=None` is `default` and is what keeps `render_reference.json` byte-for-byte; see "The Digits control" in `docs/conventions/results_pane.md`), and the **frequency provenance** the reports print through — `marker_freq_text` (THE renderer: it takes a format string and returns a sentence), `FREQ_WIDE_FMT`, `_table_freq_note` and `run_freq_snap` / `run_file_freq`. **`FreqSnap` and the three functions that BUILD one (`freq_grid_step` / `snap_to_grid` / `combine_freq_snaps`, with their tolerances) are `pkg_rlc.model.trace`'s** — where a measurement landed is a fact about it, not a rendering of it, and a run record holds one — and are re-exported from here, so `from pkg_rlc.present.report import FreqSnap` keeps resolving. Imports `pkg_rlc.physics.core` and `pkg_rlc.model.trace` only. **This module is what `tests/fixtures/render_reference.json` pins byte-for-byte**, which is exactly why it must stay Tk-free: a formatter that reaches a widget cannot be captured with no display. |
| `pkg_rlc/present/csv.py`        | **The CSV export blocks** (L3): `_write_coupling_csv`, `write_coupling_table` and `_coupling_k_array`. Beside `pkg_rlc.present.report` rather than inside it because the two answer different questions — the results pane is a measured 144-column budget with one SI prefix per column, the CSV is every value at full precision under a header a spreadsheet can read. **`write_coupling_table` is the ONE copy of the coupling table** and both front ends write their own comment block above it: the GUI names the measurement ports, the CLI names the file, the port map and the spec that was run. `pkg_rlc_extractor` used to carry a second, independent implementation of the same table under the same name; it is gone. |
| `pkg_rlc/present/attrib_report.py` | **The attribution report as TEXT.** The thirteen `_attr_print_*` / `_cold_print_*` sections `pkg_rlc_extractor.py` used to print, moved whole and RETURNING `list[str]`; the CLI's `_emit` is the only thing that prints them again, which is what lets `tests/fixtures/cli_reference/` pin the surface with no stream to capture. Also the CLI's own SPELLING of the shared formatters (`_trunc`'s `~` rather than U+2026, `_fmt_complex`'s `a + jb` rather than the pane's `a+bj`, `_table_lines`, `_attr_wrap`, `_attr_section`), the ranked-section caps (`ATTR_RANK_ROWS` / `ATTR_SWEEP_GROUPS` / `ATTR_PAIR_POOL` / `ATTR_GROUP_ROWS` / `COLD_RANK_ROWS` / `COLD_PAIR_ROWS` / `COLD_MIRROR_ROWS` / `COLD_QUANTITY`), the two CSV RECORD shapers (`_ATTR_CSV_FIELDS` / `_attr_row` / `_COLD_CSV_FIELDS` / `_cold_row` — a record is data shaping; the WRITERS stay in the CLI with the path and the flags), and the three things the CLI and the Attribution window genuinely share: the ground-model grammar (`_attr_series_impedance` / `_attr_alternative` / `_attr_ground_model` / `_attr_zt`), the grid snap (`_attr_snap`) and `rank_map`. Imports `pkg_rlc.physics.attrib`, `pkg_rlc.physics.core` and `pkg_rlc.present.report` and nothing else — **no tkinter and no matplotlib**, because this module is on the CLI's import path and `test_attrib_cli*` are in `FAST_MODULES` on exactly that property. `pkg_rlc.frontend.cli` re-exports every symbol it lost — **the root `pkg_rlc_extractor.py` shim does NOT**, because it re-exports by `import *` and star skips underscore names; reach `_attr_zt` and friends through `pkg_rlc.frontend.cli`, which is where they live. |
| `pkg_rlc/present/conntable.py`  | **The connections table's SHAPE, and the RowTable vocabulary it is spoken in** (L3): `conn_table_layout` / `_conn_row_cells`, `CONN_TABLE_COLUMNS` and its measured column budget, `_join_short_group` / `conn_cells_from_row` / `conn_row_from_cells`, `CONN_ON_GLYPH` / `CONN_OFF_GLYPH`, `CONN_NET_KEY` / `CONN_NET_SUPPORTED`, the `_CONN_COL_*` grid columns, `CONN_KIND_HINTS` / `conn_hint_text` / `CONN_TABLE_HINT*` / `HINT_SHORT_CHARS` — **and `ColumnSpec` / `TableLayout` / `identity_layout`**. Those three are here rather than beside `RowTable` because they are the INTERFACE between the layout rules at this layer and the widget one layer above, and the layer map puts this module BELOW `pkg_rlc.widgets.widgets`: a shared type has to sit at the lower of its two users or the import runs upward. Imports `pkg_rlc.physics.core` only. |
| `pkg_rlc/present/help.py`       | In-app Help window content (`HELP_DIR`, `_help_text`, the ten `HELP_*` names, `HELP_TOPICS`, `HelpWindow`, `HELP_WINDOW_WIDTH`) — **140 lines: the 2648 lines of prose are ten plain-text files under `docs/help/`, read at import time.** See "The Help window's prose lives in `docs/help/`" in `docs/conventions/session_and_help.md`. One tab per mode + syntax + save/load + worked examples. **Ten tabs, and there is no room for an eleventh** — port attribution, the Attribution window and the cold-start screen all live at the bottom of `Mode 6 (Coupling)`, cross-referenced from `Overview`, `Input syntax` and `Worked examples`. See the measurement under "Port attribution" in `docs/conventions/attribution_core.md`. |

### L4 — `pkg_rlc/widgets/` (generic Tk widgets, which know nothing about this app)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/widgets/widgets.py`    | **The generic Tk widgets, which know nothing about this app** (L4): `PlaceholderEntry`, `PlaceholderText`, `PLACEHOLDER_FG`, `RowTable`, `_CollapsibleHint`, `_tk_dash`, `editor_scroll_fraction`, **`ReflowRow` / `reflow_rows`, which used to live in `pkg_rlc.widgets.plot`**, and **THE WHOLE PALETTE** — `PLACEHOLDER_FG` was always here and `WARN_FG` / `PORT_ROLE_FG` / the `_fixed_map_filter` Treeview workaround have joined it from `pkg_rlc.frontend.app`. One palette in one module was the point: while two of the three lived beside the `App`, a panel that wanted the warning colour had to reach UP into the frontend, and three of the ten lazy `import pkg_rlc.frontend.app` statements were nothing but a colour lookup. They are colours, not data, so they did NOT go down to `pkg_rlc.model.trace` with the trace — the layering test's own advice is that colour constants belong at L3/L4. All four are re-exported from `pkg_rlc.frontend.app`. It imports six names from `pkg_rlc.present.conntable`, seven `ROLE_*` names from `pkg_rlc.physics.core` (the keys `PORT_ROLE_FG` is built on — a second spelling of those is how a bucket comes to have no colour and be painted the default one in silence), and nothing else from this repo. `StylePicker` is deliberately NOT here — see the two invariants below. |
| `pkg_rlc/widgets/plot.py`       | Matplotlib plot panel: multi-subplot grid over R/L/C/\|Z\|/Re/Im/Q/**k**, draggable freq marker, M / V / Delete keys, fullscreen window, and the `ReflowRow` / `reflow_rows` control strip that wraps instead of losing its tail. Quantities that cannot be derived from one `(freqs, Z)` pair (today only `k`) arrive via the optional `Trace.aux` dict. **`ReflowRow` / `reflow_rows` no longer live here** — they are a generic layout widget that happened to land in this module because the control strip needed one first; they are now in `pkg_rlc.widgets.widgets` and RE-EXPORTED from here, so `from pkg_rlc.widgets.plot import ReflowRow` keeps resolving (`pkg_rlc.panels.attrib_gui` and `tests/test_plot_controls.py` both use that spelling). |

### L5 — `pkg_rlc/panels/` (app-specific windows and panels)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/panels/panels_files.py` | **The Loaded Files section** (L5): `FilesPanel` — the frame, its four buttons, the Listbox, the right-click menu, and `_load_one_file` / `_on_add_file` / `_on_remove_file` / `_on_check_file` / `_on_clear_files` / `_on_file_selected` / `_refresh_file_list`, and `CLEAR_FILES_MENU_LABEL`. |
| `pkg_rlc/panels/panels_traces.py` | **The Traces section** (L5): `TracesPanel` — the frame, its four buttons, the Listbox, the right-click menu, add / remove / duplicate / toggle / freeze / unfreeze / **clear all**, `_refresh_trace_list`, and `FREEZE_MENU_LABEL` / `UNFREEZE_MENU_LABEL` / `CLEAR_TRACES_MENU_LABEL`, which moved with the menu they label. |
| `pkg_rlc/panels/panels_results.py` | **The Results pane** (L5): `ResultsPanel` — the header strip (View / Units / **Digits** / Runs / Keep), the notebook, the Log tab and its badge, the run pages with keep / evict, `_clear_results` (the pane's half of `Clear All` — it is the ONE teardown that takes the KEPT pages too), both menus, `_run_report_segments` and the view builders under it, plus `RunTab` and `_tag_swatch_rows`. `_tag_swatch_rows` is the one results-pane renderer that is NOT a formatter — it WRITES INTO a Tk `Text` — which is why it never went to `pkg_rlc.present.report`. |
| `pkg_rlc/panels/panels_editor.py` | **The editor** (L5): `EditorPanel` — the pinned footer, the whole mode-aware form, both `RowTable`s, both scrollbars and `_apply_editor_scrollbars`, the scrollregion, `_update_mode_visibility`, the strips, the footer route, the text hatch, the auto-apply sync chain — plus `StylePicker` and the editor's own constants (`MODE_PLACEHOLDERS` / `LABEL_PLACEHOLDER` / `EDITOR_FIELD_CHARS` / `FROZEN_EDITOR_NOTE` / both `MP_TABLE_HINT`s / both `MUTUAL_CURVE_HINT`s / `TEXT_DIALOG_NOTE`). `FROZEN_EDITOR_NOTE` went with the EDITOR and not with its two former neighbours on the Traces menu: it names the editor's state, not a menu entry. |
| `pkg_rlc/panels/files_gui.py`  | **Which FILES a trace is made of** (round 3): the `Files in this trace…` window (`FilePairWindow`, `open_files_window`, `FILES_MENU_LABEL`, `files_refusal`, `refresh_files_windows`, `slots_of` / `FileSlot`, `spec_problems`), the port-cell scope rules (`render_port_cell` / `cell_scope` / `cell_is_foreign` / `port_choices` / `resolve_cell`, `ALIAS_MAX_CHARS`, `PORT_CELL_CHARS`) and the GUI rendering of the reference-node check (`reference_checks_of`, `reference_strip_text`, `reference_report_lines`, `reference_provenance`, `REFERENCE_HEADLINE`). Same split as `pkg_rlc.panels.attrib_gui` against `pkg_rlc.physics.attrib`: `pkg_rlc.physics.compose` does every piece of arithmetic and this is presentation, budget and refusal. Both `pkg_rlc.frontend.app` and `pkg_rlc.panels.attrib_gui` import it at module level, and **it imports `pkg_rlc.frontend.app` NOT AT ALL** — the eight lazy `import pkg_rlc.frontend.app` statements it used to carry inside function bodies are gone, replaced by ordinary top-of-file imports of `pkg_rlc.model.trace` / `pkg_rlc.model.validate` / `pkg_rlc.present.report` / `pkg_rlc.widgets.widgets`, all of which sit below it. Its own `trace_file_labels` still DELEGATES to the live definition (now `pkg_rlc.model.validate`'s, bound as `_live_trace_file_labels`) and still falls back to a local walk; `TestFileListsAgree` still pins the two against each other. |
| `pkg_rlc/panels/attrib_gui.py` | **The Attribution window** (`AttributionWindow`, `open_attribution_window`, `ATTRIB_MENU_LABEL`, `attribution_refusal`, `refresh_attribution_windows`, `attribution_session_state` / `apply_attribution_session_state`) plus the pure formatters it is testable through with no display (`render_table` / `Column` / `TableText`, `contributions_table`, `sensitivity_table`, `detail_lines`, `sweep_caption`, `reconciliation_verdict` / `reconciliation_line`, `provenance_lines`, `staleness_text`, `stability_line`, `report_text`, `csv_records`, `signed_str`, `parse_candidate`). A modeless `Toplevel` over `pkg_rlc.physics.attrib`; `pkg_rlc/frontend/app.py` holds only the Analyze-menu entry, the Traces right-click entry, the Results-pane pointer line and the refresh hooks. It is a separate module because `pkg_rlc/frontend/app.py` was already 7000+ lines; `pkg_rlc.frontend.app` imports it, and **it now imports NOTHING back**. The `_gui()` shim and its lazy `import pkg_rlc.frontend.app` are DELETED: `_config_signature` is `pkg_rlc.model.trace`'s, `_trace_role_rows` / the three port-field scopers / `trace_is_composed` are `pkg_rlc.model.validate`'s, `_value_formatter` and `LOG_WARN` are `pkg_rlc.present.report`'s and the palette is `pkg_rlc.widgets.widgets`', all of them below this file and all imported at the top. `spec_signature` survives as the one-line pass-through that keeps the ONE-definition promise visible. The earlier `pkg_rlc_extractor` pair went the same way when the ground-model grammar moved to `pkg_rlc.present.attrib_report`. **This module has no deferred imports left.** |

### L6 — `pkg_rlc/frontend/` (the App itself and the argv entry point)

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc/frontend/app.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` hints and the `RowTable` / `ColumnSpec` row editor (measurement ports in modes 5+6, connections in mode 5), the `StylePicker` colour/linestyle palette, auto-apply (`_schedule_editor_sync` / `_flush_editor_sync`), per-trace plot visibility (`_replot_from_cache`), the port-overview / validation strips, the "Edit as text…" hatch (`_import_dsl_text`, `_editor_dsl_text`), the frozen-trace snapshot (`_freeze_trace_config`, `freeze_label`, `freeze_refusal`, the Traces-list right-click menu), the File menu and the JSON session format (`session_to_dict` / `session_from_dict` / `SessionError` / `autosave_path`), the results pane (a `ttk.Notebook` whose tab 0 is the Log, with `log_tab_label` / `_append_result(severity)` / `_select_results_tab`), and the immutable run record (`RowSnapshot` / `CouplingSnapshot` / `FitSnapshot` / `RunSnapshot`, `_snapshot_row` / `_snapshot_block` / `_snapshot_fit`) that `_render_results` consumes instead of live traces, and the THREE RESULTS VIEWS (`RESULTS_VIEWS` / `VIEW_DETAIL` / `VIEW_SUMMARY` / `VIEW_COMPARE`, the `View:` combobox on the `ReflowRow` header, `_on_results_view_changed` / `_rerender_every_page`, and the pure formatters `_format_summary_self` / `_format_summary_coupling` / `_format_compare` / `_compare_groups` / `_delta_cell` / `_render_columns` / `_file_alias_map`) that `_run_report_segments` dispatches over, above the shared `_footer_segments`. Re-exports the DSL helpers it no longer defines. **Eight modules have been split out of it** — `pkg_rlc.present.conntable`, `pkg_rlc.widgets.widgets`, `pkg_rlc.present.report`, `pkg_rlc.present.csv`, `pkg_rlc.model.validate`, `pkg_rlc.model.trace`, `pkg_rlc.services.session` and `pkg_rlc.services.run`, and the layer map now describes no module that does not exist. `FileEntry`, `TraceConfig`, `SolveNetwork`, `_duplicate_trace_config`, the two signature functions **and the whole run record** (`RowSnapshot` / `CouplingSnapshot` / `FitSnapshot` / `RunSnapshot` and the `_snapshot_*` builders) are `pkg_rlc.model.trace`'s now, and `WARN_FG` / `PORT_ROLE_FG` / `_fixed_map_filter` are `pkg_rlc.widgets.widgets`'. What is still defined here of that group is THREE WRAPPERS — `_snapshot_reference` / `_snapshot_row` / `_snapshot_block` — which do nothing but supply `reference_provenance` to the model's builders, because that render is `pkg_rlc.panels.files_gui`'s at L5 and must happen exactly once, at snapshot time (see "How the run record got its home" in `docs/conventions/architecture.md`). `freeze_label` / `freeze_refusal` / `_freeze_trace_config` are still here too. Every moved symbol is RE-EXPORTED at the top of this file, the same precedent the Mode 5 DSL helpers set, so `from pkg_rlc.frontend.app import <anything>` keeps resolving for all 23 test modules that import it and every call site. **The four SECTIONS of the window have since gone too** — `pkg_rlc.panels.panels_files`, `pkg_rlc.panels.panels_traces`, `pkg_rlc.panels.panels_results`, `pkg_rlc.panels.panels_editor` — leaving `App` with `__init__`, the wheel router, `_build_ui`, the menubar, the PanedWindows, the session methods, Calculate, and the wiring between panels; the widgets are ALIASED back onto `App` and every moved method keeps a one-line delegator, which is the re-export rule one level down (see "The four panels of the main window" in `docs/conventions/architecture.md`). The file is 3220 lines, down from 10954. |
| `pkg_rlc/frontend/app.py` (cont.) | Plus the **Ports & Roles** window (`PortRolesWindow`, `_trace_role_rows`, `_role_warnings`, `_roles_header`, `apply_ports_as`), which is what `Show Ports` now opens; and the **Attribution hooks** — the `Analyze` cascade, the third Traces right-click entry, `_on_attribution`, the Results-pane pointer line, and the `refresh_attribution_windows` calls. The window itself is `pkg_rlc/panels/attrib_gui.py`. Plus the **multi-file schema and engine** (round 3): `TraceConfig.file_labels` and its helpers (`trace_file_labels` / `trace_file_aliases` / `trace_is_composed` / `trace_file_legend` / `trace_file_scope` / `compose_spec_problems`), the port-field scopers (`_scope_port_field` / `_scope_dsl_text` / `_scope_conn_rows` / `_scope_mport_rows`, `ComposeSpecError`), `SolveNetwork` / `_trace_network` / `_cached_trace_network` / `_namespace_network` / `_trace_namespace`, `_reference_checks`, `set_trace_home_file`, and the `Files in this trace…` entries on the Analyze cascade and on BOTH right-click menus. |
| `pkg_rlc/frontend/cli.py` (and the root `pkg_rlc_extractor.py` shim) | Entry point: dispatches GUI vs CLI from argv. CLI `--mode gnd \| p2p \| coupling`, `--mport` repeatable. **The attribution and cold-start report sections are no longer here** — they are `pkg_rlc.present.attrib_report`, returning `list[str]`, and what is left on this side is the argparser, the flag refusals, the CSV writers (path and flags), the drivers that decide the printed ORDER, and `_emit`, the one `print` on the path. Every moved symbol is re-exported. |

### Outside the package

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |

### `tests/` — one line each; the full account is `docs/conventions/test_suite_map.md`

**The prose that used to be here — what each file measured, which mutation it was
checked against, the numbers it pins — is `docs/conventions/test_suite_map.md`,
moved verbatim.** This table is the index into it: enough to answer "what do I
run after changing X", not enough to answer "why does that test exist".

| File | Guards |
|-------------------------|---------------------------------------------------------|
| `tests/run_parallel.py` | **THE runner.** Class-sharded, longest-first; `-m <substr>` picks modules by name. Not auto-discovered. |
| `tests/test_run_parallel.py` | The runner itself: the contention rule, the atomic registry, the heartbeat, the BelowNormal shard spawn. |
| `tests/test_layering.py` | The import-layering gate — folder IS the layer, acyclicity, `KNOWN_BACK_IMPORTS` in both directions. |
| `tests/test_golden_regression.py` | **The bit-exactness guard.** Replays `golden_legacy.npz` through the current API with `assert_array_equal`. |
| `tests/_golden_capture.py` | Regenerates `golden_legacy.npz`. Not auto-discovered. Only in the commit that justifies moving it. |
| `tests/generate_test_snp.py` | Builds the synthetic fixtures with analytically known R/L/C/M. |
| `tests/test_parse_diagnostics.py` | Robust reading: every refusal pins the verdict AND the line number; recovery cases; out-of-order sweeps. |
| `tests/test_large_files.py` | How big a file the tool will read, and what it says when it will not. |
| `tests/test_content_sniffer.py` | The content-based Touchstone parser and the port-count sniffer. |
| `tests/test_port_parser.py` | `parse_port_range` / `parse_short_pairs`, and the range in the Mode 5 DSL's leading port field. |
| `tests/test_freq_label.py` | The marker frequency says where the numbers came from — pane and CLI render one `FreqSnap` identically. |
| `tests/test_core.py` | Parser edges, port ranges, terminations and their precedence, Schur fallback, the one-bad-frequency NaN. |
| `tests/test_connection_rows.py` | Rows ↔ DSL round trip; rows reproduce modes 1/2/3 *including* the ground-wins overlap. |
| `tests/test_conn_nets.py` | Named merged nodes and the parallel-stamp refusal (10 fH typed reads 3.333 fH). |
| `tests/test_conn_rowshape.py` | Per-kind row shape over all 63 kind subsets; the footer route. Drives real widgets — slow. |
| `tests/test_row_table.py` | The `RowTable` widget, the legacy migrations, and that Duplicate shares no list. |
| `tests/test_mode5_editor.py` | The text ↔ rows import decision, both strips, per-mode visibility, the measured layout numbers. |
| `tests/test_editor_scroll.py` | The editor keeps the reader's place when the TRACE changes, and only then. |
| `tests/test_editor_autoapply.py` | When the editor writes into a `TraceConfig` and which one; the style picker; plot visibility; the three CLEARS. |
| `tests/test_port_roles.py` | `port_roles`, `row_sources`, the open-port name check, and the Ports & Roles window. |
| `tests/test_freeze_trace.py` | Freeze's copy rules, both refusals, the label budget, the CSV provenance, the session round trip. |
| `tests/test_session.py` | Save / Load / Restore Last Session — and that the Help window's tab strip still fits. |
| `tests/test_results_notebook.py` | The Log is tab 0, selected and MAPPED at startup; the width-stable badge; severity routing. |
| `tests/test_results_views.py` | The three views, the measured 144-column budget, that a trace name is never elided, and the Digits control. |
| `tests/test_report_readability.py` | The ranked/floored coupling list, the coloured Listbox, the tagged swatch, the footer summary. |
| `tests/test_run_history.py` | The run tabs: both caps, eviction, the stale banner, the conditional auto-switch, Keep at 150%. |
| `tests/test_run_snapshot.py` | The rendered page is byte-identical to `render_reference.json`; no per-frequency array is reachable from a run. |
| `tests/_render_capture.py` | Regenerates `render_reference.json`, and knows the renderers' signatures. Not auto-discovered. |
| `tests/test_plot_controls.py` | The control strip wraps and drops nothing, at any width; the two `ReflowRow` re-place bugs. |
| `tests/test_plot_axes.py` | What range the axes show and what unit they say; the drawable-extent override; pad Y, never pad X. |
| `tests/test_plot_readout.py` | One cursor gets ONE readout: no two texts overlap, none leaves its axes; it follows Digits. |
| `tests/test_attrib_core.py` | Attribution's twelve requirements, and the reconciliation of every what-if against an honest recompute. |
| `tests/test_attrib_vs_engine.py` | A deliberately INDEPENDENT second opinion on decomposition == engine; 4000-spec fuzz. |
| `tests/test_attrib_degenerate.py` | What attribution does when the spec, the network or the data is broken — every case yields a plausible number. |
| `tests/test_attrib_coldstart.py` | The cold-start closed form against an honest re-solve; the bracket, the pair scan, the shield. |
| `tests/test_attrib_cli_coldstart.py` | `--cold-start`: the flag refusals, the printed ORDER, the CSV round trip, the line-width budget. |
| `tests/test_attrib_cli.py` | `--attribute` end to end: every refusal names what was wrong, the printed ORDER, the CSV. |
| `tests/test_attrib_window.py` | The Attribution window in isolation — pure formatters with no display, plus real Tk at 100% and 150%. |
| `tests/test_attrib_gui_integration.py` | The same window END TO END through the real app. Owns the JOIN between the hooks and the window. |
| `tests/test_attrib_golden.py` | The window's TEXT, pinned byte for byte over 56 cases. Creates no Tk root, but imports tkinter. |
| `tests/_attrib_capture.py` | Regenerates `tests/fixtures/attrib_reference/`. Not auto-discovered. |
| `tests/test_attrib_composed.py` | The composed-network gauge inside `pkg_rlc.physics.attrib`; `_island_elements` fuzzed. |
| `tests/test_compose.py` | Composition arithmetic: the weld, the reference check, the frequency plan, the pre-reduction, the export. |
| `tests/test_compose_cli.py` | Every `--compose-*` refusal BY TOKEN; the namespace surviving in and out; R2-8 as a capability. |
| `tests/test_multifile_session.py` | The multi-file SCHEMA — a single-file trace's JSON stays byte-identical. |
| `tests/test_multifile_table.py` | The files window and the measured 7-character port-cell budget. |
| `tests/test_multifile_engine.py` | What Calculate DOES with several files, and the surfaces that have to say so. |
| `tests/test_cli_golden.py` | Replays `tests/fixtures/cli_reference/` byte for byte; the flag matrix is self-guarding. |
| `tests/_cli_capture.py` | Regenerates `cli_reference/` over 143 in-process invocations. Not auto-discovered. |
| `tests/test_cli_coupling_report.py` | The three coupling-report cases no shipped `.sNp` produces (`\|k\|>1`, an undefined rank key, the alarm). |
| `tests/test_coupling.py` | The coupling matrix, degenerate probes, the ranked report, the fits. |
| `tests/test_reduce_snp.py` | `reduce_snp.py`: tied ports, port ranges, and the config file read as it was written. |
| `tests/_isolated_desktop.py` | Runs a child on its own Win32 desktop object. **NOT wired into the runner.** Not auto-discovered. |
| `tests/_smoke.py` | Manual sanity-check script. Not auto-discovered. |
| `tests/_repackage.py` | The one-shot move of the 25 flat modules into `pkg_rlc/`, committed so it reads as a diff. Not auto-discovered. |

## The rest of the rules live in `docs/conventions/`

This file was **428.7k characters**, nearly three times the 150k a session can
hold — so on **2026-08-31** the per-area dossiers moved to `docs/conventions/`,
**VERBATIM**. Nothing was deleted, nothing was reworded, and one duplicated pair
of bullets became one.

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
| [`architecture.md`](docs/conventions/architecture.md) | The four panels of the main window (`pkg_rlc/panels/panels_*.py`) · Clearing the lists (`Clear all files` / `Clear all traces` / `Clear All`) · One formatter, two spellings (the CLI and the results pane) · The run module (`pkg_rlc/services/run.py`) — the SOLVE landed, the ORCHESTRATION did not · How the run record got its home — READ THIS BEFORE MOVING ANY OF IT |
| [`attribution_core.md`](docs/conventions/attribution_core.md) | Port attribution (`pkg_rlc/physics/attrib.py`) · The cold-start screen (`--cold-start`, CLI only) |
| [`attribution_gui.md`](docs/conventions/attribution_gui.md) | The Attribution window (`pkg_rlc/panels/attrib_gui.py`) · The two attribution reports (`pkg_rlc/present/attrib_report.py`) |
| [`cli_report.md`](docs/conventions/cli_report.md) | The CLI's printed report (`tests/fixtures/cli_reference/`) |
| [`editor_and_tables.md`](docs/conventions/editor_and_tables.md) | Connection table (the Mode 5 / Mode 6 row editor) · Per-kind row shape, nets, and the parallel stamp (round 1) · Auto-apply, the style picker, plot visibility · Port names, roles, and the Ports & Roles window |
| [`multifile.md`](docs/conventions/multifile.md) | Composition — several files as ONE network (`pkg_rlc/physics/compose.py`, round 2) · The two-file GUI — schema, namespace, engine (round 3) |
| [`plot_panel.md`](docs/conventions/plot_panel.md) | The plot panel's axes (what range they show, what unit they say) · The plot panel's control strip · Cursor readout (the plot's marker / V-line labels) |
| [`reading_files.md`](docs/conventions/reading_files.md) | Reading files (robustness, diagnosis, refusal) |
| [`results_pane.md`](docs/conventions/results_pane.md) | Freeze as trace (the before/after comparison) · The run snapshot (what a finished Calculate leaves behind) · The Results pane notebook (the Log tab and its badge) · The three results views (`detail` / `summary` / `compare`) · The Digits control (how many significant digits a value is printed to) · Run history (the run tabs after the Log) |
| [`session_and_help.md`](docs/conventions/session_and_help.md) | The session file (Save Config / Load Config / autosave) · The Help window's prose lives in `docs/help/`, not in Python |
| [`standalone_and_deploy.md`](docs/conventions/standalone_and_deploy.md) | `reduce_snp.py` specifics · `deploy/` specifics (red-zone pipeline) · Hiding the GUI tests (`tests/_isolated_desktop.py`) |
| [`test_suite_map.md`](docs/conventions/test_suite_map.md) | `tests/` — the suite, in the order it grew |

Three things about them, so the split does not rot:

- **A new rule goes in the file that owns its AREA, not here.** This file is for
  what a session must know without being told to look. If it is about one
  window, one panel or one report, it belongs in its dossier — and the dossier
  is where the next session will look, because this table sends them there.
- **They ship to the red zone.** `.gitattributes` is a blacklist and
  `docs/conventions/` is not on it, which is the same treatment
  `docs/theory.md` and the three design notes already get. `CLAUDE.md` itself is
  `export-ignore`d; that asymmetry is deliberate and was left alone.
- **The measured figures still have several homes and no guard ties them
  together** (`6.07 dB`, `9.60 dB`, `-870.268 pH`, `505.25 nH`, the 405 px /
  431 px editor budget, the 144-column pane). Moving the prose did not change
  that — see the KNOWN-NOT-FIXED note at the end of
  `docs/conventions/session_and_help.md`, which counts the overlap.

## Critical invariants (do not regress these)

- **All Listboxes set `exportselection=False`.** Without it, clicking an Entry/Spinbox steals the X selection and clears the highlight. The editor resolves its auto-apply target from that selection, so a cleared highlight means every keystroke is silently discarded.
- **Auto-sync editor on Calculate.** Before computing, flush any queued sync and push current editor fields into the selected trace. Auto-apply usually got there first, but a keystroke in the same event burst as the click is still in the idle queue.
- **Truncate trace labels to 30 chars** in plot legends, or subplots squeeze.
- **Log-scale drag tolerance.** The freq-marker drag detector must use log-space distance when the x-axis is log.
- **Canvas focus.** After every `FigureCanvasTkAgg`, call `canvas.get_tk_widget().focus_set()` so M / V / Delete keys are received (also in the fullscreen `Toplevel`).
- **Port indices: 1-based at the GUI/CLI boundary, 0-based inside core.** Convert in the `build_terminations_*` builders, never deeper.
- **Schur reduction uses `np.linalg.solve`** (not explicit inverse). On `LinAlgError` or pathological condition, fall back to `np.linalg.lstsq` and emit a warning naming the offending frequency.
- **`lstsq` IS THE LAST RESORT AND IT CAN FAIL TOO — one bad frequency must NaN
  that frequency, never the sweep.** LAPACK's SVD does not converge on a
  non-finite `Y_oo`, and a non-finite `Y_oo` is ORDINARY here rather than
  exotic: a lumped `L` to ground is `y = 1/(jwL)`, which numpy evaluates to
  `inf+nanj` at `w == 0`, so any spec carrying a ground-lead inductance, read
  off a file that carries a DC point (**every composed sweep KEEPS 0 Hz**), puts
  a NaN in `Y_oo` at exactly the frequency where a DC-isolated port — one
  reaching the rest of the network only capacitively — also makes it exactly
  singular. Both conditions are needed and **both are normal**: together they
  aborted a real 61+37-port composed run at index 0 with an UNCAUGHT
  `LinAlgError` while all 2000 other frequencies were healthy (`solve` ->
  `Singular matrix`, batched then per-frequency, then the unguarded `lstsq` ->
  `SVD did not converge in Linear Least Squares`). Order matters for
  reproducing it: LAPACK's partial pivoting has to meet the exact zero before
  the `inf` poisons the column it would pivot on, which is why the fixture puts
  the DC-isolated port FIRST among the open-like ones. The guard is
  `complex(nan, nan)` for that frequency plus a warning naming it — a real NaN
  would leave `imag == 0` and `L = Im(Z)/omega` would read as a plausible 0 H.
  Same rule and same reason as `_probe_impedance`'s guard on its own SVD, which
  has had it all along; that asymmetry between two sibling paths in one
  function is the whole defect. Healthy frequencies are bit-identical with and
  without the DC point (measured, `array_equal`). The user-side workaround
  needing no redeploy is a finite series `R` on the lead (`R=1u` beside
  `L=50p`): `y` is then finite at DC, lstsq converges, and 1 µΩ against 1.74 Ω
  of reactance at 5.55 GHz costs **6.7e-8** relative.
  `tests/test_core.py::TestOneBadFrequencyDoesNotAbortTheSweep` is the guard
  (mutation-checked: 5 of its 6 tests die with the uncaught `LinAlgError` when
  the try/except is removed; the sixth is the precondition that pins the
  fixture still reaches the guarded line). The pre-existing
  `TestSchurSingularityFallback` does **not** cover this — its `Y_oo` is
  singular but FINITE, so lstsq succeeds and the guarded line is never reached,
  and it accepts a raise as correct behaviour anyway.
- **KNOWN, NOT FIXED: `s_to_y` / `y_to_s` have the same shape one level up.**
  Their per-frequency fallback is `np.linalg.pinv`, whose SVD raises identically
  on a non-finite `S`, and nothing catches it. Not the composed-run crash above
  (that file's `S` is finite; the `inf` is stamped later, by the lumped
  element), so it is recorded rather than patched blind — fix it with a fixture
  that actually carries a NaN `S` entry, not by pattern-matching this bullet.
- **Auto-create a default trace on file load.** Don't make users hit "Add Trace" for the basic workflow.
- **Y-axis log uses `symlog` with `linthresh=1e-6`** to handle data crossing zero.
- **R / L / C / Q are reported with their physical sign (Cadence convention).** `extract_rlc_at_freq`, the plot's `trace_y_values`, and both CSV exporters must NOT clip negative values to NaN. Q is `Im(Z)/Re(Z)`, not `|Im(Z)|/Re(Z)`; `L = Im(Z)/ω` and `C = -1/(ωIm(Z))` go negative past/below SRF respectively. The GUI results pane appends a brief annotation when a value is negative — keep that in sync if formulas change.
- **Multi-file comparison.** Each `TraceConfig` independently selects its file and port config — two traces can reference different files and plot together.
- **`PlaceholderEntry.get_value()` returns `""` when the placeholder is showing.** Never read `_var.get()` directly to fetch user input — placeholder text would leak in. Same rule for `PlaceholderText`.
- **The parser must split comment / option lines on the exotic line breaks too.** `str.splitlines()` — what the parser used before it streamed the file — breaks on `  -     `; iterating a text-mode file object breaks only on `
`. A header page-broken with a form feed would otherwise swallow the data record that follows it, silently dropping frequency points. Only `#`/`!` lines (and the tail of a mid-line `!` comment) need the check — every one of those characters is whitespace to `str.split()`, so data lines tokenise correctly either way. That is also why the hot path stays free of a per-line `splitlines()`.
- **The RI fill normalises signed zeros.** `np.add(body[...,0], 0.0, out=s.real)` rather than a plain assignment: real EDA exports write `-0.000000e+00`, and the historical `body[...,0] + 1j*body[...,1]` turned those into `+0.0`. `assert_array_equal` cannot see the difference (`-0.0 == 0.0`), so the golden reference does not guard it — `tests/test_core.py:TestParserSignedZero` does. Measured cost of the fused add: +2%.
- **`pkg_rlc.physics.core` IS A FACADE AND ITS WRITE-THROUGH IS LOAD-BEARING.** It is a
  `types.ModuleType` subclass whose `__setattr__` / `__delattr__` forward to
  whichever of the three split modules defines the name. Not a nicety:
  `tests/test_large_files.py` and `tests/test_parse_diagnostics.py` do
  `mock.patch.object(pkg_rlc.physics.core, "MAX_SNIFF_NPORTS" / "SNIFF_HARD_CAP" /
  "DIAGNOSE_MAX_LINES" / "DIAGNOSE_TAIL_LINES" / "_check_s_values", …)` and then
  call `parse_touchstone`, and the parser reads its OWN module global — so a
  re-export-only facade rebinds this module's copy and leaves **five tests
  asserting nothing**, with no failure to notice. Reads need no help. Dunders
  are excluded, because `hasattr(mod, "__name__")` is true of every module and
  writing one through would rename the module it reached. If you would rather
  delete the machinery, the replacement is to repoint those five call sites at
  `pkg_rlc.physics.touchstone` — one line each — in the same commit, never to drop the
  subclass and leave the patches pointing here.
- **`format_si` / `format_freq` live in `pkg_rlc.physics.touchstone`, not with the
  arithmetic, and that is forced.** `TouchstoneData.freq_span` and
  `_check_freq_axis` need `format_freq` to describe a sweep, and
  `_effective_parallel` in `pkg_rlc.physics.spec` needs `format_si` for the
  parallel-stamp message ("10 fH becomes 3.33 fH"). Both callers are at or
  below the solver, so the helpers have to be in the LOWEST of the three or the
  module holding them gets imported from underneath itself. `pkg_rlc.physics.solve`
  re-exports them, so `pkg_rlc.physics.solve.format_si` resolves and `pkg_rlc.widgets.plot`'s
  `from pkg_rlc.physics.core import format_si` is unchanged. `COMPUTE_BATCH` /
  `COMPUTE_CHUNK_BYTES` are in `pkg_rlc.physics.touchstone` for the same shape of
  reason — `_freq_batch` is there because `_check_s_values` chunks with it —
  even though their comments talk about the batched linear algebra one layer up.
- **`_validate_port_indices` is in `pkg_rlc.physics.spec`, not `pkg_rlc.physics.solve`.**
  `build_terminations_coupling` and `build_terminations_rows` both call it when
  given a port count, and `compute_z_matrix` calls it as its backstop. Putting
  it with the arithmetic makes spec import solve and solve import spec. A file's
  port count is part of the declaration; the note above the function names all
  three callers.
- **`pkg_rlc/physics/touchstone.py` is CRLF and contains a literal U+2029** (it was at
  line 760 of the old `pkg_rlc/physics/core.py`, and it moved with the parser that owns
  it; `pkg_rlc/physics/core.py` is still CRLF but no longer holds the character)
  (the parser's own exotic-line-break handling — the same characters
  the parser is documented to split comment lines on). Anything that slices
  these files by line number must split raw bytes on `b"\r\n"`:
  `str.splitlines()` breaks on U+2029 too, so every line number after it is off
  by one and a slice cuts in the wrong place **silently**. This is how the split
  itself was cut, and it is worth knowing before the next one.
- **`ReflowRow` IS IN `pkg_rlc.widgets.widgets`, NOT `pkg_rlc.widgets.plot`, and `pkg_rlc.widgets.plot`
  re-exports it.** CLAUDE.md documented it under `pkg_rlc.widgets.plot` in two places
  (the module-map row and "The plot panel's control strip"); both now say
  `pkg_rlc.widgets.widgets`. Nothing about its behaviour moved — it still lays out by
  `place`, still reads an imposed WIDTH and writes only a HEIGHT, and that
  fixed-point property is still what keeps it out of the
  `_apply_editor_scrollbars` limit cycle.
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
- **THE OTHER NINE ARE GONE, and they were removed the way this gate demands:
  BY MOVING THE SYMBOL DOWN.** `pkg_rlc.panels.files_gui -> pkg_rlc.frontend.app` x8 and
  `pkg_rlc.panels.attrib_gui -> pkg_rlc.frontend.app` x1 (the `_gui()` shim). **None of them
  ever wanted `pkg_rlc.frontend.app`.** They wanted `TraceConfig` and
  `_config_signature`, which are now `pkg_rlc.model.trace`'s;
  `compose_spec_problems` / `trace_file_labels` / `_trace_role_rows` / the
  three port-field scopers / `trace_is_composed`, which were ALREADY
  `pkg_rlc.model.validate`'s; `_value_formatter` and `LOG_WARN`, already
  `pkg_rlc.present.report`'s; and `WARN_FG` / `PORT_ROLE_FG` / `_fixed_map_filter` /
  `PLACEHOLDER_FG`, now all `pkg_rlc.widgets.widgets`'. So by the end most of those
  lazy imports were reaching *through* `pkg_rlc.frontend.app` for symbols that were no
  longer its — which is the shape a stale dodge takes, and the reason the
  removal half of this gate is worth its cost.
  **Three of the colour lookups carried a `try/except` with a hard-coded hex
  fallback and `_install_style`'s carried one that `return`ed** — leaving the
  Treeview tag colours silently unapplied, i.e. exactly the symptom
  `_fixed_map_filter` exists to prevent. Those fallbacks went with the
  imports; a duplicated colour that appears silently on a failed lookup is
  drift with no symptom. The `try/except` that guards the reach into the
  `App` (`_append_result`) STAYS, because that is what it was actually
  guarding.
  The comments that presented the dodge as deliberate design were REWRITTEN,
  not deleted: they were true when written, and a later session reading a
  deleted explanation would reinstate the trick.
- **`tests/test_multifile_table.py`'s delegation test had to move its patch
  point, and that is a real consequence rather than test-fitting.** It
  monkeypatched `pkg_rlc.frontend.app.trace_file_labels` and asserted
  `pkg_rlc.panels.files_gui.trace_file_labels` picked it up — which worked only while
  the delegation went through a lazy `import pkg_rlc.frontend.app` and an attribute
  lookup on it. The live definition is `pkg_rlc.model.validate`'s and is bound at
  the top of the file now, so the OLD patch point no longer sits on the edge
  under test and would have made the test pass against a function that had
  stopped delegating. It patches `fg._live_trace_file_labels`, the name the
  call actually goes through; the mutation the docstring names (call the
  fallback directly) still turns it red, **checked by applying it**.
- The fourth pair —
  `pkg_rlc.panels.attrib_gui -> pkg_rlc_extractor` x2 — is GONE: it was a panel
  importing two pure functions out of the CLI entry point, those two were
  L0/L3 material as recorded, and they are now in `pkg_rlc.present.attrib_report`,
  which the window imports at the top of the file like anything else. **Name
  the moved symbols precisely, because the two names are easy to swap:** what
  moved is `_attr_ground_model` / `_attr_zt`; the lazy imports sat INSIDE
  `parse_ground_model` / `ground_model_zt`, which are thin wrappers that never
  left `pkg_rlc.panels.attrib_gui`. Both moved symbols are still re-exported from
  the CLI module (rule 2), so `pkg_rlc.frontend.cli._attr_zt` keeps
  resolving — verified against the live modules, not inferred from the diff.
  **They do NOT resolve on the root `pkg_rlc_extractor` shim any more**, and
  that is the one place the shim is not a transparent stand-in for the CLI: it
  re-exports with a star import, which skips underscore names. The window's
  test and every other caller of a private CLI name reach
  `pkg_rlc.frontend.cli` directly.
  **The four `pkg_rlc.panels.panels_*` modules add NONE** — a
  panel may not import `pkg_rlc.frontend.app` at module level or inside a function, and
  they are L5 because they are in `pkg_rlc/panels/`, so a module under
  `pkg_rlc/` in a folder nobody has declared still fails outright.
  The removal was CLOSED by deleting that pair from `KNOWN_BACK_IMPORTS` and
  `KNOWN_BACK_IMPORT_COUNTS`, which is the only edit to a test file the whole
  wave made — and it is the gate working exactly as designed
  (*"REMOVING one also fails until the same commit updates the declaration"*).
  The same thing happened again, nine pairs larger, when the model landed:
  the gate went red on `KNOWN_BACK_IMPORTS` the moment the last lazy import
  was deleted, and that red IS the proof the phase worked.
- **A CLASS-BODY import counts as module-level; only a `def` body defers.**
  An import written in a class body runs at import time, so it is part of the
  real graph and cannot be a dodge. Both cases are pinned.
- **The fix is always to MOVE THE SYMBOL, not the import**, and every failure
  message in the file says so by name. A lazy import hides the cycle from the
  interpreter and leaves it in the design.

### Measurement ports / coupling (Mode 6)

- **The Z matrix is the OPEN-CIRCUIT matrix.** `Zmat[k, a, b]` is defined with every *other* measurement port carrying no current. That is the textbook definition of M and it is what makes the extracted number reusable in a simulator. Never "helpfully" terminate or short the idle ports — that is a different, load-dependent quantity.
- **M / C_c / k are signed and are never clipped, `abs()`-ed or hidden.** Same rule as R/L/C/Q. `M = Im(Z_ab)/ω` and `C_c = -1/(ω·Im(Z_ab))` are *both* always computed: when the coupling is inductive `C_c` comes out negative, when it is capacitive `M` does. Display logic picks which to headline from the sign of `Im(Z_ab)`; it must not suppress the other. `k` is NaN only where genuinely undefined (`L_a <= 0` or `L_b <= 0`), and `|k| > 1` adds a note rather than clamping. This applies to `extract_coupling_at_freq`, `_coupling_k_array` in the GUI, the plot's `trace_y_values`, and both coupling CSV writers.
- **Group `"B"` is a legacy alias for the minus side of group `"A"`.** `_normalize_signal` maps `Signal("B", +1)` -> `("A", -1)`. This is what turns every pre-existing mode, saved session and test into an ordinary single-measurement-port case. `"A"` and `"B"` are therefore **reserved names**, rejected case-insensitively by both `parse_mport_spec` and `build_terminations_coupling`. Do not remove the alias and do not let a new measurement port claim those names.
- **The `G == 1` branches in `compute_z_matrix` keep the legacy floating-point expressions verbatim.** `ones @ Y_red[ix] @ ones` then `1.0/y_eff` for the no-minus-side case; the 2x2 `.sum()` build + `np.linalg.inv` + `Z2[0,0]+Z2[1,1]-Z2[0,1]-Z2[1,0]` **in that evaluation order** otherwise. They are mathematically identical to the general `W.T @ pinv(Y_node) @ W` branch but not bit-identical (different BLAS calls sum in different orders). Do NOT "clean them up" into the general path — that is exactly what the golden regression catches.
- **Step 4c/5c (the shorted-port merge) must use `np.add.at`, never a matmul.** Cells fed by a multi-member group need order-preserving accumulation in the same row-major order the old Python double loop used. A matmul sums in a different order and breaks bit-exactness. The single-member block is a plain vectorised gather-add (still `0 + x`), which is safe.
- **The per-frequency contraction in 5f must stay per-frequency,** and `np.ascontiguousarray` on the two Schur matmul operands is load-bearing, for the same summation-order reason. Only the O(n_open^3) `np.linalg.solve` is batched.
- **`pinv(Y_node, rcond=PINV_RCOND)`, not `inv`.** A fully floating differential structure has a singular `Y_node` whose null direction is the common mode; the balanced `+/-` injection is orthogonal to it, so `pinv` is exact where `inv` returns garbage. The rank-deficiency warning is **informational** (capped at 3, mirroring the Schur-fallback cap) and fires at *every* frequency on a normal coupled-inductor file — GUI and CLI both annotate it as such. Do not turn it into an error.
- **`np.linalg.inv` does NOT raise on a numerically singular matrix**, so the `except LinAlgError -> pinv` guard alone is dead code. `Y2 = [[y,-y],[-y,y]]` gets `det ~ 1e-19` from LAPACK's LU, `inv` returns a `~1e16` matrix, and `Z2[0,0]+Z2[1,1]-Z2[0,1]-Z2[1,0]` is the difference of four huge numbers (measured: `L` wandering 1.27 / 4.77 / 2.12 / 3.18 / 2.23 nH instead of a flat 2.000). `_is_singular_2x2` tests `|det| <= rcond*|Y2|_max^2` **before** `inv` gets the chance and routes those frequencies to `_probe_impedance`. Healthy fixtures sit at `>= 1e-8`, the degenerate one at `<= 3e-15` — do not remove the guard and do not widen the threshold into that gap.
- **`pinv` is only valid for probes orthogonal to `null(Y_node)`.** `_probe_impedance` does one SVD and uses it for three things: numpy's exact `pinv` expression (verified bit-identical), the rank flag, and the null-space test `|| U[:, r:]^H w_g || / || w_g || > PROBE_RANGE_TOL`. A probe that fails it has **no return path** — its whole row and column of `Z` become `complex(nan, nan)` (a real `np.nan` would leave `imag == 0` and `L` would read as a plausible 0 H) and a warning names it. Other measurement ports are untouched: `Z[b][c] = w_b^T Y^+ w_c` only involves those two columns, so one bad probe cannot contaminate a good one. `PROBE_RANGE_TOL = sqrt(PINV_RCOND)`, not `PINV_RCOND` — that is the level below which the discarded direction contributes less than the truncation `pinv` already commits, and it is what keeps a *nearly* floating structure out of the error path.
- **The `G == 1, no minus side` branch deliberately has NO degeneracy check.** `1/y_eff -> inf` is already the honest reading of a probe with no return path, and `y_eff` also crosses zero at a genuine parallel anti-resonance, where a huge `Z` is the answer the user came for. No single-frequency magnitude test can tell those apart. **But the divide is wrapped in `np.errstate` and a non-finite result appends `_open_probe_warning` to `warnings_out` (capped at 3, like the others).** The numbers do not move — this is not a threshold, and the golden reference is untouched. What moved is where the one diagnostic the branch produced went: numpy's own "divide by zero" / "invalid value" `RuntimeWarning` goes to fd 2, and a double-clicked GUI has no fd 2, so the only notice was invisible to every GUI user while the results pane printed the roundoff as a formatted measurement (`3.6e+03 TOhm  -11.5 MH  2.21e-10 fF`) with no annotation at all. `warnings_out` is the channel the GUI prints under `Calculate @ …` and the CLI reports. The text names BOTH readings, because the branch genuinely cannot tell them apart.
- **`SCHUR_COLLAPSE_TOL` is advisory only — it must never produce a NaN.** It flags `|Y_kk - Y_ko @ X|` falling under `1e-12` of its own two terms (pure cancellation noise). Unlike the rank test this is a magnitude heuristic: healthy fixtures bottom out at `3.8e-10` against `7e-16` for the degenerate case, so the margin is real but finite. Checked once per chunk (`i == 0`) and only when `>= 2` ports survive the reduction.
- **Port indices are validated against the file's port count** in `_validate_port_indices`, called from `compute_z_matrix` (and from `build_terminations_coupling` when `nports=` is passed, which the GUI and CLI both do). Before this, `"3 / 5"` on a 4-port file silently became a ground-referenced probe reporting a plausible wrong number. The resolver only scans `range(n)`, so nothing deeper can catch it.
- **A probe port may not also be a GND port (Mode 6 only).** A probe side is tied together, so grounding one of its ports grounds the whole side; `build_terminations_coupling` raises. `build_terminations_mode1/2/3` keep their historical "ground wins" precedence — do not "fix" those, the golden reference pins them.
- **`compute_z` warns when `G > 1`.** It returns only measurement port 1. Only Mode 5 can get there (the named builders always produce `G == 1`), and Mode 5 is exactly the free-text mode where `signal V` instead of `signal B` silently defines a second measurement port and changes the answer by 37%.
- **`RECIPROCITY_WARN = 1e-3` lives in `pkg_rlc.physics.solve`** (re-exported by `pkg_rlc.physics.core`, so `pkg_rlc.physics.core.RECIPROCITY_WARN` still resolves) and is imported by both `pkg_rlc.frontend.app` and `pkg_rlc_extractor`. They used to disagree (1e-3 vs 1e-12), so the same file got opposite verdicts and the CLI cried wolf on every real EM file. The metric skips non-finite off-diagonal entries so one undefined measurement port cannot poison it.
- **`M/L` is the Norton injection ratio, NOT the current-transfer ratio.** The exact ratio into a shorted port `a` is `I_a/I_b = -Z_ab/Z_aa`; `M/L_a` equals its magnitude only where `w*L_a >> R_a` (1098% apart at 10 MHz for `L=2n, R=1.5`). The label is "coupling ratio" in six places — `pkg_rlc.physics.solve`'s docstring (it moved there with the arithmetic), the CLI report, Help, README, theory.md, and `pkg_rlc.physics.attrib`'s `DECOMPOSABLE` entry. Keep the six in sync. **The GUI legend is deliberately NOT one of them any more**: the results-views slimming replaced the per-block legend with `COUPLING_LEGEND_LINES` in `pkg_rlc.present.report`, printed once per run, and it spells the quantity `M/L = Norton injection ratio, NOT the exact current ratio |Z_ab/Z_aa| (equal only where wL >> R)` — the whole caveat rather than the short label. That is the more precise wording, so the seventh site is not a drift to repair; it is why this bullet says six and not seven. `pkg_rlc.physics.attrib.transfer_ratio` is where the EXACT ratio is available as a number rather than as a caveat.
- **BOTH surfaces' pair lists are RANKED by `max(|M/L_a|, |M/L_b|)` and floored at `COUPLING_FLOOR_DB = -60`, through the one `rank_coupling_pairs`** (the CLI joined the GUI here when divergence 2 was closed — see "One formatter, two spellings" in `docs/conventions/architecture.md`; the CLI's fold pointer names `--csv` rather than Export CSV and is true for the same reason). Six measurement ports make 15 pairs, and nested-loop `(a, b)` order carries no information about which of them matter. `|k|` alone is the wrong key: `|k| = 0.02` between two 2 nH coils and between a 2 nH and a 500 pH coil are different problems — same `M`, 4x the injection into the small one. `rank_coupling_pairs` is pure and mutation-checked, and **magnitude appears there and nowhere else** — every printed cell stays signed. Three rules are load-bearing: `_pair_strength` is computed **linearly**, not from the `*_dB` fields (`_ratio_db(0)` is NaN, and a pair with `M = 0` is the weakest there is, not an undefined one); a pair with an **undefined** ratio sorts last and is **never** folded away (NaN is a missing measurement, not a small number); and the **strongest** pair is never folded away either, or a block can consist of nothing but "3 pairs were too weak to list". The `(see Export CSV)` pointer is true because `_write_coupling_csv` enumerates every unordered pair straight off the Z matrix and has no floor — do not give it one.
- **`compute_z` is a thin wrapper returning `Zmat[:, 0, 0]`** — the self impedance of the FIRST measurement port, and a strided **view**, not a fresh contiguous array. Copy before writing into it or before handing it to code that assumes C-contiguity (the GUI does `np.ascontiguousarray`).
- **`tests/fixtures/golden_legacy.npz` is the guard for all of the above.** It pins `parse_touchstone -> s_to_y -> compute_z` bit-for-bit for every fixture and for representative Mode 1/2/3/4/5 cases. If it fails, the reduction path changed: fix the change, do not regenerate the reference to make the test pass.
- **The Mode 5 DSL and its helpers live in `pkg_rlc/physics/spec.py`** (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`) — terminations belong to the declaration model, and `pkg_rlc.physics.core` re-exports all four so `from pkg_rlc.physics.core import parse_si` keeps resolving. `pkg_rlc/frontend/app.py` re-imports them so `from pkg_rlc.frontend.app import parse_si` and friends keep resolving; keep that re-export list intact.
- **DSL signal syntax is `<port> signal <groupname> [+|-]`.** Group names are arbitrary strings; the sign is a **separate whitespace token** defaulting to `+`, and anything other than exactly `+` or `-` raises. A name whose `.upper()` is `A` or `B` is upper-cased so legacy `signal a` / `signal b` keep working. There is deliberately **no** "signal group must be A or B" validation any more, in either `compute_z_matrix` or the DSL — don't reintroduce it.

### Rejected UI proposals (do not re-propose these)

Each was designed, measured, and turned down for a reason that has not changed.
They keep coming back because they sound good in one sentence, so the reason is
recorded here rather than in a commit message nobody will find.

- **A matplotlib connection SCHEMATIC in a tab beside the plot.** Three costs,
  each fatal on its own: a notebook tab strip is **26 px of permanent plot
  height**, paid on every session whether or not the schematic is ever looked
  at (the Results notebook already spends 28 px, and the plot pane is 400 px at
  the minsize); `<<NotebookTabChanged>>` on a plot notebook forces a
  `canvas.focus_set()` handler, and the M / V / Delete keys depend on canvas
  focus, so switching tabs either steals focus from the plot or silently breaks
  those keys; and a matplotlib redraw is **~10x** the cost of drawing the same
  boxes on a `tk.Canvas`, on a path that fires from the editor's variable traces
  — i.e. per keystroke. If a schematic is ever built, it is a `tk.Canvas` in a
  Toplevel, like the Ports & Roles window.
- **A `ttk.Treeview` for the MAIN results table.** It destroys the `aligned`
  units mode outright — that mode exists so digits line up column-wise in a
  monospace `Text`, and a Treeview lays out per cell in a proportional font; it
  loses select-and-copy of a whole block, which is how numbers get into a mail
  or a spreadsheet; and its row height is frozen at 20 px whatever the font (the
  hazard the Ports & Roles window has to work around with a derived style). The
  ban is on the *editable* table for a different reason (no cell editors) and
  the *read-only role list* is a legitimate Treeview; the results table is
  neither case.
- **A unicode bar chart of `|k|` in the coupling block.** Wrong metric — `|k|`
  is exactly the key `rank_coupling_pairs` rejected, because `|k| = 0.02`
  between two 2 nH coils and between a 2 nH and a 500 pH coil are different
  problems. Wrong scale — real values span `1e-4 … 1e-1` and a linear bar
  renders every one of them as an empty cell but the strongest. And it
  contradicts the signed-value invariant: a bar has no sign, and `M`, `C_c` and
  `k` are signed everywhere in this tool on purpose.
- **A large-type KPI strip above the plot ("R = 1.5 Ω  L = 2 nH  …").** It is
  the plot's cursor readout, printed twice. The readout is already the legend,
  already tracks the marker, already prints engineering units through
  `format_si`, and is already the thing a reader is looking at. A second copy
  fed by a different code path drifts from it — and if it were fed by the same
  path it would just be the readout in a worse place, costing plot height the
  strip measurements above say is not available.
- **A FOUR-TAB notebook inside the Attribution window** (Contributions /
  Sensitivity / Sweep / Across-frequency). Rejected on what the tabs *are*, not
  on pixels: the sweep is a **drill-down on the row just clicked**, so a tab
  makes the user re-pick the element they already selected and breaks the one
  gesture the pane exists for; and "does this ranking hold across frequency" is
  a **validity qualifier on the table, not a place**, so as a tab it is never
  opened and the requirement behind it is satisfied on paper only. A
  radiobutton view toggle plus a one-line badge with an expander does both jobs
  in the height a tab strip alone would have cost.
- **A `ttk.Treeview` for the Attribution window's contribution table.**
  Different case from the two Treeview entries elsewhere in this file, and it
  loses on its own measurement: eight columns need **671 px** as a Treeview at
  100% font scaling and **971 px** at 150%, against **490 / 700 px** as
  Consolas 9 text; ttk will not shrink a column below its set width even with
  `stretch=True`, and it clips with **no ellipsis and no overflow indicator**,
  so `-0.6231` silently becomes a plausible shorter number. And in
  `TkDefaultFont` the signed-number glyphs are all different widths (`-` 5 px,
  `+` 9, U+2212 9, `.` 3, ` ` 4, digits 7), so a right-aligned column of signed
  values has its decimal point wandering ±4 px per row. A read-only Treeview is
  still right for **names and roles** (Ports & Roles); it is wrong for a table
  of signed numbers. Note this reverses `design_port_attribution.md` §9's own
  recommendation — that bullet is struck through there with the measurement.
- **An eleventh Help tab for attribution or cold start.** `HELP_TOPICS`' ten
  tabs need **968 px** against `HELP_WINDOW_WIDTH = 1010`; an eleventh labelled
  `Cold start` takes the strip to **1033 px**, `Attribution` to **1037**,
  `Port attribution` to **1064**. A `ttk.Notebook` clips silently and the tab
  that vanishes is the LAST one, so the new tab would be the invisible one.
  Fold into `Mode 6 (Coupling)` and cross-reference.

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

`--fast` now covers the cold-start screen: `test_attrib_coldstart` and
`test_attrib_cli_coldstart` were qualified on the one property `FAST_MODULES` has — neither
imports tkinter — and were added, which is what took it from 523 tests / 2.9 s to 642 / 4.1 s;
the runner's own suite then took it to 699 / 4.4 s. Round 2 added the four composition
modules on the same qualification (`test_compose`, `test_compose_cli`, `test_attrib_composed`,
`test_conn_nets` — `test_compose_cli` has its own test asserting `pkg_rlc.frontend.app` never entered
`sys.modules`), for **976 tests / 5.5 s**. `test_conn_rowshape` is deliberately excluded:
it drives real widgets and its slowest shard alone is 19 s. The list has not moved since,
so `--fast` at **1044 tests / 4.5 s** is the same eighteen modules with their own growth in
them. Three modules now QUALIFY on that one property and are not in the list yet:
`test_layering` (27 tests / 0.35 s, pure `ast`), `test_cli_golden` (37 / 1.8 s) and
`test_run_parallel` (already in). `test_attrib_golden` does **not** qualify — it creates no
Tk root but it does import tkinter (see "The text golden reference" in `docs/conventions/attribution_gui.md`).

- **Shards run at BELOW NORMAL priority on Windows, and that is free.** The user works on
  this box while the suite runs; a full run is 4-8 test processes for six to ten minutes,
  and at NORMAL priority that is head-on competition with whatever they are doing.
  `run_parallel._priority_kwargs()` returns
  `{"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}` on Windows and `{}` everywhere
  else — the guard tests **both** `sys.platform` and `hasattr`, because the constant does not
  exist in `subprocess` off Windows (`sys.platform` alone is an AttributeError on POSIX;
  `hasattr` alone is a silent no-op). **BelowNormal, not Idle**: Idle is starved by anything
  that compiles, so a suite at Idle stops making progress exactly when the user is busiest,
  which is when it was left running. Measured, two adjacent runs at ONE worker count on one
  tree (2522 tests / 452 shards, 20 cores, sibling test processes live throughout): `-j 4`
  NORMAL **461.7 s** against `-j 4` BelowNormal **464.6 s**, i.e. **+0.6%**, noise.
  `--fast -j 4` is 10.2 s BelowNormal against 10.6 s NORMAL. Verified on a real process
  rather than inferred — `(Get-Process -Id N).PriorityClass` reads BelowNormal through
  `run_shard`'s spawn and Normal through the same spawn without the flag. The `-j 8` NORMAL
  (414.2 s) vs `-j 4` BelowNormal (464.6 s) gap quoted in the docstring is the WORKER COUNT,
  not the priority: the same `-j 8` NORMAL run repeated during a contention spike read
  **648.1 s**. Read the exit code, not the clock.

## How to add a new measurement mode

Pick the **next unused integer** code (4 is retired, not free) and never renumber the existing ones — saved trace configs carry the integer.

The files below are named by their PATH, and the path is the layer: nothing a new mode adds may import upward, so the spec helper goes at L0, the fields at L1 and the widget at L5, and `tests/test_layering.py` will say so if they do not.

1. **Core**: add a `build_terminations_modeN(...)` helper in `pkg_rlc/physics/spec.py` (`pkg_rlc.physics.core` is a facade and re-exports it) that produces a `TerminationSet`, converting 1-based to 0-based *there* and nowhere deeper. If a new termination semantic is needed, add a dataclass to the `PortTermination` / `Coupling` unions and handle it in `compute_z_matrix`'s evaluation order (lumped -> short merge -> ground/vdd drop -> Schur -> probe-node contraction). If the mode only rearranges probes, it needs no new semantic at all — `Signal(group, sign)` already covers arbitrarily many measurement ports.
2. **GUI**: five files, because the editor, the model, the spec renderer, the solve and the CLI are five layers now. `pkg_rlc/panels/panels_editor.py` (L5): a new radio button in `_build_editor`, a placeholder-hint entry in `MODE_PLACEHOLDERS`, and `_update_mode_visibility` extended to show/hide and re-set placeholders. `pkg_rlc/model/trace.py` (L1): the new fields on `TraceConfig`. `pkg_rlc/model/validate.py` (L1): `_port_descriptor`. `pkg_rlc/services/run.py` (L2): the dispatch in `_build_termination` — and `App._build_termination` in `pkg_rlc/frontend/app.py`, which migrates and then calls it, needs nothing new. `pkg_rlc/frontend/cli.py` (L6): mirror the dispatch in the argparser (`_make_arg_parser` + `_run_cli`) and reject flags that belong to other modes with a clear message. A **table-based** mode registers NO `MODE_PLACEHOLDERS` entry — a cell cannot hold a hint, so its hint is a `_CollapsibleHint` under the table.
3. **Help**: document the mode in `docs/help/` (the prose files `pkg_rlc/present/help.py` reads) with assumptions, inputs, and a worked example. **A new tab is no longer free**: the ten existing tabs need 968 px against `HELP_WINDOW_WIDTH = 1010`, and an eleventh takes the strip to 1033–1064 px depending on its label (measured), where a `ttk.Notebook` silently clips the LAST tab. Either fold the mode into the nearest existing tab (what port attribution and the cold-start screen did, into `Mode 6 (Coupling)`) or widen `HELP_WINDOW_WIDTH` and re-run `tests/test_session.py::TestHelpTabsAllFit`, which re-measures it. Either way update the `Input syntax` tab if the mode adds syntax, the `Mode 5 (Custom)` tab if it could also be expressed in the DSL, and cross-reference from `Overview` and `Worked examples`.
4. **Docs**: update the mode table in `README.md` and add a section to `docs/theory.md`. If the mode changes what a "measurement" is (rather than just which ports are terminated how), say so in both.
5. **Tests**: add a case in `tests/test_core.py` (or `tests/test_coupling.py` for anything probe-shaped) that builds the new termination set and asserts the result matches a hand-coded reference. Also add an "equivalence test" pinning that the new named mode produces identical results to a hand-built `TerminationSet`.
6. **Golden regression**: if the change touches `compute_z_matrix` at all, run `python -m unittest tests.test_golden_regression` and expect it green *without* regenerating `golden_legacy.npz`. Adding a fixture or a new mode does not require regeneration; a numeric drift in an existing mode means you broke something.

## How to add a new fit model

1. Add a dataclass `XxxFit` in `pkg_rlc/physics/solve.py` (and to `pkg_rlc.physics.core`'s re-export list) and a `fit_xxx(freqs, Z, f_min, f_max)` function. Use `_scaled_lstsq` if columns have very different magnitudes.
2. Add an `eval_xxx_model(fit, freqs)` helper for plot-overlay rendering.
3. Wire the model name into `fit_auto` selection logic if appropriate.
4. Add the option to the GUI `Fit Model` combobox in `App._build_global_controls` (`pkg_rlc/frontend/app.py`) and to the CLI `--fit` choices (`pkg_rlc/frontend/cli.py`).
5. Add tests that recover known parameters from synthetic Z data within tight tolerance.

## Don'ts

- **Do not pull in `scikit-rf`.** The custom parser is deliberate — it must handle EDA-tool quirks (renamed extensions, missing option lines, ambiguous port count) that scikit-rf does not.
- **Do not add `pandas`** for CSV writing; stdlib is sufficient.
- **Do not switch GUI frameworks.** Tkinter is required.
- `scipy.optimize` and `scipy.linalg` are acceptable; gratuitous deps are not.
