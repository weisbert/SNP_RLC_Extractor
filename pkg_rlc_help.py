"""
pkg_rlc_help.py  --  In-app help content + Help window.

A self-contained reference describing each measurement mode's
physical assumptions, input fields, result interpretation, common
use cases, and pitfalls. Opened from the GUI's "Help" button.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


HELP_OVERVIEW = """\
PKG RLC Extractor -- in-app reference

What this tool does
-------------------
Given a Touchstone (.sNp / .txt / any-extension) file containing
S-parameters of an N-port linear network, the tool:
  1. Converts S to Y (admittance).
  2. Applies user-specified port boundary conditions (terminations).
  3. Reduces the network via Schur complement to the chosen
     measurement terminals.
  4. Reports R, L, C, Q at a single frequency, or fits a broadband
     equivalent circuit (inductor model or capacitor model).
  5. With more than one measurement port (Mode 6), also reports the
     COUPLING between them: mutual inductance M, coupling factor k,
     the coupling ratio M/L, and the coupling capacitance C_c.
     See the "Mode 6 (Coupling)" tab.

Universal assumptions (apply to ALL modes)
------------------------------------------
1. AC small-signal analysis only. There is no DC operating point.
   "VDD" ports are AC-grounded -- mathematically identical to GND,
   so supply pins go into the GND Ports field together with the
   ground pins. (This is why there is no separate VDD mode any
   more; see the Mode 2 tab.)

2. Ports are 1-based in the GUI/CLI, 0-based internally.
   Conversion happens at the input boundary.

3. UNUSED ports default to OPEN-CIRCUIT (I = 0).
   This is the most common source of wrong results: if you forget to
   list a GND port in the GND Ports field, that port floats. It is
   then eliminated via Schur complement -- which preserves the
   network behavior at kept ports but does NOT tie the floating port
   to ground.

4. Multiple ports on the SAME side of a measurement terminal are
   SHORTED TOGETHER internally before measurement. If you list
   "1,2,3" as Port A in Mode 1, the tool treats them as one merged
   terminal carrying the sum of currents at a common voltage. The
   same is true of the "+" side and of the "-" side of a Mode 6
   measurement port.

5. All extracted R, L, C are TOTAL values for the network as seen
   between the chosen terminals, never per-unit-length. Per-length
   parameters require multi-section ABCD extraction (not in scope).

6. The reference impedance Z0 is read from the Touchstone option
   line (e.g. '# GHZ S MA R 50') and used for S<->Y conversion.

Single-frequency RLC formulas
-----------------------------
At each frequency point f (omega = 2*pi*f):
   R(f) = Re(Z(f))
   L(f) = Im(Z(f)) / omega           (signed; <0 means capacitive at f)
   C(f) = -1 / (omega * Im(Z(f)))    (signed; <0 means inductive at f)
   Q(f) = Im(Z(f)) / Re(Z(f))        (signed; matches Cadence convention)

Sign convention: values are reported with their physical sign, not absolute
value. For an inductor past SRF, Im(Z) flips negative -> L and Q go negative
and C becomes positive (the parasitic capacitance dominates).

Reading the results table
-------------------------
Each Calculate prints a single aligned table. Columns:
   ID      trace id (in brackets), matches the Traces list. Every
           row here is a trace that is ON the plot: hiding one takes
           it out of this table as well, and names it on one line
           underneath instead -- see "Showing and hiding curves".
   Label   user-given trace label (truncated)
   File    only shown when traces span >1 file (alias F1, F2, ...)
   Ports   compact port-config descriptor:
              M1: S:[1] G:[2,3]              -- Mode 1 (port-to-GND)
              M2: 1<->2 G:[]                 -- Mode 2 (port-to-port)
              M2: 1<->{2,3} G:[4]            -- multi-port terminal
              M3: 1<->2 G:[] S:[3-4]         -- Mode 3 with shorts
              M5: tank:1/2 C:3               -- Mode 5 (custom):
                                                measurement ports, then
                                                the connection-row count
              M6: <measurement-port list>    -- Mode 6 (+/- coupling)
   R/L/C/Q numeric values
   Sign    flag column. Always indicates the sign of Im(Z); may also
           carry a non-passive warning. Possible flags:
              ind   -- Im(Z)>0; the network is inductive at this
                       frequency. L>0 and Q>0; C is the negative
                       cap-equivalent and should be ignored.
              cap   -- Im(Z)<0; the network is capacitive at this
                       frequency. C>0 and Q<0; L is the negative
                       ind-equivalent and should be ignored. For an
                       inductor, this means the data point is past SRF.
              R<0   -- Re(Z) is negative, i.e. the extracted network
                       is non-passive at this frequency. Almost always
                       a numerical artifact (rank-deficient Schur
                       reduction at lossless points) or a port-config
                       error (e.g. forgetting to ground a return path).
                       Appended to the cap/ind flag, e.g. "cap,R<0".

Units modes (selectable from the dropdown above the results pane)
-----------------------------------------------------------------
   smart    -- each cell picks its own SI prefix and prints 3
               significant digits, e.g. "345 pH", "12.3 mOhm".
               Best when traces span very different magnitudes.
   aligned  -- one SI prefix per column (largest |value| in the column
               sets it). Header carries the unit, e.g. "L[pH]".
               Best for comparing variants of the same DUT where
               values share an order of magnitude.

Switching the units mode re-renders the most recent Calculate's table
without recomputing -- the existing log entries are preserved above.

Editing traces: there is no "Apply"
-----------------------------------
Whatever is in the editor IS what the selected trace holds. Type a
port number and the trace has it; the Traces list updates as you
type, and clicking another trace keeps the edit you just made rather
than discarding it.

A trailing "*" on a line in the Traces list means that trace's spec
has changed since the curve on screen was computed -- the numbers
you are looking at are older than the setup that describes them.
Calculate clears it.

"Calculate This Trace", at the bottom of the editor, recomputes only
the selected trace. With several traces over a large package file
that is the difference between iterating on one port spec and
re-reducing everything on every pass. The results table still shows
every trace that is on the plot; only the WORK is narrowed.

Choosing a colour and a line style
----------------------------------
Click the line preview next to "Style:". It expands into the 12
colours and the 4 line styles, each drawn the way the plot will draw
it. Click a colour or a style to take it; click the preview again to
fold the palette away. Tab reaches the preview and Enter, Space or
Down opens it.

A coupling trace is not one curve, so the preview does not pretend
it is: with G measurement ports it draws G self curves plus one
mutual curve per pair, each taking the NEXT colour in the palette.
The preview shows the run of colours the trace will occupy and "xN"
for how many. Two traces whose runs overlap will share colours --
that strip is how to see it before you plot.

Showing and hiding curves
-------------------------
Every line in the Traces list starts with a checkbox: [x] drawn,
[ ] hidden (hidden lines are also greyed). Three ways to toggle the
selected trace:

   * the "Show/Hide" button above the list
   * the space bar, with the list focused
   * "Plot: this trace" in the editor

Hiding takes effect immediately. Nothing is recomputed -- the curve
is rebuilt from the numbers already in memory -- and the V-line
cursors you have placed stay where they are. This is how to compare
two traces out of five without deleting the other three and typing
them in again.

The checkbox governs every output, not just the picture: a hidden
trace also leaves the results table and Export CSV. The table reads
as "what is on the plot", and a row for a curve that is not drawn
looks like a duplicate of the one that is -- in a CSV that same row
is simply one step further from where you would notice it.

It is still MEASURED, and it is named on one line under the table:

   hidden (measured, not plotted, not exported; show it to read or
   export it): [2] t2

Its numbers stay in memory, so showing it again costs no Calculate
-- tick it back on and export again to get it into a file.

Before and after: freezing a trace
-----------------------------------
Right-click a trace in the Traces list -> "Freeze as new trace".
That takes a SNAPSHOT: a second trace holding exactly the numbers
this one has now, drawn in the next colour and the next line style,
labelled with the time it was taken ("tank <14:32>").

Then change the original however you like and press Calculate. The
snapshot does not move -- Calculate skips it and the editor refuses
to write into it, so the two curves on the plot are genuinely the
before and the after, over the whole sweep rather than at the marker
frequency alone. Both are in the results table, both are in the
cursor readout, both go into Export CSV.

A frozen trace is marked with a snowflake in the Traces list, and
selecting it greys the editor out with a note saying so. Everything
else still works: show/hide, Remove, colour (via unfreeze), export.
Right-click -> "Unfreeze" gives it back to Calculate -- the next run
REPLACES the numbers it was holding.

One limit, and it is deliberate: a config file carries the setup and
never the results (see the "Save / Load" tab), so a frozen trace
comes back from Save/Load with its spec and no numbers. It says so
in the Results pane and reads "no numbers" in the Traces list rather
than quietly drawing nothing. Unfreeze it and Calculate to measure
it again -- which reproduces the snapshot exactly if the file has
not changed in the meantime.

Run history: every Calculate keeps its page
--------------------------------------------
The Results pane is a set of tabs. "Log" is the running commentary
it has always been; every Calculate adds a page beside it, newest
first, labelled "#7 10:42".

Each page carries the report that run produced, and above it:

   Run #12 · 14:32:07 · @ 5.000 GHz · 4 traces [1,2,3,5]
   changed since #11:  [3] gnd 6-14 -> 6-16

The second line is the useful one -- twenty runs are all at 5 GHz
and nobody remembers what they were doing at 14:32, but "I widened
the ground group" is what tells two pages apart.

Old pages are dropped automatically, OLDEST FIRST, three at a time
by default. To stop one being dropped, press "Keep" (or right-click
its tab). A kept page is never dropped by anything automatic; the
only way it goes is right-click -> "Close this run". Because kept
pages have their own budget, pressing Calculate can never be blocked
by them and can never throw one away -- if the kept set is full the
Keep button is already disabled and says so.

Runs ▾ lists every page with its full description; that is where to
look once the tabs are too narrow to read. It also sets the two
limits (how many automatic pages, and how many tabs in total).

One thing to watch, and every older page says it out loud:

   ! the plot and Export CSV show run #12, not this page

The plot and Export CSV always show the LATEST numbers. Reading an
older page does not change what a Calculate or an export produces.
To compare curves rather than tables, freeze a trace (above) --
that is the tool for two curves on one plot.

Run history is in memory only: Save Config carries the setup, never
the results, so run pages do not survive a save/load.

Broadband fit models
--------------------
Inductor model    : Z(f) = R_dc + R_ac * sqrt(f) + j * 2*pi*f * L
                    (sqrt(f) term captures skin-effect loss)
Capacitor model   : Z(f) = R_esr + j * 2*pi*f * L_esl + 1/(j*2*pi*f*C)
                    (R_esr = ESR, L_esl = ESL parasitic inductance)
Auto              : picks based on Im(Z) sign distribution; if mixed,
                    fits both and returns lower-RMSE.

Numerical notes
---------------
- Schur complement uses np.linalg.solve, falls back to lstsq with a
  warning if Y_oo is singular (most often Mode 3 with very weakly
  coupled merged ports).
- Y at a Touchstone frequency where the network is purely lossless
  may be rank-deficient; the tool handles it via lstsq fallback.
- The final node admittance is inverted with a pseudo-inverse
  whenever it is singular (this applies to a single +/- measurement
  port too, not just Mode 6). A fully floating structure (e.g. two
  isolated coils, no ground port) is singular by construction -- its
  null direction is the common mode, which the balanced +/- drive
  never excites -- so the "Rank-deficient node admittance" message
  there is INFORMATIONAL, not an error.
- A probe that is NOT orthogonal to that null direction (a
  ground-referenced probe on a structure with no ground path) has no
  return path for its current. The pseudo-inverse would fabricate a
  plausible finite number, so the tool reports NaN for that
  measurement port and says so. See the Mode 6 tab.
- Where the network is non-degenerate nothing above applies: the
  historical inv()-based expressions run unchanged, which is what
  tests/test_golden_regression.py pins bit-for-bit.
"""


HELP_MODE1 = """\
Mode 1 -- Port(s) -> GND  (driving-point impedance)
===================================================

Physical model
--------------
Inject a current at the Signal port(s); measure voltage at that
terminal relative to the GND port(s). All other ports left open.

         Signal port(s)    <-- shorted together if multiple
              |
              v
       +------+------+
       |   Network   |
       |  (Y-matrix) |
       +------+------+
              |
            GND port(s)

Inputs
------
Signal / Port A   : the port(s) being driven. If you list more than
                    one (e.g. "1,2,3"), they are tied together
                    internally before driving.

GND Ports         : the port(s) held at V=0 (reference ground).
                    LEAVE BLANK ONLY IF the Touchstone reference
                    ground is the only "ground" -- i.e. the network
                    has an implicit ground node and no explicit GND
                    port. Otherwise you MUST list the GND port(s)
                    or the result will be wrong (those ports will
                    float as I=0 instead of being grounded).

Result interpretation
---------------------
Z(f) = V_signal / I_signal at each frequency, with the shorted-
together signal terminal as the "+" node and GND as the "-" node.

Then R, L, C, Q follow the universal RLC formulas at the user-
selected RLC frequency.

Typical use cases
-----------------
* Bond-wire / via inductance from one die pad to package GND:
    Signal = die-pad port,  GND = GND port(s)
* Power-rail impedance from VRM input pin to die ground:
    Signal = VRM pin,       GND = die GND pins (list ALL of them)
* DCO inductor characterization (1-port inductor symbol):
    Signal = port 1,        GND = (blank, if only 1 port exists)

Common mistakes
---------------
- Forgetting GND Ports => "open" results, much higher than expected
- Listing the same port in both Signal and GND fields
- Expecting per-length L: result is the TOTAL inductance of the path
"""


HELP_MODE2 = """\
Mode 2 -- A <-> B  (port-to-port impedance)
===========================================

Physical model
--------------
Inject equal-and-opposite currents at port group A vs port group B
(differential current source). Measure (V_A - V_B). All listed GND
ports are V=0; all unlisted ports are OPEN.

   Port A group           Port B group
       |                       |
       v                       v
   +---+---------- Network ----+---+
                  (Y-matrix)
       ^
   any GND ports here are V=0;
   any other port is OPEN (I=0)

Inputs
------
Signal / Port A   : the "+" group. Multiple ports => shorted together.
Port B            : the "-" group. Multiple ports => shorted together.
GND Ports         : optional V=0 ports. Put GROUND pins AND SUPPLY
                    (VDD) pins here -- see below. Often blank for
                    differential measurements where the network has
                    no explicit ground port.

VDD ports go in the GND Ports field
-----------------------------------
Older builds of this tool had a separate "Mode 4 (A <-> B + VDD)"
with its own VDD Ports box. That mode is gone and its job moved
here, because for AC small-signal analysis an ideal supply IS a
short to the reference node: a VDD ball and a GND ball impose the
same boundary condition, V = 0. The old Mode 4 computed exactly what
Mode 2 computes when both sets are listed in GND Ports -- there was
never any numerical difference, only a label.

So a PDN measurement with mixed pins is now:
    Signal / Port A = die signal pin
    Port B          = die return pin
    GND Ports       = all GND balls AND all VDD balls

The VDD/GND distinction still matters to you -- different schematic
nets, different DC return paths -- it just does not change the AC
impedance. If you need it visible in the report, keep it in the
trace Label, e.g. "PDN (VDD balls included)".

Algorithm
---------
1. Apply terminations.
2. Schur-eliminate open ports.
3. Collapse the surviving Y matrix to a 2x2 by shorting within
   each group (sum rows and columns of the Y matrix).
4. Z_2x2 = inv(Y_2x2)
5. Z_between = Z11 + Z22 - Z12 - Z21
   (this is the proper differential impedance between the two
    shorted groups)

Typical use cases
-----------------
* Differential pair input differential capacitance C_diff:
    A = in_p, B = in_n
    far-end pins (out_p, out_n) LEFT BLANK in all fields
    => far end is automatically open (I=0)
    Result Z is mostly capacitive at low freq -> fit Capacitor model
    Reports total C_diff between in_p and in_n.

* Loop impedance between two probe points on a power plane:
    A = probe_1, B = probe_2

* Trace impedance to a dedicated ground port:
    A = signal pin, B = adjacent ground pin

* PDN impedance with mixed VDD/GND pins (the old Mode 4):
    A = die signal pin, B = die return pin,
    GND Ports = all GND balls + all VDD balls

Common mistakes
---------------
- Using Mode 2 to measure inductance of a SINGLE-ENDED trace --
  that is Mode 1 (signal -> GND), not Mode 2.
- Forgetting that unlisted ports are OPEN -- if your differential
  pair has a far end that should be SHORTED, you need Mode 3.
- Leaving supply pins unlisted because "they are not ground". They
  float as I=0 and the impedance comes out too high; list them in
  GND Ports.

Relation to Mode 6
------------------
Mode 2 measures ONE differential terminal pair. Mode 6 is the same
measurement generalised: any number of +/- terminal pairs at once,
which is what lets it report the mutual coupling between them. For
a single pair, Mode 6 with "1 / 2" returns bit-identical numbers to
Mode 2 with A=1, B=2 -- it is literally the same code path.
"""


HELP_MODE3 = """\
Mode 3 -- A <-> B + Short Pairs
===============================

Physical model
--------------
Same as Mode 2, but with additional constraints that certain port
pairs are physically shorted to each other (V_i = V_j, currents
add). This models the effect of installed components or wires.

   Port A          Port B
     |                |
     v                v
   +-+- Network -----+-+
        |       |
       i---j   (i and j are tied together: V_i=V_j)
        ^
   "Short Pairs" entries: "i-j, m-n, ..."

Implementation
--------------
For each (i, j) in Short Pairs:
  - In the Y matrix: row j is added into row i, col j is added into
    col i, then row j and col j are deleted.
  - The merged port keeps the termination of whichever original
    port had a non-Open termination (Signal/Ground takes precedence
    over Open).
After all merges, proceed with normal Mode-2 algorithm.

Inputs
------
Signal / Port A,
Port B,
GND Ports         : same as Mode 2.
Short Pairs       : "45-46, 47-48" syntax. Each entry uses dash to
                    join ports that should be tied together. To short
                    MORE THAN TWO ports as one node, chain them with
                    additional dashes: "1-2-3-4" shorts all four
                    together. Multiple independent groups are
                    separated by commas:
                        "1-2-3-4"      -- four ports as one node
                        "3-4, 5-6"     -- two independent pairs
                        "3-4-5, 7-8"   -- a triple plus a pair

Typical use cases
-----------------
* Differential trace LOOP INDUCTANCE (very common):
    A = in_p, B = in_n
    Short Pairs = "out_p-out_n"   (e.g. "3-4" if those are the ports)
    GND Ports = whatever ground port the file has, or blank
    => Far end shorted forces signal to return through the trace.
    Fit Inductor model. Reports L_loop, R_dc.
    For ideal coupled inductors: L_loop = 2 * (L_self - M)

* Decap mounting model:
    Two decap pads (top and bottom) shorted to model the installed
    cap; signal port pair measured to see the resulting impedance.

* Wire bond / via array shorted in parallel:
    Multiple via ports paired up to model the parallel array.

Common mistakes
---------------
- Specifying signal ports inside a Short Pair (logic conflict --
  the merged port can't be both Signal-A and Signal-B).
- Forgetting the dash: "45,46" is two SEPARATE ports in a port
  range, not a short pair. Use "45-46".
"""


HELP_MODE5 = """\
Mode 5 -- Custom (advanced)
===========================

The named modes cover the most common configurations. For anything
beyond them -- arbitrary lumped R/L/C terminations, mixed short and
lumped couplings, etc. -- use Custom mode.

Two tables
----------
Mode 5 is the Mode 6 measurement-port table (what am I measuring)
plus a connections table underneath it (what else is attached).
Both grow by one row per click of "+ Add"; the "X" at the end of a
row deletes it.

MEASUREMENT PORTS -- Name / "+ ports" / "- ports". Identical to
Mode 6; see that tab. Two or more rows give you the coupling matrix
(M, k) between them, in Mode 5 exactly as in Mode 6.

CONNECTIONS -- Type / Port / To / R / L / C:

   Type          Attaches                            uses To?  R/L/C?
   ------------  ----------------------------------  --------  ------
   ground        V = 0                               no        no
   vdd           V = 0 as well (AC small-signal)     no        no
   open          nothing (the default anyway)        no        no
   short         ties Port to To                     YES       no
   rlc_gnd       series R-L-C from Port to ground    no        YES
   rlc_between   the same element from Port to To    YES       YES

   * Port and To take the full range syntax, so a package's ground
     balls are ONE row: "6-14" or "35:1:45". See the Input syntax
     tab. ("35:45" is an error -- the MATLAB form needs all three
     fields.)
   * The Port / To dropdowns list port NUMBERS, not names. To see
     which ball is which on an unfamiliar file, click "Show Ports"
     at the top of the left panel: it opens the "Ports & Roles"
     window, which lists every port with its name, the role your
     spec gives it, and which row said so -- and can write a
     selection back into these tables as a collapsed range. (A
     name-bearing dropdown does not fit the editor's width; it is
     planned, not forgotten.)
   * "To" is ignored by ground / vdd / open / rlc_gnd, which are
     always to ground. rlc_between takes exactly ONE partner port
     (an N-to-M lumped element is ambiguous -- star? mesh?).
   * A range on an rlc_gnd row is ONE ELEMENT PER PORT, not one
     element shared by them. "21:1:25" with L = 80p is five
     separate 80 pH inductors, one from each of ports 21..25 to
     ground -- the right model for five ground balls each with its
     own ball inductance. (If those five ports are one net inside
     the file, the five inductors end up in parallel there, so the
     die sees ~16 pH.) For ONE shared 80 pH instead, take two rows:
     "short 21:1:25 -> 21", then "rlc_gnd 21 L=80p".
   * Two rlc_between rows on the SAME port pair are two elements in
     PARALLEL -- their admittances add, which is how to write
     R_on || C_ds for a switch. Two rlc_gnd rows on the same PORT
     are NOT: a port carries one termination, so the row further
     DOWN the table wins and the other is discarded. (Two rlc_gnd
     rows on different ports that a short ties together do add,
     because the merged node keeps both.) R, L and C within one
     row are always a SERIES branch.
   * R / L / C hold the bare value; the unit is in the header.
     SI suffixes apply, and "5m" is 5 milli while "5M" is 5 Mega.
     The value must be ONE word: "5 m" and "1 uF" are REJECTED,
     because the text form is whitespace-separated and "R=5 m"
     would quietly compute 5 ohm instead of 5 milliohm.
   * A BLANK R/L/C means OMITTED, which is not zero. An omitted C
     is "no capacitor in the series branch"; C = 0 would be an open
     circuit. An element row with NO R, L and C at all is a 0-ohm
     short and gives NaN everywhere; the strip says so.

Under the tables, two lines:

   Ports (45): 4 probe . 8 ground . 1 element . 32 open
   ✓ port 13 -> GND: 5 mOhm + 500 pH + 1 uF

The first is the port census -- every port of the file, bucketed.
The second is either a problem or, when there is none, the PARSED
value of your element rows. That echo is there because "5m" and
"5M" are one shift key and nine orders of magnitude apart. The
strip shows at most two lines; "Calculate All & Plot" writes the
whole list to the Results pane.

Edit as text...
---------------
The button above the connections table opens the equivalent DSL
text. It is not a second format: the tables serialise to exactly
that text and that text is what the parser sees.

Your text comes back rewritten into canonical form -- "gnd" becomes
"ground", "signal a" becomes "signal A", "r=" becomes "R=", R/L/C
are reordered to R, L, C, blank lines and end-of-line comments are
dropped, and every measurement port is emitted BEFORE every
connection (which is what makes a later "ground" win over a probe
on the same port). Anything the tables cannot represent -- comment
lines, hand-written directives -- is kept verbatim and appended.

If reordering would change what your spec computes, the whole spec
is kept as text instead of being moved into the tables, and you are
told so. It still computes exactly what it computed before.

When that happens the tables can be EMPTY while the kept text is
what decides the answer -- and, being emitted last, it wins over
anything you then type into the tables. The Connections caption
therefore carries a permanent "(+N lines kept as text)" marker, and
the validation strip says so when the kept text defines measurement
ports the table does not show.

DSL syntax (what "Edit as text..." speaks)
------------------------------------------
One directive per line. Lines starting with '#' are comments.
Each directive begins with a 1-based port number or range, then a
kind:

  <port>  signal <name> [+|-]           (sign defaults to '+')
  <port>  signal A                      (legacy: '+' side of group A)
  <port>  signal B                      (legacy alias of 'signal A -')
  <port>  ground                        (or 'gnd')
  <port>  vdd                           (alias of ground)
  <port>  open                          (default if not listed)
  <port>  short_to <other_port>
  <port>  lumped_to_gnd <R/L/C params>
  <port>  lumped_between <other_port> <R/L/C params>

R/L/C parameters use 'R=...', 'L=...', 'C=...' (any subset).
Series RLC is computed automatically: Z = R + jwL + 1/(jwC).
Values support SI suffixes:
   f=1e-15  p=1e-12  n=1e-9  u=1e-6  m=1e-3
   k=1e3    M=1e6    G=1e9   T=1e12
or plain scientific notation (1.5e-9, 50, 0.001).

'signal' -- named groups and probe signs
----------------------------------------
'signal' attaches a probe to a port. <name> names the MEASUREMENT
PORT: every port carrying the same name belongs to the same
measurement port, and the optional sign says which probe touches it.

    3 signal tank +      red probe   ('+' is the default)
    4 signal tank -      black probe

Declare two or more names and you get a coupling measurement -- the
same thing Mode 6 does, just spelled out one port at a time. Two
coils in a 4-port file:

    1 signal tank +
    2 signal tank -
    3 signal vco2 +
    4 signal vco2 -

Read the Mode 6 tab for what M, k and M/L mean.

Rules and gotchas:
  * The sign is a SEPARATE token. "signal tank -" is group "tank",
    minus side; "signal tank-" is a group literally named "tank-".
    Keep the space.
  * Group names are case-sensitive ("tank" and "Tank" are two
    different measurement ports) -- with one exception, below.
  * Shorting the '+' and the '-' port of the same measurement port
    to each other is an error: it would short your own probes.

LEGACY: "signal A" and "signal B" keep their historical meaning, and
they are the one case-insensitive pair ('a' == 'A'). "signal B" is
exactly "signal A -" -- the black probe of group A -- which is why
every old Mode 1/2/3 spec still parses and still produces
bit-identical numbers. Because of that alias, A and B are RESERVED:
pick any other name for new multi-port work.

Examples (text form; each line is one table row)
------------------------------------------------
50-ohm-terminated through-line (1-port file):
    1 signal A
    2 lumped_to_gnd R=50

Differential trace with 50-ohm termination at far end:
    1 signal A
    2 signal B
    3 lumped_to_gnd R=50
    4 lumped_to_gnd R=50

Decap modeled as 1pF + 0.1nH ESL between two pads:
    1 signal A
    2 signal B
    3 lumped_between 4 R=0.01 L=0.1n C=1p

Bond-wire RL model from a die pad to package pin:
    (where port 3 is bond-wire model entry)
    1 signal A
    2 ground
    3 lumped_to_gnd R=0.5 L=1.5n

Interaction with named modes
----------------------------
Custom mode is purely additive. Anything you can express via the
named modes -- including Mode 6's multi-port coupling setups -- can
be expressed in Custom mode. The named modes exist for ergonomic
shortcuts, not extra capability.

For example, this Custom spec is exactly equivalent to Mode 3
with A=1, B=2, ShortPairs="3-4":

    1 signal A
    2 signal B
    3 short_to 4

(Verified by unit test -- numerical results identical.)
"""


HELP_MODE6 = """\
Mode 6 -- +/- Ports / Coupling (M, k)
=====================================

What this mode adds
-------------------
Modes 1-5 answer "what impedance do I see at ONE terminal?".
Mode 6 answers that for SEVERAL terminals at once, and additionally
reports how strongly they talk to each other: mutual inductance M,
coupling factor k, the coupling ratio M/L, and the coupling
capacitance C_c.

Typical questions it answers:
  * How much does the PA coil pull my VCO tank?
  * Two adjacent bond-wire loops -- what is M between them?
  * Is the coupling between these two on-chip inductors under my
    -30 dBc budget, and did my layout change help?

Mode 5 is this same measurement-port table plus a connections table
underneath it, so anything you can set up here you can also set up
there with extra terminations attached -- and two or more
measurement ports produce the coupling matrix in either mode.

The two-probe mental model
--------------------------
Think of a bench multimeter. Every measurement is ONE pair of
probes:

      RED probe  --> the "+" ports
      BLACK probe --> the "-" ports

A measurement port is therefore written

      name = <+ ports> / <- ports>

and it means exactly what it looks like:

     RED   1 o---+---------------+---o 3   RED
                 |    Network    |
   BLACK   2 o---+  (Y-matrix)   +---o 4   BLACK

           "tank = 1 / 2"        "vco2 = 3 / 4"

Three rules, and that is the whole model:
  1. Ports on the SAME side are tied together (parallel, unsigned) --
     exactly as multiple ports in Port A behave in the older modes.
     "1,3 / 2,4" is a red probe touching 1 and 3 at once.
  2. An EMPTY "-" side means the port is referenced to the Touchstone
     ground, i.e. "5 /" or just "5" is the single-ended measurement
     Mode 1 does.
  3. There are NO weights. A port is on the plus side, on the minus
     side, or not in this measurement port at all.

Inputs
------
Measurement ports : a TABLE, one row per measurement port, with a
                    "+ Add" button for as many as you need. Each row
                    is Name / + ports / - ports. Names are optional;
                    unnamed rows are auto-named P1, P2, ... The names
                    "A" and "B" are reserved for the legacy modes.
                    Both port columns take the full range syntax
                    ("5,7" or "5:1:8") -- see the "Input syntax" tab --
                    so a shield tapped at eight ports is one row, not
                    eight. Delete a row with the "X" at its end.
GND Ports         : V=0 ports, exactly as in the other modes.
                    Include supply pins here too (AC ground).
                    A port that is already a probe may NOT also be
                    listed here: a probe side is tied together, so
                    grounding one of its ports grounds the whole
                    side. The tool rejects that instead of quietly
                    dropping the port from the probe.
Short Pairs       : optional, same syntax and meaning as Mode 3.
Everything not listed anywhere is OPEN and gets Schur-eliminated,
same as always.

Port numbers are checked against the file. A number the file does
not have -- "3 / 5" on a 4-port file, the classic one-digit typo --
is an error, not a silently ground-referenced probe.

One measurement port: the differential self impedance
-----------------------------------------------------
One row in the table is enough:

      Name: tank    + ports: 1    - ports: 2

You get the DIFFERENTIAL self impedance of that structure -- the
impedance a differential driver sees across the two terminals, so
L is the differential self-inductance L_diff, the number a VCO tank
actually resonates with.

Contrast that with tying both terminals into one node:

      Name: tank    + ports: 1,2   - ports: (empty)
                                   ^ both on the RED probe

That is the COMMON-mode impedance (Mode 1 with Signal = "1,2"),
which is a completely different number and is usually not what you
want for a balanced coil. The "+/-" split is what makes the
distinction explicit.

(For a single pair, "1 / 2" returns bit-identical numbers to Mode 2
with A=1, B=2 -- same code path. Mode 6's new capability is having
several such terminals alive at the same time.)

Two or more measurement ports: the Z matrix
-------------------------------------------
With G measurement ports the tool builds a G x G impedance matrix
at every frequency:

      Z[a][a]   self impedance of port a   -> R, L, C, Q
      Z[a][b]   mutual impedance           -> M, k, M/L, C_c

ASSUMPTION -- and this is the important one:

      Z[a][b] is defined with ALL OTHER measurement ports OPEN.

That is the textbook definition of mutual inductance: drive 1 A into
port b with nothing loading port a, and read the open-circuit
voltage that appears at port a. If your real circuit loads the
victim heavily, M is still the right primitive to extract -- you
then use it in your circuit simulator, where the loading is
modelled. Just do not confuse the open-port M with a
short-circuit transfer measurement; they are different numbers.

The OTHER half of the assumption is what you did with the ports
that are not probes. Everything you did not name is OPEN, and
whether a package's ground balls are ideal grounds, series
inductors or left open moves M by decibels, not percent. The
"Where the number came from" section at the bottom of this tab is
the tool that answers "how much of my M is that assumption?".

What each reported value means
------------------------------

M -- mutual inductance, in henries
      M = Im(Z_ab) / omega          (omega = 2*pi*f)

  Faraday's law, in one sentence: if the current in structure b
  changes at 1 A/s, then M volts appear across structure a. So
  M = 20 pH means a 1 A/ns edge (that is 1e9 A/s) induces
  20e-12 * 1e9 = 20 mV. M is an absolute number: it depends on how
  big both structures are, so it is the right thing to plug into a
  simulator but a poor way to compare two layouts of different size.

k -- coupling factor, dimensionless
      k = M / sqrt(L_a * L_b)

  The fraction of the magnetic flux from one structure that links
  the other. k = 0 is perfect isolation, k = 1 is an ideal
  transformer with every flux line shared. It divides out how big
  each structure is, which is exactly why it is the number to
  compare layouts with.

  Rough on-chip scale:
      0.001 - 0.05   two inductors that are NOT meant to couple
                     (this is isolation / pulling territory)
      0.05  - 0.3    close neighbours; usually a layout problem
      0.3   - 0.5    loosely coupled, e.g. deliberately spaced coils
      0.5   - 0.9    a deliberate on-chip transformer

  k is NaN when L_a or L_b is <= 0 (i.e. that port is past its SRF
  and is capacitive at this frequency) -- the formula has no meaning
  there. If |k| > 1 the tool flags it: passive structures cannot do
  that, so suspect the input S-parameters (bad de-embedding,
  non-passive EM data, wrong port map).

M/L -- coupling (injection) ratio, dimensionless (and in dB)
      M / L_a   and   M / L_b

  This is the number to compare against an injection or pulling
  budget, and it is frequency-INDEPENDENT. Why the frequency
  cancels, in three lines:

     1. Faraday: the aggressor current I_agg induces an EMF
            V_emf = j*omega*M*I_agg
        sitting in series with the victim's own tank branch.
     2. That is a Thevenin source behind the victim's own
        inductance. Convert it to Norton:
            I_inj = V_emf / (j*omega*L_a)
                  = (j*omega*M*I_agg) / (j*omega*L_a)
     3. The j*omega cancels:
            I_inj = (M / L_a) * I_agg

  So a single scalar tells you what fraction of the aggressor's
  current gets injected into the victim tank, at every frequency.

  Step 2 approximates the victim's branch impedance as j*omega*L_a,
  i.e. it drops R_a. That is why this is a FIRST-ORDER budget
  number and not the exact current-transfer ratio. The exact ratio
  into a shorted port a is

      I_a / I_b = -Z_ab / Z_aa = -j*omega*M / (R_a + j*omega*L_a)

  which equals M/L_a only where omega*L_a >> R_a. Around and below
  the R = omega*L corner the two diverge badly: for L_a = 2 nH,
  R_a = 1.5 ohm, M = 0.9 nH the tool reports M/L_a = 0.450 at every
  frequency while |Z_ab/Z_aa| is 0.038 at 10 MHz, 0.289 at 100 MHz
  and 0.447 at 1 GHz. Use M/L for budgeting at the tank frequency,
  where the approximation is the point; do not read it as a measured
  current ratio at low frequency.

  Worked number:
      M = 20.1 pH, L_a = 1.00 nH
      M / L_a = 20.1e-12 / 1.00e-9 = 0.0201 = 2.01%
      in dB:  20*log10(0.0201) = -33.9 dB
      against a -30 dBc budget: PASS, with 3.9 dB of margin.

  Note the two ratios M/L_a and M/L_b are different numbers when the
  two structures differ in size: use M/L_victim, i.e. divide by the
  L of the structure being disturbed.

C_c -- coupling capacitance, in farads
      C_c = -1 / (omega * Im(Z_ab))

  This is the SAME Im(Z_ab), just read as a capacitance instead of
  an inductance. Which reading applies is decided by the sign:

      Im(Z_ab) > 0  -> the coupling is inductive here. Read M.
                       (C_c comes out negative and is meaningless.)
      Im(Z_ab) < 0  -> the coupling is capacitive here. Read C_c.
                       (M comes out negative; it is the negative
                        inductive-equivalent, not a real inductance.)

  Both are always computed and both are always shown -- the tool
  does not hide one -- so use the sign to pick. Electric-field
  coupling dominating over magnetic is normal for closely spaced
  traces with no shared magnetic loop, and for any structure above
  its self-resonance.

reciprocity error -- a self-check, NOT a result
  A passive, reciprocal network must satisfy Z_ab = Z_ba. The tool
  reports
      max|Z_ab - Z_ba| / max|Z_ab|
  over the FINITE off-diagonal entries, as a health check on the
  whole chain (EM solve -> Touchstone -> parse -> reduce).

      ~1e-16 to 1e-13   healthy; this is just floating-point noise
      1e-9  to 1e-6     still normal for a real EM solve; S12 and
                        S21 rarely agree to the last bit
      above 1e-3        the tool raises the alarm: non-reciprocal or
                        non-passive EM data, an interpolated /
                        extrapolated file, or a corrupted /
                        truncated Touchstone.

  1e-3 is the single alarm threshold, shared by the GUI results
  pane and the --cli report, so the same file never gets two
  different verdicts.

  It is 0.0 by definition when there is only one measurement port.
  Entries that are NaN (see "no return path" below) are left out of
  the metric rather than poisoning it.

The pair list is ranked, and its tail is folded away
----------------------------------------------------
Six measurement ports make 15 unordered pairs. In the GUI results
pane they are printed STRONGEST FIRST, ordered by the larger of the
two coupling ratios, max(|M/L_a|, |M/L_b|) -- the budget number, so
the pair at the top is the one to look at. That figure is repeated
in dB on the same line as M and k.

Pairs whose ratio is under -60 dB are folded into one line:

      ... +7 pairs below -60 dB (see Export CSV)

They are still MEASURED and still EXPORTED -- Export CSV writes a
M_nH / k column for every unordered pair, with no floor at all. Two
things are never folded away: the strongest pair (even when it is
itself below -60 dB, so the block always answers "how much coupling
is there"), and any pair whose ratio is undefined, which means a
missing measurement rather than a small one.

The ordering and the -60 dB test are the only places this tool takes
a magnitude -- see the next section.

Signs are physical and are never clipped
----------------------------------------
M, k and C_c are reported with their sign, exactly like R/L/C/Q
elsewhere in this tool. The sign is real information:

  * It encodes winding / probe orientation. Swap the "+" and "-"
    ports of one measurement port ("3 / 4" -> "4 / 3") and M, k and
    the M/L ratios all flip sign. The magnitude does not change.
  * It matters when two coupling paths ADD or CANCEL. Two aggressors
    with M = +20 pH and M = -20 pH into the same victim cancel; both
    reported as +20 pH they would look twice as bad as reality.
  * The dB fields are magnitudes: 20*log10(|M/L|). They are NaN when
    the ratio is exactly zero or non-finite.

For "how bad is this coupling?" read the magnitude. For "do these
two paths help or fight each other?" read the sign.

Messages you may see
--------------------
"Rank-deficient node admittance at freq[...] (pinv used; expected
 for a fully floating structure)"
  Informational, not an error, and capped at 3 lines. A structure
  with no ground reference at all -- two isolated coils in a 4-port
  file, the normal coupled-inductor case -- has a singular node
  admittance whose null direction is the common mode. Your balanced
  +/- drive is orthogonal to it, so the pseudo-inverse returns the
  correct answer. Expect this message on every clean floating
  coupling run. Sanity-check the result with the reciprocity error
  instead.

"Measurement port(s) '...' have no return path for the injected
 current ... Their row and column of Z are NaN."
  This one IS an error in your port setup, not a note. The
  pseudo-inverse is only valid for probes that are orthogonal to
  that null direction. A ground-referenced probe (empty "-" side)
  on a structure with no path to the reference node is not: you are
  asking a current to return through a wire that does not exist.
  Rather than invent a finite-looking number -- the old behaviour
  reported exactly Z_series/4 for a floating pair probed
  single-ended, and a flat 0 ohm for a floating series element --
  the tool NaNs the whole row and column of that measurement port
  and names it. Other measurement ports in the same run are
  unaffected and keep their exact values.
  Fix: give the port a "-" side, or add the ground ports the
  structure actually has.

"Schur contraction cancelled to roundoff at freq[...]"
  Also a port-setup error. The reduction of the unused ports left
  an admittance under 1e-12 of the terms that produced it, i.e.
  nothing but cancellation noise; the numbers that follow are
  roundoff amplified to ~1e16 ohm. Same cause and same fix as
  above. The values are still printed (this check is a magnitude
  heuristic, so it never converts a result to NaN by itself) --
  just do not read them.

Fully worked example: two coils on one die
------------------------------------------
File:    4-port EMX file. Coil "tank" = ports 1 (+) and 2 (-);
         coil "vco2" = ports 3 (+) and 4 (-). No ground port; the
         coils float.
Goal:    Is the magnetic coupling between them under a -30 dBc
         injection budget at 10 GHz?

Field entries:
   Mode        = 6 (+/- Ports / Coupling)
   Measurement ports table:
        Name    + ports    - ports
        tank    1          2
        vco2    3          4
   GND Ports   = (blank -- the file has no ground port)
   RLC Freq    = 10 GHz

Results at 10 GHz:
   self:
      tank   R = 1.62 Ohm   L = 1.00 nH   Q = 38.8
      vco2   R = 1.71 Ohm   L = 1.05 nH   Q = 38.6
   coupling  tank <-> vco2:
      Z_ab   = 0.0071 + j1.263 Ohm     (Im > 0 -> inductive, read M)
      M      = 1.263 / (2*pi*10e9) = 20.1 pH
      k      = 20.1p / sqrt(1.00n * 1.05n) = 0.0196
      M/L_tank = 2.01%  (-33.9 dB)
      M/L_vco2 = 1.91%  (-34.4 dB)
   reciprocity error = 3.4e-16          (healthy)
   note: "Rank-deficient node admittance ..." (expected: floating)

How to read it:
   k = 0.0196 is in the "unintended coupling" band -- these are two
   separate inductors, not a transformer, which is the sanity check
   that the port map is right.
   The budget number is M/L of the VICTIM. If the tank is the victim,
   -33.9 dB is 3.9 dB inside a -30 dBc budget. PASS.

The layout-iteration loop:
   1. Change the layout (more spacing, a guard ring, rotate one coil
      90 degrees, add a patterned shield).
   2. Re-run EM, load the new file, hit Calculate.
   3. Compare M/L_victim in dB to the budget, and k against the
      previous layout.
   4. Repeat until it passes.
   No VCO / PLL simulation is needed per iteration -- M/L is
   frequency-independent, so one EM run gives you the whole answer.
   Simulate the full loop once at the end to confirm.

Common mistakes
---------------
- Putting both terminals of a balanced coil on the "+" side
  ("1,2 /"). That is the common-mode impedance, not L_diff.
- Expecting M with the other structure SHORTED. The Z-matrix
  convention is other measurement ports OPEN. Shorting is a
  different (and load-dependent) quantity.
- Claiming one port in two measurement ports, or putting the same
  port on both sides of one measurement port -- both are errors.
- Naming a measurement port "A" or "B". Reserved for the legacy
  modes; pick anything else.
- Reading k where L has gone negative (past SRF). k is NaN there by
  design, with a note saying so -- move the RLC frequency below SRF.
- Forgetting the GND ports of a package file. Unlisted ports float,
  and the extracted M is then not the one your circuit sees. If the
  structure has no ground path at all you now get "no return path"
  and NaN rather than a plausible-looking wrong number.
- Comparing raw M across layouts that changed the coil size. Compare
  k, or M/L of the victim.
- Reading M/L as a measured current ratio far below the tank
  frequency. It is a first-order (R-free) budget number; see the
  M/L entry above.


Where the number came from: attribution (advanced)
--------------------------------------------------
Everything above tells you WHAT M is. This section is about WHERE
it came from, and it exists because of a real 6.07 dB argument:
the same two coils, out of the same EM solve, extracted twice,
gave |M| = 1.71 pH and |M| = 3.44 pH. Both runs were correct. What
differed was the grounding assumption -- and nothing on the screen
said so.

`pkg_rlc_attrib.py` answers two questions about ONE frequency of
ONE spec:

  Q2  ATTRIBUTION. Split the Z_ab you just read into "the bare EM
      coupling" plus one signed term per termination you declared.
      The terms add up to the total EXACTLY -- this is
      superposition, not a linearisation, not a sensitivity
      estimate, not a percentage anyone apportioned by hand.
  Q1  SENSITIVITY. What would Z_ab be if that ground ball were
      open instead? A 50 ohm resistor? A 1 nH lead? Also exact:
      it re-solves the network, it does not extrapolate.

The algebra, if you want it, is one Woodbury update: terminating a
set of ports is a low-rank change to the file's own admittance, so
the whole answer is a small dense solve on an m x m matrix, where
m is the number of terminations you declared. See docs/theory.md
section 13.

The baseline
------------
The split is always relative to a stated BASELINE, and the
baseline is: probe sides merged, EVERY OTHER PORT OPEN. Nothing
else -- no ground, no short, no lumped element. Each declared
termination is then one element on top of it:

      ground / vdd     one terminal, impedance 0
      lumped_to_gnd    one terminal, impedance from R/L/C
      short_to         two terminals, impedance 0
      lumped_between   two terminals, impedance from R/L/C

Change the baseline and every term changes, even though the total
and the physics do not. That is the same caveat PEEC states about
partial inductances: individually reference-dependent, only
collectively physical. The report names the baseline it used, so
compare two reports only when their baselines match.

Sign convention -- printed on every report and every export
-----------------------------------------------------------
  * The victim reading is V(+) - V(-) of the victim measurement
    port.
  * The aggressor is driven with +1 A into its (+) side and out of
    its (-) side. So every term is signed the way Z_ab = V_a / I_b
    is.
  * An element current I_e > 0 flows OUT of the structure into
    ground for a SHUNT element (ground / vdd / lumped_to_gnd), and
    from the first port to the second for a SERIES element
    (short_to / lumped_between).
  * Flip either measurement port's +/- assignment and every term
    flips together. RELATIVE signs between terms are physical;
    absolute signs are a labelling choice.

Three caveats, and none of them is a footnote
---------------------------------------------
1. IT IS BLIND TO OPEN PORTS. An open port contributes no element
   and therefore no term -- it is absent from the table, not small
   in it. So the contribution table is NOT a ranking of ports; it
   is a ranking of the DECLARATIONS in your spec. The 45 open
   ports of a package file are simply not in it. Ports you have
   not decided about yet are covered only by the sensitivity side,
   and it covers them by hypothesising a termination, not by
   measuring one.

2. THE SPLIT DEPENDS ON HOW THE SPEC IS SPELLED. These two are the
   same network:

        3 ground                3 short_to 4
        4 ground                3 ground

   Same total M, to the last digit that means anything. Different
   split. Measured on tests/fixtures/diff_pair_4port.s4p at 5 GHz
   with "agg = 1", "vic = 2":

        3 ground / 4 ground         3 short_to 4 / 3 ground
          bare EM      251 pH         bare EM      251 pH
          ground 3     252 pH         ground 3     253 pH
          ground 4     506 pH         short 3-4    506 pH
          total       1.01 nH         total       1.01 nH

   Both are right. Two ways of describing one network are two
   different tearings of it, and they answer different questions.
   Reorganise your connection table for readability and the
   contribution column can move -- that is not a defect.

3. MOST OF THE RETURN CURRENT CAN BE INSIDE THE EM MODEL. The
   reference plane in your EM solve is not a port, so no
   declaration of yours reaches it. Every report therefore prints
   a return-path budget: how much of the aggressor's return
   current went through your declared elements versus through the
   model's own reference. On a representative package case that
   split was 0.05% declared / 99.95% inside the model, and when
   the model dominates the report says in plain words that the
   decomposition CANNOT separate the return path. Do not read a
   "forward path minus return path" conclusion out of small
   numbers in the table.

   A fourth boundary, while you are here: re-terminating existing
   ports cannot evaluate NEW METAL. A shield, an extra via, a
   widened return path -- none of those is a termination of a port
   that already exists. They change the EM answer itself and need
   a new solve. Sensitivity output looks like a layout-exploration
   tool and is not one.

What can and cannot be split per term
-------------------------------------
A quantity splits if it is a fixed real scalar times something
R-LINEAR in Z_ab, read at one configuration:

      YES:  Z_ab, Re Z_ab, Im Z_ab, M, M/L_a, k
      NO:   C_c, Q, |Z|, anything in dB

C_c = -1/(omega*Im Z_ab) is a RECIPROCAL: superposition adds
impedances, not their inverses, so the per-element terms of C_c do
not sum to C_c. C_c is still reported as a TOTAL -- it is the
right reading whenever Im(Z_ab) < 0 -- just never per term. Q is a
ratio of two decomposable things; |Z| is a norm; dB is a logarithm
of a magnitude and has no sign. Ask for one of those per term and
the tool refuses BY NAME and tells you which linear quantity to
decompose instead.

Where to run it
---------------
There is no window for this yet. It lives on the --cli path:
add "--attribute VICTIM,AGGRESSOR" to any "--mode coupling" run,
where both sides are measurement-port NAMES as you gave them to
--mport. Everything below came out of that.

      python pkg_rlc_extractor.py --cli <file> --mode coupling \\
          --mport "agg = 1" --mport "vic = 2" --gnd "3,4" \\
          --freq 5.0 --attribute vic,agg

Five more flags, all inert without --attribute:

  --attribute-alt SPEC
      A candidate termination for the sensitivity scan;
      repeatable. "open", "ideal", or a series R/L/C in the same
      spelling the connections table uses -- R=50, L=0.3n,
      "R=0.5,L=1n", C=100p. With none given the scan is limited
      to "open" and "ideal", the two STRUCTURAL candidates that
      need no judgement about your package. The tool will not
      guess your ball's lead inductance for you.
  --attribute-ground-model MODEL
      "diag" (default, exactly as declared), "diag:SPEC" (each
      shunt lead gets SPEC on its own INDEPENDENT lead) or
      "shared:SPEC" (they all ALSO share SPEC back to the
      reference). See the last section on this tab -- the choice
      is worth 6 to 10 dB.
  --attribute-freqs 1,5,10
      Re-rank the contributions at each of these too, so a
      ranking read off one frequency can be checked against the
      band.
  --attribute-group row | flat | name
      How elements are grouped for the joint-effect section.
      "row" (default) groups by the flag that declared the port.
      "name" groups by port names with the trailing index
      stripped -- a NAMING HEURISTIC, and the report says so.
  --attribute-csv PATH
      Every record, one row each, tagged by a "section" column.
      The terminal caps some tables for readability; the CSV
      does not.

Worked example, from a fixture in this repo
-------------------------------------------
tests/fixtures/diff_pair_4port.s4p is two coupled lines: port 1
(in_p) runs to port 3 (out_p), port 2 (in_n) runs to port 4
(out_n). Drive line one, listen on line two, with both far ends
grounded:

      agg = 1     vic = 2     GND = 3,4     f = 5 GHz

That is exactly the command above, and section 1 of its report
reads:

   group        element            |I_e|   M term   share   quad
   -----------  ----------------  ------  -------  ------  -----
   --gnd 3,4    ground port 3        1 A   252 pH  25.00%  -0.00%
   --gnd 3,4    ground port 4     997 uA   506 pH  50.12%   0.00%
   (baseline)   bare EM coupling      --   251 pH  24.88%  -0.00%

with the reconciliation in section 2:

   Z_ab total (compute_z_matrix) : -2.49471e-09 + j31.7316 ohm
   Z_ab total (sum of the terms) : -2.49469e-09 + j31.7316 ohm
   M    total                    : 1.01 nH
   residual 6.42e-13 relative, floor 3.62e-09

How to read that:

  * THREE QUARTERS OF THE ANSWER IS THE GROUNDING, not the metal.
    Open both grounds and M falls from 1.01 nH to the 251 pH bare
    term. If you were arguing about a 6 dB discrepancy, this is
    where it lives.
  * The two ground balls are NOT worth the same. Port 4 is the far
    end of the VICTIM's own line and contributes 506 pH; port 3 is
    the far end of the aggressor's and contributes 252 pH. That
    asymmetry is physical and is exactly what the table is for.
  * "share" is a signed PROJECTION onto the total,
    Re(term * conj(total)) / |total|^2 -- not a complex ratio and
    not a magnitude ratio. "quad" is the part at 90 degrees to the
    total, which inflates any magnitude-based cancellation measure
    while being harmless. Here quad is 0.00% because everything is
    in phase. The share column is suppressed outright, with a
    named reason, when the total is near zero: shares of a number
    that is pure cancellation mean nothing.
  * The residual is the cross-check. The AUTHORITATIVE total is
    always compute_z_matrix's; the sum of the terms is the check
    on it. 6.42e-13 against a floor of 3.62e-09 means the two
    algorithms agree far better than they had to. The floor is
    condition-aware, not fixed: a well-conditioned 4-port reaches
    1e-13 and a 153-port package cannot beat ~1e-7, so a fixed
    gate would refuse exactly the files this exists for. If the
    residual is ever catastrophic the per-element split is
    withheld -- the totals never are.

Now ask the other question. What if those grounds were not ideal?

  * ONE AT A TIME. Opening ground 3 alone: -506 pH. Opening
    ground 4 alone: -506 pH.
  * BOTH AT ONCE: -759 pH, not -1.01 nH. The non-additivity is
    +254 pH -- a THIRD of the effect, out of only two elements.
    With sixty ground balls, every single-ball delta is nearly
    zero because the other fifty-nine already carry the return,
    and so is every pairwise second difference: the effect is
    order-sixty. That is why there is a group-level and a
    cumulative answer and not just a one-at-a-time list.
  * THE WHOLE RANGE, in closed form. Z_ab as a function of one
    element's impedance z is a Mobius map (alpha + beta*z) /
    (gamma + delta*z), so the endpoints, the interval and the
    extremum are analytic -- no loop, no sampling. Sweeping
    ground 3's series inductance on this fixture:
        ideal (L = 0)   1.01 nH
        open  (L = inf)  504 pH
        actual range over all L >= 0:  [504 pH, 1.18 nH]
    Note the range is WIDER than the two endpoints. A series L
    resonates with the structure's shunt C, so the [ideal, open]
    pair is not a bound; here the peak is 1.18 nH at L = 505 nH.
    The tool detects that and says so instead of quoting a
    bracket that does not hold.

One more thing this changes about your GND field
------------------------------------------------
A ground field written as N independent lumped_to_gnd inductors
says the balls have N independent return paths. Real package
ground balls share a return plane. N independent z in parallel is
z/N; N balls sharing one z is z -- so the independent spelling
understates the common-mode return inductance by roughly
(1 + (N-1)*k_ret), and that is worth more decibels than most
things being argued about. Measured three times on three
different networks: 9.60 dB, 8.09 dB, and 6.03 dB on
diff_pair_4port.s4p above. Monotone in the coupling, no threshold,
so there is no safe default and the tool refuses to pick one.

On the CLI that is one flag: --attribute-ground-model diag:L=1n
versus shared:L=1n, and the report prints both against the spec
as declared.

You can also spell it right here in Mode 5, with no attribution
code at all: tie the whole ground set with one short_to row, then
hang ONE lumped_to_gnd on any port of it.

      independent:  3 lumped_to_gnd L=1n
                    4 lumped_to_gnd L=1n        M = 1.0120 nH

      shared:       3 short_to 4
                    3 lumped_to_gnd L=1n        M = 2.0259 nH

Same file, same probes, same frequency: 6.03 dB apart. (It does
not matter WHICH port of the set carries the inductor -- by then
they are one node, and the two spellings give bit-identical
answers.) Which one is right is a question about your package,
not about this tool -- but you should answer it on purpose.
"""


HELP_SYNTAX = """\
Input syntax reference
======================

Port range syntax
-----------------
Used for Signal/Port A, Port B, GND Ports, each side of a
measurement-port row, and the Port / To cells of the Mode 5
connections table. All port numbers are 1-based.

   Format            Example         Meaning
   ----------------  --------------  -----------------------------
   single port       "1"             [1]
   comma list        "1,3,5"         [1, 3, 5]
   MATLAB range      "35:1:45"       [35, 36, ..., 45]
   MATLAB step 2     "1:2:9"         [1, 3, 5, 7, 9]
   reverse step      "5:-1:1"        [5, 4, 3, 2, 1]
   dash range        "6-14"          [6, 7, ..., 14]
   reverse dash      "5-1"           [5, 4, 3, 2, 1]
   mixed             "1,3,35:1:45"   [1, 3, 35, 36, ..., 45]
   empty             ""              []  (no ports)

Whitespace is tolerated. Duplicate ports are deduplicated while
preserving order.

NOTE the MATLAB range needs all THREE fields. "35:45" is an error;
write "35:1:45" or "35-45".

Short-pair / short-group syntax
-------------------------------
Used for the Short Pairs field in Mode 3.

   Format            Example          Meaning
   ----------------  ---------------  ----------------------------------
   single pair       "45-46"          ports 45 and 46 tied together
   multiple pairs    "45-46, 47-48"   two independent pairs
   chained group     "1-2-3-4"        all four ports tied as ONE node
   chain + pair      "1-2-3, 5-6"     three-port group + a pair
   trailing comma    "45-46,"         tolerated

Each group MUST use dash syntax to join ports. "45,46" is two
separate ports in a comma-list, NOT a short connection.

Measurement-port syntax (Mode 6)
--------------------------------
One measurement port per entry -- a red probe on the "+" ports and a
black probe on the "-" ports:

      [<name> =] <+ ports> [/ <- ports>]

   Example               Meaning
   --------------------  ---------------------------------------------
   "tank = 1,3 / 2,4"    named "tank"; red probe on 1 and 3,
                         black probe on 2 and 4
   "1 / 2"               unnamed; red on 1, black on 2
   "rx = 5:1:9 /"        named "rx"; red on 5..9, "-" side empty
                         => referenced to the Touchstone ground
   "3,4"                 red on 3 and 4, ground-referenced
                         (no "/" needed)

Rules:
   * "/" separates the two sides. At most one "/" per entry.
   * "=" introduces the optional name. Unnamed entries are
     auto-named P1, P2, ... in order.
   * BOTH sides accept the full port-range syntax above:
     "1,3"  "6-14"  "35:1:45"  "5:-1:1"  all work on either side.
   * The "+" side may not be empty. An EMPTY "-" side is legal and
     means "referenced to ground".
   * A given port may appear on only one side of only one
     measurement port.
   * The names "A" and "B" are RESERVED for the legacy Mode 1/2/3
     signal groups (and for 'signal A' / 'signal B' in Mode 5).
     The check is case-insensitive -- "a" and "b" are rejected too.

Signal-group syntax (Mode 5 DSL)
--------------------------------
The same idea, written one port at a time:

      <port>  signal <name> [+|-]

The sign is a separate whitespace-delimited token and defaults to
'+'. "signal A" / "signal B" are the legacy spellings, where
"signal B" == "signal A -". See the Mode 5 tab.

Frequency input
---------------
RLC Freq (GHz) and Fit f_min/f_max (GHz):
   Plain numbers in GHz: 0.1, 1.0, 5.0, 1e-3 (= 1 MHz)
   The RLC Freq field also accepts SI suffixes (50p means 50e-12 GHz
   if you really want, but that's nonsense -- use plain numbers).

Custom-mode SI suffixes for R/L/C
---------------------------------
   f=1e-15   (femto)        m=1e-3 (milli)
   p=1e-12   (pico)         k=1e3  (kilo)
   n=1e-9    (nano)         M=1e6  (mega)
   u=1e-6    (micro)        G=1e9  (giga)
                            T=1e12 (tera)

   Examples: R=50, R=1k, L=1.5n, C=10p, R=2.5, L=0.5e-9

   ONE WORD, NO UNIT. "5 m", "1 uF" and "0.5 nH" are rejected: the
   text form splits on whitespace, so "R=5 m" would be read as
   "R=5" with the "m" thrown away -- 5 ohm where you meant 5
   milliohm. The unit lives in the column header.

Where the file's port names are
-------------------------------
Every port cell and dropdown in this tool takes port NUMBERS. To
see the names the file carries ("! Port[12] = VDD_ball_2"), select
the file and click "Show Ports". That opens the "Ports & Roles"
window:

   #    Name           Role       From
   1    VSS_ball_1     ground     conn row 1
   ...
   8    VSS_ball_8     open       --            <- flagged
   9    sig_in         probe +    probe row 1 (+)

   * One line per port of the file, with the role your spec gives
     it (probe + / probe - / ground / vdd / element / shorted /
     open) and the row or kept-as-text line that decided it.
   * Filter by name, hide the open ports, and click any heading to
     sort (on the value, so port 10 sorts after port 9).
   * Rows are flagged in orange when they need a second look: an
     OPEN port whose name matches a set you grounded or probed, a
     port claimed by both a probe row and a ground row (the ground
     row wins), and a port assigned by the "kept as text" block
     rather than by a table row.
   * Select rows and press "Set as ground" or "Set as probe +" and
     they are written into the editor as a COLLAPSED RANGE, so a
     54-ball ground group is one row ("6-14,20-59") instead of 54.

The window stays open while you edit and follows what you type. The
same open-port check also appears on the validation strip under the
tables, so you see it without opening anything.

What a connection row costs you, in decibels
--------------------------------------------
Every row above is an assumption, and the assumptions are not
small. On tests/fixtures/diff_pair_4port.s4p at 5 GHz, three
quarters of the extracted M comes from the two "ground" rows and
only a quarter from the metal; and rewriting the same ground set
as one SHARED return

      3 short_to 4                (instead of two separate
      3 lumped_to_gnd L=1n         lumped_to_gnd L=1n rows)

moves M by 6.03 dB, because N independent z in parallel is z/N
while N balls sharing one z is z.

"Where the number came from" on the Mode 6 tab is the section that
splits an extracted M into one signed term per row you wrote here,
and tells you what each row would be worth if it were open, a
resistor or a lead inductance instead. It also states, in those
words, what the split is blind to: a port you left OPEN
contributes no row, so it contributes no term.
"""


HELP_WORKFLOWS = """\
Worked examples
===============

Example A: DCO spiral inductor characterization
-----------------------------------------------
File:    1- or 2-port S-parameters of a spiral inductor from EMX
Goal:    Extract L and Q over the operating band

If 1-port:
   Mode = 1 (Port -> GND)
   Signal/Port A = 1
   GND Ports = (blank)
   RLC Freq = your operating frequency, e.g. 5.0 GHz
   Fit Model = inductor
   Fit f_min/f_max = e.g. 1.0 / 10.0 GHz

If 2-port (P, N):
   Mode = 2 (A <-> B)
   Port A = 1, Port B = 2
   Fit Model = inductor

Reads:   L (nH), Q at center, R_dc, R_ac (skin)


Example B: Differential trace loop inductance (very common)
-----------------------------------------------------------
File:    5-port diff pair (in_p=1, in_n=2, out_p=3, out_n=4, gnd=5)
Goal:    Effective loop L when far end is shorted (e.g. terminated
         on-die)

   Mode = 3 (A <-> B + Short Pairs)
   Port A      = 1
   Port B      = 2
   Short Pairs = 3-4
   GND Ports   = 5
   RLC Freq    = your operating frequency
   Fit Model   = inductor

Reads:   L_loop = 2 * (L_self - M) for ideal coupled inductors


Example C: Differential trace inter-pair capacitance
----------------------------------------------------
Same file as Example B.
Goal:    Total C between in_p and in_n with far end open

   Mode = 2 (A <-> B)
   Port A     = 1
   Port B     = 2
   GND Ports  = 5
   (out_p=3 and out_n=4 left unlisted -- automatically open)
   Fit Model  = capacitor
   Fit f_min/f_max = pick a band well below SRF (e.g. 0.01 / 1 GHz)

Reads:   C_diff (pF), ESR


Example D: Decap mount with two pads shorted
--------------------------------------------
File:    4-port (top_signal, top_return, bot_signal, bot_return)
Goal:    Impedance seen from the top mounting plane after the cap
         is installed (modeled as a short between bot_signal and
         bot_return)

   Mode = 3
   Port A      = 1   (top_signal)
   Port B      = 2   (top_return)
   Short Pairs = 3-4
   Fit Model   = capacitor (or auto)


Example E: Custom termination -- trace with 50-ohm load
-------------------------------------------------------
File:    2-port trace
Goal:    Driving-point impedance with realistic 50-ohm far-end load

   Mode = 5 (Custom)
   Measurement ports table:
        Name    + ports    - ports
        m1      1
   Connections table:
        Type       Port   To    R    L    C
        rlc_gnd    2            50
   RLC Freq    = your operating frequency

   ("Edit as text..." shows the same thing as
        1 signal m1 +
        2 lumped_to_gnd R=50
    which is literally what gets parsed.)


Example F: Mutual inductance between two bond-wire loops
---------------------------------------------------------
File:    6-port PKG file (wire A: die=1, ball=2;
                          wire B: die=3, ball=4;  gnd = 5,6)
Goal:    M and k between the two loops at 1 GHz

   Mode        = 6 (+/- Ports / Coupling)
   Measurement ports table:
        Name    + ports    - ports
        wA      1          2
        wB      3          4
   GND Ports   = 5,6
   RLC Freq    = 1.0 GHz

Reads:   L_wA = 1.8 nH, L_wB = 1.9 nH
         M    = 0.42 nH,  k = 0.227
         M/L_wA = 23.3%  (-12.6 dB)  -- strong; these loops share a
         lot of flux, so they need spacing or a ground wire between
         them.

See the "Mode 6 (Coupling)" tab for what each number means and for
the layout-iteration loop.


Example F2: how much of that M is the GND field?
-------------------------------------------------
Same file as Example F. You changed "GND Ports = 5,6" to
"GND Ports = 5" and M moved by several dB, and now you need to
know which reading to defend.

That is what "Where the number came from" at the bottom of the
Mode 6 tab is for. It splits the extracted M into

      bare EM coupling  +  one signed term per GND / short /
                           lumped row you declared

with the terms summing to the total EXACTLY (superposition, not an
estimate), and it answers the other direction too -- what M would
be with any of those rows open, resistive, or a lead inductance,
and the whole range in closed form.

Measured on tests/fixtures/diff_pair_4port.s4p with "agg = 1",
"vic = 2", GND = 3,4 at 5 GHz: of M = 1.01 nH, the bare EM
coupling is 251 pH and the two ground rows are 252 pH and 506 pH.
Open both grounds and M is the 251 pH. Ground 4 is worth twice
ground 3 because it is the far end of the VICTIM's own line.

Read the three caveats on that tab before quoting any of it -- in
particular, the table ranks the rows you DECLARED, so a port you
left open is absent from it rather than small in it.


Example G: PDN impedance with mixed VDD/GND balls
--------------------------------------------------
File:    package model with many VDD and GND balls
Goal:    AC impedance from a die signal pin to its die return

   Mode        = 2 (A <-> B)
   Port A      = die signal pin
   Port B      = die return pin
   GND Ports   = all GND balls AND all VDD balls

An ideal supply is an AC short, so VDD balls belong in the GND Ports
field. (This replaces the old Mode 4, which did exactly this.)
"""


HELP_FILES = """\
Reading Touchstone files
========================

What "Add File..." prints
-------------------------
Every load writes a block like this to the Results pane:

    top_pkg.s45p
      45 ports, 2001 points, Z0 = 50Ohm, read as '# GHZ S RI R 50'
      Frequency: 0 Hz - 40 GHz  (linear, step 20 MHz)
      max |S| = 0.998

Check the frequency span against what you actually simulated -- it is
the fastest way to notice you loaded last week's file. The span also
appears on the file's line in the Files list.

  read as    the option line the tool USED. If the file had none, or
             had tokens the tool did not recognise, this says what was
             assumed and a WARN line says why.
  max |S|    a passive structure cannot exceed 1. A value well above 1
             usually means the option line's format (RI / MA / DB) does
             not match how the numbers were really written -- a file
             that parses perfectly and is completely wrong.

Two kinds of extra line can follow:
  WARN:      something was guessed or thrown away. Read it.
  Note:      the file is fine, but there is something you should know
             before reading the numbers -- for example a sweep that
             starts at DC, where L = Im(Z)/omega and C = -1/(omega*Im(Z))
             are undefined and will read as nan/inf. Pick any other
             frequency for the R/L/C extraction.

When a file will not load
-------------------------
The error dialog always names a VERDICT, because "is my file bad or is
your tool bad?" is the first thing you need to know:

  THE FILE is inconsistent      truncated, corrupt, or not Touchstone.
                                Everything before the named line was
                                read correctly.
  THE FILE looks valid, but...  a real format this tool does not read:
                                Touchstone 2.0 ([Version] / [Network
                                Data] keyword lines), or a compressed
                                file. Re-export as .sNp / decompress.
  THE FILE could not be read    missing, locked, or too big for memory.
  THE PARSER gave up            the file's structure checks out, so
                                this is a bug in this tool. The report
                                carries a traceback -- please send it.

Where a line number can be given, it is, together with the text of that
line. A file truncated mid-record says which line the incomplete record
starts on, not just "token count not divisible".

If the only problem is a stray non-numeric token, the dialog offers to
load the file anyway, skipping it. Treat the result as suspect: a
Touchstone file is a positional stream of numbers, so dropping one
shifts every number after it by one slot. That is exactly why skipping
is not the default.

"Check File"
------------
Runs the same structure report on demand and prints it to the Results
pane: size, encoding, line counts, the option line, how many numbers
each data line carries, and whether the data divides into whole records
for every plausible port count. It ends in the same VERDICT.

Use it when the file LOADS but the numbers look wrong -- that is the
case an error dialog can never cover. It answers: was the port count
guessed? is the sweep what I simulated? does the record grid line up?

With a file selected in the Files list it checks that file; with nothing
selected it asks for one, so it also reaches files that fail to load.
On the command line the same report is
    python pkg_rlc_extractor.py --diagnose myfile.s4p
which exits 0 when nothing is wrong and 1 otherwise.

Port count detection
--------------------
The port count comes from the CONTENT, not the extension -- EDA tools
rename these files constantly, so a .txt or .dat holding a 4-port sweep
loads fine. The name is used for two things only:
  * to break a tie when the numbers admit several port counts (picking
    the smallest silently reads a 2-port file as a 1-port one);
  * when nothing up to 256 ports fits, which is the one case content
    alone cannot resolve -- a .s300p package export.
Both say so in a WARN line. --force-nports overrides everything.

Formats read
------------
Touchstone 1.x, any extension. RI / MA / DB, any frequency unit, with or
without an option line. UTF-8, UTF-8 with BOM, and UTF-16 (with or
without BOM) are all read; commas and semicolons between values, and
Fortran 'D' exponents (1.0D+09), are accepted with a WARN. Touchstone
2.0 and compressed files are refused by name rather than misread.
"""


HELP_SESSION = """\
Saving and reloading your setup
===============================

File -> Save Config...       (Ctrl+S)
File -> Load Config...       (Ctrl+O)
File -> Restore Last Session

What a config file holds
------------------------
Everything you TYPED, and nothing that was computed:

  * the loaded files, by path (see "Moving a config" below);
  * every trace -- mode, port fields, both Mode 5/6 tables, anything
    kept verbatim as text, colour, line style, and whether it is shown;
  * RLC Freq, the band-fit range and model, and the Units setting;
  * the plot's checkbox row: X/Y log, Marker, Readout, which quantities
    are on screen, and where the marker sits.

It does NOT hold R, L, C, Q, Z or the coupling matrix. A config is a few
kB of readable JSON -- it goes in git, it can be mailed to a colleague,
and it can ride to an offline machine next to the data it describes.
After loading, press "Calculate All & Plot" and the numbers come back.
Export CSV remains the way to save the RESULTS.

Moving a config
---------------
Each file is recorded twice: relative to the config file, and as an
absolute path. Loading tries the relative one first, so copying the
whole folder (config + .sNp files) to another machine just works; the
absolute path is the fallback for a config file moved on its own.

A file that cannot be found at either place is reported by name in the
Results pane and the load continues. The traces that referenced it stay
in the list and stay editable -- add the file and load the config again,
or point the editor's File box at one that is loaded.

Loading REPLACES the current session (it is not a merge), and asks
first if anything is open.

Restore Last Session
--------------------
The config is written automatically when you close the window, to

    <your home>/.pkg_rlc_extractor/last_session.json

and the Results pane says on startup what is in it. It is NOT loaded
automatically: re-parsing a package export takes tens of seconds, and
that is not a good thing to do before you have asked for anything. An
empty session is never written, so opening the tool and closing it again
does not erase what the previous run left.

Editing a config by hand
------------------------
It is plain JSON and it is meant to be readable. A value that will not
parse costs that one field -- the field keeps its default and the
Results pane says which -- rather than the whole file. A file that is
not a session file, or is from a newer build, is refused by name.
"""


HELP_TOPICS = [
    ("Overview",        HELP_OVERVIEW),
    ("Reading files",   HELP_FILES),
    ("Save / Load",     HELP_SESSION),
    ("Mode 1 (->GND)",  HELP_MODE1),
    ("Mode 2 (A<->B)",  HELP_MODE2),
    ("Mode 3 (+Short)", HELP_MODE3),
    ("Mode 5 (Custom)", HELP_MODE5),
    ("Mode 6 (Coupling)", HELP_MODE6),
    ("Input syntax",    HELP_SYNTAX),
    ("Worked examples", HELP_WORKFLOWS),
]


# 1010, not the historical 950.  A ttk.Notebook does NOT wrap or scroll its tab
# strip -- it CLIPS it, so a tab that does not fit is simply unreachable, and
# the one that goes is the LAST ("Worked examples") with nothing on screen to
# say so.  Measured (Microsoft YaHei UI 9): nine tabs need 891 px and ten need
# 968, so the tenth did not fit the old width at all.  Headroom now 42 px --
# NOT enough for an eleventh.  tests/test_session.py::TestHelpTabsAllFit
# re-measures it; add a tab and it tells you whether the window has to grow.
HELP_WINDOW_WIDTH = 1010


class HelpWindow(tk.Toplevel):
    """A tabbed reference window. One tab per topic."""

    def __init__(self, master):
        super().__init__(master)
        self.title("PKG RLC Extractor -- Help")
        self.geometry(f"{HELP_WINDOW_WIDTH}x650")

        nb = ttk.Notebook(self)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        for title, body in HELP_TOPICS:
            frame = ttk.Frame(nb)
            nb.add(frame, text=title)
            txt = ScrolledText(frame, wrap=tk.WORD, font=("Consolas", 10))
            txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            txt.insert("1.0", body)
            txt.configure(state="disabled")
