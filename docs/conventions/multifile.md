# Several files as one network (composition, and the two-file GUI)

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### Composition — several files as ONE network (`pkg_rlc/physics/compose.py`, round 2)

The user's framing decides everything here: *"我们现在这种相当于用户在自己搭建一个快捷的
TB 了，得到的肯定是**所搭即所得**"*. The connection table is a quick TESTBENCH; the
deliverable is the ABSOLUTE number of the assembled thing, and the before/after delta
already exists as freeze-as-trace. But "what you built is what you measure" RAISES the
bar rather than lowering it: the tool's first duty becomes making *what was built*
unambiguously visible, because when it is not what the user thinks, the answer is a
precise wrong number. `tests/test_compose.py`, `tests/test_compose_cli.py` and
`tests/test_attrib_composed.py` are the guards, and every claim below was
mutation-checked.

- **`block_diag` WELDS the two files' reference nodes, and that is the premise, not a
  footnote.** An n-port Touchstone `Y` is the matrix with its OWN reference already
  eliminated, so stacking two of them identifies `ref_A` with `ref_B` at zero impedance.
  Measured on a 2 nH coil + 100 pH package trace + 100 pH package ground lead: with the
  die return brought out as a PORT and tied to the package ground pad, `L_eff` =
  **2.2501 nH** and it moves when the ground path changes; with the die return being the
  EM reference, the package ground pad grounded / open / through 1 nH all give
  **2.1454 nH, bit-identical, spread 0.000e+00**. The package's entire ground network is
  unreachable and nothing raises. That is the same failure shape as the 6 dB dispute the
  feature exists to settle, arriving through the door this feature is.
- **The reference-node self-check is MANDATORY OUTPUT and has no off switch.**
  `solve_composed` runs it; `reference_check` perturbs each file's declared ground set
  with a series inductor at ONE frequency (the question is topological) with TWO values a
  decade apart, and a delta of `== 0.0` — exact, not a tolerance — is `REF_WELDED`. Two
  extra solves per file. `REF_NO_GROUND` is deliberately NOT folded into `welded`: the
  CORRECT die-return-as-a-port configuration declares no package ground, so folding them
  cries wolf on exactly the composition the feature exists to make work.
- **Y is z0-invariant, so there is no renormalisation step.** Measured: `max |Y(z0=50) −
  Y(z0=75)|` = **1.049e-17**. Each file goes `S -> Y` with ITS OWN `z0` and the blocks are
  stacked. "Renormalise if z0 differs" is a NON-TASK and was deleted from the plan; it
  returns only for an export path, which needs one `z0` for the whole file.
- **Interpolate S, never Y and never Z**, and check the PHASE STEP, not `max |S|`. For a
  passive network S is bounded at every real frequency so it has no real-axis poles, while
  Y blows up at a series resonance and Z at a parallel one. A post-interpolation `max |S|`
  check is **structurally incapable of firing** — `{S : σ_max <= 1}` is convex, so any
  convex combination stays inside (measured max σ = 0.999999900000) — and `max |S|` is not
  a passivity test anyway (all off-diagonals 0.6 gives max entry 0.6 and σ_max 1.80). Do
  not ship one. What interpolation DOES break is phase: `dphi = 2*pi*df*tau`, and the
  chord error `1 - cos(dphi/2)` reads as fake insertion loss and corrupts R and Q.
  Measured: a 1 ns delay at a 100 MHz step is 36.0° → 4.89% amplitude → **0.436 dB** of
  invented loss (warn); 2 ns is 72.0° → **1.841 dB** (refuse).
- **An identical grid is detected with a RELATIVE tolerance (~1e-9), never `array_equal`.**
  A file written in GHz and one written in Hz describing the same sweep differ by
  **2.218e-16** and `np.array_equal` answers False, so the common same-flow case would
  otherwise be interpolated onto itself. The COARSER file's max step is reported as the
  effective resolution — upsampling recovers no information — and a marker frequency
  landing inside a wide coarse interval is flagged.
- **The tag separator is a DOT and must never be a colon.** `parse_port_range("PKG:12")`
  raises "Range must be start:step:stop" today — `:` is already the range separator in
  every port field in this repo. `COMPOSE_TAG_SEP = "."`. The aliases are the repo's own
  `F1` / `F2` idiom from `_format_results_table`, because the measured column budget has
  no room for a file name (one file column 451 px, two 497 px, a Name column 469 px, all
  against a **431 px** viewport) and "port 305" is unactionable on a 316-port network.
- **EVERY warning names its file.** Core's one bare-port-number message is re-raised
  scoped by `_scope_port_error`; the CLI translates the rest with `_COMPOSE_PORTNUM_RE`.
- **`merge_terms` raises with 0-BASED indices, and it is the FIRST message a composed
  network hits.** "Ports [1, 4] merged via short, but assigned to conflicting signal
  groups" carries the Union-Find MEMBER list, which is internal 0-based indices; every
  other message core raises at that boundary is 1-based. Measured: `EM.2` (global 2)
  shorted to `PKG.3` (global 5) reports `Ports [1, 4]`. `_COMPOSE_MERGED_RE` translates it
  with its own offset — translating it as 1-based would name two real, innocent ports with
  total confidence. **This is a defect in `pkg_rlc.physics.solve` (`merge_terms`, nested
  inside `compute_z_matrix`) and it is NOT fixed**: the fix
  moves a message other tests pin, and the CLI's translation depends on the current
  offset. Fix both halves together or neither.
- **The correspondence is the USER's; the tool may PROPOSE and only the user may COMMIT.**
  `--compose-propose` prints and stops, naming any `--compose-link` / `--compose-export`
  it therefore did not run. Elementwise range pairing is a HARD ERROR on a length mismatch
  and ECHOES the END pairs, because an off-by-one in one file's numbering shifts every
  pair silently. Many-to-one is normal (54 VSS balls onto one die pad), N-to-M with both
  above 1 has no defensible order and is reported as ambiguous.
- **Pre-reduction is the edit/recompute loop, not a one-shot run, and the help says so.**
  `_freq_batch` collapses at combined sizes (16→64, 60→4, 76→2, **153→1, 316→1**), so the
  stacked-solve batching `compute_z_matrix`'s docstring justifies at length stops working
  exactly where it is first needed. Measured on this box: 16-port die + 120-port package
  at 201 frequencies, the solve goes **3113 ms → 14.4 ms = 216x** and the answers agree to
  7.4e-16 — but the reduction itself costs 2.5 s, so ONE end-to-end run is 7378 → 6754 ms,
  i.e. **1.09x**. Quoting the 216x for a single run would oversell it by 200x.
  `--compose-export` is the "reduce once, load the small one" route.
- **`--compose-export` writes the STACKED network, not the assembled one, and says so.**
  The links are `ShortPair` / `LumpedBetween` in the `TerminationSet`, and a short MERGES
  NODES and changes the port count; stamping them into `Y` would be a second
  implementation of the merge the golden reference exists to pin, in the CLI layer. The
  report names every link the file does not contain, the file's comments list them, and
  the round trip (`parse_touchstone` → `s_to_y` == `net.Y`, tested at 1e-12) is the
  independent check. `EXPORT_DIGITS = 17`, measured: 9/12/15/17 reproduce S to
  9.210e-11 / 7.235e-14 / 1.777e-16 / **0.000e+00**.
- **The n==2 column-major quirk cannot be caught by a physical fixture.** Every passive
  network has `S12 == S21`, so the transpose is invisible. Any test of it must use a
  deliberately NON-reciprocal 2-port (the guard uses `S21 = 0.6`, `S12 = 0.1`).
- **A limit-case fixture needs UNEQUAL die pads.** With equal pads the EM block is
  port-symmetric and a swapped mapping reproduces the standalone number EXACTLY, so
  `limit_case_check` — which exists to catch a swapped mapping — passes for the wrong
  reason. Measured: 0.0 with equal pads, 1.30e-2 with 2 fF / 8 fF.
- **`--short` is refused on a composed network.** `a-b` cannot say which file each side is
  and `-` is already a range. `--compose-link "EM.3 short_to EM.4"` covers it.
- **`--compose` without `--cli` exits 2** rather than opening the GUI on one file and
  silently dropping the rest.

#### The attribution baseline on a composed network (R2-8)

- **The cross-file links are IN the baseline, not elements on top of it, and that is a
  DELIBERATE GAUGE CHANGE.** The all-open baseline leaves the files as disconnected
  islands, so `Ybase` is exactly block diagonal. Measured with the real engine on a
  12-port combined network: the EM-vs-PKG off-diagonal block is **0.000e+00**, every
  package-only element's contribution is **EXACTLY 0**, and `residual_rel` reads
  **6.49e-15**, i.e. perfect health. Re-measured end to end through this CLI on a 10-port
  case: the package-internal element reads **exactly 0j against a 1.70e-13 residual**
  without the gauge and **−1.939976e-09 H** with it. A confident, exactly-zero,
  perfectly-reconciled wrong answer is the worst output this tool can produce, and no test
  of the attribution arithmetic can see it because the arithmetic is right.
- **`BaselineLinks(blocks=…)`, never an enumerated link list, and there is no flag to turn
  it off.** A `PortBlocks` says "every declared link whose two ports come from different
  files is structure", which cannot MISS a link; an enumerated list can, and a missed link
  is the silent zero above. A link inside one file stays an ordinary element, because it
  is one — the gauge is about the stack, not about the spec. `_compose_baseline` builds it
  from `b.nports` (the SURVIVING count), never `b.nports_original`: after a
  `--compose-keep` pre-reduction the block in `net.Y` is the reduced one.
- **The gauge is NAMED on the report.** `COMPOSED_BASELINE_TEXT` is ONE string (the
  `SIGN_CONVENTION_TEXT` rule) and reaches the header, the decomposition's `baseline:`
  line and the cold-start notes verbatim. Two attribution reports are comparable only when
  their baselines match.
- **A composition with NO cross-file link says so.** The policy selects DECLARED links, so
  with none declared it selects nothing, the baseline is back to all-open, and the header
  still carries a paragraph saying the files are connected. `_compose_gauge_notes` names
  the contradiction; the island warning inside `build_context` cannot, because it only
  fires for elements and a far file with no elements has nothing to name.
- **The cold start needs the gauge MORE than the decomposition does**, because it REWRITES
  the spec — probes kept, every other declaration dropped. Without the policy the
  cross-file links go with them: measured on the 12-port construction, ALL SIX package
  ports come back with `delta` exactly 0.0 and `defined = True`, a screen confidently
  reporting that the package cannot matter. `cold_start_report`'s own `baseline=` is a
  no-op while the CLI passes `context=csc` (`_cs_context` returns the given context
  untouched) and is kept as the safety net for that edit; the one that bites is
  `cold_start_context`'s.
- **`--mport` text is rewritten to GLOBAL numbering before `_attr_sources` parses it, and
  the LABEL stays the text the user typed.** `parse_mport_spec` reads bare integers, so
  handing it `vic = EM.1` is a `ValueError` traceback out of a report the coupling solve
  has already been paid for. `spec_labels` is the display half; a group named after an
  index nobody wrote is the other failure.
- **A cross-file link is grouped under the `--compose-link` that declared it**
  (`link_sources`, walked last because that is the order they enter `term.couplings` and
  the map is last-assignment-wins). Without it every link falls back to its KIND and two
  links on two lines land in one group called `lumped_between`.
- **`pkg_rlc.physics.attrib.Element.describe()` renders GLOBAL indices** ("ground port 10"),
  because an element is a stamp on the combined `Y` and knows nothing about files. That is
  unactionable on a 316-port network unless the map is on the same screen, so the CLI
  prints the block map as a header note. Threading a labeller through every construction
  site in `pkg_rlc.physics.attrib` was the alternative and is the fix if this is ever revisited.
- **The naming heuristics get `ALIAS.name`, with NO local number** (`_attr_family_names`).
  `name_prefix` strips only a trailing run of digits, so the label printed elsewhere —
  `PKG.100 VSS_1` — is exactly the wrong input for it: the prefixes come out
  `PKG.100 VSS_`, `PKG.101 VSS_`, … one family per port, on the file where a family is the
  whole point. `PKG.VSS_1` gives `PKG.VSS_` for all 54 and keeps two files' identically
  named nets apart, which a bare `VSS_1` would not.

### The two-file GUI — schema, namespace, engine (round 3)

Round 2 made `pkg_rlc.physics.compose` able to answer; round 3 is what lets the GUI ask.
`tests/test_multifile_session.py` (the schema), `tests/test_multifile_table.py`
(the window and the cell budget) and `tests/test_multifile_engine.py` (the
engine and the surfaces) are the guards, and every claim below was
mutation-checked.

- **A HOME FILE PLUS EXTRAS, never one list of files.** `TraceConfig.file_label`
  stays a single `str` and keeps its meaning — a bare port number is a port of
  THAT file, in every mode — and `file_labels` holds the others in order. That
  is what makes every pre-existing spec, every golden case and every saved
  session mean exactly what it meant, and what keeps a single-file user from
  ever seeing a tag. It is also the only layout that FITS: measured, a per-row
  file COLUMN takes the connections table from 405 px to **451** (two columns
  497, widening Port/To to 11 chars 461, a Name column 469) against a **431 px**
  viewport whose documented headroom is 13 px.
- **A file's TAG IS ITS POSITION** (`default_alias`: F1 is the home file, F2 the
  first extra), resolved by `trace_file_labels` here and by `slots_of` in
  `pkg_rlc.panels.files_gui`. Two authorities for what `F2.3` means is the silent
  wrong answer this feature exists to end, so the two are pinned against each
  other and the files module DELEGATES to this one. Measured there: a port cell
  is `ttk.Combobox(width=7)` — **72 px / 7 characters at 100%, 135 px / 7
  characters at 150%**, and the character count is what is DPI-stable. `F2.` is
  33% / 34% of the text budget and leaves 4 digits; a 4-character tag is 73% /
  76% and leaves 1. `23,24,25` is 48 px and fits the 49 px budget; `F2.23,24,25`
  is 64 px, i.e. **131%**, and scrolls in a widget with no scrollbar. Hence
  `ALIAS_MAX_CHARS = 3` and a tag ONLY on an endpoint that crosses files.
- **THE HOME FILE IS BLOCK 0 AT OFFSET 0 WITH EVERY PORT KEPT, and everything
  rests on it.** That is what makes default scope FREE rather than a translation
  layer: measured on `coupled_2port_gndref.s2p + pi_2port.s2p`,
  `parse_scoped_ports('1', net, default='F1')` is `[1]` and `('2', …)` is `[2]`,
  while `'F2.1'` is `[3]`. It is also what makes the refusal free — a bare
  number PAST the home file's port count would otherwise address the next
  file's ports (`'5'` on a 4-port home is F2.1: a plausible number from a port
  nobody named), and `net.gport` raises there by name with the port map
  attached. Every port field therefore goes through `parse_scoped_ports`, tag
  or no tag; nothing is passed through untouched because it "looks bare".
- **THE FILE TAG SCOPES THE TOKEN IT IS WRITTEN ON, AND NOTHING AFTER IT. A
  BARE TOKEN IS ALWAYS THE HOME FILE, with no ordering condition.** One rule,
  in `parse_scoped_ports`, which is why `_scope_port_field` is now a single
  call to it with no parsing of its own — GUI and CLI cannot drift on what a
  field means. `2,F2.1`, `F2.1,2`, `F1.1,F2.3` and `25,F2.12,F1.65,21` all say
  exactly what they look like; a RANGE is one token, so `F2.40-42` still takes
  one tag. Ports are deduped over the whole field, on the GLOBAL index (two
  files' local port 1 are two different ports).
  The tag used to be **sticky** — it scoped the whole field and every bare
  token after it — which is why `parse_scoped_ports` had to REFUSE
  `F1.1,F2.3`: with a sticky tag that field has two readings ("one field, two
  scopes" / "the first tag scopes the lot") that differ in silence. The cost
  was the same silence one step further on. A `short` row stores its whole tied
  group in ONE cell (`_join_short_group`, R1's single-cell short — a group of
  shorted pins has no from/to), so a die-to-package tie is written
  `25,26,F2.15` there and has no other spelling; write the same group in the
  other order, `F2.15,25,26`, and the sticky rule re-pointed 25 and 26 at the
  PACKAGE. Nothing raised — it only needs the package to HAVE ports 25 and 26 —
  and it contradicted the rule stated in the Help, the README and
  `pkg_rlc.frontend.app`'s own header: *a bare number is a port of the home file, in
  every mode*. **The user reported it as unreasonable and was right.** Two
  consequences that are NOT regressions and are pinned as such: a list of one
  file's ports needs the tag on each token or a range (`PKG.10,PKG.11` or
  `PKG.10-11`, not `PKG.10,11`) — which changes what an existing
  `--compose-link "PKG.4,5 …"` means, visibly, because the CLI echoes the file
  of every port it paired; and a bare token with NO default scope (a direct
  `parse_scoped_ports` call passing `default=None`) is refused rather than
  inheriting a tag, where the CLI itself always passes
  `net.blocks[0].alias` and the GUI always passes the home file.
- **THE STRIP ECHOES WHAT EVERY TAGGED PORT FIELD RESOLVED TO**
  (`scope_echo_messages`, `✓ connection row 1 Port: 25,26,F2.15 = F1.25-26,
  F2.15`). The reading of a mixed field is the one thing in this namespace the
  user cannot check from the screen: the port cell is **7 characters** wide,
  and the validation echo under it prints GLOBAL indices (`✓ port 42,110 →
  GND`, the display-only defect recorded below), so neither answers *which
  file*. It is built from `parse_scoped_ports` — the resolver the solve itself
  uses — and rendered by `describe_ports`, so an echo that disagrees with the
  computed network is not expressible. Four rules: it takes the **UNSCOPED**
  rows (scoping rewrites the field to a global index and the tag is gone by
  then, so this is the only point where both spellings exist); only a field
  that **TAGS** a file is echoed, because a bare field is the home file by a
  rule with no exception left in it and `25 = F1.25` on every row would spend
  the strip's two lines saying nothing; it **survives alongside a problem**
  rather than being suppressed by one like the R/L/C echoes, since "which file
  is that port of" is at its most useful when something else is wrong, and it
  is V_OK so a real problem still outranks it in the two-line strip; and it
  **never raises** — a field that does not resolve gets NO echo, because a
  green tick beside a refusal about the same cell is worse than one message.
  The one spelling it exists for is `F2.40,42` = package 40 and **HOME 42**.
- **`_scope_dsl_text` rewrites FIELD POSITIONS, never every token that contains
  a dot.** `parts[0]` is always a port field and `parts[2]` is one after
  `short_to` / `lumped_between`; nothing else in the grammar is. A blanket scan
  survives `C=1.5p` by accident (`_split_tag` reads the head as `C=1`, which
  fails the alias pattern) but would silently re-point a signal group named
  `F1.something`. Node names are skipped through core's own `_collect_nets` —
  the ONE definition of which tokens in that text are names.
- **Mode 3's Short Pairs is the ONE port field that is not scoped, and it has an
  explicit check instead.** `parse_short_pairs` reads its tokens with `int()`,
  so a tag there already fails with core's message — but a bare index past the
  home file would have gone through as a global port. `_check_bare_ports` is
  that check; do not delete it in favour of "the resolver catches everything".
- **THERE ARE TWO NAMESPACE BUILDERS, and that is a measured decision.**
  `_trace_network` stacks the real thing (Calculate). `_namespace_network`
  builds a `ComposedNetwork` with the blocks and `Y = zeros((0, n, n))` — it
  answers "what does F2.3 mean" from the port counts alone and allocates
  nothing. The strips and the Ports & Roles refresh both run from
  `_apply_editor_strips`, i.e. once per KEYSTROKE, and `comp.compose` measured
  on this box with smooth synthetic data (three runs each) is **100 / 112 /
  97 ms** for 16 + 60 ports at 401 points, **10780 / 10346 / 10521 ms** for
  16 + 153, and **6772 / 6833 / 6661 ms** for 16 + 300 at 101 points. Ten
  seconds per character is a frozen application, and 153 ports is the SMALL end
  of what this tool is used on. The two must agree — a namespace that validated
  a spec the composition then addresses differently is the same drift
  `trace_file_labels` is kept mirrored against — and
  `TestTheTwoNamespaceBuildersAgree` is the guard.
- **The stack is CACHED on the App, keyed by the file labels and validated by
  FileEntry IDENTITY.** A label is re-used when a file is reloaded and the
  arrays behind it are then different objects, so a label-only key keeps serving
  the previous parse. The cache is what makes the edit/recompute loop usable at
  all (see the numbers above; `pkg_rlc.physics.compose` measures the re-solve at 2.6 ms
  against 4486 ms for the full path on 316 ports).
- **`marker_hz` is deliberately NOT passed to `compose`.** It would refuse the
  whole composition when the marker falls outside the common span, and the GUI
  already answers that its own way — `snap_to_grid` reports the distance and
  flags `off_grid`. It would also key the cache on a value the user retypes
  constantly.
- **A composed trace's numbers live on the COMPOSED axis, `TraceConfig.net_freqs`.**
  None means "the home file's own sweep", which is what it is for every trace
  that predates this. The two are equal only when nothing was interpolated, so
  the plot, the CSV and the marker snap all read `_trace_plot_freqs`; drawing a
  composed `Z` against the home file's sweep puts the right values at the wrong
  frequencies and looks like a plausible curve. When a composed trace has
  numbers and no axis, `_trace_plot_freqs` returns **None and the curve is
  skipped** — falling back is the failure it exists to prevent.
- **The composed axis is filed in `run.freqs` under the file LEGEND, not under
  either file's label.** The marker landed on an axis neither file has, so a
  header line naming one of them beside that number is exactly the disagreement
  that list exists to remove.
- **The plot legend carries the COUNT (` +N`), not the file names.** R3-4 asks
  that a composed run say which files produced it everywhere it is read, but the
  legend budget is `MAX_LABEL_LEN = 30` characters HEAD-truncated and the tool's
  own default label already overflows it for a 20-character file name; the
  legend `F1=die.s6p + F2=package.s4p` is 30 characters on its own and would
  delete the trace name it qualifies. `_plot_trace_label` trims the BASE and
  keeps the marker — `freeze_label`'s rule and the same reason. The NAMES live
  in the results table's file column, the coupling block's `files:` line, the
  CSV header, the Ports & Roles header and the files window.
- **R3-5 arrives WHERE THE NUMBER IS READ, frozen onto the snapshot.** A weld
  raises nothing and makes no number look wrong (measured in `pkg_rlc.physics.compose`:
  grounded / open / through 1 nH all give `L_eff` = 2.1454 nH, bit-identical,
  spread 0.000e+00), so it changes how the number must be READ and a report
  nobody opened is the wrong place for it. `RowSnapshot` / `CouplingSnapshot`
  carry `ref_strip` / `ref_warn` / `ref_lines`, resolved at snapshot time by
  `reference_provenance` — which renders the one-line strip and the full report
  ONCE so they cannot disagree — and `_run_report_segments` emits them under the
  table, in the Log AND on every run page. There is deliberately **NO second
  printer** at compute time: it put the same paragraph on screen twice, and two
  copies of one verdict are two things that can come to disagree.
- **An attribution of a composition decomposes against a baseline that has the
  cross-file links IN it, and there is no way to turn that off.** R2-8, arriving
  in the window. All-open on a composition leaves the files as disconnected
  islands: measured with the real engine on a 12-port combined network, the
  EM-vs-PKG off-diagonal block of `Ybase` is **0.000e+00**, every package-only
  element contributes **EXACTLY 0**, and the reconciliation residual reads
  **6.49e-15** — perfect health, wrong answer. `_attrib_network` is the ONE
  resolver (`Y`, `freqs`, `nports`, `term`, `baseline`) and it is what every
  call site in the window now goes through; `_attrib_role_rows` scopes the
  provenance rows the same way, or the From column names a row for port 3 of the
  die beside an element on port 3 of the package.
- **A GLOBAL PORT INDEX still reaches two messages, and both are display-only.**
  `Element.describe()` in the Attribution window renders "port 6 → gnd" for what
  the user typed as `F2.2`, and the editor's validation echo says
  `✓ port 6 → GND: 500 mΩ` for the same row (measured, on the two-file run in
  the screenshot). Both are the finding the CLI section already records: an
  element is a stamp on the combined `Y` and knows nothing about files, and
  `_validation_report` echoes the port field it was handed, which is the SCOPED
  one because scoping is what makes the check correct. The number, the port and
  the row are all right; only the spelling is global, and the Attribution
  window's From column and the table row itself both carry what was typed.
  Threading a labeller through `pkg_rlc.physics.attrib`'s construction sites and through
  `_validation_report` is the fix and is not this round's — do not "fix" it by
  un-scoping either, which trades a wrong spelling for a wrong answer.
- **The window is on the MENUBAR and on BOTH right-click menus, never a fifth
  button.** The Files and Traces rows are each measured at 448 px with four
  buttons already asking 364, and a fifth row inside Global Controls comes
  straight out of an editor viewport that is down to 45 px at the 1040x600
  minsize. The Files right-click deliberately does NOT move the file selection:
  the window is about the selected TRACE, and re-selecting a file would change
  what the editor and Ports & Roles are describing as a side effect of a
  question about something else.
- **`SESSION_VERSION` is 2 unconditionally.** The conditional form (1 when
  uncomposed) was implemented and reverted: `tests/test_session.py` asserts both
  that a saved file's version IS `SESSION_VERSION` and that `SESSION_VERSION + 1`
  is refused, which together force the written default and the read cap to be
  one number. It is also the safe side — a v1 reader would drop `file_labels`
  with a note and then compute the home file alone, which is the wrong answer
  this feature exists to prevent.
