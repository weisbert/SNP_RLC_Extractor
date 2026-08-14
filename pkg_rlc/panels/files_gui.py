"""
pkg_rlc_files_gui.py -- which FILES a trace is made of, and what that costs.

Round 3's file UI (R3-2, R3-3) and the GUI half of the reference-node
self-check (R3-5).  `pkg_rlc_compose` does every piece of arithmetic -- the
stacking, the frequency plan, the namespace, the weld detection -- and
everything here is presentation, budget and refusal, the same split
`pkg_rlc_attrib_gui` has against `pkg_rlc_attrib`.

It is a module of its own for the reason that one is: `pkg_rlc_gui` is 8000+
lines and this window, the port-cell scope rules and the weld strip were built
while another pair of hands was inside `TraceConfig` itself.

WHAT pkg_rlc_gui HAS TO CALL (the whole hook surface -- there is no other)
-------------------------------------------------------------------------
    FILES_MENU_LABEL                    the menubar / right-click entry text
    files_refusal(trace)                None when the window can open, else why
    open_files_window(app, trace)       -> FilePairWindow | None
    refresh_files_windows(app)          from `_apply_editor_strips`, from every
                                        path that removes a trace or a file,
                                        and after a session load
    slots_of(app, trace)                -> [FileSlot], one per file, tagged
    spec_problems(app, trace)           structural faults + the cell budget,
                                        for the editor's validation strip

and, for whoever wires the connection table's port cells:

    render_port_cell / render_port_cells / cell_scope / cell_is_foreign
    port_choices / resolve_cell         the default-scope rules (R3-2)
    reference_strip_text / reference_report_lines / reference_provenance
                                        the weld, rendered once (R3-5)

`refresh_files_windows` is a couple of Label writes and a Treeview repopulate,
and it NEVER RAISES -- the `_apply_editor_strips` contract: an error raised
there reaches no handler anyone controls, Tk prints it to a console a
double-clicked GUI does not have, and the window carries on showing a stale
answer.

WHAT THIS MODULE LOOKS UP ON THE App, BY NAME
---------------------------------------------
    _file_by_label(label)               required; a missing file is rendered as
                                        `loaded=False` rather than dropped
    set_trace_home_file(tc, label)      required for [Set as home]; the editor
                                        owns the File combobox, so a label
                                        poked onto the trace is overwritten by
                                        the next `_sync_editor_to_trace`
    add_trace_file / remove_trace_file  OPTIONAL.  Preferred when present; the
                                        fallback writes `file_labels`, which no
                                        editor widget owns, and then does the
                                        bookkeeping the edit owes (stale, the
                                        Traces list, the renumbering note).
                                        Delete the fallback when the hooks land.
Everything looked up this way REPORTS ITS ABSENCE BY NAME rather than doing
nothing: a control that silently fails is a bug report, and a control that says
which hook it is waiting for is a work item.

WHY THIS IS NOT A BUTTON ON THE FILES ROW
-----------------------------------------
Measured and recorded in CLAUDE.md: the Files row and the Traces row are each
448 px at the 1040x600 minsize with four buttons asking 364, and a fifth row in
Global Controls comes straight out of an editor viewport already down to 45 px.
So the entry is on the MENUBAR and on the Files listbox's right-click menu,
exactly as Freeze / Unfreeze and Save / Load Config are.

R3-2: DEFAULT FILE SCOPE, AND WHY THERE IS NO FILE COLUMN
---------------------------------------------------------
A connection table row's port cell is a `ttk.Combobox(width=7)`.  MEASURED on
this box with the real table inside the editor's scrolling canvas, at 100% and
at 150% font scaling (`tk scaling 2.0` + every named font x1.5):

    tightest Port cell (an `rlc_between` row -- the only Kind with two port
    fields):  72 px wide at 100%, 135 px at 150%, and SEVEN CHARACTERS of
    visible text either way (49 px / 112 px), because the cell is sized in
    characters and not in pixels.

    what a file tag costs of that budget, as a fraction, and how many digits
    of port number it leaves:

        F1. / F2.     33% / 34%      4 digits
        F10.          47% / 48%      3 digits
        EM.           45% / 44%      3 digits
        PKG.          55% / 56%      3 digits
        ABCD.         73% / 76%      1 digit

    and the case that decides it:  `23,24,25` is 48 px and fits the 49 px
    budget exactly; `F2.23,24,25` is 64 px, i.e. 131% of the cell, and scrolls.

So a tag on EVERY endpoint would put every real port group behind a horizontal
scroll, and a per-row file COLUMN is worse still -- measured, one file column
takes the table from 405 px to 451 and two to 497, against a 431 px viewport
whose measured headroom is 13 px.  Hence: the table has a HOME file, a bare
number means the home file, and a tag appears only on an endpoint that crosses
to another file.  Every existing spec, every golden case and every saved
session keeps its meaning unchanged, and a single-file user never sees a tag.

The aliases are `pkg_rlc_compose.default_alias` -- F1, F2, ... -- which is the
repo's own idiom from `_format_results_table`, so the alias in a port cell, the
alias in the results table's file column and the alias in a CLI `--compose`
report are one thing and not three.

R3-5: THE WELD, WHERE THE NUMBER IS READ
----------------------------------------
`reference_check` is mandatory output on the CLI.  A weld raises nothing and
makes no number look wrong -- measured in `pkg_rlc_compose`, the package ground
pad grounded / open / through 1 nH give L_eff = 2.1454 nH, bit-identical,
spread 0.000e+00 -- so it changes how a number must be READ, and it therefore
has to arrive where the number is read rather than in a report nobody opened.
`reference_strip_text` is ONE string (the `SIGN_CONVENTION_TEXT` rule) and it
reaches the files window, the Attribution window's strip and the copied
Attribution report verbatim.

WIRED: `pkg_rlc_gui` calls `reference_provenance` from `_snapshot_reference`,
so the verdict is FROZEN onto every `RowSnapshot` / `CouplingSnapshot` at
Calculate time and `_run_report_segments` prints it under the table it
qualifies -- in the Log and on every run page.  There is deliberately only ONE
printer: a second copy at compute time put the same paragraph on screen twice.

STILL NOT WIRED: the connection table's port cells do not go through
`render_port_cell` / `cell_scope` / `port_choices`.  What resolves a tagged cell
today is `pkg_rlc_validate._scope_port_field`, which is one `parse_scoped_ports`
call: the tag is PER-TOKEN there, so the single-cell SHORT group (`2,F2.1`,
`25,26,F2.15`) -- which has no other spelling in that table -- is an ordinary
field and needs no rule of its own.  If the cells are ever given a scope-aware
renderer, these two must be made one thing rather than two.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, fields
from tkinter import ttk
from typing import Optional, Sequence

import pkg_rlc.physics.compose as comp
from pkg_rlc.physics.compose import COMPOSE_TAG_SEP, ComposeError, default_alias
from pkg_rlc.physics.core import ROLE_PROBE_PLUS, collapse_ports
# Eight `import pkg_rlc_gui` statements written inside function bodies used to
# stand where these three lines do.  They were not a design; they were a dodge
# around a cycle, and the cycle is gone: the shared data model is
# `pkg_rlc_model`, the spec logic `pkg_rlc_validate`, the palette
# `pkg_rlc_widgets` and the log severities `pkg_rlc_report`, and all five sit
# BELOW this file in tests/test_layering.py.  Nothing here reaches up any more,
# so the imports are at the top where they can be read -- and a reader can now
# see what this window depends on without opening eight function bodies.
from pkg_rlc.model.trace import TraceConfig
from pkg_rlc.present.report import LOG_WARN
from pkg_rlc.model.validate import compose_spec_problems
# Aliased because this module exports a `trace_file_labels` of its OWN, which
# delegates to this one and falls back to a local walk -- see it below.
from pkg_rlc.model.validate import trace_file_labels as _live_trace_file_labels
from pkg_rlc.widgets.widgets import (
    PLACEHOLDER_FG,
    PORT_ROLE_FG,
    WARN_FG,
    _fixed_map_filter,
)

# `comp._split_tag` and `comp._ALIAS_RE` are reached by name on purpose.  They
# are the ONE definition of "does this token carry a file tag, and is that tag
# spellable" -- the same decision `parse_scoped_ports` takes on the read side.
# A second copy here would let the cell the user is looking at and the parser
# that resolves it disagree about whether `1x.4` is a tagged port, which is the
# drift this repo has been bitten by (RECIPROCITY_WARN, `_config_signature`).

__all__ = [
    "FILES_MENU_LABEL",
    "FILES_TITLE",
    "FileSlot",
    "FilePairWindow",
    "TRACE_FILES_FIELD",
    "TRACE_FILES_SUPPORTED",
    "PORT_CELL_CHARS",
    "PORT_CELL_TEXT_PX",
    "ALIAS_MAX_CHARS",
    "alias_tag",
    "alias_cost",
    "alias_budget_refusal",
    "alias_digits_left",
    "alias_budget_line",
    "spec_problems",
    "render_port_cell",
    "render_port_cells",
    "cell_scope",
    "cell_is_foreign",
    "port_choices",
    "alias_legend",
    "scope_hint",
    "cross_file_rows",
    "cross_file_summary",
    "slots_of",
    "home_alias",
    "trace_file_labels",
    "trace_files_supported",
    "TRACE_FILES_FIELD",
    "TRACE_HOME_FIELD",
    "files_refusal",
    "open_files_window",
    "refresh_files_windows",
    "live_windows",
    "reference_checks_of",
    "reference_strip_text",
    "reference_report_lines",
    "reference_provenance",
    "REFERENCE_HEADLINE",
    "REF_TAG",
]


# ===========================================================================
# Constants -- every number here was measured, and where
# ===========================================================================

FILES_MENU_LABEL = "Files in this trace…"
FILES_TITLE = "Files in this trace"

#: Default geometry.  Not a ReflowRow window: its controls are one short row
#: and its body is a Treeview, so pack's own "claim the fixed sections first"
#: rule is enough.  The floor is what keeps three file rows plus the strips on
#: screen -- see `_MIN_BODY_LINES`.
FILES_GEOMETRY = "720x420"
FILES_MIN_W = 520
FILES_MIN_H = 300

#: A connections-table port cell is `ColumnSpec("ports", "Port", 7, ...)`, i.e.
#: a `ttk.Combobox(width=7)`.  MEASURED with the real table, at its natural
#: width inside the editor's scrolling canvas (the canvas SCROLLS, it does not
#: squeeze, so the cell the user sees is the widget's own requested width):
#:
#:      scale   cell px   visible chars   text px   one digit
#:      100%      72          7             49         7 px
#:      150%     135          7            112        16 px
#:
#: The character count is what is stable across DPI, and it is what every rule
#: below is written in.  Re-measure with
#: `tests/test_multifile_table.py::TestPortCellBudget` before changing any of
#: the three constants.
PORT_CELL_CHARS = 7
PORT_CELL_TEXT_PX = 49          # at 100%; 112 px at 150%, same 7 characters

#: How many characters of alias a port cell can carry before the port NUMBER
#: stops fitting beside it.  A tag is `alias` + `COMPOSE_TAG_SEP`, so a 2-char
#: alias spends 3 of the 7 characters and leaves 4 -- measured as 33% of the
#: text budget at 100% and 34% at 150%, i.e. the fraction is DPI-independent
#: because both terms scale together.  At 4 characters the tag is 73% / 76% and
#: one digit is left, which is a port number the user cannot read without
#: scrolling a cell that has no scrollbar.  Refused, by name, in the one place
#: an alias can be typed.
#: The aliases a composition hands out with nobody choosing are F1, F2, ...
#: -- two characters up to nine files, which is the cheapest tag there is and
#: the only one that leaves four digits of port number.
ALIAS_MAX_CHARS = 3


# ===========================================================================
# R3-2: the port namespace as a table cell
# ===========================================================================

def alias_tag(alias: str) -> str:
    """'F2' -> 'F2.'  -- what a cross-file endpoint carries."""
    return f"{(alias or '').strip()}{COMPOSE_TAG_SEP}"


def alias_cost(alias: str) -> int:
    """
    Characters of the port cell a tag for this file spends.

    In CHARACTERS, not pixels: the cell is `width=7` and therefore sized in
    characters, and the measured pixel budget (49 px at 100%, 112 at 150%)
    divides by the digit width (7 px, 16 px) to the same seven either way.
    """
    return len(alias_tag(alias))


def alias_budget_refusal(alias: str) -> str:
    """
    "" when this alias FITS A PORT CELL, else the measured reason.

    DELIBERATELY ONLY THE BUDGET RULE.  Whether a tag is spellable at all, and
    whether two files claim the same one, belong to
    `pkg_rlc_validate.compose_spec_problems`
    -- it owns the file set and reports every structural fault in it, and a
    second copy of those two checks here is exactly the drift this repo
    documents (RECIPROCITY_WARN disagreeing between the GUI and the CLI, one
    file getting opposite verdicts).  What that function cannot know is how
    wide a connection table's port cell is, which is a pixel measurement and
    lives here beside the constant it produced.

    The refusal quotes the measured FRACTION and not a digit count, because the
    two do not agree for a letter-heavy tag: 'ABCD.' is five characters of a
    seven-character cell but 36 px of a 49 px text budget, i.e. ONE digit and
    not two.  Overstating it is the direction that hides the problem.
    """
    a = (alias or "").strip()
    if not a or len(a) <= ALIAS_MAX_CHARS:
        return ""
    return (f"'{a}' is {len(a)} characters, and a port cell shows about "
            f"{PORT_CELL_CHARS}: measured, a tag this long fills roughly "
            f"three quarters of the cell and leaves one digit of port number "
            f"visible, in a widget with no scrollbar and no overflow marker. "
            f"Use at most {ALIAS_MAX_CHARS} characters.")


def spec_problems(app, trace) -> list[str]:
    """
    Everything wrong with this trace's file set, structural THEN measured.

    `compose_spec_problems` is the owner of the first half and is imported from
    `pkg_rlc_validate` rather than reimplemented; this adds the one check it
    cannot make, which is whether each tag fits the cell it has to appear in.
    Never raises -- it is on the strips' path.
    """
    out: list[str] = []
    try:
        loaded = [f.label for f in getattr(app, "files", [])] \
            if app is not None else None
        out.extend(compose_spec_problems(trace, loaded))
    except Exception:
        pass
    try:
        for slot in slots_of(app, trace):
            msg = alias_budget_refusal(slot.alias)
            if msg:
                out.append(msg)
    except Exception:                                        # pragma: no cover
        pass
    return out


def alias_digits_left(alias: str, measure=None) -> int:
    """
    How many DIGITS of port number stay visible beside this tag.

    `measure` is a `tkfont.Font.measure`-shaped callable; the window passes its
    own font's so the answer is the one on screen, and a caller with no root
    gets the character count instead.  The two do not always agree and that is
    the point: the cell is sized in characters but the font is proportional, so
    'EM.' is two characters plus a dot yet 22 px of the measured 49 px budget,
    which is THREE digits and not four.  Quoting the character count as if it
    were digits overstates every letter-heavy alias by one.
    """
    if measure is None:
        return max(0, PORT_CELL_CHARS - alias_cost(alias))
    try:
        digit = measure("0") or 1
        return max(0, (PORT_CELL_TEXT_PX - measure(alias_tag(alias))) // digit)
    except Exception:                                        # pragma: no cover
        return max(0, PORT_CELL_CHARS - alias_cost(alias))


def alias_budget_line(alias: str, measure=None) -> str:
    """
    One line saying what this alias leaves for the port number.

    On screen because the consequence is invisible when it bites: a port cell
    has no scrollbar, so a tag that overflows it simply shows fewer characters,
    and `PKG.101` reads as `PKG.10` with nothing at all to say one was
    dropped.
    """
    left = alias_digits_left(alias, measure)
    if left <= 0:
        return (f"'{alias_tag(alias)}' fills the whole port cell; no port "
                f"number would be visible beside it.")
    return (f"'{alias_tag(alias)}' leaves room for {left} digits of port "
            f"number in a connection table's port cell.")


def render_port_cell(alias: str, locals_1based: Sequence[int],
                     home: str) -> str:
    """
    (file, its own port numbers) -> the text a port cell should hold.

    BARE when the file is the home file, tagged when it is not.  That is R3-2
    in one function: the tag is the exception, so an existing single-file spec
    renders byte-identically to what it always did.

    `collapse_ports` never emits a space -- the DSL is whitespace-tokenised and
    the port field is `parts[0]`, so `F2.40, 41` would parse as the field
    `F2.40,` with a stray `41` where the keyword belongs.  That is the same
    rule the Ports & Roles write-back is built on, and it is what makes this
    round-trip through `parse_scoped_ports`.
    """
    body = collapse_ports(sorted(locals_1based))
    if not body:
        return ""
    if (alias or "").strip().lower() == (home or "").strip().lower():
        return body
    return alias_tag(alias) + body


def render_port_cells(by_file: Sequence[tuple], home: str) -> list[str]:
    """
    [(alias, [local ports]), ...] -> ONE CELL TEXT PER FILE.

    A list and not a string, and that is the load-bearing part -- but the
    reason changed and is now a BUDGET rather than an impossibility.

    It used to be that a port field carried ONE scope: `parse_scoped_ports`
    refused a tag on any comma token after the first, so a set spanning two
    files could not be written into one cell AT ALL and a renderer returning a
    single string emitted exactly the spelling the parser refused.  The tag is
    per-token now, so `F1.1,F2.3` is legal and this is no longer impossible.

    What is unchanged is that it does not FIT.  A port cell is a
    `ttk.Combobox(width=7)` -- measured 72 px / 7 characters at 100% and
    135 px / 7 characters at 150%, the character count being the DPI-stable
    one -- and the widget has no scrollbar.  `23,24,25` is 48 px against a
    49 px budget; `F2.23,24,25` is 64 px, i.e. 131%, and simply scrolls out of
    sight.  So the caller still gets one cell per file and puts them on their
    own rows; joining them with ',' would produce a legal field the user
    cannot read.
    """
    out = []
    for alias, locals_ in by_file:
        text = render_port_cell(alias, locals_, home)
        if text:
            out.append(text)
    return out


def cell_scope(text: str, home: str, aliases: Sequence[str]) -> tuple:
    """
    (alias, body) for a port cell, with the home file filled in.

    Pure text: it answers "which file is this cell talking about" without
    resolving a single port, which is what lets the strips and this window run
    it on every keystroke and on half-typed input.  An unknown tag comes back
    as `(tag, body)` with the tag NOT in `aliases`, so a caller can tell "names
    a file I do not have" from "names no file at all" -- those are different
    mistakes and want different messages.

    A cell with no tag is the home file, which is the whole of R3-2.
    """
    lead, rest = comp._split_tag((text or "").strip())
    known = {(a or "").strip().lower(): (a or "").strip() for a in aliases}
    if not lead:
        return ((home or "").strip(), (text or "").strip())
    return (known.get(lead.lower(), lead), rest)


def cell_is_foreign(text: str, home: str, aliases: Sequence[str]) -> bool:
    """True when this cell crosses to a file other than the home one."""
    alias, _body = cell_scope(text, home, aliases)
    return bool(alias) and alias.strip().lower() != (home or "").strip().lower()


def port_choices(slots: Sequence["FileSlot"], home: str,
                 extra: Sequence[str] = ()) -> list[str]:
    """
    What the Port / To dropdowns offer on a composed trace.

    Order is the affordance: `extra` first (the merged-node names
    `_refresh_port_choices` already puts at the top -- referring to a node by
    its name is the gesture that does NOT multiply an element by N), then the
    HOME file's ports bare, then every other file's ports tagged.

    Home first and bare because that is the cell text a single-file spec has
    always had, so the list a one-file user sees is unchanged; and a ttk
    popdown is only as wide as its widget, which is the measured 7 characters,
    so a tagged entry is already at 4 digits of headroom and a NAME-bearing
    entry does not fit at all (that is the `Show Ports` / Ports & Roles
    decision, unchanged).
    """
    out = [str(e) for e in extra]
    ordered = ([s for s in slots if s.is_home(home)] +
               [s for s in slots if not s.is_home(home)])
    for slot in ordered:
        for p in slot.local_ports:
            out.append(render_port_cell(slot.alias, [p], home))
    return out


def alias_legend(slots: Sequence["FileSlot"], home: str = "") -> str:
    """
    'F1=coil.s16p   F2=package.s60p' -- `_format_results_table`'s own line.

    Literally its idiom: that function heads a multi-file results table with
    `f"{file_alias[fl]}={fl}"` joined by two spaces, and a second spelling of
    the same legend on another screen is how two surfaces come to disagree
    about which file F2 is.  The home file is marked, because "which one do I
    NOT have to type" is the question this window exists to answer.
    """
    parts = []
    for s in slots:
        mark = "  (home: type its ports bare)" if s.is_home(home) else ""
        parts.append(f"{s.alias}={s.label}{mark}")
    return "   ".join(parts)


def scope_hint(slots: Sequence["FileSlot"], home: str) -> str:
    """The one sentence that says how to type a port of each file."""
    if len(slots) < 2:
        return ("One file: a port cell is a bare port number, exactly as it "
                "always was.")
    others = [s.alias for s in slots if not s.is_home(home)]
    return (f"A port cell takes a BARE number for the home file; a port of "
            f"{' or '.join(others)} is written "
            f"{alias_tag(others[0])}<port> ({alias_tag(others[0])}12, "
            f"{alias_tag(others[0])}40-42).")


#: Which cells of a connection row are port fields.  `net` is a NAME, not a
#: port, and `R`/`L`/`C` are values -- running the scope rules over them would
#: report a lumped value as a file reference.
_PORT_KEYS = ("ports", "to")


def cross_file_rows(rows: Sequence, home: str,
                    aliases: Sequence[str]) -> list[tuple]:
    """
    [(row index, row kind, [(cell key, alias, body)])] for every crossing row.

    The point of section 0 in one function: "what you built is what you
    measure" makes the tool's first duty showing what was BUILT, and on a
    two-file trace the thing that is invisible is which rows are the ones
    joining the files.  A row is crossing when it names a file other than the
    home one in ANY port field -- including a row that names only the far file
    on both ends, which is a package-internal connection and not a link at all.
    Both are reported; the caller says which is which, because it is the
    `alias` that distinguishes them and this function returns it.

    Never raises: it is text, it runs on half-typed cells, and it is called
    from the strips' contract.
    """
    out = []
    for i, row in enumerate(rows):
        hits = []
        for key in _PORT_KEYS:
            text = str(getattr(row, key, "") or "").strip()
            if not text:
                continue
            alias, body = cell_scope(text, home, aliases)
            if alias and alias.strip().lower() != (home or "").strip().lower():
                hits.append((key, alias, body))
        if hits:
            out.append((i, str(getattr(row, "kind", "") or ""), hits))
    return out


def cross_file_summary(rows: Sequence, home: str,
                       aliases: Sequence[str]) -> list[str]:
    """One line per crossing row, plus a headline.  For the window's list."""
    hits = cross_file_rows(rows, home, aliases)
    if not rows:
        return []
    if not hits:
        return [f"No row crosses between files: every port cell is a port of "
                f"{home or 'the home file'}. The files are stacked but NOT "
                f"connected, so they are two separate networks sharing one "
                f"reference node."]
    out = [f"{len(hits)} of {len(rows)} connection rows cross between files:"]
    for i, kind, cells in hits:
        where = ", ".join(f"{k}={alias_tag(a)}{b}" for k, a, b in cells)
        out.append(f"  row {i + 1}  {kind or '(no kind)'}  {where}")
    return out


# ===========================================================================
# The trace's file list -- the ONE place that knows where it is stored
# ===========================================================================

#: The `TraceConfig` fields that carry the file set.  `pkg_rlc_model` owns the
#: schema and `pkg_rlc_validate.trace_file_labels` is the LIVE definition; these
#: names exist so this module can answer without it -- see `trace_file_labels`
#: below -- and so a refusal can say which field it is waiting for.
TRACE_FILES_FIELD = "file_labels"
TRACE_HOME_FIELD = "file_label"


def trace_file_labels(trace) -> list[str]:
    """
    Every file this trace is built from, HOME FIRST, deduplicated.

    DELEGATES to `pkg_rlc_validate.trace_file_labels`, which is the point: it
    is the live definition, `_config_signature`, the port descriptor, the CSV
    header and the plot legend all read it, and a second ANSWER here is how one
    surface comes to call a file F2 while another calls it F3.  It used to be
    reached through `pkg_rlc_gui` inside this function body, for no reason but
    the cycle that no longer exists; the delegation itself is unchanged.

    The walk below is the FALLBACK for a trace that has not got the schema, not
    a parallel implementation.  `TestFileListsAgree` runs both over the same
    battery and fails if they ever answer differently, so the fallback cannot
    drift silently.  Anything that changes the answer (stripping, sorting,
    keeping a repeat) changes on both sides or on neither; the normalising is
    done ON THE WAY IN, by `_TRACE_STRLIST_FIELDS`, and never here.
    """
    try:
        return list(_live_trace_file_labels(trace))
    except Exception:
        return _trace_file_labels_fallback(trace)


def _trace_file_labels_fallback(trace) -> list[str]:
    home = str(getattr(trace, TRACE_HOME_FIELD, "") or "")
    out = [home] if home else []
    for lbl in (getattr(trace, TRACE_FILES_FIELD, None) or []):
        lbl = str(lbl or "")
        if lbl and lbl not in out:
            out.append(lbl)
    return out


def trace_files_supported() -> bool:
    """True when `TraceConfig` really stores more than one file."""
    try:
        return TRACE_FILES_FIELD in {f.name for f in fields(TraceConfig)}
    except Exception:
        return False


@dataclass(frozen=True)
class FileSlot:
    """
    One file's place in a trace, resolved to plain values.

    Frozen and resolved, never a reference to the live `FileEntry`: this is the
    run-snapshot rule, and a window kept open across a Remove File would
    otherwise render a label off an object whose data is gone.
    """

    alias: str
    label: str
    nports: int
    z0: float
    npoints: int
    span: str
    loaded: bool = True
    local_ports: tuple = ()

    def is_home(self, home: str) -> bool:
        return self.alias.strip().lower() == (home or "").strip().lower()


def _slot_from_file_entry(alias: str, fe) -> FileSlot:
    ts = fe.ts
    return FileSlot(alias=alias, label=fe.label, nports=int(ts.nports),
                    z0=float(ts.z0), npoints=int(len(ts.freqs)),
                    span=ts.freq_span_str(), loaded=True,
                    local_ports=tuple(range(1, int(ts.nports) + 1)))


def _slot_missing(alias: str, label: str) -> FileSlot:
    return FileSlot(alias=alias, label=label, nports=0, z0=0.0, npoints=0,
                    span="", loaded=False, local_ports=())


def slots_of(app, trace) -> list[FileSlot]:
    """
    The trace's files as `FileSlot`s, tagged by POSITION -- F1, F2, ...

    The tag is the position, which is `pkg_rlc_validate.trace_file_aliases`'s rule
    and is what makes it stable: the home file is first, so it is F1 and stays
    F1 when a second file is added or removed.  A tag that moved would silently
    re-point every tagged port cell in the connection table at a different
    file.

    Never raises: it is called from the strips' path.  A label with no loaded
    file comes back with `loaded=False` rather than being dropped -- a session
    whose folder moved keeps its traces, `_on_calculate` already says
    `file '…' not loaded`, and a file silently missing from THIS list is a
    composition the user cannot see is broken.
    """
    out: list[FileSlot] = []
    try:
        labels = trace_file_labels(trace)
    except Exception:                                        # pragma: no cover
        return out
    for i, label in enumerate(labels):
        alias = default_alias(i)
        fe = None
        try:
            fe = app._file_by_label(label) if app is not None else None
        except Exception:                                    # pragma: no cover
            fe = None
        out.append(_slot_from_file_entry(alias, fe) if fe is not None
                   else _slot_missing(alias, label))
    return out


def home_alias(slots: Sequence[FileSlot]) -> str:
    """The home file's alias -- the FIRST slot's, home being first by rule."""
    return slots[0].alias if slots else default_alias(0)


# ===========================================================================
# R3-5: the reference-node check, where the number is read
# ===========================================================================

#: The tag in front of each verdict.  WIDTH-STABLE within the set that can
#: appear together, so a list of them does not ripple: 'WELD'/'ok'/'note'/'?'
#: are padded to one column here rather than at each call site, which is the
#: `_format_results_table` swatch rule.
REF_TAG = {
    comp.REF_WELDED: "WELD",
    comp.REF_LIVE: "ok",
    comp.REF_NO_GROUND: "note",
    comp.REF_UNKNOWN: "?",
}

REFERENCE_HEADLINE = (
    "REFERENCE-NODE CHECK -- is each file's ground network in the circuit? "
    "An n-port Touchstone Y already has its own reference eliminated, so "
    "stacking the files identifies their references at ZERO impedance. Each "
    "file's declared ground set is perturbed with a series inductor and the "
    "network re-solved: if the answer does not move AT ALL, that file's "
    "ground network is not in the circuit, and grounded / open / "
    "through-an-inductor are the same number."
)

#: What the strip says when there is a composition and nothing is wrong with
#: it.  Present rather than blank on purpose: a mandatory check that shows
#: nothing when it passes is indistinguishable from a check that did not run,
#: and this one costs two solves per file specifically so it can be trusted.
_REF_OK = "Reference-node check: every file's ground network is in the circuit."


def reference_checks_of(trace) -> list:
    """
    The `ReferenceCheck`s cached on a computed trace, or [].

    Defensive on purpose, and this is the second half of the seam
    `TRACE_FILES_FIELD` is the first half of: it accepts either a
    `ComposedSolution` (which carries `.reference`) or a bare list, under
    either of two attribute names, and returns [] for a single-file trace.
    [] means "no composition", which is what makes every surface below cost
    exactly zero pixels on a trace that has one file -- i.e. on every trace
    that exists today.
    """
    for attr in ("composed", "compose_solution"):
        obj = getattr(trace, attr, None)
        if obj is None:
            continue
        ref = getattr(obj, "reference", None)
        if ref is None and isinstance(obj, (list, tuple)):
            ref = obj
        if ref:
            return list(ref)
    ref = getattr(trace, "reference_checks", None)
    return list(ref) if ref else []


def reference_strip_text(checks: Sequence) -> tuple:
    """
    (one line, is-a-warning) for the strip beside the numbers.

    ONE LINE, leading with the verdict and the file, because every strip in
    this application clips rather than wraps (`wraplength=0`) and the front of
    the line is the part that must survive: a wrapping label is 21 px at 980
    and 38 px at 720, and the Attribution window's pane budget at its minimum
    size is 168 px for the whole split.  The full text of every verdict is in
    `reference_report_lines`, which the files window shows in full and the
    copied Attribution report carries verbatim.

    A weld outranks everything else: it is the one verdict that makes the
    numbers under it mean something other than they appear to.
    """
    checks = list(checks or ())
    if not checks:
        return ("", False)
    welded = [c for c in checks if getattr(c, "welded", False)]
    if welded:
        names = ", ".join(f"{c.alias} ({c.label})" for c in welded)
        return (f"WELD: {names} — that file's ground network is NOT in the "
                f"circuit. Grounded, open and through-an-inductor give the "
                f"SAME number, so every value below that depends on it is "
                f"independent of it. Bring the return path out as a PORT in "
                f"the file that owns it and connect it.", True)
    unknown = [c for c in checks if c.verdict == comp.REF_UNKNOWN]
    if unknown:
        names = ", ".join(c.alias for c in unknown)
        return (f"Reference-node check could not run for {names} — the "
                f"baseline is not finite, so 'did the answer move' has no "
                f"answer.", True)
    no_gnd = [c for c in checks if c.verdict == comp.REF_NO_GROUND]
    if no_gnd:
        names = ", ".join(c.alias for c in no_gnd)
        return (f"Reference-node check: {names} declares no ground port, so "
                f"its reference is the composed network's ground by "
                f"construction — right if that file's own reference IS the "
                f"system ground, wrong if its ground is supposed to reach the "
                f"system through one of its ports.", False)
    return (_REF_OK, False)


def reference_provenance(checks: Sequence) -> tuple:
    """
    ((strip text, is-a-warning), (full report lines)) -- BOTH renderings, once.

    One function because the two must not disagree: a strip saying the weld is
    the headline over a report that does not mention it, or the reverse, is the
    same class of defect as two definitions of `_config_signature`.  A caller
    freezes the pair onto its own provenance at compute time and never asks
    again, which is the run-snapshot rule -- `ReferenceCheck` objects are not
    frozen and a window kept open across a re-compose would otherwise print
    this run's numbers under the next composition's verdict.
    """
    strip = reference_strip_text(checks)
    if not strip[0]:
        return ((), ())
    return (strip, tuple(reference_report_lines(checks)))


def reference_report_lines(checks: Sequence) -> list[str]:
    """
    The whole check, for a report that cannot clip.

    Carries the headline as well as the verdicts: a reader who has just been
    told a ground network is not in the circuit needs to know WHY that can
    happen, and `REFERENCE_HEADLINE` is the same paragraph the CLI prints
    above the same verdicts.
    """
    checks = list(checks or ())
    if not checks:
        return []
    out = [REFERENCE_HEADLINE, ""]
    width = max(len(t) for t in REF_TAG.values())
    for c in checks:
        tag = REF_TAG.get(c.verdict, "?")
        out.append(f"{tag:<{width}}: {c.message}")
    return out


# ===========================================================================
# The window
# ===========================================================================

#: Every open FilePairWindow, per App.  A plain dict keyed by the App, the
#: `pkg_rlc_attrib_gui._LIVE` idiom, so `refresh_files_windows` has something
#: to walk and a destroyed window drops out of it.
_LIVE: dict = {}

#: Columns of the file list.  A read-only `ttk.Treeview` is the right widget
#: here for exactly the reason the Ports & Roles list is one and the RESULTS
#: table is not: nothing in it is edited in place, so the repo's ban -- which
#: is about the EDITABLE connection table having no cell editors -- does not
#: apply.  Both documented Treeview hazards are handled in `_install_style`.
FILES_COLUMNS = (
    ("alias", "Alias", 62, "w"),
    ("label", "File", 210, "w"),
    ("ports", "Ports", 56, "e"),
    ("z0", "Z0 Ω", 52, "e"),
    ("points", "Pts", 48, "e"),
    ("span", "Frequency span", 170, "w"),
)

#: The body must keep this many rows on screen at the declared minimum, or the
#: window opens showing a header and a scrollbar.  Three, because two files is
#: the case this exists for and the third row is what shows there is room for
#: one more.
_MIN_BODY_LINES = 3

#: A DERIVED ttk style name: dotted names inherit their parent's layout, so
#: this is a full Treeview whose rowheight (and only its rowheight) differs.
#: Reconfiguring "Treeview" itself would follow every Treeview ttk builds in
#: this interpreter -- including the Ports & Roles list.
_FILES_STYLE = "FilePair.Treeview"

FILES_HINT = (
    "A port cell takes a bare number for the home file. Set the home to the "
    "file you type most."
)


class FilePairWindow(tk.Toplevel):
    """
    Which files this trace is made of, which one a bare port number means,
    and whether each file's ground network is in the circuit.

    Modeless and NO `grab_set`: a modal Toplevel that outlives its opener
    blocks event delivery and `update()` never returns, which takes the GUI and
    the test suite down together (the documented style-picker hang).  It is
    read WHILE editing the connection table, which is the same reason
    `PortRolesWindow` is modeless, and it `transient`s for the same reason that
    one does -- it is a short read-while-editing panel, not a result that cost
    a Recompute.

    Every callback guards on `winfo_exists()`, nothing raises, and nothing
    writes a `TraceConfig` directly: the write-back goes through the App's own
    hooks, so auto-apply, the strips and the stale marker follow exactly as
    they do for a keystroke.
    """

    def __init__(self, app, trace):
        super().__init__(app)
        self.app = app
        self._trace = trace
        self.title(FILES_TITLE)
        self.transient(app)
        self.geometry(FILES_GEOMETRY)
        self.minsize(FILES_MIN_W, FILES_MIN_H)

        self._slots: list[FileSlot] = []
        self._home = ""

        # PACK ORDER.  pack allocates in call order and UNMAPS FROM THE END, so
        # every fixed-height section claims its space before the expanding one.
        # The footer first, so Close is unconditional; then the strips; then
        # the Treeview last with expand=True, which is what makes the LIST the
        # thing that gives up height when the window is dragged short.
        foot = ttk.Frame(self)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Close", command=self.destroy
                   ).pack(side=tk.RIGHT, padx=2)
        self.home_btn = ttk.Button(foot, text="Set as home",
                                   command=self._on_set_home)
        self.home_btn.pack(side=tk.RIGHT, padx=2)
        # LAST in the row, so at a narrow width it is the hint that goes and
        # never the buttons -- the PortRolesWindow footer's rule.
        self.foot_note = ttk.Label(foot, foreground=self._hint_fg(),
                                   wraplength=0, justify=tk.LEFT,
                                   text=FILES_HINT)
        self.foot_note.pack(side=tk.LEFT)

        # --- R3-5.  Packed at the BOTTOM, above the footer, and only when
        # there is something to say: a single-file trace has no composition, so
        # `reference_checks_of` returns [] and this label is never managed.
        # Measured: an unmanaged ttk.Label costs the window 0 px, and every
        # trace that exists today is single-file.
        self.ref_strip = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                   wraplength=0)
        self._ref_shown = False
        self._warn_fg = self._warn_colour()
        # '' is not "no colour", it is "whatever the ttk STYLE says", which is
        # the only way back to the theme default once a warning has painted a
        # Label orange.
        self._ok_fg = ""

        # --- the header and the legend, at the TOP and fixed.
        self.header = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                wraplength=0)
        self.header.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))
        self.legend = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                wraplength=0)
        self.legend.pack(side=tk.TOP, fill=tk.X, padx=8)
        self.scope = ttk.Label(self, anchor="w", justify=tk.LEFT,
                               wraplength=0, foreground=self._hint_fg())
        self.scope.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))

        # --- the list.
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        self._install_style(self)
        vsb = ttk.Scrollbar(body, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            body, style=_FILES_STYLE, selectmode="browse",
            columns=[c[0] for c in FILES_COLUMNS], show="headings",
            height=_MIN_BODY_LINES, yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.tree.yview)
        for key, title, width, anchor in FILES_COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=True)
        self.tree.tag_configure("home", foreground=self._home_fg())
        self.tree.tag_configure("warn", foreground=self._warn_fg)

        # --- Add / Remove live on a RIGHT-CLICK MENU, and that is a measured
        # decision, not a preference.  MEASURED at the 520x300 minimum, real
        # widgets, this window's own footer (504 px of usable width):
        #
        #     100%   Close 87 + Set as home 87 + Remove 87 + Add 87 + a file
        #            combobox 149 = 517 px asked of 504, and the status label
        #            was already UNMAPPED (winfo_ismapped 0)
        #     150%   the same five ask 1075 px of 504: `Add` and the combobox
        #            unmapped, `Remove` clipped to 120 px of the 186 it asked
        #
        # i.e. exactly the Files-row / Traces-row failure this window exists on
        # the menubar to avoid, reproduced one level down.  A menu costs zero
        # pixels, and it is the house pattern for precisely this case (Freeze /
        # Unfreeze on the Traces list, Close this run on the tab strip).
        #
        # It SELECTS THE ROW UNDER THE POINTER first: a menu acting on the
        # previous selection is how you remove the wrong file.
        self._menu = tk.Menu(self, tearoff=0)
        self.tree.bind("<Button-3>", self._on_right_click)
        # NOT registered with the App's wheel router: "Treeview" is in
        # App._WHEEL_OWNERS, so _route_wheel bails out over it and Tk's own
        # class binding scrolls it.  A handler here would be dead code.

        # --- the cross-file connection summary, BELOW the list and above the
        # strips, because it is about the rows in the editor and not about the
        # files in the tree.  One line, clipping, for the strip reason.
        self.cross = ttk.Label(self, anchor="w", justify=tk.LEFT,
                               wraplength=0, foreground=self._hint_fg())
        self.cross.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(2, 0))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Destroy>", self._on_destroy)
        self.refresh()

    # ------------------------------------------------------------- palette

    def _measure(self, text: str) -> int:
        """
        The connection table's own font, measuring the connection table's own
        cell.  `TkDefaultFont` is what `ttk.Combobox` uses and what every
        measurement in this module's docstring was taken in, so the width this
        window quotes is the width the cell has.
        """
        return tkfont.nametofont("TkDefaultFont", root=self).measure(text)

    # THE PALETTE, straight out of pkg_rlc_widgets.  All three used to import
    # pkg_rlc_gui inside the method and fall back to a hard-coded hex string if
    # that raised -- a fallback whose only trigger was the import cycle, which
    # no longer exists.  A duplicated colour that appears silently when a
    # lookup fails is the shape of drift this repo has been bitten by, so the
    # fallbacks went with the imports: there is one spelling of each colour.

    @staticmethod
    def _hint_fg() -> str:
        return PLACEHOLDER_FG

    @staticmethod
    def _warn_colour() -> str:
        return WARN_FG

    @staticmethod
    def _home_fg() -> str:
        # The probe colour, from the palette the user already learned in Ports
        # & Roles: the home file is the one a bare port number reaches, which
        # is the same "this is the thing being addressed" the probe rows mean.
        return PORT_ROLE_FG[ROLE_PROBE_PLUS]

    @staticmethod
    def _install_style(master=None) -> None:
        """
        Both documented Treeview hazards, and both fail SILENTLY otherwise.

        Row height is frozen at 20 px whatever `tk scaling` and whatever font
        the style carries, so it is set from the font's own metrics -- on a
        DERIVED style name, never by reconfiguring the global `Treeview`, which
        would reach every Treeview in the process.  And tag colours are ignored
        on Tk builds whose `Style().map("Treeview", …)` carries
        `('!disabled', '!selected')` specs, which match every ordinary row and
        outrank the tag.

        `master` is taken rather than defaulted because `ttk.Style()` binds to
        whatever `tkinter._default_root` happens to be, and `style.configure`
        fires `<<ThemeChanged>>` at every widget of that interpreter: called
        after the root it was built against is gone, Tcl prints
        `can't invoke "event" command: application has been destroyed` to a
        console a double-clicked GUI does not have.
        """
        style = ttk.Style(master)
        font = tkfont.nametofont("TkDefaultFont", root=master)
        style.configure(_FILES_STYLE, rowheight=font.metrics("linespace") + 4)
        # `_fixed_map_filter` is pkg_rlc_widgets', imported at the top of this
        # file.  It used to be fetched from pkg_rlc_gui inside this method, in
        # a try/except that RETURNED on failure -- so an import error left the
        # tag colours silently not applied, which is the very symptom the
        # workaround exists to prevent.  There is no import to fail now.
        fix = _fixed_map_filter
        style.map(_FILES_STYLE,
                  foreground=fix(style.map("Treeview",
                                           query_opt="foreground")),
                  background=fix(style.map("Treeview",
                                           query_opt="background")))

    # ---------------------------------------------------------------- data

    def refresh(self) -> None:
        """
        Re-render from live state.  NEVER RAISES -- the strips' contract.

        Called from `refresh_files_windows`, which `_apply_editor_strips`
        calls, i.e. from a Tk variable trace on every keystroke.  An error
        raised there reaches no handler anyone controls: Tk prints it to a
        console a double-clicked GUI does not have and this window carries on
        showing a stale file list, which is the one thing it must not do.
        """
        try:
            self._refresh_impl()
        except Exception as e:                               # pragma: no cover
            try:
                self.header.configure(text=f"could not read the file list: {e}",
                                      foreground=self._warn_fg)
            except Exception:
                pass

    def _refresh_impl(self) -> None:
        if not self.winfo_exists():
            return
        trace = self._trace
        self._slots = slots_of(self.app, trace)
        self._home = home_alias(self._slots)
        nports = sum(s.nports for s in self._slots)
        label = str(getattr(trace, "label", "") or "")
        tid = getattr(trace, "id", "?")
        n = len(self._slots)
        self.header.configure(
            text=(f"Trace [{tid}] {label} — "
                  f"{n} file{'s' if n != 1 else ''}, {nports} ports"),
            foreground=self._ok_fg)
        self.legend.configure(text=alias_legend(self._slots, self._home))
        # The scope sentence, plus what a tag costs the cell it goes in --
        # measured in the cell's OWN font, via the callable, so the number on
        # screen is the number on screen and not a character count.
        hint = scope_hint(self._slots, self._home)
        away = [s for s in self._slots if not s.is_home(self._home)]
        if away:
            hint += "  " + alias_budget_line(away[0].alias, self._measure)
        self.scope.configure(text=hint)

        self.tree.delete(*self.tree.get_children())
        for s in self._slots:
            tags = []
            if s.is_home(self._home):
                tags.append("home")
            if not s.loaded:
                tags.append("warn")
            kind = ("home" if s.is_home(self._home) else "linked")
            self.tree.insert(
                "", "end", iid=s.alias,
                values=(f"{s.alias}  {kind}", s.label,
                        s.nports if s.loaded else "—",
                        f"{s.z0:g}" if s.loaded else "—",
                        s.npoints if s.loaded else "—",
                        s.span if s.loaded else "not loaded"),
                tags=tuple(tags))

        self._refresh_cross()
        self._refresh_reference()
        self.home_btn.state(["!disabled"] if len(self._slots) > 1
                            else ["disabled"])

        # Everything wrong with the file set, structural first.  It goes on the
        # footer's own status line rather than a strip of its own: the footer
        # is the one always-visible pixel of this window, and a fifth fixed
        # section would come straight out of the list's height at the declared
        # minimum -- the same budget argument as the editor's footer.
        problems = spec_problems(self.app, trace)
        if problems:
            more = f"  (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            self._say(problems[0] + more, True)
        elif len(self._slots) > 1:
            self._say(FILES_HINT, False)

    def _refresh_cross(self) -> None:
        rows = list(getattr(self._trace, "conn_rows", None) or [])
        aliases = [s.alias for s in self._slots]
        lines = cross_file_summary(rows, self._home, aliases)
        if not lines:
            self.cross.configure(text="")
            return
        # ONE line: the headline, plus the first crossing row.  The rest is in
        # the editor, one row per line, which is where a reader can act on it.
        text = lines[0] if len(lines) == 1 else f"{lines[0]}{lines[1]}"
        self.cross.configure(text=text)

    def _refresh_reference(self) -> None:
        """
        R3-5.  Packed only when there IS a composition to report.

        `pack_forget` and not an empty string: a managed ttk.Label with no text
        still asks for a line of height, and a single-file trace -- which is
        every trace in every session that exists today -- must pay nothing at
        all for a check that has nothing to say about it.
        """
        checks = reference_checks_of(self._trace)
        text, warn = reference_strip_text(checks)
        if not text:
            if self._ref_shown:
                self.ref_strip.pack_forget()
                self._ref_shown = False
            return
        self.ref_strip.configure(
            text=text, foreground=self._warn_fg if warn else self._ok_fg)
        if not self._ref_shown:
            # BOTTOM, so it sits above the footer that was packed first.
            self.ref_strip.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(2, 0))
            self._ref_shown = True

    def report_lines(self) -> list[str]:
        """Everything this window says, for a report that cannot clip."""
        out = [f"Files in trace [{getattr(self._trace, 'id', '?')}] "
               f"{getattr(self._trace, 'label', '')}",
               alias_legend(self._slots, self._home),
               scope_hint(self._slots, self._home)]
        rows = list(getattr(self._trace, "conn_rows", None) or [])
        out.extend(cross_file_summary(rows, self._home,
                                      [s.alias for s in self._slots]))
        ref = reference_report_lines(reference_checks_of(self._trace))
        if ref:
            out.append("")
            out.extend(ref)
        return out

    # ------------------------------------------------------------- actions

    def _on_set_home(self) -> None:
        """
        Make the selected file the one a bare port number means.

        THROUGH THE APP'S OWN HOOK, never by writing the `TraceConfig`: the
        editor owns the file field, so a value poked onto the trace is
        overwritten by the next `_sync_editor_to_trace`.  The hook is looked up
        rather than assumed, and its absence is reported BY NAME -- a button
        that silently does nothing is a bug report.
        """
        if not self.winfo_exists():                          # pragma: no cover
            return
        sel = self.tree.selection()
        if not sel:
            self._say("Select a file in the list first.", True)
            return
        slot = next((s for s in self._slots if s.alias == sel[0]), None)
        if slot is None:                                     # pragma: no cover
            return
        if slot.is_home(self._home):
            self._say(f"{slot.alias} is already the home file.", False)
            return
        if not trace_files_supported():
            self._say(
                f"This build stores ONE file per trace ('{TRACE_FILES_FIELD}' "
                f"is not a field of TraceConfig here), so there is no second "
                f"file to make home.", True)
            return
        setter = getattr(self.app, "set_trace_home_file", None)
        if setter is None:
            # NAMED, not silent.  A button that does nothing is a bug report;
            # a button that says which hook it is waiting for is a work item.
            # The write-back deliberately goes through the App rather than into
            # the TraceConfig: the editor owns the File field, so a value poked
            # onto the trace is overwritten by the next `_sync_editor_to_trace`
            # -- the same rule the Ports & Roles write-back follows.
            self._say(
                "Changing the home file needs App.set_trace_home_file, which "
                "this build does not have. Set it from the editor's File "
                "field instead.", True)
            return
        try:
            setter(self._trace, slot.label)
        except Exception as e:
            self._say(f"could not set the home file: {e}", True)
            return
        self.refresh()
        self._say(f"{slot.alias} is now the home file: its ports are typed "
                  f"bare.", False)

    # ------------------------------------------------- the right-click menu

    def _candidate_files(self) -> list[str]:
        """Loaded files this trace does not already use, in the Files order."""
        have = {s.label for s in self._slots}
        try:
            return [f.label for f in getattr(self.app, "files", [])
                    if f.label not in have]
        except Exception:                                    # pragma: no cover
            return []

    def _on_right_click(self, event) -> None:
        """Select the row under the pointer, then offer what applies to it."""
        if not self.winfo_exists():                          # pragma: no cover
            return
        try:
            iid = self.tree.identify_row(event.y)
            if iid:
                self.tree.selection_set(iid)
            slot = next((s for s in self._slots if s.alias == iid), None)
            self._menu.delete(0, tk.END)
            self._menu.add_command(
                label="Set as home file", command=self._on_set_home,
                state=(tk.NORMAL if slot is not None
                       and not slot.is_home(self._home) else tk.DISABLED))
            self._menu.add_command(
                label="Remove from this trace",
                command=lambda: self._on_remove_file(slot),
                # The home file is never removable HERE: it is the file the
                # editor's own File field owns, and a trace with no home file
                # has no meaning for a bare port number.  Change the home
                # first, then remove what was the home.
                state=(tk.NORMAL if slot is not None
                       and not slot.is_home(self._home)
                       and len(self._slots) > 1 else tk.DISABLED))
            self._menu.add_separator()
            cands = self._candidate_files()
            if cands:
                sub = tk.Menu(self._menu, tearoff=0)
                for label in cands:
                    sub.add_command(
                        label=label,
                        command=lambda lb=label: self._on_add_file(lb))
                self._menu.add_cascade(label="Add a file…", menu=sub)
            else:
                self._menu.add_command(
                    label="Add a file…  (every loaded file is already here)",
                    state=tk.DISABLED)
            self._menu.tk_popup(event.x_root, event.y_root)
        except Exception:                                    # pragma: no cover
            pass
        finally:
            try:
                self._menu.grab_release()
            except Exception:                                # pragma: no cover
                pass

    def _on_add_file(self, label: str) -> None:
        self._apply_file_set("add", label)

    def _on_remove_file(self, slot) -> None:
        if slot is None:                                     # pragma: no cover
            return
        self._apply_file_set("remove", slot.label)

    def _apply_file_set(self, action: str, label: str) -> None:
        """
        Add or remove a file, through the App's hook when it has one.

        `file_labels` has NO widget in the editor -- the File combobox owns
        `file_label`, the HOME file, and nothing else -- so writing it here is
        not the "poked value overwritten by the next `_sync_editor_to_trace`"
        hazard that `set_trace_home_file` exists to avoid.  What it still owes
        is the bookkeeping an edit to the spec owes: the drawn curve is now
        older than the trace that describes it, the Traces list renders the
        file count, and the user is told the TAGS RENUMBERED, because a tag is
        a POSITION -- removing F2 of three makes the old F3 into F2, and every
        `F3.<port>` already typed now names a different file.

        The hook is preferred and looked up BY NAME so that when `pkg_rlc_gui`
        grows one this becomes a two-line delegation; the fallback is marked so
        it is deleted rather than left to drift.
        """
        if not self.winfo_exists():                          # pragma: no cover
            return
        tc = self._trace
        if getattr(tc, "frozen", False):
            self._say("This is a frozen snapshot: its files cannot be "
                      "changed, or its numbers and the spec printed beside "
                      "them would stop describing each other.", True)
            return
        hook = getattr(self.app, f"{action}_trace_file", None)
        if hook is not None:
            try:
                hook(tc, label)
            except Exception as e:
                self._say(f"could not {action} {label}: {e}", True)
                return
        elif not trace_files_supported():
            self._say(
                f"This build stores ONE file per trace "
                f"('{TRACE_FILES_FIELD}' is not a field of TraceConfig here), "
                f"so a file cannot be {action}ed.", True)
            return
        else:
            before = list(trace_file_labels(tc))
            rest = [lb for lb in before[1:] if lb != label]
            if action == "add":
                rest.append(label)
            tc.file_labels = rest
            if list(trace_file_labels(tc)) == before:
                self._say(f"{label} was already in this trace.", False)
                return
            # The spec moved, so the drawn curve is older than it -- the same
            # rule `_apply_editor_sync` and `set_trace_home_file` follow.
            if getattr(tc, "Z", None) is not None:
                tc.stale = True
            for name in ("_refresh_trace_list", "_refresh_port_roles_window"):
                fn = getattr(self.app, name, None)
                if fn is not None:
                    try:
                        fn()
                    except Exception:                        # pragma: no cover
                        pass
        self.refresh()
        legend = alias_legend(self._slots, self._home)
        verb = "added to" if action == "add" else "removed from"
        note = (f"{label} {verb} this trace. File tags are now {legend} — a "
                f"tag is a POSITION, so a tagged port cell written before this "
                f"may now name a different file.")
        self._say(note, True)
        # ALSO in the Results pane, at WARN: the footer line is one window's
        # status and scrolls away with the next click, while "a tagged port
        # cell may now name a different file" is exactly the kind of silent
        # re-pointing this feature exists to prevent.  `set_trace_home_file`
        # reports its own swap the same way and at the same severity.
        # The try/except stays, and it is NOT about the import any more: it
        # guards the reach into the App, which may be absent or may not carry
        # `_append_result` at all (this window is constructed directly in
        # several tests).  LOG_WARN is pkg_rlc_report's, imported at the top.
        try:
            self.app._append_result(
                f"  [{getattr(tc, 'id', '?')}] {getattr(tc, 'label', '')}: "
                f"{note}", LOG_WARN)
        except Exception:                                    # pragma: no cover
            pass

    def _say(self, text: str, warn: bool) -> None:
        try:
            self.foot_note.configure(
                text=text, foreground=self._warn_fg if warn
                else self._hint_fg())
        except Exception:                                    # pragma: no cover
            pass

    def _on_destroy(self, event=None) -> None:
        # A Toplevel is a bindtag of every widget inside it, so this fires for
        # every descendant's <Destroy> as well; only the window's own is about
        # the window going away.
        if event is not None and getattr(event, "widget", None) is not self:
            return
        for wins in _LIVE.values():
            if self in wins:
                wins.remove(self)


# ===========================================================================
# The hook surface
# ===========================================================================

def files_refusal(trace) -> Optional[str]:
    """None when the window can open, else the reason -- ready to show."""
    if trace is None:
        return ("Select a trace first: the file list belongs to a trace, not "
                "to the application.")
    return None


def live_windows(app) -> list:
    return [w for w in _LIVE.get(app, []) if w.winfo_exists()]


def open_files_window(app, trace) -> Optional["FilePairWindow"]:
    """
    Open, or raise, THE window for this trace.

    One per trace, not one per click: a second copy of a read-only panel is
    two things to keep in sync and two things to close.
    """
    for win in live_windows(app):
        if win._trace is trace:
            win.refresh()
            win.lift()
            win.focus_set()
            return win
    win = FilePairWindow(app, trace)
    _LIVE.setdefault(app, []).append(win)
    return win


def refresh_files_windows(app) -> None:
    """
    Re-render every open window.  NEVER RAISES -- `_apply_editor_strips`.

    Coalescing is INHERITED rather than repeated: this is called from
    `_apply_editor_strips`, which is itself `after_idle`-coalesced, so a timer
    here would only add latency to a Treeview repopulate.
    """
    for win in list(_LIVE.get(app, [])):
        try:
            if win.winfo_exists():
                win.refresh()
            else:
                _LIVE[app].remove(win)
        except Exception:                                    # pragma: no cover
            pass


# ===========================================================================
# Round-trip helpers used by the tests and by whoever wires the table
# ===========================================================================

def resolve_cell(text: str, net, home: str) -> list:
    """
    A port cell -> 1-based GLOBAL ports, through `parse_scoped_ports`.

    A thin wrapper and deliberately so: ONE parser, one set of error messages,
    one thing for the tests to pin.  It exists only to make the default scope
    explicit at the call site -- `default=home` is the whole of R3-2 on the
    read side, and a call site that forgot it would refuse every bare number
    on a composed trace with a message about naming a file.
    """
    return comp.parse_scoped_ports(text, net, default=home)


def cell_round_trip_ok(text: str, net, home: str) -> bool:
    """
    Does rendering what a cell resolves to give the cell back?

    The property `render_port_cell` has to have and the one a test can state
    without a fixture: `collapse_ports` normalises (`1,2,3` -> `1-3`), so the
    round trip is on the RESOLVED PORTS and not on the text.
    """
    try:
        ports = resolve_cell(text, net, home)
    except (ComposeError, ValueError):
        return False
    if not ports:
        return not (text or "").strip()
    by_alias: dict = {}
    for g in ports:
        block, local = net.local_of(g)
        by_alias.setdefault(block.alias, []).append(local)
    if len(by_alias) != 1:
        return False
    alias, locals_ = next(iter(by_alias.items()))
    again = render_port_cell(alias, locals_, home)
    try:
        return resolve_cell(again, net, home) == ports
    except (ComposeError, ValueError):                       # pragma: no cover
        return False
