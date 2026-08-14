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

Mode 5 is this same measurement-port table plus a connections table
underneath it, so anything you can set up here you can also set up
there with extra terminations attached -- and two or more
measurement ports produce the coupling matrix in either mode.

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
Measurement ports : a TABLE, one row per measurement port, with a
                    "+ Add" button for as many as you need. Each row
                    is Name / + ports / - ports. Names are optional;
                    unnamed rows are auto-named P1, P2, ... The names
                    "A" and "B" are reserved for the legacy modes.
                    Both port columns take the full range syntax
                    ("5,7" or "5:1:8") -- see the "Input syntax" tab --
                    so a shield tapped at eight ports is one row, not
                    eight. Delete a row with the "X" at its end.
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
One row in the table is enough:

      Name: tank    + ports: 1    - ports: 2

You get the DIFFERENTIAL self impedance of that structure -- the
impedance a differential driver sees across the two terminals, so
L is the differential self-inductance L_diff, the number a VCO tank
actually resonates with.

Contrast that with tying both terminals into one node:

      Name: tank    + ports: 1,2   - ports: (empty)
                                   ^ both on the RED probe

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

The OTHER half of the assumption is what you did with the ports
that are not probes. Everything you did not name is OPEN, and
whether a package's ground balls are ideal grounds, series
inductors or left open moves M by decibels, not percent. The
"Where the number came from" section at the bottom of this tab is
the tool that answers "how much of my M is that assumption?".

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

  On screen the block prints the VERDICT and the number --
  "✓ reciprocal (2.1e-10)", or the alarm with its sentence -- and the
  definition above is what the legend under the report points here
  for. The definition is the same every run; the number is not.

What the block prints, and what it stopped printing
----------------------------------------------------
  * The legend (ind / cap / R<0 / M/L) is printed ONCE per run,
    under the last block, not under every block.
  * With THREE or more measurement ports the Z matrix is drawn as a
    matrix, which is the compact way to show G(G-1)/2 mutual terms.
    With two, it is not: [[Z_aa, Z_ab], [Z_ab, Z_bb]] has every entry
    printed again in the two tables underneath, so the diagonal is a
    "Z (Ω)" column of the self table and the mutual term rides on the
    pair's own line as "Z_ab = ...". No number was dropped -- check
    it against R = Re(Z), L = Im(Z)/ω and M = Im(Z_ab)/ω.
  * "worst M/L" stays on the pair's FIRST line. It is the rank key,
    and with six measurement ports there are 15 pairs to scan.

  See also the "View" note on the Overview tab: `summary` puts every
  pair of every trace in one table, and `compare` puts two traces
  side by side with the change between them.

The pair list is ranked, and its tail is folded away
----------------------------------------------------
Six measurement ports make 15 unordered pairs. In the GUI results
pane they are printed STRONGEST FIRST, ordered by the larger of the
two coupling ratios, max(|M/L_a|, |M/L_b|) -- the budget number, so
the pair at the top is the one to look at. That figure is repeated
in dB on the same line as M and k.

Pairs whose ratio is under -60 dB are folded into one line:

      ... +7 pairs below -60 dB (see Export CSV)

They are still MEASURED and still EXPORTED -- Export CSV writes a
M_nH / k column for every unordered pair, with no floor at all. Two
things are never folded away: the strongest pair (even when it is
itself below -60 dB, so the block always answers "how much coupling
is there"), and any pair whose ratio is undefined, which means a
missing measurement rather than a small one.

The ordering and the -60 dB test are the only places this tool takes
a magnitude -- see the next section.

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
   Measurement ports table:
        Name    + ports    - ports
        tank    1          2
        vco2    3          4
   GND Ports   = (blank -- the file has no ground port)
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


Where the number came from: attribution (advanced)
--------------------------------------------------
Everything above tells you WHAT M is. This section is about WHERE
it came from, and it exists because of a real 6.07 dB argument:
the same two coils, out of the same EM solve, extracted twice,
gave |M| = 1.71 pH and |M| = 3.44 pH. Both runs were correct. What
differed was the grounding assumption -- and nothing on the screen
said so.

`pkg_rlc/physics/attrib.py` answers three questions about ONE frequency of
ONE spec. They are numbered in the order they were built, not the
order you ask them -- Q0 is where a new job starts:

  Q0  COLD START. You have not written a spec yet. WHICH of the
      other 149 ports in this file matter at all? Answered by the
      four-step screen in the next section, and answered from
      ALL PORTS OPEN, because that is the configuration the
      disputed number came out of.
  Q2  ATTRIBUTION. Split the Z_ab you just read into "the bare EM
      coupling" plus one signed term per termination you declared.
      The terms add up to the total EXACTLY -- this is
      superposition, not a linearisation, not a sensitivity
      estimate, not a percentage anyone apportioned by hand.
  Q1  SENSITIVITY. What would Z_ab be if that ground ball were
      open instead? A 50 ohm resistor? A 1 nH lead? Also exact:
      it re-solves the network, it does not extrapolate.

Q2 and Q1 are what the Attribution window shows (Analyze ->
Attribution...). Q0 is CLI-only today.

The algebra, if you want it, is one Woodbury update: terminating a
set of ports is a low-rank change to the file's own admittance, so
the whole answer is a small dense solve on an m x m matrix, where
m is the number of terminations you declared. See docs/theory.md
section 13.


Start here: the cold-start screen (Q0)
---------------------------------------
Everything else on this tab assumes you already wrote a spec. At
the start of a job you have not. You know the victim and the
aggressor and nothing about the other ports, and the all-open
configuration -- the one this tool returns, and the one that
produced the number you are arguing about -- contains no
declarations at all, so the contribution table below is EMPTY by
construction. The cold-start screen is the answer to that, and it
is the first thing to run on an unfamiliar file.

Four steps, in this order. Every one of them is exact (a closed
form checked against a full re-solve to 1.5e-11, not a first-order
slope) and every one measures from ALL PORTS OPEN.

STEP 0  THE BRACKET. The quantity with every non-probe port OPEN,
        against the same quantity with every one of them at IDEAL
        GROUND, and the dB between them. It is first because it
        decides whether the other three steps are worth reading.
        Measured 25.67 dB on a planted 12-port case; a file that
        reads 0 dB has nothing else connected to your two coils
        and you can stop here.
        It brackets the OPEN..IDEAL-GROUND family and NOTHING
        else. It is not a bound over all terminations: a series
        ground inductance resonates with the structure's shunt C
        and can put M outside it (measured on
        diff_pair_4port.s4p, a peak of 9 mH of apparent M at
        L = 505 nH against a 1.01 nH open..ideal bracket). The
        report prints that caveat with the numbers, every time.

STEP 1  THE TWO-COLUMN SCREEN. Every port that is not part of a
        measurement port, with

            |Z_ap|   how strongly it talks to the VICTIM
            |Z_pb|   how strongly the AGGRESSOR talks to it
            delta    the exact effect of grounding it

        ranked by |delta|. The two coupling columns are separate
        ON PURPOSE and must never be read as their product: a port
        has to do BOTH to be a path. Measured on the planted case,
        the port with the LARGEST |Z_ap| in the whole file
        (34.777 ohm, 67% more than the real path's 20.873) has
        |Z_pb| = 0.038 and moves the answer by -0.378 pH, against
        -395.369 pH for the real one. Ranked on coupling to the
        victim alone that port is FIRST and it is worthless;
        ranked on the effect it is fifth of eight. That is the
        whole reason there are two columns and not their product.
        The NEGATIVE result is a result. The list ends with "the
        other N ports are all below X dB", which is what lets you
        say the coupling is local and stop looking.

STEP 2  THE PAIR SCAN over the top K of step 1, again from
        all-open. This is not optional. Measured: a shield brought
        out as two ports reads +9.689 pH with either end grounded
        alone and -870.268 pH with BOTH -- 90x the largest
        single-port effect in the file, with the OPPOSITE SIGN. A
        single-port ranking reports that as two minor positive
        entries and you never look at it again. The mechanism is
        the closed LOOP, not the grounding: "5 short_to 6" with no
        ground anywhere gives the identical -870.268 pH.
        The same step runs the MIRROR direction -- start with
        every candidate GROUNDED and open one at a time -- because
        the two catch opposite failures. Sixty ground balls read
        ~0 each from all-grounded, since the other fifty-nine
        carry the return; that same shield reads +880 pH per end.

STEP 3  THE GREEDY CUMULATIVE CURVE. Ground the best port,
        RE-RANK, ground the next best, and so on, tabulating the
        answer against k. Neither a ranking nor a pair scan tells
        you HOW MANY ports matter; this does, and the report names
        the k at which the curve saturates and the tolerance it
        used for the word. Greedy is not optimal -- the best-k
        subset is combinatorial -- but the re-ranking is what lets
        the walk stumble into the pair effects of step 2.

Port names are a PROPOSAL, never an assumption
-----------------------------------------------
Grouping ports by name family WOULD have caught the shield above,
because the two ends of a guard ring normally share a prefix. But
which ports are one structure is a semantic judgement about your
layout, and this tool will not guess it. So the numbers are
computed both ways and the grouping stays a sentence you accept or
reject:

    ports 5,6 share the name family 'guard_ring'; tested together
    they are -870 pH, tested separately +9.7 pH each -- if they
    are one structure, group them

Nothing in the bracket, the ranking, the pair scan or the curve
changes according to whether the file carries port names at all.

What the cold-start screen cannot find
---------------------------------------
Anything that needs THREE OR MORE ports to move together. Step 1
is one port at a time, step 2 is exactly two, and step 3 can
stumble onto a triple but has no guarantee. A three-terminal
version of the shield above is invisible to every step.

And, as everywhere in this layer: it cannot evaluate NEW METAL.
Every port it considers is one your S-parameter file already has.

Running it
----------
CLI only -- there is no window for the cold-start screen. Same
shape as --attribute, and the two may be given together (the
attribution prints first, because it explains the M printed just
above it):

      python pkg_rlc_extractor.py --cli <file> --mode coupling \
          --mport "dco = 1" --mport "rx = 2" --freq 5.0 \
          --cold-start dco,rx

Note there is no --gnd and no --short in that command. --cold-start
deliberately sets your declarations aside and starts from all-open;
the report names every one it set aside.

  --cold-start VICTIM,AGGRESSOR
      Turn the four-step screen on. Both sides are named exactly
      the way --attribute names them: a measurement-port NAME from
      --mport, or a 1-based position in that list.
  --cold-start-top K
      How many ports of the step-1 ranking enter the pair scan
      (default 8, i.e. 28 pairs). Refused below 2 -- the pair scan
      is not optional.
  --cold-start-cumulative K
      Depth of the greedy curve (default 12; it is always run). 0
      means every candidate, which is the one expensive setting
      here: measured 54.9 s at 151 candidates against 132 ms at
      K = 12.
  --cold-start-csv PATH
      Every record -- the bracket, the UNCAPPED screen, every
      scanned pair whether flagged or not, the whole mirror, the
      curve and the name-family suggestions -- one row each,
      tagged by a "section" column.

Cost, measured on a 153-port package export at one frequency: the
four steps together are 9.5 s, of which 9.3 s is the mirror
direction; at 38 candidates the same four steps are 17.6 ms. What
they replace is one full re-solve per candidate port: 2.41 ms for
the ranking against 2402.6 ms, a factor of 997.


The baseline
------------
The split is always relative to a stated BASELINE, and the
baseline is: probe sides merged, EVERY OTHER PORT OPEN. Nothing
else -- no ground, no short, no lumped element. Each declared
termination is then one element on top of it:

      ground / vdd     one terminal, impedance 0
      lumped_to_gnd    one terminal, impedance from R/L/C
      short_to         two terminals, impedance 0
      lumped_between   two terminals, impedance from R/L/C

Change the baseline and every term changes, even though the total
and the physics do not. That is the same caveat PEEC states about
partial inductances: individually reference-dependent, only
collectively physical. The report names the baseline it used, so
compare two reports only when their baselines match.

Sign convention -- printed on every report and every export
-----------------------------------------------------------
  * The victim reading is V(+) - V(-) of the victim measurement
    port.
  * The aggressor is driven with +1 A into its (+) side and out of
    its (-) side. So every term is signed the way Z_ab = V_a / I_b
    is.
  * An element current I_e > 0 flows OUT of the structure into
    ground for a SHUNT element (ground / vdd / lumped_to_gnd), and
    from the first port to the second for a SERIES element
    (short_to / lumped_between).
  * Flip either measurement port's +/- assignment and every term
    flips together. RELATIVE signs between terms are physical;
    absolute signs are a labelling choice.

Three caveats, and none of them is a footnote
---------------------------------------------
1. IT IS BLIND TO OPEN PORTS. An open port contributes no element
   and therefore no term -- it is absent from the table, not small
   in it. So the contribution table is NOT a ranking of ports; it
   is a ranking of the DECLARATIONS in your spec. The 45 open
   ports of a package file are simply not in it. Ports you have
   not decided about yet are covered only by the sensitivity side,
   and it covers them by hypothesising a termination, not by
   measuring one.

2. THE SPLIT DEPENDS ON HOW THE SPEC IS SPELLED. These two are the
   same network:

        3 ground                3 short_to 4
        4 ground                3 ground

   Same total M, to the last digit that means anything. Different
   split. Measured on tests/fixtures/diff_pair_4port.s4p at 5 GHz
   with "agg = 1", "vic = 2":

        3 ground / 4 ground         3 short_to 4 / 3 ground
          bare EM      251 pH         bare EM      251 pH
          ground 3     252 pH         ground 3     253 pH
          ground 4     506 pH         short 3-4    506 pH
          total       1.01 nH         total       1.01 nH

   Both are right. Two ways of describing one network are two
   different tearings of it, and they answer different questions.
   Reorganise your connection table for readability and the
   contribution column can move -- that is not a defect.

3. MOST OF THE RETURN CURRENT CAN BE INSIDE THE EM MODEL. The
   reference plane in your EM solve is not a port, so no
   declaration of yours reaches it. Every report therefore prints
   a return-path budget: how much of the aggressor's return
   current went through your declared elements versus through the
   model's own reference. On a representative package case that
   split was 0.05% declared / 99.95% inside the model, and when
   the model dominates the report says in plain words that the
   decomposition CANNOT separate the return path. Do not read a
   "forward path minus return path" conclusion out of small
   numbers in the table.

   A fourth boundary, while you are here: re-terminating existing
   ports cannot evaluate NEW METAL. A shield, an extra via, a
   widened return path -- none of those is a termination of a port
   that already exists. They change the EM answer itself and need
   a new solve. Sensitivity output looks like a layout-exploration
   tool and is not one.

What can and cannot be split per term
-------------------------------------
A quantity splits if it is a fixed real scalar times something
R-LINEAR in Z_ab, read at one configuration:

      YES:  Z_ab, Re Z_ab, Im Z_ab, M, M/L_a, k
      NO:   C_c, Q, |Z|, anything in dB

C_c = -1/(omega*Im Z_ab) is a RECIPROCAL: superposition adds
impedances, not their inverses, so the per-element terms of C_c do
not sum to C_c. C_c is still reported as a TOTAL -- it is the
right reading whenever Im(Z_ab) < 0 -- just never per term. Q is a
ratio of two decomposable things; |Z| is a norm; dB is a logarithm
of a magnitude and has no sign. Ask for one of those per term and
the tool refuses BY NAME and tells you which linear quantity to
decompose instead.

Where to run it: the Attribution window
----------------------------------------
Analyze -> Attribution..., or right-click the trace in the Traces
list. It decomposes the SELECTED trace, so calculate that trace
first. It is a normal window and not a dialog: it stays open while
you edit, it keeps its own taskbar button, and you can park it on
a second monitor. Several can be open at once, on different traces
or different pairs; asking again for a pair that is already open
raises that window instead of making a second copy of it.

It refuses to open on a trace it cannot honestly describe, and it
says which of these is in the way:

   * the trace is a FROZEN snapshot -- its numbers came from an
     earlier run and can never be recalculated, so anything
     computed now could only be stamped with the CURRENT run;
   * the trace is STALE, i.e. edited since the last Calculate --
     the numbers and the spec beside them no longer describe each
     other;
   * it has no numbers yet, or its Touchstone file is not loaded;
   * it has only ONE measurement port. Z_ab is a MUTUAL impedance;
     there has to be a victim AND an aggressor.

The menu entry stays LIVE in all four cases on purpose. A greyed
entry cannot tell you why.

What is on the window, top to bottom:

  HEADER        trace / victim / aggressor / quantity / frequency
                / [Recompute]. Change any of the first five and
                press Recompute; nothing recomputes on its own,
                and that is deliberate (see below). The row wraps
                onto a second line at narrow widths rather than
                pushing [Recompute] off the end.
  BANNER        where these numbers came from -- "from run #7 @
                5.600 GHz". It turns into a warning the moment you
                edit the spec in the main window, which is what
                makes the button honest.
  SIGNS         the convention, stated once, above any signed
                number. A negative term OPPOSES the total and the
                terms sum to it exactly; shares are of the SIGNED
                total, so they can exceed 100% and go negative
                wherever terms cancel. The line clips at narrow
                widths -- the full declaration, including the
                ground model, is in Copy report and in the CSV.
  RECONCILE     the cross-check, in the header rather than the
                footer because it gates trust in everything under
                it: "reconciled  rel diff 3.1e-13 (floor
                4.3e-10)". If it ever fails, the per-element SPLIT
                is withheld and the TOTAL is still shown -- the
                total is compute_z_matrix's and is not in doubt.
  ACROSS FREQ   one line, and its OFF state carries the action. A
                ranking read off one frequency is a statement
                about that frequency, so the badge does not just
                say "not checked": it says what checking would
                COST on this file and does it in one click.
                Checked, it says what MOVED -- which elements
                changed rank, and at which frequency -- or, if
                nothing did, that the ranking is STABLE across the
                band, in those words. A stable ranking is a
                result, not an absence.
                The cost is one extra solve per frequency, at five
                frequencies including the one you are reading.
                Measured: 0.45 ms per point on the 4-port fixture
                below, and 223 ms per point on a synthetic dense
                153-port network -- so under 2 ms on a small file
                and roughly 0.9 s on a package export. That is why
                it is a click and not automatic. It is a ONE-SHOT:
                once it has run, the verdict beside it IS the
                answer and the button greys out. [Recompute]
                makes it live again, because that is a new
                decomposition.
  GROUND MODEL  how the declared shunt leads are modelled, in the
                same spelling the CLI uses: "diag" (the default --
                exactly as declared), "diag:L=1n" (every shunt
                lead gets that impedance on its OWN independent
                lead) or "shared:L=1n" (every lead keeps what it
                declares and they ALSO share that impedance back
                to the reference). One line beside the field says
                why the default is not obviously right:
                independent leads understate the shared return.
                Changing it takes effect on [Recompute], like
                every other input here -- it never re-decomposes
                on its own. See "One more thing this changes about
                your GND field" at the bottom of this tab.
                If the spec has no shunt lead to model at all --
                every ground expressed as "short_to", say -- the
                model CANNOT be applied, and the window says so
                rather than leaving you to read an unchanged
                number as "the shared return is worth 0 dB": the
                sign strip reads "NOT APPLIED", the line beside
                the field says why, and both exports carry it.
  TABLE         (o) Contributions   ( ) Sensitivity -- one pane,
                two views, not two tabs. Rows are coloured by
                element KIND, in the same palette as the Ports &
                Roles window. Never by sign: red means "warning"
                everywhere else in this tool, and a red negative
                would make a correct answer look like a fault.
                Click a row to drill into it.
  DETAIL        for the selected row: its element current, its
                transimpedance to the victim, what it would be
                worth as each candidate in the Candidates box
                (open, ideal, or R=/L=/C= -- the tool will not
                guess a package value for you), and the
                closed-form sweep of that one element plotted
                beside it, with both asymptotes and the current
                spec marked. Non-monotonicity is labelled where
                the sweep finds it, and so is any pole -- see
                "Reading the sweep plot" below. The pane is PROSE
                and it wraps; it never scrolls sideways, and the
                split above it opens sized to the table's own row
                count rather than to a fixed fraction. Drag the
                sash and it is yours until you close the window.
  FOOTER        Copy report / Export CSV... / Close. Both exports
                carry the full provenance: run number, the
                frequency and whether it was snapped to the file's
                grid, the whole sign convention, the ground model,
                and the termination spec verbatim.

Why a [Recompute] button and not an automatic refresh: the numbers
a trace carries are written by Calculate and by nothing else.
Editing the spec marks the trace stale and leaves those numbers at
the PREVIOUS run's. A window that re-decomposed on every keystroke
would therefore be checking a NEW spec against an OLD total, would
find them disagreeing by however much you just typed, and -- by
the reconciliation rule above -- would blank its own table. It
would erase itself while you type. So an edit moves the banner and
nothing else, and you press the button when you mean it.

The shared-return ground model is on the window, and it is worth
reading the last section of this tab before you leave it on the
default. One thing about it is unlike every other input here: the
dense (shared) model is a network that CANNOT be written as a
termination spec -- a shared return is a mutual impedance BETWEEN
two ground leads, and the connection table has no node to hang one
on -- so compute_z_matrix has never been asked about it and there
is no second opinion on its total. What is still checked is the
ARITHMETIC: the reconciliation line is always of the spec AS
DECLARED through the same machinery, so choosing a model does not
quietly cost you the cross-check, and the sign strip and both
exports name the model the numbers came out of rather than letting
you assume one.

Save Config remembers WHICH pair you were reading -- the victim,
the aggressor, the quantity, the frequency, the view and the
Candidates field -- but it does not reopen the window on Load. A
config carries the setup and never the results, so a just-loaded
trace has no numbers yet and the window could only open on its own
refusal. The Results pane names each entry it did not reopen;
Calculate, then Analyze -> Attribution..., and you land back where
you were.

Reading the sweep plot, and its pole
-------------------------------------
Click a row and the detail pane sweeps THAT element's series
inductance from ideal (L = 0) to open (L = infinity), in closed
form -- no loop, no sampling. Two numbers on it are the ones you
came for:

      M(0)     that termination made IDEAL
      M(inf)   that termination NOT THERE at all

They are exact, and the plot's y limits are set FROM them (plus a
margin), so the two readings you are comparing are always on
screen at a readable size.

Between them the curve may have ONE pole, and on a package it
usually does. A pole is not a numerical artefact and it is not
hidden: it is drawn as a labelled vertical line at the L where it
sits, with the value of the element there, because it is a real
physical event. The L you are adding RESONATES with the reactance
the network itself presents at that node. Measured on the fixture
below: the network looks like 2.005 fF at ground ball 3, and
505.25 nH series-resonates with 2.005 fF at exactly the 5 GHz
being read (5.0005 GHz, to five digits). There the termination is
anti-resonant, the curve runs off the top of the plot -- which is
what the label says -- and M passes through +-10.28 mH, ten
million times the [504 pH, 1.01 nH] the two endpoints span.

So the HEADLINE interval is the one over the POLE-FREE part of the
sweep, and the pole is stated separately, in words:

   "M lies in [-2.5 pH, 1.52 nH] over any ground inductance more
    than a factor of two away from the 505 nH resonance"
                                      ... is a budget statement.
   "M lies in [-10.3 mH, +10.3 mH]"
                     ... is the tool reading its own arithmetic
                         back to you. Same curve, same numbers.

The y axis is SYMLOG -- linear in a band around zero, logarithmic
outside it -- because M crosses zero here and a log axis cannot
draw that. The width of the linear band comes from the DATA, not
from a constant: this tool's plot panel uses 1e-6 for R/L/C, and
1 uH is a thousand times the whole curve above, so a fixed 1e-6
would put every point inside the linear band and symlog would
degenerate into the linear axis it exists to replace.

Both axes read in ENGINEERING UNITS -- "500 pH", not "5.0" under
a "1e-10" parked in the corner -- the same formatting the table,
the caption and the main plot's cursor readout use.

A sweep that does not move at all (M(0) == M(infinity)) gets an
axis the size of ITS OWN VALUE. That happens on real files --
decap_4port.s4p reads a flat -506.755 nH -- and an axis a
hundred thousand times the number on it is the same
uninformative picture from the other direction.

If the swept range holds no pole, none of this appears and the
plot is exactly what it was before.

Where to run it: the CLI
-------------------------
Add "--attribute VICTIM,AGGRESSOR" to any "--mode coupling" run,
where both sides are measurement-port NAMES as you gave them to
--mport. Everything below came out of that.

      python pkg_rlc_extractor.py --cli <file> --mode coupling \
          --mport "agg = 1" --mport "vic = 2" --gnd "3,4" \
          --freq 5.0 --attribute vic,agg

Five more flags, all inert without --attribute:

  --attribute-alt SPEC
      A candidate termination for the sensitivity scan;
      repeatable. "open", "ideal", or a series R/L/C in the same
      spelling the connections table uses -- R=50, L=0.3n,
      "R=0.5,L=1n", C=100p. A comma or a space separates the
      fields of ONE candidate, so "R=0.5,L=1n" and "R=0.5 L=1n"
      are the same thing here and in the Attribution window's
      Candidates field; there is no space inside a VALUE, and
      "R=5 m" is refused because it would silently mean 5 ohm.
      With none given the scan is limited
      to "open" and "ideal", the two STRUCTURAL candidates that
      need no judgement about your package. The tool will not
      guess your ball's lead inductance for you.
  --attribute-ground-model MODEL
      "diag" (default, exactly as declared), "diag:SPEC" (each
      shunt lead gets SPEC on its own INDEPENDENT lead) or
      "shared:SPEC" (they all ALSO share SPEC back to the
      reference). See the last section on this tab -- the choice
      is worth 6 to 10 dB.
  --attribute-freqs 1,5,10
      Re-rank the contributions at each of these too, so a
      ranking read off one frequency can be checked against the
      band.
  --attribute-group row | flat | name
      How elements are grouped for the joint-effect section.
      "row" (default) groups by the flag that declared the port.
      "name" groups by port names with the trailing index
      stripped -- a NAMING HEURISTIC, and the report says so.
  --attribute-csv PATH
      Every record, one row each, tagged by a "section" column.
      The terminal caps some tables for readability; the CSV
      does not.

Worked example, from a fixture in this repo
-------------------------------------------
tests/fixtures/diff_pair_4port.s4p is two coupled lines: port 1
(in_p) runs to port 3 (out_p), port 2 (in_n) runs to port 4
(out_n). Drive line one, listen on line two, with both far ends
grounded:

      agg = 1     vic = 2     GND = 3,4     f = 5 GHz

That is exactly the command above, and section 1 of its report
reads:

   group        element            |I_e|   M term   share   quad
   -----------  ----------------  ------  -------  ------  -----
   --gnd 3,4    ground port 3        1 A   252 pH  25.00%  -0.00%
   --gnd 3,4    ground port 4     997 uA   506 pH  50.12%   0.00%
   (baseline)   bare EM coupling      --   251 pH  24.88%  -0.00%

with the reconciliation in section 2:

   Z_ab total (compute_z_matrix) : -2.49471e-09 + j31.7316 ohm
   Z_ab total (sum of the terms) : -2.49469e-09 + j31.7316 ohm
   M    total                    : 1.01 nH
   residual 6.42e-13 relative, floor 3.62e-09

How to read that:

  * THREE QUARTERS OF THE ANSWER IS THE GROUNDING, not the metal.
    Open both grounds and M falls from 1.01 nH to the 251 pH bare
    term. If you were arguing about a 6 dB discrepancy, this is
    where it lives.
  * The two ground balls are NOT worth the same. Port 4 is the far
    end of the VICTIM's own line and contributes 506 pH; port 3 is
    the far end of the aggressor's and contributes 252 pH. That
    asymmetry is physical and is exactly what the table is for.
  * "share" is a signed PROJECTION onto the total,
    Re(term * conj(total)) / |total|^2 -- not a complex ratio and
    not a magnitude ratio. "quad" is the part at 90 degrees to the
    total, which inflates any magnitude-based cancellation measure
    while being harmless. Here quad is 0.00% because everything is
    in phase. The share column is suppressed outright, with a
    named reason, when the total is near zero: shares of a number
    that is pure cancellation mean nothing.
  * The residual is the cross-check. The AUTHORITATIVE total is
    always compute_z_matrix's; the sum of the terms is the check
    on it. 6.42e-13 against a floor of 3.62e-09 means the two
    algorithms agree far better than they had to. The floor is
    condition-aware, not fixed: a well-conditioned 4-port reaches
    1e-13 and a 153-port package cannot beat ~1e-7, so a fixed
    gate would refuse exactly the files this exists for. If the
    residual is ever catastrophic the per-element split is
    withheld -- the totals never are.

Now ask the other question. What if those grounds were not ideal?

  * ONE AT A TIME. Opening ground 3 alone: -506 pH. Opening
    ground 4 alone: -506 pH.
  * BOTH AT ONCE: -759 pH, not -1.01 nH. The non-additivity is
    +254 pH -- a THIRD of the effect, out of only two elements.
    With sixty ground balls, every single-ball delta is nearly
    zero because the other fifty-nine already carry the return,
    and so is every pairwise second difference: the effect is
    order-sixty. That is why there is a group-level and a
    cumulative answer and not just a one-at-a-time list.
  * THE WHOLE RANGE, in closed form. Z_ab as a function of one
    element's impedance z is a Mobius map (alpha + beta*z) /
    (gamma + delta*z), so the endpoints, the interval and the
    extremum are analytic -- no loop, no sampling. Sweeping
    ground 3's series inductance on this fixture:
        ideal (L = 0)         1.01 nH
        open  (L = inf)      503.7 pH
        pole at L = 505.25 nH -- the added L resonating with the
              2.005 fF the network presents at that ball, at the
              5 GHz being read
        over all L >= 0:     [-10.28 mH, +10.28 mH]   <- the pole
        away from the pole:  [-2.5 pH, 1.52 nH]       <- the answer
    Two things to take from that. The +-10.28 mH is arithmetic,
    not structure: it is the curve on its way past the pole, ten
    million times the endpoint bracket, and no component you can
    buy sits there. And the pole-free range is STILL wider than
    the two endpoints -- a series L resonates with the structure's
    shunt C, so [ideal, open] is not a bound even where nothing is
    anti-resonant. The tool labels the pole and headlines the
    pole-free interval instead of quoting a bracket that does not
    hold.

One more thing this changes about your GND field
------------------------------------------------
This is the most expensive modelling choice in the whole flow and
it is the easy one to make by accident, because the obvious
spelling is the wrong one.

A ground field written as N independent lumped_to_gnd inductors
says the balls have N independent return paths. Real package
ground balls SHARE A RETURN PLANE. N independent z in parallel is
z/N; N balls sharing one z is z -- so the independent spelling
understates the common-mode return inductance by roughly

      (1 + (N-1)*k_ret)

where k_ret is how strongly the return paths couple to each other.
That factor is why "independent" is not a conservative default: at
20 balls with a realistic k_ret = 0.2 it is 4.8x, which is 13.6 dB
of M, and it GROWS with the ball count -- the bigger and better
your ground field, the more the independent spelling flatters it.

Measured three times, on three different networks: 9.60 dB on a
four-ball cluster (four leads at 1 nH each, independently, against
the same four tied through ONE shared 1 nH), 8.09 dB on an
independently built 6-node 4-ball cluster, and 6.03 dB on
diff_pair_4port.s4p above. Every one of them is bigger than the
6.07 dB argument this whole layer exists to settle. Monotone in
k_ret, no threshold, so there is no safe default and the tool
refuses to pick one for you.

In the window it is the GROUND MODEL field: "diag" as declared,
"diag:L=1n" for independent leads, "shared:L=1n" for a shared
return. On the CLI it is the same spelling in one flag:
--attribute-ground-model diag:L=1n versus shared:L=1n. Either way
run it BOTH ways -- diag and shared are not a refinement of each
other, they are different answers, and the report says so.

You can also spell it right here in Mode 5, with no attribution
code at all -- and if you take one thing from this section, take
this one. Tie the whole ground set with ONE short row, then hang
ONE lumped_to_gnd on that node.

      independent:  3 lumped_to_gnd L=1n
                    4 lumped_to_gnd L=1n        M = 1.0120 nH

      shared:       3,4 short as pkg_gnd
                    pkg_gnd lumped_to_gnd L=1n  M = 2.0259 nH

Same file, same probes, same frequency: 6.03 dB apart. (It does
not matter WHICH port of the set carries the inductor -- by then
they are one node, so "3 lumped_to_gnd L=1n" is bit-identical, and
so is the older two-field spelling "3 short_to 4". The name simply
saves you having to pick a port and remember why.) What you must
not write is "3,4 lumped_to_gnd L=1n" after the short: once they
are one node that is two 1 nH leads in PARALLEL on it, i.e. a
500 pH shared return, which is neither of the two models above.
The validation strip refuses it and prints both numbers. Which
model is right is a question about your package, not about this
tool -- but you should answer it on purpose.


===========================================================
Hanging an EM block on a PACKAGE block (--compose, CLI only)
===========================================================
Two files -- your EM extraction and the package -- measured as ONE
network. There is no window for it yet; the command line is:

      python pkg_rlc_extractor.py --cli coil.s2p \
          --compose-alias EM --compose "PKG=package.s3p" \
          --compose-link "EM.2 short_to PKG.1" \
          --compose-link "EM.1 lumped_between PKG.3 L=0.3n" \
          --mode gnd --porta "EM.1" --gnd "PKG.2" --freq 5

Every port carries its file's tag, and the separator is a DOT.
Not a colon: ":" is already start:step:stop in every port field
in this tool, so "PKG:12" is a parse error today. A port with no
tag belongs to the file you named positionally, so a one-file
command line still reads exactly as it always did.

The mathematics is the pipeline you already know -- the two Y
matrices are stacked, each cross-file wire is an ordinary short or
lumped element, and the whole thing goes to the same solver. Every
mode, the Mode 5 DSL, Mode 6 coupling, the attribution and the
cold-start screen all work on a composition with nothing added.

ONE THING YOU MUST KNOW BEFORE TRUSTING THE NUMBER
--------------------------------------------------
Stacking two files WELDS their reference nodes together at zero
impedance. An n-port Y already has its own reference eliminated,
so there is no way to keep the two apart after the fact.

If your EM file's return current uses the EM model's own reference
-- the ordinary on-die convention -- then in the combined network
that current is already at the package's reference the moment it
leaves the die, and the package's WHOLE ground network is bypassed.
Measured on a 2 nH coil, a 100 pH package trace and a 100 pH
package ground lead:

    die return brought out as a PORT   2.2501 nH, and it MOVES
                                       when the ground path changes

    die return IS the EM reference:
      package ground pad grounded      2.1454 nH
      package ground pad open          2.1454 nH
      package ground pad through 1 nH  2.1454 nH   <- identical

Bit-identical, spread 0.000e+00. Nothing raises and the number
looks perfectly reasonable. So the tool perturbs each file's
declared ground set with a series inductor and re-solves; a change
of EXACTLY zero means welded, and it says so by name. That check
is mandatory output -- there is no flag to turn it off.

Composition answers your question only when the EM file brings the
return path out AS A PORT. That is a precondition, not a warning.

FREQUENCY GRIDS
---------------
The spans are intersected and extrapolation is refused; the report
says how many points were dropped. S is interpolated, never Y or Z
(S is bounded at every real frequency; Y blows up at a series
resonance and Z at a parallel one). Z0 is NOT renormalised because
it does not need to be -- Y does not depend on it, measured to
1.049e-17. What interpolation does break is PHASE: a 1 ns delay
across a 100 MHz step is 36 degrees of rotation, which the chord
turns into 0.436 dB of insertion loss that is not there. That is
warned past 20 degrees and refused past 60.

WHICH PACKAGE PIN COSTS YOU THE dB
-----------------------------------
--attribute and --cold-start work here, with one change you should
know about: the cross-file links go INTO the attribution baseline.
Otherwise all-open leaves the two files as disconnected islands
and every package-only element contributes EXACTLY 0 while the
reconciliation residual still reads healthy -- a confident,
perfectly reconciled wrong answer. The baseline for a composed
network is therefore "the files CONNECTED, everything else open",
the report names that gauge, and it cannot be switched off. Two
attribution reports are comparable only when their baselines match.

BEFORE AND AFTER, WITHOUT REBUILDING ANYTHING
----------------------------------------------
"What did the package cost me?" is already a gesture you have:
Calculate the bare EM trace, right-click it in the Traces list ->
Freeze as new trace, then add the package and Calculate again. The
two rows sit side by side in the results table, and the frozen one
can never be recalculated or edited by accident.
