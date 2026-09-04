**English** · [日本語](ja/developer-guide.md)

# Developer guide: code structure, the scenario DSL, and the development routine

> A guide to the source tree and how to work in it: where each file sits, which classes live in it,
> how the scenario DSL (domain-specific language) and its `Selector` fields work, what happens
> inside each class while a scenario runs, and the routine for developing and verifying a change.

Related: [architecture](architecture.md) · [concepts](concepts.md) · [run loop](run-loop.md) ·
[drivers](drivers.md) · [glossary](glossary.md)

---

## How to read this page

The page runs in one direction, from the outside in. It opens with a picture of the whole system,
then locates every file, then names the handful of types that carry the rest of the code. After
that it follows a single command through the code and describes each class in turn. A
reader who starts at the top and stops anywhere still ends with a complete, if coarser, picture.

Three companion pages carry the depth this reading guide leaves out.
[Architecture](architecture.md) holds the module table and the dependency-layer contract.
[Run loop](run-loop.md) explains the deterministic runner's semantics. [Drivers](drivers.md)
explains each backend's platform seam. The [glossary](glossary.md) defines the domain vocabulary
term by term.

Two vocabulary notes make the rest readable. A **[scenario](glossary.md#scenario-authoring)** is one
YAML file describing a user flow as a list of steps. A
**[backend](glossary.md#driver-backend-actuator-platform)** is one platform's implementation of the driver
interface — XCUITest for the iOS Simulator, adb for Android, Playwright for a web browser.

---

## 1. The shape of the system

Bajutsu splits into two tiers that never mix. Tier 1 uses a large language model (LLM) to *author* a
scenario and to *investigate* a failure. Tier 2 replays that scenario deterministically and derives
the verdict from machine-checkable assertions, with no model anywhere on the path. The scenario file
is the hub between the two: Tier 1 writes it, humans own and edit it afterwards, and Tier 2 consumes
it.

![Conceptual diagram. A natural-language goal enters Tier 1, where the record and crawl commands drive an agent that writes a scenario YAML file. Humans edit that file directly. Tier 2 reads the same file: the runner leases a device, the orchestrator executes each step through one Driver interface, the assertion evaluator produces the verdict, and the reporter writes the run artifacts. The Driver interface is the single platform seam, behind which sit the XCUITest, adb, Playwright, and fake backends. On failure the triage command reads the run artifacts and proposes edits back to the scenario.](assets/diagrams/developer-guide-concept.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-concept.svg -->
```mermaid
flowchart TB
    goal(["Natural-language goal"])
    human(["Human edit"])
    scenario[["Scenario YAML<br/>the shared hub"]]

    subgraph t1["Tier 1 · LLM authors and investigates"]
        rec["record / crawl"]
        agent["Agent<br/>proposes one step at a time"]
        rec <--> agent
    end

    subgraph t2["Tier 2 · deterministic, no LLM"]
        runner["runner<br/>leases a device, launches the app"]
        orch["orchestrator<br/>act, wait, verify"]
        driver{{"Driver interface<br/>the one platform seam"}}
        asserts["assertions<br/>the verdict"]
        report["report<br/>manifest, JUnit, CTRF, HTML"]
        runner --> orch --> driver
        orch --> asserts --> report
    end

    backends["XCUITest · adb · Playwright · fake"]
    triage["triage<br/>root cause, advisory"]

    goal --> rec
    rec ==> scenario
    human ==> scenario
    scenario ==> runner
    driver --> backends
    report --> triage
    triage -.->|proposes edits| scenario

    classDef ai fill:#fde68a,stroke:#d97706,color:#1f2937;
    classDef det fill:#bfdbfe,stroke:#2563eb,color:#1f2937;
    class t1 ai
    class t2 det
```

</details>

Three properties of the picture explain most of the code's shape.

- **The verdict path holds no model.** Prime directive 1 forbids an LLM anywhere in `run` or in the
  continuous integration (CI) gate, so the whole Tier 2 column stays free of the AI packages.
- **One seam, many platforms.** Every platform difference hides behind the `Driver` interface, so a
  new platform means a new backend rather than a fork of the core.
- **The scenario is the durable artifact.** Nothing else persists between sessions, so the scenario
  schema doubles as the contract every feature reads.

---

## 2. Where the files live

### 2.1 The repository's top level

| Path | What lives there |
|---|---|
| [`bajutsu/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu) | The Python logic core: 324 files, roughly 78,000 lines. Everything the rest of this page describes. |
| [`tests/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/tests) | The deterministic test suite: 381 files, roughly 125,000 lines. Larger than the code it covers. |
| [`BajutsuKit/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/BajutsuKit) | The Swift test-support package: the resident XCUITest runner, the in-app collector, the WebView and z-order channels. |
| `BajutsuAndroid/` · `BajutsuAndroidUIAutomatorServer/` · `IdentifierTool/` | The Kotlin counterparts: the in-app hooks, the resident UI Automator server, and the identifier-tagging library. |
| [`demos/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/demos) | Runnable examples, including the showcase fixture built five times across platforms. |
| [`scenarios/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/scenarios) | Scenario YAML files the repository runs against its own fixtures. |
| [`docs/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/docs) · `docs/ja/` | This documentation, English and its Japanese mirror. |
| [`roadmaps/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/roadmaps) | One directory per roadmap item, bilingual, carrying the rationale behind each decision. |
| [`scripts/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/scripts) | Repository tooling: the linters, the diagram renderer, the repository map. |

### 2.2 The `bajutsu/` package

The package splits into a shared `common/` core and one directory per user-facing command. A command
directory holds that command's own logic plus its `cli.py`, which registers the Typer command. The
map below groups the directories by the job they do rather than alphabetically.

![Package map. The command layer holds cli, run, record, crawl, triage, codegen, mcp, serve, and analysis. Below it, the execution core holds common/runner, common/orchestrator, and common/platform_lifecycle. Below that, the platform seam holds common/drivers, common/backend_cli, and common/backends. To the side, the contract layer holds common/scenario and common/config, and the output layer holds common/assertions, common/evidence, and common/report. A separate periphery column holds common/agents and common/ai, which the command layer reaches but the execution core never does.](assets/diagrams/developer-guide-packages.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-packages.svg -->
```mermaid
flowchart TB
    subgraph cmd["Commands · one directory each, plus its cli.py"]
        c1["run/ · record/ · crawl/ · triage/"]
        c2["codegen/ · mcp/ · serve/ · analysis/"]
        c3["cli/ · assembles the Typer app"]
    end

    subgraph exec["Execution core · common/"]
        r["runner/<br/>device pool, launch, pipeline"]
        o["orchestrator/<br/>the step loop"]
        pl["platform_lifecycle/<br/>app bring-up per platform"]
    end

    subgraph seam["Platform seam · common/"]
        d["drivers/<br/>Driver protocol + backends"]
        bc["backend_cli/<br/>simctl, adb wrappers"]
        b["backends.py<br/>actuator selection"]
    end

    subgraph contract["Contract · common/"]
        s["scenario/<br/>pydantic schema + loaders"]
        cfg["config/<br/>defaults, per-target Effective"]
    end

    subgraph out["Output · common/"]
        a["assertions/<br/>the verdict"]
        e["evidence/<br/>artifacts per step"]
        rep["report/<br/>manifest, JUnit, CTRF, HTML"]
    end

    subgraph peri["Periphery · common/"]
        ag["agents/<br/>authoring, triage, alerts"]
        ai["ai/<br/>vendor-neutral LLM seam"]
    end

    cmd --> exec
    cmd -.-> peri
    exec --> seam
    exec --> contract
    exec --> out
    peri -.-> seam
    out --> contract

    classDef det fill:#bfdbfe,stroke:#2563eb,color:#1f2937;
    classDef aip fill:#fde68a,stroke:#d97706,color:#1f2937;
    class exec,seam,contract,out det
    class peri aip
```

</details>

`common/` holds a further set of single-purpose helpers: `capability/` (what a backend supports),
`run_meta/` (run identity and artifact storage), `analytics/` (token and cost accounting),
`evidence/`, `devices/`, `provisioning/`, `github/`, and `cloud/`. The
[module table in architecture.md](architecture.md#module-list-and-roles) describes each in one row.

### 2.3 Every package, in one table

The diagram above groups directories by role; the tables below list every one of them, with the
file and line counts `make repo-map ARGS="--code"` reports and the modules each holds — a fuller
inventory than the diagram's boxes have room for, grouped the same way.

**Commands** — one directory per user-facing feature, plus its own `cli.py`.

| Package | Files, lines | Holds |
|---|---|---|
| `cli/` | 4, 786 | Typer app assembly: `_shared`, `dotenv`, `handoff` |
| `cli/commands/` | 5, 448 | Commands with no owning feature: `doctor`, `lint`, `report`, `schema` |
| `run/` | 3, 1,701 | `cli` (the `run` command), `notify` |
| `record/` | 4, 1,318 | `capture`, `cli`, `loop` |
| `crawl/` | 9, 3,109 | `cli`, `core`, `flows`, `guide`, `report`, `repro`, `serialize`, `tabs` |
| `triage/` | 3, 1,203 | `cli`, `heuristic` |
| `codegen/` | 7, 2,623 | `cli`, `common`, `xcuitest`, `playwright`, `uiautomator` |
| `mcp/` | 4, 327 | `cli`, `resources`, `tools` |
| `analysis/` | 7, 2,543 | `audit`, `coverage`, `flakiness`, `impact`, `stats`, `trace` |
| `analysis/cli/` | 8, 870 | One Typer command per report |
| `serve/` | 28, 8,457 | The local web UI's own top-level modules (`state`, `routes`, `handler`, `executor`, `jobs`, …); `cli/`, `operations/`, and `server/` break out below |
| `serve/cli/` | 4, 873 | `serve`, `worker`, `approve` |
| `serve/operations/` | 27, 6,997 | One module per web-UI operation: `capture`, `codegen`, `coverage`, `doctor`, `enrich`, `evidence`, `lint`, `metrics`, `runs`, `triage`, `upload`, … |
| `serve/server/` | 17, 3,691 | The hosted (multi-tenant) backend: `app`, `db`, `executor`, `models`, `oauth`, `artifacts`, `baselines`, `sessions`, `secrets` |

**Execution core** — the device pool, the step loop, and per-platform app bring-up.

| Package | Files, lines | Holds |
|---|---|---|
| `common/runner/` | 11, 3,122 | `pipeline`, `pool`, `launch`, `device_provider`, `recovery`, `mailbox`, `build`, `types` |
| `common/orchestrator/` | 6, 3,534 | `loop`, `waits`, `substitution`, `evidence_rules`, `types` |
| `common/orchestrator/actions/` | 2, 139 | The action-handler registry (`_registry`) |
| `common/orchestrator/actions/handlers/` | 10, 1,254 | `gestures`, `scroll`, `device`, `navigation`, `http`, `generate`, `totp`, `manual` |
| `common/platform_lifecycle/` | 7, 968 | `protocols`, `factories`, `readiness`, `relaunchers`, `device_control`, `read_session` |
| `common/platform_lifecycle/environments/` | 8, 3,115 | `ios`, `xcuitest`, `xcuitest_live`, `android`, `web`, `fake` |

**Platform seam** — the `Driver` protocol and every backend behind it.

| Package | Files, lines | Holds |
|---|---|---|
| `common/drivers/` | 14, 7,198 | `base`, `actuation`, `coordinate_tree`, `fake`, `xcuitest`, `adb`, `playwright`, `xcuitest_live`, `elements`, `dom`, `web_network`, `webview`, `zorder` |
| `common/backend_cli/` | 4, 2,428 | `simctl`, `adb`, `adb_resident` |
| `common/devices/` | 4, 181 | `errors`, `id`, `os` |
| `common/capability/` | 4, 548 | `capabilities`, `capability_preflight`, `preflight` |

**Contract** — the scenario schema and the resolved-config shape.

| Package | Files, lines | Holds |
|---|---|---|
| `common/scenario/` | 9, 1,232 | `load`, `load_expanded`, `expand`, `select`, `serialize`, `edit`, `interp`, `system_alerts` |
| `common/scenario/models/` | 9, 1,942 | `scenario`, `steps`, `actions`, `assertions`, `selector`, `evidence`, `mocks`, `_base` |
| `common/config/` | 5, 1,234 | `schema`, `effective`, `resolve`, `accessors` |

**Output** — the verdict, the captured evidence, and the rendered report.

| Package | Files, lines | Holds |
|---|---|---|
| `common/assertions/` | 6, 1,225 | `evaluate`, `network`, `visual`, `schema`, `_common` |
| `common/evidence/` | 9, 3,440 | `core`, `golden`, `intervals`, `media`, `network`, `redaction`, `sink`, `visual` |
| `common/report/` | 11, 2,176 | `manifest`, `ctrf`, `format`, `rows`, `panels`, `html`, `richtext`, `archive`, `load`, `from_grouping` |

**Periphery** — the paths that reach a model.

| Package | Files, lines | Holds |
|---|---|---|
| `common/agents/` | 12, 2,386 | `protocols`, `factory`, `claude`, `claude_backed`, `claude_enrich`, `claude_triage`, `ai_config`, `anthropic_client`, `availability`, `enrich`, `alerts` |
| `common/ai/` | 8, 1,072 | `base`, `registry`, `anthropic`, `claude_code`, `disabled`, `prompts`, `banner` |

**Shared utilities under `common/`** — single-purpose helpers no other group owns.

| Package | Files, lines | Holds |
|---|---|---|
| `common/` (flat files) | 14, 2,652 | `backends`, `doctor`, `lint`, `mailbox`, `totp`, `cancellation`, `config_source`, `deprecations`, `diagnostics`, `handoff`, `screenshots`, `stall_diagnostics`, `_yaml` |
| `common/analytics/` | 4, 862 | `ledger`, `stats`, `usage` |
| `common/run_meta/` | 6, 602 | `files`, `id`, `root`, `artifact_perms`, `object_store` |
| `common/provisioning/` | 3, 364 | `provision`, `requirements` |
| `common/cloud/` | 2, 678 | `devicefarm` |
| `common/github/` | 4, 232 | `actions`, `app`, `errors` |

### 2.4 Finding a file without reading the tree

Three commands print a fresh map on every run, so no committed index goes stale.

```bash
make repo-map ARGS="--code"              # every package and top-level module
make repo-map ARGS="--docs"              # every docs page, with its own summary
make repo-map ARGS="--headings docs/x.md" # one file's headings and their spans
```

---

## 3. The four contracts everything else builds on

Four types carry the load. Most of the remaining classes exist to produce one of the four, consume
one, or move one across a boundary. Learning the four first makes every later class legible.

![Class diagram of the four contracts. Scenario holds a name, preconditions, before, steps, expect, after, and capturePolicy, and aggregates Step, which in turn holds Assertion and Selector. Driver is a protocol with query, tap, type_text, swipe, wait_for, screenshot, and capabilities, and it returns Element values and accepts runtime Selector values. Effective is the resolved configuration for one target, holding a target name, a platform_config discriminated union, a backend list, and run defaults. RunResult holds a scenario name, an ok flag, StepOutcome values, the trailing assertion results, and artifacts, and the reporter renders it.](assets/diagrams/developer-guide-contracts.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-contracts.svg -->
```mermaid
classDiagram
    class Scenario {
        <<pydantic, authoring>>
        +str name
        +Preconditions preconditions
        +list~Step~ before
        +list~Step~ steps
        +list~Assertion~ expect
        +list~AfterRule~ after
        +list~CaptureRule~ capture_policy
    }
    class Step {
        <<pydantic, authoring>>
        +Selector tap
        +TypeText type
        +Wait wait
        +list~Assertion~ assert_
        +39 action fields in all
    }
    class Assertion {
        <<pydantic, authoring>>
        +Exists exists
        +TextMatch value
        +RequestMatch request
        +14 kinds in all
    }
    class Driver {
        <<Protocol, runtime>>
        +query() list~Element~
        +tap(Selector)
        +type_text(str)
        +swipe(Point, Point)
        +wait_for(Selector) bool
        +screenshot(str)
        +capabilities() set~str~
    }
    class Element {
        <<TypedDict, runtime>>
        +str identifier
        +str label
        +list~str~ traits
        +str value
        +Frame frame
    }
    class Effective {
        <<frozen dataclass>>
        +str target
        +PlatformConfig platform_config
        +list~str~ backend
        +str device
        +list~str~ capture
        +RunDefaults run_defaults
    }
    class RunResult {
        <<dataclass>>
        +str scenario
        +bool ok
        +list~StepOutcome~ steps
        +list~AssertionResult~ expect_results
        +list~Artifact~ artifacts
    }

    Scenario "1" *-- "many" Step
    Step "1" *-- "many" Assertion
    Driver ..> Element : returns
    Scenario ..> Driver : executed against
    Effective ..> Driver : configures
    Driver ..> RunResult : produces evidence for
```

</details>

**`Scenario`** ([`common/scenario/models/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/scenario/models))
is the authoring model, built on pydantic with `extra="forbid"`, so an unknown key fails the load
rather than passing unnoticed. A `Step` carries one of 39 action fields, an `Assertion` one of 14
kinds. The strictness matters because the scenario is the single artifact that outlives a session.

**`Driver`** ([`common/drivers/base.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/drivers/base.py))
is the runtime contract: a `Protocol` of 23 methods that every backend satisfies. Beside it
sit the runtime `Element` and `Selector`, both `TypedDict` rather than pydantic models, because the
step loop touches them thousands of times per run. The authoring `Selector` converts to the runtime
one through `as_selector()`.

**`Effective`** ([`common/config/effective.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/config/effective.py))
is one target's fully resolved configuration: the team defaults overlaid by that target's own block,
frozen so nothing downstream rewrites it. The platform-specific knobs narrow behind a single
`platform_config: PlatformConfig` field — a discriminated union of `IosConfig | WebConfig |
AndroidConfig` — rather than one optional field per platform, so a new platform adds a union member
instead of a new sibling field every reader must learn to ignore. Every app-specific difference
lives here, which keeps the tool itself app-agnostic.

**`RunResult`** ([`common/orchestrator/types.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/orchestrator/types.py))
is one scenario's outcome: the verdict, one `StepOutcome` per step, the trailing assertion results,
and every artifact captured along the way. The reporter renders a list of them and nothing else.

---

## 4. The scenario DSL: what a scenario file looks like

A scenario is plain YAML, validated against the `Scenario` / `Step` / `Assertion` models from
section 3 with `extra="forbid"`, so a typo in a key fails the load rather than passing unnoticed.
This section reads one real scenario end to end, then tables the vocabulary it draws from. The full,
normative grammar lives in [DSL grammar](dsl-grammar.md); the authoring guide with worked recipes is
[scenarios](scenarios.md) and [cookbook](cookbook.md).

### 4.1 One scenario, read end to end

[`scenarios/smoke.yaml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scenarios/smoke.yaml),
unedited, checks that this documentation site's own landing page renders:

```yaml
description: >-
  Docs-site smoke — the landing page loads and shows its "Get started" and "GitHub"
  hero buttons. The fastest check that the Playwright backend reaches the live site.
scenarios:
  - name: the landing page shows its hero calls-to-action
    description: Wait for the "Get started" hero, then assert both hero buttons and a populated page.
    steps:
      - wait: { for: { label: "Get started" }, timeout: 15 }
    expect:
      - exists: { label: "Get started" }
      - exists: { labelMatches: "GitHub" }
      - count: { sel: { traits: [link] }, atLeast: 5 }
```

Three parts recur in every scenario. `steps` is the ordered list the step loop (section 6) executes
— here, one `wait` step polls for the hero button instead of sleeping a fixed duration (prime
directive 2). `expect` is the scenario-level assertion block, checked once after the last step —
here, three independent checks, every one of which must pass. A `{ label: … }` /
`{ labelMatches: … }` / `{ traits: […] }` object is a `Selector` (section 3), the same shape a
`tap` or `type` step's target takes.

### 4.2 Selecting the element: the `Selector`

Almost every step and assertion targets one element on screen, and every target is a `Selector` —
the same type introduced as one of the four contracts in section 3. This subsection is the
authoring view: which fields exist, and which to reach for first. The runtime side — how a
`Selector` narrows a `query()` snapshot to exactly one element, and what happens when it doesn't —
is section 7.2's `resolve_unique`; the full reference is [selectors](selectors.md).

Every provided field combines with AND, with two exceptions: `within` is a separate, spatial
filter applied after the rest, and `index` is a last resort applied only once every other field has
narrowed the candidates as far as it can.

| Field | Matches | Role |
|---|---|---|
| `id` | The exact accessibility identifier. Accepts a list, matched as an OR — one candidate per platform's native spelling of the same id — for example `[stable.refresh, stable_refresh]` for an Android View, whose `android:id` cannot hold a dot. | First choice: stable, non-localized, data-derived. |
| `idMatches` | A glob pattern over the identifier (or a list of globs, matched as an OR). | For a deliberate multi-match: a `count` assertion, or a `forEach` step's `sel`. |
| `label` | The exact accessibility label. | Auxiliary; disambiguation only, since a label can be localized. |
| `labelMatches` | A substring or regex over the label. | Auxiliary, for the same reason. |
| `traits` | A subset test against the element's normalized traits (table below). | Auxiliary — narrows by type, for example `traits: [button]`. |
| `value` | The exact accessibility value. | Auxiliary. |
| `within` | Elements whose frame sits inside a container the nested `Selector` resolves to. The accessibility tree Bajutsu reads is flat, so "parent" is geometric rather than structural, and `within` may nest. | Disambiguation, when two candidates share every other field. |
| `index` | The nth of several content-distinct candidates (a negative value counts from the end). | Last resort — flaky, since it depends on paint order. |

`traits` draws from a small, backend-normalized vocabulary (`common/drivers/base.py`'s `Trait`
class):

| Trait | Marks |
|---|---|
| `button` | A tappable button. |
| `link` | A hyperlink. |
| `notEnabled` | The element is disabled — backs the `disabled` / `enabled` assertions. |
| `selected` | The element is selected or toggled — backs the `selected` assertion. |
| `other` | A generic, unclassified element; the resolution algorithm treats it specially (section 7.2). |
| `secureTextField` | The platform itself marks the field secret, so redaction masks its value with no configuration. |

An author who reaches for `index` before `id`, `label`, or `traits` has the ordering backwards:
`id` survives a redesign that only moves the element on screen, while `index` breaks the moment the
screen gains or loses a sibling.

### 4.3 The 39 step kinds

Every `Step` carries exactly one of 39 action fields (`common/scenario/models/steps.py`), enforced
by pydantic validation, not by convention. Two of them — `wait` and `assert` — are conditions the
step loop polls directly; the rest dispatch through the action registry (section 7.5).

| Group | Fields | What they do |
|---|---|---|
| Gestures | `tap`, `tapPoint`, `doubleTap`, `longPress`, `swipe`, `drag`, `scroll`, `pinch`, `rotate`, `back` | Touch and navigation input. |
| Text and selection | `type`, `select`, `clear`, `delete`, `copy`, `selectOption`, `setPickerValue` | Enter, select, and copy text — `copy` requires a prior `select` (section 7.5). |
| Conditions | `wait`, `assert` | Poll a condition or an assertion block; never a fixed sleep. |
| App and device lifecycle | `relaunch`, `setLocation`, `push`, `clearKeychain`, `clearClipboard`, `setClipboard`, `background`, `foreground`, `overrideStatusBar`, `clearStatusBar`, `handleSystemAlert` | Drive the app's lifecycle and the simulated device around it. |
| Data and values | `http`, `totp`, `generate`, `email` | Produce or fetch a value: an HTTP call, a time-based one-time password (TOTP) code, a random value, a mailbox lookup. |
| Composition and control flow | `use`, `web`, `manual`, `if`, `forEach` | Expand a component, scope a block to a WebView, mark a human takeover, or branch and loop. |

### 4.4 The 14 assertion kinds

Every `Assertion` (`common/scenario/models/assertions.py`) carries one of 14 fields, evaluated by
`assertions/evaluate.py` (section 7.6) — a total function that returns a failing result rather than
raising.

| Group | Fields | Checks |
|---|---|---|
| Element state and content | `exists`, `value`, `label`, `count`, `enabled`, `disabled`, `selected` | An element's presence, text, count, or toggled state. |
| Network | `request`, `event`, `requestSequence`, `responseSchema` | Observed HTTP traffic — one exchange, an ordered sequence, or a response body against a schema. |
| Screen comparison | `visual`, `golden` | A screenshot against a baseline image, or an element tree against a recorded one. |
| Clipboard | `clipboard` | The device clipboard's current text. |

### 4.5 Control flow, composition, and data

| Construct | Field(s) | What it does |
|---|---|---|
| Conditional | `if` step (`condition`, `then`, `else`) | Branches on an `Assertion`, evaluated the same way `expect` is. |
| Loop | `forEach` step (`sel`, `as`, `steps`) | Repeats `steps` once per element a `Selector` matches. |
| Component | `use` step, plus a separate component file | Expands a named, parameterized step list at compile time, resolved before the run ever starts (`expand.py`, section 7.1) — the step loop never sees a `use` step. |
| WebView scope | `web` step (`within`, `steps`) | Scopes a nested step list to a WebView reached through the `Selector` in `within`. |
| Interrupt handler | `interrupts:` (scenario-level) | Clears an interstitial screen — a permission dialog, an app-update prompt — that can appear at an unpredictable point mid-run, sharing the enclosing scenario's `vars.*`. |
| Setup / teardown phase | `before:` / `after:` (scenario-level) | Steps run ahead of `steps` and after the verdict exists, reported apart from the main step list. |
| Data-driven rows | `data:` / `dataFile:` | Instantiates one scenario per row of an inline list or a CSV file, substituting `${row.*}`. |

### 4.6 Interpolation: `${namespace.key}`

One primitive (`common/scenario/interp.py`) backs every substitution a scenario can make, keyed by a
flat `bindings` map the caller supplies.

| Namespace | Source | Resolved |
|---|---|---|
| `params.*` | A component's `with:` block | At component-expansion time, before the run starts. |
| `row.*` | A data-driven scenario's `data:` / `dataFile:` row | At data-expansion time, before the run starts. |
| `secrets.*` | The target config's resolved secret values | At run time, from the `Effective` config (section 3). |
| `vars.*` | An `extract` step's captured value | At run time, as the step loop executes — the one namespace that changes mid-run. |

### 4.7 Evidence capture: `capturePolicy`

A scenario's `capturePolicy` (`common/scenario/models/evidence.py`) names rules the step loop fires
repeatedly, rather than a one-shot instruction, so a second run collects the same evidence with no
AI involved. Each rule's `on` trigger is exactly one of `action` (an id-matched step just ran),
`event: screenChanged`, or `result: error`; its `capture` list names the artifact kinds to collect
when the trigger fires. [Evidence](evidence.md) covers the full mechanism, including the baseline
`capture` guarantee every step gets regardless of `capturePolicy`.

---

## 5. Layers, and the boundary the gate enforces

The packages sort into three layers, and the gate checks the sort. `make lint-imports` runs
[import-linter](https://import-linter.readthedocs.io/) against layers declared in `pyproject.toml`,
so a forbidden import fails `make check` rather than surviving until someone notices.

| Layer | Members | Rule |
|---|---|---|
| **Deterministic core** | `orchestrator/`, `runner/`, `drivers/base.py`, `assertions/`, `evidence/`, `report/`, `config/`, `scenario/`, `capability/` | Must not import the periphery, and must stay free of the hosting extras. |
| **Contract** | `scenario/` and `drivers/base.py` | Must not import the runtime core, so a consumer can depend on the schema without pulling the runner in. |
| **Periphery** | `serve/`, `mcp/`, `codegen/`, `agents/`, `ai/`, `record/`, `crawl/`, `triage/` | Each removable behind an optional install extra. |

The core-must-not-import-periphery rule enforces prime directive 1 statically. A pure element-tree
helper that a core module needs lives in the core rather than in `record/`, and the resolved AI
settings live in `config/` as a plain `AiConfig`, so the core reads them without importing an AI
client. [Architecture](architecture.md#enforced-layer-boundaries-be-0112) records the full contract
set.

---

## 6. What happens when a scenario runs

`bajutsu run` is the command every other Tier 2 path resembles. Following it once explains the
runner, the orchestrator, the driver layer, and the reporter together.

![Sequence diagram of one run. The CLI resolves the config and loads the scenarios, then asks the runner pipeline to run them all. The pipeline asks the device pool for a lease; the pool asks the platform environment to boot the device and launch the app, and returns a Lease bundling a live Driver with the evidence sink and the network collector. The pipeline hands the scenario to the orchestrator, which loops per step: it captures a pre-step baseline, dispatches the action to the driver, waits for a condition, evaluates assertions, and captures post-step evidence. After the last step the orchestrator evaluates the trailing expect block and returns a RunResult. The pipeline releases the lease and the reporter writes manifest.json, JUnit XML, CTRF JSON, and the HTML report.](assets/diagrams/developer-guide-run-sequence.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-run-sequence.svg -->
```mermaid
sequenceDiagram
    autonumber
    participant CLI as run/cli.py
    participant Pipe as runner/pipeline.py
    participant Pool as runner/pool.py
    participant Env as platform_lifecycle
    participant Orch as orchestrator/loop.py
    participant Drv as Driver backend
    participant Sink as evidence FileSink
    participant Rep as report/

    CLI->>CLI: resolve config, load and expand scenarios
    CLI->>Pipe: run_and_report(eff, scenarios, lease)
    Pipe->>Pool: lease(eff, scenario)
    Pool->>Env: boot device, install, launch app
    Env-->>Pool: ready driver
    Pool-->>Pipe: Lease(driver, sink, control, collector)
    Pipe->>Orch: run_scenario(driver, scenario, sink, ...)
    loop for each step
        Orch->>Sink: capture pre-step baseline
        Orch->>Drv: act — tap, type, swipe, ...
        Orch->>Drv: wait for condition
        Orch->>Orch: evaluate step assertions
        Orch->>Sink: capture post-step evidence
    end
    Orch->>Orch: evaluate trailing expect block
    Orch-->>Pipe: RunResult
    Pipe->>Pool: lease.release()
    Pipe->>Rep: manifest.json, JUnit, CTRF, HTML
```

</details>

The numbered walk below names the file behind each arrow.

1. **Resolve the configuration.** `run/cli.py` reads the config file, overlays the target's block,
   and produces one frozen `Effective`. Command-line flags override the file.
2. **Load and expand the scenarios.** `common/scenario/load.py` parses each YAML file into
   `Scenario` models. `expand.py` then resolves the compile-time constructs: `use:` macros expand
   into their component's steps, and a data-driven scenario becomes one instance per data row.
3. **Select the backend.** `common/backends.py` picks the actuator. Given several candidates it
   picks the cheapest one whose capability set covers the scenario's needs.
4. **Lease a device.** `common/runner/pool.py` bounds concurrency and hands out a `Lease`, which
   bundles the live driver with that device's evidence sink, relaunch function, device control, and
   network collector. `common/platform_lifecycle/` performs the platform-specific bring-up behind
   one `RunEnvironment` protocol, so the pool never branches on the backend name.
5. **Run the scenario.** `common/orchestrator/loop.py` executes the step list. Section 6.1 covers
   one step in detail.
6. **Decide the verdict.** `common/assertions/evaluate.py` evaluates every assertion. The function
   is total: it returns a failing `AssertionResult` rather than raising, so one bad assertion never
   aborts the run.
7. **Write the report.** `common/report/` renders the `RunResult` list into `manifest.json`, JUnit
   XML, Common Test Report Format (CTRF) JSON, and a self-contained HTML page. The manifest is the single source of truth the
   other three derive from.

### 6.1 One step, from the inside

Each step follows the same three beats: act, then wait, then verify. The orchestrator captures
evidence around the beats and guards the screen against interruptions between them.

![Flowchart of one step. The loop interpolates the step's variable references, captures a pre-step screenshot and element tree, then branches on the step kind. A wait step polls its condition; an assert step polls its assertions; every other kind dispatches to a registered one-shot action handler. Any of the three can fail. On failure the alert guard checks for a blocking system dialog, and if it clears one the step retries once. Success or exhausted retry both lead to capturing post-step evidence, recording a StepOutcome, and moving to the next step. A failure stops the scenario at the first failing step.](assets/diagrams/developer-guide-step-loop.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-step-loop.svg -->
```mermaid
flowchart TB
    start(["next step"]) --> interp["interpolate ${params}, ${vars}, ${secrets}"]
    interp --> pre["capture pre-step screenshot + element tree"]
    pre --> kind{"step kind"}

    kind -->|"wait"| w["poll the condition until it holds<br/>no fixed sleep, ever"]
    kind -->|"assert"| a["poll the assertions"]
    kind -->|"action"| act["_do_action dispatches to<br/>the registered handler"]

    w --> ok{"passed?"}
    a --> ok
    act --> ok

    ok -->|"yes"| post["capture post-step evidence"]
    ok -->|"no"| guard{"alert guard<br/>cleared a blocker?"}
    guard -->|"yes, retry once"| kind
    guard -->|"no"| fail["record the failure"]

    post --> outcome["record StepOutcome"]
    fail --> stop(["stop the scenario"])
    outcome --> start

    classDef det fill:#bfdbfe,stroke:#2563eb,color:#1f2937;
    class w,a,act,post det
```

</details>

Two rules of the loop follow directly from prime directive 2, determinism first. A wait never sleeps
for a fixed duration; it polls a condition until the condition holds or the budget expires. An
ambiguous selector fails immediately rather than acting on whichever element matched first.

---

## 7. The classes, layer by layer

### 7.1 The scenario model

[`common/scenario/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/scenario) holds
the authoring schema in `models/` and the pipeline that turns text into runnable objects around it.

| File | Classes and functions | Job |
|---|---|---|
| `models/scenario.py` | `Scenario`, `Component`, `ScenarioFile`, `Preconditions`, `SystemAlertHandling` | The top-level document and the per-scenario setup. |
| `models/steps.py` | `Step`, `If`, `ForEach`, `Use`, `Web`, `Interrupt`, `AfterRule`, `Extract` | One step, plus the control-flow and lifecycle wrappers. |
| `models/assertions.py` | `Assertion`, `Exists`, `TextMatch`, `CountMatch`, `RequestMatch`, `VisualMatch`, `GoldenMatch` | The 14 assertion kinds. |
| `models/actions.py` | `TypeText`, `Swipe`, `Scroll`, `HttpRequest`, `Generate`, `Email`, `Totp`, and more | One model per action's arguments. |
| `models/selector.py` | `Selector` | The authoring selector, converted to the runtime one by `as_selector()`. |
| `load.py` · `load_expanded.py` | `load_scenario_file`, `load_scenarios` | Parse YAML, check the schema version. |
| `expand.py` | `expand_components`, `expand_data`, `apply_setups` | Resolve `use:` macros and data rows before the run. |
| `interp.py` | `interpolate` | Substitute `${params.x}`, `${row.x}`, `${secrets.x}`, and `${vars.x}`. |
| `select.py` · `edit.py` · `serialize.py` | filtering, programmatic edits, YAML output | Support `--only`, triage's fixes, and the authoring paths. |

The split between compile time and run time matters. `expand.py` runs once, before any device is
touched, so the step list the orchestrator sees holds no macros. The run loop therefore never needs
to resolve a component mid-scenario.

### 7.2 The driver layer

[`common/drivers/base.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/drivers/base.py)
is the determinism core, and it is the one file worth reading in full. Beyond the `Driver` protocol
it holds the resolution functions every backend shares.

![Class diagram of the driver layer. The Driver protocol declares query, tap, type_text, swipe, wait_for, screenshot, and capabilities. XcuitestDriver, AdbDriver, PlaywrightDriver, XcuitestLiveDriver, and FakeDriver all satisfy it. AdbDriver additionally inherits the shared CoordinateTreeDriver base, which supplies retry, settle, and resolve behavior for coordinate backends. Alongside the main protocol sit narrow optional protocols such as EvidenceProvider and ViewportProvider, each satisfied structurally by the backends that support the behavior. BackendLifecycle is drawn separately: it is a typing umbrella over five lifecycle hooks split disjointly across backends, reached through an explicit cast rather than an isinstance check, so PlaywrightDriver and XcuitestDriver each implement only their own subset of its hooks.](assets/diagrams/developer-guide-driver-classes.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-driver-classes.svg -->
```mermaid
classDiagram
    class Driver {
        <<Protocol>>
        +query() list~Element~
        +tap(sel)
        +type_text(text)
        +swipe(frm, to)
        +wait_for(sel) bool
        +screenshot(path)
        +capabilities() set~str~
    }
    class CoordinateTreeDriver {
        <<shared base>>
        +transient-empty retry
        +stable-key settle
        +_resolve(sel)
    }
    class EvidenceProvider {
        <<Protocol>>
        +network_collector(mocks)
    }
    class ViewportProvider {
        <<Protocol>>
        +viewport() Point
    }
    class BackendLifecycle {
        <<Protocol>>
        +navigate()
        +close()
        +await_ready(timeout)
    }
    class XcuitestDriver {
        iOS Simulator
        resident on-device runner
    }
    class AdbDriver {
        Android
        resident UI Automator server
    }
    class PlaywrightDriver {
        web browser
    }
    class XcuitestLiveDriver {
        device cloud
        W3C WebDriver
    }
    class FakeDriver {
        in-memory, no device
    }

    Driver <|.. XcuitestDriver
    Driver <|.. AdbDriver
    Driver <|.. PlaywrightDriver
    Driver <|.. XcuitestLiveDriver
    Driver <|.. FakeDriver
    CoordinateTreeDriver <|-- AdbDriver
    PlaywrightDriver ..|> EvidenceProvider
    XcuitestDriver ..|> ViewportProvider
    AdbDriver ..|> ViewportProvider
    BackendLifecycle ..> PlaywrightDriver : cast(), 3 of 5 hooks
    BackendLifecycle ..> XcuitestDriver : cast(), 2 of 5 hooks
```

</details>

**`resolve_unique(elements, sel)`** carries prime directive 2 on its own. It narrows one `query()`
snapshot to exactly one element. Nothing matched raises `ElementNotFound`; two content-distinct
matches raise `AmbiguousSelector`. The function never picks a winner by position *on its own*,
because acting on whichever element matched first is the flakiness the whole design exists to
prevent; only an author's explicit `index` selects the nth of several content-distinct candidates,
the documented last resort ([selectors](selectors.md)). Two refinements soften the rule without
weakening it: candidates reporting identical content collapse to one, since nothing distinguishes
them for an author to disambiguate on, and a generic `other`-trait wrapper drops out when a
classified sibling shares its label.

**Narrow optional protocols** carry the capability differences between platforms. Rather than one
fat interface every backend must stub out, `base.py` declares small protocols — `EvidenceProvider`,
`ViewportProvider`, `ReadLagProvider`, `ReadOrderProvider`, `SettledReadProvider`,
`RawSourceProvider`, `SettledCacheInvalidator` — and a caller checks membership at runtime. A
backend implements a narrow protocol purely when its platform genuinely supports the behavior.
`BackendLifecycle` is the deliberate exception: the concrete drivers own disjoint subsets of its
five hooks (`PlaywrightDriver` implements `navigate`/`close`/`reset_context`; `XcuitestDriver`
implements `await_ready`/`health_ready`), so none of them satisfies a structural `isinstance`. The
`platform_lifecycle` environments reach each hook through `cast(BackendLifecycle, driver)` instead
— a typing umbrella over the call sites, not a conformance target.

**`FakeDriver`** deserves its own note. The in-memory backend lets the entire orchestrator run
without a device, which is why the deterministic gate runs on Linux in seconds and needs no
Simulator.

### 7.3 The environment layer

[`common/platform_lifecycle/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/platform_lifecycle)
answers a question the driver deliberately does not: how does the app get onto a device and become
ready? `protocols.py` declares `RunEnvironment` and `CrawlEnvironment`, and `environments/` supplies
one implementation per platform — `ios`, `xcuitest`, `xcuitest_live`, `android`, `web`, and `fake`.

The protocol covers bring-up (`start`), readiness, relaunch, device control, teardown, and the
per-platform answers to questions the runner needs — whether the platform records video up front,
whether it observes network traffic through the driver, and whether its resident runner survives
between scenarios. Because the answers live behind the protocol, `runner/pool.py` and `crawl/cli.py`
drive iOS, Android, and web through one interface rather than branching on the actuator name.

### 7.4 The runner

[`common/runner/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/runner) turns a
config plus a scenario list into a report.

| File | Key classes | Job |
|---|---|---|
| `pipeline.py` | `_ScenarioRunner`, `run_all`, `run_and_report`, `run_matrix_and_report` | One run's shared context, applied to each scenario in turn. |
| `pool.py` | `device_pool` | Bound concurrency, hand out leases, tear down cleanly. |
| `types.py` | `Lease` | A live driver bundled with its sink, relaunch, control, and collector. |
| `device_provider.py` | `DeviceProvider`, `DeviceLease`, `_LocalProvider`, `_AppiumProvider` | Where the run's devices come from: local, or a reserved cloud device. |
| `recovery.py` | `RetryDecision`, `CrashRecoveryBudget`, `RunCrashRecoveryBudget` | Whether a crashed backend earns a retry, and how much wall-clock time recovery may spend. |
| `launch.py` · `launch_server.py` | `launch_driver` | Build the driver and bring the app up. |
| `mailbox.py` | transport registry | Resolve the `email` step's transport by kind. |

**`_ScenarioRunner`** is a frozen dataclass holding one run's shared context: the resolved config,
the lease factory, the redactor, the capability set, and the output knobs. Freezing it, and keeping
every per-scenario value local to `run_one`, is what lets two or more `ThreadPoolExecutor` workers share
one instance safely when `workers > 1`.

**Crash recovery sits beside the verdict, never inside it.** A backend crash is infrastructure, not
a test result, so `recovery.py` retries the scenario on a freshly leased device up to a bounded count
and a bounded wall-clock budget. Once the budget runs out the scenario fails loudly, which keeps a
genuinely crash-inducing scenario visible.

### 7.5 The orchestrator

[`common/orchestrator/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/orchestrator)
holds the step loop. `loop.py` is the largest file in the core, and its structure repays a moment.

| Class or function | Role |
|---|---|
| `run_scenario()` | The entry point. Starts the requested evidence intervals, runs the `before`, main, and `after` phases, evaluates the trailing `expect`, and assembles the `RunResult`. |
| `StepLoopState` | The mutable state one phase carries: the accumulated variable bindings, the previous step's element tree, and the read counters. |
| `_LoopConfig` | The frozen half: the driver, sink, clock, alert guard, and scenario. |
| `_StepRunner` | The dispatcher. `exec_steps` walks the list; `_handle_if`, `_handle_for_each`, `_handle_web`, and `_handle_action` handle each step shape. |
| `_run_step_body()` | One step's effect, returning `(ok, reason, assertion_results, snapshot)`. Never raises for an expected failure. |
| `_InterruptGuard` | Fires a scenario's `interrupts` handlers when an interstitial screen appears mid-run. |
| `_ScreenRead` | A lazily cached element-tree read, so one step never queries the same screen twice. |

`types.py` holds the loop's vocabulary: `StepOutcome`, `RunResult`, `AlertEvent`, `AlertGuardConfig`,
`SelectionState`, and the `Clock` protocol that lets tests run without real sleeps. `waits.py` holds
the polling machinery and `WaitTrace`, which records a wait's poll timeline so a timeout stays
diagnosable from the artifacts alone.

**Action dispatch is a registry, not a conditional chain.**
[`actions/_registry.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/orchestrator/actions/_registry.py)
derives the runtime action list from the `Step` model itself, so declaring a field on `Step` makes
the action visible automatically. Handlers live in `actions/handlers/` grouped by theme — `gestures`,
`scroll`, `device`, `navigation`, `http`, `generate`, `totp`, and `manual` — and register themselves
with an `@_handler(kind)` decorator. The dispatcher also owns the text-selection contract in one
place: `copy` requires an active selection, `select` establishes one, and every other action
invalidates it, which keeps the handlers stateless across backends.

### 7.6 Assertion evaluation

[`common/assertions/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/assertions)
holds the verdict, and holds nothing else. `evaluate.py` keeps one evaluator per kind behind an
`@_evaluator("kind")` decorator, mirroring the action registry. `network.py` matches `request`,
`event`, and `requestSequence` against observed traffic; `visual.py` compares images; `schema.py`
validates a response body; `_common.py` defines the shared `AssertionResult`.

Two properties matter more than the individual evaluators. **Evaluation is total** — it returns a
failing result rather than raising, so a malformed assertion fails its own check without aborting the
run. And **`EvalContext` bundles the ambient inputs** an evaluator may need: the baseline directory
for a visual comparison, the schema directory, the golden directory, and the clipboard. A step-level
`assert` deliberately receives a narrowed context, since no fresh screenshot exists mid-step for a
visual comparison to read.

### 7.7 Evidence

[`common/evidence/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/evidence)
answers what a run leaves behind.

- **`core.py`** declares the `EvidenceSink` protocol with two implementations. `NullSink` records
  nothing and costs nothing, and the run loop checks for it before paying for an extra query.
  `FileSink` writes artifacts under the run directory.
- **`intervals.py`** runs the scenario-wide recordings — video and the device log — as child
  processes, started before the first step and finalized after verification. Every interval kind is
  opt-in, so a scenario requesting none records none.
- **`network.py`** holds `NetworkExchange`, the observed-request model both the in-app collectors and
  the Playwright hook produce, plus the deterministic in-protocol mocks.
- **`redaction.py`** holds `Redactor`, which masks secret values, configured labels, headers, and
  fields before anything reaches disk.
- **`visual.py`** and **`golden.py`** compare a screenshot against a baseline image and an element
  tree against a recorded tree.

Evidence capture is expressed as a rule that fires repeatedly, not as a one-shot instruction. A
scenario's `capturePolicy` names triggers, and the loop fires the matching rules at every step, so a
second run collects the same evidence with no AI involved.

### 7.8 Reporting

[`common/report/`](https://github.com/bajutsu-e2e/bajutsu/tree/main/bajutsu/common/report) renders
the `RunResult` list four ways. `manifest.py` writes `manifest.json` and the JUnit XML; `ctrf.py`
writes the CTRF JSON; `html.py`, `rows.py`, `panels.py`, and `richtext.py` build the interactive HTML
page; `archive.py` and `load.py` export a finished run as a `.zip` and reload it offline for
re-rendering. `manifest.json` is the single source of truth, and CI reads it rather than parsing the
HTML.

---

## 8. The Tier 1 paths: authoring and investigation

Three commands reach a model, and each keeps the model behind a narrow protocol so the deterministic
core never sees it.

![Class diagram of the Tier 1 paths. The Agent protocol declares next_action, taking an Observation and returning a Proposal, and plan, taking a goal. ClaudeAgent implements it on top of ClaudeBackedAgent, which in turn talks to the AiBackend protocol. AiBackend has three adapters: AnthropicBackend for the API and Bedrock, ClaudeCodeBackend for the Claude Code CLI, and a disabled backend whose factory raises. The record loop drives the Agent. The crawl engine drives an ActionProposer, implemented deterministically or by ClaudeActionProposer. The triage command drives a TriageAgent, implemented by the rule-based HeuristicTriageAgent or by ClaudeTriageAgent, and both produce a Triage verdict holding a summary, a category, plain-text suggestions, and at most one structured Fix.](assets/diagrams/developer-guide-tier1.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-tier1.svg -->
```mermaid
classDiagram
    class Agent {
        <<Protocol>>
        +next_action(Observation) Proposal
        +plan(goal) list~str~
    }
    class Observation {
        +list~Element~ elements
        +bytes screenshot
        +str goal
    }
    class Proposal {
        +Step step
        +str reasoning
        +bool done
    }
    class AiBackend {
        <<Protocol>>
        +create_message(MessageRequest) MessageResponse
    }
    class ActionProposer {
        <<Protocol>>
        +propose(elements, screenshot, candidates) Proposal
    }
    class TriageAgent {
        <<Protocol>>
        +triage(TriageContext) Triage
    }
    class Triage {
        +str summary
        +str category
        +list~str~ suggestions
        +Fix fix
    }

    Agent <|.. ClaudeAgent
    ClaudeBackedAgent <|-- ClaudeAgent
    ClaudeBackedAgent ..> AiBackend
    AiBackend <|.. AnthropicBackend
    AiBackend <|.. ClaudeCodeBackend
    ActionProposer <|.. ClaudeActionProposer
    TriageAgent <|.. HeuristicTriageAgent
    TriageAgent <|.. ClaudeTriageAgent
    TriageAgent ..> Triage
    Agent ..> Observation
    Agent ..> Proposal
```

</details>

**`record/`** authors a scenario from a natural-language goal.
[`loop.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/record/loop.py) runs observe →
propose → execute → emit: it reads the screen into an `Observation`, asks the `Agent` for a
`Proposal`, executes the proposed `Step` through the same `_do_action` dispatcher the deterministic
loop uses, and appends the step to the growing scenario. Reusing the dispatcher is what makes a
recorded step replayable. `capture.py` covers the proxy-actuation path, where a human drives the app
and the recorder captures the resulting steps.

**`crawl/`** explores an app breadth-first and emits a screen map.
[`core.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/crawl/core.py) holds the graph
types — `Fingerprint`, `Node`, `Edge`, `Action`, `ScreenMap` — and the `_Coordinator` that walks
them. A screen's `Fingerprint` decides whether the crawl has been there before, so the walk
terminates. `guide.py` declares `ActionProposer` with a deterministic implementation beside the
model-backed `ClaudeActionProposer`, which keeps a crawl runnable with no credentials at all.

**`triage/`** investigates a failed run.
[`heuristic.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/triage/heuristic.py)
assembles a `TriageContext` from the run directory, and `HeuristicTriageAgent` derives a `Triage`
from rules alone. Each `Fix` carries structure rather than prose — `renameId`, `addIndex`, `raiseTimeout`
— so `--apply` can rewrite the scenario and `--rerun` can check the rewrite. `triage --ai` swaps in
`ClaudeTriageAgent` behind the same protocol. Triage is advisory in both forms: it proposes an edit
and never changes a verdict.

**`common/ai/`** keeps the vendor behind one seam. `AiBackend` normalizes the request and response
types, and `registry.py` maps a provider name onto an adapter: the Anthropic API and Amazon Bedrock
through `anthropic.py`, the Claude Code command-line interface through `claude_code.py`, and a
`disabled` provider whose factory raises so no AI path can construct a backend by accident.

---

## 9. codegen, serve, mcp, and analysis

**`codegen/`** turns a passing scenario into a native test.
[`common.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/codegen/common.py) declares
the `CodeGenerator` protocol — `file_preamble`, `scenario_open`, `step_lines`, `assertion_lines`,
`scenario_close`, `file_footer` — and `render_test_file` walks the scenario once, calling the
protocol. Three emitters implement it: `xcuitest.py` for Swift, `playwright.py` for TypeScript, and
`uiautomator.py` for Kotlin. A step whose behavior exists purely at run time raises rather than
emitting wrong code without a word.

**`serve/`** is the browser application. `state.py` holds `ServeState`, the process-wide state
bundling the job registry, the session manager, the scenario store, and the provider settings.
`routes.py` declares the routes as data, so both the standard-library handler (`handler.py`) and the
FastAPI application (`server/app.py`) serve the same route table. `executor.py` declares the
`RunExecutor` seam where local and hosted deployment diverge: `LocalExecutor` runs each job on a
daemon thread, while `DbQueueExecutor` enqueues it for a remote `bajutsu worker`. The execution body
in `jobs.py` stays identical on both sides.

**`mcp/`** exposes `run` and `doctor` as Model Context Protocol (MCP) tools and a run's evidence as
MCP resources, so an editor-side agent reaches the deterministic paths without a shell.

**`analysis/`** holds the read-only advisory reports, none of which gates CI: `audit` (a determinism
and flakiness audit), `coverage` (identifier-namespace coverage), `impact` (which steps a diff
affects), `stats` (run statistics in aggregate), `flakiness` (cross-run ranking), and `trace` (one run's
timeline).

---

## 10. Adding or changing a feature: the development and verification routine

### 10.1 Where to start, by kind of change

The map below turns the structure above into a starting point for the most common changes.

| Goal | Start here | Then |
|---|---|---|
| Support a new platform | `common/drivers/` — a new class satisfying `Driver` | Add a `RunEnvironment` under `platform_lifecycle/environments/`, register the actuator in `backends.py`, and run the driver conformance suite. |
| Add a step action | `common/scenario/models/steps.py` — a field on `Step` | Add a handler in `orchestrator/actions/handlers/`, then an emitter arm in each `codegen/` generator. |
| Add an assertion kind | `common/scenario/models/assertions.py` | Add an evaluator in `assertions/evaluate.py` behind `@_evaluator`. |
| Add a config knob | `common/config/schema.py`, then `effective.py` | Read it wherever the resolved `Effective` already reaches. |
| Add a CLI command | A `cli.py` beside the feature it belongs to | Register it in `cli/__init__.py`'s module list and classify it in `capability/capabilities.py`. |
| Add an evidence kind | `common/evidence/core.py` | Extend the `capturePolicy` schema and the report renderer. |

Two rules hold across every row. A change on the verdict path must not reach an AI package, and the
gate checks the rule mechanically. A change that adds behavior needs a test that would fail without
it, since the deterministic suite is the regression net.

### 10.2 The development routine

![Flowchart of the development routine. A branch or worktree leads to implementing the change inside one layer, then adding or updating a test, then the fast checks (format, lint, typecheck, test). A gate decision asks whether make check passes; no loops back to implementing, yes proceeds to verifying beyond the suite (the conformance suite, an on-device pass, or make docs), then make preflight rebases onto main and re-runs the gate, then a push whose pre-push hook re-runs make check and refuses a red one, then opening the pull request, then CI re-running the same gate. A dotted warning line from push notes that git push --no-verify is forbidden without exception. A red CI result loops back to implementing.](assets/diagrams/developer-guide-routine.svg)

<details>
<summary>Mermaid source</summary>

<!-- mermaid-svg: assets/diagrams/developer-guide-routine.svg -->
```mermaid
flowchart LR
    branch(["Branch or worktree<br/>claude/&lt;topic&gt;"])
    implement["Implement the change<br/>inside one layer"]
    test["Add or update a test"]
    fast["Fast checks<br/>format · lint · typecheck · test"]
    gate{"make check<br/>green?"}
    verify["Verify beyond the suite<br/>conformance suite · on-device · make docs"]
    preflight["make preflight<br/>fetch + rebase + the gate"]
    push["Push<br/>pre-push hook re-runs make check"]
    noverify(["git push --no-verify<br/>forbidden, no exceptions"])
    pr["Open the pull request"]
    ci{"CI"}
    done(["Merged"])

    branch --> implement --> test --> fast --> gate
    gate -->|no| implement
    gate -->|yes| verify --> preflight --> push --> pr --> ci
    push -.->|never| noverify
    ci -->|red| implement
    ci -->|green + reviewed| done

    classDef det fill:#bfdbfe,stroke:#2563eb,color:#1f2937;
    classDef warn fill:#fecaca,stroke:#dc2626,color:#1f2937;
    class gate,ci det
    class noverify warn
```

</details>

1. **Isolate the session.** Branch off `main` as `claude/<topic>` (a human contributor:
   `<user>/<topic>`); `make worktree TOPIC=<topic>` builds the branch and its worktree in one
   step, so two sessions never share a checkout. Claude Code keeps its own worktrees under
   `.claude/worktrees/`.
2. **Implement the change**, staying inside the layer section 5's table points to — a change that
   reaches across the boundary (the deterministic core importing the periphery, say) fails
   `make lint-imports` immediately rather than merging unnoticed.
3. **Add or update a test alongside it.** The deterministic suite (`tests/`) is the regression net;
   a change with no test that would fail without it is not yet done.
   [`tests/driver_conformance.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/tests/driver_conformance.py)
   is the one spec every backend's tests share — a technology compatibility kit (TCK) for the
   `Driver` protocol — so a new backend implements `ConformanceHarness` once and inherits the whole
   suite rather than writing its own.
4. **Run the fast checks while iterating**, rather than the full gate on every save: `make format`,
   `make lint`, `make typecheck`, and `make test` each check one thing and finish quickly.
   `make check` runs all of them plus the slower structural checks (import layering, docstrings,
   the roadmap, secret scanning) in the same order CI does — "green locally" is meant to predict
   "green in CI".
5. **Verify manually beyond the test suite** for anything that touches a driver, a backend, or a
   diagram — section 10.3 below says which command, by what changed.
6. **Rebase before pushing.** `make preflight` fetches `origin/main`, rebases onto it, runs the
   gate, and prints a "definition of done" reminder — the do-it-early version of what the pre-push
   hook enforces anyway, so a conflict or a red check surfaces before it costs a round trip.
7. **Push, then open a pull request (PR).** The tracked pre-push hook runs `make check` before
   every push and refuses a red one; `git push --no-verify` is forbidden, without exception, since
   it only moves the same red result onto the shared PR instead of catching it locally. Whether the
   session or a human opens the PR, and whether it opens Draft or Ready for review, depends on the
   kind of work — see [`CLAUDE.md`](../CLAUDE.md)'s "Who opens the PR depends on the work" and "PRs
   created by Claude Code always start as Draft". The full parallel-development picture (rebase
   discipline, git defenses, worktree isolation) is [AI development](ai-development.md).

### 10.3 Verification, by what you touched

| What you touched | Fast check | Fuller verification |
|---|---|---|
| The scenario schema, a step action, or an assertion | `make test` (`FakeDriver`-backed unit tests, no device needed) | `tests/orchestrator/` and `tests/scenario/` cover the step loop and the model; add a scenario under `scenarios/` for an end-to-end example if the change needs one. |
| A driver or a backend | `uv run pytest tests/test_driver_conformance.py` — the shared conformance contract, run against `FakeDriver` on the fast gate | An on-device pass: `make -C demos/showcase run-swiftui` (needs `make deps` first, macOS and a Simulator) for iOS; `uv run bajutsu run --backend web --target web --config demos/web/demo.config.yaml` for the web track, which needs no Mac. |
| Config schema or resolution | `make test` | `uv run bajutsu doctor --target <name> --config <path>` against a real config, to see the resolved `Effective` the change produces. |
| A CLI command | `make test` | Run the command by hand against a fixture config; `capability/capabilities.py`'s own test asserts every registered command is classified. |
| Documentation, including a diagram | `make lint-roadmap`, textlint (`tools/textlint/`) | `make docs` (`mkdocs build --strict`); a changed mermaid fence needs `make docs-diagrams` to re-render its checked-in SVG, the convention this page's own diagrams follow. |

Coverage ratchets alongside the change, not after it: `make lint-pr` flags when measured coverage
drifts more than two points above the total floor, and `make coverage-floors` raises the per-file
snapshot (`coverage-floors.json`) once a file's coverage has genuinely risen — the deliberate,
reviewable way to record a gain, never a way to paper over a drop.

---

## 11. Further reading

- [Architecture](architecture.md) — the module table, the dependency layers, and the implementation
  status of everything the design describes.
- [Run loop](run-loop.md) — the deterministic runner's semantics in depth.
- [Drivers](drivers.md) — each backend's platform seam and capability set.
- [Selectors](selectors.md) — deterministic resolution, the determinism core in detail.
- [Scenarios](scenarios.md) and [DSL grammar](dsl-grammar.md) — the authoring reference and the
  normative grammar.
- [API reference](api/index.md) — generated from the docstrings and typed signatures.
- [`DESIGN.md`](https://github.com/bajutsu-e2e/bajutsu/blob/main/DESIGN.md) — the design rationale,
  in Japanese.
