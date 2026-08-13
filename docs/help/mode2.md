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
