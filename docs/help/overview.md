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
  6. Answers WHERE a coupling number came from, which is a separate
     question from what it is. Analyze -> Attribution... splits an
     extracted Z_ab into the bare EM coupling plus one signed term
     per termination you declared, and says what it would be with
     any of them changed. On the CLI, --cold-start goes the other
     way and ranks the ports you have NOT declared anything for.
     Both are on the "Mode 6 (Coupling)" tab, under "Where the
     number came from".

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
   ID      trace id (in brackets), matches the Traces list. Every
           row here is a trace that is ON the plot: hiding one takes
           it out of this table as well, and names it on one line
           underneath instead -- see "Showing and hiding curves".
   Label   user-given trace label (truncated)
   File    only shown when traces span >1 file (alias F1, F2, ...)
   Ports   compact port-config descriptor:
              M1: S:[1] G:[2,3]              -- Mode 1 (port-to-GND)
              M2: 1<->2 G:[]                 -- Mode 2 (port-to-port)
              M2: 1<->{2,3} G:[4]            -- multi-port terminal
              M3: 1<->2 G:[] S:[3-4]         -- Mode 3 with shorts
              M5: tank:1/2 C:3               -- Mode 5 (custom):
                                                measurement ports, then
                                                the connection-row count
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

View: what the report LOOKS like (the dropdown beside Units)
-------------------------------------------------------------
One run, three renderings. Nothing is recomputed and no numbers
change; every page is repainted in place and no new run is created.

   detail   -- everything, one block per trace. What the tool has
               always printed, and the default.
   summary  -- the whole run as two tables: one row per measurement
               port, then one row per coupling pair. Comparing two
               traces becomes reading down a column instead of paging
               between blocks.
   compare  -- the traces become COLUMNS, one quantity per row, with
               a change column when there are exactly two of them:

                  compare @ 5.55 GHz    [1] before   [4] after       Δ
                  VCO      R                9.92 Ω     9.81 Ω  -1.13 %
                           L               3.23 nH    3.23 nH  +0.09 %
                  VCO x RX M               -516 fH   -7.19 pH  +13.9 ×
                           worst M/L     -68.77 dB  -52.36 dB  +16.4 dB

               A big change is shown as a FACTOR rather than a
               percentage (-1293% is not a sentence anybody says out
               loud), and a dB quantity gets a dB DIFFERENCE, because
               dB is already a ratio. A trace that does not have a
               port or pair at all leaves an EMPTY cell -- "this trace
               has no RX" and "RX measured zero" are different
               statements.

               With one trace on the plot there is nothing to compare,
               so it says so and shows the summary instead. With three
               or more there is no Δ column: a change against
               "whichever trace happened to be first" is a reference
               chosen in silence.

The choice is saved with the session, like the units mode.

Digits: how precisely a value is printed (the dropdown beside Units)
--------------------------------------------------------------------
   default  -- what the tool has always printed: 3 significant
               digits in the smart units mode, 4 in aligned and for
               the dimensionless cells (Q, k, dB). This is the
               startup setting and choosing it again is the way back
               to it.
   3 .. 8   -- that many significant digits everywhere: the table,
               the coupling blocks, all three views, the Z matrix
               and the plot's cursor readout, all at once.

Use it when two variants read the same at three digits and differ in
the fourth -- two EM revisions of one coil both printing "2.01 nH"
is the case this exists for. Nothing is recomputed and no number
changes; the columns widen to fit and every open run page and the
cursor readout are repainted together, so one screen never shows two
precisions.

The ceiling is 8 on purpose. Every value here comes out of a Schur
reduction and a pseudo-inverse, so digits past the eighth are the
arithmetic's rather than the measurement's -- and each one widens
the readout box on the plot, which has only its own subplot to sit
in. Export CSV is unaffected either way: it has always written full
precision.

The choice is saved with the session, like the units mode and the
view.

Clearing the window
-------------------
Three gestures, and what they leave behind is what tells them apart.

   * Right-click the Loaded Files list -> "Clear all files".
     Every file goes, and so do the traces bound to them: a trace
     whose file is gone cannot be computed at all. A trace naming a
     file that never loaded (a config whose data has moved) stays,
     so its spec is not lost. The run pages stay.
   * Right-click the Traces list -> "Clear all traces". The FILES
     STAY. This is the one to use for trying a second port map on a
     large package file without re-parsing it.
   * "File -> Clear All". Files, traces, run pages -- kept pages
     included -- and the Log. What it does not touch is how you have
     set the tool up: the view, the units, the digits, the fit
     settings and the marker frequency all stay as they are.

Each asks first, names what will go, and does nothing at all on an
empty window. None of them can be undone: a spec is not recoverable
by retyping it in a hurry, and "Save Config..." is what makes one
recoverable at all.

Editing traces: there is no "Apply"
-----------------------------------
Whatever is in the editor IS what the selected trace holds. Type a
port number and the trace has it; the Traces list updates as you
type, and clicking another trace keeps the edit you just made rather
than discarding it.

A trailing "*" on a line in the Traces list means that trace's spec
has changed since the curve on screen was computed -- the numbers
you are looking at are older than the setup that describes them.
Calculate clears it.

"Calculate This Trace", at the bottom of the editor, recomputes only
the selected trace. With several traces over a large package file
that is the difference between iterating on one port spec and
re-reducing everything on every pass. The results table still shows
every trace that is on the plot; only the WORK is narrowed.

Choosing a colour and a line style
----------------------------------
Click the line preview next to "Style:". It expands into the 12
colours and the 4 line styles, each drawn the way the plot will draw
it. Click a colour or a style to take it; click the preview again to
fold the palette away. Tab reaches the preview and Enter, Space or
Down opens it.

A coupling trace is not one curve, so the preview does not pretend
it is: with G measurement ports it draws G self curves plus one
mutual curve per pair, each taking the NEXT colour in the palette.
The preview shows the run of colours the trace will occupy and "xN"
for how many. Two traces whose runs overlap will share colours --
that strip is how to see it before you plot.

Showing and hiding curves
-------------------------
Every line in the Traces list starts with a checkbox: [x] drawn,
[ ] hidden (hidden lines are also greyed). Three ways to toggle the
selected trace:

   * the "Show/Hide" button above the list
   * the space bar, with the list focused
   * "Plot: this trace" in the editor

Hiding takes effect immediately. Nothing is recomputed -- the curve
is rebuilt from the numbers already in memory -- and the V-line
cursors you have placed stay where they are. This is how to compare
two traces out of five without deleting the other three and typing
them in again.

The checkbox governs every output, not just the picture: a hidden
trace also leaves the results table and Export CSV. The table reads
as "what is on the plot", and a row for a curve that is not drawn
looks like a duplicate of the one that is -- in a CSV that same row
is simply one step further from where you would notice it.

It is still MEASURED, and it is named on one line under the table:

   hidden (measured, not plotted, not exported; show it to read or
   export it): [2] t2

Its numbers stay in memory, so showing it again costs no Calculate
-- tick it back on and export again to get it into a file.

Before and after: freezing a trace
-----------------------------------
Right-click a trace in the Traces list -> "Freeze as new trace".
That takes a SNAPSHOT: a second trace holding exactly the numbers
this one has now, drawn in the next colour and the next line style,
labelled with the time it was taken ("tank <14:32>").

Then change the original however you like and press Calculate. The
snapshot does not move -- Calculate skips it and the editor refuses
to write into it, so the two curves on the plot are genuinely the
before and the after, over the whole sweep rather than at the marker
frequency alone. Both are in the results table, both are in the
cursor readout, both go into Export CSV.

A frozen trace is marked with a snowflake in the Traces list, and
selecting it greys the editor out with a note saying so. Everything
else still works: show/hide, Remove, colour (via unfreeze), export.
Right-click -> "Unfreeze" gives it back to Calculate -- the next run
REPLACES the numbers it was holding.

One limit, and it is deliberate: a config file carries the setup and
never the results (see the "Save / Load" tab), so a frozen trace
comes back from Save/Load with its spec and no numbers. It says so
in the Results pane and reads "no numbers" in the Traces list rather
than quietly drawing nothing. Unfreeze it and Calculate to measure
it again -- which reproduces the snapshot exactly if the file has
not changed in the meantime.

Run history: every Calculate keeps its page
--------------------------------------------
The Results pane is a set of tabs. "Log" is the running commentary
it has always been; every Calculate adds a page beside it, newest
first, labelled "#7 10:42".

Each page carries the report that run produced, and above it:

   Run #12 · 14:32:07 · @ 5.000 GHz · 4 traces [1,2,3,5]
   changed since #11:  [3] gnd 6-14 -> 6-16

The second line is the useful one -- twenty runs are all at 5 GHz
and nobody remembers what they were doing at 14:32, but "I widened
the ground group" is what tells two pages apart.

Old pages are dropped automatically, OLDEST FIRST, three at a time
by default. To stop one being dropped, press "Keep" (or right-click
its tab). A kept page is never dropped by anything automatic; the
only way it goes is right-click -> "Close this run". Because kept
pages have their own budget, pressing Calculate can never be blocked
by them and can never throw one away -- if the kept set is full the
Keep button is already disabled and says so.

Runs ▾ lists every page with its full description; that is where to
look once the tabs are too narrow to read. It also sets the two
limits (how many automatic pages, and how many tabs in total).

One thing to watch, and every older page says it out loud:

   ! the plot and Export CSV show run #12, not this page

The plot and Export CSV always show the LATEST numbers. Reading an
older page does not change what a Calculate or an export produces.
To compare curves rather than tables, freeze a trace (above) --
that is the tool for two curves on one plot.

Run history is in memory only: Save Config carries the setup, never
the results, so run pages do not survive a save/load.

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
