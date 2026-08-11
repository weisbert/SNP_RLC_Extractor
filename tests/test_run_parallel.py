"""The test runner's own contention control.

This is INFRASTRUCTURE, not the product -- it is here because `run_parallel.py`
gates every other test in this repo, so the one thing its adaptation may never
do is fail a run.  Every test below runs in milliseconds and spawns NO
subprocesses: the share-out arithmetic is pure, and the registry is driven
against a temp directory with an injected clock and an injected pid.

The four properties the task asked for, and where they are:

    the pure share-out arithmetic   TestShareOut, TestJobsAndReason
    stale-entry expiry              TestStaleEntries
    an explicit -j overrides        TestExplicitJobsWins
    a corrupt lock file degrades    TestCorruptRegistryDegrades

Everything here was mutation-checked: each guard was confirmed to go red when
the behaviour it names is reverted (notes are on the individual tests where the
mutation is not obvious).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import run_parallel            # noqa: E402
from tests.run_parallel import (          # noqa: E402
    BEAT_S,
    STALE_S,
    RunRegistry,
    _make_arg_parser,
    _registry_dir,
    decide_jobs,
    jobs_and_reason,
    share_out,
    worker_budget,
)


class _TempRegistry(unittest.TestCase):
    """A registry rooted in a scratch dir, with a hand-driven clock."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name) / "reg"
        self.addCleanup(self._td.cleanup)

    def reg(self, pid: int = 1000, **kw) -> RunRegistry:
        return RunRegistry(directory=self.dir, pid=pid, **kw)

    def write_entry(self, pid: int, beat: float | None, garbage: str | None = None):
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / f"{pid}.json"
        f.write_text(garbage if garbage is not None
                     else json.dumps({"pid": pid, "beat": beat, "jobs": 0}))
        return f


# --------------------------------------------------------------------- pure

class TestWorkerBudget(unittest.TestCase):
    def test_the_budget_is_capped_at_eight_however_many_cores(self):
        # 8, not cpu_count: the docstring's measured 16 -> 115.4 s vs
        # 8 -> 112.3 s vs 6 -> 108.4 s.  A budget that tracked cpu_count on a
        # 64-core box would be a 64-worker run.
        self.assertEqual(worker_budget(20), 8)
        self.assertEqual(worker_budget(64), 8)

    def test_a_small_box_keeps_a_core_for_itself(self):
        self.assertEqual(worker_budget(4), 2)
        self.assertEqual(worker_budget(6), 4)

    def test_a_one_core_box_still_gets_one_worker(self):
        # max(1, ...): cores-2 is -1 here, and a 0-worker ThreadPoolExecutor
        # raises ValueError, i.e. the runner would not start at all.
        self.assertEqual(worker_budget(1), 1)
        self.assertEqual(worker_budget(0), 1)


class TestShareOut(unittest.TestCase):
    """The whole point: the TOTAL process count stays near the core count.

    The CORES are shared, not the 8-worker budget.  Measured on this 20-core
    box, 4 concurrent instances of one 242-test workload:

        -j 8 each   32 procs   270.7 s      -j 5 each   20 procs   188.4 s
        -j 2 each    8 procs   197.4 s   (= budget // instances, rejected)

    and at 2 instances (16 procs on 20 cores) throttling made it SLOWER:
    92.2 s at -j 8 each against 93.9 s throttled.
    """

    def test_alone_takes_the_whole_budget(self):
        self.assertEqual(share_out(20, 1), 8)

    def test_two_runs_on_a_20_core_box_are_left_alone(self):
        # 16 processes on 20 cores is not oversubscription, and the measured
        # cost of pretending it is was +1.7 s of makespan.  This is the case
        # `budget // instances` got wrong (it answers 4).
        self.assertEqual(share_out(20, 2), 8)

    def test_the_cores_are_divided_once_they_are_genuinely_short(self):
        self.assertEqual(share_out(20, 3), 6)
        self.assertEqual(share_out(20, 4), 5)      # the measured 188.4 s
        self.assertEqual(share_out(20, 8), 2)      # 8 agents -> 16 procs, not 64

    def test_the_total_never_much_exceeds_the_core_count(self):
        # Stated as a property rather than a table: this is what makes 8 agents
        # on a 20-core box 16 test processes instead of 64.  Past `cores`
        # instances the floor of 1 takes over and the total is the instance
        # count itself.
        for n in range(1, 33):
            self.assertLessEqual(share_out(20, n) * n, max(20, n),
                                 msg=f"{n} instances oversubscribe")

    def test_the_floor_is_one_worker_never_zero(self):
        # 20 // 40 == 0.  A run with 0 workers is not a smaller run, it is a
        # crash (ThreadPoolExecutor(0) raises).
        for n in (21, 40, 100):
            self.assertEqual(share_out(20, n), 1)

    def test_it_never_exceeds_the_single_run_budget(self):
        # A 64-core box alone must still take 8, not 64: measured 16 -> 115.4 s
        # against 8 -> 112.3 s even with nothing else on the box.
        self.assertEqual(share_out(64, 1), 8)
        self.assertEqual(share_out(64, 2), 8)

    def test_a_small_box_shares_what_it_has(self):
        self.assertEqual(share_out(4, 1), 2)
        self.assertEqual(share_out(4, 2), 2)
        self.assertEqual(share_out(4, 4), 1)

    def test_a_nonsense_instance_count_cannot_divide_by_zero(self):
        self.assertEqual(share_out(20, 0), 8)
        self.assertEqual(share_out(20, -3), 8)


class TestJobsAndReason(unittest.TestCase):
    """A silent adaptation is indistinguishable from a bug, so it is printed."""

    def test_alone_says_so_and_names_the_core_count(self):
        j, why = jobs_and_reason(None, 20, 1)
        self.assertEqual(j, 8)
        self.assertEqual(why, "-j 8 (no other runs detected, 20 cores)")

    def test_contended_names_the_count_and_the_cores(self):
        j, why = jobs_and_reason(None, 20, 4)
        self.assertEqual(j, 5)
        self.assertEqual(why, "-j 5 (4 concurrent runs detected, 20 cores)")

    def test_the_reason_always_states_the_chosen_number(self):
        # It is the number that ends up in ThreadPoolExecutor, so the line
        # cannot be allowed to drift from it.
        for explicit, cores, n in ((None, 20, 1), (None, 20, 3), (None, 4, 2),
                                   (6, 20, 1), (1, 2, 9)):
            j, why = jobs_and_reason(explicit, cores, n)
            self.assertIn(f"-j {j} ", why + " ")


class TestExplicitJobsWins(unittest.TestCase):
    def test_explicit_beats_any_amount_of_contention(self):
        for n in (1, 2, 8, 50):
            j, why = jobs_and_reason(12, 20, n)
            self.assertEqual(j, 12)
            self.assertEqual(why, "-j 12 (explicit, no detection)")

    def test_explicit_says_that_no_detection_ran(self):
        _, why = jobs_and_reason(3, 20, 7)
        self.assertNotIn("concurrent", why)

    def test_explicit_zero_still_yields_a_runnable_pool(self):
        self.assertEqual(jobs_and_reason(0, 20, 1)[0], 1)
        self.assertEqual(jobs_and_reason(-4, 20, 1)[0], 1)

    def test_the_parser_default_is_None_so_explicit_is_detectable(self):
        # The mutation this catches is the OLD default (min(8, cpus-2)): with a
        # number there, `explicit is None` is never true and the adaptation is
        # dead code that no other test in this file can see.
        self.assertIsNone(_make_arg_parser().parse_args([]).jobs)
        self.assertEqual(_make_arg_parser().parse_args(["-j", "5"]).jobs, 5)

    def test_an_explicit_run_is_still_visible_to_everyone_else(self):
        # "-j wins" is about MY worker count.  I am still burning cores, so I
        # must still register -- otherwise an adaptive run started next to a
        # `-j 16` run sees an empty registry and takes the full budget too.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name) / "reg"
        loud = RunRegistry(directory=d, pid=4242)
        loud.claim()
        j, why = decide_jobs(16, loud, cores=20)
        self.assertEqual((j, why), (16, "-j 16 (explicit, no detection)"))
        self.assertTrue((d / "4242.json").exists(), "explicit run left no claim")
        # and the harm the claim prevents, stated as the next run's answer:
        quiet = RunRegistry(directory=d, pid=4243)
        quiet.claim()
        others = RunRegistry(directory=d, pid=4244)
        others.claim()
        self.assertEqual(decide_jobs(None, quiet, cores=20)[0], 6)   # 3 live
        loud.release()
        quiet.release()
        others.release()


# ----------------------------------------------------------------- registry

class TestRegistration(_TempRegistry):
    def test_registering_then_counting_sees_myself(self):
        r = self.reg(pid=111)
        self.assertTrue(r.register(now=1000.0))
        self.assertEqual(r.count_live(now=1000.0), 1)

    def test_each_live_run_is_counted_once(self):
        a, b, c = self.reg(pid=1), self.reg(pid=2), self.reg(pid=3)
        for r in (a, b, c):
            r.register(now=1000.0)
        self.assertEqual(a.count_live(now=1000.0), 3)

    def test_registering_before_counting_is_what_prevents_the_herd(self):
        # Three runs launched together, each registering before it counts:
        # every one of them must see all three.  Counting first would give
        # 1/2/3 and the first two would each take the whole budget.
        regs = [self.reg(pid=p) for p in (10, 11, 12)]
        seen = []
        for r in regs:
            r.register(now=1000.0)
            seen.append(r.count_live(now=1000.0))
        self.assertEqual(seen, [1, 2, 3])   # arrival order, all registered
        self.assertEqual([r.count_live(now=1000.0) for r in regs], [3, 3, 3])

    def test_release_frees_the_slot_immediately(self):
        a, b = self.reg(pid=1), self.reg(pid=2)
        a.register(now=1000.0)
        b.register(now=1000.0)
        self.assertEqual(b.count_live(now=1000.0), 2)
        a.release()
        self.assertEqual(b.count_live(now=1000.0), 1)

    def test_release_is_safe_when_registration_never_happened(self):
        r = RunRegistry(directory=self.dir / "nope" / "deeper", pid=7)
        r.release()          # must not raise
        self.assertIsNone(r.entry)

    def test_count_is_never_zero_even_with_an_empty_registry(self):
        # A 0 would divide the budget by zero-ish and, worse, read as "nobody
        # is here" when the caller is plainly here.
        self.dir.mkdir(parents=True)
        self.assertEqual(self.reg().count_live(now=1000.0), 1)

    def test_a_missing_registry_directory_is_just_alone(self):
        self.assertEqual(self.reg().count_live(now=1000.0), 1)

    def test_the_registry_never_lives_in_the_repo(self):
        # Same rule as the timing cache, and the reason it is a rule: a lock
        # file in the tree gets committed, or gets wiped by a clean checkout in
        # the middle of a run.
        repo = Path(__file__).resolve().parent.parent
        d = _registry_dir()
        self.assertNotIn(str(repo).lower(), str(d).lower())
        self.assertEqual(d.parent, Path(tempfile.gettempdir()))

    def test_two_repos_do_not_share_a_registry(self):
        # It is keyed by repo path, so a second checkout on the same box is a
        # different registry -- as it is for the timing cache.
        self.assertIn("snp_rlc_testruns_", _registry_dir().name)


class TestStaleEntries(_TempRegistry):
    def test_an_entry_older_than_STALE_S_is_not_counted(self):
        self.write_entry(pid=1, beat=1000.0)
        self.write_entry(pid=2, beat=1000.0)
        self.write_entry(pid=3, beat=1000.0 + STALE_S)     # kept alive
        n = self.reg(pid=3).count_live(now=1000.0 + STALE_S + 0.001)
        self.assertEqual(n, 1)

    def test_a_killed_run_stops_throttling_the_box(self):
        # The whole point of expiry: -9 an agent and the next run must not be
        # pinned to -j 1 forever.
        for pid in range(1, 8):
            self.write_entry(pid=pid, beat=1000.0)
        alive = self.reg(pid=99)
        alive.register(now=1000.0)
        self.assertEqual(share_out(20, alive.count_live(now=1000.0)), 2)
        self.assertEqual(share_out(20, alive.count_live(now=1e6)), 8)

    def test_a_stale_entry_is_deleted_not_merely_ignored(self):
        f = self.write_entry(pid=1, beat=1000.0)
        self.reg(pid=2).count_live(now=1000.0 + STALE_S + 1)
        self.assertFalse(f.exists())

    def test_an_entry_exactly_at_the_threshold_is_still_live(self):
        self.write_entry(pid=1, beat=1000.0)
        self.assertEqual(self.reg(pid=1).count_live(now=1000.0 + STALE_S), 1)

    def test_a_beat_keeps_an_entry_alive_across_the_threshold(self):
        r = self.reg(pid=1)
        r.register(now=1000.0)
        r.beat(now=1000.0 + STALE_S)
        self.assertEqual(self.reg(pid=2).count_live(now=1000.0 + 2 * STALE_S), 1)

    def test_the_stale_window_is_several_missed_beats_not_one(self):
        # A beat comes off a Python thread on a box that is by definition
        # saturated.  STALE_S == BEAT_S would evict live runs under exactly the
        # load the mechanism exists for.
        self.assertGreaterEqual(STALE_S / BEAT_S, 5.0)


class TestCorruptRegistryDegrades(_TempRegistry):
    def test_a_corrupt_entry_does_not_raise_and_does_not_count(self):
        self.write_entry(pid=1, beat=None, garbage="{not json at all")
        r = self.reg(pid=2)
        self.assertEqual(r.count_live(now=1000.0), 1)
        self.assertEqual(jobs_and_reason(None, 20, r.count_live(now=1000.0))[0], 8)

    def test_every_shape_of_junk_degrades_to_the_default(self):
        # Writes are atomic (os.replace), so none of these can be a healthy run
        # caught mid-write; treating them as absent is the direction that keeps
        # the runner behaving exactly as it did before this existed.
        for i, junk in enumerate(("", "   ", "[]", "null", "3", '{"pid": 1}',
                                  '{"beat": "soon"}', '{"beat": null}',
                                  "\x00\x01\x02")):
            self.write_entry(pid=100 + i, beat=None, garbage=junk)
        self.assertEqual(self.reg(pid=999).count_live(now=1000.0), 1)

    def test_a_corrupt_entry_does_not_hide_a_live_one(self):
        self.write_entry(pid=1, beat=None, garbage="}{")
        self.write_entry(pid=2, beat=1000.0)
        self.assertEqual(self.reg(pid=2).count_live(now=1000.0), 1)

    def test_a_registry_path_that_is_a_FILE_degrades_to_the_default(self):
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        self.dir.write_text("I am not a directory")
        r = self.reg(pid=1)
        self.assertFalse(r.register(now=1000.0))     # mkdir fails, no raise
        self.assertEqual(r.count_live(now=1000.0), 1)
        self.assertEqual(decide_jobs(None, r, cores=20),
                         (8, "-j 8 (no other runs detected, 20 cores)"))

    def test_decide_jobs_survives_a_registry_that_raises_on_everything(self):
        class Exploding(RunRegistry):
            def register(self, now=None):
                raise RuntimeError("boom")

            def count_live(self, now=None):
                raise RuntimeError("boom")

        j, why = decide_jobs(None, Exploding(directory=self.dir, pid=1), cores=20)
        self.assertEqual(j, 8)                       # today's default
        self.assertIn("-j 8", why)

    def test_a_non_json_file_in_the_registry_is_ignored_outright(self):
        # The atomic write stages `<pid>.tmp<pid>`; only *.json is a claim.
        self.dir.mkdir(parents=True)
        (self.dir / "1.tmp1").write_text("half a record")
        (self.dir / "notes.txt").write_text("hello")
        self.assertEqual(self.reg(pid=2).count_live(now=1000.0), 1)


class TestHeartbeatThread(_TempRegistry):
    def test_the_thread_starts_and_stops_and_is_a_daemon(self):
        # Daemon: a run that dies must not be held open by its own heartbeat.
        r = self.reg(pid=1, beat_s=0.01)
        r.register(now=1000.0)
        r.start_heartbeat()
        self.assertIsNotNone(r._thread)
        self.assertTrue(r._thread.daemon)
        r.release()
        r._thread.join(timeout=2.0)
        self.assertFalse(r._thread.is_alive())

    def test_starting_twice_does_not_start_two_threads(self):
        r = self.reg(pid=1, beat_s=0.01)
        r.register(now=1000.0)
        r.start_heartbeat()
        first = r._thread
        r.start_heartbeat()
        self.assertIs(r._thread, first)
        r.release()

    def test_no_heartbeat_without_a_registration(self):
        r = RunRegistry(directory=self.dir / "x" / "y", pid=1)
        r.start_heartbeat()
        self.assertIsNone(r._thread)

    def test_the_entry_is_rewritten_with_the_current_numbers(self):
        r = self.reg(pid=1)
        r.register(now=1000.0)
        r.jobs = 3
        r.beat(now=1234.5)
        rec = json.loads((self.dir / "1.json").read_text())
        self.assertEqual((rec["pid"], rec["beat"], rec["jobs"]), (1, 1234.5, 3))
        self.assertFalse(list(self.dir.glob("*.tmp*")))

    def test_the_write_is_staged_and_REPLACED_never_written_in_place(self):
        # This is what makes "a file I cannot parse is not a live run" a fact
        # rather than a coin flip on whether I caught someone mid-write -- and
        # `count_live` deletes what it cannot parse, so an in-place write is a
        # live run deleting itself.
        r = self.reg(pid=1)
        r.register(now=1000.0)
        seen = []
        real = os.replace

        def recording(src, dst):
            seen.append((Path(src).name, Path(dst).name))
            return real(src, dst)

        with mock.patch.object(run_parallel.os, "replace", recording):
            r.beat(now=1001.0)
        self.assertEqual(seen, [("1.tmp1", "1.json")])

    def test_a_failed_write_leaves_the_previous_entry_intact(self):
        r = self.reg(pid=1)
        r.register(now=1000.0)
        with mock.patch.object(run_parallel.os, "replace",
                               side_effect=OSError("disk full")):
            r.beat(now=2000.0)                      # swallowed, not raised
        rec = json.loads((self.dir / "1.json").read_text())
        self.assertEqual(rec["beat"], 1000.0)       # still the old, still valid
        self.assertFalse(list(self.dir.glob("*.tmp*")))     # staging cleaned up


class TestDecideJobsEndToEnd(_TempRegistry):
    def test_a_crowd_of_runs_takes_its_share(self):
        regs = [self.reg(pid=p) for p in range(1, 9)]
        for r in regs:
            r.claim()
        # Every one of them claimed before any of them counted, so they all
        # read the same number -- which is the whole point of the claim/count
        # split, and is what the first two-instance measurement did NOT do.
        js = [decide_jobs(None, r, cores=20)[0] for r in regs]
        self.assertEqual(js, [2] * 8)          # 16 processes on 20 cores
        for r in regs:
            r.release()

    def test_the_count_follows_runs_arriving_and_leaving(self):
        a, b, c, d = (self.reg(pid=p) for p in (1, 2, 3, 4))
        a.claim()
        self.assertEqual(decide_jobs(None, a, cores=20)[0], 8)
        for r in (b, c, d):
            r.claim()
        self.assertEqual(decide_jobs(None, a, cores=20),
                         (5, "-j 5 (4 concurrent runs detected, 20 cores)"))
        b.release()
        c.release()
        d.release()
        self.assertEqual(decide_jobs(None, a, cores=20)[0], 8)
        a.release()

    def test_claim_starts_the_heartbeat(self):
        # Without it the claim goes stale after STALE_S and a run that is only
        # halfway through a 239 s suite stops throttling anyone -- the exact
        # oversubscription this exists to prevent, arriving 30 s late.
        r = self.reg(pid=1)
        r.claim()
        self.assertIsNotNone(r._thread)
        self.assertTrue(r._thread.is_alive())
        r.release()

    def test_claim_never_raises_even_on_an_impossible_registry(self):
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        self.dir.write_text("not a directory")
        self.assertFalse(self.reg(pid=1).claim())

    def test_no_registry_at_all_is_todays_behaviour(self):
        self.assertEqual(decide_jobs(None, None, cores=20)[0], 8)
        self.assertEqual(decide_jobs(None, None, cores=4)[0], 2)


class TestRunClaimsEarlyAndCountsLate(_TempRegistry):
    """The ordering guard that actually bites, driven through `_run` itself.

    Everything else in this file drives `claim` / `count_live` by hand and so
    stays green with the calls in the wrong order inside the runner.  This
    records the real sequence, with discovery stubbed out so no test process is
    ever spawned.
    """

    def _run_recording(self, jobs=None, during_discovery=None):
        order: list[str] = []

        class Recording(RunRegistry):
            def claim(self, now=None):
                order.append("claim")
                return super().claim(now)

            def count_live(self, now=None):
                order.append("count")
                return super().count_live(now)

        def fake_discover(patterns, fast):
            order.append("discover")
            if during_discovery is not None:
                during_discovery()
            return []                       # -> "no shards matched", rc 2

        reg = Recording(directory=self.dir, pid=os.getpid())
        self.addCleanup(reg.release)
        args = _make_arg_parser().parse_args(["-j", str(jobs)] if jobs else [])
        real = run_parallel.discover_shards
        run_parallel.discover_shards = fake_discover
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = run_parallel._run(args, reg)
        finally:
            run_parallel.discover_shards = real
        return order, out.getvalue(), rc, reg

    def test_the_claim_lands_before_discovery_and_the_count_after_it(self):
        # Measured: with the two adjacent at start-up, two instances launched
        # in the same millisecond still raced -- one counted 1 and took 8
        # workers, the other counted 2 and took 4, a 26 s spread off a 94 s
        # makespan.  Discovery is 1.46 / 1.50 s of window to be seen in.
        order, out, rc, _ = self._run_recording()
        self.assertEqual(order, ["claim", "discover", "count"])
        self.assertEqual(rc, 2)             # the stub matched no shards

    def test_an_explicit_j_still_claims_but_never_counts(self):
        # "-j wins" is about MY worker count; I am still burning cores, so the
        # claim has to be there for everyone else to count.
        order, out, _, reg = self._run_recording(jobs=6)
        self.assertEqual(order, ["claim", "discover"])
        self.assertTrue((self.dir / f"{os.getpid()}.json").exists())
        self.assertEqual(out.strip(), "-j 6 (explicit, no detection)")

    def test_the_reason_is_printed_before_any_shard_runs(self):
        # A silent adaptation is indistinguishable from a bug.
        _, out, _, _ = self._run_recording()
        self.assertRegex(out, r"^-j \d+ \(")

    def test_the_run_counts_the_claims_that_arrived_during_discovery(self):
        # The 1.5 s window is not decoration: an agent launched a second after
        # this one must still be counted by it.
        late = self.reg(pid=999)
        self.addCleanup(late.release)
        _, out, _, reg = self._run_recording(during_discovery=late.claim)
        self.assertEqual(reg.count_live(), 2)
        self.assertIn("2 concurrent runs detected", out)


class TestMainAlwaysReleases(_TempRegistry):
    """A claim that outlives its run throttles the box for STALE_S for nothing."""

    def _main_with(self, run_impl):
        d, events = self.dir, []

        class Reg(RunRegistry):
            def __init__(self, *a, **k):
                super().__init__(directory=d, pid=12345)

            def release(self):
                events.append("release")
                super().release()

        with mock.patch.object(run_parallel, "RunRegistry", Reg), \
                mock.patch.object(run_parallel, "_run", run_impl), \
                mock.patch.object(sys, "argv", ["run_parallel.py"]):
            try:
                rc = run_parallel.main()
            except RuntimeError:
                rc = None
        return events, rc

    def test_a_normal_exit_releases_the_claim(self):
        events, rc = self._main_with(lambda args, reg: (reg.claim(), 0)[1])
        self.assertEqual((events, rc), (["release"], 0))
        self.assertFalse((self.dir / "12345.json").exists())

    def test_a_CRASHING_run_still_releases_the_claim(self):
        # Ctrl-C, an import error, a shard blowing up in a thread: the entry
        # would otherwise sit there claiming a share until it went stale.
        def boom(args, reg):
            reg.claim()
            raise RuntimeError("shard exploded")

        events, _ = self._main_with(boom)
        self.assertEqual(events, ["release"])
        self.assertFalse((self.dir / "12345.json").exists())


if __name__ == "__main__":
    unittest.main()
