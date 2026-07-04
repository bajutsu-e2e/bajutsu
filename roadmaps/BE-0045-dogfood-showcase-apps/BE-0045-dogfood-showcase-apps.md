**English** · [日本語](BE-0045-dogfood-showcase-apps-ja.md)

# BE-0045 — Dogfood showcase apps (UIKit × SwiftUI, accessibility-paired)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0045](BE-0045-dogfood-showcase-apps.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0045") |
| Implementing PR | [#85](https://github.com/bajutsu-e2e/bajutsu/pull/85) |
| Topic | Dogfood fixtures (demo apps) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

A purpose-built fixture suite that becomes Bajutsu's primary dogfood target — the practice
ground where `record` (Tier 1 authoring), `crawl` (Tier 1 exploration,
[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
and `run` (Tier 2 deterministic gate) are all exercised against one realistic app. The suite
ships **the same app written twice** — once in UIKit, once in SwiftUI — and **each in two
accessibility variants** (identifiers on / off), for four installable products from two
codebases. The full screen-by-screen contract lives in [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)
([ja](../../../demos/showcase/SPEC.ja.md)); this item records the rationale and scope.

It supersedes the older single-app `sample` fixture ([`demos/features/app`](../../../demos/features/app)):
`sample` proves the feature surface on one SwiftUI app, but it cannot demonstrate the toolkit
axis (UIKit vs SwiftUI element-tree differences) or the accessibility axis (what `record` and
`doctor` do when identifiers are absent), which are exactly the dimensions a dogfood fixture
should stress.

## Motivation

**1. A fixture rich enough to exercise every command at once.** Today the story is split
across three apps (`demo`, `sample`, `sample2`). The showcase packs the full interaction
surface — five tabs, navigation-stack pushes, all four modal styles (detented sheet,
full-screen cover, action sheet, transient toast), text entry, async loading, networking
(live + mockable via BajutsuKit), and a tab that deliberately raises OS-level alerts — into
one coherent app. A genuinely branchy app (5 tabs × pushes × 4 modal styles) is also what makes
`crawl`'s breadth-first traversal meaningful: there is a real graph to map.

**2. The accessibility pairing is a controlled experiment, not just a fixture.** Selector
stability is *the* determinism lever ([DESIGN §2](../../../DESIGN.md)): id-based selectors
resolve uniquely and survive layout/locale changes, while coordinate/label fallbacks are
fragile ([DESIGN §5](../../../DESIGN.md) stability ladder). The `-a11y` ↔ `-noax` twins make
that abstract claim concrete and measurable — same app, same flows, identifiers the only
difference:

- Against the **`-a11y`** build, `run` replays every scenario deterministically and
  `doctor --app` grades **Ready**.
- Against the **`-noax`** build (no identifiers at all), `record` must walk *down* the stability
  ladder to `label`/`traits`/coordinates, and `doctor --app` grades **Blocked**
  (`idCoverage` ≈ 0). The `-noax` app declares `idNamespaces: []` — an honest statement that it
  exposes nothing, rather than appearing to pass.

Run the same natural-language goal through `record` against both twins and the diff *is* the
value of accessibility work — a demo no single-variant fixture can give.

**3. The toolkit axis catches element-tree differences early.** idb's element-tree
normalization is a known risk area, especially for SwiftUI standard controls
([DESIGN §11](../../../DESIGN.md); [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md)).
A UIKit twin that exposes the *same* identifier contract as the SwiftUI twin lets us run the
*same* scenario set across both and surface where the two toolkits' a11y trees diverge — a
regression net for the driver, free, as a byproduct of the fixture existing.

**4. A stable base for practising future features.** Because per-app differences live entirely
in config ([DESIGN §8](../../../DESIGN.md)), the showcase is the natural target to try new
capabilities against without inventing a throwaway app each time: visual-regression baselines
([BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions.md)),
data-driven runs, secret redaction, the crawl screen-map, and `doctor`'s whole-app coverage
([BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)) all have a ready,
representative subject.

This respects every prime directive ([CLAUDE.md](../../../CLAUDE.md)): it is purely a test
*subject* plus config and scenarios, adds **no** LLM call to any gate, and changes **nothing**
in the tool, drivers, or runner — the whole suite is onboarded through `apps.<name>` entries
exactly as [DESIGN §7.1](../../../DESIGN.md) prescribes.

## Detailed design

The authoritative, screen-by-screen contract — identifier map, launch-env hooks, deeplinks,
OS-alert placement, and the `ACCESSIBLE` build flag — is [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md).
The shape:

- **Layout** — `demos/showcase/{swiftui,uikit}/` hold the two codebases (xcodegen
  `project.yml` with **two targets sharing one `Sources/`**, the variant difference being a
  single `SWIFT_ACTIVE_COMPILATION_CONDITIONS = ACCESSIBLE` on the a11y target). Shared assets
  — `showcase.config.yaml` (the four `apps.<name>` entries), `scenarios/` (id-based `run`
  scenarios), `record/` (natural-language goals for the `-noax` demo), and a `Makefile` —
  sit alongside.
- **One identifier contract, four products.** The two `-a11y` apps expose byte-for-byte
  identical identifiers / launch hooks / deeplinks, so `scenarios/*.yaml` runs unchanged
  against either toolkit. The `accessibilityID(...)` helper (a `View` extension in SwiftUI, a
  `UIAccessibilityIdentification` extension in UIKit) is the *only* place identifiers enter the
  tree, gated by `#if ACCESSIBLE`; the `-noax` build therefore compiles to a tree with no
  identifiers and no mirrored state values.
- **OS alerts, deliberate and scoped** ([SPEC §7](../../../demos/showcase/SPEC.md)) — no
  permission prompts at launch; the notification and location prompts appear *only* on the
  **Permissions tab**, where they serve as the canonical fixture for the run's vision alert
  guard / `dismissAlerts` (the showcase's own [`permission.yaml`](../../../demos/showcase/scenarios/permission.yaml)
  scenario).
- **Networking** reuses the `sample` app's BajutsuKit integration unchanged, so `network`
  evidence and `mocks` work with no app-side change ([DESIGN §3.2](../../../DESIGN.md)).

### Scope and phasing

- **In scope now:** the four apps, the config, the id-based `run` scenarios, the `record` goal
  set, and the demo wiring (`Makefile`, READMEs). `run` and `doctor` work against this fixture
  today; `record` works today against the `-noax` apps.
- **Forward-looking:** `crawl` is itself a proposal
  ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
  and not yet implemented — the showcase is built to be its first real target (the seed config
  and expected screen-map notes ship as `crawl/` test data), but the crawl demo lands when
  BE-0038 does.
- **Migration:** the older `sample` fixture stays until the showcase covers its on-device CI and
  Web UI tours; superseding it is a follow-up, not part of this item.

## Alternatives considered

- **Extend the existing `sample` app instead of a new suite.** Rejected: `sample` is a single
  SwiftUI app, and the whole point is the *toolkit* and *accessibility* axes. Bolting a UIKit
  target and a no-a11y variant onto `sample` would entangle its existing on-device CI and Web UI
  tours with a much larger surface; a clean `demos/showcase/` keeps the migration incremental.
- **Four independent codebases (no shared source).** Rejected on maintenance cost: four hand-kept
  copies of "the same app" drift, and drift between the a11y and no-a11y twins would quietly
  break the controlled-experiment premise. One codebase per toolkit with a compile-time flag
  keeps "same app, identifiers the only difference" *true by construction*.
- **A single combined app with a runtime toggle for identifiers.** Rejected: a runtime flag still
  ships identifiers in the binary (so `doctor`/`record` would not see a genuinely
  identifier-free app), and it muddies what "the app a team that skipped accessibility ships"
  actually means. A build-time condition produces an honestly identifier-free product.
- **Skip UIKit, SwiftUI only.** Rejected: UIKit is still the larger installed base, and the
  toolkit axis is precisely where idb's element-tree normalization differences surface
  ([BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md)).

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md) ([ja](../../../demos/showcase/SPEC.ja.md)) — the screen-by-screen contract
- [DESIGN §2 / §5 / §7.1 / §7.3 / §8 / §11](../../../DESIGN.md) — determinism, stability ladder, per-app onboarding, identifier naming, config, risks
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — autonomous crawl exploration (this fixture's forward-looking target)
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) — doctor / onboarding (consumes this fixture's coverage)
- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) — idb element-tree normalization (the toolkit axis stresses this)
- [`demos/features/app`](../../../demos/features/app) — the `sample` fixture this supersedes
