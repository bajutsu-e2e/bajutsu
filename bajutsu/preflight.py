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

from bajutsu import idb_version
from bajutsu.idb_version import IdbVersions

Which = Callable[[str], str | None]
Probe = Callable[[], bool]

# backend -> the extra CLIs an on-device run needs beyond Xcode's `xcrun`.
_BACKEND_TOOLS: dict[str, list[tuple[str, str]]] = {
    "idb": [
        ("idb", "the fb-idb client — `uv sync --extra idb`"),
        ("idb_companion", "`brew install facebook/fb/idb-companion`"),
    ],
}


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
    return _ios_runnability(backend, which, booted_count)


def _ios_runnability(
    backend: str, which: Which, booted_count: Callable[[], int] | None
) -> list[Check]:
    checks = [_tool("xcrun", "Xcode + `xcode-select --install`", which)]
    for exe, hint in _BACKEND_TOOLS.get(backend, []):
        checks.append(_tool(exe, hint, which))
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


def _web_runnability(engine: str, web_pkg: Probe, web_browser: BrowserProbe) -> list[Check]:
    pkg_ok = web_pkg()
    browser_ok = web_browser(engine)
    return [
        Check(
            "playwright",
            pkg_ok,
            "installed" if pkg_ok else "the Playwright package — `uv sync --extra web`",
        ),
        Check(
            f"{engine} browser",
            browser_ok,
            "installed" if browser_ok else f"`uv run playwright install {engine}`",
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


def passed(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def render(checks: list[Check]) -> str:
    return "\n".join(f"  {'✓' if c.ok else '✗'} {c.name}: {c.detail}" for c in checks)
