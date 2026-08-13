"""
pkg_rlc_conntable.py  --  the connections table's SHAPE, and the vocabulary
that shape is spoken in.

Split out of pkg_rlc_gui.py, verbatim.  Two things live here:

  * `ColumnSpec` / `TableLayout` / `identity_layout` -- the pure, Tk-free
    vocabulary a `RowTable` (pkg_rlc_widgets) lays itself out with.  They are
    HERE rather than beside the widget because they are the INTERFACE between
    the per-table layout rules at this layer and the widget one layer above:
    the layer map (tests/test_layering.py) puts `pkg_rlc_conntable` at L3 and
    `pkg_rlc_widgets` at L4, so a shared type has to sit at the lower of the
    two or the import runs UPWARD.  pkg_rlc_widgets imports them from here.

  * The connections table itself -- which cells each Kind shows, what its
    header then says, how a row's storage maps onto its cells, and the
    per-Kind fill-in hints.

Pure: no Tk, no App, no widget.  Imports pkg_rlc_core only.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Sequence

from pkg_rlc_core import (
    CONN_KINDS,
    CONN_KINDS_WITH_NET,
    CONN_KINDS_WITH_RLC,
    ConnectionRow,
)


# ============================================================================
# The RowTable vocabulary
# ============================================================================

@dataclass(frozen=True)
class ColumnSpec:
    """One column of a RowTable.  `key` is the row dataclass's field name."""
    key: str
    title: str
    width: int                      # in characters
    kind: str = "entry"             # "entry" | "combo" | "static"
    values: tuple = ()              # choices, for kind="combo"
    placeholder: str = ""
    readonly_combo: bool = False    # combo that rejects typed-in values
    # Value a '+ Add' row starts with.  The Add button is bound as
    # command=self.add_row, which Tk calls with NO arguments, so without this a
    # new connection row arrives with kind='' -- and rows_to_dsl_text raises on
    # that rather than treating it as blank.
    default: str = ""


@dataclass(frozen=True)
class TableLayout:
    """
    Where every cell of a RowTable goes -- decided for the WHOLE table at once.

    `headers` and `weights` are one entry per GRID column; `rows` carries, per
    row, the cells that row shows as (column key, grid column, columnspan).  A
    key absent from a row's tuple is not gridded at all.

    ONE function for the whole table rather than one per row, because the two
    decisions are coupled: a cell may only spread into a grid column that NO
    row is using, and that is exactly the column whose header must be blank.
    Split them and you get a "To" title sitting over a port field -- which is
    the header half of R1-1 and the reason this type exists rather than a
    per-row `shape_fn`.

    Frozen and made of tuples so `==` is cheap and total: RowTable recomputes
    the layout on every keystroke and re-grids only when the answer changed.
    """
    ncols: int
    headers: tuple = ()
    weights: tuple = ()
    rows: tuple = ()


def identity_layout(columns: Sequence[ColumnSpec],
                    values_per_row: Sequence[dict]) -> TableLayout:
    """
    Every column, every row, one grid column each -- the historical shape.

    This is what a RowTable with no `layout_fn` uses, and it is byte-for-byte
    what the measurement-port table had before layouts existed (weight 1 on
    every non-static column, titles straight off the ColumnSpecs).
    """
    keys = [c.key for c in columns]
    cells = tuple((k, i, 1) for i, k in enumerate(keys))
    return TableLayout(
        ncols=len(keys),
        headers=tuple(c.title for c in columns),
        weights=tuple(1 if c.kind != "static" else 0 for c in columns),
        rows=tuple(cells for _ in values_per_row),
    )


#: Character budget for a hint's COLLAPSED line.
#:
#: The collapsed line is a `ttk.Label` with no wraplength sitting directly in
#: the editor form's grid, so its requested width IS a lower bound on the
#: form's -- there is nothing between it and the 431 px canvas.  Measured on
#: the real widget (Microsoft YaHei UI 9, tk scaling 1.333), with the arrow
#: prefix: the generic connections line is 59 chars / 354 px, the widest
#: per-Kind line is 64 / 380, and the connections TABLE is 410, so a line
#: inside this budget can never be the thing that decides the form's width.
#: Past it, it silently is: a 92-character line measured 533 px and took mode
#: 5's form from 418 to 540 px, turning the horizontal scrollbar on for good.
#:
#: 64 rather than a pixel count because the text is set from pure functions the
#: tests check with no display; the pixel guard is in the layout tests.
HINT_SHORT_CHARS = 64


# --- Mode 5 connections table -------------------------------------------
#
# Column widths are a MEASURED budget, not a preference. The editor canvas is
# 431 px wide once its vertical scrollbar is showing, which in mode 5 it always
# is; the label column costs 91 px, which is why this table gets a caption
# ABOVE it and spans all four form columns instead of sitting beside a label.
# At these widths the table asks for 405 px and the whole mode-5 form for 418,
# so the headroom under the 431 px viewport is 13 px, not 22. Measure it again
# before adding a column -- CLAUDE.md carries the same two numbers.  405 px is
# still the WORST case (every Kind present at once); what changed in R1-1 is
# that a table of one Kind now asks for far less -- see conn_table_layout.
#
# Type is a readonly combo -- a kind that is not in CONN_KINDS raises at build
# time, so there is nothing useful to type. Port and To are NOT readonly: a
# range ('6-14', '35:1:45') has to be typeable, and a readonly combo cannot be
# typed into at all. Their values are the file's bare port numbers (with any
# merged node's ref in front of them), filled in by _refresh_port_choices;
# there is deliberately no 'GND' entry, because "to ground" is a KIND here
# (ground / rlc_gnd) and 'short_to GND' is a parser error the user could not
# connect to what they clicked.

# Where the net name a short row carries is STORED.  R1-2 -- the net rules and
# the storage -- is core's half of this round; the cell is rendered here.
# Feature-detected rather than assumed, because a cell bound to a field that
# does not exist would take a name, show it, and lose it on the next Duplicate
# or session save: dataclasses.replace() and asdict() both work off fields(),
# so an attribute set by setattr on the instance simply is not there.  If core
# ever renames the field, this constant is the ONE place to reconcile, and
# conn_table_layout(..., net=False) is the shape the table falls back to.
CONN_NET_KEY = "net"
CONN_NET_SUPPORTED = CONN_NET_KEY in {f.name for f in fields(ConnectionRow)}

# Grid columns of the connections table.  The Net cell still shares grid
# column 3 with To rather than taking one of its own, because the measured
# headroom under the 431 px editor canvas is 13 px and an ordinary seventh
# column is 41 px at its narrowest.  The ✕ button is always at _CONN_NCOLS, so
# it does not move when a row changes shape.
#
# THE ON/OFF COLUMN IS THE ONE EXCEPTION, and it fits only because of what it
# is made of.  Measured at 100% (Microsoft YaHei UI 9, tk scaling 1.333):
# ttk.Checkbutton 23 px, ttk.Label with default padding 16 px,
# tk.Label(padx=0, pady=0, bd=0) 12 px -- and U+2611 / U+2610 both measure
# exactly 12 px in that font, so the pair is WIDTH-STABLE and toggling one row
# cannot reflow the table (the run-tab marker rule).  It is 17 px tall against
# the combobox's 25, so it does not touch the row height either.  Worst-case
# table width goes 405 -> 410 px and the mode-5 FORM 418 -> 417, i.e. one pixel
# NARROWER than before this column existed: the 14 px the cell takes are paid
# for by its own padx=0 (2 px) and by the ✕ button going from width=2 to
# width=1 (7 px), which still shows the whole 12 px glyph.
_CONN_COL_ON = 0
_CONN_COL_TYPE, _CONN_COL_PORT, _CONN_COL_SECOND = 1, 2, 3
_CONN_COL_R, _CONN_COL_L, _CONN_COL_C = 4, 5, 6
_CONN_NCOLS = 7

#: The width-stable pair the toggle cell shows.  Both 12 px; do not replace
#: either with a glyph that is not, or a table of mixed states reflows as the
#: user clicks through it.
CONN_ON_GLYPH = "☑"
CONN_OFF_GLYPH = "☐"

CONN_TABLE_COLUMNS = (
    ColumnSpec("enabled", "", 1, kind="toggle", default=CONN_ON_GLYPH),
    ColumnSpec("kind", "Type", 11, kind="combo", values=CONN_KINDS,
               readonly_combo=True, default="ground"),
    ColumnSpec("ports", "Port", 7, kind="combo"),
    ColumnSpec("to", "To", 7, kind="combo"),
    ColumnSpec("R", "R Ω", 5),
    ColumnSpec("L", "L H", 5),
    ColumnSpec("C", "C F", 5),
) + ((ColumnSpec(CONN_NET_KEY, "Net", 8),) if CONN_NET_SUPPORTED else ())
# width=9 is a MEASURED number, not a taste: grid column 2 is 74 px wide
# because a 7-character ttk.Combobox asks 72, and a ttk.Entry asks 55 / 62 /
# 69 / 76 px at 7 / 8 / 9 / 10 characters.  At 9 the Net cell costs the column
# nothing; at 10 it would widen the whole table by 4 px against 13 px of
# headroom.  The three candidate titles measure 12 ("To") / 18 ("Net") / 48
# ("To / Net") px in TkDefaultFont 8, all under 72, so the header cannot widen
# it either.  Re-measure both before changing this.


def conn_table_layout(values_per_row: Sequence[dict],
                      net: bool = CONN_NET_SUPPORTED) -> TableLayout:
    """
    Which cells each connection row shows -- decided by its Kind.

    The complaint this answers, verbatim: "不同的连接，出现的表格都是一样的
    ... 多个pin连接到一起的时候，我很自然的感觉就是一个blank，输入我要短接
    的PIN就行，但是现在有两个blank".  A short group has no natural from/to;
    a ground row has no To at all.  Measured on the shipping table (405 px, 6
    columns): a ground row's To + R + L + C are 195 px of dead cells, 48% of
    the width, and a short row's R + L + C are 123 px, 30%.

    What this gives back, measured as the table's own reqwidth (cell padding
    included, which is why the deltas are a few px larger than the figures
    above):  ground-only 405 -> 202, short-only -> 273, rlc_gnd-only -> 331,
    and every Kind at once -> 405, i.e. the WORST case is exactly the table
    this replaces.  At 150% font scaling: 413 -> 210 / 281 / 339 / 413.
    tests/test_conn_rowshape.py::TestTableWidth is the guard.

    Two rules, and the second is what keeps the HEADER honest:

      1. A row shows only the cells its Kind uses.
      2. A cell may spread rightwards ONLY over grid columns that no row in
         the table is using -- which are exactly the columns whose title is
         blank.  So a wide cell never sits under someone else's heading, and
         a table of nothing but ground rows collapses to one wide Port field.

    Grid column 2 carries To (rlc_between) or the net Name (short), so its
    title follows what is in the table: "To", "Net", "To / Net", or nothing.
    A static "To" there was a lie on a short row even with the cell hidden.

    Pure: takes the cells as text, returns a TableLayout.  `net` is passed in
    rather than read from the module constant so both branches are testable
    before core lands the storage.
    """
    kinds = [(v.get("kind") or "").strip() for v in values_per_row]
    known = set(CONN_KINDS)
    wants_to = "rlc_between" in kinds
    # CONN_KINDS_WITH_NET, not a literal "short": core decides which kinds
    # create a node (only a short does), and a second one must not need an
    # edit here as well as there.
    wants_net = bool(net) and any(k in CONN_KINDS_WITH_NET for k in kinds)
    wants_rlc = any(k in CONN_KINDS_WITH_RLC for k in kinds)
    # An unrecognised kind gets the full six-cell shape: the table must not
    # hide a cell it cannot reason about (a session hand-edited to a kind this
    # build does not know would otherwise lose its values with no symptom).
    wants_all = any(k not in known for k in kinds)

    second = ("To / Net" if wants_to and wants_net else
              "To" if wants_to else "Net" if wants_net else "")
    if wants_all:
        second = second or "To"
        wants_rlc = True
    # The on/off column has NO title and weight 0.  No title because the two
    # glyphs are their own legend and 12 px fits no word; weight 0 because it
    # is a fixed-width cell and giving it a share of the slack would take that
    # slack from the port fields, which are the cells that run out of room.
    headers = ("", "Type", "Port", second,
               *(("R Ω", "L H", "C F") if wants_rlc else ("", "", "")))
    weights = (0, 1, 1, 1 if second else 0,
               *((1, 1, 1) if wants_rlc else (0, 0, 0)))

    rows = tuple(_conn_row_cells(k, bool(second), wants_rlc, net, wants_all)
                 for k in kinds)
    return TableLayout(_CONN_NCOLS, headers, weights, rows)


def _conn_row_cells(kind: str, second_used: bool, rlc_used: bool,
                    net: bool, wants_all: bool) -> tuple:
    """
    One row's cells, as (column key, grid column, columnspan).

    A cell spreads only over columns the TABLE is not using, and it cannot
    jump one: a Port field with grid column 2 in use stops at 1 even when
    3-5 are free, because grid has no way to skip a column mid-span.
    """
    # The on/off cell is on EVERY row whatever the Kind: a row the user cannot
    # switch back on is a row they have to delete, which is the gesture the
    # switch exists to replace.
    head = (("enabled", _CONN_COL_ON, 1), ("kind", _CONN_COL_TYPE, 1))
    rlc = (("R", _CONN_COL_R, 1), ("L", _CONN_COL_L, 1), ("C", _CONN_COL_C, 1))
    both_ports = (("ports", _CONN_COL_PORT, 1), ("to", _CONN_COL_SECOND, 1))
    if wants_all or kind not in CONN_KINDS or kind == "rlc_between":
        return head + both_ports + rlc
    if kind == "rlc_gnd":
        # Recovers the 74 px of To that an rlc_gnd row has always wasted
        # (a 7-character combobox asks 72 px plus 2 px of padding), whenever
        # nothing else in the table needs that column.
        return head + (("ports", _CONN_COL_PORT, 1 if second_used else 2),) + rlc
    if kind in CONN_KINDS_WITH_NET and net:
        # The freed To cell holds the node name (design note §4b): a short row
        # needs one port field, so the second slot is where "these three are
        # one point" gets a name other rows can reference.
        return head + (("ports", _CONN_COL_PORT, 1),
                       (CONN_NET_KEY, _CONN_COL_SECOND,
                        1 if rlc_used else _CONN_NCOLS - _CONN_COL_SECOND))
    # ground / vdd / open, and short with no net storage: ONE port field, as
    # wide as the table can spare.
    if second_used:
        span = 1
    elif rlc_used:
        span = 2
    else:
        span = _CONN_NCOLS - _CONN_COL_PORT
    return head + (("ports", _CONN_COL_PORT, span),)


# ---- the short row's tied group: one cell over ports + to -------------------
#
# A short row now stores its whole tied group in `ports` and leaves `to` empty
# (`5,6,7,8 short`), which is what makes ONE cell the storage as well as the
# display.  `to` stays live as the LEGACY two-field spelling: a session saved
# before this round, and the synthetic rows _trace_role_rows builds for mode 3,
# both carry `short 5 -> 6,7,8` and still emit `5 short_to 6,7,8`.  The pair
# below is the only place that knows both spellings: it MERGES the legacy pair
# into the single cell for display, and writes the merged form back, so a
# legacy row is converted the first time it is edited and never afterwards.

def _join_short_group(ports: str, to: str) -> str:
    """('5', '6,7,8') -> '5,6,7,8'.  NO SPACES -- collapse_ports's rule, for
    the same reason: the DSL is whitespace-tokenised and the port field is
    parts[0], so '5, 6' would parse as the field '5,' with a stray '6'."""
    parts = [p for p in ((ports or "").strip(), (to or "").strip()) if p]
    return ",".join(parts)


def conn_cells_from_row(row) -> dict:
    """ConnectionRow -> the cell texts the table shows."""
    vals = {col.key: str(getattr(row, col.key, "") or "")
            for col in CONN_TABLE_COLUMNS}
    # `enabled` is a BOOL on the row and a GLYPH in the cell, and the glyph is
    # the storage as well as the display: the cell variable IS what the
    # tk.Label renders, so there is no second copy to keep in step.  str(True)
    # would put the word "True" in the cell, which is why this is not left to
    # the generic conversion above.
    vals["enabled"] = (CONN_ON_GLYPH if getattr(row, "enabled", True)
                       else CONN_OFF_GLYPH)
    if (vals.get("kind") or "").strip() == "short":
        vals["ports"] = _join_short_group(vals.get("ports", ""),
                                          vals.get("to", ""))
        vals["to"] = ""
    return vals


def conn_row_from_cells(vals: dict) -> ConnectionRow:
    """The cell texts -> the ConnectionRow they store."""
    row = ConnectionRow()
    for col in CONN_TABLE_COLUMNS:
        if col.kind == "toggle":
            continue
        setattr(row, col.key, (vals.get(col.key) or "").strip())
    # The glyph back to a bool.  Tested against the OFF glyph rather than the
    # ON one so that anything unexpected in that cell -- an empty string from a
    # row built in code, a value from a build that spelled the pair
    # differently -- reads as ENABLED.  A row that silently disappears from the
    # spec is the failure to avoid; a row that is unexpectedly present is
    # visible in the answer.
    row.enabled = (vals.get("enabled") or "").strip() != CONN_OFF_GLYPH
    if (row.kind or "").strip() == "short":
        # The cell IS the group; `to` is emptied rather than back-filled, or
        # the row would carry the same ports twice.
        row.to = ""
    return row


CONN_TABLE_HINT_SHORT = "one row per connection; the Kind decides which cells it has"

# --- Per-Kind fill-in hints ------------------------------------------------
#
# THE HINT FOLLOWS THE KINDS IN THE TABLE, which is the rule `conn_table_layout`
# already applies to the cells and to the header.  R1-1's complaint was that
# every Kind got the same table; the hint under it had the same defect one
# layer up -- a single paragraph covering all six, so a user filling in a
# `short` row read two sentences about `rlc_between` to reach the one about
# theirs, and the sentence they needed ("the whole group goes in ONE cell,
# there is no To") was in the middle of it.
#
# It costs no pixels COLLAPSED (one line, as before) and is normally SHORTER
# expanded, because a real table carries one or two Kinds and not six.  The
# general rules below are the ones that apply to every row and are appended
# once, not repeated per Kind.
#
# Each entry is (one-line summary, the full "how do I fill this in").  The
# summary is what a single-Kind table shows collapsed, so it leads with the
# Kind and then with the CELLS -- that is the question being asked.
CONN_KIND_HINTS = {
    "ground": (
        "ground: Port only -- a whole ground set is ONE row (6-14)",
        "ground -- Port only, no other cell. The port is tied to the "
        "reference node (V=0). A range is one row, so a package's ground "
        "balls are '6-14' or '35:1:45' rather than nine rows."),
    "vdd": (
        "vdd: Port only -- identical to ground for AC",
        "vdd -- Port only, and evaluated exactly as ground: for AC "
        "small-signal VDD *is* an AC ground. It exists to record the intent, "
        "so a reader of the spec can see which rows are supply and which are "
        "ground."),
    "open": (
        "open: Port only -- unlisted ports are ALREADY open",
        "open -- Port only. Everything you do not list is open already, so "
        "this row changes no number; it is for SAYING SO, which is worth a "
        "row when the alternative is a reader wondering whether the port was "
        "forgotten."),
    "short": (
        "short: the WHOLE group in Port (5,6,7,8); the next cell NAMES it",
        "short -- Port holds the WHOLE tied group: '5,6,7,8' or '23-25'. "
        "There is no To cell, because a group of shorted pins has no "
        "from/to. The cell beside Port is the node's NAME (optional): type "
        "'coil_tap' there and any port field may say that name instead of a "
        "port number. Shorting is transitive, so one row ties every port in "
        "it into one node."),
    "rlc_gnd": (
        "rlc_gnd: Port + R/L/C -- one element PER port, not one shared",
        "rlc_gnd -- Port plus R/L/C: a series R-L-C from EACH listed port to "
        "ground. A range is one element PER PORT: '21:1:25' with L=80p is "
        "five separate 80 pH inductors, which is the right model for five "
        "ground balls each with its own lead. For ONE shared element, short "
        "the ports together first and hang this row off the node (its net "
        "name, or any one member port). Two rlc_gnd rows on the same port do "
        "NOT parallel -- the lower row wins."),
    "rlc_between": (
        "rlc_between: Port and To, one port each, + R/L/C",
        "rlc_between -- Port and To, plus R/L/C. The only Type with two port "
        "fields, because a two-terminal element really has two ends. A range "
        "is refused on the To side: an N-to-M element is ambiguous (star? "
        "mesh?) and guessing would be a silent wrong answer. Two "
        "rlc_between rows on the same pair ARE two elements in parallel."),
}

#: Appended once, under whichever Kind hints are showing.  These hold for every
#: row whatever its Kind, so repeating them per Kind would be six copies of the
#: SI rule to keep in step.
CONN_TABLE_HINT_GENERAL = (
    "The box at the START of a row switches it OFF: the row keeps everything "
    "in it and contributes nothing, exactly as if it were deleted, which is "
    "the quick way to ask what a connection is worth. It is not the same as "
    "setting the Type to 'open' -- that is a different declaration, and on an "
    "rlc_gnd row it throws the element away as well. The strip below says how "
    "many rows are off, so a switch left down is not a spec you have "
    "forgotten about. "
    "Every port field takes ranges -- 6-14 or 35:1:45. R/L/C hold the bare "
    "value with SI suffixes and the unit is in the header: 5m is 5 milli, 5M "
    "is 5 Mega, and the value must be ONE word -- '5 m' and '1 uF' are "
    "rejected. A blank R/L/C means OMITTED, which is not zero: an omitted C "
    "is no capacitor, while C=0 would be an open circuit. The dropdowns list "
    "port NUMBERS; for the file's port names click 'Show Ports' at the top of "
    "this panel, which opens 'Ports & Roles' -- every port with its name and "
    "role, the open ones flagged when their names match a set you grounded, "
    "and a selection written back here as a collapsed range."
)

#: With more than one file on the trace, a port of another file carries its
#: tag.  Only shown when the trace actually has one, so a single-file user
#: never reads about tags.
CONN_TABLE_HINT_TAGGED = (
    "This trace has more than one file: a bare port number is a port of the "
    "HOME file, and a port of another carries its tag ('F2.15'). The tag "
    "scopes the ONE token it is on, so '25,26,F2.15' ties two home ports to "
    "package 15 and reads the same in any order; a range is one token, so "
    "'F2.40-42' takes one tag while 'F2.40,42' is package 40 and HOME 42. "
    "The strip below echoes what each tagged field resolved to."
)


def conn_hint_text(rows: Sequence, tagged: bool = False) -> tuple:
    """
    (collapsed line, expanded text) for the Kinds actually in the table.

    Pure, and the reason it is: the widget is refreshed on every keystroke, so
    the early-out in `_CollapsibleHint.set_text` needs a value it can compare,
    and a hint that is a property of the ROWS is testable without a display.

    Order is `CONN_KINDS`, never the order the rows happen to be in: the hint
    is a reference, and a reference that reorders itself as the user edits is
    one the eye cannot find its place in again.

    An empty table gets EVERY Kind, which is the one case where the reader is
    choosing a Kind rather than filling one in.
    """
    try:
        present = {(getattr(r, "kind", "") or "").strip()
                   for r in rows if not r.is_blank()}
    except Exception:                       # pragma: no cover - see the strips
        present = set()
    kinds = [k for k in CONN_KINDS if k in present and k in CONN_KIND_HINTS]
    if not kinds:
        kinds = [k for k in CONN_KINDS if k in CONN_KIND_HINTS]
        short = CONN_TABLE_HINT_SHORT
    elif len(kinds) == 1:
        short = CONN_KIND_HINTS[kinds[0]][0]
    else:
        # NOT the generic line plus a suffix.  Measured: that spelling is 92
        # characters / 533 px, and the collapsed line is an unwrapped Label
        # inside the editor FORM, so it sets the form's requested width -- it
        # took mode 5 from 418 px to 540 against a 431 px canvas, i.e. one
        # sentence turned the horizontal scrollbar on permanently.  See
        # HINT_SHORT_CHARS.
        short = (f"{len(kinds)} kinds in this table -- "
                 f"click for how each is filled in")
    body = [CONN_KIND_HINTS[k][1] for k in kinds]
    if tagged:
        body.append(CONN_TABLE_HINT_TAGGED)
    body.append(CONN_TABLE_HINT_GENERAL)
    return short, "\n\n".join(body)


#: The whole reference, every Kind, no table needed.  Kept as a module
#: constant because the Help text and the tests both want "all of it" and
#: neither has rows to hand.
CONN_TABLE_HINT = conn_hint_text(())[1]
