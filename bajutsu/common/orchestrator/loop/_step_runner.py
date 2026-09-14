"""The step loop itself: drive a scenario's steps over shared state and run-invariant config."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial

from bajutsu.common.assertions import AssertionResult
from bajutsu.common.cancellation import RunCancelled
from bajutsu.common.drivers import base, tracing
from bajutsu.common.drivers.webview import WebContextDriver
from bajutsu.common.evidence import Artifact, NullSink, intervals, start_after_screenshot
from bajutsu.common.orchestrator.actions import _action_of, _step_label
from bajutsu.common.orchestrator.evidence_rules import _collect_captures, _kind_of, _run_extract
from bajutsu.common.orchestrator.substitution import _interp_step, _resolve_system_alert
from bajutsu.common.orchestrator.types import (
    AlertEvent,
    ResolvedAlertRule,
    StepOutcome,
    UndeclaredInterruption,
    drain_actuations,
    drain_interruptions,
    push_interruption_policy,
    undeclared_interruption_note,
)
from bajutsu.common.orchestrator.waits import (
    WaitTick,
    WaitTrace,
    describe_wait,
    settle_after_alert_dismiss,
)
from bajutsu.common.scenario import (
    Selector,
    Step,
    UncoveredSystemAlertLocale,
    interp,
    system_alert_shapes,
)

from ._functions import (
    _dismiss_blocking_tip,
    _run_for_each,
    _run_if,
    _run_step_body,
    _settle_extract_read,
    _tip_poll_hook,
)
from ._interrupt_guard import _InterruptGuard
from ._loop_config import _LoopConfig
from ._screen_read import _ScreenRead
from ._shared import _logger
from .step_loop_state import StepLoopState


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

        # `bajutsu run --trace-driver` (BE-0415): attributes every driver/transport/subprocess
        # record made while this step runs to `f"{idx:02d}:{kind}"`, matching `trace_run.py`'s own
        # step key, and a no-op when no trace is open.
        with tracing.traced_step(f"{idx:02d}:{kind}"):
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
                        # The guard's own call settles the screen after every round it dismisses
                        # something in (BE-0418), the last one included, so nothing here settles
                        # again before the retry below reads the screen.
                        cleared = self.cfg.alert_guard(
                            active_driver,
                            outcome.alerts,
                            settle=lambda: settle_after_alert_dismiss(
                                active_driver,
                                self.cfg.clock,
                                transitions=self.cfg.transitions,
                                cancelled=self.cfg.cancelled,
                            ),
                        )
                        # Read here, appended only after the retry below, which reassigns `reason`
                        # outright and would otherwise discard it.
                        note = self.cfg.alert_guard.blocked_note
                        if cleared:
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
                        if not ok and note and note not in reason:
                            # Same as the `expect` site: an alert the guard could not fully clear
                            # still explains the failure, so the step says so instead of failing as a
                            # bare `element not found` (BE-0402). Gated on the note alone, not on
                            # `cleared`: a multi-round call can clear a stacked alert while leaving a
                            # second one unhandled, so the two are no longer mutually exclusive the
                            # way a single-shot dismiss made them (BE-0418). `not ok` so a step the
                            # retry actually passed carries no stray failure note. `not in reason`
                            # because a guarded `wait` already carried the note out of `_wait`: this
                            # guard re-probes the same still-unanswered alert, so appending
                            # unconditionally would say it twice.
                            reason = f"{reason} \u2014 {note}"
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
        # guarantee is stated for what it is: the shutter's *start* leads every consumer downstream
        # of it.
        #
        # Its start, not its completion, because the shot is begun here and joined at the end of this
        # step so the post-step tree read runs inside it (BE-0407 Unit 2). What that buys is that the
        # tree no longer waits behind the shot: it starts at the action rather than after the round
        # trip. What it costs is the fixed *direction* of the skew above — the pixels can now be up
        # to the shot's own latency *newer* than the tree. In the other direction the separation is
        # whatever the post-step read costs, an `extract` settle poll included, exactly as before. So
        # a consumer added between here and the post-step capture may rely on neither the order nor a
        # small bound.
        #
        # Only a backend that says its channel admits a second call in flight defers at all — adb
        # does, XCUITest does not (see `base.BackgroundScreenshotProvider`) — so on every other
        # backend the shot both starts and completes right here, order intact.
        after_shot: list[Artifact] = []
        body_completed = False
        finish_after_shot = start_after_screenshot(self.cfg.sink, self.cfg.driver, step_id)
        try:
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
            # half is not on that list — `_handle_action` started `screenshot.after` right after
            # the action, and the `instant` filter below drops the token. `elements.json` has one fixed
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
            # `screenshot.after` was already started above, right after the action; re-taking it here
            # would write the same path while that shot may still be in flight (BE-0407 Unit 2), and
            # leave a duplicate entry in the manifest. This also swallows a scenario's own request for
            # it (a bare `screenshot`, normalized in `_collect_captures`, or a `capturePolicy` rule's
            # `screenshot.after`) — the shutter above already satisfied it, from a moment closer to
            # the action than this call could manage.
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
                    self.cfg.driver,
                    step_id,
                    instant,
                    elements=els,
                    elements_source=active_driver.name,
                )
            )
            body_completed = True
        finally:
            # In a `finally` so a pending shot can never outlive the step that took it and land while
            # the *next* step is actuating — the pixels would then show a screen this step never saw.
            # It is also what keeps `after.png`'s bytes on the path where the step body raises, which
            # the synchronous shutter got for free by running before any of it. The artifact *record*
            # is not kept there, and does not need to be: the outcome carrying it is discarded with
            # the step either way.
            try:
                after_shot = finish_after_shot()
            except Exception as exc:
                # A device that vanished mid-step fails the read and then fails the shot against the
                # same device. Raising here would report the shot's symptom in place of the read's
                # cause, so on a step already failing the lost evidence is disclosed loudly instead —
                # the same trade the wait-timeout diagnostic makes a few lines up. With the body
                # through, the shot's own failure is the step's, exactly as the synchronous one was.
                # A local flag, not `sys.exception()`: that reports whatever exception the *thread*
                # is handling, which would also answer "something failed" for a caller that ran this
                # step from inside its own `except` — none does today, and this cannot start to.
                if body_completed:
                    raise
                _logger.warning("dropping this step's after.png: capture failed: %s", exc)
        outcome.artifacts.extend(after_shot)
        # Remembered for the *next* step's pre-step baseline to reuse as its `before.png` (BE-0407
        # Unit 1) instead of a fresh screenshot — `None` when nothing was actually written (a
        # `NullSink`), so a step with no evidence never looks like it has a file to reuse. Selected
        # by `kind`, not position, so a sink returning something other than one screenshot for this
        # request could never feed the wrong artifact into the next step's reuse.
        self.state.prev_after_screenshot = next(
            (a for a in after_shot if a.kind == "screenshot"), None
        )
        if screen.queried:
            self.state.total_reads += 1
        # Seed the next step's `before` only with a tree we actually read; if we skipped the
        # read, the next `before` reads fresh (BE-0234 Unit 2).
        self.state.prev_after = screen.cached

        self.state.outcomes.append(outcome)
        return None if outcome.ok else f"step {idx} ({kind}): {outcome.reason}"
