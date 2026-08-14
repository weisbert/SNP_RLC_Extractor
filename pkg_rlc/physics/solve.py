"""
pkg_rlc_solve.py  --  The arithmetic: S <-> Y, the Schur reduction, extraction
and fitting.

Split out of `pkg_rlc_core.py` verbatim, and "verbatim" is load-bearing here
in a way it is nowhere else in this repo.  `tests/fixtures/golden_legacy.npz`
pins `parse_touchstone -> s_to_y -> compute_z` BIT FOR BIT, and the `G == 1`
branches of `compute_z_matrix` deliberately keep their historical
floating-point expressions because two mathematically identical expressions
are not bit-identical -- different BLAS calls sum in a different order.  The
same goes for the `np.add.at` in the shorted-port merge and for the
per-frequency contraction in step 5f.  Do not tidy anything in this file; see
CLAUDE.md, "Measurement ports / coupling (Mode 6)".

It reads the declaration from `pkg_rlc_spec` and two file-level helpers
(`DEFAULT_Z0` and `_freq_batch`) from `pkg_rlc_touchstone`.  Nothing imports
it back.

Measurement model
-----------------
A measurement is a pair of multimeter probes: a RED probe on the "+" side and
a BLACK probe on the "-" side.  A measurement port is therefore a named triple
(name, plus_ports, minus_ports); ports on the same side are tied together
(parallel, unsigned) and there are no fractional weights -- only membership.
An empty minus side means the port is referenced to ground.

Any number of measurement ports may be defined.  `compute_z_matrix` returns the
G x G open-circuit impedance matrix Z(f): the diagonal is the self impedance of
each measurement port and the off-diagonal entries are the mutual impedances
(all other measurement ports open, which is the textbook definition of M).
`compute_z` is the single-measurement-port special case kept for backward
compatibility, and its G == 1 code paths are deliberately written with the
historical floating-point expressions so that existing modes stay bit-exact.

`pkg_rlc_core` re-exports every name defined here, so
`from pkg_rlc_core import compute_z, s_to_y` keeps resolving unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence, Union

import numpy as np

from pkg_rlc.physics.touchstone import DEFAULT_Z0, _freq_batch
from pkg_rlc.physics.spec import (
    Ground, LumpedBetween, LumpedToGnd, Open, PortTermination, ShortPair,
    Signal, TerminationSet, Vdd, _normalize_signal, _validate_port_indices,
    resolve_meas_ports,
)

# ============================================================================
# Constants
# ============================================================================

SCHUR_LSTSQ_RCOND = 1e-15

# rcond for the pseudo-inverse of the node admittance matrix in
# compute_z_matrix.  pinv (not inv) is required: a fully floating differential
# structure has a singular Y whose null direction is common mode, and the
# balanced +/- injection is orthogonal to that direction.
PINV_RCOND = 1e-12

# How much of a probe vector may lie in the null space of the node admittance
# before the measurement is declared undefined (no return path for the injected
# current).  pinv is only the right answer for probes that are orthogonal to
# that null space; for any other probe it silently fabricates a finite
# minimum-norm number.  The cutoff is sqrt(PINV_RCOND) because a null component
# `alpha` perturbs Z by ~alpha^2 / (s_min/s_max) relative to the kept part, so
# alpha below sqrt(rcond) is already under the truncation error pinv commits
# anyway -- which keeps a *nearly* floating structure (real ground path, just a
# very weak one) out of the error path.
PROBE_RANGE_TOL = math.sqrt(PINV_RCOND)

# `Y_kk - Y_ko @ X` below this fraction of the magnitude of its own two terms
# is pure cancellation noise: the Schur step has nothing left to say and every
# digit downstream is roundoff amplified to ~1e16 ohms.  ADVISORY ONLY (a
# warning, never a NaN) -- unlike the rank test this is a magnitude heuristic,
# and on the repo's fixtures the healthy minimum is 3.8e-10 against 7e-16 for
# the genuinely degenerate case, so the margin is real but finite.
SCHUR_COLLAPSE_TOL = 1e-12

# Above this, Z_ab and Z_ba disagree enough that the input S-parameters, not
# the maths, are the likely problem.  Shared by the GUI results pane and the
# CLI report so the same file never gets two different verdicts.  A real EM
# solver's S12/S21 mismatch routinely sits at 1e-9..1e-6, so a machine-epsilon
# threshold here would cry wolf on essentially every measured file.
RECIPROCITY_WARN = 1e-3


# ============================================================================
# S <-> Y conversion
# ============================================================================

def s_to_y(s: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """
    Y = y0 * (I - S) @ inv(I + S) per frequency, where y0 = 1/z0.
    Input s shape: (nfreqs, n, n). Returns same shape.

    Frequencies are processed COMPUTE_BATCH at a time through a single stacked
    np.linalg.solve.  If any matrix in a chunk is singular the whole chunk is
    redone one frequency at a time so the pinv fallback stays per-frequency,
    exactly as it was when the loop was written in Python.
    """
    y0 = 1.0 / z0
    n = s.shape[-1]
    I = np.eye(n)
    Y = np.empty_like(s)
    batch = _freq_batch(n)
    for start in range(0, s.shape[0], batch):
        stop = min(start + batch, s.shape[0])
        Sb = s[start:stop]
        A = I + Sb
        B = I - Sb
        # Y = y0 * B @ inv(A); avoid explicit inverse:
        # Y.T = y0 * inv(A.T) @ B.T  =>  A.T @ (Y.T) = y0 * B.T
        # (.swapaxes, not .T -- .T would reverse the frequency axis too.)
        try:
            Y[start:stop] = y0 * np.linalg.solve(
                A.swapaxes(-1, -2), B.swapaxes(-1, -2)).swapaxes(-1, -2)
        except np.linalg.LinAlgError:
            for i in range(stop - start):
                try:
                    Y[start + i] = y0 * np.linalg.solve(A[i].T, B[i].T).T
                except np.linalg.LinAlgError:
                    Y[start + i] = y0 * (B[i] @ np.linalg.pinv(A[i]))
    return Y


def y_to_s(y: np.ndarray, z0: float = DEFAULT_Z0) -> np.ndarray:
    """Inverse of s_to_y; convenient for synthesizing test fixtures."""
    n = y.shape[-1]
    I = np.eye(n)
    S = np.empty_like(y)
    batch = _freq_batch(n)
    for start in range(0, y.shape[0], batch):
        stop = min(start + batch, y.shape[0])
        Yb = y[start:stop]
        # S = (I - z0*Y) @ inv(I + z0*Y)
        A = I + z0 * Yb
        B = I - z0 * Yb
        try:
            S[start:stop] = np.linalg.solve(
                A.swapaxes(-1, -2), B.swapaxes(-1, -2)).swapaxes(-1, -2)
        except np.linalg.LinAlgError:
            for i in range(stop - start):
                try:
                    S[start + i] = np.linalg.solve(A[i].T, B[i].T).T
                except np.linalg.LinAlgError:
                    S[start + i] = B[i] @ np.linalg.pinv(A[i])
    return S


# ============================================================================
# Z computation: unified termination -> Z(f)
# ============================================================================

def _rank_deficient_warning(k: int, freq_hz: float) -> str:
    """Informational: singular node admittance that pinv handles correctly."""
    return (f"Rank-deficient node admittance at freq[{k}]={freq_hz:.4g} Hz "
            "(pinv used; expected for a fully floating structure)")


def _schur_collapse_warning(k: int, freq_hz: float) -> str:
    """The Schur contraction cancelled to roundoff -- the result is noise."""
    return (f"Schur contraction cancelled to roundoff at freq[{k}]="
            f"{freq_hz:.4g} Hz: the reduced admittance is under "
            f"{SCHUR_COLLAPSE_TOL:g} of the terms that produced it, so every "
            "digit below is numerical noise (expect ~1e16 ohm values). The "
            "usual cause is a floating structure probed with no return path "
            "-- give each measurement port a '-' side, or add the ground "
            "ports the structure needs.")


def _open_probe_warning(k: int, freq_hz: float, name: str) -> str:
    """
    The single-node probe read an open circuit (1/y_eff was not finite).

    Deliberately NOT a claim that the file or the spec is wrong: y_eff also
    passes through zero at a genuine parallel anti-resonance, where a huge Z is
    the answer the user came for, and no single-frequency magnitude test can
    tell that apart from a probe with no return path.  So this reports the
    reading and names both readings of it.
    """
    return (f"Measurement port '{name}' reads an open circuit at freq[{k}]="
            f"{freq_hz:.4g} Hz (its net admittance to the reference node is "
            "zero to the last bit, so Z is infinite). That is either a genuine "
            "parallel anti-resonance or a probe with no return path -- if the "
            "structure is floating, give the measurement port a '-' side or "
            "add the ground ports it needs.")


def _no_return_path_warning(k: int, freq_hz: float, names: Sequence[str]) -> str:
    """
    Escalation of the above: pinv is NOT valid for these measurement ports.

    Deliberately worded as a problem, not as reassurance -- the injected
    current has nowhere to return, so the reading is undefined and the affected
    row and column of Z are NaN rather than a fabricated minimum-norm number.
    """
    who = ", ".join(f"'{n}'" for n in names)
    return (f"Measurement port(s) {who} have no return path for the injected "
            f"current at freq[{k}]={freq_hz:.4g} Hz (their probes are not "
            "orthogonal to the null space of the node admittance). Their row "
            "and column of Z are NaN. Add the missing '-' side or a ground "
            "port.")


def compute_z_matrix(Y_full: np.ndarray, freqs: np.ndarray,
                     terminations: TerminationSet
                     ) -> tuple[np.ndarray, list[str], list[str]]:
    """
    Apply terminations to Y(f) and return the open-circuit impedance matrix
    Z(f) of every measurement port defined by the Signal ports.

    Returns (Zmat, port_names, warnings_list) with Zmat of shape
    (nfreqs, G, G) complex, where G is the number of measurement ports:

        Zmat[k, g, g]   self impedance of measurement port g
        Zmat[k, a, b]   mutual impedance (all other measurement ports open)

    The reduction pipeline is:
        lumped_between -> lumped_to_gnd -> short merge (Union-Find)
        -> ground/vdd row+col deletion -> Schur elimination of open ports
        -> contraction onto the probe nodes.

    Contraction: one node per non-empty probe side (sides are disjoint because
    each port carries at most one probe), Y_node[i, j] = sum of the Y_red block
    between side i and side j.  With W[node, g] = +1 on port g's plus node and
    -1 on its minus node, Z = W^T @ pinv(Y_node) @ W.  pinv (not inv) is
    required: a fully floating differential structure has a singular Y_node
    whose null direction is common mode, and the balanced +/- injection is
    orthogonal to that direction, so pinv gives the right answer where inv
    returns garbage.

    G == 1 keeps the historical scalar expressions verbatim so that every
    pre-existing mode stays bit-identical -- see compute_z -- except at
    frequencies where they are provably producing noise (_is_singular_2x2),
    which are diverted to the same SVD path as G >= 2.

    Degenerate networks (see _probe_impedance and _is_singular_2x2):

        * singular Y_node, every probe still in range  -> pinv is exact,
          "Rank-deficient node admittance" is emitted as INFORMATION;
        * singular Y_node, some probe out of range     -> that measurement port
          has no return path.  Its whole row and column of Z is NaN and the
          warning names it.  Other ports keep their exact values;
        * Schur contraction cancelled to roundoff      -> advisory warning only
          (magnitude heuristic; the values are still returned).

    Port indices are validated against n up front: an out-of-range probe would
    otherwise be dropped by the resolver and silently demote a differential
    measurement to a ground-referenced one.

    Performance: every step of the plan that does not depend on frequency (the
    Union-Find merge, the merged terminations, the ground/open/signal index
    sets, the Schur keep/eliminate arrays, the node structure and W, and the
    lumped-element admittances) is built once up front; the frequency axis is
    then swept COMPUTE_BATCH at a time and the O(n_open^3) Schur solve runs as
    one stacked np.linalg.solve.  The two products that follow it -- the Schur
    contraction and the contraction onto the probe nodes -- deliberately stay
    per-frequency: numpy routes a dot-shaped 2-D matmul and a stacked one
    through different BLAS calls, so batching them would change the summation
    order.  They are small.  Everything here is bit-exact by construction --
    see the comments on each step and tests/test_golden_regression.py.
    """
    nfreqs, n, _ = Y_full.shape
    omega = 2.0 * np.pi * freqs
    warnings_out: list[str] = []

    # --- 1. Resolve the measurement ports (probe pairs)
    _validate_port_indices(terminations, n)
    mports = resolve_meas_ports(terminations, n)
    if not mports:
        raise ValueError("No Signal-group-A ports defined; nothing to measure.")
    port_names = [mp.name for mp in mports]
    G = len(mports)

    # Node layout: plus side of port 0, minus side of port 0 (if any),
    # plus side of port 1, ...  side_keys[node] is the (group, sign) that
    # selects the member ports of that node.
    side_keys: list[tuple[str, int]] = []
    node_of: list[tuple[int, int | None]] = []
    for mp in mports:
        plus_node = len(side_keys)
        side_keys.append((mp.name, +1))
        minus_node: int | None = None
        if mp.minus:
            minus_node = len(side_keys)
            side_keys.append((mp.name, -1))
        node_of.append((plus_node, minus_node))
    n_nodes = len(side_keys)
    has_b = (G == 1 and n_nodes == 2)

    W = np.zeros((n_nodes, G), dtype=complex)
    for g, (plus_node, minus_node) in enumerate(node_of):
        W[plus_node, g] = 1.0
        if minus_node is not None:
            W[minus_node, g] = -1.0

    # --- 2. Resolve short pairs via Union-Find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cpl in terminations.couplings:
        if isinstance(cpl, ShortPair):
            union(cpl.port_i, cpl.port_j)

    root_to_members: dict[int, list[int]] = {}
    for i in range(n):
        root_to_members.setdefault(find(i), []).append(i)

    # Map from old port index -> new merged-port index (sorted by smallest member)
    merged_reps = sorted({min(members) for members in root_to_members.values()})
    rep_to_new_idx = {rep: ni for ni, rep in enumerate(merged_reps)}
    old_to_new = {old: rep_to_new_idx[min(root_to_members[find(old)])] for old in range(n)}
    new_n = len(merged_reps)

    # --- 3. Determine post-merge per-port termination
    def merge_terms(members: list[int]) -> PortTermination:
        terms = [terminations.termination_of(p) for p in members]
        sig_groups = {_normalize_signal(t) for t in terms if isinstance(t, Signal)}
        if len(sig_groups) > 1:
            shown = {f"{grp}{'+' if sgn > 0 else '-'}" for grp, sgn in sig_groups}
            raise ValueError(
                f"Ports {members} merged via short, but assigned to "
                f"conflicting signal groups {shown}"
            )
        if sig_groups:
            return Signal(*next(iter(sig_groups)))
        if any(isinstance(t, (Ground, Vdd)) for t in terms):
            return Ground()
        lumped = [t for t in terms if isinstance(t, LumpedToGnd)]
        if lumped:
            funcs = [t.y_func for t in lumped]

            def combined(om: np.ndarray, _funcs=funcs) -> np.ndarray:
                total = np.zeros(np.asarray(om).shape, dtype=complex)
                for f in _funcs:
                    total = total + f(om)
                return total
            return LumpedToGnd(combined)
        return Open()

    new_terms: list[PortTermination] = [Open()] * new_n
    for rep, members in root_to_members.items():
        ni = rep_to_new_idx[min(members)]
        new_terms[ni] = merge_terms(members)

    # Member ports of each probe node, in the merged index space.
    new_sides: list[list[int]] = [
        [i for i, t in enumerate(new_terms)
         if isinstance(t, Signal) and _normalize_signal(t) == key]
        for key in side_keys
    ]
    new_sig_set = {i for side in new_sides for i in side}
    new_gnd = [i for i, t in enumerate(new_terms) if isinstance(t, (Ground, Vdd))]

    # --- 4. Frequency-invariant reduction plan
    #
    # Everything below used to be rebuilt inside the frequency loop.  None of it
    # depends on k, and on a 153-port x 5000-frequency file rebuilding it cost
    # more than the linear algebra did.

    # 4a/4b plan: evaluate each lumped y_func ONCE over the whole omega axis
    # instead of on a one-element array per frequency.  The y_func contract is
    # elementwise, so element k of the full evaluation is bit-identical to the
    # old single-point call (verified for every y_* helper, including the
    # merge_terms "combined" closure).
    lumped_between_plan: list[tuple[int, int, np.ndarray]] = [
        (cpl.port_i, cpl.port_j, np.asarray(cpl.y_func(omega), dtype=complex))
        for cpl in terminations.couplings
        if isinstance(cpl, LumpedBetween)
    ]
    lumped_gnd_plan: list[tuple[int, np.ndarray]] = []
    for port in range(n):
        t = terminations.termination_of(port)
        if isinstance(t, LumpedToGnd):
            lumped_gnd_plan.append(
                (port, np.asarray(t.y_func(omega), dtype=complex)))
    stamps_lumped = bool(lumped_between_plan or lumped_gnd_plan)

    # 4c plan: index arrays for the shorted-port merge.
    #
    # The old code walked all n^2 (i, j) pairs in Python.  Split them instead:
    # a cell of the merged matrix whose row group AND column group are both
    # singletons receives exactly one contribution, so that whole block is a
    # single vectorised gather-add (still "0 + x", so still bit-identical).
    # Only cells fed by a multi-member group need order-preserving accumulation,
    # and those go through np.add.at, which applies duplicate indices
    # sequentially in the order given -- feed it the same row-major order the
    # Python loop used and the floating-point result is unchanged.  A matmul
    # would sum in a different order and must not be used here.
    merge_single_dst = merge_single_src = None
    merge_acc_dst = merge_acc_src = None
    if new_n < n:
        o2n = np.array([old_to_new[i] for i in range(n)], dtype=np.intp)
        port_grp_size = np.bincount(o2n, minlength=new_n)[o2n]
        singles = np.nonzero(port_grp_size == 1)[0]
        multis = np.nonzero(port_grp_size > 1)[0]
        if singles.size:
            merge_single_src = (singles[:, None], singles[None, :])
            merge_single_dst = (o2n[singles][:, None], o2n[singles][None, :])
        acc_i_parts: list[np.ndarray] = []
        acc_j_parts: list[np.ndarray] = []
        for rows, cols in ((singles, multis), (multis, singles), (multis, multis)):
            if rows.size and cols.size:
                acc_i_parts.append(np.repeat(rows, cols.size))
                acc_j_parts.append(np.tile(cols, rows.size))
        if acc_i_parts:
            acc_i = np.concatenate(acc_i_parts)
            acc_j = np.concatenate(acc_j_parts)
            merge_acc_src = (acc_i, acc_j)
            merge_acc_dst = (o2n[acc_i], o2n[acc_j])

    # 4d/4e plan: the ground drop was a relabelling of the merged matrix, so
    # composing it into the Schur index arrays selects exactly the same entries
    # in exactly the same order -- and saves materialising the dropped matrix.
    gnd_set = set(new_gnd)
    oo_g = np.array([i for i in range(new_n)
                     if i not in new_sig_set and i not in gnd_set],
                    dtype=np.intp)
    sig_idx_g = sorted(i for side in new_sides for i in side)
    kk_g = np.array(sig_idx_g, dtype=np.intp)
    if oo_g.size:
        kept_pos = {old: pos for pos, old in enumerate(sig_idx_g)}
        sides_pos = [[kept_pos[i] for i in side] for side in new_sides]
    else:
        sides_pos = [list(side) for side in new_sides]

    # 4f plan: the fancy-index tuples and the all-ones vector are constant.
    node_ix = [[np.ix_(sides_pos[i], sides_pos[j]) for j in range(n_nodes)]
               for i in range(n_nodes)]
    ones_a = np.ones(len(sides_pos[0]), dtype=complex)

    # --- 5. Frequency sweep, a chunk of frequencies at a time
    Zmat = np.empty((nfreqs, G, G), dtype=complex)
    fallback_warnings = 0
    rank_warnings = 0
    probe_warnings = 0
    collapse_warnings = 0
    check_collapse = bool(oo_g.size) and int(kk_g.size) >= 2

    # Size the chunk from the widest array a chunk actually materialises, not
    # from the port count: a 153-port file whose ports are nearly all grounded
    # reduces to a tiny Schur block and should still get a big batch.  The
    # candidates are the stamped copy of Y (only when lumped elements exist),
    # the merged matrix (only when ports are shorted) and the Schur Y_oo block.
    batch = _freq_batch(max(
        int(oo_g.size),
        new_n if new_n < n else 0,
        n if stamps_lumped else 0,
        1,
    ))
    for start in range(0, nfreqs, batch):
        stop = min(start + batch, nfreqs)
        nb = stop - start

        if stamps_lumped:
            Yb = Y_full[start:stop].astype(complex, copy=True)
            # 5a. lumped_between, then 5b. lumped_to_gnd -- same order as
            # before, both on original (pre-merge) port indices.
            for i, j, yv in lumped_between_plan:
                yk = yv[start:stop]
                Yb[:, i, i] += yk
                Yb[:, j, j] += yk
                Yb[:, i, j] -= yk
                Yb[:, j, i] -= yk
            for port, yv in lumped_gnd_plan:
                Yb[:, port, port] += yv[start:stop]
        else:
            # Nothing is stamped in place, so a view is enough (no-op when
            # Y_full is already complex128).
            Yb = np.asarray(Y_full[start:stop], dtype=complex)

        # 5c. Merge shorted ports (sum rows + cols).
        if new_n < n:
            Ym = np.zeros((nb, new_n, new_n), dtype=complex)
            for i in range(nb):
                dst, src = Ym[i], Yb[i]
                if merge_single_dst is not None:
                    dst[merge_single_dst] += src[merge_single_src]
                if merge_acc_dst is not None:
                    np.add.at(dst, merge_acc_dst, src[merge_acc_src])
            Yb = Ym

        # 5d/5e. Ground drop (folded into the indices) + Schur elimination of
        # the remaining "open-like" ports.  The O(n_open^3) solve is batched
        # over the chunk; the cheap `Y_kk - Y_ko @ X` contraction is NOT.
        #
        # Batching that matmul is not bit-exact: when only one port survives the
        # reduction (Mode 1 with a single signal port -- the most common case of
        # all) the per-matrix product is (1, n_open) @ (n_open, 1), and numpy
        # routes a 2-D dot-shaped matmul and a stacked one through different
        # BLAS calls, which sum in different orders.  Measured drift on a
        # 16-port fixture: 2.7e-16 relative -- numerically irrelevant, but the
        # acceptance criterion here is bit-identical, so it stays per-frequency
        # in 5f below.  It costs one small matmul per frequency and the solve,
        # which is where the time actually goes, is still batched.
        #
        # np.ascontiguousarray on the two matmul operands is load-bearing for
        # the same reason: 3-D advanced indexing with a leading slice hands back
        # a Fortran-ish layout, and a strided operand makes BLAS pick a
        # different kernel (and a different summation order) than the
        # C-contiguous 2-D block the old code built.  Y_oo / Y_ok are left alone
        # -- the LAPACK gufunc linearises its inputs, so solve is
        # stride-insensitive, and Y_oo is the one array here big enough for an
        # extra copy to hurt.
        if oo_g.size:
            Y_kk = np.ascontiguousarray(Yb[:, kk_g[:, None], kk_g[None, :]])
            Y_ko = np.ascontiguousarray(Yb[:, kk_g[:, None], oo_g[None, :]])
            Y_ok = Yb[:, oo_g[:, None], kk_g[None, :]]
            Y_oo = Yb[:, oo_g[:, None], oo_g[None, :]]
            try:
                X = np.ascontiguousarray(np.linalg.solve(Y_oo, Y_ok))
            except np.linalg.LinAlgError:
                # One singular matrix poisons the whole stacked solve, so redo
                # the chunk one frequency at a time on C-contiguous blocks --
                # i.e. exactly the arrays the 2-D code path used to build.  The
                # healthy frequencies get the same np.linalg.solve they always
                # did and only the singular ones fall back to lstsq (still
                # capped at 3 warnings).
                X = np.empty((nb,) + Y_ok.shape[1:], dtype=complex)
                for i in range(nb):
                    A_oo = np.ascontiguousarray(Y_oo[i])
                    B_ok = np.ascontiguousarray(Y_ok[i])
                    try:
                        X[i] = np.linalg.solve(A_oo, B_ok)
                        continue
                    except np.linalg.LinAlgError:
                        pass
                    try:
                        X[i], *_ = np.linalg.lstsq(A_oo, B_ok,
                                                   rcond=SCHUR_LSTSQ_RCOND)
                    except np.linalg.LinAlgError:
                        # lstsq is the LAST resort and it can fail too: LAPACK's
                        # SVD does not converge on a non-finite A_oo, and a
                        # non-finite A_oo is ORDINARY here rather than exotic.
                        # A lumped L to ground is y = 1/(jwL), which numpy
                        # evaluates to inf+nanj at w == 0 -- so any spec with a
                        # ground-lead inductance, read off a file that carries a
                        # DC point (every composed sweep keeps 0 Hz), puts a NaN
                        # in Y_oo at exactly the frequency where a DC-isolated
                        # port also makes it exactly singular.  Both conditions
                        # are needed and both are normal, which is why this
                        # aborted a 98-port package run at index 0 while every
                        # other frequency in the sweep was healthy.
                        #
                        # Same rule and same reason as _probe_impedance's guard
                        # on its own SVD: one bad frequency must NaN that
                        # frequency, never the sweep.  complex(nan, nan), not
                        # np.nan -- a real NaN in a complex array leaves
                        # imag == 0 and L = Im(Z)/omega would read as a
                        # perfectly plausible 0 H instead of "undefined".
                        X[i] = complex(float("nan"), float("nan"))
                        if fallback_warnings < 3:
                            k = start + i
                            warnings_out.append(
                                f"Schur reduction is undefined at freq[{k}]="
                                f"{freqs[k]:.4g} Hz (Y_oo is singular AND "
                                f"non-finite -- at 0 Hz an ideal series L to "
                                f"ground is 1/(jwL) = inf). Z is NaN at that "
                                f"frequency only; the rest of the sweep is "
                                f"unaffected."
                            )
                            fallback_warnings += 1
                        continue
                    if fallback_warnings < 3:
                        k = start + i
                        warnings_out.append(
                            f"Schur fallback to lstsq at freq[{k}]="
                            f"{freqs[k]:.4g} Hz (Y_oo singular)"
                        )
                        fallback_warnings += 1
        else:
            Y_kk = Y_ko = X = None

        # 5f. Contract onto the probe nodes, one frequency at a time.
        #
        # The two G == 1 branches below are the historical expressions, kept
        # character-for-character so that every pre-existing mode reproduces
        # bit-for-bit. Do not "unify" them with the general branch, and do not
        # batch them: `ones @ M @ ones` is a gemv+dotu pair whose summation
        # order a stacked gemm would not reproduce.  The matrices here are
        # probe-sized (a handful of rows), so the Python loop is cheap.
        #
        # The degeneracy guards added around them are all one-way: they only
        # divert a frequency away from the historical expression when that
        # expression is provably producing noise.  A non-degenerate network
        # never notices them, which is what keeps the golden reference green.
        for i in range(nb):
            k = start + i
            # Y_ko[i] / X[i] are (ns, n_open) / (n_open, ns) C-contiguous views,
            # i.e. exactly the arrays the 2-D code path used to build.
            if X is None:
                Y_red = Yb[i]
            else:
                prod = Y_ko[i] @ X[i]
                Y_red = Y_kk[i] - prod
                if check_collapse and i == 0 and collapse_warnings < 3:
                    # Did the Schur contraction cancel down to pure roundoff?
                    # That is what a floating structure probed with no return
                    # path looks like: Y_kk and Y_ko @ X are equal to the last
                    # bit, Y_red is 1e-16 of either, and everything downstream
                    # is noise amplified to ~1e16 ohms.  Checked once per chunk
                    # (the condition is topological, so it does not vary with
                    # frequency) and only when at least two ports survive:
                    # with a single kept port Y_red IS the scalar y_eff, which
                    # also passes through zero at a perfectly legitimate
                    # parallel anti-resonance.
                    #
                    # Advisory only -- unlike the rank/range test below, this
                    # is a magnitude heuristic with a few decades of margin on
                    # real data (measured: healthy fixtures bottom out at
                    # 3.8e-10, the degenerate one at 7e-16), so it must not
                    # turn a legitimate near-cancellation into NaN.
                    scale = max(float(np.abs(Y_kk[i]).max()),
                                float(np.abs(prod).max()))
                    if (scale > 0.0
                            and float(np.abs(Y_red).max())
                            <= SCHUR_COLLAPSE_TOL * scale):
                        warnings_out.append(
                            _schur_collapse_warning(k, freqs[k]))
                        collapse_warnings += 1
            if G == 1 and not has_b:
                # A single node: "rank-deficient" here means y_eff == 0, and
                # 1/0 -> inf is already the honest reading (a probe with no
                # return path measures an open circuit).  Deliberately NOT
                # thresholded: y_eff also passes through zero at a genuine
                # parallel anti-resonance, where a huge Z is the answer the
                # user came for, and no magnitude test can tell the two apart
                # at a single frequency.
                y_eff = ones_a @ Y_red[node_ix[0][0]] @ ones_a
                # errstate, not a threshold: the divide is ALLOWED to produce
                # inf/nan here, and the numbers are unchanged.  What changes is
                # where the diagnostic goes.  numpy's own "divide by zero" /
                # "invalid value" warning is written to stderr, which a
                # double-clicked GUI discards -- so the only notice the one
                # branch with no other guard produced was invisible to every
                # GUI user, while the results pane printed the roundoff as a
                # formatted measurement ("3.6e+03 TOhm  -11.5 MH") with no
                # annotation.  warnings_out is the channel the GUI already
                # prints under "Calculate @ ..." and the CLI already reports.
                with np.errstate(divide="ignore", invalid="ignore"):
                    z_eff = 1.0 / y_eff
                Zmat[k, 0, 0] = z_eff
                if not np.isfinite(z_eff) and probe_warnings < 3:
                    warnings_out.append(_open_probe_warning(
                        k, freqs[k], port_names[0]))
                    probe_warnings += 1
            elif G == 1:
                Y2 = np.empty((2, 2), dtype=complex)
                Y2[0, 0] = Y_red[node_ix[0][0]].sum()
                Y2[0, 1] = Y_red[node_ix[0][1]].sum()
                Y2[1, 0] = Y_red[node_ix[1][0]].sum()
                Y2[1, 1] = Y_red[node_ix[1][1]].sum()
                if _is_singular_2x2(Y2, PINV_RCOND):
                    # np.linalg.inv would NOT raise here (see _is_singular_2x2)
                    # and Z2[0,0]+Z2[1,1]-Z2[0,1]-Z2[1,0] would be the
                    # difference of four ~1e16 numbers.  Take the same SVD path
                    # the G >= 2 branch uses instead.
                    Z2v, _deficient, bad = _probe_impedance(Y2, W, PINV_RCOND)
                    Zmat[k, 0, 0] = Z2v[0, 0]
                    if bad and probe_warnings < 3:
                        warnings_out.append(_no_return_path_warning(
                            k, freqs[k], [port_names[g] for g in bad]))
                        probe_warnings += 1
                    elif not bad and rank_warnings < 3:
                        warnings_out.append(
                            _rank_deficient_warning(k, freqs[k]))
                        rank_warnings += 1
                    continue
                try:
                    Z2 = np.linalg.inv(Y2)
                except np.linalg.LinAlgError:
                    Z2 = np.linalg.pinv(Y2)
                Zmat[k, 0, 0] = Z2[0, 0] + Z2[1, 1] - Z2[0, 1] - Z2[1, 0]
            else:
                Y_node = np.empty((n_nodes, n_nodes), dtype=complex)
                for a in range(n_nodes):
                    row_ix = node_ix[a]
                    for b in range(n_nodes):
                        Y_node[a, b] = Y_red[row_ix[b]].sum()
                Zk, deficient, bad = _probe_impedance(Y_node, W, PINV_RCOND)
                Zmat[k] = Zk
                if bad and probe_warnings < 3:
                    warnings_out.append(_no_return_path_warning(
                        k, freqs[k], [port_names[g] for g in bad]))
                    probe_warnings += 1
                elif deficient and not bad and rank_warnings < 3:
                    warnings_out.append(_rank_deficient_warning(k, freqs[k]))
                    rank_warnings += 1

    return Zmat, port_names, warnings_out


def _is_singular_2x2(Y2: np.ndarray, rcond: float) -> bool:
    """
    Cheap O(1) rank check for the 2x2 probe-node matrix, no SVD.

    For a 2x2, s1*s2 == |det| and |Y2|_max <= s1 <= 2*|Y2|_max, so
    |det| <= rcond * |Y2|_max^2 brackets `s2 <= rcond * s1` to within a factor
    of 4 -- plenty, because the gap this has to straddle is enormous: on the
    repo's fixtures a healthy 2x2 lands at |det|/|Y2|_max^2 >= 1e-8 while the
    exactly-floating one sits at <= 3e-15.

    Why this exists: LAPACK's LU gives a mathematically singular
    Y2 = [[y, -y], [-y, y]] a determinant of ~1e-19 rather than exactly 0, so
    np.linalg.inv returns a ~1e16-magnitude garbage matrix instead of raising,
    and the `except LinAlgError -> pinv` guard is dead in exactly the case it
    was written for.  Z = Z2[0,0] + Z2[1,1] - Z2[0,1] - Z2[1,0] is then the
    difference of four ~1e16 numbers.  This test routes those frequencies to
    the SVD path *before* inv gets the chance.
    """
    # .tolist() hands back plain Python complex objects; pure-Python abs/mul on
    # four of them measures 0.27 us against 0.39 us for the same expression on
    # numpy complex128 scalars, which matters because this sits in the
    # per-frequency loop of the most common mode in the tool.
    (a, b), (c, d) = Y2.tolist()
    scale = max(abs(a), abs(b), abs(c), abs(d))
    if not math.isfinite(scale) or scale <= 0.0:
        return True
    return abs(a * d - b * c) <= rcond * scale * scale


def _probe_impedance(Y_node: np.ndarray, W: np.ndarray,
                     rcond: float) -> tuple[np.ndarray, bool, list[int]]:
    """
    Z = W^T @ pinv(Y_node) @ W, plus an explicit "does this probe have a return
    path?" check.  Returns (Z, rank_deficient, bad_probe_columns).

    One SVD does all three jobs (pinv, the rank flag and the null-space test);
    the pinv is assembled with numpy's own expression so the result is
    bit-identical to `np.linalg.pinv(Y_node, rcond=rcond)` -- verified by
    np.array_equal.

    Why the null-space test is not optional
    ---------------------------------------
    pinv is the correct inverse only for probe vectors that lie in
    range(Y_node).  That holds for a balanced +/- probe on a floating structure
    (the case pinv is here for: the null direction is the common mode and the
    balanced injection is orthogonal to it).  It does NOT hold for, say, a
    ground-referenced probe on a structure with no path to the reference node.
    For those, pinv happily returns a finite, plausible-looking minimum-norm
    number -- 1.25 + j1.57 ohm (exactly Z_series/4) for a floating pair probed
    single-ended, or a flat 0 ohm for a floating series element, where the true
    answer is "infinite / undefined".  Such probes get NaN in their whole row
    and column instead, and the caller warns.  Measurement ports whose probes
    *are* in range keep their exact values: Z[b, c] = w_b^T Y^+ w_c only
    involves those two columns.
    """
    G = W.shape[1]
    try:
        u, s, vh = np.linalg.svd(Y_node, full_matrices=False)
    except np.linalg.LinAlgError:
        # A non-finite entry at one frequency (a bad data point in the source
        # file) must NaN that frequency, not abort the whole sweep.
        undefined = complex(float("nan"), float("nan"))
        return np.full((G, G), undefined), True, list(range(G))
    smax = float(s[0]) if s.size else 0.0
    large = s > rcond * smax
    rank = int(np.count_nonzero(large))
    sinv = np.zeros_like(s)
    np.divide(1.0, s, out=sinv, where=large)
    # numpy's own pinv expression, term for term, so the product below is the
    # same arithmetic in the same order as `W.T @ pinv(Y_node, rcond) @ W`.
    Z_node = vh.conj().T @ (sinv[:, None] * u.conj().T)
    Z = W.T @ Z_node @ W

    deficient = rank < Y_node.shape[0]
    bad: list[int] = []
    if deficient:
        # Injection at probe g is solvable only if W[:, g] is in range(Y_node)
        # = span(u[:, :rank]); the reading at probe g is single-valued only if
        # W[:, g] is orthogonal to null(Y_node) = span(vh[rank:].conj()).  For a
        # reciprocal (symmetric) Y_node the two coincide, but check both so a
        # mildly non-reciprocal input cannot slip through.
        norms = np.linalg.norm(W, axis=0)
        out_of_range = np.linalg.norm(u[:, rank:].conj().T @ W, axis=0)
        not_measurable = np.linalg.norm(vh[rank:] @ W, axis=0)
        resid = np.maximum(out_of_range, not_measurable)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(norms > 0.0, resid / norms, 0.0)
        bad = [int(g) for g in np.nonzero(rel > PROBE_RANGE_TOL)[0]]
        if bad:
            # complex(nan, nan), not np.nan: assigning a real NaN to a complex
            # array leaves imag == 0, and L = Im(Z)/omega would then read as a
            # perfectly plausible 0 H instead of "undefined".
            undefined = complex(float("nan"), float("nan"))
            Z = Z.copy()
            Z[bad, :] = undefined
            Z[:, bad] = undefined
    return Z, deficient, bad


def compute_z(Y_full: np.ndarray, freqs: np.ndarray,
              terminations: TerminationSet) -> tuple[np.ndarray, list[str]]:
    """
    Apply terminations to Y(f) and return Z(f) for the measurement defined
    by Signal-group ports.

    - One Signal group ("A"): driving-point.  Z = 1 / (1^T * Y_red * 1)
    - Two Signal groups ("A","B"): port-to-port.  Collapse to 2x2,
      Z = Z11 + Z22 - Z12 - Z21.  ("B" is the minus side of "A".)

    Returns (Z, warnings_list); Z is shape (nfreqs,) complex.

    This is a thin wrapper over compute_z_matrix that returns the self
    impedance of the FIRST measurement port.  Because of the G == 1 branches
    in compute_z_matrix it is bit-identical to the historical implementation
    for every mode that defines a single measurement port -- which is all four
    named modes and every legacy Mode 5 spec.  Use compute_z_matrix directly
    when more than one measurement port is defined.
    """
    Zmat, _names, warnings_out = compute_z_matrix(Y_full, freqs, terminations)
    if len(_names) > 1:
        # Only Mode 5 can get here (the named builders can only ever produce
        # one measurement port), and Mode 5 is exactly the free-text mode where
        # a typo -- 'signal V' for 'signal B' -- silently defines a second
        # measurement port.  Returning port 1's self impedance without saying
        # so is a wrong number with no visible difference.
        others = ", ".join(f"'{nm}'" for nm in _names[1:])
        warnings_out = list(warnings_out) + [
            f"{len(_names)} measurement ports are defined "
            f"({', '.join(_names)}), but this result is the self impedance of "
            f"'{_names[0]}' alone; {others} are ignored here. Use Mode 6 / "
            "compute_z_matrix for the mutual terms, or check the signal group "
            "names for a typo."
        ]
    return Zmat[:, 0, 0], warnings_out


# ============================================================================
# RLC extraction (single frequency)
# ============================================================================

@dataclass
class RLCResult:
    freq_hz: float
    Z: complex
    R_ohm: float
    L_henry: float       # signed: Im(Z)/omega; negative past SRF (capacitive)
    C_farad: float       # signed: -1/(omega*Im(Z)); negative below SRF (inductive)
    Q: float             # signed: Im(Z)/Re(Z); negative when capacitive


def extract_rlc_at_freq(freqs: np.ndarray, Z: np.ndarray, target_freq_hz: float) -> RLCResult:
    """
    Pick the data point closest to target_freq_hz and report R, L, C, Q.

    L, C, Q are signed (Cadence convention). The caller is responsible for
    interpreting the sign — see RLCResult docstring.
    """
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    idx = int(np.argmin(np.abs(freqs - target_freq_hz)))
    f = float(freqs[idx])
    z = complex(Z[idx])
    r = z.real
    im = z.imag
    omega = 2.0 * math.pi * f
    L = im / omega if omega != 0.0 else float("nan")
    C = -1.0 / (omega * im) if (omega != 0.0 and im != 0.0) else float("nan")
    Q = im / r if r != 0.0 else float("nan")
    return RLCResult(freq_hz=f, Z=z, R_ohm=r, L_henry=L, C_farad=C, Q=Q)


# ============================================================================
# Mutual-coupling extraction (single frequency, G x G Z matrix)
# ============================================================================

@dataclass
class PortRLC:
    """Self impedance of one measurement port, decomposed. All values signed."""
    name: str
    Z: complex
    R_ohm: float
    L_henry: float       # Im(Z)/omega; negative past SRF (capacitive)
    C_farad: float       # -1/(omega*Im(Z)); negative below SRF (inductive)
    Q: float             # Im(Z)/Re(Z); negative when capacitive


@dataclass
class PairCoupling:
    """
    Mutual impedance between two measurement ports (all others open).

    M_henry and C_c_farad are SIGNED, exactly like L and C on the diagonal:
    Im(Z_ab) > 0 means inductive coupling (read M), Im(Z_ab) < 0 means
    capacitive coupling (read C_c).  Never take abs() of these.

    M_over_La is the COUPLING RATIO into port a (and M_over_Lb into port b):
    the first-order Norton injection ratio M/L_a, frequency-independent by
    construction, which is the number a spur / pulling budget is written
    against.  It is NOT the exact current-transfer ratio: the current a shorted
    port a draws when port b is driven is I_a/I_b = -Z_ab/Z_aa, and M/L_a
    equals |Z_ab/Z_aa| only where omega*L_a >> R_a (they differ by 1000%+ a
    decade below the R = omega*L corner).  The *_dB fields are 20*log10 of the
    magnitude of the ratio.
    """
    name_a: str
    name_b: str
    Z_ab: complex
    M_henry: float
    C_c_farad: float
    k: float                 # M / sqrt(L_a * L_b); NaN if L_a <= 0 or L_b <= 0
    M_over_La: float
    M_over_Lb: float
    M_over_La_dB: float
    M_over_Lb_dB: float
    notes: list[str] = field(default_factory=list)


@dataclass
class CouplingResult:
    freq_hz: float
    Z_matrix: np.ndarray     # (G, G) complex, at freq_hz
    names: list[str]
    ports: list[PortRLC]
    pairs: list[PairCoupling]
    reciprocity_error: float  # max|Z_ab - Z_ba| / max|Z_ab| off-diagonal


def _ratio_db(ratio: float) -> float:
    """20*log10(|ratio|); NaN when the ratio is zero or non-finite."""
    if not math.isfinite(ratio) or ratio == 0.0:
        return float("nan")
    return 20.0 * math.log10(abs(ratio))


def extract_coupling_at_freq(freqs: np.ndarray, Zmat: np.ndarray,
                             names: Sequence[str],
                             target_freq_hz: float) -> CouplingResult:
    """
    Pick the data point closest to target_freq_hz and report, for the G x G
    Z matrix produced by compute_z_matrix:

        * per measurement port: R, L, C, Q of the self impedance Z[g][g]
        * per unordered pair a<b: M = Im(Z_ab)/omega, C_c = -1/(omega*Im(Z_ab)),
          k = M/sqrt(L_a*L_b), the coupling ratios M/L_a and M/L_b, and their
          dB magnitudes (see PairCoupling: M/L is the Norton injection ratio,
          not the exact current-transfer ratio)
        * reciprocity_error, a numeric-health check on the input data

    Every R/L/C/Q/M/C_c value keeps its physical sign (Cadence convention) and
    is never clipped to NaN; NaN appears only where a quantity is genuinely
    undefined (division by zero, or k with a non-inductive port).
    """
    if len(freqs) == 0:
        raise ValueError("Empty frequency array")
    Zmat = np.asarray(Zmat)
    if Zmat.ndim != 3 or Zmat.shape[1] != Zmat.shape[2]:
        raise ValueError(
            f"Zmat must have shape (nfreqs, G, G), got {Zmat.shape}"
        )
    G = Zmat.shape[1]
    names = list(names)
    if len(names) != G:
        raise ValueError(
            f"names has {len(names)} entries but Zmat has {G} measurement ports"
        )

    idx = int(np.argmin(np.abs(freqs - target_freq_hz)))
    f = float(freqs[idx])
    omega = 2.0 * math.pi * f
    Zk = np.array(Zmat[idx], dtype=complex)

    ports: list[PortRLC] = []
    for g in range(G):
        z = complex(Zk[g, g])
        r = z.real
        im = z.imag
        L = im / omega if omega != 0.0 else float("nan")
        C = -1.0 / (omega * im) if (omega != 0.0 and im != 0.0) else float("nan")
        Q = im / r if r != 0.0 else float("nan")
        ports.append(PortRLC(name=names[g], Z=z, R_ohm=r,
                             L_henry=L, C_farad=C, Q=Q))

    pairs: list[PairCoupling] = []
    for a in range(G):
        for b in range(a + 1, G):
            z_ab = complex(Zk[a, b])
            im = z_ab.imag
            M = im / omega if omega != 0.0 else float("nan")
            C_c = (-1.0 / (omega * im)
                   if (omega != 0.0 and im != 0.0) else float("nan"))
            La = ports[a].L_henry
            Lb = ports[b].L_henry
            notes: list[str] = []

            # A NaN Z means the measurement itself is undefined (no return path
            # for the probe current), NOT that the port is past its SRF.  Say
            # so instead of emitting the past-SRF advice, which would send the
            # user looking for a resonance that is not there.
            undefined = [nm for nm, z in ((names[a], ports[a].Z),
                                          (names[b], ports[b].Z),
                                          (f"{names[a]}<->{names[b]}", z_ab))
                         if not (math.isfinite(z.real) and math.isfinite(z.imag))]
            if undefined:
                notes.append(
                    "Z is undefined for " + ", ".join(undefined)
                    + ": the probe current has no return path -- see the "
                      "warnings, not a resonance")
            if math.isfinite(im) and im < 0.0:
                notes.append("Im(Z_ab) < 0: coupling is capacitive here, "
                             "read C_c instead of M")
            if not undefined and not (math.isfinite(La) and La > 0.0):
                notes.append("L_a <= 0 at this frequency (past SRF): "
                             "k undefined")
            if not undefined and not (math.isfinite(Lb) and Lb > 0.0):
                notes.append("L_b <= 0 at this frequency (past SRF): "
                             "k undefined")

            if (math.isfinite(La) and La > 0.0
                    and math.isfinite(Lb) and Lb > 0.0 and math.isfinite(M)):
                k = M / math.sqrt(La * Lb)
            else:
                k = float("nan")
            if math.isfinite(k) and abs(k) > 1.0:
                notes.append("|k| > 1: check the input S-parameters")

            m_over_la = (M / La if (math.isfinite(M) and math.isfinite(La)
                                    and La != 0.0) else float("nan"))
            m_over_lb = (M / Lb if (math.isfinite(M) and math.isfinite(Lb)
                                    and Lb != 0.0) else float("nan"))

            pairs.append(PairCoupling(
                name_a=names[a], name_b=names[b], Z_ab=z_ab,
                M_henry=M, C_c_farad=C_c, k=k,
                M_over_La=m_over_la, M_over_Lb=m_over_lb,
                M_over_La_dB=_ratio_db(m_over_la),
                M_over_Lb_dB=_ratio_db(m_over_lb),
                notes=notes,
            ))

    if G < 2:
        recip = 0.0
    else:
        # Off-diagonal entries only, and only the finite ones: a measurement
        # port with no return path NaNs its whole row and column, and letting
        # that poison the metric would report `nan` for a matrix whose other
        # pairs are perfectly reciprocal.
        off = ~np.eye(G, dtype=bool)
        mag = np.abs(Zk)
        delta = np.abs(Zk - Zk.T)
        usable = off & np.isfinite(mag) & np.isfinite(delta)
        scale = float(np.max(mag[usable])) if np.any(usable) else 0.0
        if scale > 0.0 and math.isfinite(scale):
            recip = float(np.max(delta[usable])) / scale
        else:
            recip = 0.0

    return CouplingResult(freq_hz=f, Z_matrix=Zk, names=names,
                          ports=ports, pairs=pairs,
                          reciprocity_error=recip)


# ============================================================================
# Broadband fitting
# ============================================================================

@dataclass
class InductorFit:
    L_henry: float
    R_dc_ohm: float
    R_ac_ohm_per_sqrtHz: float    # coefficient on sqrt(f) skin-effect term
    Q_at_center: float
    SRF_hz: float                 # NaN -- pure-inductor model has no SRF
    rmse_ohm: float
    f_min_hz: float
    f_max_hz: float


@dataclass
class CapacitorFit:
    C_farad: float
    R_esr_ohm: float
    L_esl_henry: float
    SRF_hz: float                 # NaN if outside band
    rmse_ohm: float
    f_min_hz: float
    f_max_hz: float


def _select_band(freqs: np.ndarray, Z: np.ndarray, f_min: float, f_max: float):
    mask = (freqs >= f_min) & (freqs <= f_max)
    if mask.sum() < 3:
        raise ValueError(
            f"Band [{f_min:.4g}, {f_max:.4g}] Hz contains "
            f"only {int(mask.sum())} points; need >= 3"
        )
    return freqs[mask], Z[mask]


def _scaled_lstsq(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Linear least-squares with per-column scaling to reduce condition number.
    Necessary when columns have wildly different magnitudes
    (e.g. omega ~ 1e10 vs 1/omega ~ 1e-10 in capacitor fit).
    """
    col_norms = np.linalg.norm(A, axis=0)
    col_norms = np.where(col_norms > 0, col_norms, 1.0)
    A_s = A / col_norms
    sol_s, *_ = np.linalg.lstsq(A_s, b, rcond=None)
    return sol_s / col_norms


def fit_inductor(freqs: np.ndarray, Z: np.ndarray,
                 f_min: float, f_max: float) -> InductorFit:
    """
    Fit Z(f) = R_dc + R_ac*sqrt(f) + j*2*pi*f*L  (linear in [R_dc, R_ac, L]).
    Solved as one real least-squares over stacked Re/Im rows.
    """
    f, z = _select_band(freqs, Z, f_min, f_max)
    omega = 2.0 * np.pi * f
    A_re = np.column_stack([np.ones_like(f), np.sqrt(f), np.zeros_like(f)])
    A_im = np.column_stack([np.zeros_like(f), np.zeros_like(f), omega])
    A = np.vstack([A_re, A_im])
    b = np.concatenate([z.real, z.imag])
    sol = _scaled_lstsq(A, b)
    R_dc, R_ac, L = float(sol[0]), float(sol[1]), float(sol[2])
    z_fit = (R_dc + R_ac * np.sqrt(f)) + 1j * omega * L
    rmse = float(np.sqrt(np.mean(np.abs(z - z_fit) ** 2)))
    f_center = math.sqrt(max(f_min, 1e-30) * max(f_max, 1e-30))
    omega_c = 2.0 * math.pi * f_center
    R_at_c = R_dc + R_ac * math.sqrt(f_center)
    Q = (omega_c * L) / R_at_c if R_at_c > 0 else float("nan")
    return InductorFit(
        L_henry=L, R_dc_ohm=R_dc, R_ac_ohm_per_sqrtHz=R_ac,
        Q_at_center=Q, SRF_hz=float("nan"),
        rmse_ohm=rmse, f_min_hz=float(f.min()), f_max_hz=float(f.max()),
    )


def fit_capacitor(freqs: np.ndarray, Z: np.ndarray,
                  f_min: float, f_max: float) -> CapacitorFit:
    """
    Fit Z(f) = R_esr + j*2*pi*f*L_esl + 1/(j*2*pi*f*C)
            = R_esr + j*(omega*L_esl - 1/(omega*C))
    Linear in [R_esr, L_esl, 1/C].
    """
    f, z = _select_band(freqs, Z, f_min, f_max)
    omega = 2.0 * np.pi * f
    A_re = np.column_stack([np.ones_like(f), np.zeros_like(f), np.zeros_like(f)])
    A_im = np.column_stack([np.zeros_like(f), omega, -1.0 / omega])
    A = np.vstack([A_re, A_im])
    b = np.concatenate([z.real, z.imag])
    sol = _scaled_lstsq(A, b)
    R_esr, L_esl, inv_C = float(sol[0]), float(sol[1]), float(sol[2])
    C = 1.0 / inv_C if inv_C > 0 else float("nan")
    if not math.isnan(C):
        z_fit = R_esr + 1j * (omega * L_esl - 1.0 / (omega * C))
    else:
        z_fit = R_esr + 1j * omega * L_esl
    rmse = float(np.sqrt(np.mean(np.abs(z - z_fit) ** 2)))
    if L_esl > 0 and not math.isnan(C) and C > 0:
        srf = 1.0 / (2.0 * math.pi * math.sqrt(L_esl * C))
        if not (f_min <= srf <= f_max):
            srf = float("nan")
    else:
        srf = float("nan")
    return CapacitorFit(
        C_farad=C, R_esr_ohm=R_esr, L_esl_henry=L_esl,
        SRF_hz=srf, rmse_ohm=rmse,
        f_min_hz=float(f.min()), f_max_hz=float(f.max()),
    )


def fit_auto(freqs: np.ndarray, Z: np.ndarray,
             f_min: float, f_max: float
             ) -> tuple[str, Union[InductorFit, CapacitorFit]]:
    """
    Pick inductor vs capacitor model based on Im(Z) sign distribution; if mixed,
    fit both and return the lower-RMSE one.
    """
    f_band, z_band = _select_band(freqs, Z, f_min, f_max)
    pos_frac = float(np.mean(z_band.imag > 0))
    if pos_frac > 0.85:
        return "inductor", fit_inductor(freqs, Z, f_min, f_max)
    if pos_frac < 0.15:
        return "capacitor", fit_capacitor(freqs, Z, f_min, f_max)
    ind = fit_inductor(freqs, Z, f_min, f_max)
    cap = fit_capacitor(freqs, Z, f_min, f_max)
    if ind.rmse_ohm <= cap.rmse_ohm:
        return "inductor", ind
    return "capacitor", cap


# ============================================================================
# Convenience evaluator for full Z(f) at many frequencies (for fit overlay etc.)
# ============================================================================

def eval_inductor_model(fit: InductorFit, freqs: np.ndarray) -> np.ndarray:
    omega = 2.0 * np.pi * freqs
    return (fit.R_dc_ohm + fit.R_ac_ohm_per_sqrtHz * np.sqrt(freqs)) \
        + 1j * omega * fit.L_henry


def eval_capacitor_model(fit: CapacitorFit, freqs: np.ndarray) -> np.ndarray:
    omega = 2.0 * np.pi * freqs
    Z = np.full(freqs.shape, fit.R_esr_ohm, dtype=complex)
    if fit.L_esl_henry != 0:
        Z = Z + 1j * omega * fit.L_esl_henry
    if not math.isnan(fit.C_farad) and fit.C_farad != 0:
        Z = Z + 1.0 / (1j * omega * fit.C_farad)
    return Z
