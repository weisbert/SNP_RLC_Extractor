"""
pkg_rlc_help.py  --  In-app help content + Help window.

A self-contained reference, one tab per TASK: setting up a measurement
(the two tables), coupling, the trace model, comparing files -- each with
its physical assumptions, inputs, result interpretation, common use cases
and pitfalls. Opened from the GUI's "Help" button.

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
#
# THREE dirnames, not one: this module is `pkg_rlc/present/help.py`, so the
# install root -- the directory `docs/help/` sits in -- is three levels up.
# It was one level when the module was `pkg_rlc_help.py` at the root, and
# getting this wrong ships a Help window with nothing in it.
_INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HELP_DIR = os.path.join(_INSTALL_ROOT, "docs", "help")


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
    offline user with a broken install needs the other tabs more than
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


# One name per tab.  Until 2026-10-02 there were five MODE tabs here
# (HELP_MODE1/2/3/5/6, from mode1..mode6.md); the editor became ONE row model
# (docs/design_workspaces.md § 3) and the tabs were regrouped by task: the
# old Mode 1/2/3/5 prose is "Setting up a measurement", Mode 6's coupling and
# attribution prose is "Coupling", and its trace-model section is a tab of
# its own.  Nothing in the repo read the HELP_MODE* names, so they went with
# their files rather than surviving as aliases to text that no longer exists.
HELP_OVERVIEW = _help_text("overview.md")
HELP_FILES = _help_text("reading_files.md")
HELP_SESSION = _help_text("save_load.md")
HELP_SETUP = _help_text("setup.md")
HELP_COUPLING = _help_text("coupling.md")
HELP_TRACE_MODEL = _help_text("trace_model.md")
HELP_COMPARE = _help_text("compare_files.md")
HELP_SYNTAX = _help_text("input_syntax.md")
HELP_WORKFLOWS = _help_text("worked_examples.md")


# The tab ORDER is the reading order and is not alphabetical.  Do not add,
# remove, rename or reorder an entry without re-reading HELP_WINDOW_WIDTH's
# comment below -- a rename changes the tab strip's width just as an addition
# does.
HELP_TOPICS = [
    ("Overview",                 HELP_OVERVIEW),
    ("Reading files",            HELP_FILES),
    ("Save / Load",              HELP_SESSION),
    ("Setting up a measurement", HELP_SETUP),
    ("Coupling",                 HELP_COUPLING),
    ("Trace model",              HELP_TRACE_MODEL),
    ("Compare files",            HELP_COMPARE),
    ("Input syntax",             HELP_SYNTAX),
    ("Worked examples",          HELP_WORKFLOWS),
]


# 1010, not the historical 950.  A ttk.Notebook does NOT wrap or scroll its tab
# strip -- it CLIPS it, so a tab that does not fit is simply unreachable, and
# the one that goes is the LAST ("Worked examples") with nothing on screen to
# say so.  Measured (Microsoft YaHei UI 9, tk scaling 1.333): the old ten tabs
# (five of them "Mode N (...)") needed 968 px; the nine task tabs above need
# 812 px (re-measured 2026-10-02), so the headroom is 198 px.  The width is
# KEPT at 1010 rather than shrunk to fit: it is also the width of the text,
# and the prose is laid out for it.  The headroom is not an invitation -- the
# "eleventh Help tab" is a rejected proposal (docs/conventions/rejected_ui.md)
# and design_workspaces.md § 3.7 caps the set at ten.
# tests/test_session.py::TestHelpTabsAllFit re-measures it; add or rename a
# tab and it tells you whether the window has to grow.
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
