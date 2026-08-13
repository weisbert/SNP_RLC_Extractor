# The overnight refactor — morning report

Written 2026-08-14 ~02:40, at the end of two sequential agent runs covering
2026-08-13 20:03 → 2026-08-14 02:19. Base commit for everything below is
`3ae2dfd` ("Show the whole trace name…"), the last commit before the refactor
started. HEAD is `8a1896b`.

---

## 0. Addendum — the third run (F1 restore, then F2), 02:20 → 05:0x

Written by the run after the one that produced everything below. **Read §1–§6
as the account of runs one and two; this section is what changed afterwards,
and it supersedes them where they disagree.**

**The revert was undone and the model phase is in.** `pkg_rlc_model` (L1) now
carries `TraceConfig`, `FileEntry`, `SolveNetwork`, the signature helpers, the
frequency snap and the whole run record. Nine of the ten lazy back-imports are
gone; the one that remains (`pkg_rlc_extractor` → `App`, deferred for a
measured 251 ms of tkinter+matplotlib) is a justified deferral, not a dodge.

**Two more modules landed after it, both L2:**

| module | lines | what it is |
|---|---|---|
| `pkg_rlc_session.py` | 465 | Save Config / Load Config / autosave, as a pure dict ↔ model round trip. A verbatim move; there was never any Tk in it. |
| `pkg_rlc_run.py` | 439 | What a Calculate RUNS: the network a trace is solved against, the spec it is solved with, the reference-node check, the coupling reduction. `log` / `files` / `cache` injected. |

`pkg_rlc_gui.py` is **3206 lines**, from 10 953 at the start of the night.

**Three symbol groups moved down, each because the layering gate refused the
alternative** — and each is written up in `CLAUDE.md`:

* `FreqSnap` + `snap_to_grid` + `combine_freq_snaps` + `freq_grid_step` →
  `pkg_rlc_model`. Where a measurement landed is a fact about the measurement.
  `marker_freq_text` stayed in `pkg_rlc_report`: it takes a format string and
  returns a sentence.
* `VIEW_DETAIL` / `VIEW_SUMMARY` / `VIEW_COMPARE` / `RESULTS_VIEWS` →
  `pkg_rlc_model`. `results_view` is a SAVED setting, so the session file (L2)
  has to validate it while the renderer (L3) acts on it; a vocabulary shared
  between a format and a renderer belongs at or below the lower of the two.
* `LOG_INFO` / `LOG_WARN` / `LOG_ERROR` → `pkg_rlc_model`. A severity is a
  property of the MESSAGE, not of the pane — which is the repo's own rule,
  already written in those words. `LOG_BADGE_CAP` and `log_tab_label` stayed.

**What did NOT land, and it is one thing: `_on_calculate`'s own body.** The
brief asked for `calculate(traces, files, controls, log, cache, only)`. The
solve underneath it moved (that is `pkg_rlc_run`); the orchestration did not,
and the reason is measured rather than asserted. After the solve came out, the
body's remaining couplings above L2 are exactly five, and every one is
presentation or an App action: `marker_freq_text` (L3), `describe_run_change`
(L3), `reference_provenance` (L5, via the snapshot wrappers),
`UNFREEZE_MENU_LABEL` (L5) and `_migrate_trace` (logs, and refreshes the Traces
list). A `calculate()` at L2 would therefore take **three injected callables
and one injected string, and hand the header line and the run-to-run diff back
to its caller** — i.e. the report's ORDER split across two modules, which is
the "two things that can come to disagree" failure this repo names everywhere
else, arriving inside the fix for it. The split that IS honest is named in
`CLAUDE.md`: `pkg_rlc_run` answers *what is the number*, `_on_calculate`
answers *what does the reader see, in what order, at what severity*.

**Measured after the last commit of this run:** full suite **2531 tests, 453
shards, 357.9 s, exit 0**; `--fast` 1044 tests, 4.5 s, OK. `golden_legacy.npz`,
`render_reference.json`, `cli_reference` and `attrib_reference` **all
untouched** — `git diff --name-only` over this run's five commits lists five
`.py` files and `CLAUDE.md`, and **no test file at all**. The GUI was driven
end to end: opened, file added through `_on_add_file`, Calculate, session saved
and reloaded, closed cleanly.

**One behaviour difference was found and restored rather than shipped.**
`run._build_termination` deliberately does not migrate a legacy spec (migrating
logs a line and refreshes the Traces list, so it is an App action), and
`_calculate_coupling_trace` builds its own termination when the caller passes
none — so on that one rarely-taken path a mode-4 trace would have been read
unmigrated, silently. `App._calculate_coupling_trace` migrates first on exactly
that condition. Commit `a92d360`.
---

## 1. Verdict

**The tool works and the suite is green.** Full run measured on this box after
the last commit: `python tests/run_parallel.py -j 4` → **2531 tests, 453
shards, 422.9 s, exit 0**. `--fast` → 1044 tests, 4.5 s, OK. Every one of the
22 `pkg_rlc_*` modules imports, and the CLI produces correct output on the
coupling path. `golden_legacy.npz` and `render_reference.json` were **not
touched at any point in either run**.

**One shipped surface changed behaviour: the CLI's frequency line.** It was
printing a frequency that does not exist in the file (`@ 0.101 GHz` for a point
at 100 990 000 Hz) with no snap note anywhere on the coupling path. That was a
real defect, it was fixed, and it is the one thing to look at first — §4.

**The disappointing part: the biggest remaining piece of the plan still is not
in the tree, and this run did not fail to build it — it built it, tested it,
and then reverted it.** `pkg_rlc_model` (moving `TraceConfig` / `FileEntry` /
the snapshot types down out of `pkg_rlc_gui`) landed in three commits at 00:52–
01:18 and was reverted whole at 01:38 with **no failing test named in any of
the three revert messages**. I re-ran that reverted state tonight: `--fast`
1044 OK, and the five most-affected Tk modules (`layering`, `session`,
`freeze_trace`, `run_snapshot`, `multifile*`) 391 tests OK. §3 and §6.

---

## 2. What changed

### Modules

| module | before | after | note |
|---|---:|---:|---|
| `pkg_rlc_gui.py` | 10 954 | **4 407** | −60%. Four window panels + five libraries left it. |
| `pkg_rlc_core.py` | 4 725 | **169** | Now a re-export facade over touchstone/spec/solve. |
| `pkg_rlc_extractor.py` | 4 377 | **3 044** | 13 report sections + 6 duplicated formatters left. |
| `pkg_rlc_help.py` | 2 745 | **133** | Prose moved to `docs/help/*.md` (2 648 lines). |
| `pkg_rlc_plot.py` | 1 194 | **1 028** | Generic widgets (incl. `ReflowRow`) went to `pkg_rlc_widgets`. |
| `pkg_rlc_attrib_gui.py` | 4 439 | **4 462** | +23. Grew slightly; only the ground-model import changed. |
| `pkg_rlc_attrib.py` | 4 767 | **4 767** | Untouched all night. |
| `pkg_rlc_compose.py` | 1 861 | **1 861** | Untouched. |
| `pkg_rlc_files_gui.py` | 1 487 | **1 487** | Untouched. |
| `reduce_snp.py` | 1 224 | **1 224** | Untouched (standalone by design). |

New modules, all of which did not exist at 20:03:

| new module | lines | layer |
|---|---:|---|
| `pkg_rlc_spec.py` | 2 050 | L0 |
| `pkg_rlc_report.py` | 1 727 | L3 |
| `pkg_rlc_panels_editor.py` | 1 644 | L5 |
| `pkg_rlc_touchstone.py` | 1 562 | L0 |
| `pkg_rlc_attrib_report.py` | 1 557 | L3 |
| `pkg_rlc_validate.py` | 1 351 | L2 |
| `pkg_rlc_solve.py` | 1 218 | L0 |
| `pkg_rlc_panels_results.py` | 1 115 | L5 |
| `pkg_rlc_widgets.py` | 939 | L4 |
| `pkg_rlc_conntable.py` | 505 | L3 |
| `pkg_rlc_panels_traces.py` | 388 | L5 |
| `pkg_rlc_panels_files.py` | 273 | L5 |
| `pkg_rlc_csv.py` | 113 | L3 |

**Totals: 10 modules / 37 774 lines → 23 modules / 37 025 lines.**

That headline shrink is entirely the Help prose leaving Python. Excluding
`pkg_rlc_help.py`, the Python grew: **35 029 → 36 892, i.e. +1 863 lines
(+5.3%)**. That is the honest price of the split — module docstrings, imports,
and the re-export blocks that rule 2 requires. It is worth stating plainly:
*there is more Python in the tree this morning than there was last night*, and
the win is in where it sits, not in how much of it there is.

`CLAUDE.md` went 3 498 → 4 257 lines (+759). The map is bigger too.

### Tests

| | before | after |
|---|---:|---:|
| full suite, collected | **2 444** (41 modules) | **2 531** (44 modules) |
| full suite, run | — | 2 531 / 453 shards / 422.9 s at `-j 4`, exit 0 |
| `--fast` | 976 / 5.5 s (recorded) | **1 044 / 4.5 s** (measured tonight) |
| test modules edited | — | **6 of 44** |

The 2 444 figure is measured by collection at the base commit, not quoted.
Note the plan's baseline of "2045" is `CLAUDE.md`'s last recorded *measurement*
and was already stale before the night started — the real count at `3ae2dfd`
was 2 444.

Only **6 of 44 test modules were touched**, and **3 of those 6 are brand new**
(`test_layering`, `test_cli_golden`, `test_attrib_golden`). The three modified
ones are `test_freq_label` (+9 tests for the CLI fix), `test_run_parallel` and
`test_attrib_gui_integration`. **41 of 44 test modules were never opened** —
that is the re-export rule (rule 2) working, and it is the single best evidence
that the moves were pure.

### Golden references

| reference | status |
|---|---|
| `tests/fixtures/golden_legacy.npz` | **0 commits tonight.** Never moved. |
| `tests/fixtures/render_reference.json` | **0 commits tonight.** Never moved. |
| `tests/fixtures/attrib_reference/` (60 cases) | Created tonight; never modified since. |
| `tests/fixtures/cli_reference/` (144 cases) | Created tonight; modified **once**, by the frequency fix — 55 lines, verified below. |

### "Keep in sync" comments

I could not reproduce the plan's figure of 95 with any pattern, at the base
commit or at HEAD, so I will not pretend to a before/after on it.

What was actually measured, by the R4 phase (`fc8feb8`), with the pattern
written into the commit so it can be repeated — `keep … in sync|step|mirrored|
aligned` over every `.py` and `.md` — is **19**, classified one by one:

* **11** state that there is *no* second copy and why (true, verified),
* **5** describe duplication that is deliberate and still there (`reduce_snp`'s
  own parser, `.gitattributes` + `pack.ps1`, `files_gui.trace_file_labels`),
* **2** are false matches on the word "step",
* **1** was stale and was corrected (the "coupling ratio … keep the six in
  sync" bullet: two of its six sites had moved).

**No comment was deleted from any source file.** The finding is that the waves
which merged the duplicate code had already updated their own comments as they
went, so there was nothing to sweep. My own independent counts across `.py` +
`.md`, base → HEAD: `mirror` 100 → 103, `same rule` 77 → 88, `in sync` 5 → 6.
The count went slightly *up*, because the new modules document their own
boundaries.

---

## 3. What did not land, and why

Three modules were planned and are not in the tree: `pkg_rlc_model` (L1),
`pkg_rlc_session` (L2), `pkg_rlc_run` (L2). All three trace to one root cause —
`TraceConfig`, `FileEntry` and the snapshot types are still in `pkg_rlc_gui` at
L6, so nothing below L6 can touch the data model except through the app object.

**Run 1 (20:03 – 00:12).**

* `pkg_rlc_model` — its agent **died on "API Error: Server error mid-response"**
  after 258k tokens, one sentence before writing its first byte. Not a logic
  failure. It committed nothing.
* `pkg_rlc_session` — skipped by its agent: `trace_to_dict` / `trace_from_dict`
  need `TraceConfig`, which was still at L6.
* `pkg_rlc_run` — its agent parsed the fifteen candidate functions with `ast`,
  found **sixteen runtime symbols living above L2**, proved with a two-line
  probe that the layering gate would reject the module, deleted the probe,
  committed nothing, and reported precisely. That was correct behaviour and its
  diagnosis is now written into `CLAUDE.md` ("The run module — ATTEMPTED,
  BLOCKED, NOT STARTED").

**The gate that let the model phase through.** Run 1's phase gate checked that
the tree was healthy (suite green, references unmoved) rather than that the
phase had produced its deliverable. A phase that commits nothing at all passes
a tree-health check trivially. **This recovery run did not close that hole
either**, and it is still open in a second form: `tests/test_layering.py`
declares `pkg_rlc_model`, `pkg_rlc_session` and `pkg_rlc_run` in its `LAYERS`
map, and **a module named in `LAYERS` that does not exist is silently skipped**
(the docstring says so at line 20). So the layer map today describes three
files that are not there, and nothing goes red about it.

**Run 2 (00:37 – 02:19) — what it fixed and what it did not.**

| item | outcome |
|---|---|
| Six `CLAUDE.md` rules naming `pkg_rlc_core` as the home of symbols that had left it | **fixed** (`4784afe`) |
| `pkg_rlc_model` | **built, green, then reverted** — see below |
| `pkg_rlc_session` | **not started.** Still blocked on the model. |
| `pkg_rlc_run` | **not started.** Still blocked on the model. |
| 31 dead re-export aliases from the generous rule-2 sweep | **deleted** (`506888d`), 372 names classified, 341 kept |
| The layer map / module map describing files that had moved | **fixed** (`a3efcde`) |
| The "keep in sync" sweep | **measured and classified**, 1 stale entry corrected (`fc8feb8`) |
| The CLI/GUI frequency divergence | **fixed** (`b7f1ddd`, `181cd66`, `8a1896b`) |
| The phase-deliverable gate | **not fixed** |

**On the model revert, precisely.** It landed as `a139a58` (the move itself —
482 lines into `pkg_rlc_model.py`, the palette to `pkg_rlc_widgets`,
`pkg_rlc_validate` moved L2 → L1 with the reason written beside it), then
`cf7d832` (nine lazy `_gui()` imports deleted — the dodge the move exists to
remove), then `98ddb00` (the docs). It was reverted by `c0d3103`, `3e26ed1`,
`1bb2f35` at 01:38, taking the tree back to `4784afe` byte for byte.

The stated reason in the revert messages is *"the R1 phase is being undone
whole so the tree is coherent in the morning"*. **None of the three names a
failing test, a broken behaviour, or a rule it violated.** The doc revert is
justified by the code revert, which is circular. So I checked it empirically:
I built a detached worktree at `cf7d832` and ran it.

```
--fast                                                    1044 tests   OK
-m layering session freeze_trace run_snapshot multifile     391 tests   OK
```

Green on both. That does not prove the full 2 531 would pass there, and I did
not spend 400 s to find out while the main suite was running. But it means the
largest outstanding item of this refactor is sitting in git history in a state
that passes everything I pointed at it, and restoring it is
`git revert 1bb2f35 3e26ed1 c0d3103` plus a full-suite run — not a rebuild.
**That is the first thing I would put in front of you.**

---

## 4. Findings about the product

These are not refactor mechanics. They are places where the CLI and the GUI
have been telling users different things about the same data. Comparing the two
surfaces side by side is something nobody had done before, because until
tonight the CLI's output existed only as `print` calls to fd 1 with no way to
capture it; `pkg_rlc_attrib_report` returning `list[str]` and
`tests/fixtures/cli_reference/` are what made the comparison possible at all.

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
such point in the file** — the two nearest are 100.99 MHz and 125.9875 MHz. The
GUI, on the same data, said `Z matrix @ 0.10099 GHz`. And the coupling path
carried no snap note at all: `snapped to …` existed only under `--attribute`
and `--cold-start`, so that rounded number was the CLI's *only* statement of
where the numbers came from. The same defect was on the scalar `@ <f>:` line
above the R/L/C/Q table.

**Why this was fixed without waking you.** The rule applied, which is now
written into `CLAUDE.md` so you can audit it and will meet it again:

> Where the two surfaces disagree **and the repo already has a documented
> position** (a `CLAUDE.md` entry, a test file's stated purpose,
> `docs/theory.md`), the surface matching that position wins and the other is
> fixed. Where there is no documented position, it is a genuine product choice
> and it is left for the user.

Here the position was already written down. `tests/test_freq_label.py` exists
for exactly this, and its stated purpose is quoted in `CLAUDE.md`: *"the marker
frequency a report prints says where the numbers came from"*. Its own docstring
records the original bug — a user reading `@ 5.6 GHz` and `@ 5.512 GHz` in one
report with nothing to reconcile them. The GUI was fixed then; **the CLI was
simply missed**. So this was not a choice between two defensible spellings. It
was one surface lacking a fix the project had already made and documented.

**Commits.** `b7f1ddd` (the code + 9 new guards in `test_freq_label.py`),
`181cd66` (the reference), `8a1896b` (`CLAUDE.md`).

**The reference diff is the proof the change had the right shape.** I verified
it independently rather than taking the commit's word:

```
changed lines in tests/fixtures/cli_reference/ :  110  (55 files, 55 +/55 −)
changed lines that are NOT a "@ <freq>" marker  :    0
distinct shapes of changed line                 :    2
    "@ N GHz  (requested N GHz; nearest point, grid step N MHz)  --  Z matrix ..."
    "@ N GHz  (requested N GHz; nearest point, grid step N MHz):"
```

No number moved, no table moved, no exit code moved, no CSV cell moved.
`golden_legacy.npz` and `render_reference.json` did not move at all — which is
the shape it had to have, because the GUI was already right.

One deliberate asymmetry worth knowing about: **precision is per line and is
not uniform.** The coupling line takes the pane's `{:.6g}` (at `{:.4g}` an
exact 5.0005 GHz point renders as `5 GHz`); the scalar line keeps its
historical `{:.4g}`. `marker_freq_text` widens *both* numbers itself the moment
there are two to tell apart, so a marker that **is** a data point renders
byte-for-byte what it always did at both sites.

### 4.2 The other divergences

Seven were found in total across both runs. One is fixed; six are open. None of
the six has a documented position in the repo, so by the rule above they are
product choices and were left for you.

**Between the CLI report and the results pane:**

| # | divergence | status |
|---|---|---|
| 1 | Frequency provenance: CLI rounded, no snap note | **FIXED** — §4.1. Position: `test_freq_label.py`'s stated purpose. |
| 2 | **The CLI does not rank or floor the coupling pair list.** It is still `for pr in res.pairs:` — nested-loop `(a,b)` order, every pair, no `rank_coupling_pairs`, no `COUPLING_FLOOR_DB`, and `worst M/L` (the rank key itself) is not printed at all. | **OPEN.** *Recommendation: move the CLI to the pane's answer.* The reason the pane was changed applies verbatim — six measurement ports make 15 pairs and index order says nothing about which matter. This is the one of the six I would actually change. |
| 3 | **Reciprocity is a METRIC on the CLI and a VERDICT in the pane.** CLI: `Reciprocity error = 5.76e-15  (max|Z_ab - Z_ba| / max|Z_ab| …)` plus a paragraph. Pane: `✓ reciprocal (5.76e-15)`. | **OPEN.** Defensible as-is — a terminal has no 144-column budget, and the pane's slimming was driven by that budget. *Recommendation: leave.* But they are now different products. |
| 4 | **The CLI's legend is per-block, differently worded, and has no `\|k\|>1` prompt.** `_pair_flag` is pane-only, so a CLI user whose `\|k\|` exceeds 1 gets no "check the port setup" prompt. The pane emits `COUPLING_LEGEND_LINES` once per run; the CLI repeats its own wording under every block. | **OPEN.** *Recommendation: port `_pair_flag` to the CLI.* The missing `\|k\|>1` prompt is a real loss of a diagnostic, unlike the wording and the repetition. |

**Between the CLI attribution report and the Attribution window:**

| # | divergence | status |
|---|---|---|
| 5 | ~~**The two surfaces sort an UNDEFINED delta to OPPOSITE ENDS.**~~ `_attr_print_sensitivity` keys `(0 if isfinite else 1, -abs_delta)` so NaN sorts **last**, and its docstring states the rule ("NaN is a missing measurement, not a small number" — the same rule `rank_coupling_pairs` follows). `pkg_rlc_attrib_gui.sensitivity_table` keyed `-abs_delta if isfinite else float("-inf")`, and `-inf` is the smallest key on an ascending sort, so NaN sorted **first, above the strongest real effect**. The window contradicted *itself* too: `_fold_terms` uses `+inf` for precisely this case and comments on why. | **FIXED** (`8d98ba9`, `93326a6`, `98ee544`). The window now uses the CLI's spelling verbatim. Three things the fix turned up that the recommendation did not anticipate: the reference did **not** need regenerating for the fix — all four captured sensitivity cases hold finite deltas, so `attrib_reference` could not see the ordering move at all, which is why the bug survived a golden-referenced refactor. A hand-built case (`sensitivity_fake_undefined_delta`) was added in its own commit to close that hole, proven to catch the defect by restoring the old key. And the guard pins the **window against the CLI directly** rather than each against a literal, which is the shape the divergence would have to take to return. CLAUDE.md now states the rule once for all four implementations. |
| 6 | **Two `_e`, at two precisions.** `pkg_rlc_attrib_report._e` writes `%.6e`; `pkg_rlc_attrib_gui._e` writes `%.12e`. Both put a float from the same decomposition into a CSV. | **OPEN.** *Recommendation: pick one, probably `%.12e`* — a CSV is for re-use, and 6 digits is lossy. Low urgency. |
| 7 | **Two candidate grammars.** `--attribute-alt` splits on **comma** (`R=0.5,L=1n`, via `y_series_rlc`); the window's Candidates field splits on **whitespace** (`R=0.5 L=1n`, building `R + jwL + 1/(jwC)` directly). Both refuse a token with no `=` for the same measured `R=5 m` reason, but the two expressions are not obliged to agree at `omega == 0`. | **OPEN.** *Recommendation: accept both separators on both sides.* This is a user-facing trap — the spelling that works in one place silently fails in the other. |

### 4.3 Duplication that was removed before it could diverge

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

1. **Decide on the reverted model phase.** Read `1bb2f35`'s message, then run
   `git revert --no-commit 1bb2f35 3e26ed1 c0d3103` on a branch and run the
   full suite. If it is green, that is `pkg_rlc_model` recovered for free and
   `pkg_rlc_session` / `pkg_rlc_run` unblocked. If it is red, the revert had a
   reason nobody wrote down and you have found it.
2. **Sanity-check the CLI frequency line against your own data.** Run a
   coupling extraction at a marker that is *not* a grid point and confirm the
   `(requested … ; nearest point, grid step …)` note reads correctly, then run
   one at an exact grid point and confirm it renders exactly as it used to.
   This is the only shipped behaviour change of the night.
3. **Open the GUI and do one real extraction end to end** — load a file,
   Calculate, read the results pane, Export CSV, open the Attribution window.
   The suite drives all of this, but 87% of it is Tk geometry and none of it is
   you looking at the screen.
4. **Rule on divergences 2 and 7** (§4.2). Those are where the two surfaces are
   actively unhelpful but the right answer is a judgement call, so they are
   left for you. **#5 was the one genuine bug in that table and it is fixed**
   — it ranked an unmeasurable row above the strongest real one in the
   Attribution window, contradicting the CLI, `rank_coupling_pairs` and the
   same file twelve hundred lines up, so it was corrected rather than tabled.
5. **Read the four new sections of `CLAUDE.md`** — the layering gate, the four
   panels, the two attribution reports, the one-formatter-two-spellings
   section. If any of them describes something you did not agree to, that is
   the cheapest moment to say so.

---

## 6. What is still owed

**Not started, blocked on the model phase.**

* `pkg_rlc_session` — `trace_to_dict` / `trace_from_dict` need `TraceConfig`.
* `pkg_rlc_run` — sixteen runtime symbols live above L2. Measured by `ast`, not
  guessed. `CLAUDE.md`'s "The run module — ATTEMPTED, BLOCKED, NOT STARTED"
  carries the full diagnosis, including that the plot-curve helpers can **never**
  be part of it.

**Reverted, recoverable from history.** `pkg_rlc_model` — §3.

**Not fixed, and it is the process hole that caused the whole shortfall.** The
phase gate still checks tree health rather than phase deliverables, and
`tests/test_layering.py` still declares three modules that do not exist while
silently skipping them. A one-line assertion that every name in `LAYERS`
resolves to a file — or an explicit `PLANNED = {...}` set that is asserted
*empty* at the end of a refactor — would have turned run 1's silent model
failure into a red test.

**The honest state of the panel split.** Four panels are out
(`files` 273, `traces` 388, `results` 1 115, `editor` 1 644) and `App` is 4 407
lines. It is genuinely better, and it is not finished:

* **`App` still owns all the mutable state** — `_run_tabs`, `_last_run`,
  `_run_counter`, `_log_unseen`, `_log_forced`, both run caps and their
  IntVars, `_suppress_editor_sync`, `_ed_*`. Panels read and write it through
  the injected app. Deliberate: those are reassigned at runtime and read
  straight off `app` by the tests, so moving them needs forwarding properties —
  two places one value can be read from.
* **`App` carries a block of 7 `staticmethod` aliases** over `TraceConfig` /
  `RunSnapshot` (`_duplicate_trace_config`, `_freeze_trace_config`,
  `freeze_refusal`, `_snapshot_row`, `_snapshot_block`, `_config_signature`,
  `_draw_signature`) so panels can reach them. **That block is the checklist
  for the model phase.** When it is empty, the panels import their model
  directly.
* **`RunSnapshot` and `TraceConfig` survive in the panels only as unevaluated
  annotations** (`from __future__ import annotations`). They are inert today
  and **will `NameError` the moment anything calls `get_type_hints` on those
  modules.**
* **The failure mode this split has is a bare `self` where the app was meant**,
  and it is nearly invisible: five were in the first draft, **four of which
  failed silently** (three window-refresh calls that simply did nothing). Grep
  does not find them — `(self)\b` never matches. The check is an AST pass, and
  it must be re-run after moving anything else into a panel.
* **10 lazy back-imports over 3 pairs remain** (down from 12), all of them
  `pkg_rlc_*` reaching up into `pkg_rlc_gui`: 8 in `pkg_rlc_files_gui`, 1 in
  `pkg_rlc_attrib_gui`, 1 in `pkg_rlc_extractor`. Nine of the ten would be
  deletable by the model phase — `cf7d832` did exactly that and was reverted
  with it.

**Things that are worse than when the night started**, stated plainly:

* +1 863 lines of Python outside `pkg_rlc_help.py` (+5.3%).
* 23 modules to navigate instead of 10.
* `CLAUDE.md` is 759 lines longer.
* The layer map now describes three files that do not exist, and nothing
  complains.
* `pkg_rlc_attrib_gui.py` grew by 23 lines rather than shrinking.
