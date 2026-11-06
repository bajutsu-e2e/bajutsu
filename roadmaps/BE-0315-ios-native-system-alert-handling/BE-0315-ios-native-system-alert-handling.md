**English** · [日本語](BE-0315-ios-native-system-alert-handling-ja.md)

# BE-0315 — Make the reactive alert guard deterministic and native, reusing BE-0316's SpringBoard path

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0315](BE-0315-ios-native-system-alert-handling.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0315") |
| Implementing PR | [#1330](https://github.com/bajutsu-e2e/bajutsu/pull/1330) |
| Topic | Platform support |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md), [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch.md) |
<!-- /BE-METADATA -->

> **Reconciled with BE-0316 (shipped during implementation).** BE-0316 landed the native SpringBoard
> primitive: a `handleSystemAlert` *step*, the `handle_system_alert` driver method, the
> `/systemAlert/query` and `/systemAlert/tap` runner routes, and the `HANDLE_SYSTEM_ALERT` capability.
> BE-0316 deliberately kept `dismissAlerts` as the reactive vision guard. It recorded that making the
> deterministic mechanism reactive would break the rule that "a passing scenario never calls the model".
> BE-0315 is the reactive counterpart, and it resolves that tension: a native SpringBoard query is
> **not** a model call, so the rule still holds. This implementation **reuses** BE-0316's primitive
> and adds merely a thin non-blocking `system_alert_labels()` read over the existing
> `/systemAlert/query`, instead of a parallel API. BE-0315 contributes the automatic reactive guard,
> the deterministic button policy, the poll-interval knob, and the demotion of vision to a fallback.

## Introduction

Bajutsu clears an operating-system alert by asking Claude vision where to tap. Such an alert is a
SpringBoard prompt that the app-scoped accessibility tree cannot see. Examples are the "Allow
Notifications" permission dialog and App Tracking Transparency (ATT).

This proposal sets a deterministic native path on the iOS XCUITest backend instead. The path queries
the SpringBoard process for its alerts. The query tells the runner with certainty whether a prompt is
showing and which buttons the prompt offers. The path then taps a button by its label.

The native path uses no screenshot and no model round trip. It clears a prompt in well under a tenth
of a second, where the vision guard spends several seconds. The path calls no large language model
(LLM), so it runs even without an `ANTHROPIC_API_KEY`. The vision guard, by contrast, does nothing
when no credential is present.

The contribution is twofold. The runner gains a deterministic signal for whether a system alert is
showing, which it has never had. The common runtime prompts also move off the AI path, and vision
remains as the fallback for the cases the native path cannot name.

## Motivation

The system-alert guard is an AI-vision call, and its latency shows. Suppose a SpringBoard prompt
appears mid-wait. The gate from
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
detects that the app's element tree has collapsed and invokes `SystemAlertGuard.dismiss`
(`bajutsu/agents/alerts.py`). That method takes a screenshot, sends the screenshot to
`claude-sonnet-5`, and taps the coordinate the model returns.

One dismissal is one model round trip. The gate may fire twice, the end-of-step retry fires once
more, and a cooldown separates the attempts. A prompt that a native tap would clear instantly can
therefore sit on screen for several seconds.

The guard also depends on a credential. The guard runs by default, but with no `ANTHROPIC_API_KEY`
configured it silently does nothing. The prompt then blocks the run until the wait's whole timeout
elapses.

The signal the gate acts on is a proxy, not the fact itself. The function `shows_app_ui(elements)`
(`bajutsu/elements.py`) reports whether the app's own UI tree has actionable content. From that
report, the gate *infers* that a system overlay blocks the screen.

The inference is a correlation and can fail in both directions. A transient blank frame during
navigation reads as blocked when no alert is present. An overlay that leaves an identifiable app
element on screen reads as clear when a prompt is in fact up. The runner has never been able to
determine on its own whether a system alert is showing.

That gap was unavoidable when the guard was written. BE-0269 considered asking the backend for alert
presence directly and rejected the idea. The accessibility query of idb was scoped to the foreground
app, so idb could not see a SpringBoard prompt. The collapsed-tree proxy was the best available
signal.

[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) then
removed idb and made XCUITest the sole iOS backend. XCUITest can construct a second
`XCUIApplication(bundleIdentifier: "com.apple.springboard")` and query `springboard.alerts` across
the process boundary, which is the capability idb lacked. The premise BE-0269 reasoned from no longer
holds, so a deterministic presence signal is reachable today.

A deterministic dismisser is needed where the deterministic *preventer* cannot reach.
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) pre-sets
permission state through `simctl privacy`. The location, photos, camera, microphone, contacts, and
calendar prompts therefore never appear. The operating system's Transparency, Consent, and Control
(TCC) database backs those prompts, and `simctl privacy` writes to that database.

Two common prompts fall outside TCC, and so outside BE-0276's reach:

- Notification authorization is not a TCC service. `simctl privacy` has no name for it, and
  `bajutsu/simctl.py` rejects `notifications` for exactly this reason.
- ATT has no `simctl` toggle at all.

Both prompts still appear at run time, and only the vision guard clears them today. These two
prompts are the concrete case that a native dismisser must handle.

Removing an LLM from the common alert path serves prime directive 1 directly. The guard exists to
keep a run moving, not to judge the run. Replacing the model call with a native query and a
label-addressed tap loses no correctness. The replacement gains determinism, speed, and independence
from a credential.

## Detailed design

The work divides into a native presence query, a native dismiss action, a deterministic
policy for which button to press, the wiring that prefers the native path over vision, and on-device
verification. Each part is backend-agnostic at the interface and iOS-specific purely in the XCUITest
implementation. Prime directive 3 (per-app and per-platform differences stay behind the driver
interface) therefore holds throughout.

> **As implemented (reconciled with BE-0316).** Units 1 and 2 below were the proposal's plan for a
> presence query and a dismiss action *before* BE-0316 shipped the same SpringBoard plumbing. The
> implementation reuses BE-0316 instead (see the reconciliation banner at the top). What actually
> landed: a thin `Driver.system_alert_labels()` that reads BE-0316's `/systemAlert/query` (Unit 1),
> and the guard tapping through BE-0316's `handle_system_alert` under `HANDLE_SYSTEM_ALERT` (Unit 2).
> The implementation added no new runner route, dismiss action, or capability token. Units 3–5
> landed as written.

1. **A deterministic system-alert presence query, exposed through the `Driver` interface.** Add a
   backend-agnostic driver method. The method reports whether a system alert is showing and, when
   one is, the labels of its buttons. *(As implemented: `system_alert_labels()` reads BE-0316's
   existing `/systemAlert/query` instead of adding a new route. That route uses the second
   `com.apple.springboard` `XCUIApplication`, which BE-0316 already holds. The method returns the
   button labels, or `[]` when no alert is up.)* The signal reports a fact and never decides
   pass/fail, so the signal stays clear of prime directive 1.

2. **A deterministic dismiss-by-label action.** Dismiss the current system alert by tapping a named
   button. The action resolves the label to exactly one match. The action fails loudly on zero or
   multiple matches, as the `resolve_unique` / `AmbiguousSelector` contract requires (prime
   directive 2), instead of tapping whichever button matched first. *(As implemented: the guard taps
   through BE-0316's `handle_system_alert`, a label selector resolved by `resolve_unique` and tapped
   via `/systemAlert/tap`, under the `HANDLE_SYSTEM_ALERT` capability. The implementation added no
   parallel dismiss route or capability token.)*

3. **Evolve the existing `DismissAlerts.instruction` into a deterministic button policy.** The
   `dismissAlerts` scenario field already lets an author name the button to press.
   `DismissAlerts.instruction` (`bajutsu/scenario/models/scenario.py`) is a free-text string such as
   `"tap Allow"`, which the *vision* locator interprets.

   The work here is not to add button selection from scratch. The work evolves the existing knob into
   a deterministic form that the native path resolves without a model. One example is an ordered list
   of candidate labels to try in turn ("Allow", then "OK"), so a scenario that grants a permission and
   a scenario that dismisses the prompt both stay expressible. When no label is named, a documented
   default applies.

   Naming the existing field keeps an implementer evolving `instruction`. The alternative is a second
   labels field beside `instruction`, which creates the surplus vocabulary this unit warns against.

   The reconciliation involves three parties, not two:

   - the existing free-text `instruction`
   - the deterministic label form this proposal describes
   - the separate `interrupts` field that the merged interrupt-handlers proposal
     ([BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md))
     introduced for unpredictably timed interstitials

   The implementing session must converge all three on one grammar. The session must not leave three
   parallel vocabularies or grow a fourth.

4. **Poll the native check on an independent interval, dismiss on the first positive, and keep vision
   only as a fallback.** This unit has four parts.

   - **Interval.** The backend advertises the presence and dismiss capabilities. BE-0269's
     `_AlertGuardGate` then issues the native presence query on its own wall-clock interval, one
     second by default. The interval is decoupled from the wait's `_POLL` condition cadence. The gate
     dismisses the moment a poll finds a present alert whose button the policy names.
   - **Knob.** The right cadence is a heuristic: a trade-off between detection latency and runner
     load, and no single value fits every app. The interval is therefore a knob, not a hard-coded
     value. The interval defaults to one second and follows the precedence that the `dismissAlerts`
     setting already follows (flag > scenario > target > default, per BE-0177).
   - **No per-tick query.** A per-tick native query is deliberately avoided. The XCUITest runner
     services queries on a single main thread (`onMainCatching`). SpringBoard is a separate process,
     so the SpringBoard query cannot reuse the already-fetched app tree the way the free
     `shows_app_ui` proxy does. A second cross-process query alongside every 50 ms app snapshot would
     roughly double the runner's load and risk destabilizing or crashing the runner. A bounded
     interval caps the added load at one SpringBoard query per interval. The bound trades a small
     detection latency, at most one interval, for runner stability. The interval still clears a
     prompt far sooner than the vision path it replaces.
   - **No debounce, cooldown, or max-attempts.** No debounce is needed.
     `springboard.alerts.firstMatch.exists` reports a fact, not the collapsed-tree proxy's
     correlation, so no transient frame gives a false positive. No cooldown or max-attempts is needed
     either, since the fixed interval already rate-limits the query.

   The vision guard runs where the native path cannot act, in three cases:

   - the backend lacks the capability
   - the blocking surface is not an enumerable `springboard.alerts` alert
   - the alert carries no button the policy names

   The common iOS prompts (notifications, ATT, and any alert whose button the policy names) never
   reach vision, while an unanticipated prompt still has a safety net. A backend without the
   capability keeps today's collapsed-tree-proxy-plus-vision behavior unchanged. The orchestrator
   therefore stays backend-agnostic, and no existing path regresses. The vision guard is not on the
   pass/fail verdict path even today (prime directive 1), so keeping the guard as a fallback costs
   nothing in the determinism of the outcome.

5. **Verify on device.** The off-Simulator gate cannot prove a native tap against a real SpringBoard
   alert. The unit that lands the XCUITest implementation must therefore exercise a genuine
   notification or ATT prompt on a booted Simulator. The exercise confirms both the presence query and
   the label-addressed dismiss. Off-device tests that stub the driver capability cover the
   backend-agnostic wiring (units 3 and 4).

6. **Realize the native reactive grant in the showcase and CI.** Convert the showcase's notification
   scenario to exercise the native path. The scenario `demos/showcase/scenarios/permission.yaml`
   grants with the list form `dismissAlerts: { instruction: ["Allow"] }`. On the now-default XCUITest
   backend (BE-0290), the guard then taps "Allow" by an exact-label native query instead of through
   the vision fallback.

   The grant is now deterministic and credential-free, so the scenario sheds its `ai` tag and joins
   the required XCUITest gate (`ios-e2e.yml` drops `--exclude ai`). The scenario is the reactive
   native counterpart to BE-0316's proactive `permission_system_alert.yaml` on the same gate. This
   unit makes the native grant a demonstrated, gate-enforced flow instead of a capability that
   off-device tests alone cover.

## Alternatives considered

- **`addUIInterruptionMonitor`, XCUITest's built-in interruption handler.** Rejected. The monitor
  fires on a nondeterministic schedule, when the test harness next interacts with the application.
  The monitor offers no queryable presence signal and cannot be driven on the wait's poll cadence. An
  explicit `springboard.alerts` query is inspectable and deterministic, which is the property this
  proposal needs.
- **Keep the vision-only guard and tune BE-0269's timing further.** Rejected. The latency floor is
  the model round trip itself, and no cooldown or debounce tuning removes that floor. The vision path
  also does nothing without a credential. The native path removes both the latency and the credential
  dependence instead of trimming them.
- **Widen BE-0276 to pre-set notifications and ATT.** Rejected. Notification authorization is not a
  TCC service, so `simctl privacy` cannot write it. ATT has no `simctl` toggle at all. Neither prompt
  can be prevented up front, so a reactive dismiss for them is unavoidable.
- **Reuse the coordinate tap (`tap_point`) with hard-coded offsets instead of a native element tap.**
  Rejected. A fixed offset breaks across device sizes, button layouts, and dynamic text. Picking the
  coordinate any other way requires the vision call that this proposal removes. The native
  `springboard.alerts.buttons[label]` element resolves the button by its label regardless of
  position, which makes the element the stable primitive.
- **Poll the native check every wait tick rather than on an interval.** Rejected. The XCUITest runner
  services queries on a single main thread. A per-tick SpringBoard query on top of the per-tick app
  snapshot would roughly double the runner's load and risk destabilizing or crashing the runner. The
  latency gain would be at most one interval. A bounded interval, one second by default, keeps the
  runner stable while still clearing a prompt far faster than the vision path it replaces.

  A variant gates the native query on the free `shows_app_ui` collapse proxy, so the query runs when
  the app tree looks blocked. The variant was also considered. The independent interval was preferred
  because the interval does not inherit the proxy's false-negative blind spot. An overlay that leaves
  an app element on screen would never trigger the gated query.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — deterministic system-alert presence query. **Reused from BE-0316**: rather than a new
      route, a thin non-blocking `Driver.system_alert_labels()` reads BE-0316's existing
      `/systemAlert/query` (the second `com.apple.springboard` `XCUIApplication` BE-0316 already holds)
      and returns the alert's button labels, `[]` when none is up.
- [x] Unit 2 — deterministic dismiss-by-label action. **Reused from BE-0316**: the guard taps through
      BE-0316's `handle_system_alert` (label selector + `/systemAlert/tap`, resolved by `resolve_unique`)
      under the `HANDLE_SYSTEM_ALERT` capability, so no parallel dismiss route or capability was added.
- [x] Unit 3 — evolve `DismissAlerts.instruction` from a vision-interpreted string into a `str |
      list[str]`: a candidate-label list is the deterministic native form, the free-text string stays
      the vision form. Reconciled with the existing `instruction`, BE-0314's `interrupts`, and BE-0316's
      `handleSystemAlert` selector rather than growing another vocabulary.
- [x] Unit 4 — poll the native check on an independent interval in `_AlertGuardGate` (default one
      second, overridable through the `dismissAlerts` precedence per BE-0177) and dismiss on the first
      positive (decoupled from `_POLL`; no per-tick query, no debounce/cooldown/max-attempts), demoting
      the vision guard to a fallback and keeping backends without the capability unchanged.
- [x] Unit 5 — on-device verification against a real notification prompt (native grant, credential-free);
      off-device tests for the guard policy, gate native path, the `system_alert_labels` channel, and
      the config wiring.
- [x] Unit 6 — realize the native reactive grant in the showcase and CI. `demos/showcase/scenarios/permission.yaml`'s
      notification scenario now grants with the list form `dismissAlerts: { instruction: ["Allow"] }`,
      so on the now-default XCUITest backend (BE-0290) the native path taps "Allow" deterministically —
      a free-text string would populate no native label, fall through to the default dismissive labels,
      and tap "Don't Allow", inverting the grant. With the grant native and key-free, the scenario drops
      its `ai` tag and joins the required XCUITest gate (`ios-e2e.yml`, no more `--exclude ai`): the
      reactive counterpart to BE-0316's proactive `permission_system_alert.yaml` on the same gate.

## References

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py): `SystemAlertGuard.dismiss` and
  `ClaudeAlertLocator`, the vision path that this proposal demotes to a fallback.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py): `_AlertGuardGate`, the
  mid-wait gate that unit 4 rewires to prefer the native path.
- [`bajutsu/elements.py`](../../bajutsu/elements.py): `shows_app_ui`, the collapsed-tree proxy that
  the native presence signal replaces or confirms.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py): the `Driver` interface and capability
  tokens that the new query and action extend.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): the Python XCUITest driver that
  gains the presence and dismiss methods.
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift):
  the loopback HTTP/JSON command dispatch that the new routes join.
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
  and [`RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift): the provider that
  holds today's single app-scoped `XCUIApplication` and would hold the second SpringBoard one.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py): `apply_permissions`, which rejects `notifications`
  as a non-TCC service (the gap this proposal fills reactively).
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py):
  `DismissAlerts.instruction`, the existing vision-interpreted button string that unit 3 evolves into
  a deterministic label.
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md): the
  `dismissAlerts` precedence (flag > scenario > target > default) that the poll interval knob follows.
- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md):
  the mid-wait guard whose "ask the backend directly, no such signal exists" alternative this
  proposal reopens now that idb is gone.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md): the
  deterministic permission pre-set that prevents the TCC-backed prompts, leaving notifications and ATT
  for this proposal.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md): the
  removal of idb that makes the cross-process `springboard.alerts` query reachable.
- [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md):
  real-model verification of the vision guard, which stays relevant for the fallback this proposal
  keeps.
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md): the
  merged interrupt-handlers proposal, whose separate `interrupts` field unit 3 must reconcile its
  button policy with.
