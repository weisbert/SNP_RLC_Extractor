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

import math

from dataclasses import astuple, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from pkg_rlc.physics.core import (
    MeasPortRow,
    TouchstoneData,
    s_to_y,
)
from pkg_rlc.model.validate import (
    _import_dsl_text,
    _mport_more_lines,
    _port_descriptor,
    _union_port_specs,
    trace_file_aliases,
    trace_file_labels,
    trace_file_scope,
    trace_is_composed,
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


# ============================================================================
# The three results views -- a SAVED choice about how a run is rendered
# ============================================================================
#
# One run, three renderings, chosen by the reader and never by the code.  They
# exist because the report had exactly one shape and it was the widest one:
# measured on a two-trace mode-6 run, 40 lines and 3538 characters against a
# pane that shows 144 columns at the default 1500x900 window (79 at the
# 1040x600 minsize) and does NOT wrap, with 12 lines over 90 columns and the
# widest at 272.
#
#   detail   -- everything, one block per trace.  What the tool always had.
#   summary  -- two tables for the whole run, one row per port and one per
#               pair.  Reading ACROSS traces is a matter of reading down a
#               column instead of paging between blocks 17 lines apart.
#   compare  -- traces become COLUMNS, with a delta.  This is the one that
#               answers "what did this EM revision change", which is what a
#               run with two versions of one structure in it is for.
#
# The choice is a RENDERING choice, not a recorded fact about the MEASUREMENT
# -- the same rule as the units mode, and the reason both are read live off the
# App by `_run_report_segments` rather than frozen onto a RunSnapshot.
#
# WHY THE NAMES ARE HERE and not beside the three `_format_*` functions that
# act on them.  `results_view` is SAVED: the session file writes the chosen
# name and reads it back, and `pkg_rlc_session._CONTROL_CHOICES` validates it
# against this tuple.  So the vocabulary is shared between the file FORMAT (L2)
# and the RENDERER (L3), and a shared vocabulary lives at or below the lower of
# the two.  Spelling it as a literal in both places was the alternative, and it
# is the failure this repo names everywhere else -- two copies of one list are
# two things that can come to disagree, and here the disagreement would be a
# saved view silently refused on load.
#
# It belongs in the MODEL rather than merely fitting here: a stored choice
# about how to draw something is already model data -- `color_idx`, `ls_idx`,
# `plot_self` and `plot_mutual` are all fields of `TraceConfig` above, and this
# is the same kind of fact one level up, per RUN instead of per trace.
#
# `pkg_rlc_report` re-exports all four, so every existing
# `from pkg_rlc_report import VIEW_DETAIL` keeps resolving.
VIEW_DETAIL = "detail"
VIEW_SUMMARY = "summary"
VIEW_COMPARE = "compare"
RESULTS_VIEWS = (VIEW_DETAIL, VIEW_SUMMARY, VIEW_COMPARE)


# ============================================================================
# What a line written to the Results pane MEANS
# ============================================================================
#
# Severity of a line written to the Results pane.  INFO is what every call site
# had before the pane became a notebook, so the default keeps the old behaviour
# exactly; WARN counts towards the Log tab's badge and ERROR also brings the
# Log tab to the front.
#
# WHY THEY ARE HERE.  A severity is a property of the MESSAGE, not of the pane
# that shows it -- the rule is already written down in those words: "Severity
# routing follows what the line MEANS, not where it is printed."  So the code
# that PRODUCES a line is what classifies it, and every layer produces lines:
# `pkg_rlc_run` (L2) emits the Schur / lstsq fallback warning, the
# reference-node check that could not run, and the probe whose current has
# nowhere to return; the panels and the App emit the rest.  A vocabulary shared
# from L2 up to L6 lives at or below the lowest of them.
#
# Everything about how a severity is RENDERED stayed in `pkg_rlc_report`, which
# re-exports these three: `LOG_BADGE_CAP`, `log_tab_label` and the measured
# width-stable badge.  That is the split -- what a line means here, what the
# tab strip does about it there.
LOG_INFO = "info"
LOG_WARN = "warn"
LOG_ERROR = "error"


# ============================================================================
# Frequency provenance -- what a printed marker frequency actually IS
# ============================================================================
#
# extract_rlc_at_freq and extract_coupling_at_freq both pick their point with
# argmin(|freqs - target|) and report nothing at all about the distance, so the
# tool used to print TWO different frequencies on one screen and explain
# neither: the Calculate header and the run page printed f_rlc_hz (what the
# user typed) while the Z-matrix line printed cres.freq_hz (the point the
# numbers actually came from).  A real user read "@ 5.6 GHz" and "@ 5.512 GHz"
# in the same report and had no way to know which one their L belonged to.
#
# It is not a corner case.  Measured on tests/fixtures/diff_pair_4port.s4p
# (401 points, 1 MHz .. 10 GHz, step 24.9975 MHz) at the default marker of
# 0.1 GHz: the nearest point is 0.10099 GHz.  Every default session in this
# repo snaps by 990 kHz, and said nothing.
#
# FreqSnap is that fact as a VALUE, which is why it is here and not beside the
# renderer: it is a property of the measurement, it says nothing about how to
# print itself, and a run record holds one (`CouplingSnapshot.freq`,
# `RunSnapshot.freqs`) -- so a model type would otherwise have a field whose
# type lives two layers above it.  `marker_freq_text`, the ONE renderer for it,
# stayed in `pkg_rlc_report`: it takes a format string and returns a sentence.
# THE RULE it enforces, quoted here because it is what these fields are FOR:
# when the requested frequency IS a data point, every site renders byte-for-byte
# what it rendered before.  The common case must not grow a parenthetical, tests
# elsewhere pin those strings, and tests/fixtures/render_reference.json pins the
# Z-matrix line.

# A difference smaller than this fraction of the grid step is float noise, not
# a snap.  The noise is real and it comes from the parser's UNIT SCALING, not
# from parse_si (which is exact for every value anyone types: "5.6" -> 5.6e9 to
# the bit).  A file written in MHz or kHz carries its axis as decimal text that
# is multiplied by 1e6 / 1e3, and `33023.73 * 1e6` is 33023730000.000004 where
# the same point typed as "33.02373" GHz is 33023730000.0 exactly -- measured,
# worst case 3.8e-6 Hz over a 400-point decimal sweep in either unit.  Against
# that, the snaps worth reporting are megahertz: the default marker on
# diff_pair_4port.s4p moves 990 kHz.  1e-6 of that file's 25 MHz step is 25 Hz,
# which sits between the two with ten orders of magnitude to spare on each side.
FREQ_EXACT_FRAC = 1e-6
# ... and with no gap to scale against (a one-point sweep), relative to the
# requested frequency instead.
FREQ_EXACT_REL = 1e-9
# A sweep counts as uniform when every gap is within this fraction of the
# median gap.  Real linear sweeps carry decimal round-off in the axis (the
# fixture above: 0.0 spread); a log sweep or a band densified round a resonance
# is orders of magnitude away from passing, and gets "nearest point" with no
# step rather than a made-up number.
FREQ_UNIFORM_TOL = 1e-3

@dataclass(frozen=True)
class FreqSnap:
    """Where a value was actually read, against where it was asked for.

    Floats only, deliberately: this ends up on a RunSnapshot, and
    tests/test_run_snapshot.py walks every ndarray reachable from a run to
    prove a record does not grow with the sweep.
    """
    requested_hz: float
    # NaN means "not resolved against any grid" -- a record restored before any
    # Calculate, or a pure-text caller.  Such a snap renders like a bare float.
    actual_hz: float = float("nan")
    # The sweep's step, NaN when it is not uniform.  Display only.
    step_hz: float = float("nan")
    # The widest gap adjacent to the chosen point.  This, not `step_hz`, is
    # what the snap is JUDGED against -- see `off_grid`.
    local_step_hz: float = float("nan")
    # False when several sweeps in one run resolved to different points, so
    # there is no single frequency to print.
    agreed: bool = True

    @property
    def resolved(self) -> bool:
        return math.isfinite(self.actual_hz)

    @property
    def delta_hz(self) -> float:
        if not self.resolved:
            return float("nan")
        return self.actual_hz - self.requested_hz

    @property
    def exact(self) -> bool:
        """True when the requested frequency IS a data point.

        This is the predicate that keeps the common case silent, so it has to
        tolerate float noise: see FREQ_EXACT_FRAC.
        """
        if not self.resolved:
            return True
        d = abs(self.delta_hz)
        if d == 0.0:
            return True
        if math.isfinite(self.local_step_hz) and self.local_step_hz > 0.0:
            return d <= FREQ_EXACT_FRAC * self.local_step_hz
        return d <= FREQ_EXACT_REL * max(abs(self.requested_hz), 1.0)

    @property
    def off_grid(self) -> bool:
        """The requested frequency is not between two points -- it is OUTSIDE
        the swept band, and that is what earns a warning rather than a note.

        For any monotone axis the two statements are the same one.  If the
        target lies inside the band it falls in some gap [f_i, f_i+1], and the
        nearer end of that gap is at most half of it away -- so a distance
        greater than half the adjacent gap can only mean the target is off the
        end.  Judging against the LOCAL gap rather than the median is what
        makes this hold on a log sweep too, and taking the WIDER of the two
        adjacent gaps is what keeps it free of false alarms where the spacing
        changes.
        """
        if self.exact or not self.resolved:
            return False
        if math.isfinite(self.local_step_hz) and self.local_step_hz > 0.0:
            return abs(self.delta_hz) > 0.5 * self.local_step_hz
        # A one-point sweep has no gap at all: anything but that point is a
        # request the file cannot answer.
        return True


def freq_grid_step(freqs) -> float:
    """The sweep's step in Hz, or NaN when the sweep is not uniform."""
    f = np.asarray(freqs, dtype=float).ravel()
    if f.size < 2:
        return float("nan")
    d = np.abs(np.diff(f))
    med = float(np.median(d))
    if not math.isfinite(med) or med <= 0.0:
        return float("nan")
    if float(np.max(np.abs(d - med))) > FREQ_UNIFORM_TOL * med:
        return float("nan")
    return med


def snap_to_grid(freqs, requested_hz: float) -> FreqSnap:
    """
    Resolve a requested marker frequency against a real frequency axis, the
    same way extract_rlc_at_freq / extract_coupling_at_freq do -- and keep the
    two things they throw away: how far it moved, and how coarse the grid is.

    Measured cost: 13.4 us on the 401-point fixture and 26.4 us on a
    5000-point sweep (median of five runs of 2000 calls, numpy 2.x).  It runs
    once per FILE per Calculate, not once per trace, so it is invisible next to
    the reduction it precedes.
    """
    f = np.asarray(freqs, dtype=float).ravel()
    req = float(requested_hz)
    if f.size == 0 or not math.isfinite(req):
        return FreqSnap(requested_hz=req)
    idx = int(np.argmin(np.abs(f - req)))
    actual = float(f[idx])
    gaps = []
    if idx > 0:
        gaps.append(abs(actual - float(f[idx - 1])))
    if idx + 1 < f.size:
        gaps.append(abs(float(f[idx + 1]) - actual))
    return FreqSnap(requested_hz=req, actual_hz=actual,
                    step_hz=freq_grid_step(f),
                    local_step_hz=max(gaps) if gaps else float("nan"))


def combine_freq_snaps(snaps) -> Optional[FreqSnap]:
    """
    One FreqSnap for a whole run.  None when there is nothing to combine.

    Two traces may name two different files -- multi-file comparison is a
    feature, not an accident -- and two files rarely carry the same sweep, so a
    run does not always HAVE one frequency.  When the resolved points differ
    the combined snap says so (`agreed=False`) instead of picking one of them,
    which would be the same silent snap committed one level up.
    """
    snaps = [s for s in snaps if s is not None]
    if not snaps:
        return None
    resolved = [s for s in snaps if s.resolved]
    if not resolved:
        return FreqSnap(requested_hz=snaps[0].requested_hz)
    if len({s.actual_hz for s in resolved}) > 1:
        return replace(resolved[0], agreed=False)
    return resolved[0]


# ============================================================================
# Run snapshots -- what one finished Calculate leaves behind
# ============================================================================
#
# THE BUG THESE EXIST TO PREVENT.  _on_calculate writes its results onto the
# LIVE TraceConfig objects, and the render collections used to hold that live
# object: (tc, file_label, res).  Re-rendering such a collection after the next
# run -- or after any edit at all -- printed the NEW id / label / port
# descriptor beside the OLD numbers.  Nothing raises, nothing looks wrong, and
# the reader has no way to tell.
#
# The blast radius is exactly four fields, established by reading every
# renderer below: id, label, port_descriptor() and (for the shown/hidden
# filter) enabled -- plus color_idx, which App._append_swatched reads to tag
# the row.  Everything else was already immutable: `res` and `cres` are FRESH
# objects on every run, `file_label` is a str, and the fit summaries are
# already strings.  _format_coupling_block takes its matrix from
# cres.Z_matrix, never from tc.Zmat.
#
# port_desc is RESOLVED TO A STRING HERE.  port_descriptor() recomputes from
# the live spec fields, so storing the method (or the trace it is bound to)
# would reopen the hazard in a form that is harder to see.
#
# WHAT IS DELIBERATELY *NOT* IN A SNAPSHOT: Z, Zmat, fit_freqs, fit_Z and the
# per-curve aux arrays.  Measured envelope at 10 runs x 6 traces: the text and
# the rows are ~0.43 MB, while the arrays are 173 MB for a mode-6 run at 5000
# frequencies and 6 measurement ports, and 691 MB at 20000.  A snapshot's size
# must not depend on the sweep length; tests/test_run_snapshot.py pins that by
# measuring it at two sweep lengths and demanding the same answer.  (The one
# array a snapshot does reach is cres.Z_matrix, a G x G matrix at the single
# marker frequency, which is what the block prints.)

# A COMPOSED row's provenance, resolved at snapshot time like everything else
# here: ((alias, file_label), ...), home first.  It is EMPTY for a single-file
# trace, and that is what keeps every renderer below byte-identical for the
# case that is almost always the case -- including tests/fixtures/
# render_reference.json, which is the proof the page did not move.
#
# `file_label` stays the HOME file and keeps its meaning: it is what
# run_file_freq keys on and what the CSV heads a block with.  A composed row
# has BOTH, because "which sweep was this read against" and "which files is
# this built from" are different questions with different answers.
def _snapshot_files(tc: "TraceConfig") -> tuple:
    """((tag, file_label), ...) for a COMPOSED trace, else ()."""
    if not trace_is_composed(tc):
        return ()
    return tuple(trace_file_aliases(tc))


@dataclass(frozen=True)
class RowSnapshot:
    """One results-table row, resolved away from its TraceConfig."""
    id: int
    label: str
    port_desc: str
    enabled: bool
    color_idx: int
    file_label: str
    res: object                 # RLCResult -- a fresh object per run
    # ((alias, file_label), ...) when this row came from several files, () when
    # it came from one.  Declared LAST with a default, like `freqs` on
    # RunSnapshot and for the same reason: every construction in the repo is by
    # keyword, and a new field in the middle would silently reorder anything
    # that is not.
    files: tuple = ()
    # R3-5, FROZEN at snapshot time and rendered here rather than read live.
    # `ReferenceCheck` objects hang off the live trace and are replaced by the
    # next Calculate, so a run page holding them would print this run's numbers
    # under the next composition's verdict -- the hazard the whole snapshot
    # type exists for.  `ref_strip` is one line, `ref_lines` the full report,
    # both straight out of `reference_provenance` so the strip and the report
    # cannot disagree.  Empty on every single-file trace, which is what keeps
    # tests/fixtures/render_reference.json byte-identical.
    ref_strip: str = ""
    ref_warn: bool = False
    ref_lines: tuple = ()


@dataclass(frozen=True)
class CouplingSnapshot:
    """One mode-6 results block, resolved away from its TraceConfig."""
    id: int
    label: str
    port_desc: str
    enabled: bool
    color_idx: int
    file_label: str
    cres: object                # CouplingResult -- a fresh object per run
    # Where this block's marker frequency came from, against this file's own
    # sweep.  None for a block whose numbers Calculate did not produce this run
    # (a frozen trace, or one "Calculate This Trace" skipped): their cres was
    # resolved against some earlier request, and this run's request says
    # nothing true about them.  A None here renders exactly as before.
    freq: Optional[FreqSnap] = None
    # See RowSnapshot.files.
    files: tuple = ()
    # See RowSnapshot.ref_strip.
    ref_strip: str = ""
    ref_warn: bool = False
    ref_lines: tuple = ()


@dataclass(frozen=True)
class FitSnapshot:
    """One post-table fit summary line.

    `enabled` travels with it because _render_results drops the hidden rows,
    and a fit summary under a table with no such row is an orphan.
    """
    id: int
    enabled: bool
    text: str


@dataclass(frozen=True)
class RunSnapshot:
    """Everything one Calculate produced, as a record that cannot move.

    `number` is a MONOTONIC counter, not a value: two runs can be equal in
    every field and still be different runs, so nothing may key a run by
    equality (no sets, no value-keyed dicts).
    """
    number: int
    when: datetime
    # The frequency that was REQUESTED.  This is the run's identity and the
    # number the entry box was showing; where the values were actually read is
    # `freqs`, because that is a property of each FILE's sweep, not of the run.
    marker_freq_hz: float
    rows: tuple = ()
    blocks: tuple = ()
    fits: tuple = ()
    # The named _config_signature of every trace as this run found it, and the
    # diff against the run before it.  `signatures` is what the NEXT run diffs
    # against; `changed` is the rendered answer, frozen at the moment it was
    # true.  Both are tuples of strings, so a run record stays a record.
    signatures: tuple = ()
    prev_number: int = 0
    changed: tuple = ()
    # ((file_label, FreqSnap), ...): where `marker_freq_hz` actually landed, one
    # entry per file this run touched, resolved at Calculate time while the
    # frequency axes were in hand.  Declared LAST because every construction in
    # the repo is by keyword and a new field in the middle would silently
    # reorder anything that is not.  Floats only, so a run record still does not
    # grow with the sweep (tests/test_run_snapshot.py walks it to prove that).
    freqs: tuple = ()

    def with_visibility(self, traces) -> "RunSnapshot":
        """
        This run with each record's `enabled` re-read from the live traces.

        THE DEFAULT IS FROZEN: a run record is a record of what was measured,
        so hiding a trace tomorrow must not retroactively rewrite it, and
        _replot_from_cache stays the owner of "what is on the plot now".  This
        is the one deliberate exception, and it is only ever applied to the
        CURRENT run: re-rendering it (the units-mode switch) has always
        followed the visibility as it stands then, and it has to, because
        `enabled` gates the results table as well as the plot -- a row for a
        curve that is not drawn reads as a duplicate of the one that is.

        Matching is by trace id, which is unique and monotonic; a record whose
        trace is gone keeps the flag it was snapshotted with.
        """
        live = {tc.id: bool(tc.enabled) for tc in traces}

        def fix(recs):
            return tuple(
                replace(r, enabled=live[r.id]) if r.id in live else r
                for r in recs)

        return replace(self, rows=fix(self.rows), blocks=fix(self.blocks),
                       fits=fix(self.fits))


def _snapshot_reference(tc: "TraceConfig", *, provenance=None) -> dict:
    """The reference-node verdict as three frozen fields, or three empty ones.

    Rendered AT SNAPSHOT TIME, for the same reason `port_desc` is a resolved
    string: `tc.reference_checks` is replaced wholesale by the next Calculate,
    so a record holding the list would answer about a composition that has
    since been rebuilt.  R3-5 also requires that it be rendered exactly ONCE,
    because two copies of one verdict are two things that can come to disagree.

    `provenance` IS THAT RENDERER, INJECTED.  It is `reference_provenance`,
    which lives in `pkg_rlc_files_gui` at L5 -- three layers above this file --
    because rendering a composition's verdict for a reader is presentation and
    belongs beside the window that shows it.  So the render stays up there, the
    model stores the text it was handed, and `pkg_rlc_gui` supplies the
    argument.  This one signature is the whole of the exception; it takes a
    CALLABLE rather than an import so nothing at L1 names an L5 module.

    With no renderer supplied there is no verdict, and the three fields are
    empty -- the same answer a single-file trace gets, which is what keeps
    tests/fixtures/render_reference.json byte-identical.
    """
    if provenance is None:
        return {"ref_strip": "", "ref_warn": False, "ref_lines": ()}
    strip, lines = provenance(getattr(tc, "reference_checks", None) or [])
    if not strip:
        return {"ref_strip": "", "ref_warn": False, "ref_lines": ()}
    return {"ref_strip": strip[0], "ref_warn": bool(strip[1]),
            "ref_lines": tuple(lines)}


# `provenance` is threaded through both builders rather than reaching for a
# module-level default, so that the injection point is visible at every call
# and there is no global anyone can forget to set.  `pkg_rlc_gui` wraps all
# three of these and supplies `reference_provenance`; nothing else calls them.
def _snapshot_row(tc: "TraceConfig", file_label: str, res,
                  *, provenance=None) -> RowSnapshot:
    return RowSnapshot(id=tc.id, label=tc.label,
                       port_desc=tc.port_descriptor(),
                       enabled=bool(tc.enabled), color_idx=int(tc.color_idx),
                       file_label=file_label, res=res,
                       files=_snapshot_files(tc),
                       **_snapshot_reference(tc, provenance=provenance))


def _snapshot_block(tc: "TraceConfig", file_label: str,
                    cres, freq: Optional[FreqSnap] = None,
                    *, provenance=None) -> CouplingSnapshot:
    return CouplingSnapshot(id=tc.id, label=tc.label,
                            port_desc=tc.port_descriptor(),
                            enabled=bool(tc.enabled),
                            color_idx=int(tc.color_idx),
                            file_label=file_label, cres=cres, freq=freq,
                            files=_snapshot_files(tc),
                            **_snapshot_reference(tc, provenance=provenance))


def _snapshot_fit(tc: "TraceConfig", text: str) -> FitSnapshot:
    return FitSnapshot(id=tc.id, enabled=bool(tc.enabled), text=text)
