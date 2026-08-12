# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction — and, with more than one measurement port defined, the mutual coupling between them (M, k, M/L, C_c). Used for IC packages, EMX layout traces, DCO inductors, decap, and inductor-to-inductor pulling / spur budgeting.

`pkg_rlc_attrib.py` is a layer on top of that, not a mode: it takes one extracted `Z_ab` apart into the bare EM coupling plus one exact signed term per declared termination, and answers the exact what-if. It exists because the reduction assumption — everything unlisted is OPEN — moved a real answer by 6.07 dB with nothing on screen saying so.

## Module map

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc_core.py`       | Touchstone parser (+ `TouchstoneParseError` / `diagnose_touchstone` / `check_touchstone`), S<->Y, unified `TerminationSet` model + the Mode 5 DSL (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`), the connection-table row model (`MeasPortRow`, `ConnectionRow`, `rows_to_dsl_text`, `dsl_text_to_rows`, `build_terminations_rows`), `parse_mport_spec`, `resolve_meas_ports`, `compute_z_matrix` / `compute_z`, `extract_rlc_at_freq` / `extract_coupling_at_freq`, `fit_inductor` / `fit_capacitor` / `fit_auto`. |
| `pkg_rlc_attrib.py`     | **Port attribution.** Given `Y(f)` and a `TerminationSet`, answers three questions at one frequency. (a) An EXACT additive signed decomposition of `Z_ab` into the bare EM coupling plus one term per declared termination (`build_context` / `decompose` / `format_decomposition`). (b) The EXACT what-if of changing any of them (`sensitivity`, `group_joint`, `cumulative_curve`, `leave_one_out`, `sweep_mobius`, `transfer_ratio`). (c) The **cold-start screen** — which ports matter BEFORE a spec exists, from all-open: `cold_start_context` / `cold_start_bracket` / `cold_start_screen` / `cold_start_pairs` / `cold_start_leave_one_out` / `cold_start_cumulative` / `name_family_suggestions` / `cold_start_negative_result` / `cold_start_report` / `format_cold_start`, with `Bracket` / `PortScreenRow` / `PairEffect` / `FamilySuggestion` / `ColdStartContext` / `ColdStart` and the `COLD_START_*` constants. Plus `Element` / `Term` / `ReturnBudget` / `Decomposition` / `AttribContext`, the `DECOMPOSABLE` / `NON_DECOMPOSABLE` registries, the `Alternative` builders, `termination_impedance_diagonal` / `termination_impedance_shared_return`, `SIGN_CONVENTION_TEXT` and `AttribError`. Plus the **composed-network gauge** — `COMPOSED_BASELINE_TEXT`, `PortBlocks`, `BaselineLinks`, the `baseline=` argument every entry point takes, and `_island_elements`, the ungated structural warning for an element whose whole support sits in a probe-free component of the baseline. Imports `pkg_rlc_core` ONLY (acyclic, the `pkg_rlc_plot` rule), no scipy. `pkg_rlc_extractor.py` drives it from the `--attribute*` and `--cold-start*` flag groups (`--mode coupling` only); the GUI surface for (a) and (b) is `pkg_rlc_attrib_gui.py`, and (c) is CLI-only. |
| `pkg_rlc_compose.py`    | **Several Touchstone files measured as ONE network.** `ComposeInput` / `compose` / `ComposedNetwork` / `FileBlock` stack k files into one `Y` on a common frequency axis; every cross-file link is an ordinary `ShortPair` / `LumpedBetween` on the global indices handed to the SAME `compute_z_matrix`, so every mode, the Mode 5 DSL and the coupling path work on a composition without a line of their own. Plus the mandatory reference-node check (`reference_check`, `REF_LIVE` / `REF_WELDED` / `REF_NO_GROUND` / `REF_UNKNOWN`), the frequency plan (`align_frequencies` / `FreqPlan` / `interpolate_s`), the namespace (`COMPOSE_TAG_SEP`, `parse_scoped_ports`, `format_scoped_port`, `default_alias`, `link_short` / `link_lumped`), the pre-reduction (`reduce_block_y`), the Touchstone writer (`write_composed_touchstone`), the limit case (`limit_case_check`), `solve_composed` and `ComposeError`. Imports `pkg_rlc_core` ONLY. |
| `pkg_rlc_attrib_gui.py` | **The Attribution window** (`AttributionWindow`, `open_attribution_window`, `ATTRIB_MENU_LABEL`, `attribution_refusal`, `refresh_attribution_windows`, `attribution_session_state` / `apply_attribution_session_state`) plus the pure formatters it is testable through with no display (`render_table` / `Column` / `TableText`, `contributions_table`, `sensitivity_table`, `detail_lines`, `sweep_caption`, `reconciliation_verdict` / `reconciliation_line`, `provenance_lines`, `staleness_text`, `stability_line`, `report_text`, `csv_records`, `signed_str`, `parse_candidate`). A modeless `Toplevel` over `pkg_rlc_attrib`; `pkg_rlc_gui.py` holds only the Analyze-menu entry, the Traces right-click entry, the Results-pane pointer line and the refresh hooks. It is a separate module because `pkg_rlc_gui.py` was already 7000+ lines; `pkg_rlc_gui` imports it, and it imports `pkg_rlc_gui` back only from **inside functions** (`_gui()`), so the cycle never exists at import time. |
| `pkg_rlc_files_gui.py`  | **Which FILES a trace is made of** (round 3): the `Files in this trace…` window (`FilePairWindow`, `open_files_window`, `FILES_MENU_LABEL`, `files_refusal`, `refresh_files_windows`, `slots_of` / `FileSlot`, `spec_problems`), the port-cell scope rules (`render_port_cell` / `cell_scope` / `cell_is_foreign` / `port_choices` / `resolve_cell`, `ALIAS_MAX_CHARS`, `PORT_CELL_CHARS`) and the GUI rendering of the reference-node check (`reference_checks_of`, `reference_strip_text`, `reference_report_lines`, `reference_provenance`, `REFERENCE_HEADLINE`). Same split as `pkg_rlc_attrib_gui` against `pkg_rlc_attrib`: `pkg_rlc_compose` does every piece of arithmetic and this is presentation, budget and refusal. Both `pkg_rlc_gui` and `pkg_rlc_attrib_gui` import it at module level; it imports `pkg_rlc_gui` only from inside functions. |
| `pkg_rlc_plot.py`       | Matplotlib plot panel: multi-subplot grid over R/L/C/\|Z\|/Re/Im/Q/**k**, draggable freq marker, M / V / Delete keys, fullscreen window, and the `ReflowRow` / `reflow_rows` control strip that wraps instead of losing its tail. Quantities that cannot be derived from one `(freqs, Z)` pair (today only `k`) arrive via the optional `Trace.aux` dict. |
| `pkg_rlc_gui.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` hints and the `RowTable` / `ColumnSpec` row editor (measurement ports in modes 5+6, connections in mode 5), the `StylePicker` colour/linestyle palette, auto-apply (`_schedule_editor_sync` / `_flush_editor_sync`), per-trace plot visibility (`_replot_from_cache`), the port-overview / validation strips, the "Edit as text…" hatch (`_import_dsl_text`, `_editor_dsl_text`), the frozen-trace snapshot (`_freeze_trace_config`, `freeze_label`, `freeze_refusal`, the Traces-list right-click menu), the File menu and the JSON session format (`session_to_dict` / `session_from_dict` / `SessionError` / `autosave_path`), the results pane (a `ttk.Notebook` whose tab 0 is the Log, with `log_tab_label` / `_append_result(severity)` / `_select_results_tab`), and the immutable run record (`RowSnapshot` / `CouplingSnapshot` / `FitSnapshot` / `RunSnapshot`, `_snapshot_row` / `_snapshot_block` / `_snapshot_fit`) that `_render_results` consumes instead of live traces. Re-exports the DSL helpers it no longer defines. |
| `pkg_rlc_gui.py` (cont.) | Plus the **Ports & Roles** window (`PortRolesWindow`, `_trace_role_rows`, `_role_warnings`, `_roles_header`, `apply_ports_as`), which is what `Show Ports` now opens; and the **Attribution hooks** — the `Analyze` cascade, the third Traces right-click entry, `_on_attribution`, the Results-pane pointer line, and the `refresh_attribution_windows` calls. The window itself is `pkg_rlc_attrib_gui.py`. Plus the **multi-file schema and engine** (round 3): `TraceConfig.file_labels` and its helpers (`trace_file_labels` / `trace_file_aliases` / `trace_is_composed` / `trace_file_legend` / `trace_file_scope` / `compose_spec_problems`), the port-field scopers (`_scope_port_field` / `_scope_dsl_text` / `_scope_conn_rows` / `_scope_mport_rows`, `ComposeSpecError`), `SolveNetwork` / `_trace_network` / `_cached_trace_network` / `_namespace_network` / `_trace_namespace`, `_reference_checks`, `set_trace_home_file`, and the `Files in this trace…` entries on the Analyze cascade and on BOTH right-click menus. |
| `pkg_rlc_help.py`       | In-app Help window content (`HELP_TOPICS`, `HelpWindow`, `HELP_WINDOW_WIDTH`). One tab per mode + syntax + save/load + worked examples. **Ten tabs, and there is no room for an eleventh** — port attribution, the Attribution window and the cold-start screen all live at the bottom of `Mode 6 (Coupling)`, cross-referenced from `Overview`, `Input syntax` and `Worked examples`. See the measurement under "Port attribution". |
| `pkg_rlc_extractor.py`  | Entry point: dispatches GUI vs CLI from argv. CLI `--mode gnd \| p2p \| coupling`, `--mport` repeatable. |
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |
| `tests/test_run_parallel.py` | The RUNNER's own suite (57 tests, 0.25 s, no subprocesses, in `FAST_MODULES`): the contention rule (`max(1, min(budget, cores // live))` — sharing the *cores*, not the worker budget, because sharing the budget measured **slower** at two concurrent runs), the atomic registry write, the heartbeat and the stale-entry expiry. |
| `tests/test_parse_diagnostics.py` | The robust-reading work: what a file says about itself (span, sweep description, DC / \|S\|>1 notes) and what happens when it cannot be read. Every refusal test pins the **verdict** and the **line number**, not just "raises ValueError" — that would have passed before any of it existed. Plus the recovery cases (UTF-16, BOM, commas, `D` exponents, extension tiebreak) and the two GUI affordances. |
| `tests/test_session.py` | Save Config / Load Config / Restore Last Session. Pure round trip (no Tk) for the trace fields, the refusal verdicts, the hand-edit tolerance and the path precedence; Tk-driven for the App-level save→wipe→load, the missing-file path, the autosave, and that the File menu and its accelerators are reachable. Also the guard on the Help window's tab strip, which the tenth tab pushed past the old 950 px. |
| `tests/test_results_notebook.py` | The Results pane's `ttk.Notebook`: that the Log is tab 0, selected and MAPPED at startup (both are mechanical preconditions of tests elsewhere), the width-stable badge measured in the tab strip's own font, the unseen-warning count, the ERROR claim on the pane and the severity routing of the real call sites, plus the measured proof that a 30-tab strip does not move the left panel. Every guard mutation-checked. |
| `tests/test_plot_controls.py` | The plot panel's control strip: the pure `reflow_rows` wrap (no item is ever dropped, at any width), and — off a mapped window at 575 / 700 / 1040 / 1200 / 1500 px — that every control lies WHOLLY inside the strip, that it wraps only when it has to, that `place` keeps the strip's requested width out of `PlotPanel`, and that the layout settles instead of oscillating. The FIRST test in the repo to touch this panel. Every guard mutation-checked. |
| `tests/test_attrib_core.py` | Port attribution. The load-bearing one is the **reconciliation**: `decompose`'s sum against `compute_z_matrix` over every (spec, frequency) pair on every fixture, and every fast low-rank what-if (`sensitivity` / `group_joint` / `sweep_mobius`) against an HONEST recompute through a rebuilt `TerminationSet` — a Woodbury update that agrees with itself and with nothing else is the failure mode this module has. Plus the twelve requirements one by one: the reciprocity solve, the dense `Zt`, the singular-baseline fold, the structural rank check, the condition-aware residual floor, the return budget, the projection share, the refusal-by-name of non-decomposable quantities, non-additivity, the Möbius endpoints/interval/extremum, the sign convention, and the mode-1/2/3 ground-beats-probe precedence. Every guard mutation-checked. |
| `tests/test_attrib_vs_engine.py` | A deliberately INDEPENDENT second opinion on the one claim everything else rests on — that the node-space decomposition and the engine's Schur reduction are two routes to one number. It differs from the acceptance suite on purpose in four ways: it walks the case registry in `tests/_golden_capture.py` (so "every mode is covered" is a property of the walk, not a docstring claim) and anchors on the bit-exact `golden_legacy.npz` array rather than on the `compute_z_matrix` call `build_context` makes for itself; it computes its TOLERANCE here, from the file's own admittance slice, because comparing the module's residual against the module's own floor proves only self-consistency — which it would keep with both of them wrong; it pins requirement 12 STRUCTURALLY (which declaration became an element, which was thrown away), not only as a number that happens to agree; and it FUZZES — 4000 random specs over six fixtures, two-sided contract: either it agrees with the engine inside the condition-aware budget or the `Decomposition` says so out loud. |
| `tests/test_attrib_degenerate.py` | What the module does when the spec, the network or the data is BROKEN — the only interesting question here, because **every failure mode below produces a plausible number rather than an exception**: no DC reference (`cond(Y) = 2.5e16`) inverting to garbage; a redundant spec making `H` exactly singular (which must read as a spec bug, not as "unattributable physics"); an ill-conditioned baseline putting the decomposition's own sum 100% away from the engine with both numbers finite; independent-per-ball grounds reading **9.6 dB low** against the shared return real balls have; **eight** ground balls where every one-at-a-time and every pairwise measurement reads ~0 while the collective effect is **600x larger and the OTHER SIGN**; a ground inductance resonating with a package capacitance putting `M` outside the [ideal, open] bracket; one NaN in one S entry. It CONSTRUCTS each degeneracy — the repo's 2- and 4-port fixtures cannot express an eight-ball package or a resonant return — and checks against an honest rebuild through `compute_z_matrix`, so both sides come from shipped code. Every guard mutation-checked, with the defeating mutation named in each test's own docstring. |
| `tests/test_attrib_coldstart.py` | The cold-start screen (49 tests, 0.25 s, no Tk). The load-bearing one is the same as `test_attrib_core.py`'s: the closed form `-Zbase[a,p]Zbase[p,b]/Zbase[p,p]` against an HONEST re-solve through `compute_z_matrix` with a rebuilt `TerminationSet` — `1.47e-11` worst on the planted 12-port case, `<= 5.8e-11` over every fixture. Plus the bracket and its second opinion, the two coupling columns kept separate (the planted **red herring**: largest `\|Z_ap\|` in the file, negligible effect, ranks 5th of 8 by `\|dM\|` and 1st by `\|Z_ap\|` alone), the pair scan and the **shield** (`+9.689` / `+9.689` / `-870.268 pH`, 89.8x, sign flip), the mirror, the greedy curve's saturation, the family suggestion as a SENTENCE, and that the whole report is byte-identical with and without port names. |
| `tests/test_attrib_cli_coldstart.py` | `--cold-start` on the CLI (70 tests, 0.33 s). The flag refusals and that every `--cold-start-*` is inert without it (all three cap flags default to `None` so the check is EXACT, which `_attr_dependent_flags` cannot be); the printed ORDER, with `test_the_BRACKET_comes_before_the_RANKING` pinning that pair on its own; that `--attribute` and `--cold-start` together are allowed with cold start last; the `--cold-start-csv` `DictReader` round trip; and the line-width budget (widest line 95, the same as `--attribute`'s). |
| `tests/test_attrib_window.py` | The Attribution window IN ISOLATION (212 tests). Split in two on purpose: the pure formatters run with **no display** (the monospace table model, the width-stable sign pair, the reconciliation verdict, the provenance and staleness text, the CSV records), and the rest drives real Tk off a hand-built `TraceConfig` — the four refusals by name, the header `ReflowRow` budget, both PanedWindow starvation cases, the lazy sweep draw, that crossing the canvas does NOT steal focus from the frequency Entry (measured with `focus -lastfor`, not `focus_get()`, because the test process rarely owns WM focus under the parallel runner), the `[Recompute]`-not-auto-refresh contract, and that the swatch stays equal to `pkg_rlc_gui.RESULTS_SWATCH`. Plus the integration pass's own classes: `TestGesturesThatMustNotDiscardWork` (Escape from inside a field, and a click past the last row), `TestTheDetailPaneSaysWhatItRefused` (a refused candidate reaching a widget, the sweep not collapsing, the Candidates entry on screen at the minimum), `TestTheHeaderHearsAboutItsOwnText` (`ReflowRow.refresh`) and `TestTheDeclaredMinimumShowsContent`, which builds a SECOND App at `tk scaling 2.0` with every named font x1.5 and asserts the content is mapped at the enforced minimum, that the 100% minimum did not move, and that the layout SETTLES over eight resizes. Every guard mutation-checked. |
| `tests/test_attrib_gui_integration.py` | The same window END TO END through the REAL app, which is the other half: nothing here constructs a `TraceConfig`, a `FileEntry` or an `AttribResult` — the file goes in through `_on_add_file`, the measurement ports are typed into the real `RowTable`, and the window is opened through the menu entries a user clicks. It owns the JOIN, i.e. every defect where `pkg_rlc_gui`'s hooks and `pkg_rlc_attrib_gui`'s window are each right on their own: the number on the table against `pkg_rlc_attrib` called directly AND against the `M` the results pane printed down the separate `compute_z_matrix` path, the right-click SELECTING the row under the pointer first, every refusal by name, rule 6 in full, rule 11 (remove the trace / remove the file / load a session, each with the window open), the Results-pane pointer and its gate, and that none of it put a run or a result into the session file. **The window is MAPPED everywhere here** — a withdrawn root answers 0 to every geometry query and `Listbox.nearest()` then answers row 0 for every y, which is exactly the answer the right-click tests exist to rule out. |
| `tests/run_parallel.py` | **The test runner to use.** Class-sharded, longest-first. Measured when it was written: `python -m unittest discover -s tests` 293 s against `python tests/run_parallel.py` 108 s over the same 906 tests (2.7x). Re-measured after the composition work: **2045 tests / 364 shards in 333.7 s**, and `--fast` **976 tests in 5.5 s** over the eighteen no-Tk modules (the four composition ones were added — none imports tkinter, which is the one property that list has); `-m <substr>` picks modules by name. Sharded by CLASS not module because `test_run_history` alone is 86 s of the serial 293. Exit code 0 means every shard passed. NOT auto-discovered (no `test_` prefix). |
| `tests/test_freq_label.py` | Frequency-label honesty: the marker frequency a report prints says where the numbers came from. Both extractors snap to the nearest grid point via `argmin`, and the default 0.1 GHz marker on `diff_pair_4port.s4p` lands on 0.10099 GHz — every default session in the repo snapped and said nothing. |
| `tests/test_large_files.py` | How big a file the tool will read and what it says when it will not: the escalating port-count sniff past `MAX_SNIFF_NPORTS` to `SNIFF_HARD_CAP`, the refusal as a `TouchstoneParseError`, and the memory envelope. |
| `tests/`                | `unittest`-based suite (2045 tests run by `tests/run_parallel.py` at the time of writing, covering parser line-break/signed-zero edge cases, port range, mport specs, short groups, content sniffer, terminations, termination precedence, the connection-row model, named merged nodes, the `RowTable` widget and its per-kind layout, the Mode 5 editor, auto-apply / style picker / plot visibility, the session file, the ranked coupling report, fits, Schur fallback, the coupling matrix, degenerate probes, port attribution and its cross-check against the engine, the Attribution window, the cold-start screen, several files composed into one network and the composed-network attribution baseline, the bit-exact golden regression, and `reduce_snp`). |
| `tests/test_editor_autoapply.py` | The commit-step removal: WHEN the editor writes into a `TraceConfig` and WHICH one it lands on (the deferral, the object capture, the flush-before-selection-change), the style picker's storage / reachability / honesty about multi-curve traces, and that hiding a curve neither recomputes it nor destroys the cursors. Every guard here was mutation-checked. |
| `tests/test_connection_rows.py` | Row model: rows<->DSL round trip, the equivalence tests pinning that rows reproduce `build_terminations_mode1/2/3` *including* the ground-wins overlap the golden reference cannot see, and the reordering hazard that forces `_import_dsl_text`'s verbatim fallback. |
| `tests/test_row_table.py` | Drives real Tk widgets (skips cleanly with no display): `RowTable` add/delete/get/set/defaults/notification, the `mp1_*`->`mports` and `custom_text`->tables migrations, and that Duplicate shares neither row list. |
| `tests/test_conn_nets.py` | Named merged nodes and the parallel-stamp refusal (core's half of round 1): the measurements that prove the wrong number is real (10.000 fH typed reads 3.333 fH), every net-name rule with its own refusal, the forward-reference pre-pass, that a name resolves to ONE member and is therefore bit-identical to typing that member, the legacy `short_to` round trip, and that the refusal does not depend on Union-Find root ordering. Every guard mutation-checked. |
| `tests/test_compose.py` | `pkg_rlc_compose`'s arithmetic (80 tests, 0.16 s, no Tk, in `FAST_MODULES`): the weld (die return as a PORT gives 2.2501 nH and moves with the ground path; die return as the EM REFERENCE gives 2.1454 nH for grounded / open / 1 nH, bit-identical, spread 0.000e+00), the reference check's four verdicts, the frequency plan (identical-grid detection at a RELATIVE tolerance — a GHz file and a Hz file differ by 2.218e-16 and `np.array_equal` says False —, the refusal to extrapolate, the phase-step warn/refuse at 20/60 deg), the namespace, the pre-reduction (316 → 22 ports: 2.6 ms against 4486 ms, **1714x** per re-solve, agreeing to 4.290e-15), the export (digits 17 reproduces S exactly; the n==2 column-major quirk, which needs a deliberately NON-reciprocal 2-port because every passive network has S12 == S21 and the transpose is otherwise invisible) and the limit case. Every guard mutation-checked. |
| `tests/test_compose_cli.py` | The composition command line (71 tests, 0.58 s, no Tk, in `FAST_MODULES`): every `--compose-*` refusal by TOKEN (exit 2 alone is also argparse's answer to an unrelated typo), the port namespace surviving in and out — including through a `pkg_rlc_core` message that knows nothing about files —, the numbers being the ENGINE's, the reference check as mandatory output, the proposal that prints and stops, and `TestComposedAttributionBaseline`, which pins R2-8 as a capability: the package-internal element measured at **exactly 0j with a 1.70e-13 residual** under no gauge, and non-zero under it. Every guard mutation-checked. |
| `tests/test_attrib_composed.py` | The composed-network gauge inside `pkg_rlc_attrib` (45 tests, 0.06 s, no Tk, in `FAST_MODULES`): a 12-port construction where the package is in series in the SHARED RETURN (a fully differential probe pair injects zero net current, `1ᵀw_b == 0`, so it can only reach a common-mode-only package through asymmetry — an obvious 6-port sketch gives a fixture where every test passes and nothing is measured), the ball-grounded / ball-open engine bracket (704.70176729 pH vs 1.0837047531 nH, **−3.7381 dB**), the exactly-zero-with-a-healthy-residual failure, the bare term against an ENGINE re-solve with the balls removed (2.481e-15 relative), the cold start with and without the gauge, and `_island_elements` fuzzed over 2993 specs on every fixture. Every guard mutation-checked. |
| `tests/test_multifile_session.py` | The multi-file SCHEMA (R3-1): that a trace can name several files without moving anything about naming one. Byte identity of a single-file trace's JSON pinned against a literal captured from the build BEFORE the change (key order included); the two tag authorities (`pkg_rlc_gui` and `pkg_rlc_files_gui`) pinned against each other; the list-aliasing trap on the THIRD field (`file_labels`, after `mports` and `conn_rows`); `_config_signature` unmoved for every pre-composition trace; and `TestCalculateComposesTheFiles`, which is where the engine half is asserted from the App side. Every guard mutation-checked. |
| `tests/test_multifile_table.py` | The file WINDOW and the port-cell budget (R3-2 / R3-3 / R3-5): the measured cell (72 px / 7 chars at 100%, 135 px / 7 chars at 150% — the character count is what is DPI-stable), the alias refusal quoting the measured FRACTION rather than a digit count, the default-scope rendering, the footer's pack order at a floor where the tree actually unmaps, and the reference strip in the Attribution window (not packed at all when there is no composition — an unmanaged `ttk.Label` still answers `winfo_reqheight() == 21`). Every guard mutation-checked. |
| `tests/test_multifile_engine.py` | What Calculate DOES with several files, and the surfaces that have to say so: the namespace and its sticky-scope rule pinned against `parse_scoped_ports` on everything that one accepts, the two namespace builders pinned against each other, the composed frequency axis reaching the plot / the CSV / the marker snap, R3-5 reaching the Log and the run page exactly once, and R2-8 inside the Attribution window (the package element must not come back as exactly 0). Every guard mutation-checked. |
| `tests/test_conn_rowshape.py` | Per-kind row shape and the footer route (the GUI's half of round 1): `conn_table_layout`'s two rules over all 63 kind subsets (a cell never spreads under someone else's heading, and it spreads whenever it may), the measured table widths, the short-group cell shim's `TerminationSet` equivalence, the R1-5 consequence tiers, and — Tk-driven — that clicking the footer reaches a row past the table's OWN scroll and that it costs zero pixels. Every guard mutation-checked. |
| `tests/test_mode5_editor.py` | Stage 3: the pure text<->rows import decision and both strip renderers, plus Tk-driven editor wiring, per-mode widget visibility, the text hatch, the CSV gate, wheel routing, and the LAYOUT numbers (`ismapped` / `reqwidth` / `xview` / `scrollregion` / `sashpos`) measured off a mapped window — including that a mode with no table fits the 431 px canvas outright and every mode shows the same editor height at the minsize. |
| `tests/test_freeze_trace.py` | "Freeze as new trace": the pure copy rules (config copied, lists element-wise, results REFERENCED), the two refusals (Calculate skips it, the editor cannot write it), the two *entry* refusals (`freeze_refusal` — no numbers, and a STALE spec), the `freeze_label` budget that keeps the `<HH:MM>` stamp inside `MAX_LABEL_LEN`, that everything else still works (plot / show-hide / CSV / Remove), that the CSV does not attribute a snapshot's numbers to the newest run, the right-click menu, and the session round trip that comes back without numbers and says so. Every guard mutation-checked. |
| `tests/test_port_roles.py` | Port names put to work: the pure classifier (`port_roles`), the provenance map (`row_sources`), the run-collapser, the open-port name check with its false-alarm cases run against every real fixture, `_trace_role_rows` (any mode → rows), and the Tk-driven Ports & Roles window — filter, sort-on-the-raw-value, both Treeview hazards, the flagged rows (with the probe-and-ground message following the MODE), and the collapsed-range write-back. Every guard mutation-checked. |
| `tests/test_run_history.py` | The run tabs: the width-stable label pair measured in the tab strip's own font, the three header lines, the named-signature diff (pinned one-for-one against `_config_signature`), and — Tk-driven — that eviction touches only the auto ring, that the kept cap bites at Keep time and the button says so, that the page being read survives as the SAME widget, the widget-count leak guard after a churn loop, the conditional auto-switch in all three directions (including that a KEPT page is never yanked away), the units re-render creating no tab and reaching EVERY page, the Keep button's readability at 150% font scaling, and that no run reaches the session file. Every guard mutation-checked. |
| `tests/test_run_snapshot.py` | The immutable run snapshot: that the rendered page is byte-identical to `tests/fixtures/render_reference.json` (captured before the refactor), that a record does not move when its `TraceConfig` is relabelled / renumbered / re-ported, that no per-frequency array is reachable from a run, and the run number / frozen-visibility rules. Every guard mutation-checked. |
| `tests/_render_capture.py` | Script + case registry that (re)generates `render_reference.json`, and the ONE place that knows the renderers' signatures. NOT auto-discovered (leading underscore). Regenerate ONLY in the same commit that justifies moving the reference. |
| `tests/test_report_readability.py` | Four display-only changes, none of which touches a number: the ranked / floored coupling list (pure — the key, the two things never hidden, the sign invariant), the coloured trace Listbox, the tagged results-table swatch, and the editor's footer summary line (mapped window at the 1040x600 minsize). Every guard mutation-checked. |
| `tests/generate_test_snp.py` | Builds synthetic fixtures with analytically known R/L/C/M; run as a script to (re)generate `tests/fixtures/`. The `COUPLED_*` module constants are the single source of truth for the coupled-coil fixtures. |
| `tests/test_golden_regression.py` | Replays `tests/fixtures/golden_legacy.npz` through the current API and asserts `assert_array_equal`. This is the guard on every "stays bit-identical" claim below. |
| `tests/_golden_capture.py` | Script + case registry that (re)generates `golden_legacy.npz`. NOT auto-discovered (leading underscore). Regenerate ONLY in the same commit that justifies moving the reference. |
| `tests/_smoke.py`       | Manual sanity-check script (NOT auto-discovered by `unittest`). |

## Critical invariants (do not regress these)

- **All Listboxes set `exportselection=False`.** Without it, clicking an Entry/Spinbox steals the X selection and clears the highlight. The editor resolves its auto-apply target from that selection, so a cleared highlight means every keystroke is silently discarded.
- **Auto-sync editor on Calculate.** Before computing, flush any queued sync and push current editor fields into the selected trace. Auto-apply usually got there first, but a keystroke in the same event burst as the click is still in the idle queue.
- **Truncate trace labels to 30 chars** in plot legends, or subplots squeeze.
- **Log-scale drag tolerance.** The freq-marker drag detector must use log-space distance when the x-axis is log.
- **Canvas focus.** After every `FigureCanvasTkAgg`, call `canvas.get_tk_widget().focus_set()` so M / V / Delete keys are received (also in the fullscreen `Toplevel`).
- **Port indices: 1-based at the GUI/CLI boundary, 0-based inside core.** Convert in the `build_terminations_*` builders, never deeper.
- **Schur reduction uses `np.linalg.solve`** (not explicit inverse). On `LinAlgError` or pathological condition, fall back to `np.linalg.lstsq` and emit a warning naming the offending frequency.
- **Auto-create a default trace on file load.** Don't make users hit "Add Trace" for the basic workflow.
- **Y-axis log uses `symlog` with `linthresh=1e-6`** to handle data crossing zero.
- **R / L / C / Q are reported with their physical sign (Cadence convention).** `extract_rlc_at_freq`, the plot's `trace_y_values`, and both CSV exporters must NOT clip negative values to NaN. Q is `Im(Z)/Re(Z)`, not `|Im(Z)|/Re(Z)`; `L = Im(Z)/ω` and `C = -1/(ωIm(Z))` go negative past/below SRF respectively. The GUI results pane appends a brief annotation when a value is negative — keep that in sync if formulas change.
- **Multi-file comparison.** Each `TraceConfig` independently selects its file and port config — two traces can reference different files and plot together.
- **`PlaceholderEntry.get_value()` returns `""` when the placeholder is showing.** Never read `_var.get()` directly to fetch user input — placeholder text would leak in. Same rule for `PlaceholderText`.
- **The parser must split comment / option lines on the exotic line breaks too.** `str.splitlines()` — what the parser used before it streamed the file — breaks on `  -     `; iterating a text-mode file object breaks only on `
`. A header page-broken with a form feed would otherwise swallow the data record that follows it, silently dropping frequency points. Only `#`/`!` lines (and the tail of a mid-line `!` comment) need the check — every one of those characters is whitespace to `str.split()`, so data lines tokenise correctly either way. That is also why the hot path stays free of a per-line `splitlines()`.
- **The RI fill normalises signed zeros.** `np.add(body[...,0], 0.0, out=s.real)` rather than a plain assignment: real EDA exports write `-0.000000e+00`, and the historical `body[...,0] + 1j*body[...,1]` turned those into `+0.0`. `assert_array_equal` cannot see the difference (`-0.0 == 0.0`), so the golden reference does not guard it — `tests/test_core.py:TestParserSignedZero` does. Measured cost of the fused add: +2%.
### Reading files (robustness, diagnosis, refusal)

- **A non-numeric token in a data line is a HARD ERROR, not a skipped token.**
  The old parser dropped it and warned. Touchstone is a positional stream, so a
  dropped value shifts every later value by one slot: the frequency column
  starts reading S-parameters, and the file either fails the divisibility check
  with a meaningless message or — worse — still divides evenly and yields a
  plausible wrong answer. `lenient=True` (`--lenient`, and a button on the GUI
  error dialog) restores the old behaviour for people who know what they are
  doing; it is not the default and its warnings say the result is suspect.
- **Every failure is a `TouchstoneParseError` carrying a `kind`.** FAULT_FILE /
  FAULT_UNSUPPORTED / FAULT_ACCESS / FAULT_INTERNAL, rendered as a **verdict**
  line. That is the whole point of the class: "is my file bad or is your tool
  bad?" is the first question a parse failure has to answer. It subclasses
  `ValueError` (what the parser raised before) and `str(e)` IS the full report,
  so existing `except Exception as e: show(e)` call sites upgrade for free.
  Nothing escapes `parse_touchstone` as a bare traceback — an unexpected
  internal exception becomes FAULT_INTERNAL *with the diagnosis attached*, and
  only after the diagnosis agrees the file is consistent.
- **The bookkeeping for good error messages lives in a SECOND PASS, never on
  the hot path.** `_diagnose` re-reads the file with a line number and token
  count per data line and is what turns "token count 3603 not divisible by 9"
  into "the file ends mid-record at line 408". It runs only on failure or when
  the user asks (`Check File` / `--diagnose`), it must never raise
  (`_safe_diagnose`), and its `headline` overrides the caller's when the caller
  has a worse one — the sniffer can only ever say "could not infer port count",
  which on a truncated file sends the user to force a port count that was never
  wrong.
- **`FAULT_NONE` means the diagnosis found nothing wrong**, and it is what
  `--diagnose` turns into exit code 0. Do not fold it into FAULT_INTERNAL: the
  parse path maps NONE -> INTERNAL itself, because "the file is fine and we
  still failed" is our bug, but a standalone check needs to be able to say
  "fine" without accusing anyone.
- **`data_notes` is not `parser_warnings`.** Warnings mean "I guessed, or I
  threw something away"; notes mean "the file is fine, here is what is in it"
  (DC point, `max |S| > 1`, irregular sweep). The split is also load-bearing
  for the golden reference, which pins `parser_warnings` element-for-element —
  a new descriptive check in that list would force regenerating
  `golden_legacy.npz`, which is exactly what must not happen.
- **The encoding is sniffed; the file is no longer opened blind as UTF-8.**
  `errors="replace"` turned a UTF-16 export (real EDA tools write them) into a
  wall of skipped tokens, and a UTF-8 BOM glued itself to the leading `#` so
  the option line was never recognised and the file silently parsed as
  `# GHZ S MA R 50`. Compressed/binary files are refused by magic number rather
  than misread. Measured cost: 24 µs.
- **Descriptive checks (`_check_freq_axis`, `_check_s_values`) are below the
  noise floor** — the `|S|` scan is 0.6 ms of a 120 ms parse on a 16 MB file,
  and it is chunked by `_freq_batch` because `np.abs` on a whole
  (5000, 153, 153) array allocates ~1 GB. Keep it chunked.
- **The extension is a TIEBREAK and a LAST RESORT, never the primary source.**
  Content-sniffing stays first (EDA tools rename these files constantly), but
  picking the smallest of several candidates silently read a 2-port file as a
  1-port one, and nothing above `MAX_SNIFF_NPORTS = 256` could be opened at all
  — a `.s300p` package export is the normal case this tool exists for. Both
  uses emit a warning naming what happened.
- **Touchstone 2.0 is refused, in lenient mode too.** Read as v1, the numbers
  inside `[Number of Ports] 4` land in the data stream and shift everything
  after them. "Skip the bad tokens" is precisely the wrong answer here, so
  `_recover_data_line` checks `_V2_KEYWORD_RE` before anything else.
- **`_decode_options` returns its unrecognised tokens.** A misspelt format
  keyword used to fall through to the `MA` default in silence, which reads RI
  data as magnitude/angle and produces a well-formed, completely wrong file.
- **`Check File` is the FOURTH button in the Files row** — and `pack` unmaps
  from the end, so that is not free. Measured at the 1040x600 minsize: the row
  needs 364 px and has 448. `tests/test_parse_diagnostics.py::
  TestGuiFileChecking::test_check_file_button_is_on_screen_at_minsize` asserts
  `winfo_ismapped()` on all four; re-measure before adding a fifth.
- **`FileEntry.info_str` puts the frequency span BEFORE M and Z0.** A Listbox
  has no horizontal scrollbar, so a long file name clips the tail of that line
  (measured: a 37-char name needs 476 px against a 444 px list). Of the four
  facts on it the span is the one worth keeping.
- **Touchstone v1 quirk for n=2.** The 2-port column order is `S11 S21 S12 S22` (column-major), but n>=3 is row-major. `parse_touchstone` transposes only when `nports == 2`. `tests/generate_test_snp.py:write_touchstone` writes the matching column order on output. Don't "fix" either side without fixing the other.
- **Capacitor fit needs `_scaled_lstsq`.** The Im(Z) design columns `omega` and `-1/omega` differ by ~1e20 in magnitude; raw `np.linalg.lstsq` kills the small singular value and reports `C=1e41`. The column-rescaling helper in `pkg_rlc_core.py` is load-bearing -- don't remove it.
- **SI suffix `M` is Mega (1e6), not milli.** Milli is lowercase `m`. Used in Custom Mode lumped-value parsing and exposed in Help → Input syntax.
- **Mode 3 short-group syntax.** `1-2-3-4` is a single group of 4 ports tied together (parser emits chained binary pairs `(1,2),(2,3),(3,4)` which Union-Find inside `compute_z` merges). Don't simplify the parser into "exactly two ports per group".
- **Mode codes are stable and are never renumbered.** 1, 2, 3, 5, 6 are live; **4 is retired** (`A ↔ B + VDD/GND`) because for AC small-signal VDD *is* an AC ground. A mode-4 trace migrates to mode 2 with its VDD ports unioned into GND (`TraceConfig.migrate_legacy_mode`), `TraceConfig.vdd_ports` stays as a field so old configs still load, `build_terminations_mode4` stays as a re-export, and `--vdd` on the CLI is a deprecated alias that unions into `--gnd` and prints a note. Do not reuse code 4 and do not delete the `Vdd` termination class — it documents intent and is evaluated as `Ground`.

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
- **`RECIPROCITY_WARN = 1e-3` lives in `pkg_rlc_core`** and is imported by both `pkg_rlc_gui` and `pkg_rlc_extractor`. They used to disagree (1e-3 vs 1e-12), so the same file got opposite verdicts and the CLI cried wolf on every real EM file. The metric skips non-finite off-diagonal entries so one undefined measurement port cannot poison it.
- **`M/L` is the Norton injection ratio, NOT the current-transfer ratio.** The exact ratio into a shorted port `a` is `I_a/I_b = -Z_ab/Z_aa`; `M/L_a` equals its magnitude only where `w*L_a >> R_a` (1098% apart at 10 MHz for `L=2n, R=1.5`). The label is "coupling ratio" everywhere — core docstring, CLI report, GUI legend, Help, README, theory.md, and `pkg_rlc_attrib`'s `DECOMPOSABLE` entry. Keep the six in sync. `pkg_rlc_attrib.transfer_ratio` is where the EXACT ratio is available as a number rather than as a caveat.
- **The GUI's pair list is RANKED by `max(|M/L_a|, |M/L_b|)` and floored at `COUPLING_FLOOR_DB = -60`.** Six measurement ports make 15 pairs, and nested-loop `(a, b)` order carries no information about which of them matter. `|k|` alone is the wrong key: `|k| = 0.02` between two 2 nH coils and between a 2 nH and a 500 pH coil are different problems — same `M`, 4x the injection into the small one. `rank_coupling_pairs` is pure and mutation-checked, and **magnitude appears there and nowhere else** — every printed cell stays signed. Three rules are load-bearing: `_pair_strength` is computed **linearly**, not from the `*_dB` fields (`_ratio_db(0)` is NaN, and a pair with `M = 0` is the weakest there is, not an undefined one); a pair with an **undefined** ratio sorts last and is **never** folded away (NaN is a missing measurement, not a small number); and the **strongest** pair is never folded away either, or a block can consist of nothing but "3 pairs were too weak to list". The `(see Export CSV)` pointer is true because `_write_coupling_csv` enumerates every unordered pair straight off the Z matrix and has no floor — do not give it one.
- **`compute_z` is a thin wrapper returning `Zmat[:, 0, 0]`** — the self impedance of the FIRST measurement port, and a strided **view**, not a fresh contiguous array. Copy before writing into it or before handing it to code that assumes C-contiguity (the GUI does `np.ascontiguousarray`).
- **`tests/fixtures/golden_legacy.npz` is the guard for all of the above.** It pins `parse_touchstone -> s_to_y -> compute_z` bit-for-bit for every fixture and for representative Mode 1/2/3/4/5 cases. If it fails, the reduction path changed: fix the change, do not regenerate the reference to make the test pass.
- **The Mode 5 DSL and its helpers live in `pkg_rlc_core.py`** (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`) — terminations belong to core. `pkg_rlc_gui.py` re-imports them so `from pkg_rlc_gui import parse_si` and friends keep resolving; keep that re-export list intact.
- **DSL signal syntax is `<port> signal <groupname> [+|-]`.** Group names are arbitrary strings; the sign is a **separate whitespace token** defaulting to `+`, and anything other than exactly `+` or `-` raises. A name whose `.upper()` is `A` or `B` is upper-cased so legacy `signal a` / `signal b` keep working. There is deliberately **no** "signal group must be A or B" validation any more, in either `compute_z_matrix` or the DSL — don't reintroduce it.

### Port attribution (`pkg_rlc_attrib.py`)

Design note: `docs/design_port_attribution.md`. Theory: `docs/theory.md` §13
(and §13.14 for the cold-start closed form). User docs: Help → Mode 6 →
"Where the number came from", and the README's "Port attribution" section.
`tests/test_attrib_core.py`, `tests/test_attrib_vs_engine.py` and
`tests/test_attrib_degenerate.py` are the guards, and every claim below was
mutation-checked.

**All five stages are shipped.** 1-3 are the engine, the sensitivity / Möbius
layer and the CLI report (`--attribute` and its flag group in
`pkg_rlc_extractor.py`, `--mode coupling` only). Stage 4 is the `Toplevel` —
`pkg_rlc_attrib_gui.py`, and see "The Attribution window" below for its own
rules. Stage 5 is the cold-start screen, which is in `pkg_rlc_attrib.py` and
is **CLI-only** — see "The cold-start screen" below. The CLI-before-GUI order
was deliberate and is worth keeping in mind for anything added here: the
output of this feature is a table and a paragraph, both of which a CLI can
print, and every pixel in the GUI is already spoken for by the measurements
elsewhere in this file.

**Why it exists at all.** The user extracted `M` between the same two coils
from the same EM solve twice and got 1.71 pH and 3.44 pH — **6.07 dB apart**,
both correct. 6.1 dB of that was the grounding assumption and 0.6 dB the
frequency marker. `compute_z_matrix` returns the OPEN-circuit matrix and
everything unlisted is open; that convention was stated in `theory.md` §8.5
and **nowhere on screen**. Every rule below is there because the alternative
was measured and was worse.

- **`Zbase`, never `Z0`.** `Z0` already means the reference impedance
  everywhere in this repo. The collision is a real bug source, not a style
  preference.
- **The baseline is: probe sides merged, EVERY other port OPEN.** Nothing else
  — no ground, no short, no lumped element. Every non-probe declaration is an
  element on top of it. Change that definition and every term changes, which
  is why the report names the baseline it used on every line of output.
- **`Zt` is the element IMPEDANCE matrix, so an ideal element is `0` and NO
  INFINITY EVER ENTERS THE ARITHMETIC.** The Woodbury identity in the `D = Zt^-1`
  form has `D = inf` for an ideal ground; in the `Zt` form `H = Zt + G` is well
  conditioned whenever `G` is. `H` is also the only matrix inverted on this
  path, which is what makes `cond(H)` the right thing to gate the tolerance on.
- **`r_a` is ITS OWN SOLVE, never `p_a`, and the transposes are plain `.T`.**
  Reciprocity is not assumed. The user's real file sits at `3.41e-10`, a
  thousand times the residual this feature advertises, so aliasing them would
  silently spend the whole error budget. `.conj().T` is the easy numpy slip and
  is simply the wrong operator: `Y` is complex-**symmetric**, not Hermitian.
  `|r_a - p_a| / |p_a|` is reported as a diagnostic. The repo has **no fixture
  that can catch this shortcut** — that is precisely why it is written down.
- **`Zt` MAY BE DENSE, and the default ground topology must not be diagonal.**
  Real package ground balls share a return plane. `N` independent `z` in
  parallel is `z/N`; `N` balls sharing one `z` is `z`, so independent
  per-lead inductors understate the common-mode return inductance by
  `(1 + (N-1)k_ret)` — `4.8x`, i.e. **13.6 dB**, at 20 balls with a realistic
  `k_ret = 0.2`, and it GROWS with the ball count. Measured on three different
  networks: **9.60 dB** (review,
  synthetic 4-ball), **8.09 dB** (design note §5.2, independently constructed
  4-ball), **6.03 dB** (`diff_pair_4port.s4p`, `agg=1`/`vic=2`, grounds 3+4,
  5 GHz: 1.0120 nH independent vs 2.0259 nH shared). Every one **larger than
  the 6.07 dB dispute this feature exists to settle**, monotone in `k_ret` with
  no threshold, so there is no safe default and the module refuses to pick one:
  `termination_impedance_diagonal` and `termination_impedance_shared_return`
  are both explicit. `H = Zt + G` takes a dense `Zt` with zero math change and
  zero cost change. The same physics is spellable in Mode 5 TODAY with no new
  code — one `short_to` row tying the set, one `lumped_to_gnd` on any port of
  it — and the two spellings of *which* port carries the inductor are
  **bit-identical** (measured, `==`), because the set is one node by then.
- **The reconciliation compares TWO ALGORITHMS ON ONE NETWORK, so it is always
  taken on the DECLARED configuration — never on a what-if.** `ctx.Zop_declared`
  exists for exactly this and is the left-hand side of the residual whatever
  `zt` is in force. Comparing the what-if's answer against the engine's value
  for the declared spec compares two *networks*, and the difference is one the
  caller asked for: measured on `diff_pair_4port.s4p` (probes 1/2, grounds 3/4,
  5 GHz), a shared 1 nH return doubles M, so the residual read **1.01**, sailed
  past `RESIDUAL_CATASTROPHIC`, emptied `terms`, and printed *"the two
  algorithms disagree about the answer itself"* about **2.026 nH, which was
  right** — i.e. requirement 2's headline feature could never produce the split
  it exists for, at the setting that matters most. A plain `diagonal` `zt` was
  the quieter half: 0.2% is under the gate, so the table survived and the
  spurious `Reconciliation:` warning printed anyway. Do NOT "fix" this back by
  exempting `zt` from the gate — the arithmetic is still checked, on the
  declared configuration, which is one extra `O(m^3)` solve and is the check
  that was always meant.
- **What a dense `Zt` genuinely loses is the second OPINION, not the check, and
  `reference_applicable` is how that is said.** `compute_z_matrix` cannot be
  handed a dense element-impedance matrix at all — a shared return is a mutual
  impedance between ground leads and the DSL has no node to hang one on — so
  `total_reference` stays the **declared** spec's answer and is re-labelled
  *"compute_z_matrix, DECLARED spec — a DIFFERENT network"* rather than printed
  under a heading that claims to be the same measurement. `ctx.notes` says
  compute_z_matrix was NEVER ASKED ABOUT THIS NETWORK. Both halves matter:
  dropping the engine's number hides a comparison the reader wants, and leaving
  it unlabelled is a lie.
- **A SINGULAR baseline auto-recovers; it must never refuse.** Measured:
  `coupled_4port_float.s4p`, the repo's flagship Mode 6 example (used in
  `theory.md` and the README), has `cond(Y) = 2.5e16`, so `inv(Ybase)` does not
  exist and a naive implementation is red on day one. Recovery is automatic and
  introduces no new user concept: SVD `Ybase`, partition elements by whether
  `u_e` is in `range(Ybase)` using core's existing `PROBE_RANGE_TOL`, fold the
  OUT-OF-RANGE ones into the baseline, Woodbury the rest, and **report by
  name** — "Port(s) X are IN THE BASELINE because the structure has no
  reference without them". A folded element has no term of its own, which is a
  gauge change (see below) and is why naming it is not optional. Measured with
  one `4 lumped_to_gnd R=50` on that fixture: effective cond `7.3e15 -> 5.7`.
  With no elements at all there is nothing to fold, so `Zbase` is a `pinv` and
  the balanced `+/-` probe is orthogonal to the common-mode null direction —
  exact, residual `0.0`, effective cond `2.2`. Same argument as §8.4.
- **STRUCTURAL rank check BEFORE any conditioning check.** A rank-deficient `U`
  from a redundant spec — one port written `ground` twice through overlapping
  ranges, a `short_to` between two already-grounded ports — is a **SPEC BUG**,
  and reporting it as "genuinely unattributable physics" is the worst available
  outcome. Test it on integer port-index sets first and NAME the offending
  elements; only then look at `cond(G)`. Elements whose `u` is the zero vector
  after probe-side merging are dropped as already inert — the same class
  `inert_lumped_messages` reports on the Mode 5 strip.
- **Reconciliation DEGRADES, never refuses outright, and its tolerance is
  CONDITION-AWARE.** The authoritative total is always `compute_z_matrix`'s;
  the decomposition's own sum is the check. Measured cross-algorithm agreement
  is `3e-16` on a trivial 4-port and `~1e-7` at best on a 153-port file with
  `cond(Ybase)*cond(G) ~ 1e7-1e9`, so a **fixed `1e-9` gate would refuse
  exactly the files this exists for**. The gate is
  `RESIDUAL_SAFETY * (cond(Ybase) + cond(H)) * eps * (S / |Z_ab|)` and the
  `S/|Z_ab|` factor is NOT decoration: measured on `diff_pair_4port.s4p` at
  1 MHz, `cond(Ybase) = 1.3e10` and `cond(H) = 1` give a cond-only bound of
  `4.5e-5` while the actual disagreement is `0.25` — an inverse is accurate
  relative to its LARGEST entry, and there the largest entry of `Zbase` is the
  1 fF port capacitance's 159 kΩ while the answer is a 6 mΩ mutual. Report the
  residual AND its achievable floor; only withhold the per-element **split**
  when the residual is catastrophic, and **never** the total.
- **The RETURN-PATH BUDGET is always reported, and it is what stops the output
  being over-read.** The EM model's reference plane is not a port, so no
  declaration reaches it. Report `|1^T Ybase V|` against `sum|I_e|`. Measured
  on `diff_pair_4port.s4p` with both far ends grounded the declared elements
  carry 99.41%, but on the representative package case the split was **0.05%
  declared / 99.95% inside the EM model** — so the decomposition **cannot**
  confirm or refute a "forward path minus return path" hypothesis and must
  print that in words rather than let a user infer a null result from small
  numbers.
- **The SHARE of a complex term is not a complex ratio.** Report the signed
  projection `Re(term * conj(total)) / |total|^2` PLUS a separate quadrature
  component. A term at 90° to the total inflates any magnitude-based
  cancellation measure while being harmless. Suppress the share column
  entirely, **with a named reason**, when `|total|` is near zero — including
  when it is smaller than the reconciliation residual, because a total smaller
  than our own error bar is not a total.
- **ONLY DECOMPOSE WHAT IS DECOMPOSABLE, and refuse the rest BY NAME.** A
  quantity decomposes iff it is (fixed real scalar) x (R-linear functional of
  `Z_ab`) at ONE configuration. YES: `Z`, `ReZ`, `ImZ`, `M`, `M/L_a`, `k`. NO:
  `C_c = -1/(omega*Im Z_ab)` (a reciprocal — superposition adds impedances, not
  their inverses), `Q` (a ratio of two decomposable things), `|Z|` (a norm),
  anything in dB. **`C_c` is a first-class output of this tool** and is the
  right reading whenever `Im(Z_ab) < 0`, so it must still be shown — as a
  TOTAL only, never per term — `Decomposition.C_c_total` and one line in
  `format_decomposition`, headlined when `Im(Z_ab) < 0`. The refusal names the
  quantity and the linear one to ask for instead; "unsupported quantity" would
  send the caller hunting for a typo, and a refusal pointing at a facility that
  does not exist is worse still.
- **"At ONE configuration" means fixed WITHIN one evaluation, NOT frozen at the
  declared spec.** `M/L_a` and `k` divide by `L_a` (and `L_b`), and those are
  properties of the NETWORK — every sensitivity row, group, cumulative point
  and leave-one-out row is a different network. `_scale_from` therefore takes
  the scale from the `(G, G)` matrix of the configuration being evaluated;
  `_quantity_scale` is the wrapper that supplies `ctx.Zref` for the DECLARED
  spec, so `decompose(..., "k")` still means byte-for-byte what the results
  pane and the CSV print. Measured on `diff_pair_4port.s4p` at 5 GHz, opening
  `ground port 3` takes `L_a` from `+5.026 nH` to `-505.3 nH`, so the frozen
  scale reported `M/L_a = +0.100227` where the truth is `-0.000996976` —
  **sign flipped, 100x** — and `k = +0.100227` where `L_a < 0` makes `k`
  genuinely NaN by `extract_coupling_at_freq`'s own rule. On the first row of
  the default scan. **`sweep_mobius` REFUSES `k` and `M/L_a` by name**
  (`_SWEEP_REFUSED`): a curve has no single configuration to take a scale from,
  so the only thing it could deliver is that same bug drawn as a graph.
- **Sensitivity must include GROUP-LEVEL and CUMULATIVE, not only per-port and
  pairwise.** With 60 ground balls every single-port delta is ~0 (the other 59
  already carry the return) and so is every pairwise second difference: the
  collective effect is order-60. Even at `m = 2` it bites — measured on
  `diff_pair_4port.s4p`, opening ground 3 alone is `-506 pH`, ground 4 alone is
  `-506 pH`, and **both at once is `-759 pH`, not `-1012 pH`**: non-additivity
  `+254 pH`, a third of the effect from two elements. So: per element, per
  GROUP (a whole connection-table row at once — the rows already define the
  groups, so this is free), the non-additivity for groups AND pairs, a greedy
  cumulative curve at `k = 1,2,4,8,16,…`, and leave-one-out from all-grounded.
  **Every fast low-rank result MUST be verified in tests against an honest
  recompute through `compute_z_matrix` with a rebuilt `TerminationSet`. That is
  the single most important test in the file** — a Woodbury update that agrees
  with itself and with nothing else is this module's characteristic failure.
- **The series-L sweep is a CLOSED-FORM MÖBIUS MAP, not a loop.** `z` enters
  `H` in exactly one entry, so `Z_ab(z) = (alpha + beta*z)/(gamma + delta*z)`:
  exact endpoints (`z=0` ideal, `z=inf` open), the whole interval in closed
  form, and the extremum over `[0, inf)` analytic (a Möbius map takes the real
  line to a circular arc). **The INTERVAL is the headline scalar** ("M lies in
  [1.71, 3.44] pH over any physical ground inductance"); the sampled curve is
  secondary. **The curve need not be monotone and the endpoints are NOT a
  bound** — a series L resonates with the package's shunt C. **The two
  ENDPOINTS are the numbers the user came for** (`M(0)` = ideal ground, `M(inf)`
  = open, the two assumptions the 6.07 dB dispute differed by), and both are
  exact. Re-measured on `diff_pair_4port.s4p` at 5.0005 GHz sweeping ground
  port 3: ideal `1.01 nH`, open `503.7 pH`, one pole at `L = 505.25 nH` (there
  `ctx.Gm[0,0] = -391 µΩ - j15.8745 kΩ`, i.e. 2.005 fF, and 505.25 nH
  series-resonates with 2.005 fF at exactly the 5.0005 GHz being read), and an
  extremum of **±10.28 mH** — `1.0e7` times the bracket. Away from the pole
  (factor-of-two guard) the same curve is `[-2.5 pH, 1.52 nH]`, and at a
  factor of ten it is `[447.5 pH, 1.066 nH]`, still outside the bracket at both
  ends. This bullet used to quote `[504 pH, 1.18 nH]` as the actual range,
  which predates the pole-seeded extremum search and is what the code no longer
  says: **an interval quoted over the whole half-line is the pole, i.e.
  arithmetic; the pole-free interval is the answer, and the pole is reported
  separately by its `L`.** On an UNBOUNDED sweep an extremum `NEAR_POLE_RATIO`
  past the bracket is a near-pole, not a design margin. **`bracket` must be in the SAME quantity as
  `interval`** — for the complex `quantity="Z"` the interval is of `|Z|`, so
  the bracket has to be too: measured with `t_max=20 nH`, the real-part
  spelling put `(-2.49 nOhm, 376 pOhm)` beside an interval of
  `(31.7 Ohm, 32.4 Ohm)` and announced a `1.3e10`-times-the-bracket near-pole
  that does not exist.
- **The sweep is evaluated from its PARTIAL FRACTIONS, never from the expanded
  polynomial.** `Z(t) = c0 - sum_j residues[j]/(lam[j] + t)`, with the poles at
  `t = -lam[j]`; `t -> inf` is exactly `c0` and `t = 0` is one sum, both
  overflow-free at any `|S|`. Expanding it multiplies `|S|` eigenvalues
  together, and with `param="L"` each is of order `1e-9`: measured on a
  synthetic package sweeping one ground group, `den[-1]` is `5.98e-273` at 30
  balls, `3.70e-309` at 34 and **exactly 0 at 36** — so `value_ideal`, which
  was `num[-1]/den[-1]`, printed `+inf` at 36, `NaN` at 38 and `NaN` for the
  whole curve at 60, with `method` still saying `"closed-form"` and `notes`
  empty. **Requirement 9 is written around 60 ground balls.** `num` / `den`
  survive as DIAGNOSTIC fields and are EMPTIED (with a note) rather than left
  holding underflowed garbage — two redundant halves, `_EXPAND_MAX_DEGREE` and
  the `den[-1] == 0` check, either of which alone does the job.
- **The extremum search SEEDS FROM THE POLES and then polishes; `np.roots` on
  the expanded critical polynomial is not enough.** Every candidate is a point
  the curve really passes through, so the interval is always ACHIEVED and can
  only ever be too narrow — that one-sidedness is what makes extending the
  candidate set safe. Measured on `diff_pair_4port.s4p` at 5 GHz sweeping BOTH
  grounds as one group over `L`: the two poles sit at `5.05000e-7` and
  `5.05503e-7`, **0.1% apart, both on the positive real axis**, and the
  degree-4 critical polynomial's roots found neither — reported
  `(+7.46e-21, +2.138e-3) H` against a true `(-5.187, +5.187) H`, i.e. the
  maximum `2.4e3x` too small and the minimum **the wrong sign**. The
  single-element sweep on the same file was correct throughout, which is why it
  went unnoticed: the defect needs `|S| >= 2`, i.e. exactly requirement 9b's
  "change a whole connection-table row". Seeds are `Re(p) +/- c*|Im p|` for
  `_POLE_SEED_OFFSETS`; the Newton polish on `Z'` / `Z''` (partial-fraction
  form, no expanded coefficient anywhere) is what reaches a BROAD extremum that
  sits on no seed — measured on a two-pole case, `-0.058971` without it against
  `-0.059261` with it and on a 2M-point grid.
- **The SIGN CONVENTION is declared globally and in every export**
  (`SIGN_CONVENTION_TEXT`, one string so exports carry it verbatim). Victim
  reference = `V(+) - V(-)` of the victim port; aggressor driven `+1 A` into
  its `+` side; `I_e > 0` flows OUT of the structure into ground for a shunt
  element (`u = e_p`) and from `p` to `q` for a series one (`u = e_p - e_q`).
  Flipping either measurement port's `+/-` flips every term together:
  **relative** signs between terms are physical, absolute ones are a labelling
  choice. Same rule and same reason as `M`/`k`/`C_c` in core.
- **Replicate `compute_z_matrix`'s PRECEDENCE EXACTLY.** Modes 1/2/3 let a
  `Ground` beat a `Signal` (`merge_terms`, pinned by
  `TestTerminationPrecedence`); `build_terminations_coupling` raises on the same
  overlap. `_normalize_signal` is imported from core **on purpose** —
  reimplementing the legacy "B == minus side of A" alias here is exactly how
  the two would drift, and the symptom would be a reconciliation failure on the
  specs the reconciliation exists to guard.
- **The contribution table is a ranking of DECLARATIONS, never of PORTS.** A
  port that is open contributes no element and therefore no term — it is
  **absent**, not small. A table headed "contributions by port" that omits the
  45 open ports of a package is a wrong answer with a plausible shape. Only the
  sensitivity side reaches ports the user has not decided about, and it does so
  by hypothesising a termination. State this in the docstring AND in the report
  header, in those words. Related distinction the reviews surfaced and the docs
  now carry: **a port left open because the SIMULATOR owns it is a different
  thing from a port left open because nobody decided, and only the first is
  safe** — the two are indistinguishable in the file, in the `TerminationSet`
  and in the table, which is what the Ports & Roles open-port name check exists
  to catch.
- **The split depends on how the spec is SPELLED, and that cannot be fixed.**
  `6:1:14 ground` (9 elements) and `6 short_to 7:1:14` + `6 ground` (8 shorts +
  1 ground) are the same network, give the same total, and decompose
  differently — they are two different *tearings* of it in the Kron sense.
  Measured on `diff_pair_4port.s4p`: `3 ground / 4 ground` splits as
  `bare 251 pH / gnd3 252 pH / gnd4 506 pH`, and `3 short_to 4 / 3 ground` as
  `bare 251 pH / gnd3 253 pH / short 3-4 506 pH`, both totalling 1.01 nH. Say
  so in the report — a user who reorganises their table for readability and
  sees the contribution column move must find that sentence before filing a
  defect.
- **A NaN residual is NOT a pass — `split_trustworthy` requires
  `math.isfinite(resid)`.** A NaN means nothing was checked at all, and the
  cases that produce one are exactly where the module is most convincing and
  most wrong: measured on `coupled_4port_float.s4p` with only one of the two
  coils referenced, `compute_z_matrix` says NaN ("'c2' has no return path")
  while this module folds the single ground in and reports **400.000 pH —
  exactly half** the fixture's real 800 pH. The warning said "could not be
  measured" and a caller gating on `split_trustworthy` got a green light. The
  TOTAL is still reported; only the apportionment is withheld.
- **A non-finite `Y` at the analysed frequency is REFUSED BY NAME, and so is a
  non-finite caller-supplied `zt`.** The first escaped as numpy's bare
  `LinAlgError("SVD did not converge")` out of `build_context` — no verdict, no
  frequency, no file, in a repo whose `TouchstoneParseError` contract exists to
  answer exactly that question; `compute_z_matrix` survives the same input and
  returns NaN with a warning, so the user still has the engine's reading and
  this module says which frequency it cannot follow it at. The second is the
  only route round the `Zt = D^-1` formulation's guarantee that **no infinity
  ever enters the arithmetic** (contract priority 4): an OPEN element is
  spelled by leaving it out, never by a large or infinite impedance. The dead
  `z_declared[i] = complex("inf")` branch that used to sit in `build_context`
  is gone with a comment saying why it was unreachable — a zero-admittance
  element is dropped as inert before it can get there.
- **A multi-element what-if models the replaced leads as INDEPENDENT unless
  told otherwise, and it SAYS SO.** `group_joint` / `cumulative_curve` /
  `sweep_mobius` take `z_ret=`, which puts one shared return impedance across
  the changed block — measured, that reproduces the equivalent
  `termination_impedance_shared_return` context **bit-identically** (rel `0.0`)
  and lands **6.06 dB** from the independent answer on a two-ball spec. With
  `z_ret = 0` and two or more shunt elements changed, `notes` carries the
  `(1 + (n-1)k)` warning: `build_context`'s DIAGONAL note inspects `ctx.Zt`
  only and therefore cannot see a what-if, which is exactly where the model is
  chosen rather than inherited.
- **`cond(G)` and `cond(Ybase)` are DIAGNOSTICS, not trust signals.** Measured:
  a node space collapsed by a 1 pΩ tie reports `cond(G) = 1.0` — `Gm` has
  underflowed to `~1e-14` times the identity, so its condition number is
  perfect — while `Zop[a, b]` is exactly 0 against the engine's 305 pH. The
  reconciliation residual is what catches that; the condition numbers only
  explain it afterwards. Said in `AttribContext`'s docstring for the same
  reason.
- **The decomposition is GAUGE-DEPENDENT.** Change the baseline and every term
  changes; fold one element in and the rest all move, though the network, the
  total and the physics are identical. What does **not** change is the element
  currents `I_e` — those are physical; the attribution of *voltage* to them is
  a gauge choice. This is PEEC's partial-inductance warning restated, and it is
  the reason the report names its baseline every time: two reports are
  comparable only when their baselines match.
- **Re-terminating existing ports cannot evaluate NEW METAL.** A shield, an
  extra via, a widened return path — none is a termination of an existing port.
  They change `Y` itself and need a new EM run. Worth drawing sharply because
  the sensitivity output looks exactly like a layout-exploration tool and is
  not one.
- **Also expose the EXACT current-transfer ratio.** `-Z_ab/Z_aa`, and an
  optional loaded `-Z_ab/(Z_aa + Z_load)`. `theory.md` §8.8 documents that
  `M/L_a` is only the first-order Norton approximation to it (1098% apart at
  10 MHz for `L=2n, R=1.5`); the user measured 0.87 dB of difference on their
  own file by hand. It is a TOTAL, not a decomposable quantity — `Z_ab` is in
  the denominator.
- **The prior art is named on purpose** (`theory.md` §13.5): Kron diakoptics
  (`H = Zt + G` *is* the connection matrix), the adjoint variable method (`r_a`
  is the adjoint solution — which is why requirement 1 is stated in adjoint
  language), PEEC partial elements (the gauge warning, verbatim), and Norton
  path decomposition / transfer-path analysis. Each of those literatures
  already found the trap the corresponding rule guards against; "this is
  diakoptics, and diakoptics has the following known failure mode" is cheaper
  than rediscovering it.
- **No scipy, and no explicit `inv`.** The contract permitted
  `scipy.linalg.lu_factor` / `lu_solve`; the module ships without it because
  `np.linalg.solve` with a multi-column right-hand side IS one LU factorisation
  plus k triangular solves — exactly what those two buy — and
  `deploy/doctor.sh`'s tiers assume numpy is this repo's only hard dependency.
  Adding scipy would silently move the red-zone bar. Don't, without a
  measurement that justifies it.
- **`compute_z_matrix` is called on a ONE-FREQUENCY SLICE, first**, before any
  attribution arithmetic. Its Schur solve is a gufunc and its contraction is
  already per-frequency, so the slice returns exactly what a full sweep would
  put at that index — microseconds instead of hundreds of milliseconds on a
  5000-point file — and calling it first is what validates the port indices,
  resolves the measurement ports and raises on conflicting signal groups.
  **Nothing in this module may modify `compute_z_matrix`, `_probe_impedance`,
  `_is_singular_2x2` or anything else `golden_legacy.npz` pins.**
- **THERE IS NO ELEVENTH HELP TAB, and that is a measurement, not a
  preference.** `HELP_TOPICS` has 10 tabs and the strip needs **968 px**
  against `HELP_WINDOW_WIDTH = 1010`. Re-measured for this feature (Tk 8.6,
  vista theme, `TkDefaultFont` = Microsoft YaHei UI 9, `tk scaling` 1.333): an
  eleventh tab labelled `Cold start` takes it to **1033 px**, `Attribution` to
  **1037**, `Port attribution` to **1064**. A `ttk.Notebook` CLIPS a strip it
  cannot fit — no wrap, no scroll, no chevron — and the tab that vanishes is
  the **LAST** one, so the new tab would be the invisible one. Everything about
  this feature therefore folds into **Mode 6 (Coupling)**, cross-referenced
  from `Overview`, `Input syntax` and `Worked examples`.
  `tests/test_session.py::TestHelpTabsAllFit` is the guard and re-measures it.

### The Attribution window (`pkg_rlc_attrib_gui.py`)

`tests/test_attrib_window.py` is the guard, and every claim below was
mutation-checked. `docs/design_port_attribution.md` §13 records what stage 4
became and where it departed from that note's own §9 sketch; **§13.13 records
the four things the first screenshot changed** — the sweep plot's pole, the
sash, the across-frequency badge and the ground model — none of which was a
wrong number, and all four of which had a written reason a later session would
otherwise reinstate.

**The hook surface `pkg_rlc_gui.py` calls, and there is no other:**
`ATTRIB_MENU_LABEL`, `attribution_refusal(trace, file_entry)`,
`open_attribution_window(app, trace)`, `refresh_attribution_windows(app)` —
which **must** be called from `_apply_editor_strips`, from every path that
removes a trace or a file, and after a session load —
`refresh_attribution_windows(app, rerender=True)` from
`_on_units_mode_changed` and **nowhere else**, and
`attribution_session_state` / `apply_attribution_session_state`. The two
`refresh` forms are not interchangeable: measured with one window open,
**28.6 µs** for the banner refresh against **6049 µs** for `rerender=True`,
which redraws the tables and the sweep — 200x, and the reason the cheap one is
the default on a path that fires from a Tk variable trace. The reverse import
is **lazy** (`_gui()`), which is the only reason
`import pkg_rlc_attrib_gui` at the top of `pkg_rlc_gui` is not a cycle. A
second copy of `_value_formatter` / `_trace_role_rows` / `_build_termination`
was the alternative, and two renderings of one spec drifting apart is a
failure this repo has already had more than once.

- **MODELESS, no `grab_set` — and deliberately NOT `transient(app)`.** No
  `grab_set` for the documented reason: a modal `Toplevel` that outlives its
  opener blocks event delivery, `update()` never returns, and the GUI and the
  test suite hang together (the style-picker / scrollbar-limit-cycle failure).
  `transient` is the one where this window and `PortRolesWindow` diverge **on
  purpose**: on Windows it removes the taskbar button and the Alt-Tab entry
  and makes the WM withdraw the child with its master. That is right for a
  quick read-while-editing panel and wrong for a window holding a RESULT that
  cost a Recompute, which is read against the plot over many edits and parked
  on a second monitor. The cost of omitting it — the window can end up behind
  the main one — is paid by `open_attribution_window` **lifting and focusing
  an existing window** for the same `(trace, victim, aggressor)` instead of
  opening a second copy.
- **NO NOTEBOOK, and specifically not the obvious four tabs.** Contributions /
  Sensitivity / Sweep / Across-frequency was designed and rejected: the sweep
  is a **drill-down on the row just clicked**, so a tab makes the user re-pick
  the element they already selected; and "does this ranking hold across
  frequency" is a **validity qualifier on the table, not a place**, so as a tab
  it is never opened and the acceptance item is satisfied on paper only.
  Shipped instead: a fixed header, three one-line strips, a one-line
  across-frequency badge with an expander, one pane with a **radiobutton view
  toggle**, and a detail pane under a sash. Every pane is populated **before**
  `PanedWindow.add()` — ttk sizes a pane from its requested size at `add()`
  time and never recomputes.
- **THE TABLES ARE A MONOSPACE `tk.Text`, NOT A `ttk.Treeview`** — this
  reverses `design_port_attribution.md` §9's own bullet, on a measurement.
  Measured here: the same eight columns need **671 px** as a Treeview at 100%
  and **971 px** at 150%, against **490 / 700 px** as Consolas 9 text; ttk will
  not shrink a Treeview column below its set width even with `stretch=True`
  and it clips with **no ellipsis and no overflow indicator**, so `-0.6231`
  silently becomes a plausible shorter number; and in `TkDefaultFont` the
  signed-number glyphs are all different widths (`-` 5 px, `+` 9, U+2212 9,
  `.` 3, ` ` 4, digits 7), so a right-aligned column of signed values has its
  decimal point wandering ±4 px per row. In Consolas 9 every glyph the tables
  emit — `' ' 0 9 - + − █ ▸ ▾ . M X ( ) % j … Ω ←` — measures **exactly 7 px**
  (re-measured), which is why they line up. `✓` is **12 px** there and is
  banned from tables, used only in ttk Labels. The pure text formatter is
  separate from the widget so it is testable with no display.
- **Numeric columns are auto-sized from the widest cell OR the header, and are
  never capped.** Sizing on the values alone puts a 7-char value under a 5-char
  header and throws the heading one place off the numbers it names — the
  cursor-readout rule. Only the two TEXT columns cap, and they ellipsise with
  U+2026; a clipped number is a plausible wrong number, which is the whole
  reason this is not a Treeview.
- **A sign is always one of a WIDTH-STABLE PAIR, and colour NEVER means sign.**
  U+2212 for negative, an explicit `+` for positive, one of the two on every
  numeric cell — both 7 px in Consolas 9, the same as a space, so a column of
  mixed signs keeps its decimal points in one place. Rows are coloured by
  element **KIND**, reusing `PORT_ROLE_FG`, the palette the user already
  learned in Ports & Roles. Not by sign: red is `WARN_FG` everywhere else in
  this application and a red negative makes a correct answer look like a fault
  — which bites hardest exactly here, because §5.7's measured case has a
  **negative bare EM term** with four declared grounds cancelling most of it.
  The rule is stated ONCE in the header, beside the reference declaration.
- **RECONCILIATION GOES IN THE HEADER, NOT THE FOOTER.** It gates trust in
  everything under it, and at the bottom of a scrolling table it is the first
  thing off screen. Verdict word plus number
  (`reconciled  rel diff 3.1e-13  (floor 4.3e-10)`), and the **TOTAL is shown
  even when the split is withheld** — that is the core module's "degrade, never
  refuse" made visible.
- **`[Recompute]`, NOT AUTO-REFRESH. This is the one that would have shipped
  broken.** `tc.Zmat` is written **only** by `_on_calculate`; editing the spec
  sets `tc.stale` and leaves the numbers at the PREVIOUS run's. A window
  refreshing from `_apply_editor_strips` would therefore, on the first
  keystroke, decompose the NEW spec and reconcile it against the OLD
  authoritative total — a residual not of `1e-13` but of however much the edit
  changed — and by the rule above it would then withhold its own split. **The
  documented behaviour of the auto-refresh design is a window that erases
  itself while you type.** The editor hook stays, but it updates exactly ONE
  thing: the **staleness banner**, a signature comparison costing microseconds,
  which is what makes the button honest. The banner also names the provenance
  permanently (`from run #7 @ 5.600 GHz`).
- **REFUSE ON A STALE OR FROZEN TRACE, BY NAME, and keep the menu entry LIVE.**
  `attribution_refusal` returns `None` or the reason, duck-typed on `getattr`
  so it is importable and testable without `pkg_rlc_gui`. Four refusals, in the
  order the work would hit them except that `frozen` is tested FIRST (a frozen
  trace can never be attributed whatever the file is doing, so sending the user
  to load one is a dead end): **frozen** — its numbers came from an earlier run
  and can never be recalculated, so a decomposition now could only be stamped
  with the CURRENT run, which is the frozen-trace-CSV bug through another door;
  **file not loaded**; **no numbers**, or `Zmat is None` with `Z` set, which
  means ONE measurement port and there is no mutual impedance to attribute;
  and **stale**. The entry stays live in all four cases — CLAUDE.md's rule on
  the identical decision for Freeze: *a greyed entry would be the same bug
  report.*
- **THE SWEEP CANVAS follows `FullscreenPlotWindow` with two deliberate
  departures.** `Figure()`, never `pyplot.figure()` — pyplot's global registry
  outlives the `Toplevel` and the figure is never collected. Then: **no
  `<Enter>` → `focus_set`** (measured: that binding moves focus off a sibling
  Entry, and this window has Entry fields directly above the plot, so a user
  typing `5.6` into Freq and moving the mouse toward `[Recompute]` loses the
  rest of the keystrokes), and **no M / V / Delete bindings** — it is a
  read-only what-if curve, not the measurement plot. It is drawn **LAZILY on
  first reveal**: a canvas in an unmapped pane has no size, so `draw()` /
  `tight_layout` there lays the axes out for a 1x1 widget and the labels stay
  on top of each other forever after. The curve itself is the closed-form
  Möbius sweep — do not re-solve per point.
- **NO ACCELERATOR, and nothing registered with the wheel router.** `bind_all`
  reaches every `Toplevel`: measured on this application's own menubar, Ctrl+S
  typed into a `Toplevel` Entry fires the App's `_on_save_config`, so a Ctrl+O
  here would open Load Config and replace every trace including the one this
  window describes. `<Return>` in the frequency field recomputes and is bound
  **on the Entry**. For the wheel, this window registers NOTHING with
  `App._register_scrollable`, so `_route_wheel` walks out of it, finds no
  handler and lets Tk's class bindings scroll the Texts (`"Text"` is in
  `App._WHEEL_OWNERS`). `"Canvas"` is **not** in that set, so a matplotlib
  canvas gets no bail-out — which is safe only because there is no registered
  scrollable ancestor here, by construction. Do not nest it inside one.
- **PACK ORDER: footer `side=BOTTOM` FIRST, then the header and the strips,
  then the `PanedWindow` with `expand=True` LAST.** `pack` unmaps from the END,
  so the buttons and the reconciliation verdict are unconditional and it is the
  TABLE that gives up height. **"Gives up height" must not mean "gives up all
  of it", and the MINIMUM is therefore measured rather than declared.**
  Re-measured at 150% DPI (`tk scaling 2.0` + every named font x1.5, the
  definition `test_run_history.py::test_the_keep_button_is_READABLE_at_150_percent_font_scaling`
  uses): the fixed chrome is **436 px at 720 wide** and 388 at 980, against a
  declared minimum of 420 — so at exactly 720x420 the whole `PanedWindow` read
  `winfo_ismapped() == 0`, table and detail pane and sweep canvas all gone,
  with no scrollbar, no message and no way down because the user is already AT
  the minimum. It was not only the minimum: the table was already unmapped at
  820x540. `_apply_min_height` therefore raises the floor to
  `chrome + ATTRIB_SPLIT_FLOOR_LINES x <table font linespace>` at the CURRENT
  width — the split needs a measured **124 px at 100% / 162 px at 150%** for
  the table to be mapped, and 9 lines is 126 / 198. At 100% the computed value
  is 333 px, i.e. under the declared 420, so **nothing about the 100% window
  moves**. It SETTLES because it reads the WIDTH and writes only a minimum
  HEIGHT (the `ReflowRow` fixed point, not the `_apply_editor_scrollbars` limit
  cycle): measured over eight resizes in both directions at both scalings,
  every one settled inside 40 update rounds with the last twenty identical.
- **The same rule applies INSIDE the sweep pane: the caption is packed before
  the canvas.** With the canvas first it claimed its whole 90 px request and
  left the caption 1 px — measured at 720x420, **103 x 6 PIXELS of axes** over
  a 1 px label, and that label is where rule 8's mandatory `NON-MONOTONIC: the
  curve LEAVES the [ideal, open] bracket` goes, along with the `cannot sweep`
  refusal for `k` / `M/L_a` and any refused candidate. Six pixels of curve is
  worth nothing; the sentence saying the two endpoints are not a bound is worth
  the pane.
- **The sweep caption is CAPPED at `SWEEP_NOTE_LINES = 3` clipping lines, and
  the full text is in Copy report.** It was a wrapping Label at
  `wraplength=420` holding up to four sentences (957 characters on a
  non-monotonic sweep), i.e. a **293 px** request — and packed against an
  `expand=True` canvas that meant the plot sat at its 90 px floor at every
  window size: measured 194 -> 90 px of canvas at 980x700 and 274 -> 90 at
  1400x900 the moment a row was selected. Three lines and not two, seen on
  screen rather than argued: at two, the second line was `… +N more` and the
  MANDATORY non-monotonicity label was inside the +N. Order is priority — a
  refused candidate, the interval, the warning, then the module's notes — and
  whatever does not fit is COUNTED, never dropped in silence.
- **A REFUSED CANDIDATE has to reach a widget.** `_alternatives` used to write
  its problem list into `sweep_note` and `_draw_sweep` overwrote that Label
  later in the same `_render()` pass, so `candidate_list("open, R=5 m", …)` --
  which produces exactly the `_rlc_tokens` message this repo requires, "'R=5 m'
  would silently mean 5 Ω, not 5 mΩ" — reached NOTHING: measured, `sweep_note`
  held the caption, `foot_note` was empty, and the Sensitivity table went from
  four rows to two with its own note saying "2 rows" and nothing else. It is
  now a RECORD (`_cand_problems`) rendered by `_set_sweep_note` ahead of the
  caption and counted on `table_note`, and `_on_candidates_changed` parses
  EAGERLY so the refusal lands on the keystroke that caused it.
- **The Candidates Entry is packed BEFORE the hint that describes it.** pack
  unmaps from the end: measured at 100%, the caption row needs 103 + 615 + 188
  = 920 px, so the field was 112/188 px wide at 860, 12 at 760 and
  `winfo_ismapped() == 0` at the declared 720 minimum — gone, while the 601 px
  sentence telling you to type into it was still there; at 150% it needed a
  1920 px window to appear at all. The hint is `wraplength=0` and clips, like
  the three header strips.
- **NO `<Escape>` BINDING.** A Toplevel is in every descendant's bindtags, so
  `self.bind("<Escape>", …destroy)` fires from anywhere inside the window:
  measured, Escape in the Freq entry, in either port combobox, in the table and
  on `[Recompute]` all destroyed it. `PortRolesWindow` binds it and is right
  to — it is a read-only list that rebuilds from live state on reopen — but
  this window HOLDS a result: a Recompute, plus five `build_context` +
  `decompose` passes if the badge was expanded, all O(N^3) in the port count,
  and nothing restores it. The Close button and the WM close box remain.
- **`[Recompute]` FLUSHES the queued editor sync first, and re-asks the
  refusal.** The documented "Auto-sync editor on Calculate" invariant, on the
  one button the module calls "THE button". Measured with the trace calculated
  at `gnd_ports = "2,4"`: typing `2` into the GND field and pressing Recompute
  in the same event burst decomposed the OLD spec — the table came back with
  `ground port 2` AND `ground port 4` — and only then did the queued
  `after_idle` sync land, after which the banner said "the spec has been EDITED
  since — press Recompute", pointing at the edit Recompute was supposed to have
  picked up. `_on_toggle_stability` flushes for the same reason. The refusal is
  re-asked with **`allow_stale=True`**, which exists for this call site and no
  other: an edited spec is what the button is FOR, but a trace that became
  frozen or lost its file while the window was open must still be turned away
  (measured without it: a frozen trace recomputed here came back reconciled and
  stamped `spec_matches_run: True`).
- **The BANNER says `spec_matches_run`, not just the export.**
  `_on_recompute`'s docstring promises "the banner, the export and the copied
  report all say the plot and the results table are showing something else";
  only two of the three did. `staleness_text` compared
  `spec_signature(trace) != prov.signature`, which is False immediately after a
  Recompute — the Recompute just re-captured it — and nothing consulted
  `spec_matches_run`. Measured: run #1 is M = +821 pH, editing GND to `2` and
  pressing Recompute gives +407 pH (**2.0x** what the plot, the results table
  and Export CSV are showing), and the banner read
  `from run #1 @ 5.1 GHz   ·   M: 'vic' ← 'agg'` in the theme foreground. A
  MOVED signature outranks it when both are true: "press Recompute" is the
  action.
- **The refusal counts MEASUREMENT PORTS, not just `Zmat is None`.**
  `_on_calculate` routes on `tc.mode == 6 or n_mports > 1`, so a mode-6 trace
  with ONE measurement port takes the coupling path anyway and comes back with
  a real `(F, 1, 1)` `Zmat` — measured. Testing `Zmat is None` alone waved it
  through, and what stopped it was `open_attribution_window`'s
  "fewer than two measurement port names cached. Calculate it again." backstop:
  a message about an internal inconsistency that had not happened, advising
  something that cannot help. Both routes to the shortfall now reach the one
  message that names it.
- **A click PAST the last table row selects nothing.** Tk's `@x,y` index CLAMPS
  to the nearest existing line, so a click in the empty space below the table
  resolved to the last row — measured, a 5-line table in a 222 px widget
  answered `index("@50,218")` (about 150 px below the last text line) with
  `"5.51"` and selected the final element, silently re-driving the detail pane
  and running a sweep solve for something nobody clicked. `_on_table_click`
  tests the clicked line's own `bbox`.
- **`ReflowRow.refresh()` after a child's TEXT changes.** `_reflow` runs from
  `add()` and from the strip's own `<Configure>`, and a child whose text grew
  fires neither — `place` then goes on forcing the stale width and the child is
  CLIPPED with no ellipsis and no overflow marker, which is the Treeview
  failure this window's tables exist to refuse, arriving in the header instead.
  Measured at 980x700 with the trace relabelled to the 18-character cap: the
  item asked 307 px and was placed at 220, i.e. 87 px / 14 characters gone in
  silence, while the strip went on reporting one row against items asking
  1048 px of 964. A 1 px resize fixed both, which is what makes it a missing
  notification rather than a layout bug. The refresh is `after_idle`,
  coalesced, and CANCELLED on `<Destroy>` (an un-cancelled `after` fires
  against a Tcl command teardown has already deleted).
- **THE WINDOW OUTLIVES ITS SUBJECT.** `PortRolesWindow` re-reads `app.traces`
  every refresh and degrades gracefully; this one holds a RESULT and cannot.
  Every path that removes a trace or a file, and every session load, must call
  `refresh_attribution_windows(app)` — the same class of omission as the
  documented `_on_remove_file` forgot-to-replot bug, which is why the poke is a
  named function here rather than a line of code in the host.
- **THE UNITS MODE IS READ LIVE OFF THE APP; everything else on the
  `Provenance` is frozen.** Same rule and same reason as
  `_run_report_segments`: the unit is a **rendering choice, not a recorded
  fact**, so freezing it would leave this window printing `-1.242` beside a
  results pane printing `-1.24 mH`. The stored value is only the fallback for a
  window whose App has gone.
- **EXPORT CARRIES THE FULL PROVENANCE.** Copy report and the CSV both head
  with the run number, the frequency and its snap note, the complete sign
  convention, the ground model and the termination spec verbatim. The
  frozen-trace CSV precedent is explicit that a block attributed to the wrong
  run is a real bug — and here the exports are also where the clipped strips'
  tails live, so they are not optional prose.
- **The three strips are ONE LINE each with `wraplength=0`, i.e. they CLIP.**
  Re-measured on a real window: each is **21 px** at 980x700 and still 21 px at
  720x420, i.e. all three stay one line at both declared sizes. Letting them
  wrap costs 51 px of a **213 px** pane budget at the minimum, and there are
  three of them. **THE BUDGET IS 48 CHARACTERS, NOT 120.** The 120 / 162 figures
  were measured at 100% only; re-measured at 150% the same strip fits **52
  characters at 720 px and 74 at 980**, and the old sign strip spent its first
  64 on the sign rule alone — so the shares rule, which rule 4 requires to be
  stated once in the header, was off screen at **every** supported size while
  the comment beside it said the opposite. `SIGN_STRIP_TEXT` is rewritten to
  spend its first 48 characters on both rules and nothing else (measured on the
  real widget: 48 at 150%/720, 66 at 150%/980, 110 at 100%/720, all 143 at
  100%/980). For the same reason `reconciliation_line` puts `— split WITHHELD`
  **immediately after the verdict word** instead of behind 45 characters of
  `rel diff … (floor …)`: measured, WITHHELD never appeared at 150% at either
  width, while the table underneath read "(no per-element split — see the
  reconciliation line above)" and pointed at a line that no longer carried the
  reason. Each strip leads with its verdict and its number, and the full
  declaration is in Copy report and the CSV, where it cannot clip.
- **The header is a `ReflowRow`, and the trace label is capped at 18+18
  chars.** Re-measured on a real window (mode-6 trace `coil` on
  `coupled_4port_diff.s4p`): the six items are 228/160/186/145/143/99 px =
  **961 px**, the strip is **964 px** at the 980 default — **one row, 29 px** —
  and **704 px** at the 720 minimum, where it wraps to **two rows, 58 px**.
  Three pixels of headroom at the default width is the whole reason the label
  is capped; the test's own default label puts the same sum at 970 and
  uncapped the first item is 317 px worst case, which forces a THIRD row.
  `winfo_reqwidth()` stays at **1** at both sizes, which is the other half of
  why `ReflowRow` (it lays out by `place`, and place does not propagate), so
  the header cannot force the `Toplevel` wider than the user set it. A plain
  `pack(side=LEFT)` run would have unmapped `[Recompute]` from the END with no
  scrollbar and no other route to it — exactly the defect
  `tests/test_plot_controls.py` exists to stop recurring.
- **`ttk.Panedwindow` starves its FIRST pane, not its last.** Measured at
  720x420: the split wanted 445 px and got 168, and the **table** read
  `winfo_ismapped() == 0` while the detail pane was fine. The cause was
  `FigureCanvasTkAgg` sizing its Tk widget from `figsize x dpi` and therefore
  REQUESTING 420x240 against the table pane's 156; capping the canvas *widget*
  to 240x90 balances them at 156/156 and both survive at 54/74 px. Only the
  FLOOR moved — matplotlib re-lays the figure out on every `<Configure>`, so
  the drawn plot is whatever the pane actually gets (183 px tall at the default
  size).
- **THE WINDOW OFFERS THE GROUND MODEL, in the CLI's own `shared:0.3n`
  spelling, and it goes through `[Recompute]` like every other input.** It was
  CLI-only at first on a pixel argument — the header is a `ReflowRow` measured
  at **961 px of 964** at the 980 default, so any further control there wraps
  it to a second row (**29 px**, measured) — and that argument lost to the
  measurement it was weighed against: the choice is worth
  **9.60 dB** (four balls at 1 nH each independently versus the same four tied
  through one shared 1 nH), against the **6.07 dB** dispute this whole layer
  exists to settle, and it grows with the ball count — `(1 + (n−1)k)` is
  **13.6 dB** at 20 balls with `k = 0.2`. A control the user cannot reach is
  not a default, it is a decision taken for them, and this one is the most
  expensive decision in the flow. One field, one spelling shared with
  `--attribute-ground-model` so the two cannot drift, and **one line beside it**
  saying why the default is not obviously right (independent leads understate
  the shared return) — one line, not a lecture. The **sign strip states which
  model is in force** and both exports keep doing so — but it says so AFTER the
  sign rule and the shares rule, never in front of them: the strip's measured
  budget is **48 characters at 150%/720**, rule 4 requires both of those stated
  once in the header, and the model name is the item that may clip, because it
  is also on the control itself and in full in both exports. Whatever a later session
  does to the header, the control may not cost `[Recompute]` its place: that is
  what `ReflowRow` is for (`pack` unmaps from the end and has no scrollbar and
  no other route to the button — `tests/test_plot_controls.py`'s whole
  subject).
- **A dense `Zt` has NO second opinion, and the window must NOT invent a second
  rule for that.** The rule is already written down twice above — *"the
  reconciliation compares two algorithms on ONE network, so it is always taken
  on the DECLARED configuration"* (`ctx.Zop_declared` is the left-hand side
  whatever `zt` is in force) and *"what a dense `Zt` loses is the second
  OPINION, not the check"* (`reference_applicable = False`, the total
  re-labelled *"compute_z_matrix, DECLARED spec — a DIFFERENT network"*).
  Follow those; `_attr_print_ground_model` is the CLI's working example.
  The failure they exist to prevent is measured on `diff_pair_4port.s4p`
  (probes 1/2, grounds 3/4, 5 GHz): reconciling the MODELLED total against the
  engine's DECLARED one made a shared 1 nH return — which **doubles** `M` —
  read as a residual of **1.01**, past `RESIDUAL_CATASTROPHIC`, so `terms`
  emptied and the window printed *"the two algorithms disagree about the answer
  itself"* about **2.026 nH, which was right**. The split would vanish at
  exactly the setting the control exists for.
- **THE SWEEP PLOT LABELS THE POLE; it never lets the pole set the axis.**
  Measured on `diff_pair_4port.s4p` at 5.0005 GHz sweeping `ground port 3`:
  `M(0) = 1.01 nH`, `M(inf) = 503.7 pH`, and one pole at `L = 505.25 nH` where
  the extremum is **±10.28 mH** — 1.0e7 times the endpoint bracket. Autoscaled,
  that is a y axis reading `1e-5`, one vertical spike, and a caption honestly
  saying NON-MONOTONIC beside a picture carrying no information at all — and a
  true sentence beside an uninformative picture teaches the reader to stop
  looking at the picture. Five rules, of which the middle three are the ones
  that get "simplified" back into an autoscale: **x stays
  log; y is SYMLOG with `linthresh` derived from the ENDPOINT SCALE, never the
  panel's `1e-6`** (these are henries — 1 µH is a thousand times the whole
  curve, so a fixed `1e-6` puts every point inside the linear band and symlog
  degenerates into the linear axis it replaces); **the y limits come from the
  physical endpoints `M(0)` and `M(inf)` plus a margin** — those are the two
  numbers the user came for — and the pole is allowed to run off the top;
  **the pole is drawn as a labelled vertical line at its `z`**, from the closed
  form (`z = -γ/δ`, i.e. `t = -λ_j`; measured, `ctx.Gm[0,0] = -391 µΩ -
  j15.8745 kΩ` and the pole sits at `L = 505.25 nH`, which series-resonates
  with that 2.005 fF at the 5.0005 GHz being read), **never by scanning the
  samples**; and **the headline interval is the POLE-FREE one** with the pole
  reported as a separate sentence (`[-2.5 pH, 1.52 nH]` at a factor-of-two
  guard band, against `[-10.28 mH, +10.28 mH]` over the whole half-line —
  the second is the tool describing its own arithmetic). A pole is a real
  feature of the structure: label it, never silently hide it. With no pole in
  range nothing changes visually. The guard is a case WITH a pole and a case
  WITHOUT, both asserting the y limits bracket the endpoints.
- **"The limits bracket the endpoints" is NOT SUFFICIENT on its own, and a
  shipped fixture proves it.** The margin for a sweep with no span was
  `SWEEP_Y_PAD * max(abs(hi), abs(lo), 1.0)`, and that bare `1.0` is one HENRY
  in an expression whose other terms are picohenries. Measured on
  `decap_4port.s4p` (ordinary mode 6, probes 1/2, `gnd_ports="3,4"`, 5 GHz,
  either ground row): every residue is exactly 0, so ideal = open =
  **−506.755 nH**, and the axis came out `(−120.00005 mH, +119.99995 mH)` —
  **473 602×** the value it was drawn to show, with the curve and both
  asymptote lines on one pixel row (endpoint separation **0.0 px of a 223.5 px
  axes**) and `linear_ticks` False, so the symlog decade locator printed
  **17** labelled decades from −10⁰ to +10⁰. The bracket assertion passes that
  trivially — ±0.12 H brackets everything — which is exactly the shape of
  failure item 1 was about, arriving inside item 1's own fix. `SWEEP_Y_PAD_FLAT`
  takes the margin as a fraction of the VALUE (1.12× the value, `linear_ticks`
  True), and the guard is the **ratio of the axis to the endpoint magnitude**,
  not membership. A curve that is identically zero gets a zero pad, and
  `_scale_sweep_axis`'s `yhi > ylo` test then declines to set any limit at all
  — matplotlib's own autoscale is the honest answer there.
- **Both sweep axes print ENGINEERING UNITS (`si_tick` → `format_si`), and the
  exponent offset is gone.** The original complaint about this plot was
  literally "the y axis reads 1e-5"; after the symlog change it read `1e-10` —
  measured, `ax.yaxis.get_offset_text()` was `'1e−10'` over tick labels
  `['−2.5','0.0','2.5','5.0','7.5','10.0']` with an ylabel of `M [H]`, beside a
  table cell reading `+413 pH` and a caption reading `ideal +821 pH`. A bare
  exponent is a second notation for the same quantity on the same screen, and
  it is the one the reader has to do arithmetic on. This is the plot cursor
  readout's own rule (`_readout_value` → `format_si`), so the axis, the table
  and the caption cannot drift. The unit therefore lives on the TICKS and is
  **not** repeated in the axis label.
- **A sub-decade symlog range gets a LINEAR major locator.** Measured: with
  ylim `(310 pH, 919 pH)` the default symlog locator put `[]` labelled ticks
  inside the range and `subs=[1,2,5]` put `['500 pH']`, against
  `['450 pH','600 pH','750 pH','900 pH']` from `MaxNLocator`. Inside
  `linthresh` the symlog transform IS the identity, so a linear locator places
  its ticks exactly right; the axis is unchanged and only the tick choice
  moves.
- **The sweep caption's endpoints are printed the way the interval beside them
  is COMPUTED.** For a complex quantity (`Z`) `Sweep.interval` and
  `Sweep.bracket` are over the MAGNITUDE while the endpoints were printed as
  `.real` unconditionally: measured on `coupled_4port_diff.s4p` at 5.1 GHz the
  line read `[−2.15 mΩ, +27.5 Ω]   ideal +6.41 mΩ   open +785 µΩ` against a
  `|value_ideal|` of **26.3 Ω** — the ideal endpoint reported four orders of
  magnitude below the interval that is supposed to contain it, and below the
  `[13.1 Ohm, 26.3 Ohm]` bracket the caption's own NON-MONOTONIC line quotes
  two lines further down. A magnitude is labelled `|Z|`, so no sign is being
  suppressed — there is none to suppress — and every real-valued quantity
  (`M`, `ReZ`, `ImZ`, …) is untouched.
- **The caption's LINE CAP is a budget, not a count, and it is read off the
  WINDOW.** `SWEEP_NOTE_LINES = 3` is a count; the note is packed `side=BOTTOM`
  against the `expand=True` canvas, so it takes its whole request and the plot
  gets the remainder. Three lines are 55 px at 100% and **112 px at 150%**,
  out of a pane that got smaller rather than bigger. Measured on
  `coupled_4port_diff.s4p` with an element row **SELECTED** — the state the
  pane exists for, and the state the existing 150% guard never entered:

  | scaling | window | paned | detail | note | CANVAS |
  |---|---|---|---|---|---|
  | 100% | 980x700 | 497 | 331 | 55 | 309x276 |
  | 100% | 720x420 | 188 | 90 | 55 | 179x35 |
  | 150% | 980x700 | 268 | 114 | 112 | **309x2** |
  | 150% | 720x678 | 198 | 70 | 112 | **309x2, `ismapped()==0`** |

  The last one also hung 309 px off a 179 px parent — the only containment
  violation in the window. So everything item 1 does was invisible at 150% DPI.
  `_sweep_note_cap()` is `budget // line − ATTRIB_SWEEP_NOTE_RESERVE_LINES`
  with `budget = winfo_height() − _chrome_height()`, giving 3 / 3 / 1 / 1 on
  that table: **100% is untouched, including 720x420, where three lines over a
  35 px canvas is the documented trade** ("the sentence saying the two
  endpoints are not a bound is worth the whole pane"), and both 150% cases get
  the plot back (309x74 and 179x30, mapped and contained). **Both terms of the
  budget are independent of the note** — `_chrome_height` enumerates seven
  fixed widgets and the note is not among them — and that is the whole reason
  the rule is written against the window rather than against the sweep pane,
  which is the obvious place to read it: the pane's height comes from the sash,
  `_sash_target` reads the bottom pane's REQUESTED height, and the note's
  request is part of it. A cap read from the pane is a rule that changes the
  size it is measured from, i.e. the `_apply_editor_scrollbars` limit cycle. A
  fraction of the budget was tried and is wrong in the other direction:
  a quarter puts 720x420 at two lines, which drops rule 8's mandatory
  NON-MONOTONIC label into the "+N more".
- **THE SASH IS DERIVED FROM CONTENT, NEVER FROM THE MEASURED PANE HEIGHT.**
  Observed on the shipped window: three data rows with roughly **250 px** of
  empty space under them while the detail pane below was scrolling *and*
  clipping horizontally (`…because the other n-1` cut off at the right edge
  with an h-scrollbar under it). The initial position is therefore computed
  from the table's own row count — rows + header + a couple of spare lines, at
  the table font's linespace — and the detail pane takes the rest. Deriving it
  from the pane's measured height instead is the documented **limit-cycle
  shape**: a layout rule that reads a size it can itself change flips forever
  and `update()` never returns, hanging the GUI and the test suite together
  (`_apply_editor_scrollbars`, and the reason `ReflowRow` reads an imposed
  width and writes only a height). Row count is an input the sash cannot move,
  so it is a fixed point. **The detail text WRAPS and never scrolls
  horizontally** — it is prose, and a horizontal scrollbar on prose is the
  Treeview clipping failure this window's tables exist to refuse, arriving in
  the pane underneath them. Re-derive on a material row-count change (a new
  decomposition), and **never fight a user who has dragged it**: once moved it
  is theirs until the window closes. Measured, `sashpos` / table / detail /
  canvas with a row selected: **138 / 98 / 331 / 276** at 980x700 at 100%
  (against 279 / 239 / 198 / 160 before), **70 / 30 / 90 / 35** at the 720x420
  minimum, **107 / 48 / 114 / 74** at 980x700 at 150%, and **81 / 22 / 70 / 30**
  at the 150% enforced minimum of 720x678. `detail.yview` and `detail.xview`
  are `(0, 1.0)` at every one of them.
- **"Was that a drag?" needs the WRITE COUNTER as well as the position.**
  Comparing `sashpos` at ButtonRelease against ButtonPress is right and is not
  enough on its own: anything that moves the sash while a button is held is
  then recorded as a gesture and the split freezes for the session — i.e.
  the content-derived position above stops working, permanently, from a click.
  Measured at 100% / 980x700: `_on_sash_press()` → `_apply_sash(30)` (exactly
  what a new decomposition does) → `_on_sash_release()` left `_sash_user` True
  with no pointer movement at all. `_apply_sash` is reachable while a button
  is down from `_render_impl` (Recompute, a view switch, and the units switch's
  `refresh_attribution_windows(rerender=True)`) and from the `after_idle`
  `<Configure>`. `_sash_writes` is bumped only where `_apply_sash` actually
  writes a position, sampled at press, and required unchanged at release —
  orthogonal to the "not the last value applied" rule, which stands. The cost
  is at most a real drag that raced an automatic write in the same gesture,
  which is self-correcting; the false claim is not.
- **`across frequency: not checked` MUST CARRY THE ACTION AND ITS COST.** The
  ranking is read off one frequency, which is exactly what acceptance item 5 is
  about, and the window shipped with the check off and a badge that only said
  so. Turning it on by default is the wrong fix — it is a fresh
  `build_context` + `decompose` per frequency, `O(N³)` in the PORT count:
  measured **0.45 ms** per point on `diff_pair_4port.s4p` and **223 ms** per
  point on a synthetic dense 153-port network, so at `STABILITY_POINTS = 5`
  the check is four extra points, under 2 ms on a small file and about **0.9 s**
  on a package export. So the OFF state names the cost *on this file* and
  checks it in one click, and the ON state says what **MOVED** — which elements
  changed rank and at which frequency — not merely "checked". **A stable
  ranking is a RESULT and must be said in those words**, not rendered as the
  absence of a complaint.
- **Both badge states are FRONT-LOADED, because this Label clips.** It is
  `wraplength=0` (the `_footer_strip_text` rule — a wrapping strip costs plot
  height), so what is written last is not on screen. Measured on the real
  widget, the offer being 237 characters and the STABLE verdict 152:

  | | 1500 px | 980 px | 720 px |
  |---|---|---|---|
  | offer, 100% | 238 | 156 | 111 |
  | offer, 150% | 104 | **64** | **46** |
  | verdict, 150% | — | **65** | 48 |

  With the caveat first, at 150% / 980 px — the DEFAULT size — the reader saw
  `across frequency: not checked — a ranking read off ONE frequency` and
  nothing else: the gesture and the cost were both off screen, and at 100% /
  980 px the cost was too (the visible text ended `… across the band: 4`). The
  STABLE verdict clipped before `nothing changed places`, and a real NOT-stable
  verdict off a 32-port file clipped before the moved ranks and their
  frequencies — in each case exactly the words the rule above demands. So the
  order is **verdict → gesture → cost → caveat** and **verdict → what moved →
  span**: after the change the whole action and cost fit at 150% / 980 px
  (`not checked — press ▸: 4 more solves (4-port file`) and the gesture
  survives even at the 150% minimum.
- **THE GROUND MODEL IS SECOND ON THE SIGN STRIP, ahead of the shares rule,
  and that is a measured trade.** The strip is 137 characters and clips like
  every other one: 137 / 137 / 114 at 100% and **106 / 66 / 48** at 150% for
  1500 / 980 / 720 px. With the model last, `'Grounds:' in shown` was **False
  at 980, 860 and 720 px at 150%** — at every supported size at that scaling
  bar a maximised window there was no on-screen statement of the model that
  produced the numbers, for a choice worth a measured **7.19 dB** on the
  shipped fixture. "It is also on the control" is not a substitute: the control
  shows the FIELD, which can have been edited without a Recompute, while the
  strip shows what is IN FORCE (it renders `prov.ground_model_label`, never the
  Entry). What clips instead is the shares rule, which is also in the table's
  column heading, in Help and in the README, and which cannot change a number.
- **`_attr_zt`'s NOTES are carried, and the first thing they can say is that
  the model was not applied.** The window used to discard them
  (`zt, _gm_notes = ground_model_zt(...)`) while the CLI prints them in
  `header_notes`. Measured on `coupled_4port_diff.s4p`, probes 1/3, one
  connection row `2 short_to 4` (a legal spelling of the same network),
  `shared:L=1n` + [Recompute]: `_attr_zt` returns `zt is None` with *"The
  ground model was ignored: this spec declares no shunt element … there is no
  ground lead to model."*, so the numbers stay the DECLARED network's and
  `reference_applicable` stays **True** — while the sign strip read
  `Grounds: shared:L=1n` and both exports headed the block
  `Ground model: shared:L=1n`. A reader who typed `shared:L=1n`, saw the number
  not move and read that strip concludes the shared return is worth 0 dB, in
  the one flow that exists to settle a 6.07 dB dispute. The discriminator is
  free (`gm_z is not None and zt is None`); the marker goes on
  `ground_model_label`, so ONE assignment reaches the sign strip, Copy report
  and the CSV rather than three that can disagree, and the full sentence goes
  on the hint Label beside the control (no new pixels — it replaces the
  standing hint only while there is something more urgent to say there).
- **A session-restored ground model that no longer parses says so.** The
  retry-without-it is right — a bad value costs its own field, never the
  window — but the second half of that rule is that every other dropped field
  in the session code notes itself in the Results pane. Without it the window
  opens on `diag` with the saved model silently gone, and a ground model is
  worth a measured 7.19 dB, so a silent revert to the default is a silent
  7.19 dB.
- **The across-frequency button is a ONE-SHOT, not an expander.** Measured:
  press → `▾` plus the verdict; press again → the glyph went back to `▸` and
  the label text was UNCHANGED, still the verdict, with `_badge_row` 27 px
  throughout. `_expanded` gated no content — only the glyph — so the collapse
  offer was inert, and re-running would spend four more solves on an answer
  already on the line beside it. It is therefore disabled once `res.stability`
  is set, and the reason a disabled button owes the reader (the Keep button's
  rule) is the verdict in the Label next to it. [Recompute] builds a fresh
  `AttribResult` with `stability=""`, which makes it live again.
- **A session restores the CHOICES, never the WINDOW.** `attribution_refusal`
  turns away a trace with no numbers, and a freshly loaded session has none
  until Calculate has run — so auto-reopening would show nothing but the
  refusal, once per saved entry, before the user has asked for anything. What
  is kept is the pair, the quantity, the frequency, the view **and the
  Candidates field**, so reopening from the menu lands on what was being read.
  Candidates is on that list because it is the one field here the user TYPED
  and that this tool refuses to guess (`STRUCTURAL_CANDIDATES`' docstring says
  so in as many words); it was missing at first, so the round trip handed back
  the default pair and the only part of the state that cost anyone any thought
  was the only part not kept. `view` and `candidates` are also APPLIED on
  reopen — both were being written to the file and stored in `_RESTORED` and
  then never read, which is the same as not saving them. `_apply_session`
  **names**
  every entry it did not reopen in the Results pane. Same shape as the frozen
  trace coming back without its numbers and saying so twice; same rule as the
  rest of the session file — a bad value costs its own entry, never the file,
  and `apply_attribution_session_state` never raises.

### The cold-start screen (`--cold-start`, CLI only)

Which ports matter BEFORE a spec exists. `tests/test_attrib_coldstart.py` and
`tests/test_attrib_cli_coldstart.py` are the guards; the mathematics is
`docs/theory.md` §13.14 and the rationale is
`docs/design_port_attribution.md` §14.

- **`decompose` is STRUCTURALLY BLIND to this case and that is why the screen
  exists.** With every port open there are no elements, so `m = 0`, `U` is
  empty and the contribution table is empty — and the all-open configuration is
  exactly the one that produced the disputed number. `sensitivity` does reach
  undecided ports but is framed as "check the spec you already wrote", which is
  a different question. Do not "fix" this by folding hypothetical elements into
  `decompose`; the contribution table ranking DECLARATIONS is an invariant.
- **It is READ OFF THE EXISTING MACHINERY, not reimplemented.** One
  `AttribContext` whose `TerminationSet` is "the probes plus one ideal ground
  per candidate" gives all four steps: `ctx.Dmat` is all-open, `ctx.Zop` is
  all-grounded, `ctx.Rmat[e,a]` is `Zbase[a,p]`, `ctx.Pmat_b[e,b]` is
  `Zbase[p,b]`, `ctx.Gm[e,e]` is `Zbase[p,p]`, the one-element solve IS the
  closed form, the two-element solve is the pair scan, and
  `leave_one_out(ctx, …)` is the mirror with no new code. Probe membership
  comes from `_probe_side_of_port` (`merge_terms`' rule), so a short that
  defines a probe side survives the rewrite; every other declaration is dropped
  and **named** in `notes`.
- **The closed form is `dZ_ab = -Zbase[a,p]·Zbase[p,b]/Zbase[p,p]`, and it is
  EXACT.** It is §13.3 with one element and `Zt = [0]`, and equivalently the
  Schur complement of deleting row/column `p`. Verified against an HONEST
  re-solve through `compute_z_matrix` with a rebuilt `TerminationSet`:
  **1.47e-11** worst on the planted 12-port case, `<= 5.8e-11` over every
  fixture; re-measured while writing the docs on `diff_pair_4port.s4p` at
  5.0005 GHz (probes 1/2, candidates 3/4), **7.11e-13** and **8.30e-13**. The
  mathematics needs only **two solves plus the diagonal**; the implementation
  builds the whole baseline once because the other steps need it.
- **BOTH COUPLING COLUMNS ARE MANDATORY AND MUST NOT BE COLLAPSED INTO THEIR
  PRODUCT.** Measured on the planted case: the port with the **largest**
  `|Zbase[a,p]|` in the file (34.777 Ω, 67% more than the real path's 20.873)
  has `|Zbase[p,b]| = 0.038` and a true effect of **-0.378 pH** against
  **-395.369 pH** — ranked on coupling-to-the-victim alone it is FIRST and
  worthless, ranked on `|dM|` it is fifth of eight. The repo's own fixture
  makes the converse point and is the cleaner demonstration: on
  `diff_pair_4port.s4p` at 5.0005 GHz port 3 reads `|Z_ap| = 15953.3`,
  `|Z_pb| = 7.89368` and port 4 reads them **swapped** — a factor of 2021 —
  while both have the **same effect to twelve digits** (`+7.93284 Ω`). Rank on
  either column alone and one of two identical ports is first and the other is
  last.
- **THE PAIR SCAN IS NOT OPTIONAL.** Measured: a shield brought out as two
  ports reads **+9.689 pH** with either end grounded alone and **-870.268 pH**
  with both — **89.8x** the largest single-port effect, with the **OPPOSITE
  SIGN**. A single-port ranking reports it as two minor positive entries.
  `5 short_to 6` with no ground anywhere gives the identical -870.268 pH, which
  proves the mechanism is the closed **LOOP** and not the grounding. The
  algebra says where the surprise lives: with `Zbase[p,q] = 0` the 2x2 `H` is
  diagonal and the two effects add **exactly**, so the non-additivity is
  entirely how much the two candidates talk to each other.
- **THE MIRROR DIRECTION IS ALSO MANDATORY, and it is a different failure, not
  a check.** Leave-one-out from ALL-GROUNDED: 60 ground balls read `~0` each
  because the other 59 carry the return, while the shield reads **+880 pH** per
  end. One-at-a-time-from-all-open and leave-one-out-from-all-grounded catch
  opposite failures (loop closure versus parallel-return saturation) and
  neither subsumes the other. On a large file the mirror is also the expensive
  half — measured **9.3 s of a 9.5 s** four-step report at 151 candidates.
- **A pair is FLAGGED against a threshold that is REPORTED, and nothing is
  hidden by it.** `max(COLD_START_PAIR_REL * largest single-port |delta|,
  COLD_START_PAIR_FLOOR_REL * |all-open value|)` = `max(0.5·…, 0.01·…)`. The
  first term is the one that means something; the second is a floor for the
  case where every single-port effect is `~0` — the normal reading of a shield
  and of 60 balls from all-grounded — without which the first term collapses
  onto the noise and flags all 28 pairs. Measured: planted case threshold
  197.7 pH, **no** pair clears it (largest non-additivity 5.40 pH — correct, no
  pair mechanism was planted); shield case threshold 4.84 pH, the one pair
  clears at 889.6 pH, **184x**. Every scanned pair is returned, ranked, each
  carrying the threshold it was judged against.
- **STEP 0 COMES FIRST AND IS PINNED THAT WAY.** The open..ideal-ground bracket
  answers "is any of this worth my time" before anything else is computed
  (measured **25.67 dB** on the planted case), and
  `test_the_BRACKET_comes_before_the_RANKING` pins that pair on its own. It is
  **not** a bound over all terminations — a reactive termination leaves the
  Möbius arc; measured on `diff_pair_4port.s4p`, one ground's series `L` swept
  over `[0, inf)` peaks at 9 mH of apparent `M` at `L = 505 nH` against a
  1.01 nH bracket. `COLD_START_BRACKET_CAVEAT` is one string so every export
  carries it verbatim, the `SIGN_CONVENTION_TEXT` rule.
- **THE NEGATIVE RESULT IS REPORTED AS A RESULT.** "The other N ports are all
  below X dB, so the coupling is LOCAL" is what lets a designer stop looking,
  and a screen that only prints a top-10 cannot say it.
  `COLD_START_LOCAL_DB = 1.0` is anchored on the 6.07 dB dispute this whole
  module exists to settle: a port that cannot move the answer by 1 dB is not
  part of that argument. It changes no number, only the sentence.
- **A NAME FAMILY IS A PROPOSAL THE TOOL TESTED, NEVER AN ASSUMPTION IT FOLDED
  IN.** The requirement was explicit that which port is ground is a semantic
  judgement and the script must not guess it. So the NUMBERS are computed both
  ways and the GROUPING stays a sentence ("ports 5,6 share the name family
  'guard_ring'; tested together they are -870 pH, tested separately +9.7 pH
  each — if they are one structure, group them"). **Nothing else in the report
  depends on port names at all**, and the test suite pins that by running the
  whole thing twice. `COLD_START_MIN_FAMILY = 2`, deliberately **not** core's
  `OPEN_CLUSTER_MIN_FAMILY = 4`: that threshold keeps a REMNANT check from
  crying wolf about `coil1`/`coil2`, while the case this one exists for is
  exactly a two-member family.
- **WHAT IT CANNOT FIND goes on the screen, not in a footnote.** Anything
  needing THREE OR MORE ports to move together: step 1 is first order in the
  candidate set, step 2 exactly second, and step 3's greedy walk can stumble
  onto a triple with no guarantee. `COLD_START_BLIND_SPOT_TEXT` is the one
  string that says so and it is printed by the report.
- **`context=` is KEYWORD-ONLY on every step, and the four contract signatures
  are positionally exact.** The context is the only `O(N³)` piece (measured
  350.6 ms at 153 ports) and every step off it costs microseconds to
  milliseconds, so building four of them is the one expensive mistake
  available. `cold_start_report` shares it for you.
- **`--cold-start-cumulative` takes an explicit K because a bare "every
  candidate" was a 55-second trap.** Measured at 151 candidates: 132 ms at
  `k = 12`, 237 ms at `k = 24`, **54.9 s** at `k = 0`. Step 3 is always run —
  it is the only step that answers "how many ports matter" and at the default
  it is 132 ms of a 9.5 s report, so there is nothing to opt out of. All three
  cap flags default to `None` so `_cold_dependent_flags` is EXACT where
  `_attr_dependent_flags` (which compares against a substituted default)
  cannot be.
- **`--attribute` and `--cold-start` together are ALLOWED, cold start last.**
  The attribution explains the `M` printed just above it and must stay next to
  it; refusing the combination would only force the file to be read and
  inverted twice (measured 132 ms + 675 ms on a 16 MB 153-port file).
- **Both port names go on a line UNDER the pair table, not in the cell.**
  Measured: `guard_ring1` / `guard_ring2` in one cell truncate to two identical
  `guard_rin~` stumps, because `_trunc` keeps the HEAD — the same failure
  `freeze_label` was fixed for. Putting the names in full underneath took the
  table from 110 to 89 columns as well.

### Connection table (the Mode 5 / Mode 6 row editor)

Design note: `docs/design_connection_table.md`. Stages 0-3 are done; stage 4
(modes reframed as presets that seed the table) is specified there and
deliberately unstarted — it rewrites the editor skeleton and needs a human
looking at the screen.

- **The DSL's leading port field takes `parse_port_range`, not `int`.** `6:1:14 ground`
  is one line, which is what lets a table row hold a package's ground balls without
  one row per ball. A single port number still parses to a one-element list, so every
  pre-existing spec and every golden case is unaffected. `short_to` takes a range on
  both sides (shorting is transitive, so the chained-pair spelling `parse_short_pairs`
  uses is unambiguous); **`lumped_between` refuses one on its right** — an N-to-M lumped
  element is ambiguous (star? mesh?) and guessing would be a silent wrong answer.
- **Rows reach a `TerminationSet` through the DSL text, never by building one directly.**
  `build_terminations_rows` = `parse_custom_termination_text(rows_to_dsl_text(...))`.
  One parser, one set of error messages, one thing for the tests to pin — and the "edit
  as text" view then shows literally what is computed.
- **`rows_to_dsl_text` emits measurement ports BEFORE connections, and that order is
  load-bearing.** The DSL is last-assignment-wins, so a later `ground` row must win over
  a probe on the same port — that is the "ground wins" precedence
  `build_terminations_mode1/2/3` have always had. Reversing the order makes a table
  seeded from a named mode answer a different question.
- **`build_terminations_mode1/2/3` let ground win over a probe; `build_terminations_coupling`
  raises on the same overlap.** Both are intended. **The golden reference does not guard
  this** — `tests/_golden_capture.py` calls the builders directly, so any new path to a
  `TerminationSet` bypasses every golden case. `tests/test_core.py::TestTerminationPrecedence`
  and `tests/test_connection_rows.py::TestRowsReproduceNamedModes` are the guard. Anything
  claiming to reproduce a named mode must satisfy them, including the overlap cases.
- **The coupling path is chosen by the measurement-port count, not the mode number.**
  A Mode 5 spec with two probes used to go to `compute_z`, which returns `Zmat[:, 0, 0]`
  and warns that the rest were ignored — a wrong number with no visible difference. Once
  both modes share an editor, "I defined two probes" has to mean the same thing in both.
  Single-measurement-port specs still take `compute_z` so they stay bit-identical.
- **`RowTable` is a Canvas plus a grid of real widgets, NOT `ttk.Treeview`.** Treeview has
  no cell editors: it means floating Entry/Combobox widgets over cells and hand-managing
  placement, tab order and scroll offset, and the overlays misalign under Win11 DPI
  scaling. Its mousewheel is bound on `<Enter>`/`<Leave>`, **never `bind_all`**, or the
  table and the Matplotlib canvas fight over every scroll event. Both `<Configure>`
  bindings are needed (inner frame -> scrollregion, canvas -> inner frame width).
- **`TraceConfig.mports` is a LIST, so Duplicate must copy it element-wise.**
  `TraceConfig(**src.__dict__)` is a shallow splat and handed both traces the same list;
  editing the copy's measurement ports then silently edited the original's, with no
  symptom but two curves quietly agreeing. `_duplicate_trace_config` exists to make that
  testable. Same trap for any future list-valued field.
- **`mp1_*` / `mp2_*` / `mp_more` are retired but must keep loading.** `migrate_legacy_mports`
  folds them into `mports` the same way `migrate_legacy_mode` folds mode 4 into mode 2.
  It splits the old `name = +ports / -ports` lines **textually**, not via `parse_mport_spec`,
  so ranges survive as ranges and a malformed old line migrates instead of raising during
  load (it fails later, at Calculate, with a message that names it).
- **`pack` UNMAPS what does not fit, starting from the end — so pin fixed sections first.**
  A fixed-size section packed *after* an `expand=True` sibling disappears entirely once the
  sibling outgrows the panel; it is not clipped, `winfo_ismapped()` returns 0. Measured: in
  mode 6 at 1500x900 the whole "Global Controls" frame was gone, i.e. **Calculate All &
  Plot / Export CSV / Help were not on screen**, while modes 1/2/3/5 looked fine — which is
  what made it read as flaky rather than broken. `Global Controls` is therefore packed
  `side=BOTTOM` **before** the editor, and the editor's footer (`Calculate This Trace`) is
  packed `side=BOTTOM` inside the editor **before** the scrollable body. Do not reorder
  either, and do not empty the footer — Tk does not reissue a geometry request when a
  master's last slave is removed, so an emptied footer keeps its requested height forever.
- **The editor form lives in a Canvas and must have its scrollregion refreshed on every
  mode change.** `_update_mode_visibility` uses `grid()`/`grid_remove()`, and the inner
  frame's `<Configure>` does NOT fire usefully for that: the scrollregion keeps its old
  height and the view stays scrolled down, parking a now-short form out of sight.
  `_refresh_editor_scrollregion` handles it — via `after_idle`, never `update_idletasks`.
- **Never call `update_idletasks()` while the UI is being built.** It flushes geometry for
  the WHOLE application, not just the calling widget. `RowTable` did this to auto-size
  itself, and because `_build_left_panel` runs before `_build_right_panel`, the flush
  pinned the right-hand `ttk.PanedWindow`'s sash at 2px — the entire **Results pane
  disappeared**, with `_build_right_panel` untouched in the diff. Defer to `after_idle`
  (`RowTable._schedule_resize`, which also coalesces repeats and checks `winfo_exists`).
  `tests/test_row_table.py::TestResultsPaneVisible` is the guard; it needs a MAPPED
  window, because `sashpos()` reads 0 on a withdrawn root whatever the layout is.
- **Populate a pane before `PanedWindow.add()`ing it.** ttk sizes a pane from its requested
  size at `add()` time and never recomputes, so adding an empty frame and filling it after
  works only while nothing forces a geometry pass in between. This alone did NOT fix the
  bug above (measured: reverting it leaves the test green) — it removes the latent
  fragility that made an unrelated widget able to trigger it.
- **A table cell cannot hold a placeholder hint, so the hint is a permanent label under
  the table.** `PlaceholderEntry` / `PlaceholderText` delete their hint on `<FocusIn>` —
  that deletion is the mechanical reason nobody could remember the syntax. Do not
  "restore" per-cell placeholders, and do not wire `ColumnSpec.placeholder` into
  anything. A table-based mode therefore registers **no** `MODE_PLACEHOLDERS` entry.
- **Port cells take NUMBERS (or a net name); `Show Ports` is the only route to the file's
  port names.**
  A name-bearing dropdown does not fit the editor width (design note §5a — a ttk popdown
  is only as wide as the widget, and 15 chars ≈ 105 px the 431 px viewport does not have),
  so it is deferred to stage 4. `Show Ports` is no longer a *substitute* for it — it opens
  the **Ports & Roles** window, which carries the name, the role and the source per port
  and writes a selection back as a collapsed range, i.e. strictly more than a 105 px
  popdown could. It still has to be *findable*: it is named in both table hints, in
  Help → Mode 5 and Help → Input syntax, and in the README, and `_on_show_ports` falls back
  to the editor's file rather than silently doing nothing when the Files listbox has no
  selection. If the dropdown ever carries names it is an ADDITION and those five pointers
  stay.
- **`TraceConfig.custom_text` is retired-but-loading, exactly like `mp1_*`.** `conn_rows`
  + `extra_lines` are the storage; the DSL text is a DERIVED view (`_editor_dsl_text()`).
  `migrate_legacy_custom_text` folds an old free-text spec in on load and
  `_sync_editor_to_trace` never writes the field again — two stored copies would leave
  the migration guard unable to tell a legacy trace from a freshly synced one, and the
  text would overwrite the rows on the next selection. The guard is on **three** fields
  (`mports`, `conn_rows`, `extra_lines`), because unlike `mports` no single one proves
  the conversion happened. `_migrate_trace` runs it **after** `migrate_legacy_mports`, so
  a config carrying both does not merge two unrelated specs.
- **A text→rows import that would change the answer keeps the text verbatim in
  `extra_lines`.** `dsl_text_to_rows` drops line order and `rows_to_dsl_text` hoists every
  probe above every connection, so `3 ground / 3 signal A / 4 signal B` — where the probe
  deliberately overrides the ground — comes back with port 3 grounded and
  `resolve_meas_ports` then raises. `_import_dsl_text` compares the resolved
  `TerminationSet` (per-port types, couplings, measurement-port triples) before and after
  and falls back; `extra_lines` is re-emitted unchanged, so the fallback is bit-identical.
  It also **never raises**: that, not the accident of `dsl_text_to_rows` being total, is
  what makes "a malformed old spec migrates instead of failing to load" hold.
- **The connections table's column widths are a measured budget.** 405 px table / 418 px
  form against a **431 px** viewport (the editor canvas once its vertical scrollbar shows,
  which in Mode 5 is always) — so the headroom for a new column is **13 px**, not 22.
  405 px is now the WORST case (every Kind present at once); per-kind row shape gives
  most of it back — see "Per-kind row shape" below.
  Re-measure `_ed_form.winfo_reqwidth()` before adding one; the in-file comment beside
  `CONN_TABLE_COLUMNS` carries the same two numbers. The table is gridded across all four
  form columns with its caption **above** it, because a label beside it costs 91 px. The
  editor canvas has an **x-scrollbar** as the safety net — the budget is a 100%-font
  number and no column set fits at 150% DPI. It also fixes a pre-existing Mode 6 defect:
  form 463 vs canvas 431, `xview (0.0, 0.962)`, 32 px of the ✕ column unreachable with no
  way to scroll to it.
- **A mode with NO table must fit the 431 px canvas outright, and its two widest
  fields are sized to make it.** The editor form's requested width is the widest LABEL
  (129 px, `GND / VDD (AC gnd):`) + the widest FIELD + 8 px of cell padding, and the
  horizontal scrollbar costs 17 px of a 45 px viewport at the 1040x600 minsize. Modes
  1/2/3 measured **440 px** against 431, `xview (0, 0.98)`, canvas height **28** — they
  paid a third of their remaining height to reach **9 px** of overhang, while Mode 5,
  whose column budget WAS measured, fitted at 417 and paid nothing. That is the same
  28 px the empty-host-frame rule below was removed to fix, arriving from the other
  direction. The File combobox is therefore `width=38` (303 → 289 px) and the four
  single-line fields `EDITOR_FIELD_CHARS = 40` (300 → 286 px), for a 426 px form and
  the whole 45 px back; both are `sticky="we"`, so this is only their MINIMUM and
  nothing looks different at any width where the form fits. **Mode 6 is deliberately
  not in that set** — its measurement-port table genuinely overhangs (462 px) and
  keeps the bar. Guarded per mode by
  `tests/test_mode5_editor.py::TestEditorLayout::test_a_table_free_mode_does_not_pay_for_a_scrollbar_it_barely_needs`;
  re-measure `_ed_form.winfo_reqwidth()` before widening any field or lengthening any
  label in the form's first column.
- **Both editor scrollbars are decided in ONE function, `_apply_editor_scrollbars`.**
  Autohiding each off its own `yscrollcommand` / `xscrollcommand` is a **limit cycle**,
  not a race: hiding the horizontal bar gives the canvas 17 px of height back, which can
  hide the vertical bar, which gives 17 px of width back, which brings the horizontal bar
  back. Measured with the two decisions split: the editor flipped `431x245 <-> 448x228`
  forever and `update()` never returned — the whole GUI hangs, and so does the test suite.
  The single decision reads the **body frame's** size and the **form's requested** size,
  neither of which a scrollbar packed inside `body` can change, so it is a fixed point.
  `_ed_scroll_set` / `_ed_hscroll_set` move the thumb and nothing else. Both bars are
  packed `before=self._ed_canvas` — pack unmaps from the end, and an `expand=True` canvas
  packed first leaves nothing for either. There is deliberately **no host frame** for the
  horizontal bar: Tk does not reissue a geometry request when a master's LAST slave is
  removed, so an "empty" host keeps a 17 px requested height forever, in every mode
  (measured at the 1040x600 minsize: 45 px of editor viewport became 28).
- **`_refresh_editor_scrollregion` has two entry points.** `preserve=False` for a mode
  change (a now-short form must not stay parked out of sight); `preserve=True` for a row
  add or a hint toggle (or the row the user just created scrolls away). Within one pending
  batch a reset wins over a preserve, and the flag is re-armed each time it is scheduled —
  a sticky `False` swallowed every later row add. Both the canvas's **and the form's**
  `<Configure>` must re-measure: a table grows the form one idle pass *after* the row was
  added, so a scrollregion measured from the row-add callback alone is one row short.
- **The strips run inside Tk variable traces.** `_apply_editor_strips` is
  `after_idle`-coalesced, must never raise (a raised error there reaches no handler you
  control — Tk prints it and the GUI carries on showing a stale "spec is fine"), and must
  write to nothing but its three Labels (overview, validation, and the kept-as-text marker
  on the Connections caption), the style preview, and the Port / To dropdowns' combobox
  **choices** — never a cell's VALUE. That last one is what lets the merged-node entries
  follow the short rows as they are typed (`_refresh_port_choices` is called from here);
  `set_column_values` early-returns when the list has not moved, which on a keystroke is
  the normal case. Writing a choice list cannot alter the spec, which is the property that
  keeps `_sync_editor_to_trace` the only writer to a `TraceConfig`. `_on_editor_rows_changed` returns early while `_suppress_editor_sync` is
  set — that flag was inert before stage 3, and leaving it inert while adding an
  `on_change` is exactly how it becomes a re-entrancy bug.
- **A green tick has to mean "Calculate will work".** `_validation_messages` reports every
  way a row contributes nothing or something other than it looks like — no Port, no R/L/C
  at all (`y_series_rlc(0, 0, inf)` is `1/0`, so Z is NaN at every frequency while the
  emitted warning blames the measurement port's return path), a name-only measurement-port
  row, and **no measurement port at all**. The last one is why an empty Mode 5 editor says
  so instead of "✓ no problems found" and then raising at Calculate. Echoes are one
  message per element row, not one line naming the first, because `5m` vs `5M` is a
  property of the row it is on; `_validation_strip_text` caps the strip at
  `VALIDATION_STRIP_LINES` and `_on_calculate` prints the whole list to the Results pane,
  which is what makes "… +N more (see Results)" true.
- **An R/L/C cell must be ONE token, and `_rlc_tokens` raises otherwise.** The DSL is
  whitespace-separated and `parse_kv_rlc_params` drops any token without an `=`, so
  `R=5 m` computed 5 Ω where 5 mΩ was typed and `C=1 uF` computed 1 farad — with the
  validation strip cheerfully echoing "5 mΩ" beside the 5 Ω, because it re-parses the raw
  cell as one token. There is no way to quote a value in the DSL, so refusing is the only
  answer that cannot be silently wrong. The unit lives in the column header. `_rlc_echo`
  carries the same guard so it never echoes a value that will not be computed, and
  `_on_edit_as_text` catches the raise (it would otherwise be an unhandled Tk traceback
  with the dialog half-built).
- **`extra_lines` has a permanent marker on the Connections caption.** It is the only part
  of the spec with no widget of its own and `rows_to_dsl_text` emits it LAST, so it wins
  over everything typed into the two tables. After a verbatim-kept import both tables are
  empty while it decides the whole answer; `_extra_lines_indicator` says "(+N lines kept as
  text)" on the caption row (no vertical cost) and the validation strip names the
  measurement ports that come from it and not from the table. `_port_descriptor` shows the
  text for a Mode 5 trace with empty tables and appends `+txt` when both are present —
  "M5: (no probe) C:0" was a false claim in the column the user reads to confirm what ran.
- **`TCombobox` is no longer in `App._WHEEL_OWNERS`.** Three of the connections table's six
  columns are comboboxes, so with it there the router bailed out and *nothing* scrolled
  over half the table. It is safe because `unbind_class("TCombobox", "<MouseWheel>")` in
  `_install_wheel_router` already removed the value-changing class binding; an OPEN
  dropdown is a `Listbox`, still in the set, and still scrolls itself.
- **The Mode 5 table lets ground win over a probe; Mode 6's builder raises on the same
  overlap.** Both are pinned and intended. The validation strip is where Mode 5 makes the
  overlap visible — it must report it, not raise, and not "fix" it.
- **A lumped element the reduction ANNIHILATES must be reported, not echoed.**
  `compute_z_matrix` stamps every lumped element onto Y and only then merges shorted
  ports and drops grounded ones, so a `lumped_between` whose two ports land on the same
  merged node has its `+y, +y, -y, -y` block summed to exactly zero, and one with both
  ends grounded (or a `lumped_to_gnd` shorted to a grounded port) has its row and column
  deleted. Nothing raises and the number on screen does not move: measured on the
  reported case, `5 short_to 6` next to `5 lumped_between 6 R=…` answered identically
  for R=20 and R=2000 (5e-12 relative, i.e. roundoff), and the strip affirmed
  `✓ port 5 → 6: 20 Ω` beside it. `inert_lumped_messages` in core is the guard and it
  runs BEFORE the `✓` echoes so the green tick is suppressed, not merely outranked.
  Its Union-Find deliberately duplicates `compute_z_matrix`'s rather than refactoring
  the one function the golden reference exists to pin. It must mirror `merge_terms`'s
  precedence — a Signal on the merged node beats a Ground, so that case is NOT inert —
  and must not fire when only ONE end is grounded, which is the ordinary way to spell a
  shunt element. `tests/test_mode5_editor.py::TestValidationMessages` pins both the
  four positives and the two false-alarm cases.
- **Mode 5 passes `nports` to `build_terminations_rows`.** It used to pass none, so a
  one-digit typo (`3 / 5` on a 4-port file) became a plausible wrong number until
  `compute_z_matrix`'s backstop. Likewise the CSV exporter gates the coupling block on
  `tc.Zmat is not None`, the same predicate `_on_calculate` routes on — gating on
  `tc.mode == 6` exported a two-probe Mode 5 trace as a well-formed scalar table with
  every M and every k silently absent.

### Per-kind row shape, nets, and the parallel stamp (round 1)

The user's complaint, verbatim: *"不同的连接，出现的表格都是一样的，比如多个pin连接
到一起的时候，我很自然的感觉就是一个blank，输入我要短接的PIN就行，但是现在有两个
blank"*. The shorting example is the instance; the complaint is that every Kind gets
the same table. `tests/test_conn_nets.py` (core) and `tests/test_conn_rowshape.py`
(GUI) are the guards, and every claim below was mutation-checked.

- **`RowTable` lays out through a `TableLayout`, and `_regrid` is GONE.** `layout_fn`
  returns one frozen `TableLayout` for the WHOLE table — `ncols`, `headers`, `weights`,
  and per row the cells it shows as `(column key, grid column, columnspan)`. It is one
  function and not a per-row `shape_fn` because the two decisions are coupled: a cell
  may spread only into grid columns NO row is using, and those are exactly the columns
  whose heading must be blank. Two hazards made the obvious implementation wrong and
  both fail silently: the old `_regrid` re-gridded **by list position** (it enumerated
  `entry["_widgets"]` and called `grid_configure(column=c)`), and `grid_configure`
  **re-manages a `grid_remove()`d slave**, so a per-kind widget set both misplaced cells
  and un-hid them. `_widgets` therefore stays parallel to `_columns` — `set_column_values`
  and the frozen-state path index it and are untouched — and only the grid column varies.
  `_apply_layout` recomputes on every cell write and re-grids only when the layout
  actually moved (`TableLayout` is tuples, so `==` is total). It CANNOT oscillate: it
  reads cell TEXT and writes GRID OPTIONS, so nothing it writes can change what it reads —
  the same fixed-point property `_apply_editor_scrollbars` needs and for the same reason.
  Measured per variable write on a six-row table: **31 µs** when the layout does not move
  (15.6 µs of it deriving the layout to find that out) against **263 µs** on a Kind change.
- **The HEADER follows the rows, and that is half of R1-1.** A shared grid header states
  a column's meaning once, so `To` was a lie on a short row even with the cell hidden.
  Grid column 2's title is derived — `""` / `"To"` / `"Net"` / `"To / Net"` — and the
  R/L/C titles vanish when no row carries them.
  `test_a_cell_never_spreads_under_someone_elses_heading` and its converse check all
  **63** kind subsets. An unrecognised kind keeps all six cells: a session hand-edited to
  a kind this build does not know must not lose its values.
- **Measured table `winfo_reqwidth()`, Microsoft YaHei UI 9 (150% in brackets):**
  ground only **405 → 202** (413 → 210), short only 405 → 273 (281), rlc_gnd only
  405 → 331 (339), **every kind at once 405 → 405** (413) — i.e. the worst case is
  exactly the table this replaces, so the 13 px headroom rule above is unchanged.
  A rendered ground row's Port cell goes 76 → **185 px**. The inert cells it recovers:
  a ground row wasted To+R+L+C = 195 px = **48%** of the table, a short row R+L+C =
  123 px = 30%, an rlc_gnd row To = 72 px.
- **The Net cell shares grid column 2 with To; it is NOT a seventh column.** `width=9`
  is measured: grid column 2 is 74 px because a 7-char `ttk.Combobox` asks 72, and a
  `ttk.Entry` asks 55 / 62 / 69 / 76 px at 7 / 8 / 9 / 10 chars — at 9 the cell costs
  the column nothing, at 10 it would take 4 px of the 13. The widest candidate title
  `"To / Net"` is 48 px, also free.
- **A short row stores its whole tied group in `ports` and leaves `to` empty**
  (`5,6,7,8 short`), so one cell is the storage as well as the display. `to` stays live
  as the LEGACY two-field spelling — a pre-round-1 session, and the synthetic rows
  `_trace_role_rows` builds for mode 3, both carry `short 5 → 6,7,8` and must keep
  working. `conn_cells_from_row` / `conn_row_from_cells` are the only place that knows
  both spellings; `rows_to_dsl_text` emits `short_to` verbatim for a legacy row, so its
  answer cannot move on load (measured bit-identical, `==` on the complex Z).
- **A net is SUGAR, and it resolves to ONE representative member port.** Referring to a
  merged node by any one member already worked (measured: after `1 short_to 2,3`,
  `1 lumped_between 4 L=10f` gives 10.000 fH); what was missing was a refusal and an
  affordance. Expanding a name to every member would reintroduce the ×N bug inside the
  sugar meant to remove it. `_collect_nets` is a separate PRE-PASS because
  `rows_to_dsl_text` emits every measurement port above every connection, so a probe on
  a named node is always a forward reference — a single-pass parser would refuse it for
  a reason that is pure table layout.
- **Net-name rules, each from a measured collision.** A name `parse_port_range` would
  accept is refused (the port field is the one slot where a number and a name share a
  token — the collision `reduce_snp.py` already documents); `A`/`B` plus the DSL's own
  keywords are reserved; no whitespace and none of `:,-#` (`:` is also what a future
  file prefix may not use — `parse_port_range('PKG:12')` raises); matched
  case-insensitively, stored as typed; an unknown name is a HARD refusal naming the
  defined nets, never a new empty node (that would hang the element off a dangling node
  and change the answer in silence); two names on one merged node is an error, because
  every echo and every Ports & Roles row would otherwise pick one arbitrarily. Only a
  `short` row may carry an `as` — anywhere else it would be dropped by
  `parse_kv_rlc_params` in silence and the user would go on referring to a node that was
  never named.
- **Merged nodes sit at the TOP of the Port / To dropdowns, and `MergedNode.ref` is one
  member, never the group.** The right gesture has to be the cheap one: offering the
  whole group is precisely the spelling that multiplies the element by N.
- **`parallel_stamp_messages` is the refusal, and it fires only when the listed ports are
  ALREADY ONE node.** N separate elements from a range is documented, intended and the
  normal flip-chip case (54 VSS bumps each with its own 20 pH) — never refuse that.
  Measured on the 5-port probe network: `1 short_to 2,3` then `1,2,3 lumped_between 4
  L=10f` reads **3.333 fH** where 10 fH was typed, ratio exactly 3.000; `1 short_to
  2,3,4` then `1,2,3 lumped_to_gnd R=50` reads **15.625 Ω** (250‖16.7) instead of
  41.667 Ω (250‖50). Nothing raises and `inert_lumped_messages` says nothing — it reports
  elements worth EXACTLY zero, these are worth **N times too much**. It returns messages
  and does not raise (the strips' contract), and the GUI appends it before the `✓` echoes
  exactly as it does for `inert_lumped_messages`, so the green tick is suppressed.
- **The effective value is stated PER ELEMENT TYPE — R/N, L/N, N·C — never templated**,
  and only the values the user actually typed are shown (an omitted R is 0 and an omitted
  C is inf; neither is a number anybody wrote down). That is why `params` was added to
  `LumpedToGnd` / `LumpedBetween`: `y_func` is an opaque closure, and without the numbers
  the message could only say "3 identical elements", not "10 fH becomes 3.33 fH".
  `params` is metadata — nothing in the reduction reads it and a set built in code leaves
  it `None`, so the golden reference is untouched.
- **The merged side is found from the PORT LISTS, never from `lo`/`hi`.** Those are
  Union-Find ROOTS — arbitrary integers whose order falls out of which port won its union
  — and the first implementation tested only the `lo` side. Measured: `1,2,3 short` +
  `1,2,3 lumped_between 10 L=10f` was refused while `21,22,23 short` + `21,22,23
  lumped_between 1 L=10f`, the SAME network with the same 3.3333 fH against a typed
  10 fH, was **silent**, purely because port 1's root was the smaller number. Whether a
  user is warned about a factor-of-N error may not depend on their port numbering. The
  final message ORDER is sorted by port number for the same reason: the strip shows two
  lines, so which one is first must be decided by something the reader can see.
  Three exclusions stay and are each pinned: `lo == hi` (both ends on one node — worth
  zero, `inert_lumped_messages` owns it, and two messages about one element would
  contradict each other); one distinct port on each side (the same line typed twice is
  visible on its own row, and "ports 1 are ALREADY ONE NODE" would be false about a spec
  with no short in it); and grouping by `(node pair, R/L/C)` rather than node pair alone,
  so a deliberate R‖L stays silent.
- **The footer verdict is a ROUTE, and it costs zero pixels.** It is the only
  always-visible pixel of the editor, and it was a dead end: measured at the 1040x600
  minsize the messages it counts sit **366 and 387 px** below the fold of a 45 px
  viewport, and every mode change scrolls the form back to the top. `<Button-1>` →
  `_validation_report` → the FIRST message's anchor → `RowTable.see_row` → the editor
  canvas → `focus_set`. **The table scrolls first**: a row past `max_visible=6` is
  clipped by the table's OWN canvas and the editor scroll alone cannot reach it (measured:
  row 7 landed 37 px ABOVE the editor viewport). `see_row` COMPUTES where the row lands
  rather than re-measuring — `yview_moveto` does not reach `winfo_rooty` until the next
  idle pass (measured 192 → 192 with no idle, 164 after one) and forcing one from a click
  handler is the `update_idletasks()` this repo has been bitten by. The affordance is
  `cursor="hand2"` plus a hover underline, measured **191x21 px identical** with and
  without it (291x29 at 150%). It follows the first message's anchor and NO other:
  scanning down for one that happens to have a row would take the reader to a
  lower-priority message's row, answering a different question than the footer is
  counting. Never raises — same contract as `_apply_editor_strips`.
- **Validation messages are ordered by CONSEQUENCE, not by check order.**
  `VALIDATION_STRIP_LINES` is 2 and the footer summarises the rest as a count, so the
  first two messages ARE what gets read. `_validation_report` returns
  `_VMsg(tier, text, anchor)` and `_validation_messages` is its `.text`; the sort is
  STABLE, so row order survives inside a tier. `V_WRONG_NUMBER` (parallel stamp,
  annihilated element, a probe a ground row outranks, measurement-port rows that collapse
  into one, an open-port remnant) → `V_NO_RESULT` (Calculate raises or every value is
  NaN) → `V_ROW_INERT` (this row does nothing — its own cells show it) → `V_OK`. A
  *measurement-port* row that resolves to nothing is `V_NO_RESULT`, not `V_ROW_INERT`:
  it is the measurement itself going missing, and
  `test_mode5_editor.py::test_flags_a_measurement_port_row_with_a_name_but_no_ports`
  pins cause-above-consequence.
- **KNOWN GAP, deliberate: every `V_WRONG_NUMBER` message is UNANCHORED**, so the footer
  route falls back to the validation strip for exactly the tier R1-5 promoted to the top.
  Those messages come from `parallel_stamp_messages` / `inert_lumped_messages` /
  `open_port_name_messages`, which take a `TerminationSet` — by then the row structure is
  gone. Anchoring them needs either a second copy of `rows_to_dsl_text`'s emission order
  (rejected here, and whose drift would show up as the route landing on the WRONG row) or
  a line↔row map out of `rows_to_dsl_text`. The fallback is not a dead end: the strip
  carries the full text of the top message, including the ports it names and the repair.
  Fix it from the row side if it is ever fixed, not by parsing the message.

### Composition — several files as ONE network (`pkg_rlc_compose.py`, round 2)

The user's framing decides everything here: *"我们现在这种相当于用户在自己搭建一个快捷的
TB 了，得到的肯定是**所搭即所得**"*. The connection table is a quick TESTBENCH; the
deliverable is the ABSOLUTE number of the assembled thing, and the before/after delta
already exists as freeze-as-trace. But "what you built is what you measure" RAISES the
bar rather than lowering it: the tool's first duty becomes making *what was built*
unambiguously visible, because when it is not what the user thinks, the answer is a
precise wrong number. `tests/test_compose.py`, `tests/test_compose_cli.py` and
`tests/test_attrib_composed.py` are the guards, and every claim below was
mutation-checked.

- **`block_diag` WELDS the two files' reference nodes, and that is the premise, not a
  footnote.** An n-port Touchstone `Y` is the matrix with its OWN reference already
  eliminated, so stacking two of them identifies `ref_A` with `ref_B` at zero impedance.
  Measured on a 2 nH coil + 100 pH package trace + 100 pH package ground lead: with the
  die return brought out as a PORT and tied to the package ground pad, `L_eff` =
  **2.2501 nH** and it moves when the ground path changes; with the die return being the
  EM reference, the package ground pad grounded / open / through 1 nH all give
  **2.1454 nH, bit-identical, spread 0.000e+00**. The package's entire ground network is
  unreachable and nothing raises. That is the same failure shape as the 6 dB dispute the
  feature exists to settle, arriving through the door this feature is.
- **The reference-node self-check is MANDATORY OUTPUT and has no off switch.**
  `solve_composed` runs it; `reference_check` perturbs each file's declared ground set
  with a series inductor at ONE frequency (the question is topological) with TWO values a
  decade apart, and a delta of `== 0.0` — exact, not a tolerance — is `REF_WELDED`. Two
  extra solves per file. `REF_NO_GROUND` is deliberately NOT folded into `welded`: the
  CORRECT die-return-as-a-port configuration declares no package ground, so folding them
  cries wolf on exactly the composition the feature exists to make work.
- **Y is z0-invariant, so there is no renormalisation step.** Measured: `max |Y(z0=50) −
  Y(z0=75)|` = **1.049e-17**. Each file goes `S -> Y` with ITS OWN `z0` and the blocks are
  stacked. "Renormalise if z0 differs" is a NON-TASK and was deleted from the plan; it
  returns only for an export path, which needs one `z0` for the whole file.
- **Interpolate S, never Y and never Z**, and check the PHASE STEP, not `max |S|`. For a
  passive network S is bounded at every real frequency so it has no real-axis poles, while
  Y blows up at a series resonance and Z at a parallel one. A post-interpolation `max |S|`
  check is **structurally incapable of firing** — `{S : σ_max <= 1}` is convex, so any
  convex combination stays inside (measured max σ = 0.999999900000) — and `max |S|` is not
  a passivity test anyway (all off-diagonals 0.6 gives max entry 0.6 and σ_max 1.80). Do
  not ship one. What interpolation DOES break is phase: `dphi = 2*pi*df*tau`, and the
  chord error `1 - cos(dphi/2)` reads as fake insertion loss and corrupts R and Q.
  Measured: a 1 ns delay at a 100 MHz step is 36.0° → 4.89% amplitude → **0.436 dB** of
  invented loss (warn); 2 ns is 72.0° → **1.841 dB** (refuse).
- **An identical grid is detected with a RELATIVE tolerance (~1e-9), never `array_equal`.**
  A file written in GHz and one written in Hz describing the same sweep differ by
  **2.218e-16** and `np.array_equal` answers False, so the common same-flow case would
  otherwise be interpolated onto itself. The COARSER file's max step is reported as the
  effective resolution — upsampling recovers no information — and a marker frequency
  landing inside a wide coarse interval is flagged.
- **The tag separator is a DOT and must never be a colon.** `parse_port_range("PKG:12")`
  raises "Range must be start:step:stop" today — `:` is already the range separator in
  every port field in this repo. `COMPOSE_TAG_SEP = "."`. The aliases are the repo's own
  `F1` / `F2` idiom from `_format_results_table`, because the measured column budget has
  no room for a file name (one file column 451 px, two 497 px, a Name column 469 px, all
  against a **431 px** viewport) and "port 305" is unactionable on a 316-port network.
- **EVERY warning names its file.** Core's one bare-port-number message is re-raised
  scoped by `_scope_port_error`; the CLI translates the rest with `_COMPOSE_PORTNUM_RE`.
- **`merge_terms` raises with 0-BASED indices, and it is the FIRST message a composed
  network hits.** "Ports [1, 4] merged via short, but assigned to conflicting signal
  groups" carries the Union-Find MEMBER list, which is internal 0-based indices; every
  other message core raises at that boundary is 1-based. Measured: `EM.2` (global 2)
  shorted to `PKG.3` (global 5) reports `Ports [1, 4]`. `_COMPOSE_MERGED_RE` translates it
  with its own offset — translating it as 1-based would name two real, innocent ports with
  total confidence. **This is a defect in `pkg_rlc_core` and it is NOT fixed**: the fix
  moves a message other tests pin, and the CLI's translation depends on the current
  offset. Fix both halves together or neither.
- **The correspondence is the USER's; the tool may PROPOSE and only the user may COMMIT.**
  `--compose-propose` prints and stops, naming any `--compose-link` / `--compose-export`
  it therefore did not run. Elementwise range pairing is a HARD ERROR on a length mismatch
  and ECHOES the END pairs, because an off-by-one in one file's numbering shifts every
  pair silently. Many-to-one is normal (54 VSS balls onto one die pad), N-to-M with both
  above 1 has no defensible order and is reported as ambiguous.
- **Pre-reduction is the edit/recompute loop, not a one-shot run, and the help says so.**
  `_freq_batch` collapses at combined sizes (16→64, 60→4, 76→2, **153→1, 316→1**), so the
  stacked-solve batching `compute_z_matrix`'s docstring justifies at length stops working
  exactly where it is first needed. Measured on this box: 16-port die + 120-port package
  at 201 frequencies, the solve goes **3113 ms → 14.4 ms = 216x** and the answers agree to
  7.4e-16 — but the reduction itself costs 2.5 s, so ONE end-to-end run is 7378 → 6754 ms,
  i.e. **1.09x**. Quoting the 216x for a single run would oversell it by 200x.
  `--compose-export` is the "reduce once, load the small one" route.
- **`--compose-export` writes the STACKED network, not the assembled one, and says so.**
  The links are `ShortPair` / `LumpedBetween` in the `TerminationSet`, and a short MERGES
  NODES and changes the port count; stamping them into `Y` would be a second
  implementation of the merge the golden reference exists to pin, in the CLI layer. The
  report names every link the file does not contain, the file's comments list them, and
  the round trip (`parse_touchstone` → `s_to_y` == `net.Y`, tested at 1e-12) is the
  independent check. `EXPORT_DIGITS = 17`, measured: 9/12/15/17 reproduce S to
  9.210e-11 / 7.235e-14 / 1.777e-16 / **0.000e+00**.
- **The n==2 column-major quirk cannot be caught by a physical fixture.** Every passive
  network has `S12 == S21`, so the transpose is invisible. Any test of it must use a
  deliberately NON-reciprocal 2-port (the guard uses `S21 = 0.6`, `S12 = 0.1`).
- **A limit-case fixture needs UNEQUAL die pads.** With equal pads the EM block is
  port-symmetric and a swapped mapping reproduces the standalone number EXACTLY, so
  `limit_case_check` — which exists to catch a swapped mapping — passes for the wrong
  reason. Measured: 0.0 with equal pads, 1.30e-2 with 2 fF / 8 fF.
- **`--short` is refused on a composed network.** `a-b` cannot say which file each side is
  and `-` is already a range. `--compose-link "EM.3 short_to EM.4"` covers it.
- **`--compose` without `--cli` exits 2** rather than opening the GUI on one file and
  silently dropping the rest.

#### The attribution baseline on a composed network (R2-8)

- **The cross-file links are IN the baseline, not elements on top of it, and that is a
  DELIBERATE GAUGE CHANGE.** The all-open baseline leaves the files as disconnected
  islands, so `Ybase` is exactly block diagonal. Measured with the real engine on a
  12-port combined network: the EM-vs-PKG off-diagonal block is **0.000e+00**, every
  package-only element's contribution is **EXACTLY 0**, and `residual_rel` reads
  **6.49e-15**, i.e. perfect health. Re-measured end to end through this CLI on a 10-port
  case: the package-internal element reads **exactly 0j against a 1.70e-13 residual**
  without the gauge and **−1.939976e-09 H** with it. A confident, exactly-zero,
  perfectly-reconciled wrong answer is the worst output this tool can produce, and no test
  of the attribution arithmetic can see it because the arithmetic is right.
- **`BaselineLinks(blocks=…)`, never an enumerated link list, and there is no flag to turn
  it off.** A `PortBlocks` says "every declared link whose two ports come from different
  files is structure", which cannot MISS a link; an enumerated list can, and a missed link
  is the silent zero above. A link inside one file stays an ordinary element, because it
  is one — the gauge is about the stack, not about the spec. `_compose_baseline` builds it
  from `b.nports` (the SURVIVING count), never `b.nports_original`: after a
  `--compose-keep` pre-reduction the block in `net.Y` is the reduced one.
- **The gauge is NAMED on the report.** `COMPOSED_BASELINE_TEXT` is ONE string (the
  `SIGN_CONVENTION_TEXT` rule) and reaches the header, the decomposition's `baseline:`
  line and the cold-start notes verbatim. Two attribution reports are comparable only when
  their baselines match.
- **A composition with NO cross-file link says so.** The policy selects DECLARED links, so
  with none declared it selects nothing, the baseline is back to all-open, and the header
  still carries a paragraph saying the files are connected. `_compose_gauge_notes` names
  the contradiction; the island warning inside `build_context` cannot, because it only
  fires for elements and a far file with no elements has nothing to name.
- **The cold start needs the gauge MORE than the decomposition does**, because it REWRITES
  the spec — probes kept, every other declaration dropped. Without the policy the
  cross-file links go with them: measured on the 12-port construction, ALL SIX package
  ports come back with `delta` exactly 0.0 and `defined = True`, a screen confidently
  reporting that the package cannot matter. `cold_start_report`'s own `baseline=` is a
  no-op while the CLI passes `context=csc` (`_cs_context` returns the given context
  untouched) and is kept as the safety net for that edit; the one that bites is
  `cold_start_context`'s.
- **`--mport` text is rewritten to GLOBAL numbering before `_attr_sources` parses it, and
  the LABEL stays the text the user typed.** `parse_mport_spec` reads bare integers, so
  handing it `vic = EM.1` is a `ValueError` traceback out of a report the coupling solve
  has already been paid for. `spec_labels` is the display half; a group named after an
  index nobody wrote is the other failure.
- **A cross-file link is grouped under the `--compose-link` that declared it**
  (`link_sources`, walked last because that is the order they enter `term.couplings` and
  the map is last-assignment-wins). Without it every link falls back to its KIND and two
  links on two lines land in one group called `lumped_between`.
- **`pkg_rlc_attrib.Element.describe()` renders GLOBAL indices** ("ground port 10"),
  because an element is a stamp on the combined `Y` and knows nothing about files. That is
  unactionable on a 316-port network unless the map is on the same screen, so the CLI
  prints the block map as a header note. Threading a labeller through every construction
  site in `pkg_rlc_attrib` was the alternative and is the fix if this is ever revisited.
- **The naming heuristics get `ALIAS.name`, with NO local number** (`_attr_family_names`).
  `name_prefix` strips only a trailing run of digits, so the label printed elsewhere —
  `PKG.100 VSS_1` — is exactly the wrong input for it: the prefixes come out
  `PKG.100 VSS_`, `PKG.101 VSS_`, … one family per port, on the file where a family is the
  whole point. `PKG.VSS_1` gives `PKG.VSS_` for all 54 and keeps two files' identically
  named nets apart, which a bare `VSS_1` would not.

### The two-file GUI — schema, namespace, engine (round 3)

Round 2 made `pkg_rlc_compose` able to answer; round 3 is what lets the GUI ask.
`tests/test_multifile_session.py` (the schema), `tests/test_multifile_table.py`
(the window and the cell budget) and `tests/test_multifile_engine.py` (the
engine and the surfaces) are the guards, and every claim below was
mutation-checked.

- **A HOME FILE PLUS EXTRAS, never one list of files.** `TraceConfig.file_label`
  stays a single `str` and keeps its meaning — a bare port number is a port of
  THAT file, in every mode — and `file_labels` holds the others in order. That
  is what makes every pre-existing spec, every golden case and every saved
  session mean exactly what it meant, and what keeps a single-file user from
  ever seeing a tag. It is also the only layout that FITS: measured, a per-row
  file COLUMN takes the connections table from 405 px to **451** (two columns
  497, widening Port/To to 11 chars 461, a Name column 469) against a **431 px**
  viewport whose documented headroom is 13 px.
- **A file's TAG IS ITS POSITION** (`default_alias`: F1 is the home file, F2 the
  first extra), resolved by `trace_file_labels` here and by `slots_of` in
  `pkg_rlc_files_gui`. Two authorities for what `F2.3` means is the silent
  wrong answer this feature exists to end, so the two are pinned against each
  other and the files module DELEGATES to this one. Measured there: a port cell
  is `ttk.Combobox(width=7)` — **72 px / 7 characters at 100%, 135 px / 7
  characters at 150%**, and the character count is what is DPI-stable. `F2.` is
  33% / 34% of the text budget and leaves 4 digits; a 4-character tag is 73% /
  76% and leaves 1. `23,24,25` is 48 px and fits the 49 px budget; `F2.23,24,25`
  is 64 px, i.e. **131%**, and scrolls in a widget with no scrollbar. Hence
  `ALIAS_MAX_CHARS = 3` and a tag ONLY on an endpoint that crosses files.
- **THE HOME FILE IS BLOCK 0 AT OFFSET 0 WITH EVERY PORT KEPT, and everything
  rests on it.** That is what makes default scope FREE rather than a translation
  layer: measured on `coupled_2port_gndref.s2p + pi_2port.s2p`,
  `parse_scoped_ports('1', net, default='F1')` is `[1]` and `('2', …)` is `[2]`,
  while `'F2.1'` is `[3]`. It is also what makes the refusal free — a bare
  number PAST the home file's port count would otherwise address the next
  file's ports (`'5'` on a 4-port home is F2.1: a plausible number from a port
  nobody named), and `net.gport` raises there by name with the port map
  attached. Every port field therefore goes through `parse_scoped_ports`, tag
  or no tag; nothing is passed through untouched because it "looks bare".
- **A COMMA TOKEN MAY CARRY ITS OWN TAG, and the scope is STICKY — one rule on
  top of `parse_scoped_ports`, not a second parser.** The connection table
  forces it: a `short` row stores its whole tied group in ONE cell
  (`_join_short_group`, R1's single-cell short), so `2,F2.1` has no other
  spelling there. `parse_scoped_ports` refuses a tag on a later token, and is
  right to on ITS input — `F1.1,F2.3` would have to mean either "one field, two
  scopes" or "F1 scopes everything". `_scope_port_field` removes the ambiguity
  instead of re-deciding it: each comma token is resolved ON ITS OWN with the
  scope carried forward from the last tag seen, so `F2.1,2` is identical to what
  that function answers and `2,F2.1` becomes expressible. Every token still goes
  through the same parser, so every other rule stays its rule.
- **`_scope_dsl_text` rewrites FIELD POSITIONS, never every token that contains
  a dot.** `parts[0]` is always a port field and `parts[2]` is one after
  `short_to` / `lumped_between`; nothing else in the grammar is. A blanket scan
  survives `C=1.5p` by accident (`_split_tag` reads the head as `C=1`, which
  fails the alias pattern) but would silently re-point a signal group named
  `F1.something`. Node names are skipped through core's own `_collect_nets` —
  the ONE definition of which tokens in that text are names.
- **Mode 3's Short Pairs is the ONE port field that is not scoped, and it has an
  explicit check instead.** `parse_short_pairs` reads its tokens with `int()`,
  so a tag there already fails with core's message — but a bare index past the
  home file would have gone through as a global port. `_check_bare_ports` is
  that check; do not delete it in favour of "the resolver catches everything".
- **THERE ARE TWO NAMESPACE BUILDERS, and that is a measured decision.**
  `_trace_network` stacks the real thing (Calculate). `_namespace_network`
  builds a `ComposedNetwork` with the blocks and `Y = zeros((0, n, n))` — it
  answers "what does F2.3 mean" from the port counts alone and allocates
  nothing. The strips and the Ports & Roles refresh both run from
  `_apply_editor_strips`, i.e. once per KEYSTROKE, and `comp.compose` measured
  on this box with smooth synthetic data (three runs each) is **100 / 112 /
  97 ms** for 16 + 60 ports at 401 points, **10780 / 10346 / 10521 ms** for
  16 + 153, and **6772 / 6833 / 6661 ms** for 16 + 300 at 101 points. Ten
  seconds per character is a frozen application, and 153 ports is the SMALL end
  of what this tool is used on. The two must agree — a namespace that validated
  a spec the composition then addresses differently is the same drift
  `trace_file_labels` is kept mirrored against — and
  `TestTheTwoNamespaceBuildersAgree` is the guard.
- **The stack is CACHED on the App, keyed by the file labels and validated by
  FileEntry IDENTITY.** A label is re-used when a file is reloaded and the
  arrays behind it are then different objects, so a label-only key keeps serving
  the previous parse. The cache is what makes the edit/recompute loop usable at
  all (see the numbers above; `pkg_rlc_compose` measures the re-solve at 2.6 ms
  against 4486 ms for the full path on 316 ports).
- **`marker_hz` is deliberately NOT passed to `compose`.** It would refuse the
  whole composition when the marker falls outside the common span, and the GUI
  already answers that its own way — `snap_to_grid` reports the distance and
  flags `off_grid`. It would also key the cache on a value the user retypes
  constantly.
- **A composed trace's numbers live on the COMPOSED axis, `TraceConfig.net_freqs`.**
  None means "the home file's own sweep", which is what it is for every trace
  that predates this. The two are equal only when nothing was interpolated, so
  the plot, the CSV and the marker snap all read `_trace_plot_freqs`; drawing a
  composed `Z` against the home file's sweep puts the right values at the wrong
  frequencies and looks like a plausible curve. When a composed trace has
  numbers and no axis, `_trace_plot_freqs` returns **None and the curve is
  skipped** — falling back is the failure it exists to prevent.
- **The composed axis is filed in `run.freqs` under the file LEGEND, not under
  either file's label.** The marker landed on an axis neither file has, so a
  header line naming one of them beside that number is exactly the disagreement
  that list exists to remove.
- **The plot legend carries the COUNT (` +N`), not the file names.** R3-4 asks
  that a composed run say which files produced it everywhere it is read, but the
  legend budget is `MAX_LABEL_LEN = 30` characters HEAD-truncated and the tool's
  own default label already overflows it for a 20-character file name; the
  legend `F1=die.s6p + F2=package.s4p` is 30 characters on its own and would
  delete the trace name it qualifies. `_plot_trace_label` trims the BASE and
  keeps the marker — `freeze_label`'s rule and the same reason. The NAMES live
  in the results table's file column, the coupling block's `files:` line, the
  CSV header, the Ports & Roles header and the files window.
- **R3-5 arrives WHERE THE NUMBER IS READ, frozen onto the snapshot.** A weld
  raises nothing and makes no number look wrong (measured in `pkg_rlc_compose`:
  grounded / open / through 1 nH all give `L_eff` = 2.1454 nH, bit-identical,
  spread 0.000e+00), so it changes how the number must be READ and a report
  nobody opened is the wrong place for it. `RowSnapshot` / `CouplingSnapshot`
  carry `ref_strip` / `ref_warn` / `ref_lines`, resolved at snapshot time by
  `reference_provenance` — which renders the one-line strip and the full report
  ONCE so they cannot disagree — and `_run_report_segments` emits them under the
  table, in the Log AND on every run page. There is deliberately **NO second
  printer** at compute time: it put the same paragraph on screen twice, and two
  copies of one verdict are two things that can come to disagree.
- **An attribution of a composition decomposes against a baseline that has the
  cross-file links IN it, and there is no way to turn that off.** R2-8, arriving
  in the window. All-open on a composition leaves the files as disconnected
  islands: measured with the real engine on a 12-port combined network, the
  EM-vs-PKG off-diagonal block of `Ybase` is **0.000e+00**, every package-only
  element contributes **EXACTLY 0**, and the reconciliation residual reads
  **6.49e-15** — perfect health, wrong answer. `_attrib_network` is the ONE
  resolver (`Y`, `freqs`, `nports`, `term`, `baseline`) and it is what every
  call site in the window now goes through; `_attrib_role_rows` scopes the
  provenance rows the same way, or the From column names a row for port 3 of the
  die beside an element on port 3 of the package.
- **A GLOBAL PORT INDEX still reaches two messages, and both are display-only.**
  `Element.describe()` in the Attribution window renders "port 6 → gnd" for what
  the user typed as `F2.2`, and the editor's validation echo says
  `✓ port 6 → GND: 500 mΩ` for the same row (measured, on the two-file run in
  the screenshot). Both are the finding the CLI section already records: an
  element is a stamp on the combined `Y` and knows nothing about files, and
  `_validation_report` echoes the port field it was handed, which is the SCOPED
  one because scoping is what makes the check correct. The number, the port and
  the row are all right; only the spelling is global, and the Attribution
  window's From column and the table row itself both carry what was typed.
  Threading a labeller through `pkg_rlc_attrib`'s construction sites and through
  `_validation_report` is the fix and is not this round's — do not "fix" it by
  un-scoping either, which trades a wrong spelling for a wrong answer.
- **The window is on the MENUBAR and on BOTH right-click menus, never a fifth
  button.** The Files and Traces rows are each measured at 448 px with four
  buttons already asking 364, and a fifth row inside Global Controls comes
  straight out of an editor viewport that is down to 45 px at the 1040x600
  minsize. The Files right-click deliberately does NOT move the file selection:
  the window is about the selected TRACE, and re-selecting a file would change
  what the editor and Ports & Roles are describing as a side effect of a
  question about something else.
- **`SESSION_VERSION` is 2 unconditionally.** The conditional form (1 when
  uncomposed) was implemented and reverted: `tests/test_session.py` asserts both
  that a saved file's version IS `SESSION_VERSION` and that `SESSION_VERSION + 1`
  is refused, which together force the written default and the read cap to be
  one number. It is also the safe side — a v1 reader would drop `file_labels`
  with a note and then compute the home file alone, which is the wrong answer
  this feature exists to prevent.

### Auto-apply, the style picker, plot visibility

`tests/test_editor_autoapply.py` is the guard for this whole section, and every
claim below was mutation-checked — reverting the behaviour turns its test red.

- **The editor applies itself; there is no `Apply to Trace` button.** Three properties make
  that safe and none is optional. **(a) The sync is DEFERRED to `after_idle`, never run
  straight from the variable trace.** `PlaceholderEntry._show_if_empty()` sets the variable
  *before* it sets `_showing`, and Tcl runs write traces synchronously inside `.set()`, so a
  synchronous handler reads `get_value()` while the flag still says "not showing" and stores
  the grey hint (`"e.g.  1  (signal port to drive)"`) as a port spec. That is the reason for
  the deferral, not performance, and
  `TestAutoApply::test_a_synchronous_reader_really_would_see_the_placeholder` demonstrates
  the hazard rather than asserting it. **(b) It captures the `TraceConfig` OBJECT, not the
  Listbox index, and `_flush_editor_sync` runs before any selection change** (`_on_trace_selected`,
  `_on_duplicate_trace`, `_on_calculate`). Resolving the target when the callback runs lets
  "type into A, click B" write B's freshly loaded editor content into B and drop the edit —
  the exact loss auto-apply exists to remove, now rare instead of reliable. **(c) It never
  raises and never opens a dialog**, same rule and same reason as `_apply_editor_strips`.
  `_apply_editor_sync` uses `any(t is tc ...)`, not `tc in self.traces`: `TraceConfig` is an
  `eq=True` dataclass holding numpy arrays, so `in` raises "truth value of an array is
  ambiguous" as soon as it compares against a trace it does not match.
- **`_update_mode_visibility()` is called INSIDE the `_suppress_editor_sync` guard.** It
  calls `set_placeholder` on four `PlaceholderEntry`s, each of which writes its variable —
  four unguarded syncs per selection. They usually write the same value back, but
  `_sync_editor_to_trace` turns an empty Label into `trace_<id>`, so merely *looking* at a
  trace could rename it.
- **`_refresh_trace_list` returns early when the rendered lines are unchanged.** It now runs
  on every keystroke, and rebuilding a Listbox resets `yview` — a user editing trace 9 of 12
  would be yanked to the top on every character. (Programmatic `delete`/`insert`/
  `selection_set` do *not* fire `<<ListboxSelect>>` on Tk 8.6, verified, so the rebuild
  cannot re-enter `_on_trace_selected` and reload the editor mid-typing. Do not rely on that
  for anything else.)
- **`_config_signature` marks `stale`, `_draw_signature` triggers a replot.** Editing the
  spec makes the drawn curve older than the trace that describes it (a trailing `*` in the
  list); changing colour/linestyle/visibility changes the picture only. `label` is
  deliberately in NEITHER — it reaches the plot as a legend name, and including it would
  re-render every subplot on every keystroke of the Label field.
- **`_replot_from_cache` is the ONLY place a computed trace becomes plot curves.** Calculate
  fills `Z`/`Zmat`/`fit_freqs`/`fit_Z` and then calls it, so Calculate and a visibility
  toggle cannot drift apart in what they draw. It reads colour, linestyle and self/mutual
  fresh every time. Anything the plot needs must be cached on the `TraceConfig` — a value
  only `_on_calculate` knows would silently vanish from the traces that stay.
- **EVERY path that removes a trace must call it — including `_on_remove_file`.**
  `_on_remove_trace` always did; `_on_remove_file` dropped the file's traces from
  `self.traces` and from the Traces listbox and then did not replot, so until the next
  Calculate the plot kept drawing and LEGENDING curves whose trace and whose file were
  both gone. Measured: two files each with a trace, Calculate, Remove the second file →
  `app.traces` and the listbox held one entry while `app.plot.view.traces` still held
  two. The readout box IS the legend, so the stale name sat in the cursor readout too,
  and the plot disagreeing with the Traces list about which measurements exist is the
  same disagreement the run pages' banner exists to prevent. `_replot_from_cache`
  already skips a trace whose `_file_by_label` returns `None`, so the call was the
  whole of what was missing.
- **Toggling visibility must NOT recompute and must NOT drop the cursors.** Hiding a curve
  through `_on_calculate` costs a Schur reduction of a 153-port file to produce numerically
  identical results, and `set_traces` clears `_anno_stack` *and* `_vline_freqs` (twice —
  `redraw` clears them again), so every tidy-up of the view would destroy the V lines the
  comparison was set up to make. `set_traces(..., keep_cursors=True)` re-places them via
  `_place_vline`; M markers are deliberately NOT restored, each being anchored to one data
  point of one trace that may no longer be drawn. A **full** Calculate still clears them —
  its numbers are new.
- **`enabled` gates EVERY output: the plot, the results table and Export CSV.** The checkbox
  selects what the session is about. A hidden trace leaves the results table because the
  table is read as "what is on the plot", and a row for an undrawn curve reads as a
  *duplicate* of the drawn one — which is exactly how a hidden trace normally arises
  (Duplicate, then hide the copy: two near-identical rows). It leaves the CSV for the same
  reason, one step further from where the user could notice it; there is no `Plotted: no`
  marker any more, because every trace in the file was on the plot. Nothing is destroyed: it
  is still **computed** and cached, so showing it again costs no Calculate, and one line
  under the table names it (`hidden (measured, not plotted, not exported; …)`) — that line is
  now the ONLY place the report accounts for it, so do not drop it. **The filtering is in
  `_render_results`, not at collection time** — `run.rows` holds every trace so a units-mode
  re-render follows the visibility as it stands then (via `RunSnapshot.with_visibility`, see
  below), and `Calculate This Trace` still narrows the work rather than the report. A
  `FitSnapshot` therefore carries its own `enabled`: a fit summary under a table with no such
  row is an orphan. Two empty
  cases must stay distinguishable: with everything hidden the "plot is empty on purpose" note
  must not claim the numbers are above it, and `_on_export_csv` must say "every calculated
  trace is hidden" rather than "Run Calculate first" — the latter is wrong and unactionable
  when the numbers exist. The editor owns the *selected* trace, so poking `tc.enabled`
  directly on it is overwritten by the next sync — go through `_on_toggle_trace` or
  `ed_enabled_var`.
- **The style picker stores INDICES and expands IN PLACE.** Indices keep `pkg_rlc_plot`,
  `test_plot_readout` and `golden_legacy.npz` out of this change, and keep
  `_coupling_plot_traces` able to derive sibling colours as `(color_idx + n) % len(COLORS)`
  — an arbitrary RGB has no "next colour", so all six curves of a mode-6 trace would come
  out identical. In place, not a popup: a `grab_set` Toplevel that outlives its opener
  blocks event delivery and `update()` never returns, which is the documented scrollbar
  limit-cycle failure again (GUI and test suite hang together). All sizes are in units of
  the default font's linespace, never pixels.
- **`_preview` sets `takefocus=True`.** A bare `tk.Canvas` has `takefocus=''` and Tk's
  traversal heuristic skips a widget with no key bindings, so replacing two Spinboxes with a
  Canvas would have dropped Style out of the Tab order the Spinboxes were in.
- **`_editor_curve_span` counts mode-6 rows DIRECTLY, not through the DSL.**
  `build_terminations_rows` goes via `rows_to_dsl_text`, where `b` is the legacy alias for
  the minus side of `A`, so two measurement ports named `a`/`b` resolve to ONE and the
  preview would claim a span of 1 for a trace Calculate refuses outright.
- **`Show/Hide` is the FOURTH button in the Traces row.** Measured at the 1040x600 minsize:
  the row is 448 px, three buttons ask 273 and four ask 364. Re-measure before a fifth. It
  duplicates the editor's `Plot: this trace` checkbox on purpose — the checkbox needs the
  trace selected first and the `<space>` route is invisible.
- **The `☑`/`☐` prefix is width-stable.** Measured in Microsoft YaHei UI 9: both glyphs are
  12 px, 16 px with the trailing space, so toggling does not shift the rest of the line
  (`✓` vs a space would have jittered by 8 px). Cost against the 444 px list: a typical
  entry goes 356 → 372 px. `Listbox.itemconfig` does not survive `delete()`, so the grey
  foreground for a hidden trace is re-applied inside `_refresh_trace_list`, not at the
  toggle.
- **A trace list row wears its CURVE's colour, and the early-return cache key carries the
  colour index.** `COLORS[tc.color_idx % len(COLORS)]` when the trace is enabled, the same
  `#909090` grey when it is not (grey is the state; a hidden trace has no curve to be tied
  to). Both are re-applied inside `_refresh_trace_list` for the `itemconfig`-does-not-survive-
  `delete()` reason above. **`_trace_list_shown` is now `[(line, color_idx)]`, not `[line]`** —
  `info_str()` renders no colour, so a style change alone left the rendered lines byte-identical,
  the "unchanged → return early" optimisation fired, and the list kept the old foreground while
  the plot was already redrawn in the new one.
- **The results table's rows are headed by a width-stable colour swatch**, a Text tag
  (`c0`..`c11`, the `"flag"` tag's precedent) — **not** a `ttk.Treeview`, which was reviewed
  and rejected: it destroys the `aligned` units mode (one SI prefix per column, right-aligned,
  which is what makes corner-to-corner comparison possible), loses select-drag-copy into a
  mail, and freezes its row height at 20 px so text clips at 150% DPI. `RESULTS_SWATCH` is
  `█`, measured in the pane's own font (Consolas 9) at 7 px — exactly one monospace cell, the
  same as ` `, `0`, `M` and `X`, so the header's `_SWATCH_PAD` lines up with the rows under it.
  Rejected on the same measurement: `▇` (12 px) and `▰` (10 px). `_format_results_table` stays
  a pure text function; `_append_swatched` finds the rows by their `RESULTS_SWATCH` prefix and
  consumes the colour list in order, so no line-number arithmetic has to track however many
  header lines the table emits. Nothing else in the table may start with that character.
- **The editor footer carries a ONE-LINE summary of the two below-the-fold strips, and one
  line is the whole budget.** Measured at the 1040x600 minsize: the mode-5 form is 516 px
  against a 45 px viewport, so `ed_overview` sits 366 px below the fold and `ed_validation`
  387 px below it — 7.8% of the form is on screen and every mode change resets the scroll to
  the top. Moving them into the footer verbatim is **not** the fix: the footer's height is the
  button's 33 px and a label packed after it shares that row, so line 1 costs +0 px, line 2
  +9, line 3 +26 and line 4 +43 — and at +43 the editor canvas reports `winfo_ismapped() == 0`
  in modes 1/2/3/6, i.e. the whole form disappears. `VALIDATION_STRIP_LINES = 2` already
  renders up to 3 display lines, so that is exactly what "just move them down" measures.
  Hence `_footer_strip_text`: one line, `wraplength=0` so it **clips** (wrapping costs 26 px,
  not 9), `FOOTER_STRIP_CHARS = 52` against a measured 303 px slot, the verdict never
  truncated and the port counts giving up characters first. It counts the messages that do
  **not** start with `✓` — `_validation_messages` never returns an empty list, so `len(msgs)`
  reports a clean two-element spec as "2 problems". It is packed **after** the button (pack
  unmaps from the end: the button must never be the one that goes) and `pack_forget`ed outside
  mode 5, where the connections table is hidden but its rows still exist.
- **`Calculate This Trace` narrows the WORK, not the report.** Traces it skips still
  contribute their last numbers to the results table; a table that shrank to one row would
  make the fast path look like it had discarded the others. It keeps the cursors (it is the
  iteration loop) where the all-traces path does not.

### Freeze as trace (the before/after comparison)

`tests/test_freeze_trace.py` is the guard, and every claim below was
mutation-checked.

- **A frozen trace is INERT, and two separate refusals make it so.** `_on_calculate`
  skips it (before the `only is not None and tc is not only` branch, whose shape it
  mirrors) and `_sync_editor_to_trace` returns immediately. The editor guard lives in
  `_sync_editor_to_trace`, **not** at its four call sites (the deferred sync, the
  flush, and both of Calculate's) — the one that forgot would relabel or re-port a
  snapshot with whatever the editor happened to be showing. `_set_editor_editable`
  greying the fields is belt and braces, not the mechanism: it stops the user typing
  into a field that discards every keystroke in silence, which is the exact failure
  auto-apply exists to remove.
- **Calculate skips the WORK, not the REPORT.** A frozen trace still contributes its
  cached `rlc` / `coupling` to the results table, same rule as `Calculate This Trace`
  — a snapshot missing from the table it exists to be compared against is worse than
  useless. `Calculate This Trace` aimed AT a frozen trace says so by name rather than
  silently doing nothing.
- **A STALE trace may not be frozen, and `freeze_refusal` is the one place that
  says so.** A snapshot's whole contract is "this spec produced these numbers",
  and a frozen trace can never clear `stale` again — Calculate skips it and
  `_sync_editor_to_trace` refuses it — so a stale trace frozen once is
  mislabelled FOREVER, with the trailing `*` that would have warned about it
  deliberately deleted. Measured on `coupled_2port_gndref.s2p` (port 1 =
  0.6 Ω / 2 nH, port 2 = 0.9 Ω / 3 nH): Calculate with Port A = 1, type `2`
  into Port A, freeze without recalculating, and the results table read
  `█ [ 2] coil <21:36>  M1: S:[2] G:[]  600 mΩ  2 nH  −1.27 nF  2.09  ind` —
  port 2's descriptor over port 1's numbers, a 50% error on L, and the same
  wrong pairing in the run page, the CSV and the plot legend. Nothing raises
  and the numbers are real. `_on_freeze_trace` flushes the editor FIRST, which
  is what makes the check answer about the spec on screen rather than the one
  from an event ago. Carrying `stale` onto the snapshot instead was rejected:
  the flag means "the drawn curve is older than the spec", and on a trace that
  can never be recomputed that is a permanent complaint with no action behind
  it. The Freeze menu entry stays LIVE on a stale trace so the refusal can
  explain itself — a greyed entry would be the same bug report.
- **The `<HH:MM>` stamp goes through `freeze_label`, which trims the BASE.**
  `pkg_rlc_plot` truncates a legend entry to the FIRST `MAX_LABEL_LEN = 30`
  characters, and the tool's own default label is `f"{fe.label}_p1_to_gnd"`, so
  any file name of 20 characters already overflows. Appending the stamp put the
  one thing that tells a snapshot from its source exactly where head-truncation
  deletes it: measured, source `coupled_2port_gndref.s2p_p1_to_gnd` and
  snapshot `coupled_2port_gndref.s2p_p1_to_gnd <21:29>` both legend as
  `coupled_2port_gndref.s2p_p1_to` — byte-identical entries for the two curves
  the feature exists to put side by side, in `_draw_plain_legend`, which is what
  is on screen right after Calculate. Same rule and same `…` elision as
  `_compose_curve_label`: trim the base, keep the discriminator. (The cursor
  readout was never affected — `_fit_names` keeps the tail — which is why the
  suite did not catch it.)
- **Export CSV does NOT head a frozen trace with the current run.** Every other
  trace's block carries `# Run: #N @ f, HH:MM:SS` because export writes the
  cached state, which is the newest run. A snapshot's numbers came from an
  EARLIER run and cannot be recomputed, so the provenance line said the
  opposite of the truth for exactly the trace type that exists to be a
  baseline — and a before/after CSV, the only reason two such traces are in one
  file, labelled both as the same run. It writes
  `# Run: frozen snapshot taken at HH:MM, numbers from an earlier run`
  instead, reading the stamp back off the label with `_freeze_stamp_of` (a
  user-renamed snapshot degrades to `(unknown)` rather than raising).
- **`_freeze_trace_config` copies config and REFERENCES results.** `mports` /
  `conn_rows` element-wise (the documented Duplicate aliasing bug), but `Z` / `Zmat` /
  `rlc` / `fit` / `fit_freqs` / `fit_Z` are the same objects: `_on_calculate` ASSIGNS
  new arrays on every run instead of writing into the old ones, so nothing can move
  the snapshot's numbers, and a deepcopy would carry megabytes (a 6x6 `Zmat` over 5000
  frequencies is 2.88 MB). If a future Calculate ever writes in place, that assumption
  breaks and this is where it breaks.
- **Colour AND linestyle both advance (`+1`, modulo).** A snapshot drawn in its
  source's exact colour and dash is indistinguishable from it, which defeats the one
  picture the feature exists to produce; the linestyle carries the distinction where
  the palette wraps onto a colour already in use.
- **`_duplicate_trace_config` must clear `frozen`.** Duplicate drops the results, and
  a frozen trace with no numbers is one Calculate will never fill in — a dead row.
- **A frozen trace comes back from a session file WITHOUT its numbers, and says so
  twice.** `frozen` is a config field (classified by `_config_trace_fields`, coerced
  through `_TRACE_BOOL_FIELDS`), the results are not and cannot be (numpy arrays are
  not JSON, and `test_computed_results_are_not_written` pins it). Dropping frozen
  traces on save was the alternative and was rejected: the SPEC is still worth having,
  because unfreeze + Calculate reproduces the snapshot exactly whenever the file has
  not changed, which is the normal case (what is compared is usually two port configs
  of one file). What is not acceptable either way is doing it quietly — `_apply_session`
  names them in the Results pane and `info_str` renders `❄ no numbers` in the Traces
  list.
- **Freeze / Unfreeze are on a RIGHT-CLICK MENU, not a fifth button.** The Traces row
  is measured at 448 px with four buttons asking 364, and a fifth row in Global
  Controls comes straight out of an editor viewport already down to 45 px. The
  right-click SELECTS the row under the pointer first — a menu acting on the previous
  selection is how you freeze the wrong trace — and only the applicable entry is live.
  A test that clicks it needs a MAPPED window: `Listbox.nearest()` reads pixel
  geometry and on a withdrawn root every y answers row 0, which is precisely the
  wrong answer being ruled out.
- **The "frozen" note is row 0 of the editor FORM, never the footer.** The footer's
  whole spare budget is one line and mode 5 already spends it (`_footer_strip_text`);
  the form is in a Canvas that every mode change scrolls back to the top, so row 0 is
  the one place in the editor that is always the first thing on screen.
- **`RowTable.set_editable` / `StylePicker.set_editable` use ttk STATE FLAGS
  (`state(["disabled"])` / `state(["!disabled"])`), not `configure(state=…)`.** Three
  of the connections table's six columns are readonly combos, and reconstructing the
  original state string by hand is what loses the readonly. `StylePicker` needs a
  plain flag instead: its palette is twelve bare `tk.Canvas` cells with `<Button-1>`
  bindings, ttk state does not cascade to children, and guarding `_choose` / `toggle`
  is the only thing that actually stops a click. `RowTable.add_row` re-applies the
  current flag, because `set_rows` runs before the editor knows the trace is frozen.

### The run snapshot (what a finished Calculate leaves behind)

`tests/test_run_snapshot.py` is the guard, and every claim below was
mutation-checked.

- **The render collections hold SNAPSHOTS, never the live `TraceConfig`.**
  `_on_calculate` writes its results onto the live trace objects, and the
  collections used to be `(tc, file_label, res)` — so anything that kept a run
  and re-rendered it later printed the NEXT run's id, label and port descriptor
  beside THIS run's numbers. Nothing raises and the numbers are real, which is
  what makes it the worst kind of bug. `RowSnapshot` / `CouplingSnapshot` /
  `FitSnapshot` resolve the blast radius — **`id`, `label`,
  `port_descriptor()`, `enabled`, and `color_idx`** (what the swatch is tagged
  from) — at snapshot time. Everything else was already immutable: `res` and
  `cres` are FRESH objects per run, `file_label` is a `str`, the fit summaries
  are already strings, and `_format_coupling_block` takes its matrix from
  `cres.Z_matrix`, never from `tc.Zmat`.
- **`port_desc` is a resolved STRING.** `port_descriptor()` is a method that
  recomputes from the live spec fields, so storing the callable — or the trace
  it is bound to — reopens the hazard in a form that is harder to see.
- **A snapshot NEVER retains `Z` / `Zmat` / `fit_freqs` / `fit_Z` / `aux`.**
  Measured envelope at 10 runs x 6 traces: text plus rows is ~0.43 MB, while
  the arrays are **173 MB** for a mode-6 run at 5000 frequencies and 6
  measurement ports, and **691 MB** at 20000. The one array a snapshot reaches
  is `cres.Z_matrix`, the G x G matrix at the marker frequency, which is what
  the block prints — and `extract_coupling_at_freq` **copies** it
  (`np.array(Zmat[idx])`), so it is not a view keeping the whole base array
  alive. The test states the property as "the reachable ndarray size is the
  same at 200 frequencies as at 2000", which is the thing that actually
  matters and which a fixed element cap would not catch.
- **A run is identified by a monotonic counter, never by value.** Two runs of
  an unchanged spec are equal in every field and are still two different runs:
  no sets of runs, no value-keyed dicts. `App._run_counter` is bumped once per
  `_on_calculate`; **freezing a trace joins the current run** (it measures
  nothing) rather than starting one.
- **An old run's `enabled` filter is FROZEN.** A run record is a record of what
  was measured, so hiding a trace tomorrow must not retroactively rewrite it;
  `_replot_from_cache` stays the owner of "what is on the plot now".
  `RunSnapshot.with_visibility(traces)` is the ONE deliberate exception and it
  is only ever applied to the **current** run, by `_on_units_mode_changed`:
  `enabled` gates the results table as well as the plot, so a re-rendered row
  for a curve that is no longer drawn would read as a duplicate of one that is.
  It matches by **trace id**, refreshes nothing but the flag, and leaves a
  record whose trace is gone with the flag it was taken with. A test that
  renumbers the trace while checking "nothing else changed" passes with no
  guard at all — nothing matches — so that precondition is asserted.
- **`tests/fixtures/render_reference.json` is the proof that the page did not
  move.** It was captured from the renderers as they stood BEFORE the snapshot
  types existed, and `tests/_render_capture.py` is the single place that knows
  the current signature (the same split as `_golden_capture.py` /
  `test_golden_regression.py`). If it fails, the rendered report changed — fix
  the change, do not regenerate the reference.

- **Plot quantities that need more than one curve arrive via `Trace.aux`.** `k` needs three curves at once (`Z_ab`, `Z_aa`, `Z_bb`) and so cannot be derived from a single `(freqs, Z)` pair; the GUI precomputes it and attaches it. `trace_y_values` must return an all-NaN array (draw nothing) for a trace with no matching `aux` entry, never raise — self curves share the subplot grid with mutual ones. New derived quantities go in `AUX_PLOT_TYPES` the same way.

### The session file (Save Config / Load Config / autosave)

`tests/test_session.py` is the guard, and every claim below was mutation-checked.

- **A session file holds the CONFIG, never the results.** `_COMPUTED_TRACE_FIELDS`
  is the blacklist and the saved set is *everything else*, so a new config field
  round-trips without anyone remembering it. That trade is deliberate: a forgotten
  config field silently stops saving and nothing catches it, while a forgotten
  computed field fails loudly (`json.dump` on a numpy array).
  `TestFieldCoverage::test_every_traceconfig_field_is_classified` pins that every
  field of `TraceConfig` is in exactly one of the two sets.
- **Retired fields are written only when non-empty.** A trace the user has never
  selected still carries `custom_text` / `mp1_*` unmigrated, so dropping them
  would lose a spec — but emitting eight empty strings per trace buries the ones
  that matter. Migration happens on load, through the existing `_migrate_trace`.
- **Every file is recorded twice and the RELATIVE path wins.** That is what makes
  a session survive the folder being copied to another machine, which is the
  normal way work reaches the red zone; the absolute path is the fallback for a
  config file moved on its own. A test where only the relative path exists does
  NOT pin the precedence — reversing the candidate order still passes it —
  which is why `test_the_relative_path_wins_when_BOTH_exist` exists.
- **`rel_path` is written only when it is shorter than the absolute path.** A
  config saved somewhere unrelated to the data produces a ten-deep `../../..`
  chain that describes no copyable tree, resolves on this machine and nowhere
  else, and is pure noise in the file. `data/coil.s4p` and `../data/coil.s4p`
  both survive the rule, which are the layouts the relative path exists for.
- **A missing file is reported, not fatal.** The traces bound to it stay in the
  list; `_on_calculate` already says `file '…' not loaded`. `_apply_session` also
  re-binds traces when a resolved file's basename differs from the stored label,
  which is the only route a hand-edited config has to re-point at moved data.
  The `found` flag is checked BEFORE `_load_one_file`, which reports through a
  **modal** dialog — a session whose folder moved would otherwise open one per
  file (measured: the test does not fail, it hangs) before the user could read
  the single Results line that says the same thing.
- **`WM_DELETE_WINDOW` must point at `_on_close`, and the test checks the
  handler NAME.** With nothing registered Tk reports its own built-in
  `"…destroy"`, which is truthy — `assertTrue` on it passes in exactly the
  broken state, where closing the window skips the autosave entirely.
- **`_session_dict` flushes the editor first**, same rule and same reason as
  Calculate: `Ctrl+S` in the same event burst as a keystroke would otherwise save
  the value from before it.
- **Loading CANCELS the queued editor sync rather than flushing it.** The target
  trace is about to be discarded. `_cancel_editor_sync` is for that case only —
  everywhere else the queued edit is the user's last keystroke and must land.
- **A bad value costs its own field, never the file.** A session file is readable
  text, so it will be hand-edited. Unknown keys, unparseable ints and malformed
  rows are dropped with a note in the Results pane. `_coerce_bool` is not
  `bool()`: `bool("false")` is `True`, which would silently invert a checkbox.
  A combobox value outside its list is refused because both are `state="readonly"`
  and there would be no way back through the UI.
- **`SessionError` carries the whole verdict in `str(e)`**, the
  `TouchstoneParseError` contract: not-ours, no version, and version-from-the-
  future are three different messages, and the future one names both numbers.
- **The autosave never raises and never writes an empty session.** It runs inside
  `WM_DELETE_WINDOW`, where a raise is an application that cannot be closed; and
  opening the tool, changing nothing and closing it must not erase what the
  previous run left. Startup only *names* what is on disk — loading it would
  re-parse every Touchstone file in it before the user has asked for anything.
- **Save/Load are on a MENU BAR, not a button.** The Files and Traces rows are
  both four buttons deep against a measured 448 px, and a fifth row in Global
  Controls comes straight out of the editor viewport, which at the 1040x600
  minsize is already down to tens of pixels. `unbind_class("Text", "<Control-o>")`
  goes with the accelerators: Tk's Text binds it to "insert a newline" and a
  `bind_all` handler runs *after* the class binding, so Ctrl+O would open the
  dialog and scribble in the Results pane behind it.
- **A `ttk.Notebook` CLIPS a tab strip it cannot fit** — no wrap, no scroll, and
  the tab that vanishes is the LAST one. Measured (Microsoft YaHei UI 9): the
  Help window's nine tabs needed 891 px and the tenth took it to 968, past the
  historical 950. `HELP_WINDOW_WIDTH` is now 1010, i.e. **42 px of headroom, not
  enough for an eleventh tab**; `TestHelpTabsAllFit` re-measures it.

### The Results pane notebook (the Log tab and its badge)

`tests/test_results_notebook.py` is the guard, and every claim below was
mutation-checked.

- **`results_text` is a REAL, PERSISTENT widget attribute — never a property
  resolving to whichever tab is active.** Six tests take `index(END)`, run
  Calculate and read back from that mark; a fresh widget answers a stale mark
  with `""`, so a property turns all six into empty-string assertions that read
  like formatting bugs. `_append_result` still writes to it and nothing else.
- **The Log is tab 0 and it is SELECTED at startup; there is no run tab.**
  `focus_set` and `event_generate` are **no-ops on an unmapped widget**, and a
  non-selected tab's widget is unmapped — so
  `test_session.py::test_control_o_does_not_also_scribble_in_the_results_pane`,
  which focuses `results_text` and synthesises Ctrl+O, proves nothing at all if
  the Log is not on screen. Pre-creating an empty run tab is the "tidy up" that
  moves the failure to a test whose name points at the menubar.
- **No `<<NotebookTabChanged>>` → `canvas.focus_set()` handler on this
  notebook.** Measured: `nb.select()` does not steal focus, and that is exactly
  what makes an automatic switch safe. Wiring one here would invent a focus
  steal on every Calculate that does not exist today. (That handler belongs to a
  PLOT notebook, which this is not.)
- **A hidden tab still accepts `insert` / `get` / `see`, so warnings must
  announce themselves.** `_append_result` takes a `severity` defaulting to
  `LOG_INFO` (byte-for-byte the old behaviour). `LOG_WARN` increments
  `_log_unseen` **only while the Log is not the selected tab** — a warning read
  as it is written is not unseen — and the count is cleared by *looking* at the
  Log, i.e. from the tab-changed handler, not by any other action. `LOG_ERROR`
  does not badge at all: it pulls the Log to the front instead.
- **The badge is WIDTH-STABLE, and that is why the count is always shown,
  zero-padded to two digits.** The Log is the leftmost tab, so a label that
  changes width reflows every tab to its right. Measured in TkDefaultFont
  (Microsoft YaHei UI 9, what the vista theme's `TNotebook.Tab` uses): `' '` and
  `'!'` are both 4 px and every digit is 7 px, so `"Log  00"`, `"Log !03"` and
  `"Log !99"` all measure **44 px**. A digit-free `"Log"` is 22 px and **cannot**
  be padded to match with spaces — `22 + 4a == 37 + 4b` has no integer solution
  — which is the whole reason the zero is on screen. `LOG_BADGE_CAP = 99` is not
  cosmetic either: a third digit is a third width.
- **An ERROR claims the pane, and `_select_results_tab` is how a later automatic
  switch respects that.** `_log_forced` is set *after* `select()` so the
  `<<NotebookTabChanged>>` that `select()` generates cannot clear the flag it
  just set (delivery order is not something to depend on). The claim is released
  by the user moving off the Log, and by the start of the next Calculate — not
  by anything in between, or every later run would stay pinned to the Log.
- **Severity routing follows what the line MEANS, not where it is printed.**
  Parser `WARN:` lines, Schur/pinv warnings, `file … not loaded`, legacy-config
  migrations, session load notes and a failed fit are `LOG_WARN`; `ERROR` lines,
  their tracebacks and the two "this IS an error in the port setup" annotations
  are `LOG_ERROR`; the results table, the descriptive half of a file summary,
  Check File and the **rank-deficiency** annotation are `LOG_INFO` —
  that last one exists to say the warning above it is not a fault, so badging it
  again would contradict it. The mode-5 "spec notes" block counts the messages
  that do **not** start with `✓`, the same rule `_footer_strip_text` counts by.
- **The tab strip costs 28 px of PLOT height and nothing horizontally.**
  Measured at the 1040x600 minsize: the right paned's sash goes 167 → 195 and
  the plot pane 428 → 400 (`results_nb.winfo_reqheight()` = 172, one visual
  row); it is the strip's height and is constant in the tab count. Horizontally
  there is no cost at any tab count — `outer.sashpos(0)`, the left panel's width
  and `_ed_canvas.winfo_width()` read **460 / 460 / 431** in mode 5 with 1 tab
  and with 30 fat tabs. Two existing properties are what make that structural:
  `left` is `ttk.Frame(outer, width=460)` with `pack_propagate(False)` and
  `weight=0`, and the results pane is populated **before** `add()`. Break either
  and the guard goes red.
- **Tab labels are for LEGIBILITY, not layout.** In the 575 px pane at the
  minsize a tab strip clips from 13 tabs (100%) / 9 tabs (150%); at 30 tabs a
  tab is 22 px, about three characters. That is why the run tab's label is
  `#7 10:42` and not a timestamp — a timestamped label is what drove a 50-tab
  strip to a 8808 px requested width in the measurements — and why the caps
  are what they are. See "Run history" below.

### Run history (the run tabs after the Log)

`tests/test_run_history.py` is the guard, and every claim below was
mutation-checked.

- **TWO DISJOINT SETS, and that is what makes the all-locked deadlock
  UNREACHABLE BY CONSTRUCTION rather than handled.** The **auto ring**
  (`_auto_run_tabs`, default `RUN_AUTO_DEFAULT = 3`) is the only set Calculate
  ever touches: never kept, evicted oldest-first, silently. The **kept set** is
  entered only by the user pressing Keep, is hard-capped, and is never evicted
  by anything automatic. **Therefore Calculate can never block, never prompt,
  and never destroy something the user asked to keep.** That sentence is the
  invariant; a change that lets Calculate consider a kept tab, or lets the kept
  cap be checked anywhere but at the moment of Keep, breaks it.
- **The kept cap bites AT KEEP TIME, and by then the button already says why.**
  `_keep_run_tab` refuses as a backstop. A disabled button with no reason on it
  is a bug report — but a reason that does not FIT is not a reason either.
  `keep_button_label(..., "full")` renders `Keep (5/5) — full` on the button
  and, with `long=True`, `Keep (5/5) — close a kept run first` on the tab
  strip's right-click entry, which is not width-bound and is where the button
  sends the user anyway. Measured with `TkDefaultFont` scaled 1.5x (the
  supported 150% DPI) at the 1040x600 minsize: the Results header is 575 px,
  requests 687 with the long label, and the Keep button — packed LAST of five
  `side=LEFT` — got the 213 px that were left and the sentence was clipped
  mid-phrase with `winfo_ismapped()` still 1, so no ismapped assertion could
  see it. The guard is
  `test_the_keep_button_is_READABLE_at_150_percent_font_scaling`, which asserts
  `winfo_width() >= winfo_reqwidth()`. `_kept_cap() = _run_tabs_max - _run_auto_max`
  and `_on_run_caps_changed` clamps the auto ring to `_run_tabs_max - 1`, so
  the kept cap can never reach zero and leave the button permanently dead with
  nothing to close.
- **Eviction is `nb.forget(widget)` THEN `widget.destroy()`, in that order, in
  ONE function (`_destroy_run_tab`).** Measured: `forget()` alone does **not**
  destroy the child — 300 runs at a limit of 10 left 290 orphan widgets and
  +21.5 MB, growing linearly. The guard is
  `len(nb.winfo_children()) == len(nb.tabs())` after a churn loop; **never
  assert on RSS**, the working set does not drop even on correct teardown.
- **Tabs are tracked BY WIDGET, never by index.** Measured: evicting a lower
  index renumbers the tabs after it but keeps the same widget selected and
  preserves its scroll position, so a stored index silently starts pointing at
  the neighbour. `RunTab` holds the frame; every lookup compares `str(frame)`.
- **The SELECTED tab is implicitly protected from eviction, like a kept one —
  and so is the page for the CURRENT run.** Evicting what the user is reading
  raises no error at all (Tk silently selects a neighbour), which is worse than
  an error. The second guard is not redundant: at an auto ring of 1 with the
  reader parked on the older page, the oldest-first scan skips the page they
  are on and takes the run that just finished. The ring is therefore allowed to
  sit **one** over its size while a page is protected; the loop still evicts
  the next-oldest, so it stays bounded.
- **"Newest" means TWO different things and both are needed.** For the *banner*
  it is `_current_run_number()` = `_last_run.number`, what the plot and Export
  CSV are showing — closing the newest page does not un-plot its curves, so a
  banner derived from the surviving tabs would quietly promote an older page to
  "current" and stop warning about exactly the disagreement it exists for. For
  the *auto-switch* it is the youngest page ON SCREEN, because
  `_reader_is_at_the_newest_run` runs from `_add_run_tab`, by which point
  `_last_run` is already the run being added — comparing against it answers
  "am I at the newest?" with a flat no and the switch never happens again after
  the first run.
- **The auto-switch is CONDITIONAL: only if the reader was already on the
  newest run, or on the Log — and a KEPT page never counts, even when it IS
  the newest.** Calculate is pressed constantly in the edit/compute/read loop,
  and yanking a reader off a page they deliberately kept is the opposite of
  what keeping means. The kept half is not redundant with "am I at the newest":
  the natural gesture is to press Keep on the page you are looking at, which is
  by definition the newest, so without `if rt.kept: return False` the very next
  Calculate moved them off it. Measured: Calculate → land on `#2` → Keep →
  Calculate → selected `#3`. The decision is taken **before**
  the new page exists, or "am I at the newest?" answers itself. When the switch
  does not happen the page is marked unseen instead, so nothing arrives
  silently. An ERROR still wins with no extra rule: it claimed the pane before
  the page existed and `_select_results_tab` declines to move off it.
- **No focus handler here.** `nb.select()` does not steal focus and
  `<<NotebookTabChanged>>` does not fire on re-selecting the current tab — both
  measured, both load-bearing for the switch being safe.
- **The unseen marker and the kept marker are WIDTH-STABLE GLYPH PAIRS: one of
  each pair is emitted ALWAYS, never a conditional glyph.** A run tab that
  changes width reflows every tab on a compressing strip. Measured in the tab
  strip's own font (TkDefaultFont = Microsoft YaHei UI 9): `'!'` and `' '` are
  both **4 px**; `'☑'` and `'☐'` are both **12 px**; `'🔒'` / `'🔓'` are both
  16 px but emoji-font bound. The brief's leading `'*'` is **5 px against a
  4 px space**, and **no** blank glyph in this font measures 5 px (checked
  U+0020, 00A0, 2002, 2003, 2005–200A, 2007, 2008, 205F, 3000 — 2, 3, 4, 6, 8
  and 12 px), so `'*'` cannot be made width-stable here and `'!'` already means
  "unread" on the Log tab of this very notebook.
- **Caps are set by LEGIBILITY, not by layout.** The vista notebook compresses
  tabs and never wraps (`results_nb.winfo_reqheight()` is 172 px at 1 tab and
  at 32), so a long strip cannot steal plot height, and it cannot reach the
  outer sash either. What binds is that a tab is ~47 px up to 12 tabs and 22 px
  at 30 (about three characters), and at 150% DPI clipping starts at 9. Hence
  `RUN_TABS_DEFAULT = 8` and `RUN_TABS_HARD_CAP = 12`.
- **`Runs ▾` is not a convenience.** Tk 8.6's `ttk.Notebook` has no tab-strip
  scrolling and no overflow chevron, so a menu carrying each run's FULL
  description is the only way a compressed tab stays identifiable. It is also
  where the two caps live — the header has no room for two more spinboxes.
- **Line 2 is WHAT CHANGED, and it is the real discriminator.** Time is not
  one: nobody remembers what they were doing at 14:32 and twenty runs are all
  at 5 GHz. `trace_signature_fields` is a NAMED `_config_signature` and must
  stay one-for-one with it —
  `TestSignatureFieldsCoverConfigSignature` mutates every field
  `_config_signature` watches and demands the named version notices too, so a
  tenth field added there cannot silently make a run page claim nothing
  changed. The diff is computed at Calculate time, while both sides exist, and
  stored **rendered** on the record.
- **Line 3 (`! the plot and Export CSV show run #N, not this page`) is
  MANDATORY on every page but the newest.** Without it three surfaces on one
  screen disagree with nothing to explain it: the tab shows run #3, the plot
  200 px below shows run #7, and Export CSV pressed while reading it writes
  run #7. It is why `_render_all_run_tabs` rewrites every page on every run —
  and why `_render_run_tab` restores `yview` rather than jumping a scrolled
  reader back to the top.
- **`_run_report_segments` is the ONE builder of a run's report**, consumed by
  the Log (`_render_results`, with severity routing) and by the page
  (`_write_run_report`, without — a run page is not the Log, and badging the
  Log for a line the user is looking at elsewhere would be a lie). A second
  copy would let the two disagree about a run's contents with nothing to tell
  them apart.
- **The units switch re-renders EVERY page in place and creates no tab.**
  A units switch measures nothing, so it is not a run. It still appends to the
  Log — that is what a chronological log is for, and
  `test_run_snapshot.py::test_a_units_re_render_follows_the_visibility_as_it_stands_then`
  reads it back from there. Every page, not just the newest, because the unit
  is a **rendering choice, not a recorded fact**: a run snapshot holds numbers
  and `_run_report_segments` reads `units_mode_var` live. Repainting only the
  newest did not leave an old page "as recorded", it left it stale until the
  next Calculate repainted it anyway (that path re-renders every page so their
  banners name the current run) — measured, one screen showing
  `-399.8 / -1.242 / 2` on page #3 and `-400 mOhm / -1.24 mH / 2 fF` on page
  #1, and then a silent flip the user did not ask for. What IS frozen per run
  is everything in the snapshot: id, label, port descriptor, `enabled`,
  `color_idx` and the numbers.
- **Freezing a trace joins the CURRENT run and rewrites its page in place**,
  for the same reason: the run number counts Calculates, and freezing measures
  nothing.
- **Run tabs are IN-MEMORY ONLY.** Run history is computed output and never
  reaches the session file. No `TraceConfig` field was added, so
  `test_session.py::TestFieldCoverage` is untouched; the guard on this side is
  `TestRunHistoryIsNotSaved`.
- **No `Z` / `Zmat` per tab** — the run snapshot already forbids it. Curve-level
  comparison is what a frozen trace is for.
- **No `Style.element_create` close button.** On the vista theme that means
  replacing the layout that draws the native tab, hand-wiring hit-testing, and
  a result that renders differently on the red-zone box with no test able to
  see it. Right-click (`nb.index("@x,y")`, and `<Button-3>` is not a Notebook
  class binding) does the job. `Close other runs` deliberately spares the kept
  ones, so `Close this run` stays the ONLY route by which a kept page is
  destroyed — which is exactly what the disabled Keep button tells the user to
  press.

### Port names, roles, and the Ports & Roles window

`tests/test_port_roles.py` is the guard, and every claim below was
mutation-checked.

- **`port_roles` in `pkg_rlc_core` is the ONE classifier.** The port-overview
  strip, the footer summary and the window all count off the same records —
  `_port_bucket` no longer exists in the GUI. A role is finer than a bucket
  (`probe +` / `probe −` collapse into one `probe` count via `ROLE_TO_BUCKET`)
  because the strip has to stay short while the window has to say which probe
  touched the port. With `nports=None` it still drops every `open` record,
  including an explicit `open` row: an open port is one the FILE has and the
  spec did not name, so without the file it is unknowable, and a count derived
  from the largest port typed would look authoritative and be wrong.
- **A `TerminationSet` carries no provenance, so `row_sources` is separate and
  is passed IN.** It walks the rows in exactly the order `rows_to_dsl_text`
  emits them (measurement ports, connections, then the kept text) and keeps the
  LAST writer, because the DSL is last-assignment-wins — that is the same rule
  that makes a `ground` row beat a probe, so the "From" column and the answer
  cannot disagree. It never raises: a half-typed range contributes nothing,
  exactly as it contributes nothing to the spec.
- **`_trace_role_rows` renders EVERY mode through the rows path, and that is
  deliberately the permissive one.** Modes 1/2/3/6 have no connections table
  but every one of them is expressible as one (that is the premise of the Mode
  5 DSL), so one rendering covers all five instead of five that can drift. It
  is also why the window can show a mode-6 probe-and-ground overlap that
  `build_terminations_coupling` REFUSES: refusing is right for Calculate and
  exactly wrong for a window whose job is to show what was typed. The synthetic
  rows are relabelled to the FIELD the user typed into (`GND / VDD`, `Port B`,
  `Short Pairs`) — telling a mode-1 user their port came from "probe row 1 (+)"
  names a row that exists nowhere on their screen.
- **The probe-and-ground flag states the rule of the MODE it is showing.**
  Mode 5 lets ground win (`WARN_PROBE_AND_GROUND`, "the ground row wins"); Mode
  6 does not — `build_terminations_coupling` raises, because a probe side is
  tied together and grounding one of its ports grounds the whole side. Both are
  pinned and intended, so `_role_warnings(..., coupling=)` picks the wording and
  `_port_roles_data` passes `coupling=(tc.mode == 6)`. Measured with the Mode-5
  wording on a mode-6 trace (probes on 1 and 2, GND field `1`): the window said
  "the ground row wins", the user read that as "legal, and I know which side
  won", and Calculate then refused the trace outright with `ERROR Port(s) 1 are
  listed both as a probe (measurement port 'c1') and as ground`. Mode 6 has
  neither a validation strip nor a footer strip, so this row is the ONLY thing
  on screen about the overlap; `WARN_PROBE_AND_GROUND_COUPLING` therefore says
  the same thing the exception does, plus what to do about it.
- **`collapse_ports` must never emit a space.** The DSL is whitespace-tokenised
  and the port field is `parts[0]`, so `1-3, 7` parses as the port field `1-3,`
  with a stray `7` where the keyword belongs. `1-3,7` round-trips through
  `parse_port_range`. This is what makes the write-back safe, not a formatting
  preference. Same rule for `_append_port_spec`, which APPENDS — replacing
  would throw away the only copy of what the field already said.
- **The open-port name check is a REMNANT check, and its thresholds are
  calibrated against real fixtures, not taste.** `name_prefix` strips only a
  TRAILING run of digits (and then the separators in front of it): stripping
  digits anywhere makes `c1_p` and `c2_p` one family and every use of
  `coupled_4port_float.s4p` raises a warning. `OPEN_CLUSTER_MIN_FAMILY = 4`
  keeps `coil1`/`coil2` out — probing one coil and floating the other is the
  ordinary way to use `coupled_2port_gndref.s2p`.
  `OPEN_CLUSTER_MAX_OPEN_FRACTION = 0.25` is what makes it a remnant: grounding
  5 of 10 is a decision, leaving 3 of 54 is a typo, and it is also what keeps a
  file whose ports are all `port1..port153` silent. A file with NO names is
  silent by construction (every prefix is `""`). The false-alarm tests run
  against every fixture in the repo under the config that fixture exists for.
- **`_validation_messages` gained `port_names` and still must not raise.** The
  new check is wrapped in its own `except` for that reason. It is the only
  message there that reads the FILE rather than the spec — everything else says
  "your spec is inconsistent", this one says "your spec is consistent and
  probably not what you meant", which is the failure that survives review.
- **A read-only `ttk.Treeview` is the RIGHT widget here and the repo's ban does
  not apply.** The ban is about the EDITABLE connection table, which needs cell
  editors Treeview does not have. Two hazards are handled and both fail
  SILENTLY otherwise: row height is frozen at 20 px whatever `tk scaling` and
  whatever font the style carries, so it is set from the font's metrics on a
  DERIVED style name (`PortRoles.Treeview`) — never by reconfiguring the global
  `Treeview`, which would reach every Treeview in the process; and tag colours
  are ignored on Tk builds whose `Style().map("Treeview", …)` carries
  `('!disabled', '!selected')` specs, which match every ordinary row and
  outrank the tag. `_fixed_map_filter` is applied unconditionally and is pure,
  so the rule itself is testable without a display.
- **Sorting is on the RAW record, never the rendered string.** `#` is an int
  and a string sort puts port 10 between 1 and 2 — the classic Treeview bug.
- **The Treeview is NOT registered with the wheel router.** `"Treeview"` is in
  `App._WHEEL_OWNERS`, so `_route_wheel` bails out over it and Tk's own class
  binding scrolls it; a registered handler would be dead code, and taking
  Treeview out of the set to reach one would break every other Treeview.
- **The window is modeless and refreshes from `_apply_editor_strips`.** Same
  contract: `after_idle`-coalesced, never raises, writes to nothing but its own
  widgets, never writes a `TraceConfig`, guards on `winfo_exists()`. Its
  refresh sits OUTSIDE that function's try/except so a window failure cannot
  blank the strips and a strip failure cannot leave the window stale.
  `_strips_wanted()` is why the strips now run outside mode 5 at all — without
  it the window froze the moment a mode-1 user edited the GND field, which is
  the edit it exists to check. No `grab_set`: a modal Toplevel that outlives
  its opener blocks event delivery and hangs `update()` (the documented style
  picker / scrollbar failure).
- **The write-back goes through the widgets, never into the `TraceConfig`.**
  `RowTable.add_row` / `PlaceholderEntry.set_value` plus
  `_schedule_editor_sync`, so auto-apply, the strips and the stale marker
  follow exactly as they do for a keystroke — poking the trace directly is
  overwritten by the next sync. A frozen trace refuses the write, by name, for
  the same reason `_sync_editor_to_trace` does.
- **This window IS the affordance `docs/design_connection_table.md` §5a
  deferred, and the five "Show Ports" pointers now point HERE.** The port cells
  take bare numbers because a name-bearing dropdown does not fit the editor
  width (a ttk popdown is only as wide as its widget, and 15 chars ≈ 105 px the
  431 px viewport does not have), so the substitute had to be findable: it is
  named in both table hints, in Help → Mode 5, in Help → Input syntax, and in
  the README. If a dropdown ever carries names, those five and this window's
  role in them are one decision, not six.

### The plot panel's control strip

`tests/test_plot_controls.py` is the guard, and every claim below was
mutation-checked. Before that file, **no test in the repo touched this panel**,
which is how it stayed broken.

- **The strip WRAPS; it must never be a plain `pack(side=LEFT)` run.** Measured:
  thirteen controls asking **918 px** into the 575 px right-hand pane at the
  declared 1040x600 minsize, and pack unmaps from the END — so `Im(Z)`, `Q`,
  `k`, the fullscreen-quantity combobox and the `Fullscreen` button were simply
  not on screen, with no scrollbar, no chevron and no other route to them. Two
  of those have no alternative at all: `k` is the coupling quantity Mode 6
  exists to produce (`AUX_PLOT_TYPES = ("k",)`) and Fullscreen is the documented
  escape hatch for a readout box too wide for a 4-subplot grid. It was not only
  the minsize — `_clamp_to_screen` opens the window at `min(1500, screen-80)`,
  so on the 1280-logical-px laptop its own comment names the app opened 1200 px
  wide and Fullscreen was off screen out of the box (sweep: 1040/1100 lost `k`
  and Fullscreen, 1160–1280 lost Fullscreen, 1320+ was whole).
- **`ReflowRow` lays out by `place`, and that is load-bearing TWICE.** Place
  does not propagate, so the strip's requested width is ~1 px and the 918 px no
  longer travels up through `PlotPanel` into the PanedWindow sash; and the wrap
  decision reads the strip's IMPOSED width (`fill=X` from the parent) and writes
  only its height, which cannot change that width. That makes it a **fixed
  point**, not the limit cycle `_apply_editor_scrollbars` documents — a layout
  rule that reads a size it can itself change flips forever and `update()` never
  returns, hanging the GUI and the test suite together. Do not "simplify" it to
  `grid` or `pack`.
- **A wrap costs plot height, so it must happen only when needed.** Measured:
  the strip is 29 px (one line) at 1500 and 58 px (two) at the 1040 and 1200
  widths where it does not fit. The default window pays nothing.
- **The assertion is "wholly inside", not `winfo_ismapped()`.** A placed widget
  stays mapped while it hangs off the right edge, so the test checks
  `x + width <= strip width` and `y + height <= strip height` for every child.
  Re-measure before adding a fourteenth control.

### Cursor readout (the plot's marker / V-line labels)

- **One cursor gets ONE readout, never one label per curve.** A vertical cursor
  crossing N curves used to draw N annotations, all anchored at the same x with
  the same fixed `(6, 4)` pt offset and no collision or boundary check. That is
  fine at N=1 and unreadable on a coupling plot, which is the normal case, not a
  corner: one mode-6 trace with 3 measurement ports expands to 3 self + 3 mutual
  curves and the mutual ones all sit near zero. Measured over 4 subplots: 21
  labels, **19 overlapping pairs, 20 of 21 crossing into the neighbouring
  subplot**, and the frequency printed 21 times for one cursor.
  `tests/test_plot_readout.py` is the guard and asserts the two structural
  properties directly off the rendered text — no two texts overlap, no text
  leaves its axes. It is the first coverage `pkg_rlc_plot` has ever had.
- **The readout box IS the legend.** It carries the colour/linestyle swatch and
  the curve name, so drawing a separate legend next to it is the same names
  twice competing for the same empty corner. When there is no cursor to read
  (`show_marker` off, or the `Readout` toggle off) `_draw_plain_legend` restores
  the old names-only legend on the first axes — do not let both exist at once.
- **The readout's corner is scored from the CURVE DATA and frozen until the next
  redraw.** matplotlib's `loc="best"` also weighs the legend's own size, so a
  value going from `2 nH` to `2.05 nH` flips the box to another corner while the
  user is dragging the cursor. `_pick_readout_loc` caches per axes and `redraw`
  clears the cache. The candidate set deliberately includes `center left` /
  `center right`: on a coupling plot the self curves sit at the top and the
  mutual ones near zero, so all four *corners* hold data and the middle band is
  the only empty space.
- **The name column is a measured budget, not a constant.** 4 subplots give each
  axes ~210 px against ~320 px at 3, and a box wider than its subplot is exactly
  the failure being fixed. `_name_budget` derives it from `ax.get_window_extent()`
  in em of the readout font (so it holds at any DPI), with `READOUT_NAME_MIN = 8`
  as a floor — rows that cannot be told apart are worse than a slightly wide box,
  and that corner is what the `Readout` toggle and Fullscreen are for. `_fit_names`
  keeps the **tail**: `osc_primary_coil_00` / `_01` differ only at the end, and
  `MAX_LABEL_LEN` (a legend-width rule) must not be applied before the shared
  trace prefix is stripped or they arrive already identical.
- **A value column is as wide as its widest cell OR its header.** Sizing on the
  values alone put a 7-char `5.1 GHz` over a 5-char column and threw every
  multi-cursor heading one place off the numbers it names.
- **V lines are cursors, so they are columns in the same box** — they had the
  identical per-curve-label bug and can be stacked several deep. `_anno_stack`
  entries are `(kind, artists)` and a `"v"` entry also owns one element of
  `_vline_freqs`; Delete must pop both or the readout keeps a column for a line
  that is no longer drawn.
- **The readout prints engineering units** (`_readout_value` → `format_si`):
  `300 pH`, not `0.3 nH`; `1.5 Ω`, not the `1.5e+03 mΩ` a plain `%.3g` produced.
  Non-finite reads `--` and the row **stays** — dropping it shifts every row
  below and the swatches stop lining up with the curves. This is why
  `pkg_rlc_plot` imports `pkg_rlc_core` (acyclic: core imports nothing back);
  a second copy of the rule would let the plot and the results pane disagree.
- **A dragged box must be captured BEFORE the legend is replaced, not from a
  button-release handler.** The box is draggable (`set_draggable(True,
  update="loc")`) and is rebuilt on every cursor move, so the position has to be
  read back off the old artist or it snaps home on the next marker drag.
  `DraggableLegend` registers its own release callback when the box is built —
  *after* `_PlotView.__init__` registered ours — so a release handler here runs
  first and reads the position from before the drag. `_capture_manual_locs` is
  therefore called at the top of `_refresh_marker` and `redraw`, which does not
  depend on callback order at all. It keys on **plot type, not axes id**, so a
  placement survives Calculate rebuilding the axes. A dragged legend stores
  `_loc` as a 2-tuple and an auto-placed one keeps an integer code — that is the
  discriminator.
- **`loc=(x, y)`, never `Legend.set_loc`.** `set_loc` is matplotlib 3.8+; the red
  zone is pinned to **3.7.2** (see `ENVIRONMENT.local.md`). The 2-tuple form of
  `loc=` is accepted by both and is exactly what `update="loc"` writes back.
- **The readout wins the press gesture, and double-click is the way out.**
  `_legend_at` gates `_on_press`: a box sitting over the marker line would
  otherwise move the cursor and the box with one drag. Double-clicking inside it
  drops the manual placement — and it must `remove()` the legend *before*
  `_refresh_marker`, or the capture reads the dragged position straight back out
  of the artist and undoes the reset.
- **A test that parks the readout where the marker is not passes without the
  guard.** `test_pressing_on_the_box_does_not_grab_the_marker` read green that
  way once. It now computes the marker's x and asserts the press lands within
  `MARKER_PIXEL_TOLERANCE` of it before testing anything — keep that
  precondition assertion, it is what stops the test rotting back into a
  tautology.

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

### `reduce_snp.py` specifics

- **Standalone, no repo imports.** It runs from a scratch directory on a sim server. numpy + stdlib only. Duplicating the Touchstone parser here is intentional, not an oversight — keep the n=2 column-order quirk mirrored on both sides.
- **Three port buckets, not two.** KEEP becomes an output port; a group named `GND`/`GROUND`/`SHORT` is shorted to the reference node (**delete that row and column in Y**, because V=0); everything unlisted is Schur-eliminated. Grounding is *not* the same as opening — PKG ground balls need the GND group or the result is wrong.
- **A range token must be numeric END TO END.** `4:1:17` (`start:step:stop`, mirroring
  the GUI's `parse_port_range`) and `6-14` are ranges; anything else goes to the name
  resolver, because `-` and `:` are ordinary characters in a net name (`VDD-1`,
  `I0:VDD`) and this is the one parser in the repo where numbers and names share a
  token slot. A token that is both a valid range **and** an exact port name is refused,
  not guessed. Unlike `parse_port_range`, a range that expands to nothing (`17:1:4`)
  is an **error** here — in a config file a silently-empty group is a wrong answer with
  no symptom, and `_fmt_ports` collapses the echo back into runs so a 54-ball GND group
  stays one readable line. `tests/test_reduce_snp.py::TestPortRanges` is the guard and
  all five behaviours above were mutation-checked.
- **The port config is a HAND-WRITTEN file and is read as one.** Every failure below
  rendered as `31:1:52` in an editor and was refused as *"neither an integer, a port
  range, nor a known port name"* — the user is looking at a line that is already
  correct, which is what made the message unactionable. `read_config_text` sniffs the
  encoding (Notepad's "Unicode" is UTF-16 and read as `' 3 1 : 1 : 5 2 '`; its "UTF-8"
  writes a BOM that glued itself to the leading `#`, so the first group HEADER parsed
  as data and every ground ball landed in the keep group; a GBK comment raised nothing
  and ate its line). `normalise_config_line` folds the full-width punctuation a CJK
  input method produces (`：，、；－–—＃！＝`, NBSP, a stray BOM) — none of which is legal
  in a port name. Full-width DIGITS and U+3000 are deliberately **not** in that map:
  `\d`, `\s` and `int()` are Unicode-aware already, so an entry would be dead code and
  would stop the tests noticing if a regex were ever narrowed to `[0-9]`. A `#` after
  the ports starts a comment (`(?:^|(?<=\s))#`, so a port *named* `NET#3` survives) —
  before this the module's OWN docstring example, `1, 2, 3, 4:1:17, 80  # start:step:stop`,
  was a spec this parser refused. `describe_bad_token` is what a leftover says: the
  offending character **by code point** (it is invisible on screen), or for `31:52` the
  exact spelling to type (`'31:1:52'` or `'31-52'`) rather than the rule. `31:52` stays
  unsupported on purpose — `parse_port_range` refuses it too, and a config that works
  in one and not the other is worse than a clear refusal.
  `tests/test_reduce_snp.py::TestConfigFileIsReadAsWritten` is the guard; every case is
  mutation-checked, and the utf-8-BOM one goes red only when BOTH halves (the sniff and
  the U+FEFF map entry) are reverted, which is deliberate redundancy.
- **`--keep` / `--gnd` reach the SAME code path as a config file.** They build the
  `{group: [token]}` mapping `parse_port_config` returns (`groups_from_cli`) and go
  through `resolve_port_config` — one resolver, one set of error messages. `--keep` is
  repeatable and takes an optional `NAME=` prefix so it can express several KEEP groups;
  a reserved ground name there is refused rather than silently becoming a GND group.
  Either source may be given, or both (the file first, inline merged on top).
- **`--method matched` with no GND ports == plain S sub-matrix.** Proven in `test_matched_equals_submatrix`; the code takes the sub-matrix fast path there. Terminating in Z0 == adding `Y0=1/z0` to the unused diagonal before elimination.
- **Do NOT use `np.fromstring(sep=' ')` in the parser.** It is ~9x *slower* than `float()` on numpy 2.x and truncates silently on a bad token. The `array.array('d')` + bounded staging list + `np.frombuffer` view is the measured optimum (2.5x less peak memory than a list-of-floats for +16% time).
- **Build `s` via `s.real = ... / s.imag = ...`,** never `raw[...,0] + 1j*raw[...,1]` — the latter allocates two full-size complex temporaries and doubles peak memory on multi-GB files.
- **Output defaults to `RI` with 12 significant digits.** DB output loses precision on small entries; on a 4-port fixture this default is ~300x more accurate than the old `DB`/`%.10g` combination.
- **Frequency-batched everywhere** (`--batch`, default 256) so a 153-port file doesn't materialise every Y matrix at once. `s_to_y`/`y_to_s`/Schur all operate on stacked `(F,N,N)` arrays via `np.linalg.solve`, with a per-frequency `lstsq` fallback in `_solve_batch`.

### `deploy/` specifics (red-zone pipeline)

- **The package is a blacklist, not a whitelist.** `git archive` ships everything
  except what `.gitattributes` marks `export-ignore`. New scripts are packaged
  automatically — do NOT convert this to an explicit file list, that was the
  design requirement.
- **Shell scripts must be LF in the git index.** CRLF there is the one mistake
  that bricks a deploy (`bash: $'\r': command not found`). `.gitattributes` pins
  `*.sh text eol=lf`, and `pack.ps1` aborts if the index ever disagrees. Keep both
  halves of that guard.
- **`git archive`, never the working tree.** Packing from committed blobs is what
  makes the package immune to autocrlf, backslash paths, and lost exec bits.
- **`pack.ps1` emits exactly two files: the tarball and its `.sha256`.** It also
  emitted a loose, hash-named `reduce_snp_<short>.py` "fast lane" for copying
  onto a sim server; that was removed because a `dist/` with four files in it
  made the operator ask what the extra ones were, and the answer ("the same file
  again, for a workflow you may not have") did not justify the question. The
  sim-server case is `tar -xzf <pkg> Snp_analyzer/reduce_snp.py`. Do not
  reintroduce a second delivery artifact without a use case that cannot be
  served from inside the package.
- **`cmd.exe` is resolved from `%ComSpec%`, not the PATH.** The remaining
  `cmd.exe` call (the CR-byte preflight, which redirects `git archive` to a probe
  tar) must not depend on `C:\Windows\System32` being on the PATH — a yellow-zone
  box whose PATH had lost it failed with "The term 'cmd.exe' is not recognized",
  which reads as a script bug rather than a broken environment. PowerShell's own
  capture cannot replace the redirection: it re-encodes the stream and would turn
  LF into CRLF.
- **`deploy.sh` touches only the install dir**, never the parent. Preserves
  `.deploy/` plus anything in `.deploy/preserve.list`, and rolls back via an `ERR`
  trap if the swap fails halfway.
- **Nothing may be written outside the install dir** — no `/tmp`, no `/opt`, no
  `mktemp`. All staging, backups and scratch go under `<install>/.deploy/`. This
  is an operator requirement, not a preference; `doctor.sh` uses `.deploy/tmp`.
- **Rollback must distinguish backup-phase from install-phase failure** (`PHASE`).
  A partial backup does NOT license deleting what is still in the install dir —
  those are the only surviving originals. Collapsing the two branches silently
  destroys the install; there is a regression test for this in the commit history.
- **Neither the install dir name nor the package root name is hardcoded.**
  `deploy.sh` treats its own directory as the install, and auto-detects the single
  top-level dir in the archive. `pack.ps1 -Name` sets the package root
  (default `Snp_analyzer`).
- **No-argument deploy is the primary path.** `bash deploy.sh` picks the newest
  `*.tar.gz` in the install dir and prints which it chose. Keep the explicit-path
  form working as an override.
- **The far side has no network, no pip, no venv.** Never add a dependency that
  cannot be assumed present; `numpy` is the only hard one. Anything new that the
  GUI needs must degrade gracefully, and `deploy/_env_check.py` must learn about
  it so `doctor.sh` reports the right tier.
- **`_env_check.py` is parse-compatible with Python 2** on purpose, so an ancient
  interpreter reports itself as unusable instead of throwing a `SyntaxError` that
  looks like a corrupt package. No f-strings, no annotations in that file.

## How to run tests

```bash
python tests/run_parallel.py            # the whole suite -- use this
python tests/run_parallel.py --fast     # 5.5 s, 976 tests, the eighteen no-Tk modules
python tests/run_parallel.py -m attrib coupling core    # substring on module name
```

**Re-measured on this box after the composition work: 2045 tests / 364 shards in 333.7 s,
and 976 tests in 5.5 s for `--fast`.** (The historical figures the runner's docstring
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
that a change to `pkg_rlc_core` numerics or to `pkg_rlc_attrib` cannot affect. Run the full
parallel suite once before reporting — never the serial `discover`.

`--fast` now covers the cold-start screen: `test_attrib_coldstart` and
`test_attrib_cli_coldstart` were qualified on the one property `FAST_MODULES` has — neither
imports tkinter — and were added, which is what took it from 523 tests / 2.9 s to 642 / 4.1 s;
the runner's own suite then took it to 699 / 4.4 s. Round 2 added the four composition
modules on the same qualification (`test_compose`, `test_compose_cli`, `test_attrib_composed`,
`test_conn_nets` — `test_compose_cli` has its own test asserting `pkg_rlc_gui` never entered
`sys.modules`), for **976 tests / 5.5 s**. `test_conn_rowshape` is deliberately excluded:
it drives real widgets and its slowest shard alone is 19 s.

## How to add a new measurement mode

Pick the **next unused integer** code (4 is retired, not free) and never renumber the existing ones — saved trace configs carry the integer.

1. **Core**: add a `build_terminations_modeN(...)` helper in `pkg_rlc_core.py` that produces a `TerminationSet`, converting 1-based to 0-based *there* and nowhere deeper. If a new termination semantic is needed, add a dataclass to the `PortTermination` / `Coupling` unions and handle it in `compute_z_matrix`'s evaluation order (lumped -> short merge -> ground/vdd drop -> Schur -> probe-node contraction). If the mode only rearranges probes, it needs no new semantic at all — `Signal(group, sign)` already covers arbitrarily many measurement ports.
2. **GUI**: add a new radio button in `_build_editor`, add the fields to `TraceConfig`, register placeholder hints in `MODE_PLACEHOLDERS`, extend `_update_mode_visibility` to show/hide and re-set placeholders, extend `_port_descriptor`, and dispatch in `_build_termination`. Mirror the dispatch in the CLI argparser (`_make_arg_parser` + `_run_cli`) and reject flags that belong to other modes with a clear message. A **table-based** mode registers NO `MODE_PLACEHOLDERS` entry — a cell cannot hold a hint, so its hint is a `_CollapsibleHint` under the table.
3. **Help**: document the mode in `pkg_rlc_help.py` with assumptions, inputs, and a worked example. **A new tab is no longer free**: the ten existing tabs need 968 px against `HELP_WINDOW_WIDTH = 1010`, and an eleventh takes the strip to 1033–1064 px depending on its label (measured), where a `ttk.Notebook` silently clips the LAST tab. Either fold the mode into the nearest existing tab (what port attribution and the cold-start screen did, into `Mode 6 (Coupling)`) or widen `HELP_WINDOW_WIDTH` and re-run `tests/test_session.py::TestHelpTabsAllFit`, which re-measures it. Either way update the `Input syntax` tab if the mode adds syntax, the `Mode 5 (Custom)` tab if it could also be expressed in the DSL, and cross-reference from `Overview` and `Worked examples`.
4. **Docs**: update the mode table in `README.md` and add a section to `docs/theory.md`. If the mode changes what a "measurement" is (rather than just which ports are terminated how), say so in both.
5. **Tests**: add a case in `test_core.py` (or `test_coupling.py` for anything probe-shaped) that builds the new termination set and asserts the result matches a hand-coded reference. Also add an "equivalence test" pinning that the new named mode produces identical results to a hand-built `TerminationSet`.
6. **Golden regression**: if the change touches `compute_z_matrix` at all, run `python -m unittest tests.test_golden_regression` and expect it green *without* regenerating `golden_legacy.npz`. Adding a fixture or a new mode does not require regeneration; a numeric drift in an existing mode means you broke something.

## How to add a new fit model

1. Add a dataclass `XxxFit` in `pkg_rlc_core.py` and a `fit_xxx(freqs, Z, f_min, f_max)` function. Use `_scaled_lstsq` if columns have very different magnitudes.
2. Add an `eval_xxx_model(fit, freqs)` helper for plot-overlay rendering.
3. Wire the model name into `fit_auto` selection logic if appropriate.
4. Add the option to the GUI `Fit Model` combobox in `_build_global_controls` and to the CLI `--fit` choices.
5. Add tests that recover known parameters from synthetic Z data within tight tolerance.

## Don'ts

- **Do not pull in `scikit-rf`.** The custom parser is deliberate — it must handle EDA-tool quirks (renamed extensions, missing option lines, ambiguous port count) that scikit-rf does not.
- **Do not add `pandas`** for CSV writing; stdlib is sufficient.
- **Do not switch GUI frameworks.** Tkinter is required.
- `scipy.optimize` and `scipy.linalg` are acceptable; gratuitous deps are not.
