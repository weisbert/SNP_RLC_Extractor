"""
pkg_rlc_panels_editor.py  --  the mode-aware editor for the selected trace.

The bottom half of the left-hand column: the pinned footer, the scrollable
form and every field on it, the two `RowTable`s, the `StylePicker`, both
scrollbars and the ONE function that decides them, the scrollregion, the
per-mode visibility, the two strips and the footer route, the "Edit as text…"
hatch, and the auto-apply sync chain that is this editor instead of an Apply
button.

HAS-A, NOT IS-A, and here that matters more than anywhere else in the window,
because nearly everything below is a measured ORDER or a measured FIXED
POINT:

  * the footer is packed side=BOTTOM BEFORE the scrollable body, or it falls
    off the bottom;
  * both scrollbars are decided in ONE function against the BODY frame's size
    and the FORM's requested size -- neither of which a scrollbar packed
    inside the body can change -- because deciding them separately is a limit
    cycle that hangs `update()`, i.e. the GUI and the test suite together;
  * `_refresh_editor_scrollregion` defers to `after_idle` and never calls
    `update_idletasks()`, which flushes geometry for the WHOLE application;
  * `_update_mode_visibility` runs on every TRACE selection as well as every
    MODE change, and only the second may reset the scroll;
  * `_apply_editor_strips` runs from Tk variable traces, once per keystroke,
    must never raise and may write to nothing but its own labels.

A mixin would hide every one of those.  App builds this panel where the
editor's LabelFrame was built, and `App._build_left_panel` still shows Global
Controls being packed side=BOTTOM BEFORE it.

WHAT MOVED WITH IT.  `StylePicker` (it is a field of this form, and it draws
from pkg_rlc_plot's palettes, which is L4), and the editor's own constants:
`MODE_PLACEHOLDERS`, `LABEL_PLACEHOLDER`, `EDITOR_FIELD_CHARS`,
`FROZEN_EDITOR_NOTE`, `MP_TABLE_HINT` / `_SHORT`, `MUTUAL_CURVE_HINT` /
`_SHORT` and `TEXT_DIALOG_NOTE`.  All are re-exported from `pkg_rlc_gui`,
where every existing caller and test looks for them.

WHAT THE PANEL OWNS, AND WHAT THE APP STILL OWNS.  The panel owns the WIDGETS
(every `ed_*` and every `_ed_*` widget), which App aliases onto itself so
`app.ed_conn_table`, `app._ed_canvas` and the rest keep resolving.  The
editor's mutable STATE stays on App -- `_suppress_editor_sync`,
`_ed_extra_lines`, `_ed_strips_pending`, `_ed_sync_after` / `_ed_sync_target`,
`_ed_shown_mode`, `_ed_scroll_pending` / `_ed_scroll_preserve` -- for the same
reason as in pkg_rlc_panels_results: they are reassigned at runtime and read
straight off `app` by the tests.

It may not import `pkg_rlc_gui` (L5 -> L6, tests/test_layering.py).
`TraceConfig` survives here only as an unevaluated annotation, and
`_config_signature` / `_draw_signature` -- pure functions over one, so L1
material -- are reached through the App's alias block.
"""

from __future__ import annotations

from typing import Optional, Sequence

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from pkg_rlc_core import (
    ConnectionRow,
    MeasPortRow,
    build_terminations_rows,
    merged_nodes,
    resolve_meas_ports,
    rows_to_dsl_text,
)
from pkg_rlc_plot import COLORS, LINESTYLES
from pkg_rlc_conntable import (
    CONN_TABLE_COLUMNS,
    CONN_TABLE_HINT,
    CONN_TABLE_HINT_SHORT,
    ColumnSpec,
    conn_cells_from_row,
    conn_hint_text,
    conn_row_from_cells,
    conn_table_layout,
)
from pkg_rlc_widgets import (
    PLACEHOLDER_FG,
    PlaceholderEntry,
    RowTable,
    _CollapsibleHint,
    _tk_dash,
    editor_scroll_fraction,
)
from pkg_rlc_validate import (
    _extra_lines_indicator,
    _footer_strip_text,
    _import_dsl_text,
    _ordering_diff_summary,
    _port_overview_text,
    _scope_conn_rows,
    _scope_dsl_text,
    _scope_mport_rows,
    _validation_messages,
    _validation_report,
    _validation_strip_text,
    scope_echo_messages,
    trace_is_composed,
)
from pkg_rlc_attrib_gui import (
    live_windows as attribution_windows,
    refresh_attribution_windows,
)
from pkg_rlc_files_gui import refresh_files_windows


# ============================================================================
# StylePicker -- the colour / linestyle chooser
# ============================================================================
#
# Replaces two integer Spinboxes ("Color idx: 7", "LS idx: 2") that gave the
# user no way to know what 7 looked like.  Three design points:
#
#  * The palette EXPANDS IN PLACE, it is not a popup Toplevel.  A grab_set
#    window that outlives its opener blocks event delivery, and update() then
#    never returns -- the exact failure mode already recorded for the editor's
#    scrollbar limit cycle, i.e. the GUI and the test suite hang together.
#    Expanding in place reuses the form's existing Canvas scrolling instead
#    (_refresh_editor_scrollregion(preserve=True)) and needs no grab, no
#    focus_force, no off-screen placement clamp.
#  * The stored value stays an INDEX into COLORS / LINESTYLES.  A free colour
#    chooser looks like an upgrade but _coupling_plot_traces derives the colours
#    of a mode-6 trace's expanded curves as (color_idx + n) % len(COLORS): an
#    arbitrary RGB has no "next colour", so all six curves would come out the
#    same.  This is a picker change only -- pkg_rlc_plot is untouched.
#  * All sizes are in units of the default font's linespace, never in pixels.
#    The editor's column budget is already documented as a 100%-font number
#    that does not survive 150% DPI; a pixel-sized Canvas would repeat that.


class StylePicker(ttk.Frame):
    """
    A line preview that expands into a 12-colour + 4-linestyle palette.

    The preview draws the real colour and the real dash pattern, so what is on
    the button is what the plot draws -- with one honest exception it labels
    itself: a mode 5/6 trace with G measurement ports expands into several
    curves consuming CONSECUTIVE palette slots, so `set_span(n)` makes the
    preview show the whole run and the '×n'.  Without that, picking a
    distinct-looking colour for a coupling trace tells you nothing about the
    five curves either side of it, and two traces can collide invisibly.
    """

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, **kw)
        self._color_idx = 0
        self._ls_idx = 0
        self._span = 1
        self._on_change = on_change
        self._expanded = False
        self._editable = True
        self._cells: dict = {}

        u = self._u = max(12, tkfont.Font(font="TkDefaultFont")
                          .metrics("linespace"))
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="w")
        self._preview = tk.Canvas(head, width=5 * u, height=2 * u,
                                  highlightthickness=1,
                                  highlightbackground="#a0a0a0",
                                  # A bare Canvas has takefocus='' and Tk's
                                  # traversal heuristic skips a widget with no
                                  # key bindings, so without this the style
                                  # control drops out of the Tab order that the
                                  # two Spinboxes were in.
                                  takefocus=True)
        self._preview.pack(side=tk.LEFT)
        self._preview.bind("<Button-1>", lambda e: self.toggle())
        self._preview.bind("<Return>", lambda e: self.toggle())
        self._preview.bind("<space>", lambda e: self.toggle())
        self._preview.bind("<Down>", lambda e: self.expand())
        self._preview.bind("<Escape>", lambda e: self.collapse())
        self._preview.bind("<FocusIn>", lambda e: self._preview.configure(
            highlightbackground="#0078d7"))
        self._preview.bind("<FocusOut>", lambda e: self._preview.configure(
            highlightbackground="#a0a0a0"))
        self._arrow = ttk.Label(head, text="▸")
        self._arrow.pack(side=tk.LEFT, padx=(4, 0))
        self._arrow.bind("<Button-1>", lambda e: self.toggle())

        self._palette = ttk.Frame(self)
        self._build_palette()
        self._palette.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self._palette.grid_remove()
        self._redraw_preview()

    # -------- palette --------

    def _build_palette(self) -> None:
        u = self._u
        colors = ttk.Frame(self._palette)
        colors.grid(row=0, column=0, sticky="w")
        # 2 rows of 6 rather than one row of 12: 12 x 2u would be ~390 px and
        # the editor canvas viewport is 431 px before the label column.
        for i, c in enumerate(COLORS):
            cv = tk.Canvas(colors, width=2 * u, height=u, highlightthickness=1,
                           highlightbackground="#d0d0d0", background=c,
                           cursor="hand2")
            cv.grid(row=i // 6, column=i % 6, padx=1, pady=1)
            cv.bind("<Button-1>", lambda e, k=i: self._choose(color=k))
            self._cells[("c", i)] = cv

        styles = ttk.Frame(self._palette)
        styles.grid(row=1, column=0, sticky="w", pady=(3, 0))
        for i, ls in enumerate(LINESTYLES):
            cv = tk.Canvas(styles, width=3 * u, height=u, highlightthickness=1,
                           highlightbackground="#d0d0d0", background="white",
                           cursor="hand2")
            cv.grid(row=0, column=i, padx=1)
            cv.create_line(3, u // 2, 3 * u - 3, u // 2, width=2, fill="black",
                           dash=_tk_dash(ls, 2))
            cv.bind("<Button-1>", lambda e, k=i: self._choose(ls=k))
            self._cells[("l", i)] = cv

    def _choose(self, color: int | None = None, ls: int | None = None) -> None:
        if not self._editable:
            return
        if color is not None:
            self._color_idx = color
        if ls is not None:
            self._ls_idx = ls
        self._redraw_preview()
        self._mark_selection()
        if self._on_change is not None:
            self._on_change()

    def _mark_selection(self) -> None:
        for (kind, i), cv in self._cells.items():
            sel = (self._color_idx if kind == "c" else self._ls_idx) == i
            cv.configure(highlightbackground="#000000" if sel else "#d0d0d0",
                         highlightthickness=2 if sel else 1)

    # -------- preview --------

    def _redraw_preview(self) -> None:
        u = self._u
        cv = self._preview
        cv.delete("all")
        w = 5 * u
        color = COLORS[self._color_idx % len(COLORS)]
        dash = _tk_dash(LINESTYLES[self._ls_idx % len(LINESTYLES)], 2)
        cv.create_line(4, u * 0.55, w - 4, u * 0.55, width=2, fill=color,
                       dash=dash)
        if self._span > 1:
            # The consecutive slots this trace will actually occupy, so the
            # preview cannot claim a coupling trace is one colour.
            shown = min(self._span, 6)
            cw = (w - 8) / shown
            for j in range(shown):
                x0 = 4 + j * cw
                cv.create_rectangle(x0, u * 1.05, x0 + cw - 2, u * 1.55,
                                    fill=COLORS[(self._color_idx + j)
                                                % len(COLORS)],
                                    outline="")
            cv.create_text(w - 4, u * 1.75, anchor="se",
                           text=f"×{self._span}", font=("TkDefaultFont", 7),
                           fill="#606060")

    # -------- public API --------

    def set_span(self, n: int) -> None:
        """How many curves this trace expands into (mode 5/6). 1 = plain."""
        n = max(1, int(n))
        if n != self._span:
            self._span = n
            self._redraw_preview()

    def get(self) -> tuple[int, int]:
        return self._color_idx, self._ls_idx

    def set(self, color_idx: int, ls_idx: int) -> None:
        self._color_idx = int(color_idx) % len(COLORS)
        self._ls_idx = int(ls_idx) % len(LINESTYLES)
        self._redraw_preview()
        self._mark_selection()

    def expand(self) -> None:
        if not self._expanded:
            self.toggle()

    def collapse(self) -> None:
        if self._expanded:
            self.toggle()

    def set_editable(self, editable: bool) -> None:
        """
        Lock the picker for a frozen trace.

        A flag rather than a `state(['disabled'])` on the Frame: the palette is
        twelve bare tk.Canvas cells with <Button-1> bindings, and ttk state
        does not cascade to children, let alone to a Canvas.  Guarding _choose
        and expand() is what actually stops a click.  The preview keeps drawing
        the real colour -- a snapshot's style is worth READING -- but it leaves
        the Tab order, because a focusable control that answers no key is worse
        than one that is not there.
        """
        self._editable = bool(editable)
        if not self._editable:
            self.collapse()
        try:
            self._preview.configure(takefocus=self._editable)
            self._arrow.state(["!disabled"] if self._editable else ["disabled"])
        except Exception:                               # pragma: no cover
            pass

    def toggle(self) -> None:
        if not self._editable and not self._expanded:
            # Collapsing is always allowed (set_editable(False) uses it); only
            # OPENING a locked palette is refused.
            return
        self._expanded = not self._expanded
        self._arrow.configure(text="▾" if self._expanded else "▸")
        if self._expanded:
            self._mark_selection()
            self._palette.grid()
        else:
            self._palette.grid_remove()
        # The form got taller or shorter; the editor's scrollregion is measured,
        # not inferred, so it has to be told.  preserve=True -- collapsing must
        # not scroll the user away from the row they just used.
        app = self.winfo_toplevel()
        refresh = getattr(app, "_refresh_editor_scrollregion", None)
        if callable(refresh):
            refresh(preserve=True)


# Per-mode placeholder hints for the remaining PlaceholderEntry fields, keyed
# by (field, mode) -> hint text.  A table-based mode registers NOTHING here: a
# table cell cannot hold a hint (PlaceholderEntry deletes it on <FocusIn>), so
# its hint is a _CollapsibleHint label under the table instead.
MODE_PLACEHOLDERS: dict[str, dict[int, str]] = {
    "port_a": {
        1: "e.g.  1   (signal port to drive)",
        2: "e.g.  1,2   (positive group; ports shorted internally)",
        3: "e.g.  1,2",
    },
    "port_b": {
        2: "e.g.  3,4   (negative group; ports shorted internally)",
        3: "e.g.  3,4",
    },
    "short_pairs": {
        3: "e.g.  3-4   or   3-4, 5-6   or   1-2-3-4   (chain dashes to short >2 ports)",
    },
    "gnd": {
        1: "e.g.  5   or   6:1:14   (V=0; put supply/VDD balls here too)",
        2: "e.g.  5   (optional; V=0 -- supply balls belong here too)",
        3: "e.g.  5   (optional; V=0 -- supply balls belong here too)",
        6: "e.g.  5:1:8   (optional; V=0 -- supply balls belong here too)",
    },
}

LABEL_PLACEHOLDER = "trace name shown in plot legend (optional)"

# The editor's single-line fields, in characters.  A MEASURED number, not a
# taste: the form's requested width is the widest label (129 px,
# "GND / VDD (AC gnd):") plus the widest field plus 8 px of cell padding, and
# it is checked against a 431 px canvas.  At 42 chars a ttk.Entry asks 300 px
# and the form asks 437 -- six pixels of overhang, which raises the editor's
# horizontal scrollbar, which costs 17 px of a 45 px viewport at the 1040x600
# minsize.  At 40 it asks 286 and the form 423, and modes 1/2/3 keep the whole
# 45 px.  The fields are sticky="we", so this is only their MINIMUM; at any
# width where the form fits they look identical.
EDITOR_FIELD_CHARS = 40

# The note that explains why the editor is greyed out.  A named constant
# because three tests and one menu lookup key off it and its two neighbours,
# and a menu entry nobody can find is the same as no feature at all.  Those
# two neighbours -- FREEZE_MENU_LABEL / UNFREEZE_MENU_LABEL -- live in
# pkg_rlc_panels_traces with the menu they label.  This one names the
# EDITOR's state, so it lives here.
FROZEN_EDITOR_NOTE = (
    "❄ frozen snapshot — unfreeze to edit "
    "(right-click it in the Traces list)")

# Shown under the measurement-port table. This has to carry everything
# the six retired placeholder hints used to say, because a plain ttk.Entry in
# a table cell has no room for a hint of its own -- so it is long, and it lives
# behind a disclosure triangle (collapsed by default) rather than costing 125px
# of a form that already does not fit. Unlike a PlaceholderEntry's hint, focus
# cannot destroy it: it is a Label, and it is still there after you click away.
MP_TABLE_HINT_SHORT = "'+' = red probe, '−' = black (empty = vs GND)"
MP_TABLE_HINT = (
    "One row per measurement port. '+' is the red probe, '−' the black one; "
    "leave '−' empty to measure against GND. Ports listed on the same side are "
    "tied together, and ranges work (5,7 or 5:1:8). Two or more rows give you "
    "the coupling (M, k) between them. Names are optional (P1, P2, … if blank) "
    "but 'A' and 'B' are reserved. Port NUMBERS are what these cells take -- "
    "for the file's port names use 'Show Ports' at the top of this panel. It "
    "opens 'Ports & Roles': every port with its name, what your spec is doing "
    "with it, and which row said so. Select rows there and 'Set as probe +' "
    "fills a row in here for you, as a collapsed range."
)


# Shown under the mode-6 plot checkboxes: the subplot grid is shared with the
# self curves, so the axis titles need reinterpreting on a mutual curve.
MUTUAL_CURVE_HINT_SHORT = "on a mutual curve, L(nH) reads as M and C(pF) as C_c"
MUTUAL_CURVE_HINT = (
    "On a mutual curve the L(nH) subplot IS M in nH and C(pF) IS the coupling "
    "capacitance C_c; the k subplot is filled in for mutual curves only."
)


# What the "Edit as text…" dialog promises, verbatim. The round trip is
# deliberately lossy in known ways and saying so is the difference between a
# normalisation and a surprise.
TEXT_DIALOG_NOTE = (
    "This is the spec that will be computed. Your text is rewritten into "
    "canonical form: 'gnd'→'ground', 'signal a'→'signal A', 'r='→'R=', R/L/C "
    "reordered to R,L,C, and every measurement port emitted before every "
    "connection (which is what makes a later 'ground' win). Blank lines and "
    "end-of-line comments are dropped; anything the table cannot represent is "
    "kept verbatim."
)


class EditorPanel:
    """The Edit-Selected-Trace section: the footer, the form, and the sync."""

    def __init__(self, parent: ttk.LabelFrame, app) -> None:
        self.app = app
        self._build_editor(parent)

    def _build_editor(self, parent: ttk.LabelFrame) -> None:
        """
        Editor = a pinned footer + a scrollable form.

        The form is mode-dependent and in mode 5 it is taller than any laptop
        screen, so it lives in a Canvas.  The footer button does NOT: it is
        packed side=BOTTOM *first*, outside the scroll region, so the form can
        clip or scroll all it likes and the button stays reachable.  Packing it
        after the expanding body is what made it fall off the bottom.

        That button used to be "Apply to Trace".  The editor now applies itself
        as you type (see _schedule_editor_sync), so the slot went to the thing
        this GUI had no way to do at all: recompute ONE trace.  The only compute
        path was "Calculate All & Plot", which on four traces over a large
        package file does four times the work the user asked for on every pass
        of the edit / compute / read-the-cursor loop.  The footer is also not
        allowed to be empty -- Tk does not reissue a geometry request when a
        master's last slave is removed, so an emptied footer would keep its
        requested height forever.
        """
        foot = ttk.Frame(parent)
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(foot, text="Calculate This Trace",
                   command=self.app._on_calculate_selected
                   ).pack(side=tk.RIGHT, padx=6, pady=3)
        # The one-line summary of the two below-the-fold strips (see
        # FOOTER_STRIP_CHARS).  It shares the button's 33 px row, so it costs
        # no vertical space at all -- and it is created here but NOT packed:
        # _update_mode_visibility packs it, because it only has a meaning in
        # mode 5.  Whenever it is packed it goes in AFTER the button, never
        # before, since pack unmaps from the END: if the footer is ever
        # squeezed it must be this label that goes, not Calculate This Trace.
        self._ed_foot = foot
        # R1-4: the footer verdict is the only always-visible pixel of the
        # editor, and it used to be a dead end -- measured at the 1040x600
        # minsize, the messages it counts sit 366 and 387 px below the fold of
        # a 45 px viewport, and every mode change scrolls the form back to the
        # top.  Clicking it scrolls to the row it is talking about.  It costs
        # ZERO pixels: the affordance is the hand cursor plus an underline on
        # hover, and an underline changes no font metric (measured: the
        # label's reqwidth/reqheight are identical with and without it).
        self._ed_footer_font = tkfont.Font(font="TkDefaultFont")
        self._ed_footer_font_u = tkfont.Font(font="TkDefaultFont")
        self._ed_footer_font_u.configure(underline=1)
        self.ed_footer_strip = ttk.Label(foot, anchor="w", wraplength=0,
                                         foreground=PLACEHOLDER_FG,
                                         cursor="hand2",
                                         font=self._ed_footer_font)
        self.ed_footer_strip.bind("<Button-1>", self._on_footer_route)
        self.ed_footer_strip.bind(
            "<Enter>",
            lambda _e: self.ed_footer_strip.configure(
                font=self._ed_footer_font_u))
        self.ed_footer_strip.bind(
            "<Leave>",
            lambda _e: self.ed_footer_strip.configure(
                font=self._ed_footer_font))

        self._ed_body = body = ttk.Frame(parent)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._ed_vsb = ttk.Scrollbar(body, orient="vertical")
        self._ed_canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0,
                                    yscrollcommand=self._ed_scroll_set,
                                    xscrollcommand=self._ed_hscroll_set)
        self._ed_vsb.configure(command=self._ed_canvas.yview)
        # The horizontal scrollbar is the safety net under DPI scaling: the
        # column-width budget the tables fit into is a 100%-font number, and at
        # 150% no column set fits.
        #
        # Both scrollbars are DIRECT children of `body` and neither is packed
        # here: _apply_editor_scrollbars decides both together and packs them
        # with `before=self._ed_canvas`, because pack unmaps from the END and a
        # fixed-size slave after an expand=True one disappears rather than
        # clipping.  The horizontal one used to live in a permanently packed
        # host frame instead -- which is NOT "0 px tall while empty": Tk does
        # not reissue a geometry request when a master's LAST slave is removed,
        # so once the first layout pass had packed and unpacked the scrollbar
        # the empty host kept a 17 px requested height forever, in EVERY mode,
        # including 1/2/3 which never raise the scrollbar at all (measured at
        # 1040x600: 45 px of editor viewport became 28).
        self._ed_hsb = ttk.Scrollbar(body, orient="horizontal",
                                     command=self._ed_canvas.xview)
        self._ed_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._ed_form = ttk.Frame(self._ed_canvas)
        self._ed_win = self._ed_canvas.create_window(
            (0, 0), window=self._ed_form, anchor="nw")
        self._ed_canvas.bind("<Configure>", self._on_editor_canvas_configure)
        # The form's own <Configure> is needed as well as the canvas's, for the
        # same reason RowTable needs both: a table that grows changes the form's
        # size one idle pass AFTER the row was added, so a scrollregion measured
        # from the row-add callback alone is one row short and the new row
        # cannot be scrolled to.
        self._ed_form.bind(
            "<Configure>",
            lambda e: self._refresh_editor_scrollregion(preserve=True))
        self.app._register_scrollable(self._ed_canvas, self._ed_wheel)

        self._build_editor_form(self._ed_form)

    def _ed_scroll_set(self, first: str, last: str) -> None:
        """Move the thumb only.  Whether the bar is SHOWN is decided in one
        place -- see _apply_editor_scrollbars."""
        self._ed_vsb.set(first, last)

    def _on_editor_canvas_configure(self, event) -> None:
        """
        Keep the form at least as wide as it asked for, and re-measure.

        Never let it be narrower: a squashed 6-column table with no horizontal
        scrollbar is unreachable content.  The scrollregion has to be recomputed
        here too, not only on a mode change -- when the vertical scrollbar
        appears the canvas narrows 448 -> 431, and a scrollregion still 448 wide
        raises a horizontal scrollbar for 17 px of nothing.
        """
        self._ed_canvas.itemconfigure(
            self._ed_win, width=max(event.width,
                                    self._ed_form.winfo_reqwidth()))
        self._refresh_editor_scrollregion(preserve=True)

    def _ed_hscroll_set(self, first: str, last: str) -> None:
        """Thumb only; same reason as _ed_scroll_set."""
        self._ed_hsb.set(first, last)

    # Both editor scrollbars autohide.  Deciding each one from its own
    # scrollcommand is a LIMIT CYCLE, not a race: hiding the horizontal bar
    # gives the canvas 17 px of height back, which can hide the vertical bar,
    # which gives 17 px of width back, which brings the horizontal bar back.
    # Measured with the two decisions split: the editor flipped 431x245 <->
    # 448x228 forever and update() never returned -- the whole GUI hangs.
    #
    # So both are decided HERE, in one pass, from inputs that a scrollbar
    # cannot change: `body`'s size (set by its master, not by its slaves) and
    # the form's REQUESTED size.  Same inputs -> same answer -> it converges.
    def _apply_editor_scrollbars(self) -> None:
        body = self._ed_body
        avail_w, avail_h = body.winfo_width(), body.winfo_height()
        form_w = self._ed_form.winfo_reqwidth()
        form_h = self._ed_form.winfo_reqheight()
        vsb_w = max(self._ed_vsb.winfo_reqwidth(), 1)
        hsb_h = max(self._ed_hsb.winfo_reqheight(), 1)

        need_v = form_h > avail_h
        need_h = form_w > avail_w - (vsb_w if need_v else 0)
        if need_h and not need_v:
            # The bar we just decided to show eats the height. Re-check the
            # vertical one ONCE and never re-check the horizontal one: a
            # second round trip is how the cycle above got started.
            need_v = form_h > avail_h - hsb_h

        # Pinned ahead of the canvas: pack unmaps from the end, and an
        # expand=True canvas packed first would leave nothing for either bar.
        if need_v:
            self._ed_vsb.pack(side=tk.RIGHT, fill=tk.Y,
                              before=self._ed_canvas)
        else:
            self._ed_vsb.pack_forget()
        if need_h:
            self._ed_hsb.pack(side=tk.BOTTOM, fill=tk.X,
                              before=self._ed_canvas)
        else:
            self._ed_hsb.pack_forget()

    def _ed_wheel(self, event) -> bool:
        """Scroll the editor form. Returns False when it has nowhere to go."""
        first, last = self._ed_canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self._ed_canvas.yview_scroll(int(-event.delta / 120), "units")
        return True

    def _refresh_editor_scrollregion(self, preserve: bool = False) -> None:
        """
        Re-measure after grid()/grid_remove() or a row add changed the form.

        The <Configure> binding on the inner frame is NOT sufficient: measured,
        after hiding 17 rows the scrollregion stayed at its old height and the
        view stayed scrolled down, leaving a short form parked out of sight;
        and after six '+ Add' clicks in mode 6 the form grew 357 -> 476 px
        while the scrollregion stayed at 357, so the new rows could not be
        reached by scrolling at all.

        `preserve` says what to do with the scroll offset.  A MODE CHANGE must
        reset it (preserve=False) or a now-short form stays parked out of
        sight; a ROW ADD must keep it (preserve=True) or the row the user just
        created scrolls away and the view jumps back to the File combobox.

        Deferred to after_idle, NEVER update_idletasks(). This runs during
        construction too (_build_editor_form ends by calling
        _update_mode_visibility), and forcing a geometry pass there is exactly
        what collapsed the Results pane's PanedWindow sash to 2px --
        tests/test_row_table.py::TestResultsPaneVisible caught this very edit.
        """
        if getattr(self.app, "_ed_scroll_pending", False):
            # Coalescing a reset with a preserve: the reset wins, because the
            # reason for it (the form is a different shape now) has not gone
            # away.  Only within one pending batch -- the flag is re-armed
            # below, or a stale False would swallow every later row add.
            self.app._ed_scroll_preserve = self.app._ed_scroll_preserve and preserve
            return
        self.app._ed_scroll_preserve = preserve
        self.app._ed_scroll_pending = True
        self.app.after_idle(self._apply_editor_scrollregion)

    def _apply_editor_scrollregion(self) -> None:
        self.app._ed_scroll_pending = False
        if not self._ed_canvas.winfo_exists():
            return
        self._apply_editor_scrollbars()
        first = self._ed_canvas.yview()[0]
        # Re-apply the window item's width here as well as in the canvas's
        # <Configure>: that binding fires only when the CANVAS resizes, so
        # after a table grows the item width is stale and the form is clipped.
        self._ed_canvas.itemconfigure(
            self._ed_win,
            width=max(self._ed_canvas.winfo_width(),
                      self._ed_form.winfo_reqwidth()))
        self._ed_canvas.configure(scrollregion=self._ed_canvas.bbox("all"))
        if self.app._ed_scroll_preserve:
            self._ed_canvas.yview_moveto(first)
        else:
            self._ed_canvas.yview_moveto(0)
            self._ed_canvas.xview_moveto(0)

    def _build_editor_form(self, parent: ttk.Frame) -> None:
        row = 0
        # Why the editor is greyed out.  ROW 0, and gridded/removed rather than
        # packed into the footer: the footer's whole spare budget is one line
        # and mode 5 already spends it (_footer_strip_text), whereas the form
        # is inside a Canvas that every mode change scrolls back to the top --
        # so row 0 is the one place in the editor that is always the first
        # thing on screen.
        self.ed_frozen_note = ttk.Label(parent, anchor="w", justify=tk.LEFT,
                                        wraplength=400, foreground="#b04000",
                                        text=FROZEN_EDITOR_NOTE)
        self.ed_frozen_note.grid(row=row, column=0, columnspan=4, sticky="we",
                                 padx=2, pady=(2, 1))
        self.ed_frozen_note.grid_remove()
        row += 1

        # File combobox
        ttk.Label(parent, text="File:").grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_file_var = tk.StringVar()
        # width=38, not 40, and it is a MEASURED number.  The editor form's
        # requested width is the widest LABEL (129 px, "GND / VDD (AC gnd):")
        # plus the widest FIELD plus 8 px of cell padding, and this combobox
        # is the widest field in modes 1/2/3 -- 303 px against the 431 px
        # canvas, for a form of 440.  Nine pixels of overhang bought a
        # horizontal scrollbar, and at the 1040x600 minsize that scrollbar
        # costs 17 px of a 45 px editor viewport: the four modes with no table
        # paid a third of their remaining height to reach 9 px they did not
        # need, while Mode 5 -- whose column budget was actually measured --
        # fitted and paid nothing.  It is sticky="we", so this changes only
        # the minimum; at any width where the form fits it looks identical.
        # (Mode 6's form is 462 px and still raises the bar. That overhang is
        # the measurement-port table, not this, and it is real.)
        self.ed_file_cbo = ttk.Combobox(parent, textvariable=self.ed_file_var,
                                        state="readonly", width=38)
        self.ed_file_cbo.grid(row=row, column=1, columnspan=3, sticky="we", padx=2, pady=1)
        row += 1

        # Mode radio
        ttk.Label(parent, text="Mode:").grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        mode_frame = ttk.Frame(parent)
        mode_frame.grid(row=row, column=1, columnspan=3, sticky="w")
        self.ed_mode_var = tk.IntVar(value=1)
        self._ed_mode_buttons: list = []
        # Mode 4 ("A ↔ B + VDD/GND") is retired: VDD is an AC ground, so it is
        # mode 2 with the supply ports merged into GND. Codes stay stable.
        for v, label in [(1, "Port(s) → GND"),
                         (2, "A ↔ B"),
                         (3, "A ↔ B + Short Pairs"),
                         (6, "+/- Ports / Coupling (M, k)"),
                         (5, "Custom (advanced)")]:
            rb = ttk.Radiobutton(mode_frame, text=label,
                                 variable=self.ed_mode_var, value=v,
                                 command=self._on_mode_changed)
            rb.pack(side=tk.TOP, anchor="w")
            self._ed_mode_buttons.append(rb)
        row += 1

        # Port A
        self.ed_porta_lbl = ttk.Label(parent, text="Signal / Port A:")
        self.ed_porta_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_porta = PlaceholderEntry(parent, width=EDITOR_FIELD_CHARS,
                                         placeholder=MODE_PLACEHOLDERS["port_a"][1])
        self.ed_porta.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Port B
        self.ed_portb_lbl = ttk.Label(parent, text="Port B:")
        self.ed_portb_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_portb = PlaceholderEntry(parent, width=EDITOR_FIELD_CHARS,
                                         placeholder=MODE_PLACEHOLDERS["port_b"][2])
        self.ed_portb.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Short pairs
        self.ed_short_lbl = ttk.Label(parent, text="Short Pairs:")
        self.ed_short_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_short = PlaceholderEntry(parent, width=EDITOR_FIELD_CHARS,
                                         placeholder=MODE_PLACEHOLDERS["short_pairs"][3])
        self.ed_short.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # --- Modes 5 and 6: measurement ports (probe pairs) ---
        # One table row per measurement port, added with the '+' button. This
        # replaces two hard-coded ports plus a free-text box for the third
        # onward -- that cliff (ports 1-2 get fields, port 3+ gets syntax) was
        # the same disease as the Mode 5 text box, just less obvious.  Mode 5
        # shows the same table plus the connections table below it, so the
        # superset relationship is in the layout rather than hidden as a trap.
        self.ed_mp_lbl = ttk.Label(parent, text="Measurement\nports:",
                                   justify=tk.RIGHT)
        self.ed_mp_lbl.grid(row=row, column=0, sticky="ne", padx=2, pady=1)
        self.ed_mp_table = RowTable(
            parent,
            columns=(ColumnSpec("name", "Name", 9),
                     ColumnSpec("plus", "+ ports (red)", 13),
                     ColumnSpec("minus", "− ports (black)", 13)),
            row_factory=MeasPortRow,
            on_change=self._on_editor_rows_changed,
            min_rows=1, max_visible=5,
        )
        self.ed_mp_table.grid(row=row, column=1, columnspan=3, sticky="we",
                              padx=2, pady=1)
        self.ed_mp_table.register_wheel(self.app._register_scrollable)
        row += 1

        self.ed_mp_hint = _CollapsibleHint(parent, MP_TABLE_HINT_SHORT,
                                           MP_TABLE_HINT)
        self.ed_mp_hint.grid(row=row, column=1, columnspan=3, sticky="we",
                             padx=2, pady=(0, 2))
        row += 1

        # GND / VDD ports (VDD merged in: for AC small-signal they are the same)
        self.ed_gnd_lbl = ttk.Label(parent, text="GND / VDD (AC gnd):")
        self.ed_gnd_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_gnd = PlaceholderEntry(parent, width=EDITOR_FIELD_CHARS,
                                       placeholder=MODE_PLACEHOLDERS["gnd"][1])
        self.ed_gnd.grid(row=row, column=1, columnspan=3, sticky="we",
                         padx=2, pady=1)
        row += 1

        # What to plot.  "this trace" is the per-trace visibility switch and is
        # shown in EVERY mode; "self" / "mutual" pick which of a coupling
        # trace's expanded curves are drawn and stay gated to modes 5/6.  They
        # share one row because they answer the same question at two scales,
        # and because the row costs nothing: measured, adding "this trace"
        # takes the frame from 113 px to 189 px against a 437 px row.
        # The children are GRIDDED, not packed: grid_remove()/grid() puts a
        # widget back in the same column, whereas re-packing appends it to the
        # end and would silently reorder the row.
        self.ed_plot_lbl = ttk.Label(parent, text="Plot:")
        self.ed_plot_lbl.grid(row=row, column=0, sticky="e", padx=2, pady=1)
        self.ed_plot_frame = ttk.Frame(parent)
        self.ed_plot_frame.grid(row=row, column=1, columnspan=3, sticky="w",
                                padx=2, pady=1)
        self.ed_enabled_var = tk.BooleanVar(value=True)
        self.ed_plot_self_var = tk.BooleanVar(value=True)
        self.ed_plot_mutual_var = tk.BooleanVar(value=True)
        self.ed_enabled_cb = ttk.Checkbutton(
            self.ed_plot_frame, text="this trace",
            variable=self.ed_enabled_var, command=self._on_enabled_toggled)
        self.ed_enabled_cb.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.ed_plot_self_cb = ttk.Checkbutton(
            self.ed_plot_frame, text="self", variable=self.ed_plot_self_var)
        self.ed_plot_self_cb.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.ed_plot_mutual_cb = ttk.Checkbutton(
            self.ed_plot_frame, text="mutual", variable=self.ed_plot_mutual_var)
        self.ed_plot_mutual_cb.grid(row=0, column=2, sticky="w")
        row += 1

        self.ed_mutual_hint = _CollapsibleHint(parent, MUTUAL_CURVE_HINT_SHORT,
                                               MUTUAL_CURVE_HINT)
        self.ed_mutual_hint.grid(row=row, column=1, columnspan=3, sticky="we",
                                 padx=2, pady=(0, 2))
        row += 1

        # --- Mode 5: connections table ---
        # The caption and the 'Edit as text…' button share ONE sub-frame across
        # all four columns, so the button's width cannot influence the grid
        # column widths the table needs.  The table itself spans columns 0-3
        # rather than sitting beside a label: the label column costs 91 px and
        # the budget is 431.
        self.ed_conn_head = ttk.Frame(parent)
        self.ed_conn_head.grid(row=row, column=0, columnspan=4, sticky="we",
                               padx=2, pady=(4, 0))
        ttk.Label(self.ed_conn_head, text="Connections:").pack(side=tk.LEFT)
        self.ed_edit_text_btn = ttk.Button(self.ed_conn_head,
                                           text="Edit as text…",
                                           command=self._on_edit_as_text)
        self.ed_edit_text_btn.pack(side=tk.RIGHT)
        # extra_lines has no widget of its own, so without this the two tables
        # can be EMPTY while a hidden block of DSL is in force -- and, being
        # emitted last, winning over anything typed into them afterwards.  The
        # count rides on the caption row, so it costs no vertical space.
        self.ed_extra_lbl = ttk.Label(self.ed_conn_head,
                                      foreground=PLACEHOLDER_FG)
        self.ed_extra_lbl.pack(side=tk.LEFT, padx=(6, 0))
        row += 1

        self.ed_conn_table = RowTable(
            parent, columns=CONN_TABLE_COLUMNS, row_factory=ConnectionRow,
            on_change=self._on_editor_rows_changed,
            min_rows=1, max_visible=6,
            # R1-1: the cells a row has follow its Kind, and the short row's
            # tied group is ONE cell over the two fields ConnectionRow still
            # stores it in.
            layout_fn=conn_table_layout,
            to_cells=conn_cells_from_row, from_cells=conn_row_from_cells,
        )
        self.ed_conn_table.grid(row=row, column=0, columnspan=4, sticky="we",
                                padx=2, pady=1)
        self.ed_conn_table.register_wheel(self.app._register_scrollable)
        row += 1

        # Direct child of the form, NOT of a sub-frame: _CollapsibleHint._toggle
        # re-renders by walking self.master.winfo_children(), and the expanded
        # flag is class-level, so a hint one level down desynchronises its arrow
        # from the shared state.
        self.ed_conn_hint = _CollapsibleHint(parent, CONN_TABLE_HINT_SHORT,
                                             CONN_TABLE_HINT)
        self.ed_conn_hint.grid(row=row, column=0, columnspan=4, sticky="we",
                               padx=2, pady=(0, 2))
        row += 1

        self.ed_overview = ttk.Label(parent, anchor="w",
                                     foreground=PLACEHOLDER_FG)
        self.ed_overview.grid(row=row, column=0, columnspan=4, sticky="we",
                              padx=2)
        row += 1
        self.ed_validation = ttk.Label(parent, anchor="w", justify=tk.LEFT,
                                       wraplength=400)
        self.ed_validation.grid(row=row, column=0, columnspan=4, sticky="we",
                                padx=2)
        row += 1

        # Label
        ttk.Label(parent, text="Label:").grid(row=row, column=0, sticky="e",
                                              padx=2, pady=1)
        self.ed_label = PlaceholderEntry(parent, width=EDITOR_FIELD_CHARS,
                                         placeholder=LABEL_PLACEHOLDER)
        self.ed_label.grid(row=row, column=1, columnspan=3, sticky="we",
                           padx=2, pady=1)
        row += 1

        # Colour / linestyle.  One preview that expands into a palette, in place
        # of the two "Color idx / LS idx" Spinboxes -- an integer 0..11 told the
        # user nothing about what would be drawn.  Dropping the two tk.IntVars
        # also removes the only place _sync_editor_to_trace could raise, which
        # matters now that it runs from a variable trace on every keystroke: an
        # IntVar raises TclError on the empty string a normal select-all-retype
        # passes through.
        ttk.Label(parent, text="Style:").grid(row=row, column=0, sticky="ne",
                                              padx=2, pady=1)
        self.ed_style = StylePicker(parent, on_change=self._on_style_changed)
        self.ed_style.grid(row=row, column=1, columnspan=3, sticky="w",
                           padx=2, pady=1)
        row += 1

        # No button here: the footer holds "Calculate This Trace", pinned
        # outside the scroll region so it survives however long this form gets.
        # And there is nothing to apply -- the editor writes itself into the
        # selected trace as you type (see _schedule_editor_sync).

        # Everything a frozen trace must not let the user change.  Collected
        # once, here, where a new field is added: a walk over
        # parent.winfo_children() would look tidier and would silently stop
        # covering anything that ends up inside a sub-frame (the mode radios
        # and the three Plot checkboxes both are).  The two RowTables and the
        # StylePicker are NOT in this list -- they own children of their own
        # and have a set_editable() each.
        self._ed_lockable = [
            self.ed_file_cbo, self.ed_porta, self.ed_portb, self.ed_short,
            self.ed_gnd, self.ed_label, self.ed_enabled_cb,
            self.ed_plot_self_cb, self.ed_plot_mutual_cb,
            self.ed_edit_text_btn,
        ] + list(self._ed_mode_buttons)

        parent.columnconfigure(1, weight=1)
        self._update_mode_visibility()

    def _set_editor_editable(self, editable: bool) -> None:
        """
        Grey the whole editor out for a frozen trace, and put it back.

        Belt and braces beside _sync_editor_to_trace's refusal, not a
        substitute for it: the refusal is what makes the snapshot safe, and
        this is what stops the user typing into a field that then discards
        every keystroke in silence -- which is exactly the failure auto-apply
        was built to remove.
        """
        editable = bool(editable)
        RowTable._set_state(self._ed_lockable, editable)
        self.ed_mp_table.set_editable(editable)
        self.ed_conn_table.set_editable(editable)
        self.ed_style.set_editable(editable)
        if editable:
            self.ed_frozen_note.grid_remove()
        else:
            self.ed_frozen_note.grid()


    def _on_trace_selected(self) -> None:
        # Land any queued edit on the trace it was typed into, BEFORE the
        # editor is reloaded from a different one.  Deferring the sync is what
        # makes it safe (see _schedule_editor_sync); flushing here is what
        # keeps it from landing on the wrong trace.
        self._flush_editor_sync()
        idx = self.app._sel_idx(self.app.traces_lb)
        if idx is None:
            return
        tc = self.app.traces[idx]
        self.app._migrate_trace(tc)
        self.app._suppress_editor_sync = True
        try:
            self.ed_file_var.set(tc.file_label)
            self.ed_mode_var.set(tc.mode)
            self.ed_porta.set_value(tc.port_a)
            self.ed_portb.set_value(tc.port_b)
            self.ed_short.set_value(tc.short_pairs)
            self.ed_gnd.set_value(tc.gnd_ports)
            self.ed_mp_table.set_rows(tc.mports)
            self.ed_enabled_var.set(bool(tc.enabled))
            self.ed_plot_self_var.set(bool(tc.plot_self))
            self.ed_plot_mutual_var.set(bool(tc.plot_mutual))
            self.ed_label.set_value(tc.label)
            self.ed_style.set(tc.color_idx, tc.ls_idx)
            self.ed_conn_table.set_rows(tc.conn_rows)
            self.app._ed_extra_lines = tc.extra_lines
            self._refresh_port_choices()
            # Before _update_mode_visibility, which ends by re-measuring the
            # scrollregion: the frozen note is a gridded row of the form, so it
            # changes the form's height.
            self._set_editor_editable(not tc.frozen)
            # INSIDE the guard.  _update_mode_visibility calls set_placeholder
            # on four PlaceholderEntries, and each of those writes its variable
            # -- four unguarded write traces per selection if it runs after the
            # finally.  They usually write the same value back, but
            # _sync_editor_to_trace turns an empty Label into 'trace_<id>', so a
            # sync fired from there can rename a trace nobody touched.
            self._update_mode_visibility()
        finally:
            self.app._suppress_editor_sync = False
        self._refresh_editor_strips()


    def _on_mode_changed(self) -> None:
        self._update_mode_visibility()

    def _update_mode_visibility(self) -> None:
        mode = self.ed_mode_var.get()

        def show(widget, on):
            if on:
                widget.grid()
            else:
                widget.grid_remove()

        # Modes 5 and 6 both replace the structured fields with tables: mode 6
        # is the measurement-port table alone, mode 5 is that table plus the
        # connections table and the two strips under it.
        ab_modes = mode in (1, 2, 3)
        coupling = mode == 6
        rows_mode = mode in (5, 6)
        custom = mode == 5
        show(self.ed_porta_lbl, ab_modes)
        show(self.ed_porta, ab_modes)
        show(self.ed_portb_lbl, mode in (2, 3))
        show(self.ed_portb, mode in (2, 3))
        show(self.ed_short_lbl, mode == 3)
        show(self.ed_short, mode == 3)
        show(self.ed_mp_lbl, rows_mode)
        show(self.ed_mp_table, rows_mode)
        show(self.ed_mp_hint, rows_mode)
        # No GND field in mode 5: grounding there is a connection row, and two
        # ways to say the same thing is what the table is trying to remove.
        show(self.ed_gnd_lbl, ab_modes or coupling)
        show(self.ed_gnd, ab_modes or coupling)
        # The Plot row itself is unconditional -- "this trace" is the
        # visibility switch and every mode has one.  Only the self/mutual pair
        # is mode-gated. They are shown in mode 5 too: _coupling_plot_traces
        # already READS tc.plot_self / tc.plot_mutual for any trace routed to
        # the coupling path, mode 5 included, so hiding them left a Mode 5 user
        # unable to turn either off -- and never shown the hint explaining that
        # on a mutual curve L(nH) is M and C(pF) is C_c.
        show(self.ed_plot_self_cb, rows_mode)
        show(self.ed_plot_mutual_cb, rows_mode)
        show(self.ed_mutual_hint, rows_mode)
        show(self.ed_conn_head, custom)
        show(self.ed_conn_table, custom)
        show(self.ed_conn_hint, custom)
        show(self.ed_overview, custom)
        show(self.ed_validation, custom)
        # The footer summary is pack-managed (it lives in the pinned footer,
        # not in the gridded form), so it needs its own show/hide.  Gated on
        # the same `custom` as the two strips it summarises: outside mode 5 the
        # connections table is hidden but its rows still exist, and an overview
        # built from them would count rows the running spec does not use.
        # winfo_manager() rather than a re-pack: pack() on an already-managed
        # widget keeps its slot, but asking first makes that independent of Tk.
        if custom:
            if not self.ed_footer_strip.winfo_manager():
                self.ed_footer_strip.pack(side=tk.LEFT, fill=tk.X,
                                          expand=True, padx=4)
        else:
            self.ed_footer_strip.pack_forget()

        # Update placeholders to match the active mode
        self.ed_porta.set_placeholder(
            MODE_PLACEHOLDERS["port_a"].get(mode, ""))
        self.ed_portb.set_placeholder(
            MODE_PLACEHOLDERS["port_b"].get(mode, ""))
        self.ed_short.set_placeholder(
            MODE_PLACEHOLDERS["short_pairs"].get(mode, ""))
        self.ed_gnd.set_placeholder(
            MODE_PLACEHOLDERS["gnd"].get(mode, ""))
        # Neither table needs per-mode placeholders: their hints live
        # permanently under them (MP_TABLE_HINT / CONN_TABLE_HINT), where --
        # unlike a PlaceholderEntry -- focus cannot delete them.

        # Which rows exist just changed, so the scroll region is stale. The
        # inner frame's <Configure> does NOT cover this -- see the docstring.
        #
        # preserve=False ONLY when the MODE actually moved.  That is the case
        # the reset exists for: a now-short form must not stay parked out of
        # sight, and every field the view is scrolled past has been replaced
        # anyway.  But this function also runs on every TRACE SELECTION, where
        # the mode is usually the SAME and the form is the same shape -- and
        # resetting there threw the reader back to the top of the form on every
        # click.  Measured at 1500x900 on a mode-5 trace: the form is 728 px
        # against a 345 px viewport, so the connections table is BELOW THE FOLD
        # at yview 0.  The reader scrolls to 0.35 to reach it (220 px of table
        # on screen), clicks the other trace to compare the two specs -- the
        # whole point of having two traces -- and lands back at yview 0.0 with
        # ZERO px of it visible.  Nothing was hidden and the spec still
        # computed, which is exactly how it was reported: the Connections table
        # "disappeared from the GUI" while "the calculation is still fine".
        #
        # Preserving is safe here in a way it was not when this was written:
        # _apply_editor_scrollregion re-measures the scrollregion BEFORE
        # re-applying the offset, so a shorter form clamps to its own bottom
        # instead of parking past the end.  The stale-scrollregion failure the
        # unconditional reset was guarding against cannot come back through
        # this call.
        moved = getattr(self.app, "_ed_shown_mode", None) != mode
        self.app._ed_shown_mode = mode
        self._refresh_editor_scrollregion(preserve=not moved)
        if self._strips_wanted():
            # The tables' on_change does not fire on set_rows, so the strips
            # would otherwise still show the previous mode's spec.
            self._refresh_editor_strips()

    # ------------------------------------------------- Mode 5 editor plumbing

    def _editor_nports(self) -> Optional[int]:
        """Port count of the file the editor currently points at, or None."""
        fe = self.app._file_by_label(self.ed_file_var.get())
        return fe.ts.nports if fe is not None else None

    def _refresh_port_choices(self) -> None:
        """
        Fill the Port / To dropdowns with the current file's port numbers.

        Numbers, not names: measured, a ttk Combobox's popdown is only as wide
        as the widget, so a 7-char Port cell shows '12: VDD_bal…' truncated in
        the list as well as in the cell.  A name-bearing dropdown needs ~105 px
        the editor does not have; the names stay reachable through Show Ports.

        MERGED NODES COME FIRST (R1-2).  Referring to a node by any ONE of its
        member ports already works; listing every member is the spelling that
        silently multiplies an element by N (measured by core on a 5-port
        probe network: '1,2,3 lumped_between 4 L=10f' after '1 short_to 2,3'
        is 3.333 fH where 10 fH was typed).  So the right gesture has to be
        the cheap one: the node's `ref` -- its net name, or its first member --
        sits at the TOP of the list, above the bare port numbers.
        """
        n = self._editor_nports() or 0
        values = [str(i) for i in range(1, n + 1)]
        try:
            nodes = merged_nodes(self.ed_mp_table.get_rows(),
                                 self.ed_conn_table.get_rows(),
                                 self.app._ed_extra_lines)
        except Exception:       # pragma: no cover - merged_nodes never raises
            nodes = []
        refs = [nd.ref for nd in nodes if nd.ref]
        values = refs + [v for v in values if v not in set(refs)]
        self.ed_conn_table.set_column_values("ports", values)
        self.ed_conn_table.set_column_values("to", values)

    def _strips_wanted(self) -> bool:
        """
        Is a strip refresh worth an idle pass?

        Mode 5 owns the two Labels, mode 6 needs the style preview's curve
        span -- and an OPEN Ports & Roles window needs it in every mode, since
        it is the same after_idle-coalesced pass that feeds it.  Without that
        clause the window would go stale the moment the user edited a
        mode-1 GND field, which is precisely the edit it exists to check.

        An open ATTRIBUTION window is the same clause for the same reason, and
        it is not covered by either of the first two: an attribution needs two
        measurement ports, so its trace is a mode 6 one in the normal case, and
        mode 6 alone does NOT reach here -- `_apply_editor_sync` asks this
        question before scheduling anything.  Measured without this clause: an
        open window on a mode-6 trace, edit the GND field, and the banner still
        read "from run #1 @ 5.1 GHz" with no staleness warning while the trace
        was already marked stale and [Recompute] was already answering about a
        different network.  The banner is the ONE thing that makes that button
        honest, so a banner that does not update is the whole hook not working.
        `attribution_windows` prunes dead windows and returns a list, so an
        empty one is falsey and a closed window costs nothing again.  Measured
        on this machine, per call: 0.6 us for the whole predicate with no
        window open (0.2 us of it the pruning walk) and 2.1 us with one -- the
        extra 1.5 us is a single `winfo_exists` round trip to Tcl.  What the
        clause really buys back is the strip pass itself, 137 us per keystroke
        in mode 6, which is the price of the window being right rather than
        stale and is the same price an open Ports & Roles window already pays.
        """
        return (self.ed_mode_var.get() == 5
                or self.app._port_roles_win is not None
                or bool(attribution_windows(self.app)))

    def _on_editor_file_changed(self) -> None:
        self._refresh_port_choices()
        if self._strips_wanted():
            self._refresh_editor_strips()

    def _on_editor_rows_changed(self) -> None:
        """RowTable on_change: fires on EVERY keystroke in EVERY cell."""
        if self.app._suppress_editor_sync:
            return
        self._refresh_editor_scrollregion(preserve=True)
        self._schedule_editor_sync()    # also refreshes the strips
        if self.ed_mode_var.get() == 6 or self.app._port_roles_win is not None:
            self._refresh_editor_strips()   # for the style preview's span

    def _refresh_editor_strips(self) -> None:
        """Queue a strip refresh for the next idle moment, coalescing repeats."""
        if self.app._ed_strips_pending:
            return
        self.app._ed_strips_pending = True
        self.app.after_idle(self._apply_editor_strips)

    def _apply_editor_strips(self) -> None:
        """
        Recompute the port-overview strip, the validation strip, the
        kept-as-text indicator on the Connections caption, and the style
        preview's curve-count.

        Writes to nothing but those Labels and the preview -- _sync_editor_to_trace
        stays the only writer to a TraceConfig -- and never lets an exception
        escape. This is reached from a Tcl variable trace, where a raised error
        does not reach a handler we control: Tk prints it to stderr and the GUI
        carries on showing a stale strip that says the spec is fine.
        """
        self.app._ed_strips_pending = False
        if not self.ed_overview.winfo_exists():
            return
        try:
            mports, conn, extra, nports, names, echoes = \
                self._editor_spec_inputs()
            # The merged-node entries at the top of the Port / To dropdowns
            # follow the short rows as they are typed, so they are refreshed
            # here rather than only on a file or trace change.  This writes
            # combobox CHOICES, never a cell's value -- it cannot alter the
            # spec, which is the property that keeps _sync_editor_to_trace the
            # only writer -- and set_column_values returns immediately when the
            # list has not moved, which on a keystroke is the normal case.
            self._refresh_port_choices()
            try:
                term = build_terminations_rows(mports, conn, extra,
                                               nports=nports)
            except Exception:
                term = None
            msgs = _validation_messages(mports, conn, extra, nports, names,
                                        echoes)
            self.ed_style.set_span(self._editor_curve_span(term))
            self.ed_overview.configure(
                text=_port_overview_text(term, nports))
            self.ed_validation.configure(text=_validation_strip_text(msgs))
            # The same two facts, on one line, in the pinned footer -- because
            # at the minsize the two Labels above are 366 and 387 px below the
            # fold of a 45 px viewport.
            self.ed_footer_strip.configure(
                text=_footer_strip_text(term, nports, msgs))
            self.ed_extra_lbl.configure(text=_extra_lines_indicator(extra))
            # The hint follows the Kinds in the table, the same rule
            # conn_table_layout applies to the cells and the header.  `conn` is
            # the SCOPED copy, which is fine here and only here: scoping
            # rewrites port fields and never touches `kind`.
            sel = self._selected_trace()
            self.ed_conn_hint.set_text(*conn_hint_text(
                conn, tagged=(sel is not None and trace_is_composed(sel))))
        except Exception as e:
            # Belt and braces: _validation_messages already swallows its own
            # errors, but this is the last frame before Tcl and nothing beyond
            # it can report a failure.
            self.ed_overview.configure(text="")
            self.ed_validation.configure(text=f"⚠ {e}")
            self.ed_footer_strip.configure(text="⚠ 1 problem")
        # Outside the try above, and with a swallow of its own: a window that
        # fails to render must not blank the strips, and a strip failure must
        # not leave the window showing the previous spec.
        self.app._refresh_port_roles_window()
        # The Attribution windows get the STALENESS BANNER and nothing else,
        # and that restraint is the point rather than an optimisation.  tc.Zmat
        # is written only by _on_calculate; editing the spec sets tc.stale and
        # leaves the numbers at the PREVIOUS run's.  A window that recomputed
        # from here would, on the first keystroke, decompose the new spec and
        # reconcile it against the old authoritative total -- a residual not of
        # 1e-13 but of however much the edit changed -- and then withhold its
        # own table, i.e. a window that erases itself while you type.  What the
        # banner does instead is make [Recompute] honest.
        #
        # `refresh_attribution_windows` defaults to rerender=False for exactly
        # that reason; the units re-render is the one caller that passes True
        # (see _on_units_mode_changed).  Measured with one window open:
        # 28.6 us for the banner refresh against 6049 us for the rerender=True
        # form, which redraws the tables and the sweep -- a 200x difference,
        # and the reason the default is the cheap one on a path that fires from
        # a Tk variable trace.  It never raises, same contract as this
        # function.  `_strips_wanted` is what gets us here at all in mode 6;
        # see the note there.
        refresh_attribution_windows(self.app)
        # The file windows get the whole picture rather than a banner, because
        # unlike an Attribution table nothing in them is a computed NUMBER:
        # the alias legend, the port counts and the spec problems are all
        # properties of the spec being typed, so they must follow it.  Same
        # contract -- `refresh_files_windows` never raises -- and it is outside
        # the try above for the same reason the Ports & Roles refresh is: a
        # window failure must not blank the strips.
        refresh_files_windows(self.app)

    def _editor_spec_inputs(self) -> tuple:
        """
        Everything the validation pass needs, read off the LIVE editor.

        One reader, because the strips and the footer's route (R1-4) must
        answer about the same spec: a route computed from a cached message list
        would send the user to a row number from before their last keystroke.

        On a COMPOSED trace the rows are first resolved into the composed
        namespace, so a `F2.13` cell is validated as the port it names instead
        of being reported as a port field that does not parse.  The namespace
        costs a list comprehension over the file list (`_namespace_network`) --
        stacking the real thing here is measured at up to 10.5 s per keystroke
        and is what Calculate pays once.

        It NEVER RAISES: this is on the strips' path, and a bad tag is a
        message for the strip, not an exception.  A field that cannot be
        scoped is left exactly as typed and the ordinary validation reports it.

        The sixth item is the SCOPE ECHO, and it is built from the rows BEFORE
        they are scoped -- scoping rewrites a tagged field to a global index
        and the tag is gone by then, so it is the only point where both
        spellings exist at once.
        """
        mports = self.ed_mp_table.get_rows()
        conn = self.ed_conn_table.get_rows()
        extra = self.app._ed_extra_lines
        nports = self._editor_nports()
        names = self._editor_port_names()
        echoes: list[tuple] = []
        tc = self._selected_trace()
        if tc is not None and trace_is_composed(tc):
            net, home = self.app._trace_namespace(tc)
            if net is not None:
                nports, names = net.nports, net.port_labels()
                echoes = scope_echo_messages(mports, conn, extra, net, home)
                try:
                    mports = _scope_mport_rows(mports, net, home)
                    conn = _scope_conn_rows(conn, net, home)
                    extra = _scope_dsl_text(extra, net, home)
                except Exception:
                    pass
        return (mports, conn, extra, nports, names, echoes)

    def _selected_trace(self) -> Optional[TraceConfig]:
        """The trace the editor is showing, or None."""
        idx = self.app._sel_idx(self.app.traces_lb)
        return (self.app.traces[idx]
                if idx is not None and idx < len(self.app.traces) else None)

    def _on_footer_route(self, _event=None) -> None:
        """
        Click the footer verdict -> scroll to the row it is about (R1-4).

        It follows the FIRST message's anchor and no other.  The list is
        ordered by consequence (see V_WRONG_NUMBER and friends), so scanning
        down for one that happens to have a row would take the reader to a
        row belonging to a LOWER-priority message than the one the footer is
        counting -- a route that quietly answers a different question.  When
        the top message is about the spec rather than a row ("no measurement
        port defined", or the builder's own error) the fallback is the
        validation strip, which is where the full text is written.

        Never raises: this is a Tk binding, and an exception here reaches no
        handler we control.  Same contract as _apply_editor_strips.
        """
        try:
            mports, conn, extra, nports, names, echoes = \
                self._editor_spec_inputs()
            report = _validation_report(mports, conn, extra, nports, names,
                                        echoes)
            target = None
            anchor = report[0].anchor if report else None
            if anchor is not None:
                kind, idx = anchor
                table = (self.ed_conn_table if kind == "conn"
                         else self.ed_mp_table)
                # 'ports' / 'plus' is the cell worth putting a caret in; the
                # Type combo is first in the row and is not what is wrong.
                target = table.data_row_widget(
                    idx, "ports" if kind == "conn" else "plus")
            if target is None:
                self._scroll_editor_to(self.ed_validation)
                return
            # The table scrolls FIRST -- a row past max_visible is clipped by
            # the table's own canvas, and the editor canvas cannot reach it.
            inside = table.see_row(target)
            self._scroll_editor_to(
                target,
                top=inside + (table.winfo_rooty()
                              - self._ed_form.winfo_rooty()))
            try:
                target.focus_set()
            except Exception:                       # pragma: no cover
                pass
        except Exception:                           # pragma: no cover
            pass

    def _scroll_editor_to(self, widget, top: Optional[int] = None) -> None:
        """
        Bring a widget inside the editor form on screen.

        `top` overrides the measured y for a widget that is about to move --
        see RowTable.see_row, which returns where its row lands rather than
        forcing an idle pass to re-measure it.
        """
        form = self._ed_form
        if not (widget.winfo_exists() and form.winfo_exists()):
            return
        if top is None:
            top = widget.winfo_rooty() - form.winfo_rooty()
        self._ed_canvas.yview_moveto(editor_scroll_fraction(
            top, widget.winfo_height(), self._ed_canvas.winfo_height(),
            form.winfo_height()))

    def _editor_port_names(self) -> Optional[Sequence[str]]:
        """Port names of the file the editor points at, or None."""
        fe = self.app._file_by_label(self.ed_file_var.get())
        return fe.ts.port_names if fe is not None else None

    def _editor_curve_span(self, term) -> int:
        """
        How many consecutive palette slots the trace being edited will use.

        A coupling trace with G measurement ports draws G self curves and
        G*(G-1)/2 mutual ones, each taking the NEXT colour
        (_coupling_plot_traces), so the style preview would otherwise show one
        line for something that arrives as six. Returns 1 whenever that cannot
        be answered -- an unresolvable spec is not the preview's problem.
        """
        mode = self.ed_mode_var.get()
        if mode == 6:
            # Mode 6 is one measurement port per table row, so count the rows.
            # NOT through build_terminations_rows: that goes via the DSL, where
            # 'b' is the legacy alias for the minus side of 'A', so two rows
            # named a/b resolve to ONE measurement port and the preview would
            # show a span of 1 for a trace Calculate refuses outright.
            G = len([r for r in self.ed_mp_table.get_rows()
                     if r.plus.strip() or r.minus.strip()])
        elif mode == 5 and term is not None:
            try:
                G = len(resolve_meas_ports(term, self._editor_nports() or 0))
            except Exception:
                return 1
        else:
            return 1
        n = (G if self.ed_plot_self_var.get() else 0)
        if self.ed_plot_mutual_var.get() and G >= 2:
            n += G * (G - 1) // 2
        return max(1, n)

    def _editor_dsl_text(self) -> str:
        """
        The DSL the two tables currently serialise to -- what the text dialog
        shows.  Built from the LIVE tables, not from the selected trace, so an
        edit that has not been applied yet is visible there.
        """
        return rows_to_dsl_text(self.ed_mp_table.get_rows(),
                                self.ed_conn_table.get_rows(),
                                self.app._ed_extra_lines)

    def _on_edit_as_text(self) -> None:
        """
        The escape hatch: show the DSL the tables serialise to, and take it back.

        MODAL on purpose.  A non-modal editing surface is not part of the editor
        form, so Calculate's auto-sync would push the tables and silently ignore
        whatever is typed in the dialog -- which is the exact opposite of what
        the button promises.
        """
        try:
            initial = self._editor_dsl_text()
        except Exception as e:
            # rows_to_dsl_text refuses a cell it cannot serialise (an R/L/C
            # value with a space in it).  There is no text to show, and letting
            # this escape would be an unhandled Tk traceback.
            messagebox.showerror(
                "Cannot show the text",
                f"{e}\n\nFix the cell in the connections table first; the "
                "validation strip names it.")
            return

        dlg = tk.Toplevel(self.app)
        dlg.title("Edit as text")
        dlg.transient(self.app)

        ttk.Label(dlg, text=TEXT_DIALOG_NOTE, wraplength=560, justify=tk.LEFT,
                  foreground=PLACEHOLDER_FG).pack(side=tk.TOP, anchor="w",
                                                  padx=8, pady=(8, 4))

        def _ok() -> None:
            text = box.get("1.0", "end-1c")
            dlg.destroy()
            self._import_text_into_tables(text)

        # THE FOOTER IS PACKED FIRST, side=BOTTOM.  pack unmaps what does not
        # fit starting from the END, so packing it after the expand=True Text
        # made both buttons winfo_ismapped() == 0 the moment the dialog was
        # dragged shorter than its natural height -- and with grab_set() and no
        # keyboard commit there was then no way to apply the edit at all.
        # Same rule as Global Controls and the editor footer in the main window.
        foot = ttk.Frame(dlg)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Cancel", command=dlg.destroy
                   ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(foot, text="OK", command=_ok).pack(side=tk.RIGHT, padx=2)

        # Plain tk.Text, not PlaceholderText: a placeholder here would be a
        # get_value() trap, and the hint above is a Label that focus cannot
        # delete.  It gets a scrollbar because a spec long enough to want this
        # dialog is long enough to overflow it.
        wrap = ttk.Frame(dlg)
        wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        vsb = ttk.Scrollbar(wrap, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        box = tk.Text(wrap, width=80, height=24, font=("Consolas", 9),
                      yscrollcommand=vsb.set)
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=box.yview)
        box.insert("1.0", initial)

        # Escape cancels.  Return is deliberately NOT bound: this is a
        # multi-line editor and Return has to insert a newline.
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.grab_set()
        box.focus_set()
        self.app.wait_window(dlg)

    def _import_text_into_tables(self, text: str) -> None:
        """
        OK on the text dialog: import the text and re-render both tables
        IMMEDIATELY, so the user sees the canonical rewrite rather than
        discovering it later.
        """
        mports, conn, extra, changed = _import_dsl_text(text)
        if changed:
            self.ed_mp_table.set_rows([])
            self.ed_conn_table.set_rows([])
            self.app._ed_extra_lines = text.rstrip()
            messagebox.showwarning(
                "Kept as text",
                "This spec is kept verbatim instead of being moved into the "
                "table.\n\nThe table emits every measurement port before every "
                "connection, which is what makes a later 'ground' win. Your "
                "spec depends on the order it is written in, so importing it "
                "would change which port wins:\n\n"
                + (_ordering_diff_summary(text) or "(the resolved spec differs)")
                + "\n\nIt still computes exactly what it computed before.")
        else:
            self.ed_mp_table.set_rows(mports)
            self.ed_conn_table.set_rows(conn)
            self.app._ed_extra_lines = extra
        self._refresh_port_choices()
        self._refresh_editor_strips()
        self._refresh_editor_scrollregion(preserve=True)

    # ------------------------------------------------------------- auto-apply
    #
    # The editor writes itself into the selected trace as you type; there is no
    # "Apply" step any more.  Three properties make that safe, and none of them
    # is optional:
    #
    #  1. It is DEFERRED to after_idle, never run straight from the variable
    #     trace.  PlaceholderEntry._show_if_empty() sets the variable BEFORE it
    #     sets _showing (see that method), and Tcl runs write traces
    #     synchronously inside .set() -- so a synchronous handler reads
    #     get_value() while the flag still says "not showing" and stores the
    #     grey hint text as if the user had typed it.  By the idle pass the flag
    #     is right.  This is the reason for the deferral, not performance.
    #  2. It captures the TraceConfig OBJECT, not the Listbox index, and any
    #     pending sync is FLUSHED before the selection changes.  Resolving the
    #     target when the callback finally runs would let "type in A, click B"
    #     write B's freshly loaded editor content into A -- silently losing the
    #     edit that scheduled the callback, which is the exact failure this
    #     whole feature exists to remove.
    #  3. It never raises and never opens a dialog.  It runs from a Tcl
    #     variable trace, where an exception reaches no handler we control (Tk
    #     prints it and carries on), and a modal messagebox raised while the
    #     user is typing is a hang.  Calculate keeps its error dialog.

    def _schedule_editor_sync(self, *_args) -> None:
        """Queue a push of the editor into the selected trace."""
        if self.app._suppress_editor_sync:
            return
        idx = self.app._sel_idx(self.app.traces_lb)
        if idx is None or idx >= len(self.app.traces):
            return
        self.app._ed_sync_target = self.app.traces[idx]
        if self.app._ed_sync_after is None:
            self.app._ed_sync_after = self.app.after_idle(self._apply_editor_sync)

    def _flush_editor_sync(self) -> None:
        """Run any queued sync NOW, against the trace it was queued for."""
        if self.app._ed_sync_after is None:
            return
        try:
            self.app.after_cancel(self.app._ed_sync_after)
        except Exception:
            pass
        self.app._ed_sync_after = None
        self._apply_editor_sync()

    def _cancel_editor_sync(self) -> None:
        """
        Drop a queued sync WITHOUT running it.

        Only correct when the target trace is being discarded outright (loading
        a session replaces the whole trace list).  Everywhere else the queued
        edit is the user's most recent keystroke and must land -- use
        _flush_editor_sync.
        """
        if self.app._ed_sync_after is not None:
            try:
                self.app.after_cancel(self.app._ed_sync_after)
            except Exception:
                pass
        self.app._ed_sync_after = None
        self.app._ed_sync_target = None

    def _apply_editor_sync(self) -> None:
        self.app._ed_sync_after = None
        tc = self.app._ed_sync_target
        self.app._ed_sync_target = None
        # A trace deleted between scheduling and running is not an error.
        # Identity, not `in`: TraceConfig is an eq=True dataclass holding numpy
        # arrays, so `tc not in self.app.traces` compares field by field and raises
        # "truth value of an array is ambiguous" the moment it reaches a Z that
        # is not the same object.
        if tc is None or not any(t is tc for t in self.app.traces):
            return
        before_spec = self.app._config_signature(tc)
        before_draw = self.app._draw_signature(tc)
        try:
            self._sync_editor_to_trace(tc)
        except Exception:
            return          # see (3) above -- Calculate will report it properly
        if self.app._config_signature(tc) != before_spec and tc.Z is not None:
            # The curve on screen is older than the spec that now describes it.
            tc.stale = True
        self.app._refresh_trace_list()
        if self.app._draw_signature(tc) != before_draw:
            self.app._replot_from_cache()
        if self._strips_wanted():
            self._refresh_editor_strips()

    def _on_style_changed(self) -> None:
        self._schedule_editor_sync()

    def _on_enabled_toggled(self) -> None:
        self._schedule_editor_sync()

    def _sync_editor_to_trace(self, tc: TraceConfig) -> None:
        # A FROZEN trace is a snapshot, so the editor may not write into it at
        # all -- its numbers and the spec printed beside them have to keep
        # describing each other.  The guard lives here, not at the call sites:
        # there are four of them (the deferred sync, the flush, and both of
        # Calculate's), and the one that forgot would silently relabel or
        # re-port a snapshot with whatever the editor happened to be showing.
        if tc.frozen:
            return
        tc.enabled = bool(self.ed_enabled_var.get())
        tc.color_idx, tc.ls_idx = self.ed_style.get()
        tc.file_label = self.ed_file_var.get()
        tc.mode = int(self.ed_mode_var.get())
        tc.port_a = self.ed_porta.get_value()
        tc.port_b = self.ed_portb.get_value()
        tc.short_pairs = self.ed_short.get_value()
        tc.gnd_ports = self.ed_gnd.get_value()
        tc.mports = self.ed_mp_table.get_rows()
        tc.plot_self = bool(self.ed_plot_self_var.get())
        tc.plot_mutual = bool(self.ed_plot_mutual_var.get())
        # custom_text is deliberately NOT written: the two tables are the
        # storage and the DSL text is derived from them.  Writing both would
        # leave migrate_legacy_custom_text unable to tell a legacy trace from a
        # freshly synced one, and the text would overwrite the rows on the next
        # selection.
        tc.conn_rows = self.ed_conn_table.get_rows()
        tc.extra_lines = self.app._ed_extra_lines
        tc.label = self.ed_label.get_value() or f"trace_{tc.id}"

