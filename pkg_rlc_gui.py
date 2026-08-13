"""
pkg_rlc_gui.py  --  Tkinter GUI for PKG RLC Extractor.

Layout (horizontal PanedWindow):
  Left  ~ 460 px : Files / Traces / Editor / Global controls
  Right          : Results text (top) + plot panel (bottom)
                   (vertical PanedWindow, sash draggable by user)

Measurement modes (integer codes are stable and never renumbered):
  1  Port(s) -> GND
  2  A <-> B
  3  A <-> B + Short Pairs
  4  RETIRED (A <-> B + VDD/GND).  For AC small-signal VDD *is* an AC ground,
     so a mode-4 trace migrates to mode 2 with its VDD ports folded into GND.
  5  Custom (advanced) -- the Mode 5 termination DSL
  6  +/- Ports / Coupling (M, k) -- any number of measurement ports, each a
     pair of probes (red = plus side, black = minus side).  Produces a G x G
     impedance matrix: self impedance on the diagonal, open-circuit mutual
     impedance off it, from which M, k, C_c and the M/L ratios are read.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import traceback
from dataclasses import asdict, astuple, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

from pkg_rlc_core import (
    CONN_KINDS,
    CONN_KINDS_WITH_NET,
    CONN_KINDS_WITH_PARTNER,
    CONN_KINDS_WITH_RLC,
    DEFAULT_Z0,
    RECIPROCITY_WARN,
    SI_SUFFIXES,
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
    Open,
    OVERVIEW_BUCKETS,
    PortRole,
    ROLE_ELEMENT,
    ROLE_GROUND,
    ROLE_OPEN,
    ROLE_PROBE_MINUS,
    ROLE_PROBE_PLUS,
    ROLE_SHORTED,
    ROLE_VDD,
    ShortPair,
    Signal,
    TerminationSet,
    TouchstoneData,
    Vdd,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_mode4,
    build_terminations_rows,
    compute_z,
    compute_z_matrix,
    eval_capacitor_model,
    eval_inductor_model,
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    fit_auto,
    fit_capacitor,
    fit_inductor,
    inert_lumped_messages,
    merged_nodes,
    parallel_stamp_messages,
    parse_custom_termination_text,
    parse_kv_rlc_params,
    parse_mport_spec,
    parse_port_range,
    parse_short_pairs,
    parse_si,
    parse_touchstone,
    collapse_ports,
    open_name_clusters,
    open_port_name_messages,
    port_roles,
    row_sources,
    resolve_meas_ports,
    rows_to_dsl_text,
    dsl_text_to_rows,
    s_to_y,
    y_series_rlc,
    diagnose_touchstone,
    format_si,
    TouchstoneParseError,
)
from pkg_rlc_plot import (
    COLORS, LINESTYLES, MAX_LABEL_LEN, PlotPanel, ReflowRow,
    Trace as PlotTrace,
)
from pkg_rlc_help import HelpWindow
# The connections table's SHAPE, and the RowTable vocabulary it is spoken in,
# now live in pkg_rlc_conntable (they are pure -- no Tk, no App -- and the
# editor is not the only reader).  Imported by name and RE-EXPORTED here, the
# same precedent as the Mode 5 DSL helpers below: `from pkg_rlc_gui import
# conn_table_layout` and friends keep resolving for every existing caller and
# every test.
from pkg_rlc_conntable import (
    CONN_KIND_HINTS,
    CONN_NET_KEY,
    CONN_NET_SUPPORTED,
    CONN_OFF_GLYPH,
    CONN_ON_GLYPH,
    CONN_TABLE_COLUMNS,
    CONN_TABLE_HINT,
    CONN_TABLE_HINT_GENERAL,
    CONN_TABLE_HINT_SHORT,
    CONN_TABLE_HINT_TAGGED,
    ColumnSpec,
    HINT_SHORT_CHARS,
    TableLayout,
    _CONN_COL_C,
    _CONN_COL_L,
    _CONN_COL_ON,
    _CONN_COL_PORT,
    _CONN_COL_R,
    _CONN_COL_SECOND,
    _CONN_COL_TYPE,
    _CONN_NCOLS,
    _conn_row_cells,
    _join_short_group,
    conn_cells_from_row,
    conn_hint_text,
    conn_row_from_cells,
    conn_table_layout,
    identity_layout,
)
# The generic Tk widgets -- the ones that know nothing about a trace, a file or
# a termination -- now live in pkg_rlc_widgets.  Same re-export rule as above:
# `from pkg_rlc_gui import RowTable` and friends keep resolving.  `StylePicker`
# is deliberately NOT there and stays below in this file: it draws from COLORS
# and LINESTYLES, which live in pkg_rlc_plot, and pkg_rlc_plot imports
# ReflowRow from pkg_rlc_widgets -- so reaching back for the palettes would be
# a module-level cycle.
from pkg_rlc_widgets import (
    PLACEHOLDER_FG,
    PlaceholderEntry,
    PlaceholderText,
    RowTable,
    _CollapsibleHint,
    _tk_dash,
    editor_scroll_fraction,
)
# Turning a finished run into TEXT -- the three results views and every
# formatter under them, the run-tab and Log-tab labels, the run-to-run diff and
# the frequency-provenance types the reports print through.  All pure, which is
# what lets tests/fixtures/render_reference.json pin the rendered page
# byte-for-byte with no display.  Re-exported, same rule as above.
#
# `_tag_swatch_rows` stayed in this file on purpose: it WRITES INTO A Tk Text
# and is therefore not a formatter.  So did `trace_signature_fields` /
# `run_signatures`, which read a live TraceConfig.
from pkg_rlc_report import (
    COMPARE_SEG_MIN,
    COMPARE_STACK_LINES_MAX,
    COUPLING_FLOOR_DB,
    COUPLING_LEGEND_LINES,
    FREQ_EXACT_FRAC,
    FREQ_EXACT_REL,
    FREQ_UNIFORM_TOL,
    FREQ_WIDE_FMT,
    FreqSnap,
    LOG_BADGE_CAP,
    LOG_ERROR,
    LOG_INFO,
    LOG_WARN,
    RESULTS_PANE_COLS,
    RESULTS_SWATCH,
    RESULTS_VIEWS,
    RUN_AUTO_DEFAULT,
    RUN_AUTO_MAX_UI,
    RUN_CHANGE_ITEMS,
    RUN_CHANGE_VALUE_W,
    RUN_KEPT_GLYPH,
    RUN_MARK_NEW,
    RUN_MARK_SEEN,
    RUN_OPEN_GLYPH,
    RUN_TABS_DEFAULT,
    RUN_TABS_HARD_CAP,
    RUN_TABS_MIN,
    SUMMARY_LABEL_MAX,
    VIEW_COMPARE,
    VIEW_DETAIL,
    VIEW_SUMMARY,
    _ALIGNED_PREFIXES,
    _SWATCH_PAD,
    _TABLE_BASE_UNITS,
    _aligned_prefix_for,
    _compare_groups,
    _compare_head_cells,
    _delta_cell,
    _file_alias_map,
    _file_cell,
    _fmt_aligned,
    _fmt_plain,
    _format_coupling_block,
    _format_compare,
    _format_results_table,
    _format_summary_coupling,
    _format_summary_self,
    _format_z_matrix,
    _pair_flag,
    _pair_strength,
    _pair_strength_db,
    _render_columns,
    _row_file_labels,
    _sign_flag,
    _snapshot_file_legend,
    _summary_self_rows,
    _table_freq_note,
    _trunc_str,
    _value_formatter,
    _wrap_name,
    combine_freq_snaps,
    describe_run_change,
    freq_grid_step,
    keep_button_label,
    log_tab_label,
    marker_freq_text,
    rank_coupling_pairs,
    run_change_line,
    run_file_freq,
    run_freq_snap,
    run_headline,
    run_stale_banner,
    run_tab_label,
    run_trace_ids,
    snap_to_grid,
    _run_marker_text,
)
# `default_alias` is the ONE place that turns a file's position in a trace into
# the tag its ports wear (F1, F2, ...), and it lives in pkg_rlc_compose because
# that is what parses the tag back.  pkg_rlc_files_gui imports the same
# function for the same reason -- a second copy here is how the tag a port cell
# shows and the tag the engine resolves come to disagree.
#
# Measured marginal import cost with pkg_rlc_core (which this file already
# imports) warm: 3.0 / 2.9 / 8.3 ms in three fresh processes, against a
# documented 349 / 352 / 364 ms for `import pkg_rlc_gui` itself.  No cycle:
# pkg_rlc_compose imports pkg_rlc_core and numpy and nothing else.
import pkg_rlc_compose as comp
from pkg_rlc_compose import default_alias
# `_collect_nets` is reached by name on purpose.  It is the ONE definition of
# which tokens in a Mode 5 DSL block are NODE NAMES rather than port fields,
# and `_scope_dsl_text` has to skip exactly those.  A second copy here would
# let the field this file rewrites and the field core resolves disagree, which
# is the drift this repo has been bitten by (RECIPROCITY_WARN, and the two
# definitions of "which files is this trace made of" that `trace_file_labels`
# now has to keep mirrored).  It never raises for a malformed line.
from pkg_rlc_core import _collect_nets
# The Attribution window lives in its own module (pkg_rlc_attrib_gui);
# this file carries only the hooks below.  It is imported at module
# level, NOT lazily, for two measured reasons.
#
# (a) It is not a cycle.  pkg_rlc_attrib_gui imports pkg_rlc_gui only from
#     inside functions (its `_gui()`), so whichever of the two is imported
#     first, the other is complete in sys.modules by the time it is touched.
# (b) It costs 9.4 / 10.2 / 11.5 ms in three fresh processes, against 349 /
#     352 / 364 ms for `import pkg_rlc_gui` itself and a measured 258 ms for
#     App() construction -- about 1.6% of GUI startup.  Nothing on the CLI path
#     pays it at all: pkg_rlc_extractor imports pkg_rlc_gui only inside the
#     GUI-launch branch.  A deferred import would have bought that 10 ms at the
#     price of a SECOND copy of ATTRIB_MENU_LABEL in this file (the menu entry
#     needs the label at build time), and a menu path spelled in two places is
#     exactly the drift the "Show Ports needed five pointers" history warns
#     about.
from pkg_rlc_attrib_gui import (
    ATTRIB_MENU_LABEL,
    apply_attribution_session_state,
    attribution_session_state,
    live_windows as attribution_windows,
    open_attribution_window,
    refresh_attribution_windows,
)
# The file UI (R3-2 / R3-3) and the GUI half of the reference-node check
# (R3-5).  Same split, same reasons, as pkg_rlc_attrib_gui above: it is a
# module of its own because this file is 8000+ lines, it is imported at module
# level because the menubar needs FILES_MENU_LABEL at build time and a second
# copy of a menu path is the drift the "Show Ports needed five pointers"
# history warns about, and it is not a cycle -- pkg_rlc_files_gui imports
# pkg_rlc_gui only from inside functions.
#
# Measured marginal import cost with pkg_rlc_core and pkg_rlc_compose (which
# this file already imports) warm: 11.8 / 14.5 / 13.7 ms in three fresh
# processes, against a documented 349 / 352 / 364 ms for `import pkg_rlc_gui`
# itself -- and it is already paid today, because pkg_rlc_attrib_gui imports
# it at module level for the reference-node strip.  Nothing on the CLI path
# pays it at all: pkg_rlc_extractor imports pkg_rlc_gui only inside the
# GUI-launch branch.
from pkg_rlc_files_gui import (
    FILES_MENU_LABEL,
    FILES_TITLE,
    files_refusal,
    open_files_window,
    reference_provenance,
    refresh_files_windows,
)


# ============================================================================
# Helpers
# ============================================================================
#
# SI_SUFFIXES, parse_si, parse_kv_rlc_params and parse_custom_termination_text
# now live in pkg_rlc_core (terminations belong to core). They are re-exported
# here so `from pkg_rlc_gui import parse_si` and friends keep resolving.
#
# build_terminations_mode4 is likewise kept in the import list purely as a
# re-export: mode 4 is retired in the GUI (VDD is an AC ground, so it folds
# into mode 2), but the CLI and old scripts still reference the builder.


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


# ============================================================================
# A trace's FILE SET (one home file, plus the files it is composed with)
# ============================================================================
#
# DEFAULT SCOPE, NOT PER-CELL SCOPE.  The trace has a home file; a bare port
# number means the home file; a tag appears only on an endpoint that crosses
# into another file.  That is not a preference, it is the only option that
# fits: measured, a per-row file COLUMN costs 451 px against the editor's
# 431 px viewport (two columns 497 px, widening Port/To to 11 chars 461 px, a
# Name column 469 px), and the string 'EM.23,24,25' renders 70 px into a ~55 px
# visible combo area.  It is also what makes every pre-existing spec, golden
# case and saved session keep its meaning byte for byte.
#
# Everything below is pure and Tk-free, so the whole schema is testable without
# a display -- the same split `session_to_dict` / `session_from_dict` follow.

def trace_file_labels(tc: "TraceConfig") -> list[str]:
    """
    Every file this trace is built from, HOME FIRST, deduplicated.

    Home first is not cosmetic: the tag is the POSITION, so the home file is
    F1 and stays F1 when a second file is added or removed.  A tag that moved
    would silently re-point every tagged port cell in the connection table at
    a different file.

    This rule is MIRRORED, line for line, by pkg_rlc_files_gui's function of
    the same name -- that module reads a trace through `TRACE_FILES_FIELD` /
    `TRACE_HOME_FIELD` so it can degrade gracefully on a build without this
    schema, and the two must not answer differently.  Anything that would
    change the answer (stripping, sorting, keeping a repeat) has to change on
    both sides or not at all; the normalising is therefore done ON THE WAY IN
    (see _TRACE_STRLIST_FIELDS), never here.
    """
    home = str(getattr(tc, "file_label", "") or "")
    out = [home] if home else []
    for lbl in (getattr(tc, "file_labels", None) or []):
        lbl = str(lbl or "")
        if lbl and lbl not in out:
            out.append(lbl)
    return out


def trace_file_aliases(tc: "TraceConfig") -> list[tuple[str, str]]:
    """[(tag, file_label), ...], home first.  The tag IS the position."""
    return [(default_alias(i), lbl)
            for i, lbl in enumerate(trace_file_labels(tc))]


def trace_is_composed(tc: "TraceConfig") -> bool:
    """True when this trace is built from more than one file."""
    return len(trace_file_labels(tc)) > 1


def trace_file_legend(tc: "TraceConfig", sep: str = " + ") -> str:
    """'F1=die.s6p + F2=package.s4p' -- what this trace is built from."""
    return sep.join(f"{a}={lbl}" for a, lbl in trace_file_aliases(tc))


def trace_file_scope(tc: "TraceConfig") -> str:
    """
    The file set as ONE comparable, rendered value for _config_signature.

    Deliberately EMPTY for a single-file trace -- which is every trace that
    existed before this schema -- so nothing about an existing run's diff
    moves.  The home file on its own is already watched, by `file_label`.
    """
    return trace_file_legend(tc) if trace_is_composed(tc) else ""


def compose_spec_problems(tc: "TraceConfig",
                          loaded_labels: Optional[Sequence[str]] = None
                          ) -> list[str]:
    """
    What is wrong with this trace's FILE SET -- one message per problem.

    NEVER RAISES.  It is written to be callable from the editor strips, which
    run inside Tk variable traces where a raised error reaches no handler you
    control: Tk prints it and the GUI carries on showing a stale verdict.  It
    reports; `compose()` is what refuses.

    `loaded_labels` is optional because the pure half of this has to be
    testable without an App; with it, a file that is named but not loaded is
    reported here rather than only at Calculate.
    """
    msgs: list[str] = []
    listed = [str(lbl or "") for lbl in
              (getattr(tc, "file_labels", None) or [])]
    home = str(getattr(tc, "file_label", "") or "")
    if any(not lbl.strip() for lbl in listed):
        msgs.append("a file entry is empty; it contributes nothing")
    # A REPEAT counts ONCE (trace_file_labels drops it), and that has to be
    # said out loud rather than silently repaired: "the same file twice" looks
    # like two blocks of one part, but a label resolves through
    # _file_by_label to ONE FileEntry, so the second copy could only ever be
    # the first block again -- and the tag it would have had is then a tag no
    # port cell can use.
    for lbl in dict.fromkeys(listed):
        if not lbl.strip():
            continue
        n = listed.count(lbl) + (1 if lbl == home else 0)
        if n > 1:
            msgs.append(
                f"'{lbl}' is listed {n} times and counts once: a file label "
                f"names one loaded file, so a second copy of it is the same "
                f"block again")
    if loaded_labels is not None:
        for lbl in trace_file_labels(tc):
            if lbl not in loaded_labels:
                msgs.append(f"file '{lbl}' is not loaded")
    return msgs


# ============================================================================
# The composed network: what a multi-file trace is actually solved against
# ============================================================================
#
# `pkg_rlc_compose` does every piece of arithmetic -- the stacking, the
# frequency plan, the namespace, the reference-node check -- and everything
# here is the GUI's side of the seam: which files, which scope, and what to do
# with the answer.  Same split as pkg_rlc_attrib / pkg_rlc_attrib_gui.
#
# THE ONE PROPERTY EVERYTHING BELOW RESTS ON: the home file is block 0 of the
# composition, at offset 0, with every port kept -- so a BARE port number in a
# composed trace is already the correct global index, and a port field that
# carries no tag needs no rewriting at all.  That is what makes "default file
# scope" (R3-2) free rather than a translation layer: measured on
# coupled_2port_gndref.s2p + pi_2port.s2p, `parse_scoped_ports('1', net,
# default='F1')` is [1] and `('2', ...)` is [2], while 'F2.1' is [3].
#
# It is also what makes the refusal free.  A bare port number PAST the home
# file's port count would otherwise silently address the second file's ports
# ('3' on a 2-port home would be F2.1), which is the same class of silent
# wrong answer as the weld.  `net.gport` raises there, by name, with the port
# map attached -- so every port field goes through parse_scoped_ports rather
# than being passed through when it has no tag.


class ComposeSpecError(ValueError):
    """A port field that does not resolve in the composed namespace.

    A plain ValueError from here would be indistinguishable from core's own
    port errors in _on_calculate's handler, which is where the difference
    matters: core's message names a bare port number, and on a composed
    network a bare port number names nothing anyone can act on.
    """


def _scope_port_field(spec: str, net, home: str,
                      skip: Sequence[str] = ()) -> str:
    """
    One port field, scoped: 'F2.13,14' -> '19-20';  '3' -> '3'.

    `skip` is the set of NODE NAMES in the same spec (lower-cased).  A node
    name is a port field too -- `tap lumped_between 30 L=10f` -- and it is not
    a port reference, so it passes through untouched and core resolves it.
    Everything else goes through `parse_scoped_ports`, tag or no tag, because
    that is where the "this is not a port of the home file" refusal lives.

    THE SCOPE RULE IS THAT FUNCTION'S, NOT THIS ONE'S: every comma token
    carries its own scope, and a BARE token is always the home file.

    This used to hold a second rule of its own -- it split the field here and
    made a tag STICKY over the tokens after it -- because `parse_scoped_ports`
    refused a tag on any but the first token, while the connection table has to
    be able to spell a die-to-package tie in ONE cell (`_join_short_group`: a
    group of shorted pins has no from/to, so `25,26,F2.15` has no other
    spelling there).  The sticky reading made

        'F2.15,25,26'   ->  three PACKAGE ports

    which contradicts the rule this tool states everywhere else -- a bare
    number is a port of the HOME file, in every mode -- and contradicts it in
    silence: it only needs the package to have ports 25 and 26.  Per-token now
    lives in `parse_scoped_ports` itself, so GUI and CLI cannot drift on what a
    field means and there is no parsing left here.  The agreement is
    pinned in tests/test_multifile_engine.py::TestScopePortField.

    `collapse_ports` never emits a space, which is what makes the rewritten
    text still tokenise as ONE field in a whitespace-split DSL.
    """
    text = (spec or "").strip()
    if not text or text.lower() in skip:
        return text
    try:
        ports = comp.parse_scoped_ports(text, net, default=home)
    except comp.ComposeError as e:
        raise ComposeSpecError(str(e)) from None
    return collapse_ports(ports) if ports else text


#: DSL keywords whose FIRST remaining token is a second port field.  `short`
#: takes its whole group in the port field and has no partner; `lumped_between`
#: and `short_to` do.  Anything else (ground / vdd / open / signal /
#: lumped_to_gnd) has no second port field at all.
_DSL_SECOND_PORT_KINDS = ("short_to", "lumped_between")


def _scope_dsl_text(text: str, net, home: str) -> str:
    """
    Rewrite the port FIELDS of a Mode 5 DSL block into the composed namespace.

    Field positions, not a blanket token scan: `parts[0]` is always a port
    field and `parts[2]` is one after `short_to` / `lumped_between`, and
    nothing else in the grammar is.  A blanket scan would have to decide what
    `C=1.5p` is -- `_split_tag` says its head is `C=1`, which fails the alias
    pattern, so it survives today -- but a signal group legitimately named
    `F1.something` would not, and a rewrite that depends on a group name is a
    silent re-pointing.

    Node names are collected through core's own `_collect_nets`, by name and
    on purpose: it is the ONE definition of which tokens in this text are node
    names, and a second copy here would let the field this rewrites and the
    field core resolves disagree.  It never raises for a malformed line (the
    main pass does, with the line number), so a failure here degrades to "no
    node names" and the main pass still gives its own message.
    """
    try:
        nets = set(_collect_nets(text).keys())
    except Exception:
        nets = set()
    out: list[str] = []
    for raw in (text or "").splitlines():
        body, sep, comment = raw.partition("#")
        parts = body.split()
        if not parts:
            out.append(raw)
            continue
        # The indent and the run of spaces in front of a trailing comment are
        # kept, so the transform is as close to identity as the field rewrite
        # allows: `extra_lines` is shown verbatim in "Edit as text…", and a
        # block that reflowed itself every time it was scoped would look like
        # the tool had edited the user's text.
        lead = body[:len(body) - len(body.lstrip())]
        trail = body[len(body.rstrip()):]
        parts[0] = _scope_port_field(parts[0], net, home, nets)
        if len(parts) >= 3 and parts[1].lower() in _DSL_SECOND_PORT_KINDS:
            parts[2] = _scope_port_field(parts[2], net, home, nets)
        out.append(lead + " ".join(parts) + trail
                   + (sep + comment if sep else ""))
    text_out = "\n".join(out)
    if (text or "").endswith(("\n", "\r")) and not text_out.endswith("\n"):
        text_out += "\n"
    return text_out


def _scope_conn_rows(rows: Sequence, net, home: str) -> list:
    """Connection rows with their two port cells scoped.  Copies; never edits."""
    names = {(r.net or "").strip().lower()
             for r in rows if (r.net or "").strip()}
    return [replace(row,
                    ports=_scope_port_field(row.ports, net, home, names),
                    to=_scope_port_field(row.to, net, home, names))
            for row in rows]


def _scope_mport_rows(rows: Sequence, net, home: str) -> list:
    """Measurement-port rows with both probe sides scoped.  Copies."""
    return [replace(r,
                    plus=_scope_port_field(r.plus, net, home),
                    minus=_scope_port_field(r.minus, net, home))
            for r in rows]


#: A port field is echoed only when it TAGS a file.  A bare field on a
#: composition is the home file by a rule with no exception left in it, and
#: echoing `25 = F1.25` on every row would spend the strip's two lines saying
#: nothing.  A tagged field is where the reading is worth confirming: it is the
#: only place the scope changes, it is what the per-token rule changed, and
#: `F2.40,42` -- package 40 and HOME 42 -- is the one spelling that still looks
#: like something else.
def _field_has_tag(spec: str) -> bool:
    return any(comp._split_tag(t.strip())[0]
               for t in (spec or "").split(",") if t.strip())


def scope_echo_messages(mport_rows: Sequence, conn_rows: Sequence,
                        extra_lines: str, net, home: str) -> list[tuple]:
    """
    What every TAGGED port field actually resolved to -> [(text, anchor)].

    Takes the ORIGINAL rows, never the scoped ones: scoping rewrites a field to
    global indices and the tag is gone by then, so an echo built from the output
    could only repeat the number it is trying to explain.

    The echo is the answer to "which file is that port of", asked of the one
    thing that can answer it -- the resolver the solve itself uses.  It is not a
    second reading of the spec: `_scope_port_field` is called here exactly as
    Calculate calls it, and `describe_ports` is the compose module's own
    renderer, so a drift between what is echoed and what is computed is not
    expressible.

    MUST NOT RAISE -- it runs from the strips, on every keystroke, where a
    raised exception reaches no handler we control.  A field that does not
    resolve gets NO echo and the ordinary validation reports it: two messages
    about one broken cell, one of which is a green tick, is worse than one.
    """
    out: list[tuple] = []
    if net is None:
        return out

    def _one(spec: str, anchor, what: str) -> None:
        text = (spec or "").strip()
        if not text or not _field_has_tag(text):
            return
        try:
            ports = comp.parse_scoped_ports(text, net, default=home)
        except Exception:
            return
        if not ports:
            return
        out.append((f"✓ {what} {text} = {net.describe_ports(ports)}", anchor))

    try:
        live_mp = [r for r in mport_rows if not r.is_blank()]
        for i, row in enumerate(live_mp):
            _one(row.plus, ("mport", i), f"measurement port row {i + 1} '+':")
            _one(row.minus, ("mport", i), f"measurement port row {i + 1} '−':")
        live_conn = [r for r in conn_rows if not r.is_blank()]
        names = {n.lower() for n in _collect_nets_safe(conn_rows)}
        for i, row in enumerate(live_conn):
            anchor = ("conn", i)
            # A switched-off row is not in the spec, so what its ports WOULD
            # have resolved to is not a fact about the network -- and the
            # switched-off line above already names the row.  Enumerated
            # anyway so the anchors keep matching the screen.
            if not getattr(row, "enabled", True):
                continue
            if (row.ports or "").strip().lower() not in names:
                _one(row.ports, anchor, f"connection row {i + 1} Port:")
            if row.kind in CONN_KINDS_WITH_PARTNER and \
                    (row.to or "").strip().lower() not in names:
                _one(row.to, anchor, f"connection row {i + 1} To:")
        for line in (extra_lines or "").splitlines():
            parts = line.split()
            if parts and _field_has_tag(parts[0]):
                _one(parts[0], None, "kept text:")
    except Exception:                       # pragma: no cover - MUST NOT RAISE
        pass
    return out


def _collect_nets_safe(conn_rows: Sequence) -> list[str]:
    """The node names in these rows, or none if they cannot be read."""
    try:
        return [r.net.strip() for r in conn_rows
                if getattr(r, "net", "") and r.net.strip()]
    except Exception:                       # pragma: no cover
        return []


def _check_bare_ports(ports: Sequence[int], net, home: str, what: str) -> None:
    """
    Refuse a bare port index that is past the home file's ports.

    For every field that goes through `_scope_port_field` this is free -- the
    resolver raises.  Mode 3's Short Pairs field does NOT go through it:
    `parse_short_pairs` reads its tokens with `int()`, so a tag there already
    fails with core's own message, but a bare '3' on a 2-port home file would
    quietly become F2.1.  This is the one field that needs the check spelled
    out, and spelling it out is cheaper than a second parser.
    """
    for p in sorted({int(p) for p in ports}):
        comp.parse_scoped_ports(str(p), net, default=home)


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


def _namespace_network(entries: Sequence["FileEntry"]):
    """
    The PORT NAMESPACE of a composition, without composing anything.

    `parse_scoped_ports`, `gport`, `local_of`, `port_labels` and
    `describe_ports` read nothing but `ComposedNetwork.blocks`, so 'what does
    F2.3 mean' can be answered from the files' port counts alone -- no S -> Y,
    no interpolation, no stacked matrix.  `Y` is `zeros((0, n, n))` because
    `ComposedNetwork.nports` reads `Y.shape[-1]`; it allocates nothing.

    That is what makes the validation strip and the Ports & Roles window
    understand a tagged port cell IMMEDIATELY, on the keystroke that types it,
    instead of after a Calculate.  Measured, the real thing is not an option
    there: `comp.compose` of 16 + 153 ports at 401 points is 10.5 SECONDS
    (10780 / 10346 / 10521 ms, three runs), and these two run once per
    keystroke.  This one is a list comprehension over the file list.

    The blocks are built with the SAME rule `_trace_network` uses -- home
    first, every port kept, `default_alias` for the tag -- because a namespace
    that disagreed with the one Calculate solves against would validate a spec
    that then addresses different ports.
    """
    blocks, offset = [], 0
    for i, fe in enumerate(entries):
        n = int(fe.ts.nports)
        blocks.append(comp.FileBlock(
            alias=default_alias(i), label=fe.label, offset=offset,
            local_ports=list(range(1, n + 1)), z0=float(fe.ts.z0),
            port_names=[(fe.ts.port_names[k] if k < len(fe.ts.port_names)
                         else "") for k in range(n)],
            nports_original=n))
        offset += n
    total = offset
    return comp.ComposedNetwork(freqs=np.zeros(0), Y=np.zeros((0, total, total)),
                                blocks=blocks)


def _fmt_port_terminal(spec: str) -> str:
    """Render a port terminal spec: '1' -> '1', '2,3' -> '{2,3}', '' -> '?'."""
    try:
        ports = parse_port_range(spec)
    except Exception:
        return spec.strip() or "?"
    if not ports:
        return "?"
    if len(ports) == 1:
        return str(ports[0])
    return "{" + ",".join(str(p) for p in ports) + "}"


def _fmt_port_set(spec: str) -> str:
    """Render a port-set spec (gnd/vdd): always bracketed. '' -> '[]'."""
    try:
        ports = parse_port_range(spec)
    except Exception:
        return f"[{spec.strip()}]"
    return "[" + ",".join(str(p) for p in ports) + "]"


def _fmt_short_pairs(spec: str) -> str:
    """Render short-pair groups: '1-2,3-4-5' -> '[1-2,3-4-5]'."""
    s = (spec or "").strip()
    return f"[{s}]"


def _union_port_specs(*specs: str) -> str:
    """
    Merge port-range specs into one sorted comma list ('5' + '7,8' -> '5,7,8').

    Used by the retired-mode-4 migration to fold VDD ports into GND.  If a spec
    cannot be parsed it is passed through verbatim rather than silently lost.
    """
    ports: list[int] = []
    leftovers: list[str] = []
    for spec in specs:
        text = (spec or "").strip()
        if not text:
            continue
        try:
            ports.extend(parse_port_range(text))
        except Exception:
            leftovers.append(text)
    merged = [str(p) for p in sorted(set(ports))]
    merged.extend(leftovers)
    return ",".join(merged)


def _fmt_mport(name: str, plus: str, minus: str) -> str:
    """Render one measurement port: 'tank:1/2', '3,4', 'rx:{5,6}'."""
    body = _fmt_port_terminal(plus)
    if (minus or "").strip():
        body += "/" + _fmt_port_terminal(minus)
    name = (name or "").strip()
    return f"{name}:{body}" if name else body


def _mport_more_lines(text: str) -> list[str]:
    """Non-empty, non-comment lines of the 'More ports' box."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _port_descriptor(tc: "TraceConfig") -> str:
    if tc.mode == 1:
        return f"M1: S:{_fmt_port_set(tc.port_a)} G:{_fmt_port_set(tc.gnd_ports)}"
    if tc.mode == 2:
        return (f"M2: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(tc.gnd_ports)}")
    if tc.mode == 3:
        return (f"M3: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(tc.gnd_ports)} S:{_fmt_short_pairs(tc.short_pairs)}")
    if tc.mode == 4:
        # Retired: shown only if a stale config has not been migrated yet.
        return (f"M4→M2: {_fmt_port_terminal(tc.port_a)}↔{_fmt_port_terminal(tc.port_b)} "
                f"G:{_fmt_port_set(_union_port_specs(tc.gnd_ports, tc.vdd_ports))}")
    if tc.mode == 6:
        tc.migrate_legacy_mports()
        parts = [_fmt_mport(r.name, r.plus, r.minus) for r in tc.mports
                 if r.plus.strip() or r.minus.strip()]
        body = " ".join(parts[:3]) if parts else "(empty)"
        if len(parts) > 3:
            body += f" +{len(parts) - 3}"
        return f"M6: {body} G:{_fmt_port_set(tc.gnd_ports)}"
    if tc.mode == 5:
        # No side effects here: unlike the mode-6 branch above this does NOT
        # call the migration, because that would consume it silently and the
        # user would never see the Results-pane message explaining what moved.
        #
        # With both tables empty the spec lives entirely in extra_lines (a
        # migration that kept an order-dependent spec verbatim, or an import
        # through 'Edit as text…').  Reporting '(no probe) C:0' for that is a
        # positive false claim in the very column the user reads to confirm
        # what was computed -- so fall back to showing the text, exactly as the
        # unmigrated custom_text case below it does.
        if not (tc.mports or tc.conn_rows):
            text = (tc.extra_lines or tc.custom_text or "").strip()
            if text:
                text = " ".join(text.split())
                return f"M5: {text[:25]}..." if len(text) > 28 else f"M5: {text}"
        parts = [_fmt_mport(r.name, r.plus, r.minus) for r in tc.mports
                 if r.plus.strip() or r.minus.strip()]
        body = " ".join(parts[:2]) if parts else "(no probe)"
        if len(parts) > 2:
            body += f" +{len(parts) - 2}"
        desc = f"M5: {body} C:{len(tc.conn_rows)}"
        # Rows AND kept text: the text is in force too and is emitted last, so
        # it wins.  Say it is there rather than describe the rows as the whole
        # spec.
        if (tc.extra_lines or "").strip():
            desc += "+txt"
        return desc
    return f"M?: mode={tc.mode}"


# ============================================================================
# Mode 5 helpers (the connection table <-> the DSL text)
# ============================================================================

def _import_dsl_text(text: str) -> tuple[list, list, str, bool]:
    """
    DSL text -> (mport_rows, conn_rows, extra_lines, meaning_changed).

    `meaning_changed` is True when routing the text through the tables would
    not compute the same thing, and the caller must then keep `text` verbatim
    in extra_lines instead of using the rows.  It is decided by comparing the
    RESOLVED TerminationSet before and after the round trip -- per-port
    termination types, couplings, and the measurement ports resolve_meas_ports
    produces -- because dsl_text_to_rows discards line order while the DSL is
    last-assignment-wins.

    Never raises.  On any internal failure it returns the same safe fallback
    ([], [], text, True), which is what makes "a malformed old spec migrates
    instead of raising during load" hold by construction rather than by the
    accident of dsl_text_to_rows happening to be total today.
    """
    try:
        mports, conn, extra = dsl_text_to_rows(text)
        if _dsl_meaning(text) == _dsl_meaning(
                rows_to_dsl_text(mports, conn, extra)):
            return mports, conn, extra, False
    except Exception:
        pass
    return [], [], text, True


def _dsl_meaning(text: str):
    """
    A comparable fingerprint of what a DSL spec computes, or None if it does
    not parse.  Used only to decide whether text may become rows.

    The port count handed to the resolver is one past the largest port the spec
    mentions: resolve_meas_ports only scans 0..n-1, so a smaller window would
    hide a difference at the far end of the spec.
    """
    try:
        term = parse_custom_termination_text(text)
    except Exception:
        return None
    ports = set(term.per_port)
    for cpl in term.couplings:
        ports.add(cpl.port_i)
        ports.add(cpl.port_j)
    n = (max(ports) + 1) if ports else 0
    try:
        mports = [(mp.name, tuple(mp.plus), tuple(mp.minus))
                  for mp in resolve_meas_ports(term, n)]
    except Exception:
        # A spec whose probes do not resolve still has a meaning -- "it fails"
        # -- and one side failing while the other does not IS a change.
        mports = None
    return (
        {p: type(t).__name__ for p, t in term.per_port.items()},
        [(type(c).__name__, c.port_i, c.port_j) for c in term.couplings],
        mports,
    )


def _ordering_diff_summary(text: str) -> str:
    """
    Name the ports whose termination changes when `text` goes through the
    tables.  Used only to explain why a spec was kept verbatim.
    """
    before = _dsl_meaning(text)
    try:
        after = _dsl_meaning(rows_to_dsl_text(*dsl_text_to_rows(text)))
    except Exception:
        after = None
    if before is None or after is None:
        return ""
    lines = []
    for port in sorted(set(before[0]) | set(after[0])):
        was = before[0].get(port, "Open")
        now = after[0].get(port, "Open")
        if was != now:
            lines.append(f"  port {port + 1}: {was} → {now}")
    return "\n".join(lines)


def _scan_count(term: TerminationSet, nports: Optional[int]) -> int:
    """
    How many ports to scan when the file's port count is unknown.

    resolve_meas_ports and the overview only look at 0..n-1, so with no file
    loaded the window has to reach the largest port the spec mentions -- but
    that number is NOT reported as a port count anywhere, because it is not one.
    """
    if nports is not None:
        return int(nports)
    ports = set(term.per_port)
    for cpl in term.couplings:
        ports.add(cpl.port_i)
        ports.add(cpl.port_j)
    return (max(ports) + 1) if ports else 0


# Bucket order in the port-overview strip.  Re-exported from core, where the
# classifier now lives -- the strip and the Ports & Roles window must never be
# able to disagree about what a port is doing.
_OVERVIEW_BUCKETS = OVERVIEW_BUCKETS

# Abbreviated bucket names for the one-line footer summary, where the whole
# string has to fit a measured 303 px slot beside "Calculate This Trace".
_OVERVIEW_SHORT = {"probe": "probe", "ground": "gnd", "vdd": "vdd",
                   "element": "elem", "shorted": "short", "open": "open"}


def _bucket_counts(roles: Sequence[PortRole]) -> dict:
    counts = dict.fromkeys(_OVERVIEW_BUCKETS, 0)
    for r in roles:
        counts[r.bucket] += 1
    return counts


def _port_overview_text(term: Optional[TerminationSet],
                        nports: Optional[int], short: bool = False) -> str:
    """
    'Ports (45): 4 probe · 8 ground · 1 element · 32 open'.

    Counted off core's `port_roles`, which is also what the Ports & Roles
    window renders row by row -- ONE classifier, so the summary line and the
    detailed list cannot drift apart.

    With no file loaded the port count is unknown, so only the ports the rows
    mention are counted and the 'open' bucket is dropped entirely (port_roles
    does the dropping) -- an open port is one the file has and the spec did not
    name, which cannot be known without the file.  Guessing nports from the
    largest port mentioned would invent a number that looks authoritative.

    `short=True` abbreviates the bucket names for the footer summary, which has
    a measured 303 px to fit both this and a validation verdict.  Nothing is
    dropped -- the same buckets in the same order, just shorter words -- so the
    two renderings can never disagree about what the spec contains.
    """
    header = (f"Ports ({nports})" if nports is not None else
              ("Ports (no file)" if short else "Ports (no file selected)"))
    if term is None:
        return f"{header}: —"

    counts = _bucket_counts(port_roles(term, nports))
    parts = [f"{counts[b]} {_OVERVIEW_SHORT[b] if short else b}"
             for b in _OVERVIEW_BUCKETS if counts[b]]
    return f"{header}: " + (" · ".join(parts) if parts else "(no rows yet)")


def _rlc_echo(row: ConnectionRow) -> str:
    """
    'port 13 → GND: 5 mΩ + 500 pH + 1 uF' for one element row, or "".

    Design §2 wanted this per row; a static column costs ~140 px the 431 px
    editor does not have, so it lands in the validation strip instead.  It
    catches the same error: '5m' and '5M' are one shift key and nine orders of
    magnitude apart, and only the parsed value shows which one was typed.
    """
    if row.kind not in CONN_KINDS_WITH_RLC or not row.ports.strip():
        return ""
    vals = {k: getattr(row, k).strip() for k in ("R", "L", "C")}
    if any(any(ch.isspace() for ch in v) for v in vals.values()):
        # rows_to_dsl_text refuses these (see _rlc_tokens): the DSL is
        # whitespace-tokenised, so 'R=5 m' would compute 5 Ω while this
        # function -- which re-parses the raw cell as ONE token -- would echo
        # '5 mΩ' beside it.  Say nothing rather than say something else.
        return ""
    try:
        params = parse_kv_rlc_params(
            [f"{k}={v}" for k, v in vals.items() if v])
    except Exception:
        return ""
    bits = []
    if row.R.strip():
        bits.append(format_si(params["R"], "Ω"))
    if row.L.strip():
        bits.append(format_si(params["L"], "H"))
    if row.C.strip():
        bits.append(format_si(params["C"], "F"))
    if not bits:
        return ""
    to = row.to.strip() if row.kind == "rlc_between" else "GND"
    return f"port {row.ports.strip()} → {to or '?'}: " + " + ".join(bits)


# ---- how much a validation message COSTS the reader (R1-5) -----------------
#
# VALIDATION_STRIP_LINES is 2 and the footer summarises the rest as a count, so
# the first two messages ARE what gets read.  Emitting them in check order put
# "connection row 3 has values but no Port" -- which is visible on row 3, in
# the table, with an empty cell in it -- above "this element is shorted out",
# which is the one failure that produces a plausible number and says nothing.
#
# The ordering rule is therefore by CONSEQUENCE:
#
#   V_WRONG_NUMBER  Calculate succeeds and the number is not the one you asked
#                   for, and nothing else on screen says so.  A merged-node
#                   parallel stamp, an annihilated element, a probe a ground
#                   row silently outranks, two measurement-port rows that
#                   collapse into one, an open-port remnant.
#   V_NO_RESULT     Calculate raises, or every value comes back NaN.  Loud, so
#                   it ranks below a quiet wrong answer -- but above a row
#                   whose only symptom is on the row itself.
#   V_ROW_INERT     This row contributes nothing.  Its own cells show it.
#   V_OK            The '✓' echoes.  Never mixed with the above: the echoes
#                   are only reached when nothing else fired.
#
# Sorting is STABLE, so within a tier the emission order (which is row order)
# is preserved -- '5m' vs '5M' is a property of the row it is on and the rows
# must stay in the order they are on screen.
V_WRONG_NUMBER = 0
V_NO_RESULT = 1
V_ROW_INERT = 2
V_OK = 3


@dataclass(frozen=True)
class _VMsg:
    """One validation message, its consequence tier, and the row it is about.

    `anchor` is ("conn"|"mport", 0-based index into the NON-BLANK rows), which
    is what the footer strip routes to (R1-4).  None means the message is
    about the spec as a whole and there is no single row to scroll to."""
    tier: int
    text: str
    anchor: Optional[tuple] = None


def _validation_report(mport_rows: Sequence, conn_rows: Sequence,
                       extra_lines: str = "",
                       nports: Optional[int] = None,
                       port_names: Optional[Sequence[str]] = None,
                       scope_echoes: Sequence[tuple] = ()) -> list:
    """
    Everything worth saying about the two tables, worst CONSEQUENCE first.

    MUST NOT RAISE.  It runs from a Tk variable trace on every keystroke, where
    a raised exception does not reach a handler we control -- Tk prints it to
    stderr and the GUI carries on showing a stale, wrong strip.  Half-typed
    cells raise routinely: parse_port_range rejects '5:', '5:1:' and '-'.

    `port_names` (the file's "! Port[n] = ..." names) enables the open-port
    name check -- the one thing here that catches a spec which is internally
    consistent and still wrong.  Omit it and that check simply does not run.

    `scope_echoes` is `scope_echo_messages`' output: what each TAGGED port
    field resolved to, on a composition.  It is V_OK and it is appended, so a
    real problem always outranks it in the two-line strip -- but unlike the
    R/L/C echoes it survives ALONGSIDE a problem rather than being suppressed
    by one, because the question it answers ("which file is that port of")
    is at its most useful exactly when something else is wrong.  Calculate
    prints the whole list, so nothing is lost off the end of the strip.
    """
    msgs: list = []
    # Row indices as the STRIP numbers them: blanks are dropped by
    # RowTable.get_rows, so row i of this list is row i on screen.
    conn_live = [r for r in conn_rows if not r.is_blank()]
    mport_live = [r for r in mport_rows if not r.is_blank()]

    # Rows that are not blank but contribute nothing. rows_to_dsl_text skips a
    # connection row with an empty Port silently -- no error, no line, no hint
    # that the R=50 sitting next to it was thrown away.
    for i, row in enumerate(conn_live, start=1):
        anchor = ("conn", i - 1)
        # A row that is switched OFF is not in the spec at all, so none of the
        # checks below are about it: "row 3 has values but no Port" is a
        # complaint about a row that is already contributing nothing on
        # purpose.  It is still enumerated, so the row NUMBERS keep matching
        # the screen -- the footer route indexes by them.  That it is off is
        # said once, below, rather than once per row.
        if not getattr(row, "enabled", True):
            continue
        if not row.ports.strip():
            msgs.append(_VMsg(V_ROW_INERT,
                              f"⚠ connection row {i} has values but no Port "
                              "-- it does nothing.", anchor))
        elif (row.kind in CONN_KINDS_WITH_RLC
                and not (row.R.strip() or row.L.strip() or row.C.strip())):
            # The mirror image of the check above, and the one that hurts:
            # y_series_rlc(R=0, L=0, C=inf) is 1/0, so the element is an
            # infinite-admittance short and Z comes out NaN at EVERY frequency
            # -- with a warning that blames the measurement port's return path
            # rather than the empty cells.
            msgs.append(_VMsg(V_NO_RESULT,
                              f"⚠ connection row {i} ({row.kind}) has no R, L "
                              "or C -- a lumped element with no value is a "
                              "0 Ω short and the result is NaN everywhere.",
                              anchor))
    # V_NO_RESULT, not V_ROW_INERT.  A measurement-port row that resolves to
    # nothing is not "a row that does nothing": it is the measurement itself
    # going missing.  With one row that is a Calculate that RAISES ("no
    # measurement port defined"), and this message is its CAUSE -- the repo
    # already pins cause-above-consequence for the probe/ground overlap, and
    # the cause is the one you act on.  With several rows it is a coupling
    # curve that silently is not there.  Either way it outranks an element row
    # with an empty Port cell, which is R1-5's named example of the low tier.
    for i, row in enumerate(mport_live, start=1):
        if row.plus.strip():
            continue
        anchor = ("mport", i - 1)
        if row.minus.strip():
            msgs.append(_VMsg(V_NO_RESULT,
                              f"⚠ measurement port row {i} has a '−' side but "
                              "no '+' side -- it does nothing.", anchor))
        else:
            # Name typed, ports never filled in. is_blank() is False, so
            # neither branch used to see it and the row vanished silently.
            msgs.append(_VMsg(V_NO_RESULT,
                              f"⚠ measurement port row {i} has a name but no "
                              "ports -- it does nothing.", anchor))

    term: Optional[TerminationSet] = None
    try:
        term = build_terminations_rows(mport_rows, conn_rows, extra_lines,
                                       nports=nports)
    except Exception as e:
        # Deliberately UNANCHORED. The builder speaks in DSL line numbers, and
        # mapping one back to a table row means a second copy of
        # rows_to_dsl_text's emission order living here -- which would drift,
        # and whose drift shows up as the footer route landing on the wrong
        # row. The route falls back to the validation strip, where the whole
        # message is written out.
        msgs.append(_VMsg(V_NO_RESULT, f"⚠ {e}"))

    if term is not None:
        # Overlaps first: grounding a probe is what CAUSES 'no measurement
        # port defined', so naming the cause above the consequence.  Both are
        # tiered, so that order survives the sort.
        msgs.extend(_probe_ground_messages(mport_rows, term))
        msgs.extend(_measured_port_messages(mport_rows, term, nports))
        # An element the reduction annihilates (shorted out / both ends
        # grounded). Without this the strip showed the ✓ ECHO for it -- a green
        # tick reading '✓ port 5 → 6: 20 Ω' next to an answer that does not
        # depend on the 20 at all. The echoes below are only reached when msgs
        # is empty, so appending here is what suppresses that.
        msgs.extend(_VMsg(V_WRONG_NUMBER, m)
                    for m in inert_lumped_messages(term))
        # Its sibling, and the reason this whole tier exists: N identical
        # elements stamped across ONE merged node.  Measured by core on the
        # 5-port probe network -- '1,2,3 lumped_between 4 L=10f' after
        # '1 short_to 2,3' reads 3.333 fH where 10 fH was typed, with nothing
        # raised and nothing warned.  A number that is wrong by a factor of N
        # and looks entirely plausible outranks every "this row does nothing"
        # below it, which is exactly what V_WRONG_NUMBER buys.
        msgs.extend(_VMsg(V_WRONG_NUMBER, m)
                    for m in parallel_stamp_messages(term))
        # The one check that reads the FILE rather than the spec: ports whose
        # names say they belong to a set the user terminated, left open. Every
        # message above says "your spec is inconsistent"; this one says "your
        # spec is consistent and probably not what you meant", which is the
        # failure that survives review and costs three weeks.
        if port_names and nports is not None:
            try:
                msgs.extend(_VMsg(V_WRONG_NUMBER, m)
                            for m in open_port_name_messages(
                                port_roles(term, nports, port_names)))
            except Exception:       # pragma: no cover - see MUST NOT RAISE
                pass

    # A SWITCHED-OFF ROW IS SAID OUT LOUD, and it leads the V_OK block.
    #
    # The switch is for debugging, so the row that is off is meant to be off --
    # but a spec that is quietly missing a connection is precisely the shape of
    # wrong answer this strip exists to prevent, and the difference between "I
    # turned that off" and "I forgot I turned that off" is a fortnight.  One
    # line for the whole table rather than one per row (the strip renders two),
    # naming the rows so they can be found, and opening with a tick because it
    # is not a PROBLEM -- `_footer_strip_text` counts the ones that do not.
    off = [i for i, r in enumerate(conn_live, start=1)
           if not getattr(r, "enabled", True)]
    switched: list = []
    if off:
        which = ", ".join(str(i) for i in off[:6])
        more = f" +{len(off) - 6} more" if len(off) > 6 else ""
        switched.append(_VMsg(
            V_OK,
            f"✓ {len(off)} connection row{'s' if len(off) > 1 else ''} "
            f"switched OFF and not in the spec: {which}{more}",
            ("conn", off[0] - 1)))
    scoped = switched + [_VMsg(V_OK, text, anchor)
                         for text, anchor in scope_echoes]

    if msgs:
        # STABLE: row order is preserved inside a tier, and the scope echoes
        # come last because V_OK is the last tier -- so a problem is still what
        # the two-line strip shows, with the echoes behind it in the Results
        # pane where Calculate prints the whole list.
        return sorted(msgs + scoped, key=lambda m: m.tier)

    # One message per element row, not one line naming the first and counting
    # the rest: the echo exists to catch '5m' typed as '5M', which is a
    # property of the row it is on. _validation_strip_text caps the strip;
    # Calculate prints the full list to the Results pane.
    # A switched-off row gets NO echo: '✓ port 5 → GND: 50 Ω' about a row that
    # is not in the spec is a green tick for an element that is not there.  The
    # index is still conn_live's, so the anchor keeps pointing at the row the
    # reader can see.
    echoes = []
    for i, r in enumerate(conn_live):
        if not getattr(r, "enabled", True):
            continue
        e = _rlc_echo(r)
        if e:
            echoes.append(_VMsg(V_OK, "✓ " + e, ("conn", i)))
    # The scope echoes lead: on a composition "which file is this port of" is
    # the question that has to be settled before an R/L/C value means anything,
    # and the strip shows two lines.
    return scoped + echoes or [_VMsg(V_OK, "✓ no problems found")]


def _validation_messages(mport_rows: Sequence, conn_rows: Sequence,
                         extra_lines: str = "",
                         nports: Optional[int] = None,
                         port_names: Optional[Sequence[str]] = None,
                         scope_echoes: Sequence[tuple] = ()) -> list[str]:
    """The text of _validation_report, which is what the strips render."""
    return [m.text for m in _validation_report(mport_rows, conn_rows,
                                               extra_lines, nports, port_names,
                                               scope_echoes)]


def _measured_port_messages(mport_rows: Sequence, term: TerminationSet,
                            nports: Optional[int]) -> list:
    """
    Every way the measurement ports that will be MEASURED differ from the rows.

    Comparing the row count to len(resolve_meas_ports(...)) catches all of the
    merges at once without duplicating build_terminations_coupling's rule list:
    'A' + 'B' collapse (B is the legacy minus side of A) and two rows sharing a
    name do too.  Mode 6's identical-looking table RAISES on both; the Mode 5
    table keeps the DSL's permissive behaviour, and this strip is where that
    difference becomes visible instead of silent.

    It also catches the two directions the row count cannot show at all:
    NOTHING resolves (Calculate would raise), and MORE resolve than the table
    has rows -- which can only come from the lines kept as text, and is how a
    trace silently acquires a second probe and routes to the coupling path.

    Tiered (R1-5): "nothing resolves" is a Calculate that RAISES, while a
    silent collapse of two rows into one measurement port is a number that
    comes back and is 37% wrong -- so the collapse outranks it.
    """
    rows = [r for r in mport_rows if not r.is_blank() and r.plus.strip()]
    try:
        resolved = resolve_meas_ports(term, _scan_count(term, nports))
    except Exception as e:
        return [_VMsg(V_NO_RESULT, f"⚠ {e}")]
    if not resolved:
        return [_VMsg(V_NO_RESULT,
                      "⚠ no measurement port defined -- add a row to the "
                      "measurement-port table and fill in its '+' side.")]
    if len(resolved) > len(rows):
        hidden = [mp.name for mp in resolved
                  if mp.name not in {r.name.strip() for r in rows}]
        extra_n = len(resolved) - len(rows)
        named = f" ('{hidden[0]}')" if len(hidden) == 1 else ""
        head = ("1 measurement port is" if len(resolved) == 1
                else f"{len(resolved)} measurement ports are")
        return [_VMsg(V_WRONG_NUMBER,
                      f"⚠ {head} measured but the measurement-port table has "
                      f"{len(rows)} row(s): {extra_n} more{named} from the "
                      "lines kept as text. Open 'Edit as text…' to see them.")]
    if len(rows) < 2 or len(resolved) >= len(rows):
        return []
    head = (f"⚠ {len(rows)} measurement-port rows define only "
            f"{len(resolved)} measurement port(s)")
    names = [r.name.strip() for r in rows]
    upper = {n.upper() for n in names}
    if "A" in upper and "B" in upper:
        return [_VMsg(V_WRONG_NUMBER,
                      f"{head}: 'B' is the legacy minus side of 'A'. "
                      "Rename one of them.")]
    dupes = sorted({n for n in names if n and names.count(n) > 1})
    if dupes:
        return [_VMsg(V_WRONG_NUMBER,
                      f"{head}: the name '{dupes[0]}' is used twice, so both "
                      "rows feed one measurement port. Rename one.")]
    return [_VMsg(V_WRONG_NUMBER, f"{head}.")]


def _probe_ground_messages(mport_rows: Sequence,
                           term: TerminationSet) -> list:
    """
    Ports listed as a probe that a later connection row grounds.

    This is legal and pinned: the rows path emits probes before connections, so
    ground wins, exactly as build_terminations_mode1/2/3 always have.
    build_terminations_coupling raises on the same overlap.  Do not unify them
    -- just say which one happened.

    V_WRONG_NUMBER: nothing raises, nothing is NaN, and the port the user
    thinks they are probing is at 0 V.
    """
    probe_ports: set[int] = set()
    for row in mport_rows:
        for spec in (row.plus, row.minus):
            try:
                probe_ports.update(parse_port_range(spec))
            except Exception:
                continue
    hit = sorted(p for p in probe_ports
                 if isinstance(term.termination_of(p - 1), (Ground, Vdd)))
    if not hit:
        return []
    listed = ", ".join(str(p) for p in hit)
    noun = "port" if len(hit) == 1 else "ports"
    verb = "is" if len(hit) == 1 else "are"
    return [_VMsg(V_WRONG_NUMBER,
                  f"⚠ {noun} {listed} {verb} both a probe and a ground row "
                  "-- the ground row wins.")]


def _extra_lines_indicator(extra_lines: str) -> str:
    """
    '(+2 lines kept as text)' for the Connections caption, or "".

    extra_lines is the one part of the spec with no widget of its own, and
    rows_to_dsl_text emits it LAST -- so it wins over everything in the two
    tables.  After a verbatim-kept import the tables can be empty while a
    hidden block of DSL decides the whole answer; this is what says so without
    costing a row of the form.
    """
    n = len([ln for ln in (extra_lines or "").splitlines() if ln.strip()])
    if not n:
        return ""
    return f"(+{n} line{'' if n == 1 else 's'} kept as text)"


# ---- Ports & Roles: turning ANY trace into rows the classifier understands --
#
# Modes 1/2/3/6 do not have a connections table, but every one of them is
# expressible as one -- that is the whole premise of the Mode 5 DSL. Rendering
# them through the same rows means the window shows the same roles, the same
# "ground wins" precedence and the same source column in every mode, instead of
# five renderings that can disagree.  It is also DELIBERATELY the permissive
# path: build_terminations_coupling REFUSES a mode-6 probe that is also a ground
# row, and refusing is exactly the wrong answer for a window whose job is to
# show the user what they typed.  The overlap becomes a flagged row instead.

# Which editor FIELD a named mode's synthetic row stands for. Without this the
# window would tell a mode-1 user their port came from "probe row 1 (+)", a row
# that exists nowhere on their screen.
_NAMED_ROW_LABELS = {
    1: {"probe row 1 (+)": "Signal / Port A"},
    2: {"probe row 1 (+)": "Port A", "probe row 1 (−)": "Port B"},
    3: {"probe row 1 (+)": "Port A", "probe row 1 (−)": "Port B"},
}
_GND_FIELD_LABEL = "GND / VDD"
_SHORT_FIELD_LABEL = "Short Pairs"


def _trace_role_rows(tc) -> tuple:
    """
    Any TraceConfig -> (mport_rows, conn_rows, extra_lines, sources).

    `sources` is 1-based-port -> the row or field that last assigned it, with
    the named modes' synthetic rows renamed to the field the user typed into.
    Pure: no Tk, no file, no TerminationSet.
    """
    mode = getattr(tc, "mode", 1)
    overrides: dict = {}
    if mode == 5:
        mports = list(tc.mports)
        conn = list(tc.conn_rows)
        extra = tc.extra_lines or ""
    else:
        conn = []
        extra = ""
        if mode == 6:
            mports = list(tc.mports)
        else:
            plus = (tc.port_a or "").strip()
            minus = (tc.port_b or "").strip() if mode in (2, 3) else ""
            mports = ([MeasPortRow(name="A", plus=plus, minus=minus)]
                      if (plus or minus) else [])
            overrides.update(_NAMED_ROW_LABELS.get(mode, {}))
        if (tc.gnd_ports or "").strip():
            conn.append(ConnectionRow(kind="ground",
                                      ports=tc.gnd_ports.strip()))
            overrides[f"conn row {len(conn)}"] = _GND_FIELD_LABEL
        if mode == 3:
            try:
                pairs = parse_short_pairs(tc.short_pairs or "")
            except Exception:
                pairs = []
            for a, b in pairs:
                conn.append(ConnectionRow(kind="short", ports=str(a),
                                          to=str(b)))
                overrides[f"conn row {len(conn)}"] = _SHORT_FIELD_LABEL
    src = row_sources(mports, conn, extra)
    if overrides:
        src = {p: overrides.get(v, v) for p, v in src.items()}
    return mports, conn, extra, src


# The colour a flagged row takes in the Ports & Roles window. Same #b04000 as
# the frozen-trace note and the results pane's "flag" tag -- one warning colour
# in the application, not three.
WARN_FG = "#b04000"

WARN_OPEN_LOOKS_TERMINATED = "open, but its name matches a terminated set"
WARN_PROBE_AND_GROUND = "probe row AND ground row — the ground row wins"
# Mode 6 does NOT let ground win: build_terminations_coupling raises, because a
# probe side is tied together and grounding one of its ports grounds the whole
# side.  Both behaviours are pinned and intended (CLAUDE.md), so the WINDOW has
# to say which one it is showing.  Measured with the Mode-5 wording on a mode-6
# trace (probes on 1 and 2, GND field '1'): the window said "the ground row
# wins", which reads as "legal, and I know which side won", and Calculate then
# refused the trace outright -- "Port(s) 1 are listed both as a probe
# (measurement port 'c1') and as ground".  Mode 6 has neither a validation
# strip nor a footer strip, so this row is the ONLY thing on screen about the
# overlap and it must not state the other mode's rule.
WARN_PROBE_AND_GROUND_COUPLING = (
    "probe row AND ground row — Mode 6 refuses this; drop it from one list "
    "or the other")
WARN_FROM_KEPT_TEXT = "assigned by the kept-as-text block, not by a table row"


def _role_warnings(roles: Sequence[PortRole],
                   mport_rows: Sequence = (),
                   coupling: bool = False) -> dict:
    """
    1-based port -> why its row is flagged, for the rows that are.

    Three things earn a flag, and each is a way for a spec to look right and be
    wrong: an open port whose NAME belongs to a terminated set; a port a probe
    row claims that a ground row then takes (legal and invisible in Mode 5, a
    hard refusal in Mode 6 -- hence `coupling`); and a port assigned by the
    kept-as-text block, which is emitted last and so beats every table row
    while having no widget of its own.
    """
    warn: dict = {}
    for r in roles:
        if r.source.startswith("text line"):
            warn[r.index] = WARN_FROM_KEPT_TEXT

    probe_ports: set = set()
    for row in mport_rows:
        for spec in (getattr(row, "plus", ""), getattr(row, "minus", "")):
            try:
                probe_ports.update(parse_port_range(spec))
            except Exception:
                continue
    for r in roles:
        if r.index in probe_ports and r.role in (ROLE_GROUND, ROLE_VDD):
            warn[r.index] = (WARN_PROBE_AND_GROUND_COUPLING if coupling
                             else WARN_PROBE_AND_GROUND)

    for cluster in open_name_clusters(roles):
        for p in cluster.open_ports:
            warn[p] = WARN_OPEN_LOOKS_TERMINATED
    return warn


def _append_port_spec(existing: str, added: str) -> str:
    """
    '1,2' + '5-7' -> '1,2,5-7'.  APPENDS, never replaces.

    Replacing would silently throw away whatever the field already said, and
    the field is the one place that spec exists.  No space is introduced:
    parse_port_range tolerates one, the DSL's port field does not.
    """
    existing = (existing or "").strip().strip(",")
    if not existing:
        return added
    return f"{existing},{added}"


def _roles_header(file_label: str, nports: Optional[int],
                  roles: Sequence[PortRole]) -> str:
    """'coil.s4p — 153 ports · 4 probe · 54 ground · 94 open'."""
    if not file_label:
        return "(no file selected)"
    counts = _bucket_counts(roles)
    parts = [f"{counts[b]} {b}" for b in _OVERVIEW_BUCKETS if counts[b]]
    n = f"{nports} ports" if nports is not None else "? ports"
    return f"{file_label} — " + " · ".join([n, *parts])


# How many messages the strip shows before it defers to the Results pane.
# _on_calculate uses the same number to decide what to print there, so the
# "… +N more (see Results)" pointer names something that is actually written.
VALIDATION_STRIP_LINES = 2


def _validation_strip_text(msgs: Sequence[str],
                           limit: int = VALIDATION_STRIP_LINES) -> str:
    """
    Cap the strip. Measured uncapped: 21 / 38 / 55 / 89 / 140 px at 1 / 2 / 3 /
    5 / 8 lines, and 140 px is 41% of the editor canvas.  The overflow goes to
    the Results pane, which scrolls -- _on_calculate writes the full list there.
    """
    msgs = list(msgs)
    if len(msgs) <= limit:
        return "\n".join(msgs)
    return "\n".join(msgs[:limit]
                     + [f"… +{len(msgs) - limit} more (see Results)"])


# ---- the footer summary line ----------------------------------------------
#
# The two strips above (the port overview and the validation list) live at the
# BOTTOM of the scrollable editor form, and measured at the 1040x600 minsize a
# mode-5 form is 516 px against a 45 px viewport: the overview sits 366 px below
# the fold and the validation strip 387 px below it, i.e. 7.8% of the form is on
# screen and _update_mode_visibility resets the scroll to the top on every mode
# change.  In practice nobody ever saw either of them.
#
# They are SUMMARISED into the pinned footer, not moved into it.  Measured: the
# footer's height is the "Calculate This Trace" button's 33 px, and a label
# packed after the button shares that row -- so the FIRST line beside it is
# free (+0 px in every mode at every size), a second costs 9 px, a third 26 and
# a fourth 43.  At 43 px the editor canvas reports winfo_ismapped() == 0 in
# modes 1/2/3/6 at the minsize: the whole form disappears.  VALIDATION_STRIP_LINES
# is 2 and already renders up to 3 display lines, so moving both strips down
# here verbatim IS that failure, plus a one-line overview.  Hence: one line, and
# wraplength stays 0 so it clips rather than wraps (a wrapped second line costs
# 26 px, not 9).
#
# 52 chars is the measured slot: 303 px for a fill=X label beside the button,
# in Microsoft YaHei UI 9.  It is a budget, not a guarantee -- the label clips
# and the detail is one scroll away -- but it is what keeps the verdict visible.
FOOTER_STRIP_CHARS = 52


def _footer_strip_text(term: Optional[TerminationSet],
                       nports: Optional[int],
                       msgs: Sequence[str],
                       limit: int = FOOTER_STRIP_CHARS) -> str:
    """
    'Ports (153): 6 probe · 54 gnd · 3 elem  ⚠ 2 problems' -- always one line.

    The verdict is never truncated and the port counts give up characters
    first: a green tick has to mean "Calculate will work", and half a tick
    means nothing.  The count, not the messages themselves -- the messages are
    on the strip in the form and, in full, in the Results pane at Calculate;
    what the footer adds is that you cannot fail to notice there are any.
    """
    # _validation_messages NEVER returns an empty list: with nothing to warn
    # about it returns the '✓' echoes ('✓ port 5 → GND: 5 mΩ'), or
    # '✓ no problems found'.  So what is counted here is the messages that are
    # NOT affirmations -- len(msgs) would report a clean two-element spec as
    # "2 problems", which is precisely the false alarm a permanently visible
    # verdict must never raise.
    n = sum(1 for m in msgs if not m.startswith("✓"))
    status = ("✓ ok" if n == 0
              else f"⚠ {n} problem{'' if n == 1 else 's'}")
    if n == 0 and term is None:
        # Unreachable through _apply_editor_strips -- _validation_messages
        # appends the builder's own error, so a spec that does not build always
        # arrives with at least one message.  It is here so that a tick beside
        # a 'Ports (n): —' overview is a claim this function CANNOT make.
        status = "⚠ spec did not parse"
    ports = _port_overview_text(term, nports, short=True)
    budget = limit - len(status) - 2
    if budget < 1:
        return status
    if len(ports) > budget:
        ports = ports[:budget - 1] + "…"
    return f"{ports}  {status}"


# ============================================================================
# Session files (Save Config / Load Config / autosave)
# ============================================================================
#
# A session file holds the CONFIG, never the results.  A .sNp file is megabytes
# and a computed Z is one array per trace per frequency; the point of this file
# is that it is a few kB of readable JSON that can go in git, be mailed to a
# colleague, or ride along to the red zone next to the data it describes.  What
# comes back is the setup -- press Calculate and the numbers return.  Export CSV
# remains the results path, so the two never overlap and never disagree.
#
# The functions below are deliberately free of Tk: `session_to_dict` takes the
# lists and `session_from_dict` returns them, so the whole round trip is
# testable without a display.

SESSION_FORMAT = "pkg_rlc_extractor_session"
# Bumped to 2 by the multi-file schema (`TraceConfig.file_labels`).
#
# WHY UNCONDITIONALLY, when only a COMPOSED trace carries anything new.  A
# version-1 reader drops an unknown key with a note and carries on -- so it
# would load a composed trace as its home file alone and then compute a
# well-formed number, of the right order, from a network with the package
# missing.  That is precisely the silent wrong answer this feature exists to
# end, and it is worth refusing a whole file over; the refusal already exists
# and already names both numbers (`version > SESSION_VERSION` below).
#
# Writing 1 for uncomposed sessions and 2 only for composed ones was
# implemented and reverted: it keeps an uncomposed file loadable by an older
# build, but it cannot satisfy both halves of what the suite already pins --
# test_session.py asserts that a saved file's version IS this constant AND that
# `SESSION_VERSION + 1` is refused, which together force the written default
# and the read cap to be the same number.
#
# What does NOT change is the part that matters: a trace with no extra file
# still serialises BYTE FOR BYTE as before (no 'file_labels' key at all -- see
# _OPTIONAL_TRACE_FIELDS), so nothing about an existing spec moves.
SESSION_VERSION = 2

SESSION_FILETYPES = [("RLC session", "*.json"), ("All files", "*.*")]

# Where the on-exit autosave lives.  Under the user's home rather than beside
# the install, because the install may well be read-only (the red zone copies
# a tarball into place) and losing the autosave is not worth an error dialog.
AUTOSAVE_DIRNAME = ".pkg_rlc_extractor"
AUTOSAVE_FILENAME = "last_session.json"

# Filled in by Calculate and NOT saved.  This set is the blacklist and the
# config fields are everything else, so a new *config* field is saved without
# anyone remembering to add it -- the failure mode of the other arrangement is
# a field that silently stops round-tripping, which nothing would catch.  A new
# *computed* field forgotten here fails loudly instead (json.dump on a numpy
# array), and tests/test_session.py::TestFieldCoverage pins that every field of
# TraceConfig is classified one way or the other.
_COMPUTED_TRACE_FIELDS = frozenset({
    "stale", "Z", "rlc", "fit_kind", "fit", "fit_freqs", "fit_Z",
    "Zmat", "mport_names", "coupling",
    # Composed traces.  `net_freqs` is a numpy array and `reference_checks` is
    # a list of dataclasses holding floats -- neither is a spec, both are
    # products of a Calculate, and a session file that carried them would be
    # claiming a composition had been solved when the files behind it may not
    # even be on this machine.
    "net_freqs", "reference_checks",
})

# Retired-but-still-loading fields (mode 4's VDD list, the free-text Mode 5
# spec, the two hard-coded Mode 6 measurement ports).  They are written only
# when non-empty: a trace the user has never selected still carries them
# unmigrated, so dropping them would lose a spec, but emitting eight empty
# strings on every trace of every file would bury the fields that matter.
_LEGACY_TRACE_FIELDS = frozenset({
    "vdd_ports", "custom_text",
    "mp1_name", "mp1_plus", "mp1_minus",
    "mp2_name", "mp2_plus", "mp2_minus", "mp_more",
})

# Forward-looking fields written only when non-empty.  Same MECHANISM as
# _LEGACY_TRACE_FIELDS and the opposite reason: those are retired and this one
# is new.  What it buys is that a single-file trace serialises to BYTE-IDENTICAL
# JSON -- no 'file_labels': [] on every trace of every file anyone has ever
# saved.  tests/test_multifile_session.py pins the exact bytes.
_OPTIONAL_TRACE_FIELDS = frozenset({"file_labels"})

_TRACE_ROW_CLASSES = {"mports": MeasPortRow, "conn_rows": ConnectionRow}
# Plain list-of-string fields.  Without an entry here, trace_from_dict's default
# branch would `str()` the whole list and store its REPR as the value -- a field
# that round-trips into garbage instead of failing.  The coercion also
# NORMALISES: entries are stripped and blanks dropped, so `trace_file_labels`
# (mirrored in pkg_rlc_files_gui, and it must stay mirrored) never has to decide
# what a padded label means.
_TRACE_STRLIST_FIELDS = frozenset({"file_labels"})
_TRACE_INT_FIELDS = frozenset({"id", "mode", "color_idx", "ls_idx"})
_TRACE_BOOL_FIELDS = frozenset({"plot_self", "plot_mutual", "enabled",
                                "frozen"})


# Global controls, and the values the two readonly comboboxes will accept.  A
# combobox is state="readonly", so a value from outside its list would sit
# there unselectable with no way back except retyping it into the file.
_CONTROL_KEYS = ("rlc_freq_ghz", "fit_fmin_ghz", "fit_fmax_ghz",
                 "fit_model", "units_mode", "results_view")
_CONTROL_CHOICES = {
    "fit_model": ("none", "auto", "inductor", "capacitor"),
    "units_mode": ("smart", "aligned"),
    # Which of the three renderings the Results pane is showing.  Saved for the
    # same reason the units mode is: it is what the reader had set up, it costs
    # one string, and a session that came back in a layout the user had already
    # moved away from would be a silent change to what they are reading.  A
    # value outside this list is dropped with a note, like every other control.
    "results_view": RESULTS_VIEWS,
}


class SessionError(ValueError):
    """
    A session file this build will not read.

    `str(e)` IS the whole verdict, same contract as TouchstoneParseError: the
    first question a failed load has to answer is "is my file wrong or is your
    tool wrong", and a JSON traceback answers neither.
    """


@dataclass
class LoadedSession:
    """What `session_from_dict` recovered.  `files` is (label, path, found)."""
    files: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    controls: dict = field(default_factory=dict)
    plot: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    # What the Attribution windows were reading, carried OPAQUELY: this file
    # neither builds nor inspects the block, it hands whatever
    # `attribution_session_state` produced back to
    # `apply_attribution_session_state`, which owns its shape and its version
    # number.  A second reader here is how the two come to disagree about what
    # a key means.
    attribution: dict = field(default_factory=dict)


def _config_trace_fields() -> list[str]:
    """The TraceConfig fields a session file carries, in declaration order."""
    return [f.name for f in fields(TraceConfig)
            if f.name not in _COMPUTED_TRACE_FIELDS]


def autosave_path() -> Path:
    return Path.home() / AUTOSAVE_DIRNAME / AUTOSAVE_FILENAME


def trace_to_dict(tc: "TraceConfig") -> dict:
    out: dict = {}
    for name in _config_trace_fields():
        value = getattr(tc, name)
        if name in _TRACE_ROW_CLASSES:
            value = [asdict(r) for r in value]
        # `mports` and `conn_rows` are in neither skip set, so an empty table
        # is still written -- exactly as before.  Only the retired fields and
        # the forward-looking ones disappear when empty.
        if not value and (name in _LEGACY_TRACE_FIELDS
                          or name in _OPTIONAL_TRACE_FIELDS):
            continue
        out[name] = value
    return out


def _coerce_bool(value) -> bool:
    """
    JSON true/false, or the spellings a hand-edit produces.

    Plain `bool()` is wrong here: `bool("false")` is True, so a file edited by
    hand into `"enabled": "false"` would silently mean the opposite of what it
    says.  An unrecognised string raises, and the caller keeps the default with
    a note.
    """
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0", ""):
            return False
        raise ValueError(value)
    return bool(value)


def _rows_from_list(cls, value, key: str, warn) -> list:
    """
    A JSON array of row objects -> rows of `cls`.

    Every field is `str()`-ed EXCEPT the boolean ones, and that exception is
    load-bearing rather than tidy: `str(False)` is `"False"`, a non-empty
    string and therefore TRUTHY, so a `ConnectionRow` saved with
    `enabled=False` would come back switched ON and the spec would silently
    grow a connection the user had switched off.  Exactly the `_coerce_bool`
    rule one layer down, and the same reason -- see its docstring.  There is no
    checkbox to notice it on here, either: the cell's glyph is derived from the
    value, so it would look right and only the number would move.

    Boolean fields are found from the DEFAULT's type, not from `f.type`:
    `pkg_rlc_core` has `from __future__ import annotations`, so `f.type` is the
    STRING "bool" there and an `is bool` test silently matches nothing.
    """
    if not isinstance(value, list):
        warn(f"'{key}' is not a list; ignored")
        return []
    names = {f.name for f in fields(cls)}
    bool_names = {f.name for f in fields(cls) if isinstance(f.default, bool)}
    rows = []
    for item in value:
        if not isinstance(item, dict):
            warn(f"a '{key}' row is not an object; dropped")
            continue
        kw = {}
        for k, v in item.items():
            if k not in names:
                warn(f"'{key}' field '{k}' is not known to this build; ignored")
                continue
            if k in bool_names:
                try:
                    kw[k] = _coerce_bool(v)
                except ValueError:
                    warn(f"'{key}' field '{k}' is not a true/false value "
                         f"({v!r}); the default was kept")
                continue
            kw[k] = "" if v is None else str(v)
        rows.append(cls(**kw))
    return rows


def _strings_from_list(value, key: str, warn) -> list[str]:
    """
    A JSON array of file labels, defensively.

    Same contract as `_rows_from_list`: a bad value costs its own entry and a
    note, never the file.  A nested object or list is refused rather than
    `str()`-ed, because its repr would then be a "file label" no file can ever
    match and the trace would report it missing forever.
    """
    if not isinstance(value, list):
        warn(f"'{key}' is not a list; ignored")
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, (dict, list)):
            warn(f"a '{key}' entry is not a name; dropped")
            continue
        text = ("" if item is None else str(item)).strip()
        if text:
            out.append(text)
    return out


def trace_from_dict(data, warn) -> "TraceConfig":
    """
    One trace, rebuilt defensively.

    A session file is user-editable text, so every value is coerced to the type
    the field is declared with and a value that will not coerce keeps the
    default with a note.  Refusing the whole file over one bad `color_idx`
    would throw away a port map that took ten minutes to type.
    """
    if not isinstance(data, dict):
        raise SessionError("a 'traces' entry is not a JSON object")
    known = set(_config_trace_fields())
    tc = TraceConfig()
    for key, value in data.items():
        if key not in known:
            warn(f"trace field '{key}' is not known to this build; ignored")
            continue
        cls = _TRACE_ROW_CLASSES.get(key)
        try:
            if cls is not None:
                coerced = _rows_from_list(cls, value, key, warn)
            elif key in _TRACE_STRLIST_FIELDS:
                coerced = _strings_from_list(value, key, warn)
            elif key in _TRACE_INT_FIELDS:
                coerced = int(value)
            elif key in _TRACE_BOOL_FIELDS:
                coerced = _coerce_bool(value)
            else:
                coerced = "" if value is None else str(value)
        except (TypeError, ValueError):
            warn(f"trace field '{key}': {value!r} is not usable; "
                 f"kept the default")
            continue
        setattr(tc, key, coerced)
    return tc


def _file_ref(fe: "FileEntry", base_dir: Optional[str]) -> dict:
    """
    One file, addressed BOTH ways.

    The relative path is what makes a session survive the whole folder being
    copied to another machine -- which is the normal way work reaches the red
    zone -- and the absolute one is what makes a session file that has been
    moved on its own still find the data.  Loading tries relative first.
    """
    ap = Path(fe.ts.source_path).resolve()
    ref = {"label": fe.label, "path": ap.as_posix()}
    if base_dir:
        try:
            rel = Path(os.path.relpath(ap, base_dir)).as_posix()
        except ValueError:
            return ref      # different drive on Windows: absolute is all there is
        # A relative path is worth writing when there is a tree that could be
        # copied as a unit -- 'data/coil.s4p', or '../data/coil.s4p' from a
        # configs/ subfolder.  A config saved somewhere unrelated to the data
        # produces a ten-deep '../../..' chain that is longer than the absolute
        # path and describes no such tree; it would still resolve on this
        # machine and nowhere else, so it is only noise in the file.
        if len(rel) < len(ref["path"]):
            ref["rel_path"] = rel
    return ref


def resolve_session_file(ref: dict, base_dir: str) -> tuple[str, bool]:
    """(path, found).  Relative first -- see _file_ref."""
    candidates: list[str] = []
    rel = ref.get("rel_path")
    if base_dir and isinstance(rel, str) and rel:
        candidates.append(os.path.normpath(os.path.join(base_dir, rel)))
    absolute = ref.get("path")
    if isinstance(absolute, str) and absolute:
        candidates.append(os.path.normpath(absolute))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand, True
    return (candidates[0] if candidates else ""), False


def session_to_dict(files: Sequence, traces: Sequence, controls: dict,
                    plot_state: dict, base_dir: Optional[str] = None,
                    saved_utc: Optional[str] = None,
                    attribution: Optional[dict] = None) -> dict:
    """
    The whole session as a JSON-ready dict.

    `base_dir` is the directory the file is about to be written into, and is
    None for the autosave -- that one never moves, so a path relative to it
    would say nothing an absolute path does not.

    `attribution` is what the open Attribution windows were reading, and it is
    a SESSION-level key rather than a TraceConfig field on purpose.  It is
    list-valued, which is the documented `mports` Duplicate-aliasing trap; it
    would need handling in `_duplicate_trace_config` AND `_freeze_trace_config`
    (a snapshot's copy of it would describe a window the snapshot cannot
    reopen); and it must never reach `_config_signature`, because choosing a
    different victim to read does not make the drawn curve older than the spec.
    It is written only when there is something in it, the same rule as
    `_LEGACY_TRACE_FIELDS`: an empty dict on every session file is noise that
    buries the ones that carry state.
    """
    out = {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "saved_utc": saved_utc or datetime.now(timezone.utc)
                                          .strftime("%Y-%m-%d %H:%M:%S UTC"),
        "files": [_file_ref(fe, base_dir) for fe in files],
        "traces": [trace_to_dict(tc) for tc in traces],
        "controls": dict(controls),
        "plot": dict(plot_state),
    }
    if attribution:
        out["attribution"] = attribution
    return out


def session_from_dict(data, base_dir: str = "") -> LoadedSession:
    if not isinstance(data, dict):
        raise SessionError(
            "This is not a session file: its top level is not a JSON object.")
    fmt = data.get("format")
    if fmt != SESSION_FORMAT:
        said = f" (its 'format' says {fmt!r})" if fmt else " (it has no 'format' key)"
        raise SessionError(
            f"This is not a PKG RLC Extractor session file{said}.\n\n"
            "Session files are the ones written by File → Save Config.")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise SessionError("This session file has no usable 'version' number.")
    if version > SESSION_VERSION:
        raise SessionError(
            f"This session file is version {version}; this build reads up to "
            f"version {SESSION_VERSION}.\n\nUpdate the tool, or re-save the "
            f"session from the version that wrote it.")

    sess = LoadedSession()
    warn = sess.warnings.append

    raw_files = data.get("files") or []
    if not isinstance(raw_files, list):
        raise SessionError("This session file's 'files' is not a list.")
    for ref in raw_files:
        if not isinstance(ref, dict):
            warn("a 'files' entry is not an object; dropped")
            continue
        path, found = resolve_session_file(ref, base_dir)
        label = ref.get("label") or os.path.basename(path)
        sess.files.append((str(label), path, found))

    raw_traces = data.get("traces") or []
    if not isinstance(raw_traces, list):
        raise SessionError("This session file's 'traces' is not a list.")
    for entry in raw_traces:
        sess.traces.append(trace_from_dict(entry, warn))

    controls = data.get("controls")
    if isinstance(controls, dict):
        for key in _CONTROL_KEYS:
            value = controls.get(key)
            if value is None:
                continue
            value = str(value)
            choices = _CONTROL_CHOICES.get(key)
            if choices is not None and value not in choices:
                warn(f"'{key}' = {value!r} is not one of {', '.join(choices)}; "
                     f"kept the current setting")
                continue
            sess.controls[key] = value

    plot = data.get("plot")
    if isinstance(plot, dict):
        sess.plot = plot

    # A bad value costs its own field and never the file: a session file is
    # readable text and will be hand-edited, and losing a port map that took
    # ten minutes to type over a mangled window record is the wrong trade.
    # Anything deeper inside the block is `apply_attribution_session_state`'s
    # to validate -- it owns the shape and reports its own notes -- so all that
    # is checked here is that there IS an object to hand it.
    attribution = data.get("attribution")
    if attribution is not None:
        if isinstance(attribution, dict):
            sess.attribution = attribution
        else:
            warn("'attribution' is not an object; ignored")
    return sess


# ============================================================================
# Mode 6 helpers (+/- measurement ports, coupling)
# ============================================================================

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


# Wall-clock stamp appended to a frozen trace's label.  Minutes, not seconds:
# the label is read in a legend truncated to MAX_LABEL_LEN characters, and two
# snapshots taken inside the same minute are told apart by their id anyway.
FREEZE_STAMP_FMT = "%H:%M"


def freeze_label(src_label: str, stamp: str,
                 limit: int = MAX_LABEL_LEN) -> str:
    """
    '<label> <HH:MM>', with the BASE trimmed so the stamp survives truncation.

    The plot legend truncates to the FIRST `MAX_LABEL_LEN` characters
    (pkg_rlc_plot: `label = (tr.label or "")[:MAX_LABEL_LEN]`), and the tool's
    own default label is `f"{fe.label}_p1_to_gnd"` -- so any file name of 20
    characters or more already overflows.  Appending the stamp to the end put
    the ONE thing that tells a snapshot from its source exactly where
    head-truncation deletes it: measured, source
    'coupled_2port_gndref.s2p_p1_to_gnd' and snapshot
    'coupled_2port_gndref.s2p_p1_to_gnd <21:29>' both legend as
    'coupled_2port_gndref.s2p_p1_to' -- byte-identical entries for the two
    curves the whole feature exists to put side by side.

    Same rule, and the same '…' elision, as _compose_curve_label: trim the base,
    keep the discriminator.
    """
    suffix = f" <{stamp}>"
    base = (src_label or "").strip()
    room = limit - len(suffix)
    if len(base) > room and room >= 2:
        base = base[:room - 1] + "…"
    return f"{base}{suffix}"


def _freeze_stamp_of(label: str) -> str:
    """The '<HH:MM>' freeze_label appended, or '(unknown)'.

    The label is the only place a snapshot records WHEN it was taken -- the
    numbers are referenced, not re-dated -- and the CSV header needs it to say
    which run the block is not from.  A user-renamed snapshot loses it, which
    is why this degrades instead of raising.
    """
    m = re.search(r"<(\d{1,2}:\d{2})>\s*$", label or "")
    return m.group(1) if m else "(unknown)"


def freeze_refusal(tc: "TraceConfig") -> tuple:
    """
    (title, message) explaining why this trace must not be frozen, or ().

    Two refusals, and the second is the one that matters.  A snapshot's whole
    contract is "this spec produced these numbers", and a frozen trace can
    never clear `stale` again -- _on_calculate skips it and
    _sync_editor_to_trace refuses it -- so a stale trace frozen once is
    MISLABELLED FOREVER, with nothing on screen saying so.  Measured on
    coupled_2port_gndref.s2p (port 1 = 0.6 ohm / 2 nH, port 2 = 0.9 ohm / 3 nH):
    Calculate with Port A = 1, type '2' into Port A, freeze without
    recalculating, and the results table reads
    '[ 2] coil <21:36>  M1: S:[2] G:[]  600 mOhm  2 nH ...' -- port 2's
    descriptor over port 1's numbers, a 50% error on L, in the table, the run
    page, the CSV and the legend.  Nothing raises and the numbers are real,
    which is the worst kind of wrong.

    Refusing is the only answer that keeps the contract true.  Carrying `stale`
    onto the snapshot instead was considered and rejected: the flag means "the
    drawn curve is older than the spec", and on a trace that can never be
    recomputed that is a permanent complaint with no action behind it.
    """
    if tc.frozen:
        return ()
    if tc.Z is None and tc.Zmat is None:
        return ("Nothing to freeze",
                "This trace has no numbers yet.\n\nCalculate it first — "
                "freezing keeps the RESULT, so there has to be one.")
    if tc.stale:
        return ("Spec has changed",
                "This trace has been edited since it was last calculated, so "
                "its numbers and the spec beside them no longer describe each "
                "other.\n\nCalculate it first — freezing keeps the numbers, "
                "and a frozen trace can never be recalculated to catch them "
                "up.")
    return ()


def _freeze_trace_config(src: "TraceConfig", new_id: int,
                         stamp: Optional[str] = None) -> "TraceConfig":
    """
    A read-only SNAPSHOT of a computed trace: same spec, same numbers, never
    recomputed and never edited again.

    Two copying rules, and they are opposites on purpose:

      * CONFIG is copied, and the two list-valued fields (`mports`,
        `conn_rows`) element-wise.  `TraceConfig(**src.__dict__)` is a shallow
        splat and would hand both traces the same row list -- the documented
        Duplicate aliasing bug, where editing one silently edits the other.
      * RESULTS are REFERENCED, not deep-copied.  `_on_calculate` ASSIGNS new
        objects to Z / Zmat / rlc / fit / fit_freqs / fit_Z on every run
        instead of writing into the existing arrays, so nothing can change the
        snapshot's numbers under it.  A deepcopy would carry megabytes for no
        benefit (a 6x6 Zmat over 5000 frequencies is 2.88 MB).

    Colour AND linestyle both move on.  A snapshot drawn in its source's exact
    colour and dash is indistinguishable from it, which defeats the one picture
    this whole feature exists to produce -- and the linestyle carries the
    distinction even when the palette wraps back onto a colour already in use.
    """
    stamp = stamp or datetime.now().strftime(FREEZE_STAMP_FMT)
    return TraceConfig(**{**src.__dict__,
                          "id": new_id,
                          "label": freeze_label(src.label, stamp),
                          "mports": [replace(r) for r in src.mports],
                          "conn_rows": [replace(r) for r in src.conn_rows],
                          "file_labels": list(src.file_labels),
                          "frozen": True,
                          # Its numbers were computed from exactly this spec,
                          # and nothing can edit either of them again.
                          "stale": False,
                          "color_idx": (src.color_idx + 1) % len(COLORS),
                          "ls_idx": (src.ls_idx + 1) % len(LINESTYLES)})


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


def _collect_mports(tc: "TraceConfig",
                    rows: Optional[Sequence] = None
                    ) -> list[tuple[str, list[int], list[int]]]:
    """
    Measurement-port table -> the (name, plus_1based, minus_1based) triples
    that build_terminations_coupling expects.  Ports stay 1-based here; the
    core builder is the 1-based/0-based boundary.

    `rows` overrides the trace's own table and is how a COMPOSED trace gets
    here: `_build_termination` hands in the same rows with every probe side
    already resolved into the composed namespace.  It defaults to the trace's
    table, so every single-file call site is unchanged.
    """
    tc.migrate_legacy_mports()
    if rows is None:
        rows = tc.mports
    out: list[tuple[str, list[int], list[int]]] = []
    for idx, row in enumerate(rows, start=1):
        plus = row.plus.strip()
        minus = row.minus.strip()
        if not plus:
            if minus:
                label = f"'{row.name.strip()}'" if row.name.strip() else f"row {idx}"
                raise ValueError(
                    f"Measurement port {label} has a '-' side but no '+' side; "
                    "the red probe must touch at least one port.")
            continue
        out.append((row.name.strip(),
                    parse_port_range(plus), parse_port_range(minus)))

    if not out:
        raise ValueError(
            "No measurement ports defined: add a row to the measurement-port "
            "table and fill in its '+' side.")
    return out


def _coupling_k_array(Zmat: np.ndarray, freqs: np.ndarray,
                      a: int, b: int) -> np.ndarray:
    """
    Coupling coefficient k(f) = M / sqrt(L_a * L_b) for the pair (a, b).

    Signed and never clipped, exactly like extract_coupling_at_freq: NaN only
    where k is genuinely undefined (a port that is not inductive at that
    frequency).  omega cancels out of the ratio but is kept explicit so the
    formula matches the core one line for line.
    """
    omega = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        La = Zmat[:, a, a].imag / omega
        Lb = Zmat[:, b, b].imag / omega
        M = Zmat[:, a, b].imag / omega
        k = M / np.sqrt(La * Lb)
        k = np.where((La > 0.0) & (Lb > 0.0), k, np.nan)
    return k


def _compose_curve_label(trace_label: str, suffix: str,
                         limit: int = MAX_LABEL_LEN) -> str:
    """
    '<trace label> : <suffix>' clipped to `limit` chars (the plot-legend
    invariant).  The trace label is trimmed first so the measurement-port /
    pair name -- the part that distinguishes the expanded curves -- survives.
    """
    sep = " : "
    trace_label = (trace_label or "").strip()
    suffix = (suffix or "").strip()
    full = f"{trace_label}{sep}{suffix}"
    if len(full) <= limit:
        return full
    keep = limit - len(sep) - len(suffix)
    if keep >= 2:
        return f"{trace_label[:keep - 1]}…{sep}{suffix}"
    return full[:limit]


# The plot module grows a Trace.aux dict for per-curve extras (the k array on a
# mutual curve).  Detect it rather than assume it, so this file works whether
# or not that change has landed yet.
_PLOT_TRACE_FIELDS = set(getattr(PlotTrace, "__dataclass_fields__", {}) or {})
PLOT_TRACE_SUPPORTS_AUX = "aux" in _PLOT_TRACE_FIELDS


def _make_plot_trace(aux: Optional[dict] = None, **kwargs) -> PlotTrace:
    """Build a PlotTrace, attaching `aux` only if pkg_rlc_plot supports it."""
    if aux and PLOT_TRACE_SUPPORTS_AUX:
        kwargs["aux"] = aux
    return PlotTrace(**kwargs)


#: The marker a composed curve wears in the plot legend, and the reason it is
#: this short.  R3-4 asks that the composed run say WHICH FILES produced it
#: everywhere it is read, and the legend is one of those places -- but the
#: legend budget is `MAX_LABEL_LEN = 30` characters HEAD-truncated, and the
#: tool's own default label (`f"{fe.label}_p1_to_gnd"`) already overflows it
#: for any file name of 20 characters.  A file legend there ('F1=die.s6p +
#: F2=package.s4p', 30 characters on its own) would delete the trace name it is
#: qualifying.  So the legend carries the COUNT, the same ` +N` the Traces list
#: carries, and the names live in the results table's file column, the coupling
#: block's `files:` line, the CSV header and the files window.
#:
#: `freeze_label`'s rule and the reason for it, exactly: trim the BASE, keep
#: the discriminator, because head-truncation deletes the tail.
COMPOSED_LABEL_SUFFIX = " +{n}"


def _plot_trace_label(tc: "TraceConfig", limit: int = MAX_LABEL_LEN) -> str:
    """The trace's legend entry: its label, plus ' +N' when it is composed."""
    base = (tc.label or "").strip()
    if not trace_is_composed(tc):
        return base
    suffix = COMPOSED_LABEL_SUFFIX.format(n=len(trace_file_labels(tc)) - 1)
    room = limit - len(suffix)
    if len(base) > room and room >= 2:
        base = base[:room - 1] + "…"
    return f"{base}{suffix}"


def _trace_plot_freqs(tc: "TraceConfig", fe: "FileEntry"):
    """
    The frequency axis this trace's cached Z is on, or None if it has none.

    None only in one case, and it is a real one: a composed trace whose numbers
    were computed on an axis that is not stored anywhere else, restored or left
    behind by a path that did not set `net_freqs`.  Falling back to the home
    file's sweep there would draw the right values at the wrong frequencies --
    a plausible curve, shifted, with nothing on screen to say so -- which is
    the exact failure the composed axis exists to avoid.
    """
    if tc.net_freqs is not None:
        return tc.net_freqs
    if trace_is_composed(tc) and (tc.Z is not None or tc.Zmat is not None):
        return None
    return fe.ts.freqs


def _write_coupling_csv(fh, writer, tc: "TraceConfig", freqs) -> None:
    """
    Mode-6 CSV block: Re/Im of every Z_ij, then M_nH and k for every unordered
    pair, one row per frequency.  Every value keeps its physical sign; nothing
    is clipped to NaN except where it is genuinely undefined.

    `freqs` is the axis the matrix was COMPUTED on, handed in rather than
    fetched from the home file: a composed Zmat lives on the composed axis, and
    the home file's sweep is neither the same length nor the same points.
    """
    Zmat = tc.Zmat
    names = list(tc.mport_names or [])
    G = int(Zmat.shape[1])
    pairs = [(a, b) for a in range(G) for b in range(a + 1, G)]

    fh.write("# Measurement ports: " + ", ".join(names) + "\n")
    fh.write("# Off-diagonal Z is open-circuit mutual impedance "
             "(every other measurement port open).\n")
    header = ["Freq_GHz"]
    for i in range(G):
        for j in range(G):
            header.append(f"Re_Z_{names[i]}_{names[j]}")
            header.append(f"Im_Z_{names[i]}_{names[j]}")
    for a, b in pairs:
        header.append(f"M_nH_{names[a]}_{names[b]}")
        header.append(f"k_{names[a]}_{names[b]}")
    writer.writerow(header)

    omega = 2.0 * np.pi * freqs
    k_arrays = {ab: _coupling_k_array(Zmat, freqs, *ab) for ab in pairs}
    for idx in range(len(freqs)):
        row = [f"{freqs[idx] / 1e9:.6g}"]
        for i in range(G):
            for j in range(G):
                z = Zmat[idx, i, j]
                row.append(f"{z.real:.6e}")
                row.append(f"{z.imag:.6e}")
        for ab in pairs:
            a, b = ab
            M_nH = (Zmat[idx, a, b].imag / omega[idx] * 1e9
                    if omega[idx] != 0.0 else float("nan"))
            row.append(f"{M_nH:.6e}")
            row.append(f"{float(k_arrays[ab][idx]):.6e}")
        writer.writerow(row)


# ------------------------------------------------- what changed between runs
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


def _snapshot_reference(tc: "TraceConfig") -> dict:
    """The reference-node verdict as three frozen fields, or three empty ones.

    Rendered HERE, at snapshot time, for the same reason `port_desc` is a
    resolved string: `tc.reference_checks` is replaced wholesale by the next
    Calculate, so a record holding the list would answer about a composition
    that has since been rebuilt.
    """
    strip, lines = reference_provenance(getattr(tc, "reference_checks", None)
                                        or [])
    if not strip:
        return {"ref_strip": "", "ref_warn": False, "ref_lines": ()}
    return {"ref_strip": strip[0], "ref_warn": bool(strip[1]),
            "ref_lines": tuple(lines)}


def _snapshot_row(tc: "TraceConfig", file_label: str, res) -> RowSnapshot:
    return RowSnapshot(id=tc.id, label=tc.label,
                       port_desc=tc.port_descriptor(),
                       enabled=bool(tc.enabled), color_idx=int(tc.color_idx),
                       file_label=file_label, res=res,
                       files=_snapshot_files(tc),
                       **_snapshot_reference(tc))


def _snapshot_block(tc: "TraceConfig", file_label: str,
                    cres, freq: Optional[FreqSnap] = None) -> CouplingSnapshot:
    return CouplingSnapshot(id=tc.id, label=tc.label,
                            port_desc=tc.port_descriptor(),
                            enabled=bool(tc.enabled),
                            color_idx=int(tc.color_idx),
                            file_label=file_label, cres=cres, freq=freq,
                            files=_snapshot_files(tc),
                            **_snapshot_reference(tc))


def _snapshot_fit(tc: "TraceConfig", text: str) -> FitSnapshot:
    return FitSnapshot(id=tc.id, enabled=bool(tc.enabled), text=text)


@dataclass
class RunTab:
    """One page of the Results notebook: a run record and the widgets showing it.

    TRACKED BY WIDGET, NEVER BY INDEX.  Measured: evicting a lower index
    renumbers every tab after it but keeps the same widget selected and
    preserves its scroll position exactly -- so a list of records keyed on the
    frame survives eviction, while any stored index silently starts pointing at
    the neighbour.
    """
    run: RunSnapshot
    frame: object                       # ttk.Frame, the notebook's child
    text: object                        # the ScrolledText inside it
    kept: bool = False
    unseen: bool = False                # arrived while the reader was elsewhere


# ============================================================================
# The one results-pane renderer that is NOT a formatter
# ============================================================================
#
# Everything else that turns a run into text moved to pkg_rlc_report, which is
# pure and testable with no display.  This one WRITES INTO A Tk TEXT WIDGET, so
# it belongs on this side of that seam -- and it reaches COLORS, which is
# pkg_rlc_plot's.

def _tag_swatch_rows(txt, base_line: int, text: str,
                     color_idxs: Sequence[int]) -> None:
    """
    Colour the leading swatch of each data row of `text`, already inserted
    starting at line `base_line` of the Text widget `txt`.

    Rows are found by their RESULTS_SWATCH prefix and consumed in order, so no
    line-number arithmetic has to be kept in step with however many header
    lines _format_results_table decides to emit (the file-alias line alone is
    already conditional on the trace count).  Shared by the Log and by every
    run page, so a row cannot be one colour on one and another on the other.
    """
    pending = iter(color_idxs)
    for off, line in enumerate(text.split("\n")):
        # EVERY occurrence, not only a leading one.  A results table puts one
        # swatch at the head of each row; the compare view puts one in each
        # COLUMN HEADING, because there a column is a trace and the heading is
        # the only cell that names it.  Consuming them left to right, top to
        # bottom is the same contract either way, and no other line this
        # module emits carries the character at all.
        col = line.find(RESULTS_SWATCH)
        while col >= 0:
            idx = next(pending, None)
            if idx is None:
                return          # more swatches than colours: leave them plain
            ln = base_line + off
            txt.tag_add(f"c{idx % len(COLORS)}",
                        f"{ln}.{col}", f"{ln}.{col + len(RESULTS_SWATCH)}")
            col = line.find(RESULTS_SWATCH, col + len(RESULTS_SWATCH))


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

# The two Traces-list context-menu entries, and the note that explains why the
# editor is greyed out.  Named constants because three tests and one menu
# lookup key off them, and a menu entry nobody can find is the same as no
# feature at all.
FREEZE_MENU_LABEL = "Freeze as new trace"
UNFREEZE_MENU_LABEL = "Unfreeze"
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


# ============================================================================
# Ports & Roles window
# ============================================================================
#
# A read-only ttk.Treeview, and the ban on Treeview elsewhere in this file does
# NOT apply here.  That ban is about the EDITABLE connection table, which needs
# cell editors -- Treeview has none, so it would mean floating Entry/Combobox
# widgets over cells with hand-managed placement, tab order and scroll offset,
# and those overlays misalign under Win11 DPI scaling.  Nothing here is edited
# in place: this is a list you read, filter, sort and select from.
#
# Two Treeview hazards are handled below and both fail SILENTLY if they are not:
#   * row height is frozen at 20 px whatever `tk scaling` and whatever font the
#     style carries, so at 150% DPI the text clips.  It is set from the font's
#     own metrics, on a DERIVED style name -- reconfiguring the global
#     "Treeview" style would reach every other Treeview in the process;
#   * tag foreground/background colours are ignored on Tk builds whose
#     Style().map("Treeview", ...) contains negated ('!'-prefixed) state
#     specs.  The standard fix is to drop those entries; it is applied
#     unconditionally, because the symptom of not applying it is simply that
#     the colours do not appear.

PORT_ROLES_TITLE = "Ports & Roles"
PORT_ROLES_STYLE = "PortRoles.Treeview"

# (key, heading, width px, anchor).  Widths are a starting point -- the window
# is resizable and every column is stretchable.
PORT_ROLES_COLUMNS = (
    ("index", "#", 44, "e"),
    ("name", "Name", 210, "w"),
    ("role", "Role", 90, "w"),
    ("source", "From", 170, "w"),
)

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

PORT_ROLES_HINT = (
    "Select rows, then send them to the editor. Ports are written back as a "
    "collapsed range (1-3,7), so a 54-ball ground group stays one row."
)


def _fixed_map_filter(entries: Sequence) -> list:
    """
    Drop the ('!disabled', '!selected') state specs from a ttk style map.

    This is the standard workaround for the Tk bug that makes a Treeview ignore
    tag colours: those two negated states match every ordinary row, so the
    style map wins over the tag and every row is painted the default colour.
    Pure, so the rule itself is testable without a display.
    """
    return [e for e in entries if tuple(e[:2]) != ("!disabled", "!selected")]


class PortRolesWindow(tk.Toplevel):
    """
    What every port of the file is actually doing, for the selected trace.

    Modeless (no grab_set): the point is to read it WHILE editing, and a
    grab_set Toplevel that outlives its opener blocks event delivery and hangs
    update() -- the same failure documented for the style picker.
    """

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title(PORT_ROLES_TITLE)
        self.transient(app)
        self.geometry("640x460")
        self.minsize(430, 260)

        self._roles: list = []
        self._warn: dict = {}
        self._sort_key = "index"
        self._sort_rev = False

        self.header = ttk.Label(self, anchor="w", justify=tk.LEFT)
        self.header.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 2))

        # --- filter row ---
        filt = ttk.Frame(self)
        filt.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))
        # No Listbox anywhere in this window -- the list is a Treeview, whose
        # selection is not the X selection, so the documented
        # exportselection=False rule has nothing to apply to. Clicking this
        # Entry therefore cannot clear the Traces listbox highlight that
        # auto-apply resolves its target from (that listbox already sets
        # exportselection=False, which is what makes it safe).
        ttk.Label(filt, text="Filter name:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filt, textvariable=self.filter_var,
                                      width=18)
        self.filter_entry.pack(side=tk.LEFT, padx=(4, 10))
        self.filter_var.trace_add("write", lambda *_a: self._repopulate())
        self.hide_open_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text="hide open", variable=self.hide_open_var,
                        command=self._repopulate).pack(side=tk.LEFT)
        self.count_lbl = ttk.Label(filt, foreground=PLACEHOLDER_FG)
        self.count_lbl.pack(side=tk.RIGHT)

        # --- write-back and the detail line, PACKED BEFORE THE LIST ---
        # pack allocates in call order and simply UNMAPS whatever no longer
        # fits, starting from the end, so a fixed-height section packed after an
        # expand=True sibling disappears outright once the window is dragged
        # short. Same rule as Global Controls and the editor footer: claim the
        # bottom first, and the buttons become unconditional.
        foot = ttk.Frame(self)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(foot, text="Close", command=self.destroy
                   ).pack(side=tk.RIGHT, padx=2)
        self.probe_btn = ttk.Button(foot, text="Set as probe +",
                                    command=lambda: self._send("probe+"))
        self.probe_btn.pack(side=tk.RIGHT, padx=2)
        self.gnd_btn = ttk.Button(foot, text="Set as ground",
                                  command=lambda: self._send("ground"))
        self.gnd_btn.pack(side=tk.RIGHT, padx=2)
        # LAST in the row, so at a narrow width it is the hint that goes and
        # never one of the three buttons.
        ttk.Label(foot, text=PORT_ROLES_HINT, foreground=PLACEHOLDER_FG,
                  wraplength=330, justify=tk.LEFT).pack(side=tk.LEFT)

        self.detail = ttk.Label(self, anchor="w", justify=tk.LEFT,
                                wraplength=600, foreground=WARN_FG)
        self.detail.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(2, 0))

        # --- the list ---
        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)
        self._install_style()
        vsb = ttk.Scrollbar(body, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(
            body, style=PORT_ROLES_STYLE, selectmode="extended",
            columns=[c[0] for c in PORT_ROLES_COLUMNS], show="headings",
            yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.configure(command=self.tree.yview)
        for key, title, width, anchor in PORT_ROLES_COLUMNS:
            self.tree.heading(key, text=title,
                              command=lambda k=key: self._on_sort(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=True)
        for role, colour in PORT_ROLE_FG.items():
            self.tree.tag_configure(f"role_{role}", foreground=colour)
        self.tree.tag_configure("warn", foreground=WARN_FG)
        # NOT registered with the App's wheel router.  "Treeview" is in
        # App._WHEEL_OWNERS, so _route_wheel bails out over it and lets Tk's own
        # ttk::treeview class binding scroll it; registering a handler here
        # would never be reached, and taking Treeview OUT of _WHEEL_OWNERS to
        # reach it would break every other Treeview in the process.

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.bind("<Escape>", lambda _e: self.destroy())

    # ------------------------------------------------------------- style

    @staticmethod
    def _install_style() -> None:
        style = ttk.Style()
        # A DERIVED name: dotted ttk style names inherit their parent's layout,
        # so this is a full Treeview whose rowheight (and only its rowheight)
        # differs.  Reconfiguring "Treeview" itself would follow every Treeview
        # ttk ever builds in this interpreter.
        font = tkfont.nametofont("TkDefaultFont")
        style.configure(PORT_ROLES_STYLE,
                        rowheight=font.metrics("linespace") + 4)
        style.map(PORT_ROLES_STYLE,
                  foreground=_fixed_map_filter(
                      style.map("Treeview", query_opt="foreground")),
                  background=_fixed_map_filter(
                      style.map("Treeview", query_opt="background")))

    # -------------------------------------------------------------- data

    def refresh(self, header: str, roles: Sequence, warn: dict) -> None:
        """Re-render from a fresh snapshot, keeping filter, sort and selection."""
        self._roles = list(roles)
        self._warn = dict(warn)
        self.header.configure(text=header)
        self._repopulate()

    _SORT_KEYS = {
        "index": lambda r: r.index,
        "name": lambda r: (r.name == "", r.name.lower()),
        "role": lambda r: (r.role, r.index),
        "source": lambda r: (r.source == "", r.source.lower(), r.index),
    }

    def _on_sort(self, key: str) -> None:
        if key == self._sort_key:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_key, self._sort_rev = key, False
        self._repopulate()

    def visible_roles(self) -> list:
        """The records the list is showing, in the order it is showing them."""
        needle = self.filter_var.get().strip().lower()
        hide_open = bool(self.hide_open_var.get())
        rows = [r for r in self._roles
                if (not needle or needle in r.name.lower())
                and not (hide_open and r.role == ROLE_OPEN)]
        # Sorted on the RAW record, never on the rendered string: '#' is an int
        # and a string sort puts port 10 between 1 and 2.
        rows.sort(key=self._SORT_KEYS[self._sort_key], reverse=self._sort_rev)
        return rows

    def _repopulate(self) -> None:
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        rows = self.visible_roles()
        for r in rows:
            tags = [f"role_{r.role}"]
            if r.index in self._warn:
                tags.append("warn")     # last tag wins for foreground
            self.tree.insert("", "end", iid=str(r.index),
                             values=(r.index, r.name or "(unnamed)",
                                     r.role, r.source or "—"),
                             tags=tuple(tags))
        back = [i for i in keep if self.tree.exists(i)]
        if back:
            self.tree.selection_set(back)
        self.count_lbl.configure(
            text=(f"{len(rows)} of {len(self._roles)}"
                  if len(rows) != len(self._roles) else f"{len(rows)} ports"))
        self._on_select()

    # ------------------------------------------------------------ actions

    def selected_ports(self) -> list:
        return sorted(int(i) for i in self.tree.selection())

    def _on_select(self, _event=None) -> None:
        ports = self.selected_ports()
        notes = [f"port {p}: {self._warn[p]}" for p in ports if p in self._warn]
        self.detail.configure(text=notes[0] if notes else "")
        state = ["!disabled"] if ports else ["disabled"]
        self.gnd_btn.state(state)
        self.probe_btn.state(state)

    def _send(self, role: str) -> None:
        ports = self.selected_ports()
        if not ports:
            return
        msg = self.app.apply_ports_as(role, ports)
        if msg:
            self.detail.configure(text=msg)


# ============================================================================
# Main GUI
# ============================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PKG RLC Extractor")
        self.geometry("1500x900")

        self.files: list[FileEntry] = []
        self.traces: list[TraceConfig] = []
        self._next_trace_id = 1
        self._suppress_editor_sync = False
        # Shadow of the selected trace's extra_lines: whatever the connections
        # table cannot represent (comments, hand-written directives, a spec
        # parked verbatim by the migration). The editor has no widget for it --
        # it is edited through "Edit as text…" -- but it has to survive a load
        # / apply cycle, so it lives here alongside the widgets.
        self._ed_extra_lines: str = ""
        self._ed_strips_pending = False
        # The Ports & Roles window, while one is open. Modeless and at most
        # one: a second copy of the same read-only list is only a second thing
        # to keep in sync.
        self._port_roles_win: Optional[PortRolesWindow] = None
        # Auto-apply state.  The target is the TraceConfig OBJECT, never a
        # Listbox index -- see _schedule_editor_sync.
        self._ed_sync_after: object = None
        self._ed_sync_target: Optional[TraceConfig] = None
        # (rendered line, colour index) per trace -- see _refresh_trace_list.
        self._trace_list_shown: list[tuple[str, int]] = []
        self._scrollables: dict[str, object] = {}
        # Results-pane notebook state.  Set BEFORE _build_ui: adding the first
        # tab fires <<NotebookTabChanged>> straight away, and the handler reads
        # both of these.
        #
        # _log_unseen counts warnings written while the Log tab was NOT on
        # screen -- a tab that is not selected has winfo_ismapped() == 0 while
        # insert/get/see all keep working, so without the badge a parser
        # warning, a rank-deficiency note or a traceback would be written and
        # never seen, with no test failing.
        self._log_unseen = 0
        # _log_forced: an ERROR line has pulled the Log tab to the front, and
        # an automatic switch to some other tab must not undo it.  See
        # _select_results_tab.
        self._log_forced = False

        # Run history.  _run_counter is monotonic and is what identifies a run;
        # _last_run is the record the Results pane is currently showing.  A run
        # record is IMMUTABLE -- see RunSnapshot -- so re-rendering it can
        # never print one run's numbers under another run's labels.
        self._run_counter = 0
        self._last_run: Optional[RunSnapshot] = None
        # The run history tabs, NEWEST FIRST, tracked by widget.  Two disjoint
        # sets live in this one list and `kept` is the discriminator: the auto
        # ring (kept=False) is all Calculate ever touches, and the kept set is
        # entered only by the Keep button.  See the comment on RUN_AUTO_DEFAULT.
        self._run_tabs: list[RunTab] = []
        self._run_auto_max = RUN_AUTO_DEFAULT
        self._run_tabs_max = RUN_TABS_DEFAULT
        self._run_auto_var = tk.IntVar(self, value=RUN_AUTO_DEFAULT)
        self._run_tabs_var = tk.IntVar(self, value=RUN_TABS_DEFAULT)

        # Composed networks, keyed by the tuple of file labels and validated by
        # FileEntry IDENTITY on every lookup (a label is re-used when a file is
        # reloaded, and the arrays behind it are then different objects).
        #
        # A cache, not an optimisation of a fast thing.  Stacking is S -> Y per
        # file plus an interpolation, and `_freq_batch` collapses exactly where
        # this feature is first needed -- 16 ports gives 64 frequencies per
        # chunk, 76 gives 2, 153 gives 1 -- so the edit/recompute loop is where
        # the cost is.  Measured in pkg_rlc_compose on a 16-port EM + 300-port
        # package at 101 points: 3565 ms to stack and pre-reduce once against
        # 2.6 ms per later solve.  Nothing here pre-reduces (the GUI has no
        # keep list yet), but the stacking half is the same and is what this
        # avoids paying per Calculate.
        self._compose_cache: dict = {}

        self._install_wheel_router()
        self._build_ui()
        self._build_menubar()
        self._bind_events()
        self._clamp_to_screen()
        # Closing the window is the moment the session would otherwise be lost,
        # so it is the moment it is written.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._announce_last_session()

    # ------------------------------------------------------- wheel routing

    # Widget classes that scroll (or otherwise act on) the wheel themselves.
    # The router must not double-handle for these.
    #
    # TCombobox is deliberately NOT here.  Its value-changing class binding is
    # removed in _install_wheel_router below, so it no longer owns the wheel --
    # and half the connections table (Type / Port / To) is a combobox, which
    # made those three columns a dead zone the table could not be scrolled from.
    # An OPEN dropdown is a Listbox, which is still in this set and still
    # scrolls itself.
    _WHEEL_OWNERS = frozenset({"Text", "Listbox", "Treeview",
                               "TSpinbox", "Spinbox", "TScrollbar", "Scrollbar"})

    def _install_wheel_router(self) -> None:
        """
        One pointer-based wheel router for the whole window.

        Replaces the per-widget <Enter>/<Leave> + bind_all/unbind_all dance,
        which does not compose: unbind_all deletes EVERY binding on the `all`
        tag, so a second scrollable region silently disables the first, and a
        table that cannot scroll swallowed the event instead of letting the
        form behind it scroll.  Here each registered handler returns False when
        its content already fits, and the event bubbles to the next scrollable
        ancestor -- innermost wins, with fall-through.

        Matplotlib is unaffected: its wheel binding is on the figure canvas
        widget itself, which fires before the `all` tag, and the router finds
        nothing registered above it. The plot panel's <Enter> -> focus_set()
        for the M / V / Delete keys is untouched -- this keys off the POINTER,
        not focus.
        """
        self.bind_all("<MouseWheel>", self._route_wheel, add="+")
        # A ttk Combobox CHANGES ITS VALUE on the wheel (class binding
        # 'ttk::combobox::Scroll'). On a form where one of those comboboxes
        # selects the Touchstone file, an accidental scroll silently rebinds
        # the trace to a different file and every number changes with no
        # warning. Nothing here wants that behaviour.
        self.unbind_class("TCombobox", "<MouseWheel>")

    def _register_scrollable(self, widget, handler) -> None:
        """handler(event) -> True if it consumed the wheel, False to bubble."""
        self._scrollables[str(widget)] = handler

    def _route_wheel(self, event):
        w = self.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            try:
                if w.winfo_class() in self._WHEEL_OWNERS:
                    return None          # it handles itself; don't double-scroll
                handler = self._scrollables.get(str(w))
                if handler is not None and handler(event):
                    return "break"
                w = w.master
            except Exception:
                return None
        return None

    def _clamp_to_screen(self) -> None:
        """
        Never open taller than the desktop.

        The hardcoded 1500x900 is fine on a large monitor and is born partly
        off-screen on a 1920x1080 laptop at 150% scaling (1280x680 logical),
        which puts the bottom of the window -- Calculate, Apply -- beyond the
        desktop before layout is even involved.
        """
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1500, sw - 80)}x{min(900, sh - 140)}+40+20")
        self.minsize(1040, 600)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # Outer horizontal split
        outer = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(outer, width=460)
        outer.add(left, weight=0)

        right = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        outer.add(right, weight=1)

        self._build_left_panel(left)
        self._build_right_panel(right)

    def _build_menubar(self) -> None:
        """
        Save / Load live on a menu bar, not on a button.

        The left panel has no spare pixels: the Files and Traces rows are both
        four buttons deep against a measured 448 px, and a fifth row inside
        Global Controls comes straight out of the editor viewport, which at the
        1040x600 minsize is already down to tens of pixels.  A menu bar costs
        the left panel nothing, and File → Save/Open is where anyone looks
        first anyway.
        """
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Save Config…", accelerator="Ctrl+S",
                              command=self._on_save_config)
        file_menu.add_command(label="Load Config…", accelerator="Ctrl+O",
                              command=self._on_load_config)
        file_menu.add_separator()
        file_menu.add_command(label="Restore Last Session",
                              command=self._on_restore_last_session)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # Analyze is the SECOND cascade, and it is where a window that is not
        # part of the measurement loop belongs.  The two rows that could
        # otherwise have carried it are full: measured at the 1040x600 minsize
        # the Files and Traces rows are 448 px with four buttons already asking
        # 364, and a fifth row inside Global Controls comes straight out of an
        # editor viewport that is down to 45 px there.  A menu bar costs the
        # left panel nothing.
        #
        # NO ACCELERATOR, deliberately.  `bind_all` reaches every Toplevel --
        # measured on this very menubar, Ctrl+S typed into a Toplevel Entry
        # fires _on_save_config -- so an accelerator here would also fire from
        # inside the Attribution window itself, opening a second window for
        # whatever trace the main window happens to have selected.  The entry
        # is reachable two ways already (here and the Traces right-click), and
        # neither is a keystroke anyone would want to hit by accident.
        analyze_menu = tk.Menu(menubar, tearoff=False)
        analyze_menu.add_command(label=ATTRIB_MENU_LABEL,
                                 command=self._on_attribution)
        # R3-3.  Which files a trace is made of is a per-trace fact, so it
        # belongs on the same menu as the other per-trace window and on the
        # same right-click menus.  It is NOT a fifth button on the Files or
        # Traces rows: both are measured at 448 px at the 1040x600 minsize with
        # four buttons already asking 364, and a fifth row inside Global
        # Controls comes straight out of an editor viewport that is down to
        # 45 px there.  No accelerator, for the reason above this cascade.
        analyze_menu.add_command(label=FILES_MENU_LABEL,
                                 command=self._on_files_window)
        menubar.add_cascade(label="Analyze", menu=analyze_menu)

        self.config(menu=menubar)
        self._file_menu = file_menu
        self._analyze_menu = analyze_menu

        self.bind_all("<Control-s>", lambda _e: self._on_save_config())
        self.bind_all("<Control-o>", lambda _e: self._on_load_config())
        # Tk's Text class binds <Control-o> to "insert a newline without moving
        # the cursor", and a bind_all handler runs AFTER the class binding, so
        # with the caret in the Results pane Ctrl+O would open the dialog and
        # scribble in the pane behind it.  Same removal, for the same reason, as
        # the TCombobox wheel binding in _install_wheel_router.  Nothing in this
        # application is a document; open-line has no use here.
        self.unbind_class("Text", "<Control-o>")

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        parent.pack_propagate(False)

        # --- Files section ---
        files_frame = ttk.LabelFrame(parent, text="Loaded Files")
        files_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        btn_row = ttk.Frame(files_frame)
        btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(btn_row, text="Add File...", command=self._on_add_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Remove", command=self._on_remove_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Show Ports", command=self._on_show_ports
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(btn_row, text="Check File", command=self._on_check_file
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.files_lb = tk.Listbox(files_frame, height=5, exportselection=False,
                                   activestyle="dotbox")
        self.files_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        # --- Traces section ---
        traces_frame = ttk.LabelFrame(parent, text="Traces")
        traces_frame.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        tr_btn_row = ttk.Frame(traces_frame)
        tr_btn_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(tr_btn_row, text="Add Trace", command=self._on_add_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(tr_btn_row, text="Remove", command=self._on_remove_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        ttk.Button(tr_btn_row, text="Duplicate", command=self._on_duplicate_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        # FOURTH button in this row, and pack unmaps from the end, so this was
        # measured before it was added: at the 1040x600 minsize the row is
        # 448 px and four buttons ask 364 (three ask 273).  Re-measure before a
        # fifth.  It duplicates the editor's "Plot: this trace" checkbox on
        # purpose -- the checkbox needs the trace selected first, and the
        # keyboard route (space) is invisible.
        ttk.Button(tr_btn_row, text="Show/Hide", command=self._on_toggle_trace
                   ).pack(side=tk.LEFT, padx=2, pady=2)
        self.traces_lb = tk.Listbox(traces_frame, height=8, exportselection=False,
                                    activestyle="dotbox")
        self.traces_lb.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)

        # --- Global controls ---
        # PACKED BEFORE THE EDITOR, AND side=BOTTOM.  pack allocates in call
        # order and simply UNMAPS whatever no longer fits, starting from the
        # end -- so a fixed-size section packed after an expand=True sibling
        # vanishes entirely once the sibling outgrows the panel.  Measured: in
        # mode 6 at 1500x900 this whole frame came back winfo_ismapped() == 0,
        # i.e. Calculate / Export CSV / Help were not on screen at all. Claiming
        # the bottom first is what makes them unconditional.
        gc = ttk.LabelFrame(parent, text="Global Controls")
        gc.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=2)
        self._build_global_controls(gc)

        # --- Editor section ---
        ed = ttk.LabelFrame(parent, text="Edit Selected Trace")
        ed.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._build_editor(ed)

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
                   command=self._on_calculate_selected
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
        self._register_scrollable(self._ed_canvas, self._ed_wheel)

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
        if getattr(self, "_ed_scroll_pending", False):
            # Coalescing a reset with a preserve: the reset wins, because the
            # reason for it (the form is a different shape now) has not gone
            # away.  Only within one pending batch -- the flag is re-armed
            # below, or a stale False would swallow every later row add.
            self._ed_scroll_preserve = self._ed_scroll_preserve and preserve
            return
        self._ed_scroll_preserve = preserve
        self._ed_scroll_pending = True
        self.after_idle(self._apply_editor_scrollregion)

    def _apply_editor_scrollregion(self) -> None:
        self._ed_scroll_pending = False
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
        if self._ed_scroll_preserve:
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
        self.ed_mp_table.register_wheel(self._register_scrollable)
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
        self.ed_conn_table.register_wheel(self._register_scrollable)
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

    def _build_global_controls(self, parent: ttk.LabelFrame) -> None:
        # RLC freq + band-fit on the same compact form
        ttk.Label(parent, text="RLC Freq (GHz):").grid(row=0, column=0, sticky="e",
                                                      padx=2, pady=1)
        self.rlc_freq_var = tk.StringVar(value="0.1")
        ttk.Entry(parent, textvariable=self.rlc_freq_var, width=10
                  ).grid(row=0, column=1, sticky="w", padx=2, pady=1)

        ttk.Label(parent, text="Fit f_min/f_max (GHz):").grid(row=1, column=0,
                                                              sticky="e", padx=2, pady=1)
        self.fit_fmin_var = tk.StringVar(value="0.1")
        self.fit_fmax_var = tk.StringVar(value="5.0")
        ttk.Entry(parent, textvariable=self.fit_fmin_var, width=8
                  ).grid(row=1, column=1, sticky="w", padx=2, pady=1)
        ttk.Entry(parent, textvariable=self.fit_fmax_var, width=8
                  ).grid(row=1, column=2, sticky="w", padx=2, pady=1)

        ttk.Label(parent, text="Fit Model:").grid(row=2, column=0, sticky="e",
                                                  padx=2, pady=1)
        self.fit_model_var = tk.StringVar(value="none")
        ttk.Combobox(parent, textvariable=self.fit_model_var,
                     values=["none", "auto", "inductor", "capacitor"],
                     state="readonly", width=10
                     ).grid(row=2, column=1, sticky="w", padx=2, pady=1)

        ttk.Button(parent, text="Calculate All & Plot",
                   command=self._on_calculate
                   ).grid(row=3, column=0, columnspan=2, pady=4, sticky="we", padx=2)
        ttk.Button(parent, text="Export CSV", command=self._on_export_csv
                   ).grid(row=3, column=2, pady=4, sticky="we", padx=2)
        ttk.Button(parent, text="Help", command=self._on_help
                   ).grid(row=3, column=3, pady=4, sticky="we", padx=2)

        parent.columnconfigure(1, weight=1)

    def _build_right_panel(self, parent: ttk.PanedWindow) -> None:
        # POPULATE BEFORE add().  A ttk.PanedWindow sizes a new pane from its
        # requested size AT THE MOMENT IT IS ADDED, and never recomputes. Adding
        # an empty frame and filling it afterwards therefore works only by luck:
        # it depends on no geometry pass running in between, so any widget built
        # earlier that calls update_idletasks() silently pins the sash at ~2px
        # and the whole Results pane disappears. Build the children first and the
        # sash position stops depending on timing.
        results_frame = ttk.Frame(parent, height=180)
        plot_frame = ttk.Frame(parent)

        # A ReflowRow, not a pack(side=LEFT) run, and that is a measurement.
        # With five controls the strip already asked for 667 px against the
        # 575 it gets at the 1040x600 minsize at 150% font scaling, and pack
        # UNMAPS FROM THE END -- so the Keep button, whose label is the only
        # place the kept cap is stated at the moment it bites, was the one
        # being squeezed.  A 'View:' label plus a readonly combobox is a
        # further 127 px at 100% and 240 px at 150% (measured), which would
        # have taken it off screen outright with no scrollbar and no other
        # route to it: exactly the defect tests/test_plot_controls.py exists to
        # stop recurring.  ReflowRow wraps instead, keeps its own requested
        # width at 1 px so the strip can never force the pane wider, and reads
        # an imposed width while writing only a height -- a fixed point, not
        # the _apply_editor_scrollbars limit cycle.  At 100% the whole strip is
        # 477 px of 575 and stays one row, so nothing about the default window
        # moves.
        header = ReflowRow(results_frame)
        header.pack(side=tk.TOP, fill=tk.X)
        header.add(ttk.Label(header, text="Results", anchor="w"))
        # WHICH RENDERING, not which numbers.  Kept beside Units: because it is
        # the same kind of choice -- both repaint every run page in place and
        # neither creates a run.
        header.add(ttk.Label(header, text="View:"), padx=(6))
        self.results_view_var = tk.StringVar(value=VIEW_DETAIL)
        view_combo = ttk.Combobox(
            header, textvariable=self.results_view_var,
            values=list(RESULTS_VIEWS), state="readonly", width=8,
        )
        header.add(view_combo)
        view_combo.bind("<<ComboboxSelected>>",
                        lambda _e: self._on_results_view_changed())
        header.add(ttk.Label(header, text="Units:"), padx=(6))
        self.units_mode_var = tk.StringVar(value="smart")
        units_combo = ttk.Combobox(
            header, textvariable=self.units_mode_var,
            values=["smart", "aligned"], state="readonly", width=8,
        )
        header.add(units_combo)
        units_combo.bind("<<ComboboxSelected>>",
                         lambda _e: self._on_units_mode_changed())
        # Tk 8.6's ttk.Notebook has no tab-strip scrolling and no overflow
        # chevron, and past ~12 tabs a label is three characters wide.  The
        # Runs menu is where a compressed tab stays identifiable: it carries
        # the FULL description of every run, and it is also where the two caps
        # are set.  Rebuilt on post, because the list changes on every run.
        self._runs_menubutton = ttk.Menubutton(header, text="Runs ▾")
        self._runs_menu = tk.Menu(self._runs_menubutton, tearoff=0)
        self._runs_menubutton["menu"] = self._runs_menu
        self._runs_menu.configure(postcommand=self._rebuild_runs_menu)
        header.add(self._runs_menubutton, padx=(6))
        # Keep is a BUTTON, not a menu entry, because its label is the only
        # place the kept cap can be stated at the moment it bites.
        self._keep_btn = ttk.Button(header, text=keep_button_label(0, 1, "none"),
                                    command=self._on_keep_run)
        header.add(self._keep_btn)
        #: The strip has to be re-laid when the Keep button's TEXT grows --
        #: `_reflow` runs from `add()` and from the strip's own <Configure>,
        #: and neither fires for that.  See ReflowRow.refresh().
        self._results_header = header
        # The Results pane is a ttk.Notebook and tab 0 is the Log, holding the
        # same ScrolledText it has always held.
        #
        # results_text stays a REAL, PERSISTENT widget attribute -- never a
        # property resolving to whichever tab is active.  Six tests take an
        # index(END) mark, run Calculate and read back from that mark; a fresh
        # widget returns '' for a stale mark, so a property turns all six into
        # empty-string assertions that read like formatting bugs.
        #
        # There is deliberately NO second tab, and the Log is the SELECTED tab
        # at startup.  focus_set() and event_generate() are no-ops on an
        # unmapped widget, and a non-selected tab's widget is unmapped -- so
        # test_session.py::test_control_o_does_not_also_scribble_in_the_results
        # _pane, which focuses results_text and synthesises Ctrl+O, proves
        # nothing whatsoever if the Log is not on screen.  "Tidying up" by
        # pre-creating an empty run tab here moves the failure to a test whose
        # name points at the menubar.
        #
        # No <<NotebookTabChanged>> -> canvas.focus_set() handler either.
        # Measured: nb.select() does not steal focus, and that property is what
        # makes a later auto-switch safe.  Wiring a focus_set here would invent
        # a focus steal on every Calculate that does not exist today.  (That
        # handler belongs to a PLOT notebook, which is not what this is.)
        self.results_nb = ttk.Notebook(results_frame)
        self._log_tab = ttk.Frame(self.results_nb)
        self.results_text = ScrolledText(self._log_tab, height=10,
                                         wrap=tk.NONE, font=("Consolas", 9))
        self.results_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Tag for highlighting non-empty Sign flags. Configured once.
        self.results_text.tag_configure("flag", foreground="#b04000")
        # One tag per palette slot, configured once, for the swatch at the head
        # of each results row (_append_swatched).  Same precedent as "flag":
        # a Text tag, not a Treeview -- a Treeview would take the 'aligned'
        # units mode (one SI prefix per column, right-aligned) with it, lose
        # select-drag-copy into a mail, and freeze its row height at 20 px so
        # the text clips under DPI scaling.
        for _i, _c in enumerate(COLORS):
            self.results_text.tag_configure(f"c{_i}", foreground=_c)
        self.results_nb.add(self._log_tab, text=log_tab_label(0))
        self.results_nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.results_nb.bind("<<NotebookTabChanged>>",
                             self._on_results_tab_changed, add="+")
        # Right-click a tab for Keep / Close / Close others.  nb.index("@x,y")
        # resolves the clicked tab at every tab count, and <Button-3> is not a
        # Notebook class binding, so it is free.  (Deliberately NOT a per-tab
        # close button: on the vista theme Style.element_create means replacing
        # the layout that draws the native tab, hand-wiring hit-testing, and a
        # result that renders differently on the red-zone box with no test able
        # to see it.)
        self._run_tab_menu = tk.Menu(self, tearoff=0)
        self._run_tab_menu.add_command(label="Keep this run",
                                       command=self._on_menu_keep_run)
        self._run_tab_menu.add_command(label="Close this run",
                                       command=self._on_menu_close_run)
        self._run_tab_menu.add_command(
            label="Close other runs (kept runs stay)",
            command=self._on_menu_close_other_runs)
        self._run_tab_menu_target: Optional[RunTab] = None
        self.results_nb.bind("<Button-3>", self._on_run_tab_context_menu)

        self.plot = PlotPanel(plot_frame, on_marker_changed=self._on_marker_drag)
        self.plot.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        parent.add(results_frame, weight=0)
        parent.add(plot_frame, weight=1)

    def _bind_events(self) -> None:
        self.files_lb.bind("<<ListboxSelect>>", lambda e: self._on_file_selected())
        self.traces_lb.bind("<<ListboxSelect>>", lambda e: self._on_trace_selected())
        # A different file means a different port count: the Port / To
        # dropdowns and the overview strip both key off it.
        self.ed_file_cbo.bind("<<ComboboxSelected>>",
                              lambda e: self._on_editor_file_changed())
        # Expanding or collapsing a hint changes the form's height.
        self.bind("<<HintToggled>>",
                  lambda e: self._refresh_editor_scrollregion(preserve=True),
                  add="+")
        # Space toggles the selected trace's visibility -- the sweep gesture for
        # "hide these four".  Returning "break" stops the Listbox class binding
        # from also treating space as select-activate-item.
        self.traces_lb.bind("<space>", self._on_toggle_trace_key)

        # Freeze / Unfreeze live on a right-click menu, NOT on a fifth button:
        # the Traces row is measured at 448 px with four buttons already asking
        # 364, and Global Controls has no spare row either (a fifth one comes
        # straight out of an editor viewport that is down to 45 px at the
        # minsize).
        self._trace_menu = tk.Menu(self, tearoff=0)
        self._trace_menu.add_command(label=FREEZE_MENU_LABEL,
                                     command=self._on_freeze_trace)
        self._trace_menu.add_command(label=UNFREEZE_MENU_LABEL,
                                     command=self._on_unfreeze_trace)
        # Attribution is on this menu for the same reason Freeze is: it acts on
        # ONE trace, and the right-click selects the row under the pointer
        # first, so the gesture and the subject cannot disagree.  It is also on
        # the Analyze menu, which is the discoverable route -- a right-click
        # menu is invisible until you try it.
        #
        # APPENDED, and with NO separator in front of it.  A separator carries
        # no -label, so `entrycget(i, "label")` raises TclError on it, and the
        # existing guard in tests/test_freeze_trace.py enumerates this menu's
        # labels by index; a separator would turn a one-token test update into
        # an error.  Three commands, all acting on the selected trace, do not
        # need a rule between them anyway.
        self._trace_menu.add_command(label=ATTRIB_MENU_LABEL,
                                     command=self._on_attribution)
        # APPENDED for the same two reasons, and it is the FOURTH entry: the
        # file set belongs to a trace, and the right-click selects the row
        # under the pointer first so the gesture and the subject cannot
        # disagree.  Still no separator -- see above.
        self._trace_menu.add_command(label=FILES_MENU_LABEL,
                                     command=self._on_files_window)
        self.traces_lb.bind("<Button-3>", self._on_trace_context_menu)

        # The Files list gets the SAME entry, because "which files is this
        # trace made of" is the question a user asks while looking at the
        # Files list -- and because a right-click there is the only gesture
        # that reaches the window without going via the menubar.  It does NOT
        # select a file row: the window is about the SELECTED TRACE, and
        # moving the file selection under the pointer would change what the
        # editor and Ports & Roles are describing as a side effect of asking a
        # question about something else.
        self._files_menu = tk.Menu(self, tearoff=0)
        self._files_menu.add_command(label=FILES_MENU_LABEL,
                                     command=self._on_files_window)
        self.files_lb.bind("<Button-3>", self._on_files_context_menu)

        # ---- auto-apply -------------------------------------------------
        # Registered HERE, after _build_ui, and never in a widget constructor:
        # PlaceholderEntry.__init__ ends with _show_if_empty(), which writes its
        # variable, so a callback attached during construction would fire a sync
        # before self.traces exists.
        for pe in (self.ed_porta, self.ed_portb, self.ed_short, self.ed_gnd,
                   self.ed_label):
            pe.on_change(self._schedule_editor_sync)
        for var in (self.ed_file_var, self.ed_mode_var, self.ed_plot_self_var,
                    self.ed_plot_mutual_var):
            var.trace_add("write", self._schedule_editor_sync)

    # --------------------------------------------------------------- File ops

    def _load_one_file(self, path: str) -> TouchstoneData | None:
        """
        Parse one file, reporting a failure in terms the user can act on.

        `str(TouchstoneParseError)` is already the full report -- line number,
        verdict, next step -- so the dialog just shows it.  When the parser
        says the file could still be read by skipping the bad values, that is
        offered as a button rather than buried in the text: a user who only
        wants to look at a sweep should not have to find a CLI flag, and the
        warnings the lenient read produces say loudly enough that the numbers
        are suspect.
        """
        try:
            return parse_touchstone(path)
        except TouchstoneParseError as e:
            if not e.retry_lenient:
                messagebox.showerror("Cannot read file", str(e))
                return None
            if not messagebox.askyesno(
                    "Cannot read file",
                    f"{e}\n\nLoad it anyway, skipping the values that do not "
                    f"parse?"):
                return None
            try:
                return parse_touchstone(path, lenient=True)
            except TouchstoneParseError as e2:
                messagebox.showerror("Cannot read file", str(e2))
                return None
        except Exception as e:                          # pragma: no cover
            messagebox.showerror("Cannot read file", f"{path}\n\n{e}")
            return None

    def _on_add_file(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select Touchstone file(s)",
            filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                       ("All files", "*.*")],
        )
        for p in paths:
            ts = self._load_one_file(p)
            if ts is None:
                continue
            fe = FileEntry(ts)
            self.files.append(fe)
            self._append_result("")
            for line in ts.summary_lines():
                # The summary is a description of the file (info), except for
                # the parser's own WARN lines -- "I guessed" / "I threw
                # something away" is the one part of it that must announce
                # itself when the Log is not the tab on screen.
                self._append_result(
                    line,
                    LOG_WARN if line.lstrip().startswith("WARN:") else LOG_INFO)
            # Auto-create a default trace bound to this file
            tc = self._make_default_trace(fe)
            self.traces.append(tc)
        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        # Select last-added file/trace for convenience
        if self.files:
            self.files_lb.selection_clear(0, tk.END)
            self.files_lb.selection_set(tk.END)
            self.files_lb.activate(tk.END)
        if self.traces:
            self.traces_lb.selection_clear(0, tk.END)
            self.traces_lb.selection_set(tk.END)
            self.traces_lb.activate(tk.END)
            self._on_trace_selected()

    def _on_remove_file(self) -> None:
        idx = self._sel_idx(self.files_lb)
        if idx is None:
            return
        fe = self.files.pop(idx)
        # Drop traces bound to this file -- in ANY of their slots, not only as
        # the home file.  A composed trace whose second file has gone is not a
        # single-file trace, it is a trace that can no longer be computed at
        # all, and leaving it in the list to say so on the next Calculate is
        # the same "the plot and the Traces list disagree" the call below
        # exists to prevent.
        #
        # Identity, never `t not in dropped`: TraceConfig is an eq=True
        # dataclass holding numpy arrays, so == against a non-matching trace
        # raises "truth value of an array is ambiguous" -- the documented
        # reason _apply_editor_sync uses `any(t is tc ...)`.
        keep, dropped = [], []
        for t in self.traces:
            (dropped if fe.label in trace_file_labels(t) else keep).append(t)
        self.traces = keep
        # A trace removed because of its HOME file needs no explanation -- the
        # Files row is right there.  One removed because of a file it merely
        # composed with does: the name that went is not the name on the trace.
        by_extra = [t for t in dropped if t.file_label != fe.label]
        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        # Same call, same position, same reason as _on_remove_trace: the traces
        # bound to this file are gone from the list, and without this the PLOT
        # keeps drawing and legending their curves until the next Calculate.
        # The readout box IS the legend, so the stale name sat in the cursor
        # readout too -- the plot and the Traces list disagreeing about which
        # measurements exist is exactly what the run pages' banner exists to
        # prevent.  _replot_from_cache already skips a trace whose file is
        # gone, so this needs nothing but the call.
        self._replot_from_cache()
        # Same call, same reason, as in _on_remove_trace: a window whose trace
        # went with this file, or whose file alone went, must stop claiming it
        # can recompute.  Both cases land here -- the traces bound to the file
        # were dropped above, and a window on a trace bound to it resolves its
        # file through _file_by_label, which now returns None.
        refresh_attribution_windows(self)
        # And the same for the file windows, which is the LOUDER case here:
        # this is the one path that can remove a file a surviving trace still
        # composes with, so an open window would keep listing a file that is
        # gone with a port count beside it.
        refresh_files_windows(self)
        self._append_result(f"Removed {fe.label}")
        if by_extra:
            self._append_result(
                f"  also removed {len(by_extra)} trace(s) that composed with "
                f"it: " + ", ".join(f"[{t.id}] {_trunc_str(t.label, 18)}"
                                    for t in by_extra), LOG_WARN)

    def _on_show_ports(self) -> None:
        """
        Open (or raise) the Ports & Roles window.

        This used to dump the port names into the Results pane, where they
        scrolled away behind the next thing printed and said nothing about what
        the spec was doing with them -- which is the question. This is still the
        ONLY route to the file's port names (the Port / To dropdowns carry bare
        numbers, for the measured width reason in
        docs/design_connection_table.md §5a), so it must not silently do
        nothing: with no selection in the Files list it falls back to the file
        the editor is pointing at, which is the one the user is describing ports
        for, and says so if there is no file at all.
        """
        if not self.files:
            messagebox.showinfo("No file", "Add a file first.")
            return
        win = self._port_roles_win
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
        else:
            win = self._port_roles_win = PortRolesWindow(self)
        self._refresh_port_roles_window()

    def _port_roles_data(self) -> tuple:
        """
        (header, roles, warnings) for the Ports & Roles window.

        Reads the SELECTED TRACE, not the editor widgets: auto-apply has
        already written them there by the time the strips run, and going
        through the trace is what makes this work in every mode rather than
        only in the two with tables.
        """
        idx = self._sel_idx(self.files_lb)
        fe = self.files[idx] if idx is not None else None
        if fe is None:
            fe = self._file_by_label(self.ed_file_var.get())
        tidx = self._sel_idx(self.traces_lb)
        tc = (self.traces[tidx] if tidx is not None and tidx < len(self.traces)
              else None)
        if fe is None or tc is None:
            return ("(no file selected)" if fe is None
                    else f"{fe.label} — no trace selected", [], {})
        mports, conn, extra, src = _trace_role_rows(tc)
        # R3-4: on a composed trace this window shows the COMPOSED namespace,
        # because that is the port list the spec is written against -- 'port 7'
        # on a 6-port die is F2.1, and a window counting only the die's six
        # would report it as out of range or, worse, as the die's port 7.
        #
        # It never raises here.  This window's job is to show what was TYPED
        # (that is why it renders modes 1/2/3/6 through the permissive rows
        # path at all), so a composition that cannot be built degrades to the
        # home file's own port list with a note, rather than blanking.
        net, home, nports, names, note = None, "", fe.ts.nports, \
            fe.ts.port_names, ""
        if trace_is_composed(tc):
            # The NAMESPACE, not the composition: this runs from
            # _apply_editor_strips, i.e. once per keystroke, and stacking two
            # real files there is measured at up to 10.5 s.  Which ports exist
            # and what F2.3 means need none of that arithmetic.
            net, home = self._trace_namespace(tc)
            if net is not None:
                nports, names = net.nports, net.port_labels()
        try:
            if net is not None:
                mports = _scope_mport_rows(mports, net, home)
                conn = _scope_conn_rows(conn, net, home)
                extra = _scope_dsl_text(extra, net, home)
            term = build_terminations_rows(mports, conn, extra, nports=nports)
        except Exception:
            term = None
        roles = port_roles(term, nports, names, src)
        header = (_roles_header(trace_file_legend(tc), nports, roles)
                  if trace_is_composed(tc)
                  else _roles_header(fe.label, nports, roles)) + note
        if term is None:
            header += "  (spec did not parse)"
        # The mode decides which of the two overlap rules the window states:
        # mode 6 is the one that routes to build_terminations_coupling and
        # refuses, every other mode lets ground win.
        return header, roles, _role_warnings(roles, mports,
                                             coupling=(tc.mode == 6))

    def _refresh_port_roles_window(self) -> None:
        """
        Push a fresh snapshot into the window, if one is open.

        Same contract as _apply_editor_strips, which is what calls it: never
        raises, writes to nothing but the window's own widgets, and never
        touches a TraceConfig.
        """
        win = self._port_roles_win
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._port_roles_win = None
                return
            win.refresh(*self._port_roles_data())
        except Exception:               # pragma: no cover - see the contract
            pass

    def apply_ports_as(self, role: str, ports: Sequence[int]) -> str:
        """
        Write the window's selected ports back into the editor. Returns a note.

        Routed through the same widgets the user types into -- the RowTable's
        add_row / the PlaceholderEntry's set_value -- so auto-apply, the strips
        and the stale marker all follow exactly as they do for a keystroke.
        Nothing here writes a TraceConfig directly.

        The ports go in as a COLLAPSED RANGE, which is the whole point: a
        54-ball ground group becomes one readable row instead of 54.
        """
        spec = collapse_ports(ports)
        if not spec:
            return ""
        tidx = self._sel_idx(self.traces_lb)
        tc = (self.traces[tidx] if tidx is not None and tidx < len(self.traces)
              else None)
        if tc is None:
            return "Select a trace first."
        if tc.frozen:
            # Same refusal as the editor itself: a snapshot's spec has to keep
            # describing the numbers printed beside it.
            return "That trace is a frozen snapshot — unfreeze it first."
        mode = int(self.ed_mode_var.get())
        if role == "ground":
            if mode == 5:
                self.ed_conn_table.add_row({"kind": "ground", "ports": spec})
                where = "a new connections row"
            else:
                self.ed_gnd.set_value(_append_port_spec(
                    self.ed_gnd.get_value(), spec))
                where = "the GND / VDD field"
        elif role == "probe+":
            if mode in (5, 6):
                self.ed_mp_table.add_row({"plus": spec})
                where = "a new measurement-port row"
            else:
                self.ed_porta.set_value(_append_port_spec(
                    self.ed_porta.get_value(), spec))
                where = "the Signal / Port A field"
        else:                                           # pragma: no cover
            return f"Unknown role '{role}'."
        # No _schedule_editor_sync() here ON PURPOSE. RowTable.add_row notifies
        # its on_change and PlaceholderEntry.set_value writes its variable, and
        # both of those are already wired to the sync -- calling it again would
        # be an unfalsifiable line that hides whether the write really went
        # through the widgets. The strips are refreshed explicitly because they
        # are only scheduled from the sync in some modes.
        self._refresh_editor_strips()
        self._refresh_editor_scrollregion(preserve=True)
        return f"{spec} → {where}."

    def _on_check_file(self) -> None:
        """
        Print the file-structure report for the selected file, or for one
        picked from disk when nothing is selected.

        This covers the case the error dialog cannot: the file LOADS, but the
        numbers look wrong.  Then the question is whether the port count was
        guessed, whether the sweep is what was simulated, and whether the
        record grid actually lines up -- and none of that is visible anywhere
        else.  It also reaches files that fail to load, since those never make
        it into the list.
        """
        idx = self._sel_idx(self.files_lb)
        fe = self.files[idx] if idx is not None else None
        if fe is not None:
            path = fe.ts.source_path
        else:
            path = filedialog.askopenfilename(
                title="Check which Touchstone file?",
                filetypes=[("Touchstone / text", "*.s*p *.txt *.dat"),
                           ("All files", "*.*")])
            if not path:
                return
        self._append_result("")
        for line in diagnose_touchstone(path).splitlines():
            self._append_result(line)

    def _on_file_selected(self) -> None:
        # The Ports & Roles window resolves its file from the Files list first,
        # so selecting a different file there has to re-render it.  Nothing
        # else in the application reacts to this selection.
        self._refresh_port_roles_window()

    # --------------------------------------------------------------- Trace ops

    def _make_default_trace(self, fe: FileEntry) -> TraceConfig:
        tc = TraceConfig(
            id=self._next_trace_id,
            file_label=fe.label,
            mode=1,
            port_a="1",
            label=f"{fe.label}_p1_to_gnd",
            color_idx=(self._next_trace_id - 1) % len(COLORS),
            ls_idx=((self._next_trace_id - 1) // len(COLORS)) % len(LINESTYLES),
        )
        self._next_trace_id += 1
        return tc

    def _on_add_trace(self) -> None:
        if not self.files:
            messagebox.showinfo("No file", "Add a file first.")
            return
        fe = self.files[self._sel_idx(self.files_lb) or 0]
        tc = self._make_default_trace(fe)
        self.traces.append(tc)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        self._on_trace_selected()

    def _on_remove_trace(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        self.traces.pop(idx)
        self._refresh_trace_list()
        self._replot_from_cache()
        # An Attribution window HOLDS a result, so unlike the Ports & Roles
        # window it cannot re-read app.traces and degrade -- it has to be
        # told.  Same class of omission as the _on_remove_file
        # forgot-to-replot bug: nothing raises, and the window carries on
        # naming a trace that is gone with a [Recompute] button that would
        # answer about nothing.  It resolves its subject by identity against
        # app.traces, so this call is the whole of what is needed.
        refresh_attribution_windows(self)
        # Same reason, same position: a file window resolves its subject by
        # identity too, and one on a trace that is gone would keep offering
        # [Set as home] on it.
        refresh_files_windows(self)

    def _on_toggle_trace_key(self, _event=None) -> str:
        self._on_toggle_trace()
        return "break"      # or the Listbox also select-activates on space

    def _on_toggle_trace(self) -> None:
        """
        Show / hide the selected trace without deleting it.

        Replots from the cached Z instead of recomputing: the whole point is
        that taking a curve off the plot should not cost a Schur reduction of a
        153-port file to arrive at numbers that have not changed.
        """
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = self.traces[idx]
        tc.enabled = not tc.enabled
        # The editor is showing this trace, so its checkbox has to follow --
        # suppressed, or the write trace schedules a sync that would just write
        # the same value back.
        self._suppress_editor_sync = True
        try:
            self.ed_enabled_var.set(tc.enabled)
        finally:
            self._suppress_editor_sync = False
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)
        self._replot_from_cache()

    def set_trace_home_file(self, tc: TraceConfig, label: str) -> None:
        """
        Make `label` the trace's HOME file -- the one a bare port number means.

        The hook pkg_rlc_files_gui's 'Set home' button looks up BY NAME (it
        reports its absence rather than doing nothing).  It SWAPS: the file set
        is unchanged and only which of them is typed bare moves.

        Two things it must do that writing `tc.file_label` directly does not.

        (a) IT GOES THROUGH THE EDITOR for the selected trace.  The editor owns
            the File combobox, so a label poked onto the trace is overwritten
            by the very next `_sync_editor_to_trace` -- the same rule the Ports
            & Roles write-back follows.

        (b) IT SAYS THAT THE TAGS RENUMBERED.  A tag is a POSITION: F1 is the
            home file, so making F2 the home makes the old home F2, and every
            'F2.<port>' already typed in the connection table now names the
            other file.  Nothing here can rewrite those cells -- the table's
            rows are the user's text and guessing at them is how a spec means
            something it does not say -- so the swap is reported where the user
            reads results, with both names in it.

        A FROZEN trace refuses, by name and for the same reason
        `_sync_editor_to_trace` does: its numbers and the spec printed beside
        them have to keep describing each other, and re-homing it would change
        what every bare port number in it meant.
        """
        label = str(label or "").strip()
        old = tc.file_label
        if not label or label == old:
            return
        if tc.frozen:
            self._append_result(
                f"  [{tc.id}] {tc.label}: frozen snapshot -- its files cannot "
                f"be changed. Right-click it in the Traces list → "
                f"{UNFREEZE_MENU_LABEL} first.", LOG_WARN)
            return
        before = _config_signature(tc)
        # Everything except the new home, in its existing order.  The old home
        # lands wherever it fell in that order, which is position 0, so a
        # two-file trace simply swaps.
        tc.file_labels = [lbl for lbl in trace_file_labels(tc) if lbl != label]
        tc.file_label = label
        idx = self._sel_idx(self.traces_lb)
        if (idx is not None and idx < len(self.traces)
                and self.traces[idx] is tc):
            self._suppress_editor_sync = True
            try:
                self.ed_file_var.set(label)
            finally:
                self._suppress_editor_sync = False
        if _config_signature(tc) != before and tc.Z is not None:
            # Same rule as _apply_editor_sync: the drawn curve is now older
            # than the spec that describes it.
            tc.stale = True
        self._refresh_trace_list()
        self._append_result(
            f"  [{tc.id}] {tc.label}: home file is now {label} — its ports are "
            f"typed bare. The file tags renumbered: "
            f"{trace_file_legend(tc)} (was {old} first), so a tagged port cell "
            f"written before this now names a different file.", LOG_WARN)
        self._refresh_port_roles_window()

    def _on_duplicate_trace(self) -> None:
        # Without the flush the copy is taken from the trace as it was BEFORE
        # the edit still queued in the editor -- i.e. Duplicate would silently
        # copy something the user cannot see any more.
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        new = _duplicate_trace_config(self.traces[idx], self._next_trace_id)
        self._next_trace_id += 1
        self.traces.append(new)
        self._refresh_trace_list()
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(tk.END)
        self._on_trace_selected()

    # ------------------------------------------------------- Freeze / Unfreeze
    #
    # "Freeze as new trace" is the answer to "is this better than what I had?".
    # A results log can only compare two NUMBERS at the marker frequency; a
    # frozen trace is a second curve, so the two specs are compared over the
    # whole sweep, side by side, with the cursor readout printing both in
    # adjacent columns and both landing in the results table and the CSV.
    #
    # What makes it trustworthy is that a frozen trace is inert: Calculate
    # skips it (_on_calculate) and the editor refuses to write it
    # (_sync_editor_to_trace).  Everything else is unchanged -- it plots, it
    # hides, it exports, Remove removes it.

    def _on_trace_context_menu(self, event) -> None:
        """
        Right-click on the Traces list.

        The click SELECTS the row under the pointer first.  A menu that acts on
        whatever happened to be selected before is how you freeze the wrong
        trace -- and the two entries are enabled from that row's state, so the
        selection has to be settled before the menu is posted.
        """
        idx = self.traces_lb.nearest(event.y)
        if idx < 0 or idx >= len(self.traces):
            return
        self.traces_lb.selection_clear(0, tk.END)
        self.traces_lb.selection_set(idx)
        self.traces_lb.activate(idx)
        self._on_trace_selected()
        self._sync_trace_menu(self.traces[idx])
        try:
            self._trace_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._trace_menu.grab_release()

    def _sync_trace_menu(self, tc: TraceConfig) -> None:
        """Only one of the FREEZE pair is ever live, and it says which."""
        self._trace_menu.entryconfigure(
            FREEZE_MENU_LABEL, state=tk.DISABLED if tc.frozen else tk.NORMAL)
        self._trace_menu.entryconfigure(
            UNFREEZE_MENU_LABEL,
            state=tk.NORMAL if tc.frozen else tk.DISABLED)
        # Attribution is the exception: it stays LIVE even on a trace it cannot
        # run on, because `attribution_refusal` names five different reasons
        # (frozen / file not loaded / never calculated / one measurement port /
        # edited since the last Calculate) and each of them tells the user what
        # to do next.  A greyed entry would be the same bug report -- the
        # identical decision, in the identical words, as the Freeze entry on a
        # stale trace.  It is set explicitly rather than left at its default so
        # the invariant is stated where it can be read and asserted.
        self._trace_menu.entryconfigure(ATTRIB_MENU_LABEL, state=tk.NORMAL)
        # LIVE on a frozen trace too, and for the same reason: the window is
        # read-only on one (`_apply_file_set` refuses by name), and a snapshot
        # is exactly the trace whose file set someone wants to READ while
        # comparing it against the live one beside it.
        self._trace_menu.entryconfigure(FILES_MENU_LABEL, state=tk.NORMAL)

    def _on_files_context_menu(self, event) -> None:
        """
        Right-click on the Files list -> the per-trace file window.

        Deliberately does NOT move the file selection: the window is about the
        selected TRACE, and re-selecting a file would change what the editor's
        Ports & Roles view is describing as a side effect of a question about
        something else.  `_on_show_ports` already falls back to the editor's
        file when nothing is selected, so nothing here depends on it either.
        """
        try:
            self._files_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._files_menu.grab_release()

    def _on_files_window(self) -> None:
        """
        Open (or raise) the "Files in this trace" window on the selected trace.

        One decision in one place, the `_on_attribution` rule: the refusal for
        "no trace selected" lives in `files_refusal`, so the menubar entry and
        both right-click entries cannot start refusing different things.  The
        editor is flushed first for the Calculate reason -- a keystroke in the
        same event burst as the click is still in the idle queue, and a window
        that opened showing the spec from an event ago would be describing a
        file set the user has already changed.

        Returns the window (or None), so a caller -- and a test -- can reach
        the thing it just opened without walking `files_gui.live_windows`.
        """
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        tc = (self.traces[idx]
              if idx is not None and idx < len(self.traces) else None)
        refusal = files_refusal(tc)
        if refusal:
            messagebox.showinfo(FILES_TITLE, refusal)
            return None
        return open_files_window(self, tc)

    def _on_freeze_trace(self) -> None:
        # Same flush as Duplicate, for the same reason: without it the snapshot
        # is taken from the trace as it was BEFORE the edit still sitting in
        # the idle queue -- i.e. it would freeze a spec that is not on screen.
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            messagebox.showinfo("No trace", "Select a trace first.")
            return
        src = self.traces[idx]
        if src.frozen:
            return
        # The flush above is what makes the stale check reliable: the freshest
        # spec is on the trace by now, so `stale` answers about the spec the
        # user can see rather than the one that was there an event ago.
        refusal = freeze_refusal(src)
        if refusal:
            messagebox.showinfo(*refusal)
            return
        tc = _freeze_trace_config(src, self._next_trace_id)
        self._next_trace_id += 1
        self.traces.append(tc)
        self._refresh_trace_list()
        # The SOURCE stays selected: freezing is the first half of "now change
        # something and look at the difference", so the editor must not jump to
        # the copy the user is not going to edit.
        self.traces_lb.selection_set(idx)
        self._append_result(
            f"  Froze [{src.id}] {src.label} as [{tc.id}] {tc.label}: it keeps "
            f"these numbers, Calculate skips it and the editor will not write "
            f"it (right-click → {UNFREEZE_MENU_LABEL} to release it).")
        # It goes into the results table NOW, not at the next Calculate -- the
        # table is where the two are read against each other, and a baseline
        # that appears one press later is a baseline nobody trusts.  It joins
        # the CURRENT run rather than starting one: the run number counts
        # Calculates, and freezing measures nothing.
        run = self._last_run or self._empty_run()
        if tc.coupling is not None:
            run = replace(run, blocks=run.blocks + (
                _snapshot_block(tc, tc.file_label, tc.coupling),))
        elif tc.rlc is not None:
            run = replace(run, rows=run.rows + (
                _snapshot_row(tc, tc.file_label, tc.rlc),))
        self._last_run = run
        self._render_results(run)
        # The run PAGE is rewritten in place for the same reason: freezing
        # measures nothing, so it is not a new run and gets no new tab.
        newest = self._newest_run_tab()
        if newest is not None and newest.run.number == run.number:
            newest.run = run
            self._render_run_tab(newest)
        self._replot_from_cache()

    def _on_unfreeze_trace(self) -> None:
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = self.traces[idx]
        if not tc.frozen:
            return
        tc.frozen = False
        self._refresh_trace_list()
        self.traces_lb.selection_set(idx)
        # The selection is this trace, so the editor showing it has to come
        # back to life in the same gesture.
        self._set_editor_editable(True)
        self._append_result(
            f"  [{tc.id}] {tc.label} is no longer frozen: the next Calculate "
            f"will recompute it and REPLACE the snapshot numbers it is "
            f"holding.", LOG_WARN)

    # ------------------------------------------------------------ Attribution
    #
    # The window itself is pkg_rlc_attrib_gui; everything here is the route
    # to it.  Two routes on purpose: the Analyze menu is the discoverable one,
    # the Traces right-click is the one that is already under the pointer when
    # you are looking at a trace.  There is a third pointer -- one line under
    # the coupling block in the Results pane, see _run_report_segments -- and
    # that is the one that reaches the user who is staring at "M = 2.16 pH"
    # wondering where it came from.

    def _on_attribution(self) -> None:
        """
        Open the Attribution window on the SELECTED trace.

        No refusal logic here.  `open_attribution_window` resolves the file,
        flushes the editor (a keystroke in the same event burst as the click is
        still in the idle queue, so without it the staleness check answers
        about the spec from an event ago), asks `attribution_refusal` and shows
        whatever it returns -- including for `trace=None`, which is why no
        selection is not special-cased here.  One decision, in one place, so
        the menubar entry and the right-click entry cannot start refusing
        different things.
        """
        idx = self._sel_idx(self.traces_lb)
        tc = (self.traces[idx]
              if idx is not None and idx < len(self.traces) else None)
        open_attribution_window(self, tc)

    def _on_trace_selected(self) -> None:
        # Land any queued edit on the trace it was typed into, BEFORE the
        # editor is reloaded from a different one.  Deferring the sync is what
        # makes it safe (see _schedule_editor_sync); flushing here is what
        # keeps it from landing on the wrong trace.
        self._flush_editor_sync()
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            return
        tc = self.traces[idx]
        self._migrate_trace(tc)
        self._suppress_editor_sync = True
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
            self._ed_extra_lines = tc.extra_lines
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
            self._suppress_editor_sync = False
        self._refresh_editor_strips()

    def _migrate_trace(self, tc: TraceConfig) -> None:
        """
        Fold retired shapes forward: mode 4 -> 2, mp1/mp2/mp_more -> table,
        custom_text -> the two tables.

        The custom-text block runs LAST on purpose: a legacy config carrying
        both mp1_* and a stale custom_text must fill `mports` first, so the
        custom-text guard declines rather than merging two unrelated specs.
        """
        if tc.migrate_legacy_mode():
            self._append_result(
                f"  [{tc.id}] {tc.label}: mode 4 (A↔B + VDD/GND) is retired; "
                f"migrated to mode 2 with VDD folded into GND "
                f"(GND = {tc.gnd_ports or '(none)'})", LOG_WARN)
            self._refresh_trace_list()
        if tc.migrate_legacy_mports():
            self._append_result(
                f"  [{tc.id}] {tc.label}: the Port 1 / Port 2 / 'More ports' "
                f"fields are retired; migrated to {len(tc.mports)} row(s) of "
                "the measurement-port table", LOG_WARN)
            self._refresh_trace_list()
        legacy_custom = tc.custom_text
        if tc.migrate_legacy_custom_text():
            # Ask _import_dsl_text again rather than inferring the verbatim
            # fallback from empty tables: a spec that is nothing but comments
            # also leaves both empty, and telling that user their precedence
            # changed would be a lie.
            if _import_dsl_text(legacy_custom)[3]:
                self._append_result(
                    f"  [{tc.id}] {tc.label}: the free-text Custom spec is kept "
                    "verbatim -- moving it into the table would have changed "
                    "which port wins (a 'signal' line follows a 'ground' on the "
                    "same port). Open 'Edit as text…' to convert it by hand.",
                    LOG_WARN)
            else:
                self._append_result(
                    f"  [{tc.id}] {tc.label}: the free-text Custom spec is "
                    f"retired; imported into {len(tc.mports)} measurement "
                    f"port(s) and {len(tc.conn_rows)} connection row(s)",
                    LOG_WARN)
            self._refresh_trace_list()

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
        moved = getattr(self, "_ed_shown_mode", None) != mode
        self._ed_shown_mode = mode
        self._refresh_editor_scrollregion(preserve=not moved)
        if self._strips_wanted():
            # The tables' on_change does not fire on set_rows, so the strips
            # would otherwise still show the previous mode's spec.
            self._refresh_editor_strips()

    # ------------------------------------------------- Mode 5 editor plumbing

    def _editor_nports(self) -> Optional[int]:
        """Port count of the file the editor currently points at, or None."""
        fe = self._file_by_label(self.ed_file_var.get())
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
                                 self._ed_extra_lines)
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
                or self._port_roles_win is not None
                or bool(attribution_windows(self)))

    def _on_editor_file_changed(self) -> None:
        self._refresh_port_choices()
        if self._strips_wanted():
            self._refresh_editor_strips()

    def _on_editor_rows_changed(self) -> None:
        """RowTable on_change: fires on EVERY keystroke in EVERY cell."""
        if self._suppress_editor_sync:
            return
        self._refresh_editor_scrollregion(preserve=True)
        self._schedule_editor_sync()    # also refreshes the strips
        if self.ed_mode_var.get() == 6 or self._port_roles_win is not None:
            self._refresh_editor_strips()   # for the style preview's span

    def _refresh_editor_strips(self) -> None:
        """Queue a strip refresh for the next idle moment, coalescing repeats."""
        if self._ed_strips_pending:
            return
        self._ed_strips_pending = True
        self.after_idle(self._apply_editor_strips)

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
        self._ed_strips_pending = False
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
        self._refresh_port_roles_window()
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
        refresh_attribution_windows(self)
        # The file windows get the whole picture rather than a banner, because
        # unlike an Attribution table nothing in them is a computed NUMBER:
        # the alias legend, the port counts and the spec problems are all
        # properties of the spec being typed, so they must follow it.  Same
        # contract -- `refresh_files_windows` never raises -- and it is outside
        # the try above for the same reason the Ports & Roles refresh is: a
        # window failure must not blank the strips.
        refresh_files_windows(self)

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
        extra = self._ed_extra_lines
        nports = self._editor_nports()
        names = self._editor_port_names()
        echoes: list[tuple] = []
        tc = self._selected_trace()
        if tc is not None and trace_is_composed(tc):
            net, home = self._trace_namespace(tc)
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
        idx = self._sel_idx(self.traces_lb)
        return (self.traces[idx]
                if idx is not None and idx < len(self.traces) else None)

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
        fe = self._file_by_label(self.ed_file_var.get())
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
                                self._ed_extra_lines)

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

        dlg = tk.Toplevel(self)
        dlg.title("Edit as text")
        dlg.transient(self)

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
        self.wait_window(dlg)

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
            self._ed_extra_lines = text.rstrip()
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
            self._ed_extra_lines = extra
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
        if self._suppress_editor_sync:
            return
        idx = self._sel_idx(self.traces_lb)
        if idx is None or idx >= len(self.traces):
            return
        self._ed_sync_target = self.traces[idx]
        if self._ed_sync_after is None:
            self._ed_sync_after = self.after_idle(self._apply_editor_sync)

    def _flush_editor_sync(self) -> None:
        """Run any queued sync NOW, against the trace it was queued for."""
        if self._ed_sync_after is None:
            return
        try:
            self.after_cancel(self._ed_sync_after)
        except Exception:
            pass
        self._ed_sync_after = None
        self._apply_editor_sync()

    def _cancel_editor_sync(self) -> None:
        """
        Drop a queued sync WITHOUT running it.

        Only correct when the target trace is being discarded outright (loading
        a session replaces the whole trace list).  Everywhere else the queued
        edit is the user's most recent keystroke and must land -- use
        _flush_editor_sync.
        """
        if self._ed_sync_after is not None:
            try:
                self.after_cancel(self._ed_sync_after)
            except Exception:
                pass
        self._ed_sync_after = None
        self._ed_sync_target = None

    def _apply_editor_sync(self) -> None:
        self._ed_sync_after = None
        tc = self._ed_sync_target
        self._ed_sync_target = None
        # A trace deleted between scheduling and running is not an error.
        # Identity, not `in`: TraceConfig is an eq=True dataclass holding numpy
        # arrays, so `tc not in self.traces` compares field by field and raises
        # "truth value of an array is ambiguous" the moment it reaches a Z that
        # is not the same object.
        if tc is None or not any(t is tc for t in self.traces):
            return
        before_spec = _config_signature(tc)
        before_draw = _draw_signature(tc)
        try:
            self._sync_editor_to_trace(tc)
        except Exception:
            return          # see (3) above -- Calculate will report it properly
        if _config_signature(tc) != before_spec and tc.Z is not None:
            # The curve on screen is older than the spec that now describes it.
            tc.stale = True
        self._refresh_trace_list()
        if _draw_signature(tc) != before_draw:
            self._replot_from_cache()
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
        tc.extra_lines = self._ed_extra_lines
        tc.label = self.ed_label.get_value() or f"trace_{tc.id}"

    # --------------------------------------------------------------- Calculate

    def _on_calculate_selected(self) -> None:
        """Recompute only the selected trace (the editor footer's button)."""
        idx = self._sel_idx(self.traces_lb)
        if idx is None:
            messagebox.showinfo("No trace", "Select a trace first.")
            return
        self._on_calculate(only=self.traces[idx])

    def _on_calculate(self, only: Optional[TraceConfig] = None) -> None:
        # Auto-sync editor for the currently selected trace before calculating.
        # Users routinely edit a field and press Calculate without applying;
        # with auto-apply that edit is usually already in, but a keystroke in
        # the same event burst as the click would still be sitting in the idle
        # queue, so the flush is what makes "what I see is what is computed"
        # unconditional.
        idx = self._sel_idx(self.traces_lb)
        if idx is not None:
            self._flush_editor_sync()
            try:
                self._sync_editor_to_trace(self.traces[idx])
            except Exception as e:
                messagebox.showerror("Editor sync error", str(e))
                return
            self._refresh_trace_list()
            self.traces_lb.selection_set(idx)

        try:
            f_rlc_hz = parse_si(self.rlc_freq_var.get()) * 1e9
        except ValueError as e:
            messagebox.showerror("Bad RLC freq", str(e))
            return

        do_fit = self.fit_model_var.get() != "none"
        if do_fit:
            try:
                fmin_hz = float(self.fit_fmin_var.get()) * 1e9
                fmax_hz = float(self.fit_fmax_var.get()) * 1e9
                if fmin_hz >= fmax_hz:
                    raise ValueError("f_min must be < f_max")
            except ValueError as e:
                messagebox.showerror("Bad fit band", str(e))
                return

        # A new run releases the claim the PREVIOUS run's error put on the
        # Results pane; this run's own errors put it back.
        self._log_forced = False
        # Runs are numbered by a monotonic counter, never identified by value:
        # two runs of an unchanged spec are equal in every field and are still
        # two different runs.
        self._run_counter += 1

        # Resolve the marker against every sweep this run will touch, BEFORE
        # the header is printed.  The header has to name the frequency the
        # numbers under it actually come from, and until this existed it named
        # the one the user typed while the Z-matrix line twenty lines further
        # down named the other, with nothing on screen to reconcile them.
        #
        # One argmin per FILE, deduplicated, not one per trace: 13.4 us on the
        # 401-point fixture and 26.4 us on a 5000-point sweep, against a Schur
        # reduction measured in tens of milliseconds.
        freq_snaps: list = []
        for tc in self.traces:
            if trace_is_composed(tc):
                # A composed trace's numbers are read on the COMPOSED axis --
                # the span intersection, resampled onto the finer grid -- and
                # not on any one file's own sweep.  So it contributes ONE entry
                # keyed by its file legend rather than one per file: a header
                # line naming `die.s6p` beside a number read off a grid neither
                # file has is the disagreement this list exists to remove.
                #
                # Swallowed on failure and reported by name in the main pass
                # below.  A composition that cannot be built has no axis, and a
                # header is not the place to explain why.
                try:
                    sn = self._trace_network(tc)
                except Exception:
                    continue
                key = trace_file_legend(tc)
                if not any(lbl == key for lbl, _ in freq_snaps):
                    freq_snaps.append((key, snap_to_grid(sn.freqs, f_rlc_hz)))
                continue
            for label in trace_file_labels(tc):
                fe = self._file_by_label(label)
                if fe is None or any(lbl == fe.label for lbl, _ in freq_snaps):
                    continue
                freq_snaps.append(
                    (fe.label, snap_to_grid(fe.ts.freqs, f_rlc_hz)))
        run_freq = combine_freq_snaps([s for _, s in freq_snaps])
        # A snap SMALLER than half a grid step is the tool doing its job on a
        # sampled axis, and is reported without badging the Log.  A snap larger
        # than that means the requested frequency is off the end of the sweep
        # (see FreqSnap.off_grid), which is a different thing entirely: the
        # answer is the band edge, not the frequency that was asked for.
        freq_sev = (LOG_WARN if any(s.off_grid for _, s in freq_snaps)
                    else LOG_INFO)
        scope = "" if only is None else f" [{only.id}] {only.label} only"
        self._append_result(
            f"\n=== Calculate @ "
            f"{marker_freq_text(run_freq if run_freq is not None else f_rlc_hz)}"
            f"{scope} ===", freq_sev)

        # First pass: compute Z and per-freq RLC; collect rows + fit_lines.
        #
        # These are SNAPSHOTS, taken as each trace finishes -- not the live
        # TraceConfig.  The next run overwrites the trace, and a collection
        # holding the object would then print this run's numbers under the next
        # run's label and port descriptor.
        result_rows: list[RowSnapshot] = []
        fit_lines: list[FitSnapshot] = []
        coupling_blocks: list[CouplingSnapshot] = []
        for tc in self.traces:
            missing = [lbl for lbl in trace_file_labels(tc)
                       if self._file_by_label(lbl) is None]
            if missing:
                # Names EVERY file that is missing, not just the home one: a
                # composed trace reporting one name while a second is also
                # gone sends the user to fix half the problem.
                self._append_result(
                    f"  [{tc.id}] {tc.label}: file "
                    + ", ".join(f"'{lbl}'" for lbl in missing)
                    + (" is" if len(missing) == 1 else " are") + " not loaded",
                    LOG_WARN)
                continue
            fe = self._file_by_label(tc.file_label)

            if tc.frozen:
                # Never recomputed -- that is the whole of what "frozen" means
                # -- but its cached numbers still go in the report, the same
                # shape as a trace "Calculate This Trace" skipped below.  A
                # snapshot missing from the table it is meant to be compared
                # against would be worse than useless.
                if tc.coupling is not None:
                    coupling_blocks.append(
                        _snapshot_block(tc, fe.label, tc.coupling))
                elif tc.rlc is not None:
                    result_rows.append(_snapshot_row(tc, fe.label, tc.rlc))
                if only is tc:
                    # Asked for by name, so say no by name.
                    self._append_result(
                        f"  [{tc.id}] {tc.label}: frozen snapshot -- not "
                        f"recomputed. Right-click it in the Traces list → "
                        f"{UNFREEZE_MENU_LABEL} first.", LOG_WARN)
                continue

            if only is not None and tc is not only:
                # Not recomputed -- but its last numbers still go in the table,
                # so "Calculate This Trace" narrows the WORK, not the report.
                # A table that shrank to one row would make the fast path look
                # like it had thrown the other traces away.
                if tc.coupling is not None:
                    coupling_blocks.append(
                        _snapshot_block(tc, fe.label, tc.coupling))
                elif tc.rlc is not None:
                    result_rows.append(_snapshot_row(tc, fe.label, tc.rlc))
                continue

            # Drop last run's matrix so a failed or re-moded trace can never
            # export stale coupling data.
            tc.Zmat = None
            tc.mport_names = None
            tc.coupling = None
            tc.fit_freqs = None
            tc.fit_Z = None
            tc.net_freqs = None
            tc.reference_checks = None
            # About to be recomputed from the current spec, so whatever the
            # editor did since the last run is now accounted for.
            tc.stale = False

            # What this trace is solved against.  One file: the FileEntry's own
            # arrays, the path every trace took before composition existed.
            # Several: the stacked network, on the composed frequency axis.
            try:
                sn = self._trace_network(tc)
            except Exception as e:
                tc.Z = None
                self._append_result(
                    f"  [{tc.id}] {tc.label}: ERROR {e}", LOG_ERROR)
                for problem in compose_spec_problems(
                        tc, [f.label for f in self.files]):
                    self._append_result(f"      {problem}", LOG_ERROR)
                continue
            if sn.composed:
                # The file set's own faults first -- a file listed twice counts
                # once, and saying so is the difference between a spec the user
                # can fix and a port map that quietly addresses one block.
                for problem in compose_spec_problems(
                        tc, [f.label for f in self.files]):
                    self._append_result(f"    [{tc.id}] {problem}", LOG_WARN)
                # Then what the composition itself decided: the weld note, the
                # grid it adopted, what it dropped, how much phase an
                # interpolation invented.  These are the assumptions the number
                # below rests on, so they are printed with it and not stored in
                # a report nobody opens.
                for note in sn.notes:
                    self._append_result(f"    [{tc.id}] {note}")
                for warn in sn.warnings:
                    self._append_result(f"    [{tc.id}] {warn}", LOG_WARN)

            # The validation strip is capped at two lines and points here for
            # the rest; this is what makes that pointer true. Only the OVERFLOW
            # is printed -- the first two are already on screen, and repeating
            # them for every clean trace would be noise.
            if tc.mode == 5:
                # Scoped first on a composition, exactly as the editor strip
                # does it (`_editor_spec_inputs`): the two must answer about
                # the same spec, or the strip says a tagged cell is fine while
                # the Log says it does not parse.  Never raises here either --
                # a bad tag is reported by the build below, with its message.
                v_echo: list[tuple] = []
                try:
                    if sn.composed:
                        # Built from the UNSCOPED rows, exactly as the editor
                        # does it: the tag is what the echo is about and
                        # scoping removes it.
                        v_echo = scope_echo_messages(
                            tc.mports, tc.conn_rows, tc.extra_lines,
                            sn.net, sn.home_alias)
                    v_mp, v_conn, v_extra = (
                        (_scope_mport_rows(tc.mports, sn.net, sn.home_alias),
                         _scope_conn_rows(tc.conn_rows, sn.net, sn.home_alias),
                         _scope_dsl_text(tc.extra_lines, sn.net,
                                         sn.home_alias))
                        if sn.composed else
                        (tc.mports, tc.conn_rows, tc.extra_lines))
                except Exception:
                    v_mp, v_conn, v_extra = (tc.mports, tc.conn_rows,
                                             tc.extra_lines)
                notes = _validation_messages(v_mp, v_conn, v_extra, sn.nports,
                                             sn.port_names, v_echo)
                if len(notes) > VALIDATION_STRIP_LINES:
                    # Only the ones that are NOT a '✓' echo make this a
                    # warning; a long spec that is entirely fine must not
                    # badge the Log, the same rule _footer_strip_text counts by.
                    sev = (LOG_WARN if any(not n.startswith("✓") for n in notes)
                           else LOG_INFO)
                    self._append_result(f"  [{tc.id}] {tc.label}: spec notes",
                                        sev)
                    for note in notes:
                        self._append_result(f"      {note}")

            try:
                term = self._build_termination(tc, nports=sn.nports, sn=sn)
                n_mports = len(resolve_meas_ports(term, sn.nports))
            except Exception as e:
                tc.Z = None
                self._append_result(
                    f"  [{tc.id}] {tc.label}: ERROR {e}", LOG_ERROR)
                self._append_result(traceback.format_exc(), LOG_ERROR)
                continue

            # Mode 6 -- and ANY spec that defines more than one measurement
            # port -- produces a G x G Z matrix, not one curve; it gets its own
            # results block and expands into several plot curves.
            #
            # Routing on the measurement-port count rather than on the mode is
            # what stops Mode 5 from silently reporting only the first port.
            # compute_z returns Zmat[:, 0, 0] and warns about the rest, which
            # is a wrong number with no visible difference -- and once the two
            # modes share an editor, "I defined two probes here" has to mean
            # the same thing in both.  A single-measurement-port spec still
            # takes the compute_z path, so every pre-existing trace stays
            # bit-identical (golden regression).
            if tc.mode == 6 or n_mports > 1:
                if tc.mode != 6:
                    self._append_result(
                        f"    [{tc.id}] {n_mports} measurement ports defined -- "
                        "reporting the full coupling matrix (M, k), same as "
                        "Mode 6.", LOG_WARN)
                try:
                    cres = self._calculate_coupling_trace(
                        tc, sn, f_rlc_hz, term=term)
                except Exception as e:
                    tc.Z = None
                    self._append_result(
                        f"  [{tc.id}] {tc.label}: ERROR {e}", LOG_ERROR)
                    self._append_result(traceback.format_exc(), LOG_ERROR)
                    continue
                # The block's own marker snap comes from the axis its numbers
                # were read on: the composed one for a composition, keyed under
                # the file legend, and the home file's otherwise.
                snap_key = trace_file_legend(tc) if sn.composed else fe.label
                coupling_blocks.append(_snapshot_block(
                    tc, fe.label, cres,
                    freq=next((s for lbl, s in freq_snaps
                               if lbl == snap_key), None)))
                if do_fit:
                    fit_lines.append(_snapshot_fit(
                        tc,
                        f"  fit[{tc.id}]: skipped -- a band fit applies to one Z "
                        "curve, and a +/- coupling trace expands into several."))
                continue

            try:
                Z, warns = compute_z(sn.Y, sn.freqs, term)
            except Exception as e:
                self._append_result(
                    f"  [{tc.id}] {tc.label}: ERROR {e}", LOG_ERROR)
                self._append_result(traceback.format_exc(), LOG_ERROR)
                continue
            for w in warns:
                # Schur fallback / pathological-condition notes: the reduction
                # took a different route than usual and said so.
                self._append_result(f"    [{tc.id}] {w}", LOG_WARN)
            tc.Z = Z
            # The axis Z lives on, cached for _replot_from_cache.  None on a
            # single-file trace: the home file's sweep IS the axis, and storing
            # a second reference to it would be one more thing to keep in step.
            tc.net_freqs = sn.freqs if sn.composed else None
            tc.reference_checks = self._reference_checks(tc, sn, term, f_rlc_hz)
            res = extract_rlc_at_freq(sn.freqs, Z, f_rlc_hz)
            tc.rlc = res
            result_rows.append(_snapshot_row(tc, fe.label, res))

            fit_freqs = None
            fit_Z = None
            if do_fit:
                try:
                    model = self.fit_model_var.get()
                    if model == "auto":
                        which, fit = fit_auto(sn.freqs, Z, fmin_hz, fmax_hz)
                    elif model == "inductor":
                        which, fit = "inductor", fit_inductor(sn.freqs, Z, fmin_hz, fmax_hz)
                    else:
                        which, fit = "capacitor", fit_capacitor(sn.freqs, Z, fmin_hz, fmax_hz)
                    tc.fit_kind = which
                    tc.fit = fit
                    fit_freqs = sn.freqs[(sn.freqs >= fmin_hz)
                                         & (sn.freqs <= fmax_hz)]
                    if which == "inductor":
                        fit_Z = eval_inductor_model(fit, fit_freqs)
                        fit_lines.append(_snapshot_fit(
                            tc,
                            f"  fit[{tc.id} {which}]: "
                            f"L={format_si(fit.L_henry, 'H')}, "
                            f"R_dc={format_si(fit.R_dc_ohm, 'Ω')}, "
                            f"R_ac={fit.R_ac_ohm_per_sqrtHz:.3g}Ω/√Hz, "
                            f"Q@center={fit.Q_at_center:.3g}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}"))
                    else:
                        fit_Z = eval_capacitor_model(fit, fit_freqs)
                        srf_str = ("nan" if math.isnan(fit.SRF_hz)
                                   else format_si(fit.SRF_hz, 'Hz'))
                        fit_lines.append(_snapshot_fit(
                            tc,
                            f"  fit[{tc.id} {which}]: "
                            f"C={format_si(fit.C_farad, 'F')}, "
                            f"R_esr={format_si(fit.R_esr_ohm, 'Ω')}, "
                            f"L_esl={format_si(fit.L_esl_henry, 'H')}, "
                            f"SRF={srf_str}, "
                            f"RMSE={format_si(fit.rmse_ohm, 'Ω')}"))
                except Exception as e:
                    fit_lines.append(
                        _snapshot_fit(tc, f"  fit[{tc.id}] ERROR: {e}"))

            tc.fit_freqs = fit_freqs
            tc.fit_Z = fit_Z

        # Second pass: render the table, fit lines and coupling blocks.
        #
        # What CHANGED since the previous run is the real discriminator between
        # two pages -- twenty runs are all at 5 GHz and nobody remembers what
        # they were doing at 14:32 -- so it is computed here, while both sides
        # of the comparison exist, and stored rendered.
        prev = self._last_run
        sigs = run_signatures(self.traces)
        changed = (tuple(describe_run_change(prev.signatures, sigs))
                   if prev is not None else ())
        self._last_run = RunSnapshot(
            number=self._run_counter, when=datetime.now(),
            marker_freq_hz=f_rlc_hz, rows=tuple(result_rows),
            blocks=tuple(coupling_blocks), fits=tuple(fit_lines),
            signatures=sigs,
            prev_number=(prev.number if prev is not None else 0),
            changed=changed, freqs=tuple(freq_snaps))
        self._render_results(self._last_run)
        self._add_run_tab(self._last_run)

        # Curves are built in ONE place, from the cache, so that Calculate and
        # a visibility toggle cannot drift apart in what they draw.  A full
        # Calculate does not keep the cursors -- every number on the plot is
        # new -- but a single-trace recompute does: that is the fast iteration
        # loop, and throwing away the cursors the user is reading would undo
        # most of what makes it fast.
        self._replot_from_cache(keep_cursors=only is not None)
        self.plot.set_marker_freq(f_rlc_hz)
        if self.traces and not any(tc.enabled for tc in self.traces):
            # The table lists plotted traces only, so with everything hidden it
            # is empty too -- do not claim "the numbers above are still
            # current" when there are no numbers above.
            self._append_result(
                "  (every trace has 'Plot: this trace' unchecked -- the plot is "
                "empty on purpose, and so is the table; they were measured, so "
                "showing one again costs no Calculate)")

    def _replot_from_cache(self, keep_cursors: bool = True) -> None:
        """
        Rebuild the plot from each trace's cached Z / Zmat.

        This is what makes the visibility checkbox worth having: hiding a curve
        must not re-run the reduction, which on a 153-port package file is
        seconds of work to produce numerically identical results.  Colour,
        linestyle and the self/mutual choice are read fresh every time, so they
        follow the editor without a Calculate too.

        `keep_cursors` is on by default: a toggle is a change of view, and the
        V lines the user placed are part of what they were looking at.
        """
        plot_traces: list[PlotTrace] = []
        for tc in self.traces:
            if not tc.enabled:
                continue
            fe = self._file_by_label(tc.file_label)
            if fe is None:
                continue
            # The axis the cached Z was computed on.  For a composed trace that
            # is the composed one, which equals the home file's only when no
            # interpolation happened -- drawing against the home file's would
            # misplace every point in the sweep and look like a plausible curve.
            freqs = _trace_plot_freqs(tc, fe)
            if freqs is None:
                # Cached numbers from a composition, and no axis to draw them
                # against: the run that produced them is older than a reload of
                # one of its files.  Skipped rather than drawn on a guess.
                continue
            if tc.Zmat is not None and tc.mport_names:
                plot_traces.extend(
                    self._coupling_plot_traces(tc, freqs, tc.Zmat,
                                               tc.mport_names))
            elif tc.Z is not None:
                plot_traces.append(PlotTrace(
                    label=_plot_trace_label(tc),
                    freqs=freqs,
                    Z=tc.Z,
                    color_idx=tc.color_idx,
                    ls_idx=tc.ls_idx,
                    fit_freqs=tc.fit_freqs,
                    fit_Z=tc.fit_Z,
                ))
        self.plot.set_traces(plot_traces, keep_cursors=keep_cursors)

    def _calculate_coupling_trace(self, tc: TraceConfig, sn: "SolveNetwork",
                                  f_rlc_hz: float,
                                  term: TerminationSet | None = None) -> object:
        """
        Reduce to the G x G measurement-port Z matrix and extract the coupling
        result at the marker frequency.  Returns the CouplingResult.

        Caches Zmat / mport_names on the trace; the curves themselves are built
        later by _replot_from_cache, which is the single place that turns a
        computed trace into plot curves.

        Used by Mode 6 and by any Mode 5 spec that defines more than one
        measurement port.  `term` may be passed in when the caller has already
        built it, which is the normal path -- the caller has to build it anyway
        to count the measurement ports.
        """
        if term is None:
            term = self._build_termination(tc, nports=sn.nports, sn=sn)
        Zmat, names, warns = compute_z_matrix(sn.Y, sn.freqs, term)
        for w in warns:
            self._append_result(f"    [{tc.id}] {w}", LOG_WARN)
        if any("Rank-deficient" in w for w in warns):
            # INFO on purpose: this annotation exists to say the warning above
            # it is not a fault, so badging it again would contradict it.
            self._append_result(
                f"    [{tc.id}] (informational, not an error: a fully floating "
                "+/- structure is rank-deficient at every frequency and pinv "
                "handles it correctly)")
        if any("row and column of Z are NaN" in w for w in warns):
            self._append_result(
                f"    [{tc.id}] (this one IS an error in the port setup: the "
                "named measurement ports read nan because their probe current "
                "has nowhere to return. Give the port a '-' side, or add the "
                "ground ports the structure needs.)", LOG_ERROR)
        if any("cancelled to roundoff" in w for w in warns):
            self._append_result(
                f"    [{tc.id}] (also an error in the port setup: the numbers "
                "below are shown but they are roundoff noise, not a "
                "measurement. Fix the ports before reading them.)", LOG_ERROR)

        tc.Zmat = Zmat
        tc.mport_names = list(names)
        # Keep the scalar field populated with measurement port 1's self
        # impedance so anything expecting tc.Z keeps working. Zmat[:, 0, 0] is
        # a strided view, so copy it.
        tc.Z = np.ascontiguousarray(Zmat[:, 0, 0])
        tc.net_freqs = sn.freqs if sn.composed else None
        tc.reference_checks = self._reference_checks(tc, sn, term, f_rlc_hz)
        tc.rlc = extract_rlc_at_freq(sn.freqs, tc.Z, f_rlc_hz)
        cres = extract_coupling_at_freq(sn.freqs, Zmat, names, f_rlc_hz)
        tc.coupling = cres

        # The emptiness CONDITION of _coupling_plot_traces, not a call to it:
        # building the curves here just to test the list would recompute
        # _coupling_k_array over every frequency for every pair, and then throw
        # it away -- _replot_from_cache builds them for real a moment later.
        if not (tc.plot_self or (tc.plot_mutual and len(names) >= 2)):
            self._append_result(
                f"    [{tc.id}] both 'self' and 'mutual' are unchecked -- "
                "nothing plotted for this trace", LOG_WARN)
        return cres

    def _coupling_plot_traces(self, tc: TraceConfig, freqs: np.ndarray,
                              Zmat: np.ndarray, names: list) -> list:
        """
        Expand one mode-6 trace into plot curves: one self curve per
        measurement port and one mutual curve per unordered pair.  A mutual
        curve is just another complex Z array, so every subplot works on it --
        L(nH) then reads M in nH and C(pF) reads the coupling capacitance.
        """
        out: list = []
        G = len(names)
        n = 0
        if tc.plot_self:
            for g, nm in enumerate(names):
                out.append(_make_plot_trace(
                    label=_compose_curve_label(_plot_trace_label(tc), nm),
                    freqs=freqs,
                    Z=np.ascontiguousarray(Zmat[:, g, g]),
                    color_idx=(tc.color_idx + n) % len(COLORS),
                    ls_idx=tc.ls_idx % len(LINESTYLES),
                ))
                n += 1
        if tc.plot_mutual and G >= 2:
            for a in range(G):
                for b in range(a + 1, G):
                    out.append(_make_plot_trace(
                        label=_compose_curve_label(
                            _plot_trace_label(tc), f"{names[a]} x {names[b]}"),
                        freqs=freqs,
                        Z=np.ascontiguousarray(Zmat[:, a, b]),
                        color_idx=(tc.color_idx + n) % len(COLORS),
                        ls_idx=(tc.ls_idx + 1) % len(LINESTYLES),
                        aux={"k": _coupling_k_array(Zmat, freqs, a, b)},
                    ))
                    n += 1
        return out

    def _empty_run(self) -> RunSnapshot:
        """A run record with nothing in it, for a report built before any
        Calculate (freezing a trace restored from a session, say)."""
        return RunSnapshot(number=self._run_counter, when=datetime.now(),
                           marker_freq_hz=float("nan"))

    def _render_results(self, run: RunSnapshot) -> None:
        """
        Print one RUN's table, fit summaries and coupling blocks -- for the
        records that are ON THE PLOT.

        A hidden trace is filtered out here rather than at collection time, so
        `run.rows` still holds everything and the caller can decide whether the
        visibility is the one that was recorded (any past run) or the one that
        holds now (`with_visibility`, the current run).  Its numbers are not
        lost -- they stay cached on the trace, so showing it again needs no
        Calculate -- but they are not reported anywhere until it is shown,
        which is why the line under the table has to name it.

        Everything read here comes off the snapshot, never off a TraceConfig:
        the id, label and port descriptor beside a number have to be the ones
        that produced it.
        """
        for text, colors, sev in self._run_report_segments(run):
            if colors:
                self._append_swatched(text, colors)
            else:
                self._append_result(text, sev)

    def _run_report_segments(self, run: RunSnapshot) -> list:
        """
        One run's report as (text block, swatch colours, severity) segments.

        The Log and the run page print the SAME report, so they are built once
        here.  A second copy of this code would let the page the user reads and
        the log they scroll back through disagree about a run's contents, with
        nothing to tell them apart.

        THE VIEW IS READ LIVE OFF THE APP, exactly as the units mode is, and
        for the same reason: which of the three renderings is on screen is a
        RENDERING CHOICE, not a recorded fact, so freezing it onto the run
        would leave one page in a layout the switch had already left.  Both
        halves are the run pages' documented rule -- `_on_results_view_changed`
        repaints every page in place and creates no tab, because choosing a
        view measures nothing.
        """
        units = self.units_mode_var.get()
        view = self.results_view_var.get()
        shown_rows = [r for r in run.rows if r.enabled]
        shown_blocks = [b for b in run.blocks if b.enabled]
        hidden = [r for r in run.rows if not r.enabled]
        hidden += [b for b in run.blocks if not b.enabled]

        if view == VIEW_SUMMARY:
            segs = self._summary_segments(run, shown_rows, shown_blocks, units)
        elif view == VIEW_COMPARE:
            segs = self._compare_segments(run, shown_rows, shown_blocks, units)
        else:
            segs = self._detail_segments(run, shown_rows, shown_blocks, units)
        return segs + self._footer_segments(shown_rows, shown_blocks, hidden)

    def _detail_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                         units: str) -> list:
        """The full report: the results table, the fits, one block per trace.

        This is the view the tool has always had, minus the two paragraphs
        that were repeated verbatim under every block (see
        COUPLING_LEGEND_LINES and _footer_segments).
        """
        run_freq = run_freq_snap(run)
        segs: list = []
        if shown_rows:
            segs.append((_format_results_table(shown_rows, units, run_freq),
                         tuple(r.color_idx for r in shown_rows), LOG_INFO))
            for f in run.fits:
                if f.enabled:
                    # A fit that raised is reported on the same line as one
                    # that worked, so the severity has to come off the text.
                    segs.append((f.text, (),
                                 LOG_WARN if "ERROR" in f.text else LOG_INFO))
        if (isinstance(run_freq, FreqSnap) and not run_freq.agreed
                and (shown_rows or shown_blocks)):
            # Several files, several sweeps, several answers: no one line can
            # name the frequency, so each file names its own.  Built here
            # rather than at the Calculate call site because the run PAGE has
            # to carry it too -- _run_report_segments is the one builder.
            for lbl, snap in run.freqs:
                segs.append((f"  {lbl}: read at "
                             f"{marker_freq_text(snap, '{:.6g}')}", (),
                             LOG_WARN if snap.off_grid else LOG_INFO))
        for block in shown_blocks:
            segs.append(("", (), LOG_INFO))
            segs.append((_format_coupling_block(block, units), (), LOG_INFO))
        return segs

    def _run_heading(self, run: RunSnapshot, what: str) -> str:
        """'self impedance @ 5.55 GHz', with the frequency's provenance.

        The compact views drop the per-block `Z matrix @ <freq>` line, so this
        is where the frequency they were read at is stated -- a table of
        numbers with no frequency on it is the two-numbers-one-screen failure
        `tests/test_freq_label.py` exists about.
        """
        return f"  {what} @ {marker_freq_text(run_freq_snap(run), '{:.6g}')}"

    def _summary_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                          units: str) -> list:
        """One run as two tables: every self measurement, then every pair."""
        segs: list = []
        text, colors = _format_summary_self(shown_rows, shown_blocks, units)
        if text:
            segs.append((self._run_heading(run, "self impedance"), (),
                         LOG_INFO))
            segs.append((text, colors, LOG_INFO))
        # The fits belong to the self table and are already one line each.
        for f in run.fits:
            if f.enabled:
                segs.append((f.text, (),
                             LOG_WARN if "ERROR" in f.text else LOG_INFO))
        text, colors = _format_summary_coupling(shown_blocks, units)
        if text:
            segs.append(("", (), LOG_INFO))
            segs.append((self._run_heading(run, "coupling"), (), LOG_INFO))
            segs.append((text, colors, LOG_INFO))
        if not segs:
            segs.append(("  (nothing on the plot)", (), LOG_INFO))
        return segs

    def _compare_segments(self, run: RunSnapshot, shown_rows, shown_blocks,
                          units: str) -> list:
        """Traces as columns, with a delta at exactly two of them.

        FALLS BACK TO THE SUMMARY, NAMING THE REASON, rather than showing an
        empty pane: the view is chosen once and then stays chosen, so a run
        that cannot be compared must still print its numbers.  Same rule as
        the attribution split -- degrade, never refuse, and say which.
        """
        text, colors, refusal = _format_compare(shown_rows, shown_blocks,
                                                units)
        if refusal:
            return ([(f"  compare: {refusal}", (), LOG_INFO)]
                    + self._summary_segments(run, shown_rows, shown_blocks,
                                             units))
        return [(self._run_heading(run, "compare"), (), LOG_INFO),
                (text, colors, LOG_INFO)]

    def _footer_segments(self, shown_rows, shown_blocks, hidden) -> list:
        """
        The lines that qualify the whole run, once each, whatever the view.

        EVERY ONE OF THESE USED TO BE PER BLOCK OR PER RECORD, and two of them
        were the same sentence rendered twice.  Measured on the reported
        two-trace run: 3538 characters of report, of which the 272-column
        coupling legend and the 262-column reference-node verdict accounted for
        1068 -- 30% of the report was one of two paragraphs said twice, in a
        pane that is 144 columns wide at the default window size and does not
        wrap.  They are deduplicated here rather than shortened at the source
        because both of them are honest: what was wrong was the repetition.
        """
        segs: list = []
        # The legend belongs to the coupling blocks, so it is emitted only when
        # one was printed -- a run of nothing but mode-1 traces has no ind/cap
        # column and no M/L to qualify.  The results table keeps its own,
        # shorter legend line, which was never repeated.
        if shown_blocks:
            for line in COUPLING_LEGEND_LINES:
                segs.append((line, (), LOG_INFO))
        # WHERE DID THAT M COME FROM?  This is the pointer that matters: the
        # user is looking at "M = 2.16 pH" here, not at a menu bar, and the
        # "Show Ports needed five pointers before anyone found it" history is
        # what says an analysis window nobody can find is an analysis window
        # nobody uses.  Same idiom as the "(see Export CSV)" line inside the
        # block: one line, naming the route.
        #
        # It is emitted HERE and not inside `_format_coupling_block` so that it
        # is said once per RUN rather than once per block -- the same rule as
        # the legend above it and the hidden-traces line below: six coupling
        # traces do not need six copies of one sentence.  (It also keeps that
        # function's output free of anything the summary and compare views
        # would have to strip back out.)
        #
        # Gated on a PAIR existing, not merely on a block: a block with one
        # measurement port prints "(only one measurement port ...)" and
        # `attribution_refusal` turns that trace away by name.  Pointing at a
        # refusal is worse than not pointing at all.
        #
        # TWO PHRASES ARE LOAD-BEARING and both are pinned by
        # tests/test_attrib_gui_integration.py: 'where each M above comes from'
        # is how the line is found, and 'right-click menu' is the second route
        # -- a pointer that names one way in is a pointer that fails whenever
        # the reader is already holding the mouse over the Traces list.  The
        # tail was shortened around them to bring the whole sentence inside the
        # pane's measured 144 columns; it was 147, so the ROUTE was the part
        # falling off the right-hand edge.
        if any(b.cres.pairs for b in shown_blocks):
            segs.append((
                f"  where each M above comes from, and what would move it: "
                f"select the trace → Analyze → {ATTRIB_MENU_LABEL} "
                f"(or its right-click menu)", (), LOG_INFO))
        # R3-5: THE WELD, WHERE THE NUMBER IS READ.  A weld raises nothing and
        # makes no number look wrong -- measured in pkg_rlc_compose, the
        # package ground pad grounded / open / through 1 nH give
        # L_eff = 2.1454 nH, bit-identical, spread 0.000e+00 -- so it does not
        # change the number, it changes how the number must be READ.  A report
        # nobody opened is therefore the wrong place for it, and this is the
        # right one: directly under the table and the blocks it qualifies, in
        # the Log AND on every run page, because `_run_report_segments` is the
        # one builder of both.
        #
        # Read off the SNAPSHOT, frozen at Calculate time.  A page re-rendered
        # by a units switch must not pick up a verdict from a composition that
        # has been rebuilt since.
        #
        # IDENTICAL VERDICTS ARE SAID ONCE, NAMING EVERY TRACE THEY ARE ABOUT.
        # Two traces over the same two files get the same 262-column sentence,
        # and the reported run printed it twice for [1] and [4] -- 524 of 3538
        # characters, in a pane that shows 144 of them.  The id list is what
        # keeps it a statement about specific traces rather than a general
        # remark, so nothing is lost by saying it once: `[1][4] Reference-node
        # check: …`.  Grouped on the FULL verdict (strip, warn flag and detail
        # lines together), so two traces whose checks differ in any way keep
        # their own lines -- collapsing on the strip alone would put one
        # trace's id on another trace's detail paragraph.
        groups: dict = {}
        for rec in list(shown_rows) + list(shown_blocks):
            if not getattr(rec, "ref_strip", ""):
                continue
            key = (rec.ref_strip, bool(rec.ref_warn),
                   tuple(rec.ref_lines or ()))
            groups.setdefault(key, []).append(rec.id)
        for (strip, warn, detail), ids in groups.items():
            tag = "".join(f"[{i}]" for i in ids)
            segs.append((f"  {tag} {strip}", (),
                         LOG_WARN if warn else LOG_INFO))
            if warn:
                for line in detail:
                    segs.append((f"      {line}", (), LOG_WARN))
        if hidden:
            # Named, not silently dropped: Calculate still measured them, and
            # since they are in no other output either, this line is the only
            # place the report says where they went -- otherwise a trace is
            # simply missing from it.
            segs.append((
                "  hidden (measured, not plotted, not exported; show it to "
                "read or export it): "
                + ", ".join(f"[{r.id}] {_trunc_str(r.label, 18)}"
                            for r in hidden), (), LOG_INFO))
        return segs

    def _write_run_report(self, txt, run: RunSnapshot) -> None:
        """The same segments, written into a run page instead of the Log.

        No severity routing here: a run page is not the Log, and badging the
        Log for a line the user is looking at on another tab would be a lie.
        """
        for text, colors, _sev in self._run_report_segments(run):
            base = int(txt.index("end-1c").split(".")[0])
            txt.insert(tk.END, text + "\n")
            if colors:
                _tag_swatch_rows(txt, base, text, colors)

    def _on_results_view_changed(self) -> None:
        """Repaint the Log and every run page in the newly chosen view.

        SAME RULE AS THE UNITS SWITCH, and deliberately the same code path: the
        view is a rendering choice, not a recorded fact, so it repaints every
        page (leaving one page in the previous layout is the "one screen, two
        formattings, then a silent flip" failure that rule is written from) and
        it creates NO run tab, because choosing a view measures nothing.

        The Attribution window is NOT poked: it has tables of its own and no
        view selector, so unlike the units mode there is nothing there this
        choice can leave stale.
        """
        self._rerender_every_page(
            f"\n--- re-rendered with view={self.results_view_var.get()} ---")

    def _on_units_mode_changed(self) -> None:
        self._rerender_every_page(
            f"\n--- re-rendered with units={self.units_mode_var.get()} ---")
        # The ONE caller that passes rerender=True, and the reason is the same
        # one that makes this repaint every run page: the unit is a RENDERING
        # choice, not a recorded fact.  An Attribution window's tables are
        # formatted through the app's units_mode_var exactly as
        # `_run_report_segments` is, and there is no other way for it to hear
        # that the choice changed -- it would sit in the previous formatting
        # beside a Results pane that had already flipped.  Not the default: a
        # re-render redraws the sweep too, and on the editor's per-keystroke
        # path that would be a closed-form solve per character.
        if self._last_run is not None and (self._last_run.rows
                                           or self._last_run.blocks):
            refresh_attribution_windows(self, rerender=True)

    def _rerender_every_page(self, log_note: str) -> None:
        run = self._last_run
        if run is None or not (run.rows or run.blocks):
            return
        self._append_result(log_note)
        # The CURRENT run follows the visibility as it stands now -- `enabled`
        # gates the results table as well as the plot, so a row for a curve
        # that is no longer drawn would read as a duplicate of one that is.
        # Every other field is frozen.  A PAST run is rendered as recorded.
        self._last_run = run.with_visibility(self.traces)
        self._render_results(self._last_run)
        # EVERY run page is rewritten in place, and no tab is created: a units
        # switch measures nothing, so it is not a run.  (The Log is a
        # chronological log and does append, which is what it is for.)
        #
        # Every page, not just the newest, because the unit is a RENDERING
        # choice and not a recorded fact -- the run snapshots hold numbers, and
        # _run_report_segments reads units_mode_var live.  Repainting only the
        # newest left the older pages in the previous formatting until the next
        # Calculate repainted them anyway (it re-renders all pages so their
        # banners name the current run), so "a past run is rendered as
        # recorded" was never what the user saw: they saw one screen showing
        # two formattings, and then a silent flip.
        newest = self._newest_run_tab()
        if newest is not None and newest.run.number == self._last_run.number:
            newest.run = self._last_run
        self._render_all_run_tabs()

    def _trace_network(self, tc: TraceConfig) -> "SolveNetwork":
        """
        The network this trace is solved against -- one file, or several.

        A single-file trace returns the FileEntry's OWN arrays, not copies:
        `fe.Y` and `fe.ts.freqs` are the objects every pre-composition path
        used, so the reduction sees the same bytes and the golden regression is
        untouched.  Nothing about a one-file trace goes near pkg_rlc_compose.

        A composed trace is stacked by `pkg_rlc_compose.compose`, cached on the
        App and validated by FileEntry identity, and the HOME file is block 0
        with every port kept -- which is the property that makes a bare port
        number mean the home file (R3-2).

        `marker_hz` is deliberately NOT passed to `compose`.  It would make the
        composition refuse outright when the marker falls outside the common
        span, and the GUI already answers that question its own way: the
        marker is snapped onto the composed axis by `snap_to_grid`, which
        reports the distance and flags `off_grid` when the request was off the
        end of the sweep.  Refusing here would also key the cache on a value
        the user retypes constantly, which is exactly the value a cache must
        not depend on.
        """
        labels = trace_file_labels(tc)
        entries = [self._file_by_label(lbl) for lbl in labels]
        if any(fe is None for fe in entries) or not entries:
            raise ValueError(
                "file " + ", ".join(f"'{lbl}'" for lbl, fe in zip(labels, entries)
                                    if fe is None) + " is not loaded")
        if len(entries) == 1:
            fe = entries[0]
            return SolveNetwork(freqs=fe.ts.freqs, Y=fe.Y, nports=fe.ts.nports,
                                port_names=list(fe.ts.port_names))
        key = tuple(labels)
        hit = self._compose_cache.get(key)
        if hit is not None:
            cached_entries, net = hit
            if len(cached_entries) == len(entries) and all(
                    a is b for a, b in zip(cached_entries, entries)):
                return _composed_solve_network(net)
            # A file was reloaded under the same name: the label matches and
            # the arrays behind it are different objects, so the cached stack
            # is a stack of the PREVIOUS parse.  Identity is what catches that;
            # a label-only key would have kept serving it.
            self._compose_cache.pop(key, None)
        net = comp.compose([comp.ComposeInput(data=fe.ts, alias=default_alias(i))
                            for i, fe in enumerate(entries)])
        self._compose_cache[key] = (list(entries), net)
        return _composed_solve_network(net)

    def _trace_namespace(self, tc: TraceConfig):
        """
        (ComposedNetwork, home alias) for scoping this trace's port fields, or
        (None, "").  Namespace only -- see `_namespace_network`.

        Never raises: it is on the strips' path, where a raised error reaches
        no handler anyone controls.
        """
        if not trace_is_composed(tc):
            return None, ""
        try:
            entries = [self._file_by_label(lbl)
                       for lbl in trace_file_labels(tc)]
            if any(fe is None for fe in entries):
                return None, ""
            net = _namespace_network(entries)
            return net, (net.blocks[0].alias if net.blocks else "")
        except Exception:                                    # pragma: no cover
            return None, ""

    def _cached_trace_network(self, tc: TraceConfig) -> "SolveNetwork | None":
        """
        The composed network for this trace IF IT IS ALREADY BUILT, else None.

        NEVER BUILDS ONE, and that is a measured rule rather than caution.  The
        editor strips and the Ports & Roles refresh both run from
        `_apply_editor_strips`, i.e. from a Tk variable trace, i.e. once per
        keystroke.  Measured on this box with smooth synthetic data
        (`comp.compose` of two files, three runs each):

            16 + 60 ports, 401 points  ->  76 ports:  100 / 112 /  97 ms
            16 + 153 ports, 401 points -> 169 ports:  10780 / 10346 / 10521 ms
            16 + 300 ports, 101 points -> 316 ports:  6772 / 6833 / 6661 ms

        Ten seconds per character is not a slow strip, it is a frozen
        application -- and 153 ports is the SMALL end of what this tool is used
        on (its own docstring names a 153-port package).  So the strips read
        what Calculate has already paid for and fall back to the home file
        until then, which is honest: before a Calculate there is no composition
        to describe.

        Identity-checked exactly as `_trace_network` is, for the same reason: a
        file reloaded under the same name is a different set of arrays.
        """
        if not trace_is_composed(tc):
            return None
        labels = trace_file_labels(tc)
        hit = self._compose_cache.get(tuple(labels))
        if hit is None:
            return None
        cached_entries, net = hit
        entries = [self._file_by_label(lbl) for lbl in labels]
        if len(cached_entries) != len(entries) or any(
                a is not b for a, b in zip(cached_entries, entries)):
            return None
        return _composed_solve_network(net)

    def _build_termination(self, tc: TraceConfig,
                           nports: int | None = None,
                           sn: "SolveNetwork | None" = None) -> TerminationSet:
        """
        The trace's spec as a TerminationSet.

        `sn` is the network the spec is being read against.  For a single-file
        trace it is None or a plain one and NOTHING below changes -- the same
        builders get the same strings, which is what keeps every golden case
        and every saved session bit-identical.  For a composed one every port
        field is first resolved into the composed namespace: a bare number
        still means the home file (R3-2), a tagged one names the file it says,
        and a bare number past the home file's port count is REFUSED rather
        than quietly addressing the next file's ports.

        Each mode keeps its OWN builder.  Routing a composed mode-6 trace
        through the permissive rows path would silently allow the probe-and-
        ground overlap that `build_terminations_coupling` refuses, which is a
        rule of the mode and not of the number of files.
        """
        self._migrate_trace(tc)
        net = sn.net if sn is not None else None
        home = sn.home_alias if sn is not None else ""
        if tc.mode == 6:
            # nports lets the builder reject a port number the file does not
            # have (a one-digit typo in a '+/-' spec would otherwise silently
            # demote a differential probe to a ground-referenced one).
            mp_rows = (tc.mports if net is None
                       else _scope_mport_rows(tc.mports, net, home))
            gnd = (tc.gnd_ports if net is None
                   else _scope_port_field(tc.gnd_ports, net, home))
            return build_terminations_coupling(
                _collect_mports(tc, mp_rows), parse_port_range(gnd),
                nports=nports)
        if tc.mode == 5:
            # Through the rows, never through tc.custom_text: the tables are
            # the storage and the DSL text is derived from them.  nports lets
            # the builder reject a port the file does not have -- Mode 5 used
            # to pass none, so '3 / 5' on a 4-port file became a plausible
            # wrong number until compute_z_matrix's backstop caught it.
            if net is None:
                return build_terminations_rows(tc.mports, tc.conn_rows,
                                               tc.extra_lines, nports=nports)
            return build_terminations_rows(
                _scope_mport_rows(tc.mports, net, home),
                _scope_conn_rows(tc.conn_rows, net, home),
                _scope_dsl_text(tc.extra_lines, net, home), nports=nports)
        if net is None:
            a = parse_port_range(tc.port_a)
            b = parse_port_range(tc.port_b)
            g = parse_port_range(tc.gnd_ports)
            sp = parse_short_pairs(tc.short_pairs)
        else:
            a = parse_port_range(_scope_port_field(tc.port_a, net, home))
            b = parse_port_range(_scope_port_field(tc.port_b, net, home))
            g = parse_port_range(_scope_port_field(tc.gnd_ports, net, home))
            sp = parse_short_pairs(tc.short_pairs)
            # The ONE field that is not scoped: parse_short_pairs reads its
            # tokens with int(), so 'F2.3' there already fails with core's own
            # message -- but a BARE index past the home file would have gone
            # through as a global port.  See _check_bare_ports.
            _check_bare_ports([p for pair in sp for p in pair], net, home,
                              "Short Pairs")
        if tc.mode == 1:
            return build_terminations_mode1(a, g)
        if tc.mode == 2:
            return build_terminations_mode2(a, b, g)
        if tc.mode == 3:
            return build_terminations_mode3(a, b, g, sp)
        raise ValueError(f"Unknown mode: {tc.mode}")

    def _reference_checks(self, tc: TraceConfig, sn: "SolveNetwork",
                          term: TerminationSet, f_hz: float) -> list:
        """
        R3-5.  Is each file's ground network in the circuit at all?

        MANDATORY on every composed trace and there is deliberately no way to
        turn it off, the same rule the CLI is written to: a weld raises
        nothing and makes no number look wrong -- measured in pkg_rlc_compose,
        the package ground pad grounded / open / through 1 nH give
        L_eff = 2.1454 nH, bit-identical, spread 0.000e+00 -- so it changes how
        the number must be READ, and it has to arrive where the number is read.

        It costs two single-frequency solves per file.  It never propagates a
        failure: a check that could not run must not cost the measurement it
        was checking, so it degrades to a warning line and an empty list.
        """
        if not sn.composed:
            return []
        try:
            return comp.reference_check(sn.net, term, freq_hz=f_hz)
        except Exception as e:
            self._append_result(
                f"    [{tc.id}] the reference-node check could not run: {e} "
                f"-- the numbers below stand, but nothing has confirmed that "
                f"each file's ground network is in the circuit.", LOG_WARN)
            return []


    def _on_marker_drag(self, freq_hz: float) -> None:
        self.rlc_freq_var.set(f"{freq_hz/1e9:.4g}")

    def _on_help(self) -> None:
        HelpWindow(self)

    # ----------------------------------------------------------- Session I/O

    def _session_dict(self, base_dir: Optional[str]) -> dict:
        """Everything the user typed, ready for json.dump."""
        # Same reason Calculate flushes: a keystroke in the same event burst as
        # the click is still in the idle queue, and a saved config that is one
        # character behind what is on screen is worse than no config at all.
        self._flush_editor_sync()
        return session_to_dict(
            files=self.files,
            traces=self.traces,
            controls={
                "rlc_freq_ghz": self.rlc_freq_var.get(),
                "fit_fmin_ghz": self.fit_fmin_var.get(),
                "fit_fmax_ghz": self.fit_fmax_var.get(),
                "fit_model": self.fit_model_var.get(),
                "units_mode": self.units_mode_var.get(),
                "results_view": self.results_view_var.get(),
            },
            plot_state=self.plot.view_state(),
            base_dir=base_dir,
            attribution=self._attribution_state(),
        )

    def _attribution_state(self) -> dict:
        """
        What the open Attribution windows are reading, or {}.

        Wrapped, because this is reached from `_autosave_session` -- which runs
        inside WM_DELETE_WINDOW, where a raise is an application that cannot be
        closed -- and from Save Config, where the trade would be losing a port
        map that took ten minutes to type in order to report that an analysis
        window's state could not be serialised.  A bad value costs its own
        field, never the file: the same rule the loader is written to.
        """
        try:
            return attribution_session_state(attribution_windows(self))
        except Exception:
            return {}

    def _write_session(self, path: str, base_dir: Optional[str]) -> None:
        target = Path(path)
        data = self._session_dict(base_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False so a port named in Chinese stays readable in the
        # file; indent=2 so it diffs line by line in git.
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def _on_save_config(self) -> None:
        if not self.files and not self.traces:
            messagebox.showinfo(
                "Nothing to save",
                "There is no file and no trace to save yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save configuration",
            defaultextension=".json",
            initialfile="rlc_session.json",
            filetypes=SESSION_FILETYPES,
        )
        if not path:
            return
        try:
            self._write_session(path, str(Path(path).parent))
        except Exception as e:
            messagebox.showerror("Save error", f"{path}\n\n{e}")
            return
        self._append_result(
            f"Saved config ({len(self.files)} file(s), {len(self.traces)} "
            f"trace(s)): {path}")

    def _on_load_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Load configuration", filetypes=SESSION_FILETYPES)
        if path:
            self._load_session_file(path, "Loaded config")

    def _on_restore_last_session(self) -> None:
        path = autosave_path()
        if not path.is_file():
            messagebox.showinfo(
                "No last session",
                f"Nothing has been auto-saved yet.\n\nThe session is written "
                f"to\n{path}\nwhen the window is closed with at least one file "
                f"or trace open.")
            return
        self._load_session_file(str(path), "Restored last session")

    def _load_session_file(self, path: str, origin: str) -> bool:
        """
        Read, validate, confirm, apply.  Returns True when it was applied.

        Every failure is a dialog naming the file and what is wrong with it --
        same contract as the Touchstone reader, for the same reason: a JSON
        traceback does not tell the user whether their file is bad or the tool
        is.
        """
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as e:
            messagebox.showerror("Cannot read config", f"{path}\n\n{e}")
            return False
        except UnicodeDecodeError:
            messagebox.showerror(
                "Cannot read config",
                f"{path}\n\nThis is not a UTF-8 text file. A session file is "
                f"JSON written by File → Save Config.")
            return False
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "Cannot read config",
                f"{path}\n\nThis file is not valid JSON — line {e.lineno}, "
                f"column {e.colno}: {e.msg}.")
            return False
        try:
            sess = session_from_dict(
                data, os.path.dirname(os.path.abspath(path)))
        except SessionError as e:
            messagebox.showerror("Cannot read config", f"{path}\n\n{e}")
            return False

        # Ask BEFORE anything is torn down, and only when there is something to
        # lose.  Loading is a replace, not a merge: two sessions describing the
        # same file under the same label would fight over every trace binding.
        if (self.files or self.traces) and not messagebox.askyesno(
                "Replace current session?",
                f"Loading this config replaces the {len(self.files)} file(s) "
                f"and {len(self.traces)} trace(s) now open, and anything not "
                f"saved is lost.\n\nContinue?"):
            return False

        self._apply_session(sess, f"{origin}: {path}")
        return True

    def _apply_session(self, sess: LoadedSession, origin: str) -> None:
        # Cancel, don't flush: the queued edit belongs to a trace that is about
        # to be discarded.  _apply_editor_sync's identity check would decline it
        # anyway; running it just to be declined is a way for that check to rot.
        self._cancel_editor_sync()
        self.files = []
        self.traces = []
        self._trace_list_shown = []
        self._append_result(f"\n=== {origin} ===")
        for note in sess.warnings:
            # A dropped key or a coerced value: the file did not load as
            # written, which is exactly what must not pass unseen.
            self._append_result(f"  note: {note}", LOG_WARN)

        missing: list[tuple[str, str]] = []
        for label, path, found in sess.files:
            # `found` first, so a file that is simply not there never reaches
            # _load_one_file -- that one reports through a MODAL dialog, and a
            # session whose folder has moved would open one per file before the
            # user could read the single Results line that says the same thing.
            ts = self._load_one_file(path) if found else None
            if ts is None:
                missing.append((label, path))
                continue
            fe = FileEntry(ts)
            if fe.label != label:
                # Only reachable via a hand-edited file -- which is also the
                # only way to re-point a session at data that moved, since the
                # loader offers no relocate dialog.  Re-bind rather than leave
                # every trace reporting "file not loaded".
                for tc in sess.traces:
                    if tc.file_label == label:
                        tc.file_label = fe.label
                    # The extra files are bound by the same label and have to
                    # be re-pointed by the same rule: re-binding only the home
                    # file would leave a composed trace half resolved, naming
                    # a file that is loaded under another name.
                    tc.file_labels = [fe.label if lbl == label else lbl
                                      for lbl in tc.file_labels]
                self._append_result(
                    f"  '{label}' resolved to {fe.label}; its traces were "
                    f"re-bound to the new name", LOG_WARN)
            self.files.append(fe)

        self.traces = list(sess.traces)
        self._next_trace_id = max((tc.id for tc in self.traces), default=0) + 1

        controls = sess.controls
        for key, var in (("rlc_freq_ghz", self.rlc_freq_var),
                         ("fit_fmin_ghz", self.fit_fmin_var),
                         ("fit_fmax_ghz", self.fit_fmax_var),
                         ("fit_model", self.fit_model_var),
                         ("units_mode", self.units_mode_var),
                         ("results_view", self.results_view_var)):
            if key in controls:
                var.set(controls[key])

        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        self.plot.set_view_state(sess.plot)
        # keep_cursors=False: nothing is computed yet, so there is no curve for
        # a restored cursor to read.
        self._replot_from_cache(keep_cursors=False)
        if self.traces:
            self.traces_lb.selection_clear(0, tk.END)
            self.traces_lb.selection_set(0)
            self.traces_lb.activate(0)
            self._on_trace_selected()

        self._append_result(
            f"  {len(self.files)} file(s), {len(self.traces)} trace(s) "
            f"restored — press Calculate All & Plot for the numbers "
            f"(a config carries the setup, not the results).")
        # A frozen trace is a snapshot of RESULTS, and results are exactly what
        # a session file does not carry (_COMPUTED_TRACE_FIELDS -- and a numpy
        # array is not JSON anyway).  So it comes back with its spec, its
        # colour and its frozen flag, and with nothing to draw.
        #
        # It is reported here and marked '❄ no numbers' in the Traces list
        # rather than being dropped on save, because the SPEC is still worth
        # having: recomputing it reproduces the snapshot exactly whenever the
        # file has not changed, which is the normal case (what is being
        # compared is usually two port configurations of one file).  Dropping
        # it on save would throw that away silently, and silently is the part
        # that is not acceptable either way.
        thawed = [tc for tc in self.traces
                  if tc.frozen and tc.Z is None and tc.Zmat is None]
        if thawed:
            self._append_result(
                "  frozen snapshot(s) came back WITHOUT their numbers (a "
                "config carries the setup, never the results): "
                + ", ".join(f"[{tc.id}] {_trunc_str(tc.label, 18)}"
                            for tc in thawed), LOG_WARN)
            self._append_result(
                f"  Each is still frozen, so Calculate skips it and it draws "
                f"nothing. Right-click → {UNFREEZE_MENU_LABEL}, then "
                f"Calculate, to measure it again from the file as it is now — "
                f"which reproduces the snapshot only if the file has not "
                f"changed.")
        # The Attribution windows, in two halves.
        #
        # First: every window that is still OPEN has just had its subject
        # replaced wholesale -- the trace it holds is not in `self.traces` any
        # more, whatever the new session calls its traces.  Rule 11: a window
        # that holds a result cannot re-read its way out of that, it has to be
        # told, or it carries on offering [Recompute] on a trace that is gone.
        refresh_attribution_windows(self)
        refresh_files_windows(self)
        # Second: what the SAVED windows were reading.  Nothing is reopened --
        # `attribution_refusal` turns away a trace with no numbers, and a
        # freshly loaded session has none until Calculate has run, so an
        # auto-reopen could only produce one refusal dialog per entry before
        # the user had asked for anything.  The choices are kept so that
        # reopening from the menu lands on the pair that was being read, and
        # the notes say so out loud -- a restore that silently drops part of
        # what was saved is the failure mode this pane exists to prevent.
        for note in apply_attribution_session_state(self, sess.attribution):
            self._append_result(f"  {note}", LOG_WARN)

        for label, path in missing:
            self._append_result(
                f"  MISSING: '{label}' — looked for {path or '(no path)'}",
                LOG_WARN)
        if missing:
            self._append_result(
                "  Traces bound to a missing file are still listed and still "
                "editable; add the file and load the config again, or point "
                "the editor's File box at one that is loaded.")

    def _autosave_session(self) -> None:
        """
        Write the config to the user directory on the way out.

        Never raises and never opens a dialog: this runs while the window is
        closing, where the only thing a failure could achieve is to stop the
        application from exiting.  An EMPTY session is not written -- opening
        the tool, changing nothing and closing it must not erase what the
        previous run left behind.

        base_dir is None deliberately: this file never moves, so a path
        relative to it would say nothing the absolute path does not.
        """
        if not self.files and not self.traces:
            return
        try:
            self._write_session(str(autosave_path()), None)
        except Exception:
            pass

    def _announce_last_session(self) -> None:
        """
        Name what is on disk from last time, in one line, and stop there.

        Loading it would re-parse every Touchstone file in it, which on package
        exports is tens of seconds before the user has asked for anything --
        a tool that is busy at startup is a worse trade than one that waits to
        be told.  Runs during construction, so it must never raise.
        """
        try:
            path = autosave_path()
            if not path.is_file():
                return
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or data.get("format") != SESSION_FORMAT:
                return
            n_files = len(data.get("files") or [])
            n_traces = len(data.get("traces") or [])
            if not n_files and not n_traces:
                return
            self._append_result(
                f"Last session: {n_files} file(s), {n_traces} trace(s), saved "
                f"{data.get('saved_utc') or 'at an unknown time'}.")
            self._append_result(
                "  File → Restore Last Session to load it.")
        except Exception:
            pass

    def _on_close(self) -> None:
        self._flush_editor_sync()
        self._autosave_session()
        self.destroy()

    # --------------------------------------------------------------- CSV

    def _on_export_csv(self) -> None:
        """
        Write the shown traces out.  Hiding a trace takes it off the plot, out
        of the results table and out of here: the checkbox selects what the
        session is about, and a file carrying rows the user took off the screen
        is the same duplicate they hid, one step further from where it can be
        noticed.  Nothing is destroyed -- the numbers stay cached on the trace,
        so showing it again and exporting once more costs no Calculate.
        """
        computed = [tc for tc in self.traces
                    if tc.Z is not None or tc.Zmat is not None]
        traces_with_data = [tc for tc in computed if tc.enabled]
        if not traces_with_data:
            if computed:
                # Distinguish the two empty cases: "you have not calculated" is
                # wrong and unactionable advice when the numbers exist and are
                # merely hidden.
                messagebox.showinfo(
                    "Nothing shown",
                    "Every calculated trace is hidden, and hidden traces are "
                    "not exported.\n\nShow at least one (Show/Hide, or the "
                    "space bar on the Traces list) and export again -- it "
                    "needs no recalculation.")
            else:
                messagebox.showinfo("No data", "Run Calculate first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        run = self._last_run
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                for tc in traces_with_data:
                    fe = self._file_by_label(tc.file_label)
                    if fe is None:
                        continue
                    # The axis these numbers were computed on -- the composed
                    # one for a composition.  None means a composed trace whose
                    # axis is not stored, and it is SKIPPED rather than written
                    # against the home file's sweep: the lengths differ, so
                    # that would either raise halfway through a file the user
                    # is waiting on or, worse, line every value up against the
                    # wrong frequency.
                    freqs = _trace_plot_freqs(tc, fe)
                    if freqs is None:
                        self._append_result(
                            f"  [{tc.id}] {tc.label}: not exported -- its "
                            f"numbers came from a composition whose frequency "
                            f"axis is no longer available. Calculate again.",
                            LOG_WARN)
                        continue
                    fh.write(f"# Trace: {tc.label}\n")
                    # No 'Plotted: no' marker any more -- every trace in the
                    # file is one that was on the plot, so a CSV and a
                    # screenshot of the same session carry the same traces.
                    # One file names itself exactly as it always has; a
                    # composed trace names all of them with their tags, because
                    # a CSV headed '# File: die.s6p' whose numbers came from
                    # the die AND the package is a false claim in the one line
                    # a spreadsheet keeps.
                    if trace_is_composed(tc):
                        fh.write(f"# Files: {trace_file_legend(tc)}, "
                                 f"Mode: {tc.mode_name()}\n")
                    else:
                        fh.write(f"# File: {fe.label}, "
                                 f"Mode: {tc.mode_name()}\n")
                    # WHICH RUN this is.  Export writes the CURRENT cached
                    # state, which is the newest run -- not whatever page the
                    # user happens to be reading -- so the file has to say so
                    # in its own words, the same way the older pages do.
                    #
                    # Except for a FROZEN trace, whose numbers came from an
                    # EARLIER run and cannot be recomputed (Calculate skips
                    # it): heading it with the newest run number says the
                    # opposite of the truth for exactly the trace type that
                    # exists to be a baseline.  A before/after CSV -- the only
                    # reason two such traces are in one file -- labelled both
                    # snapshots as belonging to the same run.
                    if tc.frozen:
                        fh.write(f"# Run: frozen snapshot taken at "
                                 f"{_freeze_stamp_of(tc.label)}, numbers from "
                                 f"an earlier run\n")
                    elif run is not None:
                        # The marker stays the REQUESTED frequency here, and
                        # deliberately: this line is the run's identity -- the
                        # number that was in the entry box -- and the rows
                        # below it are the FULL sweep, so nothing in this file
                        # was snapped to anything.  What the snap does change
                        # is the results pane, so where it moved gets its own
                        # key line rather than a parenthetical buried inside a
                        # value a script may be reading.
                        fh.write(f"# Run: #{run.number} "
                                 f"{_run_marker_text(run.marker_freq_hz)}, "
                                 f"{run.when.strftime('%H:%M:%S')}\n")
                        # Keyed by the composition's legend for a composed
                        # trace, because that is the key `_on_calculate` filed
                        # its snap under -- the marker landed on the composed
                        # axis, not on the home file's.
                        snap = run_file_freq(
                            run, trace_file_legend(tc) if trace_is_composed(tc)
                            else fe.label)
                        if (isinstance(snap, FreqSnap)
                                and not (snap.exact and snap.agreed)):
                            fh.write(
                                f"# Marker: the reported R/L/C/Q/M/k were read "
                                f"at {marker_freq_text(snap, '{:.6g}')}\n")
                    # Gate on the DATA, not the mode -- _on_calculate routes on
                    # the measurement-port count, so a Mode 5 spec with two
                    # probes has a full Zmat too.  Gating on `mode == 6` used to
                    # export that trace's scalar Zmat[:, 0, 0] table instead:
                    # well-formed, headed '# Mode: Custom', and missing every
                    # mutual term, every M and every k.
                    if tc.Zmat is not None:
                        _write_coupling_csv(fh, w, tc, freqs)
                        fh.write("\n")
                        continue
                    w.writerow(["Freq_GHz", "Re_Z", "Im_Z", "abs_Z",
                                "R_mOhm", "L_nH", "C_pF", "Q"])
                    omega = 2 * np.pi * freqs
                    for k in range(len(freqs)):
                        z = tc.Z[k]
                        f = freqs[k]
                        r = z.real
                        im = z.imag
                        L = im / omega[k] * 1e9 if omega[k] != 0.0 else float("nan")
                        C = (-1.0 / (omega[k] * im) * 1e12) if (omega[k] != 0.0 and im != 0.0) else float("nan")
                        Q = im / r if r != 0.0 else float("nan")
                        w.writerow([f"{f/1e9:.6g}",
                                    f"{r:.6e}", f"{im:.6e}", f"{abs(z):.6e}",
                                    f"{r*1000:.6e}", f"{L:.6e}",
                                    f"{C:.6e}", f"{Q:.6e}"])
                    fh.write("\n")
            which = "" if run is None else f" (run #{run.number})"
            self._append_result(f"Exported CSV{which}: {path}")
        except Exception as e:
            messagebox.showerror("Export error", str(e))

    # --------------------------------------------------------------- Misc

    def _refresh_file_list(self) -> None:
        self.files_lb.delete(0, tk.END)
        for fe in self.files:
            self.files_lb.insert(tk.END, fe.info_str())

    def _refresh_trace_list(self) -> None:
        """
        Re-render the trace list, but only when it would actually look
        different.

        Auto-apply calls this on every keystroke, and rebuilding a Listbox
        resets its scroll position -- so a user editing trace 9 of 12 would be
        yanked back to the top on each character.  Comparing the rendered
        strings costs nothing and is what makes the live list usable.
        (Programmatic delete / insert / selection_set do NOT fire
        <<ListboxSelect>>, verified on Tk 8.6, so this cannot re-enter
        _on_trace_selected and reload the editor mid-typing.)
        """
        lines = [tc.info_str() for tc in self.traces]
        # The cache key carries the COLOUR as well as the text.  info_str() has
        # no colour in it -- deliberately, the ☑/☐ prefix is the only state it
        # renders -- so picking a new palette slot leaves `lines` byte-identical
        # and the early return would keep the old foreground on screen forever,
        # with the plot already redrawn in the new one.
        key = [(ln, tc.color_idx) for ln, tc in zip(lines, self.traces)]
        if key == self._trace_list_shown:
            return
        self._trace_list_shown = key
        sel = self._sel_idx(self.traces_lb)
        self.traces_lb.delete(0, tk.END)
        for i, (tc, line) in enumerate(zip(self.traces, lines)):
            self.traces_lb.insert(tk.END, line)
            # itemconfig does not survive delete(), so BOTH foregrounds are
            # re-applied here every time rather than at the point of the
            # toggle.  The colour is the only thing tying a name in this list
            # to a curve on the plot -- without it four traces are four
            # identical lines of black text and the reader has to open the
            # editor on each one to find out which curve is which.  A hidden
            # trace keeps the grey: it has no curve to be tied to, and grey
            # is the state, not the style.
            #
            # A mode-6 trace expands into several curves taking consecutive
            # palette slots (_coupling_plot_traces), so this is its FIRST
            # colour -- the same one the style preview shows.
            self.traces_lb.itemconfig(
                i, foreground=("#909090" if not tc.enabled
                               else COLORS[tc.color_idx % len(COLORS)]))
        if sel is not None and sel < len(self.traces):
            self.traces_lb.selection_set(sel)

    def _refresh_file_combobox(self) -> None:
        self.ed_file_cbo["values"] = [fe.label for fe in self.files]

    def _file_by_label(self, label: str) -> Optional[FileEntry]:
        for fe in self.files:
            if fe.label == label:
                return fe
        return None

    def _sel_idx(self, lb: tk.Listbox) -> Optional[int]:
        sel = lb.curselection()
        if not sel:
            return None
        return int(sel[0])

    # ------------------------------------------------- the Results notebook

    def _log_selected(self) -> bool:
        """True when the Log tab is the one on screen."""
        try:
            return self.results_nb.select() == str(self._log_tab)
        except Exception:                               # pragma: no cover
            return False

    def _render_log_badge(self) -> None:
        """Repaint the Log tab's label. Never raises -- it runs from a trace."""
        try:
            self.results_nb.tab(self._log_tab,
                                text=log_tab_label(self._log_unseen))
        except Exception:                               # pragma: no cover
            pass

    def _on_results_tab_changed(self, _event=None) -> None:
        """
        The badge is cleared by LOOKING at the Log, not by any other action.

        Selecting some other tab is the user (or a later automatic switch)
        deliberately moving on, which is what releases the claim an ERROR line
        put on the pane.
        """
        if self._log_selected():
            self._log_unseen = 0
            self._render_log_badge()
        else:
            self._log_forced = False
        # A run tab that is now on screen has been read, so its "!" goes.
        rt = self._selected_run_tab()
        if rt is not None and rt.unseen:
            rt.unseen = False
            self._render_run_tab_label(rt)
        self._refresh_keep_button()

    def _select_log_tab(self) -> None:
        """
        Bring the Log to the front and keep it there.

        The flag is set AFTER select() so it cannot be cleared by the
        <<NotebookTabChanged>> that select() generates, whose delivery order
        relative to this line is not something to depend on.
        """
        try:
            self.results_nb.select(self._log_tab)
        except Exception:                               # pragma: no cover
            return
        self._log_forced = True

    def _select_results_tab(self, tab) -> bool:
        """
        Switch the Results notebook to `tab` -- unless an error owns it.

        This is the polite switch, and it is the one an automatic
        "show the run that just finished" must use: an ERROR line already
        pulled the Log to the front and moving off it would hide the only
        explanation of why the numbers are missing.  Returns True when the
        switch happened.
        """
        if self._log_forced:
            return False
        try:
            self.results_nb.select(tab)
        except Exception:                               # pragma: no cover
            return False
        return True

    # ------------------------------------------------------- run history tabs

    def _kept_run_tabs(self) -> list[RunTab]:
        return [rt for rt in self._run_tabs if rt.kept]

    def _auto_run_tabs(self) -> list[RunTab]:
        """The auto ring, newest first.  This is the ONLY set Calculate touches."""
        return [rt for rt in self._run_tabs if not rt.kept]

    def _kept_cap(self) -> int:
        """
        How many runs may be kept at once.

        Total budget minus the auto ring, so the two disjoint sets together can
        never exceed the tab count the strip was measured to stay readable at.
        """
        return max(1, self._run_tabs_max - self._run_auto_max)

    def _selected_run_tab(self) -> Optional[RunTab]:
        try:
            sel = self.results_nb.select()
        except Exception:                               # pragma: no cover
            return None
        for rt in self._run_tabs:
            if str(rt.frame) == sel:
                return rt
        return None

    def _current_run_number(self) -> int:
        """
        The run the PLOT and Export CSV are showing.

        Deliberately not "the highest number still on a tab": closing the
        newest page does not un-plot its curves, and a banner derived from the
        surviving tabs would then quietly promote an older page to "current"
        and stop warning about exactly the disagreement it exists for.
        """
        return self._last_run.number if self._last_run is not None else 0

    def _newest_run_tab(self) -> Optional[RunTab]:
        """The tab holding the run the plot and Export CSV are showing, if it
        still exists -- the user may have closed it."""
        for rt in self._run_tabs:
            if rt.run.number == self._current_run_number():
                return rt
        return None

    def _make_results_text(self, parent):
        """
        A results page: the same ScrolledText the Log has always been.

        height=10 on every one of them, so the notebook's requested height is
        the same whichever page is on screen and the vertical sash cannot creep
        as runs accumulate.
        """
        txt = ScrolledText(parent, height=10, wrap=tk.NONE,
                           font=("Consolas", 9))
        txt.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        txt.tag_configure("flag", foreground="#b04000")
        txt.tag_configure("stale", foreground="#b04000")
        for i, c in enumerate(COLORS):
            txt.tag_configure(f"c{i}", foreground=c)
        return txt

    def _new_run_tab(self, run: RunSnapshot) -> RunTab:
        """Add a page for `run` at the front of the run tabs and fill it in."""
        frame = ttk.Frame(self.results_nb)
        txt = self._make_results_text(frame)
        rt = RunTab(run=run, frame=frame, text=txt)
        # Newest first: index 1 is straight after the Log.  ttk's insert()
        # refuses a position past the last existing tab ("Slave index 1 out of
        # bounds"), so the very first run page is an add().
        if len(self.results_nb.tabs()) > 1:
            self.results_nb.insert(1, frame, text="")
        else:
            self.results_nb.add(frame, text="")
        self._run_tabs.insert(0, rt)
        self._render_run_tab_label(rt)
        return rt

    def _reader_is_at_the_newest_run(self) -> bool:
        """
        True when switching to the run that just finished is what the reader
        was going to do anyway.

        The Log counts: it is where the user watches a run happen.  A tab they
        deliberately kept does not -- yanking them off it is the opposite of
        what keeping means, and Calculate is pressed constantly in the
        edit/compute/read loop.
        """
        if self._log_selected():
            return True
        rt = self._selected_run_tab()
        if rt is None:                                  # pragma: no cover
            return True
        if rt.kept:
            # The natural gesture is to press Keep on the page you are looking
            # at, which is by definition the newest -- so without this the very
            # next Calculate yanked the reader off the page they had just
            # deliberately kept, which is what the docstring above says must
            # not happen.  Measured: Calculate -> land on '#2' -> Keep ->
            # Calculate -> selected '#3'.  The page they stay on is marked
            # unseen instead, which is the documented fallback.
            return False
        # Against the YOUNGEST PAGE ON SCREEN, not _current_run_number(): this
        # is called from _add_run_tab, by which point _last_run is already the
        # run being added, so "am I at the newest?" would answer itself with a
        # flat no and the switch would never happen.  It is also the right
        # frame of reference after the current run's page has been closed --
        # the reader is then at the front of what is left.
        return rt.run.number == max(t.run.number for t in self._run_tabs)

    def _add_run_tab(self, run: RunSnapshot) -> RunTab:
        """
        Give the finished run a page, trim the auto ring, and switch to it --
        CONDITIONALLY.

        The decision is taken BEFORE the new page exists, or "am I at the
        newest?" answers itself.  When the switch does not happen the page is
        marked unseen instead, so nothing arrives silently.  An ERROR has
        already claimed the pane by this point and _select_results_tab declines
        to move off it, which is how "an error still wins" holds without a
        second rule for it.
        """
        at_newest = self._reader_is_at_the_newest_run()
        rt = self._new_run_tab(run)
        self._evict_run_tabs()
        # Every OTHER page's "not this page" banner is relative to this run.
        self._render_all_run_tabs()
        rt.unseen = not at_newest
        self._render_run_tab_label(rt)
        if at_newest and not self._select_results_tab(rt.frame):
            rt.unseen = True
            self._render_run_tab_label(rt)
        self._refresh_keep_button()
        return rt

    def _destroy_run_tab(self, rt: RunTab) -> None:
        """
        The ONE teardown.  forget() THEN destroy(), in that order.

        Measured: forget() alone does not destroy the child -- 300 runs at a
        limit of 10 left 290 orphan widgets and +21.5 MB, growing linearly.
        tests/test_run_history.py asserts len(nb.winfo_children()) ==
        len(nb.tabs()) after a churn loop, which is the only honest form of
        that check (the working set does not drop even on correct teardown, so
        an RSS assertion would be measuring the allocator).
        """
        try:
            self.results_nb.forget(rt.frame)
        except Exception:                               # pragma: no cover
            pass
        try:
            rt.frame.destroy()
        except Exception:                               # pragma: no cover
            pass
        if rt in self._run_tabs:
            self._run_tabs.remove(rt)

    def _evict_run_tabs(self) -> None:
        """
        Trim the AUTO RING to its size, oldest first.  Nothing else is touched.

        A kept run is not a candidate -- that is what keeping means -- and
        neither is the tab that is ON SCREEN: evicting what the user is reading
        raises no error at all, Tk silently selects a neighbour, which is worse
        than an error.  Nor is the page for the CURRENT run, which is the one
        the plot and Export CSV are showing: at an auto ring of 1, with the
        reader parked on the older page, the oldest-first scan would otherwise
        skip the page they are on and take the run that just finished.

        So the ring is allowed to sit one over its size while a page is
        protected; the next Calculate that finds the reader elsewhere trims it.
        """
        try:
            sel = self.results_nb.select()
        except Exception:                               # pragma: no cover
            sel = ""
        current = self._current_run_number()
        autos = self._auto_run_tabs()
        while len(autos) > self._run_auto_max:
            victim = None
            for rt in reversed(autos):          # oldest first
                if str(rt.frame) != sel and rt.run.number != current:
                    victim = rt
                    break
            if victim is None:
                break
            autos.remove(victim)
            self._destroy_run_tab(victim)

    def _render_run_tab_label(self, rt: RunTab) -> None:
        try:
            self.results_nb.tab(rt.frame,
                                text=run_tab_label(rt.run.number, rt.run.when,
                                                   rt.kept, rt.unseen))
        except Exception:                               # pragma: no cover
            pass

    def _render_run_tab(self, rt: RunTab) -> None:
        """
        (Re)write one run page from its record.  In place -- never appended.

        Three header lines, then exactly the report _render_results prints to
        the Log.  Line 3 is mandatory on every page but the newest: without it
        three surfaces on one screen disagree with nothing to explain it.
        """
        current = self._current_run_number()
        is_newest = current == 0 or rt.run.number == current
        txt = rt.text
        try:
            # Line 3 names the NEWEST run, so every other page is rewritten on
            # every run -- and a reader scrolled halfway down an old page must
            # not be thrown back to the top by that.  Empty means "first draw",
            # where the top is where they want to be.
            had_text = txt.index("end-1c") != "1.0"
            where = txt.yview()[0]
            txt.delete("1.0", tk.END)
        except Exception:                               # pragma: no cover
            return
        head = [run_headline(rt.run)]
        line2 = run_change_line(rt.run.prev_number, rt.run.changed)
        if line2:
            head.append(line2)
        if not is_newest:
            head.append(run_stale_banner(current))
        for line in head:
            txt.insert(tk.END, line + "\n")
        if not is_newest:
            ln = len(head)
            txt.tag_add("stale", f"{ln}.0", f"{ln}.end")
        txt.insert(tk.END, "\n")
        self._write_run_report(txt, rt.run)
        if had_text:
            txt.yview_moveto(where)
        else:
            txt.see("1.0")

    def _render_all_run_tabs(self) -> None:
        """Re-render every page: the 'not this page' banner is relative to the
        newest run, so a new run changes what every OTHER page has to say."""
        for rt in list(self._run_tabs):
            self._render_run_tab(rt)
            self._render_run_tab_label(rt)

    def _refresh_keep_button(self) -> None:
        rt = self._selected_run_tab()
        kept = len(self._kept_run_tabs())
        cap = self._kept_cap()
        if rt is None:
            state = "none"
        elif rt.kept:
            state = "kept"
        elif kept >= cap:
            state = "full"
        else:
            state = "free"
        try:
            self._keep_btn.configure(text=keep_button_label(kept, cap, state))
            if state == "free":
                self._keep_btn.state(["!disabled"])
            else:
                self._keep_btn.state(["disabled"])
            # The label just changed WIDTH -- 'Keep run' to 'Keep (5/5) — full'
            # is the whole point of it -- and a ReflowRow re-lays only from
            # add() and from its own <Configure>, neither of which a child's
            # new text fires.  Without this the strip goes on forcing the old
            # width and the button is CLIPPED with no ellipsis, which is
            # exactly the state the long-label measurement was taken from.
            self._results_header.refresh()
        except Exception:                               # pragma: no cover
            pass

    def _keep_run_tab(self, rt: RunTab) -> bool:
        """
        Move one page out of the auto ring and into the kept set.

        The cap bites HERE, on the action the user took -- and by then the
        button is already disabled and already says why, so this is the
        backstop, not the message.
        """
        if rt.kept:
            return False
        if len(self._kept_run_tabs()) >= self._kept_cap():
            return False
        rt.kept = True
        self._render_run_tab_label(rt)
        self._refresh_keep_button()
        return True

    def _on_keep_run(self) -> None:
        rt = self._selected_run_tab()
        if rt is None:
            return
        if self._keep_run_tab(rt):
            self._append_result(
                f"  Keeping run #{rt.run.number}: Calculate will not evict it "
                f"({len(self._kept_run_tabs())}/{self._kept_cap()} kept).")

    # -- the tab strip's right-click menu

    def _run_tab_at(self, x: int, y: int) -> Optional[RunTab]:
        try:
            idx = self.results_nb.index(f"@{x},{y}")
        except Exception:
            return None
        try:
            name = self.results_nb.tabs()[idx]
        except Exception:                               # pragma: no cover
            return None
        for rt in self._run_tabs:
            if str(rt.frame) == name:
                return rt
        return None

    def _on_run_tab_context_menu(self, event) -> None:
        rt = self._run_tab_at(event.x, event.y)
        if rt is None:
            # The Log, or the empty strip to the right of the last tab.  The
            # Log cannot be kept and cannot be closed, so there is no menu.
            return
        self._sync_run_tab_menu(rt)
        try:
            self._run_tab_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._run_tab_menu.grab_release()

    def _sync_run_tab_menu(self, rt: "RunTab") -> None:
        """Point the tab menu at one page and label its entries for it.

        Split out of the popup handler so it can be driven without an event --
        same shape as _sync_trace_menu, and the same reason.
        """
        self._run_tab_menu_target = rt
        kept = len(self._kept_run_tabs())
        cap = self._kept_cap()
        can_keep = (not rt.kept) and kept < cap
        if rt.kept:
            state = "kept"
        elif can_keep:
            state = "free"
        else:
            state = "full"
        # long=True: a menu entry is not width-bound, so this is where the
        # sentence the button cannot afford at 150% DPI actually lives.  The
        # button is what sends the user here, so it has to be here.
        self._run_tab_menu.entryconfigure(
            0, state=(tk.NORMAL if can_keep else tk.DISABLED),
            label=keep_button_label(kept, cap, state, long=True))
        self._run_tab_menu.entryconfigure(
            2, state=(tk.NORMAL if len(self._run_tabs) > 1 else tk.DISABLED))

    def _on_menu_keep_run(self) -> None:
        rt = self._run_tab_menu_target
        if rt is not None and self._keep_run_tab(rt):
            self._append_result(
                f"  Keeping run #{rt.run.number}: Calculate will not evict it "
                f"({len(self._kept_run_tabs())}/{self._kept_cap()} kept).")

    def _on_menu_close_run(self) -> None:
        rt = self._run_tab_menu_target
        if rt is None:
            return
        self._run_tab_menu_target = None
        self._destroy_run_tab(rt)
        self._render_all_run_tabs()
        self._refresh_keep_button()

    def _on_menu_close_other_runs(self) -> None:
        """
        Close every other run page -- except the kept ones.

        "A kept run is destroyed only by Close THIS run" is the whole of the
        rule, and a bulk command that quietly broke it is exactly the surprise
        keeping exists to prevent.  The menu entry says so.
        """
        rt = self._run_tab_menu_target
        if rt is None:
            return
        for other in list(self._run_tabs):
            if other is not rt and not other.kept:
                self._destroy_run_tab(other)
        self._render_all_run_tabs()
        self._refresh_keep_button()

    # -- the Runs menubutton

    def _rebuild_runs_menu(self) -> None:
        m = self._runs_menu
        m.delete(0, tk.END)
        if not self._run_tabs:
            m.add_command(label="(no runs yet — press Calculate)",
                          state=tk.DISABLED)
        else:
            for rt in self._run_tabs:
                mark = RUN_KEPT_GLYPH if rt.kept else RUN_OPEN_GLYPH
                m.add_command(
                    label=f"{mark} {run_headline(rt.run)}",
                    command=lambda t=rt: self._select_results_tab(t.frame))
        m.add_separator()
        auto = tk.Menu(m, tearoff=0)
        for n in range(1, RUN_AUTO_MAX_UI + 1):
            auto.add_radiobutton(
                label=str(n), value=n, variable=self._run_auto_var,
                command=self._on_run_caps_changed)
        m.add_cascade(label="Auto runs kept (evicted oldest first)", menu=auto)
        total = tk.Menu(m, tearoff=0)
        for n in range(RUN_TABS_MIN, RUN_TABS_HARD_CAP + 1):
            total.add_radiobutton(
                label=str(n), value=n, variable=self._run_tabs_var,
                command=self._on_run_caps_changed)
        m.add_cascade(label="Max run tabs (auto + kept)", menu=total)

    def _on_run_caps_changed(self) -> None:
        """
        Apply the two caps from the Runs menu.

        The auto ring is clamped to leave at least one kept slot, so the kept
        cap can never reach zero and the Keep button can never be permanently
        disabled with nothing to close.
        """
        try:
            total = int(self._run_tabs_var.get())
            auto = int(self._run_auto_var.get())
        except Exception:                               # pragma: no cover
            return
        self._run_tabs_max = max(RUN_TABS_MIN,
                                 min(total, RUN_TABS_HARD_CAP))
        self._run_auto_max = max(1, min(auto, self._run_tabs_max - 1))
        self._run_auto_var.set(self._run_auto_max)
        self._run_tabs_var.set(self._run_tabs_max)
        self._evict_run_tabs()
        self._render_all_run_tabs()
        self._refresh_keep_button()

    def _append_result(self, text: str, severity: str = LOG_INFO) -> None:
        """
        Write one line to the Log.

        `severity` defaults to LOG_INFO, which is exactly what every call site
        did before the Results pane became a notebook.  LOG_WARN counts
        towards the Log tab's badge while the Log is not on screen; LOG_ERROR
        brings the Log to the front instead, because an error the user never
        sees is worse than a tab switch they did not ask for.
        """
        self.results_text.insert(tk.END, text + "\n")
        self.results_text.see(tk.END)
        if severity == LOG_ERROR:
            # No badge: the log is now on screen, so nothing about it is unseen.
            self._select_log_tab()
        elif severity == LOG_WARN and not self._log_selected():
            self._log_unseen += 1
            self._render_log_badge()

    def _append_swatched(self, text: str, color_idxs: Sequence[int]) -> None:
        """
        Append `text` and colour the leading swatch of each of its data rows.

        Rows are found by their RESULTS_SWATCH prefix and consumed in order,
        so no line-number arithmetic has to be kept in step with however many
        header lines _format_results_table decides to emit (the file-alias
        line alone is already conditional on the trace count).

        Tk clamps an insert at END to just before the Text's trailing newline,
        so the first line of `text` lands on the line `end-1c` names now.
        """
        base = int(self.results_text.index("end-1c").split(".")[0])
        self._append_result(text)
        _tag_swatch_rows(self.results_text, base, text, color_idxs)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
