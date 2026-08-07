# PKG RLC Extractor

A desktop tool for RF and analog IC engineers to extract R, L, C, and Q from Touchstone (`.sNp`) files. It targets package parasitic characterization but applies equally to EMX-extracted layout traces, DCO / spiral inductors, decoupling capacitors, and any other passive structure for which an EM solver (EMX, HFSS, Q3D, etc.) has produced an S-parameter matrix. All extractions go through a unified Y-parameter Schur-complement reduction; the four named "modes" in the UI are presets of a single underlying port-termination model.

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
3. In **Edit Selected Trace**, pick a measurement mode (Modes 1-4 or Custom) and fill in the relevant port fields.
4. Set **RLC Freq (GHz)** for single-point extraction, optionally enter a **Band Fit** range and model.
5. Click **Calculate All & Plot**. Results appear in the right pane and overlay on the multi-subplot view.

Use **Export CSV** to dump per-trace `Freq, Re(Z), Im(Z), |Z|, R, L, C, Q` tables.

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

CLI flags:

| Flag      | Meaning                                                                  |
|-----------|--------------------------------------------------------------------------|
| `--cli`   | Enable CLI mode (otherwise GUI launches)                                 |
| `--mode`  | `gnd` (Mode 1) or `p2p` (Mode 2)                                         |
| `--porta` | Signal / Port A specification (port range syntax)                        |
| `--portb` | Port B specification (required for `p2p`)                                |
| `--gnd`   | Ground port specification                                                |
| `--vdd`   | VDD ports (treated as AC ground; Mode 4)                                 |
| `--short` | Short pairs for Mode 3 (e.g., `"45-46,47-48"`)                           |
| `--freq`  | Single-point extraction frequency in GHz (default `0.1`)                 |
| `--fit`   | Band-fit model: `none` \| `auto` \| `inductor` \| `capacitor`            |
| `--fmin`, `--fmax` | Band edges in GHz for `--fit`                                   |
| `--csv`   | CSV output path                                                          |

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

---

## Measurement Modes

| Mode | UI label                | What it measures                                                                                  |
|------|-------------------------|---------------------------------------------------------------------------------------------------|
| 1    | `Port(s) -> GND`        | Driving-point impedance from a signal port (or shorted group) to ground.                          |
| 2    | `A <-> B`               | Impedance between two port groups; collapse to 2x2 then `Z = Z11 + Z22 - Z12 - Z21`.              |
| 3    | `A <-> B + Short Pairs` | Like Mode 2, but with explicit `i-j` shorts (Y-matrix row/col merging) before reduction.          |
| 4    | `A <-> B + VDD/GND`     | Like Mode 2; VDD ports are treated as AC ground (`vdd` is an alias of `ground` internally).       |
| 5    | `Custom (advanced)`     | Per-port termination editor: `open / ground / vdd / signal / short_to / lumped_to_gnd / lumped_between`, with R/L/C values for the lumped types. |

### EMX trace and inductor use cases

The same engine handles structures that are conceptually very different. What changes is only the port-termination configuration:

| Structure                          | Mode | Port assignment                                                                 | Fit model |
|------------------------------------|------|---------------------------------------------------------------------------------|-----------|
| **DCO / spiral inductor** (2-port P, N) | 2    | A=P, B=N, GND=(none if no GND port)                                             | Inductor  |
| **Diff trace, loop inductance** (5-port: inp, inn, outp, outn, gnd) | 3 | A=inp, B=inn, Short Pairs=`outp-outn`, GND=gnd_port | Inductor |
| **Diff trace, differential C**     | 2    | A=inp, B=inn, GND=gnd_port (outp/outn left default Open -> Schur-eliminated)    | Capacitor |
| **Decap with two mounting pads shorted** | 3 | A=pad1_top, B=gnd_top, Short Pairs=`pad1_bot-gnd_bot`                          | Capacitor (reports ESR, ESL, C) |
| **50 ohm-terminated signal path**  | 5    | port1=signal, port2=`lumped_to_gnd(R=50)`, others=ground                        | Auto      |

For loop-inductance measurements on a trace (Mode 3), shorting the far end forces the signal to return through the trace itself, exposing the differential loop inductance. For `C_diff` measurements (Mode 2), leaving the far end open isolates the inter-trace capacitance.

---

## Important Notes

- **Results are TOTAL values, not per-unit-length.** `L`, `C`, `R` are reported for the network as seen between the chosen signal ports. To get per-unit-length values, divide by your known trace length yourself. The tool does not perform distributed (RLGC-per-length) extraction; that requires multi-section ABCD or `gamma`/`Z_0` extraction.
- **AC small-signal only.** `vdd` is an alias for `ground` because at AC the supply is an ideal short. The distinction exists in the UI for documentation clarity.
- **Content-based file detection.** Port count is inferred from token count and frequency monotonicity — extension is ignored. Files without an option line are assumed `# GHZ S MA R 50` and a warning is emitted.
- **Numerical fallbacks.** Schur reduction uses `np.linalg.solve`; if `Y_oo` is singular, it falls back to `lstsq` and reports the offending frequency.

---

## Project Structure

```
SNP_RLC_Extractor/
  CLAUDE.md                  Conventions for future Claude Code sessions
  README.md                  This file
  CLAUDE_CODE_PROMPT.md      Authoritative spec
  requirements.txt           numpy (hard), matplotlib (GUI only)
  VERSION                    Commit stamp, filled in by the red-zone packer
  pkg_rlc_core.py            Touchstone parser, S->Y, termination model, Schur, RLC, fits
  pkg_rlc_plot.py            Matplotlib plot panel with M / V / Delete / drag features
  pkg_rlc_gui.py             Tkinter GUI with file/trace management
  pkg_rlc_extractor.py       Entry point (GUI + CLI)
  reduce_snp.py              Standalone CLI: shrink a big .sNp to a few ports
  tests/
    test_core.py
    test_port_parser.py
    test_content_sniffer.py
    test_reduce_snp.py
    generate_test_snp.py
  docs/
    theory.md                Math, circuit diagrams, mode derivations
  deploy/
    pack.ps1                 Windows: build the red-zone package
    deploy.sh                Red zone: verify, back up, swap in, auto-rollback
    doctor.sh                Red zone: what can this box actually run?
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

Upload `deploy\dist\snp_rlc_extractor_<short>.tar.gz` **and** its `.sha256`, then
on the isolated box (its login shell is often tcsh, so invoke with `bash`):

```bash
bash deploy/deploy.sh snp_rlc_extractor_<short>.tar.gz
bash deploy/doctor.sh --test
```

`deploy.sh` checksums the package, backs up the current install, swaps atomically
enough to auto-roll-back on failure, and keeps the last 3 versions. `doctor.sh`
then reports what that box can run, in tiers — `reduce_snp.py` and the CLI need
only `numpy`; the GUI additionally needs `matplotlib`, `tkinter` and `$DISPLAY`.
Missing GUI dependencies are a degrade, not a failure.

If you only need port reduction on a simulation server, `pack.ps1` also emits a
standalone `reduce_snp_<short>.py` that runs on its own with nothing but `numpy`.

Full procedure, rollback, and how to keep your own data across deploys:
[deploy/README.md](deploy/README.md).

---

## Theory

For the math behind each mode (S->Y conversion, Schur complement, the unified termination abstraction, and the broadband fitting models) see [docs/theory.md](docs/theory.md).
