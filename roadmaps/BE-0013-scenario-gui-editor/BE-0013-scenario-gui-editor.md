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

The scenario GUI (graphical user interface) editor lets an author visually edit the scenario YAML and the assertion DSL (domain-specific language). The author selects an element on a screenshot, and the editor resolves the selector. The editor integrates with the `doctor` score.

## Motivation

A scenario is plain YAML, and that is the point: humans own the scenario after the AI writes the first draft. Editing by hand asks the author to keep two bodies of knowledge in mind: the scenario grammar (steps, waits, the assertion DSL) and the app's stable selectors.

The selector is the hardest part. To write `tap: { id: settings.toggle }` correctly, the author must already know the element's `accessibilityIdentifier`. That means reading a `doctor` dump or the live element tree by hand.

The `serve` UI ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) already exposes a raw YAML textarea. This proposal turns the textarea into structured editing, with the screenshot as the source of truth. The author clicks the element they mean. The editor resolves the right selector and inserts it, and the `doctor` convention score shows how stable that choice is. The editor lowers the cost of the human-owned edit loop, and authorship never moves into the runner.

## Detailed design

The editor lives in the existing `serve` web UI as an enrichment of the scenario view, not as a new surface. It has two coupled panes:

- A structured view of the scenario (steps and the assertion DSL), editable field by field.
- The screenshot of the screen each step acts on.

YAML stays the canonical form. The editor reads and writes the same `*.yaml` through the existing scenario load/save path. A round-trip through the editor and a hand-edit in `$EDITOR` are interchangeable, and a PR can review both.

### The element picker is one shared component

The resolver that maps a point to an element and then to a selector is **the same component that [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) introduces**. The component has two parts:

- The pure `bajutsu/capture.py` core: `hit_test(elements, point)` over `_contains` frame containment, and `resolve_capture(elements, point, namespaces) -> selector + doctor rung + ambiguity`.
- A shared `serve.js` screenshot-overlay picker.

The editor and capture call the *same* resolver. Only **where the element tree and screenshot come from** differs:

- **Capture (BE-0012):** a live `driver.query()` and `driver.screenshot()` taken at mark time. Capture is interactive and needs a booted-device session.
- **Editor (this item):** the artifacts the run already captured. Per step, these are `runs/<runId>/<stepId>/elements.json` (`evidence.write_elements`) and `after.png` (the `screenshot` capture kind). The `stepId` is `<scenarioId>/<step name or stepN>`, for example `00-s/step0`. `orchestrator/loop.py` forms this key, and `manifest.json` records it. The editor works **offline, needs no live device, and is deterministic**.

Whichever of BE-0012 and BE-0013 lands first introduces the resolver, and the other reuses it. There are never two copies. The `el → Selector` stability ladder is already implemented three times, in `crawl`, `crawl_repro`, and capture. BE-0012 already plans to factor the ladder into one helper, and the picker builds on that helper.

The author clicks a point on a step's screenshot. The client maps displayed pixels to normalized `[0,1]`, then to points, with the same scaling that the `tap_point` of `crawl.Action.perform` uses. The client POSTs the point, and the server runs `resolve_capture` against that step's `elements.json`.

The server offers the most stable selector. It tries `id` first. It goes down the ladder (`label` / `traits`) only when no identifier is present, exactly as `resolve_unique` would. The editor shows the selector with its `doctor` score, so the author can tell a stable rung from a fragile one. A coordinate fallback is flagged.

If the point resolves to more than one element, the picker **surfaces the ambiguity**. It asks the author to narrow the choice (`within` / `index`) and never picks one on its own. This applies the runner's "ambiguous fails" rule at authoring time.

### Structured editing over canonical YAML

The structured pane is a **view over the YAML**, not a separate model. The editor parses the scenario with the existing load path into steps and `expect` assertions, and renders them field by field. The picker writes a resolved selector into the field the author is editing. The editor keeps no hidden state. The YAML is the single source of truth, so the structured view and a hand-edit can never disagree.

Each step is paired with the screen it acts on through its `stepId`, the key the run records in `manifest.json` and writes its evidence directory under. The editor never guesses a positional path.

The author edits a scenario **in the context of a selected run**, which is the report the author is already viewing. When a scenario has no run yet, the editor degrades to raw-field editing, with no screenshot to pick against. It does not block.

### Save path and seams

Saving goes through the existing author-owned write path, `serve/scenarios.py:ScenarioScope.save()`. The save **validates with `load_scenario_file` before writing**, so the editor can never persist a scenario that the runner would reject.

The change touches three places:

- **Routes in `serve/handler.py`**, with the FastAPI `server/app.py` mirror. They load a scenario and its run's per-step artifacts for editing, resolve a picked point to a selector, and save.
- **Orchestration in `serve/operations.py`.**
- **The two-pane UI and picker overlay in `bajutsu/templates/serve.js`**, which reuse the crawl report's screenshot-overlay precedent.

Unlike capture, the editor holds **no live driver across requests**. It reads captured artifacts statelessly, which keeps BE-0011's stateless shell-out model.

### Determinism, app-agnosticism, and the gate

The editor stays [Tier 1](../../docs/glossary.md#the-two-tiers) and app-agnostic. It reads `targets.<name>` (the app, its scenarios dir, and the identifier namespaces that feed the `doctor` score) and the artifacts a run already produces. Selection is structural (point-in-frame plus the `doctor` heuristic), with **no LLM and no `ANTHROPIC_API_KEY`**. Nothing here touches the deterministic `run` / CI gate. The editor lowers the cost of the human-owned edit loop, and does nothing more.

### Dependency and ordering

The picker's resolver is BE-0012's `capture.py` core (`hit_test` / `resolve_capture`) plus the shared `el → Selector` stability ladder. BE-0012 is still a proposal, so **this item is gated on that core existing**. Two orders are possible, and both yield a single resolver:

- **BE-0012 first** (the natural order): BE-0012 introduces `capture.py` and the consolidated ladder. BE-0013 reuses both unchanged and supplies its own artifact-backed element tree and screenshot.
- **BE-0013 first**: the first slice factors the ladder, which is duplicated today in `crawl` and `crawl_repro`, into `resolve_capture` in `capture.py`. BE-0012 later builds its live-capture path on the same function.

Either way, no second copy of the resolver appears. The editor's own surface (the two-pane view, the artifact-backed picker, and the save-with-validate route) is independent of the order chosen, so the design here stands regardless.

### The editor API

The editor adds three author-owned `serve` routes (stdlib `serve/handler.py` and the FastAPI `server/app.py` mirror). All three are stateless: no live driver is held across requests, which keeps BE-0011's shell-out model.

- **Load for editing.** This route extends the existing `GET /api/scenario` (`operations.read_scenario`). For a chosen run, the route also returns each step's captured-artifact handles: `{ yaml, steps: [{ stepId, action, fields, elementsUrl, screenshotUrl }] }`. The URLs point at the already-byte-served `runs/<runId>/<stepId>/elements.json` and `after.png`. The run is the report the author is viewing. With no run, the handles are null, and the pane degrades to raw-field editing.
- **Resolve a pick.** `POST /api/scenario/resolve { target, runId, stepId, point: [x, y] }` calls `resolve_capture(elements_of(stepId), point, namespaces_of(target))`. It returns `{ selector, rung, doctorScore, ambiguous, candidates? }`. The `point` is normalized `[0,1]`: the client maps displayed pixels to normalized values, with the scaling that `crawl.Action.perform` already uses. The server reads that step's `elements.json` and resolves **structurally, with no device and no LLM**. An ambiguous hit returns `ambiguous: true` with the candidates, so the author can narrow the choice (`within` / `index`). The route never picks silently.
- **Save.** This route is the existing `POST /api/scenario` (`operations.save_scenario` → `ScenarioScope.save()`), which already validates with `load_scenario_file` before writing. The editor serializes the structured pane back to YAML and saves through this unchanged path. An editor save and a hand-edit are the same write, and the validation can reject either.

### Validation

The fast gate needs no device, no browser, and no LLM. It covers three cases:

- *Resolver reuse.* Against a fixed `elements.json` fixture, a point resolves to the expected id-first selector with its `doctor` rung. A point over overlapping frames returns `ambiguous` with its candidates. These are the same assertions that BE-0012's resolver tests make: one resolver, one test surface.
- *Save validates.* `load_scenario_file` rejects the save of a structurally edited but invalid scenario before any bytes are written, so no partial file remains.
- *Route shapes.* The load, resolve, and save handlers return the shapes above for a fixture run. A scenario with no run degrades to null artifact handles instead of raising an error.

The fast gate does not cover the two-pane overlay rendering in `serve.js`. That rendering is exercised through the existing serve UI path, like the crawl report's screenshot overlay.

## Alternatives considered

* **Keep the raw YAML textarea only.** That is the BE-0011 baseline. Rejected as the end state. The author would still need to know selectors by hand and would get no feedback on selector stability. The picker plus the `doctor` score is the whole value add.
* **A fully visual, code-free editor that hides the YAML.** Rejected. A canonical YAML that is hand-editable and PR-reviewable is a core principle. The editor augments the YAML and must not replace or obscure it.
* **A live, interactive Simulator embedded in the page (pick on the running app).** Rejected for the first cut. It needs a live device per editing session and a streaming pipeline. Picking against the screenshot and element tree that the run already captured is offline, cheap, and deterministic. A live picker can come later.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[scenarios.md](../../docs/scenarios.md), [selectors.md](../../docs/selectors.md); `bajutsu/drivers/base.py` (`_contains`, `resolve_unique`, `Selector` / `Element`), `bajutsu/doctor.py` (`score`, `ACTIONABLE_TRAITS`), `bajutsu/evidence.py` (`write_elements` → per-step `elements.json`, the `screenshot` kind → `after.png`), `bajutsu/serve/scenarios.py` (`ScenarioScope.save`), `bajutsu/scenario/load.py` (`load_scenario_file`), `bajutsu/serve/` (`handler.py` routing, `operations.py`), `bajutsu/templates/serve.js` + the crawl report's screenshot-overlay precedent.

**Dependencies / related items:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot plumbing this extends), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (**shares the point → element picker + doctor score — one resolver, two sources**: the editor reads captured artifacts, capture reads a live driver), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the role demarcation across the authoring surfaces).
