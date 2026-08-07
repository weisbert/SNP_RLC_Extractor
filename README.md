# PKG RLC Extractor

A desktop tool for RF and analog IC engineers to extract R, L, C, and Q from Touchstone (`.sNp`) files — and, with more than one measurement port defined, the **mutual coupling** between them: M, k, the coupling ratio M/L, and the coupling capacitance C_c. It targets package parasitic characterization but applies equally to EMX-extracted layout traces, DCO / spiral inductors, decoupling capacitors, and any other passive structure for which an EM solver (EMX, HFSS, Q3D, etc.) has produced an S-parameter matrix. All extractions go through a unified Y-parameter Schur-complement reduction; the named "modes" in the UI are presets of a single underlying port-termination model.

The mental model for a measurement is a pair of multimeter probes: a **red probe** on the `+` ports and a **black probe** on the `-` ports. One such probe pair gives a self impedance; several of them alive at once give a G x G impedance matrix whose diagonal is the self impedances and whose off-diagonal is the open-circuit mutual impedance.

---

## Installation

Requires Python 3.11+.

```bash
pip install -r requirements.txt
```

`requirements.txt` pulls in `numpy` (hard requirement) and `matplotlib` (GUI only). Tkinter ships with the standard Python distribution.

On an isolated machine where nothing can be installed, see [Air-gapped deployment](#air-gapped-deployment-red-zone) — `numpy` alone is enough for the CLI and for `reduce_snp.py`.

---

## Quick Start — GUI

```bash
python pkg_rlc_extractor.py
```

Basic flow:

1. **Add File...** — load any Touchstone file. The parser content-sniffs the port count and ignores the file extension; `.s2p`, `.s45p`, `.txt`, `.dat`, or no extension all work.
2. A default trace is auto-created against the loaded file. Select it in the **Traces** listbox to edit.
3. In **Edit Selected Trace**, pick a measurement mode (Modes 1-3, `+/- Ports / Coupling`, or Custom) and fill in the relevant port fields. Modes 5 and 6 are filled in as **tables** with a `+ Add` button rather than typed as text: Mode 6 gets the measurement-port table, Mode 5 that table plus a connections table underneath, with a port-overview line and a validation line under both. Every port cell takes port **numbers**; **Show Ports** lists the file's port *names* in the Results pane, which is where to look on an unfamiliar package. R/L/C cells take one word with an SI suffix and no unit (`5m`, `0.5n`, `1u`) — the unit is in the column header, and a value with a space in it is rejected rather than silently truncated.
4. Set **RLC Freq (GHz)** for single-point extraction, optionally enter a **Band Fit** range and model.
5. Click **Calculate All & Plot**. Results appear in the right pane and overlay on the multi-subplot view.

Use **Export CSV** to dump per-trace `Freq, Re(Z), Im(Z), |Z|, R, L, C, Q` tables.

A `+/- Ports / Coupling` trace expands into several curves: one self curve per measurement port plus one mutual curve per pair (the `self` / `mutual` checkboxes select which). A mutual curve is just another complex `Z(f)` array, so every subplot works on it — on a mutual curve the `L(nH)` subplot reads **M in nH** and `C(pF)` reads the coupling capacitance **C_c**. The `k` subplot is filled in for mutual curves only; self curves leave it blank.

---

## Quick Start — CLI

Single-frequency Mode 1 (driving-point to ground):

```bash
python pkg_rlc_extractor.py --cli file.s45p --mode gnd \
    --porta "1" --gnd "6:1:14" --freq 0.1
```

Mode 2 (port group A vs port group B) with broadband inductor fit and CSV export:

```bash
python pkg_rlc_extractor.py --cli file.s45p --mode p2p \
    --porta "1,2" --portb "3,4" --gnd "5:1:10" \
    --freq 0.1 --fit auto --fmin 0.1 --fmax 5 --csv output.csv
```

Mode 2 with a capacitor band fit (e.g., differential trace `C_diff`):

```bash
python pkg_rlc_extractor.py --cli trace.s5p --mode p2p \
    --porta "1" --portb "2" --gnd "5" \
    --fit capacitor --fmin 0.1 --fmax 5
```

### Coupling mode (self + mutual)

`--mode coupling` takes one `--mport` per measurement port. Each one is a probe pair
(`--porta` / `--portb` are not used in this mode):

```bash
python pkg_rlc_extractor.py --cli tests/fixtures/coupled_4port_float.s4p \
    --mode coupling --mport "c1 = 1 / 2" --mport "c2 = 3 / 4" --freq 5.0
```

Real output (the fixture is two synthetic coils, `L1 = 2 nH`, `L2 = 3 nH`, `M = 800 pH`,
fully floating — no ground port anywhere in the file):

```
Loaded tests\fixtures\coupled_4port_float.s4p: N=4, M=100, Z0=50Ω
  WARN: Rank-deficient node admittance at freq[0]=1e+08 Hz (pinv used; expected for a fully floating structure)
  WARN: Rank-deficient node admittance at freq[1]=2e+08 Hz (pinv used; expected for a fully floating structure)
  WARN: Rank-deficient node admittance at freq[2]=3e+08 Hz (pinv used; expected for a fully floating structure)
  (rank-deficient node admittance is informational: a fully floating differential structure trips it at every frequency and pinv still gives the right answer)

Measurement ports: c1 [0], c2 [1]

@ 5 GHz  --  Z matrix (Ω), open-circuit: every other measurement port carries no current
                      c1                  c2
  c1        0.6 + j62.83  3.419e-15 + j25.13
  c2  2.272e-16 + j25.13        0.9 + j94.25

Self impedance (diagonal). Signs are physical (Cadence convention):
  Port       R     L        C      Q  Sign
  ----  ------  ----  -------  -----  ----
  c1    600 mΩ  2 nH  -507 fF  104.7  ind
  c2    900 mΩ  3 nH  -338 fF  104.7  ind
  legend: ind = Im(Z)>0 (inductive) | cap = Im(Z)<0 (capacitive; past SRF for an inductor) | R<0 = non-passive

Mutual coupling (per unordered pair):

  c1 <-> c2
    Z_ab   = 3.419e-15 + j25.13 Ω
    M      = 800 pH                Im(Z_ab)/ω
    C_c    = -1.27 pF              -1/(ω*Im(Z_ab))   <- negative: Im(Z_ab)>0, the coupling is inductive here -- read M
    k      = 0.3266                M/sqrt(L_c1 * L_c2)
    M/L_c1 = 0.4  (-7.959 dB)      coupling ratio into c1
    M/L_c2 = 0.2667  (-11.48 dB)   coupling ratio into c2

  M/L_x is the first-order Norton injection ratio into x -- frequency-independent, and the number a spur / pulling budget is written against. It is not the exact current-transfer ratio |Z_ab/Z_aa|, which it matches only where omega*L_x >> R_x.

Reciprocity error = 7.18e-16   (max|Z_ab - Z_ba| / max|Z_ab| over the finite off-diagonal entries)
  (data looks reciprocal; the alarm threshold is 0.001. A clean EM solve lands at 1e-16..1e-9, so a few 1e-9s here are normal, not a defect.)
```

Two more shapes of the same command:

```bash
# ground-referenced probes (empty '-' side): a package file with real GND balls
python pkg_rlc_extractor.py --cli bus.s16p --mode coupling \
    --mport "vic = 1" --mport "agg = 2" --gnd "3:1:16" --freq 1.0

# per-frequency CSV: Re/Im of every Z_ij, plus M_nH and k for every pair
python pkg_rlc_extractor.py --cli xfmr.s4p --mode coupling \
    --mport "w1 = 1 / 3" --mport "w2 = 2 / 4" --freq 1.0 --csv coupling.csv
```

`--fit` works in coupling mode too: it is applied to the **self** impedance of every
measurement port in turn (the diagonal), one fit report each.

CLI flags:

| Flag      | Meaning                                                                  |
|-----------|--------------------------------------------------------------------------|
| `--cli`   | Enable CLI mode (otherwise GUI launches)                                 |
| `--mode`  | `gnd` (Mode 1), `p2p` (Mode 2/3) or `coupling` (Mode 6)                  |
| `--porta` | Signal / Port A specification (port range syntax)                        |
| `--portb` | Port B specification (required for `p2p`)                                |
| `--mport` | One measurement port for `coupling`; **repeatable**. `"<name> = <+ ports> / <- ports>"` |
| `--gnd`   | Ground port specification                                                |
| `--vdd`   | **Deprecated** alias for `--gnd`; the ports are unioned into the ground list and a note is printed |
| `--short` | Short pairs for Mode 3 (e.g., `"45-46,47-48"`); also accepted in `coupling` |
| `--freq`  | Single-point extraction frequency in GHz (default `0.1`)                 |
| `--fit`   | Band-fit model: `none` \| `auto` \| `inductor` \| `capacitor`            |
| `--fmin`, `--fmax` | Band edges in GHz for `--fit`                                   |
| `--csv`   | CSV output path (coupling mode: Re/Im of every `Z_ij` plus M and k)      |

---

## Port Range Syntax

Port numbers are **1-based** at every UI and CLI surface (the core converts to 0-based internally).

| Form                 | Example         | Expands to                          |
|----------------------|-----------------|-------------------------------------|
| Single               | `1`             | `[1]`                               |
| Comma list           | `1,3,5`         | `[1, 3, 5]`                         |
| MATLAB `start:step:stop` | `35:1:45`   | `[35, 36, ..., 45]` (inclusive)     |
| Dash range           | `6-14`          | `[6, 7, ..., 14]`                   |
| Mixed                | `1,3,35:1:45,50-55` | concatenation of the above       |

Short-pair syntax (Mode 3 only): comma-separated `a-b` pairs, e.g. `45-46, 47-48`.
Chain dashes to tie more than two ports into one node: `1-2-3-4` is a single 4-port group.

### Measurement-port (mport) syntax

Used by every `--mport` on the CLI. In the GUI, Mode 6 presents the same thing as a
**table** — one row per measurement port, `Name` / `+ ports` / `- ports`, with a `+ Add`
button — so the syntax below is only needed on the command line. The rules are identical
either way; a row's two port cells take the same range syntax as the sides of a spec.

One measurement port per entry — a red probe on the `+` ports, a black probe on the
`-` ports:

```
[<name> =] <+ ports> [/ <- ports>]
```

| Example            | Meaning                                                                |
|--------------------|------------------------------------------------------------------------|
| `tank = 1,3 / 2,4` | named `tank`; red probe on 1 and 3, black probe on 2 and 4              |
| `1 / 2`            | unnamed (auto-named `P1`); red on 1, black on 2                        |
| `rx = 5:1:9 /`     | named `rx`; red on 5..9, `-` side empty -> referenced to ground         |
| `3,4`              | red on 3 and 4, ground-referenced (no `/` needed)                       |

Rules:

- `/` separates the two sides; at most one per entry. `=` introduces the optional name.
- **Both** sides take the full port-range syntax above (`1,3`, `6-14`, `35:1:45`, ...).
- Ports on the same side are tied together in parallel; there are **no weights**, only
  membership in the `+` side, the `-` side, or neither.
- The `+` side may not be empty. An empty `-` side is legal and means "referenced to the
  Touchstone ground".
- A port may appear on only one side of only one measurement port.
- The names `A` and `B` are **reserved** for the legacy Mode 1/2/3 signal groups (and for
  `signal A` / `signal B` in Mode 5). The check is case-insensitive.

---

## Measurement Modes

| Mode | UI label                | What it measures                                                                                  |
|------|-------------------------|---------------------------------------------------------------------------------------------------|
| 1    | `Port(s) -> GND`        | Driving-point impedance from a signal port (or shorted group) to ground.                          |
| 2    | `A <-> B`               | Impedance between two port groups; collapse to 2x2 then `Z = Z11 + Z22 - Z12 - Z21`.              |
| 3    | `A <-> B + Short Pairs` | Like Mode 2, but with explicit `i-j` shorts (Y-matrix row/col merging) before reduction.          |
| 4    | *(retired)*             | Was `A <-> B + VDD/GND`. See below.                                                               |
| 5    | `Custom (advanced)`     | Two tables: the measurement-port table (Name / `+` / `−`) plus a connections table (Type / Port / To / R / L / C), where Type is `ground / vdd / open / short / rlc_gnd / rlc_between`. Port and To take the full range syntax, so `6-14` or `35:1:45` is one row. **Edit as text…** shows and takes back the equivalent DSL — `open / ground / vdd / signal <name> [+ or -] / short_to / lumped_to_gnd / lumped_between`, one directive per line — which is what the tables serialise to and what is actually computed. |
| 6    | `+/- Ports / Coupling (M, k)` | Any number of measurement ports, each a `+` / `-` probe pair, entered as table rows. Produces the G x G impedance matrix: self impedance on the diagonal, **open-circuit mutual impedance** off it, and from that M, k, C_c and the M/L ratios. |

Defining more than one measurement port gives you the coupling matrix **in either mode** —
Mode 5 used to report only the first one and warn about the rest, which was a wrong number
with no visible difference. The full matrix is now produced whenever the spec defines two
or more measurement ports, whichever mode wrote it.

Mode codes are stable and are never renumbered, so saved configurations keep working.

### Mode 4 is retired: VDD ports go into the GND field

For AC small-signal analysis an ideal supply **is** a short to the reference node: a VDD
ball and a GND ball impose the same boundary condition, `V = 0`. The old Mode 4 computed
exactly what Mode 2 computes when both sets are listed as ground ports — there was never a
numerical difference, only a label. So:

- The GUI field is now labelled **GND / VDD (AC gnd)**; put ground pins and supply pins in it.
- A saved mode-4 trace is migrated automatically to mode 2 with its VDD ports folded into
  GND, and the migration is reported in the results pane.
- On the CLI, `--vdd` still parses but is deprecated: its ports are unioned into `--gnd` and
  a `NOTE:` line says so.
- `Vdd` remains a distinct termination class in the core (and `vdd` in the Mode 5 DSL) so
  intent stays documentable — it is evaluated identically to `Ground`.

### Mode 6 in one picture

```
     RED   1 o---+---------------+---o 3   RED
                 |    Network    |
   BLACK   2 o---+  (Y-matrix)   +---o 4   BLACK

           "tank = 1 / 2"        "vco2 = 3 / 4"
```

With G measurement ports the tool builds a `G x G` matrix at every frequency:

| Entry      | Meaning                                                                     |
|------------|------------------------------------------------------------------------------|
| `Z[a][a]`  | self impedance of port a -> `R`, `L = Im(Z)/ω`, `C = -1/(ω·Im(Z))`, `Q = Im/Re` |
| `Z[a][b]`  | mutual impedance, **all other measurement ports open** -> `M`, `k`, `M/L`, `C_c` |

and reports, per unordered pair:

| Quantity | Formula                        | Use it for                                              |
|----------|--------------------------------|---------------------------------------------------------|
| `M`      | `Im(Z_ab)/ω`                   | the absolute number to hand to a circuit simulator      |
| `C_c`    | `-1/(ω·Im(Z_ab))`              | the same `Im(Z_ab)` read as a capacitance when it is negative |
| `k`      | `M / sqrt(L_a·L_b)`            | comparing layouts; size-normalised, abs(k) <= 1 if passive |
| `M/L_a`  | coupling (Norton injection) ratio into a, also in dB | comparing against an injection / spur budget |
| reciprocity error | `max abs(Z_ab - Z_ba) / max abs(Z_ab)` over the finite off-diagonal entries; alarm above `1e-3` | a health check on the input data, not a result   |

Filling in **one** measurement port with both sides (`tank = 1 / 2`) gives the
*differential* self impedance — the `L_diff` a balanced tank actually resonates with. Putting
both terminals on the `+` side (`tank = 1,2 /`) gives the *common-mode* impedance instead;
the `+/-` split is what makes that distinction explicit.

### EMX trace and inductor use cases

The same engine handles structures that are conceptually very different. What changes is only the port-termination configuration:

| Structure                          | Mode | Port assignment                                                                 | Fit model |
|------------------------------------|------|---------------------------------------------------------------------------------|-----------|
| **DCO / spiral inductor** (2-port P, N) | 2    | A=P, B=N, GND=(none if no GND port)                                             | Inductor  |
| **Diff trace, loop inductance** (5-port: inp, inn, outp, outn, gnd) | 3 | A=inp, B=inn, Short Pairs=`outp-outn`, GND=gnd_port | Inductor |
| **Diff trace, differential C**     | 2    | A=inp, B=inn, GND=gnd_port (outp/outn left default Open -> Schur-eliminated)    | Capacitor |
| **Decap with two mounting pads shorted** | 3 | A=pad1_top, B=gnd_top, Short Pairs=`pad1_bot-gnd_bot`                          | Capacitor (reports ESR, ESL, C) |
| **50 ohm-terminated signal path**  | 5    | port1=signal, port2=`lumped_to_gnd(R=50)`, others=ground                        | Auto      |
| **Two coils, M / k between them** (4-port) | 6 | `c1 = 1 / 2`, `c2 = 3 / 4`, GND=(none if the coils float)                  | Inductor (per diagonal) |
| **Aggressor -> victim on a bus** (16-port) | 6 | `vic = 1`, `agg = 2`, GND=`3:1:16`                                        | Auto      |

For loop-inductance measurements on a trace (Mode 3), shorting the far end forces the signal to return through the trace itself, exposing the differential loop inductance. For `C_diff` measurements (Mode 2), leaving the far end open isolates the inter-trace capacitance.

---

## Workflow: budgeting coupling in dB without re-simulating the VCO

This is the loop Mode 6 exists for. The expensive thing in a pulling / spur investigation is
not the EM run — it is the nonlinear VCO or PLL simulation you would otherwise repeat for
every layout candidate. `M/L` lets you skip it.

**The key property:** `M/L_victim` is **frequency-independent**. The aggressor current
`I_agg` induces an EMF `j·ω·M·I_agg` in series with the victim's tank branch; Thevenin ->
Norton across the victim's own inductance divides that by `j·ω·L_victim`, and the `j·ω`
cancels:

```
I_inj = (M / L_victim) * I_agg
```

So a single scalar — one number, in dB — says what fraction of the aggressor's current is
injected into the victim tank, at every frequency. That is exactly the shape of an injection
or spur budget.

**The loop:**

1. Run EM **once** on the current layout, load the `.sNp`.
2. Declare one measurement port per structure (`--mport "tank = 1 / 2"`,
   `--mport "pa = 3 / 4"`, ...) and read `M/L_victim` in dB.
3. Compare against the budget. From the example output above: `M/L_c1 = -7.959 dB`. Against
   a `-30 dBc` budget that fails by 22 dB; against a `-5 dB` transformer spec it passes.
4. Change the layout — more spacing, a guard ring, rotate one coil 90 degrees, add a
   patterned shield — re-run EM, reload, hit Calculate. Compare the new dB number and the
   new `k` to the previous iteration.
5. Repeat until it passes. Only then run the full nonlinear loop simulation, **once**, to
   confirm.

**Which number to compare against what:**

- **`M/L_victim` in dB** vs. the injection / spur budget. Note `M/L_a` and `M/L_b` are
  different numbers when the two structures differ in size — always divide by the `L` of the
  structure being *disturbed*.
- **`k`** vs. the previous layout iteration. `k` divides out how big each structure is, so it
  is the honest layout-vs-layout comparison; raw `M` is not, if the coil geometry changed.
  On-chip, `k` in the 0.001-0.05 range is normal for two coils that are not meant to couple;
  0.05-0.3 usually means a layout problem; above 0.3 you have built a transformer.
- **`M` in henries** is what you hand to the circuit simulator for the final confirmation
  run.

**Signs carry information.** `M`, `k` and the `M/L` ratios are reported signed and are never
`abs()`-ed. Two aggressors with `M = +20 pH` and `M = -20 pH` into the same victim *cancel*;
reported as two `+20 pH` paths they would look twice as bad as reality. Swapping the `+` and
`-` ports of one measurement port flips every sign and changes no magnitude.

**Caveat, stated plainly.** `M/L` is a first-order Norton equivalent at the victim's tank
branch. It is a budget number, not a spur prediction: the actual spur also depends on the
tank Q, the aggressor amplitude and the nonlinearity. Use it to *rank and screen* layouts,
and confirm the winner in the full simulator. It is also **not** the exact current-transfer
ratio, which is `I_a/I_b = -Z_ab/Z_aa`; the derivation drops `R_a`, so the two agree only
where `ω·L_a >> R_a`. For `L_a = 2 nH`, `R_a = 1.5 Ω`, `M = 0.9 nH` the tool reports a flat
`M/L_a = 0.450` while `abs(Z_ab/Z_aa)` is `0.038` at 10 MHz, `0.289` at 100 MHz and `0.447`
at 1 GHz. At the tank frequency — where the budget lives — they agree.

---

## Important Notes

- **Results are TOTAL values, not per-unit-length.** `L`, `C`, `R` are reported for the network as seen between the chosen signal ports. To get per-unit-length values, divide by your known trace length yourself. The tool does not perform distributed (RLGC-per-length) extraction; that requires multi-section ABCD or `gamma`/`Z_0` extraction.
- **AC small-signal only.** `vdd` is an alias for `ground` because at AC the supply is an ideal short. The distinction exists in the UI for documentation clarity; Mode 4 was retired for the same reason.
- **Unlisted ports are OPEN, not grounded.** This is the most common source of wrong results. A forgotten GND ball floats and is Schur-eliminated, which preserves the behaviour at the kept ports but does *not* tie the port to the reference node.
- **The mutual `Z_ab` is the open-circuit one.** `Z[a][b]` is defined with every *other* measurement port carrying no current — that is the textbook definition of M, and the right primitive to hand to a simulator, where the real loading is modelled. It is not the same number as a short-circuit transfer measurement.
- **Signs are physical and are never clipped.** `R / L / C / Q` and `M / C_c / k` all keep their sign (Cadence convention). `L` and `Q` go negative past SRF; `C_c` comes out negative whenever the coupling is inductive; `M` comes out negative when it is capacitive. Both readings are always computed — use the sign of `Im(Z_ab)` to pick which one to headline. `k` is `NaN` (with a note) where `L_a <= 0` or `L_b <= 0`, and `abs(k) > 1` is flagged rather than clamped.
- **Content-based file detection.** Port count is inferred from token count and frequency monotonicity — extension is ignored. Files without an option line are assumed `# GHZ S MA R 50` and a warning is emitted.
- **Numerical fallbacks.** Schur reduction uses `np.linalg.solve`; if `Y_oo` is singular, it falls back to `lstsq` and reports the offending frequency. The contraction onto the probe nodes uses `pinv`, so a fully floating differential structure works — see the next note.
- **"Rank-deficient node admittance" is informational, not an error.** A structure with no ground reference at all (two isolated coils in a 4-port file — the normal coupled-inductor case) has a singular node admittance whose null direction is the common mode. The balanced `+/-` injection is orthogonal to it, so the pseudo-inverse returns the correct answer. Expect this message, capped at 3 lines, on every clean floating run. It applies to a **single** `+/-` measurement port too: `LAPACK`'s `inv` does not raise on a numerically singular 2x2, so the tool tests the determinant itself and routes those frequencies to `pinv`. Sanity-check with the reciprocity error instead.
- **Ground-referenced probes need a real ground path — and the tool now says so.** If you give a measurement port an empty `-` side but the network has no admittance to the reference node, you are asking current to return through a wire that does not exist. `pinv` would happily return a finite, plausible-looking minimum-norm number (exactly `Z_series/4` for a floating pair probed single-ended, a flat `0 Ω` for a floating series element), so instead that measurement port's whole row and column of `Z` come back `NaN` with a warning that names it — `"Measurement port(s) '…' have no return path for the injected current"`. Other measurement ports in the same run are unaffected and keep their exact values. A second, advisory check reports `"Schur contraction cancelled to roundoff"` when the reduction of the unused ports leaves nothing but cancellation noise (e.g. `--mport "c1 = 1" --mport "c2 = 3"` on the floating fixture above); those numbers are still printed but are roundoff amplified to ~`1e16 Ω`.
- **Port numbers are checked against the file.** A number the file does not have — `"3 / 5"` on a 4-port file — is a hard error, not a silently ground-referenced probe. Same for out-of-range `--gnd` and `--short` ports.
- **A probe port may not also be a GND port.** A probe side is tied together, so grounding one of its ports grounds the whole side; Mode 6 rejects the combination instead of quietly dropping the port from the probe. (Modes 1-3 keep their historical "ground wins" precedence.)

---

## Project Structure

```
SNP_RLC_Extractor/
  CLAUDE.md                  Conventions for future Claude Code sessions
  README.md                  This file
  CLAUDE_CODE_PROMPT.md      Authoritative spec
  requirements.txt           numpy (hard), matplotlib (GUI only)
  VERSION                    Commit stamp, filled in by the red-zone packer
  pkg_rlc_core.py            Touchstone parser, S->Y, termination model + DSL, Schur,
                             compute_z / compute_z_matrix, RLC + coupling extraction, fits
  pkg_rlc_plot.py            Matplotlib plot panel with M / V / Delete / drag features
                             (R, L, C, |Z|, Re, Im, Q, k subplots)
  pkg_rlc_gui.py             Tkinter GUI with file/trace management
  pkg_rlc_help.py            In-app Help window (one tab per mode + syntax + examples)
  pkg_rlc_extractor.py       Entry point (GUI + CLI)
  reduce_snp.py              Standalone CLI: shrink a big .sNp to a few ports
  deploy.sh                  Red-zone update entry point (top level by design)
  tests/
    test_core.py
    test_coupling.py         Mode 6: probe pairs, Z matrix, M / k / C_c / M-over-L
    test_golden_regression.py  Bit-exact replay of the pre-coupling behaviour
    test_port_parser.py
    test_content_sniffer.py
    test_reduce_snp.py
    generate_test_snp.py
    _golden_capture.py       Script (not a test) that (re)builds the golden .npz
  docs/
    theory.md                Math, circuit diagrams, mode derivations
  deploy/
    pack.ps1                 Windows: build the red-zone package
    doctor.sh                Red zone: what can this box actually run?
    _env_check.py            Per-interpreter probe used by doctor.sh
    README.md                Full air-gap procedure
```

---

## Air-gapped deployment (red zone)

To run this on an isolated Linux machine with no network, no git, and no ability
to `pip install` or create a venv:

```powershell
# on Windows, after committing
powershell -ExecutionPolicy Bypass -File deploy\pack.ps1
```

Upload `deploy\dist\Snp_analyzer_<short>.tar.gz` **and** its `.sha256` into the
install directory, then on the isolated box (its login shell is often tcsh, so
invoke with `bash`):

```bash
cd .../Snp_analyzer
bash deploy.sh              # no argument -- picks up the tarball sitting here
bash deploy/doctor.sh --test
```

`deploy.sh` checksums the package, backs up the current install, swaps atomically
enough to auto-roll-back on failure, and keeps the last 3 versions. Everything it
writes stays under `<install>/.deploy/` — never `/tmp` or anywhere else on the box.
`doctor.sh` then reports what that box can run, in tiers — `reduce_snp.py` and the
CLI need only `numpy`; the GUI additionally needs `matplotlib`, `tkinter` and
`$DISPLAY`. Missing GUI dependencies are a degrade, not a failure.

If you only need port reduction on a simulation server, `pack.ps1` also emits a
standalone `reduce_snp_<short>.py` that runs on its own with nothing but `numpy`.

Full procedure, rollback, and how to keep your own data across deploys:
[deploy/README.md](deploy/README.md).

---

## Theory

For the math behind each mode (S->Y conversion, Schur complement, the unified termination
abstraction, the `+/-` probe model and the M / k / M-over-L derivations, and the broadband
fitting models) see [docs/theory.md](docs/theory.md).

The in-app **Help** button opens the same material as a tabbed reference — one tab per mode,
plus input syntax and worked examples.
