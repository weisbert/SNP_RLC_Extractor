# Compare files (`pkg_rlc/panels/ws_compare.py`, `pkg_rlc/present/compare_report.py`, `pkg_rlc/physics/similarity.py`)

*Added 2026-09-30 for the modeless Compare window; rewritten 2026-10-03 when
that window became the third WORKSPACE (stage 3 of
`docs/design_workspaces.md` § 4). As binding as every other dossier here.
Every rule of the window that is still true is below with its measurement;
the window-specific ones are rewritten for the workspace and marked as such.*

### Compare files — one reference against N files, over the band each pair shares

The owner's question: *an inductor extracted to 30 GHz and again to 50 and
80 GHz — inside the band they share, are the others the same as the 30 GHz
one?* An EM solver sizes its mesh from the highest frequency it is asked for,
so the runs are different discretisations of one layout and need not agree.
The workspace answers with numbers, at two levels, each failing on its own:

- **The FILE:** the largest `|S_B - S_A|` over every entry, per frequency. A
  Z0 mismatch is renormalised away through Y; a port-count mismatch is
  refused, and refuses ONLY this level.
- **What was EXTRACTED:** a port setup THE USER DEFINED (measurement ports +
  connection rows — the one row model) solved on each file through
  `build_terminations_rows(mports, conn_rows, "", nports=fe.ts.nports)` +
  `compute_z_matrix`, and the signed difference of L, Q and R, B against A.
  **There is no default setup** (below).

**Three modules, one job each, and the panel computes nothing.**

- `pkg_rlc/physics/similarity.py` (L0; imports `core` and `compose` only): the
  pairwise maths — `common_axis`, `compare_s`, `compare_z`, `ZCompare`, the
  three exclusion rules and their constants.
- `pkg_rlc/present/compare_report.py` (L3; imports `similarity`, `core` and
  `model.validate` only — **no tkinter, no matplotlib**, asserted in a child
  process by `tests/test_compare_report.py::TestNoTk`): `compare_pair` /
  `compare_against` (the computation, with no App), `pair_verdict` /
  `verdict_strip_lines` (the strip), `compare_summary_lines` (the reading),
  `s_matrix_lines`, `line_tags`, `pct_view_span`, `not_judged_spans`,
  `default_reference`, `trace_setup_choices`, `copy_trace_setup`. These are
  the pure functions that lived in `compare_gui.py`, moved down a layer so
  they are testable with no display.
- `pkg_rlc/panels/ws_compare.py` (L5): `CompareWorkspace(app, left, right)`,
  built into two frames the App owns exactly as the Trace model workspace is.
  **It imports `pkg_rlc.frontend.app` NOT AT ALL and calls no `messagebox`**
  (`tests/test_ws_compare.py::TestTheModule` reads the source for both).

**`pkg_rlc/panels/compare_gui.py` is deleted, and so is every route to it**:
the Analyze → `Compare files…` item, the Files list's right-click item, and the
`refresh_compare_windows` calls in `app.py` and `panels_files.py`.
`TestItIsTheThirdWorkspace::test_the_old_routes_are_gone` and
`TestTheModule::test_the_old_window_is_gone` pin both halves. The old
window's two bugs went with it: Refresh after a file removal compared from
the arrays it had kept, and Clear All / a session load never told it.

Rules, each pinned by `tests/test_compare_report.py` (pure) or
`tests/test_ws_compare.py` (a real App):

- **ONE REFERENCE AGAINST N FILES, EACH PAIR ON ITS OWN.** Every ticked file
  B_i is compared with the reference A through `compare_pair` — its own
  overlap band, its own grid — and gets its own strip line and its own curve
  on every panel. `compare_s` / `compare_z` are pairwise functions with no
  global state, so nothing is shared between pairs
  (`TestOneReferenceNFiles::test_each_pair_is_computed_on_its_own`,
  `TestTheFiles::test_N_ticked_files_give_N_verdict_lines_and_N_curves_per_panel`).
- **THE BAND IS THE OVERLAP. Nothing is extrapolated.**
- **THE GRID IS THE COARSER FILE'S points inside the overlap**, and the finer
  file is interpolated onto it — the OPPOSITE of `compose.align_frequencies`,
  deliberately: interpolating the coarse file chords it across its own gaps,
  and that chord error would read as a difference neither file contains.
  Identical grids (relative 1e-9) are not interpolated at all.
- **A relative difference is not computed where the reference is near its own
  zero** — below `NEAR_ZERO_FRAC` (1 %) of its MEDIAN |value| in the band — and
  the excluded count is printed. The median, not the maximum: R climbs four
  decades into a self-resonance and 1 % of that peak discarded every ordinary
  low-frequency R point (measured in the first draft, caught by
  `test_ordinary_R_is_not_mistaken_for_zero`).
- **The limits are DEFAULTS the reader edits** (S 1 % of full scale = −40 dB,
  L 1 %, Q 5 %; R has no limit and is shown for reference). How close is close
  enough is the design margin's call, so the workspace always shows the worst
  value, where it is, and the curve under the limit lines — never only a
  verdict. **Editing a limit re-judges the CACHED results without solving
  anything** (`TestLimitsAndMarkerSolveNothing`, which patches the solver);
  a keystroke in a limit box re-judges on its own after a 150 ms debounce. A
  box that does not read as a number falls back to its default rather than
  refusing the reading (`limits_from_text`).
- **The reference defaults to the file with the LOWEST top frequency** — the
  one whose whole sweep is inside the others'. After Add File the selection
  is always the last file, so "the selected one" would make the 80 GHz run
  the reference of the question about it. The user can pick another in the
  Reference box, and **a pick sticks** when files are added; it is re-defaulted
  only when the picked file is no longer loaded
  (`TestTheFiles::test_the_users_pick_sticks_when_a_file_is_added`).
- **The S level shows EVERY entry, not one number.** The worst entry, how many
  of the n² entries are over the limit, the largest few (`S_RANK_SHOWN = 5`;
  identical entries are not ranked), and — up to `S_MATRIX_MAX_PORTS = 32`
  ports — the whole matrix of each entry's largest difference in percent, `*`
  over the limit, `.` identical (below −200 dB). An entry is ALWAYS written
  `S(i,j)`: the first version printed `S1415` for S(14,15), which the owner
  could not read and which is ambiguous from ten ports up.
- **A percentage axis is not scaled by one wild point** (`pct_view_span`): it
  covers the 98th percentile, never less than twice the limit, and counts what
  it leaves off. A near-open port read −4e8 % at 1 MHz and flattened a band
  that sat inside ±2 %. The worst value is still in the text.

### No default setup, and a setup is the user's own

- **NO DEFAULT PORT SETUP.** The window gave a freshly loaded file "port 1 to
  ground", and that default — which nobody chose — decided the big verdict
  (`design_workspaces.md` § 0). Now, with the setup tables empty, or with
  **Raw S-parameters only** chosen, the extracted level says
  `No setup defined` (`compare_report.NO_SETUP`) and only S is judged; the
  strip line reads `(raw S-parameters only -- no setup defined)`. Connection
  rows with no measurement port also count as no setup — nothing is measured
  (`setup_is_empty`). Choosing Raw S greys the setup tables and the copy
  button (`set_editable(False)`); it does not clear them.
- **The setup component is the shared `SetupTables`**
  (`pkg_rlc/panels/setup_tables.py`): the measurement-port table
  (Name / + ports / − ports), the connections table with the fixed line
  `Ports not listed anywhere are OPEN.`, the Template box, and an issues line.
  Same columns, layout function and colours as the RLC editor's two tables —
  a port of how that editor builds them, not a second design. **The RLC
  editor still builds its own two tables in this stage; moving it onto the
  component is deferred.**
- **"Copy setup from trace…" is a ONE-SHOT deep copy.** It lists every
  single-file trace as `[id] label`; picking one copies its migrated `mports`
  + `conn_rows` into the tables (`copy_trace_setup`, which migrates a COPY —
  the trace itself is not touched), switches to the extracted choice, and says
  in the status line that it is a copy. **Nothing links the two afterwards**:
  editing either leaves the other alone
  (`TestCopySetupFromTrace::test_it_copies_once_and_does_not_link`). A
  trace's extra text lines, which the tables cannot hold, are not copied and
  the status line counts them. **Composed traces are not offered**: their port
  numbers name ports of several files, which no one file has alone. With
  none to offer, the menu says `(no single-file trace to copy from)`.
- **A REFUSED SETUP is painted in the cells AND is the extracted level's
  "why".** The cells are painted from `probe_rule_issues`, the connection
  rows' own parse and any `+` / `−` cell that does not parse — red `#b00020`
  refused, amber `WARN_FG` computed-but-read-this — never a dialog. A setup
  with a red cell is **not handed to the solver at all**: the reading would
  otherwise show a number the cells call refused. S is still compared, and
  when S is within its limit the `✓` line says `raw S-parameters only -- the
  setup is refused -- …` (`TestARefusedSetup`). A setup that parses but one file cannot hold (a port
  past its port count) is refused by the builder and its reason, naming the
  file, is the "why" in the same way.

### The workspace (stage 3; replaces the window's surface rules)

- **It is the third button on the workspace strip** (`Compare files`, key
  `"compare"`), registered by the App with one more
  `WorkspaceSwitch.register` call and nothing else in `workspaces.py` moved.
  The Loaded Files panel stays shared above; the left column is the setup,
  the right side the results. There is no menu item and no right-click item
  any more, and no accelerator.
- **It re-reads the loaded files on EVERY change.** `App._refresh_file_combobox`
  — the one path every add, remove, relabel, Clear all files, Clear All and
  session load goes through — calls `refresh_files()` on both the Trace model
  and the Compare files workspaces. A compared file that is gone loses its
  tick, its strip line and its curves AT ONCE; removing the reference drops
  every result (`TestRemovingAFile`, three tests including Clear all files).
  One Checkbutton per loaded file other than the reference, keyed by the
  `FileEntry` itself, so a relabel keeps its tick; a file not seen before
  starts ticked.
- **STALENESS, never auto-recompute — the Trace model workspace's rule.**
  Nothing is solved except by the Compare button. Changing the reference,
  the ticked files, the What-to-compare choice or the setup — or the loaded
  files under them — makes `is_stale()` true, and the verdict strip's FIRST
  line then says `Out of date: the files, the reference or the setup changed
  since this was computed -- press Compare.` (Details says it too). The
  results stay on screen with their old numbers until Compare.
- **Limits re-judge and the marker re-reads; neither solves.** The cached
  `CompareResult`s carry everything a verdict and a marker reading need; a
  limit change re-renders, and a marker change sets `marker_hz` on each cached
  result and moves the marker lines.
- **The marker belongs to the workspace.** Its own `Marker [ ] GHz` field
  beside the limits, and a press on the plot moves it, a drag follows the
  pointer, and the release snaps it to the nearest point of the REFERENCE's
  own sweep — nearest in log f when the axis is log (`snap_marker_hz`, the
  log-space rule CLAUDE.md sets for the RLC plot's drag) — and writes the
  field. **It no longer reads the RLC workspace's `rlc_freq_var`**, and the
  reading no longer says "(set in the main window)".
- **The verdict strip: one line per compared file, a mark and ONE sentence.**
  `✓` the same within the limits, `✗` not the same (the line is red), `?`
  could not be compared (no overlap, say) — the third mark is not in the
  design, which names only the first two. The sentence of a `✗` line names
  the first thing over its limit in words and counts the rest, e.g.
  `✗ ind_80G/L.s1p: NOT the same in 100 MHz - 30 GHz: its inductance L is
  1.4 % higher than the reference (worst at 29.9 GHz).` The strip is a
  wrapped `tk.Text` as tall as its wrapped lines, 2 to `STRIP_MAX_LINES = 7`,
  then it scrolls: a sentence cut off at the pane edge is a verdict the
  reader does not get. `_judge` is the ONE place a verdict is decided, so the
  strip and the reading cannot disagree.
- **The plot: ΔS, ΔL, ΔQ, ΔR (%), one curve per compared file.** One colour
  per compared file (the reference is the zero line), one dash per
  measurement port of a multi-port setup; legend labels cut at 30 characters
  (CLAUDE.md's legend rule). ΔS is on a log axis in % with `1 %`, `0.1 %`
  ticks, floored at 0.0001 %; the L / Q / R panels appear only when some pair
  has an extracted result; R has no limit lines. The not-judged regions are
  shaded grey and labelled (`not judged (resonance)` on L and Q,
  `not judged (A lossless)` on Q and R). A `Log frequency axis` check sits
  under the plot.
- **Details is collapsed by default** (`▸ Details` / `▾ Details`): the full
  reading of every pair, each under a bold header
  `<B>  against the reference  <A>`.
- **CLAUDE.md invariants it keeps:** the plot canvas gets `focus_set()` after
  its `FigureCanvasTkAgg` (the window had lost it); the legend truncation;
  the log-space drag distance. Entering the RLC workspace again hands the
  RLC plot its focus back, pinned with real M / V / Delete key events
  (`TestThePlotKeysSurviveTheWorkspace`).

**The left column is a measured budget** (Microsoft YaHei UI 9, tk scaling
1.333; the left host is 460 px wide and, under the shared Files panel, 421 px
tall at the 1040x600 minsize and 721 at 1500x900), and the numbers live in
`CompareWorkspace._build_left`'s docstring. The Trace model column's three
rules again: the Compare button, the status line and the limits row are
packed FIRST at the BOTTOM of a column only as tall as it asks, so on a short
window the setup tables give, never the button; each table shows two rows
before it scrolls; and the copy button rides in the measurement-port table's
own `+ Add` row (`table_head`). Measured: the first draft (choices on two
rows, Limits in a titled LabelFrame, the copy button in the Template row)
asked 478 x 472 — 18 px too WIDE, the copy button cut at the edge — and
squeezed the tables from 196 to 145 px at 1040x600. As built it asks
450 x 393; at 1040x600 Compare ends at 391 of 421 and the tables get their
full 194 px; in the worst case (four rows in each table, seven issue lines)
Compare still ends at 419 of 421 with the limits on screen. Right side at
1040x600: strip 74 px for two files, plot 567 x 462 with Details closed.
`TestTheLayoutBudget` re-measures it.

### Q and R are not judged where the reference is lossless (`RE_JUDGE_FRAC`)

**The owner's Q plot was a ±100 %-and-beyond SQUARE WAVE.** On a lossless
part, `Re Z` is whatever the file's precision and the solver's noise left in
it, `Q = Im/Re` is that noise inverted, and the relative difference of two
noises flips sign from point to point. So, besides the median rule and the
resonance rule:

- **Where `|Re Z_A| < RE_JUDGE_FRAC · |Z_A|` (`RE_JUDGE_FRAC = 1e-3`, i.e.
  Q_A above about 1000), Q and R are not judged**: those points are `nan` in
  `dq_pct` / `dr_pct`, kept out of every worst case and verdict, shaded grey
  on the ΔQ and ΔR panels (`not_judged_spans`), and COUNTED
  (`ZCompare.qr_not_judged`, `ZCompare.n_not_judged`). Details says
  `(N of M points: Q and R not judged -- A is almost lossless there …)`, and
  when it holds over the whole band IN SHORT says `Q and R are not judged
  anywhere` and the marker row reads `Q: not judged (A is lossless)`.
- **The REFERENCE decides where the ratio is not computed** — but a B that
  clearly HAS loss there is not hidden behind A's noise. Where A is lossless
  and B's `|Re Z| >= LOSS_MARGIN · RE_JUDGE_FRAC · |Z|` (`LOSS_MARGIN = 10`,
  i.e. Q_B below about 100, ten times clear of the worst measured noise floor
  1.4e-3) the pair is ✗ with "B has loss where the reference is lossless (Q
  below about 100 at N points …)" — a count, not a percentage, because there
  is no ratio against a zero (`ZCompare.n_b_lossy`,
  `test_a_lossy_B_against_a_lossless_A_is_not_hidden`). The first version
  masked on A alone and read ✓ for A lossless against B at Q = 100, ✗ with the
  two swapped (stage-3 review, 2026-10-03).
- **A ✓ says what it did not judge**: "Q and R not judged at N points where
  the reference is lossless" rides on the strip line.
- **Known limit:** at 1e-4 relative S noise (EM-like), 3 of 200 points of a
  lossless reference still pass the threshold, and a 0.4 % L change in B can
  then read ✗ on Q through those few points. Below that noise level (1e-5, or
  6 written digits) nothing is left. Recorded, not fixed: a minimum-run rule
  would hide a real narrow-band difference just as easily.
- **The points are set to `nan` BEFORE `_rel_pct`**, so the median-near-zero
  rule neither sees them nor counts them as its own; the two counts stay
  separate (`test_the_count_is_its_own_not_the_median_rules`).
- **L is untouched** — it is `Im Z / ω` and does not care about `Re Z`.

**The value is MEASURED** (2026-10-02, the C1 implementer's
`measure_eps.py`; the summary is the constant's comment in `similarity.py`,
which is the copy to keep current):

- The lossless cases — `diff_pair_4port`'s loop (P1 +1 −2, short 3,4), the
  same probed single-ended, a pure 1 nH — each against a B with L +0.4 %
  read dQ up to **2e5 %** before, the sign flipping at about half the points.
  Their `|Re Z| / |Z|` is the file's noise floor: **≤ 9e-8** written at 10
  significant digits, **≤ 2.0e-4** at 6 digits, **≤ 3.5e-4** with 1e-5
  relative noise on S, **≤ 1.4e-3** with 1e-4 noise (EM-like).
- **At 1e-3** every 10- and 6-digit lossless loop and pure-L case is cleared
  completely; at 1e-4 S noise 3 isolated points of 200 remain — no longer a
  wave. 3e-4 still leaves 12 points up to 6e4 %; 1e-4 leaves the 6-digit
  wave.
- **Real losses are untouched at 1e-3**: the tests' synthetic RLC at 30 vs
  80 GHz meshes (min ratio 9.3e-3), the coupled 4-port coil with R1 +3 % (dQ
  −2.9 % still caught), and a 1 nH at Q = 30 / 100 / 300 at 5 GHz with R +5 %
  (0 points excluded, dQ −4.76 % caught each time).
- **The cost:** at Q = 1000 half the band is not judged (the low half still
  catches the −4.76 %). 3e-3 would start hiding a Q = 300 part's top band and
  1e-2 hides half of an ordinary Q = 100 one.
- **What it does not cure:** the near-open single-ended probe of the 6-digit
  diff pair (P1 +1, everything else open; `|Re Z| / |Z|` up to 0.16) still
  keeps 3 of 150 points with |dQ| up to 221 % and |dR| up to 219 %. That
  noise is past any usable ε — even 1e-2 keeps one point. Recorded, not
  fixed; design § 8 item 5 asks for these numbers to go to the owner.

`TestLosslessQIsNotJudged` (nine tests) pins all of it, including
`test_the_constant_is_the_measured_one`. Mutation-checked by the
implementer — five of the seventeen mutations run against
`compare_report` / `similarity`, all killed: ε = 0, ε judged on B instead of
A, ε = 1e-1, the count dropped, the excluded points counted by the median
rule.

### The reading is written for someone DECIDING (2026-09-30, second pass)

The owner, on the first version: *"我希望这个软件里面的解读，是人能看懂的样子，
现在这个样子根本看不懂"*. It printed the measurement (`worst |S_B - S_A| = -39.5
dB ... S1415 ... limit -40 dB -> DIFFERENT`). `compare_summary_lines` is now
ordered as a reader asks, and `tests/test_compare_report.py::TestSummary` pins
it (ported from the deleted `tests/test_compare_files.py`):

- **IN SHORT first**: THE SAME / NOT THE SAME, then one bullet per thing over
  its limit, in words ("inductance L: B is 5.9 % higher than A below 9.44 GHz
  (worst at 9.4 GHz)"), then the numbers AT THE MARKER FREQUENCY — the working
  frequency is what a designer signs off on. Then WHAT TO DO NEXT, conditional
  on what was found. The evidence (what was compared, the raw file, the
  inductor, the port-pair table) comes after, never before. The Details pane
  prints this reading once per compared file.
- **No dB and no scientific notation anywhere in the text.** The S limit box is
  in PERCENT of full scale (1 % = −40 dB; `sim.db_to_pct` / `pct_to_db`). The
  S plot is in % on a log axis with `1 %`, `0.1 %` ticks, floored at
  0.0001 %.
- **`_num` never prints `1e+02`** (stage 3 fix): from 10 up a percentage is a
  whole number, so 99.5–100 prints `100`; below 10 it is THREE significant
  digits (two printed 1.04 as `1` beside a 1 % limit and still called it
  OVER — the old dB table's −39.6-as-−40 contradiction again); below 0.001 it
  is `<0.001`. A B that is about zero where A is not reads "about zero where
  A is not", never `-4e-06 × A`. The S-matrix table had the same
  disease (`%.2g`) and now goes through `_num` too
  (`TestFormattingFixes::test_99_5_to_100_is_never_1e_plus_02`,
  `test_the_s_table_has_the_same_cure`).
- **From +100 % up, a signed L / Q / R difference is a MULTIPLE**, as
  `report._delta_cell` does past ×10: `B is 3.8 × A`, and a sign flip is
  `-1.5 × A (the sign flipped)`. A percentage past 100 is not a sentence
  anybody says. **S stays in percent of full scale** — `|ΔS|` is at most
  200 % and is not a ratio of two values.
- **Table columns are sized from their content** (`_columns`), not the fixed
  24 that ran `B is 280 % higher than A` into the next column; the ranked S
  list and the A / B lines likewise, and the S-matrix column is as wide as its
  widest cell.
- **Ports in words**: `S(14,15), between port 14 and port 15`.
- **L and Q are JUDGED only below `SRF_JUDGE_FRAC` (85 %) of the lower
  self-resonance** (`ZCompare.usable_limit`). Past the resonance the part is a
  capacitor, and at it a 1 % shift of the resonance reads as hundreds of percent
  of L — the first version's headline was that number. The region past it is
  shaded "not judged" on the plots and its worst is still printed; the
  resonance shift itself is one sentence ("moved from 11.2 GHz (A) to 11.1 GHz
  (B), 1.2 % down"). Mutation-checked: an `usable_limit` of +inf fails
  `test_the_resonance_is_not_what_the_verdict_reads`.
- Section headings bold, what is over a limit red (`line_tags`, which also
  reds a `✗` strip line).
- The text stays ENGLISH, like every other window: the red zone's X11 fonts are
  not known to carry CJK, and a reading in boxes is worse than one in English.

**The self-resonance is read through the pole.** An inductor's SRF is a
parallel resonance: Z goes through a POLE, and a straight line through two
Im Z samples either side of it lands wherever the grid puts them — one
network on three grids read 26.991 / 26.858 / 26.922 GHz (true 26.902), and
the reading said the resonance had "moved 0.65 %". `self_resonance` now
interpolates the zero of Im(1/Z), which is smooth through a pole, when both
samples sit above the band's median |Z|; the three grids agree to 1e-5
(`TestSelfResonanceIsReadThroughThePole`). `OVER` reddens a line only as a
WORD, so a file called `COVER.s2p` is not painted.

### Session

The workspace's state rides in the session file's `workspaces` block under
`"compare"` (`state_get` / `state_set`, through `WorkspaceSwitch`;
`pkg_rlc/services/session.py` did not change). Inner version
`COMPARE_STATE_VERSION = 1`, like the trace block's own:
`{"version", "reference", "checked", "what", "mports", "conn_rows",
"limits": {"s", "l", "q"}, "marker_ghz"}`, the row lists through
`setup_tables.rows_to_json` (so a row's on/off switch survives). **Nothing at
all at the defaults**, so a session that never used the workspace carries no
block. **Config, never results** — a loaded workspace shows `Nothing compared
yet` until Compare. A reference or ticked file that is not loaded is skipped;
a garbled row list costs that list; a block of a newer version raises, and the
switch drops ONLY that block with one Log note (`TestTheSession`).
