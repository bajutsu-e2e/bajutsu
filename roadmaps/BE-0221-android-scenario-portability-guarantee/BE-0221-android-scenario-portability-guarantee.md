**English** · [日本語](BE-0221-android-scenario-portability-guarantee-ja.md)

# BE-0221 — Guarantee shared showcase scenarios run unchanged on Android

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0221](BE-0221-android-scenario-portability-guarantee.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0221") |
| Implementing PR | [#889](https://github.com/bajutsu-e2e/bajutsu/pull/889) |
| Topic | Platform support |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

The showcase has a shared scenario set ([`demos/showcase/scenarios/`](../../demos/showcase/scenarios)).
Its scenarios are authored id-first for iOS (`showcase-swiftui`, `showcase-uikit`). One of the two
Android UI toolkits, Compose (`showcase-compose`), already reuses the set verbatim. `Modifier.aid(...)`
surfaces the dotted SPEC ids through `testTagsAsResourceId` unchanged.

The other Android toolkit, Views (`showcase-views`), cannot yet run that same set. `android:id`
disallows `.` and `-`, so Views ids are mechanically `_`-mapped (`stable.refresh` → `stable_refresh`).

Both [`demos/showcase/android/README.md`](../../demos/showcase/android/README.md) and
[`showcase.config.yaml`](../../demos/showcase/showcase.config.yaml) name the resulting id-matching
question and leave it open:

> Whether the BE-0007 driver normalizes `.` ↔ `_` when matching, or the Views targets get their own
> scenario variant, is a BE-0007 design decision.

This item resolves that decision. Today, "the iOS-authored scenarios run unchanged on the Android
showcase" holds for one toolkit. The item turns the claim into a guarantee for both toolkits. CI
checks the guarantee continuously.

## Motivation

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) states the
portability model plainly. A scenario can be shared across platforms when its selectors are by `id`.
The Driver alone decides which app-side attribute satisfies that `id`; the scenario never names it.

Compose already proves the model. Its `testTag` accepts the dotted SPEC id verbatim. The shared
scenario set therefore drives Compose with no scenario-side change.

Views is where the model is still unproven. The platform's own `android:id` naming rules force a
mechanical `.`/`-` → `_` transform. Today nothing in the codebase decides how a
`{ id: stable.refresh }` selector finds a `resource-id` of `stable_refresh`.

This gap is not hypothetical. The Android e2e CI lane of
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
(`.github/workflows/android-e2e.yml`) is the sole automated signal on the shared scenarios. The signal
says whether they keep working against an Android showcase build.

That lane drives [`--target showcase-compose`](../../demos/showcase/android/Makefile) alone. The `e2e`
target of `demos/showcase/android/Makefile` depends on `compose-build`, not `views-build`.

"The shared scenarios run unchanged on Android" is therefore true for one of the two Android UI
toolkits. Nothing exercises the claim for the other toolkit, in CI or anywhere else.

Views is the architectural twin of UIKit, the reference accessibility-on toolkit on the iOS side
(`showcase-uikit`). Leaving the portability of Views unresolved has a cost. Half of the Android
showcase's own accessibility-on surface cannot use the scenario assets that every other showcase
target already shares. That undermines a premise that both BE-0009 and BE-0007 build on: "the showcase
proves the abstraction holds".

## Detailed design

This item resolves the id-matching decision **in the scenario, not the driver**. A selector's `id` /
`idMatches` may list multiple candidate ids, matched as an OR. One shared scenario then carries every
platform's form of an id.

The difference between `stable.refresh` (iOS / Compose) and `stable_refresh` (Views) stays *explicit
and visible* where the scenario author works. A driver-side `.`↔`_` rewrite would hide the difference.
That rewrite would be implicit and could conflate distinct ids. It would also need threading through
every resolution path.

The decision is a deliberate, narrow refinement of
[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md). The Driver
still owns *which app-side attribute* satisfies an `id` (the `resource-id`). The scenario expresses
*which id spelling* a platform surfaces, where its native id syntax cannot reproduce the SPEC id
verbatim.

The work breaks down MECE into four pieces:

1. **Extend the selector with an OR candidate list.** `Selector.id` and `Selector.idMatches` accept
   `str | list[str]`, in both the scenario model and the shared `base.Selector`. `matches` and
   `find_all` treat a list as an OR. An element satisfies the selector when its identifier equals (or
   glob-matches) *any* candidate. Single-value selectors do not change, so every existing scenario and
   backend behaves as before. The showcase's shared scenarios then list both id forms:
   `id: [stable.refresh, stable_refresh]` and `idMatches: [stable.row.*, stable_row_*]`.
   `showcase-swiftui`, `showcase-compose`, and `showcase-views` all run the same files unchanged.
2. **Keep ambiguous-fails-fast intact.** The shared determinism core resolves the OR, and that core
   already fails a 2+ match. A given app shows one id form on screen, so the candidate list resolves
   uniquely. If both forms were somehow present at once, `resolve_unique` still raises
   `AmbiguousSelector` rather than picking one. An OR never turns a fail-fast ambiguity into a silent
   pick (prime directive 2). Tests cover four cases:
   - either form resolves;
   - both forms present is ambiguous;
   - `find_all` returns matches in `elements` order;
   - an `idMatches` OR drives a `count` on both toolkits.
3. **Widen the Android e2e CI matrix.** Extend the `android-e2e.yml` workflow of
   [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) and the `e2e`
   target of `demos/showcase/android/Makefile`. They then build and run `showcase-views` over the same
   Stable-tab scenario set that they already drive against `showcase-compose`. CI checks the guarantee
   on every relevant push or PR instead of relying on a one-off manual check. `showcase-views` declares `readyWhen` in
   both id forms, because its underscore ids defeat the `idNamespaces` launch-readiness heuristic
   (`namespace_of` splits on `.`, which is absent there).
4. **Close the open callout and document the contract.** Replace the "BE-0007 design decision" callout
   in `demos/showcase/android/README.md` (+ ja) and `showcase.config.yaml` with the resolved rule.
   Document the candidate-list DSL where selectors and the per-platform id mapping already live:
   `docs/scenarios.md`, `docs/drivers.md`, `docs/multi-platform.md`, and `docs/architecture.md` (all +
   ja). A future backend implementer then finds one stated rule. The rule says how id matching
   behaves when a platform's native id syntax cannot reproduce the SPEC id verbatim.

### Non-goals

This item does not add a new backend or actuation mode. It extends the selector with a
backward-compatible candidate list and changes no other DSL surface. It does not attempt scenario
portability beyond the id-namespace opt-in model that BE-0009 already establishes. It closes a
specific, already-named gap in that model's Android coverage, and nothing broader.

### Scaling beyond two id forms

The current roadmap needs two forms (dotted and underscore). iOS and Android Compose surface the SPEC
id verbatim, and Android Views maps it to underscores. A candidate list grows by one entry per
platform whose native id syntax differs. The canonical-form-first rule keeps holding as it grows.

This item does not generalize that hand-maintenance, for two reasons.

- A third id spelling is not concrete yet. A Flutter backend
  ([BE-0008](../BE-0008-flutter-support/BE-0008-flutter-support.md)) is still a Proposal.
- Breakage is loud per target. A selector missing a candidate fails *that* target's run, not
  everywhere in silence. An omission surfaces the moment someone runs the new target.

When a third platform actually lands, the natural fit is an `audit`-side coverage check. The check
would cross-reference each configured target's id convention against every selector's candidates. It
would extend `referenced_ids`, which already enumerates the candidates. The team should build the
check *with* the new platform, so the check has a concrete convention to test against. Building an
N-form mechanism now would be speculative.

## Alternatives considered

- **Driver-side `.`↔`_` normalization (the proposal's original recommendation).** A first design had
  the `AdbDriver` retry a selector after mapping `.`/`-` → `_` when the verbatim id missed (an
  `id_fallback`). Rejected on review, for three reasons.
  - The transform is *implicit* and hidden in the driver. It applies on a miss alone, so it invites
    ambiguous id specification: a scenario author cannot tell which id a selector resolves to.
  - The `.`/`-` → `_` mapping itself can conflate distinct ids (`a.b` and `a-b` both become `a_b`).
  - It would not reach the assertion, `wait`, and `forEach` paths. Those paths resolve against
    `query()` output *outside* the driver, so the shared resolver would need the normalization too.

  The scenario-side candidate list is explicit, covers every resolution path uniformly, and leaves the
  shared determinism core untouched.
- **A Views-only scenario fork, mirroring `ios/scenarios-xcuitest`.** Rejected. It would resolve the
  immediate blocker but defeat the stated goal of one shared scenario set. It would repeat the
  "per-platform scenario variant" shape, now inside a single platform's two toolkits. The candidate
  list keeps a single file.
- **Leave the Android e2e lane at Compose-only indefinitely, and revisit if Views regresses.**
  Rejected. An unexercised path is the kind of latent per-backend drift that the conformance philosophy
  of [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) warns against.
  The team should build the id matching of Views and check it continuously. A standing, documented gap
  is the wrong outcome.
- **Fold this work as sub-bullets into BE-0007 and BE-0208 directly, rather than a new item.**
  Considered during ideation. The decision was to keep it as its own tracked item, with an explicit
  Progress checklist and Related links back to both prerequisites. The alternative would split one
  end-to-end guarantee across the work breakdowns of two other items.

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
