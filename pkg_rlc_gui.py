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
    DEFAULT_Z0,
    RECIPROCITY_WARN,
    SI_SUFFIXES,
    ConnectionRow,
    Ground,
    LumpedBetween,
    LumpedToGnd,
    MeasPortRow,
    Open,
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
    CONN_TABLE_HINT_SHORT,
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
    COMPARE_STACK_LINES_MAX,
    COUPLING_FLOOR_DB,
    COUPLING_LEGEND_LINES,
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
    RUN_KEPT_GLYPH,
    RUN_MARK_NEW,
    RUN_MARK_SEEN,
    RUN_OPEN_GLYPH,
    RUN_TABS_DEFAULT,
    RUN_TABS_HARD_CAP,
    SUMMARY_LABEL_MAX,
    VIEW_COMPARE,
    VIEW_DETAIL,
    VIEW_SUMMARY,
    _SWATCH_PAD,
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
    _render_columns,
    _row_file_labels,
    _sign_flag,
    _snapshot_file_legend,
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
# The CSV export blocks.  A file format, not a rendering of the results pane,
# so a module of its own beside pkg_rlc_report.  Re-exported, same rule again.
from pkg_rlc_csv import _coupling_k_array, _write_coupling_csv
# What a spec SAYS, what it will DO, and what is wrong with it -- the file set
# and the composed port namespace, the port descriptor and the overview counts,
# the text <-> rows import decision, and `_validation_report` with the two
# strip renderers over it.  All pure and all written NOT TO RAISE, because the
# editor strips call them from inside Tk variable traces once per keystroke.
# Re-exported, same rule as above.
#
# `WARN_FG` stayed in this file: it is a COLOUR the Ports & Roles window
# paints with, not a verdict.  So did `SolveNetwork` / `_composed_solve_network`,
# which carry the arrays a Calculate actually runs on.
from pkg_rlc_validate import (
    ComposeSpecError,
    FOOTER_STRIP_CHARS,
    VALIDATION_STRIP_LINES,
    V_NO_RESULT,
    V_OK,
    V_ROW_INERT,
    V_WRONG_NUMBER,
    WARN_FROM_KEPT_TEXT,
    WARN_OPEN_LOOKS_TERMINATED,
    WARN_PROBE_AND_GROUND,
    WARN_PROBE_AND_GROUND_COUPLING,
    _VMsg,
    _append_port_spec,
    _bucket_counts,
    _check_bare_ports,
    _collect_nets_safe,
    _dsl_meaning,
    _extra_lines_indicator,
    _field_has_tag,
    _footer_strip_text,
    _import_dsl_text,
    _measured_port_messages,
    _mport_more_lines,
    _namespace_network,
    _ordering_diff_summary,
    _port_descriptor,
    _port_overview_text,
    _probe_ground_messages,
    _rlc_echo,
    _role_warnings,
    _roles_header,
    _scan_count,
    _scope_conn_rows,
    _scope_dsl_text,
    _scope_mport_rows,
    _scope_port_field,
    _trace_role_rows,
    _union_port_specs,
    _validation_messages,
    _validation_report,
    _validation_strip_text,
    compose_spec_problems,
    scope_echo_messages,
    trace_file_aliases,
    trace_file_labels,
    trace_file_legend,
    trace_file_scope,
    trace_is_composed,
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
# The panels of the main window.  Each is a HAS-A: a plain class that OWNS its
# widgets and is handed this App at construction, built by `_build_left_panel`
# / `_build_right_panel` in exactly the position its widgets used to be
# written out in, and given a `bind_*` hook that `_bind_events` calls at the
# moment those lines used to run.  Deliberately NOT mixins -- almost every
# rule these panels have to keep is a rule about ORDER (pack order, build
# order, what is populated before `PanedWindow.add()`), and a mixin hides
# exactly that.
#
# They may not import this module back, at module level or inside a function:
# they are L5 in tests/test_layering.py and this file is L6.  What they need
# from here they get through the injected App.
from pkg_rlc_panels_files import FilesPanel
# FREEZE_MENU_LABEL / UNFREEZE_MENU_LABEL moved WITH the menu they label and
# are RE-EXPORTED here, the same rule as the DSL helpers and the connections
# table: `from pkg_rlc_gui import FREEZE_MENU_LABEL` keeps resolving.
from pkg_rlc_panels_traces import (
    FREEZE_MENU_LABEL,
    TracesPanel,
    UNFREEZE_MENU_LABEL,
)
# `RunTab` and `_tag_swatch_rows` moved WITH the notebook they belong to and
# are RE-EXPORTED here, the same rule again.  `_tag_swatch_rows` is the one
# results-pane renderer that is NOT a formatter -- it WRITES INTO a Tk Text --
# which is why it never went to pkg_rlc_report with the others.
from pkg_rlc_panels_results import ResultsPanel, RunTab, _tag_swatch_rows
# `StylePicker` and the editor's own constants moved WITH the form they belong
# to, and are RE-EXPORTED here, the same rule again.  StylePicker in
# particular could not stay: it draws from COLORS / LINESTYLES, which are
# pkg_rlc_plot's, and it is a FIELD of this form and of nothing else.
from pkg_rlc_panels_editor import (
    EDITOR_FIELD_CHARS,
    EditorPanel,
    FROZEN_EDITOR_NOTE,
    LABEL_PLACEHOLDER,
    MODE_PLACEHOLDERS,
    MP_TABLE_HINT,
    MP_TABLE_HINT_SHORT,
    MUTUAL_CURVE_HINT,
    StylePicker,
    TEXT_DIALOG_NOTE,
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


# The colour a flagged row takes in the Ports & Roles window. Same #b04000 as
# the frozen-trace note and the results pane's "flag" tag -- one warning colour
# in the application, not three.
WARN_FG = "#b04000"


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


# `RunTab` and `_tag_swatch_rows` moved to pkg_rlc_panels_results with the
# notebook they belong to, and are re-exported at the top of this file.


# `StylePicker` and the editor's own constants -- MODE_PLACEHOLDERS,
# LABEL_PLACEHOLDER, EDITOR_FIELD_CHARS, FROZEN_EDITOR_NOTE, the two
# MP_TABLE_HINTs, the two MUTUAL_CURVE_HINTs and TEXT_DIALOG_NOTE -- moved
# to pkg_rlc_panels_editor with the form they belong to, and are
# re-exported at the top of this file.


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

    # ---------------------------------------- what a panel reaches through App
    #
    # A panel is L5 and this module is L6 (tests/test_layering.py), so a panel
    # may not import these -- not at module level and not inside a function.
    # Every one of them is a pure function over a TraceConfig or a RunSnapshot
    # and BELONGS at L1 / L2 with those types; it cannot go there until they
    # do.  Until then a panel reaches them through the App it already holds.
    #
    # Plain aliases, deliberately: `pkg_rlc_gui._duplicate_trace_config` and
    # `app._duplicate_trace_config` are the SAME object, so there is nothing
    # here that can come to disagree with the module-level name every test
    # imports.  This list is also the checklist for the phase that moves the
    # model down -- when it is empty, the panels import their model directly.
    _duplicate_trace_config = staticmethod(_duplicate_trace_config)
    _freeze_trace_config = staticmethod(_freeze_trace_config)
    freeze_refusal = staticmethod(freeze_refusal)
    _snapshot_row = staticmethod(_snapshot_row)
    _snapshot_block = staticmethod(_snapshot_block)
    _config_signature = staticmethod(_config_signature)
    _draw_signature = staticmethod(_draw_signature)

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
        # Built by FilesPanel, in this position and in this pack order.  The
        # widget alias below is what keeps `app.files_lb` resolving for every
        # existing caller and test -- the same rule as re-exporting a moved
        # symbol from the module it came out of.
        self._files_panel = FilesPanel(parent, self)
        self.files_lb = self._files_panel.files_lb

        # --- Traces section ---
        # Built by TracesPanel, in this position and in this pack order; the
        # widget alias is what keeps `app.traces_lb` resolving.  Same rule as
        # the Files section above.
        self._traces_panel = TracesPanel(parent, self)
        self.traces_lb = self._traces_panel.traces_lb

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
        # Built by EditorPanel, in this position -- AFTER Global Controls has
        # claimed the bottom, which is the whole of the rule above.  The panel
        # owns every widget on the form and App aliases each one, so
        # `app.ed_conn_table`, `app._ed_canvas` and the rest keep resolving for
        # every existing caller and test; each is created once during the build
        # and never reassigned, so an alias is exactly as good as the attribute.
        ed = ttk.LabelFrame(parent, text="Edit Selected Trace")
        ed.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._editor_panel = ep = EditorPanel(ed, self)
        self._ed_body = ep._ed_body
        self._ed_canvas = ep._ed_canvas
        self._ed_foot = ep._ed_foot
        self._ed_footer_font = ep._ed_footer_font
        self._ed_footer_font_u = ep._ed_footer_font_u
        self._ed_form = ep._ed_form
        self._ed_hsb = ep._ed_hsb
        self._ed_lockable = ep._ed_lockable
        self._ed_mode_buttons = ep._ed_mode_buttons
        self._ed_vsb = ep._ed_vsb
        self._ed_win = ep._ed_win
        self.ed_conn_head = ep.ed_conn_head
        self.ed_conn_hint = ep.ed_conn_hint
        self.ed_conn_table = ep.ed_conn_table
        self.ed_edit_text_btn = ep.ed_edit_text_btn
        self.ed_enabled_cb = ep.ed_enabled_cb
        self.ed_enabled_var = ep.ed_enabled_var
        self.ed_extra_lbl = ep.ed_extra_lbl
        self.ed_file_cbo = ep.ed_file_cbo
        self.ed_file_var = ep.ed_file_var
        self.ed_footer_strip = ep.ed_footer_strip
        self.ed_frozen_note = ep.ed_frozen_note
        self.ed_gnd = ep.ed_gnd
        self.ed_gnd_lbl = ep.ed_gnd_lbl
        self.ed_label = ep.ed_label
        self.ed_mode_var = ep.ed_mode_var
        self.ed_mp_hint = ep.ed_mp_hint
        self.ed_mp_lbl = ep.ed_mp_lbl
        self.ed_mp_table = ep.ed_mp_table
        self.ed_mutual_hint = ep.ed_mutual_hint
        self.ed_overview = ep.ed_overview
        self.ed_plot_frame = ep.ed_plot_frame
        self.ed_plot_lbl = ep.ed_plot_lbl
        self.ed_plot_mutual_cb = ep.ed_plot_mutual_cb
        self.ed_plot_mutual_var = ep.ed_plot_mutual_var
        self.ed_plot_self_cb = ep.ed_plot_self_cb
        self.ed_plot_self_var = ep.ed_plot_self_var
        self.ed_porta = ep.ed_porta
        self.ed_porta_lbl = ep.ed_porta_lbl
        self.ed_portb = ep.ed_portb
        self.ed_portb_lbl = ep.ed_portb_lbl
        self.ed_short = ep.ed_short
        self.ed_short_lbl = ep.ed_short_lbl
        self.ed_style = ep.ed_style
        self.ed_validation = ep.ed_validation

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

        # The whole Results half is built by ResultsPanel, into the frame
        # created above and BEFORE the add() below -- see that module's
        # docstring.  The aliases are what keep `app.results_text`,
        # `app.results_nb`, `app._keep_btn` and the rest resolving for every
        # existing caller and test; each is a widget, created once and never
        # reassigned, so an alias is exactly as good as the attribute was.
        self._results_panel = ResultsPanel(results_frame, self)
        self.results_view_var = self._results_panel.results_view_var
        self.units_mode_var = self._results_panel.units_mode_var
        self._runs_menubutton = self._results_panel._runs_menubutton
        self._runs_menu = self._results_panel._runs_menu
        self._keep_btn = self._results_panel._keep_btn
        self._results_header = self._results_panel._results_header
        self.results_nb = self._results_panel.results_nb
        self._log_tab = self._results_panel._log_tab
        self.results_text = self._results_panel.results_text
        self._run_tab_menu = self._results_panel._run_tab_menu

        self.plot = PlotPanel(plot_frame, on_marker_changed=self._on_marker_drag)
        self.plot.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        parent.add(results_frame, weight=0)
        parent.add(plot_frame, weight=1)

    def _bind_events(self) -> None:
        # The Files list's own binding, in the position the line held.  See
        # FilesPanel: the panel is a HAS-A, so the ORDER stays visible here.
        self._files_panel.bind_selection()
        self._traces_panel.bind_selection()
        # A different file means a different port count: the Port / To
        # dropdowns and the overview strip both key off it.
        self.ed_file_cbo.bind("<<ComboboxSelected>>",
                              lambda e: self._on_editor_file_changed())
        # Expanding or collapsing a hint changes the form's height.
        self.bind("<<HintToggled>>",
                  lambda e: self._refresh_editor_scrollregion(preserve=True),
                  add="+")
        # The Traces list's <space> binding and its right-click menu, in the
        # position these lines held.  `_trace_menu` is aliased for the same
        # reason `traces_lb` is: tests/test_freeze_trace.py enumerates its four
        # entries by index off `app._trace_menu`.
        self._traces_panel.bind_context_menu()
        self._trace_menu = self._traces_panel._trace_menu

        # The Files list's right-click menu, in the position these lines held.
        # `_files_menu` is aliased for the same reason `files_lb` is.
        self._files_panel.bind_context_menu()
        self._files_menu = self._files_panel._files_menu

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
    #
    # The Files section is `pkg_rlc_panels_files.FilesPanel`, which OWNS the
    # implementations behind the four delegators below.  They are the
    # method-level form of this file's re-export rule: `app._on_add_file` and
    # friends keep resolving for every existing caller and every test.

    def _make_file_entry(self, ts: TouchstoneData) -> FileEntry:
        """
        The panel's route to a FileEntry.

        `FileEntry` lives in this module, and a panel may not import upward
        (tests/test_layering.py) -- so the panel asks the App for one,
        exactly as it already asks for a default trace through
        `_make_default_trace`.
        """
        return FileEntry(ts)

    def _load_one_file(self, path: str) -> TouchstoneData | None:
        return self._files_panel._load_one_file(path)

    def _on_add_file(self) -> None:
        self._files_panel._on_add_file()

    def _on_remove_file(self) -> None:
        self._files_panel._on_remove_file()

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
        self._files_panel._on_check_file()

    def _on_file_selected(self) -> None:
        self._files_panel._on_file_selected()

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

    # The Traces section is `pkg_rlc_panels_traces.TracesPanel`, which OWNS
    # the implementations behind the delegators below -- the method-level
    # form of this file's re-export rule, exactly as for the Files section.

    def _on_add_trace(self) -> None:
        self._traces_panel._on_add_trace()

    def _on_remove_trace(self) -> None:
        self._traces_panel._on_remove_trace()

    def _on_toggle_trace_key(self, _event=None) -> str:
        return self._traces_panel._on_toggle_trace_key(_event)

    def _on_toggle_trace(self) -> None:
        self._traces_panel._on_toggle_trace()

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
        self._traces_panel._on_duplicate_trace()

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
        self._traces_panel._on_trace_context_menu(event)

    def _sync_trace_menu(self, tc: TraceConfig) -> None:
        self._traces_panel._sync_trace_menu(tc)

    def _on_files_context_menu(self, event) -> None:
        self._files_panel._on_files_context_menu(event)

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
        self._traces_panel._on_freeze_trace()

    def _on_unfreeze_trace(self) -> None:
        self._traces_panel._on_unfreeze_trace()

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

    # ----------------------------------------------------------- the editor
    #
    # The editor is `pkg_rlc_panels_editor.EditorPanel`, which OWNS every
    # implementation below: the form, both scrollbars and the one function
    # that decides them, the scrollregion, the per-mode visibility, the two
    # strips, the text hatch and the auto-apply sync chain.  Delegators, the
    # method-level form of this file's re-export rule -- `app._flush_editor_
    # sync`, `app._apply_editor_strips`, `app._sync_editor_to_trace` and the
    # rest keep resolving.  `_build_editor` / `_build_editor_form` get NO
    # delegator: the panel builds itself, and a forward that would build a
    # second editor into a fresh parent is a trap rather than a re-export.
    #
    # The editor's mutable STATE is not down there -- `_suppress_editor_sync`,
    # `_ed_extra_lines`, `_ed_strips_pending`, the two `_ed_sync_*`,
    # `_ed_shown_mode` and the two `_ed_scroll_*` stay on this object and the
    # panel reads and writes them through the App it holds.

    def _ed_scroll_set(self, first: str, last: str) -> None:
        return self._editor_panel._ed_scroll_set(first, last)

    def _on_editor_canvas_configure(self, event) -> None:
        return self._editor_panel._on_editor_canvas_configure(event)

    def _ed_hscroll_set(self, first: str, last: str) -> None:
        return self._editor_panel._ed_hscroll_set(first, last)

    def _apply_editor_scrollbars(self) -> None:
        return self._editor_panel._apply_editor_scrollbars()

    def _ed_wheel(self, event) -> bool:
        return self._editor_panel._ed_wheel(event)

    def _refresh_editor_scrollregion(self, preserve: bool = False) -> None:
        return self._editor_panel._refresh_editor_scrollregion(preserve)

    def _apply_editor_scrollregion(self) -> None:
        return self._editor_panel._apply_editor_scrollregion()

    def _set_editor_editable(self, editable: bool) -> None:
        return self._editor_panel._set_editor_editable(editable)

    def _on_trace_selected(self) -> None:
        return self._editor_panel._on_trace_selected()

    def _on_mode_changed(self) -> None:
        return self._editor_panel._on_mode_changed()

    def _update_mode_visibility(self) -> None:
        return self._editor_panel._update_mode_visibility()

    def _editor_nports(self) -> Optional[int]:
        return self._editor_panel._editor_nports()

    def _refresh_port_choices(self) -> None:
        return self._editor_panel._refresh_port_choices()

    def _strips_wanted(self) -> bool:
        return self._editor_panel._strips_wanted()

    def _on_editor_file_changed(self) -> None:
        return self._editor_panel._on_editor_file_changed()

    def _on_editor_rows_changed(self) -> None:
        return self._editor_panel._on_editor_rows_changed()

    def _refresh_editor_strips(self) -> None:
        return self._editor_panel._refresh_editor_strips()

    def _apply_editor_strips(self) -> None:
        return self._editor_panel._apply_editor_strips()

    def _editor_spec_inputs(self) -> tuple:
        return self._editor_panel._editor_spec_inputs()

    def _selected_trace(self) -> Optional[TraceConfig]:
        return self._editor_panel._selected_trace()

    def _on_footer_route(self, _event = None) -> None:
        return self._editor_panel._on_footer_route(_event)

    def _scroll_editor_to(self, widget, top: Optional[int] = None) -> None:
        return self._editor_panel._scroll_editor_to(widget, top)

    def _editor_port_names(self) -> Optional[Sequence[str]]:
        return self._editor_panel._editor_port_names()

    def _editor_curve_span(self, term) -> int:
        return self._editor_panel._editor_curve_span(term)

    def _editor_dsl_text(self) -> str:
        return self._editor_panel._editor_dsl_text()

    def _on_edit_as_text(self) -> None:
        return self._editor_panel._on_edit_as_text()

    def _import_text_into_tables(self, text: str) -> None:
        return self._editor_panel._import_text_into_tables(text)

    def _schedule_editor_sync(self, *_args) -> None:
        return self._editor_panel._schedule_editor_sync(*_args)

    def _flush_editor_sync(self) -> None:
        return self._editor_panel._flush_editor_sync()

    def _cancel_editor_sync(self) -> None:
        return self._editor_panel._cancel_editor_sync()

    def _apply_editor_sync(self) -> None:
        return self._editor_panel._apply_editor_sync()

    def _on_style_changed(self) -> None:
        return self._editor_panel._on_style_changed()

    def _on_enabled_toggled(self) -> None:
        return self._editor_panel._on_enabled_toggled()

    def _sync_editor_to_trace(self, tc: TraceConfig) -> None:
        return self._editor_panel._sync_editor_to_trace(tc)

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
        self._files_panel._refresh_file_list()

    def _refresh_trace_list(self) -> None:
        self._traces_panel._refresh_trace_list()

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
    #
    # The Results pane is `pkg_rlc_panels_results.ResultsPanel`, which OWNS
    # every implementation below -- the notebook, the Log badge, the run
    # pages, the two menus and the one builder that turns a run into text.
    # These are the method-level form of this file's re-export rule, as for
    # the Files and Traces sections: `app._append_result`,
    # `app._add_run_tab`, `app._keep_run_tab` and the rest keep resolving.
    #
    # The run-history STATE is NOT down there: `_run_tabs`, `_last_run`,
    # `_run_counter`, `_log_unseen`, `_log_forced`, the two caps and their
    # IntVars stay on this object and the panel reads and writes them
    # through the App it holds.  See that module's docstring for why.

    def _render_results(self, run: RunSnapshot) -> None:
        return self._results_panel._render_results(run)

    def _run_report_segments(self, run: RunSnapshot) -> list:
        return self._results_panel._run_report_segments(run)

    def _detail_segments(self, run: RunSnapshot, shown_rows, shown_blocks, units: str) -> list:
        return self._results_panel._detail_segments(run, shown_rows, shown_blocks, units)

    def _run_heading(self, run: RunSnapshot, what: str) -> str:
        return self._results_panel._run_heading(run, what)

    def _summary_segments(self, run: RunSnapshot, shown_rows, shown_blocks, units: str) -> list:
        return self._results_panel._summary_segments(run, shown_rows, shown_blocks, units)

    def _compare_segments(self, run: RunSnapshot, shown_rows, shown_blocks, units: str) -> list:
        return self._results_panel._compare_segments(run, shown_rows, shown_blocks, units)

    def _footer_segments(self, shown_rows, shown_blocks, hidden) -> list:
        return self._results_panel._footer_segments(shown_rows, shown_blocks, hidden)

    def _write_run_report(self, txt, run: RunSnapshot) -> None:
        return self._results_panel._write_run_report(txt, run)

    def _on_results_view_changed(self) -> None:
        return self._results_panel._on_results_view_changed()

    def _on_units_mode_changed(self) -> None:
        return self._results_panel._on_units_mode_changed()

    def _rerender_every_page(self, log_note: str) -> None:
        return self._results_panel._rerender_every_page(log_note)

    def _log_selected(self) -> bool:
        return self._results_panel._log_selected()

    def _render_log_badge(self) -> None:
        return self._results_panel._render_log_badge()

    def _on_results_tab_changed(self, _event = None) -> None:
        return self._results_panel._on_results_tab_changed(_event)

    def _select_log_tab(self) -> None:
        return self._results_panel._select_log_tab()

    def _select_results_tab(self, tab) -> bool:
        return self._results_panel._select_results_tab(tab)

    def _kept_run_tabs(self) -> list[RunTab]:
        return self._results_panel._kept_run_tabs()

    def _auto_run_tabs(self) -> list[RunTab]:
        return self._results_panel._auto_run_tabs()

    def _kept_cap(self) -> int:
        return self._results_panel._kept_cap()

    def _selected_run_tab(self) -> Optional[RunTab]:
        return self._results_panel._selected_run_tab()

    def _current_run_number(self) -> int:
        return self._results_panel._current_run_number()

    def _newest_run_tab(self) -> Optional[RunTab]:
        return self._results_panel._newest_run_tab()

    def _make_results_text(self, parent):
        return self._results_panel._make_results_text(parent)

    def _new_run_tab(self, run: RunSnapshot) -> RunTab:
        return self._results_panel._new_run_tab(run)

    def _reader_is_at_the_newest_run(self) -> bool:
        return self._results_panel._reader_is_at_the_newest_run()

    def _add_run_tab(self, run: RunSnapshot) -> RunTab:
        return self._results_panel._add_run_tab(run)

    def _destroy_run_tab(self, rt: RunTab) -> None:
        return self._results_panel._destroy_run_tab(rt)

    def _evict_run_tabs(self) -> None:
        return self._results_panel._evict_run_tabs()

    def _render_run_tab_label(self, rt: RunTab) -> None:
        return self._results_panel._render_run_tab_label(rt)

    def _render_run_tab(self, rt: RunTab) -> None:
        return self._results_panel._render_run_tab(rt)

    def _render_all_run_tabs(self) -> None:
        return self._results_panel._render_all_run_tabs()

    def _refresh_keep_button(self) -> None:
        return self._results_panel._refresh_keep_button()

    def _keep_run_tab(self, rt: RunTab) -> bool:
        return self._results_panel._keep_run_tab(rt)

    def _on_keep_run(self) -> None:
        return self._results_panel._on_keep_run()

    def _run_tab_at(self, x: int, y: int) -> Optional[RunTab]:
        return self._results_panel._run_tab_at(x, y)

    def _on_run_tab_context_menu(self, event) -> None:
        return self._results_panel._on_run_tab_context_menu(event)

    def _sync_run_tab_menu(self, rt: 'RunTab') -> None:
        return self._results_panel._sync_run_tab_menu(rt)

    def _on_menu_keep_run(self) -> None:
        return self._results_panel._on_menu_keep_run()

    def _on_menu_close_run(self) -> None:
        return self._results_panel._on_menu_close_run()

    def _on_menu_close_other_runs(self) -> None:
        return self._results_panel._on_menu_close_other_runs()

    def _rebuild_runs_menu(self) -> None:
        return self._results_panel._rebuild_runs_menu()

    def _on_run_caps_changed(self) -> None:
        return self._results_panel._on_run_caps_changed()

    def _append_result(self, text: str, severity: str = LOG_INFO) -> None:
        return self._results_panel._append_result(text, severity)

    def _append_swatched(self, text: str, color_idxs: Sequence[int]) -> None:
        return self._results_panel._append_swatched(text, color_idxs)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
