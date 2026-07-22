"""Environment runnability gate (the `doctor` preflight)."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu import preflight


def _which(present: set[str]) -> Callable[[str], str | None]:
    return lambda exe: f"/usr/bin/{exe}" if exe in present else None


def test_config_checks_ios_requires_bundle_id() -> None:
    ok = preflight.config_checks(
        "xcuitest", target="demo", bundle_id="com.example.demo", base_url=None
    )
    assert [c.name for c in ok] == ["target bundleId"] and preflight.passed(ok)

    missing = preflight.config_checks("xcuitest", target="demo", bundle_id="", base_url=None)
    assert not preflight.passed(missing)
    assert "set targets.demo.bundleId" in missing[0].detail


def test_config_checks_web_requires_base_url() -> None:
    ok = preflight.config_checks(
        "playwright", target="site", bundle_id="", base_url="http://x/index.html"
    )
    assert [c.name for c in ok] == ["target baseUrl"] and preflight.passed(ok)

    missing = preflight.config_checks("playwright", target="site", bundle_id="", base_url=None)
    assert not preflight.passed(missing)
    assert "set targets.site.baseUrl" in missing[0].detail


def test_config_checks_fake_needs_nothing() -> None:
    assert preflight.config_checks("fake", target="t", bundle_id="", base_url=None) == []


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


# --- iOS (xcuitest) — the sole iOS backend since BE-0290 ---


def test_xcuitest_all_present_passes() -> None:
    checks = preflight.runnability(
        "xcuitest", which=_which({"xcrun", "xcodebuild"}), booted_count=lambda: 1
    )
    assert [c.name for c in checks] == ["xcrun", "xcodebuild", "Simulator booted"]
    assert preflight.passed(checks)


def test_xcuitest_needs_xcrun_and_xcodebuild() -> None:
    checks = preflight.runnability(
        "xcuitest", which=_which({"xcrun", "xcodebuild"}), booted_count=lambda: 1
    )
    assert [c.name for c in checks] == ["xcrun", "xcodebuild", "Simulator booted"]
    assert preflight.passed(checks)


def test_xcuitest_missing_xcodebuild_fails() -> None:
    checks = preflight.runnability("xcuitest", which=_which({"xcrun"}), booted_count=lambda: 1)
    assert not preflight.passed(checks)
    xcodebuild = next(c for c in checks if c.name == "xcodebuild")
    assert not xcodebuild.ok and "Xcode" in xcodebuild.detail


def test_no_booted_simulator_fails() -> None:
    checks = preflight.runnability(
        "xcuitest", which=_which({"xcrun", "xcodebuild"}), booted_count=lambda: 0
    )
    assert not preflight.passed(checks)
    assert not next(c for c in checks if c.name == "Simulator booted").ok


# --- Android (adb) ---


def test_config_checks_adb_requires_package() -> None:
    ok = preflight.config_checks("adb", target="app", bundle_id="", base_url=None, package="com.x")
    assert preflight.passed(ok)
    missing = preflight.config_checks("adb", target="app", bundle_id="", base_url=None, package="")
    assert not preflight.passed(missing)
    assert "targets.app.package" in missing[0].detail


def test_adb_needs_platform_tools_and_a_device() -> None:
    checks = preflight.runnability("adb", which=_which({"adb"}), booted_count=lambda: 1)
    assert [c.name for c in checks] == ["adb", "device attached"]
    assert preflight.passed(checks)
    # No Xcode dependency on the Android path.
    assert "xcrun" not in {c.name for c in checks}


def test_adb_missing_binary_or_device_fails() -> None:
    no_adb = preflight.runnability("adb", which=_which(set()), booted_count=lambda: 1)
    assert not preflight.passed(no_adb)
    no_device = preflight.runnability("adb", which=_which({"adb"}), booted_count=lambda: 0)
    assert not preflight.passed(no_device)


def test_render_marks_pass_and_fail() -> None:
    out = preflight.render(
        preflight.runnability("xcuitest", which=_which({"xcrun"}), booted_count=lambda: 0)
    )
    assert "✓ xcrun" in out and "✗ xcodebuild" in out


# --- shared doctor environment-check assembly (BE-0199) ---


def test_doctor_environment_checks_is_the_runnability_set() -> None:
    # iOS is a single actuator (BE-0290), so there is no cheaper actuator to merge and no version
    # pin to report — the shared assembly is just the backend's runnability checks, the same for CLI and serve.
    which = _which({"xcrun", "xcodebuild"})
    checks = preflight.doctor_environment_checks(
        "xcuitest", booted_count=lambda: 1, web_engine="chromium", which=which
    )
    assert checks == preflight.runnability(
        "xcuitest", which=which, booted_count=lambda: 1, web_engine="chromium"
    )
    names = [c.name for c in checks]
    assert "xcodebuild" in names and "Simulator booted" in names
