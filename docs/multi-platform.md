**English** · [日本語](ja/multi-platform.md)

# Extending to Android (multi-platform) — overview

> Forward-looking overview. Bajutsu is **multi-platform via a backend-agnostic driver**: the **iOS
> Simulator** (idb) and a **Web (Playwright) backend** have both landed ([DESIGN §1](../DESIGN.md),
> [README](../README.md)) — the web backend runs a deterministic `run` against a browser on the Linux
> gate (see [drivers](drivers.md#playwright-web) and `demos/web`) — while **Android** (emulator) and
> **Flutter** remain planned. This page is the **big-picture overview**
> of how the existing abstractions extend to a new platform: what stays
> unchanged, what each platform adds, and the order to build it in. The **concrete, per-platform design
> and the implementation plan live in the roadmap** — each item is linked below. Read this for the
> direction; follow the roadmap items for the specifics.

Related: [drivers](drivers.md) · [selectors](selectors.md) · [concepts](concepts.md) · [vision](vision.md) · [roadmap → Platform support](../roadmaps/README.md#platform-support-ios--android--web--flutter)

---

## The abstraction is already platform-shaped

Bajutsu's core was built behind a backend-agnostic `Driver` interface intentionally
([drivers](drivers.md), [DESIGN §5](../DESIGN.md)). The deterministic spine — scenario DSL
(domain-specific language), selector resolution, machine assertions, the orchestrator loop, the
evidence subsystem, the reporter — never names iOS. Only **three seams** are iOS-specific today:

1. **The actuator** (`drivers/idb.py`) — drives the UI via `idb` + frame-center coordinate taps.
2. **The environment manager** (`simctl.py`) — `simctl` boot / erase / launch / openurl.
3. **The stable-id convention** (`accessibilityIdentifier`, [DESIGN §7](../DESIGN.md)) — the app-side
   source that makes `Selector.id` resolution deterministic.

Adding multi-platform support means **adding a new triple** (actuator + environment + id convention)
per platform, while the deterministic core stays byte-for-byte the same. This is the same move the
design already anticipates for a second iOS actuator (XCUITest) — generalized across OSes.

## The crux: selector portability

A scenario is portable across platforms **only to the extent its selectors are by `id`**
([concepts §4–5](concepts.md#4-stable-selectors-prefer-accessibilityidentifier)). Each platform has a
native equivalent of `accessibilityIdentifier` — a non-localized, developer-assigned handle — and the
per-platform id convention maps onto it:

| `Selector` field | iOS | Android | Web |
|---|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `resource-id` (Compose: `testTag`) | `data-testid` |
| `label` (auxiliary) | `accessibilityLabel` | `content-desc` / `text` | accessible name / `aria-label` |
| `traits` (role filter) | UI traits | widget class | ARIA `role` |

The key property: **the YAML selector `{ id: settings.reindex }` is already platform-neutral.** What
differs is *which app-side attribute the backend reads to satisfy it* — and that lives entirely inside
the new Driver, never in the scenario. Realistically the model is **per-platform scenarios that share
one DSL, one runner, and one toolchain**; cross-platform *reuse* is an **opt-in** for the slices that
genuinely match, expressed through shared id namespaces (`auth.*`, `nav.*`) kept in parity.

One wrinkle: a platform's native id syntax may not reproduce the SPEC id **verbatim**. Android's
`android:id` (the Views toolkit) allows neither `.` nor `-`, so `stable.refresh` surfaces as
`stable_refresh`. Rather than a hidden driver-side `.`↔`_` rewrite — which would conflate distinct ids
and erode determinism — the scenario keeps the difference **explicit** by giving `id` / `idMatches` a
**list of candidates** (`id: [stable.refresh, stable_refresh]`), matched as an OR; only one form is
ever on screen for a given app, so it stays deterministic. This is what lets the showcase's shared
scenarios run unchanged on both Android UI toolkits (BE-0221); see
[scenarios](scenarios.md#cross-platform-ids-a-candidate-list-be-0221).

## Direction & phasing (what's planned)

The deterministic core does not change; each platform only adds its triple. The first slice has
already landed, and the rest is sequenced to pay the generalization cost where it is cheapest:

| Step | Scope | Status / roadmap item |
|---|---|---|
| **Landed** | Platform-aware backend registry (`--backend` / `backend:` accept `ios`/`android`/`web`/`fake`) | Implemented — [BE-0042](../roadmaps/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) |
| **Shared abstractions** | Extract an `Environment` Protocol; audit for leaked iOS-isms; the selector / config / determinism design | Implemented — [BE-0009](../roadmaps/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) |
| **Phase 1 — Web** | Playwright; **runs on the existing Linux gate, no Mac / emulator**; exercises the rich end of the capability model. Recommended first | Implemented (deterministic `run` + `demos/web`) — [BE-0041](../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md); rich-end capabilities (network / video / multi-touch / parallel) tracked in [BE-0054](../roadmaps/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) |
| **Phase 2 — Android** | adb + UI Automator; the coordinate-driven twin of idb | In progress — [BE-0007](../roadmaps/BE-0007-android-backend/BE-0007-android-backend.md) |
| **Phase 3 — Flutter / hybrids** | Cross-rendered UIs need a semantics bridge, not a new OS actuator | Planned — [BE-0008](../roadmaps/BE-0008-flutter-support/BE-0008-flutter-support.md) |
| **Cross-cutting** | Multi-platform is a strategic scope change (DESIGN / README / docs) | Implemented — [BE-0010](../roadmaps/BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) |

**Why Web before Android**, even though Android is the closer architectural twin of idb: Web is the
only platform that needs no macOS and no device emulator, so it fits inside the current
[`make check`](../CLAUDE.md) / [CI](ci.md) gate from day one — proving the core is platform-neutral at
the lowest possible cost. Android then confirms the lean / coordinate path on an already-generalized
core.

## What stays fixed across every platform

The shared, deterministic core does **not** fork as platforms are added: the scenario DSL & grammar
([scenarios](scenarios.md) / [dsl-grammar](dsl-grammar.md)), selector resolution
([selectors](selectors.md)), machine assertions, the observe → act → verify orchestrator
([run-loop](run-loop.md)), the evidence subsystem ([evidence](evidence.md)), and the reporter
([reporting](reporting.md)). The **prime directives** hold identically on Android and Web:
determinism-first, app-agnostic, and **AI is never the judge** — no new platform may put an LLM into
the Tier-2 `run` / CI gate.

> **How this relates to the roadmap.** This page is the overview; the prioritized, concrete plans are
> the [Platform support](../roadmaps/README.md#platform-support-ios--android--web--flutter) items above.
> When a platform ships, it also moves to the
> [architecture status table](architecture.md#implementation-status).
