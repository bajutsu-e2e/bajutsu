**English** · [日本語](BE-0010-update-scope-statement-ja.md)

# BE-0010 — Update the scope statement

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0010](BE-0010-update-scope-statement.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0010") |
| Implementing PR | [#327](https://github.com/bajutsu-e2e/bajutsu/pull/327) |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Going multi-platform is a **strategic scope change**, not just code. Bajutsu is documented today as scoped to the iOS Simulator only ([DESIGN §1](../../../DESIGN.md), [README](../../../README.md)). When the first real second platform lands (Web, then Android — see [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) for the cross-cutting abstraction work), the project's stated scope has to move with it. This item tracks those documentation and positioning changes as a deliberate, coordinated step, so the product description never lags the code.

## Motivation

The scope statement is load-bearing: it sets reader expectations, frames the design rationale, and tells contributors what is in and out of bounds. If the code grows to drive Android and Web while the docs still say "iOS Simulator only," the project misrepresents itself — and the carefully argued "why iOS-only" reasoning becomes stale rather than relocated. Treating the scope update as its own item ensures it lands **in the same change** as the first platform, rather than drifting behind it.

## Detailed design

### Scope-statement updates this triggers

When the first new platform lands (Phase 1, Web), update in the same change:

- **[DESIGN §1](../../../DESIGN.md)** "やること / やらないこと" (what we do / don't do) — iOS-Simulator-only → multi-platform; move the "実機 / クラウドデバイスファーム" (physical device / cloud device farm) reasoning to where it remains relevant.
- **[README](../../../README.md) / [README.ja](../../../README.ja.md)** — the product one-liner and the core-principles section.
- **[architecture status](../../../docs/architecture.md)** — register the new backend in the implementation-status table.
- **docs navigation** — both [`docs/README.md`](../../../docs/README.md) and [`docs/ja/README.md`](../../../docs/ja/README.md).

### Keep the prime directives intact

The scope **widens**, but the prime directives do **not** change. Determinism-first, app-agnostic, and *AI is the author and the failure investigator, never the judge* apply identically on Android and Web. In particular, **no new platform may introduce an LLM into the Tier-2 run/CI gate** — pass/fail stays fully deterministic on every backend. The scope update must reaffirm these directives, not soften them: the surface broadens while the guarantees hold.

## Alternatives considered

- **Let the scope statement drift and fix it later.** Rejected: a product whose docs say "iOS-Simulator-only" while the code drives Web misrepresents itself, and the original iOS-only rationale goes stale rather than being relocated. Coupling the update to the first platform's landing keeps docs and code honest.
- **Fold these doc edits into the per-platform backend items** ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0007](../in-progress/BE-0007-android-backend/BE-0007-android-backend.md)). Rejected: the scope change is cross-cutting (DESIGN, README, architecture status, docs nav) and strategic, so tracking it as its own item keeps it from being lost inside a backend's implementation detail — while still landing in the same change as Phase 1.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN §1](../../../DESIGN.md) (scope: what we do / don't do)
- [README](../../../README.md), [README.ja](../../../README.ja.md)
- [architecture.md](../../../docs/architecture.md) (implementation status)
- Related items: [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) (cross-platform abstractions), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (web Playwright backend), [BE-0007](../in-progress/BE-0007-android-backend/BE-0007-android-backend.md) (Android backend)
