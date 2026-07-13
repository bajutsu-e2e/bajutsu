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
| Topic | Platform support (iOS / Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

On the web (Playwright) backend, a `swipe` step does not scroll the page. The gesture is meant
to scroll — the directional form `swipe: { on, direction }` is the cross-platform way to reveal
off-screen content, and the authoring agent is told to "swipe to scroll it into view" — but on
web the step moves nothing. This proposal makes `swipe` produce a real scroll on the web backend
by dispatching the gesture to the input primitive that actually scrolls, and structures that
dispatch to branch on the browser's input mode (pointer vs. touch) so it stays correct once a
mobile emulation mode exists (the companion **web-device-mode-emulation** item).

## Motivation

`swipe` is one action in the scenario schema (`bajutsu/scenario/models/actions.py`), shared by
every backend. Its directional form resolves an element, computes a `(from, to)` travel via the
backend-agnostic gesture math (`bajutsu/orchestrator/actions/handlers/gestures.py`), and calls
`driver.swipe(from, to)`. The idb backend turns that into `idb ui swipe … --duration 0.2`, which
the OS recognises as a real drag, so lists and scroll views scroll. The Playwright backend
(`bajutsu/drivers/playwright.py`) instead implements `swipe` as a bare **mouse drag**
(`mouse.move → down → move → up`).

A mouse drag does not scroll an ordinary web page. It selects text, or drags a custom draggable
element, but the browser's scroll containers do not respond to it. The effect is that on web:

- **`record` produces scenarios that don't work as intended.** The agent, following its "swipe to
  scroll" instruction, emits a `swipe` to bring an off-screen control into view; the recorded
  step scrolls nothing, so the subsequent tap can land on the wrong element or fail to resolve.
- **`run` replays the same no-op.** Because `record` and `run` share the one `swipe` handler, a
  scenario that scrolls on iOS silently fails to scroll on web — the determinism the tool
  promises is undermined by a backend that can't perform the gesture.
- **`codegen` emits the same defect.** `bajutsu/codegen_playwright.py` `_emit_swipe_direction`
  generates the identical mouse-drag sequence, so a generated Playwright test inherits the
  no-op.

This was verified directly: driving `PlaywrightDriver.swipe()` with the exact `(from, to)` the
orchestrator computes, against a 3000px-tall page in a real headless Chromium, left
`window.scrollY` at `0`, while an equivalent `page.mouse.wheel()` of the same magnitude scrolled
the page as expected.

The gap matters because scrolling is table stakes for any non-trivial page — a long form, a feed,
a settings list. Today web scenarios that need to reach below the fold are quietly broken.

## Detailed design

The fix is to dispatch `swipe` on the web backend to an input primitive that scrolls, and to make
that dispatch **mode-aware** so it is correct for both a desktop pointer browser and a touch
(mobile) browser. The correct primitive differs by mode:

- **Desktop / pointer mode** — `page.mouse.wheel(delta_x, delta_y)`. Wheel events drive the
  browser's real scroll containers (verified above), matching how a user scrolls with a wheel or
  trackpad. The `swipe` direction maps to a wheel delta: an `up` swipe (content travels up, i.e.
  the user pushes the surface up) scrolls the page **down** (positive `delta_y`), mirroring the
  physical gesture, and the `amount` fraction sets the magnitude from the same screen-relative
  distance the gesture math already computes.
- **Touch / mobile mode** — a CDP (Chrome DevTools Protocol) touch drag, the exact path `pinch` / `rotate` already use
  (`_touch_drag` → `Input.dispatchTouchEvent`). A real touch drag is what scrolls a touch page,
  and it fires the page's touch / gesture listeners, unlike a synthetic DOM event. `swipe` would
  reuse `_touch_drag` with the two endpoints collapsed to a single finger.

**Selecting the mode.** The branch keys off whether the active browser context has touch input
enabled — the `has_touch` value the context was created with. Playwright's Python `BrowserContext`
has no public getter for it (it is a `new_context()` creation-time option only), so the driver
records the touch mode at context-creation time (alongside `self._context`) rather than reading it
back from the context object. Note that `self._context` is created from two independent sites — the
`_start_browser` starter closure (the initial launch *and* `relaunch()`) and `_new_context()`
(`reset_context()` and the video-recording swap) — so the companion item must set `has_touch` and
refresh the cached touch mode at **both**, or a `relaunch()` (which goes through the starter, not
`_new_context()`) could leave the cached mode stale relative to the freshly created context. Today the web
backend always launches a plain desktop context (`has_touch` is false), so this proposal ships
the **desktop / wheel** path as the live behaviour and wires the branch so the touch path
activates automatically once a context can be configured for touch. Standing up that
configuration — a desktop/mobile device mode with viewport, `is_mobile`, and `has_touch` — is the
companion **web-device-mode-emulation** item; this item does not introduce it, but leaves the
dispatch ready for it so the two compose without a later rewrite.

**Coordinate form is unchanged.** `swipe: { from, to }` stays the raw-drag last resort it is
documented to be (`docs/scenarios.md`), for custom canvases / map pans / drag handles where a
literal pointer drag *is* the intent — the same position the coordinate form holds on other
backends (cf. [BE-0025](../BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md)).
Only the directional form `{ on, direction }` — the one whose meaning is "scroll" — changes.

**New `drag` action for element-anchored pointer drags.** Redefining the directional `swipe` as a
scroll displaced a real, shipped use: grabbing an element *by selector* and dragging it in a
direction — a resize divider, a slider thumb — which on web relied on the old directional-swipe
mouse drag (the serve-UI `panel-resize` dogfood does exactly this). The coordinate form can't serve
it (it takes raw pixels, no selector resolution), so implementation added a first-class `drag: { on,
direction, amount? }` action: it shares `swipe`'s directional endpoint math but routes to
`driver.swipe` (a genuine pointer drag) rather than `driver.scroll`. On web that is a `page.mouse`
drag that moves the grabbed element; on iOS / Android a real OS drag already both scrolls and moves
handles, so `drag` and directional `swipe` coincide there. codegen emits it on every backend (a
`page.mouse` drag on web; the `swipeX()` / `swipe(Direction, …)` a directional swipe emits on
iOS / Android).

**codegen parity.** `bajutsu/codegen_playwright.py` `_emit_swipe_direction` is updated to emit the
matching wheel scroll (e.g. `page.mouse.wheel(...)`) for the directional form, so a generated
Playwright test scrolls exactly as `run` does. This keeps the structural `scenario → test`
mapping faithful, the same principle BE-0025 / BE-0085 hold for the other emitters.

Work breakdown (MECE):

1. **Mode-aware dispatch in `PlaywrightDriver.swipe`** — branch on the context's touch mode:
   desktop → `mouse.wheel`; touch → `_touch_drag` (single finger). Desktop is the live path today.
2. **Direction → wheel-delta mapping** — translate the gesture math's `(from, to)` / direction /
   `amount` into a wheel `(delta_x, delta_y)` that scrolls in the physically correct direction and
   distance.
3. **codegen parity** — update `_emit_swipe_direction` in `bajutsu/codegen_playwright.py` to emit
   the wheel scroll for the directional form; coordinate form keeps its drag.
4. **Tests** — replace the mouse-call-sequence assertion in `tests/test_playwright.py::test_swipe`
   with one that asserts a real scroll effect (a change in `window.scrollY` / the target's
   `scrollTop`), and cover the direction → delta sign mapping; add/adjust the codegen test in
   `tests/test_codegen_playwright.py`.
5. **Docs** — note in `docs/scenarios.md` (and the ja mirror) and `docs/drivers.md` how the web
   backend realises a directional `swipe` (wheel scroll on desktop; touch drag under a touch
   context), keeping the coordinate-form wording intact.

This stays within the prime directives:

- **Determinism.** The dispatch is a fixed mapping from the resolved gesture to a wheel/touch
  input — no model is consulted, and the run/CI gate is untouched. A wheel scroll is as
  reproducible as the mouse drag it replaces.
- **App-agnostic.** No per-app configuration; the mapping is identical across every web target.
  The desktop/mobile *mode* is a target-level browser setting (the companion item), not
  per-app gesture logic.
- **Backend-agnostic core.** The scenario schema and the shared gesture math are unchanged; only
  the web driver's realisation of the gesture, and its codegen emitter, change — exactly the
  per-backend seam the architecture reserves for this.

## Alternatives considered

- **Always use a CDP touch drag (drop the mode branch).** A single touch-drag path would fire the
  page's touch handlers, but on a desktop pointer context a synthesised touch drag does not map to
  the wheel-scroll behaviour a desktop page expects (momentum, overscroll, wheel-only listeners),
  and it misrepresents the emulated input as touch on a device the target treats as desktop.
  Rejected: the input mode genuinely differs between desktop and mobile, which is exactly why the
  branch exists.
- **`element.scrollIntoView()` / `scrollBy()` via `page.evaluate`.** Scrolling by injected JS is
  reliable but bypasses the browser's real input path, so the page's scroll / wheel / touch
  listeners never fire — the same objection the driver already records against synthetic DOM
  events for `pinch` / `rotate`. It would also diverge from how a user actually scrolls, which is
  what a faithful gesture should reproduce. Rejected: it trades fidelity for convenience.
- **Keep the mouse drag and document that web `swipe` doesn't scroll.** Rejected: it leaves a
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
