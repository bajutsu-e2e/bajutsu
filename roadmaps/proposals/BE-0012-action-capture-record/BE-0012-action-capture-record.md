**English** · [日本語](BE-0012-action-capture-record-ja.md)

# BE-0012 — Action-capture record

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0012](BE-0012-action-capture-record.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Authoring experience (record / GUI editor) |
<!-- /BE-METADATA -->

## Introduction

Record real user operations on the Simulator (tap / type / swipe) directly into a scenario, without relying on AI. idb exposes no way to *observe* input events, so capture is driven by proxy actuation: the author marks each action by clicking the point on the screen's screenshot in the `serve` web UI, and Bajutsu resolves that point to a stable selector and performs the action on their behalf.

## Motivation

Today the only path from "a thing a human does" to a scenario is the AI record loop (`record.py`): a natural-language goal drives an agent that explores the app and proposes steps. That is powerful for authoring from intent, but it has costs the author cannot always pay — it needs `ANTHROPIC_API_KEY`, it spends LLM (large language model) round-trips, and for a flow the author already knows by heart, narrating it as a goal and waiting for the agent is slower than simply *doing* it. There are also flows easier to demonstrate than to describe (a precise swipe, a multi-field form, an exact tap order). A capture mode that turns real operations on the Simulator directly into scenario steps gives authors a fast, offline, deterministic on-ramp, complementing — not replacing — the AI loop.

## Detailed design

Capture observes the operations a human performs on a booted device (tap / type / swipe) and emits the same `Scenario` (steps + `expect`) the AI loop produces, so everything downstream — `run`, `codegen`, the report — is unchanged. The hard part is determinism: a raw event stream is a sequence of *coordinates*, and the prime directive is that scenarios select by stable `accessibilityIdentifier`, not coordinates. So each captured action is resolved against a fresh `query()` taken at the moment of the action, written as an `id` selector wherever possible, with the same "ambiguous fails rather than tapping whatever matched first" rule the runner enforces.

The design is largely an **assembly of primitives the codebase already has**, not new machinery:

- **Point-in-frame hit-testing** already exists: `drivers/base.py:_contains(outer, inner)` does inclusive frame containment, and `Element["frame"]` is `(x, y, w, h)` in points. The accessibility tree is flat and "parent" is purely geometric, so a point → element hit-test is the natural model (pass a zero-size inner box to reuse `_contains`).
- **The stability ladder** is already encoded in `crawl.py:Action.as_selector()` and `crawl_repro.py:_selector()` (`id` → `label` (+`index`) → coordinate). `crawl_repro` already refuses to emit a step for an unaddressable coordinate ("faithful or nothing"); capture follows the same stance.
- **Determinism enforcement** is `drivers/base.py:resolve_unique`, which raises on 0 (not found) or ≥2 (ambiguous) matches — the exact rule capture applies at *authoring* time.
- **The stability score** is `doctor.score(elements, id_namespaces)`, reused to report how stable the resolved selector is (the same `doctor` integration BE-0013's picker needs).
- **Normalized `[0,1]` coordinates** (`crawl.py:_screen_size`, `Action.perform`'s `tap_point` scaling, `Node.targets`) are the bridge between a screenshot click in the browser and the element tree's points space.
- **Step emission** reuses `Step` / `TypeText` / `Swipe` and `serialize.dump_scenario_file`; the author-owned write path is `serve/scenarios.py:ScenarioScope.save()` / `.authored()` — the same path the AI loop writes through, auto-named and never overwriting.

### Capture flow (per action)

1. **Snapshot.** The backend takes a fresh `driver.query()` + `driver.screenshot()` and `_screen_size(driver)`, and shows the screenshot in the `serve` UI.
2. **Mark.** The author clicks a point (tap / type) or drags two points (swipe) on the screenshot. The browser converts the click from displayed-pixel space → normalized `[0,1]` → points space (mirroring `Action.perform`'s `tap_point` scaling) and POSTs it.
3. **Hit-test.** The server finds every element whose frame contains the point via `_contains`, and chooses the **most specific actionable** element (smallest frame among `doctor.ACTIONABLE_TRAITS`), so it resolves the button rather than the screen-filling window behind it.
4. **Resolve + validate.** Build the selector with the stability-ladder policy, then validate uniqueness with `resolve_unique`. If it is not unique, capture **surfaces the ambiguity** to the author (the competing elements) instead of emitting — never guessing.
5. **Score.** Run `doctor.score` and report the chosen rung (stable `id` / `label` fallback / coordinate degradation), so the author sees the stability of the choice as they author.
6. **Proxy-actuate.** Perform the action through the driver (`tap(sel)` / `type_text` / `swipe`) — `tap_point` only on the explicit, flagged coordinate fallback. Actuation is real, so the next snapshot reflects the app's true new state.
7. **Emit.** Append the resolved `Step` to the in-progress scenario and re-serialize + `save()` after every step, so the author always owns the current YAML.

### Per action kind

- **tap** → `Step(tap=sel)`.
- **type** → focus the field (`driver.tap(sel)`) then `type_text`; emit `Step(type=TypeText(text, into=sel))`. The text comes from a small input in the UI — capture cannot *observe* keystrokes any more than taps.
- **swipe** → two clicks; emit `Step(swipe=Swipe(from_, to))` in normalized points (the existing convention; replay scales them). When both endpoints resolve to one element, optionally upgrade to the stabler `Swipe(on=sel, direction=…)`.

### Selector choice policy (exact)

`id` present → validate unique → emit (or surface ambiguity if the id is duplicated on screen, a `doctor` `Blocked` condition); else `label` → unique → emit, else add `index` to disambiguate (flagged as flaky), else surface; neither `id` nor `label` → coordinate fallback, flagged as a degradation. The `el → Selector` ladder is implemented three times today (`crawl`, `crawl_repro`, and now capture), so this proposal recommends factoring it into one shared helper as a small separate refactor.

### Timing as condition waits, not sleeps

When an action lands on a screen whose fingerprint (`crawl.py:_fingerprint`) differs from the previous one, capture inserts a `wait` for the first element the next action targets — the same self-sufficiency the AI loop gets from `record._settle_step` — so replay never relies on wall-clock timing.

### Where it lives

Capture is inherently interactive (mark → actuate → observe → mark) and *requires* a screenshot the author clicks, so it belongs in `serve`, not a new CLI command. Two pieces:

- **A new pure module `bajutsu/capture.py`** for the driver- and HTTP-free core — `hit_test(elements, point)`, `resolve_capture(elements, point, namespaces)` (selector + doctor rung + ambiguity), `step_for_{tap,type,swipe}` — mirroring how `record.py` / `crawl_repro.py` keep their logic testable. This unit-tests on a fake element tree under `make check` with no Simulator.
- **A thin serve layer** — routes in `serve/handler.py` (e.g. `POST /api/capture/{start,mark,finish}`) and ops in `serve/operations.py`. The one architectural departure from BE-0011's stateless shell-out is that capture needs a **live `Driver` held across requests** for the booted target; that session lives in `ServeState` for the capture session's duration, and the actuation calls (the device-touching boundary) stay out of the pure core.

The resolver and emitter sit behind a small interface so a real event source — idb event capture, or events recorded by an XCUITest backend (BE-0019) — can replace the proxy input later without touching the resolver/emitter.

### Determinism, app-agnosticism, and the gate

Every step above is structural (point-in-frame, the stability ladder, `resolve_unique`, `doctor.score`): there is **no model call and no `ANTHROPIC_API_KEY`**, the sharp contrast with `record.py`'s `agent.next_action` / `_plan_goal`. Capture is strictly Tier 1 — it authors a scenario and, like the AI loop, introduces **no code path into the deterministic `run` / CI gate**. It reads `targets.<name>` (target app, scenarios dir, redaction) and writes the authored YAML exactly as the existing commands do, so it stays app- and backend-agnostic.

### Test strategy

Fits the Linux `make check` gate, no Simulator, minimal mocks (the fake driver / fake element tree are real test data, not behavior mocks):

- **Pure-core tests** over literal `Element` lists: `hit_test` picks the smallest containing actionable element; the selector ladder (id / label+index / coordinate); ambiguity surfaces and emits no step; the `doctor` rung; `type` / `swipe` emission; the cross-screen settle `wait`; and the emitted `Scenario` round-trips `dump_scenario_file` → `load_scenario_file`.
- **A `FakeDriver`** for proxy actuation: assert `tap(sel)` is used for an addressable element and `tap_point` only on the flagged fallback.
- **A serve-op test** at the operations layer (not over HTTP) with a fake session.

### Scope & non-goals

**In scope:** proxy-actuation capture of tap / type / swipe via screenshot-click in `serve`; structural selector resolution with the stability ladder + doctor score; ambiguity surfacing; coordinate-fallback flagging; cross-screen settle waits; streaming the steps into an author-owned YAML.

**Non-goals:** observing real touch events (waits on idb event capture or the XCUITest backend, BE-0019); inferring `expect` / assertions (intent inference is the AI loop's job — see BE-0014's enrichment direction); the structured GUI editor (BE-0013); multi-touch / pinch / rotate capture (idb is single-touch); merging capture into the `record` command surface (BE-0014).

### Open questions

1. **No coordinate-tap `Step`.** The schema has a coordinate `Swipe` and a driver-level `tap_point`, but `Step` has only `tap: Selector`. For an id-less *and* label-less tap, the recommendation is "faithful or nothing" — refuse to capture it and tell the author the element needs an `accessibilityIdentifier` — consistent with `crawl_repro`. (Confirm vs. adding a coordinate-tap step type.)
2. **Live-driver-in-serve lifecycle.** Holding a booted driver across requests needs session ownership, cleanup on disconnect, and a single-session guard per target.
3. **`type` text source.** Capture can't observe keystrokes, so the author types the text in the UI — confirm this UX vs. expecting it from a future event stream.
4. **Redaction at capture time.** `type` text may be a secret; honor the app's redaction config when serializing.

## Alternatives considered

* **Record raw coordinate taps and replay them verbatim.** Rejected outright: coordinate replay breaks on any layout, device-size, or translation change and violates determinism-by-selection. Resolving each tap to a stable `id` at capture time is the whole point.
* **Resolve a tapped point to the topmost / first matching element silently.** Rejected: that reintroduces "tap whatever matched first." When a point is ambiguous, capture must surface it for disambiguation, consistent with `resolve_unique`.
* **Wait for a real event-capture backend before shipping capture.** A true event stream (idb event capture, or events recorded by an XCUITest backend) would let the author operate the Simulator directly instead of marking points in the web UI. Rejected as a blocker: idb offers no such stream today and the XCUITest backend (BE-0019) is itself unbuilt, so gating capture on it would defer the whole feature. Proxy actuation ships the offline, no-API-key path now, and the resolver/emitter interface lets a real event source slot in later without a rewrite.
* **Fold capture into the existing AI `record` loop as an input mode.** Plausible but deferred to BE-0014, which defines the division of roles and the conversion between the two forms; this proposal scopes only the capture mechanism itself.

## References

[DESIGN §6.5](../../../DESIGN.md); `bajutsu/record.py` (the AI loop, `_settle_step`, screenshot plumbing), `bajutsu/drivers/base.py` (`_contains`, `resolve_unique`, `Selector` / `Element`), `bajutsu/crawl.py` (`Action.as_selector` / `Action.perform` / `_screen_size`, normalized `Node.targets`), `bajutsu/crawl_repro.py` (`_selector`, the "faithful or nothing" stance), `bajutsu/doctor.py` (`score`, `ACTIONABLE_TRAITS`), `bajutsu/scenario/models` (`Step` / `TypeText` / `Swipe`) + `scenario/serialize.py` (`dump_scenario_file`), `bajutsu/serve/` (`handler.py` routing, `operations.py`, `scenarios.py` `ScenarioScope`), `bajutsu/templates/serve.js` + `crawl.html.j2` (the screenshot + overlay precedent).

**Dependencies / related items:** [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot plumbing this extends), [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (shares the point → element picker + doctor score; the picker should be one shared component), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the role demarcation vs the AI loop and the capture → assertion enrichment), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) (a future real event source behind the same resolver/emitter interface).
