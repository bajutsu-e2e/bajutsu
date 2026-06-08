**English** · [日本語](ja/scenarios.md)

# Scenario specification (authoring reference)

> A scenario is Bajutsu's **only persisted artifact**. It is plain YAML — version-controlled
> in git, reviewable in a PR. `record` (AI) writes it the first time only; from then on humans
> own and edit it. `run` executes this structure **without AI**.
>
> Implementation: `bajutsu/scenario.py` (pydantic, `extra="forbid"` rejects unknown keys).

Related: [selectors](selectors.md) (how selectors and assertions evaluate) · [evidence](evidence.md) · [run-loop](run-loop.md) (execution)

---

## File shape

One file = **a list of scenarios**. `load_scenarios()` rejects a top level that is not a list.

```yaml
- name: ...        # scenario 1
  steps: [...]
- name: ...        # scenario 2
  steps: [...]
```

## Top-level structure (`Scenario`)

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | str | required | Scenario name (used for the report / JUnit testcase / codegen method name) |
| `preconditions` | object | `{}` | Per-test environment setup (below) |
| `steps` | list | required | The ordered actions (below) |
| `expect` | list | `[]` | Final assertions after all steps pass ([selectors](selectors.md#assertion-evaluation)) |
| `capturePolicy` | list | `[]` | Repeatedly-firing evidence rules ([evidence](evidence.md#a-capturepolicy-rule-based)) |
| `networkSteps` | object | none | `{ domains: [...] }` — which observed requests to interleave into the report's Steps timeline (by URL host; a parent domain matches subdomains). Unset shows all; the Network tab always lists them all ([reporting](reporting.md#reporthtml)) |
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
| `locale` | str | none | (**not wired**: value is held but not applied at launch) | ⚠️ |
| `setup` | str | none | A reusable prelude scenario (**not wired**: schema only) | ⚠️ |

> **launchEnv resolution order** is **config's `launchEnv` < preconditions' `launchEnv`** (the
> one closer to the test wins). `launch_driver` merges `{**eff.launch_env, **pre.launch_env}`.

## Step grammar (`steps`)

Each step is **exactly one action** + optional modifiers (`capture:` / `name:`). Two or more
actions in one step is a validation error (`scenario.py` `_one_action`).

| Action | Form | Description |
|---|---|---|
| `tap` | `tap: <Selector>` | requires unique resolution (fails if ambiguous) |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | long press |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | with `into`, focuses first |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` or `swipe: { from: [x,y], to: [x,y] }` | selector form and coordinate form cannot mix |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | condition wait (below) |
| `assert` | `assert: [ <Assertion>... ]` | mid-step verification |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | **not implemented** (`NotImplementedError`) |

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
matches (same matcher as the `request` assertion: `method` / `url` / `urlMatches` / `path` /
`pathMatches` / `status`, all AND-ed; `count` raises the threshold). The endpoint is pinned by `url`
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

- `exists` writes its selector **inline** (`{ id: ... }` directly). `negate` is optional.
- `value` / `label` take `sel:` + **exactly one** of `equals` / `contains` / `matches`.
- `count` takes `sel:` + **exactly one** of `equals` / `atLeast` / `atMost`.
- `enabled` / `disabled` / `selected` take a selector inline.

> **Locale caveat**: string comparisons on `label`/`value` and assertions that look at visible
> text break under translation. Write these against config's fixed locale, and write the selector
> itself by `id`.

## capture token grammar

Shared by `capture:` (per-step) and `capturePolicy[].capture` (rules). The form is
`<kind>[.<modifier>]`.

- **Kinds**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **Modifiers**: `before` / `after` / `around` / `onError`

Validation is over the set of kinds and modifiers (`scenario.py` `_validate_capture`). The
acquisition timing per kind, and which are actually captured today (`network`/`appTrace` are not
implemented), are in [evidence](evidence.md#evidence-kinds-and-acquisition-timing).

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
