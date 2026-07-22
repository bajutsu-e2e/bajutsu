**English** · [日本語](BE-0293-codegen-playwright-real-compile-ja.md)

# BE-0293 — Real-compile verification for the Playwright (TypeScript) codegen target

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0293](BE-0293-codegen-playwright-real-compile.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0293") |
| Topic | codegen coverage |
<!-- /BE-METADATA -->

## Introduction

`bajutsu codegen --emit playwright` turns a scenario into a TypeScript Playwright test file, but
nothing in the repository ever compiles or runs that file: every assertion in
`tests/test_codegen_playwright.py` checks the emitted source as a string. This item adds the
missing gate — generate a scenario's Playwright test, execute it with the real `@playwright/test`
runner against a real browser, and assert it passes — mirroring the real-compile gate the XCUITest
codegen target already has (`ios-e2e.yml`'s `xcuitest (codegen)` job).

## Motivation

The emitter's unit tests are thorough at the level they operate on: for a given step, they confirm
the right TypeScript call comes out (`page.getByTestId(...)`, `expect(...).toBeVisible()`, and so
on). What they cannot confirm is codegen's actual claim — that the emitted file is a real, runnable
native test. A substring match proves `import { test, expect } from '@playwright/test';` is present
in the text; it says nothing about whether the file compiles under a real `tsconfig`, whether a
chained call resolves against the installed `@playwright/test` version's actual API surface, or
whether the emitted assertions pass against a live page. A wrong method name, a malformed template
literal, or an emitter change that drifts from the real Playwright API would pass every one of the
453 lines in `tests/test_codegen_playwright.py` and only surface when a user tries to run the
generated file.

No workflow or Makefile target closes this gap today. `demos/web`'s own `e2e` target drives the
Playwright backend directly through Bajutsu's own driver layer at runtime — it never touches codegen
output, so it cannot stand in for this check. The XCUITest codegen target already proves the model
works: `demos/showcase/Makefile`'s `ui-test` target generates a Swift file, builds it with
`xcodegen`, and runs it with a real `xcodebuild test`, and that job is a required CI check. Playwright
has no analogous step, even though it is the cheaper of the two targets to verify — no Simulator, no
macOS runner, just the Chromium install `demos/web` already carries.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Emit and land a fixture.** Generate a `demos/web` scenario's Playwright test via
  `bajutsu codegen --emit playwright` and check the emitted `.spec.ts` in, the same way
  `ComponentsUITests.swift` is checked in for the XCUITest target.
- **Run it for real.** Execute the generated spec with the real `@playwright/test` runner against
  the Chromium `demos/web` already installs, asserting the run passes — not a `tsc --noEmit`
  syntax check alone (see *Alternatives considered*).
- **Wire it into CI.** Add a Makefile target mirroring `ui-test` and a `web-e2e.yml` job mirroring
  `xcuitest (codegen)`; land it non-gating first, per the signal-then-required precedent set by
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md), and
  promote it once it proves stable.
- **Match the XCUITest floor, not exceed it.** Scope the fixture scenario to the same DSL surface the
  current XCUITest codegen gate covers (`tap` / `wait` / `type` / basic assertions) so both targets
  start from a comparable real-compile floor. Expanding either emitter's *compiled* DSL coverage
  beyond that floor is a separate, following concern.

## Alternatives considered

- **Type-check with `tsc --noEmit` only, without executing `@playwright/test`.** Cheaper, and it
  would catch a syntax or type error, but it proves only that the file compiles — not that the
  generated selectors, actions, and assertions actually pass against a live page. That is the same
  gap that led the XCUITest gate to run a real `xcodebuild test` rather than a Swift-only syntax
  check, and the reasoning carries over unchanged.
- **Leave the string-only test suite as the only gate.** It already covers the emitter's DSL
  surface broadly; the problem is not coverage breadth but coverage kind. No number of additional
  substring assertions would catch a real `@playwright/test` API drift.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Emit a `demos/web` scenario's Playwright test and check the generated `.spec.ts` in.
- [ ] Run it with the real `@playwright/test` runner against a real browser, asserting it passes.
- [ ] Wire a Makefile target + non-gating `web-e2e.yml` job; promote to required once stable.
- [ ] Scope the fixture to the DSL surface the XCUITest codegen gate already covers.

## References

- [BE-0083 — Unify the codegen emitters behind a shared scenario walk](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)
- [BE-0054 — Web backend completion (rich capabilities & parallel runs)](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/codegen/playwright.py`, `tests/test_codegen_playwright.py`,
  `demos/showcase/Makefile` (`ui-test` target, the XCUITest precedent), `.github/workflows/ios-e2e.yml`
  (`xcuitest (codegen)` job)
