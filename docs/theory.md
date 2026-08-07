# PKG RLC Extractor — Theory

This document explains the math and the modeling choices behind the tool. It assumes familiarity with S- and Y-parameters at the level of any standard RF text.

**Section 8** (the `+/-` probe model, and the M / k / M-over-L derivations) assumes no prior
coupling theory — it builds the whole thing from Faraday's law and one matrix identity, and
explains why each modelling choice was made rather than only what it is. If coupling is what
brought you here, sections 2, 3 and 8 are enough.

---

## 1. What is a Touchstone file?

A Touchstone (`.sNp`) file is a tabular dump of the **S-parameter matrix** of an N-port linear network as a function of frequency. EM solvers (EMX, HFSS, Q3D, ADS Momentum, ...) produce these files when an engineer asks "what does this passive structure look like, electrically, between these N reference planes?"

For each frequency `f_k` the file contains an `N x N` complex matrix `S(f_k)`. Each entry `S_ij` describes how a wave incident at port `j` (with all other ports terminated in `Z_0`, conventionally 50 ohm) is transmitted to or reflected from port `i`.

Touchstone v1 quirks the parser handles:

- The format/encoding (`RI`, `MA`, `DB`) is given on an option line: `# GHZ S MA R 50`.
- For **N=2** files the data is laid out as `S11 S21 S12 S22` (column-major-ish); for `N > 2` it is row-major. The parser transposes the 2-port case.
- Port count is content-sniffed: tokenize, find the smallest `N` such that token count divides into `(1 + 2*N*N)`-sized records and the first column is strictly increasing. This is robust to renamed extensions.

---

## 2. S -> Y conversion

Y-parameters (admittance matrix) are preferred for our purposes because:

- **Parallel networks add**: lumped terminations to ground are added directly into `Y`'s diagonal.
- **Open ports (I=0) are eliminated cleanly** by the Schur complement.
- **Shorted ports (V_i = V_j)** become a row/column merge.

The conversion at each frequency is:

```
Y = y0 * (I - S) * inv(I + S),    y0 = 1 / Z_0
```

The implementation avoids the explicit inverse via `np.linalg.solve(A.T, B.T).T`, falling back to the pseudo-inverse if the linear solve fails.

---

## 3. Schur complement — the physical picture

Partition the ports into **kept** (`k`) and **open** (`o`, where `I_o = 0`):

```
[ I_k ]   [ Y_kk  Y_ko ] [ V_k ]
[     ] = [            ] [     ]
[ I_o ]   [ Y_ok  Y_oo ] [ V_o ]
```

Setting `I_o = 0` and solving the bottom row for `V_o = -inv(Y_oo) * Y_ok * V_k`, then substituting back into the top row gives the **reduced N_k x N_k admittance** seen at the kept ports:

```
Y_red = Y_kk - Y_ko * inv(Y_oo) * Y_ok
```

Physically, Schur reduction "absorbs" floating ports into the network. Their voltages are still defined by the network equations — they just carry no external current.

```
   kept ports                      kept ports
       o                              o
       |                              |
   +---+---+---o open                 |
   |       |   I=0       =====>   +---+---+
   |  Y    +---o open                 |
   |       |   I=0                +---+---+
   +---+---+                          |
       |                              |
       o                              o
       k                              k
   (full N-port)                  (Y_red, N_k-port)
```

A **ground port** (`V = 0`) is even simpler: just delete its row and column.

---

## 4. Mode 1 — Port(s) to ground (driving-point impedance)

Excite the signal port(s) with current; tie ground ports to `V = 0`; leave everything else open.

```
        Signal Port(s)
            |
            v
    +-------+-------+
    |   Package     |
    |   Network     |
    |   (Y-matrix)  |
    +-------+-------+
            |
           GND
```

Steps:

1. Delete ground rows/columns.
2. Schur-eliminate all unspecified ports (`I = 0`).
3. The remaining signal ports are shorted together (carry a common voltage). The driving-point impedance is

```
Z = 1 / (1^T * Y_red * 1)
```

where `1` is a column of ones with length equal to the number of signal ports.

Use case: bond-wire + trace + via inductance from a pad to the ground plane.

---

## 5. Mode 2 — Port-to-Port (between two groups)

Measure impedance between port group A and port group B (e.g., the P and N sides of a differential pair).

```
    Port A group          Port B group
        |                     |
        v                     v
    +---+---------------------+---+
    |       Package Network       |
    |         (Y-matrix)          |
    +---+---------------------+---+
        |                     |
       GND                   GND
```

Steps:

1. Delete ground rows/columns.
2. Schur-eliminate open ports.
3. Collapse to a 2x2 by summing within each group:

```
Y2 = [ sum(Y_AA)  sum(Y_AB) ]
     [ sum(Y_BA)  sum(Y_BB) ]
```

4. Invert to `Z2 = inv(Y2)`.
5. The two-terminal impedance between A and B is:

```
Z_AB = Z_11 + Z_22 - Z_12 - Z_21
```

Use case: differential loop inductance between two signal traces; differential capacitance with a far-end open.

This formula is not a special case bolted on beside the coupling model — it *is* the coupling
model with exactly one measurement port whose red probe is group A and whose black probe is
group B. See section 8.6.

---

## 6. Mode 3 — Port-to-Port with shorted pairs

Some port pairs are explicitly shorted before the reduction. The classic application is **modeling decoupling-capacitor mounting**, where physical solder joints tie pad pairs together.

The constraint `V_i = V_j` is implemented by **merging row i and row j** (sum) and **merging column i and column j** (sum):

```python
# Merge port j into port i (0-indexed)
Y[:, i] += Y[:, j]
Y[i, :] += Y[j, :]
Y = np.delete(np.delete(Y, j, axis=0), j, axis=1)
```

The merged port carries the sum of the two original currents at a single common voltage — exactly Kirchhoff for two nodes tied together. The implementation uses Union-Find to handle chains of shorts (e.g., `1-2, 2-3` => single merged node).

After merging, proceed with the normal Mode 2 algorithm.

```
                   o A1
                   |
   +---------------+----+
   |                    |
   |     Y matrix       +---o pad_top  --+
   |                    |                | shorted
   |                    +---o pad_bot  --+
   |                    |
   +---------------+----+
                   |
                   o B1
```

---

## 7. Mode 4 (retired) — VDD ports

In **AC small-signal** analysis a stiff supply is an AC ground:

```
V_dd = V_DC + v_ac     and     v_ac(VDD) = 0
```

So `vdd` and `ground` produce identical reductions — a VDD ball and a GND ball impose the
same boundary condition, `V = 0`. The old Mode 4 therefore computed *exactly* what Mode 2
computes when both sets are listed as ground ports: there was never a numerical difference,
only a label. Mode 4 is retired for that reason. Supply pins go into the GND field, a saved
mode-4 trace is migrated to mode 2 with its VDD ports folded into GND, and `--vdd` on the CLI
is a deprecated alias that unions into `--gnd`.

The `Vdd` termination class survives in the core (and `vdd` in the Mode 5 DSL) so that intent
stays documentable in a spec; it is evaluated identically to `Ground`. Mode codes are never
renumbered, so 4 stays reserved.

If you need to model a non-ideal supply, use Mode 5 with `lumped_to_gnd(...)` to attach a
finite supply impedance.

---

## 8. Mode 6 — measurement ports as signed probe pairs

Sections 4-6 each answer "what impedance do I see at *one* terminal?". Mode 6 generalises the
question: define **G** terminals at once and get the whole `G x G` impedance matrix, whose
off-diagonal is the coupling between them.

### 8.1 The probe model

A measurement is a pair of multimeter probes. The **red** probe touches the ports on the plus
side, the **black** probe the ports on the minus side:

```
     RED   1 o---+---------------+---o 3   RED
                 |    Network    |
   BLACK   2 o---+  (Y-matrix)   +---o 4   BLACK

           "tank = 1 / 2"        "vco2 = 3 / 4"
```

Formally a measurement port is a triple `(name, plus_set, minus_set)`. Three rules are the
whole model:

1. Ports on the **same** side are tied together — a probe clip is one piece of metal touching
   several pads. This is the same "multiple ports in Port A are shorted" rule the older modes
   already had.
2. An **empty minus side** means the port is referenced to the Touchstone ground node.
3. There are **no weights**. A port is on the plus side, on the minus side, or not in this
   measurement port at all. Section 8.3 explains why.

### 8.2 The contraction: from `Y_red` to the port impedance matrix

Run the existing pipeline of section 9 unchanged — lumped stamps, short merges, ground
row/column deletion, Schur elimination of the open ports. What survives is a reduced
admittance over the probe-carrying ports only:

```
I_p = Y_red * V_p
```

Every port on one side shares a node voltage, so with the **membership matrix** `A`
(`A[p, s] = 1` when port `p` belongs to side `s`, else 0):

```
V = A * u            (all members of a side sit at the side's node voltage u_s)
J = A^T * I          (KCL at the clip: the node current is the sum of member currents)
```

Substituting gives the node admittance

```
Y_node = A^T * Y_red * A          i.e.  Y_node[s,t] = sum of the Y_red block (side s, side t)
```

which is exactly the `.sum()` over a sub-block that the implementation performs.

Now attach the probes. A two-terminal measurement injects `i_g` into the plus node and pulls
the *same* current out of the minus node — that is what makes it a two-terminal measurement.
With the **incidence matrix** `W` (`+1` on port `g`'s plus node, `-1` on its minus node):

```
J = W * i                                 (injected node currents)
v_g = u_plus(g) - u_minus(g)  =>  v = W^T * u    (measured probe voltages)
```

so

```
v = W^T * Y_node^+ * W * i        =>       Z = W^T * pinv(Y_node) * W
```

`Z` is a `G x G` complex matrix per frequency, and that matrix is the first-class output of
Mode 6.

### 8.3 Why signed sides and not fractional weights

Nothing in the algebra above *forces* `W` to hold only `0`, `+1` and `-1`. A weighted `W`
would still produce a matrix. It is deliberately not offered, for three reasons:

1. **It is not a probe.** `W[s,g] = w` means the node receives `w * i_g` while the port
   terminal carries `i_g`, and (via `W^T`) that the measured voltage is `w` times the node
   voltage. That component is an ideal transformer of turns ratio `w` — a part that does not
   exist in the Touchstone file and that the user did not ask for. Membership covers every
   probing you can actually perform with two clips.
2. **The normalisation is ambiguous.** `W` appears twice in `W^T Z_node W`, so the answer
   scales as `w^2`. Should `Z` come back scaled by `w^2` (the honest transformer-loaded
   impedance), by `w` (a hybrid), or by 1 (a "normalised" weighting)? All three are
   defensible, they disagree, and no convention settles it. With `+1 / -1` there is nothing
   to normalise.
3. **It silently pins the common mode.** Column `g` of a signed `W` sums to zero whenever the
   port has a minus side, so `1^T * J = 0`: no *net* current enters the structure and the
   excitation is purely differential. Give the sides unequal weights and the column no longer
   sums to zero — net current now has to return through the reference node, so the "floating"
   measurement quietly becomes partly ground-referenced and the answer depends on a ground
   the user never specified.

### 8.4 Why `pinv`, not `inv`

Take a structure with no ground port and no path to the reference node — two isolated coils
in a 4-port EMX file, the ordinary coupled-inductor case. Adding a constant to every node
voltage changes no branch current, so

```
Y_node * 1 = 0        and, by symmetry,      1^T * Y_node = 0
```

`Y_node` is exactly singular; its null space is the common mode. `inv()` either raises or
returns amplified noise.

The physical question is still well posed, though. Because each column of `W` sums to zero,
the injected current satisfies `1^T * J = 0` — which is precisely the solvability condition
for the singular system `Y_node * u = J` (the right-hand side lies in the range of
`Y_node`, i.e. orthogonal to its left null space). Infinitely many `u` solve it, differing by
a common-mode offset `c * 1`; the pseudo-inverse picks the minimum-norm one. And since we
only ever read differences, `v = W^T u`, and `W^T * 1 = 0`, every one of those solutions gives
the *same* `v`. So

```
Z = W^T * pinv(Y_node) * W
```

is the exact answer for a floating structure, not an approximation. This is why the tool
emits `"Rank-deficient node admittance at freq[...] (pinv used; expected for a fully floating
structure)"` as **information**, capped at three lines, rather than as an error. A clean
floating coupling run trips it at every frequency.

The synthetic fixture `tests/fixtures/coupled_4port_float.s4p` is built for this case: with
`W == A` and `A^T A == 2I`, one can show `W^T pinv(A Y_loop A^T) W == inv(Y_loop) == Z_loop`
analytically, and the tool recovers `L1 = 2 nH`, `L2 = 3 nH`, `M = 800 pH` exactly.

**The one case that does not work:** a *ground-referenced* measurement port (empty minus
side) has a `W` column summing to `+1`, so it demands a return path through the reference
node. If the network has no admittance to that node, the system is inconsistent and `pinv`
returns a least-squares fit to an impossible question — a finite, plausible-looking number
that is not a measurement of anything (exactly `Z_series/4` for a floating pair probed
single-ended; a flat `0 Ω` for a floating series element).

The tool detects this from the same SVD it already computes for `pinv`. A probe column
`w_g` is valid iff it is orthogonal to `null(Y_node)`, i.e. iff its projection onto the
discarded singular directions is negligible:

```
alpha_g = || U[:, r:]^H w_g || / || w_g ||      r = numerical rank at PINV_RCOND
```

`alpha_g > sqrt(PINV_RCOND) = 1e-6` means the discarded direction would have contributed
more to `Z` than the truncation `pinv` already commits, so the measurement is undefined:
that port's whole row and column of `Z` become `NaN` and a warning names it. Ports whose
probes *are* in range are untouched — `Z[b][c] = w_b^T Y^+ w_c` only involves those two
columns, so one bad probe cannot contaminate a good one.

A balanced `+/-` probe on a floating structure has `alpha ~ 1e-16` and is never flagged;
the threshold is `sqrt(rcond)` rather than `rcond` precisely so that a *nearly* floating
structure (a real but very weak ground path) stays out of the error path.

A second, weaker check covers the case where the Schur step itself cancels to nothing:
if `|Y_kk - Y_ko Y_oo^-1 Y_ok|` falls below `1e-12` of the magnitude of its own two terms,
everything downstream is roundoff amplified to `~1e16 Ω`. That one is a magnitude heuristic
(healthy fixtures bottom out at `3.8e-10` against `7e-16` for the degenerate case), so it
only warns — it never converts a result to `NaN` on its own.

### 8.5 The open-circuit convention — and why that *is* the definition of M

Setting `i_b = 1` and `i_a = 0` for every other `a` is what the `Z` matrix means: only one
measurement port is driven and **every other measurement port carries no current**, i.e. it
is open.

That is exactly the textbook definition of mutual inductance. Drive 1 A into structure `b`
with nothing loading structure `a`, and read the open-circuit voltage induced at `a`:

```
Z_ab = v_a / i_b  |  (all other i = 0)
```

Two consequences worth stating out loud:

- This is the right primitive to extract. `M` is a property of the geometry; the loading your
  real circuit applies belongs in the circuit simulator, which already models it. Extract
  open-circuit `M`, then let the simulator load it.
- It is **not** the same number as a short-circuit transfer measurement. If you short the
  victim and measure the current that flows, you get a load-dependent quantity, not `M`.
  Do not compare the two.

### 8.6 The legacy A<->B formula is exactly one signed measurement port

Set `G = 1` with plus set `A` and minus set `B`. Then there are two nodes, and

```
W = [ +1 ]
    [ -1 ]
```

so

```
Z = W^T * Z_node * W
  = Z_node[0,0] + Z_node[1,1] - Z_node[0,1] - Z_node[1,0]
```

with `Z_node = inv(Y_node)` and `Y_node` the 2x2 block-sum from section 8.2. That is
**character-for-character** the Mode 2 formula of section 5. Likewise Mode 1 is `G = 1` with
no minus side: one node, `W = [1]`, and

```
Z = 1 / Y_node[0,0] = 1 / (1^T * Y_red * 1)
```

which is the section-4 expression.

So the coupling model does not *approximate* the old behaviour, it **contains** it. This is
why the legacy group name `"B"` is defined as an alias for "the minus side of group `A`":
`Signal("B", +1)` normalises to `Signal("A", -1)` at resolve time, which turns every
pre-existing mode, saved session and test into an ordinary single-measurement-port case. `A`
and `B` are reserved names for that reason — a new measurement port may not take them.

One subtlety about the *implementation*: mathematical equality is not the same as bit-for-bit
equality in floating point. `ones @ M @ ones` and a stacked `gemm` sum in different orders and
differ in the last ulp. Because "existing modes stay bit-identical" was an acceptance
criterion, the `G == 1` branches keep the historical expressions verbatim rather than routing
through the general `pinv` path, and `tests/test_golden_regression.py` asserts exact equality
against a recorded reference.

### 8.7 Reading `Z_ab`: M, C_c and k

With `omega = 2*pi*f`, from one off-diagonal entry:

```
M    = Im(Z_ab) / omega              mutual inductance, henries
C_c  = -1 / (omega * Im(Z_ab))       coupling capacitance, farads
k    = M / sqrt(L_a * L_b)           coupling factor, dimensionless
```

`M` and `C_c` are two readings of the **same** `Im(Z_ab)`, exactly as `L` and `C` are two
readings of the same `Im(Z)` on the diagonal (section 10). The sign decides which one is
physical:

```
Im(Z_ab) > 0   ->   magnetic coupling dominates.  Read M.   (C_c comes out negative)
Im(Z_ab) < 0   ->   electric coupling dominates.  Read C_c. (M comes out negative)
```

Both are always computed and always shown; the tool does not hide one. Electric-field
coupling dominating is normal for closely spaced traces with no shared magnetic loop, and for
any structure above its self-resonance.

The signs are physical and are never `abs()`-ed, because they carry information:

- They encode probe / winding orientation. Swapping the `+` and `-` ports of one measurement
  port flips `M`, `k` and both `M/L` ratios; magnitudes do not change.
- They decide whether two coupling paths add or cancel. Aggressors at `M = +20 pH` and
  `M = -20 pH` into the same victim cancel; reported as two `+20 pH` paths they would look
  twice as bad as reality.

`k` is `NaN` where it has no meaning — when `L_a <= 0` or `L_b <= 0`, i.e. one of the ports
is past its own SRF and is capacitive at that frequency — and a note says so. `abs(k) > 1` is
flagged, not clamped: a passive structure cannot do that, so the input S-parameters are
suspect (bad de-embedding, non-passive EM data, wrong port map).

### 8.8 M/L — the coupling (Norton injection) ratio, and why frequency cancels

`M` in henries is the number to hand a circuit simulator, but it is a poor budgeting number:
it scales with how big *both* structures are. The quantity that maps directly onto an
injection or spur budget is `M / L_victim`. The derivation is three lines.

1. **Faraday.** Current `I_agg` in the aggressor induces an EMF in series with the victim's
   own tank branch:

   ```
   V_emf = j*omega*M*I_agg
   ```

2. **Thevenin -> Norton.** That source sits behind the victim's own inductance, whose branch
   impedance near the tank frequency is `j*omega*L_a`. Convert the voltage source into the
   equivalent current source across the same branch:

   ```
   I_inj = V_emf / (j*omega*L_a) = (j*omega*M*I_agg) / (j*omega*L_a)
   ```

3. **The `j*omega` cancels.**

   ```
   I_inj = (M / L_a) * I_agg
   ```

So one dimensionless scalar says what fraction of the aggressor's current is injected into
the victim's tank, **at every frequency**. In dB it drops straight onto a dBc budget:

```
20 * log10( abs(M / L_a) )
```

Worked from the real fixture output (`L_c1 = 2 nH`, `L_c2 = 3 nH`, `M = 800 pH`):

```
M / L_c1 = 800e-12 / 2e-9 = 0.400   ->  20*log10(0.400)  = -7.96 dB
M / L_c2 = 800e-12 / 3e-9 = 0.267   ->  20*log10(0.267)  = -11.48 dB
```

Note the two ratios differ: **divide by the `L` of the structure being disturbed**, not the
aggressor's.

This is a first-order Norton equivalent at the victim's tank branch. It is a *budget* number,
not a spur prediction — the actual spur also depends on the tank Q, the aggressor amplitude
and the nonlinearity. Its value is that it lets you rank and screen layouts from EM data
alone, and run the expensive nonlinear loop simulation once, on the winner.

**It is not the exact current-transfer ratio.** Step 2 above approximates the victim's branch
impedance as `j*omega*L_a`, dropping `R_a`. The current a *shorted* port `a` actually draws
when port `b` is driven is

```
I_a / I_b = -Z_ab / Z_aa = -j*omega*M / (R_a + j*omega*L_a)
```

so `M/L_a == abs(Z_ab/Z_aa)` only where `omega*L_a >> R_a`. Around and below the
`R = omega*L` corner the two diverge by orders of magnitude. Measured on a ground-referenced
pair with `L1 = 2 nH`, `R1 = 1.5 Ω`, `M = 0.9 nH`, the tool reports a flat `M/L_a = 0.450000`
at every frequency (correct, as a coupling ratio), while `abs(Z_ab/Z_aa)` is:

```
10 MHz    0.037568      (M/L_a overstates by 1098%)
100 MHz   0.288983      (by 55.7%)
1 GHz     0.446828      (by 0.7%)
10 GHz    0.449968      (converged)
```

At the tank frequency — where a pulling / spur budget lives — they agree, which is why the
frequency-independent ratio is the useful figure of merit. Do not read it as a measured
current ratio at low frequency.

### 8.9 k versus M/L — which to use when

The two are related by the size ratio of the structures:

```
M / L_a = k * sqrt(L_b / L_a)
```

Check against the fixture: `0.3266 * sqrt(3n/2n) = 0.3266 * 1.2247 = 0.400`. They agree only
when `L_a == L_b`.

| Use          | Because                                                                        |
|--------------|--------------------------------------------------------------------------------|
| `k`          | Symmetric, dimensionless, and divides out how big each structure is — the honest layout-vs-layout comparison, and the passivity sanity check (`abs(k) <= 1`). |
| `M / L_victim` | Asymmetric on purpose: it is the injection ratio the victim actually experiences, so it is the number that meets a dBc budget. |
| `M`          | The absolute henries to put in a simulator netlist.                             |

Rough on-chip scale for `k`: `0.001-0.05` is two inductors that are *not* meant to couple
(isolation / pulling territory); `0.05-0.3` is close neighbours and usually a layout problem;
`0.3-0.5` is loosely coupled deliberately spaced coils; `0.5-0.9` is a deliberate on-chip
transformer.

### 8.10 Reciprocity error — a self-check, not a result

A passive, reciprocal network must satisfy `Z_ab = Z_ba`. The tool reports

```
reciprocity error = max|Z_ab - Z_ba| / max|Z_ab|      over the off-diagonal
```

as a health check on the whole chain: EM solve -> Touchstone -> parse -> reduce -> contract.
It is `0.0` by definition when there is only one measurement port.

over the **finite** off-diagonal entries — an undefined measurement port (section 8.4) NaNs
its whole row and column, and letting that poison the metric would report `nan` for a matrix
whose other pairs are perfectly reciprocal.

```
~1e-16 to 1e-13   healthy; floating-point noise
1e-9  to 1e-6     still normal for a real EM solve; S12 and S21 rarely agree to the
                  last bit, and a de-embedded file rarely does better than 1e-9
above 1e-3        the alarm threshold: non-reciprocal or non-passive EM data, an
                  interpolated/extrapolated file, a truncated Touchstone, or a port
                  setup that is unphysical (see the end of section 8.4)
```

`1e-3` is `pkg_rlc_core.RECIPROCITY_WARN`, imported by both the GUI results pane and the
`--cli` report so the same file cannot get two different verdicts.

Caveat: the normalisation is a single global `max abs(Z_off)`, so with `G >= 3` a strongly
coupled pair can mask a badly non-reciprocal weak one. Read the `Z` matrix itself if a
specific weak pair matters.

---

## 9. Unified port-termination abstraction (master model)

The named modes are convenience presets of a single underlying model. Every port carries one of these terminations:

| Termination                  | Physical meaning                              | Effect on Y at each frequency                                                       |
|------------------------------|-----------------------------------------------|-------------------------------------------------------------------------------------|
| `open`                       | I=0, port floating                            | Schur-eliminate                                                                     |
| `ground`                     | V=0, tied to reference                        | Delete row / column                                                                 |
| `vdd`                        | V_ac = 0, AC-grounded ideal supply            | Same as `ground` (alias)                                                            |
| `signal(group, sign)`        | Port carries a probe: `sign=+1` red side, `sign=-1` black side of measurement port `group` | Kept, and contracted onto its probe node (section 8.2)     |
| `short_to(j)`                | V_i = V_j, two ports tied together            | Merge rows i,j and cols i,j                                                         |
| `lumped_to_gnd(Y_term(f))`   | Port terminated through R/L/C network to GND  | `Y[i,i] += Y_term(f)`, then mark `open`                                             |
| `lumped_between(j, Y_term(f))`| Two ports linked by R/L/C network            | `Y[i,i] += y; Y[j,j] += y; Y[i,j] -= y; Y[j,i] -= y`, then mark both `open`         |

`group` is an arbitrary string; ports sharing a `(group, sign)` are tied together, and there
are no fractional weights (section 8.3). The historical group `"B"` is an **alias** for the
minus side of group `"A"`: `Signal("B", +1)` normalises to `Signal("A", -1)`. That alias is
what makes every pre-existing mode a single-measurement-port case (section 8.6), and it is
why `A` and `B` are reserved names.

`Y_term(f)` is any frequency-dependent admittance:

```
resistor R    -> 1 / R
inductor L    -> 1 / (j*omega*L)
capacitor C   -> j*omega*C
series RLC    -> 1 / (R + j*omega*L + 1/(j*omega*C))
parallel      -> sum of admittances
```

Evaluation order at each frequency:

1. Apply `lumped_*` terminations: modify `Y` per the table; those ports then behave as `open`.
2. Apply `short_to(j)` merges.
3. Delete `ground` and `vdd` rows/columns.
4. Schur-eliminate all `open` ports.
5. Contract the surviving `signal` ports onto the probe nodes and invert (section 8.2).

Steps 1-4 are identical for every mode; only step 5 knows about measurement ports. `compute_z_matrix`
returns the whole `G x G` matrix, and `compute_z` is a thin wrapper that hands back
`Zmat[:, 0, 0]` — the self impedance of the first measurement port — which is what every
single-terminal mode wants.

The named modes lower into this dispatch:

| Mode | Built-in builder                   | Termination preset                                                              |
|------|------------------------------------|---------------------------------------------------------------------------------|
| 1    | `build_terminations_mode1`         | signal -> `A+`; gnd -> ground                                                   |
| 2    | `build_terminations_mode2`         | signal_a -> `A+`; signal_b -> `B` (= `A-`); gnd -> ground                       |
| 3    | `build_terminations_mode3`         | as Mode 2, plus `ShortPair` couplings                                           |
| 4    | `build_terminations_mode4` *(retired in the UI)* | as Mode 2, plus `vdd -> Vdd` (alias of ground)                    |
| 5    | (user-supplied `TerminationSet`)   | arbitrary mix of the above; the DSL writes `signal <name>` plus an optional `+` / `-` token |
| 6    | `build_terminations_coupling`      | one `Signal(name, +-1)` per probed port, from `(name, plus, minus)` triples      |

All of these take **1-based** port numbers and emit 0-based ones: the builders are the
GUI/CLI boundary, and nothing deeper converts.

---

## 10. RLC extraction at a single frequency

At a chosen frequency `f` with `omega = 2*pi*f` and `Z(f) = R + j*X`:

```
R(f) = Re(Z(f))
L(f) = Im(Z(f)) / omega          (signed; <0 when capacitive at f)
C(f) = -1 / (omega * Im(Z(f)))   (signed; <0 when inductive at f)
Q(f) = Im(Z(f)) / Re(Z(f))       (signed; matches Cadence)
```

Values are reported with their physical sign rather than masked outside a "valid" region — this matches Cadence and lets the curve through SRF stay continuous on plots. Past SRF (`Im(Z) < 0` for an inductor) `L` and `Q` go negative while `C` becomes positive (the parasitic capacitance dominates). Q here is the reactance-to-resistance ratio of a series-equivalent at `f`; its sign reflects whether the network is net-inductive (`Q > 0`) or net-capacitive (`Q < 0`) at that frequency.

The same formulas apply to every diagonal entry `Z[g][g]` of a Mode 6 matrix, and their
off-diagonal counterparts `M` / `C_c` (section 8.7) are literally the same two expressions
applied to `Z[a][b]`.

---

## 11. Broadband fitting

Single-frequency RLC is fragile: skin effect makes `R(f)` move, parasitic ESL makes capacitors look inductive above SRF, and DCO inductors have very different `R_dc` and `R_ac` contributions. Fitting a physically motivated model over a band gives much more usable numbers.

### Inductor model

For inductors, bond wires, and traces with a shorted far end:

```
Z(f) = R_dc + R_ac * sqrt(f) + j*2*pi*f*L
```

The `R_ac * sqrt(f)` term captures skin-effect resistance growth. The model is **linear** in `[R_dc, R_ac, L]` against the feature columns `[1, sqrt(f), j*omega]`, so the fit is a single complex-stacked least-squares (real + imaginary rows). The implementation column-scales the design matrix to keep its condition number reasonable.

Reports: `L`, `R_dc`, `R_ac`, `Q@f_center` (geometric mean of band edges), `SRF` (`NaN` for the pure-inductor model — by construction it never resonates), and the fit RMSE.

### Capacitor model

For capacitors, decap, and traces with an open far end:

```
Z(f) = R_esr + j*2*pi*f*L_esl + 1 / (j*2*pi*f*C)
```

Linear in `[R_esr, L_esl, 1/C]` against `[1, j*omega, 1/(j*omega)]`. Reports: `C`, `R_esr`, `L_esl`, `SRF = 1 / (2*pi*sqrt(L_esl*C))` (only if it falls inside the band; otherwise `NaN`), and RMSE.

### Auto model selection

Inspect `Im(Z(f))` over the band:

- More than ~85% positive -> **inductor** model.
- More than ~85% negative -> **capacitor** model.
- Otherwise fit both and return the one with lower RMSE.

The fitted curve is overlaid on the Re/Im/|Z| subplots in the band region.

---

## 12. Use case examples

The same tool, the same modes — only the port assignment changes.

### A. DCO / spiral inductor (2-port: P, N)

```
Mode 2 (A<->B):  A = P, B = N, GND = (none if no GND port)
Fit:             Inductor model over [f_min, f_max]
Reports:         L, R_dc, R_ac, Q@f_center, SRF
```

### B. Differential trace — loop inductance (5-port: inp, inn, outp, outn, gnd)

```
Mode 3 (A<->B + Short Pairs):
   A = inp,  B = inn,  Short Pairs = "outp-outn",  GND = gnd_port
Why:    Shorting the far end forces the signal to return through the trace,
        exposing the differential loop inductance.
Fit:    Inductor model
Reports: L_loop (TOTAL, in nH — not per unit length), R_dc, Q
```

### C. Differential trace — differential capacitance

```
Mode 2 (A<->B):
   A = inp,  B = inn,  GND = gnd_port
   (outp, outn left default open -> Schur-eliminated)
Why:    Open far end isolates the inter-trace capacitance.
Fit:    Capacitor model
Reports: C_diff (total)
```

### D. Decap with two mounting pads shorted

```
Mode 3:  A = pad1_top, B = gnd_top, Short Pairs = "pad1_bot-gnd_bot"
Reports: ESR, ESL, C as seen at the top mounting plane
```

### E. Custom — signal through a 50 ohm termination (Mode 5)

```
Measurement ports:            Connections:
  Name  +ports  -ports          Type      Port  To  R    L  C
  m1    1                       rlc_gnd   2         50
                                ground    3-N
Why:    Measure driving-point impedance with a realistic source / load termination.
```

The two tables serialise to exactly this DSL text, and that text is what the
parser sees — "Edit as text…" shows it verbatim:

```
1 signal m1 +
2 lumped_to_gnd R=50
3-N ground
```

Rows and text are the same thing, not two ways of specifying the same thing:
`build_terminations_rows` *is* `parse_custom_termination_text(rows_to_dsl_text(...))`.
Measurement ports are emitted before connections, which is why a later `ground`
row wins over a probe on the same port.

### F. Two floating coils — M and k (Mode 6, 4-port)

```
Mode 6:  "c1 = 1 / 2",  "c2 = 3 / 4",  GND = (blank; the coils float)
Why:     Each coil gets its own probe pair, so the off-diagonal of the 2x2 Z
         matrix is the open-circuit mutual impedance between them.
Reports: L_c1, L_c2 on the diagonal; M, k, C_c, M/L_c1, M/L_c2 off it.
Expect:  a "Rank-deficient node admittance (pinv used)" note at every
         frequency — see section 8.4, it is correct and informational.
```

Real numbers from `tests/fixtures/coupled_4port_float.s4p` at 5 GHz
(`L1 = 2 nH`, `L2 = 3 nH`, `M = 800 pH` by construction):

```
Z_ab   = 3.4e-15 + j25.13 Ω     (Im > 0 -> inductive, read M)
M      = 800 pH
k      = 0.3266
M/L_c1 = 0.400  (-7.96 dB)      <- the budget number if c1 is the victim
M/L_c2 = 0.267  (-11.48 dB)     <- the budget number if c2 is the victim
reciprocity error = 7.18e-16    (healthy)
```

### G. Aggressor -> victim on a package bus (Mode 6, ground-referenced)

```
Mode 6:  "vic = 1",  "agg = 2",  GND = "3:1:16"
Why:     Both probes are ground-referenced (empty "-" side), which is legal
         precisely because the GND balls give the return current a path.
         Omit the GND ports and the answer is meaningless — the tool reports
         "no return path" and NaN for those ports rather than a number.
Reports: M and C_c between the two nets; read C_c where Im(Z_ab) < 0, which is
         common for closely spaced traces with no shared magnetic loop.
```

### What this tool does NOT do

All extracted R/L/C/Q values are **totals** for the network as seen between the chosen signal ports. They are never per-unit-length. If you want per-unit-length values, divide by your known length yourself. The tool does not perform distributed transmission-line (RLGC-per-length) extraction; that requires a different procedure (multi-section ABCD or `gamma` / `Z_0` extraction).

`M` is likewise a total mutual inductance between the two chosen terminal pairs, not a
per-length or per-turn quantity, and it is the **open-circuit** one (section 8.5) — not a
loaded transfer measurement. `M/L` is a first-order Norton equivalent for budgeting, not a
spur prediction and not the exact current-transfer ratio (section 8.8); the tool has no model
of your tank Q, drive amplitude or nonlinearity.
