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
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Related | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) |
<!-- /BE-METADATA -->

## Introduction

Extend the `visual` assertion (BE-0029) so a comparison can be **scoped to a single element**
instead of the whole screen, and so **exclude regions can reference a selector** instead of a raw
pixel rectangle. Both stay fully deterministic — the comparison is still a pixel diff against a
stored baseline, with no model in the loop.

## Motivation

BE-0029 shipped a deterministic `visual` assertion, but it compares the **entire screen** and masks
dynamic content only with hardcoded pixel rectangles (`{ x, y, w, h }`). Two limitations follow, and
both are exactly what the element-level visual testing in MagicPod and mabl exists to solve:

1. **Whole-screen comparison is brittle and imprecise.** A test that only cares about one
   component — a chart, a button, an avatar — fails whenever *anything else* on the screen changes
   (an unrelated banner, a list that grew a row). The author cannot say "regression-test *this*
   card and ignore the rest", so the baseline churns and the signal is noisy. Competitors offer
   element-level screenshot comparison precisely because most visual regressions are local.

2. **Pixel-rectangle masks are fragile across devices and layouts.** An `exclude` region is written
   as absolute coordinates, so it silently drifts the moment the layout reflows, the device
   resolution changes, or a locale widens a label. The thing an author actually wants to ignore is
   usually *an element* (the clock, a "last updated" timestamp, a randomized ad slot) — not a fixed
   box. Addressing it by selector is stable across the same conditions that break coordinates.

Both fit Bajutsu's prime directives without strain: resolving an element to its bounding box and
cropping/masking before a pixel diff is a pure, deterministic machine operation. No AI enters the
`run`/CI gate; the same screen produces the same verdict every time. This is the same reasoning
that let BE-0029 be accepted rather than shelved as "too fuzzy".

## Detailed design

Two additions to the `VisualMatch` schema (`bajutsu/scenario/models/assertions.py`), both optional
and backward-compatible — an existing `visual:` block keeps its current whole-screen behavior.

**1. `element:` — scope the comparison to one element's bounding box.**

```yaml
expect:
  - visual:
      element: { id: "summary-card" }   # a Selector, resolved uniquely
      baseline: summary-card.png
      threshold: 0.1
```

The selector is resolved with the existing unique-resolution rules ([DESIGN §5](../../DESIGN.md)) —
**an ambiguous selector fails immediately** (prime directive 2), never "crop whatever matched first". The element's frame is read
from the driver's element tree, the captured screenshot is cropped to that frame, and the crop is
diffed against the baseline. The baseline is therefore the *element*, not the screen, which is what
makes it robust to unrelated changes.

**2. Selector-based masking — let an `exclude` entry name a selector instead of a rectangle.**

```yaml
expect:
  - visual:
      baseline: home.png
      exclude:
        - { selector: { label: "last updated" } }   # mask this element's frame
        - { x: 0, y: 0, w: 390, h: 54 }              # raw rectangle still allowed
```

`ExcludeRegion` becomes a union: the current `{ x, y, w, h }` rectangle **or** `{ selector: <Selector> }`.
A selector mask is resolved to the element's frame at evaluation time and masked exactly as a
rectangle is today. A selector that matches nothing is a masking no-op (there is nothing on screen to
hide); an ambiguous one fails, consistent with directive 2.

### Work breakdown (MECE)

- **Schema** — add `element: Selector | None` to `VisualMatch`; turn `ExcludeRegion` into a union of
  the pixel rectangle and a `{ selector: Selector }` form. Keep both optional so BE-0029 scenarios
  are unchanged.
- **Frame resolution** — a small helper that resolves a `Selector` to a screenshot-pixel frame via
  the driver's element tree, reusing the existing unique-resolution path (ambiguous → fail). Element
  frames are in points; the screenshot is in device pixels, so the helper scales by the
  screenshot/point ratio (screenshot size over the point-space screen extent).
- **Comparison engine** (`bajutsu/visual.py`) — unchanged. Because the baseline is the *element*
  crop (below), the actual is cropped to the element frame in the evaluation step and the existing
  engine compares the two element-sized images; resolved selector masks are just more
  `ExcludeRegion` rectangles passed to the existing mask path. The pixel-diff core is untouched.
- **Evaluation** (`bajutsu/assertions.py`, `_eval_visual` / `VisualContext`) — resolve `element` and
  selector masks to pixel frames, crop the actual to the element (writing the crop as the evidence
  `actual`), translate masks into the crop's local coordinates, and compare against the
  element-sized baseline.
- **Evidence & report** — the `visual` evidence records whether the comparison was element-scoped
  (`element_scoped`) and which selector(s) masked (`masked_selectors`); the HTML report's
  baseline/actual/diff strip renders the cropped images (they *are* the recorded paths) plus badges
  for the provenance, so the reviewer sees exactly what was compared.
- **Approve workflow** — `bajutsu approve` promotes element-scoped baselines the same way it does
  whole-screen ones: it copies whatever the evidence `actual` points at, which for an element-scoped
  check is the crop, so no special-casing is needed (confirmed).
- **Docs & schema reference** — document `element` and selector masks in the scenario schema and
  `docs/` / `docs/ja/`; add an example to the showcase visual scenario.
- **Tests** — element-scoped pass/fail, ambiguous-selector failure, selector mask hiding a dynamic
  element, and backward-compatibility of an unchanged whole-screen `visual:` block.

## Alternatives considered

- **A separate `visualElement` assertion kind.** Rejected — it would duplicate `baseline` /
  `threshold` / `exclude` and fragment the schema. An optional `element` field on the existing
  `visual` keeps one concept.
- **Auto-detecting the region of interest.** Rejected here — inferring "the interesting part" is a
  judgement call that trends toward an LLM, which cannot enter the gate (directive 1). The author
  names the element explicitly; determinism is preserved.
- **Only selector masks, not element scoping (or vice versa).** They share the same frame-resolution
  helper and address the same brittleness from opposite ends (what to *compare* vs what to
  *ignore*), so shipping them together is cheaper than either alone and gives authors the full
  element-level story competitors offer.

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
