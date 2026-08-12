# PKG RLC Extractor

A desktop tool for RF and analog IC engineers to extract R, L, C, and Q from Touchstone (`.sNp`) files — and, with more than one measurement port defined, the **mutual coupling** between them: M, k, the coupling ratio M/L, and the coupling capacitance C_c. It targets package parasitic characterization but applies equally to EMX-extracted layout traces, DCO / spiral inductors, decoupling capacitors, and any other passive structure for which an EM solver (EMX, HFSS, Q3D, etc.) has produced an S-parameter matrix. All extractions go through a unified Y-parameter Schur-complement reduction; the named "modes" in the UI are presets of a single underlying port-termination model.

The mental model for a measurement is a pair of multimeter probes: a **red probe** on the `+` ports and a **black probe** on the `-` ports. One such probe pair gives a self impedance; several of them alive at once give a G x G impedance matrix whose diagonal is the self impedances and whose off-diagonal is the open-circuit mutual impedance.

A separate layer, [port attribution](#port-attribution-where-a-coupling-number-comes-from), answers the question that follows: of the `M` you just extracted, how much is the metal and how much is the grounding you assumed? It splits one `Z_ab` into the bare EM coupling plus one signed term per termination you declared — exactly, by superposition — and tells you what the answer would be with any of those terminations changed. That layer has a window (**Analyze → Attribution…**) and a CLI report.

The same layer answers the question that comes *before* a spec exists, which on an unfamiliar 153-port export is the one you actually have first: **which of these ports matter at all?** The [cold-start screen](#cold-start-which-ports-matter-before-you-have-a-spec) brackets the whole question in one number, ranks every undeclared port by the exact effect of grounding it, scans pairs for the effects a one-at-a-time ranking is structurally blind to, and says how many ports it takes before the answer stops moving.

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
3. In **Edit Selected Trace**, pick a measurement mode (Modes 1-3, `+/- Ports / Coupling`, or Custom) and fill in the relevant port fields. Modes 5 and 6 are filled in as **tables** with a `+ Add` button rather than typed as text: Mode 6 gets the measurement-port table, Mode 5 that table plus a connections table underneath, with a port-overview line and a validation line under both. In the connections table the cells a row has **follow its Type** — a `ground` row is one wide Port field, a `short` row is one Port field for the whole tied group plus that node's **Net** name, and `rlc_between` is the only Type with two port fields. Every port cell takes port **numbers** or a Net name; **Show Ports** opens the **Ports & Roles** window, which is where to look on an unfamiliar package — see [Ports & Roles](#ports--roles). R/L/C cells take one word with an SI suffix and no unit (`5m`, `0.5n`, `1u`) — the unit is in the column header, and a value with a space in it is rejected rather than silently truncated. The **box at the start of a row switches it off**: the row keeps its values and contributes nothing, exactly as if deleted, which is how you ask what one connection is worth without retyping it (and it is *not* the same as Type `open`, which is a declaration rather than an absence). The validation line says how many rows are off and names them. The **verdict line in the editor's footer is clickable**: it scrolls the form to the row it is talking about and puts the caret in it, which is the only route to messages that otherwise sit a few hundred pixels below the fold.
4. Pick the curve's **Style** — click the line preview to open a palette of the 12 colours and 4 line styles, drawn as they will be drawn on the plot. On a coupling trace the preview also shows the run of colours the trace's expanded curves will occupy, and `×n` for how many.
5. Set **RLC Freq (GHz)** for single-point extraction, optionally enter a **Band Fit** range and model.
6. Click **Calculate All & Plot**. Results appear in the right pane and overlay on the multi-subplot view. **Calculate This Trace**, in the editor's footer, recomputes only the selected trace — the fast path when you are iterating on one port spec with several traces loaded.

**Edits apply as you type.** There is no *Apply* step: whatever is in the editor is what the selected trace holds, and the Traces list updates live. A trace whose spec has changed since it was last computed carries a trailing `*` in that list.

**Showing and hiding curves.** Every trace has a `☑` / `☐` in the Traces list. Toggle it with the **Show/Hide** button, with the space bar on the list, or with **Plot: this trace** in the editor — a hidden trace comes off the plot immediately, without recomputing anything and without disturbing the `V` cursors you have placed. The checkbox governs every output, not just the picture: a hidden trace comes off the results table and out of the CSV export too — the table says what is on the plot, and a row for a curve that is not drawn reads as a duplicate of the one that is. It is still measured: one line under the table names it, and its numbers stay in memory, so showing it again costs no Calculate (tick it back on and export again to get it into a file). This is the way to compare two of five traces without deleting the other three.

**Before and after: freezing a trace.** Right-click a trace in the Traces list → **Freeze as new trace**. That takes a snapshot: a second trace holding exactly the numbers this one has now, in the next colour and line style, labelled with the time (`tank <14:32>`). Change the original and press Calculate — the snapshot does not move, because Calculate skips it and the editor refuses to write into it (selecting one greys the editor out and says so; the list marks it `❄`). So the two curves are genuinely the before and the after, compared over the whole sweep instead of at one marker frequency, and both are in the results table, the cursor readout and the CSV. Right-click → **Unfreeze** gives it back to Calculate, which then *replaces* the numbers it was holding. One deliberate limit: a config file carries the setup and never the results, so a frozen trace comes back from Save/Load with its spec and no numbers — it says so in the Results pane and reads `❄ no numbers` in the list rather than quietly drawing nothing; unfreeze and Calculate reproduces the snapshot exactly if the file has not changed.

**Run history.** The Results pane is a set of tabs. `Log` is the running commentary it has always been; every Calculate adds a page beside it, newest first, labelled `#7 10:42`, holding that run's report under a heading that says which run it is, at what marker frequency, over which traces — and, on the second line, **what you changed since the previous run** (`changed since #11:  [3] gnd 6-14 -> 6-16`). That line is the useful one: twenty runs are all at 5 GHz and nobody remembers what they were doing at 14:32. Old pages are dropped automatically, oldest first, three at a time by default; press **Keep** (or right-click the tab) and that page is never dropped by anything automatic — only by right-click → **Close this run**. The kept pages have their own budget, which is why Calculate can never be blocked by them and can never throw one away: at the cap the Keep button is already disabled and says `Keep (5/5) — close a kept run first`. `Runs ▾` lists every page with its full description, which is where to look once the tabs are too narrow to read, and it is also where the two limits are set. Switching to the new page is **conditional** — it happens only if you were already reading the newest page (or the Log), so a page you deliberately kept open is not yanked away by the next Calculate; an unvisited new page is marked `!` instead. And because the plot and Export CSV always show the *latest* numbers, every older page carries `! the plot and Export CSV show run #12, not this page`. Run history is in memory only; a config file carries the setup, never the results.

**Three ways to read one run.** The `View` dropdown above the Results pane picks the shape of the report; nothing is recomputed and no number changes, and every open run page is repainted with it.

- `detail` — everything, one block per trace. The default, and what the tool has always printed.
- `summary` — the whole run as two tables: one row per measurement port, then one row per coupling pair, ranked exactly as the detail view ranks them. Comparing traces becomes reading down a column instead of paging between blocks.
- `compare` — the traces become **columns**, one quantity per row, with a change column when there are exactly two of them. This is the view for "what did this EM revision actually move":

  ```
  compare @ 5.55 GHz         [1] before      [4] after             Δ
  VCO      R                     9.92 Ω         9.81 Ω       -1.13 %
           L                    3.23 nH        3.23 nH      +0.089 %
  RX       R                     4.83 Ω         10.6 Ω        +119 %
  VCO x RX M                    -516 fH       -7.19 pH      +13.93 ×
           worst M/L           -68.77 dB      -52.36 dB     +16.41 dB
  ```

  A change past a factor of ten is printed as a **factor** rather than a percentage (−1293 % is not a sentence anybody says out loud), and a dB quantity gets a dB **difference**, because dB is already a ratio. A trace that does not have a given port or pair leaves an **empty** cell — "this trace has no RX" and "RX measured zero" are different statements. One trace on the plot cannot be compared, so it says so and falls back to the summary; three or more get no Δ column, because a change against "whichever trace happened to be first" is a reference chosen in silence.

The view is saved with the config, like the units mode.

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

`--mode coupling` additionally takes two flag families, each in its own `--help` group and each
inert (and refused by name) without its lead flag:

| Family | Lead flag | What it answers |
|---|---|---|
| [Attribution](#running-it--cli) | `--attribute VICTIM,AGGRESSOR` | Of the `Z_ab` this spec produced, how much came from each termination you declared — and what it would be if any of them were different. |
| [Cold start](#cold-start-which-ports-matter-before-you-have-a-spec) | `--cold-start VICTIM,AGGRESSOR` | Which ports the spec should have mentioned at all. Starts from **all-open** and sets your `--gnd` / `--short` aside, naming every one it set aside. |

They may be given together; the attribution prints first, because it explains the `M` printed
immediately above it.

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
| 5    | `Custom (advanced)`     | Two tables: the measurement-port table (Name / `+` / `−`) plus a connections table whose **cells follow the row's Type** — `ground / vdd / open` take one port field and nothing else, `short` takes one port field for the whole tied group plus that node's **Net** name, `rlc_gnd` is one port field + R/L/C, and `rlc_between` is the only Type with two port fields. Every port field takes the full range syntax (`6-14`, `35:1:45`) or a Net name, and every row has an **on/off box** that takes it out of the spec without deleting it. **Edit as text…** shows and takes back the equivalent DSL — `open / ground / vdd / signal <name> [+ or -] / short [as <name>] / short_to / lumped_to_gnd / lumped_between`, one directive per line — which is what the tables serialise to and what is actually computed. |
| 6    | `+/- Ports / Coupling (M, k)` | Any number of measurement ports, each a `+` / `-` probe pair, entered as table rows. Produces the G x G impedance matrix: self impedance on the diagonal, **open-circuit mutual impedance** off it, and from that M, k, C_c and the M/L ratios. |

Defining more than one measurement port gives you the coupling matrix **in either mode** —
Mode 5 used to report only the first one and warn about the rest, which was a wrong number
with no visible difference. The full matrix is now produced whenever the spec defines two
or more measurement ports, whichever mode wrote it.

Mode codes are stable and are never renumbered, so saved configurations keep working.

Beside the modes there is one **post-processing layer**, which is not a mode and gets no code:

| Layer | Module | Surface | What it answers |
|-------|--------|---------|-----------------|
| Port attribution | `pkg_rlc_attrib.py` | **Analyze → Attribution…** (`pkg_rlc_attrib_gui.py`), or `--attribute` | Of the `Z_ab` a mode just produced, how much is the bare EM coupling and how much is each termination you declared — and what the answer would be if any of them were different. Exact both ways. See [Port attribution](#port-attribution-where-a-coupling-number-comes-from). |
| Cold-start port screen | `pkg_rlc_attrib.py` | `--cold-start` (CLI only) | Which ports matter *before* a spec exists. A bracket, a two-column ranking of every undeclared port, a pair scan, and a greedy cumulative curve — all from **all-open**, all exact. See [Cold start](#cold-start-which-ports-matter-before-you-have-a-spec). |

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

It answers three questions about **one frequency of one spec**. They are numbered in the order
they were built, not the order you ask them — on an unfamiliar file **Q0 is first**:

| | Question | Exactness | Where |
|---|---|---|---|
| **Q0 — cold start** | No spec written yet. Which of the file's other 149 ports matter at all? | Exact. A closed form verified against a full re-solve to `1.5e-11`, not a first-order slope. | CLI (`--cold-start`) |
| **Q2 — attribution** | Split `Z_ab` into the bare EM coupling plus one signed term per termination you declared. | Exact. The terms sum to the total by superposition — not a linearisation, not an estimate, not percentages apportioned by hand. | Window + CLI |
| **Q1 — sensitivity** | What would `Z_ab` be if that ground ball were open? A 50 Ω resistor? A 1 nH lead? All of them at once? | Exact. It re-solves the network through a Woodbury update; it does not extrapolate. | Window + CLI |

The algebra is one rank-`m` update, where `m` is the number of terminations you declared, so
a 60-ball ground field costs a 60x60 solve rather than another Schur reduction of the whole
file. The derivation, the prior art it rederives (Kron diakoptics, the adjoint variable
method, PEEC partial elements, transfer-path analysis), the cold-start closed form and the
precise statement of which quantities decompose are in
[docs/theory.md §13](docs/theory.md).

### Running it — the Attribution window

**Analyze → Attribution…**, or right-click the trace in the Traces list. It decomposes the
**selected** trace, so Calculate it first. The window is modeless: it stays open while you
edit, it keeps its own taskbar button and Alt-Tab entry, and several can be open at once on
different traces or different pairs. Asking again for a pair that is already open **raises
that window** rather than making a second copy of the same decomposition.

It refuses, **by name**, on a trace it cannot honestly describe — a frozen snapshot (its
numbers came from an earlier run and can never be recalculated), a stale trace (edited since
the last Calculate, so the numbers and the spec beside them no longer describe each other), a
trace with no numbers or no loaded file, and a trace with only one measurement port (`Z_ab` is
a *mutual* impedance; there has to be a victim **and** an aggressor). The menu entry stays live
in all four cases on purpose: a greyed entry cannot say why.

| Part | What it is |
|---|---|
| Header | trace / victim / aggressor / quantity / frequency / **[Recompute]**. It wraps onto a second row at narrow widths rather than pushing the button off the end. |
| Banner | provenance — `from run #7 @ 5.600 GHz`. It turns into a warning the moment you edit the spec in the main window. |
| Sign strip | the convention, once, above any signed number: a negative term **opposes** the total and the terms sum to it exactly; shares are of the **signed** total, so they exceed 100 % and go negative wherever terms cancel. |
| Reconciliation | the cross-check against `compute_z_matrix`, in the **header** and not the footer because it gates trust in everything under it: `reconciled  rel diff 3.1e-13 (floor 4.3e-10)`. If it fails, the per-element **split** is withheld and the **total** is still shown. |
| Across-frequency badge | one line whose **off** state carries the action: a ranking read off one frequency is a statement about that frequency, so the badge says what checking would **cost on this file** and does it in one click. Checked, it names what **moved** — which elements changed rank, at which frequency — or says the ranking is **stable** across the band, which is a result and not an absence. It is a one-shot: the verdict is the answer, so the button greys out until the next **[Recompute]**. |
| Ground model | `diag` (as declared) / `diag:SPEC` (each shunt lead on its own **independent** lead) / `shared:SPEC` (every lead keeps what it declares and they **also** share `SPEC` back to the reference) — the same spelling the CLI takes, with one line beside it saying why the default is not obviously right. It takes effect on **[Recompute]** like every other input. If the spec declares no shunt lead to model at all (every ground written as `short_to`, say) the model **cannot** be applied — the strip reads `NOT APPLIED` and the line beside the field says why, rather than leaving an unchanged number to be read as "the shared return is worth 0 dB". See [the ground field](#the-ground-field-independent-leads-understate-the-return-inductance). |
| Table | `(•) Contributions  ( ) Sensitivity` — one pane, two views. Rows are coloured by element **kind**, in the Ports & Roles palette; never by sign, because red means *warning* everywhere else in this tool and a red negative would make a correct answer look like a fault. Click a row to drill in. |
| Detail pane | for the selected row: its element current, its transimpedance to the victim, what it would be worth as each candidate you list (`open`, `ideal`, or `R=`/`L=`/`C=`), and the **closed-form** sweep of that one element plotted beside it with both asymptotes, the current spec marked and any **pole labelled** ([how to read it](#reading-the-sweep-plot-and-its-pole)). It is prose and it **wraps**; it never scrolls sideways, and the split above it opens sized to the table's own row count. Drag the sash and it stays where you put it. |
| Footer | Copy report / Export CSV… / Close. Both carry the full provenance: run number, the frequency and whether it was snapped to the file's grid, the whole sign convention, the ground model, and the termination spec verbatim. |

**Why there is a [Recompute] button and no auto-refresh.** A trace's numbers are written by
Calculate and by nothing else; editing the spec marks the trace stale and leaves them at the
*previous* run's value. A window that re-decomposed on every keystroke would be checking a
**new** spec against an **old** total, would find them disagreeing by however much you just
typed, and — by the reconciliation rule above — would blank its own table. It would erase
itself while you type. So an edit moves the banner and nothing else, which is what makes the
button honest.

**The ground model is on the window**, in the CLI's own spelling so the two cannot drift, and
changing it goes through `[Recompute]` like every other input. One thing about it is unlike
everything else here: the dense (`shared`) model is a network `compute_z_matrix` **cannot be
handed** — a shared return is a mutual impedance *between* ground leads and the connection
table has no node to hang one on — so there is no second opinion on its total. What is still
checked is the **arithmetic**: the reconciliation line is always of the spec **as declared**
through the same machinery, so choosing a model does not quietly cost you the cross-check.
The sign strip and both exports name the model the numbers came out of rather than letting you
assume one.

#### Reading the sweep plot, and its pole

Clicking a row sweeps **that** element's series inductance from ideal (`L = 0`) to open
(`L = ∞`), in closed form. Two numbers on it are the ones you came for — `M(0)`, the
termination made ideal, and `M(∞)`, the termination not there at all — and the plot's **y
limits are set from them** plus a margin, so the two readings you are comparing are always on
screen at a readable size.

Between them the curve may have **one pole**, and on a package it usually does. A pole is not a
numerical artefact and it is not hidden: it is drawn as a **labelled vertical line** at the `L`
where it sits, with the element's value there, because it is a physical event — the `L` you are
adding **resonates with the reactance the network itself presents at that node**. Measured on
`diff_pair_4port.s4p` at 5 GHz with grounds on 3 and 4, sweeping `ground port 3`:

```
M(0)   ideal                1.01 nH        <- the two numbers you came for
M(inf) open                503.7 pH
pole at L = 505.25 nH   <- the network looks like 2.005 fF at that ball, and 505.25 nH
                           series-resonates with 2.005 fF at 5.0005 GHz — the frequency
                           being read, to five digits
over all L >= 0        [-10.28 mH, +10.28 mH]     <- the pole, i.e. arithmetic
away from the pole     [-2.5 pH,   1.52 nH]       <- the answer
```

So the **headline interval is the pole-free one** and the pole is stated separately, in words.
`M lies in [−2.5 pH, 1.52 nH] over any ground inductance more than a factor of two away from
the 505 nH resonance` is a sentence a budget can be written against; `M lies in
[−10.3 mH, +10.3 mH]` is the tool reading its own arithmetic back to you — same curve, same
numbers, ten million times the endpoint bracket, and reached only within femtohenries of a
505 nH inductor nobody is putting on a ground ball. Note the pole-free range is **still wider
than the two endpoints**, and stays wider well away from the resonance (at a factor-of-**ten**
guard band it reads `[447.5 pH, 1.066 nH]`, outside `[503.7 pH, 1.01 nH]` at both ends) — a
series `L` resonates with the structure's shunt `C`, so `[ideal, open]` is not a bound.

The y axis is **symlog** (linear in a band around zero, logarithmic outside it) because `M`
crosses zero here and a log axis cannot draw that. The linear band's width comes from the
**data**, not from a constant: the main plot panel's `linthresh = 1e-6` is right for R/L/C and
useless here — 1 µH is a thousand times the whole curve above, so every point would land inside
the linear band and symlog would degenerate into the linear axis it exists to replace. Both
axes are labelled in **engineering units** (`500 pH`, not `5.0` under a `1e-10` in the corner),
the same `format_si` the table, the caption and the main plot's cursor readout use. With no
pole in range, none of this appears and the plot is what it always was.

A sweep that does not move at all — every residue zero, so `M(0) == M(∞)` — gets an axis sized
to **its own value**, not to a fixed span: it happens on real files (`decap_4port.s4p` reads a
flat −506.755 nH), and an axis a hundred thousand times the number on it is the same
uninformative picture from the other direction.

**Save Config remembers which pair you were reading** — victim, aggressor, quantity, frequency,
the view and the Candidates field — but does not reopen the window on Load. A config carries the setup and never the
results, so a just-loaded trace has no numbers and the window could only open on its own
refusal; the Results pane names each entry it did not reopen. Calculate, then
**Analyze → Attribution…**, and you land back where you were.

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
the **victim's** own line and is worth twice port 3, the far end of the aggressor's.

### Cold start: which ports matter, before you have a spec

Everything above ranks **declarations**. At the start of a job there are none — you know the
victim and the aggressor and nothing about the other 149 ports, and the all-open
configuration (the one `compute_z_matrix` returns, and the one that produced the disputed
number) contains no declarations at all, so the contribution table is empty by construction.
The cold-start screen answers that question instead, in four steps, each exact and each
measured **from all-open**.

```bash
python pkg_rlc_extractor.py --cli <file> --mode coupling \
    --mport "dco = 1" --mport "rx = 2" --freq 5.0 \
    --cold-start dco,rx
```

Note there is no `--gnd` and no `--short` in that command: `--cold-start` deliberately sets
your declarations aside and starts from all-open, and the report names every one it set aside.

**Step 0 — the bracket.** The quantity with every non-probe port open, against the same
quantity with every one of them at ideal ground, and the dB between. It is first because it
decides whether the other three steps are worth reading: **25.67 dB** on a planted 12-port
case is a real argument; 0 dB means nothing else in the file touches your two coils and you
can stop. It brackets the **open..ideal-ground family and nothing else** — a series ground
inductance resonates with the structure's shunt C and can put the answer outside it (measured
on `diff_pair_4port.s4p`: a peak of 9 mH of apparent `M` at `L = 505 nH` against a 1.01 nH
open..ideal bracket). The report prints that caveat with the numbers, every time.

**Step 1 — the two-column screen.** Every port that is not part of a measurement port, with

| Column | Meaning |
|---|---|
| `\|Z_ap\|` | how strongly the port talks to the **victim** |
| `\|Z_pb\|` | how strongly the **aggressor** talks to the port |
| `Δ` | the exact effect of grounding it — `dZ_ab = −Zbase[a,p]·Zbase[p,b]/Zbase[p,p]`, verified against a full re-solve to `1.5e-11` |

ranked by `|Δ|`. **The two coupling columns are separate on purpose and must never be read as
their product.** Measured on the planted case, the port with the *largest* `|Z_ap|` in the
whole file (34.777 Ω, 67 % more than the real path's 20.873) has `|Z_pb| = 0.038` and moves
the answer by **−0.378 pH**, against **−395.369 pH** for the real one. Ranked on coupling to
the victim alone that port comes **first** and is worthless; ranked on the effect it is fifth
of eight. The negative result is a result too: the list ends with *"the other N ports are all
below X dB"*, which is what lets you call the coupling local and stop looking.

**Step 2 — the pair scan** over the top `K` of step 1, again from all-open, plus the **mirror**
direction (start from every candidate grounded, open one at a time). This is not optional.
Measured: a shield brought out as two ports reads **+9.689 pH** with either end grounded alone
and **−870.268 pH** with both — **90×** the largest single-port effect in the file, with the
**opposite sign**. A one-at-a-time ranking reports it as two minor positive entries. The
mechanism is the closed **loop**, not the grounding: `5 short_to 6` with no ground anywhere
gives the identical −870.268 pH. The mirror catches the opposite failure — sixty ground balls
read ~0 each from all-grounded because the other fifty-nine carry the return, while that same
shield reads +880 pH per end.

**Step 3 — the greedy cumulative curve.** Ground the best port, **re-rank**, ground the next
best, tabulating the answer against `k`. Neither a ranking nor a pair scan says *how many*
ports matter; this does, and the report names the `k` at which the curve saturates and the
tolerance it used for the word. Greedy is not optimal — the best-`k` subset is combinatorial —
but the re-ranking is what lets the walk find the pair effects of step 2.

| Flag | Meaning |
|------|---------|
| `--cold-start VICTIM,AGGRESSOR` | Turn the four-step screen on. Both sides are named exactly as `--attribute` names them: a measurement-port **name** from `--mport`, or a 1-based position in that list. |
| `--cold-start-top K` | How many ports of the step-1 ranking enter the pair scan (default `8`, i.e. 28 pairs). Refused below 2 — the pair scan is not optional. |
| `--cold-start-cumulative K` | Depth of the greedy curve (default `12`; it is always run). `0` means *every* candidate, which is the one expensive setting here: **54.9 s** at 151 candidates against **132 ms** at `K = 12`. |
| `--cold-start-csv PATH` | Every record — the bracket, the **uncapped** screen (with the complex coupling columns the report shows as magnitudes), every scanned pair whether flagged or not, the whole mirror, the curve, and the name-family suggestions — one row each, tagged by a `section` column. |

#### Port names are a proposal the tool tests, never an assumption it folds in

Grouping ports by name family *would* have caught the shield, because the two ends of a guard
ring normally share a prefix. But which ports are one structure is a semantic judgement about
the layout, and the tool does not guess it. So the numbers are computed both ways and the
grouping stays a sentence you accept or reject:

```
ports 5,6 share the name family 'guard_ring'; tested together they are -870 pH,
tested separately +9.7 pH each -- if they are one structure, group them
```

Nothing in the bracket, the ranking, the pair scan or the curve changes according to whether
the file carries port names at all.

#### What the cold-start screen cannot find

**Anything that needs three or more ports to move together.** Step 1 is one port at a time,
step 2 is exactly two, and step 3 can stumble onto a triple but has no guarantee. A
three-terminal version of the shield above is invisible to every step. And, as everywhere in
this layer, it **cannot evaluate new metal**: every port it considers is one the S-parameter
file already has.

#### Cost

Measured on a 153-port package export at one frequency, 151 candidates:

| | |
|---|---|
| the four steps together | **9.5 s** — of which **9.3 s** is the mirror direction |
| the same four steps at 38 candidates | **17.6 ms** |
| `build_context` (the only `O(N³)` piece, built once and shared) | 350.6 ms |
| step 1, the whole ranking | **2.41 ms**, against **2402.6 ms** for one full re-solve per candidate — a factor of **997** |
| step 2, the pair scan | 0.44 ms at `K = 8`, 3.4 ms at `K = 20`, 8.2 ms at `K = 30` |
| step 3, the curve | 132 ms at `k = 12`, 237 ms at `k = 24`, **54.9 s** at `k = 0` (every candidate) |

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

The cold-start screen is the same module, and every step takes an optional `context=` so the
one `O(N³)` factorisation is shared — `cold_start_report` does that for you and
`format_cold_start` renders it:

| Call | Answers |
|------|---------|
| `cold_start_context(Y, freqs, terminations, freq_hz, …)` | the shared context: the probes plus one ideal ground per candidate port |
| `cold_start_bracket(...)` → `Bracket` | step 0 — all-open versus all-grounded, the dB between, and the caveat verbatim |
| `cold_start_screen(...)` → `list[PortScreenRow]` | step 1 — every candidate, ranked by `\|Δ\|`, with **both** coupling columns kept separate |
| `cold_start_pairs(..., top_k)` → `list[PairEffect]` | step 2 — every scanned pair, its non-additivity, the threshold it was judged against, and a `sign_flip` marker |
| `cold_start_leave_one_out(...)` | the mirror direction, from all-grounded |
| `cold_start_cumulative(..., max_k)` → `CumulativeCurve` | step 3 — the greedy curve, with `saturation_k` and the tolerance behind the word |
| `name_family_suggestions(...)` → `list[FamilySuggestion]` | the tested proposal, never an assumption |
| `cold_start_negative_result(rows, unit)` | *"the other N ports are all below X dB"*, as a sentence |
| `cold_start_report(...)` / `format_cold_start(cs)` | all of the above with one shared context, and its rendering |

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
by accident, because the obvious spelling is the wrong one. A GND field written as *N*
independent `lumped_to_gnd` inductors says the balls have *N* independent return paths. Real
package ground balls **share a return plane**. *N* independent `z` in parallel is `z/N`; *N*
balls sharing one `z` is `z` — so the independent spelling understates the common-mode return
inductance by roughly `(1 + (N−1)·k_ret)`, where `k_ret` is how strongly the return paths
couple to each other.

That factor is why "independent" is not a conservative default: at 20 balls with a realistic
`k_ret = 0.2` it is `4.8x`, i.e. **13.6 dB** of `M`, and it **grows with the ball count** — the
bigger and better your ground field, the more the independent spelling flatters it.

Measured three times, on three different networks:

| Network | Independent vs shared |
|---|---|
| Synthetic 4-ball cluster — four leads at 1 nH each independently, against the same four tied through **one** shared 1 nH | **9.60 dB** |
| Independently constructed 6-node 4-ball cluster, 5 GHz (`docs/design_port_attribution.md` §5.2) | **8.09 dB** |
| `tests/fixtures/diff_pair_4port.s4p`, `agg = 1`, `vic = 2`, grounds 3 and 4, 5 GHz | **6.03 dB** |

All three are **larger than the 6.07 dB discrepancy this feature exists to settle**. The
effect is monotone in the return coupling with no threshold behaviour, so there is no safe
default and the tool refuses to pick one for you.

**In the Attribution window** it is the `Ground model` field, in the same `diag` / `diag:SPEC`
/ `shared:SPEC` spelling as the flag below so the two cannot drift; it takes effect on
`[Recompute]`, and the sign strip states which model is in force. On the CLI it is one flag.
Either way, run it **both** ways — `diag` and `shared` are not a refinement of each other, they
are different answers. The report gets a section 3b for whichever model you asked for, measured
against the spec as declared:

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
all.** Tie the whole ground set together with one `short` row, name that node, then hang
**one** `lumped_to_gnd` on the node:

```
# independent — N separate return paths
3 lumped_to_gnd L=1n
4 lumped_to_gnd L=1n          ->  M = 1.0120 nH

# SHARED — one return path for the whole set
3,4      short  as pkg_gnd
pkg_gnd  lumped_to_gnd L=1n   ->  M = 2.0259 nH        6.03 dB apart
```

(The name is convenience, not capability: it stands for one member port, so
`3 lumped_to_gnd L=1n` after the short — and the older two-field spelling `3 short_to 4` —
give bit-identical answers. The set is one node by then, so it does not matter which port of
it carries the inductor. In the connection table that is one `short` row with the whole
ground range in its single **Port** cell and the name in **Net**, plus one `rlc_gnd` row.)
Verified against `compute_z_matrix` with no attribution code in the path, and separately
against `pkg_rlc_attrib`'s dense `termination_impedance_shared_return` builder, agreeing to
`3.2e-13` relative.

What you must **not** write is `3,4 lumped_to_gnd L=1n` after the short. Once those ports are
one node that is two 1 nH leads in **parallel** on it — a 500 pH shared return, which is
neither of the two models above, and it looks exactly like the line that would have been
right without the short. Measured on the probe network: `1 nH` reads back as `498.9 pH`.
The validation strip refuses it by name and prints both numbers
(`⚠ ports 3-4 are ALREADY ONE NODE … L 1 nH becomes 500 pH`); without a short those same two
rows are two real, independent ball inductances and nothing is said.

Which spelling is right is a question about your package, not about this tool — but answer it
on purpose rather than by default.

---

## Several files as ONE network (`--compose`)

Your EM block and your package are two `.sNp` files. `--compose` hangs one on the other and
measures the assembled thing: the blocks are stacked into one `Y`, every cross-file wire is an
ordinary short or lumped element, and the result goes through **the same `compute_z_matrix`**
as everything else — so every mode, the Mode 5 DSL, the coupling path, the attribution and the
cold-start screen all work on a composition with no special case of their own.

```bash
python pkg_rlc_extractor.py --cli coil.s2p --compose-alias EM \
    --compose "PKG=package.s3p" \
    --compose-link "EM.2 short_to PKG.1" \
    --compose-link "EM.1 lumped_between PKG.3 L=0.3n" \
    --gnd "PKG.2" --mode gnd --porta "EM.1" --freq 5
```

Every port carries its file's tag. **The separator is a dot, never a colon** — `:` is already
`start:step:stop` in every port field here, and `parse_port_range("PKG:12")` raises. Ports with
no tag default to the positional file, so a one-file command line still reads exactly as it did.

### The reference-node check, and why it is not optional

An n-port Touchstone `Y` is the matrix with its **own reference already eliminated**. Stacking
two of them therefore does not put two networks side by side — it welds file A's reference to
file B's at zero impedance. If your EM file's return current uses its own reference (the
ordinary on-die convention), the package's entire ground network is then **not in the circuit**,
and nothing about the answer looks wrong.

Measured on a 2 nH coil + 100 pH package trace + 100 pH package ground lead:

| die return | package ground pad | `L_eff` |
|---|---|---|
| brought out as a **port**, tied to the pad | — | **2.2501 nH**, and it moves when the ground path changes |
| **is** the EM reference | grounded | 2.1454 nH |
| **is** the EM reference | open | 2.1454 nH |
| **is** the EM reference | through 1 nH | 2.1454 nH |

Those last three are **bit-identical**, spread `0.000e+00`. So after composing, the tool
perturbs each file's declared ground set with a series inductor and re-solves; a delta of
*exactly* zero means welded, and it says so by name. This runs whether you ask for it or not.

**The composition only answers your question when the EM file brings its return path out as a
port.** That is a precondition, not a warning.

### Frequency grids

The span is intersected and **extrapolation is refused**; the report says how many points were
dropped. `S` is interpolated (not `Y`, not `Z`: for a passive network `S` is bounded at every
real frequency, while `Y` blows up at a series resonance and `Z` at a parallel one). An
already-identical grid is detected with a *relative* tolerance — a file written in GHz and one
written in Hz describing the same sweep differ by `2.2e-16` and never compare equal as floats —
and skipped. `z0` is **not** renormalised because it does not need to be: `max |Y(z0=50) −
Y(z0=75)| = 1.049e-17`.

What interpolation does break is **phase**. Across one step `Δφ = 2π·Δf·τ`, and the chord error
`1 − cos(Δφ/2)` shows up as fake insertion loss that corrupts `R` and `Q`. A 1 ns delay at a
100 MHz step is 36° → **0.436 dB** of invented loss (warned); 2 ns is 72° → **1.841 dB**
(refused). The coarser file's largest step is reported as the effective resolution — resampling
onto a finer grid recovers nothing.

### Big packages: pre-reduce

`--compose-keep PKG.10-12,40-42 --compose-gnd PKG.100:1:153` shrinks the package to the ports
your spec actually uses before stacking (its ground balls go in `--compose-gnd`, **not** `--gnd`
— they are shorted to that file's own reference before the stack). Measured on this box, a
16-port die + 120-port package at 201 frequencies: the solve goes **3113 ms → 14.4 ms (216×)**,
answers agreeing to `7.4e-16`. The reduction itself costs 2.5 s, so one end-to-end run is only
1.09× faster — **the 216× is the edit/recompute loop.** `--compose-export combined.s22p` writes
the reduced network out so you can load the small one next time.

### Which package pin costs you the dB

`--attribute` and `--cold-start` work on a composition, and there is one thing to know about
them: **the cross-file links go into the attribution baseline.** The all-open baseline those
reports normally use would leave the two files as disconnected islands — measured on a 12-port
combined network, every package-only element's contribution comes out **exactly 0** while the
reconciliation residual reads `6.49e-15`, i.e. perfect health. A confident, exactly-zero,
perfectly-reconciled wrong answer. So the baseline for a composed network is *"the files
connected, everything else open"*, the report names that gauge on its header, and there is no
flag to turn it off. Two attribution reports are comparable only when their baselines match.

```bash
python pkg_rlc_extractor.py --cli coil.s2p --compose "PKG=package.s3p" \
    --compose-link "F1.2 short_to PKG.1" --mode coupling \
    --mport "vic = F1.1" --mport "agg = PKG.2" --freq 5 \
    --attribute vic,agg --cold-start vic,agg
```

### Port correspondence

The mapping is **yours**; the tool may propose and only you may commit. `--compose-propose
EM,PKG` matches the two files' own port names, prints the matched / ambiguous / unmatched lists
and **stops** — naming any `--compose-link` or `--compose-export` it therefore did not run.
Review the CSV it writes, edit it, then commit what you accept with `--compose-map`. Elementwise
range pairing is a hard error on a length mismatch and echoes the *end* pairs, because an
off-by-one in one file's numbering shifts every pair silently. Many-to-one is normal (54 ground
balls onto one die pad).

### Before / after, without rebuilding anything

The delta ("what did the package cost me?") already exists: Calculate the bare EM trace,
**right-click → Freeze as new trace**, then add the package and Calculate again. The two rows sit
side by side in the results table with the snapshot's own timestamp on it, and the frozen one can
never be recomputed or edited by accident.

### GUI

Composition works in the GUI too. A trace has a **home file** (the `File:` combobox, exactly as
before) and any number of extra files:

1. Select the trace, then **Analyze → Files in this trace…** (also on the right-click menu of the
   Traces list and of the Files list). Right-click a row there to **Add a file…**, to **Remove**
   one, or to **Set as home**.
2. In the connection table, a **bare port number always means the home file** — every spec you
   already have keeps its meaning, and if you only ever use one file you will never see a tag. A
   port of another file is written with its tag: `F2.13`, `F2.40-42`.

   The tag scopes **the one token it is written on**, so a comma list may mix files freely and in
   any order — `25,26,F2.15` and `F2.15,25,26` are the same three ports, and
   `25,F2.12,F1.65,21` reads exactly as it looks. A **range is one token**, so `F2.40-42` takes a
   single tag; a *list* of one file's ports needs the tag on each (`F2.40,F2.42`) or a range.
3. Calculate. The trace is solved on the stacked network, over the **intersection of the two
   frequency spans resampled onto the finer grid** — never extrapolated. What the composition
   decided (the grid it took, what it dropped, how much phase an interpolation invented) is
   printed with the numbers.

The tags are `F1`, `F2`, … in the order the files are listed, `F1` being the home file. A tag is a
**position**, so changing the home file or removing a file renumbers the rest — the tool says so in
the Results pane when it happens, because a `F2.<port>` cell you already typed then names a
different file and nothing can rewrite it for you.

Everything that names a file follows: the results table grows a `File` column (`F1+F2`), the
coupling block heads with `files: F1=… + F2=…`, the CSV block writes `# Files: …`, **Ports &
Roles** lists the composed port list with each port's tag and name, and the plot legend marks a
composed curve with ` +N` (the file names do not fit a 30-character legend entry; they are in all
of the above).

**The reference-node check is mandatory** and appears under the numbers it qualifies — in the
Results pane, on every run page, in the files window and in the Attribution window. Read it: see
the section above for why grounded, open and through-an-inductor can be the same number.

Two things are worth knowing:

- A **cross-file short** goes in one cell of a `short` row: `2,F2.1`, or `25,26,F2.15` to tie two
  die ports and a package ball into one node. (A `short` row has no *To*: a group of shorted pins
  has no from/to, so the whole group lives in the one port cell. The cell next to it is the
  optional **Net** name for the node it creates.) A cross-file element is an `rlc_between` row
  with `2` in Port and `F2.1` in To.
- The composed port list appears in **Ports & Roles** as soon as you type a tag; the network
  itself is only stacked at Calculate, because stacking a 16-port die onto a 153-port package is
  measured at ten seconds and the editor's strips run on every keystroke.

---

## Important Notes

- **Results are TOTAL values, not per-unit-length.** `L`, `C`, `R` are reported for the network as seen between the chosen signal ports. To get per-unit-length values, divide by your known trace length yourself. The tool does not perform distributed (RLGC-per-length) extraction; that requires multi-section ABCD or `gamma`/`Z_0` extraction.
- **AC small-signal only.** `vdd` is an alias for `ground` because at AC the supply is an ideal short. The distinction exists in the UI for documentation clarity; Mode 4 was retired for the same reason.
- **Unlisted ports are OPEN, not grounded.** This is the most common source of wrong results. A forgotten GND ball floats and is Schur-eliminated, which preserves the behaviour at the kept ports but does *not* tie the port to the reference node.
- **The mutual `Z_ab` is the open-circuit one.** `Z[a][b]` is defined with every *other* measurement port carrying no current — that is the textbook definition of M, and the right primitive to hand to a simulator, where the real loading is modelled. It is not the same number as a short-circuit transfer measurement.
- **The termination spec is worth decibels, and there is a tool that says how many.** How you spell the ground field is not a detail: on `diff_pair_4port.s4p` three quarters of the extracted `M` comes from the two `ground` rows, and rewriting the same set as a shared return rather than independent leads moves `M` by 6.03 dB. [Port attribution](#port-attribution-where-a-coupling-number-comes-from) (**Analyze → Attribution…**) splits an extracted `Z_ab` into the bare EM coupling plus one signed term per declared termination, exactly, and answers the what-if exactly too. Read its three caveats before quoting it — in particular that it is **blind to ports you left open**, so its table ranks your declarations and never your ports.
- **The ports you did *not* declare have their own screen, and it is where a new file starts.** Because the attribution table is blind to open ports, it is empty on a file where nothing has been declared yet — which is exactly the state you are in when you open an unfamiliar 153-port export. [`--cold-start`](#cold-start-which-ports-matter-before-you-have-a-spec) ranks every undeclared port from all-open by the *exact* effect of grounding it, with its coupling to the victim and its coupling to the aggressor as **two separate columns** (measured: the port with the largest coupling-to-the-victim in a file moved the answer by −0.378 pH against −395.369 pH for the real path), and it scans **pairs**, because a shield brought out as two ports reads +9.689 pH per end alone and −870.268 pH for both — 90× the largest single-port effect, opposite sign.
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
                             plus one term per declared termination, answers the
                             exact what-if, and carries the four-step cold-start port
                             screen. Imports pkg_rlc_core only (acyclic)
  pkg_rlc_attrib_gui.py      The Attribution window (Analyze -> Attribution...): a
                             modeless Toplevel over pkg_rlc_attrib. pkg_rlc_gui holds
                             only the menu / right-click / refresh hooks
  pkg_rlc_compose.py         Several .sNp files as ONE network: block-diagonal stack,
                             common frequency axis, cross-file links, the mandatory
                             reference-node check, pre-reduction and export.
                             Imports pkg_rlc_core only (acyclic)
  pkg_rlc_plot.py            Matplotlib plot panel with M / V / Delete / drag features
                             (R, L, C, |Z|, Re, Im, Q, k subplots)
  pkg_rlc_gui.py             Tkinter GUI with file/trace management, and the
                             JSON session format (Save / Load / Restore Config)
  pkg_rlc_help.py            In-app Help window (one tab per mode + syntax + examples)
  pkg_rlc_extractor.py       Entry point (GUI + CLI), incl. the --attribute and
                             --cold-start reports
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
    test_attrib_coldstart.py   The four-step screen: the closed form against an honest
                             re-solve, the red herring, the shield pair, the mirror
    test_attrib_cli_coldstart.py  --cold-start on the CLI: flag refusals, the printed
                             order (bracket before ranking), the CSV round trip
    test_attrib_window.py    The Attribution window in isolation: the pure formatters
                             with no display, and the Tk-driven layout / refusal / export
    test_attrib_gui_integration.py  The same window end to end through the real app —
                             Add File to a number on the table, and every hook
    test_compose.py          Composition arithmetic: the weld, the reference check,
                             the frequency plan, the pre-reduction, the export
    test_compose_cli.py      The composition command line end to end, incl. the
                             composed-network attribution baseline
    test_attrib_composed.py  The composed-network gauge inside pkg_rlc_attrib
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
