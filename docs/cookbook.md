**English** · [日本語](ja/cookbook.md)

# Scenario cookbook

> Task-oriented recipes: "I want to do X", answered by a complete, runnable scenario. [scenarios](scenarios.md)
> is the grammar reference (every step kind, wait, and assertion); this page is worked examples.
> Every recipe below is trimmed from a real file the repo's own CI runs — follow the link under
> each one to see it in full context, including the parts trimmed here for focus.

Related: [scenarios](scenarios.md) · [selectors](selectors.md) · [network](network.md) · [Getting started](getting-started/index.md)

Run any of these yourself once you've built the showcase app or served the web demo (see
[Getting started](getting-started/index.md)):

```bash
uv run bajutsu run --scenario <path-to-file> --target showcase-swiftui --backend ios --udid booted --no-erase
```

---

## Navigate and assert a value changed

The simplest useful shape: land on a screen, act on one element, assert the result. This scenario is the
showcase's own guided tour — the same scenario [`demos/tour/demo.sh`](../demos/tour/demo.sh) runs,
then deliberately breaks (an assertion, then a selector) to show a machine assertion catching the
first and `triage` diagnosing the second.

```yaml
- name: favorite a horse
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - wait: { for: { id: stable.row.3 }, timeout: 10 }
    - tap: { id: stable.row.3 }
    - wait: { for: { id: horse.favorite }, timeout: 5 }
    - tap: { id: horse.favorite }
  expect:
    - value: { sel: { id: horse.favorite.value }, equals: "on" }
```

Every `wait` before a `tap` is a **condition wait**, not a fixed sleep — Bajutsu polls until the
target id exists (or times out and fails loudly). Full file:
[`demos/showcase/scenarios/menu/tour.yaml`](../demos/showcase/scenarios/menu/tour.yaml).

## Search and filter a list

Type into a field and assert the result count — plus the companion "no results" case, which is
just as important to cover as the happy path.

```yaml
- name: filter narrows the catalog
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { text: "Horse 3", into: { id: search.field } }
    - wait: { for: { id: search.row.3 }, timeout: 5 }
  expect:
    - count: { sel: { idMatches: "search.row.*" }, equals: 1 }
    - value: { sel: { id: search.count }, equals: "1" }
    - exists: { id: search.results-empty, negate: true }

- name: no match shows the empty state
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { text: "zzz", into: { id: search.field } }
    - wait: { for: { id: search.results-empty }, timeout: 5 }
  expect:
    - exists: { id: search.results-empty }
    - count: { sel: { idMatches: "search.row.*" }, equals: 0 }
```

`idMatches` is a glob against the id — useful for asserting "how many rows" without naming each one.
Full file (including the cross-platform id-candidate-list form):
[`demos/showcase/scenarios/search.yaml`](../demos/showcase/scenarios/search.yaml).

## Grant a system permission dialog

A runtime permission prompt (notifications, location, …) is an **out-of-process system alert**, not
part of the app's own UI — the iOS backend can't tap it directly. `dismissAlerts` hands that one tap to the AI
alert guard, which watches for the prompt and taps "Allow", while every assertion around it stays
machine-checked.

```yaml
- name: grant notification permission
  tags: [permission, system]
  dismissAlerts: { instruction: "tap Allow" }
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Permissions", traits: [button] }
    - wait: { for: { id: perm.requestNotif }, timeout: 10 }
    - assert:
        - value: { sel: { id: perm.notif.value }, equals: "notDetermined" }
    - tap: { id: perm.requestNotif }
    - wait: { for: { id: perm.notif.authorized }, timeout: 10 }
  expect:
    - value: { sel: { id: perm.notif.value }, equals: "authorized" }
```

`dismissAlerts` is an alert **handler**, never an assertion — it never touches the pass/fail
verdict, only unblocks a step that would otherwise hang on an alert the iOS backend can't see into. On Android
the same scenario runs with no prompt at all (the target config pre-grants the permission), so the
guard stays idle — one scenario, two platforms, no branching. Full file with the
platform-parity notes: [`demos/showcase/scenarios/permission.yaml`](../demos/showcase/scenarios/permission.yaml).

## Mock a network response

`mocks` intercepts a request in-protocol and answers it deterministically — no real server, no
flaky network, and `request` assertions confirm the mocked call happened.

```yaml
- name: log submit answered by a mock, toast appears and clears
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  mocks:
    - match: { method: POST, pathMatches: "/post$" }
      respond: { status: 201, body: "{\"ok\":true}" }
  steps:
    - tap: { label: "Log", traits: [button] }
    - wait: { for: { id: log.submit }, timeout: 10 }
    - tap: { id: log.submit }
    - wait: { until: { request: { method: POST, path: /post, status: 201 } }, timeout: 6 }
    - wait: { for: { id: log.toast }, timeout: 4 }
    - wait: { until: { gone: { id: log.toast } }, timeout: 5 }
  expect:
    - request: { method: POST, path: /post, status: 201 }
    - value: { sel: { id: log.status }, equals: "done" }
```

`wait: { until: { gone: … } }` polls until an element *disappears* — useful for a transient toast
like this one. Full file (including the `redact` policy that masks the request's `Authorization`
header and `password` body field in captured evidence):
[`demos/showcase/scenarios/network_mock.yaml`](../demos/showcase/scenarios/network_mock.yaml).

## Run the same scenario over a data table

`data` runs one scenario body once per row, each in its own clean environment, substituting
`${row.*}` tokens — here in both the typed query and the asserted id, so each row proves the filter
found exactly the horse it searched for.

```yaml
- name: search finds the seeded horse
  data:
    - { q: "Horse 1", n: "1" }
    - { q: "Horse 3", n: "3" }
    - { q: "Horse 5", n: "5" }
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { into: { id: search.field }, text: "${row.q}" }
    - wait: { for: { id: "search.row.${row.n}" }, timeout: 5 }
  expect:
    - value: { sel: { id: search.count }, equals: "1" }
    - exists: { id: "search.row.${row.n}" }
```

Full file (with the cross-platform id-candidate-list form of each token):
[`demos/showcase/scenarios/data_driven.yaml`](../demos/showcase/scenarios/data_driven.yaml).

## Reuse a step sequence as a component

A **component** is a parameterized, reusable step sequence — a scenario DSL macro, expanded at load
time (it never shows up as its own step in the run result). Define it once:

```yaml
# _components/search_for.yaml
params: [query]
steps:
  - tap: { label: "Search", traits: [button] }
  - wait: { for: { id: search.field }, timeout: 10 }
  - type: { into: { id: search.field }, text: "${params.query}" }
```

Then call it from any scenario with `use` / `with`:

```yaml
- name: search finds a horse by name
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - use: { component: _components/search_for.yaml, with: { query: "Horse 3" } }
    - wait: { for: { id: search.row.3 }, timeout: 5 }
  expect:
    - value: { sel: { id: search.count }, equals: "1" }
```

Component files: [`demos/showcase/scenarios/menu/_components/search_for.yaml`](../demos/showcase/scenarios/menu/_components/search_for.yaml);
a caller: [`demos/showcase/scenarios/menu/features.yaml`](../demos/showcase/scenarios/menu/features.yaml).

## The same scenario shape on the web backend

Every recipe above is written against the iOS showcase, but nothing in the step/expect grammar is
iOS-specific — only the selector's underlying attribute changes (`accessibilityIdentifier` → the
web's `data-testid`, through the same selector-resolution core). The web demo's own second scenario
shows the same shape: navigate, act repeatedly, assert a final value, with evidence capture on top.

```yaml
scenarios:
  - name: onboard, log in, and increment the counter three times
    steps:
      - tap: { id: onboarding.start }
      - type: { text: "a@b.com", into: { id: auth.email } }
      - type: { text: "pw", into: { id: auth.password } }
      - tap: { id: auth.submit }
      - wait: { for: { id: home.title }, timeout: 5 }
        capture: [deviceLog, video]
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
    expect:
      - exists: { id: home.title }
      - value: { sel: { id: counter.value }, equals: "3" }
```

Run it with `uv run bajutsu run --scenario demos/web/scenarios/counter.yaml --target web --backend
web --config demos/web/demo.config.yaml` (see the [web track](getting-started/web.md)). Full file:
[`demos/web/scenarios/counter.yaml`](../demos/web/scenarios/counter.yaml).

---

## Where these come from

Every recipe on this page is a real scenario the repo's own CI runs — nothing here is
illustration-only. The showcase suite ([`demos/showcase/scenarios/`](../demos/showcase/scenarios/))
has about 25 more covering gestures, multi-touch, device control, visual regression, relaunch/state
persistence, and more; [showcase](showcase.md) catalogs the identifiers each screen exposes. The web
demo ([`demos/web/scenarios/`](../demos/web/scenarios/)) and the web UI's own dogfooding suite
([`demos/serve-ui/scenarios/`](../demos/serve-ui/scenarios/)) are worth a look too — the latter is
itself a good example of testing a complex single-page app. For the full step/wait/assertion
grammar behind every recipe here, see [scenarios](scenarios.md); for the formal EBNF, see
[dsl-grammar](dsl-grammar.md).
