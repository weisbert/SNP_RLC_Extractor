# The trace model (`--trace-model`)

*New area, added 2026-09-14. `CLAUDE.md`'s pointer table and
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
sensitivity; the two termination fields on the window pin the row you care
about. This is `attrib`'s answer to `attrib`'s question: show the what-if
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

Drawn in the Trace Model window instead, beside the two fields that define it,
it reads the same `Bandwidth` objects the table does. Three incidental costs
also avoided: `plot_panel.md` says *"Re-measure before adding a fourteenth
control"*, `format_si` renders 0.02 dB as **"20 mdB"** in the readout, and the
y-log switch is global with no per-type hook. **Do not move it to the plot
panel without solving the terminations problem first.**

### The Trace Model window (`pkg_rlc/panels/tracemodel_gui.py`)

**GUI is this project's acceptance criterion, so a trace-model that only
reached the CLI was not finished.** The window is the deliverable; the
`--trace-model` flag is the same analysis reached another way.

**Three routes in, and they are the Attribution window's three, for the
reasons `app.py` gives beside them**: the Analyze menubar (discoverable), the
Traces right-click (already under the pointer), and one line in the Results
pane footer (reaches the reader who is staring at a number). All three land in
`open_trace_model_window`, which owns the ONLY refusal logic — so they cannot
start refusing different things — and **none of them is ever greyed out**,
because the window names its five refusals and a disabled menu entry names
none of them.

**The window authors NO geometry.** Every coordinate comes from
`pi_canvas_items` at L3 and this module hands each item to `Canvas.create_*`
with a colour and a font. Two reasons: the geometry is then assertable with no
display, in `FAST_MODULES`, which is where "where things are" can be checked
cheaply; and the window and the text block read the SAME `PiModel` through the
SAME `branch_value_lines`, so they cannot print different numbers for one
measurement. **Do not compute a coordinate in the panel module.**

**Nothing is re-solved for the pi.** `Calculate` already caches `Zmat`
(nfreqs, G, G) on the `TraceConfig` and it is the open-circuit matrix, so the
2x2 sub-block over the two measurement ports is read directly. The one solve
the window ever runs is the differential imbalance check, one frequency wide.
**`RunSnapshot` is NOT the source here and must not become one**: it keeps
only the marker-frequency `Z_matrix` and `tests/test_run_snapshot.py` pins
that the total reachable array size stays ≤ 64 whatever the sweep length.

**The two termination fields are window-local on purpose.** `source` (Ω) and
`extra load` (fF) are what-if knobs, not spec: putting them on `TraceConfig`
would make them session state and owe a migration for two numbers that mean
nothing without the window open. A bad entry falls back to the default and
redraws rather than blanking the window — a knob that goes empty mid-keystroke
is worse than one showing the default.

**Two measurement ports means no picker, and more than two is refused.** A pi
has two nodes; a trace declaring exactly two ports IS a trace, in the order
declared, and one declaring more is a coupling study. `resolve_ends` reads the
differential flag off `MeasPortRow.minus` being non-blank — which is exactly
what makes `compute_z_matrix` return the differential 2x2 — so **no session
field was added and no migration is owed**.

**Staleness, never auto-refresh.** Same rule as the Attribution window: the
result is frozen at open time with the spec signature and run number stamped
on it, `refresh_banner` compares them against the live trace, and `[Recompute]`
is the user's move. `refresh_trace_model_windows` is registered at all eight
sites the Attribution refresh is, and like it, **never raises** — it sits on a
keystroke handler, a session load and Clear All.

**The drawing's labels are part of its correctness, on the Canvas too.** A
single-ended pi ends in a ground symbol and the word `reference`; a
differential pi ends in a plain rail and says `across the pair — no reference
node`, because its shunt goes between the two conductors and there is no
reference node to draw.
`tests/test_tracemodel_window.py::TestTheDifferentialWindow::test_there_is_NO_ground_under_a_differential_pi`
pins it on the widget and `tests/test_tracemodel.py` pins it on the geometry.

**What only driving the real App could catch.** The module imported, the
layering gate was green and every pure test passed while `compute_trace_model`
still reached for `file_entry.data` — an attribute `FileEntry` does not have.
The Tk half of `tests/test_tracemodel_window.py` exists for that class of
defect, and the fixture values (`R = 1 Ω`, `L = 1 nH`, `C = 1 fF`,
`L = 8 nH`) are asserted **off the canvas items**, not off the model.

### Why the schematic was TEXT first, and how the Canvas got here

`rejected_ui.md` turned down a matplotlib schematic in a tab beside the plot
on three measurements, and ends with the sentence that governs this area:

> "If a schematic is ever built, it is a `tk.Canvas` in a Toplevel, like the
> Ports & Roles window."

**So a schematic was never banned — the tab and the matplotlib were.** The
text form shipped first because it costs zero pixels, serves the CLI and the
pane from one code path, and needs no Tk, which is what keeps
`test_tracemodel` in `FAST_MODULES`. **The `tk.Canvas` Toplevel landed on
2026-09-14 and reads the SAME `PiModel`**, so the two surfaces cannot disagree
about a number. It must not become a tab, and it must not be matplotlib.

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
