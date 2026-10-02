"""
The workspace switch: ONE main window, several tasks, one strip to pick the
task (L5).

`docs/design_workspaces.md` § 1.  The owner's complaint was that the trace
model took five steps, three of them with nothing on screen saying so, and
that nobody looking for "the loading of a PN signal" would think to open an
Analyze menu.  So the tool is divided by TASK: a full-width strip of
`ttk.Radiobutton`s in the Toolbutton style sits under the menubar, and each
button shows one WORKSPACE -- a left region under the shared Loaded Files
panel, and the whole right side of the outer PanedWindow.

What stays put and what swaps:

  * The Loaded Files panel is SHARED.  Every workspace reads the same files,
    so it is built once, packed at the top of the left column, and never
    touched here.  `App.files_lb` is the same widget in every workspace.
  * Below it, `left_host` is the one place a workspace's left frame is packed
    into.  Showing a workspace `pack_forget`s the outgoing frame and packs the
    incoming one; the frames are siblings inside the host.
  * The right side is the outer PanedWindow's SECOND PANE.  Showing a
    workspace `forget`s the outgoing pane and `add`s the incoming one, and the
    pane's own widget tree is untouched -- the RLC workspace's vertical
    PanedWindow (results + plot) is the same object before and after, and its
    Tk master is still the outer PanedWindow.  That is what keeps the tests
    that walk `results_text -> TPanedwindow -> master == outer` valid.

Why forget/add and not a host frame on the right too: a host frame would put
itself between the RLC right PanedWindow and the outer one, and four tests
(and `tests/_isolated_desktop.py`'s measurements) read `master` of that
PanedWindow as the outer one.  `ttk.PanedWindow` has no `hide`, so the swap
is the only way to keep the widget tree as it is.

FOCUS.  `docs/conventions/rejected_ui.md` turned down a notebook beside the
plot partly because switching tabs steals focus and the M / V / Delete keys
depend on the plot canvas having it.  Here the RLC workspace's `on_enter`
hands the plot canvas focus every time the workspace is shown, and
`tests/test_workspaces.py` sends real key events after a round trip to prove
those keys still land.

No `pkg_rlc.frontend.app` import, not at module level and not inside a
function: the App is passed in and duck-typed, as for every panel.  The two
session helpers at the bottom are the `attribution` block's shape -- an inner
version number, written only when there is something besides the defaults,
and a bad value costs its own field, never the file.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, Optional

#: The inner version of the session file's "workspaces" block.  Independent
#: of `SESSION_VERSION`, which does not move for this block: an older reader
#: ignores a top-level key it does not know, which is the whole reason the
#: block is top-level and carries its own number.
WORKSPACES_SESSION_VERSION = 1

#: The workspace a session with no "workspaces" block loads into, and the one
#: a fresh App shows: today's whole UI.
DEFAULT_WORKSPACE = "rlc"


@dataclass
class Workspace:
    """One registered workspace.  The two callables are optional hooks."""
    key: str
    title: str
    left_frame: tk.Widget
    right_widget: tk.Widget
    on_enter: Optional[Callable[[], None]] = None
    on_leave: Optional[Callable[[], None]] = None
    state_get: Optional[Callable[[], dict]] = None
    state_set: Optional[Callable[[dict], None]] = None


class WorkspaceSwitch(ttk.Frame):
    """
    The strip, and the show/hide it drives.

    `outer` is the horizontal PanedWindow whose second pane is swapped;
    `left_host` is the frame under the shared Files panel that a workspace's
    left frame is packed into.  Register every workspace, then `show` one.
    Stage 3 adds its workspace with one more `register` call.
    """

    def __init__(self, master, outer: ttk.PanedWindow, left_host: tk.Widget,
                 right_weight: int = 1) -> None:
        super().__init__(master)
        self._outer = outer
        self._left_host = left_host
        self._right_weight = right_weight
        self._workspaces: dict[str, Workspace] = {}
        self._order: list[str] = []
        self._buttons: dict[str, ttk.Radiobutton] = {}
        self._var = tk.StringVar(master=self, value="")
        self._active: Optional[str] = None

    # ------------------------------------------------------------ registry

    def register(self, key: str, title: str, left_frame: tk.Widget,
                 right_widget: tk.Widget,
                 on_enter: Optional[Callable[[], None]] = None,
                 on_leave: Optional[Callable[[], None]] = None,
                 state_get: Optional[Callable[[], dict]] = None,
                 state_set: Optional[Callable[[dict], None]] = None,
                 ) -> Workspace:
        """
        Add a workspace and its button.  `left_frame` must be a child of
        `left_host`; `right_widget` a child of `outer`, so that a forgotten
        pane's `master` is still the outer PanedWindow.
        """
        if key in self._workspaces:
            raise ValueError(f"workspace {key!r} is already registered")
        ws = Workspace(key=key, title=title, left_frame=left_frame,
                       right_widget=right_widget, on_enter=on_enter,
                       on_leave=on_leave, state_get=state_get,
                       state_set=state_set)
        self._workspaces[key] = ws
        self._order.append(key)
        btn = ttk.Radiobutton(self, text=title, value=key, variable=self._var,
                              style="Toolbutton",
                              command=lambda k=key: self.show(k))
        btn.pack(side=tk.LEFT, padx=(2, 0))
        self._buttons[key] = btn
        return ws

    def keys(self) -> list[str]:
        return list(self._order)

    def workspace(self, key: str) -> Workspace:
        return self._workspaces[key]

    def button(self, key: str) -> ttk.Radiobutton:
        return self._buttons[key]

    @property
    def active(self) -> Optional[str]:
        return self._active

    # ---------------------------------------------------------------- show

    def show(self, key: str) -> None:
        """
        Show `key`'s left frame and right pane, hide the active one's.

        Idempotent for the active workspace (only the radio variable is
        re-synced), and safe for a right widget that is ALREADY a pane of
        `outer` -- the RLC pane is added by `_build_ui` before the switch
        exists, in the order the 2px-sash rule there requires.
        """
        ws = self._workspaces[key]          # KeyError: not registered
        if self._active == key:
            self._var.set(key)
            return
        old = self._workspaces.get(self._active) if self._active else None
        if old is not None:
            if old.on_leave is not None:
                old.on_leave()
            old.left_frame.pack_forget()
            if str(old.right_widget) in self._outer.panes():
                self._outer.forget(old.right_widget)
        if str(ws.right_widget) not in self._outer.panes():
            self._outer.add(ws.right_widget, weight=self._right_weight)
        ws.left_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._active = key
        self._var.set(key)
        if ws.on_enter is not None:
            ws.on_enter()


# ============================================================================
# The session block
# ============================================================================

def workspaces_session_state(switch) -> dict:
    """
    The "workspaces" block for `session_to_dict`, or {} when everything is at
    its default.

    `{"version": 1, "active": <key>, <key>: state_get(), ...}`.  A workspace
    without `state_get`, or whose state is empty, contributes no key; with
    the default workspace active and nothing else to say the block is {} and
    `session_to_dict` leaves it out -- the `attribution` rule: an empty block
    on every file is noise that buries the ones that carry state.  A
    `state_get` that raises costs its own key, never the block.
    """
    out: dict = {}
    for key in switch.keys():
        ws = switch.workspace(key)
        if ws.state_get is None:
            continue
        try:
            state = ws.state_get()
        except Exception:                                # pragma: no cover
            continue
        if state:
            out[key] = state
    active = switch.active or DEFAULT_WORKSPACE
    if active == DEFAULT_WORKSPACE and not out:
        return {}
    block = {"version": WORKSPACES_SESSION_VERSION, "active": active}
    block.update(out)
    return block


def apply_workspaces_session_state(switch, data) -> list[str]:
    """
    Restore the block, and say what could not be.  Never raises.

    No block (an older session file, or one saved at the defaults) shows the
    default workspace; a block of the wrong version, a key for a workspace
    this build does not have, or a state that is not an object costs that
    piece and produces one note for the Log.  Returns the notes.
    """
    notes: list[str] = []
    known = switch.keys()
    fallback = DEFAULT_WORKSPACE if DEFAULT_WORKSPACE in known else (
        known[0] if known else None)
    if not isinstance(data, dict) or not data:
        if fallback is not None:
            switch.show(fallback)
        return notes
    ver = data.get("version")
    if ver != WORKSPACES_SESSION_VERSION:
        notes.append(f"Workspaces: session block version {ver!r} is not "
                     f"{WORKSPACES_SESSION_VERSION}; the block was ignored.")
        if fallback is not None:
            switch.show(fallback)
        return notes
    for key, value in data.items():
        if key in ("version", "active"):
            continue
        if key not in known:
            notes.append(f"Workspaces: '{key}' is not a workspace this build "
                         f"has; its saved state was ignored.")
            continue
        ws = switch.workspace(key)
        if ws.state_set is None:
            notes.append(f"Workspaces: '{key}' keeps no session state in "
                         f"this build; its saved state was ignored.")
            continue
        if not isinstance(value, dict):
            notes.append(f"Workspaces: the saved state of '{key}' is not an "
                         f"object; ignored.")
            continue
        try:
            ws.state_set(value)
        except Exception as exc:                         # noqa: BLE001
            notes.append(f"Workspaces: the saved state of '{key}' could not "
                         f"be restored ({exc}); ignored.")
    active = data.get("active", fallback)
    if not isinstance(active, str) or active not in known:
        notes.append(f"Workspaces: active workspace {active!r} is not one of "
                     f"{', '.join(known)}; showing '{fallback}'.")
        active = fallback
    if active is not None:
        switch.show(active)
    return notes
