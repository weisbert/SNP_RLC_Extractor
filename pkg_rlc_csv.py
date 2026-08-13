"""
pkg_rlc_csv.py  --  the CSV export blocks.

Split out of pkg_rlc_gui.py, verbatim.  A CSV is a FILE FORMAT, not a
rendering of the results pane, which is why this is a module of its own beside
pkg_rlc_report rather than part of it: the pane is a measured 144-column
budget with an SI prefix per column, and the CSV is every value at full
precision with a header row a spreadsheet can read.

Pure: it writes into a file handle and a `csv.writer` the caller opened, and
knows nothing about Tk or the App.

`pkg_rlc_extractor.py` carries a SECOND, independent `_write_coupling_csv` for
the CLI.  A later phase deletes that one and points the CLI here; the two are
not merged in this commit because the GUI one is called with a `TraceConfig`
by tests/test_report_readability.py, and moving the signature to plain arrays
means editing a test that is not this phase's to touch.
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
    """
    Zmat = tc.Zmat
    names = list(tc.mport_names or [])
    G = int(Zmat.shape[1])
    pairs = [(a, b) for a in range(G) for b in range(a + 1, G)]

    fh.write("# Measurement ports: " + ", ".join(names) + "\n")
    fh.write("# Off-diagonal Z is open-circuit mutual impedance "
             "(every other measurement port open).\n")
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
