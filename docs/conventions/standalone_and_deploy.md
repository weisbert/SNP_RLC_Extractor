# `reduce_snp.py`, the red-zone pipeline, and GUI-test isolation

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### `reduce_snp.py` specifics

- **Standalone, no repo imports.** It runs from a scratch directory on a sim server. numpy + stdlib only. Duplicating the Touchstone parser here is intentional, not an oversight — keep the n=2 column-order quirk mirrored on both sides.
- **Three port buckets, not two.** KEEP becomes an output port; a group named `GND`/`GROUND`/`AGND`/`DGND` is shorted to the reference node (**delete that row and column in Y**, because V=0); everything unlisted is Schur-eliminated. Grounding is *not* the same as opening — PKG ground balls need the GND group or the result is wrong.
- **`# TIE:<name>` is a WIRE, not a fourth bucket, and that is the whole design.**
  It ties its ports into one node (`merge_node_index` Union-Find, then
  `merge_tied_nodes` does `Y' = TᵀYT`); the node then goes through the ordinary
  KEEP / GND / eliminate rules, so "name one member in a KEEP group and the whole
  node becomes ONE output port" and "name one in GND and the whole node is
  grounded" cost no new code. Tying is **not** opening each pin: open is I=0 per
  pin, a floating wire is one voltage with the currents summing to zero.
  Measured on the fixture `tie_demo_network` builds (the only path from port 1 to
  port 2 runs through the tied pins): `S21` is **exactly 0** opened, **−10.56 dB**
  tied, and the tied answer agrees with a network rebuilt with those pins as one
  node from the start to **1.4e-16**. `T` is real, so the merge is a congruence
  and cannot break passivity — `check_passivity` needed nothing. Two matmuls, not
  `np.add.at`: that rule is `pkg_rlc.physics.solve`'s because `golden_legacy.npz` pins the
  summation order bit-for-bit, and nothing pins this path — it is new.
- **`SHORT` / `SHORTED` are REFUSED, naming both routes.** They used to be GND
  aliases, and `SHORT` is the word a user reaches for to say "tie these pins to
  each other" — reading it as "tie them to the reference node" raises nothing and
  produces a plausible wrong answer. The refusal says `# GND` ties to the
  reference node and `# TIE:<name>` ties to each other.
- **A tie group MUST be named, and the two ways a wire silently rewrites the
  output file are refused by name.** Two `# TIE` headers merge into one
  OrderedDict entry, so two wires drawn separately would become one node with
  nothing on screen saying so. `_validate_ties` then refuses two KEEP ports on one
  node ("one node can only be one port") and a KEEP port tied to a GND port
  ("that grounds it"); both are legal circuits that raise nowhere downstream —
  the port count just comes out different from what the KEEP groups describe.
- **`matched` stamps its Y0 on the ORIGINAL diagonal, before the merge.** The
  method terminates each *pin* in Z0, so four tied pins are 12.5 Ω on the node,
  not 50. Moving the stamp out of `Y_uu` is bit-identical when nothing is tied
  (same addition, same operands), which `test_no_tie_is_bit_identical_to_the_old_path`
  pins. The `matched`-with-no-GND sub-matrix fast path is disabled the moment a
  tie exists — a wire changes the network, so the shortcut is simply wrong there.
- **`tie_node_fates` is the ONE authority on what happens to a tied node**, read
  by the console summary, the mapping report and nothing else. Two printers would
  be two things that can come to disagree — the R3-5 rule, in this file's own
  terms. It follows transitivity, so a fate is a property of the NODE, not of the
  group the ports were typed in. `tests/test_reduce_snp.py::TestTiedPorts` and
  `::TestTieConfig` are the guards; every claim above was mutation-checked.
- **A range token must be numeric END TO END.** `4:1:17` (`start:step:stop`, mirroring
  the GUI's `parse_port_range`) and `6-14` are ranges; anything else goes to the name
  resolver, because `-` and `:` are ordinary characters in a net name (`VDD-1`,
  `I0:VDD`) and this is the one parser in the repo where numbers and names share a
  token slot. A token that is both a valid range **and** an exact port name is refused,
  not guessed. Unlike `parse_port_range`, a range that expands to nothing (`17:1:4`)
  is an **error** here — in a config file a silently-empty group is a wrong answer with
  no symptom, and `_fmt_ports` collapses the echo back into runs so a 54-ball GND group
  stays one readable line. `tests/test_reduce_snp.py::TestPortRanges` is the guard and
  all five behaviours above were mutation-checked.
- **The port config is a HAND-WRITTEN file and is read as one.** Every failure below
  rendered as `31:1:52` in an editor and was refused as *"neither an integer, a port
  range, nor a known port name"* — the user is looking at a line that is already
  correct, which is what made the message unactionable. `read_config_text` sniffs the
  encoding (Notepad's "Unicode" is UTF-16 and read as `' 3 1 : 1 : 5 2 '`; its "UTF-8"
  writes a BOM that glued itself to the leading `#`, so the first group HEADER parsed
  as data and every ground ball landed in the keep group; a GBK comment raised nothing
  and ate its line). `normalise_config_line` folds the full-width punctuation a CJK
  input method produces (`：，、；－–—＃！＝`, NBSP, a stray BOM) — none of which is legal
  in a port name. Full-width DIGITS and U+3000 are deliberately **not** in that map:
  `\d`, `\s` and `int()` are Unicode-aware already, so an entry would be dead code and
  would stop the tests noticing if a regex were ever narrowed to `[0-9]`. A `#` after
  the ports starts a comment (`(?:^|(?<=\s))#`, so a port *named* `NET#3` survives) —
  before this the module's OWN docstring example, `1, 2, 3, 4:1:17, 80  # start:step:stop`,
  was a spec this parser refused. `describe_bad_token` is what a leftover says: the
  offending character **by code point** (it is invisible on screen), or for `31:52` the
  exact spelling to type (`'31:1:52'` or `'31-52'`) rather than the rule. `31:52` stays
  unsupported on purpose — `parse_port_range` refuses it too, and a config that works
  in one and not the other is worse than a clear refusal.
  `tests/test_reduce_snp.py::TestConfigFileIsReadAsWritten` is the guard; every case is
  mutation-checked, and the utf-8-BOM one goes red only when BOTH halves (the sniff and
  the U+FEFF map entry) are reverted, which is deliberate redundancy.
- **`--keep` / `--gnd` reach the SAME code path as a config file.** They build the
  `{group: [token]}` mapping `parse_port_config` returns (`groups_from_cli`) and go
  through `resolve_port_config` — one resolver, one set of error messages. `--keep` is
  repeatable and takes an optional `NAME=` prefix so it can express several KEEP groups;
  a reserved ground name there is refused rather than silently becoming a GND group.
  Either source may be given, or both (the file first, inline merged on top).
- **`--method matched` with no GND ports == plain S sub-matrix.** Proven in `test_matched_equals_submatrix`; the code takes the sub-matrix fast path there. Terminating in Z0 == adding `Y0=1/z0` to the unused diagonal before elimination.
- **Do NOT use `np.fromstring(sep=' ')` in the parser.** It is ~9x *slower* than `float()` on numpy 2.x and truncates silently on a bad token. The `array.array('d')` + bounded staging list + `np.frombuffer` view is the measured optimum (2.5x less peak memory than a list-of-floats for +16% time).
- **Build `s` via `s.real = ... / s.imag = ...`,** never `raw[...,0] + 1j*raw[...,1]` — the latter allocates two full-size complex temporaries and doubles peak memory on multi-GB files.
- **Output defaults to `RI` with 12 significant digits.** DB output loses precision on small entries; on a 4-port fixture this default is ~300x more accurate than the old `DB`/`%.10g` combination.
- **Frequency-batched everywhere** (`--batch`, default 256) so a 153-port file doesn't materialise every Y matrix at once. `s_to_y`/`y_to_s`/Schur all operate on stacked `(F,N,N)` arrays via `np.linalg.solve`, with a per-frequency `lstsq` fallback in `_solve_batch`.

### `deploy/` specifics (red-zone pipeline)

- **The package is a blacklist, not a whitelist.** `git archive` ships everything
  except what `.gitattributes` marks `export-ignore`. New scripts are packaged
  automatically — do NOT convert this to an explicit file list, that was the
  design requirement.
- **Shell scripts must be LF in the git index.** CRLF there is the one mistake
  that bricks a deploy (`bash: $'\r': command not found`). `.gitattributes` pins
  `*.sh text eol=lf`, and `pack.ps1` aborts if the index ever disagrees. Keep both
  halves of that guard.
- **`git archive`, never the working tree.** Packing from committed blobs is what
  makes the package immune to autocrlf, backslash paths, and lost exec bits.
- **`pack.ps1` emits exactly two files: the tarball and its `.sha256`.** It also
  emitted a loose, hash-named `reduce_snp_<short>.py` "fast lane" for copying
  onto a sim server; that was removed because a `dist/` with four files in it
  made the operator ask what the extra ones were, and the answer ("the same file
  again, for a workflow you may not have") did not justify the question. The
  sim-server case is `tar -xzf <pkg> Snp_analyzer/reduce_snp.py`. Do not
  reintroduce a second delivery artifact without a use case that cannot be
  served from inside the package.
- **`cmd.exe` is resolved from `%ComSpec%`, not the PATH.** The remaining
  `cmd.exe` call (the CR-byte preflight, which redirects `git archive` to a probe
  tar) must not depend on `C:\Windows\System32` being on the PATH — a yellow-zone
  box whose PATH had lost it failed with "The term 'cmd.exe' is not recognized",
  which reads as a script bug rather than a broken environment. PowerShell's own
  capture cannot replace the redirection: it re-encodes the stream and would turn
  LF into CRLF.
- **`deploy.sh` touches only the install dir**, never the parent. Preserves
  `.deploy/` plus anything in `.deploy/preserve.list`, and rolls back via an `ERR`
  trap if the swap fails halfway.
- **`SENTINEL` is `pkg_rlc_extractor.py`, and that is why the root shim may never
  be deleted.** The post-swap check `[[ -f "$TARGET/$SENTINEL" ]]` trips the
  `ERR` trap and rolls the whole install back if it is missing. So the shim left
  at the repo root by the package move is not only the entry point every doc
  names — it is what tells `deploy.sh` the swap produced an install rather than
  a pile of files. "Tidy up that pointless shim" would roll back every red-zone
  deploy, on the far side, where nobody can debug it.
- **Uploaded deliveries ROTATE: `KEEP_PACKAGES = 2`.** They used to accumulate
  forever ("never removed"), and after a handful of updates the install dir was
  mostly tarballs and the `(ignoring …)` list was longer than the output that
  mattered. Three rules make it safe and all three are load-bearing: rotation
  runs **only after the swap succeeded and the sentinel check passed**, so a
  corrupt package or a failed swap destroys no delivery (measured: a bad
  checksum leaves all four in place); the tarball being deployed is **never**
  removed, checked by resolved path and not merely by being newest; and the
  `.sha256` sidecar goes with its tarball. 2 rather than `KEEP_BACKUPS`' 3
  because deliveries are the SECONDARY rollback route — `.deploy/backups/<ts>/`
  holds three whole installs and needs no untarring, so a delivery only has to
  cover "re-deploy one version back without another transfer across the air
  gap". `KEEP_PACKAGES=0` restores the old keep-everything behaviour.
- **THE `deploy.sh` THAT RUNS IS THE ONE ALREADY ON THE BOX, not the one in the
  tarball** — the swap replaces the script mid-run and bash keeps reading the
  old one through its open fd (there is a note in the file about why that is
  safe). So any change to `deploy.sh` takes effect on the deploy AFTER the one
  that delivers it. Worth saying out loud when a change to it is expected to fix
  something the operator is seeing right now: it will not, on that run.
- **Nothing may be written outside the install dir** — no `/tmp`, no `/opt`, no
  `mktemp`. All staging, backups and scratch go under `<install>/.deploy/`. This
  is an operator requirement, not a preference; `doctor.sh` uses `.deploy/tmp`.
- **Rollback must distinguish backup-phase from install-phase failure** (`PHASE`).
  A partial backup does NOT license deleting what is still in the install dir —
  those are the only surviving originals. Collapsing the two branches silently
  destroys the install; there is a regression test for this in the commit history.
- **Neither the install dir name nor the package root name is hardcoded.**
  `deploy.sh` treats its own directory as the install, and auto-detects the single
  top-level dir in the archive. `pack.ps1 -Name` sets the package root
  (default `Snp_analyzer`).
- **No-argument deploy is the primary path.** `bash deploy.sh` picks the newest
  `*.tar.gz` in the install dir and prints which it chose. Keep the explicit-path
  form working as an override.
- **The far side has no network, no pip, no venv.** Never add a dependency that
  cannot be assumed present; `numpy` is the only hard one. Anything new that the
  GUI needs must degrade gracefully, and `deploy/_env_check.py` must learn about
  it so `doctor.sh` reports the right tier.
- **`_env_check.py` is parse-compatible with Python 2** on purpose, so an ancient
  interpreter reports itself as unusable instead of throwing a `SyntaxError` that
  looks like a corrupt package. No f-strings, no annotations in that file.

### Hiding the GUI tests (`tests/_isolated_desktop.py`)

87% of this suite drives real Tk, so a full run throws hundreds of windows on
the screen and takes the keyboard off the user for the length of the run.
`docs/test_isolation.md` is the finding and carries the reproduction commands;
every claim below was measured on this box.

- **Windows 11 "virtual desktops" (Win+Ctrl+D) DO NOT isolate** — a new window
  lands on the ACTIVE virtual desktop and focus stealing crosses. Do not
  re-propose them. What works is a Win32 **desktop OBJECT** (`CreateDesktopW`
  plus `STARTUPINFO.lpDesktop` on `CreateProcessW`, stdlib `ctypes` only — the
  red zone is numpy-only, so no new dependency is available). It is the
  mechanism UAC's secure desktop uses.
- **It is a full YES, not a partial one.** All 23 Tk-driven modules pass there,
  **1401 tests**, run both ways at `-j6`: *"modules where isolation changed the
  outcome: NONE"*. That includes `test_attrib_window`, which builds a second
  `App` at `tk scaling 2.0` with every named font x1.5 and is the one module
  that reads the clipboard.
- **The measured numbers do not move: 40 of 40 identical, DIFFS = 0** (20 values
  per scaling, at 100% and at 150%), including every figure this file pins —
  `_ed_canvas` 431, `_ed_form` reqwidth 417, left panel and `sashpos(0)` 460,
  `results_nb.winfo_reqheight()` 172, connections table 400, and the glyph
  widths the tables are built on (TkDefaultFont `' '` 4 / `-` 5 / `+` 9 / `.` 3
  / digit 7 / `M` 12; Consolas 9 all 7). `winfo_screenwidth/height` are
  unchanged at 2048x1152.
- **It cannot steal focus, and that was measured WITH A CONTROL.** A rude window
  (`deiconify` + `lift` + `-topmost` + `focus_force`, 30 times) against a victim
  on the interactive desktop, 40 foreground samples: same desktop
  `{'STEALER': 30, 'VICTIM': 10}`, desktop object `{'VICTIM': 40}`. Without the
  control, "no steal" is indistinguishable from a broken test.
- **A GREEN "OK" IS NOT EVIDENCE THAT Tk RAN, AND NEITHER IS THE TEST COUNT.**
  Every Tk module guards itself with `@skipUnless(TK_OK, …)`, and `unittest`
  counts a SKIPPED test in its `Ran N tests` line and still prints `OK` — so a
  total failure to reach Tk looks exactly like a clean pass with a matching
  count. Check the skip count itself (measured: zero). This bit the first draft
  of the isolation doc, which claimed the count was the guard.
- **TIMING HERE MUST BE MEASURED INTERLEAVED OR NOT AT ALL.** A sequential
  "arm A then arm B" on this box reported the desktop object **2.6x to 9.7x
  SLOWER** (4.72→12.34, 8.89→86.42, 27.26→167.53, 157.07→416.18 s) — every one
  an artifact of sibling agents' load ramping between the arms, with 18 python
  processes on 20 cores. Alternating the arms and taking the MINIMUM reverses
  the sign: the desktop object is **0.73x**, i.e. slightly FASTER, having no
  compositor to paint through. Same discipline as the `run_parallel.py`
  docstring's "read the exit code, not the clock".
- **The launcher must carry `creationflags` through, or it silently undoes the
  priority work.** `run_shard` spawns every shard `BELOW_NORMAL_PRIORITY_CLASS`
  so the suite gives way to the user at the CPU; `CreateProcessW` has its own
  `dwCreationFlags`, so a launcher hardcoding 0 puts every shard back to NORMAL
  with nothing on screen saying so. Verified on the child's own
  `PriorityClass`: `0 -> Normal`, `BELOW_NORMAL -> BelowNormal`. The two
  mechanisms are complementary — priority is CPU contention, the desktop object
  is windows and focus — and neither replaces the other.
- **It is NOT wired into `tests/run_parallel.py`.** The change is one call site
  (`run()` returns a `subprocess.CompletedProcess`, so it is a drop-in for the
  `subprocess.run` in `run_shard`), and it must be guarded on `available()`
  with the plain spawn as the fallback and a flag for a developer who WANTS to
  watch the windows. See "Wiring it in" in `docs/test_isolation.md`.
- Not auto-discovered: the leading underscore, the `_golden_capture.py` /
  `_render_capture.py` / `_smoke.py` precedent — `discover_shards` globs
  `test_*.py`. Output capture uses inheritable temp FILES, not pipes (a pipe
  needs a reader thread per stream or a shard's stderr fills it and the child
  blocks), and quoting goes through `subprocess.list2cmdline` — a hand-rolled
  "quote it if it has a space" sends `-c print("hi")` through unquoted and the
  child receives `print(hi)` (measured: rc 1, empty stdout).
- **A hung child on a desktop object is INVISIBLE** — no window to notice,
  nothing to click. Use `run(..., timeout=)`, which terminates it and raises
  `subprocess.TimeoutExpired`.
