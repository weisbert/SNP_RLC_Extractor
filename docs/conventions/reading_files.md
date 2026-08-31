# Reading Touchstone files (robustness, diagnosis, refusal)

*Moved out of `CLAUDE.md` on 2026-08-31, VERBATIM, when that file passed the
150k characters a session can hold. Every heading below is the section title it
had there, so a cross-reference of the form ``CLAUDE.md § <title>`` still
resolves. **These rules are exactly as binding as the ones that stayed.**
The index is `docs/conventions/README.md` and the pointer table is in
`CLAUDE.md` under "The rest of the rules live in `docs/conventions/`".*

### Reading files (robustness, diagnosis, refusal)

- **A non-numeric token in a data line is a HARD ERROR, not a skipped token.**
  The old parser dropped it and warned. Touchstone is a positional stream, so a
  dropped value shifts every later value by one slot: the frequency column
  starts reading S-parameters, and the file either fails the divisibility check
  with a meaningless message or — worse — still divides evenly and yields a
  plausible wrong answer. `lenient=True` (`--lenient`, and a button on the GUI
  error dialog) restores the old behaviour for people who know what they are
  doing; it is not the default and its warnings say the result is suspect.
- **Every failure is a `TouchstoneParseError` carrying a `kind`.** FAULT_FILE /
  FAULT_UNSUPPORTED / FAULT_ACCESS / FAULT_INTERNAL, rendered as a **verdict**
  line. That is the whole point of the class: "is my file bad or is your tool
  bad?" is the first question a parse failure has to answer. It subclasses
  `ValueError` (what the parser raised before) and `str(e)` IS the full report,
  so existing `except Exception as e: show(e)` call sites upgrade for free.
  Nothing escapes `parse_touchstone` as a bare traceback — an unexpected
  internal exception becomes FAULT_INTERNAL *with the diagnosis attached*, and
  only after the diagnosis agrees the file is consistent.
- **The bookkeeping for good error messages lives in a SECOND PASS, never on
  the hot path.** `_diagnose` re-reads the file with a line number and token
  count per data line and is what turns "token count 3603 not divisible by 9"
  into "the file ends mid-record at line 408". It runs only on failure or when
  the user asks (`Check File` / `--diagnose`), it must never raise
  (`_safe_diagnose`), and its `headline` overrides the caller's when the caller
  has a worse one — the sniffer can only ever say "could not infer port count",
  which on a truncated file sends the user to force a port count that was never
  wrong.
- **`FAULT_NONE` means the diagnosis found nothing wrong**, and it is what
  `--diagnose` turns into exit code 0. Do not fold it into FAULT_INTERNAL: the
  parse path maps NONE -> INTERNAL itself, because "the file is fine and we
  still failed" is our bug, but a standalone check needs to be able to say
  "fine" without accusing anyone.
- **`data_notes` is not `parser_warnings`.** Warnings mean "I guessed, or I
  threw something away"; notes mean "the file is fine, here is what is in it"
  (DC point, `max |S| > 1`, irregular sweep). The split is also load-bearing
  for the golden reference, which pins `parser_warnings` element-for-element —
  a new descriptive check in that list would force regenerating
  `golden_legacy.npz`, which is exactly what must not happen.
- **The encoding is sniffed; the file is no longer opened blind as UTF-8.**
  `errors="replace"` turned a UTF-16 export (real EDA tools write them) into a
  wall of skipped tokens, and a UTF-8 BOM glued itself to the leading `#` so
  the option line was never recognised and the file silently parsed as
  `# GHZ S MA R 50`. Compressed/binary files are refused by magic number rather
  than misread. Measured cost: 24 µs.
- **Descriptive checks (`_check_freq_axis`, `_check_s_values`) are below the
  noise floor** — the `|S|` scan is 0.6 ms of a 120 ms parse on a 16 MB file,
  and it is chunked by `_freq_batch` because `np.abs` on a whole
  (5000, 153, 153) array allocates ~1 GB. Keep it chunked.
- **The extension is a TIEBREAK and a LAST RESORT, never the primary source.**
  Content-sniffing stays first (EDA tools rename these files constantly), but
  picking the smallest of several candidates silently read a 2-port file as a
  1-port one, and nothing above `MAX_SNIFF_NPORTS = 256` could be opened at all
  — a `.s300p` package export is the normal case this tool exists for. Both
  uses emit a warning naming what happened.
- **A STRICTLY INCREASING FREQUENCY COLUMN IS NOT A PROPERTY OF A GOOD FILE,
  and treating it as one made the tool refuse a healthy 19-port export.**
  HFSS and ADS adaptive/discrete sweeps write their points in SOLVE order —
  the two endpoints, then bisect — so the frequency column of a perfectly
  correct file is not sorted. Measured on the reported file: 9399 numbers,
  **13 records of 723 at N=19 with NOTHING left over**, record 3 at 15 GHz
  behind record 2's 30 GHz. Three separate defects fell out of it and all
  three are fixed; `tests/test_parse_diagnostics.py::
  TestFrequenciesWrittenOutOfOrder` is the guard (15 mutations, 15 behaved as
  declared).
  **(a) `_sniff_nports` gained STEP 2b** (`_freq_column_plausible`): after the
  strict name check and *before* the wide search, the file name's N is
  accepted with the frequency column OUT OF ORDER, provided the record size
  divides exactly and the column reads as a set of sweep points at all —
  every value finite, none negative, **all DISTINCT**. Those three tests are
  what keeps the relaxation from swallowing a wrong port count, which is the
  whole risk: a wrong N slices S-parameter values into the leading column, and
  S data repeats and goes negative. It is gated on the NAME on purpose, so the
  answer rests on two independent pieces of evidence; **do not promote it to a
  content-only search**, and do not relax it further. The warning says which
  test the file got in on.
  **(b) `parse_touchstone` SORTS the axis** (`_check_freq_axis` returns the
  permutation, the caller applies it to `freqs` AND `s`). Stable, so records
  sharing a frequency keep the order the file wrote them in. Sorting is what
  every consumer downstream already assumes — the plot connects points in
  array order and draws a zigzag otherwise, `freq_span_str` and the fit window
  read the ends, `snap_to_grid` derives a step, `compose.align_frequencies`
  compares grids — so leaving the order alone means half a dozen surfaces each
  looking wrong in their own way. **The sort cannot hide a wrong forced port
  count**: the existing "not strictly increasing … a forced port count that is
  wrong looks exactly like this" warning still fires, verbatim, with one
  sentence added saying the points were reordered. The failure mode the fix
  itself could introduce is sorting `freqs` and forgetting `s` — a readable
  file with every number misfiled — so the guard stamps each S entry with its
  own record's frequency and checks it back, at n==2 (which takes the
  column-major transpose) and n>2 (which does not).
  **(c) The `_diagnose` VERDICT no longer assumes truncation.**
  `_diag_candidate` returns a REASON (`""` / `"short"` / `"reordered"` /
  `"scrambled"`) instead of a bool, because the caller writes the verdict from
  it and used to write one verdict for all of them. The reported file got
  *"the data does not divide into whole records … **plus 0 left over**"* — a
  headline contradicted by its own number — over the hint *"the file is
  usually truncated — re-export it"*, which sends the user to ask for a
  re-export of a file with nothing wrong with it. Now: `"reordered"` is
  **FAULT_NONE** and says nothing needs re-exporting; `"scrambled"` (divides
  exactly, leading column is not a frequency axis) names the PORT COUNT as the
  likelier suspect; only a genuine leftover keeps the truncation wording, and
  that wording is unchanged — `truncated_refused`, `truncated_lenient` and
  `diagnose_truncated` in `tests/fixtures/cli_reference/` did not move.
  **The diagnosis and the reader go through the SAME `_freq_column_plausible`**,
  or a file the parser opens could still be called broken.
- **`freq_span_str` takes min/max, not `freqs[0]` / `freqs[-1]`.** On the
  reported file it read **`1 GHz - 17 GHz`** over a sweep covering 1–30 GHz —
  the last record was 17 GHz. The sort above makes the two agree on anything
  `parse_touchstone` returns, which is exactly why the accessor must not rely
  on it: a `TouchstoneData` built anywhere else would be silently wrong. The
  string reaches the Files listbox, the CLI load block and the GUI summary.
- **KNOWN, NOT FIXED: `force_nports` is CLI-only.** There is no GUI control
  for it, and a port-count refusal carries no `retry_lenient`, so no button
  appears either — which is what made the reported file a dead end in the GUI
  while the verdict was telling the user to "force the right one instead".
  Step 2b removes the need for it on THIS class of file; it does not add the
  control. If a forced port count ever reaches the GUI, note that the Files
  row is already four buttons deep against a measured 448 px (a fifth is not
  free) — the error dialog or the right-click menu is where it goes.
- **Touchstone 2.0 is refused, in lenient mode too.** Read as v1, the numbers
  inside `[Number of Ports] 4` land in the data stream and shift everything
  after them. "Skip the bad tokens" is precisely the wrong answer here, so
  `_recover_data_line` checks `_V2_KEYWORD_RE` before anything else.
- **`_decode_options` returns its unrecognised tokens.** A misspelt format
  keyword used to fall through to the `MA` default in silence, which reads RI
  data as magnitude/angle and produces a well-formed, completely wrong file.
- **`Check File` is the FOURTH button in the Files row** — and `pack` unmaps
  from the end, so that is not free. Measured at the 1040x600 minsize: the row
  needs 364 px and has 448. `tests/test_parse_diagnostics.py::
  TestGuiFileChecking::test_check_file_button_is_on_screen_at_minsize` asserts
  `winfo_ismapped()` on all four; re-measure before adding a fifth.
- **`FileEntry.info_str` puts the frequency span BEFORE M and Z0.** A Listbox
  has no horizontal scrollbar, so a long file name clips the tail of that line
  (measured: a 37-char name needs 476 px against a 444 px list). Of the four
  facts on it the span is the one worth keeping.
- **Touchstone v1 quirk for n=2.** The 2-port column order is `S11 S21 S12 S22` (column-major), but n>=3 is row-major. `parse_touchstone` transposes only when `nports == 2`. `tests/generate_test_snp.py:write_touchstone` writes the matching column order on output. Don't "fix" either side without fixing the other.
- **Capacitor fit needs `_scaled_lstsq`.** The Im(Z) design columns `omega` and `-1/omega` differ by ~1e20 in magnitude; raw `np.linalg.lstsq` kills the small singular value and reports `C=1e41`. The column-rescaling helper in `pkg_rlc/physics/solve.py` (re-exported by `pkg_rlc.physics.core`) is load-bearing -- don't remove it.
- **SI suffix `M` is Mega (1e6), not milli.** Milli is lowercase `m`. Used in Custom Mode lumped-value parsing and exposed in Help → Input syntax.
- **Mode 3 short-group syntax.** `1-2-3-4` is a single group of 4 ports tied together (parser emits chained binary pairs `(1,2),(2,3),(3,4)` which Union-Find inside `compute_z` merges). Don't simplify the parser into "exactly two ports per group".
- **Mode codes are stable and are never renumbered.** 1, 2, 3, 5, 6 are live; **4 is retired** (`A ↔ B + VDD/GND`) because for AC small-signal VDD *is* an AC ground. A mode-4 trace migrates to mode 2 with its VDD ports unioned into GND (`TraceConfig.migrate_legacy_mode`), `TraceConfig.vdd_ports` stays as a field so old configs still load, `build_terminations_mode4` stays as a re-export, and `--vdd` on the CLI is a deprecated alias that unions into `--gnd` and prints a note. Do not reuse code 4 and do not delete the `Vdd` termination class — it documents intent and is evaluated as `Ground`.
