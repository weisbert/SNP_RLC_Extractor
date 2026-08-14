"""
pkg_rlc_csv.py  --  the CSV export blocks.

Split out of pkg_rlc_gui.py, verbatim.  A CSV is a FILE FORMAT, not a
rendering of the results pane, which is why this is a module of its own beside
pkg_rlc_report rather than part of it: the pane is a measured 144-column
budget with an SI prefix per column, and the CSV is every value at full
precision with a header row a spreadsheet can read.

Pure: it writes into a file handle and a `csv.writer` the caller opened, and
knows nothing about Tk or the App.

`pkg_rlc_extractor.py` used to carry a SECOND, independent
`_write_coupling_csv` for the CLI -- same columns, same order, same formats,
written twice.  It is gone: `write_coupling_table` is the one copy of the
table and each front end writes its own comment block above it.  The GUI's
entry point keeps its `TraceConfig` signature because
tests/test_report_readability.py calls it that way.
"""

from __future__ import annotations

import numpy as np


def _coupling_k_array(Zmat: np.ndarray, freqs: np.ndarray,
                      a: int, b: int) -> np.ndarray:
    """
    Coupling coefficient k(f) = M / sqrt(L_a * L_b) for the pair (a, b).

    Signed and never clipped, exactly like extract_coupling_at_freq: NaN only
    where k is genuinely undefined (a port that is not inductive at that
    frequency).  omega cancels out of the ratio but is kept explicit so the
    formula matches the core one line for line.
    """
    omega = 2.0 * np.pi * np.asarray(freqs, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        La = Zmat[:, a, a].imag / omega
        Lb = Zmat[:, b, b].imag / omega
        M = Zmat[:, a, b].imag / omega
        k = M / np.sqrt(La * Lb)
        k = np.where((La > 0.0) & (Lb > 0.0), k, np.nan)
    return k


def _write_coupling_csv(fh, writer, tc: "TraceConfig", freqs) -> None:
    """
    Mode-6 CSV block: Re/Im of every Z_ij, then M_nH and k for every unordered
    pair, one row per frequency.  Every value keeps its physical sign; nothing
    is clipped to NaN except where it is genuinely undefined.

    `freqs` is the axis the matrix was COMPUTED on, handed in rather than
    fetched from the home file: a composed Zmat lives on the composed axis, and
    the home file's sweep is neither the same length nor the same points.

    The two comment lines are this caller's; the table under them is
    `write_coupling_table`, which the CLI writes under its own header.
    """
    fh.write("# Measurement ports: " + ", ".join(tc.mport_names or []) + "\n")
    fh.write("# Off-diagonal Z is open-circuit mutual impedance "
             "(every other measurement port open).\n")
    write_coupling_table(writer, tc.Zmat, list(tc.mport_names or []), freqs)


def write_coupling_table(writer, Zmat, names, freqs) -> None:
    """
    The coupling table itself: the column header, then one row per frequency.

    Columns are Freq_GHz, Re/Im of every Z_ij in row-major order, then M_nH
    and k for every unordered pair in (i, j<i) order.  Values are `%.6e` and
    keep their physical sign; k is nan wherever it is genuinely undefined
    (L_i <= 0 or L_j <= 0, or omega == 0).

    THIS IS THE ONE COPY.  `pkg_rlc_extractor` carried a second, independent
    implementation of the same table under the same name -- same columns, same
    order, same formats, arrived at twice -- differing only in the comment
    block above it, which is why the split is header-in-the-caller and table
    here.  Two spellings of one file format are two things that can come to
    disagree about a number, which is the failure this repo's references exist
    to refuse.

    `writer` is a `csv.writer` the caller opened; the caller has already
    written whatever comment lines it wants above this.
    """
    G = int(Zmat.shape[1])
    pairs = [(a, b) for a in range(G) for b in range(a + 1, G)]

    header = ["Freq_GHz"]
    for i in range(G):
        for j in range(G):
            header.append(f"Re_Z_{names[i]}_{names[j]}")
            header.append(f"Im_Z_{names[i]}_{names[j]}")
    for a, b in pairs:
        header.append(f"M_nH_{names[a]}_{names[b]}")
        header.append(f"k_{names[a]}_{names[b]}")
    writer.writerow(header)

    omega = 2.0 * np.pi * freqs
    k_arrays = {ab: _coupling_k_array(Zmat, freqs, *ab) for ab in pairs}
    for idx in range(len(freqs)):
        row = [f"{freqs[idx] / 1e9:.6g}"]
        for i in range(G):
            for j in range(G):
                z = Zmat[idx, i, j]
                row.append(f"{z.real:.6e}")
                row.append(f"{z.imag:.6e}")
        for ab in pairs:
            a, b = ab
            M_nH = (Zmat[idx, a, b].imag / omega[idx] * 1e9
                    if omega[idx] != 0.0 else float("nan"))
            row.append(f"{M_nH:.6e}")
            row.append(f"{float(k_arrays[ab][idx]):.6e}")
        writer.writerow(row)
