"""
tests/test_plot_readout.py -- the cursor readout box.

The bug this pins: one vertical cursor crossing N curves used to produce N
free-floating labels, all anchored at the same x with the same fixed (6, 4) pt
offset and no collision or boundary check.  On a mutual-coupling plot -- where
a 3-coil study is 3 self + 3 mutual curves and the mutual ones all sit near
zero -- that measured 19 overlapping label pairs and 20 of 21 labels crossing
out of their own subplot.

These tests render through the real _PlotView with an Agg canvas (no Tk window)
and measure the drawn text, so they fail on the geometry rather than on the
implementation.  The two structural assertions are:

    * no two texts in a subplot overlap
    * no text leaves the subplot it belongs to

Both were violated by the old code and are what a reader actually notices.
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.text import Text
    import pkg_rlc.widgets.plot as P
    _IMPORT_ERROR = None
except Exception as exc:            # no tkinter / no matplotlib on this box
    _IMPORT_ERROR = exc


OVERLAP_TOL_PX2 = 1.0       # touching bounding boxes are not a collision
EDGE_TOL_PX = 0.5


class _NullCanvas:
    """_PlotView only calls mpl_connect / draw_idle; a real canvas would need
    a Tk window."""

    def mpl_connect(self, *a, **k):
        return 0

    def draw_idle(self):
        pass


def _make_view(traces, types, marker_hz, figsize=(11, 6), dpi=110):
    fig = Figure(figsize=figsize, dpi=dpi)
    # The canvas has to exist BEFORE the first draw: set_draggable registers
    # its callbacks on figure.canvas, and with none there the readout would be
    # built non-draggable and the drag tests would pass on a technicality.
    FigureCanvasAgg(fig)            # binds itself as fig.canvas
    view = P._PlotView(fig, _NullCanvas(), lambda: list(types))
    view.x_log = True
    view.marker_freq_hz = marker_hz
    view.set_traces(traces)
    fig.canvas.draw()
    return fig, view


class _Press:
    """Minimal stand-in for a MouseEvent."""

    def __init__(self, ax, x, y, button=1, dblclick=False):
        self.inaxes, self.x, self.y = ax, x, y
        self.button, self.dblclick = button, dblclick
        self.xdata = self.ydata = None


def _drag_legend_to(view, ax, xy_axes_frac):
    """Simulate what DraggableLegend leaves behind after a drag: the position
    as a 2-tuple in axes fraction.  Driving the real pick/motion machinery
    would test matplotlib, not this module."""
    ax.get_legend()._loc = tuple(xy_axes_frac)


def _axes_texts(ax):
    """Every text the readout puts inside `ax` -- annotations and legend."""
    out = list(ax.texts)
    lg = ax.get_legend()
    if lg is not None:
        out.extend(lg.get_texts())
        if lg.get_title().get_text():
            out.append(lg.get_title())
    return out


def _extent(artist, renderer):
    # Text.get_window_extent, not Annotation's: Annotation unions in its
    # leader arrow, so two crossing leaders would read as a text collision.
    return Text.get_window_extent(artist, renderer=renderer)


def _collisions(fig, view):
    """(overlapping text pairs, texts leaving their axes) over the figure."""
    r = fig.canvas.get_renderer()
    pairs, outside = [], []
    for ax, _t in view._axes_types:
        texts = _axes_texts(ax)
        boxes = [_extent(t, r) for t in texts]
        axb = ax.get_window_extent()
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ov = boxes[i].intersection(boxes[i], boxes[j])
                if ov is not None and ov.width * ov.height > OVERLAP_TOL_PX2:
                    pairs.append((texts[i].get_text(), texts[j].get_text()))
            b = boxes[i]
            if (b.x0 < axb.x0 - EDGE_TOL_PX or b.x1 > axb.x1 + EDGE_TOL_PX
                    or b.y0 < axb.y0 - EDGE_TOL_PX or b.y1 > axb.y1 + EDGE_TOL_PX):
                outside.append(texts[i].get_text())
    return pairs, outside


def _coupling_traces(nfreq=100, ncoil=3, prefix="dco3"):
    """The shape a mode-6 trace expands into: one self curve per measurement
    port, one mutual curve per pair, mutual curves carrying aux['k']."""
    f = np.logspace(8, np.log10(1.1e10), nfreq)
    w = 2 * np.pi * f
    L = [2.0e-9 + 0.2e-9 * i for i in range(ncoil)]
    R = [1.5 + 0.1 * i for i in range(ncoil)]
    M = {}
    for a in range(ncoil):
        for b in range(a + 1, ncoil):
            M[(a, b)] = 0.30e-9 / (1 + a + b)

    traces, n = [], 0
    for i in range(ncoil):
        traces.append(P.Trace(label=f"{prefix} : L{i + 1}", freqs=f,
                              Z=R[i] + 1j * w * L[i],
                              color_idx=n % len(P.COLORS), ls_idx=0))
        n += 1
    for (a, b), m in M.items():
        k = np.full(nfreq, m / np.sqrt(L[a] * L[b]))
        traces.append(P.Trace(label=f"{prefix} : L{a + 1} x L{b + 1}", freqs=f,
                              Z=0.0 + 1j * w * m,
                              color_idx=n % len(P.COLORS), ls_idx=1,
                              aux={"k": k}))
        n += 1
    return f, traces


@unittest.skipIf(_IMPORT_ERROR is not None, f"plot stack unavailable: {_IMPORT_ERROR}")
class TestReadoutGeometry(unittest.TestCase):
    """The structural guarantees: nothing overlaps, nothing escapes."""

    def test_coupling_plot_has_no_overlapping_labels(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["R(mOhm)", "L(nH)", "C(pF)", "k"],
                               f[len(f) // 2])
        pairs, outside = _collisions(fig, view)
        self.assertEqual(pairs, [], f"{len(pairs)} overlapping text pairs")
        self.assertEqual(outside, [], f"{len(outside)} texts left their axes")

    def test_two_cursors_still_do_not_collide(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["R(mOhm)", "L(nH)", "C(pF)", "k"],
                               f[len(f) // 2])
        view._vline_freqs = [f[20]]
        view._refresh_marker()
        fig.canvas.draw()
        pairs, outside = _collisions(fig, view)
        self.assertEqual(pairs, [])
        self.assertEqual(outside, [])

    def test_box_fits_inside_a_four_column_subplot(self):
        """Long names in the narrowest grid: the box must not hang over the
        neighbouring subplot, which is the failure it exists to remove."""
        f, traces = _coupling_traces(ncoil=4, prefix="pkg_revC_run7_long_label")
        fig, view = _make_view(traces, ["R(mOhm)", "L(nH)", "C(pF)", "k"],
                               f[len(f) // 2])
        r = fig.canvas.get_renderer()
        for ax, t in view._axes_types:
            lg = ax.get_legend()
            self.assertIsNotNone(lg, f"no readout on {t}")
            b, axb = lg.get_window_extent(r), ax.get_window_extent()
            self.assertGreaterEqual(b.x0, axb.x0 - EDGE_TOL_PX, t)
            self.assertLessEqual(b.x1, axb.x1 + EDGE_TOL_PX, t)

    def test_row_cap_reports_what_it_dropped(self):
        f, traces = _coupling_traces(ncoil=6)      # 6 self + 15 mutual = 21
        self.assertGreater(len(traces), P.READOUT_MAX_ROWS)
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        rows = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        self.assertLessEqual(len(rows), P.READOUT_MAX_ROWS + 1)
        self.assertTrue(any("more" in r for r in rows),
                        f"silent truncation: {rows}")
        self.assertIn(str(len(traces) - P.READOUT_MAX_ROWS), rows[-1])


@unittest.skipIf(_IMPORT_ERROR is not None, f"plot stack unavailable: {_IMPORT_ERROR}")
class TestReadoutContent(unittest.TestCase):

    def test_frequency_is_stated_once_not_once_per_curve(self):
        """The old labels repeated the marker frequency on every curve: 21
        copies of '5.1G' for one cursor."""
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        lg = view.axes[0].get_legend()
        self.assertIn("GHz", lg.get_title().get_text())
        rows = [t.get_text() for t in lg.get_texts()]
        self.assertEqual([r for r in rows if "GHz" in r], [],
                         "the frequency is repeated in the rows")

    def test_values_use_engineering_units(self):
        # 0.3 on an L(nH) axis is 300 pH, not "0.3 nH"; 1500 on an R(mOhm)
        # axis is 1.5 Ohm, not the "1.5e+03 mOhm" that %.3g produced.
        self.assertEqual(P._readout_value(0.3, "L(nH)"), "300 pH")
        self.assertEqual(P._readout_value(1500.0, "R(mOhm)"), "1.5 Ω")
        self.assertEqual(P._readout_value(-0.487, "C(pF)"), "-487 fF")
        self.assertEqual(P._readout_value(0.143, "k"), "0.143")

    def test_undefined_value_reads_as_a_dash(self):
        """A self curve has no k.  The row has to stay, or every row below it
        shifts and the colour swatches stop lining up with the curves."""
        self.assertEqual(P._readout_value(float("nan"), "k"), "--")
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["k"], f[len(f) // 2])
        rows = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        self.assertEqual(sum(1 for r in rows if r.endswith("--")), 3,
                         f"expected the 3 self curves to read '--': {rows}")

    def test_shared_trace_prefix_is_dropped(self):
        f, traces = _coupling_traces(prefix="float4p")
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        rows = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        self.assertFalse(any("float4p" in r for r in rows), rows)
        self.assertTrue(any(r.startswith("L1×L2") for r in rows), rows)

    def test_long_names_keep_their_tail(self):
        """'osc_primary_coil_00/01' differ only at the end -- clipping the
        tail would make every row read the same."""
        got = P._fit_names(["osc_primary_coil_00", "osc_primary_coil_01"], 8)
        self.assertEqual(len(set(got)), 2, got)
        self.assertTrue(all(len(g) <= 8 for g in got), got)

    def test_multi_cursor_columns_line_up(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        view._vline_freqs = [f[20]]
        view._refresh_marker()
        fig.canvas.draw()
        rows = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        widths = {len(r) for r in rows if "more" not in r}
        self.assertEqual(len(widths), 1,
                         f"header and rows are not the same width: {rows}")


@unittest.skipIf(_IMPORT_ERROR is not None, f"plot stack unavailable: {_IMPORT_ERROR}")
class TestReadoutWiring(unittest.TestCase):

    def test_v_line_adds_a_column_and_delete_removes_it(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        before = [t.get_text() for t in view.axes[0].get_legend().get_texts()]

        class _Evt:
            inaxes = view.axes[0]
            xdata = float(f[20])
        view._add_v_line(_Evt())
        after = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        self.assertEqual(len(after), len(before) + 1, "no header row appeared")
        self.assertGreater(len(after[-1]), len(before[-1]), "no second column")

        view._delete_last()
        back = [t.get_text() for t in view.axes[0].get_legend().get_texts()]
        self.assertEqual(back, before, "Delete left the cursor's column behind")
        self.assertEqual(view._vline_freqs, [])

    def test_readout_off_falls_back_to_the_plain_legend(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        view.show_readout = False
        view.redraw()
        fig.canvas.draw()
        lg = view.axes[0].get_legend()
        self.assertIsNotNone(lg, "turning the readout off removed the legend")
        rows = [t.get_text() for t in lg.get_texts()]
        self.assertTrue(any("dco3" in r for r in rows),
                        f"not the plain legend: {rows}")
        self.assertTrue(view._marker_lines, "the marker line went away too")

    def test_marker_off_keeps_a_legend(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        view.show_marker = False
        view.redraw()
        fig.canvas.draw()
        self.assertIsNotNone(view.axes[0].get_legend())

    def test_readout_position_is_stable_while_dragging(self):
        """loc='best' also weighs the legend's own size, so a value going from
        '2 nH' to '2.05 nH' can flip the box to another corner mid-drag."""
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["R(mOhm)", "L(nH)"], f[len(f) // 2])
        first = dict(view._readout_loc)
        for i in (10, 30, 60, 90):
            view.marker_freq_hz = float(f[i])
            view._refresh_marker()
        fig.canvas.draw()
        self.assertEqual(view._readout_loc, first)

    def test_the_box_is_draggable(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        lg = view.axes[0].get_legend()
        self.assertTrue(lg.get_draggable(),
                        "the readout cannot be moved out of the way")

    def test_a_dragged_box_survives_a_marker_move(self):
        """The box is rebuilt on every cursor move, so without capturing the
        dragged position it would snap back on the next drag of the marker."""
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        _drag_legend_to(view, view.axes[0], (0.31, 0.62))

        view.marker_freq_hz = float(f[70])
        view._refresh_marker()
        fig.canvas.draw()
        self.assertEqual(view._readout_manual.get("L(nH)"), (0.31, 0.62))
        self.assertEqual(view.axes[0].get_legend()._loc, (0.31, 0.62))

    def test_a_dragged_box_survives_recalculate(self):
        """set_traces rebuilds the axes, so a placement keyed by axes id would
        be lost every time the user hits Calculate."""
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        _drag_legend_to(view, view.axes[0], (0.25, 0.44))

        view.set_traces(traces)          # what Calculate does
        fig.canvas.draw()
        self.assertEqual(view.axes[0].get_legend()._loc, (0.25, 0.44))

    def test_double_click_returns_the_box_to_automatic_placement(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        ax = view.axes[0]
        auto = view._readout_loc[id(ax)]
        _drag_legend_to(view, ax, (0.4, 0.4))
        view.marker_freq_hz = float(f[60])
        view._refresh_marker()
        fig.canvas.draw()
        self.assertIn("L(nH)", view._readout_manual)

        b = ax.get_legend().get_window_extent(fig.canvas.get_renderer())
        view._on_press(_Press(ax, (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2,
                              dblclick=True))
        fig.canvas.draw()
        self.assertNotIn("L(nH)", view._readout_manual)
        self.assertEqual(view._readout_loc[id(ax)], auto)

    def test_pressing_on_the_box_does_not_grab_the_marker(self):
        """The box can sit on top of the marker line; one press must not move
        both."""
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        ax = view.axes[0]
        axb = ax.get_window_extent()
        lgb = ax.get_legend().get_window_extent(fig.canvas.get_renderer())
        marker_x, _ = ax.transData.transform(
            (view.marker_freq_hz, ax.get_ylim()[0]))
        # Park the box centred ON the marker line -- placing it anywhere else
        # makes this test pass without the guard, which is how it read green
        # the first time it was written.
        _drag_legend_to(view, ax, ((marker_x - lgb.width / 2 - axb.x0) / axb.width,
                                   0.45))
        view._refresh_marker()
        fig.canvas.draw()

        b = ax.get_legend().get_window_extent(fig.canvas.get_renderer())
        px, py = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
        self.assertLess(abs(px - marker_x), P.MARKER_PIXEL_TOLERANCE,
                        "precondition: the press must land on the marker line")
        view._on_press(_Press(ax, px, py))
        self.assertFalse(view._dragging,
                         "the marker was grabbed through the box")

    def test_pressing_the_marker_line_still_grabs_it(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        ax = view.axes[0]
        mx, _ = ax.transData.transform((view.marker_freq_hz, ax.get_ylim()[0]))
        view._on_press(_Press(ax, mx, ax.get_window_extent().y0 + 5))
        self.assertTrue(view._dragging, "the marker is no longer draggable")

    def test_position_is_rechosen_on_redraw(self):
        f, traces = _coupling_traces()
        fig, view = _make_view(traces, ["L(nH)"], f[len(f) // 2])
        self.assertTrue(view._readout_loc)
        view.redraw()
        # ids are per-axes and redraw() rebuilds them, so a stale cache would
        # keep growing instead of being replaced
        self.assertEqual(len(view._readout_loc), 1)


if __name__ == "__main__":
    unittest.main()
