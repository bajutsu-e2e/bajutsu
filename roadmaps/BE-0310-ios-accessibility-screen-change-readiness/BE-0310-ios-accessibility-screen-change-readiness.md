**English** · [日本語](BE-0310-ios-accessibility-screen-change-readiness-ja.md)

# BE-0310 — Use iOS accessibility screen-change notifications to make the readiness and settle waits more accurate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0310](BE-0310-ios-accessibility-screen-change-readiness.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0310") |
| Implementing PR | [#1309](https://github.com/bajutsu-e2e/bajutsu/pull/1309) |
| Topic | Platform support |
| Related | [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) |
<!-- /BE-METADATA -->

## Introduction

This item gives Bajutsu's iOS backend a positive signal for *when a screen transition has finished*.
Two places infer that moment today: the post-launch readiness gate and the `settled` wait. Both can
act on the transition itself instead of a guess derived from repeated screen reads.

The signal comes from an opt-in addition to the `BajutsuKit` test-support package, which every
target links identically. The addition hooks UIKit's `UIViewController.viewDidAppear`, which fires
*after* a completed appearance transition.

The proposal first aimed to observe the accessibility notification that iOS posts on such a
transition, `UIAccessibility.screenChangedNotification`. That symbol proved to be outbound-only. It
is a `UIAccessibility.post(...)` enum case, not an observable `NSNotification.Name`. The appearance
hook, weighed in *Alternatives considered* below, is what ships.

`UINavigationController`, `UITabBarController`, and modal presentation each drive a view-controller
appearance directly. SwiftUI's `NavigationStack`, `.sheet`/`.fullScreenCover`, and tab switches each
present a `UIHostingController` beneath the surface. One mechanism therefore covers UIKit and
SwiftUI apps at the container-transition granularity, with no change to the app under test.

A target that does not link `BajutsuKit` keeps today's tree-diff polling in both gates, unchanged.
The change strengthens apps that opt in and never becomes a new requirement for apps that do not.

## Motivation

Today Bajutsu decides "has the screen settled" by reading the accessibility tree repeatedly and
watching for it to stop changing. Two places do this:

- The post-launch readiness gate `_await_ready` (`bajutsu/platform_lifecycle/readiness.py`) polls
  `query()` until the app foregrounds. [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
  hardened the gate to reject an off-namespace SpringBoard screen.
- The `settled` wait (`_wait_settled` in `bajutsu/orchestrator/waits.py`) proceeds once two
  consecutive `query()` reads match.

Both places infer the result from the *absence* of observed change across polls. Neither receives a
signal that the transition is done.

That inference is fragile on a slow or loaded host, and iOS flakiness surfaces on such hosts. Two
consecutive tree reads can match on a transient intermediate frame. Examples are a navigation
animation paused mid-flight and a screen whose final subviews have not been laid out. The poll then
declares a still-moving screen settled, and the next step acts too early.

The opposite problem appears in the idb backend analysis of
[BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md). A `describe-all` read costs
seconds on a loaded CI Simulator. A poll budget of a handful of reads can run out before a genuinely
slow transition finishes.

The tree diff cannot tell a paused animation from a finished one. Its only evidence is whether two
reads happened to agree.

iOS already emits the missing signal. When a standard container transition completes, UIKit posts
`UIAccessibility.screenChangedNotification` so that VoiceOver can reset focus to the new screen.
Container transitions include a navigation push or pop, a modal presentation or dismissal, and a tab
switch. By the accessibility contract's own design, the notification fires *after* the transition
settles. That is the event that the readiness gate and the `settled` wait try to reconstruct from
tree diffs. Capturing the completion directly replaces an inference with the fact it approximated.

The notification named the event, but the shipping observation reads the same completion through
`UIViewController.viewDidAppear`. The notification proved not to be an observable
`NSNotification.Name`. See *Detailed design* unit 1 and *Alternatives considered*.

Bajutsu can capture the completion without asking any app to change its screen code. The project
already has the mechanism and the policy for this shape of capability.
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) established a
standing design policy. A capability reachable from inside the app process is realized through a
uniform, opt-in software development kit (SDK) that every target links identically. Per-app
configuration is not used. The policy keeps the capability app-agnostic under prime directive 3.

`BajutsuKit` already is that SDK on iOS. Its network capture links into the app under test. The
capture reports each observed request out of process to a collector that Bajutsu runs
(`BajutsuNet`, gated on the `BAJUTSU_COLLECTOR` launch environment variable).

A screen-transition observer has the same shape. The observer makes an in-process observation that
the actuator cannot make from outside. The observer surfaces the observation through the same
app-side collaboration channel. The item reuses an established pattern and invents none.

The signal's reach has a deliberate boundary. Naming the boundary is part of the proposal.
`UIAccessibility.screenChangedNotification` fires on a *screen* transition. It does not fire on an
ordinary data update within one screen, such as a counter reflected into a label or a field's edited
text.

[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) addresses
that within-screen value-reflection race, and this item does not. A screen whose data changes in
place posts at most `UIAccessibility.layoutChangedNotification`, and often nothing automatic at all.
The element-level condition wait of
[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) remains the
right tool there.

This item gives readiness and `settled` a more accurate *screen-transition* signal. The item
complements the value-reflection work and does not replace it.

The shared name needs one more clarification. Bajutsu already has a scenario wait condition and a
capture-policy event, both named `screenChanged`. Today the `until: screenChanged` wait runs on the
same tree-diff polling that this item improves (`current != before` across reads). The reused name
is not accidental, because that wait is a natural further consumer of the same signal.

This item scopes its consumers to the two default, always-on flakiness sites that this section
traces: the post-launch readiness gate and the `settled` wait. The author-invoked
`until: screenChanged` step stays on its current tree-diff polling. Feeding the signal into that
wait is a clean follow-on. This paragraph names the follow-on so that the shared word does not read
as an unstated conflation. The item leaves the follow-on out to keep its work breakdown on the two
sites that flake without any author opt-in.

## Detailed design

This section stays at proposal altitude. Each unit below is opt-in and gated on the app linking
`BajutsuKit`. Each unit preserves today's behavior for a target that does not link it. The readiness
gate and the `settled` wait keep their tree-diff polling as the fallback. No scenario regresses, and
no app is newly required to link the SDK.

No unit puts an LLM near the `run`/CI verdict, and no unit adds a fixed `sleep`. Every wait remains a
condition wait, now with a more accurate condition available.

The signal reaches the deterministic core as a read-only input threaded into `_await_ready` and
`_wait_settled`. The input is read from the transition records that the collector accumulates on the
Python side.

The exact seam is deferred to implementation. The seam is either a `Driver` capability that the
backend advertises or a collector query passed into the two wait functions. One constraint holds
either way. The readiness and settle core consults the signal as a read and nothing more. The core
never gains a write-path or verdict-path dependency on the network-capture collector state, from
which it stays independent today.

### Work breakdown (MECE)

1. **Observe completed view-controller appearances in `BajutsuKit`.** Add an opt-in hook that
   swizzles `UIViewController.viewDidAppear(_:)`. The hook reuses the
   `method_exchangeImplementations` idiom that `BajutsuURLProtocol` already uses. The hook records a
   monotonic timestamp and a running counter into a small observable store. The store has the same
   in-app shape as `BajutsuExchangeStore`, which holds network exchanges.

   The hook fires on the appearances that UIKit's container machinery drives *automatically*: a
   navigation push or pop, a modal presentation, and a tab switch. That behavior is the basis for
   needing no app-screen change.

   The proposal first named `UIAccessibility.screenChangedNotification` as the source. That symbol is
   outbound-only: an enum case for `UIAccessibility.post(...)`, not an observable
   `NSNotification.Name`. The appearance hook weighed in *Alternatives considered* implements this
   unit instead, at the same container-transition granularity.

   Gate activation on an injected launch environment variable, as `BajutsuNet` gates on
   `BAJUTSU_COLLECTOR`. An app that links `BajutsuKit` but runs outside a Bajutsu run then observes
   nothing. This unit touches `BajutsuKit` and never the screen code of the app under test.

2. **Report the signal out of process through the existing collaboration channel.** Surface the
   observed transitions to Bajutsu the way network capture already surfaces exchanges. Report them
   to the collector that Bajutsu runs, over the `BAJUTSU_COLLECTOR` channel with its per-run token.
   The transport, the authentication, and the process boundary are then the ones that the project
   already operates. No new path appears.

   The reported record is minimal: a transition kind and a monotonic timestamp. The record carries no
   screen content, so it adds no new evidence-privacy surface.

3. **Consult the signal from the readiness gate, with the tree diff as fallback.** Extend
   `_await_ready`. When the app has reported at least one screen-change transition since launch,
   that transition satisfies readiness. The element-count and in-namespace heuristics that
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
   layered in no longer decide readiness in that case.

   Keep the precedence of
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
   intact as the fallback ladder for a target that reports no signal. The ladder runs from an explicit
   `readyWhen` selector, to an in-namespace element, to the count of two or more elements. The signal
   becomes a new strongest rung on that ladder. The rung is available when the SDK is linked.

   Code review cannot settle one case that this rung depends on. Does the cold-launch appearance of
   the app's *first* screen post `UIAccessibility.screenChangedNotification` at all? The readiness
   event is the first screen coming up. That event is not a *change* from a prior screen, the way a
   later push or tab switch is. Unit 5 measures the case.

   If the launch screen posts nothing, readiness stays on the ladder of
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
   with no regression. The guaranteed gain of the signal then lands on the `settled` wait (unit 4).
   That wait observes transitions *between* screens mid-scenario, where a screen change is
   unambiguous.

4. **Consult the signal from the `settled` wait, with the tree diff as fallback.** Extend
   `_wait_settled`. When the signal is available, the screen counts as settled once no further
   screen-change transition has been reported for a short quiescence window. The step's wall-clock
   deadline bounds the window. The wait then rests on a positive fact, "the last transition has
   finished and no new one started", and not on "two reads happened to match".

   When no signal is available, `_wait_settled` keeps its behavior of two consecutive unchanged
   reads. The existing refusal to treat an empty or system-alert-covered tree as settled also stays.
   The wait stays best-effort on timeout, as documented today.

5. **Verify on the Simulator that the signal fires as assumed, across UIKit and SwiftUI.** The
   proposal rests on one empirical assumption that code review cannot settle. The assumption is that
   SwiftUI's `NavigationStack` transitions post `UIAccessibility.screenChangedNotification` at the
   same granularity as UIKit's container transitions, because `NavigationStack` is backed by
   `UINavigationController` beneath the surface.

   Confirm the assumption on a named environment against both showcase iOS targets: the UIKit app
   (`demos/showcase/ios/uikit`) and the SwiftUI app (`demos/showcase/ios/swiftui`). Measure two
   things on each app. The first is the cold-launch-to-first-screen appearance, which the readiness
   rung of unit 3 depends on. The second is a representative navigation push, modal presentation, and
   tab switch. In every case, check that the observer records a transition and that readiness and
   `settled` fire on it.

   Record the outcome for the cases that the signal does *not* cover: a custom transition that
   bypasses the standard containers, and a within-screen data update. The role of the fallback is
   then documented from evidence and not assumed.

   This on-device confirmation is the gate of the item. It follows the spirit of the Simulator-parity
   gate that [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md)
   proposes and of the on-device gates that the Platform-support items already use.

## Alternatives considered

- **Swizzle `UIViewController.viewDidAppear` instead of observing the accessibility notification.**
  *Adopted. This is what shipped.* The original plan was to observe
  `UIAccessibility.screenChangedNotification`. That symbol proved to be outbound-only. iOS vends
  `.screenChanged` as an enum case for `UIAccessibility.post(notification:argument:)`, which runs from
  the app to VoiceOver. iOS does not vend an observable `NSNotification.Name`, so no public
  notification exists to subscribe to. The code that tried to subscribe failed to compile.

  This bullet had first set the swizzle aside over a feared SwiftUI gap: "the hook fires only at the
  `UIHostingController` boundary and misses transitions within one hosting controller". The fear was
  overstated for the *container* transitions that this item targets. A `NavigationStack` push, a
  `.sheet` or `.fullScreenCover` presentation, and a tab switch each present a `UIHostingController`,
  and its `viewDidAppear` fires. The hook covers UIKit and SwiftUI equally at the
  container-transition granularity.

  The genuine gap is an in-place SwiftUI view swap within one hosting controller. That swap is a
  within-screen update, not a container transition. It is the same out-of-scope case as the next
  bullet and
  [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md). With the
  notification route unavailable, this hook is the remaining public-API mechanism that needs no
  app-screen change.

- **Add a per-screen `.onAppear` (SwiftUI) / lifecycle call (UIKit) that reports to the SDK.**
  Asking each screen to announce its own appearance would cover SwiftUI's transitions within one
  hosting controller, which the accessibility notification may miss. The approach requires editing
  every screen of every app under test.

  That is the per-app cooperation that prime directive 3 and the
  [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) policy keep out of
  the app's own code. A uniform SDK that the app links once is app-agnostic. A modifier that the
  author must sprinkle through the app's views is a per-app difference.

  The narrower automatic signal needs no app-screen change, so the item prefers it. The item
  documents the coverage boundary (unit 5). It does not close the boundary by pushing work into every
  app.

- **Do nothing and rely on the tree-diff polling as it stands.** The option is acceptable, and it
  stays the behavior for any target that does not link `BajutsuKit`. It falls short for apps that can
  opt in. The tree diff cannot distinguish a paused mid-transition frame from a finished screen, and
  it can exhaust its poll budget on a slow host. *Motivation* traces both failure modes. An app that
  links the SDK gains a strictly more accurate signal, at no cost to an app that does not.

- **Widen the signal to cover within-screen data updates as well.** Rejected as out of scope and as
  the wrong mechanism. An in-place data update posts at most `UIAccessibility.layoutChangedNotification`
  and frequently nothing automatic. A screen-transition observer cannot reliably see the update.

  [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait.md) already
  addresses that race at the element level, which is where the race belongs. This item stays scoped
  to the screen-transition signal that it can observe reliably.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Observe completed view-controller appearances (a `viewDidAppear` swizzle) in
  `BajutsuKit`, opt-in gated. (Shipped via the appearance hook, not the accessibility notification the
  proposal first named — see the log below and *Alternatives considered*.)
- [x] Unit 2 — Report the signal out of process through the existing collector channel.
- [x] Unit 3 — Consult the signal from `_await_ready`, with the BE-0218 tree-diff ladder as fallback.
- [x] Unit 4 — Consult the signal from `_wait_settled`, with the tree-diff behavior as fallback.
- [ ] Unit 5 — Confirm on the Simulator that the signal fires across UIKit and SwiftUI (the gate).
  Units 1–4 are implemented and covered by the fast Linux gate (a fake transition source stands in
  for the Simulator); this Unit is the empirical, on-device-only confirmation and needs a human with
  a Mac + Simulator to run it. Procedure and an XCUITest scaffold:
  [`demos/showcase/BE-0310-screen-transition-verification.md`](../../demos/showcase/BE-0310-screen-transition-verification.md).

Log (oldest first):

- Mechanism pivot during implementation. The premise of the item was to *observe*
  `UIAccessibility.screenChangedNotification`. That symbol is outbound-only (a
  `UIAccessibility.post(...)` enum case, not an observable `NSNotification.Name`), so no public
  notification exists to subscribe to. Unit 1 instead swizzles `UIViewController.viewDidAppear`, the
  alternative weighed in *Alternatives considered*. The SwiftUI reach of the swizzle turned out to
  cover the container transitions that this item targets: NavigationStack, modal, and tab each
  present a `UIHostingController` that appears. The wire contract, the Python collector, and units
  2–4 are unchanged. Only the iOS observation mechanism differs. The on-device confirmation of unit 5
  is what proves that the appearance hook fires as assumed across both toolkits.

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
