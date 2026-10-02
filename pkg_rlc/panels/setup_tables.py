"""
pkg_rlc/panels/setup_tables.py -- the shared SETUP COMPONENT (L5).

`docs/design_workspaces.md` § 4.1 and § 8 item 2.  The RLC editor's two
tables -- the measurement ports and the connections -- as a component the
other workspaces can put on their left side, so "which ports are probed,
and what is done with the others" is asked ONE way everywhere:

    ConnectionsTable   the connections RowTable (the editor's columns and
                       per-Kind layout, `pkg_rlc.present.conntable`), the
                       fixed "Ports not listed anywhere are OPEN." line,
                       port-number dropdowns with the merged nodes on top,
                       `get_rows()` / `set_rows()` / `paint(issues)`.
                       The Trace model workspace uses it in place of its
                       stage-1 GND field.

    SetupTables        a Template box, the measurement-port RowTable (Name /
                       + ports / - ports), a ConnectionsTable and an issues
                       line.  `get() -> (mports, conn_rows)`, `set(...)`,
                       `set_nports(n, port_choices)`, `issues()`,
                       `on_change`, `set_editable(bool)`.  The Compare files
                       workspace uses it.

The RLC editor (`pkg_rlc.panels.panels_editor`) still builds its own two
tables in this stage: moving it onto this component is deferred, and the
code here is a port of how that editor builds and paints them -- same
columns, same layout function, same colours, same "never a dialog" rule for
a refusal -- not a second design.

CELLS ARE PAINTED, NEVER A DIALOG.  Every edit re-runs the checks and
colours the offending cell red (#b00020, refused) or amber (`WARN_FG`,
computed -- read this), with the reasons in the issues line.  The checks are
the L0 probe rules (`probe_rule_issues`), the connection rows' own parse
(`pkg_rlc.services.tracenets.conn_row_issues`, the parser asked one row at
a time so the error lands on its row), and a measurement-port cell that
does not parse.  The one dialog in this module is the Template box's
"replace what you typed?" confirmation, asked only over a non-empty table.

PORT CHOICES.  The dropdowns list bare port numbers by default (the
editor's rule: a 7-character combobox cannot show a name).  A caller may
pass "N  name" entries instead; a picked one is stored as "N"
(`port_text_from_choice`), so a name never reaches the DSL, which is
whitespace-tokenised.

No `pkg_rlc.frontend.app` import, at module level or inside a function.
"""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import asdict
from tkinter import messagebox, ttk
from typing import Callable, Optional, Sequence

from pkg_rlc.physics.core import (
    ConnectionRow, MeasPortRow, SpecIssue, build_terminations_rows,
    merged_nodes, probe_rule_issues,
)
from pkg_rlc.present.conntable import (
    CONN_OPEN_NOTE, CONN_TABLE_COLUMNS, EDITOR_TEMPLATES, TEMPLATE_PROMPT,
    ColumnSpec, conn_cells_from_row, conn_row_from_cells, conn_table_layout,
    template_rows,
)
from pkg_rlc.services.session import _rows_from_list
from pkg_rlc.services.tracenets import conn_row_issues
from pkg_rlc.widgets.widgets import PLACEHOLDER_FG, WARN_FG, RowTable

__all__ = [
    "ConnectionsTable", "SetupTables",
    "ERROR_FG", "WARN_CELL_FG", "MPORT_TABLE_COLUMNS", "TEMPLATE_CONFIRM",
    "port_choices", "port_text_from_choice", "issue_text", "table_head",
    "rows_to_json", "mports_from_json", "conn_rows_from_json",
]

#: Cell colours -- the editor's and the Trace model workspace's two, so red
#: means "refused" and amber "computed, read this" in every table.
ERROR_FG = "#b00020"
WARN_CELL_FG = WARN_FG

#: The measurement-port table, as the editor lays it out.
MPORT_TABLE_COLUMNS = (
    ColumnSpec("name", "Name", 9),
    ColumnSpec("plus", "+ ports (red)", 13),
    ColumnSpec("minus", "− ports (black)", 13),
)

#: Asked before a template overwrites rows the user typed -- the editor's
#: wording.  `{template}` is the template's name as the box lists it.
TEMPLATE_CONFIRM = ("Replace the measurement ports and connections with the "
                    "'{template}' template?")

#: How a cell is named in the issues line.
_CONN_COLUMN_TITLE = {"ports": "Port", "to": "To", "R": "R", "L": "L",
                      "C": "C", "kind": "Type", "net": "Net"}
_MPORT_COLUMN_TITLE = {"name": "Name", "plus": "+ ports", "minus": "− ports"}


# ============================================================================
# Pure helpers -- no Tk
# ============================================================================

_CHOICE_RE = re.compile(r"^\s*(\d+)\s{2}\S")
_LINE_PREFIX = re.compile(r"^Line \d+:\s*")


def port_choices(ts) -> list[str]:
    """
    The dropdown entries for one file's ports: "N  name" when the file
    names its ports, "N" when it does not.  Two spaces between the number
    and the name, which is what `port_text_from_choice` keys on.
    """
    names = list(getattr(ts, "port_names", None) or [])
    n = int(getattr(ts, "nports", 0) or 0)
    out = []
    for i in range(1, n + 1):
        name = names[i - 1].strip() if i - 1 < len(names) and names[i - 1] else ""
        out.append(f"{i}  {name}" if name else str(i))
    return out


def port_text_from_choice(text: str) -> str:
    """A picked "N  name" entry becomes "N"; anything typed is kept as is."""
    m = _CHOICE_RE.match(str(text or ""))
    return m.group(1) if m else str(text or "").strip()


def _mport_cell_issues(mports: Sequence[MeasPortRow],
                       conn_rows: Sequence[ConnectionRow]) -> list[SpecIssue]:
    """
    A measurement-port cell that does not parse, on its cell.

    `probe_rule_issues` leaves an unparseable cell to the parser ("a cell
    that does not parse is left to the parser's own message, which the
    build raises"), and the build names a DSL line, not a cell.  So each
    non-empty +/- cell is parsed alone, with the connection rows' named
    nodes (`as coil_tap`) in scope so a node name is not a false alarm.
    Ranges and grounding are the probe rules' business and are not judged
    here.
    """
    nets = [r for r in conn_rows if not r.is_blank()
            and getattr(r, "enabled", True) and r.kind == "short"
            and str(r.net).strip()]
    out: list[SpecIssue] = []
    for idx, row in enumerate(mports):
        if row.is_blank():
            continue
        for col in ("plus", "minus"):
            text = str(getattr(row, col) or "").strip()
            if not text:
                continue
            try:
                build_terminations_rows([MeasPortRow("P", plus=text)], nets,
                                        "", nports=None)
            except Exception as e:                          # noqa: BLE001
                out.append(SpecIssue(
                    "mports", idx, col, "error",
                    _LINE_PREFIX.sub("", str(e) or type(e).__name__),
                    "parse"))
    return out


def issue_text(issues: Sequence) -> str:
    """One line per issue, errors first, naming the table, row and cell."""
    lines = []
    for iss in sorted(issues, key=lambda i: 0 if i.is_error else 1):
        if getattr(iss, "table", "conn") == "mports":
            where = (f"Measurement port row {iss.row + 1}, "
                     f"{_MPORT_COLUMN_TITLE.get(iss.column, iss.column)}")
        else:
            where = (f"Connection row {iss.row + 1}, "
                     f"{_CONN_COLUMN_TITLE.get(iss.column, iss.column)}")
        kind = "" if iss.is_error else "warning: "
        lines.append(f"{where}: {kind}{iss.message}")
    return "\n".join(lines)


def rows_to_json(rows: Sequence) -> list[dict]:
    """Rows as the session file stores them -- the trace block's shape."""
    return [asdict(r) for r in rows]


def _from_json(cls, value, key: str, warn) -> list:
    """The session reader's own row coercion (booleans stay booleans), so a
    workspace block and a trace read a row the same way."""
    if value is None:
        return []
    return _rows_from_list(cls, value, key, warn or (lambda _m: None))


def mports_from_json(value, warn: Optional[Callable[[str], None]] = None
                     ) -> list[MeasPortRow]:
    return _from_json(MeasPortRow, value, "mports", warn)


def conn_rows_from_json(value, warn: Optional[Callable[[str], None]] = None
                        ) -> list[ConnectionRow]:
    return _from_json(ConnectionRow, value, "conn_rows", warn)


# ============================================================================
# Painting and the head row -- shared by both classes
# ============================================================================

def table_head(table: RowTable) -> ttk.Frame:
    """
    The frame a RowTable's '+ Add' button sits in: one row, the button
    packed RIGHT and the rest of it empty.  A caption, the OPEN line or a
    pair of row buttons packed LEFT into it cost no height at all, where a
    line of their own costs 17-29 px of a left column measured to the pixel
    (`ws_tracemodel.TraceModelWorkspace._build_left`).

    Reached through the button because `RowTable` (L4) does not name the
    frame; the right home for this is a `RowTable.head` property, and this
    is the only place that would change.
    """
    return table._add_btn.master

def _paint(table: RowTable, issues, painted: list,
           fallback: Optional[str] = None) -> list:
    """
    Put the last pass's colours back, then colour every cell `issues` names
    (red wins over amber on a cell named twice).  Returns the cells painted,
    for the next pass.  `fallback` is the column to colour when the named
    one is not shown on that row (a Kind change hides cells), rather than
    the row's first cell, which on the connections table is the on/off
    glyph.  Never raises.
    """
    for w in painted:
        try:
            if w.winfo_exists():
                w.configure(foreground="")
        except Exception:                                   # noqa: BLE001
            pass
    worst: dict = {}
    for iss in issues:
        try:
            w = table.data_row_widget(iss.row, iss.column)
            if w is not None and fallback is not None:
                first = table.data_row_widget(iss.row, "enabled")
                if w is first and iss.column != "enabled":
                    w = table.data_row_widget(iss.row, fallback)
        except Exception:                                   # noqa: BLE001
            w = None
        if w is None:
            continue
        key = str(w)
        if key in worst and worst[key][1]:
            continue                                        # already red
        worst[key] = (w, iss.is_error)
    out = []
    for w, is_error in worst.values():
        try:
            w.configure(foreground=ERROR_FG if is_error else WARN_CELL_FG)
            out.append(w)
        except Exception:                                   # noqa: BLE001
            pass
    return out


# ============================================================================
# ConnectionsTable
# ============================================================================

class ConnectionsTable(ttk.Frame):
    """
    The connections RowTable with the editor's columns and per-Kind layout,
    the fixed OPEN line under it, and cell painting.

    `on_change()` fires on every edit (a keystroke, a Kind pick, the on/off
    glyph, + Add, ✕), exactly as the RowTable's does; `set_rows` does not
    fire it -- a programmatic fill is not an edit, and the caller that made
    it knows.
    """

    def __init__(self, master, on_change: Optional[Callable[[], None]] = None,
                 min_rows: int = 1, max_visible: int = 6,
                 add_text: str = "+ Add", caption: str = "",
                 note: bool = True, **kw) -> None:
        super().__init__(master, **kw)
        self._on_change = on_change
        self._choices: tuple = ()
        self._painted: list = []
        self.table = RowTable(
            self, columns=CONN_TABLE_COLUMNS, row_factory=ConnectionRow,
            on_change=self._changed, min_rows=min_rows,
            max_visible=max_visible, add_text=add_text,
            layout_fn=conn_table_layout, to_cells=conn_cells_from_row,
            from_cells=self._from_cells)
        self.table.pack(side=tk.TOP, fill=tk.X)
        # The caption and the one rule neither table can show by itself -- a
        # port nobody listed is an open circuit -- are fixed text in the
        # table's own '+ Add' row (`table_head`): always on screen, never
        # behind a hint, and no height of their own.
        head = table_head(self.table)
        self.caption_lbl = ttk.Label(head, text=caption)
        if caption:
            self.caption_lbl.pack(side=tk.LEFT, padx=(0, 6))
        self.note_lbl = ttk.Label(head, text=CONN_OPEN_NOTE, anchor="w",
                                  foreground=PLACEHOLDER_FG)
        if note:
            self.note_lbl.pack(side=tk.LEFT)

    # ------------------------------------------------------------- rows

    @staticmethod
    def _from_cells(vals: dict) -> ConnectionRow:
        """The editor's conversion, with a picked "N  name" stored as N."""
        vals = dict(vals)
        for key in ("ports", "to"):
            vals[key] = port_text_from_choice(vals.get(key, ""))
        return conn_row_from_cells(vals)

    def get_rows(self) -> list[ConnectionRow]:
        """The rows, blanks dropped -- `SpecIssue.row` / `CellIssue.row`
        index THIS list, which is what `paint` maps onto the widgets."""
        return self.table.get_rows()

    def set_rows(self, rows: Sequence[ConnectionRow]) -> None:
        """Fill the table (a session load, a template, a test).  Does not
        call `on_change`; the colours of the previous rows are dropped."""
        self._painted = []
        self.table.set_rows(list(rows))
        self._refresh_choices()

    def _changed(self) -> None:
        self._refresh_choices()
        if self._on_change is not None:
            self._on_change()

    # ---------------------------------------------------------- choices

    def set_port_choices(self, choices: Sequence[str]) -> None:
        """The Port / To dropdowns' entries: bare numbers, or "N  name"."""
        self._choices = tuple(str(c) for c in choices)
        self._refresh_choices()

    def _refresh_choices(self) -> None:
        """Merged nodes first (their net name, else their first member) --
        the editor's R1-2 rule: naming the node is the cheap gesture, and
        listing its members multiplies an element by N."""
        try:
            refs = [nd.ref for nd in merged_nodes((), self.get_rows(), "")
                    if nd.ref]
        except Exception:                                   # noqa: BLE001
            refs = []
        values = refs + [c for c in self._choices
                         if port_text_from_choice(c) not in set(refs)]
        self.table.set_column_values("ports", values)
        self.table.set_column_values("to", values)

    def column_values(self, key: str) -> tuple:
        return self.table.column_values(key)

    # --------------------------------------------------------- painting

    def paint(self, issues) -> None:
        """Colour the cells `issues` name -- the ones whose `table` is
        'conn' (an issue with no `table` is taken as this table's)."""
        mine = [i for i in issues if getattr(i, "table", "conn") == "conn"]
        self._painted = _paint(self.table, mine, self._painted,
                               fallback="ports")

    # ------------------------------------------------------- passthrough

    def data_row_widget(self, index: int, key: Optional[str] = None):
        return self.table.data_row_widget(index, key)

    def set_editable(self, editable: bool) -> None:
        self.table.set_editable(editable)

    def register_wheel(self, register) -> None:
        self.table.register_wheel(register)


# ============================================================================
# SetupTables
# ============================================================================

class SetupTables(ttk.Frame):
    """
    The whole port setup: Template, measurement ports, connections, and an
    issues line.  Checks run on every edit and on every `set` /
    `set_nports`; `on_change()` fires on user edits and on a template
    applied, never on a programmatic `set`.
    """

    def __init__(self, master, on_change: Optional[Callable[[], None]] = None,
                 mport_max_visible: int = 4, conn_max_visible: int = 5,
                 wraplength: int = 400, **kw) -> None:
        super().__init__(master, **kw)
        self._on_change = on_change
        self._nports: Optional[int] = None
        self._painted: list = []
        self._issues: list = []
        self._editable = True

        trow = ttk.Frame(self)
        trow.pack(side=tk.TOP, fill=tk.X, pady=(0, 2))
        ttk.Label(trow, text="Template:").pack(side=tk.LEFT)
        self.template_var = tk.StringVar(master=self, value=TEMPLATE_PROMPT)
        self.template_cbo = ttk.Combobox(
            trow, textvariable=self.template_var, state="readonly",
            values=list(EDITOR_TEMPLATES), width=24)
        self.template_cbo.pack(side=tk.LEFT, padx=(6, 0))
        self.template_cbo.bind("<<ComboboxSelected>>",
                               lambda _e: self._on_template_selected())

        self.mp_table = RowTable(self, MPORT_TABLE_COLUMNS, MeasPortRow,
                                 on_change=self._changed, min_rows=1,
                                 max_visible=mport_max_visible)
        self.mp_table.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(table_head(self.mp_table),
                  text="Measurement ports:").pack(side=tk.LEFT)

        self.conn = ConnectionsTable(self, caption="Connections:",
                                     on_change=self._changed,
                                     max_visible=conn_max_visible)
        self.conn.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))

        self.issues_lbl = ttk.Label(self, text="", anchor="w",
                                    justify=tk.LEFT, wraplength=wraplength,
                                    foreground=ERROR_FG)
        self.issues_lbl.pack(side=tk.TOP, fill=tk.X, pady=(2, 0))
        self.set_nports(None)

    # ------------------------------------------------------------ the API

    def get(self) -> tuple[list[MeasPortRow], list[ConnectionRow]]:
        """(measurement-port rows, connection rows), blanks dropped."""
        return self.mp_table.get_rows(), self.conn.get_rows()

    def set(self, mports: Sequence[MeasPortRow],
            conn_rows: Sequence[ConnectionRow]) -> None:
        """Fill both tables and re-check.  Does not call `on_change`."""
        self._painted = []
        self.mp_table.set_rows(list(mports))
        self.conn.set_rows(list(conn_rows))
        self.refresh()

    def is_empty(self) -> bool:
        mports, conn = self.get()
        return not mports and not conn

    def set_nports(self, n: Optional[int],
                   port_choices: Optional[Sequence[str]] = None) -> None:
        """The file the setup is checked against: its port count (None or 0:
        no file, no range check) and the dropdown entries -- bare numbers
        unless `port_choices` is given."""
        n = int(n) if n else None
        self._nports = n
        if port_choices is None:
            port_choices = [str(i) for i in range(1, (n or 0) + 1)]
        self.conn.set_port_choices(port_choices)
        self.refresh()

    @property
    def nports(self) -> Optional[int]:
        return self._nports

    def issues(self) -> list[SpecIssue]:
        """Every cell's complaint, as `SpecIssue`s: the connection rows'
        parse (table 'conn'), the measurement-port cells' parse, and the
        probe rules (table 'mports')."""
        return list(self._issues)

    def has_errors(self) -> bool:
        return any(i.is_error for i in self._issues)

    def set_editable(self, editable: bool) -> None:
        self._editable = bool(editable)
        # State FLAGS, so the readonly box comes back readonly.
        RowTable._set_state([self.template_cbo], self._editable)
        self.mp_table.set_editable(self._editable)
        self.conn.set_editable(self._editable)

    def register_wheel(self, register) -> None:
        self.mp_table.register_wheel(register)
        self.conn.register_wheel(register)

    # ------------------------------------------------------- the checks

    def _compute_issues(self) -> list[SpecIssue]:
        mports, conn = self.get()
        out: list[SpecIssue] = []
        try:
            for ci in conn_row_issues(conn, self._nports or 0):
                out.append(SpecIssue("conn", ci.row, ci.column, ci.severity,
                                     ci.message, "conn_row"))
        except Exception:                                   # noqa: BLE001
            pass
        conn_ok = not any(i.is_error for i in out)
        try:
            out.extend(_mport_cell_issues(mports, conn if conn_ok else []))
        except Exception:                                   # noqa: BLE001
            pass
        try:
            out.extend(probe_rule_issues(mports, conn if conn_ok else [], "",
                                         nports=self._nports))
        except Exception:                                   # noqa: BLE001
            pass
        return out

    def refresh(self) -> None:
        """Re-check and repaint.  Never raises."""
        self._issues = self._compute_issues()
        self._painted = _paint(self.mp_table,
                               [i for i in self._issues
                                if i.table == "mports"],
                               self._painted)
        self.conn.paint(self._issues)
        any_error = any(i.is_error for i in self._issues)
        self.issues_lbl.configure(
            text=issue_text(self._issues),
            foreground=ERROR_FG if any_error else WARN_CELL_FG)

    def _changed(self) -> None:
        self.refresh()
        if self._on_change is not None:
            self._on_change()

    # --------------------------------------------------------- template

    def _on_template_selected(self) -> None:
        name = self.template_var.get()
        # Back to the prompt FIRST: a template is an action, not a state.
        self.template_var.set(TEMPLATE_PROMPT)
        if name in EDITOR_TEMPLATES:
            self.apply_template(name)

    def apply_template(self, name: str) -> bool:
        """
        Fill both tables from the template `name`; True when written.
        Asks first (messagebox.askyesno) when either table has a row --
        what is there was typed by someone, and this replaces all of it.
        """
        if name not in EDITOR_TEMPLATES or not self._editable:
            return False
        if not self.is_empty() and not messagebox.askyesno(
                "Replace the tables?",
                TEMPLATE_CONFIRM.format(template=name), parent=self):
            return False
        mports, conn = template_rows(name, self._nports)
        self.set(mports, conn)
        if self._on_change is not None:
            self._on_change()
        return True
