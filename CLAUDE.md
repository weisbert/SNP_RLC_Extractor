# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction; used for IC packages, EMX layout traces, DCO inductors, and decap.

## Module map

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc_core.py`       | Touchstone parser, S<->Y, unified `TerminationSet` model, `compute_z`, `extract_rlc_at_freq`, `fit_inductor` / `fit_capacitor` / `fit_auto`. |
| `pkg_rlc_plot.py`       | Matplotlib plot panel: multi-subplot grid, draggable freq marker, M / V / Delete keys, fullscreen window. |
| `pkg_rlc_gui.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` / `PlaceholderText` hints, results pane. |
| `pkg_rlc_help.py`       | In-app Help window content (`HELP_TOPICS`, `HelpWindow`). One tab per mode + syntax + worked examples. |
| `pkg_rlc_extractor.py`  | Entry point: dispatches GUI vs CLI from argv.                                   |
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `tests/`                | `unittest`-based suite (102 tests covering parser, port range, short groups, content sniffer, terminations, fits, Schur fallback, and `reduce_snp`). |
| `tests/generate_test_snp.py` | Builds synthetic fixtures with analytically known R/L/C; run as a script to (re)generate `tests/fixtures/`. |
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
- **Touchstone v1 quirk for n=2.** The 2-port column order is `S11 S21 S12 S22` (column-major), but n>=3 is row-major. `parse_touchstone` transposes only when `nports == 2`. `tests/generate_test_snp.py:write_touchstone` writes the matching column order on output. Don't "fix" either side without fixing the other.
- **Capacitor fit needs `_scaled_lstsq`.** The Im(Z) design columns `omega` and `-1/omega` differ by ~1e20 in magnitude; raw `np.linalg.lstsq` kills the small singular value and reports `C=1e41`. The column-rescaling helper in `pkg_rlc_core.py` is load-bearing -- don't remove it.
- **SI suffix `M` is Mega (1e6), not milli.** Milli is lowercase `m`. Used in Custom Mode lumped-value parsing and exposed in Help → Input syntax.
- **Mode 3 short-group syntax.** `1-2-3-4` is a single group of 4 ports tied together (parser emits chained binary pairs `(1,2),(2,3),(3,4)` which Union-Find inside `compute_z` merges). Don't simplify the parser into "exactly two ports per group".

### `reduce_snp.py` specifics

- **Standalone, no repo imports.** It runs from a scratch directory on a sim server. numpy + stdlib only. Duplicating the Touchstone parser here is intentional, not an oversight — keep the n=2 column-order quirk mirrored on both sides.
- **Three port buckets, not two.** KEEP becomes an output port; a group named `GND`/`GROUND`/`SHORT` is shorted to the reference node (**delete that row and column in Y**, because V=0); everything unlisted is Schur-eliminated. Grounding is *not* the same as opening — PKG ground balls need the GND group or the result is wrong.
- **`--method matched` with no GND ports == plain S sub-matrix.** Proven in `test_matched_equals_submatrix`; the code takes the sub-matrix fast path there. Terminating in Z0 == adding `Y0=1/z0` to the unused diagonal before elimination.
- **Do NOT use `np.fromstring(sep=' ')` in the parser.** It is ~9x *slower* than `float()` on numpy 2.x and truncates silently on a bad token. The `array.array('d')` + bounded staging list + `np.frombuffer` view is the measured optimum (2.5x less peak memory than a list-of-floats for +16% time).
- **Build `s` via `s.real = ... / s.imag = ...`,** never `raw[...,0] + 1j*raw[...,1]` — the latter allocates two full-size complex temporaries and doubles peak memory on multi-GB files.
- **Output defaults to `RI` with 12 significant digits.** DB output loses precision on small entries; on a 4-port fixture this default is ~300x more accurate than the old `DB`/`%.10g` combination.
- **Frequency-batched everywhere** (`--batch`, default 256) so a 153-port file doesn't materialise every Y matrix at once. `s_to_y`/`y_to_s`/Schur all operate on stacked `(F,N,N)` arrays via `np.linalg.solve`, with a per-frequency `lstsq` fallback in `_solve_batch`.

## How to run tests

```bash
python -m unittest discover -s tests
```

## How to add a new measurement mode

1. **Core**: add a `build_terminations_modeN(...)` helper in `pkg_rlc_core.py` that produces a `TerminationSet`. If a new termination semantic is needed, add a dataclass to the `PortTermination` / `Coupling` unions and handle it in `compute_z`'s evaluation order (lumped -> short -> ground -> Schur).
2. **GUI**: add a new radio button in `_build_editor`, register placeholder hints in `MODE_PLACEHOLDERS`, extend `_update_mode_visibility` to show/hide and re-set placeholders, and dispatch in `_build_termination`. Mirror the dispatch in the CLI argparser.
3. **Help**: add a new tab to `HELP_TOPICS` in `pkg_rlc_help.py` with assumptions, inputs, and a worked example. Update the `Mode 5 (Custom)` tab if the new mode could also be expressed in DSL.
4. **Tests**: add a case in `test_core.py` that builds the new termination set and asserts `compute_z` matches a hand-coded reference. Also add an "equivalence test" pinning that the new named mode produces identical results to a hand-built `TerminationSet`.

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
