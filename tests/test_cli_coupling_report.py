"""
The CLI's coupling report, on the three cases no shipped fixture produces.

`tests/fixtures/cli_reference/` pins this surface byte for byte over 143 real
invocations, which is the guard that matters for everything a fixture CAN
reach.  What it cannot reach is exactly where the three divergences closed here
used to hide:

  * a pair whose |k| exceeds 1 -- the "check the port setup" prompt was
    pane-only, so on a headless red-zone box (`deploy/doctor.sh` calls a
    CLI-only install SUCCESSFUL, tier 2) it was said nowhere at all;
  * a pair whose rank key is UNDEFINED, which must sort LAST and must never be
    folded away -- NaN is a missing measurement, not a small number, and the
    only .sNp in this repo that produces one produces nothing else, so the
    ordering is invisible in the reference;
  * the reciprocity alarm, which no fixture trips, and the not-checkable
    reading, which no fixture reaches with a finite pair beside it.

Everything here is pure: it builds a `CouplingResult` by hand and captures
stdout.  No tkinter, no display, no Touchstone file -- the same one property
that qualifies `test_cli_golden` for FAST_MODULES.
"""

from __future__ import annotations

import io
import math
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import pkg_rlc.frontend.cli as cli  # noqa: E402
import pkg_rlc.present.report as report  # noqa: E402
from pkg_rlc.physics.core import (  # noqa: E402
    CouplingResult,
    PairCoupling,
    PortRLC,
)

NAN = float("nan")


def _port(name: str, L: float = 2e-9) -> PortRLC:
    return PortRLC(name=name, Z=complex(0.6, 62.8), R_ohm=0.6, L_henry=L,
                   C_farad=-5.07e-13, Q=104.7)


def _pair(a: str, b: str, ratio: float, *, k: float = 0.3,
          im: float = 25.13) -> PairCoupling:
    """One pair whose rank key is `ratio` (both sides equal, so max() is it)."""
    db = (20.0 * math.log10(abs(ratio))
          if math.isfinite(ratio) and ratio != 0.0 else NAN)
    return PairCoupling(
        name_a=a, name_b=b,
        Z_ab=complex(0.0, im) if math.isfinite(ratio) else complex(NAN, NAN),
        M_henry=8e-10 if math.isfinite(ratio) else NAN,
        C_c_farad=-1.27e-12 if math.isfinite(ratio) else NAN,
        k=k,
        M_over_La=ratio, M_over_Lb=ratio,
        M_over_La_dB=db, M_over_Lb_dB=db,
    )


def _result(pairs, recip: float = 7.07e-16) -> CouplingResult:
    names = sorted({n for p in pairs for n in (p.name_a, p.name_b)})
    G = max(len(names), 1)
    Z = np.zeros((G, G), dtype=complex)
    return CouplingResult(freq_hz=5e9, Z_matrix=Z, names=names,
                          ports=[_port(n) for n in names], pairs=list(pairs),
                          reciprocity_error=recip)


def _render(res) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_coupling_report(res)
    return buf.getvalue()


def _headlines(text: str) -> list[str]:
    """The `  a <-> b …` lines, in the order they were printed."""
    return [ln for ln in text.splitlines() if " <-> " in ln]


class TestTheRankKeyIsOnTheSurface(unittest.TestCase):
    """Decision 2: the CLI printed neither the ranking nor its key."""

    def test_worst_M_over_L_is_printed_at_all(self):
        """It is the rank key and the number a spur budget is written
        against, and this surface did not print it anywhere.  Mutation:
        drop it from the headline -> no line carries 'worst M/L'.
        """
        text = _render(_result([_pair("vic", "agg", 0.4)]))
        self.assertIn("worst M/L", text)
        # -7.959 dB, which is 20*log10(0.4) -- the pane's own key.
        self.assertIn("-7.959 dB", text)

    def test_the_pairs_come_out_strongest_first(self):
        """Mutation: iterate res.pairs instead of the ranked list -> the
        headlines come back in the (a, b) order they were built in.
        """
        pairs = [_pair("weak", "x", 0.01),
                 _pair("loud", "y", 0.9),
                 _pair("mid", "z", 0.2)]
        heads = _headlines(_render(_result(pairs)))
        self.assertEqual(3, len(heads))
        self.assertIn("loud", heads[0])
        self.assertIn("mid", heads[1])
        self.assertIn("weak", heads[2])

    def test_it_is_the_PANE_s_ranker_and_not_a_copy(self):
        """A second implementation of the key is the whole failure this
        closes.  The CLI must hold the pane's own object.
        """
        self.assertIs(report.rank_coupling_pairs, cli.rank_coupling_pairs)
        self.assertIs(report._pair_flag, cli._pair_flag)
        self.assertIs(report.reciprocity_verdict, cli.reciprocity_verdict)


class TestTheFloorAndItsTwoExceptions(unittest.TestCase):
    """`rank_coupling_pairs`' rules have to hold on this surface too."""

    def test_a_weak_pair_is_folded_and_counted(self):
        pairs = [_pair("loud", "y", 0.9)] + [
            _pair(f"q{i}", "z", 1e-6) for i in range(4)]
        text = _render(_result(pairs))
        self.assertEqual(1, len(_headlines(text)))
        self.assertIn("+4 pairs below -60 dB", text)

    def test_the_fold_pointer_at_the_csv_is_true(self):
        """`_write_coupling_csv` enumerates every unordered pair off the Z
        matrix and has no floor, which is what makes the pointer honest.
        """
        text = _render(_result([_pair("loud", "y", 0.9),
                                _pair("q", "z", 1e-6)]))
        self.assertIn("+1 pair below -60 dB", text)
        self.assertIn("--csv", text)

    def test_an_UNDEFINED_pair_sorts_last_and_is_NEVER_folded(self):
        """NaN is a missing measurement, not a small number.  Mutation: key
        it at -inf (the bug the Attribution window had) -> it prints FIRST;
        fold it with the weak ones -> it does not print at all.
        """
        pairs = [_pair("gone", "x", NAN), _pair("loud", "y", 0.9),
                 _pair("weak", "z", 1e-6)]
        text = _render(_result(pairs))
        heads = _headlines(text)
        self.assertEqual(2, len(heads))          # 'weak' folded, 'gone' not
        self.assertIn("loud", heads[0])
        self.assertIn("gone", heads[1])
        self.assertIn("+1 pair below -60 dB", text)

    def test_the_strongest_pair_is_never_folded_away(self):
        """A block whose whole content is '3 pairs were too weak to list'
        answers no question.
        """
        text = _render(_result([_pair(f"q{i}", "z", 1e-6 * (i + 1))
                                for i in range(3)]))
        self.assertEqual(1, len(_headlines(text)))
        self.assertIn("+2 pairs below -60 dB", text)

    def test_a_single_pair_is_not_announced_as_ranked(self):
        """Ranking one pair means nothing; the pane says so by omission."""
        one = _render(_result([_pair("vic", "agg", 0.4)]))
        two = _render(_result([_pair("vic", "agg", 0.4),
                               _pair("vic", "oth", 0.2)]))
        self.assertNotIn("strongest first", one)
        self.assertIn("strongest first", two)


class TestTheFlagReachesThisSurface(unittest.TestCase):
    """Decision 4: `_pair_flag` was pane-only."""

    def test_k_above_one_says_check_the_port_setup(self):
        """|k| > 1 means the port setup is probably wrong and core's rule is
        to note it rather than clamp.  No shipped fixture produces one, which
        is why this test exists rather than a reference case.
        """
        text = _render(_result([_pair("vic", "agg", 0.4, k=1.5)]))
        self.assertIn("|k|>1", _headlines(text)[0])
        self.assertIn("|k|>1 = check the port setup", text)

    def test_a_healthy_pair_carries_no_k_flag(self):
        text = _render(_result([_pair("vic", "agg", 0.4, k=0.3)]))
        self.assertNotIn("|k|>1", _headlines(text)[0])

    def test_the_flag_names_the_sign_of_Im_Z_ab(self):
        ind = _headlines(_render(_result([_pair("a1", "b1", 0.4, im=25.0)])))
        cap = _headlines(_render(_result([_pair("a1", "b1", 0.4, im=-25.0)])))
        self.assertIn("[ind]", ind[0])
        self.assertIn("[cap]", cap[0])


class TestTheReciprocityVerdict(unittest.TestCase):
    """Decision 3: a SUPERSET of the pane, never a replacement."""

    def test_the_verdict_is_the_headline(self):
        text = _render(_result([_pair("vic", "agg", 0.4)], recip=7.07e-16))
        self.assertIn("Reciprocity: OK -- reciprocal (7.07e-16)", text)

    def test_the_metric_and_the_paragraph_SURVIVE_under_it(self):
        """The pane dropped them to a measured 144-column budget a terminal
        does not have; a headless reader has no other source for what the
        metric means.  Mutation: delete either -> red.
        """
        text = _render(_result([_pair("vic", "agg", 0.4)], recip=7.07e-16))
        self.assertIn("max|Z_ab - Z_ba| / max|Z_ab|", text)
        self.assertIn("A clean EM solve lands at 1e-16..1e-9", text)

    def test_the_alarm_keeps_its_whole_sentence(self):
        """Above RECIPROCITY_WARN the sentence IS the reading."""
        text = _render(_result([_pair("vic", "agg", 0.4)], recip=5e-2))
        self.assertIn("Reciprocity: WARN -- Z_ab and Z_ba disagree (0.05)",
                      text)
        self.assertIn("suspect the EM/de-embedding setup, not this tool",
                      text)

    def test_nothing_to_check_is_its_own_reading(self):
        text = _render(_result([_pair("gone", "x", NAN)], recip=0.0))
        self.assertIn("Reciprocity: NOT CHECKED", text)
        self.assertIn("fix the port setup first", text)

    def test_the_two_surfaces_classify_ONE_number_alike(self):
        """The threshold test lives in one function, so a reading cannot
        differ between the pane and the terminal.  Mutation: change either
        side's comparison to '<' -> the boundary case disagrees.
        """
        from pkg_rlc.physics.core import RECIPROCITY_WARN
        at = [_pair("vic", "agg", 0.4)]
        self.assertEqual(report.RECIP_OK,
                         report.reciprocity_verdict(at, RECIPROCITY_WARN))
        self.assertIn("Reciprocity: OK",
                      _render(_result(at, recip=RECIPROCITY_WARN)))


class TestTheLegendIsPrintedOnce(unittest.TestCase):
    """Decision 4, second half: it was two fragments in two places."""

    def test_one_legend_at_the_foot_of_the_report(self):
        text = _render(_result([_pair("vic", "agg", 0.4)]))
        self.assertEqual(1, text.count("  legend: "))
        self.assertEqual(1, text.count("Norton injection ratio"))
        tail = text.rstrip().splitlines()[-2:]
        self.assertTrue(tail[0].startswith("  legend: "), tail)

    def test_a_single_measurement_port_still_gets_the_sign_key(self):
        """No pairs means no M/L caveat and no |k| entry -- but the self
        table still prints ind/cap/R<0 and still needs its key.
        """
        res = _result([])
        res.names = ["solo"]
        res.ports = [_port("solo")]
        res.Z_matrix = np.zeros((1, 1), dtype=complex)
        text = _render(res)
        self.assertIn("ind = Im(Z)>0", text)
        self.assertNotIn("|k|>1", text)
        self.assertNotIn("Norton injection ratio", text)

    def test_the_M_over_L_caveat_is_kept_VERBATIM(self):
        """One of the six homes of that sentence.  It moved position in this
        change; it did not move a character.
        """
        text = _render(_result([_pair("vic", "agg", 0.4)]))
        self.assertIn(
            "It is not the exact current-transfer ratio |Z_ab/Z_aa|, which "
            "it matches only where omega*L_x >> R_x.", text)


if __name__ == "__main__":
    unittest.main()
