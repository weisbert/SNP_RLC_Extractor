"""
pkg_rlc/present/tracemodel_report.py  --  the trace pi model as TEXT.

Every section here RETURNS its lines; nothing writes to a stream, opens a file
or knows a terminal exists.  That is `attrib_report`'s rule and it is what lets
`tests/fixtures/cli_reference/` pin this whole surface with no subprocess.

It imports `pkg_rlc.physics.tracemodel`, `pkg_rlc.physics.core` and
`pkg_rlc.present.report` -- L0 and its own layer, no tkinter, no matplotlib.

WHY THE SCHEMATIC IS TEXT AND NOT A CANVAS
------------------------------------------
`docs/conventions/rejected_ui.md` turned down a matplotlib schematic in a tab
beside the plot, on three measurements (26 px of permanent plot height, the
`<<NotebookTabChanged>>` / `canvas.focus_set()` collision that breaks the
M / V / Delete keys, and a ~10x redraw cost on a path that fires per
keystroke).  It ends with the one line that governs this file:

    "If a schematic is ever built, it is a `tk.Canvas` in a Toplevel, like the
     Ports & Roles window."

So the door is open, and this is deliberately the half that goes through it
first: TEXT costs zero pixels, serves the CLI and the results pane from one
code path, and needs no Tk to test -- 87 % of the suite is Tk GUI tests a
change here cannot affect.  A `tk.Canvas` Toplevel is the second step and
reads the SAME `PiModel`, so the two can never disagree about the numbers.

WHAT THE DRAWING IS ALLOWED TO SAY
----------------------------------
The single-ended and differential drawings share one skeleton and differ only
in their labels, because they ARE the same topology -- a pi over two nodes.
What changes is what the nodes and the bottom rail MEAN: single-ended shunts
go to the declared reference, differential shunts go ACROSS THE PAIR and there
is no reference node in the picture at all.  Saying that on the drawing is the
whole point; a differential pi whose bottom rail is drawn as ground is a lie
that reads as a diagram.

Every branch prints its own `|Q|` and the verdict that follows from it.  That
is not decoration: a shunt with |Q| = 267 and a series branch with |Q| = 0.003
are the same three printed numbers and two completely different circuits, and
reading the second one's C as a capacitance is the single most common way to
misread this tool's output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from pkg_rlc.physics.core import format_si
from pkg_rlc.physics.tracemodel import (
    BW_DROP_DB, Bandwidth, BranchCorner, LUMPED_DRIFT_WARN, Branch, PiModel,
    lumped_drift,
)
from pkg_rlc.present.report import marker_freq_text

__all__ = [
    "pi_report_lines", "pi_schematic_lines", "branch_value_lines",
    "bandwidth_lines",
    "CanvasItem", "pi_canvas_items", "CANVAS_W", "CANVAS_H",
    "response_canvas_items", "RESPONSE_DB_FLOOR",
    "MODE_CONVERSION_WARN",
    "SUMMARY_COLUMNS", "SUMMARY_KEYS", "summary_table_lines", "summary_order",
    "summary_sort_value",
]

# Above this, a differential pair is imbalanced enough that the differential
# two-port is no longer the whole story and the report says so.  0.05 is 5 %
# of the differential term leaking to common mode -- measured on a synthetic
# pair, a 30 % conductor imbalance reads 0.261 and a symmetric one 9e-14, so
# the threshold sits an order of magnitude clear of numerical zero and well
# below a genuinely broken pair.
MODE_CONVERSION_WARN = 0.05

#: A response that rises this far above its reference is PEAKING, not flat.
#: 1 dB is comfortably above the +0.03..+0.14 dB a clean lossless fixture
#: shows from round-off, and well below the +13.6 dB a real series-L into a
#: 200 fF load produced.
PEAK_WARN_DB = 1.0

# 'Ω' the way every other printed surface spells it -- `report.py`'s
# `_TABLE_BASE_UNITS`, its three `_value_formatter` call sites and the CLI's
# own coupling block all use U+03A9, and a second spelling here would be a
# second vocabulary for one quantity.
OHM = "Ω"

_MIN_SPAN = 30          # narrowest series run, so short values still look drawn
_VERDICT = {
    "L": "inductive -- read L",
    "C": "capacitive -- read C",
    "R": "|Q| < 0.01: a resistor, the reactance is a residue -- do not read L or C",
}


def branch_value_lines(model: PiModel, branch: Branch) -> list[str]:
    """
    One branch as two or three short lines: R, then whichever of L / C it
    READS AS, then the odd-mode restatement on a differential shunt.

    A branch that reads as a bare resistor still prints its L and C -- hiding
    them would be a different lie from the one this tool is trying not to tell
    -- but prints them after the verdict that says not to use them.
    """
    kind = branch.reads_as
    out = [f"R = {format_si(branch.R_ohm, OHM)}"]
    if kind == "L" or (kind == "R" and branch.Z.imag > 0.0):
        out.append(f"L = {format_si(branch.L_henry, 'H')}")
    else:
        out.append(f"C = {format_si(branch.C_farad, 'F')}")
    if model.differential and branch is not model.series:
        odd = model.odd_mode_farads(branch)
        if math.isfinite(odd):
            out.append(f"(odd {format_si(odd, 'F')})")
    return out


def pi_schematic_lines(model: PiModel) -> list[str]:
    """
    The pi, drawn, with every element value on it.

    The layout is computed from the value strings rather than hard-coded, so a
    344 ohm series branch and a 1.23 Mohm one both come out aligned and neither
    can push the drawing into wrapping.  `_MIN_SPAN` only sets the floor.
    """
    series_txt = "   ".join(branch_value_lines(model, model.series))
    in_txt = branch_value_lines(model, model.shunt_in)
    out_txt = branch_value_lines(model, model.shunt_out)

    in_w = max(len(s) for s in in_txt)
    out_w = max(len(s) for s in out_txt)
    # The series run has to be wide enough that the two shunt labels, which
    # hang off the inside of each leg, cannot collide in the middle.
    span = max(_MIN_SPAN, len(series_txt) + 4, in_w + out_w + 5)

    left_name = model.in_name[:12]
    right_name = model.out_name[:12]
    head = f"   {left_name:>12} "          # node name, then the bullet
    lead = len(head)                       # COLUMN OF BOTH LEGS and of the
    bar = " " * lead                       # bullets -- they must agree or the
                                           # drawing reads as two circuits

    lines: list[str] = []
    lines.append(" " * (lead + max(0, (span - len(series_txt)) // 2))
                 + series_txt)
    lines.append(f"{head}*" + "-" * span + f"*  {right_name}")
    lines.append(bar + "|" + " " * span + "|")
    for i in range(max(len(in_txt), len(out_txt))):
        li = in_txt[i] if i < len(in_txt) else ""
        ro = out_txt[i] if i < len(out_txt) else ""
        gap = span - len(li) - len(ro)
        lines.append(bar + "|" + li + " " * gap + ro + "|")
    lines.append(bar + "|" + " " * span + "|")

    mid = span // 2
    if model.differential:
        lines.append(bar + "+" + "-" * span + "+")
        lines.append(bar + " " * max(0, mid - 12)
                     + "across the pair (no reference node)")
    else:
        lines.append(bar[:-2] + "--+" + "-" * mid + "*"
                     + "-" * (span - mid - 1) + "+--")
        lines.append(bar + " " * max(0, mid - 4) + "reference")
    return lines


def _drift_lines(model: PiModel, reference: PiModel) -> list[str]:
    """
    The same pi read at the bottom of the sweep, as a movement per branch.

    This is the cheap form of "is this one lumped element over my band": two
    points out of a sweep that is already solved, no extra solve and no curve.
    It is here rather than left to the reader because the alternative is what
    this tool's own author did by hand -- extract at 0.1 GHz, extract at
    5 GHz, and only then discover the two agreed.
    """
    if reference.freq_hz == model.freq_hz:
        return []
    drift = lumped_drift(model, reference)
    out = [
        f"  lumped check   the same pi at the bottom of the sweep "
        f"({format_si(reference.freq_hz, 'Hz')}):",
    ]
    worst = 0.0
    for b, rb in zip(model.branches, reference.branches):
        d = drift[b.name]
        # A drift of -1e-16 is a drift of zero; printing it as "-0.0 %" reads
        # as a measurement rather than as the float noise it is.
        if math.isfinite(d) and abs(d) < 1e-12:
            d = 0.0
        kind = rb.reads_as
        val = {"R": (b.R_ohm, OHM), "L": (b.L_henry, "H"),
               "C": (b.C_farad, "F")}[kind]
        ref_val = {"R": rb.R_ohm, "L": rb.L_henry, "C": rb.C_farad}[kind]
        pct = "n/a" if not math.isfinite(d) else f"{d * 100:+.1f} %"
        flag = ("" if not math.isfinite(d) or abs(d) <= LUMPED_DRIFT_WARN
                else "   <-- NOT one lumped element across this span")
        out.append(
            f"                 {b.name:<9} {kind} "
            f"{format_si(ref_val, val[1]):>11} -> "
            f"{format_si(val[0], val[1]):>11}   {pct:>8}{flag}")
        if math.isfinite(d):
            worst = max(worst, abs(d))
    if worst <= LUMPED_DRIFT_WARN:
        out.append(f"                 all branches within "
                   f"{LUMPED_DRIFT_WARN * 100:.0f} % -- the values above are "
                   f"reusable across this band.")
    else:
        out.append("                 a branch moved more than "
                   f"{LUMPED_DRIFT_WARN * 100:.0f} %: this structure is not "
                   "one lumped pi over the band, so the")
        out.append("                 values above are good AT THIS FREQUENCY "
                   "and must not be reused elsewhere in it.")
    return out


def pi_report_lines(model: PiModel, freq_snap=None,
                    reference: PiModel | None = None,
                    mode_conversion: float | None = None,
                    port_note: str = "", drawing: bool = True) -> list[str]:
    """
    The whole trace-model report for one two-port at one frequency.

    `freq_snap` is a `FreqSnap` (or a bare Hz value) and goes through
    `marker_freq_text`, the ONE renderer for a printed marker frequency -- so a
    request below the bottom of the file says "outside the swept band" here in
    exactly the words every other surface uses.  `reference` is the same pi at
    the bottom of the sweep and drives the lumped check; pass None to skip it.
    `mode_conversion` is `mode_conversion_ratio`'s number, differential only.
    """
    kind = "differential" if model.differential else "single-ended"
    lines = [
        "",
        f"Trace model ({kind}):  {model.in_name} -> {model.out_name}",
    ]
    if port_note:
        lines.append(f"  {port_note}")
    freq_txt = (marker_freq_text(freq_snap) if freq_snap is not None
                else f"{model.freq_hz / 1e9:.4g} GHz")
    lines.append(f"  @ {freq_txt}")
    lines.append("")
    # `drawing=False` is what the Toplevel passes: it draws the SAME PiModel
    # on a tk.Canvas, and printing the picture twice in two notations under
    # one another is how the two start disagreeing about a value.
    if drawing:
        lines.extend(pi_schematic_lines(model))
        lines.append("")

    for b in model.branches:
        lines.append(f"  {b.name:<9} |Q| = {abs(b.Q):<10.4g} "
                     f"{_VERDICT[b.reads_as]}")
    lines.append("")

    if reference is not None:
        drift = _drift_lines(model, reference)
        if drift:
            lines.extend(drift)
            lines.append("")

    if model.differential:
        if mode_conversion is None or not math.isfinite(mode_conversion):
            lines.append(
                "  mode conversion  not checked -- the differential pi above "
                "assumes common mode OPEN at both ends, which is exact only "
                "for a")
            lines.append(
                "                   symmetric pair. Declare the four ports "
                "single-ended to have the imbalance measured.")
        else:
            lines.append(f"  mode conversion  max|Ydc| / max|Ydd| = "
                         f"{mode_conversion:.3e}")
            if mode_conversion > MODE_CONVERSION_WARN:
                lines.append(
                    "                   above "
                    f"{MODE_CONVERSION_WARN:.0%}: this pair is IMBALANCED. A "
                    "differential two-port cannot represent where the")
                lines.append(
                    "                   converted energy went, so the pi "
                    "above is incomplete -- treat it as the differential "
                    "part only.")
            else:
                lines.append(
                    "                   the pair is balanced at this "
                    "frequency, so the differential pi above is the whole "
                    "story.")
        lines.append("")

    for w in model.warnings:
        lines.append(f"  WARN: {w}")
    if model.warnings:
        lines.append("")
    return lines


# ============================================================================
# The drawn schematic, as GEOMETRY -- no Tk
# ============================================================================
#
# The schematic is a `tk.Canvas` (never matplotlib -- `rejected_ui.md`), and
# since 2026-10-02 it lives in the Trace model WORKSPACE rather than a
# Toplevel.  This is the half of it which can be tested without a display.
#
# `pi_canvas_items` returns primitives -- lines, rectangles and texts, in
# canvas coordinates -- and `pkg_rlc/panels/ws_tracemodel.py` does nothing
# but hand each one to `Canvas.create_*`.  Keeping the geometry HERE, at L3,
# is what lets the drawing be asserted with no Tk root at all (the layout
# tests run in `FAST_MODULES`, where a widget test could not), and it is the
# same split `attrib_gui` uses for its own pure formatters.
#
# The item list is also the reason the drawn window and the text block cannot
# disagree about a number: both read the SAME `PiModel` through the SAME
# `branch_value_lines`.


CANVAS_W = 620          # the drawing's natural size.  Everything below is
CANVAS_H = 330          # derived from these two, so a caller may scale.


@dataclass(frozen=True)
class CanvasItem:
    """
    One primitive for `tk.Canvas`, with the ROLE that decides its styling.

    `kind` is 'line' | 'rect' | 'text' | 'oval'; `coords` is what the matching
    `create_*` takes.  `role` is what the renderer keys colour and font off --
    'wire', 'box', 'value', 'label', 'node', 'gnd', 'note' -- so the palette
    lives with the widgets and the geometry lives here.
    """
    kind: str
    coords: tuple[float, ...]
    text: str = ""
    role: str = "wire"
    anchor: str = "center"


def _box(items, cx, cy, w, h, role="box"):
    items.append(CanvasItem("rect", (cx - w / 2, cy - h / 2,
                                     cx + w / 2, cy + h / 2), role=role))


def pi_canvas_items(model: PiModel, width: int = CANVAS_W,
                    height: int = CANVAS_H) -> list[CanvasItem]:
    """
    The pi, as canvas primitives, with every element value placed.

    Coordinates are derived from `width` / `height` rather than hard-coded, so
    the window can be resized and the drawing follows.  Nothing here imports
    tkinter or knows a colour.

    The single-ended and differential drawings are ONE layout and differ only
    in the bottom rail: single-ended ends in a ground symbol, differential
    ends in a plain rail labelled "across the pair", because a differential
    pi's shunt goes between the two conductors and there is no reference node
    to draw.  Drawing a ground under it would be a lie that reads as a
    diagram -- see `tests/test_tracemodel.py` and the same rule on the text
    drawing.
    """
    items: list[CanvasItem] = []
    # The end-node NAMES hang outside the legs, so the side margin has to hold
    # a name and not just the leg.  Measured against a 12-character name in
    # the window's own font: 0.175 leaves 108 px at CANVAS_W, the name needs
    # ~84 plus the 12 px offset.  At 0.135 the last character of "out" was
    # drawn past the canvas edge and clipped with no indication at all.
    x_in = width * 0.175
    x_out = width - x_in
    y_top = height * 0.26   # room above the rail for the series
                           # values AND the |Q| note over them
    y_bot = height * 0.76
    span = x_out - x_in
    leg = y_bot - y_top

    # ---- the two end nodes, named
    r = width * 0.0065           # NOTHING in this function may be an absolute
    off = width * 0.019          # pixel count, or a resize skews the drawing
    for x, name, anch in ((x_in, model.in_name, "e"),
                          (x_out, model.out_name, "w")):
        items.append(CanvasItem("oval", (x - r, y_top - r, x + r, y_top + r),
                                role="node"))
        dx = -off if anch == "e" else off
        items.append(CanvasItem("text", (x + dx, y_top), text=name[:12],
                                role="label", anchor=anch))

    # ---- the series branch: the top rail, two boxes on it, values above
    items.append(CanvasItem("line", (x_in, y_top, x_out, y_top), role="wire"))
    svals = branch_value_lines(model, model.series)
    bw, bh = span * 0.22, height * 0.085
    for i, txt in enumerate(svals[:2]):
        cx = x_in + span * (0.30 + 0.40 * i)
        _box(items, cx, y_top, bw, bh)
        items.append(CanvasItem("text", (cx, y_top - bh * 0.5 - height * 0.055),
                                text=txt, role="value"))
    if model.series.is_resistive:
        items.append(CanvasItem(
            "text", (width / 2, y_top - bh * 0.5 - height * 0.135),
            text=f"|Q| = {abs(model.series.Q):.4g} -- a resistor here",
            role="note"))

    # ---- the two shunt legs, each with its boxes and values INSIDE the span
    for x, branch, side in ((x_in, model.shunt_in, +1),
                            (x_out, model.shunt_out, -1)):
        items.append(CanvasItem("line", (x, y_top, x, y_bot), role="wire"))
        vals = branch_value_lines(model, branch)
        vw, vh = width * 0.045, leg * 0.20
        for i, txt in enumerate(vals[:2]):
            cy = y_top + leg * (0.28 + 0.34 * i)
            _box(items, x, cy, vw, vh)
            items.append(CanvasItem(
                "text", (x + side * (vw * 0.5 + width * 0.018), cy),
                text=txt, role="value",
                anchor="w" if side > 0 else "e"))
        if len(vals) > 2:                       # the odd-mode restatement
            items.append(CanvasItem(
                "text", (x + side * (vw * 0.5 + width * 0.018),
                         y_top + leg * 0.62 + vh * 0.75),
                text=vals[2], role="note",
                anchor="w" if side > 0 else "e"))

    # ---- the bottom rail, and what it MEANS
    items.append(CanvasItem("line", (x_in, y_bot, x_out, y_bot), role="wire"))
    cx = width / 2
    if model.differential:
        items.append(CanvasItem("text", (cx, y_bot + height * 0.085),
                                text="across the pair -- no reference node",
                                role="note"))
    else:
        items.append(CanvasItem("line", (cx, y_bot, cx, y_bot + height * 0.035),
                                role="wire"))
        for i, half in enumerate((width * 0.055, width * 0.035, width * 0.016)):
            y = y_bot + height * (0.035 + 0.022 * i)
            items.append(CanvasItem("line", (cx - half, y, cx + half, y),
                                    role="gnd"))
        items.append(CanvasItem("text", (cx, y_bot + height * 0.155),
                                text="reference", role="label"))
    return items


# ============================================================================
# Bandwidth -- three numbers, kept apart on purpose
# ============================================================================

def bandwidth_lines(table: "list[Bandwidth]",
                    corners: "list[BranchCorner]",
                    model_band: "tuple[float, str]",
                    sweep_top_hz: float) -> list[str]:
    """
    The bandwidth block: the sensitivity table, then what explains it.

    The -3 dB column is a SWEEP over load capacitance rather than one number,
    because the number is not a property of the trace -- measured on a real
    line, the load alone moves it 7.3x.  The `vs marker` column is what turns
    the table into a verdict: "6.3 GHz" is a fact, "82x your marker" is an
    answer.

    The two trace-only numbers sit UNDER it rather than beside it: the model
    band and the branch corners explain where the -3 dB figure came from, and
    printing them in the same column as it would invite reading all three as
    one quantity.
    """
    if not table:
        return []
    ref = table[0]
    out = [
        f"  bandwidth      {BW_DROP_DB:.0f} dB of |V_load / V_src|, referenced "
        f"to {format_si(ref.reference_hz, 'Hz')}"
        f"   (source {format_si(ref.z_src_ohm, OHM)})",
        "",
        f"                 {'C_load':>10}  {'f_3dB':>16}     vs marker "
        f"({format_si(ref.marker_hz, 'Hz')})",
    ]
    for bw in table:
        cl = format_si(bw.c_load_farad, "F") if bw.c_load_farad else "open"
        if not bw.passband_ok:
            out.append(f"                 {cl:>10}  {'--':>16}     no flat "
                       f"passband at the bottom of the sweep")
            continue
        # A response that PEAKED has no -3 dB bandwidth: the crossing below
        # is measured from a baseline the curve left long ago, and printing
        # it would be one more plausible wrong number -- 6.17 GHz for a
        # response already 46 dB up was the measured case.  The peak is the
        # answer here, so the peak is what goes in the column.
        if bw.peak_db > PEAK_WARN_DB and math.isfinite(bw.peak_hz):
            out.append(f"                 {cl:>10}  {'peaks':>16}     "
                       f"{bw.peak_db:+.1f} dB at "
                       f"{format_si(bw.peak_hz, 'Hz')} -- LC resonance, no "
                       f"flat passband")
        elif bw.crossed:
            ratio = bw.ratio_to_marker
            rtxt = f"{ratio:,.0f} x" if math.isfinite(ratio) else ""
            out.append(f"                 {cl:>10}  "
                       f"{format_si(bw.f_3db_hz, 'Hz'):>16}     {rtxt:>10}")
        else:
            # NOTHING is extrapolated past the file.  A -3 dB point the sweep
            # never reaches is not a measurement, and printing one would be
            # inventing the part of the curve the file does not contain.
            out.append(f"                 {cl:>10}  "
                       f"{'> ' + format_si(sweep_top_hz, 'Hz'):>16}     "
                       f"top of sweep is {bw.droop_top_db:+.2f} dB")
    out.append("")

    mk = ref.droop_marker_db
    if math.isfinite(mk):
        verdict = ("the trace is not the limit here"
                   if abs(mk) < 0.5 else
                   "the trace is ALREADY costing you signal here")
        out.append(f"                 at the marker: {mk:+.4f} dB   {verdict}")

    band_hz, why = model_band
    reason = {
        "sweep": "the top of the sweep -- the FILE stops there, not the model",
        "drift": f"an element moved more than {LUMPED_DRIFT_WARN:.0%}",
        "resonance": "a branch's reactance changed sign (it resonated)",
    }.get(why, why)
    out.append(f"                 model band:    the pi holds to "
               f"{format_si(band_hz, 'Hz')} -- {reason}")

    if corners:
        parts = []
        for c in corners:
            if math.isfinite(c.f_hz):
                parts.append(f"{c.name} {c.kind} {format_si(c.f_hz, 'Hz')}")
            else:
                # R is round-off on this branch, so there is no corner to
                # quote.  '--' once, not '-- --'.
                parts.append(f"{c.name} --")
        out.append("                 corners:       " + "  ·  ".join(parts))
    return out


# ============================================================================
# The |H(f)| curve, as GEOMETRY -- no Tk, no matplotlib
# ============================================================================
#
# WHY IT IS NOT A `PLOT_TYPES` ENTRY.  It was going to be one.  `trace_y_values`
# is handed `(freqs, Z, plot_type, aux)` and `Z` there is the ONE-DIMENSIONAL
# trace impedance -- the transfer function needs the 2x2 `Zmat` AND a declared
# source and load.  The existing escape hatch for that is `aux`, the mechanism
# `k` uses; but an aux series is computed once at Calculate, so a curve fed
# that way would silently be drawn for different terminations than the table
# beside it names.  That is precisely the two-surfaces-disagree failure this
# whole module is built to avoid.
#
# Drawn HERE, in the same window as the two fields that define it, it reads
# the same `Bandwidth` objects the table does and cannot drift from them.  It
# also costs the plot panel nothing: `docs/conventions/plot_panel.md` says
# "Re-measure before adding a fourteenth control", `format_si` would render
# 0.02 dB as "20 mdB" in the readout, and the y-log switch is global with no
# per-type hook.  Three costs avoided and one class of lie avoided.


#: The bottom of the response window, in dB.  Deep enough to show the corner
#: and the roll-off past it; not so deep that the passband is a flat line
#: squashed against the top.
RESPONSE_DB_FLOOR = -24.0
RESPONSE_DB_CEIL = 3.0


def _db_to_y(db: float, top: float, bot: float) -> float:
    t = (RESPONSE_DB_CEIL - db) / (RESPONSE_DB_CEIL - RESPONSE_DB_FLOOR)
    return top + t * (bot - top)


def response_canvas_items(freqs, curves, width: int, height: int,
                          marker_hz: float = 0.0) -> list[CanvasItem]:
    """
    |H(f)| in dB against log frequency, as canvas primitives.

    `curves` is a sequence of `(label, db_array)` -- one per load capacitance,
    already referenced to the bottom of the sweep by `bandwidth_3db`'s own
    rule, so the picture and the table are reading one normalisation.

    Roles the renderer keys off: 'axis' for the frame and ticks, 'grid' for
    the -3 dB rule, 'curve0'..'curve3' for the traces (so the palette lives
    with the widgets), 'marker' for the working-frequency line, 'label' and
    'note' for text.
    """
    items: list[CanvasItem] = []
    if not curves or freqs is None or len(freqs) < 2:
        return items

    pad_l, pad_r = width * 0.115, width * 0.035
    pad_t, pad_b = height * 0.12, height * 0.22
    x0, x1 = pad_l, width - pad_r
    y0, y1 = pad_t, height - pad_b

    f = np.asarray(freqs, dtype=float)
    pos = f > 0.0
    if not np.any(pos):
        return items
    lo, hi = math.log10(float(f[pos][0])), math.log10(float(f[pos][-1]))
    if hi <= lo:
        return items

    def fx(hz: float) -> float:
        if hz <= 0.0:
            return x0
        t = (math.log10(hz) - lo) / (hi - lo)
        return x0 + min(1.0, max(0.0, t)) * (x1 - x0)

    # ---- frame
    items.append(CanvasItem("line", (x0, y0, x0, y1), role="axis"))
    items.append(CanvasItem("line", (x0, y1, x1, y1), role="axis"))

    # ---- the -3 dB rule, which is what the whole picture is about
    y3 = _db_to_y(BW_DROP_DB, y0, y1)
    items.append(CanvasItem("line", (x0, y3, x1, y3), role="grid"))
    items.append(CanvasItem("text", (x0 - width * 0.012, y3),
                            text=f"{BW_DROP_DB:.0f} dB", role="label",
                            anchor="e"))
    y_top_lbl = _db_to_y(0.0, y0, y1)
    items.append(CanvasItem("text", (x0 - width * 0.012, y_top_lbl),
                            text="0 dB", role="label", anchor="e"))

    # ---- decade ticks
    for d in range(int(math.floor(lo)), int(math.ceil(hi)) + 1):
        hz = 10.0 ** d
        if not (lo <= d <= hi):
            continue
        x = fx(hz)
        items.append(CanvasItem("line", (x, y1, x, y1 + height * 0.03),
                                role="axis"))
        items.append(CanvasItem("text", (x, y1 + height * 0.10),
                                text=format_si(hz, "Hz"), role="label"))

    # ---- the working frequency, so the reader sees where they actually live
    if marker_hz > 0.0 and lo <= math.log10(marker_hz) <= hi:
        xm = fx(marker_hz)
        items.append(CanvasItem("line", (xm, y0, xm, y1), role="marker"))
        items.append(CanvasItem("text", (xm, y0 - height * 0.055),
                                text=f"marker {format_si(marker_hz, 'Hz')}",
                                role="note"))

    # ---- the curves
    for ci, (label, db) in enumerate(curves):
        db = np.asarray(db, dtype=float)
        role = f"curve{ci % 4}"
        pts: list[float] = []
        for hz, v in zip(f, db):
            if hz <= 0.0 or not math.isfinite(v):
                continue
            pts.extend((fx(hz), _db_to_y(max(v, RESPONSE_DB_FLOOR), y0, y1)))
        if len(pts) >= 4:
            items.append(CanvasItem("line", tuple(pts), role=role))
        items.append(CanvasItem(
            "text", (x1, y0 + height * 0.075 * (ci + 1) - height * 0.03),
            text=label, role=role, anchor="e"))
    return items


# ============================================================================
# The workspace's SUMMARY table -- one line per net, sortable by column
# ============================================================================
#
# A monospace `tk.Text`, not a Treeview (`editor_and_tables.md`): the values
# are signed and the table has to read as one block of numbers.  The column
# geometry is published as `SUMMARY_COLUMNS` so the panel can turn a click on
# the header line into a sort key without re-deriving the layout, and the
# display order as `summary_order` so a click on a body line maps back to its
# `NetResult`.  Every value cell goes through `format_si`, the same formatter
# the drawing and the CLI print with -- one formatter, not a second copy.

#: (key, header, width, align) -- the one table the header, the body and
#: `SUMMARY_COLUMNS` are all derived from.
_SUMMARY_LAYOUT = (
    ("net", "Net", 14, "<"),
    ("r_ser", "R_ser", 10, ">"),
    ("l_ser", "L_ser", 10, ">"),
    ("c_in", "C_in", 10, ">"),
    ("c_out", "C_out", 10, ">"),
    ("f_3db", "f_3dB (open)", 13, ">"),
    ("lumped", "lumped", 12, "<"),
)
_SUMMARY_GAP = 2
#: The sort markers, ASCII on purpose -- red-zone X11 fonts.
_SORT_MARK = {False: " ^", True: " v"}


def _summary_columns() -> tuple:
    cols = []
    start = 0
    for key, header, width, _align in _SUMMARY_LAYOUT:
        cols.append((key, header, start, start + width))
        start += width + _SUMMARY_GAP
    return tuple(cols)


#: (key, header, start_char, end_char) per column, `[start, end)` on the
#: header line and on every body line.  A click at character `c` belongs to
#: the column with `start <= c < end`.
SUMMARY_COLUMNS = _summary_columns()
SUMMARY_KEYS = tuple(c[0] for c in SUMMARY_COLUMNS)


def _lumped_worst(res) -> float:
    """The largest finite |drift| over the three branches, or NaN."""
    model, reference = res.model, res.reference
    if model is None or reference is None:
        return float("nan")
    if reference.freq_hz == model.freq_hz:
        return float("nan")
    finite = [abs(v) for v in lumped_drift(model, reference).values()
              if math.isfinite(v)]
    return max(finite) if finite else float("nan")


def _lumped_cell(res) -> str:
    worst = _lumped_worst(res)
    if not math.isfinite(worst):
        return "--"
    if worst <= LUMPED_DRIFT_WARN:
        return "ok"
    return f"warn {worst * 100:.0f} %"


def _f3db_open(res) -> tuple[str, float]:
    """
    The open-load row of the sensitivity table as (cell, sort value).

    The same four cases `bandwidth_lines` prints, in the same order: no
    passband -> '--', a peak -> 'peaks', a crossing -> the frequency, and a
    sweep that never fell 3 dB -> '> <top>' (nothing is extrapolated past the
    file; the sort value is the top of the sweep, which the answer is at
    least).
    """
    nan = float("nan")
    bw = next((b for b in (res.bw_table or ()) if b.c_load_farad == 0.0), None)
    if bw is None or not bw.passband_ok:
        return ("--", nan)
    if bw.peak_db > PEAK_WARN_DB and math.isfinite(bw.peak_hz):
        return ("peaks", nan)
    if bw.crossed:
        return (format_si(bw.f_3db_hz, "Hz"), float(bw.f_3db_hz))
    try:
        top = float(np.asarray(res.freqs, dtype=float)[-1])
    except (TypeError, IndexError, ValueError):
        return ("--", nan)
    return ("> " + format_si(top, "Hz"), top)


def summary_sort_value(res, key: str):
    """
    What one net sorts BY under `key`: its name (lower-cased) for 'net', the
    element value for the four value columns, the open-load -3 dB point for
    'f_3db', the worst drift for 'lumped'.  None when the net has no model,
    NaN when it has one but that cell is empty; both sort last.
    """
    if res.model is None:
        return None
    m = res.model
    if key == "net":
        return str(res.name).lower()
    if key == "r_ser":
        return m.series.R_ohm
    if key == "l_ser":
        return m.series.L_henry
    if key == "c_in":
        return m.shunt_in.C_farad
    if key == "c_out":
        return m.shunt_out.C_farad
    if key == "f_3db":
        return _f3db_open(res)[1]
    if key == "lumped":
        return _lumped_worst(res)
    raise ValueError(f"Unknown summary column '{key}'; one of {SUMMARY_KEYS}")


def summary_order(results, sort_key: str | None = None,
                  descending: bool = False) -> list[int]:
    """
    Indices into `results` in DISPLAY order.  Line `k + 1` of
    `summary_table_lines` (after the header) is `results[order[k]]`.

    No key: table order.  With a key: the nets that have a value, sorted on
    it (stable, so ties keep table order), then the nets whose cell is empty
    (NaN), then the nets with no model at all -- an error row has nothing to
    sort by and goes to the bottom whichever way the column is sorted.
    """
    idx = list(range(len(results)))
    if sort_key is None:
        return idx
    valued, empty, missing = [], [], []
    for i in idx:
        v = summary_sort_value(results[i], sort_key)
        if v is None:
            missing.append(i)
        elif isinstance(v, float) and not math.isfinite(v):
            empty.append(i)
        else:
            valued.append((v, i))
    valued.sort(key=lambda t: t[0], reverse=bool(descending))
    return [i for _v, i in valued] + empty + missing


def _summary_row_cells(res) -> list[str]:
    m = res.model
    return [
        str(res.name),
        format_si(m.series.R_ohm, OHM),
        format_si(m.series.L_henry, "H"),
        format_si(m.shunt_in.C_farad, "F"),
        format_si(m.shunt_out.C_farad, "F"),
        _f3db_open(res)[0],
        "stale" if res.status == "stale" else _lumped_cell(res),
    ]


def _summary_line(cells: list[str]) -> str:
    parts = []
    for (key, _header, width, align), text in zip(_SUMMARY_LAYOUT, cells):
        text = text[:width]
        parts.append(f"{text:{align}{width}}")
    return (" " * _SUMMARY_GAP).join(parts).rstrip()


def summary_table_lines(results, sort_key: str | None = None,
                        descending: bool = False) -> list[str]:
    """
    One header line, then one line per net, columns per `SUMMARY_COLUMNS`:
    Net | R_ser | L_ser | C_in | C_out | f_3dB (open) | lumped.

    A net that has no numbers says WHY in place of them rather than leaving
    the line blank: 'error: <message>' on an error row, 'stale: ...' on a
    stale row that was never solved.  A stale row that still carries its old
    numbers prints them with 'stale' in the lumped column, because a number
    that was right when it was computed looks exactly like one that still is.
    The sorted column's header carries ' ^' or ' v'.
    """
    cells = []
    for key, header, _width, _align in _SUMMARY_LAYOUT:
        if sort_key is not None and key == sort_key:
            header = header + _SORT_MARK[bool(descending)]
        cells.append(header)
    lines = [_summary_line(cells)]
    net_w = _SUMMARY_LAYOUT[0][2]
    gap = " " * _SUMMARY_GAP
    for i in summary_order(results, sort_key, descending):
        res = results[i]
        if res.model is None:
            name = str(res.name)[:net_w]
            if res.status == "error":
                why = res.error or "not solved"
                lines.append(f"{name:<{net_w}}{gap}error: {why}")
            elif res.status == "stale":
                lines.append(f"{name:<{net_w}}{gap}stale: not calculated "
                             "yet -- press Calculate all")
            else:
                lines.append(f"{name:<{net_w}}{gap}{res.status}: no model")
            continue
        lines.append(_summary_line(_summary_row_cells(res)))
    return lines
