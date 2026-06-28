"""Backend selection and driver construction.

A backend token names either a **platform** (`ios` / `android` / `web` / `fake`) or a
concrete **actuator** (e.g. `idb`). A platform expands to its actuators in stability order;
the chosen actuator is the first one that is implemented and available in this environment.

So `--backend ios` (or `backend: [ios]` in config) resolves to `idb` today, and will pick up
a richer iOS actuator (XCUITest) when one lands — without the scenario or config changing.
`android` / `web` are declared so they can be requested now and slot in when built; until then
requesting them fails with a clear "not implemented yet" pointing at the design. Unknown tokens
are skipped (forward-compat: an older build can run a config that lists a future backend).

See `docs/multi-platform.md` for the per-platform actuator/environment/id design.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.drivers.idb import IdbDriver

# Platform token -> its actuators, most-stable-first. `--backend` / config `backend` accept
# either a platform token (these keys) or a bare actuator name (the values below).
PLATFORMS: dict[str, tuple[str, ...]] = {
    "ios": ("idb",),  # later: ("xcuitest", "idb")
    "android": ("adb",),
    "web": ("playwright",),
    "fake": ("fake",),
}

# Every actuator the registry knows about (implemented or planned), de-duplicated in order.
KNOWN_ACTUATORS: tuple[str, ...] = tuple(
    dict.fromkeys(a for actuators in PLATFORMS.values() for a in actuators)
)

# Actuators with a driver today. Requesting a planned-but-absent one (adb) gives a
# "not implemented yet" error instead of a generic failure.
IMPLEMENTED: frozenset[str] = frozenset({"idb", "fake", "playwright"})

# Which executable backs each actuator (the coarse availability check). `fake` needs none;
# `playwright` is a Python package (probed by import), not a PATH executable.
_EXECUTABLE = {"idb": "idb", "adb": "adb"}


def _playwright_available() -> bool:
    """Whether the `playwright` package is installed.

    Checked without importing it, so the default import path stays free of the heavy
    dependency; see tests/serve/test_import_guard.py.
    """
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def default_available(actuator: str) -> bool:
    """Whether the actuator is implemented and its backing tool is present.

    `fake` is always available; `playwright` is gated on the python package, every other
    actuator on a PATH executable.
    """
    if actuator not in IMPLEMENTED:
        return False
    if actuator == "fake":
        return True
    if actuator == "playwright":
        return _playwright_available()
    exe = _EXECUTABLE.get(actuator)
    return exe is not None and shutil.which(exe) is not None


def _expand(token: str) -> tuple[str, ...]:
    """A platform token expands to its actuators; a bare actuator stands for itself."""
    return PLATFORMS.get(token, (token,))


def resolve_actuators(backends: list[str]) -> list[str]:
    """Expand each backend token (platform alias or actuator) to actuator names, in order."""
    return [a for token in backends for a in _expand(token)]


def select_actuator(
    backends: list[str], available: Callable[[str], bool] = default_available
) -> str:
    """First implemented + available actuator for the requested platforms/actuators."""
    actuators = resolve_actuators(backends)
    for a in actuators:
        if a in KNOWN_ACTUATORS and available(a):
            return a
    # Distinguish "recognized but not built yet" from "available but absent" for a useful error.
    planned = sorted({a for a in actuators if a in KNOWN_ACTUATORS and a not in IMPLEMENTED})
    if planned:
        raise RuntimeError(
            f"backend(s) {planned} are recognized but not implemented yet "
            f"(see docs/multi-platform.md); requested {backends}"
        )
    raise RuntimeError(f"no available actuator among {actuators} (requested {backends})")


def ensure_web_runtime(backends: list[str], browser: str = "chromium") -> None:
    """Provision the web backend (and the requested engine) on demand.

    When a `web`/`playwright` backend is requested but the Playwright package is absent
    (e.g. the venv currently carries the idb extra, as after `make serve`), install it
    *additively* — `uv pip install` so it doesn't evict idb. Then, whether or not the package was
    just added, install the engine this run needs (`playwright install <browser>`). `playwright
    install` is idempotent — a present browser is a fast no-op and a missing one is fetched — so a
    `firefox` / `webkit` run pulls its binary on first use without disturbing Chromium (BE-0076).

    A no-op unless web is requested; mirrors how `make serve` provisions idb on demand. The
    deterministic run/CI gate drives the fake backend and never reaches this.
    """
    if "playwright" not in resolve_actuators(backends):
        return
    import importlib
    import subprocess
    import sys

    pkg_missing = not _playwright_available()
    try:
        if pkg_missing:
            sys.stderr.write(
                "bajutsu: web backend requested but Playwright is not installed — installing it "
                "now (uv pip install playwright). This runs once per environment.\n"
            )
            sys.stderr.flush()
            subprocess.run(["uv", "pip", "install", "playwright"], check=True)
            importlib.invalidate_caches()  # so find_spec() in select_actuator sees the new package
        subprocess.run([sys.executable, "-m", "playwright", "install", browser], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise RuntimeError(
            "failed to auto-install the web backend (Playwright). Install it manually with "
            f"`uv pip install playwright && uv run playwright install {browser}`, or `uv sync "
            "--extra web`."
        ) from e


def capabilities_for(actuator: str) -> frozenset[str]:
    """The static capability set a backend advertises.

    Read without constructing a driver, so the preflight (BE-0082) needs no device (idb)
    or browser (playwright). Same source as `Driver.capabilities()`: each driver's
    `CAPABILITIES` class constant.
    """
    if actuator == "idb":
        return IdbDriver.CAPABILITIES
    if actuator == "fake":
        return FakeDriver.CAPABILITIES
    if actuator == "playwright":
        # Lazy import (heavy optional dep) — only reached on a web run; reading the class constant
        # does not start a browser (only constructing PlaywrightDriver does).
        from bajutsu.drivers.playwright import PlaywrightDriver

        return PlaywrightDriver.CAPABILITIES
    if actuator in KNOWN_ACTUATORS:
        raise NotImplementedError(
            f"backend {actuator!r} is planned but not implemented yet (see docs/multi-platform.md)"
        )
    raise ValueError(f"unknown backend: {actuator!r}")


def make_driver(
    actuator: str,
    udid: str,
    *,
    base_url: str | None = None,
    headless: bool = True,
    browser: str = "chromium",
    record_video_dir: Path | None = None,
) -> base.Driver:
    """Construct the driver for an actuator, wiring up its backend-specific arguments."""
    if actuator == "idb":
        return IdbDriver(udid)
    if actuator == "fake":
        return FakeDriver([])
    if actuator == "playwright":
        # Lazy: keep Playwright (a heavy optional dep) off the default import path.
        from bajutsu.drivers.playwright import PlaywrightDriver

        if not base_url:
            raise ValueError("web backend requires base_url (set apps.<app>.baseUrl)")
        return PlaywrightDriver(
            base_url, headless=headless, browser=browser, record_video_dir=record_video_dir
        )
    if actuator in KNOWN_ACTUATORS:
        raise NotImplementedError(
            f"backend {actuator!r} is planned but not implemented yet (see docs/multi-platform.md)"
        )
    raise ValueError(f"unknown backend: {actuator!r}")
