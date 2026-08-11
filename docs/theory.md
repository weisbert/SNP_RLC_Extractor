# PKG RLC Extractor — Theory

This document explains the math and the modeling choices behind the tool. It assumes familiarity with S- and Y-parameters at the level of any standard RF text.

**Section 8** (the `+/-` probe model, and the M / k / M-over-L derivations) assumes no prior
coupling theory — it builds the whole thing from Faraday's law and one matrix identity, and
explains why each modelling choice was made rather than only what it is. If coupling is what
brought you here, sections 2, 3 and 8 are enough.

**Section 13** (port attribution) is the follow-on: sections 4-8 compute a number from a set
of termination assumptions, and section 13 takes that number apart into "the bare EM
coupling" plus one exact term per assumption. Read section 8 first; §13 leans on §8.5 (why
open-circuit is the right primitive) and §8.8 (M/L versus the exact current-transfer ratio)
throughout. **§13.14** is the same algebra pointed the other way — before any assumption has
been made, *which ports would matter if you made one?* — and is where a new file starts.

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

---

## 13. Port attribution — decomposing `Z_ab` into its causes

Everything above computes **a** number from **a** set of termination assumptions. This
section is about taking that number apart: of the `Z_ab` that came out, how much is the metal
and how much is the grounding you assumed? And what would it have been under a different
assumption?

The implementation is `pkg_rlc_attrib.py`, which imports `pkg_rlc_core` and nothing else from
the repo — the same acyclic relationship `pkg_rlc_plot` has — driven from the CLI by
`--attribute VICTIM,AGGRESSOR` and its flag group (`--mode coupling` only; there is no GUI
surface). The engineering rationale, the measurements behind every design rule, and what was
deliberately left out are in `docs/design_port_attribution.md`. This section is the
mathematics.

### 13.1 Why the question exists

The same two coils, out of the same EM solve, extracted twice:

```
|M| = 1.71 pH        |M| = 3.44 pH        6.07 dB apart
```

Both runs are correct. Broken down one factor at a time, the frequency marker moved by
0.6 dB and the **grounding assumption** by 6.1 dB. (They do not sum to 6.07 dB — the factors
are not additive, which is itself the effect §13.7 exists for.)

The bottleneck is not EM accuracy. It is that `compute_z_matrix` returns the **open-circuit**
matrix, every port that is neither a probe nor explicitly grounded is left **open**, and
until now that convention was stated in §8.5 and nowhere on screen. Two different, equally
defensible spellings of a package's ground balls produce two answers 6 dB apart with nothing
in the output that names the difference.

### 13.2 Notation

`N` ports in the file, `Y(f)` the `N x N` admittance from §2.

`A` merges each measurement-port **side** — every port sharing one `(group, sign)` — into a
single node, mirroring exactly what §8.2 does when it sums the `Y_red` block. Then

```
Ybase = A^T Y A          Zbase = Ybase^-1
```

**The baseline is: probe sides merged, every other port OPEN.** Nothing else is in it — no
ground, no short, no lumped element. `w_g` is the injection vector of measurement port `g`:
`+1` on its plus node, `-1` on its minus node, exactly the `W` of §8.2.

Every **non-probe declaration** in the `TerminationSet` is then one two-terminal element
stamped on top of that baseline:

| Declaration | `u` | Element impedance |
|-------------|-----|-------------------|
| `ground` / `vdd` | `e_p` | `0` |
| `lumped_to_gnd` | `e_p` | `1 / y_series_rlc(ω)` |
| `short_to` | `e_p - e_q` | `0` |
| `lumped_between` | `e_p - e_q` | `1 / y_series_rlc(ω)` |

`U` is the `(n_nodes x m)` matrix of those `u` vectors as columns and `Zt` the `(m x m)`
element **impedance** matrix. Writing the elements as impedances rather than admittances is
the whole trick: an ideal ground is `Zt[e,e] = 0`, so **no infinity ever enters the
arithmetic**. `Zt` is also allowed to be dense — see §13.8.

Note the name: `Zbase`, not `Z0`. `Z0` already means the reference impedance everywhere else
in this tool and the collision is a real source of bugs.

### 13.3 The decomposition

Drive 1 A into aggressor `b` and read the open-circuit voltage at victim `a`. With no
elements at all that is the baseline, by definition:

```
Z_ab^base = w_a^T Zbase w_b
```

Now add the elements. Each carries an unknown current `I_e` out of the structure. By
superposition the node voltages are the baseline response to the drive plus the baseline
response to every element current:

```
V = Zbase (w_b - U I)
```

Element `e`'s constitutive law is `u_e^T V = Zt[e, :] I` — the voltage across it equals its
own impedance times the currents. (A *dense* row is what lets an element's voltage depend on
the *other* elements' currents, which is precisely the shared-return case of §13.8.)
Stacking all `m` of them:

```
U^T Zbase w_b  -  U^T Zbase U I  =  Zt I
      p_b      -        G I      =  Zt I
```

which is one small dense solve:

```
G   = U^T Zbase U            H = Zt + G
p_b = U^T Zbase w_b          I = solve(H, p_b)
r_a = U^T Zbase^T w_a        (its own solve — see §13.5)
```

and then

```
Z_ab = w_a^T Zbase w_b  -  r_a · I
     = direct term      -  sum over e of  I_e · r_a[e]
```

**This is exact.** It is superposition: no linearisation, no small-signal expansion about a
nominal, no first-order term dropped. `I_e` is the physical current in element `e`; `r_a[e]`
is the **baseline** transimpedance from element `e` to the victim. The per-element terms
`-I_e · r_a[e]` are an exact additive, signed decomposition of the total.

### 13.4 Why this is a Woodbury identity

Terminating a set of ports is a rank-`m` update to `Ybase`:

```
Yterm = Ybase + U D U^T,        D = Zt^-1
Zterm = inv(Yterm)
      = Zbase - Zbase U (D^-1 + U^T Zbase U)^-1 U^T Zbase
      = Zbase - Zbase U (Zt + G)^-1 U^T Zbase
```

Sandwich that between `w_a^T` and `w_b` and you get §13.3 term for term. Writing it in the
`Zt` form rather than the `D` form is what keeps it finite: `D` is infinite for an ideal
ground, `Zt` is zero, and `H = Zt + G` is well conditioned whenever `G` is. `H` is also the
only matrix inverted on this path, which is why its condition number is what gates the
reconciliation tolerance in §13.6.

Cost: `O(m^3)` to factor `H` plus `O(n_nodes^2 · m)` to build `G`, against `O(n_open^3)` per
chunk for the engine's Schur solve. A package with 60 declared ground balls has `m = 60`,
which is nothing — and the same factorisation is reused for every what-if in §13.7.

### 13.5 Prior art this rederives

None of §13.3-13.4 is new, and naming the prior art is not decoration: each of these
literatures already found the trap that the corresponding rule below guards against, and
"this is diakoptics, and diakoptics has the following known failure mode" is cheaper than
rediscovering it.

- **Kron diakoptics / multiport network connection.** Tear a network at chosen ports, solve
  the pieces, reconnect them through a small dense matrix at the tear. `H = Zt + G` *is*
  Kron's connection matrix, and §13.10's "the split depends on how you spelled the spec" is
  the statement that two spellings are two different **tearings** of one network.
- **The adjoint variable method.** `r_a` is the adjoint (victim-driven) solution and `p_b`
  the direct (aggressor-driven) one. One extra solve buys the sensitivity of one output to
  *every* element — which is exactly what §13.7 exploits. It is also why `r_a` must be its
  own solve: reciprocity would make `r_a = p_a`, but real EM data is only approximately
  reciprocal (§8.10), and the user's own file sits at `3.4e-10`, a thousand times the
  residual this method advertises. Reusing `p_a` would silently spend that error budget.
  For the same reason the transposes are plain `.T` and never `.conj().T` — `Y` is
  complex-**symmetric**, not Hermitian, and the conjugate transpose is simply the wrong
  operator here.
- **PEEC partial elements.** PEEC's canonical warning applies verbatim: partial inductances
  are individually reference-dependent and only collectively physical. Substitute
  "baseline-dependent" and it is §13.10's gauge caveat, unchanged.
- **Norton path decomposition** and **transfer-path analysis (TPA)** from structural
  acoustics / NVH. TPA already knows that the sum of path *contributions* is not the sum of
  path *magnitudes* (§13.6), that paths interact (§13.7), and that a path you did not
  instrument is invisible rather than zero (§13.10).

### 13.6 Reading the output: reconciliation, shares, the return budget

**The authoritative total is always `compute_z_matrix`'s.** The decomposition's own sum is
the *check* on it, never the answer. Two genuinely different routes to the same number — a
Schur elimination plus a `pinv` contraction versus a node-space inverse plus a Woodbury
update — will not agree to the last bit, and the tolerance is **condition-aware** rather than
fixed. A fixed `1e-9` gate would refuse exactly the files this exists for: measured
cross-algorithm agreement is `3e-16` on a trivial 4-port and no better than `~1e-7` on a
153-port package whose condition numbers run `1e7`-`1e9`. When the residual is catastrophic
the tool withholds the **per-element split** and never the total. Measured on the fixture
worked in §13.12: residual `6.4e-13` against a reported floor of `3.6e-9`.

**A share is a projection, not a complex ratio.** Reporting `term / total` for complex
numbers produces a complex "percentage" nobody can read, and reporting `|term| / |total|`
double-counts anything out of phase. The signed inline share is

```
share_inline = Re(term · conj(total)) / |total|^2
```

with the quadrature part reported alongside it. A term at 90 degrees to the total inflates
any magnitude-based cancellation measure while contributing nothing to the total. Where
`|total|` is near zero — pure cancellation, or smaller than the reconciliation residual
itself — the share column is **suppressed outright with a named reason**, because a share of
a number that is not really there means nothing.

**The return budget is always reported**, and it is what stops the decomposition from being
read as something it is not. The EM model's reference plane is **not a port**, so no
declaration can reach it. The report gives the current returning through the model's own
reference against the current returning through declared elements. Measured on
`diff_pair_4port.s4p` with probes on 1 and 2: with **one** ground on port 3 the declared
element carries 99.5 %, but on the representative package case that motivated the rule the
split was **0.05 % declared, 99.95 % inside the model**. When the model dominates the report
says so in words, because a "forward path minus return path" hypothesis is **not falsifiable
this way** and small numbers in the table must not be read as a null result.

### 13.7 Sensitivity: why per-port and pairwise are not enough

The same factorisation answers the other direction exactly: replace one element's `Zt` entry
(or a whole block of them) and re-solve. Sherman-Morrison for one, a bordered Schur or a
rank-`|S|` Woodbury for a set. This is **not** a first-order sensitivity — it is the answer
the network actually has.

What matters is *which* deltas are worth computing:

- **Per element** — one at a time, against a candidate termination (open, ideal, `R = Z0`,
  series `L`, series `R+L`, shunt `C`).
- **Per group** — a whole connection-table row changed at once. The rows already define the
  groups, so this is free, and it is the one that answers the question actually being asked.
- **Non-additivity**, `Δ_joint − Σ Δ_individual`, for groups and for pairs.
- **A cumulative curve** — rank by single-element delta, then evaluate with the top
  `k = 1, 2, 4, 8, 16, …` changed together.
- **Leave-one-out from all-grounded**, which is usually more informative than one-at-a-time
  from all-open.

The reason the last four exist, rather than only the first: **with 60 ground balls every
single-port delta is nearly zero**, because the other 59 already carry the return — and so is
every pairwise second difference. The collective effect is order-60, not order-2, and a
one-at-a-time table would report "nothing matters" about a factor of two. Even at `m = 2`
this bites. Measured on `diff_pair_4port.s4p` at 5 GHz, `agg = 1`, `vic = 2`, grounds on 3
and 4, opening grounds:

```
open port 3 alone            -506 pH
open port 4 alone            -506 pH
                    sum      -1012 pH
open BOTH at once             -759 pH
              non-additivity  +254 pH        a third of the effect, from two elements
```

### 13.8 One element's impedance: a Möbius map, not a loop

`Z_ab` as a function of a single element's impedance `z` is a bilinear (Möbius) function

```
Z_ab(z) = (alpha + beta*z) / (gamma + delta*z)
```

which follows immediately from §13.4: `z` enters `H = Zt + G` in exactly one entry, so the
solve is a ratio of two affine functions of it. Therefore the endpoints `z = 0` (ideal) and
`z → ∞` (open), the whole interval between them, and the extremum over `z ∈ [0, ∞)` are all
**closed form** — a Möbius map takes the real line to a circular arc, so the extremum is
analytic. No sampling loop, and the headline is an interval rather than a curve:
"M lies in [1.71, 3.44] pH over any physical ground inductance."

**The two endpoints are the two numbers worth reading, and they are exact.** `z = 0` is the
termination made ideal and `z → ∞` is the termination not there at all — "ideal ground" and
"open", the two assumptions the disputed number of §13.1 differed by. In the partial-fraction
form below they are `c0 - Σ c_j/λ_j` and `c0` respectively: one sum and one constant, with no
subtraction of large nearly-equal quantities anywhere. Everything else on the curve is context
for those two.

**The map has one pole per swept element, and its position is closed form.** A Möbius map has
exactly one in the extended plane — whether it lands inside the swept range `[0, ∞)` is a
property of the network, not of the algebra — and a group of `|S|` elements tied to one value
has `|S|`. From `Z_ab(z) = (α + βz)/(γ + δz)` it is at `z = -γ/δ`; from the partial fraction,
at `t = -λ_j`. Physically it is the value at which **the added impedance cancels the impedance
the network presents at that element's own terminals** (with every other declared element in
place) — `H = Zt + G` is singular there — i.e. the termination you are hypothesising
*anti-resonates* with the structure. Measured on `diff_pair_4port.s4p` at 5.0005 GHz with
probes on 1 and 2 and grounds on 3 and 4, sweeping the series `L` of `ground port 3`:

| | |
|---|---|
| the network at that ball (`ctx.Gm[0,0]`; on this fixture the *other* declared ground moves it only in the eighth significant figure, so `z_pole = −Gm[0,0]` here to seven) | `−391 µΩ − j15.8745 kΩ`, i.e. **2.005 fF** |
| pole (`−λ`) | `L = 505.25 nH` — and `505.25 nH` series-resonates with `2.005 fF` at **5.0005 GHz**, the frequency being read, to five digits |
| `Im λ` (the loss, which is what keeps the pole off the real axis) | `12.44 fH` — so the peak is finite: `±10.28 mH` |

**Which is why the reported interval must be the pole-free one.** Over the whole half-line the
extremum is that `±10.28 mH` — ten million times the `[503.7 pH, 1.01 nH]` the endpoints span,
and reached only within femtohenries of a 505 nH inductor nobody is putting on a ground ball.
Quoting it as *the* interval is the tool describing its own arithmetic. Away from the pole (a
factor-of-two guard band, measured) the same curve reads `[−2.5 pH, 1.52 nH]`, which is a
budget statement. So: **headline the interval over the pole-free portion, and state the pole
separately, naming its `L` and the value of the element there.** A pole is a real feature of
the structure and hiding it would be a different lie from letting it eat the axis.

**Even pole-free, the curve need not be monotone and the endpoints are not a bound.** A series
`L` resonates with the package's shunt `C`, so `M` leaves the `[ideal, open]` bracket at both
ends and stays outside it well away from the resonance: measured with a factor-of-**ten** guard
band the same curve reads `[447.5 pH, 1.066 nH]` against the `[503.7 pH, 1.01 nH]` bracket. The tool
detects that and says so rather than quoting a bracket that does not hold. On an **unbounded**
sweep an extremum orders of magnitude past the bracket is the near-pole, not a design margin,
and is reported as such.

**Tying a whole group to one value is a degree-`|S|` rational function, and two things about
it are easy to get wrong.** First, do not expand it into polynomial coefficients. The
canonical form is the partial fraction

```
Z_ab(t) = c0 - sum_j  c_j / (lambda_j + t)          poles at t = -lambda_j
```

in which `t → ∞` is exactly `c0` and `t = 0` is one sum. Multiplying it out multiplies `|S|`
eigenvalues together, and when the parameter is an inductance every `lambda_j` is of order
`1e-9`: on a synthetic package sweeping one ground group, the constant term of the expanded
denominator measures `5.98e-273` at 30 balls, `3.70e-309` at 34 and **exactly zero at 36** —
so an endpoint read as `num[-1]/den[-1]` returns `+inf`, then `NaN`, while the interior of
the curve evaluates perfectly and the interval printed beside it looks entirely confident.
§13.7's whole argument is about 60 ground balls.

Second, `|S| ≥ 2` puts several poles on the curve, and they can be *clustered*. Finding the
extremum by rooting the expanded degree-`2|S|` critical polynomial loses them: measured on
`diff_pair_4port.s4p` at 5 GHz, sweeping **both** grounds as one group, the two poles sit at
`t = 5.05000e-7` and `5.05503e-7` — a tenth of a percent apart, both on the positive real
axis — and the reported interval came back `(+7.5e-21, +2.1e-3) H` against a true
`(−5.19, +5.19) H`: the maximum three orders of magnitude too small and the minimum the
wrong sign. The single-element sweep on the same file was exact throughout, which is why the
defect needs `|S| ≥ 2` to show at all — i.e. precisely the "change a whole connection-table
row" case §13.7 exists for. The extremum search therefore **seeds from the poles** (a pole at
`p` contributes a feature of half-width `|Im p|`, so `Re(p) ± c·|Im p|` finds it) and then
polishes each seed with Newton on `Z'` and `Z''`, both in partial-fraction form. Every
candidate is a point the curve genuinely passes through, so the reported interval is always
*achieved*: it can be too narrow, never too wide, which is what makes it safe to keep adding
candidates.

**And this is why `Zt` may be dense.** Real package ground balls share a return plane. `N`
independent `z` in parallel is `z/N`; `N` balls sharing one `z` is `z`, so modelling a ground
field as `N` independent series inductors understates the effective common-mode return
inductance by roughly `(1 + (N-1)·k_ret)`. That factor is why the independent spelling is not a
conservative default: at 20 balls with a realistic `k_ret = 0.2` it is `4.8x`, i.e. **13.6 dB**
of `M`, and it grows with the ball count. Measured on three different networks — 9.60 dB
(four leads at 1 nH each independently, against the same four tied through **one** shared
1 nH), 8.09 dB, and 6.03 dB on `diff_pair_4port.s4p` — every one of them **larger than the
6.07 dB dispute of §13.1**, monotone in `k_ret` with no threshold behaviour. There is therefore
no defensible default and the tool refuses to pick one; it offers `diag(z)` and
`diag(z_self) + z_ret · ones(m, m)` and makes you choose, on the CLI
(`--attribute-ground-model`) and in the Attribution window, in one spelling. `H = Zt + G`
accepts a dense `Zt` with zero change to the mathematics and zero change to the cost.

The dense case is the one configuration in this layer with **no second opinion**: a mutual
impedance *between* two ground leads is not expressible as a `TerminationSet` — the DSL has no
node to hang one on — so `compute_z_matrix` cannot be asked about that network at all. What is
reconciled, whatever model is in force, is the **declared** configuration through the same
machinery (§13.6), which checks the arithmetic the modelled total came out of; the modelled
number itself is this module's alone and every surface that prints it says so.

The same physics is expressible in the Mode 5 table today with no new code: one `short_to`
row tying the ground set together, then **one** `lumped_to_gnd` on any port of it.

### 13.9 Precisely what does and does not decompose

**A quantity decomposes iff it is (a fixed real scalar) × (an R-linear functional of `Z_ab`),
evaluated at ONE configuration.** Multiplying an exact additive decomposition by a constant,
or taking `Re` / `Im` of it term by term, is still exact. Anything with `Z_ab` in a
denominator, inside an absolute value, or inside a logarithm is not.

| | Quantity | Why |
|---|---|---|
| **Yes** | `Z_ab` | the decomposition itself |
| **Yes** | `Re Z_ab`, `Im Z_ab` | `Re` and `Im` are R-linear; the terms' real parts sum to the total's real part |
| **Yes** | `M = Im(Z_ab)/ω` | `1/ω` is a fixed real scalar at one frequency |
| **Yes** | `M/L_a`, `k = M/sqrt(L_a·L_b)` | the divisor is a *fixed* real scalar **of the configuration being evaluated**, so it multiplies through that evaluation |
| **No** | `C_c = -1/(ω·Im Z_ab)` | a **reciprocal**. Superposition adds impedances, not their inverses |
| **No** | `Q = Im(Z)/Re(Z)` | a ratio of two decomposable quantities is not itself decomposable |
| **No** | `\|Z_ab\|` | a norm, not R-linear |
| **No** | anything in dB | a logarithm of a magnitude: neither linear nor signed |

Two consequences worth being explicit about.

`C_c` is a **first-class output of this tool** and is the right reading whenever
`Im(Z_ab) < 0` (§8.7). It therefore still appears — as a **total**, never per term. A
per-term `C_c` would be a column of numbers that do not add up to the number above them. The
API refuses a per-term request for a non-decomposable quantity **by name**, with the reason
and with the linear quantity to ask for instead, because "unsupported quantity" would send
the caller hunting for a typo.

The `M/L_a` and `k` rows carry a caveat inherited from §8.8. "A fixed real scalar evaluated
at **one** configuration" means fixed *within one evaluation* — that is what keeps the terms
additive — and it emphatically does **not** mean frozen at the declared spec while the
network changes underneath it. `L_a` and `L_b` are properties of the network, and every
sensitivity row, every group and every leave-one-out row is a different network.

For a `decompose()` of the spec as declared the divisor is read off `compute_z_matrix`'s
matrix, the same one the results pane and the CSV print, so the number here means the same
thing as the number there rather than a second, slightly different self inductance. For every
**what-if** it is read off the `(G, G)` matrix of the configuration actually being evaluated.
The difference is not academic. Measured on `diff_pair_4port.s4p` at 5 GHz with probes on
ports 1 and 2 and grounds on 3 and 4, opening `ground port 3` takes `L_a` from `+5.026 nH` to
`−505.3 nH`:

| | `M/L_a` after opening ground 3 | `k` after opening ground 3 |
|---|---|---|
| divisor frozen at the declared `L_a` | `+0.100227` | `+0.100227` |
| divisor of the network being asked about | `−0.000997` | `NaN` |

The sign is flipped and the magnitude is a hundred times out, and the `k` column is worse
than wrong: with `L_a < 0` the coupling coefficient is **undefined** by the same rule
`extract_coupling_at_freq` applies (§8.7), and a plausible positive number in its place is
exactly the failure mode this tool's signed-value convention exists to prevent.

The **sweep** (§13.9) cannot resolve this the same way, because a curve has no single
configuration to take a scalar from: `L_a` moves with the swept parameter. `sweep_mobius`
therefore **refuses `M/L_a` and `k` by name**, and points at `M` / `Im Z_ab` for a curve or
at `sensitivity()` for exact `M/L_a` and `k` at named alternatives.

`M/L_a` is also **not** the exact current-transfer ratio: `I_a/I_b = -Z_ab/Z_aa` has `Z_ab`
in a denominator and is therefore in the "No" column above (§8.8 measures them 1098 % apart
at 10 MHz for `L = 2 nH`, `R = 1.5 Ω`). The attribution layer exposes that exact ratio, and a
loaded `-Z_ab/(Z_aa + Z_load)`, as a **total**, so the Norton approximation and the exact
ratio can be compared directly instead of by hand.

### 13.10 What the method is blind to

Prominent, not a footnote. Each of these is a question a user asks within a week, and each
answer is "no".

**It is blind to open ports.** An open port contributes no element and therefore no term. It
is not a small contribution; it is *absent*. So the contribution table is **not a ranking of
ports** — it is a ranking of the **declarations in the spec**. A table headed "contributions
by port" that silently omits the 45 open ports of a package file would be a wrong answer with
a plausible shape. Only the sensitivity side of §13.7 reaches ports the user has not decided
about, and it reaches them by *hypothesising* a termination, not by measuring one.

This is where the reviews surfaced a distinction worth stating carefully. **A port left open
because the SIMULATOR owns it is a different thing from a port left open because nobody
decided, and only the first is safe.** In case one — a die pad that the circuit netlist
drives, a pin whose load lives in the schematic — "open" is the correct and deliberate
primitive, exactly as §8.5 argues for the mutual `Z_ab`: extract the open-circuit quantity,
then let the simulator apply the real loading, because the simulator models it better than
any single termination you could type. In case two — a ground ball nobody listed, a shield
tap left blank — "open" is not a model of anything. It is the absence of a decision, and it
silently became a boundary condition. The two are indistinguishable in the file, in the
`TerminationSet`, and in the attribution table, which is precisely why the Ports & Roles
window flags an open port whose *name* matches a set that was grounded or probed elsewhere.
When you read an attribution table, the open ports it does not mention are the ones to check
first.

**The split depends on how the spec is spelled.** These describe the same network:

```
6:1:14 ground                             ->  9 elements
6 short_to 7:1:14   +   6 ground          ->  8 shorts + 1 ground
```

Same total `Z_ab`, to the reconciliation floor; completely different per-element splits.
Measured on `diff_pair_4port.s4p` at 5 GHz with `agg = 1`, `vic = 2`:

```
   3 ground / 4 ground              3 short_to 4 / 3 ground
     bare EM      251 pH              bare EM      251 pH
     ground 3     252 pH              ground 3     253 pH
     ground 4     506 pH              short 3-4    506 pH
     -------------------              -----------------------
     total       1.01 nH              total       1.01 nH
```

This is not a bug and it cannot be fixed: the elements **are** the user's declarations, and
two declarations describing one network are two different tearings of it in the Kron sense
(§13.5). The report says so, so that a user who reorganises their table for readability and
sees the contribution column move finds the sentence before filing a defect.

**Re-terminating existing ports cannot evaluate new metal.** A shield, an extra via, a moved
trace, a widened return path — none of these is a termination of an existing port. They
change `Y` itself, which needs a new EM run. This is the boundary between "which of my
assumptions moved the answer" (this section) and "which layout is better" (a new solve), and
it is worth drawing sharply, because §13.7's output looks exactly like a layout-exploration
tool and is not one.

**The decomposition is gauge-dependent.** Change the baseline and every term changes. Fold
one element into the baseline — which the implementation does automatically when `Ybase` is
singular, the case of the repo's own flagship floating fixture at `cond(Y) = 2.5e16` — and
the remaining terms all move, even though the network, the total and the physics are
identical. What does **not** change is the element currents `I_e`: those are physical. The
*attribution of voltage* to each of them is a choice of gauge. This is PEEC's
partial-inductance warning restated, and it is why the report names the baseline it used
every time: two reports are comparable only when their baselines match.

### 13.11 Sign convention

Stated once, globally, and carried verbatim into every export:

- The victim reading is `V(+) − V(−)` of the victim measurement port.
- The aggressor is driven `+1 A` into its `+` side and out of its `−` side, so every term is
  signed the way `Z_ab = V_a / I_b` is.
- An element current `I_e > 0` flows **out of the structure into ground** for a shunt element
  (`u = e_p`), and **from `p` to `q`** for a series element (`u = e_p − e_q`).
- Flipping either measurement port's `+/−` assignment flips **every** term together.
  **Relative** signs between terms are physical; absolute signs are a labelling choice.

That last point is the same one §8.7 makes about `M`, `k` and `C_c`, and for the same reason:
two coupling paths that cancel must not be reported as two paths that add.

### 13.12 Worked example

`tests/fixtures/diff_pair_4port.s4p` is two coupled lines — port 1 (`in_p`) runs to port 3
(`out_p`), port 2 (`in_n`) runs to port 4 (`out_n`), `L_self = 5 nH`, `M = 1 nH`, with 1 fF
to ground at every port. Drive line one, listen on line two, both far ends grounded:

```
agg = 1     vic = 2     GND = 3,4     f = 5 GHz
```

Output, verbatim:

```
Attribution of M (vic <- agg) at 5 GHz
  total (compute_z_matrix) : 1.01 nH
  total (sum of terms)     : 1.01 nH   residual 6.42e-13 (floor 3.62e-09)
  cond(Ybase) 506   cond(H) 1   reciprocity 4.79e-15

  element                          contribution     share      quad
  bare EM coupling                       251 pH    24.88%     0.00%
  ground port 3                          252 pH    25.00%     0.00%
  ground port 4                          506 pH    50.12%     0.00%
```

Three readings:

1. **Three quarters of the answer is the grounding, not the metal.** Open both grounds and
   `M` falls to the 251 pH bare term. A 6 dB argument about this structure is an argument
   about the ground spec.
2. **The two ground balls are not worth the same.** Port 4 is the far end of the **victim's**
   own line and contributes 506 pH; port 3 is the far end of the aggressor's and contributes
   252 pH. That asymmetry is physical and it is what the table exists to expose — it is
   invisible in the single number `M = 1.01 nH`.
3. **`quad` is 0.00 % here** because everything is in phase at this frequency. On a lossy
   package it is not, and that column is what stops a 90-degree term from being read as
   cancellation.

Removing one ground gives a two-element case with a clean 50/50 split
(`bare EM 251 pH / ground 3 252 pH`, total 504 pH), which is a useful sanity check that the
bare term is the same object in both runs — it is, because the baseline is defined
independently of what was declared.

### 13.13 The singular baseline, and how it recovers

`Zbase = Ybase^-1` does not always exist, and the case where it does not is the repo's own
flagship coupling example. `coupled_4port_float.s4p` (`c1 = 1/2`, `c2 = 3/4`, no ground) has
`cond(Y) = 2.5e16` at 5 GHz: a fully floating differential structure has a singular node
admittance whose null direction is the common mode, exactly as §8.4 describes. A naive
implementation of §13.3 is wrong on day one against the tool's own worked example.

Two mechanisms recover it, in this order, and neither is a new user-facing concept.

**Elements outside the range of `Ybase` are folded into the baseline.** SVD `Ybase`, partition
the elements by whether `u_e` lies in `range(Ybase)` using core's existing `PROBE_RANGE_TOL`
(§8.4), fold the out-of-range ones in — which makes `Ybase'` nonsingular — and Woodbury only
the rest. A folded element has **no term of its own**, and it is reported by name rather than
silently absorbed:

```
Port(s) 4 are IN THE BASELINE because the structure has no reference without
them: with every non-probe port open the node admittance is singular, so
port 4 -> gnd was folded in and has no term of its own.
```

This is a gauge change in the sense of §13.10, which is why it is named on every report that
uses it: two reports are comparable only when their baselines match. Measured on that fixture
with a single `4 lumped_to_gnd R=50`, folding takes the effective condition number from
`7.3e15` to `5.7`.

**Otherwise `Zbase` is a pseudo-inverse**, and the report says which directions are therefore
untrustworthy. With no declared elements at all there is nothing to fold, so this is the path
`coupled_4port_float.s4p` actually takes: `pinv`, plus the warning that only probes and
elements orthogonal to the null space are meaningful. That is not a degradation here — it is
§8.4's argument again. A **balanced** `+/-` probe is orthogonal to the common-mode null
direction, so the pseudo-inverse is *exact* for it, which the numbers confirm: **zero**
declared elements, the decomposition is the bare EM term and nothing else, `M = 800 pH`, and
a reconciliation residual of exactly `0.0` against `compute_z_matrix`. The effective
condition number seen by the probes is `2.2`.

Both paths are structural checks and both come **before** any conditioning check. A rank
deficiency in `U` — the same port written `ground` twice through overlapping ranges, or a
`short_to` between two ports that are already grounded — is a **spec bug**, and reporting it
as "genuinely unattributable physics" would be the worst available outcome. It is tested
structurally on integer port-index sets first, with the offending elements named; only then
is `cond(G)` looked at. Elements whose `u` is the zero vector after probe-side merging are
dropped as already inert — the same class `inert_lumped_messages` reports on the Mode 5
validation strip.

### 13.14 The cold-start screen — which ports matter before a spec exists

Everything from §13.3 to §13.13 ranks **declarations**. At the start of a job there are none.
The designer knows the victim and the aggressor and nothing about the other 149 ports, and
the all-open configuration — the one `compute_z_matrix` returns for a probes-only spec, and
the one that produced the disputed number of §13.1 — has no elements in it, so `m = 0`, `U`
is empty, and the contribution table of §13.3 is **empty by construction**. §13.7's
sensitivity does reach undecided ports, but it is framed as "check the spec you already
wrote", which is a different question from "what should the spec say".

The cold-start screen answers the second one, in four steps, each exact and each read off the
**same** `Zbase` that §13.2 already defines.

#### 13.14.1 The closed form

Take one candidate port `p` and ground it ideally. That is §13.3 with exactly one element:
`u = e_p`, and `Zt = [0]`, because an ideal element has **zero impedance** (§13.2 — this is
the whole reason the `Zt` form is used rather than the `D = Zt^-1` form, which would need
`D = inf` here). Substituting into §13.3 term by term,

```
H   = Zt + G = [ u^T Zbase u ]      = [ Zbase[p,p] ]
p_b = u^T Zbase w_b                 = Zbase[p,b]
r_a = (w_a^T Zbase u)^T             = Zbase[a,p]
I   = solve(H, p_b)                 = Zbase[p,b] / Zbase[p,p]
```

and therefore

```
Z_ab(p grounded) = Zbase[a,b] − Zbase[a,p]·Zbase[p,b] / Zbase[p,p]

           dZ_ab =            − Zbase[a,p]·Zbase[p,b] / Zbase[p,p]
```

`H` is `1x1`, so the solve is a division and the "Woodbury update" is a scalar. The same
expression drops out of plain linear algebra without going through §13.3 at all: grounding a
port means `V_p = 0`, i.e. deleting row and column `p` of the node admittance, and the
`(a,b)` entry of the inverse of a matrix with one row and column deleted **is** that Schur
complement. Both readings earn their place — the first says *why the number is a
transimpedance times a current*, the second says *why it is exact*.

**Exact, not first order.** There is no expansion in a small parameter anywhere. The element
is not small, it is ideal, and the identity holds for any `Zbase[p,p] ≠ 0`.

Reproduced in this repo on `tests/fixtures/diff_pair_4port.s4p` at the grid point nearest
5 GHz (5.0005 GHz), ground-referenced probes on port 1 (`vic`) and port 2 (`agg`), candidates
3 and 4 — the closed form above, the module's own `cold_start_screen`, and an **honest**
re-solve through `compute_z_matrix` with a rebuilt `TerminationSet`:

| candidate | closed-form `dZ_ab` (Ω) | vs the honest re-solve |
|---|---:|---:|
| ground port 3 | `−6.5709e-10 + 7.9328411386800 j` | `7.11e-13` relative |
| ground port 4 | `−6.5593e-10 + 7.9328411386806 j` | `8.30e-13` relative |

On a planted 12-port case built for the purpose the worst relative disagreement over every
candidate is `1.47e-11`, and it is `<= 5.8e-11` over every fixture in the repo
(`tests/test_attrib_coldstart.py`).

**Cost.** The mathematics needs `Zbase[a, :]`, `Zbase[:, b]` and `diag(Zbase)` — **two solves
plus the diagonal** — and then one division per candidate. The implementation builds the whole
baseline once anyway (measured 350.6 ms at `N = 153`), because the other three steps need it;
the ranking itself is then **2.41 ms** for 151 candidates against **2402.6 ms** for one
`compute_z_matrix` per candidate, a factor of **997** for the identical answer.

#### 13.14.2 Why two coupling columns, and not their product

The formula is a product of three factors and the screen ranks on the product. It
nevertheless prints `|Zbase[a,p]|` and `|Zbase[p,b]|` as **two separate columns**, because
the product cannot be read backwards and the two factors mean different physical things:

- `Zbase[a,p]` — how strongly port `p` talks to the **victim**. A port with no path to the
  victim cannot carry coupling into it however loud the aggressor is at it.
- `Zbase[p,b]` — how strongly the **aggressor** talks to port `p`. The same argument the
  other way round.

A port has to do **both** to be a path, and either factor alone is a plausible-looking
ranking key that is wrong. Measured on the planted 12-port case: the port with the **largest**
`|Zbase[a,p]|` in the whole file — `34.777 Ω`, 67 % more than the real coupling path's
`20.873 Ω` — has `|Zbase[p,b]| = 0.038` and a true effect of **−0.378 pH**, against
**−395.369 pH** for the real path. Ranked on coupling-to-the-victim alone that port comes
**first** and is worthless; ranked on `|dZ_ab|` it is **fifth of eight**.

The repo's own fixture makes the complementary point, and more cleanly, because there the two
ports are provably equivalent. `diff_pair_4port.s4p` at 5.0005 GHz with the probes above:

| candidate | `\|Zbase[a,p]\|` | `\|Zbase[p,b]\|` | `\|Zbase[p,p]\|` | `dZ_ab` |
|---|---:|---:|---:|---:|
| port 3 (`out_p`, far end of the **victim's** line) | 15953.3 Ω | 7.89368 Ω | 15874.5 Ω | `+7.93284 Ω` |
| port 4 (`out_n`, far end of the **aggressor's** line) | 7.89368 Ω | 15953.3 Ω | 15874.5 Ω | `+7.93284 Ω` |

The two columns are swapped between the rows, they differ by a factor of **2021**, and the
two ports have the **same effect to twelve digits**. Rank on either column alone and one of
these two identical ports comes first while the other comes last. That is the argument for
printing both and ranking on neither.

#### 13.14.3 Step 0: the bracket

Before any ranking, one number: the quantity with every non-probe port **open**, against the
same quantity with every one of them at **ideal ground**, and the dB between. The low end is
`Zbase[a,b]` itself; the high end is §13.3 with one ideal element per candidate, i.e. one
`m x m` solve with `m` the candidate count — and that end comes with a second opinion for
free, because `compute_z_matrix` *can* be asked about the all-grounded spec (measured
agreement `1.44e-14` on `diff_pair_4port.s4p`). The all-open end has no second opinion
available and the report says so: no `TerminationSet` spells "the probe sides merged and every
element removed" — that **is** the baseline.

Measured **25.67 dB** on the planted case. It is printed first because it decides whether the
other three steps are worth reading at all.

**It brackets the open..ideal-ground family and nothing else.** It is not a bound over all
terminations, for exactly the reason §13.8 gives: as a function of one element's impedance
`z` the answer traces a **Möbius arc**, and an arc between two endpoints is not the segment
between them. A series ground inductance resonates with the structure's shunt capacitance and
leaves the bracket — measured on `diff_pair_4port.s4p`, sweeping one ground's series `L` over
`[0, inf)` peaks at 9 mH of apparent `M` at `L = 505 nH`, against a 1.01 nH open..ideal
bracket. `sweep_mobius` is the closed form for the interval actually achieved over a range
you can build; the bracket is the cheap first question, and it is labelled as such
(`COLD_START_BRACKET_CAVEAT`, carried verbatim into every export).

#### 13.14.4 Step 2: pairs, and why the second order is not optional

Two candidates `p`, `q` grounded together is §13.3 with `m = 2`:

```
      [ Zbase[p,p]  Zbase[p,q] ]         [ Zbase[p,b] ]
H  =  [ Zbase[q,p]  Zbase[q,q] ]   p_b = [ Zbase[q,b] ]   r_a = [ Zbase[a,p], Zbase[a,q] ]

dZ_ab  =  − r_a^T H^-1 p_b
```

one 2x2 solve, microseconds, exact. And the algebra says immediately where the surprise comes
from: **if `Zbase[p,q] = 0` then `H` is diagonal and `dZ_pair = dZ_p + dZ_q` exactly.** The
non-additivity `dZ_pair − dZ_p − dZ_q` is driven entirely by how much the two candidate ports
talk **to each other** — which is precisely what a one-at-a-time scan never measures, and
precisely what the two ends of one physical structure do maximally.

Measured, and this is why the step is mandatory rather than a refinement: a shield brought out
as two ports reads **+9.689 pH** with either end grounded alone and **−870.268 pH** with both
— **90x** the largest single-port effect in the file, with the **opposite sign**. A
single-port ranking reports that as two minor positive entries and nobody looks again. The
mechanism is the closed **loop**, not the grounding: `5 short_to 6`, with no ground anywhere,
gives the identical −870.268 pH.

The **mirror** direction — start from every candidate grounded and open one at a time, i.e.
§13.7(e)'s leave-one-out over the same context — catches the opposite failure and is run
alongside it. Sixty ground balls read `~0` each from all-grounded, because the other
fifty-nine carry the return; that same shield reads +880 pH per end. Neither direction
subsumes the other, which is why both are printed.

A pair is **flagged** when its non-additivity exceeds `max(0.5 x` the largest single-port
`|delta|` in the scan`, 0.01 x |`the all-open value`|)`. The first term is the one that means
something — a surprise smaller than half the best single port's effect will not change which
port you ground first. The second is a floor for the case where every single-port effect is
`~0`, which is the *normal* reading of a shield and of 60 ground balls from all-grounded, and
without it the first term collapses onto the noise and flags all 28 pairs. Measured: on the
planted case the threshold is 197.7 pH and **no** pair clears it (largest non-additivity
5.40 pH — the right answer, no pair mechanism was planted); on the shield case the threshold
is 4.84 pH and the one pair clears it at 889.6 pH, **184x**. Nothing is hidden by the
threshold — every scanned pair is returned, ranked, each carrying the threshold it was judged
against.

#### 13.14.5 Step 3: the greedy cumulative curve

Ground the best candidate, **re-rank**, ground the next best, and tabulate the answer against
`k`. This is §13.7(d) applied to candidates rather than to declarations, and it is the only
one of the four steps that answers *how many* ports matter — a ranking says which is biggest
and a pair scan says which two interact, and neither is that question. The report names the
`k` at which the curve comes within a stated fraction of the full open → all-grounded span,
**and states the fraction**, so "saturated" is a number the reader can disagree with rather
than a verdict.

Greedy is **not optimal**: the best-`k` subset is combinatorial and this module does not claim
to solve it (`docs/design_port_attribution.md` §11 item 4). What the re-ranking buys is that
the walk can step *into* the pair effects of §13.14.4 instead of past them.

#### 13.14.6 Port-name families: a proposal the tool tests, never an assumption

Grouping candidates by name family (core's `name_prefix`: `guard_ring1` and `guard_ring2` are
one `guard_ring`) **would** have caught the shield above, because the two ends of a guard ring
normally share a prefix. It is deliberately not done. Which ports are one physical structure
is a semantic judgement about the layout, and a tool that folds a guess about it into a number
has produced an answer nobody can audit.

So a name family only ever produces a **sentence**, with the numbers computed both ways
beside it:

```
ports 5,6 share the name family 'guard_ring'; tested together they are -870 pH,
tested separately +9.7 pH each -- if they are one structure, group them
```

The **numbers** are computed; the **grouping** is a suggestion the reader accepts or rejects.
Nothing in the bracket, the ranking, the pair scan or the curve depends on whether the file
carries port names at all — the whole report runs identically on a file with none, and
`tests/test_attrib_coldstart.py` pins that by running it twice.

(The minimum family size is **2** here, and deliberately not core's
`OPEN_CLUSTER_MIN_FAMILY = 4`. That threshold keeps a *remnant* check from crying wolf about
`coil1`/`coil2`; the case this one exists for is exactly a two-member family — the two ends of
one ring.)

#### 13.14.7 What the cold-start screen cannot find

**Anything that needs three or more ports to move together.** Step 1 is first order in the
candidate set, step 2 is exactly second order, and step 3's greedy walk can stumble onto a
triple but has no guarantee. A three-terminal version of the shield above is invisible to
every step. That sentence is on the report, not in a footnote
(`COLD_START_BLIND_SPOT_TEXT`).

Every boundary in §13.10 still applies too, in particular: **it cannot evaluate new metal.**
Every port it considers is one the S-parameter file already has. A shield that is not already
a port changes `Y` itself and needs a new EM solve.
