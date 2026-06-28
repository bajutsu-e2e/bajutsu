"""Environment runnability gate (the `doctor` preflight)."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu import preflight
from bajutsu.idb_version import IdbVersions


def _which(present: set[str]) -> Callable[[str], str | None]:
    return lambda exe: f"/usr/bin/{exe}" if exe in present else None


def test_idb_all_present_passes() -> None:
    checks = preflight.runnability(
        "idb", which=_which({"xcrun", "idb", "idb_companion"}), booted_count=lambda: 1
    )
    assert [c.name for c in checks] == ["xcrun", "idb", "idb_companion", "Simulator booted"]
    assert preflight.passed(checks)


def test_missing_companion_fails_with_hint() -> None:
    checks = preflight.runnability("idb", which=_which({"xcrun", "idb"}), booted_count=lambda: 1)
    assert not preflight.passed(checks)
    companion = next(c for c in checks if c.name == "idb_companion")
    assert not companion.ok and "brew install" in companion.detail


def test_no_booted_simulator_fails() -> None:
    checks = preflight.runnability(
        "idb", which=_which({"xcrun", "idb", "idb_companion"}), booted_count=lambda: 0
    )
    assert not preflight.passed(checks)
    assert not next(c for c in checks if c.name == "Simulator booted").ok


def test_fake_backend_needs_nothing() -> None:
    assert preflight.runnability("fake") == []


def test_web_all_present_passes() -> None:
    checks = preflight.runnability("playwright", web_pkg=lambda: True, web_browser=lambda _e: True)
    assert [c.name for c in checks] == ["playwright", "chromium browser"]
    assert preflight.passed(checks)


def test_web_reports_the_selected_engine() -> None:
    # doctor reports which engine was selected (BE-0076): the check names and probes the engine,
    # so "you asked for webkit but only chromium is installed" surfaces here.
    seen: list[str] = []

    def probe(engine: str) -> bool:
        seen.append(engine)
        return True

    checks = preflight.runnability(
        "playwright", web_engine="webkit", web_pkg=lambda: True, web_browser=probe
    )
    assert [c.name for c in checks] == ["playwright", "webkit browser"]
    assert seen == ["webkit"]  # the selected engine was the one probed


def test_web_does_not_require_xcode_or_simulator() -> None:
    # The web backend needs no Xcode and no Simulator — only the iOS path checks those.
    names = [
        c.name
        for c in preflight.runnability(
            "playwright", web_pkg=lambda: True, web_browser=lambda _e: True
        )
    ]
    assert "xcrun" not in names and "Simulator booted" not in names


def test_web_missing_package_fails_with_hint() -> None:
    checks = preflight.runnability(
        "playwright", web_pkg=lambda: False, web_browser=lambda _e: False
    )
    assert not preflight.passed(checks)
    pkg = next(c for c in checks if c.name == "playwright")
    assert not pkg.ok and "--extra web" in pkg.detail


def test_web_missing_browser_fails_with_hint() -> None:
    checks = preflight.runnability(
        "playwright", web_engine="firefox", web_pkg=lambda: True, web_browser=lambda _e: False
    )
    assert not preflight.passed(checks)
    browser = next(c for c in checks if c.name == "firefox browser")
    assert not browser.ok and "playwright install firefox" in browser.detail


def test_idb_version_check_skipped_when_no_pin() -> None:
    # No declared range → no line in doctor (the pin is optional).
    assert preflight.idb_version_check(None, IdbVersions(companion="1.1.8", client="1.1.8")) is None


def test_idb_version_check_passes_when_in_range() -> None:
    check = preflight.idb_version_check(">=1.1.8", IdbVersions(companion="1.2.0", client=None))
    assert check is not None and check.ok
    assert "1.2.0" in check.detail and ">=1.1.8" in check.detail


def test_idb_version_check_fails_below_range() -> None:
    check = preflight.idb_version_check(">=1.1.8", IdbVersions(companion="1.1.7", client=None))
    assert check is not None and not check.ok
    assert "1.1.7" in check.detail and ">=1.1.8" in check.detail


def test_idb_version_check_fails_when_companion_unknown() -> None:
    # idb_companion not found / unreadable: can't confirm the pin, so report not-ok, don't guess.
    check = preflight.idb_version_check(">=1.1.8", IdbVersions(companion=None, client=None))
    assert check is not None and not check.ok
    assert "unknown" in check.detail


def test_render_marks_pass_and_fail() -> None:
    out = preflight.render(
        preflight.runnability("idb", which=_which({"xcrun"}), booted_count=lambda: 0)
    )
    assert "✓ xcrun" in out and "✗ idb" in out
