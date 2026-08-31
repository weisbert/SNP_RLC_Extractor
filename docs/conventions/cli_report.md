# The CLI's printed report

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### The CLI's printed report (`tests/fixtures/cli_reference/`)

The CLI (`pkg_rlc/frontend/cli.py`, then `pkg_rlc_extractor.py`) was ~4400
lines of which most was print statements, and
until this reference existed **nothing pinned a character of any of it**. It is
**3060 lines** now: the eight `_attr_print_*` and six `_cold_print_*` sections
moved whole into `pkg_rlc/present/attrib_report.py` and RETURN their lines instead of
printing them, which is what lets this reference pin them with no stream to
capture. What still prints from the entry point is `_print_coupling_report`,
three `_compose_print_*` sections, four CSV writers and `_emit`.
`golden_legacy.npz` pins the numbers and `render_reference.json` pins the GUI's
results pane; this is the third large rendered surface.

- **143 cases, and the matrix is SELF-GUARDING.** Every mode, every `--fit`,
  both `--csv` shapes, all eight `--diagnose` cases, every flag in the
  attribution / cold-start / composition groups (including both
  `--attribute-ground-model` spellings and the "model was ignored" case), and
  **43 refusals pinned on the message TOKEN and the exit code** — 86 exit 0,
  14 exit 1, 43 exit 2. `TestTheMatrixCoversTheFlags` walks
  `_make_arg_parser()._actions`, so a new flag added with no case is a test
  failure rather than a silent hole.
- **DETERMINISM IS NORMALISED IN THE CAPTURE, NEVER TOLERATED IN THE COMPARE.**
  Repo root → `<ROOT>`, scratch dir → `<OUT>`, in both OS spellings **and in
  the repr-doubled form `str(OSError)` embeds** (a Windows path arrives inside
  an errno message with every separator doubled, and the plain spelling does
  not match it); CRLF → LF; `COLUMNS` pinned to 80, because argparse wraps
  `--help` and every usage message to whatever terminal captured it; the
  localised `[WinError 2] <sentence>` → `[OS-ERROR]` with the path inside it
  kept — that sentence is the operating system's and this box answers in
  Chinese. Nothing pins `PYTHONHASHSEED`: the script captures everything TWICE
  in-process and refuses to write unless the two agree, so a set-of-strings
  iteration order reaching the output would surface here. Two cases really were
  unstable on the first attempt and were fixed in the capture.
- **stdout and stderr are NEVER elided; a written FILE is, past 140 lines**
  (head 120 + a counted marker + tail 20). It bites on three artifacts only —
  the two `--compose-export`s and the 401-row `--csv` — and saved ~200 KiB. The
  marker carries the dropped count, so a change in how many rows are written is
  still a failure.
- **A failure here means the CLI's output changed.** Regenerate with
  `python tests/_cli_capture.py`, and ONLY in the same commit that justifies
  it. Mutation-checked: `.4g` → `.5g` on one `R` line fails 23 cases, and one
  character of a `--csv` header fails the artifact compare.
- **KNOWN, NOT FIXED: the composition refusal headline can contradict its
  body.** `CANNOT COMPOSE -- a port reference names nothing` is printed for an
  elementwise LENGTH MISMATCH and for a `--compose-propose` tag naming no file;
  both bodies say something else entirely. Pinned as-is, so fixing it is a
  one-case regeneration.
- **KNOWN AND DELIBERATE: `--diagnose` exits 1 with an EMPTY stderr**, the whole
  report on stdout — the report is the product and the exit code is the verdict
  (`FAULT_NONE`). `test_a_non_zero_exit_always_wrote_to_stderr` exempts it by
  name rather than loosening the rule for everyone; do not "fix" it onto stderr.
- `tests/test_cli_golden.py` imports no tkinter and **qualifies for
  `FAST_MODULES`** on that one property (there is a test asserting neither
  `pkg_rlc.frontend.app` nor `tkinter` ever enters `sys.modules`); it is not in the list
  yet.
