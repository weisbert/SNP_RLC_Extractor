"""
tests/test_plot_controls.py -- the plot panel's control strip.

The bug this pins: the strip was one `pack(side=LEFT)` run of thirteen
controls asking 918 px.  pack UNMAPS what does not fit, starting from the END,
and the right-hand pane is 575 px at the declared 1040x600 minsize -- so
'Im(Z)', 'Q', 'k', the fullscreen-quantity combobox and the Fullscreen button
were simply not on screen, with no scrollbar, no overflow chevron and no other
route to them.  Two of those have no alternative at all: 'k' is the coupling
quantity Mode 6 exists to produce, and Fullscreen is the documented escape
hatch for a readout box too wide for a 4-subplot grid.

It was not only the minsize.  `_clamp_to_screen` opens the window at
min(1500, screen-80), so on the 1280-logical-px laptop its own comment names
the app opened 1200 px wide and Fullscreen was off screen out of the box
(measured: 1160..1280 lost Fullscreen, 1040..1100 lost 'k' as well).

This is the same failure class CLAUDE.md already documents for the Global
Controls frame, applied to the one panel nothing guarded: before this file, no
test in the repo touched PlotPanel's control row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from pkg_rlc_plot import PlotPanel, ReflowRow, reflow_rows  # noqa: E402


def _tk_available() -> bool:
    try:
        root = tk.Tk()
    except Exception:
        return False
    root.destroy()
    return True


TK_OK = _tk_available()


# ============================================================================
# 1 -- the wrap itself, with no display
# ============================================================================

class TestReflowRows(unittest.TestCase):

    def test_everything_fits_on_one_line_when_it_fits(self):
        self.assertEqual(reflow_rows([10, 10, 10], 100), [[0, 1, 2]])

    def test_it_wraps_rather_than_dropping_the_tail(self):
        rows = reflow_rows([40, 40, 40, 40], 100)
        self.assertEqual(rows, [[0, 1], [2, 3]])
        # The property that matters: no item is lost.
        self.assertEqual(sorted(i for r in rows for i in r), [0, 1, 2, 3])

    def test_no_item_is_ever_dropped_at_any_width(self):
        widths = [54, 53, 66, 72, 2, 78, 53, 53, 72, 53, 53, 33, 30, 2, 93, 87]
        for w in range(20, 1200, 7):
            with self.subTest(width=w):
                rows = reflow_rows(widths, w)
                self.assertEqual(sorted(i for r in rows for i in r),
                                 list(range(len(widths))))

    def test_an_item_wider_than_the_strip_gets_its_own_line(self):
        """Not an EMPTY line before it -- that would place the next item on
        top of it."""
        rows = reflow_rows([200, 10], 100)
        self.assertEqual(rows, [[0], [1]])

    def test_no_row_is_empty(self):
        for w in (1, 5, 30, 500):
            for rows in [reflow_rows([50, 60, 70], w)]:
                self.assertTrue(all(rows), f"empty row at width {w}")


# ============================================================================
# 2 -- the real strip, measured off a mapped window
# ============================================================================

@unittest.skipUnless(TK_OK, "no Tk display available")
class TestControlRowIsWhollyOnScreen(unittest.TestCase):
    """
    The assertion is stronger than winfo_ismapped(): a placed widget stays
    'mapped' while it hangs off the right edge, so each control is checked to
    lie WHOLLY inside the strip's own rectangle.
    """

    def _panel(self, width: int):
        root = tk.Tk()
        root.geometry(f"{width}x600")
        root.update()
        panel = PlotPanel(root)
        panel.pack(fill=tk.BOTH, expand=True)
        for _ in range(4):
            root.update_idletasks()
            root.update()
        return root, panel

    def _offenders(self, panel) -> list:
        ctrl = panel.ctrl
        out = []
        for c in ctrl.winfo_children():
            try:
                name = str(c.cget("text")) or c.winfo_class()
            except Exception:
                name = c.winfo_class()
            if not c.winfo_ismapped():
                out.append(f"{name} (unmapped)")
            elif c.winfo_x() + c.winfo_width() > ctrl.winfo_width():
                out.append(f"{name} (past the right edge)")
            elif c.winfo_y() + c.winfo_height() > ctrl.winfo_height():
                out.append(f"{name} (past the bottom edge)")
        return out

    def test_every_control_is_reachable_at_every_supported_width(self):
        # 575 is the right-hand pane at the 1040x600 minsize -- the width the
        # strip actually gets, rather than the window's.
        for width in (575, 700, 1040, 1200, 1500):
            with self.subTest(width=width):
                root, panel = self._panel(width)
                try:
                    self.assertEqual(self._offenders(panel), [])
                finally:
                    root.destroy()

    def test_the_named_casualties_are_back(self):
        """The five controls the measurement named, by name."""
        root, panel = self._panel(575)
        try:
            wanted = {"Im(Z)", "Q", "k", "Fullscreen"}
            seen = set()
            for c in panel.ctrl.winfo_children():
                try:
                    t = str(c.cget("text"))
                except Exception:
                    continue
                if t in wanted and c.winfo_ismapped():
                    seen.add(t)
            self.assertEqual(seen, wanted)
        finally:
            root.destroy()

    def test_it_wraps_only_when_it_has_to(self):
        """A wrap costs plot height, so the default window must not pay it."""
        heights = {}
        for width in (575, 1500):
            root, panel = self._panel(width)
            try:
                heights[width] = panel.ctrl.winfo_height()
            finally:
                root.destroy()
        self.assertGreater(heights[575], heights[1500],
                           "the narrow strip did not wrap")
        self.assertLess(heights[1500], 40,
                        "the wide strip is more than one line tall")

    def test_the_strip_does_not_carry_its_width_into_the_panel(self):
        """
        `place` does not propagate, and that is load-bearing: the 918 px the
        strip used to REQUEST travelled up through PlotPanel to the
        PanedWindow's sash.
        """
        root, panel = self._panel(1040)
        try:
            self.assertLessEqual(panel.ctrl.winfo_reqwidth(), 2)
        finally:
            root.destroy()

    def test_the_layout_settles_instead_of_oscillating(self):
        """
        The wrap decision reads the strip's IMPOSED width and writes only its
        height, so it is a fixed point.  A rule that reads a size it can itself
        change is the documented editor-scrollbar limit cycle, where update()
        never returns and the GUI and the test suite hang together.
        """
        root, panel = self._panel(700)
        try:
            first = panel.ctrl.winfo_height()
            for _ in range(6):
                root.update_idletasks()
                root.update()
            self.assertEqual(panel.ctrl.winfo_height(), first)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
