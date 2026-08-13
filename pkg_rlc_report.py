"""
pkg_rlc_report.py  --  turning a finished run into TEXT.

Split out of pkg_rlc_gui.py, verbatim.  Everything here is pure: it takes the
run snapshots (`RowSnapshot` / `CouplingSnapshot` / `RunSnapshot`, which are
read by duck typing, never imported) plus the units mode, and returns strings.
No Tk, no App, no widget -- which is why the whole Results pane can be pinned
byte-for-byte by tests/fixtures/render_reference.json with no display.

What is in here: the three results VIEWS (detail / summary / compare) and the
formatters under them, the run-tab and Log-tab labels, the run-to-run diff, the
coupling ranking, and the frequency-provenance types the reports print through.

What is deliberately NOT: `_tag_swatch_rows`, which WRITES INTO A Tk TEXT and
so is not a formatter at all; and `trace_signature_fields` / `run_signatures`,
which read a live `TraceConfig`.  Both stay in pkg_rlc_gui.

`_write_coupling_csv` is not here either -- it lives in pkg_rlc_csv, because a
CSV is a file format and not a rendering of the results pane.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Optional, Sequence

import numpy as np

from pkg_rlc_core import RECIPROCITY_WARN, format_si


# ---- the three results views ----------------------------------------------
#
# One run, three renderings, chosen by the reader and never by the code.  They
# exist because the report had exactly one shape and it was the widest one:
# measured on a two-trace mode-6 run, 40 lines and 3538 characters against a
# pane that shows 144 columns at the default 1500x900 window (79 at the
# 1040x600 minsize) and does NOT wrap, with 12 lines over 90 columns and the
# widest at 272.
#
#   detail   -- everything, one block per trace.  What the tool always had.
#   summary  -- two tables for the whole run, one row per port and one per
#               pair.  Reading ACROSS traces is a matter of reading down a
#               column instead of paging between blocks 17 lines apart.
#   compare  -- traces become COLUMNS, with a delta.  This is the one that
#               answers "what did this EM revision change", which is what a
#               run with two versions of one structure in it is for.
#
# The choice is a RENDERING choice, not a recorded fact -- the same rule as
# the units mode, and the reason both are read live off the App by
# `_run_report_segments` rather than frozen onto a RunSnapshot.
VIEW_DETAIL = "detail"
VIEW_SUMMARY = "summary"
VIEW_COMPARE = "compare"
RESULTS_VIEWS = (VIEW_DETAIL, VIEW_SUMMARY, VIEW_COMPARE)

#: The Results pane's MEASURED width in characters at the default 1500x900
#: window: 1014 px of Consolas 9, every glyph this report emits being 7 px.
#: (102 at 1200x800, 79 at the 1040x600 minsize.)  `wrap=tk.NONE`, so a line
#: past this is reachable only by a horizontal scroll that takes the leftmost
#: column off the edge at the same time -- which is why it is a budget the
#: formatters spend rather than a number they may exceed.
RESULTS_PANE_COLS = 144

#: How many lines of stacked trace name a compare header may spend before the
#: view switches to the numbered legend instead.  Past this the header is taller
#: than the group of numbers it labels and the name reads as a column of
#: syllables; the legend says the same thing in one line per trace.
COMPARE_STACK_LINES_MAX = 4

#: Floor on a stacked segment.  Below it a name is shredded into more lines than
#: a reader can reassemble, whatever the depth cap says.
COMPARE_SEG_MIN = 6

#: Ceiling on a label anywhere it is one cell of a row (both summary tables).
#: Not a width budget -- `_render_columns` already sizes that column to its
#: widest cell, and the measured cost of the full names on the reported run is
#: 10 columns of 144.  It is a backstop against a pasted path for a label.
SUMMARY_LABEL_MAX = 40


# ============================================================================
# Frequency provenance -- what a printed marker frequency actually IS
# ============================================================================
#
# extract_rlc_at_freq and extract_coupling_at_freq both pick their point with
# argmin(|freqs - target|) and report nothing at all about the distance, so the
# tool used to print TWO different frequencies on one screen and explain
# neither: the Calculate header and the run page printed f_rlc_hz (what the
# user typed) while the Z-matrix line printed cres.freq_hz (the point the
# numbers actually came from).  A real user read "@ 5.6 GHz" and "@ 5.512 GHz"
# in the same report and had no way to know which one their L belonged to.
#
# It is not a corner case.  Measured on tests/fixtures/diff_pair_4port.s4p
# (401 points, 1 MHz .. 10 GHz, step 24.9975 MHz) at the default marker of
# 0.1 GHz: the nearest point is 0.10099 GHz.  Every default session in this
# repo snaps by 990 kHz, and said nothing.
#
# FreqSnap is that fact as a value and marker_freq_text is the ONE renderer for
# it -- the Calculate header, the run headline, the run page, the results
# table, the Z-matrix line and the CSV all go through it, so they cannot drift
# apart again.  THE RULE: when the requested frequency IS a data point, every
# one of those renders byte-for-byte what it rendered before.  The common case
# must not grow a parenthetical, tests elsewhere pin those strings, and
# tests/fixtures/render_reference.json pins the Z-matrix line.

# A difference smaller than this fraction of the grid step is float noise, not
# a snap.  The noise is real and it comes from the parser's UNIT SCALING, not
# from parse_si (which is exact for every value anyone types: "5.6" -> 5.6e9 to
# the bit).  A file written in MHz or kHz carries its axis as decimal text that
# is multiplied by 1e6 / 1e3, and `33023.73 * 1e6` is 33023730000.000004 where
# the same point typed as "33.02373" GHz is 33023730000.0 exactly -- measured,
# worst case 3.8e-6 Hz over a 400-point decimal sweep in either unit.  Against
# that, the snaps worth reporting are megahertz: the default marker on
# diff_pair_4port.s4p moves 990 kHz.  1e-6 of that file's 25 MHz step is 25 Hz,
# which sits between the two with ten orders of magnitude to spare on each side.
FREQ_EXACT_FRAC = 1e-6
# ... and with no gap to scale against (a one-point sweep), relative to the
# requested frequency instead.
FREQ_EXACT_REL = 1e-9
# A sweep counts as uniform when every gap is within this fraction of the
# median gap.  Real linear sweeps carry decimal round-off in the axis (the
# fixture above: 0.0 spread); a log sweep or a band densified round a resonance
# is orders of magnitude away from passing, and gets "nearest point" with no
# step rather than a made-up number.
FREQ_UNIFORM_TOL = 1e-3

# Precision used by every site ONCE it has to name two frequencies at once.
# Each caller keeps its own historical precision for the unchanged case (the
# banner has always printed 4 significant digits, the run headline 3 decimals,
# the Z-matrix line 6), but a line whose whole job is to tell two nearby
# frequencies apart must not round them into each other -- and two sites
# rounding one point differently, on one screen, is the very shape of the
# disagreement this section exists to end.  Measured: at 4 significant digits
# the banner said "0.101 GHz" over a table saying "0.10099 GHz".
FREQ_WIDE_FMT = "{:.6g}"


@dataclass(frozen=True)
class FreqSnap:
    """Where a value was actually read, against where it was asked for.

    Floats only, deliberately: this ends up on a RunSnapshot, and
    tests/test_run_snapshot.py walks every ndarray reachable from a run to
    prove a record does not grow with the sweep.
    """
    requested_hz: float
    # NaN means "not resolved against any grid" -- a record restored before any
    # Calculate, or a pure-text caller.  Such a snap renders like a bare float.
    actual_hz: float = float("nan")
    # The sweep's step, NaN when it is not uniform.  Display only.
    step_hz: float = float("nan")
    # The widest gap adjacent to the chosen point.  This, not `step_hz`, is
    # what the snap is JUDGED against -- see `off_grid`.
    local_step_hz: float = float("nan")
    # False when several sweeps in one run resolved to different points, so
    # there is no single frequency to print.
    agreed: bool = True

    @property
    def resolved(self) -> bool:
        return math.isfinite(self.actual_hz)

    @property
    def delta_hz(self) -> float:
        if not self.resolved:
            return float("nan")
        return self.actual_hz - self.requested_hz

    @property
    def exact(self) -> bool:
        """True when the requested frequency IS a data point.

        This is the predicate that keeps the common case silent, so it has to
        tolerate float noise: see FREQ_EXACT_FRAC.
        """
        if not self.resolved:
            return True
        d = abs(self.delta_hz)
        if d == 0.0:
            return True
        if math.isfinite(self.local_step_hz) and self.local_step_hz > 0.0:
            return d <= FREQ_EXACT_FRAC * self.local_step_hz
        return d <= FREQ_EXACT_REL * max(abs(self.requested_hz), 1.0)

    @property
    def off_grid(self) -> bool:
        """The requested frequency is not between two points -- it is OUTSIDE
        the swept band, and that is what earns a warning rather than a note.

        For any monotone axis the two statements are the same one.  If the
        target lies inside the band it falls in some gap [f_i, f_i+1], and the
        nearer end of that gap is at most half of it away -- so a distance
        greater than half the adjacent gap can only mean the target is off the
        end.  Judging against the LOCAL gap rather than the median is what
        makes this hold on a log sweep too, and taking the WIDER of the two
        adjacent gaps is what keeps it free of false alarms where the spacing
        changes.
        """
        if self.exact or not self.resolved:
            return False
        if math.isfinite(self.local_step_hz) and self.local_step_hz > 0.0:
            return abs(self.delta_hz) > 0.5 * self.local_step_hz
        # A one-point sweep has no gap at all: anything but that point is a
        # request the file cannot answer.
        return True


def freq_grid_step(freqs) -> float:
    """The sweep's step in Hz, or NaN when the sweep is not uniform."""
    f = np.asarray(freqs, dtype=float).ravel()
    if f.size < 2:
        return float("nan")
    d = np.abs(np.diff(f))
    med = float(np.median(d))
    if not math.isfinite(med) or med <= 0.0:
        return float("nan")
    if float(np.max(np.abs(d - med))) > FREQ_UNIFORM_TOL * med:
        return float("nan")
    return med


def snap_to_grid(freqs, requested_hz: float) -> FreqSnap:
    """
    Resolve a requested marker frequency against a real frequency axis, the
    same way extract_rlc_at_freq / extract_coupling_at_freq do -- and keep the
    two things they throw away: how far it moved, and how coarse the grid is.

    Measured cost: 13.4 us on the 401-point fixture and 26.4 us on a
    5000-point sweep (median of five runs of 2000 calls, numpy 2.x).  It runs
    once per FILE per Calculate, not once per trace, so it is invisible next to
    the reduction it precedes.
    """
    f = np.asarray(freqs, dtype=float).ravel()
    req = float(requested_hz)
    if f.size == 0 or not math.isfinite(req):
        return FreqSnap(requested_hz=req)
    idx = int(np.argmin(np.abs(f - req)))
    actual = float(f[idx])
    gaps = []
    if idx > 0:
        gaps.append(abs(actual - float(f[idx - 1])))
    if idx + 1 < f.size:
        gaps.append(abs(float(f[idx + 1]) - actual))
    return FreqSnap(requested_hz=req, actual_hz=actual,
                    step_hz=freq_grid_step(f),
                    local_step_hz=max(gaps) if gaps else float("nan"))


def combine_freq_snaps(snaps) -> Optional[FreqSnap]:
    """
    One FreqSnap for a whole run.  None when there is nothing to combine.

    Two traces may name two different files -- multi-file comparison is a
    feature, not an accident -- and two files rarely carry the same sweep, so a
    run does not always HAVE one frequency.  When the resolved points differ
    the combined snap says so (`agreed=False`) instead of picking one of them,
    which would be the same silent snap committed one level up.
    """
    snaps = [s for s in snaps if s is not None]
    if not snaps:
        return None
    resolved = [s for s in snaps if s.resolved]
    if not resolved:
        return FreqSnap(requested_hz=snaps[0].requested_hz)
    if len({s.actual_hz for s in resolved}) > 1:
        return replace(resolved[0], agreed=False)
    return resolved[0]


def marker_freq_text(freq, fmt: str = "{:.4g}") -> str:
    """
    THE renderer for a printed marker frequency, with its provenance.

    `freq` is a FreqSnap, or a bare frequency in Hz for the sites that have no
    grid to compare against (a run record restored before any Calculate).
    `fmt` formats the value in GHz and is the caller's existing precision, so
    that the unchanged case really is unchanged.

    A bare float, an unresolved snap and an exact snap all render as the plain
    "<f> GHz" this tool has always printed.  Returns "" when there is no finite
    frequency at all -- the caller decides what to say instead, because "no
    marker" is a sentence and this function returns a value.
    """
    if not isinstance(freq, FreqSnap):
        if freq is None or not math.isfinite(float(freq)):
            return ""
        return f"{fmt.format(float(freq) / 1e9)} GHz"

    if not math.isfinite(freq.requested_hz):
        return ""
    # Every branch below this point prints two frequencies, or names one that
    # is not the one the numbers came from, so all of them use FREQ_WIDE_FMT
    # rather than the caller's precision -- see its comment.  `fmt` governs the
    # unchanged case and only the unchanged case.
    req_txt = FREQ_WIDE_FMT.format(freq.requested_hz / 1e9)
    if not freq.agreed:
        # No single number is true here, so no single number is printed.  Two
        # different things arrive at this branch -- several FILES whose sweeps
        # disagree (combine_freq_snaps) and a table holding a row from an
        # earlier run at another marker (_table_freq_note) -- so the wording
        # states the fact both have in common and points at nothing it may not
        # be able to deliver.  The per-file lines under the table and each
        # coupling block's own Z-matrix line carry the individual points where
        # they exist.
        return (f"several points  (requested {req_txt} GHz; the values are not "
                f"all at one frequency)")
    if not freq.resolved or freq.exact:
        hz = freq.actual_hz if freq.resolved else freq.requested_hz
        return f"{fmt.format(hz / 1e9)} GHz"

    # Snapped.  The PRIMARY number is the point the numbers came from -- that
    # is the whole correction -- and the bracket names what was asked for.
    act_txt = FREQ_WIDE_FMT.format(freq.actual_hz / 1e9)
    if req_txt == act_txt:
        # Different, but not at this precision.  Widening the one in brackets
        # beats printing "0.1 GHz (requested 0.1 GHz)", which reads as a bug.
        req_txt = f"{freq.requested_hz / 1e9:.9g}"
    if freq.off_grid:
        return (f"{act_txt} GHz  (requested {req_txt} GHz is outside the swept "
                f"band; nearest point, {format_si(abs(freq.delta_hz), 'Hz')} "
                f"away)")
    if math.isfinite(freq.step_hz):
        return (f"{act_txt} GHz  (requested {req_txt} GHz; nearest point, grid "
                f"step {format_si(freq.step_hz, 'Hz')})")
    return f"{act_txt} GHz  (requested {req_txt} GHz; nearest point)"


# Header units (kept aligned with format_si base unit). Tk Text uses a
# monospace font, so rendering 'Ω' is fine.
_TABLE_BASE_UNITS = {"R": "Ω", "L": "H", "C": "F", "Q": ""}

# Aligned mode: pick the column unit by the largest absolute value seen.
_ALIGNED_PREFIXES = [
    (-15, "f"), (-12, "p"), (-9, "n"), (-6, "u"), (-3, "m"),
    (0, ""), (3, "k"), (6, "M"), (9, "G"),
]


def _aligned_prefix_for(values):
    """Pick the SI prefix exponent best suited for the largest |v| in `values`."""
    finite = [abs(v) for v in values if math.isfinite(v) and v != 0.0]
    if not finite:
        return 0, ""
    largest = max(finite)
    log10 = math.log10(largest)
    chosen = (-15, "f")
    for exp, pfx in _ALIGNED_PREFIXES:
        if log10 >= exp:
            chosen = (exp, pfx)
        else:
            break
    return chosen


def _fmt_aligned(value: float, exp: int, sig: int = 4) -> str:
    if not math.isfinite(value):
        return "nan"
    return f"{value / (10 ** exp):.{sig}g}"


def _sign_flag(res) -> str:
    """Compact flag: always 'cap' or 'ind' (per Im(Z) sign), plus 'R<0' if non-passive."""
    flags = []
    if math.isfinite(res.L_henry):
        if res.L_henry < 0:
            flags.append("cap")
        elif res.L_henry > 0:
            flags.append("ind")
    if math.isfinite(res.R_ohm) and res.R_ohm < 0:
        flags.append("R<0")
    return ",".join(flags)


# The swatch that heads every data row of the results table, coloured with a
# Text tag to match the curve (see App._append_swatched).
#
# WIDTH-STABLE, measured with tkinter.font in the Results pane's own font
# (Consolas 9, the only font this table is ever rendered in): '█' is 7 px
# and so is ' ', 'M', 'X' and '0' -- i.e. exactly one monospace cell, so the
# swatch column costs the same on a data row as on the header and legend rows
# and nothing below it shifts.  Rejected on the same measurement: '▇'
# (12 px) and '▰' (10 px), either of which would have knocked the header
# out of line with the numbers under it.  Every state this column can take is
# one of those two glyphs; there is no third.
RESULTS_SWATCH = "█"
_SWATCH_PAD = " " * len(RESULTS_SWATCH)


# --------------------------------------------------------- the Log tab badge
#
# Severity of a line written to the Results pane.  INFO is what every call
# site had before the pane became a notebook, so the default keeps the old
# behaviour exactly; WARN counts towards the Log tab's badge and ERROR also
# brings the Log tab to the front.
LOG_INFO = "info"
LOG_WARN = "warn"
LOG_ERROR = "error"

# The badge counts unseen warnings, and it stops counting at 99.  The cap is
# not cosmetic: the number of DIGITS decides the label's width, and a label
# that changes width on the LEFTMOST tab reflows every tab to its right.
LOG_BADGE_CAP = 99


def log_tab_label(unseen: int) -> str:
    """
    The Log tab's text, at a width that never changes.

    Measured with tkinter.font in the tab strip's own font (TkDefaultFont =
    Microsoft YaHei UI 9, the vista theme's TNotebook.Tab font): ' ' and '!'
    are both 4 px and every digit is 7 px, so "Log  00", "Log !03" and
    "Log !99" all measure 44 px.  That is the whole reason the count is
    zero-padded to two digits and shown even when it is zero: 'Log' with no
    digits at all cannot be padded to the same width, because a space is 4 px
    and the widths differ by 15 px, which is not a multiple of 4.  (Checked:
    22 + 4a == 37 + 4b has no integer solution.)  The marker character, not
    the presence of a number, is what says there is something to read.
    """
    n = max(0, min(int(unseen), LOG_BADGE_CAP))
    return f"Log {'!' if n else ' '}{n:02d}"


# ------------------------------------------------------- the run history tabs
#
# TWO DISJOINT SETS, and that is what makes the all-locked deadlock UNREACHABLE
# rather than handled:
#
#   * the AUTO RING -- what Calculate writes into.  Never kept, evicted
#     oldest-first, silently.  Calculate touches nothing else.
#   * the KEPT SET  -- entered only by the user pressing Keep, hard-capped, and
#     never evicted by anything automatic.
#
# So Calculate can never block, never prompt, and never destroy something the
# user asked to keep.  The cap on the kept set is enforced AT THE MOMENT THE
# USER PRESSES KEEP -- at the cap the button is already disabled and says why
# -- which is the only place a refusal can be attached to an action the user
# actually took.
#
# THE NUMBERS.  Measured on the vista theme: the notebook COMPRESSES tabs, it
# never wraps, so the strip's requested height is constant and a long strip
# cannot steal plot height (nb reqheight 172 px at 1 tab and at 32).  It also
# cannot reach the outer sash -- the left panel is a fixed-width frame with
# pack_propagate(False) and weight=0, measured unmoved at 50 tabs and 8808 px
# of requested strip width.  What DOES bind is legibility: in the 575 px pane
# at the 1040x600 minsize a tab is ~47 px up to 12 tabs and then collapses
# (39 px at 16, 22 px at 30 -- about three characters), and at 150% DPI the
# natural tab is 73 px so clipping starts at 9.  Hence the default total of 8
# and the hard cap of 12, and hence the Runs menubutton: Tk 8.6's ttk.Notebook
# has no tab-strip scrolling and no overflow chevron, so a menu listing the
# full descriptions is the only way a compressed tab stays identifiable.
RUN_AUTO_DEFAULT = 3            # auto ring size (user-settable)
RUN_AUTO_MAX_UI = 6             # the largest auto ring the Runs menu offers
RUN_TABS_DEFAULT = 8            # default total run tabs (auto + kept), no Log
RUN_TABS_MIN = 2                # a total below this leaves no room to keep one
RUN_TABS_HARD_CAP = 12          # measured: labels stop being readable past this

# The unseen marker and the kept marker are WIDTH-STABLE GLYPH PAIRS: one of
# each pair is emitted always, never a conditional glyph.  A run tab that
# changes width reflows every tab on a compressing strip.
#
# Measured with tkinter.font in the tab strip's own font (TkDefaultFont =
# Microsoft YaHei UI 9, what the vista theme's TNotebook.Tab uses):
#     '!'  4 px   ' '  4 px      -> EQUAL   (the Log badge's own pair)
#     '☑' 12 px   '☐' 12 px      -> EQUAL   (the Traces list's own pair)
#     '🔒' 16 px  '🔓' 16 px      -> EQUAL but 16 px per tab and emoji-font bound
#     '*'  5 px   ' '  4 px      -> DIFF by 1 px, and NO blank glyph in this
#                                   font measures 5 px (checked U+0020, 00A0,
#                                   2002, 2003, 2005..200A, 2007, 2008, 205F,
#                                   3000 -- 2, 3, 4, 6, 8 and 12 px, never 5).
# So the brief's leading '*' is not width-stable here and '!' is, in the same
# notebook, already meaning "there is something here you have not read".
RUN_MARK_NEW = "!"
RUN_MARK_SEEN = " "
RUN_KEPT_GLYPH = "☑"
RUN_OPEN_GLYPH = "☐"

# How many "what changed" items line 2 spells out before it stops counting.
RUN_CHANGE_ITEMS = 4
# How wide a single changed value is rendered before it is elided.
RUN_CHANGE_VALUE_W = 22


def run_tab_label(number: int, when, kept: bool, unseen: bool) -> str:
    """
    A run tab's text: short, and the same width in every state.

    Identity is the RUN NUMBER, not the clock -- nobody remembers what they
    were doing at 14:32 and twenty runs are all at 5 GHz.  The time is on the
    label only as a rough "how long ago"; the full description lives in the
    Runs menu and on line 1 inside the tab.
    """
    hhmm = when.strftime("%H:%M") if when is not None else "--:--"
    return (f"{RUN_MARK_NEW if unseen else RUN_MARK_SEEN}"
            f"{RUN_KEPT_GLYPH if kept else RUN_OPEN_GLYPH}"
            f"#{int(number)} {hhmm}")


def _run_marker_text(freq) -> str:
    """
    '@ <freq>' for a run, with its provenance when the marker snapped.

    Takes a FreqSnap or a bare Hz value -- a record built before any Calculate
    has only the number the user typed, and renders exactly as it always did.
    """
    text = marker_freq_text(freq, "{:.3f}")
    return f"@ {text}" if text else "no marker"


def run_trace_ids(run: "RunSnapshot") -> list[int]:
    """The trace ids this run produced numbers for, in order, without repeats."""
    out: list[int] = []
    for rec in tuple(run.rows) + tuple(run.blocks):
        if rec.id not in out:
            out.append(rec.id)
    return sorted(out)


def run_freq_snap(run: "RunSnapshot"):
    """
    This run's marker as a FreqSnap -- or as the bare requested Hz value when
    the record carries no resolved grids (a run built before any Calculate).

    `marker_freq_hz` stays the REQUESTED frequency and nothing here changes
    that: it is the run's identity, it is what the entry box says, and several
    tests pin it.  Where the numbers were read is `freqs`, one entry per file
    the run touched.
    """
    if not run.freqs:
        return run.marker_freq_hz
    return combine_freq_snaps([s for _, s in run.freqs])


def run_file_freq(run: "RunSnapshot", file_label: str):
    """This run's marker as it resolved against ONE file's sweep."""
    for lbl, snap in run.freqs:
        if lbl == file_label:
            return snap
    return run_freq_snap(run)


def run_headline(run: "RunSnapshot") -> str:
    """Line 1 inside a run tab, and the Runs menu's entry for it."""
    ids = run_trace_ids(run)
    when = run.when.strftime("%H:%M:%S") if run.when is not None else "--:--:--"
    plural = "trace" if len(ids) == 1 else "traces"
    return (f"Run #{run.number} · {when} · {_run_marker_text(run_freq_snap(run))}"
            f" · {len(ids)} {plural} [{','.join(str(i) for i in ids)}]")


def run_stale_banner(newest_number: int) -> str:
    """
    Line 3, on every tab that is not the newest.

    Mandatory.  Without it three surfaces on one screen disagree with nothing
    to explain it: the tab shows run #3, the plot 200 px below it shows run #7,
    and Export CSV pressed while reading this page writes run #7.
    """
    return (f"! the plot and Export CSV show run #{newest_number}, "
            f"not this page")


def keep_button_label(kept: int, cap: int, state: str,
                      long: bool = False) -> str:
    """
    The Keep button's text.  `state` is one of:

      'none' -- the Log is on screen, so there is no run to keep
      'kept' -- the run on screen is already kept
      'free' -- it can be kept
      'full' -- the kept set is at its cap, and the label has to say so,
                because a disabled button with no reason is a bug report

    `long` is the difference between a slot and a menu.  On the BUTTON, 'full'
    reads 'Keep (5/5) — full': the sentence that says what to do about it does
    not survive the slot.  Measured with TkDefaultFont scaled 1.5x (the
    supported 150% DPI) at the 1040x600 minsize, the Results header is 575 px
    and requests 687, and the Keep button is the LAST of five packed side=LEFT
    -- so pack gave it the 213 px that were left and clipped
    'Keep (5/5) — close a kept run first' mid-phrase, with winfo_ismapped()
    still 1 so no ismapped assertion could see it.  A reason that is
    unreadable is the state the rule exists to prevent.  The sentence lives on
    the tab strip's right-click entry, which is not width-bound.
    """
    if state == "none":
        return "Keep run"
    if state == "kept":
        return f"Kept ({kept}/{cap})"
    if state == "full":
        if long:
            return f"Keep ({kept}/{cap}) — close a kept run first"
        return f"Keep ({kept}/{cap}) — full"
    return f"Keep run ({kept}/{cap})"


def describe_run_change(prev: tuple, cur: tuple,
                        max_items: int = RUN_CHANGE_ITEMS) -> list[str]:
    """
    What changed between two runs' signatures, as short human phrases.

    A trace that appeared or went away is reported as such: "nothing changed"
    beside a table that grew a row would be a false claim.
    """
    prev_map = dict(prev)
    cur_map = dict(cur)
    items: list[str] = []
    for tid, fields_now in cur_map.items():
        was = prev_map.get(tid)
        if was is None:
            items.append(f"[{tid}] added")
            continue
        for (name, new), (_, old) in zip(fields_now, was):
            if new != old:
                items.append(
                    f"[{tid}] {name} "
                    f"{_trunc_str(old, RUN_CHANGE_VALUE_W) or '(none)'} -> "
                    f"{_trunc_str(new, RUN_CHANGE_VALUE_W) or '(none)'}")
    for tid in prev_map:
        if tid not in cur_map:
            items.append(f"[{tid}] removed")
    if len(items) > max_items:
        extra = len(items) - max_items
        items = items[:max_items] + [f"… +{extra} more"]
    return items


def run_change_line(prev_number: int, items: Sequence[str]) -> str:
    """Line 2 inside a run tab. Empty when nothing changed -- the line is then
    not printed at all, which is itself the message."""
    if not items:
        return ""
    return f"changed since #{prev_number}:  " + ";  ".join(items)


def _row_file_labels(rec) -> list[str]:
    """Every file one snapshot was built from, home first.

    `getattr` rather than `rec.files`: tests/_render_capture.py and several
    test helpers build these records directly, and this must read a record
    written before the field existed as the single-file record it is.
    """
    files = getattr(rec, "files", ()) or ()
    return [lbl for _alias, lbl in files] or [rec.file_label]


def _snapshot_file_legend(rec) -> str:
    """'files: F1=die.s6p + F2=pkg.s4p', or 'file: coil.s4p' for one file."""
    files = getattr(rec, "files", ()) or ()
    if not files:
        return f"file: {rec.file_label}"
    return "files: " + " + ".join(f"{a}={lbl}" for a, lbl in files)


def _table_freq_note(rows: Sequence[RowSnapshot],
                     freq: Optional[FreqSnap]) -> str:
    """
    The results table's "read at" line, or "" when there is nothing to say.

    The ACTUAL frequency is taken from the rows, not from `freq`: every
    RLCResult carries the point it was read at, and a row Calculate did not
    produce this run -- a frozen trace, or one that "Calculate This Trace"
    skipped -- carries an older one.  So the rows decide where, and `freq`
    only supplies what was asked for.  When the rows disagree among
    themselves the line says so instead of picking one, which is the same
    rule combine_freq_snaps follows for several files.
    """
    if not isinstance(freq, FreqSnap) or not math.isfinite(freq.requested_hz):
        return ""
    actuals = set()
    for r in rows:
        hz = getattr(r.res, "freq_hz", float("nan"))
        if hz is not None and math.isfinite(hz):
            actuals.add(float(hz))
    if not actuals:
        return ""
    if len(actuals) == 1:
        shown = replace(freq, actual_hz=actuals.pop(), agreed=True)
    else:
        shown = replace(freq, agreed=False)
    if shown.agreed and shown.exact:
        return ""
    return f"{_SWATCH_PAD} ! read at: {marker_freq_text(shown, '{:.6g}')}"


def _file_alias_map(records) -> tuple:
    """
    ({file label: 'F1'}, [file labels in order of first appearance]).

    One entry per FILE, not per record: a COMPOSED record names several, and
    its first is its home file, so a single-file record and the home file of a
    composed one land on the same alias.  Shared by every view, because two
    tables on one screen calling one file by two letters is the collision this
    scheme exists to avoid.
    """
    order: list = []
    seen = set()
    for r in records:
        for fl in _row_file_labels(r):
            if fl not in seen:
                seen.add(fl)
                order.append(fl)
    return {fl: f"F{i + 1}" for i, fl in enumerate(order)}, order


def _file_cell(rec, alias: dict) -> str:
    """'F1' for one file, 'F1+F2' for a composition."""
    return "+".join(alias[fl] for fl in _row_file_labels(rec))


def _render_columns(headers: Sequence, aligns: Sequence[str],
                    rows: Sequence[Sequence[str]], lead: str = "",
                    gap: str = "  ") -> list:
    """
    A monospace table: every column as wide as its widest cell OR its header.

    THE HEADER COUNTS TOWARDS THE WIDTH.  Sizing on the values alone puts a
    7-character value under a 5-character heading and throws the heading one
    place off the numbers it names -- the cursor-readout rule, and the same one
    the Attribution window's tables are built on.  Nothing is capped and
    nothing is ellipsised here: a clipped NUMBER is a plausible wrong number,
    so callers truncate their own text cells before they get here.

    A HEADER CELL MAY BE SEVERAL LINES: pass a list of strings instead of a
    string and the header is rendered that deep, each line exactly where the
    caller put it.  The placement is deliberately NOT decided here -- where a
    stacked name sits relative to the numbers it labels is a reading decision,
    so the caller pads with "" (see _compare_head_cells).  A plain `str`
    header is one line, byte-for-byte what it always was.

    The last cell of every line is right-stripped, so a table copied into a
    mail carries no trailing whitespace.
    """
    heads = [[h] if isinstance(h, str) else list(h) for h in headers]
    depth = max([len(h) for h in heads] + [1])
    heads = [h + [""] * (depth - len(h)) for h in heads]
    w = [max([len(x) for x in h] + [len(r[i]) for r in rows])
         for i, h in enumerate(heads)]

    def line(cells):
        out = gap.join(
            (c.rjust(w[i]) if aligns[i] == ">" else c.ljust(w[i]))
            for i, c in enumerate(cells))
        return (lead + out).rstrip()

    return ([line([h[d] for h in heads]) for d in range(depth)]
            + [line(r) for r in rows])


def _format_results_table(rows: Sequence[RowSnapshot], units_mode: str,
                          freq: Optional[FreqSnap] = None) -> str:
    """
    rows: list of RowSnapshot. Returns a multi-line aligned table.
    units_mode in {'smart', 'aligned'}.

    Every data row starts with RESULTS_SWATCH and every other line starts with
    an equally wide run of spaces; App._append_swatched finds the rows by that
    prefix and colours them.  Nothing here knows the colours: this stays a
    pure text function and the palette lookup stays in the one place that owns
    a Text widget.

    `freq` supplies what the rows cannot know -- the frequency that was ASKED
    for -- and buys the table a "read at" line whenever that is not where the
    numbers came from.  It is None for every pure caller (and for
    tests/_render_capture.py), and a None, or a marker that landed on a data
    point, adds no line at all: the table below has to look exactly as it
    always did in the case that is almost always the case.
    """
    if not rows:
        return ""

    # The letters are the TABLE's, assigned in order of appearance across all
    # rows, and the legend line above the table is what defines them -- that is
    # what this column has always meant and two traces cannot be given one
    # letter for two files.
    #
    # KNOWN COLLISION, left for whoever owns the composed report: a TRACE's own
    # file tags are also F1/F2 (positional within that trace, see
    # trace_file_aliases), so with a single-file trace listed first the table
    # calls the composed trace's home file F2 while its port cells call it F1.
    # Both mappings are printed where they are used -- this legend line, and
    # the Files window's -- and inventing a third scheme here would make it
    # three.
    file_alias, file_labels_in_order = _file_alias_map(rows)
    multi_file = len(file_labels_in_order) > 1

    def _file_cell_(r) -> str:
        return _file_cell(r, file_alias)

    # Truncation widths
    LABEL_W = 18
    PORT_W = 24
    # 4 = len('File'), the header.  It only ever grows for a COMPOSED row, so a
    # table of single-file rows is byte-identical to what it always was --
    # every cell is 'F1'/'F2' and max(4, 2) is 4.
    FILE_W = (max([4] + [len(_file_cell_(r)) for r in rows])
              if multi_file else 4)
    NUM_W = 10  # per numeric cell (smart mode); aligned mode tighter

    def _trunc(s: str, w: int) -> str:
        if len(s) <= w:
            return s
        return s[: w - 1] + "…"

    lines = []
    if multi_file:
        lines.append(_SWATCH_PAD + " " + "  ".join(
            f"{file_alias[fl]}={fl}" for fl in file_labels_in_order
        ))
    else:
        lines.append(f"{_SWATCH_PAD} file: {file_labels_in_order[0]}")

    note = _table_freq_note(rows, freq)
    if note:
        lines.append(note)

    # Header
    if units_mode == "aligned":
        # Pick per-column prefix from the data
        Rs = [r.res.R_ohm for r in rows]
        Ls = [r.res.L_henry for r in rows]
        Cs = [r.res.C_farad for r in rows]
        Qs = [r.res.Q for r in rows]
        r_exp, r_pfx = _aligned_prefix_for(Rs)
        l_exp, l_pfx = _aligned_prefix_for(Ls)
        c_exp, c_pfx = _aligned_prefix_for(Cs)
        col_R = f"R[{r_pfx}Ω]"
        col_L = f"L[{l_pfx}H]"
        col_C = f"C[{c_pfx}F]"
        col_Q = "Q"
        NUM_W = 9
    else:
        col_R, col_L, col_C, col_Q = "R", "L", "C", "Q"
        NUM_W = 10

    # "ID   " is FIVE wide, matching "[{id:>2}] " on the data rows.  It was
    # four, so the header sat one column left of everything under it -- barely
    # visible with a ragged left edge, obvious now that a swatch squares it up.
    parts = [_SWATCH_PAD + " ", "ID   ", f"{'Label':<{LABEL_W}}  "]
    if multi_file:
        parts.append(f"{'File':<{FILE_W}}  ")
    parts.append(f"{'Ports':<{PORT_W}}  ")
    parts.append(f"{col_R:>{NUM_W}}  ")
    parts.append(f"{col_L:>{NUM_W}}  ")
    parts.append(f"{col_C:>{NUM_W}}  ")
    parts.append(f"{col_Q:>{NUM_W}}  ")
    parts.append("Sign")
    lines.append("".join(parts))

    for r in rows:
        res = r.res
        flag = _sign_flag(res)
        if units_mode == "aligned":
            r_str = _fmt_aligned(res.R_ohm, r_exp)
            l_str = _fmt_aligned(res.L_henry, l_exp)
            c_str = _fmt_aligned(res.C_farad, c_exp)
            q_str = "nan" if not math.isfinite(res.Q) else f"{res.Q:.4g}"
        else:
            r_str = format_si(res.R_ohm, "Ω")
            l_str = format_si(res.L_henry, "H")
            c_str = format_si(res.C_farad, "F")
            q_str = "nan" if not math.isfinite(res.Q) else f"{res.Q:.3g}"

        row_parts = [
            # Every row here IS on the plot: _render_results filters the hidden
            # traces out before calling, and names them on one line under the
            # table instead.  A row for a curve that is not drawn reads as a
            # duplicate of the one that is -- which on two similar traces (the
            # normal way a hidden one comes about, via Duplicate) is exactly
            # what it looks like.  That is also what makes the swatch honest:
            # every swatched row has a curve of that colour on the plot.
            RESULTS_SWATCH + " ",
            f"[{r.id:>2}] ",
            f"{_trunc(r.label, LABEL_W):<{LABEL_W}}  ",
        ]
        if multi_file:
            row_parts.append(f"{_file_cell_(r):<{FILE_W}}  ")
        row_parts.append(f"{_trunc(r.port_desc, PORT_W):<{PORT_W}}  ")
        row_parts.append(f"{r_str:>{NUM_W}}  ")
        row_parts.append(f"{l_str:>{NUM_W}}  ")
        row_parts.append(f"{c_str:>{NUM_W}}  ")
        row_parts.append(f"{q_str:>{NUM_W}}  ")
        row_parts.append(flag)
        lines.append("".join(row_parts))

    lines.append(
        f"{_SWATCH_PAD} legend: ind = Im(Z)>0 (inductive) | "
        "cap = Im(Z)<0 (capacitive; past SRF for an inductor) | "
        "R<0 = non-passive"
    )
    return "\n".join(lines)


# ============================================================================
# Mode 6 results block (Z matrix + self table + per-pair coupling)
# ============================================================================

# RECIPROCITY_WARN (the threshold above which Z_ab and Z_ba disagree enough
# that the S-parameters, not the maths, are the likely problem) now lives in
# pkg_rlc_core and is imported above, so the GUI and the CLI cannot drift into
# giving the same file opposite verdicts.  It is re-exported here because it
# used to be defined in this module.


def _trunc_str(s: str, w: int) -> str:
    s = s or ""
    return s if len(s) <= w else s[: w - 1] + "…"


def _value_formatter(values, unit: str, units_mode: str):
    """
    (header suffix, format function) for one column, honouring the units mode.

    'smart' delegates to format_si (per-value prefix, unit inline); 'aligned'
    picks one SI prefix for the whole column and puts it in the header, exactly
    like the main results table.
    """
    if units_mode == "aligned":
        exp, pfx = _aligned_prefix_for(list(values))
        return f"[{pfx}{unit}]", (lambda v: _fmt_aligned(v, exp))
    return "", (lambda v: format_si(v, unit))


def _fmt_plain(value: float, sig: int = 4) -> str:
    return "nan" if not math.isfinite(value) else f"{value:.{sig}g}"


# ---- ranking the coupling list -------------------------------------------
#
# Six measurement ports make 15 unordered pairs, and they used to be printed in
# nested-loop (a, b) index order -- which says nothing at all about which of the
# fifteen the user has to do something about.  They are now ranked, and the tail
# is folded into one line.
#
# THE KEY IS max(|M/L_a|, |M/L_b|), the Norton injection ratio, because that is
# the quantity a spur / pulling budget is written against.  |k| alone is the
# wrong key: |k| = 0.02 between two 2 nH coils and |k| = 0.02 between a 2 nH
# coil and a 500 pH one are different problems, and only the ratio separates
# them (M is the same, the injection into the small coil is 4x).
#
# MAGNITUDE APPEARS HERE AND NOWHERE ELSE.  M, C_c and k keep their physical
# sign in every printed cell, exactly as on the diagonal -- only the ordering
# and the floor test take an abs(), the same way sorting a column by |x| does
# not change what x is.
COUPLING_FLOOR_DB = -60.0


def _pair_strength(pair) -> float:
    """
    max(|M/L_a|, |M/L_b|) -- the rank key.  NaN when neither ratio is defined.

    Linear, not read off the *_dB fields: `_ratio_db` maps an exactly-zero
    ratio to NaN, and a pair with M = 0 is not undefined, it is the weakest
    pair there is and has to sort and truncate as such.
    """
    vals = [abs(v) for v in (pair.M_over_La, pair.M_over_Lb)
            if math.isfinite(v)]
    return max(vals) if vals else float("nan")


def _pair_strength_db(pair) -> float:
    """20*log10 of the rank key; NaN when it is zero or undefined.

    Same contract as pkg_rlc_core._ratio_db, which is what the per-port dB
    columns on the detail line already use.
    """
    s = _pair_strength(pair)
    if not math.isfinite(s) or s == 0.0:
        return float("nan")
    return 20.0 * math.log10(s)


def rank_coupling_pairs(pairs, floor_db: Optional[float] = COUPLING_FLOOR_DB):
    """
    (shown, hidden): the pairs strongest first, split at `floor_db`.

    Python's sort is stable, so pairs of equal strength keep the (a, b) index
    order they arrived in.  Two rules that are easy to get wrong:

      * a pair whose strength is UNDEFINED is never hidden.  The floor means
        "too weak to matter", and NaN is not a small number -- it is a missing
        measurement (a probe with no return path, a port past its SRF), which
        is the one thing the reader most needs to see.  It sorts last, after
        every finite pair, and prints.
      * the strongest pair is never hidden either, even when it is below the
        floor.  A coupling block whose entire content is "3 pairs were too
        weak to list" answers no question; "how much coupling is there" has
        an answer even when the answer is "none worth the name".
    """
    def key(p):
        s = _pair_strength(p)
        return -s if math.isfinite(s) else float("inf")

    ordered = sorted(pairs, key=key)
    if floor_db is None or not ordered:
        return ordered, []
    threshold = 10.0 ** (floor_db / 20.0)
    shown, hidden = [], []
    for p in ordered:
        s = _pair_strength(p)
        (hidden if (math.isfinite(s) and s < threshold) else shown).append(p)
    if not shown:
        shown.append(hidden.pop(0))     # hidden[0] is the strongest of them
    return shown, hidden


def _pair_flag(pair) -> str:
    """Compact sign flag for a pair, mirroring _sign_flag on the diagonal."""
    flags = []
    im = pair.Z_ab.imag
    if math.isfinite(im):
        if im > 0:
            flags.append("ind")
        elif im < 0:
            flags.append("cap")
    if math.isfinite(pair.k) and abs(pair.k) > 1.0:
        flags.append("|k|>1")
    return ",".join(flags)


def _format_z_matrix(names, Zk, indent: str = "      ") -> str:
    """Render the G x G Z matrix with aligned columns (Re + jIm, in ohms)."""
    G = len(names)
    disp = [_trunc_str(n, 12) for n in names]
    cells = [[f"{Zk[i, j].real:.4g}{Zk[i, j].imag:+.4g}j" for j in range(G)]
             for i in range(G)]
    name_w = max(len(n) for n in disp)
    col_w = max([len(c) for row in cells for c in row] + [name_w])
    out = [indent + " " * name_w + "  "
           + "  ".join(f"{n:>{col_w}}" for n in disp)]
    for i, n in enumerate(disp):
        out.append(indent + f"{n:<{name_w}}" + "  "
                   + "  ".join(f"{c:>{col_w}}" for c in cells[i]))
    return "\n".join(out)


#: The legend the coupling blocks used to carry, ONE COPY PER BLOCK.
#
# It was a single 272-character line, repeated verbatim under every block.
# Measured on a two-trace run: the results pane is 144 columns wide at the
# default 1500x900 window and 79 at the 1040x600 minsize, and the pane is
# `wrap=tk.NONE` -- so a 272-column line is 53% readable at the default size
# and 29% at the minimum, and the only way to the tail is a horizontal scroll
# that takes the Port column off the left edge at the same time.  Two copies
# of it were 544 of that run's 3538 characters; with the reference-node
# verdict, which was also repeated verbatim, 30% of the report was one of two
# sentences said twice.
#
# So it is emitted ONCE PER RUN, by _run_report_segments, which is the one
# builder of both the Log and the run pages.  What survives here is what a
# number on the screen cannot be read without; the full definitions live in
# Help -> Mode 6 and in the CSV header, where nothing clips.  The M/L wording
# is load-bearing and is kept in the shortened form -- "Norton injection
# ratio, NOT the exact current ratio |Z_ab/Z_aa|" is one of the six places
# that sentence has to agree (core docstring, CLI, here, Help, README,
# theory.md).
# Every line is inside the 144-column budget the default 1500x900 window
# measures, so the legend never needs the horizontal scrollbar the 272-column
# line always did.  The reciprocity DEFINITION is not here: it is in Help and
# in the CSV header, and the block prints a verdict rather than a metric.
COUPLING_LEGEND_LINES = (
    "  legend: ind = Im(Z)>0 (read M) · cap = Im(Z)<0 (read C_c) · "
    "R<0 = non-passive · |k|>1 = check the port setup",
    "          M/L = Norton injection ratio, NOT the exact current ratio "
    "|Z_ab/Z_aa| (equal only where wL >> R)",
    "          signs are physical (Cadence convention), never clipped · "
    "full definitions: Help → Mode 6",
)


def _format_coupling_block(block: CouplingSnapshot, units_mode: str) -> str:
    """
    Full mode-6 results block for one trace at the marker frequency:
    the Z matrix, the per-port self table, then one entry per pair.

    Takes a CouplingSnapshot, not a live TraceConfig: the heading is the
    identity of the trace AS MEASURED, and the trace it came from may since
    have been relabelled, re-ported or recomputed.

    TWO MEASUREMENT PORTS GET NO SEPARATE MATRIX BLOCK, and that is a
    redundancy claim, not a taste one.  At G = 2 the matrix is
    `[[Z_aa, Z_ab], [Z_ba, Z_aa]]` and every entry of it is printed again
    directly underneath: measured on the user's own run, the diagonal
    `9.924+112.6j` is the self table's `9.92 Ω` and `112.6/w = 3.229 nH`, and
    the off-diagonal `-0.04322-0.01799j` is the pair line's `M = -516 fH`.
    Four lines saying what the six under them already say.  So at G = 2 the
    diagonal becomes a column of the self table and the single off-diagonal
    goes on the pair's detail line, and EXACTLY ONE PLACE shows each raw
    complex number.  At G >= 3 the matrix earns its block back -- it is the
    compact way to show G(G-1)/2 off-diagonals -- and the Z column and the
    per-pair Z_ab are dropped instead, by the same rule.

    The `Z matrix @ <freq>` line prints in both cases and is unchanged: it is
    this block's frequency provenance (`tests/test_freq_label.py` pins that
    the banner and this line name one frequency), and its parenthetical is
    where the open-circuit convention is stated.
    """
    cres = block.cres
    names = list(cres.names)
    # cres.freq_hz is authoritative for WHERE this matrix was read -- it always
    # was, and it is the number this line has always printed.  The snapshot's
    # FreqSnap contributes only what cres cannot know: what was ASKED for, and
    # how coarse the grid is.  Overriding the snap's own actual with cres's is
    # what stops the two from ever drifting into printing different numbers,
    # which is the whole failure this change exists to end.
    freq = cres.freq_hz
    if isinstance(block.freq, FreqSnap):
        freq = replace(block.freq, actual_hz=float(cres.freq_hz))
    # G >= 3 keeps the matrix block; G == 2 folds it into the two tables
    # underneath, which already carry every one of its entries.  See the
    # docstring -- this is the one switch the whole shape of the block turns on.
    matrix_block = len(names) >= 3

    lines = [
        # 'file: x' for one file -- byte for byte what this line has always
        # said -- and 'files: EM=… + PKG=…' for a composition, because a block
        # headed with one file name when the numbers came from two is a false
        # claim in the line the reader uses to identify the measurement.
        f"  [{block.id}] {block.label}  |  {_snapshot_file_legend(block)}  |  "
        f"{block.port_desc}",
        f"  Z matrix @ {marker_freq_text(freq, '{:.6g}')}   (Ω, Re+jIm; "
        f"off-diagonal = mutual, every other port open)",
    ]
    if matrix_block:
        lines.append(_format_z_matrix(names, cres.Z_matrix))

    # --- self impedance table -------------------------------------------
    ports = list(cres.ports)
    r_sfx, fmt_r = _value_formatter([p.R_ohm for p in ports], "Ω", units_mode)
    l_sfx, fmt_l = _value_formatter([p.L_henry for p in ports], "H", units_mode)
    c_sfx, fmt_c = _value_formatter([p.C_farad for p in ports], "F", units_mode)
    NAME_W = max([len(_trunc_str(n, 14)) for n in names] + [4])
    NUM_W = 11
    # The heading exists to separate this table from the matrix ABOVE it.  With
    # no matrix there is nothing to separate it from, and the table's own
    # header row names every column -- so it would be a line spent restating
    # the line above it.
    if matrix_block:
        lines.append("  self impedance (diagonal):")
    # 'Sign' is padded ONLY when a Z column follows it.  Sign is the last cell
    # on the line otherwise, and padding a last cell is trailing whitespace on
    # every row of a table that is copied into mails.  SIGN_W is 7, the widest
    # _sign_flag can return ('ind,R<0').
    SIGN_W, Z_W = 7, 18
    if matrix_block:
        sign_cell, z_head, z_cells = (lambda s: s), "", (lambda p: "")
    else:
        sign_cell = (lambda s: f"{s:<{SIGN_W}}")
        z_head = f"  {'Z (Ω)':>{Z_W}}"
        # Rendered exactly as _format_z_matrix renders a cell, so the two
        # spellings of one number cannot drift.
        z_cells = (lambda p: "  "
                   + f"{p.Z.real:.4g}{p.Z.imag:+.4g}j".rjust(Z_W))
    lines.append(
        f"      {'Port':<{NAME_W}}  {'R' + r_sfx:>{NUM_W}}  "
        f"{'L' + l_sfx:>{NUM_W}}  {'C' + c_sfx:>{NUM_W}}  "
        f"{'Q':>{NUM_W}}  {sign_cell('Sign')}{z_head}")
    for p in ports:
        lines.append(
            f"      {_trunc_str(p.name, 14):<{NAME_W}}  "
            f"{fmt_r(p.R_ohm):>{NUM_W}}  {fmt_l(p.L_henry):>{NUM_W}}  "
            f"{fmt_c(p.C_farad):>{NUM_W}}  {_fmt_plain(p.Q):>{NUM_W}}  "
            f"{sign_cell(_sign_flag(p))}{z_cells(p)}")

    # --- per-pair coupling ----------------------------------------------
    pairs = list(cres.pairs)
    if not pairs:
        lines.append("  coupling: (only one measurement port -- "
                     "add a second measurement-port row to get M and k)")
    else:
        shown_pairs, weak_pairs = rank_coupling_pairs(pairs)
        # Column prefixes come from the pairs that are PRINTED: in aligned mode
        # a hidden pair setting the column's SI prefix would scale every cell
        # to a value that is not on screen.
        m_sfx, fmt_m = _value_formatter([p.M_henry for p in shown_pairs], "H",
                                        units_mode)
        cc_sfx, fmt_cc = _value_formatter([p.C_c_farad for p in shown_pairs],
                                          "F", units_mode)
        # The heading says two things: the open-circuit convention, which the
        # 'Z matrix @' line two lines up has already said, and the RANKING,
        # which means nothing when there is one pair to rank.  So a single-pair
        # block (which is every two-measurement-port trace, the common case)
        # does without it.
        if len(shown_pairs) + len(weak_pairs) > 1:
            lines.append("  coupling (mutual, all other measurement ports "
                         "open; strongest first by worst-case M/L):")
        for p in shown_pairs:
            flag = _pair_flag(p)
            # 'worst M/L' STAYS ON THE HEADLINE.  It was moved off while this
            # view was being slimmed and
            # tests/test_report_readability.py::test_the_db_is_on_the_first_
            # line_beside_M_and_k caught it: it is the RANK KEY, and with
            # fifteen pairs -- six measurement ports -- scanning for the loud
            # one off the headline means reading thirty lines instead of
            # fifteen.  The line is 93 columns against a 144-column pane, so
            # there was nothing to buy by moving it.
            lines.append(
                f"      {p.name_a} x {p.name_b}:  "
                f"M{m_sfx} = {fmt_m(p.M_henry)}   "
                f"k = {_fmt_plain(p.k)}   "
                f"worst M/L = {_fmt_plain(_pair_strength_db(p))} dB   "
                f"C_c{cc_sfx} = {fmt_cc(p.C_c_farad)}"
                + (f"   [{flag}]" if flag else ""))
            # Z_ab only where there is no matrix block to read it off.  Both
            # ratios stay in their SIGNED linear form beside the dB: dB is the
            # ranking key and takes an abs(), so a dB-only line would be the
            # one place in this report where a physical sign is hidden.
            zab = ("" if matrix_block
                   else f"   Z_ab = {p.Z_ab.real:.4g}{p.Z_ab.imag:+.4g}j")
            lines.append(
                f"          M/L({p.name_a}) = {_fmt_plain(p.M_over_La)} "
                f"({_fmt_plain(p.M_over_La_dB)} dB)   "
                f"M/L({p.name_b}) = {_fmt_plain(p.M_over_Lb)} "
                f"({_fmt_plain(p.M_over_Lb_dB)} dB){zab}")
            for note in p.notes:
                lines.append(f"          note: {note}")
        if weak_pairs:
            # The pointer has to be TRUE: _write_coupling_csv enumerates every
            # unordered pair from the Z matrix and knows nothing about this
            # floor, so what is folded away here really is in the export.
            noun = "pair" if len(weak_pairs) == 1 else "pairs"
            lines.append(f"      … +{len(weak_pairs)} {noun} below "
                         f"{COUPLING_FLOOR_DB:g} dB (see Export CSV)")

    # --- health check -----------------------------------------------------
    recip = cres.reciprocity_error
    checkable = any(math.isfinite(p.Z_ab.real) and math.isfinite(p.Z_ab.imag)
                    for p in pairs)
    # VERDICT AND NUMBER, and nothing else on the line.  What the metric IS
    # (max|Z_ab-Z_ba| / max|Z_ab| over the finite off-diagonal entries, alarm
    # above RECIPROCITY_WARN) is a definition, not a reading: it is the same
    # every run, it is in COUPLING_LEGEND_LINES once per run and in Help, and
    # putting it here cost 100 of this line's 140 columns for a number the
    # reader is scanning for a tick or a cross.  The one case that keeps its
    # sentence is the alarm, because there the sentence IS the reading.
    if not checkable:
        lines.append("      · reciprocity: nothing to check — every mutual "
                     "term is undefined")
    elif recip <= RECIPROCITY_WARN:
        lines.append(f"      ✓ reciprocal ({recip:.3g})")
    else:
        lines.append(f"      ⚠ RECIPROCITY {recip:.3g} — Z_ab and Z_ba "
                     "disagree; the input S-parameters are suspect "
                     "(non-reciprocal or under-converged EM solve)")
    # NO LEGEND HERE.  See COUPLING_LEGEND_LINES: it is emitted once per run by
    # _run_report_segments, not once per block.
    return "\n".join(lines)


# ============================================================================
# The SUMMARY view -- one run, two tables
# ============================================================================
#
# The detail view says everything about one trace before it says anything about
# the next, so comparing two traces means paging between blocks (17 lines apart
# on the reported run).  This says one thing about every trace at a time, which
# turns that comparison into reading down a column.
#
# TWO TABLES AND NOT ONE.  A self measurement has R/L/C/Q and a coupling has
# M/k/M-L/C_c; there is no column set that is honest about both, and a merged
# table would either leave half its cells empty on every row or put two
# quantities under one heading.  Splitting them is also what lets each table
# keep the units mode's per-column SI prefix, which is the whole point of
# 'aligned'.

def _summary_self_rows(rows, blocks) -> list:
    """(record, port name, RLC-like) for every self measurement in the run.

    A RowSnapshot contributes one entry and a CouplingSnapshot one per
    measurement port, so a mode-1 trace and one port of a mode-6 trace sit on
    the same footing -- which is what the table is for.
    """
    out = []
    for r in rows:
        # A scalar row's "port" is its port descriptor: that IS the identity of
        # the thing measured, and it is what the detail table has always shown.
        out.append((r, "", r.res))
    for b in blocks:
        for p in b.cres.ports:
            out.append((b, p.name, p))
    return out


def _format_summary_self(rows, blocks, units_mode: str) -> tuple:
    """(text, colour indices) for the self-impedance table, or ('', ())."""
    entries = _summary_self_rows(rows, blocks)
    if not entries:
        return "", ()
    alias, order = _file_alias_map([e[0] for e in entries])
    multi = len(order) > 1
    have_port = any(name for _r, name, _v in entries)

    vals = [v for _r, _n, v in entries]
    r_sfx, fmt_r = _value_formatter([v.R_ohm for v in vals], "Ω", units_mode)
    l_sfx, fmt_l = _value_formatter([v.L_henry for v in vals], "H", units_mode)
    c_sfx, fmt_c = _value_formatter([v.C_farad for v in vals], "F", units_mode)

    # The heading sits over the id DIGITS, not over the bracket: the
    # swatch and the "[N]" are one cell so a coloured row reads as one
    # thing, and _tag_swatch_rows finds it by its leading glyph.
    heads = [_SWATCH_PAD + "  ID ", "Label"]
    aligns = ["<", "<"]
    if have_port:
        heads.append("Port")
        aligns.append("<")
    heads += ["R" + r_sfx, "L" + l_sfx, "C" + c_sfx, "Q", "Sign"]
    aligns += [">", ">", ">", ">", "<"]
    if multi:
        heads.append("File")
        aligns.append("<")

    body, colors = [], []
    for rec, name, v in entries:
        # SUMMARY_LABEL_MAX, not 18.  A Label column sized to 18 made
        # '..._RDL_shield_open' and '..._RDL_shield_short' one string,
        # 'VCO_EM_0812_RDL_s…', on the two rows the reader is comparing --
        # and bought nothing, because _render_columns already sizes this
        # column to its widest cell: measured on the reported run, the full
        # names cost 10 columns of 144 (73 -> 83).
        cells = [f"{RESULTS_SWATCH} [{rec.id:>2}]",
                 _trunc_str(rec.label, SUMMARY_LABEL_MAX)]
        if have_port:
            # A scalar row has no measurement-port NAME; its port descriptor is
            # what identifies it, and is what the detail table prints.
            cells.append(_trunc_str(name or rec.port_desc, 22))
        cells += [fmt_r(v.R_ohm), fmt_l(v.L_henry), fmt_c(v.C_farad),
                  _fmt_plain(v.Q), _sign_flag(v)]
        if multi:
            cells.append(_file_cell(rec, alias))
        body.append(cells)
        colors.append(rec.color_idx)

    out = _render_columns(heads, aligns, body, lead="  ")
    if multi:
        out.insert(0, _SWATCH_PAD + " " + "  ".join(f"{alias[fl]}={fl}"
                                                    for fl in order))
    return "\n".join(out), tuple(colors)


def _format_summary_coupling(blocks, units_mode: str) -> tuple:
    """(text, colour indices) for the coupling table, or ('', ()).

    EVERY pair of every block, ranked within its block exactly as the detail
    view ranks them -- `rank_coupling_pairs` is called here too rather than
    re-sorted, so a pair the detail view folds under the floor is folded here
    and the two views cannot disagree about which coupling matters.
    """
    entries = []
    folded = 0
    for b in blocks:
        shown, weak = rank_coupling_pairs(list(b.cres.pairs))
        folded += len(weak)
        for p in shown:
            entries.append((b, p))
    if not entries:
        return "", ()

    pairs = [p for _b, p in entries]
    m_sfx, fmt_m = _value_formatter([p.M_henry for p in pairs], "H", units_mode)
    cc_sfx, fmt_cc = _value_formatter([p.C_c_farad for p in pairs], "F",
                                      units_mode)
    heads = [_SWATCH_PAD + "  ID ", "Label", "Pair", "M" + m_sfx, "k",
             "worst M/L", "C_c" + cc_sfx, "Sign"]
    aligns = ["<", "<", "<", ">", ">", ">", ">", "<"]

    body, colors = [], []
    for b, p in entries:
        body.append([
            f"{RESULTS_SWATCH} [{b.id:>2}]",
            _trunc_str(b.label, SUMMARY_LABEL_MAX),   # see _format_summary_self
            f"{p.name_a} x {p.name_b}",
            fmt_m(p.M_henry), _fmt_plain(p.k),
            f"{_fmt_plain(_pair_strength_db(p))} dB",
            fmt_cc(p.C_c_farad), _pair_flag(p),
        ])
        colors.append(b.color_idx)
    out = _render_columns(heads, aligns, body, lead="  ")
    if folded:
        noun = "pair" if folded == 1 else "pairs"
        out.append(f"  … +{folded} {noun} below {COUPLING_FLOOR_DB:g} dB "
                   f"(see Export CSV)")
    return "\n".join(out), tuple(colors)


# ============================================================================
# The COMPARE view -- traces become columns
# ============================================================================
#
# The question a run with two revisions of one structure in it exists to
# answer, and the one the other two views answer worst: the numbers being
# compared are 17 lines apart in `detail` and in different rows of two
# different tables in `summary`.  Here they are side by side with the change
# between them computed.
#
# It is a VIEW and not a mode: it reads the same RunSnapshot, adds nothing to
# it, and refuses (in words, with a fallback) rather than inventing a
# comparison it cannot make.
#
# THE TRACE NAME IS NEVER TRUNCATED HERE, AND THAT IS A MEASUREMENT RATHER THAN
# A PREFERENCE.  It used to be head-cut at 14 characters, which on a set of
# revision names COLLIDES -- measured on four realistic ones,
# 'VCO_EM_0731_ideal_ground_ref' / 'VCO_EM_0812_ideal_ground_ref' /
# 'VCO_EM_0812_RDL_shield_open' / 'VCO_EM_0812_RDL_shield_short', the last two
# both render as 'VCO_EM_0812_RDL_shie…': two columns of a table whose whole
# purpose is telling those two apart, headed byte-identically.  That is the
# `freeze_label` defect arriving in the Results pane.
#
# NO BETTER TRUNCATION RULE EXISTS, which is why none was chosen.  At the 15
# characters each column gets with five traces, measured on that same set:
# head-cut collides, TAIL-cut collides ('…eal_ground_ref' twice -- and tail is
# what pkg_rlc_plot._fit_names keeps, for its own good reasons), middle-elision
# collides ('VCO_EM_…und_ref' twice), and stripping the common prefix first
# rescues only the middle form.  The reason is structural: [1] and [2] differ
# only at the HEAD (0731 vs 0812) while [3] and [4] differ only at the TAIL
# (open vs short), so one rule cannot keep both ends.
#
# So the name is shown in FULL, in one of two shapes, and the shape is chosen
# by the height it would cost:
#
#   STACKED (the default) -- the name is wrapped down the column heading at
#       '_' / '.' / '-' boundaries.  This is also NARROWER than truncating,
#       because the name stops setting the column width: the values need ~10
#       characters and a 22-character name was forcing 22.  Measured on the
#       reported run, 3 traces: 87 columns head-cut against 60 stacked.
#   LEGEND -- one line per trace above the table carrying the full name and its
#       curve colour, with the heading reduced to '# [N]'.  57 columns on the
#       same run.  It costs a lookup, and is used only when stacking would go
#       past COMPARE_STACK_LINES_MAX.
#
# Both fit the 144-column pane where the shipped 14-character head-cut needed
# 131 at five traces and 87 at three.

#: Where a stacked trace name may be broken.  The separator stays with the
#: segment it ENDS, so a reader can see the break is a wrap and not a character
#: the name does not contain.
_NAME_BREAK_RE = re.compile(r"[^_.\-]+[_.\-]*")


def _wrap_name(s: str, w: int) -> list:
    """
    A trace label as a list of lines at most `w` wide, broken at '_', '.', '-'.

    A token longer than `w` is HARD-wrapped rather than truncated: a name with
    no separators in it still has to be shown in full, because the alternative
    is the collision this whole shape exists to remove.  Never returns [] -- an
    empty label is one empty line, so the column keeps its place.
    """
    s = s or ""
    if w <= 0:
        return [s]
    out: list = []
    cur = ""
    for tok in (_NAME_BREAK_RE.findall(s) or [s]):
        if cur and len(cur) + len(tok) > w:
            out.append(cur)
            cur = tok
        else:
            cur += tok
        while len(cur) > w:
            out.append(cur[:w])
            cur = cur[w:]
    if cur:
        out.append(cur)
    return out or [""]


def _compare_head_cells(records, base_w: Sequence[int], fixed_w: int,
                        gap: int = 2) -> tuple:
    """
    (header cells, legend lines, colour repeats) for the compare table.

    `base_w` is what each trace column already costs for its VALUES (and its
    id cell) and `fixed_w` everything else on the line, so what is left of
    RESULTS_PANE_COLS is what the name may spend.

    THE PRIORITY IS THE WHOLE NAME ON AS FEW LINES AS THE BUDGET ALLOWS, not
    the narrowest possible table.  The complaint this answers is a name being
    ELIDED, and a name on one line is easier to read than the same name in
    three; width matters only in that it is what forced the eliding.  So the
    segment is the widest the budget affords, capped at the name itself -- with
    few traces every name lands on one line and the table is as wide as it
    honestly needs to be (measured, the reported run: 85 columns of 144, where
    the 14-character head-cut was 87 AND wrong).  As the trace count rises the
    share shrinks and the names wrap instead of the table overflowing: five
    28-character revision names come out three lines deep at 91 columns.

    The id cell is pinned to header line 0 for every column and the name is
    BOTTOM-aligned under it.  Both halves are load-bearing.  Line 0, because
    `_tag_swatch_rows` walks lines and consumes one colour per swatch it finds,
    so swatches spread over several header lines would be coloured in the wrong
    order.  Bottom-aligned, because the last line of the name then sits directly
    above the numbers it labels whatever depth its neighbours needed.
    """
    n = len(records)
    names = [r.label or "" for r in records]
    spare = max(0, RESULTS_PANE_COLS - fixed_w - sum(b + gap for b in base_w))
    share = spare // n
    # Floored at COMPARE_SEG_MIN so a name that must wrap always has something
    # to wrap INTO; the floor cannot widen a column, because _render_columns
    # takes the max against the value cells anyway.
    seg = [max(COMPARE_SEG_MIN, min(len(nm), max(b, share)))
           for nm, b in zip(names, base_w)]

    wrapped = [_wrap_name(nm, s) for nm, s in zip(names, seg)]
    depth = max(len(w) for w in wrapped)
    # A name with no separator inside the column has to be cut MID-TOKEN, and a
    # hard-wrapped name reads as corruption rather than as a wrap -- the legend
    # is the honest shape for it, exactly as it is for one too deep to stack.
    hard = any(len(tok) > s
               for nm, s in zip(names, seg)
               for tok in (_NAME_BREAK_RE.findall(nm) or [nm]))
    if depth <= COMPARE_STACK_LINES_MAX and not hard:
        # Stacked: id on line 0, name bottom-aligned in the lines under it.
        cells = [[f"{RESULTS_SWATCH} [{r.id}]"]
                 + [""] * (depth - len(w)) + w
                 for r, w in zip(records, wrapped)]
        return cells, [], 1
    # The legend carries the curve COLOUR as well as the name, so the mapping a
    # reader needs (colour on the plot -> name -> column) is on one line.  That
    # is also why the caller repeats the colour tuple: the legend lines and the
    # header line each hold one swatch per record, in the same order.
    legend = [f"  {RESULTS_SWATCH} [{r.id}] {r.label or ''}" for r in records]
    return [[f"{RESULTS_SWATCH} [{r.id}]"] for r in records], legend, 2


#: Rows of the compare table, as (quantity label, attribute, kind).  `kind`
#: picks how the delta is expressed: a ratio for a physical value, a plain
#: difference for something already in dB.
_CMP_SELF = (("R", "R_ohm", "Ω"), ("L", "L_henry", "H"),
             ("C", "C_farad", "F"), ("Q", "Q", ""))
_CMP_PAIR = (("M", "M_henry", "H"), ("k", "k", ""),
             ("C_c", "C_c_farad", "F"))


def _compare_groups(rows, blocks) -> list:
    """
    [(group label, [(quantity, unit, {record id: value})])], in first-seen
    order: every measurement port of the run, then every pair.

    A record that does not have a group leaves its cell EMPTY rather than
    zero -- 'this trace has no port called RX' and 'RX measured 0' are
    different statements and the table has to be able to make both.
    """
    order: list = []
    seen: dict = {}

    def group(label):
        if label not in seen:
            seen[label] = {}
            order.append(label)
        return seen[label]

    for r in rows:
        g = group(_trunc_str(r.port_desc, 22))
        for name, attr, unit in _CMP_SELF:
            g.setdefault((name, unit), {})[r.id] = getattr(r.res, attr)
    for b in blocks:
        for p in b.cres.ports:
            g = group(p.name)
            for name, attr, unit in _CMP_SELF:
                g.setdefault((name, unit), {})[b.id] = getattr(p, attr)
    for b in blocks:
        for p in b.cres.pairs:
            g = group(f"{p.name_a} x {p.name_b}")
            for name, attr, unit in _CMP_PAIR:
                g.setdefault((name, unit), {})[b.id] = getattr(p, attr)
            g.setdefault(("worst M/L", "dB"), {})[b.id] = _pair_strength_db(p)

    out = []
    for label in order:
        quantities = [(nm, unit, vals)
                      for (nm, unit), vals in seen[label].items()]
        out.append((label, quantities))
    return out


def _delta_cell(a: float, b: float, unit: str) -> str:
    """
    How much b differs from a, in the form a reader can act on.

    A dB quantity gets a dB DIFFERENCE -- expressing a change of decibels as a
    percentage of decibels is meaningless, and dB is already a ratio.
    Everything else gets a relative change, as a PERCENTAGE while that is
    readable and as a FACTOR once it is not: measured on the reported run,
    M goes -516 fH -> -7.19 pH, which is -1293% and 13.9x, and only the second
    of those is a sentence anybody says out loud.  The crossover is a factor of
    ten either way.

    A sign change comes out of the same expression correctly (+1 -> -1 is
    -200%), so nothing special is done for it: the number says so.
    """
    if not (math.isfinite(a) and math.isfinite(b)):
        return "—"
    if unit == "dB":
        return f"{b - a:+.4g} dB"
    if a == 0.0:
        return "—" if b != 0.0 else "0"
    rel = (b - a) / abs(a)
    if abs(rel) <= 9.0:
        return f"{rel * 100.0:+.3g} %"
    return f"{b / a:+.4g} ×"


def _format_compare(rows, blocks, units_mode: str) -> tuple:
    """
    (text, colour indices, refusal).  The refusal is empty when the table is
    the whole answer and carries the reason when it is not -- the caller falls
    back to the summary and prints it, which is the "degrade, never refuse"
    rule this repo applies to the attribution split.

    The delta column appears ONLY at exactly two records.  With three it would
    have to pick a reference silently, and a column headed 'Δ' that is secretly
    'against whichever trace sorted first' is the kind of quiet decision this
    tool refuses everywhere else; with three columns of numbers side by side
    the reader can see the change without one.
    """
    records = list(rows) + list(blocks)
    if len(records) < 2:
        return "", (), ("compare needs at least two traces on the plot — "
                        "showing the summary instead")
    groups = _compare_groups(rows, blocks)
    if not groups:
        return "", (), "nothing to compare — showing the summary instead"

    ids = [r.id for r in records]
    aligns = ["<", "<"] + [">"] * len(records)
    two = len(records) == 2
    if two:
        aligns.append(">")

    body: list = []
    for label, quantities in groups:
        first = True
        for name, unit, vals in quantities:
            # A DIMENSIONLESS quantity must not be given an SI prefix.  k and Q
            # have no unit, and format_si on a bare number renders k = -2.412e-4
            # as '-241 u' -- a micro-nothing, which is not a quantity.  dB is
            # excluded for the same reason from the other end: it is already a
            # ratio, so a prefix on it would be a milli-decibel.  Everything
            # else takes one SI prefix per ROW in aligned mode, because here the
            # row is the quantity and so it is the row, not the column, that
            # shares a unit.
            if unit in ("", "dB"):
                sfx = ""
                fmt = ((lambda v: f"{_fmt_plain(v)} dB") if unit == "dB"
                       else _fmt_plain)
            else:
                sfx, fmt = _value_formatter(list(vals.values()), unit,
                                            units_mode)
            cells = [label if first else "", name + sfx]
            for r in records:
                v = vals.get(r.id)
                cells.append("" if v is None else fmt(v))
            if two:
                a, b = vals.get(ids[0]), vals.get(ids[1])
                cells.append("" if a is None or b is None
                             else _delta_cell(a, b, unit))
            body.append(cells)
            first = False

    # The header shape is decided from what the BODY costs, so the name is only
    # ever charged for the width it does not already have for free.  base_w is
    # per trace column: its values, or its id cell, whichever is wider.
    n = len(records)
    base_w = [max([len(f"{RESULTS_SWATCH} [{r.id}]")]
                  + [len(row[2 + i]) for row in body])
              for i, r in enumerate(records)]
    fixed_w = (2                                            # lead
               + max(len(row[0]) for row in body) + 2       # group column
               + max(len(row[1]) for row in body) + 2       # quantity column
               + ((max(len("Δ"), max(len(row[-1]) for row in body)) + 2)
                  if two else 0))
    head_cells, legend, repeats = _compare_head_cells(records, base_w, fixed_w)

    # The swatch is in the HEADER here, because a column is a trace and the
    # heading is the only cell that names it.  _tag_swatch_rows consumes every
    # occurrence on a line, left to right, so the header's swatches are
    # coloured in column order -- and in the legend shape the same tuple is
    # consumed once by the legend lines and once by the header, in that order,
    # which is why `repeats` is 2 there.
    heads: list = ["", ""] + list(head_cells)
    if two:
        heads.append("Δ")
    lines = legend + _render_columns(heads, aligns, body, lead="  ")
    return ("\n".join(lines),
            tuple(r.color_idx for r in records) * repeats, "")
