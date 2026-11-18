**English** · [日本語](BE-0371-visualize-touches-in-app-ja.md)

# BE-0371 — Draw a marker at each touch the app receives so recordings show where a gesture landed

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0371](BE-0371-visualize-touches-in-app.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0371") |
| Implementing PR | [#1588](https://github.com/bajutsu-e2e/bajutsu/pull/1588) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

An iOS run records a video of the Simulator screen. The video shows every consequence of a gesture
without ever showing the gesture. A viewer watches the screen change and must infer, from the change
alone, which pixel was touched.

This item draws a marker at each touch the app under test actually receives. The marker is drawn
inside the app's own process. The touch then appears in the recorded video and in the per-step
screenshot as a visible mark on the frame it belongs to.

We draw the marker from the `UIEvent` the app dequeues, not from the coordinate the driver sent.
That choice makes the marker evidence of delivery, not a redrawing of intent. The visualization is off
by default and serves as evidence alone. No assertion ever reads it, so the deterministic verdict is
untouched.

## Motivation

Two recent items brought a run's artifacts close to answering "where did that gesture land". Both
stopped one step short.

- [BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) records the concrete
  coordinate each step actuated.
- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) corrects every step
  timestamp against the confirmed start of the recording. A video seek in the report then lands on
  the right frame.

A person can now read a coordinate out of a table, seek the video to the matching moment, and hold
the number against the frame by eye. The arithmetic is the reader's, every time. A coordinate in point
units also means nothing against a video whose frames are in pixels. The reader must first recover
the scale factor of the device.

The deeper gap is that a coordinate record cannot answer the question a failing tap raises.
[BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) records what the
[driver](../../docs/glossary.md#driver-backend-actuator-platform) handed to the platform. That is the
last thing Bajutsu itself can observe.

Suppose a tap runs, reports success, and changes nothing on screen. Two explanations fit every
artifact of the run equally well. Either the driver aimed at a point that the intended control does
not occupy, or the touch never reached the app at all.

On iOS the second explanation is not exotic. The iOS driver usually hands XCUITest a handle to a
snapshotted element and lets XCUITest choose the point. The record then carries the frame of the
element and no coordinate. A run whose app was covered by a transparent overlay produces the same
record as a run that worked. So does a run whose window had lost first-responder status.

A marker drawn inside the app separates the two explanations, because the app draws it only from a
touch the app received. `UIWindow.sendEvent(_:)` is the single funnel that every touch passes
through on its way from the system to the gesture recognizers and views of the app.

- A marker at that point means the touch arrived. It also carries the location that the app itself
  computed.
- No marker under a gesture that the run recorded as actuated means the touch never arrived.

Neither fact is recoverable from the artifacts today.

## Detailed design

The design rests on one existing fact about the codebase: Bajutsu already runs Swift code inside the
app under test. `BajutsuKit` is the library target of the `BajutsuKit/` Swift package. It links UIKit
and does not link XCTest. The demo applications declare it as a package dependency
(`demos/showcase/ios/swiftui/project.yml:15-17`), so an application that adopts the package carries it
in its own process.

The separate `BajutsuRunner` target runs in the XCUITest runner process. That process differs from
the app, so it can draw nothing into the window of the app. Everything below lives in `BajutsuKit`.

### The hook, and why we swizzle rather than subclass

The technique this item adapts installs a `UIWindow` subclass that overrides `sendEvent(_:)`. It
replaces the window of the application with that subclass. We adapt the interception point and reject
the installation method. Swapping the window of an application is a change to the application's own
source, and prime directive 3 keeps per-app differences out of the tool.

Instead we exchange the implementation of `-[UIWindow sendEvent:]` on the class itself, with
`method_exchangeImplementations`. No application code changes, and every window in the process is
covered at once.

Swizzling a framework lifecycle method is an established mechanism here, not a new one.
`BajutsuScreen` (`BajutsuKit/Sources/BajutsuKit/BajutsuScreen.swift:84-97`) already exchanges
`UIViewController.viewDidAppear(_:)` to report screen transitions. Its header records the argument
this item inherits: hooking a framework lifecycle method touches no application screen code, so the
signal stays app-agnostic.

An application that subclasses `UIWindow` and overrides `sendEvent(_:)` itself is still covered. The
override calls `super`, and the exchanged implementation sits on the superclass.

### The marker is a `CALayer`, never a `UIView`

Bajutsu resolves every [selector](../../docs/glossary.md#scenario-authoring) against the
accessibility hierarchy that `app.snapshot()` returns
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:44`). Prime directive 2 requires an
ambiguous selector to fail immediately rather than act on the first match.

A visualization that adds a node to that hierarchy would not be a cosmetic addition. It would change
element counts. It could turn a unique selector of a scenario into an ambiguous one, in any
application, at any step. The design therefore turns on one rule: **the marker must be provably
invisible to the accessibility hierarchy**. Only one of the obvious renderings satisfies the rule.

A `CALayer` added to `window.layer` satisfies it. `CALayer` is not a `UIResponder`. It conforms to no
accessibility protocol and takes part in no hit testing. It has no representation in the hierarchy
that `app.snapshot()` walks, so no selector can reach it. The two alternatives that look equivalent
are not equivalent. The *Alternatives considered* section below records why.

A layer is not a view, so the marker also takes no part in touch delivery. The visualization cannot
swallow or redirect the very gesture it draws.

The touch visualization of Android shows what the choice of a layer buys. The Android lanes of the
showcase turn on the operating system settings `show_touches` and `pointer_location`
(`demos/showcase/android/Makefile:211-212`). `pointer_location` draws each contact together with its
movement trail. This item matches that behavior on iOS.

The same Android lanes turn the overlay off for every comparison lane. That covers the tree-comparing
golden lane and the pixel-comparing visual lane. An overlay drawn by a separate system window can
disturb both the pixels of a screenshot and the tree.

A `CALayer` inside the application's own window cannot disturb the tree at all. The iOS visualization
can stay on while a tree golden is asserted. The invariance check in the *Work breakdown* relies on
that property. A pixel comparison remains the one place where the visualization has to stay off.

### Activation

Activation uses a launch environment variable, `BAJUTSU_TOUCH_MARKERS`. The variable must be set to
`1`. The hook installs on no other condition. An application that never sees the variable behaves as
it does today.

`BajutsuTouch.startIfEnabled(environment:)` is called from `BajutsuNet.startIfEnabled()`, which the
demo applications already call at launch. The call comes **before** the guard in that function,
`guard collectorURL != nil || !BajutsuMocks.shared.rules.isEmpty else { return }`
(`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift:38`). It does not come after the guard, alongside
`BajutsuScreen.startIfEnabled()`.

The guard exists because network observation and stubbing both need a collector or a mock rule.
Touch visualization needs neither. A plain recorded run with no network features at all is the case
the feature is for.

On the Python side, `bajutsu run` gains a `--touch-markers` flag. The flag sets the variable on the
launch environment of the scenario, as the existing `BAJUTSU_MOCKS` injection does at
`bajutsu/cli/commands/run.py:460`. No new transport is needed. The launch environment already travels
along this path:

1. `_launch_params()` (`bajutsu/platform_lifecycle/environments/xcuitest.py:1262`)
2. the `BAJUTSU_LAUNCH_ENV_*` forwarding
3. `RunnerServer.forwardedLaunchEnvironment` (`BajutsuKit/Sources/BajutsuRunner/RunnerServer.swift:56`)
4. `app.launchEnvironment`, set before the runner launches the app
   (`BajutsuKit/Runner/Sources/RunnerUITest.swift:54-57`)

One combination turns itself off rather than degrading: a scenario whose verdict compares a
screenshot. A `visual` assertion reads the very image that the markers are drawn into. The marks
persist by design, so the baseline could never match. The flag would turn a green scenario red for a
reason that has nothing to do with the app.

`_apply_touch_markers` scans the `expect` of each scenario and the `assert` of each step. It skips the
scenarios it finds and names them on stderr. Masking is not an alternative, because the marker follows
the gesture and occupies no fixed region.

The skip is per scenario, not per run. The platform already gives that granularity. The app is
terminated and relaunched with **each scenario's own** launch env. This holds on the warm-runner path
(`_reuse_live_runner`, BE-0291) as much as on the cold one. A skipped scenario thus runs in a process
where the hook was never installed. Every other scenario in the same run still draws.

Narrowing further would need a channel into the running app. An example is drawing for the gestures
of a scenario but not for its `visual` step. The launch environment is fixed for the life of the
process, and it is the only channel BajutsuKit has. Its collector channel is outbound and
fire-and-forget. An in-app control channel is a separate item, not a detail of this one.

### What is drawn

The hook iterates `event.allTouches` on each event. It keeps a circle layer and a trail layer per
`UITouch`. A dictionary holds them, keyed by the object identity of each touch. The dictionary needs
no lock, because `sendEvent(_:)` runs on the main thread alone.

Each touch phase has its own drawing rule:

- `.began`: a translucent circle appears at `touch.location(in:)`.
- `.moved`: the circle moves and the trail extends. A swipe thus leaves its route rather than a
  single point.
- `.stationary`: the circle stays where it is. That makes a long press readable.
- `.ended` or `.cancelled`: both layers stay on screen.

The circle and the trail are green while the touch is down and red once it has lifted. A single
frame can then distinguish the gesture happening at that moment from the marks an earlier gesture
left.

Position updates run inside a `CATransaction` with actions disabled. A layer follows the finger
instead of animating behind it. The hook reads `event.allTouches` rather than a single touch, so a
pinch and a rotation each draw both contacts and both trails with no additional code.

The next gesture clears the marks of a gesture, and nothing else does. When a `.began` arrives while
no other touch is active, the hook removes the layers of the previous gesture before it draws the new
one. A `.began` that arrives while a touch is still down joins the gesture in progress and clears
nothing. That rule keeps the second finger of a pinch from erasing the first.

Two consequences follow:

- The visualization owns no timer, no fade animation, and no duration constant. It has no timing
  behavior that could differ between a fast workstation and a loaded continuous-integration runner.
- The marks of the gesture a step performed are still on screen when the step ends. That puts them in
  the screenshot of that step.

The marker thus reaches both artifacts a run already captures.

- **Video.** It comes from `xcrun simctl io <udid> recordVideo` (`bajutsu/simctl.py:191`) and
  captures the whole Simulator screen. Any layer the app draws appears in it.
- **Per-step screenshot.** It comes from `app.screenshot()`
  (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:218`) and is cropped to the application
  element. A layer inside the application frame appears there too.

A screenshot is taken after a step settles, and the marks of the gesture of that step are still there.
`after.png` thus shows where the step touched. A long press is included: its touch has lifted by
then, because `press(forDuration:)` returns after it does. We document the screenshot behavior as
intended rather than suppressing it. A marker on a screenshot is the same evidence as a marker on a
frame.

### Work breakdown

The units below are mutually exclusive and collectively exhaustive.

| Unit | Work |
|---|---|
| 1 | The marker model: radius, trail accumulation, per-touch lifecycle, and the rule that a `.began` arriving with no touch active clears the previous gesture, in a Foundation-only type with no UIKit import, so the Swift lane's `swift test` on a plain macOS runner covers it without a Simulator |
| 2 | The hook and the rendering: the `-[UIWindow sendEvent:]` exchange, the per-touch layer store, and the `CALayer` circles and trails |
| 3 | Activation: `BajutsuTouch.startIfEnabled(environment:)`, its call site above the collector guard in `BajutsuNet`, the `bajutsu run --touch-markers` flag, and the flag's Python tests |
| 4 | The accessibility-invariance check: a scenario in `demos/showcase/scenarios/golden/golden_xcuitest.yaml` that runs with the visualization enabled and asserts the same `controls.json` baseline its visualization-off twin asserts, so a visualization that perturbed the pinned controls fails it. The compare walks the baseline's own keys (`bajutsu/evidence/golden.py`), which catches a control that moved or changed rather than a node that was added; the no-added-node half rests on `CALayer` having no accessibility representation at all, and `tests/test_touch_markers.py` keeps that premise true by failing if the drawing code ever reaches for a view. It taps, since a scenario that touches nothing draws no marker and would prove nothing; that is also why it does not go in `golden.yaml`, whose one scenario only waits. The `golden` job in `.github/workflows/ios-e2e.yml` gains the file beside `golden.yaml`, both running on one warm runner, so the check is gated rather than left to a manual run |
| 5 | Documentation in both languages, covering the flag, the default, and the screenshot behavior |

## Alternatives considered

**Draw the marker as a `UIView` added to the window.** The source technique adds a translucent
`UIView` as a window subview. We rejected it for the reason that the *Detailed design* builds on.
XCUITest surfaces a plain non-accessible container view as an `.other` element. The marker would
change element counts in the tree against which every selector resolves.
`accessibilityElementsHidden` does not rescue the approach, because it hides the descendants of a
view rather than the view itself.

**Draw the marker in a separate `UIWindow` at a high window level.** A dedicated overlay window is
worse than a subview, not better. The root of the tree that `app.snapshot()` returns is the
application element, and its direct children are its windows. An extra window adds a top-level node
that every `app.windows` query sees.

**Inject the visualization as a dynamic library, so applications that do not adopt `BajutsuKit` are
covered too.** Passing `DYLD_INSERT_LIBRARIES` through the existing launch environment would reach any
Simulator application without adopting the package. Xcode itself injects
`libXCTestBundleInject.dylib` the same way.

We deferred the approach rather than rejecting it. It needs a separately built, ad-hoc-signed
Simulator library product, and a build target to produce it. Every application that the continuous
integration of this repository exercises already adopts `BajutsuKit`. The extra machinery would ship
untested. The visualization code this item adds is self-contained and carries no dependency on the
rest of `BajutsuKit`, which keeps the option open.

**Overlay the coordinates on the report's video player instead of drawing in the app.** The report
already embeds a video player. The coordinates of
[BE-0345](../BE-0345-actuation-record/BE-0345-actuation-record.md) and the corrected timestamps of
[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) would let a script draw markers
over the player with no Swift at all.

We rejected the approach as the primary one, on the ground that the *Motivation* states. An overlay
drawn from the record of the driver shows where the driver aimed. It cannot distinguish a mis-aimed
touch from an undelivered one. The overlay is also confined to the generated report. An exported video
file would still show nothing, and that file is the artifact a person attaches to a bug report.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the Foundation-only marker model and its unit tests
- [x] Unit 2 — the `-[UIWindow sendEvent:]` exchange and the `CALayer` rendering
- [x] Unit 3 — activation: the call site above the collector guard, the `--touch-markers` flag, and its tests
- [x] Unit 4 — the accessibility-invariance golden scenario
- [x] Unit 5 — bilingual documentation

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
