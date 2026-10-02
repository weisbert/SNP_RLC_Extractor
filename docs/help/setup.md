Setting up a measurement
========================

Every trace is set up the same way: two tables, and nothing else.

   MEASUREMENT PORTS   what you are measuring -- where the probes go
   CONNECTIONS         what else is attached -- ground, supply,
                       shorts, lumped elements

                       Ports not listed anywhere are OPEN.

Older builds had five modes (Port -> GND, A <-> B, A <-> B + Short
Pairs, Custom, and +/- Ports / Coupling). Every one of them was a
particular way of filling these two tables, and the solver always
received the same thing. The modes are gone; the common setups are
TEMPLATES now (below), and a config saved by an older build is moved
into the tables when it is loaded -- see "Configs from older builds"
at the end of this tab.

The editor, top to bottom
-------------------------
   File               which loaded file the trace reads
   Template           fills both tables with a common setup
   Measurement ports  Name | + ports (red) | - ports (black)  [+ Add]
   Connections        Type | Port | To | R | L | C            [+ Add]
                      Ports not listed anywhere are OPEN.
   two lines          the port census, then problems (or the parsed
                      values of your element rows)
   Plot               this trace  (and, with two or more measurement
                      ports: self, mutual)
   Label, Style

Both tables are always there. "+ Add" adds a row; the "X" at the end
of a row deletes it. Whatever is in the tables IS the trace -- there
is no Apply button (see "Editing traces" on the Overview tab).


Measurement ports: a red probe and a black probe
-------------------------------------------------
Think of a bench multimeter. Every measurement is ONE pair of
probes:

      RED probe   --> the "+" ports
      BLACK probe --> the "-" ports

A row of the table is one measurement port:

      Name    + ports    - ports
      tank    1          2

  * SINGLE-ENDED: fill "+" only. The black probe is on ground, and
    you get the impedance from the "+" ports to ground.
  * DIFFERENTIAL: fill "+" AND "-". You get the impedance between
    the two sides -- for a balanced coil, the L_diff a VCO tank
    actually resonates with.

Several ports on the SAME side are tied together into one terminal
(the sum of their currents at one common voltage): "+ 1,3   - 2,4"
is a red probe touching 1 and 3 at once. There are no weights; a
port is on the "+" side, on the "-" side, or not in this
measurement port at all.

A differential measurement is SOLVED DIRECTLY, with the black probe
on the "-" ports. It is never computed by measuring the two sides
single-ended and combining them (Z11 + Z22 - Z12 - Z21). That is the
same algebra, but on a real file it subtracts two large numbers to
get a small one. Measured on tests/fixtures/decap_4port.s4p at
1 GHz: port 1 single-ended reads -12642 nH (the other end is open,
so it is a capacitor), port 1 to port 2 differential reads 1.000 nH,
and the combined single-ended route is off by up to 1.5e-8 relative
across the sweep. So "+ 1" and "+ 1  - 2" are two different rows
meaning two different things, and the table never confuses them.

Do not put both terminals of a coil on the "+" side ("+ 1,2", empty
"-"). That ties them into one node: it is the COMMON-mode impedance,
a completely different number from L_diff.

The Name is optional. A blank one is shown and solved as P1, P2, ...
in order. "A" and "B" are reserved (they are the old "signal A /
signal B" spelling), in either case.

ONE row gives you R, L, C, Q and the broadband fit. TWO OR MORE rows
also give you the coupling between them -- M, k, M/L, C_c -- and the
results pane switches to the coupling block. See the "Coupling" tab.


Connections: what else is attached
----------------------------------
The cells a row has FOLLOW ITS TYPE:

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

GROUND AND SUPPLY. This is AC small-signal analysis: an ideal supply
IS a short to the reference node, so a VDD ball and a GND ball
impose the same boundary condition, V = 0. Put supply pins in a
ground (or vdd) row together with the ground pins; "vdd" exists only
so the table says what the pins are. If you leave supply pins out
because "they are not ground", they float and the impedance comes
out too high.

  * Every port field takes the full range syntax, so a package's
    ground balls are ONE row: "6-14" or "35:1:45". See the "Input
    syntax" tab. ("35:45" is an error -- the MATLAB form needs all
    three fields.)
  * A short row has ONE port field: list the whole tied group in it
    ("5,6,7,8", "23-25"). A group of shorted pins has no natural
    "from" and "to". The freed cell is the node's NAME -- see
    "Naming a node" below.
  * The Port / To dropdowns list port NUMBERS. To see which ball is
    which on an unfamiliar file, click "Show Ports": the "Ports &
    Roles" window lists every port with its name, the role your
    setup gives it and the row that said so, and can write a
    selection back into these tables as a collapsed range.
  * rlc_between is the only Type with two port fields, because a
    two-terminal element really has two ends. It takes exactly ONE
    partner port (an N-to-M lumped element is ambiguous -- star?
    mesh?). ground / vdd / open / rlc_gnd are always to ground.
  * A range on an rlc_gnd or rlc_between row is ONE ELEMENT PER
    PORT, not one element shared by them. "21:1:25" with L = 80p
    is five separate 80 pH inductors, one from each of ports
    21..25 to ground -- the right model for five ground balls each
    with its own ball inductance. For ONE shared 80 pH instead,
    short the ports and hang the element off the NODE:

         short     21:1:25   as gnd_ring
         rlc_gnd   gnd_ring  L=80p

    What you must NOT write is "rlc_gnd 21:1:25 L=80p" after the
    short. That is five 80 pH inductors between the SAME two
    nodes, i.e. 16 pH, and the strip refuses it by name: "ports
    21-25 are ALREADY ONE NODE ... L 80 pH becomes 16 pH".
  * Two rlc_between rows on the SAME port pair are two elements in
    PARALLEL -- their admittances add, which is how to write
    R_on || C_ds for a switch. Two rlc_gnd rows on the same PORT
    are NOT: a port carries one termination, so the row further
    DOWN the table wins. R, L and C within one row are always a
    SERIES branch.
  * R / L / C hold the bare value; the unit is in the header. SI
    suffixes apply, and "5m" is 5 milli while "5M" is 5 Mega. The
    value must be ONE word: "5 m" and "1 uF" are REJECTED, because
    "R=5 m" would otherwise quietly compute 5 ohm.
  * A BLANK R/L/C means OMITTED, which is not zero. An omitted C is
    "no capacitor in the series branch"; C = 0 would be an open
    circuit. An element row with NO R, L and C at all is a 0-ohm
    short and gives NaN everywhere; the strip says so.
  * The box at the start of a connection row switches it OFF
    without deleting it -- see "Switching a row OFF" on the "Input
    syntax" tab.


Ports not listed anywhere are OPEN
----------------------------------
That line sits under the connections table and it is the most
common source of a wrong answer. A port you did not list carries no
current (I = 0). It is eliminated exactly -- the network at the
ports you kept behaves as it should -- but it is NOT tied to
ground. Forget a package's ground balls and they float; the number
is then the impedance of a different circuit.

Whether the other ports are ideal grounds, series inductors or left
open can move a coupling number by decibels, not percent. The
"Where the number came from" section of the "Coupling" tab is the
tool that measures how much.


Templates
---------
The Template box fills both tables for a common setup. What you see
afterwards IS the table -- nothing is hidden, and every cell can be
edited. If either table already has something in it, you are asked
before it is replaced. The box then goes back to its prompt.

   Port to GND                 P1: + 1
                               (fill the blank ground row below
                               with your ground ports)
   Between two ports           P1: + 1   - 2
   Loop with shorted far end   P1: + 1   - 2      short 3,4
   Several nets (coupling)     P1: + 1   and   P2: + 2

A template fills port numbers that the file has; a cell is left
blank where the port does not exist. Change the numbers to your
ports -- the template is a starting shape, not a guess about your
file.


The probe rules
---------------
A port in a measurement port can also appear in a connection row.
What that means depends on which SIDE it is on, because every port
on one side is tied to the others: grounding one of them grounds
the whole side.

  * A "+" PORT IN A GROUND (or vdd) ROW IS AN ERROR. The red probe
    would sit on a node held at 0 V; there is nothing to measure.
    Drop the port from one of the two.

  * A "-" PORT IN A GROUND (or vdd) ROW IS ACCEPTED, with an amber
    note. The whole "-" side is then at ground, so this is the
    single-ended measurement of the "+" side to GND, and it is
    solved as exactly that:

       '-' side is grounded (port 3), so this measures P1 to GND;
       ports 3,4 are all at GND.

    When EVERY "-" port is grounded the number is the same as
    "+ 1" with a ground row 3,4, to the last bit. When only SOME of
    them are, the rest of the "-" side is at ground too, because it
    is tied to the grounded ones. (Older builds instead quietly
    dropped the grounded port from the "-" side and reported a
    different, physically wrong number.)

The other refusals, each one an error:

  * a measurement port named "A" or "B";
  * two measurement ports with the same name;
  * one port on both the "+" and the "-" side of one measurement
    port;
  * one port claimed by two measurement ports -- a port can carry
    only one probe;
  * a "-" side with no "+" side -- the red probe must touch
    something;
  * a port number the file does not have ("3 / 5" on a 4-port
    file, the classic one-digit typo).

A problem is shown IN THE CELL as you type -- red for an error,
amber for a note -- and the reason is a line under the tables.
There is no dialog. A trace with an error is skipped by Calculate,
and the Log says which trace and why; the other traces are
calculated as usual.


The old modes, written as rows
------------------------------
   Old mode                         As rows
   ------------------------------   --------------------------------
   Port(s) -> GND        (Mode 1)   "+" only; the GND ports in a
                                    ground row
   A <-> B               (Mode 2)   "+" = A, "-" = B; GND ports in a
                                    ground row
   A <-> B + Short Pairs (Mode 3)   as Mode 2, plus one short row
                                    per pair
   A <-> B + VDD         (Mode 4)   as Mode 2; VDD ports in the
                                    ground row (or a vdd row)
   Custom                (Mode 5)   unchanged -- it was already
                                    these two tables
   +/- Ports / Coupling  (Mode 6)   one row per measurement port;
                                    GND ports in a ground row

The command line keeps its own spelling (--mode gnd | p2p |
coupling, with --porta, --portb, --gnd, --short, --mport); see the
README.


Typical setups
--------------
  Bond-wire / via inductance from a die pad to package ground
      P1: + die pad            ground: the GND ports
  Power-rail impedance from a VRM pin to die ground
      P1: + VRM pin            ground: ALL the die GND pins
  A 1-port inductor
      P1: + 1                  (no connection rows)
  A 2-port coil (P, N)
      P1: + 1   - 2
  Input differential capacitance of a pair, far end open
      P1: + in_p   - in_n      (out_p, out_n not listed: open)
      fit the Capacitor model in a band well below SRF
  Loop inductance of a pair with the far end shorted
      P1: + in_p   - in_n      short: out_p,out_n
      ground: the GND port, if the file has one
      fit the Inductor model; for ideal coupled lines
      L_loop = 2 * (L_self - M)
  Decap mounting: impedance from the top pads, cap installed
      P1: + top_signal   - top_return
      short: bot_signal,bot_return
  PDN impedance with mixed VDD/GND balls
      P1: + die signal pin   - die return pin
      ground: all GND balls AND all VDD balls
  A trace into a 50-ohm load
      P1: + 1                  rlc_gnd: port 2, R = 50


Naming a node
-------------
A short row creates a NODE, and the cell it does not need for a
second port field is that node's NAME:

   Type    Port          Net
   ------  ------------  ----------
   short   23,24,25      coil_tap

Any port field in either table may then say "coil_tap" instead of a
port number -- an element row, a probe's "+" or "-" side, a later
ground row:

   Type          Port      To       L
   ------------  --------  -------  -----
   rlc_between   coil_tap  10       10f

The name is pure convenience. It resolves to one member port of the
node, so the answer is bit-identical to typing "23" there.

What a name may be:
   * NOT a number or a range ("1", "6-14", "35:1:45" are refused).
   * No spaces, and none of  :  ,  -  #  .
   * Not one of the text form's own words (ground, vdd, open,
     signal, short, as, ...), and not "A" or "B".
   * Matched WITHOUT regard to case, stored exactly as typed.

A name nothing defines is an ERROR naming the ones that are defined
-- never a new, empty node. Two names for ONE node is an error too:
put all of its ports on one short row.

Every merged node appears at the TOP of the Port / To dropdowns,
above the bare port numbers. Picking the node there is the right
gesture; typing out all of its members is the one that multiplies
your element by N.


The two lines under the tables
------------------------------
   Ports (45): 4 probe . 8 ground . 1 element . 32 open
   ✓ port 13 -> GND: 5 mOhm + 500 pH + 1 uF

The first is the port census -- every port of the file, bucketed.
The second is either a problem or, when there is none, the PARSED
value of your element rows: "5m" and "5M" are one shift key and
nine orders of magnitude apart. The strip shows at most two lines;
"Calculate All & Plot" writes the whole list to the Results pane.


Edit as text...
---------------
The button above the connections table opens the same setup as
text. It is not a second format: the tables serialise to exactly
that text, and that text is what the parser sees.

  <port>  signal <name> [+|-]           (sign defaults to '+')
  <port>  ground                        (or 'gnd')
  <port>  vdd                           (alias of ground)
  <port>  open                          (default if not listed)
  <ports> short [as <name>]             (ties the list into ONE
                                         node, optionally named)
  <port>  short_to <other_port>         (the same, in two fields)
  <port>  lumped_to_gnd <R/L/C params>
  <port>  lumped_between <other_port> <R/L/C params>

One directive per line; '#' starts a comment. R/L/C use R=, L=, C=
(any subset; series R-L-C), with the SI suffixes above. The sign of
"signal" is a SEPARATE token: "signal tank -" is the "-" side of
"tank", while "signal tank-" is a measurement port literally named
"tank-".

Two coils in a 4-port file:

    1 signal tank +
    2 signal tank -
    3 signal vco2 +
    4 signal vco2 -

A trace into a 50-ohm load, and a decap between two pads:

    1 signal m1
    2 lumped_to_gnd R=50

    1 signal m1 +
    2 signal m1 -
    3 lumped_between 4 R=0.01 L=0.1n C=1p

Your text comes back rewritten into canonical form ("gnd" becomes
"ground", R/L/C reordered, comments dropped) and moved into the
tables. Anything the tables cannot represent -- comment lines,
hand-written directives -- is kept verbatim as text and appended;
the Connections caption then carries a permanent "(+N lines kept as
text)" marker, because that text is emitted last and decides the
answer. If moving your text into the tables would change what it
computes, the whole of it is kept as text instead, and you are told.

"signal A" and "signal B" are the old spelling -- "signal B" is
exactly "signal A -" -- and they still parse. A measurement port
called A that comes in through Edit as text is renamed to the first
free P<n> before it lands in the table: the name is a label, and
what it measures does not change.


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
token it is on. The full rules -- what a tag is, why it is short,
and what renumbers it -- are on the "Input syntax" tab.


Configs from older builds
-------------------------
Loading a config saved by an older build moves EVERY trace into the
two tables at once, and the Log gets one line per trace saying what
it became, e.g.

   [3] loop: the 'A ↔ B + Short Pairs' setup is now rows of the two
       tables: P1 +1 -2; ground 5; short 3-4

Only the fields the old mode actually used are carried over -- the
leftovers of another mode a trace was once switched to are dropped,
as they never counted. Port lists lose their spaces ("1, 2" becomes
"1,2"); the meaning is the same. Every number is bit-for-bit what
the older build computed, with these exceptions, each one named in
the Log:

  * A "+" port that was ALSO a GND port: the old build silently let
    the ground win. The port is now left out of the "+" side
    explicitly, so the number is unchanged and the setup says so.
  * A "-" side that was only PARTLY in GND: the old build dropped
    the grounded port from the probe and gave a physically wrong
    number. The whole "-" side is now at ground (the probe rule
    above), so this number changes, and the Log line says why.
  * A coupling trace with a probe port in GND, which the old build
    refused outright: a grounded "-" side now measures "+" to GND,
    so that trace has a number now.
  * A measurement port named "A" is renamed to the first free P<n>.

A migrated config is saved in the new form. An older build reading
it opens every trace as a Custom trace, which is what it is.
