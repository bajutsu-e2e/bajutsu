"""Backend selection and driver construction.

A backend token names either a **platform** (`ios` / `android` / `web` / `fake`) or a
concrete **actuator** (e.g. `xcuitest`). A platform expands to its actuators in stability order;
the chosen actuator is the first one that is implemented and available in this environment.

So `--backend ios` (or `backend: [ios]` in config) resolves to `xcuitest` — without the scenario
or config changing. A platform not yet backed by any actuator (declared in `KNOWN_ACTUATORS` but
not `IMPLEMENTED`, e.g. a future Flutter bridge) can still be requested; it fails with a clear
"not implemented yet" instead of a generic error. Unknown tokens are skipped (forward-compat: an
older build can run a config that lists a future backend).

See `docs/vision.md` for the per-platform actuator/environment/id design.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver

if TYPE_CHECKING:
    from bajutsu.config import Effective
    from bajutsu.scenario import Scenario

# Platform token -> its actuators, most-stable-first. `--backend` / config `backend` accept
# either a platform token (these keys) or a bare actuator name (the values below).
PLATFORMS: dict[str, tuple[str, ...]] = {
    "ios": ("xcuitest",),  # XCUITest is the sole iOS backend (BE-0290; idb retired)
    "android": ("adb",),
    "web": ("playwright",),
    "fake": ("fake",),
}

# Platform token -> its actuators, cheapest-first (BE-0240). The *cost* order — which actuator is
# cheapest to run (fewest dependencies, no resident runner) — answers a different question than
# `PLATFORMS`' *stability* order (which is most capable), so it is its own explicit table rather
# than a `reversed(PLATFORMS[...])`: a future platform with 3+ actuators need not have the two be
# exact reverses. Only platforms whose cost order differs from their stability order need an entry;
# `select_actuator_for_scenario` falls back to the resolved order for any platform absent here.
# Empty since BE-0290 retired idb: iOS now has one actuator, so no platform's cost order differs
# from its stability order. A future multi-actuator platform re-populates this table.
COST_ORDER: dict[str, tuple[str, ...]] = {}

# Every actuator the registry knows about (implemented or planned), de-duplicated in order.
KNOWN_ACTUATORS: tuple[str, ...] = tuple(
    dict.fromkeys(a for actuators in PLATFORMS.values() for a in actuators)
)

# Actuators with a driver today. Requesting a planned-but-absent one gives a
# "not implemented yet" error instead of a generic failure.
IMPLEMENTED: frozenset[str] = frozenset({"fake", "playwright", "xcuitest", "adb"})

# Which executable backs each actuator (the coarse availability check). `fake` needs none;
# `playwright` is a Python package (probed by import), not a PATH executable; `xcuitest` is gated
# on `xcodebuild` in `default_available`.
_EXECUTABLE = {"adb": "adb"}


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
    if actuator == "xcuitest":
        return shutil.which("xcodebuild") is not None
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
            f"(see docs/vision.md); requested {backends}"
        )
    raise RuntimeError(f"no available actuator among {actuators} (requested {backends})")


def ensure_web_runtime(backends: list[str], browser: str = "chromium") -> None:
    """Provision the web backend (and the requested engine) on demand.

    When a `web`/`playwright` backend is requested but the Playwright package is absent
    (e.g. the venv carries only the base install), install it *additively* — `uv pip install` so it
    doesn't evict another backend's extra. Then, whether or not the package was just added, install
    the engine this run needs (`playwright install <browser>`). `playwright install` is idempotent —
    a present browser is a fast no-op and a missing one is fetched — so a `firefox` / `webkit` run
    pulls its binary on first use without disturbing Chromium (BE-0076).

    A no-op unless web is requested; mirrors how `make serve` provisions a backend's deps on demand.
    The deterministic run/CI gate drives the fake backend and never reaches this.
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

    Read without constructing a driver, so the preflight (BE-0082) needs no device (Simulator)
    or browser (playwright). Same source as `Driver.capabilities()`: each driver's
    `CAPABILITIES` class constant.
    """
    if actuator == "fake":
        return FakeDriver.CAPABILITIES
    if actuator == "playwright":
        # Lazy import (heavy optional dep) — only reached on a web run; reading the class constant
        # does not start a browser (only constructing PlaywrightDriver does).
        from bajutsu.drivers.playwright import PlaywrightDriver

        return PlaywrightDriver.CAPABILITIES
    if actuator == "xcuitest":
        # The iOS actuator's capabilities are readable without bringing its runner up: reading the
        # class constant constructs no driver and starts no runner.
        from bajutsu.drivers.xcuitest import XcuitestDriver

        return XcuitestDriver.CAPABILITIES
    if actuator == "adb":
        from bajutsu.drivers.adb import AdbDriver

        return AdbDriver.CAPABILITIES
    if actuator in KNOWN_ACTUATORS:
        raise NotImplementedError(
            f"backend {actuator!r} is planned but not implemented yet (see docs/vision.md)"
        )
    raise ValueError(f"unknown backend: {actuator!r}")


def capabilities_for_run(
    actuator: str, eff: Effective, udid_spec: str = "booted"
) -> frozenset[str]:
    """The capability set for one run, narrowing the static set to the run's device target (BE-0238).

    `capabilities_for` returns a backend's *static* capabilities. Two XCUITest device targets narrow
    it, each so preflight (BE-0082) skips an unrunnable scenario up front instead of failing late:

    - **A live WebDriver endpoint** drives a reserved device through Appium's XCUITest `mobile:`
      commands, not simctl and not the native text selection the local runner does — so it advertises
      exactly what that transport drives, the live driver's own `CAPABILITIES` (the single source of
      truth). This is the narrower of the two, dropping text selection on top of the real-device
      narrowing below. The signal is `udid_spec` being a WebDriver URL — the *same* `is_webdriver_endpoint`
      check `environment_for` routes on, so preflight and routing can never disagree (whether the URL
      arrives from the `appium` provider's endpoint or a raw `--udid https://…` under the local provider).
    - **A real device via `xcuitest.deviceType: device`** loses the simctl-backed `DeviceControl`
      family and the simctl-privacy permission grants (simctl cannot reach a physical device), the
      same fail-fast the permission preconditions already get in the XCUITest lifecycle (Unit 1). This
      keys on config (`deviceType`), which a udid spec cannot express, so it stays an `eff` check.

    Every other backend, and the Simulator default, is unchanged.
    """
    # Lazy import: `bajutsu.config` imports this module (`resolve` -> `platform_of`), so a top-level
    # import would close the cycle. By call time config is fully loaded.
    from bajutsu.config import xcuitest_targets_real_device

    caps = capabilities_for(actuator)
    if actuator == "xcuitest":
        # Route on the resolved udid spec with the very predicate `environment_for` uses, so the
        # advertised set and the environment actually chosen stay in lockstep — a capability the
        # WebDriver transport cannot drive can never be advertised here yet raise `UnsupportedAction`
        # at run time. Lazy import keeps this module clear of the platform-lifecycle layer at import.
        from bajutsu.platform_lifecycle.environments.xcuitest_live import is_webdriver_endpoint

        if is_webdriver_endpoint(udid_spec):
            from bajutsu.drivers.xcuitest_live import XcuitestLiveDriver

            return XcuitestLiveDriver.CAPABILITIES
        if xcuitest_targets_real_device(eff):
            return caps - base.DEVICE_CONTROL_ALL - base.IOS_PERMISSION_CAPABILITIES
    return caps


def _cost_ordered(actuators: list[str]) -> list[str]:
    """Reorder *actuators* cheapest-first per each one's platform `COST_ORDER` (BE-0240).

    Stable: an actuator whose platform has no `COST_ORDER` entry (or that entry omits it) keeps its
    original relative position, so a single-platform ladder is reordered while anything unranked is
    left as resolved.
    """
    unranked = len(actuators)  # sorts any actuator with no cost rank after the ranked ones

    def key(item: tuple[int, str]) -> tuple[int, int]:
        i, a = item
        platform = platform_of(a)
        order = COST_ORDER.get(platform, ()) if platform is not None else ()
        return (order.index(a) if a in order else unranked, i)

    return [a for _, a in sorted(enumerate(actuators), key=key)]


def _cost_ordered_available(
    backends: list[str], available: Callable[[str], bool]
) -> list[str] | None:
    """The cost-ordered available candidates for *backends*, or None to defer to `select_actuator`.

    The shared prefix of both cost-first selectors (BE-0240 / BE-0267): resolve the requested
    backends to a deduped candidate list, cost-order it (cheapest first), and keep the ones that are
    both known and available. Returns `None` — meaning "delegate to `select_actuator(backends,
    available)`" — for the two cases both callers treat identically: a single resolved candidate (a
    hard pin, no reordering) and no available candidate at all (reuse `select_actuator`'s precise
    planned-vs-absent diagnostic, which raises). A non-empty list is otherwise never returned empty.
    """
    candidates = list(dict.fromkeys(resolve_actuators(backends)))
    if len(candidates) <= 1:
        return None
    avail = [a for a in _cost_ordered(candidates) if a in KNOWN_ACTUATORS and available(a)]
    return avail or None


def select_actuator_cost_first(
    backends: list[str],
    available: Callable[[str], bool] = default_available,
) -> str:
    """The cheapest available actuator among the requested backends, without a scenario (BE-0267).

    A live serve capture/enrich session drives one device and needs no capability escalation — only
    the cheapest actuator it can actually bring up. This is `select_actuator_for_scenario` without
    the scenario: cost order (cheapest first) over the resolved candidates, first available wins. With
    `COST_ORDER` empty (BE-0290 retired idb, so no platform has a cost order differing from stability),
    this now returns the first available candidate in the resolved order — `[ios]` selects XCUITest.

    A single resolved candidate is a hard pin: it delegates to `select_actuator`, keeping its
    planned/absent diagnostics and any explicit-actuator error (e.g. XCUITest's runner requirement),
    consistent with `select_actuator_for_scenario`'s single-actuator rule.
    """
    avail = _cost_ordered_available(backends, available)
    if avail is None:
        return select_actuator(backends, available)  # hard pin / none available (may raise)
    return avail[0]


def select_actuator_for_scenario(
    backends: list[str],
    scenario: Scenario,
    available: Callable[[str], bool] = default_available,
    caps: Callable[[str], frozenset[str]] = capabilities_for,
) -> str:
    """The cheapest available actuator that can run *scenario* (BE-0240).

    Reuses `capability_preflight.unsupported` — the pure `(scenario, capability set)` function
    BE-0082 already computes — against each candidate's capability set, in cost order (cheapest
    first), and returns the first candidate that is both available and sufficient; it escalates to a
    richer actuator only when the cheaper one can't run the scenario.

    An explicit single-actuator request (or a single-actuator platform) is a hard pin with no
    capability escalation, exactly like `select_actuator` and consistent with `--backend <one>`
    behaving like `--udid` (DESIGN §3.3): this scenario-aware selection only activates when the
    requested backends resolve to more than one candidate. Since BE-0290 retired idb, iOS is a single
    actuator and always takes the hard-pin path; the escalation logic stays for a future
    multi-actuator platform (`backend: [<a>, <b>]`).

    When candidates are available but none is sufficient, the **richest** available one is returned
    so the caller's preflight fails it with the fewest-gap (most informative) reasons; when none is
    available at all, `select_actuator`'s precise "planned vs absent" error is raised.
    """
    avail = _cost_ordered_available(backends, available)
    if avail is None:
        return select_actuator(backends, available)  # hard pin / none available (may raise)
    # Lazy import: keeps this module's import free of the scenario/preflight graph (used only here).
    from bajutsu import capability_preflight

    for actuator in avail:
        if not capability_preflight.unsupported(scenario, caps(actuator)):
            return actuator
    return avail[-1]  # richest available (cost order, cheapest first); preflight names its gaps


def make_driver(
    actuator: str,
    udid: str,
    *,
    base_url: str | None = None,
    headless: bool = True,
    browser: str = "chromium",
    device_mode: str = "desktop",
    record_video_dir: Path | None = None,
    runner_port: int = 0,
    fetch_hierarchy: Callable[[], str] | None = None,
) -> base.Driver:
    """Construct the driver for an actuator, wiring up its backend-specific arguments.

    The adb backend reads through `fetch_hierarchy` (the resident-server channel, BE-0245) when one is
    supplied, else via `uiautomator dump`; every other actuator ignores it.
    """
    if actuator == "adb":
        from bajutsu.drivers.adb import AdbDriver

        return AdbDriver(udid, fetch_hierarchy=fetch_hierarchy)
    if actuator == "fake":
        return FakeDriver([])
    if actuator == "xcuitest":
        if runner_port <= 0:
            raise ValueError(
                "xcuitest backend requires a runner_port (the runner must be started first)"
            )
        from bajutsu.drivers.xcuitest import XcuitestDriver

        return XcuitestDriver(host="127.0.0.1", port=runner_port)
    if actuator == "playwright":
        # Lazy: keep Playwright (a heavy optional dep) off the default import path.
        from bajutsu.drivers.playwright import PlaywrightDriver

        if not base_url:
            raise ValueError("web backend requires base_url (set apps.<app>.baseUrl)")
        return PlaywrightDriver(
            base_url,
            headless=headless,
            browser=browser,
            device_mode=device_mode,
            record_video_dir=record_video_dir,
        )
    if actuator in KNOWN_ACTUATORS:
        raise NotImplementedError(
            f"backend {actuator!r} is planned but not implemented yet (see docs/vision.md)"
        )
    raise ValueError(f"unknown backend: {actuator!r}")


# Evidence *kind* -> the capability that supplies it natively (BE-0020). Today only network; a
# kind whose capability the actuator lacks is filled read-only by a same-platform provider below.
KIND_CAPABILITY: dict[str, str] = {"network": base.Capability.NETWORK}


def _platform_of(actuator: str, platforms: dict[str, tuple[str, ...]]) -> str | None:
    """The platform whose actuator set contains *actuator* (reverse lookup)."""
    return next((p for p, acts in platforms.items() if actuator in acts), None)


def platform_of(actuator: str) -> str | None:
    """The platform an actuator belongs to (`xcuitest` -> `ios`, `playwright` -> `web`), or None if unknown.

    Lets config derive a target's platform from its backend (BE-0009 Slice 4).
    """
    return _platform_of(actuator, PLATFORMS)


def evidence_backends(
    backends: list[str],
    actuator: str,
    available: Callable[[str], bool] = default_available,
    platforms: dict[str, tuple[str, ...]] = PLATFORMS,
) -> list[str]:
    """The read-only evidence providers for *actuator* (BE-0020).

    Returns the remaining available actuators on the actuator's own platform, in `backends` order
    (deduped, the actuator excluded). Eligibility is *same system under test*: only a same-platform
    backend observes the
    same running app, so a cross-platform token (e.g. `web` for an iOS run) is never a provider.
    `platforms` is injectable so the resolver is unit-testable before a platform has two actuators.
    """
    platform = _platform_of(actuator, platforms)
    if platform is None:
        return []
    siblings = set(platforms.get(platform, ()))
    out: list[str] = []
    for token in backends:
        for a in platforms.get(token, (token,)):
            if a != actuator and a in siblings and available(a) and a not in out:
                out.append(a)
    return out


def resolve_evidence_providers(
    backends: list[str],
    actuator: str,
    available: Callable[[str], bool] = default_available,
    caps: Callable[[str], frozenset[str]] = capabilities_for,
    platforms: dict[str, tuple[str, ...]] = PLATFORMS,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve one read-only provider per evidence gap (BE-0020).

    For each kind the actuator lacks natively (its `caps` miss the kind's capability), pick the
    first eligible same-platform provider that advertises it. Returns ``(providers, skipped)``:
    ``providers`` maps a gap kind to its provider actuator; ``skipped`` maps a gap kind with no
    provider to a recorded reason (graceful degradation, never a run failure).
    """
    actuator_caps = caps(actuator)
    providers = evidence_backends(backends, actuator, available, platforms)
    chosen: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for kind, capability in KIND_CAPABILITY.items():
        if capability in actuator_caps:
            continue  # the actuator supplies it natively — no fallback needed
        provider = next((b for b in providers if capability in caps(b)), None)
        if provider is not None:
            chosen[kind] = provider
        else:
            skipped[kind] = f"no same-platform backend provides {kind} ({capability})"
    return chosen, skipped
