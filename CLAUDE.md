# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction — and, with more than one measurement port defined, the mutual coupling between them (M, k, M/L, C_c). Used for IC packages, EMX layout traces, DCO inductors, decap, and inductor-to-inductor pulling / spur budgeting.

## Module map

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc_core.py`       | Touchstone parser (+ `TouchstoneParseError` / `diagnose_touchstone` / `check_touchstone`), S<->Y, unified `TerminationSet` model + the Mode 5 DSL (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`), the connection-table row model (`MeasPortRow`, `ConnectionRow`, `rows_to_dsl_text`, `dsl_text_to_rows`, `build_terminations_rows`), `parse_mport_spec`, `resolve_meas_ports`, `compute_z_matrix` / `compute_z`, `extract_rlc_at_freq` / `extract_coupling_at_freq`, `fit_inductor` / `fit_capacitor` / `fit_auto`. |
| `pkg_rlc_plot.py`       | Matplotlib plot panel: multi-subplot grid over R/L/C/\|Z\|/Re/Im/Q/**k**, draggable freq marker, M / V / Delete keys, fullscreen window. Quantities that cannot be derived from one `(freqs, Z)` pair (today only `k`) arrive via the optional `Trace.aux` dict. |
| `pkg_rlc_gui.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` hints and the `RowTable` / `ColumnSpec` row editor (measurement ports in modes 5+6, connections in mode 5), the `StylePicker` colour/linestyle palette, auto-apply (`_schedule_editor_sync` / `_flush_editor_sync`), per-trace plot visibility (`_replot_from_cache`), the port-overview / validation strips, the "Edit as text…" hatch (`_import_dsl_text`, `_editor_dsl_text`), the frozen-trace snapshot (`_freeze_trace_config`, the Traces-list right-click menu), the File menu and the JSON session format (`session_to_dict` / `session_from_dict` / `SessionError` / `autosave_path`), the results pane (a `ttk.Notebook` whose tab 0 is the Log, with `log_tab_label` / `_append_result(severity)` / `_select_results_tab`). Re-exports the DSL helpers it no longer defines. |
| `pkg_rlc_gui.py` (cont.) | Plus the **Ports & Roles** window (`PortRolesWindow`, `_trace_role_rows`, `_role_warnings`, `_roles_header`, `apply_ports_as`), which is what `Show Ports` now opens. |
| `pkg_rlc_help.py`       | In-app Help window content (`HELP_TOPICS`, `HelpWindow`, `HELP_WINDOW_WIDTH`). One tab per mode + syntax + save/load + worked examples. |
| `pkg_rlc_extractor.py`  | Entry point: dispatches GUI vs CLI from argv. CLI `--mode gnd \| p2p \| coupling`, `--mport` repeatable. |
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |
| `tests/test_parse_diagnostics.py` | The robust-reading work: what a file says about itself (span, sweep description, DC / \|S\|>1 notes) and what happens when it cannot be read. Every refusal test pins the **verdict** and the **line number**, not just "raises ValueError" — that would have passed before any of it existed. Plus the recovery cases (UTF-16, BOM, commas, `D` exponents, extension tiebreak) and the two GUI affordances. |
| `tests/test_session.py` | Save Config / Load Config / Restore Last Session. Pure round trip (no Tk) for the trace fields, the refusal verdicts, the hand-edit tolerance and the path precedence; Tk-driven for the App-level save→wipe→load, the missing-file path, the autosave, and that the File menu and its accelerators are reachable. Also the guard on the Help window's tab strip, which the tenth tab pushed past the old 950 px. |
| `tests/test_results_notebook.py` | The Results pane's `ttk.Notebook`: that the Log is tab 0, selected and MAPPED at startup (both are mechanical preconditions of tests elsewhere), the width-stable badge measured in the tab strip's own font, the unseen-warning count, the ERROR claim on the pane and the severity routing of the real call sites, plus the measured proof that a 30-tab strip does not move the left panel. Every guard mutation-checked. |
| `tests/`                | `unittest`-based suite (646 tests covering parser line-break/signed-zero edge cases, port range, mport specs, short groups, content sniffer, terminations, termination precedence, the connection-row model, the `RowTable` widget, the Mode 5 editor, auto-apply / style picker / plot visibility, the session file, the ranked coupling report, fits, Schur fallback, the coupling matrix, degenerate probes, the bit-exact golden regression, and `reduce_snp`). |
| `tests/test_editor_autoapply.py` | The commit-step removal: WHEN the editor writes into a `TraceConfig` and WHICH one it lands on (the deferral, the object capture, the flush-before-selection-change), the style picker's storage / reachability / honesty about multi-curve traces, and that hiding a curve neither recomputes it nor destroys the cursors. Every guard here was mutation-checked. |
| `tests/test_connection_rows.py` | Row model: rows<->DSL round trip, the equivalence tests pinning that rows reproduce `build_terminations_mode1/2/3` *including* the ground-wins overlap the golden reference cannot see, and the reordering hazard that forces `_import_dsl_text`'s verbatim fallback. |
| `tests/test_row_table.py` | Drives real Tk widgets (skips cleanly with no display): `RowTable` add/delete/get/set/defaults/notification, the `mp1_*`->`mports` and `custom_text`->tables migrations, and that Duplicate shares neither row list. |
| `tests/test_mode5_editor.py` | Stage 3: the pure text<->rows import decision and both strip renderers, plus Tk-driven editor wiring, per-mode widget visibility, the text hatch, the CSV gate, wheel routing, and the LAYOUT numbers (`ismapped` / `reqwidth` / `xview` / `scrollregion` / `sashpos`) measured off a mapped window. |
| `tests/test_freeze_trace.py` | "Freeze as new trace": the pure copy rules (config copied, lists element-wise, results REFERENCED), the two refusals (Calculate skips it, the editor cannot write it), that everything else still works (plot / show-hide / CSV / Remove), the right-click menu, and the session round trip that comes back without numbers and says so. Every guard mutation-checked. |
| `tests/test_port_roles.py` | Port names put to work: the pure classifier (`port_roles`), the provenance map (`row_sources`), the run-collapser, the open-port name check with its false-alarm cases run against every real fixture, `_trace_role_rows` (any mode → rows), and the Tk-driven Ports & Roles window — filter, sort-on-the-raw-value, both Treeview hazards, the flagged rows and the collapsed-range write-back. Every guard mutation-checked. |
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
- **The `G == 1, no minus side` branch deliberately has NO degeneracy check.** `1/y_eff -> inf` is already the honest reading of a probe with no return path, and `y_eff` also crosses zero at a genuine parallel anti-resonance, where a huge `Z` is the answer the user came for. No single-frequency magnitude test can tell those apart.
- **`SCHUR_COLLAPSE_TOL` is advisory only — it must never produce a NaN.** It flags `|Y_kk - Y_ko @ X|` falling under `1e-12` of its own two terms (pure cancellation noise). Unlike the rank test this is a magnitude heuristic: healthy fixtures bottom out at `3.8e-10` against `7e-16` for the degenerate case, so the margin is real but finite. Checked once per chunk (`i == 0`) and only when `>= 2` ports survive the reduction.
- **Port indices are validated against the file's port count** in `_validate_port_indices`, called from `compute_z_matrix` (and from `build_terminations_coupling` when `nports=` is passed, which the GUI and CLI both do). Before this, `"3 / 5"` on a 4-port file silently became a ground-referenced probe reporting a plausible wrong number. The resolver only scans `range(n)`, so nothing deeper can catch it.
- **A probe port may not also be a GND port (Mode 6 only).** A probe side is tied together, so grounding one of its ports grounds the whole side; `build_terminations_coupling` raises. `build_terminations_mode1/2/3` keep their historical "ground wins" precedence — do not "fix" those, the golden reference pins them.
- **`compute_z` warns when `G > 1`.** It returns only measurement port 1. Only Mode 5 can get there (the named builders always produce `G == 1`), and Mode 5 is exactly the free-text mode where `signal V` instead of `signal B` silently defines a second measurement port and changes the answer by 37%.
- **`RECIPROCITY_WARN = 1e-3` lives in `pkg_rlc_core`** and is imported by both `pkg_rlc_gui` and `pkg_rlc_extractor`. They used to disagree (1e-3 vs 1e-12), so the same file got opposite verdicts and the CLI cried wolf on every real EM file. The metric skips non-finite off-diagonal entries so one undefined measurement port cannot poison it.
- **`M/L` is the Norton injection ratio, NOT the current-transfer ratio.** The exact ratio into a shorted port `a` is `I_a/I_b = -Z_ab/Z_aa`; `M/L_a` equals its magnitude only where `w*L_a >> R_a` (1098% apart at 10 MHz for `L=2n, R=1.5`). The label is "coupling ratio" everywhere — core docstring, CLI report, GUI legend, Help, README, theory.md. Keep the five in sync.
- **The GUI's pair list is RANKED by `max(|M/L_a|, |M/L_b|)` and floored at `COUPLING_FLOOR_DB = -60`.** Six measurement ports make 15 pairs, and nested-loop `(a, b)` order carries no information about which of them matter. `|k|` alone is the wrong key: `|k| = 0.02` between two 2 nH coils and between a 2 nH and a 500 pH coil are different problems — same `M`, 4x the injection into the small one. `rank_coupling_pairs` is pure and mutation-checked, and **magnitude appears there and nowhere else** — every printed cell stays signed. Three rules are load-bearing: `_pair_strength` is computed **linearly**, not from the `*_dB` fields (`_ratio_db(0)` is NaN, and a pair with `M = 0` is the weakest there is, not an undefined one); a pair with an **undefined** ratio sorts last and is **never** folded away (NaN is a missing measurement, not a small number); and the **strongest** pair is never folded away either, or a block can consist of nothing but "3 pairs were too weak to list". The `(see Export CSV)` pointer is true because `_write_coupling_csv` enumerates every unordered pair straight off the Z matrix and has no floor — do not give it one.
- **`compute_z` is a thin wrapper returning `Zmat[:, 0, 0]`** — the self impedance of the FIRST measurement port, and a strided **view**, not a fresh contiguous array. Copy before writing into it or before handing it to code that assumes C-contiguity (the GUI does `np.ascontiguousarray`).
- **`tests/fixtures/golden_legacy.npz` is the guard for all of the above.** It pins `parse_touchstone -> s_to_y -> compute_z` bit-for-bit for every fixture and for representative Mode 1/2/3/4/5 cases. If it fails, the reduction path changed: fix the change, do not regenerate the reference to make the test pass.
- **The Mode 5 DSL and its helpers live in `pkg_rlc_core.py`** (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`) — terminations belong to core. `pkg_rlc_gui.py` re-imports them so `from pkg_rlc_gui import parse_si` and friends keep resolving; keep that re-export list intact.
- **DSL signal syntax is `<port> signal <groupname> [+|-]`.** Group names are arbitrary strings; the sign is a **separate whitespace token** defaulting to `+`, and anything other than exactly `+` or `-` raises. A name whose `.upper()` is `A` or `B` is upper-cased so legacy `signal a` / `signal b` keep working. There is deliberately **no** "signal group must be A or B" validation any more, in either `compute_z_matrix` or the DSL — don't reintroduce it.
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
- **Port cells take NUMBERS; `Show Ports` is the only route to the file's port names.**
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
  Re-measure `_ed_form.winfo_reqwidth()` before adding one; the in-file comment beside
  `CONN_TABLE_COLUMNS` carries the same two numbers. The table is gridded across all four
  form columns with its caption **above** it, because a label beside it costs 91 px. The
  editor canvas has an **x-scrollbar** as the safety net — the budget is a 100%-font
  number and no column set fits at 150% DPI. It also fixes a pre-existing Mode 6 defect:
  form 463 vs canvas 431, `xview (0.0, 0.962)`, 32 px of the ✕ column unreachable with no
  way to scroll to it.
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
  on the Connections caption). `_sync_editor_to_trace` stays the only writer to a
  `TraceConfig`. `_on_editor_rows_changed` returns early while `_suppress_editor_sync` is
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
  `_render_results`, not at collection time** — `_last_result_rows` holds every trace so a
  units-mode re-render follows the visibility as it stands then, and `Calculate This Trace`
  still narrows the work rather than the report. `fit_lines` entries are therefore
  `(tc, line)` tuples: a fit summary under a table with no such row is an orphan. Two empty
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
  tab is 22 px, about three characters. A future run tab wants a short label
  (`#1`) and a rolling cap, not a timestamp — a timestamped label is what drove
  a 50-tab strip to a 8808 px requested width in the measurements.

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
python -m unittest discover -s tests
```

## How to add a new measurement mode

Pick the **next unused integer** code (4 is retired, not free) and never renumber the existing ones — saved trace configs carry the integer.

1. **Core**: add a `build_terminations_modeN(...)` helper in `pkg_rlc_core.py` that produces a `TerminationSet`, converting 1-based to 0-based *there* and nowhere deeper. If a new termination semantic is needed, add a dataclass to the `PortTermination` / `Coupling` unions and handle it in `compute_z_matrix`'s evaluation order (lumped -> short merge -> ground/vdd drop -> Schur -> probe-node contraction). If the mode only rearranges probes, it needs no new semantic at all — `Signal(group, sign)` already covers arbitrarily many measurement ports.
2. **GUI**: add a new radio button in `_build_editor`, add the fields to `TraceConfig`, register placeholder hints in `MODE_PLACEHOLDERS`, extend `_update_mode_visibility` to show/hide and re-set placeholders, extend `_port_descriptor`, and dispatch in `_build_termination`. Mirror the dispatch in the CLI argparser (`_make_arg_parser` + `_run_cli`) and reject flags that belong to other modes with a clear message. A **table-based** mode registers NO `MODE_PLACEHOLDERS` entry — a cell cannot hold a hint, so its hint is a `_CollapsibleHint` under the table.
3. **Help**: add a new tab to `HELP_TOPICS` in `pkg_rlc_help.py` with assumptions, inputs, and a worked example. Update the `Input syntax` tab if the mode adds syntax, and the `Mode 5 (Custom)` tab if the new mode could also be expressed in the DSL.
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
