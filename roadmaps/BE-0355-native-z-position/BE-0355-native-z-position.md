**English** · [日本語](BE-0355-native-z-position-ja.md)

# BE-0355 — Surface each element's real Z position via an opt-in app-side SDK

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0355](BE-0355-native-z-position.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0355") |
| Implementing PR | [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556), [#1709](https://github.com/bajutsu-e2e/bajutsu/pull/1709) |
| Topic | Driver & backend architecture |
| Related | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) |
<!-- /BE-METADATA -->

## Introduction

The `Element` type in `bajutsu/drivers/base.py` carries five fields.
They are `identifier`, `label`, `traits`, `value`, and `frame`. None of them says which element actually sits in front of another.
[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) already
closed the tap-time correctness gap that this leaves. iOS asks the platform's own `isHittable`.
The adb backend of Android falls back to `topmost_at_point` (`bajutsu/drivers/base.py:830`).
That function is a document-order proxy for paint order.

Neither exposes the underlying front-to-back relationship as evidence.
A scenario author cannot read it after the fact.
Neither can an agent that investigates a failure. `elements.json` and the opt-in `rawTree` capture both show the frame of every element.
See [docs/evidence.md](../../docs/evidence.md).
Neither shows which element covers which.

This proposal adds an optional `nativeZ` field to `Element`. The field holds the real front-to-back
position of each element, as measured by the app itself.
The app under test opts in through a small hook.
The hook belongs to a Bajutsu-provided Software Development Kit (SDK).

- iOS computes the position from the layer tree of the app.
  It does so through an extension of the existing in-app hook of BajutsuKit.
- Android reads the position from `View.getZ()`.
  It does so through the on-demand extra-data mechanism of the accessibility framework.

The field is diagnostic only. Every existing occlusion check keeps its current behavior. The field
stays `None` for any backend or app that does not cooperate. `ViewportProvider`, `ReadLagProvider`,
and `RawSourceProvider` already use the same opt-in shape.

## Motivation

Consider a step that fails with `ElementNotTappable`.
Consider also a scenario author who debugs why two elements appear to overlap in a screenshot. Neither can read the actual stacking order from the
evidence that bajutsu already writes. `elements.json` and `rawTree` both carry the frame of every
element. The sole signal for which element is on top is the document order of the array. That is the proxy that `topmost_at_point` already uses at tap time.
Its own docstring names the proxy as a heuristic, not a real z-index.

Recovering the true stacking order today means leaving bajutsu entirely.
The investigator attaches a platform debugger, or reads the source of the app. The captured evidence cannot point an investigator toward
either on its own.

BE-0349 already closed the correctness question that this gap leaves for actuation. The native `isHittable` of iOS and the `topmost_at_point` of Android both decide whether a resolved element is reachable.
Both are accurate enough for tapping. The *Alternatives considered* section of that item
deferred the harder half of the problem to a future proposal: "a fully rigorous same-window Android
mechanism … reflecting into the app's real view hierarchy to read actual Z values". It called that
"a materially larger scope" than a tappability check needed. This proposal is that deferred
mechanism, extended to iOS for symmetry.

The proposal also revisits a second alternative that BE-0349 rejected, for a different reason. That
alternative was a uniform `z_index` field added to `Element` for every backend. BE-0349 rejected it
because a value *derived* from document order would look authoritative while staying wrong wherever
paint order and document order diverge. The `View.elevation` of Android is the reproduced case.

`nativeZ` sidesteps that objection by construction.
It is never derived from the tree that bajutsu already has.
The app itself measures it and reports it through explicit cooperation.
A backend or app that does not cooperate reports `None`, an honest absence, not a wrong guess.

This proposal scopes `nativeZ` to diagnostics. It changes no runtime decision. `is_tappable`, `topmost_at_point`, and the `isHittable` guard of iOS keep their current behavior.
No scenario assertion or selector reads the new field. Two reasons hold that boundary here.
The alternative is to widen it and replace the occlusion heuristic that BE-0349 already ships.

1. Nobody has confirmed whether the accessibility-node generation of Jetpack Compose forwards the
   extra data that the Android mechanism depends on (below).
   Gating a working correctness check on an unverified mechanism would be premature.
2. Keeping the two questions separate lets this proposal ship on its own.
   A future proposal may fold `nativeZ` back into `topmost_at_point`.
   That can happen once the mechanism is proven.

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

`nativeZ` holds the real front-to-back position of the element. The app-side hook below computes it,
and it is never derived from the document order of `elements`. The field is `None` in every other
case:

- a backend with no such hook (Playwright);
- an app that has not opted in;
- on Android, a Compose screen.
  This holds if the spike of Unit 0 finds no way to carry the value through Compose.

`resolve_unique` (`bajutsu/drivers/base.py:647`) and every existing selector match are unaffected.
`nativeZ` joins the record the way `frame` already does: read, but never filtered on.

What the number *means* is per-platform, and Unit 0 settles it before Units 2 and 3 encode it.

- The responder of iOS reports a front-to-back *ordinal* from the layer walk.
- Android reports `View.getZ()` in device pixels. `getZ()` orders siblings within one parent, not
  the whole tree. Take a child at `getZ() == 0` under a parent at `getZ() == 8`.
  It still composites in front of the sibling of that parent at `getZ() == 4`.

The `nativeZ` values of two elements are therefore not comparable across backends.
On Android, they are not comparable across parents either.

**Unit 0 settled this as per-backend units, named explicitly.**
One convention is shared: a larger `nativeZ` is closer to the viewer, on every backend.

The alternative was to normalize both backends onto one cross-backend ordinal. Unit 0 rejected it.
Normalizing the `getZ()` of Android into a tree-global ordinal means combining the measurement of the
app with the document order that bajutsu already has.
That is the derivation that the Motivation faults in the rejected `z_index` alternative.
Normalizing would also discard what the Android reading carries beyond order: how far in front.

Unit 6 records the choice in [`docs/evidence.md`](../../docs/evidence.md).
A reader then cannot draw the same falsely authoritative conclusion from `nativeZ`.

### iOS: a new synchronous channel into BajutsuKit's in-app hook

BajutsuKit already ships one opt-in in-app hook that computes something at runtime and reports it to
the host: `BajutsuScreen` (`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift`,
[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)).
It swizzles `UIViewController.viewDidAppear`.
It POSTs each completed transition to a collector that the Python side starts.
The collector listens on the shared loopback of the Simulator.

That channel is one-directional and event-driven: the app pushes when a transition happens.
This fits a screen-change signal, but not the need of this proposal.
The driver must ask, synchronously, "what is this element's real position right now".
The question is timed to the same query that the driver already issues. The
collector of `BajutsuNet` receives; it does not answer.

Closing that gap needs a genuinely new capability in BajutsuKit.
The capability is a small in-app Hypertext Transfer Protocol (`HTTP`) responder. It is opt-in.
The gate is the same `BAJUTSU_COLLECTOR`-style launch environment that `BajutsuNet` already uses. The driver calls it right alongside its own `/elements` query.
It computes the answer fresh on each request.
It never replays a stale push.

A *listener* needs protection that the shape of the outbound collector does not imply.
It binds loopback and nothing else.
It requires on every request the same per-run shared secret that `BajutsuNet` already carries (`BAJUTSU_COLLECTOR_TOKEN`, `BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:16`).
iOS loopback is not isolated between apps.
This responder returns the whole view hierarchy of the app. That constraint is an input to the port-shape decision of Unit 0 below, not an afterthought. Any co-resident app on a real device can probe a fixed well-known port.
No app can probe a negotiated one.

What the responder computes is not `CALayer.zPosition` alone. Apple's documentation for [`zPosition`](https://developer.apple.com/documentation/quartzcore/calayer/1410884-zposition) is plain.
The property "should not be used to specify the order of layer siblings". Most UIKit and SwiftUI screens are in the ordinary, non-3D-transformed case.
There, the order of the sublayer array governs sibling order.

A raw `zPosition` passthrough would read as authoritative.
It would often be degenerate, with zero for every element on a flat layout. That is the failure mode that the Motivation faults in the
rejected `z_index` alternative of BE-0349.

The responder instead walks the real layer tree. It follows the `sublayers` order of each `CALayer`.
It applies `zPosition` where it is non-zero, and nowhere else.
It reports a definitive front-to-back index per accessibility element. The index is keyed by the identifier that `_query_with_handles` already resolves.
That function lives in `bajutsu/drivers/xcuitest.py:533`.

A `SwiftUI` view hierarchy does not expose its layout tree as directly as UIKit does.
Unit 0 had to confirm on device whether the computation walks `UIView`s, `CALayer`s, or both.
The computation must match what composites on screen.
BE-0349 confirmed the behavior of `isHittable` the same way, and did not trust documentation alone.

**Unit 0's findings (measured on a booted Simulator against the showcase's UIKit and SwiftUI apps).**

- **The responder is not a new capability after all.** `BajutsuWebView`
  ([`BajutsuKit/Sources/BajutsuKit/BajutsuWebView.swift`](../../BajutsuKit/Sources/BajutsuKit/BajutsuWebView.swift),
  BE-0037) already runs a loopback socket server inside the app under test, with a host-allocated
  ephemeral port. This responder reuses that shape and adds the token check that the existing bridge
  lacks.
- **The port is negotiated, and the secret is its own.** `BAJUTSU_ZORDER_PORT` and
  `BAJUTSU_ZORDER_TOKEN` are allocated per lease beside the own port of the WebView bridge. Parallel
  devices never contend, and a fixed well-known port is never probeable. The secret is minted for
  this responder, instead of reusing `BAJUTSU_COLLECTOR_TOKEN` as this proposal first assumed. That
  token exists only when a scenario runs a network collector, and a responder that must refuse every
  unauthenticated request cannot depend on a secret that is often absent.
- **A `UIView`-tree walk reports for UIKit.** The front-to-back ordinal of each view comes from the
  `subviews` array, and `zPosition` breaks the order only where an app set one. The ordinal is keyed
  by `accessibilityIdentifier`.
- **SwiftUI reports nothing, and no non-private path changes that.** A walk of the whole view tree of
  the SwiftUI showcase finds no `accessibilityIdentifier` anywhere and no vended accessibility
  elements. The leaves are `CGDrawingView` and `_UIShapeHitTestingView`, because SwiftUI
  materializes its accessibility elements only for an assistive technology attached to the process,
  which an app that queries itself is not. Bar-button and tab-bar items are likewise uncovered on both toolkits.
  They are `UIBarButtonItem` and `UITabBarItem` rather than views. Unit 2 therefore ships a UIKit-only first slice.
  It is the symmetric twin of the `View`-only slice of Unit 3.
- **Cost.**
  One `/zorder` round trip against a showcase-sized tree measured 2.0–2.4 ms.
  A refused connection to an app with no responder measured 0.16 ms.

### Android: the accessibility framework's own on-demand extra-data mechanism

The accessibility framework of Android publishes `AccessibilityNodeInfo`.
It is a public Application Programming Interface (API). Android already has a first-class, opt-in mechanism for this shape of problem.
This half needs no new channel.

A `View` overrides
[`addExtraDataToAccessibilityNodeInfo(AccessibilityNodeInfo, String, Bundle)`](https://developer.android.com/reference/android/view/View)
to attach data that an accessibility client did not ask for by default. Inside the same override, it declares the extra-data keys it supports.
It does so through `AccessibilityNodeInfo.setAvailableExtraData(List<String>)`.

A client reads the data back. Here the client is the on-device request handler of the resident
server: `respondSource` in
`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt`
([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md)).
It already talks to the device as an `AccessibilityService`.
It calls
[`AccessibilityNodeInfo.refreshWithExtraData(String key, Bundle args)`](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo)
per node, and reads the result back from `getExtras()`.
The call sits alongside the dump that `respondSource` already builds today.
The dump is in Extensible Markup Language (`XML`).

That pairing is not free. The body of `respondSource` comes from `settledDump`, which calls
`UiDevice.dumpWindowHierarchy`. That call traverses and serializes in one step.
It exposes no per-node `AccessibilityNodeInfo` to refresh. The extra data therefore needs a second walk
over `UiAutomation.getRootInActiveWindow()`. That walk covers the active window alone.
`dumpWindowHierarchy` spans every window, including the SystemUI status bar. Unit 0 settles how the
two walks are reconciled and what the second one costs.

The platform's own `EXTRA_DATA_TEXT_CHARACTER_LOCATION_KEY` uses the same mechanism.
It serves on-demand text bounds. The Android SDK of this proposal is a small helper.
Apps call it from a `View` subclass or a `ViewCompat` extension. It reports `view.getZ()` under a Bajutsu-owned
extra-data key. `getZ()` is elevation plus any translation on the z axis, the value that actually
reorders the real composited draw order of Android, unlike the `zPosition` of iOS above. The spike of
BE-0349 already confirmed this empirically for `View.elevation`.

The `XML` format of `dumpWindowHierarchy` has no attribute slot for a value it does not define.
So `respondSource` cannot add `nativeZ` as a new `XML` attribute.
It returns the per-node values as a small side structure.
The structure is index-aligned with the `<node>` sequence of the `XML` body.
It is keyed by document-order position and not by node identity.

Identity alone cannot key this. The four-field `resource-id` / `content-desc` / `text` / `class`
tuple that `_identity()` in `adb.py` (`bajutsu/drivers/adb.py:307`) produces is deliberately *not*
unique. The resident channel pairs it with `index` of `count` (`bajutsu/drivers/adb.py:104`–`106`).
A list of identical rows collapses to a single identity. An identity-keyed map would
silently hand every such row the same `nativeZ`.

The hierarchy fetch of `bajutsu/adb_resident.py` (`fetch_source`, `bajutsu/adb_resident.py:90`)
stays the same single `GET /source` round trip as today, and now also carries that side structure
back. `_elements_from_nodes` (`bajutsu/drivers/adb.py:322`) walks the same `<node>` sequence that
`parse_hierarchy_with_identities` (`bajutsu/drivers/adb.py:336`) already aligns element *i* against.
It carries the value at position *i* into the `nativeZ` of element *i*.

The added cost sits inside `respondSource` itself.
The reconciling second walk over the active window runs on every read.
It runs even for an app that did not opt in.
The per-node `refreshWithExtraData` call inside that walk is gated, on a node that already advertises the Bajutsu key through `AccessibilityNodeInfo.getAvailableExtraData()`.
Nothing else in the walk is gated. Unit 0's cost finding below gives the measured size of the walk itself.
The shipped slice does not gate that walk.

**Unit 0's findings (measured on an API 34 emulator against the showcase's Compose and Views apps).**

- **Compose forwards no app-declared extra-data key.** Two `Modifier.semantics` nodes declared a
  custom `SemanticsPropertyKey` named exactly like the Bajutsu key. They advertised only the fixed
  set of Compose (`androidx.compose.ui.semantics.id`, `…testTag`) plus the keys of the platform, and
  no node answered the Bajutsu key. Unit 3 therefore ships the `View`-only first slice that this
  section already named as the fallback. Two related readings are left for a future item. First, the
  XML of the resident channel carries a `drawing-order` attribute (which the `uiautomator dump`
  fallback does not), and it reads `0` for every Compose node because a Compose node is not a real
  `View`. Second, the own accessibility tree of Compose already orders `Modifier.zIndex` siblings by
  paint order, so document order is not wrong there the way it is for `View.elevation`.
- **The API this section named does not exist.** The client-side getter is
  `AccessibilityNodeInfo.getAvailableExtraData()`, not `getAvailableExtraDataKeys()`. Only the setter
  carries the `…ExtraData` name that this section assumed both did.
- **The platform does not route the on-demand callback through an accessibility delegate.** The
  `onInitializeAccessibilityNodeInfo` of a delegate is called, so `setAvailableExtraData` from one
  takes effect. But `addExtraDataToAccessibilityNodeInfo` is not called, so `refreshWithExtraData`
  returned true and delivered nothing. The app-side helper therefore both declares the key and
  writes the value into `AccessibilityNodeInfo.getExtras()` while the node is being built, which is
  delivered. It keeps the on-demand override, so a `View` subclass written to the original shape of
  this section answers too. `respondSource` reads the value that is already present first, and asks
  only when it is absent, so the common case costs no per-node round trip at all.
- **A document-order-keyed side structure does not line up, by 28 nodes.** On a plain showcase screen,
  `dumpWindowHierarchy` returned 72 nodes spanning every window, while the walk of
  `getRootInActiveWindow()` returned 45. The same element sat at document position 35 in the body and
  at index 7 in the walk. Each value is therefore keyed by what the host can recompute from the
  `<node>` it is reading: bounds, class, package, and the occurrence index among nodes that agree on
  all three. Both sides walk the same accessibility tree depth-first, so the occurrence counts agree,
  scoped to the active window. `narrow_to_active_window` drops the own windows of SystemUI on the
  host, but not a second window of the app under test itself (a dialog over its main window, for
  example). Two opted-in nodes that share bounds, class, and package across the app's own windows
  could shift each other's count. This is recorded as a known limitation in the docstring of
  `_native_z_key`, and is not closed in this slice.
- **The bounds half of that key must be screen-clipped, not raw.** The own `bounds` attribute of
  `dumpWindowHierarchy` is the rect of a node intersected with the display
  (`AccessibilityNodeInfoDumper` via `AccessibilityNodeInfoHelper.getVisibleBoundsInScreen`). A walk
  that keyed on the raw `getBoundsInScreen()` rect disagreed with the recomputed key of the host for
  any node hanging past the display edge. It reported the honest absence instead of the real
  measurement of the app, on exactly the clipped, partially off-screen layouts that an investigator
  opens the evidence for. The walk now intersects each rect with the own display bounds of the device
  before keying, matching the dumper. This reproduces only the display-edge half of that clipping,
  and not clipping by the own bounds of an ancestor (a node inside a smaller scrollable container
  that never reaches the screen edge, for example). That is a narrower gap, left beside the
  multi-window limitation above.
- **Cost.** A full per-node `refresh()` walk over 45 nodes measured 24 ms, against 19 ms for a warm
  `dumpWindowHierarchy`. The eager path above avoids the per-node round trip entirely for an app that
  uses the helper, but not the walk itself, which runs on every read whether or not the app opted in
  (see *Cost stays opt-in on both platforms* below).

### Cost stays opt-in on both platforms

A run without a cooperating app pays one bounded probe on either platform, and no more.

On iOS, the driver gets a connection refused. If a socket opens and never answers, it gets a short
connect or read timeout that Unit 0 fixes. Either way, `nativeZ` stays `None`.
That is the degrade that `RawSourceProvider` gives a backend that never implements it. iOS cannot tell an uninstrumented app
apart without attempting the connection. The driver caches that first failure for the session and stops probing.
A non-cooperating app thus pays the timeout once rather than on every `/elements` query. This keeps the read "bounded and synchronous".
*Prime directives preserved* below claims that.

On Android, the per-node `refreshWithExtraData` round trip is gated.
The gate is that the node already advertises the Bajutsu key in `getAvailableExtraData()`. The tree walk of an uninstrumented app pays one cheap existence check per node.
It pays nothing more *for that call*.

For a cooperating app on either platform, the spike of Unit 0 also measures a cost.
The cost is the actual per-element round trip, against a showcase-sized tree. It holds the cost to the same per-read bar as the
resident-channel motivation of
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md),
and records the result in this section before Units 2 and 3 land.

**As shipped, the title of this section overstates Android's half.**
The reconciling walk itself runs on every `GET /source`.
The per-node call is not the only part that runs.
That includes an app that never linked the helper.
`respondSource` cannot know in advance that a tree carries no opted-in node without walking it.

The cost measurement of Unit 0 (above) puts that walk in the same order as the warm
`dumpWindowHierarchy` of the resident channel.
The walk therefore roughly doubles the per-read cost of every Android run, not only one that uses `nativeZ`.

This is left as a known gap rather than closed here.
The host could send a signal only for a target that has opted into `nativeZ`, mirroring how `since=`
is already threaded per read.
Gating the walk on that signal would restore the opt-in cost that this section originally promised.
That plumbing is a follow-up unit's to design and land.
This unit does not improvise it under review.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

0. **Spike.** The spike decides what `nativeZ` *means* across backends: a normalized cross-backend
   front-to-back ordinal, or per-backend units named explicitly. See "The `nativeZ` field" in
   *Detailed design*. It also covers the following.
   - On iOS, confirm on device the layer or view walk that reports a definitive front-to-back index
     for both UIKit and SwiftUI screens.
   - On iOS, fix the connect and read timeout of the responder, and the session-scoped negative
     cache that the single failed probe of a non-cooperating app relies on.
   - On iOS, design the shape of the responder. Weigh security (a fixed local port is probeable by
     any co-resident app on a real device) against the existing `BAJUTSU_COLLECTOR`-style launch
     environment.
   - On Android, confirm whether the accessibility-node generation of Jetpack Compose forwards a
     custom extra-data key declared through `Modifier.semantics`. Use a small Compose screen with
     `Modifier.zIndex` and `graphicsLayer`.
   - On Android, confirm how the node set of `UiAutomation.getRootInActiveWindow()` (needed to call
     `refreshWithExtraData`) reconciles with the wider, multi-window set of
     `dumpWindowHierarchy`. The index-aligned side structure must line up with the `XML` body it
     accompanies, and the spike must not assume that it does.
   - On both, measure the per-element round-trip cost against a showcase-sized tree.

   Every finding is recorded in *Detailed design* above (works, needs a workaround, or not
   supported) before Units 2 and 3 start, as the Log entry of BE-0349 did. Unit 0 blocks Units 2 and 3.
1. **The `nativeZ` field.** Add it to `Element` (`bajutsu/drivers/base.py`). `Element` carries no
   `total=False`, so every existing dict-literal construction site across every backend must set
   `nativeZ` too. The sites include adb, XCUITest, the live XCUITest client, the web Document Object
   Model (DOM) parser, and the refused-capture sentinel of `record_capture.py`. Otherwise
   `mypy --strict` (part of `make check`) fails the build. That check is the safety net against
   missing one, so this unit need not list every site by hand. No behavior changes, since nothing
   yet computes a real value for it.
2. **iOS reporting.** The new BajutsuKit in-app responder, and the read of it into `nativeZ` in
   `xcuitest.py`, scoped by the findings of Unit 0.
3. **Android reporting.** The app-side extra-data helper; the opt-in per-node
   `refreshWithExtraData` calls in `respondSource` of `ResidentServerTest.kt`, with the side
   structure that it returns alongside the `XML` body; and the read of that structure into
   `nativeZ` in `adb.py`. The findings of Unit 0 scope it: a `View`-only first slice if Compose
   does not forward the key.
4. **`FakeDriver` support.** A settable `nativeZ` per fixture element (`bajutsu/drivers/fake.py`),
   so a scenario-level test can exercise a `nativeZ`-aware reader without a real device.
5. **Driver conformance suite.** Extend the suite of
   [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)
   (`tests/driver_conformance.py`) with a case. The case asserts that `nativeZ` is populated when a
   fixture or backend explicitly reports it, and stays `None` otherwise.
6. **Docs.** [`docs/evidence.md`](../../docs/evidence.md) and
   [`docs/architecture.md`](../../docs/architecture.md), with their `docs/ja/` mirrors. They state
   plainly that `nativeZ` is diagnostic and does not change `is_tappable`, `topmost_at_point`, or
   `isHittable`. They also record the choice of Unit 0 on what `nativeZ` means across backends.
7. **Tests.** Unit tests per backend for the new reporting path, plus a regression that pins
   `is_tappable` and `topmost_at_point` as unchanged by this proposal.

### Prime directives preserved

- **AI never judges.** `nativeZ` is a measured value, read through an app-side SDK call or an
  accessibility API. No model call enters any path that produces or reads it.
- **Determinism first.** Reading `nativeZ` is a bounded, synchronous call on both platforms, with no
  polling and no fixed sleep. It has the same shape as reading `frame` today.
- **App-agnostic.** The SDK hook is uniform and opt-in on both platforms, the same shape as the
  existing `viewDidAppear` swizzle of BajutsuKit. The tool and drivers gain one code path each, with
  no per-app branching anywhere in it. An app that never links the hook is unaffected and reports
  `None`.

## Alternatives considered

- **Folding `nativeZ` into `topmost_at_point` / `is_tappable` now, instead of keeping it
  diagnostic-only.** Considered, since it would let the occlusion check of Android stop misjudging
  `View.elevation` once a real value exists. Rejected for the first slice of this proposal. The
  extra-data support of Compose is unconfirmed, and gating an existing, working correctness check on
  an unverified mechanism would be premature. It is a natural follow-up once Unit 0 confirms that
  the mechanism works.
- **A new opt-in capture kind alongside `rawTree`, instead of a routine `Element` field.**
  Considered, for symmetry with the own opt-in shape of `rawTree`. Rejected. `rawTree` is a snapshot
  of the pre-parse device reply, deliberately kept out of `Element` itself (see
  [docs/evidence.md](../../docs/evidence.md)). `nativeZ` is a per-element property of the same kind
  as `frame` and `traits`. It belongs on `Element` directly, and not in a second artifact that a
  reader would have to reconcile with `elements.json` by identity. That is the alignment problem that
  the kind-ordering rule of `capture()` (`bajutsu/evidence/core.py:218`) already exists to avoid for
  `rawTree`.
- **A fully derived, non-measured z field, computed uniformly from document order plus per-toolkit
  heuristics.** This is the rejected `z_index` alternative of BE-0349, restated. See the Motivation
  for why it does not survive here either.
- **Cross-window occlusion (a system dialog or a toast that covers the whole app).** Out of scope,
  unchanged from the own deferral of BE-0349. `AccessibilityWindowInfo.getLayer()` was found
  insufficient for the two most common cases there, and this proposal does not reopen that question.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 0 — spike: iOS layer-walk feasibility and responder shape; Android Compose extra-data
      support; per-element round-trip cost on both platforms
- [x] Unit 1 — the `nativeZ` field on `Element`
- [x] Unit 2 — iOS reporting (BajutsuKit responder, `xcuitest.py` read)
- [x] Unit 3 — Android reporting (extra-data helper, resident server round trip, `adb.py` read)
- [x] Unit 4 — `FakeDriver` support
- [x] Unit 5 — driver conformance suite case
- [x] Unit 6 — docs (`evidence.md`, `architecture.md`, and their `ja` mirrors)
- [x] Unit 7 — tests

### Log

- The first slice landed the Python-side foundation and left every device-dependent unit to a later
  change. `Element` gained `nativeZ` as a required field, so `mypy --strict` named all twelve
  construction sites instead of letting one slip through. Ten of the twelve hardcode `None`: the
  driver parsers, `record_capture`, and the demo scripts. That is the honest absence that a backend
  with no app-side hook owes its reader. The remaining two are the evidence readers below, which
  carry through whatever the artifact recorded.

  `FakeDriver` reports back whatever `nativeZ` a test seeds, and keeps it through the frame
  translation that its scrollable mode applies. That seam is how the deterministic gate exercises a
  `nativeZ`-aware reader with no device.

  Two readers of persisted evidence now share one coercion, `base.native_z_from_json`: the
  golden-file loader and the pick resolver of `serve`. A value that round-trips through an artifact
  keeps the meaning it had coming off the driver. The golden loader also stops requiring the field.
  Every golden recorded before this change lacks it, and a golden pins identity and state rather
  than a reading of the moment.

  The driver conformance suite pins the contract as it stands across every backend: the field is
  always present, and always `None` until an app measures one. `docs/evidence.md` and its Japanese
  mirror record what the field means and, explicitly, that no occlusion check reads it. Unit 6
  stays open for the choice of Unit 0 on what `nativeZ` means across backends. Unit 7 stays open for
  the per-backend reporting paths that Units 2 and 3 will add. The regression net that this slice
  does carry lives in `tests/test_native_z.py`. It pins both the honest absence and the untouched
  behavior of `is_tappable` and `topmost_at_point`.
- The second slice ran the spike of Unit 0 on real devices and shipped the two reporting paths that
  it scoped. *Detailed design* above records what the spike found. Three findings changed the design
  rather than merely confirming it.
  - The iOS responder is not the new BajutsuKit capability that this proposal assumed.
    `BajutsuWebView` already runs a loopback socket server inside the app under test, so the new
    responder copies that shape and adds the token check that the existing one lacks. It carries its
    own per-run secret instead of `BAJUTSU_COLLECTOR_TOKEN`, which exists only when a scenario runs a
    network collector.
  - The framework of Android does not route `addExtraDataToAccessibilityNodeInfo` through an
    accessibility delegate. The app-side helper therefore writes the position into the own extras of
    the node as the node is built, and keeps the on-demand override for a `View` subclass. The
    resident server reads what is already there, and asks when it is absent.
  - The side structure rides in a response header, like the read mark, so the XML body stays
    byte-identical to that of `uiautomator dump`. It is keyed by bounds, class, package, and
    occurrence, because the device measures in a walk whose node sequence does not line up with the
    body.

  Both declarative toolkits report nothing, for the same underlying reason on each platform. SwiftUI
  and Compose generate their own accessibility elements and expose no underlying one to measure. The
  shipped surface is therefore UIKit and Android `View`, and everything else keeps the honest
  absence.

  The Views app of the showcase opts in from the one helper that already tags a view for testing,
  which is what gives the Android path on-device coverage. The conformance contract moved with the
  code. It no longer asserts that the field is always absent. It asserts that the field is absent or
  a real finite measurement, since a backend can now be on either side of the opt-in.
- Review on the implementing PR found five more real issues. All were fixed or recorded above rather
  than deferred without notice.
  - `ZOrderResponder.positions()` latched its negative cache on a connect-phase timeout too.
    `urllib` wraps that timeout in `URLError`, rather than raising the bare `TimeoutError` that the
    read-phase clause already caught. The latch permanently lost `nativeZ` for a lease whose app was
    still coming up. The fix inspects the wrapped reason.
  - Two comments beside `Element.nativeZ` and `_to_element` in `xcuitest.py` still said that the iOS
    and Android paths were unshipped, which this PR makes false. They were corrected to name what
    actually reports.
  - The bounds key of the Android resident server used the raw `getBoundsInScreen()` rect, while the
    dumped body that it must match keys on the display-clipped rect that `AccessibilityNodeInfoDumper`
    writes. The position of any node hanging past the screen edge was dropped. The fix intersects the
    rect with the own display bounds of the device before keying.
  - The *Detailed design* prose above still named an API that does not exist. It also claimed that
    the Android walk cost "nothing beyond one cheap check". The Unit 0 findings a few lines below
    refute both. They were corrected in place (English and Japanese) and recorded as a known
    follow-up, rather than optimized under review-loop pressure.

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
