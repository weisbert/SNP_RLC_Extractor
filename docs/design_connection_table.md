# Design note — the connection table (Mode 5 / Mode 6 editor)

Status: **in progress on branch `feature/connection-table`.**
Stages 0–2 are implemented; stages 3–4 are specified here but deliberately NOT
started — they need a human looking at the screen. See "Staging" at the bottom.

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
│ Ground   ▾ │ 5:12 ▾ │ GND    │      │      │      │✕│
│ Series RLC▾│ 13   ▾ │ GND  ▾ │ 5m   │ 0.5n │ 1u   │✕│
│ Short    ▾ │ 3    ▾ │ 4    ▾ │      │      │      │✕│
└────────────┴────────┴────────┴──────┴──────┴──────┴─┘

▸ Ports (45): 4 probe · 8 ground · 1 element · 32 open
⚠ 3 open ports are named VSS_ball_*  — intended?
```

- **Mode 6** is the measurement-port table + GND field. Nothing else.
- **Mode 5** is that same table *plus* the connections table underneath. The
  superset relationship is shown by the layout rather than hidden as a trap.
- The "To" column in the connections table has exactly one domain: a port
  dropdown whose first entry is `GND`.

Key details that came out of the design review and are not negotiable
afterwards without redoing the analysis:

- **The Port cell takes a range**, not a single port. `5:12 ground` is one row.
  Without this a 45-port package needs a row per ground ball and the table is
  worse than the text box it replaces.
- **The Port dropdown carries the file's port names** — the parser already
  harvests `! Port[12] = VDD_ball_2` into `TouchstoneData.port_names`. On an
  unfamiliar file this is the single highest-value affordance in the design:
  what the user has forgotten is not the syntax, it is *the layout*.
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
been edited since, and the order of measurement ports. `custom_text` is kept and
still round-trips, but it is now an interchange/escape-hatch format rather than
storage.

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

## 5. Staging

| Stage | Content | Verifiable without a human? |
|-------|---------|-----------------------------|
| 0 | Precedence tests, DSL port ranges, this note | **Yes** — test suite |
| 1 | Row model, `build_terminations_rows`, Mode 5 → `compute_z_matrix` when ≥2 probes | **Yes** — test suite |
| 2 | Measurement-port table widget, wired to Mode 6 | Code yes, look-and-feel no |
| 3 | Mode 5 full editor: both tables, port overview, validation strip | **No** |
| 4 | Modes reframed as presets that fill the table | **No** |

Stages 3 and 4 are where the actual UX judgement lives — whether the result is
less confusing than what it replaces cannot be settled by a test. Stage 4 also
rewrites the main editor skeleton and changes what every existing workflow looks
like. Both wait for review.

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

(3) is the one that changes what the tool is for; the row model in stage 1 is
shaped so it stays cheap.
