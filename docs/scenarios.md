**English** · [日本語](ja/scenarios.md)

# Scenario specification (authoring reference)

> A scenario is Bajutsu's **only persisted artifact**. It is plain YAML — version-controlled
> in git, reviewable in a PR. `record` (AI) writes it the first time only; from then on humans
> own and edit it. `run` executes this structure **without AI**.
>
> Implementation: `bajutsu/scenario.py` (pydantic, `extra="forbid"` rejects unknown keys).
>
> The **normative grammar** — every production, type, default, and validation rule — lives in
> [dsl-grammar](dsl-grammar.md). This page is the authoring guide: how to write a scenario, by example.

Related: [dsl-grammar](dsl-grammar.md) (formal grammar) · [selectors](selectors.md) (how selectors and assertions evaluate) · [evidence](evidence.md) · [run-loop](run-loop.md) (execution)

---

## File shape

One file = **a list of scenarios**, or a `{ description, scenarios }` mapping when you want a
file-level description. `load_scenarios()` accepts either form; a top level that is neither is
rejected.

```yaml
- name: ...        # scenario 1
  steps: [...]
- name: ...        # scenario 2
  steps: [...]
```

With a file-level description (and an optional per-scenario `description`):

```yaml
description: What this file covers.
scenarios:
  - name: ...
    description: What this scenario checks.
    steps: [...]
```

Both the file description and each scenario's `description` appear in `report.html` (the
summary header and each scenario card) and in the `bajutsu serve` UI.

## Top-level structure (`Scenario`)

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | str | required | Scenario name (used for the report / JUnit testcase / codegen method name) |
| `description` | str | none | Optional human description; shown on the scenario's report card and in the serve UI |
| `tags` | list[str] | `[]` | Selection labels; the CLI `--tag` / `--exclude` flags pick which scenarios run ([reuse, data, and tags](#reuse-data-and-tags)) |
| `data` / `dataFile` | list / str | none | Data-driven rows — inline `data`, or `dataFile` (a CSV path). Expands into one run per row, substituting `${row.col}`. Mutually exclusive ([reuse, data, and tags](#reuse-data-and-tags)) |
| `preconditions` | object | `{}` | Per-test environment setup (below) |
| `steps` | list | required | The ordered actions (below) |
| `expect` | list | `[]` | Final assertions after all steps pass ([selectors](selectors.md#assertion-evaluation)) |
| `capturePolicy` | list | `[]` | Repeatedly-firing evidence rules ([evidence](evidence.md#a-capturepolicy-rule-based)) |
| `network` | object | none | `{ filter: { domains: [...] } }` — `filter.domains` scopes which observed requests are interleaved into the report's Steps timeline (by URL host; a parent domain matches subdomains). Unset shows all; the Network tab always lists them all ([reporting](reporting.md#reporthtml)) |
| `mocks` | list | `[]` | Deterministic network stubs — a matching outgoing request gets a canned response instead of hitting the network ([network mocks](#network-mocks-deterministic-stubs)) |
| `redact` | object | none | Masking applied before evidence is written ([evidence](evidence.md#masking-redact)) |

```yaml
- name: onboard, log in, and increment the counter
  preconditions:
    launchEnv: { SAMPLE_UITEST: "1" }
  steps:
    - tap: { id: onboarding.start }
    - type: { text: "a@b.com", into: { id: auth.email } }
    - type: { text: "pw", into: { id: auth.password } }
    - tap: { id: auth.submit }
    - wait: { for: { id: home.title }, timeout: 5 }
    - tap: { id: counter.increment }
    - tap: { id: counter.increment }
  expect:
    - exists: { id: home.title }
    - value: { sel: { id: counter.value }, equals: "2" }
```

(real file: [`sample/scenarios/smoke.yaml`](../sample/scenarios/smoke.yaml))

## preconditions (environment setup)

Implementation: `scenario.py` `Preconditions`. The runner's `launch_driver` reads this to build
the launch sequence ([run-loop](run-loop.md#runner-the-run-pipeline)).

| Key | Type | Default | Description | Wired |
|---|---|---|---|---|
| `erase` | bool | `true` | `simctl erase` before each test (clean environment) | ✅ |
| `launchArgs` | list[str] | `[]` | Launch arguments (appended to config's `launchArgs`) | ✅ |
| `launchEnv` | dict | `{}` | Launch env (injected via `SIMCTL_CHILD_*`; merged onto config's `launchEnv`) | ✅ |
| `deeplink` | str | none | Opened after launch via `simctl openurl` | ✅ |
| `locale` | str | none | Force the locale/language at launch (`-AppleLocale`/`-AppleLanguages`); overrides the app/config default | ✅ |
| `setup` | str | none | A reusable prelude scenario file (resolved relative to this scenario); its steps run before this scenario's own | ✅ |

> **launchEnv resolution order** is **config's `launchEnv` < preconditions' `launchEnv`** (the
> one closer to the test wins). `launch_driver` merges `{**eff.launch_env, **pre.launch_env}`.

## Selectors (addressing an element)

A selector says **which element** to act on or assert against. Provide one or more fields; multiple
fields are **AND**-ed (all must hold), and at least one is required. How a selector narrows to exactly
one element — and why an *ambiguous* selector fails rather than picking the first match — is in
[selectors](selectors.md); the formal shape is in [dsl-grammar](dsl-grammar.md#2-grammar-at-a-glance).

| Field | Type | Description |
|---|---|---|
| `id` | str | Exact `accessibilityIdentifier` — **first choice** (stable, non-localized) |
| `idMatches` | str | Glob over the id (e.g. `"list.row.*"`; assumes multiple matches) |
| `label` | str | Exact `accessibilityLabel` (visible text) — auxiliary / disambiguation |
| `labelMatches` | str | Regex / substring over the label (`re.search`) |
| `traits` | list[str] | Narrow by accessibility trait (subset test, e.g. `[button]`) |
| `value` | str | Exact accessibility value |
| `within` | Selector | Scope to a container — the match must sit inside an element the nested selector resolves to (nestable) |
| `index` | int | Pick the k-th of multiple matches (negatives allowed) — last resort, order-sensitive |

```yaml
- tap: { id: counter.increment }                               # by id (recommended)
- tap: { label: "Delete" }                                     # by visible label (e.g. an alert button)
- tap: { id: row.action, within: { id: list.row.3 } }          # scoped to a container's subtree
- tap: { labelMatches: "^Item ", traits: [button], index: 0 }  # first matching button, fields AND-ed
```

> Prefer `id`. For a *set* of elements (count / existence) use `idMatches`; reach for `index` only as a
> last resort — it breaks when order changes. Full resolution semantics: [selectors](selectors.md).

## Step grammar (`steps`)

Each step is **exactly one action** + optional modifiers (`capture:` / `name:`). Two or more
actions in one step is a validation error (`scenario.py` `_one_action`).

| Action | Form | Description |
|---|---|---|
| `tap` | `tap: <Selector>` | requires unique resolution (fails if ambiguous) |
| `doubleTap` | `doubleTap: <Selector>` | two quick taps on the resolved element |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | long press |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | with `into`, focuses first |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` or `swipe: { from: [x,y], to: [x,y] }` | selector form and coordinate form cannot mix |
| `pinch` | `pinch: { sel: <Selector>, scale: <num> }` | two-finger magnify; `scale > 0` (`>1` zooms in, `<1` out) |
| `rotate` | `rotate: { sel: <Selector>, radians: <num> }` | two-finger rotation; `>0` is clockwise |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | condition wait (below) |
| `assert` | `assert: [ <Assertion>... ]` | mid-step verification |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | terminate + relaunch the app (re-applying launch env/args, plus the given overrides), then wait until ready |
| `setLocation` | `setLocation: { lat: <num>, lon: <num> }` | override the simulated GPS location (`simctl location set`) |
| `push` | `push: { payload: {...} }` | deliver a simulated push notification (`simctl push`) with this APNs payload |
| `use` | `use: { component: <file>, with?: {...} }` | expand a reusable component's steps — a compile-time macro ([reuse](#reuse-data-and-tags)) |

Modifiers:

- `capture: [<token>...]` — evidence for this step only ([evidence](evidence.md#b-inline-evidence)).
- `name: <str>` — the step id (the evidence output directory name · report label). Defaults to `step<i>`.

### `tap`

```yaml
- tap: { id: counter.increment }      # exact id (recommended)
- tap: { label: "Delete" }            # exact label (for an in-app alert etc. with no id)
```

### `type`

```yaml
- type: { text: "a@b.com", into: { id: auth.email } }   # focus, then type
- type: { text: "hello", submit: true }                 # submit appends a newline / confirm (uses current focus)
```

> Internally, when `into` is given, the target is `tap`ped before `type_text` (`orchestrator.py`
> `_do_action`).

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame center → 100pt in a direction
- swipe: { from: [100, 400], to: [100, 200] }              # raw coordinates (last resort)
```

`{on,direction}` and `{from,to}` must be **exactly one or the other** (mixing or omitting a side
is a validation error).

### `doubleTap` / `pinch` / `rotate` (gestures)

```yaml
- doubleTap: { id: gest.doubletap }                    # two quick taps
- pinch:  { sel: { id: gest.pinch },  scale: 2.0 }     # >1 zooms in, 0<scale<1 zooms out
- rotate: { sel: { id: gest.rotate }, radians: 1.57 }  # >0 clockwise (radians)
```

`scale` must be **> 0** (a validation error otherwise). `pinch` / `rotate` need multi-touch: on the idb
backend they fail with a clear "needs multiTouch" reason — their on-device home is the generated
XCUITest (`pinch(withScale:)` / `rotate(_:)`); `doubleTap` runs on idb (two taps). (real file:
[`sample/scenarios/gestures.yaml`](../sample/scenarios/gestures.yaml))

### `wait` (condition wait)

There is no fixed-sleep grammar. **`timeout` is mandatory** (no infinite waits).

```yaml
- wait: { for: { id: home.title }, timeout: 5 }            # until an element appears
- wait: { until: { gone: { id: home.spinner } }, timeout: 15 }  # until an element disappears
- wait: { until: screenChanged, timeout: 5 }              # until query() changes
- wait: { until: settled, timeout: 3 }                    # until the screen stops changing
- wait: { until: { request: { method: GET, path: /items, status: 200 } }, timeout: 8 }  # until a matching request is observed
```

`for` and `until` are exclusive (only one). `until` is `screenChanged` / `settled` /
`{ gone: <Selector> }` / `{ request: <RequestMatch> }`. The `request` form polls the network
collector ([evidence](evidence.md), the `--network` run flag) until at least one observed exchange
matches (same matcher as the [`request` assertion](#request-network-assertion): `method` / `url` /
`urlMatches` / `path` / `pathMatches` / `status` / `bodyMatches`, all AND-ed; `count` raises the
threshold). The endpoint is pinned by `url`
(exact full URL) or `urlMatches` (regex/substring), or just `path`. Timeout handling differs by kind
([run-loop](run-loop.md#waits-condition-waits-only)): `for` / `gone` / `screenChanged` / `request`
time out = step failure; `settled` is a stabilization hint, so a timeout just proceeds with the
current screen (it does not fail).

### `assert` (mid-step verification)

Verification mid-step. The DSL is the same as `expect` (next section).

```yaml
- assert:
    - disabled: { id: auth.submit }
```

### `setLocation` / `push` (device control)

```yaml
- setLocation: { lat: 35.681, lon: 139.767 }              # simctl location set
- push: { payload: { aps: { alert: "You have mail" } } }  # simctl push (APNs payload)
```

Both drive the Simulator via `simctl` and need a per-device control channel, so they are unavailable on
the fake driver and in parallel runs — there the step fails cleanly (it does not crash). `push` delivers
its `payload` as the APNs JSON to the app under test.

## Assertion DSL

Shared by `expect` (final verification) and `assert` (mid-step). Items in the list are all
**AND**-ed; one failure fails the step. The evaluation mechanics (element resolution, comparison)
are in [selectors](selectors.md#assertion-evaluation).

| Assertion | Meaning | Example |
|---|---|---|
| `exists` | a matching element exists (`negate: true` checks absence) | `exists: { id: home.title }` / `exists: { id: settings.banner, negate: true }` |
| `value` | accessibility value match | `value: { sel: { id: counter.value }, equals: "2" }` |
| `label` | label exact / substring / regex | `label: { sel: { id: settings.status }, contains: "done" }` |
| `count` | number of matching elements | `count: { sel: { idMatches: "list.row.*" }, equals: 5 }` |
| `enabled` / `disabled` | actionable or not (the `notEnabled` trait) | `disabled: { id: auth.submit }` |
| `selected` | selected / toggled state (the `selected` trait) | `selected: { id: tab.home }` |
| `request` | a matching network exchange was observed (needs `--network`) | `request: { method: POST, path: /login, status: 200, count: 1 }` |

- `exists` writes its selector **inline** (`{ id: ... }` directly). `negate` is optional.
- `value` / `label` take `sel:` + **exactly one** of `equals` / `contains` / `matches`.
- `count` takes `sel:` + **exactly one** of `equals` / `atLeast` / `atMost`.
- `enabled` / `disabled` / `selected` take a selector inline.
- `request` matches an **observed network exchange** ([details below](#request-network-assertion)); needs the `--network` run flag.

> **Locale caveat**: string comparisons on `label`/`value` and assertions that look at visible
> text break under translation. Write these against config's fixed locale, and write the selector
> itself by `id`.

### `request` (network assertion)

`request` asserts that the run's network collector **observed a matching HTTP exchange** (needs the
`--network` run flag and BajutsuKit in the app). The same matcher backs the `until: { request: ... }`
wait and `mocks` (below). At least one match field is required; the listed fields are **AND**-ed.

| Field | Type | Description |
|---|---|---|
| `method` | str | HTTP method (`GET`, `POST`, …) |
| `url` | str | Exact full URL (the endpoint) |
| `urlMatches` | str | Regex / substring over the URL (query strings live here) |
| `path` | str | Exact path (query ignored) |
| `pathMatches` | str | Regex over the path |
| `status` | int | Response status code |
| `bodyMatches` | str | Regex / substring over the **request body** |
| `count` | int | Number of matching exchanges — **exact** for the assertion, a **lower bound** for the `wait` |

```yaml
- assert:
    - request: { method: POST, path: /login, status: 200, count: 1 }
    - request: { urlMatches: "/search", bodyMatches: "apple" }   # match on the request body
```

> `count` is **not** a match field — at least one of `method` / `url` / `urlMatches` / `path` /
> `pathMatches` / `status` / `bodyMatches` must be present. (real file:
> [`sample/scenarios/network_mock.yaml`](../sample/scenarios/network_mock.yaml))

## Network mocks (deterministic stubs)

`mocks` makes a test independent of a live server: when an outgoing request matches, BajutsuKit returns
a canned response instead of hitting the network. Each mock is `{ match, respond }`.

- **`match`** reuses the **request-side** fields of the [request matcher](#request-network-assertion)
  (`method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`). `status` / `count` do
  **not** apply to a mock's `match`.
- **`respond`** is the canned reply: `status` (default `200`), `headers` (default `{}`), `body` (a
  string), `delayMs` (artificial latency). Omitting `respond` returns an empty `200`.

```yaml
- name: GET answered by a mock stub
  mocks:
    - match: { method: GET, urlMatches: "example.com" }
      respond:
        status: 418                       # real example.com returns 200; 418 proves the stub served it
        headers: { Content-Type: text/plain }
        body: "stubbed by bajutsu"
  steps:
    - tap:  { id: net.fetch }
    - wait: { until: { request: { method: GET, urlMatches: "example.com", status: 418 } }, timeout: 6 }
  expect:
    - request: { method: GET, urlMatches: "example.com", status: 418 }
```

Mocks are handed to BajutsuKit via the `BAJUTSU_MOCKS` env (`dump_mocks`, `scenario.py:638`). The formal
shape is in [dsl-grammar](dsl-grammar.md#2-grammar-at-a-glance).

## Reuse, data, and tags

A small templating + macro layer wraps the core grammar. It runs **at load time, before the
deterministic run**, so the runner only ever sees plain, fully-expanded scenarios. The normative rules
(expansion order, `${ns.key}` interpolation, depth limits) are in
[dsl-grammar](dsl-grammar.md#6-the-templating--macro-layer); this is the authoring view.

### Components (`use` → reusable steps)

A **component** is a separate file: a list of `params` and a list of `steps` that reference them as
`${params.<name>}`. A `use` step invokes it, binding params via `with`. `use` is a **compile-time macro**
— `expand_components` (`scenario.py:474`) replaces it with the component's substituted steps before the
run (recursive — a component may itself `use` another, depth ≤ 25). It errors on a missing or unknown
param, a residual `${params.*}` referencing something undeclared, or a reference cycle. No `use` survives
into the run, so determinism is unaffected.

```yaml
# login.component.yaml — a component file (a single mapping, loaded separately)
params: [user, pass]
steps:
  - type: { text: "${params.user}", into: { id: auth.user } }
  - type: { text: "${params.pass}", into: { id: auth.pass } }
  - tap:  { id: auth.submit }
```

```yaml
# in a scenario — expands to the three steps above with params substituted
steps:
  - use: { component: login.component.yaml, with: { user: alice, pass: hunter2 } }
  - tap: { id: home.tab }
```

### Data-driven scenarios (`data` / `dataFile`)

A scenario with `data` (inline rows) or `dataFile` (a CSV path — the two are **mutually exclusive**) is
expanded into **one scenario per row**, substituting `${row.<column>}` (`expand_data`, `scenario.py:537`).
Each derived scenario is renamed `"<name> [row N: col=val, …]"` and keeps the original preconditions (so
`erase` still defaults true — every row runs in its own clean environment).

```yaml
- name: search returns a result
  data:
    - { q: dog, expect: "1 result" }
    - { q: cat, expect: "2 results" }
  steps:
    - type: { text: "${row.q}", into: { id: search.field }, submit: true }
  expect:
    - label: { sel: { id: home.status }, equals: "${row.expect}" }
```

> A string that is **exactly one token** (`"${row.qty}"`) takes the **raw** value (a number stays a
> number); a token **embedded** in a larger string is spliced in as text (`"item-${row.id}"`).

A CSV `dataFile` has a header row naming the columns; each subsequent row becomes one scenario.

### Tags and selection

`tags` label a scenario; the CLI `--tag` / `--exclude` flags pick which scenarios run. A scenario is kept
when it carries at least one `--tag` (or none was given) **and** none of the `--exclude` tags —
`--exclude` wins over `--tag` (`select_scenarios`, `scenario.py:560`). Both flags accept a comma list.

```yaml
- name: checkout smoke
  tags: [smoke, checkout]
  steps:
    - tap: { id: cart.checkout }
```

```bash
uv run bajutsu run scenarios.yaml --tag smoke --exclude wip   # run @smoke, skip anything @wip
```

### Secrets (`${secrets.X}`)

Declare secret environment-variable names in config (`secrets: [API_TOKEN, ...]`). Each declared name
`X` is resolved from the environment and substituted into the executed step **at action time** as
`${secrets.X}`. The scenario file keeps the **token**, never the value, and the literal values are
**auto-masked** in evidence — so a secret is safe to commit and review. Unlike `${params.*}` /
`${row.*}` (load-time expansion), this namespace is resolved by the run loop.

```yaml
# config declares: secrets: [API_TOKEN]
steps:
  - type: { text: "${secrets.API_TOKEN}", into: { id: auth.token } }   # real value typed; token kept in the report
```

> `vars.*` (capturing a UI value at runtime) is **not yet implemented** — the `${...}` primitive would
> support it, but the run loop never binds `vars.*`.

## capture token grammar

Shared by `capture:` (per-step) and `capturePolicy[].capture` (rules). The form is
`<kind>[.<modifier>]`.

- **Kinds**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **Modifiers**: `before` / `after` / `around` / `onError`

Validation is over the set of kinds and modifiers (`scenario.py` `_validate_capture`). The
acquisition timing per kind, and which are captured, are in
[evidence](evidence.md#evidence-kinds-and-acquisition-timing).

## YAML caveat

PyYAML (YAML 1.1) resolves `on`/`off`/`yes`/`no` to booleans. To prevent the `capturePolicy`
trigger key `on:` from becoming `True`, Bajutsu's YAML loader (`_yaml.py`) treats **only
`true`/`false` as booleans** and keeps `on`/`off`/`yes`/`no` as strings.

## Round-trip (load ⇄ dump)

- `load_scenarios(text) -> list[Scenario]`: YAML string → validated models.
- `dump_scenarios(scenarios) -> str`: models → YAML (pruning `None` / empty list / empty dict for
  readability).

`record`'s output goes through this `dump_scenarios`. The generated YAML reloads cleanly via
`load_scenarios`.
