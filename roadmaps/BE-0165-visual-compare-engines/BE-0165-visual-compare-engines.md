**English** · [日本語](BE-0165-visual-compare-engines-ja.md)

# BE-0165 — Selectable perceptual compare engines for visual regression

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0165](BE-0165-visual-compare-engines.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0165") |
| Implementing PR | _pending_ |
| Topic | Candidates from competitive research (MagicPod / Autify) |
<!-- /BE-METADATA -->

## Introduction

The `visual` assertion ([BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md))
compares a screenshot to a baseline with a single, hard-wired comparison: an exact pixel diff. This
proposal makes the comparison engine **selectable** — keeping the current exact diff as the default,
adding a perceptual `pixelmatch` engine that tolerates sub-pixel rendering differences, and leaving a
clean seam for a future structural engine (`ssim`). Every engine is a pure, deterministic machine
check, so this stays firmly inside the Tier-2 gate.

## Motivation

Exact pixel comparison produces false positives that erode trust in the visual gate. The current
engine counts a pixel as *different* if **any** color channel differs by **any** amount
(`bajutsu/visual.py` reduces the diff to grayscale and counts every `px > 0`). The existing
`threshold` only bounds the *area* of changed pixels — it never tolerates how *much* a single pixel
changed. Two rendering realities routinely trip this:

- **Sub-pixel color variance.** Anti-aliasing and font hinting shift RGB values by 1–3 on glyph and
  icon edges. None of these are visible to a human, yet each counts as a fully "different" pixel, and
  the area piles up past the threshold.
- **One-pixel edge shifts.** Sub-pixel layout rounding moves a border or glyph edge one pixel over.
  A pure color tolerance cannot absorb this — the shifted edge is a genuinely different color at that
  coordinate — but a perceptual engine that inspects the neighborhood can recognize it as
  anti-aliasing and discount it.

Competitors (MagicPod, Autify) offer visual testing that does not fail on this noise, and the
industry-standard fix is a perceptual per-pixel comparison (the `pixelmatch` algorithm, used by
Playwright's `toHaveScreenshot` among others). Crucially, a perceptual diff is **not** a judgement
call handed to a model: given two images and a fixed engine choice, it returns the same pass/fail
every time, with no model in the loop. That is exactly what lets it live in the deterministic gate
rather than the AI paths.

The design is deliberately **selectable** rather than a wholesale replacement: the exact engine is
the strictest possible check and remains right for pixel-perfect targets, so it stays the default and
`pixelmatch` is opt-in. Making the engine a config-level and per-assertion choice also honors the
app-agnostic principle — an app whose rendering is AA-heavy sets its default engine in
`targets.<name>`, and the tool, drivers, and runner stay unchanged.

## Detailed design

A `compare` selector on the `visual` assertion picks the engine; the engine-specific tolerances live
alongside it. The default is `exact` (today's behavior, fully backward-compatible).

```yaml
expect:
  - visual:
      baseline: home.png
      compare: pixelmatch      # exact (default) | pixelmatch
      threshold: 0.1           # allowed diff-pixel AREA % (existing, unchanged meaning)
      colorTolerance: 0.1      # per-pixel perceptual color tolerance (pixelmatch; 0–1)
      antialiasing: true       # discount anti-aliased pixels from the diff (pixelmatch)
      exclude:                 # existing mask regions
        - { x: 0, y: 0, w: 390, h: 54 }
```

Work breakdown (MECE):

1. **Schema.** Add `compare: Literal["exact", "pixelmatch"]` (default `"exact"`) and the pixelmatch
   tolerances (`colorTolerance`, `antialiasing`) to `VisualMatch` in
   `bajutsu/scenario/models/assertions.py`. `threshold` keeps its current area-% meaning across all
   engines. Validate that engine-specific fields are only meaningful for their engine.
2. **Config default.** Allow a target-level default compare engine under `targets.<name>` so
   per-app rendering differences stay in config, with the per-assertion `compare` overriding it. The
   effective engine is resolved before evaluation (`bajutsu/config.py` + the resolution site).
3. **Engine seam.** Refactor `bajutsu/visual.py` so `compare_images` dispatches on the engine.
   Factor the exact path unchanged, add the `pixelmatch` engine (per-pixel YIQ perceptual color
   distance with `colorTolerance`, plus neighborhood anti-aliasing detection when `antialiasing` is
   on), and keep the seam open so a future `ssim` engine is an additive change, not a rewrite.
4. **Performance short-circuit.** Run a fast exact/byte pre-check first; if the images are identical,
   pass immediately without the perceptual pass. The verdict is always the selected engine's — this
   is a speed optimization only, and it is sound because an exact match always passes pixelmatch too
   (monotonic), so the short-circuit can never change a result.
5. **Explicit dependency errors.** If a selected engine needs a dependency that is absent (e.g. the
   perceptual math), fail the assertion with a clear message rather than silently downgrading to a
   looser engine — a silent downgrade would make the verdict depend on the environment and break
   determinism across CI and local machines.
6. **Diff output & wiring.** Keep writing the diff image; for `pixelmatch`, highlight the surviving
   (non-discounted) differing pixels so the diff reflects what actually failed. Thread the resolved
   engine + tolerances through `_eval_visual` / `VisualContext` in `bajutsu/assertions.py` and record
   the engine used in the assertion evidence.
7. **Docs & tests.** Deterministic unit tests per engine (identical / sub-pixel-noise / one-pixel
   edge-shift / over-threshold cases) using in-test generated images — no fixtures. Update
   `docs/evidence.md` (and the `docs/ja/` mirror) and the scenario schema reference.

`ssim` is intentionally **out of scope** here: this item ships `pixelmatch` as the concrete perceptual
engine and only reserves the seam for a structural engine, whose different threshold semantics
(similarity score vs. area %) deserve their own proposal.

## Alternatives considered

- **Replace exact with perceptual outright.** Rejected: exact is the strictest check and is correct
  for pixel-perfect targets. Keeping it the default preserves backward compatibility and lets teams
  opt into leniency explicitly.
- **Per-pixel color tolerance only (no anti-aliasing detection).** A smaller change, but it cannot
  absorb one-pixel edge shifts (the shifted edge is a real color difference at that coordinate).
  `pixelmatch` subsumes plain color tolerance while also handling edge shifts, so it is the better
  target; plain tolerance would just be a partial `pixelmatch`.
- **Rescue fallback (on failure, retry with a looser engine and pass if any passes).** Rejected — it
  conflicts with *determinism first*. Escalating to a looser engine on failure makes the effective
  criterion the loosest engine and silently weakens the gate; the stricter engine never contributes a
  surviving failure. The gate must fail rather than quietly relax. The only fallback we adopt is the
  performance short-circuit above, which never changes a verdict.
- **Environment-driven auto-fallback (downgrade when a dependency is missing).** Rejected: it makes
  the verdict depend on the machine, so CI and local runs could disagree. We fail explicitly instead.
- **SSIM as the default/primary engine.** Deferred: its similarity-score semantics are incompatible
  with the existing area-% `threshold`, needing its own compatibility design, and it is heavier than
  needed to fix the immediate false-positive problem. The seam here makes it a clean follow-up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Schema: `compare` selector + pixelmatch tolerances on `VisualMatch`
- [x] Config: target-level default compare engine with per-assertion override
- [x] Engine seam: dispatch in `compare_images`; add the `pixelmatch` engine
- [x] Performance short-circuit: exact pre-check before the perceptual pass
- [x] Explicit dependency errors (no silent downgrade)
- [x] Diff output + wiring through `_eval_visual` / `VisualContext` + engine recorded in evidence
- [x] Docs (`docs/evidence.md` + `docs/ja/`) & deterministic per-engine tests

## References

[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md) (the
existing `visual` assertion this extends), `bajutsu/visual.py`, `bajutsu/assertions.py`,
`bajutsu/scenario/models/assertions.py`, [DESIGN §6.4](../../../DESIGN.md),
[evidence.md](../../../docs/evidence.md), the `pixelmatch` algorithm (perceptual per-pixel diff with
anti-aliasing detection; used by Playwright's `toHaveScreenshot`)
