**English** · [日本語](BE-0189-serve-ui-dogfood-ci-gate-ja.md)

# BE-0189 — Gate the serve Web UI dogfood in CI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0189](BE-0189-serve-ui-dogfood-ci-gate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0189") |
| Implementing PR | [#733](https://github.com/bajutsu-e2e/bajutsu/pull/733) |
| Topic | Dogfood fixtures (web UI) |
| Related | [BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) shipped the `demos/serve-ui`
harness, in which the Web (Playwright) backend drives Bajutsu's *own* `serve` Web UI. BE-0058 left one
piece of its design as forward-looking work: **wiring the target into a CI job alongside
`demos/web`**. Until that job exists, nothing runs the net, so a regression in the served
single-page app goes unnoticed. This item closes the gap by adding the CI job. It also repairs the two
scenarios that drifted out of green while nothing ran them.

## Motivation

**1. An ungated net catches nothing.** BE-0058 is valuable as a deterministic regression net
for the Web UI. But `demos/serve-ui` is in no workflow, so `make check` and every PR stay green
whatever its state. Running the harness on `main` today already fails: two of its nine scenarios
are red, and no gate reports them. A net that no job runs is documentation, not protection.

**2. The drift is what the net exists to catch, which is why the net must be gated.** The two failures
are genuine signal, not flakiness:

- `record-form` asserts the Record view's **Generate** button is *enabled*. After
  [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) (AI-free graceful
  degradation), the Record surface *disables* Generate and shows a "needs Claude" gate whenever no AI
  provider is reachable. That condition always holds for this keyless dogfood. The scenario encodes a
  pre-BE-0101 assumption and has gone stale. A gated net would have forced BE-0101 to update the
  scenario in the same change.
- `panel-resize` drives a resize divider with `swipe { on, direction }`, but the gesture never moves
  the panels. The divider is a 10px-wide handle, and the directional swipe centers its travel
  *across* the element, so `down` lands about 50px away, beside the handle on the neighbouring panel,
  and drags nothing. The limitation belongs to the swipe primitive, not just to this scenario.

**3. The change respects every prime directive** ([CLAUDE.md](../../CLAUDE.md)). The job stays purely
Tier-2: keyless, no model, pass/fail from machine assertions alone, on Linux with no Mac or
Simulator. The proposed gesture change keeps the swipe deterministic (a fixed travel from a resolved
element, no `sleep`, no ambiguity). Nothing here puts an LLM on the `run`/CI path.

## Detailed design

### 1. CI job: `dogfood (serve UI)` in `web-e2e.yml`

The third job in [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) mirrors
the existing `smoke (playwright)` job: check out, install uv, cache Chromium, `uv sync --extra web`
+ `playwright install`, then `make -C demos/serve-ui e2e`. Like the smoke, it carries
`UV_NO_DEV: "1"`, because the dogfood needs the `web` extra and never the dev group. The config's
`launchServer` brings up and tears down the inner `serve` the job drives, so the job starts no server by hand.

The workflow's positive-list path filter gains the paths the dogfood exercises and the smoke
does not: `bajutsu/serve/**`, `bajutsu/templates/**`, and `demos/serve-ui/**`. A change to the
served SPA, which the smoke's filter deliberately excludes, then triggers the web workflow. Like the
other web jobs, the new job is **not** a required check. The filter keeps it off PRs that cannot affect the
web path, so it never blocks an unrelated PR.

### 2. Gesture fix: a directional swipe begins *on* the element

[`_scroll_gesture`](../../bajutsu/orchestrator/actions/handlers/gestures.py) computes the
`(from, to)` for a `{ on, direction }` swipe by centering the travel segment on the element's
centre, so the `down` point sits half the travel distance away from the element. The offset is invisible
when the gesture merely passes through a large scrolled region, but it leaves the gesture unable to
grab a small handle. For the 10px resize divider, `down` lands on the adjacent panel and the drag is
a no-op.

The proposed fix makes the gesture **start at the element's centre** and travel the same distance
outward in the requested direction. If the travel would overrun a screen edge, the segment slides back onto the
screen, which preserves the travelled distance. Scroll displacement is unchanged, so the existing
`amount`-scaling test still holds, while a swipe can now land on and drag a
handle. A new unit test pins the invariant: for an element with room in both directions, `down` is
exactly its centre.

### 3. Scenario repairs

- **`record-form`** becomes the keyless Record-degradation net it should be under BE-0101.
  With no Claude reachable, the `record.ai-gate` banner is present, `record.generate` and
  `record.save` are disabled, and the `record.goal` textarea still records typed input. The stale
  assertion becomes a live guard for BE-0101's Record surface.
- **`panel-resize`** keeps its intent. It passes once the gesture fix (§2) lets the divider
  drag redistribute the two adjacent panels while the non-adjacent report panel keeps its 50%
  share.

### Machine-checkable outcome

With the fixes, `make -C demos/serve-ui e2e` is green (9/9); without them it is red. The new CI job
runs it on every web-path PR. `make check` stays green, including the new `_scroll_gesture` unit
test. No assertion consults a model.

## Alternatives considered

- **Fix `panel-resize` with raw `{ from, to }` coordinates instead of the gesture.** Rejected: raw
  coordinates are viewport-fragile, and [`audit`](../../bajutsu/audit.py) already steers authors
  away from them toward `{ on, direction }` on a stable id. The right fix lets the
  stable-selector form grab a handle, which benefits every backend, not just this scenario.
- **Shrink the swipe `amount` so `down` lands near the divider.** Rejected: `amount` is a fraction
  of the *screen*, so the value needed is tiny, viewport-dependent, and rounding-fragile. It would
  encode a magic number that breaks on any layout change.
- **Drop `panel-resize` from the net.** Rejected: the scenario guards a specific tiling bug (a resize wrongly
  disturbing a non-adjacent panel) that has no other test, because there is no JS test stack. Removing
  it loses real coverage. Fixing the gesture keeps the guard *and* improves the primitive.
- **Give the dogfood CI job an API key so `record-form`'s original assertion holds.** Rejected: the
  dogfood is valuable because it runs keyless on Linux with no secret and no model. Requiring a key would
  conflict with that design and with the AI-free posture of BE-0101. Asserting the *degraded* state is the
  more accurate and more useful check.
- **Fold this into BE-0058.** BE-0058's ID is permanent and its scope is the harness itself. This item is
  BE-0058's forward-looking CI slice plus the repairs a gated net surfaces, so it is tracked as its own
  item and linked reciprocally under `Related`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] CI job `dogfood (serve UI)` added to `web-e2e.yml`, with the serve/templates/serve-ui path filters
- [x] `_scroll_gesture` begins the directional swipe on the element; unit test pins the invariant
- [x] `record-form` reframed as the keyless BE-0101 Record-degradation net
- [x] `panel-resize` green via the gesture fix (intent unchanged)

Log:

- [#733](https://github.com/bajutsu-e2e/bajutsu/pull/733) — implemented. Added the `dogfood (serve UI)`
  job (plus the serve/templates/serve-ui path filters) to `web-e2e.yml`, made `_scroll_gesture` begin
  the directional swipe on the element with a unit test pinning `down` on the centre, reframed
  `record-form` as the keyless BE-0101 Record-degradation net, and brought `panel-resize` green via
  the gesture fix — `make -C demos/serve-ui e2e` now 9/9.

## References

- [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the harness this completes
- [BE-0101 — AI-free zero-config](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) — the graceful degradation `record-form` guards
- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the backend under test
- [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) — the workflow the job joins
- [`demos/serve-ui`](../../demos/serve-ui) — the harness · [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — the swipe gesture
