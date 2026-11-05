**English** · [日本語](BE-0038-autonomous-crawl-exploration-ja.md)

# BE-0038 — Autonomous crawl exploration (App Explorer style)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0038](BE-0038-autonomous-crawl-exploration.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0038") |
| Implementing PR | [#80](https://github.com/bajutsu-e2e/bajutsu/pull/80), [#83](https://github.com/bajutsu-e2e/bajutsu/pull/83), [#307](https://github.com/bajutsu-e2e/bajutsu/pull/307), [#319](https://github.com/bajutsu-e2e/bajutsu/pull/319), [#716](https://github.com/bajutsu-e2e/bajutsu/pull/716) |
| Topic | Autonomous crawl |
| Origin | Autify VAX |
<!-- /BE-METADATA -->

## Introduction

AI autonomously crawls screen transitions, generates a screen map, and reports crashes and unreachable states. This extends the [Tier 1](../../docs/glossary.md#the-two-tiers) `record` capability.

`record` is *goal-directed*. The AI explores toward one natural-language goal and writes out one deterministic scenario ([recording.md](../../docs/recording.md)). `crawl` is *breadth-first*. It systematically visits as many screens as it can reach, builds a graph of the screens and the transitions between them, and reports what it found. It is an exploration and discovery tool, never a pass/fail gate.

## Motivation

The current authoring path assumes that you already know the flow you want to test. You give `record` a goal ("log in and open settings"), and it produces a scenario for that path. That is the right tool once you know what to write. It leaves three gaps, which a crawl fills.

**1. Discovery before authoring.** Onboarding a new app involves the [`apps.<name>`](../../DESIGN.md) per-app config and [BE-0024 doctor / onboarding](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md). There the hardest question is "what flows even exist, and which screens are worth testing?" Today the only answers come from reading the app by hand. A crawl produces a screen map: a labeled inventory of reachable screens, with a representative screenshot and element dump for each. The map turns "I don't know the app" into "here are its 40 screens, pick the ones to author scenarios for." This is the natural front end to `record`. Each discovered screen, together with the shortest path to it, becomes a seed goal.

**2. Coverage measurement across the whole app.** [DESIGN §7.2](../../DESIGN.md) is explicit that `doctor --app` measures accessibility-identifier coverage only on the entry screen and on screens reachable by a declared deeplink. It already anticipates a `doctor --app <name> --from <runId>` mode. That mode reuses the per-screen `elements` dumps left by a prior run to compute whole-app coverage honestly. A crawl is the run that produces those dumps. It makes the §7.2 promise of "honesty of the measurement range" real. The coverage report is computed over every screen the crawl actually reached, not over a guess.

**3. Robustness smoke testing.** A systematic crawl exercises the app far more widely than a handful of authored happy paths do. It surfaces crashes, hangs, and dead ends that scenario tests never visit. This is the "App Explorer / Robo test" value, and the Autify VAX origin of this item. The coverage is cheap, broad, and needs no authoring. It catches "the app crashes if you tap *that* in *this* order" before a user does.

**The determinism boundary is the whole point of doing this in Bajutsu.** A crawl is non-deterministic by nature, because exploration order, AI choices, and app state all vary from run to run. The crawl itself can therefore never be a CI gate ([prime directive #1](../../CLAUDE.md): *AI is the author and the failure investigator, never the judge*). Bajutsu adds one thing over a generic monkey or explorer: the crawl's *byproducts* are first-class deterministic scenarios. A crash's reproducing path and each discovered flow are emitted as plain YAML. The user reviews that YAML, and once it is committed, `run` replays it AI-free as a [Tier 2](../../docs/glossary.md#the-two-tiers) regression. Crashes *do* reach CI, as committed repro scenarios and not as the flaky crawl. This is the same hub model that the rest of the tool uses: the scenario is the shared artifact, AI authors it once, and the deterministic runner owns it forever.

## Detailed design

### Command surface

A new Tier 1 command mirrors `record`'s flags:

```
bajutsu crawl --app <name>
    [--max-screens N] [--max-steps N] [--budget DURATION] [--max-depth N]
    [--seed <deeplink> ...]          # extra entry points besides the launch screen
    [--guide ai|off]                 # whether to use AI to prioritize/handle inputs
    [--backend idb] [--udid booted] [--dismiss-alerts]
    [--out runs/<runId>]
```

Like `record`, `crawl` is an *AI-live* path and not part of the deterministic gate ([DESIGN §3.1](../../DESIGN.md)). With `--guide off`, the crawl runs purely on the identifier-driven heuristics below and needs no `ANTHROPIC_API_KEY`. With `--guide ai`, the optional agent layers on top. Either way, AI never decides pass/fail.

### State model

The nodes of the graph are states. Each screen the crawl visits is reduced to a **state fingerprint**, so that revisiting the same screen is recognized instead of re-explored. The fingerprint is derived from `driver.query()` ([`drivers/base.py`](../../bajutsu/drivers/base.py)):

- **Primary:** the sorted set of `accessibilityIdentifier`s present on screen, hashed. Identifiers are non-localized and data-derived ([DESIGN §7.3](../../DESIGN.md)), so this fingerprint is stable across locales and minor content changes. The property that makes id-based selectors deterministic also makes id-based state identity deterministic.
- **Fallback (low-id screens):** when too few elements carry ids, the crawl falls back to a structural hash over `(traits, frame-bucket)` of the actionable elements. This hash is less stable and is flagged in the report, mirroring the stability-ladder honesty rule ([DESIGN §5](../../DESIGN.md)).

The fingerprint is the graph node identity. A node stores a representative screenshot, the `elements` dump, and the id set. These are the same per-screen artifacts that §7.2 needs.

### Frontier and action selection

The edges of the graph are actions. From a state, the **candidate edges** are its actionable elements: those with `button`, `link`, `textField`, or `searchField` traits. Elements that carry an `id` are preferred (stability ladder, [DESIGN §5](../../DESIGN.md)). The crawl keeps a frontier of unexplored `(state, action)` pairs and traverses it breadth-first. Candidate ordering within a state is deterministic, sorted by id. Two crawls of an unchanged app therefore explore in the same order, as far as the app's own non-determinism allows. This matters for the reproducibility of the resulting maps.

Text fields need an input value. With `--guide off`, the crawl uses a fixed safe placeholder per trait. With `--guide ai`, the optional agent supplies context-appropriate input, which is still Tier 1 (see below).

### Traversal by deterministic replay, not in-place backtracking

App transitions are usually irreversible, because you cannot reliably "undo" a tap. Navigating backward would be flaky, so the crawl does not try it. The crawl revisits a frontier node the same way `run` reaches any state: it **erases to a clean environment and replays the known shortest path to that node** ([DESIGN §2](../../DESIGN.md) environment cleanliness; [`simctl.py`](../../bajutsu/simctl.py) erase/boot/launch).

Every edge is a recorded `Step`, so the path from the entry screen to any discovered node *is* a deterministic scenario. Replaying it reuses the orchestrator's `_do_action` / `_wait` ([`orchestrator.py`](../../bajutsu/orchestrator.py)) and lands on the node. The crawl then takes the next untried action. This keeps the whole traversal on the determinism-friendly primitives that the tool already has. Every node also has a committed, replayable path to it.

Between actions, the crawl waits on a condition (`screenChanged` / target element), never a fixed sleep ([DESIGN §6.3](../../DESIGN.md)).

### Crash and stuck-state detection

After each action, the crawl deterministically checks that the app is still healthy, classifying what it finds in one of three ways:

- **Crash:** the app process is no longer foreground or alive. The check uses a `simctl` / env pid check. It also covers the a11y tree collapsing to a bare window with no app content. The crawl reuses `_shows_app_ui` from [`record.py`](../../bajutsu/record.py) to tell that case from a system alert, which the existing alert guard clears instead. On a crash, the crawl captures the full evidence set through the existing `result:error` safety net ([DESIGN §9](../../DESIGN.md)). It then emits the replayed path as a **minimal repro scenario**.
- **Stuck / dead end:** a state with no actionable elements, or a state where no candidate action produces a screen change within the condition-wait budget.
- **Unreachable:** a state that was *expected* but never reached. Expectations have two sources. The first is declared deeplinks: each `--seed` or configured deeplink that fails to open. The second is the app's declared id namespaces ([DESIGN §7.3](../../DESIGN.md)) whose screens never appeared in the map. The crawl reports these as gaps, not failures.

None of these classifications is an LLM judgment. A crash is a process fact. "Stuck" means that no edge changed the state fingerprint. "Unreachable" is set subtraction over declared versus seen. AI stays out of the verdict.

### The optional AI layer

The `--guide ai` layer is strictly Tier 1. The crawl is useful with no AI at all. When the layer is enabled, the agent, an extension of the existing [`Agent` protocol](../../bajutsu/agent_protocols.py), only *guides exploration*. It never evaluates results:

- **Prioritize the frontier:** rank candidate actions by likely interest (for example, "Checkout" over "About") instead of plain breadth-first order, to spend a limited budget well.
- **Supply realistic inputs** for text fields, such as a valid-looking email or a search term.
- **Collapse near-duplicate states** that the structural fingerprint would otherwise split, for example a list whose rows differ only by data.

The layer reuses the `record` infrastructure directly (`_screenshot_bytes`, the alert guard plumbing, `_execute_with_recovery`). The boundary is firm. The agent influences *what to explore*, and the deterministic checks above decide *what happened*.

### Outputs

Everything lands under `runs/<runId>/`, alongside the existing `manifest.json` ([DESIGN §9](../../DESIGN.md)):

- **Screen map:** `screenmap.json` (nodes and edges), plus a rendered graph (Mermaid/Graphviz) in `report.html`. Each node links to its screenshot and `elements` dump.
- **Crash report:** each crash with its repro `Scenario`, emitted as YAML through `dump_scenarios` and directly runnable by `run`.
- **Candidate scenarios:** per discovered flow, a draft scenario that the user can promote to a real test. As with all AI authoring, these are emitted as *proposals* for human review, never written silently into committed YAML ([DESIGN §6.5](../../DESIGN.md)).
- **Coverage feed:** the per-screen `elements` dumps that `doctor --app <name> --from <runId>` consumes for whole-app coverage ([DESIGN §7.2](../../DESIGN.md)).

### Relationship to other items

- **`record`** ([recording.md](../../docs/recording.md)): crawl is the breadth-first complement, and discovered screens seed `record` goals. The two share the loop, alert handling, and evidence plumbing.
- **[BE-0012 action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) / [BE-0014 record demarcation](../BE-0014-record-demarcation/BE-0014-record-demarcation.md):** those items cover *human-driven* capture and how it is demarcated from AI `record`. Crawl is a third, fully autonomous, authoring front end and should be demarcated alongside them.
- **[BE-0024 doctor / onboarding](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md):** crawl supplies the whole-app coverage input that doctor's §7.2 measurement needs.

## Alternatives considered

**Pure random "monkey" testing** (random taps, like Android Monkey or UIAutomation fuzzing). It is trivial to build and good at shaking out crashes. It produces no clean screen map, does not respect id stability, and has paths that are not reproducible. It therefore cannot feed `record` or `doctor`, and it cannot emit a deterministic repro. We prefer systematic id-driven breadth-first traversal. A random fuzz mode is worth keeping only as a fallback for coordinate-only apps with almost no identifiers.

**Pure-AI free exploration** (reuse `record` as-is with a vague goal like "explore everything"). The LLM wanders turn by turn with no explicit state graph. It loops, revisits screens, and cannot deduplicate or measure coverage. It is also slow and expensive. Crawl needs an explicit state model and frontier. AI is an *optional guide* on top, not the engine, and is kept out of the result regardless.

**Making crawl itself a CI / Tier 2 gate** ("fail the build if the crawl finds a crash"). The idea is tempting, because crash detection is deterministic. The crawl *path* is not deterministic, though, and a flaky, changing crawl as a required gate violates determinism-first ([DESIGN §2](../../DESIGN.md)). The resolution is as follows. The crawl stays Tier 1 discovery, and its emitted repro scenarios become Tier 2 gates after human review. That is the normal `record → run` promotion. Crashes reach CI as committed scenarios, not as the crawl.

**Static analysis of the app binary or source** to build the screen map without running the app. Out of scope. Bajutsu is app-agnostic and consumes built artifacts only ([DESIGN §1](../../DESIGN.md)). Static analysis also cannot find runtime crashes, which are half the value.

## Progress

- [x] Crawl engine and CLI — breadth-first traversal by deterministic replay, the `ScreenMap` state graph, crash/stuck detection, the optional `--guide ai` layer, and the parallel pool.
- [x] Screen-map output — `screenmap.json` and per-screen `screens/<fingerprint>.png`.
- [x] Live **Crawl** tab in serve.
- [x] Rendered screen-map graph — a self-contained `screenmap.html` ([#307](https://github.com/bajutsu-e2e/bajutsu/pull/307)).
- [x] Automatic crash-repro scenario emission — one `crashes/crash-NNN.yaml` per faithfully reproducible crash ([#319](https://github.com/bajutsu-e2e/bajutsu/pull/319)).
- [x] AI-guided text-input supply for form-heavy flows — the crawl guide proposes realistic `type` values and compound `fill` (several fields at once, so a submit stays valid) ([#83](https://github.com/bajutsu-e2e/bajutsu/pull/83)).
- [x] Candidate-scenario proposals per discovered flow — one `flows/flow-NNN.yaml` draft per faithfully reachable screen, built from the replayable path the crawl recorded to it, for a user to promote into a Tier-2 test ([#716](https://github.com/bajutsu-e2e/bajutsu/pull/716)).

Shipped across [#80](https://github.com/bajutsu-e2e/bajutsu/pull/80) / [#83](https://github.com/bajutsu-e2e/bajutsu/pull/83) / [#307](https://github.com/bajutsu-e2e/bajutsu/pull/307) / [#319](https://github.com/bajutsu-e2e/bajutsu/pull/319) and the candidate-flow slice ([#716](https://github.com/bajutsu-e2e/bajutsu/pull/716)). The crawl now emits both crash repros and candidate flow scenarios, which completes the item.

## References

[recording.md](../../docs/recording.md), [drivers.md](../../docs/drivers.md), [concepts.md](../../docs/concepts.md), [DESIGN §2 / §3.1 / §5 / §7.2 / §9](../../DESIGN.md), [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md), [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md), [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md)
</content>
</invoke>
