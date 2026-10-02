# Rejected UI proposals

*Moved out of `CLAUDE.md` on 2026-09-12, VERBATIM. The heading below is the
section title it had there, so a cross-reference of the form
``CLAUDE.md § Rejected UI proposals`` still resolves. `CLAUDE.md` keeps the
seven names and points here.*

**These are exactly as binding as the rules that stayed: each was designed,
measured and turned down, and the measurement is what makes the refusal
re-checkable.**

### Rejected UI proposals (do not re-propose these)

Each was designed, measured, and turned down for a reason that has not changed.
They keep coming back because they sound good in one sentence, so the reason is
recorded here rather than in a commit message nobody will find.

- **A matplotlib connection SCHEMATIC in a tab beside the plot.** Three costs,
  each fatal on its own: a notebook tab strip is **26 px of permanent plot
  height**, paid on every session whether or not the schematic is ever looked
  at (the Results notebook already spends 28 px, and the plot pane is 400 px at
  the minsize); `<<NotebookTabChanged>>` on a plot notebook forces a
  `canvas.focus_set()` handler, and the M / V / Delete keys depend on canvas
  focus, so switching tabs either steals focus from the plot or silently breaks
  those keys; and a matplotlib redraw is **~10x** the cost of drawing the same
  boxes on a `tk.Canvas`, on a path that fires from the editor's variable traces
  — i.e. per keystroke. If a schematic is ever built, it is a `tk.Canvas` in a
  Toplevel, like the Ports & Roles window.
  **Followed, 2026-09-14** (`docs/conventions/trace_model.md`): the trace pi
  model draws its schematic BOTH sanctioned ways and neither rejected one — as
  TEXT returning `list[str]` (CLI and Results pane, zero plot height), and as
  a **`tk.Canvas` in a Toplevel**, `pkg_rlc/panels/tracemodel_gui.py` (deleted
  2026-10-02, see below), exactly
  as the sentence above prescribes. Both read one `PiModel`. What stays
  rejected is this entry as written: the tab and the matplotlib.
  **Superseded 2026-10-02** (`docs/design_workspaces.md` § 1.2): the Toplevel
  is gone and the Canvas schematic now fills the right side of its own
  **workspace**, picked from a strip of `ttk.Radiobutton`s under the menubar
  (`pkg_rlc/panels/workspaces.py`, `pkg_rlc/panels/ws_tracemodel.py`). That
  is not this entry coming back. **What was rejected** is a schematic TAB
  inside the RLC plot area — a picture beside the RLC numbers, paid for in
  that plot's height and that plot's focus on every session. **What was
  built** is a whole-window switch by TASK: the strip swaps the left column
  below the shared Loaded Files panel and the entire right side, so the RLC
  workspace's plot is never sharing its pane with a schematic. Each of the
  three measured costs, as it now stands:
  - *Height.* The strip is a real cost and is measured, not denied: **25 px
    tall**, and at the 1040x600 minsize the plot pane went from **422 to
    397 px** (its canvas 360 to 335) — the ~26 px this entry priced a tab
    strip at, accepted as the known cost of switching tasks in the main
    window. The outer sash (460), the editor viewport width (431) and the
    results sash (173) did not move, and a round trip through the other
    workspace gives every one back. The same 25 px also comes out of the
    LEFT column: at the minsize the Mode 5 editor viewport is 20 px against a
    23 px row, so no row is wholly on screen there — an open question for the
    owner (raise the minsize to 1040x625, or recover 25 px in the left
    column), and the three `TestFooterIsARoute` tests that need a whole row
    now map at 1040x625.
  - *Focus.* Entering the RLC workspace hands the plot canvas focus
    explicitly (`on_enter = App._focus_plot_canvas`, which calls
    `canvas.get_tk_widget().focus_set()`).
    `tests/test_workspaces.py::TestThePlotKeysSurviveARoundTrip` sends REAL
    M / V / Delete key events after switching away and back and asserts the
    marker and V-line stacks moved; mutation-checked by registering the RLC
    workspace with `on_enter=None`, which leaves focus on the Files list and
    fails it.
  - *Redraw cost.* Still not matplotlib: both canvases in the workspace are
    `tk.Canvas`, fed coordinates by `pkg_rlc/present/tracemodel_report.py`,
    and they redraw on the workspace's own edits, `Calculate all`, a change
    to the loaded files, and a resize — never from the RLC editor's variable
    traces, so a keystroke in the RLC editor costs the schematic nothing.
  **Still rejected, unchanged:** a schematic tab (or any second tab) in the
  RLC workspace's plot area, and a matplotlib schematic anywhere.
- **A `ttk.Treeview` for the MAIN results table.** It destroys the `aligned`
  units mode outright — that mode exists so digits line up column-wise in a
  monospace `Text`, and a Treeview lays out per cell in a proportional font; it
  loses select-and-copy of a whole block, which is how numbers get into a mail
  or a spreadsheet; and its row height is frozen at 20 px whatever the font (the
  hazard the Ports & Roles window has to work around with a derived style). The
  ban is on the *editable* table for a different reason (no cell editors) and
  the *read-only role list* is a legitimate Treeview; the results table is
  neither case.
- **A unicode bar chart of `|k|` in the coupling block.** Wrong metric — `|k|`
  is exactly the key `rank_coupling_pairs` rejected, because `|k| = 0.02`
  between two 2 nH coils and between a 2 nH and a 500 pH coil are different
  problems. Wrong scale — real values span `1e-4 … 1e-1` and a linear bar
  renders every one of them as an empty cell but the strongest. And it
  contradicts the signed-value invariant: a bar has no sign, and `M`, `C_c` and
  `k` are signed everywhere in this tool on purpose.
- **A large-type KPI strip above the plot ("R = 1.5 Ω  L = 2 nH  …").** It is
  the plot's cursor readout, printed twice. The readout is already the legend,
  already tracks the marker, already prints engineering units through
  `format_si`, and is already the thing a reader is looking at. A second copy
  fed by a different code path drifts from it — and if it were fed by the same
  path it would just be the readout in a worse place, costing plot height the
  strip measurements above say is not available.
- **A FOUR-TAB notebook inside the Attribution window** (Contributions /
  Sensitivity / Sweep / Across-frequency). Rejected on what the tabs *are*, not
  on pixels: the sweep is a **drill-down on the row just clicked**, so a tab
  makes the user re-pick the element they already selected and breaks the one
  gesture the pane exists for; and "does this ranking hold across frequency" is
  a **validity qualifier on the table, not a place**, so as a tab it is never
  opened and the requirement behind it is satisfied on paper only. A
  radiobutton view toggle plus a one-line badge with an expander does both jobs
  in the height a tab strip alone would have cost.
- **A `ttk.Treeview` for the Attribution window's contribution table.**
  Different case from the two Treeview entries elsewhere in this file, and it
  loses on its own measurement: eight columns need **671 px** as a Treeview at
  100% font scaling and **971 px** at 150%, against **490 / 700 px** as
  Consolas 9 text; ttk will not shrink a column below its set width even with
  `stretch=True`, and it clips with **no ellipsis and no overflow indicator**,
  so `-0.6231` silently becomes a plausible shorter number. And in
  `TkDefaultFont` the signed-number glyphs are all different widths (`-` 5 px,
  `+` 9, U+2212 9, `.` 3, ` ` 4, digits 7), so a right-aligned column of signed
  values has its decimal point wandering ±4 px per row. A read-only Treeview is
  still right for **names and roles** (Ports & Roles); it is wrong for a table
  of signed numbers. Note this reverses `design_port_attribution.md` §9's own
  recommendation — that bullet is struck through there with the measurement.
- **An eleventh Help tab for attribution or cold start.** `HELP_TOPICS`' ten
  tabs need **968 px** against `HELP_WINDOW_WIDTH = 1010`; an eleventh labelled
  `Cold start` takes the strip to **1033 px**, `Attribution` to **1037**,
  `Port attribution` to **1064**. A `ttk.Notebook` clips silently and the tab
  that vanishes is the LAST one, so the new tab would be the invisible one.
  Fold into `Mode 6 (Coupling)` and cross-reference.
