# The plot panel (axes, control strip, cursor readout)

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### The plot panel's axes (what range they show, what unit they say)

`tests/test_plot_axes.py` is the guard (41 tests, no display), and every claim
below was mutation-checked. Reported as one complaint — *"plotting R, the
values are ~15.2 ohms but the y axis reads milliohms with 1e14, so the curve
is a flat line at y=0"* — and it was two independent defects.

- **A POINT THE X SCALE CANNOT DRAW MUST NOT OWN THE Y RANGE.** matplotlib's
  y autoscale ranges over the whole data set; a log x axis (the shipped
  default) cannot draw `f <= 0`. So a Touchstone file with a DC row hands the
  y axis a point that never appears on screen — and **every composed sweep
  KEEPS 0 Hz**. Measured through the real panel on a flat 15.2 Ω curve
  carrying one large finite value at 0 Hz: `ylim (-5e+12, 1.05e+14)`, offset
  text `'1e14'`, the curve at **4.545%** of the axis height, and the culprit
  at `f = 0` **outside** `xlim`. That last part is the whole signature and is
  what separates this from an ordinary pole — the reporter said *"I cannot
  see any outlier at all"*, and they were right, because it is not drawn.
  **A true `inf` there is harmless** (matplotlib drops non-finite values from
  the range), so it takes a large FINITE value — which is exactly what
  inverting a near-singular `Y` at DC produces, i.e. the same physics the
  `lstsq`/NaN invariant above is about. `test_an_infinite_value_was_never_the_problem`
  is that control, and without it the whole class would pass against a fix
  that only filtered non-finite values.
- **THE OVERRIDE ONLY FIRES WHEN SOMETHING IS ACTUALLY HIDDEN, and that is
  the safety property the fix rests on.** `drawable_extent` returns
  `n_hidden`, counting points that are FINITE (so matplotlib would have
  ranged over them) and undrawable. At zero, `_apply_y_axis` sets no limit at
  all and matplotlib's autoscale is left untouched — so a healthy plot is
  byte-identical to what it always was, asserted against a bare matplotlib
  figure of the same points rather than against a literal.
- **What is not drawn is NAMED on the axes** (`N pt(s) at f≤0 not shown`,
  `fontsize=6`, lower right). A point the reader can neither see nor infer is
  what caused this; removing its influence in silence would be the same
  defect facing the other way.
- **`trace_y_values` RETURNS SI, AND THE PLOT TYPE NAME NO LONGER DESCRIBES
  ITS UNITS.** `R(mOhm)` yields ohms, `L(nH)` henries, `C(pF)` farads. Those
  names are a stored session field and are quoted in the editor's hints, the
  README and `CLAUDE_CODE_PROMPT.md`, so they survive as IDENTIFIERS with a
  historical parenthetical. One unit convention through the module is what
  lets the axis, the cursor readout and the M marker read a value the same
  way, with the prefix applied once where it is RENDERED — there is no
  scale-to-SI factor left anywhere, so nothing has to remember to apply one.
- **THE Y AXIS NAMES THE SI BASE UNIT AND LEAVES THE EXPONENT TO
  MATPLOTLIB; THE READOUT AND THE M MARKER CARRY THE PREFIX.** `L (H)` with
  `1e-9` in matplotlib's corner offset, not `L (nH)` with an auto-chosen
  prefix. **This is the user's decision and it REVERSES an earlier one**: an
  axis whose prefix follows the data changes meaning between one glance and
  the next, so two subplots and two sessions stop being comparable. The
  division of labour is the point — **the axis is for shape and magnitude,
  the cursor readout and an M marker are for reading a value**, and both of
  those go through `format_si` and print `2 nH` / `300 pH` / `15.2 Ω`.
  Note the tension with the Attribution window's sweep-axis rule, which
  bans the bare exponent: that rule was written for a plot whose axis read
  `1e-10` while the table BESIDE it read `+413 pH` — a mismatch, not a
  notation. Here the two surfaces agree about the quantity and differ only
  in where the prefix is applied. **A perfectly flat curve renders its own
  numerical noise as an additive offset** (`1e-21+2e-9` on a synthetic
  fixture that is 2 nH to thirteen digits); that is matplotlib being honest
  about a degenerate range, it is what the old rendering did too, and it is
  not something to paper over.
- **THE X AXIS KEEPS THE ENGINEERING FORM**, per tick on the log scale
  (`1 MHz`, `1 GHz`). It is the same `_label_axis`, switched by
  `engineering=`. **Do not use matplotlib's `EngFormatter` for it**: that
  picks a prefix PER TICK on a linear axis too, rendering `[500 pH, 2 nH]`
  as `500 pH, 750 pH, 1 nH, 1.25 nH` — mixed units down one column, the
  failure the results pane's `aligned` mode exists to prevent.
- **THE SYMLOG `linthresh` IS DERIVED FROM THE DATA, AND THE MOVE TO SI IS
  WHY IT HAD TO BE.** The panel used a fixed `1e-6`, survivable only while
  the values carried a prefix (2 nH was `2.0`); in henries it is `2e-9` and
  every point falls inside the linear band, so symlog degenerates into the
  linear axis it replaces — the defect CLAUDE.md already names for the
  Attribution sweep, arriving here through the units change.
  `symlog_linthresh` takes the smallest non-zero drawable magnitude, floored
  at `SYMLOG_MAX_DECADES` below the largest so one stray near-zero sample
  cannot open a hundred empty decades. **`set_yscale` therefore happens in
  `_apply_y_axis`, after the curves are drawn and before `set_ylim`**, since
  the band comes from the data and changing the scale resets the limits.
- **`tick_label_sig` IS WHAT MAKES THE X RELABELLING SAFE, AND IT MUST BE
  ALLOWED TO GIVE UP.** It raises precision until no two rendered tick labels
  are equal (KiCad's `formatLabels` loop — it DETECTS the collision rather
  than predicting it), capped at `TICK_SIG_TRIES`. Past the cap the axis is
  handed back to matplotlib, whose exponent-offset notation is the right
  rendering of a narrow range around a large value, and the label falls back
  to the unit the stored values are ALREADY in. Measured: 500.000001 to
  500.000003 mΩ renders as five identical `500 mΩ` under a per-tick
  `format_si` and five identical `500` under any fixed `%g`. **Returning a
  precision instead of `None` from the exhausted loop is a real mutation and
  is caught.** This is also why `pkg_rlc.panels.attrib_gui`'s `si_tick` was
  NOT ported across: per-tick `format_si` is degenerate on exactly this case.
- **A DIMENSIONLESS QUANTITY TAKES NO PREFIX AND NO UNIT.** `Q` and `k` are
  labelled with the bare name; `format_si` renders `k = -2.412e-4` as
  `-241 u`, a micro-nothing. Same rule as the results views.
- **THE `PLOT_TYPES` STRINGS DID NOT MOVE and must not.** They are a stored
  session field (`view_state()["types"]`), and they are quoted in
  `panels_editor`'s `MUTUAL_CURVE_HINT`, the README and `CLAUDE_CODE_PROMPT.md`.
  Only the DISPLAY changed, through `PLOT_TYPE_NAMES` — **a table, not a
  strip-the-parenthetical rule**, because `Re(Z)` and `Im(Z)` would strip to
  `Re` and `Im`. **KNOWN, NOT FIXED:** the checkbox labels in the control
  strip still read `R(mOhm)` while the axis reads `R (Ω)`. Changing them
  ripples into the two hint strings and two docs and is a separate change.
- **The whole of `_apply_y_axis` is guarded**, and falls back to
  `set_ylabel(plot_type)` with matplotlib's own autoscale. An axis that
  cannot be scaled is worth less than a curve that cannot be drawn — the same
  rule, and the same reason, as the Attribution window's sweep axis.
- **PAD Y, NEVER PAD X.** Every surveyed tool that plots a swept measurement
  does this — Qucs 10%/0%, KiCad 3%/0% — because the sweep endpoints ARE the
  data and blank space past them reads as measurements nobody took.
  matplotlib's 5%/5% is the outlier and is what this panel inherited: a
  1 MHz .. 10 GHz sweep was drawn inside an axis running to 16 GHz. It is one
  `ax.set_xmargin(0.0)`, and it must come BEFORE anything reads a limit —
  `get_xlim()` is what forces the autoscale. **The asymmetry is the rule**;
  stripping the margin from BOTH axes is the easy over-correction and
  `test_the_y_axis_still_keeps_its_margin` is what catches it. The frequency
  MARKER can still pull the axis out past the sweep (`axvline` joins the
  autoscale), which is pre-existing and is what keeps a marker parked past
  the end reachable rather than clipped away.
- **`_label_si_axis` IS ONE IMPLEMENTATION FOR BOTH AXES.** x and y differ
  only in which accessors it is handed, so the two cannot come to disagree
  about what a prefix means. The frequency axis is `("Freq", "Hz", 1.0)`, so
  log x — the shipped default — gets the per-tick form (`1 MHz`, `1 GHz`,
  label `Freq`) and linear x gets one prefix on the label (`Freq (GHz)`,
  bare ticks). There is deliberately no `_label_y_axis` any more; a test
  asserts its absence, because a second implementation is exactly how the
  two would drift.
- **ZERO ALIGNMENT NEEDS NO CODE HERE, AND THAT IS MEASURED, NOT ASSUMED.**
  KiCad shifts its whole tick grid so a tick lands exactly on zero, and it
  has to: its own step search starts from a floored multiple and picks up
  float offset. matplotlib's `MaxNLocator` places ticks at integer multiples
  of a nice step, so **zero is already on the grid whenever it is in range**
  — measured over the sign-crossing ranges R / L / C / M / k really produce,
  9 of 9 with zero in range, on linear AND on symlog. So the port was
  REFUSED rather than written. `TestZeroAlignmentNeedsNoCodeOfOurs` pins the
  property anyway — it is a property of the LOCATOR, not of our code, and a
  later change that swaps the locator (a percentile autoscale, say) has to
  notice that it just took this away.

### The plot panel's control strip

`tests/test_plot_controls.py` is the guard, and every claim below was
mutation-checked. Before that file, **no test in the repo touched this panel**,
which is how it stayed broken. `ReflowRow` / `reflow_rows` themselves now live
in **`pkg_rlc/widgets/widgets.py`** — `pkg_rlc.widgets.plot` re-exports them, so the spelling
every caller and this guard use is unchanged; the strip that consumes them is
still here.

- **The strip WRAPS; it must never be a plain `pack(side=LEFT)` run.** Measured:
  thirteen controls asking **918 px** into the 575 px right-hand pane at the
  declared 1040x600 minsize, and pack unmaps from the END — so `Im(Z)`, `Q`,
  `k`, the fullscreen-quantity combobox and the `Fullscreen` button were simply
  not on screen, with no scrollbar, no chevron and no other route to them. Two
  of those have no alternative at all: `k` is the coupling quantity Mode 6
  exists to produce (`AUX_PLOT_TYPES = ("k",)`) and Fullscreen is the documented
  escape hatch for a readout box too wide for a 4-subplot grid. It was not only
  the minsize — `_clamp_to_screen` opens the window at `min(1500, screen-80)`,
  so on the 1280-logical-px laptop its own comment names the app opened 1200 px
  wide and Fullscreen was off screen out of the box (sweep: 1040/1100 lost `k`
  and Fullscreen, 1160–1280 lost Fullscreen, 1320+ was whole).
- **`ReflowRow` lays out by `place`, and that is load-bearing TWICE.** Place
  does not propagate, so the strip's requested width is ~1 px and the 918 px no
  longer travels up through `PlotPanel` into the PanedWindow sash; and the wrap
  decision reads the strip's IMPOSED width (`fill=X` from the parent) and writes
  only its height, which cannot change that width. That makes it a **fixed
  point**, not the limit cycle `_apply_editor_scrollbars` documents — a layout
  rule that reads a size it can itself change flips forever and `update()` never
  returns, hanging the GUI and the test suite together. Do not "simplify" it to
  `grid` or `pack`.
- **A wrap costs plot height, so it must happen only when needed.** Measured:
  the strip is 29 px (one line) at 1500 and 58 px (two) at the 1040 and 1200
  widths where it does not fit. The default window pays nothing.
- **The assertion is "wholly inside", not `winfo_ismapped()`.** A placed widget
  stays mapped while it hangs off the right edge, so the test checks
  `x + width <= strip width` and `y + height <= strip height` for every child.
  Re-measure before adding a fourteenth control.

### Cursor readout (the plot's marker / V-line labels)

- **One cursor gets ONE readout, never one label per curve.** A vertical cursor
  crossing N curves used to draw N annotations, all anchored at the same x with
  the same fixed `(6, 4)` pt offset and no collision or boundary check. That is
  fine at N=1 and unreadable on a coupling plot, which is the normal case, not a
  corner: one mode-6 trace with 3 measurement ports expands to 3 self + 3 mutual
  curves and the mutual ones all sit near zero. Measured over 4 subplots: 21
  labels, **19 overlapping pairs, 20 of 21 crossing into the neighbouring
  subplot**, and the frequency printed 21 times for one cursor.
  `tests/test_plot_readout.py` is the guard and asserts the two structural
  properties directly off the rendered text — no two texts overlap, no text
  leaves its axes. It is the first coverage `pkg_rlc.widgets.plot` has ever had.
- **The readout box IS the legend.** It carries the colour/linestyle swatch and
  the curve name, so drawing a separate legend next to it is the same names
  twice competing for the same empty corner. When there is no cursor to read
  (`show_marker` off, or the `Readout` toggle off) `_draw_plain_legend` restores
  the old names-only legend on the first axes — do not let both exist at once.
- **The readout's corner is scored from the CURVE DATA and frozen until the next
  redraw.** matplotlib's `loc="best"` also weighs the legend's own size, so a
  value going from `2 nH` to `2.05 nH` flips the box to another corner while the
  user is dragging the cursor. `_pick_readout_loc` caches per axes and `redraw`
  clears the cache. The candidate set deliberately includes `center left` /
  `center right`: on a coupling plot the self curves sit at the top and the
  mutual ones near zero, so all four *corners* hold data and the middle band is
  the only empty space.
- **The name column is a measured budget, not a constant.** 4 subplots give each
  axes ~210 px against ~320 px at 3, and a box wider than its subplot is exactly
  the failure being fixed. `_name_budget` derives it from `ax.get_window_extent()`
  in em of the readout font (so it holds at any DPI), with `READOUT_NAME_MIN = 8`
  as a floor — rows that cannot be told apart are worse than a slightly wide box,
  and that corner is what the `Readout` toggle and Fullscreen are for. `_fit_names`
  keeps the **tail**: `osc_primary_coil_00` / `_01` differ only at the end, and
  `MAX_LABEL_LEN` (a legend-width rule) must not be applied before the shared
  trace prefix is stripped or they arrive already identical.
- **A value column is as wide as its widest cell OR its header.** Sizing on the
  values alone put a 7-char `5.1 GHz` over a 5-char column and threw every
  multi-cursor heading one place off the numbers it names.
- **V lines are cursors, so they are columns in the same box** — they had the
  identical per-curve-label bug and can be stacked several deep. `_anno_stack`
  entries are `(kind, artists)` and a `"v"` entry also owns one element of
  `_vline_freqs`; Delete must pop both or the readout keeps a column for a line
  that is no longer drawn.
- **The readout prints engineering units** (`_readout_value` → `format_si`):
  `300 pH`, not `0.3 nH`; `1.5 Ω`, not the `1.5e+03 mΩ` a plain `%.3g` produced.
  Non-finite reads `--` and the row **stays** — dropping it shifts every row
  below and the swatches stop lining up with the curves. This is why
  `pkg_rlc.widgets.plot` imports `pkg_rlc.physics.core` (acyclic: core imports nothing back);
  a second copy of the rule would let the plot and the results pane disagree.
- **A dragged box must be captured BEFORE the legend is replaced, not from a
  button-release handler.** The box is draggable (`set_draggable(True,
  update="loc")`) and is rebuilt on every cursor move, so the position has to be
  read back off the old artist or it snaps home on the next marker drag.
  `DraggableLegend` registers its own release callback when the box is built —
  *after* `_PlotView.__init__` registered ours — so a release handler here runs
  first and reads the position from before the drag. `_capture_manual_locs` is
  therefore called at the top of `_refresh_marker` and `redraw`, which does not
  depend on callback order at all. It keys on **plot type, not axes id**, so a
  placement survives Calculate rebuilding the axes. A dragged legend stores
  `_loc` as a 2-tuple and an auto-placed one keeps an integer code — that is the
  discriminator.
- **`loc=(x, y)`, never `Legend.set_loc`.** `set_loc` is matplotlib 3.8+; the red
  zone is pinned to **3.7.2** (see `ENVIRONMENT.local.md`). The 2-tuple form of
  `loc=` is accepted by both and is exactly what `update="loc"` writes back.
- **The readout wins the press gesture, and double-click is the way out.**
  `_legend_at` gates `_on_press`: a box sitting over the marker line would
  otherwise move the cursor and the box with one drag. Double-clicking inside it
  drops the manual placement — and it must `remove()` the legend *before*
  `_refresh_marker`, or the capture reads the dragged position straight back out
  of the artist and undoes the reset.
- **A test that parks the readout where the marker is not passes without the
  guard.** `test_pressing_on_the_box_does_not_grab_the_marker` read green that
  way once. It now computes the marker's x and asserts the press lands within
  `MARKER_PIXEL_TOLERANCE` of it before testing anything — keep that
  precondition assertion, it is what stops the test rotting back into a
  tautology.
