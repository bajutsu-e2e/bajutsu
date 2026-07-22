**English** · [日本語](BE-0297-codegen-xcuitest-dsl-coverage-ja.md)

# BE-0297 — Expand XCUITest codegen's real-compile coverage to the full DSL surface

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0297](BE-0297-codegen-xcuitest-dsl-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0297") |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

The XCUITest codegen target is the one real-compile success story among the three codegen emitters:
`ios-e2e.yml`'s `xcuitest (codegen)` job generates a Swift file from
`demos/showcase/scenarios/components.yaml`, builds it with `xcodegen`, and runs it with a real
`xcodebuild test`. But that scenario, by its own header comment, deliberately covers only a narrow
slice — `tap` (by label, by traits, by id), `wait`, `type`, and basic assertions — leaving most of
what `bajutsu/codegen/xcuitest.py` emits unreachable by any compiler. This item expands the compiled
fixture so the gate proves what the emitter actually implements, not just its simplest corner.

## Motivation

`tests/test_codegen.py` exercises far more of the emitter than the CI compile step does:
the text-editing steps `clear` / `delete` / `select` / `copy`
([BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md)), `longPress` / `swipe` /
`drag` gestures, coordinate swipes, and compound selectors with `traits` and `index`. All of these
are checked only as substrings of the generated Swift, never compiled. (`within`, a geometric
frame-containment constraint, stays unsupported by design — `_query()` returns `UNSUPPORTED_SELECTOR`
for it rather than real Swift — so it has no compiled coverage to add and sits outside this item's
scope.) Two gaps are sharper still. `pinch` / `rotate` multi-touch emits real
`.pinch(withScale:)` / `.rotate(...)` XCTest calls (`xcuitest.py:169-180`) that are never compiled
or run against a device anywhere in the repository —
not even the on-device conformance suite exercises the codegen emitter's version of these calls, only
the driver's own. And `forEach` / `if` control flow and `extract` have no emitter handling at all:
they fall through to a generic `// TODO: unsupported step` comment
(`xcuitest.py:284`), so a scenario using them silently produces a no-op stub with no compiler or test
ever flagging the gap.

The risk is concrete: an emitter change to any of these paths can break the generated Swift's syntax
or its real XCTest API usage, and every existing test — string-based, by construction — would still
pass.

## Detailed design

The work breaks down MECE into the units below.

- **Extend the compiled scenario.** Add text-editing (`clear` / `delete` / `select` / `copy`),
  gesture (`longPress`, both `swipe` forms — direction and coordinate `from`/`to` — and `drag`), and
  compound-selector (`traits` + `index`) steps to `components.yaml` or a sibling scenario compiled by
  the same `xcuitest (codegen)` job, so the generated Swift for each construct is actually built and
  run.
- **Compile and run `pinch` / `rotate`.** Add a multi-touch scenario to the compiled set, reusing the
  showcase gestures screen the driver-level `xcuitest (multi-touch)` job already exercises, so the
  *emitted* `.pinch(withScale:)` / `.rotate(...)` calls are compiled and run, not only the driver's own.
- **Decide `forEach` / `if` / `extract` codegen: implement or declare unsupported explicitly.** Today
  these silently degrade to a TODO comment. Either emit real Swift control flow and compile/run it, or
  make the emitter raise a clear "unsupported for codegen" error at generation time instead of a silent
  stub — either resolution closes the gap; leaving it silent does not.
- **Land incrementally, non-gating first for the new slices.** The existing `components.yaml` slice
  stays required; extend it (or add a sibling scenario) as non-gating signal until the newly compiled
  constructs prove stable, then fold them into the required job.

## Alternatives considered

- **Leave the current narrow scenario as-is and rely on the string-based unit tests for the rest.**
  This status quo is what this item addresses; a substring match cannot catch a real Swift compile
  error or a real XCTest API misuse, which is exactly the failure mode a compiled scenario exists to
  catch.
- **Write a second, fully separate compiled scenario per uncovered construct.** More granular, but
  each new on-device Simulator job is expensive on metered macOS runners; grouping the additions into
  the existing `components.yaml` (or one sibling file) keeps the added CI cost proportionate.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Extend the compiled scenario with text-editing, gesture, and compound-selector steps.
- [ ] Compile and run `pinch` / `rotate` multi-touch codegen output.
- [ ] Resolve `forEach` / `if` / `extract` codegen: implement and compile, or fail loudly at generation time.
- [ ] Land new slices non-gating first, promote once stable.

## References

- [BE-0265 — Text-editing steps: select, clear, delete, copy](../BE-0265-text-editing-steps/BE-0265-text-editing-steps.md)
- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/codegen/xcuitest.py`, `tests/test_codegen.py`, `tests/test_gestures.py`,
  `demos/showcase/scenarios/components.yaml`, `.github/workflows/ios-e2e.yml`
  (`xcuitest (codegen)` and `xcuitest (multi-touch)` jobs)
