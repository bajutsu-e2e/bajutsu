"""Orchestrator — the deterministic Tier2 run loop.

Each step runs as act -> (wait) -> verify. Pass/fail comes from machine
assertions only; no AI is involved. Execution stops at the first failure.

This module is backend-agnostic (via base.Driver): it works with a real driver
or the FakeDriver. Evidence and preconditions / relaunch (env integration) are
wired in later.
"""

from __future__ import annotations

import fnmatch
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from bajutsu import assertions, interp, intervals
from bajutsu.assertions import AssertionResult, VisualContext
from bajutsu.drivers import base
from bajutsu.evidence import Artifact, EvidenceSink, NullSink
from bajutsu.network import NetworkExchange
from bajutsu.scenario import (
    Assertion,
    CaptureRule,
    Extract,
    ForEach,
    Gone,
    If,
    HttpRequest,
    Relaunch,
    Scenario,
    Selector,
    Step,
    Wait,
    WaitRequest,
)

# Returns the network exchanges observed so far (for `request` assertions / waits).
NetworkSource = Callable[[], list[NetworkExchange]]
# Performs an in-scenario app relaunch (terminate + launch). Injected by the runner so the
# orchestrator stays backend-agnostic; None means relaunch is unavailable (e.g. fake driver).
RelaunchFn = Callable[[Relaunch], None]
# Receives a human-readable progress line (e.g. "step 2/5: tap home.title") as the run advances.
# Injected from the CLI (`--progress`) so the web UI can stream per-scenario/step progress; None
# (the default everywhere) keeps the pipeline silent.
ProgressFn = Callable[[str], None]


class DeviceControl(Protocol):
    """Device-environment operations a step may trigger (simctl-backed). Injected by the
    runner so the orchestrator stays backend-agnostic; None means unavailable (the fake
    driver, or parallel runs which don't pin a single device)."""

    def set_location(self, lat: float, lon: float) -> None: ...
    def push(self, payload: dict[str, object]) -> None: ...
    def clear_keychain(self) -> None: ...
    def clear_clipboard(self) -> None: ...
    def home(self) -> None: ...
    def override_status_bar(self, **kwargs: str | int) -> None: ...
    def clear_status_bar(self) -> None: ...


def _no_network() -> list[NetworkExchange]:
    return []


_SWIPE_DIST = 100.0
_POLL = 0.05

# Always captured, regardless of capturePolicy: an after-screenshot and the element
# tree per step (instant), and these interval recordings for the whole scenario.
_BASELINE_INSTANT = ("screenshot.after", "elements")
_SCENARIO_INTERVALS = ("video", "deviceLog", "appTrace")


class Clock(Protocol):
    """Time and sleep (swappable in tests to make waits deterministic)."""

    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class AlertEvent:
    """A system prompt the guard dismissed so a blocked step/expect could proceed.

    Recorded on the outcome (StepOutcome.alerts / RunResult.expect_alerts) and surfaced in
    the report, so a step that only passed on a retry isn't shown as if nothing had blocked
    it. `label` is the button the guard tapped (e.g. "Not Now"); empty when the locator
    named none."""

    label: str = ""


@dataclass
class StepOutcome:
    index: int
    action: str
    ok: bool = True
    reason: str = ""
    duration_s: float = 0.0
    started_at: float = 0.0  # offset (s) from the scenario video's start, for video sync
    assertion_results: list[AssertionResult] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    # System prompts the guard cleared before this step succeeded (usually 0 or 1).
    alerts: list[AlertEvent] = field(default_factory=list)


@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult] = field(default_factory=list)
    failure: str | None = None
    # Scenario-level artifacts (the always-on screen recording, etc.).
    artifacts: list[Artifact] = field(default_factory=list)
    # Which backend (actuator) drove this scenario: "idb" / "fake".
    backend: str = ""
    # The simulator udid this scenario ran on — shows how a parallel pool split the work.
    device: str = ""
    # The simulator's device model / OS runtime (e.g. "iPhone 15" / "iOS 17.2"), for the
    # report's Environment tab; empty when not resolvable (e.g. the fake driver).
    device_name: str = ""
    device_runtime: str = ""
    # Wall-clock the scenario took end to end (steps + verification), for the report.
    duration_s: float = 0.0
    # System prompts the guard cleared before the scenario-level `expect` re-checked.
    expect_alerts: list[AlertEvent] = field(default_factory=list)


def scenario_slug(name: str) -> str:
    """A filesystem-safe id derived from a scenario name (for its evidence dir)."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower()
    return slug or "scenario"


def _action_of(step: Step) -> str:
    for a in (
        "tap",
        "double_tap",
        "long_press",
        "type",
        "swipe",
        "pinch",
        "rotate",
        "wait",
        "assert_",
        "relaunch",
        "set_location",
        "push",
        "http",
        "clear_keychain",
        "clear_clipboard",
        "background",
        "override_status_bar",
        "clear_status_bar",
        "if_",
        "for_each",
    ):
        if getattr(step, a) is not None:
            return a
    raise AssertionError("no valid action on step (guaranteed by scenario validation)")


def _selector_hint(obj: object) -> str:
    """A short target string for a progress label — the first id/label found on an action object
    or its nested selector (e.g. `type`'s `into`, `swipe`'s `on`). Empty when nothing identifies
    it. Never returns typed text (kept out of progress so secrets don't leak)."""
    for attr in ("id", "label", "id_matches", "label_matches"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    for attr in ("into", "on", "sel", "of", "within"):
        nested = getattr(obj, attr, None)
        if nested is not None:
            hint = _selector_hint(nested)
            if hint:
                return hint
    return ""


def _step_label(step: Step, kind: str) -> str:
    """A concise description of a step for progress output: the step's own `name` if set,
    otherwise the action kind plus its target id/label (e.g. "tap home.title")."""
    if step.name:
        return step.name
    hint = _selector_hint(getattr(step, kind))
    pretty = kind.rstrip("_").replace("_", " ")
    return f"{pretty} {hint}".strip()


def _center(frame: base.Frame) -> base.Point:
    x, y, w, h = frame
    return (x + w / 2, y + h / 2)


def _target(center: base.Point, direction: str) -> base.Point:
    cx, cy = center
    if direction == "up":
        return (cx, cy - _SWIPE_DIST)
    if direction == "down":
        return (cx, cy + _SWIPE_DIST)
    if direction == "left":
        return (cx - _SWIPE_DIST, cy)
    return (cx + _SWIPE_DIST, cy)  # right


def _exists(elements: list[base.Element], sel: base.Selector) -> bool:
    return len(base.find_all(elements, sel)) >= 1


def _adaptive_sleep(clock: Clock, before: float) -> None:
    """Sleep only the remainder of _POLL after subtracting time already spent (e.g. in query).

    When `driver.query()` is backed by a subprocess (idb describe-all ≈ 100-300ms), the call
    itself already provides sufficient delay and an additional fixed sleep is wasteful."""
    elapsed = clock.now() - before
    remaining = _POLL - elapsed
    if remaining > 0:
        clock.sleep(remaining)


def _wait(
    driver: base.Driver, w: Wait, clock: Clock, network: NetworkSource = _no_network
) -> tuple[bool, str]:
    """Condition wait. Polls query() (or the observed network) until satisfied instead
    of a fixed sleep."""
    deadline = clock.now() + w.timeout
    if w.for_ is not None:
        target = w.for_.as_selector()
        while True:
            t0 = clock.now()
            if _exists(driver.query(), target):
                return True, ""
            if clock.now() >= deadline:
                return False, f"wait timeout: for {target} ({w.timeout}s)"
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, Gone):
        target = w.until.gone.as_selector()
        while True:
            t0 = clock.now()
            if not _exists(driver.query(), target):
                return True, ""
            if clock.now() >= deadline:
                return False, f"wait timeout: gone {target} ({w.timeout}s)"
            _adaptive_sleep(clock, t0)
    if isinstance(w.until, WaitRequest):
        req = w.until.request
        need = req.count if req.count is not None else 1
        while True:
            t0 = clock.now()
            if assertions.count_matching(network(), req) >= need:
                return True, ""
            if clock.now() >= deadline:
                label = assertions.request_label(req)
                return False, f"wait timeout: request {label} ({w.timeout}s)"
            _adaptive_sleep(clock, t0)
    if w.until == "settled":
        return _wait_settled(driver, deadline, clock)
    # until == "screenChanged"
    before = driver.query()
    while True:
        t0 = clock.now()
        current = driver.query()
        if current != before:
            return True, ""
        if clock.now() >= deadline:
            return False, f"wait timeout: screenChanged ({w.timeout}s)"
        _adaptive_sleep(clock, t0)


_SETTLE_POLLS = 2  # consecutive unchanged polls that count as "settled"


def _wait_settled(driver: base.Driver, deadline: float, clock: Clock) -> tuple[bool, str]:
    """Wait until a non-empty screen stops changing (transition/animation finished).

    A blank/collapsed tree (e.g. a screen mid-render, or one covered by a system
    alert) is never treated as settled. Best-effort: timing out just proceeds with the
    current screen — a settle is a stabilization hint, not a correctness assertion, so
    it never fails the step.
    """
    previous = driver.query()
    stable = 0
    while stable < _SETTLE_POLLS:
        if clock.now() >= deadline:
            return True, ""
        t0 = clock.now()
        current = driver.query()
        if current == previous and any(el["identifier"] for el in current):
            stable += 1
        else:
            stable, previous = 0, current
        _adaptive_sleep(clock, t0)
    return True, ""


def _interp_step(step: Step, bindings: Mapping[str, str]) -> Step:
    """A copy of the step with ${...} tokens (e.g. secrets.*) substituted, for execution.

    Only the executed action sees the real value — the caller keeps the original step for
    the manifest/report, so the recorded scenario shows the token, never the secret."""
    if not bindings:
        return step
    # Fast path: model_dump_json() is Rust-backed in Pydantic v2 and much cheaper than
    # model_dump() (which builds Python dicts). Most steps contain no tokens at all, so
    # a quick substring check on the JSON avoids the heavier serialisation + walk.
    if "${" not in step.model_dump_json(by_alias=True, exclude_none=True):
        return step
    dumped = step.model_dump(by_alias=True, exclude_none=True)
    if not interp.find_tokens(dumped) & bindings.keys():
        return step
    return Step.model_validate(interp.interpolate(dumped, bindings))


def _interp_asserts(asserts: list[Assertion], bindings: Mapping[str, str]) -> list[Assertion]:
    """Substitute ${...} tokens in a list of assertions (for scenario-level `expect`)."""
    if not bindings:
        return asserts
    # Fast path: if no assertion contains a token marker, skip the whole list.
    if not any("${" in a.model_dump_json(by_alias=True, exclude_none=True) for a in asserts):
        return asserts
    return [
        Assertion.model_validate(
            interp.interpolate(a.model_dump(by_alias=True, exclude_none=True), bindings)
        )
        for a in asserts
    ]


def _do_http(http: HttpRequest, bindings: dict[str, str] | None) -> None:
    """Execute an HTTP request and optionally save the response body to vars.*."""
    import urllib.error
    import urllib.request

    if not http.url.startswith(("http://", "https://")):
        raise base.SelectorError(f"http: only http/https URLs are allowed, got {http.url!r}")

    req = urllib.request.Request(
        http.url,
        data=http.body.encode("utf-8") if http.body else None,
        headers=dict(http.headers or {}),
        method=http.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code
    except urllib.error.URLError as e:
        raise base.SelectorError(f"http: request failed: {e.reason}") from e
    if http.status is not None and status != http.status:
        raise base.SelectorError(f"http: expected status {http.status}, got {status}")
    if http.save_body is not None and bindings is not None:
        bindings[f"vars.{http.save_body}"] = body


def _do_action(
    driver: base.Driver,
    step: Step,
    relaunch: RelaunchFn | None = None,
    control: DeviceControl | None = None,
    bindings: dict[str, str] | None = None,
) -> None:
    """Run tap / longPress / type / swipe / relaunch / device control / http (wait and
    assert live in the run loop)."""
    if step.tap is not None:
        driver.tap(step.tap.as_selector())
        return
    if step.double_tap is not None:
        driver.double_tap(step.double_tap.as_selector())
        return
    if step.long_press is not None:
        driver.long_press(step.long_press.sel.as_selector(), step.long_press.duration)
        return
    if step.type is not None:
        if step.type.into is not None:
            driver.tap(step.type.into.as_selector())
        driver.type_text(step.type.text)
        return
    if step.swipe is not None:
        sw = step.swipe
        if sw.from_ is not None and sw.to is not None:
            driver.swipe(sw.from_, sw.to)
        elif sw.on is not None and sw.direction is not None:
            el = base.resolve_unique(driver.query(), sw.on.as_selector())
            center = _center(el["frame"])
            driver.swipe(center, _target(center, sw.direction))
        return
    if step.pinch is not None:
        _require_multi_touch(driver, "pinch")
        driver.pinch(step.pinch.sel.as_selector(), step.pinch.scale)
        return
    if step.rotate is not None:
        _require_multi_touch(driver, "rotate")
        driver.rotate(step.rotate.sel.as_selector(), step.rotate.radians)
        return
    if step.relaunch is not None:
        if relaunch is None:
            raise base.UnsupportedAction(
                "relaunch requires a real device environment (not supported on fake driver)"
            )
        relaunch(step.relaunch)
        return
    if step.set_location is not None:
        if control is None:
            raise base.UnsupportedAction(
                "setLocation requires a real device environment (not supported on fake driver)"
            )
        control.set_location(step.set_location.lat, step.set_location.lon)
        return
    if step.push is not None:
        if control is None:
            raise base.UnsupportedAction(
                "push requires a real device environment (not supported on fake driver)"
            )
        control.push(step.push.payload)
        return
    if step.http is not None:
        _do_http(step.http, bindings)
        return
    if step.clear_keychain is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearKeychain requires a real device environment (not supported on fake driver)"
            )
        control.clear_keychain()
        return
    if step.clear_clipboard is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearClipboard requires a real device environment (not supported on fake driver)"
            )
        control.clear_clipboard()
        return
    if step.background is not None:
        if control is None:
            raise base.UnsupportedAction(
                "background requires a real device environment (not supported on fake driver)"
            )
        control.home()
        return
    if step.override_status_bar is not None:
        if control is None:
            raise base.UnsupportedAction(
                "overrideStatusBar requires a real device environment (not supported on fake driver)"
            )
        osb = step.override_status_bar
        kwargs: dict[str, str | int] = {}
        if osb.time is not None:
            kwargs["time"] = osb.time
        if osb.battery_level is not None:
            kwargs["battery_level"] = osb.battery_level
        if osb.battery_state is not None:
            kwargs["battery_state"] = osb.battery_state
        if osb.cellular_bars is not None:
            kwargs["cellular_bars"] = osb.cellular_bars
        if osb.wifi_bars is not None:
            kwargs["wifi_bars"] = osb.wifi_bars
        control.override_status_bar(**kwargs)
        return
    if step.clear_status_bar is not None:
        if control is None:
            raise base.UnsupportedAction(
                "clearStatusBar requires a real device environment (not supported on fake driver)"
            )
        control.clear_status_bar()
        return
    raise AssertionError("unhandled action")


def _require_multi_touch(driver: base.Driver, action: str) -> None:
    """Fail clearly before a two-finger gesture if the actuator can't do multi-touch
    (e.g. idb), rather than emitting a single-touch approximation that silently passes."""
    if base.Capability.MULTI_TOUCH not in driver.capabilities():
        raise base.UnsupportedAction(
            f"{action} requires a multi-touch capable backend (idb supports single touch only; use codegen→XCUITest instead)"
        )


# on_blocked(driver) -> the AlertEvent it dismissed if it cleared a blocking condition
# (e.g. a system alert), so the step/expect is worth retrying; else None.
BlockedHandler = Callable[[base.Driver], "AlertEvent | None"]


def _run_step_body(
    driver: base.Driver,
    step: Step,
    kind: str,
    clock: Clock,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
) -> tuple[bool, str, list[AssertionResult]]:
    """Execute one step's effect, returning (ok, reason, assertion_results).

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason = _wait(driver, step.wait, clock, network)
            return ok, reason, []
        if kind == "assert_":
            assert step.assert_ is not None
            results = assertions.evaluate(driver.query(), step.assert_, network())
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results
        _do_action(driver, step, relaunch, control, bindings)
        return True, "", []
    except (base.SelectorError, base.UnsupportedAction, NotImplementedError) as e:
        return False, str(e), []


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


# --- capturePolicy firing (evidence rules) ---

_DSL_ACTION = {"long_press": "longPress", "double_tap": "doubleTap", "assert_": "assert"}


def _primary_selector(step: Step) -> Selector | None:
    if step.tap is not None:
        return step.tap
    if step.double_tap is not None:
        return step.double_tap
    if step.long_press is not None:
        return step.long_press.sel
    if step.type is not None:
        return step.type.into
    if step.swipe is not None:
        return step.swipe.on
    if step.pinch is not None:
        return step.pinch.sel
    if step.rotate is not None:
        return step.rotate.sel
    return None


def _rule_fires(
    rule: CaptureRule, kind: str, primary_id: str | None, screen_changed: bool, ok: bool
) -> bool:
    trigger = rule.on
    if trigger.action is not None:
        if trigger.action != _DSL_ACTION.get(kind, kind):
            return False
        if trigger.id_matches is not None:
            return primary_id is not None and fnmatch.fnmatchcase(primary_id, trigger.id_matches)
        return True
    if trigger.event == "screenChanged":
        return screen_changed
    if trigger.result == "error":
        return not ok
    return False


def _dedupe(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _kind_of(token: str) -> str:
    return token.partition(".")[0]


_ExecSteps = Callable[[list[Step]], str | None]


def _run_if(
    driver: base.Driver,
    if_block: If,
    clock: Clock,
    network: NetworkSource,
    bindings: dict[str, str],
    exec_steps: _ExecSteps,
) -> tuple[bool, str]:
    """Evaluate the condition (with interpolation) and run the matching branch."""
    interp_condition = _interp_asserts([if_block.condition], bindings)[0]
    elements = driver.query()
    results = assertions.evaluate(elements, [interp_condition], network())
    branch = if_block.then if assertions.passed(results) else (if_block.else_ or [])
    if not branch:
        return True, ""
    failure = exec_steps(branch)
    return (True, "") if failure is None else (False, failure)


def _run_for_each(
    driver: base.Driver,
    loop: ForEach,
    bindings: dict[str, str],
    exec_steps: _ExecSteps,
) -> tuple[bool, str]:
    """Iterate over elements matching the (interpolated) selector."""
    sel_dict = interp.interpolate(loop.sel.model_dump(by_alias=True), bindings)
    sel = Selector.model_validate(sel_dict).as_selector()
    elements = driver.query()
    matched = base.find_all(elements, sel)
    for el in matched:
        ident = el.get("identifier")
        if not ident:
            return False, f"forEach: matched element has no identifier (label={el.get('label')!r})"
        bindings[f"vars.{loop.as_}"] = ident
        failure = exec_steps(loop.steps)
        if failure is not None:
            return False, failure
    return True, ""


def _run_extract(
    elements: list[base.Element],
    extracts: Mapping[str, Extract],
    live_bindings: dict[str, str],
) -> tuple[bool, str]:
    """Resolve each extract selector and store the property value in live_bindings."""
    for name, ext in extracts.items():
        try:
            el = base.resolve_unique(elements, ext.sel.as_selector())
        except base.SelectorError as e:
            return False, f"extract '{name}': {e}"
        raw: str | None = el.get(ext.prop)
        if raw is None:
            return False, f"extract '{name}': {ext.prop} is None on the matched element"
        live_bindings[f"vars.{name}"] = str(raw)
    return True, ""


def _collect_captures(
    scenario: Scenario, step: Step, kind: str, ok: bool, screen_changed: bool
) -> list[str]:
    """Capture kinds for this step: the always-on instant baseline, plus inline
    `capture` and any matching capturePolicy rules."""
    fired: list[str] = [*_BASELINE_INSTANT, *(step.capture or [])]
    primary = _primary_selector(step)
    primary_id = primary.id if primary is not None else None
    for rule in scenario.capture_policy:
        if _rule_fires(rule, kind, primary_id, screen_changed, ok):
            fired.extend(rule.capture)
    return _dedupe(fired)


def run_scenario(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock | None = None,
    sink: EvidenceSink | None = None,
    on_blocked: BlockedHandler | None = None,
    scenario_id: str | None = None,
    network: NetworkSource = _no_network,
    relaunch: RelaunchFn | None = None,
    bindings: Mapping[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
    visual_context: VisualContext | None = None,
) -> RunResult:
    """Run one scenario deterministically, firing capturePolicy rules into `sink`.

    The whole scenario is screen-recorded (always on): the sink starts a video before
    the first step and finalizes it after verification, attaching it to the result.

    If a step fails and `on_blocked` clears a blocking condition (e.g. dismisses a
    system alert), the step is retried once before being recorded as a failure.
    """
    clock = clock or RealClock()
    sink = sink or NullSink()
    sid = scenario_id or scenario_slug(scenario.name)
    recordings = sink.start_scenario_intervals(sid, list(_SCENARIO_INTERVALS))
    wants_screen_changed = any(r.on.event == "screenChanged" for r in scenario.capture_policy)
    outcomes: list[StepOutcome] = []
    expect_results: list[AssertionResult] = []
    expect_alerts: list[AlertEvent] = []
    failure: str | None = None
    artifacts: list[Artifact] = []
    scenario_start = clock.now()  # ~video start; step offsets are measured from here
    # Mutable bindings: extract steps populate vars.* during the run; scenario-level
    # expect sees the accumulated values.
    live_bindings: dict[str, str] = dict(bindings or {})

    try:
        failure = _run_steps(
            driver,
            scenario,
            clock,
            sink,
            on_blocked,
            wants_screen_changed,
            outcomes,
            scenario_start,
            sid,
            network,
            relaunch,
            live_bindings,
            control,
            progress,
        )
        if failure is None and scenario.expect:
            expect = _interp_asserts(scenario.expect, live_bindings)
            if visual_context is not None:
                driver.screenshot(str(visual_context.screenshot_path))
            expect_results = assertions.evaluate(
                driver.query(), expect, network(), visual_context=visual_context
            )
            if not assertions.passed(expect_results) and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    expect_alerts.append(event)
                    if visual_context is not None:
                        driver.screenshot(str(visual_context.screenshot_path))
                    expect_results = assertions.evaluate(
                        driver.query(), expect, network(), visual_context=visual_context
                    )  # retry once
            if not assertions.passed(expect_results):
                failure = "expect: " + _fail_reason(expect_results)
    finally:
        artifacts = sink.finish_scenario_intervals(sid, recordings)

    return RunResult(
        scenario=scenario.name,
        ok=failure is None,
        steps=outcomes,
        expect_results=expect_results,
        failure=failure,
        artifacts=artifacts,
        backend=getattr(driver, "name", ""),
        duration_s=max(0.0, clock.now() - scenario_start),
        expect_alerts=expect_alerts,
    )


def _run_steps(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock,
    sink: EvidenceSink,
    on_blocked: BlockedHandler | None,
    wants_screen_changed: bool,
    outcomes: list[StepOutcome],
    scenario_start: float,
    sid: str,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
) -> str | None:
    """Run the step loop, appending outcomes; return the failure string or None.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    step_counter = [0]  # mutable counter shared across recursive exec_steps calls

    def exec_steps(steps: list[Step]) -> str | None:
        for step in steps:
            kind = _action_of(step)
            idx = step_counter[0]
            step_counter[0] += 1
            outcome = StepOutcome(index=idx, action=kind)
            if progress is not None:
                progress(f"{sid} · step {idx + 1}: {_step_label(step, kind)}")
            step_id = f"{sid}/{step.name or f'step{idx}'}"
            start = clock.now()
            outcome.started_at = max(0.0, start - scenario_start)

            if kind == "if_":
                assert step.if_ is not None
                ok, reason = _run_if(driver, step.if_, clock, network, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            if kind == "for_each":
                assert step.for_each is not None
                ok, reason = _run_for_each(driver, step.for_each, bindings, exec_steps)
                outcome.ok, outcome.reason = ok, reason
                outcome.duration_s = clock.now() - start
                outcomes.append(outcome)
                if not ok:
                    return f"step {idx} ({kind}): {reason}"
                continue

            interp = _interp_step(step, bindings)
            before = driver.query() if wants_screen_changed else None
            ok, reason, results = _run_step_body(
                driver, interp, kind, clock, network, relaunch, bindings, control
            )
            if not ok and on_blocked is not None:
                event = on_blocked(driver)
                if event is not None:
                    outcome.alerts.append(event)
                    ok, reason, results = _run_step_body(
                        driver, interp, kind, clock, network, relaunch, bindings, control
                    )
            outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
            outcome.duration_s = clock.now() - start

            after = driver.query()
            screen_changed = before is not None and after != before

            if outcome.ok and interp.extract:
                ext_ok, ext_reason = _run_extract(after, interp.extract, bindings)
                if not ext_ok:
                    outcome.ok, outcome.reason = False, ext_reason

            fired = _collect_captures(scenario, step, kind, outcome.ok, screen_changed)
            instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
            outcome.artifacts.extend(sink.capture(driver, step_id, instant, elements=after))

            outcomes.append(outcome)
            if not outcome.ok:
                return f"step {idx} ({kind}): {outcome.reason}"
        return None

    return exec_steps(scenario.steps)
