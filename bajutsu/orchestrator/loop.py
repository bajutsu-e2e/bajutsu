"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import partial

from bajutsu import assertions, interp
from bajutsu.assertions import AssertionResult, EvalContext
from bajutsu.drivers import base
from bajutsu.drivers.actuation import Actuation
from bajutsu.evidence import Artifact, EvidenceSink, NullSink, intervals
from bajutsu.evidence.network import TransitionSource, _no_transitions
from bajutsu.mailbox import extract_value, select
from bajutsu.orchestrator.actions import _action_of, _do_action, _step_label
from bajutsu.orchestrator.evidence_rules import (
    _collect_captures,
    _extract_stable_key,
    _kind_of,
    _run_extract,
    requested_intervals,
)
from bajutsu.orchestrator.substitution import (
    _interp_asserts,
    _interp_step,
    _resolve_system_alert,
)
from bajutsu.orchestrator.types import (
    AlertEvent,
    AlertGuardConfig,
    Clock,
    DeviceControl,
    MailboxReader,
    NetworkSource,
    ProgressFn,
    RealClock,
    RelaunchFn,
    RunResult,
    SelectionState,
    StepOutcome,
    WallClock,
    _no_network,
    drain_actuations,
    scenario_slug,
)
from bajutsu.orchestrator.waits import (
    WaitTick,
    WaitTrace,
    _adaptive_sleep,
    _timeout_floor,
    _wait,
    describe_wait,
)
from bajutsu.scenario import (
    Assertion,
    Email,
    Extract,
    ForEach,
    If,
    Interrupt,
    Scenario,
    Selector,
    Step,
    UncoveredSystemAlertLocale,
)
from bajutsu.webview import DomSource, WebContextDriver

_logger = logging.getLogger(__name__)

# How often `email` re-polls the mailbox. Unlike the UI's 50 ms `_POLL`, each tick is a remote HTTP
# request to a (often rate-limited / metered) provider, so it polls about once a second.
_EMAIL_POLL = 1.0


def _fail_reason(results: list[AssertionResult]) -> str:
    return "; ".join(r.reason for r in results if not r.ok)


# Assertion kinds whose result a tree re-read cannot change: the clipboard and screenshot are read
# once, and the network kinds have their own `wait until: request`. Waiting on one of these would
# only idle to the deadline, so the poll stops as soon as every *still-failing* assertion is one of
# them. Any other (tree-derived) kind — value / label / exists / count / state / golden, and any
# future UI kind — keeps the wait, which is the read-race this poll exists to close.
_READ_ONCE_KINDS = frozenset(
    {"clipboard", "visual", "request", "responseSchema", "requestSequence", "event"}
)


def _poll_asserts(
    driver: base.Driver,
    asserts: list[Assertion],
    network: NetworkSource,
    clock: Clock,
    *,
    ctx: EvalContext,
) -> tuple[list[AssertionResult], list[base.Element]]:
    """Evaluate `asserts` as a condition wait: re-read the tree until it passes or the deadline.

    Polling `query()` until `passed()` — bounded by a wall-clock deadline, never a fixed sleep — is
    what keeps a fast read (the resident channel, ~0.1s) no more flaky than a slow one (`uiautomator
    dump`, ~2.4s, which incidentally waited out an action's async-mirrored value: a value an action
    mirrors into the tree can land a beat after the action returns, as Compose recomposes the
    `content-desc` asynchronously). The wait budget is the lane's wait floor
    (`BAJUTSU_MIN_WAIT_TIMEOUT`), the same knob every other condition wait honors — so it is zero
    (a single read, today's behavior) on lanes that don't set it, and the Android e2e lane's 15s
    where the race lives. Only the UI tree goes stale, so only it is re-read; the caller takes the
    screenshot and reads the clipboard once, and the poll ends the moment nothing a tree re-read
    could fix is still failing (`_READ_ONCE_KINDS`).

    Returns the final results and the last tree read, so a step-level caller can reuse that settled
    tree as its `after` snapshot instead of re-querying (BE-0299 Unit 1 / BE-0259).
    """
    deadline = clock.now() + _timeout_floor()
    while True:
        t0 = clock.now()
        tree = driver.query()
        results = assertions.evaluate(tree, asserts, network(), ctx=ctx)
        if assertions.passed(results) or clock.now() >= deadline:
            return results, tree
        if all(r.ok or r.kind in _READ_ONCE_KINDS for r in results):
            return results, tree  # only read-once assertions are left failing; a re-read can't help
        _adaptive_sleep(clock, t0)


def _evaluate_expect(
    driver: base.Driver,
    expect: list[Assertion],
    network: NetworkSource,
    clock: Clock,
    *,
    ctx: EvalContext,
) -> list[AssertionResult]:
    """Evaluate the trailing `expect` block as a condition wait (BE-0245), via `_poll_asserts`.

    The scenario-level `expect` needs only the assertion results, not the settled tree, so it drops
    the tree `_poll_asserts` also returns.
    """
    results, _ = _poll_asserts(driver, expect, network, clock, ctx=ctx)
    return results


def _settle_extract_read(
    driver: base.Driver,
    extracts: Mapping[str, Extract],
    clock: Clock,
    *,
    initial: list[base.Element] | None = None,
    actuated_at: float | None = None,
) -> list[base.Element]:
    """Read the post-step tree, polling until the properties `extract` reads stop changing.

    An `extract` has no assertion to satisfy — it copies a value out — so this is the settle-shaped
    sibling of `_poll_asserts`: it stops when two consecutive reads share the extract projection
    (`_extract_stable_key`), or the same wall-clock deadline (`BAJUTSU_MIN_WAIT_TIMEOUT`) elapses.
    With no wait floor the budget is zero, so it reads exactly once — today's single-read behavior on
    every lane that does not set the floor (BE-0299 Unit 3).

    `actuated_at` anchors an actuation-anchored barrier (BE-0332 Unit 1): on a backend that declares a
    `read_lag()`, two agreeing reads are not trusted until they also postdate `actuated_at` by that
    budget. On Android the tree can keep publishing the pre-tap value for a beat, so the first reads
    after an action agree with each other on a value the action already superseded (`extract.yaml`
    binds the counter's previous value); the barrier holds the poll across that window. It is passed
    only on the mutating path, where an actuation this step is what a read must postdate — a seeded
    (`initial=`) read did not actuate, so it has no window to wait out. A backend reporting no lag,
    and any read with no `actuated_at`, keep the plain two-agreeing-reads settle byte-for-byte.

    The backend's read mark (`ReadOrderProvider`, BE-0332 Unit 3) deliberately does **not** release this
    poll early, though it did until `smoke (adb)` reproduced the very failure BE-0332 set out to close
    (`step 4 (assert_): expected equals='2' but actual='3'`). The mark answers "an accessibility event
    postdates the gesture", and this poll needs "the property I am copying out has been republished".
    They are not the same question, because one gesture produces several events: Compose publishes the
    tapped button's own event before the `Text` mirroring the new count recomposes. A read taken between
    the two postdates the tap and still carries the previous value, and the read after it agrees — so
    the mark and the two-agreeing-reads test both pass on a stale pair. Ordering is the right question
    for the driver's own catch-up barrier, which waits on *frames* the mark does speak for; it is the
    wrong question for a *value*. So the wall-clock budget stays this poll's only release, and a lag
    exceeding the lane's floor is simply never met and falls through to the latest read.

    `initial`, when given, is the seed a non-mutating step (`assert` / `wait`) already settled on: it
    is taken as the first sample so the poll refines that seed in place rather than re-reading it,
    which is why this is applied at that earlier read site — the seed short-circuits `_ScreenRead`, so
    the poll cannot live there for a seeded step. A mutating step passes no seed and reads fresh.
    """
    lag = driver.read_lag() if isinstance(driver, base.ReadLagProvider) else 0.0
    barrier = actuated_at + lag if actuated_at is not None else None
    deadline = clock.now() + _timeout_floor()
    tree = initial if initial is not None else driver.query()
    key = _extract_stable_key(tree, extracts)
    while clock.now() < deadline:
        t0 = clock.now()
        next_tree = driver.query()
        next_key = _extract_stable_key(next_tree, extracts)
        settled = next_key == key
        # `barrier` is the wall-clock ceiling the wait falls through at, and the only thing separating
        # a pair that agrees because the value settled from one that agrees because both reads landed
        # before it republished. The docstring above says why the read mark cannot stand in for it.
        if settled and (barrier is None or clock.now() >= barrier):
            return next_tree
        tree = next_tree
        if not settled:
            key = next_key
        _adaptive_sleep(clock, t0)
    # Deadline hit while the extract projection was still moving (or still inside the read-lag window):
    # return the latest read (best-effort, like the driver `_settle`), and say so, so a later assert
    # failing on a still-propagating value is traceable to an un-settled extract rather than looking
    # inexplicable (BE-0299 Unit 3).
    _logger.debug(
        "extract settle: projection still changing at the wait deadline; using latest read"
    )
    return tree


class _ScreenRead:
    """A step's post-step screen read, taken at most once and cached (BE-0234 Unit 2).

    On the adb backend a screen read (`uiautomator dump`) is the dominant per-step cost — ~2.4s
    against ~0.1-0.3s for a lighter read channel — so the end-of-step read is deferred until a
    consumer actually needs it: a `screenChanged` capture, an `extract`, or a `wait`-timeout
    diagnostic. A plain `tap`/`assert` step with none of these under a `NullSink` never reads.
    When it is read, the tree also seeds the next step's `before` — nothing actuates between a
    step's `after` and the next step's `before`, so they observe identical device state.

    A non-mutating step (`assert`, `wait`) already queried the tree to evaluate itself, and nothing
    actuates between that query and this read, so the caller can `seed` it with that snapshot: a
    consumer then reuses it instead of issuing a second identical query (BE-0259). A seeded read is
    not a runner-issued read — `queried` stays False — so the BE-0234 read-count yardstick keeps
    counting only the queries this class actually performs.

    `read` overrides the default `query()` for the first, uncached read: a step whose `extract` will
    consume the tree passes a property-aware settle poll here (`_settle_extract_read`), so the value
    it copies out is a settled one rather than whichever was still propagating when a single read
    fired (BE-0299 Unit 3). It is mutually exclusive with `seed` — a seeded step is refined at its
    earlier read site instead — and only fires on a genuine read, so `queried` still reflects one.
    """

    def __init__(
        self,
        driver: base.Driver,
        seed: list[base.Element] | None = None,
        *,
        read: Callable[[], list[base.Element]] | None = None,
    ) -> None:
        # A seed short-circuits `.get()`, so a `read` passed alongside one would be silently dropped —
        # fail loudly instead (the two are mutually exclusive by construction; see the class docstring).
        assert not (seed is not None and read is not None), "seed and read are mutually exclusive"
        self._driver = driver
        self._tree = seed
        self._available = seed is not None
        self._queried = False
        self._read = read

    def get(self) -> list[base.Element]:
        """The post-step tree: the seed if one was given, else read once (via `read`) and cached."""
        if not self._available:
            self._tree = self._read() if self._read is not None else self._driver.query()
            self._available = True
            self._queried = True
        assert (
            self._tree is not None
        )  # set on seed or the read above; narrows the Optional for mypy
        return self._tree

    @property
    def cached(self) -> list[base.Element] | None:
        """The tree if seeded or already read, else None — so a capture can read lazily on its own."""
        return self._tree if self._available else None

    @property
    def queried(self) -> bool:
        """Whether `get()` issued a `query()` — False for a seeded (reused) tree."""
        return self._queried


def _clipboard_for(block: list[Assertion], control: DeviceControl | None) -> str | None:
    """The device pasteboard, read once when `block` has a `clipboard` assertion; None otherwise.

    None when no `clipboard` assertion is present, when no device-control channel is available
    (fake driver / parallel run), or when the read itself fails (`simctl pbpaste` errored). In every
    None case a `clipboard` assertion fails cleanly via `evaluate` rather than aborting the run —
    the read is a verification input, not a scenario step."""
    if control is None or not any(a.clipboard is not None for a in block):
        return None
    try:
        return control.get_clipboard()
    except (OSError, subprocess.CalledProcessError):
        return None


def _do_email(
    email: Email,
    clock: Clock,
    mailbox: MailboxReader | None,
    bindings: dict[str, str] | None,
) -> tuple[bool, str]:
    """Poll the mailbox until a matching message arrives, then extract its value into `vars.*`.

    A condition wait bounded by `email.timeout` (never a fixed sleep): it baselines the ids present
    at the start so only mail arriving *after* counts (skew-free), then re-fetches until a match or
    the deadline. A missing mailbox, a timeout, or a matched message whose body the regex can't hit
    is a clean failure — never a silent wrong value. `mailbox.fetch` raising `SelectorError` (an
    unreachable / non-2xx endpoint) propagates to the caller's handler, which records it as a failure.
    """
    if mailbox is None:
        return False, "email: no mailbox configured (set targets.<name>.mailbox)"
    if bindings is None:  # defensive: the run loop always passes a dict for a step
        return True, ""
    deadline = clock.now() + email.timeout
    baseline = frozenset(m.id for m in mailbox.fetch(email.timeout))
    while True:
        remaining = deadline - clock.now()
        if remaining <= 0:
            return False, f"email: no matching message within {email.timeout:g}s"
        # Bound each fetch by the time left, so a single hung request can't overrun email.timeout.
        picked = select(mailbox.fetch(remaining), email.match, baseline)
        if picked is not None:
            value = extract_value(picked.body, email.extract)
            if value is None:
                return False, "email: matched a message but extract regex did not match its body"
            bindings[f"vars.{email.extract.var}"] = value
            return True, ""
        clock.sleep(min(_EMAIL_POLL, deadline - clock.now()))


def _run_step_body(
    driver: base.Driver,
    step: Step,
    kind: str,
    clock: Clock,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
    mailbox: MailboxReader | None = None,
    ctx: EvalContext | None = None,
    wait_trace: WaitTrace | None = None,
    selection: SelectionState | None = None,
    alert_guard: AlertGuardConfig | None = None,
    alerts: list[AlertEvent] | None = None,
    on_wait_tick: WaitTick | None = None,
    transitions: TransitionSource = _no_transitions,
    on_interrupt_poll: Callable[[list[base.Element]], bool] | None = None,
) -> tuple[bool, str, list[AssertionResult], list[base.Element] | None]:
    """Execute one step's effect, returning (ok, reason, assertion_results, snapshot).

    ``snapshot`` is the settled tree a non-mutating step (`assert`, `wait`) already queried to
    evaluate itself; the caller reuses it as the step's `after` instead of re-querying (BE-0259). It
    is ``None`` for steps that mutate the screen (`tap`, `type`, …) or read no tree (`email`,
    `wait until: request`), so the post-step read falls back to a fresh query for exactly the steps
    where "before" and "after" may differ.

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function. ``wait_trace``, when given for a wait step, records the poll timeline so a
    timeout is diagnosable from artifacts (BE-0231 Unit 1). ``alert_guard``/``alerts``, when given for
    a wait step, are passed through to ``_wait``'s mid-wait alert guard (BE-0269); other step kinds
    ignore them. ``on_interrupt_poll``, when given for a wait step, is passed to ``_wait`` so a
    scenario's ``interrupts`` handlers can clear an interstitial screen mid-wait (BE-0314)."""
    try:
        if kind == "wait":
            assert step.wait is not None
            ok, reason, tree = _wait(
                driver,
                step.wait,
                clock,
                network,
                trace=wait_trace,
                alert_guard=alert_guard,
                alerts=alerts,
                on_tick=on_wait_tick,
                transitions=transitions,
                on_interrupt_poll=on_interrupt_poll,
            )
            return ok, reason, [], tree
        if kind == "email":
            assert step.email is not None
            ok, reason = _do_email(step.email, clock, mailbox, bindings)
            return ok, reason, [], None
        if kind == "assert_":
            assert step.assert_ is not None
            clip = _clipboard_for(step.assert_, control)
            # A step-level assert sees only golden + clipboard: no per-step screenshot is taken, so
            # `visual` / `responseSchema` have no fresh input here (they run at scenario `expect`).
            # Drop them from the bundled context to preserve that behavior (BE-0250 Unit 2).
            step_ctx = replace(ctx or EvalContext(), visual=None, schema=None, clipboard=clip)
            # A condition wait, not a single snapshot: a value the prior action mirrors into the tree
            # a beat late is caught, the same race the trailing `expect` already closes (BE-0299
            # Unit 2). Zero-budget (no wait floor) reads exactly once, as before.
            results, tree = _poll_asserts(driver, step.assert_, network, clock, ctx=step_ctx)
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results, tree
        _do_action(driver, step, relaunch, control, bindings, selection)
        return True, "", [], None
    except (base.SelectorError, base.UnsupportedAction, NotImplementedError) as e:
        return False, str(e), [], None


def _resolve_video_start_offset(
    video_interval: intervals.Interval | None, scenario_start: float
) -> float:
    """The correction the report's video anchor (`RunResult.video_anchor_s`) is offset by.

    `video_interval.true_start` (confirmed or driver-stamped) may precede or follow
    `scenario_start` — a prestarted device recording begins before it, an on-demand iOS
    recording's confirmation wait completes just before it — so this offset, resolved once here,
    places the anchor at the video's real origin instead of the moment `scenario_start`
    happened to be stamped. `0.0` (no correction) both when no confirmed `true_start` exists and
    when the resolved offset is positive: a video starting *after* `scenario_start` is not a case
    this design expects to occur in production (see BE-0346's Motivation), so it is surfaced with a
    warning rather than trusted. The guard is one-sided by construction: a *negative* offset is
    trusted unconditionally, so a stale `true_start` — necessarily an older instant than
    `scenario_start`, and so always negative — is not caught here.

    Mixing the two time sources is deliberate but load-bearing: `true_start` is always a raw
    `time.monotonic()` instant, so `clock` must share that epoch (`RealClock`). A clock with a
    different origin makes this offset — and so every video-relative second a report derives from
    the anchor — meaningless rather than merely shifted.
    """
    if video_interval is None or video_interval.true_start is None:
        return 0.0
    offset = video_interval.true_start - scenario_start
    if offset > 0:
        _logger.warning(
            "video true_start (%s) is after scenario_start (%s) for kind=%s provider=%s; this is "
            "not expected in production, so the video-sync correction is skipped for this "
            "scenario rather than trusted",
            video_interval.true_start,
            scenario_start,
            video_interval.kind,
            video_interval.provider,
        )
        return 0.0
    return offset


def run_scenario(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock | None = None,
    sink: EvidenceSink | None = None,
    alert_guard: AlertGuardConfig | None = None,
    scenario_id: str | None = None,
    network: NetworkSource = _no_network,
    relaunch: RelaunchFn | None = None,
    bindings: Mapping[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
    ctx: EvalContext | None = None,
    mailbox: MailboxReader | None = None,
    webview_bridge: DomSource | None = None,
    transitions: TransitionSource = _no_transitions,
    interrupts: list[Interrupt] | None = None,
    locale: str | None = None,
    wall_clock: WallClock = time.time,
) -> RunResult:
    """Run one scenario deterministically, firing capturePolicy rules into `sink`.

    Heavy scenario-wide intervals (video / deviceLog / appTrace) are opt-in (BE-0028): the sink
    starts only the interval kinds the scenario actually requests (`requested_intervals`) before
    the first step and finalizes them after verification, attaching them to the result. A scenario
    that requests none records no intervals; the instant baseline still fires every step.

    If a step fails and `alert_guard` clears a blocking condition (e.g. dismisses a
    system alert), the step is retried once before being recorded as a failure.

    `transitions` (BE-0310) is the read-only screen-transition signal a `wait until: settled` step
    consults in place of tree-diff polling; the default reports none, so a caller that doesn't pass
    one (most callers, and every non-iOS backend) sees the unchanged tree-diff behavior.

    `wall_clock` (BE-0348) is read exactly once, beside `clock.now()`, to form the anchor pair every
    recorded timestamp is derived from. Every timing *decision* still reads `clock` alone, so a
    backward wall-clock jump can never shorten a wait or a duration.
    """
    clock = clock or RealClock()
    sink = sink or NullSink()
    ctx = ctx or EvalContext()
    sid = scenario_id or scenario_slug(scenario.name)
    recordings = sink.start_scenario_intervals(sid, requested_intervals(scenario))
    wants_screen_changed = any(r.on.event == "screenChanged" for r in scenario.capture_policy)
    outcomes: list[StepOutcome] = []
    expect_results: list[AssertionResult] = []
    expect_alerts: list[AlertEvent] = []
    # The guard's expect-phase dismissing tap: the one actuation that happens outside the step loop, so
    # it is drained here rather than left in the driver's log with no step to carry it (see BE-0315's
    # `expect_alerts` beside it).
    expect_actuations: list[Actuation] = []
    failure: str | None = None
    artifacts: list[Artifact] = []
    # The anchor pair: a monotonic instant every in-run duration is measured from, and the wall-clock
    # instant it corresponds to. Read back to back so the two describe the same moment as closely as
    # the platform allows — `wall_offset_s` is their difference, and every recorded timestamp is
    # `t + wall_offset_s` for a monotonic instant `t` from this run (the step loop below and
    # `pipeline.py`'s network write both spell the conversion exactly that way).
    scenario_start = clock.now()
    scenario_wall_start = wall_clock()
    wall_offset_s = scenario_wall_start - scenario_start
    video_interval = next((r for r in recordings if r.kind == "video"), None)
    video_start_offset = _resolve_video_start_offset(video_interval, scenario_start)
    # Mutable bindings: extract steps populate vars.* during the run; scenario-level
    # expect sees the accumulated values.
    live_bindings: dict[str, str] = dict(bindings or {})

    try:
        failure = _run_steps(
            driver,
            scenario,
            clock,
            sink,
            alert_guard,
            wants_screen_changed,
            outcomes,
            wall_offset_s,
            sid,
            network,
            relaunch,
            live_bindings,
            control,
            progress,
            mailbox,
            ctx,
            webview_bridge,
            transitions,
            interrupts,
            locale,
        )
        if failure is None and scenario.expect:
            expect = _interp_asserts(scenario.expect, live_bindings)
            clip = _clipboard_for(expect, control)
            if ctx.visual is not None:
                driver.screenshot(str(ctx.visual.screenshot_path))
            expect_results = _evaluate_expect(
                driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
            )
            if not assertions.passed(expect_results) and alert_guard is not None:
                event = alert_guard(driver)
                if event is not None:
                    expect_alerts.append(event)
                    expect_actuations.extend(drain_actuations(driver).records)
                    if ctx.visual is not None:
                        driver.screenshot(str(ctx.visual.screenshot_path))
                    # Re-read the clipboard too: clearing the block may have let the app update the
                    # pasteboard, so the retry must compare against the fresh value, not the stale one.
                    clip = _clipboard_for(expect, control)
                    expect_results = _evaluate_expect(
                        driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
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
        video_anchor_s=scenario_wall_start + video_start_offset,
        wall_offset_s=wall_offset_s,
        expect_alerts=expect_alerts,
        expect_actuations=expect_actuations,
    )


# A recursive step runner: run these steps against this active driver, return the failure or None.
# The driver is passed explicitly so a web block can hand its inner steps a WebView driver without
# any shared mutable state (BE-0172).
_ExecSteps = Callable[[list[Step], base.Driver], str | None]


class _StepCounter:
    """A monotonically increasing step index shared across the recursive step loop (BE-0172).

    A named replacement for the former ``step_counter = [0]`` closure smuggle: ``take()`` returns
    the current index and advances, so nested ``for_each`` / ``web`` groups keep unique, ordered
    indices without a boxed list.
    """

    def __init__(self) -> None:
        self._next = 0

    def take(self) -> int:
        idx = self._next
        self._next += 1
        return idx


# Consecutive fires of the *same* interrupt entry allowed within one step's resolution (BE-0314). A
# mis-set entry — a condition its own `steps` never clear — must not hang the run, so after this many
# fires it goes inert and the step falls back to its ordinary outcome. In the spirit of BE-0269's
# `_GUARD_MAX_ATTEMPTS`: a small fixed ceiling that turns "the recovery didn't take" into a clean
# step failure/timeout rather than an infinite loop.
_INTERRUPT_MAX_FIRES = 3


@dataclass
class _InterruptGuard:
    """Checks a scenario's `interrupts` handlers against already-fetched trees mid-step (BE-0314).

    Analogous to `waits._AlertGuardGate`, but for in-tree interstitial screens the assertion DSL can
    see (not out-of-process system alerts): each entry's `condition` is evaluated against a tree the
    loop already holds — a `wait`'s poll tick, or an act step's pre-action read — and its `steps` run
    to clear the screen when it matches, wherever in the sequence it surfaced. It is the deterministic
    trigger only; `condition` is a machine predicate, never a model call (prime directive 1).

    One guard is built per step, so `_fires` — the per-entry consecutive-fire counter — resets each
    step; an entry that keeps matching (its recovery didn't clear it) goes inert at
    `_INTERRUPT_MAX_FIRES` and the step falls through to its ordinary outcome. A non-matching entry
    resets its own counter, so "consecutive" stays honest. `failure` records a recovery step's
    failure so the caller fails the step loudly rather than swallowing it (determinism first).
    """

    interrupts: list[Interrupt]
    driver: base.Driver
    network: NetworkSource
    bindings: dict[str, str]
    run_recovery: _ExecSteps
    _fires: dict[int, int] = field(default_factory=dict)
    failure: str | None = None
    # Conditions interpolated once against the step's bindings (below), not per poll: `observe` runs
    # every wait tick, and re-interpolating a `${...}`-free condition there re-serializes it each time
    # for no change. The bindings a guard sees are the step's, fixed for its lifetime.
    _conditions: list[Assertion] = field(init=False)

    def __post_init__(self) -> None:
        self._conditions = [
            _interp_asserts([e.condition], self.bindings)[0] for e in self.interrupts
        ]

    def _fire_once(self, elements: list[base.Element]) -> bool:
        """Run one pass over the entries against `elements`; return whether any recovery ran."""
        fired = False
        net = self.network()
        for i, condition in enumerate(self._conditions):
            if self.failure is not None:
                return fired
            if self._fires.get(i, 0) >= _INTERRUPT_MAX_FIRES:
                continue
            if not assertions.passed(assertions.evaluate(elements, [condition], net)):
                self._fires[i] = 0
                continue
            self._fires[i] = self._fires.get(i, 0) + 1
            failure = self.run_recovery(self.interrupts[i].steps, self.driver)
            if failure is not None:
                self.failure = failure
                return fired
            fired = True
        return fired

    def observe(self, elements: list[base.Element]) -> bool:
        """Check the entries against one poll's tree; return whether the wait should abort now.

        A `True` return means a recovery step just failed (`self.failure` is now set): the outcome
        is already decided, so `_wait`/`_wait_settled` end the poll immediately instead of burning
        the rest of the timeout on a wait that can no longer pass — the caller (the run loop) reads
        `self.failure` for the real reason once `_run_step_body` returns (BE-0314)."""
        self._fire_once(elements)
        return self.failure is not None

    def clear_before_act(self, seed: list[base.Element]) -> list[base.Element]:
        """Before a UI act, clear any matching interstitial, re-reading until settled or capped.

        `seed` is the pre-action tree the loop already read for the step (a `screenChanged` step's
        `before`, or the one extra query an interrupts-declaring scenario pays for a bare act). Each
        fired recovery actuates, so the new screen is re-read and re-checked — the loop a `wait` gets
        for free from its own polling.

        Returns the settled pre-act tree — the last one read, so it reflects the screen *after* any
        interstitial was cleared. The caller re-baselines its `screenChanged` `before` from it, so a
        recovery's own screen mutation is not misattributed to the step's action (the return is `seed`
        unchanged when nothing fired).
        """
        elements = seed
        while self.failure is None and self._fire_once(elements):
            elements = self.driver.query()
        return elements


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
    failure = exec_steps(branch, driver)
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
        failure = exec_steps(loop.steps, driver)
        if failure is not None:
            return False, failure
    return True, ""


@dataclass(frozen=True)
class LastLeafStep:
    """The last leaf step (an actuating/`wait`/`assert`/`email` kind) to actually run, however deep
    the `if`/`forEach`/`web` nesting (BE-0341). `_run_steps` gives it one more screenshot once the
    whole run finishes, since no following step exists to carry its result forward as a pre-step
    baseline the way every other step's does. Bundled rather than two parallel `StepLoopState`
    fields so the two are always set together — `_handle_action` constructs one in a single
    assignment, and a consumer narrows both at once from one `is not None` check.
    """

    outcome: StepOutcome
    step_id: str


@dataclass
class StepLoopState:
    """The mutable context a run's step loop carries across its recursive descent.

    Aggregates what the loop used to smuggle through closure `nonlocal`s and free variables, so a
    single object threads the shared state through nested `if` / `forEach` / `web` groups and an
    interrupt's recovery — all of which run through the same step loop and must see the same
    counter, outcomes, bindings, and screen-read bookkeeping.
    """

    counter: _StepCounter
    outcomes: list[StepOutcome]
    # `bindings` is a mutable dict (guaranteed by `run_scenario`) — extract steps add `vars.*`
    # entries so that subsequent steps and scenario-level `expect` can reference them.
    bindings: dict[str, str]
    # One selection tracker per run, shared across the recursive step loop (like `_StepCounter`), so
    # a `copy` sees the selection a prior `select` left — and any action in between clears it (BE-0265).
    selection: SelectionState = field(default_factory=SelectionState)
    # `prev_after` carries a step's post-step tree to the next step's `before` (BE-0234 Unit 2):
    # nothing actuates between the two, so they observe the same device state and the `before` read
    # is skipped. It holds only a tree we actually read; a step that took no read leaves it None so
    # the next `before` reads fresh, and a `web` block resets it (the tree is a different driver's).
    prev_after: list[base.Element] | None = None
    total_reads: int = 0  # runner-issued screen reads, the BE-0234 read-count yardstick (Unit 1)
    # Set only by `_handle_action`, once per leaf step it runs (see `LastLeafStep`).
    last_leaf: LastLeafStep | None = None
    # True while an interrupt's own recovery steps run (BE-0314). Those steps go through the step loop
    # too, so without this an interrupt whose recovery targets the very screen its `condition` matches
    # would re-trigger itself on each recovery step and recurse without end. Suppressing the guard
    # while recovery runs also keeps a run's interrupt handling to the outermost screen, not the
    # handlers reacting to each other mid-recovery.
    running_recovery: bool = False


@dataclass(frozen=True)
class _LoopConfig:
    """The run-invariant inputs the step loop reads but never mutates.

    Kept apart from `StepLoopState` (the mutable, shared bookkeeping) so a step handler takes the
    whole loop context as two values: `self.state` for what changes step to step, `self.cfg` for
    what is fixed for the run.
    """

    driver: base.Driver
    scenario: Scenario
    clock: Clock
    sink: EvidenceSink
    alert_guard: AlertGuardConfig | None
    wants_screen_changed: bool
    # Added to a monotonic `clock.now()` instant to get its wall-clock epoch — the same conversion
    # `pipeline.py` applies to network receive times (`RunResult.wall_offset_s`). The loop carries
    # only this delta, never the wall instant itself, so no timing decision can read a wall clock.
    wall_offset_s: float
    sid: str
    network: NetworkSource
    relaunch: RelaunchFn | None
    control: DeviceControl | None
    progress: ProgressFn | None
    mailbox: MailboxReader | None
    ctx: EvalContext | None
    webview_bridge: DomSource | None
    transitions: TransitionSource
    interrupts: list[Interrupt] | None
    locale: str | None


class _StepRunner:
    """Drives a scenario's steps over shared `state` and run-invariant `cfg`.

    `exec_steps` and `_run_recovery` are bound methods, so each is a `_ExecSteps` value that
    `_run_if` / `_run_for_each` / `_InterruptGuard` take unchanged: the recursion into nested `if` /
    `forEach` / `web` groups and an interrupt's recovery all re-enter through the same runner, so
    they share one `StepLoopState`.
    """

    def __init__(self, state: StepLoopState, cfg: _LoopConfig) -> None:
        self.state = state
        self.cfg = cfg

    def _run_recovery(self, steps: list[Step], active_driver: base.Driver) -> str | None:
        self.state.running_recovery = True
        try:
            return self.exec_steps(steps, active_driver)
        finally:
            self.state.running_recovery = False

    def exec_steps(self, steps: list[Step], active_driver: base.Driver) -> str | None:
        for step in steps:
            failure = self._run_one(step, active_driver)
            if failure is not None:
                return failure
        return None

    def _run_one(self, step: Step, active_driver: base.Driver) -> str | None:
        """Prepare the step's outcome, then dispatch to the handler for its kind.

        The `if` chain keeps the fall-through of the original loop: `if` / `forEach` / `web` have
        dedicated handlers, and every other kind — the actuating steps, `wait`, `assert`, `email`,
        and any future kind — flows to `_handle_action`, so a new step kind needs no wiring here.
        """
        kind = _action_of(step)
        idx = self.state.counter.take()
        outcome = StepOutcome(index=idx, action=kind)
        if self.cfg.progress is not None:
            self.cfg.progress(f"{self.cfg.sid} · step {idx + 1}: {_step_label(step, kind)}")
        start = self.cfg.clock.now()
        # The absolute instant this step began, converted through the scenario's anchor pair
        # (BE-0348). The video correction is deliberately not applied here — the report derives it
        # from `video_anchor_s` at render time, so it stays recomputable after the run.
        outcome.started_at = start + self.cfg.wall_offset_s

        if kind == "if_":
            return self._handle_if(step, active_driver, idx, kind, outcome, start)
        if kind == "for_each":
            return self._handle_for_each(step, active_driver, idx, kind, outcome, start)
        if kind == "web":
            return self._handle_web(step, active_driver, idx, kind, outcome, start)
        return self._handle_action(step, active_driver, idx, kind, outcome, start)

    def _handle_if(
        self,
        step: Step,
        active_driver: base.Driver,
        idx: int,
        kind: str,
        outcome: StepOutcome,
        start: float,
    ) -> str | None:
        assert step.if_ is not None
        ok, reason = _run_if(
            active_driver,
            step.if_,
            self.cfg.clock,
            self.cfg.network,
            self.state.bindings,
            self.exec_steps,
        )
        outcome.ok, outcome.reason = ok, reason
        outcome.duration_s = self.cfg.clock.now() - start
        self.state.outcomes.append(outcome)
        return None if ok else f"step {idx} ({kind}): {reason}"

    def _handle_for_each(
        self,
        step: Step,
        active_driver: base.Driver,
        idx: int,
        kind: str,
        outcome: StepOutcome,
        start: float,
    ) -> str | None:
        assert step.for_each is not None
        ok, reason = _run_for_each(
            active_driver, step.for_each, self.state.bindings, self.exec_steps
        )
        outcome.ok, outcome.reason = ok, reason
        outcome.duration_s = self.cfg.clock.now() - start
        self.state.outcomes.append(outcome)
        return None if ok else f"step {idx} ({kind}): {reason}"

    def _handle_web(
        self,
        step: Step,
        active_driver: base.Driver,
        idx: int,
        kind: str,
        outcome: StepOutcome,
        start: float,
    ) -> str | None:
        assert step.web is not None
        try:
            if self.cfg.webview_bridge is None:
                ok, reason = (
                    False,
                    "web: no WebView bridge configured (BAJUTSU_WEBVIEW_PORT not set)",
                )
            else:
                sel = interp.interpolate(
                    step.web.within.model_dump(by_alias=True), self.state.bindings
                )
                host_sel = Selector.model_validate(sel).as_selector()
                base.resolve_unique(active_driver.query(), host_sel)
                host_id = step.web.within.first_id()
                if host_id is None:
                    ok, reason = False, "web: within selector must specify an id"
                else:
                    # The inner steps run against a WebView driver; the active driver is
                    # passed explicitly, so control returns to `active_driver` for the
                    # steps after this block with no shared mutable state (BE-0172).
                    web_driver = WebContextDriver(
                        bridge=self.cfg.webview_bridge, webview_id=host_id
                    )
                    # The inner steps run on a different driver, so its trees must not seed a
                    # native step's `before`: reset around the block on both sides (BE-0234).
                    self.state.prev_after = None
                    failure = self.exec_steps(step.web.steps, web_driver)
                    self.state.prev_after = None
                    ok = failure is None
                    reason = failure or ""
        except base.SelectorError as e:
            ok, reason = False, str(e)
        outcome.ok, outcome.reason = ok, reason
        outcome.duration_s = self.cfg.clock.now() - start
        self.state.outcomes.append(outcome)
        return None if ok else f"step {idx} ({kind}): {reason}"

    def _handle_action(
        self,
        step: Step,
        active_driver: base.Driver,
        idx: int,
        kind: str,
        outcome: StepOutcome,
        start: float,
    ) -> str | None:
        step_id = f"{self.cfg.sid}/{step.name or f'step{idx}'}"
        # The report's baseline: the screen this step is about to act on, captured before it acts
        # (BE-0341). Reuses `prev_after` — already maintained unconditionally (BE-0234 Unit 2) —
        # rather than a fresh query, so a sink that reads nothing pays nothing here either. The sink
        # call below always targets `self.cfg.driver` (native — a `WebContextDriver` cannot
        # screenshot), so a `None` `elements` would make its fallback query the wrong driver whenever
        # `active_driver` is the web one; only a genuinely unset `prev_after` (the block's first
        # nested step — reset around the whole block, BE-0234 Unit 2) pays a fresh, correctly-targeted
        # `active_driver.query()` here, and every later nested step reuses `prev_after` for free, same
        # as a native step. `NullSink` ignores `elements` outright, so skip the query entirely under
        # it too — a sink that reads nothing must pay nothing even for a `web` block's first step.
        # Deliberately ahead of locale resolution below: this baseline depends only on the screen,
        # never on the step's own resolved fields, so a step that fails resolving them still gets it
        # — the run loop's report contract guarantees a pre-step baseline for every leaf step, a
        # failure at this point notwithstanding.
        pre_elements = self.state.prev_after
        pre_kinds = ["screenshot.before", "elements"]
        pre_query_was_fresh = False
        if (
            pre_elements is None
            and active_driver is not self.cfg.driver
            and not isinstance(self.cfg.sink, NullSink)
        ):
            try:
                pre_elements = active_driver.query()
                self.state.total_reads += 1
                # Seed `prev_after` with this same read: the `screenChanged`-policy `before` below
                # would otherwise see `prev_after` still unset and pay a second, duplicate query of
                # the same web driver for the same pre-action moment. Tracked separately from
                # `before_is_fresh` below (a tree just read *this iteration*, vs. one merely
                # available from `prev_after`) so the interrupt guard also recognizes this read as
                # current and skips its own redundant re-query.
                self.state.prev_after = pre_elements
                pre_query_was_fresh = True
            except (ConnectionError, base.UnsupportedAction, OSError) as exc:
                # Best-effort: a web context that can't be read yet must not crash the step before it
                # even gets to attempt its own action — that failure surfaces normally through
                # `_run_step_body` instead. Only `elements` needs the web driver; `screenshot.before`
                # is captured from the native driver regardless, so drop just `elements` here rather
                # than losing the whole baseline, and disclose the gap via logging rather than guess.
                _logger.debug(
                    "%s: pre-step elements capture skipped, web driver query failed: %s",
                    step_id,
                    exc,
                )
                pre_kinds = ["screenshot.before"]
        outcome.artifacts.extend(
            self.cfg.sink.capture(self.cfg.driver, step_id, pre_kinds, elements=pre_elements)
        )
        # Interpolate ${...} tokens, then turn a `handleSystemAlert` naming a prompt and a
        # choice into the concrete button label this run's locale renders (BE-0320). Resolving
        # here rather than per action kind means nested steps — `if` / `forEach` branches and an
        # interrupt's recovery — all arrive already resolved, since they come back through here.
        # A locale the lookup does not cover fails this step loudly, like the blocks above; it
        # never falls back to a guessed label.
        try:
            interp_step = _resolve_system_alert(
                _interp_step(step, self.state.bindings), self.cfg.locale
            )
        except UncoveredSystemAlertLocale as exc:
            outcome.ok, outcome.reason = False, str(exc)
            outcome.duration_s = self.cfg.clock.now() - start
            # Drained here too, like the `last_leaf` assignment below: nothing can have actuated this
            # early today (only the pre-step baseline capture has run), but leaving the one early
            # return as the single path that skips the drain is how a record would later be stranded
            # into the *next* step's outcome, silently and only for this failure.
            drained = drain_actuations(active_driver)
            outcome.actuations, outcome.dropped_actuations = drained.records, drained.dropped
            self.state.outcomes.append(outcome)
            # This early return skips the rest of the function, including the `last_leaf`
            # assignment at its end — set it here too, so a scenario that ends on this failure
            # still gets a final capture attributed to the step that actually ran last, rather than
            # a stale one left over from an earlier step (or none at all, for a single-step run).
            # The pre-step baseline above already ran (whatever it produced — a `NullSink` writes
            # nothing, same as any other step), so this failure gets the same evidence contract
            # every other leaf step does, not just the final capture.
            self.state.last_leaf = LastLeafStep(outcome, step_id)
            return f"step {idx} ({kind}): {exc}"
        # `before` is needed only for a `screenChanged` policy. Reuse the previous step's
        # post-step tree when we have one (same device state — nothing actuated in between), so
        # the read drops to (near) zero across the scenario; only the first step, or a step after
        # one that took no read, reads a fresh `before` (BE-0234 Unit 2). `before_is_fresh` tracks
        # which case this was, for the interrupt guard below: a tree just read this iteration is
        # current, but `prev_after` is a snapshot from the *previous* step's boundary — valid for
        # BE-0234's "nothing we actuated in between" assumption, not proof against an interstitial
        # that appeared asynchronously since (a timer/network overlay), which is exactly the case
        # `interrupts` exists to catch.
        before_is_fresh = False
        if not self.cfg.wants_screen_changed:
            before = None
        elif self.state.prev_after is not None:
            before = self.state.prev_after
            # A snapshot seeded by *this* step's own pre-step query above is current, not a
            # carried-over one from the previous step's boundary — recognized as fresh here too,
            # so the interrupt guard below skips its own redundant re-query of the same tree.
            before_is_fresh = pre_query_was_fresh
        else:
            before = active_driver.query()
            self.state.total_reads += 1
            before_is_fresh = True
        # A fresh interrupt guard per step (BE-0314), so its re-entrancy cap resets each step. A
        # bare act clears any interstitial up front — reusing `before` only when it is a tree just
        # read this iteration (zero extra cost); a carried-over `prev_after` snapshot, or no tree
        # at all, costs one extra query (paid only by interrupt-declaring scenarios) so the guard
        # checks the live screen rather than a possibly-stale one. A `wait` instead hooks the guard
        # into its own polling (`on_interrupt_poll` below), riding the poll tree at zero extra cost.
        # Only the step guard queries here; the recovery `steps` it runs go through `exec_steps`,
        # sharing the counter/outcomes/bindings like `if`'s branches.
        guard = (
            _InterruptGuard(
                self.cfg.interrupts,
                active_driver,
                self.cfg.network,
                self.state.bindings,
                self._run_recovery,
            )
            if self.cfg.interrupts and not self.state.running_recovery
            else None
        )
        if guard is not None and kind != "wait":
            # Re-baseline `before` from the settled post-recovery tree either way, so a cleared
            # interstitial's own screen change is not later misattributed to this step's action by
            # the `screenChanged` capture decision.
            if before is not None and before_is_fresh:
                before = guard.clear_before_act(before)
            else:
                before_read = guard.clear_before_act(active_driver.query())
                self.state.total_reads += 1
                if before is not None:
                    before = before_read
        # A `for` wait records its poll timeline so a timeout is diagnosable from artifacts
        # (BE-0231 Unit 1); the alert_guard retry gets a fresh trace so the diagnostic reflects the
        # attempt that actually failed.
        wait_trace = WaitTrace() if kind == "wait" and interp_step.wait is not None else None
        # A wait blocks silently for its whole timeout; stream a "still waiting <condition>" line
        # so the run log shows what it is blocked on, live. Only when progress is wired.
        wait_tick: WaitTick | None = None
        if self.cfg.progress is not None and kind == "wait" and interp_step.wait is not None:
            desc = describe_wait(interp_step.wait)
            prefix = f"{self.cfg.sid} · step {idx + 1}"

            def wait_tick(remaining: float, _desc: str = desc, _prefix: str = prefix) -> None:
                assert self.cfg.progress is not None
                self.cfg.progress(f"{_prefix}: waiting {_desc} ({remaining:.0f}s left)")

        if guard is not None and guard.failure is not None:
            # The pre-act clear already decided the outcome (a recovery step failed): skip the
            # step's own action rather than poke a screen the failed recovery left broken —
            # symmetric with how a wait's `on_interrupt_poll` aborts the poll instead of running
            # on. The rest of the pipeline below (evidence capture, outcome bookkeeping) still
            # runs unchanged, exactly as it does for any other failed step.
            ok, reason, snapshot = False, guard.failure, None
            results: list[AssertionResult] = []
        else:
            ok, reason, results, snapshot = _run_step_body(
                active_driver,
                interp_step,
                kind,
                self.cfg.clock,
                self.cfg.network,
                self.cfg.relaunch,
                self.state.bindings,
                self.cfg.control,
                self.cfg.mailbox,
                self.cfg.ctx,
                wait_trace=wait_trace,
                selection=self.state.selection,
                alert_guard=self.cfg.alert_guard,
                alerts=outcome.alerts,
                on_wait_tick=wait_tick,
                transitions=self.cfg.transitions,
                on_interrupt_poll=guard.observe if guard is not None else None,
            )
            if guard is not None and guard.failure is not None:
                # A mid-wait recovery failure is a decided outcome — fail on it now rather than
                # firing the end-of-step alert-guard dismiss/retry against the screen the failed
                # recovery left, symmetric with the pre-act short-circuit above.
                ok, reason = False, guard.failure
            elif not ok and self.cfg.alert_guard is not None:
                event = self.cfg.alert_guard(active_driver)
                if event is not None:
                    outcome.alerts.append(event)
                    wait_trace = WaitTrace() if wait_trace is not None else None
                    # The retry is the end-of-step "one more shot": it does not re-arm the mid-wait
                    # guard (no alert_guard passed), so a step's AI-vision calls stay bounded at
                    # _GUARD_MAX_ATTEMPTS (mid-wait) + 1 (this end-of-step dismiss).
                    ok, reason, results, snapshot = _run_step_body(
                        active_driver,
                        interp_step,
                        kind,
                        self.cfg.clock,
                        self.cfg.network,
                        self.cfg.relaunch,
                        self.state.bindings,
                        self.cfg.control,
                        self.cfg.mailbox,
                        self.cfg.ctx,
                        wait_trace=wait_trace,
                        selection=self.state.selection,
                        on_wait_tick=wait_tick,
                        transitions=self.cfg.transitions,
                        on_interrupt_poll=guard.observe if guard is not None else None,
                    )
            # A failure inside an interrupt's own recovery `steps` fails the step loudly, rather
            # than being swallowed while the run continues against a screen the recovery left
            # broken (determinism first). It overrides a step that otherwise passed — this is the
            # wait path's version of the pre-act short-circuit above (guard.failure can newly
            # become True during `_run_step_body`'s `on_interrupt_poll` calls).
            if guard is not None and guard.failure is not None:
                ok, reason = False, guard.failure
        outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
        outcome.duration_s = self.cfg.clock.now() - start
        # What the driver actually did to the screen during this step. Drained once, after the body has
        # finished, rather than per attempt: when the alert guard dismissed a prompt and retried, both
        # attempts really happened to the device and belong on this step in the order they occurred —
        # as does the guard's own dismissing tap, on the step it interrupted. `active_driver`, not
        # `cfg.driver`, because a step inside a `web` block actuates the WebView driver; nothing
        # actuates the native driver during such a step, so nothing is stranded.
        drained = drain_actuations(active_driver)
        outcome.actuations, outcome.dropped_actuations = drained.records, drained.dropped

        # The post-step read is lazy (BE-0234 Unit 2): `.get()` reads (once) only where a
        # consumer needs the tree, so a step with no consumer under a NullSink never reads. A
        # non-mutating step (`assert`/`wait`) hands back the tree it already settled on, so the
        # read reuses that snapshot rather than issuing a second identical query (BE-0259);
        # `snapshot` is None for mutating/tree-less steps, restoring the fresh post-step read.
        #
        # An `extract` on this step consumes the read, so it must observe a value that has stopped
        # propagating, not whichever one the single read caught (BE-0299 Unit 3). Gated on
        # `outcome.ok`, matching where the extract actually runs (below), so a failed step never
        # pays the poll for a value it will not read. A mutating step (or `wait until: request`,
        # which hands back no tree) has no seed, so the property-aware read is deferred into
        # `_ScreenRead` and fires only when a consumer needs the tree; `partial` binds this step's
        # driver/extracts now, not a later iteration's. A seeded non-mutating step cannot poll
        # there — the seed short-circuits `.get()` — so it is refined here, at that earlier read
        # site, before `_ScreenRead` reuses it (keeping `queried` False for it).
        read: Callable[[], list[base.Element]] | None = None
        if outcome.ok and interp_step.extract:
            if snapshot is None:
                # A mutating step: the extract read must postdate this step's actuation by the
                # backend's read lag (BE-0332 Unit 1). Nothing actuates between the step body
                # returning and here, so `now` is that actuation's completion; bound into the deferred
                # read so the barrier is measured from the action, not from whenever `_ScreenRead`
                # later fires.
                actuated_at = self.cfg.clock.now()
                read = partial(
                    _settle_extract_read,
                    active_driver,
                    interp_step.extract,
                    self.cfg.clock,
                    actuated_at=actuated_at,
                )
            else:
                # A seeded (non-mutating) step did not actuate, so it has no actuation to postdate.
                snapshot = _settle_extract_read(
                    active_driver, interp_step.extract, self.cfg.clock, initial=snapshot
                )
        screen = _ScreenRead(active_driver, seed=snapshot, read=read)
        screen_changed = before is not None and screen.get() != before

        # An unconditional first-wait diagnostic on a `for`-wait timeout: capturePolicy may not
        # request an element dump on failure, so without this the timeout leaves no evidence to
        # decide which cause fired (BE-0231 Unit 1). Deterministic, no LLM (prime directive 1).
        # `polls > 0` fires only after a `for`-wait ran (only that branch records the trace), so
        # the trigger is a structural fact, not the wording of the timeout message.
        if wait_trace is not None and not ok and wait_trace.polls > 0:
            try:
                art = self.cfg.sink.wait_diagnostic(
                    step_id, trace=wait_trace, elements=screen.get()
                )
            except OSError as exc:
                # Best-effort evidence: a disk/permission failure writing the diagnostic must not
                # mask the real timeout with an I/O traceback — keep the timeout as the failure and
                # disclose the lost evidence loudly. A genuine bug (e.g. a redaction error) still
                # surfaces rather than being swallowed here.
                _logger.warning("dropping wait-timeout diagnostic: write failed: %s", exc)
            else:
                if art is not None:
                    outcome.artifacts.append(art)

        if outcome.ok and interp_step.extract:
            ext_ok, ext_reason = _run_extract(
                screen.get(), interp_step.extract, self.state.bindings
            )
            if not ext_ok:
                outcome.ok, outcome.reason = False, ext_reason

        # `_collect_captures` already excludes `screenshot.before` (BE-0341): the pre-step baseline
        # above wrote that file from the true pre-action state, so re-taking it here would silently
        # mislabel a post-action pixel as `before.png`.
        fired = _collect_captures(self.cfg.scenario, step, kind, outcome.ok, screen_changed)
        # Interval kinds are recorded scenario-wide (run_scenario), so only the
        # instant kinds are captured per step here. Pass the tree only if we already read it;
        # otherwise `elements=None` lets the sink's `elements` writer read on its own (a NullSink
        # reads nothing), so a FileSink run stays at one read and a NullSink run at zero. A `web`
        # block captures against the native `driver`, so it must read the active (web) tree here
        # rather than let the native writer fall back to a mismatched tree (BE-0234 Unit 2).
        instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
        els = screen.get() if active_driver is not self.cfg.driver else screen.cached
        outcome.artifacts.extend(
            self.cfg.sink.capture(self.cfg.driver, step_id, instant, elements=els)
        )
        if screen.queried:
            self.state.total_reads += 1
        # The last leaf step to actually run (BE-0341): `_run_steps` uses this after the whole run
        # finishes to give the scenario's true final state a capture too, since no following step
        # exists to carry it forward as its own pre-step baseline (unlike every other step).
        self.state.last_leaf = LastLeafStep(outcome, step_id)
        # Seed the next step's `before` only with a tree we actually read; if we skipped the
        # read, the next `before` reads fresh (BE-0234 Unit 2).
        self.state.prev_after = screen.cached

        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"


def _run_steps(
    driver: base.Driver,
    scenario: Scenario,
    clock: Clock,
    sink: EvidenceSink,
    alert_guard: AlertGuardConfig | None,
    wants_screen_changed: bool,
    outcomes: list[StepOutcome],
    wall_offset_s: float,
    sid: str,
    network: NetworkSource,
    relaunch: RelaunchFn | None = None,
    bindings: dict[str, str] | None = None,
    control: DeviceControl | None = None,
    progress: ProgressFn | None = None,
    mailbox: MailboxReader | None = None,
    ctx: EvalContext | None = None,
    webview_bridge: DomSource | None = None,
    transitions: TransitionSource = _no_transitions,
    interrupts: list[Interrupt] | None = None,
    locale: str | None = None,
) -> str | None:
    """Run the step loop, appending outcomes; return the failure string or None.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    state = StepLoopState(counter=_StepCounter(), outcomes=outcomes, bindings=bindings)
    cfg = _LoopConfig(
        driver=driver,
        scenario=scenario,
        clock=clock,
        sink=sink,
        alert_guard=alert_guard,
        wants_screen_changed=wants_screen_changed,
        wall_offset_s=wall_offset_s,
        sid=sid,
        network=network,
        relaunch=relaunch,
        control=control,
        progress=progress,
        mailbox=mailbox,
        ctx=ctx,
        webview_bridge=webview_bridge,
        transitions=transitions,
        interrupts=interrupts,
        locale=locale,
    )
    result = _StepRunner(state, cfg).exec_steps(scenario.steps, driver)
    _logger.debug("%s: %d runner-issued screen reads (BE-0234)", sid, state.total_reads)
    # The scenario's true final state has no following step to carry it forward as a pre-step
    # baseline, so the last leaf step's outcome gets one more screenshot here (BE-0341). `elements`
    # is deliberately NOT re-captured: `elements.json` has one fixed filename, so re-capturing it
    # here would overwrite the pre-step baseline's pre-action tree with a post-action one — while
    # `screenshotUrl` (the editor's element-picker pairing, `bajutsu/serve/operations/reads.py`) keeps
    # resolving to the *first*-recorded screenshot, `before.png`. That mismatch would let a picked
    # element's coordinates (from the post-action tree) drift from what `before.png` actually shows.
    # Keeping `elements.json` the pre-action tree for every step, including the last, keeps that pair
    # consistent throughout. `after.png` is written as a raw artifact for anyone reading the manifest
    # directly; today's viewers (the HTML report and the serve editor) both resolve a step's
    # displayed screenshot to the *first*-recorded one, `before.png`, so this file is not surfaced by
    # default — making a viewer prefer it for the scenario's last step, if ever wanted, is separate,
    # future scope.
    # Gated on the leaf not already having recorded an `after.png`: a `capturePolicy` rule
    # (`screenshot.after`, or bare `screenshot` — defaults to `after`) firing post-step on this same
    # last leaf already wrote one. Capturing again would silently overwrite the rule's own shot with
    # a slightly later one (same fixed filename) and leave a second, duplicate `screenshot`/`after.png`
    # entry in `leaf.outcome.artifacts` for anyone reading the manifest directly — exactly the
    # audience the comment above names for this file.
    if (leaf := state.last_leaf) is not None and not any(
        a.kind == "screenshot" and a.name.endswith("after.png") for a in leaf.outcome.artifacts
    ):
        leaf.outcome.artifacts.extend(sink.capture(driver, leaf.step_id, ["screenshot.after"]))
    return result
