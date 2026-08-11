#!/usr/bin/env python
"""
Run the suite in parallel, sharded by TEST CLASS.

Why this exists (all numbers measured on this repo, 20 logical cores):

    python -m unittest discover -s tests          293 s   906 tests
    python tests/run_parallel.py                  108 s   906 tests   (2.7x)
    python tests/run_parallel.py --fast           2.9 s   523 tests

Re-measured on this box after the attribution work, at 1566 tests / 289 shards:

    python tests/run_parallel.py                  236 s / 230 s (two runs)
    python tests/run_parallel.py --fast           4.1 s / 4.1 s   642 tests

Re-measured again with the contention control below, at 304 shards:

    python tests/run_parallel.py                  261 s   (ONE run, see below)
    python tests/run_parallel.py --fast           3.6 - 4.5 s   699 tests
                                                  127 shards

Re-measured on the integration pass, with nothing else on the box and an empty
run registry:

    python tests/run_parallel.py                  310 s   1703 tests
                                                  314 shards
    python tests/run_parallel.py --fast           4.4 s     699 tests
                                                  127 shards

Re-measured after the composition work (round 2) -- these are the current
numbers, both single runs on an otherwise idle box:

    python tests/run_parallel.py                  333.7 s  2045 tests
                                                  364 shards
    python tests/run_parallel.py --fast           5.5 s     976 tests
                                                  164 shards

--fast gained 277 tests for 1.1 s of wall: `test_compose`, `test_compose_cli`,
`test_attrib_composed` and `test_conn_nets` all satisfy the one property this
list has and were outside it.  The full number moved 310 -> 334 s for 342 more
tests, i.e. the new work is cheap per test and the slowest shard did not change
(`test_mode5_editor.TestEditorLayout`, 90.7 s, a module round 2 did not touch).

The full number moved from 261 to 310 s and the test count from 1693 to 1703.
Ten of those are the integration pass's own guards, all Tk-driven; --fast is
unchanged in both count and time, which is the expected shape -- nothing new
here is numeric.  The 49 s is not attributed: the slowest shard went from
`TestTheSessionRoundTrip` to `test_mode5_editor.TestEditorLayout` at 92 s,
which is a module this pass did not touch, so at least part of it is box
state.  Treat 310 s as the current upper bound.

--fast GAINED 57 tests (this file's own suite, which is in FAST_MODULES) and
did not get slower for them.  The range is box state rather than anything in
this change: 3.6 / 3.6 / 3.6 s in one session and 4.4 / 4.4 / 4.5 s later on
the same tree with an EMPTY run registry and no other python process on the
box.  Checked and ruled out: the timing cache, which measures 4.5 s in both the
old and the new state.  I could not attribute the remaining 0.9 s.

The full number is 261 s of wall measured by the shell's `time` on the whole
process, and it is an upper bound rather than a correction of the 236 / 230
above.  It was a single run (this repo's test budget allows one); 2 of its 304
shards failed inside another agent's in-flight edits to test_attrib_window.py,
and that agent's own test processes were on the box for part of it.  Those
failures reproduce under a plain serial
`python -m unittest tests.test_attrib_window.TestTheDeclaredMinimumShowsContent`
with no parallelism at all, so they are not this runner's, and the shard count
itself grew 289 -> 304 in the same window.

The full number tracks CONTENTION as much as anything -- 120 s on an idle box
and 339 s with another agent on the same cores have both been measured on the
same tree -- so read the exit code, not the clock.

CONCURRENT RUNS: the runner used to assume it was the only thing on the box.
In a multi-agent workflow it is not -- eight agents each starting `-j 8` on a
20-core box is 64 test processes -- so each run now registers in a heartbeat
directory under the system temp dir (keyed by repo path, NEVER in the repo),
counts how many are live, and takes `cores // live` workers, capped at the
usual 8 and floored at 1.  The chosen number and the reason are printed:

    -j 8 (no other runs detected, 20 cores)
    -j 5 (4 concurrent runs detected, 20 cores)
    -j 6 (explicit, no detection)

Measured, and the measurement is why the rule shares CORES rather than the
8-worker budget.  One workload throughout (`-m run_history freeze_trace
results_notebook session`, 242 tests / 50 shards), N instances launched
simultaneously, wall time = makespan, i.e. when the LAST one finished:

    1 instance                            -j 8       50.1 s
    2 instances                    2 x    -j 8       92.2 s   (16 procs / 20)
    2 instances, budget-shared     8 + 4 workers     93.9 s   <- SLOWER
    4 instances                    4 x    -j 8      270.7 s   (32 procs / 20)
    4 instances, cores-shared      4 x    -j 5      188.4 s   (1.44x)
    4 instances, budget-shared     4 x    -j 2      197.4 s
    4 instances, adaptive (real detection path)     204.0 s   (1.33x)

and a second pair from an identical restored timing cache, taken while ANOTHER
agent's run was live on the box (which the adaptive instances detected as a
fifth run and sized themselves around):

    4 instances                    4 x    -j 8      344.2 s
    4 instances, adaptive          4 x    -j 4      228.7 s   (1.50x)

Two instances is where the mechanism has to keep its hands off: 16 processes on
20 cores is not oversubscription, and throttling there measured 1.7 s SLOWER.
`cores // live` is a no-op at 2 instances on this box and bites from 3 up;
`budget // live` would have cut it to 2 workers each and lost 1.7 s at 2
instances and 9.0 s at 4.  An explicit -j always wins and skips detection
entirely, but still registers -- it is burning cores, so everyone else must see
it.  Every part of this falls back to today's default on any error: a corrupt
or unreadable entry is treated as absent, and a killed run's entry expires
(measured: a real one, left by a killed agent run, was pruned at 153 s old).

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
    python tests/run_parallel.py --fast           # the no-Tk modules (3.6-4.5 s)
    python tests/run_parallel.py -m core coupling # substring match on module names
    python tests/run_parallel.py -j 8             # worker count; wins outright

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
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

# Modules with no Tk dependency: the guard to run while iterating on the
# numeric core.  Measured together: 976 tests in 5.5 s, against 334 s for
# the full parallel suite.  Anything touching pkg_rlc_gui.py or pkg_rlc_plot.py
# needs more than this -- see --fast in the help text and the note in
# CLAUDE.md.  `test_freq_label` is deliberately NOT here despite being quick:
# it imports tkinter, and the one property this list has is that it does not.
#
# The SIX attribution modules belong here and were missing: `pkg_rlc_attrib`
# imports numpy and `pkg_rlc_core` and nothing else, and the CLI ones drive
# `pkg_rlc_extractor` through argv.  A change to the attribution engine that
# cannot be caught by --fast is a change whose author will run the full suite
# instead, or nothing.  The two cold-start modules arrived after that and were
# left out, so `--fast` was silent about the whole cold-start screen -- 119 of
# its tests, and 0.6 s of measured cost.  Verified against the one property
# this list has: neither imports tkinter.
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
    "test_attrib_coldstart",
    "test_attrib_cli_coldstart",
    # The runner's own contention control.  It belongs here by the one property
    # this list has -- it imports no tkinter -- and it costs 57 tests in a
    # measured 0.24 s, spawning no subprocesses.
    "test_run_parallel",
    # The composition work.  All four satisfy the one property: `pkg_rlc_compose`
    # imports `pkg_rlc_core` and nothing else, `test_compose_cli` drives
    # `pkg_rlc_extractor` through argv (and has a test asserting pkg_rlc_gui
    # never entered sys.modules), and `test_conn_nets` is pure net arithmetic.
    # Measured on this box, serially: test_compose 80 tests / 0.159 s,
    # test_compose_cli 71 / 0.577 s, test_attrib_composed 45 / 0.066 s,
    # test_conn_nets 81 / 0.057 s -- 277 tests for 0.86 s of CPU.  --fast went
    # 699 tests / 4.4 s to 976 / 5.5 s.  `test_conn_rowshape` is deliberately
    # NOT here: it drives real widgets and its slowest shard alone is 19 s,
    # more than the whole --fast run.
    "test_compose",
    "test_compose_cli",
    "test_attrib_composed",
    "test_conn_nets",
)

_RAN_RE = re.compile(r"Ran (\d+) test")


def _repo_key() -> str:
    """What keys BOTH temp-dir artefacts: two checkouts never share either."""
    return hashlib.sha1(str(REPO).encode()).hexdigest()[:12]


def _cache_path() -> Path:
    """Timing cache, keyed by repo path, kept OUT of the repo."""
    return Path(tempfile.gettempdir()) / f"snp_rlc_testtimes_{_repo_key()}.json"


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


# --------------------------------------------------------------------------
# Contention: how many of these runs are live right now, and my share.
#
# The runner used to assume it was the only thing on the box.  It is not: in a
# multi-agent workflow several copies start within seconds of each other, and
# 8 workers each on a 20-core box is 64 test processes.  See the measured A/B
# in the module docstring.
#
# The registry is one small JSON file per live run in a directory under the
# system temp dir, keyed by repo path -- the same rule as the timing cache, and
# for the same reason: it must NEVER live in the repo.
#
# BEAT_S / STALE_S: the entry is rewritten every BEAT_S and an entry whose
# recorded beat is older than STALE_S is not counted (and is deleted).  Six
# missed beats before a live run is declared dead, because these beats come off
# a Python thread on a box that is by definition saturated.  Measured during
# the 4-instance contended run below (37 beats, 20 test processes on 20 cores):
# min 5.001 s, mean 5.009 s, max 5.017 s against the 5.0 s nominal -- i.e. the
# jitter is 17 ms and 30 s is 6 nominal beats of margin over it, not a guess.
# A crashed or -9'd instance stops throttling the box after 30 s.
BEAT_S = 5.0
STALE_S = 30.0


def _registry_dir() -> Path:
    """Live-run registry, keyed by repo path, kept OUT of the repo."""
    return Path(tempfile.gettempdir()) / f"snp_rlc_testruns_{_repo_key()}"


def worker_budget(cores: int) -> int:
    """The single-instance default worker count.  Pure.

    8, not cpu_count: measured 16 -> 115.4 s, 8 -> 112.3 s, 6 -> 108.4 s on a
    20-core box (see the docstring).  `cores - 2` leaves a core for the OS on a
    small box, and the floor of 1 is what keeps a 1-core CI box runnable.
    """
    return min(8, max(1, cores - 2))


def share_out(cores: int, instances: int) -> int:
    """This instance's workers when `instances` runs are live.  Pure.

    The CORES are shared, not the 8-worker budget, and that is a measured
    choice, not a tidy one.  On this 20-core box, 4 concurrent instances of the
    same 242-test workload (see the module docstring for the full table):

        -j 8 each   32 processes   270.7 s   today's behaviour
        -j 5 each   20 processes   188.4 s   cores // instances   <- this
        -j 2 each    8 processes   197.4 s   budget // instances

    and at 2 instances -- 16 processes on 20 cores, which is NOT
    oversubscribed -- throttling made it slower (92.2 s at -j 8 each against
    93.9 s throttled).  `cores // instances` is a no-op exactly there and bites
    exactly where the box is genuinely oversubscribed, which `budget //
    instances` does not: it would have taken 2 workers at 2 instances.

    Capped at `worker_budget` because more than 8 is slower even alone
    (16 -> 115.4 s vs 8 -> 112.3 s, the docstring's measurement), and floored
    at 1 because a run with no workers is not a smaller run, it is a crash.
    """
    return max(1, min(worker_budget(cores), cores // max(1, instances)))


def jobs_and_reason(explicit: int | None, cores: int, instances: int) -> tuple[int, str]:
    """(workers, one-line explanation).  Pure -- no files, no processes.

    An explicit -j always wins and skips detection entirely; a silent
    adaptation is indistinguishable from a bug, so the reason is always
    printed, including when nothing was adapted.
    """
    if explicit is not None:
        j = max(1, explicit)
        return j, f"-j {j} (explicit, no detection)"
    j = share_out(cores, instances)
    if instances <= 1:
        return j, f"-j {j} (no other runs detected, {cores} cores)"
    return j, f"-j {j} ({instances} concurrent runs detected, {cores} cores)"


class RunRegistry:
    """One file per live run under `directory`; nothing here may ever raise.

    This gates the whole test suite, so every method swallows its own errors
    and degrades to "I am alone", which is exactly today's behaviour.
    """

    def __init__(self, directory: str | os.PathLike | None = None,
                 pid: int | None = None,
                 stale_s: float = STALE_S, beat_s: float = BEAT_S) -> None:
        self.dir = Path(directory) if directory is not None else _registry_dir()
        self.pid = os.getpid() if pid is None else pid
        self.stale_s = stale_s
        self.beat_s = beat_s
        self.entry: Path | None = None
        self.jobs = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- registration ------------------------------------------------------
    def register(self, now: float | None = None) -> bool:
        """Claim a slot.  Call this BEFORE counting, never after.

        Ordering is load-bearing: N runs launched together each read the
        registry within milliseconds of each other, so counting first means
        every one of them sees zero others and every one of them takes the
        whole budget -- the thundering herd this exists to prevent.  Counting
        after registering means the worst case is that they all see each other.
        """
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.entry = self.dir / f"{self.pid}.json"
            self._write(now)
            return True
        except Exception:
            self.entry = None
            return False

    def _write(self, now: float | None = None) -> None:
        assert self.entry is not None
        now = time.time() if now is None else now
        payload = {"pid": self.pid, "beat": now, "jobs": self.jobs}
        # Atomic: os.replace, not a plain write.  That is what makes "a file I
        # cannot parse is not a live run" true rather than a coin flip on
        # whether I caught someone mid-write.
        tmp = self.entry.with_suffix(f".tmp{self.pid}")
        try:
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self.entry)
        except Exception:
            try:
                tmp.unlink()
            except Exception:
                pass
            raise

    def beat(self, now: float | None = None) -> None:
        if self.entry is None:
            return
        try:
            self._write(now)
        except Exception:
            pass

    def claim(self, now: float | None = None) -> bool:
        """Register + start beating.  Never raises; False means "not visible"."""
        try:
            ok = self.register(now)
            self.start_heartbeat()
            return ok
        except Exception:
            return False

    def start_heartbeat(self) -> None:
        if self.entry is None or self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self.beat_s):
                self.beat()          # already swallows; the wait paces retries

        try:
            self._thread = threading.Thread(target=loop, daemon=True,
                                            name="run-registry-beat")
            self._thread.start()
        except Exception:
            self._thread = None

    def release(self) -> None:
        self._stop.set()
        try:
            if self.entry is not None:
                self.entry.unlink()
        except Exception:
            pass

    # -- counting ----------------------------------------------------------
    def count_live(self, now: float | None = None) -> int:
        """How many runs are live, including this one.  Never raises, never 0.

        An entry that cannot be read or parsed is treated as ABSENT, not as
        live: writes are atomic, so a healthy run never leaves one behind, and
        the safe direction for garbage in a temp dir is to under-throttle (i.e.
        behave exactly as the runner did before this existed) rather than to
        let one corrupt byte pin every future run to -j 1.
        """
        now = time.time() if now is None else now
        try:
            entries = [f for f in self.dir.iterdir() if f.suffix == ".json"]
        except Exception:
            return 1                 # no registry -> assume alone -> default
        live = 0
        for f in entries:
            beat = None
            try:
                rec = json.loads(f.read_text())
                if isinstance(rec, dict):
                    beat = float(rec["beat"])
            except Exception:
                beat = None
            if beat is None or (now - beat) > self.stale_s:
                try:
                    f.unlink()       # stale or garbage: a killed run must not
                except Exception:    # throttle the box forever
                    pass
                continue
            live += 1
        return max(1, live)


def decide_jobs(explicit: int | None, registry: RunRegistry | None = None,
                cores: int | None = None) -> tuple[int, str]:
    """Count and choose.  Falls back to today's default on any error.

    The caller must have `claim()`ed already: claiming early and counting late
    is what makes the count right.  Measured, with the two calls adjacent at
    start-up: two instances launched in the same millisecond still raced, one
    counting 1 (and taking 8 workers) while the other counted 2 (and took 4),
    for a 26 s spread in their finishing times off a 94 s makespan.  Counting
    after discovery gives every instance ~1.5 s of start-up jitter to be seen
    in (discovery is 1.46 / 1.50 s measured), and both then read the same
    number.  Measured after the split, with the SHORTEST window there is
    (--fast, 0.20 s of discovery): four instances launched simultaneously all
    four printed `-j 5 (4 concurrent runs detected, 20 cores)`.
    """
    cores = os.cpu_count() or 4 if cores is None else cores
    instances = 1
    try:
        if registry is not None and explicit is None:
            instances = registry.count_live()
    except Exception:
        instances = 1
    try:
        return jobs_and_reason(explicit, cores, instances)
    except Exception:
        # Unreachable by construction (it is pure arithmetic on three ints),
        # and still caught: this function gates every test in the repo, so a
        # crash here would be worse than any slow run it could ever prevent.
        return max(1, worker_budget(cores)), "-j ? (detection failed)"


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


def _make_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # default=None, NOT the old min(8, cpus-2): the default is now decided at
    # run time from how many other runs are live (`decide_jobs`), and "the user
    # said -j" has to stay distinguishable from "nobody said anything".
    ap.add_argument("-j", "--jobs", type=int, default=None,
                    help="workers; given explicitly it wins and no detection runs")
    ap.add_argument("-m", "--modules", nargs="*", default=None,
                    help="substring match on module names")
    ap.add_argument("--fast", action="store_true",
                    help=f"only the no-Tk modules ({len(FAST_MODULES)} of them)")
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def main() -> int:
    args = _make_arg_parser().parse_args()
    registry = RunRegistry()
    try:
        return _run(args, registry)
    finally:
        registry.release()           # a run that ends must stop throttling the
                                     # box now, not STALE_S from now


def _run(args: argparse.Namespace, registry: RunRegistry) -> int:
    # CLAIM before discovery, COUNT after it, and the gap between them is the
    # point.  Discovery imports every test module -- measured on this box in a
    # fresh process: 1.46 / 1.50 s for the full 298 shards, 0.20 / 0.21 s for
    # --fast's 116 -- so claiming first makes this run visible ~1.5 s before it
    # has to answer "how many of us are there?", and every instance launched
    # inside that window is counted by every other one.  Both orders adjacent
    # at start-up raced; see `decide_jobs`.
    registry.claim()
    shards = discover_shards(args.modules, args.fast)
    jobs, why = decide_jobs(args.jobs, registry)
    print(why)
    registry.jobs = jobs             # informational; the next beat records it
    registry.beat()

    if not shards:
        print("no shards matched", file=sys.stderr)
        return 2

    # Longest-first (LPT): the makespan is dominated by whichever shard starts
    # last, so the big ones have to go first.
    cache = _load_cache()
    shards.sort(key=lambda s: cache.get(s[0], s[1] * 0.5), reverse=True)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(jobs) as ex:
        results = list(ex.map(lambda s: run_shard(s[0]), shards))
    wall = time.perf_counter() - t0

    _save_cache({**cache, **{n: round(d, 3) for n, d, _, _, _ in results}})

    total = sum(n for _, _, n, _, _ in results)
    bad = [r for r in results if not r[3]]
    if not args.quiet:
        for name, dt, n, ok, _ in sorted(results, key=lambda r: -r[1])[:5]:
            print(f"  {dt:6.2f}s {n:4d}  {name.split('.', 1)[1]}")
    print(f"\n{total} tests in {wall:.1f}s wall "
          f"({len(shards)} shards, {jobs} workers)")

    for name, _, _, _, err in bad:
        print(f"\n{'=' * 70}\nFAILED SHARD {name}\n{'=' * 70}\n{err}")
    if bad:
        print(f"\nFAILED: {len(bad)} of {len(shards)} shards")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
