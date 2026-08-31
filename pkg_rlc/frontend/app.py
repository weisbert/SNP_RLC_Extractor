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
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

from pkg_rlc.physics.core import (
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
from pkg_rlc.widgets.plot import (
    COLORS, LINESTYLES, MAX_LABEL_LEN, PlotPanel, ReflowRow,
    Trace as PlotTrace,
)
from pkg_rlc.present.help import HelpWindow
# The connections table's SHAPE, and the RowTable vocabulary it is spoken in,
# now live in pkg_rlc_conntable (they are pure -- no Tk, no App -- and the
# editor is not the only reader).  Imported by name and RE-EXPORTED here, the
# same precedent as the Mode 5 DSL helpers below: `from pkg_rlc_gui import
# conn_table_layout` and friends keep resolving for every existing caller and
# every test.
from pkg_rlc.present.conntable import (
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
# a termination -- now live in pkg_rlc_widgets, and so does the whole palette:
# PLACEHOLDER_FG was always there, and WARN_FG / PORT_ROLE_FG / the
# `_fixed_map_filter` Treeview workaround have joined them.  ONE palette in ONE
# module was the point: while two of the three lived in this file, a panel that
# wanted the warning colour had to reach UP into the frontend for it, and three
# of the ten function-level `import pkg_rlc_gui` dodges were nothing but a
# colour lookup.  Same re-export rule as above: `from pkg_rlc_gui import
# RowTable` and `pkg_rlc_gui.WARN_FG` keep resolving.
#
# `StylePicker` is deliberately NOT there and lives in pkg_rlc_panels_editor:
# it draws from COLORS and LINESTYLES, which live in pkg_rlc_plot, and
# pkg_rlc_plot imports ReflowRow from pkg_rlc_widgets -- so reaching back for
# the palettes would be a module-level cycle.
from pkg_rlc.widgets.widgets import (
    PLACEHOLDER_FG,
    PORT_ROLE_FG,
    WARN_FG,
    PlaceholderEntry,
    PlaceholderText,
    RowTable,
    _CollapsibleHint,
    _fixed_map_filter,
    _tk_dash,
    editor_scroll_fraction,
)
# The shared data model: what a trace IS, what one file loaded as, what a
# Calculate runs against, and the pure functions that copy a trace or compare
# two of them.  It sits BELOW every panel and every window, which is the whole
# reason it exists as a module -- see its docstring, and the LAYERS map in
# tests/test_layering.py.  Re-exported here, the same rule as everything above:
# `from pkg_rlc_gui import TraceConfig` and `pkg_rlc_gui.FileEntry` keep
# resolving for every call site and every test.
from pkg_rlc.model.trace import (
    CouplingSnapshot,
    FileEntry,
    FitSnapshot,
    RowSnapshot,
    RunSnapshot,
    SolveNetwork,
    TraceConfig,
    _composed_solve_network,
    _config_signature,
    _draw_signature,
    _duplicate_trace_config,
    _SIGNATURE_FIELDS,
    _snapshot_files,
    _snapshot_fit,
    run_signatures,
    trace_signature_fields,
)
# The three snapshot BUILDERS are imported under private names because this
# file defines three wrappers of the same name over them -- the injection of
# `reference_provenance`, which is the one thing that could not move down with
# the record.  See "Run snapshots" below.  `from pkg_rlc_gui import
# _snapshot_row` therefore keeps resolving, and keeps resolving to the wrapper,
# which is the version every caller in the repo has always had.
from pkg_rlc.model.trace import (
    _snapshot_block as _model_snapshot_block,
    _snapshot_reference as _model_snapshot_reference,
    _snapshot_row as _model_snapshot_row,
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
from pkg_rlc.present.report import (
    COMPARE_STACK_LINES_MAX,
    COUPLING_FLOOR_DB,
    COUPLING_LEGEND_LINES,
    DIGITS_DEFAULT,
    FREQ_WIDE_FMT,
    FreqSnap,
    LOG_BADGE_CAP,
    LOG_ERROR,
    LOG_INFO,
    LOG_WARN,
    RESULTS_DIGITS,
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
    digits_sig,
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
from pkg_rlc.present.csv import _coupling_k_array, _write_coupling_csv
# The session file -- Save Config, Load Config and the on-exit autosave, as a
# pure dict <-> model round trip with no Tk in it.  Re-exported, same rule as
# everything above: `from pkg_rlc_gui import session_from_dict` and
# `pkg_rlc_gui.SESSION_FORMAT` keep resolving.  The private names are in the
# list because tests reach for them by module attribute
# (`pkg_rlc_gui._TRACE_STRLIST_FIELDS`, `_LEGACY_TRACE_FIELDS`,
# `_CONTROL_CHOICES`), which is what pins the field classification.
#
# What stayed in this file is the half that needs the App: `_session_dict`
# (which flushes the editor first, the Calculate rule), `_on_save_config` /
# `_on_load_config` and their dialogs, `_load_session_file`, `_apply_session`
# and the autosave hook on WM_DELETE_WINDOW.
# What a Calculate actually runs: the network a trace is solved against (one
# file or a composed stack), the spec it is solved with, the reference-node
# check and the coupling reduction.  No Tk in any of it -- the App injects
# `_append_result` as `log`, its `files` list and its composed-stack cache.
#
# Imported as `run` because the App keeps a same-named thin method over most of
# these (`self._trace_network` -> `run._trace_network(tc, self.files, ...)`),
# and a bare `from ... import _trace_network` would shadow nothing useful while
# making the two hard to tell apart at the call site.  The module functions are
# ALSO re-exported below under their old names, because tests reach for
# `pkg_rlc_gui._collect_mports` and `pkg_rlc_gui._trace_plot_freqs` directly.
import pkg_rlc.services.run as run
from pkg_rlc.services.run import _collect_mports, _trace_plot_freqs
from pkg_rlc.services.session import (
    AUTOSAVE_DIRNAME,
    AUTOSAVE_FILENAME,
    LoadedSession,
    SESSION_FILETYPES,
    SESSION_FORMAT,
    SESSION_VERSION,
    SessionError,
    _COMPUTED_TRACE_FIELDS,
    _CONTROL_CHOICES,
    _CONTROL_KEYS,
    _LEGACY_TRACE_FIELDS,
    _OPTIONAL_TRACE_FIELDS,
    _TRACE_BOOL_FIELDS,
    _TRACE_INT_FIELDS,
    _TRACE_ROW_CLASSES,
    _TRACE_STRLIST_FIELDS,
    _coerce_bool,
    _config_trace_fields,
    _file_ref,
    _rows_from_list,
    _strings_from_list,
    autosave_path,
    resolve_session_file,
    session_from_dict,
    session_to_dict,
    trace_from_dict,
    trace_to_dict,
)
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
from pkg_rlc.model.validate import (
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
import pkg_rlc.physics.compose as comp
from pkg_rlc.physics.compose import default_alias
# `_collect_nets` is reached by name on purpose.  It is the ONE definition of
# which tokens in a Mode 5 DSL block are NODE NAMES rather than port fields,
# and `_scope_dsl_text` has to skip exactly those.  A second copy here would
# let the field this file rewrites and the field core resolves disagree, which
# is the drift this repo has been bitten by (RECIPROCITY_WARN, and the two
# definitions of "which files is this trace made of" that `trace_file_labels`
# now has to keep mirrored).  It never raises for a malformed line.
from pkg_rlc.physics.core import _collect_nets
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
from pkg_rlc.panels.attrib_gui import (
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
from pkg_rlc.panels.files_gui import (
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
from pkg_rlc.panels.panels_files import CLEAR_FILES_MENU_LABEL, FilesPanel
# FREEZE_MENU_LABEL / UNFREEZE_MENU_LABEL moved WITH the menu they label and
# are RE-EXPORTED here, the same rule as the DSL helpers and the connections
# table: `from pkg_rlc_gui import FREEZE_MENU_LABEL` keeps resolving.
from pkg_rlc.panels.panels_traces import (
    CLEAR_TRACES_MENU_LABEL,
    FREEZE_MENU_LABEL,
    TracesPanel,
    UNFREEZE_MENU_LABEL,
)
# `RunTab` and `_tag_swatch_rows` moved WITH the notebook they belong to and
# are RE-EXPORTED here, the same rule again.  `_tag_swatch_rows` is the one
# results-pane renderer that is NOT a formatter -- it WRITES INTO a Tk Text --
# which is why it never went to pkg_rlc_report with the others.
from pkg_rlc.panels.panels_results import ResultsPanel, RunTab, _tag_swatch_rows
# `StylePicker` and the editor's own constants moved WITH the form they belong
# to, and are RE-EXPORTED here, the same rule again.  StylePicker in
# particular could not stay: it draws from COLORS / LINESTYLES, which are
# pkg_rlc_plot's, and it is a FIELD of this form and of nothing else.
from pkg_rlc.panels.panels_editor import (
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
#
# FileEntry, TraceConfig and its three legacy migrations, SolveNetwork and
# `_composed_solve_network` moved to pkg_rlc_model, and are re-exported at the
# top of this file.  They left because every panel and every window needs the
# data model and none of them may import this file: while they lived here, the
# only way to reach them was an `import pkg_rlc_gui` inside a function body.


# WARN_FG moved to pkg_rlc_widgets with the rest of the palette, and is
# re-exported at the top of this file.


# ============================================================================
# Session files (Save Config / Load Config / autosave)
# ============================================================================
#
# The whole round trip moved to pkg_rlc_session and is re-exported at the top
# of this file.  It left because none of it is Tk and none of it ever was:
# `session_to_dict` takes the lists and `session_from_dict` returns them, which
# is what has always made the format testable with no display.  What stayed
# here is the part that genuinely needs the App -- the file dialogs, reading
# the widgets into a `controls` dict, and applying a `LoadedSession` back onto
# live traces (`_session_dict`, `_load_session_file`, `_apply_session`).


# ============================================================================
# Mode 6 helpers (+/- measurement ports, coupling)
# ============================================================================

# `_duplicate_trace_config` moved to pkg_rlc_model with the TraceConfig it
# copies, and is re-exported at the top of this file.

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


# `_config_signature` and `_draw_signature` moved to pkg_rlc_model with the
# TraceConfig they compare, and are re-exported at the top of this file.

# `_collect_mports` moved to pkg_rlc_run beside the `_build_termination` that
# is its only real caller, and is re-exported at the top of this file.


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


# `_trace_plot_freqs` moved to pkg_rlc_run with the composed axis it reads, and
# is re-exported at the top of this file.


# ------------------------------------------------- what changed between runs
# `_SIGNATURE_FIELDS`, `trace_signature_fields` and `run_signatures` moved to
# pkg_rlc_model with the TraceConfig they read, and are re-exported at the top
# of this file.


# ============================================================================
# Run snapshots -- the RECORD is pkg_rlc_model's; the reference RENDER is here
# ============================================================================
#
# `RowSnapshot`, `CouplingSnapshot`, `FitSnapshot`, `RunSnapshot` and the
# `_snapshot_*` builders moved DOWN to `pkg_rlc_model` with the `TraceConfig`
# they resolve, and are re-exported at the top of this file.  Everything they
# say about themselves -- what a snapshot is for, the four fields that are the
# blast radius, and what is deliberately NOT in one (no Z, no Zmat, no
# fit_freqs, no fit_Z, no aux) -- is written there now.
#
# ONE THING COULD NOT GO WITH THEM.  `_snapshot_reference` calls
# `reference_provenance`, which is `pkg_rlc_files_gui`'s at L5: rendering the
# composition's reference-node verdict for a reader is presentation, and it
# stays beside the window that shows it.  Rendering it once, at snapshot time,
# is R3-5 and is not negotiable -- two copies of one verdict are two things
# that can come to disagree -- so the call could not simply be dropped either.
#
# So it is INJECTED.  The three builders below take `provenance`, the model
# stores the text it is handed, and these three wrappers are the only place
# that names the renderer.  Every existing call site is untouched, which is the
# point: `_snapshot_row(tc, label, res)` still means what it always meant, and
# `App._snapshot_row` / `App._snapshot_block` still resolve to these.
def _snapshot_reference(tc: "TraceConfig") -> dict:
    return _model_snapshot_reference(tc, provenance=reference_provenance)


def _snapshot_row(tc: "TraceConfig", file_label: str, res) -> RowSnapshot:
    return _model_snapshot_row(tc, file_label, res,
                               provenance=reference_provenance)


def _snapshot_block(tc: "TraceConfig", file_label: str,
                    cres, freq: Optional[FreqSnap] = None) -> CouplingSnapshot:
    return _model_snapshot_block(tc, file_label, cres, freq,
                                 provenance=reference_provenance)


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

PORT_ROLES_HINT = (
    "Select rows, then send them to the editor. Ports are written back as a "
    "collapsed range (1-3,7), so a 54-ball ground group stays one row."
)

# PORT_ROLE_FG and `_fixed_map_filter` moved to pkg_rlc_widgets with the
# rest of the palette, and are re-exported at the top of this file.


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

#: The File-menu entry that empties the whole window.  Named for the reason
#: every other menu label in this app is: the test looks it up by label, and a
#: menu entry nobody can find is the same as no feature at all.  The two
#: NARROWER clears -- `Clear all files` and `Clear all traces` -- are on the
#: two lists' own right-click menus and live with them
#: (`pkg_rlc.panels.panels_files` / `pkg_rlc.panels.panels_traces`).
CLEAR_ALL_MENU_LABEL = "Clear All"


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
        # BETWEEN the session entries and Exit, because that is what it is:
        # the start of a new session without leaving the window.  The two
        # narrower clears are on the lists' own right-click menus, where the
        # thing being cleared is what the pointer is already on; this one is
        # the everything gesture and so it is on the menu that owns the
        # session.  It asks first -- see `_on_clear_all`.
        file_menu.add_command(label=CLEAR_ALL_MENU_LABEL,
                              command=self._on_clear_all)
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
        self.sig_digits_var = self._results_panel.sig_digits_var
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
        """Reduce to the G x G measurement-port Z matrix and extract the
        coupling result at the marker frequency.  See
        `run._calculate_coupling_trace`.

        The migration is HERE for the same reason it is on
        `_build_termination`, and on the same condition the old code had it:
        this function builds its own `TerminationSet` only when the caller did
        not, and that build used to go through `App._build_termination`, which
        migrates.  `run._build_termination` does not, so the wrapper has to --
        otherwise a legacy-shaped trace reaching this path with `term=None`
        would be read UNMIGRATED (mode 4 still mode 4, `mp1_*` still unfolded)
        and answer a different question in silence.  `_on_calculate` always
        passes `term`, so this is the rarely-taken half; it is preserved
        because it was there, not because it is hot.
        """
        if term is None:
            self._migrate_trace(tc)
        return run._calculate_coupling_trace(tc, sn, f_rlc_hz,
                                             self._append_result, term=term)

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
        return run._empty_run(self._run_counter)

    def _trace_network(self, tc: TraceConfig) -> "SolveNetwork":
        """The network this trace is solved against -- one file, or several.
        See `run._trace_network`; the stack cache is the App's."""
        return run._trace_network(tc, self.files, self._compose_cache)

    def _trace_namespace(self, tc: TraceConfig):
        """(ComposedNetwork, home alias) for scoping this trace's port fields,
        or (None, ""). Namespace only, and it never raises -- it is on the
        strips' path.  See `run._trace_namespace`."""
        return run._trace_namespace(tc, self.files)

    def _cached_trace_network(self, tc: TraceConfig) -> "SolveNetwork | None":
        """The composed network IF IT IS ALREADY BUILT, else None -- and it
        NEVER builds one.  See `run._cached_trace_network` for the measurement
        that rule rests on (10.5 s per keystroke at 169 ports)."""
        return run._cached_trace_network(tc, self.files, self._compose_cache)

    def _build_termination(self, tc: TraceConfig,
                           nports: int | None = None,
                           sn: "SolveNetwork | None" = None) -> TerminationSet:
        """
        The trace's spec as a TerminationSet -- `run._build_termination`, with
        the legacy migration in front of it.

        The migration stays HERE and not in `pkg_rlc_run` because folding a
        retired shape forward LOGS a line and REFRESHES THE TRACES LIST: it is
        an App action, not arithmetic.  The order is what it always was --
        migrate, then read the migrated spec -- so every caller, including
        `pkg_rlc_attrib_gui`'s `app._build_termination(...)`, is unchanged.
        """
        self._migrate_trace(tc)
        return run._build_termination(tc, nports=nports, sn=sn)

    def _reference_checks(self, tc: TraceConfig, sn: "SolveNetwork",
                          term: TerminationSet, f_hz: float) -> list:
        """R3-5, in pkg_rlc_run.  See `run._reference_checks`."""
        return run._reference_checks(tc, sn, term, f_hz, self._append_result)


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
                "sig_digits": self.sig_digits_var.get(),
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

    def _on_clear_all(self) -> None:
        """
        Back to the window the tool starts with, without restarting it.

        WHAT IT CLEARS AND WHY EACH: the files and the traces, because that is
        the gesture; the RUN PAGES and the Log, because every line in them is
        about a file or a trace that is gone, and a run page whose numbers
        cannot be traced back to anything is worse than no page; the composed
        stack cache, because every entry is validated by FileEntry IDENTITY
        and every FileEntry it could validate against has just gone, so all
        that is left of it is the largest allocation this app holds; and the
        two counters, because "a new session" is what this claims to be and a
        run numbered 7 in a window with no runs in it is not that.

        WHAT IT DOES NOT CLEAR: the display controls (view, units, digits, the
        fit model, the marker frequency).  Those are how the reader has set
        the tool up, not what they are looking at -- the same distinction the
        session file draws when it saves them next to the data rather than
        inside it.

        IT ASKS FIRST, and it asks with the counts, because none of this is
        undoable and a mode-6 spec is not recoverable by retyping it in a
        hurry.  It asks only when there is something to lose: on an empty
        window the entry does nothing at all rather than opening a dialog
        about nothing.
        """
        if not (self.files or self.traces or self._run_tabs):
            return
        bits = []
        if self.files:
            bits.append(f"{len(self.files)} file(s)")
        if self.traces:
            bits.append(f"{len(self.traces)} trace(s)")
        if self._run_tabs:
            kept = sum(1 for rt in self._run_tabs if rt.kept)
            bits.append(f"{len(self._run_tabs)} run page(s)"
                        + (f" ({kept} kept)" if kept else ""))
        if not messagebox.askyesno(
                CLEAR_ALL_MENU_LABEL,
                "Clear " + ", ".join(bits) + " and the Log?\n\n"
                "The view, units, digits and fit settings stay as they are. "
                "This cannot be undone.",
                parent=self):
            return
        # Cancel, don't flush: the queued edit belongs to a trace that is about
        # to be discarded -- `_apply_session`'s rule, and its reason.
        self._cancel_editor_sync()
        self.files = []
        self.traces = []
        self._trace_list_shown = []
        self._compose_cache.clear()
        self._next_trace_id = 1
        self._run_counter = 0
        # The pane's own half: the run pages, the Log text and the badge.  It
        # is the panel's because the panel built those widgets.
        self._results_panel._clear_results()
        self._refresh_file_list()
        self._refresh_trace_list()
        self._refresh_file_combobox()
        # keep_cursors=False: nothing is computed any more, so there is no
        # curve for a kept cursor to read -- the same argument `_apply_session`
        # makes for the same call.
        self._replot_from_cache(keep_cursors=False)
        # Every open window that HOLDS a result has just had its subject taken
        # away and cannot re-read its way out of that; the Ports & Roles window
        # can, and re-reads to an empty list.
        refresh_attribution_windows(self)
        refresh_files_windows(self)
        self._refresh_port_roles_window()
        self._append_result("Cleared " + ", ".join(bits) + ".")

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
                         ("results_view", self.results_view_var),
                         ("sig_digits", self.sig_digits_var)):
            if key in controls:
                var.set(controls[key])
        # The plot's cursor readout is the one thing the Digits control
        # reaches that is not repainted by simply setting the variable: the
        # readout is built during a draw, and the draw below is the first one
        # this session.  Set BEFORE it, so the restored session's first frame
        # is already at the restored precision.
        self.plot.set_sig_digits(digits_sig(self.sig_digits_var.get()))

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

    def _on_digits_changed(self) -> None:
        return self._results_panel._on_digits_changed()

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
