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
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md), [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) |
<!-- /BE-METADATA -->

> **Reconciled with BE-0316 (shipped during implementation).** BE-0316 landed the native SpringBoard
> primitive — a `handleSystemAlert` *step*, the `handle_system_alert` driver method, the
> `/systemAlert/query` + `/systemAlert/tap` runner routes, and the `HANDLE_SYSTEM_ALERT` capability.
> BE-0316 deliberately kept `dismissAlerts` the reactive vision guard, recording that it would not
> make the deterministic mechanism reactive so that "a passing scenario never calls the model" held.
> BE-0315 is exactly that reactive counterpart, and it resolves that tension: a native SpringBoard
> query is **not** a model call, so the invariant still holds. This implementation therefore **reuses**
> BE-0316's primitive (adding only a thin non-blocking `system_alert_labels()` read over the existing
> `/systemAlert/query`) rather than adding a parallel API; its contribution is the automatic reactive
> guard, the deterministic button policy, the poll-interval knob, and demoting vision to a fallback.

## Introduction

Bajutsu clears an operating-system alert — a SpringBoard prompt such as the "Allow Notifications"
permission dialog or App Tracking Transparency (ATT) that the app-scoped accessibility tree cannot see — only by asking
Claude vision where to tap. This proposal sets a deterministic native path on the iOS XCUITest
backend instead: query the SpringBoard process for its alerts to learn with certainty whether a
prompt is showing and which buttons it offers, then tap a button by its label to dismiss it. The
native path
uses no screenshot and no model round trip, so it clears a prompt in well under a tenth of a second
rather than the several seconds the vision guard spends, and — because it calls no large language
model (LLM) — it runs even without an `ANTHROPIC_API_KEY`, unlike the vision guard, which no-ops
when no credential is present. The contribution is twofold: the runner gains the deterministic
signal for whether a system alert is showing that it has never had, and the common runtime prompts
move off the AI path, leaving vision as the fallback for the cases the native path cannot name.

## Motivation

The system-alert guard is an AI-vision call, and its latency shows. When a SpringBoard prompt
appears mid-wait, [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)'s
gate detects that the app's element tree has collapsed and invokes `SystemAlertGuard.dismiss`
(`bajutsu/agents/alerts.py`), which takes a screenshot, sends it to `claude-sonnet-5`, and taps the
coordinate the model returns. One dismissal is one model round trip; the gate may fire twice, the
end-of-step retry once more, and a cooldown separates the attempts — so a prompt that a native tap
would clear instantly can sit on screen for several seconds. Worse, the guard runs by default but is
credential-gated: with no `ANTHROPIC_API_KEY` configured it silently does nothing, and the prompt
blocks the run until the wait's whole timeout elapses.

The signal the gate acts on is a proxy, not the fact itself. `shows_app_ui(elements)`
(`bajutsu/elements.py`) reports whether the app's own UI tree has actionable content, from which the
gate *infers* that a system overlay is blocking the screen. The inference is only a correlation: a
transient blank frame during navigation reads as blocked when no alert is present, and an overlay
that leaves an identifiable app element on screen reads as clear when a prompt is in fact up. The
runner has never been able to determine whether a system alert is showing on its own.

That gap was unavoidable when the guard was written, and is not anymore. BE-0269 considered asking
the backend for alert presence directly and rejected it: idb's accessibility query was scoped to the
foreground app, so a SpringBoard prompt was invisible to it, and the collapsed-tree proxy was the
best available signal. [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md)
then removed idb and made XCUITest the sole iOS backend. XCUITest can construct a second
`XCUIApplication(bundleIdentifier: "com.apple.springboard")` and query `springboard.alerts` across
the process boundary — the exact capability idb lacked. The premise BE-0269 reasoned from no longer
holds, which is what makes a deterministic presence signal reachable today.

A deterministic dismisser is needed precisely where the deterministic *preventer* cannot reach.
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) pre-sets
permission state through `simctl privacy`, so the location, photos, camera, microphone, contacts,
and calendar prompts never appear — those are backed by the operating system's Transparency,
Consent, and Control (TCC) database, which `simctl privacy` writes. Two common prompts fall outside
TCC and so outside BE-0276's reach: notification authorization is not a TCC service (`simctl privacy`
has no name for it, and `bajutsu/simctl.py` rejects `notifications` for exactly this reason), and
ATT has no `simctl` toggle at all. Both therefore still appear at run time, and today only the
vision guard clears them. These two prompts are the concrete case a native dismisser must handle.

Removing an LLM from the common alert path serves prime directive 1 directly. The guard exists to
keep a run moving, not to judge it, so replacing the model call with a native query and a
label-addressed tap loses no correctness and gains determinism, speed, and independence from a
credential.

## Detailed design

The work divides into a native presence query, a native dismiss action, a deterministic policy for
which button to press, the wiring that prefers the native path over vision, and on-device
verification. Each unit is backend-agnostic at the interface and iOS-specific only in the XCUITest
implementation, so prime directive 3 (per-app and per-platform differences stay behind the driver
interface) holds throughout.

> **As implemented (reconciled with BE-0316).** Units 1 and 2 below were the proposal's plan for a
> presence query and a dismiss action *before* BE-0316 shipped the same SpringBoard plumbing. The
> implementation reuses BE-0316 instead — see the reconciliation banner at the top. What actually
> landed: a thin `Driver.system_alert_labels()` reading BE-0316's `/systemAlert/query` (Unit 1), and
> the guard tapping through BE-0316's `handle_system_alert` under `HANDLE_SYSTEM_ALERT` (Unit 2). No
> new runner route, dismiss action, or capability token was added. Units 3–5 landed as written.

1. **A deterministic system-alert presence query, exposed through the `Driver` interface.** Add a
   backend-agnostic driver method that reports whether a system alert is showing and, when one is,
   the labels of its buttons. *(As implemented: `system_alert_labels()` reads BE-0316's existing
   `/systemAlert/query` — the second `com.apple.springboard` `XCUIApplication` BE-0316 already holds —
   rather than adding a new route; it returns the button labels, `[]` when none is up.)* This signal
   reports a fact and never decides pass/fail, so it stays clear of prime directive 1.

2. **A deterministic dismiss-by-label action.** Dismiss the current system alert by tapping a named
   button, resolved to exactly one match and failing loudly on zero or multiple (the
   `resolve_unique` / `AmbiguousSelector` contract, prime directive 2) rather than tapping whichever
   button matched first. *(As implemented: the guard taps through BE-0316's `handle_system_alert`
   — a label selector resolved by `resolve_unique` and tapped via `/systemAlert/tap` — under the
   `HANDLE_SYSTEM_ALERT` capability, so no parallel dismiss route or capability token was added.)*

3. **Evolve the existing `DismissAlerts.instruction` into a deterministic button policy.** The
   `dismissAlerts` scenario field already lets an author name the button to press:
   `DismissAlerts.instruction` (`bajutsu/scenario/models/scenario.py`) is a free-text string such as
   `"tap Allow"` that the *vision* locator interprets. The work here is therefore not to add button
   selection from scratch but to evolve that existing knob into a deterministic form the native path
   resolves without a model — for example an ordered list of candidate labels to try in turn ("Allow",
   then "OK"), so a scenario granting a permission and one dismissing it stay expressible; when nothing
   is named, a documented default applies. Naming the existing field is what keeps an implementer
   evolving `instruction` rather than adding a second labels field beside it and creating exactly the
   surplus vocabulary this unit warns against. The reconciliation is therefore three-way, not two: the
   existing free-text `instruction`, the deterministic label form this proposes, and the separate
   `interrupts` field the merged interrupt-handlers proposal
   ([BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md)) introduced
   for unpredictably timed interstitials. The implementing session must converge all three on one
   grammar rather than leave three parallel vocabularies or grow a fourth.

4. **Poll the native check on an independent interval, dismiss on the first positive, and keep vision
   only as a fallback.** When the backend advertises the presence and dismiss
   capabilities, BE-0269's `_AlertGuardGate` issues the native presence query on its own wall-clock
   interval — one second by default — decoupled from the wait's `_POLL` condition cadence, and
   dismisses the moment a poll finds a present alert whose button the policy names. The right cadence
   is a heuristic — a trade-off between detection latency and runner load that no single value fits
   every app — so the interval is exposed as a knob rather than hard-coded: it defaults to one second
   and is overridable through the same precedence the `dismissAlerts` setting already follows (flag >
   scenario > target > default, per BE-0177). A per-tick native
   query is deliberately avoided: the XCUITest runner services queries on a single main thread
   (`onMainCatching`), and because SpringBoard is a separate process its query cannot reuse the
   already-fetched app tree the way the free `shows_app_ui` proxy does, so a second cross-process query
   alongside every 50 ms app snapshot would roughly double the runner's load and risk destabilizing or
   crashing it. A bounded interval caps the added load to one SpringBoard query per interval and
   trades a small, bounded detection latency — at most one interval — for runner stability, still
   clearing a prompt far sooner than the vision path it replaces. No debounce is needed —
   `springboard.alerts.firstMatch.exists` reports a fact, not the collapsed-tree proxy's correlation,
   so there is no transient-frame false positive — and no cooldown or max-attempts, since the fixed
   interval already rate-limits the query. The vision guard runs only where the native path cannot act:
   the backend lacks the capability, the blocking surface is not an enumerable `springboard.alerts`
   alert, or the alert carries no button the policy names. The common iOS prompts (notifications, ATT,
   and any alert whose button the policy names) therefore never reach vision, while an unanticipated
   prompt still has a safety net. A backend without the capability keeps today's
   collapsed-tree-proxy-plus-vision behavior unchanged, so the orchestrator stays backend-agnostic and
   no existing path regresses. The vision guard is not on the pass/fail verdict path even today (prime
   directive 1), so keeping it as a fallback costs nothing on the determinism of the outcome.

5. **Verify on device.** A native tap against a real SpringBoard alert cannot be proven by the
   off-Simulator gate, so the unit that lands the XCUITest implementation must exercise a genuine
   notification or ATT prompt on a booted Simulator and confirm both the presence query and the
   label-addressed dismiss. The backend-agnostic wiring (units 3 and 4) is covered by off-device tests
   that stub the driver capability.

## Alternatives considered

- **`addUIInterruptionMonitor`, XCUITest's built-in interruption handler.** Rejected: the monitor
  fires on a nondeterministic schedule — only when the test harness next interacts with the
  application — so it offers no queryable presence signal and cannot be driven on the wait's poll
  cadence. An explicit `springboard.alerts` query is inspectable and deterministic, which is the
  property this proposal needs.
- **Keep the vision-only guard and tune BE-0269's timing further.** Rejected: the latency floor is the
  model round trip itself, which no cooldown or debounce tuning removes, and the vision path still
  no-ops without a credential. The native path removes both the latency and the credential dependence
  rather than trimming them.
- **Widen BE-0276 to pre-set notifications and ATT.** Rejected: notification authorization is not a
  TCC service, so `simctl privacy` cannot write it, and ATT has no `simctl` toggle at all. Neither
  prompt can be prevented up front, so a reactive dismiss for them is unavoidable.
- **Reuse the coordinate tap (`tap_point`) with hard-coded offsets instead of a native element tap.**
  Rejected: a fixed offset breaks across device sizes, button layouts, and dynamic text, and picking
  the coordinate any other way requires the vision call this proposal is removing. The native
  `springboard.alerts.buttons[label]` element resolves the button by its label regardless of
  position, which is the stable primitive.
- **Poll the native check every wait tick rather than on an interval.** Rejected: the XCUITest runner
  services queries on a single main thread, so a per-tick SpringBoard query on top of the per-tick app
  snapshot would roughly double its load and risk destabilizing or crashing the runner, for a latency
  gain of at most one interval. A bounded interval — one second by default — keeps the runner stable
  while still clearing a prompt far faster than the vision path it replaces. A variant that gates the native query
  on the free `shows_app_ui` collapse proxy — querying only when the app tree looks blocked — was also
  considered, but the independent interval was preferred because it does not inherit the proxy's
  false-negative blind spot, in which an overlay that leaves an app element on screen would never
  trigger the query.

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

## References

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — `SystemAlertGuard.dismiss` and
  `ClaudeAlertLocator`, the vision path this proposal demotes to a fallback.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, the
  mid-wait gate that unit 4 rewires to prefer the native path.
- [`bajutsu/elements.py`](../../bajutsu/elements.py) — `shows_app_ui`, the collapsed-tree proxy the
  native presence signal replaces or confirms.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver` interface and capability
  tokens the new query and action extend.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the Python XCUITest driver that
  gains the presence and dismiss methods.
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)
  — the loopback HTTP/JSON command dispatch the new routes join.
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
  and [`RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift) — the provider that
  holds today's single app-scoped `XCUIApplication` and would hold the second SpringBoard one.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) — `apply_permissions`, showing that `notifications`
  is rejected as a non-TCC service (the gap this proposal fills reactively).
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) —
  `DismissAlerts.instruction`, the existing vision-interpreted button string unit 3 evolves into a
  deterministic label.
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) — the
  `dismissAlerts` precedence (flag > scenario > target > default) the poll interval knob rides on.
- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  — the mid-wait guard whose "ask the backend directly — no such signal exists" alternative this
  proposal reopens now that idb is gone.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) — the
  deterministic permission pre-set that prevents the TCC-backed prompts, leaving notifications and ATT
  for this proposal.
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) — the
  removal of idb that makes the cross-process `springboard.alerts` query reachable.
- [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md)
  — real-model verification of the vision guard, which stays relevant for the fallback this proposal
  keeps.
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) — the merged
  interrupt-handlers proposal, whose separate `interrupts` field unit 3 must reconcile its button
  policy with.
