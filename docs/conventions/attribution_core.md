# Port attribution and the cold-start screen (the engine)

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### Port attribution (`pkg_rlc/physics/attrib.py`)

Design note: `docs/design_port_attribution.md`. Theory: `docs/theory.md` §13
(and §13.14 for the cold-start closed form). User docs: Help → Mode 6 →
"Where the number came from", and the README's "Port attribution" section.
`tests/test_attrib_core.py`, `tests/test_attrib_vs_engine.py` and
`tests/test_attrib_degenerate.py` are the guards, and every claim below was
mutation-checked.

**All five stages are shipped.** 1-3 are the engine, the sensitivity / Möbius
layer and the CLI report (`--attribute` and its flag group in
`pkg_rlc_extractor.py`, `--mode coupling` only). Stage 4 is the `Toplevel` —
`pkg_rlc/panels/attrib_gui.py`, and see "The Attribution window" below for its own
rules. Stage 5 is the cold-start screen, which is in `pkg_rlc/physics/attrib.py` and
is **CLI-only** — see "The cold-start screen" below. The CLI-before-GUI order
was deliberate and is worth keeping in mind for anything added here: the
output of this feature is a table and a paragraph, both of which a CLI can
print, and every pixel in the GUI is already spoken for by the measurements
elsewhere in this file.

**Why it exists at all.** The user extracted `M` between the same two coils
from the same EM solve twice and got 1.71 pH and 3.44 pH — **6.07 dB apart**,
both correct. 6.1 dB of that was the grounding assumption and 0.6 dB the
frequency marker. `compute_z_matrix` returns the OPEN-circuit matrix and
everything unlisted is open; that convention was stated in `theory.md` §8.5
and **nowhere on screen**. Every rule below is there because the alternative
was measured and was worse.

- **`Zbase`, never `Z0`.** `Z0` already means the reference impedance
  everywhere in this repo. The collision is a real bug source, not a style
  preference.
- **The baseline is: probe sides merged, EVERY other port OPEN.** Nothing else
  — no ground, no short, no lumped element. Every non-probe declaration is an
  element on top of it. Change that definition and every term changes, which
  is why the report names the baseline it used on every line of output.
- **`Zt` is the element IMPEDANCE matrix, so an ideal element is `0` and NO
  INFINITY EVER ENTERS THE ARITHMETIC.** The Woodbury identity in the `D = Zt^-1`
  form has `D = inf` for an ideal ground; in the `Zt` form `H = Zt + G` is well
  conditioned whenever `G` is. `H` is also the only matrix inverted on this
  path, which is what makes `cond(H)` the right thing to gate the tolerance on.
- **`r_a` is ITS OWN SOLVE, never `p_a`, and the transposes are plain `.T`.**
  Reciprocity is not assumed. The user's real file sits at `3.41e-10`, a
  thousand times the residual this feature advertises, so aliasing them would
  silently spend the whole error budget. `.conj().T` is the easy numpy slip and
  is simply the wrong operator: `Y` is complex-**symmetric**, not Hermitian.
  `|r_a - p_a| / |p_a|` is reported as a diagnostic. The repo has **no fixture
  that can catch this shortcut** — that is precisely why it is written down.
- **`Zt` MAY BE DENSE, and the default ground topology must not be diagonal.**
  Real package ground balls share a return plane. `N` independent `z` in
  parallel is `z/N`; `N` balls sharing one `z` is `z`, so independent
  per-lead inductors understate the common-mode return inductance by
  `(1 + (N-1)k_ret)` — `4.8x`, i.e. **13.6 dB**, at 20 balls with a realistic
  `k_ret = 0.2`, and it GROWS with the ball count. Measured on three different
  networks: **9.60 dB** (review,
  synthetic 4-ball), **8.09 dB** (design note §5.2, independently constructed
  4-ball), **6.03 dB** (`diff_pair_4port.s4p`, `agg=1`/`vic=2`, grounds 3+4,
  5 GHz: 1.0120 nH independent vs 2.0259 nH shared). Every one **larger than
  the 6.07 dB dispute this feature exists to settle**, monotone in `k_ret` with
  no threshold, so there is no safe default and the module refuses to pick one:
  `termination_impedance_diagonal` and `termination_impedance_shared_return`
  are both explicit. `H = Zt + G` takes a dense `Zt` with zero math change and
  zero cost change. The same physics is spellable in Mode 5 TODAY with no new
  code — one `short_to` row tying the set, one `lumped_to_gnd` on any port of
  it — and the two spellings of *which* port carries the inductor are
  **bit-identical** (measured, `==`), because the set is one node by then.
- **The reconciliation compares TWO ALGORITHMS ON ONE NETWORK, so it is always
  taken on the DECLARED configuration — never on a what-if.** `ctx.Zop_declared`
  exists for exactly this and is the left-hand side of the residual whatever
  `zt` is in force. Comparing the what-if's answer against the engine's value
  for the declared spec compares two *networks*, and the difference is one the
  caller asked for: measured on `diff_pair_4port.s4p` (probes 1/2, grounds 3/4,
  5 GHz), a shared 1 nH return doubles M, so the residual read **1.01**, sailed
  past `RESIDUAL_CATASTROPHIC`, emptied `terms`, and printed *"the two
  algorithms disagree about the answer itself"* about **2.026 nH, which was
  right** — i.e. requirement 2's headline feature could never produce the split
  it exists for, at the setting that matters most. A plain `diagonal` `zt` was
  the quieter half: 0.2% is under the gate, so the table survived and the
  spurious `Reconciliation:` warning printed anyway. Do NOT "fix" this back by
  exempting `zt` from the gate — the arithmetic is still checked, on the
  declared configuration, which is one extra `O(m^3)` solve and is the check
  that was always meant.
- **What a dense `Zt` genuinely loses is the second OPINION, not the check, and
  `reference_applicable` is how that is said.** `compute_z_matrix` cannot be
  handed a dense element-impedance matrix at all — a shared return is a mutual
  impedance between ground leads and the DSL has no node to hang one on — so
  `total_reference` stays the **declared** spec's answer and is re-labelled
  *"compute_z_matrix, DECLARED spec — a DIFFERENT network"* rather than printed
  under a heading that claims to be the same measurement. `ctx.notes` says
  compute_z_matrix was NEVER ASKED ABOUT THIS NETWORK. Both halves matter:
  dropping the engine's number hides a comparison the reader wants, and leaving
  it unlabelled is a lie.
- **A SINGULAR baseline auto-recovers; it must never refuse.** Measured:
  `coupled_4port_float.s4p`, the repo's flagship Mode 6 example (used in
  `theory.md` and the README), has `cond(Y) = 2.5e16`, so `inv(Ybase)` does not
  exist and a naive implementation is red on day one. Recovery is automatic and
  introduces no new user concept: SVD `Ybase`, partition elements by whether
  `u_e` is in `range(Ybase)` using core's existing `PROBE_RANGE_TOL`, fold the
  OUT-OF-RANGE ones into the baseline, Woodbury the rest, and **report by
  name** — "Port(s) X are IN THE BASELINE because the structure has no
  reference without them". A folded element has no term of its own, which is a
  gauge change (see below) and is why naming it is not optional. Measured with
  one `4 lumped_to_gnd R=50` on that fixture: effective cond `7.3e15 -> 5.7`.
  With no elements at all there is nothing to fold, so `Zbase` is a `pinv` and
  the balanced `+/-` probe is orthogonal to the common-mode null direction —
  exact, residual `0.0`, effective cond `2.2`. Same argument as §8.4.
- **STRUCTURAL rank check BEFORE any conditioning check.** A rank-deficient `U`
  from a redundant spec — one port written `ground` twice through overlapping
  ranges, a `short_to` between two already-grounded ports — is a **SPEC BUG**,
  and reporting it as "genuinely unattributable physics" is the worst available
  outcome. Test it on integer port-index sets first and NAME the offending
  elements; only then look at `cond(G)`. Elements whose `u` is the zero vector
  after probe-side merging are dropped as already inert — the same class
  `inert_lumped_messages` reports on the Mode 5 strip.
- **Reconciliation DEGRADES, never refuses outright, and its tolerance is
  CONDITION-AWARE.** The authoritative total is always `compute_z_matrix`'s;
  the decomposition's own sum is the check. Measured cross-algorithm agreement
  is `3e-16` on a trivial 4-port and `~1e-7` at best on a 153-port file with
  `cond(Ybase)*cond(G) ~ 1e7-1e9`, so a **fixed `1e-9` gate would refuse
  exactly the files this exists for**. The gate is
  `RESIDUAL_SAFETY * (cond(Ybase) + cond(H)) * eps * (S / |Z_ab|)` and the
  `S/|Z_ab|` factor is NOT decoration: measured on `diff_pair_4port.s4p` at
  1 MHz, `cond(Ybase) = 1.3e10` and `cond(H) = 1` give a cond-only bound of
  `4.5e-5` while the actual disagreement is `0.25` — an inverse is accurate
  relative to its LARGEST entry, and there the largest entry of `Zbase` is the
  1 fF port capacitance's 159 kΩ while the answer is a 6 mΩ mutual. Report the
  residual AND its achievable floor; only withhold the per-element **split**
  when the residual is catastrophic, and **never** the total.
- **The RETURN-PATH BUDGET is always reported, and it is what stops the output
  being over-read.** The EM model's reference plane is not a port, so no
  declaration reaches it. Report `|1^T Ybase V|` against `sum|I_e|`. Measured
  on `diff_pair_4port.s4p` with both far ends grounded the declared elements
  carry 99.41%, but on the representative package case the split was **0.05%
  declared / 99.95% inside the EM model** — so the decomposition **cannot**
  confirm or refute a "forward path minus return path" hypothesis and must
  print that in words rather than let a user infer a null result from small
  numbers.
- **The SHARE of a complex term is not a complex ratio.** Report the signed
  projection `Re(term * conj(total)) / |total|^2` PLUS a separate quadrature
  component. A term at 90° to the total inflates any magnitude-based
  cancellation measure while being harmless. Suppress the share column
  entirely, **with a named reason**, when `|total|` is near zero — including
  when it is smaller than the reconciliation residual, because a total smaller
  than our own error bar is not a total.
- **ONLY DECOMPOSE WHAT IS DECOMPOSABLE, and refuse the rest BY NAME.** A
  quantity decomposes iff it is (fixed real scalar) x (R-linear functional of
  `Z_ab`) at ONE configuration. YES: `Z`, `ReZ`, `ImZ`, `M`, `M/L_a`, `k`. NO:
  `C_c = -1/(omega*Im Z_ab)` (a reciprocal — superposition adds impedances, not
  their inverses), `Q` (a ratio of two decomposable things), `|Z|` (a norm),
  anything in dB. **`C_c` is a first-class output of this tool** and is the
  right reading whenever `Im(Z_ab) < 0`, so it must still be shown — as a
  TOTAL only, never per term — `Decomposition.C_c_total` and one line in
  `format_decomposition`, headlined when `Im(Z_ab) < 0`. The refusal names the
  quantity and the linear one to ask for instead; "unsupported quantity" would
  send the caller hunting for a typo, and a refusal pointing at a facility that
  does not exist is worse still.
- **"At ONE configuration" means fixed WITHIN one evaluation, NOT frozen at the
  declared spec.** `M/L_a` and `k` divide by `L_a` (and `L_b`), and those are
  properties of the NETWORK — every sensitivity row, group, cumulative point
  and leave-one-out row is a different network. `_scale_from` therefore takes
  the scale from the `(G, G)` matrix of the configuration being evaluated;
  `_quantity_scale` is the wrapper that supplies `ctx.Zref` for the DECLARED
  spec, so `decompose(..., "k")` still means byte-for-byte what the results
  pane and the CSV print. Measured on `diff_pair_4port.s4p` at 5 GHz, opening
  `ground port 3` takes `L_a` from `+5.026 nH` to `-505.3 nH`, so the frozen
  scale reported `M/L_a = +0.100227` where the truth is `-0.000996976` —
  **sign flipped, 100x** — and `k = +0.100227` where `L_a < 0` makes `k`
  genuinely NaN by `extract_coupling_at_freq`'s own rule. On the first row of
  the default scan. **`sweep_mobius` REFUSES `k` and `M/L_a` by name**
  (`_SWEEP_REFUSED`): a curve has no single configuration to take a scale from,
  so the only thing it could deliver is that same bug drawn as a graph.
- **Sensitivity must include GROUP-LEVEL and CUMULATIVE, not only per-port and
  pairwise.** With 60 ground balls every single-port delta is ~0 (the other 59
  already carry the return) and so is every pairwise second difference: the
  collective effect is order-60. Even at `m = 2` it bites — measured on
  `diff_pair_4port.s4p`, opening ground 3 alone is `-506 pH`, ground 4 alone is
  `-506 pH`, and **both at once is `-759 pH`, not `-1012 pH`**: non-additivity
  `+254 pH`, a third of the effect from two elements. So: per element, per
  GROUP (a whole connection-table row at once — the rows already define the
  groups, so this is free), the non-additivity for groups AND pairs, a greedy
  cumulative curve at `k = 1,2,4,8,16,…`, and leave-one-out from all-grounded.
  **Every fast low-rank result MUST be verified in tests against an honest
  recompute through `compute_z_matrix` with a rebuilt `TerminationSet`. That is
  the single most important test in the file** — a Woodbury update that agrees
  with itself and with nothing else is this module's characteristic failure.
- **The series-L sweep is a CLOSED-FORM MÖBIUS MAP, not a loop.** `z` enters
  `H` in exactly one entry, so `Z_ab(z) = (alpha + beta*z)/(gamma + delta*z)`:
  exact endpoints (`z=0` ideal, `z=inf` open), the whole interval in closed
  form, and the extremum over `[0, inf)` analytic (a Möbius map takes the real
  line to a circular arc). **The INTERVAL is the headline scalar** ("M lies in
  [1.71, 3.44] pH over any physical ground inductance"); the sampled curve is
  secondary. **The curve need not be monotone and the endpoints are NOT a
  bound** — a series L resonates with the package's shunt C. **The two
  ENDPOINTS are the numbers the user came for** (`M(0)` = ideal ground, `M(inf)`
  = open, the two assumptions the 6.07 dB dispute differed by), and both are
  exact. Re-measured on `diff_pair_4port.s4p` at 5.0005 GHz sweeping ground
  port 3: ideal `1.01 nH`, open `503.7 pH`, one pole at `L = 505.25 nH` (there
  `ctx.Gm[0,0] = -391 µΩ - j15.8745 kΩ`, i.e. 2.005 fF, and 505.25 nH
  series-resonates with 2.005 fF at exactly the 5.0005 GHz being read), and an
  extremum of **±10.28 mH** — `1.0e7` times the bracket. Away from the pole
  (factor-of-two guard) the same curve is `[-2.5 pH, 1.52 nH]`, and at a
  factor of ten it is `[447.5 pH, 1.066 nH]`, still outside the bracket at both
  ends. This bullet used to quote `[504 pH, 1.18 nH]` as the actual range,
  which predates the pole-seeded extremum search and is what the code no longer
  says: **an interval quoted over the whole half-line is the pole, i.e.
  arithmetic; the pole-free interval is the answer, and the pole is reported
  separately by its `L`.** On an UNBOUNDED sweep an extremum `NEAR_POLE_RATIO`
  past the bracket is a near-pole, not a design margin. **`bracket` must be in the SAME quantity as
  `interval`** — for the complex `quantity="Z"` the interval is of `|Z|`, so
  the bracket has to be too: measured with `t_max=20 nH`, the real-part
  spelling put `(-2.49 nOhm, 376 pOhm)` beside an interval of
  `(31.7 Ohm, 32.4 Ohm)` and announced a `1.3e10`-times-the-bracket near-pole
  that does not exist.
- **The sweep is evaluated from its PARTIAL FRACTIONS, never from the expanded
  polynomial.** `Z(t) = c0 - sum_j residues[j]/(lam[j] + t)`, with the poles at
  `t = -lam[j]`; `t -> inf` is exactly `c0` and `t = 0` is one sum, both
  overflow-free at any `|S|`. Expanding it multiplies `|S|` eigenvalues
  together, and with `param="L"` each is of order `1e-9`: measured on a
  synthetic package sweeping one ground group, `den[-1]` is `5.98e-273` at 30
  balls, `3.70e-309` at 34 and **exactly 0 at 36** — so `value_ideal`, which
  was `num[-1]/den[-1]`, printed `+inf` at 36, `NaN` at 38 and `NaN` for the
  whole curve at 60, with `method` still saying `"closed-form"` and `notes`
  empty. **Requirement 9 is written around 60 ground balls.** `num` / `den`
  survive as DIAGNOSTIC fields and are EMPTIED (with a note) rather than left
  holding underflowed garbage — two redundant halves, `_EXPAND_MAX_DEGREE` and
  the `den[-1] == 0` check, either of which alone does the job.
- **The extremum search SEEDS FROM THE POLES and then polishes; `np.roots` on
  the expanded critical polynomial is not enough.** Every candidate is a point
  the curve really passes through, so the interval is always ACHIEVED and can
  only ever be too narrow — that one-sidedness is what makes extending the
  candidate set safe. Measured on `diff_pair_4port.s4p` at 5 GHz sweeping BOTH
  grounds as one group over `L`: the two poles sit at `5.05000e-7` and
  `5.05503e-7`, **0.1% apart, both on the positive real axis**, and the
  degree-4 critical polynomial's roots found neither — reported
  `(+7.46e-21, +2.138e-3) H` against a true `(-5.187, +5.187) H`, i.e. the
  maximum `2.4e3x` too small and the minimum **the wrong sign**. The
  single-element sweep on the same file was correct throughout, which is why it
  went unnoticed: the defect needs `|S| >= 2`, i.e. exactly requirement 9b's
  "change a whole connection-table row". Seeds are `Re(p) +/- c*|Im p|` for
  `_POLE_SEED_OFFSETS`; the Newton polish on `Z'` / `Z''` (partial-fraction
  form, no expanded coefficient anywhere) is what reaches a BROAD extremum that
  sits on no seed — measured on a two-pole case, `-0.058971` without it against
  `-0.059261` with it and on a 2M-point grid.
- **The SIGN CONVENTION is declared globally and in every export**
  (`SIGN_CONVENTION_TEXT`, one string so exports carry it verbatim). Victim
  reference = `V(+) - V(-)` of the victim port; aggressor driven `+1 A` into
  its `+` side; `I_e > 0` flows OUT of the structure into ground for a shunt
  element (`u = e_p`) and from `p` to `q` for a series one (`u = e_p - e_q`).
  Flipping either measurement port's `+/-` flips every term together:
  **relative** signs between terms are physical, absolute ones are a labelling
  choice. Same rule and same reason as `M`/`k`/`C_c` in core.
- **Replicate `compute_z_matrix`'s PRECEDENCE EXACTLY.** Modes 1/2/3 let a
  `Ground` beat a `Signal` (`merge_terms`, pinned by
  `TestTerminationPrecedence`); `build_terminations_coupling` raises on the same
  overlap. `_normalize_signal` is imported from core **on purpose** —
  reimplementing the legacy "B == minus side of A" alias here is exactly how
  the two would drift, and the symptom would be a reconciliation failure on the
  specs the reconciliation exists to guard.
- **The contribution table is a ranking of DECLARATIONS, never of PORTS.** A
  port that is open contributes no element and therefore no term — it is
  **absent**, not small. A table headed "contributions by port" that omits the
  45 open ports of a package is a wrong answer with a plausible shape. Only the
  sensitivity side reaches ports the user has not decided about, and it does so
  by hypothesising a termination. State this in the docstring AND in the report
  header, in those words. Related distinction the reviews surfaced and the docs
  now carry: **a port left open because the SIMULATOR owns it is a different
  thing from a port left open because nobody decided, and only the first is
  safe** — the two are indistinguishable in the file, in the `TerminationSet`
  and in the table, which is what the Ports & Roles open-port name check exists
  to catch.
- **AN UNDEFINED READING SORTS LAST, ON EVERY SURFACE, AND IS NEVER FOLDED
  AWAY.** NaN is a **missing measurement, not a small number** — a probe with
  no return path, a port past its SRF — and it is the one row the reader most
  needs to see, so it goes to the bottom of the ranking and stays on the
  screen. This is ONE rule with four implementations and they are pinned
  against each other, not each against a literal: `rank_coupling_pairs` in
  core (which states it), `_fold_terms` in `pkg_rlc.panels.attrib_gui` (which keys the
  case at **`+inf`**, because an element whose contribution is exactly `0` is
  an ordinary reading — an annihilated lumped element — and `-0.0` compares
  EQUAL to `0.0`, so a NaN keyed at `0.0` ties with it and a stable sort puts
  the missing measurement on top), `_attr_print_sensitivity` in
  `pkg_rlc.present.attrib_report` and `sensitivity_table` in `pkg_rlc.panels.attrib_gui`
  (both `(0 if isfinite else 1, -abs_delta)` — **the same spelling on purpose**,
  so the two read alike to anyone comparing them). The window was the odd one
  out and contradicted not only the other three but ITSELF: it keyed a
  non-finite delta at `float("-inf")`, which is the **SMALLEST** key on an
  ascending sort, so the row that measured nothing printed **above the
  strongest real effect in the table** — on the surface a user reads before
  deciding which port to go and fix, twelve hundred lines below `_fold_terms`
  getting the identical case right in the same file. Whatever a later session
  does here, do not "simplify" any of the four into a bare `-abs_delta`: the
  ordering is only visible on data no shipped fixture produces, which is
  exactly why it went unnoticed. `tests/test_attrib_window.py::
  TestSensitivityRanking` is the guard and pins the window against the CLI
  directly; the reference case is `sensitivity_fake_undefined_delta`, which is
  **hand-built because no `.sNp` in this repo reaches the branch** — before it,
  all four captured sensitivity cases held finite deltas and the golden
  reference could not see the ranking move at all.
- **The split depends on how the spec is SPELLED, and that cannot be fixed.**
  `6:1:14 ground` (9 elements) and `6 short_to 7:1:14` + `6 ground` (8 shorts +
  1 ground) are the same network, give the same total, and decompose
  differently — they are two different *tearings* of it in the Kron sense.
  Measured on `diff_pair_4port.s4p`: `3 ground / 4 ground` splits as
  `bare 251 pH / gnd3 252 pH / gnd4 506 pH`, and `3 short_to 4 / 3 ground` as
  `bare 251 pH / gnd3 253 pH / short 3-4 506 pH`, both totalling 1.01 nH. Say
  so in the report — a user who reorganises their table for readability and
  sees the contribution column move must find that sentence before filing a
  defect.
- **A NaN residual is NOT a pass — `split_trustworthy` requires
  `math.isfinite(resid)`.** A NaN means nothing was checked at all, and the
  cases that produce one are exactly where the module is most convincing and
  most wrong: measured on `coupled_4port_float.s4p` with only one of the two
  coils referenced, `compute_z_matrix` says NaN ("'c2' has no return path")
  while this module folds the single ground in and reports **400.000 pH —
  exactly half** the fixture's real 800 pH. The warning said "could not be
  measured" and a caller gating on `split_trustworthy` got a green light. The
  TOTAL is still reported; only the apportionment is withheld.
- **A non-finite `Y` at the analysed frequency is REFUSED BY NAME, and so is a
  non-finite caller-supplied `zt`.** The first escaped as numpy's bare
  `LinAlgError("SVD did not converge")` out of `build_context` — no verdict, no
  frequency, no file, in a repo whose `TouchstoneParseError` contract exists to
  answer exactly that question; `compute_z_matrix` survives the same input and
  returns NaN with a warning, so the user still has the engine's reading and
  this module says which frequency it cannot follow it at. The second is the
  only route round the `Zt = D^-1` formulation's guarantee that **no infinity
  ever enters the arithmetic** (contract priority 4): an OPEN element is
  spelled by leaving it out, never by a large or infinite impedance. The dead
  `z_declared[i] = complex("inf")` branch that used to sit in `build_context`
  is gone with a comment saying why it was unreachable — a zero-admittance
  element is dropped as inert before it can get there.
- **A multi-element what-if models the replaced leads as INDEPENDENT unless
  told otherwise, and it SAYS SO.** `group_joint` / `cumulative_curve` /
  `sweep_mobius` take `z_ret=`, which puts one shared return impedance across
  the changed block — measured, that reproduces the equivalent
  `termination_impedance_shared_return` context **bit-identically** (rel `0.0`)
  and lands **6.06 dB** from the independent answer on a two-ball spec. With
  `z_ret = 0` and two or more shunt elements changed, `notes` carries the
  `(1 + (n-1)k)` warning: `build_context`'s DIAGONAL note inspects `ctx.Zt`
  only and therefore cannot see a what-if, which is exactly where the model is
  chosen rather than inherited.
- **`cond(G)` and `cond(Ybase)` are DIAGNOSTICS, not trust signals.** Measured:
  a node space collapsed by a 1 pΩ tie reports `cond(G) = 1.0` — `Gm` has
  underflowed to `~1e-14` times the identity, so its condition number is
  perfect — while `Zop[a, b]` is exactly 0 against the engine's 305 pH. The
  reconciliation residual is what catches that; the condition numbers only
  explain it afterwards. Said in `AttribContext`'s docstring for the same
  reason.
- **The decomposition is GAUGE-DEPENDENT.** Change the baseline and every term
  changes; fold one element in and the rest all move, though the network, the
  total and the physics are identical. What does **not** change is the element
  currents `I_e` — those are physical; the attribution of *voltage* to them is
  a gauge choice. This is PEEC's partial-inductance warning restated, and it is
  the reason the report names its baseline every time: two reports are
  comparable only when their baselines match.
- **Re-terminating existing ports cannot evaluate NEW METAL.** A shield, an
  extra via, a widened return path — none is a termination of an existing port.
  They change `Y` itself and need a new EM run. Worth drawing sharply because
  the sensitivity output looks exactly like a layout-exploration tool and is
  not one.
- **Also expose the EXACT current-transfer ratio.** `-Z_ab/Z_aa`, and an
  optional loaded `-Z_ab/(Z_aa + Z_load)`. `theory.md` §8.8 documents that
  `M/L_a` is only the first-order Norton approximation to it (1098% apart at
  10 MHz for `L=2n, R=1.5`); the user measured 0.87 dB of difference on their
  own file by hand. It is a TOTAL, not a decomposable quantity — `Z_ab` is in
  the denominator.
- **The prior art is named on purpose** (`theory.md` §13.5): Kron diakoptics
  (`H = Zt + G` *is* the connection matrix), the adjoint variable method (`r_a`
  is the adjoint solution — which is why requirement 1 is stated in adjoint
  language), PEEC partial elements (the gauge warning, verbatim), and Norton
  path decomposition / transfer-path analysis. Each of those literatures
  already found the trap the corresponding rule guards against; "this is
  diakoptics, and diakoptics has the following known failure mode" is cheaper
  than rediscovering it.
- **No scipy, and no explicit `inv`.** The contract permitted
  `scipy.linalg.lu_factor` / `lu_solve`; the module ships without it because
  `np.linalg.solve` with a multi-column right-hand side IS one LU factorisation
  plus k triangular solves — exactly what those two buy — and
  `deploy/doctor.sh`'s tiers assume numpy is this repo's only hard dependency.
  Adding scipy would silently move the red-zone bar. Don't, without a
  measurement that justifies it.
- **`compute_z_matrix` is called on a ONE-FREQUENCY SLICE, first**, before any
  attribution arithmetic. Its Schur solve is a gufunc and its contraction is
  already per-frequency, so the slice returns exactly what a full sweep would
  put at that index — microseconds instead of hundreds of milliseconds on a
  5000-point file — and calling it first is what validates the port indices,
  resolves the measurement ports and raises on conflicting signal groups.
  **Nothing in this module may modify `compute_z_matrix`, `_probe_impedance`,
  `_is_singular_2x2` or anything else `golden_legacy.npz` pins.**
- **THERE IS NO ELEVENTH HELP TAB, and that is a measurement, not a
  preference.** `HELP_TOPICS` has 10 tabs and the strip needs **968 px**
  against `HELP_WINDOW_WIDTH = 1010`. Re-measured for this feature (Tk 8.6,
  vista theme, `TkDefaultFont` = Microsoft YaHei UI 9, `tk scaling` 1.333): an
  eleventh tab labelled `Cold start` takes it to **1033 px**, `Attribution` to
  **1037**, `Port attribution` to **1064**. A `ttk.Notebook` CLIPS a strip it
  cannot fit — no wrap, no scroll, no chevron — and the tab that vanishes is
  the **LAST** one, so the new tab would be the invisible one. Everything about
  this feature therefore folds into **Mode 6 (Coupling)**, cross-referenced
  from `Overview`, `Input syntax` and `Worked examples`.
  `tests/test_session.py::TestHelpTabsAllFit` is the guard and re-measures it.

### The cold-start screen (`--cold-start`, CLI only)

Which ports matter BEFORE a spec exists. `tests/test_attrib_coldstart.py` and
`tests/test_attrib_cli_coldstart.py` are the guards; the mathematics is
`docs/theory.md` §13.14 and the rationale is
`docs/design_port_attribution.md` §14.

- **`decompose` is STRUCTURALLY BLIND to this case and that is why the screen
  exists.** With every port open there are no elements, so `m = 0`, `U` is
  empty and the contribution table is empty — and the all-open configuration is
  exactly the one that produced the disputed number. `sensitivity` does reach
  undecided ports but is framed as "check the spec you already wrote", which is
  a different question. Do not "fix" this by folding hypothetical elements into
  `decompose`; the contribution table ranking DECLARATIONS is an invariant.
- **It is READ OFF THE EXISTING MACHINERY, not reimplemented.** One
  `AttribContext` whose `TerminationSet` is "the probes plus one ideal ground
  per candidate" gives all four steps: `ctx.Dmat` is all-open, `ctx.Zop` is
  all-grounded, `ctx.Rmat[e,a]` is `Zbase[a,p]`, `ctx.Pmat_b[e,b]` is
  `Zbase[p,b]`, `ctx.Gm[e,e]` is `Zbase[p,p]`, the one-element solve IS the
  closed form, the two-element solve is the pair scan, and
  `leave_one_out(ctx, …)` is the mirror with no new code. Probe membership
  comes from `_probe_side_of_port` (`merge_terms`' rule), so a short that
  defines a probe side survives the rewrite; every other declaration is dropped
  and **named** in `notes`.
- **The closed form is `dZ_ab = -Zbase[a,p]·Zbase[p,b]/Zbase[p,p]`, and it is
  EXACT.** It is §13.3 with one element and `Zt = [0]`, and equivalently the
  Schur complement of deleting row/column `p`. Verified against an HONEST
  re-solve through `compute_z_matrix` with a rebuilt `TerminationSet`:
  **1.47e-11** worst on the planted 12-port case, `<= 5.8e-11` over every
  fixture; re-measured while writing the docs on `diff_pair_4port.s4p` at
  5.0005 GHz (probes 1/2, candidates 3/4), **7.11e-13** and **8.30e-13**. The
  mathematics needs only **two solves plus the diagonal**; the implementation
  builds the whole baseline once because the other steps need it.
- **BOTH COUPLING COLUMNS ARE MANDATORY AND MUST NOT BE COLLAPSED INTO THEIR
  PRODUCT.** Measured on the planted case: the port with the **largest**
  `|Zbase[a,p]|` in the file (34.777 Ω, 67% more than the real path's 20.873)
  has `|Zbase[p,b]| = 0.038` and a true effect of **-0.378 pH** against
  **-395.369 pH** — ranked on coupling-to-the-victim alone it is FIRST and
  worthless, ranked on `|dM|` it is fifth of eight. The repo's own fixture
  makes the converse point and is the cleaner demonstration: on
  `diff_pair_4port.s4p` at 5.0005 GHz port 3 reads `|Z_ap| = 15953.3`,
  `|Z_pb| = 7.89368` and port 4 reads them **swapped** — a factor of 2021 —
  while both have the **same effect to twelve digits** (`+7.93284 Ω`). Rank on
  either column alone and one of two identical ports is first and the other is
  last.
- **THE PAIR SCAN IS NOT OPTIONAL.** Measured: a shield brought out as two
  ports reads **+9.689 pH** with either end grounded alone and **-870.268 pH**
  with both — **89.8x** the largest single-port effect, with the **OPPOSITE
  SIGN**. A single-port ranking reports it as two minor positive entries.
  `5 short_to 6` with no ground anywhere gives the identical -870.268 pH, which
  proves the mechanism is the closed **LOOP** and not the grounding. The
  algebra says where the surprise lives: with `Zbase[p,q] = 0` the 2x2 `H` is
  diagonal and the two effects add **exactly**, so the non-additivity is
  entirely how much the two candidates talk to each other.
- **THE MIRROR DIRECTION IS ALSO MANDATORY, and it is a different failure, not
  a check.** Leave-one-out from ALL-GROUNDED: 60 ground balls read `~0` each
  because the other 59 carry the return, while the shield reads **+880 pH** per
  end. One-at-a-time-from-all-open and leave-one-out-from-all-grounded catch
  opposite failures (loop closure versus parallel-return saturation) and
  neither subsumes the other. On a large file the mirror is also the expensive
  half — measured **9.3 s of a 9.5 s** four-step report at 151 candidates.
- **A pair is FLAGGED against a threshold that is REPORTED, and nothing is
  hidden by it.** `max(COLD_START_PAIR_REL * largest single-port |delta|,
  COLD_START_PAIR_FLOOR_REL * |all-open value|)` = `max(0.5·…, 0.01·…)`. The
  first term is the one that means something; the second is a floor for the
  case where every single-port effect is `~0` — the normal reading of a shield
  and of 60 balls from all-grounded — without which the first term collapses
  onto the noise and flags all 28 pairs. Measured: planted case threshold
  197.7 pH, **no** pair clears it (largest non-additivity 5.40 pH — correct, no
  pair mechanism was planted); shield case threshold 4.84 pH, the one pair
  clears at 889.6 pH, **184x**. Every scanned pair is returned, ranked, each
  carrying the threshold it was judged against.
- **STEP 0 COMES FIRST AND IS PINNED THAT WAY.** The open..ideal-ground bracket
  answers "is any of this worth my time" before anything else is computed
  (measured **25.67 dB** on the planted case), and
  `test_the_BRACKET_comes_before_the_RANKING` pins that pair on its own. It is
  **not** a bound over all terminations — a reactive termination leaves the
  Möbius arc; measured on `diff_pair_4port.s4p`, one ground's series `L` swept
  over `[0, inf)` peaks at 9 mH of apparent `M` at `L = 505 nH` against a
  1.01 nH bracket. `COLD_START_BRACKET_CAVEAT` is one string so every export
  carries it verbatim, the `SIGN_CONVENTION_TEXT` rule.
- **THE NEGATIVE RESULT IS REPORTED AS A RESULT.** "The other N ports are all
  below X dB, so the coupling is LOCAL" is what lets a designer stop looking,
  and a screen that only prints a top-10 cannot say it.
  `COLD_START_LOCAL_DB = 1.0` is anchored on the 6.07 dB dispute this whole
  module exists to settle: a port that cannot move the answer by 1 dB is not
  part of that argument. It changes no number, only the sentence.
- **A NAME FAMILY IS A PROPOSAL THE TOOL TESTED, NEVER AN ASSUMPTION IT FOLDED
  IN.** The requirement was explicit that which port is ground is a semantic
  judgement and the script must not guess it. So the NUMBERS are computed both
  ways and the GROUPING stays a sentence ("ports 5,6 share the name family
  'guard_ring'; tested together they are -870 pH, tested separately +9.7 pH
  each — if they are one structure, group them"). **Nothing else in the report
  depends on port names at all**, and the test suite pins that by running the
  whole thing twice. `COLD_START_MIN_FAMILY = 2`, deliberately **not** core's
  `OPEN_CLUSTER_MIN_FAMILY = 4`: that threshold keeps a REMNANT check from
  crying wolf about `coil1`/`coil2`, while the case this one exists for is
  exactly a two-member family.
- **WHAT IT CANNOT FIND goes on the screen, not in a footnote.** Anything
  needing THREE OR MORE ports to move together: step 1 is first order in the
  candidate set, step 2 exactly second, and step 3's greedy walk can stumble
  onto a triple with no guarantee. `COLD_START_BLIND_SPOT_TEXT` is the one
  string that says so and it is printed by the report.
- **`context=` is KEYWORD-ONLY on every step, and the four contract signatures
  are positionally exact.** The context is the only `O(N³)` piece (measured
  350.6 ms at 153 ports) and every step off it costs microseconds to
  milliseconds, so building four of them is the one expensive mistake
  available. `cold_start_report` shares it for you.
- **`--cold-start-cumulative` takes an explicit K because a bare "every
  candidate" was a 55-second trap.** Measured at 151 candidates: 132 ms at
  `k = 12`, 237 ms at `k = 24`, **54.9 s** at `k = 0`. Step 3 is always run —
  it is the only step that answers "how many ports matter" and at the default
  it is 132 ms of a 9.5 s report, so there is nothing to opt out of. All three
  cap flags default to `None` so `_cold_dependent_flags` is EXACT where
  `_attr_dependent_flags` (which compares against a substituted default)
  cannot be.
- **`--attribute` and `--cold-start` together are ALLOWED, cold start last.**
  The attribution explains the `M` printed just above it and must stay next to
  it; refusing the combination would only force the file to be read and
  inverted twice (measured 132 ms + 675 ms on a 16 MB 153-port file).
- **Both port names go on a line UNDER the pair table, not in the cell.**
  Measured: `guard_ring1` / `guard_ring2` in one cell truncate to two identical
  `guard_rin~` stumps, because `_trunc` keeps the HEAD — the same failure
  `freeze_label` was fixed for. Putting the names in full underneath took the
  table from 110 to 89 columns as well.
