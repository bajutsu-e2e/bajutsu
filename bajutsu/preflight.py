"""Environment runnability gate for `doctor` / CI.

The convention `doctor.score` answers "is the app's screen testable?". This answers the
prior question: "can this host actually drive the chosen backend?" — for iOS, are the CLIs
an on-device run needs present and is a Simulator booted; for web, is Playwright and its
browser installed. Pure checks over injectable probes, so they are testable without
touching the machine.
"""

from __future__ import annotations

import importlib.util
import os.path
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from bajutsu import idb_version, requirements
from bajutsu.idb_version import IdbVersions

Which = Callable[[str], str | None]
Probe = Callable[[], bool]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str  # the path found, or how to fix it


def _tool(exe: str, hint: str, which: Which) -> Check:
    path = which(exe)
    return Check(exe, path is not None, path or hint)


def _playwright_installed() -> bool:  # pragma: no cover - trivial env probe
    """Whether the `playwright` package is importable — without importing it (mirrors
    `backends._playwright_available`, kept local so this light module stays decoupled)."""
    return importlib.util.find_spec("playwright") is not None


def _browser_installed(engine: str) -> bool:  # pragma: no cover - needs the playwright runtime
    """Whether the named Playwright engine is actually installed (the `playwright install` step).
    Reads the computed executable path without launching a browser; missing package → not installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as p:
        launcher = getattr(p, engine, None)
        if launcher is None:  # an unknown engine is "not installed", not a doctor crash
            return False
        return os.path.exists(launcher.executable_path)


# Probe for a specific engine's binary, defaulting to Chromium (BE-0076). Injectable so the web
# checks stay testable without a real Playwright install.
BrowserProbe = Callable[[str], bool]


def config_checks(
    backend: str, *, target: str, bundle_id: str, base_url: str | None, package: str = ""
) -> list[Check]:
    """Whether the resolved target carries the config the selected backend needs to launch.

    The web (`playwright`) backend needs a `baseUrl` to navigate to; the Android (`adb`) backend a
    `package`; the iOS (`idb`) backend a `bundleId`. Config parsing already rejects a target with
    *none* (`_need_target`), but a target can still carry the *wrong* field for the backend it is run
    on — an iOS target with only a `baseUrl`, a web target with only a `bundleId` — which would
    otherwise surface as a confusing downstream launch/navigate failure. This catches it up front
    with a remedy naming the target. `fake` needs neither.

    Args:
        backend: The selected actuator (`idb` / `adb` / `playwright` / `fake`).
        target: The resolved target's name, used in the remedy string.
        bundle_id: The target's `bundleId` (empty when unset).
        base_url: The target's `baseUrl` (None when unset).
        package: The target's Android `package` (empty when unset).
    """
    if backend == "fake":
        return []
    if backend == "playwright":
        return [
            Check("target baseUrl", bool(base_url), base_url or f"set targets.{target}.baseUrl")
        ]
    if backend == "adb":
        return [Check("target package", bool(package), package or f"set targets.{target}.package")]
    return [
        Check("target bundleId", bool(bundle_id), bundle_id or f"set targets.{target}.bundleId")
    ]


def runnability(
    backend: str,
    which: Which = shutil.which,
    booted_count: Callable[[], int] | None = None,
    *,
    web_engine: str = "chromium",
    web_pkg: Probe = _playwright_installed,
    web_browser: BrowserProbe = _browser_installed,
) -> list[Check]:
    """The runnability checks for `backend`, chosen by its platform family. The web
    (`playwright`) backend needs the Playwright package and the selected engine's browser
    (`web_engine`, BE-0076); the iOS (`idb`) backend needs Xcode's `xcrun`, the backend's CLIs, and
    (when `booted_count` is given) a booted Simulator. `fake` needs nothing. The web probes are
    injectable so the checks stay testable without a real Playwright install."""
    if backend == "fake":
        return []
    if backend == "playwright":
        return _web_runnability(web_engine, web_pkg, web_browser)
    if backend == "adb":
        return _android_runnability(which, booted_count)
    return _ios_runnability(backend, which, booted_count)


def _ios_runnability(
    backend: str, which: Which, booted_count: Callable[[], int] | None
) -> list[Check]:
    # The extra CLIs beyond `xcrun` come from the one requirements mapping (BE-0164), so the
    # remedy strings never drift from what the installer actually installs.
    checks = [
        _tool("xcrun", "Xcode + `xcode-select --install`", which),
        *(
            _tool(tool.exe, requirements.remedy(tool.install), which)
            for tool in requirements.BACKENDS.get(backend, requirements.Requirement()).tools
        ),
    ]
    if booted_count is not None:
        count = booted_count()
        checks.append(
            Check(
                "Simulator booted",
                count > 0,
                f"{count} booted" if count else "boot one: `xcrun simctl boot <udid>`",
            )
        )
    return checks


def _android_runnability(which: Which, booted_count: Callable[[], int] | None) -> list[Check]:
    """adb present plus (when `booted_count` is given) an attached, ready device/emulator.

    The adb path has no separate SDK-tool prerequisite (nothing like the XCUITest `xcrun` step); the
    tools come from the one requirements mapping (BE-0164), so the remedy strings never drift from
    what the installer installs.
    """
    checks = [
        _tool(tool.exe, requirements.remedy(tool.install), which)
        for tool in requirements.BACKENDS.get("adb", requirements.Requirement()).tools
    ]
    if booted_count is not None:
        count = booted_count()
        checks.append(
            Check(
                "device attached",
                count > 0,
                # `emulator` ships with the Android SDK, not the `android-platform-tools` `adb`
                # requirement, so the remedy names the SDK tool rather than implying `adb` has it.
                f"{count} attached"
                if count
                else "attach a device, or boot an AVD (Android SDK `emulator`); shows in `adb devices`",
            )
        )
    return checks


def _web_runnability(engine: str, web_pkg: Probe, web_browser: BrowserProbe) -> list[Check]:
    pkg_ok = web_pkg()
    browser_ok = web_browser(engine)
    # Remedy strings come from the one requirements mapping (BE-0164): the `web` extra for the
    # package, a `playwright install <engine>` for the selected engine's browser.
    web_extra = requirements.BACKENDS["playwright"].extra
    pkg_remedy = requirements.remedy(requirements.Extra(web_extra)) if web_extra else ""
    browser_remedy = requirements.remedy(requirements.playwright_browser(engine).install)
    return [
        Check(
            "playwright",
            pkg_ok,
            "installed" if pkg_ok else f"the Playwright package — {pkg_remedy}",
        ),
        Check(
            f"{engine} browser",
            browser_ok,
            "installed" if browser_ok else browser_remedy,
        ),
    ]


def idb_version_check(expected: str | None, versions: IdbVersions) -> Check | None:
    """Compare the installed `idb_companion` against the config-declared range (BE-0005).

    None `expected` means no pin is declared, so there is no line to show. An unreadable installed
    version (idb absent / unparseable) fails the check rather than guessing — the same fail-loudly
    stance as a missing tool. This is a `doctor` pre-flight signal, never a run pass/fail gate."""
    if expected is None:
        return None
    installed = versions.companion
    if installed is None:
        return Check("idb_companion version", False, f"expected {expected}, installed unknown")
    ok = idb_version.satisfies(installed, expected)
    return Check("idb_companion version", ok, f"{installed} (expected {expected})")


def doctor_environment_checks(
    backend: str,
    *,
    booted_count: Callable[[], int],
    web_engine: str,
    ios_pin: str | None,
    which: Which = shutil.which,
    idb_probe: Callable[[], IdbVersions] = idb_version.probe,
) -> list[Check]:
    """The environment checks `doctor` reports for `backend`, shared by the CLI and serve (BE-0199).

    Runnability plus the two doctor-specific augmentations both surfaces must report the same way,
    so the CLI and the serve panel never drift on how thoroughly they answer "is this target
    healthy?": xcuitest also needs idb (it falls back to idb for the screen query, BE-0019), and a
    declared idb version pin (`defaults.idbVersion`) is reported against the installed companion so
    a compatibility break surfaces here, not as a confusing downstream failure (BE-0005).

    Args:
        ios_pin: the declared idb_companion version range, or None when no pin is declared.
        idb_probe: reads the installed idb versions; injectable so the pin check stays testable
            without idb installed. Called only when a pin is declared and the companion is present.
    """
    checks = runnability(backend, which=which, booted_count=booted_count, web_engine=web_engine)
    if backend == "xcuitest":
        seen = {c.name for c in checks}
        checks.extend(
            c
            for c in runnability("idb", which=which, booted_count=booted_count)
            if c.name not in seen
        )
    # Only probe the version when a pin exists *and* the companion is present — runnability already
    # reports a missing companion, so probing it would spawn a doomed subprocess and print a
    # redundant "installed unknown" line.
    companion_ok = any(c.name == "idb_companion" and c.ok for c in checks)
    if backend == "idb" and ios_pin is not None and companion_ok:
        version_check = idb_version_check(ios_pin, idb_probe())
        if version_check is not None:
            checks.append(version_check)
    return checks


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def render(checks: list[Check]) -> str:
    return "\n".join(f"  {'✓' if c.ok else '✗'} {c.name}: {c.detail}" for c in checks)
