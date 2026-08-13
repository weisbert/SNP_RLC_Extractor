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
