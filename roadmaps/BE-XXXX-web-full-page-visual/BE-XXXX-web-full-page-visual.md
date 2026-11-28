**English** · [日本語](BE-XXXX-web-full-page-visual-ja.md)

# BE-XXXX — Compare a whole web page in one full-page visual assertion

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-web-full-page-visual.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

This item adds a `fullPage` option to the web backend's `visual` assertion. With `fullPage: true`,
the assertion compares the whole scrollable page as one image. The image
includes everything below the first screen.

The comparison stays a deterministic pixel diff with no model in the loop. The option is a web
capability. A scenario that uses it against another backend stops at preflight, before the run.

## Motivation

The `visual` assertion (BE-0029) compares the captured screenshot against a stored baseline. On the
web backend, that screenshot covers the viewport alone. Content the user must scroll to see stays
outside the comparison. Nothing in the repository requests Playwright's `full_page` capture today.

Long pages suffer most. On a settings form, a report, or a landing page, most regressions sit below
the first screen. A broken footer passes the assertion.

An author can scroll and assert one screen at a time, but that workaround is unstable and costly. A
fixed header overlaps each shot. A scroll offset that drifts by two pixels shifts the whole image.
Every screen needs its own baseline to review and approve.

Naming the page's wrapping element in `element` does not help either. The element-scoped comparison
(BE-0171) crops the viewport screenshot. The part of the element beyond the viewport never reaches
the image.

A single full-page image removes all three problems. It also gives a run that does not pass a better
artifact. The actual image and the diff show the whole page. A reader sees where the layout broke
without paging through screens.

Take a page taller than the viewport whose footer changes. After this ships, a `visual` assertion
with `fullPage: true` fails on that page. The run's evidence then holds the full-page actual image
and its diff. The same change passes a viewport assertion today, and that gap is what this item
closes.

## Detailed design

### The scenario surface

`fullPage` is an optional boolean on `visual`, and it defaults to false. Its spelling follows the
existing camelCase options such as `colorTolerance`.

```yaml
- assert:
    - visual:
        baseline: settings-page.png
        fullPage: true
        compare: pixelmatch
        threshold: 0.2
        exclude: [ { selector: { id: last-updated-at } } ]
```

`fullPage` combines with `element`, `compare`, `threshold`, and `exclude`. A full-page baseline is a
different image from a viewport baseline. A baseline that the author recorded in one mode does not
match the other. The existing size-mismatch check reports that case. The author then records a new
baseline through the usual approve flow.

### Capture

The web driver captures the page through Playwright's `page.screenshot(full_page=True)`. A new
capability token, `fullPageScreenshot`, names this ability. The web driver advertises the token, and
no other backend does. The same pattern already keeps `selectOption` and `attribute` on the web.

The preflight (BE-0082) maps a `visual` assertion with `fullPage: true` to that token. A scenario
aimed at iOS or Android then stops before the run starts. The message names the construct and the
backends that support it.

The core reaches the capture through a narrow opt-in protocol. `ViewportProvider` in
`bajutsu/common/drivers/base/` already follows that pattern. `Driver.screenshot` keeps its signature. A
driver without the protocol raises at capture time. That error is a backstop behind the preflight.

`VisualContext.capture_actual` takes the screenshot that the `visual` assertions compare against. A scenario can hold a viewport assertion and a full-page assertion together. The context
captures one image per mode, and each assertion reads the image for its own mode.

The capture keeps the image at full size. A downscale would change which pixels `pixelmatch`
compares. The compared image keeps the browser's device pixel ratio.

### Coordinates of `exclude` and `element`

Element frames come from `getBoundingClientRect`, so each frame sits in viewport coordinates. A
full-page image needs each frame shifted by the scroll offset. The capture reads that offset together
with the frames. The resolver applies that shift first, then scales
the rectangle into image pixels.

The current scale divides the image height by the viewport height. A full-page image is taller than
the viewport, so that ratio goes wrong. Dividing by the viewport width breaks the same way on a page
that overflows horizontally. For a full-page image, the capture reads `window.devicePixelRatio`
together with the frames. That ratio is the scale on both axes.

The web query already returns elements outside the viewport. `query()` keeps off-screen Document
Object Model (DOM) nodes in the tree. Their frames stay in viewport coordinates. The `viewport()`
comment in `playwright_driver.py` records that behavior (BE-0326). A selector below the first screen
resolves without a new query.

### Boundaries

#### Lazy loading and infinite scroll

The capture never scrolls to load lazy images. Pages with infinite scroll are out of scope. The
author completes the page with an explicit wait before the assertion. A `wait` with `until:
settled` or a content condition serves that purpose. Scrolling on the author's behalf would add a hidden,
timing-dependent step. Prime directive 2 rules that step out.

#### Fixed and sticky elements

A `position: fixed` element keeps its frame while the page scrolls. A sticky element stuck to an
edge keeps its frame too. Adding the scroll offset to such a frame can aim the rectangle at the wrong
pixels. Each engine decides where it draws such an element in a full-page capture. The mapping
rests on a measurement of that choice, not a guess.

The implementation measures the placement on each engine that the web driver launches.
Those engines are Chromium, Firefox, and WebKit. A fixture page carries a fixed header, a fixed footer, and a sticky heading. The
test captures that page in full and finds where each element landed. The result is one rule per
engine, kept in code. An example rule is "draw at the frame with no scroll offset".

At capture time, the driver reads the computed `position` of each element a selector resolved. It
reads these values together with the frames. For a sticky element, the same read records whether the
element sits stuck to an edge. A fixed or stuck element then takes the engine's measured rule in
place of the scroll shift. The mapping never varies from run to run.

The fixture test stays in the suite. A Playwright upgrade that moves such an element breaks the
fixture test before it breaks a scenario. Some engine may show no single stable placement. On that
engine, a selector that resolves to such an element stops the assertion. The message names the
element. A plain `{ x, y, w, h }` rectangle still masks it on every engine.

#### Very tall pages

A browser caps the size of a single capture. A page over the cap fails the assertion with a clear
message. The assertion never compares a truncated image. The check does not rely on the browser to
raise. The expected size is the document's scroll size times the device pixel ratio,
rounded down. The check fails when either image dimension falls short of it. Rounding at a fractional ratio then never reads as truncation. The
capture reads both values with the frames. The implementation measures the exact cap.

#### Evidence policy

The per-step evidence screenshots keep the viewport size. A full-page option for them would multiply
file sizes, and no scenario needs one yet. The assertion's own actual and diff images always cover
the whole page.

#### Other backends

A native app has no scrollable page in this sense. `fullPage` stays off for iOS and Android.

### Codegen

The Playwright codegen emits a `TODO` comment for every `visual` assertion today. The implementation
confirms that `fullPage` takes the same path. Codegen never emits a viewport check in its place.

### Work breakdown (MECE)

1. **Schema and preflight.** Add `fullPage` to `VisualMatch`. Map the assertion to
   `fullPageScreenshot` in the capability preflight.
2. **Capture.** Add the opt-in full-page protocol, with the web driver as its one provider. Make
   `capture_actual` take one image per mode.
3. **Coordinate mapping.** Shift frames by the scroll offset and scale them by the device pixel
   ratio. Measure fixed and sticky placement per engine, and map such elements by the measured rule.
4. **Tests.** Cover a page taller than the viewport and an `exclude` selector below the first
   screen. Cover an `element` taller than the viewport and a page over the capture cap. Cover the
   placement fixture on each engine, and the preflight rejection on iOS and Android. Each test must break once someone
   reverts its fix.
5. **Documentation.** Update the scenario reference, `docs/architecture.md`, and their `docs/ja/`
   mirrors.

## Alternatives considered

| Alternative | Why this item does not take it |
|---|---|
| Scroll and assert one screen at a time | A fixed header and scroll-offset drift make each image unstable. The baselines multiply. |
| Name a page-wrapping element with `element` | The comparison crops the viewport screenshot, so the part beyond the viewport stays missing. |
| Switch the element capture to Playwright's element screenshot | The switch fixes one element's overflow but cannot capture the whole page. It also changes how every element-scoped baseline comes out. `fullPage: true` with `element` already covers an element taller than the viewport. |
| Add a full-page mode to per-step evidence | The files grow large, and the assertion does not need that mode. A later item can add it if a scenario needs one. |
| Fail on every fixed or stuck sticky element in `fullPage` mode | A fixed header often holds the clock an author wants to mask. A strict rule would push that author back to pixel rectangles. The strict rule stays as the fallback for an engine without a stable placement. |
| Scroll automatically to load lazy content | That scroll would be a hidden, timing-dependent step. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Schema and preflight
- [ ] Capture
- [ ] Coordinate mapping
- [ ] Tests
- [ ] Documentation

## References

- [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md): the
  `visual` assertion this item extends.
- [BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines.md): the compare engines
  that run on the full-page image.
- [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions.md):
  the element scope and selector masks whose coordinates this item maps.
- [BE-0441](../BE-0441-step-level-visual-assertions/BE-0441-step-level-visual-assertions.md): the
  step-level `visual` assertion that shares the capture path.
