# The session file and the Help window

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### The session file (Save Config / Load Config / autosave)

The round trip is `pkg_rlc/services/session.py` (L2) and is re-exported from
`pkg_rlc.frontend.app`, so every rule below is unchanged and every call site still
resolves. `tests/test_session.py` is the guard, and every claim below was
mutation-checked.

- **A session file holds the CONFIG, never the results.** `_COMPUTED_TRACE_FIELDS`
  is the blacklist and the saved set is *everything else*, so a new config field
  round-trips without anyone remembering it. That trade is deliberate: a forgotten
  config field silently stops saving and nothing catches it, while a forgotten
  computed field fails loudly (`json.dump` on a numpy array).
  `TestFieldCoverage::test_every_traceconfig_field_is_classified` pins that every
  field of `TraceConfig` is in exactly one of the two sets.
- **Retired fields are written only when non-empty.** Emitting eight empty
  strings per trace buries the ones that matter. Since stage 2 (2026-10-02)
  every trace is migrated as it LOADS, so a saved trace normally carries none
  of them; the rule stays for a session written by a build in between.
- **Every trace is migrated EAGERLY on load, and each one says what moved, in
  the Log** (stage 2 of `docs/design_workspaces.md`, § 3.4). `_apply_session`
  calls `_migrate_trace(tc, refresh=False)` on EVERY trace, selected or not —
  the old "migrate on selection" left unclicked traces carrying dead fields
  into the session file, the run signature and the results descriptor.
  `migrate_trace_to_rows` (`pkg_rlc/model/validate.py`, exposed as
  `TraceConfig.migrate_to_rows`) returns `MigrationNote`s — a `str` with a
  `.level` — and `_migrate_trace` logs each as one line, `[id] label: <note>`,
  at `LOG_WARN` only where the user should look (a number moved, an old
  contradiction was resolved, a retired field folded) and `LOG_INFO` for a
  plain record of what moved, so an old session does not badge the Log once
  per trace. Only the LIVE fields of the old mode are carried; the hidden
  fields of every other mode are cleared. The one intended change of a number
  (a '−' side only PARTLY in GND) and the bit-for-bit claim for everything
  else are pinned by `tests/test_trace_path_golden.py` against
  `tests/fixtures/golden_trace_paths.npz`.
- **`table_version` is a saved int, and it is what makes the migration
  idempotent.** `0` (absent) is an old trace; `1` (`TABLE_VERSION`) is a trace
  already in the one row model, which `migrate_trace_to_rows` never touches
  again — so a spec the user is in the middle of fixing is not "repaired"
  behind their back on the next load. It is in `_TRACE_INT_FIELDS`.
- **`mode` is still written, and it is ALWAYS `5`.** The field stays on
  `TraceConfig` for exactly one reader: an OLDER build opening a new session.
  It reads a missing mode as 1 and would compute `port_a` to GND; reading 5,
  it opens the trace as Custom, which is what the two tables are. Do not drop
  the key and do not write any other value. `MODE_NAMES` survives for the
  migration's Log lines only.
- **Every file is recorded twice and the RELATIVE path wins.** That is what makes
  a session survive the folder being copied to another machine, which is the
  normal way work reaches the red zone; the absolute path is the fallback for a
  config file moved on its own. A test where only the relative path exists does
  NOT pin the precedence — reversing the candidate order still passes it —
  which is why `test_the_relative_path_wins_when_BOTH_exist` exists.
- **`rel_path` is written only when it is shorter than the absolute path.** A
  config saved somewhere unrelated to the data produces a ten-deep `../../..`
  chain that describes no copyable tree, resolves on this machine and nowhere
  else, and is pure noise in the file. `data/coil.s4p` and `../data/coil.s4p`
  both survive the rule, which are the layouts the relative path exists for.
- **A missing file is reported, not fatal.** The traces bound to it stay in the
  list; `_on_calculate` already says `file '…' not loaded`. `_apply_session` also
  re-binds traces when a resolved file's label differs from the stored one,
  which is the only route a hand-edited config has to re-point at moved data.
- **A file's label is a function of the SET of loaded paths, never its
  basename alone** (`distinct_file_labels` in `pkg_rlc/model/trace.py`). The
  label is the KEY traces bind by; as the bare basename, two `L.s1p` from two
  folders shared one, `_file_by_label` returned the first for both, and the
  trace made for an 80 GHz file drew a 30 GHz one. A clashing group carries the
  fewest trailing folders that separate it (`ind_30G/L.s1p`), and
  `App._relabel_files` recomputes on every add, remove and session load — so a
  session reloads under the labels it saved, and the survivor of a removed pair
  gets its plain name back. After a relabel the editor MUST be reloaded, or its
  next auto-apply writes the old label back into the trace
  (`tests/test_file_labels.py`).
  The `found` flag is checked BEFORE `_load_one_file`, which reports through a
  **modal** dialog — a session whose folder moved would otherwise open one per
  file (measured: the test does not fail, it hangs) before the user could read
  the single Results line that says the same thing.
- **`WM_DELETE_WINDOW` must point at `_on_close`, and the test checks the
  handler NAME.** With nothing registered Tk reports its own built-in
  `"…destroy"`, which is truthy — `assertTrue` on it passes in exactly the
  broken state, where closing the window skips the autosave entirely.
- **`_session_dict` flushes the editor first**, same rule and same reason as
  Calculate: `Ctrl+S` in the same event burst as a keystroke would otherwise save
  the value from before it.
- **Loading CANCELS the queued editor sync rather than flushing it.** The target
  trace is about to be discarded. `_cancel_editor_sync` is for that case only —
  everywhere else the queued edit is the user's last keystroke and must land.
- **A bad value costs its own field, never the file.** A session file is readable
  text, so it will be hand-edited. Unknown keys, unparseable ints and malformed
  rows are dropped with a note in the Results pane. `_coerce_bool` is not
  `bool()`: `bool("false")` is `True`, which would silently invert a checkbox.
  A combobox value outside its list is refused because both are `state="readonly"`
  and there would be no way back through the UI.
- **`sig_digits` is a saved control, and its `default` is a VALUE rather than
  an absent key.** It joins `units_mode` and `results_view` in `_CONTROL_KEYS`
  / `_CONTROL_CHOICES` (validated against `RESULTS_DIGITS`, which is
  `pkg_rlc.model.trace`'s for the reason `RESULTS_VIEWS` is: a vocabulary
  shared between the file format at L2 and the renderer at L3 lives at or
  below the lower of the two). A file written before the control existed
  carries no key, which keeps the current setting — the same "an absent
  control changes nothing" every other key here has. `_apply_session` sets the
  variable AND calls `plot.set_sig_digits` before the first replot, because
  the cursor readout is built during a draw and the restored session's first
  frame has to already be at the restored precision. See "The Digits control"
  in `docs/conventions/results_pane.md`.
- **`SessionError` carries the whole verdict in `str(e)`**, the
  `TouchstoneParseError` contract: not-ours, no version, and version-from-the-
  future are three different messages, and the future one names both numbers.
- **The autosave never raises and never writes an empty session.** It runs inside
  `WM_DELETE_WINDOW`, where a raise is an application that cannot be closed; and
  opening the tool, changing nothing and closing it must not erase what the
  previous run left. Startup only *names* what is on disk — loading it would
  re-parse every Touchstone file in it before the user has asked for anything.
- **Save/Load are on a MENU BAR, not a button.** The Files and Traces rows are
  both four buttons deep against a measured 448 px, and a fifth row in Global
  Controls comes straight out of the editor viewport, which at the 1040x600
  minsize is already down to tens of pixels. `unbind_class("Text", "<Control-o>")`
  goes with the accelerators: Tk's Text binds it to "insert a newline" and a
  `bind_all` handler runs *after* the class binding, so Ctrl+O would open the
  dialog and scribble in the Results pane behind it.
- **A `ttk.Notebook` CLIPS a tab strip it cannot fit** — no wrap, no scroll, and
  the tab that vanishes is the LAST one. Measured (Microsoft YaHei UI 9): the
  Help window's nine tabs needed 891 px and the tenth took it to 968, past the
  historical 950, which is why `HELP_WINDOW_WIDTH` is 1010. Since the stage-2
  regroup (2026-10-02) the strip is NINE task tabs needing **812 px**, so the
  headroom is 198 px — and the "eleventh Help tab" stays rejected
  (`rejected_ui.md`) whatever the headroom; `TestHelpTabsAllFit` re-measures
  it.

### The Help window's prose lives in `docs/help/`, not in Python

`pkg_rlc/present/help.py` is ~145 lines: `HELP_DIR`, `_help_text`, the nine
`HELP_*` names, `HELP_TOPICS`, `HELP_WINDOW_WIDTH` and `HelpWindow`. The prose
that used to be triple-quoted constants is nine files under `docs/help/`, read
at import time. `tests/test_session.py::TestHelpTabsAllFit` is still the guard
on the tab strip.

**The tabs are TASKS, not editor modes (2026-10-02, stage 2 of
`docs/design_workspaces.md`, § 3.7).** When the editor became one row model
there were no modes left to have a tab each, so the five `Mode 1/2/3/5/6` tabs
became four task tabs, nine in all, in this reading order:

| Tab | File | Holds |
|---|---|---|
| Overview | `overview.md` | what the tool does, the universal assumptions, the results table and its controls |
| Reading files | `reading_files.md` | the parser, the diagnosis, the refusals |
| Save / Load | `save_load.md` | the session file, incl. configs from older builds |
| Setting up a measurement | `setup.md` | the ONE table: measurement ports (`+` / `-`), connections, templates, the probe rules, the old modes written as rows, Edit as text, a second file, migration |
| Coupling | `coupling.md` | two or more measurement ports: M, k, M/L, C_c, attribution, cold start, `--compose` (was most of `mode6.md`) |
| Trace model | `trace_model.md` | the stage-1 workspace and the pi / bandwidth reading (was the tail of `mode6.md`) |
| Compare files | `compare_files.md` | today's Compare files window (closed backlog TASK-017) |
| Input syntax | `input_syntax.md` | port ranges, tags, node names, the `--short` / `--mport` spellings |
| Worked examples | `worked_examples.md` | every example written as rows |

`TestHelpTabsAllFit` pins the titles and their ORDER exactly, that no tab is
the "help content not found" fallback, that every `"<title>" tab` the prose
names is a real title (whitespace folded, since the prose wraps titles), and
that the setup tab carries the editor's own words "Ports not listed anywhere
are OPEN." Each was mutation-checked. Mode numbers appear in the Help prose in
exactly one place — the "old modes, written as rows" table on the setup tab,
which is there FOR the reader holding an old config — plus the CLI's own
`--mode gnd | p2p | coupling`, which is unchanged.

- **When the prose moved out of Python (2026-08) the rendered text was
  byte-identical to the pre-move build**, checked by dumping every tab before
  and after and by loading the old module out of git beside the new one. That
  invariant ended deliberately with the stage-2 regroup, which rewrote the
  content. Edit the `.md`, never re-derive it.
- **The `HELP_MODE1` … `HELP_MODE6` names are GONE (2026-10-02)**, with
  `mode1.md` … `mode6.md`. They had been kept as aliases on the grounds that
  keeping them was free; once the text they were bound to no longer existed,
  an alias would have resolved to a tab that is not in the window. Nothing in
  the repo read them (grepped). The names now are `HELP_OVERVIEW`,
  `HELP_FILES`, `HELP_SESSION`, `HELP_SETUP`, `HELP_COUPLING`,
  `HELP_TRACE_MODEL`, `HELP_COMPARE`, `HELP_SYNTAX`, `HELP_WORKFLOWS`.
- **NINE TABS, 812 px against `HELP_WINDOW_WIDTH = 1010`** (measured
  2026-10-02, Microsoft YaHei UI 9, tk scaling 1.333; the old ten measured 968
  in the same session, matching the figure above). The width was NOT shrunk:
  it is also the text's width. The "no eleventh tab" rule is unchanged — and a
  RENAME moves the strip width just as an addition does, which is why the
  slugs are decoupled from the titles: renaming a `.md` is free, renaming a
  TAB is not.
- **NO MARKDOWN LIBRARY, and the `.md` extension is a filename, not a format.**
  The bodies are plain text — the same plain text the `ScrolledText` has always
  drawn — and `_help_text` is a file read. Do not introduce syntax the window
  cannot draw; there is no renderer to teach it to.
- **THEY SHIP TO THE RED ZONE WITH NO `.gitattributes` ENTRY, and that was
  verified rather than reasoned about.** `.gitattributes` is a BLACKLIST for
  `git archive` and neither `docs/` nor `*.md` is on it. Confirmed three ways:
  `git check-attr` reports `export-ignore: unspecified` and `eol: lf`;
  `git archive HEAD | tar -t` lists all of them; and the extracted bytes are
  identical to the worktree with zero CR bytes. Getting this wrong ships a Help
  window with no content to a machine where nobody can fix it, so re-run those
  three checks if the packaging rules are ever touched.
- **A MISSING OR UNREADABLE FILE COSTS ITS OWN TAB, NEVER THE WINDOW.**
  `_help_text` returns `help content not found: <path>` as that tab's body and
  the other tabs open normally — the session loader's "a bad value costs its
  own field, never the file", one layer over. **`UnicodeDecodeError` is caught
  beside `OSError` and is NOT redundant**: it is a `ValueError`, so a file
  truncated mid-codepoint or re-saved by an editor in the local codepage would
  sail past an `OSError`-only guard and take the whole window down on the one
  platform where that is likeliest.
- **The read names `utf-8` EXPLICITLY and uses universal newlines.** This tool
  runs on Windows boxes whose locale encoding is **GBK**, and the tabs carry
  `Ω`, `±` and `✓` — relying on the platform default is mojibake or a raise,
  not a style question. Universal newlines mean a CRLF checkout (or a
  hand-edited file) still yields exactly the LF text the window used to hold as
  a literal. `HELP_DIR` is derived from `__file__`, never the cwd: the GUI is
  launched by double-click and from a shortcut, and neither guarantees one.

#### KNOWN, NOT FIXED: the Help prose is duplicated in README.md and theory.md

Now that the tabs are diffable text, the overlap is measurable, and it is much
larger than the "keep the six in sync" bullets imply. Measured with an 8-word
shingle scan over sentences of >= 7 words (whole sentences, not fragments),
BEFORE the stage-2 regroup — `mode6.md` is now `coupling.md` + `trace_model.md`,
and `mode2.md` / `mode5.md` are inside `setup.md`; the table was not
re-measured:

| Help tab | shares with README.md | shares with docs/theory.md |
|---|---|---|
| `mode6.md` | **189** | **80** |
| `overview.md` | 30 | 4 |
| `input_syntax.md` | 23 | 1 |
| `reading_files.md` | 10 | — |
| `mode5.md` | 4 | 1 |
| `worked_examples.md` | 4 | 1 |
| `mode2.md` | 3 | 3 |
| `save_load.md` | 1 | — |

The dangerous half is the MEASURED FIGURES, which now have four to six homes
each and no guard tying them together: `6.07 dB` (help/mode6, theory,
design_port_attribution, README, `pkg_rlc.physics.attrib`, `pkg_rlc.panels.attrib_gui`),
`9.60 dB` (the same set plus `design_snp_composition`), `-870.268 pH`
(help/mode6, help/worked_examples, theory, README, `pkg_rlc.physics.attrib`,
`pkg_rlc_extractor`, two test modules) and `505.25 nH` (four). The
"coupling ratio" rule's Help home is now **`docs/help/coupling.md` and
`docs/help/overview.md`**, not `pkg_rlc/present/help.py` — update that bullet's pointer
when it is next touched.

**NOT unified in this phase, deliberately.** Each pair is two DIFFERENT
wordings of one fact aimed at two different readers, so collapsing them is a
judgement about which wording wins and needs a human reading both. The move
was a prerequisite: before it, none of this was greppable next to the prose it
duplicates.
