# The editor, the connection table, and the port roles

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

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
- **That hint FOLLOWS THE KINDS IN THE TABLE** (`conn_hint_text` /
  `CONN_KIND_HINTS` / `_CollapsibleHint.set_text`) — the same rule
  `conn_table_layout` applies to the cells and the header, one layer up. It was
  a single paragraph covering all six Kinds, so a user filling in a `short` row
  read two sentences about `rlc_between` to reach theirs, and the sentence they
  needed — *the whole group goes in ONE cell, there is no To* — was in the
  middle of it. That is not hypothetical: a user wrote `short 25 to 26`, typed
  a port number into the cell that is the node NAME on a short row (grid column
  2 carries To **or** Net and its heading reads `To / Net` when both are in the
  table), and got *"node name '26' is a port number or range"* — while the same
  spelling with a tag, `short 25 to F2.15`, is a **legal node name** and passes
  in silence with the package never connected at all. Collapsed it costs
  **nothing** (one line, as before, and it names the Kind when there is only
  one); expanded it is normally **shorter** than what it replaced, because a
  real table carries one or two Kinds and not six — measured, 965 chars for a
  `short`-only table against 2315 for all six. Three rules: the order is
  `CONN_KINDS`, never row order, because a reference that reorders itself as
  the user edits is one the eye cannot find its place in again; the general
  rules (SI suffixes, one word, blank ≠ zero, Show Ports) are appended ONCE
  rather than per Kind; and the **file-tag** paragraph appears only on a
  composed trace, so a single-file user never reads about tags.
  `set_text` early-outs on an unchanged value — it runs once per keystroke —
  and generates `<<HintToggled>>`, which is what refreshes the editor
  scrollregion, because the form's height moved.
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
- **THE RESET BELONGS TO A MODE CHANGE, AND `_update_mode_visibility` ALSO RUNS ON EVERY
  TRACE SELECTION.** It used to end with an unconditional `preserve=False`, so every click
  in the Traces list threw the reader back to the top of the form — and the form does not
  fit: measured at **1500x900** on a mode-5 trace, it is **728 px against a 345 px
  viewport**, so the connections table is BELOW THE FOLD at `yview 0`. The reader scrolls
  to 0.35 to reach it (**220 px** of table on screen), clicks the other trace to compare
  the two specs — the whole reason for having two traces — and lands back at **0.0 with
  ZERO px** of it visible, on every switch, in both directions. It was reported as the
  Connections table *"disappearing from the GUI"* while *"the calculation is still fine"*,
  which is exactly what it was: nothing hidden, nothing lost, the spec still computed, only
  the viewport moved. `_ed_shown_mode` gates it, so `preserve=False` fires only when the
  mode really moved — the case the reset was written for, where the fields the view is
  scrolled past have been replaced. **Preserving is safe here in a way it was not when the
  reset was written**: `_apply_editor_scrollregion` re-measures the scrollregion BEFORE
  re-applying the offset, so a shorter form clamps to its own bottom instead of parking
  past the end, and the stale-scrollregion failure above cannot come back through this
  call. What is preserved is the **fraction**, not the pixel: measured, 0.3007 of a 728 px
  form (top pixel 219) returns as 0.3014 of a 574 px one (top pixel 173), a **46 px** shift
  of the content — harmless at every size tested, and the reason the guard asserts the
  table is still ON SCREEN rather than only that the number came back.
  `tests/test_editor_scroll.py` is that guard and
  `tests/test_mode5_editor.py::TestModeVisibility::test_switching_from_mode5_to_mode1_resets_the_scroll`
  is the guard on the half a careless fix removes. **Known and NOT fixed:** a fresh select
  still puts the connections table below the fold at 1500x900 — that is the editor-height
  problem `docs/design_connection_table.md` stage 4 owns, and it is what made the reset
  cost so much rather than being a cosmetic annoyance.
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
- **A CONNECTION ROW HAS AN ON/OFF BOX, and "off" means the row is not in the
  spec at all.** `ConnectionRow.enabled`, dropped by `rows_to_dsl_text` — in
  that one place, so the solve, the strips, the text hatch, Ports & Roles and
  the run report all agree without any of them knowing about the flag. It
  exists because both ways of asking *"what is this connection worth?"*
  without it are destructive: **deleting** the row loses its R/L/C and its
  ports (on a package ground row that is a range someone worked out), and
  setting the Kind to **`open` is a DIFFERENT SPEC rather than an absent
  one** — `open` is a declaration, it survives into `port_roles`, and on an
  `rlc_gnd` row it silently discards the element too. `is_blank()` deliberately
  ignores it, or clicking the toggle on the table's spare bottom row would turn
  that row into a row.
  **The control is `tk.Label(padx=0, pady=0, bd=0)` showing `☑`/`☐`, and every
  part of that is a measurement.** At 100% (Microsoft YaHei UI 9, tk scaling
  1.333) a `ttk.Checkbutton` is **23 px** and a `ttk.Label` with its default
  padding **16 px**, against **13 px** of headroom; the bare `tk.Label` is
  **12 px**, exactly the glyph, and both glyphs measure 12 px so the pair is
  **width-stable** (the run-tab marker rule — a toggling cell must not reflow
  the table). It is 17 px tall against the combobox's 25, so the row height is
  unmoved. It is gridded with **`padx=0`** where every other cell gets 1, and
  the **`✕` button went from `width=2` (24 px) to `width=1` (17 px)**, which
  still shows the whole 12 px glyph: those two are what pay for it. Net result
  measured — worst-case table **405 → 410 px**, and the mode-5 **form 418 →
  417 px**, i.e. one pixel *narrower* than before the column existed.
  `ttk` state does not cascade to a `tk.Label`, so **`_toggle_cell` guards on
  `self._editable` itself** (the StylePicker precedent) — otherwise the one
  cell still live on a frozen trace would be this one.
  Two silent-failure guards, both pinned: **`_rows_from_list` coerces boolean
  fields instead of `str()`-ing them** — `str(False)` is `"False"`, which is
  truthy, so a switched-off row came back from a session switched **on** and
  the spec quietly grew a connection (the `_coerce_bool` rule, one level down,
  and there is no checkbox to notice it on); and `conn_row_from_cells` tests
  against the **OFF** glyph, so anything unexpected in that cell reads as
  ENABLED — a row that silently vanishes from the spec is the failure to
  avoid, a row unexpectedly present is visible in the answer.
  **A switched-off row is said out loud**, once for the whole table, leading
  the `V_OK` block and naming the rows: the switch is for debugging, so the row
  is *meant* to be off, but a spec quietly missing a connection is exactly the
  wrong answer this strip exists to prevent, and the difference between "I
  turned that off" and "I forgot I turned that off" is a fortnight. Its own
  per-row checks and both its echoes (`_rlc_echo` and the scope echo) are
  **skipped** — `✓ port 5 → GND: 50 Ω` about a row that is not in the spec is a
  green tick for an element that is not there — while the row is still
  enumerated so the footer route's row numbers keep matching the screen.
- **`HINT_SHORT_CHARS = 64`, and it is a real budget, not tidiness.** A
  `_CollapsibleHint`'s collapsed line is an unwrapped `ttk.Label` sitting
  directly in the editor form's grid, so **its requested width is a lower bound
  on the form's** — there is nothing between it and the 431 px canvas. Measured
  with the `▸ ` prefix: the generic connections line is 59 chars / **354 px**,
  the widest per-Kind line 64 / **380**, and the connections table itself 410,
  so a line inside the budget can never be what decides the form's width. Past
  it, it silently is: the first spelling of the multi-Kind line was the generic
  sentence plus a suffix, 92 chars / **533 px**, and it took mode 5's form from
  418 to **540 px** against a 431 px canvas — one sentence turning the
  horizontal scrollbar on permanently, in a viewport already down to 45 px.
  `_CollapsibleHint._render` clips as a backstop; the budget is what the
  callers are pinned against.
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
- **The style picker stores INDICES and expands IN PLACE.** Indices keep `pkg_rlc.widgets.plot`,
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

### Port names, roles, and the Ports & Roles window

`tests/test_port_roles.py` is the guard, and every claim below was
mutation-checked.

- **`port_roles` in `pkg_rlc.physics.spec` (re-exported by `pkg_rlc.physics.core`) is the ONE
  classifier.** The port-overview
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
