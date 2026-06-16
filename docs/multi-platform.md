**English** · [日本語](ja/multi-platform.md)

# Extending to Android and Web (multi-platform)

> Forward-looking design — **not implemented yet**. Today Bajutsu is scoped to the **iOS
> Simulator only** ([DESIGN §1](../DESIGN.md), [README](../README.md)). This page describes a
> concrete strategy and design for reusing the existing abstractions to also drive **Android**
> (emulator) and **Web** (browser) — what stays unchanged, what each new backend adds, and the
> order to build it in. It is the long-form of [roadmap → Platform expansion](roadmap/README.md#platform-expansion-android--flutter).

Related: [drivers](drivers.md) · [selectors](selectors.md) · [concepts](concepts.md) · [configuration](configuration.md) · [vision](vision.md)

---

## The abstraction is already platform-shaped

Bajutsu's core was built behind a backend-agnostic `Driver` interface intentionally
([drivers](drivers.md), [DESIGN §5](../DESIGN.md)). The deterministic spine — scenario DSL
(domain-specific language), selector resolution, machine assertions, the orchestrator loop, the
evidence subsystem, the reporter — never names iOS. Only **three seams** are iOS-specific today:

1. **The actuator** (`drivers/idb.py`) — drives the UI via `idb` + frame-center coordinate taps.
2. **The environment manager** (`env.py`) — `simctl` boot / erase / launch / openurl.
3. **The stable-id convention** (`accessibilityIdentifier`, [§7](../DESIGN.md)) — the app-side
   source that makes `Selector.id` resolution deterministic.

Adding multi-platform support means **adding a new triple** (actuator + environment + id convention) per
platform, while the deterministic core stays byte-for-byte the same. This is the same move the
design already anticipates for a second iOS actuator (XCUITest) — generalized across OSes.

### What stays unchanged vs. what each platform adds

| Layer | Status when adding a platform |
|---|---|
| Scenario DSL & grammar ([scenarios](scenarios.md) / [dsl-grammar](dsl-grammar.md)) | **Unchanged.** Steps, waits, assertions, capture tokens are platform-neutral |
| Selector model & resolution (`drivers/base.py` `resolve_unique`) | **Unchanged.** 0/1/2+ semantics and ambiguous-fails-fast are backend-agnostic |
| Machine assertions (`assertions.py`) | **Unchanged.** `exists`/`value`/`label`/`count`/`enabled`/… evaluate the normalized `Element` tree |
| Orchestrator loop (`orchestrator.py`) | **Unchanged.** observe → act → verify; condition-waits poll `query()` |
| Evidence subsystem (`evidence.py`, capturePolicy, `manifest.json`) | **Mostly unchanged.** Capture *tokens* stay; *providers* gain per-platform sources (below) |
| Reporter (`report.py`) | **Unchanged.** manifest / JUnit / HTML are platform-neutral |
| Config layering (`config.py`, `defaults × apps`) | **Extended.** A `platform` field + per-platform target fields (below) |
| **Driver backend** (`drivers/*.py`, `capabilities()`) | **New per platform** — the actuator |
| **Environment/lifecycle manager** (`env.py` peer) | **New per platform** — boot / clean / launch / deeplink |
| **doctor convention checks** (`doctor.py`) | **New per platform** — the §7-equivalent score |
| **codegen emitter** (`codegen.py`) | **New per platform** — the native test it transpiles to |

The first new platform is the most expensive to add because it forces any latent iOS-specific assumptions in the "unchanged" column out into the open. The second platform is cheaper. The rollout order is chosen to pay that cost where it is smallest — see [Phasing](#phasing-build-web-first).

---

## The crux: selector portability

A scenario is portable across platforms **only to the extent its selectors are by `id`**
([concepts §4–5](concepts.md#4-stable-selectors-prefer-accessibilityidentifier)). Each platform
has a native equivalent of `accessibilityIdentifier` — a **non-localized, developer-assigned,
data-derived** handle — and the per-platform id convention ([§7.3](../DESIGN.md)) maps onto it:

| `Selector` field | iOS | Android | Web |
|---|---|---|---|
| `id` (primary) | `accessibilityIdentifier` | `resource-id` (Compose: `Modifier.testTag` + `testTagsAsResourceId`) | `data-testid` |
| `label` (auxiliary) | `accessibilityLabel` | `content-desc` / `text` | accessible name / `aria-label` / text |
| `traits` (role filter) | UI traits (`button`, `link`, …) | widget class (`android.widget.Button`) | ARIA (Accessible Rich Internet Applications) `role` (`button`, `link`, `textbox`) |
| `value` | accessibility value | `text` / checked state | input `value` / `aria-*` |

The key property: **the YAML selector `{ id: settings.reindex }` is already platform-neutral.**
What differs is *which app-side attribute the backend reads to satisfy it* — and that lives
entirely inside the new Driver, never in the scenario.

**Honest stance on shared scenarios.** Three apps for the same product rarely have identical
screens, so the realistic model is **per-platform scenarios that share one DSL, one runner, and
one toolchain** — not one YAML run thrice. Cross-platform *reuse* is then an **opt-in** for the
slices that genuinely match, expressed through the existing **reserved/shared id namespaces**
(`auth.*`, `nav.*`, [§7.3](../DESIGN.md)): a login `setup:` component ([scenarios](scenarios.md))
can run on all three platforms iff those ids are kept in parity. The tool provides *portable tooling*
and *portable scenarios* only where the team maintains an id contract — a single YAML is not automatically a tri-platform test.

---

## Per-platform design

### Web — Playwright (recommended first)

| Seam | Choice |
|---|---|
| **Actuator** | **Playwright (Python)** — `playwright` is a Python package, headless, cross-browser. Selects by `getByTestId` / `getByRole`; **clicks semantically** (no coordinates) |
| **Environment** | A **`BrowserContext`**, not a device. Clean state = a fresh incognito `browser.new_context()` (the `erase` equivalent, but ~free). "launch" = `page.goto(url)`; "deeplink" = a URL; launch env = query params / seeded `localStorage` / cookies |
| **id convention** | `data-testid` (non-localized, developer-set). ARIA `role` → `traits`; accessible name → `label` |
| **Evidence providers** | screenshot = `page.screenshot`; video = context video recording; **`network` = native route interception** (first backend with it); `deviceLog` ≈ console logs / page errors |
| **codegen target** | Playwright test (TypeScript) or `pytest-playwright` |

Playwright's capabilities lead the capability gradient: it provides `semanticTap`, native `conditionWait`
(auto-waiting), `network` (request stubbing **and** observation in one API), and emulated
`multiTouch`. This raises the ceiling of the capability model and — see below — makes Web
the lowest-cost place to prove the abstraction.

### Android — adb + UI Automator

| Seam | Choice |
|---|---|
| **Actuator** | **`adb` + `uiautomator dump`** — `uiautomator dump` yields an XML tree; actuation is `adb shell input tap x y` at the element's bounds center. **Coordinate-based, no semantic tap — a near-exact twin of idb.** (A richer Appium UiAutomator2 path could add semantic actions later) |
| **Environment** | `adb`: clean state = `pm clear <package>` (the `erase` equivalent); boot via emulator/AVD (Android Virtual Device); launch = `am start`; deeplink = `am start -a android.intent.action.VIEW -d <url>`; launch args = intent extras |
| **id convention** | `resource-id` (XML `android:id`; Jetpack Compose `Modifier.testTag` surfaced as `resource-id` via `testTagsAsResourceId`). `content-desc`/`text` → `label`; widget class → `traits` |
| **Evidence providers** | screenshot = `adb exec-out screencap`; video = `adb shell screenrecord`; `deviceLog` = `adb logcat` (filtered by tag/pid); `network` = no native monitor → same mock story as iOS |
| **codegen target** | Espresso or UI Automator (Kotlin/Java) |

Android is the **architectural twin of idb**: subprocess-driven, coordinate actuation, a
transiently-empty tree during transitions (so it reuses idb's *resolve-with-retry, fail-ambiguity-fast*
pattern, [drivers](drivers.md#idb)). It validates that the iOS-specific parts were really isolated to the
three seams, with almost no new shape.

### Flutter / React Native / WebView hybrids (later)

Cross-rendered UIs (Flutter draws its own pixels; hybrids embed a WebView) often don't surface
elements in the OS a11y (accessibility) tree. These need a **semantics bridge** rather than a new
OS actuator: Flutter's semantics tree (`integration_test` / VM Service / Flutter Driver), or a
WebView→DOM (Document Object Model) bridge for the embedded-web case. Treat these as a **third phase**, after the two native trees
prove the abstraction.

### Extended capability matrix

The capability tokens ([drivers](drivers.md#capabilities-capability)) already express the spread —
the new backends slot in without inventing concepts:

| Capability | idb (iOS) | adb (Android) | Playwright (Web) | fake |
|---|:--:|:--:|:--:|:--:|
| `query` / `elements` / `screenshot` | ✅ | ✅ | ✅ | ✅ |
| `semanticTap` | — | — | ✅ | ✅ |
| `conditionWait` (native) | — | — | ✅ | ✅ |
| `network` (native) | — | — | ✅ | — |
| `multiTouch` | — | — | ✅ (emulated) | ✅ |

idb and Android sit at the lean end (coordinate actuation, mocked network); Playwright at the rich
end (semantic, native network). That an unmodified capability model spans both extremes is evidence
the abstraction holds.

---

## Configuration changes

`apps.<name>` ([configuration](configuration.md)) gains a **`platform`** discriminator and
per-platform target fields; the deterministic resolution order (`defaults < app < scenario`) is
unchanged.

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

`platform` selects which **environment manager** and **backend registry** are in play; the rest of
the schema (namespaces, redact, setup, capture) stays shared.

> **Landed already (the selector slice).** `bajutsu/backends.py` now keys off a platform
> registry: `PLATFORMS = {ios: (idb,), android: (adb,), web: (playwright,), fake: (fake,)}`, and
> `--backend` / `backend:` accept a **platform token** (`ios`/`android`/`web`/`fake`) as well as a
> bare actuator. A platform expands to its actuators; `select_actuator` returns the first
> *implemented and available* one (`idb` / `fake` today), and `android` / `web` raise a clear
> "not implemented yet". What remains for a real second platform is the rest of the triple — the
> per-platform **environment manager** (a `simctl` peer) and the **actuator driver** (`adb` /
> `playwright`) — plus an optional explicit `platform` config field. See
> [drivers → backend selection](drivers.md#backend-selection-and-the-actuator).

---

## Determinism is preserved per platform

The four mechanisms ([concepts §3](concepts.md#3-determinism-first-four-concrete-mechanisms)) hold
on every backend — only their *implementation* differs:

| Principle | iOS | Android | Web |
|---|---|---|---|
| Ambiguous selector fails fast | `resolve_unique` (shared) | `resolve_unique` (shared) | `resolve_unique` (shared) |
| Condition waits, no fixed sleep | poll `query()` | poll `uiautomator dump` | Playwright auto-wait + poll |
| Clean environment per test | `simctl erase` | `pm clear` | fresh `new_context()` |
| Pass/fail machine-checkable only | normalized `Element` | normalized `Element` | normalized `Element` |

`resolve_unique` and `assertions.py` are shared code — the determinism guarantees are
not re-implemented per platform, which is the whole point of normalizing every backend's tree into
the common `Element`.

---

## Phasing (build Web first)

| Phase | Scope | Why this order |
|---|---|---|
| **0 — Abstract the seams** | Extract an `Environment` Protocol (today `simctl` is concrete); add `platform` to config + platform-scoped backend registry; audit `runner.py`/`orchestrator.py` for leaked iOS-isms | Pays the generalization cost once, with no device involved |
| **1 — Web (Playwright)** | First real second platform | **Runs on Linux — no Mac needed**, so it fits the *existing* `make check` / CI (continuous integration) gate ([ci](ci.md)). Native network + video + semantic actions exercise the **rich** end of `capabilities()`. Lowest friction, broadest reach, cheapest proof that the core is platform-neutral |
| **2 — Android (adb + UI Automator)** | The idb twin | Coordinate model mirrors idb almost exactly, so little new shape; emulator runs on Linux CI (KVM, the Linux Kernel Virtual Machine). Exercises the **lean** end of `capabilities()` |
| **3 — Hybrid/cross-rendered** | Flutter / React Native / WebView | Needs a semantics bridge, not a new OS actuator — defer until the two native trees are solid |
| **Cross-cutting** | per-platform `doctor` score, per-platform codegen emitter, **scope-statement updates** | Done alongside each phase |

**Why Web before Android**, even though Android is the closer architectural twin: Web is the only
platform that needs **no macOS and no device emulator**, so it fits inside the current Linux gate
from day one — reducing the risk of the question "is the core truly platform-neutral?" at the lowest
possible cost. Android then confirms the *lean/coordinate* path on top of an already-generalized
core.

---

## Scope-statement updates this triggers

Multi-platform is a **strategic scope change**, not just code. When Phase 1 lands, update in the
same change:

- **[DESIGN §1](../DESIGN.md)** "やること / やらないこと" — iOS-Simulator-only → multi-platform;
  move "実機 / クラウドデバイスファーム" reasoning where relevant.
- **[README](../README.md) / [README.ja](../README.ja.md)** product one-liner and core-principles.
- **[architecture status](architecture.md#implementation-status)** — register the new backend.
- **docs nav** — both [`docs/README.md`](README.md) and [`docs/ja/README.md`](ja/README.md).

Keep the **prime directives intact**: determinism-first, app-agnostic, and *AI is never the judge*
apply identically on Android and Web. No new platform may introduce an LLM into the Tier-2 gate.

---

## Open questions / risks

- **Environment abstraction shape.** `simctl` is iOS-specific; the right `Environment` Protocol
  (erase/boot/launch/deeplink/screenshot) must fit a browser context (where "erase" and "boot" are
  nearly no-ops) without leaking device assumptions.
- **Compose / cross-rendered id surfacing.** `testTagsAsResourceId` must be enabled app-side, the
  same way iOS requires `accessibilityIdentifier`; the §7 convention and `doctor` score must teach
  this per platform.
- **CI cost.** Web is free-ish on Linux; Android emulators on CI need KVM and are heavier; the
  hosted-pool economics ([cloud-hosting](cloud-hosting.md)) shift when iOS is no longer the only
  device that needs a Mac.
- **codegen breadth.** Each platform needs its own emitter; start with the common subset and fall
  back to `// TODO` for unmapped constructs, as the XCUITest emitter already does ([codegen](codegen.md)).
- **Selector parity drift.** Shared `auth.*`/`nav.*` namespaces only stay portable if every
  platform's app keeps them in sync — `doctor` must check the id contract per platform.
