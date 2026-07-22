"""Tests for device bring-up (launch_driver) and readiness polling (_await_ready).

The iOS bring-up drives XCUITest, the sole iOS backend (BE-0290): a run does
the simctl device prep (shutdown / erase / boot / install / permissions) and then launches the app
through the `xcodebuild` runner — there is no `simctl launch` verb. The
device tests mock the runner spawn so they exercise the simctl prep sequence off-device.
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
from _runner import _el, _ios_eff

from bajutsu import simctl
from bajutsu.config import XcuitestConfig, require_ios
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.runner import (
    _await_ready,
    launch_driver,
)
from bajutsu.scenario import Preconditions


def _recording_run(calls: list[list[str]]):
    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    return fake_run


def _xcuitest_eff(tmp_path: Path, **ios_kwargs: object) -> object:
    """An iOS `Effective` wired to a real (empty) `.xctestrun` so `start` gets past its runner check."""
    runner = tmp_path / "Runner.xctestrun"
    with runner.open("wb") as f:
        plistlib.dump({"__xctestrun_metadata__": {"FormatVersion": 1}, "T": {}}, f)
    return _ios_eff(xcuitest=XcuitestConfig(test_runner=str(runner)), **ios_kwargs)


def _mock_runner_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the `xcodebuild` runner spawn and its driver so a launch exercises only the simctl prep."""
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._allocate_port", lambda: 54321
    )

    class _FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            pass

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr("subprocess.Popen", _FakePopen)

    class _ReadyFake(FakeDriver):
        # The XCUITest lifecycle probes the runner via `await_ready` before returning; the fake has
        # no runner, so this is a no-op. `query()` still returns the ready screen for `_await_ready`.
        def await_ready(self, timeout: float = 0.0) -> None:
            return None

    ready = _ReadyFake([_el("home.title", "H"), _el("ok", "OK")])  # 2 elems -> ready immediately
    monkeypatch.setattr("bajutsu.backends.make_driver", lambda *a, **k: ready)


def _launch_recording(
    monkeypatch: pytest.MonkeyPatch, eff: object, pre: Preconditions, **kwargs: object
) -> list[list[str]]:
    _mock_runner_spawn(monkeypatch)
    calls: list[list[str]] = []
    launch_driver("UDID-1", eff, "xcuitest", pre, env_run=_recording_run(calls), **kwargs)
    return calls


def test_launch_driver_shuts_down_before_erase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """erase requires a shut-down device, so the sequence is shutdown -> erase -> boot."""
    calls = _launch_recording(monkeypatch, _xcuitest_eff(tmp_path), Preconditions(erase=True))
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert verbs.index("shutdown") < verbs.index("erase") < verbs.index("boot")


def test_launch_driver_applies_permissions_after_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BE-0276: `permissions` runs after boot (a fresh install/erase resets TCC grants) but before
    the runner launches the app, so a permission prompt never blocks the run."""
    calls = _launch_recording(
        monkeypatch,
        _xcuitest_eff(tmp_path),
        Preconditions(erase=False),
        permissions={"camera": "grant", "location": "revoke"},
    )
    bundle_id = require_ios(_ios_eff()).bundle_id
    assert ["xcrun", "simctl", "privacy", "UDID-1", "grant", "camera", bundle_id] in calls


def test_launch_driver_applies_no_permissions_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _launch_recording(monkeypatch, _xcuitest_eff(tmp_path), Preconditions(erase=False))
    assert not any(c[:3] == ["xcrun", "simctl", "privacy"] for c in calls)


def test_launch_driver_reinstall_clean_uninstalls_then_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default `reinstall: clean` removes the app then installs it fresh before each run."""
    app = tmp_path / "X.app"
    app.mkdir()
    eff = _xcuitest_eff(tmp_path, app_path=str(app))
    calls = _launch_recording(monkeypatch, eff, Preconditions(erase=False))  # reinstall=clean
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
    eff = _xcuitest_eff(tmp_path, app_path=str(app))
    calls = _launch_recording(monkeypatch, eff, Preconditions(erase=False, reinstall="overwrite"))
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "install" in verbs and "uninstall" not in verbs


def test_launch_driver_erase_skips_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `erase` already wiped the app, so `clean` skips the redundant uninstall and installs."""
    app = tmp_path / "X.app"
    app.mkdir()
    eff = _xcuitest_eff(tmp_path, app_path=str(app))
    calls = _launch_recording(monkeypatch, eff, Preconditions(erase=True))  # reinstall=clean
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "erase" in verbs and "install" in verbs and "uninstall" not in verbs


def test_launch_driver_errors_on_missing_app_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured appPath that doesn't exist fails with a clear, actionable DeviceError."""
    _mock_runner_spawn(monkeypatch)
    eff = _xcuitest_eff(tmp_path, app_path="/nope/X.app")
    with pytest.raises(simctl.DeviceError) as excinfo:
        launch_driver(
            "UDID-1", eff, "xcuitest", Preconditions(erase=False), env_run=_recording_run([])
        )
    assert "appPath not found" in str(excinfo.value)


def test_launch_driver_surfaces_failing_erase_as_device_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A simctl failure becomes a clean DeviceError (exit 2 at the CLI), not a traceback."""
    _mock_runner_spawn(monkeypatch)

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
        launch_driver(
            "UDID-1",
            _xcuitest_eff(tmp_path),
            "xcuitest",
            Preconditions(erase=True),
            env_run=fake_run,
        )
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

    monkeypatch.setattr("bajutsu.drivers.base.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.drivers.base.time.monotonic", lambda: clock)

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

    # The shared deadline loop (base.deadline_ticks) owns the sleep now (BE-0256); patch it there.
    from bajutsu.drivers import base as base_mod

    orig_sleep = base_mod.time.sleep
    base_mod.time.sleep = lambda s: sleeps.append(s)
    try:
        _await_ready(ReadyDriver())  # type: ignore[arg-type]
    finally:
        base_mod.time.sleep = orig_sleep

    assert sleeps == []


def test_await_ready_respects_timeout_on_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Total sleep time must not exceed the timeout."""
    sleeps: list[float] = []
    clock = 0.0

    def fake_sleep(s: float) -> None:
        nonlocal clock
        sleeps.append(s)
        clock += s

    monkeypatch.setattr("bajutsu.drivers.base.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.drivers.base.time.monotonic", lambda: clock)

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

    monkeypatch.setattr("bajutsu.drivers.base.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.drivers.base.time.monotonic", lambda: clock)

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

    monkeypatch.setattr("bajutsu.drivers.base.time.sleep", fake_sleep)
    monkeypatch.setattr("bajutsu.drivers.base.time.monotonic", lambda: clock)


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


def test_await_ready_ignores_off_namespace_home_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dominant smoke flake: on a slow cold boot the driver queries SpringBoard (the Home screen's app
    # icons) before the app foregrounds. Those are 2+ *off-namespace* elements, which the bare count
    # heuristic wrongly accepts as "ready" — so the first scenario step then races the real launch and
    # times out. With declared idNamespaces, readiness must wait for an element that belongs to the app.
    _install_bounded_clock(monkeypatch)
    springboard = [
        _el("Safari", "Safari"),
        _el("Messages", "Messages"),
    ]  # Home screen, off-namespace
    app_row = _el("stable.row.1", "Row 1")
    driver = _ScriptedDriver([springboard, springboard, [*springboard, app_row]])
    _await_ready(driver, id_namespaces=["stable"])  # type: ignore[arg-type]
    assert (
        driver.calls >= 3
    )  # did not settle on the Home screen; waited for an in-namespace element


def test_await_ready_returns_on_a_single_in_namespace_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One element under a declared namespace already proves the app foregrounded, so it is ready even
    # below the 2+ count — the namespace signal is stronger evidence than raw element count.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("stable.row.1", "Row 1")]])
    _await_ready(driver, id_namespaces=["stable"])  # type: ignore[arg-type]
    assert driver.calls == 1


def test_await_ready_without_namespaces_keeps_count_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No declared idNamespaces (a -noax app, web, or an unconfigured target): the 2+ count heuristic
    # is unchanged, so behavior for those targets is exactly as before.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("Safari", "S"), _el("Messages", "M")]])
    _await_ready(driver, id_namespaces=[])  # type: ignore[arg-type]
    assert driver.calls == 1


def test_await_ready_selector_takes_precedence_over_namespaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit readyWhen is the strongest signal: when both are given, the selector governs and the
    # namespace fallback does not short-circuit it (a modal over in-namespace chrome still waits).
    _install_bounded_clock(monkeypatch)
    chrome = [_el("stable.row.1", "Row 1")]  # in-namespace, but not the awaited modal
    target = _el("onboarding.start", "Start")
    driver = _ScriptedDriver([chrome, chrome, [*chrome, target]])
    _await_ready(driver, ready_sel={"id": "onboarding.start"}, id_namespaces=["stable"])  # type: ignore[arg-type]
    assert driver.calls >= 3  # waited for the selector despite an in-namespace element present


# --- BE-0231 Unit 1: readiness signal capture for the wait-timeout diagnostic ---


def test_await_ready_reports_the_readywhen_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    # The diagnostic needs to know which signal declared the app ready. When readyWhen matches, the
    # gate must report that — the strongest signal, and the one a first-wait on the same element then
    # races if it later flaps.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("onboarding.start", "Start"), _el("chrome", "C")]])
    result = _await_ready(driver, ready_sel={"id": "onboarding.start"})  # type: ignore[arg-type]
    assert result.ready is True
    assert result.signal == "readyWhen"
    assert result.elapsed_s >= 0.0


def test_await_ready_reports_the_namespace_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    # With declared idNamespaces and no readyWhen match key, an in-namespace element is what declares
    # ready — the diagnostic distinguishes this weaker signal from a readyWhen match.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("stable.row.1", "Row 1")]])
    result = _await_ready(driver, id_namespaces=["stable"])  # type: ignore[arg-type]
    assert result.ready is True
    assert result.signal == "namespace"


def test_await_ready_reports_the_count_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    # No readyWhen, no namespaces: the 2+ count fallback declared ready. A first-wait timeout after a
    # count-signal readiness is the classic "gate returned before the content" hypothesis.
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[_el("a", "A"), _el("b", "B")]])
    result = _await_ready(driver)  # type: ignore[arg-type]
    assert result.ready is True
    assert result.signal == "count"


def test_await_ready_reports_timeout_when_never_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate never saw a ready screen. The diagnostic must record that readiness itself timed out —
    # a distinct hypothesis from "ready passed but the awaited element then didn't render".
    _install_bounded_clock(monkeypatch)
    driver = _ScriptedDriver([[]])  # always empty
    result = _await_ready(driver, timeout=1.0)  # type: ignore[arg-type]
    assert result.ready is False
    assert result.signal == "timeout"
