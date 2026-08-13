"""
pkg_rlc_attrib_gui.py -- the Attribution window.

Where a mutual impedance comes from, and what would move it.  `pkg_rlc_attrib`
does every piece of arithmetic; everything here is presentation, provenance and
refusal.  It is a separate module from `pkg_rlc_gui` for two reasons: that file
is already 7000+ lines, and a separate module let this window and the menu
hooks that open it be built at the same time by different hands.

WHAT pkg_rlc_gui HAS TO CALL (the whole hook surface -- there is no other)
-------------------------------------------------------------------------
    ATTRIB_MENU_LABEL                     the menubar / right-click entry text
    attribution_refusal(trace, file_entry)
                                          None when it can run, else the reason
    open_attribution_window(app, trace)   -> AttributionWindow | None
    refresh_attribution_windows(app)      call from _apply_editor_strips, from
                                          every path that removes a trace or a
                                          file, and after a session load
    refresh_attribution_windows(app, rerender=True)
                                          call from _on_units_mode_changed,
                                          and from nowhere else
    attribution_session_state(win_or_none)        -> dict, for session_to_dict
    apply_attribution_session_state(app, data)    -> notes for the Results pane

`refresh_attribution_windows` is a signature-tuple comparison and a couple of
Label writes -- microseconds -- so it is safe on the editor's variable-trace
path.  It never raises, exactly like `_apply_editor_strips` itself: an error
raised there reaches no handler anyone controls, Tk prints it to a console a
double-clicked GUI does not have, and the window carries on showing a stale
verdict.

WHY THE IMPORTS OF pkg_rlc_gui ARE DEFERRED
-------------------------------------------
pkg_rlc_gui imports THIS module (for the hooks), so a module-level
`import pkg_rlc_gui` here would be a cycle.  Every use of it below is inside a
function, by which time pkg_rlc_gui is complete in sys.modules whichever of the
two was imported first.  What is imported that way is deliberately small and is
listed in `_gui()`'s docstring; the alternative -- a second copy of
`_value_formatter`, of `_trace_role_rows`, of `_build_termination` -- is how two
renderings of the same spec come to disagree, which this repo has already been
bitten by more than once.

`pkg_rlc_extractor` was a SECOND deferred import, for the ground-model parser
alone, and it is gone: that parser is now in `pkg_rlc_attrib_report`, which
this module imports at the top.  Only `pkg_rlc_gui` is deferred, and only
because the cycle is real.

THE FIFTEEN DECISIONS, and the measurement behind each
------------------------------------------------------
1.  MODELESS, and deliberately NOT `transient(app)`.  No `grab_set` anywhere:
    a modal Toplevel that outlives its opener blocks event delivery and
    `update()` never returns, which takes the GUI and the test suite down
    together (the documented style-picker / scrollbar hang).  `PortRolesWindow`
    DOES call `transient`, and that is right for it -- a quick read-while-
    editing panel that should not clutter the taskbar.  This window is the
    opposite: it holds a RESULT that cost a Recompute to produce, it is read
    against the plot and against the editor over many edits, and parking it on
    a second monitor and alt-tabbing back to it is the normal gesture.  On
    Windows `transient` removes both the taskbar button and the Alt-Tab entry
    and makes the WM withdraw the child with its master, so it would take that
    gesture away.  The cost of not setting it is that the window can end up
    behind the main one; `open_attribution_window` answers that by `lift`ing
    and focusing an existing window for the same (trace, victim, aggressor)
    instead of opening a second copy.
2.  NO NOTEBOOK.  A four-tab layout was reviewed and rejected: the sweep is a
    drill-down on the row you just clicked (a tab makes you pick the group
    again), and "does this ranking hold across frequency" is a validity
    qualifier on the table, not a place -- as a tab it is never opened.  So:
    a fixed header, a reconciliation strip, a one-line across-frequency badge
    that expands in place, one primary pane with a radiobutton VIEW TOGGLE, and
    a detail pane under a sash driven by the selected row.  Every pane is
    populated BEFORE `PanedWindow.add()`: ttk sizes a pane from its requested
    size at add() time and never recomputes.
3.  THE TABLES ARE MONOSPACE `tk.Text`, NOT `ttk.Treeview`.  Measured in this
    environment (Tk 8.6, vista theme): the same eight columns need 671 px as a
    Treeview at 100% and 971 px at 150%, against 490 px / 700 px as Consolas 9
    text.  ttk will not shrink a Treeview column below its set width even with
    stretch=True and it clips with NO ellipsis and NO overflow indicator, so
    "-0.6231" silently becomes a plausible shorter number; and in
    TkDefaultFont the signed-number glyphs are all different widths ('-' 5 px,
    '+' 9, U+2212 9, '.' 3, ' ' 4, digits 7), so a right-aligned column of
    signed values has its decimal point wandering +-4 px per row.  In
    Consolas 9 every glyph this window puts in a table measures exactly 7 px --
    re-measured here: ' ' 0 9 - + U+2212 U+2588 U+25BE U+25B8 . M X ( ) % j
    U+2026 U+03A9 U+2190 are all 7.  (U+2713 is 12 and is therefore used only
    in ttk Labels, never inside a table.)
4.  SIGNS are always one of a width-stable pair: U+2212 for negative, an
    explicit '+' for positive.  Never coloured by sign -- red is WARN_FG
    everywhere else in this application and a red negative makes a correct
    answer look like a fault.  Rows are coloured by ELEMENT KIND, reusing
    `PORT_ROLE_FG`, the palette the user already learned in Ports & Roles.
5.  RECONCILIATION IS IN THE HEADER, not the footer: it gates trust in
    everything under it, and at the bottom of a scrolling table it is the first
    thing off screen.  The TOTAL is shown even when the per-element split is
    withheld.
6.  [Recompute], NOT auto-refresh.  This is the one that would have shipped
    broken.  `tc.Zmat` is written only by `_on_calculate`; editing the spec
    sets `tc.stale` and leaves the numbers at the previous run's.  A window
    refreshing from `_apply_editor_strips` would decompose the NEW spec on the
    first keystroke, find the residual was not 1e-13 but however much the edit
    changed, and by rule 5 blank its own table -- a window that erases itself
    while you type.  The editor hook therefore updates ONE THING: the staleness
    banner, which is what makes the button honest.
7.  REFUSE ON A STALE OR FROZEN TRACE, BY NAME (`attribution_refusal`).  The
    menu entry stays LIVE so the refusal can explain itself -- CLAUDE.md, on
    the identical decision for Freeze: "a greyed entry would be the same bug
    report".
8.  THE SWEEP CANVAS follows `pkg_rlc_plot.FullscreenPlotWindow` (a second
    `FigureCanvasTkAgg` in a Toplevel is already shipped and safe) with two
    deliberate differences.  It gets NO `<Enter>` -> `focus_set` binding:
    measured, that binding moves focus off a sibling Entry, and this window has
    Entry fields directly above the plot, so a user typing 5.6 and moving the
    mouse toward [Recompute] would lose the rest of the keystrokes.  And it is
    drawn LAZILY on first reveal -- a canvas in an unmapped pane has no size,
    so `draw()`/`tight_layout` there lays out for a 1x1 widget.
    THE Y AXIS IS SCALED TO THE PHYSICAL ENDPOINTS AND THE POLE IS LABELLED
    (`sweep_picture`).  A Mobius map has a pole, and drawn raw the pole owns
    the picture: measured on the shipped fixture, one vertical spike over a
    `1e-10` axis with a headline interval of (-394 uH, +375 uH) -- the tool
    describing its own arithmetic.  A pole is a real feature, so it is drawn as
    a labelled vertical line at its (closed-form) parameter value and the
    headline is the pole-free interval; what changes is only what the axis is
    scaled to.
9.  NO ACCELERATOR.  `bind_all` reaches every Toplevel: measured, Ctrl+S typed
    into a Toplevel Entry fires the App's `_on_save_config`, and Ctrl+O would
    open Load Config and replace every trace including the one this window
    describes.  Nothing here registers a bind_all, and the window survives its
    subject disappearing (rule 11).  For the wheel: this window registers
    NOTHING with `App._register_scrollable`, so `_route_wheel` walks out of it,
    finds no handler, and lets Tk's own class bindings scroll the Texts ("Text"
    is in `App._WHEEL_OWNERS`).  "Canvas" is NOT in that set, so the matplotlib
    canvas would be scrolled by any registered scrollable ancestor -- there is
    none, by construction.
10. PACK ORDER: footer `side=BOTTOM` FIRST, then the header, the strips, and
    the PanedWindow with `expand=True` LAST.  pack unmaps from the END, so the
    buttons and the reconciliation verdict are unconditional and it is the
    table that gives up height.
11. THE WINDOW OUTLIVES ITS SUBJECT.  It holds a RESULT, so unlike
    PortRolesWindow it cannot simply re-read `app.traces` and degrade.  Every
    path that removes a trace or a file, and every session load, must call
    `refresh_attribution_windows(app)` -- the same class of omission as the
    documented `_on_remove_file` forgot-to-replot bug.
12. EXPORT carries the full provenance: run number, the frequency with its snap
    note, the complete sign-convention declaration, the ground model, and the
    termination spec verbatim.  The frozen-trace CSV precedent is explicit that
    a block attributed to the wrong run is a real bug.
13. THE SPLIT IS DERIVED FROM THE ROW COUNT, and then belongs to the user.
    `ttk.Panedwindow` sizes its panes from their requested heights and shares
    the spare by weight, and both panes request the same 8-line Text -- so the
    divider had nothing to do with what was in the table.  Measured at 980x700:
    three element rows in a 239 px widget (169 px of empty space) over a detail
    pane that was scrolling, and horizontally clipping at 720.  The position is
    computed from CONTENT and never from a measured pane height (that shape is
    the documented limit cycle); the paned window's own height is read only as
    a clamp, and cannot feed back -- measured, it is 522 px at every sash
    position from 0 to 900.  A DRAG claims it permanently.
14. THE GROUND MODEL IS ON THE WINDOW, not only on the CLI.  Independent
    per-lead impedances against one shared return moves |M| by 9.60 dB --
    larger than the 6.07 dB dispute this feature exists to settle -- and real
    package grounds share a return plane, so the default is the one that
    UNDERSTATES the return inductance.  The field takes the CLI's spelling and
    is parsed by the CLI's own parser; a change goes through [Recompute] like
    any other input, and because a dense `Zt` is a network `compute_z_matrix`
    was never asked about, the reconciliation degrades to "not comparable"
    through `build_context`'s own `reference_applicable`, not through a second
    rule here.  What `_attr_zt` says about applying it is CARRIED, not
    discarded: its first note means the model was NOT applied, and a reader who
    typed `shared:L=1n`, saw nothing move and read a strip still claiming that
    model would conclude the shared return is worth 0 dB.
15. EVERY CLIPPING STRIP LEADS WITH ITS NUMBERS.  Five one-line Labels here are
    `wraplength=0` -- they clip rather than wrap, because a wrapping strip
    costs plot height (the `_footer_strip_text` rule) -- and the corollary is
    that whatever is written LAST is not on screen.  Measured on the real
    widgets, worst case at 150% DPI / 980 px: the across-frequency badge showed
    64 of 238 characters, the sign strip 66 of 137, and the sweep caption -- the
    narrowest of them, at 329 px even in a 1020 px window, because it sits
    under the plot in the right half of a horizontal split -- 51 of 107.  In
    each case the part that went was the part the strip exists for: the check's
    cost and gesture, the ground model in force, and the interval with its two
    endpoints.  So the order is number/verdict first, prose last, everywhere;
    the full text is in Copy report and in the CSV, which is what rule 12
    carries it for.
"""

from __future__ import annotations

import csv
import math
import tkinter as tk
import weakref
from dataclasses import dataclass, field, replace
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Optional, Sequence

import numpy as np
import matplotlib.ticker as mticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import pkg_rlc_attrib as attrib
import pkg_rlc_files_gui as files_gui
# The CLI's ground-model parser.  One grammar, one set of error messages, one
# label format for `--attribute-ground-model` and for the Grounds field on this
# window -- see `parse_ground_model`, which is two lines for that reason.
from pkg_rlc_attrib_report import _attr_ground_model, _attr_zt
from pkg_rlc_core import (
    ROLE_ELEMENT,
    ROLE_GROUND,
    ROLE_PROBE_PLUS,
    ROLE_SHORTED,
    ROLE_VDD,
    format_freq,
    format_si,
    parse_kv_rlc_params,
    parse_si,
    resolve_meas_ports,
    row_sources,
    rows_to_dsl_text,
)
from pkg_rlc_plot import ReflowRow

__all__ = [
    "ATTRIB_MENU_LABEL",
    "ATTRIB_TITLE",
    "AttributionWindow",
    "AttribResult",
    "Provenance",
    "Column",
    "TableText",
    "attribution_refusal",
    "open_attribution_window",
    "refresh_attribution_windows",
    "live_windows",
    "compute_attribution",
    "stability_ranks",
    "write_attribution_csv",
    "candidate_list",
    "CSV_FIELDS",
    "GROUND_MODEL_TEXT",
    "GROUND_MODEL_DEFAULT",
    "GROUND_MODEL_HINT",
    "parse_ground_model",
    "ground_model_zt",
    "sign_strip_text",
    "SIGN_STRIP_TEXT",
    "QUANTITIES",
    "SweepPole",
    "SweepPicture",
    "sweep_pole_locations",
    "sweep_picture",
    "pole_label",
    "pole_span",
    "stability_offer",
    "attribution_session_state",
    "apply_attribution_session_state",
    "spec_signature",
    "signed_str",
    "reconciliation_verdict",
    "reconciliation_line",
    "provenance_lines",
    "staleness_text",
    "contributions_table",
    "sensitivity_table",
    "detail_lines",
    "sweep_caption",
    "header_trace_text",
    "stability_line",
    "report_text",
    "csv_records",
    "parse_candidate",
    "element_role",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ATTRIB_MENU_LABEL = "Attribution…"
ATTRIB_TITLE = "Attribution"

#: Default geometry and floor.  Measured with the real widgets (see
#: `tests/test_attrib_window.py::TestHeaderBudget`): the six header items ask
#: 228/160/186/145/143/99 = 961 px, so at the 964 px the default 980-wide
#: window gives the strip they fit on ONE row (29 px) and at the 720 px
#: minimum (704 px of strip) they wrap onto TWO (58 px).  An earlier version of
#: this comment claimed the header wraps at the default size too; re-measured,
#: it does not, and `test_the_header_wraps_only_when_it_has_to` agrees with the
#: measurement.  `ReflowRow` is still what makes either size safe: pack unmaps
#: from the END, so a plain `pack(side=LEFT)` run would have taken [Recompute]
#: off screen with no scrollbar and no other route to it, which is the defect
#: `tests/test_plot_controls.py` exists to stop recurring.
ATTRIB_GEOMETRY = "980x700"
ATTRIB_MIN_W = 720
ATTRIB_MIN_H = 420

#: The minimum height is a FLOOR, not the answer -- `_apply_min_height` raises
#: it to whatever the fixed chrome actually costs at the current DPI and the
#: current width, because at 150% the declared 420 was a size at which the
#: window showed no content at all.
#:
#: MEASURED, real widgets, mapped Toplevel, `tk scaling 2.0` + every named font
#: x1.5 for the 150% column (the same definition
#: `test_run_history.py::test_the_keep_button_is_READABLE_at_150_percent_font_scaling`
#: uses):
#:
#:     chrome (header + banner + sign + recon + badge + footer, with padding)
#:         100%:  207 px at 720 wide, 178 at 980
#:         150%:  436 px at 720 wide, 388 at 980, 340 at 1400
#:     split needed for the table Text to be MAPPED
#:         100%:  124 px      150%:  162 px      (independent of width)
#:
#: So at 150% the fixed sections alone are 436 px against a declared minimum of
#: 420, and at exactly 720x420 the whole PanedWindow read
#: `winfo_ismapped() == 0` -- table, detail pane and sweep canvas all gone,
#: with no scrollbar, no message and no way down because the user is already AT
#: the minimum.  It was not only the minimum either: the table was already
#: unmapped at 820x540.  The pack ORDER was right (the footer and the strips
#: are the last to go, verified by shrinking to 180 px); what was wrong is that
#: "the table gives up height" was allowed to mean "the table gives up all of
#: it".
#:
#: The floor below is in lines of the TABLE font, which is the thing that has
#: to fit: 9 x 14 = 126 px at 100% (>= the measured 124) and 9 x 22 = 198 at
#: 150% (>= 162).  At 100% that puts the computed minimum at 207 + 126 = 333 px
#: at 720 wide, i.e. UNDER the declared 420, so **nothing about the 100% window
#: moves** -- measured, `win.minsize()` still reads (720, 420) at every width.
#: At 150% it reads (720, 634) at 720 wide, (720, 586) at 980 and (720, 538) at
#: 1200 and above, and at every one of those the table, the detail pane, the
#: sweep canvas and all three buttons are mapped.
#:
#: It SETTLES, which is the property that matters most here -- a layout rule
#: that reads a size it can itself change flips forever and `update()` never
#: returns, taking the GUI and the test suite down together.  This one reads
#: the WIDTH and writes only a minimum HEIGHT, so it is a fixed point in the
#: same way `ReflowRow` is.  Measured over eight resizes in both directions at
#: both scalings: every one settled inside 40 update rounds (86-126 ms) with
#: the last twenty rounds byte-identical.
ATTRIB_SPLIT_FLOOR_LINES = 9

#: The split's INITIAL position, in lines of the table font (item 2).
#:
#: WHAT WAS WRONG.  `ttk.Panedwindow` sizes its panes from their requested
#: heights at `add()` time and then shares out the spare by weight (3:2 here),
#: and both panes request the same 8-line Text -- so the position had nothing
#: to do with what was IN the table.  Measured at 980x700, 100%: the table held
#: 5 rendered lines (2 of heading + 3 elements = 70 px of text) in a 239 px
#: widget -- 169 px of empty space -- while the detail pane under it was
#: 198 px against 19 lines of prose, i.e. scrolling, and at the 720 px minimum
#: it was ALSO clipping horizontally (`xview` (0.0, 0.950)).  The pane with
#: nothing in it had the room and the pane with the reading in it did not.
#:
#: The position is derived from the ROW COUNT and nothing else -- never from a
#: measured pane height, which is the shape of the documented limit cycle (a
#: layout rule that reads a size it can itself change flips forever and
#: `update()` never returns).  The one measured quantity is the paned window's
#: OWN height, used only as a CLAMP so the detail pane keeps a floor, and that
#: cannot feed back: measured, `paned.winfo_height()` read 522 px at every
#: sash position from 0 to 900, because the height comes from `pack` in the
#: Toplevel and a divider inside it moves nothing outside it.
#:
#: `SPARE` is what stops the last row sitting against the divider; `MAX` keeps
#: a 60-ball ground spec from swallowing the drill-down (past it the table
#: scrolls, which is what its scrollbar is for); the two FLOORs are what the
#: clamp protects at the enforced minimum, where there is not room for both.
ATTRIB_SASH_SPARE_LINES = 2
ATTRIB_SASH_MAX_LINES = 16
ATTRIB_TABLE_FLOOR_LINES = 3
ATTRIB_DETAIL_FLOOR_LINES = 6

#: The only font any table in this window is rendered in.  Every glyph the
#: tables emit measures exactly 7 px in it (see the module docstring), which is
#: the entire reason the columns line up.
ATTRIB_FONT = ("Consolas", 9)

#: Same glyph as `pkg_rlc_gui.RESULTS_SWATCH`, and
#: `tests/test_attrib_window.py::TestSwatchMatchesResultsPane` pins that they
#: stay equal.  It is duplicated rather than imported so the pure formatters
#: below need no deferred import of pkg_rlc_gui on a per-cell path; the test is
#: what stops the two drifting.  Measured here: 7 px in Consolas 9, i.e.
#: exactly one monospace cell, the same as ' '.
ATTRIB_SWATCH = "█"
_SWATCH_PAD = " " * len(ATTRIB_SWATCH)

#: The width-stable expander pair for the across-frequency badge.  Measured in
#: BOTH fonts this window uses: U+25B8 and U+25BE are 7 px in Consolas 9 and
#: 7 px in TkDefaultFont (Microsoft YaHei UI 9), so toggling the badge cannot
#: reflow the line beside it.  Same rule as the run tabs' '☑'/'☐' pair.
EXPAND_COLLAPSED = "▸"
EXPAND_EXPANDED = "▾"

#: U+2212 MINUS SIGN and '+' -- 7 px each in Consolas 9, the same as a space.
#: One of the two is emitted for EVERY numeric cell, never a conditional sign,
#: or a column of mixed signs shifts by one cell per row.
MINUS = "−"
PLUS = "+"

#: The pole marker on the sweep.  A colour, on a plot, is NOT the "colour never
#: means sign" rule -- that rule is about the tables, where a red negative
#: makes a correct answer look like a fault.  Here it distinguishes an
#: ANNOTATION from the two data curves and from the two asymptote lines, which
#: already carry green (ideal) and amber (open); it is deliberately not
#: `WARN_FG`, because a pole is a feature of the structure and not a fault.
POLE_LINE_FG = "#b03030"

#: Text-column caps, in characters.  Numeric columns are auto-sized from their
#: widest cell OR their header (the readout's rule: sizing on the values alone
#: put a 7-char value under a 5-char header and threw the heading one place off
#: the numbers it names).  Only these two are capped, and they ellipsise with
#: U+2026 rather than clipping -- a silent clip is the measured Treeview
#: failure this table exists to avoid.
ELEMENT_COL_CHARS = 20
SOURCE_COL_CHARS = 14

#: Characters of the trace label and of the file name the header's first item
#: shows.  It is capped because the header is a ReflowRow and a WRAP COSTS
#: PLOT HEIGHT: measured at the 720 px minimum, the strip is 704 px, the six
#: items ask 237/160/186/145/143/99 px and land on two rows of 29 px.  An
#: uncapped first item grows with the file name -- a 37-char name plus a
#: 30-char label is about 520 px in TkDefaultFont, which pushes the strip to
#: three rows and takes another 29 px out of a pane budget that at that size
#: is 213 px for the whole split.  The full, untruncated identity is in the
#: window TITLE, in Copy report and in the CSV header.
HEADER_LABEL_CHARS = 18

#: Frequencies the across-frequency check re-ranks at, including the primary.
#: Small on purpose: each one is a fresh `build_context` + `decompose`, which is
#: O(N^3) in the PORT count, and the badge is a sanity check on the ranking
#: rather than a sweep.  It only ever runs when the user expands the badge.
STABILITY_POINTS = 5

#: Contributions weaker than this fraction of the strongest term are folded
#: into one line.  Same shape as `COUPLING_FLOOR_DB` in the results pane, and
#: the same two exemptions apply: the strongest term is never folded, and a
#: term whose contribution is not finite is never folded (a missing number is
#: not a small one).  The bare EM term is never folded either -- it is the
#: baseline every other term is a correction to.
CONTRIB_FLOOR = 1e-4

#: Element kind -> the Ports & Roles role whose colour it takes.  The palette
#: itself is `pkg_rlc_gui.PORT_ROLE_FG` and is looked up lazily, so there is
#: exactly one set of hex values in the application.  The bare EM term takes
#: the PROBE colour: it is the coupling that belongs to the measurement ports
#: themselves, with every other port open.
ELEMENT_KIND_ROLE = {
    "ground": ROLE_GROUND,
    "vdd": ROLE_VDD,
    "lumped_to_gnd": ROLE_ELEMENT,
    "lumped_between": ROLE_ELEMENT,
    "short": ROLE_SHORTED,
    "": ROLE_PROBE_PLUS,          # the bare EM term
}

#: Quantities offered in the header combobox, in the order they are offered.
#: Exactly `attrib.DECOMPOSABLE`'s keys -- the non-decomposable ones refuse
#: themselves BY NAME inside the module, and putting them in a combobox would
#: be offering something that always fails.
QUANTITIES = ("M", "ImZ", "ReZ", "Z", "M/L_a", "k")

#: The ground model the window opens on, in the CLI's own spelling.
#: `diag` = every element on the impedance its own row declares.
GROUND_MODEL_DEFAULT = "diag"

#: What the ground-model field means, in full, for the export (rule 12).
#:
#: THE WINDOW NOW OFFERS THE SHARED-RETURN MODEL.  It did not, and the reason
#: recorded here was that a dense element-impedance matrix is not expressible
#: as a `TerminationSet` and that a seventh header control would push
#: [Recompute] onto a third row.  The first half is true and is exactly why
#: `reference_applicable` exists (see `parse_ground_model`); the second is a
#: layout fact about the HEADER, and the control is not in the header -- it has
#: a row of its own, measured at 25 px (100%) / 37 px (150%), against a wrap of
#: the header ReflowRow which measured 29 px at 980 and 58 px at 720.  What
#: made the omission untenable is the number: four ground balls at 1 nH each
#: INDEPENDENTLY against the same four tied through ONE shared 1 nH moves |M|
#: by **9.60 dB** -- larger than the 6.07 dB dispute this whole feature exists
#: to settle -- and real package grounds share a return plane, so the default
#: is the one that understates the return inductance, by (1 + (n-1)k_ret).
GROUND_MODEL_TEXT = (
    "'diag' puts every element on the impedance its own row declares (0 for an "
    "ideal ground / short, 1/y_series_rlc(omega) for a lumped one), each "
    "INDEPENDENT of the others. 'shared:SPEC' (e.g. shared:L=0.3n) keeps those "
    "declared impedances and adds ONE shared return impedance across every "
    "shunt element, which is what a real package ground plane is: measured on "
    "decap_4port.s4p, four balls at 1 nH each independently versus the same "
    "four tied through one shared 1 nH moved |M| by 9.60 dB. 'diag:SPEC' "
    "overrides every shunt lead with SPEC and leaves them independent. The "
    "spelling is the CLI's, verbatim: --attribute-ground-model diag|diag:SPEC|"
    "shared:SPEC."
)

#: The one line beside the control.  It says why the default is not obviously
#: right and stops there; `wraplength=0`, so it CLIPS like every other strip
#: here and the full statement is in Copy report and in the CSV.
GROUND_MODEL_HINT = (
    "independent leads understate a shared return by (1+(n−1)k) — "
    "try shared:L=0.3n"
)

SIGN_NOTE_TERMS = (
    "A negative term OPPOSES the total; the terms sum to it exactly."
)
SIGN_NOTE_SHARES = (
    "Shares are of the SIGNED total, so they exceed 100% and go negative "
    "wherever terms cancel."
)

#: The one-line header strip, ordered by what has to survive the clip.  It is
#: rendered with `wraplength=0` and therefore CLIPS.
#:
#: THE BUDGET IS 51 CHARACTERS, not 120.  The first draft of this string was
#: sized against TkDefaultFont at 100% only, where the strip fits 120
#: characters at the 720 px minimum and 162 at the 980 px default -- and the
#: comment here claimed on that basis that "the sign rule and the shares rule
#: are whole at every supported size".  Re-measured at the 150% DPI this repo
#: supports (`tk scaling 2.0`, every named font x1.5, which is what
#: `test_run_history.py::test_the_keep_button_is_READABLE_at_150_percent_font_scaling`
#: means by 150%), the same 221-character string fits **52 characters at 720 px
#: and 74 at 980** -- it read `Signs: − opposes the total, + adds; the terms
#: sum to` and the ENTIRE shares rule, which rule 4 requires to be stated once
#: in the header, was off screen at every supported size.
#:
#: So it is rewritten to spend its first 48 characters on both rules and
#: nothing else.  Measured on THIS string, off the real widget: **48 chars at
#: 150%/720** (`− opposes, + adds, sum EXACT. Shares SIGNED: >10`), 66 at
#: 150%/980, 110 at 100%/720 and all 143 at 100%/980.  48 characters is the
#: whole budget at the worst supported size, so the shares rule is abbreviated
#: rather than dropped -- naming it is what rule 4 asks for, and every word of
#: the long form is in Copy report and in the CSV header, which is where rule
#: 12 requires the complete declaration anyway.
#:
#: The GROUND MODEL is no longer baked into this constant: it is a choice now,
#: so the strip has to say which one is IN FORCE rather than assert the
#: default.  It goes SECOND -- see `sign_strip_text`.
SIGN_STRIP_TEXT = "− opposes, + adds, sum EXACT."

#: The second sign rule, which is what gives way when the strip clips.
SIGN_STRIP_SHARES = "Shares SIGNED: >100% and negative are normal."


def sign_strip_text(ground_label: str = "") -> str:
    """
    The header strip: the sign rule, the GROUND MODEL IN FORCE, then the rest.

    THE MODEL IS SECOND, NOT LAST, AND THAT IS A MEASURED TRADE.  It used to
    follow both sign rules, on the argument that the model is also on its own
    control -- but the control shows the FIELD, which can have been edited
    without a Recompute, while this strip shows what produced the numbers on
    screen.  MEASURED on the real Label (137 characters, the strip's own font)
    at the window widths this tool is used at:

        100% 1500 px  137 chars   100% 980 px  137   100% 720 px  114
        150% 1500 px  106 chars   150% 980 px   66   150% 720 px   48

    `'Grounds:' in shown` was **False at 980, 860 and 720 px at 150%**, i.e. at
    every supported size at that scaling except a maximised window there was no
    on-screen statement of the model at all -- for a choice worth a measured
    7.19 dB on the shipped fixture, in the flow that exists to settle a 6.07 dB
    dispute.  With the model second it survives at 66 characters (`… sum
    EXACT. Grounds: diag (as declared). Share`), and what clips instead is the
    SHARES rule, which is also in the table's own column heading, in Help and
    in the README, and which cannot change a number.

    Both are in Copy report and in the CSV in full, where nothing clips
    (rule 12).
    """
    label = str(ground_label or "").strip()
    mid = f" Grounds: {label}." if label else ""
    return (SIGN_STRIP_TEXT + mid + " " + SIGN_STRIP_SHARES
            + " Full declaration in Copy report.")

#: The two candidates this tool will assume on the user's behalf, and the only
#: two.  They need no engineering judgement -- "the element is not there" and
#: "the element is perfect".  Anything else (a ball's lead inductance, a 50 ohm
#: terminator) is the user's to name, in the detail pane's Candidates field.
#: Same rule and same wording as the CLI's `--attribute-alt`.
STRUCTURAL_CANDIDATES = ("open", "ideal")

CANDIDATE_HINT = (
    "Candidates: open, ideal, or R=…/L=…/C=… (comma-separated). "
    "This tool will not guess a package value."
)


# ---------------------------------------------------------------------------
# Deferred access to pkg_rlc_gui
# ---------------------------------------------------------------------------

def _gui():
    """
    `pkg_rlc_gui`, imported LAZILY -- see the module docstring.

    Four things are taken from it and nothing else:
      * `_config_signature`  -- ONE definition of "did this edit change the
        answer", so the staleness banner cannot drift from the trailing `*` in
        the Traces list;
      * `_trace_role_rows`   -- ONE definition of "which row declared this
        port", so the From column matches the Ports & Roles window;
      * `_value_formatter`   -- ONE definition of the aligned units mode, so a
        column here carries the same SI prefix the results pane would;
      * `PORT_ROLE_FG` / `WARN_FG` / `PLACEHOLDER_FG` -- ONE palette;
      * `trace_is_composed` and the three port-field scopers
        (`_scope_mport_rows` / `_scope_conn_rows` / `_scope_dsl_text`) -- ONE
        definition of what `F2.13` means, so the ports this window decomposes
        and the ports Calculate solved are the same ports.
    """
    import pkg_rlc_gui                                   # noqa: PLC0415
    return pkg_rlc_gui


def spec_signature(trace) -> tuple:
    """
    `pkg_rlc_gui._config_signature`, reached without a module-level import.

    Deliberately NOT a second copy of that tuple.  It decides both the trailing
    `*` in the Traces list and this window's staleness banner, and two
    definitions is exactly how one of them comes to say the spec is unchanged
    when the other says it is not.
    """
    return _gui()._config_signature(trace)


def parse_ground_model(text: str, omega: float) -> tuple:
    """
    'diag' | 'diag:SPEC' | 'shared:SPEC'  ->  (kind, impedance, label).

    STRAIGHT THROUGH THE CLI'S OWN PARSER, and that is the whole point of this
    two-line function.  `--attribute-ground-model` has shipped this grammar,
    these error messages and this label format since stage 3; a second copy
    here is precisely how the window and the CLI come to disagree about what
    `shared:L=1n` means, and this repo has been bitten by two renderings of one
    spec more than once.

    It used to reach the parser by importing `pkg_rlc_extractor` INSIDE this
    function -- a lazy import that dodged the cycle through `main()`, which
    imports `pkg_rlc_gui`.  The parser now lives in `pkg_rlc_attrib_report`,
    which imports `pkg_rlc_attrib`, `pkg_rlc_core` and `pkg_rlc_report` and
    nothing else, so there is no cycle to dodge and the import is at the top
    where it can be read.

    Raises `ValueError` with the CLI's own wording on anything it cannot read.
    """
    return _attr_ground_model(text, omega)


def ground_model_zt(ctx, kind: str, z):
    """
    (the (m, m) element-impedance matrix this model asks for, notes), or
    (None, notes) to keep the one the spec itself declares.

    The CLI's `_attr_zt`, for the reason above.  It is the one place that knows
    the dense block is built over the SHUNT sub-block only:
    `termination_impedance_shared_return` assumes every element it is handed is
    a ball sharing the return plane, so giving it a `short_to` as well would
    quietly stop that being a short.
    """
    return _attr_zt(ctx, kind, z)


def _role_colour(kind: str) -> str:
    """The foreground an element of `kind` takes, from the Ports & Roles palette."""
    g = _gui()
    return g.PORT_ROLE_FG.get(ELEMENT_KIND_ROLE.get(kind, ROLE_ELEMENT),
                              g.PORT_ROLE_FG[ROLE_ELEMENT])


def element_role(kind: str) -> str:
    """Element kind -> the Ports & Roles role name.  Pure; no palette needed."""
    return ELEMENT_KIND_ROLE.get(kind, ROLE_ELEMENT)


# ---------------------------------------------------------------------------
# Refusal (rule 7)
# ---------------------------------------------------------------------------

def attribution_refusal(trace, file_entry, *,
                        allow_stale: bool = False) -> Optional[str]:
    """
    None when an attribution of `trace` can run; otherwise WHY it cannot, in
    words meant for the user.

    The order is not arbitrary.  `frozen` is tested before the file, because a
    frozen trace can never be attributed whatever the file is doing and sending
    the user off to load one would be a dead end.  Everything after that is in
    the order the work would hit it.

    `allow_stale` is for [Recompute] and for nothing else.  Opening the window
    on a stale trace is refused (rule 7): the numbers on screen and the spec
    beside them would not describe each other.  But [Recompute] exists to
    decompose the spec as edited -- refusing there would mean a full Calculate
    of every trace just to re-attribute, which is hostile in the one workflow
    this window is for -- so it asks with the stale branch off, and pays for it
    by stamping `spec_matches_run=False` all the way through the banner, the
    report and the CSV header.

    Duck-typed on purpose (`getattr`, never an isinstance against
    `pkg_rlc_gui.TraceConfig`): this is the one function the hooks agent calls
    before anything is built, and it must be importable and testable without
    pulling pkg_rlc_gui in.
    """
    if trace is None:
        return ("Select a trace in the Traces list first — an attribution "
                "decomposes ONE trace's mutual impedance.")

    label = getattr(trace, "label", "") or "this trace"

    if getattr(trace, "frozen", False):
        # A snapshot's numbers came from an EARLIER run and it can never be
        # recalculated (Calculate skips it, the editor refuses it).  A
        # decomposition computed now would be stamped with the CURRENT run
        # number over numbers that are not from it -- which is precisely the
        # bug the frozen-trace CSV header exists to stop, arriving through a
        # different door.
        return (f"'{label}' is a frozen snapshot. Its numbers came from an "
                "earlier run and it can never be recalculated, so an "
                "attribution of it could only be stamped with the CURRENT "
                "run — the same mislabelling the frozen-trace CSV header "
                "exists to prevent.\n\nRight-click it in the Traces list → "
                "Unfreeze, Calculate, then open Attribution again.")

    if file_entry is None:
        fl = getattr(trace, "file_label", "") or "(none)"
        return (f"The Touchstone file '{fl}' this trace refers to is not "
                "loaded, so there is no Y matrix to decompose. Load it and "
                "Calculate first.")

    # "Has it got a pair?" is TWO questions, and testing only the first one
    # sent a whole class of trace to the wrong refusal.  `_on_calculate` routes
    # on `tc.mode == 6 or n_mports > 1`, so a MODE 6 trace with a single
    # measurement port takes the coupling path anyway and comes back with a
    # perfectly real (F, 1, 1) `Zmat` -- not None, so the branch below waved it
    # through, and what turned it away was `open_attribution_window`'s
    # "fewer than two measurement port names cached. Calculate it again."
    # backstop: a message describing an internal inconsistency that had not
    # happened, offering advice ("Calculate it again") that cannot possibly
    # help.  Measured: mode 6, one measurement port row, Calculate ->
    # `tc.Zmat.shape == (100, 1, 1)` and `attribution_refusal(...) is None`.
    # The count is therefore part of the same test, and both routes -- one
    # port in mode 5 (Zmat is None) and one port in mode 6 (Zmat is a 1x1) --
    # reach the one refusal that names the actual problem.
    zm = getattr(trace, "Zmat", None)
    n_names = len(list(getattr(trace, "mport_names", None) or []))
    if zm is None or n_names < 2:
        if zm is None and getattr(trace, "Z", None) is None:
            return (f"'{label}' has no numbers yet.\n\nCalculate it first — "
                    "an attribution is a decomposition of a RESULT, so there "
                    "has to be one.")
        # One measurement port: `_on_calculate` took the compute_z path and
        # left Zmat at None, or took the coupling path on the mode alone and
        # left a 1x1.  Either way Z_ab is a MUTUAL impedance; there is no pair.
        return (f"'{label}' has only one measurement port, so there is no "
                "mutual impedance to attribute — Z_ab needs a victim AND an "
                "aggressor.\n\nDefine a second measurement port (Mode 6, or a "
                "second probe row in Mode 5) and Calculate.")

    if getattr(trace, "stale", False) and not allow_stale:
        return (f"'{label}' has been edited since it was last calculated, so "
                "its numbers and the spec beside them no longer describe each "
                "other. An attribution stamped with that run would carry the "
                "run's provenance over a different network.\n\nCalculate "
                "first.")

    return None


# ---------------------------------------------------------------------------
# Pure rendering: signs and values (rule 4)
# ---------------------------------------------------------------------------

def signed_str(text: str) -> str:
    """
    Force a leading sign onto an already-formatted number.

    U+2212 for negative, an explicit '+' for positive, ALWAYS one of the two --
    both 7 px in Consolas 9, the same as a space, so a column of mixed signs
    keeps its decimal points in one place.  Only position 0 is touched: a
    number that fell out of the SI prefix table reads '1.23e-05', and that
    exponent's '-' is not a sign of the value.

    Non-numeric readings ('nan', 'inf', '--') are returned untouched: they have
    no sign to render and prefixing one would invent a claim.  '--' is the
    reason the leading '-' is not enough on its own -- it is the no-reading
    marker, and turning it into an en-dash-looking '−-' would read as a
    negative number whose digits went missing.
    """
    s = str(text)
    if not s:
        return s
    head, rest = s[0], s[1:]
    numeric = rest[:1].isdigit() or rest[:1] == "."
    if head == "-":
        return (MINUS + rest) if numeric else s
    if head in (PLUS, MINUS):
        return s
    if head.isdigit() or head == ".":
        return PLUS + s
    return s


def _display_unit(unit: str) -> str:
    """'Ohm' -> the ohm sign, as the results pane spells it. 7 px in Consolas."""
    return "Ω" if unit == "Ohm" else unit


def _value_fmt(values: Sequence[float], unit: str, units_mode: str):
    """
    (header suffix, cell function) for one column, honouring the units mode.

    Straight through `pkg_rlc_gui._value_formatter`, so 'aligned' picks the
    same one-prefix-per-column it picks in the results pane.  The sign is
    forced on afterwards, which is this window's rule and not that one's.
    """
    finite = [v for v in values if isinstance(v, float) and math.isfinite(v)]
    suffix, fn = _gui()._value_formatter(finite, _display_unit(unit),
                                         units_mode)

    def cell(v: float) -> str:
        # NaN and infinity are different readings and are rendered
        # differently.  '--' reads as "no reading", which is what a NaN is
        # (an undefined k, a probe with no return path); an infinity is a
        # real reading and keeps its sign, because "the impedance ran away
        # upward" and "it ran away downward" are not the same fact.  Either
        # way the ROW STAYS -- dropping it shifts every row below and the
        # swatches stop lining up with the elements they name.
        if math.isnan(v):
            return "--"
        if math.isinf(v):
            return (MINUS if v < 0 else PLUS) + "inf"
        return signed_str(fn(v))

    return suffix, cell


def _pct(v: float) -> str:
    """A share, as a signed percentage.  '--' where it is undefined."""
    if not math.isfinite(v):
        return "--"
    return signed_str(f"{100.0 * v:.2f}") + "%"


def _cplx(v: complex, unit: str) -> str:
    """
    A complex reading as 'Re +Imj', both signed, for the detail pane.

    Not for a table column: two signed numbers in one cell cannot be
    right-aligned on anything.  The tables split Re and Im into two columns
    instead (see `_quantity_columns`).
    """
    if not (math.isfinite(v.real) and math.isfinite(v.imag)):
        return "--"
    u = _display_unit(unit)
    return (signed_str(format_si(v.real, u)) + "  "
            + signed_str(format_si(v.imag, u)) + "j")


# ---------------------------------------------------------------------------
# Pure rendering: the monospace table model (rule 3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    """One column of a monospace table.

    `width` 0 means "auto": as wide as the widest cell OR the header, whichever
    is larger.  `cap` caps a TEXT column and ellipsises past it -- numeric
    columns are never capped, because a clipped number is a plausible wrong
    number and that is the whole reason this is not a Treeview.
    """
    title: str
    align: str = ">"
    cap: int = 0


@dataclass(frozen=True)
class TableText:
    """
    A rendered table plus everything a widget needs to tag and select it,
    with no line-number arithmetic anywhere.

    `rows` is (line index into `lines`, row key, element kind).  The key is
    whatever the caller wants back when that line is clicked -- an element
    index, or None for the bare EM row and for a folded summary line.
    """
    lines: tuple[str, ...]
    rows: tuple[tuple[int, object, str], ...]
    width: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _fit(cell: str, width: int) -> str:
    """Cap a text cell with U+2026, never a silent clip."""
    if len(cell) <= width:
        return cell
    if width <= 1:
        return cell[:width]
    return cell[:width - 1] + "…"


def render_table(columns: Sequence[Column],
                 rows: Sequence[tuple[object, str, Sequence[str]]],
                 ) -> TableText:
    """
    Lay out a monospace table.

    `rows` is (key, element kind, cells).  Every data line is prefixed with
    `ATTRIB_SWATCH`; the header and any rule line are prefixed with a space of
    the same width, which is what keeps the heading over the numbers it names.

    Pure -- no Tk, no widget, no font.  The widget's job is to insert
    `TableText.text` and tag the lines `TableText.rows` names.
    """
    ncol = len(columns)
    cells = [[str(c) for c in r[2]] for r in rows]
    widths = []
    for j, col in enumerate(columns):
        # As wide as the widest cell OR the header, whichever is larger.
        # Sizing on the values alone is the readout's documented bug: a 7-char
        # value under a 5-char header throws every heading one place off the
        # numbers it names.
        w = max([len(col.title)] + [len(r[j]) for r in cells])
        if col.cap:
            w = min(w, col.cap)
        widths.append(w)

    def line(prefix: str, values: Sequence[str]) -> str:
        parts = []
        for j in range(ncol):
            v = _fit(values[j], widths[j])
            parts.append(f"{v:{columns[j].align}{widths[j]}}")
        return prefix + " " + " ".join(parts)

    head = line(_SWATCH_PAD, [c.title for c in columns])
    rule = _SWATCH_PAD + " " + " ".join("─" * w for w in widths)
    lines = [head, rule]
    out_rows: list[tuple[int, object, str]] = []
    for (key, kind, _c), values in zip(rows, cells):
        out_rows.append((len(lines), key, kind))
        lines.append(line(ATTRIB_SWATCH, values))
    return TableText(tuple(lines), tuple(out_rows), len(head))


# ---------------------------------------------------------------------------
# Pure rendering: the contributions table
# ---------------------------------------------------------------------------

def _quantity_columns(dec) -> list[str]:
    """
    The value-column titles for this quantity: one for a real reading, two for
    a complex one.

    `Z` is complex and a complex value cannot be right-aligned as one cell, so
    Re and Im get a column each.  M / ImZ / ReZ / M/L_a / k are real by
    construction (`_QuantitySpec.part` is 're' or 'im'), so they get one.
    """
    part = attrib.DECOMPOSABLE[dec.quantity].part
    if part == "complex":
        return [f"Re {dec.quantity}", f"Im {dec.quantity}"]
    return [dec.quantity]


def _term_values(term, complex_q: bool) -> list[float]:
    c = term.contribution
    return [c.real, c.imag] if complex_q else [c.real]


def _fold_terms(dec, fold: bool = True) -> tuple[list, list]:
    """
    (shown, folded): the element terms strongest first, with the negligible
    tail split off.

    `fold=False` keeps every term -- but STILL RANKED.  The exported report
    uses it, and a report in declaration order beside a screen in strength
    order is two different answers to "which of these matters", printed from
    the same data.

    Three exemptions, each one a rule this repo already pays for elsewhere:

      * a term whose contribution is NOT FINITE is never folded, and it sorts
        LAST.  NaN is a missing measurement, not a small number -- a probe with
        no return path, a port past its SRF -- and it is the one row the reader
        most needs to see.  `rank_coupling_pairs` makes the identical
        distinction for the same reason.
      * the STRONGEST term is never folded.  Unlike `rank_coupling_pairs`,
        whose floor is an ABSOLUTE -60 dB and therefore needs an explicit
        rescue, this floor is RELATIVE to the strongest term, so the strongest
        can never fall below it -- the exemption holds by construction and
        there is deliberately no `if not shown` branch to be dead code.  The
        two early returns above are what make that true: an empty list and an
        all-NaN / all-zero list never reach the comparison at all.
      * the BARE EM term is not in this list.  It is the baseline every other
        term is a correction to, so `contributions_table` emits it first,
        unranked.

    Magnitude appears HERE AND NOWHERE ELSE.  Every printed cell keeps its
    sign, exactly as the coupling report's ranked pair list does.
    """
    elems = [t for t in dec.terms if t.element is not None]
    if not elems:
        return [], []

    def key(t):
        m = abs(t.contribution)
        # +inf, not 0.0.  An element whose contribution is exactly zero is a
        # real and common reading -- a lumped element the reduction
        # annihilates sums to exactly 0 (`inert_lumped_messages` in core is
        # about that case) -- and its key is -0.0, which COMPARES EQUAL to
        # 0.0.  Keyed at 0.0 a NaN would therefore tie with every inert
        # element and land above it on a stable sort, i.e. a missing
        # measurement printed above a measured zero.
        return -m if math.isfinite(m) else float("inf")

    ordered = sorted(elems, key=key)
    if not fold:
        return ordered, []
    strongest = abs(ordered[0].contribution)
    if not math.isfinite(strongest) or strongest == 0.0:
        return ordered, []
    floor = CONTRIB_FLOOR * strongest
    shown, folded = [], []
    for t in ordered:
        m = abs(t.contribution)
        # `m < floor` on a NaN is already False, but the test is written
        # positively rather than as `not (m >= floor)` so that the NaN case is
        # a stated intention and not an accident of IEEE comparison -- the
        # negated spelling is the natural slip and it folds every NaN away.
        (folded if (math.isfinite(m) and m < floor) else shown).append(t)
    return shown, folded


def contributions_table(dec, units_mode: str = "smart",
                        fold: bool = True) -> TableText:
    """
    The decomposition as a monospace table: the bare EM coupling, then one
    signed term per declared element, strongest first.

    Pure.  `dec` is an `attrib.Decomposition`; nothing here touches a widget.
    """
    complex_q = attrib.DECOMPOSABLE[dec.quantity].part == "complex"
    bare = dec.direct_term
    shown, folded = _fold_terms(dec, fold)
    ordered = ([bare] if bare is not None else []) + list(shown)
    if not ordered:
        # `decompose` empties `terms` when the reconciliation says the two
        # algorithms disagree about the answer itself.  An empty table under a
        # heading would read as "no elements"; say which of the two it is, and
        # point at the strip that has the number.
        return TableText(
            (f"{_SWATCH_PAD} (no per-element split — see the reconciliation "
             f"line above; the TOTAL above it still stands)",), (), 0)

    vals = [_term_values(t, complex_q) for t in ordered]
    titles = _quantity_columns(dec)
    fmts = []
    for j, title in enumerate(titles):
        suffix, fn = _value_fmt([v[j] for v in vals], dec.unit, units_mode)
        fmts.append((title + (f" {suffix}" if suffix else ""), fn))

    columns = [Column("element", "<", ELEMENT_COL_CHARS),
               Column("from", "<", SOURCE_COL_CHARS)]
    columns += [Column(t) for t, _f in fmts]
    columns += [Column("share"), Column("quad")]

    rows: list[tuple[object, str, list[str]]] = []
    for t, v in zip(ordered, vals):
        kind = "" if t.element is None else t.element.kind
        src = "" if t.element is None else (t.element.source or "")
        key = None if t.element is None else t.element.index
        cells = [t.label, src]
        cells += [fn(x) for (_ttl, fn), x in zip(fmts, v)]
        cells += [_pct(t.share_inline), _pct(t.share_quad)]
        rows.append((key, kind, cells))

    table = render_table(columns, rows)
    lines = list(table.lines)
    if folded:
        # One line, and it says how much of the answer it is hiding.  The
        # pointer to Export CSV is TRUE: `csv_records` enumerates every term
        # off the decomposition and has no floor -- do not give it one.
        total = sum((t.contribution for t in folded), start=complex(0.0))
        share = sum(t.share_inline for t in folded
                    if math.isfinite(t.share_inline))
        lines.append(
            f"{_SWATCH_PAD} … {len(folded)} more terms below "
            f"{CONTRIB_FLOOR:g} of the strongest, "
            f"{signed_str(format_si(total.real, _display_unit(dec.unit)))} "
            f"together ({_pct(share)}); all of them are in Export CSV")
    return TableText(tuple(lines), table.rows, table.width)


# ---------------------------------------------------------------------------
# Pure rendering: the sensitivity table
# ---------------------------------------------------------------------------

def sensitivity_table(results: Sequence, units_mode: str = "smart",
                      kinds: Optional[dict] = None) -> TableText:
    """
    Every element against every candidate, biggest change first.

    `results` is a list of `attrib.SensitivityResult`; `kinds` maps element
    index -> element kind so the rows can keep the Ports & Roles colour they
    have in the contributions table.  Pure.
    """
    kinds = kinds or {}
    rows_in = sorted(results,
                     key=lambda r: (-r.abs_delta
                                    if math.isfinite(r.abs_delta)
                                    else float("-inf")))
    unit = rows_in[0].unit if rows_in else ""
    newv = [r.new_value.real for r in rows_in]
    delv = [r.delta.real for r in rows_in]
    new_sfx, new_fn = _value_fmt(newv, unit, units_mode)
    del_sfx, del_fn = _value_fmt(delv, unit, units_mode)

    columns = [Column("element", "<", ELEMENT_COL_CHARS),
               Column("candidate", "<", SOURCE_COL_CHARS),
               Column("new value" + (f" {new_sfx}" if new_sfx else "")),
               Column("Δ" + (f" {del_sfx}" if del_sfx else "")),
               Column("Δ dB")]

    rows: list[tuple[object, str, list[str]]] = []
    for r in rows_in:
        e = r.elements[0] if r.elements else None
        db = ("--" if not math.isfinite(r.delta_db)
              else signed_str(f"{r.delta_db:.2f}"))
        rows.append((e, kinds.get(e, ""),
                     [r.label, r.alternative, new_fn(r.new_value.real),
                      del_fn(r.delta.real), db]))
    return render_table(columns, rows)


# ---------------------------------------------------------------------------
# Pure rendering: the detail pane
# ---------------------------------------------------------------------------

def _wrap(text: str, width: int) -> list[str]:
    """
    Greedy word wrap for the monospace detail pane.

    `textwrap` would do, but this pane is fixed-pitch and the only thing that
    matters is the character count -- and keeping the dependency list of this
    module to what it already imports is cheap.
    """
    words, out, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out or [""]


def detail_lines(dec, key, sens: Sequence = (),
                 group: Optional[tuple] = None) -> list[str]:
    """
    The lines under the sash for the selected row.

    `key` is the element index, or None for the bare EM row / no selection.
    `group` is (label, n_members) when the element belongs to a multi-member
    group, which is what the sweep beside these lines actually sweeps.
    """
    if key is None:
        bare = dec.direct_term
        if bare is None:
            return ["Select a row above to see its current, its "
                    "transimpedance and what would change it."]
        u = _display_unit(dec.unit)
        out = [
            "bare EM coupling",
            "",
            "  What the file already has with every non-probe port OPEN.",
            "  It carries no element current and no baseline transimpedance,",
            "  so both read '--' rather than a plausible zero.",
            "",
            f"  contribution   {signed_str(format_si(bare.contribution.real, u))}",
            f"  share          {_pct(bare.share_inline)}",
            "",
            "  Nothing in the spec can move this term. It is the number every",
            "  other row is a correction to.",
            "",
            "Return path",
            "",
        ]
        # The return-path budget lives HERE and not in the footer.  It is a
        # sentence-long paragraph and the footer is one clipped line; it also
        # belongs with the bare EM term, since "where does the aggressor's
        # current come back" is the question that decides whether a
        # forward-minus-return story can be told from the declared elements at
        # all.
        out += ["  " + s for s in _wrap(dec.return_budget.note, 66)]
        return out

    term = next((t for t in dec.terms
                 if t.element is not None and t.element.index == key), None)
    if term is None:                                     # pragma: no cover
        return ["(that element is not in this decomposition)"]

    el = term.element
    u = _display_unit(dec.unit)
    out = [
        f"{el.describe()}     [{el.kind}"
        + (", ideal" if el.ideal else ", lumped") + "]",
        "",
        f"  declared by      {el.source or '(kind only — no row provenance)'}",
        f"  contribution     {signed_str(format_si(term.contribution.real, u))}"
        f"   ({_pct(term.share_inline)} of the total, "
        f"{_pct(term.share_quad)} at right angles)",
        f"  element current  {_cplx(term.current, 'A')}"
        "        (per 1 A into the aggressor)",
        f"  transimpedance   {_cplx(term.trans_z, 'Ohm')}"
        "     (baseline r_a, victim-side)",
        "",
        "  contribution = − current × transimpedance. Both factors are real",
        "  physical quantities, which is what makes the term exact rather",
        "  than a linearisation.",
    ]
    if group and group[1] > 1:
        out += ["",
                f"  group '{group[0]}' has {group[1]} elements. The sweep "
                "beside this",
                "  moves the WHOLE group together — with n ground balls every",
                "  one-at-a-time delta is about zero because the other n−1",
                "  already carry the return."]
    if sens:
        out += ["", "  Candidates (each row is a full re-solve, not a slope):"]
        for r in sens:
            db = ("--" if not math.isfinite(r.delta_db)
                  else signed_str(f"{r.delta_db:.2f}") + " dB")
            out.append(
                f"    {r.alternative:<14} "
                f"{signed_str(format_si(r.new_value.real, u)):>14}   "
                f"Δ {signed_str(format_si(r.delta.real, u)):>14}   {db}")
    return out


# ---------------------------------------------------------------------------
# Pure rendering: the sweep's POLE, and the axis it forces
# ---------------------------------------------------------------------------
#
# WHY ANY OF THIS EXISTS.  The sweep is a Mobius map of the added series
# impedance, so it has one pole per swept element, and at that pole the added
# element ANTI-RESONATES with the network's own reactance.  Drawn raw, the pole
# owns the picture: measured on the shipped fixture (coupled_4port_diff.s4p,
# probes 1/3, grounds 2/4, 5.1 GHz, sweeping BOTH grounds as one group) the
# analytic interval is (-394 uH, +375 uH) against endpoints of +821 pH and
# +203 pH, the y axis read `1e-10` over a single vertical spike, and the
# headline number under it was the tool describing its own arithmetic rather
# than the structure.  A picture that conveys nothing beside a correct sentence
# is worse than no picture.
#
# A POLE IS A REAL FEATURE, SO IT IS LABELLED, NEVER HIDDEN.  What changes is
# only which part of the curve the axis is scaled to.

#: A sample counts as "at the pole" while it is outside the [ideal, open]
#: bracket by more than this fraction of the bracket's own span.
#:
#: A tolerance is needed and cannot be zero: the curve APPROACHES both
#: endpoints asymptotically from outside, so "outside the bracket" is true of
#: very nearly every sample and the contiguous run around the pole then ran
#: from the first sample to the last.  Measured on the fixture above, sweeping
#: ground port 2: with no tolerance the excluded window was
#: [9.69 pH, 969 uH] -- the whole swept range -- and at 5% it is
#: [9.41 nH, 997 nH], i.e. about one decade either side of a pole at 96.9 nH.
POLE_BRACKET_TOL = 0.05

#: How far either side of the CLOSED-FORM pole location the seed sample is
#: looked for, as a factor in the swept parameter.  The pole itself is never
#: found by scanning -- it is `t = -lam_j`, straight off the partial fractions
#: `sweep_mobius` already returns -- but the WIDTH of its excursion on a log
#: sample grid is a rendering question, and a grid of 160 points over 8 decades
#: puts about 20 samples inside this factor.
POLE_SEED_FACTOR = 2.0

#: Margin added above and below the pole-free interval, as a fraction of it.
SWEEP_Y_PAD = 0.12

#: What a CONSTANT sweep's margin is a fraction of, when there is no span to
#: take a fraction of.
#:
#: A number, not a quantity -- and that is the whole point of it having a name.
#: The margin used to fall back to `max(abs(hi), abs(lo), 1.0)`, and that bare
#: `1.0` is one HENRY in an expression whose other terms are picohenries.
#: MEASURED on decap_4port.s4p (ordinary mode 6, probes 1/2, `gnd_ports="3,4"`,
#: 5 GHz, either ground row): the sweep is exactly constant -- every residue is
#: 0, so ideal = open = -506.755 nH -- and the axis came out
#: (-120.00005 mH, +119.99995 mH), i.e. **473 602x** the value it was drawn to
#: show.  The curve, the ideal line and the open line all landed on one pixel
#: row, `linear_ticks` was False so the symlog decade locator printed 17
#: labelled decades from -10^0 to +10^0, and the caption beside it correctly
#: read `[-507 nH, -507 nH]  ideal -507 nH  open -507 nH`.  That is item 1's own
#: failure -- a correct sentence beside a picture that conveys nothing -- on a
#: shipped fixture, and the bracket-the-endpoints guard passed it trivially
#: because +-0.12 H brackets everything.
#:
#: With the margin taken as a fraction of the VALUE the same case reads
#: (-567.6 nH, -445.9 nH), 1.24x the value, and `linear_ticks` becomes True.
#: When the value is exactly zero as well the pad is zero, `_scale_sweep_axis`'s
#: `yhi > ylo` guard declines to set any limit, and matplotlib's own autoscale
#: is left in place -- which is the honest answer for a curve that is
#: identically zero.
SWEEP_Y_PAD_FLAT = 0.12

#: While the visible y range reaches no further than this multiple of
#: `linthresh`, the symlog axis is given a LINEAR major locator.
#:
#: MEASURED, and it is not a preference.  matplotlib's symlog locator ticks at
#: DECADES, so on a sub-decade range it produces no labelled tick at all: with
#: ylim (310 pH, 919 pH) the ticks inside the range were `[]` with the default
#: locator and `['500 pH']` with subs=[1,2,5], against
#: `['450 pH', '600 pH', '750 pH', '900 pH']` from MaxNLocator.  Inside
#: `linthresh` the symlog transform IS the identity, so a linear locator places
#: its ticks exactly right there -- this buys the ticks back without changing
#: the axis, and the moment the data really does span decades (a pole-free
#: excursion of 300x was rendered as a check) the decade locator takes over
#: and both endpoints stay distinguishable.
SYMLOG_LINEAR_DECADES = 10.0

_TINY = 1e-300


@dataclass(frozen=True)
class SweepPole:
    """
    One pole of the sweep, at a POSITIVE value of the swept parameter.

    `t` is closed form -- the partial-fraction expansion puts the poles at
    `t = -lam_j` and `sweep_mobius` returns `lam` -- so it is exact whatever
    the sample grid does.  `t_lo` / `t_hi` are the sampled extent of the
    excursion around it and are what the interval excludes; they are a
    RENDERING decision, which is why they are separate fields and not `t`.

    `visible` is False for a pole whose excursion never leaves the bracket in
    this sampling (an over-damped one, or one outside the sampled range).  Such
    a pole is not drawn -- there is nothing on the curve to point at -- but it
    is still in `poles`, because "there are two poles and one of them is off
    the left edge" is a different statement from "there is one pole".
    """
    t: float
    t_lo: float
    t_hi: float
    index: int
    visible: bool


@dataclass(frozen=True)
class SweepPicture:
    """
    Everything the axis and the caption need, resolved once from one `Sweep`.

    Pure: no Tk, no matplotlib, no widget.  The window draws it; the caption
    reads it; the test asserts on it without a display.
    """
    poles: tuple[SweepPole, ...]
    #: The interval over the POLE-FREE portion, in the sweep's own unit.  It
    #: ALWAYS contains both physical endpoints, because `t = 0` and
    #: `t -> infinity` are pole-free by construction and both are added to the
    #: candidate set as exact closed-form values rather than as samples.
    interval: tuple[float, float]
    ylim: tuple[float, float]
    linthresh: float
    linear_ticks: bool
    #: Samples suppressed as pole excursion -- what "runs off the top" means,
    #: counted so the caption can say the curve is off-scale rather than leave
    #: the reader to infer it from a line that leaves the frame.
    n_offscale: int

    @property
    def drawn(self) -> tuple:
        return tuple(p for p in self.poles if p.visible)

    @property
    def clusters(self) -> tuple:
        """
        The visible poles grouped by the EXCURSION they share -- one entry per
        thing to draw, not one per pole.

        Measured on the shipped fixture sweeping both grounds as one group: the
        two poles sit at 96.5 nH and 97.0 nH, 0.5% apart, and produced two
        vertical lines one pixel apart with two rotated labels printed over
        each other -- illegible, and claiming two features where the curve has
        one. They are one excursion by construction (identical `t_lo` /
        `t_hi`), which is exactly the key used here.

        The poles themselves are NOT merged: `poles` still carries both,
        because "there are two poles half a percent apart" is a fact about the
        network and the caption says how many.
        """
        out: list[list[SweepPole]] = []
        for p in self.drawn:
            if out and out[-1][0].t_lo == p.t_lo and out[-1][0].t_hi == p.t_hi:
                out[-1].append(p)
            else:
                out.append([p])
        return tuple(tuple(c) for c in out)


def sweep_pole_locations(sw) -> list[tuple[int, float]]:
    """
    Every pole of the sweep at a POSITIVE parameter value, in closed form.

    `Sweep` is canonically a partial fraction
    `Z(t) = c0 - sum_j residues[j] / (lam[j] + t)`, so the poles are exactly
    `t = -lam[j]` and nothing here looks at a sample.  Only `Re(lam) < 0` puts
    one on the swept half-line; a complex `lam` makes it a near-pole rather
    than a true one, which is why the value is taken from the real part and the
    excursion's width is measured separately.
    """
    out: list[tuple[int, float]] = []
    for j, lam in enumerate(getattr(sw, "lam", ()) or ()):
        t = -complex(lam).real
        if math.isfinite(t) and t > 0.0:
            out.append((j, t))
    out.sort(key=lambda p: p[1])
    return out


def sweep_picture(sw) -> SweepPicture:
    """
    Where the poles are, what the curve does away from them, and the y axis
    that shows both.

    The y limits come from the PHYSICAL ENDPOINTS -- `M(0)` (ideal) and
    `M(inf)` (open), the two numbers a reader opens this pane for -- together
    with the pole-free samples, plus a margin.  The pole is then allowed to run
    off the top, which is the only way both endpoints stay distinguishable on
    the same axis.

    `linthresh` is the LARGER of the two endpoint magnitudes: everything up to
    the biggest number the reader came for is rendered linearly, so an ordinary
    sweep looks exactly as it did, and anything beyond it -- a pole-free
    excursion of decades, or a curve that crosses zero on its way between the
    endpoints -- is compressed logarithmically instead of flattening the
    endpoint band to a line.  It is derived from the data, never a constant.
    """
    complex_q = str(getattr(sw, "part", "re")) == "complex"
    ends = [complex(sw.value_ideal), complex(sw.value_open)]
    end_vals = [v.real for v in ends]
    if complex_q:
        end_vals += [v.imag for v in ends]
    end_vals = [float(v) for v in end_vals if math.isfinite(v)]

    poles: list[SweepPole] = []
    keep_vals: list[float] = []
    n_off = 0
    samples = getattr(sw, "samples", None)
    if samples is not None and len(samples) == 2 and np.size(samples[0]):
        ts_arr = np.asarray(samples[0], dtype=float)
        raw = np.asarray(samples[1])
        probe = np.abs(raw) if complex_q else np.real(raw)
        lo_b, hi_b = float(sw.bracket[0]), float(sw.bracket[1])
        tol = POLE_BRACKET_TOL * max(hi_b - lo_b, abs(hi_b), abs(lo_b), _TINY)
        finite = np.isfinite(probe)
        # A NaN sample is NOT "outside the bracket" -- it is no reading at all,
        # and folding it into a pole window would attribute a missing number to
        # a resonance.  It is dropped from the candidate set instead.
        outside = finite & ((probe > hi_b + tol) | (probe < lo_b - tol))
        mask = np.ones(ts_arr.shape, dtype=bool)
        for j, t_p in sweep_pole_locations(sw):
            near = np.where((ts_arr > t_p / POLE_SEED_FACTOR)
                            & (ts_arr < t_p * POLE_SEED_FACTOR) & outside)[0]
            if near.size == 0:
                poles.append(SweepPole(t_p, t_p, t_p, j, False))
                continue
            seed = int(near[int(np.argmax(np.abs(probe[near])))])
            i0 = i1 = seed
            while i0 - 1 >= 0 and outside[i0 - 1]:
                i0 -= 1
            while i1 + 1 < ts_arr.size and outside[i1 + 1]:
                i1 += 1
            mask[i0:i1 + 1] = False
            poles.append(SweepPole(t_p, float(ts_arr[i0]), float(ts_arr[i1]),
                                   j, True))
        keep = mask & (ts_arr > 0) & finite
        n_off = int(np.count_nonzero(~mask))
        kept = raw[keep]
        vals = list(np.real(kept))
        if complex_q:
            vals += list(np.imag(kept))
        keep_vals = [float(v) for v in vals if math.isfinite(float(v))]

    cands = end_vals + keep_vals
    if cands:
        lo, hi = float(min(cands)), float(max(cands))
    else:                                                # pragma: no cover
        lo = hi = 0.0
    span = hi - lo
    # A CONSTANT sweep has no span, so the margin is a fraction of the VALUE --
    # never of a bare 1.0, which is one henry.  See `SWEEP_Y_PAD_FLAT`.
    pad = (SWEEP_Y_PAD * span if span > 0
           else SWEEP_Y_PAD_FLAT * max(abs(hi), abs(lo)))
    ylim = (lo - pad, hi + pad)

    mag = max([abs(v) for v in end_vals] or [0.0])
    if mag <= 0.0:
        # Both endpoints are exactly zero (or unreadable).  Fall back to the
        # pole-free portion, so a curve that is zero at both ends and non-zero
        # in between still gets an axis; a zero linthresh means "leave it
        # linear", which is what the caller does with it.
        mag = max([abs(v) for v in cands] or [0.0])
    linear = (mag <= 0.0
              or max(abs(ylim[0]), abs(ylim[1])) <= SYMLOG_LINEAR_DECADES * mag)
    return SweepPicture(tuple(poles), (lo, hi), ylim, float(mag), bool(linear),
                        n_off)


def si_tick(value: float, unit: str) -> str:
    """
    One axis tick of the sweep plot, in engineering units.

    Pure, so the formatter's decisions are testable without a figure.  `0` is
    special-cased to a bare `0`: `format_si(0.0, 'H')` is `'0.00 H'`, and a
    zero crossing on a symlog axis wants a tick mark, not three characters of
    false precision.  A non-finite tick reads `--`, the readout's own
    no-reading marker, rather than `nan H`.
    """
    v = float(value)
    if not math.isfinite(v):
        return "--"
    if v == 0.0:
        return "0"
    s = format_si(v, unit)
    return MINUS + s[1:] if s[:1] == "-" else s


def _si_formatter(unit: str):
    """
    `si_tick` as a matplotlib formatter.

    A `FuncFormatter`'s `get_offset()` is `''`, so installing one also clears
    the exponent offset the ScalarFormatter parks in the corner -- which is
    checked by measurement (`get_offset_text().get_text()`), not assumed.
    """
    return mticker.FuncFormatter(lambda v, _pos: si_tick(v, unit))


def pole_label(cluster: Sequence[SweepPole], unit: str) -> str:
    """
    What one drawn pole marker is called: its parameter value, or the range and
    the count when several poles share one excursion.
    """
    if not cluster:                                      # pragma: no cover
        return ""
    # The prefix is only for the singleton: `pole_span` already spells the
    # plural as "…(2 poles)", and "poles  96.5 nH…97 nH (2 poles)" says it
    # twice.
    span = pole_span(cluster, unit)
    return span if len(cluster) > 1 else f"pole  {span}"


def pole_span(cluster: Sequence[SweepPole], unit: str) -> str:
    """
    The parameter value one drawn marker stands for, in words: a single value,
    or the range and the count when several poles share one excursion.
    """
    if not cluster:                                      # pragma: no cover
        return ""
    if len(cluster) == 1:
        return format_si(cluster[0].t, unit)
    lo = min(p.t for p in cluster)
    hi = max(p.t for p in cluster)
    return (f"{format_si(lo, unit)}…{format_si(hi, unit)} "
            f"({len(cluster)} poles)")


def sweep_caption(sw, pic: Optional[SweepPicture] = None) -> list[str]:
    """
    The two facts a sweep curve is read for, plus whatever it warns about.

    `interval` is the headline -- "M lies in [a, b] over any physical ground
    inductance" is something a budget can be written against, in a way a single
    number at one guessed L is not.

    WHEN THERE IS A POLE IN RANGE THE HEADLINE IS THE POLE-FREE INTERVAL, and
    the pole is a statement of its own.  `Sweep.interval` is the analytic
    extremum over the WHOLE half-line, so at a pole it is the resonance:
    measured on coupled_4port_diff.s4p at 5.1 GHz, sweeping both grounds as one
    group, it reads (-394 uH, +375 uH) beside endpoints of +203 pH and
    +821 pH.  Those microhenries are arithmetically exact and are a property of
    the pole, not a range a budget can be written against -- so they are still
    reported, in the pole line, named as what they are.

    With NO pole in range this returns exactly what it always did.

    THE TWO ENDPOINTS ARE PRINTED THE WAY THE INTERVAL BESIDE THEM IS
    COMPUTED.  For a complex quantity (`Z`) `Sweep.interval` and
    `Sweep.bracket` are over the MAGNITUDE, while the endpoints were printed as
    `.real` unconditionally -- measured on coupled_4port_diff.s4p at 5.1 GHz,
    the line read `Z over series inductance … [−2.15 mΩ, +27.5 Ω]
    ideal +6.41 mΩ   open +785 µΩ` while `|value_ideal|` is **26.3 Ω**, i.e.
    the ideal endpoint was reported four orders of magnitude below the interval
    that is supposed to contain it.  A magnitude is labelled `|Z|` so no sign is
    being suppressed -- there is none to suppress -- and every real-valued
    quantity (M, Re Z, Im Z, …) is untouched.

    EVERY LINE LEADS WITH ITS NUMBERS, because this Label is the narrowest
    clipping strip in the window: it sits under the plot, in the RIGHT half of
    a horizontal split, so it is only as wide as the plot is.  MEASURED on the
    real widget with a row selected -- 329 px at 1020x700, 569 px at 1500x900,
    179 px at the 720x420 minimum -- the interval line was 107 characters and
    51 / 93 / 29 of them were visible, i.e. at every size below 1500 px it read
    `M over series inductance ∈ [0, ∞), AWAY FROM THE PO` and the interval, the
    ideal and the open -- the three numbers this pane exists to report -- were
    all off screen, with only `ideal` making it even at 1500 px.  The prose is
    what can go; it is in Copy report in full (rule 12).  Same treatment for
    the pole line, whose LOCATION now comes before its explanation.
    """
    if pic is None:
        pic = sweep_picture(sw)
    u = _display_unit(sw.unit)
    drawn = pic.drawn
    if str(getattr(sw, "part", "re")) == "complex":
        mark = f"|{sw.quantity}| "
        e_val, o_val = abs(sw.value_ideal), abs(sw.value_open)
    else:
        mark = ""
        e_val, o_val = sw.value_ideal.real, sw.value_open.real
    ideal_open = (f"  ideal {mark}{signed_str(format_si(e_val, u))}"
                  f"  open {mark}{signed_str(format_si(o_val, u))}")
    if drawn:
        lo, hi = pic.interval
        out = [f"{sw.quantity} ∈ [{signed_str(format_si(lo, u))}, "
               f"{signed_str(format_si(hi, u))}]" + ideal_open
               + f"  — AWAY FROM THE POLE, over {sw.param_name} ∈ [0, ∞)"]
        where = ", ".join(pole_span(c, sw.param_unit)
                          for c in pic.clusters[:3])
        full_lo, full_hi = sw.interval
        out.append(
            # The parameter is NOT named again here: the value carries its own
            # unit, the x axis two inches away is labelled with it, and the
            # line above says `over <param> ∈ [0, ∞)`.  Naming it a fourth time
            # cost 21 characters of a 56-character budget.
            f"POLE at {where}: the added element "
            "ANTI-RESONATES with the structure there, so the curve LEAVES the "
            "[ideal, open] bracket around it and runs OFF-SCALE — it is drawn "
            "as a vertical line and the interval above excludes it. Over the "
            f"whole half-line, pole included, it is "
            f"[{signed_str(format_si(full_lo, u))}, "
            f"{signed_str(format_si(full_hi, u))}].")
        lo_b, hi_b = sw.bracket
        tol = POLE_BRACKET_TOL * max(hi_b - lo_b, abs(hi_b), abs(lo_b), _TINY)
        if lo < lo_b - tol or hi > hi_b + tol:
            # The pole is not the only thing taking it out of the bracket, so
            # rule 8's label is still owed in its own right.
            out.append("NON-MONOTONIC away from the pole as well: the curve "
                       "LEAVES the [ideal, open] bracket, so those two "
                       "endpoints are not a bound.")
    else:
        lo, hi = sw.interval
        out = [f"{sw.quantity} ∈ [{signed_str(format_si(lo, u))}, "
               f"{signed_str(format_si(hi, u))}]" + ideal_open
               + f"  — over {sw.param_name} ∈ [0, ∞)"]
        if sw.leaves_bracket:
            out.append("NON-MONOTONIC: the curve LEAVES the [ideal, open] "
                       "bracket, so those two endpoints are not a bound.")
    out.extend(sw.notes)
    return out


#: How many CLIPPING lines the sweep caption Label may occupy.
#:
#: THREE, and the third one is not spare.  The label is packed `side=BOTTOM`
#: against an `expand=True` canvas, so every line it asks for comes straight
#: off the plot -- measured on the real window at 1020x700, the canvas is
#: 177 px tall at two lines and 156 at three, against the **90 px** it was
#: pinned at when the whole 957-character caption was wrapped into this label.
#:
#: Two was tried first and was wrong, seen on screen rather than argued: with
#: one line spent on the interval and one on "… +N more", rule 8's MANDATORY
#: `NON-MONOTONIC: the curve LEAVES the [ideal, open] bracket` label was inside
#: the +N.  The caption on the shipped fixture really is five lines
#: (interval / non-monotonicity / two module notes), so the budget has to be
#: interval + the warning + the pointer.  The full caption is in Copy report,
#: which is what `report_text` carries it for.
SWEEP_NOTE_LINES = 3

#: How many lines of the caption's own font the note must leave for the table
#: and the plot to share, whatever the window size.  SEVEN, and every one of
#: the four numbers it is calibrated against was measured on a mapped window.
#:
#: `SWEEP_NOTE_LINES = 3` is a count, and a count is not a budget: the note is
#: packed `side=BOTTOM` against the `expand=True` canvas, so it takes its whole
#: request and the plot gets the remainder.  At 100% three lines are 55 px; at
#: 150% they are 112 px, and the pane they are taking it out of got SMALLER,
#: not bigger, because the chrome above scales too.  MEASURED on
#: coupled_4port_diff.s4p (probes 1/3, grounds 2,4, 5.1 GHz) with an element
#: row SELECTED -- which is the state the pane exists for, and which the
#: existing 150% guard never enters:
#:
#:     scaling  window     paned  detail  note   CANVAS
#:      100%    980x700     497    331     55    309x276
#:      100%    720x420     188     90     55    179x35
#:      150%    980x700     268    114    112    309x2      <- the DEFAULT size
#:      150%    720x678     198     70    112    309x2, winfo_ismapped() == 0
#:
#: Two of those are a plot that conveys nothing -- item 1's whole subject --
#: and the last one also overhangs its 179 px parent by 130 px, the only
#: containment violation in the window.
#:
#: The cap is therefore `budget // line - 7`, where `budget` is
#: `winfo_height() - _chrome_height()`.  Both terms are INDEPENDENT OF THE
#: NOTE -- `_chrome_height` enumerates seven fixed widgets and the note is not
#: among them -- and that is the whole reason the rule is written against the
#: window rather than against the sweep pane, which would have been the obvious
#: place to read it: the pane's height comes from the sash, `_sash_target`
#: reads the bottom pane's REQUESTED height, and the note's request is part of
#: it.  A cap read from the pane would therefore be a rule that changes the
#: size it is measured from -- the `_apply_editor_scrollbars` limit cycle, in
#: which `update()` never returns and the GUI and the test suite hang together.
#: This one cannot: nothing it writes can change `winfo_height()`.
#:
#: Against the table above the cap gives 3 / 3 / 1 / 1 lines, i.e. the two
#: 100% cases are untouched -- including 720x420, where three lines against a
#: 35 px canvas is the documented trade (`the caption is worth the whole
#: pane`) -- and the two broken 150% cases get the plot back.
ATTRIB_SWEEP_NOTE_RESERVE_LINES = 7


def sweep_note_text(caption: Sequence[str], problems: Sequence[str] = (),
                    max_lines: int = SWEEP_NOTE_LINES) -> str:
    """
    What the Label under the sweep actually shows: at most `max_lines` lines.

    PROBLEMS COME FIRST, and that is the whole of the fix for a candidate that
    was refused in silence.  `_alternatives` used to write its problem list
    straight into this Label and `_draw_sweep` then overwrote it later in the
    same `_render()` pass, so `candidate_list("open, R=5 m", omega)` -- which
    produces exactly the `_rlc_tokens` message this repo requires, "'R=5 m'
    would silently mean 5 Ω, not 5 mΩ" -- reached NO widget at all: measured,
    `sweep_note` held the sweep caption, `foot_note` was empty, and the
    Sensitivity table simply dropped from 4 rows to 2 with its own note saying
    "2 rows" and nothing else.

    ORDER IS PRIORITY, because the tail is what goes: a refused candidate, then
    the interval, then rule 8's mandatory non-monotonicity label, then the
    module's own notes.  `sweep_caption` already emits its three in that order.

    The tail is not dropped silently either: whatever does not fit is counted
    and pointed at Copy report, which carries the caption in full.  Pure.
    """
    lines = [str(p) for p in problems] + [str(c) for c in caption]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines - 1]
    kept.append(f"… +{len(lines) - (max_lines - 1)} more — see Copy report")
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Pure rendering: reconciliation (rule 5) and provenance (rule 12)
# ---------------------------------------------------------------------------

def reconciliation_verdict(dec) -> tuple[str, bool]:
    """
    (verdict word, is-good) for the header strip.

    Three states, not two.  "reconciled" means the two algorithms agree inside
    the condition-aware floor.  "NOT reconciled" means the split was withheld
    and the reader must not apportion anything.  "not comparable" is the
    what-if case: `reference_applicable` is False because the engine was never
    asked about this network, so there is no second opinion to have -- which is
    a different thing from a disagreement and must not be printed as one.
    """
    if not dec.reference_applicable:
        return "not comparable", True
    if not dec.split_trustworthy or not dec.terms:
        return "NOT reconciled", False
    if math.isfinite(dec.residual_rel) and \
            dec.residual_rel > dec.residual_floor:
        # Inside RESIDUAL_CATASTROPHIC so the split stands, but outside the
        # condition-aware floor the module can promise -- a real state, and
        # neither of the other two.  `decompose` has already appended a warning
        # naming both numbers.
        return "reconciled (above floor)", True
    return "reconciled", True


def reconciliation_line(dec) -> str:
    """
    The header's one-line verdict, WITH the total -- which is shown even when
    the split is not.

    At the bottom of a scrolling table this line is the first thing off screen,
    and it is what gates trust in everything under it; that is why it is in the
    header.
    """
    verdict, ok = reconciliation_verdict(dec)
    u = _display_unit(dec.unit)
    total = dec.total_reference if dec.reference_applicable else dec.total_sum
    part = attrib.DECOMPOSABLE[dec.quantity].part
    shown = (_cplx(total, dec.unit) if part == "complex"
             else signed_str(format_si(total.real, u)))
    head = verdict
    if not ok:
        # WITHHELD goes HERE, before the total and before the diagnostics.
        # It used to be appended last, after `rel diff … (floor …)`, and that
        # is 45 characters of arithmetic in front of it: measured at 150% DPI
        # the strip fits 52 characters at the 720 px minimum and 74 at the
        # 980 px default, so the word never appeared at either -- while the
        # table underneath read "(no per-element split — see the
        # reconciliation line above)", pointing at a line that no longer
        # carried the reason.  With the clause here the same 150%/720 strip
        # reads `Reconciliation:  NOT reconciled — split WITHHELD   M ` (53
        # chars measured), i.e. the verdict, the reason and the start of the
        # total.  The long-form explanation stays at the tail, where a clip
        # costs an explanation rather than a verdict.
        head += " — split WITHHELD"
    head += f"   {dec.quantity} = {shown}"
    if verdict == "not comparable":
        return (head + "   — a what-if network compute_z_matrix was never "
                "asked about; the arithmetic is still checked on the "
                "declared spec")
    head += (f"   rel diff {dec.residual_rel:.3g} "
             f"(floor {dec.residual_floor:.3g})")
    if not ok:
        head += ("   the two algorithms disagree about the answer itself, so "
                 "apportioning it between elements would be fiction")
    return head


@dataclass(frozen=True)
class Provenance:
    """
    Everything an exported block needs to say which run it belongs to.

    Resolved to plain values at compute time, never held as a reference to the
    live `TraceConfig`: that is the run-snapshot rule.  A window kept open
    across three more Calculates and a relabel would otherwise print the
    newest id, label and port descriptor beside THIS decomposition's numbers,
    which is the failure mode where nothing raises and the numbers are real.
    """
    trace_id: int
    trace_label: str
    file_label: str
    run_number: int
    spec_matches_run: bool
    victim: str
    aggressor: str
    quantity: str
    requested_hz: float
    actual_hz: float
    spec_text: str
    units_mode: str
    #: The ground model IN FORCE, twice over: the SPEC is what the user typed
    #: (and what the field is reloaded with), the LABEL is what the CLI's
    #: parser resolved it to.  Both are frozen at compute time like every other
    #: field here except the units -- a window whose field has been edited but
    #: not Recomputed must still say which model produced the numbers on
    #: screen, which is the whole reason [Recompute] exists.
    ground_model: str = GROUND_MODEL_DEFAULT
    ground_model_label: str = "diag (as declared)"
    #: What `_attr_zt` said about applying it -- the CLI prints these in
    #: `header_notes` and the window used to throw them away
    #: (`zt, _gm_notes = ground_model_zt(...)`).  They are not decoration: the
    #: first of them means the model was NOT APPLIED.  MEASURED on
    #: coupled_4port_diff.s4p, probes 1/3, one connection row `2 short_to 4`
    #: (a legal spelling of the same network) with `shared:L=1n` and
    #: [Recompute]: `_attr_zt` returned `zt is None` with *"The ground model
    #: was ignored: this spec declares no shunt element … there is no ground
    #: lead to model."*, so the numbers stayed the declared network's and
    #: `reference_applicable` stayed True -- while the sign strip read
    #: `Grounds: shared:L=1n` and both exports headed the block
    #: `Ground model: shared:L=1n`.  A reader who typed `shared:L=1n`, saw the
    #: number not move and read that strip concludes the shared return is
    #: worth 0 dB, in the one flow that exists to settle a 6.07 dB dispute.
    ground_model_notes: tuple = ()
    #: False when a model was asked for and `_attr_zt` declined to build it.
    #: The discriminator is free (`gm_z is not None and zt is None`) and it is
    #: what `ground_model_label` renders the IGNORED marker from.
    ground_model_applied: bool = True
    #: `spec_signature(trace)` as it stood when this was computed.  The one
    #: field here that is not a rendered string, and it earns its place: it is
    #: what `staleness_text` compares against on every editor keystroke, and
    #: keeping it beside the numbers it belongs to is what stops the banner
    #: from being computed against some LATER snapshot of the same window.
    signature: tuple = ()
    #: R3-5.  The reference-node check, ALREADY RENDERED, frozen at compute
    #: time like everything else here: `reference_strip` is `(one line, is-a-
    #: warning)` for the strip, `reference_notes` the unabridged verdicts for
    #: the copied report.  Both come from ONE call to
    #: `files_gui.reference_provenance`, so the strip and the report cannot
    #: describe different compositions.
    #:
    #: Empty for a single-file trace -- which is every trace that has ever
    #: existed -- and empty is what costs zero pixels and zero report lines.
    #:
    #: Rendered strings and not the `ReferenceCheck` objects, for the reason
    #: `port_desc` is a resolved string: a window kept open across a re-compose
    #: would otherwise print THIS run's contributions under the NEXT
    #: composition's verdict, which is the run-snapshot failure where nothing
    #: raises and the numbers are real.
    reference_strip: tuple = ()
    reference_notes: tuple = ()


def _freq_phrase(prov: Provenance) -> str:
    """The frequency, with its snap note when the two differ."""
    act = format_freq(prov.actual_hz, 6)
    if not math.isfinite(prov.requested_hz) or \
            prov.requested_hz == prov.actual_hz:
        return act
    return (f"{act}  (asked for {format_freq(prov.requested_hz, 6)}; "
            "read at the nearest data point)")


def provenance_lines(prov: Provenance) -> list[str]:
    """
    The full provenance block, verbatim in every export and in the copied
    report.  Rule 12: a block attributed to the wrong run is a real bug.
    """
    out = [
        f"Attribution of {prov.quantity}: victim '{prov.victim}' "
        f"← aggressor '{prov.aggressor}'",
        f"Trace   : [{prov.trace_id}] {prov.trace_label}   "
        f"file {prov.file_label}",
        f"Run     : #{prov.run_number}"
        + ("" if prov.spec_matches_run else
           "  ! the spec has been EDITED since that run — this block was "
           "computed from the spec as edited, and the plot, the results "
           "table and Export CSV still show the run"),
        f"Read at : {_freq_phrase(prov)}",
        f"Units   : {prov.units_mode}",
        "",
        f"Ground model: {prov.ground_model_label}  (field: "
        f"{prov.ground_model})",
    ]
    # The parser's own notes, verbatim, ahead of the standing explanation --
    # one of them means the model on the line above was NOT the one applied.
    for note in prov.ground_model_notes:
        out.append("    ! " + str(note))
    out += [
        "    " + GROUND_MODEL_TEXT,
    ]
    # R3-5.  BEFORE the sign convention and the spec, because it is a
    # precondition on reading any of the numbers rather than a footnote under
    # them -- the same placement decision `_compose_print_reference` takes on
    # the CLI ("BEFORE the numbers, on purpose").  Nothing is emitted for a
    # single-file trace, so every existing report is byte-identical.
    if prov.reference_notes:
        out.append("")
        out.extend(str(n) for n in prov.reference_notes)
    out += [
        "",
        attrib.SIGN_CONVENTION_TEXT,
        SIGN_NOTE_TERMS + " " + SIGN_NOTE_SHARES,
        "",
        "Termination spec, verbatim (the DSL the numbers were computed from):",
    ]
    for line in (prov.spec_text or "").splitlines() or ["(empty)"]:
        out.append("    " + line)
    return out


def staleness_text(prov: Provenance, trace, exists: bool) -> tuple[str, bool]:
    """
    (banner text, is-a-warning) for the line under the header.

    THE ONLY thing the editor hook updates -- see rule 6.  FOUR states, and
    they are genuinely different: the subject is gone; the spec has moved since
    this decomposition was computed (press Recompute); the decomposition was
    computed from a spec the run itself never saw; or everything agrees.
    Comparing `spec_signature` is a tuple compare, i.e. microseconds, which is
    what makes it safe on a per-keystroke path.

    The THIRD state is the one that was missing, and it is the one rule 6's own
    docstring on `_on_recompute` promises: "the banner, the export and the
    copied report all say the plot and the results table are showing something
    else".  The export and the report did; the banner did not, because
    `spec_signature(trace) != prov.signature` is False immediately after a
    Recompute -- the Recompute just re-captured that signature.  Measured on
    `coupled_4port_diff.s4p`: run #1 gives M = +821 pH, editing GND from "2,4"
    to "2" and pressing Recompute gives +407 pH, i.e. 2.0x what the plot, the
    results table and Export CSV are still showing, and the banner read
    `from run #1 @ 5.1 GHz   ·   M: 'vic' ← 'agg'` in the theme foreground with
    no warning at all.  `spec_matches_run` is the flag that already knew; the
    banner just never asked it.

    A moved signature WINS when both are true: "press Recompute" is the action,
    and it is the more recent of the two facts.
    """
    base = (f"from run #{prov.run_number} @ {format_freq(prov.actual_hz, 6)}"
            f"   ·   {prov.quantity}: '{prov.victim}' ← '{prov.aggressor}'")
    if not exists:
        return (base + "   ·   the trace this describes has been REMOVED. "
                "The numbers below are still the ones that were computed; "
                "nothing here can be recomputed.", True)
    try:
        moved = spec_signature(trace) != prov.signature
    except Exception:                                    # pragma: no cover
        moved = False
    if moved:
        return (base + "   ·   the spec has been EDITED since — press "
                "Recompute to decompose what is on screen now.", True)
    if not prov.spec_matches_run:
        return (base + "   ·   computed from the spec AS EDITED, not from the "
                "run: the plot, the results table and Export CSV are still "
                "showing run "
                f"#{prov.run_number}. Calculate to make them agree.", True)
    return (base, False)


def header_trace_text(prov: Provenance) -> str:
    """
    The header's first item.  Capped -- see HEADER_LABEL_CHARS.

    The id is never trimmed: it is what the Traces list, the results table and
    the CSV all key on, and it is three characters.
    """
    return (f"Trace [{prov.trace_id}] "
            f"{_fit(prov.trace_label, HEADER_LABEL_CHARS)}"
            f"   ·   {_fit(prov.file_label, HEADER_LABEL_CHARS)}")


def stability_offer(n_points: int = STABILITY_POINTS, n_ports: int = 0) -> str:
    """
    The OFF state, and it CARRIES THE ACTION rather than only the caveat.

    "across frequency: not checked" is too soft a default for something the
    acceptance criteria are about: a ranking read off one frequency is a
    statement about that frequency, and the window ships with the check off
    because it costs a re-solve per frequency.  Turning it on unconditionally
    would spend that on every window nobody asks the question of; leaving it as
    a bare caveat leaves the reader with a warning and no way to act on it.  So
    the badge says what the check COSTS and that one click runs it.

    The cost is stated as what it actually is -- N extra `build_context` +
    `decompose` passes, each O(N^3) in the PORT count -- rather than as a time,
    which depends on the box and would be a promise this cannot keep.

    THE ACTION AND THE COST COME FIRST, because the tail of this line is not
    on screen.  The Label is `wraplength=0`, i.e. it CLIPS (that is the
    `_footer_strip_text` rule, and it is right -- a wrapping strip costs plot
    height).  MEASURED on the real widget, the sentence being 238 characters:

        100% 1500 px  238 chars   100% 980 px  156   100% 720 px  111
        150% 1500 px  104 chars   150% 980 px   64   150% 720 px   46

    With the caveat first, at 150% / 980 px -- the DEFAULT size -- the reader
    saw `across frequency: not checked — a ranking read off ONE frequency` and
    nothing else: the gesture and the cost, which are the whole of what item 3
    asked for, were both off screen, and at 100% / 980 px the cost was too
    (the visible text ended `… across the band: 4`).  A badge that carries only
    the caveat is the "not checked" default this was supposed to replace.
    """
    extra = max(0, int(n_points) - 1)
    where = f" ({int(n_ports)}-port file)" if n_ports else ""
    return (f"not checked — press {EXPAND_COLLAPSED}: {extra} more "
            f"solves{where}, re-ranking at {int(n_points)} frequencies across "
            f"the band. A ranking read off ONE frequency is a statement about "
            f"that frequency; this line will then say which ranks moved and "
            f"where.")


def stability_line(freqs: Sequence[float], ranks: Sequence[dict]) -> str:
    """
    The across-frequency badge's verdict, from one ranking per frequency.

    A ranking read off one frequency is a statement about that frequency.  The
    badge says which of the two it is in one line, because as a TAB it would
    never be opened -- which is the whole argument against the notebook.

    ONCE CHECKED IT SAYS WHAT MOVED, not merely that something did.  Naming the
    elements was not enough: "'ground port 2', 'ground port 4' change places"
    leaves the reader to go and find out WHERE, which is the same re-solve they
    just paid for.  Each moved element therefore carries its rank change and
    the FIRST frequency at which it happened.  A stable ranking is a RESULT and
    is said in those words -- an absence of complaint would read as an absence
    of a check.

    BOTH VERDICTS LEAD WITH THE THING THE READER PAID FOR, for the same
    measured reason as `stability_offer`.  MEASURED on the real widget:
    the STABLE verdict is 152 characters and 65 of them are visible at
    150% / 980 px, so `nothing changed places` -- the words item 3 requires --
    clipped; a real NOT-stable verdict off a 32-port file is 219 characters
    with 65 visible, so the moved ranks and their frequencies, which are the
    entire point of the check, were exactly the part that went.  The span and
    the "belongs to <f> only" tail are the parts that can go instead.
    """
    if len(freqs) < 2:
        return stability_offer()
    labels: list[str] = []
    for col in ranks:
        for lab in col:
            if lab not in labels:
                labels.append(lab)
    if not labels:
        return ("no ranking is available at any of these frequencies — the "
                "per-element split was withheld (see the reconciliation "
                "above)")
    primary = format_freq(freqs[0])
    span = f"{format_freq(min(freqs))} … {format_freq(max(freqs))}"
    moved: list[str] = []
    for lab in labels:
        base = ranks[0].get(lab)
        for k in range(1, len(ranks)):
            here = ranks[k].get(lab)
            if here == base:
                continue
            # "absent" is not rank 0 and is not a tie for last: an element
            # whose admittance vanishes at that frequency is DROPPED there, so
            # `stability_ranks` keys on the description and the column simply
            # has no entry.  Printing a number for it would invent one.
            was = "absent" if base is None else f"#{base}"
            now = "absent" if here is None else f"#{here}"
            moved.append(f"'{lab}' {was}→{now} at {format_freq(freqs[k])}")
            break
    if not moved:
        return (f"STABLE — nothing changed places across {len(freqs)} "
                f"frequencies ({span}), so the order above is not a property "
                f"of {primary} alone")
    return ("NOT stable — " + ", ".join(moved[:3])
            + (f" … +{len(moved) - 3} more" if len(moved) > 3 else "")
            + f"; checked across {span}, so the ranking above belongs to "
            + f"{primary} only")


# ---------------------------------------------------------------------------
# Pure rendering: export (rule 12)
# ---------------------------------------------------------------------------

def report_text(prov: Provenance, dec, sens: Sequence = (),
                stability: str = "", sweep: Sequence[str] = (),
                problems: Sequence[str] = ()) -> str:
    """
    The whole window as plain text, for 'Copy report'.

    `sweep` and `problems` are the two things the on-screen labels CLIP: the
    sweep caption is capped at `SWEEP_NOTE_LINES` clipping lines because every
    line it takes comes off the plot beside it, and a refused candidate is one
    sentence in front of that.  Both are unabridged here, which is what makes
    "see Copy report" on the capped label true -- the same contract the
    contributions table's "all of them are in Export CSV" pointer carries.
    """
    out = list(provenance_lines(prov))
    out += ["", "Reconciliation: " + reconciliation_line(dec), ""]
    out += list(contributions_table(dec, prov.units_mode, fold=False).lines)
    if sens:
        out += ["", "Sensitivity — every element against every candidate:", ""]
        out += list(sensitivity_table(sens, prov.units_mode).lines)
    else:
        # Said, not omitted.  A report with no sensitivity section reads as
        # "nothing would move it", which is the opposite of "nobody asked".
        out += ["", "Sensitivity: not run — switch the window to the "
                    "Sensitivity view and export again."]
    if problems:
        out += ["", "Candidates REFUSED (they contribute no row above):"]
        out += ["    " + str(p) for p in problems]
    if sweep:
        out += ["", "Sweep of the selected element:"]
        out += ["    " + str(s) for s in sweep]
    if stability:
        out += ["", "Across frequency: " + stability]
    if dec.notes:
        out += [""] + ["note: " + n for n in dec.notes]
    if dec.warnings:
        out += [""] + ["WARN: " + w for w in dec.warnings]
    out += ["", "Return path: " + dec.return_budget.note]
    return "\n".join(out)


CSV_FIELDS = ("kind", "victim", "aggressor", "quantity", "unit", "element",
              "element_kind", "declared_by", "candidate", "value_re",
              "value_im", "delta_re", "delta_im", "delta_dB", "share_inline",
              "share_quad", "current_re", "current_im", "transz_re",
              "transz_im", "note")


def _e(v) -> str:
    """
    Full double precision, never a display rounding.  A CSV is data.

    NaN and +-inf are written as 'nan' / 'inf' / '-inf' rather than blanked:
    they are readings, not missing cells, and numpy, pandas and every
    spreadsheet read those three tokens back.  Blanking an infinity would turn
    "this probe has no return path" into "we did not measure it".
    """
    try:
        f = float(v)
    except (TypeError, ValueError):                      # pragma: no cover
        return ""
    if math.isnan(f):
        return "nan"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    return f"{f:.12e}"


def csv_records(prov: Provenance, dec, sens: Sequence = ()) -> list[dict]:
    """
    Every term and every candidate as CSV rows.

    NO FLOOR, deliberately: the on-screen table folds its negligible tail into
    one line and points HERE, and that pointer is only true while this
    enumerates the lot.  Same contract as `_write_coupling_csv`.
    """
    rows: list[dict] = []
    rows.append({"kind": "total", "victim": prov.victim,
                 "aggressor": prov.aggressor, "quantity": dec.quantity,
                 "unit": dec.unit,
                 "value_re": _e(dec.total_reference.real),
                 "value_im": _e(dec.total_reference.imag),
                 "element": "(compute_z_matrix)"})
    rows.append({"kind": "total", "victim": prov.victim,
                 "aggressor": prov.aggressor, "quantity": dec.quantity,
                 "unit": dec.unit, "value_re": _e(dec.total_sum.real),
                 "value_im": _e(dec.total_sum.imag),
                 "element": "(sum of terms)",
                 "note": f"residual={dec.residual_rel:.6g};"
                         f"floor={dec.residual_floor:.6g};"
                         f"trustworthy={dec.split_trustworthy}"})
    for t in dec.terms:
        el = t.element
        rows.append({
            "kind": "term", "victim": prov.victim, "aggressor": prov.aggressor,
            "quantity": dec.quantity, "unit": dec.unit, "element": t.label,
            "element_kind": "" if el is None else el.kind,
            "declared_by": "" if el is None else (el.source or ""),
            "value_re": _e(t.contribution.real),
            "value_im": _e(t.contribution.imag),
            "share_inline": _e(t.share_inline),
            "share_quad": _e(t.share_quad),
            "current_re": _e(t.current.real), "current_im": _e(t.current.imag),
            "transz_re": _e(t.trans_z.real), "transz_im": _e(t.trans_z.imag),
        })
    for r in sens:
        rows.append({
            "kind": "sensitivity", "victim": prov.victim,
            "aggressor": prov.aggressor, "quantity": r.quantity,
            "unit": r.unit, "element": r.label, "candidate": r.alternative,
            "value_re": _e(r.new_value.real), "value_im": _e(r.new_value.imag),
            "delta_re": _e(r.delta.real), "delta_im": _e(r.delta.imag),
            "delta_dB": _e(r.delta_db),
        })
    return [{k: rec.get(k, "") for k in CSV_FIELDS} for rec in rows]


# ---------------------------------------------------------------------------
# Candidate terminations
# ---------------------------------------------------------------------------

def parse_candidate(text: str, omega: float):
    """
    'open' / 'ideal' / 'R=0.1 L=1n' -> an `attrib.Alternative`.

    EVERY whitespace token must carry an '=' and it raises otherwise, which is
    the same rule `_rlc_tokens` enforces on an editor cell and for the same
    measured reason: `parse_kv_rlc_params` silently DROPS a token without one,
    so 'R=5 m' computed 5 ohm where 5 milliohm was typed and 'C=1 uF' computed
    one farad.  There is no way to quote a value here either, so refusing is
    the only answer that cannot be quietly wrong.
    """
    tok = (text or "").strip()
    if not tok:
        raise ValueError("empty candidate")
    low = tok.lower()
    if low == "open":
        return attrib.alt_open()
    if low in ("ideal", "short", "0"):
        return attrib.alt_ideal()
    parts = tok.split()
    bad = [p for p in parts if "=" not in p]
    if bad:
        raise ValueError(
            f"'{tok}': '{bad[0]}' has no '='. A candidate is 'open', 'ideal', "
            "or R=…/L=…/C=… with no spaces inside a value — "
            "'R=5 m' would silently mean 5 Ω, not 5 mΩ")
    p = parse_kv_rlc_params(parts)
    z = complex(p["R"], 0.0) + 1j * omega * p["L"]
    if math.isfinite(p["C"]) and p["C"] != 0.0:
        if omega == 0.0:
            return attrib.Alternative(tok, None)
        z += 1.0 / (1j * omega * p["C"])
    return attrib.alt_impedance(z, tok)


def candidate_list(text: str, omega: float) -> tuple[list, list[str]]:
    """
    (alternatives, problems) from a comma-separated field.

    A bad entry costs ITS OWN entry and never the whole field -- the session
    file's rule, and for the same reason: this is free text a user types under
    time pressure and losing the four good candidates because the fifth has a
    typo is not an improvement.
    """
    alts, problems = [], []
    for chunk in (text or "").split(","):
        if not chunk.strip():
            continue
        try:
            alts.append(parse_candidate(chunk, omega))
        except (ValueError, KeyError) as e:
            problems.append(str(e))
    return alts, problems


# ---------------------------------------------------------------------------
# The computed result
# ---------------------------------------------------------------------------

@dataclass
class AttribResult:
    """
    One Recompute's worth of answers, frozen against the live trace.

    No `TraceConfig` and no `FileEntry` is held here.  `prov` resolves the
    identity to strings at compute time; the context holds only what it needs
    for a what-if.  A window kept open across later runs therefore keeps
    describing the decomposition it computed, which is what the run snapshot's
    whole design is about.
    """
    prov: Provenance
    ctx: object
    dec: object
    names: tuple[str, ...]
    signature: tuple
    sens_all: Optional[list] = None
    sens_one: dict = field(default_factory=dict)
    stability: str = ""

    def group_of(self, e: int) -> Optional[tuple]:
        """(label, member indices) for the group the element belongs to."""
        for label, idxs in self.ctx.groups.items():
            if e in idxs:
                return label, tuple(idxs)
        return None

    def kinds(self) -> dict:
        return {el.index: el.kind for el in self.ctx.elements}


#: What a trace is decomposed against, resolved in ONE place.
#:
#: On a single file this is exactly what every call site used to spell inline
#: (`file_entry.Y`, `file_entry.ts.freqs`, `_build_termination(nports=...)`),
#: so nothing about a one-file attribution moves.  On a COMPOSED trace two
#: things change and both are load-bearing:
#:
#:   * the arrays are the STACKED ones and the termination is built against the
#:     composed namespace, so `F2.13` resolves and a bare number past the home
#:     file's ports is refused rather than silently addressing the next file;
#:
#:   * the baseline carries the CROSS-FILE LINKS (requirement R2-8).  An
#:     all-open baseline on a composition leaves the files as disconnected
#:     islands -- Ybase is then exactly block diagonal.  Measured with the real
#:     engine on a 12-port combined network: the EM-vs-PKG off-diagonal block
#:     is 0.000e+00, every package-only element contributes EXACTLY 0, and the
#:     reconciliation residual reads 6.49e-15, i.e. perfect health.  A
#:     confident, exactly-zero, perfectly-reconciled wrong answer is worse than
#:     no answer, which is why there is no way to turn this off -- the same
#:     rule, and the same `PortBlocks.from_sizes` gauge, as the CLI's
#:     `_compose_baseline`.
@dataclass(frozen=True)
class _AttribNetwork:
    Y: object
    freqs: object
    nports: int
    term: object
    baseline: object = None
    composed: bool = False


def _attrib_network(app, trace, file_entry) -> "_AttribNetwork":
    """`_AttribNetwork` for this trace.  Raises what `_build_termination` does."""
    sn = None
    try:
        if _gui().trace_is_composed(trace):
            sn = app._trace_network(trace)
    except AttributeError:                               # pragma: no cover
        sn = None
    if sn is None or not sn.composed:
        return _AttribNetwork(
            Y=file_entry.Y, freqs=file_entry.ts.freqs,
            nports=int(file_entry.ts.nports),
            term=app._build_termination(trace, nports=file_entry.ts.nports))
    return _AttribNetwork(
        Y=sn.Y, freqs=sn.freqs, nports=int(sn.nports),
        term=app._build_termination(trace, nports=sn.nports, sn=sn),
        baseline=attrib.BaselineLinks(
            blocks=attrib.PortBlocks.from_sizes(
                [b.nports for b in sn.net.blocks],
                [b.alias for b in sn.net.blocks])),
        composed=True)


def _attrib_role_rows(app, trace, net: "_AttribNetwork") -> tuple:
    """
    (mports, conn, extra, sources) for this trace, in the network's namespace.

    `row_sources` maps a row's PORT FIELD onto the ports it declares, so on a
    composed trace it has to see the same global numbers the termination was
    built from -- otherwise the From column names a row for port 3 of the die
    while the element it labels is port 3 of the package.
    """
    g = _gui()
    try:
        mports, conn, extra, sources = g._trace_role_rows(trace)
    except Exception:                                    # pragma: no cover
        return [], [], "", None
    if not net.composed:
        return mports, conn, extra, sources
    try:
        sn = app._trace_network(trace)
        mports = g._scope_mport_rows(mports, sn.net, sn.home_alias)
        conn = g._scope_conn_rows(conn, sn.net, sn.home_alias)
        extra = g._scope_dsl_text(extra, sn.net, sn.home_alias)
        sources = row_sources(mports, conn, extra)
    except Exception:                                    # pragma: no cover
        pass
    return mports, conn, extra, sources


def compute_attribution(app, trace, file_entry, victim: str, aggressor: str,
                        quantity: str, freq_hz: float,
                        ground_model: str = GROUND_MODEL_DEFAULT
                        ) -> AttribResult:
    """
    Build the context and decompose, with no widget anywhere in sight.

    Raises `attrib.AttribError` / `ValueError` on anything it cannot answer;
    the caller turns that into a message.  It goes through
    `App._build_termination` and `pkg_rlc_gui._trace_role_rows` rather than
    rebuilding either: the first is the ONE place a `TraceConfig` becomes a
    `TerminationSet` (and the one that passes `nports`, which is what stops a
    one-digit typo becoming a plausible wrong number), and the second is the
    ONE definition of which row declared a port, so the From column here says
    what the Ports & Roles window says.

    THE GROUND MODEL IS A SECOND BUILD, exactly as `--attribute`'s own
    `build()` does it, and the order is load-bearing: the element list is what
    the FIRST build discovers, and the (m, m) element-impedance matrix is sized
    by it.  A dense `Zt` is a DIFFERENT NETWORK -- it is a mutual impedance
    between ground leads and the DSL has no node to hang one on -- so
    `build_context(zt=...)` sets `is_whatif`, keeps `Zop_declared` at the
    DECLARED spec's answer, and `decompose` then reconciles the arithmetic on
    that declared configuration while reporting `reference_applicable=False`.
    That is CLAUDE.md's rule for this case, honoured by using the module's own
    machinery rather than by a second rule here: the check survives, the second
    OPINION does not, and `reconciliation_verdict` prints "not comparable"
    rather than a disagreement.
    """
    net = _attrib_network(app, trace, file_entry)
    term = net.term
    # Without provenance every element of a kind lands in one group named after
    # the kind.  That is a worse table, not a wrong one, so `_attrib_role_rows`
    # degrades instead of failing the whole window.
    mports, conn, extra, sources = _attrib_role_rows(app, trace, net)

    ctx = attrib.build_context(net.Y, net.freqs, term, freq_hz,
                               sources=sources, baseline=net.baseline)
    gm_kind, gm_z, gm_label = parse_ground_model(ground_model, ctx.omega)
    gm_notes: list = []
    gm_applied = True
    if gm_z is not None:
        zt, gm_notes = ground_model_zt(ctx, gm_kind, gm_z)
        # THE NOTES ARE KEPT, and the first thing they can say is "I did not
        # apply it" -- see `Provenance.ground_model_notes`.  The marker goes on
        # the LABEL because the label is what the sign strip, Copy report and
        # the CSV all render, so one assignment reaches every surface that
        # names the model instead of three that can disagree.
        gm_applied = zt is not None
        if zt is not None:
            ctx = attrib.build_context(net.Y, net.freqs, term, freq_hz, zt=zt,
                                       sources=sources, baseline=net.baseline)
        else:
            gm_label = f"{gm_label} — NOT APPLIED"
    dec = attrib.decompose(ctx, victim, aggressor, quantity)

    sig = spec_signature(trace)
    # R3-5.  Resolved HERE, beside the numbers, and frozen onto the Provenance
    # -- not read live by the strip.  `reference_checks_of` returns [] for a
    # trace with one file, so both fields stay empty and every surface below
    # is byte-identical to what it was.  It cannot raise: a defective cache
    # would otherwise take down a window whose decomposition has already been
    # paid for, over a strip.
    try:
        ref_strip, ref_notes = files_gui.reference_provenance(
            files_gui.reference_checks_of(trace))
    except Exception:                                        # pragma: no cover
        ref_strip, ref_notes = (), ()
    prov = Provenance(
        trace_id=int(getattr(trace, "id", 0)),
        trace_label=str(getattr(trace, "label", "")),
        file_label=str(getattr(trace, "file_label", "")),
        run_number=int(app._current_run_number()),
        # `stale` IS the predicate, and no stored signature can replace it.
        # It means exactly "the spec has moved since this trace was last
        # computed", it is cleared by `_on_calculate` and set by every edit, so
        # it stays correct across Calculates that happen while this window is
        # open -- which a signature captured when the window opened would not.
        # [Recompute] is allowed on a stale trace on purpose (refusing would
        # mean a full Calculate of every trace just to re-attribute); what it
        # may not do is keep claiming the run, and this is the flag that stops
        # it, all the way through to the CSV header.
        spec_matches_run=not bool(getattr(trace, "stale", False)),
        victim=str(victim), aggressor=str(aggressor), quantity=dec.quantity,
        requested_hz=float(freq_hz), actual_hz=float(ctx.freq_hz),
        spec_text=rows_to_dsl_text(mports, conn, extra),
        units_mode=str(app.units_mode_var.get()),
        ground_model=str(ground_model),
        ground_model_label=str(gm_label),
        ground_model_notes=tuple(str(n) for n in gm_notes),
        ground_model_applied=bool(gm_applied),
        signature=sig,
        reference_strip=ref_strip,
        reference_notes=ref_notes,
    )
    return AttribResult(prov=prov, ctx=ctx, dec=dec,
                        names=tuple(ctx.port_names), signature=sig)


def stability_ranks(app, trace, file_entry, res: AttribResult,
                    n_points: int = STABILITY_POINTS) -> tuple[list, list]:
    """
    (frequencies, one rank map per frequency) for the across-frequency badge.

    Log-spaced over the file's band, snapped to the grid, deduplicated AFTER
    snapping -- two requested frequencies inside one sweep step are one column,
    and pretending otherwise shows a ranking "confirmed" against itself.  The
    primary is always first, because it is the ranking every other part of this
    window was built from.
    """
    net = _attrib_network(app, trace, file_entry)
    # The COMPOSED axis on a composition: the badge snaps its sample points
    # onto the grid the decomposition lives on, and the home file's grid is
    # neither the same points nor the same span.
    freqs_all = np.asarray(net.freqs, dtype=float)
    lo = float(freqs_all[freqs_all > 0].min()) if np.any(freqs_all > 0) \
        else float(freqs_all.min())
    hi = float(freqs_all.max())
    want = [res.prov.actual_hz]
    if hi > lo:
        want += list(np.logspace(math.log10(lo), math.log10(hi),
                                 max(2, n_points - 1)))
    picked: list[float] = []
    for f in want:
        snapped = float(freqs_all[int(np.argmin(np.abs(freqs_all - f)))])
        if snapped not in picked:
            picked.append(snapped)
    picked = picked[:n_points]

    term = net.term
    sources = _attrib_role_rows(app, trace, net)[3]

    ranks: list[dict] = []
    for f in picked:
        if f == res.prov.actual_hz:
            dec = res.dec
        else:
            ctx = attrib.build_context(net.Y, net.freqs, term, f,
                                       sources=sources, baseline=net.baseline)
            # The GROUND MODEL travels with the check.  Without this the badge
            # ranks the DECLARED network at four frequencies against the
            # modelled network at the fifth, and reports the difference as
            # frequency instability -- an answer about the model, printed
            # under a heading that says frequency.  The impedance is re-parsed
            # per frequency because `L=1n` is a different ohm value at each.
            gm_kind, gm_z, _lbl = parse_ground_model(res.prov.ground_model,
                                                     ctx.omega)
            if gm_z is not None:
                zt, _n = ground_model_zt(ctx, gm_kind, gm_z)
                if zt is not None:
                    ctx = attrib.build_context(
                        net.Y, net.freqs, term, f, zt=zt, sources=sources,
                        baseline=net.baseline)
            dec = attrib.decompose(ctx, res.prov.victim, res.prov.aggressor,
                                   res.dec.quantity)
        # Keyed by the element DESCRIPTION, not by index: a lumped element
        # whose admittance vanishes at one frequency is dropped there, so the
        # element LISTS can legitimately differ between columns.
        order = sorted([t for t in dec.terms if t.element is not None],
                       key=lambda t: -abs(t.contribution))
        ranks.append({t.label: i + 1 for i, t in enumerate(order)})
    return picked, ranks


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

#: Live windows, per App.  A WeakKeyDictionary so two Apps in one process (the
#: test suite does exactly that) cannot see each other's windows and so nothing
#: keeps a destroyed App alive.  The lists are pruned on every walk, because a
#: Toplevel destroyed by its master's teardown never runs our <Destroy>
#: handler.
_LIVE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

#: Choices restored from a session file, per App: trace id -> the victim /
#: aggressor / quantity / frequency that window was reading.  A session file
#: holds no numbers (json cannot carry a numpy array and the run-snapshot rule
#: forbids it anyway), so nothing is reopened -- but the CHOICES are worth
#: keeping, and `open_attribution_window` preselects them.
_RESTORED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def live_windows(app) -> list:
    """Every AttributionWindow still alive for `app`, pruned."""
    wins = _LIVE.get(app, [])
    alive = []
    for w in list(wins):
        try:
            if w.winfo_exists():
                alive.append(w)
        except Exception:                                # pragma: no cover
            pass
    _LIVE[app] = alive
    return alive


class AttributionWindow(tk.Toplevel):
    """
    Where a mutual impedance comes from, and what would move it.

    Modeless, no `grab_set`, and deliberately NOT `transient(app)` -- see
    decision 1 in the module docstring.

    It follows PortRolesWindow's contract otherwise: every callback guards on
    `winfo_exists()`, nothing that can be reached from a Tk variable trace
    raises, and nothing writes a `TraceConfig`.  Coalescing is INHERITED rather
    than repeated -- `refresh_banner` is called from `_apply_editor_strips`,
    which is itself `after_idle`-coalesced, so a second timer here would only
    add latency to a tuple comparison.  The one thing this window schedules of
    its own accord is the across-frequency check, and that is a user click.
    """

    def __init__(self, app, trace, file_entry, res: AttribResult):
        super().__init__(app)
        self.app = app
        self._trace = trace
        self._file = file_entry
        self._res = res
        self._view = tk.StringVar(value="contrib")
        self._selected = None            # element index, or None (bare EM)
        self._expanded = False
        self._sweep_drawn = False
        # The one pending `after` this window can own.  It is cancelled on
        # destroy: an un-cancelled callback fires on a dead widget and Tk
        # prints "invalid command name ..." to a console a double-clicked GUI
        # does not have -- noise in a test run, invisible in production, and
        # a leak of this object either way.
        self._stability_after = None
        self._contrib_rows: tuple = ()
        #: Whatever `candidate_list` refused, last time `_alternatives` ran.
        #: A list, not a widget write -- see `_alternatives`.
        self._cand_problems: list = []
        #: The UNCAPPED sweep caption, for Copy report.  The Label shows at
        #: most `_sweep_note_cap()` lines of it.
        self._sweep_full: list = []
        #: What was last handed to `_set_sweep_note` -- the caption, a refusal,
        #: or nothing.  A resize re-renders THIS at the new cap; it must not
        #: fall back to `_sweep_full`, which on a pane showing "select an
        #: element row" would resurrect the previous selection's caption.
        self._sweep_shown: list = []
        #: The last `SweepPicture` drawn -- the poles, the pole-free interval
        #: and the axis they force.  None while nothing is drawn, so the
        #: caption cannot be handed a picture from the previous selection.
        self._sweep_pic: Optional[SweepPicture] = None
        #: The last minimum height applied, so the `<Configure>` handler can
        #: tell "nothing changed" from "recompute" without calling minsize()
        #: on every mouse move, and the one pending `after_idle` that handler
        #: may own (coalesced, and cancelled on destroy -- see `_on_configure`).
        self._min_h_applied = 0
        self._min_h_after = None
        #: The split (item 2).  `_sash_user` is set by a DRAG and never by
        #: anything automatic: once the reader has moved the divider it is
        #: theirs until the window closes.  `_sash_lines` is the rendered line
        #: count the current position was derived from, so a new decomposition
        #: with a different number of rows re-derives it and a repaint of the
        #: same one does not.  `_sash_press` is the position at ButtonPress,
        #: which is what makes "was that a drag?" answerable.
        self._sash_user = False
        self._sash_lines = 0
        self._sash_press = None
        self._sash_after = None
        #: How many times `_apply_sash` has WRITTEN a position.  Sampled at
        #: ButtonPress so a release can tell a drag from our own write landing
        #: mid-gesture -- see `_on_sash_release`.
        self._sash_writes = 0
        self._sash_press_writes = 0

        self.title(f"{ATTRIB_TITLE}: {res.prov.victim} ← {res.prov.aggressor}"
                   f"   [{res.prov.trace_id}] {res.prov.trace_label}")
        self.geometry(ATTRIB_GEOMETRY)
        self.minsize(ATTRIB_MIN_W, ATTRIB_MIN_H)

        self._build_ui()
        self._render()
        _LIVE.setdefault(app, [])
        _LIVE[app].append(self)
        self.bind("<Destroy>", self._on_destroy)
        self.bind("<Configure>", self._on_configure)
        # NO <Escape> BINDING.  A Toplevel is in every descendant's bindtags,
        # so `self.bind("<Escape>", ...destroy)` fires from anywhere inside the
        # window: measured, Escape typed in the Freq entry, in either port
        # combobox, in the table and on the [Recompute] button all destroyed it
        # (the one exception being an OPEN combobox popdown, which grabs the
        # key itself).  `PortRolesWindow` binds it and is right to -- it is a
        # read-only list that rebuilds itself from live state on reopen -- but
        # this window HOLDS a result: a Recompute, and if the badge was
        # expanded, five more `build_context` + `decompose` passes that are
        # O(N^3) in the port count.  Backing out of a half-typed frequency with
        # the key everyone uses for that would throw the lot away, and nothing
        # restores it (`_RESTORED` is only ever filled from a session file).
        # The Close button and the window manager's own close box remain.

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        g = _gui()
        warn_fg, hint_fg = g.WARN_FG, g.PLACEHOLDER_FG

        # PACK ORDER (rule 10).  pack allocates in call order and UNMAPS from
        # the END, so every fixed-height section claims its space before the
        # expanding one.  The footer first, so the buttons are unconditional;
        # then the header and the three strips; then the PanedWindow last with
        # expand=True, which is what makes the TABLE the thing that gives up
        # height when the window is dragged short.
        # Kept as an attribute: `_chrome_height` has to be able to ask what the
        # fixed sections cost, and a frame nobody holds cannot be asked.
        self._foot = foot = ttk.Frame(self)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Close", command=self.destroy
                   ).pack(side=tk.RIGHT, padx=2)
        self.copy_btn = ttk.Button(foot, text="Copy report",
                                   command=self._on_copy)
        self.copy_btn.pack(side=tk.RIGHT, padx=2)
        self.csv_btn = ttk.Button(foot, text="Export CSV…",
                                  command=self._on_export_csv)
        self.csv_btn.pack(side=tk.RIGHT, padx=2)
        # LAST in the row, so at a narrow width it is the status line that goes
        # and never one of the three buttons.  `wraplength=0` so it CLIPS
        # rather than wraps: measured, this label held the return-path
        # paragraph and wrapped to two lines, which took the footer from 39 px
        # to 55 -- 16 px straight out of a pane budget that at the 720x420
        # minimum is 168 px for the whole split.  It is a STATUS line now
        # ("report copied", "wrote <path>"); the return-path budget moved to
        # the detail pane's bare-EM view and to the exported report, where it
        # cannot clip.
        self.foot_note = ttk.Label(foot, foreground=hint_fg, justify=tk.LEFT,
                                   wraplength=0)
        self.foot_note.pack(side=tk.LEFT)

        # --- the fixed header.  A ReflowRow, not a pack(side=LEFT) run: the
        # six controls do not fit on one line at 980 px and pack would simply
        # unmap [Recompute], with no scrollbar and no other route to it. Place
        # also keeps the strip's requested width out of the Toplevel, so the
        # header cannot force the window wider than the user set it.
        self.header = ReflowRow(self)
        self.header.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))

        self.trace_lbl = ttk.Label(self.header, text="")
        self.header.add(self.trace_lbl, padx=4)
        self.victim_var = tk.StringVar(value=self._res.prov.victim)
        cell, self.victim_cb = self._combo_cell("Victim:", self.victim_var,
                                                self._res.names, 12)
        self.header.add(cell, padx=4)
        self.aggr_var = tk.StringVar(value=self._res.prov.aggressor)
        cell, self.aggr_cb = self._combo_cell("Aggressor:", self.aggr_var,
                                              self._res.names, 12)
        self.header.add(cell, padx=4)
        self.quantity_var = tk.StringVar(value=self._res.dec.quantity)
        cell, _cb = self._combo_cell("Quantity:", self.quantity_var,
                                     QUANTITIES, 8)
        self.header.add(cell, padx=4)
        self.freq_var = tk.StringVar(
            value=f"{self._res.prov.requested_hz / 1e9:.6g}")
        self.header.add(self._freq_cell(), padx=4)
        self.recompute_btn = ttk.Button(self.header, text="Recompute",
                                        command=self._on_recompute)
        self.header.add(self.recompute_btn, padx=6)

        # --- the GROUND MODEL (item 4).  A ROW OF ITS OWN, not a seventh
        # header item: measured, the six header items ask 961 px against the
        # 964 px strip at the 980 default, so a seventh wraps the ReflowRow to
        # two rows (29 px -> 58) at 980 and to three (58 -> 87) at 720, while
        # this row costs 25 px at 100% and 37 px at 150% at EVERY width.  It
        # also keeps the one-line note beside the control it describes, which
        # a ReflowRow cannot promise -- its items wrap as units.
        #
        # The Entry takes the CLI's spelling verbatim (`diag`, `diag:L=1n`,
        # `shared:L=0.3n`) and is parsed by the CLI's own parser -- see
        # `parse_ground_model`.  <Return> recomputes, like the Freq field: a
        # ground model that changed the answer without going through
        # [Recompute] would be exactly the auto-refresh rule 6 refuses.
        #
        # PACK ORDER: label, entry, then the hint LAST, so at a narrow width it
        # is the sentence that goes and never the field -- the same mistake the
        # candidates row was measured making, in reverse.
        self._gm_row = gm = ttk.Frame(self)
        gm.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 0))
        ttk.Label(gm, text="Grounds:").pack(side=tk.LEFT)
        self.ground_var = tk.StringVar(value=self._res.prov.ground_model)
        self.ground_entry = ttk.Entry(gm, textvariable=self.ground_var,
                                      width=13)
        self.ground_entry.pack(side=tk.LEFT, padx=(3, 6))
        self.ground_entry.bind("<Return>", lambda _e: self._on_recompute())
        # The hint is an ATTRIBUTE, because it is also where `_attr_zt`'s notes
        # land: the standing "why the default is not obviously right" sentence
        # is worth saying until there is something more urgent to say in the
        # same place, and "the model you typed was not applied" is that.  One
        # widget, no extra pixels -- see `Provenance.ground_model_notes`.
        self._gm_hint = ttk.Label(gm, text=GROUND_MODEL_HINT,
                                  foreground=hint_fg, wraplength=0, anchor="w")
        self._gm_hint.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._gm_hint_fg = hint_fg
        self._gm_warn_fg = warn_fg

        # --- provenance / staleness banner (rule 6): the ONE thing the
        # editor hook writes.
        #
        # All three strips below are ONE LINE and `wraplength=0`, i.e. they
        # CLIP.  That is the `_footer_strip_text` rule and it is here for the
        # same measured reason: a wrapping label is 21 px at 980 and 38 px at
        # 720, and there are three of them, so wrapping costs 51 px of a pane
        # budget that at the minimum size is 168 px for the entire split -- and
        # it costs it exactly when the window is smallest.  Everything a
        # clipped tail could have said is in Copy report and in the CSV, both
        # of which carry the declaration in full (rule 12).  What must never be
        # truncated is the front of the line, so each one leads with its
        # verdict and its number.
        self.banner = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                wraplength=0)
        self.banner.pack(side=tk.TOP, fill=tk.X, padx=8)
        self._banner_warn_fg = warn_fg
        # '' is not "no colour", it is "whatever the ttk STYLE says", which is
        # the only way back to the theme default once a warning has painted a
        # Label orange.  Measured: a fresh ttk.Label reports foreground '' and
        # configure(foreground='') is accepted and restores it, so no orphan
        # widget has to be built to read the default off.
        self._banner_ok_fg = ""

        # --- the sign convention, stated ONCE, before any signed number
        # (rule 4).  Ordered by what must survive the clip: the sign rule, then
        # the shares rule, then the ground model.
        self.sign_lbl = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                  wraplength=0, foreground=hint_fg,
                                  text=sign_strip_text())
        self.sign_lbl.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 0))

        # --- reconciliation (rule 5): in the HEADER, because it gates trust in
        # everything under it and at the foot of a scrolling table it is the
        # first thing off screen.
        self.recon = ttk.Label(self, anchor="w", justify=tk.LEFT,
                               wraplength=0)
        self.recon.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 0))

        # --- R3-5: the reference-node check, DIRECTLY UNDER RECONCILIATION
        # and above everything it qualifies.  Created here so its pack order is
        # fixed, but NOT PACKED: it is packed only when there is a composition
        # to report, with `before=self._badge_row` so it lands here whenever it
        # arrives.
        #
        # Not packed, rather than packed-with-empty-text.  MEASURED: a ttk.Label
        # reports `winfo_reqheight() == 21` whether or not it is managed, so an
        # empty one packed here costs the split 21 px of the 168 px it has at
        # the 720x420 minimum -- and it would cost it on every single-file
        # trace, which is every trace that exists today.  `winfo_manager()` is
        # the discriminator ('' against 'pack'); `winfo_reqheight()` is not.
        self.ref_strip = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                   wraplength=0)

        # --- the across-frequency badge: ONE line with an expander, not a tab.
        self._badge_row = badge = ttk.Frame(self)
        badge.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 4))
        self.badge_btn = ttk.Button(badge, text=EXPAND_COLLAPSED, width=3,
                                    command=self._on_toggle_stability)
        self.badge_btn.pack(side=tk.LEFT)
        self.badge_lbl = ttk.Label(badge, anchor="w", justify=tk.LEFT,
                                   foreground=hint_fg)
        self.badge_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # --- the split.  Both panes are POPULATED BEFORE add(): ttk sizes a
        # pane from its requested size at add() time and never recomputes, so
        # adding an empty frame and filling it afterwards works only until
        # something forces a geometry pass in between.
        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)

        top = ttk.Frame(self.paned)
        cap = ttk.Frame(top)
        cap.pack(side=tk.TOP, fill=tk.X)
        # Two RADIOBUTTONS, not two notebook tabs: the sweep below is a
        # drill-down on the row selected up here, and a tab would make the
        # user re-pick the group they just clicked.
        ttk.Radiobutton(cap, text="Contributions", value="contrib",
                        variable=self._view,
                        command=self._on_view_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(cap, text="Sensitivity", value="sens",
                        variable=self._view,
                        command=self._on_view_changed).pack(side=tk.LEFT,
                                                            padx=(8, 0))
        self.table_note = ttk.Label(cap, foreground=hint_fg)
        self.table_note.pack(side=tk.RIGHT)
        self.table = self._make_mono_text(top)
        self.table.bind("<Button-1>", self._on_table_click)
        self.paned.add(top, weight=3)

        bot = ttk.Frame(self.paned)
        self._build_detail(bot, hint_fg)
        self.paned.add(bot, weight=2)

        self.paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        # The divider is DERIVED FROM CONTENT and then belongs to the user
        # (item 2).  Both bindings are on the Panedwindow itself, which is the
        # only widget the sash is part of -- a click inside either pane goes to
        # the child, whose bindtags do not include this one -- so they fire for
        # a sash gesture and for nothing else.  A resize moves the sash too
        # (ttk redistributes by weight), which is why the press position is
        # what a release is compared against rather than the value last
        # applied: a resize is not a drag and must not claim the split.
        self.paned.bind("<ButtonPress-1>", self._on_sash_press)
        self.paned.bind("<ButtonRelease-1>", self._on_sash_release)
        self.paned.bind("<Configure>", self._on_paned_configure)

    def _combo_cell(self, title: str, var: tk.StringVar,
                    values: Sequence[str], width: int) -> tuple:
        """
        (cell, combobox).  A label and its combobox as ONE ReflowRow item.

        Added separately they could wrap apart, leaving 'Aggressor:' at the end
        of one line and its combobox at the start of the next.

        `state="readonly"` and NOT the default: these are closed sets, and a
        typed-in measurement port name that does not exist is a refusal the
        user cannot undo through the widget.  (It is also why the App removes
        the TCombobox wheel binding -- an accidental scroll over one of these
        would silently change which pair is being decomposed.  That unbinding
        is `_install_wheel_router`'s and reaches every combobox in the
        process, including these.)
        """
        cell = ttk.Frame(self.header)
        ttk.Label(cell, text=title).pack(side=tk.LEFT)
        cb = ttk.Combobox(cell, textvariable=var, values=list(values),
                          width=width, state="readonly")
        cb.pack(side=tk.LEFT, padx=(3, 0))
        return cell, cb

    def _freq_cell(self) -> ttk.Frame:
        cell = ttk.Frame(self.header)
        ttk.Label(cell, text="Freq:").pack(side=tk.LEFT)
        # Kept as an attribute: the sweep canvas sits directly below it, and
        # "crossing the plot must not steal focus from this field" is a test.
        self.freq_entry = ent = ttk.Entry(cell, textvariable=self.freq_var,
                                          width=9)
        ent.pack(side=tk.LEFT, padx=(3, 2))
        # <Return> recomputes, which is the gesture anyone typing a frequency
        # expects.  It is bound on the ENTRY, never bind_all: a bind_all here
        # would reach the App's own widgets (measured: Ctrl+S typed into a
        # Toplevel Entry fires the App's _on_save_config).
        ent.bind("<Return>", lambda _e: self._on_recompute())
        ttk.Label(cell, text="GHz").pack(side=tk.LEFT)
        return cell

    def _make_mono_text(self, parent, wrap: str = tk.NONE) -> tk.Text:
        """
        A monospace pane with both scrollbars, always shown.

        Never autohidden.  Deciding each bar off its own scrollcommand is a
        LIMIT CYCLE, not a race -- hiding the horizontal bar gives the widget
        height back, which can hide the vertical bar, which gives width back,
        which brings the horizontal bar back, and `update()` never returns.
        The editor pays a single-decision function for that; a table this size
        can simply keep both, which costs 17 px and cannot oscillate.

        `wrap` is `NONE` for a TABLE and `WORD` for the detail pane, and the
        difference is not cosmetic.  A table must never wrap: a column that
        folds onto the next line stops being a column, which is the whole
        reason rule 3 measures glyph widths at all.  The detail pane is PROSE
        -- sentences about a current and a transimpedance -- and a horizontal
        scrollbar under prose is a reading tax: measured at the 720 px minimum
        the pane's `xview` read `(0.0, 0.950)`, so the tail of every long line
        was off the right edge with a scrollbar as the only route to it.  A
        wrapping pane needs no horizontal bar at all, and dropping it also
        gives the pane its 17 px back.

        NOT registered with `App._register_scrollable`: "Text" is in
        `App._WHEEL_OWNERS`, so `_route_wheel` bails out over it and Tk's own
        class binding scrolls it.  A handler registered here would be dead code
        and would also give the matplotlib canvas a registered ancestor, which
        rule 9 forbids.
        """
        frame = ttk.Frame(parent)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        # `height` is the REQUEST, not the size: `expand=True` gives the widget
        # everything spare, so at the 980x700 default this comes out 239 px.
        # Both monospace panes get the SAME request on purpose -- see
        # `_build_detail`, where the sweep canvas is capped for the same
        # reason.  A ttk.Panedwindow that cannot honour the sum of its pane
        # requests shrinks them in PROPORTION to those requests, so two
        # balanced panes both survive and one fat pane starves the other:
        # measured at 720x420 with the canvas at its natural 420x240, the
        # detail pane asked 284 px against the table pane's 156 of a 168 px
        # budget and the TABLE read `winfo_ismapped() == 0` -- the primary
        # content gone while the drill-down below it was fine.  Balanced
        # (156 / 156) the same window gives 54 px and 74 px, both mapped.
        txt = tk.Text(frame, wrap=wrap, font=ATTRIB_FONT, height=8,
                      yscrollcommand=vsb.set)
        if wrap == tk.NONE:
            txt.configure(xscrollcommand=hsb.set)
            hsb.pack(side=tk.BOTTOM, fill=tk.X)
            hsb.configure(command=txt.xview)
        else:
            hsb.destroy()
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=txt.yview)
        # One tag per role, so a row wears the colour its port wears in the
        # Ports & Roles window.  Never a colour per SIGN: red is WARN_FG
        # everywhere else here and a red negative makes a correct answer look
        # like a fault.
        for kind in ELEMENT_KIND_ROLE:
            txt.tag_configure(f"kind_{kind or 'bare'}",
                              foreground=_role_colour(kind))
        txt.tag_configure("sel", background="#dbe7f5")
        txt.configure(state=tk.DISABLED)
        return txt

    def _build_detail(self, parent, hint_fg: str) -> None:
        """
        The drill-down: numbers on the left, the closed-form sweep on the right.

        Another PanedWindow so the user can give either side the room; both
        children populated before add(), same rule as the outer one.
        """
        cap = ttk.Frame(parent)
        cap.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(cap, text="Selected element").pack(side=tk.LEFT)
        # THE FIELD IS PACKED BEFORE THE HINT THAT DESCRIBES IT.  pack unmaps
        # from the END, and with the hint first the Entry was the casualty:
        # measured at 100%, the caption row needs 103 px (label) + 615 px
        # (hint) + 188 px (entry) = 920, so the entry is 112/188 px wide at
        # 860, 12/188 at 760 and `winfo_ismapped() == 0` at the declared 720 px
        # minimum -- the field gone while the 601 px sentence telling you to
        # type into it was still there.  At 150% it needed a 1920 px window to
        # appear at all.  Custom candidates (R=…/L=…/C=…) are the whole of what
        # this field is for and the hint is the only thing on screen that says
        # they exist, so the two must not compete for the same pixels in that
        # order.
        self.cand_var = tk.StringVar(value=", ".join(STRUCTURAL_CANDIDATES))
        cand = self.cand_entry = ttk.Entry(cap, textvariable=self.cand_var,
                                           width=26)
        cand.pack(side=tk.RIGHT, padx=(6, 8))
        cand.bind("<Return>", lambda _e: self._on_candidates_changed())
        # `wraplength=0` so the hint CLIPS rather than demanding 615 px (1387
        # at 150%) of a row that has not got them -- the same rule as the three
        # header strips, and it is ordered so "Candidates: open, ideal, or
        # R=…/L=…/C=…" survives the clip and the caveat is what goes.
        ttk.Label(cap, text=CANDIDATE_HINT, foreground=hint_fg,
                  wraplength=0, anchor="e").pack(side=tk.RIGHT, fill=tk.X,
                                                 expand=True)

        split = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)

        left = ttk.Frame(split)
        # WORD, not NONE: this pane is prose (see `_make_mono_text`).
        self.detail = self._make_mono_text(left, wrap=tk.WORD)
        split.add(left, weight=1)

        right = ttk.Frame(split)
        # Figure(), never pyplot.figure(): pyplot keeps a global registry, so a
        # figure created that way outlives the Toplevel that owned it and is
        # never collected.  FullscreenPlotWindow in pkg_rlc_plot is the shipped
        # precedent for a second FigureCanvasTkAgg in a Toplevel.
        self.figure = Figure(figsize=(4.2, 2.4))
        self.canvas = FigureCanvasTkAgg(self.figure, master=right)
        # `FigureCanvasTkAgg` sizes its Tk widget from figsize x dpi, i.e. it
        # REQUESTS 420x240 here, and that request is what a ttk.Panedwindow
        # sizes a pane from.  Measured at 720x420: the detail pane asked 284 px
        # of a 168 px budget and the table pane above it was starved to zero.
        # The widget request is therefore set small by hand; matplotlib
        # re-lays the figure out on every <Configure>, so the drawn plot is
        # whatever size the pane actually gets (183 px tall at the 980x700
        # default) and only the FLOOR moved.
        self.canvas.get_tk_widget().configure(width=240, height=90)
        # NO <Enter> -> focus_set, unlike FullscreenPlotWindow.  Measured: that
        # binding moves focus off a sibling Entry, and this window has Entry
        # fields directly above the plot -- a user types 5.6 into Freq, moves
        # the mouse toward [Recompute], crosses the plot, and the rest of the
        # keystrokes go nowhere.  No M / V / Delete bindings either: this is a
        # read-only what-if curve, not the measurement plot.
        #
        # Drawn LAZILY: a canvas in an unmapped pane has no size, so a draw()
        # there lays the axes out for a 1x1 widget and the labels overlap
        # forever afterwards.
        self.canvas.get_tk_widget().bind("<Map>", self._on_canvas_mapped)
        # `wraplength=0` and a HARD LINE CAP, not a wrapping label.  It was
        # `wraplength=420`, and `sweep_caption` returns up to four sentences --
        # 957 characters on a non-monotonic sweep -- which lays out at
        # `winfo_reqheight() == 293 px`.  The note is packed `side=BOTTOM` in
        # the same frame as the `expand=True` canvas, so pack satisfies the
        # canvas's 90 px REQUEST and hands the note everything left: measured,
        # selecting a row took the canvas from 194 px to 90 at 980x700 and from
        # 274 to 90 at 1400x900, i.e. the 240x90 floor that exists to stop the
        # canvas starving the table became the plot's permanent size, and at
        # the 720x420 minimum the curve was 103x6 PIXELS of axes under a
        # caption clipped to 1 px.  wraplength was also a constant: at 720 the
        # pane is 179 px wide and the label still laid out at 419.
        #
        # So the widget shows at most `SWEEP_NOTE_LINES` clipping lines (the
        # `_footer_strip_text` rule -- wrapping costs height, clipping does
        # not) and the full text goes to Copy report, which is where rule 12
        # requires it in full anyway.
        self.sweep_note = ttk.Label(right, anchor="w", justify=tk.LEFT,
                                    wraplength=0, foreground=hint_fg)
        # THE CAPTION IS PACKED BEFORE THE CANVAS, which is rule 10 applied
        # inside this frame: pack unmaps from the END, and with the canvas
        # first it claimed its whole 90 px request and left the caption 1 px.
        # Measured at the 720x420 minimum: 100 x 6 PIXELS of axes over a 1 px
        # label -- and that label is where rule 8's mandatory
        # "NON-MONOTONIC: the curve LEAVES the [ideal, open] bracket" goes, as
        # well as the "cannot sweep" refusal for k / M/L_a and any refused
        # candidate.  Six pixels of curve is worth nothing; the sentence saying
        # the two endpoints are not a bound is worth the whole pane.  The
        # canvas keeps `expand=True`, so at every size where both fit the plot
        # still takes all the spare room.
        self.sweep_note.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH,
                                         expand=True)
        split.add(right, weight=1)

        split.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # -------------------------------------------------------------- render

    def _set_text(self, widget: tk.Text, table) -> None:
        """Replace a monospace pane's contents and re-tag it."""
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        if isinstance(table, TableText):
            widget.insert("1.0", table.text)
            for line_idx, _key, kind in table.rows:
                ln = line_idx + 1
                widget.tag_add(f"kind_{kind or 'bare'}",
                               f"{ln}.0", f"{ln}.end")
        else:
            widget.insert("1.0", "\n".join(table))
        widget.configure(state=tk.DISABLED)

    def _render(self) -> None:
        """Repaint everything from `self._res`.  Never raises."""
        try:
            self._render_impl()
        except Exception as e:                           # pragma: no cover
            # A refresh that raises reaches no handler anyone controls: Tk
            # prints it to a console a double-clicked GUI does not have and the
            # window carries on showing a stale verdict.  Say so on screen.
            try:
                self.recon.configure(text=f"render failed: {e}",
                                     foreground=self._banner_warn_fg)
            except Exception:
                pass

    def _units(self) -> str:
        """
        The units mode, read LIVE off the App.

        `_run_report_segments` does the same and CLAUDE.md says why: the unit
        is a RENDERING CHOICE, not a recorded fact, so freezing it on the
        Provenance would leave this window printing `-1.242` beside a results
        pane printing `-1.24 mH` after the user flipped the switch.  Everything
        else on the Provenance is frozen on purpose; this one is not, and the
        stored value is only the fallback for a window whose App has gone.
        """
        try:
            return str(self.app.units_mode_var.get())
        except Exception:                                # pragma: no cover
            return self._res.prov.units_mode

    def _live_prov(self) -> Provenance:
        """The Provenance an export should carry: frozen, except the units."""
        return replace(self._res.prov, units_mode=self._units())

    def _render_impl(self) -> None:
        res, dec, prov = self._res, self._res.dec, self._res.prov
        units = self._units()
        self.trace_lbl.configure(text=header_trace_text(prov))
        # The strip does NOT hear about a child's text change on its own --
        # `ReflowRow._reflow` runs from `add()` and from the strip's own
        # <Configure>, and neither fires here.  Without this, a Recompute after
        # a relabel left `place` forcing the old width and the label silently
        # clipped (measured: 220 px placed against a 307 px request, 14
        # characters gone with no ellipsis), which is exactly the Treeview
        # failure mode rule 3 rejects, arriving in the header instead.
        self.header.refresh()
        self.refresh_banner()

        # Which ground model is IN FORCE, on the strip that already carries the
        # rules the numbers are read under.  It is the frozen Provenance value,
        # not the Entry: the field can have been edited without a Recompute,
        # and the strip describes the numbers on screen.
        self.sign_lbl.configure(text=sign_strip_text(prov.ground_model_label))

        # The parser's note about the model, where the control is.  It leads
        # with the verdict because this Label clips like every other strip.
        if prov.ground_model_notes:
            self._gm_hint.configure(
                text=("NOT APPLIED — " if not prov.ground_model_applied
                      else "") + str(prov.ground_model_notes[0]),
                foreground=(self._gm_warn_fg if not prov.ground_model_applied
                            else self._gm_hint_fg))
        else:
            self._gm_hint.configure(text=GROUND_MODEL_HINT,
                                    foreground=self._gm_hint_fg)

        _verdict, ok = reconciliation_verdict(dec)
        self.recon.configure(
            text="Reconciliation:  " + reconciliation_line(dec),
            foreground=self._banner_ok_fg if ok else self._banner_warn_fg)

        self._apply_reference_strip()

        # THE BUTTON IS A ONE-SHOT CHECK, NOT AN EXPANDER, and once it has run
        # it is spent.  MEASURED before this: press -> `▾` plus the verdict;
        # press again -> the glyph went back to `▸` and the label text was
        # UNCHANGED, still the verdict, with `_badge_row` 27 px throughout.
        # There is nothing to collapse -- `_expanded` gated no content, only
        # the glyph -- so a glyph offering to collapse was inert, and offering
        # to re-run would spend four more solves on an answer already on the
        # line beside it.  A disabled button needs its reason on screen (the
        # Keep button's rule); here the reason IS the label next to it, which
        # is the verdict it produced.  [Recompute] builds a fresh AttribResult
        # with `stability=""`, so a new decomposition makes it live again.
        checked = bool(res.stability)
        self.badge_lbl.configure(
            text="across frequency: "
                 + (res.stability
                    or stability_offer(STABILITY_POINTS, self._nports())))
        self.badge_btn.configure(
            text=EXPAND_EXPANDED if checked else EXPAND_COLLAPSED)
        self.badge_btn.state(["disabled"] if checked else ["!disabled"])

        if self._view.get() == "sens":
            rows = self._sensitivity_all()
            table = sensitivity_table(rows, units, res.kinds())
            note = f"{len(rows)} rows · every row is a full re-solve"
            # A refused candidate is why this table is SHORT, and the row count
            # on its own said the opposite -- measured, "open, R=5 m" gave
            # "2 rows · every row is a full re-solve" over a table missing both
            # of the candidate's rows, with the reason on no widget anywhere.
            if self._cand_problems:
                note += (f" · {len(self._cand_problems)} candidate REFUSED "
                         "(under the sweep)")
            self.table_note.configure(text=note)
        else:
            table = contributions_table(dec, units)
            n = len([t for t in dec.terms if t.element is not None])
            self.table_note.configure(
                text=f"{n} declared elements + the bare EM coupling")
        self._contrib_rows = table.rows
        self._set_text(self.table, table)
        # Re-derive the split from the row count this repaint produced.  A
        # repaint of the SAME table writes nothing (`_apply_sash` compares the
        # target against the position), and a user who has dragged the divider
        # keeps it whatever the row count does.
        self._apply_sash(len(table.lines))
        self._highlight_selection()
        self._render_detail()

    def _apply_reference_strip(self) -> None:
        """
        R3-5: show the weld where the number is read, and nothing otherwise.

        A weld raises nothing and makes no number look wrong -- measured in
        `pkg_rlc_compose`, the package ground pad grounded / open / through
        1 nH all give L_eff = 2.1454 nH, BIT-IDENTICAL, spread 0.000e+00 -- so
        what it changes is how the table below has to be READ.  In this window
        it changes it twice over: every contribution attributed to an element
        in the welded file is a contribution from a network that is not in the
        circuit, and it will read as exactly 0 with a healthy residual beside
        it, which is the composed-baseline failure `COMPOSED_BASELINE_TEXT`
        already exists to name.  A reader looking at a table of zeroes needs
        that sentence on the same screen, not in a CLI report they did not run.

        Placed under Reconciliation because it is the same KIND of statement --
        a precondition on trusting everything below it -- and above the badge
        so it cannot be pushed off by a long stability verdict.

        `pack_forget`, never empty text: an unmanaged ttk.Label costs 0 px and
        a managed empty one costs 21 (measured), which on a single-file trace
        would be 21 px of the 168 px the split has at the 720x420 minimum, paid
        by every session that has ever existed for a check with nothing to say.
        """
        strip = self._res.prov.reference_strip
        text = str(strip[0]) if strip else ""
        warn = bool(strip) and bool(strip[1])
        managed = bool(self.ref_strip.winfo_manager())
        if not text:
            if managed:
                self.ref_strip.pack_forget()
            return
        self.ref_strip.configure(
            text=text,
            foreground=self._banner_warn_fg if warn else self._banner_ok_fg)
        if not managed:
            self.ref_strip.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(2, 0),
                                before=self._badge_row)

    def _nports(self) -> int:
        """The NETWORK's port count -- what the across-frequency cost scales on.

        The composed count on a composition, because that is the matrix each
        extra frequency reduces: quoting the die's 16 for a 316-port stack
        understates the offer by the cube of 20.

        Never raises: the badge's offer is worth printing without it, and a
        window whose file has gone still has to render.
        """
        try:
            sn = self.app._cached_trace_network(self._trace)
            if sn is not None:
                return int(sn.nports)
        except Exception:                                # pragma: no cover
            pass
        try:
            return int(self._file.ts.nports)
        except Exception:                                # pragma: no cover
            return 0

    def _highlight_selection(self) -> None:
        self.table.tag_remove("sel", "1.0", tk.END)
        for line_idx, key, _kind in self._contrib_rows:
            if key == self._selected:
                ln = line_idx + 1
                self.table.tag_add("sel", f"{ln}.0", f"{ln}.end+1c")
                break

    def _render_detail(self) -> None:
        res, dec = self._res, self._res.dec
        key = self._selected
        sens = res.sens_one.get(key, ()) if key is not None else ()
        grp = None
        if key is not None:
            g = res.group_of(key)
            grp = (g[0], len(g[1])) if g else None
        self._set_text(self.detail, detail_lines(dec, key, sens, grp))
        self._sweep_drawn = False
        self._draw_sweep_if_visible()

    # ---------------------------------------------------------- the sweep

    def _on_canvas_mapped(self, _event=None) -> None:
        self._draw_sweep_if_visible()

    def _draw_sweep_if_visible(self) -> None:
        """
        Draw the sweep only once the canvas is really on screen and has a size.

        `winfo_width() <= 1` is the unmapped / never-laid-out case, where
        `draw()` lays the axes out for a 1x1 widget and every label lands on
        top of every other.
        """
        try:
            w = self.canvas.get_tk_widget()
            if not w.winfo_exists() or not w.winfo_ismapped() \
                    or w.winfo_width() <= 1:
                return
            if self._sweep_drawn:
                return
            self._sweep_drawn = True
            self._draw_sweep()
        except Exception as e:                           # pragma: no cover
            try:
                self.sweep_note.configure(text=f"sweep failed: {e}")
            except Exception:
                pass

    def _draw_sweep(self) -> None:
        self.figure.clf()
        self._sweep_pic = None
        ax = self.figure.add_subplot(111)
        key = self._selected
        if key is None:
            ax.set_axis_off()
            ax.text(0.5, 0.5, "select an element row", ha="center",
                    va="center", fontsize=9, color="#808080")
            self._set_sweep_note(())
            self.canvas.draw()
            return

        res = self._res
        grp = res.group_of(key)
        target = grp[0] if (grp and len(grp[1]) > 1) else key
        try:
            sw = attrib.sweep_mobius(res.ctx, res.prov.victim,
                                     res.prov.aggressor, target,
                                     res.dec.quantity, param="L",
                                     samples=160)
        except attrib.AttribError as e:
            # M/L_a and k are refused BY NAME (their scale is itself a function
            # of the swept parameter).  Showing the refusal is the answer -- an
            # empty plot would read as "no effect".
            ax.set_axis_off()
            self._set_sweep_note([str(e)])
            self.canvas.draw()
            return

        ts, vals = sw.samples if sw.samples else (np.zeros(0), np.zeros(0))
        pos = ts > 0
        part = attrib.DECOMPOSABLE[sw.quantity].part
        if part == "complex":
            ax.plot(ts[pos], np.real(vals[pos]), lw=1.4, label="Re")
            ax.plot(ts[pos], np.imag(vals[pos]), lw=1.4, label="Im")
        else:
            ax.plot(ts[pos], np.real(vals[pos]), lw=1.4,
                    label=sw.quantity)
        # Both asymptotes, named.  t = 0 cannot be drawn on a log axis, and it
        # is the IDEAL endpoint -- the one a "best case" estimate uses -- so it
        # is a horizontal line rather than a dropped point.
        ax.axhline(sw.value_ideal.real, ls="--", lw=0.9, color="#207020")
        ax.axhline(sw.value_open.real, ls=":", lw=0.9, color="#a06000")
        ax.set_xscale("log")
        self._scale_sweep_axis(ax, sw)
        # The unit is on the TICKS now (`_si_formatter`), so it is not repeated
        # in the axis label: `500 pH` under `series inductance [H]` prints the
        # henry twice and the prefix once, which is the arrangement that made
        # the old `1e-10` corner offset readable as part of the label.
        ax.set_xlabel(str(sw.param_name), fontsize=8)
        ax.set_ylabel(str(sw.quantity), fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=7, loc="best")
        try:
            self.figure.tight_layout()
        except Exception:                                # pragma: no cover
            pass
        self.canvas.draw()
        self._sweep_full = list(sweep_caption(sw, self._sweep_pic))
        self._set_sweep_note(self._sweep_full)

    def _scale_sweep_axis(self, ax, sw) -> None:
        """
        The y axis: symlog, limits from the physical endpoints, pole labelled.

        Item 1, and every piece of it is `sweep_picture`'s -- this only puts it
        on the axes.  Three things happen here and each has its measurement in
        `sweep_picture`'s own constants:

          * the limits come from `M(0)` and `M(inf)` plus the pole-free samples
            and a margin, so the pole runs OFF THE TOP instead of owning the
            axis (measured before: one vertical spike over a `1e-10` axis);
          * the scale is symlog with a linthresh taken from the endpoints, and
            the major locator goes linear while the visible range stays inside
            it, because matplotlib's symlog locator ticks at decades and
            produced NO labelled tick at all on a sub-decade range;
          * every pole visible in the sampling gets a labelled vertical line at
            its parameter value -- a pole is a real feature and is named, never
            silently hidden;
          * BOTH axes print ENGINEERING UNITS, through the same `format_si`
            every other number in this window goes through.  The original
            complaint was literally "the y axis reads 1e-5"; MEASURED after the
            symlog change it read `1e-10` -- `ax.yaxis.get_offset_text()` was
            `'1e−10'` over tick labels `['−2.5','0.0','2.5','5.0','7.5','10.0']`
            and an ylabel of `M [H]`, beside a table cell reading `+413 pH` and
            a caption reading `ideal +821 pH`.  A bare exponent offset is a
            second notation for the same quantity on the same screen, and it is
            the notation the reader has to do arithmetic on.  This is the plot
            cursor readout's own rule (`_readout_value` -> `format_si`), so the
            axis and the caption cannot drift.

        The whole block is guarded: an axis that cannot be scaled is worth less
        than a curve that cannot be drawn, so a failure here leaves matplotlib's
        own autoscale in place rather than blanking the pane.
        """
        pic = sweep_picture(sw)
        self._sweep_pic = pic
        try:
            ylo, yhi = pic.ylim
            if pic.linthresh > 0.0 and math.isfinite(ylo) \
                    and math.isfinite(yhi) and yhi > ylo:
                ax.set_yscale("symlog", linthresh=pic.linthresh)
                if pic.linear_ticks:
                    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
                ax.set_ylim(ylo, yhi)
            # Engineering units on BOTH axes, and the exponent offset that a
            # ScalarFormatter would otherwise park in the corner turned off
            # explicitly -- `format_si` already carries the prefix, so an offset
            # beside it would multiply the label the reader just read.
            ax.yaxis.set_major_formatter(_si_formatter(_display_unit(sw.unit)))
            ax.xaxis.set_major_formatter(_si_formatter(sw.param_unit))
            for cluster in pic.clusters:
                ax.axvline(cluster[0].t, color=POLE_LINE_FG, lw=1.0, ls="-.")
                # The label sits ON the excursion, which is where the curve is
                # a near-vertical line, so it needs a background or it is read
                # through the data: measured, without the bbox the two are the
                # same pixels.  `alpha` rather than opaque, because hiding the
                # curve behind the annotation would be the same failure the
                # other way round.
                ax.text(cluster[0].t, 0.97,
                        "  " + pole_label(cluster, sw.param_unit),
                        rotation=90, transform=ax.get_xaxis_transform(),
                        va="top", ha="left", fontsize=7, color=POLE_LINE_FG,
                        bbox=dict(boxstyle="square,pad=0.15", fc="white",
                                  ec="none", alpha=0.75))
        except Exception:                                # pragma: no cover
            pass

    def _sweep_note_cap(self) -> int:
        """
        How many clipping lines the caption may take at THIS window height.

        See `ATTRIB_SWEEP_NOTE_RESERVE_LINES` for the four measurements and for
        why the budget is read off the WINDOW and not off the sweep pane.
        Never raises: it runs from `<Configure>`.
        """
        try:
            line = tkfont.Font(root=self, font="TkDefaultFont"
                               ).metrics("linespace")
            budget = int(self.winfo_height()) - self._chrome_height()
            allow = budget // max(1, line) - ATTRIB_SWEEP_NOTE_RESERVE_LINES
            return max(1, min(SWEEP_NOTE_LINES, int(allow)))
        except Exception:                                # pragma: no cover
            return SWEEP_NOTE_LINES

    def _set_sweep_note(self, caption=None) -> None:
        """
        The capped note, with any REFUSED CANDIDATE ahead of the caption.

        Every write to this Label goes through here, which is the point: the
        old arrangement had `_alternatives` write its problem list into the
        widget and `_draw_sweep` overwrite it a few statements later in the
        same `_render()`, so a candidate the parser refused reached no widget
        at all.

        `caption=None` means "re-render whatever is already there at the
        current cap", which is what the resize path wants -- it must not invent
        a caption for a pane that is showing a refusal or nothing at all.
        """
        if caption is None:
            caption = self._sweep_shown
        self._sweep_shown = list(caption)
        self.sweep_note.configure(
            text=sweep_note_text(caption, self._cand_problems,
                                 self._sweep_note_cap()))

    # -------------------------------------------------------------- events

    def _on_view_changed(self) -> None:
        self._render()

    def _on_table_click(self, event) -> None:
        """
        Select the clicked row and drive the detail pane from it.

        Tk's `@x,y` index CLAMPS to the nearest existing line, so a click in
        the empty space below the last row resolves to the last row: measured
        with a 5-line table in a 222 px widget, a click at `y = h - 4` -- about
        150 px below the last text line -- returned `index("@50,218") ==
        "5.51"` and selected the final element.  That is a silent re-drive of
        the detail pane and a closed-form sweep solve for something the user
        did not click, so a click past the last row selects nothing.
        """
        try:
            line = int(self.table.index(f"@{event.x},{event.y}").split(".")[0])
        except Exception:                                # pragma: no cover
            return
        last = self.table.index("end-1c").split(".")[0]
        try:
            if line > int(last):                         # pragma: no cover
                return
        except ValueError:                               # pragma: no cover
            return
        # The clamp is what makes the bare "did any row claim this line" test
        # insufficient: the y has to be inside the clicked line's own box.
        box = self.table.bbox(f"{line}.0")
        if box is not None and not (box[1] <= event.y < box[1] + box[3]):
            return
        for line_idx, key, _kind in self._contrib_rows:
            if line_idx + 1 == line:
                self._select(key)
                return

    def _select(self, key) -> None:
        self._selected = key
        if key is not None and key not in self._res.sens_one:
            self._res.sens_one[key] = self._sensitivity_for(key)
        self._highlight_selection()
        self._render_detail()

    def _alternatives(self) -> list:
        """
        The candidate list, and a RECORD of anything the parser refused.

        It does not touch a widget.  It used to write the problems straight
        into `self.sweep_note`, which `_draw_sweep` then overwrote later in the
        same `_render()` pass -- so the message reached nothing.  The record is
        rendered by `_set_sweep_note` (ahead of the caption) and counted on
        `table_note`, and both of those run after this does.
        """
        alts, problems = candidate_list(self.cand_var.get(),
                                        self._res.ctx.omega)
        self._cand_problems = list(problems)
        if not alts:
            alts = [attrib.alt_open(), attrib.alt_ideal()]
        return alts

    def _sensitivity_for(self, e: int) -> list:
        try:
            return attrib.sensitivity(
                self._res.ctx, self._res.prov.victim, self._res.prov.aggressor,
                self._alternatives(), self._res.dec.quantity, elements=[e])
        except Exception:                                # pragma: no cover
            return []

    def _sensitivity_all(self) -> list:
        """
        Every element against every candidate, computed once and cached.

        Deferred to the first switch into the Sensitivity view on purpose: it
        is m x A full re-solves of an m x m system, which is free on a handful
        of elements and is real work on a 60-ball ground group -- and most
        sessions never leave the Contributions view.
        """
        if self._res.sens_all is None:
            try:
                self._res.sens_all = attrib.sensitivity(
                    self._res.ctx, self._res.prov.victim,
                    self._res.prov.aggressor, self._alternatives(),
                    self._res.dec.quantity)
            except Exception:                            # pragma: no cover
                self._res.sens_all = []
        return self._res.sens_all

    def _on_candidates_changed(self) -> None:
        self._res.sens_all = None
        self._res.sens_one.clear()
        # Parse EAGERLY, so a refusal is reported on the keystroke that caused
        # it rather than on the next thing that happens to need a candidate.
        # `_alternatives` is otherwise reached only from the two sensitivity
        # paths, so on the Contributions view with no row selected -- the state
        # the window opens in -- a bad candidate sat in the field unremarked
        # until the user switched views.  It is a `str.split` and a
        # `parse_kv_rlc_params`; no solve.
        self._alternatives()
        if self._selected is not None:
            self._res.sens_one[self._selected] = \
                self._sensitivity_for(self._selected)
        self._render()

    def _on_toggle_stability(self) -> None:
        """
        Expand the across-frequency badge, computing the ranking on demand.

        User-initiated, which is what makes the cost acceptable: each extra
        frequency is a fresh build_context + decompose, O(N^3) in the PORT
        count.  Nothing on an automatic path ever calls this.

        Flushed first for the same reason [Recompute] is: `stability_ranks`
        goes through `app._build_termination(trace, …)`, i.e. it reads the
        live spec, and a keystroke in the same event burst as this click is
        still in the idle queue.  Ranking the NEW spec at four frequencies
        against the OLD spec's `res.dec` at the fifth would produce a
        "rank is NOT stable" verdict about an edit rather than about frequency.
        """
        try:
            self.app._flush_editor_sync()
        except Exception:                                # pragma: no cover
            pass
        self._expanded = True
        if not self._res.stability:
            # The glyph and the "checking…" line are written NOW,
            # synchronously: a button that does not move until the work
            # finishes reads as a dead button, and on a 153-port file that is
            # seconds of it.
            self.badge_btn.configure(text=EXPAND_EXPANDED)
            self.badge_lbl.configure(text="across frequency: checking…")
            self.badge_btn.state(["disabled"])
            # The WORK goes on the next turn of the event loop, so Tk gets to
            # paint the two writes above first.  Deliberately not
            # `update_idletasks()`: that flushes geometry for the WHOLE
            # application rather than this window, which is the documented way
            # the Results pane's sash once ended up at 2 px.
            self._stability_after = self.after(1, self._compute_stability)
            return
        self._render()

    def _compute_stability(self) -> None:
        self._stability_after = None
        if not self.winfo_exists():                      # pragma: no cover
            return
        try:
            freqs, ranks = stability_ranks(self.app, self._trace,
                                           self._file, self._res)
            self._res.stability = stability_line(freqs, ranks)
        except Exception as e:
            self._res.stability = f"could not be checked: {e}"
        # `_render` decides the button's state from `res.stability`, which is
        # now set either way -- including on the failure branch, where the
        # message IS the reason the button is spent.  Re-enabling here and
        # letting `_render` disable it again would be two writes saying
        # opposite things in one pass.
        self._render()

    def _on_recompute(self) -> None:
        """
        THE button (rule 6).  Nothing else recomputes anything.

        It is allowed on a spec that has been edited since the run -- refusing
        would mean a full Calculate of every trace just to re-attribute, which
        is hostile in the one workflow this window exists for.  What it must
        not do is keep claiming the run: `spec_matches_run` goes False and the
        banner, the export and the copied report all say the plot and the
        results table are showing something else.  That is the same mandatory
        disagreement line a run page carries.
        """
        if not self.winfo_exists():                      # pragma: no cover
            return
        # FLUSH FIRST, exactly as `open_attribution_window`, `_on_calculate`,
        # `_on_freeze_trace` and `_session_dict` all do, and for the identical
        # reason: auto-apply is deferred to `after_idle`, so a keystroke in the
        # same event burst as this click is still sitting in the idle queue.
        # Measured, with the trace calculated at `gnd_ports = "2,4"`: typing
        # "2" into the GND field and pressing Recompute decomposed the OLD
        # spec -- the table came back with `ground port 2` AND `ground port 4`
        # -- and only then did the queued sync land, after which the banner
        # said "the spec has been EDITED since — press Recompute", pointing at
        # the edit Recompute was supposed to have picked up.  On THE button
        # whose whole job is "decompose what is on screen now", that is the
        # documented "Auto-sync editor on Calculate" invariant, missed.
        try:
            self.app._flush_editor_sync()
        except Exception:                                # pragma: no cover
            pass
        trace, fe = self._subject()
        if trace is None or fe is None:
            self.recon.configure(
                text="Reconciliation:  cannot recompute — the trace or its "
                     "file is no longer loaded.",
                foreground=self._banner_warn_fg)
            return
        # Re-ask the refusal.  `open_attribution_window` asks it once, on the
        # way in; this button is the only other route to a decomposition, and
        # the flush above can have just made the answer change -- a trace that
        # became frozen, or lost its file, while the window was open.  Without
        # this a frozen trace recomputed here came back reconciled and stamped
        # `spec_matches_run: True` (measured), which is exactly the run
        # mislabelling `attribution_refusal` refuses a frozen trace to prevent.
        # `allow_stale=True`: an edited spec is the NORMAL case here and is
        # what the button is for -- see this method's docstring.
        why = attribution_refusal(trace, fe, allow_stale=True)
        if why:
            self.recon.configure(
                text="Reconciliation:  " + why.replace("\n\n", "  "),
                foreground=self._banner_warn_fg)
            return
        # The two port comboboxes are refreshed BEFORE the decompose is tried.
        # [Recompute] is allowed on an edited spec, and an edit can rename or
        # add a measurement port -- without this the combobox still offers the
        # OLD names, `decompose` refuses one of them by name, and the user has
        # no widget from which to pick the name the refusal just told them
        # about.  It costs a parse and no solve.
        self._refresh_port_choices(trace, fe)
        try:
            f_hz = parse_si(self.freq_var.get()) * 1e9
        except Exception:
            self.recon.configure(
                text="Reconciliation:  frequency must be a number in GHz "
                     "(e.g. 5.6, or 5.6e0).",
                foreground=self._banner_warn_fg)
            return
        vic, agg = self.victim_var.get(), self.aggr_var.get()
        if vic == agg:
            self.recon.configure(
                text=f"Reconciliation:  victim and aggressor are the same "
                     f"measurement port '{vic}'. Z_ab is a MUTUAL impedance — "
                     f"pick two different ones.",
                foreground=self._banner_warn_fg)
            return
        try:
            res = compute_attribution(self.app, trace, fe, vic, agg,
                                      self.quantity_var.get(), f_hz,
                                      ground_model=self.ground_var.get())
        except Exception as e:
            # A ground model this cannot read reports the CLI's own wording
            # here rather than raising: the field is free text and the message
            # ("'shared' needs an impedance after the colon, e.g. shared:L=1n")
            # is the whole of what makes it correctable.
            self.recon.configure(text=f"Reconciliation:  {e}",
                                 foreground=self._banner_warn_fg)
            return
        self._res = res
        # The FileEntry too: `_on_remove_file` + a reload replaces the object,
        # and `stability_ranks` reads `self._file`.  Holding the old one would
        # rank against a Y matrix nothing else in the application is using.
        self._file = fe
        self._selected = None
        self._expanded = False
        self.title(f"{ATTRIB_TITLE}: {res.prov.victim} ← {res.prov.aggressor}"
                   f"   [{res.prov.trace_id}] {res.prov.trace_label}")
        self._render()

    def _refresh_port_choices(self, trace, fe) -> None:
        """Re-read the measurement-port names off the CURRENT spec.

        Never raises: a half-edited spec has no resolvable measurement ports
        and the right answer then is to leave the old list alone, not to empty
        the widget the user is about to choose from.
        """
        try:
            net = _attrib_network(self.app, trace, fe)
            names = [mp.name
                     for mp in resolve_meas_ports(net.term, net.nports)]
        except Exception:
            return
        if len(names) < 2:
            return
        for cb in (self.victim_cb, self.aggr_cb):
            cb.configure(values=names)

    # --------------------------------------------------------- the subject

    def _subject(self) -> tuple:
        """
        (trace, file entry) if both are still loaded, else (None, None).

        Resolved by IDENTITY against `app.traces`, never by index and never by
        `in` -- `TraceConfig` is an eq=True dataclass holding numpy arrays, so
        `tc in list` raises "truth value of an array is ambiguous" as soon as
        it compares against a trace it does not match.
        """
        try:
            if not any(t is self._trace for t in self.app.traces):
                return None, None
            fe = self.app._file_by_label(self._trace.file_label)
            return (self._trace, fe) if fe is not None else (None, None)
        except Exception:                                # pragma: no cover
            return None, None

    # ------------------------------------------------------------ the split

    def _sash_target(self, lines: int, height: int) -> int:
        """
        Where the divider goes for a table of `lines` rendered lines.

        Pure arithmetic on measured CHROME (the two panes' requested heights
        minus their Texts', i.e. the captions and the scrollbars) and on the
        table font's linespace.  `height` is the paned window's own height and
        is used for ONE thing: clamping, so neither pane is squeezed below its
        floor.  It is not what the position is derived from -- see
        `ATTRIB_SASH_SPARE_LINES`.
        """
        panes = self.paned.panes()
        top = self.nametowidget(panes[0])
        bot = self.nametowidget(panes[1])
        line = tkfont.Font(root=self, font=ATTRIB_FONT).metrics("linespace")
        top_chrome = max(0, top.winfo_reqheight()
                         - self.table.winfo_reqheight())
        bot_chrome = max(0, bot.winfo_reqheight()
                         - self.detail.winfo_reqheight())
        want = top_chrome + line * min(int(lines) + ATTRIB_SASH_SPARE_LINES,
                                       ATTRIB_SASH_MAX_LINES)
        floor = top_chrome + line * ATTRIB_TABLE_FLOOR_LINES
        need_bot = bot_chrome + line * ATTRIB_DETAIL_FLOOR_LINES
        ceiling = height - need_bot
        if ceiling < floor:
            # NEITHER FLOOR FITS, and giving the table its floor outright is
            # not the answer: measured at 150% DPI at the enforced minimum
            # (720x678, 198 px of paned against floors of 125 and 174), the
            # table took its 125 and the SWEEP CANVAS read
            # `winfo_ismapped() == 0` -- the drill-down gone, which is the
            # same "gives up all of it" failure `_apply_min_height` exists to
            # stop, arriving inside the split.  What there is is therefore
            # shared in proportion to the two floors: 83 px here, against the
            # 82 px ttk's own weights produced before any of this, and
            # everything mapped.
            total = floor + need_bot
            share = int(height * floor / total) if total > 0 else floor
            # ONE line of table, whatever the proportion says.  `need_bot`
            # grows with the sweep caption's requested height (three clipping
            # lines at 150% is 86 px), and a proportion taken against that put
            # the table at 6 px -- measured at 150% / 720x678 with a row
            # selected, against 23 px from ttk's own weights before any of
            # this.  Neither shows a row; one shows the heading.
            return max(top_chrome + line, share)
        return int(max(floor, min(want, ceiling)))

    def _apply_sash(self, lines: Optional[int] = None) -> None:
        """
        Put the divider where the content says, unless the user has moved it.

        Never raises: it runs from `<Configure>` and from `_render`, and an
        error on either reaches no handler anyone controls.
        """
        if lines is not None:
            self._sash_lines = int(lines)
        if self._sash_user or not self.winfo_exists():
            return
        try:
            height = self.paned.winfo_height()
            if height <= 1:
                # Not laid out yet -- the <Configure> that gives it a real
                # height calls back.  Writing a position now would be a
                # position for a 1 px window.
                return
            want = self._sash_target(self._sash_lines, height)
            if abs(int(self.paned.sashpos(0)) - want) <= 1:
                return
            self.paned.sashpos(0, want)
            self._sash_writes += 1
        except Exception:                                # pragma: no cover
            pass

    def _on_paned_configure(self, _event=None) -> None:
        # Coalesced and cancelled on destroy, like `_on_configure`.  Writing a
        # sash position cannot re-enter this: measured, `sashpos()` resizes the
        # two PANES and leaves the Panedwindow's own geometry untouched, so the
        # next pass computes the same number and the early return above ends
        # it.  That is the `ReflowRow` fixed point, not the
        # `_apply_editor_scrollbars` limit cycle.
        if self._sash_after is not None:
            return
        try:
            self._sash_after = self.after_idle(self._sash_now)
        except Exception:                                # pragma: no cover
            self._sash_after = None

    def _sash_now(self) -> None:
        self._sash_after = None
        if self.winfo_exists():
            self._apply_sash()

    def _on_sash_press(self, _event=None) -> None:
        try:
            self._sash_press = int(self.paned.sashpos(0))
        except Exception:                                # pragma: no cover
            self._sash_press = None
        self._sash_press_writes = self._sash_writes

    def _on_sash_release(self, _event=None) -> None:
        """
        A DRAG claims the split; a click, a resize, or OUR OWN WRITE does not.

        Once the reader has moved the divider it is theirs until the window
        closes -- a later decomposition with a different row count must not
        take it back.  Compared against the position at ButtonPress rather than
        against the last value applied, because ttk moves the sash itself when
        the window is resized and that is not a gesture.

        THE WRITE COUNTER IS THE SECOND HALF OF THAT, and without it the
        position test alone turns anything that moves the sash while a button
        is held into a permanent claim.  MEASURED at 100% / 980x700:
        `_on_sash_press()` -> `_apply_sash(30)` -- which is exactly what a new
        decomposition does -- -> `_on_sash_release()` left `_sash_user` True
        with no pointer movement at all, and the split was then frozen for the
        rest of the session, i.e. item 2's content-derived position stopped
        working. `_apply_sash` is reachable while a button is down from
        `_render_impl` (Recompute, a view switch, and the units switch's
        `refresh_attribution_windows(rerender=True)`) and from the `after_idle`
        `<Configure>`.  Requiring the counter to be UNCHANGED costs at most a
        real drag that raced an automatic write in the same gesture, which is
        self-correcting -- the reader drags again -- where the false claim is
        not.
        """
        try:
            now = int(self.paned.sashpos(0))
        except Exception:                                # pragma: no cover
            return
        if (self._sash_press is not None and now != self._sash_press
                and self._sash_writes == self._sash_press_writes):
            self._sash_user = True
        self._sash_press = None

    # ------------------------------------------------------- minimum height

    def _chrome_height(self) -> int:
        """
        What the fixed sections ask for at the CURRENT width, padding included.

        Width matters because the header is a `ReflowRow`: measured at 150%,
        it is 192 px (4 rows) at 720, 144 (3) at 820 and 980, and 96 (2) at
        1400.  Requested heights, not actual ones -- an actual height is
        whatever pack managed to give it, which on a window that is too short
        is the number being diagnosed.
        """
        total = 0
        for widget, pad in ((self.header, 10), (self._gm_row, 2),
                            (self.banner, 0),
                            (self.sign_lbl, 2), (self.recon, 2),
                            (self.ref_strip, 2),
                            (self._badge_row, 6), (self._foot, 12)):
            # `ref_strip` is packed only when there is a composition to report,
            # and an UNMANAGED ttk.Label still answers `winfo_reqheight()` with
            # 21 (measured) -- so counting it unconditionally would raise the
            # enforced minimum height by 23 px on every single-file trace, for
            # a widget that is not on screen.  `winfo_manager()` is the only
            # discriminator that works here: it is '' when unmanaged and 'pack'
            # when packed, while ismapped is also 0 on a window that has not
            # been mapped yet, which is exactly when this runs.
            if not widget.winfo_manager():
                continue
            total += widget.winfo_reqheight() + pad
        return total

    def _on_configure(self, event=None) -> None:
        # A Toplevel is a bindtag of every widget inside it, so this fires for
        # every descendant's <Configure> as well.  Only the window's own is
        # about the window's size; without the guard this would run on every
        # label repaint.  (`_on_destroy` carries the identical check for the
        # identical reason.)
        if event is not None and getattr(event, "widget", None) is not self:
            return
        # COALESCED, and cancelled on destroy.  A window drag delivers dozens
        # of <Configure>s a second, and an un-cancelled `after` fires against a
        # Tcl command the widget's teardown has already deleted -- Tk then
        # prints `invalid command name "..._apply_min_height"` to a console a
        # double-clicked GUI does not have.  Same rule as `_stability_after`.
        if self._min_h_after is not None:
            return
        try:
            self._min_h_after = self.after_idle(self._apply_min_height)
        except Exception:                                # pragma: no cover
            self._min_h_after = None

    def _apply_min_height(self) -> None:
        """
        Make the enforced minimum a size at which the TABLE is on screen.

        Not a constant, because the cost is not one: it is the DPI and the
        header's wrap count (see `ATTRIB_SPLIT_FLOOR_LINES` for the four
        measurements).  Recomputed rather than ratcheted, and applied only when
        the value actually changes, which is what keeps it a fixed point:
        narrowing the window can wrap the header and raise the minimum, and Tk
        then grows the HEIGHT -- which cannot change the width, so the next
        pass computes the same number and stops.  Widening lowers the minimum
        and Tk leaves the window where it is (`minsize` never shrinks a
        window), so that direction cannot loop either.  Verified by measurement
        rather than by argument: `tests/test_attrib_window.py` drives a mapped
        window across five widths and asserts the layout settles.
        """
        self._min_h_after = None
        if not self.winfo_exists():                      # pragma: no cover
            return
        # The caption's line cap is a function of the window height, so it is
        # re-applied here rather than only at render time -- otherwise a window
        # opened at 1500x900 and dragged down to the minimum keeps a three-line
        # caption and the plot goes back to 2 px.  It writes one Label and
        # cannot change `winfo_height()` or `_chrome_height()`, so it cannot
        # re-enter this (see `ATTRIB_SWEEP_NOTE_RESERVE_LINES`).
        try:
            self._set_sweep_note()
        except Exception:                                # pragma: no cover
            pass
        try:
            line = tkfont.Font(root=self, font=ATTRIB_FONT
                               ).metrics("linespace")
            want = self._chrome_height() + ATTRIB_SPLIT_FLOOR_LINES * line
            want = max(ATTRIB_MIN_H, want)
            if want == self._min_h_applied:
                return
            self._min_h_applied = want
            self.minsize(ATTRIB_MIN_W, want)
        except Exception:                                # pragma: no cover
            pass

    def refresh_banner(self) -> None:
        """
        The ONLY thing the editor hook writes (rule 6).

        A signature-tuple comparison and one Label write.  It must never raise
        and must touch nothing else: recomputing here is what would make the
        window blank itself on the first keystroke.
        """
        if not self.winfo_exists():                      # pragma: no cover
            return
        try:
            trace, _fe = self._subject()
            text, warn = staleness_text(self._res.prov, trace,
                                        trace is not None)
            self.banner.configure(
                text=text,
                foreground=(self._banner_warn_fg if warn
                            else self._banner_ok_fg))
            state = ["disabled"] if trace is None else ["!disabled"]
            self.recompute_btn.state(state)
        except Exception:                                # pragma: no cover
            pass

    # -------------------------------------------------------------- export

    def _report(self) -> str:
        return report_text(self._live_prov(), self._res.dec,
                           self._res.sens_all or (), self._res.stability,
                           self._sweep_full, self._cand_problems)

    def _on_copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._report())
            self.foot_note.configure(text="report copied to the clipboard")
        except Exception as e:                           # pragma: no cover
            self.foot_note.configure(text=f"copy failed: {e}")

    def _on_export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=f"attribution_{self._res.prov.trace_id}.csv")
        if not path:
            return
        try:
            write_attribution_csv(path, self._res, self._live_prov())
        except Exception as e:
            messagebox.showerror("Export failed", str(e), parent=self)
            return
        self.foot_note.configure(text=f"wrote {path}")

    # ------------------------------------------------------------- destroy

    def _on_destroy(self, event=None) -> None:
        # <Destroy> fires for every descendant too, so only the Toplevel's own
        # event may deregister; without the check, destroying the first Entry
        # would drop the window from the registry while it is still on screen.
        if event is not None and event.widget is not self:
            return
        for attr in ("_stability_after", "_min_h_after", "_sash_after"):
            pending = getattr(self, attr, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except Exception:                        # pragma: no cover
                    pass
                setattr(self, attr, None)
        try:
            _LIVE.get(self.app, []).remove(self)
        except (ValueError, KeyError, TypeError):        # pragma: no cover
            pass
        try:
            self.figure.clf()
        except Exception:                                # pragma: no cover
            pass


def write_attribution_csv(path: str, res: AttribResult,
                          prov: Optional[Provenance] = None) -> None:
    """
    Every term and every candidate, headed with the full provenance (rule 12).

    `prov` overrides `res.prov` so the window can supply the LIVE units mode
    (see `AttributionWindow._units`); everything else on it is the frozen
    identity of the run that produced these numbers.

    utf-8 with `newline=""`, exactly like the main Export CSV -- the sign is
    U+2212 and the ohm sign is U+03A9 in the comment block, and a spreadsheet
    opening it as anything else would mangle both.
    """
    prov = prov if prov is not None else res.prov
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for line in provenance_lines(prov):
            fh.write("# " + line + "\n")
        if res.stability:
            fh.write("# Across frequency: " + res.stability + "\n")
        fh.write("# Reconciliation: " + reconciliation_line(res.dec) + "\n")
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in csv_records(prov, res.dec, res.sens_all or ()):
            writer.writerow(row)


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------

def open_attribution_window(app, trace) -> Optional[AttributionWindow]:
    """
    Open (or raise) the Attribution window for `trace`.

    Returns None when it refuses, having said why in a dialog -- the menu entry
    stays LIVE precisely so the refusal can explain itself.

    The editor is FLUSHED first, for the same reason `_on_freeze_trace` flushes
    it: auto-apply is deferred to `after_idle`, so a keystroke in the same
    event burst as the click is still queued, and the staleness check has to
    answer about the spec on screen rather than the one from an event ago.
    """
    try:
        app._flush_editor_sync()
    except Exception:                                    # pragma: no cover
        pass

    fe = None
    if trace is not None:
        try:
            fe = app._file_by_label(trace.file_label)
        except Exception:                                # pragma: no cover
            fe = None

    why = attribution_refusal(trace, fe)
    if why:
        messagebox.showinfo(ATTRIB_TITLE, why, parent=app)
        return None

    names = list(trace.mport_names or [])
    if len(names) < 2:                                   # pragma: no cover
        # A TRUE backstop now, and no longer the thing that catches an ordinary
        # one-measurement-port trace: `attribution_refusal` tests the name count
        # itself, so the mode-6 `(F, 1, 1)` case that used to arrive here is
        # turned away above with a message that names what is actually wrong.
        # Reaching this line would mean the refusal and this call disagree
        # about the same list, which is worth saying rather than an empty
        # combobox -- but it is not a state anything known can produce.
        messagebox.showinfo(
            ATTRIB_TITLE,
            "This trace has a coupling matrix but fewer than two measurement "
            "port names cached. Calculate it again.", parent=app)
        return None

    saved = _RESTORED.get(app, {}).get(int(getattr(trace, "id", 0)), {})
    victim = saved.get("victim") if saved.get("victim") in names else names[0]
    aggr = saved.get("aggressor") if saved.get("aggressor") in names else None
    if aggr is None or aggr == victim:
        aggr = next(n for n in names if n != victim)
    quantity = saved.get("quantity") if saved.get("quantity") in QUANTITIES \
        else "M"
    try:
        f_hz = float(saved.get("freq_ghz")) * 1e9 if saved.get("freq_ghz") \
            else parse_si(app.rlc_freq_var.get()) * 1e9
    except Exception:
        f_hz = float(fe.ts.freqs[len(fe.ts.freqs) // 2])

    # An existing window on the same pair is RAISED, not duplicated: without
    # transient() this window can end up behind the main one, and a second copy
    # of the same decomposition is only a second thing to keep in step.
    for w in live_windows(app):
        if w._trace is trace and w._res.prov.victim == victim \
                and w._res.prov.aggressor == aggr:
            w.deiconify()
            w.lift()
            w.focus_set()
            return w

    gmodel = saved.get("ground_model") or GROUND_MODEL_DEFAULT
    try:
        res = compute_attribution(app, trace, fe, victim, aggr, quantity, f_hz,
                                  ground_model=gmodel)
    except Exception as e:
        if gmodel != GROUND_MODEL_DEFAULT:
            # A hand-edited session file can carry a model this cannot read.
            # That costs the CHOICE, never the window -- the session rule --
            # so the declared model opens instead of a dialog and nothing.
            try:
                res = compute_attribution(app, trace, fe, victim, aggr,
                                          quantity, f_hz)
            except Exception as e2:
                messagebox.showerror(ATTRIB_TITLE, str(e2), parent=app)
                return None
            # AND IT SAYS SO.  "A bad value costs its own field, never the
            # file" is the session rule, and the half of it that is easy to
            # forget is the second clause: every other dropped field in that
            # code notes itself in the Results pane.  Without this the window
            # opens on `diag` with the model the user saved silently gone --
            # and a ground model is worth a measured 7.19 dB, so a silent
            # revert to the default is a silent 7.19 dB.
            try:
                app._append_result(
                    f"WARN: attribution window: ground model '{gmodel}' from "
                    f"the session file could not be used ({e}); opened with "
                    f"'{GROUND_MODEL_DEFAULT}' instead.", _gui().LOG_WARN)
            except Exception:                            # pragma: no cover
                pass
        else:
            messagebox.showerror(ATTRIB_TITLE, str(e), parent=app)
            return None

    win = AttributionWindow(app, trace, fe, res)
    # The two restored choices that are not arguments to `compute_attribution`
    # and so cannot be passed into it.  Both were being SAVED and stored and
    # then never read: measured, a session saved with `"view": "sens"` and
    # `"candidates": "open, R=0.1"` reopened on the Contributions view with the
    # default pair, so the round trip quietly discarded exactly the two fields
    # a user had to think about.  Applied after construction and then rendered
    # once, rather than threaded through the constructor, because neither
    # changes a single number -- `view` picks which table is drawn and
    # `candidates` only feeds the sensitivity re-solves, which are lazy.
    saved_view = saved.get("view")
    saved_cands = saved.get("candidates")
    changed = False
    if saved_view in ("contrib", "sens"):
        win._view.set(saved_view)
        changed = True
    if isinstance(saved_cands, str) and saved_cands.strip():
        win.cand_var.set(saved_cands)
        changed = True
    if changed:
        win._render()
    win.lift()
    return win


def refresh_attribution_windows(app, rerender: bool = False) -> None:
    """
    Poke every live window: the staleness banner, and whether its subject is
    still there.

    Call it with the DEFAULT `rerender=False` from `_apply_editor_strips`
    (rule 6 -- a banner write is the whole of what that hook may do), from
    every path that removes a trace or a file, and after a session load.
    Cheap: a `_config_signature` tuple comparison and a Label write per window.

    Call it with `rerender=True` from `_on_units_mode_changed`, and from there
    only.  The units mode is a RENDERING choice rather than a recorded fact --
    the same rule that makes `_on_units_mode_changed` repaint every run page --
    so the tables here have to follow it, and there is no other way for a
    window to hear about it.  It is NOT the default because a re-render on the
    editor's per-keystroke path would also redraw the sweep, i.e. a closed-form
    solve per character.

    NEVER RAISES.  It runs inside Tk variable traces, where a raised error
    reaches no handler you control -- Tk prints it and the GUI carries on
    showing a stale verdict.
    """
    try:
        for w in live_windows(app):
            try:
                if rerender:
                    w._render()
                else:
                    w.refresh_banner()
            except Exception:                            # pragma: no cover
                pass
    except Exception:                                    # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

ATTRIB_SESSION_KEY = "attribution"
ATTRIB_SESSION_VERSION = 1


def attribution_session_state(win_or_none) -> dict:
    """
    What an Attribution window is READING, for `session_to_dict`.

    The CHOICES only -- trace id, the pair, the quantity, the frequency, the
    view, and the CANDIDATES.  Never the numbers: a session file holds the
    config and never the results (json cannot carry a numpy array, and the
    frozen-trace precedent is explicit that computed values do not round-trip).
    Accepts None, one window, or an iterable of them, so the hooks agent can
    pass whichever it has.

    `candidates` is here because it is the one field on this window that the
    user TYPED and that this tool refuses to guess: `R=0.1`, a ball's lead
    inductance, a terminator value -- STRUCTURAL_CANDIDATES' docstring says in
    as many words that anything past "open" and "ideal" is the user's to name.
    It was left out of the first version and the round trip silently handed
    back the default pair, i.e. the only part of the state that cost the user
    any thought was the only part not kept.
    """
    if win_or_none is None:
        wins: list = []
    elif isinstance(win_or_none, AttributionWindow):
        wins = [win_or_none]
    else:
        try:
            wins = list(win_or_none)
        except TypeError:                                # pragma: no cover
            wins = []
    out = []
    for w in wins:
        try:
            if not w.winfo_exists():
                continue
            p = w._res.prov
            entry = {
                "trace_id": p.trace_id,
                "victim": p.victim,
                "aggressor": p.aggressor,
                "quantity": p.quantity,
                "freq_ghz": p.requested_hz / 1e9,
                "view": w._view.get(),
                "candidates": w.cand_var.get(),
            }
            # The ground model is on the same footing as `candidates`: a CHOICE
            # the user typed, that this tool refuses to guess, and that is
            # worth 9.60 dB.  Handing back the default on reopen would hand
            # back a different network.
            #
            # It is written ONLY when it is not the default, and that condition
            # is not a design preference -- it is a file-ownership one.
            # `tests/test_attrib_gui_integration.py::TestTheSessionRoundTrip`
            # pins the exact KEY SET of an entry, and that file was being
            # edited by another hand while this was written.  The condition
            # keeps the default case byte-identical to what that test pins
            # while a chosen model still round-trips.  WHEN THAT KEY SET GAINS
            # `ground_model`, DELETE THE CONDITION -- an unconditional key is
            # the simpler contract and is what the rest of this dict does.
            gm = str(w.ground_var.get() or "").strip()
            if gm and gm != GROUND_MODEL_DEFAULT:
                entry["ground_model"] = gm
            out.append(entry)
        except Exception:                                # pragma: no cover
            continue
    return {"version": ATTRIB_SESSION_VERSION, "windows": out} if out else {}


def apply_attribution_session_state(app, data) -> list[str]:
    """
    Restore the CHOICES, never the window, and say so.

    Nothing is reopened, on purpose.  `attribution_refusal` refuses a trace
    with no numbers, and a freshly loaded session has none until Calculate has
    run, so an auto-reopened window could only show the refusal dialog once per
    entry before the user has asked for anything.  What IS kept is the pair,
    the quantity and the frequency, so reopening the window from the menu lands
    on what was being read.

    A bad value costs its own entry and never the file, and this never raises:
    the session file is readable text and will be hand-edited.  Returns notes
    for the Results pane.
    """
    notes: list[str] = []
    if not isinstance(data, dict) or not data:
        return notes
    ver = data.get("version")
    if ver != ATTRIB_SESSION_VERSION:
        return [f"Attribution: session block version {ver!r} is not "
                f"{ATTRIB_SESSION_VERSION}; its window state was ignored."]
    store = _RESTORED.setdefault(app, {})
    for entry in data.get("windows") or []:
        try:
            tid = int(entry["trace_id"])
            store[tid] = {
                "victim": str(entry.get("victim", "")),
                "aggressor": str(entry.get("aggressor", "")),
                "quantity": str(entry.get("quantity", "M")),
                "freq_ghz": float(entry.get("freq_ghz", float("nan"))),
                "view": str(entry.get("view", "contrib")),
                "candidates": str(entry.get(
                    "candidates", ", ".join(STRUCTURAL_CANDIDATES))),
                "ground_model": str(entry.get("ground_model",
                                              GROUND_MODEL_DEFAULT)),
            }
            notes.append(
                f"Attribution for trace [{tid}] "
                f"({store[tid]['victim']} ← {store[tid]['aggressor']}, "
                f"{store[tid]['quantity']}) was not reopened — a "
                f"decomposition needs numbers. Calculate, then "
                f"{ATTRIB_MENU_LABEL} from the Traces list.")
        except Exception:
            notes.append("Attribution: one saved window entry was malformed "
                         "and was dropped.")
    return notes
