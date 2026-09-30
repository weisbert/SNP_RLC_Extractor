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
