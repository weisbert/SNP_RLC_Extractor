"""
Port names: what every port is DOING, and which of them look forgotten.

The tool has always harvested "! Port[12] = VDD_ball_2" into
TouchstoneData.port_names and then done almost nothing with it.  This is the
coverage for the three things that changed:

  * PURE -- `port_roles` is the single classifier (the port-overview strip and
    the Ports & Roles window both count off it), `row_sources` says which row
    put a port where it is, and `open_name_clusters` is the check that a spec
    can be internally consistent and still wrong.  The false-alarm tests matter
    more than the positive ones here: a warning that fires on
    tests/fixtures/coupled_2port_gndref.s2p (probe coil1, leave coil2 open --
    the ordinary way to use that file) would be trained away in a week.
  * TK -- the window itself: filtering, sorting on the RAW value, the two
    Treeview hazards, the flagged rows, and the write-back that turns a
    selection into a collapsed range.

Every guard here was mutation-checked.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402

from pkg_rlc_core import (  # noqa: E402
    ConnectionRow,
    MeasPortRow,
    OPEN_CLUSTER_MIN_FAMILY,
    PortRole,
    ROLE_ELEMENT,
    ROLE_GROUND,
    ROLE_OPEN,
    ROLE_PROBE_MINUS,
    ROLE_PROBE_PLUS,
    ROLE_SHORTED,
    ROLE_VDD,
    build_terminations_rows,
    collapse_ports,
    name_prefix,
    open_name_clusters,
    open_port_name_messages,
    parse_port_range,
    parse_touchstone,
    port_roles,
    row_sources,
)
from pkg_rlc_gui import (  # noqa: E402
    App,
    FileEntry,
    PORT_ROLES_STYLE,
    PortRolesWindow,
    TraceConfig,
    WARN_OPEN_LOOKS_TERMINATED,
    WARN_PROBE_AND_GROUND,
    WARN_FROM_KEPT_TEXT,
    WARN_FG,
    _append_port_spec,
    _fixed_map_filter,
    _port_overview_text,
    _role_warnings,
    _roles_header,
    _trace_role_rows,
    _validation_messages,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _ensure_fixtures() -> None:
    if (FIX / "diff_pair_4port.s4p").exists():
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import generate_test_snp  # type: ignore
    generate_test_snp.main()


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


# ============================================================================
# A synthetic PACKAGE file: many ports, real ball names
# ============================================================================
#
# The whole point of the feature is a 153-port export nobody has memorised, and
# no fixture in the repo looks like one.  12 ports is enough to exercise every
# threshold (a family of 8 with 1 open clears MIN_FAMILY and the 25% remnant
# rule) while staying a file the parser reads in microseconds.

PKG_NAMES = ([f"VSS_ball_{i}" for i in range(1, 9)]
             + ["sig_in", "sig_out", "VDD_ball_1", "VDD_ball_2"])


def _write_named_snp(path: Path, names, nfreq: int = 3) -> Path:
    """A tiny well-formed .sNp carrying '! Port[n] = name' comments."""
    n = len(names)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("! synthetic package export\n")
        for i, nm in enumerate(names, 1):
            fh.write(f"! Port[{i}] = {nm}\n")
        fh.write("# HZ S RI R 50\n")
        for k in range(nfreq):
            vals = []
            for a in range(n):
                for b in range(n):
                    vals += [0.01 * (a + 1) * (b + 1) / (n * n),
                             0.002 * (k + 1)]
            fh.write(f"{1e9 * (k + 1):.6e} "
                     + " ".join(f"{v:.6e}" for v in vals) + "\n")
    return path


# ============================================================================
# PURE: port_roles
# ============================================================================

class TestPortRoles(unittest.TestCase):
    def _term(self):
        return build_terminations_rows(
            [MeasPortRow("tank", "1", "2")],
            [ConnectionRow(kind="ground", ports="3"),
             ConnectionRow(kind="vdd", ports="4"),
             ConnectionRow(kind="rlc_gnd", ports="5", R="50"),
             ConnectionRow(kind="short", ports="6", to="7")],
            nports=8)

    def test_every_bucket_is_reachable(self):
        roles = port_roles(self._term(), 8)
        self.assertEqual([r.role for r in roles],
                         [ROLE_PROBE_PLUS, ROLE_PROBE_MINUS, ROLE_GROUND,
                          ROLE_VDD, ROLE_ELEMENT, ROLE_SHORTED, ROLE_SHORTED,
                          ROLE_OPEN])
        self.assertEqual([r.index for r in roles], list(range(1, 9)))

    def test_a_probe_carries_its_measurement_port_name(self):
        roles = port_roles(self._term(), 8)
        self.assertEqual(roles[0].group, "tank")
        self.assertEqual(roles[1].group, "tank")
        self.assertEqual(roles[2].group, "")

    def test_the_legacy_B_group_reads_as_the_minus_side_of_A(self):
        """
        Signal('B') IS 'the minus side of A' everywhere else in the code, so a
        window that showed it as a separate probe would contradict the number
        the tool computes.
        """
        term = build_terminations_rows([], [], "1 signal A\n2 signal B\n",
                                       nports=2)
        roles = port_roles(term, 2)
        self.assertEqual([(r.role, r.group) for r in roles],
                         [(ROLE_PROBE_PLUS, "A"), (ROLE_PROBE_MINUS, "A")])

    def test_names_come_from_the_file(self):
        roles = port_roles(self._term(), 8,
                           ["a", "b", "c", "d", "e", "f", "g", "h"])
        self.assertEqual([r.name for r in roles], list("abcdefgh"))

    def test_a_file_with_no_names_leaves_them_empty(self):
        roles = port_roles(self._term(), 8, None)
        self.assertEqual({r.name for r in roles}, {""})

    def test_a_short_name_list_does_not_raise(self):
        roles = port_roles(self._term(), 8, ["only", "two"])
        self.assertEqual(roles[0].name, "only")
        self.assertEqual(roles[7].name, "")

    def test_with_no_file_the_open_ports_are_not_invented(self):
        """
        An open port is one the file HAS and the spec did not name.  Without
        the file that is unknowable, and a count derived from the largest port
        typed would look authoritative and be wrong.  So: no open records at
        all, not even for an explicit `open` row.
        """
        term = build_terminations_rows(
            [MeasPortRow("tank", "1", "2")],
            [ConnectionRow(kind="open", ports="3")])
        roles = port_roles(term, None)
        self.assertEqual([r.index for r in roles], [1, 2])
        self.assertNotIn(ROLE_OPEN, [r.role for r in roles])

    def test_no_termination_set_is_an_empty_list_not_a_crash(self):
        self.assertEqual(port_roles(None, 8), [])

    def test_the_overview_strip_counts_off_the_same_records(self):
        """
        ONE classifier.  If the strip and the window could disagree, the strip
        is the one the user believes and the window is the one they check.
        """
        self.assertEqual(
            _port_overview_text(self._term(), 8),
            "Ports (8): 2 probe · 1 ground · 1 vdd · 1 element · 2 shorted "
            "· 1 open")


# ============================================================================
# PURE: row_sources
# ============================================================================

class TestRowSources(unittest.TestCase):
    def test_the_last_row_to_name_a_port_owns_it(self):
        """
        rows_to_dsl_text emits every measurement port BEFORE every connection
        and the DSL is last-assignment-wins, so a ground row beats a probe row
        on the same port.  The source column has to say the same thing the
        answer does.
        """
        src = row_sources([MeasPortRow("tank", "1,3", "2")],
                          [ConnectionRow(kind="ground", ports="3")])
        self.assertEqual(src[1], "probe row 1 (+)")
        self.assertEqual(src[2], "probe row 1 (−)")
        self.assertEqual(src[3], "conn row 1")

    def test_a_range_marks_every_port_in_it(self):
        src = row_sources([], [ConnectionRow(kind="ground", ports="4:1:7")])
        self.assertEqual(sorted(src), [4, 5, 6, 7])

    def test_the_partner_side_of_a_short_belongs_to_the_same_row(self):
        src = row_sources([], [ConnectionRow(kind="short", ports="5", to="6")])
        self.assertEqual(src[5], "conn row 1")
        self.assertEqual(src[6], "conn row 1")

    def test_a_kept_as_text_line_is_named_by_its_line_number(self):
        src = row_sources([], [], "# a note\n9 ground\n")
        self.assertEqual(src[9], "text line 2")

    def test_a_kept_as_text_line_beats_a_table_row(self):
        """extra_lines is emitted LAST, so it wins -- and it has no widget."""
        src = row_sources([], [ConnectionRow(kind="ground", ports="4")],
                          "4 signal A\n")
        self.assertEqual(src[4], "text line 1")

    def test_a_half_typed_range_contributes_nothing_and_does_not_raise(self):
        src = row_sources([MeasPortRow("t", "5:", "")],
                          [ConnectionRow(kind="ground", ports="-")])
        self.assertEqual(src, {})

    def test_blank_rows_are_skipped(self):
        self.assertEqual(row_sources([MeasPortRow()], [ConnectionRow()]), {})


# ============================================================================
# PURE: collapse_ports / _append_port_spec
# ============================================================================

class TestCollapsePorts(unittest.TestCase):
    def test_runs_collapse(self):
        self.assertEqual(collapse_ports([1, 2, 3, 7]), "1-3,7")

    def test_a_pair_is_still_a_run(self):
        self.assertEqual(collapse_ports([4, 5]), "4-5")

    def test_it_sorts_and_dedupes(self):
        self.assertEqual(collapse_ports([7, 1, 7, 2]), "1-2,7")

    def test_empty(self):
        self.assertEqual(collapse_ports([]), "")

    def test_there_is_never_a_space_in_it(self):
        """
        The DSL is whitespace-tokenised and the port field is parts[0], so
        '1-3, 7' would parse as the port field '1-3,' with a stray '7' where
        the keyword belongs.  This is the property that makes the write-back
        safe, not a formatting preference.
        """
        spec = collapse_ports(list(range(1, 60)) + [70, 72])
        self.assertNotIn(" ", spec)

    def test_it_round_trips_through_the_port_parser(self):
        ports = [1, 2, 3, 7, 8, 20]
        self.assertEqual(parse_port_range(collapse_ports(ports)), ports)


class TestAppendPortSpec(unittest.TestCase):
    def test_it_appends_rather_than_replaces(self):
        """The field is the only place that spec exists."""
        self.assertEqual(_append_port_spec("1,2", "5-7"), "1,2,5-7")

    def test_an_empty_field_takes_the_new_spec_alone(self):
        self.assertEqual(_append_port_spec("", "5-7"), "5-7")
        self.assertEqual(_append_port_spec("   ", "5-7"), "5-7")

    def test_no_space_is_introduced(self):
        self.assertNotIn(" ", _append_port_spec("1,2", "5-7"))


# ============================================================================
# PURE: the open-port name check (B2) -- false alarms first
# ============================================================================

class TestNamePrefix(unittest.TestCase):
    def test_a_trailing_ball_number_is_stripped(self):
        self.assertEqual(name_prefix("VSS_ball_31"), "VSS_ball")
        self.assertEqual(name_prefix("coil1"), "coil")

    def test_a_name_that_does_not_end_in_a_digit_is_its_own_family(self):
        self.assertEqual(name_prefix("in_p"), "in_p")
        self.assertEqual(name_prefix("c1_p"), "c1_p")

    def test_digits_in_the_MIDDLE_are_never_stripped(self):
        """
        c1_p and c2_p (tests/fixtures/coupled_4port_float.s4p) are two
        different coils.  Stripping digits anywhere would make them one family
        and every use of that fixture would raise a warning.
        """
        self.assertNotEqual(name_prefix("c1_p"), name_prefix("c2_p"))

    def test_an_all_digit_name_has_no_prefix(self):
        self.assertEqual(name_prefix("31"), "")

    def test_empty_and_none(self):
        self.assertEqual(name_prefix(""), "")
        self.assertEqual(name_prefix(None), "")


def _roles_from(spec):
    """[(name, role)] -> [PortRole], 1-based in order."""
    return [PortRole(index=i, name=n, role=r)
            for i, (n, r) in enumerate(spec, start=1)]


class TestOpenNameClusters(unittest.TestCase):
    def test_the_reported_case_fires(self):
        """54 ground balls, 3 of them left open."""
        spec = ([(f"VSS_ball_{i}", ROLE_GROUND) for i in range(1, 52)]
                + [("VSS_ball_52", ROLE_OPEN), ("VSS_ball_53", ROLE_OPEN),
                   ("VSS_ball_54", ROLE_OPEN)])
        clusters = open_name_clusters(_roles_from(spec))
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].prefix, "VSS_ball")
        self.assertEqual(clusters[0].kind, "grounded")
        self.assertEqual(clusters[0].open_ports, (52, 53, 54))
        self.assertEqual(clusters[0].terminated, 51)

    def test_the_message_names_the_ports_and_caps_the_list(self):
        spec = ([(f"VSS_{i}", ROLE_GROUND) for i in range(1, 30)]
                + [(f"VSS_{i}", ROLE_OPEN) for i in range(30, 35)])
        msg = open_port_name_messages(_roles_from(spec))
        self.assertEqual(len(msg), 1)
        self.assertTrue(msg[0].startswith("⚠"), msg)
        self.assertIn("VSS_30", msg[0])
        self.assertIn("VSS_32", msg[0])
        self.assertNotIn("VSS_34", msg[0])      # capped at three + "…"
        self.assertIn("…", msg[0])

    def test_a_probed_family_says_probed(self):
        spec = ([(f"coil_{i}", ROLE_PROBE_PLUS) for i in range(1, 8)]
                + [("coil_8", ROLE_OPEN)])
        self.assertEqual(open_name_clusters(_roles_from(spec))[0].kind,
                         "probed")

    # ---- the false alarms -------------------------------------------------

    def test_a_two_member_family_is_not_evidence(self):
        """
        tests/fixtures/coupled_2port_gndref.s2p is coil1 / coil2, and probing
        one while the other floats is the ordinary way to use it.  This is why
        MIN_FAMILY exists.
        """
        spec = [("coil1", ROLE_PROBE_PLUS), ("coil2", ROLE_OPEN)]
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])
        self.assertGreater(OPEN_CLUSTER_MIN_FAMILY, 2)

    def test_a_deliberate_half_and_half_split_is_not_a_remnant(self):
        """Grounding 5 of 10 is a decision; leaving 3 of 54 is a typo."""
        spec = ([(f"GND_{i}", ROLE_GROUND) for i in range(1, 6)]
                + [(f"GND_{i}", ROLE_OPEN) for i in range(6, 11)])
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])

    def test_a_file_whose_ports_are_all_named_portN_stays_silent(self):
        """One family, most of it open -- there is no remnant to report."""
        spec = ([(f"port{i}", ROLE_GROUND) for i in range(1, 20)]
                + [(f"port{i}", ROLE_OPEN) for i in range(20, 154)])
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])

    def test_a_file_with_no_port_names_at_all_stays_silent(self):
        spec = [("", ROLE_GROUND)] * 20 + [("", ROLE_OPEN)] * 2
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])

    def test_a_family_with_too_few_terminated_members_stays_silent(self):
        spec = ([(f"BALL_{i}", ROLE_GROUND) for i in range(1, 3)]
                + [(f"BALL_{i}", ROLE_ELEMENT) for i in range(3, 9)]
                + [("BALL_9", ROLE_OPEN)])
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])

    def test_a_fully_terminated_family_says_nothing(self):
        spec = [(f"VSS_{i}", ROLE_GROUND) for i in range(1, 10)]
        self.assertEqual(open_name_clusters(_roles_from(spec)), [])


class TestOpenNameCheckOnRealFixtures(unittest.TestCase):
    """
    The check runs against every fixture in the repo under the config that
    fixture exists for.  Any hit here is a false alarm by construction.
    """

    @classmethod
    def setUpClass(cls):
        _ensure_fixtures()

    def _names(self, fname):
        return parse_touchstone(FIX / fname).port_names

    def test_no_fixture_raises_a_warning_for_its_natural_spec(self):
        cases = [
            # (fixture, mport rows, conn rows)  -- the way it is actually used
            ("coupled_2port_gndref.s2p", [MeasPortRow("c1", "1", "")], []),
            ("coupled_4port_float.s4p", [MeasPortRow("c1", "1", "2")], []),
            ("coupled_4port_diff.s4p", [MeasPortRow("c1", "1", "2")], []),
            ("diff_pair_4port.s4p", [MeasPortRow("in", "1", "2")], []),
            ("decap_4port.s4p", [MeasPortRow("d", "3", "4")],
             [ConnectionRow(kind="ground", ports="1")]),
        ]
        for fname, mports, conn in cases:
            with self.subTest(fixture=fname):
                names = self._names(fname)
                msgs = _validation_messages(mports, conn, "", len(names),
                                            names)
                self.assertFalse([m for m in msgs if "left OPEN" in m], msgs)


class TestValidationMessagesWiring(unittest.TestCase):
    """B2 as the user meets it: on the validation strip."""

    NAMES = PKG_NAMES
    _KEEP = object()

    def _msgs(self, gnd, names=_KEEP):
        return _validation_messages(
            [MeasPortRow("sig", "9", "10")],
            [ConnectionRow(kind="ground", ports=gnd)],
            "", len(self.NAMES),
            self.NAMES if names is self._KEEP else names)

    def test_a_forgotten_ground_ball_is_reported(self):
        msgs = self._msgs("1-7")            # VSS_ball_8 left open
        hits = [m for m in msgs if "left OPEN" in m]
        self.assertEqual(len(hits), 1, msgs)
        self.assertIn("VSS_ball_8", hits[0])
        self.assertIn("VSS_ball", hits[0])

    def test_grounding_all_of_them_says_nothing(self):
        msgs = self._msgs("1-8")
        self.assertFalse([m for m in msgs if "left OPEN" in m], msgs)

    def test_it_does_not_fire_without_port_names(self):
        """A file with no names offers no evidence; silence is the answer."""
        msgs = self._msgs("1-7", names=None)
        self.assertFalse([m for m in msgs if "left OPEN" in m], msgs)
        msgs = self._msgs("1-7", names=[""] * len(self.NAMES))
        self.assertFalse([m for m in msgs if "left OPEN" in m], msgs)

    def test_it_suppresses_the_green_tick(self):
        """A green tick has to mean 'Calculate will do what you meant'."""
        self.assertNotIn("✓", " ".join(self._msgs("1-7")))

    def test_it_never_raises_on_a_half_typed_spec(self):
        for gnd in ("", "5:", "-", "1-", "abc"):
            with self.subTest(gnd=gnd):
                self._msgs(gnd)


# ============================================================================
# PURE: any trace -> rows (so the window works in every mode)
# ============================================================================

class TestTraceRoleRows(unittest.TestCase):
    def _roles(self, tc, nports, names=None):
        mports, conn, extra, src = _trace_role_rows(tc)
        term = build_terminations_rows(mports, conn, extra, nports=nports)
        return port_roles(term, nports, names, src), mports

    def test_mode_1(self):
        tc = TraceConfig(mode=1, port_a="1", gnd_ports="3-4")
        roles, _ = self._roles(tc, 5)
        self.assertEqual([r.role for r in roles],
                         [ROLE_PROBE_PLUS, ROLE_OPEN, ROLE_GROUND,
                          ROLE_GROUND, ROLE_OPEN])
        self.assertEqual(roles[0].source, "Signal / Port A")
        self.assertEqual(roles[2].source, "GND / VDD")

    def test_mode_2_uses_the_minus_side_for_port_B(self):
        tc = TraceConfig(mode=2, port_a="1", port_b="2", gnd_ports="4")
        roles, _ = self._roles(tc, 4)
        self.assertEqual([r.role for r in roles],
                         [ROLE_PROBE_PLUS, ROLE_PROBE_MINUS, ROLE_OPEN,
                          ROLE_GROUND])
        self.assertEqual(roles[1].source, "Port B")

    def test_mode_3_short_pairs_become_shorted_ports(self):
        tc = TraceConfig(mode=3, port_a="1", port_b="2", short_pairs="3-4")
        roles, _ = self._roles(tc, 4)
        self.assertEqual([r.role for r in roles[2:]],
                         [ROLE_SHORTED, ROLE_SHORTED])
        self.assertEqual(roles[2].source, "Short Pairs")

    def test_mode_3_survives_a_half_typed_short_field(self):
        tc = TraceConfig(mode=3, port_a="1", port_b="2", short_pairs="3-")
        roles, _ = self._roles(tc, 4)
        self.assertEqual(roles[0].role, ROLE_PROBE_PLUS)

    def test_mode_6_reads_the_table_and_the_gnd_field(self):
        tc = TraceConfig(mode=6,
                         mports=[MeasPortRow("a1", "1", "2"),
                                 MeasPortRow("a2", "3", "4")],
                         gnd_ports="5")
        roles, _ = self._roles(tc, 5)
        self.assertEqual([r.role for r in roles],
                         [ROLE_PROBE_PLUS, ROLE_PROBE_MINUS, ROLE_PROBE_PLUS,
                          ROLE_PROBE_MINUS, ROLE_GROUND])
        self.assertEqual(roles[0].source, "probe row 1 (+)")
        self.assertEqual(roles[4].source, "GND / VDD")

    def test_mode_6_probe_and_ground_overlap_is_SHOWN_not_refused(self):
        """
        build_terminations_coupling RAISES on this, which is right for
        Calculate and exactly wrong for a window whose job is to show what was
        typed.  The window takes the permissive rows path and flags the row.
        """
        tc = TraceConfig(mode=6, mports=[MeasPortRow("a1", "1", "2")],
                         gnd_ports="1")
        roles, mports = self._roles(tc, 2)
        self.assertEqual(roles[0].role, ROLE_GROUND)
        self.assertEqual(_role_warnings(roles, mports)[1],
                         WARN_PROBE_AND_GROUND)

    def test_mode_5_is_the_tables_verbatim(self):
        tc = TraceConfig(mode=5, mports=[MeasPortRow("t", "1", "2")],
                         conn_rows=[ConnectionRow(kind="ground", ports="3")],
                         extra_lines="4 vdd\n")
        mports, conn, extra, src = _trace_role_rows(tc)
        self.assertEqual(mports, tc.mports)
        self.assertEqual(conn, tc.conn_rows)
        self.assertEqual(extra, "4 vdd\n")
        self.assertEqual(src[4], "text line 1")

    def test_an_empty_named_mode_produces_no_rows_at_all(self):
        tc = TraceConfig(mode=1, port_a="", gnd_ports="")
        mports, conn, extra, src = _trace_role_rows(tc)
        self.assertEqual((mports, conn, extra, src), ([], [], "", {}))


class TestRoleWarnings(unittest.TestCase):
    def test_a_port_from_the_kept_text_is_flagged(self):
        tc = TraceConfig(mode=5, mports=[MeasPortRow("t", "1", "2")],
                         extra_lines="3 ground\n")
        mports, conn, extra, src = _trace_role_rows(tc)
        term = build_terminations_rows(mports, conn, extra, nports=4)
        roles = port_roles(term, 4, None, src)
        self.assertEqual(_role_warnings(roles, mports)[3],
                         WARN_FROM_KEPT_TEXT)

    def test_a_table_row_is_not_flagged(self):
        tc = TraceConfig(mode=5, mports=[MeasPortRow("t", "1", "2")],
                         conn_rows=[ConnectionRow(kind="ground", ports="3")])
        mports, conn, extra, src = _trace_role_rows(tc)
        term = build_terminations_rows(mports, conn, extra, nports=4)
        roles = port_roles(term, 4, None, src)
        self.assertEqual(_role_warnings(roles, mports), {})

    def test_an_open_port_that_looks_terminated_is_flagged(self):
        roles = _roles_from(
            [(f"VSS_{i}", ROLE_GROUND) for i in range(1, 9)]
            + [("VSS_9", ROLE_OPEN)])
        self.assertEqual(_role_warnings(roles)[9],
                         WARN_OPEN_LOOKS_TERMINATED)


class TestRolesHeader(unittest.TestCase):
    def test_it_names_the_file_and_the_buckets(self):
        roles = _roles_from([("a", ROLE_PROBE_PLUS), ("b", ROLE_GROUND),
                             ("c", ROLE_OPEN), ("d", ROLE_OPEN)])
        self.assertEqual(_roles_header("pkg.s4p", 4, roles),
                         "pkg.s4p — 4 ports · 1 probe · 1 ground · 2 open")

    def test_no_file_says_so(self):
        self.assertEqual(_roles_header("", None, []), "(no file selected)")


class TestFixedMapFilter(unittest.TestCase):
    """
    The Tk bug: a Treeview ignores tag colours when the style map carries
    negated state specs, because ('!disabled', '!selected') matches every
    ordinary row and the map outranks the tag.  Silent failure -- the colours
    simply do not appear.
    """

    def test_the_offending_entry_is_dropped(self):
        entries = [("!disabled", "!selected", "#000000"),
                   ("disabled", "#a0a0a0"),
                   ("selected", "#ffffff")]
        self.assertEqual(_fixed_map_filter(entries), entries[1:])

    def test_everything_else_survives_in_order(self):
        entries = [("disabled", "grey"), ("selected", "white")]
        self.assertEqual(_fixed_map_filter(entries), entries)

    def test_an_empty_map_is_fine(self):
        self.assertEqual(_fixed_map_filter([]), [])


# ============================================================================
# TK: the Ports & Roles window
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class _WindowCase(unittest.TestCase):
    MODE = 5

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = _write_named_snp(
            Path(cls._tmp.name) / f"pkg.s{len(PKG_NAMES)}p", PKG_NAMES)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.fe = FileEntry(parse_touchstone(self.path))
        self.app.files.append(self.fe)
        self.app._refresh_file_list()
        self.app._refresh_file_combobox()
        self.tc = TraceConfig(id=1, file_label=self.fe.label, mode=self.MODE,
                              label="t1", port_a="9", gnd_ports="1-7",
                              mports=[MeasPortRow("sig", "9", "10")],
                              conn_rows=[ConnectionRow(kind="ground",
                                                       ports="1-7")])
        self.app.traces.append(self.tc)
        self.app._refresh_trace_list()
        self.app.traces_lb.selection_set(0)
        self.app._on_trace_selected()
        self._settle()

    def tearDown(self):
        self.app.destroy()

    def _settle(self, rounds=4):
        for _ in range(rounds):
            self.app.update_idletasks()
            self.app.update()

    def _open(self):
        self.app._on_show_ports()
        self._settle()
        return self.app._port_roles_win


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowBasics(_WindowCase):
    def test_show_ports_opens_the_window(self):
        win = self._open()
        self.assertIsInstance(win, PortRolesWindow)
        self.assertTrue(win.winfo_exists())

    def test_asking_twice_raises_the_same_window(self):
        first = self._open()
        second = self._open()
        self.assertIs(first, second)

    def test_the_header_names_the_file_and_counts_the_buckets(self):
        win = self._open()
        text = win.header.cget("text")
        self.assertIn(self.fe.label, text)
        self.assertIn(f"{len(PKG_NAMES)} ports", text)
        self.assertIn("7 ground", text)
        self.assertIn("2 probe", text)

    def test_every_port_of_the_file_gets_a_row(self):
        win = self._open()
        self.assertEqual(len(win.tree.get_children()), len(PKG_NAMES))

    def test_the_rows_carry_the_names_the_dropdowns_cannot(self):
        win = self._open()
        vals = win.tree.item("9", "values")
        self.assertEqual(vals[0], "9")
        self.assertEqual(vals[1], "sig_in")

    def test_the_source_column_says_which_row_put_it_there(self):
        win = self._open()
        self.assertEqual(win.tree.item("1", "values")[3], "conn row 1")
        self.assertEqual(win.tree.item("9", "values")[3], "probe row 1 (+)")

    def test_an_unnamed_port_reads_as_unnamed_not_blank(self):
        win = self._open()
        win.refresh("h", [PortRole(index=1, name="", role=ROLE_OPEN)], {})
        self.assertEqual(win.tree.item("1", "values")[1], "(unnamed)")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowFilterAndSort(_WindowCase):
    def test_the_name_filter_is_a_substring_match(self):
        win = self._open()
        win.filter_var.set("VDD")
        self._settle()
        self.assertEqual([r.name for r in win.visible_roles()],
                         ["VDD_ball_1", "VDD_ball_2"])
        self.assertEqual(len(win.tree.get_children()), 2)

    def test_the_filter_is_case_insensitive(self):
        win = self._open()
        win.filter_var.set("vdd_ball")
        self._settle()
        self.assertEqual(len(win.visible_roles()), 2)

    def test_hide_open_drops_exactly_the_open_ports(self):
        win = self._open()
        before = [r.index for r in win.visible_roles() if r.role == ROLE_OPEN]
        self.assertTrue(before)
        win.hide_open_var.set(True)
        win._repopulate()
        self.assertEqual(
            [r for r in win.visible_roles() if r.role == ROLE_OPEN], [])

    def test_sorting_by_number_sorts_on_the_INT_not_the_string(self):
        """
        12 ports is enough for the difference to show: a string sort puts port
        10 between 1 and 2, which is the classic Treeview bug.
        """
        win = self._open()
        # It opens sorted by number, ascending.
        self.assertEqual([r.index for r in win.visible_roles()],
                         list(range(1, len(PKG_NAMES) + 1)))
        win._on_sort("index")           # clicking the active column reverses
        self.assertEqual([r.index for r in win.visible_roles()],
                         list(range(len(PKG_NAMES), 0, -1)))
        win._on_sort("index")
        self.assertEqual([r.index for r in win.visible_roles()],
                         list(range(1, len(PKG_NAMES) + 1)))

    def test_sorting_by_another_column_starts_ascending(self):
        win = self._open()
        win._on_sort("index")
        win._on_sort("index")           # now descending
        win._on_sort("name")
        names = [r.name for r in win.visible_roles()]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_the_count_label_says_how_many_are_hidden(self):
        win = self._open()
        self.assertIn("ports", win.count_lbl.cget("text"))
        win.filter_var.set("VDD")
        self._settle()
        self.assertEqual(win.count_lbl.cget("text"),
                         f"2 of {len(PKG_NAMES)}")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowTreeviewHazards(_WindowCase):
    def test_the_row_height_is_set_from_the_font_not_left_at_20px(self):
        """
        Treeview freezes rowheight at 20 px whatever `tk scaling` and whatever
        font the style carries, so at 150% DPI the text clips.  Unset, the
        lookup returns "" and int() raises -- which is the mutation.
        """
        self._open()
        want = tkfont.nametofont("TkDefaultFont").metrics("linespace") + 4
        got = int(ttk.Style().lookup(PORT_ROLES_STYLE, "rowheight"))
        self.assertEqual(got, want)

    def test_it_is_a_DERIVED_style_so_other_treeviews_are_untouched(self):
        win = self._open()
        self.assertEqual(str(win.tree.cget("style")), PORT_ROLES_STYLE)
        self.assertNotEqual(PORT_ROLES_STYLE, "Treeview")

    def test_the_style_map_carries_no_negated_state_spec(self):
        """
        NOTE: this assertion is vacuous on a build whose base map is already
        clean -- measured on the vista theme here, Style().map('Treeview') is
        [('disabled', …), ('selected', …)] and there is nothing to filter. The
        real guard on the RULE is TestFixedMapFilter above, which is pure and
        does go red when the filter is removed. This one is the end-to-end
        check for the builds that do carry the offending entry.
        """
        self._open()
        for opt in ("foreground", "background"):
            with self.subTest(option=opt):
                entries = ttk.Style().map(PORT_ROLES_STYLE, query_opt=opt)
                self.assertFalse(
                    [e for e in entries
                     if tuple(e[:2]) == ("!disabled", "!selected")], entries)

    def test_each_row_is_tagged_with_its_role(self):
        win = self._open()
        self.assertIn("role_ground", win.tree.item("1", "tags"))
        self.assertIn(f"role_{ROLE_PROBE_PLUS}", win.tree.item("9", "tags"))

    def test_the_treeview_scrolls_itself_and_is_not_double_handled(self):
        """
        'Treeview' is in App._WHEEL_OWNERS, so the router bails out over it and
        Tk's own class binding scrolls it.  Registering a handler would be dead
        code; taking Treeview out of the set to reach one would break every
        other Treeview in the process.
        """
        win = self._open()
        self.assertIn("Treeview", App._WHEEL_OWNERS)
        self.assertNotIn(str(win.tree), self.app._scrollables)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowLayout(_WindowCase):
    """
    pack UNMAPS what does not fit, starting from the END -- so the write-back
    buttons must be packed before the expanding list, or dragging the window
    short takes them off screen entirely (winfo_ismapped() == 0, not clipped).
    """

    def test_the_buttons_survive_the_window_being_dragged_to_its_minsize(self):
        # A MAPPED window, and that means the App too: the Toplevel is
        # transient() on it, and the WM withdraws a transient whose master is
        # withdrawn -- on which every winfo_ismapped() reads 0 whatever the
        # layout is, which is exactly the wrong answer being ruled out.
        self.app.deiconify()
        win = self._open()
        win.deiconify()
        win.geometry("430x260")
        self._settle(6)
        for name, w in (("Set as ground", win.gnd_btn),
                        ("Set as probe +", win.probe_btn),
                        ("the list", win.tree)):
            with self.subTest(widget=name):
                self.assertEqual(w.winfo_ismapped(), 1,
                                 f"{name} is not on screen at 430x260")


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowWarnings(_WindowCase):
    def test_a_forgotten_ground_ball_is_flagged_in_the_list(self):
        self.tc.conn_rows = [ConnectionRow(kind="ground", ports="1-7")]
        win = self._open()
        # VSS_ball_8 is port 8, open, and its name matches the grounded set.
        self.assertIn("warn", win.tree.item("8", "tags"))
        self.assertNotIn("warn", win.tree.item("1", "tags"))

    def test_the_warning_colour_is_the_one_the_rest_of_the_app_uses(self):
        win = self._open()
        self.assertEqual(str(win.tree.tag_configure("warn", "foreground")),
                         WARN_FG)

    def test_selecting_a_flagged_row_says_what_is_wrong(self):
        win = self._open()
        win.tree.selection_set("8")
        self._settle()
        self.assertIn(WARN_OPEN_LOOKS_TERMINATED, win.detail.cget("text"))

    def test_a_kept_as_text_port_is_flagged(self):
        self.tc.conn_rows = []
        self.tc.extra_lines = "1-7 ground\n"
        self.app._on_trace_selected()
        win = self._open()
        self.assertIn("warn", win.tree.item("1", "tags"))


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWindowLiveUpdate(_WindowCase):
    def test_editing_the_spec_re_renders_the_window(self):
        win = self._open()
        self.assertEqual(win.tree.item("11", "values")[2], ROLE_OPEN)
        self.app.ed_conn_table.add_row({"kind": "ground", "ports": "11-12"})
        self._settle()
        self.assertEqual(win.tree.item("11", "values")[2], ROLE_GROUND)

    def test_it_survives_the_window_being_closed(self):
        win = self._open()
        win.destroy()
        self._settle()
        self.app._refresh_port_roles_window()
        self.assertIsNone(self.app._port_roles_win)

    def test_the_refresh_never_raises(self):
        """Same contract as _apply_editor_strips: it runs from a Tk trace."""
        win = self._open()
        self.app.files.clear()
        self.app.ed_file_var.set("nope")
        self.app._refresh_port_roles_window()       # must not raise
        self.assertTrue(win.winfo_exists())

    def test_a_mode_1_trace_keeps_the_window_current(self):
        """
        Modes 1/2/3 have no tables, and the strips used to refresh only in
        mode 5 -- so without _strips_wanted the window froze on the exact edit
        it exists to check.
        """
        self.tc.mode = 1
        self.app._on_trace_selected()
        win = self._open()
        self.app.ed_gnd.set_value("1-8")
        self._settle()
        self.assertEqual(win.tree.item("8", "values")[2], ROLE_GROUND)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWriteBackMode5(_WindowCase):
    MODE = 5

    def test_ground_lands_as_a_collapsed_range_in_a_new_row(self):
        win = self._open()
        win.tree.selection_set("3", "4", "5")
        self._settle()
        win._send("ground")
        self._settle()
        rows = [(r.kind, r.ports) for r in self.app.ed_conn_table.get_rows()]
        self.assertIn(("ground", "3-5"), rows)

    def test_a_gappy_selection_keeps_its_gaps(self):
        win = self._open()
        win.tree.selection_set("3", "4", "7")
        self._settle()
        win._send("ground")
        self._settle()
        rows = [r.ports for r in self.app.ed_conn_table.get_rows()]
        self.assertIn("3-4,7", rows)

    def test_probe_lands_in_the_measurement_port_table(self):
        win = self._open()
        win.tree.selection_set("11", "12")
        self._settle()
        win._send("probe+")
        self._settle()
        self.assertIn("11-12",
                      [r.plus for r in self.app.ed_mp_table.get_rows()])

    def test_the_write_reaches_the_trace_through_auto_apply(self):
        """
        Routed through the widgets the user types into, so the sync, the
        strips and the stale marker all follow -- not poked into the
        TraceConfig, which the next keystroke would overwrite.  Both halves
        are asserted: the EDITOR has the row (so it is not a direct write to
        the trace) and the TRACE has it after the flush (so auto-apply ran
        without anyone calling it here).
        """
        win = self._open()
        win.tree.selection_set("11", "12")
        self._settle()
        win._send("ground")
        self.assertIn("11-12",
                      [r.ports for r in self.app.ed_conn_table.get_rows()])
        self.app._flush_editor_sync()
        self._settle()
        self.assertIn("11-12", [r.ports for r in self.tc.conn_rows])
        self.assertTrue(self.tc.stale or self.tc.Z is None)

    def test_nothing_selected_writes_nothing(self):
        win = self._open()
        win.tree.selection_remove(*win.tree.selection())
        before = len(self.app.ed_conn_table.get_rows())
        win._send("ground")
        self._settle()
        self.assertEqual(len(self.app.ed_conn_table.get_rows()), before)

    def test_a_frozen_trace_refuses_the_write(self):
        self.tc.frozen = True
        self.app._on_trace_selected()
        win = self._open()
        win.tree.selection_set("3")
        self._settle()
        before = [r.ports for r in self.app.ed_conn_table.get_rows()]
        msg = self.app.apply_ports_as("ground", [3])
        self.assertIn("frozen", msg)
        self.assertEqual([r.ports for r in self.app.ed_conn_table.get_rows()],
                         before)


@unittest.skipUnless(TK_OK, "no Tk display available")
class TestWriteBackOtherModes(_WindowCase):
    MODE = 6

    def test_mode_6_ground_lands_in_the_gnd_field(self):
        win = self._open()
        win.tree.selection_set("11", "12")
        self._settle()
        win._send("ground")
        self._settle()
        self.assertEqual(self.app.ed_gnd.get_value(), "1-7,11-12")

    def test_mode_6_probe_lands_in_the_measurement_port_table(self):
        win = self._open()
        win.tree.selection_set("11")
        self._settle()
        win._send("probe+")
        self._settle()
        self.assertIn("11", [r.plus for r in self.app.ed_mp_table.get_rows()])

    def test_mode_1_uses_the_two_fields(self):
        self.tc.mode = 1
        self.tc.gnd_ports = ""
        self.app._on_trace_selected()
        win = self._open()
        win.tree.selection_set("1", "2", "3")
        self._settle()
        win._send("ground")
        self._settle()
        self.assertEqual(self.app.ed_gnd.get_value(), "1-3")
        win.tree.selection_set("11")
        self._settle()
        win._send("probe+")
        self._settle()
        self.assertEqual(self.app.ed_porta.get_value(), "9,11")


if __name__ == "__main__":
    unittest.main()
