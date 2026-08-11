# Design note — the connection table (Mode 5 / Mode 6 editor)

Status: **stages 0–3 implemented.** Stage 4 (modes reframed as presets that fill
the table) is specified here and deliberately NOT started — it rewrites the main
editor skeleton and needs a human looking at the screen. See "Staging" at the
bottom, and "What shipped differs from the mock" below it.

What works today: Modes 5 and 6 share the measurement-port table; Mode 5 adds
the connections table, the port-overview strip, the validation strip and an
"Edit as text…" escape hatch. The DSL takes port ranges, the row model
round-trips, and any spec defining two or more measurement ports produces the
full coupling matrix regardless of which mode wrote it.
What does not exist yet: preset seeding (stage 4), and everything in
"Deliberately out of scope" below.

---

## 1. The problem

Mode 5 ("Custom") is one bare `tk.Text` widget. The greyed-out syntax
cheat-sheet inside it is **deleted the moment the widget takes focus**
(`PlaceholderText._on_focus_in`), so the user must have memorised the
mini-language before clicking in. Nobody has — including the tool's author.
Errors surface only as a Python traceback after Calculate.

Mode 6 has the same disease in a less obvious form: measurement ports 1 and 2
get structured fields, and port 3 onward falls off a cliff into a free-text box
(`mp_more`) with its own syntax.

The ask: replace both with a table of rows and a `+` button.

## 2. Shape of the fix

Two tables, not one. Sixteen of the seventeen directive shapes in the DSL are
"a two-terminal element between node X and node Y"; only `signal` (attaching a
probe) is a different kind of statement. Mixing them into one table forces the
"To" column to hold two different value domains — a port number for an element
row, a measurement-port *name* for a probe row — and the dropdown contents then
change under the cursor.

Splitting them removes the problem instead of managing it:

```
Measurement ports                                   [+ Add]
┌──────────┬──────────────┬──────────────┬─┐
│ Name     │ + ports      │ − ports      │ │
│ tank     │ 1            │ 2            │✕│
│ vco      │ 5,7          │ 6,8          │✕│
└──────────┴──────────────┴──────────────┴─┘

Connections                                  [+ Add] [Text…]
┌────────────┬────────┬────────┬──────┬──────┬──────┬─┐
│ Type       │ Port   │ To     │ R Ω  │ L H  │ C F  │ │
│ ground   ▾ │ 6-14 ▾ │        │      │      │      │✕│
│ rlc_gnd  ▾ │ 13   ▾ │        │ 5m   │ 0.5n │ 1u   │✕│
│ short    ▾ │ 3    ▾ │ 4    ▾ │      │      │      │✕│
└────────────┴────────┴────────┴──────┴──────┴──────┴─┘

Ports (45): 4 probe · 8 ground · 1 element · 32 open
✓ port 13 → GND: 5 mΩ + 500 pH + 1 uF
```

(The second line is the validation strip. The name-aware warning sketched here
— "3 open ports are named VSS_ball_*" — is not shipped; the strip reports what
the two tables say, plus the parsed value of the first element row.)

- **Mode 6** is the measurement-port table + GND field. Nothing else.
- **Mode 5** is that same table *plus* the connections table underneath. The
  superset relationship is shown by the layout rather than hidden as a trap.
- The "To" column in the connections table has exactly one domain: a port
  dropdown whose first entry is `GND`.

Key details that came out of the design review and are not negotiable
afterwards without redoing the analysis:

- **The Port cell takes a range**, not a single port. `6-14 ground` — or
  `35:1:45 ground`, the MATLAB `start:step:stop` form — is one row. (`5:12` is
  NOT valid: `parse_port_range` requires all three colon-separated fields.)
  Without this a 45-port package needs a row per ground ball and the table is
  worse than the text box it replaces.
- **The Port dropdown carries the file's port names** — the parser already
  harvests `! Port[12] = VDD_ball_2` into `TouchstoneData.port_names`. On an
  unfamiliar file this is the single highest-value affordance in the design:
  what the user has forgotten is not the syntax, it is *the layout*.
  (**Deferred in stage 3** for a measured width reason — see §5a. The shipped
  dropdown carries bare port numbers; the names stay reachable through
  **Show Ports**, which is now named in both table hints, in Help → Mode 5 and
  Help → Input syntax, and in the README, and which falls back to the editor's
  file instead of silently doing nothing when the Files listbox has no
  selection. A deferral nobody is told about is just a missing feature.)
  (**Superseded**: `Show Ports` is no longer a substitute, it is the answer.
  It opens the **Ports & Roles** window — every port with its name, the role
  the spec gives it, the row that decided it, and a write-back that turns a
  selection into a collapsed range in the table. That is strictly more than a
  dropdown could carry in 105 px, and it is what the five pointers now point
  at. If stage 4 ever puts names *in* the dropdown, it is an addition, not a
  replacement, and the pointers stay.)
- **Units live in the column headers** (`R Ω`, `L H`, `C F`); the cell holds
  `5m`. The string `R=` never appears in the UI again.
- **Each element row echoes its parsed value** (`5 mΩ + 0.5 nH + 1 µF series`).
  `5m` vs `5M` is one shift key and nine orders of magnitude; the echo catches
  it. It also makes the blank-cell semantics visible — blank means *omitted*,
  and omitted C is C=∞ (no capacitor), not C=0 (an open circuit).
- **A blank `−` side is legal** (ground-referenced probe) and must warn at most,
  never error.

## 3. Decisions taken without the user present

The user approved the design and then left. These three were open; all are
additive, reversible, and now pinned by tests.

**(a) The DSL's port field accepts `parse_port_range` syntax.**
`parse_custom_termination_text` used `int(parts[0])`, so a range-bearing row had
no way to round-trip through text. Extending the field is backward compatible
(`parse_port_range("3") == [3]`), makes the text mode better on its own, and
avoids a second serialisation format. `short_to` takes a range on both sides
(shorting is transitive, so the chained-pair spelling of `parse_short_pairs` is
unambiguous). `lumped_between` refuses one on the right — an N-to-M lumped
element is ambiguous (star? mesh?) and guessing would be a silent wrong answer.

**(b) Rows are stored structurally on `TraceConfig`; `custom_text` becomes a
derived view.** The alternative — text as the single source of truth — cannot
represent state the table needs: which preset a trace came from, whether it has
been edited since, and the order of measurement ports. As of stage 3 the field
is *retired-but-loading*, like `mp1_*`: `migrate_legacy_custom_text` folds it
into the two tables on load and nothing writes it again. The DSL text is
computed on demand by `_editor_dsl_text()`; storing it as well would leave the
migration guard unable to tell a legacy trace from a freshly synced one.

**(c) The row model is serialisable to a file.** Not used yet. It costs nothing
now and it is the thing that later enables "save a configuration and reuse it
across corner files" and "the CLI consumes what the GUI wrote" — both of which
are expensive to retrofit onto an in-memory-only model.

## 4. The hole in the regression net (why stage 0 came first)

`build_terminations_mode1/2/3` assign `Signal` first and `Ground` second into
the same dict, so when a port appears in both lists **ground silently wins**.
`build_terminations_coupling` **raises** on the same overlap, because a probe
side is tied together and grounding one port grounds the whole side.

Both behaviours are intended (see CLAUDE.md). The danger is that a preset which
seeds the table from Mode 2 crosses from one rule to the other, and a working
configuration either starts erroring or quietly answers a different question.

**The golden reference does not catch this.** `tests/_golden_capture.py`
constructs its cases by calling `build_terminations_modeN` directly, so any new
path to a `TerminationSet` bypasses every golden case. Before this branch, no
test anywhere pinned the precedence.

`tests/test_core.py::TestTerminationPrecedence` now does, including:

- ground-wins for modes 1/2/3 and vdd-wins-over-ground for mode 4,
- that the overlap is visible *in the computed impedance*, not just the dict,
- that the coupling builder raises on the same input,
- that the DSL is last-assignment-wins, so a table serialising to text must emit
  ground **after** the probe to reproduce a named mode,
- that a DSL spec written that way is bit-identical to mode 1 on a real fixture.

Anything that claims to reproduce a named mode must satisfy this class.

The same hole has a second mouth, found in stage 3: **importing existing text
into the table can flip the precedence the other way.** `dsl_text_to_rows`
discards line order and `rows_to_dsl_text` re-emits every probe before every
connection, so `3 ground / 3 signal A / 4 signal B` — where the probe
deliberately overrides the earlier ground — comes back with port 3 grounded and
`resolve_meas_ports` then raises. `_import_dsl_text` therefore compares the
resolved `TerminationSet` before and after the round trip and, when they differ,
keeps the whole spec verbatim in `extra_lines` (which `rows_to_dsl_text` appends
unchanged, so it is bit-identical to what the trace computed before).
`tests/test_connection_rows.py::TestRoundTripIdempotent` pins both halves.

## 5. Staging

| Stage | Content | Verifiable without a human? |
|-------|---------|-----------------------------|
| 0 | Precedence tests, DSL port ranges, this note | **Yes** — test suite |
| 1 | Row model, `build_terminations_rows`, Mode 5 → `compute_z_matrix` when ≥2 probes | **Yes** — test suite |
| 2 | Measurement-port table widget, wired to Mode 6 | Code yes, look-and-feel no |
| 3 | Mode 5 full editor: both tables, port overview, validation strip | **Done** — the verifiable half only |
| 4 | Modes reframed as presets that fill the table | **No** |

Stage 3's *verifiable* half is pinned by `tests/test_mode5_editor.py`: the
text↔rows import decision, both strip renderers, what the editor loads and
stores, which widgets each mode grids, and every layout claim measured off a
mapped window (`winfo_ismapped`, `winfo_reqwidth`, canvas `xview`/`yview`/
`scrollregion`, `PanedWindow.sashpos`). Its look-and-feel half — whether the
result is less confusing than what it replaces — still cannot be settled by a
test and wants review.

Stage 4 rewrites the main editor skeleton and changes what every existing
workflow looks like. It waits.

### 5-R1. Per-kind row shape, nets and the parallel stamp (landed after stage 3)

Not stage 4, and not a prerequisite for it. The user came back with a second
complaint about the same table — *"不同的连接，出现的表格都是一样的 … 多个pin
连接到一起的时候，我很自然的感觉就是一个blank"* — and the fix is orthogonal to
the preset question:

| | What landed |
|---|---|
| R1-1 | The cells a row shows follow its **Kind**. `ground`/`vdd`/`open` one port field, `short` one port field for the whole tied group plus a Net name, `rlc_gnd` port + R/L/C, `rlc_between` the only two-port-field kind. The shared **header** follows the rows too, because `To` was a lie on a short row even with the cell hidden. **No column added** — the 13 px budget is untouched and the worst case (every kind at once) is still 405 px. |
| R1-2 | A short row may **name** the node it creates, and any port field may use that name. Sugar: it resolves to one member port, so the answer is bit-identical to typing that member — which already worked. |
| R1-3 | `parallel_stamp_messages` **refuses** an element row whose left side expands to N ports that are already ONE node. Measured 3.333 fH against a typed 10 fH, with nothing on screen saying so. |
| R1-4 | The footer verdict is **clickable** and scrolls to the offending row. Zero pixels. |
| R1-5 | Validation messages are ordered by **consequence**, not check order. |

CLAUDE.md § *Per-kind row shape, nets, and the parallel stamp* carries the
measured numbers and the hazards; `tests/test_conn_nets.py` and
`tests/test_conn_rowshape.py` are the guards.

Two things §5a below said are now out of date and are corrected here rather
than rewritten above, so the reasoning stays readable: the Port/To dropdowns
still carry NUMBERS, but they now carry every **merged node's** reference token
above them (a name, or its first member); and the parsed-value echo's strip is
now consequence-ordered, so an echo is only ever reached when nothing worse
fired.

## 5a. What shipped differs from the mock

Four decisions taken during stage 3, each for a measured reason:

- **No `GND` entry in the "To" dropdown.** `ConnectionRow(kind="short",
  to="GND")` emits `3 short_to GND` and the parser raises *"short_to partner
  must be a port number or range"*. "To ground" is a **kind** here (`ground`,
  `rlc_gnd`), whose `to` field is ignored entirely. Putting GND in both places
  makes the same fact expressible two ways, one of which is an error message
  the user cannot connect to what they clicked.
- **The Port dropdown carries port NUMBERS, not names** — deferred, not
  dropped. Measured: a ttk Combobox's popdown is only as wide as the widget, so
  a 7-char Port cell shows `12: VDD_bal…` truncated in the list as well as in
  the cell. A name-bearing dropdown needs ≥15 chars ≈ 105 px, which the 431 px
  editor viewport does not have. Revisit in stage 4, when the rail can be
  re-proportioned; the names are reachable through **Show Ports**, which now
  opens the **Ports & Roles** window (name + role + source per port, with a
  collapsed-range write-back into these tables) rather than printing a list
  into the Results pane.
- **The per-row parsed-value echo moved into the validation strip.** A dedicated
  static column needs ~20 chars ≈ 140 px. The strip costs nothing and catches
  the same error (`✓ port 13 → GND: 5 mΩ + 500 pH + 1 uF`). It emits **one line
  per element row**, so the property design §2 asked for holds for every row;
  the strip itself shows two and `Calculate All & Plot` writes the whole list to
  the Results pane. (It shipped emitting only the first row's echo plus
  `(+N more)`, which left the `5m` vs `5M` typo invisible on every row but one.)
- **An R/L/C cell must be one token.** `5 m` and `1 uF` are refused rather than
  serialised. The DSL splits on whitespace and drops a token with no `=`, so
  `R=5 m` computed 5 Ω and `C=1 uF` computed 1 farad — and because the echo
  re-parses the raw cell as one token it printed `5 mΩ` beside the 5 Ω. The unit
  in the column header is exactly what invites `uF` into the cell, so the guard
  is not optional.
- **The two strips are Mode 5 only.** Mode 6 shipped and is on main; every check
  the strips make is one `build_terminations_coupling` already raises for at
  Calculate, so two extra rows there buy little and risk the one mode that must
  not regress.

## 6. Deliberately out of scope

Raised during the design review, real, and not part of this branch:

1. **Load a port with another `.sNp`** (vendor MLCC `.s2p`, another EM block).
   Named as the most frequent reason to abandon the tool for `skrf`.
2. **Save/reuse a configuration across files, bound by port name not index** —
   port order changes between extractions, so index-based reuse is itself a
   wrong-number machine.
3. **A CLI that consumes the GUI's configuration file.** Build and eyeball once
   in the GUI, then loop it over thirty corner files from the shell.
4. Parallel R‖L‖C, raw complex Z, frequency-dependent R.
5. Stating next to the `k` readout that **the sign of k is arbitrary** — it
   follows from which terminal the user called `+`, not from physics, and the
   number gets pasted into review decks as if it were intrinsic.
6. Moving the rank-deficiency warning out of the per-frequency results and into
   a single pre-Calculate line ("structure is DC-floating; differential probes
   are valid, common mode is undefined"). A warning that fires on every
   frequency of every normal coupled-inductor file trains users to stop reading
   warnings.
7. **Both strips are below the fold at rest.** Measured on a mapped window at
   1500x900: the editor canvas is 345 px against a 501 px mode-5 form, and
   `_update_mode_visibility` resets the scroll to 0 on every mode change (it has
   to — a now-short form must not stay parked out of sight), so switching to
   Mode 5 shows File, the mode radios, the measurement-port table and the top of
   the connections table, and neither strip until the user scrolls. Nothing is
   unreachable and once scrolled to the connections table the table and both
   strips are co-visible; the first impression is simply that the strips do not
   exist. Pinning them into the editor's footer would fix it and would also eat
   the last of a viewport that is already a 45 px slit at the 1040x600 minsize —
   which is the proportion question **stage 4** exists to answer, since it
   rewrites the editor skeleton anyway.

(3) is the one that changes what the tool is for; the row model in stage 1 is
shaped so it stays cheap.
