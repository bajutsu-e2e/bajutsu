**English** · [日本語](BE-0221-android-scenario-portability-guarantee-ja.md)

# BE-0221 — Guarantee shared showcase scenarios run unchanged on Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0221](BE-0221-android-scenario-portability-guarantee.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

The showcase's shared scenario set ([`demos/showcase/scenarios/`](../../demos/showcase/scenarios))
is already authored id-first for iOS (`showcase-swiftui`, `showcase-uikit`) and reused verbatim by
one of the two Android UI toolkits, Compose (`showcase-compose`): `Modifier.aid(...)` surfaces the
dotted SPEC ids through `testTagsAsResourceId` unchanged. The other Android toolkit, Views
(`showcase-views`), cannot yet run that same set — `android:id` disallows `.` and `-`, so its ids
are mechanically `_`-mapped (`stable.refresh` → `stable_refresh`), and the resulting id-matching
question is left open by name in both
[`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) and
[`showcase.config.yaml`](../../demos/showcase/showcase.config.yaml): *"Whether the BE-0007 driver
normalizes `.` ↔ `_` when matching, or the Views targets get their own scenario variant, is a
BE-0007 design decision."* This item resolves that decision and turns "the iOS-authored scenarios
run unchanged on the Android showcase" from a claim that holds for one toolkit into a guarantee
that holds for both, continuously checked in CI.

## Motivation

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) states the
portability model plainly: a scenario is shared across platforms only to the extent its selectors
are by `id`, and which app-side attribute satisfies that `id` lives entirely inside the Driver,
never the scenario. Compose already proves the model — its `testTag` accepts the dotted SPEC id
verbatim, so the shared scenario set drives it with no scenario-side change at all. Views is where
the model is still unproven: the platform's own `android:id` naming rules force a mechanical `.`/`-`
→ `_` transform, and nothing in the codebase today decides, on the driver side, how a `{ id:
stable.refresh }` selector is supposed to find a `resource-id` of `stable_refresh`.

This is not a hypothetical gap. [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)'s
Android e2e CI lane (`.github/workflows/android-e2e.yml`) is the only automated signal that the
shared scenarios keep working against an Android showcase build, and it drives
[`--target showcase-compose`](../../demos/showcase/android/Makefile) only —
`demos/showcase/android/Makefile`'s `e2e` target depends on `compose-build`, not `views-build`. So
today "the shared scenarios run unchanged on Android" is true for one of the two Android UI
toolkits and unexercised, in CI or otherwise, for the other. Views is the architectural twin of
UIKit, which is the reference accessibility-on toolkit on the iOS side (`showcase-uikit`); leaving
its portability unresolved means half of the Android showcase's own accessibility-on surface still
can't be dogfooded with the same scenario assets every other showcase target already shares —
undermining the "the showcase proves the abstraction holds" premise both BE-0009 and BE-0007 build
on.

## Detailed design

The work breaks down MECE into four pieces:

1. **Resolve the id-matching decision, in the driver.** Recommend driver-side normalization over a
   scenario fork: when an `AdbDriver` `id` lookup finds no exact `resource-id` match, retry after
   mapping `.` and `-` in the selector's id to `_` — the same mechanical transform
   `demos/showcase/android/README.md` already documents the Views build performing in the other
   direction. This keeps "the id convention lives entirely inside the Driver" (BE-0009) intact and
   avoids forking `demos/showcase/scenarios/` the way `demos/showcase/ios/scenarios-xcuitest`
   already forks for the iOS `-noax` targets — a fork this item deliberately does not repeat, since
   BE-0009 already rejected "one YAML run thrice" as the portability model and a Views-only fork
   would repeat that rejected shape one level down, inside a single platform. Ownership of the
   driver-side change stays with [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md);
   this item specifies the decision and its rationale.
2. **Keep ambiguous-fails-fast intact through the retry.** A normalization retry must never mask a
   genuine ambiguous-selector failure (prime directive 2): if the exact match already resolves
   uniquely, no retry happens; if the exact match is ambiguous, the retry must not "help" by picking
   one of the ambiguous matches instead. Add a driver-level test asserting both the retry path (a
   selector that only matches after `.`/`-` → `_`) and this failure-preserving case (an ambiguous
   exact match stays ambiguous, and a `_`-normalized match that is itself ambiguous also fails).
3. **Widen the Android e2e CI matrix.** Extend
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)'s
   `android-e2e.yml` / `demos/showcase/android/Makefile` `e2e` target to also build and run
   `showcase-views` over the same scenario set BE-0208 already drives against `showcase-compose`,
   so the guarantee is checked on every relevant push/PR, not asserted once by hand.
4. **Close the open callout in the docs.** Once the decision lands, replace the "BE-0007 design
   decision" callout in `demos/showcase/android/README.md` and `showcase.config.yaml` with the
   resolved rule, and record the id-matching contract in `docs/architecture.md` (+ ja) alongside the
   existing per-platform selector-mapping table, so a future backend implementer finds one place
   that states how id matching is expected to behave when a platform's native id syntax can't
   reproduce the SPEC id verbatim.

### Non-goals

This item does not add a new backend or actuation mode, does not change the scenario DSL, and does
not attempt scenario portability beyond the id-namespace opt-in model BE-0009 already establishes —
it closes a specific, already-named gap in that model's Android coverage, nothing broader.

## Alternatives considered

- **A Views-only scenario fork, mirroring `ios/scenarios-xcuitest`.** Rejected: it would resolve the
  immediate blocker but defeat the stated goal, and it repeats the "per-platform scenario variant"
  shape BE-0009 already confined to the iOS `-noax` case, now inside a single platform's two
  toolkits instead of across platforms.
- **Leave the Android e2e lane at Compose-only indefinitely, revisit only if Views regresses.**
  Rejected: an unexercised path is exactly the kind of latent per-backend drift
  [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)'s conformance
  philosophy warns against — Views' id matching should be built and continuously checked, not left
  as a standing, documented gap.
- **Fold this work as sub-bullets into BE-0007 and BE-0208 directly, rather than a new item.**
  Considered during ideation; the decision was to keep it as its own tracked item, with an explicit
  Progress checklist and Related links back to both prerequisites, rather than splitting one
  end-to-end guarantee across two other items' own work breakdowns.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Resolve the id-matching decision — driver-side `.`/`-` → `_` normalization retry in `AdbDriver` (owned by BE-0007).
- [ ] Ambiguous-fails-fast test coverage for the normalization retry path.
- [ ] Widen `android-e2e.yml` / the Android showcase `Makefile`'s `e2e` target to also run `showcase-views`.
- [ ] Replace the open "BE-0007 design decision" callout in the README/config with the resolved rule; document the contract in `docs/architecture.md` (+ ja).

## References

- [`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) — the id-convention
  writeup and the open callout this item resolves.
- [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) — the
  `showcase-compose` / `showcase-views` target definitions and the same callout.
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml),
  [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) — the CI lane currently
  scoped to `showcase-compose` only.
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) — the Android backend and driver
  this item's normalization decision extends.
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) — the
  portability model (id-namespace opt-in, id convention lives in the Driver) this item upholds.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the
  conformance philosophy motivating "checked continuously, not asserted once."
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) — the Android e2e
  CI lane this item widens.
- Originates from a 2026-07-10 ideation session.
