"""The conformance spec file's path: read once per lease, and retried once on a wedge (BE-0378).

The on-device conformance suite reseeds each screen by writing a file in the app's data container,
so it needs that container's path — a `simctl get_app_container` read. The path belongs to the app
installation rather than to any one test, and its function-scoped `harness` fixture used to pay the
device round trip for every one of the suite's items, which is where a wedged CoreSimulator found a
required check to redden (see the item's Motivation for the measured occurrence).

Kept out of `backend_crash_recovery.py` for the reason `xcuitest_lease.py` is: a data container is
an iOS notion, and that plugin's whole point is never to learn which backend it drives.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backend_crash_recovery import LeaseHolder

from bajutsu.common.backend_cli import simctl


def read_data_container(
    udid: str, bundle_id: str, run: simctl.RunFn, announce: Callable[[str], None]
) -> str:
    """The installed app's data container path, giving a timed-out read one further attempt.

    A stall that clears between two deadlines is the fault this absorbs: the occurrence BE-0378
    measured answered the identical call 41 seconds after the first was abandoned, so a second
    attempt would have cleared it outright, while a stall that survives both deadlines is the
    persistent wedge the lane reports as a host fault instead. Retrying here rather than inside
    `simctl`'s own runner leaves the policy BE-0363 settled untouched — this call is the harness's
    own preparatory read, idempotent and made before any test body runs, not a step whose result is
    a scenario's data.

    Args:
        run: The simctl runner, injected so the retry is exercised on the fast gate without a device.
        announce: Reports an absorbed stall. Injected rather than logged, because the caller that
            pays the deadline is a fixture of a test that then passes, and a captured log record is
            rendered only for a report that failed — so the lane would eat the stall in silence.
    """
    cmd = simctl.data_container_cmd(udid, bundle_id)
    try:
        return run(cmd, None).strip()
    except simctl.DeviceTimeout as exc:
        announce(f"resolving the app's data container timed out, retrying once: {exc}")
    return run(cmd, None).strip()


class SpecPathMemo:
    """The spec file's path, memoised against the lease whose installation it names (BE-0378).

    Not the module's lifetime: each lease launches the app under a `clean` reinstall, which takes the
    data container with it, so a path cached across a cold respawn would point where the app is no
    longer reading — and the suite would then fail a conformance assertion, reporting a host fault as
    a driver-contract defect. Keying on `LeaseHolder.generation` ties the memo to the installation it
    was read from, so a re-lease drops it.
    """

    def __init__(self, resolve: Callable[[], Path]) -> None:
        self._resolve = resolve
        self._cached: tuple[int, Path] | None = None

    def for_lease(self, holder: LeaseHolder) -> Path:
        """The path for `holder`'s current lease, reading the device only when the lease changed.

        A resolve that raises leaves the previous memo in place rather than arming a new one, so the
        next caller retries instead of being served a path that was never read.
        """
        # Lease first, so the generation below names a live lease rather than one not yet launched:
        # after a crash the holder still carries the old generation until this access re-leases, and
        # memoising against that would hand back the previous installation's container — the very
        # stale path this memo exists to prevent. Enforced here rather than asked of each caller.
        _ = holder.driver
        cached = self._cached
        if cached is not None and cached[0] == holder.generation:
            return cached[1]
        path = self._resolve()
        self._cached = (holder.generation, path)
        return path
