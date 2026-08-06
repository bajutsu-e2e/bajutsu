**English** · [日本語](BE-XXXX-tap-target-hittability-check-ja.md)

# BE-XXXX — Verify tappability before acting, with a bounded scroll safety net

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-tap-target-hittability-check.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1524](https://github.com/bajutsu-e2e/bajutsu/pull/1524) |
| Topic | Driver & backend architecture |
| Related | [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) |
<!-- /BE-METADATA -->

## Introduction

`tap` (and `double_tap`, `long_press`, and the focus-tap inside `type`/`clear`/`select`) resolves a
selector to one element and acts on its frame's center point. Resolution never asks whether that
point is actually reachable on screen. An element can be uniquely matched, carry a valid frame, and
still sit under a sticky header, a toast, or a dimmed modal backdrop, so the tap lands on the
obstruction instead of the target. Each backend now checks, in the way most idiomatic to its own
platform, that the resolved target is really the thing at its own point before acting. When it is
not, the driver takes one bounded, deterministic scroll step to try to clear the obstruction, then
re-checks; if the target is still unreachable, the action fails with a new, dedicated error,
`ElementNotTappable`, instead of the misleading `ElementNotFound`.

## Motivation

`bajutsu/drivers/base.py`'s `Element` is a flat record — `identifier`, `label`, `traits`, `value`,
`frame` — with no `children`, `parent`, or z-index. `resolve_unique` (`base.py:647`) already guards
against *selector* ambiguity: two or more matches fail the step immediately, per prime directive 2.
It has no way to guard against *occlusion*: one match with a real frame that is nonetheless not the
front-most thing at that point. Each backend's tap path resolves a point and fires a platform
primitive without ever asking whether that point is reachable. XCUITest's `tap()`
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`) calls the native `XCUIElement.tap()`
directly. Playwright's `tap` (`bajutsu/drivers/playwright.py:566`) calls `page.mouse.click(x, y)` at
a raw coordinate, bypassing the actionability checks a locator-based click would give for free. adb's
`tap` (`bajutsu/drivers/adb.py:1097`) resolves a frame and shells out to `adb.tap_cmd`, and its only
existing recovery is "not found → scroll toward it" (`_scroll_into_view`, `adb.py:915`) — nothing
covers "found, but covered."

A scenario author has no way to see this happen. The run either does nothing, because the tap
silently lands on the obstruction, or it fails with an `ElementNotFound` that actively misleads,
since the selector did resolve.

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) already solved a related but
distinct problem — an element missing from the current viewport — with an explicit `scroll` action.
Its own "Alternatives considered" rejected making that scroll-into-view implicit inside `tap` as the
primary idiom, reasoning that implicit auto-scroll hides intent. That reasoning does not transfer
whole to this proposal. BE-0326 was rejecting implicit scrolling as a replacement for an author
writing `scroll` when the author already knows a target starts off-screen. This proposal is not a
substitute for that: an author who knows a target sits below the fold should still write `scroll`.
This proposal is a correctness check on an element the selector already resolved, with a safety-net
recovery that is deliberately narrow — a small step bound, one direction, triggered only by the new
occlusion signal — the same shape adb's own not-found fallback already has, now generalized to the
obstructed case and widened to every backend.

## Detailed design

### The tappability check, per backend

A new `Driver` protocol method, `is_tappable(self, sel: Selector) -> bool` (`base.py`, alongside
`tap` / `double_tap` / `long_press` in the `Driver` protocol at `base.py:186`–`218`), joins the
protocol as a required member, not a narrow opt-in like `ViewportProvider` /
`ReadLagProvider` / `SettledReadProvider` (`base.py:277`–`369`). It is required because the whole
point is universal coverage: every backend resolves `sel` to one element through the same
`resolve_unique` determinism core it already uses, then asks, in its own idiomatic way, whether that
element is the thing actually reachable at its own point. `is_tappable` is a pure query — it never
actuates — so the tap path can call it once to guard the actuation, and the scroll-recovery loop
(below) can call it again, repeatedly, with no side effects.

**iOS (XCUITest)** uses the native signal the platform already computes. Apple's own documentation
for [`isHittable`](https://developer.apple.com/documentation/xctest/xcuielement/1500561-ishittable)
states that the property returns `false` when the element does not exist, is offscreen, or is
covered by another element, and `true` otherwise — a native answer to reachability, not an
approximation. We confirmed this on-device rather than trusting the documentation alone: a spike
screen placed a target button under a fixed overlay inside a `ScrollView`, and five runs of an
XCUITest test on iOS Simulator all showed `isHittable` reading `false` while the target sat under
the overlay and `true` once a scroll moved the overlay clear, with the target's own frame confirming
it stayed on-screen throughout (so the transition tracked occlusion, not offscreen-ness). Community
reports (an [Apple Developer Forums thread](https://developer.apple.com/forums/thread/720155))
describe `isHittable` occasionally throwing a "Failed to determine hittability" error instead of
returning a clean boolean; the spike's 60 total reads across five runs never reproduced that
failure, which is a non-reproduction in one environment, not proof the issue does not exist
elsewhere. `XcuitestElementProvider.tap(backingElement:taps:duration:)`
(`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`) gains a guard immediately before its
native tap calls: `guard el.isHittable else { return .notHittable }`. `TapResult`
(`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift:31`) gains a `.notHittable` case alongside
its existing `.ok` / `.stale` / `.notFound`, and `xcuitest.py`'s `_actuate` gains a fourth status
constant and branch that raises `base.ElementNotTappable`, parallel to its existing stale/not-found
handling. `is_tappable(sel)` is realized as a lightweight variant of the same round trip: resolve the
handle and ask the runner for `isHittable` without actuating.

**Web (Playwright)** reuses the `document.elementFromPoint` pattern already precedented in this
codebase, inside `select_option` (`bajutsu/drivers/playwright.py:764`–`782`). It resolves the
target's center via the existing `base.frame_center(base.resolve_unique(...))`, then evaluates
JavaScript that walks `document.elementFromPoint(x, y)` up its ancestor chain and checks whether the
resolved target appears in that chain. A hit chain that never reaches the target means an unrelated
element genuinely covers the point; `is_tappable` returns `false`, and `tap` / `double_tap` /
`long_press` raise `base.ElementNotTappable` instead of calling `page.mouse.click` / `dblclick`.
Every actuator already routes its point through one seam, `_center_with_element(sel)`
(`playwright.py:531` — `tap`, `double_tap`, `long_press`, and `select_option` all call it directly;
the thin `_center(sel)` wrapper around it, `playwright.py:527`, has no caller of its own), so the
check replaces that seam with `_center_checked(sel)` rather than triplicating the logic. We
keep raw coordinate actuation and add a point-test rather than switching to Playwright's own
`locator.click()` (which carries its own actionability checks) for two reasons: `find_all` /
`resolve_unique` (`base.py:534`, `647`) are the one determinism core every backend shares, and a Cascading
Style Sheets (CSS) selector or text locator does not understand the OR'd id lists or `within`
geometric scoping that core already handles; and `locator.click()`'s auto-wait carries an implicit
timeout-and-retry shape that is not
the "resolve once, verify once" contract the rest of the driver layer holds to.

**Android (adb)** has no equivalent native signal, and the spike surfaced a real asymmetry between
its two UI toolkits rather than one clean approximation. `bajutsu/drivers/adb.py`'s
`parse_hierarchy` / `parse_hierarchy_with_identities` (`adb.py:295`–`345`) already walk the
UI Automator dump in document order with no re-sort, mirroring the same "later sibling drawn after
earlier ones" proxy XCUITest's own `flattenSnapshot`
(`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift:69`) already carries. On a spike screen with no
elevation, document order matched the visual front-to-back order on both Jetpack Compose and the
View system, confirming the ordinary case. On a spike screen using `Modifier.zIndex` in Compose to
place an element that renders on top despite being declared first, the accessibility semantics tree
itself reordered to match the visual result — the heuristic's expected blind spot did not
materialize for Compose. On the equivalent spike using `View.elevation` in the View system, the blind
spot did materialize: the elevated view rendered on top while staying earlier in document order, so
reading document order alone would misjudge which element covers the point. A further, distinct
limitation surfaced on Compose: a lightweight positioning modifier
(`Modifier.absoluteOffset`) moved a view's rendered position on screen while the accessibility tree
kept reporting its original bounds, so a purely coordinate-based check can start from an already
wrong frame, independent of document-order accuracy. Given this, `base.py` gains a new helper
alongside `_contains` (`base.py:527`) and `frame_center` (`base.py:737`) —
`topmost_at_point(elements: list[Element], point: Point, target: Element) -> Element | None` — that
scans the document-ordered list for the last element containing `point`, excluding the target's own
descendants (tapping through a child still taps the target) and its ancestors. A non-`None` result
means an unrelated element covers the point. `adb.py`'s `_resolve_frame_and_screen`
(`adb.py:892`) calls it right after resolving the frame — after any not-found `_scroll_into_view`
recovery, unchanged — raising `base.ElementNotTappable` on a match, before `adb.tap_cmd` ever fires.
The Motivation section documents both known limits plainly: the check can misjudge a View-based
layout that uses `elevation` (Compose's `zIndex` is unaffected), and a Compose layout using a
lightweight offset modifier can hand it an already-stale frame.

`_resolve_frame_and_screen` is not tap-exclusive: `adb.py:889` calls it for the tap family, and
`adb.py:1295` calls it again for the two-finger `pinch` / `rotate` gestures. Wiring the check into
this one shared seam means an occluded `pinch` / `rotate` target now raises `ElementNotTappable`
too, which is the correct outcome for a target genuinely covered by another element. Its scroll
safety net does not extend that far in this first slice, though: `_tap_with_recovery` (below) wraps
only the tap family, so a `pinch` or `rotate` on an occluded target fails immediately, with no
scroll attempt, once this check lands. Extending the recovery wrapper to the two-finger gestures is
left for a follow-up rather than this proposal's first slice.

`is_tappable(sel)`'s own Android realization stays a separate, side-effect-free read: it settles a
fresh tree, resolves `sel` with `resolve_unique`, and runs `topmost_at_point`, without going through
`_resolve_frame_and_screen`'s not-found `_scroll_into_view` fallback. Routing `is_tappable` through
that fallback would let a single query silently scroll the screen, which `scroll_until_tappable`'s
stop predicate (below) calls repeatedly and would then double-count against its own step bound —
exactly the "no side effects" property the tappability check promises on every backend. Only the
actuation-time call through `_resolve_frame_and_screen` keeps the not-found scroll fallback; the
tappability query itself never scrolls.

We considered detecting cross-window occlusion — a dialog, a toast, or a system overlay covering the
whole screen — through `AccessibilityWindowInfo.getLayer()`, an official Application Programming
Interface (API) that reports windows' relative z-order. The spike found this less useful in
practice than the API's existence suggests.
Reading it at all requires setting `FLAG_RETRIEVE_INTERACTIVE_WINDOWS` on the UiAutomation's own
`AccessibilityServiceInfo`; it is not set by default. A full-screen modal dialog, the most common
real-world case, did not produce two coexisting windows at different layers to compare — the base
window disappeared from `getWindows()` entirely and the dialog's window took its place, which means
this common case surfaces through the target selector becoming unresolvable, not through a layer
comparison. A toast did not appear in `getWindows()` at all, so this mechanism cannot detect a toast
covering a button underneath it, a gap the spike could not close within its time budget. Given the
setup cost and these two real gaps, this proposal keeps window-level occlusion detection out of its
first slice; see Alternatives considered.

### A new typed error: `ElementNotTappable`

Add this to `bajutsu/drivers/base.py` alongside `ElementNotFound` / `AmbiguousSelector`
(`base.py:421`–`427`), but not as a `SelectorError` subclass:

```python
class ElementNotTappable(Exception):
    """The selector resolved to exactly one element, but it could not be reached at its own point
    (obstructed by another on-screen element, or the platform's own hit-test refused it) — even
    after the bounded scroll safety net tried to clear the obstruction.

    Distinct from SelectorError: resolution succeeded. Only reachability failed.
    """
```

`SelectorError`'s own docstring, "selector resolution failed," would be actively wrong here — the
selector did resolve, so this is a sibling top-level exception, not a subclass. `loop.py:399`'s
generic step-execution catch, `except (base.SelectorError, base.UnsupportedAction, NotImplementedError)
as e: return False, str(e), [], None`, gains `base.ElementNotTappable` in that tuple, so a step that
raises it still fails cleanly rather than crashing the run (prime directive 1). Each backend raises
it at the point it would otherwise raise `ElementNotFound` for a genuinely missing element:
`xcuitest.py`'s `_actuate` on the new not-hittable status, `playwright.py`'s tap methods on a failed
hit-test, and `adb.py`'s `_resolve_frame_and_screen` on a non-`None` `topmost_at_point`.

### Bounded, deterministic scroll recovery as a safety net

Calling the existing `scroll_to_target(driver, sel, "down", None, max_scrolls)`
(`bajutsu/orchestrator/actions/handlers/scroll.py:442`) whenever `is_tappable` fails looks like the
obvious move, but it is a no-op bug: `scroll_to_target`'s stop condition is "`to` resolves and its
frame center sits inside the viewport" (`_center_in_viewport`, `scroll.py:123`), and an occluded
element's center is already inside the viewport — that is exactly why it is occluded rather than
off-screen. Calling it unmodified returns immediately, without a single scroll step.

The fix generalizes `scroll_to_target` at its one hard-coded stop check rather than duplicating its
several hundred lines of BE-0329 motion and end-of-content bookkeeping. Its loop body
(`scroll.py:474`–`537`) takes its stop predicate as a parameter instead of hard-coding
`_center_in_viewport`; `scroll_to_target` keeps its existing signature and behavior, supplying
`_center_in_viewport` as the default, and a new function, `scroll_until_tappable(driver, sel,
direction, within, max_scrolls)`, supplies `lambda target: driver.is_tappable(sel)` instead. Every
other line — end-of-content fail-fast, overshoot detection, the read-lag re-read budget, the
viewport contract — stays shared and unchanged.

This stop predicate must never be relaxed to a weaker signal. `scroll_until_tappable` succeeds only
when `is_tappable` itself returns `true`; a target that scrolled into the viewport, or one for which
`ElementNotFound` stopped firing, is not evidence of tappability and must never be treated as such.
Exhausting the scroll bound while `is_tappable` still reads `false` is always a failure, surfaced as
`ElementNotTappable`, regardless of which internal signal (viewport membership, end-of-content, a
bare re-query) looked satisfied along the way. Both the unit that adds `scroll_until_tappable` and
the unit that tests it state this invariant explicitly, and the test suite carries a case that
scrolls a target into the viewport while a second element still covers it, to confirm this does not
regress into a false success.

`scroll.py` already imports `_SWIPE_FRACTION` and `_scroll_gesture` from `gestures.py`. Placing the
recovery wiring in `gestures.py` (where `_do_tap` / `_do_double_tap` / `_do_long_press` already live)
while needing to call `scroll_until_tappable` from `scroll.py` would make the two modules import
each other. The fix is a small, mechanical prerequisite: extract `_SWIPE_FRACTION` and
`_scroll_gesture` into a neutral module (for example, `_gesture_math.py`) that both `gestures.py` and
`scroll.py` import from, with no behavior change. This lands before the recovery wiring, as its own
unit.

The wiring itself is a small wrapper in `gestures.py`, `_tap_with_recovery(actuate, driver, sel)`,
that calls the specific `driver.tap(sel)` / `driver.double_tap(sel)` / `driver.long_press(sel,
duration)`, and on `base.ElementNotTappable` calls `scroll_until_tappable(driver, sel, "down", None,
_TAP_RECOVERY_MAX_SCROLLS)` once, then retries the actuation exactly once. Any failure along that
path collapses to a single `base.ElementNotTappable`, chained (`raise ... from exc`) so the
underlying cause survives in the traceback. `_do_tap`, `_do_double_tap`, `_do_long_press`, and the
focus-tap call sites inside `_do_type`, `_do_clear`, `_do_delete`, and `_do_select` all switch their
bare `driver.tap(sel)` call to this one shared wrapper, rather than seven copies of the same
try/except. `_TAP_RECOVERY_MAX_SCROLLS` stays small, well under `scroll`'s own default of 15, and
the direction stays fixed at `"down"`: this is a safety net for the common case — a transient overlay,
a sticky header or footer — not a search. An author who already knows a target needs scrolling in a
specific direction, through a specific container, still writes the explicit `scroll` action; this net
insures only against the case the author did not expect.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

0. **Prerequisite refactor.** Extract `_SWIPE_FRACTION` and `_scroll_gesture` from
   [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
   into a neutral module both it and `scroll.py` import from. No behavior change; unblocks Unit 6.
1. **The `ElementNotTappable` error.** Add the class to
   [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) beside `ElementNotFound` /
   `AmbiguousSelector`. Add it to the step-execution catch tuple in
   [`bajutsu/orchestrator/loop.py:399`](../../bajutsu/orchestrator/loop.py).
2. **`Driver.is_tappable` and a pluggable scroll stop condition.** Add `is_tappable(self, sel:
   Selector) -> bool` to the `Driver` protocol in `base.py`. Refactor
   [`scroll_to_target`](../../bajutsu/orchestrator/actions/handlers/scroll.py) to take its stop
   predicate as a parameter, preserving its existing default and callers. Add
   `scroll_until_tappable`, whose stop predicate is `is_tappable` itself — never a viewport or
   existence check standing in for it.
3. **iOS hit-test.** A `TapResult.notHittable` case
   (`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`); an `isHittable` guard in
   `XcuitestElementProvider.tap` (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:57`); a
   `Router.tapResultResponse` mapping; a new status constant and branch in `xcuitest.py`'s
   `_actuate`; `is_tappable` realized as the equivalent non-actuating query.
4. **Web hit-test.** A `document.elementFromPoint`-based ancestor-chain check in
   [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py), generalizing the
   `select_option` precedent (`playwright.py:764`–`782`) into a shared `_center_checked(sel)` used
   by `tap` / `double_tap` / `long_press`.
5. **Android hit-test.** `topmost_at_point` in [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py),
   wired into `adb.py`'s `_resolve_frame_and_screen` (`adb.py:892`), after the existing not-found
   `_scroll_into_view` recovery, before the frame is returned to the actuator. This seam is shared
   with the two-finger `pinch` / `rotate` gestures (`adb.py:1295`), so an occluded target there also
   raises `ElementNotTappable`, without the scroll recovery this proposal's tap-only wrapper
   provides — note this scope boundary explicitly rather than leaving it implicit. Realize
   `is_tappable`'s Android query as its own settle-then-`resolve_unique`-then-`topmost_at_point`
   path, never through `_resolve_frame_and_screen`'s not-found `_scroll_into_view` fallback, so the
   query itself never scrolls. Document the known limits in code comments where the heuristic
   lives: correct for Compose's `zIndex`, and for the ordinary undecorated case on both toolkits;
   can misjudge a View-based layout using `elevation`; depends on accessibility bounds that a
   lightweight Compose offset modifier can leave stale.
6. **Orchestrator recovery wiring.** `_tap_with_recovery` in `gestures.py`, applied at every
   `driver.tap` / `driver.double_tap` / `driver.long_press` call site, including the focus-taps
   inside `_do_type` / `_do_clear` / `_do_delete` / `_do_select`. Depends on Units 0 and 2.
7. **`FakeDriver` support.** `FakeDriver.is_tappable`
   ([`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py)) reuses `topmost_at_point` directly
   over `self.screen`, making the generic recovery loop testable on the fast Linux gate without a
   device or emulator.
8. **Driver conformance suite.** Add an obstruction case to
   [`tests/driver_conformance.py`](../../tests/driver_conformance.py): a screen with a target and a
   second element sharing its point, placed to be "on top" in whatever way each backend's fixture
   expresses that. Assert `is_tappable` is false while covered and true once cleared; assert `tap`
   raises `ElementNotTappable` after the recovery bound is exhausted, and succeeds once the safety
   net clears the obstruction. Runs on `FakeDriver` on the fast gate and against each real backend's
   own harness, per [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)'s
   existing pattern of one shared spec across every backend.
9. **Docs.** Update the [`docs/drivers.md`](../../docs/drivers.md) note (currently describing adb's
   not-found scroll recovery as adb-only and a robustness net, not the portable idiom) to describe
   the now-portable tappability check and its scroll safety net, keeping the same framing: the
   explicit `scroll` action is still the answer when an author already knows a target starts
   off-screen; this check is a distinct, narrower correctness net for the obstructed-but-in-tree
   case. Document `ElementNotTappable` alongside `ElementNotFound` / `AmbiguousSelector` in
   `docs/selectors.md` / `docs/drivers.md` and their `docs/ja/` mirrors.
10. **Tests.** Per-backend unit tests for each hit-test mechanism in isolation (the `isHittable`
    guard and the new `TapResult` case; the `elementFromPoint` ancestor-chain check, including the
    case where the hit point lands on the target's own descendant, which is not an obstruction; the
    descendant/ancestor exclusion and the genuinely-covering case in `topmost_at_point`).
    Orchestrator-level tests over `FakeDriver` for `_tap_with_recovery`: success without recovery
    when already tappable; recovery after N scroll steps; the recovery bound exhausted, raising
    `ElementNotTappable`, never `ElementNotFound`; a target that scrolls into the viewport while
    still covered, confirming this never reads as success; the ambiguous-selector path unaffected,
    still raising `AmbiguousSelector` immediately, never reaching recovery.

### Prime directives preserved

- **AI never judges.** The tappability check and its recovery are pure geometry, a native API read,
  or a Document Object Model (DOM) query, plus a deterministic bounded loop. No model call enters
  this path. A step that
  ultimately fails still fails through the ordinary `ElementNotTappable` step-failure path, the same
  shape as any other selector or actuation failure.
- **Determinism first.** `is_tappable` is a single-shot, side-effect-free query, not a poll with
  hidden waiting. The recovery loop is `scroll_to_target`'s own already-deterministic, no-fixed-sleep,
  bounded stepping machinery, reused apart from its stop predicate — the same end-of-content
  fail-fast, the same read-lag budget, the same overshoot handling. Selector ambiguity is untouched:
  `resolve_unique` still fails immediately on two or more matches, before any tappability question is
  asked, and the recovery path is never entered for that case.
- **App-agnostic.** The check is per-platform, not per-app — the kind of difference the driver layer
  already exists to hold. The generic policy, "if not tappable, scroll a bounded amount and re-check,
  else fail with a named error," lives once in the orchestrator layer, shared by every backend, with
  no per-app branching anywhere in it.

## Alternatives considered

- **An explicit new scenario action (for example, `unobstruct`) instead of an implicit tap-time
  check.** Considered, for symmetry with `scroll`'s explicit-action shape. Rejected as the primary
  mechanism. Occlusion is not something an author can generally know in advance — a toast that
  happens to be up this run, a sticky header at a particular scroll offset — so there is no explicit
  verb an author could reach for instead; asking every author to guard every tap with a defensive
  step would be noise on the overwhelming majority of taps that are never occluded. A correctness
  check with a narrow, bounded safety net fits a condition the author cannot anticipate; an explicit
  `scroll` remains the right shape for a condition the author already knows about. The two are
  complementary, not competing.
- **A uniform `z_index` field added to `Element` for every backend.** Considered, since it would make
  occlusion a single shared geometric check instead of three backend-specific ones. Rejected,
  especially for web: DOM order is not a reliable paint-order proxy once CSS `z-index` and
  `position` are involved, so a naively derived field for the web backend would often be wrong —
  worse than no field, since it would look authoritative. Web already has a strictly better, exact
  mechanism (`document.elementFromPoint`), and iOS already has one (`isHittable`) — both native
  answers to "what is actually at this point," not proxies. Only Android genuinely benefits from a
  document-order geometric proxy, and even there the spike showed the proxy holds for Compose's
  `zIndex` but not for View `elevation`, so a single shared field would still need the same
  per-toolkit caveat this proposal states directly instead.
- **A fully rigorous same-window Android mechanism from the start** (reflecting into the app's real
  view hierarchy to read actual Z values). UiAutomator's accessibility API exposes no per-node Z
  value, so this would require in-process reflection or a new, uniform app-side test-support hook —
  a materially larger scope than this proposal's first slice. Left for a future proposal.
- **Wiring `AccessibilityWindowInfo.getLayer()` into v1 for cross-window occlusion (a dialog, a
  toast, or a system overlay covering the screen).** The spike confirmed the API works, but found it
  less useful than its existence suggests for the cases that matter most. A full-screen modal
  dialog, the most common real case, does not coexist with the base window at a different layer to
  compare — the base window disappears from `getWindows()` entirely, so this case already surfaces
  through the target selector becoming unresolvable, without needing a layer comparison. A toast
  never appears in `getWindows()` at all, so this mechanism cannot close that gap regardless of
  effort spent on it here. Combined with the setup cost (`FLAG_RETRIEVE_INTERACTIVE_WINDOWS` is not
  set by default), this proposal defers window-level occlusion detection to a future item rather than
  build a mechanism whose two most common target cases it cannot cleanly cover.
- **Skip recovery entirely; fail fast the moment occlusion is detected.** The minimal alternative,
  and the floor this proposal's other units are useful without: `ElementNotTappable` alone, with no
  scroll safety net, would already turn a silent wrong-tap or a misleading `ElementNotFound` into an
  honestly named failure. Rejected as the sole design because it does not meet the stated
  requirement to take a corrective action before failing, and because the analogous net already
  exists and is valued for the not-found case (adb's `_scroll_into_view`) — dropping it for the
  obstructed case would leave a needless asymmetry between "missing" and "covered" failures.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 0 — extract `_SWIPE_FRACTION` / `_scroll_gesture` to break the `gestures.py` /
      `scroll.py` import cycle
- [x] Unit 1 — `ElementNotTappable` error and the `loop.py` catch-tuple wiring
- [x] Unit 2 — `Driver.is_tappable`, the pluggable `scroll_to_target` stop condition, and
      `scroll_until_tappable`
- [x] Unit 3 — iOS `isHittable` hit-test
- [x] Unit 4 — web `elementFromPoint` hit-test
- [x] Unit 5 — Android `topmost_at_point` geometric hit-test
- [x] Unit 6 — orchestrator `_tap_with_recovery` wiring
- [x] Unit 7 — `FakeDriver.is_tappable`
- [x] Unit 8 — driver conformance suite obstruction case
- [x] Unit 9 — docs (`drivers.md`, `selectors.md`, and their `ja` mirrors)
- [x] Unit 10 — tests

### Log

- An empirical spike, run before this proposal's design was finalized, built minimal throwaway
  screens on iOS Simulator and an Android emulator to confirm or refute the design's core
  assumptions rather than trust documentation and research alone. On iOS, five runs of an XCUITest
  test confirmed `isHittable` reads `false` while a target sits under a fixed overlay and `true`
  once a scroll clears it, with no reproduction of a community-reported flakiness issue. On Android,
  the spike confirmed document order matches visual order in the ordinary case on both Compose and
  the View system, confirmed Compose's `zIndex` reorders the accessibility tree to match the visual
  result (no blind spot), confirmed View `elevation` does not (a real, reproducible blind spot),
  surfaced a separate bounds-staleness risk with a Compose offset modifier, and found
  `AccessibilityWindowInfo.getLayer()` real but insufficient for the two most common cross-window
  cases (a modal dialog, a toast) — leading to the decision to defer window-level occlusion
  detection (see Alternatives considered). All spike code was reverted; none of it ships as part of
  this proposal.
- All ten units above landed together on the branch that carries this proposal. `make check` is
  green; see the `Implementing PR` row above.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Element`, the `Driver` protocol,
  `resolve_unique`, `_contains`, `frame_center`, and the `SelectorError` / `ElementNotFound` /
  `AmbiguousSelector` hierarchy this proposal adds `ElementNotTappable` beside
- [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py) —
  `scroll_to_target`, the BE-0326/BE-0329 non-inertial stepping and end-of-content machinery this
  proposal generalizes rather than reimplements
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) —
  `_do_tap` / `_do_double_tap` / `_do_long_press` and the focus-tap call sites the recovery wrapper
  wires into
- [`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) — the step-execution catch
  tuple `ElementNotTappable` joins
- `bajutsu/drivers/xcuitest.py`,
  `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`,
  `BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift` / `Router.swift` — the iOS `isHittable`
  wiring
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — the `elementFromPoint`
  precedent (`select_option`, lines 764–782) this proposal generalizes
- [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — `_resolve_frame_and_screen` /
  `_scroll_into_view`, the existing not-found safety net this proposal's obstruction net sits beside
- [`tests/driver_conformance.py`](../../tests/driver_conformance.py) — the shared conformance
  contract the obstruction case is added to
- [`isHittable` — Apple Developer Documentation](https://developer.apple.com/documentation/xctest/xcuielement/1500561-ishittable) —
  confirms the property returns `false` for an offscreen or covered element
- [Apple Developer Forums thread 720155](https://developer.apple.com/forums/thread/720155) — a
  community report of `isHittable` occasionally throwing instead of returning a boolean, not
  reproduced in this proposal's spike
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the non-inertial `scroll`
  machinery this proposal reuses, and the implicit-auto-scroll precedent this proposal reconciles
  with
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the
  conformance suite the obstruction case is added to
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) — the adb
  not-found scroll fallback this proposal's obstruction net generalizes to every backend
