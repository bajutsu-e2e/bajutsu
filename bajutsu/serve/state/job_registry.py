"""The control-plane job registry: the in-flight jobs and their monotonic ids (BE-0198)."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from bajutsu.serve.logbus import LogBus

from .job import Job


@dataclass
class JobRegistry:
    """The control-plane job registry (BE-0198): the in-flight ``jobs`` dict, the monotonic id
    sequence, and the concurrency-cap enforcement, carved out of `ServeState` so the atomic
    "count-then-insert under one lock" invariant is expressed by this type's boundary rather than by
    prose on a docstring of the shared state. The registry is the sole owner of the id counter and of
    its own lock; ``logbus`` — the live-log channel wired onto each registered job (BE-0015) — is its
    only external dependency. The concurrency caps are configuration, not registry state, so
    `try_register` receives them per call rather than holding them."""

    logbus: LogBus
    # Which of the given job ids finished off this process, asked before every count so a cap check
    # can never read a stale one (see `_release_finished`). None where nothing runs off-process.
    finished_ids: Callable[[list[str]], set[str]] | None = None
    jobs: dict[str, Job] = field(default_factory=dict)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def seed_seq(self, floor: int) -> None:
        """Fast-forward the id counter past *floor* (a persisted store's highest existing id).

        A restart otherwise reissues ids from 1 while a DB-backed ``jobs`` table keeps every id the
        previous process handed out, so the first dispatch after restart collides on a primary key
        already taken.

        Fixes a single control-plane process restarting, not two replicas racing this seed
        concurrently — both would read the same floor and hand out the same next id. A DB-side
        sequence/identity column or UUID ids would remove that case too.
        """
        with self._lock:
            self._seq = max(self._seq, floor)

    def active_jobs(self) -> int:
        """How many spawned jobs are still running (not yet finished)."""
        self._release_finished()
        with self._lock:
            return len(self._running())

    def in_flight_by_org(self) -> dict[str, int]:
        """Running jobs grouped by org, for the ``/metrics`` endpoint (BE-0169). Counted under the
        lock, like `active_jobs`, so a concurrent register/finish can't corrupt the snapshot."""
        self._release_finished()
        with self._lock:
            return dict(Counter(j.org for j in self._running()))

    def _running(self) -> list[Job]:
        """The jobs still holding a concurrency-cap slot — the one definition every count and cap
        check reads, so a released job can't be running for one of them and finished for another.
        Caller must hold ``self._lock``."""
        return [j for j in self.jobs.values() if j.status == "running" and not j.released]

    def _release_finished(self) -> None:
        """Release the cap slot of every job `finished_ids` reports as finished elsewhere.

        The registry observes only an end written to its own `Job` (a job failed at dispatch). A job
        dispatched to a remote worker finishes in the jobs table instead — on the worker's result,
        or on a lease reclaim that no worker ever reports — and nothing writes that back to the
        control plane's `Job` (BE-0015 W2), so its slot would be held until serve restarts.

        Run by the counting entry points themselves rather than asked of their callers, the way
        `_freeze_binding` sits inside `register`/`try_register`: a count a caller had to remember to
        precede with a sweep is a count a later caller answers stale — this bug rediscovered.

        The probe runs outside the lock: it is a database read, and holding the registry lock across
        it would block every dispatch on the database. A job registered mid-sweep is simply not
        probed this round and stays counted, which errs toward the cap rather than past it.
        """
        if self.finished_ids is None:
            return
        with self._lock:
            live = [j.id for j in self._running()]
        if not live:
            return
        for job_id in self.finished_ids(live):
            job = self.jobs.get(job_id)
            if job is not None:
                job.released = True

    def register(self, job: Job) -> Job:
        with self._lock:
            return self._register(job)

    def try_register(
        self,
        job: Job,
        *,
        max_concurrent: int = 0,
        max_concurrent_per_user: int = 0,
        max_concurrent_per_org: int = 0,
    ) -> Job | None:
        """Register *job* only if under the concurrency caps, counting and inserting atomically under
        the lock so two concurrent dispatches can't both slip past a cap (BE-0051). Returns None at
        the global cap, at the per-user cap for an identified ``job.actor`` (BE-0015 7c-3), or at the
        per-org cap for ``job.org`` (BE-0016 Tier B pool fairness). Each cap ``<= 0`` is unlimited.
        A Device Farm batch has no cap of its own here: the worker that reserves the device bounds
        its jobs in flight with its ``maxJobConcurrency`` (BE-0448)."""
        self._release_finished()
        with self._lock:
            running = self._running()
            if max_concurrent > 0 and len(running) >= max_concurrent:
                return None
            if job.actor and max_concurrent_per_user > 0:
                mine = sum(1 for j in running if j.actor == job.actor)
                if mine >= max_concurrent_per_user:
                    return None
            if max_concurrent_per_org > 0:
                same_org = sum(1 for j in running if j.org == job.org)
                if same_org >= max_concurrent_per_org:
                    return None
            return self._register(job)

    def _register(self, job: Job) -> Job:
        """Assign the job its id + live-log bus and store it. Caller must hold ``self._lock``. The
        caller builds a fresh `Job` (the dataclass is the single source of truth for its fields), so
        adding a field never touches this layer. Single-use: registering a job that already has an id
        is a programming error (it would orphan the earlier ``jobs`` entry)."""
        if job.id:
            raise ValueError(f"job {job.id!r} is already registered")
        self._seq += 1
        job.id = str(self._seq)
        job.bus = self.logbus
        # Don't alias caller-owned collections (preserves the prior new_job semantics): a later edit
        # to the list/dict the caller passed must not mutate the registered job.
        job.udids = list(job.udids)
        job.materials = dict(job.materials)
        self.jobs[job.id] = job
        return job
