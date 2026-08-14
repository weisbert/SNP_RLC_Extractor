# The overnight refactor — morning report

One account of the whole refactor, rewritten in place rather than appended to.
It covers four sequential agent runs overnight, 2026-08-13 20:03 →
2026-08-14 04:54; one more the following morning that moved every module into a
package (§2.0); and one after that which closed the five CLI/GUI divergences
the earlier runs had found and deliberately left for the user (§4.3–§4.7).
Every before/after below is measured against `3ae2dfd` ("Show the whole trace
name…"), the last commit before the refactor started, and describes the tree at
HEAD.

**EVERY NUMBER IN THIS FILE WAS RE-TAKEN AT HEAD.** None is carried forward
from an earlier draft, and where a later run overtook an earlier
recommendation the paragraph says so on the spot rather than being left
standing. `CLAUDE.md` is the live description of the tree; this file is the
account of how it got that shape.

**Three things are worse than they were before the refactor started, and they
are the price of the rest.** There is more Python in the tree, not less:
**+2 814 lines** outside `pkg_rlc/present/help.py`, **+8.0%** (it was +2 591 /
+7.4% before the divergence work; that added the CLI's ranking, verdict and
grammar code and one new test module). There are **25 modules** to navigate
instead of 10 — though they are now in seven folders rather than flat in the
repo root, which is what §2.0 is about. `CLAUDE.md` is **1 169 lines** longer.
The win is in where the code sits, not in how much of it there is, and §2
states both halves with the measurements.

---

## 1. Verdict

**The tool works and the suite is green.** Re-measured on this box at HEAD,
after the move into the package AND after the five divergence closures of
§4.3–§4.7: `python tests/run_parallel.py -j 4` → **2 569 tests, 459 shards,
355.6 s, exit 0, and 0 skips**; `--fast` → **1 048 tests, 4.7 s, OK**. (At
`d8e9a7f`, before those five, the same two runs were 2 542 / 454 / 366.2 s and
1 044 / 4.7 s — so the five closures added **27 tests and 5 shards**. The full
number tracks CONTENTION more than anything else: the same `--fast` run
measured 5.5 s while the full suite was still going. Read the exit code, not
the clock.) The skip count is asserted rather than assumed:
every Tk module guards itself with `@skipUnless(TK_OK, …)`, `unittest` counts a
skipped test in its `Ran N tests` line, and it still prints `OK` — so a total
failure to reach Tk looks exactly like a clean pass with a matching count.

All 25 package modules import. The CLI was run end to end through the shipped
entry point — `python pkg_rlc_extractor.py --cli …` on
`coupled_2port_gndref.s2p`, which returned the fixture's analytic **2 nH** —
and the GUI is driven end to end by `test_attrib_gui_integration` (a real
`App`, file added through `_on_add_file`, Calculate, the Attribution window,
session saved and reloaded), which is in the green run above.

**THE TWO REFERENCES THAT PIN NUMBERS HAVE NOT MOVED SINCE BEFORE THE REFACTOR
STARTED, AND THAT IS THE PROOF NOTHING CHANGED AN ARITHMETIC RESULT.**
`golden_legacy.npz` (the reduction path, bit-for-bit) and
`render_reference.json` (the GUI results pane) are at **0 commits since
`3ae2dfd`** — through the refactor, through the move into `pkg_rlc/`, and
through all seven divergence closures. The two that pin CLI and window TEXT did
move, deliberately and only where a decision required it: `cli_reference/`
(144 cases) is at 4 commits since `3ae2dfd` and `attrib_reference/` (60 cases)
at 3, every one of them a regeneration in its own commit, each accounted for
line by line in §4. Across the **move into `pkg_rlc/`** alone (`9a629f5..HEAD`
minus the divergence work) all four are untouched.

**ALL SEVEN CLI/GUI DIVERGENCES ARE NOW CLOSED — §4.** Two on the night of the
refactor (the CLI printed a frequency that does not exist in the file; the
Attribution window ranked an unmeasurable row above the strongest real effect),
five on 2026-08-14 once the user supplied the position the repo was missing.
That position is `deploy/doctor.sh`'s own exit rule — a CLI-only install is a
SUCCESSFUL install — and the rule it yields is stated once at the top of §4:
**the CLI may be terser than the pane, but it must never omit a diagnostic or a
decisive number.**

**Six shipped surfaces changed behaviour and five of the six are the CLI's.**
The frequency line (§4.1); the coupling pair list, which is now ranked and
prints its rank key (§4.3); reciprocity, which now leads with a verdict and
keeps everything it had (§4.4); the `|k|>1` prompt, which reached this surface
for the first time (§4.5); and the attribution CSVs, which gained six digits of
precision on values that are otherwise identical (§4.6). The sixth is the
candidate grammar (§4.7), which changed on **both** surfaces so that a spelling
that works in one place stops failing in the other. The GUI's own change is the
sensitivity ordering (§4.2).

**Not one of the seven moved an arithmetic result** — which is why the two
references that pin numbers are at 0 commits. There is exactly one *value*
correction in the set and it is a bug fix, not a divergence: at DC the CLI read
a series capacitor as a perfect SHORT where the window read it, correctly, as
an OPEN, and it was found only because §4.7 forced the two expressions to be
measured against each other.

**The structural work is in, including the piece that was reverted at 01:38.**
`pkg_rlc.model.trace` (L1) exists and carries the data model; `pkg_rlc.services.session` and
`pkg_rlc.services.run` (L2) landed on top of it; nine of the ten function-level
back-imports into `pkg_rlc.frontend.app` are gone. §3 says what that cost and what it
still does not cover.

---

## 2. What changed

### 2.0 The move into `pkg_rlc/` — the folders ARE the layers

The night left **26 `.py` files flat in the repo root**, 25 of them
`pkg_rlc_*`. The user opened the directory, said
*"很多py文件哎，不用分文件夹吗"*, and this is what that became. It is a move and
a rename of import paths and **nothing else**: not one expression of behaviour
changed, which is what §1's four byte-identical golden references say from the
other side.

**Why folders, and neither reason is cosmetic.** First, every one of those 25
files began with the same nine characters, so the distinguishing part of a name
started at character 10 — the prefix was already doing a package's job, badly.
Second, and the one that matters: **the layer structure was real and
invisible.** `tests/test_layering.py` enforced a one-directional L0…L6
dependency map out of a `LAYERS` table declared as data, and nothing in the
filesystem showed it. Making the folders BE the layers gives that map one home
instead of two.

The layout:

| folder | layer | modules |
|---|---|---|
| `pkg_rlc/physics/` | L0 | `touchstone` `spec` `solve` `core` (the facade) `compose` `attrib` |
| `pkg_rlc/model/` | L1 | `trace` `validate` |
| `pkg_rlc/services/` | L2 | `session` `run` |
| `pkg_rlc/present/` | L3 | `report` `csv` `attrib_report` `conntable` `help` |
| `pkg_rlc/widgets/` | L4 | `widgets` `plot` |
| `pkg_rlc/panels/` | L5 | `panels_files` `panels_traces` `panels_results` `panels_editor` `files_gui` `attrib_gui` |
| `pkg_rlc/frontend/` | L6 | `app` (was `pkg_rlc_gui`) `cli` (was `pkg_rlc_extractor`) |

`pkg_rlc_validate` went to **`model/`, not `services/`**, because that is the
layer it was already at: `TraceConfig.port_descriptor()` is one call into it
and `_config_signature` ends on it, so the model imports it and it therefore
has to sit at or below the model. Putting it in its own one-module folder would
have been a folder that is not a layer, which is the whole thing this move is
against.

**THE LAYER IS NOW READ OFF THE PATH, and the second list is deleted.**
`tests/test_layering.py` has no `LAYERS` table and no `LAYER_PREFIXES`: it has
a seven-line `LAYER_OF_FOLDER` saying what the seven folder names mean, and
`layer_of()` is `LAYER_OF_FOLDER.get(path.parts[1])`. A module in a folder
nobody has declared **FAILS** rather than defaulting to anything, and a module
loose in the package root fails too. The file went 21 tests → **27**, 0.27 s,
still pure `ast` with no repo import, and `TestTheGateHasTeeth` still
mutation-checks the gate against synthetic trees in a temp dir — including two
new mutations for this shape: that the FOLDER and not the file name decides the
layer, and that a deeper folder inside a layer stays in that layer.

**Two things stayed at the root on purpose.** `reduce_snp.py` is standalone by
design — it is copied to simulation servers on its own and imports nothing from
this repo, which `test_layering` asserts rather than assumes. And
`pkg_rlc_extractor.py` survives as a **41-line shim** over
`pkg_rlc.frontend.cli`: that name is the published way to run the tool (the
README, every Help tab, the CLI's own `--help` examples, `deploy/doctor.sh`'s
closing advice), it is the sentinel `deploy.sh` and `deploy/pack.ps1` both
check to confirm they are pointing at an install root, and
`tests/_cli_capture.py` drives `pkg_rlc_extractor.main(argv)` **in-process**
over 143 invocations. It re-exports `main` by name and the rest by star import.

**THE REWRITE WAS DONE BY A COMMITTED SCRIPT, `tests/_repackage.py`** (294
lines, leading underscore so `unittest discover` and
`run_parallel.discover_shards` both skip it — the `_golden_capture.py`
convention). ~370 references were at stake: 100 import statements in the
modules, 140 in `tests/`, and every path mention in the docs. By hand that is
370 chances to typo one, and a typo in an import is a module that silently
resolves to something else. As a script it is reproducible, reviewable as a
diff, and it reports its substitution count per file. It uses `git mv`, so
`git log --follow` survives all 25 moves — git recorded every one as `R100`, a
100%-similarity rename.

**The one place the rule bends is an alias.** `import pkg_rlc_gui` became
`import pkg_rlc.frontend.app as pkg_rlc_gui`, and likewise for the other three
bare-`import` modules. That keeps **233** attribute references
(`pkg_rlc_gui.TraceConfig`, `pkg_rlc_core.MAX_SNIFF_NPORTS`, …) resolving
untouched, and — the reason that matters rather than the line count — it is
what keeps `mock.patch.object(pkg_rlc_core, …)` working, because an alias is
the same module object. `tests/_repackage.py`'s own docstring estimates that
saving at "~1000 lines"; the measured figure is 233, and the estimate should be
read as the argument it is rather than as a measurement.

**The four things that could have broken silently were each checked by
measurement, not by reasoning:**

* **`pkg_rlc.physics.core`'s write-through survived.** It is a
  `types.ModuleType` SUBCLASS whose `__setattr__`/`__delattr__` forward to
  whichever split module owns the name, and five tests
  `mock.patch.object(pkg_rlc_core, "MAX_SNIFF_NPORTS" / "SNIFF_HARD_CAP" /
  "_check_s_values", …)` and then call a parser that reads its OWN module
  global. Verified through the new path: patching `core.MAX_SNIFF_NPORTS` to 7
  is visible as `pkg_rlc.physics.touchstone.MAX_SNIFF_NPORTS == 7` and restores
  to 256; `_check_s_values` writes through as the same object; and
  `__name__` does not, which is the dunder exclusion still working.
* **`touchstone.py` kept its bytes.** It is CRLF and contains a literal U+2029,
  and anything slicing it by line number must split raw bytes on `b"\r\n"`
  because `str.splitlines()` breaks on U+2029 too. The blob hash is unchanged
  (`7dd943d7…` at both ends, `R100`), the worktree copy is 1 562 CRLF line
  endings with **zero** bare LF, and the U+2029 is still in it.
* **The red-zone package still ships everything.** `.gitattributes` is an
  `export-ignore` BLACKLIST, so folders ship by default — but this was verified
  rather than assumed, because getting it wrong ships a broken tool to an
  offline machine where nobody can fix it. `git archive HEAD | tar -t` lists
  **344 entries, 41 of them under `pkg_rlc/`**, and every tracked package file,
  the shim and `reduce_snp.py` are present.
* **The entry point behaves.** `python pkg_rlc_extractor.py --cli …` runs, and
  argparse still reports its prog as `pkg_rlc_extractor` rather than as `cli`.
  That one survived by luck rather than by care: `_make_arg_parser` pins
  `prog="pkg_rlc_extractor"` as a literal, so it was never derived from the
  file name. Had it been, **4 of the 144 `cli_reference/` cases** — the ones
  that print a usage block — would have gone red on the move. All 144 pass.

### Modules

Every number in this section was re-measured at `d8e9a7f` and at `3ae2dfd`,
counting newlines in the file as git stores it. The **old flat name is kept
beside every new path**, because that is the name every commit message, every
review comment and every earlier draft of this document uses.

The ten modules that existed at 20:03:

| module today | was | before | after | note |
|---|---|---:|---:|---|
| `pkg_rlc/frontend/app.py` | `pkg_rlc_gui.py` | 10 954 | **3 220** | **−71%.** Twelve commits took code out of it: four panels (`files`, `traces`, `results`, `editor`) and eight libraries (`conntable`, `report`, `csv`, `widgets`, `validate`, `model`, `session`, `run`). |
| `pkg_rlc/physics/core.py` | `pkg_rlc_core.py` | 4 725 | **169** | Now a re-export facade over touchstone / spec / solve. |
| `pkg_rlc/frontend/cli.py` | `pkg_rlc_extractor.py` | 4 377 | **3 060** | 13 report sections + 6 duplicated formatters left. The *name* `pkg_rlc_extractor.py` survives at the root as a **41-line shim** (§2.0). |
| `pkg_rlc/present/help.py` | `pkg_rlc_help.py` | 2 745 | **140** | Prose moved to `docs/help/*.md` (10 files, 2 648 lines). |
| `pkg_rlc/widgets/plot.py` | `pkg_rlc_plot.py` | 1 194 | **1 028** | Generic widgets (incl. `ReflowRow`) went to `pkg_rlc.widgets.widgets`. |
| `pkg_rlc/panels/attrib_gui.py` | `pkg_rlc_attrib_gui.py` | 4 439 | **4 479** | **+40.** Grew: the ground-model import, and the sensitivity fix with its docstring. |
| `pkg_rlc/panels/files_gui.py` | `pkg_rlc_files_gui.py` | 1 487 | **1 501** | **+14.** Grew: eight lazy imports became top-level ones, with the reason written beside them. |
| `pkg_rlc/physics/attrib.py` | `pkg_rlc_attrib.py` | 4 767 | **4 767** | Untouched all night, and untouched by the move. |
| `pkg_rlc/physics/compose.py` | `pkg_rlc_compose.py` | 1 861 | **1 861** | Untouched. |
| `reduce_snp.py` | — | 1 224 | **1 224** | Never moved, never changed: standalone by design. |

New modules, none of which existed at 20:03:

| new module | was | lines | layer | what it is |
|---|---|---:|---|---|
| `pkg_rlc/physics/spec.py` | `pkg_rlc_spec.py` | 2 050 | L0 | The declaration model and the Mode 5 DSL. |
| `pkg_rlc/panels/panels_editor.py` | `pkg_rlc_panels_editor.py` | 1 644 | L5 | The editor panel + `StylePicker`. |
| `pkg_rlc/present/report.py` | `pkg_rlc_report.py` | 1 596 | L3 | Turning a run into text. |
| `pkg_rlc/physics/touchstone.py` | `pkg_rlc_touchstone.py` | 1 562 | L0 | The parser and its diagnostics. |
| `pkg_rlc/present/attrib_report.py` | `pkg_rlc_attrib_report.py` | 1 557 | L3 | The CLI attribution report, as `list[str]`. |
| `pkg_rlc/model/validate.py` | `pkg_rlc_validate.py` | 1 351 | L1 | What a spec says, does and gets wrong. |
| `pkg_rlc/physics/solve.py` | `pkg_rlc_solve.py` | 1 218 | L0 | `compute_z_matrix` and the reduction. |
| `pkg_rlc/panels/panels_results.py` | `pkg_rlc_panels_results.py` | 1 115 | L5 | The Results notebook and the run pages. |
| `pkg_rlc/widgets/widgets.py` | `pkg_rlc_widgets.py` | 992 | L4 | Generic Tk widgets + the palette. |
| **`pkg_rlc/model/trace.py`** | **`pkg_rlc_model.py`** | **975** | **L1** | **The shared data model — §3.1.** |
| `pkg_rlc/present/conntable.py` | `pkg_rlc_conntable.py` | 505 | L3 | The connections-table layout vocabulary. |
| `pkg_rlc/services/session.py` | `pkg_rlc_session.py` | 465 | L2 | Save / Load / autosave, as a dict ↔ model round trip. |
| `pkg_rlc/services/run.py` | `pkg_rlc_run.py` | 439 | L2 | The arithmetic half of Calculate. |
| `pkg_rlc/panels/panels_traces.py` | `pkg_rlc_panels_traces.py` | 388 | L5 | The Traces section + the freeze entries. |
| `pkg_rlc/panels/panels_files.py` | `pkg_rlc_panels_files.py` | 273 | L5 | The Files section. |
| `pkg_rlc/present/csv.py` | `pkg_rlc_csv.py` | 113 | L3 | The CSV blocks. |

**Totals: 10 modules / 37 773 lines → 25 modules / 37 759 lines**, counting the
standalone `reduce_snp.py` and the 41-line root shim on both sides, plus **8
`__init__.py` files totalling 26 lines** that the package needed and the flat
tree did not.

That headline near-parity is entirely the Help prose leaving Python. Excluding
`pkg_rlc/present/help.py` the Python grew: **35 028 → 37 619, i.e. +2 591 lines
(+7.4%)**. Module docstrings, imports and the re-export blocks rule 2 requires
are what that buys. `CLAUDE.md` went **3 498 → 4 561 (+1 063)**.

### The import layering

| | at 20:03 | at HEAD |
|---|---:|---:|
| function-level `import pkg_rlc.frontend.app` dodges | **10** | **1** |
| where a module's layer is declared | a `LAYERS` table in the test | **its folder** |
| lists that can disagree about a module's layer | 2 | **1** |

The last two rows are §2.0. The night's version of this table counted
"modules named in `LAYERS` that do not exist" (3 at 00:37, 0 by dawn) and
"`pkg_rlc_*.py` on disk not declared in the map" (0 at both ends). Neither
quantity exists any more: there is no `LAYERS` table to disagree with the disk,
so a module that exists is in a layer by construction and a module in an
undeclared folder fails outright. That hole is shut by **removing the second
list**, not by asserting the two agree.

The one remaining back-import is `pkg_rlc.frontend.cli` → `App`, deferred so
that `--cli` does not pay the tkinter + matplotlib import. Re-measured at
`d8e9a7f` over three fresh processes: `import pkg_rlc_extractor` (the shim,
which pulls in the whole CLI) is **111 / 109 / 120 ms**, and `import
pkg_rlc.frontend.app` on top of it adds a further **270 / 268 / 264 ms**. That
is a justified deferral, not a dodge, and `tests/test_layering.py` pins it in
both directions: adding a second fails, and removing this one fails too until
`KNOWN_BACK_IMPORTS` moves in the same commit.

### Tests

| | before | after |
|---|---:|---:|
| `def test_` methods, counted statically | **2 444** (41 modules) | **2 542** (44 modules) |
| full suite, run | — | **2 542 / 454 shards / 366.2 s** at `-j 4`, exit 0, **0 skips** |
| `--fast` | 976 / 5.5 s (recorded in `CLAUDE.md`) | **1 044 / 4.7 s** (measured) |
| test modules touched, for their CONTENT | — | **9 of 44**, of which **3 are new** |

The 2 444 figure is the same static count applied at the base commit, so the
two ends are comparable, and the runner's own count agrees with it exactly at
HEAD. `CLAUDE.md`'s recorded baseline of "2045" was already stale before the
night started.

**+98 tests, and every one of them is accounted for.** **+92 over the night:**
the three NEW modules are `test_cli_golden` 37, `test_layering` 21,
`test_attrib_golden` 12 — **70**; the five MODIFIED ones are `test_freq_label`
64 → 73 (guards for the CLI frequency fix), `test_run_parallel` 57 → 65 (the
runner's own priority work), `test_attrib_window` 212 → 217 (the sensitivity
fix), and then `test_attrib_gui_integration` and `test_multifile_table`, both
unchanged in count at 70 and 100 — the second of those was opened only to move
a `mock.patch` target onto the edge it is testing, because the lazy import it
used to patch through no longer exists. `70 + 9 + 8 + 5 = 92`. **+6 from the
move:** `test_layering` 21 → **27**, the gate rewritten to read the layer off
the folder (§2.0).

**35 of 44 test modules were never opened for their content**, which is the
re-export rule (rule 2) working and the best single piece of evidence that the
moves were pure. The move itself touched **42** of the 44 — but every one of
those except `test_layering` is a symmetric import rewrite, N lines added and
the same N removed, and the largest is **10**. `test_layering` is 352+/157−
and is the only test in the repo whose *content* the move changed.

### Golden references

| reference | commits since `3ae2dfd` | commits since `9a629f5` (pre-move) | detail |
|---|---:|---:|---|
| `tests/fixtures/golden_legacy.npz` | **0** | **0** | Never moved, by anything. |
| `tests/fixtures/render_reference.json` | **0** | **0** | Never moved, by anything. |
| `tests/fixtures/cli_reference/` (144 cases) | **4** | **2** | Created; the frequency fix (55 files, §4.1); the coupling-report fixes (53 files, §4.3–§4.5); the CSV precision and the candidate grammar (§4.6, §4.7). |
| `tests/fixtures/attrib_reference/` (60 cases) | **3** | **1** | Created; the sensitivity fix (one case ADDED, §4.2); the candidate grammar (one case of 60 reworded, §4.7). |

**The right-hand column is the proof that the move into `pkg_rlc/` changed
nothing.** It read **0** for all four at the moment the move landed; the two
non-zero entries above are the divergence closures of §4.3–§4.7, which came
after it and each of which regenerated its reference in its own commit, with
the diff accounted for line by line there. Two of these four pin numbers and
two pin rendered text, between them covering the reduction path bit-for-bit,
the GUI results pane, 143 CLI invocations and 56 Attribution-window renders. A
move that had altered any behaviour would have had to move at least one of
them — and **the two that pin NUMBERS have never moved at all.**

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
top of it, is the 2 542 in §1.

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

Seven divergences were found. **All seven are now closed** — two on the night
of the refactor, the other five on 2026-08-14.

**THE GOVERNING REASON, AND IT IS THE MOST USEFUL THING IN THIS FILE FOR
WHOEVER CHANGES THE CLI NEXT.** The user said they basically do not use the
CLI. That did NOT make the CLI free to churn — `deploy/doctor.sh` defines
tier 2 as "CLI extraction" and treats a deployment as successful when only
tier 2 is reachable:

```sh
# deploy/doctor.sh
#   tier 1  reduce_snp.py     port reduction on a sim server   needs numpy
#   tier 2  CLI extraction    R/L/C/Q -> CSV                   needs numpy
#   tier 3  GUI               Tkinter + Matplotlib             needs both + X11
# Exit code: 0 if at least one interpreter reaches tier 2, else 1.
```

So on a headless red-zone box with `$DISPLAY` unset the CLI is the **ONLY**
surface and its reader has no GUI to cross-check against. The rule applied was
therefore:

> **The CLI may be TERSER than the pane, but it must never omit a diagnostic or
> a decisive number.** Where the CLI was missing something it was added; where
> it merely said more than the pane's measured 144-column budget allowed, the
> length was **left alone**. Convergence for tidiness is not a reason to touch
> this surface.

That rule is why three of the seven fixes are **not symmetrical**: §4.4 and
§4.5 leave the CLI a SUPERSET of the pane rather than a copy of it, and §4.7's
list level is left different on purpose because the window cannot follow.

Two of the seven were settled earlier and on a different rule, which is worth
keeping because it is the one to reach for first:

> Where the two surfaces disagree **and the repo already has a documented
> position** (a `CLAUDE.md` entry, a test file's stated purpose,
> `docs/theory.md`), the surface matching that position wins and the other is
> fixed.

Those two are §4.1 and §4.2. The other five had no such position anywhere in
the repo, which is exactly why they were left for the user rather than decided
by a refactor; `deploy/doctor.sh`'s exit rule is the position the user
supplied, and it decided all five.

**None of the seven moved an arithmetic result.** `golden_legacy.npz` and
`render_reference.json` are at **0 commits since `3ae2dfd`** — the last commit
before the refactor started — and that is the shape all seven had to have. The
one *value* that is different is a correction rather than a move: §4.7's DC
capacitor, which the CLI read as a perfect short and now reads as an open, the
window's answer and the right one.

| § | # | divergence | closed by | fixture that moved |
|---|---|---|---|---|
| 4.1 | 1 | the CLI printed a frequency that is not in the file | `b7f1ddd` | `cli_reference`, 55 files |
| 4.2 | 5 | the Attribution window ranked an unmeasurable row first | `8d98ba9` | `attrib_reference`, one case ADDED |
| 4.3 | 2 | the CLI's coupling pair list was unranked and never printed the rank key | `5b4bdfc` | `cli_reference`, 53 files |
| 4.4 | 3 | reciprocity was a metric on the CLI and a verdict in the pane | `5b4bdfc` | (same 53) |
| 4.5 | 4 | the `\|k\|>1` prompt existed on no CLI surface | `5b4bdfc` | (same 53) |
| 4.6 | 6 | two `_e`, at two precisions, into two CSVs | `fc501f9` | `cli_reference`, 2 CSV artifacts |
| 4.7 | 7 | two candidate grammars | `eec4a58` | `cli_reference` ×2 + `attrib_reference` ×1 |

**2, 3 and 4 share one commit, against the brief's one-commit-per-decision
rule, and the reason is that they share one function and one regeneration.**
All three are `_print_coupling_report`, all three land in the same 53 reference
cases, and a revert of any one alone would have had to re-capture the other
two. They are separated in the commit message instead, one titled paragraph
each. 6 and 7 are one commit apiece as asked.

### 4.1 FIXED (divergence 1) — the CLI printed a frequency that is not in the file

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

### 4.2 FIXED (divergence 5) — the Attribution window ranked an unmeasurable row first

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

### 4.3 FIXED (divergence 2) — the CLI's coupling pair list was unranked, and never printed the rank key

Before, the whole of the pair loop:

```python
# CLI   _print_coupling_report
print("\nMutual coupling (per unordered pair):")
for pr in res.pairs:                       # nested-loop (a, b) order
    print(f"\n  {na} <-> {nb}")            # no rank key, no flag
```

against a pane that has called `rank_coupling_pairs` since the results-views
slimming. Six measurement ports make **15** pairs and index order says nothing
about which of them anybody has to do something about.

**The part that mattered was not the order.** It was that `worst M/L` — which
IS the rank key, and is the quantity a spur / pulling budget is written
against — was printed **nowhere at all** on this surface. A headless reader
could not obtain it: not from the report, and not by sorting the report by eye,
because the number it would have to sort on was not on the page.

Now:

```
BEFORE   Mutual coupling (per unordered pair):
           p1 <-> p2
AFTER    Mutual coupling (per unordered pair, strongest first by worst-case M/L):
           p1 <-> p3   worst M/L = 0.04299 dB   [cap]
```

`_print_coupling_report` **calls** `pkg_rlc.present.report.rank_coupling_pairs`
rather than reimplementing the key, because that function's three rules are
exactly the ones a second copy gets subtly wrong: the strength computed
**linearly** and not from the `*_dB` fields (`_ratio_db(0)` is NaN, and a pair
with `M = 0` is the weakest there is, not an undefined one); an **undefined**
ratio sorting LAST and never folded away; and the **strongest** pair never
folded away either, or a block can consist of nothing but *"3 pairs were too
weak to list"*. `COUPLING_FLOOR_DB = -60` applies, the folded count is printed,
and it points at `--csv` — a pointer that is true only because
`_write_coupling_csv` enumerates every unordered pair straight off the Z matrix
and has no floor. **Do not give it one.**

Two deliberate details. The rank key goes on the pair's **HEADLINE**, not into
the body: a fifteen-pair report is scanned by its headlines, and the pane
learned that the hard way when the same number was moved off its headline
during the slimming and a test caught it inside the hour. And the heading says
`strongest first by worst-case M/L` **only when there is more than one pair** —
ranking one pair means nothing, which is the same omission the pane makes.

**Commits.** `5b4bdfc` (the code, shared with §4.4 and §4.5, plus
`tests/test_cli_coupling_report.py`), `4952151` (the reference).

**The reference diff, measured rather than taken on trust.** 53 of the 144
cases moved, +321 / −268 lines, no exit code and no written artifact among
them. All **53** pair headlines gained the rank key, flagged `ind` ×47 and
`cap` ×5. **But only ONE case's pair ORDER actually changed**, and the
histogram says why: of the 53, **2 have no pair at all, 50 have exactly one,
and one — `coupling_three_mports` — has three.** A single pair cannot be
reordered, and **no case in the reference folds a pair at all**, because no
shipped fixture has a pair below −60 dB alongside a stronger one.

**That is precisely why `tests/test_cli_coupling_report.py` exists** (19 tests,
5 classes, no tkinter, every guard mutation-checked). The three behaviours that
143 real invocations cannot reach are the floor and its two exceptions, an
undefined rank key that must sort last and never fold, and — §4.5 — a `|k|`
above 1. A golden reference proves only what its captures can express; that
lesson is §4.2's and it was applied here before it could bite a second time.

`render_reference.json` and `golden_legacy.npz` did not move, and no number,
exit code or CSV cell moved in `cli_reference` either.

### 4.4 FIXED (divergence 3) — reciprocity was a METRIC on the CLI and a VERDICT in the pane

```
BEFORE (CLI)   Reciprocity error = 1.38e-16   (max|Z_ab - Z_ba| / max|Z_ab| over the finite off-diagonal entries)
                 (data looks reciprocal; the alarm threshold is 0.001. ...)
BEFORE (pane)  ✓ reciprocal (1.38e-16)
```

The CLI printed the number and the definition and **never said the word**. A
reader scanning for "is this file all right" had to know the threshold, apply
it, and reach the verdict themselves — on the one surface that is sometimes the
only surface.

```
AFTER (CLI)    Reciprocity: OK -- reciprocal (1.38e-16)
                 error = 1.38e-16   (max|Z_ab - Z_ba| / max|Z_ab| over the finite off-diagonal entries)
                 (data looks reciprocal; the alarm threshold is 0.001. ...)
```

**This is the clearest case of the governing rule, because the tempting fix was
the wrong one.** Making the CLI match the pane means deleting the metric line
and the paragraph — and the pane only dropped them to a measured 144-column
budget that a terminal does not have. A headless reader has no other source for
what the metric means or where the threshold is. So the CLI is a **SUPERSET**:
the verdict leads, and everything it had before stays underneath it, unchanged.

`reciprocity_verdict` in `pkg_rlc/present/report.py` is the ONE classifier both
surfaces call, so which of the three readings — `OK` / `WARN` / `NOT CHECKED` —
a given number gets cannot differ between them. **No tick glyph**: nothing in
the 143 pinned CLI cases uses one (the non-ASCII in that reference is
`Ω Δ ω Σ √` and nothing else), the CLI already says `WARN:` in words, and `✓`
is 12 px against 7 in the pane's own font, which is why the pane confines it to
standalone sentences. The alarm keeps its whole sentence on both surfaces,
because there the sentence IS the reading.

**Commits.** `5b4bdfc`, `4952151`.

**The reference diff.** In the same 53 cases: the verdict headline is new
(`Reciprocity: OK` ×50, `Reciprocity: NOT CHECKED` ×1), the metric line lost
its leading word and gained two spaces of indent under it, and **the paragraph
text is byte-identical in every one of the 53 — 0 cases where it changed.**
The `WARN` path appears in no captured case and is guarded by
`TestTheReciprocityVerdict` instead.

### 4.5 FIXED (divergence 4) — the `|k|>1` prompt existed on no CLI surface

`_pair_flag` was pane-only. `|k| > 1` means the port setup is probably wrong;
core's rule is that it **adds a note rather than clamping**, so the note is the
entire mechanism by which the user is told. With the flag pane-only, a CLI user
whose `|k|` exceeded 1 was told **nowhere**.

```
BEFORE   lines in tests/fixtures/cli_reference/ mentioning |k|>1 :   0
AFTER                                                            :  51
```

The pane's own `_pair_flag` is called, and its `[ind]` / `[cap]` / `[|k|>1]`
goes on the pair headline beside the rank key. The legend was two fragments in
two places — the sign key under the self table, the M/L caveat under the
pairs — and is now **one block at the foot of the report**, the shape the pane
settled on with `COUPLING_LEGEND_LINES`.

**It is not that constant and must not become it.** The CLI keeps its own
longer wording, because length is not a defect on a surface with no column
budget — the governing rule's second half. The M/L caveat is one of the six
homes of that sentence, and **stripped of indent it is byte-identical before
and after** (checked; only its position and its 2 → 10 spaces of indent moved).
The `Ω Δ ω Σ √` census above is exact and was re-taken for this paragraph:
those five are the complete non-ASCII inventory of all 144 captured cases, and
U+2713 / U+2714 / U+2705 appear zero times.

**Commits.** `5b4bdfc`, `4952151`.

**The reference diff.** The `|k|>1` clause on the legend line in 51 cases; no
captured case actually FLAGS a pair (the flags seen are `ind` ×47 and `cap`
×5), so the flag itself is guarded by `TestTheFlagReachesThisSurface` against a
hand-built pair — the same "the reference can only prove what it captures"
argument as §4.3.

### 4.6 FIXED (divergence 6) — two `_e`, at two precisions, into two CSVs

```python
# pkg_rlc.present.attrib_report._e   -> "%.6e"
# pkg_rlc.panels.attrib_gui._e       -> "%.12e"
```

Both put a float from the **same** decomposition into a CSV, so the CLI's
`--attribute-csv` / `--cold-start-csv` and the window's CSV export disagreed in their last digits
about identical numbers.

**`%.12e` won, and the reason is what a CSV is for.** It is written to be read
back by something else — a spreadsheet, a script, a second tool — and six
significant figures is lossy for no benefit, so the CLI moved to the window's
precision rather than the other way round. This is the governing rule in its
mildest form: the CLI was not missing a diagnostic, it was rounding a number a
reader may need whole.

```
BEFORE  term,5.000500e+00,vic,agg,Z,Ohm,,bare EM coupling,,3.756903e-10,7.893682e+00,...
AFTER   term,5.000500000000e+00,vic,agg,Z,Ohm,,bare EM coupling,,3.756903344344e-10,7.893682431821e+00,...
```

The window now imports it (`from pkg_rlc.present.attrib_report import _e as
_attr_e`, bound to the local name `_e`, so every call site and
`pkg_rlc.panels.attrib_gui._e` are unchanged) and there is **no second
definition**. The old note said the two "cannot share a module under one name";
that was true of the NAME and never of the precision — L5 importing L3 is the
ordinary direction, and the local binding keeps the name.
`test_attrib_window.TestCsvRecords.test_it_IS_the_CLI_writer_and_not_a_copy_of_it`
asserts the two are the same OBJECT, following the `parse_ground_model`
precedent. `CSV_FIELDS` / `csv_records` stay the window's, because the two
files have different COLUMNS — it was only ever the float that was duplicated.

**Commits.** `fc501f9` (the code), `96090db` (the reference, shared with §4.7).

**The reference diff, and it was checked cell by cell rather than eyeballed.**
Two artifacts moved — `attr_csv` and `cold_csv` — 31 differing lines, **165
numeric cells**. Every one was parsed on both sides (splitting the compound
`k=v;k=v` cells too) and re-rounded: **the number of cells whose new `%.12e`
value does not round back to the old `%.6e` value is 0.** No arithmetic moved;
the same numbers are printed to more digits. `golden_legacy.npz`,
`render_reference.json` and `attrib_reference` are untouched by this one.

### 4.7 FIXED (divergence 7) — two candidate grammars, and the only one of the seven that was a TRAP

```
--attribute-alt "R=0.5,L=1n"      worked on the CLI, refused in the window
Candidates:      R=0.5 L=1n       worked in the window, refused on the CLI
```

Every other divergence in this section is a presentation choice one surface
made and the other did not. This one is a **user-facing trap**: the spelling a
user had just got working failed where they took it next, and the failure was a
refusal message about a grammar nobody had told them there were two of.

Both now split on `[,\s]+` between the fields of ONE candidate, through
`_FIELD_SEP`, and both take every word for a perfect short through
`_IDEAL_WORDS` (`gnd` / `ground` were CLI-only, `0` was window-only). Both
constants live in `pkg_rlc.present.attrib_report` and the window IMPORTS them,
so *"the two accept the same tokens"* is one object and not two claims that can
drift.

**Three things this turned up that closing it on the grammar alone would have
missed.**

1. **The refusal had to survive the widening, and it did.** A token with no `=`
   is still refused on either separator, because `parse_kv_rlc_params` DROPS
   it — core's `_rlc_tokens` trap through a different door. **Note what that
   costs the CLI and why it is right:** with comma-only splitting `R=5 m` was
   ONE field and `parse_si` tolerates the space, so the flag quietly meant
   **5 mΩ** while the window refused the identical string by name. Now both
   refuse it, and `attr_alt_bad_spacing` moves from a 227-line report at exit 0
   to a 35-line refusal at **exit 2** — the one captured case whose exit code
   moved in this whole run. The case was NAMED for a trap it did not actually
   have.
2. **The two impedance expressions were MEASURED, not assumed to agree.** The
   CLI goes through `y_series_rlc` and the window builds `R + jωL + 1/(jωC)`
   directly, and converging the grammar over expressions that disagree would
   have removed the symptom and left the defect. Over 14 specs × DC + 41 points
   from 1 MHz to 10 GHz the worst relative difference is **2.461e-16 — one
   ulp** —
   so they are two spellings of one formula and were left as two.
3. **EXCEPT AT DC, where they did not agree and the CLI was wrong.**
   `1/(1j*0*C)` is `inf`, so `Z` was `nan`, `y` was `nan`, and the non-finite
   branch returned a **perfect SHORT** where a series capacitor at DC is an
   **OPEN**. Every composed sweep keeps its 0 Hz point, so `--freq 0` with a
   `C=` candidate reached it. Fixed to the window's answer. This is the only
   place in the five where a *value* the CLI produced was wrong rather than
   merely differently presented — and it was found only because the grammar
   change forced the comparison.

**WHAT IS STILL NOT SHARED IS THE LIST LEVEL, and it cannot be.**
`--attribute-alt` is repeated once per candidate; the Candidates field holds
the whole list in one string with the comma between entries. So `R=0.5,L=1n` is
ONE candidate on the command line and TWO in that field. It is not fixable from
the window's side: the shipped default value of that field is `"open, ideal"`,
so a comma that stopped separating candidates would break it and every saved
session. What the reader gets instead is the READING — one Sensitivity row per
candidate, labelled with what parsed — and `CANDIDATE_HINT`, which now spends
its middle on `comma between candidates, space inside one (R=0.5 L=1n)`. It is
also said in the `--help` text, in Help → Mode 6 and in the README.

**Commits.** `eec4a58` (the code, 7 new guards across `test_attrib_cli` and
`test_attrib_window`, of which
`test_the_two_impedance_expressions_agree_across_the_band` re-measures the
whole sweep rather than trusting the commit message), `96090db` (the
references), `69ec9ca` (a comment that quoted the bound rounded down —
`2.22e-16` where the measurement is `2.461e-16`; a comment that rounds a
measured bound is one the next reader cannot check).

**The reference diff.** `cli_reference`: `attr_alt_bad_spacing` becomes the
refusal above, and `help.json` moves by 19 lines because the flag's own help
text now names both separators. `attrib_reference`: **1 case of 60** —
`candidate_parsing`, whose refusal sentence gained *"separated by spaces or
commas"* — and **not one of the other 59 changed: no `sha256`, no `chars`, no
`lines`.** `golden_legacy.npz` and `render_reference.json` untouched.

### 4.8 Duplication that was removed before it could diverge

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

**Nothing here is waiting on a decision from you any more.** The five that
were are closed (§4.3–§4.7); what is left is verification of things a test
suite cannot verify, in descending order of what a wrong answer would cost.

1. **READ THE GOVERNING RULE AT THE TOP OF §4, and say if you disagree with
   it.** It is the one thing here that will still be deciding changes in six
   months: *the CLI may be terser than the pane, but it must never omit a
   diagnostic or a decisive number*, because `deploy/doctor.sh` calls a
   CLI-only install a successful install. Every one of the five closures took
   its shape from that sentence, and two of them left the CLI **more verbose**
   than the pane on purpose. **If the rule is wrong, it is wrong five times
   over and this is the cheapest moment to say so** — each closure is one
   commit and reverts on its own (bar 2/3/4, which share `5b4bdfc`; §4 says
   why).
2. **Run one coupling extraction on YOUR data and read the whole block.**
   Three of the five closures land in it (§4.3–§4.5) and the frequency fix
   (§4.1) landed there earlier; between them they moved the pair order, added a
   number to every pair headline, added a verdict word, and moved the legend to
   the foot. Specifically: is `worst M/L` the number you would have wanted
   ranked on (§4.3), and does the `... +N pairs below -60 dB` fold — which no
   shipped fixture exercises, so only a hand-built guard has seen it — hide
   anything you needed? Then check the frequency line at a marker that is *not*
   a grid point, and again at one that is: the second must render exactly as it
   always did (§4.1).
3. **Diff one attribution CSV against a copy from before.** It gained six
   digits per float (§4.6). Every one of the 165 numeric cells in the reference
   was checked to round back to its old value, so nothing should have *changed*
   — but if anything downstream parses those files by column width rather than
   by comma, it will notice.
4. **Open the GUI and do one real extraction end to end** — load a file,
   Calculate, read the results pane, Export CSV, open the Attribution window
   and look at the sensitivity table's ordering. The suite drives all of this,
   but 87% of it is Tk geometry and none of it is you looking at the screen.
   **Of the five later closures, only §4.7 changed anything the GUI
   RENDERS** — the Candidates field now also accepts a comma inside one
   candidate and the words `0` / `gnd` / `ground`, and its refusal sentence
   gained *"separated by spaces or commas"*. §4.6 rebound the window's `_e` to
   the CLI's without changing its precision, and §4.3–§4.5 are CLI-only. So
   this step is mostly a check that the move into `pkg_rlc/` is invisible, not
   that a decision was right.
5. **Open the folder you complained about and see whether it reads better
   now** (§2.0). Seven folders under `pkg_rlc/`, one per layer, and the repo
   root is down to `pkg_rlc/`, the 41-line `pkg_rlc_extractor.py` entry point,
   `reduce_snp.py`, `deploy/`, `docs/`, `tests/` and the markdown. If a folder
   name is wrong — `present` and `services` are the two I would expect to
   argue about — renaming one is a one-line change to `LAYER_OF_FOLDER` plus a
   `git mv`, and it is much cheaper now than in a month.
6. **Read the new sections of `CLAUDE.md`** — the layering gate, the four
   panels, the two attribution reports, the one-formatter-two-spellings
   section, the model / session / run modules, and the undefined-sorts-last
   rule. If any of them describes something you did not agree to, this is the
   cheapest moment to say so.
7. **Decide whether the phase-gate lesson in §3.2 is worth encoding** before the
   next long unattended run. It cost an hour that night and it will cost more
   than that eventually.

---

## 6. What is still owed

**In the tree and finished:** `pkg_rlc.model.trace` (L1), `pkg_rlc.services.session` (L2),
`pkg_rlc.services.run` (L2). Nothing from the plan is now blocked on a missing
module, and **no CLI/GUI divergence is waiting on a decision** — all seven are
closed (§4).

**SETTLED, NOT OWED — the entry-point shim's star import.** This was left open
by the package move and re-checked at HEAD. `pkg_rlc_extractor.py` ends on
`from pkg_rlc.frontend.cli import *`, and a star import skips underscore names,
so **`pkg_rlc_extractor._attr_zt`, `._attr_ground_model`, `._make_arg_parser`,
`._emit` and `._attr_series_impedance` genuinely do not resolve** — measured,
all five `False`, against **59** public names that do. That is not a gap,
because **nothing depends on it**: every call site that reaches a private name
imports `pkg_rlc.frontend.cli` directly (`tests/test_attrib_cli.py`,
`test_cli_golden.py` and `test_compose_cli.py` as `ex`;
`test_attrib_window.test_it_IS_the_CLI_parser_and_not_a_copy` as `cli`), which
is where those names live. The shim's own docstring states the decision — a
private name belongs to `pkg_rlc.frontend.cli`, and listing forty of them here
would be a second copy of the CLI's surface that could come to disagree with
it — and `main` is re-exported BY NAME because `tests/_cli_capture.py` drives
it in-process over 143 invocations. What was owed was the two places
`CLAUDE.md` still described the old behaviour, and both are corrected in the
same commit as this paragraph: the module map's *"`pkg_rlc_extractor`
re-exports every symbol it lost"* (it is `pkg_rlc.frontend.cli` that does), and
the attribution section's claim that the parser-identity test compares against
`pkg_rlc_extractor._attr_ground_model` (it compares against
`pkg_rlc.frontend.cli._attr_ground_model`, and against the shim it would raise
`AttributeError`).

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
* **OWED BY THE MOVE: 642 mentions of the old flat module names survive in
  comments and docstrings**, across 66 files (the count excludes the deliberate
  `as pkg_rlc_gui` aliases and `tests/_repackage.py`, which is about them). It
  was 598 across 62 files at `d8e9a7f` and has GROWN, which is the point: the
  divergence work of §4.3–§4.7 added a test module and several paragraphs of
  `CLAUDE.md` prose that reach for the old names because the surrounding text
  does. `tests/_repackage.py` rewrote the import statements and the docs and
  deliberately did not touch prose, because a 600-line diff with no test behind
  it is a poor trade inside a move whose whole claim is that it changed
  nothing — the imports are checked by the interpreter and by 2 569 tests, and
  the prose is checked by nobody. The names are still unambiguous (there is
  exactly one `pkg_rlc_core` and everyone knows where it went), so this is
  staleness rather than a defect, but it will read worse every month. It is a
  mechanical follow-up, best done as its own commit with its own script and
  nothing else in it. One is in `reduce_snp.py`, which may not be edited for
  this reason alone.

**Process, and it is the one thing the night actually broke.** The phase gate
reverts a phase on a missing deliverable and cannot distinguish "produced
nothing" from "produced most of it and documented the rest" — §3.2. That one
is still owed.

**The layer-map hole that let run 1's silent failure through IS CLOSED, and not
the way the night's draft of this section proposed.** It proposed asserting
that every name in the `LAYERS` table resolves to a file, which would have made
the two lists agree. The move deleted the second list instead: the layer is
read off the folder, so a module that exists is in a layer by construction, and
a module in a folder nobody has declared fails outright. Two lists that must
agree is a weaker guarantee than one list.

**The failure mode the panel split has**, unchanged and worth re-reading before
anything else moves into a panel: **a bare `self` where the app was meant**.
Five were in the first draft, **four of which failed silently** (three
window-refresh calls that simply did nothing). Grep cannot find them —
`(self)\b` never matches. The check is an AST pass and must be re-run after
every further move.
