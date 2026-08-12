**English** · [日本語](BE-XXXX-visualize-touches-in-app-ja.md)

# BE-XXXX — Draw a marker at each touch the app receives so recordings show where a gesture landed

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-visualize-touches-in-app.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

An iOS run records a video of the Simulator screen, and the video shows every consequence of a
gesture without ever showing the gesture. A viewer watches a screen change and has to infer, from the
change alone, which pixel was touched. This item draws a marker at each touch the app under test
actually receives, inside the app's own process, so the touch appears in the recorded video and in
the per-step screenshot as a visible mark on the frame it belongs to. We draw the marker from the
`UIEvent` the app dequeues rather than from the coordinate the driver sent, which is what makes the
marker evidence of delivery and not a redrawing of intent. The visualization is off by default, is
evidence only, and no assertion ever reads it, so the deterministic verdict is untouched.

## Motivation

Two recent items brought a run's artifacts close to answering "where did that gesture land", and
stopped one step short. [BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) records
the concrete coordinate each step actuated, and
[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) corrects every step timestamp
against the recording's confirmed start so a report's video seek lands on the right frame. Between
them a person can read a coordinate out of a table, seek the video to the matching moment, and hold
the number against the frame by eye. The arithmetic is the reader's, every time, and a coordinate in
point units means nothing against a video whose frames are in pixels until the reader has also
recovered the device's scale factor.

The deeper gap is that a coordinate record cannot answer the question a failing tap actually raises.
[BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) records what the
[driver](../../docs/glossary.md#driver-backend-actuator-platform) handed to the platform, which is
the last thing Bajutsu itself can observe. When a tap runs, reports success, and changes nothing on
screen, two explanations fit every artifact the run produces equally well: the driver aimed at a
point the intended control does not occupy, or the touch never reached the app at all. On iOS the
second explanation is not exotic. The iOS driver usually hands XCUITest a handle to a snapshotted
element and lets XCUITest choose the point, so the record carries the element's frame and no
coordinate — and a run whose app was covered by a transparent overlay, or whose window had lost
first-responder status, produces exactly the same record as a run that worked.

A marker drawn inside the app separates the two explanations, because the app draws it only from a
touch the app received. `UIWindow.sendEvent(_:)` is the single funnel every touch passes through on
its way from the system to the app's gesture recognizers and views. A marker at that point means the
touch arrived and carries the location the app itself computed; the absence of a marker under a
gesture the run recorded as actuated means the touch never arrived. Neither fact is recoverable from
the artifacts today.

## Detailed design

The design rests on one existing fact about the codebase: Bajutsu already runs Swift code inside the
app under test. `BajutsuKit` is the library target of the `BajutsuKit/` Swift package, it links UIKit
and does not link XCTest, and the demo applications declare it as a package dependency
(`demos/showcase/ios/swiftui/project.yml:15-17`), so an application that adopts the package carries
it in its own process. The separate `BajutsuRunner` target runs in the XCUITest runner process, which
is a different process from the app and can therefore draw nothing into the app's window. Everything
below lives in `BajutsuKit`.

### The hook, and why we swizzle rather than subclass

The technique this item adapts installs a `UIWindow` subclass that overrides `sendEvent(_:)` and
replaces the application's window with it. We adapt the interception point and reject the
installation method: swapping an application's window is a change to the application's own source,
and prime directive 3 keeps per-app differences out of the tool. Instead we exchange the
implementation of `-[UIWindow sendEvent:]` on the class itself with `method_exchangeImplementations`,
so no application code changes and every window in the process is covered at once.

Swizzling a framework lifecycle method is the established mechanism here rather than a new one.
`BajutsuScreen` (`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift:84-97`) already exchanges
`UIViewController.viewDidAppear(_:)` to report screen transitions, and its header records the
argument this item inherits: hooking a framework lifecycle method touches no application screen code,
so the signal stays app-agnostic. An application that subclasses `UIWindow` and overrides
`sendEvent(_:)` itself is still covered, because the override calls `super` and the exchanged
implementation sits on the superclass.

### The marker is a `CALayer`, never a `UIView`

Bajutsu resolves every [selector](../../docs/glossary.md#scenario-authoring) against the
accessibility hierarchy that `app.snapshot()` returns
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:44`), and prime directive 2 requires an
ambiguous selector to fail immediately rather than act on the first match. A visualization that adds
a node to that hierarchy would therefore not be a cosmetic addition: it would change element counts
and could turn a scenario's unique selector into an ambiguous one, in any application, at any step.
The rule the design turns on is that **the marker must be provably invisible to the accessibility
hierarchy**, and only one of the obvious renderings satisfies it.

A `CALayer` added to `window.layer` satisfies it. `CALayer` is not a `UIResponder`, conforms to no
accessibility protocol, and participates in no hit testing, so it has no representation in the
hierarchy `app.snapshot()` walks and cannot be reached by a selector. The two alternatives that look
equivalent are not, and the *Alternatives considered* section below records why. Because the marker
is a layer rather than a view, it also takes no part in touch delivery, so the visualization cannot
swallow or redirect the very gesture it draws.

### Activation

Activation is a launch environment variable, `BAJUTSU_TOUCH_MARKERS`, that the visualization requires
to be set to `1`; the hook installs on no other condition, and an application that never sees the
variable behaves exactly as it does today. `BajutsuTouch.startIfEnabled(environment:)` is called from
`BajutsuNet.startIfEnabled()`, which the demo applications already call at launch — but it is called
**before** that function's `guard collectorURL != nil || !BajutsuMocks.shared.rules.isEmpty else
{ return }` at `BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:35`, not after it alongside
`BajutsuScreen.startIfEnabled()`. The guard exists because network observation and stubbing both need
a collector or a mock rule, and touch visualization needs neither: a plain recorded run with no
network features at all is the case the feature is for.

On the Python side, `bajutsu run` gains a `--touch-markers` flag that sets the variable on the
scenario's launch environment, exactly as the existing `BAJUTSU_MOCKS` injection does at
`bajutsu/cli/commands/run.py:460`. No new transport is needed: the launch environment already travels
from `_launch_params()` (`bajutsu/platform_lifecycle/environments/xcuitest.py:1262`) through the
`BAJUTSU_LAUNCH_ENV_*` forwarding, into `RunnerServer.forwardedLaunchEnvironment`
(`BajutsuKit/Sources/BajutsuRunner/RunnerServer.swift:56`), and onto `app.launchEnvironment` before
the runner launches the app (`BajutsuKit/Runner/Sources/RunnerUITest.swift:54-57`).

### What is drawn

The hook iterates `event.allTouches` on each event and keeps one layer per `UITouch`, held in a
weak-to-strong `NSMapTable` that needs no lock because `sendEvent(_:)` runs only on the main thread. A
touch in the `.began` phase gets a translucent circle at `touch.location(in:)`; a touch in the
`.moved` phase moves the circle and extends a trailing path, so a swipe leaves its route rather than
a single point; a touch in the `.stationary` phase leaves the circle where it is, which is what makes
a long press readable without a timer; and a touch in the `.ended` or `.cancelled` phase fades its
circle out and removes it. Position updates run inside a `CATransaction` with actions disabled, so a
layer follows the finger instead of animating behind it. Because the hook reads `event.allTouches`
rather than a single touch, a pinch and a rotation each draw both contacts and both trails with no
additional code.

The marker reaches both artifacts a run already captures. The video comes from
`xcrun simctl io <udid> recordVideo` (`bajutsu/simctl.py:191`) and captures the whole Simulator
screen, so any layer the app draws appears in it. The per-step screenshot comes from
`app.screenshot()` (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:218`) and is cropped to
the application element, so a layer inside the application's frame appears there too. A screenshot is
taken after a step settles, and every gesture's touch has ended by then — a long press included,
since `press(forDuration:)` returns only after the touch lifts — so a step's screenshot shows at
most a fading marker. We document the screenshot behavior as intended rather than suppressing it,
since a marker on a screenshot is the same evidence as a marker on a frame.

### Work breakdown

The units below are mutually exclusive and collectively exhaustive.

| Unit | Work |
|---|---|
| 1 | The marker model: radius, fade duration, trail accumulation, and per-touch lifecycle, in a Foundation-only type with no UIKit import, so the Swift lane's `swift test` on a plain macOS runner covers it without a Simulator |
| 2 | The hook and the rendering: the `-[UIWindow sendEvent:]` exchange, the per-touch layer store, and the `CALayer` drawing |
| 3 | Activation: `BajutsuTouch.startIfEnabled(environment:)`, its call site above the collector guard in `BajutsuNet`, the `bajutsu run --touch-markers` flag, and the flag's Python tests |
| 4 | The accessibility-invariance gate: a golden scenario that runs with the visualization enabled and must reproduce the existing golden tree byte for byte |
| 5 | Documentation in both languages, covering the flag, the default, and the screenshot behavior |

## Alternatives considered

**Draw the marker as a `UIView` added to the window.** The source technique adds a translucent
`UIView` as a window subview, and we rejected it for the reason the *Detailed design* section builds
on: XCUITest surfaces a plain non-accessible container view as an `.other` element, so the marker
would change element counts in the tree every selector resolves against.
`accessibilityElementsHidden` does not rescue the approach, because it hides a view's descendants
rather than the view itself.

**Draw the marker in a separate `UIWindow` at a high window level.** A dedicated overlay window is
worse than a subview, not better. The root of the tree `app.snapshot()` returns is the application
element whose direct children are its windows, so an extra window adds a top-level node that every
`app.windows` query sees.

**Inject the visualization as a dynamic library, so applications that do not adopt `BajutsuKit` are
covered too.** Passing `DYLD_INSERT_LIBRARIES` through the existing launch environment would reach
any Simulator application without adopting the package, and Xcode itself injects
`libXCTestBundleInject.dylib` the same way. We deferred the approach rather than rejecting it: it
needs a separately built, ad-hoc-signed Simulator library product and a build target to produce it,
and the only application this repository's continuous integration exercises already adopts
`BajutsuKit`, so the extra machinery would ship untested. The visualization code this item adds is
self-contained and carries no dependency on the rest of `BajutsuKit`, which keeps the option open.

**Overlay the coordinates on the report's video player instead of drawing in the app.** The report
already embeds a video player, and
[BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md)'s coordinates plus
[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md)'s corrected timestamps would let
a script draw markers over the player with no Swift at all. We rejected the approach as the primary
one on the ground the *Motivation* section states: an overlay drawn from the driver's record shows
where the driver aimed, so it cannot distinguish a mis-aimed touch from an undelivered one. The
overlay is also confined to the generated report, and an exported video file — the artifact a person
attaches to a bug report — would still show nothing.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — the Foundation-only marker model and its unit tests
- [ ] Unit 2 — the `-[UIWindow sendEvent:]` exchange and the `CALayer` rendering
- [ ] Unit 3 — activation: the call site above the collector guard, the `--touch-markers` flag, and its tests
- [ ] Unit 4 — the accessibility-invariance golden scenario
- [ ] Unit 5 — bilingual documentation

## References

- [BE-0345 — Record the concrete coordinate and gesture geometry each step actually actuated](../BE-0345-actuation-record/BE-0345-actuation-record.md)
  — the driver-side coordinate record this item complements with an app-side one.
- [BE-0346 — Anchor step and network timestamps to the recording's confirmed start](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md)
  — the timing correction that makes a video frame addressable from a step.
- [`docs/evidence.md`](../../docs/evidence.md) — the evidence kinds a run captures, including the
  video this item draws into.
- [Visualizing Touches in iOS Apps](https://medium.com/@zzeynalov/visualizing-touches-in-ios-apps-90e048be54c2)
  — the source of the `UIWindow.sendEvent(_:)` interception point, whose installation method and
  rendering this item replaces.
