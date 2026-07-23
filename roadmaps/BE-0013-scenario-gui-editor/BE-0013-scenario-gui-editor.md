**English** · [日本語](BE-0013-scenario-gui-editor-ja.md)

# BE-0013 — Scenario GUI editor

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0013](BE-0013-scenario-gui-editor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0013") |
| Implementing PR | [#385](https://github.com/bajutsu-e2e/bajutsu/pull/385), [#387](https://github.com/bajutsu-e2e/bajutsu/pull/387) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Visually edit the scenario YAML and assertion DSL (domain-specific language). Select an element on a screenshot to resolve a selector, with integration to the doctor score.

## Motivation

A scenario is just YAML, and that is the point — humans own it after the AI writes the first draft. But editing it by hand asks the author to hold two things in their head at once: the scenario grammar (steps, waits, the assertion DSL) and the app's stable selectors. The hardest part is the selector: to write `tap: { id: settings.toggle }` correctly you must already know the element's `accessibilityIdentifier`, which means reading a `doctor` dump or the live element tree by hand. The `serve` UI (BE-0011) already exposes a raw YAML textarea; this proposal turns that into structured editing where the screenshot is the source of truth: click the element you mean, and the editor resolves and inserts the right selector, with the `doctor` convention score telling you how stable that choice is. It lowers the cost of the human-owned edit loop without ever moving authorship into the runner.

## Detailed design

The editor lives in the existing `serve` web UI as an enrichment of the scenario view, not a new surface. It has two coupled panes: a structured view of the scenario (steps and the assertion DSL, editable field by field) and the screenshot of the screen each step acts on. YAML stays the canonical form — the editor reads and writes the same `*.yaml` through the existing scenario load/save path, so a round-trip through the editor and a hand-edit in `$EDITOR` are interchangeable and reviewable in a PR.

### The element picker is one shared component

The point → element → selector resolver is **the same component [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) introduces** — its pure `bajutsu/capture.py` core (`hit_test(elements, point)` over `_contains` frame containment, and `resolve_capture(elements, point, namespaces) -> selector + doctor rung + ambiguity`) plus a shared `serve.js` screenshot-overlay picker. The editor and capture call the *same* resolver; only **where the element tree and screenshot come from** differs:

- **Capture (BE-0012):** a live `driver.query()` + `driver.screenshot()` taken at mark time — interactive, needs a booted-device session.
- **Editor (this item):** the artifacts the run already captured — per step, `runs/<runId>/<stepId>/elements.json` (`evidence.write_elements`) and `after.png` (the `screenshot` capture kind), where `stepId` is `<scenarioId>/<step name or stepN>` (e.g. `00-s/step0`, the key `orchestrator/loop.py` forms and `manifest.json` records). **Offline, no live device, deterministic.**

So whichever of BE-0012 / BE-0013 lands first introduces the resolver and the other reuses it — never two copies. (The `el → Selector` stability ladder is already implemented three times in `crawl` / `crawl_repro` / capture, so BE-0012 already plans to factor it into one helper; the picker builds on that.)

Clicking a point on a step's screenshot maps displayed-pixel → normalized `[0,1]` → points (the same scaling `crawl.Action.perform`'s `tap_point` uses), POSTs it, and the server runs `resolve_capture` against that step's `elements.json`. It offers the most-stable selector — `id` first, down the ladder (`label` / `traits`) only when no identifier is present, exactly as `resolve_unique` would — shown with its `doctor` score so the author sees a stable rung vs. a fragile one (a coordinate fallback is flagged). If the point resolves to more than one element, the picker **surfaces the ambiguity** and asks the author to narrow it (`within` / `index`) rather than silently picking one — the runner's "ambiguous fails" rule, surfaced at authoring time.

### Structured editing over canonical YAML

The structured pane is a **view over the YAML**, not a separate model: the editor parses the scenario with the existing load path into steps + `expect` assertions and renders them field by field; the picker writes a resolved selector into the field the author is editing. There is no hidden editor state — the YAML is the single source of truth, so the structured view and a hand-edit can never disagree.

Each step is paired with the screen it acts on through its `stepId` — `<scenarioId>/<step name or stepN>`, the key the run records in `manifest.json` and writes its evidence directory under — rather than guessing a positional path. The author edits a scenario **in the context of a selected run** — the report they are already looking at; when a scenario has no run yet, the editor degrades to raw-field editing (no screenshot to pick against) rather than blocking.

### Save path and seams

Saving goes through the existing author-owned write path, `serve/scenarios.py:ScenarioScope.save()`, and **validates with `load_scenario_file` before writing**, so the editor can never persist a scenario the runner would reject. Concretely: routes in `serve/handler.py` (with the FastAPI `server/app.py` mirror) — load a scenario + its run's per-step artifacts for editing, resolve a picked point to a selector, and save; orchestration in `serve/operations.py`; the two-pane UI + picker overlay in `bajutsu/templates/serve.js`, reusing the crawl report's screenshot-overlay precedent. Unlike capture, the editor holds **no live driver across requests** — it reads captured artifacts statelessly, keeping BE-0011's stateless shell-out model.

### Determinism, app-agnosticism, and the gate

The editor stays Tier 1 and app-agnostic: it reads `targets.<name>` (the app, its scenarios dir, the identifier namespaces feeding the `doctor` score) and the artifacts a run already produces. Selection is structural (point-in-frame plus the `doctor` heuristic) — **no LLM, no `ANTHROPIC_API_KEY`** — so nothing here touches the deterministic `run` / CI gate; it only lowers the cost of the human-owned edit loop.

### Dependency and ordering

The picker's resolver is BE-0012's `capture.py` core (`hit_test` / `resolve_capture`) plus the shared
`el → Selector` stability ladder. BE-0012 is still a proposal, so **this item is gated on that core
existing** — one of two orders, both yielding a single resolver:

- **BE-0012 first** (natural): it introduces `capture.py` and the consolidated ladder; BE-0013 reuses
  them unchanged, supplying its own (artifact-backed) element tree and screenshot.
- **BE-0013 first**: its first slice factors the ladder — today duplicated in `crawl` /
  `crawl_repro` — into `capture.py`'s `resolve_capture`, and BE-0012 later builds its live-capture
  path on the same function.

Either way there is never a second copy of the resolver. The editor's own surface (the two-pane view,
the artifact-backed picker, the save-with-validate route) is independent of which order is chosen, so
the design below stands regardless.

### The editor API

Three author-owned `serve` routes (stdlib `serve/handler.py` + the FastAPI `server/app.py` mirror),
all stateless — no live driver is held across requests, keeping BE-0011's shell-out model:

- **Load for editing** — extend the existing `GET /api/scenario` (`operations.read_scenario`) to also
  return, for a chosen run, each step's captured-artifact handles:
  `{ yaml, steps: [{ stepId, action, fields, elementsUrl, screenshotUrl }] }`, where the URLs point at
  the already-byte-served `runs/<runId>/<stepId>/elements.json` and `after.png`. The run is the report
  the author is viewing; with no run, the handles are null and the pane degrades to raw-field editing.
- **Resolve a pick** — `POST /api/scenario/resolve { target, runId, stepId, point: [x, y] }` →
  `resolve_capture(elements_of(stepId), point, namespaces_of(target))`, returning
  `{ selector, rung, doctorScore, ambiguous, candidates? }`. `point` is normalized `[0,1]` (the client
  maps displayed-pixel → normalized, the scaling `crawl.Action.perform` already uses); the server reads
  that step's `elements.json` and resolves **structurally — no device, no LLM**. An ambiguous hit
  returns `ambiguous: true` with the candidates for the author to narrow (`within` / `index`), never a
  silent pick.
- **Save** — the existing `POST /api/scenario` (`operations.save_scenario` → `ScenarioScope.save()`),
  which already validates with `load_scenario_file` before writing. The editor serializes the
  structured pane back to YAML and saves through this unchanged path, so an editor save and a hand-edit
  are the same write and equally rejectable.

### Validation

Fast-gate (no device, no browser, no LLM):

- *Resolver reuse.* Against a fixed `elements.json` fixture, a point resolves to the expected id-first
  selector with its `doctor` rung, and a point over overlapping frames returns `ambiguous` with its
  candidates — the same assertions BE-0012's resolver tests make (one resolver, one test surface).
- *Save validates.* Saving a structurally-edited-but-invalid scenario is rejected by
  `load_scenario_file` before any bytes are written (no partial file).
- *Route shapes.* The load / resolve / save handlers return the shapes above for a fixture run, and a
  scenario with no run degrades to null artifact handles rather than erroring.

The two-pane overlay rendering in `serve.js` is exercised through the existing serve UI path (like the
crawl report's screenshot overlay), not the fast gate.

## Alternatives considered

* **Keep the raw YAML textarea only.** That is the BE-0011 baseline. Rejected as the end state: it still requires the author to know selectors by hand and gives no feedback on selector stability — the picker plus `doctor` score is the whole value add.
* **A fully visual, code-free editor that hides the YAML.** Rejected: the YAML being the canonical, hand-editable, PR-reviewable artifact is a core principle. The editor augments the YAML; it must not replace or obscure it.
* **A live, interactive Simulator embedded in the page (pick on the running app).** Rejected for the first cut: it needs a live device per editing session and a streaming pipeline. Picking against the screenshot and element tree the run already captured is offline, cheap, and deterministic; a live picker can come later.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[scenarios.md](../../docs/scenarios.md), [selectors.md](../../docs/selectors.md); `bajutsu/drivers/base.py` (`_contains`, `resolve_unique`, `Selector` / `Element`), `bajutsu/doctor.py` (`score`, `ACTIONABLE_TRAITS`), `bajutsu/evidence.py` (`write_elements` → per-step `elements.json`, the `screenshot` kind → `after.png`), `bajutsu/serve/scenarios.py` (`ScenarioScope.save`), `bajutsu/scenario/load.py` (`load_scenario_file`), `bajutsu/serve/` (`handler.py` routing, `operations.py`), `bajutsu/templates/serve.js` + the crawl report's screenshot-overlay precedent.

**Dependencies / related items:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot plumbing this extends), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (**shares the point → element picker + doctor score — one resolver, two sources**: the editor reads captured artifacts, capture reads a live driver), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the role demarcation across the authoring surfaces).
