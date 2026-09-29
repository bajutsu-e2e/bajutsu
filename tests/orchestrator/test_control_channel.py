"""The acknowledgement wait behind a mid-scenario command (BE-0365 unit 3).

The four outcomes are the point: applied, refused, never answered, and issued against a collector
that carries no channel at all. Each has to reach the caller as its own message, because collapsing
any two of them is what would let a run proceed on an app state bajutsu never established.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from bajutsu.common.cancellation import RunCancelled
from bajutsu.common.evidence.network import (
    AppCommandReport,
    ControlChannel,
    InAppCapability,
    NetworkCollector,
    NetworkExchange,
    ScreenTransition,
    ToggleCapability,
)
from bajutsu.common.orchestrator.control_channel import (
    ControlChannelError,
    apply_capability,
    capability_suspended,
    replace_stub_table,
)
from bajutsu.common.scenario import Mock

_TOUCH: ToggleCapability = InAppCapability.TOUCH_VISUALIZATION
# Short enough that the timeout case costs the fast suite nothing, and still a real monotonic
# deadline rather than a stubbed clock — `deadline_ticks` owns the wait, and stubbing it out would
# test the stub.
_QUICK = 0.05


class _FakeChannel:
    """A channel whose reports are decided by the test, not by an app.

    Also a `Collector` — a bare `enqueue_command`/`report_for` stub would satisfy `ControlChannel`
    but not the wider `Collector | None` parameter type `apply_capability` actually declares, which
    matches what `pipeline.py` really passes (`Lease.collector`). The five methods below are
    unexercised no-ops; only `enqueue_command`/`report_for` matter to these tests.
    """

    def __init__(self, reply: tuple[bool, str] | None = (True, "")) -> None:
        self.reply = reply
        self.issued: list[tuple[InAppCapability, bool]] = []
        self.tables: list[list[Mock]] = []

    def enqueue_command(self, capability: InAppCapability, *, enabled: bool) -> str:
        self.issued.append((capability, enabled))
        return f"c{len(self.issued)}"

    def enqueue_stub_table(self, mocks: Sequence[Mock]) -> str:
        self.tables.append(list(mocks))
        return f"t{len(self.tables)}"

    def report_for(self, command_id: str) -> AppCommandReport | None:
        if self.reply is None:
            return None
        applied, reason = self.reply
        return AppCommandReport(id=command_id, applied=applied, reason=reason)

    def snapshot(self) -> list[NetworkExchange]:
        return []

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return []

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        return []

    def clear(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_the_real_collector_carries_a_channel_and_the_fake_one_does_not() -> None:
    """Which collectors carry a channel is answered by the protocol, not by a registry.

    `NetworkCollector` holds the queue its receiver drains; a driver-observed collector has no way
    to reach the app at all, so it simply does not satisfy `ControlChannel`.
    """
    from bajutsu.common.drivers.fake import FakeNetworkCollector

    assert isinstance(NetworkCollector(), ControlChannel)
    assert not isinstance(FakeNetworkCollector([]), ControlChannel)


def test_a_collector_with_no_channel_fails_loudly_rather_than_skipping() -> None:
    from bajutsu.common.drivers.fake import FakeNetworkCollector

    with pytest.raises(ControlChannelError) as err:
        apply_capability(FakeNetworkCollector([]), _TOUCH, enabled=False, timeout=_QUICK)
    assert "carries no control channel" in str(err.value)
    assert "FakeNetworkCollector" in str(err.value)


def test_no_collector_at_all_fails_the_same_way() -> None:
    """`None` is the common shape of "this run has no collector", and it is not a licence to skip."""
    with pytest.raises(ControlChannelError) as err:
        apply_capability(None, _TOUCH, enabled=False, timeout=_QUICK)
    # A bare "NoneType" would read as a bajutsu bug rather than as "network collection is off" —
    # the `--no-network` shape this branch actually names.
    assert "no collector at all" in str(err.value)
    assert "NoneType" not in str(err.value)


def test_an_applied_command_releases_the_wait() -> None:
    channel = _FakeChannel(reply=(True, ""))
    apply_capability(channel, _TOUCH, enabled=False, timeout=_QUICK)
    assert channel.issued == [(_TOUCH, False)]


def test_a_refusal_fails_at_once_carrying_the_apps_own_reason() -> None:
    """A refused command is not a slow one: the wait ends immediately, with the app's words."""
    channel = _FakeChannel(reply=(False, "touch_visualization is compiled out"))
    with pytest.raises(ControlChannelError) as err:
        apply_capability(channel, _TOUCH, enabled=True, timeout=_QUICK)
    assert "compiled out" in str(err.value)


def test_a_refusal_with_no_reason_still_says_the_app_refused() -> None:
    """The app is not obliged to explain, and an empty reason must not read as an empty failure."""
    channel = _FakeChannel(reply=(False, ""))
    with pytest.raises(ControlChannelError) as err:
        apply_capability(channel, _TOUCH, enabled=True, timeout=_QUICK)
    assert "without saying why" in str(err.value)


def test_an_unanswered_command_times_out_naming_both_gates() -> None:
    """Silence usually means the app was never built to carry the channel, so the message says so.

    Timing out blind is what the wait exists to avoid: the two gates are the only things an operator
    can act on, and neither is visible to bajutsu from the host.
    """
    channel = _FakeChannel(reply=None)
    with pytest.raises(ControlChannelError) as err:
        apply_capability(channel, _TOUCH, enabled=False, timeout=_QUICK)
    message = str(err.value)
    assert "BAJUTSU_ENABLE_CONTROL_CHANNEL" in message
    assert "BAJUTSU_CONTROL_CHANNEL=1" in message


def test_a_cancelled_run_stops_the_wait_instead_of_spending_the_full_timeout() -> None:
    """BE-0370: `cancelled` is checked inside this poll like every other condition wait.

    Without this, a `SIGTERM` mid-wait would still cost up to `timeout` seconds here before the
    run's own cancellation could take effect — this pins that it does not.
    """
    channel = _FakeChannel(reply=None)
    with pytest.raises(RunCancelled):
        apply_capability(channel, _TOUCH, enabled=False, timeout=10.0, cancelled=lambda: True)


def test_suspension_turns_the_capability_off_for_the_body_and_back_on_after() -> None:
    channel = _FakeChannel(reply=(True, ""))
    seen: list[list[tuple[InAppCapability, bool]]] = []
    with capability_suspended(channel, _TOUCH, timeout=_QUICK):
        seen.append(list(channel.issued))
    assert seen == [[(_TOUCH, False)]]
    assert channel.issued == [(_TOUCH, False), (_TOUCH, True)]


def test_a_body_that_raises_still_restores_the_capability() -> None:
    channel = _FakeChannel(reply=(True, ""))
    with pytest.raises(ZeroDivisionError), capability_suspended(channel, _TOUCH, timeout=_QUICK):
        raise ZeroDivisionError
    assert channel.issued == [(_TOUCH, False), (_TOUCH, True)]


def test_a_failed_restore_does_not_mask_the_bodys_own_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The body's failure is the one worth reporting; a restore that also fails is logged, not raised.

    Raising the restore's error instead would report a control-channel problem for a run that failed
    for an entirely different reason.
    """

    class _OnlyOffWorks(_FakeChannel):
        def report_for(self, command_id: str) -> AppCommandReport | None:
            return None if command_id == "c2" else AppCommandReport(id=command_id, applied=True)

    channel = _OnlyOffWorks()
    with (
        caplog.at_level(logging.WARNING, logger="bajutsu.common.orchestrator.control_channel"),
        pytest.raises(ValueError, match="cares about"),
        capability_suspended(channel, _TOUCH, timeout=_QUICK),
    ):
        raise ValueError("the assertion the run actually cares about")
    # That warning is the only trace the swallowed restore leaves, so an investigator reading the
    # body's failure can still tell the app was left with the capability off.
    assert any(
        r.name == "bajutsu.common.orchestrator.control_channel"
        and r.levelno == logging.WARNING
        and _TOUCH.value in r.getMessage()
        for r in caplog.records
    )


def test_a_restore_failure_of_any_kind_still_lets_the_bodys_own_failure_through() -> None:
    """The restore's own exception type must not decide whether the body's failure survives.

    A channel implementation that raises something other than `ControlChannelError` while
    restoring — a bug, or a test double that does not honor the protocol — must not turn into the
    exception the caller sees; only the body's own failure may do that.
    """

    class _RestoreRaisesSomethingElse(_FakeChannel):
        def report_for(self, command_id: str) -> AppCommandReport | None:
            if command_id == "c2":
                raise RuntimeError("a bug in this channel implementation")
            return AppCommandReport(id=command_id, applied=True)

    channel = _RestoreRaisesSomethingElse()
    with (
        pytest.raises(ValueError, match="cares about"),
        capability_suspended(channel, _TOUCH, timeout=_QUICK),
    ):
        raise ValueError("the assertion the run actually cares about")


def test_a_failed_restore_after_a_clean_body_is_raised() -> None:
    """Nothing else is failing, so leaving the app in the suspended state silently would be a lie."""

    class _OnlyOffWorks(_FakeChannel):
        def report_for(self, command_id: str) -> AppCommandReport | None:
            return None if command_id == "c2" else AppCommandReport(id=command_id, applied=True)

    with (
        pytest.raises(ControlChannelError),
        capability_suspended(_OnlyOffWorks(), _TOUCH, timeout=_QUICK),
    ):
        pass


# --- the stub table (BE-0365 unit 4) ------------------------------------------------------------


def _table() -> list[Mock]:
    return [Mock.model_validate({"match": {"path": "/me"}, "respond": {"status": 500}})]


def test_a_stub_table_the_app_applied_releases_the_wait_with_the_whole_table_sent() -> None:
    channel = _FakeChannel()
    replace_stub_table(channel, _table(), timeout=_QUICK)
    assert channel.tables == [_table()]
    assert channel.issued == []  # never routed through the toggle call


def test_a_refused_stub_table_fails_at_once_naming_the_table_and_the_apps_reason() -> None:
    channel = _FakeChannel(reply=(False, "mock 0 has no match"))
    with pytest.raises(ControlChannelError) as err:
        replace_stub_table(channel, _table(), timeout=_QUICK)
    assert "stub_table (1 mock)" in str(err.value)
    assert "mock 0 has no match" in str(err.value)


def test_an_unanswered_stub_table_times_out_naming_both_gates() -> None:
    with pytest.raises(ControlChannelError) as err:
        replace_stub_table(_FakeChannel(reply=None), [], timeout=_QUICK)
    message = str(err.value)
    assert "stub_table (0 mocks)" in message
    assert "BAJUTSU_ENABLE_CONTROL_CHANNEL" in message and "BAJUTSU_CONTROL_CHANNEL=1" in message


def test_a_stub_table_against_no_channel_fails_loudly() -> None:
    from bajutsu.common.drivers.fake import FakeNetworkCollector

    with pytest.raises(ControlChannelError, match="no collector at all"):
        replace_stub_table(None, _table(), timeout=_QUICK)
    with pytest.raises(ControlChannelError, match="carries no control channel"):
        replace_stub_table(FakeNetworkCollector([]), _table(), timeout=_QUICK)


def test_a_cancelled_run_stops_the_stub_table_wait() -> None:
    with pytest.raises(RunCancelled):
        replace_stub_table(_FakeChannel(reply=None), _table(), timeout=5.0, cancelled=lambda: True)


def test_a_stub_table_round_trips_through_the_real_collector() -> None:
    """Against `NetworkCollector` itself: the wait releases only once a report for the id the
    collector minted arrives, which is the contract unit 3's toggle already relies on."""
    import threading
    import time

    collector = NetworkCollector()

    def app() -> None:
        # Stand in for BajutsuKit's poll: drain, then acknowledge what was drained. Bounded, so a
        # regression that never enqueues fails this test instead of spinning past it.
        deadline = time.monotonic() + 5.0
        while not (drained := collector.drain_commands()):
            if time.monotonic() > deadline:
                return
            threading.Event().wait(0.01)
        for command in drained:
            collector.record_report({"id": command.id, "applied": True})

    worker = threading.Thread(target=app, daemon=True)
    worker.start()
    replace_stub_table(collector, _table(), timeout=5.0)
    worker.join()
