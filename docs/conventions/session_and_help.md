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
- **Retired fields are written only when non-empty.** A trace the user has never
  selected still carries `custom_text` / `mp1_*` unmigrated, so dropping them
  would lose a spec — but emitting eight empty strings per trace buries the ones
  that matter. Migration happens on load, through the existing `_migrate_trace`.
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
  re-binds traces when a resolved file's basename differs from the stored label,
  which is the only route a hand-edited config has to re-point at moved data.
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
  historical 950. `HELP_WINDOW_WIDTH` is now 1010, i.e. **42 px of headroom, not
  enough for an eleventh tab**; `TestHelpTabsAllFit` re-measures it.

### The Help window's prose lives in `docs/help/`, not in Python

`pkg_rlc/present/help.py` is 140 lines: `HELP_DIR`, `_help_text`, the ten `HELP_*`
names, `HELP_TOPICS`, `HELP_WINDOW_WIDTH` and `HelpWindow`. The prose that used
to be triple-quoted constants — 2295 lines when it moved out, 2648 today — is
ten files under `docs/help/`, read at import time. `tests/test_session.py::TestHelpTabsAllFit` is still the
guard on the tab strip.

- **THE RENDERED TEXT IS BYTE-IDENTICAL to the pre-move build and must stay
  so.** Checked two ways when it moved: every tab dumped to disk before and
  after (`diff -r` clean), and the pre-change module loaded out of git
  alongside the new one in ONE process, comparing all ten titles, all ten
  bodies, the ten `HELP_*` constants and `HELP_WINDOW_WIDTH` — 0 mismatches.
  Edit the `.md`, never re-derive it.
- **THE TEN `HELP_OVERVIEW` / `HELP_MODE1` / … NAMES ARE KEPT**, bound to the
  same text, so `HELP_TOPICS` is byte-for-byte the list it always was and
  `pkg_rlc.present.help.HELP_MODE6` goes on resolving. Same precedent as `pkg_rlc.frontend.app`
  re-exporting the DSL helpers that moved into `pkg_rlc.physics.core`. Nothing outside
  the module reads them today; that is not a reason to delete them, it is why
  keeping them is free.
- **STILL TEN TABS, still 968 px against `HELP_WINDOW_WIDTH = 1010`**
  (re-measured after the move). Everything the existing "no eleventh tab" rule
  says is unchanged — and a RENAME moves the strip width just as an addition
  does, which is why the slugs are decoupled from the titles: renaming a `.md`
  is free, renaming a TAB is not.
- **NO MARKDOWN LIBRARY, and the `.md` extension is a filename, not a format.**
  The bodies are plain text — the same plain text the `ScrolledText` has always
  drawn — and `_help_text` is a file read. Do not introduce syntax the window
  cannot draw; there is no renderer to teach it to.
- **THEY SHIP TO THE RED ZONE WITH NO `.gitattributes` ENTRY, and that was
  verified rather than reasoned about.** `.gitattributes` is a BLACKLIST for
  `git archive` and neither `docs/` nor `*.md` is on it. Confirmed three ways:
  `git check-attr` reports `export-ignore: unspecified` and `eol: lf`;
  `git archive HEAD | tar -t` lists all ten; and the extracted bytes are
  identical to the worktree with zero CR bytes. Getting this wrong ships a Help
  window with no content to a machine where nobody can fix it, so re-run those
  three checks if the packaging rules are ever touched.
- **A MISSING OR UNREADABLE FILE COSTS ITS OWN TAB, NEVER THE WINDOW.**
  `_help_text` returns `help content not found: <path>` as that tab's body and
  the other nine open normally — the session loader's "a bad value costs its
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
shingle scan over sentences of >= 7 words (whole sentences, not fragments):

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
"coupling ratio" rule's Help home is now **`docs/help/mode6.md` and
`docs/help/overview.md`**, not `pkg_rlc/present/help.py` — update that bullet's pointer
when it is next touched.

**NOT unified in this phase, deliberately.** Each pair is two DIFFERENT
wordings of one fact aimed at two different readers, so collapsing them is a
judgement about which wording wins and needs a human reading both. The move
was a prerequisite: before it, none of this was greppable next to the prose it
duplicates.
