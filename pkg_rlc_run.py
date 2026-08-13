"""
What a Calculate actually runs: the network, the spec, and the checks on both.

This is the arithmetic half of `App._on_calculate`, lifted out of the frontend
so it can be read, tested and reasoned about without a display.  Nothing here
touches Tk.  Three things that DO belong to the App are injected instead of
reached for:

  * `log(text, severity=LOG_INFO)` -- what `App._append_result` does.  Every
    warning the reduction emits has to reach the reader, and the Schur / lstsq
    / one-bad-frequency-NaN guard is the reason: a lumped ground inductance on
    a file with a DC point NaNs exactly one frequency and says so, and a caller
    that swallowed that line would leave a plausible 0 H on screen with nothing
    to explain it.
  * `files` -- the loaded `FileEntry` list, in place of `App.files` and
    `App._file_by_label`.
  * `cache` -- the composed-stack cache, in place of `App._compose_cache`.
    Keyed by the file labels and validated by FileEntry IDENTITY, because a
    file reloaded under the same name is a different set of arrays.  It is
    passed in rather than owned here for one measured reason: `comp.compose` is
    100 ms for 16 + 60 ports and 10.5 SECONDS for 16 + 153, and the strips call
    into this module once per keystroke.

WHAT IS DELIBERATELY NOT HERE.  The four plot-curve helpers --
`_make_plot_trace`, `_compose_curve_label`, `_plot_trace_label` and
`_coupling_plot_traces` -- stayed in `pkg_rlc_gui`.  They are defined by
`PlotTrace`, `COLORS`, `LINESTYLES` and `MAX_LABEL_LEN`, which are L4 widgets:
a curve is a drawing instruction, not a measurement, and moving them here would
be an upward import wearing a service's name.

`_migrate_trace` is not here either, and `_build_termination` no longer calls
it.  Folding a retired spec forward LOGS and REFRESHES THE TRACES LIST, so it
is an App action; `App._build_termination` migrates first and then calls this
one, which is exactly what it always did in that order.

Re-exported from `pkg_rlc_gui`, so `pkg_rlc_gui._collect_mports` and
`App._build_termination` keep resolving for every call site and every test.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

import numpy as np

import pkg_rlc_compose as comp
from pkg_rlc_compose import default_alias
from pkg_rlc_core import (
    TerminationSet,
    build_terminations_coupling,
    build_terminations_mode1,
    build_terminations_mode2,
    build_terminations_mode3,
    build_terminations_rows,
    compute_z_matrix,
    extract_coupling_at_freq,
    extract_rlc_at_freq,
    parse_port_range,
    parse_short_pairs,
)
from pkg_rlc_model import (
    FileEntry,
    LOG_ERROR,
    LOG_WARN,
    RunSnapshot,
    SolveNetwork,
    TraceConfig,
    _composed_solve_network,
)
from pkg_rlc_validate import (
    _check_bare_ports,
    _namespace_network,
    _scope_conn_rows,
    _scope_dsl_text,
    _scope_mport_rows,
    _scope_port_field,
    trace_file_labels,
    trace_is_composed,
)


def _file_by_label(files: Sequence, label: str) -> Optional["FileEntry"]:
    """The loaded file with this label, or None.

    `App._file_by_label` delegates here, so the two cannot come to disagree
    about what a label names.
    """
    for fe in files:
        if fe.label == label:
            return fe
    return None


# ============================================================================
# The network a trace is solved against
# ============================================================================

def _trace_network(tc: TraceConfig, files: Sequence,
                   cache: dict) -> "SolveNetwork":
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
    entries = [_file_by_label(files, lbl) for lbl in labels]
    if any(fe is None for fe in entries) or not entries:
        raise ValueError(
            "file " + ", ".join(f"'{lbl}'" for lbl, fe in zip(labels, entries)
                                if fe is None) + " is not loaded")
    if len(entries) == 1:
        fe = entries[0]
        return SolveNetwork(freqs=fe.ts.freqs, Y=fe.Y, nports=fe.ts.nports,
                            port_names=list(fe.ts.port_names))
    key = tuple(labels)
    hit = cache.get(key)
    if hit is not None:
        cached_entries, net = hit
        if len(cached_entries) == len(entries) and all(
                a is b for a, b in zip(cached_entries, entries)):
            return _composed_solve_network(net)
        # A file was reloaded under the same name: the label matches and
        # the arrays behind it are different objects, so the cached stack
        # is a stack of the PREVIOUS parse.  Identity is what catches that;
        # a label-only key would have kept serving it.
        cache.pop(key, None)
    net = comp.compose([comp.ComposeInput(data=fe.ts, alias=default_alias(i))
                        for i, fe in enumerate(entries)])
    cache[key] = (list(entries), net)
    return _composed_solve_network(net)


def _trace_namespace(tc: TraceConfig, files: Sequence):
    """
    (ComposedNetwork, home alias) for scoping this trace's port fields, or
    (None, "").  Namespace only -- see `_namespace_network`.

    Never raises: it is on the strips' path, where a raised error reaches
    no handler anyone controls.
    """
    if not trace_is_composed(tc):
        return None, ""
    try:
        entries = [_file_by_label(files, lbl)
                   for lbl in trace_file_labels(tc)]
        if any(fe is None for fe in entries):
            return None, ""
        net = _namespace_network(entries)
        return net, (net.blocks[0].alias if net.blocks else "")
    except Exception:                                    # pragma: no cover
        return None, ""


def _cached_trace_network(tc: TraceConfig, files: Sequence,
                          cache: dict) -> "SolveNetwork | None":
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
    hit = cache.get(tuple(labels))
    if hit is None:
        return None
    cached_entries, net = hit
    entries = [_file_by_label(files, lbl) for lbl in labels]
    if len(cached_entries) != len(entries) or any(
            a is not b for a, b in zip(cached_entries, entries)):
        return None
    return _composed_solve_network(net)


# ============================================================================
# The spec that is solved with it
# ============================================================================

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


def _build_termination(tc: TraceConfig,
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


# ============================================================================
# The checks, the coupling solve, and the empty record
# ============================================================================

def _reference_checks(tc: TraceConfig, sn: "SolveNetwork",
                      term: TerminationSet, f_hz: float, log) -> list:
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
        log(
            f"    [{tc.id}] the reference-node check could not run: {e} "
            f"-- the numbers below stand, but nothing has confirmed that "
            f"each file's ground network is in the circuit.", LOG_WARN)
        return []


def _calculate_coupling_trace(tc: TraceConfig, sn: "SolveNetwork",
                              f_rlc_hz: float, log,
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
        term = _build_termination(tc, nports=sn.nports, sn=sn)
    Zmat, names, warns = compute_z_matrix(sn.Y, sn.freqs, term)
    for w in warns:
        log(f"    [{tc.id}] {w}", LOG_WARN)
    if any("Rank-deficient" in w for w in warns):
        # INFO on purpose: this annotation exists to say the warning above
        # it is not a fault, so badging it again would contradict it.
        log(
            f"    [{tc.id}] (informational, not an error: a fully floating "
            "+/- structure is rank-deficient at every frequency and pinv "
            "handles it correctly)")
    if any("row and column of Z are NaN" in w for w in warns):
        log(
            f"    [{tc.id}] (this one IS an error in the port setup: the "
            "named measurement ports read nan because their probe current "
            "has nowhere to return. Give the port a '-' side, or add the "
            "ground ports the structure needs.)", LOG_ERROR)
    if any("cancelled to roundoff" in w for w in warns):
        log(
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
    tc.reference_checks = _reference_checks(tc, sn, term, f_rlc_hz, log)
    tc.rlc = extract_rlc_at_freq(sn.freqs, tc.Z, f_rlc_hz)
    cres = extract_coupling_at_freq(sn.freqs, Zmat, names, f_rlc_hz)
    tc.coupling = cres

    # The emptiness CONDITION of _coupling_plot_traces, not a call to it:
    # building the curves here just to test the list would recompute
    # _coupling_k_array over every frequency for every pair, and then throw
    # it away -- _replot_from_cache builds them for real a moment later.
    if not (tc.plot_self or (tc.plot_mutual and len(names) >= 2)):
        log(
            f"    [{tc.id}] both 'self' and 'mutual' are unchecked -- "
            "nothing plotted for this trace", LOG_WARN)
    return cres


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


def _empty_run(number: int) -> RunSnapshot:
    """A run record with nothing in it, for a report built before any
    Calculate (freezing a trace restored from a session, say)."""
    return RunSnapshot(number=number, when=datetime.now(),
                       marker_freq_hz=float("nan"))
