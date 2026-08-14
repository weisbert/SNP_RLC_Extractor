"""
pkg_rlc_core.py  --  Core computation for PKG RLC Extractor.

THIS FILE IS NOW A FACADE.  What used to be 4725 lines here is three modules:

    pkg_rlc_touchstone   reading a file, and saying what is wrong with it
    pkg_rlc_spec         what the user DECLARED -- terminations, rows, roles
    pkg_rlc_solve        the arithmetic -- S<->Y, Schur, extraction, fitting

They stack in that order and nothing reaches back up.  Every name any of them
defines is re-exported below, so `from pkg_rlc_core import <anything>` means
exactly what it has always meant: the GUI, the CLI, pkg_rlc_attrib,
pkg_rlc_compose and all 45 test modules import from here unchanged, and this
is the module to keep importing from unless you are inside one of the three.
An attribute WRITE here is forwarded to the module that owns the name -- see
the note at the foot of this file, which is not a nicety.

The rest of this docstring is the description that was here before the split
and still describes the whole:

Touchstone parser (universal content-based, ignores extension), S<->Y
conversion, unified port-termination model, Schur-complement reduction,
single-frequency RLC extraction, mutual-coupling extraction, the Mode 5
custom-termination DSL, and broadband fitting.

User-facing port indices are 1-based; internal computation is 0-based.
The boundary between the two is the build_terminations_* helpers
(input is 1-based) and the GUI/CLI layer.

Measurement model
-----------------
A measurement is a pair of multimeter probes: a RED probe on the "+" side and
a BLACK probe on the "-" side.  A measurement port is therefore a named triple
(name, plus_ports, minus_ports); ports on the same side are tied together
(parallel, unsigned) and there are no fractional weights -- only membership.
An empty minus side means the port is referenced to ground.

Any number of measurement ports may be defined.  `compute_z_matrix` returns the
G x G open-circuit impedance matrix Z(f): the diagonal is the self impedance of
each measurement port and the off-diagonal entries are the mutual impedances
(all other measurement ports open, which is the textbook definition of M).
`compute_z` is the single-measurement-port special case kept for backward
compatibility, and its G == 1 code paths are deliberately written with the
historical floating-point expressions so that existing modes stay bit-exact.
"""

from __future__ import annotations

import sys as _sys
import types as _types

import pkg_rlc.physics.solve as _pkg_rlc_solve
import pkg_rlc.physics.spec as _pkg_rlc_spec
import pkg_rlc.physics.touchstone as _pkg_rlc_touchstone

# ============================================================================
# The re-exports
# ============================================================================
#
# Explicit lists rather than `import *`, for two reasons.  `import *` skips
# every name beginning with an underscore, and the private ones are imported
# by name elsewhere in this repo -- `pkg_rlc_gui` writes
# `from pkg_rlc_core import _collect_nets`, `pkg_rlc_attrib` uses
# `_normalize_signal`, and the parser tests reach for `_sniff_nports`,
# `_strictly_increasing`, `_max_possible_nports` and `_MONO_CHUNK`.  And a
# written-out list is the thing that goes red in review when a symbol is
# quietly dropped, which a star cannot do.
#
# Same precedent as the Mode 5 DSL helpers, which moved into pkg_rlc_core and
# are re-exported by pkg_rlc_gui so that `from pkg_rlc_gui import parse_si`
# keeps resolving.
# ----------------------------------------------------------------------------

from pkg_rlc.physics.touchstone import (        # noqa: F401  (re-export, not a use)
    DEFAULT_Z0, FREQ_UNIT_SCALE, MAX_SNIFF_NPORTS, SNIFF_HARD_CAP,
    COMPUTE_BATCH, COMPUTE_CHUNK_BYTES, _freq_batch, PARSE_FLUSH_VALUES,
    PARSE_WARN_CAP, ENCODING_SNIFF_BYTES, DIAGNOSE_MAX_LINES,
    DIAGNOSE_TAIL_LINES, FAULT_FILE, FAULT_UNSUPPORTED, FAULT_ACCESS,
    FAULT_INTERNAL, FAULT_NONE, _VERDICT, _clip, TouchstoneParseError,
    TouchstoneData, _PORT_NAME_RE, _V2_KEYWORD_RE, _SNP_EXT_RE, _BOMS,
    _BINARY_MAGIC, _sniff_encoding, _ext_nports, _decode_options,
    parse_touchstone, _parse_touchstone, _recover_data_line, _to_float,
    _floats_or_none, _check_freq_axis, _check_s_values, _MONO_CHUNK,
    _strictly_increasing, _sniff_range, _sniff_nports, _max_possible_nports,
    _sniff_reach, _nports_fits, _Diagnosis, _LineScan, _scan_lines,
    _value_line, _nports_from_record, _diag_candidate, _diagnose,
    _safe_diagnose, check_touchstone, diagnose_touchstone, _SI_PREFIXES,
    format_si, format_freq,
)
from pkg_rlc.physics.spec import (              # noqa: F401  (re-export, not a use)
    LEGACY_GROUP_NAMES, SI_SUFFIXES, parse_port_range, parse_short_pairs,
    parse_mport_spec, parse_si, parse_kv_rlc_params, YFunc, y_resistor,
    y_inductor, y_capacitor, y_series_rlc, Open, Ground, Vdd, Signal,
    LumpedToGnd, ShortPair, LumpedBetween, PortTermination, Coupling,
    TerminationSet, MeasPort, _normalize_signal, resolve_meas_ports,
    build_terminations_mode1, build_terminations_mode2,
    build_terminations_mode3, build_terminations_mode4,
    build_terminations_coupling, NET_KEYWORD, NET_RESERVED_NAMES,
    NET_BAD_CHARS, validate_net_name, NetDef, _split_net_tail, _net_key,
    _looks_like_a_name, _defined_nets_phrase, _first_port_of, _collect_nets,
    _resolve_port_field, _check_one_name_per_node,
    parse_custom_termination_text, CONN_KINDS, CONN_KINDS_WITH_PARTNER,
    CONN_KINDS_WITH_RLC, CONN_KINDS_WITH_NET, MeasPortRow, ConnectionRow,
    short_group_spec, _rlc_tokens, rows_to_dsl_text, dsl_text_to_rows,
    _conn_with_rlc, build_terminations_rows, _merge_view, _params_key,
    _effective_parallel, parallel_stamp_messages, inert_lumped_messages,
    ROLE_PROBE_PLUS, ROLE_PROBE_MINUS, ROLE_GROUND, ROLE_VDD, ROLE_ELEMENT,
    ROLE_SHORTED, ROLE_OPEN, OVERVIEW_BUCKETS, ROLE_TO_BUCKET, PortRole,
    _role_of, port_roles, row_sources, collapse_ports, MergedNode,
    merged_nodes, OPEN_CLUSTER_MIN_FAMILY, OPEN_CLUSTER_MIN_TERMINATED,
    OPEN_CLUSTER_MAX_OPEN_FRACTION, OPEN_CLUSTER_NAMES_SHOWN,
    _TERMINATED_ROLES, name_prefix, OpenNameCluster, open_name_clusters,
    open_port_name_messages, _validate_port_indices,
)
from pkg_rlc.physics.solve import (             # noqa: F401  (re-export, not a use)
    SCHUR_LSTSQ_RCOND, PINV_RCOND, PROBE_RANGE_TOL, SCHUR_COLLAPSE_TOL,
    RECIPROCITY_WARN, s_to_y, y_to_s, _rank_deficient_warning,
    _schur_collapse_warning, _open_probe_warning, _no_return_path_warning,
    compute_z_matrix, _is_singular_2x2, _probe_impedance, compute_z,
    RLCResult, extract_rlc_at_freq, PortRLC, PairCoupling, CouplingResult,
    _ratio_db, extract_coupling_at_freq, InductorFit, CapacitorFit,
    _select_band, _scaled_lstsq, fit_inductor, fit_capacitor, fit_auto,
    eval_inductor_model, eval_capacitor_model,
)


# ============================================================================
# The facade's write-through
# ============================================================================
#
# An attribute WRITE on this module is forwarded to whichever split module
# defines the name, as well as being applied here.
#
# `mock.patch.object(pkg_rlc_core, "MAX_SNIFF_NPORTS", 2)` -- which
# tests/test_large_files.py and tests/test_parse_diagnostics.py both do, and
# which is how the port-count cap, the diagnosis line budget and the
# descriptive |S| check are tested at all -- has to keep changing what the
# PARSER sees.  A plain re-export cannot: the parser reads its own module
# global, so a patch applied only here would rebind this module's copy and
# silently turn five tests into assertions about nothing.  Reads need no help;
# the `from ... import ...` block above has already bound every name in this
# namespace.
#
# Dunders are left alone: `hasattr(mod, "__name__")` is true of every module,
# and writing one through would rename the module it was forwarded to.

_SPLIT_MODULES = (_pkg_rlc_touchstone, _pkg_rlc_spec, _pkg_rlc_solve)


class _CoreFacade(_types.ModuleType):
    """`pkg_rlc_core`'s own type -- see the note above."""

    def __setattr__(self, name, value):
        if not name.startswith("__"):
            for _mod in _SPLIT_MODULES:
                if hasattr(_mod, name):
                    setattr(_mod, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if not name.startswith("__"):
            for _mod in _SPLIT_MODULES:
                if hasattr(_mod, name):
                    delattr(_mod, name)
        super().__delattr__(name)


_sys.modules[__name__].__class__ = _CoreFacade

