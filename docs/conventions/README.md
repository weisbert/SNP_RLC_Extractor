# `docs/conventions/` — the per-area rules

Moved out of `CLAUDE.md` on **2026-08-31**, VERBATIM, when that file reached
428.7k characters against the 150k a session can hold. **These rules are exactly
as binding as the ones that stayed there** — they were split by AREA, not by
importance. `CLAUDE.md` keeps what applies to a change anywhere (the layer map,
the module map, the cross-cutting invariants, the import gate, the
bit-exactness rules, the rejected-proposal list) and points here for the rest.

**Read the file that covers the area you are about to touch, before you touch
it.** Every `###` heading below is the section title it had in `CLAUDE.md`, so a
cross-reference of the form ``CLAUDE.md § <title>`` still resolves.

## [`architecture.md`](architecture.md) — 27k

The four sections of the main window as four classes; what a Calculate RUNS vs what it SHOWS; why the run record sits at L1; and the arithmetic the CLI and the results pane share, against the spellings they deliberately do not.

- The four panels of the main window (`pkg_rlc/panels/panels_*.py`)
- One formatter, two spellings (the CLI and the results pane)
- The run module (`pkg_rlc/services/run.py`) — the SOLVE landed, the ORCHESTRATION did not
- How the run record got its home — READ THIS BEFORE MOVING ANY OF IT

## [`attribution_core.md`](attribution_core.md) — 38k

Why `Z_ab` decomposes at all, the twelve rules that keep it honest (dense `Zt`, the condition-aware residual, refuse-by-name, the Möbius sweep), and the cold-start screen that ranks ports before a spec exists.

- Port attribution (`pkg_rlc/physics/attrib.py`)
- The cold-start screen (`--cold-start`, CLI only)

## [`attribution_gui.md`](attribution_gui.md) — 56k

The Attribution window: every pixel budget, the four refusals, `[Recompute]`-not-auto-refresh, the sweep plot's pole; and the two attribution REPORTS that must not drift apart.

- The Attribution window (`pkg_rlc/panels/attrib_gui.py`)
- The two attribution reports (`pkg_rlc/present/attrib_report.py`)

## [`cli_report.md`](cli_report.md) — 4k

`tests/fixtures/cli_reference/` — 143 pinned invocations, what the capture normalises, and the two KNOWN-NOT-FIXED cases pinned as-is.

- The CLI's printed report (`tests/fixtures/cli_reference/`)

## [`editor_and_tables.md`](editor_and_tables.md) — 59k

The Mode 5 / Mode 6 row editor, per-kind row shape, named nets and the parallel-stamp refusal, auto-apply and the style picker, and the Ports & Roles window.

- Connection table (the Mode 5 / Mode 6 row editor)
- Per-kind row shape, nets, and the parallel stamp (round 1)
- Auto-apply, the style picker, plot visibility
- Port names, roles, and the Ports & Roles window

## [`multifile.md`](multifile.md) — 27k

Several Touchstone files solved as ONE network: the reference-node weld, the frequency plan, the `F1.`/`F2.` namespace, and the composed attribution baseline.

- Composition — several files as ONE network (`pkg_rlc/physics/compose.py`, round 2)
- The two-file GUI — schema, namespace, engine (round 3)

## [`plot_panel.md`](plot_panel.md) — 18k

What range the axes show and what unit they say, the control strip that wraps instead of losing its tail, and the one readout box per cursor.

- The plot panel's axes (what range they show, what unit they say)
- The plot panel's control strip
- Cursor readout (the plot's marker / V-line labels)

## [`reading_files.md`](reading_files.md) — 11k

A non-numeric token is a HARD error; every failure is a `TouchstoneParseError` carrying a verdict; the second-pass diagnosis; the sniffer, the encoding, and the out-of-order sweep.

- Reading files (robustness, diagnosis, refusal)

## [`results_pane.md`](results_pane.md) — 42k

The Log tab and its badge, the three views and the 144-column budget, the run tabs and their two disjoint caps, the immutable run snapshot, and freeze-as-trace.

- Freeze as trace (the before/after comparison)
- The run snapshot (what a finished Calculate leaves behind)
- The Results pane notebook (the Log tab and its badge)
- The three results views (`detail` / `summary` / `compare`)
- Run history (the run tabs after the Log)

## [`session_and_help.md`](session_and_help.md) — 11k

The session file as a pure dict round trip (config never results), and the Help window whose prose lives in `docs/help/`.

- The session file (Save Config / Load Config / autosave)
- The Help window's prose lives in `docs/help/`, not in Python

## [`standalone_and_deploy.md`](standalone_and_deploy.md) — 18k

`reduce_snp.py` (standalone, imports nothing from this repo), the air-gapped deploy pipeline, and running the Tk suite on its own Win32 desktop.

- `reduce_snp.py` specifics
- `deploy/` specifics (red-zone pipeline)
- Hiding the GUI tests (`tests/_isolated_desktop.py`)

## [`test_suite_map.md`](test_suite_map.md) — 31k

One row per test file: what it measures, which mutation it was checked against, and the numbers it pins. `CLAUDE.md` carries the one-line index into it.

- `tests/` — the suite, in the order it grew

---

**A new rule goes in the file that owns its area, not back into `CLAUDE.md`.**
That file is for what a session must know without being told to look; if a rule
is about one window, one panel or one report, this is where the next session
will find it, because `CLAUDE.md`'s pointer table sends them here.
