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
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Going multi-platform changes Bajutsu's strategic scope, and the code is only part of that change. The documentation today scopes Bajutsu to the iOS Simulator alone ([DESIGN §1](../../DESIGN.md), [README](../../README.md)). The first real second platform will be Web, then Android. [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) covers the cross-cutting abstraction work. When that platform lands, the project's stated scope has to move with it. This item tracks the documentation and positioning changes as one deliberate, coordinated step, so the product description never lags the code.

## Motivation

The scope statement sets reader expectations, frames the design rationale, and tells contributors what is in and out of bounds. Suppose the code grows to drive Android and Web while the docs still say "iOS Simulator only". The project then misrepresents itself, and the carefully argued "why iOS-only" reasoning goes stale instead of moving to where it still applies. Treating the scope update as its own item makes it land in the same change as the first new platform, rather than drifting behind that platform.

## Detailed design

### Scope-statement updates this triggers

When the first new platform lands (Phase 1, Web), the same change updates four places:

- **[DESIGN §1](../../DESIGN.md)**, "やること / やらないこと" (what we do / don't do): change iOS-Simulator-only to multi-platform. Move the "実機 / クラウドデバイスファーム" (physical device / cloud device farm) reasoning to where it remains relevant.
- **[README](../../README.md) and [README.ja](../../README.ja.md)**: the product one-liner and the core-principles section.
- **[architecture status](../../docs/architecture.md)**: register the new backend in the implementation-status table.
- **Docs navigation**: both [`docs/README.md`](../../docs/README.md) and [`docs/ja/README.md`](../../docs/ja/README.md).

### Keep the prime directives intact

The scope widens, but the prime directives do not change. Determinism-first, app-agnostic, and "AI is the author and the failure investigator, never the judge" apply identically on Android and Web. In particular, no new platform may introduce an LLM (large language model) into the Tier-2 run/CI gate. Pass/fail stays fully deterministic on every backend. The scope update must reaffirm these directives and must not soften them: the surface broadens while the guarantees hold.

## Alternatives considered

- **Let the scope statement drift and fix it later.** Rejected. A product whose docs say "iOS-Simulator-only" while the code drives Web misrepresents itself. The original iOS-only rationale also goes stale instead of being relocated. Coupling the update to the first platform's landing keeps docs and code honest.
- **Fold these doc edits into the per-platform backend items** ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)). Rejected. The scope change is cross-cutting (DESIGN, README, architecture status, docs navigation) and strategic. A separate item keeps the change from being lost inside a backend's implementation detail, and it still lands in the same change as Phase 1.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [DESIGN §1](../../DESIGN.md) (scope: what we do / don't do)
- [README](../../README.md), [README.ja](../../README.ja.md)
- [architecture.md](../../docs/architecture.md) (implementation status)
- Related items: [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) (cross-platform abstractions), [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (web Playwright backend), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) (Android backend)
