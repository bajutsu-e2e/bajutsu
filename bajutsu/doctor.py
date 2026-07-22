"""Convention score for an app — how ready it is to be tested, plus the screen probe that feeds it.

Pure scoring computed from a screen (a list of Element): id coverage over
actionable elements, namespace conformance, and id uniqueness. AI is not
involved. The screen probe (`probe_screen`) that reads that screen from the device is
shared by the CLI and serve doctors (BE-0199); the environment/connection *gates* that decide
whether to probe stay with each caller (they need a device and their own UX).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from bajutsu import simctl
from bajutsu.backends import make_driver
from bajutsu.config import Effective, require_web, web_base_url
from bajutsu.drivers import base

# Traits that count as "actionable" (the denominator for id coverage).
ACTIONABLE_TRAITS = {
    "button",
    "link",
    "textField",
    "searchField",
    "textView",
    "switch",
    "slider",
    "tab",
    "cell",
}

OK_COVERAGE = 0.9
FAIL_COVERAGE = 0.7


@dataclass(frozen=True)
class Score:
    """The current screen's accessibility-convention score — id coverage, conformance, and grade."""

    actionable: int
    with_id: int
    id_coverage: float
    namespace_conformance: float
    duplicate_ids: int
    grade: str  # "Ready" | "Partial" | "Blocked"
    # Nothing actionable on the screen (likely blank / not loaded / wrong screen).
    no_actionable: bool
    missing_id: list[base.Element]  # actionable elements without an id
    off_namespace: list[str]  # ids whose first segment is not a declared namespace
    duplicates: list[str]  # ids that appear 2+ times on the screen


def _is_actionable(el: base.Element) -> bool:
    return bool(set(el["traits"]) & ACTIONABLE_TRAITS)


def namespace_of(identifier: str) -> str:
    """The id's namespace — the first segment before a '.' (e.g. `auth.email` → `auth`)."""
    return identifier.split(".", 1)[0]


def score(
    elements: list[base.Element],
    id_namespaces: list[str],
    *,
    ok_coverage: float = OK_COVERAGE,
    fail_coverage: float = FAIL_COVERAGE,
) -> Score:
    """Compute the convention score for one screen.

    Args:
        elements: the screen's element tree from ``driver.query()``.
        id_namespaces: declared namespaces for namespace-conformance scoring.
        ok_coverage: id coverage >= this is eligible for "Ready" (configurable via
            ``defaults.doctor.idCoverageOk``, BE-0024).
        fail_coverage: id coverage < this drops to "Blocked" (configurable via
            ``defaults.doctor.idCoverageFail``, BE-0024).
    """
    actionable = [e for e in elements if _is_actionable(e)]
    with_id = sum(1 for e in actionable if e["identifier"])
    id_coverage = with_id / len(actionable) if actionable else 1.0

    ids: list[str] = []
    for e in elements:
        ident = e["identifier"]
        if ident:
            ids.append(ident)

    namespaces = set(id_namespaces)
    if namespaces and ids:
        conforming = sum(1 for i in ids if namespace_of(i) in namespaces)
        namespace_conformance = conforming / len(ids)
        off_namespace = [i for i in ids if namespace_of(i) not in namespaces]
    else:
        namespace_conformance = 1.0
        off_namespace = []

    seen: set[str] = set()
    duplicates: list[str] = []
    for i in ids:
        if i in seen and i not in duplicates:
            duplicates.append(i)
        seen.add(i)

    # No actionable elements means nothing is addressable, so the screen can't be "Ready" — the
    # 1.0 coverage default would otherwise grade a blank / not-yet-loaded / wrong screen as Ready.
    no_actionable = not actionable
    if no_actionable or duplicates or id_coverage < fail_coverage:
        grade = "Blocked"
    elif id_coverage >= ok_coverage and namespace_conformance >= 1.0:
        grade = "Ready"
    else:
        grade = "Partial"

    return Score(
        actionable=len(actionable),
        with_id=with_id,
        id_coverage=id_coverage,
        namespace_conformance=namespace_conformance,
        duplicate_ids=len(duplicates),
        grade=grade,
        no_actionable=no_actionable,
        missing_id=[e for e in actionable if not e["identifier"]],
        off_namespace=off_namespace,
        duplicates=duplicates,
    )


def render(s: Score) -> str:
    """Human-readable summary that points at what to fix."""
    lines = [
        f"grade: {s.grade}",
        f"idCoverage: {s.id_coverage:.2f} ({s.with_id}/{s.actionable})",
        f"namespaceConformance: {s.namespace_conformance:.2f}",
        f"duplicateIds: {s.duplicate_ids}",
    ]
    if s.no_actionable:
        lines.append(
            "  no actionable elements found — is the app on the expected screen and fully loaded?"
        )
    lines.extend(
        f"  missing id: label={e['label']!r} traits={e['traits']} frame={e['frame']}"
        for e in s.missing_id
    )
    if s.off_namespace:
        lines.append(f"  off-namespace ids: {s.off_namespace}")
    if s.duplicates:
        lines.append(f"  duplicate ids: {s.duplicates}")
    return "\n".join(lines)


class DoctorProbeError(RuntimeError):
    """The screen can't be probed for scoring — a fixable config error, not a crash.

    Raised only for the config-level "can't even attempt the probe" case (e.g. a web target
    with no baseUrl). The caller maps it to its own surface (CLI: `typer.Exit(2)`; serve:
    `ValueError`). Device/reachability faults keep raising their transport error (`DeviceError`,
    a Playwright error), which the callers already handle distinctly.
    """


def _first_udid(udid: str) -> str:
    """The first UDID of a possibly comma-separated list (the /api/run parallel-worker format).

    doctor scores one screen, so it targets one device; passing "A,B" on to resolve/make_driver
    would treat the whole string as one invalid UDID. Empty falls back to "booted".
    """
    return (udid.split(",")[0].strip() if udid else "") or "booted"


def probe_screen(
    actuator: str,
    udid: str,
    eff: Effective,
    *,
    simctl_run: simctl.RunFn = simctl._real_run,
) -> list[base.Element]:
    """Query the current screen's elements to score, backend by backend (shared, BE-0199).

    Web (Playwright) navigates a fresh browser to the target's baseUrl (the `launch` equivalent)
    and scores that page, tearing the browser down after; iOS brings up a short-lived XCUITest runner
    on the booted Simulator, scores the launched app's tree, then tears the runner down (BE-0290);
    Android reads whatever is on the attached device, at the resolved udid.

    Args:
        actuator: the selected backend (xcuitest / playwright / adb / fake).
        udid: the device to target; a comma-list (the /api/run parallel format) is narrowed to its
            first entry, since doctor scores one screen.
        eff: the resolved target config (supplies the web baseUrl / browser knobs).
        simctl_run: how to invoke simctl — serve routes it through its host-safe runner (so it
            never shells out on a host without Xcode) and tests inject a fake; the CLI uses the
            default real runner.

    Raises:
        DoctorProbeError: the target is misconfigured for probing (a web target with no baseUrl).
    """
    if actuator == "playwright":
        # A missing baseUrl (or a non-web target forced onto playwright) is a clean, fixable config
        # error, not a crash — `web_base_url` is None for both, so this raises before the
        # `require_web` narrowing below (which a present baseUrl guarantees is a WebConfig). In
        # practice the caller's config gate already caught it; this is the defensive backstop.
        base_url = web_base_url(eff)
        if not base_url:
            raise DoctorProbeError("web target needs baseUrl (set targets.<name>.baseUrl)")
        web = require_web(eff)
        # Lazy import keeps Playwright (a heavy optional dep) off the default path.
        from bajutsu.drivers.playwright import PlaywrightDriver, _playwright_error_types

        # Probe the same device mode the run will use (BE-0228): a mobile context can render a
        # different element tree, so the id-coverage doctor assesses the real target.
        driver = PlaywrightDriver(
            base_url, headless=web.headless, browser=web.browser, device_mode=web.device_mode
        )
        try:
            driver.navigate()
            return driver.query()
        finally:
            # Suppress browser-side errors on teardown so a close() failure during a faulted
            # browser does not mask the original navigate/query exception.
            with contextlib.suppress(*_playwright_error_types()):
                driver.close()
    if actuator == "fake":
        # The fake driver needs no device, so it must not touch simctl — resolving a udid would
        # shell out to `xcrun` and fail on a host without Xcode (e.g. the Linux gate).
        return make_driver("fake", _first_udid(udid)).query()
    first = _first_udid(udid)
    if actuator == "xcuitest":
        # idb read the tree with no runner (BE-0019); with idb retired (BE-0290), doctor brings a
        # short-lived XCUITest runner up, scores the launched app's tree, and tears it down — outside
        # the runner-reuse pool, so it never regresses into a persistent per-run startup. Lazy import:
        # `platform_lifecycle` imports `namespace_of` from this module, so a top-level one would cycle.
        from bajutsu.platform_lifecycle.read_session import ios_read_session

        with ios_read_session(first, eff, simctl_run) as ios_driver:
            return ios_driver.query()
    # How the device handle resolves is the platform's, behind the Environment seam (BE-0256): the
    # Android family via adb, at the resolved udid. Lazy import for the same import-cycle reason.
    from bajutsu.platform_lifecycle import environment_for

    resolved = environment_for(actuator, first, simctl_run).resolve_device(first)
    return make_driver(actuator, resolved).query()
