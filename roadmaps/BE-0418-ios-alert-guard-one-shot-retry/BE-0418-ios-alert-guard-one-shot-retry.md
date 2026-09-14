**English** · [日本語](BE-0418-ios-alert-guard-one-shot-retry-ja.md)

# BE-0418 — Let the end-of-step alert guard clear more than one alert, and retry a tap that misses

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0418](BE-0418-ios-alert-guard-one-shot-retry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0418") |
| Implementing PR | [#1979](https://github.com/bajutsu-e2e/bajutsu/pull/1979) |
| Topic | Platform support |
| Related | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md), [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md), [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) |
<!-- /BE-METADATA -->

## Introduction

`systemAlertHandling`'s guard answers a blocking system alert through two different mechanisms. Which
one runs depends on where in a step a scenario meets the alert. A `wait` step drives
`_AlertGuardGate`, which polls the screen on every tick. It can clear a whole sequence of alerts
across however many polls the wait allows. An ordinary action step — a `tap`, for instance — carries
no such polling loop. Once it fails, `AlertGuardConfig.__call__` gets one attempt to clear whatever
blocks the screen. The step then retries once, and the run moves on.

We propose letting that one attempt clear more than one alert, and retry a tap that misses on its
first try. Today it does neither. It resolves at most one alert per call. On the one path that can
clear an alert an application raises inside its own process, it gives up the moment a tap comes back
`ElementNotTappable` — the same "not yet reachable" condition `_AlertGuardGate` has carried a bounded
retry for since a real device first measured it. The result: a step fails with no alert-related note
in its reason, precisely when two prompts land close together or a tap catches an alert still
mid-animation. Both are common on iOS, and the polling guard already handles both.

## Motivation

A scenario's `tap` step can fail intermittently against an in-app-process alert — iOS's Save Password
prompt, notably. Its failure reason names nothing about the alert, just the target element the step
could not reach. The same scenario, run again, passes. Investigating one such run turned up two
concrete mechanisms in `AlertGuardConfig`
([bajutsu/common/orchestrator/types/alert_guard_config.py](../../bajutsu/common/orchestrator/types/alert_guard_config.py)),
the reactive guard's per-scenario configuration. Both trace to the same design choice: its `__call__`
method runs a single dismiss attempt, not a loop.

The first mechanism is a stacked-alert gap. iOS commonly queues more than one prompt after a single
user action — a notification-authorization request queued behind, or ahead of, a save-password
prompt the same sign-in raises. `__call__` (lines 172–191) reads `probe_native` and returns the
moment that probe reports `"dismissed"`, the SpringBoard-owned prompt cleared. It never checks
whether the application's own process still has the save-password sheet up underneath. The caller
sees a cleared alert and retries the step once. That retry fails again against the sheet `__call__`
never looked at, with no note explaining why.

The second mechanism is a landing-race gap, and it affects even a single alert.
`dismiss_from_tree_once` (lines 127–170), the one-shot counterpart to the mid-wait
`_AlertGuardGate._dismiss_from_tree`, taps the alert's button. It treats `ElementNotTappable` as a
reason to walk away: return `None`, set no note, retry nothing. The mid-wait path treats the same
exception differently. Its own docstring records a scrim that still covers a sheet's button while the
sheet's presentation animation finishes, and it carries a bounded retry (`_TREE_RETAP_DELAY`,
`_TREE_DISMISS_MAX_TAPS`) for that case, in
[`_alert_guard_gate.py`](../../bajutsu/common/orchestrator/waits/_alert_guard_gate.py) (lines
329–343). An alert that has *just* stopped being blocked by another one — the stacked case above,
resolved one layer at a time — sits in that same window: its own entrance animation is often still
running the instant this one-shot path gets its single try.

Both gaps trace to one cause, and call for one fix. `__call__` decides once whether anything blocks
the screen, then stops, rather than looping until the screen actually clears or a small bound is hit.
`_AlertGuardGate` already loops, because a `wait` step's own poll cycle drives it once per poll.
`AlertGuardConfig.__call__` gets no such cycle; it has to loop on its own. A later reader can tell
this landed by reproducing the motivating case. That case is two alerts raised close together after
an ordinary action step, with no `wait` step placed to cover the interruption. That scenario should
then pass reliably. A step that still fails should name, in its own reason, any alert the guard could
not clear — not a bare "element not found" that says nothing about a prompt on screen at all.

## Detailed design

### Unit 1 — loop `AlertGuardConfig.__call__` until the screen clears or a bound is reached

`__call__` changes from a single `probe_native` / `dismiss_from_tree_once` pair to a bounded loop
over that same pair. A new constant, `_GUARD_CALL_MAX_ROUNDS` (proposed: 3), caps the loop. The value
matches the order of `_TREE_DISMISS_MAX_TAPS`, the mid-wait path's own tap ceiling. Each round takes
one of four actions.

1. Calls `probe_native`. A `"dismissed"` result records the event and starts the next round, rather
   than returning right away. A SpringBoard-owned alert stacked in front of an in-tree one no longer
   ends the call before the second alert is even read.
2. On `"absent"`, calls the revised `dismiss_from_tree_once` (Unit 2). A returned `AlertEvent`
   records the event and starts the next round, the same as a native dismissal.
3. Still on `"absent"`, a `"not_tappable"` result (Unit 2) starts the next round without recording a
   dismissal — the same retry a scrim mid-animation gets in the mid-wait path.
4. Any other outcome — `"unhandled"`, `"reserved"`, `"incapable"`, or an `"absent"` state
   `dismiss_from_tree_once` can no longer match — ends the loop. Nothing further here can act on what
   remains, as today.

Every round that records a dismissal or a `"not_tappable"` result runs the existing
`settle_after_alert_dismiss`
([`_functions.py:687`](../../bajutsu/common/orchestrator/waits/_functions.py)) before the loop reads
the screen again *or returns*. The round that exhausts `_GUARD_CALL_MAX_ROUNDS` is included, since
that round has no next round to settle before. Unit 3 removes the caller's own settle on the strength
of this guarantee holding on every exit. This is otherwise unchanged from how the settle already runs
once today: a prompt tapped a moment ago has not necessarily vanished yet. A round that reads a
tree still mid-animation risks matching nothing at all.

`blocked_note` keeps its existing meaning for what a probe saw and could not clear. It gains one
case that meaning does not cover today: a loop that exhausts `_GUARD_CALL_MAX_ROUNDS` on a
`"not_tappable"` result sets `blocked_note = uncleared_prompt_note(label)`
([`types/_functions.py:51`](../../bajutsu/common/orchestrator/types/_functions.py)). This names the
label Unit 2's return carries, the same note `_alert_guard_gate.py` writes when it gives up on a
showing. Without it, the native probe reports `"absent"` for an in-app-process sheet the SpringBoard
query cannot see. The existing `state == "unhandled"` condition then leaves the note empty, and the
motivating case — the one this proposal exists for — still reads as a bare missing element. A step
that still fails once the loop hits its bound now names that leftover alert in its own reason, from
whichever of the two origins (an unhandled native alert, or an obstructed in-tree one) produced it.

### Unit 2 — give the in-tree tap the same landing-race retry the mid-wait path already has

`dismiss_from_tree_once` stops folding `ElementNotTappable` in with `ElementNotFound` and
`AmbiguousSelector`, which one `except` clause answers with `None` today
([`alert_guard_config.py:165`](../../bajutsu/common/orchestrator/types/alert_guard_config.py)). It
gets its own branch. The return widens from `AlertEvent | None` to a shape that keeps three outcomes
distinguishable. A dismissal is still an `AlertEvent`. Visible but not yet reachable becomes a
`"not_tappable"` sentinel, paired with the label the method already resolved via `match_alert_rule`
before attempting the tap — no extra lookup. Nothing to do stays `None`: the prompt closed itself, or
the match turned ambiguous. The other two exceptions keep returning `None` and keep ending the loop.
Retrying a prompt that closed itself, or a label that resolved ambiguously, would spend the whole
`_GUARD_CALL_MAX_ROUNDS` budget on a tap that will never land.

This mirrors `_alert_guard_gate.py`'s own retry for the same exception, without copying its full
state machine (`_TREE_RETAP_DELAY`, `_TREE_DISMISS_MAX_TAPS`, `_tree_signature`). The mid-wait path
needs that machinery because a `wait` step's poll cadence can call it repeatedly against one alert.
Across those repeated polls, it has to tell a lingering fade apart from a tap the application never
acted on. `dismiss_from_tree_once` runs once per failed step. A short, round-bounded retry serves the
same purpose there, without carrying state across separate calls.

### Unit 3 — change `__call__`'s contract to report every alert it clears

`__call__`'s signature changes from `(driver: base.Driver) -> AlertEvent | None` to
`(driver: base.Driver, alerts: list[AlertEvent]) -> bool`. This matches `_AlertGuardGate`'s existing
pattern: append directly to a caller-supplied list, rather than return one event. A single
`AlertEvent | None` return can no longer represent what a multi-round call clears. A caller reading
the last event alone would under-report every alert the earlier rounds dismissed.
[BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md)'s Unit
2a raised the same under-reporting risk for a different call site.

Both call sites move to the new contract, and their note-appending changes alongside the return
type, not only the retry check. Today, `event is not None` and a non-empty `blocked_note` are
mutually exclusive: `__call__` clears the note on `"dismissed"`. So `if event is None and note …`
already works as the gate for both, as things stand. A multi-round call breaks that exclusivity.
Round 1 can dismiss the SpringBoard-owned prompt while round 2 leaves a second one unhandled, so
`cleared` is `True` and `blocked_note` is still non-empty. Both sites gate the note on the note
alone from here on, not on `cleared`. The stacked case then still names what remained, even though
the call cleared something. `cleared` alone decides whether the retry runs.

- [`_step_runner.py:653–695`](../../bajutsu/common/orchestrator/loop/_step_runner.py) — the
  end-of-step retry. `event = self.cfg.alert_guard(active_driver)` becomes a call that appends
  directly into `outcome.alerts`; the `if event is not None` branch that follows becomes `if
  cleared`. The note branch beside it does *not* become `if not cleared`: `if event is None and
  note` becomes `if note and note not in reason`, gating on the note alone, per the paragraph
  above. `settle_after_alert_dismiss` no longer runs here: Unit 1 already settles after every round
  that dismissed or found something not yet tappable, the last one included.
- [`_functions.py:752–769`](../../bajutsu/common/orchestrator/loop/_functions.py) — the `expect`
  retry. The guard's own result there is `expect_alerts.append(event)`
  ([`loop/_functions.py:760`](../../bajutsu/common/orchestrator/loop/_functions.py)); that becomes
  appending directly, not the `expect_alerts.extend(...)` at line 750, which is
  `drain_interruptions`' result and stays unchanged. `if event is None and
  alert_guard.blocked_note` changes the same way, to `if note and note not in reason`.

Neither call site's own retry count changes. A step or an `expect` still gets one retry after the
guard runs, as today. What changes is how much the guard itself clears within that one call, before
the retry it earns.

### Unit 4 — tests

New `FakeDriver`-backed tests join the existing one-shot coverage in
[`tests/orchestrator/test_native_alert_guard.py`](../../tests/orchestrator/test_native_alert_guard.py).

- A stacked-alert case: a native-only prompt sits in front of an in-tree-only one. Both events land in
  the caller's `alerts` list from a single `__call__`. The step's retry then succeeds against the
  now-clear screen.
- A stacked case where the second alert stays unhandled: the native probe dismisses the first alert,
  then reports `"unhandled"` on a second. `cleared` is `True`, but `blocked_note` is still set, and
  the note reaches the eventual failure reason even though the call cleared something.
- A landing-race case: `dismiss_from_tree_once` reports `"not_tappable"` on its first attempt within a
  round, then dismisses on a later round. The alert still clears. The loop stays within its
  `_GUARD_CALL_MAX_ROUNDS` bound.
- A permanently obstructed case: every round's tap keeps reporting `"not_tappable"`. The loop stops at
  `_GUARD_CALL_MAX_ROUNDS`. The step fails, and the failure reason names the alert via
  `uncleared_prompt_note(label)`, rather than reading as a bare missing element.
- A settle-on-exhaustion case: three stacked prompts each dismiss, and the loop exhausts
  `_GUARD_CALL_MAX_ROUNDS` right after the last one. `settle_after_alert_dismiss` still ran before
  `__call__` returned, so the retry that follows reads a settled tree, not one still mid-animation.
- A regression guard for today's single-alert, single-round behavior: a scenario with one in-tree
  alert that dismisses on the first attempt still clears in one round. The loop adds no extra latency
  to that common case.

### Unit 5 — documentation

[`docs/architecture.md`](../../docs/architecture.md) covers `systemAlertHandling` around lines
705–787. It, and its [`docs/ja/`](../../docs/ja/architecture.md) mirror, gain a short account of the
end-of-step and `expect` guard clearing more than one alert per call. That account sits alongside the
mid-wait guard's own account of the same behavior.

### Out of scope

Two cases stay unresolved after this proposal, by design rather than by omission. An alert no
`systemAlertHandling` rule identifies still ends the loop at its `"unhandled"` outcome, unchanged,
with nothing tapped.
[BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)
removed the AI-vision fallback that used to guess an answer for an undeclared alert. Guessing a tap on
a screen a scenario never described was itself the risk, not the fix. Nothing here reopens that
question. Likewise, an in-tree alert whose button never becomes tappable within
`_GUARD_CALL_MAX_ROUNDS` still fails the step, unresolved.

What this proposal changes for both cases is only the diagnosis. The failure names the alert
`blocked_note` recorded, instead of reading as a bare missing element. Actually clearing either case —
answering an alert no rule names, or retrying past a permanently obstructed screen — is a different,
larger proposal. It would need to revisit BE-0402's own reasoning first.

## Alternatives considered

| Option | Outline | Why not adopted |
|---|---|---|
| Narrow `_AlertGuardGate`'s `_tree_signature` comparison to the candidate button set alone | Address a suspected false-positive "the tap landed" verdict in the *mid-wait* retry, by comparing fewer elements between polls | Investigated first, and rejected once checked against `test_dismiss_from_tree_does_not_retry_when_the_tap_moved_the_screen`. That test's own scenario — a sheet closing to reveal an application button carrying the same label — needs the wider comparison so it does not re-tap the revealed application button. Narrowing it would fix a suspected flaw at the cost of that safety property. It also targets the wrong call site: the reported failures trace to the one-shot end-of-step path, not the mid-wait one |
| Debounce the mid-wait signature check (require the difference to persist across two polls before declining a retry) | A smaller, mid-wait-only refinement of the same idea | Same call-site mismatch as above. The reported failures happen after an ordinary action step, where `_AlertGuardGate` never runs at all |
| Give `dismiss_from_tree_once` its own full retry state machine, matching `_alert_guard_gate.py`'s `_TREE_RETAP_DELAY` / `_TREE_DISMISS_MAX_TAPS` / `_tree_signature` | Copy the mid-wait path's state machine into the one-shot path | That state machine exists to compare taps across many polls spread over a `wait` step's whole timeout. A one-shot call carries no such history between invocations, so a short, round-bounded retry inside the single call serves the same purpose without the extra state or its own edge cases |
| Document the workaround: tell scenario authors to place a `wait` step before an action likely to meet a stacked or slow-animating alert | No code change; rely on the author predicting the interruption | [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) records the same workaround for a related gap, and the same objection to it. It asks an author to predict operating-system timing that is not always predictable, and it cannot be written when that timing is genuinely unknown |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — loop `AlertGuardConfig.__call__` until the screen clears or a bound is reached
- [x] Unit 2 — give the in-tree tap the same landing-race retry the mid-wait path already has
- [x] Unit 3 — change `__call__`'s contract to report every alert it clears
- [x] Unit 4 — tests
- [x] Unit 5 — documentation

Log:

- [#1979](https://github.com/bajutsu-e2e/bajutsu/pull/1979) implemented all five units.
  `AlertGuardConfig.__call__` loops up to `_GUARD_CALL_MAX_ROUNDS` (3), appending every dismissed
  `AlertEvent` into a caller-supplied list and returning whether anything cleared;
  `dismiss_from_tree_once` gained a `NotTappable` outcome, an `exclude` parameter, and a `(result,
  buttons, signature)` return for the round-bounded landing-race retry; both call sites
  (`loop/_step_runner.py`, `loop/_functions.py`) moved to the new `(driver, alerts, *, settle) ->
  bool` contract, gating their note-append on the note alone rather than on whether the call
  cleared anything. `docs/architecture.md` and its Japanese mirror describe the new multi-round
  behavior. Live review on the PR surfaced several further correctness gaps in the dedup itself,
  fixed in the same PR: `probe_native` gained a sixth state, `already_dismissed`, declining a
  repeat tap on an already-answered alert before it reaches the device, rather than tapping and
  discarding the duplicate event; the dedup keys on a matched rule's `identifying_labels` instead
  of the raw buttons a probe reads, since that read enumerates the whole surface and moves the
  moment a different alert joins it; `_resolve_alert_rule` retries among the shapes not yet
  answered when the plain match lands on one already answered, so a stacked alert behind the fade
  is still found on both the native and tree paths, and treats a narrower rendering of an
  already-answered shape as answered too, since a policy's own rules for one prompt can nest
  (`savePassword`'s three shapes are one such case). A round that exhausts the bound with the only
  thing still on screen being the alert this call already tapped now reports it via
  `uncleared_prompt_note`, rather than the note going silently empty. The fast suite covers the
  stacked-alert, landing-race, permanently-obstructed, settle-on-exhaustion, nested-shape, and
  lingering-tree-exclusion cases the design and the live review both call for.

  Coverage floors moved on five files across the PR's rounds, accepted via `make
  coverage-floors` as each round landed. Three dropped by 0.01-0.03 points each, every one traced
  via `coverage.json`'s own `missing_lines`/`missing_branches` to a pre-existing, unrelated gap
  becoming a marginally larger share of a slightly smaller file rather than new untested logic:
  `loop/_functions.py` (97.41 → 97.38), `loop/_step_runner.py` (98.29 → 98.28, the original
  multi-round loop), and `waits/_functions.py` (97.9 → 97.88, relocating `_tree_signature` to the
  shared `drivers/elements.py` as the public `tree_signature`). The other two rose over the same
  span rather than dropping: `waits/_alert_guard_gate.py` (97.07 → 97.36) and `types/_functions.py`
  (97.46 → 97.75), a later round's shared-helper extraction (`identified_alert_rules`,
  `subtract_labels`) and its regression tests fully covering branches an earlier round had briefly
  left untested.

## References

- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  — the mid-wait guard's own intervention during a wait. It holds the first record of iOS's
  save-password prompt stalling one.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — the native SpringBoard query `probe_native` reads. This proposal loops that same deterministic
  path, rather than replacing it.
- [BE-0399](../BE-0399-ios-system-alert-interruption-policy/BE-0399-ios-system-alert-interruption-policy.md)
  — where each prompt lives, SpringBoard versus the application's own process. That split is why a
  stacked pair needs two different answer paths within one call.
- [BE-0402](../BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md)
  — `blocked_note`. This proposal keeps it and extends it to a multi-round call, rather than replacing
  it.
- [BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts.md) — added
  the settle-before-retry step this proposal's loop reuses between rounds. It also raised the same
  under-reporting risk this proposal's Unit 3 addresses, for a different call site.
