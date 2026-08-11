#!/usr/bin/env python
"""
Run the suite in parallel, sharded by TEST CLASS.

Why this exists (all numbers measured on this repo, 20 logical cores):

    python -m unittest discover -s tests          293 s   906 tests
    python tests/run_parallel.py                  108 s   906 tests   (2.7x)
    python tests/run_parallel.py --fast           1.2 s   267 tests

Where the time goes: 87% of the suite is the eight Tk-driven modules, and
HALF of that is `App()` being rebuilt in setUp -- 258 ms a time (against 31 ms
for a bare `tk.Tk()`), 498 times, which is 128 s, i.e. 44% of the whole suite.
The work itself is real (mapped windows, measured geometry); the rebuilding is
the part that parallelises.

MORE WORKERS IS NOT FASTER, measured: 16 -> 115.4 s, 8 -> 112.3 s, 6 -> 108.4 s.
The Tk tests that loop until the layout settles burn wall clock while they wait,
so contention inflates their CPU too: the suite is 293 s of CPU serially and
650 s of CPU at 6-way.  650/6 = 108, i.e. the run is CPU-bound rather than
bound by any one shard, and the 2.7x is the ceiling for this approach.  Going
faster means attacking the App() rebuild itself, which was considered and
rejected: these tests measure real geometry on mapped windows and carry live
autosave / run-counter / _log_forced state, so sharing an App between them is
exactly the silent cross-contamination this codebase is built to refuse.

Sharded by CLASS, not by module, deliberately.  By module the floor is the
slowest module and nothing else matters: test_run_history alone is 86 s of the
293 s, so a 16-worker module-level run measured 151 s -- the whole run was
spent waiting for that one file.  Class-level sharding needs no edit to any
existing test file, so it cannot break a guard, and the floor becomes the
slowest CLASS instead.

Scheduling is longest-first off a timing cache (system temp, never the repo),
which is the standard LPT heuristic for makespan.  The first run has no cache
and falls back to "most test methods first".

    python tests/run_parallel.py                  # everything
    python tests/run_parallel.py --fast           # the no-Tk modules only (0.6 s)
    python tests/run_parallel.py -m core coupling # substring match on module names
    python tests/run_parallel.py -j 8             # worker count

Exit code is 0 only if every shard passed, so it is a drop-in for CI and for
the `discover` line in CLAUDE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

# Modules with no Tk dependency: the guard to run while iterating on the
# numeric core.  Measured together: 522 tests in 2.9 s, against 116 s for the
# full parallel suite.  Anything touching pkg_rlc_gui.py or pkg_rlc_plot.py
# needs more than this -- see --fast in the help text and the note in
# CLAUDE.md.  `test_freq_label` is deliberately NOT here despite being quick:
# it imports tkinter, and the one property this list has is that it does not.
#
# The four attribution modules belong here and were missing: `pkg_rlc_attrib`
# imports numpy and `pkg_rlc_core` and nothing else, and the CLI ones drive
# `pkg_rlc_extractor` through argv.  A change to the attribution engine that
# cannot be caught by --fast is a change whose author will run the full 116 s
# suite instead, or nothing.
FAST_MODULES = (
    "test_golden_regression",
    "test_core",
    "test_coupling",
    "test_connection_rows",
    "test_port_parser",
    "test_content_sniffer",
    "test_reduce_snp",
    "test_attrib_core",
    "test_attrib_vs_engine",
    "test_attrib_degenerate",
    "test_attrib_cli",
)

_RAN_RE = re.compile(r"Ran (\d+) test")


def _cache_path() -> Path:
    """Timing cache, keyed by repo path, kept OUT of the repo."""
    key = hashlib.sha1(str(REPO).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"snp_rlc_testtimes_{key}.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text())
    except Exception:
        return {}


def _save_cache(times: dict) -> None:
    try:
        _cache_path().write_text(json.dumps(times, indent=0, sort_keys=True))
    except Exception:
        pass          # a cache that cannot be written must never fail a run


def discover_shards(patterns: list[str] | None, fast: bool) -> list[tuple[str, int]]:
    """[(dotted.path.To.Class, n_test_methods)], one shard per TestCase class."""
    sys.path.insert(0, str(REPO))
    loader = unittest.defaultTestLoader
    shards: list[tuple[str, int]] = []
    for f in sorted(TESTS.glob("test_*.py")):
        mod = f.stem
        if fast and mod not in FAST_MODULES:
            continue
        if patterns and not any(p in mod for p in patterns):
            continue
        try:
            suite = loader.loadTestsFromName(f"tests.{mod}")
        except Exception as e:                      # a broken module is a shard
            print(f"  !! cannot load tests.{mod}: {e}", file=sys.stderr)
            shards.append((f"tests.{mod}", 1))
            continue
        by_class: dict[str, int] = {}
        for grp in suite:
            for t in grp if hasattr(grp, "__iter__") else [grp]:
                cls = type(t).__name__
                # A module that failed to import shows up as _FailedTest; run it
                # as the whole module so the real error is reported.
                if cls == "_FailedTest":
                    by_class.clear()
                    by_class[""] = 1
                    break
                by_class[cls] = by_class.get(cls, 0) + 1
        for cls, n in by_class.items():
            shards.append((f"tests.{mod}.{cls}" if cls else f"tests.{mod}", n))
    return shards


def run_shard(name: str) -> tuple[str, float, int, bool, str]:
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "unittest", name],
                       capture_output=True, text=True, cwd=str(REPO))
    dt = time.perf_counter() - t0
    err = p.stderr or ""
    m = _RAN_RE.search(err)
    ok = p.returncode == 0 and "\nOK" in err
    return name, dt, int(m.group(1)) if m else 0, ok, err


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # 8, not cpu_count: measured 16 -> 115.4 s, 8 -> 112.3 s, 6 -> 108.4 s on a
    # 20-core box.  See the module docstring -- past ~6 the Tk settle-loops
    # contend and each shard costs more CPU than it saves in wall clock.
    ap.add_argument("-j", "--jobs", type=int,
                    default=min(8, max(1, (os.cpu_count() or 4) - 2)))
    ap.add_argument("-m", "--modules", nargs="*", default=None,
                    help="substring match on module names")
    ap.add_argument("--fast", action="store_true",
                    help=f"only the no-Tk modules ({len(FAST_MODULES)} of them)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    shards = discover_shards(args.modules, args.fast)
    if not shards:
        print("no shards matched", file=sys.stderr)
        return 2

    # Longest-first (LPT): the makespan is dominated by whichever shard starts
    # last, so the big ones have to go first.
    cache = _load_cache()
    shards.sort(key=lambda s: cache.get(s[0], s[1] * 0.5), reverse=True)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(args.jobs) as ex:
        results = list(ex.map(lambda s: run_shard(s[0]), shards))
    wall = time.perf_counter() - t0

    _save_cache({**cache, **{n: round(d, 3) for n, d, _, _, _ in results}})

    total = sum(n for _, _, n, _, _ in results)
    bad = [r for r in results if not r[3]]
    if not args.quiet:
        for name, dt, n, ok, _ in sorted(results, key=lambda r: -r[1])[:5]:
            print(f"  {dt:6.2f}s {n:4d}  {name.split('.', 1)[1]}")
    print(f"\n{total} tests in {wall:.1f}s wall "
          f"({len(shards)} shards, {args.jobs} workers)")

    for name, _, _, _, err in bad:
        print(f"\n{'=' * 70}\nFAILED SHARD {name}\n{'=' * 70}\n{err}")
    if bad:
        print(f"\nFAILED: {len(bad)} of {len(shards)} shards")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
