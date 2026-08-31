# The Attribution window and the two attribution reports

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### The Attribution window (`pkg_rlc/panels/attrib_gui.py`)

`tests/test_attrib_window.py` is the guard, and every claim below was
mutation-checked. `docs/design_port_attribution.md` §13 records what stage 4
became and where it departed from that note's own §9 sketch; **§13.13 records
the four things the first screenshot changed** — the sweep plot's pole, the
sash, the across-frequency badge and the ground model — none of which was a
wrong number, and all four of which had a written reason a later session would
otherwise reinstate.

**The hook surface `pkg_rlc/frontend/app.py` calls, and there is no other:**
`ATTRIB_MENU_LABEL`, `attribution_refusal(trace, file_entry)`,
`open_attribution_window(app, trace)`, `refresh_attribution_windows(app)` —
which **must** be called from `_apply_editor_strips`, from every path that
removes a trace or a file, and after a session load —
`refresh_attribution_windows(app, rerender=True)` from
`_on_units_mode_changed` and **nowhere else**, and
`attribution_session_state` / `apply_attribution_session_state`. The two
`refresh` forms are not interchangeable: measured with one window open,
**28.6 µs** for the banner refresh against **6049 µs** for `rerender=True`,
which redraws the tables and the sweep — 200x, and the reason the cheap one is
the default on a path that fires from a Tk variable trace. The reverse import
**no longer exists at all** — the `_gui()` shim is deleted, and what it
reached for now lives below both files (`pkg_rlc.model.trace`, `pkg_rlc.model.validate`,
`pkg_rlc.present.report`, `pkg_rlc.widgets.widgets`), so there is no cycle left for
`import pkg_rlc.panels.attrib_gui` at the top of `pkg_rlc.frontend.app` to dodge. A
second copy of `_value_formatter` / `_trace_role_rows` / `_build_termination`
was the alternative, and two renderings of one spec drifting apart is a
failure this repo has already had more than once.

- **MODELESS, no `grab_set` — and deliberately NOT `transient(app)`.** No
  `grab_set` for the documented reason: a modal `Toplevel` that outlives its
  opener blocks event delivery, `update()` never returns, and the GUI and the
  test suite hang together (the style-picker / scrollbar-limit-cycle failure).
  `transient` is the one where this window and `PortRolesWindow` diverge **on
  purpose**: on Windows it removes the taskbar button and the Alt-Tab entry
  and makes the WM withdraw the child with its master. That is right for a
  quick read-while-editing panel and wrong for a window holding a RESULT that
  cost a Recompute, which is read against the plot over many edits and parked
  on a second monitor. The cost of omitting it — the window can end up behind
  the main one — is paid by `open_attribution_window` **lifting and focusing
  an existing window** for the same `(trace, victim, aggressor)` instead of
  opening a second copy.
- **NO NOTEBOOK, and specifically not the obvious four tabs.** Contributions /
  Sensitivity / Sweep / Across-frequency was designed and rejected: the sweep
  is a **drill-down on the row just clicked**, so a tab makes the user re-pick
  the element they already selected; and "does this ranking hold across
  frequency" is a **validity qualifier on the table, not a place**, so as a tab
  it is never opened and the acceptance item is satisfied on paper only.
  Shipped instead: a fixed header, three one-line strips, a one-line
  across-frequency badge with an expander, one pane with a **radiobutton view
  toggle**, and a detail pane under a sash. Every pane is populated **before**
  `PanedWindow.add()` — ttk sizes a pane from its requested size at `add()`
  time and never recomputes.
- **THE TABLES ARE A MONOSPACE `tk.Text`, NOT A `ttk.Treeview`** — this
  reverses `design_port_attribution.md` §9's own bullet, on a measurement.
  Measured here: the same eight columns need **671 px** as a Treeview at 100%
  and **971 px** at 150%, against **490 / 700 px** as Consolas 9 text; ttk will
  not shrink a Treeview column below its set width even with `stretch=True`
  and it clips with **no ellipsis and no overflow indicator**, so `-0.6231`
  silently becomes a plausible shorter number; and in `TkDefaultFont` the
  signed-number glyphs are all different widths (`-` 5 px, `+` 9, U+2212 9,
  `.` 3, ` ` 4, digits 7), so a right-aligned column of signed values has its
  decimal point wandering ±4 px per row. In Consolas 9 every glyph the tables
  emit — `' ' 0 9 - + − █ ▸ ▾ . M X ( ) % j … Ω ←` — measures **exactly 7 px**
  (re-measured), which is why they line up. `✓` is **12 px** there and is
  banned from tables, used only in ttk Labels. The pure text formatter is
  separate from the widget so it is testable with no display.
- **Numeric columns are auto-sized from the widest cell OR the header, and are
  never capped.** Sizing on the values alone puts a 7-char value under a 5-char
  header and throws the heading one place off the numbers it names — the
  cursor-readout rule. Only the two TEXT columns cap, and they ellipsise with
  U+2026; a clipped number is a plausible wrong number, which is the whole
  reason this is not a Treeview.
- **A sign is always one of a WIDTH-STABLE PAIR, and colour NEVER means sign.**
  U+2212 for negative, an explicit `+` for positive, one of the two on every
  numeric cell — both 7 px in Consolas 9, the same as a space, so a column of
  mixed signs keeps its decimal points in one place. Rows are coloured by
  element **KIND**, reusing `PORT_ROLE_FG`, the palette the user already
  learned in Ports & Roles. Not by sign: red is `WARN_FG` everywhere else in
  this application and a red negative makes a correct answer look like a fault
  — which bites hardest exactly here, because §5.7's measured case has a
  **negative bare EM term** with four declared grounds cancelling most of it.
  The rule is stated ONCE in the header, beside the reference declaration.
- **RECONCILIATION GOES IN THE HEADER, NOT THE FOOTER.** It gates trust in
  everything under it, and at the bottom of a scrolling table it is the first
  thing off screen. Verdict word plus number
  (`reconciled  rel diff 3.1e-13  (floor 4.3e-10)`), and the **TOTAL is shown
  even when the split is withheld** — that is the core module's "degrade, never
  refuse" made visible.
- **`[Recompute]`, NOT AUTO-REFRESH. This is the one that would have shipped
  broken.** `tc.Zmat` is written **only** by `_on_calculate`; editing the spec
  sets `tc.stale` and leaves the numbers at the PREVIOUS run's. A window
  refreshing from `_apply_editor_strips` would therefore, on the first
  keystroke, decompose the NEW spec and reconcile it against the OLD
  authoritative total — a residual not of `1e-13` but of however much the edit
  changed — and by the rule above it would then withhold its own split. **The
  documented behaviour of the auto-refresh design is a window that erases
  itself while you type.** The editor hook stays, but it updates exactly ONE
  thing: the **staleness banner**, a signature comparison costing microseconds,
  which is what makes the button honest. The banner also names the provenance
  permanently (`from run #7 @ 5.600 GHz`).
- **REFUSE ON A STALE OR FROZEN TRACE, BY NAME, and keep the menu entry LIVE.**
  `attribution_refusal` returns `None` or the reason, duck-typed on `getattr`
  so it is importable and testable without `pkg_rlc.frontend.app`. Four refusals, in the
  order the work would hit them except that `frozen` is tested FIRST (a frozen
  trace can never be attributed whatever the file is doing, so sending the user
  to load one is a dead end): **frozen** — its numbers came from an earlier run
  and can never be recalculated, so a decomposition now could only be stamped
  with the CURRENT run, which is the frozen-trace-CSV bug through another door;
  **file not loaded**; **no numbers**, or `Zmat is None` with `Z` set, which
  means ONE measurement port and there is no mutual impedance to attribute;
  and **stale**. The entry stays live in all four cases — CLAUDE.md's rule on
  the identical decision for Freeze: *a greyed entry would be the same bug
  report.*
- **THE SWEEP CANVAS follows `FullscreenPlotWindow` with two deliberate
  departures.** `Figure()`, never `pyplot.figure()` — pyplot's global registry
  outlives the `Toplevel` and the figure is never collected. Then: **no
  `<Enter>` → `focus_set`** (measured: that binding moves focus off a sibling
  Entry, and this window has Entry fields directly above the plot, so a user
  typing `5.6` into Freq and moving the mouse toward `[Recompute]` loses the
  rest of the keystrokes), and **no M / V / Delete bindings** — it is a
  read-only what-if curve, not the measurement plot. It is drawn **LAZILY on
  first reveal**: a canvas in an unmapped pane has no size, so `draw()` /
  `tight_layout` there lays the axes out for a 1x1 widget and the labels stay
  on top of each other forever after. The curve itself is the closed-form
  Möbius sweep — do not re-solve per point.
- **NO ACCELERATOR, and nothing registered with the wheel router.** `bind_all`
  reaches every `Toplevel`: measured on this application's own menubar, Ctrl+S
  typed into a `Toplevel` Entry fires the App's `_on_save_config`, so a Ctrl+O
  here would open Load Config and replace every trace including the one this
  window describes. `<Return>` in the frequency field recomputes and is bound
  **on the Entry**. For the wheel, this window registers NOTHING with
  `App._register_scrollable`, so `_route_wheel` walks out of it, finds no
  handler and lets Tk's class bindings scroll the Texts (`"Text"` is in
  `App._WHEEL_OWNERS`). `"Canvas"` is **not** in that set, so a matplotlib
  canvas gets no bail-out — which is safe only because there is no registered
  scrollable ancestor here, by construction. Do not nest it inside one.
- **PACK ORDER: footer `side=BOTTOM` FIRST, then the header and the strips,
  then the `PanedWindow` with `expand=True` LAST.** `pack` unmaps from the END,
  so the buttons and the reconciliation verdict are unconditional and it is the
  TABLE that gives up height. **"Gives up height" must not mean "gives up all
  of it", and the MINIMUM is therefore measured rather than declared.**
  Re-measured at 150% DPI (`tk scaling 2.0` + every named font x1.5, the
  definition `test_run_history.py::test_the_keep_button_is_READABLE_at_150_percent_font_scaling`
  uses): the fixed chrome is **436 px at 720 wide** and 388 at 980, against a
  declared minimum of 420 — so at exactly 720x420 the whole `PanedWindow` read
  `winfo_ismapped() == 0`, table and detail pane and sweep canvas all gone,
  with no scrollbar, no message and no way down because the user is already AT
  the minimum. It was not only the minimum: the table was already unmapped at
  820x540. `_apply_min_height` therefore raises the floor to
  `chrome + ATTRIB_SPLIT_FLOOR_LINES x <table font linespace>` at the CURRENT
  width — the split needs a measured **124 px at 100% / 162 px at 150%** for
  the table to be mapped, and 9 lines is 126 / 198. At 100% the computed value
  is 333 px, i.e. under the declared 420, so **nothing about the 100% window
  moves**. It SETTLES because it reads the WIDTH and writes only a minimum
  HEIGHT (the `ReflowRow` fixed point, not the `_apply_editor_scrollbars` limit
  cycle): measured over eight resizes in both directions at both scalings,
  every one settled inside 40 update rounds with the last twenty identical.
- **The same rule applies INSIDE the sweep pane: the caption is packed before
  the canvas.** With the canvas first it claimed its whole 90 px request and
  left the caption 1 px — measured at 720x420, **103 x 6 PIXELS of axes** over
  a 1 px label, and that label is where rule 8's mandatory `NON-MONOTONIC: the
  curve LEAVES the [ideal, open] bracket` goes, along with the `cannot sweep`
  refusal for `k` / `M/L_a` and any refused candidate. Six pixels of curve is
  worth nothing; the sentence saying the two endpoints are not a bound is worth
  the pane.
- **The sweep caption is CAPPED at `SWEEP_NOTE_LINES = 3` clipping lines, and
  the full text is in Copy report.** It was a wrapping Label at
  `wraplength=420` holding up to four sentences (957 characters on a
  non-monotonic sweep), i.e. a **293 px** request — and packed against an
  `expand=True` canvas that meant the plot sat at its 90 px floor at every
  window size: measured 194 -> 90 px of canvas at 980x700 and 274 -> 90 at
  1400x900 the moment a row was selected. Three lines and not two, seen on
  screen rather than argued: at two, the second line was `… +N more` and the
  MANDATORY non-monotonicity label was inside the +N. Order is priority — a
  refused candidate, the interval, the warning, then the module's notes — and
  whatever does not fit is COUNTED, never dropped in silence.
- **A REFUSED CANDIDATE has to reach a widget.** `_alternatives` used to write
  its problem list into `sweep_note` and `_draw_sweep` overwrote that Label
  later in the same `_render()` pass, so `candidate_list("open, R=5 m", …)` --
  which produces exactly the `_rlc_tokens` message this repo requires, "'R=5 m'
  would silently mean 5 Ω, not 5 mΩ" — reached NOTHING: measured, `sweep_note`
  held the caption, `foot_note` was empty, and the Sensitivity table went from
  four rows to two with its own note saying "2 rows" and nothing else. It is
  now a RECORD (`_cand_problems`) rendered by `_set_sweep_note` ahead of the
  caption and counted on `table_note`, and `_on_candidates_changed` parses
  EAGERLY so the refusal lands on the keystroke that caused it.
- **The Candidates Entry is packed BEFORE the hint that describes it.** pack
  unmaps from the end: measured at 100%, the caption row needs 103 + 615 + 188
  = 920 px, so the field was 112/188 px wide at 860, 12 at 760 and
  `winfo_ismapped() == 0` at the declared 720 minimum — gone, while the 601 px
  sentence telling you to type into it was still there; at 150% it needed a
  1920 px window to appear at all. The hint is `wraplength=0` and clips, like
  the three header strips.
- **NO `<Escape>` BINDING.** A Toplevel is in every descendant's bindtags, so
  `self.bind("<Escape>", …destroy)` fires from anywhere inside the window:
  measured, Escape in the Freq entry, in either port combobox, in the table and
  on `[Recompute]` all destroyed it. `PortRolesWindow` binds it and is right
  to — it is a read-only list that rebuilds from live state on reopen — but
  this window HOLDS a result: a Recompute, plus five `build_context` +
  `decompose` passes if the badge was expanded, all O(N^3) in the port count,
  and nothing restores it. The Close button and the WM close box remain.
- **`[Recompute]` FLUSHES the queued editor sync first, and re-asks the
  refusal.** The documented "Auto-sync editor on Calculate" invariant, on the
  one button the module calls "THE button". Measured with the trace calculated
  at `gnd_ports = "2,4"`: typing `2` into the GND field and pressing Recompute
  in the same event burst decomposed the OLD spec — the table came back with
  `ground port 2` AND `ground port 4` — and only then did the queued
  `after_idle` sync land, after which the banner said "the spec has been EDITED
  since — press Recompute", pointing at the edit Recompute was supposed to have
  picked up. `_on_toggle_stability` flushes for the same reason. The refusal is
  re-asked with **`allow_stale=True`**, which exists for this call site and no
  other: an edited spec is what the button is FOR, but a trace that became
  frozen or lost its file while the window was open must still be turned away
  (measured without it: a frozen trace recomputed here came back reconciled and
  stamped `spec_matches_run: True`).
- **The BANNER says `spec_matches_run`, not just the export.**
  `_on_recompute`'s docstring promises "the banner, the export and the copied
  report all say the plot and the results table are showing something else";
  only two of the three did. `staleness_text` compared
  `spec_signature(trace) != prov.signature`, which is False immediately after a
  Recompute — the Recompute just re-captured it — and nothing consulted
  `spec_matches_run`. Measured: run #1 is M = +821 pH, editing GND to `2` and
  pressing Recompute gives +407 pH (**2.0x** what the plot, the results table
  and Export CSV are showing), and the banner read
  `from run #1 @ 5.1 GHz   ·   M: 'vic' ← 'agg'` in the theme foreground. A
  MOVED signature outranks it when both are true: "press Recompute" is the
  action.
- **The refusal counts MEASUREMENT PORTS, not just `Zmat is None`.**
  `_on_calculate` routes on `tc.mode == 6 or n_mports > 1`, so a mode-6 trace
  with ONE measurement port takes the coupling path anyway and comes back with
  a real `(F, 1, 1)` `Zmat` — measured. Testing `Zmat is None` alone waved it
  through, and what stopped it was `open_attribution_window`'s
  "fewer than two measurement port names cached. Calculate it again." backstop:
  a message about an internal inconsistency that had not happened, advising
  something that cannot help. Both routes to the shortfall now reach the one
  message that names it.
- **A click PAST the last table row selects nothing.** Tk's `@x,y` index CLAMPS
  to the nearest existing line, so a click in the empty space below the table
  resolved to the last row — measured, a 5-line table in a 222 px widget
  answered `index("@50,218")` (about 150 px below the last text line) with
  `"5.51"` and selected the final element, silently re-driving the detail pane
  and running a sweep solve for something nobody clicked. `_on_table_click`
  tests the clicked line's own `bbox`.
- **`ReflowRow.refresh()` after a child's TEXT changes.** `_reflow` runs from
  `add()` and from the strip's own `<Configure>`, and a child whose text grew
  fires neither — `place` then goes on forcing the stale width and the child is
  CLIPPED with no ellipsis and no overflow marker, which is the Treeview
  failure this window's tables exist to refuse, arriving in the header instead.
  Measured at 980x700 with the trace relabelled to the 18-character cap: the
  item asked 307 px and was placed at 220, i.e. 87 px / 14 characters gone in
  silence, while the strip went on reporting one row against items asking
  1048 px of 964. A 1 px resize fixed both, which is what makes it a missing
  notification rather than a layout bug. The refresh is `after_idle`,
  coalesced, and CANCELLED on `<Destroy>` (an un-cancelled `after` fires
  against a Tcl command teardown has already deleted).
- **THE WINDOW OUTLIVES ITS SUBJECT.** `PortRolesWindow` re-reads `app.traces`
  every refresh and degrades gracefully; this one holds a RESULT and cannot.
  Every path that removes a trace or a file, and every session load, must call
  `refresh_attribution_windows(app)` — the same class of omission as the
  documented `_on_remove_file` forgot-to-replot bug, which is why the poke is a
  named function here rather than a line of code in the host.
- **THE UNITS MODE IS READ LIVE OFF THE APP; everything else on the
  `Provenance` is frozen.** Same rule and same reason as
  `_run_report_segments`: the unit is a **rendering choice, not a recorded
  fact**, so freezing it would leave this window printing `-1.242` beside a
  results pane printing `-1.24 mH`. The stored value is only the fallback for a
  window whose App has gone.
- **EXPORT CARRIES THE FULL PROVENANCE.** Copy report and the CSV both head
  with the run number, the frequency and its snap note, the complete sign
  convention, the ground model and the termination spec verbatim. The
  frozen-trace CSV precedent is explicit that a block attributed to the wrong
  run is a real bug — and here the exports are also where the clipped strips'
  tails live, so they are not optional prose.
- **The three strips are ONE LINE each with `wraplength=0`, i.e. they CLIP.**
  Re-measured on a real window: each is **21 px** at 980x700 and still 21 px at
  720x420, i.e. all three stay one line at both declared sizes. Letting them
  wrap costs 51 px of a **213 px** pane budget at the minimum, and there are
  three of them. **THE BUDGET IS 48 CHARACTERS, NOT 120.** The 120 / 162 figures
  were measured at 100% only; re-measured at 150% the same strip fits **52
  characters at 720 px and 74 at 980**, and the old sign strip spent its first
  64 on the sign rule alone — so the shares rule, which rule 4 requires to be
  stated once in the header, was off screen at **every** supported size while
  the comment beside it said the opposite. `SIGN_STRIP_TEXT` is rewritten to
  spend its first 48 characters on both rules and nothing else (measured on the
  real widget: 48 at 150%/720, 66 at 150%/980, 110 at 100%/720, all 143 at
  100%/980). For the same reason `reconciliation_line` puts `— split WITHHELD`
  **immediately after the verdict word** instead of behind 45 characters of
  `rel diff … (floor …)`: measured, WITHHELD never appeared at 150% at either
  width, while the table underneath read "(no per-element split — see the
  reconciliation line above)" and pointed at a line that no longer carried the
  reason. Each strip leads with its verdict and its number, and the full
  declaration is in Copy report and the CSV, where it cannot clip.
- **The header is a `ReflowRow`, and the trace label is capped at 18+18
  chars.** Re-measured on a real window (mode-6 trace `coil` on
  `coupled_4port_diff.s4p`): the six items are 228/160/186/145/143/99 px =
  **961 px**, the strip is **964 px** at the 980 default — **one row, 29 px** —
  and **704 px** at the 720 minimum, where it wraps to **two rows, 58 px**.
  Three pixels of headroom at the default width is the whole reason the label
  is capped; the test's own default label puts the same sum at 970 and
  uncapped the first item is 317 px worst case, which forces a THIRD row.
  `winfo_reqwidth()` stays at **1** at both sizes, which is the other half of
  why `ReflowRow` (it lays out by `place`, and place does not propagate), so
  the header cannot force the `Toplevel` wider than the user set it. A plain
  `pack(side=LEFT)` run would have unmapped `[Recompute]` from the END with no
  scrollbar and no other route to it — exactly the defect
  `tests/test_plot_controls.py` exists to stop recurring.
- **`ttk.Panedwindow` starves its FIRST pane, not its last.** Measured at
  720x420: the split wanted 445 px and got 168, and the **table** read
  `winfo_ismapped() == 0` while the detail pane was fine. The cause was
  `FigureCanvasTkAgg` sizing its Tk widget from `figsize x dpi` and therefore
  REQUESTING 420x240 against the table pane's 156; capping the canvas *widget*
  to 240x90 balances them at 156/156 and both survive at 54/74 px. Only the
  FLOOR moved — matplotlib re-lays the figure out on every `<Configure>`, so
  the drawn plot is whatever the pane actually gets (183 px tall at the default
  size).
- **THE WINDOW OFFERS THE GROUND MODEL, in the CLI's own `shared:0.3n`
  spelling, and it goes through `[Recompute]` like every other input.** It was
  CLI-only at first on a pixel argument — the header is a `ReflowRow` measured
  at **961 px of 964** at the 980 default, so any further control there wraps
  it to a second row (**29 px**, measured) — and that argument lost to the
  measurement it was weighed against: the choice is worth
  **9.60 dB** (four balls at 1 nH each independently versus the same four tied
  through one shared 1 nH), against the **6.07 dB** dispute this whole layer
  exists to settle, and it grows with the ball count — `(1 + (n−1)k)` is
  **13.6 dB** at 20 balls with `k = 0.2`. A control the user cannot reach is
  not a default, it is a decision taken for them, and this one is the most
  expensive decision in the flow. One field, one spelling shared with
  `--attribute-ground-model` so the two cannot drift, and **one line beside it**
  saying why the default is not obviously right (independent leads understate
  the shared return) — one line, not a lecture. The **sign strip states which
  model is in force** and both exports keep doing so — but it says so AFTER the
  sign rule and the shares rule, never in front of them: the strip's measured
  budget is **48 characters at 150%/720**, rule 4 requires both of those stated
  once in the header, and the model name is the item that may clip, because it
  is also on the control itself and in full in both exports. Whatever a later session
  does to the header, the control may not cost `[Recompute]` its place: that is
  what `ReflowRow` is for (`pack` unmaps from the end and has no scrollbar and
  no other route to the button — `tests/test_plot_controls.py`'s whole
  subject).
- **A dense `Zt` has NO second opinion, and the window must NOT invent a second
  rule for that.** The rule is already written down twice above — *"the
  reconciliation compares two algorithms on ONE network, so it is always taken
  on the DECLARED configuration"* (`ctx.Zop_declared` is the left-hand side
  whatever `zt` is in force) and *"what a dense `Zt` loses is the second
  OPINION, not the check"* (`reference_applicable = False`, the total
  re-labelled *"compute_z_matrix, DECLARED spec — a DIFFERENT network"*).
  Follow those; `_attr_print_ground_model` is the CLI's working example.
  The failure they exist to prevent is measured on `diff_pair_4port.s4p`
  (probes 1/2, grounds 3/4, 5 GHz): reconciling the MODELLED total against the
  engine's DECLARED one made a shared 1 nH return — which **doubles** `M` —
  read as a residual of **1.01**, past `RESIDUAL_CATASTROPHIC`, so `terms`
  emptied and the window printed *"the two algorithms disagree about the answer
  itself"* about **2.026 nH, which was right**. The split would vanish at
  exactly the setting the control exists for.
- **THE SWEEP PLOT LABELS THE POLE; it never lets the pole set the axis.**
  Measured on `diff_pair_4port.s4p` at 5.0005 GHz sweeping `ground port 3`:
  `M(0) = 1.01 nH`, `M(inf) = 503.7 pH`, and one pole at `L = 505.25 nH` where
  the extremum is **±10.28 mH** — 1.0e7 times the endpoint bracket. Autoscaled,
  that is a y axis reading `1e-5`, one vertical spike, and a caption honestly
  saying NON-MONOTONIC beside a picture carrying no information at all — and a
  true sentence beside an uninformative picture teaches the reader to stop
  looking at the picture. Five rules, of which the middle three are the ones
  that get "simplified" back into an autoscale: **x stays
  log; y is SYMLOG with `linthresh` derived from the ENDPOINT SCALE, never the
  panel's `1e-6`** (these are henries — 1 µH is a thousand times the whole
  curve, so a fixed `1e-6` puts every point inside the linear band and symlog
  degenerates into the linear axis it replaces); **the y limits come from the
  physical endpoints `M(0)` and `M(inf)` plus a margin** — those are the two
  numbers the user came for — and the pole is allowed to run off the top;
  **the pole is drawn as a labelled vertical line at its `z`**, from the closed
  form (`z = -γ/δ`, i.e. `t = -λ_j`; measured, `ctx.Gm[0,0] = -391 µΩ -
  j15.8745 kΩ` and the pole sits at `L = 505.25 nH`, which series-resonates
  with that 2.005 fF at the 5.0005 GHz being read), **never by scanning the
  samples**; and **the headline interval is the POLE-FREE one** with the pole
  reported as a separate sentence (`[-2.5 pH, 1.52 nH]` at a factor-of-two
  guard band, against `[-10.28 mH, +10.28 mH]` over the whole half-line —
  the second is the tool describing its own arithmetic). A pole is a real
  feature of the structure: label it, never silently hide it. With no pole in
  range nothing changes visually. The guard is a case WITH a pole and a case
  WITHOUT, both asserting the y limits bracket the endpoints.
- **"The limits bracket the endpoints" is NOT SUFFICIENT on its own, and a
  shipped fixture proves it.** The margin for a sweep with no span was
  `SWEEP_Y_PAD * max(abs(hi), abs(lo), 1.0)`, and that bare `1.0` is one HENRY
  in an expression whose other terms are picohenries. Measured on
  `decap_4port.s4p` (ordinary mode 6, probes 1/2, `gnd_ports="3,4"`, 5 GHz,
  either ground row): every residue is exactly 0, so ideal = open =
  **−506.755 nH**, and the axis came out `(−120.00005 mH, +119.99995 mH)` —
  **473 602×** the value it was drawn to show, with the curve and both
  asymptote lines on one pixel row (endpoint separation **0.0 px of a 223.5 px
  axes**) and `linear_ticks` False, so the symlog decade locator printed
  **17** labelled decades from −10⁰ to +10⁰. The bracket assertion passes that
  trivially — ±0.12 H brackets everything — which is exactly the shape of
  failure item 1 was about, arriving inside item 1's own fix. `SWEEP_Y_PAD_FLAT`
  takes the margin as a fraction of the VALUE (1.12× the value, `linear_ticks`
  True), and the guard is the **ratio of the axis to the endpoint magnitude**,
  not membership. A curve that is identically zero gets a zero pad, and
  `_scale_sweep_axis`'s `yhi > ylo` test then declines to set any limit at all
  — matplotlib's own autoscale is the honest answer there.
- **Both sweep axes print ENGINEERING UNITS (`si_tick` → `format_si`), and the
  exponent offset is gone.** The original complaint about this plot was
  literally "the y axis reads 1e-5"; after the symlog change it read `1e-10` —
  measured, `ax.yaxis.get_offset_text()` was `'1e−10'` over tick labels
  `['−2.5','0.0','2.5','5.0','7.5','10.0']` with an ylabel of `M [H]`, beside a
  table cell reading `+413 pH` and a caption reading `ideal +821 pH`. A bare
  exponent is a second notation for the same quantity on the same screen, and
  it is the one the reader has to do arithmetic on. This is the plot cursor
  readout's own rule (`_readout_value` → `format_si`), so the axis, the table
  and the caption cannot drift. The unit therefore lives on the TICKS and is
  **not** repeated in the axis label.
- **A sub-decade symlog range gets a LINEAR major locator.** Measured: with
  ylim `(310 pH, 919 pH)` the default symlog locator put `[]` labelled ticks
  inside the range and `subs=[1,2,5]` put `['500 pH']`, against
  `['450 pH','600 pH','750 pH','900 pH']` from `MaxNLocator`. Inside
  `linthresh` the symlog transform IS the identity, so a linear locator places
  its ticks exactly right; the axis is unchanged and only the tick choice
  moves.
- **The sweep caption's endpoints are printed the way the interval beside them
  is COMPUTED.** For a complex quantity (`Z`) `Sweep.interval` and
  `Sweep.bracket` are over the MAGNITUDE while the endpoints were printed as
  `.real` unconditionally: measured on `coupled_4port_diff.s4p` at 5.1 GHz the
  line read `[−2.15 mΩ, +27.5 Ω]   ideal +6.41 mΩ   open +785 µΩ` against a
  `|value_ideal|` of **26.3 Ω** — the ideal endpoint reported four orders of
  magnitude below the interval that is supposed to contain it, and below the
  `[13.1 Ohm, 26.3 Ohm]` bracket the caption's own NON-MONOTONIC line quotes
  two lines further down. A magnitude is labelled `|Z|`, so no sign is being
  suppressed — there is none to suppress — and every real-valued quantity
  (`M`, `ReZ`, `ImZ`, …) is untouched.
- **The caption's LINE CAP is a budget, not a count, and it is read off the
  WINDOW.** `SWEEP_NOTE_LINES = 3` is a count; the note is packed `side=BOTTOM`
  against the `expand=True` canvas, so it takes its whole request and the plot
  gets the remainder. Three lines are 55 px at 100% and **112 px at 150%**,
  out of a pane that got smaller rather than bigger. Measured on
  `coupled_4port_diff.s4p` with an element row **SELECTED** — the state the
  pane exists for, and the state the existing 150% guard never entered:

  | scaling | window | paned | detail | note | CANVAS |
  |---|---|---|---|---|---|
  | 100% | 980x700 | 497 | 331 | 55 | 309x276 |
  | 100% | 720x420 | 188 | 90 | 55 | 179x35 |
  | 150% | 980x700 | 268 | 114 | 112 | **309x2** |
  | 150% | 720x678 | 198 | 70 | 112 | **309x2, `ismapped()==0`** |

  The last one also hung 309 px off a 179 px parent — the only containment
  violation in the window. So everything item 1 does was invisible at 150% DPI.
  `_sweep_note_cap()` is `budget // line − ATTRIB_SWEEP_NOTE_RESERVE_LINES`
  with `budget = winfo_height() − _chrome_height()`, giving 3 / 3 / 1 / 1 on
  that table: **100% is untouched, including 720x420, where three lines over a
  35 px canvas is the documented trade** ("the sentence saying the two
  endpoints are not a bound is worth the whole pane"), and both 150% cases get
  the plot back (309x74 and 179x30, mapped and contained). **Both terms of the
  budget are independent of the note** — `_chrome_height` enumerates seven
  fixed widgets and the note is not among them — and that is the whole reason
  the rule is written against the window rather than against the sweep pane,
  which is the obvious place to read it: the pane's height comes from the sash,
  `_sash_target` reads the bottom pane's REQUESTED height, and the note's
  request is part of it. A cap read from the pane is a rule that changes the
  size it is measured from, i.e. the `_apply_editor_scrollbars` limit cycle. A
  fraction of the budget was tried and is wrong in the other direction:
  a quarter puts 720x420 at two lines, which drops rule 8's mandatory
  NON-MONOTONIC label into the "+N more".
- **THE SASH IS DERIVED FROM CONTENT, NEVER FROM THE MEASURED PANE HEIGHT.**
  Observed on the shipped window: three data rows with roughly **250 px** of
  empty space under them while the detail pane below was scrolling *and*
  clipping horizontally (`…because the other n-1` cut off at the right edge
  with an h-scrollbar under it). The initial position is therefore computed
  from the table's own row count — rows + header + a couple of spare lines, at
  the table font's linespace — and the detail pane takes the rest. Deriving it
  from the pane's measured height instead is the documented **limit-cycle
  shape**: a layout rule that reads a size it can itself change flips forever
  and `update()` never returns, hanging the GUI and the test suite together
  (`_apply_editor_scrollbars`, and the reason `ReflowRow` reads an imposed
  width and writes only a height). Row count is an input the sash cannot move,
  so it is a fixed point. **The detail text WRAPS and never scrolls
  horizontally** — it is prose, and a horizontal scrollbar on prose is the
  Treeview clipping failure this window's tables exist to refuse, arriving in
  the pane underneath them. Re-derive on a material row-count change (a new
  decomposition), and **never fight a user who has dragged it**: once moved it
  is theirs until the window closes. Measured, `sashpos` / table / detail /
  canvas with a row selected: **138 / 98 / 331 / 276** at 980x700 at 100%
  (against 279 / 239 / 198 / 160 before), **70 / 30 / 90 / 35** at the 720x420
  minimum, **107 / 48 / 114 / 74** at 980x700 at 150%, and **81 / 22 / 70 / 30**
  at the 150% enforced minimum of 720x678. `detail.yview` and `detail.xview`
  are `(0, 1.0)` at every one of them.
- **"Was that a drag?" needs the WRITE COUNTER as well as the position.**
  Comparing `sashpos` at ButtonRelease against ButtonPress is right and is not
  enough on its own: anything that moves the sash while a button is held is
  then recorded as a gesture and the split freezes for the session — i.e.
  the content-derived position above stops working, permanently, from a click.
  Measured at 100% / 980x700: `_on_sash_press()` → `_apply_sash(30)` (exactly
  what a new decomposition does) → `_on_sash_release()` left `_sash_user` True
  with no pointer movement at all. `_apply_sash` is reachable while a button
  is down from `_render_impl` (Recompute, a view switch, and the units switch's
  `refresh_attribution_windows(rerender=True)`) and from the `after_idle`
  `<Configure>`. `_sash_writes` is bumped only where `_apply_sash` actually
  writes a position, sampled at press, and required unchanged at release —
  orthogonal to the "not the last value applied" rule, which stands. The cost
  is at most a real drag that raced an automatic write in the same gesture,
  which is self-correcting; the false claim is not.
- **`across frequency: not checked` MUST CARRY THE ACTION AND ITS COST.** The
  ranking is read off one frequency, which is exactly what acceptance item 5 is
  about, and the window shipped with the check off and a badge that only said
  so. Turning it on by default is the wrong fix — it is a fresh
  `build_context` + `decompose` per frequency, `O(N³)` in the PORT count:
  measured **0.45 ms** per point on `diff_pair_4port.s4p` and **223 ms** per
  point on a synthetic dense 153-port network, so at `STABILITY_POINTS = 5`
  the check is four extra points, under 2 ms on a small file and about **0.9 s**
  on a package export. So the OFF state names the cost *on this file* and
  checks it in one click, and the ON state says what **MOVED** — which elements
  changed rank and at which frequency — not merely "checked". **A stable
  ranking is a RESULT and must be said in those words**, not rendered as the
  absence of a complaint.
- **Both badge states are FRONT-LOADED, because this Label clips.** It is
  `wraplength=0` (the `_footer_strip_text` rule — a wrapping strip costs plot
  height), so what is written last is not on screen. Measured on the real
  widget, the offer being 237 characters and the STABLE verdict 152:

  | | 1500 px | 980 px | 720 px |
  |---|---|---|---|
  | offer, 100% | 238 | 156 | 111 |
  | offer, 150% | 104 | **64** | **46** |
  | verdict, 150% | — | **65** | 48 |

  With the caveat first, at 150% / 980 px — the DEFAULT size — the reader saw
  `across frequency: not checked — a ranking read off ONE frequency` and
  nothing else: the gesture and the cost were both off screen, and at 100% /
  980 px the cost was too (the visible text ended `… across the band: 4`). The
  STABLE verdict clipped before `nothing changed places`, and a real NOT-stable
  verdict off a 32-port file clipped before the moved ranks and their
  frequencies — in each case exactly the words the rule above demands. So the
  order is **verdict → gesture → cost → caveat** and **verdict → what moved →
  span**: after the change the whole action and cost fit at 150% / 980 px
  (`not checked — press ▸: 4 more solves (4-port file`) and the gesture
  survives even at the 150% minimum.
- **THE GROUND MODEL IS SECOND ON THE SIGN STRIP, ahead of the shares rule,
  and that is a measured trade.** The strip is 137 characters and clips like
  every other one: 137 / 137 / 114 at 100% and **106 / 66 / 48** at 150% for
  1500 / 980 / 720 px. With the model last, `'Grounds:' in shown` was **False
  at 980, 860 and 720 px at 150%** — at every supported size at that scaling
  bar a maximised window there was no on-screen statement of the model that
  produced the numbers, for a choice worth a measured **7.19 dB** on the
  shipped fixture. "It is also on the control" is not a substitute: the control
  shows the FIELD, which can have been edited without a Recompute, while the
  strip shows what is IN FORCE (it renders `prov.ground_model_label`, never the
  Entry). What clips instead is the shares rule, which is also in the table's
  column heading, in Help and in the README, and which cannot change a number.
- **`_attr_zt`'s NOTES are carried, and the first thing they can say is that
  the model was not applied.** The window used to discard them
  (`zt, _gm_notes = ground_model_zt(...)`) while the CLI prints them in
  `header_notes`. Measured on `coupled_4port_diff.s4p`, probes 1/3, one
  connection row `2 short_to 4` (a legal spelling of the same network),
  `shared:L=1n` + [Recompute]: `_attr_zt` returns `zt is None` with *"The
  ground model was ignored: this spec declares no shunt element … there is no
  ground lead to model."*, so the numbers stay the DECLARED network's and
  `reference_applicable` stays **True** — while the sign strip read
  `Grounds: shared:L=1n` and both exports headed the block
  `Ground model: shared:L=1n`. A reader who typed `shared:L=1n`, saw the number
  not move and read that strip concludes the shared return is worth 0 dB, in
  the one flow that exists to settle a 6.07 dB dispute. The discriminator is
  free (`gm_z is not None and zt is None`); the marker goes on
  `ground_model_label`, so ONE assignment reaches the sign strip, Copy report
  and the CSV rather than three that can disagree, and the full sentence goes
  on the hint Label beside the control (no new pixels — it replaces the
  standing hint only while there is something more urgent to say there).
- **A session-restored ground model that no longer parses says so.** The
  retry-without-it is right — a bad value costs its own field, never the
  window — but the second half of that rule is that every other dropped field
  in the session code notes itself in the Results pane. Without it the window
  opens on `diag` with the saved model silently gone, and a ground model is
  worth a measured 7.19 dB, so a silent revert to the default is a silent
  7.19 dB.
- **The across-frequency button is a ONE-SHOT, not an expander.** Measured:
  press → `▾` plus the verdict; press again → the glyph went back to `▸` and
  the label text was UNCHANGED, still the verdict, with `_badge_row` 27 px
  throughout. `_expanded` gated no content — only the glyph — so the collapse
  offer was inert, and re-running would spend four more solves on an answer
  already on the line beside it. It is therefore disabled once `res.stability`
  is set, and the reason a disabled button owes the reader (the Keep button's
  rule) is the verdict in the Label next to it. [Recompute] builds a fresh
  `AttribResult` with `stability=""`, which makes it live again.
- **A session restores the CHOICES, never the WINDOW.** `attribution_refusal`
  turns away a trace with no numbers, and a freshly loaded session has none
  until Calculate has run — so auto-reopening would show nothing but the
  refusal, once per saved entry, before the user has asked for anything. What
  is kept is the pair, the quantity, the frequency, the view **and the
  Candidates field**, so reopening from the menu lands on what was being read.
  Candidates is on that list because it is the one field here the user TYPED
  and that this tool refuses to guess (`STRUCTURAL_CANDIDATES`' docstring says
  so in as many words); it was missing at first, so the round trip handed back
  the default pair and the only part of the state that cost anyone any thought
  was the only part not kept. `view` and `candidates` are also APPLIED on
  reopen — both were being written to the file and stored in `_RESTORED` and
  then never read, which is the same as not saving them. `_apply_session`
  **names**
  every entry it did not reopen in the Results pane. Same shape as the frozen
  trace coming back without its numbers and saying so twice; same rule as the
  rest of the session file — a bad value costs its own entry, never the file,
  and `apply_attribution_session_state` never raises.

#### The text golden reference (`tests/fixtures/attrib_reference/`)

- **ONE `.txt` PER CASE, NOT ONE JSON BLOB.** What is pinned here is PROSE, and
  a per-case file is what makes its git diff readable; `render_reference.json`
  is a blob because its five renderers are pinned as a set. `manifest.json`
  carries the case order, a describe line, a **sha256** and the capture
  environment, so a case renamed, a case deleted, a case never captured, a
  stray leftover file and a `.txt` edited BY HAND are five different loud
  failures instead of one silent pass. Both directions open with
  `newline=""` and `.gitattributes` pins `eol=lf`, so the text lives in the
  file as itself; a CRLF that crept in would fail every case with a diff
  showing no visible difference, and `test_no_reference_file_carries_a_carriage_return`
  is the guard.
- **`sweep()` PASSES `samples=160` BECAUSE `_draw_sweep` DOES.** The poles are
  closed form whatever the grid does, but `sweep_picture` measures the WIDTH of
  a pole's excursion off the SAMPLES — so with `sweep_mobius`'s default
  `samples=0` there is no curve, `pic.drawn` is empty, and the caption takes
  its NO-POLE branch on a sweep that has one. Measured: the reference read
  `poles: 0` and lost the whole `POLE at 505 nH:` line until the count matched
  the window's.
- **DETERMINISM WAS MEASURED, NOT ASSUMED.** Four captures in fresh processes,
  two of them under different `PYTHONHASHSEED`, all byte-identical. Every
  input is a shipped fixture through the shipped entry points or a literal in
  the registry; nothing is read off a clock or off a live `TraceConfig`.
- **DISPLAY-FREE BUT NOT TKINTER-FREE, and that is why it is not in
  `FAST_MODULES`.** Measured: `import pkg_rlc.panels.attrib_gui` succeeds with no
  display and `tkinter._default_root` is **None** after the whole registry has
  rendered (`test_rendering_creates_no_Tk_root` asserts it, and a formatter
  that started needing a font measurement would break it). But the module pulls
  in `tkinter`, `_tkinter`, `matplotlib` and the TkAgg backend at import time —
  it subclasses `tk.Toplevel` — and `contributions_table` reaches
  `_value_formatter`, which is `pkg_rlc.present.report`'s and is now imported at the top
  of the file (it used to be fetched from `pkg_rlc.frontend.app` through the deleted
  `_gui()` shim; the module no longer imports `pkg_rlc.frontend.app` at all). `FAST_MODULES`' one stated
  property is "it imports no tkinter", so this module does not qualify;
  relaxing that to "creates no Tk root" is a decision for whoever owns the
  list. Every import is deferred into `setUpClass`, so collecting the file
  costs nothing either way.
- **KNOWN, NOT FIXED (both are now pinned as-is):** the folded-tail line reads
  `… 1 more terms below …` — "1 more terms" — and an element whose
  contribution is EXACTLY ZERO folds into that tail rather than showing as a
  signed `+0.00 H` row, so the composed-no-gauge case's headline defect (the
  far file's ball at exactly 0) is visible as a fold count rather than as a
  zero. Both are `contributions_table` / `_fold_terms` behaviour, not this
  reference's, and changing either moves the reference.

### The two attribution reports (`pkg_rlc/present/attrib_report.py`)

One analysis, two surfaces. `tests/fixtures/cli_reference/` pins the terminal's
and `tests/fixtures/attrib_reference/` pins the window's, both byte for byte.

- **A SECTION RETURNS ITS LINES; ONE FUNCTION PRINTS THEM.** Every
  `_attr_print_*` / `_cold_print_*` hands back `list[str]` and
  `pkg_rlc_extractor._emit` is the only `print` on the path. Before this the
  only way to see what the CLI says was to run it and capture fd 1, which is
  also the only way a refactor of it could be checked.
- **THE NAMES KEEP THEIR `_print` PREFIX AND THAT IS DELIBERATE.** They are
  called by those names in the CLI, in `tests/_cli_capture.py`'s docstring and
  in three design notes. Read `_attr_print_x` as "the lines section x would
  print"; a rename is a separate, greppable change.
- **THE PRINTED ORDER IS PART OF THE REPORT.** The bracket comes before the
  ranking (`test_the_BRACKET_comes_before_the_RANKING` pins that pair on its
  own), the sign convention comes before the first signed number, and
  `--attribute` with `--cold-start` puts cold start LAST so the attribution
  stays next to the `M` it explains. The drivers own the order; the sections
  own their own text and neither knows about the other.
- **`SIGN_CONVENTION_TEXT`, `COMPOSED_BASELINE_TEXT`, `Bracket.caveat` and
  `ColdStart.blind_spot` are ONE STRING EACH and are never reflowed into a
  formatter.** Every export carries them verbatim; that is the rule they exist
  for and it survives the split untouched.
- **WHAT THE TWO SURFACES SHARE IS THE DATA SHAPING, NEVER THE RENDERING, and
  that is a measurement rather than a preference.** The window's strips CLIP at
  a measured 48 characters at 150% / 720 px and 65 at 150% / 980 px against
  this report's 95-column widest line, so the same sentence cannot serve both:
  the badge must lead with its verdict and the report can afford a paragraph.
  The blocks differ in CONTENT too — section 1 groups by provenance and prints
  Z and M side by side with the share/quadrature pair, while
  `contributions_table` folds a negligible tail, signs with U+2212, prefixes a
  swatch and shows one quantity. **Do not unify a rendering; unify the step
  underneath it.**
- **`rank_map(dec)` is that step, and it was written twice.** "Which element
  ranks where at this frequency" — sort the element terms by `|contribution|`,
  number from 1, key on the element DESCRIPTION because a lumped element whose
  admittance vanishes at one frequency is DROPPED there and an index would name
  a different element in each column. Section 8 renders it as a column per
  frequency with a `RANK IS NOT STABLE` paragraph; `stability_ranks` /
  `stability_line` render the same maps as a one-line badge. Same for
  `_attr_snap`, whose argmin `stability_ranks` had written out inline: two
  surfaces landing on different grid points is the failure the snap exists to
  prevent.
- **`pkg_rlc.panels.attrib_gui` no longer imports `pkg_rlc_extractor` from inside a
  function.** That lazy import existed only to reach `_attr_ground_model` /
  `_attr_zt` while dodging the cycle through `main()`. With the parser here the
  import is at the top, and the window is left with exactly ONE deferred import
  — `pkg_rlc.frontend.app`, where the cycle is real.
  `test_attrib_window.test_it_IS_the_CLI_parser_and_not_a_copy` still compares
  `ag.parse_ground_model` against **`pkg_rlc.frontend.cli._attr_ground_model`**
  and still guards it: the re-export makes them the same object. It is spelled
  that way and not `pkg_rlc_extractor._attr_ground_model`, which would raise
  `AttributeError` — the root shim re-exports by `import *` and star skips
  underscore names.
- **FIXED — the two surfaces used to sort an UNDEFINED delta to OPPOSITE
  ENDS, and they now spell the key identically.** `_attr_print_sensitivity`
  always keyed `(0 if isfinite else 1, -abs_delta …)`, so NaN sorts LAST, and
  its docstring states the rule ("NaN is a missing measurement, not a small
  number", the same rule `rank_coupling_pairs` follows).
  `pkg_rlc.panels.attrib_gui.sensitivity_table` keyed
  `-abs_delta if isfinite else float("-inf")`, and `-inf` is the SMALLEST key
  on an ascending sort — so NaN sorted FIRST, above the strongest real effect,
  contradicting the CLI, core's rule and `_fold_terms` twelve hundred lines up
  in the same file. The CLI's spelling won, verbatim, because it was the
  documented one. The whole rule and the three non-obvious halves of it are
  under "AN UNDEFINED READING SORTS LAST, ON EVERY SURFACE" above, which is the
  ONE place it is stated; `tests/test_attrib_window.py::TestSensitivityRanking`
  pins the window against the CLI directly, and the reference case
  `sensitivity_fake_undefined_delta` is hand-built because no `.sNp` in this
  repo reaches the branch.
- **FIXED — there was one `_e` per surface, at two precisions, and now there is
  ONE.** `pkg_rlc.present.attrib_report._e` wrote `%.6e` and
  `pkg_rlc.panels.attrib_gui._e` wrote `%.12e`, both putting a float from the
  SAME decomposition into a CSV — two files disagreeing in their last digits
  about identical numbers. **`%.12e` won**: a CSV is written to be read back by
  something else and six significant figures is lossy for no benefit, so the
  CLI moved to the window's precision and not the other way round. The window
  now imports it (`from pkg_rlc.present.attrib_report import _e as _attr_e`,
  bound to the local name `_e`, so every call site and
  `pkg_rlc.panels.attrib_gui._e` are unchanged) and there is no second
  definition. The old note said the two "cannot share a module under one name":
  that was true of the NAME and never of the precision — L5 importing L3 is the
  ordinary direction, and the local binding keeps the name.
  `test_attrib_window.TestCsvRecords.test_it_IS_the_CLI_writer_and_not_a_copy_of_it`
  asserts the two are the same OBJECT, the `parse_ground_model` precedent.
  Only the CLI's CSV moved (`--attribute-csv` / `--cold-start-csv`, six digits
  to twelve); `CSV_FIELDS` / `csv_records` are still the window's, because the
  two files have different COLUMNS — it was only ever the float that was
  duplicated.
- **FIXED — ONE CANDIDATE GRAMMAR: both separators, both word sets, on both
  sides.** `--attribute-alt` split on COMMA (`R=0.5,L=1n`) and the window's
  Candidates field split on WHITESPACE (`R=0.5 L=1n`), so a spelling a user
  had just got working on one surface failed on the other — the only one of
  the seven divergences that was a TRAP rather than a presentation choice.
  Both now take `[,\s]+` between the fields of one candidate, through
  `_FIELD_SEP`, and both take every word for a perfect short through
  `_IDEAL_WORDS` (`gnd` / `ground` were CLI-only, `0` was window-only). Both
  constants live in `pkg_rlc.present.attrib_report` and the window IMPORTS
  them, so "the two accept the same tokens" is one object and not two claims.
- **The refusal survived the widening, and that was the risk.** A token with
  no `=` is still refused on either separator, because `parse_kv_rlc_params`
  DROPS it — `R=5 m` would mean 5 Ω where 5 mΩ was typed, core's `_rlc_tokens`
  trap through a different door. **Note what that cost the CLI and why it is
  right:** with comma-only splitting, `R=5 m` was ONE field and `parse_si`
  tolerates the space, so the flag quietly meant 5 mΩ while the window refused
  the identical string by name. Now both refuse it. That is the one spelling
  this change takes away, and `tests/fixtures/cli_reference/attr_alt_bad_spacing`
  moved from a full report to a refusal because of it — the case was NAMED for
  a trap it did not actually have.
- **WHAT IS STILL NOT SHARED IS THE LIST LEVEL, and it cannot be.**
  `--attribute-alt` is repeated once per candidate; the Candidates field holds
  the whole list in one string with the comma between entries. So
  `R=0.5,L=1n` is ONE candidate on the command line and TWO in that field.
  It is not fixable from the window's side: the shipped default value of the
  field is `"open, ideal"`, so a comma that stopped separating candidates
  would break that and every saved session. What the reader gets instead is
  the READING — one Sensitivity row per candidate, labelled with what was
  parsed — and `CANDIDATE_HINT`, which now spends its middle on
  `comma between candidates, space inside one (R=0.5 L=1n)`.
- **The two expressions were MEASURED, because a converged grammar over
  divergent values would be the worse fix.** The CLI goes through
  `y_series_rlc` (`Z = R + jwL + 1/(jwC)`, then `1/Z`, then `1/y`) and the
  window builds `Z` directly. Over 14 specs x DC + 41 points from 1 MHz to
  10 GHz the worst relative difference is **2.461e-16 — about one ulp** — and
  every open/short verdict is identical, so they are two spellings of one
  formula and were left as two. **Except at DC, where they did not agree and
  the CLI was wrong**: `1/(1j*0*C)` is `inf`, so `Z` was `nan`, `y` was `nan`,
  and the non-finite branch returned a PERFECT SHORT — 0 Ω where a series
  capacitor at DC is an OPEN, the widest a value can be wrong. Reachable:
  every composed sweep keeps its 0 Hz point, so `--freq 0` with a `C=`
  candidate landed there. `_attr_series_impedance` now returns OPEN for it,
  which is what `parse_candidate` always did.
  `test_attrib_window.TestCandidates.test_the_two_impedance_expressions_agree_across_the_band`
  re-measures the whole sweep and is the guard on both halves.
- **The window's own formatters could not move here, and matplotlib is why.**
  `sweep_picture` / `si_tick` / `_si_formatter` / `sweep_caption` need
  `matplotlib.ticker`, and this module is on the CLI's import path — moving
  them would put matplotlib in the CLI and in `FAST_MODULES`. Everything above
  them (`contributions_table`, `sensitivity_table`, `report_text`) could move
  on the imports alone now that `_value_formatter` lives in `pkg_rlc.present.report`,
  but it renders a block this report does not produce, so moving it would
  co-locate rather than de-duplicate.
