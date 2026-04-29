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

Universal assumptions (apply to ALL modes)
------------------------------------------
1. AC small-signal analysis only. There is no DC operating point.
   "VDD" ports are AC-grounded -- mathematically identical to GND.

2. Ports are 1-based in the GUI/CLI, 0-based internally.
   Conversion happens at the input boundary.

3. UNUSED ports default to OPEN-CIRCUIT (I = 0).
   This is the most common source of wrong results: if you forget to
   list a GND port in the GND Ports field, that port floats. It is
   then eliminated via Schur complement -- which preserves the
   network behavior at kept ports but does NOT tie the floating port
   to ground.

4. Multiple ports inside one Signal group are SHORTED TOGETHER
   internally before measurement. If you list "1,2,3" as Port A in
   Mode 1, the tool treats them as one merged terminal carrying the
   sum of currents at a common voltage.

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
              M4: 1<->2 V:[3] G:[4]          -- Mode 4 with VDD
              M5: <first 28 chars of DSL>    -- Mode 5 (custom)
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
GND Ports         : optional V=0 ports. Often blank for differential
                    measurements where the network has no explicit
                    ground port.

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

Common mistakes
---------------
- Using Mode 2 to measure inductance of a SINGLE-ENDED trace --
  that is Mode 1 (signal -> GND), not Mode 2.
- Forgetting that unlisted ports are OPEN -- if your differential
  pair has a far end that should be SHORTED, you need Mode 3.
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


HELP_MODE4 = """\
Mode 4 -- A <-> B + VDD/GND
===========================

Physical model
--------------
Same as Mode 2, but separately tracks VDD ports for documentation
clarity. Mathematically identical to listing them in GND Ports.

Why a separate mode? In real packages, distinguishing GND from VDD
is important for users (different schematic nets, different return
currents in DC). For AC small-signal impedance, they behave the
same -- both are AC-grounded by ideal supplies.

Inputs
------
Signal / Port A,
Port B,
GND Ports         : same as Mode 2.
VDD Ports         : ports connected to an ideal supply. INTERNALLY
                    treated identically to Ground.

Typical use cases
-----------------
* PDN impedance with mixed VDD/GND pins:
    A = die signal pin, B = die return pin
    GND Ports = all GND balls
    VDD Ports = all VDD balls (each ball at AC ground via the supply)

* Documentation clarity when reporting results to a digital designer
  who cares about VDD vs GND distinction.

Note
----
If you don't care about the VDD/GND distinction (just need both
shorted to AC ground), you can use Mode 2 and put both groups in
the GND Ports field -- you'll get the same numbers.
"""


HELP_MODE5 = """\
Mode 5 -- Custom (advanced)
===========================

The named modes 1-4 cover the most common configurations. For
anything beyond them -- arbitrary lumped R/L/C terminations, mixed
short and lumped couplings, etc. -- use Custom mode and write a
small per-port termination spec.

Syntax
------
One directive per line. Lines starting with '#' are comments.
Each directive begins with a 1-based port number, then a kind:

  <port>  signal A
  <port>  signal B
  <port>  ground                       (or 'gnd')
  <port>  vdd                          (alias of ground)
  <port>  open                         (default if not listed)
  <port>  short_to <other_port>
  <port>  lumped_to_gnd <R/L/C params>
  <port>  lumped_between <other_port> <R/L/C params>

R/L/C parameters use 'R=...', 'L=...', 'C=...' (any subset).
Series RLC is computed automatically: Z = R + jwL + 1/(jwC).
Values support SI suffixes:
   f=1e-15  p=1e-12  n=1e-9  u=1e-6  m=1e-3
   k=1e3    M=1e6    G=1e9   T=1e12
or plain scientific notation (1.5e-9, 50, 0.001).

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
Custom mode is purely additive. Anything you can express via
named modes 1-4 can be expressed in Custom mode. The named modes
exist for ergonomic shortcuts, not extra capability.

For example, this Custom spec is exactly equivalent to Mode 3
with A=1, B=2, ShortPairs="3-4":

    1 signal A
    2 signal B
    3 short_to 4

(Verified by unit test -- numerical results identical.)
"""


HELP_SYNTAX = """\
Input syntax reference
======================

Port range syntax
-----------------
Used for Signal/Port A, Port B, GND Ports, VDD Ports.
All port numbers are 1-based.

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
"""


HELP_TOPICS = [
    ("Overview",        HELP_OVERVIEW),
    ("Mode 1 (->GND)",  HELP_MODE1),
    ("Mode 2 (A<->B)",  HELP_MODE2),
    ("Mode 3 (+Short)", HELP_MODE3),
    ("Mode 4 (+VDD)",   HELP_MODE4),
    ("Mode 5 (Custom)", HELP_MODE5),
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
