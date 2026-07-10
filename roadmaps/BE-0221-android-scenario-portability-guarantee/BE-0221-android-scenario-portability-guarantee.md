**English** · [日本語](BE-0221-android-scenario-portability-guarantee-ja.md)

# BE-0221 — Guarantee shared showcase scenarios run unchanged on Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0221](BE-0221-android-scenario-portability-guarantee.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
| Implementing PR | _pending — fill when the PR opens_ |
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
→ `_` transform, and nothing in the codebase today decides how a `{ id: stable.refresh }` selector
is supposed to find a `resource-id` of `stable_refresh`.

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

The id-matching decision is resolved **in the scenario, not the driver**: a selector's `id` /
`idMatches` may list several candidate ids, matched as an OR, so one shared scenario carries every
platform's form of an id. This keeps the difference between `stable.refresh` (iOS / Compose) and
`stable_refresh` (Views) *explicit and visible* where the scenario is authored, rather than hidden
behind a driver-side `.`↔`_` rewrite that would be implicit, could conflate distinct ids, and would
have to be threaded through every resolution path to work. It is a deliberate, narrow refinement of
[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md): the Driver
still owns *which app-side attribute* satisfies an `id` (the `resource-id`), while *which of several
id spellings* a platform surfaces — when its native id syntax cannot reproduce the SPEC id verbatim
— is expressed in the scenario. The work breaks down MECE into four pieces:

1. **Extend the selector with an OR candidate list.** `Selector.id` / `Selector.idMatches` (the
   scenario model and the shared `base.Selector`) accept `str | list[str]`; `matches` / `find_all`
   treat a list as an OR — an element satisfies it when its identifier equals (or glob-matches) *any*
   candidate. Single-value selectors are unchanged, so every existing scenario and backend behaves
   exactly as before. The showcase's shared scenarios then list both id forms —
   `id: [stable.refresh, stable_refresh]`, `idMatches: [stable.row.*, stable_row_*]` — so
   `showcase-swiftui` / `showcase-compose` / `showcase-views` all run the same files unchanged.
2. **Keep ambiguous-fails-fast intact.** The OR is resolved by the shared determinism core, which
   already fails a 2+ match: only one id form is ever on screen for a given app, so the candidate
   list resolves uniquely, and if both forms were somehow present at once `resolve_unique` still
   raises `AmbiguousSelector` rather than picking one — an OR never turns a fail-fast ambiguity into
   a silent pick (prime directive 2). Tests cover: either form resolves; both-present is ambiguous;
   `find_all` returns matches in `elements` order; and an `idMatches` OR drives a `count` on both
   toolkits.
3. **Widen the Android e2e CI matrix.** Extend
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)'s
   `android-e2e.yml` / `demos/showcase/android/Makefile` `e2e` target to build and run
   `showcase-views` over the same Stable-tab scenario set it already drives against
   `showcase-compose`, so the guarantee is checked on every relevant push/PR, not asserted once by
   hand. `showcase-views` declares `readyWhen` (in both id forms) because its underscore ids defeat
   the `idNamespaces` launch-readiness heuristic (`namespace_of` splits on `.`, absent there).
4. **Close the open callout + document the contract.** Replace the "BE-0007 design decision" callout
   in `demos/showcase/android/README.md` (+ ja) and `showcase.config.yaml` with the resolved rule,
   and document the candidate-list DSL where selectors and the per-platform id mapping already live —
   `docs/scenarios.md`, `docs/drivers.md`, `docs/multi-platform.md`, `docs/architecture.md` (all + ja)
   — so a future backend implementer finds one stated rule for how id matching behaves when a
   platform's native id syntax can't reproduce the SPEC id verbatim.

### Non-goals

This item does not add a new backend or actuation mode. It extends the selector with a
backward-compatible candidate list, but changes no other DSL surface, and does not attempt scenario
portability beyond the id-namespace opt-in model BE-0009 already establishes — it closes a specific,
already-named gap in that model's Android coverage, nothing broader.

## Alternatives considered

- **Driver-side `.`↔`_` normalization (the proposal's original recommendation).** A first design had
  the `AdbDriver` retry a selector after mapping `.`/`-` → `_` when the verbatim id missed (an
  `id_fallback`). Rejected on review: it is an *implicit* transform hidden in the driver, and being a
  fallback that fires only on a miss it invites ambiguous id specification — a scenario author cannot
  tell which id a selector will actually resolve to — while the `.`/`-` → `_` mapping itself can
  conflate distinct ids (`a.b` and `a-b` both become `a_b`). It also would not reach the assertion /
  `wait` / `forEach` paths, which resolve against `query()` output *outside* the driver, without
  threading the normalization through the shared resolver. The scenario-side candidate list is
  explicit, covers every resolution path uniformly, and leaves the shared determinism core untouched.
- **A Views-only scenario fork, mirroring `ios/scenarios-xcuitest`.** Rejected: it would resolve the
  immediate blocker but defeat the stated goal (one shared scenario set), repeating the "per-platform
  scenario variant" shape now inside a single platform's two toolkits. The candidate list keeps a
  single file.
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

- [x] Extend the selector — `id` / `idMatches` accept an OR candidate list (`str | list[str]`) in the scenario model and `base.Selector`; `matches` / `find_all` match any candidate; the shared showcase scenarios list both id forms.
- [x] Ambiguous-fails-fast test coverage — either form resolves, both-present is ambiguous, `find_all` keeps `elements` order, and an `idMatches` OR drives a `count` on both toolkits.
- [x] Widen `android-e2e.yml` / the Android showcase `Makefile`'s `e2e` target to also build and run `showcase-views`; `showcase-views` declares `readyWhen` in both id forms.
- [x] Replace the open "BE-0007 design decision" callout in the README (+ ja) / config with the resolved rule; document the candidate-list DSL in `docs/scenarios.md`, `docs/drivers.md`, `docs/multi-platform.md`, `docs/architecture.md` (all + ja).

Log:

- 2026-07-10 — Implemented as a scenario-side OR candidate list rather than the originally-proposed
  driver-side normalization (see *Alternatives considered*), keeping the id convention explicit in
  the scenario and the shared determinism core untouched. Selector DSL extended, the shared showcase
  scenarios list both id forms, the Android e2e lane now drives `showcase-compose` and
  `showcase-views` over the same set, and the docs/callouts record the contract.

## References

- [`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) — the id-convention
  writeup and the open callout this item resolves.
- [`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) — the
  `showcase-compose` / `showcase-views` target definitions and the same callout.
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml),
  [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile) — the CI lane this item
  widens to drive `showcase-views` alongside `showcase-compose`.
- [`docs/scenarios.md`](../../docs/scenarios.md) — the candidate-list selector DSL this item adds.
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) — the Android backend and driver
  whose exact id matching this item keeps unchanged.
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) — the
  portability model this item refines: the Driver still owns which attribute satisfies an `id`, while
  a platform's differing id *spelling* is expressed as a scenario-side OR.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the
  conformance philosophy motivating "checked continuously, not asserted once."
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) — the Android e2e
  CI lane this item widens.
- Originates from a 2026-07-10 ideation session.
