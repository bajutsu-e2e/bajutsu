"""A timed-out resident read recovers before the lease falls back to `uiautomator dump`."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import el

from bajutsu.common import stall_diagnostics
from bajutsu.common.backend_cli import adb_resident
from bajutsu.common.backend_cli.adb_resident import Keepalive, budgets
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import (
    ActRequest,
    AdbDriver,
    AdbResidentError,
    AdbResidentTimeout,
    ChannelRecovery,
    HierarchyRead,
)
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.report import load, manifest
from bajutsu.common.scenario import Scenario

_TREE = '<hierarchy rotation="0"><node class="android.widget.FrameLayout" package="p" bounds="[0,0][9,9]" /></hierarchy>'
_READ = HierarchyRead(_TREE, 1.0)


class _Proc:
    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:  # `_Process` shape
        return 0


class _Lease:
    """A `ResidentServer` over scripted reads, recording what it spawned and which ports it read."""

    def __init__(
        self, tmp_path: Path, reads: list[HierarchyRead | Exception], **kwargs: object
    ) -> None:
        server_apk = tmp_path / "server.apk"
        test_apk = tmp_path / "test.apk"
        server_apk.write_bytes(b"s")
        test_apk.write_bytes(b"t")
        # The readiness probe's own read, so each case scripts only the reads the driver makes.
        self.reads: Iterator[HierarchyRead | Exception] = iter(reads)
        self.spawns = 0
        self.ports: list[int] = []
        self.stops = 0
        self.next_port = 41000
        self.probing = True

        def run(args: list[str]) -> str:
            if "forward" in args and "--remove" not in args:
                self.next_port += 1
                return f"{self.next_port}\n"
            if "force-stop" in args:
                self.stops += 1
            return ""

        def spawn(_argv: list[str]) -> _Proc:
            self.spawns += 1
            self.probing = True
            return _Proc()

        def fetch(port: int, _since: float | None) -> HierarchyRead:
            if self.probing:
                self.probing = False
                return _READ
            self.ports.append(port)
            result = next(self.reads)
            if isinstance(result, Exception):
                raise result
            return result

        self.server = adb_resident.ResidentServer(
            "U",
            run=run,
            spawn=spawn,
            fetch=fetch,
            server_apk=server_apk,
            test_apk=test_apk,
            **kwargs,  # type: ignore[arg-type]
        )


_TIMEOUT = AdbResidentTimeout("timed out")


def test_a_read_that_times_out_is_retried_without_touching_the_server(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _READ])
    channel = lease.server.start()
    assert channel.fetch(None).text == _TREE
    assert lease.spawns == 1
    assert lease.stops == 0
    assert channel.recovery is not None
    assert channel.recovery.live()
    assert channel.recovery.drain_notes() == ["resident read recovered on a retry after: timed out"]
    assert channel.recovery.drain_notes() == []


def test_a_server_still_silent_after_the_retries_is_restarted_on_a_new_port(
    tmp_path: Path,
) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT, _READ])
    channel = lease.server.start()
    assert channel.fetch(None).text == _TREE
    assert lease.spawns == 2
    assert lease.stops == 1
    assert lease.ports == [41001, 41001, 41002]
    assert channel.recovery is not None
    assert channel.recovery.drain_notes() == ["resident server restarted after: timed out"]


def test_read_retries_sets_how_many_reads_repeat(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT, _TIMEOUT, _READ], read_retries=3)
    assert lease.server.start().fetch(None).text == _TREE
    assert lease.spawns == 1


def test_a_lease_that_may_not_restart_stops_and_falls_back(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT], restart=False)
    channel = lease.server.start()
    with pytest.raises(AdbResidentTimeout):
        channel.fetch(None)
    assert lease.spawns == 1
    assert lease.stops == 1  # the UiAutomation session is released before the dump fallback


def test_a_restarted_server_that_still_cannot_answer_is_stopped(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT, _TIMEOUT])
    channel = lease.server.start()
    with pytest.raises(AdbResidentTimeout):
        channel.fetch(None)
    assert lease.spawns == 2
    assert lease.stops == 2
    assert channel.recovery is not None
    assert not channel.recovery.live()


def test_a_channel_that_refuses_falls_back_at_once(tmp_path: Path) -> None:
    # A timeout may be a slow device. A refusal is a server that cannot answer: retrying only delays
    # the dump path every later read then takes anyway.
    lease = _Lease(tmp_path, [AdbResidentError("refused")])
    channel = lease.server.start()
    with pytest.raises(AdbResidentError, match="refused"):
        channel.fetch(None)
    assert lease.ports == [41001]
    assert lease.spawns == 1
    assert lease.stops == 1


def test_the_other_endpoints_follow_a_restart_and_notice_a_stop(tmp_path: Path) -> None:
    clock_ports: list[int] = []

    def clock(port: int) -> float:
        clock_ports.append(port)
        return 1.0

    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT, _READ], clock=clock)
    channel = lease.server.start()
    channel.fetch(None)
    channel.clock()
    assert clock_ports == [41002]
    lease.server.stop()
    assert channel.clock() is None
    with pytest.raises(AdbResidentError, match="stopped"):
        channel.fetch(None)
    with pytest.raises(AdbResidentError, match="stopped"):
        channel.type_text("x", None)


def test_revive_starts_a_stopped_server_again(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [])
    channel = lease.server.start()
    lease.server.stop()
    assert channel.recovery is not None
    assert channel.recovery.revive()
    assert lease.spawns == 2
    assert channel.recovery.live()


def test_revive_honors_a_lease_that_may_not_restart(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [], restart=False)
    channel = lease.server.start()
    lease.server.stop()
    assert channel.recovery is not None
    assert not channel.recovery.revive()
    assert lease.spawns == 1


def test_revive_reports_a_server_that_does_not_come_back(tmp_path: Path) -> None:
    lease = _Lease(tmp_path, [])
    channel = lease.server.start()
    lease.server.stop()
    (tmp_path / "server.apk").unlink()
    assert channel.recovery is not None
    assert not channel.recovery.revive()
    assert "did not come back" in channel.recovery.drain_notes()[0]


# The wire: a silent peer and a closed port have to reach the caller as different errors.


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A port that accepts connections and never answers, as a server busy on another request does."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        accepted: list[socket.socket] = []

        def serve() -> None:
            try:
                while True:
                    accepted.append(listener.accept()[0])
            except OSError:
                return

        threading.Thread(target=serve, daemon=True).start()
        yield listener.getsockname()[1]
        for conn in accepted:
            conn.close()


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_fetch_source_tells_a_timeout_from_a_refusal(silent_port: int) -> None:
    with pytest.raises(AdbResidentTimeout):
        adb_resident.fetch_source(silent_port, timeout=0.05)
    with pytest.raises(AdbResidentError) as refused:
        adb_resident.fetch_source(_closed_port(), timeout=0.05)
    assert not isinstance(refused.value, AdbResidentTimeout)


def test_a_timed_out_connection_is_not_kept_for_the_next_call(silent_port: int) -> None:
    # The late reply would otherwise be read as the next request's answer.
    keepalive = Keepalive()
    with pytest.raises(AdbResidentTimeout):
        adb_resident.fetch_source(silent_port, timeout=0.05, keepalive=keepalive)
    assert keepalive.conn is None


# The driver: what it does with a channel that has been through all of the above.


class _Channel:
    """The read side of a channel a driver can lose, and the recovery behind it."""

    def __init__(self, *, revives: bool = True) -> None:
        self.up = True
        self.revives = revives
        self.revive_calls = 0
        self.notes: list[str] = []
        self.typed: list[tuple[str, float | None]] = []
        self.recovery = ChannelRecovery(
            live=lambda: self.up, revive=self._revive, drain_notes=self._drain
        )

    def _revive(self) -> bool:
        self.revive_calls += 1
        self.up = self.revives
        return self.revives

    def _drain(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    def fetch(self, _since: float | None) -> HierarchyRead:
        if not self.up:
            raise AdbResidentError("channel lost")
        return _READ

    def type_text(self, text: str, since: float | None) -> None:
        self.typed.append((text, since))


def _driver(channel: _Channel, dumps: list[list[str]] | None = None) -> AdbDriver:
    def run(args: list[str]) -> str:
        if dumps is not None:
            dumps.append(args)
        return _TREE

    return AdbDriver(
        "U",
        run=run,
        fetch_hierarchy=channel.fetch,
        fetch_clock=lambda: 5.0,
        type_on_device=channel.type_text,
        channel_recovery=channel.recovery,
    )


def _lost(channel: _Channel, dumps: list[list[str]] | None = None) -> AdbDriver:
    driver = _driver(channel, dumps)
    channel.up = False
    driver.query()
    return driver


def test_a_lost_channel_is_recorded_for_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    channel = _Channel()
    channel.notes.append("resident read recovered on a retry after: timed out")
    driver = _lost(channel)
    assert driver.channel_degraded() == "channel lost"
    notes = driver.drain_channel_notes()
    assert notes[0].startswith("resident read recovered")
    assert notes[1].startswith("resident channel lost (channel lost)")
    assert driver.drain_channel_notes() == []
    assert capsys.readouterr().out == ""


def test_non_ascii_text_brings_a_lost_channel_back_first() -> None:
    channel = _Channel()
    driver = _lost(channel)
    driver.type_text("馬術")
    assert channel.revive_calls == 1
    assert [text for text, _ in channel.typed] == ["馬術"]
    assert driver.channel_degraded() is None
    assert any("back after: channel lost" in note for note in driver.drain_channel_notes())
    # The channel is the read path again, and its clock anchors the next entry.
    driver.query()
    assert driver._fetch_clock is not None
    driver.type_text("二")
    assert channel.typed[-1] == ("二", 5.0)


def test_text_fails_the_way_it_always_did_when_the_server_will_not_come_back() -> None:
    channel = _Channel(revives=False)
    driver = _lost(channel)
    with pytest.raises(base.UnsupportedAction, match="needs the resident UI Automator server"):
        driver.type_text("馬術")
    with pytest.raises(base.UnsupportedAction):
        driver.type_text("馬術")
    assert channel.revive_calls == 1  # the next step does not pay another failed start
    assert driver.channel_degraded() == "channel lost"


def test_ascii_text_does_not_restart_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AdbDriver, "_run_text", staticmethod(lambda _cmd, _script: None))
    channel = _Channel()
    driver = _lost(channel)
    driver.type_text("horse")
    assert channel.revive_calls == 0
    assert driver.channel_degraded() == "channel lost"


def test_a_driver_rejoins_a_server_another_driver_brought_back() -> None:
    # A device group shares one server, and a dump beside a live server reads an empty tree.
    channel = _Channel()
    dumps: list[list[str]] = []
    driver = _lost(channel, dumps)
    assert len(dumps) == 1
    channel.up = True
    driver.query()
    assert len(dumps) == 1
    assert driver.channel_degraded() is None


def test_rejoining_keeps_what_an_older_server_could_not_do() -> None:
    channel = _Channel()
    driver = _driver(channel)
    driver._act_unavailable = True  # an older server answered 404 for `/act`
    channel.up = False
    driver.query()
    channel.up = True
    driver.query()
    assert driver._act_unavailable is True


def test_a_server_that_never_started_is_on_the_record_from_the_first_drain() -> None:
    driver = AdbDriver("U", run=lambda _a: _TREE, channel_start_failure="did not answer in 20s")
    assert driver.channel_degraded() == "did not answer in 20s"
    assert "did not start (did not answer in 20s)" in driver.drain_channel_notes()[0]


def test_a_healthy_driver_reports_nothing() -> None:
    driver = AdbDriver("U", run=lambda _a: _TREE)
    assert driver.channel_degraded() is None
    assert driver.drain_channel_notes() == []


def test_a_server_that_keeps_wedging_stops_being_restarted(tmp_path: Path) -> None:
    # A restarted server answers its first read, so every read could cost a restart forever.
    lease = _Lease(
        tmp_path, [_TIMEOUT, _TIMEOUT, _READ, _TIMEOUT, _TIMEOUT, _READ, _TIMEOUT, _TIMEOUT]
    )
    channel = lease.server.start()
    channel.fetch(None)
    channel.fetch(None)
    with pytest.raises(AdbResidentTimeout):
        channel.fetch(None)
    assert lease.spawns == 3  # the first start and two restarts


def test_a_read_that_answers_first_try_clears_the_restart_count(tmp_path: Path) -> None:
    wedge: list[HierarchyRead | Exception] = [_TIMEOUT, _TIMEOUT, _READ]
    lease = _Lease(tmp_path, [*wedge, _READ, *wedge, _READ, *wedge])
    channel = lease.server.start()
    for _ in range(5):
        channel.fetch(None)
    assert lease.spawns == 4


def test_a_restart_captures_the_wedged_server_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = _Lease(tmp_path, [_TIMEOUT, _TIMEOUT, _READ])
    channel = lease.server.start()
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        stall_diagnostics, "capture", lambda reason, _probes: seen.append((reason, lease.stops))
    )
    channel.fetch(None)
    assert seen == [("resident-restart", 0)]  # taken while the server was still there to read


def test_text_revives_a_server_another_driver_stopped() -> None:
    # This driver holds no loss of its own: it has not read since the other member lost the channel.
    channel = _Channel()
    driver = _driver(channel)
    channel.up = False
    assert driver.channel_degraded() == "resident server is stopped"
    driver.type_text("馬術")
    assert channel.revive_calls == 1
    assert [text for text, _ in channel.typed] == ["馬術"]
    assert driver.channel_degraded() is None


def test_a_server_lost_twice_is_revived_twice() -> None:
    channel = _Channel()
    driver = _lost(channel)
    driver.type_text("一")
    channel.up = False
    driver.query()
    driver.type_text("二")
    assert channel.revive_calls == 2


class _ReportingDriver(FakeDriver):
    """A driver whose channel falls back during its last expected tap and stays that way."""

    def __init__(self, taps_before_loss: int = 0) -> None:
        super().__init__([el("x", "X")])
        self.notes: list[str] = []
        self.loss: str | None = None
        self.taps_before_loss = taps_before_loss

    def tap(self, sel: base.Selector) -> None:
        super().tap(sel)
        if self.taps_before_loss > 0:
            self.taps_before_loss -= 1
            return
        self.notes.append("resident channel lost (timed out); reading via uiautomator dump")
        self.loss = "timed out"

    def drain_channel_notes(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    def channel_degraded(self) -> str | None:
        return self.loss


_TAP = {"tap": {"id": "x"}}


def test_a_passing_scenario_shows_which_step_lost_the_channel() -> None:
    scenario = Scenario.model_validate({"name": "s", "steps": [_TAP, _TAP]})
    result = run_scenario(_ReportingDriver(taps_before_loss=1), scenario)
    assert result.ok, result.failure
    assert result.steps[0].warnings == ()
    assert result.steps[1].warnings == (
        "resident channel lost (timed out); reading via uiautomator dump",
    )
    assert result.channel_degraded == "timed out"
    assert result.channel_notes == []


def test_the_record_survives_the_manifest() -> None:
    result = run_scenario(
        _ReportingDriver(), Scenario.model_validate({"name": "s", "steps": [_TAP]})
    )
    stored = manifest._scenario_dict(result)
    assert stored["channel_degraded"] == "timed out"
    json.dumps(stored)
    loaded = load._result(stored)
    assert loaded.channel_degraded == "timed out"
    assert loaded.steps[0].warnings == result.steps[0].warnings


def test_a_backend_with_no_such_channel_records_nothing() -> None:
    scenario = Scenario.model_validate({"name": "s", "steps": [_TAP]})
    result = run_scenario(FakeDriver([el("x", "X")]), scenario)
    assert result.channel_degraded == ""
    assert result.channel_notes == []
    assert result.steps[0].warnings == ()


def test_a_connect_that_times_out_is_a_timeout_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_connect(_self: Keepalive, _port: int, _timeout: float) -> None:
        raise TimeoutError("connect")

    monkeypatch.setattr(Keepalive, "connection", slow_connect)
    with pytest.raises(AdbResidentTimeout):
        adb_resident.fetch_source(1, keepalive=Keepalive())


def test_a_press_and_hold_extends_the_timeout_by_its_own_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The server holds the press before it answers, so the hold cannot come out of its wait budget.
    seen: list[float] = []

    class _Conn:
        def __init__(self, _host: str, _port: int, timeout: float) -> None:
            seen.append(timeout)

        def request(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("nothing sent")

        def close(self) -> None:
            pass

    monkeypatch.setattr("http.client.HTTPConnection", _Conn)
    request = ActRequest(
        kind="longPress",
        identity=("", "", "", ""),
        index=0,
        count=1,
        since=None,
        duration_ms=12_000,
    )
    with pytest.raises(AdbResidentError):
        adb_resident.act(1, request)
    assert seen == [budgets.ACTION_TIMEOUT_S + 12.0]
