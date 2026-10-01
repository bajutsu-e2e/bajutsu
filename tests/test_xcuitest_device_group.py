"""Tests for the iOS side of a device group (BE-0447, unit 5).

The members of a device group share the lease's one XCUITest runner: each member's driver retargets
the runner's base app to its own bundle (`/app/target`, which activates nothing) only when another
member was the last to address it, and every read first checks that its own app is the one in front
(`/app/state`), raising `AppNotInFront` rather than reading another member's tree. The environment
installs, launches, and brings members forward through simctl, scripted here, so none of this needs
a Simulator.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common import backends
from bajutsu.common.backend_cli import simctl
from bajutsu.common.config import Effective, IosConfig, XcuitestConfig
from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import RunnerTarget, XcuitestDriver
from bajutsu.common.drivers.xcuitest._reply import _Reply
from bajutsu.common.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.common.platform_lifecycle.protocols import ReadinessResult
from bajutsu.common.scenario import Preconditions, Redact

_UDID = "UDID-1"
_APP = "com.example.app"
_AUTH = "com.example.auth"


def _eff(bundle: str, app_path: str | None = None, **xcuitest: Any) -> Effective:
    return Effective(
        target=bundle,
        platform_config=IosConfig(
            bundle_id=bundle,
            app_path=app_path,
            xcuitest=XcuitestConfig(**xcuitest) if xcuitest else None,
        ),
        backend=["xcuitest"],
        device="iPhone 15",
        locale="en_US",
        launch_env={"FROM_CONFIG": "1"},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


class _Runner:
    """A scripted runner: which app is in front, and every request in order."""

    def __init__(self, front: str = _APP) -> None:
        self.front = front
        self.base = _APP
        self.calls: list[tuple[str, str]] = []
        self.running = {_APP, _AUTH}

    def transport(self, method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        self.calls.append((method, path if path != "/app/target" else f"/app/target {body}"))
        if path == "/app/target":
            assert body is not None
            self.base = str(body["bundleId"])
            return _Reply(status="ok")
        if path == "/app/state":
            if self.base not in self.running:
                return _Reply(status="ok", app_state="notRunning")
            state = "runningForeground" if self.base == self.front else "runningBackground"
            return _Reply(status="ok", app_state=state)
        if path == "/elements":
            return _Reply(status="ok", elements=[_el("ok")])
        return _Reply(status="ok")


def _el(identifier: str) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "label": identifier,
        "value": None,
        "traits": [],
        "frame": [0.0, 0.0, 10.0, 10.0],
        "handle": identifier,
    }


def _member(runner: _Runner, bundle: str, target: RunnerTarget) -> XcuitestDriver:
    driver = XcuitestDriver(transport=runner.transport)
    driver.bind_to_member(bundle, target)
    return driver


# --- the shared runner ---------------------------------------------------------------------------


def test_a_member_retargets_the_runner_only_when_another_member_addressed_it() -> None:
    runner = _Runner(front=_AUTH)
    target = RunnerTarget(current=_APP)
    auth = _member(runner, _AUTH, target)
    auth.query()
    auth.query()
    retargets = [c for c in runner.calls if c[1].startswith("/app/target")]
    assert retargets == [("POST", "/app/target {'bundleId': 'com.example.auth'}")]
    assert target.current == _AUTH


def test_a_member_in_front_reads_its_own_tree() -> None:
    runner = _Runner(front=_AUTH)
    elements = _member(runner, _AUTH, RunnerTarget(current=_APP)).query()
    assert [e["identifier"] for e in elements] == ["ok"]


def test_a_member_in_the_background_refuses_to_read_by_name() -> None:
    runner = _Runner(front=_APP)
    auth = _member(runner, _AUTH, RunnerTarget(current=_APP))
    with pytest.raises(base.AppNotInFront, match=rf"{_AUTH} is not in front"):
        auth.query()
    assert ("GET", "/elements") not in runner.calls  # never read another app's tree


def test_a_member_in_the_background_is_never_read_as_untappable() -> None:
    # `is_tappable` folds a missing element into False; another app's screen must fail by name
    # instead, or a scroll's stop condition would scroll through the app now in front.
    runner = _Runner(front=_APP)
    auth = _member(runner, _AUTH, RunnerTarget(current=_APP))
    with pytest.raises(base.AppNotInFront):
        auth.is_tappable({"id": "ok"})


def test_a_lone_driver_reads_without_the_state_check() -> None:
    runner = _Runner(front=_AUTH)
    XcuitestDriver(transport=runner.transport).query()
    assert ("POST", "/app/state") not in runner.calls


def test_retarget_points_the_runner_back() -> None:
    runner = _Runner()
    runner.base = _AUTH
    XcuitestDriver(transport=runner.transport).retarget(_APP)
    assert runner.base == _APP


def test_a_refused_retarget_fails_loudly() -> None:
    from bajutsu.common.drivers.xcuitest import XcuitestChannelError

    driver = XcuitestDriver(transport=lambda m, p, b: _Reply(status="error"))
    with pytest.raises(XcuitestChannelError, match="could not retarget"):
        driver.retarget(_APP)


def test_the_simulator_advertises_device_groups_and_a_real_device_does_not() -> None:
    assert base.Capability.DEVICE_GROUP in XcuitestDriver.CAPABILITIES
    real = _eff(_APP, deviceType="device")
    assert base.Capability.DEVICE_GROUP not in backends.capabilities_for_run("xcuitest", real)
    assert base.Capability.DEVICE_GROUP in backends.capabilities_for_run("xcuitest", _eff(_APP))


# --- the environment -----------------------------------------------------------------------------


class _Simctl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], env: Mapping[str, str] | None = None) -> str:
        self.calls.append(list(args) + sorted(f"{k}={v}" for k, v in (env or {}).items()))
        return ""

    def ran(self, *words: str) -> bool:
        return any(all(w in argv for w in words) for argv in self.calls)


def _started(run: _Simctl, runner: _Runner, monkeypatch: pytest.MonkeyPatch) -> XcuitestEnvironment:
    """An environment as if `start` had brought the lease's own app up on its runner."""
    env = XcuitestEnvironment("xcuitest", _UDID, run)
    env._bundle_id = _APP
    env._lease_driver = XcuitestDriver(transport=runner.transport)
    env._launch_inputs = (Preconditions(), {"BAJUTSU_COLLECTOR": "http://x"})
    monkeypatch.setattr(
        backends, "make_driver", lambda *a, **k: XcuitestDriver(transport=runner.transport)
    )
    return env


def test_a_starting_member_installs_launches_and_joins_the_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Auth.app"
    app.mkdir()
    run, runner = _Simctl(), _Runner(front=_AUTH)
    env = _started(run, runner, monkeypatch)
    member = env.start_member(_eff(_AUTH, str(app)), Preconditions(reinstall="overwrite"))
    assert run.ran("install", str(app))
    assert not run.ran("uninstall", _AUTH)
    assert run.ran("launch", _AUTH, "SIMCTL_CHILD_FROM_CONFIG=1")
    # Both the member and the lease's own driver now check that their app is in front.
    member.query()
    lease_driver = env._lease_driver
    assert isinstance(lease_driver, XcuitestDriver)
    with pytest.raises(base.AppNotInFront):
        lease_driver.query()


def test_a_clean_member_reinstalls_and_resets_its_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "Auth.app"
    app.mkdir()
    run = _Simctl()
    env = _started(run, _Runner(), monkeypatch)
    env.start_member(_eff(_AUTH, str(app)), Preconditions(reinstall="clean"))
    assert run.ran("uninstall", _AUTH)
    assert run.ran("privacy", _AUTH)


def test_a_later_member_starts_launch_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "Auth.app"
    app.mkdir()
    run = _Simctl()
    env = _started(run, _Runner(), monkeypatch)
    env.start_member(_eff(_AUTH, str(app)), Preconditions(), install=False)
    assert not run.ran("install", str(app))
    assert run.ran("launch", _AUTH)


def test_a_member_needs_a_simulator_this_environment_started() -> None:
    with pytest.raises(base.UnsupportedAction, match="need a Simulator"):
        XcuitestEnvironment("xcuitest", _UDID, _Simctl()).start_member(_eff(_AUTH), Preconditions())


def test_install_app_terminates_installs_over_and_resets_the_digest(tmp_path: Path) -> None:
    app = tmp_path / "New.app"
    app.mkdir()
    run = _Simctl()
    env = XcuitestEnvironment("xcuitest", _UDID, run)
    env._installed_app_digest = "abc"
    env.install_member(_eff(_APP, str(app)), keep_data=True)
    assert run.ran("terminate", _APP)
    assert run.ran("install", str(app))
    assert not run.ran("uninstall", _APP)
    assert env._installed_app_digest is None
    env.install_member(_eff(_APP, str(app)), keep_data=False)
    assert run.ran("uninstall", _APP)


def test_install_app_refuses_a_missing_build(tmp_path: Path) -> None:
    from bajutsu.common.backend_cli import simctl

    env = XcuitestEnvironment("xcuitest", _UDID, _Simctl())
    with pytest.raises(simctl.DeviceError, match="appPath not found"):
        env.install_member(_eff(_APP, str(tmp_path / "Gone.app")), keep_data=True)


def test_ending_a_member_keeps_a_namesakes_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    run, runner = _Simctl(), _Runner()
    env = _started(run, runner, monkeypatch)
    member = env.start_member(_eff(_AUTH), Preconditions(), install=False)
    env.end_member(env._lease_driver, _eff(_AUTH))  # type: ignore[arg-type]
    assert env._member_drivers[_AUTH] is member
    env.end_member(member, _eff(_AUTH))
    assert _AUTH not in env._member_drivers
    assert run.ran("terminate", _AUTH)


# --- foreground ----------------------------------------------------------------------------------


def _ready(monkeypatch: pytest.MonkeyPatch, ready: bool) -> None:
    monkeypatch.setattr(
        "bajutsu.common.platform_lifecycle.readiness.await_ready",
        lambda *a, **k: ReadinessResult(ready, "count" if ready else "timeout", 0.0),
    )


def test_foreground_resumes_a_running_member_and_waits_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, runner = _Simctl(), _Runner(front=_AUTH)
    env = _started(run, runner, monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    run.calls.clear()
    _ready(monkeypatch, True)
    control = env.controller(_eff(_AUTH))
    assert control is not None
    control.foreground()
    launches = [c for c in run.calls if "launch" in c]
    assert launches and not any("SIMCTL_CHILD_FROM_CONFIG=1" in c for c in launches)


def test_foreground_launches_a_member_that_is_not_running_with_its_launch_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, runner = _Simctl(), _Runner(front=_AUTH)
    env = _started(run, runner, monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    runner.running.discard(_AUTH)
    run.calls.clear()
    _ready(monkeypatch, True)
    control = env.controller(_eff(_AUTH))
    assert control is not None
    control.foreground()
    assert run.ran("launch", _AUTH, "SIMCTL_CHILD_FROM_CONFIG=1")


def test_a_foreground_that_never_reaches_the_front_fails_its_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, runner = _Simctl(), _Runner()
    env = _started(run, runner, monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    _ready(monkeypatch, False)
    control = env.controller(_eff(_AUTH))
    assert control is not None
    with pytest.raises(base.AppNotInFront, match="did not come to the front"):
        control.foreground()


def test_a_lone_apps_foreground_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # No group on the device: `foreground` resumes as it always did, with no readiness wait.
    run = _Simctl()
    env = _started(run, _Runner(), monkeypatch)
    _ready(monkeypatch, False)  # would fail the step if the wait ran
    control = env.controller(_eff(_APP))
    assert control is not None
    control.foreground()
    assert run.ran("launch", _APP)


def test_a_warm_runner_a_group_retargeted_points_back_at_the_next_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _Runner()
    env = _started(_Simctl(), runner, monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    runner.front = _AUTH
    env._member_drivers[_AUTH].query()  # the runner now addresses the member
    assert runner.base == _AUTH
    fresh = XcuitestDriver(transport=runner.transport)
    monkeypatch.setattr(env, "_start", lambda *a, **k: fresh)
    env.start(_eff(_APP), Preconditions())
    assert runner.base == _APP
    assert env._member_drivers == {}  # the previous lease's group is gone


# --- the edges -----------------------------------------------------------------------------------


class _FailingSimctl(_Simctl):
    """A simctl that refuses one subcommand, the way a real device error surfaces."""

    def __init__(self, refuse: str) -> None:
        super().__init__()
        self.refuse = refuse

    def __call__(self, args: list[str], env: Mapping[str, str] | None = None) -> str:
        super().__call__(args, env)
        if self.refuse in args:
            raise subprocess.CalledProcessError(1, args)
        return ""


def test_a_member_gets_its_own_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _Simctl()
    env = _started(run, _Runner(), monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), permissions={"camera": "grant"}, install=False)
    assert run.ran("privacy", "grant", "camera", _AUTH)


def test_a_failed_member_launch_is_a_device_error(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _started(_FailingSimctl("launch"), _Runner(), monkeypatch)
    with pytest.raises(simctl.DeviceError):
        env.start_member(_eff(_AUTH), Preconditions(), install=False)


def test_a_member_with_a_missing_build_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _started(_Simctl(), _Runner(), monkeypatch)
    with pytest.raises(simctl.DeviceError, match="appPath not found"):
        env.start_member(_eff(_AUTH, str(tmp_path / "Gone.app")), Preconditions())


def test_a_member_with_no_build_configured_installs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _Simctl()
    env = _started(run, _Runner(), monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions())
    assert not run.ran("install")


def test_a_failed_install_is_a_device_error(tmp_path: Path) -> None:
    app = tmp_path / "New.app"
    app.mkdir()
    env = XcuitestEnvironment("xcuitest", _UDID, _FailingSimctl("install"))
    with pytest.raises(simctl.DeviceError):
        env.install_member(_eff(_APP, str(app)), keep_data=True)


def test_a_third_member_joins_the_same_group(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner()
    env = _started(_Simctl(), runner, monkeypatch)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    group = env._group
    env.start_member(_eff("com.example.third"), Preconditions(), install=False)
    assert env._group is group


def test_foreground_relaunches_the_leases_own_app_with_a_fresh_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, runner = _Simctl(), _Runner()
    env = _started(run, runner, monkeypatch)
    runner.running.discard(_APP)
    env._app_launched_at = 1.0
    _ready(monkeypatch, True)
    control = env.controller(_eff(_APP))
    assert control is not None
    control.foreground()
    assert run.ran("launch", _APP, "SIMCTL_CHILD_FROM_CONFIG=1")
    assert env._app_launched_at is not None and env._app_launched_at > 1.0


def test_a_failed_foreground_is_a_device_error(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _started(_FailingSimctl("launch"), _Runner(), monkeypatch)
    control = env.controller(_eff(_APP))
    assert control is not None
    with pytest.raises(simctl.DeviceError):
        control.foreground()


def test_a_members_relaunch_leaves_the_leases_marker_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _started(_Simctl(), _Runner(), monkeypatch)
    env._app_launched_at = 1.0
    monkeypatch.setattr(
        "bajutsu.common.platform_lifecycle.environments.ios._DeviceEnvironment.relauncher",
        lambda self, eff, scenario, driver, *, extra_env=None: lambda step: None,
    )
    from bajutsu.common.scenario import Relaunch, Scenario

    relaunch = env.relauncher(
        _eff(_AUTH),
        Scenario.model_validate({"name": "s", "steps": [{"tap": {"id": "x"}}]}),
        XcuitestDriver(transport=_Runner().transport),
    )
    relaunch(Relaunch())
    assert env._app_launched_at == 1.0


def test_a_driver_with_no_member_routes_is_left_alone() -> None:
    # A backend whose driver cannot retarget or report state (the fake) simply skips both.
    from bajutsu.common.drivers.fake import FakeDriver
    from bajutsu.common.platform_lifecycle.environments.xcuitest import xcuitest_environment as xe

    fake = FakeDriver([])
    xe._bind_member(fake, _APP, RunnerTarget(current=_APP))
    xe._retarget(fake, _APP)
    assert xe._app_state(fake) == "unknown"


def test_the_live_route_refuses_device_groups() -> None:
    from bajutsu.common.platform_lifecycle.environments.xcuitest_live import (
        XcuitestLiveEnvironment,
    )

    env = XcuitestLiveEnvironment("xcuitest", "https://appium.example/wd/hub")
    with pytest.raises(base.UnsupportedAction, match="device groups"):
        env.start_member(_eff(_AUTH), Preconditions())
    with pytest.raises(base.UnsupportedAction, match="installApp"):
        env.install_member(_eff(_AUTH), keep_data=True)


# --- the review round ----------------------------------------------------------------------------


def test_the_state_and_target_posts_retry_like_a_read() -> None:
    from bajutsu.common.drivers.xcuitest._functions import _is_retry_eligible

    assert _is_retry_eligible("POST", delivered=True, path="/app/state")
    assert _is_retry_eligible("POST", delivered=True, path="/app/target")
    assert not _is_retry_eligible("POST", delivered=True, path="/tap")


def test_a_health_probe_never_retargets_the_runner() -> None:
    runner = _Runner()
    driver = _member(runner, _AUTH, RunnerTarget(current=_APP))
    driver._transport("GET", "/health", None)
    assert not any(c[1].startswith("/app/target") for c in runner.calls)


def test_a_lone_apps_launch_waits_for_readiness_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, runner = _Simctl(), _Runner()
    env = _started(run, runner, monkeypatch)
    runner.running.discard(_APP)
    waited: list[bool] = []

    def wait(*a: object, **k: object) -> ReadinessResult:
        waited.append(True)
        return ReadinessResult(False, "timeout", 0.0)

    monkeypatch.setattr("bajutsu.common.platform_lifecycle.readiness.await_ready", wait)
    control = env.controller(_eff(_APP))
    assert control is not None
    control.foreground()  # waits like `relaunch`, but a lone app is never "not in front"
    assert waited == [True]
