Reading Touchstone files
========================

What "Add File..." prints
-------------------------
Every load writes a block like this to the Results pane:

    top_pkg.s45p
      45 ports, 2001 points, Z0 = 50Ohm, read as '# GHZ S RI R 50'
      Frequency: 0 Hz - 40 GHz  (linear, step 20 MHz)
      max |S| = 0.998

Check the frequency span against what you actually simulated -- it is
the fastest way to notice you loaded last week's file. The span also
appears on the file's line in the Files list.

  read as    the option line the tool USED. If the file had none, or
             had tokens the tool did not recognise, this says what was
             assumed and a WARN line says why.
  max |S|    a passive structure cannot exceed 1. A value well above 1
             usually means the option line's format (RI / MA / DB) does
             not match how the numbers were really written -- a file
             that parses perfectly and is completely wrong.

Two kinds of extra line can follow:
  WARN:      something was guessed or thrown away. Read it.
  Note:      the file is fine, but there is something you should know
             before reading the numbers -- for example a sweep that
             starts at DC, where L = Im(Z)/omega and C = -1/(omega*Im(Z))
             are undefined and will read as nan/inf. Pick any other
             frequency for the R/L/C extraction.

When a file will not load
-------------------------
The error dialog always names a VERDICT, because "is my file bad or is
your tool bad?" is the first thing you need to know:

  THE FILE is inconsistent      truncated, corrupt, or not Touchstone.
                                Everything before the named line was
                                read correctly.
  THE FILE looks valid, but...  a real format this tool does not read:
                                Touchstone 2.0 ([Version] / [Network
                                Data] keyword lines), or a compressed
                                file. Re-export as .sNp / decompress.
  THE FILE could not be read    missing, locked, or too big for memory.
  THE PARSER gave up            the file's structure checks out, so
                                this is a bug in this tool. The report
                                carries a traceback -- please send it.

Where a line number can be given, it is, together with the text of that
line. A file truncated mid-record says which line the incomplete record
starts on, not just "token count not divisible".

If the only problem is a stray non-numeric token, the dialog offers to
load the file anyway, skipping it. Treat the result as suspect: a
Touchstone file is a positional stream of numbers, so dropping one
shifts every number after it by one slot. That is exactly why skipping
is not the default.

"Check File"
------------
Runs the same structure report on demand and prints it to the Results
pane: size, encoding, line counts, the option line, how many numbers
each data line carries, and whether the data divides into whole records
for every plausible port count. It ends in the same VERDICT.

Use it when the file LOADS but the numbers look wrong -- that is the
case an error dialog can never cover. It answers: was the port count
guessed? is the sweep what I simulated? does the record grid line up?

With a file selected in the Files list it checks that file; with nothing
selected it asks for one, so it also reaches files that fail to load.
On the command line the same report is
    python pkg_rlc_extractor.py --diagnose myfile.s4p
which exits 0 when nothing is wrong and 1 otherwise.

Port count detection
--------------------
The port count comes from the CONTENT, not the extension -- EDA tools
rename these files constantly, so a .txt or .dat holding a 4-port sweep
loads fine. The name is used for three things only:
  * to break a tie when the numbers admit several port counts (picking
    the smallest silently reads a 2-port file as a 1-port one);
  * when nothing up to 256 ports fits, which is the one case content
    alone cannot resolve -- a .s300p package export;
  * when the file's frequencies are written OUT OF ORDER (below).
All three say so in a WARN line. --force-nports overrides everything.

Sweeps written out of order
---------------------------
An adaptive or discrete sweep is often exported in the order the solver
computed it -- the two endpoints first, then the midpoint, and so on --
rather than in frequency order. Such a file is perfectly good; its
frequency column simply does not increase.

Content sniffing looks for a port count whose records start with an
INCREASING frequency column, so on its own it finds none. When the file
NAME already says the port count, the numbers divide into whole records
at that count, and the leading column is a set of distinct non-negative
values, the file is read at that port count and the points are SORTED on
load. Nothing is changed and nothing is dropped; two records at the same
frequency keep the order the file wrote them in. Two WARN lines say what
happened, and the frequency line reads "reordered by frequency".

Check the port count on the numbers you get. The same symptom -- a
frequency column that jumps around -- is also what a WRONG port count
looks like, because the wrong record size slices S-parameter values into
the frequency column. That is why an out-of-order file is only accepted
when its name corroborates the count, and why the warning is loud.

Formats read
------------
Touchstone 1.x, any extension. RI / MA / DB, any frequency unit, with or
without an option line. UTF-8, UTF-8 with BOM, and UTF-16 (with or
without BOM) are all read; commas and semicolons between values, and
Fortran 'D' exponents (1.0D+09), are accepted with a WARN. Touchstone
2.0 and compressed files are refused by name rather than misread.
