**English** · [日本語](BE-0009-cross-platform-abstractions-ja.md)

# BE-0009 — Cross-platform abstractions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0009](BE-0009-cross-platform-abstractions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Implementing PR | [#346](https://github.com/bajutsu-e2e/bajutsu/pull/346) |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu is scoped today to the iOS Simulator only ([DESIGN §1](../../../DESIGN.md), [README](../../../README.md)), but its core was deliberately built behind a backend-agnostic `Driver` interface. This item is the cross-cutting abstraction work that lets the same deterministic core also drive Android (emulator) and Web (browser): extracting the iOS-specific seams behind platform-neutral protocols, generalizing the config schema with a `platform` discriminator, and auditing the runner and orchestrator for leaked iOS assumptions. The per-platform backends themselves are separate items — Android is [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md), Web is [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (web-playwright-backend), and the already-landed selector/registry slice is [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) (platform-backend-registry). This item is the shared substrate they all build on.

## Motivation

### The abstraction is already platform-shaped

The deterministic spine — scenario DSL (domain-specific language), selector resolution, machine assertions, the orchestrator loop, the evidence subsystem, the reporter — never names iOS. Only **three seams** are iOS-specific today:

1. **The actuator** (`drivers/idb.py`) — drives the UI via `idb` + frame-center coordinate taps.
2. **The environment manager** (`env.py`) — `simctl` boot / erase / launch / openurl.
3. **The stable-id convention** (`accessibilityIdentifier`, [DESIGN §7](../../../DESIGN.md)) — the app-side source that makes `Selector.id` resolution deterministic.

Adding multi-platform support means **adding a new triple** (actuator + environment + id convention) per platform, while the deterministic core stays byte-for-byte the same. This is the same move the design already anticipates for a second iOS actuator (XCUITest, [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)) — generalized across OSes.

#### What stays unchanged vs. what each platform adds

| Layer | Status when adding a platform |
|---|---|
| Scenario DSL & grammar | **Unchanged.** Steps, waits, assertions, capture tokens are platform-neutral |
| Selector model & resolution (`drivers/base.py` `resolve_unique`) | **Unchanged.** 0/1/2+ semantics and ambiguous-fails-fast are backend-agnostic |
| Machine assertions (`assertions.py`) | **Unchanged.** `exists`/`value`/`label`/`count`/`enabled`/… evaluate the normalized `Element` tree |
| Orchestrator loop (`orchestrator.py`) | **Unchanged.** observe → act → verify; condition-waits poll `query()` |
| Evidence subsystem (`evidence.py`, capturePolicy, `manifest.json`) | **Mostly unchanged.** Capture *tokens* stay; *providers* gain per-platform sources |
| Reporter (`report.py`) | **Unchanged.** manifest / JUnit / HTML are platform-neutral |
| Config layering (`config.py`, `defaults × apps`) | **Extended.** A `platform` field + per-platform target fields (below) |
| **Driver backend** (`drivers/*.py`, `capabilities()`) | **New per platform** — the actuator |
| **Environment/lifecycle manager** (`env.py` peer) | **New per platform** — boot / clean / launch / deeplink |
| **doctor convention checks** (`doctor.py`) | **New per platform** — the §7-equivalent score |
| **codegen emitter** (`codegen.py`) | **New per platform** — the native test it transpiles to |

The first new platform is the most expensive to add because it forces any latent iOS-specific assumptions in the "unchanged" column out into the open. The second platform is cheaper. Doing this abstraction work as its own item pays that cost once, with no device involved.

## Detailed design

### The crux: selector portability

A scenario is portable across platforms **only to the extent its selectors are by `id`**. Each platform has a native equivalent of `accessibilityIdentifier` — a **non-localized, developer-assigned, data-derived** handle — and the per-platform id convention ([DESIGN §7.3](../../../DESIGN.md)) maps onto it:

| `Selector` field | iOS | Android | Web |
|---|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `resource-id` (Compose: `Modifier.testTag` + `testTagsAsResourceId`) | `data-testid` |
| `label` (auxiliary) | `accessibilityLabel` | `content-desc` / `text` | accessible name / `aria-label` / text |
| `traits` (role filter) | UI traits (`button`, `link`, …) | widget class (`android.widget.Button`) | ARIA (Accessible Rich Internet Applications) `role` (`button`, `link`, `textbox`) |
| `value` | accessibility value | `text` / checked state | input `value` / `aria-*` |

The key property: **the YAML selector `{ id: settings.reindex }` is already platform-neutral.** What differs is *which app-side attribute the backend reads to satisfy it* — and that lives entirely inside the new Driver, never in the scenario.

**Honest stance on shared scenarios.** Three apps for the same product rarely have identical screens, so the realistic model is **per-platform scenarios that share one DSL, one runner, and one toolchain** — not one YAML run thrice. Cross-platform *reuse* is then an **opt-in** for the slices that genuinely match, expressed through the existing **reserved/shared id namespaces** (`auth.*`, `nav.*`, [DESIGN §7.3](../../../DESIGN.md)): a login `setup:` component can run on all three platforms iff those ids are kept in parity. The tool provides *portable tooling* and *portable scenarios* only where the team maintains an id contract — a single YAML is not automatically a tri-platform test.

### Configuration changes

`apps.<name>` gains a **`platform`** discriminator and per-platform target fields; the deterministic resolution order (`defaults < app < scenario`) is unchanged.

```yaml
defaults:
  platform: ios                 # default; per-app override below
  locale:  ja_JP

apps:
  sample-ios:
    platform:       ios
    backend:        [idb]
    bundleId:       com.bajutsu.sample
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, settings]

  sample-android:
    platform:       android
    backend:        [adb]
    package:        com.bajutsu.sample          # ← bundleId's peer
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, settings]

  sample-web:
    platform:       web
    backend:        [playwright]
    baseUrl:        https://app.example.test     # ← bundleId's peer
    idNamespaces:   [home, settings]
```

`platform` selects which **environment manager** and **backend registry** are in play; the rest of the schema (namespaces, redact, setup, capture) stays shared. The selector slice of this registry has already landed (`bajutsu/backends.py` keys off a platform registry, and `--backend` / `backend:` accept a platform token); see [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md). This item covers the remaining cross-cutting work: the `platform` config field and the `Environment` protocol the registry hands off to.

> **The web backend's v1 took a shortcut here.** The first Web (Playwright) slice ([BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) needed a target URL but not yet a `platform` discriminator, so it added a single `baseUrl` field to `apps.<name>` and kept the environment lifecycle (fresh `BrowserContext` = `erase`, `goto(baseUrl)` = `launch`) **inside the driver**, branching the runner on `actuator == "playwright"` instead of going through a neutral `Environment` protocol. That was the smallest correct change to land a working web `run`. This item generalizes it: the `platform` field subsumes the `bundleId`-vs-`baseUrl` choice, and the per-platform lifecycle moves behind the `Environment` protocol so the runner stops branching on the actuator name.

### Determinism is preserved per platform

The four mechanisms hold on every backend — only their *implementation* differs:

| Principle | iOS | Android | Web |
|---|---|---|---|
| Ambiguous selector fails fast | `resolve_unique` (shared) | `resolve_unique` (shared) | `resolve_unique` (shared) |
| Condition waits, no fixed sleep | poll `query()` | poll `uiautomator dump` | Playwright auto-wait + poll |
| Clean environment per test | `simctl erase` | `pm clear` | fresh `new_context()` |
| Pass/fail machine-checkable only | normalized `Element` | normalized `Element` | normalized `Element` |

`resolve_unique` and `assertions.py` are shared code — the determinism guarantees are not re-implemented per platform, which is the whole point of normalizing every backend's tree into the common `Element`.

### Phase 0 — abstract the seams

This item is **Phase 0** of the platform-expansion rollout: the generalization that has to happen before any second platform can land.

| Phase | Scope | Why this order |
|---|---|---|
| **0 — Abstract the seams** | Extract an `Environment` Protocol (today `simctl` is concrete); add `platform` to config + platform-scoped backend registry; audit `runner.py` / `orchestrator.py` for leaked iOS-isms | Pays the generalization cost once, with no device involved |

Concretely, Phase 0 is three pieces of work:

- **Extract an `Environment` Protocol.** `simctl` is concrete and iOS-specific today. The protocol (erase / boot / launch / deeplink / screenshot) must fit a browser context — where "erase" and "boot" are nearly no-ops — without leaking device assumptions.
- **Add `platform` to config + a platform-scoped backend registry.** The registry slice ([BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)) already routes `--backend` / `backend:` through a platform token; the remaining work is the explicit `platform` config field and wiring it to environment-manager selection.
- **Audit `runner.py` / `orchestrator.py` for leaked iOS-isms.** The "unchanged" column above is a claim that has to be verified — the first abstraction pass is what forces any latent iOS-specific assumption into the open.

The per-platform backends that build on this (Web first, then Android) are tracked separately: Web in [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), Android in [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md).

### The `Environment` seam (implementation)

The audit confirms the "unchanged core" claim: no iOS assumption leaked into `resolve_unique`, `assertions`, the orchestrator loop, or the reporter. The only platform leakage is **control flow that branches on the actuator name**, concentrated in three places — `runner/launch.py` (the per-run bring-up fork, `if actuator == "playwright"`), `runner/pool.py` (the parallel-lane `is_web` split plus `cast(PlaywrightDriver, …)` for the web-only network/relaunch), and `cli/commands/crawl.py` (the reset/recovery split). The web v1 shortcut ([BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) kept its lifecycle inside the driver and reached it through those branches.

The fix is a single Protocol the runner calls instead of branching:

```python
class Environment(Protocol):
    def start(self, eff, pre, *, extra_env=None, record_video_dir=None) -> Driver: ...
```

`start` owns a platform's whole per-run startup and returns a ready-to-poll driver — so the caller never knows whether that meant a `simctl` device sequence or a fresh browser context. `IosEnvironment` runs the simctl sequence (erase → boot → install → launch → deeplink) then builds the idb driver; `WebEnvironment` builds the Playwright driver and `navigate()`s (the web-only call now confined to that class, off the runner); `FakeEnvironment` is a no-op. `environment_for(actuator, udid, env_run)` is the factory. A future `AndroidEnvironment` ([BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md)) implements the same Protocol over `adb` (`pm clear` → AVD → `am start` → deeplink intent).

Phase 0 lands incrementally so each PR stays small and the gate stays green:

- **Slice 1 (shipped):** the `Environment` Protocol + `IosEnvironment` / `WebEnvironment` / `FakeEnvironment` + `environment_for`, and `launch_driver` delegates to it — removing the `actuator == "playwright"` fork in `launch.py`.
- **Slice 2:** fold `runner/pool.py`'s `is_web` lease split and the `cast(PlaywrightDriver, …)` network/relaunch behind the Protocol (the per-scenario relaunch and per-release teardown become Environment methods).
- **Slice 3:** fold `cli/commands/crawl.py`'s reset/recovery split.
- **Slice 4:** the explicit `platform` config discriminator. Today the actuator token already implies the platform (`backends.PLATFORMS`); this slice adds `platform` to `defaults` / `apps.<name>` / `Effective` and validates that the platform's identifier (`bundleId` / `baseUrl` / `package`) is present.

## Alternatives considered

- **Per-platform forks of the runner.** Rejected on the core premise: re-implementing `resolve_unique` / `assertions.py` per platform would duplicate exactly the determinism guarantees that normalizing into a common `Element` tree exists to share. The whole value of the abstraction is that the deterministic spine stays single-sourced.
- **One YAML run thrice as the portability model.** Rejected as dishonest about real apps: three apps for the same product rarely share screens. The chosen model is per-platform scenarios over one shared DSL/runner/toolchain, with cross-platform reuse opt-in through shared id namespaces.
- **Defer the abstraction until a second platform is actually built.** Rejected because the first platform pays the abstraction cost regardless; doing it as a device-free Phase 0 surfaces leaked iOS-isms at the lowest cost and de-risks the second-platform work.

## References

- [DESIGN §5](../../../DESIGN.md) (backend-agnostic `Driver` interface), [DESIGN §7 / §7.3](../../../DESIGN.md) (stable-id convention)
- `bajutsu/drivers/` (`base.py` `resolve_unique`, `idb.py`), `bajutsu/backends.py` (platform registry)
- [architecture.md](../../../docs/architecture.md)
- Related items: [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md) (Android backend), [BE-0041](../../implemented/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) (web Playwright backend), [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md) (platform backend registry), [BE-0010](../../implemented/BE-0010-update-scope-statement/BE-0010-update-scope-statement.md) (scope-statement update), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) (XCUITest backend)
