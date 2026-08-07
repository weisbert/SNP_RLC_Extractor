"""Probe ONE Python interpreter for SNP_RLC_Extractor capability tiers.

Called by deploy/doctor.sh once per candidate interpreter; prints machine
readable KEY=VALUE lines on stdout. Not meant to be run by hand (but harmless
if you do).

Deliberately written in the most conservative syntax available -- no f-strings,
no annotations, no dataclasses -- so that even a Python 2 interpreter can PARSE
it and cleanly report itself as unusable, instead of dying with a SyntaxError
that looks like a corrupt package.

The strong checks here are the real `import`s of the shipped modules, not just
"is numpy installed": importing pkg_rlc_core proves numpy works AND the package
arrived intact AND this interpreter can compile the code.
"""

import os
import sys

MIN_PY = (3, 8)  # from __future__ import annotations + dataclasses, plus margin
MIN_NUMPY = (1, 20)


def emit(key, value):
    sys.stdout.write("%s=%s\n" % (key, value))


def try_import(name):
    """Return (ok, detail). detail is a version string or the error text."""
    try:
        mod = __import__(name)
    except Exception:
        exc = sys.exc_info()[1]
        return False, ("%s: %s" % (exc.__class__.__name__, exc)).replace("\n", " ")
    ver = getattr(mod, "__version__", "")
    return True, ver


def numpy_too_old(ver):
    try:
        parts = ver.split(".")
        got = (int(parts[0]), int(parts[1]))
    except Exception:
        return False  # unparseable -- do not cry wolf
    return got < MIN_NUMPY


def main():
    emit("PY_EXEC", sys.executable or "?")
    emit("PY_VERSION", sys.version.split()[0])
    # The red-zone rule is "no venv" -- flag one if we are somehow inside it, so
    # doctor.sh can warn instead of silently recommending a non-reproducible env.
    base = getattr(sys, "base_prefix", getattr(sys, "real_prefix", sys.prefix))
    emit("PY_VENV", "YES" if base != sys.prefix else "NO")

    if sys.version_info[0] < 3 or sys.version_info[:2] < MIN_PY:
        emit("PY_OK", "NO")
        emit("PY_WHY", "need >= %d.%d" % MIN_PY)
        return 1
    emit("PY_OK", "YES")

    # --- third-party -------------------------------------------------------
    np_ok, np_detail = try_import("numpy")
    emit("MOD_numpy", "OK" if np_ok else "MISSING")
    emit("MOD_numpy_detail", np_detail)
    if np_ok and np_detail and numpy_too_old(np_detail):
        emit("MOD_numpy_warn", "older than %d.%d -- untested" % MIN_NUMPY)

    mpl_ok, mpl_detail = try_import("matplotlib")
    emit("MOD_matplotlib", "OK" if mpl_ok else "MISSING")
    emit("MOD_matplotlib_detail", mpl_detail)

    tk_ok, tk_detail = try_import("tkinter")
    emit("MOD_tkinter", "OK" if tk_ok else "MISSING")
    emit("MOD_tkinter_detail", tk_detail)

    # Can a window actually be opened here? (X11 forwarding / $DISPLAY)
    display = "YES" if tk_ok else "SKIP"
    if tk_ok:
        try:
            import tkinter as _tk

            _root = _tk.Tk()
            _root.destroy()
        except Exception:
            display = "NO"
    emit("TK_DISPLAY", display)
    emit("ENV_DISPLAY", os.environ.get("DISPLAY", ""))

    # --- the shipped modules (the real test) -------------------------------
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    emit("INSTALL_ROOT", root)
    if root not in sys.path:
        sys.path.insert(0, root)

    core_ok, core_detail = try_import("pkg_rlc_core")
    emit("IMP_pkg_rlc_core", "OK" if core_ok else "FAIL")
    emit("IMP_pkg_rlc_core_detail", core_detail)

    red_ok, red_detail = try_import("reduce_snp")
    emit("IMP_reduce_snp", "OK" if red_ok else "FAIL")
    emit("IMP_reduce_snp_detail", red_detail)

    # pkg_rlc_plot pulls in matplotlib's Tk backend; import it only when both
    # halves are present, and never let a headless box make it look broken.
    plot_ok = False
    plot_detail = "skipped (matplotlib or tkinter missing)"
    if mpl_ok and tk_ok:
        plot_ok, plot_detail = try_import("pkg_rlc_plot")
    emit("IMP_pkg_rlc_plot", "OK" if plot_ok else "FAIL")
    emit("IMP_pkg_rlc_plot_detail", plot_detail)

    # --- capability tiers --------------------------------------------------
    emit("CAP_reduce", "YES" if (np_ok and red_ok) else "NO")
    emit("CAP_cli", "YES" if (np_ok and core_ok) else "NO")
    emit("CAP_gui", "YES" if (np_ok and core_ok and plot_ok) else "NO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
