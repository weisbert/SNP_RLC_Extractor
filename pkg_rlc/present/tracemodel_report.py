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

from pkg_rlc.physics.core import format_si
from pkg_rlc.physics.tracemodel import (
    LUMPED_DRIFT_WARN, Branch, PiModel, lumped_drift,
)
from pkg_rlc.present.report import marker_freq_text

__all__ = [
    "pi_report_lines", "pi_schematic_lines", "branch_value_lines",
    "CanvasItem", "pi_canvas_items", "CANVAS_W", "CANVAS_H",
    "MODE_CONVERSION_WARN",
]

# Above this, a differential pair is imbalanced enough that the differential
# two-port is no longer the whole story and the report says so.  0.05 is 5 %
# of the differential term leaking to common mode -- measured on a synthetic
# pair, a 30 % conductor imbalance reads 0.261 and a symmetric one 9e-14, so
# the threshold sits an order of magnitude clear of numerical zero and well
# below a genuinely broken pair.
MODE_CONVERSION_WARN = 0.05

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
# `rejected_ui.md` allows exactly one form of schematic in this tool: "a
# `tk.Canvas` in a Toplevel, like the Ports & Roles window".  This is the half
# of that which can be tested without a display.
#
# `pi_canvas_items` returns primitives -- lines, rectangles and texts, in
# canvas coordinates -- and `pkg_rlc/panels/tracemodel_gui.py` does nothing
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
