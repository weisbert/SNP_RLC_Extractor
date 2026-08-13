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
