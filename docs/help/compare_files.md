Compare files -- are these files the same network?
==================================================

The question it answers: "I have the inductor extracted to 30 GHz,
and again to 50 GHz and 80 GHz. Inside the band they share, are
the others the same as the 30 GHz one?" An EM solver sizes its
mesh from the highest frequency it is asked for, so two runs of
one layout are two different discretisations and need not agree.
This workspace says how far apart they are, over the band each
pair shares, at two levels -- and each level can fail on its own.

It is a WORKSPACE of its own: press "Compare files" on the strip
under the menu bar ("RLC extraction" takes you back). The Loaded
Files panel stays where it is and stays shared; the left column
becomes the setup and the right side the results. Nothing on the
RLC side -- the Traces list, the editor, the plot -- is read or
changed by it.

Step by step
------------
  1. Load the files (Add File...). Two at least.
  2. Files to compare:
       Reference   the file the others are measured against. It
                   starts as the file with the LOWEST top
                   frequency -- the one whose whole sweep lies
                   inside the others' -- so the 80 GHz run is the
                   one being checked, not the reference of the
                   question about it. Pick another if you like;
                   your pick stays when you add more files.
       Compare     one tick box per other loaded file. Tick every
                   file you want checked against the reference.
                   A newly loaded file starts ticked.
  3. What to compare:
       Raw S-parameters only
                   just the files, every S-parameter of every
                   port pair.
       Extracted L/Q/R under this setup:
                   also the inductance, Q and resistance of a
                   port setup YOU define, solved on every file.
  4. Same if within  S [1] %  L [1] %  Q [5] %, and the Marker
     frequency (GHz).
  5. Compare.

There is NO default setup. With the setup tables empty (or "Raw
S-parameters only" chosen) the extracted part says "No setup
defined" and only the S-parameters are judged -- the tool does
not pick "port 1 to ground" for you and then hand you a verdict
about it.

The setup
---------
The same two tables as the RLC editor, and the same rules:

  Measurement ports   Name, + ports, - ports. One row per thing
                      you measure.
  Connections         ground, vdd, open, short, R / L / C rows
                      for the other ports. Ports not listed
                      anywhere are OPEN.
  Template            fills the tables with a starting point
                      (Port to GND, Between two ports, Loop with
                      shorted far end, Several nets (coupling)).
                      Over rows you typed it asks first.

See the "Setting up a measurement" tab for what each row does.
A problem is marked IN THE CELL as you type -- red for a setup
that cannot be solved, amber for one that is solved but worth a
second look -- with the reason under the tables. There is no
dialog. A setup with a red cell is not solved at all: the S
comparison still runs, and the extracted part says why it was
refused.

"Copy setup from trace..." (beside the measurement ports' "+ Add")
copies the setup of one of your traces into the tables, ONCE.
After that the two are not linked: edit either and the other
stays as it was. A trace built from several files is not offered
-- its port numbers name ports of more than one file.

The setup is solved on each file separately. A port one file does
not have is refused on that file, and the reading names the file.

Reading the result
------------------
  Verdict strip   (top right) one line per compared file:
                    ✓  the same as the reference within your
                       limits, and what was judged;
                    ✗  NOT the same, and the first thing over its
                       limit, in words, with where it is worst;
                    ?  could not be compared, and why (no band in
                       common, say).
  Plots           ΔS, ΔL, ΔQ, ΔR in %, one curve per compared
                  file, the reference is the zero line. The
                  dashed red lines are your limits; R has none.
                  A setup with several measurement ports draws
                  one dash style per port.
  Details         (collapsed until you open it) the full reading
                  of every pair, in the order you would ask:
                    IN SHORT          the answer, what is over
                                      its limit, and the L / Q / R
                                      difference AT THE MARKER;
                    WHAT TO DO NEXT   depends on what was found;
                    WHAT WAS COMPARED both sweeps, the band, whose
                                      points were used;
                    1. THE RAW FILE   every S-parameter: the
                                      largest difference, which
                                      pair, where, how many over;
                    2. THE INDUCTOR   L / Q / R, B against A, for
                                      your setup;
                    ALL PORT PAIRS    (up to 32 ports) each pair's
                                      largest difference, "*"
                                      over the limit, "."
                                      identical.

Every S difference is in PERCENT OF FULL SCALE: 1 % means the two
S values are 0.01 apart (|S| is never more than 1). A pair is
always written S(i,j) -- "S(14,15), between port 14 and port 15"
-- never S1415, which cannot be read back from ten ports up.

An L / Q / R difference of 100 % or more is written as a multiple
-- "B is 3.8 × A", or "-1.5 × A (the sign flipped)" -- because a
percentage that large is not a sentence anybody says.

Nothing is recomputed behind your back
--------------------------------------
  * Change the reference, the ticks, the choice or the setup, or
    load / remove a file, and the results stay on screen with the
    strip's first line saying "Out of date ... press Compare".
  * Change a LIMIT and the verdict is re-judged at once, from the
    numbers already computed.
  * Move the MARKER and the reading at the marker is re-read at
    once, from the curves already computed. Type a frequency in
    the Marker box, or click or drag on the plot: on release it
    lands on the nearest point of the reference's own sweep.
  * Remove a compared file and its line and curves go at once;
    remove the reference and every result goes.

What it compares, and on which points
-------------------------------------
  * ONLY THE BAND BOTH FILES OF A PAIR COVER. Nothing is
    extrapolated. Each compared file has its own band with the
    reference, and the strip says which.
  * ON THE COARSER FILE'S POINTS. The finer file is interpolated
    onto them, never the other way round: interpolating the coarse
    file would draw straight lines across its own gaps, and that
    error would read as a difference neither file contains. Two
    files with the same points are compared point by point, with
    no interpolation at all.
  * A different reference impedance (Z0) in the two files is
    allowed and is accounted for. A different PORT COUNT is not:
    the raw S comparison is refused, and says why.

Four rules that keep the numbers honest
---------------------------------------
  * L AND Q ARE JUDGED ONLY BELOW 85 % OF THE LOWER SELF-RESONANCE.
    Near and past the resonance the part is no longer an inductor:
    a 1 % shift of the resonance reads as hundreds of percent of L,
    and that is not a statement about the inductor. That region is
    shaded grey, "not judged (resonance)", its worst difference is
    still printed, and the resonance shift itself is one sentence
    ("moved from 11.2 GHz (A) to 11.1 GHz (B), 1.2 % down").
  * Q AND R ARE NOT JUDGED WHERE THE REFERENCE IS LOSSLESS. Where
    A's resistance is under 0.1 % of |Z| (Q above about 1000), its
    R is the file's rounding and Q = Im/Re is that rounding turned
    upside down: two such files give a +-100 % zigzag that means
    nothing. Those points are shaded grey, "not judged (A
    lossless)", kept out of the verdict, and counted in Details.
    L is still judged there.
  * NO PERCENTAGE OF NEARLY NOTHING. Where A's own value is under
    1 % of its typical (median) size in the band, a relative
    difference is not computed, and the reading says how many
    points were skipped.
  * ONE WILD POINT DOES NOT SET THE SCALE. A percentage axis covers
    the bulk of the points (never less than twice the limit) and
    says how many it leaves off; the worst value is still in the
    text.

The limits are defaults for you to change. How close is close
enough is your design margin's call, not this tool's.

Two files with the same name
----------------------------
Two extractions are often both called L.s1p. The Loaded Files
list then names each by as much of its folder as it takes to tell
them apart -- "30G/L.s1p" and "80G/L.s1p" -- and those are the
names in the tick boxes, the strip and the reading. The same file
loaded twice is "L.s1p" and "L.s1p (2)".

Saved with the session
----------------------
The reference, the ticks, the choice, the setup, the limits and
the marker are saved with the config (Save Config), like
everything else you typed. The results are not: after a load the
workspace says "Nothing compared yet" until you press Compare.

The text stays in English, like every other window in this tool.
