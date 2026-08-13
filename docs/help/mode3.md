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
