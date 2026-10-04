"""Tests for running `installApp` and `setPrimaryTarget` through the pipeline (BE-0447, unit 3).

The update journey end to end on the fake path: start on the old build, install the new one over
it, move the primary, bring the new build to the front, and keep asserting on it. Each target's
lease records what happens to the shared device, so the order of installs, launches, retirements,
and releases is checkable without a Simulator.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from _runner import _eff, _el

from bajutsu.common import backends
from bajutsu.common.config import Effective, IosConfig, require_ios
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import Artifact, FileSink, NullSink
from bajutsu.common.orchestrator import DeviceControl
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import LeaseFn, TargetPool
from bajutsu.common.scenario import Scenario


@pytest.fixture(autouse=True)
def _fake_foregrounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake driver advertises no app-lifecycle control (its environment's control is simctl's),
    # so these runs grant it here: each lease below carries its own in-memory `foreground` double.
    def caps(actuator: str, eff: Effective, udid: str = "booted") -> frozenset[str]:
        return backends.capabilities_for_run(actuator, eff, udid) | {base.Capability.DC_FOREGROUND}

    monkeypatch.setattr("bajutsu.common.runner.pipeline.capabilities_for_run", caps)


_SCREEN = [
    _el("ok", "OK", ["button"]),
    _el("popup", "Popup"),
    _el("dismiss", "Dismiss"),
    _el("row", "Row 1"),
    _el("row", "Row 2"),
]


class _Foreground:
    """A `DeviceControl` double: `foreground()` is the only operation these runs use."""

    def foreground(self) -> None:
        return None


class _Device:
    """One shared device whose leases record installs, launches, and releases in order."""

    def __init__(self, bundles: dict[str, str]) -> None:
        self.log: list[str] = []
        self.drivers: dict[str, FakeDriver] = {}
        self._names = {bundle: name for name, bundle in bundles.items()}

    def pool(self, name: str) -> LeaseFn:
        def lease(eff: Effective, scenario: Scenario) -> Lease:
            self.log.append(f"lease {name}")
            return self._lease(name, joinable=True)

        return lease

    def _lease(self, name: str, *, joinable: bool) -> Lease:
        def join(eff: Effective, scenario: Scenario, fresh: bool) -> Lease:
            member = self._names[require_ios(eff).bundle_id]
            self.log.append(f"{'join' if fresh else 'launch'} {member}")
            return self._lease(member, joinable=False)

        def install(eff: Effective, keep_data: bool) -> None:
            member = self._names[require_ios(eff).bundle_id]
            self.log.append(f"install {member} keepData={keep_data}")

        driver = FakeDriver(list(_SCREEN))
        self.drivers[name] = driver
        return Lease(
            driver=driver,
            sink=NullSink(),
            relaunch=None,
            control=cast(DeviceControl, _Foreground()),
            collector=None,
            release=lambda: self.log.append(f"release {name}"),
            join=join if joinable else None,
            install=install,
        )


def _app_eff(bundle: str) -> Effective:
    return replace(_eff(), platform_config=IosConfig(bundle_id=bundle))


_SAME_APP = {"old": "com.example.showcase", "new": "com.example.showcase"}


def _run(device: _Device, bundles: dict[str, str], data: dict[str, object]) -> list[object]:
    targets = {n: TargetPool(_app_eff(b), device.pool(n), "fake") for n, b in bundles.items()}
    scenario = Scenario.model_validate({"name": "update", **data})
    return list(run_all(_eff(), [scenario], device.pool("unused"), targets=targets))


def test_the_update_journey_runs_on_one_device() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {"tap": {"id": "ok"}},
                {"installApp": {"from": "new"}},
                {"setPrimaryTarget": {"target": "new"}},
                {"foreground": {}},
                {"tap": {"id": "ok"}},
            ],
            "expect": [{"exists": {"id": "ok"}}],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert device.log == [
        "lease old",
        "install new keepData=True",
        "launch new",
        "release new",
        "release old",
    ]
    assert ("tap", {"id": "ok"}) in device.drivers["new"].actions


def test_a_step_on_the_retired_build_fails_by_name() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {"installApp": {"from": "new", "keepData": False}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "target 'old' is retired" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert "install new keepData=False" in device.log


def test_a_companion_with_its_own_identifier_retires_nothing() -> None:
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _Device(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "steps": [
                {"installApp": {"from": "auth"}},
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "ok"}},
                {"foreground": {}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]


def test_an_install_running_a_second_time_fails_by_name() -> None:
    # Two matches, so the loop body's install runs twice; the scenario model cannot see that.
    results = _run(
        _Device(_SAME_APP),
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {
                    "forEach": {
                        "sel": {"id": "row"},
                        "as": "x",
                        "steps": [{"target": "new", "installApp": {"from": "new"}}],
                    }
                }
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "it was already installed in this scenario" in (results[0].failure or "")  # type: ignore[attr-defined]


def test_an_omitted_target_interrupt_follows_a_moved_primary() -> None:
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _Device(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "interrupts": [
                {"condition": {"exists": {"id": "popup"}}, "steps": [{"tap": {"id": "dismiss"}}]}
            ],
            "steps": [
                {"setPrimaryTarget": {"target": "auth"}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert ("tap", {"id": "dismiss"}) in device.drivers["auth"].actions
    assert ("tap", {"id": "dismiss"}) not in device.drivers["app"].actions


def test_an_install_the_backend_refuses_fails_its_step() -> None:
    device = _Device(_SAME_APP)

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        held = device.pool("old")(eff, scenario)

        def refuse(eff: Effective, keep_data: bool) -> None:
            raise RuntimeError("the device refused the build")

        return replace(held, install=refuse)

    targets = {n: TargetPool(_app_eff(b), lease, "fake") for n, b in _SAME_APP.items()}
    scenario = Scenario.model_validate(
        {
            "name": "update",
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [{"installApp": {"from": "new"}}],
        }
    )
    results = run_all(_eff(), [scenario], lease, targets=targets)
    assert not results[0].ok
    assert "installApp from 'new': the device refused the build" in (results[0].failure or "")


def test_an_expect_entry_on_the_retired_build_fails_by_name() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [{"installApp": {"from": "new"}}],
            "expect": [{"exists": {"id": "ok"}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "expect: target 'old' is retired" in (results[0].failure or "")  # type: ignore[attr-defined]


class _Backgrounded(FakeDriver):
    """A member's driver whose app is behind another member's until its `foreground` runs."""

    in_front = False

    def query(self) -> list[base.Element]:
        if not self.in_front:
            raise base.AppNotInFront("auth is not in front")
        return super().query()

    def tap(self, sel: base.Selector) -> None:
        # A real driver resolves the selector through the same read, so it fails the same way.
        self.query()
        super().tap(sel)


class _BringsUp:
    """A `DeviceControl` double whose `foreground()` brings *driver*'s app to the front."""

    def __init__(self, driver: _Backgrounded) -> None:
        self._driver = driver

    def foreground(self) -> None:
        self._driver.in_front = True


class _CompanionDevice(_Device):
    """The companion's lease answers with a backgrounded driver until its `foreground`.

    With *run_dir*, every lease writes real evidence, so each step reads its tree afterwards.
    """

    def __init__(self, bundles: dict[str, str], run_dir: Path | None = None) -> None:
        super().__init__(bundles)
        self._run_dir = run_dir

    def _lease(self, name: str, *, joinable: bool) -> Lease:
        lease = super()._lease(name, joinable=joinable)
        if self._run_dir is not None:
            lease = replace(lease, sink=FileSink(self._run_dir / name))
        if name != "auth":
            return lease
        driver = _Backgrounded(list(_SCREEN))
        self.drivers[name] = driver
        return replace(lease, driver=driver, control=cast(DeviceControl, _BringsUp(driver)))


def test_a_foreground_to_a_backgrounded_member_passes_its_interrupt_guard() -> None:
    # The guard reads the step's own app before acting, and before a `foreground` that app is behind
    # another member's: the read has nothing to clear, so it must not fail the step that brings the
    # app up (an Android target's config-level ANR `interrupts` hit this on every hop).
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "interrupts": [
                {
                    "target": "auth",
                    "condition": {"exists": {"id": "popup"}},
                    "steps": [{"tap": {"id": "dismiss"}}],
                }
            ],
            "steps": [
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert ("tap", {"id": "ok"}) in device.drivers["auth"].actions


def test_any_other_step_to_a_backgrounded_member_still_fails() -> None:
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    results = _run(
        _CompanionDevice(bundles),
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "interrupts": [
                {
                    "target": "auth",
                    "condition": {"exists": {"id": "popup"}},
                    "steps": [{"tap": {"id": "dismiss"}}],
                }
            ],
            "steps": [{"target": "auth", "tap": {"id": "ok"}}],
        },
    )
    # A step failure naming the cause, not an escape that aborts the run.
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]


def test_a_step_that_acts_without_reading_fails_on_a_backgrounded_member() -> None:
    # A `tapPoint` resolves nothing, so it never reads its own screen: without the guard's pre-act
    # read failing it by name, it would tap whatever the member in front shows and pass.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "interrupts": [
                {
                    "target": "auth",
                    "condition": {"exists": {"id": "popup"}},
                    "steps": [{"tap": {"id": "dismiss"}}],
                }
            ],
            "steps": [{"target": "auth", "tapPoint": {"x": 0.5, "y": 0.5}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert not any(a[0] == "tap_point" for a in device.drivers["auth"].actions)


def test_a_step_that_passes_without_its_app_in_front_fails_on_its_evidence_read(
    tmp_path: Path,
) -> None:
    # No `interrupts`, so nothing reads before the `tapPoint` acts; the post-step evidence read is
    # the first to see another app in front, and it fails the step rather than aborting the run.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles, tmp_path)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"target": "auth", "tapPoint": {"x": 0.5, "y": 0.5}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]


def test_a_foreground_hop_passes_under_a_screen_changed_policy(tmp_path: Path) -> None:
    # A `screenChanged` policy reads a fresh `before` on a target switch; for the `foreground` that
    # brings a backgrounded member up there is no screen yet, and that is not a failure.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles, tmp_path)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            "steps": [
                {"tap": {"id": "ok"}},
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert ("tap", {"id": "ok"}) in device.drivers["auth"].actions


def test_a_non_foreground_hop_to_a_backgrounded_member_fails_under_a_screen_changed_policy(
    tmp_path: Path,
) -> None:
    # The `screenChanged` baseline read on a target switch is the first to see another app in
    # front: it fails the step by name without acting, never escaping the run.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles, tmp_path)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            "steps": [{"tap": {"id": "ok"}}, {"target": "auth", "tap": {"id": "ok"}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert ("tap", {"id": "ok"}) not in device.drivers["auth"].actions


def test_a_failed_step_on_a_backgrounded_member_writes_no_other_apps_tree(tmp_path: Path) -> None:
    # The evidence read after a step that already failed keeps the step's own reason and writes no
    # `elements.json`: the tree on screen belongs to another member, not to this step.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    results = _run(
        _CompanionDevice(bundles, tmp_path),
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"target": "auth", "tap": {"id": "ok"}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert not list((tmp_path / "auth").rglob("elements.json"))


class _Homes(_BringsUp):
    """`foreground()` brings the member's app up, and `home()` sends it away again."""

    def __init__(self, driver: _Backgrounded) -> None:
        super().__init__(driver)
        self._member = driver

    def home(self) -> None:
        self._member.in_front = False


class _HomingDevice(_CompanionDevice):
    def _lease(self, name: str, *, joinable: bool) -> Lease:
        lease = super()._lease(name, joinable=joinable)
        if name != "auth":
            return lease
        driver = cast(_Backgrounded, lease.driver)
        return replace(lease, control=cast(DeviceControl, _Homes(driver)))


def test_a_background_step_passes_though_its_evidence_read_finds_another_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `background` sends its own app away on purpose, so the evidence read that then finds another
    # app in front is the step's expected outcome, not a failure.
    def caps(actuator: str, eff: Effective, udid: str = "booted") -> frozenset[str]:
        return backends.capabilities_for_run(actuator, eff, udid) | {
            base.Capability.DC_FOREGROUND,
            base.Capability.DC_BACKGROUND,
        }

    monkeypatch.setattr("bajutsu.common.runner.pipeline.capabilities_for_run", caps)
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    results = _run(
        _HomingDevice(bundles, tmp_path),
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"target": "auth", "foreground": {}}, {"target": "auth", "background": {}}],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]


@pytest.mark.parametrize("with_files", [False, True], ids=["null-sink", "file-sink"])
def test_a_tap_point_on_a_backgrounded_member_fails_without_tapping(
    tmp_path: Path, with_files: bool
) -> None:
    # No `interrupts` and no capture policy: the member's own pre-act read is all that stands
    # between a `tapPoint` and the other app's screen, under a sink that reads nothing afterwards too.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _CompanionDevice(bundles, tmp_path if with_files else None)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"target": "auth", "tapPoint": {"x": 0.5, "y": 0.5}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert not any(a[0] == "tap_point" for a in device.drivers["auth"].actions)


class _Counting(FakeDriver):
    reads = 0

    def query(self) -> list[base.Element]:
        self.reads += 1
        return super().query()


class _CountingDevice(_Device):
    def _lease(self, name: str, *, joinable: bool) -> Lease:
        lease = super()._lease(name, joinable=joinable)
        driver = _Counting(list(_SCREEN))
        self.drivers[name] = driver
        return replace(lease, driver=driver)


def test_a_target_alone_on_its_device_pays_no_pre_act_read() -> None:
    # The front check is for a shared device only: a single target's `tapPoint` reads nothing.
    bundles = {"app": "com.example.app"}
    device = _CountingDevice(bundles)
    results = _run(
        device, bundles, {"targets": ["app"], "steps": [{"tapPoint": {"x": 0.5, "y": 0.5}}]}
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert cast(_Counting, device.drivers["app"]).reads == 0


_AWAY_SCREEN = [*_SCREEN, _el("away", "Away", ["button"])]


class _LeavesOnTap(_Backgrounded):
    """Tapping `away` hands the screen to another app: at once, or only after the next read."""

    def __init__(self, *, after_read: bool) -> None:
        super().__init__(list(_AWAY_SCREEN))
        self._after_read = after_read
        self._leaving = False

    def tap(self, sel: base.Selector) -> None:
        super().tap(sel)
        if self.actions[-1] == ("tap", {"id": "away"}):
            if self._after_read:
                self._leaving = True
            else:
                self.in_front = False

    def query(self) -> list[base.Element]:
        tree = super().query()
        if self._leaving:
            self._leaving, self.in_front = False, False
        return tree


class _RecordingSink(FileSink):
    """A `FileSink` that also keeps the capture kinds each call asked for."""

    def __init__(self, run_dir: Path) -> None:
        super().__init__(run_dir)
        self.calls: list[tuple[str, list[str]]] = []

    def capture(
        self, driver: base.Driver, step_id: str, kinds: list[str], **kwargs: object
    ) -> list[Artifact]:
        self.calls.append((step_id, list(kinds)))
        return super().capture(driver, step_id, kinds, **kwargs)  # type: ignore[arg-type]


class _LeavingDevice(_CompanionDevice):
    def __init__(self, bundles: dict[str, str], run_dir: Path, *, after_read: bool) -> None:
        super().__init__(bundles)
        self._after_read = after_read
        self.sink = _RecordingSink(run_dir)

    def _lease(self, name: str, *, joinable: bool) -> Lease:
        lease = super()._lease(name, joinable=joinable)
        if name != "auth":
            return lease
        driver = _LeavesOnTap(after_read=self._after_read)
        self.drivers[name] = driver
        return replace(
            lease, driver=driver, sink=self.sink, control=cast(DeviceControl, _BringsUp(driver))
        )


def test_a_verdict_flipped_by_the_evidence_read_fires_error_captures(tmp_path: Path) -> None:
    # The tap passes, then the evidence read finds another app in front and fails it: the capture
    # set is chosen again for that failure, and the other app's tree is written in no form.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _LeavingDevice(bundles, tmp_path, after_read=False)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "capturePolicy": [{"on": {"result": "error"}, "capture": ["actionLog", "rawTree"]}],
            "steps": [
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "away"}},
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]
    kinds = [k for step_id, k in device.sink.calls if step_id.endswith("step1")][-1]
    assert "actionLog" in kinds
    assert "rawTree" not in kinds
    assert "elements" not in kinds


def test_a_guarded_step_after_its_app_left_fails_by_name(tmp_path: Path) -> None:
    # The previous step's tree is carried over as `before`, then the guard's own read finds another
    # app in front: the step fails by name, and the `screenChanged` comparison never re-reads.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _LeavingDevice(bundles, tmp_path, after_read=True)
    device.sink = cast(_RecordingSink, NullSink())
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            "interrupts": [
                {
                    "target": "auth",
                    "condition": {"exists": {"id": "popup"}},
                    "steps": [{"tap": {"id": "dismiss"}}],
                }
            ],
            "steps": [
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "away"}},
                {"target": "auth", "assert": [{"exists": {"id": "ok"}}]},
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "(assert_): auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]


@pytest.mark.parametrize("extract", [False, True], ids=["plain", "with-extract"])
def test_a_step_that_hands_the_screen_away_fails_on_its_screen_changed_read(
    tmp_path: Path, extract: bool
) -> None:
    # The `screenChanged` comparison is the first read to find another app in front; it fails the
    # step there, so a later `extract` never re-reads that screen outside the step and aborts the run.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    tap: dict[str, object] = {"target": "auth", "tap": {"id": "away"}}
    if extract:
        tap["extract"] = {"x": {"sel": {"id": "ok"}, "prop": "label"}}
    results = _run(
        _LeavingDevice(bundles, tmp_path, after_read=False),
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            "steps": [
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "ok"}},
                tap,
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "(tap): auth is not in front" in (results[0].failure or "")  # type: ignore[attr-defined]


def test_a_step_that_drives_no_screen_keeps_its_verdict_on_a_backgrounded_member(
    tmp_path: Path,
) -> None:
    # The evidence read still finds another app in front, but a step that aims at no screen lost
    # only a tree it never needed: it keeps its verdict, and no other app's tree is written.
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    results = _run(
        _CompanionDevice(bundles, tmp_path),
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [
                {"target": "auth", "sleep": {"seconds": 0.01, "reason": "aims at no screen"}}
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert not list((tmp_path / "auth").rglob("elements.json"))
