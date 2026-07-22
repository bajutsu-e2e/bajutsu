**English** · [日本語](ja/glossary.md)

# Glossary

> A term-by-term reference for the domain vocabulary that runs through the rest of the docs.
> Where [concepts](concepts.md) explains *why* Bajutsu is shaped the way it is, this page
> answers *what a word means* and — for the clusters that are easy to confuse — *which of two
> similar-sounding words is which*. The source of truth is the implementation (`bajutsu/`), not
> how any single page phrases a term; each entry points at the page or module that defines it.

Related: [concepts](concepts.md) (design rationale) · [scenarios](scenarios.md) · [drivers](drivers.md) · [cli](cli.md)

---

## Scenario authoring

**scenario** — One named test: a `name`, optional `preconditions`, an ordered list of `steps`,
and an `expect` list of machine-checkable assertions (plus per-scenario `capturePolicy`,
`network`, `mocks`, and similar). It is Bajutsu's only persisted artifact — plain YAML, reviewed
in a pull request. Defined by `Scenario` in `bajutsu/scenario/models/scenario.py`; the authoring
guide is [scenarios](scenarios.md).

> **scenario vs. scenario file vs. test.** A **scenario file** holds a *list of scenarios*; each
> named entry in that list is itself **a scenario**, never a *test*. "test" is not a Bajutsu term
> — where the docs say "test" they mean one scenario. (`ScenarioFile` wraps the list;
> `load_scenarios()` accepts either a bare list or a `{ description, scenarios }` mapping.)

**goal** — The natural-language objective a human hands to `bajutsu record`; the AI explores the
app toward it and authors a scenario. It is authoring input, not a scenario field — it survives
in the finished scenario only as provenance (see `from`). See [recording](recording.md).

**step** — Exactly one action (tap / type / swipe / wait / …) plus optional modifiers
(`capture` / `extract` / `name` / `from`). The "exactly one action" rule is enforced by the
schema. A **control-flow step** (`if` / `forEach`) is the exception: it carries no capture or
extract modifiers. Defined by `Step` in `bajutsu/scenario/models/steps.py`.

**precondition** — Per-scenario environment setup applied before the run: `erase`, `reinstall`,
`launchArgs`, `launchEnv`, `deeplink`, `locale`, `setup`. Distinct from runtime concerns
(`mocks` / `network`) and from config-level `defaults` / `targets`. Defined by `Preconditions` in
`bajutsu/scenario/models/scenario.py`.

**expect / assertion** — An **assertion** is a single machine-checkable check (one of `exists` /
`value` / `label` / `count` / `enabled` / `disabled` / `selected` / `request` / `event` /
`visual` / …). **`expect`** is the scenario-level list of assertions. This list is the *sole*
source of pass/fail — no large language model (LLM) ever decides a verdict (see [concepts §1](concepts.md#1-ai-is-the-author-and-the-investigator-never-the-judge)).
Assertions also appear inline on a step (`assert:`) and as a wait's `until` condition. Defined by
`Assertion` in `bajutsu/scenario/models/assertions.py`.

**selector / identifier** — A **selector** is *how you address a UI element*: an AND-combined set
of fields (`id`, `idMatches`, `label`, `labelMatches`, `traits`, `value`, `within`, `index`). An
**identifier** (the element's stable `id`) is the primary selector field and the one to prefer.
So the selector is the whole query object; the identifier is one field within it. Resolution is
deterministic — zero matches or two-or-more matches fail rather than guess. See
[selectors](selectors.md); defined by `Selector` in `bajutsu/scenario/models/selector.py`.

**component** — A reusable, parameterized sequence of steps, invoked from a step with `use:` and
`with:`. It is a macro in the scenario DSL (domain-specific language), expanded away at compile time, so it does not affect
determinism — not a UI "component". Defined by `Component` in
`bajutsu/scenario/models/scenario.py`; expanded by `bajutsu/scenario/expand.py`.

**from (provenance)** — The natural-language phrase a construct was recorded from. It is authoring
metadata for display only; `run` never reads it, so it never affects pass/fail. Present on a step,
an assertion, a capture rule, and a scenario (YAML key `from`, model field `from_`).

## The two tiers

**Tier 1** — AI live operation: exploration and authoring (`record`, `crawl`, `triage`, `serve`).
Flexible and non-deterministic; it authors and investigates but never decides pass/fail.

**Tier 2** — The deterministic runner (`bajutsu run`) that gates CI (continuous integration). No AI on this path; the
verdict comes only from the `expect` assertions. Tier 2 is the only pass/fail authority.

The split is the top-level constraint of the project — see
[concepts §1–2](concepts.md#1-ai-is-the-author-and-the-investigator-never-the-judge).

## Driver, backend, actuator, platform

These four name the one platform-specific seam of an otherwise platform-neutral core. They are
easy to blur, so here is the whole relationship in one place. The source of truth is
`bajutsu/backends.py` (`PLATFORMS`, `IMPLEMENTED`), not the prose on any page.

| Term | What it is |
|---|---|
| **driver** | The abstract `Driver` interface (a `Protocol` in `bajutsu/drivers/base.py`) — the single platform-specific seam. Every actuator implements it. |
| **backend** | The user-facing token accepted by `--backend` and config `backend:`. It is *either* a platform alias (`ios`) *or* a bare actuator name (`xcuitest`). "backend" is the umbrella word for the input token; it resolves to an actuator. |
| **actuator** | The concrete engine that actually performs actions (tap / type / swipe / query) — what a driver implements. Selection resolves a backend token to one actuator, and the chosen actuator is fixed once at the start of a run and held for the whole run. |
| **platform** | A coarse token naming a class of target — `ios` / `android` / `web` / `fake` — that expands to an ordered, most-stable-first list of actuators. |

The `backend:` list is written most-stable-first; selection expands each token to its actuators
and picks the first one that is both known and available on this machine. Platform → actuator, as
wired in code today (all four actuators are in `IMPLEMENTED`):

| Platform | Actuator(s), most-stable-first | Availability gate |
|---|---|---|
| `ios` | `xcuitest` | needs `xcodebuild` |
| `android` | `adb` | needs the `adb` executable |
| `web` | `playwright` | needs the `playwright` Python package |
| `fake` | `fake` | always available (in-memory; for tests) |

> **`adb` is implemented, not planned.** The Android actuator (`adb`) is wired and in the
> `IMPLEMENTED` set today, validated end-to-end on an emulator ([architecture → implementation
> status](architecture.md#implementation-status), [vision → reach](vision.md#1-reach--more-platforms-and-surfaces)).
> On iOS, `xcuitest` is the sole actuator (`--backend ios` resolves to it); the earlier `idb`
> backend was retired in BE-0290.

See [drivers](drivers.md) for the interface and per-actuator capability differences.

## target, app, device

These three look interchangeable to a newcomer but name three different things — the distinction
is why BE-0057 renamed the config concept from `app` to `target`.

| Term | What it is |
|---|---|
| **target** | One config entry under `targets.<name>` describing an app to test: its per-platform identifier (iOS `bundleId`, web `baseUrl`, Android `package`), plus `backend`, `device`, `appPath`, and so on. A *config unit*. Defined by `TargetConfig` in `bajutsu/config/schema.py`. |
| **app** | The application under test itself — the software the target points at and that gets installed on a device. |
| **device** | The concrete runtime instance the target is driven on — a Simulator / emulator / browser context, named by `device` (e.g. `iPhone 15`) and addressed at runtime by `udid`. |

So a **target** (config) points at an **app** (software) driven on a **device** (runtime instance).

## Evidence, capturePolicy, trace, triage

**evidence** — Artifacts captured during a run, each tagged with the provider that produced it.
Two shapes: **instant** (screenshot / element hierarchy, captured per step) and **interval**
(video / device log / app trace, captured across a scenario). See [evidence](evidence.md);
defined in `bajutsu/evidence/core.py`.

**capturePolicy / CaptureRule / "rule"** — Three names for one concept, reconciled here:

- **`capturePolicy`** is the scenario's YAML field — a *list*.
- **`CaptureRule`** is the type of each element in that list (`bajutsu/scenario/models/evidence.py`).
- **"rule"** in prose means one `CaptureRule`: capture the artifacts in its `capture` whenever its
  `on` trigger fires. A rule fires *repeatedly*, which is what lets the same evidence reproduce on
  every run with no AI.

Distinct from a step's inline `capture:`, which is a *one-shot* capture at that step.

**trace vs. triage** — One edit apart in spelling, opposite in kind:

- **`trace`** — a CLI (command-line interface) verb that renders a *finished* run as a read-only text timeline (steps + network +
  app trace), or with `--explain` previews how a scenario's `capturePolicy` would fire. It is
  deterministic and observational — no AI, no verdict. Engine in `bajutsu/trace.py`.
- **`triage`** (CLI verb) is *AI diagnosing a failed run* and proposing a minimal fix. It is a
  Tier-1, advisory activity — it never gates CI. Engine in `bajutsu/triage.py`.

> `trace` is overloaded: besides the verb, **`appTrace`** is an interval evidence kind
> (os_signpost / os_log intervals). Both senses are observational; neither decides pass/fail.

## CLI verbs

The commands the rest of the docs lean on. This is the vocabulary, not the full reference — every
command and option is in [cli](cli.md).

| Verb | Tier | What it does |
|---|---|---|
| `record` | 1 | AI explores toward a `goal` and authors a scenario. |
| `crawl` | 1 | AI explores an app breadth-first and draws its screen map. |
| `run` | 2 | Deterministic execution; the only pass/fail authority. |
| `trace` | — | Render a finished run as a read-only timeline (no AI). |
| `triage` | 1 | AI diagnoses a failed run and proposes a fix (advisory). |
| `codegen` | — | Structural mapping of a scenario to native XCUITest / Playwright. |
| `doctor` | — | Score how well a target follows the conventions Bajutsu relies on. |
| `serve` | 1 | Launch the local web UI (record / replay / crawl / stats); Tier 1, not for CI. |
