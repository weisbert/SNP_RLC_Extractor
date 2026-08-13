"""
pkg_rlc_validate.py  --  what a spec SAYS, what it will DO, and what is wrong
with it.

Split out of pkg_rlc_gui.py, verbatim.  Three groups, all of them pure -- no
Tk, no App, no widget -- which is the property the editor strips depend on:
they run inside Tk variable traces, i.e. once per keystroke, where a raised
exception reaches no handler we control and the GUI carries on showing a
stale, wrong verdict.  Every entry point here is written not to raise, and
that contract is stated again on each one.

  * THE FILE SET AND THE NAMESPACE.  Which files a trace is built from, what
    a tagged port field resolves to, and the port-count refusal that stops a
    bare number silently addressing the next file's ports.

  * WHAT THE SPEC SAYS.  The one-line port descriptor, the port-overview
    counts, and the text <-> rows import decision.

  * WHAT IS WRONG WITH IT.  `_validation_report` and everything under it,
    ordered by CONSEQUENCE rather than by check order, plus the two strip
    renderers that spend their measured character budgets on it.

A `TraceConfig` is read by duck typing and never imported -- it still lives in
pkg_rlc_gui, and this module sits below it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

import numpy as np

import pkg_rlc_compose as comp
from pkg_rlc_compose import default_alias
from pkg_rlc_core import (
    CONN_KINDS_WITH_PARTNER,
    CONN_KINDS_WITH_RLC,
    ConnectionRow,
    Ground,
    MeasPortRow,
    OVERVIEW_BUCKETS,
    PortRole,
    ROLE_GROUND,
    ROLE_VDD,
    TerminationSet,
    Vdd,
    build_terminations_rows,
    collapse_ports,
    dsl_text_to_rows,
    format_si,
    inert_lumped_messages,
    open_name_clusters,
    open_port_name_messages,
    parallel_stamp_messages,
    parse_custom_termination_text,
    parse_kv_rlc_params,
    parse_port_range,
    parse_short_pairs,
    port_roles,
    resolve_meas_ports,
    row_sources,
    rows_to_dsl_text,
)
# `_collect_nets` is reached by name on purpose.  It is the ONE definition of
# which tokens in a Mode 5 DSL block are NODE NAMES rather than port fields,
# and `_scope_dsl_text` has to skip exactly those.  A second copy here would
# let the field this module rewrites and the field core resolves disagree.
from pkg_rlc_core import _collect_nets


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
