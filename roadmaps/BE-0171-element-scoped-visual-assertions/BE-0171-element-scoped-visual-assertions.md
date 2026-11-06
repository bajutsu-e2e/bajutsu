**English** · [日本語](BE-0171-element-scoped-visual-assertions-ja.md)

# BE-0171 — Element-scoped visual assertions and selector-based masking

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0171](BE-0171-element-scoped-visual-assertions.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0171") |
| Implementing PR | [#701](https://github.com/bajutsu-e2e/bajutsu/pull/701) |
| Topic | Verification & coverage |
| Related | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) |
<!-- /BE-METADATA -->

## Introduction

This item extends the `visual` assertion (BE-0029) in two ways. A comparison can be **scoped to a single element** instead of the whole screen. **Exclude regions can reference a selector** instead of a raw pixel rectangle.

Both stay fully deterministic. The comparison is still a pixel diff against a stored baseline, with no model in the loop.

## Motivation

BE-0029 shipped a deterministic `visual` assertion. It compares the **entire screen**, and it masks dynamic content only with hardcoded pixel rectangles (`{ x, y, w, h }`). Two limitations follow. Both are what the element-level visual testing in MagicPod and mabl exists to solve:

1. **Whole-screen comparison is brittle and imprecise.** A test that cares about one component (a chart, a button, an avatar) fails whenever *anything else* on the screen changes, such as an unrelated banner or a list that grew a row. The author cannot say "regression-test *this* card and ignore the rest". The baseline churns, and the signal is noisy. Competitors offer element-level screenshot comparison because most visual regressions are local.

2. **Pixel-rectangle masks are fragile across devices and layouts.** An `exclude` region is written as absolute coordinates. It silently drifts when the layout reflows, the device resolution changes, or a locale widens a label. The author usually wants to ignore *an element* (the clock, a "last updated" timestamp, a randomized ad slot), not a fixed box. A selector stays stable under the same conditions that break coordinates.

Both additions fit Bajutsu's prime directives without strain. Resolving an element to its bounding box, then cropping or masking before a pixel diff, is a pure, deterministic machine operation. No AI enters the `run`/CI gate, and the same screen produces the same verdict every time. The same reasoning let BE-0029 be accepted rather than shelved as "too fuzzy".

## Detailed design

The `VisualMatch` schema (`bajutsu/scenario/models/assertions.py`) gains two additions. Both are optional and backward-compatible: an existing `visual:` block keeps its current whole-screen behavior.

**1. `element:` — scope the comparison to one element's bounding box.**

```yaml
expect:
  - visual:
      element: { id: "summary-card" }   # a Selector, resolved uniquely
      baseline: summary-card.png
      threshold: 0.1
```

The selector is resolved with the existing unique-resolution rules ([DESIGN §5](../../DESIGN.md)). **An ambiguous selector fails immediately** (prime directive 2), and the assertion never crops whatever matched first. The element's frame is read from the driver's element tree. The captured screenshot is cropped to that frame, and the crop is diffed against the baseline. The baseline is therefore the *element*, not the screen, which makes it robust to unrelated changes.

**2. Selector-based masking — let an `exclude` entry name a selector instead of a rectangle.**

```yaml
expect:
  - visual:
      baseline: home.png
      exclude:
        - { selector: { label: "last updated" } }   # mask this element's frame
        - { x: 0, y: 0, w: 390, h: 54 }              # raw rectangle still allowed
```

`ExcludeRegion` becomes a union: the current `{ x, y, w, h }` rectangle **or** `{ selector: <Selector> }`. A selector mask is resolved to the element's frame at evaluation time. It is then masked exactly as a rectangle is today. A selector that matches nothing is a masking no-op, because nothing on screen needs hiding. An ambiguous selector fails, consistent with directive 2.

### Work breakdown (MECE)

- **Schema** — add `element: Selector | None` to `VisualMatch`. Turn `ExcludeRegion` into a union of the pixel rectangle and a `{ selector: Selector }` form. Keep both optional so BE-0029 scenarios are unchanged.
- **Frame resolution** — add a small helper that resolves a `Selector` to a screenshot-pixel frame via the driver's element tree. The helper reuses the existing unique-resolution path (ambiguous → fail). Element frames are in points and the screenshot is in device pixels, so the helper scales by the screenshot/point ratio (screenshot size over the point-space screen extent).
- **Comparison engine** (`bajutsu/visual.py`) — unchanged. The baseline is the *element* crop (below), so the evaluation step crops the actual to the element frame. The existing engine then compares the two element-sized images. Resolved selector masks are just more `ExcludeRegion` rectangles passed to the existing mask path. The pixel-diff core is untouched.
- **Evaluation** (`bajutsu/assertions.py`, `_eval_visual` / `VisualContext`) — resolve `element` and selector masks to pixel frames. Crop the actual to the element, writing the crop as the evidence `actual`. Translate masks into the crop's local coordinates, and compare against the element-sized baseline.
- **Evidence & report** — the `visual` evidence records whether the comparison was element-scoped (`element_scoped`) and which selectors masked (`masked_selectors`). The HTML report's baseline/actual/diff strip renders the cropped images, which are the recorded paths, plus badges for the provenance. The reviewer sees exactly what was compared.
- **Approve workflow** — `bajutsu approve` promotes element-scoped baselines the same way it promotes whole-screen ones. It copies whatever the evidence `actual` points at, which for an element-scoped check is the crop, so no special-casing is needed (confirmed).
- **Docs & schema reference** — document `element` and selector masks in the scenario schema and in `docs/` and `docs/ja/`. Add an example to the showcase visual scenario.
- **Tests** — cover element-scoped pass and fail, ambiguous-selector failure, a selector mask hiding a dynamic element, and backward compatibility of an unchanged whole-screen `visual:` block.

## Alternatives considered

- **A separate `visualElement` assertion kind.** Rejected. It would duplicate `baseline`, `threshold`, and `exclude`, and fragment the schema. An optional `element` field on the existing `visual` keeps one concept.
- **Auto-detecting the region of interest.** Rejected here. Inferring "the interesting part" is a judgement call that trends toward an LLM, and an LLM cannot enter the gate (directive 1). The author names the element explicitly, so determinism is preserved.
- **Only selector masks, not element scoping (or vice versa).** Rejected. The two share the same frame-resolution helper. They address the same brittleness from opposite ends (what to *compare* versus what to *ignore*). Shipping them together is cheaper than either alone, and it gives authors the full element-level story that competitors offer.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema — `element` field + `ExcludeRegion` / `SelectorRegion` union
- [x] Frame resolution helper (selector → pixel frame, ambiguous → fail)
- [x] Comparison engine — crop of the actual + selector-frame masking (engine signature unchanged)
- [x] Evaluation wiring (`_eval_visual` / `VisualContext`)
- [x] Evidence & HTML report (cropped strip, `element_scoped` / masked-selector provenance)
- [x] Approve workflow confirmation for element-scoped baselines
- [x] Docs & schema reference + showcase example
- [x] Tests (element pass/fail, ambiguous fail, selector mask, backward compat)

**Log**

- [#701](https://github.com/bajutsu-e2e/bajutsu/pull/701) — Ship BE-0171: `element` scoping + `SelectorRegion` masking on the `visual`
  assertion. The screenshot is cropped to the resolved element (baseline stays the element, not
  the screen; `approve` needs no special-casing); selector masks resolve to frames and combine
  with literal rectangles. Determinism preserved (ambiguous selector fails; no-match mask is a
  no-op), no LLM on the `run` path. Evidence records `element_scoped` / `masked_selectors`; the
  report strip shows both.

## References

[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md),
`bajutsu/visual.py`, `bajutsu/assertions.py`, `bajutsu/scenario/models/assertions.py`,
`bajutsu/scenario/models/selector.py`, [DESIGN §6.4](../../DESIGN.md),
[evidence.md](../../docs/evidence.md)
