"""
pkg_rlc_help.py  --  In-app help content + Help window.

A self-contained reference describing each measurement mode's
physical assumptions, input fields, result interpretation, common
use cases, and pitfalls. Opened from the GUI's "Help" button.

The PROSE lives in `docs/help/*.md`, one file per tab, and is read at import
time by `_help_text`.  It used to be ten triple-quoted string constants in this
file -- 2295 of its 2745 lines -- and prose in a .py file is prose nobody
diffs: the same sentences also appear in README.md, docs/theory.md and several
source docstrings, and CLAUDE.md carries rules of the form "keep the six in
sync".  The files are plain text with a .md extension and the renderer is a
file read: there is no markdown library here and the window draws no
formatting it did not draw before.

They ship to the red zone automatically -- `.gitattributes` is a BLACKLIST for
`git archive` and neither `docs/` nor `*.md` is `export-ignore`d.  Do not add
one.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


# Absolute, derived from this file rather than from the process's working
# directory: the GUI is launched by double-click and from a shortcut, and
# neither guarantees a cwd anywhere near the install.
HELP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "help")


def _help_text(slug: str) -> str:
    """
    Read one tab's body out of `docs/help/`.

    Two details are load-bearing.  The encoding is named EXPLICITLY -- this
    tool is used on Windows boxes whose locale encoding is GBK, and the tabs
    carry Ohm, +/- and check-mark glyphs that decode to mojibake or raise
    there.  And the read is in TEXT mode with universal newlines, so a working
    tree checked out with CRLF (or a file hand-edited on Windows) still yields
    exactly the LF text the window used to hold as a literal.

    A missing or unreadable file costs its own TAB, never the window: an
    offline user with a broken install needs the other nine tabs more than
    they need a traceback.  Same rule as the session loader's "a bad value
    costs its own field, never the file".  `UnicodeDecodeError` is caught
    alongside `OSError` and is NOT redundant -- it is a `ValueError`, so a
    file truncated mid-codepoint or re-saved by an editor in the local
    codepage would otherwise take the whole window down on the one platform
    where that is most likely.
    """
    path = os.path.join(HELP_DIR, slug)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return (
            f"help content not found: {path}\n"
            f"\n"
            f"({exc})\n"
            f"\n"
            f"The other tabs are unaffected.  This file ships with the tool;\n"
            f"if it is missing, the install is incomplete.\n"
        )


# The ten names below held the prose as triple-quoted literals until it moved
# into `docs/help/`.  They are kept, bound to exactly the same text, for the
# same reason `pkg_rlc_gui` re-exports the DSL helpers that moved into
# `pkg_rlc_core`: `pkg_rlc_help.HELP_MODE6` goes on resolving for anything that
# reads it, and HELP_TOPICS below is byte-for-byte the list it always was.
HELP_OVERVIEW = _help_text("overview.md")
HELP_FILES = _help_text("reading_files.md")
HELP_SESSION = _help_text("save_load.md")
HELP_MODE1 = _help_text("mode1.md")
HELP_MODE2 = _help_text("mode2.md")
HELP_MODE3 = _help_text("mode3.md")
HELP_MODE5 = _help_text("mode5.md")
HELP_MODE6 = _help_text("mode6.md")
HELP_SYNTAX = _help_text("input_syntax.md")
HELP_WORKFLOWS = _help_text("worked_examples.md")


# The tab ORDER is the reading order and is not alphabetical.  Do not add,
# remove, rename or reorder an entry without re-reading HELP_WINDOW_WIDTH's
# comment below -- a rename changes the tab strip's width just as an addition
# does.
HELP_TOPICS = [
    ("Overview",        HELP_OVERVIEW),
    ("Reading files",   HELP_FILES),
    ("Save / Load",     HELP_SESSION),
    ("Mode 1 (->GND)",  HELP_MODE1),
    ("Mode 2 (A<->B)",  HELP_MODE2),
    ("Mode 3 (+Short)", HELP_MODE3),
    ("Mode 5 (Custom)", HELP_MODE5),
    ("Mode 6 (Coupling)", HELP_MODE6),
    ("Input syntax",    HELP_SYNTAX),
    ("Worked examples", HELP_WORKFLOWS),
]


# 1010, not the historical 950.  A ttk.Notebook does NOT wrap or scroll its tab
# strip -- it CLIPS it, so a tab that does not fit is simply unreachable, and
# the one that goes is the LAST ("Worked examples") with nothing on screen to
# say so.  Measured (Microsoft YaHei UI 9): nine tabs need 891 px and ten need
# 968, so the tenth did not fit the old width at all.  Headroom now 42 px --
# NOT enough for an eleventh.  tests/test_session.py::TestHelpTabsAllFit
# re-measures it; add a tab and it tells you whether the window has to grow.
HELP_WINDOW_WIDTH = 1010


class HelpWindow(tk.Toplevel):
    """A tabbed reference window. One tab per topic."""

    def __init__(self, master):
        super().__init__(master)
        self.title("PKG RLC Extractor -- Help")
        self.geometry(f"{HELP_WINDOW_WIDTH}x650")

        nb = ttk.Notebook(self)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        for title, body in HELP_TOPICS:
            frame = ttk.Frame(nb)
            nb.add(frame, text=title)
            txt = ScrolledText(frame, wrap=tk.WORD, font=("Consolas", 10))
            txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            txt.insert("1.0", body)
            txt.configure(state="disabled")
