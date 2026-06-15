**English** · [日本語](ja/concepts.md)

# Core concepts and design principles

> Every module in Bajutsu follows from a small set of principles. Read this before the
> implementation details to understand why things are shaped the way they are. The
> full design rationale is in [`DESIGN.md`](../DESIGN.md).

Related: [architecture](architecture.md) · [selectors](selectors.md) · [run-loop](run-loop.md)

---

## 1. AI is the author and the investigator, never the judge

The non-determinism, cost, and latency of an LLM (large language model) must **not enter the CI
(continuous integration) gate**. This is the top-level constraint, and it directly produces the
two-tier structure (below).

| Command | Tier | AI | How pass/fail is decided |
|---|---|---|---|
| `record` | Tier 1 | author | explores and proposes the next move → writes a deterministic scenario ([recording](recording.md)) |
| `run` | Tier 2 | **none** | each step is act → wait → verify; pass/fail comes only from the `expect` machine assertions ([run-loop](run-loop.md)) |
| `codegen` | — | none | structural mapping of a scenario to XCUITest ([codegen](codegen.md)) |

The `run` path contains no `anthropic` call at all. The single exception is `--dismiss-alerts`
(it visually dismisses OS system alerts); that prepares the environment rather than deciding
pass/fail, and runs only when explicitly opted in
([the alert guard](recording.md#dismissing-system-alerts-automatically)).

## 2. Two tiers (Tier 1 / Tier 2)

- **Tier 1 = AI live operation**: exploration and authoring. Flexible but non-deterministic.
  The artifact (the scenario YAML) is AI-independent and, from then on, owned and edited by humans.
- **Tier 2 = deterministic runner**: CI regression. The same scenario follows the same path
  every time.

Both use the same `observe → act → verify` loop, but the layer where AI participates is
strictly separated.

> The scenario is just YAML, so humans extend it with authoring features that never involve
> AI: reusable `use` components, data-driven rows (`data` / `dataFile`), secret variables
> (`${secrets.X}`), `tag`s for selection, `setLocation` / `push` device steps, and file- /
> scenario-level `description`s ([scenarios](scenarios.md)).

## 3. Determinism first (four concrete mechanisms)

Bajutsu's "deterministic" behavior is enforced by the structure of the code.

1. **An ambiguous selector fails immediately.** When a single action's target matches 2+
   elements, Bajutsu raises `AmbiguousSelector` instead of tapping whatever matched first
   ([selectors](selectors.md#resolution-semantics)). Ruling out non-determinism structurally
   is the most important of these four mechanisms.
2. **Condition waits only; no fixed sleep.** Waiting polls `query()` until a condition holds.
   A `timeout` is mandatory (no infinite waits) ([run-loop](run-loop.md#waits-condition-waits-only)).
3. **Start from a clean environment.** Each test, by default, `simctl erase`s before boot/launch,
   cutting off contamination from the previous test. State is injected via launch env / deeplink
   ([drivers](drivers.md#environment-management-simctl)).
4. **Pass/fail is machine-checkable only.** There is no "looks like it passed" judgment. The
   eight machine assertions are `exists`/`value`/`label`/`count`/`enabled`/`disabled`/`selected`/`request`
   ([selectors](selectors.md#assertion-evaluation)).

> Note the scope: accessibility identifiers only stabilize the **determinism of selection**.
> Flakiness from timing, state, or the network is handled separately by waits, the environment,
> and (in the future) mocks.

## 4. Stable selectors (prefer accessibilityIdentifier)

Always write selectors by **`accessibilityIdentifier` (non-localized, unique, data-derived)**.
The reason is to eliminate flakiness from layout changes, translation, and coordinate drift.
`label` is for VoiceOver / AI semantic understanding; because its wording changes with
localization, it is not used as a selector (only as an auxiliary / disambiguator). The naming
convention (`<namespace>.<element>`) is in
[configuration](configuration.md#identifier-naming-convention).

## 5. The stability ladder

UI actions are attempted **most-stable-first**, where "most stable" refers to selection (which
element), not actuation. idb actuates by coordinate tap at the frame center regardless, so what
changes between rungs is how the element is chosen. The lower the rung, the more fragile.

| Rung | Selection (which element) | Stability |
|---|---|---|
| 1 | resolve uniquely by `id` | most stable (independent of layout / translation / coordinates) |
| 2 | resolve by `label` / `traits` | weak to localization |
| 3 | `index` / raw coordinates | breaks on layout changes. Last resort |

> Actuation is always a frame-center coordinate tap: idb exposes no semantic tap, so the run loop
> resolves the unique element and taps its frame center.

The **actuator (the backend that performs actions) is the first available backend in the list**; it
is fixed once at the start of a run and held for the whole run (to avoid the non-determinism of two
drivers operating one device). The `backend` list is still written most-stable-first, but idb is the
only registered backend today, so it is always the actuator — the list is kept so another backend can
be added later. Selection is always by `id`, so scenarios do not change
([drivers](drivers.md#backend-selection-and-the-actuator)).

## 6. App-agnostic (push differences into config)

The tool core, the drivers, and the runner do not depend on any app. To target a new app, you
change **the app-side preparation (adding identifiers, etc.) and one `apps.<name>` config entry**
— nothing else. Each app's determinism is guaranteed by the same implementation convention
([onboarding in configuration](configuration.md#onboarding-a-new-app)).

## 7. Evidence is rules (fire repeatedly)

The request "capture evidence **every time** a particular action happens" is stored not as a
one-shot instruction but as a **trigger-based rule** (`capturePolicy`). This way the same
evidence reproduces without AI on the second run onward ([evidence](evidence.md)).

---

### Where each principle shows up in the code (quick map)

| Principle | Main implementation |
|---|---|
| Ambiguous selector fails fast | `drivers/base.py` `resolve_unique` |
| Condition waits only | `orchestrator.py` `_wait` |
| Clean environment | `runner.py` `launch_driver` · `env.py` `Env.erase` |
| Machine assertions | `assertions.py` |
| Stability order / actuator | `backends.py` `select_actuator` · each `drivers/*.py` `capabilities()` |
| App-agnostic | `config.py` `resolve` → `Effective` |
| Evidence rules | `orchestrator.py` `_collect_captures` · `evidence.py` |
