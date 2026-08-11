# Design note — port attribution (`pkg_rlc_attrib.py`)

Status: **specified, nothing implemented.** No code, no GUI, no tests exist
yet. This note is the authoritative statement of what the module computes,
what it refuses to compute, and which of its rules came from a measurement
rather than from taste. Every number quoted below was reproduced in this
repo against `tests/fixtures/` unless it is explicitly attributed to the
user's own (not-in-repo) package file.

The module imports `pkg_rlc_core` and nothing else from the repo — the same
acyclic relationship `pkg_rlc_plot` has (`pkg_rlc_plot.py` imports
`format_si` from core; core imports nothing back, and the comment above that
import says so).

---

## 1. The problem

The user extracted the mutual inductance between the same two coils, from the
same EM solve, twice, and got two answers:

```
|M| = 1.71 pH        |M| = 3.44 pH        6.07 dB apart
```

Both runs are correct. They answer different questions, and the tool prints
nothing that says so. Broken down by cause, one factor at a time:

| Cause | Contribution |
|---|---|
| Frequency (marker moved) | 0.6 dB |
| The grounding assumption | 6.1 dB |

(The two do not sum to the 6.07 dB between the endpoints, because they were
measured as separate one-factor-at-a-time deltas and the factors are not
additive. That is not sloppiness in the measurement — it is the first
appearance of the effect §5.9 exists for.)

A full-circuit simulation of the same structure back-solves **2.16 pH**, which
sits 2.03 dB above the low reading and 4.05 dB below the high one. So the
answer the user actually needs is bracketed by two numbers this tool produces
and equal to neither.

**The bottleneck is no longer EM accuracy.** The EM solve is fine. What moved
the answer by 6 dB is the reduction assumption: `compute_z_matrix` returns the
**open-circuit** matrix, every port that is not a probe and not explicitly
grounded is left **open**, and that convention is stated once in
`docs/theory.md` §8.5 and nowhere on the screen. The results pane prints a
coupling block headed `M`, `k`, `C_c`, `M/L_a`, `M/L_b`
(`pkg_rlc_gui.py::_format_coupling_block`) with no field anywhere in it that
names the assumption — no indication that a different, equally defensible
spelling of the ground balls would have printed 3.44 pH instead of 1.71 pH.
Today the only way to discover the sensitivity is to type a different spec and
notice the number moved — which is exactly how the user found it, and
exactly what a tool should not make a user
do by hand.

Two questions follow, and they need one piece of algebra between them:

- **Q2 — attribution.** Of the `Z_ab` that was reported, how much is the bare
  EM coupling and how much came in through each declared termination element?
- **Q1 — sensitivity.** If element *e* were terminated differently, exactly
  what would `Z_ab` become? Not to first order. Exactly.

## 2. Shape of the fix

One extra layer, sitting beside `compute_z_matrix`, not inside it.

`pkg_rlc_core.py::compute_z_matrix` stays untouched: it is pinned bit-for-bit
by `tests/fixtures/golden_legacy.npz`, its two `G == 1` branches in step 5f
keep the historical floating-point expressions character-for-character, and it
is the *authoritative* value of `Z_ab` in everything below. The attribution
layer recomputes the same number by a
different route and **reconciles against it** (§5.5). When the two disagree
past tolerance, the attribution layer is what gets withheld.

The layer needs a baseline that `compute_z_matrix` does not produce, so it
builds its own — and this is the one place where the two implementations
deliberately diverge in *what they compute* while agreeing on the answer.

## 3. Notation

Naming first, because one collision here is a real bug source.

> **The baseline impedance matrix is `Zbase`, never `Z0`.** `Z0` already means
> the Touchstone reference impedance throughout this repo —
> `pkg_rlc_core.DEFAULT_Z0`, `TouchstoneData.z0`, `s_to_y(s, z0)`. A second
> meaning for the same symbol, in a module whose whole job is impedance
> arithmetic, is a defect waiting to happen.

| Symbol | Meaning |
|---|---|
| `N` | ports in the file |
| `Y(f)` | `N x N` admittance from `s_to_y` |
| `A` | `N x n_nodes` incidence matrix: merges each measurement-port **side** (all ports sharing one `(group, sign)`) into ONE node; every other port keeps its own node |
| `Ybase` | `A^T Y A` — **probe sides merged, EVERY other port OPEN.** Nothing else is in it |
| `Zbase` | `inv(Ybase)` (see §5.3 for when it does not exist) |
| `w_g` | injection vector of measurement port `g`: `+1` on its plus node, `-1` on its minus node |
| `U` | `n_nodes x m`, the element vectors `u_e` as columns |
| `Zt` | `m x m` element **impedance** matrix, i.e. `D^-1`. **May be dense** |
| `G` | `U^T Zbase U` |
| `H` | `Zt + G` |
| `p_b` | `U^T Zbase w_b` |
| `r_a` | `(w_a^T Zbase U)^T` — **its own solve**, see §5.1 |
| `I` | `solve(H, p_b)` — element currents for 1 A driven into aggressor `b` |

`A` mirrors what `compute_z_matrix` step 5f does when it sums the `Y_red`
block over each probe side (its `Y_node[a, b] = Y_red[row_ix[b]].sum()` double
loop). It is the same node space; the difference is that the engine has
already deleted grounded
rows and Schur-eliminated open ones by that point, and the baseline has not.

Every non-probe declaration in the `TerminationSet` becomes exactly one
two-terminal element:

| `TerminationSet` member | `u_e` | element admittance |
|---|---|---|
| `Ground` / `Vdd` | `e_p` | infinite |
| `LumpedToGnd` | `e_p` | `y_series_rlc(omega)` |
| `ShortPair` | `e_p - e_q` | infinite |
| `LumpedBetween` | `e_p - e_q` | `y_series_rlc(omega)` |

`Zt` stores **impedance**, so an ideal element is `Zt[e, e] = 0`. **No
infinity ever appears in the arithmetic.** That is not a convenience — it is
what lets the ideal and the lumped case share one code path, and it is what
makes the Möbius sweep of §5.10 have a finite, exact endpoint at "ideal".

## 4. The mathematics

### 4.1 The decomposition

Drive 1 A into aggressor `b`, read the open-circuit voltage at victim `a`.
With no elements at all, that is the baseline by definition:

```
Z_ab^base = w_a^T Zbase w_b
```

Now add the elements. Each element `e` is a two-terminal branch carrying an
unknown current `I_e` out of the structure (sign convention in §5.11). By
superposition the node voltages are the baseline response to the drive plus
the baseline response to every element current:

```
V = Zbase (w_b - U I)
```

The constitutive law of element `e` is `u_e^T V = Zt[e, :] I` — the voltage
across the element equals its own impedance times the currents (dense `Zt`
allows an element's voltage to depend on the *other* elements' currents, which
is exactly the shared-return case of §5.2). Stacking:

```
U^T Zbase w_b  -  U^T Zbase U I  =  Zt I
      p_b      -        G I      =  Zt I
```

which is one small dense solve:

```
(Zt + G) I = p_b        i.e.       H I = p_b        I = solve(H, p_b)
```

and then

```
Z_ab = w_a^T Zbase w_b  -  r_a . I
     = direct term      -  sum over e of  I_e * r_a[e]
```

**This is exact.** It is superposition — no linearisation, no small-signal
expansion around a nominal, no first-order term dropped. `I_e` is the physical
current in element `e`. `r_a[e]` is the **baseline** transimpedance from
element `e` to the victim. The per-element terms `-I_e * r_a[e]` are an exact
additive, signed decomposition of the total.

### 4.2 Why this is a Woodbury identity

Terminating a set of ports is a rank-`m` update to `Ybase`:

```
Yterm = Ybase + U D U^T,        D = Zt^-1
Zterm = inv(Yterm) = Zbase - Zbase U (D^-1 + U^T Zbase U)^-1 U^T Zbase
                   = Zbase - Zbase U (Zt + G)^-1 U^T Zbase
```

Sandwiching between `w_a^T` and `w_b` gives §4.1 term for term. Writing the
identity in the `Zt` form rather than the `D` form is the whole trick: `D` is
infinite for an ideal ground, `Zt` is zero, and `H = Zt + G` is perfectly
well conditioned when `G` is. This is also why `H` is the matrix whose
condition number gates the tolerance in §5.5 — it is the only matrix inverted
on the attribution path.

The cost is `O(m^3)` for the factorisation of `H` plus `O(n_nodes^2 m)` to
build `G`, against `O(n_open^3)` per chunk for the engine's Schur solve. For a
package with 60 declared ground balls `m = 60`, which is nothing.

### 4.3 Prior art this rederives

None of §4.1–4.2 is new. Naming the prior art is not decoration: each of these
literatures has already found the trap that the corresponding requirement in
§5 guards against, and being able to say "this is diakoptics, and diakoptics
has the following known failure mode" is cheaper than rediscovering it.

- **Kron diakoptics / multiport network connection.** Tearing a network at
  chosen ports, solving the pieces, and reconnecting them through a small
  dense matrix at the tear. `H = Zt + G` *is* Kron's connection matrix.
- **The adjoint variable method.** `r_a` is the adjoint (victim-driven)
  solution and `p_b` is the direct (aggressor-driven) one. One extra solve
  buys the sensitivity of one output to *every* element — which is precisely
  what §5.9 exploits, and why requirement §5.1 (`r_a` gets its own solve
  rather than being aliased to `p_a`) is stated in adjoint language.
- **PEEC partial elements.** PEEC's canonical warning applies **verbatim**:
  partial inductances are individually reference-dependent and only
  collectively physical. Substitute "baseline-dependent" and it is §6.5 of
  this note, unchanged.
- **Norton path decomposition** and **transfer-path analysis (TPA)** from
  structural acoustics / NVH. TPA already knows that the sum of path
  contributions is not the sum of path magnitudes (our §5.7), that paths
  interact (§5.9), and that a path you did not instrument is invisible rather
  than zero (§6.1).

## 5. The twelve requirements

Each came from a review finding that was verified numerically. Measurements
labelled *(reproduced)* were re-run in this repo while writing this note;
the command output is in §5's individual entries.

### 5.1 Reciprocity is not assumed

`r_a` must be its own solve. Never `r_a = p_a`.

The user's real package file has reciprocity error **3.41e-10** —
about 1000x the residual this feature advertises (§5.5), and well above the
`1e-16 … 1e-13` "healthy" band `docs/theory.md` §8.10 documents. The tool
already treats reciprocity as a measured health check with its own threshold
(`pkg_rlc_core.RECIPROCITY_WARN = 1e-3`, imported by both the GUI
and the CLI so one file cannot get two verdicts). A new layer that *silently
assumes* the property the tool elsewhere *measures and reports* would
contradict its own product.

Report `|r_a - p_a| / |p_a|` as a diagnostic on every attribution.

**Measured (reproduced), and this is the uncomfortable part:** on
`diff_pair_4port.s4p` at 5 GHz with probes on ports 1 and 3 and ideal grounds
on 2 and 4, reusing `p_a` for `r_a` changes the answer by **nothing**:

```
.T  (correct)      rel err vs compute_z_matrix = 1.514e-14
reuse p_a          rel err vs compute_z_matrix = 1.514e-14
|r_a - p_a|/|p_a|                              = 8.02e-12
```

Every fixture in this repo is synthetic and reciprocal to `2.3e-16 … 5.7e-16`
(measured with the tool's own metric on `diff_pair_4port.s4p`,
`coupled_2port_gndref.s2p`, `decap_4port.s4p`). **No test in this repo can
ever catch the reciprocity shortcut.** That is the argument for the rule, not
against it: the extra solve is free, the assumption is invisible to the test
suite, and the only machine that would notice is the user's real file.

Use plain `.T`, **never** `.conj().T`. That one *is* catchable and is the easy
numpy bug here — `Zbase` is complex *symmetric*, not Hermitian, so
`.conj().T` is wrong even on a perfectly reciprocal network. Measured
(reproduced) on the same case: `.conj().T` gives a relative error of
**1.984e-04**, ten decades above the `1.5e-14` floor. A single fixture test
pins it.

### 5.2 `Zt` may be dense, and the default ground topology is not diagonal

Real package ground balls share a return plane and are mutually coupled.
Modelling them as `m` independent series inductors understates the effective
common-mode return inductance by a factor of `(1 + (m-1) k_ret)`, because `m`
independent `z` in parallel is `z/m` while `m` balls sharing one `z` is `z`.

**Measured** in review on a synthetic 4-ball network: independent 1 nH per
ball versus the same balls tied through one shared 1 nH moved `M` by
**9.60 dB** — larger than the 6.07 dB dispute this whole feature exists to
settle.

**Reproduced** here on an independently constructed, fully specified 4-ball
network (a 6-node cluster: aggressor 2 nH, victim 3 nH, four 1 nH balls, all
mutuals 0.05–0.30 nH, `R = 0.5 Ω` on each node, `f = 5 GHz`):

```
diag   (independent 1 nH per ball)   M = 14.2824 pH
dense  (one SHARED 1 nH return)      M = 36.2288 pH        +8.09 dB

k_ret = 0.00   M = 14.2824 pH   (+0.00 dB)
k_ret = 0.25   M = 22.5708 pH   (+3.97 dB)
k_ret = 0.50   M = 28.1988 pH   (+5.91 dB)
k_ret = 0.75   M = 32.4898 pH   (+7.14 dB)
k_ret = 1.00   M = 36.2288 pH   (+8.09 dB)
```

8.09 dB against the review's 9.60 dB on a different synthetic network — the
two networks differ, the conclusion does not: **the return-path topology is
worth more decibels than the thing being argued about**, and it is monotone
in `k_ret` with no threshold behaviour, so there is no "safe" default.

Provide both builders explicitly, so choosing is a decision and not a default
nobody noticed:

```python
termination_impedance_diagonal(values)             # diag(values)
termination_impedance_shared_return(z_self, z_ret) # diag(z_self) + z_ret * ones(m, m)
```

`H = Zt + G` accepts a dense `Zt` with **zero** math change and zero cost
change — `Zt` is already `m x m` and already summed into `H`. This
requirement costs one function and a docstring.

### 5.3 A singular baseline must auto-recover

`inv(Ybase)` does not always exist, and the case where it does not is the
repo's own flagship example.

**Measured (reproduced)** on `coupled_4port_float.s4p` — the fixture
`docs/theory.md` §12.F and the README both use to demonstrate Mode 6 — at
`f = 5.1 GHz`:

```
cond(Y)          = 2.475e+16
singular values  = [4.009e-02, 1.813e-02, 3.736e-18, 1.620e-18]
rank             = 2 of 4
```

Two exactly-zero singular values, because the two coils float independently
and each contributes one common-mode null direction. A naive
`Zbase = inv(Ybase)` is red on day one, on the file the documentation points
at first.

The fallback is automatic and introduces **no new user concept**: SVD `Ybase`,
partition the elements by whether `u_e` lies in `range(Ybase)` using core's
existing `pkg_rlc_core.PROBE_RANGE_TOL` (`= sqrt(PINV_RCOND)` — the same test
`pkg_rlc_core.py::_probe_impedance` already applies to probe vectors), fold
the **out-of-range** elements INTO the baseline so `Ybase'` is nonsingular,
Woodbury the rest, and **report by name**:

> ports 1, 3 are in the baseline because the structure has no reference
> without them

The partition is not a close call. **Measured (reproduced)** on the same
fixture, out-of-range residual `|| P_null u || / || u ||` with `P_null` the
projector onto `null(Ybase)` (note `U` is taken here by the element matrix, so
the SVD's left factor is not called `U` in this note):

```
u = e_1                      0.7071      out of range
u = e_2                      0.7071      out of range
u = e_3                      0.7071      out of range
u = e_4                      0.7071      out of range
u = e_1 - e_2                2.077e-16   in range
u = e_3 - e_4                2.069e-16   in range
u = e_1 - e_3                0.7071      out of range
                             PROBE_RANGE_TOL = 1e-06
```

Sixteen orders of magnitude of separation. And the physics reads straight off
it: on a fully floating structure a single-ended ground declaration *is* the
reference — it has no return path of its own, so it cannot be an element, it
can only be the baseline. `e_1 - e_3` is out of range too, because bridging
the two coils still leaves both common modes undefined; only the two balanced
pairs are in range.

Folding an element into the baseline is not a fudge, it is the correct answer,
and naming it is what stops it looking like one.

### 5.4 Structural rank check before any conditioning check

`rank(U)` deficiency has two completely different causes and they need
completely different messages.

- A port written `ground` twice via overlapping ranges (`6:1:14 ground` plus
  `10 ground`), or a `short_to` between two already-grounded ports, is a
  **spec bug**. Two identical columns in `U`.
- An ill-conditioned but full-rank `G` is genuine physics.

Reporting the first as "these elements are not individually attributable, the
physics does not separate them" is the worst available outcome: it is a
plausible sentence, it is wrong, and it sends the user to look at their
layout instead of at row 7 of their table.

So: test **structurally on integer port-index sets first** — the elements are
built from `pkg_rlc_core.py::parse_port_range` output, so duplicate and
subset relationships are exact integer facts, not floating-point ones. Name
the offending elements, using the provenance `pkg_rlc_core.py::row_sources`
already produces for the Ports & Roles window. Then, and only then, look at
`cond(G)`.

Also drop elements whose `u` is the **zero vector** after probe-side merging.
These are already inert and core already knows the class:
`pkg_rlc_core.py::inert_lumped_messages` reports exactly this — a
`lumped_between` whose two ports land on the same merged node has its
`+y, +y, -y, -y` block summed to zero, measured at `5e-12` relative (i.e.
roundoff) between `R = 20` and `R = 2000`. An element that contributes
nothing must not appear in a contribution table as `0.000`, which reads as a
measurement.

### 5.5 Reconciliation degrades, never refuses outright, and its tolerance is condition-aware

`compute_z_matrix`'s value is authoritative. The decomposition's own sum is a
**check**, not a competing answer.

**Measured (reproduced)** cross-algorithm agreement, decomposition versus an
honest `compute_z_matrix` call with a rebuilt `TerminationSet`, on
`diff_pair_4port.s4p` at 5 GHz:

```
all open                          rel = 6.992e-15
ground port 2                     rel = 2.940e-14
ground port 4                     rel = 2.972e-14
ground ports 2 and 4 (joint)      rel = 1.514e-14
cond(Ybase) = 5.055e+02   cond(G) = 4.042e+02   cond(H) = 4.042e+02
```

That is the floor on a trivial, well-conditioned 4-port: a few times `1e-14`,
consistent with the review's 5.9e-14. It is **not** the floor on the files
this feature exists for. On a 153-port package export with
`cond(Ybase) * cond(G)` in the `1e7 … 1e9` range the achievable relative
residual is `~1e-7`. A fixed `1e-9` gate would refuse every real input and
accept only the fixtures.

So:

- Gate on `C * cond(H) * eps`, with `C` a small documented constant, not on a
  fixed number.
- **Report the residual and its achievable floor side by side.** A user
  looking at `residual 4e-8 (floor 2e-8)` knows something different from one
  looking at `residual 4e-8` alone.
- Warn loudly when the residual exceeds the floor by a wide margin.
- **Withhold the per-element split, never the total,** and only when the
  residual is catastrophic. The total came from `compute_z_matrix` and is not
  in doubt; it is only the attribution of it that degrades.

For scale, `cond(Ybase)` across this repo's fixtures at mid-band:
`coupled_2port_gndref.s2p` 2.2, `diff_pair_4port.s4p` 5.1e2,
`decap_4port.s4p` 2.0e3, `coupled_4port_float.s4p` 2.5e16 (§5.3). The gate has
to span all of that.

### 5.6 Return-path budget, always reported

**Measured (reproduced)** on `diff_pair_4port.s4p`, aggressor probe on port 1,
victim probe on port 3, **one** declared ideal ground:

```
declared ground element           0.0497 %
the EM model's own reference     99.9503 %
```

and it is stable across the band (`0.0497 %` at 1 MHz through 10 GHz). With
**two** declared grounds it rises to 16.70 % and with two grounds through 1 nH
each to 12.53 % — so the number is a real function of the spec, but on the
representative single-ground case **99.95 % of the return current never
touches a declared element at all.** It flows through the reference node
inside the Touchstone file, which this tool cannot see into.

Consequence, and it must be stated on the report and not buried in a docstring:

> **the return path is inside the EM model; this decomposition cannot
> separate it.**

Report `|1^T Ybase V|` (return through the EM reference, where
`V = Zbase (w_b - U I)`) against `sum |I_e|` (return through declared
elements), always, and print that sentence whenever the former dominates.
A user who came to test a "forward path minus return path" hypothesis has to
be told, on the same screen as the numbers, that the decomposition cannot
settle it (§6.3).

### 5.7 The share of a complex term is not a complex ratio

Report the **signed projection onto the total**,
`Re(term * conj(total)) / |total|^2`, plus a **separate quadrature component**
`Im(term * conj(total)) / |total|^2`.

`|term| / |total|` is the wrong statistic twice over: it has no sign, and a
term at 90° to the total inflates it while being harmless to the answer.

**Measured (reproduced)** on the 4-ball network of §5.2 with ideal grounds
(total `Z_ab = 1.363e-02 - 1.336e-01 j`, `M = -4.2524 pH`):

| term | projection | quadrature | `\|term\|/\|total\|` |
|---|---:|---:|---:|
| direct (bare EM) | −1163.690 % | +118.767 % | 1169.735 % |
| ball 1 | +397.373 % | −35.262 % | 398.934 % |
| ball 2 | +368.329 % | −33.996 % | 369.895 % |
| ball 3 | +291.470 % | −28.309 % | 292.842 % |
| ball 4 | +206.518 % | −21.198 % | 207.603 % |
| **sum** | **100.000000 %** | — | 2439.009 % |

The projections sum to exactly 100 %; the magnitudes sum to 2439 %. The bare
EM term is *negative* — the declared grounds do not attenuate this coupling,
they reverse its sign. No magnitude-based measure can express that, and any
"cancellation factor" built on magnitudes would report 24x here where the
honest reading is "one term dominates and four cancel most of it".

The quadrature column is not decorative either: `+118.8 %` on the direct term
means the bare EM contribution is more out-of-phase with the total than it is
in-phase with it.

**Suppress the share column entirely, with a named reason, when `|total|` is
near zero.** Dividing by a total that is itself a cancellation product
produces percentages in the thousands that mean nothing.

### 5.8 Only decompose what is decomposable

A quantity decomposes iff it is

```
(fixed real scalar) x (R-linear functional of Z_ab),   evaluated at ONE configuration
```

| Decomposable | Not decomposable |
|---|---|
| `Z_ab` | `C_c = -1/(omega * Im Z_ab)` — a reciprocal |
| `Re Z_ab`, `Im Z_ab` | `Q` |
| `M = Im(Z_ab)/omega` | `\|Z\|` |
| `M/L_a` (fixed `L_a`, one configuration) | anything in dB |
| `k = M/sqrt(L_a L_b)` (fixed `L`s) | |

Note the parenthetical on `M/L_a` and `k`: they are decomposable **only** with
`L_a`, `L_b` held at their values in the configuration being decomposed. They
are not decomposable across a sensitivity sweep that also moves `L_a` —
re-terminating a ground ball moves the self impedance too.

**Measured (reproduced)**, why `C_c` has to be refused rather than approximated
— on the §5.9 case, total `Im(Z_ab)` change `+1.5825 Ω` split into two terms of
`-3.9252e-03 Ω` each:

```
C_c(total)               =     -20112.535 fF
C_c(t1) + C_c(t2)        =   16217163.884 fF        <- meaningless
```

Three orders of magnitude and the wrong sign, from a formula that is *correct*
for the total.

`C_c` is a first-class output of this tool and is the right reading whenever
`Im(Z_ab) < 0` (`docs/theory.md` §8.7; `pkg_rlc_core.py::extract_coupling_at_freq`
always computes both). It must therefore still be shown — **as a total only,
never per term.** The API must **refuse a
per-term request for a non-decomposable quantity, by name**, in the style of
the rest of the repo's refusals: not silently omit the column, not print
zeros, raise with the quantity named and the reason given.

### 5.9 Sensitivity must include group-level and cumulative, not only per-port and pairwise

With 60 ground balls, every single-port delta is `~0` — the other 59 already
carry the return — and so is every pairwise second difference. The collective
effect is order-60, not order-2. A tool that offers only one-at-a-time
sensitivity will report, correctly and uselessly, that nothing matters.

**Measured (reproduced)** on `diff_pair_4port.s4p` at 5 GHz, probes on 1 and 3,
change of `Im(Z_ab)` from the all-open baseline:

```
ground port 2 alone        -0.003925 ohm
ground port 4 alone        -0.003925 ohm
sum of the two singles     -0.007850 ohm
ground both, jointly       +1.582486 ohm
```

**201.6x larger, and the opposite sign.** In `M` terms the joint change is
`+50.3670 pH` against a sum-of-singles of `-0.2499 pH`. Two out of two
single-port probes say "this port does not matter"; both together move the
answer by more than the entire 6 dB dispute. This is with **two** elements. At
60 it is not a correction, it is the whole effect.

Provide, all exact:

**(a) Per-element swap** to each user-supplied candidate termination —
open / ideal ground / `R = z0` / series L / series R+L / shunt C — via
bordered-Schur or Sherman–Morrison off the already-factored `H`.

**(b) Group-level joint effect**: a whole connection-table row changed at
once, as a rank-`|S|` Woodbury update. **The rows already define the groups.**
`6:1:14 ground` is one `pkg_rlc_core.ConnectionRow` holding nine ports, and
`row_sources` already maps ports back to the row that wrote them. This is
free.

**(c) Non-additivity** = `delta_joint - sum(delta_individual)`, reported for
groups **and** pairs. On the measurement above it is 100.5 % of the joint
change — i.e. the interaction *is* the effect. Printing the joint number
without printing this alongside invites the reader to assume the parts add.

**(d) A cumulative / greedy curve**: rank by single-element delta, then
evaluate with the top `k = 1, 2, 4, 8, 16, …` changed **together**. This is
the only output that shows the order-60 behaviour on one line.

**(e) Leave-one-out starting from all-grounded**, which is often more
informative than one-at-a-time from all-open — the measurement above is
exactly why: from all-open both single deltas are noise, and the interesting
structure only appears once the other elements are present.

> **Every fast low-rank result MUST be verified in tests against an honest
> recompute through `compute_z_matrix` with a rebuilt `TerminationSet`. That
> is the single most important test in this module.** Not a self-consistency
> check between two expressions in the same file — a round trip out through
> the engine the golden reference pins and back. The four rows quoted at the
> top of §5.5 are that test, written by hand; the module ships with it
> parameterised over the fixtures.

### 5.10 The series-L sweep is a closed-form Möbius map, not a loop

As a function of one element's impedance `z`, holding everything else fixed,

```
Z_ab(z) = (alpha + beta z) / (gamma + delta z)
```

— immediately, because `z` enters only as `Zt[e,e]` inside `H^-1` and Cramer's
rule makes every entry of `H^-1` affine in `z` over a common affine
determinant.

**Verified (reproduced)** on the 4-ball network: fit `alpha, beta, delta` from
three points (`z = 0`, `j omega * 0.3 nH`, `j omega * 1 nH`), predict a fourth
(`j omega * 3 nH`):

```
actual = 1.3995305466e-02 + 4.4892815994e-04 j
Mobius = 1.3995305466e-02 + 4.4892815994e-04 j
rel err = 3.276e-14
```

and the `z -> inf` limit `beta/delta` reproduces a direct open-circuit solve
to printed precision (`1.487224e-02 + 3.947998e-02 j` both ways).

So deliver, exactly and without sampling:

- both endpoints — `z = 0` (ideal short) and `z = inf` (open);
- the whole `[ideal, open]` **interval** in closed form;
- `max |M|` over `z in [0, inf)` in closed form. A Möbius map takes the real
  line (here, the positive imaginary axis of a lossless series L) to a
  **circular arc**, so the extremum is analytic — no optimiser, no sweep.

**The headline scalar is the interval**, not the curve — one line, of this
shape:

> `M` lies in `[M_ideal, M_open]` over any physical ground inductance:
> **[1.71, 3.44] pH**

That is the shape of the answer to §1, with §1's two measured readings written
into it to show what it would look like if the sweep reproduces them. Whether
it does is precisely the question — §1's two numbers came from two hand-typed
specs, not from a sweep, and confirming that the closed-form interval brackets
both of them is the first acceptance test of stage 2. The sampled curve is a
secondary output for people who want to see the shape.

**The curve need not be monotone**, and the interval need not bracket it: a
series L resonates with the package's shunt C and `M` can leave the
`[ideal, open]` bracket entirely. Detect and report that — comparing
`max |M|` over the arc against the two endpoints is an exact test, so this
costs nothing and is not a heuristic. (In the reproduction above, with a
0.4 pF shunt C added on the element's node, `M` swept from `-4.2524 pH` at
`z = 0` to `+4.1554 pH` at open and stayed inside the bracket at every sampled
`L` from 1 pH to 1 µH — so this particular network does *not* escape. The
detector must still exist; it must not be a claim that escape is impossible,
because a 62-point sample proving non-escape on one network proves nothing
about the next.)

### 5.11 Declare the sign convention, globally and in every export

Three separate conventions have to be pinned, and all three flip signs:

1. **Victim reference** — which port of `a` is `+`.
2. **Aggressor drive direction** — which port of `b` receives the 1 A.
3. **Element current** — `I_e > 0` means current **out of the structure into
   ground** for `u = e_p`, and **from p to q** for `u = e_p - e_q`.

Flipping either measurement port's `+/-` flips **every** term together.
Therefore: **relative signs between terms are physical; absolute signs are
not.** That sentence goes in the report header, in the CSV header, and in the
docstring — not in the Help window only.

This is the same rule `docs/theory.md` §8.7 already states for `M`, `k` and
both `M/L` ratios, and it is on `design_connection_table.md`'s own
out-of-scope list (item 5: the sign of `k` is arbitrary and gets pasted into
review decks as if intrinsic). A per-element table with twelve signed rows is
twelve more chances to make that mistake, so this module does not get to
inherit the omission.

### 5.12 Replicate `compute_z_matrix`'s precedence exactly

The probe-node construction in this module must agree with the engine's,
including the parts that are inconsistent between modes on purpose:

- In modes 1/2/3 a `Ground` on a port **beats** a `Signal`. That is
  `compute_z_matrix`'s inner `merge_terms` (signal groups are collected
  first, then `if any(isinstance(t, (Ground, Vdd)) ...) return Ground()`), and
  it is pinned by `tests/test_core.py::TestTerminationPrecedence`.
- `pkg_rlc_core.py::build_terminations_coupling` **raises** on the same
  overlap, because a probe side is tied together and grounding one of its
  ports grounds the whole side.
- The DSL is **last-assignment-wins**, and `pkg_rlc_core.py::rows_to_dsl_text`
  emits measurement ports **before** connections so that a later `ground` row
  wins — which is what makes a table reproduce a named mode.

Build the baseline's node space by resolving the `TerminationSet` through the
same path (`pkg_rlc_core.py::resolve_meas_ports`, including the legacy `"B"`
alias applied by `_normalize_signal`), then classify what is left as elements.
Do **not** re-derive "which ports are probes" from the row tables.

If this is got wrong, the failure is specific and nasty: the reconciliation
line of §5.5 fails **on exactly the specs it exists to guard**, because those
are the specs where the two node spaces differ. A precedence bug does not
show up as a wrong number, it shows up as the checker crying wolf on the
overlap cases and being trusted nowhere.

## 6. What this method cannot do

Prominent, not a footnote. Each of these is a question a user will ask of the
output within a week, and each answer is "no".

### 6.1 It is blind to open ports

An open port contributes **no element and no term**. It is not a small
contribution; it is absent from the table.

Therefore **the contribution table must not be presented as a ranking of
ports.** It is a ranking of *declared elements*. Only the sensitivity side
(§5.9) covers ports the user has not yet decided about, and it covers them by
hypothesising a termination, not by measuring one.

This must be stated in the module docstring **and** in the report header, in
those words. A table headed "contributions by port" that silently omits the
45 open ports of a package is a wrong answer with a plausible shape.

### 6.2 The split depends on how the spec is spelled

```
6:1:14 ground                                 -> 9 elements
6 short_to 7:1:14   +   6 ground              -> 8 shorts + 1 ground
```

**The same network.** The same total `Z_ab`, to the reconciliation floor. Two
completely different per-element decompositions.

This is not a bug and it cannot be fixed — the elements *are* the user's
declarations, and two declarations describing one network are two different
tearings of it in the Kron sense. Say so in the report. A user who reorganises
their table for readability and sees the contribution column change needs to
find that sentence before they file a defect.

### 6.3 "Forward path minus return path" is not falsifiable this way

§5.6: on a representative fixture, **99.95 %** of the return current is inside
the EM black box. The decomposition can tell you what the declared elements
do; it cannot tell you what the reference plane does, because the reference
plane is not a port.

The tool must print this rather than let the user infer a null result from
small numbers in the table.

### 6.4 Re-terminating existing ports cannot evaluate new metal

A shield, an extra via, a moved trace, a widened return path — none of these
is a termination of an existing port. They change `Y` itself. **That needs a
new EM run.** No amount of Woodbury on the existing `Y` reaches them.

This is the boundary between "which of my assumptions moved the answer" (this
module) and "which layout is better" (a new solve), and it is worth drawing
sharply because the sensitivity output looks exactly like a layout-exploration
tool and is not one.

### 6.5 The decomposition is gauge-dependent

Change the baseline and **every term changes**. Fold one element into the
baseline (§5.3) and the remaining terms all move, even though the network,
the total, and the physics are identical.

What does **not** change is the element currents `I_e`. Those are physical.
The *attribution of voltage* to each of them is a choice of gauge.

This is PEEC's partial-inductance warning restated (§4.3), and it is the
reason §5.11 insists that relative signs are physical and absolute ones are
not. The report should name the baseline it used — "probe sides merged, all
other ports open, plus ports 1 and 3 folded in (§5.3)" — every time, so two
reports can be compared only when their baselines match.

## 7. The open question, deliberately not resolved

The 2.16 pH from the full-circuit simulation is a **back-solved** value from a
spur chain. It is not an open-circuit `M`. So part of the 6.1 dB gap may not
live in the grounding assumption at all — it may live in the
`M/L -> injection ratio` chain under real tank loading.

`docs/theory.md` §8.8 already documents that `M/L_a` is the **first-order
Norton approximation** to the exact short-circuit current-transfer ratio:

```
I_a / I_b = -Z_ab / Z_aa = -j omega M / (R_a + j omega L_a)
```

and that the two agree only where `omega L_a >> R_a`. **Measured
(reproduced)** on `coupled_2port_gndref.s2p` (`L_1 = 2 nH`, `R_1 = 1.5 Ω`,
`M = 0.9 nH`, so `M/L_a = 0.400000` flat):

```
f = 100 MHz    |Z_ab/Z_aa| = 0.360965      M/L_a overstates by +0.89 dB
f = 600 MHz    |Z_ab/Z_aa| = 0.398739                          +0.03 dB
f = 2.1 GHz    |Z_ab/Z_aa| = 0.399897                          +0.00 dB
f = 5.1 GHz    |Z_ab/Z_aa| = 0.399982                          +0.00 dB
```

+0.89 dB at 100 MHz on a synthetic fixture, against the **0.87 dB** the user
measured by hand on the real structure. Same effect, same size. It is not the
whole 6.1 dB — but it is not nothing either, and it is currently being
attributed to grounding by default.

**The one-evening check that settles it**, recorded here so it is not
rediscovered:

> Compute `-Z_ab / (Z_aa + Z_load)` with the circuit simulation's **own**
> termination set, and see whether it lands on 2.16 pH.

If it does, the grounding assumption is worth less than 6.1 dB and this whole
module is answering a smaller question than advertised. If it does not, the
gap is where we think it is. Either outcome is worth an evening, and neither
requires this module to exist.

Independently of the check, the module exposes both ratios, because the tool
should not force the user to redo the algebra:

- `-Z_ab / Z_aa` — the **exact** short-circuit current-transfer ratio;
- `-Z_ab / (Z_aa + Z_load)` — optional loaded form, `Z_load` user-supplied.

Both are ratios of two decomposable quantities and are therefore **not**
themselves decomposable per term (§5.8). They are totals.

## 8. API sketch

Names are indicative; the shapes are the commitments.

```python
# --- element impedance topology (requirement 5.2)
termination_impedance_diagonal(values)             -> np.ndarray   # (m, m)
termination_impedance_shared_return(z_self, z_ret) -> np.ndarray   # (m, m)

# --- the baseline (requirements 5.3, 5.4, 5.12)
@dataclass(frozen=True)
class Baseline:
    node_members: tuple[tuple[int, ...], ...]   # 0-based ports per node
    Ybase: np.ndarray
    cond_Ybase: float
    elements: tuple[Element, ...]               # provenance-labelled
    folded: tuple[int, ...]                     # element ids folded in (5.3)
    notes: tuple[str, ...]                      # named reasons, user-facing

build_baseline(Y_at_f, terminations, freq_hz) -> Baseline

# --- the decomposition (requirements 5.1, 5.5, 5.6, 5.7, 5.8, 5.11)
@dataclass(frozen=True)
class Attribution:
    quantity: str                # "Z_ab" | "M" | "Re" | "Im" | "M/L_a" | "k"
    victim: str; aggressor: str; freq_hz: float
    total: complex               # AUTHORITATIVE, from compute_z_matrix
    total_decomposed: complex
    residual_rel: float
    residual_floor: float        # C * cond(H) * eps           (5.5)
    direct: complex              # the bare EM term
    terms: tuple[Term, ...]      # signed, with projection + quadrature (5.7)
    currents: np.ndarray         # I_e, physical (6.5)
    reciprocity_diag: float      # |r_a - p_a| / |p_a|         (5.1)
    cond_Ybase: float; cond_G: float; cond_H: float
    return_budget: ReturnBudget  # EM reference vs declared    (5.6)
    sign_convention: str         # rendered, goes in every export (5.11)
    caveats: tuple[str, ...]     # 6.1, 6.2, 6.3 -- always populated

decompose(Y, freqs, terminations, victim, aggressor, freq_hz,
          quantity="Z_ab", Zt=None) -> Attribution
```

`decompose(quantity="C_c")` **raises**, naming `C_c` and the reason (§5.8).

```python
# --- sensitivity (requirement 5.9), every one exact
sensitivity_swap(base, candidates)        -> per-element x per-candidate table
sensitivity_group(base, rows, candidate)  -> one row of the table changed at once
sensitivity_cumulative(base, candidate)   -> k = 1, 2, 4, 8, 16, ... greedy curve
leave_one_out(base, candidate)            -> from all-grounded
non_additivity(joint, individuals)        -> delta_joint - sum(delta_individual)

# --- the Mobius sweep (requirement 5.10)
@dataclass(frozen=True)
class ElementSweep:
    ideal: complex; open_: complex        # exact endpoints
    interval: tuple[float, float]         # THE headline scalar
    extremum: tuple[float, complex]       # closed-form max|M| and its z
    escapes_bracket: bool                 # detected, not assumed away
    samples: tuple[tuple[complex, complex], ...]   # secondary

element_sweep(base, element_id, quantity="M") -> ElementSweep
```

**Linear algebra:** numpy only. `scipy.linalg.lu_factor` / `lu_solve` would be
the idiomatic choice for "factor `H` once, solve many right-hand sides", and
scipy 1.11.2 is present in the red zone — but `requirements.txt` has it
commented out as "not currently imported anywhere; do NOT chase it on an
isolated machine", and `deploy/_env_check.py` probes only numpy and matplotlib,
so a scipy import would create a capability tier `deploy/doctor.sh` cannot
report on. `H` is `m x m` with `m` = number of declared elements (60 for a
large package); every right-hand side of a given sweep is known up front, so
`np.linalg.solve(H, np.column_stack(rhs))` factors once internally and closes
the gap. Report `cond(Ybase)` and `cond(G)` regardless — §5.5 needs them.

## 9. Why the GUI is a separate `Toplevel`

Not a results-notebook tab, not a new plot control. Three measured budgets say
so, and CLAUDE.md records all three because each was already paid for once.

**Not a run tab in the Results notebook.** The tab strip already costs
**28 px of plot height** at the 1040x600 minsize (the right paned sash goes
167 → 195 and the plot pane 428 → 400), and it *clips* rather than wraps or
scrolls: in the 575 px pane a strip clips from **13 tabs at 100 % font
scaling and 9 tabs at 150 %**, and the tab that vanishes is the last one. The
run history already spends that budget deliberately, with two hard caps
(`pkg_rlc_gui.RUN_TABS_DEFAULT = 8`, `RUN_TABS_HARD_CAP = 12`) chosen for
legibility. An attribution page is not a run — it is a different *question*
about one run — so it would either consume the run budget or need a second
axis of tabs inside a strip that has no room for the first.

**Not a plot control.** `tests/test_plot_controls.py` exists because the strip
was measured at **918 px of controls into a 575 px pane**, and `pack` unmaps
from the end — so `Im(Z)`, `Q`, `k`, the fullscreen-quantity combobox and the
`Fullscreen` button were simply not on screen, with no scrollbar and no
chevron. `pkg_rlc_plot.py::reflow_rows` / `ReflowRow` now wrap instead of
losing the tail, and a wrap costs plot height (29 px at one line, 58 px at
two). CLAUDE.md's rule on that strip is explicit: *re-measure before
adding a fourteenth control.* This feature needs a table, not a control.

**Not a tab beside the plot either** — that is already on CLAUDE.md's
rejected list, for the connection *schematic*, and every reason transfers: a
notebook tab strip is 26 px of permanent plot height paid whether the tab is
opened or not; `<<NotebookTabChanged>>` on a plot notebook forces a
`canvas.focus_set()` handler, and the M / V / Delete keys depend on canvas
focus, so switching tabs either steals focus from the plot or silently breaks
those keys.

**So: a modeless `Toplevel`, modelled on `PortRolesWindow`**
(`pkg_rlc_gui.py::PortRolesWindow`, opened by `_on_show_ports`). That window
already solved the same set of problems and its solutions transfer directly:

- **No `grab_set`.** A modal `Toplevel` that outlives its opener blocks event
  delivery and `update()` never returns — the documented style-picker /
  scrollbar-limit-cycle failure, which hangs the GUI and the test suite
  together. `PortRolesWindow`'s own docstring says exactly this. The
  attribution window must be readable *while* editing the spec, so modeless is
  also what the feature wants.
- **Refresh from `_apply_editor_strips`**, on the same contract:
  `after_idle`-coalesced, never raises, writes to nothing but its own widgets,
  never writes a `TraceConfig`, guards on `winfo_exists()`, and sits **outside**
  that function's try/except so a window failure cannot blank the strips.
- **A read-only `ttk.Treeview` is legitimate here.** The repo's Treeview ban
  covers the *editable* connection table (no cell editors) and the *main
  results table* (it destroys the `aligned` units mode and freezes row height
  at 20 px). A contribution table is read-only and columnar, which is the
  `PortRolesWindow` case — with its two handled hazards: set row height from
  the font metrics on a **derived** style name, never by reconfiguring the
  global `Treeview`; and apply the `_fixed_map_filter` so tag colours are not
  outranked by `('!disabled', '!selected')` specs. Sort on the **raw record**,
  never the rendered string.
- **Write-back through the widgets, never into the `TraceConfig`** — the same
  rule, for the same reason: poking the trace directly is overwritten by the
  next auto-apply sync. If the sensitivity table ever grows an "apply this
  termination" button, it goes through `RowTable.add_row` /
  `PlaceholderEntry.set_value` plus `_schedule_editor_sync`, and refuses on a
  frozen trace by name.

The one thing `PortRolesWindow` does not have to solve and this one does: it
must state its baseline (§6.5), its sign convention (§5.11), and the three
"cannot" clauses of §6.1–6.3 **on the window**, not only in Help. The
Ports & Roles window's own history is the precedent — the probe-and-ground
flag had to be reworded per mode (`WARN_PROBE_AND_GROUND` vs
`WARN_PROBE_AND_GROUND_COUPLING`) precisely because a row that states the
wrong rule is worse than no row.

## 10. Staging

| Stage | Content | Verifiable without a human? |
|---|---|---|
| 0 | This note. Fixture-level verification of §5.1, §5.3, §5.5, §5.9, §5.10 by script | **Yes** |
| 1 | `pkg_rlc_attrib.py` core: baseline, elements, `decompose`, reconciliation, return budget, diagnostics (§5.1–5.8, 5.11, 5.12) | **Yes** — round trip against `compute_z_matrix` |
| 2 | Sensitivity (§5.9) and the Möbius sweep (§5.10), each verified against an honest recompute | **Yes** |
| 3 | CLI report — the whole feature is usable headless before any Tk exists | **Yes** |
| 4 | The `Toplevel` (§9) | Code yes, look-and-feel no |

Stage 1 must not start before stage 0's script is green on every fixture,
because §5.3 says the repo's flagship Mode 6 example breaks a naive
implementation and §5.1 says the repo has no fixture that can catch the
reciprocity shortcut. Both facts change what stage 1 is allowed to assume.

Stage 3 before stage 4 is deliberate: the output of this feature is a table
and a paragraph, both of which a CLI can print, and CLAUDE.md's measurements
say every pixel in the GUI is already spoken for.

## 11. Deliberately out of scope

1. **Frequency sweeps of the attribution.** One frequency at a time. The
   decomposition is `O(m^3)` per frequency and the output is a table, not a
   curve; a swept attribution needs a display design this note does not have.
   (`Trace.aux` and `pkg_rlc_plot.AUX_PLOT_TYPES` are how a swept derived
   quantity would reach the plot if that ever changes.)
2. **Attributing the diagonal.** `Z_aa` decomposes by the identical algebra
   (`a == b`), and it is probably useful, but every worked example and every
   measurement in this note is about `Z_ab`. Enabling it is one line; claiming
   it is tested is not.
3. **Automatic choice of `Zt`.** §5.2 provides two builders and refuses to
   pick. Guessing the return-path topology is worth 8–10 dB and there is no
   defensible default.
4. **Optimisation** — "find the ground-ball set that minimises `M`". The
   cumulative curve of §5.9(d) is a greedy ranking, not an optimum, and
   labelling it one would be a claim about a combinatorial problem this
   module does not solve.
5. **Reading it back into the connection table as a preset.** That is stage 4
   of `design_connection_table.md`, and it is unstarted for reasons that have
   not changed.

## 12. Rejected

- **Folding the attribution into `compute_z_matrix`.** It is pinned bit-for-bit
  by `tests/fixtures/golden_legacy.npz`, its `G == 1` branches keep historical
  expressions character-for-character for exactly that reason, and its
  `np.add.at` merge and per-frequency 5f contraction exist to preserve
  summation order. A second output from that function is a second reason for
  the golden reference to move. The attribution layer is a separate module
  that *checks itself against* the engine (§5.5); that is strictly better than
  being inside it.
- **Reporting `|term| / |total|` as the share.** §5.7 — measured, the
  magnitudes sum to 2439 % on a real case where the projections sum to exactly
  100 %.
- **A fixed reconciliation tolerance.** §5.5 — `1e-9` refuses every file above
  a few hundred ports, i.e. the entire population this exists for.
- **Diagonal `Zt` as the only option.** §5.2 — 8.09 dB, reproduced, monotone
  in `k_ret`, with no threshold to hide behind.
- **A per-term `C_c` column.** §5.8 — measured, `-20112 fF` total against
  `+16217164 fF` from summing the terms.
- **Sampling the series-L sweep instead of solving it.** §5.10 — the map is
  Möbius, verified to `3.3e-14` on a held-out point; the interval and the
  extremum are closed form, and a sample grid can miss both.
- **Presenting the contribution table as a port ranking.** §6.1 — it omits
  every open port, which on a package is most of them.
