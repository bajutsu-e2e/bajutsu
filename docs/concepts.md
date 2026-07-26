**English** · [日本語](ja/concepts.md)

# Core concepts and design principles

> Every module in Bajutsu follows from a small set of principles. Read this before the
> implementation details to understand why things are shaped the way they are. The
> full design rationale is in [`DESIGN.md`](../DESIGN.md).

Related: [architecture](architecture.md) · [selectors](selectors.md) · [run-loop](run-loop.md)

---

## 1. AI is the author and the investigator, never the judge

The non-determinism, cost, and latency of an LLM (large language model) must **not enter the CI
(continuous integration) gate**. This prohibition is the top-level constraint, and it directly produces the
two-tier structure (below).

| Command | Tier | AI | How pass/fail is decided |
|---|---|---|---|
| `record` | Tier 1 | author | explores and proposes the next move → writes a deterministic scenario ([recording](recording.md)) |
| `run` | Tier 2 | **none** | each step is act → wait → verify; pass/fail comes only from the `expect` machine assertions ([run-loop](run-loop.md)) |
| `codegen` | — | none | structural mapping of a scenario to XCUITest ([codegen](codegen.md)) |

The `run` path contains no `anthropic` call at all. The single exception is `--alert-handling`
(it visually dismisses OS system alerts); it prepares the environment rather than deciding
pass/fail, and runs only when explicitly opted in
([the alert guard](recording.md#dismissing-system-alerts-automatically)).

## 2. Two tiers (Tier 1 / Tier 2)

- **[Tier 1](glossary.md#the-two-tiers) = AI live operation**: exploration and authoring. Flexible
  but non-deterministic. The artifact (the scenario YAML) is AI-independent and, from then on,
  owned and edited by humans.
- **[Tier 2](glossary.md#the-two-tiers) = deterministic runner**: CI regression. The same scenario
  follows the same path every time.

Both use the same `observe → act → verify` loop, but the layer where AI participates is
strictly separated.

> The scenario is just YAML, so humans extend it with authoring features that never involve
> AI: reusable `use` components, data-driven rows (`data` / `dataFile`), secret variables
> (`${secrets.X}`), `tag`s for selection, `setLocation` / `push` device steps, and file- /
> scenario-level `description`s ([scenarios](scenarios.md)).

## 3. Determinism first (four concrete mechanisms)

The structure of the code enforces Bajutsu's "deterministic" behavior.

1. **An ambiguous selector fails immediately.** When a single action's target matches two or more
   elements, Bajutsu raises `AmbiguousSelector` instead of tapping whatever matched first
   ([selectors](selectors.md#resolution-semantics)). Of these four mechanisms, ruling out
   non-determinism structurally matters most.
2. **Condition waits only; no fixed sleep.** Waiting polls `query()` until a condition holds.
   A `timeout` is mandatory (no infinite waits) ([run-loop](run-loop.md#waits-condition-waits-only)).
3. **Start from a clean environment.** Each test, by default, `simctl erase`s before boot/launch,
   cutting off contamination from the previous test. State is injected via launch env / deeplink
   ([drivers](drivers.md#environment-management-simctl)).
4. **Pass/fail is machine-checkable only.** There is no "looks like it passed" judgment. The
   machine assertions are `exists`/`value`/`label`/`count`/`enabled`/`disabled`/`selected`/`request`/`visual`
   ([selectors](selectors.md#assertion-evaluation)).

> The scope is narrow: stable identifiers only stabilize the **determinism of selection**.
> Flakiness from timing, state, or the network is handled separately by waits, the environment,
> and `mocks`.

## 4. Stable selectors (prefer accessibilityIdentifier)

Always write selectors by a **non-localized, unique, data-derived id**. On iOS that is
`accessibilityIdentifier`; on the web it is `data-testid`; on Android it is `resource-id`. The
selector YAML is the same across backends — only the attribute the backend reads to satisfy it
differs. The reason is to eliminate flakiness from layout changes, translation, and coordinate drift.
`label` is for VoiceOver / AI semantic understanding; because its wording changes with
localization, it is not used as a selector (only as an auxiliary / disambiguator). The naming
convention (`<namespace>.<element>`) is in
[configuration](configuration.md#identifier-naming-convention).

## 5. The stability ladder

UI actions are attempted **most-stable-first**, where "most stable" refers to selection (which
element), not actuation. idb (the iOS actuator) actuates by coordinate tap at the frame center
regardless, so what changes between rungs is how the element is chosen. The lower the rung, the
more fragile.

| Rung | Selection (which element) | Stability |
|---|---|---|
| 1 | resolve uniquely by `id` | most stable (independent of layout / translation / coordinates) |
| 2 | resolve by `label` / `traits` | weak to localization |
| 3 | `index` / raw coordinates | breaks on layout changes. Last resort |

> Actuation is always a frame-center coordinate tap: idb exposes no semantic tap, so the run loop
> resolves the unique element and taps its frame center.

The **actuator** — the backend that performs actions — is the first available one in the
most-stable-first `backend` list; it is fixed once at the start of a run and held for the whole run
(to avoid the non-determinism of two drivers operating one device). The full
[driver / backend / actuator / platform](glossary.md#driver-backend-actuator-platform)
relationship, and which actuators each platform expands to, is in the glossary. Selection is always
by `id`, so scenarios do not change
([drivers](drivers.md#backend-selection-and-the-actuator)).

## 6. App-agnostic (push differences into config)

The tool core, the drivers, and the runner do not depend on any app. To target a new app, you
change **the app-side preparation (adding identifiers, etc.) and one `targets.<name>` config entry**
— nothing else. The same implementation convention guarantees each app's determinism
([onboarding in configuration](configuration.md#onboarding-a-new-target)).

The same move makes Bajutsu **platform-agnostic**: a platform is a **backend** behind the `Driver`
interface (the actuator, §5). Web (`playwright`) and Android (`adb`) are new targets built this way
— each a new backend; the scenario format, selector resolution, assertions, the orchestrator,
evidence, and the reporter stay byte-for-byte the same. Per-platform differences live only in the
backend and config.

## 7. Evidence is rules (fire repeatedly)

The request "capture evidence **every time** a particular action happens" is stored not as a
one-shot instruction but as a **trigger-based rule**
([`capturePolicy`](glossary.md#evidence-capturepolicy-trace-triage)). This way the same
evidence reproduces without AI on the second run onward ([evidence](evidence.md)).

---

### Where each principle shows up in the code (quick map)

| Principle | Main implementation |
|---|---|
| Ambiguous selector fails fast | `drivers/base.py` `resolve_unique` |
| Condition waits only | `orchestrator/waits.py` `_wait` |
| Clean environment | `runner/launch.py` `launch_driver` · `simctl.py` `Env.erase` |
| Machine assertions | `assertions/` |
| Stability order / actuator | `backends.py` `select_actuator` · each `drivers/*.py` `capabilities()` |
| App-agnostic | `config/resolve.py` `resolve` → `Effective` |
| Evidence rules | `orchestrator/loop.py` `_collect_captures` · `evidence/core.py` |
