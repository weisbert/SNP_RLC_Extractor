Compare files -- are two files the same network?
================================================

The question it answers: "I have the inductor extracted to 30 GHz
and again to 80 GHz. Inside 0-30 GHz, is the 80 GHz one the same?"
An EM solver sizes its mesh from the highest frequency it is asked
for, so two runs of one layout are two different discretisations
and need not agree. This window says how far apart they are, over
the band they share, at two levels -- and each level can fail on
its own.

Opening it
----------
Analyze -> "Compare files...", or right-click the Loaded Files list.
It needs two loaded files; with fewer it says so and opens nothing.

It is an ordinary window, not a dialog: it stays open while you
work in the main window, and you can keep it on a second monitor.

The top of the window
---------------------
  A (reference)   the file the other is measured against. It starts
                  as the file with the LOWER top frequency -- the one
                  whose whole sweep lies inside the other's -- so the
                  80 GHz run is the one being checked, not the
                  reference of the question about it.
  B               the other file.
  Port setup      what is EXTRACTED from both files for the L / Q / R
                  comparison: either "port 1 to ground (default)", or
                  the setup of one of your traces, applied unchanged
                  to BOTH files. A trace built from several files is
                  not offered -- its port numbers name ports of more
                  than one file.

  "Call them the same if they differ by less than:"
                  S-parameters 1 %   L 1 %   Q 5 %

The three limits are DEFAULTS for you to change. How close is close
enough is your design margin's call, not this tool's. Type a new
limit and press Enter (or click away): the verdict is re-judged at
once, with nothing recomputed. R has no limit; it is shown for
reference.

Refresh (at the bottom) re-reads the marker frequency from the main
window and recomputes. The window does not follow the marker on its
own.

What it compares, and on which points
-------------------------------------
  * ONLY THE BAND BOTH FILES COVER. Nothing is extrapolated. The
    reading says which band that is.
  * ON THE COARSER FILE'S POINTS. The finer file is interpolated
    onto them, never the other way round: interpolating the coarse
    file would draw straight lines across its own gaps, and that
    error would read as a difference neither file contains. Two
    files with the same points are compared point by point, with no
    interpolation at all.
  * A different reference impedance (Z0) in the two files is
    allowed and is accounted for. A different PORT COUNT is not: the
    raw S comparison is refused, and says why.

Reading the result
------------------
The text is written in the order you would ask the questions:

  IN SHORT          THE SAME, or NOT THE SAME -- then one line per
                    thing that is over its limit, in words ("inductance
                    L: B is 5.9 % higher than A below 9.44 GHz"), then
                    the L / Q / R difference AT THE MARKER FREQUENCY,
                    because the working frequency is what you sign
                    off on.
  WHAT TO DO NEXT   depends on what was found.
  WHAT WAS COMPARED the two files with their own sweeps, the band,
                    and whose points were used.
  1. THE RAW FILE   every S-parameter of every port pair: the
                    largest difference, which pair, at which
                    frequency, and how many pairs are over the limit.
  2. THE INDUCTOR   the L / Q / R difference, B against A, for the
                    port setup you picked.
  ALL PORT PAIRS    (up to 32 ports) a table of each pair's largest
                    difference, one decimal, "*" over the limit,
                    "." identical.

Under the text, the curves the verdict was read off, with the limit
lines drawn on them -- so a near-miss at one frequency is visible as
exactly that, and never hidden behind a single word.

Every S difference is in PERCENT OF FULL SCALE: 1 % means the two S
values are 0.01 apart (|S| is never more than 1). A pair is always
written S(i,j) -- "S(14,15), between port 14 and port 15" -- never
S1415, which cannot be read back from ten ports up.

Three rules that keep the numbers honest
----------------------------------------
  * L AND Q ARE JUDGED ONLY BELOW 85 % OF THE LOWER SELF-RESONANCE.
    Near and past the resonance the part is no longer an inductor:
    a 1 % shift of the resonance reads as hundreds of percent of L,
    and that is not a statement about the inductor. That region is
    shaded "not judged" on the plots, its worst difference is still
    printed, and the resonance shift itself is one sentence ("moved
    from 11.2 GHz (A) to 11.1 GHz (B), 1.2 % down").
  * NO PERCENTAGE OF NEARLY NOTHING. Where A's own value is under
    1 % of its typical (median) size in the band, a relative
    difference is not computed, and the reading says how many points
    were skipped.
  * ONE WILD POINT DOES NOT SET THE SCALE. A percentage axis covers
    the bulk of the points (never less than twice the limit) and
    says how many it leaves off; the worst value is still in the
    text.

When a file goes away
---------------------
Remove a loaded file and the window says so by name instead of
quietly comparing what it last saw; pick the files again at the
top. A renamed file is simply shown under its new name.

The text stays in English, like every other window in this tool.
