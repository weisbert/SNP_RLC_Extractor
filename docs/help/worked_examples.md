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

      python pkg_rlc_extractor.py --cli <file> --mode coupling \
          --mport "dco = 1" --mport "rx = 2" --freq 5.0 \
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
