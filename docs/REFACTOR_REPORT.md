# The overnight refactor — morning report

One account of the night, rewritten in place rather than appended to. It covers
four sequential agent runs, 2026-08-13 20:03 → 2026-08-14 04:54. Every
before/after below is measured against `3ae2dfd` ("Show the whole trace
name…"), the last commit before the refactor started, and describes the tree at
`849decb`, which is HEAD as this was written.

**Three things are worse this morning than they were last night, and they are
the price of the rest.** There is more Python in the tree, not less: **+2 524
lines** outside `pkg_rlc/present/help.py`, **+7.2%**. There are **26 modules** to
navigate instead of 10. `CLAUDE.md` is **937 lines** longer. The win is in
where the code sits, not in how much of it there is, and §2 states both halves
with the measurements.

---

## 1. Verdict

**The tool works and the suite is green.** Measured on this box at HEAD:
`python tests/run_parallel.py -j 4` → **2 536 tests, 454 shards, 363.1 s, exit
0**; `--fast` → **1 044 tests, 4.5 s, OK**. All 25 `pkg_rlc_*` modules import.
The CLI was run end to end on the coupling path and its output was read (it is
the `@ 0.10099 GHz  (requested 0.11 GHz; nearest point, grid step 25 MHz)` line
quoted in §4.1, confirmed on the real fixture rather than from a test). The GUI
was driven end to end — file added through `_on_add_file`, Calculate, session
saved and reloaded, closed cleanly — by run three, an hour before this
rewrite; nothing has touched a `.py` file since.

**`golden_legacy.npz` and `render_reference.json` were not touched at any point
in any of the four runs** — `git log 3ae2dfd..HEAD` lists **0 commits** against
either.

**One shipped surface changed behaviour: the CLI's frequency line.** It printed
a frequency that does not exist in the file (`@ 0.101 GHz` for a point at
100 990 000 Hz) with no snap note anywhere on the coupling path. That was a
real defect, it was fixed, and it is the first thing to look at — §4.1.

**One shipped surface had a real ordering bug and it is also fixed.** The
Attribution window sorted a row that could not be measured **above** the
strongest real effect in the sensitivity table, contradicting the CLI, core's
own rule and the same file twelve hundred lines up — §4.2.

**The structural work is in, including the piece that was reverted at 01:38.**
`pkg_rlc.model.trace` (L1) exists and carries the data model; `pkg_rlc.services.session` and
`pkg_rlc.services.run` (L2) landed on top of it; nine of the ten function-level
back-imports into `pkg_rlc.frontend.app` are gone. §3 says what that cost and what it
still does not cover.

---

## 2. What changed

### Modules

Every number in this section was measured at HEAD and at `3ae2dfd` while
writing this section, not carried forward from an earlier draft.

| module | before | after | note |
|---|---:|---:|---|
| `pkg_rlc/frontend/app.py` | 10 954 | **3 220** | **−71%.** Twelve commits took code out of it: four panels (`files`, `traces`, `results`, `editor`) and eight libraries (`conntable`, `report`, `csv`, `widgets`, `validate`, `model`, `session`, `run`). |
| `pkg_rlc/physics/core.py` | 4 726 | **169** | Now a re-export facade over touchstone / spec / solve. |
| `pkg_rlc_extractor.py` | 4 377 | **3 060** | 13 report sections + 6 duplicated formatters left. |
| `pkg_rlc/present/help.py` | 2 745 | **133** | Prose moved to `docs/help/*.md` (10 files, 2 648 lines). |
| `pkg_rlc/widgets/plot.py` | 1 194 | **1 028** | Generic widgets (incl. `ReflowRow`) went to `pkg_rlc.widgets.widgets`. |
| `pkg_rlc/panels/attrib_gui.py` | 4 439 | **4 479** | **+40.** Grew: the ground-model import, and the sensitivity fix with its docstring. |
| `pkg_rlc/panels/files_gui.py` | 1 487 | **1 501** | **+14.** Grew: eight lazy imports became top-level ones, with the reason written beside them. |
| `pkg_rlc/physics/attrib.py` | 4 767 | **4 767** | Untouched all night. |
| `pkg_rlc/physics/compose.py` | 1 861 | **1 861** | Untouched. |
| `reduce_snp.py` | 1 224 | **1 224** | Untouched (standalone by design). |

New modules, none of which existed at 20:03:

| new module | lines | layer | what it is |
|---|---:|---|---|
| `pkg_rlc/physics/spec.py` | 2 050 | L0 | The declaration model and the Mode 5 DSL. |
| `pkg_rlc/present/report.py` | 1 596 | L3 | Turning a run into text. |
| `pkg_rlc/panels/panels_editor.py` | 1 644 | L5 | The editor panel + `StylePicker`. |
| `pkg_rlc/physics/touchstone.py` | 1 563 | L0 | The parser and its diagnostics. |
| `pkg_rlc/present/attrib_report.py` | 1 557 | L3 | The CLI attribution report, as `list[str]`. |
| `pkg_rlc/model/validate.py` | 1 351 | L1 | What a spec says, does and gets wrong. |
| `pkg_rlc/physics/solve.py` | 1 218 | L0 | `compute_z_matrix` and the reduction. |
| `pkg_rlc/panels/panels_results.py` | 1 115 | L5 | The Results notebook and the run pages. |
| `pkg_rlc/widgets/widgets.py` | 992 | L4 | Generic Tk widgets + the palette. |
| **`pkg_rlc/model/trace.py`** | **975** | **L1** | **The shared data model — §3.1.** |
| `pkg_rlc/present/conntable.py` | 505 | L3 | The connections-table layout vocabulary. |
| `pkg_rlc/services/session.py` | 465 | L2 | Save / Load / autosave, as a dict ↔ model round trip. |
| `pkg_rlc/services/run.py` | 439 | L2 | The arithmetic half of Calculate. |
| `pkg_rlc/panels/panels_traces.py` | 388 | L5 | The Traces section + the freeze entries. |
| `pkg_rlc/panels/panels_files.py` | 273 | L5 | The Files section. |
| `pkg_rlc/present/csv.py` | 113 | L3 | The CSV blocks. |

**Totals: 10 modules / 37 774 lines → 26 modules / 37 686 lines**, counting the
standalone `reduce_snp.py` on both sides.

That headline near-parity is entirely the Help prose leaving Python. Excluding
`pkg_rlc/present/help.py` the Python grew: **35 029 → 37 553, i.e. +2 524 lines
(+7.2%)**. Module docstrings, imports and the re-export blocks rule 2 requires
are what that buys. `CLAUDE.md` went **3 498 → 4 435 (+937)**.

### The import layering

| | at 20:03 | at HEAD |
|---|---:|---:|
| function-level `import pkg_rlc.frontend.app` dodges | **10** | **1** |
| modules named in `test_layering.LAYERS` that do not exist | 3 (from 00:37) | **0** |
| `pkg_rlc_*.py` on disk not declared in the layer map | 0 | **0** |

The one remaining back-import is `pkg_rlc_extractor` → `App`, deferred so that
`--cli` does not pay the tkinter + matplotlib import. Re-measured for this
rewrite over three fresh processes: `import pkg_rlc_extractor` is 98 / 109 /
104 ms, and `import pkg_rlc.frontend.app` on top of it adds **245 / 250 / 247 ms**. That
is a justified deferral, not a dodge, and `tests/test_layering.py` pins it in
both directions: adding an eleventh fails, and removing this one fails too
until `KNOWN_BACK_IMPORTS` moves in the same commit.

### Tests

| | before | after |
|---|---:|---:|
| `def test_` methods, counted statically | **2 444** (41 modules) | **2 536** (44 modules) |
| full suite, run | — | **2 536 / 454 shards / 363.1 s** at `-j 4`, exit 0 |
| `--fast` | 976 / 5.5 s (recorded in `CLAUDE.md`) | **1 044 / 4.5 s** (measured) |
| test modules touched | — | **8 of 44**, of which **3 are new** |

The 2 444 figure is the same static count applied at the base commit, so the
two ends are comparable, and the runner's own count agrees with it exactly at
HEAD. `CLAUDE.md`'s recorded baseline of "2045" was already stale before the
night started.

**+92 tests over the night, and every one of them is accounted for.** The three
NEW modules are `test_cli_golden` 37, `test_layering` 21, `test_attrib_golden`
12 — **70**. The five MODIFIED ones are `test_freq_label` 64 → 73 (guards for
the CLI frequency fix), `test_run_parallel` 57 → 65 (the runner's own priority
work), `test_attrib_window` 212 → 217 (the sensitivity fix), and then
`test_attrib_gui_integration` and `test_multifile_table`, both unchanged in
count at 70 and 100 — the second of those was opened only to move a
`mock.patch` target onto the edge it is testing, because the lazy import it
used to patch through no longer exists. `70 + 9 + 8 + 5 = 92`.

**36 of 44 test modules were never opened**, which is the re-export rule
(rule 2) working and the best single piece of evidence that the moves were
pure.

### Golden references

| reference | commits since `3ae2dfd` | detail |
|---|---:|---|
| `tests/fixtures/golden_legacy.npz` | **0** | Never moved. |
| `tests/fixtures/render_reference.json` | **0** | Never moved. |
| `tests/fixtures/cli_reference/` (144 cases) | **2** | Created; then modified once by the frequency fix — 55 files, verified in §4.1. |
| `tests/fixtures/attrib_reference/` (60 cases) | **2** | Created; then modified once by the sensitivity fix — one case ADDED, verified in §4.2. |

### "Keep in sync" comments

The plan's figure of 95 could not be reproduced with any pattern at either end,
so there is no before/after on it. What the R4 phase (`fc8feb8`) did measure,
with the pattern in the commit message so it can be repeated —
`keep … in sync|step|mirrored|aligned` over every `.py` and `.md` — is **19**,
classified one by one: **11** state that there is *no* second copy and why
(true, verified); **5** describe duplication that is deliberate and still there
(`reduce_snp`'s own parser, `.gitattributes` + `pack.ps1`,
`files_gui.trace_file_labels`); **2** are false matches on the word "step";
**1** was stale and was corrected.

**No comment was deleted from any source file.** The waves that merged the
duplicate code had already updated their own comments as they went. Counted
again for this rewrite over `*.py` + `*.md` + `docs/*.md` + `tests/*.py`, base
→ HEAD: `mirror` 105 → 107, `same rule` 77 → 93, `in sync` 5 → 10. The count
went **up**, because the new modules document their own boundaries.

---

## 3. What landed below the frontend, and the lesson of how it nearly did not

### 3.1 `pkg_rlc.model.trace` — what is actually on disk

`pkg_rlc/model/trace.py` is in the tree, **975 lines**, at L1. It carries:

* `FileEntry`, `TraceConfig`, `SolveNetwork` and `_composed_solve_network`;
* the signature family — `_duplicate_trace_config`, `_config_signature`,
  `_draw_signature`, `trace_signature_fields`, `run_signatures`;
* `VIEW_DETAIL` / `VIEW_SUMMARY` / `VIEW_COMPARE` / `RESULTS_VIEWS`;
* `LOG_INFO` / `LOG_WARN` / `LOG_ERROR`;
* the frequency snap — `FreqSnap`, `freq_grid_step`, `snap_to_grid`,
  `combine_freq_snaps` and the three `FREQ_*` tolerances;
* the whole run record — `RowSnapshot`, `CouplingSnapshot`, `FitSnapshot`,
  `RunSnapshot` and the `_snapshot_*` builders.

It imports `pkg_rlc.physics.core` and `pkg_rlc.model.validate` and nothing else. No Tk, no
matplotlib, no `App`.

### 3.2 It was built, tested, and thrown away by the orchestration script

**This is the most useful thing in this file for whoever runs the next one.**

It landed at 00:52–01:18 in three commits (`a139a58`, `cf7d832`, `98ddb00`) and
was reverted **whole** at 01:38 (`1bb2f35`, `3e26ed1`, `c0d3103`). **None of
the three revert messages names a failing test, a broken behaviour, or a rule
the work violated.** The stated reason was that the phase was being undone
whole "so the tree is coherent in the morning".

That revert was the **orchestration script's** mistake, not the agent's. The
script applied one blunt rule — *any missing deliverable ⇒ revert the phase* —
and that rule cannot tell **"produced nothing"** from **"produced four fifths,
tested it, and wrote down precisely which fifth it did not do and why"**. The
agent had done the second. What was discarded was a module that passed
everything it was pointed at: run two checked the reverted state empirically
before writing this report's first draft, rebuilding a detached worktree at
`cf7d832` and measuring `--fast` 1 044 OK and the five most-affected Tk modules
(`layering`, `session`, `freeze_trace`, `run_snapshot`, `multifile*`) 391 OK.
The full suite at HEAD, with that work restored and two more modules built on
top of it, is the 2 536 in §1.

Recovery cost three `git revert`s (`75cee65`, `811f514`, `91be95d` at 02:49).
**A phase gate that reverts on a missing deliverable needs a second question:
did the phase say what it did not do, and is that account specific enough to
finish from?** Here it was, and an hour of good work was thrown away anyway.

### 3.3 The two blockers it stopped on, and how they were settled

Both were real, both were documented by the agent that hit them, and both were
resolved by the run after it — each of them by moving a symbol rather than by
adding an exception:

1. **`CouplingSnapshot.freq` is a `FreqSnap`, and `FreqSnap` was at L3.** A
   model type holding a presentation type is an upward edge. Settled by moving
   `FreqSnap` / `freq_grid_step` / `snap_to_grid` / `combine_freq_snaps` **down
   into the model** (`60b7e3e`): where a measurement landed is a fact about the
   measurement, and the dataclass carries `delta_hz` / `exact` / `off_grid` and
   nothing about how to print them. `marker_freq_text` stayed at L3 — it takes
   a format string and returns a sentence, which is a rendering.
2. **`_snapshot_reference` calls `reference_provenance`, which is L5.**
   Rendering the reference-node verdict once at snapshot time is deliberate
   (R3-5: two copies of one verdict are two things that can come to disagree),
   so the call could not simply be deleted. Settled by **injection**
   (`fe0ff58`): `_snapshot_reference(tc, *, provenance=None)`, with a
   three-line wrapper in `pkg_rlc.frontend.app` supplying `reference_provenance`. The
   render stays at L5, the model stores text it was handed, nothing at L1 names
   an L5 module, and **no call site and no test moved**. With no renderer
   supplied the three fields are empty — the same answer a single-file trace
   gets, which is what keeps `render_reference.json` byte-identical.

Two more symbol groups moved down for the same class of reason, each written up
in `CLAUDE.md`: the three `VIEW_*` names plus `RESULTS_VIEWS` (`e25cb09`) —
`results_view` is a SAVED setting, so the session file at L2 has to validate it
while the renderer at L3 acts on it, and a vocabulary shared between a format
and a renderer belongs at or below the lower of the two — and `LOG_INFO` /
`LOG_WARN` / `LOG_ERROR`, because a severity is a property of the MESSAGE, not
of the pane, which is the repo's own rule already written in those words.
`LOG_BADGE_CAP` and `log_tab_label` stayed at L3.

### 3.4 What the model does NOT carry, and it is not an oversight yet

The **freeze family** — `FREEZE_STAMP_FMT`, `freeze_label`, `_freeze_stamp_of`,
`freeze_refusal`, `_freeze_trace_config` — is still in `pkg_rlc.frontend.app` at L6,
even though every one of those is a pure function over a `TraceConfig` with no
Tk in it. It is reachable by panels only through the `App` alias block, which
is the same shape the model phase exists to remove. §6 carries it as owed work.

### 3.5 The two service modules

| module | lines | what landed |
|---|---:|---|
| `pkg_rlc/services/session.py` | 465 | Save Config / Load Config / autosave as a pure dict ↔ model round trip: `session_to_dict`, `session_from_dict`, `trace_to_dict`, `trace_from_dict`, `resolve_session_file`, `SessionError`, `LoadedSession`. A verbatim move; there was never any Tk in it. `pkg_rlc.frontend.app` keeps the file dialogs, reading the widgets into a `controls` dict, and applying a `LoadedSession` onto live traces. |
| `pkg_rlc/services/run.py` | 439 | What a Calculate RUNS: `_trace_network`, `_cached_trace_network`, `_trace_namespace`, `_build_termination`, `_collect_mports`, `_reference_checks`, `_calculate_coupling_trace`, `_trace_plot_freqs`, `_empty_run`. `log` / `files` / `cache` are injected rather than reached for. |

**What did not land, and it is one thing: `_on_calculate`'s own body** (386
lines, still in `pkg_rlc.frontend.app`). The plan asked for
`calculate(traces, files, controls, log, cache, only)`. The solve underneath it
moved; the orchestration did not, and the reason is measured rather than
asserted. After the solve came out, the body's remaining couplings above L2 are
exactly **five**, and every one is presentation or an App action:
`marker_freq_text` (L3), `describe_run_change` (L3), `reference_provenance`
(L5, via the snapshot wrappers), `UNFREEZE_MENU_LABEL` (L5) and `_migrate_trace`
(which logs a line and refreshes the Traces list). A `calculate()` at L2 would
therefore take **three injected callables and one injected string, and hand the
header line and the run-to-run diff back to its caller** — i.e. the report's
ORDER split across two modules, which is the "two things that can come to
disagree" failure this repo names everywhere else, arriving inside the fix for
it. The split that IS honest is now named in `CLAUDE.md`: `pkg_rlc.services.run` answers
*what is the number*, `_on_calculate` answers *what does the reader see, in
what order, at what severity*.

**One behaviour difference was found while doing this and restored rather than
shipped.** `run._build_termination` deliberately does not migrate a legacy
spec (migrating logs a line and refreshes the Traces list, so it is an App
action), and `_calculate_coupling_trace` builds its own termination when the
caller passes none — so on that one rarely-taken path a mode-4 trace would have
been read unmigrated, silently. `App._calculate_coupling_trace` now migrates
first on exactly that condition (`a92d360`).

---

## 4. Findings about the product

These are not refactor mechanics. They are places where the CLI and the GUI
have been telling users different things about the same data. Comparing the two
surfaces side by side is something nobody had done before, because until
tonight the CLI's output existed only as `print` calls to fd 1 with no way to
capture it; `pkg_rlc.present.attrib_report` returning `list[str]` and
`tests/fixtures/cli_reference/` are what made the comparison possible at all.

Seven divergences were found. **Two are fixed and five are open**, and the
rule that decided which is which is now in `CLAUDE.md`:

> Where the two surfaces disagree **and the repo already has a documented
> position** (a `CLAUDE.md` entry, a test file's stated purpose,
> `docs/theory.md`), the surface matching that position wins and the other is
> fixed. Where there is no documented position, it is a genuine product choice
> and it is left for the user.

### 4.1 FIXED — the CLI printed a frequency that is not in the file

The two spellings, which had sat side by side unnoticed:

```python
# CLI   _print_coupling_report
f"@ {res.freq_hz / 1e9:.4g} GHz  --  Z matrix ..."

# GUI   _format_coupling_block
f"Z matrix @ {marker_freq_text(freq, '{:.6g}')} ..."
```

`tests/fixtures/diff_pair_4port.s4p` is 401 points from 1 MHz to 10 GHz, so the
grid step is 24.9975 MHz and the points near the default marker are
100 990 000 Hz and 125 987 500 Hz. Ask for `--freq 0.11`:

```
BEFORE   @ 0.101 GHz  --  Z matrix (Ω), open-circuit: ...
AFTER    @ 0.10099 GHz  (requested 0.11 GHz; nearest point, grid step 25 MHz)  --  Z matrix (Ω), ...
```

`format(0.10099, '.4g')` is `'0.101'`. **0.101 GHz is 101 MHz, and there is no
such point in the file.** The GUI, on the same data, said
`Z matrix @ 0.10099 GHz`. And the coupling path carried no snap note at all:
`snapped to …` existed only under `--attribute` and `--cold-start`, so that
rounded number was the CLI's *only* statement of where the numbers came from.
The same defect was on the scalar `@ <f>:` line above the R/L/C/Q table.

**The documented position that settled it.** `tests/test_freq_label.py` exists
for exactly this, and its stated purpose is quoted in `CLAUDE.md`: *"the marker
frequency a report prints says where the numbers came from"*. Its own docstring
records the original bug — a user reading `@ 5.6 GHz` and `@ 5.512 GHz` in one
report with nothing to reconcile them. The GUI was fixed then; **the CLI was
simply missed.** Not a choice between two defensible spellings.

**Commits.** `b7f1ddd` (the code + 9 new guards in `test_freq_label.py`),
`181cd66` (the reference), `8a1896b` (`CLAUDE.md`).

**The reference diff, verified independently rather than taken on trust:**

```
changed lines in tests/fixtures/cli_reference/ :  110  (55 files, 55 +/55 −)
changed lines that are NOT a "@ <freq>" marker  :    0
distinct shapes of changed line                 :    2
    "@ N GHz  (requested N GHz; nearest point, grid step N MHz)  --  Z matrix ..."
    "@ N GHz  (requested N GHz; nearest point, grid step N MHz):"
```

No number moved, no table moved, no exit code moved, no CSV cell moved.
`golden_legacy.npz` and `render_reference.json` did not move at all — the shape
it had to have, because the GUI was already right.

One deliberate asymmetry worth knowing about: **precision is per line and is
not uniform.** The coupling line takes the pane's `{:.6g}` (at `{:.4g}` an
exact 5.0005 GHz point renders as `5 GHz`); the scalar line keeps its
historical `{:.4g}`. `marker_freq_text` widens *both* numbers itself the moment
there are two to tell apart, so a marker that **is** a data point renders
byte-for-byte what it always did at both sites.

### 4.2 FIXED — the Attribution window ranked an unmeasurable row first

`pkg_rlc.panels.attrib_gui.sensitivity_table` and the CLI's
`_attr_print_sensitivity` sort the same list of `SensitivityResult` and sorted
an UNDEFINED delta to **opposite ends of the table**:

```python
# CLI   _attr_print_sensitivity          -- undefined LAST
key=lambda r: (0 if math.isfinite(r.abs_delta) else 1, -r.abs_delta ...)

# WINDOW  sensitivity_table  (BEFORE)    -- undefined FIRST
key=lambda r: (-r.abs_delta if math.isfinite(r.abs_delta) else float("-inf"))
```

`-inf` is the smallest key on an ascending sort, so **a row that measured
nothing printed above the strongest real effect** — on the surface a user reads
before deciding which port to go and fix.

**The documented position, and there were three of them.** `rank_coupling_pairs`
in core states it outright — *NaN is a missing measurement, not a small number*;
`_attr_print_sensitivity`'s docstring says the same; and `_fold_terms`, twelve
hundred lines up **in the same file as the defect**, keys the identical case at
`+inf` and carries a comment explaining why. The window contradicted not only
the other two surfaces but itself. The key is now spelled exactly as the CLI
spells it, so the two read alike to anyone comparing them.

**Commits.** `8d98ba9` (the fix + 5 guards in `test_attrib_window.py`),
`93326a6` (the reference case), `98ee544` (`CLAUDE.md` states the rule once for
all four implementations, including the non-obvious half: keying a NaN at `0.0`
instead of `+inf` ties it with a legitimately inert element, because `-0.0 ==
0.0`, and a stable sort then floats the missing measurement to the top again).

**Three things the fix turned up that the recommendation did not anticipate.**

1. **The reference did not need regenerating for the fix, and could not have
   seen it.** All four captured sensitivity cases in `attrib_reference` hold
   finite deltas — every candidate on every fixture in this repo returns one —
   so the ordering move was structurally invisible to the golden reference.
   **That is how the bug survived a golden-referenced refactor in the first
   place**, and it is worth more than the bug: a reference proves only what its
   captures can express.
2. **The case that closes that hole had to be hand-built**, for the same reason
   `fake_nan_and_inf` is: a probe with no return path is ordinary in the field
   and no `.sNp` here produces one through this path.
   `sensitivity_fake_undefined_delta` has two undefined candidates among three
   measured, which also pins that they sort last **among themselves** in
   declaration order — the stable sort doing its job.
3. **The guard pins the window against the CLI directly**, rather than each
   against a literal. That is the shape the divergence would have to take to
   come back.

**The attrib_reference diff, verified line by line:** 59 cases → **60**, one
entry added to `manifest.json` (7 insertions) plus one new 7-line capture, and
**not one of the 59 existing entries changed — no `sha256`, no `chars`, no
`lines`.** `golden_legacy.npz`, `render_reference.json` and `cli_reference` are
untouched; this was a GUI-only fix.

**The guard was mutation-checked twice, and the second time was for this
report.** Putting the old `float("-inf")` key back: `test_attrib_golden` goes
from 12 passed to **1 failure** and `TestSensitivityRanking` from 5 passed to
**2 failures**, the second reporting
`['strong', 'middling', 'weak', 'undefined a', 'undefined b']` against the
order it demands. Restored from a copy; `git status` clean and all 17 green
again.

### 4.3 The five that are open

None of these five has a documented position in the repo, so by the rule above
they are product choices and were left for you. Each keeps its recommendation.

**Between the CLI report and the results pane:**

| # | divergence | recommendation |
|---|---|---|
| 2 | **The CLI does not rank or floor the coupling pair list.** Still `for pr in res.pairs:` — nested-loop `(a,b)` order, every pair, no `rank_coupling_pairs`, no `COUPLING_FLOOR_DB`, and `worst M/L` (the rank key itself) is not printed at all. | *Move the CLI to the pane's answer.* The reason the pane was changed applies verbatim — six measurement ports make 15 pairs and index order says nothing about which matter. **This is the one of the five I would actually change.** |
| 3 | **Reciprocity is a METRIC on the CLI and a VERDICT in the pane.** CLI: `Reciprocity error = 5.76e-15  (max\|Z_ab − Z_ba\| / max\|Z_ab\| …)` plus a paragraph. Pane: `✓ reciprocal (5.76e-15)`. | *Leave.* Defensible as-is — a terminal has no 144-column budget, and the pane's slimming was driven by that budget. But they are now different products. |
| 4 | **The CLI's legend is per-block, differently worded, and has no `\|k\|>1` prompt.** `_pair_flag` is pane-only, so a CLI user whose `\|k\|` exceeds 1 gets no "check the port setup" prompt. The pane emits `COUPLING_LEGEND_LINES` once per run; the CLI repeats its own wording under every block. | *Port `_pair_flag` to the CLI.* The missing `\|k\|>1` prompt is a real loss of a diagnostic, unlike the wording and the repetition. |

**Between the CLI attribution report and the Attribution window:**

| # | divergence | recommendation |
|---|---|---|
| 6 | **Two `_e`, at two precisions.** `pkg_rlc.present.attrib_report._e` writes `%.6e`; `pkg_rlc.panels.attrib_gui._e` writes `%.12e`. Both put a float from the same decomposition into a CSV. | *Pick one, probably `%.12e`* — a CSV is for re-use and 6 digits is lossy. Low urgency. |
| 7 | **Two candidate grammars.** `--attribute-alt` splits on **comma** (`R=0.5,L=1n`, via `y_series_rlc`); the window's Candidates field splits on **whitespace** (`R=0.5 L=1n`, building `R + jwL + 1/(jwC)` directly). Both refuse a token with no `=` for the same measured `R=5 m` reason, but the two expressions are not obliged to agree at `omega == 0`. | *Accept both separators on both sides.* This is a user-facing trap — the spelling that works in one place silently fails in the other. |

### 4.4 Duplication that was removed before it could diverge

Not user-visible today, but each was two implementations of one answer, i.e.
two things that can come to disagree about a number:

* **Six formatters** the CLI had its own copy of (truncation, plain number,
  sign flag, monospace table, Z matrix, and — under the same name on both
  sides — the coupling CSV). Collapsed onto the shared ones with exactly three
  parameters (`ell` / `rule` / `name`+`cell`), each defaulting to the pane's
  behaviour, because both surfaces are now pinned byte for byte.
* **The two coupling CSV writers were arrived at independently and agreed
  anyway** — numpy-vectorised `k` in the GUI, per-frequency `math.sqrt` with
  `isfinite` guards in the CLI, same columns, same order, same `%.6e`. They
  were checked branch by branch (`omega == 0`, `L <= 0`, non-finite `M`, `-0.0`)
  before being merged. That they agreed was luck.
* **`rank_map`** — "which element ranks where at this frequency" — was written
  twice, identically, in the CLI's section 8 and in the window's stability
  badge. Now one function.
* **`_attr_snap`** — the window had the grid-snap `argmin` written out inline.
  Two surfaces landing on different grid points is precisely the failure the
  snap exists to prevent.
* **The ground-model grammar** (`parse_ground_model` / `ground_model_zt`) —
  reached by the window through a lazy `import pkg_rlc_extractor` inside a
  function body, purely to dodge an import cycle. Now a normal top-level
  import. This one matters: the choice it parses is worth a measured 9.60 dB.

---

## 5. What a human should check first

1. **Rule on the five open divergences (§4.3).** They are the only items in
   this whole night genuinely waiting on you: each is a product choice with no
   documented position in the repo, so none was decided without you.
   * **#2, the unranked CLI coupling list** — the one I would change.
   * **#7, the two candidate grammars** — a user-facing trap; the spelling that
     works in one place silently fails in the other.
   * **#4, the missing `|k|>1` prompt on the CLI** — a lost diagnostic, worth
     porting even if the wording stays different.
   * **#6, `%.6e` vs `%.12e` in two CSV writers** — low urgency, pick one.
   * **#3, reciprocity metric vs verdict** — recommend leaving it, but the two
     surfaces are now different products and you should know that.
2. **Sanity-check the CLI frequency line against your own data.** Run a
   coupling extraction at a marker that is *not* a grid point and confirm the
   `(requested …; nearest point, grid step …)` note reads correctly, then run
   one at an exact grid point and confirm it renders exactly as it used to.
   This and the sensitivity ordering are the only shipped behaviour changes of
   the night.
3. **Open the GUI and do one real extraction end to end** — load a file,
   Calculate, read the results pane, Export CSV, open the Attribution window
   and look at the sensitivity table's ordering. The suite drives all of this,
   but 87% of it is Tk geometry and none of it is you looking at the screen.
4. **Read the new sections of `CLAUDE.md`** — the layering gate, the four
   panels, the two attribution reports, the one-formatter-two-spellings
   section, the model / session / run modules, and the undefined-sorts-last
   rule. If any of them describes something you did not agree to, this is the
   cheapest moment to say so.
5. **Decide whether the phase-gate lesson in §3.2 is worth encoding** before the
   next long unattended run. It cost an hour tonight and it will cost more than
   that eventually.

---

## 6. What is still owed

**In the tree and finished:** `pkg_rlc.model.trace` (L1), `pkg_rlc.services.session` (L2),
`pkg_rlc.services.run` (L2). Nothing from the plan is now blocked on a missing module.

**Not done, in descending order of how much it would buy:**

* **`_on_calculate` itself** — 386 lines at L6, five couplings above L2, all
  presentation or App actions. §3.5 measures them and argues that moving it as
  it stands would split one report's ORDER across two modules. It is the one
  place where the plan and the measurement disagree, so it is left as a
  documented decision rather than a gap.
* **The freeze family** — `FREEZE_STAMP_FMT`, `freeze_label`,
  `_freeze_stamp_of`, `freeze_refusal`, `_freeze_trace_config` are pure
  functions over a `TraceConfig` and are still at L6.
* **`App`'s 7-alias `staticmethod` block** (`_duplicate_trace_config`,
  `_freeze_trace_config`, `freeze_refusal`, `_snapshot_row`, `_snapshot_block`,
  `_config_signature`, `_draw_signature`) is still there, and **its comment is
  now stale**: it says these "cannot go there until [the model types] do", and
  they have. **Three of the seven** — `_duplicate_trace_config`,
  `_config_signature`, `_draw_signature` — are already in `pkg_rlc.model.trace` and
  could be imported directly by the panels today. `_snapshot_row` /
  `_snapshot_block` must stay wrappers here (they are what supply
  `provenance=`), and `freeze_refusal` / `_freeze_trace_config` need the freeze
  family to move first.
* **`pkg_rlc.panels.panels_results.RunTab.run: "RunSnapshot"` is still an unresolvable
  annotation**, and its module docstring still says `RunSnapshot` "could not
  follow… still L6". `RunSnapshot` is at L1 now. Measured: `get_type_hints` on
  `RunTab` raises `NameError`; the other three panels are clean. One import
  fixes it, and the docstring must move with it.
* **`App` still owns all the mutable run/log state** — `_run_tabs`,
  `_last_run`, `_run_counter`, `_log_unseen`, `_log_forced`, both caps and
  their IntVars, `_suppress_editor_sync`, `_ed_*`. Deliberate: those are
  reassigned at runtime and read straight off `app` by the tests, so moving
  them needs forwarding properties — two places one value can be read from.

**Process, and it is the one thing this night actually broke.** The phase gate
reverts a phase on a missing deliverable and cannot distinguish "produced
nothing" from "produced most of it and documented the rest" — §3.2. The layer-map
hole that let run 1's silent failure through is closed by accident rather than
by design: `tests/test_layering.py` still silently SKIPS a module named in
`LAYERS` that does not exist, and today all 21 exist, so nothing is skipped. A
one-line assertion that every name in `LAYERS` resolves to a file would make
that a property instead of a coincidence.

**The failure mode the panel split has**, unchanged and worth re-reading before
anything else moves into a panel: **a bare `self` where the app was meant**.
Five were in the first draft, **four of which failed silently** (three
window-refresh calls that simply did nothing). Grep cannot find them —
`(self)\b` never matches. The check is an AST pass and must be re-run after
every further move.
