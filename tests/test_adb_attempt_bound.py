"""BE-0505: the adb driver's waits are bounded by completed reads, not by the host's speed alone.

The same fake resident channel runs under a driver clock at its own pace and ten times slower, and a
tree that catches up within the floor of reads must reach the same answer both times. A tree the
device reports it could not settle is read again before a selector resolves against it.
"""

from __future__ import annotations

import logging

import pytest
from test_adb import FIXTURE, _by_id, _Clock, _moved

import bajutsu.common.drivers.adb.adb_driver as adb_driver_impl
from bajutsu.common.backend_cli.adb_resident import narrowed_root
from bajutsu.common.drivers.adb import AdbDriver, AdbResidentError, HierarchyRead

_READ_COST_S = 0.4  # a quiet emulator's resident read
_SPEEDS = [1, 10]  # the second is the loaded host: every read and sleep takes ten times longer
_Y = "stable_refresh"


class _Channel:
    """A resident channel serving scripted reads, each one costing time on the driver's clock."""

    def __init__(self, reads: list[HierarchyRead], clock: _Clock, speed: int = 1) -> None:
        self._reads = list(reads)
        self._clock = clock
        self._cost = _READ_COST_S * speed
        self.count = 0

    def fetch(self, _since: float | None) -> HierarchyRead:
        self.count += 1
        self._clock.t += self._cost
        return self._reads.pop(0) if len(self._reads) > 1 else self._reads[0]


def _driver(channel: _Channel) -> AdbDriver:
    return AdbDriver(
        "U", run=lambda _args: "", fetch_hierarchy=channel.fetch, fetch_clock=lambda: 1000.0
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(adb_driver_impl, "time", fake)
    return fake


def _read(y: int = 100, mark: float = 1000.0, settled: bool | None = True) -> HierarchyRead:
    return HierarchyRead(_moved(y), mark, settled=settled)


def test_query_reads_again_while_the_device_could_not_settle(clock: _Clock) -> None:
    channel = _Channel([_read(110, settled=False), _read(130, settled=False), _read(150)], clock)
    tree = _driver(channel).query()
    assert channel.count == 3
    assert _by_id(tree, _Y)["frame"][1] == 150.0


@pytest.mark.parametrize("settled", [True, None])
def test_query_takes_one_read_when_the_device_does_not_say_unsettled(
    clock: _Clock, settled: bool | None
) -> None:
    # None is the server with no header, which behaves as it did before.
    channel = _Channel([_read(settled=settled)], clock)
    _driver(channel).query()
    assert channel.count == 1


def test_query_returns_the_latest_tree_with_a_warning_when_it_never_settles(
    clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    channel = _Channel([_read(settled=False)], clock)
    driver = _driver(channel)
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        tree = driver.query()
    assert tree
    assert channel.count == 1 + driver._UNSETTLED_REREADS
    assert "could not settle" in caplog.text


def test_agreeing_unsettled_reads_do_not_prove_rest(clock: _Clock) -> None:
    channel = _Channel([_read(settled=False), _read(settled=False), _read()], clock)
    driver = _driver(channel)
    driver._settle()
    # Two equal frames arrived on the second read; only the third, settled one closed the poll.
    assert channel.count == 3
    assert driver._settled_key is not None


def test_the_settled_fast_path_is_not_taken_on_an_unsettled_read(clock: _Clock) -> None:
    channel = _Channel([_read(), _read(), _read(settled=False), _read()], clock)
    driver = _driver(channel)
    driver._settle()  # proves the key
    before = channel.count
    driver._settle()
    assert channel.count - before > 1


def test_a_gesture_reply_the_device_could_not_settle_is_not_seeded(clock: _Clock) -> None:
    driver = _driver(_Channel([_read()], clock))
    root, _, _ = narrowed_root(FIXTURE)
    driver._seed_from_act(HierarchyRead(FIXTURE, 2000.0, root=root, settled=False), 1000.0)
    assert driver._seeded_tree is None
    driver._seed_from_act(HierarchyRead(FIXTURE, 2000.0, root=root, settled=True), 1000.0)
    assert driver._seeded_tree is not None


@pytest.mark.parametrize("speed", _SPEEDS)
def test_a_tree_published_on_the_fourth_read_closes_the_barrier_at_any_speed(
    clock: _Clock, speed: int, caplog: pytest.LogCaptureFixture
) -> None:
    stale, caught_up = _read(mark=1000.0), _read(mark=1001.0)
    channel = _Channel([stale, stale, stale, stale, caught_up], clock, speed)
    driver = _driver(channel)
    driver.query()  # the pre-gesture read
    driver.swipe((10, 300), (10, 100))
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        driver._await_catchup()
    assert driver.read_postdates_actuation()
    assert "read lag" not in caplog.text


@pytest.mark.parametrize("speed", _SPEEDS)
def test_a_tree_that_never_publishes_still_ends_the_barrier(
    clock: _Clock, speed: int, caplog: pytest.LogCaptureFixture
) -> None:
    channel = _Channel([_read(mark=1000.0)], clock, speed)
    driver = _driver(channel)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    before = channel.count
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        driver._await_catchup()
    assert driver._catchup is None
    assert not driver.read_postdates_actuation()
    assert channel.count - before >= driver._MIN_DEVICE_WAITS
    assert clock.t >= driver._READ_LAG_S  # the wall-clock span a quiet machine keeps
    assert "read lag" in caplog.text


@pytest.mark.parametrize("speed", _SPEEDS)
def test_a_screen_resting_by_the_fourth_poll_settles_at_any_speed(
    clock: _Clock, speed: int
) -> None:
    reads = [_read(110), _read(130), _read(150), _read(170), _read(170)]
    channel = _Channel(reads, clock, speed)
    driver = _driver(channel)
    tree = driver._settle()
    assert _by_id(tree, _Y)["frame"][1] == 170.0
    assert driver._settled_key is not None


def test_read_lag_stays_a_span_of_seconds(clock: _Clock) -> None:
    # The `scroll` loop and the `extract` poll take it as seconds on every backend.
    assert _driver(_Channel([_read()], clock)).read_lag() == AdbDriver._READ_LAG_S


def test_the_dump_fallback_forgets_an_earlier_unsettled_answer(clock: _Clock) -> None:
    def fetch(_since: float | None) -> HierarchyRead:
        if driver._device_settled is False:
            raise AdbResidentError("gone")
        return _read(settled=False)

    driver = AdbDriver(
        "U", run=lambda _args: FIXTURE, fetch_hierarchy=fetch, fetch_clock=lambda: 1000.0
    )
    driver.query()  # unsettled, then the channel dies and the re-read lands on the dump
    assert driver._device_settled is None


@pytest.mark.parametrize("speed", _SPEEDS)
def test_a_screen_the_device_never_settles_ends_the_poll_unproved(
    clock: _Clock, speed: int, caplog: pytest.LogCaptureFixture
) -> None:
    channel = _Channel([_read(settled=False)], clock, speed)
    driver = _driver(channel)
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        driver._settle()
    assert driver._settled_key is None
    # The frames never moved, so the warning must not blame a changing layout.
    assert "the frames agreed but the device could not settle" in caplog.text
    assert channel.count >= 1 + driver._MIN_DEVICE_WAITS
    if speed > 1:  # past the ceiling the floor alone sets the count: no nested re-reads
        assert channel.count == 1 + driver._MIN_DEVICE_WAITS


def test_the_barrier_does_not_nest_the_unsettled_rereads(
    clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    channel = _Channel([_read(mark=1000.0, settled=False)], clock)
    driver = _driver(channel)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        driver._await_catchup()
    assert "reads; resolving from the latest one" not in caplog.text


def test_a_settled_gesture_reply_clears_an_earlier_unsettled_answer(clock: _Clock) -> None:
    channel = _Channel([_read(settled=False)], clock)
    driver = _driver(channel)
    driver.query()
    root, _, _ = narrowed_root(FIXTURE)
    driver._seed_from_act(HierarchyRead(FIXTURE, 2000.0, root=root, settled=True), 1000.0)
    before = channel.count
    driver.query()
    assert channel.count == before


def test_a_quiet_host_keeps_polling_past_the_floor_until_the_ceiling(clock: _Clock) -> None:
    moving = [_read(100 + 10 * i) for i in range(9)]
    channel = _Channel([*moving, _read(180)], clock)
    driver = _driver(channel)
    tree = driver._settle()
    assert _by_id(tree, _Y)["frame"][1] == 180.0
    assert driver._settled_key is not None
