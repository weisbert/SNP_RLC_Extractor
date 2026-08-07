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
   ID      trace id (in brackets), matches the Traces list
   Label   user-given trace label (truncated)
   File    only shown when traces span >1 file (alias F1, F2, ...)
   Ports   compact port-config descriptor:
              M1: S:[1] G:[2,3]              -- Mode 1 (port-to-GND)
              M2: 1<->2 G:[]                 -- Mode 2 (port-to-port)
              M2: 1<->{2,3} G:[4]            -- multi-port terminal
              M3: 1<->2 G:[] S:[3-4]         -- Mode 3 with shorts
              M5: <first 28 chars of DSL>    -- Mode 5 (custom)
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
lumped couplings, etc. -- use Custom mode and write a small per-port
termination spec.

Syntax
------
One directive per line. Lines starting with '#' are comments.
Each directive begins with a 1-based port number, then a kind:

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

Examples
--------
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
Measurement ports : one spec per port -- Port 1, Port 2, and any
                    further entries. Full syntax in the "Input
                    syntax" tab. Names are optional; unnamed ports
                    are auto-named P1, P2, ... The names "A" and "B"
                    are reserved for the legacy modes.
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
Fill in Port 1 only and leave Port 2 empty:

      Port 1 = tank = 1 / 2

You get the DIFFERENTIAL self impedance of that structure -- the
impedance a differential driver sees across the two terminals, so
L is the differential self-inductance L_diff, the number a VCO tank
actually resonates with.

Contrast that with tying both terminals into one node:

      Port 1 = tank = 1,2 /        <-- both on the RED probe

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
   Port 1      = tank = 1 / 2
   Port 2      = vco2 = 3 / 4
   GND Ports   = (blank -- the file has no ground port)
   Short Pairs = (blank)
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
"""


HELP_SYNTAX = """\
Input syntax reference
======================

Port range syntax
-----------------
Used for Signal/Port A, Port B, GND Ports, and for each side of a
Mode 6 measurement port. All port numbers are 1-based.

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
   Custom Spec:
       1 signal A
       2 lumped_to_gnd R=50
   RLC Freq    = your operating frequency


Example F: Mutual inductance between two bond-wire loops
---------------------------------------------------------
File:    6-port PKG file (wire A: die=1, ball=2;
                          wire B: die=3, ball=4;  gnd = 5,6)
Goal:    M and k between the two loops at 1 GHz

   Mode        = 6 (+/- Ports / Coupling)
   Port 1      = wA = 1 / 2
   Port 2      = wB = 3 / 4
   GND Ports   = 5,6
   RLC Freq    = 1.0 GHz

Reads:   L_wA = 1.8 nH, L_wB = 1.9 nH
         M    = 0.42 nH,  k = 0.227
         M/L_wA = 23.3%  (-12.6 dB)  -- strong; these loops share a
         lot of flux, so they need spacing or a ground wire between
         them.

See the "Mode 6 (Coupling)" tab for what each number means and for
the layout-iteration loop.


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


HELP_TOPICS = [
    ("Overview",        HELP_OVERVIEW),
    ("Mode 1 (->GND)",  HELP_MODE1),
    ("Mode 2 (A<->B)",  HELP_MODE2),
    ("Mode 3 (+Short)", HELP_MODE3),
    ("Mode 5 (Custom)", HELP_MODE5),
    ("Mode 6 (Coupling)", HELP_MODE6),
    ("Input syntax",    HELP_SYNTAX),
    ("Worked examples", HELP_WORKFLOWS),
]


class HelpWindow(tk.Toplevel):
    """A tabbed reference window. One tab per topic."""

    def __init__(self, master):
        super().__init__(master)
        self.title("PKG RLC Extractor -- Help")
        self.geometry("950x650")

        nb = ttk.Notebook(self)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        for title, body in HELP_TOPICS:
            frame = ttk.Frame(nb)
            nb.add(frame, text=title)
            txt = ScrolledText(frame, wrap=tk.WORD, font=("Consolas", 10))
            txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            txt.insert("1.0", body)
            txt.configure(state="disabled")
