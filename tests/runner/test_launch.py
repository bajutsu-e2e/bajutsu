"""Tests for device bring-up (launch_driver) and readiness polling (_await_ready)."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from _runner import _eff, _el

from bajutsu import simctl
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.runner import (
    _await_ready,
    launch_driver,
)
from bajutsu.scenario import Preconditions


def test_launch_driver_shuts_down_before_erase(monkeypatch: pytest.MonkeyPatch) -> None:
    """erase requires a shut-down device, so the sequence is shutdown -> erase -> boot."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    ready = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])  # 2 elems -> ready immediately
    monkeypatch.setattr("bajutsu.platform_lifecycle.make_driver", lambda actuator, udid: ready)

    launch_driver("UDID-1", _eff(), "idb", Preconditions(erase=True), env_run=fake_run)

    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert verbs.index("shutdown") < verbs.index("erase") < verbs.index("boot")
    assert verbs.index("boot") < verbs.index("launch")  # boot before launching the app


def test_launch_driver_injects_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_env (e.g. the device's collector url) reaches the app via the launch child env."""
    calls: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append((args, extra_env))
        return ""

    ready = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])
    monkeypatch.setattr("bajutsu.platform_lifecycle.make_driver", lambda actuator, udid: ready)

    launch_driver(
        "UDID-1",
        _eff(),
        "idb",
        Preconditions(erase=False),
        env_run=fake_run,
        extra_env={"BAJUTSU_COLLECTOR": "http://127.0.0.1:7"},
    )

    _, launch_env = next(c for c in calls if "launch" in c[0])
    assert launch_env.get("SIMCTL_CHILD_BAJUTSU_COLLECTOR") == "http://127.0.0.1:7"


def _recording_run(calls: list[list[str]]):
    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    return fake_run


def _launch_recording(
    monkeypatch: pytest.MonkeyPatch, app_path: str, pre: Preconditions
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.make_driver",
        lambda actuator, udid: FakeDriver([_el("home.title", "H"), _el("ok", "OK")]),
    )
    launch_driver(
        "UDID-1", replace(_eff(), app_path=app_path), "idb", pre, env_run=_recording_run(calls)
    )
    return calls


def test_launch_driver_reinstall_clean_uninstalls_then_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default `reinstall: clean` removes the app then installs it fresh before each run."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(monkeypatch, str(app), Preconditions(erase=False))  # reinstall=clean
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "uninstall" in verbs and "install" in verbs
    assert verbs.index("uninstall") < verbs.index("install")  # remove, then install fresh
    assert ["xcrun", "simctl", "install", "UDID-1", str(app)] in calls


def test_launch_driver_reinstall_overwrite_installs_without_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`reinstall: overwrite` installs over the existing app (no uninstall, keeps data)."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(
        monkeypatch, str(app), Preconditions(erase=False, reinstall="overwrite")
    )
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "install" in verbs and "uninstall" not in verbs


def test_launch_driver_erase_skips_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `erase` already wiped the app, so `clean` skips the redundant uninstall and installs."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(monkeypatch, str(app), Preconditions(erase=True))  # reinstall=clean
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "erase" in verbs and "install" in verbs and "uninstall" not in verbs


def test_launch_driver_errors_on_missing_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured appPath that doesn't exist fails with a clear, actionable DeviceError."""
    eff = replace(_eff(), app_path="/nope/X.app")
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.make_driver", lambda actuator, udid: FakeDriver([])
    )
    with pytest.raises(simctl.DeviceError) as excinfo:
        launch_driver("UDID-1", eff, "idb", Preconditions(erase=False), env_run=_recording_run([]))
    assert "appPath not found" in str(excinfo.value)


def test_launch_driver_surfaces_failing_erase_as_device_error() -> None:
    """A simctl failure becomes a clean DeviceError (exit 2 at the CLI), not a traceback."""

    def fake_run(args: list[str], extra_env: object = None) -> str:
        if args[:3] == ["xcrun", "simctl", "erase"]:
            raise subprocess.CalledProcessError(
                149,
                args,
                output="",
                stderr="Unable to erase contents and settings in current state: Booted",
            )
        return ""

    with pytest.raises(simctl.DeviceError) as excinfo:
        launch_driver("UDID-1", _eff(), "idb", Preconditions(erase=True), env_run=fake_run)
    msg = str(excinfo.value)
    assert "exit 149" in msg
    assert "Booted" in msg  # simctl's actionable stderr is carried through


def test_await_ready_uses_exponential_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """_await_ready should use exponential backoff rather than a fixed poll interval,
    so early polls are short and later polls grow up to a cap."""
    sleeps: list[float] = []
    clock = 0.0

    def fake_sleep(s: float) -> None:
        nonlocal clock
        sleeps.append(s)
        clock += s

    monkeypatch.setattr("bajutsu.platform_lifecycle.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.platform_lifecycle.time.monotonic", lambda: clock)

    query_count = 0

    class SlowStartDriver:
        name = "slow"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            query_count += 1
            if query_count >= 6:
                return [_el("a", "A"), _el("b", "B")]
            return [_el("a", "A")]  # only 1 element — not ready

    _await_ready(SlowStartDriver())  # type: ignore[arg-type]

    # Sleep intervals should increase (exponential backoff).
    assert len(sleeps) >= 3
    assert sleeps[1] > sleeps[0], f"expected increasing intervals, got {sleeps}"
    # All intervals should be capped at a reasonable maximum.
    assert all(s <= 1.0 for s in sleeps), f"sleep exceeded cap: {sleeps}"


def test_await_ready_returns_immediately_when_already_ready() -> None:
    """If the app is already rendered (>=2 elements), no sleep at all."""
    sleeps: list[float] = []

    class ReadyDriver:
        name = "ready"

        def query(self) -> list[base.Element]:
            return [_el("a", "A"), _el("b", "B")]

    import bajutsu.platform_lifecycle as launch_mod

    orig_sleep = launch_mod.time.sleep
    launch_mod.time.sleep = lambda s: sleeps.append(s)
    try:
        _await_ready(ReadyDriver())  # type: ignore[arg-type]
    finally:
        launch_mod.time.sleep = orig_sleep

    assert sleeps == []


def test_await_ready_respects_timeout_on_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Total sleep time must not exceed the timeout."""
    sleeps: list[float] = []
    clock = 0.0

    def fake_sleep(s: float) -> None:
        nonlocal clock
        sleeps.append(s)
        clock += s

    monkeypatch.setattr("bajutsu.platform_lifecycle.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.platform_lifecycle.time.monotonic", lambda: clock)

    class NeverReadyDriver:
        name = "never"

        def query(self) -> list[base.Element]:
            return [_el("a", "A")]

    _await_ready(NeverReadyDriver(), timeout=1.0)  # type: ignore[arg-type]

    total_slept = sum(sleeps)
    assert total_slept <= 1.0, f"slept {total_slept}s which exceeds timeout 1.0s"


def test_await_ready_caps_poll_init_to_poll_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """When poll_init > poll_max, the first sleep should still respect poll_max."""
    sleeps: list[float] = []
    clock = 0.0

    def fake_sleep(s: float) -> None:
        nonlocal clock
        sleeps.append(s)
        clock += s

    monkeypatch.setattr("bajutsu.platform_lifecycle.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.platform_lifecycle.time.monotonic", lambda: clock)

    query_count = 0

    class SlowDriver:
        name = "slow"

        def query(self) -> list[base.Element]:
            nonlocal query_count
            query_count += 1
            if query_count >= 3:
                return [_el("a", "A"), _el("b", "B")]
            return [_el("a", "A")]

    _await_ready(SlowDriver(), poll_init=2.0, poll_max=0.3)  # type: ignore[arg-type]

    assert all(s <= 0.3 for s in sleeps), f"sleep exceeded poll_max: {sleeps}"


class _ScriptedDriver:
    """Returns a scripted sequence of trees on successive query() calls (last repeats)."""

    name = "scripted"

    def __init__(self, trees: list[list[base.Element]]) -> None:
        self._trees = trees
        self.calls = 0

    def query(self) -> list[base.Element]:
        tree = self._trees[min(self.calls, len(self._trees) - 1)]
        self.calls += 1
        return tree


def _install_bounded_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # Advance a local clock from the fake sleep so the loop is bounded by _await_ready's timeout —
    # a regression that never reaches readiness exits at the deadline (fails) instead of hanging.
    clock = 0.0

    def fake_sleep(s: float) -> None:
        nonlocal clock
        clock += s

    monkeypatch.setattr("bajutsu.platform_lifecycle.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.platform_lifecycle.time.monotonic", lambda: clock)


def test_await_ready_waits_for_ready_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    # With a ready selector, the gate must wait for that element — not return on the chrome that is
    # already present (the smoke flake: an onboarding modal over always-present Home chrome).
    _install_bounded_clock(monkeypatch)
    chrome = [_el("home.title", "H"), _el("tab", "T")]  # 2 elements -> the old gate would return
    target = _el("onboarding.start", "Start")
    driver = _ScriptedDriver([chrome, chrome, [*chrome, target]])  # modal appears on the 3rd read
    _await_ready(driver, ready_sel={"id": "onboarding.start"})  # type: ignore[arg-type]
    assert driver.calls >= 3  # did not settle on chrome-only; waited for the modal element


def test_await_ready_without_selector_returns_on_element_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No ready selector: the existing "any 2 elements" heuristic still applies (unchanged default).
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("home.title", "H"), _el("tab", "T")]])
    _await_ready(driver)  # type: ignore[arg-type]
    assert driver.calls == 1


def test_await_ready_empty_selector_falls_back_to_count(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty `readyWhen: {}` must not weaken the gate to "any 1 element" (an empty selector matches
    # everything); it falls back to the 2+ count heuristic.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("only", "O")], [_el("only", "O"), _el("second", "S")]])
    _await_ready(driver, ready_sel={})  # type: ignore[arg-type]
    assert driver.calls >= 2  # did not return on the single-element tree


def test_await_ready_positional_only_selector_falls_back_to_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A positional-only selector (`index`) matches every element via find_all, so it must not declare
    # readiness on a single element — it falls back to the 2+ count heuristic.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("only", "O")], [_el("only", "O"), _el("second", "S")]])
    _await_ready(driver, ready_sel={"index": 0})  # type: ignore[arg-type]
    assert driver.calls >= 2  # ignored the index-only selector; waited for 2+ elements
