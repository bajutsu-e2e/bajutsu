**English** · [日本語](BE-0189-serve-ui-dogfood-ci-gate-ja.md)

# BE-0189 — Gate the serve Web UI dogfood in CI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0189](BE-0189-serve-ui-dogfood-ci-gate.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0189") |
| Topic | Dogfood fixtures (web UI) |
| Related | [BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md), [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) shipped the `demos/serve-ui`
harness — the Web (Playwright) backend driving Bajutsu's *own* `serve` Web UI — but left one piece
of its own design as forward-looking work: **wiring the target into a CI job alongside
`demos/web`**. Without that, the net exists but nothing runs it, so a regression in the served
single-page app slips through unnoticed. This item proposes to close that gap by adding the CI job,
and to repair the two scenarios that have drifted out of green while nothing runs them.

## Motivation

**1. An ungated net catches nothing.** The whole value of BE-0058 is a deterministic regression net
for the Web UI. But `demos/serve-ui` is not in any workflow, so `make check` and every PR stay green
regardless of its state. Running the harness on `main` today already fails: of its nine scenarios,
two are red and no gate reports it. A net that no job runs is documentation, not protection.

**2. The drift is exactly what the net is for — and shows why it must be gated.** The two failures
are genuine signal, not flakiness:

- `record-form` asserts the Record view's **Generate** button is *enabled*. After
  [BE-0101](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) (AI-free graceful
  degradation) the Record surface *disables* Generate and shows a "needs Claude" gate whenever no AI
  provider is reachable — which is always true for this keyless dogfood. The scenario encodes a
  pre-BE-0101 assumption and has gone stale; a gated net would have forced BE-0101 to update it in
  the same change.
- `panel-resize` drives a resize divider with `swipe { on, direction }`, but the gesture never moves
  the panels: the divider is a 10px-wide handle, and the directional swipe centers its travel
  *across* the element, so `down` lands ~50px away — beside the handle, on the neighbouring panel —
  and drags nothing. This is a real limitation of the swipe primitive, not just this scenario.

**3. It respects every prime directive** ([CLAUDE.md](../../CLAUDE.md)). The job stays pure
Tier-2: keyless, no model, pass/fail from machine assertions only, on Linux with no Mac or
Simulator. The proposed gesture change keeps the swipe deterministic (a fixed travel from a resolved
element, no `sleep`, no ambiguity). Nothing here puts an LLM on the `run`/CI path.

## Detailed design

### 1. CI job: `dogfood (serve UI)` in `web-e2e.yml`

A third job in [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml), mirroring
the existing `smoke (playwright)` job: check out, install uv, cache Chromium, `uv sync --extra web`
+ `playwright install`, then `make -C demos/serve-ui e2e`. It carries `UV_NO_DEV: "1"` like the
smoke (the dogfood needs only the `web` extra, never the dev group). The inner `serve` it drives is
brought up and torn down by the config's `launchServer`, so the job hand-starts no server.

The workflow's positive-list path filter should gain the paths the dogfood exercises but the smoke
does not — `bajutsu/serve/**`, `bajutsu/templates/**`, `demos/serve-ui/**` — so a change to the
served SPA (which the smoke's filter deliberately excludes) triggers the web workflow. Like the
other web jobs it stays **not** a required check: the filter keeps it off PRs that can't affect the
web path, and it never blocks an unrelated PR.

### 2. Gesture fix: a directional swipe begins *on* the element

[`_scroll_gesture`](../../bajutsu/orchestrator/actions/handlers/gestures.py) computes the
`(from, to)` for a `{ on, direction }` swipe by centering the travel segment on the element's
centre — so the `down` point sits half the travel distance away from the element. That is invisible
when scrolling a large region the gesture merely passes through, but it makes the gesture unable to
grab a small handle: for the 10px resize divider, `down` lands on the adjacent panel and the drag is
a no-op.

The proposed fix: the gesture **starts at the element's centre** and travels the same distance
outward in the requested direction, sliding the segment back onto the screen only if a travel would
overrun an edge (which preserves the travelled distance). Scroll behaviour is unchanged in
displacement — the existing `amount`-scaling test still holds — while a swipe can land on and drag a
handle. A new unit test pins the invariant: for an element with room in both directions, `down` is
exactly its centre.

### 3. Scenario repairs

- **`record-form`** → reframed as the keyless Record-degradation net it should be under BE-0101:
  with no Claude reachable, the `record.ai-gate` banner is present, `record.generate` and
  `record.save` are disabled, and the `record.goal` textarea still records typed input. This turns a
  stale assertion into a live guard for BE-0101's Record surface.
- **`panel-resize`** → unchanged in intent; it passes once the gesture fix (§2) lets the divider
  drag redistribute the two adjacent panels while leaving the non-adjacent report panel at its 50%
  share.

### Machine-checkable outcome

`make -C demos/serve-ui e2e` is green (9/9) with the fixes and red without them, and the new CI job
runs it on every web-path PR. `make check` stays green, including the new `_scroll_gesture` unit
test. No assertion anywhere consults a model.

## Alternatives considered

- **Fix `panel-resize` with raw `{ from, to }` coordinates instead of the gesture.** Rejected: raw
  coordinates are viewport-fragile and [`audit`](../../bajutsu/audit.py) already steers authors
  away from them toward `{ on, direction }` on a stable id. The honest fix is to make the
  stable-selector form able to grab a handle, which benefits every backend, not just this scenario.
- **Shrink the swipe `amount` so `down` lands near the divider.** Rejected: `amount` is a fraction
  of the *screen*, so the value needed is tiny, viewport-dependent, and rounding-fragile — it would
  encode a magic number that breaks on any layout change.
- **Drop `panel-resize` from the net.** Rejected: it guards a specific tiling bug (a resize wrongly
  disturbing a non-adjacent panel) that has no other test — there is no JS test stack — so removing
  it loses real coverage. Fixing the gesture keeps the guard *and* improves the primitive.
- **Give the dogfood CI job an API key so `record-form`'s original assertion holds.** Rejected: the
  dogfood's value is that it runs keyless on Linux with no secret and no model; requiring a key would
  fight both that design and the AI-free posture of BE-0101. Asserting the *degraded* state is the
  more honest and more useful check.
- **Fold this into BE-0058.** BE-0058's ID is permanent and its scope is the harness itself; this is
  its forward-looking CI slice plus the repairs a gated net surfaces, so it is tracked as its own
  item and linked reciprocally under `Related`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] CI job `dogfood (serve UI)` added to `web-e2e.yml`, with the serve/templates/serve-ui path filters
- [ ] `_scroll_gesture` begins the directional swipe on the element; unit test pins the invariant
- [ ] `record-form` reframed as the keyless BE-0101 Record-degradation net
- [ ] `panel-resize` green via the gesture fix (intent unchanged)

## References

- [BE-0058 — Dogfood the serve Web UI](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui.md) — the harness this completes
- [BE-0101 — AI-free zero-config](../BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config.md) — the graceful degradation `record-form` guards
- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the backend under test
- [`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) — the workflow the job joins
- [`demos/serve-ui`](../../demos/serve-ui) — the harness · [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — the swipe gesture
