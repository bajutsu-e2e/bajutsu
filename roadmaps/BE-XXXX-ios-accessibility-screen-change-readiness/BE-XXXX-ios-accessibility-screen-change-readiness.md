**English** · [日本語](BE-XXXX-ios-accessibility-screen-change-readiness-ja.md)

# BE-XXXX — Use iOS accessibility screen-change notifications to make the readiness and settle waits more accurate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-accessibility-screen-change-readiness.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) |
<!-- /BE-METADATA -->

## Introduction

This item gives Bajutsu's iOS backend a positive signal for *when a screen transition has finished*,
so the two places that today only *infer* it — the post-launch readiness gate and the `settled`
wait — can act on the transition itself rather than on a guess derived from repeated screen reads.
The signal comes from an accessibility notification iOS already posts when a standard container
transition completes (`UIAccessibility.screenChangedNotification`), observed by an opt-in addition to
the `BajutsuKit` test-support package that every target links identically. Because that notification
is posted by UIKit's container machinery — which backs `UINavigationController`, `UITabBarController`,
and modal presentation directly, and `SwiftUI`'s `NavigationStack` beneath the surface — the signal
covers UIKit and SwiftUI apps through one mechanism at the container-transition granularity, with no
change to the app under test. When a
target does not link `BajutsuKit`, both gates fall back to today's tree-diff polling unchanged, so
the change is a strengthening for apps that opt in, never a new requirement for apps that do not.

## Motivation

Today Bajutsu decides "has the screen settled" by reading the accessibility tree repeatedly and
watching for it to stop changing. The post-launch readiness gate `_await_ready`
(`bajutsu/platform_lifecycle/readiness.py`) polls `query()` until the app foregrounds, hardened by
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
to reject an off-namespace SpringBoard screen; the `settled` wait (`bajutsu/orchestrator/waits.py`,
`_wait_settled`) proceeds once two consecutive `query()` reads match. Both are inferences from the
*absence* of observed change across polls, not from a signal that the transition is actually done.

That inference is fragile on a slow or loaded host, which is exactly where iOS flakiness surfaces.
Two consecutive tree reads can match on a transient intermediate frame — a navigation animation
paused mid-flight, a screen whose final subviews have not yet been laid out — so the poll declares a
still-moving screen settled and the next step acts too early. The opposite failure appears under
[BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md)'s own analysis of the idb
backend: a `describe-all` read costs seconds on a loaded CI Simulator, so a poll budget expressed in
a handful of reads can run out before a genuinely slow transition finishes. The tree-diff has no way
to tell a paused animation from a finished one, because the only evidence it consults is whether two
reads happened to agree.

iOS itself already emits the missing signal. When a standard container transition completes — a
navigation push or pop, a modal presentation or dismissal, a tab switch — UIKit posts
`UIAccessibility.screenChangedNotification` so VoiceOver can reset focus to the new screen. The
notification fires *after* the transition settles, by the accessibility contract's own design, which
is precisely the event the readiness gate and the `settled` wait are trying to reconstruct from tree
diffs. Observing the notification directly replaces an inference with the fact it was approximating.

Bajutsu can observe that notification without asking any app to change its screen code, because the
project already has the mechanism and the policy for exactly this shape of capability.
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) established the
standing design policy that a capability reachable only from inside the app process is realized
through a uniform, opt-in software development kit (SDK) that every target links identically — not
through per-app configuration — so it stays app-agnostic under prime directive 3. `BajutsuKit`
already is that SDK on iOS: its network capture links into the app under test and reports each
observed request out of process to a collector Bajutsu runs (`BajutsuNet`, gated on the
`BAJUTSU_COLLECTOR` launch environment variable). A screen-transition observer is the same shape —
an in-process observation the actuator cannot make from outside, surfaced through the same app-side
collaboration channel — so it reuses an established pattern rather than inventing one.

The signal's reach has a deliberate boundary, and naming it is part of the proposal rather than a
later surprise. `UIAccessibility.screenChangedNotification` fires on a *screen* transition, not on an
ordinary data update within one screen — a counter reflected into a label, a field's edited text.
That within-screen value-reflection race is the subject of
[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md), and this
item does not address it: a screen whose data changes in place posts at most
`UIAccessibility.layoutChangedNotification`, and often nothing automatic at all, so
[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md)'s
element-level condition wait remains the right tool there. This item gives readiness and `settled` a
more accurate *screen-transition* signal to act on; it is complementary to, not a replacement for,
the value-reflection work.

The shared name invites one more clarification. Bajutsu already has a scenario wait condition and a
capture-policy event both named `screenChanged`, and the `until: screenChanged` wait is today
implemented by exactly the tree-diff polling this item improves on (`current != before` across
reads). That existing `until: screenChanged` wait is a natural further consumer of the same signal,
so the reused name is not accidental. This item nonetheless scopes its consumers to the two
default, always-on flakiness sites *Motivation* traces — the post-launch readiness gate and the
`settled` wait — and leaves the author-invoked `until: screenChanged` step on its current tree-diff
polling. Feeding the signal into the `until: screenChanged` wait is a clean follow-on, named here so
the shared word does not read as an unstated conflation, and left out only to keep this item's work
breakdown on the two sites that flake without any author opt-in.

## Detailed design

Proposal altitude. Each unit below is opt-in, gated on the app linking `BajutsuKit`, and preserves
today's behavior exactly for a target that does not — the readiness gate and the `settled` wait keep
their existing tree-diff polling as the fallback, so no scenario regresses and no app is newly
required to link the SDK. No unit puts an LLM anywhere near the `run`/CI verdict, and no unit adds a
fixed `sleep`: every wait remains a condition wait, now with a more accurate condition available.

The signal reaches the deterministic core as a read-only input threaded into `_await_ready` and
`_wait_settled`, read from the transition records the collector accumulates Python-side. The exact seam — a `Driver` capability the backend advertises, or a
collector query passed into the two wait functions — is deferred to implementation, under one
constraint: the readiness and settle core consults the signal only as a read, and never gains a
write-path or verdict-path dependency on the network-capture collector state it already stays
independent of today.

### Work breakdown (MECE)

1. **Observe the accessibility screen-change notification in `BajutsuKit`.** Add an opt-in observer
   that registers for `UIAccessibility.screenChangedNotification` on the main run loop and records a
   monotonic timestamp and a running counter into a small observable store — the same in-app-store
   shape `BajutsuExchangeStore` already uses for network exchanges. The observer subscribes only to
   the notification UIKit's container machinery posts *automatically*, which is the whole basis for
   needing no app-screen change; a notification an app must post by hand (such as
   `UIAccessibility.pageScrolledNotification`, whose page-description argument the app supplies) is
   deliberately excluded, since subscribing to it would observe nothing without the per-app
   cooperation this item rejects. Gate activation on an injected launch environment variable exactly as `BajutsuNet` gates
   on `BAJUTSU_COLLECTOR`, so an app that links `BajutsuKit` but runs outside a Bajutsu run observes
   nothing. This unit touches only `BajutsuKit`, never the app under test's screen code.

2. **Report the signal out of process through the existing collaboration channel.** Surface the
   observed transitions to Bajutsu the same way network capture already surfaces exchanges: report
   them to the collector Bajutsu runs, over the `BAJUTSU_COLLECTOR` channel with its per-run token,
   so the transport, the authentication, and the process boundary are the ones the project already
   operates rather than a new path. The reported record is minimal — a transition kind and a
   monotonic timestamp — carrying no screen content, so it adds no new evidence-privacy surface.

3. **Consult the signal from the readiness gate, with the tree-diff as fallback.** Extend
   `_await_ready` so that, when the app has reported at least one screen-change transition since
   launch, readiness is satisfied by that transition rather than by the element-count and
   in-namespace heuristics
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
   layered in. Keep [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)'s
   precedence intact as the fallback ladder for a target that reports no signal: an explicit
   `readyWhen` selector, then an in-namespace element, then the 2-or-more-element count. The signal
   becomes a new strongest rung on that ladder, available only when the SDK is linked. One case this
   rung depends on cannot be settled by code review: whether the cold-launch appearance of the app's
   *first* screen posts `UIAccessibility.screenChangedNotification` at all — the readiness event is
   the first screen coming up, which is not a *change* from a prior screen the way a later push or tab
   switch is. Unit 5 measures it. If the launch screen posts nothing, readiness simply stays on
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)'s
   ladder with no regression, and the signal's guaranteed gain lands on the `settled` wait (unit 4),
   which observes transitions *between* screens mid-scenario where a screen-change is unambiguous.

4. **Consult the signal from the `settled` wait, with the tree-diff as fallback.** Extend
   `_wait_settled` so that, when the signal is available, the screen counts as settled once no
   further screen-change transition has been reported for a short quiescence window bounded by the
   step's wall-clock deadline — a positive "the last transition has finished and no new one started"
   rather than "two reads happened to match." When no signal is available, `_wait_settled` keeps its
   two-consecutive-unchanged-reads behavior unchanged, including its existing refusal to treat an
   empty or system-alert-covered tree as settled. The wait stays best-effort on timeout, exactly as
   documented today.

5. **Verify on the Simulator that the signal fires as assumed, across UIKit and SwiftUI.** The
   proposal rests on one empirical assumption that code review cannot settle: that `SwiftUI`'s
   `NavigationStack` transitions post `UIAccessibility.screenChangedNotification` at the same
   granularity UIKit's container transitions do, because `NavigationStack` is `UINavigationController`
   -backed beneath the surface. Confirm it on a named environment against both showcase iOS targets —
   the UIKit app (`demos/showcase/ios/uikit`) and the SwiftUI app (`demos/showcase/ios/swiftui`) —
   measuring, for the cold-launch-to-first-screen appearance (the case unit 3's readiness rung
   depends on) and for a representative navigation push, modal presentation, and tab switch on each,
   that the observer records a transition and that readiness and `settled` fire on it. Record the outcome
   for the cases the signal does *not* cover — a custom transition that bypasses the standard
   containers, and a within-screen data update — so the fallback's role is documented from evidence,
   not assumed. This on-device confirmation is the item's gate, in the same spirit as the
   Simulator-parity gate
   [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) proposes
   and the on-device gates the Platform-support items already use.

## Alternatives considered

- **Swizzle `UIViewController.viewDidAppear` instead of observing the accessibility notification.**
  Method swizzling on `UIViewController` would hook UIKit's per-controller lifecycle with no app code
  change, but it does not reach SwiftUI on equal terms: a SwiftUI view is a value type with no shared
  view-controller base class to swizzle, so the hook would fire only at the `UIHostingController`
  boundary and miss transitions within one hosting controller. The requirement that the signal cover
  UIKit and SwiftUI equally rules this out; the accessibility notification is posted by the shared
  container machinery both frameworks sit on, so it covers both through one observer.

- **Add a per-screen `.onAppear` (SwiftUI) / lifecycle call (UIKit) that reports to the SDK.**
  Asking each screen to announce its own appearance would cover SwiftUI's within-hosting-controller
  transitions that the accessibility notification may miss, but it requires editing every screen of
  every app under test. That is precisely the per-app cooperation prime directive 3 and the
  [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) policy keep out of
  the app's own code: a uniform SDK the app links once is app-agnostic, whereas a modifier the author
  must sprinkle through the app's views is a per-app difference. The narrower automatic signal that
  needs no app-screen change is preferred, with the coverage boundary documented (unit 5) rather than
  closed by pushing work into every app.

- **Do nothing and rely on the tree-diff polling as it stands.** Acceptable, and it stays the
  behavior for any target that does not link `BajutsuKit`. It is not enough on its own for apps that
  can opt in, because the tree-diff cannot distinguish a paused mid-transition frame from a finished
  screen and can exhaust its poll budget on a slow host — the two failure modes *Motivation* traces —
  so an app willing to link the SDK gains a strictly more accurate signal at no cost to one that is
  not.

- **Widen the signal to cover within-screen data updates as well.** Rejected as out of scope and
  as the wrong mechanism: an in-place data update posts at most
  `UIAccessibility.layoutChangedNotification` and frequently nothing automatic, so a screen-transition
  observer cannot reliably see it.
  [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) already
  addresses that race at the element level, which is where it belongs; this item stays deliberately
  scoped to the screen-transition signal it can observe reliably.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Observe the `UIAccessibility` screen-change notification in `BajutsuKit`, opt-in gated.
- [ ] Unit 2 — Report the signal out of process through the existing collector channel.
- [ ] Unit 3 — Consult the signal from `_await_ready`, with the BE-0218 tree-diff ladder as fallback.
- [ ] Unit 4 — Consult the signal from `_wait_settled`, with the tree-diff behavior as fallback.
- [ ] Unit 5 — Confirm on the Simulator that the signal fires across UIKit and SwiftUI (the gate).

## References

- [`bajutsu/platform_lifecycle/readiness.py`](../../bajutsu/platform_lifecycle/readiness.py) —
  `_await_ready`, the post-launch readiness gate this item gives a positive signal to.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_wait_settled`, the
  `settled` wait this item gives a positive signal to.
- [`BajutsuKit`](../../BajutsuKit) — the opt-in iOS SDK the observer joins; `BajutsuNet` and
  `BajutsuExchangeStore` are the network-capture precedents this item mirrors.
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
  — the readiness ladder this item adds a strongest rung to.
- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md) — the settle-before-actuation
  analysis that quantifies the tree-read cost this item's signal avoids on the settle path.
- [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) — the app-side-SDK
  cooperation policy this item follows to stay app-agnostic.
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) — the
  complementary within-screen value-reflection race this item deliberately does not cover.
