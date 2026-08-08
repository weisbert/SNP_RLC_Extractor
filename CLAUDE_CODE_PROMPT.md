# Claude Code Project Prompt: SNP RLC Extractor

## Project Overview

Build a desktop GUI application **PKG RLC Extractor** using Python + Tkinter + Matplotlib. The tool extracts R, L, C, Q parameters from Touchstone `.sNp` files using Y-parameter Schur complement methods. It is used by RF/analog IC design engineers to characterize package parasitic impedance from electromagnetic simulation results (e.g., from EMX, HFSS, Q3D).

The entire project should be created from scratch in the current empty directory.

---

## Project Structure

```
pkg_rlc_extractor/
├── CLAUDE.md                  # Project conventions for Claude Code
├── README.md                  # User documentation with theory & usage
├── requirements.txt           # matplotlib, numpy
├── pkg_rlc_core.py            # Touchstone parser, S→Y, Z computation, RLC+Q extraction
├── pkg_rlc_plot.py            # Matplotlib plot panel with interactive features
├── pkg_rlc_gui.py             # Main Tkinter GUI with trace/file management
├── pkg_rlc_extractor.py       # Entry point (GUI + CLI modes)
├── tests/
│   ├── test_core.py           # Unit tests for parser, S→Y, Schur, RLC
│   ├── test_port_parser.py    # Port range parser edge cases
│   └── generate_test_snp.py   # Script to generate synthetic .sNp test fixtures
└── docs/
    └── theory.md              # Detailed theory: circuit models, measurement modes, Schur complement
```

---

## Core Computation Theory (MUST be implemented exactly as described)

### S-parameter to Y-parameter Conversion

```
Y = y0 * (I - S) * inv(I + S)
```
where `y0 = 1/Z0`, `I` is identity matrix, `S` is the S-parameter matrix at each frequency.

### Schur Complement for Port Elimination

Given a Y-matrix partitioned into kept ports (k) and open ports (o, where I=0):

```
Y_reduced = Y_kk - Y_ko * inv(Y_oo) * Y_ok
```

This eliminates "open" ports (I=0 boundary condition) from the network.

### Unified Port Termination Abstraction (MASTER MODEL)

All measurement scenarios reduce to one underlying operation: each port carries a **termination** describing its boundary condition. The four named modes below are convenient UI shortcuts; internally they all lower to this abstraction. The same abstraction also supports arbitrary lumped terminations (R, L, C, or combinations) for advanced users.

Supported terminations per port:

| Termination | Physical meaning | Effect on Y-matrix |
|---|---|---|
| `open` | I=0, port floating | Eliminate via Schur complement |
| `ground` | V=0, tied to reference | Delete row/column |
| `vdd` | V_ac=0, AC-grounded ideal supply | Same as `ground` (alias) |
| `signal` | Excitation port | Keep for measurement |
| `short_to(j)` | V_i = V_j, two ports tied together | Merge rows i,j and cols i,j (sum) |
| `lumped_to_gnd(Y_term(f))` | Port terminated through R/L/C network to ground | `Y[i,i] += Y_term(f)`, then mark open |
| `lumped_between(j, Y_term(f))` | Two ports linked through R/L/C network | `Y[i,i]+=y; Y[j,j]+=y; Y[i,j]-=y; Y[j,i]-=y`, then mark open |

`Y_term(f)` is any frequency-dependent admittance:
- Resistor R → `1/R`
- Inductor L → `1/(jωL)`
- Capacitor C → `jωC`
- Series R+L+C → `1 / (R + jωL + 1/(jωC))`
- Parallel combinations → sum of admittances

**Evaluation order at each frequency:**
1. Apply all `lumped_*` terminations: modify Y per the table. These ports then behave as `open`.
2. Apply `short_to(j)` merges: combine rows/columns.
3. Delete `ground` and `vdd` rows/columns.
4. Schur-eliminate all `open` ports.
5. Compute Z from the remaining `signal` ports per user query (driving-point or port-to-port).

The four named modes below are predefined termination configurations exposed in the UI for common workflows. A **"Custom"** mode (Mode 5) lets advanced users specify arbitrary per-port terminations directly, including lumped R/L/C end-terminations.

### Measurement Mode 1: Port(s) to Ground (Driving-Point Impedance)

Physical model: signal port(s) excited with current, ground port(s) held at V=0, all other ports open (I=0).

Steps:
1. Ground ports: removed from Y-matrix (V=0 → rows/cols deleted).
2. Other (unused) ports: eliminated via Schur complement (I=0).
3. Signal ports shorted together: `Z = 1 / (ones^T * Y_reduced * ones)`

Circuit analogy: measuring impedance from a bond wire pad to the ground plane through the package.

### Measurement Mode 2: Port-to-Port (Between Two Groups)

Physical model: measure impedance between port group A and port group B. Example: differential pair P-side vs N-side.

Steps:
1. Ground ports removed (V=0).
2. Schur complement eliminates open ports (I=0).
3. Remaining ports = A ∪ B. Collapse to 2×2 Y by shorting within each group:
   - `Y_2x2[0,0] = sum(Y_AA)`, `Y_2x2[0,1] = sum(Y_AB)`, etc.
4. Invert to `Z_2x2 = inv(Y_2x2)`.
5. `Z_between = Z_11 + Z_22 - Z_12 - Z_21`

Circuit analogy: measuring loop inductance between two signal traces through the package.

### NEW — Measurement Mode 3: Port-to-Port with Shorted Port Pairs

Physical model: some port pairs are explicitly shorted together (e.g., Port45-Port46, Port47-Port48 shorted as decap connections), then measure impedance between two signal port groups.

**This is critical for modeling decoupling capacitor mounting or wirebond shorting scenarios.**

Implementation approach:
1. When user specifies "short pairs" like `45-46, 47-48`, these pairs have V_i = V_j constraint.
2. Merge shorted port pairs in the Y-matrix: for ports i,j being shorted, combine rows and columns:
   - New row = row_i + row_j, new col = col_i + col_j
   - This is equivalent to: the merged port carries the sum of currents, at a common voltage.
3. After merging, proceed with normal Schur complement + port-to-port calculation.

Algorithm for merging port pair (i, j) in Y-matrix:
```python
# Merge port j into port i (0-indexed)
Y[:, i] += Y[:, j]    # sum columns
Y[i, :] += Y[j, :]    # sum rows  
# Then delete row j and col j
Y = np.delete(np.delete(Y, j, axis=0), j, axis=1)
# Update port index mapping accordingly
```

### NEW — Measurement Mode 4: Ports with VDD Bias

Physical model: some ports connected to VDD (ideal voltage source, V=V_dd), some to GND (V=0), measure impedance between signal port groups.

From the Y-parameter perspective:
- VDD ports behave the same as GND ports in terms of AC impedance measurement — they are **AC ground** (V_ac = 0).
- The distinction is conceptual/documentation only for the user.
- Implementation: treat VDD ports identically to GND ports (remove from Y-matrix).

**Important**: Make this clear in the UI and documentation — VDD ports are AC-grounded. The tool does AC small-signal analysis only. In the unified termination model `vdd` and `ground` are aliases; the distinction exists only for user clarity in the UI.

### RLC Extraction from Z(f)

At each frequency point (signed convention, matches Cadence):
```
R(f) = Re(Z(f))
L(f) = Im(Z(f)) / (2π*f)           # <0 when capacitive at f
C(f) = -1 / (2π*f * Im(Z(f)))      # <0 when inductive at f
Q(f) = Im(Z(f)) / Re(Z(f))         # signed quality factor
```
Past SRF, an inductor's Im(Z) flips negative and L/Q go negative while C
becomes positive (parasitic-capacitance regime). The GUI prints a short
note in the results pane when L, C, or R is reported as negative.

User selects a specific frequency (e.g., 0.1 GHz) for single-value extraction.

### Broadband Fitting (v1 REQUIREMENT)

Single-frequency extraction is insufficient for many real use cases (DCO inductors, EMX traces, decap models). The tool must support fitting an equivalent-circuit model over a user-specified band `[f_min, f_max]`.

**Inductor model** (use for inductors, bond wires, traces with shorted far-end):
```
Z(f) = R_dc + R_ac * sqrt(f) + j*2π*f*L
```
- `R_ac * sqrt(f)` term captures skin-effect resistance growth
- Reports: `L`, `R_dc`, `R_ac`, `Q@f_center`, `SRF` (frequency where Im(Z) crosses zero, if within data)

**Capacitor model** (use for caps, decap, traces with open far-end):
```
Z(f) = R_esr + j*2π*f*L_esl + 1 / (j*2π*f*C)
```
- Reports: `C`, `R_esr`, `L_esl`, `SRF` (resonance where Im(Z) crosses zero)

**Auto model selection**: inspect `Im(Z(f))` over the band:
- Predominantly increasing & positive → inductor model
- Predominantly negative or 1/f-shaped → capacitor model
- Crosses zero within band → fit both, report which has lower RMSE

**Implementation:**
- Use `scipy.optimize.least_squares` (or `numpy.linalg.lstsq` after linearization where possible) to fit complex Z(f).
- For the inductor model, fitting is linear in `[R_dc, R_ac, L]` against features `[1, sqrt(f), j*2π*f]` — solve as one complex least-squares problem.
- For the capacitor model, fitting is linear in `[R_esr, L_esl, 1/C]` against features `[1, j*2π*f, 1/(j*2π*f)]`.
- Report fit RMSE (in Ω) and an R²-like goodness-of-fit so user can judge model validity.
- Plot the fitted curve as a dashed overlay on the Re(Z)/Im(Z)/|Z| subplots in the band region.

**UI**: a "Band Fit" panel next to "RLC Freq" with three controls — `f_min (GHz)`, `f_max (GHz)`, `Model: [Auto | Inductor | Capacitor]` — and a "Fit Band" button. Results append to the results pane.

---

## Touchstone Parser Requirements

### File Format Support
- Accept **any file extension** (or no extension): `.s2p`, `.s45p`, `.txt`, `.dat`, `.snp`, no extension at all, etc. The parser **must not branch on extension** — port count is detected from file content.

### Universal Content-Based Port-Count Detection

```
1. Read all lines. Separate:
     - Option line: first line beginning with '#'
     - Comment lines: starting with '!' → keep for port-name extraction
     - Data lines: everything else (skip blanks)
2. Parse option line if present → freq_unit, param_type, format, Z0
   If absent: assume default '# GHZ S MA R 50' and warn the user.
3. Tokenize all data lines into a single flat list of floats.
   Total token count = T.
4. For N = 1, 2, 3, ..., 256:
     record_size = 1 + 2 * N * N
     if T % record_size != 0:        continue
     freqs = tokens[0::record_size]  (first column of each record)
     if not strictly monotonically increasing: continue
     candidate N accepted.
5. If exactly one N accepted → use it.
   If multiple accepted → take the smallest, log a warning, allow user override.
   If none accepted → open a dialog asking user to specify N manually.
6. Reshape and decode per the option-line format (RI / MA / DB).
```

The strict-monotonic-frequency check is what disambiguates `.s2p` from `.s4p` from `.s8p` reliably, even when files are renamed.

### Supported Formats
- Option line: `# [freq_unit] [param_type] [format] R [z0]`
- Frequency units: HZ, KHZ, MHZ, GHZ, THZ
- Data formats: RI (real/imaginary), MA (magnitude/angle), DB (dB/angle)
- Port names from comments: `! Port[N] = name`

### Parser Robustness
- Handle multi-line data records (large port count files span many lines per frequency)
- Ignore blank lines and comment lines (`!`)
- Handle inconsistent whitespace

---

## GUI Specification

### Layout: Horizontal PanedWindow
- **Left panel** (~440px): file management, trace management, editor, controls
- **Right panel** (expandable): results text + plot area (vertical PanedWindow, user-draggable)

### Left Panel Components

#### 1. Loaded Files Section
- **Add File...** button: opens file dialog, accepts `.s*p` and `.txt` files
- **Remove** button: removes selected file and its traces
- **Show Ports** button: opens the **Ports & Roles** window (port number, name, the role the current spec gives it, the row that decided it; filter / sort / flagged rows; write a selection back into the editor as a collapsed range). It printed a plain port list into the results area originally.
- Listbox showing loaded files with info: `filename (Np, Mf, Z0=50Ω)`
- `exportselection=False` on all Listboxes (critical — prevents selection loss on focus change)

#### 2. Traces Section
- **Add Trace** / **Remove** / **Duplicate** buttons
- Listbox showing traces: `[id] label | filename mode`
- Auto-create a default trace when a file is loaded

#### 3. Edit Selected Trace Panel
- **File**: Combobox selecting from loaded files
- **Mode**: Radio buttons (all map to the unified termination model internally)
  - `Port(s) → GND` (Mode 1)
  - `A ↔ B` (Mode 2)
  - `A ↔ B + Short Pairs` (Mode 3)
  - `A ↔ B + VDD/GND` (Mode 4)
  - `Custom (advanced)` (Mode 5) — opens a per-port termination table editor: each row = port number + termination type (open/ground/vdd/signal/short_to/lumped_to_gnd/lumped_between) + optional R/L/C value(s). Lumped values use SI units (Ω, H, F).
- **Signal / Port A**: Entry field (port range syntax)
- **Port B**: Entry field (shown only for Modes 2/3/4)
- **Short Pairs**: Entry field (shown only for Mode 3), syntax: `45-46, 47-48`
- **GND Ports**: Entry field
- **VDD Ports**: Entry field (shown only for Mode 4, treated as AC ground internally)
- **Label**: Entry field for trace legend name
- **Plot**: `this trace` checkbox (every mode — per-trace visibility) plus `self` / `mutual` (modes 5/6 only)
- **Style**: a line preview that expands in place into a palette of the 12 colours and 4 linestyles, each drawn as it will be drawn on the plot. Stores the same two indices the Spinboxes it replaced did.
- **Calculate This Trace** button (editor footer) — recomputes only the selected trace

There is no *Apply* button: the editor writes itself into the selected trace as
you type. See the auto-apply section of `CLAUDE.md` for the three properties
that make that safe.

Mode switching should show/hide relevant fields dynamically.

#### 4. Global Controls
- **RLC Freq (GHz)**: Entry field (frequency for single-point R/L/C/Q extraction)
- **Calculate All & Plot** button
- **Export CSV** button

### Right Panel

#### Results Area
- ScrolledText widget, monospace font
- Shows per-trace results: `[id] label (file, mode) @ freq: R=... L=... C=... Q=...`
- User-resizable via PanedWindow sash

#### Plot Area
- Control bar at top with:
  - X axis: Log checkbox
  - Y axis: Log checkbox  
  - Freq marker line: Show checkbox
  - Plot type checkboxes: R(mΩ), L(nH), C(pF), |Z|(Ω), Re(Z), Im(Z), Q
  - Fullscreen: Combobox (select type) + Fullscreen button
- Matplotlib figure with subplots arranged in grid (max 4 cols)
- Matplotlib navigation toolbar

### Port Range Syntax
Support these formats (1-based port numbers):
- Single: `1`
- Comma-separated: `1,3,5`
- Range with step: `35:1:45` (MATLAB-style start:step:stop)
- Dash range: `6-14`
- Mixed: `1,3,35:1:45,50-55`

### Short Pair Syntax (for Mode 3)
- Comma-separated pairs with dash: `45-46, 47-48`
- Each pair means "short port X to port Y"
- Parser returns list of (int, int) tuples

---

## Interactive Plot Features

### 1. Freq Marker Line (Red Dashed Vertical)
- Drawn at the RLC extraction frequency on all subplots
- **Draggable**: click near the line and drag horizontally to change frequency
- At each intersection with a trace curve, show annotation: `(freq GHz, value)`
- Can be toggled on/off via checkbox
- Drag tolerance must account for log-scale x-axis

### 2. M Key — Point Marker
- Press `M` with mouse over a subplot
- Adds a square marker at the nearest data point
- Annotation shows `(freq GHz, value)` with arrow
- Works for all traces on that subplot
- **Deletable**: Press `Delete` to remove the most recent annotation (stack-based LIFO)

### 3. V Key — Vertical Line
- Press `V` with mouse over a subplot
- Adds a gray dotted vertical line at that frequency
- Shows diamond markers + value annotations at intersections with ALL traces on ALL subplots
- **Deletable**: Press `Delete` to remove most recent

### 4. Delete Key
- Removes the most recently added M-marker or V-line (LIFO stack)
- Works in both main view and fullscreen window

### 5. Fullscreen Window
- Select plot type from combobox, click "Fullscreen"
- Opens new `Toplevel` window (1200×700) with single large plot
- Has its own matplotlib toolbar
- Supports M, V, Delete keys independently
- Canvas must have `focus_set()` for key events to work

### 6. Log/Linear Toggle
- X axis and Y axis independently switchable
- Y axis log uses `symlog` scale with appropriate `linthresh`

### 7. Multi-trace Overlay
- All traces drawn on same axes with different colors/linestyles
- Legend shows trace labels (truncated to 30 chars max to avoid layout issues)
- Color palette: 12 distinct colors
- Linestyle palette: 4 styles (-, --, -., :)

---

## Data Flow

```
User loads .sNp/.txt file
  → parse_touchstone() → freqs, S-data, Z0, port_names
  → s_to_y() → Y-data
  → stored in FileEntry object

User configures trace (file + ports + mode)
  → stored in TraceConfig object

User clicks "Calculate All & Plot"
  → for each TraceConfig:
    → build_terminations(trace_config) → dict[port_idx, PortTermination]
       (the named modes 1-4 are convenience builders; Mode 5 is a passthrough)
    → compute_z(Y_data, terminations, freqs) → Z(f) array
    → extract_rlc_at_freq(freqs, Z, freq_for_rlc) → single-point R/L/C/Q
    → if band-fit enabled:
        fit_broadband(freqs, Z, model, f_min, f_max) → broadband result + RMSE
  → PlotPanel.set_traces(trace_list)
  → display results text
```

---

## CSV Export Format

```csv
# Trace: label_name
Freq_GHz,Re_Z,Im_Z,|Z|,R_mOhm,L_nH,C_pF,Q
0.001000,1.23e-03,4.56e-02,4.57e-02,1.23e+00,7.26e+00,-3.49e+03,3.71e+01
...
```

One section per trace, separated by blank line.

---

## CLI Mode

```bash
python pkg_rlc_extractor.py --cli file.s45p --mode gnd --porta "1" --gnd "6:1:14" --freq 0.1
python pkg_rlc_extractor.py --cli file.s45p --mode p2p --porta "1,2" --portb "3,4" --gnd "5:1:10" --freq 0.1 --csv output.csv
```

Arguments:
- `file`: Touchstone file path (any extension; content-sniffed)
- `--mode`: `gnd` | `p2p`
- `--porta`: Port A specification
- `--portb`: Port B specification (required for p2p)
- `--gnd`: Ground ports
- `--vdd`: VDD ports (treated as AC ground)
- `--short`: Short pairs for mode 3 (e.g., `"45-46,47-48"`)
- `--freq`: Extraction frequency in GHz for single-point R/L/C/Q (default 0.1)
- `--fit`: Broadband fit model: `none` | `auto` | `inductor` | `capacitor` (default `none`)
- `--fmin`, `--fmax`: Band for `--fit` (in GHz)
- `--csv`: Export CSV path
- `--cli`: Flag to enable CLI mode

---

## Documentation Requirements (README.md and docs/theory.md)

### README.md should cover:
- Installation (`pip install matplotlib numpy`)
- Quick start (GUI and CLI)
- Port range syntax reference
- Measurement mode descriptions with practical examples

### docs/theory.md should cover (with equations and ASCII circuit diagrams):

#### 1. What is a Touchstone file?
- S-parameter matrix representation
- Physical meaning: how EM simulators produce these

#### 2. S → Y conversion
- Why Y-parameters? (parallel network addition, port elimination)
- The conversion formula with derivation sketch

#### 3. Schur Complement — what it means physically
- Open port (I=0): the port is floating, no current flows
- Ground port (V=0): the port is tied to reference, voltage is zero
- Schur complement removes open ports while preserving the network behavior at kept ports
- ASCII circuit diagram showing the equivalent

#### 4. Mode 1: Port-to-Ground impedance
- Circuit diagram:
```
        Signal Port(s)
            │
            ▼
    ┌───────┴───────┐
    │   Package     │
    │   Network     │
    │   (Y-matrix)  │
    └───────┬───────┘
            │
           GND
```
- Formula: `Z = 1 / (1^T · Y_red · 1)`
- Use case: measuring bond wire + trace + via inductance to ground

#### 5. Mode 2: Port-to-Port impedance
- Circuit diagram:
```
    Port A group          Port B group
        │                     │
        ▼                     ▼
    ┌───┴─────────────────────┴───┐
    │       Package Network       │
    │         (Y-matrix)          │
    └───┬─────────────────────┬───┘
        │                     │
       GND                   GND
```
- Formula: collapse to 2×2, invert, `Z = Z11+Z22-Z12-Z21`
- Use case: differential loop inductance measurement

#### 6. Mode 3: With shorted pairs
- Circuit diagram showing decap-shorted ports
- Y-matrix row/column merging algorithm
- Use case: modeling the effect of shorting decap pads

#### 7. Mode 4: With VDD ports
- Explain why VDD = AC ground
- Same math as Mode 1/2, VDD ports treated identically to GND

#### 8. RLC extraction from Z(f)
- Why R, L, C are frequency-dependent
- Series RLC equivalent circuit at a given frequency
- Q factor interpretation

#### 9. Broadband Fitting
- Why single-frequency extraction is not enough for inductors/decap
- Inductor model with skin-effect resistance: `Z = R_dc + R_ac·√f + jωL`
- Capacitor model with ESR/ESL: `Z = R_esr + jωL_esl + 1/(jωC)`
- Auto-selection logic and fit-quality reporting

#### 10. Use Case Examples — what to measure for what

Tool is used not just for IC packages but also for EMX-extracted layout traces, DCO inductors, decap, etc. The **same tool, same modes** apply — what differs is only the port-termination configuration. Underlying math is identical.

**Example A — DCO / spiral inductor (2-port: P, N)**
```
Mode 2 (A↔B):  A=P, B=N, GND=(none if no GND port)
Fit:           Inductor model over [f_min, f_max]
Reports:       L, R_dc, R_ac, Q@f_center, SRF
```

**Example B — Differential trace (5-port: inp, inn, outp, outn, gnd) — loop inductance**
```
Mode 3 (A↔B + Short Pairs):
   A = inp,  B = inn,  Short Pairs = "outp-outn",  GND = gnd_port
Why:          Shorting the far end forces the signal to return through
              the trace, exposing the differential loop inductance.
Fit:          Inductor model
Reports:      L_loop (total, in nH — NOT per unit length), R_dc, Q
```

**Example C — Differential trace — differential capacitance**
```
Mode 2 (A↔B):
   A = inp,  B = inn,  GND = gnd_port
   (outp, outn left as default open → I=0 via Schur)
Why:          Open far end isolates the inter-trace capacitance.
Fit:          Capacitor model
Reports:      C_diff (total)
```

**Example D — Decap with two mounting pads shorted (Mode 3)**
```
Mode 3:  A = pad1_top, B = gnd_top, Short Pairs = "pad1_bot-gnd_bot"
Reports: ESR, ESL, C as seen at the top mounting plane
```

**Example E — Custom: signal through a 50Ω termination (Mode 5)**
```
Mode 5 with terminations:
   port1 = signal
   port2 = lumped_to_gnd(R=50)
   port3..N = ground
Why:     Measure driving-point impedance with realistic source/load termination.
```

**Important note about results**: all extracted R/L/C/Q values are **totals** for the network as seen between the chosen signal ports — never per-unit-length. If the user wants per-unit-length values, they must divide by their known trace length manually. The tool does **not** do distributed transmission-line (RLGC-per-length) extraction; that requires a different procedure (multi-section ABCD or γ/Z₀ extraction).

---

## Critical Implementation Notes

1. **Listbox `exportselection=False`**: ALL Listbox widgets MUST set this. Without it, clicking any Entry/Spinbox/Combobox steals the X selection and clears the Listbox highlight. The editor resolves its auto-apply target from that selection, so a cleared highlight means every keystroke is silently discarded.

2. **Auto-sync editor on Calculate**: Before calculating, flush any queued sync and push the current editor fields into the selected trace. Auto-apply usually got there first, but a keystroke in the same event burst as the click is still in the idle queue.

3. **Label truncation**: Truncate trace labels to 30 chars for plot legends to prevent subplot squeezing.

4. **Log-scale drag tolerance**: When x-axis is log scale, the drag detection for the freq marker line must use log-space distance, not linear.

5. **Canvas focus**: After creating any FigureCanvasTkAgg, call `canvas.get_tk_widget().focus_set()` so key events (M, V, Delete) are received.

6. **Port index convention**: User-facing is 1-based. Internal computation is 0-based. Convert at the GUI↔core boundary.

7. **Schur complement numerical stability**: Use `np.linalg.solve` instead of explicit inverse. Compute `cond(Y_oo)` (1-norm or condition estimate). If `cond > 1e12` or `solve` raises `LinAlgError`, fall back to `np.linalg.lstsq` and emit a warning to the results pane (with the offending frequency and condition number). This matters most for Mode 3 (shorted pairs at a far-end of weakly-coupled ports) and for `lumped_*` terminations at frequencies where `Y_term(f)` is near-singular.

8. **Auto-create trace on file load**: When user loads a file, automatically create a default TraceConfig bound to it. Don't make user manually "Add Trace" for the basic workflow.

9. **Y-axis symlog**: When y-log is enabled, use `ax.set_yscale('symlog', linthresh=1e-6)` to handle data crossing zero.

10. **Multi-file comparison**: Each trace independently selects its file and port config. Two traces can reference different files with different port configs and be plotted on the same axes.

---

## Test Requirements

### test_core.py
- Test `parse_port_range` with: `"1"`, `"1,3,5"`, `"35:1:45"`, `"6-14"`, `"1,3,35:1:45"`, `""`, edge cases
- Test S→Y conversion with known 2-port S-parameters
- Test Schur complement: 3-port Y with one port open → verify 2-port result
- Test `extract_rlc_at_freq`: given a known Z(f), verify R/L/C/Q values
- Test **unified termination dispatch**: for each named mode (1–4), build the corresponding termination dict and verify `compute_z` produces the same result as a hand-coded reference implementation of that mode. This pins the equivalence guarantee.
- Test **lumped terminations**: 3-port network with port 2 terminated through R=50Ω to ground; result should match the analytical 2-port reduction.
- Test **`lumped_between`**: 4-port network with ports 3↔4 connected through C=1pF; result at low frequency should approximate open (cap is high-Z), at high frequency should approximate short.
- Test **broadband fit**:
  - Synthetic Z = R_dc + R_ac·√f + jωL with known params → inductor fit recovers them within 1%
  - Synthetic Z = R + jωL_esl + 1/(jωC) with known params → capacitor fit recovers them within 1%
  - Auto mode picks the right model
- Test **numerical stability fallback**: construct a near-singular `Y_oo` (two near-identical rows) and verify that lstsq fallback triggers and emits warning.

### test_port_parser.py
- Cover `parse_short_pairs`: `"45-46, 47-48"` → `[(45,46),(47,48)]`; trailing commas; whitespace; invalid syntax raises `ValueError`.

### test_content_sniffer.py (NEW)
- File renamed `.txt` containing `.s2p` data → detected as 2-port
- File with no extension containing `.s4p` data → detected as 4-port
- File with no option line → assumes default + warns
- Ambiguous file (data could parse as N=1 or N=2) → returns smallest, logs warning
- Garbage file → raises clear error

### generate_test_snp.py
- Generate a synthetic `.s2p` file with known R+jωL impedance
- Generate a `.s4p` with known port-to-port impedance
- These serve as regression test fixtures

---

## Technology Stack
- **Python 3.11** (target runtime: 3.11.4)
- **Tkinter** for GUI (ships with Python)
- **Matplotlib** with TkAgg backend for plotting
- **NumPy** for matrix computation
- **Custom Touchstone parser** — no scikit-rf dependency (the parser must handle edge cases specific to EDA tool exports that scikit-rf may not cover well)
- **Third-party libraries are allowed** when they genuinely improve efficiency or quality — but do not add dependencies for things that are straightforward to implement with stdlib + numpy. Every added dependency must justify itself. For example:
  - `scipy.interpolate` for proper interpolation at marker points — acceptable
  - `scipy.linalg` for more robust matrix operations — acceptable
  - A full GUI framework replacement (PyQt, wxPython) — not acceptable, stick with Tkinter
  - `pandas` just to write a CSV — not acceptable
  - `scikit-rf` just to parse Touchstone — not acceptable (we need custom control)

Build this project file by file, starting with `pkg_rlc_core.py`, then `pkg_rlc_plot.py`, then `pkg_rlc_gui.py`, then `pkg_rlc_extractor.py`, then tests, then documentation.
