**English** · [日本語](BE-XXXX-native-z-position-ja.md)

# BE-XXXX — Surface each element's real Z position via an opt-in app-side SDK

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-native-z-position.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
| Related | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/drivers/base.py`'s `Element` — `identifier`, `label`, `traits`, `value`, `frame` — carries
no signal for which element actually sits in front of another. [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)
already closed the tap-time correctness gap this leaves: iOS asks the platform's own `isHittable`,
and Android's adb backend falls back to `topmost_at_point` (`bajutsu/drivers/base.py:830`), a
document-order proxy for paint order. Neither exposes the underlying front-to-back relationship as
evidence a scenario author, or an agent investigating a failure, can read after the fact —
`elements.json` and the opt-in `rawTree` capture (see [docs/evidence.md](../../docs/evidence.md))
both show every element's frame, but nothing about which one actually covers which. This proposal
adds an optional `nativeZ` field to `Element`, populated with each element's real, app-measured
front-to-back position when the app under test opts into a small Bajutsu-provided Software
Development Kit (SDK) hook: iOS computes it from the app's own layer tree through an extension of
BajutsuKit's existing in-app hook, and Android reads it from `View.getZ()` through the accessibility
framework's own on-demand extra-data mechanism. The field is diagnostic only — every existing
occlusion check keeps its current behavior unchanged — and stays `None` for any backend or app that
does not cooperate, the same opt-in shape `ViewportProvider` / `ReadLagProvider` /
`RawSourceProvider` already use.

## Motivation

A step that fails with `ElementNotTappable`, or a scenario author debugging why two elements appear
to overlap in a screenshot, has no way to read the actual stacking order out of the evidence bajutsu
already writes. `elements.json` and `rawTree` both carry every element's frame, but the only signal
for which element is on top is the array's own document order — the same proxy `topmost_at_point`
already uses at tap time, and one its own docstring names as a heuristic, not a real z-index.
Recovering the true stacking order today means leaving bajutsu entirely: attaching a platform
debugger, or reading the app's own source, neither of which the captured evidence can point an
investigator toward on its own.

BE-0349 already closed the correctness question this gap leaves for actuation: iOS's native
`isHittable` and Android's `topmost_at_point` both decide, accurately enough for tapping, whether a
resolved element is reachable. That item's own *Alternatives considered* explicitly deferred the
harder half of this problem to a future proposal — "a fully rigorous same-window Android mechanism
… reflecting into the app's real view hierarchy to read actual Z values", calling it "a materially
larger scope" than a tappability check needed. This proposal is that deferred mechanism, extended to
iOS for symmetry. It also revisits a second alternative BE-0349 rejected, for a different reason: a
uniform `z_index` field added to `Element` for every backend, rejected because a value *derived* from
document order would look authoritative while staying wrong wherever paint order and document order
diverge — Android's `View.elevation` is the reproduced case. `nativeZ` sidesteps that objection by
construction: never derived from the tree bajutsu already has, only measured by the app itself
and reported through explicit cooperation, so a backend or app that does not cooperate reports
`None`, an honest absence, not a wrong guess.

This proposal scopes `nativeZ` to diagnostics: it changes no runtime decision. `is_tappable`,
`topmost_at_point`, and iOS's `isHittable` guard keep their current behavior unchanged, and no
scenario assertion or selector reads the new field. Two reasons hold that boundary here rather than
widening it to replace the occlusion heuristic BE-0349 already ships. First, whether Jetpack
Compose's own accessibility-node generation forwards the extra data Android's mechanism depends on
(below) is not yet confirmed, and gating an existing, working correctness check on an unverified
mechanism would be premature. Second, keeping the two questions separate lets this proposal ship
independently of whether a future one chooses to fold `nativeZ` back into `topmost_at_point`'s
decision once that mechanism is proven.

## Detailed design

### The `nativeZ` field

`Element` (`bajutsu/drivers/base.py:139`) gains one new field:

```python
class Element(TypedDict):
    identifier: str | None
    label: str | None
    traits: list[str]
    value: str | None
    frame: Frame
    nativeZ: float | None
```

`nativeZ` holds the element's own real front-to-back position — computed by the app-side hook
below, never derived from `elements`' own document order — when that hook reports one for the
underlying view, and `None` in every other case: a backend with no such hook (Playwright), an app
that has not opted in, or, on Android, a Compose screen if Unit 0's spike finds no way to carry the
value through Compose's own accessibility-node generation. `resolve_unique`
(`bajutsu/drivers/base.py:647`) and every existing selector match are unaffected — `nativeZ` joins the
record the same way `frame` already does, read but never filtered on.

### iOS: a new synchronous channel into BajutsuKit's in-app hook

BajutsuKit already ships one opt-in in-app hook that computes something at runtime and reports it to
the host: `BajutsuScreen` (`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`,
[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)),
which swizzles `UIViewController.viewDidAppear` and POSTs each completed transition to a collector
the Python side starts on the Simulator's shared loopback. That channel is one-directional and
event-driven — the app pushes when a transition happens — which fits a screen-change signal but not
this proposal's need: the driver must ask, synchronously, "what is this element's real position
right now", timed to the same query it is already issuing. `BajutsuNet`'s collector receives; it does
not answer. Closing that gap needs a genuinely new capability in BajutsuKit: a small in-app
Hypertext Transfer Protocol (`HTTP`) responder, opt-in behind the same `BAJUTSU_COLLECTOR`-style
launch environment gate `BajutsuNet`
already uses, that the driver calls right alongside its own `/elements` query and that computes the
answer fresh on each request rather than replaying a stale push.

What that responder computes is not `CALayer.zPosition` alone. Apple's own documentation for
[`zPosition`](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition) states
plainly that the property "should not be used to specify the order of layer siblings" — the
sublayer array's own order governs that in the ordinary, non-3D-transformed case most UIKit and
SwiftUI screens are in. A raw `zPosition` passthrough would therefore read as authoritative while
being often degenerate (zero for every element on a flat layout) — exactly the failure mode this
proposal's Motivation faults BE-0349's rejected `z_index` alternative for. The responder instead
walks the real layer tree — each `CALayer`'s `sublayers` order, with `zPosition` applied only where
it is non-zero — and reports a definitive front-to-back index per accessibility element, keyed by the
identifier `xcuitest.py`'s `_query_with_handles` (`bajutsu/drivers/xcuitest.py:533`) already resolves.
Whether this computation needs to walk `UIView`s, `CALayer`s, or both to match what actually
composites on screen for a `SwiftUI` view hierarchy — which does not expose its own layout tree as
directly as UIKit's — is Unit 0's to confirm empirically, on-device, the same way BE-0349 confirmed
`isHittable`'s behavior rather than trusting documentation alone.

### Android: the accessibility framework's own on-demand extra-data mechanism

Android's accessibility framework publishes `AccessibilityNodeInfo` as a public Application
Programming Interface (API). Android already has a first-class, opt-in mechanism for exactly this
shape of problem, so this half needs no new channel. A `View` overrides
[`addExtraDataToAccessibilityNodeInfo(AccessibilityNodeInfo, String, Bundle)`](https://developer.android.com/reference/android/view/View)
to attach data an accessibility client did not ask for by default, and declares which extra-data
keys it supports through `AccessibilityNodeInfo.setAvailableExtraData(List<String>)` inside the same
override; a client — here, the resident server's own on-device request handler
(`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`'s
`respondSource`, [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)),
which already talks to the device as an `AccessibilityService` — calls
[`AccessibilityNodeInfo.refreshWithExtraData(String key, Bundle args)`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
per node and reads the result back from `getExtras()`, alongside the Extensible Markup Language
(`XML`) dump `respondSource` already builds today. That pairing is not free: `respondSource`'s body
comes from `settledDump` → `UiDevice.dumpWindowHierarchy`, which traverses and serializes in one
platform call and exposes no per-node `AccessibilityNodeInfo` to refresh, so the extra data needs a
second walk over `UiAutomation.getRootInActiveWindow()` — which covers only the active window, while
`dumpWindowHierarchy` spans every window including the SystemUI status bar. Unit 0 settles how the
two walks are reconciled and what the second one costs. This is the same mechanism the
platform's own
`EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` uses for on-demand text bounds, so this proposal's Android
SDK is a small helper apps call from a `View` subclass or a `ViewCompat` extension, reporting
`view.getZ()` — elevation plus any translation on the z axis, the value that actually reorders
Android's own real composited draw order, unlike iOS's `zPosition` above (BE-0349's own spike already
confirmed this empirically for `View.elevation`) — under a Bajutsu-owned extra-data key.
`dumpWindowHierarchy`'s `XML` format has no attribute slot for a value it does not itself define, so
`respondSource` cannot add `nativeZ` as a new `XML` attribute directly; it instead returns the
collected
per-node values as a small side structure index-aligned with the `<node>` sequence of the `XML`
body — keyed by document-order position, not by node identity. Identity alone cannot key this: the
four-field `resource-id`/`content-desc`/`text`/`class` tuple `adb.py`'s own `_identity()`
(`bajutsu/drivers/adb.py:307`) produces is deliberately *not* unique — the resident channel pairs it
with `index` of `count` (`bajutsu/drivers/adb.py:104`–`106`) precisely because a list of identical
rows collapses to a single identity — so an identity-keyed map would silently hand every such row
the same `nativeZ`. `bajutsu/adb_resident.py`'s hierarchy fetch (`fetch_source`,
`bajutsu/adb_resident.py:90`) stays the same single `GET /source` round trip it is today, now also
carrying that side structure back; and `_elements_from_nodes` (`bajutsu/drivers/adb.py:322`), which
walks the same `<node>` sequence `parse_hierarchy_with_identities` (`bajutsu/drivers/adb.py:336`)
already aligns element *i* against, carries position *i*'s value into element *i*'s `nativeZ`.
The one added round trip is inside `respondSource` itself —
the per-node `refreshWithExtraData` calls — issued only when a node's own
`getAvailableExtraDataKeys()` already lists the Bajutsu key, so a non-cooperating app pays nothing
beyond that one cheap check.

Whether Jetpack Compose's own accessibility-node generation
(`AndroidComposeViewAccessibilityDelegateCompat`) forwards a `Modifier`-declared extra-data key
through to the `AccessibilityNodeInfo` a `View`-based override would normally control is not yet
confirmed — Compose owns that generation step itself, and neither this proposal's author nor
BE-0349's own spike exercised it. Unit 0 answers this empirically, the same way BE-0349's own
pre-design spike settled the `View.elevation` versus Compose `zIndex` question rather than guessing
from documentation, and this section's Android design — specifically whether Compose needs a
reporting path distinct from the `View` override above, or is out of scope for a first slice — is
finalized once that spike returns.

### Cost stays opt-in on both platforms

Neither platform's path costs anything extra for a backend or app that does not cooperate. iOS's
driver gets a connection refused (or no response) from a responder no app installed, and
`nativeZ` stays `None`, the same degrade `RawSourceProvider` gives a backend that never implements
it. Android's per-node `refreshWithExtraData` round trip is gated on that node's own
`getAvailableExtraDataKeys()` already advertising the Bajutsu key, so an uninstrumented app's tree
walk pays one cheap existence check per node and nothing more. For a cooperating app on either
platform, Unit 0's spike also measures the actual per-element round-trip cost against a
showcase-sized tree — the same per-read cost bar [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)'s
own resident-channel motivation was held to — and records the result in this section before Units 2
and 3 land.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

0. **Spike.** On iOS: confirm the layer/view walk needed to report a definitive front-to-back index
   for both UIKit and SwiftUI screens, on-device, and design the new synchronous in-app responder's
   shape (a fixed local port versus one negotiated through the existing `BAJUTSU_COLLECTOR`-style
   launch environment). On Android: confirm whether Jetpack Compose's accessibility-node generation
   forwards a custom extra-data key declared through `Modifier.semantics`, on a small Compose screen
   using `Modifier.zIndex` and `graphicsLayer`. On both: measure the per-element round-trip cost
   against a showcase-sized tree. Record every finding in *Detailed design* above (works / needs a
   workaround / not supported) before Units 2 and 3 start, the same practice BE-0349's own Log
   entry followed. Blocks Units 2 and 3.
1. **The `nativeZ` field.** Add it to `Element` (`bajutsu/drivers/base.py`). `Element` carries no
   `total=False`, so every existing dict-literal construction site across every backend — adb,
   XCUITest, the live XCUITest client, the web Document Object Model (DOM) parser, and
   `record_capture.py`'s refused-capture
   sentinel among them — must set `nativeZ` too, or `mypy --strict` (part of `make check`) fails the
   build; that check is the safety net against missing one, so this unit need not enumerate every
   site by hand. No behavior change, since nothing yet computes a real value for it.
2. **iOS reporting.** The new BajutsuKit in-app responder, and `xcuitest.py`'s read of it into
   `nativeZ`, scoped by Unit 0's findings.
3. **Android reporting.** The app-side extra-data helper; `ResidentServerTest.kt`'s `respondSource`
   opt-in per-node `refreshWithExtraData` calls and the side structure it returns alongside the `XML`
   body; and `adb.py`'s read of that structure into `nativeZ`. Scoped by Unit 0's findings — a
   `View`-only first slice if Compose turns out not to forward the key.
4. **`FakeDriver` support.** A settable `nativeZ` per fixture element
   (`bajutsu/drivers/fake.py`), so a scenario-level test can exercise a `nativeZ`-aware reader
   without a real device.
5. **Driver conformance suite.** Extend [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)'s
   suite (`tests/driver_conformance.py`) with a case asserting `nativeZ` is populated only when a
   fixture or backend explicitly reports it, and stays `None` otherwise.
6. **Docs.** [`docs/evidence.md`](../../docs/evidence.md) and [`docs/architecture.md`](../../docs/architecture.md)
   (and their `docs/ja/` mirrors), stating plainly that `nativeZ` is diagnostic only and does not
   change `is_tappable`, `topmost_at_point`, or `isHittable`.
7. **Tests.** Unit tests per backend for the new reporting path, plus a regression pinning that
   `is_tappable` and `topmost_at_point` are unchanged by this proposal.

### Prime directives preserved

- **AI never judges.** `nativeZ` is a measured value read through an app-side SDK call or an
  accessibility API; no model call enters any path that produces or reads it.
- **Determinism first.** Reading `nativeZ` is a bounded, synchronous call on both platforms, with no
  polling and no fixed sleep — the same shape as reading `frame` today.
- **App-agnostic.** The SDK hook is uniform and opt-in on both platforms, the same shape as
  BajutsuKit's existing `viewDidAppear` swizzle: the tool and drivers gain one code path each, with
  no per-app branching anywhere in it. An app that never links the hook is unaffected and reports
  `None`.

## Alternatives considered

- **Folding `nativeZ` into `topmost_at_point` / `is_tappable` now, instead of keeping it
  diagnostic-only.** Considered, since it would let Android's occlusion check stop misjudging
  `View.elevation` once a real value exists. Rejected for this proposal's first slice: Compose's
  extra-data support is unconfirmed, and gating an existing, working correctness check on an
  unverified mechanism would be premature. Left as a natural follow-up once Unit 0 confirms the
  mechanism works.
- **A new opt-in capture kind alongside `rawTree`, instead of a routine `Element` field.**
  Considered, for symmetry with `rawTree`'s own opt-in shape. Rejected: unlike `rawTree` — a snapshot
  of the pre-parse device reply, deliberately kept out of `Element` itself
  (see [docs/evidence.md](../../docs/evidence.md)) — `nativeZ` is a per-element property of the same
  kind `frame` and `traits` already are, so it belongs on `Element` directly rather than in a second
  artifact a reader would have to reconcile with `elements.json` by identity, the exact alignment
  problem `capture()`'s own kind-ordering rule (`bajutsu/evidence/core.py:218`) already exists to
  avoid for `rawTree`.
- **A fully derived, non-measured z field, computed uniformly from document order plus
  per-toolkit heuristics.** This is BE-0349's own rejected `z_index` alternative, restated; see
  Motivation for why it does not survive here either.
- **Cross-window occlusion (a system dialog or a toast covering the whole app).** Out of scope,
  unchanged from BE-0349's own deferral: `AccessibilityWindowInfo.getLayer()` was found insufficient
  for the two most common cases there, and this proposal does not reopen that question.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 0 — spike: iOS layer-walk feasibility and responder shape; Android Compose extra-data
      support; per-element round-trip cost on both platforms
- [ ] Unit 1 — the `nativeZ` field on `Element`
- [ ] Unit 2 — iOS reporting (BajutsuKit responder, `xcuitest.py` read)
- [ ] Unit 3 — Android reporting (extra-data helper, resident server round trip, `adb.py` read)
- [ ] Unit 4 — `FakeDriver` support
- [ ] Unit 5 — driver conformance suite case
- [ ] Unit 6 — docs (`evidence.md`, `architecture.md`, and their `ja` mirrors)
- [ ] Unit 7 — tests

## References

- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) — the
  tappability check whose *Alternatives considered* deferred this proposal's Android mechanism and
  rejected a derived `z_index` field for reasons this proposal's `nativeZ` sidesteps
- [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md) —
  `BajutsuScreen`, the existing in-app hook and its one-directional collector channel this proposal's
  iOS mechanism extends with a new synchronous responder
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident `AccessibilityService` channel this proposal's Android mechanism adds a per-node round
  trip to
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the
  conformance suite Unit 5 extends
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Element`, `topmost_at_point`,
  `resolve_unique`
- [`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`](../../BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift) —
  the existing in-app hook this proposal's iOS responder sits beside
- [`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt) —
  `respondSource`, the on-device request handler this proposal's per-node `refreshWithExtraData`
  calls run inside
- [`bajutsu/adb_resident.py`](../../bajutsu/adb_resident.py),
  [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — the unchanged Python-side hierarchy
  fetch transport and the `Element` construction this proposal's Android mechanism extends
- [`zPosition` — Apple Developer Documentation](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition) —
  states that the property should not be used to determine sibling layer order, the reason this
  proposal's iOS responder walks the real layer tree rather than reading it directly
- [`View` — Android Developers API reference](https://developer.android.com/reference/android/view/View) —
  `addExtraDataToAccessibilityNodeInfo`, the on-demand extra-data mechanism this proposal's Android
  helper uses
- [`AccessibilityNodeInfo` — Android Developers API reference](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo) —
  `refreshWithExtraData` / `setAvailableExtraData`, the client-side half of the same mechanism
