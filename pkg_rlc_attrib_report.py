"""
pkg_rlc_attrib_report.py  --  the attribution report as TEXT.

Split out of `pkg_rlc_extractor.py`, verbatim apart from one deliberate
change: every section below RETURNS its lines instead of printing them.  The
CLI prints what it is given; nothing here writes to a stream, opens a file or
knows that a terminal exists, which is what lets
`tests/fixtures/cli_reference/` pin the whole surface with no subprocess.

WHY THE NAMES STILL SAY `print`.  `_attr_print_context`, `_cold_print_screen`
and their eleven siblings are called by those names in the CLI, in
`tests/_cli_capture.py`'s docstring and in three of this repo's own design
notes.  They RETURN `list[str]` now; the prefix is historical and a rename is
a separate, greppable change.  Read `_attr_print_x` as "the lines section x
would print".

WHAT ELSE IS HERE, and why it is here rather than in `pkg_rlc_extractor`:

  * `_attr_series_impedance` / `_attr_alternative` / `_attr_ground_model` /
    `_attr_zt` -- the SHARED data-shaping step.  `--attribute-ground-model`
    and the Attribution window's Grounds field are one spelling on purpose
    (CLAUDE.md: "one spelling shared with --attribute-ground-model so the two
    cannot drift"), and `pkg_rlc_attrib_gui.parse_ground_model` used to reach
    it by importing `pkg_rlc_extractor` INSIDE the function -- a lazy import
    that existed only to dodge a cycle through `main()`.  With the parser
    here, both callers import it at module level and the cycle is gone.
  * the CLI's own SPELLING of the shared formatters (`_trunc`'s '~' rather
    than U+2026, `_fmt_complex`'s 'a + jb' rather than the pane's 'a+bj') and
    the caps every ranked section is floored at.
  * the two CSV RECORD shapers (`_attr_row` / `_cold_row`).  A record is data
    shaping, not a file format: `_write_attrib_csv` and `_write_cold_csv`
    stay in the CLI, where the path, the flags and the header comments are.

WHAT IS DELIBERATELY NOT HERE.  `pkg_rlc_attrib_gui`'s own formatters
(`contributions_table`, `sensitivity_table`, `detail_lines`, `report_text`,
`csv_records`) render the SAME analysis for a strip that is 48 characters wide
at 150% DPI, against this file's 95.  They are not the same block: the window
folds a negligible tail, signs with U+2212, prefixes a swatch and shows one
quantity, while section 1 here groups by provenance and prints Z and M side by
side with the share/quadrature pair.  Sharing the RENDERING would mean picking
a winner between two measured constraints, so both stand.  What they share is
`pkg_rlc_attrib`'s dataclasses and its one-string constants
(`SIGN_CONVENTION_TEXT`, `COMPOSED_BASELINE_TEXT`, `Bracket.caveat`,
`ColdStart.blind_spot`), which are single strings precisely so every export
carries them verbatim -- do not reflow one into a formatter.

Imports `pkg_rlc_attrib`, `pkg_rlc_core` and `pkg_rlc_report`, and nothing
else.  No tkinter and no matplotlib: this module is on the CLI's import path,
and `tests/test_attrib_cli*.py` are in the runner's `FAST_MODULES` on exactly
that property.
"""

from __future__ import annotations

import math
import textwrap

import numpy as np

import pkg_rlc_attrib as attrib
from pkg_rlc_core import (
    RECIPROCITY_WARN,
    collapse_ports,
    format_freq,
    format_si,
    parse_kv_rlc_params,
    y_series_rlc,
)
from pkg_rlc_report import (
    _fmt_plain,
    _render_columns,
    _trunc_str,
)


# ============================================================================
# Formatting helpers
# ============================================================================
#
# NONE OF THE ARITHMETIC BELOW IS THIS FILE'S.  Truncation, the plain-number
# format and the monospace table live in `pkg_rlc_report`, where the results
# pane reads them too; what is here is the CLI's own SPELLING of each, passed
# to the shared formatter as an argument.

_NAME_W = 16          # measurement-port names are truncated to this for tables


def _trunc(s: str, w: int) -> str:
    """`_trunc_str` with the CLI's cut marker."""
    return _trunc_str(s, w, "~")


def _fmt_complex(z: complex, sig: int = 4) -> str:
    """'a + jb' / 'a - jb' with `sig` significant digits."""
    re = z.real
    im = z.imag
    sign = "-" if (im < 0.0) else "+"
    return f"{re:.{sig}g} {sign} j{abs(im):.{sig}g}"


#: The plain-number format, which is `pkg_rlc_report._fmt_plain` exactly -- it
#: was the same two lines in both files.  Kept under the CLI's own name
#: because every `_attr_print_*` and `_cold_print_*` section calls it that.
_fmt_num = _fmt_plain


def _fmt_db(value: float, sig: int = 4) -> str:
    return _fmt_num(value, sig) + " dB"


def _table_lines(headers: list[str], rows: list[list[str]],
                 aligns: list[str], indent: str = "  ",
                 ) -> list[str]:
    """The CLI's monospace table: `_render_columns`, ruled.

    The argument ORDER is this file's own (headers, rows, aligns) because
    seventeen call sites in the attribution and cold-start sections pass it
    that way.  `pkg_rlc_extractor._print_table` is this printed, which is what
    the compose report and the coupling report still call.
    """
    return _render_columns(headers, aligns, rows, lead=indent, rule="-")



#: Rows of the sensitivity ranking printed to the terminal.  The CSV has NO
#: cap, which is the only thing that makes the "(see --attribute-csv)" pointer
#: true -- the same split the coupling report's ranked pair list makes, where
#: `_write_coupling_csv` enumerates every pair and the printed list is floored.
ATTR_RANK_ROWS = 20

#: Element groups given a Mobius sweep.  With --attribute-group row this CLI
#: can only ever produce three groups (--mport / --gnd / --short), so the cap
#: bites only in flat or name mode, where a 60-ball package would otherwise
#: print 60 sweeps of six lines each.
ATTR_SWEEP_GROUPS = 3

#: Elements entered into the PAIRWISE non-additivity scan, strongest first.
#: Unlike ATTR_RANK_ROWS this cap is computational rather than cosmetic -- the
#: scan is O(k^2) full re-solves -- so it applies to the CSV too and both say
#: so.  8 elements is 28 pairs.
ATTR_PAIR_POOL = 8

#: Group-level joint effects printed.  Same reason as ATTR_SWEEP_GROUPS.
ATTR_GROUP_ROWS = 8

# There is deliberately NO cap on the cumulative curve: its k = 1, 2, 4, ...
# schedule is the engine's default and is logarithmic in the element count, so
# a 60-ball package is seven rows.

_ATTR_LINE = "=" * 74
_ATTR_RULE = "-" * 74


def _attr_section(title: str, subtitle: str = "") -> list[str]:
    """A numbered section head, with its explanatory line UNDER the rule."""
    out: list[str] = []
    out.append("\n" + _ATTR_RULE)
    out.append(title)
    out.append(_ATTR_RULE)
    if subtitle:
        for line in _attr_wrap(subtitle):
            out.append(line)
        out.append("")
    return out


def _attr_wrap(text: str, indent: str = "  ", width: int = 78,
               hang: str | None = None) -> list[str]:
    """
    Wrap one of pkg_rlc_attrib's sentence-long notes to terminal width.

    `hang` is the continuation indent when it differs from the first line's --
    a bulleted caveat whose second line starts under the '*' reads as a second
    bullet, which is the one thing a list of three must not do.
    """
    # break_on_hyphens=False: textwrap's default splits "last-assignment-wins"
    # across two lines, and a hyphenated technical term broken at the margin
    # reads as two different words.
    return textwrap.wrap(text, width=width, initial_indent=indent,
                         subsequent_indent=(indent if hang is None else hang),
                         break_on_hyphens=False) or [indent.rstrip()]


def _attr_series_impedance(spec: str, omega: float) -> tuple[complex | None, str]:
    """
    'R=0.5,L=1n' -> (its series impedance at `omega`, a label).  None is OPEN.

    Deliberately the SAME R/L/C spelling the Mode 5 DSL uses
    (`parse_kv_rlc_params` + `y_series_rlc`), so one syntax covers a lumped
    termination inside a spec and a what-if candidate here, and in particular
    so that `M` keeps meaning Mega on both sides of the tool.

    OPEN comes back as None rather than as a very large number: pkg_rlc_attrib
    removes the element from the network entirely, which is a different -- and
    exact -- thing from stamping 1e12 ohms.

    A comma-separated field with no '=' is REFUSED.  `parse_kv_rlc_params`
    silently DROPS such a token, so 'R=5,m' would compute 5 ohm where 5
    milliohm was meant: the same factor-of-1000 trap core's `_rlc_tokens`
    refuses, arriving here through a different door.  A bare number is refused
    for the neighbouring reason -- '50' does not say whether it is ohms,
    henries or farads, and guessing would be silently wrong rather than loudly
    wrong.
    """
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("empty termination spec")
    low = raw.lower()
    if low == "open":
        return None, "open"
    if low in ("gnd", "ground", "ideal", "short"):
        return 0j, "ideal"
    fields = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in fields if "=" not in t]
    if bad:
        raise ValueError(
            f"'{raw}': field(s) {', '.join(repr(b) for b in bad)} carry no "
            "'='. A candidate termination is 'open', 'ideal', or R=/L=/C= "
            "fields separated by commas -- 'R=50', 'L=0.3n', 'R=0.5,L=1n', "
            "'C=100p'. A bare number is refused because it does not say "
            "whether it means ohms, henries or farads")
    try:
        params = parse_kv_rlc_params(fields)
    except ValueError as e:
        # parse_kv_rlc_params names the offending KEY but not the spec it came
        # from, and a repeatable flag can carry six of them.
        raise ValueError(f"'{raw}': {e}") from None
    with np.errstate(divide="ignore", invalid="ignore"):
        y = complex(np.asarray(y_series_rlc(**params)(np.array([omega])))[0])
    label = ",".join(fields)
    if y == 0:
        return None, label                      # infinite impedance == open
    if not (math.isfinite(y.real) and math.isfinite(y.imag)):
        return 0j, label                        # R=L=0, no C: a perfect short
    return 1.0 / y, label


def _attr_alternative(spec: str, omega: float) -> attrib.Alternative:
    z, label = _attr_series_impedance(spec, omega)
    return attrib.Alternative(label, z)


def _attr_ground_model(spec: str,
                       omega: float) -> tuple[str, complex | None, str]:
    """'diag' | 'diag:SPEC' | 'shared:SPEC'  ->  (kind, impedance, label)."""
    raw = (spec or "").strip()
    low = raw.lower()
    if low == "diag":
        return "diag", None, "diag (as declared)"
    for kind in ("diag", "shared"):
        if low.startswith(kind + ":"):
            body = raw[len(kind) + 1:].strip()
            if not body:
                raise ValueError(
                    f"'{raw}': '{kind}:' needs an impedance after the colon, "
                    f"e.g. '{kind}:L=1n'")
            z, label = _attr_series_impedance(body, omega)
            if z is None:
                raise ValueError(
                    f"'{raw}': an OPEN lead is not a ground model -- an "
                    "element that is not in the network has no impedance to "
                    "give itself or to share. Drop the port from --gnd "
                    "instead")
            return kind, z, f"{kind}:{label}"
    raise ValueError(
        f"'{raw}' is not one of 'diag', 'diag:SPEC' or 'shared:SPEC' (SPEC "
        "being an R/L/C termination such as L=1n)")


def _attr_zt(ctx, kind: str,
             z: complex | None) -> tuple[np.ndarray | None, list[str]]:
    """
    The (m, m) element impedance matrix this ground model asks for, or None to
    keep the one the spec itself declares.

    Only the SHUNT elements are touched.  `termination_impedance_shared_return`
    assumes every element it is given is a ball sharing the return plane, so
    handing it the whole matrix would give a `short_to` a return impedance and
    quietly stop it being a short; the dense block is therefore built on the
    shunt sub-block and scattered back in.  The builder is still the one place
    the dense form is written down.
    """
    notes: list[str] = []
    if z is None:
        return None, notes
    shunts = [i for i, e in enumerate(ctx.elements) if e.is_shunt]
    if not shunts:
        notes.append(
            "The ground model was ignored: this spec declares no shunt "
            "element (no --gnd port, no lumped termination to ground), so "
            "there is no ground lead to model.")
        return None, notes
    if kind == "shared" and len(shunts) < 2:
        notes.append(
            "'shared' with a single shunt element is the same network as "
            "'diag' with the same impedance -- there is nothing for it to "
            "share the return with.")
    Zt = np.array(ctx.Zt, dtype=complex)
    if kind == "diag":
        for i in shunts:
            Zt[i, i] = z
        return Zt, notes
    sub = attrib.termination_impedance_shared_return(
        [complex(Zt[i, i]) for i in shunts], z)
    Zt[np.ix_(shunts, shunts)] = sub
    return Zt, notes


def _attr_snap(freqs: np.ndarray, f_hz: float) -> float:
    """
    The grid point `build_context` will land on -- the same argmin.

    Used so that a candidate termination is EVALUATED at the omega it is later
    APPLIED at.  Off by one grid point they would differ by the sweep step,
    which on a coarse file is not small.
    """
    fa = np.asarray(freqs, dtype=float)
    return float(fa[int(np.argmin(np.abs(fa - float(f_hz))))])


# ---------------------------------------------------------------------------
# The CSV.  One long table with a `section` column: the records have genuinely
# different shapes (a term, a swap, a sweep interval), and a single header that
# csv.DictReader round-trips beats five tables nobody can join.
# ---------------------------------------------------------------------------

_ATTR_CSV_FIELDS = [
    "section", "freq_GHz", "victim", "aggressor", "quantity", "unit",
    "group", "element", "alternative", "value_re", "value_im",
    "delta_re", "delta_im", "delta_dB", "share_inline", "share_quad",
    "current_re", "current_im", "extra",
]


def _e(x: float) -> str:
    """A float for the CSV: %.6e, and nan/inf spelled out rather than blank."""
    v = float(x)
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return f"{v:.6e}"


def _attr_row(section: str, **kw) -> dict:
    """One CSV record.  An unknown field name RAISES rather than vanishing."""
    row = {f: "" for f in _ATTR_CSV_FIELDS}
    for k in kw:
        if k not in row:
            raise KeyError(f"unknown attribution CSV field '{k}'")
    row["section"] = section
    row.update(kw)
    return row



# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _attr_print_context(ctx, model_label: str, group_mode: str,
                        notes: list[str]) -> list[str]:
    """The header: what was measured, how well conditioned it is, what moved."""
    out: list[str] = []
    out.append(f"  frequency        : {format_freq(ctx.freq_hz)}"
               + ("" if ctx.freq_hz == ctx.requested_hz
                  else f"   (requested {format_freq(ctx.requested_hz)}; snapped to "
                       "the file's grid)"))
    out.append("  measurement ports: "
               + "   ".join(f"{i + 1} '{n}'" for i, n in enumerate(ctx.port_names))
               + "      <- --attribute takes either the name or the number")
    out.append(f"  ground model     : {model_label}")
    out.append(f"  element grouping : {group_mode}")
    out.append(f"  elements         : {ctx.n_elements} declared, "
               f"{len(ctx.groups)} group(s)")
    out.append(f"  conditioning     : cond(Ybase) {ctx.cond_Ybase:.3g}   "
               f"cond(G) {ctx.cond_G:.3g}   "
               f"reciprocity |r_a - p_a|/|p_a| {ctx.reciprocity_rel:.3g}")
    if ctx.reciprocity_rel > RECIPROCITY_WARN:
        for line in _attr_wrap(
                "WARN: the baseline is not reciprocal to within "
                f"{RECIPROCITY_WARN:g}. r_a is solved separately from p_a so "
                "the numbers below are still right, but a non-reciprocal EM "
                "solve is worth explaining before a budget is written against "
                "it.", "    "):
            out.append(line)
    for el, why in ctx.dropped:
        for line in _attr_wrap(f"dropped: {el.describe()} -- {why}", "    ",
                               hang="      "):
            out.append(line)
    for n in list(notes) + list(ctx.notes):
        for line in _attr_wrap("note: " + n, "    ", hang="      "):
            out.append(line)
    for w in ctx.warnings:
        for line in _attr_wrap("WARN: " + w, "    ", hang="      "):
            out.append(line)
    for w in ctx.core_warnings:
        for line in _attr_wrap("WARN (compute_z_matrix): " + w, "    ",
                               hang="      "):
            out.append(line)
    return out


def _attr_print_terms(decZ, decM, csv_rows: list[dict]) -> list[str]:
    """
    The decomposition table, grouped by provenance and ordered by group weight.

    Both quantities are printed because they answer different halves of the
    question.  Z_ab is what actually decomposes -- the complex terms are what
    add up -- and the share/quadrature pair (requirement 7) only means anything
    there: a term at 90 degrees to the total inflates every magnitude-based
    cancellation measure while being harmless.  M is the number the budget is
    written in, and for it the quadrature share is identically zero because
    M is real by construction.
    """
    out: list[str] = []
    if not decZ.terms:
        out.append("  (per-element split WITHHELD -- see the reconciliation below)")
        return out
    if len(decZ.terms) != len(decM.terms):        # pragma: no cover
        out.append("  (per-element split unavailable: the Z and M decompositions "
                   "disagree about the element list)")
        return out

    # group -> [(term_Z, term_M)], with the bare EM term in its own pseudo-group
    # because it belongs to no declaration: it is what is left when every
    # non-probe port is open.
    buckets: dict[str, list[tuple]] = {}
    for tz, tm in zip(decZ.terms, decM.terms):
        key = "(baseline: bare EM)" if tz.element is None else tz.element.source
        buckets.setdefault(key, []).append((tz, tm))

    def weight(item) -> float:
        return abs(sum(tz.contribution for tz, _ in item[1]))

    order = sorted(buckets.items(), key=weight, reverse=True)

    rows: list[list[str]] = []
    for label, items in order:
        for tz, tm in items:
            rows.append([
                label,
                tz.label,
                "--" if not math.isfinite(abs(tz.current))
                else format_si(abs(tz.current), "A"),
                _fmt_complex(tz.contribution, 4),
                format_si(tm.contribution.real, decM.unit),
                "--" if not math.isfinite(tz.share_inline)
                else f"{100 * tz.share_inline:.2f}%",
                "--" if not math.isfinite(tz.share_quad)
                else f"{100 * tz.share_quad:.2f}%",
            ])
    out += _table_lines(["group", "element", "|I_e|", "Z term (Ω)", "M term",
                         "share", "quad"], rows,
                        ["<", "<", ">", ">", ">", ">", ">"])

    if len(order) > 1:
        out.append("\n  By group:")
        grows = []
        for label, items in order:
            zt = sum(tz.contribution for tz, _ in items)
            mt = sum(tm.contribution for _, tm in items)
            sh = sum(tz.share_inline for tz, _ in items)
            grows.append([label, str(len(items)), _fmt_complex(zt, 4),
                          format_si(mt.real, decM.unit),
                          "--" if not math.isfinite(sh) else f"{100 * sh:.2f}%"])
        out += _table_lines(["group", "n", "Z total (Ω)", "M total", "share"], grows,
                            ["<", ">", ">", ">", ">"])

    for tz, tm in zip(decZ.terms, decM.terms):
        csv_rows.append(_attr_row(
            "term", freq_GHz=_e(decZ.freq_hz / 1e9),
            victim=decZ.victim, aggressor=decZ.aggressor,
            quantity="Z", unit="Ohm",
            group=("" if tz.element is None else tz.element.source),
            element=tz.label,
            value_re=_e(tz.contribution.real), value_im=_e(tz.contribution.imag),
            share_inline=_e(tz.share_inline), share_quad=_e(tz.share_quad),
            current_re=_e(tz.current.real), current_im=_e(tz.current.imag),
            extra=f"trans_z={tz.trans_z}"))
        csv_rows.append(_attr_row(
            "term", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity="M", unit="H",
            group=("" if tm.element is None else tm.element.source),
            element=tm.label,
            value_re=_e(tm.contribution.real), value_im=_e(tm.contribution.imag),
            share_inline=_e(tm.share_inline), share_quad=_e(tm.share_quad)))
    return out


def _attr_print_reconciliation(decZ, decM) -> list[str]:
    out: list[str] = []
    out.append(f"  Z_ab  total (compute_z_matrix) : "
               f"{_fmt_complex(decZ.total_reference, 6)} Ω")
    out.append(f"  Z_ab  total (sum of the terms) : "
               f"{_fmt_complex(decZ.total_sum, 6)} Ω")
    out.append(f"  M     total (compute_z_matrix) : "
               f"{format_si(decM.total_reference.real, decM.unit)}")
    out.append(f"  residual {decZ.residual_rel:.3g} relative, against an achievable "
               f"floor of {decZ.residual_floor:.3g}")
    out.append(f"    (the floor is what cond(Ybase)={decZ.cond_Ybase:.3g} and "
               f"cond(H)={decZ.cond_H:.3g} allow in double precision; it is not a "
               "quality target)")
    if decZ.residual_rel <= decZ.residual_floor:
        out.append("    the two algorithms agree to within it -- the split above "
                   "may be read as exact")
    # C_c is a first-class reading of this tool whenever the coupling is
    # capacitive, and it is NOT decomposable (it is a reciprocal of the
    # decomposed quantity).  Print the total and quote the engine's own reason
    # rather than leaving a hole where the number the user came for should be.
    im = decZ.total_reference.imag
    om = 2.0 * math.pi * decZ.freq_hz
    if om != 0.0 and im != 0.0:
        c_c = -1.0 / (om * im)
        if im < 0.0:
            out.append(f"\n  Im(Z_ab) < 0: the coupling is CAPACITIVE here and "
                       f"C_c = {format_si(c_c, 'F')} is the reading, not M "
                       f"({format_si(decM.total_reference.real, decM.unit)}).")
        else:
            out.append(f"\n  C_c = {format_si(c_c, 'F')} (negative: Im(Z_ab) > 0, "
                       "so the coupling is inductive here and M is the reading).")
        for line in _attr_wrap(
                "C_c has NO per-element split: "
                + attrib.NON_DECOMPOSABLE["C_c"] + ".", "    "):
            out.append(line)
    return out


def _attr_print_sensitivity(rows: list, csv_rows: list[dict], dec) -> list[str]:
    """
    The ranking: every element against every candidate, strongest first.

    An UNDEFINED delta sorts last rather than in the middle, the same rule
    `rank_coupling_pairs` follows: NaN is a missing measurement, not a small
    number, and letting it float to wherever 0.0 lands would bury a real
    result under a row that says nothing.
    """
    out: list[str] = []
    if not rows:                                             # pragma: no cover
        return out
    ranked = sorted(rows, key=lambda r: (0 if math.isfinite(r.abs_delta) else 1,
                                         -r.abs_delta
                                         if math.isfinite(r.abs_delta) else 0.0))
    shown = ranked[:ATTR_RANK_ROWS]
    trows = []
    for r in shown:
        trows.append([
            r.label, r.alternative,
            format_si(r.new_value.real, r.unit),
            format_si(r.delta.real, r.unit),
            _fmt_db(r.delta_db),
        ])
    out += _table_lines(["element", "candidate", f"{rows[0].quantity} after",
                         "Δ", "Δ (dB)"], trows, ["<", "<", ">", ">", ">"])
    if len(ranked) > len(shown):
        out.append(f"  ... {len(ranked) - len(shown)} more rows "
                   "(all of them are in --attribute-csv, which has no cap)")
    for r in rows:
        csv_rows.append(_attr_row(
            "sensitivity", freq_GHz=_e(dec.freq_hz / 1e9),
            victim=dec.victim, aggressor=dec.aggressor,
            quantity=r.quantity, unit=r.unit, element=r.label,
            alternative=r.alternative,
            value_re=_e(r.new_value.real), value_im=_e(r.new_value.imag),
            delta_re=_e(r.delta.real), delta_im=_e(r.delta.imag),
            delta_dB=_e(r.delta_db)))
    return out


def _attr_print_whatifs(ctx, a: int, b: int, alts, user_supplied_alts: bool,
                        modelled: bool, omega: float, decM,
                        csv_rows: list[dict]) -> list[str]:
    """
    Sections 4 to 7: every what-if, measured against `ctx`.

    Split out because these four are the only ones that need an element to
    change.  Sections 8 and 9 -- the cross-frequency ranking and the exact
    transfer ratio -- are about the PAIR, so an all-open spec still gets them,
    and returning early from the report would have withheld the one thing such
    a spec can still be told.

    `ctx` is the GROUND-MODELLED context when there is one: a what-if measured
    against the declared ideal grounds would answer a question about a network
    the user has just said is not theirs.
    """
    out: list[str] = []
    # -- 4. sensitivity ranking
    if ctx.n_elements:
     out += _attr_section(
        "4. Sensitivity: every element against every candidate",
        "Every row is a full re-solve of the network with that one element "
        "replaced, so a 60 dB change is reported as 60 dB and not as a "
        "first-order slope that stopped being true two decades ago."
        + (" The baseline is the GROUND-MODELLED network of section 3b, not "
           "the declared one." if modelled else ""))
    if not user_supplied_alts:
        for line in _attr_wrap(
                "No --attribute-alt was given, so the scan is limited to the "
                "two STRUCTURAL candidates: 'open' (the element is not there) "
                "and 'ideal' (a perfect short to the reference). Those two "
                "need no judgement about your package. For a real candidate "
                "-- a ball's lead inductance, a 50 ohm terminator -- supply "
                "it yourself: --attribute-alt L=0.3n --attribute-alt R=50. "
                "This tool will not guess."):
            out.append(line)
        out.append("")
    srows = attrib.sensitivity(ctx, a, b, alts, "M")
    out += _attr_print_sensitivity(srows, csv_rows, decM)

    out.append("\n  Leave-one-out, starting from ALL elements ideal "
               "(the number that moves is the one carrying something):")
    loo = attrib.leave_one_out(ctx, a, b, "M")
    lrows = []
    for r in sorted(loo, key=lambda r: -r.abs_delta):
        lrows.append([r.label, format_si(r.baseline_value.real, r.unit),
                      format_si(r.new_value.real, r.unit),
                      format_si(r.delta.real, r.unit), _fmt_db(r.delta_db)])
        csv_rows.append(_attr_row(
            "leave_one_out", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=r.quantity, unit=r.unit, element=r.label,
            alternative=r.alternative,
            value_re=_e(r.new_value.real), value_im=_e(r.new_value.imag),
            delta_re=_e(r.delta.real), delta_im=_e(r.delta.imag),
            delta_dB=_e(r.delta_db)))
    out += _table_lines(["element", "M (all ideal)", "M without it", "Δ", "Δ (dB)"],
                        lrows[:ATTR_RANK_ROWS], ["<", ">", ">", ">", ">"])

    # -- 5. group joint effects and non-additivity
    primary = alts[0]
    out += _attr_section(
        f"5. Joint effects: a whole group changed at once to '{primary.name}'",
        "This is the case per-element numbers cannot see. With 60 ground "
        "balls every single-ball delta is about zero -- the other 59 already "
        "carry the return -- and so is every pairwise second difference: the "
        "effect is order-60, not order-2. 'non-additivity' is the joint "
        "change minus the sum of the one-at-a-time changes.")
    grs = []
    for label in ctx.groups:
        try:
            grs.append(attrib.group_joint(ctx, a, b, label, primary, "M"))
        except attrib.AttribError as e:                      # pragma: no cover
            out.append(f"  ({label}: {e})")
    grs.sort(key=lambda g: -abs(g.joint_delta))
    grows = []
    for g in grs[:ATTR_GROUP_ROWS]:
        grows.append([g.label, str(len(g.elements)),
                      format_si(g.joint_value.real, g.unit),
                      format_si(g.joint_delta.real, g.unit),
                      format_si(g.sum_individual.real, g.unit),
                      format_si(g.non_additivity.real, g.unit)])
    for g in grs:
        csv_rows.append(_attr_row(
            "group_joint", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=g.quantity, unit=g.unit, group=g.label,
            alternative=g.alternative,
            value_re=_e(g.joint_value.real), value_im=_e(g.joint_value.imag),
            delta_re=_e(g.joint_delta.real), delta_im=_e(g.joint_delta.imag),
            extra=f"n={len(g.elements)};sum_individual={g.sum_individual};"
                  f"non_additivity={g.non_additivity}"))
    out += _table_lines(["group", "n", "M joint", "Δ joint", "Σ Δ individual",
                         "non-additivity"], grows, ["<", ">", ">", ">", ">", ">"])
    if len(grs) > ATTR_GROUP_ROWS:
        out.append(f"  ... {len(grs) - ATTR_GROUP_ROWS} more groups "
                   "(all in --attribute-csv)")

    # Pairwise non-additivity, over the strongest few elements only: the scan
    # is O(k^2) full re-solves, so this cap is computational and applies to the
    # CSV as well -- unlike the display cap on the ranking above.
    pool = [r.elements[0] for r in
            sorted([r for r in srows if r.alternative == primary.name],
                   key=lambda r: -r.abs_delta)][:ATTR_PAIR_POOL]
    seen: list[int] = []
    for e in pool:
        if e not in seen:
            seen.append(e)
    pool = seen
    if len(pool) >= 2:
        out.append(f"\n  Pairwise non-additivity over the {len(pool)} strongest "
                   f"elements ({len(pool) * (len(pool) - 1) // 2} pairs; this cap "
                   "is computational and applies to the CSV too):")
        prs = []
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                pr = attrib.group_joint(ctx, a, b, [pool[i], pool[j]],
                                        primary, "M")
                prs.append(pr)
                csv_rows.append(_attr_row(
                    "pair_joint", freq_GHz=_e(decM.freq_hz / 1e9),
                    victim=decM.victim, aggressor=decM.aggressor,
                    quantity=pr.quantity, unit=pr.unit, group=pr.label,
                    alternative=pr.alternative,
                    value_re=_e(pr.joint_value.real),
                    value_im=_e(pr.joint_value.imag),
                    delta_re=_e(pr.joint_delta.real),
                    delta_im=_e(pr.joint_delta.imag),
                    extra=f"sum_individual={pr.sum_individual};"
                          f"non_additivity={pr.non_additivity}"))
        prs.sort(key=lambda p: -abs(p.non_additivity))
        out += _table_lines(
            ["pair", "Δ joint", "Σ Δ individual", "non-additivity"],
            [[p.label, format_si(p.joint_delta.real, p.unit),
              format_si(p.sum_individual.real, p.unit),
              format_si(p.non_additivity.real, p.unit)]
             for p in prs[:ATTR_RANK_ROWS]],
            ["<", ">", ">", ">"])

    # -- 6. cumulative curve
    out += _attr_section(
        f"6. Cumulative: change the top k elements TOGETHER to "
        f"'{primary.name}'",
        "Greedy by the one-at-a-time ranking. The point is the "
        "order-of-the-group effect that per-element numbers hide, not an "
        "optimal subset -- that is combinatorial and this is not it.")
    cc = attrib.cumulative_curve(ctx, a, b, primary, "M")
    crows = []
    for k, v, dv, sv, na in zip(cc.k, cc.values, cc.deltas,
                                cc.sum_individual, cc.non_additivity):
        crows.append([str(k), format_si(v.real, cc.unit),
                      format_si(dv.real, cc.unit),
                      format_si(sv.real, cc.unit),
                      format_si(na.real, cc.unit)])
        csv_rows.append(_attr_row(
            "cumulative", freq_GHz=_e(decM.freq_hz / 1e9),
            victim=decM.victim, aggressor=decM.aggressor,
            quantity=cc.quantity, unit=cc.unit, alternative=cc.alternative,
            value_re=_e(v.real), value_im=_e(v.imag),
            delta_re=_e(dv.real), delta_im=_e(dv.imag),
            extra=f"k={k};sum_individual={sv};non_additivity={na}"))
    out.append(f"  baseline M = {format_si(cc.baseline_value.real, cc.unit)}   "
               f"order: "
               + ", ".join(ctx.elements[i].describe() for i in cc.order[:6])
               + (" ..." if len(cc.order) > 6 else ""))
    out += _table_lines(["k", "M with top k changed", "Δ", "Σ Δ individual",
                         "non-additivity"], crows, [">", ">", ">", ">", ">"])

    # -- 7. the Mobius interval
    out += _attr_section(
        "7. Series-inductance sweep of each group, in closed form",
        "Z_ab as a function of one termination impedance is a Mobius map, so "
        "both endpoints and the whole interval between them are analytic -- "
        "there is no loop and no sampling here. The interval is the headline: "
        "'M lies in [x, y] over any ground inductance' is a statement a "
        "budget can be written against, in a way that one number at one "
        "guessed L is not.")
    l_max = None
    for alt in alts:
        if alt.z is None or omega == 0.0:
            continue
        li = complex(alt.z).imag / omega
        if li > 0 and math.isfinite(li):
            l_max = li if l_max is None else max(l_max, li)
    sweep_labels = [g.label for g in grs[:ATTR_SWEEP_GROUPS]] or \
        list(ctx.groups)[:ATTR_SWEEP_GROUPS]
    for label in sweep_labels:
        try:
            # np.errstate for the same reason core wraps its open-probe divide:
            # a RuntimeWarning on fd 2 names a line of pkg_rlc_attrib and reads
            # as a crash, and a double-clicked GUI has no fd 2 at all.  The
            # sweep no longer expands its rational function -- it evaluates
            # from the partial fractions, so a 38-ball group is as finite as a
            # 2-ball one -- but a near-pole still divides by something very
            # small and the guard costs nothing.
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                sw = attrib.sweep_mobius(ctx, a, b, label, "M", "L")
        except attrib.AttribError as e:                      # pragma: no cover
            out.append(f"  ({label}: {e})")
            continue
        # No extra quotes around the label: in --attribute-group name it
        # already carries its own ("name 'c2_n*'") and nesting them reads as
        # a typo.
        out.append(f"\n  group {label} ({len(sw.elements)} element(s)), every "
                   "member tied to the same L:")
        out.append(f"    M(ideal, L=0)      = "
                   f"{format_si(sw.value_ideal.real, sw.unit, 4)}")
        out.append(f"    M(open,  L=inf)    = "
                   f"{format_si(sw.value_open.real, sw.unit, 4)}")
        # 4 significant digits here, 3 everywhere else: the interval is the
        # headline scalar requirement 10 asks for, and on a well-grounded part
        # the whole span sits inside the third digit -- "[801 pH, 801 pH]"
        # reads as "the lead inductance does not matter" when the measured
        # span is 47 fH.
        out.append(f"    M over L in [0, inf) lies in "
                   f"[{format_si(sw.interval[0], sw.unit, 4)}, "
                   f"{format_si(sw.interval[1], sw.unit, 4)}]"
                   f"   (min at L = {format_si(sw.arg_min, sw.param_unit)}, "
                   f"max at L = {format_si(sw.arg_max, sw.param_unit)})")
        if not (math.isfinite(sw.value_ideal.real)
                and math.isfinite(sw.value_open.real)):      # pragma: no cover
            # This used to fire on any group past ~34 elements: the sweep
            # expanded its degree-|S| rational function and the product of |S|
            # eigenvalues of order 1e-9 underflowed, so BOTH endpoints came
            # back NaN while the interval still printed a confident span.
            # Measured on the synthetic 40-port part in test_attrib_cli, the
            # same 38-ball group now reports M(ideal) = -3.386 nH and
            # M(open) = -286.7 pH, because the partial-fraction form has no
            # product to underflow.  The branch stays as a backstop -- a NaN in
            # the data still reaches here -- but it is no longer the normal
            # fate of a large group.
            for line in _attr_wrap(
                    "WARN: an endpoint of this sweep is UNDEFINED, so the "
                    "interval above is taken over the parameter values that "
                    "stayed finite: read it as a lower bound on the span, not "
                    "as a bound on M.", "    ", hang="      "):
                out.append(line)
        csv_rows.append(_attr_row(
            "sweep", freq_GHz=_e(decM.freq_hz / 1e9), victim=decM.victim,
            aggressor=decM.aggressor, quantity=sw.quantity, unit=sw.unit,
            group=label, alternative="L in [0, inf)",
            value_re=_e(sw.interval[0]), value_im=_e(sw.interval[1]),
            extra=f"ideal={sw.value_ideal.real:.6e};"
                  f"open={sw.value_open.real:.6e};"
                  f"arg_min={sw.arg_min:.6e};arg_max={sw.arg_max:.6e};"
                  f"leaves_bracket={sw.leaves_bracket}"))
        if l_max is not None:
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                swb = attrib.sweep_mobius(ctx, a, b, label, "M", "L",
                                          t_max=l_max)
            out.append(f"    M over L in [0, {format_si(l_max, 'H')}] lies in "
                       f"[{format_si(swb.interval[0], swb.unit, 4)}, "
                       f"{format_si(swb.interval[1], swb.unit, 4)}]"
                       f"   <- bounded by the largest inductance YOU named in "
                       "--attribute-alt")
            csv_rows.append(_attr_row(
                "sweep", freq_GHz=_e(decM.freq_hz / 1e9), victim=decM.victim,
                aggressor=decM.aggressor, quantity=swb.quantity,
                unit=swb.unit, group=label,
                alternative=f"L in [0, {l_max:.6e}]",
                value_re=_e(swb.interval[0]), value_im=_e(swb.interval[1]),
                extra=f"arg_min={swb.arg_min:.6e};arg_max={swb.arg_max:.6e};"
                      f"leaves_bracket={swb.leaves_bracket}"))
        for n in sw.notes:
            for line in _attr_wrap("note: " + n, "    ", hang="      "):
                out.append(line)
    if len(grs) > len(sweep_labels):
        out.append(f"\n  ... {len(grs) - len(sweep_labels)} more group(s) not "
                   "swept (cap: the strongest "
                   f"{ATTR_SWEEP_GROUPS}). Narrow the spec, or read "
                   "--attribute-csv.")
    return out


def _attr_print_ground_model(ctx, a: int, b: int, gm_label: str,
                             decM_ref, csv_rows: list[dict]) -> list[str]:
    """
    What the declared ideal grounds are hiding (requirement 2).

    A ground model is a WHAT-IF, and it is placed here rather than folded into
    section 1 for a reason that is not cosmetic: it describes a network
    `compute_z_matrix` cannot be handed.  The dense case is not expressible as
    a TerminationSet at all -- a shared return is a mutual impedance between
    ground leads and the DSL has no node to hang one on -- so there is no
    second OPINION on the modelled total, only on the arithmetic that produced
    it.  `Decomposition` reconciles the DECLARED configuration against the
    engine whatever `zt` is in force, which is what makes that distinction
    real: the machinery is checked, the model's answer is this module's alone,
    and `reference_applicable` is False to say so.
    """
    out: list[str] = []
    out += _attr_section(
        f"3b. Ground model '{gm_label}': what the declared grounds are hiding",
        "The declared network is the one every other section reconciles "
        "against. This one is a what-if about the leads themselves, and no "
        "second opinion on it exists: a dense element-impedance matrix cannot "
        "be written as a TerminationSet, so compute_z_matrix cannot be asked "
        "about this network at all. What IS checked is the arithmetic -- the "
        "reconciliation in section 2 is of the declared configuration through "
        "this same machinery. Sections 4 to 7 below are measured against THIS "
        "baseline.")
    m_ref = complex(decM_ref.total_reference).real
    om = ctx.omega
    m_mod = float(np.imag(ctx.Zop[a, b])) / om if om else float("nan")
    # 4 significant digits, not the usual 3: 'diag:L=1n' moves M on a 2-ball
    # fixture from 1.010 nH to 1.012 nH, and at 3 digits both read "1.01 nH",
    # i.e. the section would print the dB of a difference it had just hidden.
    out.append(f"  M as declared          = {format_si(m_ref, 'H', 4)}")
    out.append(f"  M under '{gm_label}'".ljust(25)
               + f"= {format_si(m_mod, 'H', 4)}")
    if m_ref != 0.0 and math.isfinite(m_mod) and m_mod != 0.0:
        out.append(f"  difference             = "
                   f"{20 * math.log10(abs(m_mod / m_ref)):.3g} dB")
    for line in _attr_wrap(
            "Independent leads understate the effective common-mode return "
            "inductance by (1 + (n-1)k), so 'diag' and 'shared' are not a "
            "refinement of each other -- they are different answers. Compare "
            "them by running the same command twice with "
            "--attribute-ground-model diag:SPEC and shared:SPEC."):
        out.append(line)
    csv_rows.append(_attr_row(
        "ground_model", freq_GHz=_e(ctx.freq_hz / 1e9),
        victim=ctx.port_names[a], aggressor=ctx.port_names[b],
        quantity="M", unit="H", alternative=gm_label,
        value_re=_e(m_mod),
        extra=f"declared={_e(m_ref)};note=no reference: not expressible as a "
              f"TerminationSet"))

    # The split UNDER the model.  This used to be a hole: the engine compared
    # its own sum against compute_z_matrix's value for the DECLARED spec, so a
    # shared return -- which doubles M, i.e. moves the answer by 100% -- read
    # as a catastrophic algorithm disagreement and the split vanished at
    # exactly the setting the section exists for.  The reconciliation is now of
    # the declared configuration whatever model is in force, so the split
    # survives; the fallback below stays for the cases that really are broken
    # (an ill-conditioned baseline does not stop being ill-conditioned because
    # a model was applied).
    try:
        dz = attrib.decompose(ctx, a, b, "Z")
        dm = attrib.decompose(ctx, a, b, "M")
    except attrib.AttribError as e:                          # pragma: no cover
        out.append(f"  (no split under this model: {e})")
        return out
    out.append("")
    if dz.terms:
        out += _attr_print_terms(dz, dm, csv_rows)
    else:                                                    # pragma: no cover
        for line in _attr_wrap(
                "The per-element split under this model is not available: the "
                "declared configuration does not reconcile against "
                f"compute_z_matrix ({100 * dz.residual_rel:.1f}% relative), so "
                "the arithmetic this model's totals came out of is not trusted "
                "either. That is a property of the file and the spec, not of "
                "the model. The totals above are this module's own."):
            out.append(line)
    return out


def _attr_print_stability(ts, Y, term, src, a, b, f_primary, decZ0,
                          extra_freqs, csv_rows: list[dict]) -> list[str]:
    """
    The same ranking at several frequencies, side by side.

    --freq is always the first column, so the ranking every other section of
    this report is built from is the one being checked.  Duplicates are dropped
    AFTER snapping to the file's grid: two requested frequencies inside one
    sweep step are one column, and pretending otherwise would show a ranking
    "confirmed" against itself.
    """
    out: list[str] = []
    freqs = [f_primary]
    for f in extra_freqs:
        snapped = _attr_snap(ts.freqs, f)
        if snapped not in freqs:
            freqs.append(snapped)

    # element description -> [rank per column]; the description is the key
    # because a lumped element whose admittance vanishes at one frequency is
    # dropped there, so the element LISTS can legitimately differ.
    ranks: list[dict[str, int]] = []
    totals: list[complex] = []
    for f in freqs:
        if f == f_primary:
            dec = decZ0
        else:
            dec = attrib.decompose(
                attrib.build_context(Y, ts.freqs, term, f, sources=src),
                a, b, "Z")
        totals.append(dec.total_reference)
        order = sorted([t for t in dec.terms if t.element is not None],
                       key=lambda t: -abs(t.contribution))
        col = {t.label: i + 1 for i, t in enumerate(order)}
        ranks.append(col)
        for label, r in col.items():
            csv_rows.append(_attr_row(
                "rank", freq_GHz=_e(f / 1e9), victim=dec.victim,
                aggressor=dec.aggressor, quantity="Z", unit="Ohm",
                element=label, value_re=_e(r), extra="rank"))

    out.append("  Z_ab per frequency: " + "   ".join(
        f"{format_freq(f)} {_fmt_complex(z, 3)} Ω" for f, z in zip(freqs, totals)))
    if len(freqs) == 1:
        for line in _attr_wrap(
                "Only one frequency was evaluated. Pass "
                "--attribute-freqs 1,5,10 to re-rank across the band; a "
                "ranking read off one frequency is a statement about that "
                "frequency only."):
            out.append(line)
        return out

    labels: list[str] = []
    for col in ranks:
        for lab in col:
            if lab not in labels:
                labels.append(lab)
    if not labels:
        # Every column's split was withheld, so there is nothing to rank.  An
        # empty table under a "rank is stable" verdict would be a claim about
        # a comparison that never happened.
        out.append("  No ranking is available at any of these frequencies -- the "
                   "per-element split was withheld (see the reconciliation above).")
        return out
    labels.sort(key=lambda l: ranks[0].get(l, 10 ** 6))
    rows = [[lab] + [(str(col[lab]) if lab in col else "--") for col in ranks]
            for lab in labels]
    out += _table_lines(["element"] + [format_freq(f) for f in freqs], rows,
                        ["<"] + [">"] * len(freqs))
    moved = [lab for lab in labels
             if len({col.get(lab) for col in ranks}) > 1]
    if moved:
        for line in _attr_wrap(
                "RANK IS NOT STABLE: "
                + ", ".join(f"'{m}'" for m in moved[:6])
                + (" ..." if len(moved) > 6 else "")
                + " change places across the band, so a ranking read off one "
                  "frequency is a statement about that frequency only."):
            out.append(line)
    else:
        out.append("  Rank is stable across every frequency evaluated.")
    return out


def _attr_print_caveats() -> list[str]:
    out: list[str] = []
    out.append("\n" + _ATTR_RULE)
    out.append("Caveats -- three things this report cannot do")
    out.append(_ATTR_RULE)
    for text in (
        "BLIND TO OPEN PORTS. A port you did not name in --gnd or --short "
        "contributes no element and therefore no term, so the table above "
        "ranks the DECLARATIONS in your spec, not the ports of your package. "
        "To ask about a port you have not decided on, declare it and read its "
        "'open' row in the sensitivity scan: that row is exactly 'what if "
        "this port were not connected'.",
        "THE SPLIT DEPENDS ON HOW THE SPEC IS SPELLED, not only on the "
        "network it describes. '--gnd 6:1:14' (9 ground elements) and "
        "'--gnd 6 --short 6-7,6-8,...' (1 ground and 8 shorts) are the same "
        "network, give the same total, and decompose differently. Both are "
        "right; they answer different questions.",
        "IT CANNOT EVALUATE NEW METAL. Every what-if here changes a "
        "TERMINATION on a port the S-parameter file already has. Moving a "
        "trace, adding a shield, or adding a ball that is not already a port "
        "is a different EM solve, and nothing in this report predicts it.",
    ):
        for line in _attr_wrap("* " + text, "  ", hang="    "):
            out.append(line)
        out.append("")
    return out



#: Screen rows printed to the terminal.  This is NOT a free choice.  The
#: sentence that covers the rest of the file -- "the other N port(s) would each
#: move the answer by at most X" -- is `cold_start_negative_result(rows, unit)`
#: at its default `top=COLD_START_SHOW`, and `cold_start_report` builds it that
#: way.  Print any other number of rows and that sentence counts from the wrong
#: place: at 20 printed rows it would re-describe 10 ports the reader has just
#: read as "the other ports", and at 5 it would silently leave 5 out of both.
#: Track the engine's constant; do not pick a number here.
COLD_RANK_ROWS = attrib.COLD_START_SHOW

#: Flagged pairs printed.  With the default --cold-start-top 8 the scan is 28
#: pairs, so this bites only when more than 20 of them are surprising -- which
#: is itself the message -- and the CSV carries every scanned pair, flagged or
#: not, so nothing is lost.
COLD_PAIR_ROWS = 20

#: Mirror rows printed.  There is one per candidate, i.e. 151 on a 153-port
#: package, and the engine's own renderer shows COLD_START_SHOW of them.
COLD_MIRROR_ROWS = attrib.COLD_START_SHOW

#: The quantity the whole report is in.  Hard-wired to M, like every ranked
#: section of --attribute: it is the number a spur / pulling budget is written
#: against, and it is real by construction so a table of it needs one column
#: per value rather than two.  The engine takes 'k', 'ImZ', 'M/L_a' and the
#: rest, and the CSV carries a `quantity` column, so exposing a flag later is
#: an addition rather than a format change.
COLD_QUANTITY = "M"


def _cold_q(value: complex, unit: str) -> str:
    """
    One value of the report's quantity.

    Non-finite prints '--' rather than 'nan': a row the screen could not
    evaluate is a MISSING measurement, and `format_si` would render it as the
    word 'nan' in a column of henries, where it reads like a number.  Same
    guard, same reason, as the attribution tables above.

    Only the real part is rendered, which is exact for COLD_QUANTITY = "M":
    `_map_value` divides Im(Z_ab) by omega, so the imaginary part is
    identically zero (checked in the export -- every `value_im` in the CSV is
    0.000000e+00).  A future --cold-start-quantity flag that admitted 'Z' would
    have to widen this, and the CSV already carries both parts so nothing is
    lost in the meantime.
    """
    v = complex(value)
    if not math.isfinite(v.real):
        return "--"
    return format_si(v.real, unit)


def _cold_abs(value: complex) -> str:
    """|Z| for one of the two coupling columns, or '--'."""
    m = abs(complex(value))
    return "--" if not math.isfinite(m) else f"{m:.4g}"



# ---------------------------------------------------------------------------
# The CSV.  Same shape as the attribution's -- one long table with a `section`
# column -- because the records have genuinely different shapes and a single
# header that csv.DictReader round-trips beats six tables nobody can join.
#
# What `value` and `delta` MEAN is per section, and the header written into the
# file says so.  That is the attribution CSV's own convention (its `sweep` rows
# put interval[0] in value_re and interval[1] in value_im), not a shortcut
# taken here.
# ---------------------------------------------------------------------------

_COLD_CSV_FIELDS = [
    "section", "freq_GHz", "victim", "aggressor", "quantity", "unit",
    "port", "port_j", "port_name", "declared", "k",
    "z_ap_re", "z_ap_im", "z_pb_re", "z_pb_im", "z_pp_re", "z_pp_im",
    "value_re", "value_im", "delta_re", "delta_im", "delta_dB",
    "threshold", "flagged", "defined", "extra",
]


def _cold_row(section: str, **kw) -> dict:
    """One CSV record.  An unknown field name RAISES rather than vanishing."""
    row = {f: "" for f in _COLD_CSV_FIELDS}
    for k in kw:
        if k not in row:
            raise KeyError(f"unknown cold-start CSV field '{k}'")
    row["section"] = section
    row.update(kw)
    return row



# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _cold_print_header(csc, br, names: list[str],
                       notes_extra: list[str]) -> list[str]:
    """What was screened, from what baseline, and how well conditioned it is."""
    out: list[str] = []
    ctx = csc.ctx
    out.append(f"  frequency        : {format_freq(br.freq_hz)}"
               + ("" if br.freq_hz == br.requested_hz
                  else f"   (requested {format_freq(br.requested_hz)}; snapped to "
                       "the file's grid)"))
    out.append("  measurement ports: "
               + "   ".join(f"{i + 1} '{n}'" for i, n in enumerate(names))
               + "      <- --cold-start takes either the name or the number")
    out.append(f"  candidate ports  : {br.n_candidates} (every port that is not "
               f"part of a measurement port), {br.n_screenable} screenable")
    out.append(f"  conditioning     : cond(Ybase) {ctx.cond_Ybase:.3g}   "
               f"cond(G) {ctx.cond_G:.3g}   "
               f"reciprocity |r_a - p_a|/|p_a| {ctx.reciprocity_rel:.3g}")
    if ctx.reciprocity_rel > RECIPROCITY_WARN:
        for line in _attr_wrap(
                "WARN: the baseline is not reciprocal to within "
                f"{RECIPROCITY_WARN:g}. The screen's two coupling columns are "
                "solved separately, so the numbers below are still right, but "
                "a non-reciprocal EM solve is worth explaining before a budget "
                "is written against it.", "    ", hang="      "):
            out.append(line)
    # The baseline is the single most load-bearing line on this page: on a
    # structure with no DC reference it is NOT all-open, the engine folds a
    # ground in to have one, and every delta below is measured from that
    # instead.  Measured on coupled_4port_float.s4p probed differentially on
    # coil 1, the two readings of "grounding port 4" differ completely
    # (-6.8355 Ohm against 2.8e-14), so this cannot be a footnote.
    for line in _attr_wrap("baseline         : " + br.baseline_note, "  ",
                           hang="                     "):
        out.append(line)
    for n in notes_extra:
        for line in _attr_wrap("note: " + n, "    ", hang="      "):
            out.append(line)
    for w in ctx.core_warnings:
        for line in _attr_wrap("WARN (compute_z_matrix): " + w, "    ",
                               hang="      "):
            out.append(line)
    return out


def _cold_print_bracket(br, shared_notes: list[str],
                        csv_rows: list[dict]) -> list[str]:
    """Step 0: the two ends and the dB between them."""
    out: list[str] = []
    u = br.unit
    lo_label = ("every non-probe port OPEN" if not br.baseline_grounded
                else "all open EXCEPT port(s) "
                     + collapse_ports([p + 1 for p in br.baseline_grounded]))
    w = max(len(lo_label), 29)
    out.append(f"  {lo_label:<{w}} = {_cold_q(br.value_open, u)}")
    out.append(f"  {'every non-probe port GROUNDED':<{w}} = "
               f"{_cold_q(br.value_grounded, u)}")
    out.append(f"  {'the whole question is worth':<{w}}   {_fmt_db(br.span_db)}")
    if math.isfinite(br.reconciliation_rel):
        for line in _attr_wrap(
                "(the grounded end agrees with compute_z_matrix to "
                f"{br.reconciliation_rel:.3g} relative. The open end cannot "
                "have a second opinion: no TerminationSet spells 'the probe "
                "sides merged and every element removed' -- that IS the "
                "baseline.)"):
            out.append(line)
    # The bracket's OWN notes.  `Bracket.notes` is seeded from the shared
    # context notes so that `cold_start_bracket` can be called on its own and
    # still tell the whole story; the header has already printed those, so only
    # what the bracket ADDED belongs here.  Both lists come from one
    # ColdStartContext, so the membership test compares strings from one place
    # and cannot drift apart in whitespace.  What it carries is load-bearing:
    # a 0 dB span that is "0 by construction, not by measurement" -- no
    # candidate port could be screened -- reads as "nothing here matters",
    # which is the opposite of what it means.
    for n in br.notes:
        if n not in shared_notes:
            for line in _attr_wrap("note: " + n, "  ", hang="        "):
                out.append(line)
    out.append("")
    for line in _attr_wrap(br.caveat):
        out.append(line)
    csv_rows.append(_cold_row(
        "bracket", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
        aggressor=br.aggressor, quantity=br.quantity, unit=u,
        value_re=_e(br.value_open.real), value_im=_e(br.value_open.imag),
        delta_re=_e((br.value_grounded - br.value_open).real),
        delta_im=_e((br.value_grounded - br.value_open).imag),
        delta_dB=_e(br.span_db),
        extra=f"grounded_re={br.value_grounded.real:.6e};"
              f"grounded_im={br.value_grounded.imag:.6e};"
              f"n_candidates={br.n_candidates};"
              f"n_screenable={br.n_screenable};"
              f"reconciliation_rel={br.reconciliation_rel:.6e};"
              f"baseline_grounded="
              + (collapse_ports([p + 1 for p in br.baseline_grounded])
                 if br.baseline_grounded else "")))
    return out


def _cold_print_screen(cs, csv_rows: list[dict]) -> list[str]:
    """Step 1: both coupling columns and the exact effect, ranked by |delta|."""
    out: list[str] = []
    br = cs.bracket
    u = br.unit
    rows = cs.screen
    if not rows:
        for line in _attr_wrap(
                "There is no candidate port to screen: every port of this file "
                "carries a measurement port. The bracket above is 0 dB by "
                "construction, not by measurement."):
            out.append(line)
        return out

    trows = []
    for r in rows[:COLD_RANK_ROWS]:
        trows.append([
            r.label,
            _cold_abs(r.z_ap), _cold_abs(r.z_pb),
            _cold_q(r.value, u), _cold_q(r.delta, u),
            "--" if not math.isfinite(r.delta_db) else _fmt_db(r.delta_db),
            r.declared,
        ])
    out += _table_lines(["port", "|Z_ap| (Ω)", "|Z_pb| (Ω)", f"{br.quantity} after",
                         "Δ", "Δ (dB)", "declared"], trows,
                        ["<", ">", ">", ">", ">", ">", "<"])
    if len(rows) > COLD_RANK_ROWS:
        out.append(f"  (the top {COLD_RANK_ROWS} of {len(rows)}; every candidate "
                   "port is in --cold-start-csv, which has no cap)")

    # The NEGATIVE result is a deliverable, not a leftover: a screen that names
    # two ports and says nothing about the remaining 147 has withheld the thing
    # the user most wanted, which is permission to stop looking.  It is the
    # engine's sentence verbatim, and it counts from COLD_RANK_ROWS -- see the
    # constant.
    if cs.negative_result:
        out.append("")
        for line in _attr_wrap(cs.negative_result):
            out.append(line)

    # Rows the screen could not evaluate sort LAST and so may not be on screen
    # at all, which would leave the context note's "see each row's note"
    # pointing at nothing.  Grouped by the note itself: a folded baseline puts
    # the same sentence on every unreachable port, and forty identical
    # paragraphs is how a reader learns to skip the one that matters.
    by_note: dict[str, list[int]] = {}
    for r in rows:
        if not r.defined:
            by_note.setdefault(r.note or "not evaluated", []).append(r.port + 1)
    for note, ports in by_note.items():
        for line in _attr_wrap(
                f"port(s) {collapse_ports(ports)} have NO delta -- {note}.",
                "  ", hang="      "):
            out.append(line)

    for r in rows:
        csv_rows.append(_cold_row(
            "screen", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
            aggressor=br.aggressor, quantity=br.quantity, unit=u,
            port=str(r.port + 1), port_name=r.name, declared=r.declared,
            z_ap_re=_e(r.z_ap.real), z_ap_im=_e(r.z_ap.imag),
            z_pb_re=_e(r.z_pb.real), z_pb_im=_e(r.z_pb.imag),
            z_pp_re=_e(r.z_pp.real), z_pp_im=_e(r.z_pp.imag),
            value_re=_e(r.value.real), value_im=_e(r.value.imag),
            delta_re=_e(r.delta.real), delta_im=_e(r.delta.imag),
            delta_dB=_e(r.delta_db), defined=str(r.defined),
            extra=("" if r.defined else f"note={r.note}")))
    return out


def _cold_print_pairs(cs, csc, top_k: int, csv_rows: list[dict]) -> list[str]:
    """Step 2: pairs from the baseline, and the mirror from all-grounded."""
    out: list[str] = []
    br = cs.bracket
    u = br.unit

    if cs.pairs:
        thr = cs.pairs[0].threshold
        # The number actually scanned, not the flag's value: the engine takes
        # `usable[:top_k]`, so on a small file "over the top 8" would name a
        # depth that does not exist and make 1 pair look like a truncation of
        # 28.  Recovered from the pair count, which is n*(n-1)/2 by
        # construction, so it cannot drift from what the engine did.
        n_scanned = int(round((1 + math.sqrt(1 + 8 * len(cs.pairs))) / 2))
        for line in _attr_wrap(
                f"{len(cs.pairs)} pair(s) scanned over the top {n_scanned} of "
                f"step 1 (--cold-start-top {top_k}). A pair is FLAGGED when "
                f"its non-additivity exceeds {format_si(thr, u)} -- half the "
                "largest single-port effect in the scan, floored at 1% of the "
                "baseline value so that a file where every single-port effect "
                "is ~0 (which is the normal reading of a shield) cannot "
                "collapse the threshold onto its own noise and flag "
                "everything."):
            out.append(line)
        out.append("")
    flagged = [p for p in cs.pairs if p.flagged]
    if flagged:
        prows = []
        for p in flagged[:COLD_PAIR_ROWS]:
            prows.append([
                # Port NUMBERS only in the cell, and the names on their own
                # line under the table.  Putting both names in the cell needs
                # them truncated to fit -- and `_trunc` keeps the HEAD, so
                # 'guard_ring1' and 'guard_ring2' both render as
                # 'guard_rin~': two indistinguishable stumps beside the one
                # pair the section exists to name.  That is the same
                # head-truncation failure `freeze_label` documents (trim the
                # base, keep the discriminator), and the answer here is not to
                # truncate at all.  Measured: with the names in the cell the
                # shield's table is 110 columns and with them under it 89.
                p.label,
                _cold_q(p.delta_i, u), _cold_q(p.delta_j, u),
                _cold_q(p.delta_pair, u), _cold_q(p.non_additivity, u),
                # %.3g, not %.1f: the ratio runs from ~0.02 (the two together
                # very nearly cancel) to 89.8 (the measured shield), and at
                # one decimal place every cancelling pair prints '0.0x', which
                # is the half of the scale that says the ports are fighting.
                "--" if not math.isfinite(p.ratio) else f"{p.ratio:.3g}x",
                "SIGN FLIP" if p.sign_flip else "",
            ])
        out += _table_lines(["pair", "Δ i alone", "Δ j alone", "Δ together",
                             "non-additivity", "vs larger", "flag"], prows,
                            ["<", ">", ">", ">", ">", ">", "<"])
        if len(flagged) > COLD_PAIR_ROWS:
            out.append(f"  ... {len(flagged) - COLD_PAIR_ROWS} more flagged pairs "
                       "(all of them, flagged or not, are in --cold-start-csv)")
        named = [p for p in flagged[:COLD_PAIR_ROWS] if p.name_i or p.name_j]
        for p in named:
            out.append(f"    {p.label} = {p.name_i or '(unnamed)'}, "
                       f"{p.name_j or '(unnamed)'}")
        if any(p.sign_flip for p in flagged):
            for line in _attr_wrap(
                    "SIGN FLIP means the two ports together move the answer "
                    "the OTHER WAY from either of them alone. That is the "
                    "measured signature of a closed loop -- a shield or guard "
                    "ring brought out as two ports -- and a single-port "
                    "ranking reports it as two minor entries with the wrong "
                    "sign."):
                out.append(line)
    elif cs.pairs:
        for line in _attr_wrap(
                "No pair exceeds the threshold: within the ports scanned, "
                "grounding two together does what grounding them one at a "
                "time predicts. That is a result, not a gap -- it is what "
                "says the single-port ranking above can be read at face "
                "value."):
            out.append(line)
    else:
        for line in _attr_wrap(
                "No pair could be scanned: fewer than two candidate ports "
                "could be evaluated, so there is no second-order effect to "
                "look for."):
            out.append(line)

    for p in cs.pairs:
        csv_rows.append(_cold_row(
            "pair", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
            aggressor=br.aggressor, quantity=br.quantity, unit=u,
            port=str(p.port_i + 1), port_j=str(p.port_j + 1),
            port_name=p.name_i,
            value_re=_e(p.delta_pair.real), value_im=_e(p.delta_pair.imag),
            delta_re=_e(p.non_additivity.real),
            delta_im=_e(p.non_additivity.imag),
            threshold=_e(p.threshold), flagged=str(p.flagged),
            extra=f"name_j={p.name_j};delta_i={p.delta_i};"
                  f"delta_j={p.delta_j};ratio={p.ratio:.6e};"
                  f"sign_flip={p.sign_flip}"))

    # The mirror.  Both directions are needed because they catch OPPOSITE
    # failures: from all-open a set of ports that only acts collectively reads
    # ~0 one at a time, and from all-grounded a set that SHARES a return reads
    # ~0 one at a time for the opposite reason -- the other 59 balls still
    # carry the current.
    out.append("")
    for line in _attr_wrap(
            "Mirror: from ALL candidate ports GROUNDED, opening one. The "
            "number that moves is the one that was carrying something -- and "
            "it is a different failure from the one above, not a check on it: "
            "60 ground balls read ~0 each from all-grounded because the other "
            "59 carry the return, and the shield reads +879.956 pH per end."):
        out.append(line)
    port_of = {e: p for p, e in csc.element_of_port.items()}
    name_of = {r.port: r.label for r in cs.screen}
    mrows = []
    ranked = sorted(cs.mirror, key=lambda s: -s.abs_delta)
    for s in ranked:
        p = port_of.get(s.elements[0]) if s.elements else None
        label = name_of.get(p, s.label) if p is not None else s.label
        mrows.append([label, _cold_q(s.baseline_value, u),
                      _cold_q(s.new_value, u), _cold_q(s.delta, u),
                      "--" if not math.isfinite(s.delta_db)
                      else _fmt_db(s.delta_db)])
        csv_rows.append(_cold_row(
            "mirror", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
            aggressor=br.aggressor, quantity=s.quantity, unit=s.unit,
            port=("" if p is None else str(p + 1)),
            port_name=("" if p is None else csc.name_of(p)),
            value_re=_e(s.new_value.real), value_im=_e(s.new_value.imag),
            delta_re=_e(s.delta.real), delta_im=_e(s.delta.imag),
            delta_dB=_e(s.delta_db),
            extra=f"element={s.label};baseline_re={s.baseline_value.real:.6e};"
                  f"baseline_im={s.baseline_value.imag:.6e}"))
    if mrows:
        out += _table_lines(["port opened", f"{br.quantity} (all grounded)",
                             f"{br.quantity} without it", "Δ", "Δ (dB)"],
                            mrows[:COLD_MIRROR_ROWS], ["<", ">", ">", ">", ">"])
        if len(mrows) > COLD_MIRROR_ROWS:
            out.append(f"  ... {len(mrows) - COLD_MIRROR_ROWS} more "
                       "(all of them are in --cold-start-csv, which has no cap)")
    else:
        out.append("    (nothing to open: no candidate port could be evaluated)")
    return out


def _cold_print_curve(cs, csc, csv_rows: list[dict]) -> list[str]:
    """Step 3: the greedy cumulative curve and where it saturates."""
    out: list[str] = []
    br = cs.bracket
    u = br.unit
    cv = cs.curve
    if not cv.k:
        for line in _attr_wrap(
                "The curve is empty: no candidate port could be evaluated, so "
                "there is no order to ground them in."):
            out.append(line)
        return out
    labels = {r.port: r.label for r in cs.screen}
    crows = []
    for i, k in enumerate(cv.k):
        p = cv.order[i]
        crows.append([
            str(k), labels.get(p, f"port {p + 1}"),
            _cold_q(cv.values[i], u), _cold_q(cv.deltas[i], u),
            _cold_q(cv.sum_individual[i], u),
            _cold_q(cv.non_additivity[i], u),
        ])
        csv_rows.append(_cold_row(
            "cumulative", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
            aggressor=br.aggressor, quantity=cv.quantity, unit=cv.unit,
            # The bare NAME here, not `labels[p]`: every other section's
            # `port_name` is the file's own name for the port, and a column
            # that reads 'aux1' in five sections and 'port 5 (aux1)' in the
            # sixth cannot be grouped on.
            k=str(k), port=str(p + 1), port_name=csc.name_of(p),
            value_re=_e(cv.values[i].real), value_im=_e(cv.values[i].imag),
            delta_re=_e(cv.deltas[i].real), delta_im=_e(cv.deltas[i].imag),
            extra=f"sum_individual={cv.sum_individual[i]};"
                  f"non_additivity={cv.non_additivity[i]};"
                  f"saturation_k={cv.saturation_k};"
                  f"saturation_tol={cv.saturation_tol:.6e};"
                  f"alternative={cv.alternative}"))
    out += _table_lines(["k", "port grounded", f"{br.quantity} with the top k", "Δ",
                         "Σ Δ individual", "non-additivity"], crows,
                        [">", "<", ">", ">", ">", ">"])
    # The saturation point is the answer to "how many ports actually matter",
    # which neither the ranking nor the pair scan gives.  It is the engine's
    # own sentence and it carries the tolerance it was judged against, so a
    # reader can see what "saturated" was taken to mean.
    for n in cv.notes:
        for line in _attr_wrap("note: " + n, "  ", hang="        "):
            out.append(line)
    return out


def _cold_print_families(cs, csv_rows: list[dict]) -> list[str]:
    """The name-family proposals -- tested, shown, and binding on nothing."""
    out: list[str] = []
    br = cs.bracket
    if not cs.families:
        for line in _attr_wrap(
                "No name family was proposed: this file's port names give no "
                "two screened ports a shared prefix (a family is 'guard_ring1' "
                "and 'guard_ring2', with only a TRAILING run of digits "
                "stripped, so 'c1_p' and 'c2_p' stay two families). Nothing "
                "above depended on the names either way."):
            out.append(line)
        return out
    for fs in cs.families:
        for line in _attr_wrap(("* " if fs.flagged else "- ") + fs.text, "  ",
                               hang="    "):
            out.append(line)
        csv_rows.append(_cold_row(
            "family", freq_GHz=_e(br.freq_hz / 1e9), victim=br.victim,
            aggressor=br.aggressor, quantity=br.quantity, unit=br.unit,
            port_name=fs.prefix,
            value_re=_e(fs.together.real), value_im=_e(fs.together.imag),
            delta_re=_e(fs.non_additivity.real),
            delta_im=_e(fs.non_additivity.imag),
            threshold=_e(fs.threshold), flagged=str(fs.flagged),
            extra=f"ports={collapse_ports([p + 1 for p in fs.ports])};"
                  f"separate={fs.separate};tested={fs.tested}"))
    out.append("")
    for line in _attr_wrap(
            "These are SUGGESTIONS. Which ports are one physical structure is "
            "a judgement about your layout, and this tool will not make it: "
            "the numbers beside each line are computed both ways, the "
            "grouping is not folded into any answer above, and every NUMBER "
            "in this report is identical on a file with no port names at all "
            "-- only the labels lose their names."):
        out.append(line)
    return out
