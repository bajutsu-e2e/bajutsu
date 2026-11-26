"""Borrow a claimed job's devices from the lease broker, and give them back (BE-0511).

The worker borrows after it has claimed a job, so it is the one that can give the claim back when
the broker has no device. The devices the job needs at once are all or nothing; the extra lanes of
`--workers` are taken while the broker lends them. Nothing here decides a verdict.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bajutsu.common.capability.worker_capability import LeaseBroker, load_worker_capability
from bajutsu.common.runner.device_provider import merge_lease_env
from bajutsu.common.runner.device_provider.lease_command import (
    BorrowedDevice,
    LeaseDeclinedError,
    borrow_device,
)
from bajutsu.common.runner.device_provider.tagged_udid import tag_addresses
from bajutsu.serve.jobs import _spawn_env
from bajutsu.serve.server.worker_job import run_flags
from bajutsu.serve.state import Job

# Borrowing is bounded below the job-lease timeout (`DEFAULT_LEASE_TIMEOUT_SECONDS`), so several
# slow borrows never outlast the claim they are made for.
BORROW_BUDGET_SECONDS = 90.0
# How long counting the devices may take: a config load, with a Git config's fetch.
_DEMAND_TIMEOUT_SECONDS = 120.0
_DEMAND_MODULE = "bajutsu.run.demand"

_logger = logging.getLogger("bajutsu.serve.worker")


@dataclass
class _Held:
    """The devices held for one job, each with the platform of its pool."""

    devices: list[tuple[str, BorrowedDevice]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def release(self) -> None:
        for platform, device in self.devices:
            try:
                device.release()
            except Exception:
                _logger.warning(
                    "could not release the %s device %s; it may be leaked",
                    platform,
                    device.address,
                    exc_info=True,
                )
        self.devices.clear()


class JobDevices:
    """One job's borrowed devices: `prepare` borrows them, `release` gives them back.

    *stop* answers True once the run must not start, because the job lease is lost or a cancel has
    arrived; it is asked before and between borrows.
    """

    def __init__(
        self,
        *,
        worker_file: Path | None,
        cwd: Path,
        stop: Callable[[], bool],
        log: Callable[[str], None],
        budget: float = BORROW_BUDGET_SECONDS,
    ) -> None:
        self._worker_file = worker_file
        self._cwd = cwd
        self._stop = stop
        self._log = log
        self._budget = budget
        self._held = _Held()

    def prepare(self, job: Job) -> bool:
        """Borrow what *job*'s run needs and point it at them; False when it must not start.

        A job that is not a run borrows nothing here: `record` and `crawl` borrow for themselves.

        Raises:
            LeaseDeclinedError: The broker did not lend every device one scenario needs at once;
                nothing is held.
            ValueError: The devices cannot be counted, a borrow was answered badly, or two leases
                need different values for one variable; nothing is held.
        """
        capability = load_worker_capability(self._worker_file)
        flags = run_flags(job.cmd)
        if capability.lease is None or flags is None:
            return True
        if self._stop():
            return False
        pools, workers = self._demand(job, flags)
        try:
            complete = self._borrow(capability.lease, pools, workers)
        except BaseException:
            self.release()
            raise
        if not complete or self._stop():
            self.release()
            return False
        if self._held.devices:
            addresses = tag_addresses((p, d.address) for p, d in self._held.devices)
            job.cmd = [*job.cmd, "--udid", addresses]
            job.lease_env = dict(self._held.env)
        return True

    def report_problems(self) -> None:
        """Say which leases ended while the run held them.

        A broker may have lent such a device again while the run drove it, so the job's own log
        carries the cause of what that run then saw. Called before that log is uploaded.
        """
        for _platform, device in self._held.devices:
            if (problem := device.problem()) is not None:
                _logger.warning("%s", problem)
                self._log(problem)

    def release(self) -> None:
        """End every lease held; safe to call more than once."""
        self._held.release()

    def _demand(self, job: Job, flags: list[str]) -> tuple[list[dict[str, Any]], int]:
        """The run's pools and `--workers`, counted in the run's own directory and environment."""
        try:
            done = subprocess.run(
                [sys.executable, "-m", _DEMAND_MODULE, *flags],
                cwd=str(job.cwd or self._cwd),
                env=_spawn_env(job),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_DEMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"could not count the devices this job needs: {e}") from e
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        if done.returncode != 0 or not lines:
            said = "\n".join([*lines, *done.stderr.strip().splitlines()[-5:]])
            raise ValueError(f"could not count the devices this job needs: {said}")
        answer = json.loads(lines[-1])
        return answer["pools"], answer["workers"]

    def _borrow(self, broker: LeaseBroker, pools: list[dict[str, Any]], workers: int) -> bool:
        """Borrow the mandatory demand, then up to *workers* - 1 more lanes; False when stopped."""
        # A list of models multiplies runs (BE-0468), so each takes the pool's count of its own.
        lane: list[tuple[str, str | None]] = [
            (pool["platform"], model)
            for pool in pools
            for model in (pool["models"] or [None])
            for _ in range(pool["count"])
        ]
        lane += [(pool["platform"], None) for pool in pools for _ in range(pool.get("unnamed", 0))]
        if not lane:
            return True
        deadline = time.monotonic() + self._budget
        for platform, model in lane:
            if self._stop():
                return False
            self._take(self._held, broker, platform, model, deadline)
        for extra in range(workers - 1):
            more = _Held(env=dict(self._held.env))
            try:
                for platform, model in lane:
                    if self._stop():
                        return False
                    self._take(more, broker, platform, model, deadline)
            except LeaseDeclinedError as e:
                # A lane is all or nothing, and a shortfall only means fewer lanes.
                self._log(f"running {extra + 1} lane(s) of {workers}: {e}")
                break
            else:
                self._held.devices += more.devices
                self._held.env = more.env
                more = _Held()
            finally:
                more.release()
        return True

    def _take(
        self,
        held: _Held,
        broker: LeaseBroker,
        platform: str,
        model: str | None,
        deadline: float,
    ) -> None:
        """Borrow one device into *held*, waiting no longer than the budget allows."""
        left = deadline - time.monotonic()
        if left <= 0:
            raise LeaseDeclinedError(f"the borrow budget of {self._budget:g} seconds is spent")
        try:
            device = borrow_device(
                broker.command,
                platform=platform,
                device=model,
                acquire_timeout=min(broker.acquire_timeout, left),
                release_timeout=broker.release_timeout,
            )
        except LeaseDeclinedError as e:
            if left >= broker.acquire_timeout:
                raise
            raise LeaseDeclinedError(
                f"{e} (only {left:.0f} s of the {self._budget:g} s borrow budget were left)"
            ) from e
        # Held before the merge, so a conflict releases this device with the rest.
        held.devices.append((platform, device))
        merge_lease_env(held.env, device.env)
        self._log(f"borrowed {platform} device {device.address}")
