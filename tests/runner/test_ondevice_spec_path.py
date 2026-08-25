"""The conformance harness's per-lease data-container read (BE-0378 units 1 and 2).

The read itself needs a Simulator, so what is pinned here is everything around it: that the path is
resolved once per lease rather than once per test, that a re-lease drops the memo, and that a single
timed-out read gets one further attempt while a second timeout still fails. The runner is injected,
so all of it runs on the fast gate with no device present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend_crash_recovery import LeaseHolder
from ondevice_spec_path import SpecPathMemo, read_data_container

from bajutsu import simctl

_UDID = "2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA"
_BUNDLE = "com.bajutsu.showcase.ios.swiftui"
_CONTAINER = "/Users/ci/Library/Devices/2A6D/data/Containers/Data/Application/1234"


class _FakeDriver:
    pass


def _timeout() -> simctl.DeviceTimeout:
    return simctl.DeviceTimeout(
        "device operation timed out after 60s: xcrun simctl get_app_container"
        " (this host's CoreSimulator may be wedged)"
    )


def test_reads_the_container_once_when_the_device_answers() -> None:
    calls: list[list[str]] = []
    said: list[str] = []

    def run(args: list[str], extra_env: object) -> str:
        calls.append(args)
        return _CONTAINER + "\n"

    assert read_data_container(_UDID, _BUNDLE, run, said.append) == _CONTAINER
    assert len(calls) == 1
    assert calls[0] == simctl.data_container_cmd(_UDID, _BUNDLE)
    assert said == []  # nothing stalled, so the lane hears nothing


def test_a_timed_out_read_gets_one_further_attempt() -> None:
    # The measured occurrence: the identical call answered 41s after the first was abandoned, so the
    # second attempt clears the stall outright rather than reddening a required check.
    attempts = {"n": 0}

    def run(args: list[str], extra_env: object) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _timeout()
        return _CONTAINER + "\n"

    said: list[str] = []
    assert read_data_container(_UDID, _BUNDLE, run, said.append) == _CONTAINER
    assert attempts["n"] == 2
    # Announced rather than logged: the caller is a fixture of a test that then passes, whose
    # captured log pytest never renders — so a logged stall would cost a deadline in silence.
    assert len(said) == 1
    assert "timed out" in said[0]


def test_a_stall_that_outlives_both_deadlines_still_fails() -> None:
    # Two full deadlines is the persistent wedge the lane reports as a host fault; the retry must not
    # keep grinding at it, so the second timeout propagates.
    attempts = {"n": 0}

    def run(args: list[str], extra_env: object) -> str:
        attempts["n"] += 1
        raise _timeout()

    with pytest.raises(simctl.DeviceTimeout):
        read_data_container(_UDID, _BUNDLE, run, lambda _: None)
    assert attempts["n"] == 2


def test_a_device_error_that_is_not_a_timeout_is_not_retried() -> None:
    # An app that is not installed is this run's own wiring, not a stall — a second identical call
    # would report the same thing, so it fails at once.
    attempts = {"n": 0}

    def run(args: list[str], extra_env: object) -> str:
        attempts["n"] += 1
        raise simctl.DeviceError("app is not installed")

    with pytest.raises(simctl.DeviceError):
        read_data_container(_UDID, _BUNDLE, run, lambda _: None)
    assert attempts["n"] == 1


def _holder() -> LeaseHolder:
    def launch() -> tuple[object, object]:
        return _FakeDriver(), (lambda: None)

    return LeaseHolder(launch)  # type: ignore[arg-type]


def test_resolves_once_per_lease_not_once_per_test() -> None:
    # The exposure BE-0378 removes: the suite's 23 collected items made 23 identical device round
    # trips for a path that is fixed for as long as the app installation is.
    resolved = {"n": 0}

    def resolve() -> Path:
        resolved["n"] += 1
        return Path(f"/spec/{resolved['n']}")

    memo = SpecPathMemo(resolve)
    holder = _holder()
    assert holder.driver is not None
    first = memo.for_lease(holder)
    assert memo.for_lease(holder) == first
    assert memo.for_lease(holder) == first
    assert resolved["n"] == 1


def test_a_re_lease_drops_the_memo() -> None:
    # Each lease reinstalls the app `clean`, which replaces the data container — a path cached across
    # the respawn would name a directory the next lease is not reading from.
    paths = iter([Path("/spec/first"), Path("/spec/second")])
    memo = SpecPathMemo(lambda: next(paths))
    holder = _holder()
    assert holder.driver is not None
    assert memo.for_lease(holder) == Path("/spec/first")
    holder.invalidate()
    assert holder.driver is not None
    assert memo.for_lease(holder) == Path("/spec/second")


def test_the_memo_leases_the_device_itself() -> None:
    # The invariant is enforced rather than asked of the caller: a memo that read the generation off
    # an unlaunched (or crash-invalidated) holder would arm against a lease that does not exist yet,
    # then serve the previous installation's container to the lease that replaced it.
    paths = iter([Path("/spec/first"), Path("/spec/second")])
    memo = SpecPathMemo(lambda: next(paths))
    holder = _holder()
    assert memo.for_lease(holder) == Path("/spec/first")  # never touched `driver` itself
    holder.invalidate()
    assert memo.for_lease(holder) == Path("/spec/second")


def test_a_failed_resolve_leaves_the_memo_unarmed() -> None:
    # Arming on a read that raised would serve a path nobody ever read; the next caller must retry.
    attempts = {"n": 0}

    def resolve() -> Path:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise simctl.DeviceTimeout("both attempts timed out")
        return Path("/spec/second-try")

    memo = SpecPathMemo(resolve)
    holder = _holder()
    assert holder.driver is not None
    with pytest.raises(simctl.DeviceTimeout):
        memo.for_lease(holder)
    assert memo.for_lease(holder) == Path("/spec/second-try")
