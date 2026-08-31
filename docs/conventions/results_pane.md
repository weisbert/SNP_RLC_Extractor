# The Results pane (notebook, views, run history, snapshots, freeze)

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

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
  `pkg_rlc.widgets.plot` truncates a legend entry to the FIRST `MAX_LABEL_LEN = 30`
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

### The three results views (`detail` / `summary` / `compare`)

`tests/test_results_views.py` is the guard (74 tests, plus the 3 in
`tests/test_plot_controls.py::TestAGrownChildIsRePlaced`), and every claim below was
mutation-checked — 18 mutations, 18 caught, each named in the test that catches
it.

The user's complaint: *"目前字太多了，而且不是很直观可看"*. Measured on the run
they were reading — two composed mode-6 traces — the report was **40 lines and
3538 characters** against a Results pane that is `wrap=tk.NONE` and **144
columns** wide at the default 1500x900 window (measured: 1014 px of Consolas 9
at 7 px/glyph), **102** at 1200x800 and **79** at the 1040x600 minsize. Twelve
of the forty lines were over 90 columns and the widest was **272**. The only
route to a clipped tail is a horizontal scroll, which takes the Port column off
the left edge at the same moment.

- **THE REPETITION WAS WORSE THAN THE WIDTH, and it is what `_footer_segments`
  exists for.** The 272-column coupling legend and the 262-column
  reference-node verdict were each emitted **once per block**, verbatim:
  **1068 of 3538 characters — 30% of the report was one of two sentences said
  twice.** Both are now said once per RUN. The legend moved out of
  `_format_coupling_block` into `COUPLING_LEGEND_LINES` (three lines, each
  inside the 144-column budget, with the load-bearing *"Norton injection ratio,
  NOT the exact current ratio `|Z_ab/Z_aa|`"* wording intact — that sentence has
  six homes that must agree); the reference-node verdicts are grouped on the
  **FULL** verdict (strip, warn flag and detail lines together) and printed as
  `[1][4] Reference-node check: …`. Grouping on the strip alone would put one
  trace's ids on another trace's detail paragraph.
- **THE Z MATRIX IS A REDUNDANCY CLAIM, NOT A TASTE ONE, and it is checked
  numerically.** At `G = 2` the matrix is `[[Z_aa, Z_ab], [Z_ab, Z_bb]]` and
  every entry is printed again in the two tables under it: measured on the
  reported run, `9.924+112.6j` is the self table's `9.92 Ω` and
  `112.6/ω = 3.229 nH`, and `-0.04322-0.01799j` is the pair line's
  `M = -516 fH`. Four lines saying what six lines already say. So at `G <= 2`
  the diagonal becomes a `Z (Ω)` column of the self table and the single
  off-diagonal joins the pair's detail line as `Z_ab = …`, and **exactly one
  place shows each raw complex number**. At `G >= 3` the matrix block is
  restored and the Z column and the per-pair `Z_ab` are dropped instead — there
  it is the compact way to show `G(G-1)/2` off-diagonals. The block went from
  13 lines to 8.
- **The `Z matrix @ <freq>` line prints at EVERY port count and is unchanged.**
  It is the block's frequency PROVENANCE — `tests/test_freq_label.py` pins that
  the Calculate banner and this line name one frequency — and its parenthetical
  is where the open-circuit convention is stated. Folding the matrix must not
  take the frequency with it.
- **`worst M/L` STAYS ON THE PAIR'S HEADLINE.** It was moved to the detail line
  while the block was being slimmed and
  `test_report_readability.py::test_the_db_is_on_the_first_line_beside_M_and_k`
  caught it within the hour: it is the RANK KEY, and with six measurement ports
  there are 15 pairs, so scanning for the loud one off the headline means
  reading thirty lines instead of fifteen. The line is 93 columns against a
  144-column pane — there was nothing to buy.
- **The reciprocity line is a VERDICT (`✓ reciprocal (2.1e-10)`), not a
  metric.** What the metric IS — `max|Z_ab-Z_ba| / max|Z_ab|`, alarm above
  `RECIPROCITY_WARN` — is a definition, the same every run, and it cost 100 of
  that line's 140 columns for a number the reader is scanning for a tick or a
  cross. It moved to the legend and to Help → Mode 6, which the legend points
  at. **The alarm keeps its sentence**, because there the sentence IS the
  reading.
- **The view is read LIVE off the App, exactly like the units mode, and for the
  same reason.** Which rendering is on screen is a RENDERING CHOICE, not a
  recorded fact, so it is not frozen onto a `RunSnapshot`.
  `_on_results_view_changed` and `_on_units_mode_changed` share
  `_rerender_every_page`: **every** page is repainted in place (repainting only
  the newest leaves one screen showing two formattings and then a silent flip),
  and **no run tab is created**, because choosing a view measures nothing. The
  Attribution window is deliberately NOT poked — unlike the units mode it has
  no view of its own that this can leave stale.
- **`_footer_segments` is shared by all three views and is NOT gated on the
  view.** The legend, the attribution pointer, the reference-node verdicts and
  the hidden-traces line qualify the RUN. A compact view printing `ind`/`cap`
  and `M/L` with nothing saying what they mean is the same defect one layer
  down.
- **`compare` DEGRADES, NAMING THE REASON, and never shows an empty pane.**
  Same rule as the attribution split. The view is chosen once and then stays
  chosen, so a run that cannot be compared must still print its numbers: fewer
  than two records prints `compare: compare needs at least two traces on the
  plot — showing the summary instead` and then the summary.
- **THE Δ COLUMN APPEARS ONLY AT EXACTLY TWO RECORDS.** With three it would
  have to pick a reference, and a column headed `Δ` that is secretly "against
  whichever trace sorted first" is the kind of quiet decision this tool refuses
  everywhere else.
- **THE TRACE NAME IS NEVER TRUNCATED IN `compare` OR IN `summary`, AND NO
  BETTER TRUNCATION RULE EXISTS — that is a measurement, not a preference.**
  The user reported the names being elided ("有些名字我其实包含了重要的信息的");
  what the measurement found is worse than hiding a tail. The compare header
  was head-cut at **14** characters and the summary Label column at **18**, and
  on a set of real EM revision names that COLLIDES: with
  `VCO_EM_0731_ideal_ground_ref` / `VCO_EM_0812_ideal_ground_ref` /
  `VCO_EM_0812_RDL_shield_open` / `VCO_EM_0812_RDL_shield_short`, the last two
  head-cut to the same `VCO_EM_0812_RD…` and, at 18, to the same
  `VCO_EM_0812_RDL_s…` — **two columns (and two rows) of the table whose entire
  purpose is telling those two apart, headed byte-identically.** That is
  `freeze_label`'s defect arriving in the Results pane. Then: at the **15**
  characters each column gets with five traces, **head-cut collides, TAIL-cut
  collides** (`…eal_ground_ref` twice — and the tail is what
  `pkg_rlc.widgets.plot._fit_names` keeps, correctly, for its own case), **middle-elision
  collides** (`VCO_EM_…und_ref` twice), and stripping the common prefix first
  rescues only the middle form. The reason is structural and cannot be
  engineered around: `[1]`/`[2]` differ only at the **HEAD** (0731 vs 0812) and
  `[3]`/`[4]` only at the **TAIL** (open vs short), so one rule cannot keep both
  ends. Do not "improve" this back into a cleverer `_trunc_str`.
- **The summary cap bought NOTHING, which is why it simply went.**
  `_render_columns` already sizes that column to its widest cell, so the full
  names cost **10 columns of 144** (self 73 → 83, coupling 85 → 95).
  `SUMMARY_LABEL_MAX = 40` stays as a backstop against a pasted path arriving
  as a label — not as a width budget.
- **`compare` shows the whole name in one of TWO shapes, and the shape is chosen
  by the HEIGHT it would cost.** STACKED is the default: the name is wrapped
  down the column heading at `_` / `.` / `-` boundaries by `_wrap_name`, with
  the separator kept on the LEFT of the break so a reader can see it is a wrap
  and not a character the name lacks. LEGEND is the fallback: one line per trace
  above the table carrying the curve colour and the full name, heading reduced
  to `█ [N]`. Measured on the reported run (3 traces): **87 columns head-cut
  (and wrong) → 85 stacked → 57 legend**, against the pane's 144. The fallback
  fires on two conditions, both of them real: the stack would be deeper than
  `COMPARE_STACK_LINES_MAX = 4` name lines, or a name has **no separator inside
  the column** and would have to be cut mid-token — a hard-wrapped name reads as
  corruption rather than as a wrap.
- **THE PRIORITY IS THE WHOLE NAME ON AS FEW LINES AS THE BUDGET ALLOWS, not the
  narrowest table.** The complaint was a name being ELIDED; width mattered only
  because it was what forced the eliding. So `_compare_head_cells` takes the
  widest segment the budget affords, **capped at the name itself** — with few
  traces every name lands on one line, and as the trace count rises the
  per-column share shrinks and the names wrap instead of the table overflowing.
  Measured, `VCO_EM_0812_RDL_shield_variant_N` at 2/3/4/5/6/8/10 traces: 96 96
  93 91 93 117 141 columns, every one inside 144, every name reassembling
  character for character.
- **THE IDS STAY ON HEADER LINE 0 AND THE NAME IS BOTTOM-ALIGNED UNDER IT.**
  Line 0 because `_tag_swatch_rows` walks lines and consumes ONE colour per
  swatch it finds, so swatches spread over several header lines would colour the
  columns in the wrong order; bottom-aligned because the last line of a name
  then sits directly above the numbers it labels whatever depth its neighbours
  needed. The legend shape emits a swatch per legend line **and** per column,
  which is what `_format_compare`'s `* repeats` is for — and why the guard on it
  asserts `tagged == swatch count` rather than `> 0`, which the mutation passes.
  **A test of any of this needs a fixture whose columns require DIFFERENT
  depths**: with two traces the share is wide enough for both names to fit on
  one line, the padding is empty, and the alignment mutation is a no-op (six
  traces squeeze the share to 10 characters, which is what the guard uses).
- **`_render_columns` takes a multi-line header cell (a list instead of a str)
  and places it EXACTLY as given.** Where a stacked name sits relative to its
  numbers is a reading decision, so the caller pads with `""`; every line of the
  header counts towards the column width. A plain `str` is one line,
  byte-for-byte what it always was — which is what keeps the two
  reference-pinned renderers out of this change.
- **KNOWN, NOT FIXED: the `detail` view's Label column is still head-cut at 18.**
  It is `_format_results_table`, which `tests/fixtures/render_reference.json`
  pins byte-for-byte, and the reference deliberately contains a 30-character
  label (`a_very_long_trace_label_indeed`) — so widening it moves two reference
  cases and needs the documented "regenerate ONLY in the same commit that
  justifies it" escape. The collision above applies there too. Fix it from that
  side, with the reference regenerated in the same commit; do not paper over it
  by changing `LABEL_W` and quietly re-capturing.
- **A big change is a FACTOR, a dB change is a dB DIFFERENCE, and a missing
  quantity is an EMPTY CELL.** Measured on the reported run, `M` goes
  `-516 fH → -7.19 pH`, which is `-1293%` and `13.93×`; the crossover is a
  factor of ten either way. dB is already a ratio, so a percentage of decibels
  is meaningless (`-68.77 → -52.36` is `+16.41 dB`, not `+23.9%`). And "this
  trace has no port called RX" and "RX measured 0" are different statements —
  `vals.get(r.id)` with no default, so the cell is blank and the Δ column
  cannot invent a change against a zero nobody measured.
- **A DIMENSIONLESS QUANTITY MUST NOT TAKE AN SI PREFIX.** `format_si` renders
  `k = -2.412e-4` as **`-241 u`** — a micro-nothing, which is not a quantity.
  `k` and `Q` go through `_fmt_plain`; `dB` is excluded from the other end for
  the same reason (a milli-decibel).
- **The summary calls `rank_coupling_pairs`, it does not sort for itself.** Two
  views disagreeing about which coupling matters is worse than either being
  wrong alone, and the floored tail is counted and pointed at the CSV exactly
  as the detail view counts it.
- **`_tag_swatch_rows` consumes EVERY occurrence on a line, not only a leading
  one.** The compare view puts the swatch in each COLUMN HEADING, because there
  a column is a trace and the heading is the only cell that names it. No other
  line this module emits carries the character at all.
- **THE NEW GLYPHS WERE MEASURED IN THE PANE'S OWN FONT before being used, and
  two of them are not table-safe.** Consolas 9: `·` **7 px**, `—` **7**, `×`
  **7**, `Δ` **7** — the same as a space and a digit, so the compare table's Δ
  column and the delta cells line up like every other column. But `✓` is
  **12 px** and `⚠` is **16 px** (the Attribution window's rule, re-measured
  here), so both are confined to the reciprocity VERDICT line, which is a
  standalone sentence with nothing column-aligned under it. Do not move either
  into a cell.
- **THE RESULTS HEADER IS A `ReflowRow` NOW, and that was forced by a
  measurement.** With five packed controls it already asked **667 px against
  the 575** it gets at the 1040x600 minsize at 150% font scaling, and `pack`
  unmaps from the END — the Keep button, whose label is the only place the kept
  cap is stated at the moment it bites, was the one being squeezed. A `View:`
  label plus a readonly combobox is a further **127 px at 100% and 240 px at
  150%**. At 100% the whole strip is **477 px of 575** and stays one row, so
  nothing about the default window moves. `_refresh_keep_button` calls
  `_results_header.refresh()`, because a child whose TEXT grows fires neither
  `add()` nor the strip's own `<Configure>`.
- **Two latent `ReflowRow` bugs came out of that and are fixed in
  `pkg_rlc/widgets/widgets.py`.** (a) `_applied` was keyed on the row ASSIGNMENT and the
  row height only, so a child that grew without pushing the strip onto another
  row left both unchanged — `refresh()` called `_reflow()` and `_reflow()`
  returned early having done nothing, leaving every item after it at its old
  `x`, i.e. drawn through its neighbour. The item widths are part of the key
  now. It only ever looked fixed because the case it was first measured on
  (220 → 307 px in the Attribution header) happened to wrap as well. (b) An
  ordinary control is placed with **no explicit width**, so Tk sizes it from
  its own request AND TRACKS IT; a slave pinned to a stale explicit width is
  CLIPPED with no ellipsis and no overflow marker, which `winfo_ismapped()`
  cannot see. The wrap DECISION still needs the notification — that is the
  strip's arithmetic, not the child's.
- **`render_reference.json` MOVED, deliberately, and `_render_capture.py`'s
  docstring records exactly what moved.** The five `table_*` cases are
  BYTE-IDENTICAL across the change and are the evidence the results table did
  not move; the five `block_*` cases changed in the three ways above and
  nothing else. This is the documented escape ("regenerate ONLY in the same
  commit that justifies moving the reference") and it is the first time it has
  been used.
- **KNOWN, NOT FIXED: the reference-node strip is still 262 columns on one
  line.** Wrapping it would be right — it is prose in a pane that does not wrap
  — but `tests/test_multifile_engine.py` asserts `rec.ref_strip` appears
  CONTIGUOUSLY in the Log and on the run page, which is a legitimate property
  (the verdict reaches both surfaces intact). Fix it from the
  `reference_provenance` side, where the sentence is written, not by re-wrapping
  it here.

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
