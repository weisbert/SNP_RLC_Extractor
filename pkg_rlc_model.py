"""
pkg_rlc_model.py  --  the shared data model every layer above passes around.

WHY THIS MODULE EXISTS.  `FileEntry`, `TraceConfig` and the snapshot-adjacent
helpers below used to live in `pkg_rlc_gui`, in the same file as the Tk `App`.
Every panel and every window that needed the data model therefore had to reach
UP into the frontend, and the only way to do that without an import cycle was
an `import pkg_rlc_gui` written inside a function body.  There were ten of
them.  They are gone, and this file is why: what those functions reached for is
here, below everything that reaches for it, and imported at the top of the file
like anything else.

WHAT BELONGS HERE.  The data a trace IS, and the pure functions that copy it,
compare it or describe what one Calculate found.  No Tk, no matplotlib, no
`App`.  A colour is not part of the data model and lives in `pkg_rlc_widgets`
with the rest of the palette; a rendering of the data model is
`pkg_rlc_report`'s.

WHAT IT IMPORTS.  `pkg_rlc_core` for the row dataclasses and the parser types,
and `pkg_rlc_validate` for the spec logic that is duck-typed over a trace --
the port descriptor, the file set, the three legacy migrations.  Both are at or
below this module in `tests/test_layering.py`; `pkg_rlc_validate` in particular
imports nothing but L0 and knows nothing about this file, which is what keeps
the edge one-directional.  See that file's LAYERS map for the reason it sits
where it does.
"""

from __future__ import annotations

from dataclasses import astuple, dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from pkg_rlc_core import (
    MeasPortRow,
    TouchstoneData,
    s_to_y,
)
from pkg_rlc_validate import (
    _import_dsl_text,
    _mport_more_lines,
    _port_descriptor,
    _union_port_specs,
    trace_file_labels,
    trace_file_scope,
)


# ============================================================================
# Data classes
# ============================================================================

class FileEntry:
    """A loaded Touchstone file with its derived Y-matrix."""

    def __init__(self, ts: TouchstoneData):
        self.ts = ts
        self.Y = s_to_y(ts.s, ts.z0)
        self.label = Path(ts.source_path).name

    def info_str(self) -> str:
        # The frequency span is here because it is the first thing anyone
        # checks against what they simulated, and the list line was the one
        # place it could have been seen without being shown at all.  It goes
        # ahead of M and Z0 on purpose: a Listbox has no horizontal scrollbar,
        # so a long file name clips the TAIL of this line (measured: a 37-char
        # name needs 476 px against a 444 px list), and of the four facts the
        # span is the one worth keeping.
        return (f"{self.label}  "
                f"(N={self.ts.nports}, {self.ts.freq_span_str()}, "
                f"M={len(self.ts.freqs)}, Z0={self.ts.z0:g}Ω)")


@dataclass
class TraceConfig:
    """
    User-editable trace specification (1-based ports).

    Mode codes are stable and never renumbered: 1, 2, 3, 5 and 6 are live;
    4 ("A ↔ B + VDD/GND") is RETIRED — for AC small-signal VDD is just another
    AC ground, so a mode-4 trace is migrated to mode 2 with its VDD ports
    unioned into the GND ports (see `migrate_legacy_mode`).  `vdd_ports` is
    kept as a field so old configs still load; nothing edits it any more.
    """
    id: int = 0
    # THE HOME FILE.  A bare port number anywhere in this trace means a port of
    # THIS file, in every mode -- which is what keeps every pre-existing spec,
    # every golden case and every saved session meaning exactly what it meant
    # before composition existed, and what keeps a single-file user from ever
    # seeing a tag.  It stays a single `str` on purpose: the alternative (one
    # list of files, home at index 0) would have moved every one of the ~20
    # consumers of this field at once, and would have written a one-element
    # list into every session file ever saved.
    file_label: str = ""
    # The OTHER files this trace is composed with, in order: plain FileEntry
    # labels, no tags.  A file's TAG is its POSITION -- F1 is the home file,
    # F2 the first of these -- resolved through pkg_rlc_compose.default_alias
    # by `trace_file_aliases` here and by `slots_of` in pkg_rlc_files_gui, so
    # there is exactly one authority for what 'F2.3' means.  Measured there:
    # a port cell shows about 7 characters, 'F2.' is 33% of that and leaves 4
    # digits of port number, while a 4-character tag is 73% and leaves 1.
    #
    # Empty is the ordinary single-file trace, and empty is what makes this
    # field invisible: it is not written to a session file (see
    # _OPTIONAL_TRACE_FIELDS), it contributes nothing to _config_signature and
    # a snapshot of a single-file trace carries no file list at all -- so
    # every spec, golden case and saved session that predates composition
    # means exactly what it always meant.
    #
    # It is LIST-VALUED, so it is the documented `mports` Duplicate aliasing
    # trap: _duplicate_trace_config and _freeze_trace_config must copy it, or
    # two traces silently share one file set.
    file_labels: list = field(default_factory=list)   # list[str]
    mode: int = 1            # 1, 2, 3, 5, 6  (4 retired -> migrated to 2)
    port_a: str = "1"
    port_b: str = ""
    short_pairs: str = ""
    gnd_ports: str = ""
    vdd_ports: str = ""      # retired; kept so mode-4 configs still migrate
    # RETIRED: the free-text Mode 5 spec.  Kept only so an older config still
    # loads (see migrate_legacy_custom_text) and never written again -- the two
    # tables below are the live storage and the DSL text is a DERIVED view,
    # computed on demand.  Storing both would give the migration guard two
    # states it cannot tell apart.
    custom_text: str = ""
    # --- Mode 6 (+/- measurement ports / coupling) ---
    # `mports` is the live storage: one MeasPortRow per measurement port, port
    # specs kept as typed so '5:1:8' stays a range.  The mp1_*/mp2_*/mp_more
    # fields below are RETIRED -- Mode 6 used to offer two hard-coded ports and
    # a free-text box for the third onward -- and are kept only so an older
    # config still loads (see migrate_legacy_mports).
    mports: list = field(default_factory=list)   # list[MeasPortRow]
    mp1_name: str = ""
    mp1_plus: str = ""
    mp1_minus: str = ""
    mp2_name: str = ""
    mp2_plus: str = ""
    mp2_minus: str = ""
    mp_more: str = ""
    # --- Mode 5 (Custom): the connection table ---
    # conn_rows + extra_lines are the LIVE storage, exactly as `mports` is for
    # the measurement-port table.  `extra_lines` holds whatever the table
    # cannot represent (comments, hand-written directives, and a whole spec
    # whose meaning the table would change -- see migrate_legacy_custom_text).
    conn_rows: list = field(default_factory=list)   # list[ConnectionRow]
    extra_lines: str = ""
    plot_self: bool = True
    plot_mutual: bool = True
    # Drawn or not.  This gates the PLOT ONLY: a hidden trace is still
    # computed, still in the results table and still in the CSV export -- the
    # user asked to stop drawing it, not to stop measuring it.  Toggling it
    # replots from the cached Z / Zmat below, without re-running the reduction.
    enabled: bool = True
    # A FROZEN trace is a snapshot: it keeps the numbers it was computed with,
    # Calculate never recomputes it and the editor never writes into it, so the
    # curve on the plot and the spec beside it keep describing each other.  It
    # is what makes a before/after comparison a comparison of CURVE SHAPES over
    # the whole sweep rather than of two numbers in a log.  It is a CONFIG
    # field (the user set it), so it round-trips through a session file -- but
    # the numbers do NOT, and _apply_session says so rather than leaving a
    # frozen trace that silently plots nothing.
    frozen: bool = False
    label: str = ""
    color_idx: int = 0
    ls_idx: int = 0
    # Computed (filled in after Calculate)
    # `stale` says the config has been edited since Z was computed, so the
    # curve on screen is not what this row now describes.  Without it the
    # replot-from-cache path would quietly redraw last run's numbers under the
    # new port spec -- and with auto-apply, EVERY keystroke makes Z stale.
    stale: bool = False
    Z: Optional[np.ndarray] = None
    rlc: Optional[object] = None
    fit_kind: str = ""
    fit: Optional[object] = None
    # The evaluated fit overlay, cached so the curves can be rebuilt from this
    # trace alone.  Everything the plot needs has to live here, or toggling
    # visibility would silently drop the fit overlay off the traces that stay.
    fit_freqs: Optional[np.ndarray] = None
    fit_Z: Optional[np.ndarray] = None
    # Computed, mode 6 only
    Zmat: Optional[np.ndarray] = None          # (nfreqs, G, G) complex
    mport_names: Optional[list[str]] = None    # length G
    coupling: Optional[object] = None          # CouplingResult at marker freq
    # Computed, COMPOSED traces only.
    #
    # `net_freqs` is the frequency axis `Z` / `Zmat` actually live on.  None
    # means "the home file's own sweep", which is what it is for every
    # single-file trace and therefore for every trace that existed before
    # composition -- so nothing about them moves.  On a composed trace the two
    # axes are equal only when no interpolation happened, and drawing a
    # composed Z against the home file's freqs would misplace every point in
    # the sweep with no symptom but a plausible curve.
    net_freqs: Optional[np.ndarray] = None
    # The reference-node verdicts (R3-5), one per file, cached so the run
    # snapshot, the Attribution window's strip and the files window all read
    # the same list.  `pkg_rlc_files_gui.reference_checks_of` looks for exactly
    # this attribute name.
    reference_checks: Optional[list] = None

    MODE_NAMES = {1: "GND", 2: "A↔B", 3: "A↔B+Short",
                  4: "A↔B+VDD (retired)", 5: "Custom", 6: "+/- Coupling"}

    def info_str(self) -> str:
        # The ☑/☐ prefix is what makes visibility readable at a glance without
        # selecting the trace.  Measured (Microsoft YaHei UI 9): both glyphs are
        # 12 px, 16 px with the trailing space, so toggling does NOT shift the
        # rest of the line -- '✓' vs a space would have jittered by 8 px.  Cost
        # against the 444 px list: a typical entry goes 356 -> 372 px.
        # A trailing '*' means the drawn curve is older than this spec.
        #
        # The ❄ is at the END, not in the prefix: it is not a toggling state
        # the way ☑/☐ is, so it cannot jitter the line, and a frozen trace that
        # came back from a session file WITHOUT its numbers has to say so here
        # -- it is the one place a trace that plots nothing is visible before
        # the next Calculate.
        frozen = ""
        if self.frozen:
            frozen = ("  ❄" if (self.Z is not None or self.Zmat is not None)
                      else "  ❄ no numbers")
        # A composed trace names its HOME file and counts the rest.  ' +N'
        # rather than ' +N files' or the extra file's tag, for the usual
        # measured reason: on a representative entry in the Traces list
        # (Microsoft YaHei UI 9, 444 px of list) the line is 388 px bare,
        # 408 px with ' +1' -- and 408 px with ' +2' and ' +9' too, so the
        # count cannot jitter the line.  ' +1 file' is 429 px, 15 px from the
        # edge, and ' +PKG' is 421 px and grows with the tag.
        extra = len(trace_file_labels(self)) - 1
        more = f" +{extra}" if extra > 0 else ""
        return (f"{'☑' if self.enabled else '☐'} "
                f"[{self.id}] {self.label}  |  "
                f"{self.file_label}{more}  {self.MODE_NAMES.get(self.mode, '?')}"
                f"{' *' if self.stale else ''}{frozen}")

    def port_descriptor(self) -> str:
        """Compact one-line port-config descriptor for the results table."""
        return _port_descriptor(self)

    def mode_name(self) -> str:
        return self.MODE_NAMES.get(self.mode, f"mode{self.mode}")

    def migrate_legacy_mode(self) -> bool:
        """
        Retired mode 4 -> mode 2 with VDD folded into GND.  Returns True when a
        migration actually happened, so the caller can tell the user.
        """
        if self.mode != 4:
            return False
        self.gnd_ports = _union_port_specs(self.gnd_ports, self.vdd_ports)
        self.vdd_ports = ""
        self.mode = 2
        return True

    def migrate_legacy_mports(self) -> bool:
        """
        Fold the retired mp1_*/mp2_*/mp_more fields into the `mports` table.

        The old Mode 6 editor had two hard-coded measurement ports plus a
        free-text box for the third onward; the table replaces all three.  The
        'name = +ports / -ports' lines are split textually rather than through
        parse_mport_spec, so port RANGES survive as ranges ('5:1:8' stays one
        cell) and a malformed old line migrates instead of raising during load.
        It will fail later, at Calculate, with a message that names it.

        Returns True when a migration actually happened.
        """
        if self.mports:
            return False
        rows: list = []
        for name, plus, minus in ((self.mp1_name, self.mp1_plus, self.mp1_minus),
                                  (self.mp2_name, self.mp2_plus, self.mp2_minus)):
            if (plus or "").strip() or (minus or "").strip():
                rows.append(MeasPortRow(name=(name or "").strip(),
                                        plus=(plus or "").strip(),
                                        minus=(minus or "").strip()))
        for line in _mport_more_lines(self.mp_more):
            name, sep, rest = line.partition("=")
            if not sep:
                name, rest = "", line
            plus, _slash, minus = rest.partition("/")
            rows.append(MeasPortRow(name=name.strip(), plus=plus.strip(),
                                    minus=minus.strip()))
        if not rows:
            return False
        self.mports = rows
        self.mp1_name = self.mp1_plus = self.mp1_minus = ""
        self.mp2_name = self.mp2_plus = self.mp2_minus = ""
        self.mp_more = ""
        return True

    def migrate_legacy_custom_text(self) -> bool:
        """
        Fold the retired free-text Mode 5 spec into the two tables.

        Guarded on THREE fields, not one: unlike `mports` there is no single
        field that proves the conversion happened -- a Mode 5 trace can
        legitimately have rows in one table and none in the other.

        MEANING IS NEVER CHANGED.  dsl_text_to_rows discards line order and
        rows_to_dsl_text re-emits every probe BEFORE every connection, so a
        spec whose 'signal' line follows a 'ground' on the same port would come
        back meaning something else ('3 ground / 3 signal A / 4 signal B'
        resolves port 3 as Signal directly and as Ground after one pass, and
        then resolve_meas_ports raises because group A is left with only a
        minus side).  When the round trip is not meaning-preserving the WHOLE
        spec is parked verbatim in extra_lines, which rows_to_dsl_text appends
        unchanged -- bit-identical to what the trace computed before.

        Returns True when a migration actually happened.
        """
        if self.mports or self.conn_rows or self.extra_lines:
            return False
        text = (self.custom_text or "").strip()
        if not text:
            return False
        mports, conn, extra, changed = _import_dsl_text(self.custom_text)
        if changed:
            self.mports, self.conn_rows = [], []
            self.extra_lines = self.custom_text.rstrip()
        else:
            self.mports, self.conn_rows, self.extra_lines = mports, conn, extra
        self.custom_text = ""
        return True


@dataclass
class SolveNetwork:
    """
    What ONE trace is solved against.

    A single-file trace gets the FileEntry's own arrays verbatim -- the same
    `fe.Y`, the same `fe.ts.freqs` -- so every pre-existing trace takes exactly
    the path it took before composition existed and the golden regression is
    untouched.  A composed trace gets the stacked network and its own frequency
    axis, which is the composed one and not the home file's.
    """
    freqs: np.ndarray
    Y: np.ndarray
    nports: int
    port_names: list
    #: '' for a single file; the home file's tag ('F1') for a composition,
    #: which is what a bare port field is scoped to.
    home_alias: str = ""
    net: object = None                  # comp.ComposedNetwork | None
    notes: tuple = ()
    warnings: tuple = ()

    @property
    def composed(self) -> bool:
        return self.net is not None


def _composed_solve_network(net) -> SolveNetwork:
    """A stacked network as a SolveNetwork.  The port NAMES carry their tags.

    `net.port_labels()` is 'F2.13 VSS_1', not 'VSS_1': on a combined network a
    bare port name names nothing anyone can act on, and these names reach the
    Ports & Roles window and the open-port remnant check, both of which are
    read as "which port is this".
    """
    return SolveNetwork(freqs=net.freqs, Y=net.Y, nports=net.nports,
                        port_names=net.port_labels(),
                        home_alias=(net.blocks[0].alias if net.blocks else ""),
                        net=net, notes=tuple(net.notes),
                        warnings=tuple(net.warnings))


def _duplicate_trace_config(src: "TraceConfig", new_id: int) -> "TraceConfig":
    """
    Copy a trace for the Duplicate button, dropping last run's results.

    `mports` and `conn_rows` are LISTS of dataclasses, so both are copied
    element-wise.  A plain `TraceConfig(**src.__dict__)` hands both traces the
    same list object, and editing the copy's measurement ports or connections
    then silently edits the original's -- a bug with no visible symptom until
    two curves quietly agree.
    """
    return TraceConfig(**{**src.__dict__,
                          "id": new_id,
                          "label": src.label + "_copy",
                          "mports": [replace(r) for r in src.mports],
                          "conn_rows": [replace(r) for r in src.conn_rows],
                          # Same trap, third list-valued field: without this
                          # the copy and the original share one file set, and
                          # adding a file to one silently adds it to the other.
                          "file_labels": list(src.file_labels),
                          # Duplicating a frozen trace must NOT produce another
                          # frozen one: the copy drops the results (below), and
                          # a frozen trace with no numbers is one Calculate
                          # will never fill in.  Duplicate means "carry on
                          # editing from here", which is the opposite of frozen.
                          "frozen": False,
                          "Z": None, "rlc": None, "fit": None, "fit_kind": "",
                          "fit_freqs": None, "fit_Z": None,
                          "Zmat": None, "mport_names": None,
                          "coupling": None, "stale": False,
                          # Both are products of a run this copy never had.
                          # `net_freqs` is harmless while Z is None (nothing
                          # is drawn), but a verdict is not: `_snapshot_row`
                          # reads `reference_checks` off the trace, so the
                          # first Calculate of a duplicate whose file set has
                          # since been edited would print the ORIGINAL's
                          # reference-node verdict beside the new numbers.
                          "net_freqs": None, "reference_checks": None})



def _config_signature(tc: "TraceConfig") -> tuple:
    """
    Everything that feeds _build_termination, as a comparable value.

    Auto-apply writes the editor into the trace on every keystroke, so "did
    this edit invalidate the computed curve?" has to be answerable cheaply.
    Only fields that change the ANSWER belong here -- colour, linestyle and the
    plot checkboxes change the picture and are handled by _draw_signature,
    which triggers a replot from cache rather than marking anything stale.
    """
    return (tc.file_label, tc.mode, tc.port_a, tc.port_b, tc.short_pairs,
            tc.gnd_ports, tc.extra_lines,
            tuple((r.name, r.plus, r.minus) for r in tc.mports),
            tuple(astuple(r) for r in tc.conn_rows),
            # The file SET, as one element, so this stays one-for-one with
            # _SIGNATURE_FIELDS.  It is "" for every trace that predates
            # composition, so no existing signature moves.  Appended rather
            # than inserted next to `file_label`: nothing indexes into this
            # tuple, but a run diff reads _SIGNATURE_FIELDS in order and the
            # file set belongs after the spec, not in the middle of it.
            trace_file_scope(tc))


def _draw_signature(tc: "TraceConfig") -> tuple:
    """
    What changes the picture without changing the numbers.

    `label` is deliberately absent: it reaches the plot only as a legend name,
    and including it would re-render every subplot on every keystroke of the
    Label field.  These five all change discretely (a click), so a replot per
    change is free.
    """
    return (tc.enabled, tc.color_idx, tc.ls_idx, tc.plot_self, tc.plot_mutual)



#
# The real discriminator between two run tabs is not the clock, it is what the
# user changed.  These render _config_signature's fields with a NAME each, so
# a diff can say "[3] gnd 6-14 -> 6-16" instead of "something is different".
#
# The list mirrors _config_signature ONE FOR ONE and
# tests/test_run_history.py::TestSignatureFieldsCoverConfigSignature pins that
# -- a new field that changes the answer must show up here too, or a run tab
# will claim nothing changed when the numbers did.
_SIGNATURE_FIELDS: tuple = (
    ("file", lambda tc: tc.file_label),
    ("mode", lambda tc: tc.mode_name()),
    ("port A", lambda tc: tc.port_a),
    ("port B", lambda tc: tc.port_b),
    ("short", lambda tc: tc.short_pairs),
    ("gnd", lambda tc: tc.gnd_ports),
    ("text", lambda tc: " / ".join(
        ln for ln in (tc.extra_lines or "").splitlines() if ln.strip())),
    ("mports", lambda tc: "; ".join(
        f"{r.name}={r.plus}/{r.minus}" for r in tc.mports)),
    ("connections", lambda tc: " | ".join(
        " ".join(str(v) for v in astuple(r) if str(v)) for r in tc.conn_rows)),
    # Which files this trace is built from -- '' for a single-file trace that
    # has not tagged its own file, i.e. for everything that predates
    # composition, so no existing run page gains a line.
    ("files", trace_file_scope),
)


def trace_signature_fields(tc: "TraceConfig") -> tuple:
    """((label, rendered value), ...) for one trace -- a named _config_signature."""
    return tuple((name, str(fn(tc) or "")) for name, fn in _SIGNATURE_FIELDS)


def run_signatures(traces: Sequence) -> tuple:
    """((trace_id, ((label, value), ...)), ...) for a whole run."""
    return tuple((tc.id, trace_signature_fields(tc)) for tc in traces)
