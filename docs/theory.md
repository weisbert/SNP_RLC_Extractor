# PKG RLC Extractor — Theory

This document explains the math and the modeling choices behind the tool. It assumes familiarity with S- and Y-parameters at the level of any standard RF text.

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

## 7. Mode 4 — VDD ports

In **AC small-signal** analysis a stiff supply is an AC ground:

```
V_dd = V_DC + v_ac     and     v_ac(VDD) = 0
```

So `vdd` and `ground` produce identical reductions. The distinction exists in the UI to document intent (this is a power port, not a return). Internally `Vdd` and `Ground` are treated as the same termination class.

If you need to model a non-ideal supply, use Mode 5 with `lumped_to_gnd(...)` to attach a finite supply impedance.

---

## 8. Unified port-termination abstraction (master model)

The four named modes are convenience presets of a single underlying model. Every port carries one of these terminations:

| Termination                  | Physical meaning                              | Effect on Y at each frequency                                                       |
|------------------------------|-----------------------------------------------|-------------------------------------------------------------------------------------|
| `open`                       | I=0, port floating                            | Schur-eliminate                                                                     |
| `ground`                     | V=0, tied to reference                        | Delete row / column                                                                 |
| `vdd`                        | V_ac = 0, AC-grounded ideal supply            | Same as `ground` (alias)                                                            |
| `signal` (group A or B)      | Excitation port, kept for the measurement     | Kept                                                                                |
| `short_to(j)`                | V_i = V_j, two ports tied together            | Merge rows i,j and cols i,j                                                         |
| `lumped_to_gnd(Y_term(f))`   | Port terminated through R/L/C network to GND  | `Y[i,i] += Y_term(f)`, then mark `open`                                             |
| `lumped_between(j, Y_term(f))`| Two ports linked by R/L/C network            | `Y[i,i] += y; Y[j,j] += y; Y[i,j] -= y; Y[j,i] -= y`, then mark both `open`         |

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
5. Compute `Z` from the remaining `signal` ports per the user query (driving-point or A<->B).

The named modes lower into this dispatch:

| Mode | Built-in builder              | Termination preset                                                                  |
|------|-------------------------------|-------------------------------------------------------------------------------------|
| 1    | `build_terminations_mode1`    | signal -> A; gnd -> ground                                                          |
| 2    | `build_terminations_mode2`    | signal_a -> A; signal_b -> B; gnd -> ground                                         |
| 3    | `build_terminations_mode3`    | as Mode 2, plus `ShortPair` couplings                                               |
| 4    | `build_terminations_mode4`    | as Mode 2, plus `vdd -> Vdd` (alias of ground)                                      |
| 5    | (user-supplied `TerminationSet`) | arbitrary mix of the above                                                       |

---

## 9. RLC extraction at a single frequency

At a chosen frequency `f` with `omega = 2*pi*f` and `Z(f) = R + j*X`:

```
R(f) = Re(Z(f))
L(f) = Im(Z(f)) / omega          (signed; <0 when capacitive at f)
C(f) = -1 / (omega * Im(Z(f)))   (signed; <0 when inductive at f)
Q(f) = Im(Z(f)) / Re(Z(f))       (signed; matches Cadence)
```

Values are reported with their physical sign rather than masked outside a "valid" region — this matches Cadence and lets the curve through SRF stay continuous on plots. Past SRF (`Im(Z) < 0` for an inductor) `L` and `Q` go negative while `C` becomes positive (the parasitic capacitance dominates). Q here is the reactance-to-resistance ratio of a series-equivalent at `f`; its sign reflects whether the network is net-inductive (`Q > 0`) or net-capacitive (`Q < 0`) at that frequency.

---

## 10. Broadband fitting

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

## 11. Use case examples

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
Mode 5 with terminations:
   port1 = signal
   port2 = lumped_to_gnd(R = 50)
   port3..N = ground
Why:    Measure driving-point impedance with a realistic source / load termination.
```

### What this tool does NOT do

All extracted R/L/C/Q values are **totals** for the network as seen between the chosen signal ports. They are never per-unit-length. If you want per-unit-length values, divide by your known length yourself. The tool does not perform distributed transmission-line (RLGC-per-length) extraction; that requires a different procedure (multi-section ABCD or `gamma` / `Z_0` extraction).
