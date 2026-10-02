Saving and reloading your setup
===============================

File -> Save Config...       (Ctrl+S)
File -> Load Config...       (Ctrl+O)
File -> Restore Last Session

What a config file holds
------------------------
Everything you TYPED, and nothing that was computed:

  * the loaded files, by path (see "Moving a config" below);
  * every trace -- its file, both tables (measurement ports and
    connections), anything kept verbatim as text, colour, line
    style, and whether it is shown;
  * the Trace model workspace's nets, GND list and conditions, and
    which workspace was showing;
  * RLC Freq, the band-fit range and model, and the Units setting;
  * the plot's checkbox row: X/Y log, Marker, Readout, which quantities
    are on screen, and where the marker sits.

It does NOT hold R, L, C, Q, Z or the coupling matrix. A config is a few
kB of readable JSON -- it goes in git, it can be mailed to a colleague,
and it can ride to an offline machine next to the data it describes.
After loading, press "Calculate All & Plot" and the numbers come back.
Export CSV remains the way to save the RESULTS.

Moving a config
---------------
Each file is recorded twice: relative to the config file, and as an
absolute path. Loading tries the relative one first, so copying the
whole folder (config + .sNp files) to another machine just works; the
absolute path is the fallback for a config file moved on its own.

A file that cannot be found at either place is reported by name in the
Results pane and the load continues. The traces that referenced it stay
in the list and stay editable -- add the file and load the config again,
or point the editor's File box at one that is loaded.

Loading REPLACES the current session (it is not a merge), and asks
first if anything is open.

Restore Last Session
--------------------
The config is written automatically when you close the window, to

    <your home>/.pkg_rlc_extractor/last_session.json

and the Results pane says on startup what is in it. It is NOT loaded
automatically: re-parsing a package export takes tens of seconds, and
that is not a good thing to do before you have asked for anything. An
empty session is never written, so opening the tool and closing it again
does not erase what the previous run left.

Configs from older builds
-------------------------
A config saved before the editor became one table still loads. Every
trace in it is moved into the two tables AS IT LOADS -- all of them,
not only the one you click -- and the Log gets one line per trace
saying what it became. The numbers are the ones the older build
computed, bit for bit, except in the few cases the Log line names;
the "Setting up a measurement" tab, "Configs from older builds",
lists them.

Save it again and it is written in the new form. Each trace then
carries "table_version": 1, which is what tells a later load that it
has been moved already and must be left alone, and "mode": 5, which
is what lets an OLDER build open the same file: it reads every trace
as a Custom trace, which is exactly what it now is.

Editing a config by hand
------------------------
It is plain JSON and it is meant to be readable. A value that will not
parse costs that one field -- the field keeps its default and the
Results pane says which -- rather than the whole file. A file that is
not a session file, or is from a newer build, is refused by name.
