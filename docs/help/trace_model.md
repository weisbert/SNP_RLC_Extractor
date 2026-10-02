Trace model -- a routed trace as its pi circuit
===============================================

"What IS this routed trace, as a circuit, and what are its
element values?"  Give it the two ports that are the two ENDS of
the trace and it draws the pi -- series R and L, and the shunt at
each end -- with every value on the drawing, and its bandwidth.

It is a WORKSPACE of its own, not a kind of trace: press
"Trace model" on the strip under the menu bar ("RLC extraction"
takes you back).  The Loaded Files panel stays where it is; the
rest of the window is the workspace.  Nothing on the RLC side --
the Traces list, the editor, the plot -- is read or changed by it.

Step by step
------------
  1. Nets: pick the File, then "+ Add net" and fill one row per
     net -- a Name, and the IN+ and OUT+ ports.  For a
     differential pair fill IN- and OUT- too.  Nothing is named
     or picked for you: the port cells offer the file's port
     numbers (with the file's own port names beside them when it
     has any), and what goes in a cell is your choice.
  2. Other ports: list the ground ports under GND.  Every port
     not in a net row and not in GND is left OPEN, which the line
     beside the box says.
  3. Conditions: the marker Freq (GHz), and the Source (ohm) and
     Load (fF) the bandwidth table is read against.
  4. Calculate all.  The Summary gets one row per net; click a
     row to draw that net's pi and its response below it.

Several nets may share a port.  Each net is its own solve, so DQ_P
(1 -> 3), DQ_N (2 -> 4) and the pair DQ (1,2 -> 3,4) can sit in
three rows side by side -- that comparison is what the table is for.

A problem with a row is marked in that cell as you type, with the
reason under the table, and there is no dialog:

  * red    -- no name (once a port is typed), a duplicate name, the
              reserved names A / B, a port on both sides of one
              net, a port that is also in GND, a port past the
              file's port count, or a differential net with only
              one of its two minus cells filled;
  * amber  -- a differential side that ties several ports: the pair
              is solved, but the imbalance check below is skipped.

A completely blank row is left unpainted: it is the row you type
into next.

What the workspace shows
------------------------
  Summary    one line per net:
                Net  R_ser  L_ser  C_in  C_out  f_3dB (open)  lumped
             Click a heading to sort by it, again to reverse it.  A
             net that could not be solved says why ("error: ...")
             instead of leaving a blank line.
  Schematic  the selected net's pi, values on the drawing.
  Response   |H(f)| of the selected net into each load, one curve
             per load, the other solved nets overlaid dashed.  The
             marker can be DRAGGED here: it lands on the nearest
             sweep point and writes Freq.
  Details    collapsed at first (the triangle opens it): the full
             report for the selected net -- every branch, its |Q|,
             the lumped check and the bandwidth table -- plus any
             warning the solver gave.
  Export CSV the Summary at full precision, then every net's branch
             values, then every net's bandwidth table.

Each net is solved on its own: one bad row does not stop the
others.  Nothing is redrawn underneath you.  Editing a row marks
THAT row stale; changing GND or the file marks EVERY row stale;
a stale row keeps its old numbers, says "stale", and waits for
Calculate all -- because a picture that quietly became a picture of
something else is worse than no picture.  Calculate all re-solves
only the rows that need it.

Two inputs are not like that, because they need no new solve:

  * Freq.  The pi is read off the sweep that is already in memory,
    so moving the marker (or typing a frequency) answers at once.
  * Source and Load.  They only pick rows of the bandwidth table,
    which is re-read from the same sweep.

The workspace's rows, GND and conditions are saved with the session
(Save Config), like everything else you typed.  The numbers are not:
after a load every row reads "not calculated yet" until Calculate
all.

FROM THE COMMAND LINE
---------------------
One net at a time, through the coupling mode:

    --mode coupling --mport "in = 1" --mport "out = 2" \
    --gnd 3 --freq 0.1 --trace-model in,out

It is the same arithmetic, bit for bit: the workspace and the CLI
print the same numbers for the same net.

IT IS NOT A FIT
---------------
A two-port's Y matrix and a pi circuit are the same object, not
an approximation of one another, so the elements come out EXACT
at the frequency you ask for.  Nothing is least-squared, nothing
assumes the two ends are symmetric.  The 2x2 it reads is the
open-circuit Z of the two ends -- the same Z matrix the Coupling
tab describes, for a "measurement port" at each end.

DIFFERENTIAL: NOTHING EXTRA TO TURN ON
--------------------------------------
Give each measurement port a MINUS side and you get the
differential pi.  In the workspace that is the IN- and OUT-
cells of the net's row; on the command line it is the '/':

    --mport "in = 1 / 2" --mport "out = 3 / 4" --gnd 5 \
    --freq 0.1 --trace-model in,out

A file with a separate ground pin just adds the ground; a 4-port
file with no ground pin leaves it out.  Both work.

The differential drawing has NO ground rail under it, on purpose:
the shunt goes between the two conductors, and there is no
reference node in that picture to draw.

The differential shunt is the capacitance ACROSS THE PAIR --
what a differential driver actually sees -- and it is HALF the
per-line odd-mode capacitance an EM tool usually quotes.  The
report prints both on the same line so there is nothing to guess:

    C = 0.5 fF          (across the pair)
    (odd 1 fF)          (per line, odd mode)

It also measures how much differential energy the pair converts
to common mode.  The differential pi assumes common mode OPEN at
both ends, which is exact for a symmetric pair and an unstated
assumption otherwise, so the report states it: above 5 % the pair
is imbalanced and the pi is the differential part only.

BANDWIDTH: THREE NUMBERS, KEPT APART
------------------------------------
"The bandwidth of this trace" is three different things:

  model band   how high the pi is still ONE lumped pi.  Nothing
               declared, nothing assumed.  It usually answers
               "the top of the sweep", which means the FILE
               stops there -- not the model.
  corners      f_RL = R/(2*pi*L) and f_RC = 1/(2*pi*R*C), per
               branch.  Where the reactance overtakes the
               resistance.  These EXPLAIN the third number.
  -3 dB        what everyone means -- and NOT a property of the
               trace.  It belongs to trace + source + load.

That last point is why the report prints a TABLE and not a
number.  On a real routed line the load capacitance alone moves
the answer 7.3x (15.31 GHz open, 2.09 GHz into 200 fF), and the
source resistance another 1.9x.  The Source (ohm) and Load (fF)
fields under Conditions pin the row you care about, and the
Response curve under the schematic is drawn from the same
numbers.

The "vs marker" column is the useful one: "6.3 GHz" is a fact,
"82x your working frequency" is an answer.

Three things it will not do:

  * it never extrapolates past the file.  If the sweep never
    drops 3 dB you get "> 5 GHz" and the droop actually reached;
  * it references the BOTTOM OF THE SWEEP, not DC, and says so;
  * a response that PEAKS has no -3 dB bandwidth.  A series L
    into a load C rises before it rolls off, so the column says
    "peaks +13.6 dB at 8.2 GHz" instead of a number that would
    be measured from a baseline the curve already left.

From the command line: --trace-model-src OHM and
--trace-model-load FF[,FF].

READ THE |Q| BEFORE YOU READ THE C
----------------------------------
Every branch prints its own |Q| and what follows from it:

    series    |Q| = 0.003087   a resistor, the reactance is a
                               residue -- do not read L or C
    shunt_in  |Q| = 532.6      capacitive -- read C

|Q| far below 1 means the branch is a RESISTOR whose reactive
part is too small to interpret.  A series branch like that will
still print a capacitance -- it has to, the sign is real -- but
that number is the reading of a fraction of an ohm, not a
capacitor.  This is the most common way to misread the tool.

IS IT ONE LUMPED ELEMENT ACROSS YOUR BAND?
------------------------------------------
The report re-reads the same pi at the BOTTOM of the sweep and
prints the movement per branch.  Inside 10 % the values are
reusable anywhere in the band; past it the structure is not one
lumped pi and the values are good at that frequency only.  This
costs no extra solve -- both points are already in the sweep.

IF YOUR WORKING FREQUENCY IS BELOW THE FILE
-------------------------------------------
The marker resolves to the nearest swept point and the report
always prints BOTH the point it used and the frequency you
asked for, so you are never answered silently at a frequency you
did not choose.  Whether that reads as a note or as an "outside
the swept band" warning depends on how coarse the grid is at
that end.
