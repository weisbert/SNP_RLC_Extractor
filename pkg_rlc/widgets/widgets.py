"""
pkg_rlc_widgets.py  --  the generic Tk widgets, with no knowledge of this app.

Split out of pkg_rlc_gui.py (and, for `ReflowRow`, out of pkg_rlc_plot.py),
verbatim.  Nothing here knows what a trace, a file or a termination is: a
`RowTable` is told its columns and its row factory, a `PlaceholderEntry` is
told its hint, a `ReflowRow` is handed widgets to lay out.  That is what makes
them testable on their own and reusable by any panel.

Two app-side names are imported, both from pkg_rlc_conntable (L3, i.e. BELOW
this module -- see tests/test_layering.py):

  * `ColumnSpec` / `TableLayout` / `identity_layout`, the vocabulary a
    `RowTable` lays itself out with.  They live at the lower layer because the
    per-table layout rules that produce them do, and a shared type has to sit
    below both of its users.
  * `CONN_ON_GLYPH` / `CONN_OFF_GLYPH` / `HINT_SHORT_CHARS`, the width-stable
    toggle pair and the collapsed-hint budget -- both measured numbers that
    belong beside the table they were measured against.

`StylePicker` deliberately did NOT come here.  It draws from `COLORS` /
`LINESTYLES`, which live in pkg_rlc_plot, and pkg_rlc_plot imports `ReflowRow`
from this module -- so importing them back would be a module-level cycle.  It
stays in pkg_rlc_gui until those two palettes have a home below both.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

import tkinter as tk
from tkinter import ttk

from pkg_rlc.present.conntable import (
    CONN_OFF_GLYPH,
    CONN_ON_GLYPH,
    ColumnSpec,
    HINT_SHORT_CHARS,
    TableLayout,
    identity_layout,
)
# The role names PORT_ROLE_FG is keyed on.  `port_roles` in pkg_rlc_core is the
# ONE classifier; a second spelling of the keys here is how a bucket comes to
# have no colour and be painted the default one in silence.
from pkg_rlc.physics.core import (
    ROLE_ELEMENT,
    ROLE_GROUND,
    ROLE_OPEN,
    ROLE_PROBE_MINUS,
    ROLE_PROBE_PLUS,
    ROLE_SHORTED,
    ROLE_VDD,
)


# ============================================================================
# The palette
# ============================================================================
#
# ONE palette for the application, in ONE module.  These three used to be split
# between here and `pkg_rlc_gui`, which meant a panel that wanted the warning
# colour had to reach UP into the frontend for it -- three of the ten
# function-level `import pkg_rlc_gui` dodges were nothing but a colour lookup.
# They are colours and a ttk style helper, not data, so they did not go down to
# `pkg_rlc_model` with the trace: `tests/test_layering.py`'s own advice is that
# colour constants belong at L3/L4, and this is the L4 module every panel that
# paints already imports.

PLACEHOLDER_FG = "#888888"

# The colour a flagged row takes in the Ports & Roles window. Same #b04000 as
# the frozen-trace note and the results pane's "flag" tag -- one warning colour
# in the application, not three.
WARN_FG = "#b04000"

# One colour per bucket, so a 153-row list can be skimmed rather than read.
PORT_ROLE_FG = {
    ROLE_PROBE_PLUS: "#1f5fb4",
    ROLE_PROBE_MINUS: "#1f5fb4",
    ROLE_GROUND: "#207020",
    ROLE_VDD: "#207020",
    ROLE_ELEMENT: "#7030a0",
    ROLE_SHORTED: "#a06000",
    ROLE_OPEN: "#808080",
}


def _fixed_map_filter(entries: Sequence) -> list:
    """
    Drop the ('!disabled', '!selected') state specs from a ttk style map.

    This is the standard workaround for the Tk bug that makes a Treeview ignore
    tag colours: those two negated states match every ordinary row, so the
    style map wins over the tag and every row is painted the default colour.
    Pure, so the rule itself is testable without a display.
    """
    return [e for e in entries if tuple(e[:2]) != ("!disabled", "!selected")]


# ============================================================================
# Placeholder-text helpers
# ============================================================================

class PlaceholderEntry(ttk.Entry):
    """ttk.Entry that shows greyed-out hint text when empty and unfocused."""

    def __init__(self, master, placeholder: str = "", width: int = 42, **kwargs):
        kwargs.pop("textvariable", None)
        self._var = tk.StringVar()
        super().__init__(master, textvariable=self._var, width=width, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._show_if_empty()

    def _set_fg(self, color: str) -> None:
        try:
            self.configure(foreground=color)
        except tk.TclError:
            pass  # some themes may not honor this

    def _show_if_empty(self) -> None:
        if not self._var.get():
            self._var.set(self._placeholder)
            self._set_fg(PLACEHOLDER_FG)
            self._showing = True

    def _on_focus_in(self, _event=None) -> None:
        if self._showing:
            self._var.set("")
            self._set_fg("")
            self._showing = False

    def _on_focus_out(self, _event=None) -> None:
        self._show_if_empty()

    def get_value(self) -> str:
        return "" if self._showing else self._var.get().strip()

    def set_value(self, s: str) -> None:
        if s:
            self._var.set(s)
            self._set_fg("")
            self._showing = False
        else:
            self._var.set("")
            self._showing = False
            self._show_if_empty()

    def set_placeholder(self, placeholder: str) -> None:
        self._placeholder = placeholder
        if self._showing:
            self._var.set(placeholder)
        elif not self._var.get():
            self._show_if_empty()

    def on_change(self, callback) -> None:
        """
        Call `callback` whenever the contents change.

        Deliberately a method and not a constructor argument: __init__ ends
        with _show_if_empty(), which SETS the variable, so a callback attached
        during construction fires once before the caller has finished building
        itself.  Registering afterwards makes that impossible rather than
        merely unlikely.

        The callback still sees the placeholder being written and erased -- it
        must read get_value(), never _var.get(), and must not act synchronously
        on what it reads (_show_if_empty writes the variable BEFORE it sets
        _showing, so a synchronous reader sees the hint as user input).
        """
        self._var.trace_add("write", lambda *_a: callback())


class PlaceholderText(tk.Text):
    """tk.Text that shows greyed-out hint when empty and unfocused."""

    def __init__(self, master, placeholder: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self._fg_normal = self.cget("foreground") or "black"
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self._show_if_empty()

    def _is_empty(self) -> bool:
        return not self.get("1.0", "end-1c").strip()

    def _show_if_empty(self) -> None:
        if self._is_empty():
            self.delete("1.0", "end")
            self.insert("1.0", self._placeholder)
            self.configure(foreground=PLACEHOLDER_FG)
            self._showing = True

    def _on_focus_in(self, _event=None) -> None:
        if self._showing:
            self.delete("1.0", "end")
            self.configure(foreground=self._fg_normal)
            self._showing = False

    def _on_focus_out(self, _event=None) -> None:
        self._show_if_empty()

    def get_value(self) -> str:
        return "" if self._showing else self.get("1.0", "end-1c")

    def set_value(self, s: str) -> None:
        self.delete("1.0", "end")
        if s:
            self.insert("1.0", s)
            self.configure(foreground=self._fg_normal)
            self._showing = False
        else:
            self._showing = False
            self._show_if_empty()

    def set_placeholder(self, placeholder: str) -> None:
        if self._showing:
            self.delete("1.0", "end")
            self._placeholder = placeholder
            self.insert("1.0", placeholder)
        else:
            self._placeholder = placeholder
            if self._is_empty():
                self._show_if_empty()


def _tk_dash(mpl_ls: str, width: int = 1):
    """
    A matplotlib linestyle as a Tk canvas dash pattern.

    Approximate on purpose: Tk's Win32 dash rendering is not matplotlib's, and
    the preview only has to make the four styles TELL APART, not match pixel
    for pixel.  Scaled by line width because Tk multiplies dash segments by it.
    """
    w = max(1, int(width))
    return {"-": (), "--": (6 * w, 3 * w), "-.": (6 * w, 2 * w, 1, 2 * w),
            ":": (1, 3 * w)}.get(mpl_ls, ())


def editor_scroll_fraction(top: int, height: int, view: int, total: int,
                           margin: int = 6) -> float:
    """
    The canvas yview fraction that brings a form widget on screen (R1-4).

    `top` is the widget's y INSIDE the form, `total` the form's height, `view`
    the canvas viewport's.  Pure, so the arithmetic can be pinned without a
    display -- and the arithmetic is the whole of it: at the 1040x600 minsize
    the mode-5 form is 516 px against a 45 px viewport, so a scroll that is a
    few pixels out puts the target off screen just as thoroughly as no scroll.

    A widget already fully visible is left where it is (returning its own top
    would jerk the view for nothing).  Otherwise it goes to the top of the
    viewport, less a small margin so it does not sit flush against the edge.
    """
    if total <= 0 or view <= 0 or total <= view:
        return 0.0
    want = max(0.0, min(float(top - margin), float(total - view)))
    return want / float(total)


# ============================================================================
# RowTable -- scrollable table of editable rows with a '+' button
# ============================================================================
#
# The replacement for the free-text boxes in Mode 5 and Mode 6.  Deliberately
# built from a Canvas plus a grid of real widgets rather than ttk.Treeview:
# Treeview has no cell editors, so it means floating Entry/Combobox widgets
# over cells and hand-managing placement, tab order and scroll offset, and the
# overlays misalign under Win11 DPI scaling.  A grid of real widgets is less
# code and behaves correctly.


class RowTable(ttk.Frame):
    """
    A '+ Add' button over a scrollable grid of editable rows.

    get_rows() / set_rows() speak lists of the dataclass `row_factory` makes,
    so the caller never touches a widget.  Blank trailing rows are kept in the
    UI (somewhere to type) but dropped by get_rows() via the row's own
    is_blank(), which is what lets the table start with an empty row without
    that empty row meaning anything.
    """

    def __init__(self, master, columns: Sequence[ColumnSpec], row_factory,
                 on_change=None, min_rows: int = 1, max_visible: int = 6,
                 add_text: str = "+ Add", layout_fn=None,
                 to_cells=None, from_cells=None, **kwargs):
        super().__init__(master, **kwargs)
        self._columns = list(columns)
        self._row_factory = row_factory
        self._on_change = on_change
        # layout_fn(values_per_row) -> TableLayout.  None keeps the historical
        # fixed grid (see identity_layout), which is what the measurement-port
        # table still uses.
        self._layout_fn = layout_fn
        # to_cells(row) -> {key: text} and from_cells({key: text}) -> row.
        # The pair exists so a row's STORAGE and its CELLS can differ: the
        # connections table shows a short's tied group in one cell while
        # ConnectionRow still stores it as ports + to, which is what keeps
        # rows_to_dsl_text and every saved session untouched.
        self._to_cells = to_cells
        self._from_cells = from_cells
        self._min_rows = max(0, int(min_rows))
        self._max_visible = max(1, int(max_visible))
        self._rows: list[dict] = []      # per row: {key: tk.StringVar} + widgets
        self._resize_pending = False
        self._editable = True
        self._layout: Optional[TableLayout] = None
        self._bulk = False               # suspend layout during set_rows

        # --- add button (outside the scroll area) ---
        head = ttk.Frame(self)
        head.pack(side=tk.TOP, fill=tk.X)
        self._add_btn = ttk.Button(head, text=add_text, width=8,
                                   command=self.add_row)
        self._add_btn.pack(side=tk.RIGHT, padx=1)

        # --- scrollable body ---
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(body, highlightthickness=0, height=1)
        self._vsb = ttk.Scrollbar(body, orient="vertical",
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self._inner,
                                                  anchor="nw")

        # Both bindings are needed: the inner frame drives the scrollregion,
        # the canvas drives the inner frame's width (without it the columns
        # do not stretch).
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # Wheel handling is NOT bound here.  The App installs one pointer-based
        # router and calls register_wheel() below; a per-widget bind_all /
        # unbind_all pair does not compose once there is more than one
        # scrollable region (unbind_all drops every binding on the `all` tag).

        # Column headers live in the SAME grid as the cells (grid row 0, cells
        # from row 1), so they line up exactly.  A separate header frame packed
        # above cannot: its labels measure in characters of a different font
        # than the Entry widgets below, and the last title ends up clipped.
        #
        # They are created on demand and PLACED by _apply_layout, because a
        # layout may retitle or blank one: a shared header states a column's
        # meaning once, so on a table whose rows have different shapes the
        # title has to follow what the rows actually put there.  There is one
        # per GRID column, which is not the same count as len(columns) -- the
        # connections table's Net cell shares a grid column with To.
        self._header_lbls: list = []

        self._bulk = True
        for _ in range(self._min_rows):
            self.add_row(notify=False)
        self._bulk = False
        self._apply_layout(force=True)

    # ------------------------------------------------------------------ scroll

    def _on_inner_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        self._schedule_resize()

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _schedule_resize(self) -> None:
        """
        Queue _resize_to_content for the next idle moment, coalescing repeats.

        Never call update_idletasks() here.  It flushes geometry for the WHOLE
        application, and this widget is built while the rest of the window still
        is: one such flush during construction froze the Results pane's
        PanedWindow sash at 2px and made the pane vanish.  after_idle runs after
        Tk's own geometry pass, so reqheight is valid without forcing anything.
        """
        if self._resize_pending:
            return
        self._resize_pending = True
        self.after_idle(self._resize_to_content)

    def _resize_to_content(self) -> None:
        """Grow with the rows up to max_visible, then show the scrollbar."""
        self._resize_pending = False
        if not self.winfo_exists():
            return
        # A frame in create_window contributes NOTHING to the canvas's requested
        # size, so without this the canvas keeps Tk's default 378px forever and
        # a 6-column table is silently squashed with no way to reach the rest.
        self._canvas.configure(width=self._inner.winfo_reqwidth())
        total = max(1, self._inner.winfo_reqheight())
        n = len(self._rows)
        if n > self._max_visible:
            per = total / (n + 1)          # +1 for the header row
            self._canvas.configure(height=int(per * (self._max_visible + 1)))
            self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            self._canvas.configure(height=total)
            self._vsb.pack_forget()

    def register_wheel(self, register) -> None:
        """Hand the App's router this table's canvas and handler."""
        register(self._canvas, self._on_wheel)

    def _on_wheel(self, event) -> bool:
        """Scroll this table. Returns False when it fits, so the event bubbles."""
        first, last = self._canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self._canvas.yview_scroll(int(-event.delta / 120), "units")
        return True

    # ------------------------------------------------------------------- rows

    def add_row(self, values: dict | None = None, notify: bool = True) -> None:
        entry: dict = {"_vars": {}, "_widgets": []}
        for col in self._columns:
            var = tk.StringVar(value=(values or {}).get(col.key, col.default))
            entry["_vars"][col.key] = var
            if col.kind == "combo":
                w = ttk.Combobox(
                    self._inner, textvariable=var, width=col.width,
                    values=list(col.values),
                    state="readonly" if col.readonly_combo else "normal")
            elif col.kind == "static":
                w = ttk.Label(self._inner, textvariable=var, width=col.width,
                              anchor="w")
            elif col.kind == "toggle":
                # tk.Label, not ttk: ttk.Label's default padding is 4 px and
                # cannot be removed without a derived style, which would then
                # reach every Label using it.  padx/pady/bd all 0 makes the
                # cell exactly the glyph -- 12 px, measured, against the 13 px
                # of table headroom this column had to fit into.
                w = tk.Label(self._inner, textvariable=var, padx=0, pady=0,
                             bd=0, cursor="hand2")
                w.bind("<Button-1>", lambda _e, v=var: self._toggle_cell(v))
            else:
                w = ttk.Entry(self._inner, textvariable=var, width=col.width)
            # NOT gridded here: _apply_layout owns every cell's grid column,
            # its columnspan and whether it is shown at all.
            entry["_widgets"].append(w)
            var.trace_add("write", self._on_cell_write)
        # width=1, not 2.  Measured: the button asks 24 px at width=2 and 17 px
        # at width=1, while '✕' itself is 12 px -- so width=1 still shows the
        # whole glyph and gives 7 px back to a table budget with single digits
        # left in it (the connections table is 431 px of viewport, and the
        # mode-6 measurement-port table already overhangs it).
        btn = ttk.Button(self._inner, text="✕", width=1,
                         command=lambda: self._delete_row(entry))
        entry["_widgets"].append(btn)
        self._rows.append(entry)
        if not self._editable:
            # set_rows() runs before the editor decides whether the trace it is
            # loading is frozen, so a row created now has to inherit the state
            # rather than come back live under a greyed-out table.
            self._set_state(entry["_widgets"], False)
        if not self._bulk:
            self._apply_layout(force=True)
            self._schedule_resize()
        if notify and self._on_change is not None:
            self._on_change()

    def _toggle_cell(self, var: tk.StringVar) -> None:
        """
        Flip a kind='toggle' cell.

        Writing the variable is the whole mechanism: `_on_cell_write` already
        re-derives the layout and calls the owner's `on_change`, so a click
        here reaches auto-apply, the strips and the stale marker by exactly the
        route a keystroke in any other cell does.  Refuses while the table is
        not editable -- a frozen trace's rows must not move, and unlike the ttk
        widgets a tk.Label has no state flag to grey out (the StylePicker
        precedent: guard the handler, because ttk state does not cascade).
        """
        if not self._editable:
            return
        var.set(CONN_OFF_GLYPH if var.get() == CONN_ON_GLYPH
                else CONN_ON_GLYPH)

    def _on_cell_write(self, *_a) -> None:
        """
        A cell's variable changed.

        The layout is re-derived FIRST and re-applied only if it actually moved
        (TableLayout is a frozen tuple-of-tuples, so `==` settles it), then the
        owner's on_change runs.  Doing it the other way round would let the
        owner read the table through get_rows() while the widgets still show
        the previous kind's shape.
        """
        self._apply_layout()
        if self._on_change is not None:
            self._on_change()

    def _delete_row(self, entry: dict) -> None:
        if entry not in self._rows:
            return
        for w in entry["_widgets"]:
            w.destroy()
        self._rows.remove(entry)
        if len(self._rows) < self._min_rows:
            self.add_row(notify=False)
        self._apply_layout(force=True)
        self._schedule_resize()
        if self._on_change is not None:
            self._on_change()

    # --------------------------------------------------------------- layout

    def _cell_values(self) -> list:
        """Every row's cells as plain text -- the layout function's only input."""
        return [{col.key: entry["_vars"][col.key].get()
                 for col in self._columns} for entry in self._rows]

    def _compute_layout(self) -> TableLayout:
        vals = self._cell_values()
        if self._layout_fn is None:
            return identity_layout(self._columns, vals)
        return self._layout_fn(vals)

    def _apply_layout(self, force: bool = False) -> None:
        """
        Re-grid the headers and every cell from the current TableLayout.

        `force` is for structural changes (a row added or deleted), where the
        layout can be VALUE-identical and still have to be re-applied because
        the widgets are new or their grid rows have shifted.  Everything else
        goes through the equality check: this runs from a variable trace on
        every keystroke, and re-gridding 7 widgets per row per character is
        both wasted work and a visible flicker.

        Measured on a six-row connections table, per variable write: 31 us for
        a keystroke that does not move the layout (15.6 us of it deriving the
        layout to find that out) against 263 us for a Kind change, which
        re-grids every cell of every row.  The equality check is what keeps
        the common case off the second number.

        It cannot oscillate: the inputs are the cells' TEXT and the outputs
        are grid options, so nothing it writes can change what it reads.  That
        is the same fixed-point property _apply_editor_scrollbars needs and
        for the same reason -- a layout rule that reads a size it can itself
        change flips forever and update() never returns.
        """
        layout = self._compute_layout()
        if not force and layout == self._layout:
            return
        self._layout = layout
        while len(self._header_lbls) < layout.ncols:
            self._header_lbls.append(
                ttk.Label(self._inner, text="", anchor="w",
                          font=("TkDefaultFont", 8)))
        for c, lbl in enumerate(self._header_lbls):
            if c < layout.ncols:
                lbl.configure(text=(layout.headers[c]
                                    if c < len(layout.headers) else ""))
                lbl.grid(row=0, column=c, sticky="w", padx=1)
            else:
                lbl.grid_remove()
        for c in range(max(layout.ncols, len(self._header_lbls)) + 1):
            weight = (layout.weights[c] if c < len(layout.weights) else 0)
            self._inner.columnconfigure(c, weight=weight)
        key_index = {col.key: i for i, col in enumerate(self._columns)}
        for r, entry in enumerate(self._rows, start=1):  # 0 is the header row
            cells = (layout.rows[r - 1] if r - 1 < len(layout.rows) else ())
            shown = {key for key, _c, _s in cells}
            for key, col, span in cells:
                idx = key_index.get(key)
                if idx is None:
                    continue
                # A toggle cell is sized to its glyph and gets NO horizontal
                # padding: it is a 12 px cell fitted into a table measured to
                # the pixel, and 1 px each side is 2 px of a budget that has
                # single digits left in it.  Nothing sits flush against it --
                # its neighbour still carries its own padx.
                pad = 0 if self._columns[idx].kind == "toggle" else 1
                entry["_widgets"][idx].grid(
                    row=r, column=col, columnspan=max(1, span),
                    sticky="we", padx=pad, pady=1)
            for col_spec in self._columns:
                if col_spec.key not in shown:
                    entry["_widgets"][key_index[col_spec.key]].grid_remove()
            # The ✕ is always in the same grid column, on every row and in
            # every shape: a delete button that moved with the row's kind
            # would be a moving target on a table the user is editing.
            entry["_widgets"][-1].grid(row=r, column=layout.ncols,
                                       padx=1, pady=1)
        self._schedule_resize()

    def clear(self) -> None:
        for entry in list(self._rows):
            for w in entry["_widgets"]:
                w.destroy()
        self._rows.clear()
        self._layout = None

    # ------------------------------------------------------------ get / set

    def _row_object(self, entry: dict):
        """One widget row -> the row dataclass it stores."""
        vals = {col.key: entry["_vars"][col.key].get().strip()
                for col in self._columns}
        if self._from_cells is not None:
            return self._from_cells(vals)
        row = self._row_factory()
        for key, val in vals.items():
            setattr(row, key, val)
        return row

    def get_rows(self) -> list:
        """Row dataclasses, blanks dropped (the row type decides what blank is)."""
        out = []
        for entry in self._rows:
            row = self._row_object(entry)
            if not row.is_blank():
                out.append(row)
        return out

    def set_rows(self, rows: Sequence) -> None:
        self.clear()
        self._bulk = True
        try:
            for row in rows:
                if self._to_cells is not None:
                    vals = self._to_cells(row)
                else:
                    vals = {col.key: str(getattr(row, col.key, "") or "")
                            for col in self._columns}
                self.add_row(vals, notify=False)
            while len(self._rows) < self._min_rows:
                self.add_row(notify=False)
        finally:
            self._bulk = False
        self._apply_layout(force=True)
        self._schedule_resize()

    def see_row(self, widget) -> int:
        """
        Scroll this table's OWN canvas so `widget` is visible; return where it
        ends up, in pixels from the top of this RowTable frame.

        Two scrollable regions are nested here -- the editor form's canvas and
        this table's -- and a row past `max_visible` is clipped by the inner
        one, so scrolling the outer one alone cannot reach it.  Measured: with
        7 rows in a max_visible=6 table the seventh sits 192 px down a 190 px
        viewport, and the editor scroll landed it 37 px ABOVE the editor
        canvas.

        The answer is COMPUTED, not re-measured: a canvas yview_moveto does
        not reach winfo_rooty until the next idle pass (measured 192 -> 192
        with no idle, 164 after one), and forcing one from a click handler is
        exactly the update_idletasks() this repo has been bitten by.
        """
        if not (widget.winfo_exists() and self._inner.winfo_exists()):
            return 0
        inner_h = max(1, self._inner.winfo_height())
        view_h = max(1, self._canvas.winfo_height())
        y = widget.winfo_rooty() - self._inner.winfo_rooty()
        frac = editor_scroll_fraction(y, widget.winfo_height(), view_h, inner_h)
        self._canvas.yview_moveto(frac)
        return ((self._canvas.winfo_rooty() - self.winfo_rooty())
                + int(round(y - frac * inner_h)))

    def data_row_widget(self, index: int, key: Optional[str] = None):
        """
        The widget for the `index`-th NON-BLANK row -- what a validation
        message's row number refers to, since get_rows() drops the blanks.

        `key` picks a column; without one it is the first cell the row's
        current shape actually shows, which is the one worth putting a caret
        in.  Returns None when the index is out of range or the row shows
        nothing, so a caller can fall back rather than guess.
        """
        seen = -1
        key_index = {col.key: i for i, col in enumerate(self._columns)}
        for r, entry in enumerate(self._rows):
            if self._row_object(entry).is_blank():
                continue
            seen += 1
            if seen != index:
                continue
            cells = (self._layout.rows[r]
                     if self._layout is not None and r < len(self._layout.rows)
                     else ())
            order = [k for k, _c, _s in cells] or [c.key for c in self._columns]
            if key is not None and key in order:
                order = [key]
            for k in order:
                idx = key_index.get(k)
                if idx is not None:
                    return entry["_widgets"][idx]
            return None
        return None

    @staticmethod
    def _set_state(widgets, editable: bool) -> None:
        """
        Grey a list of ttk widgets, reversibly.

        ttk's state FLAGS, not `configure(state=...)`: `state(['disabled'])`
        adds a flag and `state(['!disabled'])` removes it, so a combobox that
        was `readonly` comes back readonly.  Reconstructing the original state
        string by hand is what gets that wrong -- three of the connections
        table's six columns are readonly combos.
        """
        flag = ["!disabled"] if editable else ["disabled"]
        for w in widgets:
            try:
                w.state(flag)
            except Exception:                           # pragma: no cover
                pass

    def set_editable(self, editable: bool) -> None:
        """Grey/restore every cell, the '+ Add' button and every row's ✕."""
        self._editable = bool(editable)
        widgets = [self._add_btn]
        for entry in self._rows:
            widgets.extend(entry["_widgets"])
        self._set_state(widgets, self._editable)

    def column_values(self, key: str) -> tuple:
        """A combo column's current choices."""
        for col in self._columns:
            if col.key == key:
                return tuple(col.values)
        return ()

    def set_column_values(self, key: str, values: Sequence[str]) -> None:
        """Repopulate a combo column's choices (e.g. after a file change)."""
        idx = next((i for i, c in enumerate(self._columns) if c.key == key), None)
        if idx is None:
            return
        values = tuple(values)
        if values == tuple(self._columns[idx].values):
            # Cheap enough to call from the strip pass, which fires on every
            # keystroke: the merged-node entries at the top of the connections
            # dropdowns have to follow the short rows as they are typed.
            return
        for entry in self._rows:
            w = entry["_widgets"][idx]
            if isinstance(w, ttk.Combobox):
                w.configure(values=list(values))
        self._columns[idx] = replace(self._columns[idx], values=values)


class _CollapsibleHint(ttk.Frame):
    """
    A one-line grey summary plus a disclosure triangle for the full text.

    Collapsed by default: the long hints measured 182px in mode 6, more than
    twice the table they explain, on a form that already overflowed. The full
    text stays one click away and the expanded/collapsed state is remembered
    for the session, so a user who needs it once keeps it, and a user who has
    internalised it never sees it again.
    """

    _expanded = False        # class-level: shared across every hint instance

    def __init__(self, master, short: str, long: str, **kwargs):
        super().__init__(master, **kwargs)
        self._long_text = long
        self._btn = ttk.Label(self, foreground=PLACEHOLDER_FG, cursor="hand2")
        self._btn.pack(side=tk.TOP, anchor="w")
        self._btn.bind("<Button-1>", self._toggle)
        self._short = short
        self._body = ttk.Label(self, text=long, foreground=PLACEHOLDER_FG,
                               justify=tk.LEFT, wraplength=320)
        self._render()

    def _render(self) -> None:
        arrow = "▾" if _CollapsibleHint._expanded else "▸"
        # CLIPPED as a backstop.  Every caller is expected to be inside the
        # budget and the tests pin that they are -- but this widget now takes
        # text that follows the table's contents, so "the hint got longer"
        # became something that can happen at runtime, and unclipped it
        # silently widens the whole editor.
        text = self._short
        if len(text) > HINT_SHORT_CHARS:
            text = text[:HINT_SHORT_CHARS - 1].rstrip() + "…"
        self._btn.configure(text=f"{arrow} {text}")
        if _CollapsibleHint._expanded:
            self._body.pack(side=tk.TOP, anchor="w", pady=(1, 0))
        else:
            self._body.pack_forget()

    def set_text(self, short: str, long: str) -> None:
        """
        Replace the text, for a hint that follows what is in the table.

        EARLY-OUT ON AN UNCHANGED VALUE, because this is called from
        `_apply_editor_strips`, i.e. once per keystroke, and the normal case is
        that nothing moved.  Without it every character would reconfigure two
        Labels and fire a scrollregion refresh.

        `<<HintToggled>>` is generated for the same reason the toggle does: the
        form's height has changed and the editor canvas's scrollregion is
        derived from it.  The event says "this hint is a different size now",
        which is what the binding acts on -- it is not only about the arrow.
        """
        if (short, long) == (self._short, self._long_text):
            return
        self._short, self._long_text = short, long
        self._body.configure(text=long)
        self._render()
        self.event_generate("<<HintToggled>>")

    def _toggle(self, _event=None) -> None:
        _CollapsibleHint._expanded = not _CollapsibleHint._expanded
        for w in self.master.winfo_children():
            if isinstance(w, _CollapsibleHint):
                w._render()
        self.event_generate("<<HintToggled>>")


# ============================================================================
# A control strip that wraps instead of losing its tail
# ============================================================================

def reflow_rows(widths: Sequence[int], width: int) -> list[list[int]]:
    """
    Greedy left-to-right wrap: indices into `widths`, grouped into lines.

    Pure, so the packing decision can be tested without a display.  A single
    item that is wider than the whole strip still gets a line of its own rather
    than an empty one -- there is nothing better to do with it, and returning
    an empty line would place the next item on top of it.
    """
    rows: list[list[int]] = []
    cur: list[int] = []
    cur_w = 0
    for i, w in enumerate(widths):
        if cur and cur_w + w > width:
            rows.append(cur)
            cur, cur_w = [], 0
        cur.append(i)
        cur_w += w
    if cur:
        rows.append(cur)
    return rows


class ReflowRow(ttk.Frame):
    """
    A horizontal control strip that WRAPS onto a second line when it does not
    fit, instead of letting pack silently unmap its tail.

    This exists because the plot's control row is the one panel in the
    application nothing guarded.  Measured at the declared 1040x600 minsize the
    row asked for 918 px and got 575, and pack -- which unmaps from the END --
    took 'Im(Z)', 'Q', 'k', the fullscreen-quantity combobox and the Fullscreen
    button off screen with no scrollbar and no other route to them.  'k' is the
    quantity Mode 6 exists to produce and Fullscreen is the documented escape
    hatch for a readout box too wide for a 4-subplot grid, so neither has an
    alternative.  It was not only the minsize: _clamp_to_screen opens the
    window at min(1500, screen-80), which on a 1280-logical-px laptop is 1200
    px, and Fullscreen was off screen out of the box.

    Layout is by `place`, and that is load-bearing twice over.  Place does not
    propagate, so the strip's REQUESTED width no longer carries the 918 px into
    PlotPanel and out to the PanedWindow sash; and the wrap decision reads the
    strip's IMPOSED width (fill=X from the parent) and writes only its height,
    which cannot change that width.  That makes it a fixed point rather than
    the limit cycle _apply_editor_scrollbars documents -- a layout rule that
    reads a size it can itself change flips forever and update() never returns.
    """

    def __init__(self, master, pady: int = 1, **kw):
        super().__init__(master, height=1, **kw)
        self._items: list[tuple[tk.Widget, int, bool]] = []
        self._applied: tuple = ()
        self._pady = pady
        #: The one pending `after_idle` `refresh()` may own.  Held so it can be
        #: coalesced and, more importantly, CANCELLED on destroy: an
        #: un-cancelled `after` fires against a Tcl command the widget's
        #: teardown has already deleted, and Tk prints `invalid command name
        #: "..._reflow"` to a console a double-clicked GUI does not have --
        #: noise in a test run, invisible in production.
        self._pending = None
        self.bind("<Configure>", lambda _e: self._reflow())
        self.bind("<Destroy>", self._cancel_refresh)

    def add(self, widget, padx: int = 2, fill_y: bool = False):
        """Append a control.  `fill_y` is for vertical separators."""
        self._items.append((widget, padx, fill_y))
        self._reflow()
        return widget

    def item_widths(self) -> list[int]:
        return [w.winfo_reqwidth() + 2 * padx for w, padx, _f in self._items]

    def refresh(self) -> None:
        """
        Re-lay the strip after a CHILD's requested size changed.

        `_reflow` runs from `add()` and from this strip's own `<Configure>`,
        and a child whose TEXT grew fires neither -- `place` then goes on
        forcing the stale width and the child is CLIPPED, with no ellipsis and
        no overflow marker.  Measured in the Attribution window, whose header
        is a ReflowRow carrying a label built from the trace name: relabelling
        the trace to the documented 18-character cap took that item's request
        from 220 px to 307 while place kept it at 220, i.e. 87 px / 14
        characters cut in silence, and the strip went on reporting one row
        (29 px) while its items asked 1048 px of 964.  A 1 px window resize
        fixed both, which is what makes it a missing notification rather than
        a layout bug.

        Deferred to `after_idle` because the child's own geometry request has
        not been recomputed at the moment its text is set.  It reads the
        strip's IMPOSED width and writes only its height, so it is the same
        fixed point `_reflow` is and cannot become the limit cycle
        `_apply_editor_scrollbars` documents.  Coalesced, because a repaint
        that sets three labels must still cost one relayout.
        """
        if self._pending is not None:
            return
        try:
            self._pending = self.after_idle(self._refresh_now)
        except Exception:                       # pragma: no cover
            self._pending = None                # a dead widget cannot reflow

    def _refresh_now(self) -> None:
        self._pending = None
        if self.winfo_exists():
            self._reflow()

    def _cancel_refresh(self, event=None) -> None:
        # <Destroy> reaches every descendant too, so only the strip's own event
        # may cancel -- the same guard `AttributionWindow._on_destroy` carries.
        if event is not None and event.widget is not self:
            return
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except Exception:                   # pragma: no cover
                pass
            self._pending = None

    def _reflow(self) -> None:
        if not self._items:
            return
        width = self.winfo_width()
        if width <= 1:
            # Not laid out yet.  The <Configure> that gives it a real width
            # will call back; doing nothing here is what keeps the first pass
            # from wrapping every item onto its own line.
            return
        widths = self.item_widths()
        rows = reflow_rows(widths, width)
        row_h = max(w.winfo_reqheight()
                    for w, _p, _f in self._items) + 2 * self._pady
        # THE ITEM WIDTHS ARE PART OF THE KEY, and leaving them out made
        # refresh() a no-op in exactly the case it exists for.  A child whose
        # text grows without pushing the strip onto another row leaves the row
        # ASSIGNMENT unchanged, so `key` was unchanged, so `_reflow` returned
        # early and never re-placed it -- refresh() called _reflow() and
        # _reflow() declined to do anything.  It only ever looked fixed because
        # the case it was first measured on (220 px -> 307 px in the
        # Attribution header) happened to wrap as well.
        key = (tuple(tuple(r) for r in rows), row_h, tuple(widths))
        if key == self._applied:
            return
        self._applied = key
        y = 0
        for row in rows:
            x = 0
            for i in row:
                w, padx, fill_y = self._items[i]
                ww = w.winfo_reqwidth()
                x += padx
                if fill_y:
                    w.place(x=x, y=y + self._pady, width=ww,
                            height=row_h - 2 * self._pady)
                else:
                    # NO EXPLICIT width/height for an ordinary control.  place
                    # then sizes the slave from its own request AND TRACKS IT,
                    # so a child whose label grows is re-sized by Tk on the
                    # spot instead of staying clipped until something thinks to
                    # call refresh().  The wrap decision above still needs the
                    # notification -- it is the strip's own arithmetic, not the
                    # child's -- but a CLIPPED control, the failure mode with
                    # no ellipsis and no overflow marker, can no longer happen
                    # between the change and the relayout.
                    wh = w.winfo_reqheight()
                    w.place(x=x, y=y + max(0, (row_h - wh) // 2))
                x += ww + padx
            y += row_h
        self.configure(height=row_h * len(rows))

