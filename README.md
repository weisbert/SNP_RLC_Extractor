# PKG RLC Extractor

A desktop tool for RF and analog IC engineers to extract R, L, C, and Q from Touchstone (`.sNp`) files — and, with more than one measurement port defined, the **mutual coupling** between them: M, k, the coupling ratio M/L, and the coupling capacitance C_c. It targets package parasitic characterization but applies equally to EMX-extracted layout traces, DCO / spiral inductors, decoupling capacitors, and any other passive structure for which an EM solver (EMX, HFSS, Q3D, etc.) has produced an S-parameter matrix. All extractions go through a unified Y-parameter Schur-complement reduction; the named "modes" in the UI are presets of a single underlying port-termination model.

The mental model for a measurement is a pair of multimeter probes: a **red probe** on the `+` ports and a **black probe** on the `-` ports. One such probe pair gives a self impedance; several of them alive at once give a G x G impedance matrix whose diagonal is the self impedances and whose off-diagonal is the open-circuit mutual impedance.

A separate layer, [port attribution](#port-attribution-where-a-coupling-number-comes-from), answers the question that follows: of the `M` you just extracted, how much is the metal and how much is the grounding you assumed? It splits one `Z_ab` into the bare EM coupling plus one signed term per termination you declared — exactly, by superposition — and tells you what the answer would be with any of those terminations changed.

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

1. **Add File...** — load any Touchstone file. The parser content-sniffs the port count and ignores the file extension; `.s2p`, `.s45p`, `.txt`, `.dat`, or no extension all work. Each load prints a summary — port count, point count, `Z0`, the option line actually used, **the frequency span**, and `max |S|` — and the span is repeated on the file's line in the list. See [Reading files](#reading-files) for what a load failure tells you and what **Check File** is for.
2. A default trace is auto-created against the loaded file. Select it in the **Traces** listbox to edit.
3. In **Edit Selected Trace**, pick a measurement mode (Modes 1-3, `+/- Ports / Coupling`, or Custom) and fill in the relevant port fields. Modes 5 and 6 are filled in as **tables** with a `+ Add` button rather than typed as text: Mode 6 gets the measurement-port table, Mode 5 that table plus a connections table underneath, with a port-overview line and a validation line under both. Every port cell takes port **numbers**; **Show Ports** opens the **Ports & Roles** window, which is where to look on an unfamiliar package — see [Ports & Roles](#ports--roles). R/L/C cells take one word with an SI suffix and no unit (`5m`, `0.5n`, `1u`) — the unit is in the column header, and a value with a space in it is rejected rather than silently truncated.
4. Pick the curve's **Style** — click the line preview to open a palette of the 12 colours and 4 line styles, drawn as they will be drawn on the plot. On a coupling trace the preview also shows the run of colours the trace's expanded curves will occupy, and `×n` for how many.
5. Set **RLC Freq (GHz)** for single-point extraction, optionally enter a **Band Fit** range and model.
6. Click **Calculate All & Plot**. Results appear in the right pane and overlay on the multi-subplot view. **Calculate This Trace**, in the editor's footer, recomputes only the selected trace — the fast path when you are iterating on one port spec with several traces loaded.

**Edits apply as you type.** There is no *Apply* step: whatever is in the editor is what the selected trace holds, and the Traces list updates live. A trace whose spec has changed since it was last computed carries a trailing `*` in that list.

**Showing and hiding curves.** Every trace has a `☑` / `☐` in the Traces list. Toggle it with the **Show/Hide** button, with the space bar on the list, or with **Plot: this trace** in the editor — a hidden trace comes off the plot immediately, without recomputing anything and without disturbing the `V` cursors you have placed. The checkbox governs every output, not just the picture: a hidden trace comes off the results table and out of the CSV export too — the table says what is on the plot, and a row for a curve that is not drawn reads as a duplicate of the one that is. It is still measured: one line under the table names it, and its numbers stay in memory, so showing it again costs no Calculate (tick it back on and export again to get it into a file). This is the way to compare two of five traces without deleting the other three.

**Before and after: freezing a trace.** Right-click a trace in the Traces list → **Freeze as new trace**. That takes a snapshot: a second trace holding exactly the numbers this one has now, in the next colour and line style, labelled with the time (`tank <14:32>`). Change the original and press Calculate — the snapshot does not move, because Calculate skips it and the editor refuses to write into it (selecting one greys the editor out and says so; the list marks it `❄`). So the two curves are genuinely the before and the after, compared over the whole sweep instead of at one marker frequency, and both are in the results table, the cursor readout and the CSV. Right-click → **Unfreeze** gives it back to Calculate, which then *replaces* the numbers it was holding. One deliberate limit: a config file carries the setup and never the results, so a frozen trace comes back from Save/Load with its spec and no numbers — it says so in the Results pane and reads `❄ no numbers` in the list rather than quietly drawing nothing; unfreeze and Calculate reproduces the snapshot exactly if the file has not changed.

**Run history.** The Results pane is a set of tabs. `Log` is the running commentary it has always been; every Calculate adds a page beside it, newest first, labelled `#7 10:42`, holding that run's report under a heading that says which run it is, at what marker frequency, over which traces — and, on the second line, **what you changed since the previous run** (`changed since #11:  [3] gnd 6-14 -> 6-16`). That line is the useful one: twenty runs are all at 5 GHz and nobody remembers what they were doing at 14:32. Old pages are dropped automatically, oldest first, three at a time by default; press **Keep** (or right-click the tab) and that page is never dropped by anything automatic — only by right-click → **Close this run**. The kept pages have their own budget, which is why Calculate can never be blocked by them and can never throw one away: at the cap the Keep button is already disabled and says `Keep (5/5) — close a kept run first`. `Runs ▾` lists every page with its full description, which is where to look once the tabs are too narrow to read, and it is also where the two limits are set. Switching to the new page is **conditional** — it happens only if you were already reading the newest page (or the Log), so a page you deliberately kept open is not yanked away by the next Calculate; an unvisited new page is marked `!` instead. And because the plot and Export CSV always show the *latest* numbers, every older page carries `! the plot and Export CSV show run #12, not this page`. Run history is in memory only; a config file carries the setup, never the results.

Use **Export CSV** to dump per-trace `Freq, Re(Z), Im(Z), |Z|, R, L, C, Q` tables. Each trace's header names the run it came from (`# Run: #12 @ 5.000 GHz, 14:32:07`).

**Saving the setup.** `File → Save Config...` (Ctrl+S) writes everything you typed — the loaded files, every trace's mode and port fields and tables, colour and style, the RLC frequency, the fit band and model, and the plot's checkbox row — to a few kB of readable JSON. `File → Load Config...` (Ctrl+O) brings it all back; press **Calculate All & Plot** and the numbers return. The file holds the *setup*, never the results: Export CSV remains the way to save those. Each file in it is recorded both relative to the config and absolutely, and loading tries the relative path first, so copying the whole folder to another machine (or to an offline one) just works; a file that has gone missing is named in the Results pane and the rest of the session still loads. The config is also written automatically on exit to `~/.pkg_rlc_extractor/last_session.json` — the Results pane says on startup what is in it, and `File → Restore Last Session` loads it on request rather than spending tens of seconds re-parsing package exports before you have asked for anything.

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
| `--force-nports` | Bypass content detection and force the port count                 |
| `--diagnose` | Print the file-structure report and exit (`0` = nothing wrong, `1` = something is). Needs no `--cli` |
| `--lenient` | Skip values that do not parse instead of refusing the file            |

`--mode coupling` additionally takes the `--attribute*` flags, which are listed in their own
`--help` group and are documented under
[Port attribution](#port-attribution-where-a-coupling-number-comes-from).

---

## Reading files

The port count comes from the **content**, never the extension. The file name is consulted for exactly two things, and says so in a warning when it is: breaking a tie when the numbers admit more than one port count (picking the smallest silently reads a 2-port file as a 1-port one), and rescuing a port count above the 256-port sniff cap — a `.s300p` package export, which content alone cannot resolve.

Read: Touchstone 1.x in RI / MA / DB, any frequency unit, with or without an option line; UTF-8, UTF-8 + BOM, and UTF-16 with or without BOM; commas/semicolons between values and Fortran `D` exponents (`1.0D+09`) are accepted with a warning. Touchstone **2.0** (`[Version]`, `[Network Data]`) and compressed files are refused **by name** rather than misread — read as v1, a v2 file's `[Number of Ports] 4` injects a `4` into the data stream and shifts every value after it.

Every load prints two kinds of extra line when they apply. `WARN:` means something was guessed or thrown away. `Note:` means the file is fine but there is something to know first — most usefully that the sweep starts at **DC**, where `L = Im(Z)/ω` and `C = -1/(ω·Im(Z))` are undefined and read as nan/inf.

A file that will not load produces a report naming a line, the text of that line, and a **verdict**: `THE FILE is inconsistent` (truncated/corrupt — everything before the named line was read correctly), `THE FILE looks valid but…` (a real format not read here), `THE FILE could not be read` (missing, locked, out of memory), or `THE PARSER gave up` — which means the file's structure checks out and this is a bug here, reported with a traceback to send. If the only problem is a stray non-numeric token the GUI offers to load it anyway; the result is suspect, because dropping one number in a positional stream shifts every number after it.

**Check File** (GUI) / `--diagnose` (CLI) runs the same structure report on demand: size, encoding, line counts, the option line, how many numbers each data line carries, and whether the data divides into whole records for each plausible port count. It exists for the case an error dialog cannot cover — the file *loads* but the numbers look wrong.

---

## Ports & Roles

**Show Ports**, at the top of the left panel, opens a modeless window that answers the one question a 153-port package export makes hard: *what is my spec doing with every ball?*

```
pkg.s153p — 153 ports · 4 probe · 54 ground · 1 element · 94 open
  #    Name           Role       From
  1    VSS_ball_1     ground     conn row 1
  ...
 52    VSS_ball_52    open       —                  <- flagged
 61    sig_in         probe +    probe row 1 (+)
```

* One row per port of the file, with the **name** the file carries (`! Port[12] = VDD_ball_2`), the **role** your spec gives it — `probe +` / `probe −` / `ground` / `vdd` / `element` / `shorted` / `open` — and the row or kept-as-text line that decided it. It works in every mode: modes 1/2/3 name the field (`GND / VDD`, `Port A`) rather than a table row.
* Filter by name, hide the open ports, click any heading to sort. Sorting is on the value, so port 10 sorts after port 9.
* Rows are flagged when they deserve a second look: an **open** port whose name matches a set you grounded or probed (this is the one that catches "I grounded 51 of the 54 ground balls"), a port claimed by both a probe row and a ground row — legal, and the ground row wins — and a port assigned by the *kept as text* block rather than by a table row.
* Select rows and press **Set as ground** or **Set as probe +**: the ports are written into the editor as a **collapsed range**, so a 54-ball ground group becomes one row (`6-14,20-59`) instead of 54. The write goes through the editor, so it applies as you type, marks the trace stale and shows up in the strips exactly like a keystroke.

The window follows what you type. The open-port check also appears on the validation strip under the tables, so it reaches you without opening anything.

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

Beside the modes there is one **post-processing layer**, which is not a mode and gets no code:

| Layer | Module | What it answers |
|-------|--------|-----------------|
| Port attribution | `pkg_rlc_attrib.py` | Of the `Z_ab` a mode just produced, how much is the bare EM coupling and how much is each termination you declared — and what the answer would be if any of them were different. Exact both ways. See [Port attribution](#port-attribution-where-a-coupling-number-comes-from). |

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

## Port attribution: where a coupling number comes from

The loop above assumes the extracted `M` is a property of the layout. It is not, entirely.
The same two coils out of the same EM solve, extracted twice, gave `|M| = 1.71 pH` and
`|M| = 3.44 pH` — **6.07 dB apart**, both runs correct. What differed was the grounding
assumption, and nothing on screen said so. `pkg_rlc_attrib.py` is the layer that says so.

It answers two questions about **one frequency of one spec**:

| | Question | Exactness |
|---|---|---|
| **Q2 — attribution** | Split `Z_ab` into the bare EM coupling plus one signed term per termination you declared. | Exact. The terms sum to the total by superposition — not a linearisation, not an estimate, not percentages apportioned by hand. |
| **Q1 — sensitivity** | What would `Z_ab` be if that ground ball were open? A 50 Ω resistor? A 1 nH lead? All of them at once? | Exact. It re-solves the network through a Woodbury update; it does not extrapolate. |

The algebra is one rank-`m` update, where `m` is the number of terminations you declared, so
a 60-ball ground field costs a 60x60 solve rather than another Schur reduction of the whole
file. The derivation, the prior art it rederives (Kron diakoptics, the adjoint variable
method, PEEC partial elements, transfer-path analysis) and the precise statement of which
quantities decompose are in [docs/theory.md §13](docs/theory.md).

### Running it — CLI

Add `--attribute VICTIM,AGGRESSOR` to any `--mode coupling` run. Both sides are measurement
port **names** as given to `--mport` (a plain integer is read as a 1-based position in that
list), and they must differ — `Z_ab` is a mutual impedance.

```bash
python pkg_rlc_extractor.py --cli tests/fixtures/diff_pair_4port.s4p --mode coupling \
    --mport "agg = 1" --mport "vic = 2" --gnd "3,4" --freq 5.0 \
    --attribute vic,agg
```

| Flag | Meaning |
|------|---------|
| `--attribute VICTIM,AGGRESSOR` | Turn the whole attribution report on. `--mode coupling` only; every flag below is inert without it and is refused by name if you pass it anyway. |
| `--attribute-alt SPEC` | A candidate termination for the sensitivity scan; **repeatable**. `open`, `ideal`, or a series R/L/C in the Mode 5 DSL's own spelling (`R=50`, `L=0.3n`, `R=0.5,L=1n`, `C=100p`). With none given the scan is limited to the two **structural** candidates, `open` and `ideal`, which need no judgement about your package — the tool will not guess your ball's lead inductance. Any finite candidate is also used as a victim load in the exact current-transfer ratio. |
| `--attribute-ground-model MODEL` | `diag` (default) = exactly as declared. `diag:SPEC` = every shunt lead gets `SPEC` as its own **independent** series impedance. `shared:SPEC` = every shunt lead keeps what it declares and they all **also** share `SPEC` back to the reference (a dense element-impedance matrix). See [below](#the-ground-field-independent-leads-understate-the-return-inductance) — this is worth 6-10 dB. |
| `--attribute-freqs LIST` | Extra frequencies in GHz to re-rank the contributions at, so a ranking read off one frequency can be checked for stability across the band. `--freq` is always the first column. |
| `--attribute-group row \| flat \| name` | How elements are grouped for the joint-effect section. `row` (default) groups by the flag that declared the port — this CLI's equivalent of the GUI's connection-table provenance. `flat` is one element per group. `name` groups by the file's port names with the trailing index stripped, which is a **naming heuristic**, not a fact about the network, and the report says so. |
| `--attribute-csv PATH` | Write every record — terms, the **uncapped** sensitivity scan, joint effects, the cumulative curve, the sweeps, the cross-frequency ranks — one row per record, tagged by a `section` column. The terminal caps some tables for readability; the CSV does not, which is what makes the `(see --attribute-csv)` pointers true. |

The report comes out in nine numbered sections: the sign convention, the decomposition, the
reconciliation against `compute_z_matrix`, the return-path budget, the ground-model
comparison, the sensitivity scan and leave-one-out, the joint effects, the cumulative curve,
the closed-form sweeps, and cross-frequency rank stability. Section 1 of the run above:

```
  group                element            |I_e|           Z term (Ω)  M term   share    quad
  -------------------  ----------------  ------  -------------------  ------  ------  ------
  --gnd 3,4            ground port 3        1 A  -6.041e-10 + j7.933  252 pH  25.00%  -0.00%
  --gnd 3,4            ground port 4     997 uA  -2.214e-09 + j15.91  506 pH  50.12%   0.00%
  (baseline: bare EM)  bare EM coupling      --   3.231e-10 + j7.894  251 pH  24.88%  -0.00%
```

Three quarters of that `M` is the grounding, not the metal: open both grounds and it falls to
the 251 pH bare term. The two balls are not worth the same, either — port 4 is the far end of
the **victim's** own line and is worth twice port 3, the far end of the aggressor's. There is
no GUI window yet (stage 4 of `docs/design_port_attribution.md`); the CLI report and the CSV
are the whole surface today.

### Running it — from Python

`pkg_rlc_attrib.py` imports `pkg_rlc_core` and nothing else, so it is usable directly against
any `TerminationSet` from any mode — `build_terminations_coupling`, `build_terminations_rows`
(the Mode 5 tables) or `parse_custom_termination_text` (the DSL). From the repo root:

```python
import numpy as np
from pkg_rlc_core import (parse_touchstone, s_to_y, parse_mport_spec,
                          build_terminations_coupling)
from pkg_rlc_attrib import build_context, decompose, format_decomposition

d = parse_touchstone("tests/fixtures/diff_pair_4port.s4p")
Y = s_to_y(d.s, d.z0)
terms = build_terminations_coupling(
    [parse_mport_spec("agg = 1"), parse_mport_spec("vic = 2")],
    gnd_ports=[3, 4], nports=d.nports)

ctx = build_context(Y, d.freqs, terms, freq_hz=5e9)
print("\n".join(format_decomposition(decompose(ctx, "vic", "agg", "M"))))
```

The API, all exact and all verified in `tests/test_attrib_core.py` and
`tests/test_attrib_vs_engine.py` against an honest recompute through `compute_z_matrix` with
a rebuilt `TerminationSet`:

| Call | Answers |
|------|---------|
| `decompose(ctx, victim, aggressor, quantity)` | the split above, for `Z` / `ReZ` / `ImZ` / `M` / `M/L_a` / `k` |
| `sensitivity(...)` | every element against every candidate termination |
| `group_joint(...)` | a whole connection-table **row** changed at once, plus the non-additivity against the sum of the individuals |
| `cumulative_curve(...)` | rank by single-element effect, then change the top `k = 1, 2, 4, 8, …` together |
| `leave_one_out(...)` | start from all-ideal and remove one at a time — usually more informative than one-at-a-time from all-open |
| `sweep_mobius(...)` | `Z_ab` versus one element's impedance in **closed form**: both endpoints, the whole interval and the extremum, with no loop |
| `transfer_ratio(...)` | the exact `-Z_ab/Z_aa` current-transfer ratio, and a loaded `-Z_ab/(Z_aa + Z_load)`, against the `M/L_a` Norton approximation |
| `termination_impedance_diagonal` / `_shared_return` | the two `Zt` topologies behind `--attribute-ground-model` |

### Sign convention, stated on every report

The victim reading is `V(+) − V(−)` of the victim measurement port; the aggressor is driven
`+1 A` into its `+` side and out of its `−` side, so every term is signed the way
`Z_ab = V_a / I_b` is. An element current `I_e > 0` flows **out of the structure into ground**
for a shunt element (`ground` / `vdd` / `lumped_to_gnd`) and **from the first port to the
second** for a series element (`short_to` / `lumped_between`). Flipping either measurement
port's `+/−` assignment flips every term together: **relative** signs between terms are
physical, absolute ones are a labelling choice.

### Three things it cannot do

- **It is blind to open ports.** An open port contributes no element and therefore no term —
  it is *absent* from the table, not small in it. The contribution table is a ranking of the
  **declarations in your spec**, never of ports. Only the sensitivity side reaches a port you
  have not decided about, and it reaches it by hypothesising a termination.
- **The split depends on how the spec is spelled.** `3 ground` + `4 ground` and
  `3 short_to 4` + `3 ground` are the same network and give the same total; measured on
  `diff_pair_4port.s4p` at 5 GHz they split as `bare 251 pH / gnd3 252 pH / gnd4 506 pH`
  and `bare 251 pH / gnd3 253 pH / short 3-4 506 pH`. Both are right — two descriptions of
  one network are two different tearings of it. Reorganise your table for readability and
  the contribution column can move; that is not a defect.
- **Most of the return current can be inside the EM model.** The reference plane is not a
  port, so no declaration of yours reaches it. Every report prints a return-path budget; on
  a representative package case it read **0.05 % declared / 99.95 % inside the model**, and
  when the model dominates the report says in plain words that the decomposition cannot
  separate the return path. Do not read a "forward path minus return path" conclusion out of
  small numbers in the table.

Also: re-terminating existing ports **cannot evaluate new metal**. A shield, an extra via, a
widened return path — none of those is a termination of a port that already exists. They
change `Y` itself and need a new EM run.

### What can and cannot be split per term

A quantity decomposes iff it is a fixed real scalar times an **R-linear** functional of
`Z_ab`, read at one configuration:

- **Yes:** `Z_ab`, `Re Z_ab`, `Im Z_ab`, `M = Im/ω`, `M/L_a`, `k`.
- **No:** `C_c = -1/(ω·Im Z_ab)` (a reciprocal — superposition adds impedances, not their
  inverses), `Q` (a ratio of two decomposable things), `|Z|` (a norm), anything in dB (a
  logarithm of a magnitude, and unsigned).

`C_c` stays a first-class **total** — it is the right reading whenever `Im(Z_ab) < 0` — it
just has no per-term split. Ask for one and the tool refuses **by name** and says which
linear quantity to decompose instead.

The `share` column is a signed **projection**, `Re(term · conj(total)) / |total|²`, with the
quadrature part reported separately: a term at 90° to the total inflates any magnitude-based
cancellation measure while being harmless. Where `|total|` is near zero the column is
suppressed outright, with a named reason — shares of a number that is pure cancellation mean
nothing.

### The ground field: independent leads understate the return inductance

This is the single most expensive modelling choice in the whole flow, and it is easy to make
by accident. A GND field written as *N* independent `lumped_to_gnd` inductors says the balls
have *N* independent return paths. Real package ground balls **share a return plane**. *N*
independent `z` in parallel is `z/N`; *N* balls sharing one `z` is `z` — so the independent
spelling understates the common-mode return inductance by roughly `(1 + (N−1)·k_ret)`.

Measured three times, on three different networks:

| Network | Independent vs shared |
|---|---|
| Synthetic 4-ball cluster (review) | **9.60 dB** |
| Independently constructed 6-node 4-ball cluster, 5 GHz (`docs/design_port_attribution.md` §5.2) | **8.09 dB** |
| `tests/fixtures/diff_pair_4port.s4p`, `agg = 1`, `vic = 2`, grounds 3 and 4, 5 GHz | **6.03 dB** |

All three are **larger than the 6.07 dB discrepancy this feature exists to settle**. The
effect is monotone in the return coupling with no threshold behaviour, so there is no safe
default and the tool refuses to pick one for you.

On the CLI it is one flag. Run the same command twice — the report gets a section 3b for
whichever model you asked for, measured against the spec as declared:

```
--attribute-ground-model diag:L=1n        --attribute-ground-model shared:L=1n
  M as declared        = 1.01 nH            M as declared          = 1.01 nH
  M under 'diag:L=1n'  = 1.012 nH           M under 'shared:L=1n'  = 2.026 nH
  difference           = 0.0173 dB          difference             = 6.05 dB
```

Each `difference` is against the **declared** ideal-ground spec; `diag` versus `shared` —
the comparison that matters — is the 6.03 dB in the table above. The report says so and tells
you to run it twice, because the two are not a refinement of each other.

Section 3b prints the **full per-element split under the model**, not just the total: under
`shared:L=1n` the 2.026 nH breaks down as 1.52 nH from ground 3, 253 pH from ground 4 and
251 pH of bare EM coupling, and those add up to the total above them. What the model does
*not* get is a second opinion — a dense element-impedance matrix **cannot be written as a
`TerminationSet`**, so `compute_z_matrix` has never been asked about that network, and the
report labels its number `compute_z_matrix, DECLARED spec — a DIFFERENT network` rather than
pretending the two are the same measurement. The reconciliation you see is of the **declared**
configuration through the same machinery, which is what checks the arithmetic the modelled
totals came out of.

**You can also spell the shared return in Mode 5, in the GUI, with no attribution code at
all.** Tie the whole ground set together with one `short_to` row, then hang **one**
`lumped_to_gnd` on any port of it:

```
# independent — N separate return paths
3 lumped_to_gnd L=1n
4 lumped_to_gnd L=1n          ->  M = 1.0120 nH

# SHARED — one return path for the whole set
3 short_to 4
3 lumped_to_gnd L=1n          ->  M = 2.0259 nH        6.03 dB apart
```

(`3 short_to 4` and `3 lumped_to_gnd` versus `3 short_to 4` and `4 lumped_to_gnd` give
bit-identical answers — the set is one node by then, so it does not matter which port of it
carries the inductor. In the connection table that is one `short` row with the whole ground
range in **To**, plus one `rlc_gnd` row.) Verified against `compute_z_matrix` with no
attribution code in the path, and separately against `pkg_rlc_attrib`'s dense
`termination_impedance_shared_return` builder, agreeing to `3.2e-13` relative.

Which spelling is right is a question about your package, not about this tool — but answer it
on purpose rather than by default.

---

## Important Notes

- **Results are TOTAL values, not per-unit-length.** `L`, `C`, `R` are reported for the network as seen between the chosen signal ports. To get per-unit-length values, divide by your known trace length yourself. The tool does not perform distributed (RLGC-per-length) extraction; that requires multi-section ABCD or `gamma`/`Z_0` extraction.
- **AC small-signal only.** `vdd` is an alias for `ground` because at AC the supply is an ideal short. The distinction exists in the UI for documentation clarity; Mode 4 was retired for the same reason.
- **Unlisted ports are OPEN, not grounded.** This is the most common source of wrong results. A forgotten GND ball floats and is Schur-eliminated, which preserves the behaviour at the kept ports but does *not* tie the port to the reference node.
- **The mutual `Z_ab` is the open-circuit one.** `Z[a][b]` is defined with every *other* measurement port carrying no current — that is the textbook definition of M, and the right primitive to hand to a simulator, where the real loading is modelled. It is not the same number as a short-circuit transfer measurement.
- **The termination spec is worth decibels, and there is a tool that says how many.** How you spell the ground field is not a detail: on `diff_pair_4port.s4p` three quarters of the extracted `M` comes from the two `ground` rows, and rewriting the same set as a shared return rather than independent leads moves `M` by 6.03 dB. [Port attribution](#port-attribution-where-a-coupling-number-comes-from) splits an extracted `Z_ab` into the bare EM coupling plus one signed term per declared termination, exactly, and answers the what-if exactly too. Read its three caveats before quoting it — in particular that it is **blind to ports you left open**, so its table ranks your declarations and never your ports.
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
  pkg_rlc_attrib.py          Port attribution: splits one Z_ab into the bare EM coupling
                             plus one term per declared termination, and answers the
                             exact what-if. Imports pkg_rlc_core only (acyclic)
  pkg_rlc_plot.py            Matplotlib plot panel with M / V / Delete / drag features
                             (R, L, C, |Z|, Re, Im, Q, k subplots)
  pkg_rlc_gui.py             Tkinter GUI with file/trace management, and the
                             JSON session format (Save / Load / Restore Config)
  pkg_rlc_help.py            In-app Help window (one tab per mode + syntax + examples)
  pkg_rlc_extractor.py       Entry point (GUI + CLI), incl. the --attribute report
  reduce_snp.py              Standalone CLI: shrink a big .sNp to a few ports
  deploy.sh                  Red-zone update entry point (top level by design)
  tests/
    test_core.py
    test_coupling.py         Mode 6: probe pairs, Z matrix, M / k / C_c / M-over-L
    test_attrib_core.py      Port attribution: reconciliation against compute_z_matrix,
                             and every fast what-if against an honest rebuild
    test_attrib_vs_engine.py   Independent cross-check over the golden case registry,
                             plus a 4000-spec fuzz with a two-sided contract
    test_attrib_degenerate.py  Singular baselines, redundant specs, resonant returns —
                             the failures that produce a plausible number, not an error
    test_golden_regression.py  Bit-exact replay of the pre-coupling behaviour
    test_port_parser.py
    test_content_sniffer.py
    test_reduce_snp.py
    test_session.py          Save / Load / Restore: round trip, refusals, paths
    generate_test_snp.py
    _golden_capture.py       Script (not a test) that (re)builds the golden .npz
  docs/
    theory.md                Math, circuit diagrams, mode derivations, attribution
    design_port_attribution.md  Why pkg_rlc_attrib.py is shaped the way it is
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

`pack.ps1` emits exactly two files — the tarball and its `.sha256`. Upload both;
that is the whole delivery. If you only need port reduction on a simulation
server, copy `reduce_snp.py` out of the package and onto that box: it imports
nothing from this repo and runs with nothing but `numpy`.

Full procedure, rollback, and how to keep your own data across deploys:
[deploy/README.md](deploy/README.md).

---

## Theory

For the math behind each mode (S->Y conversion, Schur complement, the unified termination
abstraction, the `+/-` probe model and the M / k / M-over-L derivations, the broadband
fitting models, and the superposition / Woodbury derivation behind port attribution) see
[docs/theory.md](docs/theory.md).

The in-app **Help** button opens the same material as a tabbed reference — one tab per mode,
plus input syntax and worked examples.
