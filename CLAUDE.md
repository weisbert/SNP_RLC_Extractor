# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction — and, with more than one measurement port defined, the mutual coupling between them (M, k, M/L, C_c). Used for IC packages, EMX layout traces, DCO inductors, decap, and inductor-to-inductor pulling / spur budgeting.

## Module map

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc_core.py`       | Touchstone parser, S<->Y, unified `TerminationSet` model + the Mode 5 DSL (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`), the connection-table row model (`MeasPortRow`, `ConnectionRow`, `rows_to_dsl_text`, `dsl_text_to_rows`, `build_terminations_rows`), `parse_mport_spec`, `resolve_meas_ports`, `compute_z_matrix` / `compute_z`, `extract_rlc_at_freq` / `extract_coupling_at_freq`, `fit_inductor` / `fit_capacitor` / `fit_auto`. |
| `pkg_rlc_plot.py`       | Matplotlib plot panel: multi-subplot grid over R/L/C/\|Z\|/Re/Im/Q/**k**, draggable freq marker, M / V / Delete keys, fullscreen window. Quantities that cannot be derived from one `(freqs, Z)` pair (today only `k`) arrive via the optional `Trace.aux` dict. |
| `pkg_rlc_gui.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` hints and the `RowTable` / `ColumnSpec` row editor (measurement ports in modes 5+6, connections in mode 5), the port-overview / validation strips, the "Edit as text…" hatch (`_import_dsl_text`, `_editor_dsl_text`), results pane. Re-exports the DSL helpers it no longer defines. |
| `pkg_rlc_help.py`       | In-app Help window content (`HELP_TOPICS`, `HelpWindow`). One tab per mode + syntax + worked examples. |
| `pkg_rlc_extractor.py`  | Entry point: dispatches GUI vs CLI from argv. CLI `--mode gnd \| p2p \| coupling`, `--mport` repeatable. |
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |
| `tests/`                | `unittest`-based suite (334 tests covering parser line-break/signed-zero edge cases, port range, mport specs, short groups, content sniffer, terminations, termination precedence, the connection-row model, the `RowTable` widget, the Mode 5 editor, fits, Schur fallback, the coupling matrix, degenerate probes, the bit-exact golden regression, and `reduce_snp`). |
| `tests/test_connection_rows.py` | Row model: rows<->DSL round trip, the equivalence tests pinning that rows reproduce `build_terminations_mode1/2/3` *including* the ground-wins overlap the golden reference cannot see, and the reordering hazard that forces `_import_dsl_text`'s verbatim fallback. |
| `tests/test_row_table.py` | Drives real Tk widgets (skips cleanly with no display): `RowTable` add/delete/get/set/defaults/notification, the `mp1_*`->`mports` and `custom_text`->tables migrations, and that Duplicate shares neither row list. |
| `tests/test_mode5_editor.py` | Stage 3: the pure text<->rows import decision and both strip renderers, plus Tk-driven editor wiring, per-mode widget visibility, the text hatch, the CSV gate, wheel routing, and the LAYOUT numbers (`ismapped` / `reqwidth` / `xview` / `scrollregion` / `sashpos`) measured off a mapped window. |
| `tests/generate_test_snp.py` | Builds synthetic fixtures with analytically known R/L/C/M; run as a script to (re)generate `tests/fixtures/`. The `COUPLED_*` module constants are the single source of truth for the coupled-coil fixtures. |
| `tests/test_golden_regression.py` | Replays `tests/fixtures/golden_legacy.npz` through the current API and asserts `assert_array_equal`. This is the guard on every "stays bit-identical" claim below. |
| `tests/_golden_capture.py` | Script + case registry that (re)generates `golden_legacy.npz`. NOT auto-discovered (leading underscore). Regenerate ONLY in the same commit that justifies moving the reference. |
| `tests/_smoke.py`       | Manual sanity-check script (NOT auto-discovered by `unittest`). |

## Critical invariants (do not regress these)

- **All Listboxes set `exportselection=False`.** Without it, clicking an Entry/Spinbox steals the X selection and clears the highlight — `Apply to Trace` then silently fails.
- **Auto-sync editor on Calculate.** Before computing, silently push current editor fields into the selected trace; users routinely click Calculate without Apply.
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
  `side=BOTTOM` **before** the editor, and `Apply to Trace` is packed `side=BOTTOM` inside
  the editor **before** the scrollable body. Do not reorder either.
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
  so it is deferred to stage 4. Until then the substitute has to be *findable*: it is named
  in both table hints, in Help → Mode 5 and Help → Input syntax, and in the README, and
  `_on_show_ports` falls back to the editor's file rather than silently doing nothing when
  the Files listbox has no selection. If the dropdown ever carries names, delete those five
  pointers together.
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
- **Mode 5 passes `nports` to `build_terminations_rows`.** It used to pass none, so a
  one-digit typo (`3 / 5` on a 4-port file) became a plausible wrong number until
  `compute_z_matrix`'s backstop. Likewise the CSV exporter gates the coupling block on
  `tc.Zmat is not None`, the same predicate `_on_calculate` routes on — gating on
  `tc.mode == 6` exported a two-probe Mode 5 trace as a well-formed scalar table with
  every M and every k silently absent.

- **Plot quantities that need more than one curve arrive via `Trace.aux`.** `k` needs three curves at once (`Z_ab`, `Z_aa`, `Z_bb`) and so cannot be derived from a single `(freqs, Z)` pair; the GUI precomputes it and attaches it. `trace_y_values` must return an all-NaN array (draw nothing) for a trace with no matching `aux` entry, never raise — self curves share the subplot grid with mutual ones. New derived quantities go in `AUX_PLOT_TYPES` the same way.

### `reduce_snp.py` specifics

- **Standalone, no repo imports.** It runs from a scratch directory on a sim server. numpy + stdlib only. Duplicating the Touchstone parser here is intentional, not an oversight — keep the n=2 column-order quirk mirrored on both sides.
- **Three port buckets, not two.** KEEP becomes an output port; a group named `GND`/`GROUND`/`SHORT` is shorted to the reference node (**delete that row and column in Y**, because V=0); everything unlisted is Schur-eliminated. Grounding is *not* the same as opening — PKG ground balls need the GND group or the result is wrong.
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
