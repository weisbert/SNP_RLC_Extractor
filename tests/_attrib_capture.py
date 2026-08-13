"""
_attrib_capture.py  --  Capture the CURRENT text of the Attribution window's
PURE formatters, byte for byte.

This is a SCRIPT plus a case registry, NOT a unittest module: the leading
underscore keeps it out of `unittest discover` (same convention as
_golden_capture.py and _render_capture.py).

WHY IT EXISTS.  A later phase unifies the attribution report: the ~1400 lines
of `_attr_print_*` / `_cold_print_*` in `pkg_rlc_extractor.py` become
text-returning functions in a shared module, and `pkg_rlc_attrib_gui`'s
formatters are meant to consume that same module.  "The window's text did not
move" is the acceptance criterion of that phase, and it is a promise rather
than a check unless a byte-exact BEFORE exists.  So this file is that before:

    tests/fixtures/attrib_reference/<case>.txt      the text, verbatim
    tests/fixtures/attrib_reference/manifest.json   the index + sha256 + env

One file per case on purpose -- the reference IS prose and a per-case file is
what makes a git diff of it readable, which a single JSON blob of escaped
strings is not.  `manifest.json` carries the case order, the describe line, the
sha256 and the capture environment, so a missing, extra or silently truncated
case is a loud failure rather than a short file nobody looks at.

WHAT IS COVERED, and why each shape is here (see `CASES` for the registry):

  * every quantity that DECOMPOSES (`Z` -- the complex one, which is the only
    two-value-column shape -- plus `ReZ`, `ImZ`, `M`, `M/L_a`, `k`) and every
    one REFUSED BY NAME (`C_c`, `Q`, `|Z|`, `dB`), whose refusal message is a
    deliverable in its own right;
  * a healthy reconciliation, a reconciliation ABOVE THE FLOOR, a WITHHELD
    split and a `not comparable` what-if -- `reconciliation_verdict`'s three
    states plus the withheld clause, which is four different header lines;
  * the DIAGONAL and the SHARED-RETURN ground models, i.e. the 9.6 dB choice,
    which also produces the `not comparable` verdict from real numbers rather
    than from a fake;
  * a COMPOSED-NETWORK baseline, with and without the `BaselineLinks` gauge, so
    the header's `baseline:` sentence and the exactly-zero far-file term are
    both pinned;
  * a sweep WITH a pole (diff_pair, the documented 505 nH anti-resonance) and
    one WITHOUT (decap, whose residues are all exactly zero so ideal == open);
  * both units modes (`smart` and `aligned`) on the shapes where the aligned
    per-column SI prefix can differ;
  * the folded tail, the empty-terms message, the singular-baseline fold, and
    the pure string helpers (`signed_str`, `render_table`, `parse_candidate`).

NO DISPLAY IS TOUCHED.  Nothing here constructs a `Tk`, a `Toplevel` or a
widget; `pkg_rlc_attrib_gui`'s pure half is deliberately built so that this is
possible, and `tests/test_attrib_golden.py` asserts it.  The module DOES import
tkinter at import time (it subclasses `tk.Toplevel`), and `contributions_table`
reaches `pkg_rlc_gui._value_formatter` through `_gui()`, so the reference
cannot be replayed without both modules being importable -- importable, not
displayable.

DETERMINISM.  Every input is either a shipped fixture or a literal here; the
only floating-point comes out of numpy on this box.  `manifest.json` stamps
numpy / python / platform for the same reason `golden_legacy.npz` does.  The
capture was run twice in fresh processes and compared before being committed.

Run it to (re)generate the reference:

    python tests/_attrib_capture.py

Regenerate ONLY in the same commit that justifies moving the reference.  A
failure in tests/test_attrib_golden.py means the rendered attribution text
changed: fix the change, do not re-capture to make the test pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import pkg_rlc_attrib as at  # noqa: E402
from pkg_rlc_core import (  # noqa: E402
    parse_custom_termination_text,
    parse_touchstone,
    row_sources,
    s_to_y,
)

FIXTURE_DIR = _HERE / "fixtures"
REFERENCE_DIR = FIXTURE_DIR / "attrib_reference"
MANIFEST_NAME = "manifest.json"

#: Fixtures this registry needs.  Named rather than discovered: a case that
#: silently stopped running because its file was renamed is exactly the kind of
#: quiet coverage loss a golden reference exists to prevent.
DIFF_PAIR = "diff_pair_4port.s4p"          # lines 1->3, 2->4; L=5n, M=1n
COUPLED_DIFF = "coupled_4port_diff.s4p"    # coils 1-2 and 3-4, M = 800 pH
COUPLED_FLOAT = "coupled_4port_float.s4p"  # the same, no shunt C -> singular Y
DECAP = "decap_4port.s4p"                  # two UNCOUPLED pi networks
PI_2PORT = "pi_2port.s2p"                  # the second file of the composition


# ============================================================================
# Loading and context construction
#
# Every context below is built from a shipped fixture through the SHIPPED
# entry points -- `parse_touchstone` -> `s_to_y` -> `parse_custom_termination_
# text` -> `at.build_context`.  Nothing is hand-rolled that the application
# itself would not do, so a change in any of those shows up here rather than
# being papered over by a private construction.
# ============================================================================

_PARSE_CACHE: dict[str, object] = {}


def load(name: str):
    """(TouchstoneData, Y) for a fixture, parsed once per process."""
    if name not in _PARSE_CACHE:
        d = parse_touchstone(FIXTURE_DIR / name)
        _PARSE_CACHE[name] = (d, s_to_y(d.s, d.z0))
    return _PARSE_CACHE[name]


def composed():
    """
    Two shipped fixtures stacked into ONE 6-port network, through the shipped
    composer.

    `diff_pair_4port.s4p` (F1, global 1..4) and `pi_2port.s2p` (F2, global
    5..6).  Both carry the SAME 401-point 1 MHz .. 10 GHz axis, so nothing is
    interpolated and the composition adds no warning of its own -- which keeps
    this case about the attribution baseline and not about the frequency plan.
    """
    import pkg_rlc_compose as comp

    d1, _ = load(DIFF_PAIR)
    d2, _ = load(PI_2PORT)
    return comp.compose([comp.ComposeInput(data=d1, alias="F1"),
                         comp.ComposeInput(data=d2, alias="F2")])


@dataclass(frozen=True)
class Spec:
    """One (network, termination spec, frequency, ground model) to decompose.

    `ground` is 'declared' (the spec's own element impedances -- what the
    reconciliation can have a second opinion about), 'diag' (an explicit
    diagonal `Zt`) or 'shared' (one return impedance across every shunt
    element, which is the 9.6 dB choice and which makes the context a what-if).

    `gauge` requests the composed-network `BaselineLinks` policy.
    """
    key: str
    describe: str
    fixture: str
    dsl: str
    victim: str
    aggressor: str
    freq_hz: float
    ground: str = "declared"
    l_model: float = 0.0
    gauge: bool = False


#: The frequency every single-file case is read at unless it says otherwise.
#: 5.1 GHz is the frequency the Attribution window's own suite uses on
#: `coupled_4port_diff.s4p`, and 5 GHz is the one every measurement in
#: CLAUDE.md's attribution section quotes on `diff_pair_4port.s4p`.
F_COUPLED = 5.1e9
F_DIFF = 5.0e9

SPECS: list[Spec] = [
    Spec("coupled_grounds",
         "coupled_4port_diff, probes 1/3, grounds 2 and 4 -- the shipped case",
         COUPLED_DIFF,
         "1 signal vic +\n3 signal agg +\n2 ground\n4 ground\n",
         "vic", "agg", F_COUPLED),

    Spec("coupled_diff_probes",
         "coupled_4port_diff, two DIFFERENTIAL probes and no element at all "
         "(the bare-EM-only table)",
         COUPLED_DIFF,
         "1 signal vic +\n2 signal vic -\n3 signal agg +\n4 signal agg -\n",
         "vic", "agg", F_COUPLED),

    Spec("diff_lumped_grounds",
         "diff_pair, probes 1/2, both far ends through R=0.1 L=1n to ground",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n"
         "3 lumped_to_gnd R=0.1 L=1n\n4 lumped_to_gnd R=0.1 L=1n\n",
         "vic", "agg", F_DIFF),

    Spec("diff_ideal_grounds",
         "diff_pair, probes 1/2, both far ends IDEALLY grounded -- the base "
         "the two ground models are measured against",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n3 ground\n4 ground\n",
         "vic", "agg", F_DIFF),

    Spec("diff_grounds_diag",
         "the same two grounds through INDEPENDENT 1 nH leads -- a what-if, "
         "so the engine was never asked about it and the verdict is 'not "
         "comparable'",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n3 ground\n4 ground\n",
         "vic", "agg", F_DIFF, ground="diag", l_model=1e-9),

    Spec("diff_grounds_shared",
         "the same two grounds through ONE SHARED 1 nH return -- the choice "
         "CLAUDE.md measures at 1.0120 nH independent against 2.0259 nH "
         "shared on exactly this spec",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n3 ground\n4 ground\n",
         "vic", "agg", F_DIFF, ground="shared", l_model=1e-9),

    Spec("diff_ideal_and_weak",
         "diff_pair with one IDEAL ground and one 1 GOhm lead -- the second "
         "term is negligible, which is what produces the folded-tail line",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n"
         "3 ground\n4 lumped_to_gnd R=1G\n",
         "vic", "agg", F_DIFF),

    Spec("diff_short_and_ground",
         "diff_pair, a short between the far ends plus a ground on one of "
         "them -- two element KINDS in one table",
         DIFF_PAIR,
         "1 signal vic +\n2 signal agg +\n3 short_to 4\n4 ground\n",
         "vic", "agg", F_DIFF),

    Spec("decap_grounds",
         "decap, two UNCOUPLED pi networks -- the sweep here is exactly "
         "constant (every residue is 0), which is the no-pole case",
         DECAP,
         "1 signal vic +\n3 signal agg +\n2 ground\n4 ground\n",
         "vic", "agg", F_DIFF),

    Spec("float_singular",
         "coupled_4port_float, whose cond(Y) is 2.5e16 -- the SINGULAR "
         "baseline auto-recovery, with BOTH coils referenced",
         COUPLED_FLOAT,
         "1 signal vic +\n3 signal agg +\n"
         "2 lumped_to_gnd R=50\n4 lumped_to_gnd R=50\n",
         "vic", "agg", F_COUPLED),

    Spec("float_one_coil_nan",
         "the same fixture with only ONE coil referenced: compute_z_matrix "
         "says NaN, so the residual is NaN and a NaN residual is NOT a pass "
         "-- the split is withheld and the total is still reported",
         COUPLED_FLOAT,
         "1 signal vic +\n3 signal agg +\n4 lumped_to_gnd R=50\n",
         "vic", "agg", F_COUPLED),

    Spec("composed_no_gauge",
         "diff_pair + pi_2port as ONE network, cross-file link 3-5, grounds "
         "on 4 and 6 -- WITHOUT the baseline gauge, where the far file's "
         "element is exactly zero",
         "", "1 signal vic +\n2 signal agg +\n3 short_to 5\n4 ground\n"
             "6 ground\n",
         "vic", "agg", F_DIFF),

    Spec("composed_gauge",
         "the same composition WITH BaselineLinks -- the cross-file link is "
         "in the baseline, which is a gauge change and is named on the report",
         "", "1 signal vic +\n2 signal agg +\n3 short_to 5\n4 ground\n"
             "6 ground\n",
         "vic", "agg", F_DIFF, gauge=True),
]

SPEC_BY_KEY = {s.key: s for s in SPECS}

_CTX_CACHE: dict[str, object] = {}


def _network_of(spec: Spec):
    """(Y, freqs, baseline) for one spec -- a fixture, or the composition."""
    if spec.fixture:
        d, Y = load(spec.fixture)
        return Y, d.freqs, None
    net = composed()
    baseline = None
    if spec.gauge:
        baseline = at.BaselineLinks(
            blocks=at.PortBlocks.from_sizes([b.nports for b in net.blocks]))
    return net.Y, net.freqs, baseline


def _build(spec: Spec, zt=None):
    """One `build_context` call, with the spec's own provenance attached.

    `row_sources` is handed the DSL as `extra_lines` because that is what this
    registry has -- there are no editor rows here -- so the `from` column reads
    `text line N`.  That is the honest label for a spec that IS text, and it is
    the same call `_editor_dsl_text`'s kept-as-text block produces.
    """
    Y, freqs, baseline = _network_of(spec)
    term = parse_custom_termination_text(spec.dsl)
    return at.build_context(Y, freqs, term, spec.freq_hz, zt=zt,
                            sources=row_sources(extra_lines=spec.dsl),
                            baseline=baseline)


def context(key: str):
    """The `AttribContext` for one `Spec`, built once per process.

    `build_context` is `O(N^3)` and every step off it is microseconds, so the
    registry shares one context per spec exactly as `cold_start_report` shares
    one -- and, more to the point here, so that two cases of the same spec
    cannot be reading two different contexts.

    A ground model is applied the way `pkg_rlc_extractor._attr_zt` applies it:
    the dense block is built over the SHUNT sub-block only, because
    `termination_impedance_shared_return` assumes every element it is handed is
    a ball sharing the return plane and handing it a `short_to` as well would
    quietly stop that being a short.
    """
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    spec = SPEC_BY_KEY[key]
    ctx = _build(spec)
    if spec.ground != "declared":
        shunt = [i for i, e in enumerate(ctx.elements) if e.is_shunt]
        z = complex(0.0, 2.0 * math.pi * ctx.freq_hz * float(spec.l_model))
        zt = np.array(ctx.Zt, dtype=complex)
        if spec.ground == "diag":
            for i in shunt:
                zt[i, i] = z
        else:
            dense = at.termination_impedance_shared_return([z] * len(shunt),
                                                           z_ret=z)
            for r, i in enumerate(shunt):
                for c, j in enumerate(shunt):
                    zt[i, j] = dense[r, c]
        ctx = _build(spec, zt=zt)
    _CTX_CACHE[key] = ctx
    return ctx


# ============================================================================
# Derived objects: decompositions, sensitivities, sweeps
# ============================================================================

_DEC_CACHE: dict[tuple, object] = {}


def dec(key: str, quantity: str = "M"):
    ck = (key, quantity)
    if ck not in _DEC_CACHE:
        spec = SPEC_BY_KEY[key]
        _DEC_CACHE[ck] = at.decompose(context(key), spec.victim,
                                      spec.aggressor, quantity)
    return _DEC_CACHE[ck]


def sens(key: str, quantity: str = "M", candidates: str = ""):
    """`sensitivity` over the default candidates, or over a typed field."""
    spec = SPEC_BY_KEY[key]
    ctx = context(key)
    if candidates:
        import pkg_rlc_attrib_gui as ag
        alts, _problems = ag.candidate_list(candidates, ctx.omega)
    else:
        alts = at.default_alternatives(ctx.omega)
    return at.sensitivity(ctx, spec.victim, spec.aggressor, alts, quantity)


#: The sample count `AttributionWindow._draw_sweep` passes, and it matters:
#: the closed-form poles are exact whatever the grid does, but `sweep_picture`
#: measures the WIDTH of a pole's excursion off the SAMPLES, so with the
#: default `samples=0` there is no curve, `pic.drawn` is empty and the caption
#: takes its no-pole branch on a sweep that has one.  Matching the window is
#: the whole point of a reference to the window's text.
SWEEP_SAMPLES = 160


def sweep(key: str, element=0, quantity: str = "M", param: str = "L",
          t_max: Optional[float] = None):
    spec = SPEC_BY_KEY[key]
    return at.sweep_mobius(context(key), spec.victim, spec.aggressor, element,
                           quantity=quantity, param=param, t_max=t_max,
                           samples=SWEEP_SAMPLES)


def prov(key: str, quantity: str = "M", units_mode: str = "smart", **kw):
    """A `Provenance` for one spec, with every non-deterministic field fixed.

    Nothing here is read off a clock or off a live `TraceConfig`: the run
    number, the trace id and the label are literals, exactly as
    `_render_capture` makes its snapshots plain data.
    """
    import pkg_rlc_attrib_gui as ag

    spec = SPEC_BY_KEY[key]
    ctx = context(key)
    base = dict(
        trace_id=3,
        trace_label="coil_pair",
        file_label=spec.fixture or "diff_pair_4port.s4p + pi_2port.s2p",
        run_number=7,
        spec_matches_run=True,
        victim=spec.victim,
        aggressor=spec.aggressor,
        quantity=quantity,
        requested_hz=spec.freq_hz,
        actual_hz=ctx.freq_hz,
        spec_text=spec.dsl.rstrip("\n"),
        units_mode=units_mode,
        signature=("sig", key),
    )
    base.update(kw)
    return ag.Provenance(**base)


# ============================================================================
# Hand-built shapes
#
# Four states of the reconciliation line, the empty table and the removed
# trace are all reachable only from a `Decomposition` a real fixture will not
# produce on demand.  These are built by hand for exactly the reason
# `tests/test_attrib_window.py`'s pure half builds its own: the subject here is
# the FORMATTER, and feeding it a constructed input is how a formatter is
# tested.  They are kept together and clearly marked so that nobody mistakes
# them for measurements.
# ============================================================================

def _fake_element(index: int, kind: str = "ground", port: int = 2,
                  source: str = "conn row 1") -> at.Element:
    ports = ((port - 1, port) if kind in ("short", "lumped_between")
             else (port - 1,))
    return at.Element(kind=kind, ports=ports, source=source, ideal=True,
                      index=index)


def _fake_term(el, contrib: complex, share: float = 0.0,
               quad: float = 0.0) -> at.Term:
    return at.Term(element=el, contribution=contrib,
                   current=complex(1e-3, -2e-4), trans_z=complex(-1.25, 0.5),
                   share_inline=share, share_quad=quad)


def fake_dec(terms, quantity: str = "M", unit: str = "H",
             total: complex = 1e-9 + 0j, resid: float = 1e-13,
             floor: float = 1e-10, applicable: bool = True,
             trustworthy: bool = True, notes=(), warnings=()):
    """A `Decomposition` built by hand -- no file, no solve, no engine."""
    rb = at.ReturnBudget(
        em_reference=1.0, declared=0.5, declared_all=0.5, em_fraction=0.5,
        dominant=False,
        note=("Return path: the declared elements carry 50.00% of the "
              "aggressor's drive current and the EM model's own reference "
              "carries the rest, so a forward-minus-return story can be told "
              "only in part."))
    return at.Decomposition(
        victim="vic", aggressor="agg", freq_hz=5.1e9, requested_hz=5.1e9,
        quantity=quantity, unit=unit, total_reference=total, total_sum=total,
        residual_rel=resid, residual_floor=floor, terms=list(terms),
        return_budget=rb, reference_note="ref note",
        split_trustworthy=trustworthy, reference_applicable=applicable,
        notes=list(notes), warnings=list(warnings))


def fake_healthy():
    return fake_dec([
        _fake_term(None, 2.5e-10, 0.25),
        _fake_term(_fake_element(0, "ground", 2), 5.0e-10, 0.50, 0.01),
        _fake_term(_fake_element(1, "ground", 4), 2.5e-10, 0.25, -0.02),
    ])


def fake_withheld():
    """The split WITHHELD: the two algorithms disagree about the total."""
    return fake_dec([], resid=1.01, floor=4.3e-10, trustworthy=False)


def fake_above_floor():
    """Inside RESIDUAL_CATASTROPHIC but outside the condition-aware floor."""
    return fake_dec([
        _fake_term(None, 2.5e-10, 0.25),
        _fake_term(_fake_element(0, "ground", 2), 7.5e-10, 0.75, 0.03),
    ], resid=3.1e-7, floor=4.3e-10)


def fake_not_comparable():
    return fake_dec([
        _fake_term(None, 2.5e-10, 0.25),
        _fake_term(_fake_element(0, "lumped_to_gnd", 2), 7.5e-10, 0.75),
    ], applicable=False)


def fake_nan_and_inf():
    """A NaN term, an infinite one and a zero one, all in one table.

    Three separate rules meet here.  `_value_fmt` renders a NaN as `--` (no
    reading) and an infinity as a SIGNED `inf` (a real reading, and "ran away
    upward" is not "ran away downward"), and KEEPS both rows -- dropping one
    shifts every row below it and the swatches stop lining up with the
    elements they name.  `_fold_terms` then sorts both of them LAST (a missing
    measurement is not a small number) and never folds them, while the EXACTLY
    ZERO element -- what an annihilated lumped element reads as -- is finite,
    is below the floor, and does fold.
    """
    return fake_dec([
        _fake_term(None, 2.5e-10, 0.25),
        _fake_term(_fake_element(0, "ground", 2), float("nan") + 0j,
                   float("nan"), float("nan")),
        _fake_term(_fake_element(1, "lumped_to_gnd", 3),
                   complex(float("inf"), 0.0), float("inf")),
        _fake_term(_fake_element(2, "short", 5), 0j, 0.0, 0.0),
        _fake_term(_fake_element(3, "lumped_between", 7), -3.0e-10, -0.30),
    ], resid=float("nan"), trustworthy=False)


def fake_undefined_sensitivity():
    """A sensitivity scan in which one candidate could not be measured.

    None of the four real captures above can produce this: every candidate on
    every shipped fixture returns a finite delta, so the RANKING of a
    non-finite one was captured nowhere and the reference could not see it
    move.  It moved -- `sensitivity_table` keyed a NaN at `float("-inf")`, the
    SMALLEST key on an ascending sort, so the row that measured nothing led
    the table.

    Hand-built for the same reason `fake_nan_and_inf` is: a probe with no
    return path is ordinary in the field and expressible in none of the
    repo's fixtures.  The two undefined rows are there to pin that they sort
    last AMONG THEMSELVES in declaration order too, which is the stable sort
    doing its job; the three real ones straddle them in strength.
    """
    nan = complex(float("nan"), float("nan"))

    def row(label, alt, delta, db=float("nan")):
        return at.SensitivityResult("element", label, (0,), alt, "M", "H",
                                    1e-9, 1e-9 + delta, delta, db)

    return [
        row("ground port 2", "open", nan),
        row("ground port 4", "L=1 nH", 4.3e-12, 0.05),
        row("short 3-4", "open", nan),
        row("ground port 2", "R=50 Ohm", -5.0e-10, -6.07),
        row("ground port 4", "C=100 pF", -4.2e-14, -0.00),
    ]


def fake_complex_dec():
    """Quantity `Z`, i.e. the only shape with TWO value columns."""
    return fake_dec([
        _fake_term(None, complex(0.25, -1.5), 0.20, 0.01),
        _fake_term(_fake_element(0, "ground", 2), complex(-0.05, 3.25), 0.80,
                   -0.04),
    ], quantity="Z", unit="Ohm", total=complex(0.2, 1.75))


def fake_prov(**kw):
    import pkg_rlc_attrib_gui as ag
    base = dict(trace_id=3, trace_label="coil", file_label="pkg.s4p",
                run_number=7, spec_matches_run=True, victim="vic",
                aggressor="agg", quantity="M", requested_hz=5.6e9,
                actual_hz=5.6e9,
                spec_text="1 signal vic +\n3 signal agg +\n2 ground\n4 ground",
                units_mode="smart", signature=("sig",))
    base.update(kw)
    return ag.Provenance(**base)


class _GoneTrace:
    """The duck `staleness_text` reads for the 'spec has moved' branch."""

    def __init__(self, signature):
        self._sig = signature


# ============================================================================
# The registry
#
# THE ONE PLACE THAT KNOWS THE FORMATTERS' SIGNATURES.  When a signature moves,
# this file moves and the reference does not -- which is exactly what makes
# "the window's text did not move" a testable statement rather than a promise.
# ============================================================================

@dataclass(frozen=True)
class Case:
    name: str
    describe: str
    render: Callable[[], str]


def _join(lines) -> str:
    return "\n".join(str(x) for x in lines)


def _csv_text(records) -> str:
    """`csv_records` as the CSV it becomes -- one header row, then the rows.

    Rendered rather than dumped as JSON because the deliverable is a CSV file
    and a reordered or renamed field has to be visible in the diff as a moved
    column, not as a moved dictionary key.
    """
    import pkg_rlc_attrib_gui as ag
    out = [",".join(ag.CSV_FIELDS)]
    for rec in records:
        out.append(",".join(str(rec[k]) for k in ag.CSV_FIELDS))
    return "\n".join(out)


def _refusal(fn) -> str:
    """The message a refusal raises, or a loud marker when it does not raise."""
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return "(NO REFUSAL -- the call returned)"


def build_cases() -> list[Case]:
    """The registry.  Built inside a function so the heavy imports are lazy."""
    import pkg_rlc_attrib_gui as ag

    cases: list[Case] = []

    def add(name: str, describe: str, render):
        cases.append(Case(name, describe, render))

    # ---- the pure string helpers ---------------------------------------
    add("signed_str", "signed_str over every input class it distinguishes",
        lambda: _join(
            f"{raw!r:>16} -> {ag.signed_str(raw)!r}" for raw in (
                "", "0", "1.23", "-1.23", "+1.23", "−1.23", ".5", "-.5",
                "nan", "inf", "-inf", "--", "1.23e-05", "-1.23e-05",
                "2.00 nH", "-2.00 nH", "0.00 H", "abc", "-abc", "e-9")))

    add("render_table_model",
        "render_table's raw model: the lines, the (line, key, kind) rows and "
        "the width, including the U+2026 cap on a text column",
        lambda: _render_table_model(ag))

    add("candidate_parsing",
        "parse_candidate / candidate_list: what is accepted, and every "
        "refusal message a typed field can produce",
        lambda: _candidates(ag))

    add("quantity_refusals",
        "pkg_rlc_attrib's refusal-by-name for every non-decomposable "
        "quantity, plus sweep_mobius's own two",
        lambda: _quantity_refusals())

    # ---- contributions, per quantity ------------------------------------
    for q in ("M", "ImZ", "ReZ", "Z", "M/L_a", "k"):
        qs = q.replace("/", "_over_")
        add(f"contrib_coupled_{qs}",
            f"contributions_table on the shipped coupled_4port_diff case, "
            f"quantity {q}",
            lambda q=q: ag.contributions_table(dec("coupled_grounds", q)).text)

    add("contrib_coupled_M_aligned",
        "the same table in the ALIGNED units mode (one SI prefix per column, "
        "in the header)",
        lambda: ag.contributions_table(dec("coupled_grounds", "M"),
                                       "aligned").text)

    add("contrib_coupled_Z_aligned",
        "the complex two-column table in the aligned units mode",
        lambda: ag.contributions_table(dec("coupled_grounds", "Z"),
                                       "aligned").text)

    add("contrib_bare_only",
        "a spec with no element at all: the bare EM term on its own",
        lambda: ag.contributions_table(dec("coupled_diff_probes")).text)

    add("contrib_folded_tail",
        "the folded tail line -- a 1 GOhm lead beside an ideal ground",
        lambda: ag.contributions_table(dec("diff_ideal_and_weak")).text)

    add("contrib_folded_tail_unfolded",
        "the same decomposition with fold=False, which is what the exported "
        "report uses: still RANKED, nothing hidden",
        lambda: ag.contributions_table(dec("diff_ideal_and_weak"), "smart",
                                       fold=False).text)

    add("contrib_two_kinds",
        "a short and a ground in one table",
        lambda: ag.contributions_table(dec("diff_short_and_ground")).text)

    add("contrib_lumped_declared",
        "two lumped ground leads, element impedances as DECLARED",
        lambda: ag.contributions_table(dec("diff_lumped_grounds")).text)

    add("contrib_ground_model_declared",
        "two IDEAL grounds -- the base the two models below are measured "
        "against",
        lambda: ag.contributions_table(dec("diff_ideal_grounds")).text)

    add("contrib_ground_model_diag",
        "the same two grounds through INDEPENDENT 1 nH leads",
        lambda: ag.contributions_table(dec("diff_grounds_diag")).text)

    add("contrib_ground_model_shared",
        "the same two grounds through ONE SHARED 1 nH return -- the 9.6 dB "
        "choice, and the reason neither model may be a default",
        lambda: ag.contributions_table(dec("diff_grounds_shared")).text)

    add("contrib_singular_baseline",
        "coupled_4port_float: cond(Y) = 2.5e16, so the baseline is recovered "
        "by SVD and any out-of-range element is folded in and named",
        lambda: ag.contributions_table(dec("float_singular")).text)

    add("contrib_composed_no_gauge",
        "the composition WITHOUT the baseline gauge -- the far file's ground "
        "ball is exactly zero",
        lambda: ag.contributions_table(dec("composed_no_gauge")).text)

    add("contrib_composed_gauge",
        "the composition WITH BaselineLinks: the cross-file link is in the "
        "baseline and the far element is no longer zero",
        lambda: ag.contributions_table(dec("composed_gauge")).text)

    add("contrib_fake_nan_inf_zero",
        "a NaN term, a signed infinity and an exact zero in one table "
        "(hand-built)",
        lambda: ag.contributions_table(fake_nan_and_inf()).text)

    add("contrib_fake_withheld",
        "the empty-terms message: no per-element split, pointing at the "
        "reconciliation line (hand-built)",
        lambda: ag.contributions_table(fake_withheld()).text)

    add("contrib_fake_complex_aligned",
        "the hand-built complex decomposition in the aligned units mode",
        lambda: ag.contributions_table(fake_complex_dec(), "aligned").text)

    # ---- reconciliation --------------------------------------------------
    add("reconciliation_all_states",
        "reconciliation_verdict and reconciliation_line over all four header "
        "states plus every real spec",
        lambda: _reconciliation(ag))

    # ---- sensitivity -----------------------------------------------------
    add("sensitivity_coupled_default",
        "sensitivity_table over the six default candidates on the shipped "
        "coupled case",
        lambda: ag.sensitivity_table(sens("coupled_grounds")).text)

    add("sensitivity_coupled_aligned",
        "the same table in the aligned units mode",
        lambda: ag.sensitivity_table(sens("coupled_grounds"), "aligned").text)

    add("sensitivity_typed_candidates",
        "sensitivity_table over a TYPED candidate field, with the element "
        "kinds supplied so the rows keep their Ports & Roles colour",
        lambda: ag.sensitivity_table(
            sens("diff_lumped_grounds", candidates="open, ideal, R=50, L=1n"),
            kinds={i: e.kind
                   for i, e in enumerate(context("diff_lumped_grounds").elements)}
        ).text)

    add("sensitivity_ImZ",
        "sensitivity_table for ImZ, i.e. a different unit and a different "
        "column suffix",
        lambda: ag.sensitivity_table(sens("diff_short_and_ground", "ImZ")).text)

    add("sensitivity_fake_undefined_delta",
        "two candidates that could not be measured among three that could: "
        "an undefined delta ranks LAST, never first (hand-built)",
        lambda: ag.sensitivity_table(fake_undefined_sensitivity()).text)

    # ---- the detail pane -------------------------------------------------
    add("detail_bare_em",
        "detail_lines for the bare EM row (key None), including the wrapped "
        "return-path budget",
        lambda: _join(ag.detail_lines(dec("coupled_grounds"), None)))

    add("detail_no_selection_empty",
        "detail_lines with key None on a decomposition that has no bare term",
        lambda: _join(ag.detail_lines(fake_withheld(), None)))

    add("detail_element",
        "detail_lines for a declared element: current, transimpedance and the "
        "exactness sentence",
        lambda: _join(ag.detail_lines(dec("coupled_grounds"), 0)))

    add("detail_element_with_group_and_candidates",
        "the same, plus the multi-member group paragraph and the candidate "
        "rows underneath",
        lambda: _join(ag.detail_lines(
            dec("diff_lumped_grounds"), 0,
            sens=[r for r in sens("diff_lumped_grounds") if r.elements == (0,)],
            group=("conn row 3", 2))))

    add("detail_unknown_key",
        "detail_lines for an element index that is not in this decomposition",
        lambda: _join(ag.detail_lines(dec("coupled_grounds"), 99)))

    # ---- the sweep -------------------------------------------------------
    add("sweep_with_pole",
        "sweep_caption on the documented anti-resonance: the pole-free "
        "interval, the POLE line and the whole-half-line interval",
        lambda: _join(ag.sweep_caption(sweep("diff_lumped_grounds"))))

    add("sweep_with_pole_picture",
        "the SweepPicture behind that caption -- poles, interval, ylim, "
        "linthresh and the off-scale count",
        lambda: _picture_text(ag, sweep("diff_lumped_grounds")))

    add("sweep_no_pole_flat",
        "decap, whose residues are all exactly zero, so ideal == open and "
        "there is no pole at all",
        lambda: _join(ag.sweep_caption(sweep("decap_grounds"))))

    add("sweep_no_pole_flat_picture",
        "the SweepPicture for the constant sweep -- the SWEEP_Y_PAD_FLAT case",
        lambda: _picture_text(ag, sweep("decap_grounds")))

    add("sweep_bounded_t_max",
        "the same element swept over a BOUNDED range, which is what makes "
        "'physical' mean something",
        lambda: _join(ag.sweep_caption(
            sweep("diff_lumped_grounds", t_max=20e-9))))

    add("sweep_complex_quantity",
        "a sweep of the COMPLEX quantity Z, whose interval and endpoints are "
        "over the MAGNITUDE and are labelled |Z|",
        lambda: _join(ag.sweep_caption(
            sweep("coupled_grounds", quantity="Z"))))

    add("sweep_group",
        "both ground leads swept as ONE group, which is the |S| >= 2 case",
        lambda: _join(ag.sweep_caption(
            sweep("diff_lumped_grounds", element=(0, 1)))))

    add("sweep_note_text",
        "sweep_note_text: the three-line cap, the problems-first order and "
        "the '+N more' pointer",
        lambda: _sweep_notes(ag))

    # ---- provenance, staleness, header, stability ------------------------
    add("provenance_plain",
        "provenance_lines for an ordinary single-file run",
        lambda: _join(ag.provenance_lines(prov("coupled_grounds"))))

    add("provenance_snapped_and_edited",
        "the frequency snap note, the spec-edited marker and a non-default "
        "units mode",
        lambda: _join(ag.provenance_lines(fake_prov(
            requested_hz=5.6e9, actual_hz=5.5987e9, spec_matches_run=False,
            units_mode="aligned"))))

    add("provenance_ground_model_ignored",
        "a ground model that was NOT applied, with the parser's own note "
        "ahead of the standing explanation",
        lambda: _join(ag.provenance_lines(fake_prov(
            ground_model="shared:L=1n",
            ground_model_label="shared:L=1n  (IGNORED)",
            ground_model_applied=False,
            ground_model_notes=(
                "The ground model was ignored: this spec declares no shunt "
                "element, so there is no ground lead to model.",)))))

    add("provenance_composed",
        "a composed trace: the reference-node verdicts go BEFORE the sign "
        "convention and the spec",
        lambda: _join(ag.provenance_lines(fake_prov(
            file_label="die.s6p + pkg.s4p",
            reference_strip=("Reference-node check: F2 pkg.s4p is WELDED.",
                             True),
            reference_notes=(
                "Reference-node check:",
                "  F1 die.s6p    LIVE     the declared ground moves the answer.",
                "  F2 pkg.s4p    WELDED   the declared ground does NOT move "
                "the answer.")))))

    add("provenance_empty_spec",
        "an empty termination spec still prints '(empty)' rather than "
        "nothing at all",
        lambda: _join(ag.provenance_lines(fake_prov(spec_text=""))))

    add("staleness_all_states",
        "staleness_text's four states, and header_trace_text's cap",
        lambda: _staleness(ag))

    add("stability_all_states",
        "stability_offer and stability_line: the offer, the STABLE verdict, "
        "a moved ranking, an absent element and the no-ranking case",
        lambda: _stability(ag))

    # ---- the exports -----------------------------------------------------
    add("report_full",
        "report_text with everything present: sensitivity, refused "
        "candidates, the unabridged sweep caption, the stability verdict, "
        "notes and warnings",
        lambda: _report_full(ag))

    add("report_minimal",
        "report_text with nothing but the decomposition -- the 'Sensitivity: "
        "not run' branch is SAID, not omitted",
        lambda: ag.report_text(prov("coupled_grounds"),
                               dec("coupled_grounds")))

    add("report_withheld",
        "report_text over a withheld split (hand-built)",
        lambda: ag.report_text(fake_prov(), fake_withheld()))

    add("report_singular_fold",
        "report_text on the singular baseline: the folded elements are named "
        "in the notes, which is the half of the auto-recovery that is not "
        "optional -- a folded element has no term of its own",
        lambda: ag.report_text(prov("float_singular"), dec("float_singular")))

    add("report_nan_total",
        "report_text over the REAL NaN case: compute_z_matrix could not "
        "measure it, so the residual is NaN, the split is withheld and the "
        "warnings say so",
        lambda: ag.report_text(prov("float_one_coil_nan"),
                               dec("float_one_coil_nan")))

    add("report_aligned_units",
        "report_text in the aligned units mode",
        lambda: ag.report_text(prov("diff_lumped_grounds",
                                    units_mode="aligned"),
                               dec("diff_lumped_grounds"),
                               sens=sens("diff_lumped_grounds")))

    add("csv_records_full",
        "csv_records over a real decomposition plus its sensitivity rows",
        lambda: _csv_text(ag.csv_records(prov("coupled_grounds"),
                                         dec("coupled_grounds"),
                                         sens("coupled_grounds"))))

    add("csv_records_nan_inf",
        "csv_records over the hand-built NaN / infinity table: 'nan', 'inf' "
        "and '-inf' are written, never blanked",
        lambda: _csv_text(ag.csv_records(fake_prov(), fake_nan_and_inf())))

    return cases


# ---------------------------------------------------------------------- bits
#
# One helper per case that needs more than an expression.  They are separate
# functions rather than lambdas so that the registry above stays readable as a
# list of what is covered.

def _render_table_model(ag) -> str:
    cols = [ag.Column("element", "<", ag.ELEMENT_COL_CHARS),
            ag.Column("from", "<", ag.SOURCE_COL_CHARS),
            ag.Column("M"), ag.Column("share")]
    rows = [
        (None, "", ["bare EM coupling", "", "+251 pH", "+24.68%"]),
        (0, "ground", ["ground port 2", "conn row 1", "+506 pH", "+50.26%"]),
        (1, "lumped_to_gnd",
         ["a_very_long_element_name_indeed", "connection row 12",
          "−252 pH", "−25.06%"]),
    ]
    t = ag.render_table(cols, rows)
    out = list(t.lines)
    out.append("")
    out.append(f"width = {t.width}")
    out.append(f"rows  = {t.rows}")
    out.append("")
    out.append("--- text property ---")
    out.append(t.text)
    return "\n".join(out)


def _candidates(ag) -> str:
    omega = 2.0 * math.pi * 5.0e9
    out = ["accepted:"]
    for txt in ("open", "ideal", "short", "0", "R=50", "L=1n",
                "R=0.1 L=1n", "C=100p", "R=1m L=50p C=2p"):
        alt = ag.parse_candidate(txt, omega)
        out.append(f"  {txt!r:>20} -> name {alt.name!r}  z {alt.z!r}")
    out.append("")
    out.append("refused, one message each:")
    for txt in ("", "   ", "R=5 m", "50", "R", "banana", "R=5 L=1n C"):
        out.append(f"  {txt!r:>20} -> " + _refusal(
            lambda t=txt: ag.parse_candidate(t, omega)))
    out.append("")
    out.append("candidate_list('open, R=5 m, ideal, banana'):")
    alts, problems = ag.candidate_list("open, R=5 m, ideal, banana", omega)
    out.append(f"  alternatives: {[a.name for a in alts]}")
    for p in problems:
        out.append(f"  problem: {p}")
    out.append("")
    out.append("candidate_list('') -> " + repr(ag.candidate_list("", omega)))
    return "\n".join(out)


def _quantity_refusals() -> str:
    out = ["decompose, refused BY NAME:"]
    ctx = context("coupled_grounds")
    for q in ("C_c", "Cc", "Q", "|Z|", "absZ", "dB", "M/L_a_dB", "k_dB",
              "banana"):
        out.append(f"  {q!r:>12} -> " + _refusal(
            lambda q=q: at.decompose(ctx, "vic", "agg", q)))
    out.append("")
    out.append("sweep_mobius, refused BY NAME (a curve has no single "
               "configuration to take a scale from):")
    for q in ("k", "M/L_a"):
        out.append(f"  {q!r:>12} -> " + _refusal(
            lambda q=q: at.sweep_mobius(ctx, "vic", "agg", 0, quantity=q)))
    out.append("")
    out.append("accepted, for contrast:")
    for q in ("Z", "ReZ", "ImZ", "M", "M/L_a", "k", "z_ab", "im", "m"):
        d = at.decompose(ctx, "vic", "agg", q)
        out.append(f"  {q!r:>12} -> quantity {d.quantity!r} unit {d.unit!r}")
    return "\n".join(out)


def _reconciliation(ag) -> str:
    out = ["hand-built states:"]
    for label, d in (("healthy", fake_healthy()),
                     ("above floor", fake_above_floor()),
                     ("withheld", fake_withheld()),
                     ("not comparable", fake_not_comparable()),
                     ("complex quantity", fake_complex_dec()),
                     ("NaN residual", fake_nan_and_inf())):
        verdict, ok = ag.reconciliation_verdict(d)
        out.append(f"  {label}")
        out.append(f"    verdict : {verdict!r}  ok={ok}")
        out.append(f"    line    : {ag.reconciliation_line(d)}")
    out.append("")
    out.append("every real spec in the registry:")
    for spec in SPECS:
        d = dec(spec.key)
        verdict, ok = ag.reconciliation_verdict(d)
        out.append(f"  {spec.key}")
        out.append(f"    verdict : {verdict!r}  ok={ok}")
        out.append(f"    line    : {ag.reconciliation_line(d)}")
    return "\n".join(out)


def _picture_text(ag, sw) -> str:
    pic = ag.sweep_picture(sw)
    out = [f"poles       : {len(pic.poles)}"]
    for p in pic.poles:
        out.append(f"  index {p.index}  t={p.t!r}  t_lo={p.t_lo!r}  "
                   f"t_hi={p.t_hi!r}  visible={p.visible}")
    out.append(f"drawn       : {len(pic.drawn)}")
    out.append(f"clusters    : {len(pic.clusters)}")
    for c in pic.clusters:
        out.append(f"  label {ag.pole_label(c, sw.param_unit)!r}  "
                   f"span {ag.pole_span(c, sw.param_unit)!r}")
    out.append(f"interval    : {pic.interval!r}")
    out.append(f"ylim        : {pic.ylim!r}")
    out.append(f"linthresh   : {pic.linthresh!r}")
    out.append(f"linear_ticks: {pic.linear_ticks}")
    out.append(f"n_offscale  : {pic.n_offscale}")
    out.append("")
    out.append("si_tick over the y limits and zero:")
    for v in (pic.ylim[0], 0.0, pic.ylim[1], float("nan"), float("inf")):
        out.append(f"  {v!r:>26} -> {ag.si_tick(v, sw.unit)!r}")
    return "\n".join(out)


def _sweep_notes(ag) -> str:
    caption = ag.sweep_caption(sweep("diff_lumped_grounds"))
    problems = ["'R=5 m': 'm' has no '='. A candidate is 'open', 'ideal', or "
                "R=…/L=…/C=… with no spaces inside a value — 'R=5 m' would "
                "silently mean 5 Ω, not 5 mΩ"]
    out = ["the caption alone, default cap:",
           ag.sweep_note_text(caption),
           "",
           "with a refused candidate FIRST:",
           ag.sweep_note_text(caption, problems),
           "",
           "capped at 2 lines:",
           ag.sweep_note_text(caption, problems, max_lines=2),
           "",
           "capped at 1 line:",
           ag.sweep_note_text(caption, problems, max_lines=1),
           "",
           "nothing to say:",
           repr(ag.sweep_note_text([], [])),
           "",
           "blank lines are dropped, not counted:",
           repr(ag.sweep_note_text(["", "  ", "one line"], []))]
    return "\n".join(out)


def _staleness(ag) -> str:
    p = fake_prov()
    live = _GoneTrace(p.signature)
    moved = _GoneTrace(("something", "else"))

    def sig(trace):
        return trace._sig

    # `staleness_text` reaches `spec_signature`, which reaches
    # `pkg_rlc_gui._config_signature`.  The duck above carries the tuple
    # directly, so the call is monkeypatched for the duration -- the subject
    # here is the SENTENCE, not how the signature is computed.
    real = ag.spec_signature
    ag.spec_signature = sig
    try:
        out = ["everything agrees:",
               "  " + repr(ag.staleness_text(p, live, True)),
               "",
               "the trace has been REMOVED:",
               "  " + repr(ag.staleness_text(p, live, False)),
               "",
               "the spec has MOVED since (this outranks the one below):",
               "  " + repr(ag.staleness_text(p, moved, True)),
               "",
               "computed from the spec AS EDITED, not from the run:",
               "  " + repr(ag.staleness_text(
                   fake_prov(spec_matches_run=False), live, True)),
               "",
               "both true at once -- the moved signature wins:",
               "  " + repr(ag.staleness_text(
                   fake_prov(spec_matches_run=False), moved, True)),
               "",
               "header_trace_text, at and past the cap:"]
        for label, fl in (("short", "pkg.s4p"),
                          ("exactly 18", "abcdefghijklmnopqr"),
                          ("19, so elided", "abcdefghijklmnopqrs")):
            out.append(f"  {label}: "
                       + repr(ag.header_trace_text(
                           fake_prov(trace_label=fl, file_label=fl))))
    finally:
        ag.spec_signature = real
    return "\n".join(out)


def _stability(ag) -> str:
    freqs = [4.0e9, 4.5e9, 5.0e9, 5.5e9, 6.0e9]
    stable = [{"ground port 2": 1, "ground port 4": 2}] * 5
    swapped = ([{"ground port 2": 1, "ground port 4": 2}] * 2
               + [{"ground port 2": 2, "ground port 4": 1}] * 3)
    absent = ([{"ground port 2": 1, "port 3 -> gnd": 2}] * 3
              + [{"ground port 2": 1}] * 2)
    many = []
    for k in range(5):
        many.append({f"ground port {p}": (p if k == 0 else 9 - p)
                     for p in range(2, 8)})
    out = ["stability_offer, default:",
           "  " + ag.stability_offer(),
           "",
           "stability_offer on a 153-port file:",
           "  " + ag.stability_offer(5, 153),
           "",
           "one frequency only -- falls back to the offer:",
           "  " + ag.stability_line(freqs[:1], stable[:1]),
           "",
           "STABLE:",
           "  " + ag.stability_line(freqs, stable),
           "",
           "two elements change places:",
           "  " + ag.stability_line(freqs, swapped),
           "",
           "an element goes ABSENT:",
           "  " + ag.stability_line(freqs, absent),
           "",
           "more than three moved -- the tail is counted:",
           "  " + ag.stability_line(freqs, many),
           "",
           "no ranking at any frequency:",
           "  " + ag.stability_line(freqs, [{}] * 5)]
    return "\n".join(out)


def _report_full(ag) -> str:
    key = "diff_lumped_grounds"
    sw = sweep(key)
    d = dec(key)
    d = at.Decomposition(**{**d.__dict__,
                            "notes": list(d.notes) + ["a registry note"],
                            "warnings": list(d.warnings) + ["a registry warning"]})
    return ag.report_text(
        prov(key), d, sens=sens(key),
        stability=ag.stability_line(
            [4.5e9, 5.0e9], [{"port 3 -> gnd": 1, "port 4 -> gnd": 2},
                             {"port 3 -> gnd": 2, "port 4 -> gnd": 1}]),
        sweep=ag.sweep_caption(sw),
        problems=["'R=5 m': 'm' has no '='. A candidate is 'open', 'ideal', "
                  "or R=…/L=…/C=… with no spaces inside a value"])


# ============================================================================
# Capture / read
# ============================================================================

def case_path(name: str) -> Path:
    return REFERENCE_DIR / f"{name}.txt"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def env_now() -> dict[str, str]:
    """The three fields the manifest stamps -- the `_golden_capture` set."""
    return {
        "numpy": np.__version__,
        "python": platform.python_version(),
        "platform": platform.system(),
    }


def read_case(name: str) -> str:
    """One captured case, verbatim.

    `newline=""` on both sides: the text lives in the file as itself rather
    than inside a JSON string escape, so nothing may be allowed to translate
    its line endings on the way in or out.  A reference that flips CRLF per
    machine is a diff nobody asked for, and here it would also be a failure.
    """
    with open(case_path(name), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_case(name: str, text: str) -> None:
    with open(case_path(name), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read_manifest() -> dict:
    with open(REFERENCE_DIR / MANIFEST_NAME, "r", encoding="utf-8") as fh:
        return json.load(fh)


def render_all() -> list[tuple[Case, str]]:
    """Every case rendered, in registry order."""
    return [(c, c.render()) for c in build_cases()]


def main() -> int:
    rendered = render_all()
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    keep = {MANIFEST_NAME}
    entries = []
    for case, text in rendered:
        write_case(case.name, text)
        keep.add(f"{case.name}.txt")
        entries.append({
            "name": case.name,
            "describe": case.describe,
            "sha256": sha256(text),
            "chars": len(text),
            "lines": text.count("\n") + 1 if text else 0,
        })

    manifest = {"__env__": env_now(), "cases": entries}
    with open(REFERENCE_DIR / MANIFEST_NAME, "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    stale = sorted(p.name for p in REFERENCE_DIR.iterdir()
                   if p.is_file() and p.name not in keep)
    print(f"wrote {len(entries)} cases to {REFERENCE_DIR}")
    for e in entries:
        print(f"  {e['name']:<38} {e['lines']:>4} lines  "
              f"{e['chars']:>6} chars  {e['sha256'][:12]}")
    if stale:
        print("\nSTALE files still in the reference directory (a case was "
              "renamed or removed -- delete them in the same commit):")
        for name in stale:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
