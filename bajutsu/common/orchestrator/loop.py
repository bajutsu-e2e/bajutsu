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

from bajutsu.common import assertions
from bajutsu.common.assertions import AssertionResult, EvalContext
from bajutsu.common.cancellation import (
    CANCELLED_FAILURE,
    CancelSource,
    RunCancelled,
    cancelled_teardown_seconds,
    grace_seconds,
    not_cancelled,
)
from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import Actuation
from bajutsu.common.drivers.webview import DomSource, WebContextDriver
from bajutsu.common.evidence import Artifact, EvidenceSink, NullSink, intervals
from bajutsu.common.evidence.network import TransitionSource, _no_transitions
from bajutsu.common.mailbox import extract_value, select
from bajutsu.common.orchestrator.actions import (
    _action_of,
    _do_action,
    _step_label,
    handle_system_alert_selector,
)
from bajutsu.common.orchestrator.evidence_rules import (
    _collect_captures,
    _extract_stable_key,
    _kind_of,
    _run_extract,
    requested_intervals,
)
from bajutsu.common.orchestrator.substitution import (
    _interp_asserts,
    _interp_step,
    _resolve_system_alert,
)
from bajutsu.common.orchestrator.types import (
    AlertEvent,
    AlertGuardConfig,
    Clock,
    DeviceControl,
    MailboxReader,
    NetworkSource,
    ProgressFn,
    RealClock,
    RelaunchFn,
    ResolvedAlertRule,
    RunResult,
    SelectionState,
    StepOutcome,
    UndeclaredInterruption,
    WallClock,
    _no_network,
    drain_actuations,
    drain_interruptions,
    push_interruption_policy,
    scenario_slug,
    undeclared_interruption_note,
)
from bajutsu.common.orchestrator.waits import (
    WaitTick,
    WaitTrace,
    _adaptive_sleep,
    _timeout_floor,
    _wait,
    describe_wait,
    settle_after_alert_dismiss,
    wait_for_system_alert,
)
from bajutsu.common.scenario import (
    AfterRule,
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
    interp,
    system_alert_shapes,
)

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
    cancelled: CancelSource = not_cancelled,
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

    `cancelled` (BE-0370) raises `RunCancelled` out of the poll, like every other condition wait, once
    the results are in — so an `assert` already satisfied on that poll still passes.
    """
    deadline = clock.now() + _timeout_floor()
    while True:
        t0 = clock.now()
        tree = driver.query()
        results = assertions.evaluate(tree, asserts, network(), ctx=ctx)
        if assertions.passed(results) or clock.now() >= deadline:
            return results, tree
        if cancelled():
            raise RunCancelled
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
    consumer actually needs it: a `screenChanged` capture, an `extract`, a `wait`-timeout
    diagnostic, or the always-on post-step `elements` write. That last one makes the read
    unconditional under any sink that writes, so the deferral now buys a run nothing beyond a
    `NullSink` — where a plain `tap`/`assert` step with none of the other three still never reads.
    When it is read, the tree also seeds the next step's `before` — nothing actuates between a
    step's `after` and the next step's `before`, so they observe identical device state.

    A non-mutating step (`assert`, `wait`) already queried the tree to evaluate itself, and nothing
    actuates between that query and this read, so the caller can `seed` it with that snapshot: a
    consumer then reuses it instead of issuing a second identical query (BE-0259). A seeded read is
    not a runner-issued read — `queried` stays False — so the BE-0234 read-count yardstick keeps
    counting only the queries this class actually performs. The seed is also the one tree that
    predates the step's post-action shutter (`_handle_action`), since the body took it: on those two
    step kinds the recorded `elements.json` is that fraction older than the `after.png` beside it.

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
    cancelled: CancelSource = not_cancelled,
) -> tuple[bool, str]:
    """Poll the mailbox until a matching message arrives, then extract its value into `vars.*`.

    A condition wait bounded by `email.timeout` (never a fixed sleep): it baselines the ids present
    at the start so only mail arriving *after* counts (skew-free), then re-fetches until a match or
    the deadline. A missing mailbox, a timeout, or a matched message whose body the regex can't hit
    is a clean failure — never a silent wrong value. `mailbox.fetch` raising `SelectorError` (an
    unreachable / non-2xx endpoint) propagates to the caller's handler, which records it as a failure.

    `cancelled` (BE-0370) raises `RunCancelled` out of the poll, like every other condition wait. This
    wait needs the check as much as any: `email.timeout` is whatever the scenario asked for — a wait
    for a one-time password commonly runs to a minute or more — so a cancelled run stuck here could
    otherwise outlive the grace window and be killed before it wrote its manifest.
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
        if cancelled():
            raise RunCancelled
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
    cancelled: CancelSource = not_cancelled,
) -> tuple[bool, str, list[AssertionResult], list[base.Element] | None]:
    """Execute one step's effect, returning (ok, reason, assertion_results, snapshot).

    ``snapshot`` is the settled tree a non-mutating step (`assert`, `wait`) already queried to
    evaluate itself; the caller reuses it as the step's `after` instead of re-querying (BE-0259). It
    is ``None`` for steps that mutate the screen (`tap`, `type`, …) or read no tree (`email`,
    `wait until: request`), so the post-step read falls back to a fresh query for exactly the steps
    where "before" and "after" may differ.

    The caller is responsible for interpolation (``_interp_step``) before
    calling this function. ``wait_trace``, when given for a wait step, records the poll timeline so a
    timeout is diagnosable from artifacts (BE-0231 Unit 1). ``alert_guard``/``alerts``, when given
    for a ``wait`` or ``handleSystemAlert`` step, drive the alert guard while that step's own wait
    runs (BE-0269, BE-0406); other step kinds ignore them. ``on_interrupt_poll``, when given for a
    wait step, is passed to ``_wait`` so a scenario's ``interrupts`` handlers can clear an
    interstitial screen mid-wait (BE-0314). ``cancelled`` reaches the four step kinds that poll —
    ``wait``, ``handleSystemAlert``, ``assert``, and ``email`` — so each notices a cancelled run
    within one polling tick (BE-0370)."""
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
                cancelled=cancelled,
            )
            return ok, reason, [], tree
        if kind == "handle_system_alert":
            assert step.handle_system_alert is not None
            # Handled here rather than through `_do_action` for the same reason `wait` is: the wait
            # needs the clock and the scenario's alert guard, and the action-handler signature
            # carries neither (BE-0406).
            ok, reason = wait_for_system_alert(
                driver,
                handle_system_alert_selector(step),
                step.handle_system_alert.timeout,
                clock,
                alert_guard=alert_guard,
                alerts=alerts,
                cancelled=cancelled,
            )
            if ok and selection is not None:
                # `_do_action` invalidates the live selection after every action but `select` and
                # `copy` (BE-0265), and this branch bypasses it. The step actuates the device, so a
                # `copy` after it must fail for want of a selection rather than copy whatever the
                # tap left — and only on a tap that landed, matching `_do_action`, which skips the
                # invalidation when its handler raises.
                selection.invalidate()
            return ok, reason, [], None
        if kind == "email":
            assert step.email is not None
            ok, reason = _do_email(step.email, clock, mailbox, bindings, cancelled)
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
            results, tree = _poll_asserts(
                driver, step.assert_, network, clock, ctx=step_ctx, cancelled=cancelled
            )
            ok = assertions.passed(results)
            return ok, "" if ok else _fail_reason(results), results, tree
        _do_action(driver, step, relaunch, control, bindings, selection)
        # Four branches return from this block; hoisting only the last into an `else` would suggest
        # the other three are not on the success path.
        return True, "", [], None  # noqa: TRY300
    except (
        base.SelectorError,
        base.ElementNotTappable,
        base.UnsupportedAction,
        NotImplementedError,
    ) as e:
        return False, str(e), [], None


def _resolve_video_start_offset(
    video_interval: intervals.Interval | None, scenario_start: float
) -> float:
    """The correction the report's video anchor (`RunResult.video_anchor_s`) is offset by.

    Resolved from the *finished* recording where it can be: `Interval.measured_start` is
    the recorder's own answer — the instant it was stopped, minus the duration the finalized file
    states — so it names the moment the first frame was captured rather than the moment some side
    signal fired. It is trusted in both directions, because a recording whose footage begins after
    `scenario_start` is an ordinary outcome of a start that lagged its confirmation, not the
    anomaly the `true_start` branch below treats it as; the report's own `max(0.0, …)` floor keeps
    an early step on the recording.

    `video_interval.true_start` (confirmed or driver-stamped) is the fallback for a recording whose
    duration could not be read. It may precede or follow `scenario_start` — a prestarted device
    recording begins before it, an on-demand iOS recording's confirmation wait completes just
    before it — so this offset places the anchor near the video's origin instead of at the moment
    `scenario_start` happened to be stamped. `0.0` (no correction) both when no confirmed
    `true_start` exists and when the resolved offset is positive: a video starting *after*
    `scenario_start` is not a case that branch expects in production (see BE-0346's Motivation), so
    it is surfaced with a warning rather than trusted. The guard is one-sided by construction: a
    *negative* offset is trusted unconditionally, so a stale `true_start` — necessarily an older
    instant than `scenario_start`, and so always negative — is not caught here.

    Mixing the two time sources is deliberate but load-bearing: both `measured_start` and
    `true_start` are raw `time.monotonic()` instants, so `clock` must share that epoch
    (`RealClock`). A clock with a different origin makes this offset — and so every video-relative
    second a report derives from the anchor — meaningless rather than merely shifted.
    """
    if video_interval is None:
        return 0.0
    if video_interval.measured_start is not None:
        return video_interval.measured_start - scenario_start
    if video_interval.true_start is None:
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


def _dispatch_after(
    rules: list[AfterRule],
    failure: str | None,
    run_steps: Callable[[list[Step], CancelSource], str | None],
    clock: Clock,
    cancelled: CancelSource,
) -> tuple[str | None, str]:
    """Run the `after` rules this run's verdict selects; return the run's composed failure (BE-0392).

    Entries run in declaration order — interleaved, not grouped by `on` — so the scenario-then-config
    merge order holds across the whole phase rather than only within one outcome group. The verdict
    is fixed before the first entry runs: an entry's own failure never re-dispatches the ones after
    it. A failing entry does not stop the phase either, since skipping the remaining cleanup is the
    outcome teardown exists to avoid.

    Args:
        failure: The run's failure so far, or None if it was passing. A failing entry becomes the
            failure on a passing run and is appended to it otherwise, so the reason a reader sees
            first stays the original cause rather than a symptom of the cleanup it triggered.
        run_steps: Runs one entry's steps under the `CancelSource` handed to it, returning that
            entry's failure or None.
        cancelled: The run's own cancel source, used only when the run was *not* cancelled.

    Returns:
        The run's failure after the phase, and the verdict it dispatched on — the report needs the
        latter to know which rules ran, which `failure` alone can no longer say once a cleanup
        step's own reason has been folded into it.
    """
    verdict = "success" if failure is None else "error"
    # A run that is shutting down cannot be bounded by `cancelled`: that source is latched, so its
    # first read inside this phase would raise and no cleanup step would run at all. Give the phase
    # its own deadline instead — cleanup gets a bounded chance to run without pushing the shutdown
    # tail past the window `serve` waits before an unconditional kill. The latch is read here rather
    # than inferred from `failure` alone, because a cancel arriving after the last step's boundary
    # check leaves `steps`/`expect` to finish and `failure` unset (BE-0370 keeps that scenario's real
    # verdict) while the process is shutting down all the same.
    bounded = cancelled() or (failure is not None and failure.startswith(CANCELLED_FAILURE))
    phase_cancelled = cancelled
    if bounded:
        deadline = clock.now() + cancelled_teardown_seconds(grace_seconds())

        def phase_cancelled() -> bool:
            return clock.now() >= deadline

    for rule in rules:
        if rule.on != "always" and rule.on != verdict:
            continue
        try:
            reason = run_steps(rule.steps, phase_cancelled)
        except RunCancelled:
            if bounded:
                # The teardown budget above, spent. Abandoning the rest is the designed bound, not a
                # new failure: a run cancelled early already carries that as its reason, and one
                # cancelled after its last boundary keeps the real verdict BE-0370 gives it. Either
                # way the After block's not-run rows are what disclose the abandoned entries.
                _logger.debug("after: teardown budget spent; abandoning the remaining entries")
                break
            # A cancel that arrived *during* teardown. On a passing run the failure must still lead
            # with the cancellation spelling downstream reads (BE-0370).
            if failure is None:
                return CANCELLED_FAILURE, verdict
            return f"{failure}; after: {CANCELLED_FAILURE}", verdict
        if reason is not None:
            failure = f"after: {reason}" if failure is None else f"{failure}; after: {reason}"
    return failure, verdict


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
    capture: list[str] | None = None,
    cancelled: CancelSource = not_cancelled,
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

    `capture` (the resolved `Effective.capture`, config's `defaults.capture`) is a baseline
    guarantee applied on top of every step, alongside `capturePolicy` rules and inline
    `capture:` tokens — the default is empty, so a caller that doesn't pass one (a test
    constructing a scenario directly) sees the unchanged capturePolicy/inline-only behavior.

    The scenario's `before` / `after` lifecycle phases (BE-0392) are read off `scenario` itself,
    not passed alongside it the way `interrupts` is: `runner.pipeline` folds the target config's own
    phases into each scenario before the run, so the scenario this function executes and the scenario
    the report renders are the same object. A caller that builds a `Scenario` directly therefore gets
    exactly the phases it declared.

    `cancelled` (BE-0370) makes a cancelled run land as an ordinary failed scenario: it is read at
    each step boundary and inside the poll loops that back every condition wait, and the resulting
    `RunCancelled` is turned into `failure: "cancelled"` here. The trailing `expect` re-check is
    deliberately left to finish — a scenario whose every step passed gets its real verdict rather
    than a cancellation label, and that block is bounded by the wait floor (zero on every lane that
    doesn't raise it).
    """
    clock = clock or RealClock()
    sink = sink or NullSink()
    ctx = ctx or EvalContext()
    sid = scenario_id or scenario_slug(scenario.name)
    recordings = sink.start_scenario_intervals(sid, requested_intervals(scenario, capture))
    wants_screen_changed = any(r.on.event == "screenChanged" for r in scenario.capture_policy)
    outcomes: list[StepOutcome] = []
    before_outcomes: list[StepOutcome] = []
    after_outcomes: list[StepOutcome] = []
    after_verdict = ""
    # One counter for the whole `after` phase: it runs one `run_phase` call per dispatched rule, and
    # a per-call counter would restart each rule at zero, colliding their evidence `step_id`s.
    after_counter = _StepCounter()
    expect_results: list[AssertionResult] = []
    expect_alerts: list[AlertEvent] = []
    # An alert that interrupted one of `expect`'s own queries and matched no `rules` entry — filled by
    # every drain this phase runs, and failing the phase outright once anything lands here (BE-0406
    # Unit 2b), unlike `expect_alerts` above which never fails anything on its own.
    expect_undeclared: list[UndeclaredInterruption] = []
    # The guard's expect-phase dismissing tap: the one actuation that happens outside the step loop, so
    # it is drained here rather than left in the driver's log with no step to carry it (see BE-0315's
    # `expect_alerts` beside it).
    expect_actuations: list[Actuation] = []
    # What the guard saw blocking the screen during the `expect` retry and could not clear (BE-0402).
    expect_block_note = ""
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
    # The offset this interval's recording implies is resolved once it is finalized, in the `finally`
    # below — the exact answer is the finished file's own duration, which does not exist yet here.
    video_interval = next((r for r in recordings if r.kind == "video"), None)
    # Mutable bindings: extract steps populate vars.* during the run; scenario-level
    # expect sees the accumulated values.
    live_bindings: dict[str, str] = dict(bindings or {})

    def run_phase(
        steps: list[Step],
        phase_outcomes: list[StepOutcome],
        phase: str,
        phase_cancelled: CancelSource,
        counter: _StepCounter | None = None,
    ) -> str | None:
        # Every phase shares `live_bindings`, so a `before` step's `vars.*` reaches `steps` and an
        # `after` step can address what `steps` captured; everything else is per-phase.
        return _run_steps(
            driver,
            scenario,
            steps,
            clock,
            sink,
            alert_guard,
            wants_screen_changed,
            phase_outcomes,
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
            capture,
            phase_cancelled,
            phase,
            counter,
        )

    try:
        try:
            if scenario.before:
                # A precondition for the scenario, not a step within it: its failure skips `steps`
                # and `expect` outright, the way an unsatisfiable `preconditions` already fails a
                # scenario before this function is reached at all.
                reason = run_phase(list(scenario.before), before_outcomes, "before", cancelled)
                if reason is not None:
                    failure = "before: " + reason
            if failure is None:
                failure = run_phase(scenario.steps, outcomes, "", cancelled)
            if failure is None and scenario.expect:
                expect = _interp_asserts(scenario.expect, live_bindings)
                clip = _clipboard_for(expect, control)
                if ctx.visual is not None:
                    ctx.visual.capture_actual(driver)
                expect_results = _evaluate_expect(
                    driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
                )
                # A prompt the backend answered or declined while it was interrupting one of
                # `expect`'s own queries. Outside the failure branch below: an `expect` that passed
                # *because* the interruption was answered still has a dismissal to report, and a
                # declined one fails the phase outright regardless of what the assertions found. The
                # step loop's own drain has already run and does not cover this phase's queries, and
                # the next scenario's `setPolicy` clears the buffer outright, so this is the only
                # chance to read what happened here.
                expect_drained = drain_interruptions(driver)
                expect_alerts.extend(expect_drained.alerts)
                expect_undeclared.extend(expect_drained.undeclared)
                if not assertions.passed(expect_results) and alert_guard is not None:
                    event = alert_guard(driver)
                    if event is None and alert_guard.blocked_note:
                        # The guard saw a prompt it could not clear (BE-0402 leaves an alert no rule
                        # identifies alone rather than guessing where to tap). Name it on the
                        # `expect` failure below, which would otherwise report only the assertion
                        # that never held.
                        expect_block_note = alert_guard.blocked_note
                    if event is not None:
                        expect_alerts.append(event)
                        expect_actuations.extend(drain_actuations(driver).records)
                        # The prompt has been tapped, not yet cleared: let the sheet finish leaving
                        # and the screen it covered finish rendering, so the retry below judges the
                        # assertions against a still tree rather than one mid-animation (BE-0406).
                        settle_after_alert_dismiss(
                            driver, clock, transitions=transitions, cancelled=cancelled
                        )
                        if ctx.visual is not None:
                            ctx.visual.capture_actual(driver)
                        # Re-read the clipboard too: clearing the block may have let the app update the
                        # pasteboard, so the retry must compare against the fresh value, not the stale one.
                        clip = _clipboard_for(expect, control)
                        expect_results = _evaluate_expect(
                            driver, expect, network, clock, ctx=replace(ctx, clipboard=clip)
                        )  # retry once
                    # The guard's own probe just now, and the retry's queries when one ran, can
                    # each be interrupted too, and nothing else drains this phase again afterwards
                    # (BE-0406 Unit 2b). Outside the `event is not None` branch above so a probe
                    # that declined without clearing anything (`event is None`) is still covered.
                    retry_drained = drain_interruptions(driver)
                    expect_alerts.extend(retry_drained.alerts)
                    expect_undeclared.extend(retry_drained.undeclared)
                if not assertions.passed(expect_results):
                    failure = "expect: " + _fail_reason(expect_results)
                    if expect_block_note:
                        failure += f" \u2014 {expect_block_note}"
                if expect_undeclared:
                    # Overrides whatever `failure` above holds, even None when `expect` otherwise
                    # passed: an interruption no rule named is evidence the scenario's assumptions
                    # were wrong regardless of what the assertions checked afterward (BE-0406 Unit
                    # 2b). Appended to an existing failure rather than replacing it, so the
                    # assertion mismatch's own detail is not lost alongside the alert that caused it.
                    note = undeclared_interruption_note(expect_undeclared)
                    failure = f"{failure} \u2014 {note}" if failure else "expect: " + note
        except RunCancelled:
            # A cancelled run is a failed run, not a silent gap: the scenario the cancel interrupted
            # (or one whose first boundary was already past it) fails with the one spelling
            # downstream reads, and the `finally` below still finalizes its intervals — so the
            # report and the manifest are written exactly as they are for any other failure
            # (BE-0370).
            failure = CANCELLED_FAILURE
        if scenario.after:
            # Reached on every path out of `steps`/`expect`, the cancelled one included — the same
            # reason the `finally` below finalizes unconditionally.
            failure, after_verdict = _dispatch_after(
                list(scenario.after),
                failure,
                lambda steps, src: run_phase(steps, after_outcomes, "after", src, after_counter),
                clock,
                cancelled,
            )
    finally:
        artifacts = sink.finish_scenario_intervals(sid, recordings)
        # After the finalize, not before it: stopping the recording is what lets its own duration
        # place its origin, which is a measurement rather than the start-confirmation proxy a
        # scenario-start resolution would have to settle for (the correction BE-0346 introduced).
        video_start_offset = _resolve_video_start_offset(video_interval, scenario_start)

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
        before_outcomes=before_outcomes,
        after_outcomes=after_outcomes,
        after_verdict=after_verdict,
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

# Mid-wait TipKit dismisses allowed within one step, for the same reason as the two ceilings above.
# TipKit dismisses on its own rules, so "the scrim tap did not clear the tip" is a state the guard has
# to survive rather than one it can rule out. Unbounded, a guarded wait would then synthesize a tap
# every tick for its whole timeout, each recorded as an actuation, and land one on whatever is
# underneath the moment the tip finally closes on its own. Bounded, "the dismiss didn't take" degrades
# to the step's ordinary failure or timeout, exactly as a mis-set `interrupts` entry does. One hook is
# built per step and shared by both retries, so the total per step is this plus the end-of-step
# dismiss's own single attempt — the same composition the alert guard already documents for
# _GUARD_MAX_ATTEMPTS.
_TIP_MAX_DISMISSES = 2


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


def _dismiss_blocking_tip(driver: base.Driver, scenario: Scenario) -> bool:
    """Clear a TipKit tip blocking the screen, when this scenario opted in; True if one was cleared.

    Deliberately narrow: it fires only after a step already failed, so a passing run never pays a
    query for it, and a scenario that did not ask keeps today's behavior exactly. The driver decides
    what identifies a tip, so no backend-specific selector reaches this layer.
    """
    if not scenario.ios_tip_kit_handling:
        return False
    if base.Capability.HANDLE_TIPKIT_TIP not in driver.capabilities():
        return False
    return driver.dismiss_blocking_tip()


def _tip_poll_hook(
    driver: base.Driver,
    scenario: Scenario,
    interrupt_poll: Callable[[list[base.Element]], bool] | None,
) -> Callable[[list[base.Element]], bool] | None:
    """Compose the mid-wait TipKit dismiss onto a step's interrupt-poll hook, or return it unchanged.

    Dismissing while the wait is still blocked is what keeps most runs off the post-failure retry
    path: the tip goes as soon as a poll sees it, and the wait's remaining budget is spent on the
    target rather than on a screen nothing can reach. The dismiss never ends the wait — `True` is
    reserved for "a recovery failed", so reporting one here would abort a wait that is now free to
    succeed. The poll's tree is passed down so the overwhelmingly common "no tip" answer needs no
    query of its own; only an actual tip costs the driver a fresh snapshot to mint a handle from.
    """
    if not scenario.ios_tip_kit_handling:
        return interrupt_poll
    if base.Capability.HANDLE_TIPKIT_TIP not in driver.capabilities():
        return interrupt_poll

    dismissed = 0

    def poll(elements: list[base.Element]) -> bool:
        nonlocal dismissed
        # The poll's own tree is handed to the driver, so ruling a tip out — the case on nearly every
        # tick — costs no query and a guarded wait polls at its usual rate.
        if dismissed < _TIP_MAX_DISMISSES and driver.dismiss_blocking_tip(elements):
            dismissed += 1
        return interrupt_poll(elements) if interrupt_poll is not None else False

    return poll


def _run_if(
    driver: base.Driver,
    if_block: If,
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
    # The previous step's `after.png` artifact, reused as this step's `before.png` (BE-0407 Unit 1)
    # instead of a fresh `driver.screenshot()`: nothing actuates between the two, so they are the
    # identical pixels. Set only when the shutter actually wrote a screenshot (a `NullSink` writes
    # nothing, so this stays `None` under it); `_handle_action` additionally forces it to `None` for
    # a recovery step, a `handleSystemAlert` step, or any scenario declaring `interrupts` — the
    # cases where an asynchronous interstitial could have arrived since, which the "nothing
    # actuated" premise does not rule out. Unlike `prev_after`, a `web` block does *not* reset this:
    # the shutter always targets the native driver, so the screen it captures is the same one
    # throughout, web block or not.
    prev_after_screenshot: Artifact | None = None
    total_reads: int = 0  # runner-issued screen reads, the BE-0234 read-count yardstick (Unit 1)
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
    capture: list[str] | None
    # Which lifecycle phase this loop is running (BE-0392): "" for the scenario's own `steps`,
    # "before" / "after" for the hook phases. Each phase counts its steps from zero, so the label
    # also namespaces their evidence `step_id`s — without it a hook's `step0` would write into the
    # directory the scenario's own first step already owns.
    phase: str = ""
    # Whether this run has been asked to stop (BE-0370). Read at each step boundary below and handed
    # to the poll loops that back `wait` / `assert`, so cancellation is noticed at a point the
    # pipeline already tolerates a pause rather than partway through an actuation.
    cancelled: CancelSource = not_cancelled


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
            # The step boundary is the cheapest safe point to stop a cancelled run (BE-0370): this
            # step has not acted yet, so nothing is left half-actuated and no artifact is half-written.
            if self.cfg.cancelled():
                raise RunCancelled
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
            label = f"{self.cfg.phase} step" if self.cfg.phase else "step"
            self.cfg.progress(f"{self.cfg.sid} · {label} {idx + 1}: {_step_label(step, kind)}")
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

    def _drain_step_interruptions(self, driver: base.Driver, outcome: StepOutcome) -> None:
        """Drain what interrupted this step, and fail it unconditionally on an undeclared one.

        Shared by every step-handling exit point — `_handle_if`, `_handle_for_each`, `_handle_web`,
        and `_handle_action` alike — each of which queries the driver (a condition check, a
        selector resolution, an action) before any nested step runs, so each can be interrupted the
        same way. One place to call from is what keeps a step kind added later from needing its own
        copy of this check (BE-0406 Unit 2b).
        """
        drained = drain_interruptions(driver)
        outcome.alerts.extend(drained.alerts)
        if drained.undeclared:
            # Overrides `outcome.ok` unconditionally, even for a step that otherwise passed: an
            # interruption no rule named is evidence the scenario's assumptions were wrong
            # regardless of what the step itself checked. Appended to whatever reason the step
            # already carries rather than replacing it, so that reason is not lost alongside the
            # alert that caused it.
            outcome.ok = False
            note = undeclared_interruption_note(drained.undeclared)
            outcome.reason = f"{outcome.reason} \u2014 {note}" if outcome.reason else note

    def _reserve_declared_alert(
        self, driver: base.Driver, step: Step
    ) -> tuple[bool, list[AlertEvent], list[UndeclaredInterruption]]:
        """Push the interruption monitor the button a waiting `handleSystemAlert` step will tap.

        Without this, the monitor knows only `systemAlertHandling.rules` \u2014 so a scenario that
        answers a SpringBoard prompt through a `handleSystemAlert` step alone, never declaring it
        as a rule too, can still meet it here first: an *earlier* action's own interruption reaches
        the monitor before this step's poll ever runs, finds no rule for a prompt only this step
        declares, and fails as undeclared \u2014 even though this very step is a few lines away from
        answering it correctly (BE-0406 Unit 2b review finding). Reserving it for the step's own
        duration, the same way the reactive guard's native probe already reserves it
        (`probe_native`'s `"reserved"` answer), closes that gap.

        Only the `prompt`/`choice` form carries the full identifying label set the monitor's exact
        matching needs; a `sel`-form step names one button, not the alert's whole shape, so it keeps
        today's behavior. Returns whether it pushed and whatever this push's own drain-before-push
        (below) found, so the caller knows whether to restore afterward and can fold that catch into
        the step's own outcome \u2014 a plain `wait`/other step kind, a `sel`-form `handleSystemAlert`, a
        disabled guard, a backend without the opt-in, or a shape this surface cannot safely reserve
        (below) all return `(False, [], [])` and push nothing.
        """
        guard = self.cfg.alert_guard
        hsa = step.handle_system_alert
        if (
            guard is None
            or hsa is None
            or hsa.prompt is None
            or hsa.choice is None
            or self.cfg.locale is None
            or not isinstance(driver, base.InterruptionPolicyTarget)
        ):
            return False, [], []
        # Resolvable without raising: the caller reaches this method only once
        # `_resolve_system_alert` has already resolved this same prompt/choice/locale triple for
        # `interp_step`, so the locale is known-covered.
        shape = system_alert_shapes(hsa.prompt, hsa.choice, self.cfg.locale)[0]
        if shape.excluded_labels:
            # `push_interruption_policy` refuses outright to push a native-reachable rule that
            # carries an exclusion set (the wire format has no room for one, and a silently dropped
            # exclusion would be matched by subset on the runner) \u2014 unreachable today because no
            # step-capable prompt's shape carries one (`_SURFACES` marks every prompt with an
            # exclusion `step: False`), but if one ever does, this reservation must not be the thing
            # that raises past this step's own try/finally and aborts every scenario after it
            # (BE-0406 Unit 2b review finding). Skipping the reservation is the honest answer: a
            # shape needing an exclusion to tell it apart from another alert cannot be reserved
            # without that exclusion, and the monitor cannot express one.
            return False, [], []
        reservation = ResolvedAlertRule(
            identifying_labels=shape.identifying_labels, tap_label=shape.tap_label
        )
        # `setPolicy` clears the monitor's pending drain along with the policy it installs
        # (`InterruptionPolicyStore.setPolicy`), so an interruption the pre-step baseline capture,
        # `before` query, or `guard.clear_before_act` met just before this call \u2014 none of them
        # reserved yet \u2014 would otherwise be wiped here, unread, by the very push meant to start
        # covering this step (BE-0406 Unit 2b review finding). Draining first keeps that record;
        # the caller folds it into the step's own outcome once it is safe to (see the call site).
        drained = drain_interruptions(driver)
        push_interruption_policy(driver, replace(guard, rules=[*guard.rules, reservation]))
        return True, drained.alerts, drained.undeclared

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
        outcome.ok, outcome.reason = _run_if(
            active_driver,
            step.if_,
            self.cfg.network,
            self.state.bindings,
            self.exec_steps,
        )
        outcome.duration_s = self.cfg.clock.now() - start
        self._drain_step_interruptions(active_driver, outcome)
        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"

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
        outcome.ok, outcome.reason = _run_for_each(
            active_driver, step.for_each, self.state.bindings, self.exec_steps
        )
        outcome.duration_s = self.cfg.clock.now() - start
        self._drain_step_interruptions(active_driver, outcome)
        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"

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
                    # native step's `before`: reset around the block on both sides (BE-0234). The
                    # screenshot reuse marker (BE-0407 Unit 1) is unaffected — the shutter always
                    # targets the native driver, web block or not, so the previous leaf step's
                    # `after.png` still describes the same screen this one is about to act on.
                    self.state.prev_after = None
                    failure = self.exec_steps(step.web.steps, web_driver)
                    self.state.prev_after = None
                    ok = failure is None
                    reason = failure or ""
        except base.SelectorError as e:
            ok, reason = False, str(e)
        outcome.ok, outcome.reason = ok, reason
        outcome.duration_s = self.cfg.clock.now() - start
        # `active_driver`, not the inner `web_driver`: the query this drains is the `within`
        # resolution above, on the native driver, before the block ever switches context — the same
        # native-only surface `_handle_action`'s own drain covers (BE-0406 Unit 2b).
        self._drain_step_interruptions(active_driver, outcome)
        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"

    def _seed_prev_after(
        self, active_driver: base.Driver, step_id: str, *, why: str, level: int
    ) -> bool:
        """Query `active_driver` into `self.state.prev_after`; return whether it succeeded.

        Best-effort: a connection/capability failure is logged at `level` and swallowed rather than
        raised, since every call site has a fallback for a `prev_after` that stays `None`.
        """
        try:
            self.state.prev_after = active_driver.query()
        except (ConnectionError, base.UnsupportedAction, OSError) as exc:
            _logger.log(level, "%s: %s (query failed: %s)", step_id, why, exc)
            return False
        self.state.total_reads += 1
        return True

    # Genuinely long: the per-action dispatch on the deterministic run path. Splitting it carries
    # real behavioral risk, so it belongs to BE-0386's ratchet steps rather than the PR that sets
    # the ceiling.
    def _handle_action(  # noqa: C901, PLR0912, PLR0915
        self,
        step: Step,
        active_driver: base.Driver,
        idx: int,
        kind: str,
        outcome: StepOutcome,
        start: float,
    ) -> str | None:
        prefix = f"{self.cfg.phase}-" if self.cfg.phase else ""
        step_id = f"{self.cfg.sid}/{prefix}{step.name or f'step{idx}'}"
        # The report's baseline: the screen this step is about to act on, captured before it acts
        # (BE-0341). It requests only the screenshot, never a tree (BE-0407 Units 3-4): the
        # post-step call below always re-reads and rewrites `elements.json` unconditionally
        # (`_collect_captures` leads with `elements` on every step, success or failure, to the one
        # fixed filename), so a tree written here would be serialized, redacted, and scrubbed only
        # to be clobbered a moment later. The one path that never reaches that post-step call — a
        # step that fails resolving `handleSystemAlert`'s locale, below — writes its own tree
        # explicitly instead, since the baseline is the only capture that path gets.
        # Deliberately ahead of locale resolution below: this baseline depends only on the screen,
        # never on the step's own resolved fields, so a step that fails resolving them still gets it
        # — the run loop's report contract guarantees a pre-step baseline for every leaf step, a
        # failure at this point notwithstanding.
        pre_query_was_fresh = False
        # A `web` block's first nested step resets `prev_after` to `None` around the whole block
        # (BE-0234 Unit 2), so there is nothing to reuse for the `screenChanged` `before` below. A
        # fresh, correctly-targeted `active_driver.query()` here is worth its cost only when
        # `wants_screen_changed` will actually consume it — every later nested step, and every
        # native step, reuses `prev_after` from the previous step's post-step write for free, and
        # neither the interrupt guard nor anything else downstream reads this seed on its own (both
        # only look at `before`, which stays `None` without a screenChanged policy). Not gated on
        # `NullSink`: unlike the elements write BE-0407 dropped, the `before` fallback just below
        # queries regardless of what the sink captures, so skipping this seed under `NullSink` would
        # not save a read — it would only move it to that fallback and lose this one's retry.
        if (
            self.cfg.wants_screen_changed
            and self.state.prev_after is None
            and active_driver is not self.cfg.driver
        ):
            # On success this overlaps with the `before` fallback below, which reads the identical
            # unacted-on screen when `prev_after` is still unset — but on a *transient* web-bridge
            # failure it is what turns that into one retry instead of the fallback's query being the
            # only attempt: this failure is swallowed and logged, and the fallback below tries the
            # same read again. A persistently broken bridge still raises there, uncaught, exactly as
            # it would with no seed at all — this only ever buys one extra try.
            pre_query_was_fresh = self._seed_prev_after(
                active_driver,
                step_id,
                why="pre-step screenChanged seed skipped, web driver query failed",
                level=logging.DEBUG,
            )
        # An inline `rawTree` request stays on the post-step capture below, never on this baseline:
        # `write_raw_tree` persists the driver's *last* read, and the post-step call's always-on
        # `elements` token re-reads the tree on every step, so a dump taken here would describe the
        # pre-action read while the `elements.json` beside it describes the post-action one. Post-step
        # the two land together, where `capture()`'s own stable sort pairs them on the same read.
        # Reuse the previous step's `after.png` bytes instead of a fresh screenshot (BE-0407 Unit
        # 1): nothing *bajutsu* has actuated since, so ordinarily the two are the same pixels. That
        # premise is not proof against an interstitial that appeared asynchronously between the two
        # steps — the exact risk `interrupts` (scenario-level) and `alert_guard` (target-config
        # level) both exist to catch (`before_is_fresh`'s own comment below) — so a recovery step
        # (its whole purpose is to face such a screen), a `handleSystemAlert` step (watching for
        # exactly this kind of surprise arrival), and any scenario declaring `interrupts` or running
        # under an `alert_guard` at all (already paying extra per-step cost for the same risk)
        # always get a fresh shot instead of one that could predate the very screen they exist to
        # show. `capture()` falls back to a real `driver.screenshot()` when this is `None` — also
        # true on the scenario's first step, or the first after a `NullSink` skipped a write.
        reuse_before_screenshot = (
            None
            if (
                self.state.running_recovery
                or kind == "handle_system_alert"
                or self.cfg.interrupts
                or self.cfg.alert_guard is not None
            )
            else self.state.prev_after_screenshot
        )
        outcome.artifacts.extend(
            self.cfg.sink.capture(
                self.cfg.driver,
                step_id,
                ["screenshot.before"],
                reuse_before_screenshot=reuse_before_screenshot,
            )
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
            # Drained here too: nothing can have actuated this early today (only the pre-step
            # baseline capture has run), but leaving the one early return as the single path that
            # skips the drain is how a record would later be stranded into the *next* step's
            # outcome, silently and only for this failure.
            drained = drain_actuations(active_driver)
            outcome.actuations, outcome.dropped_actuations = drained.records, drained.dropped
            # Drained for the same reason, one step further: the pre-step baseline capture just
            # above is itself an XCUITest query that can be interrupted, and this early return is
            # the only path out of the step that would otherwise leave the decline unread until
            # the next scenario's `setPolicy` wipes it (BE-0406 Unit 2b).
            self._drain_step_interruptions(active_driver, outcome)
            # This return skips the post-step capture entirely, so it is the one path that must
            # still write a tree itself (BE-0407 Unit 3 dropped it from the baseline above, on the
            # assumption that the post-step call always overwrites it). `elements.before`, matching
            # the baseline's own `screenshot.before`, so a viewer pairs the two rather than treating
            # this as a mismatched `web`-block pair. This exception fires only for a
            # `handleSystemAlert` step (only it resolves a locale-dependent label), which the
            # screenshot gate above always shoots fresh for — so the tree here is always queried
            # fresh too, never a carried-over `prev_after`, or a viewer could pair a just-captured
            # screenshot with an older tree that predates an interstitial the screenshot already
            # shows. A sink that reads nothing must still pay nothing, so this whole write is
            # skipped under a `NullSink`.
            # Unlike the pre-step seed above, nothing downstream re-reads for this path — the
            # post-step capture never runs — so a failed query here is the step's tree, lost for
            # good rather than merely deferred. Warn, matching the wait-timeout diagnostic's own
            # "disclose the lost evidence loudly" a few hundred lines down. `not isinstance(...,
            # NullSink)` short-circuits `_seed_prev_after` itself, so a sink that reads nothing
            # still pays nothing; gating on the call's own success, not just on `prev_after` being
            # set, matters because `_seed_prev_after` leaves a stale value in place when the query
            # fails, and writing that stale tree next to this step's fresh screenshot is the exact
            # mismatch this whole block exists to avoid.
            if not isinstance(self.cfg.sink, NullSink) and self._seed_prev_after(
                active_driver,
                step_id,
                why="step failed before acting and its element tree could not be captured",
                level=logging.WARNING,
            ):
                outcome.artifacts.extend(
                    self.cfg.sink.capture(
                        self.cfg.driver,
                        step_id,
                        ["elements.before"],
                        elements=self.state.prev_after,
                        elements_source=active_driver.name,
                    )
                )
            self.state.outcomes.append(outcome)
            # The step keeps `before.png` (the pre-step baseline) and the tree just written above,
            # both describing the same pre-action screen. Nothing acted, so there is no post-action
            # state to record — adding an `after.png` later would pair pixels from then with a tree
            # from now.
            return f"step {idx} ({kind}): {outcome.reason}"
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
        # The mid-wait TipKit dismiss rides the same poll hook, so a wait blocked behind a tip clears
        # it without a query of its own — and a step with no `interrupts` still gets the gate.
        tip_poll = _tip_poll_hook(
            active_driver,
            self.cfg.scenario,
            guard.observe if guard is not None else None,
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
            phase_label = f"{self.cfg.phase} step" if self.cfg.phase else "step"
            prefix = f"{self.cfg.sid} · {phase_label} {idx + 1}"

            def wait_tick(remaining: float, _desc: str = desc, _prefix: str = prefix) -> None:
                assert self.cfg.progress is not None
                self.cfg.progress(f"{_prefix}: waiting {_desc} ({remaining:.0f}s left)")

        # Populated only when the reservation below actually pushes (a `prompt`/`choice`
        # `handleSystemAlert` step, guard on) — stays empty for the pre-act short-circuit branch,
        # so the merge below is a no-op there.
        reserved_alerts: list[AlertEvent] = []
        reserved_undeclared: list[UndeclaredInterruption] = []
        if guard is not None and guard.failure is not None:
            # The pre-act clear already decided the outcome (a recovery step failed): skip the
            # step's own action rather than poke a screen the failed recovery left broken —
            # symmetric with how a wait's `on_interrupt_poll` aborts the poll instead of running
            # on. The rest of the pipeline below (evidence capture, outcome bookkeeping) still
            # runs unchanged, exactly as it does for any other failed step.
            ok, reason, snapshot = False, guard.failure, None
            results: list[AssertionResult] = []
        else:
            # Push the interruption monitor this step's own declared alert for the
            # duration of its own wait, restored below whether it passes, fails, or
            # raises — see `_reserve_declared_alert` for why (BE-0406 Unit 2b).
            reservation_pushed, pushed_alerts, pushed_undeclared = self._reserve_declared_alert(
                active_driver, step
            )
            reserved_alerts.extend(pushed_alerts)
            reserved_undeclared.extend(pushed_undeclared)
            # `setPolicy` clears the monitor's pending drain along with the policy it installs
            # (`InterruptionPolicyStore.setPolicy`) — harmless once per scenario, but the
            # reservation makes the restore below a *second* push inside this one step, which
            # would otherwise wipe whatever the reservation's own window recorded before the
            # step-end drain a few lines down ever reads it (BE-0406 Unit 2b review finding).
            # Captured here in the `finally`, before that restore, and merged into `outcome` after
            # `outcome.ok`/`outcome.reason` are (re)assigned below — merging any earlier would be
            # silently discarded by that assignment.
            try:
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
                    on_interrupt_poll=tip_poll,
                    cancelled=self.cfg.cancelled,
                )
                if guard is not None and guard.failure is not None:
                    # A mid-wait recovery failure is a decided outcome — fail on it now rather than
                    # firing the end-of-step alert-guard dismiss/retry against the screen the failed
                    # recovery left, symmetric with the pre-act short-circuit above.
                    ok, reason = False, guard.failure
                else:
                    # The two end-of-step guards are checked in sequence, not as one `elif` ladder: a
                    # tip and a system alert can both be up, so a tip dismissed by the first must not
                    # consume the failure and leave the alert — the case the alert guard exists for —
                    # unhandled. Each still fires at most once per step, so a step's retries stay bounded
                    # at one per guard, and each is skipped once the step passes.
                    #
                    # A failed `handleSystemAlert` step is the one case the alert guard skips outright
                    # (BE-0406): `wait_for_system_alert` already drove this exact guard, reserved against
                    # this step's own selector, for the step's whole timeout — a second, unreserved probe
                    # here adds no coverage the mid-wait one lacked, and could tap the step's own alert
                    # through the guard's looser fallback policy. That would both decide, on the step's
                    # behalf, the very prompt it was placed to answer, and discard the specific reason
                    # (no alert / an unmatched alert / an ambiguous one) for the generic timeout a doomed
                    # retry against an now-cleared screen produces instead.
                    guard_done = kind == "handle_system_alert"
                    # The dismiss can refuse loudly: `AmbiguousSelector` on two dismiss regions, or
                    # `ElementNotTappable` when something covers the scrim itself — which is exactly the
                    # tip-plus-system-alert case below. `ElementNotTappable` is not a `SelectorError`
                    # (`_run_step_body`'s own net lists it separately), so both must be named here.
                    # Unlike the mid-wait call, which raises inside that net, this one sits outside every
                    # `try`: an escape would unwind past `run_scenario` and abort the *whole run*,
                    # discarding the verdicts of every scenario that already passed. Convert it to this
                    # step's failure, which is what a refused actuation means everywhere else.
                    tip_cleared = False
                    if not ok:
                        try:
                            tip_cleared = _dismiss_blocking_tip(active_driver, self.cfg.scenario)
                        except (base.SelectorError, base.ElementNotTappable) as exc:
                            ok, reason = False, str(exc)
                            # Skip the alert guard too: the driver refused to act on this screen, so
                            # poking it again would be reacting to a state nothing has resolved.
                            guard_done = True
                    if tip_cleared:
                        # A TipKit tip hides what it covers from the tree, so the step it blocked failed
                        # as `ElementNotFound` as readily as `ElementNotTappable` — either way the target
                        # was unreachable for a reason this one dismiss just cleared, so it gets one more
                        # shot. Reached only when a tip was actually dismissed, so a step that failed for
                        # any other reason still fails on its first attempt, with no retry to mask it.
                        wait_trace = WaitTrace() if wait_trace is not None else None
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
                            on_interrupt_poll=tip_poll,
                            cancelled=self.cfg.cancelled,
                        )
                    # Re-read `guard.failure`: the tip retry above runs a whole step body, whose own
                    # mid-wait interrupt recovery can newly fail — and that is a decided outcome, so it
                    # must not be followed by an alert dismiss against the screen it left.
                    if guard is not None and guard.failure is not None:
                        ok, reason = False, guard.failure
                    elif not ok and not guard_done and self.cfg.alert_guard is not None:
                        event = self.cfg.alert_guard(active_driver)
                        note = self.cfg.alert_guard.blocked_note
                        if event is None and note and note not in reason:
                            # Same as the `expect` site: an alert the guard will not guess at still
                            # explains the failure, so the step says so instead of failing as a bare
                            # `element not found` (BE-0402). `not in reason` because a guarded `wait`
                            # already carried the note out of `_wait`: this guard re-probes the same
                            # still-unanswered alert, so appending unconditionally would say it twice.
                            reason = f"{reason} \u2014 {note}"
                        if event is not None:
                            outcome.alerts.append(event)
                            # Same reason as the `expect` site: the retry below actuates, and a sheet
                            # still animating away is a screen the step would fail against for a reason
                            # that is not its own (BE-0406).
                            settle_after_alert_dismiss(
                                active_driver,
                                self.cfg.clock,
                                transitions=self.cfg.transitions,
                                cancelled=self.cfg.cancelled,
                            )
                            wait_trace = WaitTrace() if wait_trace is not None else None
                            # The retry is the end-of-step "one more shot": it does not re-arm the
                            # mid-wait guard (no alert_guard passed), so one dismissed prompt buys one
                            # extra attempt and no more.
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
                                on_interrupt_poll=tip_poll,
                                cancelled=self.cfg.cancelled,
                            )
                # A failure inside an interrupt's own recovery `steps` fails the step loudly, rather
                # than being swallowed while the run continues against a screen the recovery left
                # broken (determinism first). It overrides a step that otherwise passed — this is the
                # wait path's version of the pre-act short-circuit above (guard.failure can newly
                # become True during `_run_step_body`'s `on_interrupt_poll` calls).
                if guard is not None and guard.failure is not None:
                    ok, reason = False, guard.failure
            finally:
                if reservation_pushed:
                    # Extends rather than replaces: `pushed_alerts`/`pushed_undeclared` above are
                    # whatever the reservation's own push-time drain already caught, and this drain
                    # covers everything since — both windows belong to this one step.
                    drained_reservation = drain_interruptions(active_driver)
                    reserved_alerts.extend(drained_reservation.alerts)
                    reserved_undeclared.extend(drained_reservation.undeclared)
                    push_interruption_policy(active_driver, self.cfg.alert_guard)
        outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results
        outcome.duration_s = self.cfg.clock.now() - start
        if reserved_alerts or reserved_undeclared:
            outcome.alerts.extend(reserved_alerts)
            if reserved_undeclared:
                outcome.ok = False
                note = undeclared_interruption_note(reserved_undeclared)
                outcome.reason = f"{outcome.reason} \u2014 {note}" if outcome.reason else note
        # What the driver actually did to the screen during this step. Drained once, after the body has
        # finished, rather than per attempt: when the alert guard dismissed a prompt and retried, both
        # attempts really happened to the device and belong on this step in the order they occurred —
        # as does the guard's own dismissing tap, on the step it interrupted. `active_driver`, not
        # `cfg.driver`, because a step inside a `web` block actuates the WebView driver; nothing
        # actuates the native driver during such a step, so nothing is stranded.
        drained = drain_actuations(active_driver)
        outcome.actuations, outcome.dropped_actuations = drained.records, drained.dropped
        # A prompt the backend answered or declined while it was interrupting one of this step's own
        # interactions. Drained beside the actuations, and for the same reason: it really happened to
        # the device during this step, so it belongs on this step's outcome rather than nowhere.
        self._drain_step_interruptions(active_driver, outcome)

        # The post-action shutter, taken here rather than down with the rest of the post-step
        # capture. Every step records `after.png` (the capture call below drops `screenshot.after`
        # from its own list, so no token list is needed yet; any other screenshot modifier a rule
        # asks for writes its own filename and stays there). Taking it here puts it ahead of the
        # three consumers that can force a tree read between this point and that call — a
        # `screenChanged` policy's `before` comparison, a `for`-wait timeout diagnostic, and
        # `extract` — where a read costs ~2.4s on adb; shooting after one of those would leave the
        # tree the older half of the pair by that whole read.
        #
        # It is not the first read on a *non-mutating* step, and cannot be: `assert` and `wait`
        # already queried a tree to evaluate themselves, and the runner reuses it rather than paying
        # a second identical query (BE-0259, `_run_step_body`'s `snapshot` seeding `_ScreenRead`
        # below). Such a step's `elements.json` therefore predates its `after.png` by this shutter's
        # own latency — a `wait for` that returns the instant its target appears can pair a tree read
        # mid-transition with pixels a moment later. Dropping the seed here would swap that for the
        # opposite skew and a full tree read per `assert`/`wait` step, so the reuse stays and the
        # guarantee is stated for what it is: the shutter leads every consumer downstream of it.
        after_shot = self.cfg.sink.capture(self.cfg.driver, step_id, ["screenshot.after"])
        outcome.artifacts.extend(after_shot)
        # Remembered for the *next* step's pre-step baseline to reuse as its `before.png` (BE-0407
        # Unit 1) instead of a fresh screenshot — `None` when nothing was actually written (a
        # `NullSink`), so a step with no evidence never looks like it has a file to reuse. Selected
        # by `kind`, not position, so a sink returning something other than one screenshot for this
        # request could never feed the wrong artifact into the next step's reuse.
        self.state.prev_after_screenshot = next(
            (a for a in after_shot if a.kind == "screenshot"), None
        )

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

        # Read the produced value back out of the bindings the handler just wrote, so the run's
        # record shows which value this step actually used (BE-0377). Evidence only — the verdict is
        # unchanged either way.
        if outcome.ok and interp_step.generate is not None:
            outcome.generated = self.state.bindings.get(f"vars.{interp_step.generate.into.var}")

        # This call records the post-action *tree*: `_collect_captures` always leads with
        # `elements`, so every step keeps one whatever the scenario asked for. The screenshot
        # half is not on that list — `_handle_action` shot `screenshot.after` right after the
        # action, and the `instant` filter below drops the token. `elements.json` has one fixed
        # name, so this write replaces the pre-step baseline's pre-action tree.
        # `screenshot.before` is excluded for the mirror-image reason (BE-0341): the baseline
        # above wrote that file from the true pre-action state, so re-taking it here would
        # silently mislabel a post-action pixel as `before.png`.
        fired = _collect_captures(
            self.cfg.scenario, step, kind, outcome.ok, screen_changed, self.cfg.capture
        )
        # Interval kinds are recorded scenario-wide (run_scenario), so only the
        # instant kinds are captured per step here. A `web` block captures against the native
        # `driver`, so it must read the active (web) tree here rather than let the native writer
        # fall back to a mismatched tree (BE-0234 Unit 2).
        instant = [t for t in fired if _kind_of(t) not in intervals.INTERVAL_KINDS]
        if active_driver is not self.cfg.driver:
            # A `web` block's capture call below always targets the native `self.cfg.driver` (a
            # `WebContextDriver` cannot screenshot), but `write_raw_tree` would then ask that native
            # driver for `last_raw_source()` — whatever adb/XCUITest read before this block began,
            # an unrelated backend entirely, next to this step's *web* `elements.json`. Drop the
            # request rather than pair the two: no artifact beats a mismatched one.
            instant = [t for t in instant if _kind_of(t) != "rawTree"]
        # `screenshot.after` was already shot above, right after the action; re-taking it here would
        # overwrite that pixel with a later one and leave a duplicate entry in the manifest. This
        # also swallows a scenario's own request for it (a bare `screenshot`, normalized in
        # `_collect_captures`, or a `capturePolicy` rule's `screenshot.after`) — the shutter above
        # already satisfied it, from a moment closer to the action than this call could manage.
        instant = [t for t in instant if t != "screenshot.after"]
        # The tree read goes through `screen.get()` rather than being left to the sink's own writer
        # (`write_elements`, when `elements=None`): a read issued inside the sink is invisible to
        # `_ScreenRead`, so it is neither counted in `total_reads` nor carried into `prev_after` —
        # and the next step's pre-step baseline, finding `prev_after` unset, pays a second read for
        # the same screen. Routing it here costs one read per step instead of two, the reuse
        # BE-0234 Unit 2 is built on (~2.4s per read on adb). A sink that writes nothing must still
        # pay nothing, hence the `NullSink` guard the pre-step baseline uses too.
        writes_elements = any(_kind_of(t) == "elements" for t in instant) and not isinstance(
            self.cfg.sink, NullSink
        )
        els = (
            screen.get()
            if active_driver is not self.cfg.driver or writes_elements
            else screen.cached
        )
        outcome.artifacts.extend(
            self.cfg.sink.capture(
                self.cfg.driver, step_id, instant, elements=els, elements_source=active_driver.name
            )
        )
        if screen.queried:
            self.state.total_reads += 1
        # Seed the next step's `before` only with a tree we actually read; if we skipped the
        # read, the next `before` reads fresh (BE-0234 Unit 2).
        self.state.prev_after = screen.cached

        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"


def _run_steps(
    driver: base.Driver,
    scenario: Scenario,
    steps: list[Step],
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
    capture: list[str] | None = None,
    cancelled: CancelSource = not_cancelled,
    phase: str = "",
    counter: _StepCounter | None = None,
) -> str | None:
    """Run one phase's step loop, appending outcomes; return the failure string or None.

    ``steps`` is the list to run — the scenario's own, or a `before` / `after` hook's (BE-0392).
    Each phase gets its own `StepLoopState`, so its steps are numbered from zero and reported as
    their own block, while ``bindings`` stays the one dict every phase shares.

    ``counter`` continues an already-started phase's numbering. The `after` phase runs one call per
    dispatched rule, so without it every rule would restart at zero — two rules' first steps would
    then claim the same evidence `step_id` and overwrite each other's screenshots.

    ``bindings`` is a mutable dict (guaranteed by ``run_scenario``) — extract
    steps add ``vars.*`` entries so that subsequent steps and scenario-level
    ``expect`` can reference them."""
    assert bindings is not None
    state = StepLoopState(counter=counter or _StepCounter(), outcomes=outcomes, bindings=bindings)
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
        capture=capture,
        phase=phase,
        cancelled=cancelled,
    )
    result = _StepRunner(state, cfg).exec_steps(steps, driver)
    _logger.debug("%s: %d runner-issued screen reads (BE-0234)", sid, state.total_reads)
    # No end-of-run safety capture here: every step that acts shoots its own `after.png` in
    # `_handle_action`, so the net only reached the step that returns before acting at all, where it
    # paired post-run pixels with a pre-action tree (BE-0341, "Later revision").
    return result
