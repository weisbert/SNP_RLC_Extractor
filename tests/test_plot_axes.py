"""
tests/test_plot_axes.py -- the y axis: what range it shows, and what unit it
is labelled with.

Two defects, both reported as one ("plotting R, the values are ~15.2 ohms but
the y axis reads milliohms with 1e14, so the curve is a flat line at y=0"),
and both reproduced here through the real `_PlotView` with an Agg canvas.

1. A POINT THE X SCALE CANNOT DRAW STILL OWNED THE Y AXIS.  matplotlib's y
   autoscale ranges over the whole data set; a log x axis cannot draw f <= 0.
   A Touchstone file with a DC row therefore handed the y axis a point that
   never appeared on screen -- and every composed sweep KEEPS 0 Hz.  Measured
   through this panel before the fix, on a flat 15.2 ohm curve carrying one
   large finite value at 0 Hz:

       ylim         (-5e+12, 1.05e+14)
       offset text  '1e14'
       the curve    4.545% up the axis
       the culprit  f = 0 Hz, OUTSIDE xlim

   The colleague's report was "I cannot see any outlier at all, just a flat
   line at y=0", which is the signature that separates this from an ordinary
   pole: the point that set the range is off the left edge.  A true `inf`
   there is harmless -- matplotlib drops non-finite values from the range --
   so it takes a large FINITE value, which is what inverting a near-singular
   Y at DC produces.  `test_an_infinite_value_was_never_the_problem` is that
   control, and without it this whole class could pass against a fix that
   only ever filtered non-finite values.

2. THE AXIS WAS LABELLED WITH A HARD-CODED PREFIX.  `R(mOhm)` values are
   milliohms, so 15.2 ohms was drawn against an axis reading 15200 -- beside
   a cursor readout, on the same subplot, saying "15.2 Ω".

The load-bearing test of the second half is
`test_a_narrow_range_does_not_collapse_to_one_repeated_label`: at any fixed
precision a narrow range around a large value renders every tick alike, which
is what a per-tick `format_si` does (measured: 500.000001 to 500.000003 mOhm
gives five identical "500 mΩ").  Passing that is what makes the SI relabelling
safe, and it is why the fallback exists at all.
"""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import pkg_rlc.widgets.plot as P
    _IMPORT_ERROR = None
except Exception as exc:            # no tkinter / no matplotlib on this box
    _IMPORT_ERROR = exc


class _NullCanvas:
    """_PlotView only calls mpl_connect / draw_idle on the canvas it is
    handed; the REAL Agg canvas is bound to the figure and is what draws."""

    def mpl_connect(self, *a, **k):
        return 0

    def draw_idle(self):
        pass


def _axes_for(plot_type, freqs, Z, *, x_log=True, y_log=False, aux=None,
              fit=None):
    """One subplot of one trace, drawn, with its axes returned."""
    fig = Figure(figsize=(5, 3.5), dpi=110)
    FigureCanvasAgg(fig)
    view = P._PlotView(fig, _NullCanvas(), lambda: [plot_type])
    view.x_log = x_log
    view.y_log = y_log
    view.show_marker = False
    view.marker_freq_hz = 1e9
    tr = P.Trace(label="t", freqs=freqs, Z=Z, aux=aux)
    if fit is not None:
        tr.fit_freqs, tr.fit_Z = fit
    view.set_traces([tr])
    fig.canvas.draw()
    return fig, fig.axes[0]


def _tick_texts(ax):
    return [t.get_text().replace("−", "-")
            for t in ax.get_yticklabels() if t.get_text()]


def _height_fraction(ax, value):
    """Where `value` sits up the axes, 0 at the bottom and 1 at the top."""
    lo, hi = ax.get_ylim()
    return (value - lo) / (hi - lo)


# A flat, entirely ordinary curve: R = 15.2 ohm, the colleague's quantity.
_N = 300
_F_AC = np.logspace(6, 10, _N)
_Z_FLAT = np.full(_N, 15.2 + 3.0j)
# The DC row a real export (and every composed sweep) carries.  The value is
# large and FINITE on purpose -- see the module docstring.
_F_DC = np.concatenate([[0.0], _F_AC])
_Z_DC = np.concatenate([[1e11 + 0j], _Z_FLAT])


@unittest.skipIf(_IMPORT_ERROR is not None,
                 f"plot stack unavailable: {_IMPORT_ERROR}")
class TestAnUndrawablePointDoesNotOwnTheAxis(unittest.TestCase):
    """
    Mutation check: make `_apply_y_axis` skip its `set_ylim` (or make
    `drawable_extent` ignore `x_log`) and the first two tests below go red at
    once.  `test_a_healthy_curve_is_left_to_matplotlib` and
    `test_an_infinite_value_was_never_the_problem` are the other direction --
    they fail if the fix starts trimming curves that were never broken.
    """

    def test_a_dc_row_no_longer_flattens_the_curve(self):
        _f, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC)
        frac = _height_fraction(ax, 15200.0)
        # Before the fix this was 0.045.  The curve must be somewhere a
        # reader would call the middle, not pinned against the bottom edge.
        self.assertGreater(frac, 0.25, f"curve at {frac:.4f} up the axis")
        self.assertLess(frac, 0.75, f"curve at {frac:.4f} up the axis")

    def test_the_exponent_offset_is_gone(self):
        _f, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC)
        self.assertEqual(ax.yaxis.get_offset_text().get_text(), "")

    def test_the_culprit_really_is_off_the_x_axis(self):
        # The PRECONDITION, asserted rather than assumed: if 0 Hz were inside
        # the drawn x range this would be an ordinary visible-outlier case and
        # the class would be testing something else entirely.
        _f, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC)
        xlo, xhi = ax.get_xlim()
        self.assertFalse(xlo <= 0.0 <= xhi)

    def test_a_healthy_curve_is_left_to_matplotlib(self):
        # Nothing undrawable -> the shipped autoscale was already right, and
        # the range must be BYTE-IDENTICAL to what a bare matplotlib plot of
        # the same points gives.  This is the safety property the whole fix
        # rests on: a plot that was fine does not move.
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        ref = Figure(figsize=(5, 3.5), dpi=110)
        FigureCanvasAgg(ref)
        rax = ref.add_subplot(1, 1, 1)
        rax.plot(_F_AC, P.trace_y_values(_F_AC, _Z_FLAT, "R(mOhm)"))
        rax.set_xscale("log")
        ref.canvas.draw()
        self.assertEqual(ax.get_ylim(), rax.get_ylim())

    def test_an_infinite_value_was_never_the_problem(self):
        # matplotlib already drops non-finite values from the data range, so
        # a DC row holding `inf` plots correctly with or without this fix.
        # Without this control, a fix that merely filtered non-finite values
        # would pass every other test in this class.
        z_inf = np.concatenate([[np.inf + 0j], _Z_FLAT])
        _f, ax = _axes_for("R(mOhm)", _F_DC, z_inf)
        frac = _height_fraction(ax, 15200.0)
        self.assertGreater(frac, 0.25)
        self.assertLess(frac, 0.75)

    def test_a_linear_x_axis_can_draw_zero_so_nothing_is_hidden(self):
        # The point is undrawable because of the LOG scale, not because it is
        # a DC row.  On a linear x axis it is on screen and owns the range,
        # which is correct -- it is a visible outlier there.
        _f, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC, x_log=False)
        self.assertGreater(ax.get_ylim()[1], 1e13)

    def test_what_is_not_drawn_is_named_on_the_axes(self):
        # A point the reader cannot see and cannot infer is what caused this
        # bug; removing its influence in silence would be the same defect
        # facing the other way.
        fig, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC)
        notes = [t.get_text() for t in ax.texts if "not shown" in t.get_text()]
        self.assertEqual(len(notes), 1, f"axes texts: {[t.get_text() for t in ax.texts]}")
        self.assertIn("1 pt", notes[0])

    def test_a_healthy_curve_gets_no_note(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        self.assertEqual([t for t in ax.texts if "not shown" in t.get_text()], [])

    def test_a_fit_overlay_is_ranged_over_too(self):
        # The overlay is drawn on this axes, so it must be part of the extent
        # or a fit reaching past the data would be clipped without a word.
        fit_f = np.logspace(6, 10, 50)
        fit_z = np.full(50, 30.0 + 0j)          # 30 ohm, well above the curve
        _f, ax = _axes_for("R(mOhm)", _F_DC, _Z_DC, fit=(fit_f, fit_z))
        self.assertGreaterEqual(ax.get_ylim()[1], 30000.0)


@unittest.skipIf(_IMPORT_ERROR is not None,
                 f"plot stack unavailable: {_IMPORT_ERROR}")
class TestTheAxisCarriesItsOwnUnit(unittest.TestCase):
    """
    Mutation check: put `ax.set_ylabel(plot_type)` back in `_draw_axes` and
    every test here goes red except the two dimensionless ones, which is what
    tells the two halves apart.
    """

    def test_ohms_are_labelled_ohms_not_milliohms(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        self.assertEqual(ax.get_ylabel(), "R (Ω)")

    def test_the_ticks_read_the_value_the_readout_reads(self):
        # The whole complaint in one assertion: the axis and the readout box
        # sit on the same subplot and must not use two notations.  15.2 must
        # be findable on the ticks, not 15200.
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        ticks = _tick_texts(ax)
        self.assertIn("15.2", ticks, f"ticks: {ticks}")
        self.assertEqual(P._readout_value(15200.0, "R(mOhm)"), "15.2 Ω")

    def test_nanohenries_pick_their_own_prefix(self):
        z = 0.6 + 1j * 2 * np.pi * _F_AC * 2e-9
        _f, ax = _axes_for("L(nH)", _F_AC, z)
        self.assertEqual(ax.get_ylabel(), "L (nH)")
        self.assertIn("2", _tick_texts(ax))

    def test_a_narrow_range_does_not_collapse_to_one_repeated_label(self):
        # THE load-bearing one.  A per-tick `format_si` renders this range as
        # five identical "500 mΩ"; a fixed `%g` renders five identical "500".
        # Whatever the axis does here, the labels must stay distinguishable.
        z = (0.500000001 + np.linspace(0.0, 2e-9, _N)) + 3.0j
        _f, ax = _axes_for("R(mOhm)", _F_AC, z)
        ticks = _tick_texts(ax)
        self.assertEqual(len(set(ticks)), len(ticks),
                         f"repeated tick labels: {ticks}")

    def test_the_narrow_range_still_names_its_unit(self):
        # Falling back to matplotlib's ticks must not fall back to a label
        # that claims nothing -- the reader needs the unit to read the offset.
        z = (0.500000001 + np.linspace(0.0, 2e-9, _N)) + 3.0j
        _f, ax = _axes_for("R(mOhm)", _F_AC, z)
        self.assertTrue(ax.get_ylabel().startswith("R ("), ax.get_ylabel())
        self.assertIn("Ω", ax.get_ylabel())

    def test_a_dimensionless_quantity_takes_no_si_prefix(self):
        # format_si renders k = -2.412e-4 as "-241 u", a micro-nothing.
        k = np.full(_N, -2.412e-4)
        _f, ax = _axes_for("k", _F_AC, np.full(_N, 1 + 1j), aux={"k": k})
        self.assertEqual(ax.get_ylabel(), "k")
        for t in _tick_texts(ax):
            self.assertNotRegex(t, r"[fpnumkMGT]$", f"prefixed tick {t!r}")

    def test_Q_is_dimensionless_too(self):
        z = 0.6 + 1j * 2 * np.pi * _F_AC * 2e-9
        _f, ax = _axes_for("Q", _F_AC, z)
        self.assertEqual(ax.get_ylabel(), "Q")

    def test_a_log_axis_prefixes_every_tick_instead(self):
        # Ticks a decade apart cannot share one prefix, and cannot collide
        # either -- so the log axis takes the per-tick form.
        z = 0.6 + 1j * 2 * np.pi * _F_AC * 2e-9
        _f, ax = _axes_for("|Z|(Ohm)", _F_AC, z, y_log=True)
        ticks = _tick_texts(ax)
        self.assertTrue(any(t.endswith("mΩ") for t in ticks), ticks)
        self.assertTrue(any(t.endswith(" Ω") for t in ticks), ticks)

    def test_the_subplot_title_does_not_claim_a_unit_either(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        self.assertEqual(ax.get_title(), "R")

    def test_the_stored_plot_type_keys_did_not_move(self):
        # The names are a session field and are quoted in the editor's hints,
        # the README and the CLI prompt.  Only the DISPLAY changed.
        self.assertEqual(P.PLOT_TYPES,
                         ["R(mOhm)", "L(nH)", "C(pF)", "|Z|(Ohm)",
                          "Re(Z)", "Im(Z)", "Q", "k"])
        for t in P.PLOT_TYPES:
            self.assertIn(t, P.PLOT_TYPE_NAMES)

    def test_Re_and_Im_keep_their_parentheses(self):
        # A strip-the-parenthetical rule would render these "Re" and "Im",
        # which is why the display names are a table.
        self.assertEqual(P.PLOT_TYPE_NAMES["Re(Z)"], "Re(Z)")
        self.assertEqual(P.PLOT_TYPE_NAMES["Im(Z)"], "Im(Z)")


@unittest.skipIf(_IMPORT_ERROR is not None,
                 f"plot stack unavailable: {_IMPORT_ERROR}")
class TestTheFrequencyAxisIsTightAndInUnits(unittest.TestCase):
    """
    Pad Y, never pad X.  Every surveyed tool that plots a swept measurement
    does this -- Qucs 10%/0%, KiCad 3%/0% -- because the sweep endpoints ARE
    the data and blank space past them reads as measurements nobody took.
    matplotlib's 5%/5% is the outlier and is what this panel inherited: a
    1 MHz .. 10 GHz sweep was drawn inside an axis running to 16 GHz.

    Mutation check: drop the `set_xmargin(0.0)` and the first two go red;
    drop the `_apply_x_axis` call and the unit tests go red;
    `test_the_y_axis_still_keeps_its_margin` is the one that fails if the
    margin is removed from BOTH axes instead of just x, which is the easy
    over-correction.
    """

    def test_the_sweep_endpoints_are_the_axis_endpoints(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        lo, hi = ax.get_xlim()
        self.assertAlmostEqual(lo / _F_AC[0], 1.0, places=9)
        self.assertAlmostEqual(hi / _F_AC[-1], 1.0, places=9)

    def test_the_y_axis_still_keeps_its_margin(self):
        # The asymmetry IS the rule.  A curve whose values span a real range
        # must not touch the top and bottom spines.
        z = 0.6 + 1j * 2 * np.pi * _F_AC * 2e-9
        _f, ax = _axes_for("L(nH)", _F_AC, z)
        y = P.trace_y_values(_F_AC, z, "L(nH)")
        lo, hi = ax.get_ylim()
        self.assertLess(lo, float(np.nanmin(y)))
        self.assertGreater(hi, float(np.nanmax(y)))

    def test_a_log_frequency_axis_reads_megahertz(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT)
        lo, hi = ax.get_xlim()
        labs = [t.get_text() for t in ax.get_xticklabels()
                if t.get_text()]
        self.assertIn("1 MHz", labs, labs)
        self.assertIn("1 GHz", labs, labs)
        self.assertEqual(ax.get_xlabel(), "Freq")

    def test_a_linear_frequency_axis_takes_one_prefix_on_the_label(self):
        _f, ax = _axes_for("R(mOhm)", _F_AC, _Z_FLAT, x_log=False)
        self.assertEqual(ax.get_xlabel(), "Freq (GHz)")
        labs = [t.get_text().replace("−", "-")
                for t in ax.get_xticklabels() if t.get_text()]
        for t in labs:                     # bare numbers, no per-tick prefix
            self.assertNotIn("Hz", t)

    def test_the_marker_can_still_pull_the_axis_out_to_itself(self):
        # No slack left on x, so this is worth pinning: a marker parked past
        # the sweep must still be reachable rather than clipped away.
        fig = Figure(figsize=(5, 3.5), dpi=110)
        FigureCanvasAgg(fig)
        view = P._PlotView(fig, _NullCanvas(), lambda: ["R(mOhm)"])
        view.x_log = True
        view.show_marker = True
        view.marker_freq_hz = 5e10                     # 5x past the last point
        view.set_traces([P.Trace(label="t", freqs=_F_AC, Z=_Z_FLAT)])
        fig.canvas.draw()
        self.assertGreaterEqual(fig.axes[0].get_xlim()[1], 5e10 * 0.999)

    def test_both_axes_go_through_one_implementation(self):
        # x and y must not come to disagree about what a prefix means.
        self.assertTrue(hasattr(P._PlotView, "_label_si_axis"))
        self.assertFalse(hasattr(P._PlotView, "_label_y_axis"))


@unittest.skipIf(_IMPORT_ERROR is not None,
                 f"plot stack unavailable: {_IMPORT_ERROR}")
class TestZeroAlignmentNeedsNoCodeOfOurs(unittest.TestCase):
    """
    KiCad shifts its whole tick grid so a tick lands exactly on zero, and it
    needs to: its own step search starts from a floored multiple and picks up
    float offset.  matplotlib's MaxNLocator places ticks at integer multiples
    of a nice step, so zero is already on the grid whenever it is in range --
    MEASURED over the sign-crossing ranges R / L / C / M / k really produce,
    9 of 9 with zero in range, on linear and on symlog.

    So there is deliberately NO zero-alignment code in this panel, and this
    class is what says so out loud.  It pins a property of the locator rather
    than of our own code, which is the point: a later change that swaps the
    locator (a percentile autoscale, say) has to notice that it just took
    this away.
    """

    RANGES = [(-506e-12, 1.01e-9), (-3.2, 12.7), (-1.5e-9, 4.5e-9),
              (-0.04322, 0.1126), (-2.5e5, 9.9e5), (-7.0, 7.0),
              (-1e-12, 3e-9), (-42e-15, 508e-12), (-0.25, 0.75)]

    def _ticks_in_range(self, lo, hi, **scale):
        fig = Figure()
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(1, 1, 1)
        ax.plot([1.0, 2.0], [lo, hi])
        if scale:
            ax.set_yscale("symlog", **scale)
        ax.set_ylim(lo, hi)
        fig.canvas.draw()
        return [t for t in ax.get_yticks() if lo <= t <= hi]

    def test_a_tick_lands_on_zero_on_a_linear_axis(self):
        for lo, hi in self.RANGES:
            with self.subTest(ylim=(lo, hi)):
                ticks = self._ticks_in_range(lo, hi)
                self.assertTrue(any(t == 0.0 for t in ticks),
                                f"no zero tick in {ticks}")

    def test_a_tick_lands_on_zero_on_a_symlog_axis(self):
        for lo, hi in self.RANGES:
            with self.subTest(ylim=(lo, hi)):
                ticks = self._ticks_in_range(lo, hi, linthresh=abs(hi) * 1e-3)
                self.assertTrue(any(t == 0.0 for t in ticks),
                                f"no zero tick in {ticks}")

    def test_a_range_that_excludes_zero_has_nothing_to_align(self):
        # The precondition: the rule is "whenever zero is IN RANGE", and this
        # is the case that must NOT be expected to carry a zero tick.
        ticks = self._ticks_in_range(-0.00025, -0.00022)
        self.assertFalse(any(t == 0.0 for t in ticks))
        self.assertTrue(ticks)


@unittest.skipIf(_IMPORT_ERROR is not None,
                 f"plot stack unavailable: {_IMPORT_ERROR}")
class TestTheHelpersAreHonestOnTheirOwn(unittest.TestCase):
    """Pure, so the rules are checkable without drawing anything."""

    def test_drawable_extent_counts_what_a_log_axis_cannot_draw(self):
        f = np.array([0.0, 1.0, 2.0])
        y = np.array([1e14, 1.0, 2.0])
        lo, hi, hidden = P.drawable_extent([(f, y)], x_log=True)
        self.assertEqual((lo, hi, hidden), (1.0, 2.0, 1))

    def test_drawable_extent_keeps_everything_on_a_linear_axis(self):
        f = np.array([0.0, 1.0, 2.0])
        y = np.array([1e14, 1.0, 2.0])
        lo, hi, hidden = P.drawable_extent([(f, y)], x_log=False)
        self.assertEqual((lo, hi, hidden), (1.0, 1e14, 0))

    def test_drawable_extent_does_not_count_non_finite_as_hidden(self):
        # matplotlib already ignores them, so counting them would raise the
        # note on ordinary curves that are drawn perfectly well.
        f = np.array([1.0, 2.0, 3.0])
        y = np.array([np.nan, 1.0, np.inf])
        lo, hi, hidden = P.drawable_extent([(f, y)], x_log=True)
        self.assertEqual((lo, hi, hidden), (1.0, 1.0, 0))

    def test_drawable_extent_survives_an_empty_series(self):
        self.assertEqual(P.drawable_extent([], x_log=True), (None, None, 0))

    def test_tick_label_sig_finds_the_precision_that_separates(self):
        self.assertEqual(P.tick_label_sig([1.0, 2.0, 3.0], 1.0), 4)

    def test_tick_label_sig_gives_up_rather_than_lying(self):
        # 500.000001 .. 500.000003 cannot be told apart inside the cap; the
        # caller must hand the axis back to matplotlib rather than print
        # three identical labels.
        vals = [500.000001, 500.000002, 500.000003]
        self.assertIsNone(P.tick_label_sig(vals, 1.0))

    def test_si_prefix_is_the_same_rule_format_si_uses(self):
        for v in (1e-13, 3.4e-10, 0.5, 12.0, 4.4e3, 9.9e8):
            exp, pfx = P._si_prefix(v)
            self.assertTrue(format_si_agrees(v, exp, pfx), f"{v}: {exp} {pfx!r}")

    def test_si_prefix_refuses_a_meaningless_magnitude(self):
        self.assertEqual(P._si_prefix(0.0), (0, ""))
        self.assertEqual(P._si_prefix(float("nan")), (0, ""))


def format_si_agrees(value, exp, pfx):
    """`_si_prefix` must pick what `format_si` picks -- one table, one rule."""
    from pkg_rlc.physics.core import format_si
    rendered = format_si(value, "H")
    return rendered.endswith(f" {pfx}H") and math.isclose(
        float(rendered.split()[0]), value / (10 ** exp), rel_tol=5e-3)


if __name__ == "__main__":
    unittest.main()
