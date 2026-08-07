# CLAUDE.md — PKG RLC Extractor

Conventions for Claude Code sessions on this repo. The authoritative spec is `CLAUDE_CODE_PROMPT.md`; the user docs are `README.md` and `docs/theory.md`.

## Project purpose

Tkinter + Matplotlib desktop tool that extracts R, L, C, Q from Touchstone files via Y-parameter Schur-complement reduction — and, with more than one measurement port defined, the mutual coupling between them (M, k, M/L, C_c). Used for IC packages, EMX layout traces, DCO inductors, decap, and inductor-to-inductor pulling / spur budgeting.

## Module map

| File                    | Responsibility                                                                  |
|-------------------------|---------------------------------------------------------------------------------|
| `pkg_rlc_core.py`       | Touchstone parser, S<->Y, unified `TerminationSet` model + the Mode 5 DSL (`parse_custom_termination_text`, `parse_si`, `parse_kv_rlc_params`, `SI_SUFFIXES`), the connection-table row model (`MeasPortRow`, `ConnectionRow`, `rows_to_dsl_text`, `dsl_text_to_rows`, `build_terminations_rows`), `parse_mport_spec`, `resolve_meas_ports`, `compute_z_matrix` / `compute_z`, `extract_rlc_at_freq` / `extract_coupling_at_freq`, `fit_inductor` / `fit_capacitor` / `fit_auto`. |
| `pkg_rlc_plot.py`       | Matplotlib plot panel: multi-subplot grid over R/L/C/\|Z\|/Re/Im/Q/**k**, draggable freq marker, M / V / Delete keys, fullscreen window. Quantities that cannot be derived from one `(freqs, Z)` pair (today only `k`) arrive via the optional `Trace.aux` dict. |
| `pkg_rlc_gui.py`        | Tkinter GUI: file management, trace management, mode-aware editor with `PlaceholderEntry` / `PlaceholderText` hints and the `RowTable` / `ColumnSpec` row editor, results pane. Re-exports the DSL helpers it no longer defines. |
| `pkg_rlc_help.py`       | In-app Help window content (`HELP_TOPICS`, `HelpWindow`). One tab per mode + syntax + worked examples. |
| `pkg_rlc_extractor.py`  | Entry point: dispatches GUI vs CLI from argv. CLI `--mode gnd \| p2p \| coupling`, `--mport` repeatable. |
| `reduce_snp.py`         | **Standalone** CLI: shrinks a big `.sNp` to a few ports (KEEP / GND-short / open-or-matched elimination). Deliberately imports nothing from this repo — it gets copied to simulation servers on its own. |
| `deploy.sh`             | **Top level on purpose.** Red-zone update entry point: `cd <install> && bash deploy.sh` auto-detects the uploaded tarball. The operator's cross-project convention is `<install>/deploy.sh` — do not move it back under `deploy/`. |
| `deploy/`               | Rest of the air-gapped ("red zone") pipeline: `pack.ps1` (Windows, `git archive`), `doctor.sh` + `_env_check.py` (what can this box run?). No network, no pip, no venv on the far side. |
| `tests/`                | `unittest`-based suite (252 tests covering parser line-break/signed-zero edge cases, port range, mport specs, short groups, content sniffer, terminations, termination precedence, the connection-row model, the `RowTable` widget, fits, Schur fallback, the coupling matrix, degenerate probes, the bit-exact golden regression, and `reduce_snp`). |
| `tests/test_connection_rows.py` | Row model: rows<->DSL round trip, and the equivalence tests pinning that rows reproduce `build_terminations_mode1/2/3` *including* the ground-wins overlap the golden reference cannot see. |
| `tests/test_row_table.py` | Drives real Tk widgets (skips cleanly with no display): `RowTable` add/delete/get/set, the `mp1_*`->`mports` migration, and that Duplicate does not share the row list. |
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

Design note: `docs/design_connection_table.md`. Stages 0-2 are done; stages 3-4
(the full Mode 5 editor, and modes reframed as presets) are specified there and
deliberately unstarted — they need a human looking at the screen.

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
- **A table cell cannot hold a placeholder hint, so the hint is a permanent label under
  the table.** `PlaceholderEntry` / `PlaceholderText` delete their hint on `<FocusIn>` —
  that deletion is the mechanical reason nobody could remember the syntax. Do not
  "restore" per-cell placeholders.

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
- **The single-file `reduce_snp_<hash>.py` is extracted via `cmd.exe` redirection
  of `git cat-file blob`**, not PowerShell capture — PowerShell re-encodes the
  stream and would turn LF into CRLF, desyncing it from the copy in the tarball.
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
2. **GUI**: add a new radio button in `_build_editor`, add the fields to `TraceConfig`, register placeholder hints in `MODE_PLACEHOLDERS`, extend `_update_mode_visibility` to show/hide and re-set placeholders, extend `_port_descriptor`, and dispatch in `_build_termination`. Mirror the dispatch in the CLI argparser (`_make_arg_parser` + `_run_cli`) and reject flags that belong to other modes with a clear message.
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
