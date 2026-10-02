# The trace model (`--trace-model`)

*New area, added 2026-09-14; the GUI half rewritten on 2026-10-02, when the
Trace Model window became the Trace model workspace. `CLAUDE.md`'s pointer table and
`docs/conventions/README.md` both point here. **These rules are exactly as
binding as the ones in `CLAUDE.md`.***

### The trace pi model (`pkg_rlc/physics/tracemodel.py`, `pkg_rlc/present/tracemodel_report.py`)

**It is a LAYER over `compute_z_matrix`, not a mode.** Same relationship
`pkg_rlc/physics/attrib.py` has, and for the same reason: it does not change
what the solver computes, it re-reads what the solver already computed. It
therefore takes **no mode integer** (which is permanent — saved trace configs
carry it), touches `compute_z_matrix` not at all, and the golden regression
stays green without regeneration. **Do not "promote" it to a mode**; the
six-step recipe in `architecture.md` is for something that changes which ports
are terminated how, and this changes nothing.

**There is no fit here, and there must never be one.** A two-port's
short-circuit matrix and a pi circuit are the same object:

```
Ym = (Y12 + Y21) / 2
Y_series = -Ym        Y_shunt_in = Y11 + Ym        Y_shunt_out = Y22 + Ym
```

is an identity, so every element comes out **exact at every frequency**, with
no least-squares and no symmetry assumption. `Zmat` is the OPEN-CIRCUIT
matrix, so its 2x2 sub-block over two measurement ports IS the two-port Z the
identity wants — which is also why the single-ended path **re-solves nothing**.
If someone proposes fitting a pi to two scalar measurements instead,
`tests/test_tracemodel.py::TestPiIsExact::test_the_symmetric_two_measurement_fit_is_the_one_that_misses`
is the measurement that answers them: on a 1.5:1 asymmetric network the fit
lands Rs to parts per million, lands Cp only as the MEAN of the two ends, and
misses Ls by ~5 %.

**The mutual term is symmetrised and the asymmetry is reported, never
swallowed.** A pi has one series branch and cannot represent a non-reciprocal
two-port at all, so `Ym` is the average; `PiModel.reciprocity` carries
`|Y12 - Y21| / max(|Y12|, |Y21|)` and anything past `RECIPROCITY_WARN` (1e-3,
the solver's own threshold, imported rather than re-declared) adds a warning
naming the frequency.

**Differential costs no new mathematics, and needs no flag of its own.** The
probe model already ties a '+' and a '-' side into one measurement port, so
`--mport "in = 1 / 2"` makes `compute_z_matrix` return the 2x2 DIFFERENTIAL
open-circuit matrix and the same identity reads the differential pi off it.
A separate ground pin is just another `--gnd`. There is no mixed-mode
transform on this path — **do not add one to the extraction**; the only place
`_TV` / `_TI` are used is the imbalance check below.

**The differential shunt is `C_diff = C12 + C1g/2`, NOT `C1g + 2*C12`.** It is
the capacitance ACROSS the pair, which is the loading a differential driver
actually sees, and it is exactly HALF the per-line odd-mode capacitance an EM
tool quotes. `PiModel.odd_mode_farads` carries the other convention and the
report prints both on the same line, because a bare "C" on a differential pi
is ambiguous by a factor of two and a reader has no way to tell which one they
were handed. Pinned both ways in `TestDifferentialIsExact`.

**A differential pi assumes common mode OPEN at both ends, and
`mode_conversion_ratio` is what stops that being an unstated assumption.** A
probed differential port carries `i_plus = -i_minus`, so its common-mode
current is zero — exact, and the whole story only for a symmetric pair. The
check takes the 4x4 SINGLE-ENDED matrix of the same four ports, transforms to
mixed mode and returns `max|Ydc| / max|Ydd|`; `MODE_CONVERSION_WARN = 0.05`.
Measured: a symmetric pair reads 9e-14 and a 30 % conductor imbalance reads
0.261, so the threshold sits an order of magnitude clear of numerical zero.
**It costs ONE extra solve, one frequency wide** (`Y[k:k+1]`), and it is
skipped — with the reason printed — on a composed network and where a probe
side ties more than one port.

**Every branch prints its own `|Q|` and the verdict that follows from it, and
that is load-bearing rather than decoration.** `Branch.reads_as` returns `'R'`
below `|Q| = 0.01` whichever way the residue points. A series branch at
`|Q| = 0.003` and a shunt at `|Q| = 267` are the same three printed numbers
and two completely different circuits; reading the first one's C as a
capacitance is the single most common way to misread this tool, and it is how
a 9.15 nF that is really a -0.17 Ω residue gets believed.

**The lumped check is two points out of a sweep that is already solved — no
extra solve, no curve.** `lumped_drift` compares the marker pi against the pi
at the BOTTOM of the sweep, on whichever of R / L / C each branch reads as,
and `LUMPED_DRIFT_WARN = 0.10` decides whether the report says the values are
reusable across the band or good at this frequency only. That is the check
that distinguishes a lumped element from a distributed line, and it exists
because the alternative — extract twice by hand and compare — is what this
feature was designed from.

### Bandwidth — THREE numbers, and conflating them is the trap

"The bandwidth of this trace" names three unrelated quantities and **only two
of them are properties of the trace at all**. They are computed separately,
printed under separate labels, and must stay that way.

1. **Model band** (`model_band_hz`) — how high the extracted pi is still ONE
   lumped pi. Assumption-free and free to compute. Its answer is usually
   `sweep`, which means *the FILE stops there, not the model* — say it that
   way, because "the pi holds to 10 GHz" read as a physics limit is wrong.
2. **Branch corners** (`branch_corners`) — `f_RL = R/(2πL)`, `f_RC = 1/(2πRC)`.
   Trace-only. They EXPLAIN the third number rather than compete with it.
3. **The −3 dB bandwidth** (`bandwidth_3db`) — what everyone means, and **not a
   property of the trace**. It belongs to trace + source + load.

**Never print one −3 dB number.** Measured on a real routed line (Rs 344 Ω,
Ls 1.69 nH, Cp 33.2 fF/end): the answer moves from **15.31 GHz at C_load = 0 to
2.09 GHz at 200 fF** — 7.3× from the load alone, and another 1.9× from a 200 Ω
source. A single figure is one arbitrary point on that curve presented as a
fact. `bandwidth_table` sweeps `DEFAULT_LOADS_F` and the report prints the
sensitivity; the workspace's Source (Ω) and Load (fF) fields pin the row you
care about. This is `attrib`'s answer to `attrib`'s question: show the what-if
rather than bury the assumption.

**H(f) comes from the RAW 2×2, never from the pi.**
`H = Z21·ZL / [(Z11+Zs)(Z22+ZL) − Z12·Z21]`, per frequency. The pi is the
picture; the transfer function has to stay right where the structure has
stopped being lumped.

**Nothing is extrapolated past the file.** No −3 dB crossing inside the sweep
means `crossed = False` and the report says `> <top>` with the droop that was
actually reached. The reference is the BOTTOM OF THE SWEEP, not DC — a file
starting at 0.1 GHz cannot say what DC does — and `reference_hz` is carried so
the report names it.

**A response that PEAKS has no −3 dB bandwidth, and the column says `peaks`
instead of a number.** A series L into a load C rises before it rolls off
(measured: **+13.6 dB**, and +50.3 dB on the lossless differential fixture);
the crossing after that is measured from a baseline the curve left long ago.
`PEAK_WARN_DB = 1.0`, clear of the +0.03…+0.14 dB a clean fixture shows from
round-off.

**`R_MEANINGFUL_Q = 1e3` exists because R is round-off on a low-loss branch,
and reasoning about it produces nonsense that looks like measurements.**
`pi_2port.s2p` is a synthetic LOSSLESS pi whose shunt reads R = 3.15 mΩ
against 160 kΩ of reactance, and the differential fixture reads **−2.87 nΩ**
— negative, because this tool never clips a sign. Dividing by either gave
corners of **"5.05e+04 THz"** and **"−57 mHz"**. Past the threshold a corner
is NaN and the report prints `--`.

**`model_band_hz` has been wrong twice; both are pinned.** It must NOT key on
`Branch.reads_as` — that is a DISPLAY verdict (`|Q| < 0.01`) and a constant
series `R + jωL` necessarily crosses it as frequency rises, which once
answered **0.32 GHz for a network built from constants**. And R must be judged
against the branch's whole `|Z|`, not against itself, or round-off swings by
orders of magnitude and answers **1 MHz** two lines under a lumped check
saying every branch was within 10 %. Element identity is the SIGN of Im(Z);
drift is on the values.

### Why `|H(f)|` is NOT a `PLOT_TYPES` entry

It was going to be one, and the reason it is not is worth keeping.
`trace_y_values(freqs, Z, plot_type, aux)` is handed the **one-dimensional**
trace impedance; the transfer function needs the 2×2 `Zmat` **and** a declared
source and load. The existing hatch for that is `aux` (the mechanism `k`
uses) — but an aux series is computed once at Calculate, so a curve fed that
way would silently be drawn for different terminations than the table beside
it names. **That is the two-surfaces-disagree failure this whole area is built
to avoid.**

Drawn in the Trace model workspace's Response canvas instead, beside the two
fields that define it, it reads the same `Bandwidth` objects the table does. Three incidental costs
also avoided: `plot_panel.md` says *"Re-measure before adding a fourteenth
control"*, `format_si` renders 0.02 dB as **"20 mdB"** in the readout, and the
y-log switch is global with no per-type hook. **Do not move it to the plot
panel without solving the terminations problem first.**

### The Trace model workspace (`pkg_rlc/panels/ws_tracemodel.py`, `pkg_rlc/services/tracenets.py`)

*Replaced the modeless Trace Model window on 2026-10-02.
`pkg_rlc/panels/tracemodel_gui.py`, `tests/test_tracemodel_window.py`, the
Analyze menu entry, the Traces right-click entry and the Results-pane pointer
line were deleted in the same change. The plan and its reasons are
`docs/design_workspaces.md` §§ 0–2; this section is what is true now.*

**Why the window went.** Measured in a real GUI walk-through on 2026-10-02:
reaching a trace model took **five steps, three with nothing on screen saying
so**; the refusal said "Mode 6", which no widget on screen says; and it gave
the same refusal with the ports filled in and only Calculate missing. The
owner's words: he wanted the loading of one PN signal in one `.sNp`, clicked
Trace model, and got "a warning I could not make head or tail of". The window
answered a question about a TRACE — a `TraceConfig` with exactly two
measurement ports, already calculated. The question asked is about a NET in a
FILE. So the workspace asks for exactly that: which file, which nets, what is
done with the other ports, at what frequency. **Nothing is inferred from the
Traces list, and no name or end is invented.**

**Stage 3 (2026-10-03): the GND field is the shared connections table**
(`docs/design_workspaces.md` § 8 item 2). Stage 1 had one `GND` entry and
"everything else OPEN"; the other ports are now the RLC editor's own
connection rows — ground, vdd, open, short, R / L / C to ground or between
two ports — through `ConnectionsTable` from `pkg_rlc/panels/setup_tables.py`
(the same component the Compare files workspace uses), with the fixed line
`Ports not listed anywhere are OPEN.` beside its caption `Other ports:`. One
concept, one view, the same in every workspace. **The RLC editor still builds
its own two tables; moving it onto the component is deferred.**

**GUI is still the acceptance criterion.** The workspace is the deliverable;
`--trace-model` is ONE net of it reached another way.

**Three modules, one job each, and the panel computes nothing.**

- `pkg_rlc/services/tracenets.py` (L2, no Tk; imports `pkg_rlc.physics.core`,
  `pkg_rlc.physics.tracemodel` and `pkg_rlc.model.trace.snap_to_grid` only):
  `validate_nets(rows, conn_rows, nports)`, `solve_net` / `solve_nets`,
  `rebandwidth`, `retarget`, `net_signature`, and since stage 3
  `ground_rows`, `net_mport_rows`, `conn_row_issues`.
  `tests/test_tracenets.py::TestNoTk` asserts it pulls in no tkinter.
- `pkg_rlc/present/tracemodel_report.py` (L3): every line of text and every
  canvas coordinate, now including `summary_table_lines` / `summary_order` /
  `SUMMARY_COLUMNS`.
- `pkg_rlc/panels/ws_tracemodel.py` (L5): `TraceModelWorkspace(app, left,
  right)` builds into two frames the App owns and hands items to
  `Canvas.create_*` with a colour and a font. **It imports
  `pkg_rlc.frontend.app` NOT AT ALL** (`TestNoAppImport` reads the source),
  and there is no `messagebox` call anywhere in it.

**Do not compute a coordinate, or call the solver, in the panel module.** The
reason is the old window's: the geometry is then assertable with no display,
and the drawing and the text read the SAME `PiModel` through the SAME
`branch_value_lines`, so they cannot print different numbers.

#### The net table

- **A `RowTable`, not a `ttk.Treeview`** — `editor_and_tables.md`'s rule for an
  editable table. Columns `Name | IN+ | IN- | OUT+ | OUT-`. A port cell is a
  combobox that also takes typed text; the dropdown offers `N  portname` when
  the file names its ports, and a pick is stored as `N` through RowTable's
  existing `from_cells` hook (no `widgets.py` change was needed).
  **What goes in a cell is the user's choice entirely.**
- **Name is required, and nothing invents one.** A row with any port typed
  and no name is red. A FULLY blank row is not painted: the table always
  keeps one blank row to type into, and `validate_nets` skips blank rows, so
  painting it would leave a permanently red empty row on screen.
- Single-ended leaves both minus cells empty. Differential needs BOTH; one
  alone marks the empty one red.
- **The rules are the builder's own refusals restated per CELL**, so the
  table and the builder cannot refuse different things: reserved names
  (`A` / `B`, `LEGACY_GROUP_NAMES`, case-insensitive), duplicate names (exact
  match — the builder's own rule), a port on both sides of one net, 1-based
  numbering. **Plus the table-level rule a per-net solve cannot see**: a port
  beyond the file's port count.
- **The probe rules of the one row model hold PER NET** (stage 3;
  `probe_rule_issues`, `editor_and_tables.md` § "The one row model"): an
  IN+ / OUT+ port in a ground or vdd row is RED on that cell (a grounded node
  has nothing to measure); a grounded `−` side is AMBER — the whole side is
  folded into ground, that end is solved SINGLE-ENDED, and the imbalance
  check is skipped with a note; an open or element-to-GND row over a probe
  port is amber. **Two refusals the per-port rules cannot see, because they
  are about a merged NODE**, are made on the cell too
  (`_net_merge_issues`): a short row tying two different ends of one net (the
  solver refuses it only at solve time, naming 0-based ports), and a short
  tying a probe `+` end to a port a ground row holds (the solver's merge keeps
  the probe and drops the ground without a word).
- **A connection row's own complaint lands on ITS cell** (`conn_row_issues`):
  the parser is asked one row at a time on top of the earlier rows that
  parsed — so a node an earlier short row named (`as coil_tap`) resolves —
  with the `Line N:` prefix stripped, R / L / C checked on their own cells
  first, and the editor's two warnings (values but no Port; an element with
  no value is a 0-ohm short). A `CellIssue` carries `table='nets'|'conn'`, and
  the issues line names a connection row as `Connection row N, <cell>: …`. A
  connection row that does not parse is every net's error: no net can be
  solved under a spec that is not one. **Two rows MAY share a port** — every net is its own solve, so DQ_P
  (1→3), DQ_N (2→4) and the pair DQ (1,2→3,4) sit side by side, which is the
  comparison this workspace exists for; the first build refused it (carried
  over from the coupling solve, where one port cannot be in two probes at
  once) and the owner-scenario walkthrough caught it
  (`test_rows_may_share_ports`). A differential side that ties
  several ports is a WARNING (amber), not an error: the pair is solved, but
  the imbalance check is skipped, because a side of several ports has no
  four-port form.
- **Validation is live and in the cell — never a dialog.** Every edit runs
  `validate_nets`; the offending cell goes `ERROR_FG` (`#b00020`) or amber
  (`WARN_FG`) through `configure(foreground=...)`, measured to work on
  `ttk.Entry` and `ttk.Combobox` in the vista theme, and the reasons are
  listed under the table as `Row N 'name', COL: message`. A dialog interrupts
  the typing it complains about, and the owner's original complaint WAS a
  dialog. `tests/test_ws_tracemodel.py` patches `messagebox` and asserts it is
  never called, in every Tk case.
- **Changing the file does not clear the table.** Cells out of the new file's
  range go red; removing the chosen file empties the File box and stales every
  row, but keeps the rows.

#### One solve per net

Each net is one `build_terminations_rows(net_mport_rows(row), conn_rows, "",
nports=)` plus one `compute_z_matrix`, on its own — the ONE row path the RLC
editor uses (stage 1 used `build_terminations_coupling([(IN), (OUT)], gnd)`).
The measurement ports are named `IN` / `OUT` (`IN_NAME` / `OUT_NAME`, neither
in `LEGACY_GROUP_NAMES`). Declaring every net's ends in ONE
`TerminationSet` would give the same numbers — measurement ports are left
open, which is what "everything unlisted is OPEN" already means — so the
split is not about arithmetic. **It is so that a failure belongs to ONE row**:
a row that does not validate becomes an error result reading `error: <why>`
and the rows beside it are still answered. `solve_net` never raises.

**It is the CLI's arithmetic, bit for bit.** `tests/test_tracenets.py::TestMatchesTheCli`
solves `pi_2port.s2p` single-ended, `diff_pair_4port.s4p` differential and
`decap_4port` with GND `3,4`, and compares against the CLI path with
`np.array_equal` on the 2x2, exact equality on every `PiModel` branch, the
mode-conversion ratio and the bandwidth table — and line for line against the
block `cli.main` prints. Since stage 3 those tests pass their GND as
`ground_rows(...)` with their assertions UNCHANGED.

**Ground-only rows are bit-identical to the old GND field — measured, then
pinned.** Before the switch: every 2-port and 4-port fixture, every IN / OUT
assignment (single-ended on each ordered pair, differential on each ordered
quadruple) under every subset of the remaining ports grounded — **368 cases**
— gives an `np.array_equal` Zmat through `build_terminations_coupling(...,
gnd)` and through one ground row. `TestGroundRowIsTheOldGndField` keeps that
measurement as a test (subsampled every 20th frequency, because the full
sweeps took 9.7 s, too slow for `FAST_MODULES`; a second test compares full
sweeps end to end through `solve_net`). Mutation-checked by the implementer:
a ground row emitted one port short fails it.

**The port-order caveat.** `compute_z_matrix` returns its measurement ports in
`resolve_meas_ports` order — by LOWEST PORT NUMBER, not by declaration. A net
whose OUT end is on a lower port than its IN end comes back as (OUT, IN), and
reading it positionally swaps the two ends with **no symptom at all on a
symmetric trace** and a wrong shunt assignment on an asymmetric one. So IN and
OUT are picked by the RETURNED `port_names`, exactly as
`cli._run_trace_model` does. `TestPortOrder` declares OUT below IN on a
SYNTHETIC asymmetric 2-port, because every shipped fixture is symmetric to
`allclose` on its two ends and no fixture can see the swap.
Mutation-checked: positional `i, j = 0, 1` fails two tests.

**The imbalance check carries the connection rows (GND in stage 1), and that
removed a GUI/CLI disagreement.** The differential pi assumes common mode
OPEN at both ends; the one-frequency, four-single-ended-probe check says how
much that hides. The old window ran it with NO ground ports while the CLI ran
it with the declared ones, so the two surfaces could print different
mode-conversion ratios for one file. **The workspace follows the CLI**: the
four single-ended probes are solved under the same connection rows the net
itself was.
`TestImbalanceCarriesGnd` has a synthetic 6-port pair that reads **1.6e-13**
with its reference ports open and **0.67** with them grounded — far either
side of `MODE_CONVERSION_WARN = 0.05`. Mutation-checked: dropping GND from the
four-port solve fails two tests.

#### The four result regions

- **Summary** — a monospace `tk.Text` from `summary_table_lines`, one line per
  net: `Net  R_ser  L_ser  C_in  C_out  f_3dB (open)  lumped`. Body lines are
  **91 characters**; a name past **14** is truncated in the Net column. Not a
  Treeview: it is a table of signed numbers (`rejected_ui.md`). A header click
  sorts by that column and a second click reverses it (` ^` / ` v`, ASCII);
  NaN cells and rows with no model sort last whatever the direction. A row
  click draws that net below. **Every row states its condition** —
  `error: <why>`, `stale`, `stale: not calculated yet` — never a blank line.
  The `f_3dB` cell follows `bandwidth_lines`' four cases (`--`, `peaks`, a
  value, `> top`); `lumped` is `ok`, `warn NN %` or `--` from `lumped_drift`.
- **Schematic** — a `tk.Canvas` from `pi_canvas_items`. A differential net
  draws no reference rail (`test_a_differential_net_draws_no_reference`); an
  error net says why on the canvas; a stale net reads `STALE -- press
  Calculate all`. Redraws on `<Configure>`.
- **Response** — a `tk.Canvas` from `response_canvas_items`, with one tick per
  solved net that overlays it dashed in a muted colour. **Two defects of the
  old window are fixed here, each pinned and mutation-checked**: the curves
  are clipped to `[RESPONSE_DB_FLOOR, RESPONSE_DB_CEIL]` BEFORE the geometry
  sees them (a peak above +3 dB used to run off the top of the canvas), and
  the load labels are a **110 px legend column** beside the plot instead of
  text written over the curves. **The marker drags here**: a ghost line follows
  the pointer and the release writes Freq, snapped to the nearest sweep point
  (`test_dragging_the_marker_moves_freq_to_a_sweep_point`).
- **Details** — collapsed by default (▸ / ▾): `pi_report_lines` +
  `bandwidth_lines` for the selected net, plus the solver's own warnings,
  which ride on `NetResult.warnings` and which the Summary does not print.

Measured at the **1040x600 minsize** (vista theme, Microsoft YaHei UI 9):
Summary **101 px** (six lines), schematic canvas **563x190**, response
**134 px**; the left column used 378 of 421 px in stage 1.

**The left column is a measured budget since the connections table replaced
the one-line GND field (stage 3)**; the numbers live in
`TraceModelWorkspace._build_left`'s docstring and
`TestLeftColumnBudget` re-measures the 1040x600 cases (`diff_pair_4port.s4p`,
two nets, the 460 px left host). Stage 1 asked 382 px and Calculate all ended
at 355 of 421 (66 px spare). The naive table (a LabelFrame, the OPEN line and
its own button row) asked 449 with one ground row — Calculate at 419 of 421 —
and with three rows asked 507 and **clipped Calculate all** (unmapped). So
three things: the table's caption and the OPEN line ride in the RowTable's own
`+ Add` row, and so do the nets table's Duplicate / Remove buttons; the
connections table shows two rows before it scrolls; and the action row and the
status line are packed FIRST at the BOTTOM of a column only as tall as it
asks, so on a short window it is the top that gives, never the button. As
built: one ground row asks 370 and Calculate ends at 343 of 421 (78 px spare);
two rows 399 / 372; three or five rows 402 / 406 (the rest scroll); the worst
case — six nets and every Kind at once — asks 520 against 421 and Calculate
still ends at 394, with Conditions squeezed off (in stage 1 six nets alone
clipped Calculate). Widest child: 443 of 460 px. **Both canvases request 150 /
110 px explicitly**, because Tk's default 265 px request squeezed the Summary
to 56 px. The Details text opens at only 56 px at the minsize; it is collapsed
by default and its sash drags.

#### Staleness by signature, never auto-refresh

The same rule as the Attribution window and the old `[Recompute]`: **a result
is never redrawn underneath its reader.** Every `NetResult` carries the
`net_signature(row, conn_rows, file_label, freq_hz)` it was solved from
(cells stripped; a ground-only table keyed as its sorted, deduped port set —
so the key is the one stage 1's GND gave — any other table by its live rows'
cells, disabled and blank rows ignored). The panel never edits a result: on
every change it re-derives the display list — a row whose signature has a
result shows it, any other row shows its last numbers (matched by name)
marked `stale`, or `stale: not calculated yet`. **Editing one row stales that
row only; a connection row or the file stales every row**
(`TestConnectionRows::test_a_keystroke_in_a_connection_cell_marks_the_rows_stale`).

**The marker frequency is NOT a staling change** (2026-10-02, owner review of
the first build): `Z2` is the whole open-circuit sweep and does not depend on
it, so a row whose only change is Freq is re-read by `tracenets.retarget` —
`extract_pi_at` on the cached sweep, plus the one-frequency imbalance solve on
a differential net — and cached under the new signature. `TestRetarget` holds
the result EQUAL to a fresh `solve_net` at the new frequency and asserts that
nothing solves more than one frequency; mutation-checked (skipping the
imbalance re-solve, and skipping the retarget in the panel, each fail).
That is what lets the Freq field and the marker drag answer at once.

**`Calculate all` solves ONLY the rows without a current `ok` result.** Error
rows always re-run, because a table-level error depends on other rows;
results whose signature has left the table are pruned. Mutation-checked both
ways — re-solving everything, and never marking stale, each fail their test.

**Source and Load are not in the signature and never re-solve.** They are
what-if knobs on the bandwidth table, exactly as the old window's two fields
were, so `rebandwidth` re-runs `bandwidth_table` on the result's own
`freqs` / `Z2`; `test_source_and_load_rebandwidth_without_a_solve` patches
`compute_z_matrix` to RAISE. A bad entry falls back to the default (1 Ω,
0 fF) rather than blanking the table.

#### Session and export

**The workspace's state rides in the session file's `workspaces` block**
under `"trace"`, at inner version `TRACE_STATE_VERSION = 2`: `version`,
`file`, `rows`, `conn_rows`, `freq_ghz`, `src_ohm`, `load_ff`, and nothing at
all at the defaults. **Config, never results** — the session file's rule — so
a loaded workspace shows every row `not calculated yet` until Calculate all. A
garbled rows list costs the rows only. **Old sessions keep working**: a
stage-1 block (no `version`, or 1) has a `gnd` STRING and no `conn_rows`, and
is read as ONE ground row (`ground_rows`; `"3, 4"` becomes `3-4`, because the
DSL is whitespace-tokenised) which computes exactly what the field did
(`TestOldSessions`). A block of a newer version raises, and the switch drops
only that block with one Log note.

**Export CSV** (`csv_rows`, pure) writes the Summary at `repr()` precision,
then every net's per-branch values, then every net's bandwidth table.

### Why the schematic was TEXT first, then a Canvas, then a workspace

`rejected_ui.md` turned down a matplotlib schematic in a tab beside the plot
on three measurements, and ended with the sentence that governed this area:

> "If a schematic is ever built, it is a `tk.Canvas` in a Toplevel, like the
> Ports & Roles window."

**So a schematic was never banned — the tab beside the plot and the
matplotlib were.** The text form shipped first because it costs zero pixels,
serves the CLI and the pane from one code path, and needs no Tk, which is
what keeps `test_tracemodel` in `FAST_MODULES`. The `tk.Canvas` Toplevel
landed on 2026-09-14 and read the SAME `PiModel`.

**On 2026-10-02 the Toplevel became a WORKSPACE, and that entry is marked
Superseded, not reversed.** What was rejected was a schematic TAB inside the
RLC plot area, paying plot height and plot focus for a picture beside the
RLC numbers. What was built is a whole-window switch by TASK: the strip under
the menubar swaps the left column (below the shared Loaded Files) and the
whole right side, and the schematic fills the right side of its own
workspace. The two measured costs are handled, not waved away — the strip is
**25 px** and the plot pane at the minsize went from **422 to 397 px** (canvas
360 to 335), accepted as the known cost; and entering the RLC workspace
hands the plot canvas focus explicitly, pinned by
`tests/test_workspaces.py::TestThePlotKeysSurviveARoundTrip` with real
M / V / Delete key events. The full note is in `rejected_ui.md`. **It is
still not matplotlib, and the RLC plot area still gets no schematic tab.**

**The drawing's labels are part of its correctness.** The single-ended and
differential drawings share one skeleton because they ARE one topology, and
differ only in what the nodes and the bottom rail mean: single-ended shunts go
to the declared reference, differential shunts go across the pair and there is
**no reference node in the picture at all**. Drawing a ground rail under a
differential pi is a lie that reads as a diagram, which is the one thing a
schematic must not be —
`TestReportRendering::test_the_differential_drawing_has_NO_reference_rail`
pins it.

**The layout is computed from the value strings, never hard-coded.** Both legs
must land in one column whatever the values are; that is asserted across three
decades of element value in
`TestReportRendering::test_both_legs_stay_in_one_column_at_any_value_width`.
Ohms are spelled `U+03A9` because `report.py`'s `_TABLE_BASE_UNITS`, its three
`_value_formatter` call sites and the CLI's coupling block all are, and a
second spelling would be a second vocabulary for one quantity.

### What the CLI surface owes

`--trace-model IN,OUT` is the fourth member of the `--attribute` /
`--cold-start` family: one parent flag naming two measurement ports, inert
without them, refused by name outside `--mode coupling`, and resolving its two
sides through the SAME `_attr_parse_pair` / `_attr_resolve_port` those two use
(so a name wins over a position, identically, in all three). It prints AFTER
the coupling report, because it is a picture of the numbers printed there.

**Its golden cases pin the element VALUES, not just the layout.**
`pi_2port.s2p` carries `R_series = 1.0`, `L_series = 1e-9`,
`C_shunt_each_port = 1e-15` in its own header and `diff_pair_4port.s4p`
carries `L_loop = 8e-9`; the two capture cases reproduce exactly those, so a
reference that froze only the drawing could not let the arithmetic drift
underneath it. Adding the flag moved `--help`, so
`tests/fixtures/cli_reference/` was regenerated in the same commit — which is
the rule for that directory, not an exception made here.

**What the CLI does NOT have that the workspace does (2026-10-02), recorded
rather than owed silently.** `--trace-model` is ONE net per invocation: there
is no table of nets, no Summary, and no CSV of the trace model — the
workspace's `Export CSV` (Summary, per-branch values, bandwidth tables) has no
command-line equivalent. The other direction too: the CLI can solve a net on
a composed network, and the workspace works on one file. Since stage 3 the
workspace's connection rows hold ground, vdd and short rows (the CLI's
`--gnd` / `--vdd` / `--short`) and also R / L / C elements and named nodes,
which the CLI has no flag for. **Where both surfaces can express the same net
they must print the same numbers**, and
`tests/test_tracenets.py::TestMatchesTheCli` is what holds them to it — it is
the reason the imbalance check now carries the declared ground on both. A future
`--trace-model` that takes several nets should call
`pkg_rlc.services.tracenets.solve_nets` and `summary_table_lines` rather than
grow a second loop.
