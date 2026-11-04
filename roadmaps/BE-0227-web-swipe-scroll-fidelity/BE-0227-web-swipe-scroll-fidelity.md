**English** · [日本語](BE-0227-web-swipe-scroll-fidelity-ja.md)

# BE-0227 — Web swipe scroll fidelity (mode-aware scroll dispatch)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0227](BE-0227-web-swipe-scroll-fidelity.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0227") |
| Implementing PR | [#948](https://github.com/bajutsu-e2e/bajutsu/pull/948) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

On the web (Playwright) [backend](../../docs/glossary.md#driver-backend-actuator-platform), a `swipe`
step does not scroll the page. The gesture is meant to scroll. The directional form
`swipe: { on, direction }` is the cross-platform way to reveal off-screen content, and the authoring
agent is told to "swipe to scroll it into view". On web, however, the step moves nothing.

This proposal makes `swipe` produce a real scroll on the web backend. It dispatches the gesture to
the input primitive that actually scrolls. The dispatch branches on the browser's input mode
(pointer or touch), so it stays correct once a mobile emulation mode exists. That mode comes from
the companion **web-device-mode-emulation** item.

## Motivation

`swipe` is one action in the scenario schema (`bajutsu/scenario/models/actions.py`), shared by every
backend. Its directional form resolves an element and computes a `(from, to)` travel via the
backend-agnostic gesture math (`bajutsu/orchestrator/actions/handlers/gestures.py`). It then calls
`driver.swipe(from, to)`.

The idb backend turns that call into `idb ui swipe … --duration 0.2`. The OS recognises it as a real
drag, so lists and scroll views scroll. The Playwright backend (`bajutsu/drivers/playwright.py`)
instead implements `swipe` as a bare **mouse drag** (`mouse.move → down → move → up`).

A mouse drag does not scroll an ordinary web page. It selects text, or drags a custom draggable
element, but the browser's scroll containers do not respond to it. The effect on web is threefold:

- **`record` produces scenarios that don't work as intended.** The agent follows its "swipe to
  scroll" instruction and emits a `swipe` to bring an off-screen control into view. The recorded
  step scrolls nothing, so the subsequent tap can land on the wrong element or fail to resolve.
- **`run` replays the same no-op.** `record` and `run` share the one `swipe` handler. A scenario that
  scrolls on iOS therefore silently fails to scroll on web. A backend that cannot perform the gesture
  undermines the determinism the tool promises.
- **`codegen` emits the same defect.** `_emit_swipe_direction` in `bajutsu/codegen_playwright.py`
  generates the identical mouse-drag sequence, so a generated Playwright test inherits the no-op.

This was verified directly. The test drove `PlaywrightDriver.swipe()` with the exact `(from, to)` the
orchestrator computes, against a 3000px-tall page in a real headless Chromium. `window.scrollY`
stayed at `0`. An equivalent `page.mouse.wheel()` of the same magnitude scrolled the page as
expected.

The gap matters because scrolling is table stakes for any non-trivial page, such as a long form, a
feed, or a settings list. Today, web scenarios that need to reach below the fold are quietly broken.

## Detailed design

The fix dispatches `swipe` on the web backend to an input primitive that scrolls. The dispatch is
**mode-aware**, so it is correct for both a desktop pointer browser and a touch (mobile) browser. The
correct primitive differs by mode:

- **Desktop / pointer mode** — `page.mouse.wheel(delta_x, delta_y)`. Wheel events drive the browser's
  real scroll containers (verified above), which matches how a user scrolls with a wheel or
  trackpad. The `swipe` direction maps to a wheel delta. An `up` swipe means content travels up, so
  the user pushes the surface up. That scrolls the page **down** (positive `delta_y`), mirroring the
  physical gesture. The `amount` fraction sets the magnitude from the same screen-relative distance
  the gesture math already computes.
- **Touch / mobile mode** — a Chrome DevTools Protocol (CDP) touch drag, the exact path `pinch` and
  `rotate` already use (`_touch_drag` → `Input.dispatchTouchEvent`). A real touch drag is what
  scrolls a touch page. Unlike a synthetic DOM event, it fires the page's touch and gesture
  listeners. `swipe` would reuse `_touch_drag` with the two endpoints collapsed to a single finger.

**Selecting the mode.** The branch keys off whether the active browser context has touch input
enabled, which is the `has_touch` value the context was created with. Playwright's Python
`BrowserContext` has no public getter for it, because it is a `new_context()` creation-time option
only. The driver therefore records the touch mode at context-creation time (alongside
`self._context`) rather than reading it back from the context object.

`self._context` is created from two independent sites:

- the `_start_browser` starter closure (the initial launch *and* `relaunch()`)
- `_new_context()` (`reset_context()` and the video-recording swap)

The companion item must therefore set `has_touch` and refresh the cached touch mode at **both**. A
`relaunch()` goes through the starter, not `_new_context()`. Refreshing only one site could leave the
cached mode stale relative to the freshly created context.

Today the web backend always launches a plain desktop context (`has_touch` is false). This proposal
ships the **desktop / wheel** path as the live behaviour. It wires the branch so the touch path
activates automatically once a context can be configured for touch.

Standing up that configuration is the companion **web-device-mode-emulation** item's job: a
desktop/mobile device mode with viewport, `is_mobile`, and `has_touch`. This item does not introduce
that mode. It leaves the dispatch ready for it, so the two compose without a later rewrite.

**Coordinate form is unchanged.** `swipe: { from, to }` stays the raw-drag last resort it is
documented to be (`docs/scenarios.md`). It serves custom canvases, map pans, and drag handles, where
a literal pointer drag *is* the intent. Other backends give the coordinate form the same position (cf.
[BE-0025](../BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md)). Only the
directional form `{ on, direction }` changes, because its meaning is "scroll".

**New `drag` action for element-anchored pointer drags.** Redefining the directional `swipe` as a
scroll displaced a real, shipped use: grabbing an element *by selector* and dragging it in a
direction, such as a resize divider or a slider thumb. On web, that use relied on the old
directional-swipe mouse drag. The serve-UI `panel-resize` dogfood does exactly this.

The coordinate form cannot serve it, because it takes raw pixels and does no selector resolution.
Implementation therefore added a first-class `drag: { on, direction, amount? }` action. It shares
`swipe`'s directional endpoint math. It routes to `driver.swipe` (a genuine pointer drag) rather than
`driver.scroll`. On web, that is a `page.mouse` drag that moves the grabbed element. On iOS and
Android, a real OS drag already both scrolls and moves handles, so `drag` and directional `swipe`
coincide there.

codegen emits `drag` on every backend. On web it emits a `page.mouse` drag. On iOS and Android it
emits the `swipeX()` / `swipe(Direction, …)` that a directional swipe emits.

**codegen parity.** `_emit_swipe_direction` in `bajutsu/codegen_playwright.py` is updated to emit the
matching wheel scroll (for example `page.mouse.wheel(...)`) for the directional form. A generated
Playwright test then scrolls exactly as `run` does. This keeps the structural `scenario → test`
mapping faithful, the same principle BE-0025 and BE-0085 hold for the other emitters.

Work breakdown (MECE):

1. **Mode-aware dispatch in `PlaywrightDriver.swipe`.** Branch on the context's touch mode: desktop
   → `mouse.wheel`; touch → `_touch_drag` (single finger). Desktop is the live path today.
2. **Direction → wheel-delta mapping.** Translate the gesture math's `(from, to)`, direction, and
   `amount` into a wheel `(delta_x, delta_y)` that scrolls in the physically correct direction and
   distance.
3. **codegen parity.** Update `_emit_swipe_direction` in `bajutsu/codegen_playwright.py` to emit the
   wheel scroll for the directional form. The coordinate form keeps its drag.
4. **Tests.** Replace the mouse-call-sequence assertion in `tests/test_playwright.py::test_swipe` with
   one that asserts a real scroll effect (a change in `window.scrollY` or the target's
   `scrollTop`). Cover the direction → delta sign mapping. Add or adjust the codegen test in
   `tests/test_codegen_playwright.py`.
5. **Docs.** Note in `docs/scenarios.md` (and the ja mirror) and `docs/drivers.md` how the web backend
   realises a directional `swipe`: a wheel scroll on desktop, and a touch drag under a touch context.
   Keep the coordinate-form wording intact.

This stays within the prime directives:

- **Determinism.** The dispatch is a fixed mapping from the resolved gesture to a wheel or touch
  input. No model is consulted, and the run/CI gate is untouched. A wheel scroll is as reproducible
  as the mouse drag it replaces.
- **App-agnostic.** There is no per-app configuration, and the mapping is identical across every web
  target. The desktop/mobile *mode* is a target-level browser setting (the companion item), not
  per-app gesture logic.
- **Backend-agnostic core.** The scenario schema and the shared gesture math are unchanged. Only the
  web driver's realisation of the gesture and its codegen emitter change. That is exactly the
  per-backend seam the architecture reserves for this.

## Alternatives considered

- **Always use a CDP touch drag (drop the mode branch).** A single touch-drag path would fire the
  page's touch handlers. On a desktop pointer context, though, a synthesised touch drag does not map
  to the wheel-scroll behaviour a desktop page expects (momentum, overscroll, wheel-only listeners).
  It also misrepresents the emulated input as touch on a device the target treats as desktop.
  Rejected: the input mode genuinely differs between desktop and mobile, which is exactly why the
  branch exists.
- **`element.scrollIntoView()` / `scrollBy()` via `page.evaluate`.** Scrolling by injected JS is
  reliable but bypasses the browser's real input path, so the page's scroll, wheel, and touch
  listeners never fire. The driver already records the same objection against synthetic DOM events
  for `pinch` and `rotate`. It would also diverge from how a user actually scrolls, which is what a
  faithful gesture should reproduce. Rejected: it trades fidelity for convenience.
- **Keep the mouse drag and document that web `swipe` doesn't scroll.** Rejected. It leaves a
  cross-platform action silently broken on a landed backend, and `record` would keep emitting
  scenarios that don't do what they say.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Mode-aware dispatch — a dedicated `Driver.scroll` seam: the directional swipe handler routes
      to `driver.scroll` (coordinate form keeps `driver.swipe`), so the web backend realizes a scroll
      as a desktop wheel / touch drag while the coordinate raw-drag is untouched. Other backends
      delegate `scroll` → `swipe` (a real drag already scrolls).
- [x] Direction → wheel-delta mapping (the wheel delta is the reverse of the gesture travel,
      `frm - to`, so an `up` swipe scrolls the page down)
- [x] codegen parity (`_emit_swipe_direction` emits `page.mouse.wheel(...)`; coordinate form keeps
      its TODO)
- [x] New `drag` action (element-anchored pointer drag) so the use directional-swipe displaced —
      grabbing a handle by selector and dragging it — stays expressible; schema + handler + codegen
      on all three backends; the serve-UI `panel-resize` dogfood migrated `swipe` → `drag`
- [x] Tests (Playwright desktop-wheel + direction-sign + touch-drag; delegation on idb/adb/xcuitest;
      handler routing via `test_gestures.py`; codegen wheel assertion; `drag` schema/handler/codegen)
- [x] Docs (`scenarios.md` / `drivers.md` / `dsl-grammar.md`, both languages)

**Log**

- [#948](https://github.com/bajutsu-e2e/bajutsu/pull/948) — implement mode-aware scroll dispatch via a `Driver.scroll` seam; codegen wheel
  parity; add a `drag` action for element-anchored pointer drags (the use directional-swipe
  displaced); tests + bilingual docs.

## References

- [scenarios.md — `swipe`](../../docs/scenarios.md)
- [drivers.md](../../docs/drivers.md)
- [BE-0025 — Coordinate swipe generation](../BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md)
- Companion item: **web-device-mode-emulation** (introduces the desktop/mobile browser mode this
  dispatch branches on)
