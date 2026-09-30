# Compare files (`pkg_rlc/physics/similarity.py`, `pkg_rlc/panels/compare_gui.py`)

*Added 2026-09-30. As binding as every other dossier here.*

### Compare files — two files over the band they share

The owner's question: *an inductor extracted to 30 GHz and again to 80 GHz —
inside 0–30 GHz, is the 80 GHz one the same?* An EM solver sizes its mesh from
the highest frequency it is asked for, so the two runs are two discretisations
of one layout and need not agree. The window answers with numbers, at two
levels, each failing on its own:

- **The FILE:** the largest `|S_B - S_A|` over every entry, per frequency, in
  dB (−40 dB is a 1 % vector error). A Z0 mismatch is renormalised away through
  Y; a port-count mismatch is refused, and refuses ONLY this level.
- **What was EXTRACTED:** one trace's port setup (or the default "port 1 to
  ground" a freshly loaded file gets) applied to BOTH files, through
  `app._build_termination` / `run._build_termination` — the same builders a
  Calculate uses — and the signed difference of L, Q and R, B against A.
  Composed traces are left out of the choice: their port numbers span files.

Rules, each pinned by `tests/test_compare_files.py` and mutation-checked:

- **THE BAND IS THE OVERLAP. Nothing is extrapolated.**
- **THE GRID IS THE COARSER FILE'S points inside the overlap**, and the finer
  file is interpolated onto it — the OPPOSITE of `compose.align_frequencies`,
  deliberately: interpolating the coarse file chords it across its own gaps,
  and that chord error would read as a difference neither file contains.
  Identical grids (relative 1e-9) are not interpolated at all.
- **A relative difference is not computed where the reference is near its own
  zero** — below `NEAR_ZERO_FRAC` (1 %) of its MEDIAN |value| in the band — and
  the excluded count is printed. The median, not the maximum: R climbs four
  decades into a self-resonance and 1 % of that peak discarded every ordinary
  low-frequency R point (measured in the first draft, caught by
  `test_ordinary_R_is_not_mistaken_for_zero`).
- **The limits are DEFAULTS the reader edits** (−40 dB, L 1 %, Q 5 %; R has no
  limit and is shown for reference). How close is close enough is the design
  margin's call, so the window always shows the worst value, where it is, and
  the curve under the limit lines — never only a verdict. Editing a limit
  re-judges without re-solving.
- **A defaults to the file with the LOWER top frequency** — the one whose whole
  sweep is inside the other's. After Add File the selection is always the last
  file, so "the selected one" would make the 80 GHz run the reference of the
  question about it.
- **Reachable from Analyze → `Compare files…` and from the Files list's
  right-click menu**, never as a fifth button: both button rows are measured
  full (see `architecture.md`). No accelerator, for the Attribution reason.
- **A removed file is SAID to be gone**; the window does not keep comparing
  from the arrays it last saw. `refresh_compare_windows` is called wherever
  `refresh_files_windows` is on the Files side, and from `App._relabel_files`.
- **The S level shows EVERY entry, not one number.** The worst entry, how many
  of the n² entries are over the limit, the largest few (identical entries are
  not ranked), and — up to 32 ports — the whole matrix of each entry's worst dB,
  one decimal (−39.6 printed as −40 beside a −40 dB limit and starred as over it
  reads as a contradiction). An entry is ALWAYS written `S(i,j)`: the first
  version printed `S1415` for S(14,15), which the owner could not read and
  which is ambiguous from ten ports up. Beside every dB, the same difference as
  a percentage.
- **A percentage axis is not scaled by one wild point** (`pct_view_span`): it
  covers the 98th percentile, never less than twice the limit, and counts what
  it leaves off. A near-open port read −4e8 % at 1 MHz and flattened a band
  that sat inside ±2 %. The worst value is still in the text.

### The reading is written for someone DECIDING (2026-09-30, second pass)

The owner, on the first version: *"我希望这个软件里面的解读，是人能看懂的样子，
现在这个样子根本看不懂"*. It printed the measurement (`worst |S_B - S_A| = -39.5
dB ... S1415 ... limit -40 dB -> DIFFERENT`). `compare_summary_lines` is now
ordered as a reader asks, and `tests/test_compare_files.py::TestSummary` pins it:

- **IN SHORT first**: THE SAME / NOT THE SAME, then one bullet per thing over
  its limit, in words ("inductance L: B is 5.9 % higher than A below 9.44 GHz
  (worst at 9.4 GHz)"), then the numbers AT THE MARKER FREQUENCY — the working
  frequency is what a designer signs off on. Then WHAT TO DO NEXT, conditional
  on what was found. The evidence (what was compared, the raw file, the
  inductor, the port-pair table) comes after, never before.
- **No dB and no scientific notation anywhere in the text.** The S limit box is
  in PERCENT of full scale (1 % = −40 dB; `sim.db_to_pct` / `pct_to_db`); a
  percentage is never `2.8e+02` (`_num`). The S plot is in % on a log axis with
  `1 %`, `0.1 %` ticks, floored at 0.0001 %.
- **Ports in words**: `S(14,15), between port 14 and port 15`.
- **L and Q are JUDGED only below `SRF_JUDGE_FRAC` (85 %) of the lower
  self-resonance** (`ZCompare.usable_limit`). Past the resonance the part is a
  capacitor, and at it a 1 % shift of the resonance reads as hundreds of percent
  of L — the first version's headline was that number. The region past it is
  shaded "not judged" on the plots and its worst is still printed; the
  resonance shift itself is one sentence ("moved from 11.2 GHz (A) to 11.1 GHz
  (B), 1.2 % down"). Mutation-checked: an `usable_limit` of +inf fails
  `test_the_resonance_is_not_what_the_verdict_reads`.
- Section headings bold, what is over a limit red (`line_tags`).
- The text stays ENGLISH, like every other window: the red zone's X11 fonts are
  not known to carry CJK, and a reading in boxes is worse than one in English.
