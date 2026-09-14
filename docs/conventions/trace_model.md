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

### Why the schematic is TEXT, and what would let it be a Canvas

`rejected_ui.md` turned down a matplotlib schematic in a tab beside the plot
on three measurements, and ends with the sentence that governs this area:

> "If a schematic is ever built, it is a `tk.Canvas` in a Toplevel, like the
> Ports & Roles window."

**So a schematic was never banned — the tab and the matplotlib were.** The
text form is deliberately the half that shipped first: it costs zero pixels,
serves the CLI and the results pane from one code path, and needs no Tk, which
is what keeps `test_tracemodel` in `FAST_MODULES` (38 tests / 0.078 s). **A
`tk.Canvas` Toplevel is the sanctioned second step and must read the SAME
`PiModel`**, so the two surfaces cannot disagree about a number. It must not
become a tab, and it must not be matplotlib.

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
