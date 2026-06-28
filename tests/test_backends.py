"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import (
    default_available,
    ensure_web_runtime,
    evidence_backends,
    make_driver,
    resolve_actuators,
    resolve_evidence_providers,
    select_actuator,
)
from bajutsu.drivers import base

# A two-actuator iOS platform (idb + a hypothetical second iOS actuator), so the same-platform
# fallback can be exercised before XCUITest (BE-0019) actually lands. Injected, not the module global.
_PLATFORMS = {"ios": ("idb", "xcuitest"), "web": ("playwright",), "fake": ("fake",)}


def _caps(actuator: str) -> frozenset[str]:
    # idb/fake have no native network; xcuitest/playwright do.
    network = frozenset({base.Capability.NETWORK})
    return network if actuator in ("xcuitest", "playwright") else frozenset()


# --- BE-0020: read-only evidence fallback resolution (pure layer) ---


def test_evidence_backends_keeps_only_same_platform_available_siblings() -> None:
    # web is a different platform than the idb actuator, so it is never an evidence provider; the
    # same-platform sibling (xcuitest) is, and the actuator itself is excluded.
    got = evidence_backends(["ios", "web"], "idb", available=lambda b: True, platforms=_PLATFORMS)
    assert got == ["xcuitest"]


def test_evidence_backends_is_empty_without_a_second_same_platform_actuator() -> None:
    # The realistic default today: ios lists xcuitest first (BE-0019) but it has no driver, so under
    # real availability it is never a usable sibling and the fallback resolves to nothing.
    assert evidence_backends(["ios", "web"], "idb", available=default_available) == []


def test_resolve_picks_the_first_same_platform_provider_for_the_gap() -> None:
    chosen, skipped = resolve_evidence_providers(
        ["ios", "web"], "idb", available=lambda b: True, caps=_caps, platforms=_PLATFORMS
    )
    assert chosen == {"network": "xcuitest"}  # web (cross-platform) is ineligible
    assert skipped == {}


def test_resolve_skips_the_gap_when_no_same_platform_provider() -> None:
    # Only a cross-platform backend has network -> recorded as skipped, never a cross-platform pick.
    plats = {"ios": ("idb",), "web": ("playwright",), "fake": ("fake",)}
    chosen, skipped = resolve_evidence_providers(
        ["ios", "web"], "idb", available=lambda b: True, caps=_caps, platforms=plats
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
        ["ios"], "idb", available=lambda b: b != "xcuitest", caps=_caps, platforms=_PLATFORMS
    )
    assert chosen == {} and "network" in skipped


def test_network_seeded_fake_is_a_readonly_evidence_provider() -> None:
    from bajutsu.drivers.base import EvidenceProvider
    from bajutsu.drivers.fake import FakeDriver
    from bajutsu.network import Collector, NetworkExchange

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


def test_idb_exposes_no_evidence_provider_surface() -> None:
    # idb has no native network, so it must not expose network_collector (read-only fallback surface).
    from bajutsu.drivers.idb import IdbDriver

    assert not hasattr(IdbDriver, "network_collector")


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["idb"], "idb"),
        # unknown backends are never selected, even when reported "available"
        (["bogus", "idb"], "idb"),
        # a platform token expands to its actuators, most-stable-first (ios -> xcuitest, idb);
        # with everything reported "available", the preferred actuator wins (BE-0019).
        (["ios"], "xcuitest"),
        (["fake"], "fake"),
    ],
)
def test_select_actuator_picks_first_known_available(order: list[str], expected: str) -> None:
    assert select_actuator(order, available=lambda b: True) == expected


def test_ios_prefers_xcuitest_but_falls_back_to_idb() -> None:
    # BE-0019 Slice 1: ios resolves xcuitest-first, but the driver does not exist yet, so xcuitest is
    # never available and a real `--backend ios` run still selects idb — no scenario/config change.
    # When a future slice lands the driver, the same ordering picks xcuitest with nothing else moving.
    assert resolve_actuators(["ios"]) == ["xcuitest", "idb"]
    assert select_actuator(["ios"], available=lambda a: a == "idb") == "idb"
    assert select_actuator(["ios"], available=lambda a: True) == "xcuitest"


def test_xcuitest_is_known_but_not_yet_selectable() -> None:
    # The ordering flip makes xcuitest a *known* actuator (derived from PLATFORMS), but its runner is
    # not wired into selection yet: it stays out of IMPLEMENTED, so it is never available/selected.
    # Its capabilities are nonetheless readable from the driver class (see the capabilities_for test).
    from bajutsu.backends import IMPLEMENTED, KNOWN_ACTUATORS, default_available

    assert "xcuitest" in KNOWN_ACTUATORS
    assert "xcuitest" not in IMPLEMENTED
    assert default_available("xcuitest") is False


def test_select_actuator_falls_through_unavailable_platform() -> None:
    # android resolves to adb (unavailable by default), so a request that lists fake after it
    # falls through to fake — no forced availability needed.
    assert select_actuator(["android", "fake"]) == "fake"


def test_resolve_actuators_expands_platforms() -> None:
    # Platform tokens expand to their actuators; bare actuators and unknowns pass through.
    assert resolve_actuators(["ios", "android", "web", "fake"]) == [
        "xcuitest",
        "idb",
        "adb",
        "playwright",
        "fake",
    ]
    assert resolve_actuators(["idb", "bogus"]) == ["idb", "bogus"]


def test_select_none_available_raises() -> None:
    with pytest.raises(RuntimeError):
        select_actuator(["idb"], available=lambda b: False)


def test_select_planned_backend_reports_not_implemented() -> None:
    # android resolves to adb, which is recognized but has no driver yet — a clear error,
    # not a generic "no available actuator".
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


def test_make_driver() -> None:
    # idb actuates by coordinates (resolving each element's frame center), so it
    # does not advertise a semantic tap.
    idb = make_driver("idb", "U")
    assert idb.name == "idb"
    assert base.Capability.QUERY in idb.capabilities()
    assert base.Capability.SEMANTIC_TAP not in idb.capabilities()


def test_make_driver_fake() -> None:
    # The fake driver is constructible without a device — used by the in-process demos/tests.
    fake = make_driver("fake", "U")
    assert fake.name == "fake"
    assert base.Capability.QUERY in fake.capabilities()


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


def test_make_driver_planned_backend() -> None:
    # A recognized-but-unimplemented actuator raises NotImplementedError (distinct from an
    # outright-unknown token), so the message can point at the multi-platform design.
    with pytest.raises(NotImplementedError, match="not implemented yet"):
        make_driver("adb", "U")


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
