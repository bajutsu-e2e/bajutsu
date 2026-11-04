**English** · [日本語](BE-0012-action-capture-record-ja.md)

# BE-0012 — Action-capture record

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0012](BE-0012-action-capture-record.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0012") |
| Implementing PR | [#381](https://github.com/bajutsu-e2e/bajutsu/pull/381), [#382](https://github.com/bajutsu-e2e/bajutsu/pull/382) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

Action-capture record turns real user operations on the Simulator (tap / type / swipe) directly into a scenario, without AI. The idb tool exposes no way to *observe* input events, so capture is driven by proxy actuation. The author marks each action by clicking a point on the screen's screenshot in the `serve` web UI. Bajutsu then resolves that point to a stable selector and performs the action on the author's behalf.

## Motivation

Today the only path from "something a human does" to a scenario is the AI record loop (`record.py`). A natural-language goal drives an agent that explores the app and proposes steps. That path is powerful for authoring from intent, but it has costs that an author cannot always pay:

- It needs `ANTHROPIC_API_KEY`.
- It spends LLM (large language model) round-trips.
- For a flow the author already knows by heart, narrating the flow as a goal and waiting for the agent is slower than simply *doing* it.

Some flows are also easier to demonstrate than to describe: a precise swipe, a multi-field form, an exact tap order. A capture mode turns real operations on the Simulator directly into scenario steps. It gives authors a fast, offline, deterministic on-ramp, and it complements the AI loop without replacing it.

## Detailed design

Capture observes the operations a human performs on a booted device (tap / type / swipe). It emits the same `Scenario` (steps + `expect`) that the AI loop produces, so everything downstream (`run`, `codegen`, the report) is unchanged.

The hard part is determinism. A raw event stream is a sequence of *coordinates*, but the prime directive is that scenarios select by stable `accessibilityIdentifier`, not by coordinates. Capture therefore resolves each action against a fresh `query()` taken at the moment of the action. It writes an `id` selector wherever possible. It also applies the rule the runner enforces: an ambiguous selector fails instead of tapping whatever matched first.

The design is largely an **assembly of primitives the codebase already has**, not new machinery:

- **Point-in-frame hit-testing** already exists. `drivers/base.py:_contains(outer, inner)` does inclusive frame containment, and `Element["frame"]` is `(x, y, w, h)` in points. The accessibility tree is flat, and "parent" is purely geometric. A point-to-element hit-test is therefore the natural model: pass a zero-size inner box to reuse `_contains`.
- **The stability ladder** is already encoded in `crawl.py:Action.as_selector()` and `crawl_repro.py:_selector()` as `id` → `label` (+`index`). A coordinate tap is a separate `tap_point` action kind, not a rung of that ladder. `crawl_repro._selector()` returns `None` for an element with neither id nor label and refuses to emit a step ("faithful or nothing"). Capture takes the same stance and stops the ladder at `label`. A tap on an element with neither id nor label is refused and never degraded to a coordinate (Decision 1).
- **Determinism enforcement** is `drivers/base.py:resolve_unique`, which raises on 0 matches (not found) or 2 or more matches (ambiguous). Capture applies the same rule at *authoring* time.
- **The stability score** is `doctor.score(elements, id_namespaces)`. Capture reuses it to report how stable the resolved selector is. The picker in [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) needs the same `doctor` integration.
- **Normalized `[0,1]` coordinates** bridge a screenshot click in the browser and the points space of the element tree. They appear in `crawl.py:_screen_size`, in the `tap_point` scaling of `Action.perform`, and in `Node.targets`.
- **Step emission** reuses `Step`, `TypeText`, `Swipe`, and `serialize.dump_scenario_file`. The author-owned write path is `serve/scenarios.py:ScenarioScope.save()` / `.authored()`. The AI loop writes through the same path, auto-named and never overwriting.

### Capture flow (per action)

1. **Snapshot.** The backend takes a fresh `driver.query()`, `driver.screenshot(path)`, and `_screen_size(driver)`. It shows the saved screenshot in the `serve` UI.
2. **Mark.** The author clicks a point (tap / type) or drags between two points (swipe) on the screenshot. The browser converts the click from displayed-pixel space to normalized `[0,1]`, then to points space. This mirrors the `tap_point` scaling of `Action.perform`. The browser then POSTs the result.
3. **Hit-test.** The server finds every element whose frame contains the point, using `_contains`. It chooses the **most specific actionable** element: the smallest frame among `doctor.ACTIONABLE_TRAITS`. The result is the button, not the screen-filling window behind the button.
4. **Resolve and validate.** The server builds the selector with the stability-ladder policy and validates uniqueness with `resolve_unique`. If the selector is not unique, capture **surfaces the ambiguity** to the author, listing the competing elements, and emits nothing. Capture never guesses.
5. **Score.** The server runs `doctor.score` and reports the chosen rung (stable `id` or `label` fallback), so the author sees the stability of the choice while authoring. A tap that reaches no addressable element is refused at this point (Decision 1), not degraded to a coordinate.
6. **Proxy-actuate.** The server performs the action through the driver (`tap(sel)` / `type_text` / `swipe`). A `type` value is the author's UI input (Decision 3). Capture redacts that value per the target's `Redact` config before serializing it (Decision 4). The actuation is real, so the next snapshot reflects the app's true new state.
7. **Emit.** The server appends the resolved `Step` to the in-progress scenario. After every step it re-serializes the scenario and calls `save()`, so the author always owns the current YAML.

### Per action kind

- **tap** → `Step(tap=sel)`.
- **type** → focus the field with `driver.tap(sel)`, then call `type_text`. Capture emits `Step(type=TypeText(text, into=sel))`. The text comes from a small input in the UI, because capture cannot *observe* keystrokes any more than taps (Decision 3). The author types the value, and capture proxy-types it. That value may be a secret, so capture runs it through the target's `Redact` config before serializing it into the step (Decision 4). A secret never lands unredacted in the authored YAML.
- **swipe** → two clicks. Capture emits `Step(swipe=Swipe(from_, to))` in normalized points, the existing convention that replay scales. When both endpoints resolve to one element, capture upgrades the step to the stabler `Swipe(on=sel, direction=…)`.

### Selector choice policy (exact)

The policy runs in this order:

1. If `id` is present, validate that it is unique and emit it. If the id is duplicated on screen, surface the ambiguity (a `doctor` `Blocked` condition).
2. Otherwise, if `label` is present and unique, emit it. If the label is not unique, add `index` to disambiguate and flag the step as flaky. If that still fails, surface the ambiguity.
3. If neither `id` nor `label` is present, **refuse the capture** and tell the author the element needs an `accessibilityIdentifier` (Decision 1, "faithful or nothing").

There is deliberately **no coordinate-tap step type**. `Step` keeps only `tap: Selector`, so a tap that `id` or `label` cannot address is never written as a coordinate. This matches the stance of `crawl_repro`, which skips an unaddressable tap instead of emitting a lossy one.

The `el → Selector` ladder is implemented in `crawl` and `crawl_repro` today. Capture would add a third copy. This proposal recommends factoring the ladder into one shared helper as a small separate refactor.

### Timing as condition waits, not sleeps

Suppose an action lands on a screen whose fingerprint (`crawl.py:fingerprint`) differs from the previous one. Capture then inserts a `wait` for the first element the next action targets. The AI loop gets the same self-sufficiency from `record._settle_step`. Replay therefore never relies on wall-clock timing.

### Where it lives

Capture is inherently interactive (mark → actuate → observe → mark) and *requires* a screenshot the author clicks. It therefore belongs in `serve`, not in a new CLI command. It has two pieces:

- **A new pure module `bajutsu/capture.py`** holds the core, which depends on neither a driver nor HTTP. The module provides `hit_test(elements, point)`, `resolve_capture(elements, point, namespaces)` (selector, `doctor` rung, and ambiguity), and `step_for_{tap,type,swipe}`. This mirrors how `record.py` and `crawl_repro.py` keep their logic testable. Under `make check`, the module is unit-tested on a fake element tree with no Simulator.
- **A thin serve layer** adds routes in `serve/handler.py` (for example `POST /api/capture/{start,mark,finish}`) and ops in `serve/operations.py`.

The serve layer departs from [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)'s stateless shell-out in one architectural respect. Capture needs a **live `Driver` held across requests** for the booted target (Decision 2):

- `start` opens one `Driver` and stores it in `ServeState`.
- Every `mark` reuses that `Driver`.
- `finish`, or a disconnect, closes the `Driver` and clears the slot.
- A **single-session guard rejects a second concurrent capture on the same target**, because one device cannot serve two interleaved sessions.
- The actuation calls, which are the device-touching boundary, stay out of the pure core.

The lifecycle mirrors how `ServeState` already owns longer-lived, swappable seams (`executor`, `sessions`). The capture driver is one more such slot, held for the session alone.

The resolver and emitter sit behind a small interface. A real event source can then replace the proxy input later without touching the resolver or emitter (Decision 3). Candidate sources are idb event capture and events recorded by an XCUITest backend ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)). The author's UI text input is today's emitter input, and a real keystroke stream would feed the same emitter.

### Determinism, app-agnosticism, and the gate

Every step above is structural: point-in-frame, the stability ladder, `resolve_unique`, and `doctor.score`. There is **no model call and no `ANTHROPIC_API_KEY`**, which sharply contrasts with `agent.next_action` and `_plan_goal` in `record.py`. Capture is strictly [Tier 1](../../docs/glossary.md#the-two-tiers). Like the AI loop, it authors a scenario and introduces **no code path into the deterministic `run` / CI gate**. Capture reads `targets.<name>` (target app, scenarios dir, redaction) and writes the authored YAML exactly as the existing commands do. It therefore stays app- and backend-agnostic.

### Test strategy

The tests fit the Linux `make check` gate, need no Simulator, and use minimal mocks. The fake driver and fake element tree are real test data, not behavior mocks.

- **Pure-core tests** run over literal `Element` lists. They cover these cases:
  - `hit_test` picks the smallest containing actionable element.
  - The selector ladder works for id and for label + index.
  - A tap with neither id nor label is refused and emits no step (Decision 1).
  - An ambiguity surfaces and emits no step.
  - The `doctor` rung is reported.
  - `type` and `swipe` steps are emitted.
  - `type` redaction masks a configured secret value before the value reaches the emitted step (Decision 4).
  - The cross-screen settle `wait` is inserted.
  - The emitted `Scenario` round-trips through `dump_scenario_file` and `load_scenario_file`.
- **A `FakeDriver`** covers proxy actuation. The tests assert that `tap(sel)` is used for an addressable element. They also assert that an unaddressable tap raises the "add an accessibilityIdentifier" refusal instead of actuating.
- **A serve-op test** runs at the operations layer, not over HTTP, with a fake session.

### Scope & non-goals

**In scope:**

- Proxy-actuation capture of tap / type / swipe by screenshot click in `serve`.
- Structural selector resolution with the stability ladder and the `doctor` score.
- Ambiguity surfacing.
- Refusing an unaddressable tap (Decision 1).
- A per-session live driver held in `ServeState`, with a single-session guard (Decision 2).
- A UI text input as the `type` source (Decision 3).
- Redaction of typed values at serialize time (Decision 4).
- Cross-screen settle waits.
- Streaming the steps into an author-owned YAML.

**Non-goals:**

- Observing real touch events. That work waits on idb event capture or the XCUITest backend ([BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)).
- Inferring `expect` or assertions. Intent inference is the AI loop's job; see the enrichment direction in [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md).
- The structured GUI editor ([BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md)).
- Multi-touch, pinch, and rotate capture, because idb is single-touch.
- Merging capture into the `record` command surface (BE-0014).

### Decisions

These four points were open during drafting. Each is now settled, and the settled form is already woven into the subsections above.

1. **No coordinate-tap `Step` ("faithful or nothing").** Capture **refuses** a tap whose target has neither an `id` nor a `label`. The refusal message tells the author to add an `accessibilityIdentifier` to that element, instead of emitting a coordinate-addressed step. We do **not** add a coordinate-tap step type to the scenario schema, and `Step` keeps only `tap: Selector`. *Rationale:* a coordinate tap breaks on any layout, device-size, or translation change, and would reintroduce non-determinism into authored YAML. `crawl_repro` already takes the same stance for unaddressable taps (`bajutsu/crawl_repro.py`).
2. **Live driver held in `ServeState` for the session.** A capture session opens **one** live `Driver` for the target and keeps it in `ServeState` for the session's duration. `finish` or a disconnect cleans it up. A **single-session guard rejects a second concurrent capture on the same target**. *Rationale:* capture is a stateful mark → actuate → observe loop, so it needs a driver that survives across requests. This is the one departure from BE-0011's stateless shell-out. Also, one device cannot serve two interleaved capture sessions.
3. **`type` text comes from a UI input the author types into.** Capture cannot observe keystrokes, because idb exposes no input-event stream. The author types the value into a small input in the `serve` UI, and capture proxy-types it into the focused field. *Rationale:* this is the only source available today. The resolver/emitter interface lets a real event source (BE-0019) replace this input later behind the same seam, without changing the resolver or emitter.
4. **Redaction is applied at capture time.** A typed `type` value may be a secret. When capture serializes the step, it honors the target's `Redact` config (reusing `bajutsu/redaction.py`), so a secret never lands unredacted in the authored YAML. *Rationale:* the authored scenario is committed and shared, and capture is where the secret first enters the scenario. Masking must therefore happen on write, under the same `Redact` config that the evidence pipeline already obeys.

## Alternatives considered

* **Record raw coordinate taps and replay them verbatim.** Rejected outright. Coordinate replay breaks on any layout, device-size, or translation change, and it violates determinism-by-selection. Resolving each tap to a stable `id` at capture time is the whole point.
* **Resolve a tapped point to the topmost or first matching element silently.** Rejected. This reintroduces "tap whatever matched first". When a point is ambiguous, capture must surface the ambiguity for disambiguation, consistent with `resolve_unique`.
* **Wait for a real event-capture backend before shipping capture.** A true event stream (idb event capture, or events recorded by an XCUITest backend) would let the author operate the Simulator directly instead of marking points in the web UI. Rejected as a blocker. idb offers no such stream today, and the XCUITest backend (BE-0019) is itself unbuilt, so gating capture on that backend would defer the whole feature. Proxy actuation ships the offline, no-API-key path now. The resolver/emitter interface lets a real event source slot in later without a rewrite.
* **Fold capture into the existing AI `record` loop as an input mode.** Plausible, but deferred to BE-0014. BE-0014 defines the division of roles and the conversion between the two forms. This proposal scopes the capture mechanism alone.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[DESIGN §6.5](../../DESIGN.md); `bajutsu/record.py` (the AI loop, `_settle_step`, screenshot plumbing), `bajutsu/drivers/base.py` (`_contains`, `resolve_unique`, `Selector` / `Element`), `bajutsu/crawl.py` (`Action.as_selector` / `Action.perform` / `_screen_size`, normalized `Node.targets`), `bajutsu/crawl_repro.py` (`_selector`, the "faithful or nothing" stance), `bajutsu/doctor.py` (`score`, `ACTIONABLE_TRAITS`), `bajutsu/scenario/models` (`Step` / `TypeText` / `Swipe`) + `bajutsu/scenario/serialize.py` (`dump_scenario_file`), `bajutsu/serve/` (`handler.py` routing, `operations.py`, `scenarios.py` `ScenarioScope`), `bajutsu/templates/serve.js` + `crawl.html.j2` (the screenshot + overlay precedent).

**Dependencies / related items:** [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) (the `serve` host, `ScenarioScope`, screenshot plumbing this extends), [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor.md) (shares the point → element picker + doctor score; the picker should be one shared component), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md) (the role demarcation vs the AI loop and the capture → assertion enrichment), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) (a future real event source behind the same resolver/emitter interface).
