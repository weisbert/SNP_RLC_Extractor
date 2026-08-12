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
  6. Answers WHERE a coupling number came from, which is a separate
     question from what it is. Analyze -> Attribution... splits an
     extracted Z_ab into the bare EM coupling plus one signed term
     per termination you declared, and says what it would be with
     any of them changed. On the CLI, --cold-start goes the other
     way and ranks the ports you have NOT declared anything for.
     Both are on the "Mode 6 (Coupling)" tab, under "Where the
     number came from".

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

View: what the report LOOKS like (the dropdown beside Units)
-------------------------------------------------------------
One run, three renderings. Nothing is recomputed and no numbers
change; every page is repainted in place and no new run is created.

   detail   -- everything, one block per trace. What the tool has
               always printed, and the default.
   summary  -- the whole run as two tables: one row per measurement
               port, then one row per coupling pair. Comparing two
               traces becomes reading down a column instead of paging
               between blocks.
   compare  -- the traces become COLUMNS, one quantity per row, with
               a change column when there are exactly two of them:

                  compare @ 5.55 GHz    [1] before   [4] after       Δ
                  VCO      R                9.92 Ω     9.81 Ω  -1.13 %
                           L               3.23 nH    3.23 nH  +0.09 %
                  VCO x RX M               -516 fH   -7.19 pH  +13.9 ×
                           worst M/L     -68.77 dB  -52.36 dB  +16.4 dB

               A big change is shown as a FACTOR rather than a
               percentage (-1293% is not a sentence anybody says out
               loud), and a dB quantity gets a dB DIFFERENCE, because
               dB is already a ratio. A trace that does not have a
               port or pair at all leaves an EMPTY cell -- "this trace
               has no RX" and "RX measured zero" are different
               statements.

               With one trace on the plot there is nothing to compare,
               so it says so and shows the summary instead. With three
               or more there is no Δ column: a change against
               "whichever trace happened to be first" is a reference
               chosen in silence.

The choice is saved with the session, like the units mode.

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

CONNECTIONS -- the cells a row has FOLLOW ITS TYPE:

   Type          Attaches                           cells on that row
   ------------  ---------------------------------  ------------------
   ground        V = 0                              Port
   vdd           V = 0 as well (AC small-signal)    Port
   open          nothing (the default anyway)       Port
   short         ties the whole listed group        Port, Net
                 into ONE node
   rlc_gnd       series R-L-C from Port to ground   Port, R, L, C
   rlc_between   the same element between two       Port, To, R, L, C
                 ports

   A short row has ONE port field, not two: list the whole tied
   group in it ("5,6,7,8", "23-25"). A group of shorted pins has no
   natural "from" and "to", and splitting it across two cells was
   arbitrary. The freed cell is the node's NAME -- see "Naming a
   node" below.

   The column headings follow the table: with no rlc_gnd/rlc_between
   row in it the R/L/C headings are blank and the port field is that
   much wider, and the third heading reads "To", "Net", "To / Net"
   or nothing depending on what the rows actually put there.

   * Every port field takes the full range syntax, so a package's
     ground balls are ONE row: "6-14" or "35:1:45". See the Input
     syntax tab. ("35:45" is an error -- the MATLAB form needs all
     three fields.)
   * The Port / To dropdowns list port NUMBERS, not names. To see
     which ball is which on an unfamiliar file, click "Show Ports"
     at the top of the left panel: it opens the "Ports & Roles"
     window, which lists every port with its name, the role your
     spec gives it, and which row said so -- and can write a
     selection back into these tables as a collapsed range. (A
     name-bearing dropdown does not fit the editor's width; it is
     planned, not forgotten.)
   * rlc_between is the only Type with two port fields, because a
     two-terminal element really has two ends. It takes exactly ONE
     partner port (an N-to-M lumped element is ambiguous -- star?
     mesh?). ground / vdd / open / rlc_gnd have no second field at
     all: they are always to ground.
   * A range on an rlc_gnd or rlc_between row is ONE ELEMENT PER
     PORT, not one element shared by them. "21:1:25" with L = 80p
     is five separate 80 pH inductors, one from each of ports
     21..25 to ground -- the right model for five ground balls each
     with its own ball inductance. (If those five ports are one net
     inside the file, the five inductors end up in parallel there,
     so the die sees ~16 pH.)
     For ONE shared 80 pH instead, short the ports and hang the
     element off the NODE:

         short     21:1:25   as gnd_ring
         rlc_gnd   gnd_ring  L=80p

     Any ONE member port does the same job ("rlc_gnd 21 L=80p"),
     because the short has already made them one node; the name
     just says so out loud.

     What you must NOT write is "rlc_gnd 21:1:25 L=80p" after the
     short. That is five 80 pH inductors between the SAME two
     nodes, i.e. 16 pH, and it is a plausible-looking number that
     nothing else on screen would question. The strip refuses it by
     name: "ports 21-25 are ALREADY ONE NODE ... L 80 pH becomes
     16 pH". Without the short those same five ports are five real
     ball inductances and nothing is said.
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

Naming a node
-------------
A short row creates a NODE, and the cell it does not need for a
second port field is that node's NAME:

   Type    Port          Net
   ------  ------------  ----------
   short   23,24,25      coil_tap

Any port field in either table may then say "coil_tap" instead of
a port number -- an element row, a probe's "+" or "-" side, a
later ground row:

   Type          Port      To       L
   ------------  --------  -------  -----
   rlc_between   coil_tap  10       10f

which reads the way you would say it out loud: "these three are one
point, and an inductor goes from that point to port 10".

The name is pure convenience. It resolves to one member port of the
node, so the answer is bit-identical to typing "23" there, and
"Edit as text..." shows exactly that:

   23,24,25  short           as coil_tap
   coil_tap  lumped_between  10  L=10f

What a name may be:
   * NOT a number or a range. "1", "6-14" and "35:1:45" are refused
     -- the port field is the one place a number and a name share a
     slot, and nothing could tell them apart.
   * No spaces, and none of  :  ,  -  #  . Those are how this
     syntax separates ports, ranges and comments.
   * Not one of the syntax's own words (ground, vdd, open, signal,
     short, as, ...), and not "A" or "B", which are reserved for
     the legacy signal groups.
   * Matched WITHOUT regard to case ("Coil_tap" finds "coil_tap"),
     stored exactly as you typed it.

Two refusals, both deliberate:
   * A name nothing defines is an ERROR naming the ones that are
     defined -- it is never quietly treated as a new, empty node,
     which would hang your element off a dangling point and change
     the answer with nothing on screen.
   * Two names for ONE node is an error. If a short ties 1,2 and
     another ties 2,3, ports 1-3 are one node and it cannot answer
     to two names; put them all on one short row.

Every merged node appears at the TOP of the Port / To dropdowns --
its name, or its first member port when it has no name -- above the
bare port numbers. That is on purpose: picking the node from the
top of the list is the right gesture, and typing out all of its
members is the one that multiplies your element by N.

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
  <ports> short                         (ties the whole list into
                                         ONE node -- what a single-
                                         cell short row serialises to)
  <port>  short_to <other_port>         (the same thing in two fields)
  <ports> short [as <name>]             (...and name the node)
  <port>  lumped_to_gnd <R/L/C params>
  <port>  lumped_between <other_port> <R/L/C params>

Any <port> above may be a NODE NAME given by an "as" on a short
line -- see "Naming a node" above. Only a short line may carry an
"as"; putting one anywhere else is an error rather than a silently
dropped word.

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

Hanging this on a package (a SECOND file)
-----------------------------------------
Add the package to the trace with Analyze -> "Files in this
trace...", then write its ports with a tag while the die's stay
bare:

    2,F2.1                     short row: bond wire, die 2 to pkg 1
    25,26,F2.15                short row: die 25 AND 26 onto pkg 15
    F2.2  rlc_gnd  R=0.5       the package's ground return
    2 / F2.1 + R/L/C           rlc_between row: a modelled wire

A bare number always means the HOME file and a tag scopes only the
token it is on, so a short group mixes files in any order and
nothing above this line changes. The full rules -- what a tag is,
why it is short, and what renumbers it -- are on the "Input syntax"
tab.
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

  On screen the block prints the VERDICT and the number --
  "✓ reciprocal (2.1e-10)", or the alarm with its sentence -- and the
  definition above is what the legend under the report points here
  for. The definition is the same every run; the number is not.

What the block prints, and what it stopped printing
----------------------------------------------------
  * The legend (ind / cap / R<0 / M/L) is printed ONCE per run,
    under the last block, not under every block.
  * With THREE or more measurement ports the Z matrix is drawn as a
    matrix, which is the compact way to show G(G-1)/2 mutual terms.
    With two, it is not: [[Z_aa, Z_ab], [Z_ab, Z_bb]] has every entry
    printed again in the two tables underneath, so the diagonal is a
    "Z (Ω)" column of the self table and the mutual term rides on the
    pair's own line as "Z_ab = ...". No number was dropped -- check
    it against R = Re(Z), L = Im(Z)/ω and M = Im(Z_ab)/ω.
  * "worst M/L" stays on the pair's FIRST line. It is the rank key,
    and with six measurement ports there are 15 pairs to scan.

  See also the "View" note on the Overview tab: `summary` puts every
  pair of every trace in one table, and `compare` puts two traces
  side by side with the change between them.

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

`pkg_rlc_attrib.py` answers three questions about ONE frequency of
ONE spec. They are numbered in the order they were built, not the
order you ask them -- Q0 is where a new job starts:

  Q0  COLD START. You have not written a spec yet. WHICH of the
      other 149 ports in this file matter at all? Answered by the
      four-step screen in the next section, and answered from
      ALL PORTS OPEN, because that is the configuration the
      disputed number came out of.
  Q2  ATTRIBUTION. Split the Z_ab you just read into "the bare EM
      coupling" plus one signed term per termination you declared.
      The terms add up to the total EXACTLY -- this is
      superposition, not a linearisation, not a sensitivity
      estimate, not a percentage anyone apportioned by hand.
  Q1  SENSITIVITY. What would Z_ab be if that ground ball were
      open instead? A 50 ohm resistor? A 1 nH lead? Also exact:
      it re-solves the network, it does not extrapolate.

Q2 and Q1 are what the Attribution window shows (Analyze ->
Attribution...). Q0 is CLI-only today.

The algebra, if you want it, is one Woodbury update: terminating a
set of ports is a low-rank change to the file's own admittance, so
the whole answer is a small dense solve on an m x m matrix, where
m is the number of terminations you declared. See docs/theory.md
section 13.


Start here: the cold-start screen (Q0)
---------------------------------------
Everything else on this tab assumes you already wrote a spec. At
the start of a job you have not. You know the victim and the
aggressor and nothing about the other ports, and the all-open
configuration -- the one this tool returns, and the one that
produced the number you are arguing about -- contains no
declarations at all, so the contribution table below is EMPTY by
construction. The cold-start screen is the answer to that, and it
is the first thing to run on an unfamiliar file.

Four steps, in this order. Every one of them is exact (a closed
form checked against a full re-solve to 1.5e-11, not a first-order
slope) and every one measures from ALL PORTS OPEN.

STEP 0  THE BRACKET. The quantity with every non-probe port OPEN,
        against the same quantity with every one of them at IDEAL
        GROUND, and the dB between them. It is first because it
        decides whether the other three steps are worth reading.
        Measured 25.67 dB on a planted 12-port case; a file that
        reads 0 dB has nothing else connected to your two coils
        and you can stop here.
        It brackets the OPEN..IDEAL-GROUND family and NOTHING
        else. It is not a bound over all terminations: a series
        ground inductance resonates with the structure's shunt C
        and can put M outside it (measured on
        diff_pair_4port.s4p, a peak of 9 mH of apparent M at
        L = 505 nH against a 1.01 nH open..ideal bracket). The
        report prints that caveat with the numbers, every time.

STEP 1  THE TWO-COLUMN SCREEN. Every port that is not part of a
        measurement port, with

            |Z_ap|   how strongly it talks to the VICTIM
            |Z_pb|   how strongly the AGGRESSOR talks to it
            delta    the exact effect of grounding it

        ranked by |delta|. The two coupling columns are separate
        ON PURPOSE and must never be read as their product: a port
        has to do BOTH to be a path. Measured on the planted case,
        the port with the LARGEST |Z_ap| in the whole file
        (34.777 ohm, 67% more than the real path's 20.873) has
        |Z_pb| = 0.038 and moves the answer by -0.378 pH, against
        -395.369 pH for the real one. Ranked on coupling to the
        victim alone that port is FIRST and it is worthless;
        ranked on the effect it is fifth of eight. That is the
        whole reason there are two columns and not their product.
        The NEGATIVE result is a result. The list ends with "the
        other N ports are all below X dB", which is what lets you
        say the coupling is local and stop looking.

STEP 2  THE PAIR SCAN over the top K of step 1, again from
        all-open. This is not optional. Measured: a shield brought
        out as two ports reads +9.689 pH with either end grounded
        alone and -870.268 pH with BOTH -- 90x the largest
        single-port effect in the file, with the OPPOSITE SIGN. A
        single-port ranking reports that as two minor positive
        entries and you never look at it again. The mechanism is
        the closed LOOP, not the grounding: "5 short_to 6" with no
        ground anywhere gives the identical -870.268 pH.
        The same step runs the MIRROR direction -- start with
        every candidate GROUNDED and open one at a time -- because
        the two catch opposite failures. Sixty ground balls read
        ~0 each from all-grounded, since the other fifty-nine
        carry the return; that same shield reads +880 pH per end.

STEP 3  THE GREEDY CUMULATIVE CURVE. Ground the best port,
        RE-RANK, ground the next best, and so on, tabulating the
        answer against k. Neither a ranking nor a pair scan tells
        you HOW MANY ports matter; this does, and the report names
        the k at which the curve saturates and the tolerance it
        used for the word. Greedy is not optimal -- the best-k
        subset is combinatorial -- but the re-ranking is what lets
        the walk stumble into the pair effects of step 2.

Port names are a PROPOSAL, never an assumption
-----------------------------------------------
Grouping ports by name family WOULD have caught the shield above,
because the two ends of a guard ring normally share a prefix. But
which ports are one structure is a semantic judgement about your
layout, and this tool will not guess it. So the numbers are
computed both ways and the grouping stays a sentence you accept or
reject:

    ports 5,6 share the name family 'guard_ring'; tested together
    they are -870 pH, tested separately +9.7 pH each -- if they
    are one structure, group them

Nothing in the bracket, the ranking, the pair scan or the curve
changes according to whether the file carries port names at all.

What the cold-start screen cannot find
---------------------------------------
Anything that needs THREE OR MORE ports to move together. Step 1
is one port at a time, step 2 is exactly two, and step 3 can
stumble onto a triple but has no guarantee. A three-terminal
version of the shield above is invisible to every step.

And, as everywhere in this layer: it cannot evaluate NEW METAL.
Every port it considers is one your S-parameter file already has.

Running it
----------
CLI only -- there is no window for the cold-start screen. Same
shape as --attribute, and the two may be given together (the
attribution prints first, because it explains the M printed just
above it):

      python pkg_rlc_extractor.py --cli <file> --mode coupling \\
          --mport "dco = 1" --mport "rx = 2" --freq 5.0 \\
          --cold-start dco,rx

Note there is no --gnd and no --short in that command. --cold-start
deliberately sets your declarations aside and starts from all-open;
the report names every one it set aside.

  --cold-start VICTIM,AGGRESSOR
      Turn the four-step screen on. Both sides are named exactly
      the way --attribute names them: a measurement-port NAME from
      --mport, or a 1-based position in that list.
  --cold-start-top K
      How many ports of the step-1 ranking enter the pair scan
      (default 8, i.e. 28 pairs). Refused below 2 -- the pair scan
      is not optional.
  --cold-start-cumulative K
      Depth of the greedy curve (default 12; it is always run). 0
      means every candidate, which is the one expensive setting
      here: measured 54.9 s at 151 candidates against 132 ms at
      K = 12.
  --cold-start-csv PATH
      Every record -- the bracket, the UNCAPPED screen, every
      scanned pair whether flagged or not, the whole mirror, the
      curve and the name-family suggestions -- one row each,
      tagged by a "section" column.

Cost, measured on a 153-port package export at one frequency: the
four steps together are 9.5 s, of which 9.3 s is the mirror
direction; at 38 candidates the same four steps are 17.6 ms. What
they replace is one full re-solve per candidate port: 2.41 ms for
the ranking against 2402.6 ms, a factor of 997.


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

Where to run it: the Attribution window
----------------------------------------
Analyze -> Attribution..., or right-click the trace in the Traces
list. It decomposes the SELECTED trace, so calculate that trace
first. It is a normal window and not a dialog: it stays open while
you edit, it keeps its own taskbar button, and you can park it on
a second monitor. Several can be open at once, on different traces
or different pairs; asking again for a pair that is already open
raises that window instead of making a second copy of it.

It refuses to open on a trace it cannot honestly describe, and it
says which of these is in the way:

   * the trace is a FROZEN snapshot -- its numbers came from an
     earlier run and can never be recalculated, so anything
     computed now could only be stamped with the CURRENT run;
   * the trace is STALE, i.e. edited since the last Calculate --
     the numbers and the spec beside them no longer describe each
     other;
   * it has no numbers yet, or its Touchstone file is not loaded;
   * it has only ONE measurement port. Z_ab is a MUTUAL impedance;
     there has to be a victim AND an aggressor.

The menu entry stays LIVE in all four cases on purpose. A greyed
entry cannot tell you why.

What is on the window, top to bottom:

  HEADER        trace / victim / aggressor / quantity / frequency
                / [Recompute]. Change any of the first five and
                press Recompute; nothing recomputes on its own,
                and that is deliberate (see below). The row wraps
                onto a second line at narrow widths rather than
                pushing [Recompute] off the end.
  BANNER        where these numbers came from -- "from run #7 @
                5.600 GHz". It turns into a warning the moment you
                edit the spec in the main window, which is what
                makes the button honest.
  SIGNS         the convention, stated once, above any signed
                number. A negative term OPPOSES the total and the
                terms sum to it exactly; shares are of the SIGNED
                total, so they can exceed 100% and go negative
                wherever terms cancel. The line clips at narrow
                widths -- the full declaration, including the
                ground model, is in Copy report and in the CSV.
  RECONCILE     the cross-check, in the header rather than the
                footer because it gates trust in everything under
                it: "reconciled  rel diff 3.1e-13 (floor
                4.3e-10)". If it ever fails, the per-element SPLIT
                is withheld and the TOTAL is still shown -- the
                total is compute_z_matrix's and is not in doubt.
  ACROSS FREQ   one line, and its OFF state carries the action. A
                ranking read off one frequency is a statement
                about that frequency, so the badge does not just
                say "not checked": it says what checking would
                COST on this file and does it in one click.
                Checked, it says what MOVED -- which elements
                changed rank, and at which frequency -- or, if
                nothing did, that the ranking is STABLE across the
                band, in those words. A stable ranking is a
                result, not an absence.
                The cost is one extra solve per frequency, at five
                frequencies including the one you are reading.
                Measured: 0.45 ms per point on the 4-port fixture
                below, and 223 ms per point on a synthetic dense
                153-port network -- so under 2 ms on a small file
                and roughly 0.9 s on a package export. That is why
                it is a click and not automatic. It is a ONE-SHOT:
                once it has run, the verdict beside it IS the
                answer and the button greys out. [Recompute]
                makes it live again, because that is a new
                decomposition.
  GROUND MODEL  how the declared shunt leads are modelled, in the
                same spelling the CLI uses: "diag" (the default --
                exactly as declared), "diag:L=1n" (every shunt
                lead gets that impedance on its OWN independent
                lead) or "shared:L=1n" (every lead keeps what it
                declares and they ALSO share that impedance back
                to the reference). One line beside the field says
                why the default is not obviously right:
                independent leads understate the shared return.
                Changing it takes effect on [Recompute], like
                every other input here -- it never re-decomposes
                on its own. See "One more thing this changes about
                your GND field" at the bottom of this tab.
                If the spec has no shunt lead to model at all --
                every ground expressed as "short_to", say -- the
                model CANNOT be applied, and the window says so
                rather than leaving you to read an unchanged
                number as "the shared return is worth 0 dB": the
                sign strip reads "NOT APPLIED", the line beside
                the field says why, and both exports carry it.
  TABLE         (o) Contributions   ( ) Sensitivity -- one pane,
                two views, not two tabs. Rows are coloured by
                element KIND, in the same palette as the Ports &
                Roles window. Never by sign: red means "warning"
                everywhere else in this tool, and a red negative
                would make a correct answer look like a fault.
                Click a row to drill into it.
  DETAIL        for the selected row: its element current, its
                transimpedance to the victim, what it would be
                worth as each candidate in the Candidates box
                (open, ideal, or R=/L=/C= -- the tool will not
                guess a package value for you), and the
                closed-form sweep of that one element plotted
                beside it, with both asymptotes and the current
                spec marked. Non-monotonicity is labelled where
                the sweep finds it, and so is any pole -- see
                "Reading the sweep plot" below. The pane is PROSE
                and it wraps; it never scrolls sideways, and the
                split above it opens sized to the table's own row
                count rather than to a fixed fraction. Drag the
                sash and it is yours until you close the window.
  FOOTER        Copy report / Export CSV... / Close. Both exports
                carry the full provenance: run number, the
                frequency and whether it was snapped to the file's
                grid, the whole sign convention, the ground model,
                and the termination spec verbatim.

Why a [Recompute] button and not an automatic refresh: the numbers
a trace carries are written by Calculate and by nothing else.
Editing the spec marks the trace stale and leaves those numbers at
the PREVIOUS run's. A window that re-decomposed on every keystroke
would therefore be checking a NEW spec against an OLD total, would
find them disagreeing by however much you just typed, and -- by
the reconciliation rule above -- would blank its own table. It
would erase itself while you type. So an edit moves the banner and
nothing else, and you press the button when you mean it.

The shared-return ground model is on the window, and it is worth
reading the last section of this tab before you leave it on the
default. One thing about it is unlike every other input here: the
dense (shared) model is a network that CANNOT be written as a
termination spec -- a shared return is a mutual impedance BETWEEN
two ground leads, and the connection table has no node to hang one
on -- so compute_z_matrix has never been asked about it and there
is no second opinion on its total. What is still checked is the
ARITHMETIC: the reconciliation line is always of the spec AS
DECLARED through the same machinery, so choosing a model does not
quietly cost you the cross-check, and the sign strip and both
exports name the model the numbers came out of rather than letting
you assume one.

Save Config remembers WHICH pair you were reading -- the victim,
the aggressor, the quantity, the frequency, the view and the
Candidates field -- but it does not reopen the window on Load. A
config carries the setup and never the results, so a just-loaded
trace has no numbers yet and the window could only open on its own
refusal. The Results pane names each entry it did not reopen;
Calculate, then Analyze -> Attribution..., and you land back where
you were.

Reading the sweep plot, and its pole
-------------------------------------
Click a row and the detail pane sweeps THAT element's series
inductance from ideal (L = 0) to open (L = infinity), in closed
form -- no loop, no sampling. Two numbers on it are the ones you
came for:

      M(0)     that termination made IDEAL
      M(inf)   that termination NOT THERE at all

They are exact, and the plot's y limits are set FROM them (plus a
margin), so the two readings you are comparing are always on
screen at a readable size.

Between them the curve may have ONE pole, and on a package it
usually does. A pole is not a numerical artefact and it is not
hidden: it is drawn as a labelled vertical line at the L where it
sits, with the value of the element there, because it is a real
physical event. The L you are adding RESONATES with the reactance
the network itself presents at that node. Measured on the fixture
below: the network looks like 2.005 fF at ground ball 3, and
505.25 nH series-resonates with 2.005 fF at exactly the 5 GHz
being read (5.0005 GHz, to five digits). There the termination is
anti-resonant, the curve runs off the top of the plot -- which is
what the label says -- and M passes through +-10.28 mH, ten
million times the [504 pH, 1.01 nH] the two endpoints span.

So the HEADLINE interval is the one over the POLE-FREE part of the
sweep, and the pole is stated separately, in words:

   "M lies in [-2.5 pH, 1.52 nH] over any ground inductance more
    than a factor of two away from the 505 nH resonance"
                                      ... is a budget statement.
   "M lies in [-10.3 mH, +10.3 mH]"
                     ... is the tool reading its own arithmetic
                         back to you. Same curve, same numbers.

The y axis is SYMLOG -- linear in a band around zero, logarithmic
outside it -- because M crosses zero here and a log axis cannot
draw that. The width of the linear band comes from the DATA, not
from a constant: this tool's plot panel uses 1e-6 for R/L/C, and
1 uH is a thousand times the whole curve above, so a fixed 1e-6
would put every point inside the linear band and symlog would
degenerate into the linear axis it exists to replace.

Both axes read in ENGINEERING UNITS -- "500 pH", not "5.0" under
a "1e-10" parked in the corner -- the same formatting the table,
the caption and the main plot's cursor readout use.

A sweep that does not move at all (M(0) == M(infinity)) gets an
axis the size of ITS OWN VALUE. That happens on real files --
decap_4port.s4p reads a flat -506.755 nH -- and an axis a
hundred thousand times the number on it is the same
uninformative picture from the other direction.

If the swept range holds no pole, none of this appears and the
plot is exactly what it was before.

Where to run it: the CLI
-------------------------
Add "--attribute VICTIM,AGGRESSOR" to any "--mode coupling" run,
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
        ideal (L = 0)         1.01 nH
        open  (L = inf)      503.7 pH
        pole at L = 505.25 nH -- the added L resonating with the
              2.005 fF the network presents at that ball, at the
              5 GHz being read
        over all L >= 0:     [-10.28 mH, +10.28 mH]   <- the pole
        away from the pole:  [-2.5 pH, 1.52 nH]       <- the answer
    Two things to take from that. The +-10.28 mH is arithmetic,
    not structure: it is the curve on its way past the pole, ten
    million times the endpoint bracket, and no component you can
    buy sits there. And the pole-free range is STILL wider than
    the two endpoints -- a series L resonates with the structure's
    shunt C, so [ideal, open] is not a bound even where nothing is
    anti-resonant. The tool labels the pole and headlines the
    pole-free interval instead of quoting a bracket that does not
    hold.

One more thing this changes about your GND field
------------------------------------------------
This is the most expensive modelling choice in the whole flow and
it is the easy one to make by accident, because the obvious
spelling is the wrong one.

A ground field written as N independent lumped_to_gnd inductors
says the balls have N independent return paths. Real package
ground balls SHARE A RETURN PLANE. N independent z in parallel is
z/N; N balls sharing one z is z -- so the independent spelling
understates the common-mode return inductance by roughly

      (1 + (N-1)*k_ret)

where k_ret is how strongly the return paths couple to each other.
That factor is why "independent" is not a conservative default: at
20 balls with a realistic k_ret = 0.2 it is 4.8x, which is 13.6 dB
of M, and it GROWS with the ball count -- the bigger and better
your ground field, the more the independent spelling flatters it.

Measured three times, on three different networks: 9.60 dB on a
four-ball cluster (four leads at 1 nH each, independently, against
the same four tied through ONE shared 1 nH), 8.09 dB on an
independently built 6-node 4-ball cluster, and 6.03 dB on
diff_pair_4port.s4p above. Every one of them is bigger than the
6.07 dB argument this whole layer exists to settle. Monotone in
k_ret, no threshold, so there is no safe default and the tool
refuses to pick one for you.

In the window it is the GROUND MODEL field: "diag" as declared,
"diag:L=1n" for independent leads, "shared:L=1n" for a shared
return. On the CLI it is the same spelling in one flag:
--attribute-ground-model diag:L=1n versus shared:L=1n. Either way
run it BOTH ways -- diag and shared are not a refinement of each
other, they are different answers, and the report says so.

You can also spell it right here in Mode 5, with no attribution
code at all -- and if you take one thing from this section, take
this one. Tie the whole ground set with ONE short row, then hang
ONE lumped_to_gnd on that node.

      independent:  3 lumped_to_gnd L=1n
                    4 lumped_to_gnd L=1n        M = 1.0120 nH

      shared:       3,4 short as pkg_gnd
                    pkg_gnd lumped_to_gnd L=1n  M = 2.0259 nH

Same file, same probes, same frequency: 6.03 dB apart. (It does
not matter WHICH port of the set carries the inductor -- by then
they are one node, so "3 lumped_to_gnd L=1n" is bit-identical, and
so is the older two-field spelling "3 short_to 4". The name simply
saves you having to pick a port and remember why.) What you must
not write is "3,4 lumped_to_gnd L=1n" after the short: once they
are one node that is two 1 nH leads in PARALLEL on it, i.e. a
500 pH shared return, which is neither of the two models above.
The validation strip refuses it and prints both numbers. Which
model is right is a question about your package, not about this
tool -- but you should answer it on purpose.


===========================================================
Hanging an EM block on a PACKAGE block (--compose, CLI only)
===========================================================
Two files -- your EM extraction and the package -- measured as ONE
network. There is no window for it yet; the command line is:

      python pkg_rlc_extractor.py --cli coil.s2p \\
          --compose-alias EM --compose "PKG=package.s3p" \\
          --compose-link "EM.2 short_to PKG.1" \\
          --compose-link "EM.1 lumped_between PKG.3 L=0.3n" \\
          --mode gnd --porta "EM.1" --gnd "PKG.2" --freq 5

Every port carries its file's tag, and the separator is a DOT.
Not a colon: ":" is already start:step:stop in every port field
in this tool, so "PKG:12" is a parse error today. A port with no
tag belongs to the file you named positionally, so a one-file
command line still reads exactly as it always did.

The mathematics is the pipeline you already know -- the two Y
matrices are stacked, each cross-file wire is an ordinary short or
lumped element, and the whole thing goes to the same solver. Every
mode, the Mode 5 DSL, Mode 6 coupling, the attribution and the
cold-start screen all work on a composition with nothing added.

ONE THING YOU MUST KNOW BEFORE TRUSTING THE NUMBER
--------------------------------------------------
Stacking two files WELDS their reference nodes together at zero
impedance. An n-port Y already has its own reference eliminated,
so there is no way to keep the two apart after the fact.

If your EM file's return current uses the EM model's own reference
-- the ordinary on-die convention -- then in the combined network
that current is already at the package's reference the moment it
leaves the die, and the package's WHOLE ground network is bypassed.
Measured on a 2 nH coil, a 100 pH package trace and a 100 pH
package ground lead:

    die return brought out as a PORT   2.2501 nH, and it MOVES
                                       when the ground path changes

    die return IS the EM reference:
      package ground pad grounded      2.1454 nH
      package ground pad open          2.1454 nH
      package ground pad through 1 nH  2.1454 nH   <- identical

Bit-identical, spread 0.000e+00. Nothing raises and the number
looks perfectly reasonable. So the tool perturbs each file's
declared ground set with a series inductor and re-solves; a change
of EXACTLY zero means welded, and it says so by name. That check
is mandatory output -- there is no flag to turn it off.

Composition answers your question only when the EM file brings the
return path out AS A PORT. That is a precondition, not a warning.

FREQUENCY GRIDS
---------------
The spans are intersected and extrapolation is refused; the report
says how many points were dropped. S is interpolated, never Y or Z
(S is bounded at every real frequency; Y blows up at a series
resonance and Z at a parallel one). Z0 is NOT renormalised because
it does not need to be -- Y does not depend on it, measured to
1.049e-17. What interpolation does break is PHASE: a 1 ns delay
across a 100 MHz step is 36 degrees of rotation, which the chord
turns into 0.436 dB of insertion loss that is not there. That is
warned past 20 degrees and refused past 60.

WHICH PACKAGE PIN COSTS YOU THE dB
-----------------------------------
--attribute and --cold-start work here, with one change you should
know about: the cross-file links go INTO the attribution baseline.
Otherwise all-open leaves the two files as disconnected islands
and every package-only element contributes EXACTLY 0 while the
reconciliation residual still reads healthy -- a confident,
perfectly reconciled wrong answer. The baseline for a composed
network is therefore "the files CONNECTED, everything else open",
the report names that gauge, and it cannot be switched off. Two
attribution reports are comparable only when their baselines match.

BEFORE AND AFTER, WITHOUT REBUILDING ANYTHING
----------------------------------------------
"What did the package cost me?" is already a gesture you have:
Calculate the bare EM trace, right-click it in the Traces list ->
Freeze as new trace, then add the package and Calculate again. The
two rows sit side by side in the results table, and the frozen one
can never be recalculated or edited by accident.
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

File tags (--compose, CLI only)
-------------------------------
With several files composed into one network, a port reference may
carry its file's tag, and the separator is a DOT:

   EM.1        port 1 of the file aliased EM
   PKG.40-42   ports 40, 41, 42 of the file aliased PKG
   PKG.1,2,3   the same three, listed

The dot is not a style choice: ":" is already the start:step:stop
separator above, so "PKG:12" is a parse error and always will be.
An untagged port belongs to the file named positionally, so every
single-file spelling on this page is unchanged. See the last
section of the "Mode 6 (Coupling)" tab.

Node names (Mode 5)
-------------------
A short row ties its listed ports into ONE node, and can give that
node a name in its Net cell ("coil_tap"). Every port field above
then also accepts that name, which resolves to the node:

   23,24,25   short           as coil_tap
   coil_tap   lumped_between  10  L=10f

A name may NOT be a number or a range, may not contain whitespace
or any of  :  ,  -  #  , may not be one of the syntax's own words
(ground / vdd / open / signal / short / short_to / lumped_to_gnd /
lumped_between / as) and may not be "A" or "B". It is matched
without regard to case and stored as typed. A name nothing defines
is an error listing the names that ARE defined; it never becomes a
new empty node.

The name is convenience, not capability: it stands for ONE member
port, so "coil_tap" and "23" compute the identical number. What it
buys you is that the wrong spelling stops looking natural: listing
every member of an already-merged node ("23,24,25 lumped_between
10 L=10f") is THREE elements in parallel, so 10 fH reads as
3.33 fH. The validation strip refuses that by name and prints both
numbers.

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

Switching a row OFF
-------------------
The box at the start of each connection row turns it off. The row
keeps its ports and its R/L/C and contributes NOTHING -- exactly as
if it were deleted -- so "what is this ground row worth?" is one
click and one Calculate, and one click back.

It is deliberately not the same as setting the Type to "open".
"open" is a DECLARATION: it reaches the port list, it is what makes
Ports & Roles show the port as deliberately open rather than
forgotten, and on an rlc_gnd row it silently discards the element
as well. Off is an ABSENCE.

The validation strip says how many rows are off and names them, so
a switch left down for a week is not a spec you have forgotten
about. It is saved with the trace, so a session comes back the way
you left it.

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
resistor or a lead inductance instead. Open it with Analyze ->
Attribution... on a calculated trace. It also states, in those
words, what the split is blind to: a port you left OPEN
contributes no row, so it contributes no term.

That last sentence is why the same tab carries a COLD-START screen
for the other direction -- which of the ports you have not written
a row for matter at all, ranked from all-open, before any of this
table exists. It is CLI-only: --cold-start VICTIM,AGGRESSOR. Read
it before you write your first ground row on an unfamiliar file.

Ports of a SECOND file:  F2.13
------------------------------
A trace can be built from more than one file -- an EM block hung
on a package block. Select the trace and open

      Analyze -> Files in this trace...

(also on the right-click menu of the Traces list and of the Files
list). Right-click a row there to add a file, remove one, or make
one the HOME file.

The home file needs no tag: a BARE port number always means a port
of the home file, in every mode, so every spec you already have
keeps its meaning and a single-file user never sees a tag. A port
of another file carries its tag:

      F2.13          port 13 of the second file
      F2.40-42       a range of it
      2,F2.1         a SHORT group across the two files
      25,26,F2.15    two home ports and a package ball, one node
      2 / F2.1       Port / To of an rlc_between row, with R/L/C

A TAG SCOPES THE ONE TOKEN IT IS WRITTEN ON. So a comma list mixes
files freely, in any order, and every token reads exactly as it
looks:

      25,26,F2.15    = home 25, home 26, package 15
      F2.15,25,26    = the same three ports
      25,F2.12,F1.65 = home 25, package 12, home 65

A RANGE IS ONE TOKEN, so `F2.40-42` takes a single tag. A LIST of
one file's ports needs the tag on each of them, or a range:

      F2.40,F2.42    two package ports
      F2.40-42       three package ports
      F2.40,42       package 40 and HOME 42  <- reads as written

Repeated ports are dropped, so a group can be built up without
worrying about naming one twice.

The tags are F1, F2, ... in the order the files are listed, F1
being the home file. A tag is a POSITION, so changing the home
file or removing one renumbers the rest -- the tool says so in the
Results pane when it happens, because an F2.<port> cell you have
already typed then names a different file.

A port cell shows about seven characters (measured: 72 px at 100%,
135 px at 150%, seven either way). "F2." spends three of them and
leaves four digits, which is why the tag is short and why it is
only ever written on the endpoint that crosses files.

Ports & Roles lists the composed port list -- every port with its
tag, its name and its role -- as soon as you type a tag. The
network itself is only stacked at Calculate.
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
Mode 6 tab is for, and the window is Analyze -> Attribution... on
the calculated trace (or right-click it in the Traces list). It
splits the extracted M into

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


Example F3: you do not know which ports matter yet
---------------------------------------------------
A fresh 153-port package export. You know the aggressor and the
victim and nothing else, and Example F2's table is empty because
you have declared nothing for it to rank.

Run the cold-start screen instead (Mode 6 tab, "Start here"):

      python pkg_rlc_extractor.py --cli <file> --mode coupling \\
          --mport "dco = 1" --mport "rx = 2" --freq 5.0 \\
          --cold-start dco,rx

and read it in the order it prints.

  STEP 0 says whether to bother at all: M with everything open
     against M with everything grounded. Measured 25.67 dB on a
     planted case -- that is a real argument. 0 dB and you are
     done, nothing else in the file touches these two coils.
  STEP 1 ranks every port by the EXACT effect of grounding it,
     with its coupling to the victim and its coupling to the
     aggressor as two SEPARATE columns. Watch that pair: the
     measured red herring had the largest coupling-to-the-victim
     in the whole file (34.777 ohm) and moved the answer by
     -0.378 pH, because its coupling to the aggressor was 0.038.
  STEP 2 grounds pairs of the top ports together. The measured
     shield reads +9.689 pH per end alone and -870.268 pH for
     both -- 90x, opposite sign. Step 1 alone would have shown you
     two harmless-looking positive entries.
  STEP 3 grounds them cumulatively, best first, re-ranking as it
     goes, and says how many actually matter.

Then write the GND rows the screen justifies, calculate, and use
Example F2's window to check what those rows are now worth. That
is the loop: cold start to decide what to declare, attribution to
audit what you declared.


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
