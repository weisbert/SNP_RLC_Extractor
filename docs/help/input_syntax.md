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
