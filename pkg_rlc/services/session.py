"""
The session file: Save Config, Load Config, and the on-exit autosave.

A session file holds the CONFIG, never the results.  A .sNp file is megabytes
and a computed Z is one array per trace per frequency; the point of this file
is that it is a few kB of readable JSON that can go in git, be mailed to a
colleague, or ride along to the red zone next to the data it describes.  What
comes back is the setup -- press Calculate and the numbers return.  Export CSV
remains the results path, so the two never overlap and never disagree.

WHY IT IS A MODULE.  Everything here is a SERVICE OVER THE MODEL: it takes the
lists of `FileEntry` and `TraceConfig` and returns a dict, or takes a dict and
returns them.  There is no Tk in it and there never was -- `session_to_dict`
takes the lists and `session_from_dict` returns them, which is what makes the
whole round trip testable without a display, and which is why the split was a
pure move.  `pkg_rlc_gui` keeps the parts that genuinely need the App: the file
dialogs, reading the widgets into a `controls` dict, and applying a
`LoadedSession` back onto live traces.

THE ONE RULE THAT SHAPES ALL OF IT: a bad value costs its own field, never the
file.  A session file is readable text, so it WILL be hand-edited -- and losing
a port map that took ten minutes to type over one mangled `color_idx` is the
wrong trade.  Unknown keys, unparseable ints and malformed rows are dropped
with a note the caller shows in the Results pane.  What is refused outright is
only what makes the file unreadable AS a session (`SessionError`): not ours, no
version, or a version from the future.

Re-exported from `pkg_rlc_gui`, so `from pkg_rlc_gui import session_to_dict`
and `pkg_rlc_gui.SESSION_FORMAT` keep resolving for every call site and every
test.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from pkg_rlc.physics.core import ConnectionRow, MeasPortRow
from pkg_rlc.model.trace import RESULTS_VIEWS, TraceConfig


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
