"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import (
    _cost_ordered,
    capabilities_for_run,
    default_available,
    ensure_web_runtime,
    evidence_backends,
    make_driver,
    resolve_actuators,
    resolve_evidence_providers,
    select_actuator,
    select_actuator_cost_first,
    select_actuator_for_scenario,
)
from bajutsu.config import DeviceProvider, Effective, IosConfig, WebConfig, XcuitestConfig
from bajutsu.drivers import base
from bajutsu.scenario import Redact, Scenario

# A synthetic two-actuator platform (a "lean" actuator with no native network plus a "rich" one that
# has it), so the same-platform evidence fallback can be exercised even though every real platform is
# single-actuator today (iOS collapsed to XCUITest, BE-0290). Injected, not the
# module global.
_PLATFORMS = {"multi": ("lean", "rich"), "web": ("playwright",), "fake": ("fake",)}


def _caps(actuator: str) -> frozenset[str]:
    # `lean`/`fake` have no native network; `rich`/`playwright` do.
    network = frozenset({base.Capability.NETWORK})
    return network if actuator in ("rich", "playwright") else frozenset()


# --- BE-0020: read-only evidence fallback resolution (pure layer) ---


def test_evidence_backends_keeps_only_same_platform_available_siblings() -> None:
    # web is a different platform than the `lean` actuator, so it is never an evidence provider; the
    # same-platform sibling (`rich`) is, and the actuator itself is excluded.
    got = evidence_backends(
        ["multi", "web"], "lean", available=lambda b: True, platforms=_PLATFORMS
    )
    assert got == ["rich"]


def test_evidence_backends_includes_the_available_same_platform_sibling() -> None:
    # The same-platform sibling is an evidence provider when it is available.
    got = evidence_backends(
        ["multi", "web"], "lean", available=lambda b: True, platforms=_PLATFORMS
    )
    assert got == ["rich"]


def test_evidence_backends_empty_when_sibling_unavailable() -> None:
    # The sibling is known but not available — no evidence provider on that platform.
    got = evidence_backends(
        ["multi", "web"], "lean", available=lambda b: b != "rich", platforms=_PLATFORMS
    )
    assert got == []


def test_evidence_backends_empty_on_a_single_actuator_platform() -> None:
    # A real single-actuator platform (iOS is XCUITest-only since BE-0290) has no same-platform
    # sibling, so it exposes no evidence provider — read straight from the module PLATFORMS.
    assert evidence_backends(["ios", "web"], "xcuitest", available=lambda b: True) == []


def test_resolve_picks_the_first_same_platform_provider_for_the_gap() -> None:
    chosen, skipped = resolve_evidence_providers(
        ["multi", "web"], "lean", available=lambda b: True, caps=_caps, platforms=_PLATFORMS
    )
    assert chosen == {"network": "rich"}  # web (cross-platform) is ineligible
    assert skipped == {}


def test_resolve_skips_the_gap_when_no_same_platform_provider() -> None:
    # Only a cross-platform backend has network -> recorded as skipped, never a cross-platform pick.
    plats = {"multi": ("lean",), "web": ("playwright",), "fake": ("fake",)}
    chosen, skipped = resolve_evidence_providers(
        ["multi", "web"], "lean", available=lambda b: True, caps=_caps, platforms=plats
    )
    assert chosen == {}
    assert "network" in skipped


def test_resolve_no_gap_when_the_actuator_has_the_capability_natively() -> None:
    chosen, skipped = resolve_evidence_providers(
        ["web"], "playwright", available=lambda b: True, caps=_caps, platforms=_PLATFORMS
    )
    assert chosen == {} and skipped == {}


def test_resolve_skips_an_unavailable_provider() -> None:
    chosen, skipped = resolve_evidence_providers(
        ["multi"], "lean", available=lambda b: b != "rich", caps=_caps, platforms=_PLATFORMS
    )
    assert chosen == {} and "network" in skipped


def test_network_seeded_fake_is_a_readonly_evidence_provider() -> None:
    from bajutsu.drivers.base import EvidenceProvider
    from bajutsu.drivers.fake import FakeDriver
    from bajutsu.evidence.network import Collector, NetworkExchange

    ex = NetworkExchange(method="GET", path="/items", status=200)
    fake = FakeDriver(exchanges=[ex])
    assert isinstance(fake, EvidenceProvider)
    assert base.Capability.NETWORK in fake.capabilities()
    collector = fake.network_collector()
    assert isinstance(collector, Collector)
    assert collector.snapshot() == [ex]


def test_plain_fake_advertises_no_network() -> None:
    from bajutsu.drivers.fake import FakeDriver

    assert base.Capability.NETWORK not in FakeDriver().capabilities()


# --- BE-0141: backend lifecycle Protocol conformance ---


def test_backend_lifecycle_is_runtime_checkable() -> None:
    # BackendLifecycle is a typing umbrella over the full hook set — no single real driver owns all
    # four (see the Protocol docstring). @runtime_checkable still lets isinstance verify the
    # structural "has every hook" shape: a class with all four passes, one missing any does not.
    from bajutsu.drivers.base import BackendLifecycle

    class FullLifecycle:
        def navigate(self) -> None: ...
        def close(self) -> None: ...
        def reset_context(self) -> None: ...
        def await_ready(self, timeout: float = 10.0, poll: float = 0.1) -> None: ...

    class PartialLifecycle:
        def navigate(self) -> None: ...

    assert isinstance(FullLifecycle(), BackendLifecycle)
    assert not isinstance(PartialLifecycle(), BackendLifecycle)


def test_playwright_driver_provides_web_lifecycle() -> None:
    # The three web-only lifecycle calls in platform_lifecycle.py resolve to these concrete methods,
    # so the cast(BackendLifecycle, driver) sites there are backed by real implementations.
    from bajutsu.drivers.playwright import PlaywrightDriver

    for name in ("navigate", "close", "reset_context"):
        assert callable(getattr(PlaywrightDriver, name))


def test_xcuitest_driver_provides_await_ready() -> None:
    # The xcuitest-only await_ready call resolves to this concrete method.
    from bajutsu.drivers.xcuitest import XcuitestDriver

    assert callable(XcuitestDriver.await_ready)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["xcuitest"], "xcuitest"),
        # unknown backends are never selected, even when reported "available"
        (["bogus", "xcuitest"], "xcuitest"),
        # a platform token expands to its actuators; iOS is XCUITest-only since BE-0290.
        (["ios"], "xcuitest"),
        (["fake"], "fake"),
    ],
)
def test_select_actuator_picks_first_known_available(order: list[str], expected: str) -> None:
    assert select_actuator(order, available=lambda b: True) == expected


def test_ios_resolves_to_xcuitest() -> None:
    # iOS is a single actuator (BE-0290), so `[ios]` resolves to XCUITest alone and
    # selects it when available — no scenario/config change.
    assert resolve_actuators(["ios"]) == ["xcuitest"]
    assert select_actuator(["ios"], available=lambda a: True) == "xcuitest"


def test_xcuitest_is_implemented_and_selectable() -> None:
    from bajutsu.backends import IMPLEMENTED, KNOWN_ACTUATORS

    assert "xcuitest" in KNOWN_ACTUATORS
    assert "xcuitest" in IMPLEMENTED


def test_xcuitest_availability_gated_on_xcodebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda exe: "/usr/bin/xcodebuild" if exe == "xcodebuild" else None
    )
    assert default_available("xcuitest") is True
    monkeypatch.setattr("shutil.which", lambda exe: None)
    assert default_available("xcuitest") is False


def test_select_actuator_falls_through_unavailable_platform() -> None:
    # android resolves to adb; with adb unavailable, a request that lists fake after it falls
    # through to fake. `fake` is always available, adb only when the `adb` binary is present.
    available = lambda a: a == "fake"  # noqa: E731 - a one-liner availability stub
    assert select_actuator(["android", "fake"], available=available) == "fake"


def test_resolve_actuators_expands_platforms() -> None:
    # Platform tokens expand to their actuators; bare actuators and unknowns pass through.
    assert resolve_actuators(["ios", "android", "web", "fake"]) == [
        "xcuitest",
        "adb",
        "playwright",
        "fake",
    ]
    assert resolve_actuators(["xcuitest", "bogus"]) == ["xcuitest", "bogus"]


def test_select_none_available_raises() -> None:
    with pytest.raises(RuntimeError):
        select_actuator(["xcuitest"], available=lambda b: False)


def test_select_android_actuator_when_available() -> None:
    # android is implemented now (BE-0007): it resolves to adb and is selected when available.
    assert select_actuator(["android"], available=lambda a: True) == "adb"


def test_select_android_unavailable_is_not_a_planned_error() -> None:
    # adb is implemented but its device tool may be absent: that is "no available actuator", not the
    # "recognized but not implemented yet" error reserved for a driver that does not exist.
    with pytest.raises(RuntimeError, match="no available actuator"):
        select_actuator(["android"], available=lambda a: False)


def test_select_planned_backend_reports_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    # The "recognized but not implemented yet" path still guards a future planned actuator: with adb
    # dropped from IMPLEMENTED it is recognized (in PLATFORMS) yet has no driver, so the message
    # points at the platform-reach design in vision.md rather than a generic "no available actuator".
    monkeypatch.setattr("bajutsu.backends.IMPLEMENTED", frozenset({"xcuitest", "fake"}))
    with pytest.raises(RuntimeError, match="not implemented yet"):
        select_actuator(["android"])


def test_select_web_actuator_when_available() -> None:
    # web is implemented now: it resolves to playwright and is selected when available.
    assert select_actuator(["web"], available=lambda a: True) == "playwright"


def test_playwright_availability_gated_on_package(monkeypatch: pytest.MonkeyPatch) -> None:
    # Availability is the python package (probed without importing it), not a PATH executable.
    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: True)
    assert default_available("playwright") is True
    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: False)
    assert default_available("playwright") is False


def test_fake_is_always_available() -> None:
    # The fake backend needs no executable, so it selects without any device tooling.
    assert select_actuator(["fake"]) == "fake"


def test_make_driver_fake() -> None:
    # The fake driver is constructible without a device — used by the in-process demos/tests.
    fake = make_driver("fake", "U")
    assert fake.name == "fake"
    assert base.Capability.QUERY in fake.capabilities()


def test_make_driver_threads_fetch_hierarchy_to_the_adb_driver() -> None:
    # The resident channel is wired in by passing a HierarchyFetch through make_driver (BE-0245);
    # the adb driver then reads over it instead of shelling out to `uiautomator dump`.
    xml = (
        "<?xml version='1.0' ?><hierarchy rotation=\"0\">"
        '<node index="0" class="android.widget.Button" resource-id="stable.submit" '
        'text="送信" bounds="[0,0][10,10]" /></hierarchy>'
    )
    driver = make_driver("adb", "U", fetch_hierarchy=lambda: xml)
    assert driver.name == "adb"
    assert len(driver.query()) == 1  # read came from the fetch, not a dump subprocess


def test_make_driver_playwright_requires_base_url() -> None:
    # The web driver needs a target URL; make_driver rejects a missing one before touching a
    # browser (so this stays browser-free).
    with pytest.raises(ValueError, match="base_url"):
        make_driver("playwright", "web")


def test_make_driver_forwards_browser_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # make_driver passes `browser=` straight to PlaywrightDriver, so the web environment's
    # eff.browser reaches the launch (BE-0076). Recorded via a stand-in driver — no real browser.
    import bajutsu.drivers.playwright as pw_mod

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, base_url: str, **kwargs: object) -> None:
            captured["base_url"] = base_url
            captured.update(kwargs)

    monkeypatch.setattr(pw_mod, "PlaywrightDriver", _Recorder)
    make_driver("playwright", "", base_url="http://app.test/", browser="webkit")
    assert captured["browser"] == "webkit"


def test_make_driver_browser_defaults_to_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    import bajutsu.drivers.playwright as pw_mod

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, base_url: str, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(pw_mod, "PlaywrightDriver", _Recorder)
    make_driver("playwright", "", base_url="http://app.test/")
    assert captured["browser"] == "chromium"


def test_make_driver_xcuitest() -> None:
    from bajutsu.drivers.xcuitest import XcuitestDriver

    driver = make_driver("xcuitest", "UDID-1", runner_port=9999)
    assert isinstance(driver, XcuitestDriver)
    assert base.Capability.SEMANTIC_TAP in driver.capabilities()
    assert base.Capability.MULTI_TOUCH in driver.capabilities()


def test_make_driver_xcuitest_requires_runner_port() -> None:
    with pytest.raises(ValueError, match="runner_port"):
        make_driver("xcuitest", "UDID-1")


def test_make_driver_adb() -> None:
    from bajutsu.drivers.adb import AdbDriver

    driver = make_driver("adb", "emulator-5554")
    assert isinstance(driver, AdbDriver)
    # The lean, coordinate-based backend: no semantic tap, no native network.
    assert base.Capability.SEMANTIC_TAP not in driver.capabilities()
    assert base.Capability.SCREENSHOT in driver.capabilities()


def test_make_driver_planned_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # A recognized-but-unimplemented actuator raises NotImplementedError (distinct from an
    # outright-unknown token), so the message can point at vision.md's reach design. Every real
    # actuator now has a driver, so a synthetic "future" token in KNOWN_ACTUATORS stands in.
    monkeypatch.setattr("bajutsu.backends.KNOWN_ACTUATORS", ("xcuitest", "future"))
    with pytest.raises(NotImplementedError, match="not implemented yet"):
        make_driver("future", "U")


def test_make_driver_unknown() -> None:
    with pytest.raises(ValueError, match="bogus"):
        make_driver("bogus", "U")


def test_ensure_web_runtime_noop_when_not_web(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-web backend never shells out — auto-install is scoped to the web actuator.
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a[0]))
    ensure_web_runtime(["ios", "fake"])
    assert calls == []


def test_ensure_web_runtime_installs_engine_when_package_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Web requested + Playwright present, but the engine binary may not be: install it (idempotent).
    # The package step is skipped (it's already importable); only `playwright install <engine>` runs.
    calls: list[list[str]] = []
    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: True)
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    ensure_web_runtime(["web"], "firefox")
    assert len(calls) == 1
    assert calls[0][1:] == ["-m", "playwright", "install", "firefox"]


def test_ensure_web_runtime_installs_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Web requested + Playwright absent: install the package additively (uv pip), then the engine.
    calls: list[list[str]] = []
    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: False)
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    ensure_web_runtime(["web"])  # default engine
    assert calls[0][:3] == ["uv", "pip", "install"] and "playwright" in calls[0]
    assert calls[1][1:] == ["-m", "playwright", "install", "chromium"]


def test_ensure_web_runtime_installs_requested_engine_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The requested engine (not chromium) is the one fetched after the package is added.
    calls: list[list[str]] = []
    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: False)
    monkeypatch.setattr("subprocess.run", lambda cmd, **k: calls.append(cmd))
    ensure_web_runtime(["web"], "webkit")
    assert calls[1][1:] == ["-m", "playwright", "install", "webkit"]


def test_ensure_web_runtime_reports_install_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed install surfaces a RuntimeError with a manual-install hint, so `run` exits 2
    # cleanly instead of crashing.
    import subprocess

    monkeypatch.setattr("bajutsu.backends._playwright_available", lambda: False)

    def boom(cmd: list[str], **k: object) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("subprocess.run", boom)
    with pytest.raises(RuntimeError, match="auto-install the web backend"):
        ensure_web_runtime(["web"])


def test_capabilities_for_xcuitest_reads_the_driver_constant_without_a_device() -> None:
    # BE-0019: the richer iOS actuator's capabilities are readable before its runner is wired into
    # selection — reading the class constant constructs no driver and starts no runner.
    from bajutsu.backends import capabilities_for
    from bajutsu.drivers.xcuitest import XcuitestDriver

    caps = capabilities_for("xcuitest")
    assert caps == XcuitestDriver.CAPABILITIES
    assert base.Capability.SEMANTIC_TAP in caps and base.Capability.MULTI_TOUCH in caps
    assert base.Capability.NETWORK not in caps  # network rides on the app-side collector (BE-0020)


# --- BE-0238 Unit 3: a real iOS device narrows the static XCUITest capability set -----------------


def _ios_eff(
    *, xcuitest: XcuitestConfig | None = None, device_provider: DeviceProvider | None = None
) -> Effective:
    """A minimal iOS `Effective`, optionally carrying an `xcuitest` sub-config / `deviceProvider`."""
    return Effective(
        target="demo",
        platform_config=IosConfig(bundle_id="com.example.demo", xcuitest=xcuitest),
        backend=["ios"],
        device="iPhone 15",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
        device_provider=device_provider,
    )


def test_capabilities_for_run_drops_simctl_backed_caps_on_a_real_ios_device() -> None:
    # A real device is not managed through simctl, so the whole simctl-backed DeviceControl family
    # and the simctl-privacy permission grants do not apply (BE-0238 Unit 3): preflight must not
    # advertise them, or a scenario needing one fails late with a simctl error instead of up front.
    eff = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="device"))
    caps = capabilities_for_run("xcuitest", eff)
    assert base.DEVICE_CONTROL_ALL.isdisjoint(caps)
    assert base.IOS_PERMISSION_CAPABILITIES.isdisjoint(caps)
    # The on-device capabilities (driven by the XCTest runner, not simctl) survive the narrowing.
    for cap in (
        base.Capability.QUERY,
        base.Capability.ELEMENTS,
        base.Capability.SCREENSHOT,
        base.Capability.SEMANTIC_TAP,
        base.Capability.MULTI_TOUCH,
    ):
        assert cap in caps


def test_capabilities_for_run_keeps_the_full_set_on_the_simulator() -> None:
    # The Simulator default (no deviceType, or explicit "simulator") keeps every static capability:
    # simctl reaches the Simulator, so DeviceControl / permissions still apply.
    from bajutsu.backends import capabilities_for

    for xcfg in (None, XcuitestConfig(test_runner="Runner.xctestrun", device_type="simulator")):
        assert capabilities_for_run("xcuitest", _ios_eff(xcuitest=xcfg)) == capabilities_for(
            "xcuitest"
        )


def test_capabilities_for_run_is_a_noop_for_non_xcuitest_backends() -> None:
    # The narrowing is XCUITest-only; adb / web read their static set unchanged even when the
    # (unrelated) target config would look like a real device to a careless check.
    from bajutsu.backends import capabilities_for

    eff = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="device"))
    assert capabilities_for_run("adb", eff) == capabilities_for("adb")
    web = Effective(
        target="w",
        platform_config=WebConfig(base_url="https://app.test"),
        backend=["web"],
        device="",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )
    assert capabilities_for_run("playwright", web) == capabilities_for("playwright")


def test_real_device_narrowing_makes_a_device_control_scenario_unsupported() -> None:
    # End-to-end with the preflight (BE-0082): a setLocation scenario runs on the Simulator but is
    # skipped up front on a real device, where simctl device control does not apply (BE-0238 Unit 3).
    from bajutsu import capability_preflight

    scenario = Scenario.model_validate(
        {"name": "loc", "steps": [{"setLocation": {"lat": 1.0, "lon": 2.0}}]}
    )
    sim = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="simulator"))
    dev = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="device"))
    assert capability_preflight.unsupported(scenario, capabilities_for_run("xcuitest", sim)) == []
    assert capability_preflight.unsupported(scenario, capabilities_for_run("xcuitest", dev))


# --- BE-0238 Slice C: the live WebDriver route narrows to what that transport actually drives -------

# The routing signal for the live route is the *resolved* udid spec being a WebDriver URL — exactly
# what `is_webdriver_endpoint` (and so `environment_for`) keys on. The `appium` provider surfaces this
# endpoint as the udid spec, but the narrowing keys on the spec itself, not the provider, so preflight
# and routing can never disagree (see the URL-udid test below).
_LIVE_ENDPOINT = "https://grid.example.com/wd/hub"


def test_capabilities_for_run_narrows_to_the_live_driver_set_on_a_webdriver_endpoint() -> None:
    # The live route drives Appium's XCUITest `mobile:` commands, not simctl and not the native text
    # selection the local runner does — so preflight must advertise exactly what the live driver drives
    # (its own CAPABILITIES), the single source of truth, rather than the local XCUITest set.
    from bajutsu.drivers.xcuitest_live import XcuitestLiveDriver

    caps = capabilities_for_run("xcuitest", _ios_eff(), _LIVE_ENDPOINT)
    assert caps == XcuitestLiveDriver.CAPABILITIES


def test_capabilities_for_run_narrows_on_a_url_udid_regardless_of_the_provider() -> None:
    # The narrowing keys on the resolved udid spec, the same signal `is_webdriver_endpoint` routes on
    # — not on `deviceProvider.kind`. Two targets whose *only* difference is the provider — the default
    # `local` (no block) versus an `appium` cloud provider — must narrow identically for the same URL
    # udid spec. If the local case failed to narrow, a `--udid https://…` run under it would advertise
    # the full local set and a select/copy scenario would fail late mid-run instead of being skipped up
    # front — the exact divergence this slice closes.
    from bajutsu.drivers.xcuitest_live import XcuitestLiveDriver

    local = capabilities_for_run("xcuitest", _ios_eff(device_provider=None), _LIVE_ENDPOINT)
    appium = capabilities_for_run(
        "xcuitest",
        _ios_eff(device_provider=DeviceProvider(kind="appium", endpoint=_LIVE_ENDPOINT)),
        _LIVE_ENDPOINT,
    )
    assert local == appium == XcuitestLiveDriver.CAPABILITIES


def test_live_route_drops_text_selection_and_simctl_backed_caps() -> None:
    # Concretely, the live set loses the three families the WebDriver transport cannot honor: text
    # selection (no first-class `mobile:` command), the whole DeviceControl family, and the
    # simctl-privacy permission grants — all of which the local Simulator driver still advertises.
    caps = capabilities_for_run("xcuitest", _ios_eff(), _LIVE_ENDPOINT)
    assert base.Capability.TEXT_SELECTION not in caps
    assert base.DEVICE_CONTROL_ALL.isdisjoint(caps)
    assert base.IOS_PERMISSION_CAPABILITIES.isdisjoint(caps)
    # What the live transport does drive survives: a semantic tap, condition waits, screenshots, and
    # the two-finger gestures (Slice B).
    for cap in (
        base.Capability.QUERY,
        base.Capability.SEMANTIC_TAP,
        base.Capability.SCREENSHOT,
        base.Capability.MULTI_TOUCH,
    ):
        assert cap in caps


def test_live_route_narrowing_makes_a_copy_selection_scenario_unsupported() -> None:
    # End-to-end with the preflight (BE-0082): a `copy` scenario runs on the local Simulator but is
    # skipped up front on the live route, whose driver raises UnsupportedAction for select/copy.
    from bajutsu import capability_preflight

    scenario = Scenario.model_validate(
        {"name": "cp", "steps": [{"select": {"into": {"id": "field"}}}, {"copy": {}}]}
    )
    sim = _ios_eff(xcuitest=XcuitestConfig(test_runner="Runner.xctestrun", device_type="simulator"))
    assert capability_preflight.unsupported(scenario, capabilities_for_run("xcuitest", sim)) == []
    assert capability_preflight.unsupported(
        scenario, capabilities_for_run("xcuitest", _ios_eff(), _LIVE_ENDPOINT)
    )


# --- BE-0240: capability-aware, cost-ordered per-scenario actuator selection -------------------
#
# iOS collapsed to a single actuator (BE-0290), so the cost-ordering / escalation mechanism no
# longer has a real multi-actuator
# platform. It is still exercised here against a synthetic two-actuator platform — a `lean` actuator
# with no multiTouch plus a `rich` one that has it, `lean` cheapest — so the logic stays covered for a
# future multi-actuator platform, alongside a test that real iOS now collapses to a hard pin.

_TAP = Scenario.model_validate({"name": "tap", "steps": [{"tap": {"id": "ok"}}]})
_PINCH = Scenario.model_validate(
    {"name": "pinch", "steps": [{"pinch": {"sel": {"id": "m"}, "scale": 2.0}}]}
)

_MULTI_PLATFORMS = {"multi": ("rich", "lean"), "web": ("playwright",), "fake": ("fake",)}
_MULTI_COST_ORDER = {"multi": ("lean", "rich")}  # lean cheapest; stability order is rich-first


def _multi_caps(actuator: str) -> frozenset[str]:
    # Both back the baseline read path (query + elements); only `rich` drives two-finger gestures —
    # `lean` does not, the way a leaner actuator lacks what a richer one has.
    base_caps = {base.Capability.QUERY, base.Capability.ELEMENTS}
    return frozenset(base_caps | ({base.Capability.MULTI_TOUCH} if actuator == "rich" else set()))


@pytest.fixture
def _multi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module's platform/cost/known tables at the synthetic two-actuator platform."""
    monkeypatch.setattr("bajutsu.backends.PLATFORMS", _MULTI_PLATFORMS)
    monkeypatch.setattr("bajutsu.backends.COST_ORDER", _MULTI_COST_ORDER)
    # KNOWN_ACTUATORS / IMPLEMENTED are derived at import, so selection (and its planned-vs-available
    # diagnostic) gates on the synthetic names.
    monkeypatch.setattr("bajutsu.backends.KNOWN_ACTUATORS", ("rich", "lean", "playwright", "fake"))
    monkeypatch.setattr(
        "bajutsu.backends.IMPLEMENTED", frozenset({"rich", "lean", "playwright", "fake"})
    )


@pytest.mark.usefixtures("_multi")
def test_cost_ordered_reorders_to_cheapest_first() -> None:
    # `[multi]` resolves in stability order (rich, lean); cost order puts the cheapest (lean) first.
    assert _cost_ordered(list(resolve_actuators(["multi"]))) == ["lean", "rich"]
    # An unranked platform (no COST_ORDER entry) keeps its resolved order.
    assert _cost_ordered(["playwright"]) == ["playwright"]


def test_ios_cost_order_is_a_noop_single_actuator() -> None:
    # Real iOS is XCUITest-only since BE-0290, so there is nothing to reorder.
    assert _cost_ordered(list(resolve_actuators(["ios"]))) == ["xcuitest"]


@pytest.mark.usefixtures("_multi")
def test_select_for_scenario_prefers_the_cheapest_sufficient_actuator() -> None:
    # A plain tap needs nothing `lean` lacks, so the cheapest (lean) is chosen even though `rich` is
    # available and more capable — cost wins when both suffice.
    got = select_actuator_for_scenario(["multi"], _TAP, available=lambda a: True, caps=_multi_caps)
    assert got == "lean"


@pytest.mark.usefixtures("_multi")
def test_select_for_scenario_escalates_only_when_the_cheap_actuator_cannot_run_it() -> None:
    # A pinch needs multiTouch, which `lean` lacks — so selection escalates to the richer `rich`.
    got = select_actuator_for_scenario(
        ["multi"], _PINCH, available=lambda a: True, caps=_multi_caps
    )
    assert got == "rich"


@pytest.mark.usefixtures("_multi")
def test_select_for_scenario_returns_richest_available_when_none_suffices() -> None:
    # A pinch with only `lean` available: no candidate suffices, so the richest *available* one is
    # returned (lean here) — the caller's preflight then fails it with lean's gaps, not a crash.
    got = select_actuator_for_scenario(
        ["multi"], _PINCH, available=lambda a: a == "lean", caps=_multi_caps
    )
    assert got == "lean"


def test_select_for_scenario_pin_never_escalates() -> None:
    # An explicit single-actuator request is a hard pin: even a pinch stays on that actuator (no
    # capability escalation), consistent with `select_actuator`. The preflight, not selection, rejects.
    assert select_actuator_for_scenario(["adb"], _PINCH, available=lambda a: True) == "adb"


@pytest.mark.usefixtures("_multi")
def test_select_for_scenario_raises_when_nothing_available() -> None:
    # No candidate available at all reuses `select_actuator`'s precise error (raised, not swallowed).
    with pytest.raises(RuntimeError, match="no available actuator"):
        select_actuator_for_scenario(["multi"], _TAP, available=lambda a: False, caps=_multi_caps)


# --- BE-0267: scenario-free, cost-ordered selection (serve capture/enrich) ---------------------


@pytest.mark.usefixtures("_multi")
def test_cost_first_prefers_the_cheapest_available_actuator() -> None:
    # No scenario: `[multi]` picks the cheapest available actuator (lean), never the alias head `rich`.
    assert select_actuator_cost_first(["multi"], available=lambda a: True) == "lean"


@pytest.mark.usefixtures("_multi")
def test_cost_first_escalates_when_the_cheap_actuator_is_unavailable() -> None:
    # lean absent: the next cost-ordered candidate (`rich`) wins — cost order, not capability.
    assert select_actuator_cost_first(["multi"], available=lambda a: a == "rich") == "rich"


def test_cost_first_single_actuator_is_a_hard_pin() -> None:
    # An explicit single actuator delegates to `select_actuator` — a hard pin, no reordering. `[ios]`
    # collapses to the single XCUITest actuator the same way since BE-0290.
    assert select_actuator_cost_first(["xcuitest"], available=lambda a: True) == "xcuitest"
    assert select_actuator_cost_first(["ios"], available=lambda a: True) == "xcuitest"


@pytest.mark.usefixtures("_multi")
def test_cost_first_raises_when_nothing_available() -> None:
    # None available reuses `select_actuator`'s precise error (raised, not swallowed).
    with pytest.raises(RuntimeError, match="no available actuator"):
        select_actuator_cost_first(["multi"], available=lambda a: False)
