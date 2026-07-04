**English** · [日本語](BE-0038-autonomous-crawl-exploration-ja.md)

# BE-0038 — Autonomous crawl exploration (App Explorer style)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0038](BE-0038-autonomous-crawl-exploration.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0038") |
| Implementing PR | [#80](https://github.com/bajutsu-e2e/bajutsu/pull/80), [#307](https://github.com/bajutsu-e2e/bajutsu/pull/307), [#319](https://github.com/bajutsu-e2e/bajutsu/pull/319) |
| Topic | Candidates from competitive research (MagicPod / Autify) |
| Origin | Autify VAX |
<!-- /BE-METADATA -->

## Introduction

AI autonomously crawls screen transitions, generates a screen map, and reports crashes and unreachable states. This extends Tier 1 `record` capability.

Where `record` is *goal-directed* — the AI explores toward one natural-language goal and writes out one deterministic scenario ([recording.md](../../../docs/recording.md)) — `crawl` is *breadth-first*: it systematically visits as many screens as it can reach, builds a graph of the screens and the transitions between them, and reports what it found. It is an exploration and discovery tool, never a pass/fail gate.

## Motivation

The current authoring path assumes you already know the flow you want to test. You give `record` a goal ("log in and open settings"), and it produces a scenario for that path. That is the right tool once you know what to write, but it leaves three gaps that a crawl fills.

**1. Discovery before authoring.** When onboarding a new app (the [`apps.<name>`](../../../DESIGN.md) per-app config and [BE-0024 doctor / onboarding](../../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)), the hardest question is "what flows even exist, and which screens are worth testing?" Today the only answers come from reading the app by hand. A crawl produces a screen map — a labeled inventory of reachable screens with a representative screenshot and element dump for each — that turns "I don't know the app" into "here are its 40 screens, pick the ones to author scenarios for." This is the natural front end to `record`: each discovered screen and the shortest path to it become a seed goal.

**2. Coverage measurement across the whole app.** [DESIGN §7.2](../../../DESIGN.md) is explicit that `doctor --app` measures accessibility-identifier coverage only on the entry screen and screens reachable by a declared deeplink — and it already anticipates a `doctor --app <name> --from <runId>` mode that reuses the per-screen `elements` dumps left by a prior run to compute whole-app coverage honestly. A crawl is the run that produces those dumps. It is what makes the §7.2 "honesty of the measurement range" promise real: instead of guessing, the coverage report is computed over every screen the crawl actually reached.

**3. Robustness smoke testing.** A systematic crawl exercises the app far more widely than a handful of authored happy paths, surfacing crashes, hangs, and dead ends that scenario tests never visit. This is the "App Explorer / Robo test" value (the Autify VAX origin of this item): cheap, broad, zero-authoring coverage that catches "the app crashes if you tap *that* in *this* order" before a user does.

**The determinism boundary is the whole point of doing this in Bajutsu.** A crawl is non-deterministic by nature — exploration order, AI choices, and app state all vary run to run — so the crawl itself can never be a CI gate ([prime directive #1](../../../CLAUDE.md): *AI is the author and the failure investigator, never the judge*). What Bajutsu adds over a generic monkey/explorer is that the crawl's *byproducts* are first-class deterministic scenarios: a crash's reproducing path and each discovered flow are emitted as plain YAML that the user reviews and, once committed, `run` replays AI-free as a Tier 2 regression. Crashes therefore *do* reach CI — as committed repro scenarios, not as the flaky crawl. This is the same hub model the rest of the tool uses (the scenario is the shared artifact; AI authors it once, the deterministic runner owns it forever).

## Detailed design

### Command surface

A new Tier 1 command, mirroring `record`'s flags:

```
bajutsu crawl --app <name>
    [--max-screens N] [--max-steps N] [--budget DURATION] [--max-depth N]
    [--seed <deeplink> ...]          # extra entry points besides the launch screen
    [--guide ai|off]                 # whether to use AI to prioritize/handle inputs
    [--backend idb] [--udid booted] [--dismiss-alerts]
    [--out runs/<runId>]
```

Like `record`, this is an *AI-live* path, not part of the deterministic gate ([DESIGN §3.1](../../../DESIGN.md)). With `--guide off` the crawl runs purely on the identifier-driven heuristics below and needs no `ANTHROPIC_API_KEY`; `--guide ai` layers the optional agent on top. Either way, AI never decides pass/fail.

### State model (graph nodes)

Each screen the crawl visits is reduced to a **state fingerprint** so that revisiting the same screen is recognized rather than re-explored. The fingerprint is derived from `driver.query()` ([`drivers/base.py`](../../../bajutsu/drivers/base.py)):

- **Primary:** the sorted set of `accessibilityIdentifier`s present on screen, hashed. Identifiers are non-localized and data-derived ([DESIGN §7.3](../../../DESIGN.md)), so this fingerprint is stable across locales and minor content changes — the same property that makes id-based selectors deterministic makes id-based state identity deterministic.
- **Fallback (low-id screens):** when too few elements carry ids, fall back to a structural hash over `(traits, frame-bucket)` of the actionable elements. This is less stable and is flagged in the report, mirroring the stability-ladder honesty rule ([DESIGN §5](../../../DESIGN.md)).

The fingerprint is the graph node identity. A node stores a representative screenshot, the `elements` dump, and the id set — the same per-screen artifacts §7.2 needs.

### Frontier and action selection (graph edges)

From a state, the **candidate edges** are its actionable elements — those with `button` / `link` / `textField` / `searchField` traits — preferring elements that carry an `id` (stability ladder, [DESIGN §5](../../../DESIGN.md)). The crawl keeps a frontier of unexplored `(state, action)` pairs and does a breadth-first traversal. Candidate ordering within a state is deterministic (sorted by id), so two crawls of an unchanged app explore in the same order as far as the app's own non-determinism allows — important for reproducibility of the resulting maps.

Text fields need an input value. With `--guide off`, a fixed safe placeholder per trait is used; with `--guide ai`, the optional agent supplies context-appropriate input (still Tier 1 — see below).

### Traversal by deterministic replay, not in-place backtracking

App transitions are usually irreversible (you cannot reliably "undo" a tap). Rather than trying to navigate backward — which would be flaky — the crawl revisits a frontier node the same way `run` reaches any state: **erase to a clean environment and replay the known shortest path to that node** ([DESIGN §2](../../../DESIGN.md) environment cleanliness; [`env.py`](../../../bajutsu/env.py) erase/boot/launch). Because every edge is a recorded `Step`, the path from the entry screen to any discovered node *is* a deterministic scenario; replaying it (reusing the orchestrator's `_do_action` / `_wait`, [`orchestrator.py`](../../../bajutsu/orchestrator.py)) lands on the node, and the crawl then takes the next untried action. This keeps the whole traversal on the determinism-friendly primitives the tool already has, and means every node already has a committed, replayable path to it.

Between actions the crawl waits on a condition (`screenChanged` / target element), never a fixed sleep ([DESIGN §6.3](../../../DESIGN.md)).

### Crash and stuck-state detection (deterministic signals)

After each action the crawl checks, deterministically, that the app is still healthy:

- **Crash** = the app process is no longer foreground/alive (`simctl`/env pid check; the a11y tree collapsing to a bare window with no app content, reusing `_shows_app_ui` from [`record.py`](../../../bajutsu/record.py) to distinguish this from a system alert, which the existing alert guard clears instead). On a crash the crawl captures the full evidence set via the existing `result:error` safety net ([DESIGN §9](../../../DESIGN.md)) and emits the replayed path as a **minimal repro scenario**.
- **Stuck / dead end** = a state with no actionable elements, or where no candidate action produces a screen change within the condition-wait budget.
- **Unreachable** = a state that was *expected* but never reached. Two sources of expectation: declared deeplinks (each `--seed` / configured deeplink that fails to open) and the app's declared id namespaces ([DESIGN §7.3](../../../DESIGN.md)) whose screens never appeared in the map. These are reported as gaps, not failures.

None of these classifications is an LLM judgment: a crash is a process fact, "stuck" is "no edge changed the state fingerprint", "unreachable" is set subtraction over declared vs. seen. AI stays out of the verdict.

### The optional AI layer (`--guide ai`, strictly Tier 1)

The crawl is useful with no AI at all. When enabled, the agent — an extension of the existing [`Agent` protocol](../../../bajutsu/agent.py) — only *guides exploration*, never evaluates results:

- **Prioritize the frontier:** rank candidate actions by likely interest (e.g. "Checkout" over "About") instead of plain breadth-first, to spend a limited budget well.
- **Supply realistic inputs** for text fields (a valid-looking email, a search term).
- **Collapse near-duplicate states** the structural fingerprint would otherwise split (e.g. a list whose rows differ only by data).

This reuses the `record` infrastructure directly (`_screenshot_bytes`, the alert guard plumbing, `_execute_with_recovery`). The boundary is firm: the agent influences *what to explore*, the deterministic checks above decide *what happened*.

### Outputs

Everything lands under `runs/<runId>/` alongside the existing `manifest.json` ([DESIGN §9](../../../DESIGN.md)):

- **Screen map** — `screenmap.json` (nodes + edges) plus a rendered graph (Mermaid/Graphviz) in `report.html`; each node links to its screenshot and `elements` dump.
- **Crash report** — each crash with its repro `Scenario` (emitted as YAML via `dump_scenarios`, directly runnable by `run`).
- **Candidate scenarios** — per discovered flow, a draft scenario the user can promote to a real test. As with all AI authoring, these are emitted as *proposals* for human review, never written silently into committed YAML ([DESIGN §6.5](../../../DESIGN.md)).
- **Coverage feed** — the per-screen `elements` dumps that `doctor --app <name> --from <runId>` consumes for whole-app coverage ([DESIGN §7.2](../../../DESIGN.md)).

### Relationship to other items

- **`record`** ([recording.md](../../../docs/recording.md)) — crawl is the breadth-first complement; discovered screens seed `record` goals. They share the loop, alert handling, and evidence plumbing.
- **[BE-0012 action-capture record](../../BE-0012-action-capture-record/BE-0012-action-capture-record.md) / [BE-0014 record demarcation](../../BE-0014-record-demarcation/BE-0014-record-demarcation.md)** — those cover *human-driven* capture and how it is demarcated from AI `record`; crawl is a third, fully autonomous, authoring front end and should be demarcated alongside them.
- **[BE-0024 doctor / onboarding](../../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)** — crawl supplies the whole-app coverage input doctor's §7.2 measurement needs.

## Alternatives considered

**Pure random "monkey" testing** (random taps, like Android Monkey / UIAutomation fuzzing). Trivial to build and good at shaking out crashes, but it produces no clean screen map, does not respect id stability, and its paths are not reproducible — so it cannot feed `record` or `doctor` and cannot emit a deterministic repro. We prefer systematic id-driven breadth-first traversal; a random fuzz mode is worth keeping only as a fallback for coordinate-only apps with almost no identifiers.

**Pure-AI free exploration** (reuse `record` as-is with a vague goal like "explore everything"). The LLM wanders turn by turn with no explicit state graph, so it loops, re-visits, and cannot deduplicate or measure coverage; it is also slow and expensive. Crawl needs an explicit state model and frontier; AI is an *optional guide* on top, not the engine — and is kept out of the result regardless.

**Making crawl itself a CI / Tier 2 gate** ("fail the build if the crawl finds a crash"). Tempting because crash detection is deterministic, but the crawl *path* is not — a flaky, changing crawl as a required gate violates determinism-first ([DESIGN §2](../../../DESIGN.md)). Resolution: the crawl stays Tier 1 discovery, and its emitted repro scenarios become Tier 2 gates after human review — the normal `record → run` promotion. Crashes reach CI as committed scenarios, not as the crawl.

**Static analysis of the app binary/source** to build the screen map without running it. Out of scope — Bajutsu is app-agnostic and consumes built artifacts only ([DESIGN §1](../../../DESIGN.md)) — and it cannot find runtime crashes, which is half the value.

## Progress

- [x] Crawl engine and CLI — breadth-first traversal by deterministic replay, the `ScreenMap` state graph, crash/stuck detection, the optional `--guide ai` layer, and the parallel pool.
- [x] Screen-map output — `screenmap.json` and per-screen `screens/<fingerprint>.png`.
- [x] Live **Crawl** tab in serve.
- [x] Rendered screen-map graph — a self-contained `screenmap.html` ([#307](https://github.com/bajutsu-e2e/bajutsu/pull/307)).
- [x] Automatic crash-repro scenario emission — one `crashes/crash-NNN.yaml` per faithfully reproducible crash ([#319](https://github.com/bajutsu-e2e/bajutsu/pull/319)).
- [ ] AI-guided text-input supply for form-heavy flows.
- [ ] Candidate-scenario proposals per discovered flow.

Shipped across [#80](https://github.com/bajutsu-e2e/bajutsu/pull/80) / [#307](https://github.com/bajutsu-e2e/bajutsu/pull/307) / [#319](https://github.com/bajutsu-e2e/bajutsu/pull/319); the two AI-assisted authoring slices remain.

## References

[recording.md](../../../docs/recording.md), [drivers.md](../../../docs/drivers.md), [concepts.md](../../../docs/concepts.md), [DESIGN §2 / §3.1 / §5 / §7.2 / §9](../../../DESIGN.md), [BE-0012](../../BE-0012-action-capture-record/BE-0012-action-capture-record.md), [BE-0014](../../BE-0014-record-demarcation/BE-0014-record-demarcation.md), [BE-0024](../../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
</content>
</invoke>
