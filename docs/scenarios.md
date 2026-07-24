**English** · [日本語](ja/scenarios.md)

# Scenario specification (authoring reference)

A [scenario](glossary.md#scenario-authoring) is Bajutsu's **only persisted artifact**: plain YAML, version-controlled in git and reviewable in a PR. `record` (AI) writes it the first time; humans own and edit it afterward. `run` executes this structure without AI.

Implementation: `bajutsu/scenario/` (pydantic models under `models/`, `extra="forbid"` rejects unknown keys).

The **normative grammar** — every production, type, default, and validation rule — is in [dsl-grammar](dsl-grammar.md). This page is the authoring guide: how to write a scenario, by example.

Related: [cookbook](cookbook.md) (worked examples) · [dsl-grammar](dsl-grammar.md) (formal grammar) · [selectors](selectors.md) (how selectors and assertions evaluate) · [evidence](evidence.md) · [run-loop](run-loop.md) (execution)

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

### Schema version

The mapping form may carry a top-level `schema` integer marking the scenario schema version. A file
that omits it is treated as version 1, so every existing scenario is valid unchanged:

```yaml
schema: 1
scenarios:
  - name: ...
    steps: [...]
```

When a scenario declares a `schema` newer than the running `bajutsu` understands, the load fails
with a clear upgrade-path message instead of an opaque "unknown field" error — the case that arises
once a scenario tree is read across versions (for example, a config sourced from a pinned Git ref).
The current version is `SCHEMA_VERSION` in `bajutsu/scenario/models/scenario.py`. Bump it only for a
load-breaking change — removing a required field's meaning, or a change an older `bajutsu` would
misinterpret rather than merely reject; a purely additive optional field needs no bump.

## Top-level structure (`Scenario`)

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | str | required | Scenario name (used for the report / JUnit testcase / codegen method name) |
| `description` | str | none | Optional human description; shown on the scenario's report card and in the serve UI |
| `from` | str | none | **Provenance** — the natural-language goal `record` authored this scenario from ([provenance](#from-provenance)). Authoring metadata only; `run` ignores it |
| `tags` | list[str] | `[]` | Selection labels; the CLI `--tag` / `--exclude` flags pick which scenarios run ([reuse, data, and tags](#reuse-data-and-tags)) |
| `data` / `dataFile` | list / str | none | Data-driven rows — inline `data`, or `dataFile` (a CSV path). Expands into one run per row, substituting `${row.col}`. Mutually exclusive ([reuse, data, and tags](#reuse-data-and-tags)) |
| `preconditions` | object | `{}` | Per-test environment setup (below) |
| `steps` | list | required | The ordered actions (below) |
| `expect` | list | `[]` | Final assertions after all steps pass ([selectors](selectors.md#assertion-evaluation)) |
| `capturePolicy` | list | `[]` | Repeatedly-firing evidence rules ([evidence](evidence.md#a-capturepolicy-rule-based)) |
| `network` | object | none | `{ filter: { domains: [...] } }` — `filter.domains` scopes which observed requests are interleaved into the report's Steps timeline (by URL host; a parent domain matches subdomains). Unset shows all; the Network tab always lists them all ([reporting](reporting.md#reporthtml)) |
| `mocks` | list | `[]` | Deterministic network stubs — a matching outgoing request gets a canned response instead of hitting the network ([network mocks](#network-mocks-deterministic-stubs)) |
| `redact` | object | none | Masking applied before evidence is written ([evidence](evidence.md#masking-redact)) |
| `dismissAlerts` | bool / object | none (on) | The vision **alert guard** — clears OS prompts the iOS backend cannot see. On by default; `false` disables it, `{ instruction: "tap Allow" }` keeps it on but taps a named button. CLI `--dismiss-alerts`/`--no-dismiss-alerts` overrides ([below](#dismissalerts-the-system-alert-guard)) |
| `permissions` | dict | `{}` | Declarative OS permission state — `{ <service>: grant \| revoke }` — applied **before the app launches** ([below](#permissions-pre-launch-permission-state)) |
| `interrupts` | list | `[]` | Handlers for an interstitial screen that surfaces at an **unpredictable** point — each `{ condition, steps }`, checked opportunistically wherever the screen appears ([below](#interrupts-handling-unpredictable-interstitial-screens)) |

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
```

(real file: [`demos/showcase/scenarios/search.yaml`](../demos/showcase/scenarios/search.yaml))

## preconditions (environment setup)

Implementation: `scenario/models/scenario.py` `Preconditions`. The runner's `launch_driver` reads this to build
the launch sequence ([run-loop](run-loop.md#runner-the-run-pipeline)).

| Key | Type | Default | Description | Wired |
|---|---|---|---|---|
| `erase` | bool | `false` | Wipe the whole simulator (`simctl erase` — apps/data/settings) before the test. Off by default; `reinstall` keeps the app fresh without a full wipe, so set `true` only when a test needs a pristine device | ✅ |
| `reinstall` | `clean` \| `overwrite` | `clean` | How the app is reinstalled before each run when the app config sets `appPath`: `clean` = uninstall then install (fresh app + data); `overwrite` = install over the existing app (keeps its data) | ✅ |
| `launchArgs` | list[str] | `[]` | Launch arguments (appended to config's `launchArgs`) | ✅ |
| `launchEnv` | dict | `{}` | Launch env (injected via `SIMCTL_CHILD_*`; merged onto config's `launchEnv`) | ✅ |
| `deeplink` | str | none | Opened after launch via `simctl openurl` | ✅ |
| `locale` | str | none | Force the locale/language at launch (`-AppleLocale`/`-AppleLanguages`); overrides the app/config default | ✅ |
| `setup` | str | none | A reusable prelude scenario file (resolved relative to this scenario); its steps run before this scenario's own | ✅ |

> **launchEnv resolution order** is **config's `launchEnv` < preconditions' `launchEnv`** (the
> one closer to the test wins). `launch_driver` merges `{**eff.launch_env, **pre.launch_env}`.

## dismissAlerts (the system-alert guard)

The iOS backend cannot see or tap **SpringBoard-level prompts** (iOS "Save Password?", a permission request, "Allow Paste"). These prompts cover the app and collapse its element tree, silently blocking a step. The **alert guard** is a vision-based fallback (`alerts.py`): when a step is blocked, it takes a screenshot, asks Claude where to tap, clears the prompt, and retries the step once ([details](recording.md#dismissing-system-alerts-automatically)). For a `wait` step (`for`/`settled`/`screenChanged`), the guard also watches the already-polled screen and fires **mid-wait** the moment the tree looks collapsed (debounced, cooldown-limited, capped at two attempts per wait) — recovering before the wait's own timeout elapses, rather than waiting for the step to fail first (BE-0269).

It is **on by default** and fires **only when a step (or `expect`) is blocked, or — for a guarded `wait` — the polled screen looks blocked**, so a passing scenario never calls the model. It requires `ANTHROPIC_API_KEY`; without one it no-ops and the run continues unaffected. Use `dismissAlerts` to change the behavior per scenario:

| Form | Meaning |
|---|---|
| (omitted) | on; tap the **least-destructive** button ("Not Now" / "Don't Allow" / "Cancel") |
| `dismissAlerts: false` | off for this scenario |
| `dismissAlerts: { instruction: "tap Allow" }` | on, but tap the button the instruction names — e.g. to **grant** a permission |
| `dismissAlerts: { enabled: false }` | off (the explicit object form of `false`) |

```yaml
- name: grant notification permission
  dismissAlerts: { instruction: "tap Allow" }   # accept the prompt instead of dismissing it
  steps:
    - tap:  { id: sys.requestNotif }
    - wait: { for: { id: sys.notif.authorized }, timeout: 4 }   # the guard taps Allow, then this passes
```

The CLI `--dismiss-alerts` / `--no-dismiss-alerts` flag **overrides every scenario** (otherwise the
per-scenario default applies); `--alert-instruction` sets a default button instruction that a
scenario's own `instruction` overrides. (real file:
[`demos/showcase/scenarios/permission.yaml`](../demos/showcase/scenarios/permission.yaml))

## handleSystemAlert (the deterministic system-alert step)

`dismissAlerts` above is a **reactive guard**: it fires only when a step is already blocked, and it
decides where to tap with a vision model. `handleSystemAlert` is its opposite — an explicit,
**deterministic step** the author places at the exact point a prompt is expected, which taps the
prompt's button by a native accessibility query, with **no screenshot and no model**
([BE-0316](../roadmaps/BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)). Reach
for it to test a request-and-grant flow itself: fire the OS permission request, then grant or deny the
prompt that follows, deterministically.

```yaml
- name: grant the notification prompt mid-flow
  steps:
    - tap: { id: perm.requestNotif }                              # fires the OS permission request
    - handleSystemAlert: { sel: { label: "Allow" }, timeout: 5 }  # tap the prompt's button by label
    - wait: { for: { id: perm.notif.authorized }, timeout: 5 }    # request granted, app state updates
```

To dismiss the prompt rather than accept it, target the dismissive button
(`handleSystemAlert: { sel: { label: "Don't Allow" }, timeout: 5 }`).

- **`sel` is label-based only.** A SpringBoard alert button carries no app-assigned identifier, trait,
  or value — only its visible text — so `sel` accepts `label` / `labelMatches` / `index` and rejects
  `id` / `idMatches` / `traits` / `value` / `within` at parse time.
- **`timeout` is required**, exactly as for `wait`: a condition wait for the prompt needs an explicit
  bound. The step waits the prompt in, then taps — no fixed sleep.
- **Fail-fast on zero or many.** No prompt within `timeout` fails the step; more than one button
  matching the label fails as ambiguous **unless** `index` selects the nth — the same rule every
  [selector](selectors.md) follows, applied to the alert's buttons.
- **iOS (XCUITest) only.** Only that backend declares the capability, so a scenario naming
  `handleSystemAlert` against the Android or web backend fails **preflight**, before any device work.
  Android surfaces a system dialog in its ordinary element tree, so a plain `tap` reaches it there;
  the web backend has no OS-level prompt at all.

When to reach for `handleSystemAlert` versus the two alert fields it stands beside:

| Field | For | Timing | Mechanism |
|---|---|---|---|
| `permissions` | an OS permission prompt you can avoid outright | pre-launch, before the app starts | deterministic device mutation |
| `handleSystemAlert` | a **known** mid-flow prompt you mean to tap | an explicit step where you place it | deterministic (native accessibility tap) |
| `dismissAlerts` | an **unexpected** out-of-process prompt the tree cannot see | reactive, when a step or wait is blocked | AI vision (`ANTHROPIC_API_KEY`) |

(real file:
[`demos/showcase/scenarios/permission_system_alert.yaml`](../demos/showcase/scenarios/permission_system_alert.yaml))

## permissions (pre-launch permission state)

`dismissAlerts` reacts to a permission prompt only *after* it appears, and only by tapping it —
useful when the prompt is unexpected, but it cannot **revoke** a permission or guarantee the app
starts from a known state. When the permission is known ahead of time, `permissions` sets it
**before the app process starts**, so the prompt never appears at all: a deterministic,
machine-checkable device mutation with no model call
([BE-0276](../roadmaps/BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)).

```yaml
- name: profile — camera already granted
  permissions:
    camera: grant
    location: grant
    contacts: revoke
  steps:
    - tap: { id: profile.avatar.upload }   # no camera-permission prompt — already granted
```

Each entry is `<service>: grant | revoke`, where `<service>` is one of a small backend-agnostic
vocabulary: `location`, `camera`, `microphone`, `contacts`, `photos`, `calendar`, `notifications`.
Each backend maps a service to its own native mechanism:

- **iOS** drives `simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>` — the same TCC
  (Transparency, Consent, and Control) database SpringBoard's permission prompts read.
- **Android** drives `pm grant` / `pm revoke`, reusing the plumbing behind the config-level
  `grantPermissions` list ([drivers](drivers.md)); a scenario's `permissions` layers on top of that
  config-level default and can revoke what it grants.

**iOS has no TCC service for `notifications`** (iOS notification authorization is not part of
TCC), so a scenario naming `notifications` on an iOS target fails **preflight** — before any device
work, naming the unsupported capability; `dismissAlerts` remains the reactive path for that one
prompt. Android's `POST_NOTIFICATIONS` *is* a runtime permission (API 33+), so Android supports the
whole vocabulary. Every other unsupported combination (a service unsupported on the chosen backend)
fails preflight the same way, named individually.

`permissions` has no app-level XCUITest / Espresso equivalent, so `codegen` emits a labeled
`// TODO` per service rather than generating code for it — bajutsu applies the field itself, before
the generated test's own launch step.

## interrupts (handling unpredictable interstitial screens)

An `if` step ([below](#conditional-steps-if)) checks its condition at **one point** in the step
sequence — the right tool when you know exactly which step precedes the screen you are branching on.
It is the wrong tool when a screen's appearance is not tied to any one step: an onboarding overlay, a
tutorial, or an in-tree permission prompt can each surface a few steps earlier or later than
expected, or not at all, depending on account state, network timing, or an A/B cohort. A single `if`
only catches the screen when it appears exactly where the `if` sits; every other timing slips through
and fails the rest of the scenario against a screen it was not written to expect.

`interrupts` handles that case. Each entry names a `condition` — the same assertion DSL `if` uses —
and the `steps` that clear the screen. The runner checks each entry **opportunistically**, against a
tree it has already fetched (a `wait`'s poll tick, an act step's pre-action read), wherever in the
sequence the screen happens to appear, and runs the entry's `steps` when the condition matches. After
the handler runs, the interrupted step resumes where it left off — a `wait` keeps polling toward its
original timeout, an act step takes its action — so an author no longer has to predict the one spot to
place an `if`.

```yaml
# config.yaml — an app-wide default: this app's onboarding screen, on every scenario
targets:
  myapp:
    interrupts:
      - condition: { exists: { id: onboarding.skip } }
        steps:
          - tap: { id: onboarding.skip }
```

```yaml
# scenario.yaml — this scenario's own addition, appended to the config-level list
- name: log in
  interrupts:
    - condition: { exists: { id: att.dialog } }   # App Tracking Transparency prompt
      steps:
        - tap: { id: att.allow }
  steps:
    - tap:  { id: login.button }
    - wait: { for: { id: home.title }, timeout: 10 }   # an interstitial mid-flow is cleared, then this passes
```

An `interrupts` list set at the **config** level (`targets.<name>.interrupts`) is an app-wide
default; a scenario's own `interrupts` is **appended** to it, config entries checked first — the same
config-then-scenario layering `dismissAlerts` follows. An entry's `steps` share the enclosing
scenario's `vars.*` bindings, exactly as `if`'s branches do. If a handler's own `steps` never clear
its `condition` (a broken selector, a screen that re-renders identically), the entry fires only a
small bounded number of times per step and then the step falls back to its ordinary outcome (pass,
fail, or timeout) — a mis-set entry fails the step cleanly rather than hanging the run.

The check is the deterministic assertion DSL, never a model call, so `interrupts` adds no AI to the
`run` verdict. That is the difference from `dismissAlerts`: the alert guard is the vision path
reserved for out-of-process system prompts the accessibility tree **cannot see**, while `interrupts`
handles a screen the tree **can** see with a machine-checkable condition. When to reach for which:

| Field | For | Timing | Mechanism |
|---|---|---|---|
| `if` | a screen at a **known** point in the sequence | one scripted check | deterministic (assertion DSL) |
| `interrupts` | a screen at an **unpredictable** point, visible in the tree | checked opportunistically throughout | deterministic (assertion DSL) |
| `handleSystemAlert` | a **known** out-of-process prompt you mean to tap mid-flow | an explicit step where you place it | deterministic (native accessibility tap) |
| `dismissAlerts` | an **unexpected** out-of-process prompt the tree cannot see | reactive, when a step or wait is blocked | AI vision (`ANTHROPIC_API_KEY`) |
| `permissions` | an OS permission prompt you can avoid outright | pre-launch, before the app starts | deterministic device mutation |

No native XCUITest / Espresso / Playwright construct maps onto "check this condition opportunistically
throughout the whole test," so `codegen` emits a labeled `// TODO` naming the field and each
configured condition rather than generating code for it — `bajutsu run` is the faithful path.

## Selectors (addressing an element)

A selector identifies **which element** to act on or assert against. Provide one or more fields; multiple fields are **AND**-ed (all must hold), and at least one is required. How a selector resolves to exactly one element, and why an ambiguous selector fails instead of picking the first match, is covered in [selectors](selectors.md). The formal shape is in [dsl-grammar](dsl-grammar.md#2-grammar-at-a-glance).

| Field | Type | Description |
|---|---|---|
| `id` | str \| list[str] | Exact `accessibilityIdentifier` — **first choice** (stable, non-localized). A list is an **OR** of candidates: the element's id must equal *any* one |
| `idMatches` | str \| list[str] | Glob over the id (e.g. `"list.row.*"`; assumes multiple matches). A list matches if the id matches *any* glob |
| `label` | str | Exact `accessibilityLabel` (visible text) — auxiliary / disambiguation |
| `labelMatches` | str | Regex / substring over the label (`re.search`) |
| `traits` | list[str] | Narrow by accessibility trait (subset test, e.g. `[button]`) |
| `value` | str | Exact accessibility value |
| `within` | Selector | Scope to a container — the match must sit inside an element the nested selector resolves to (nestable) |
| `index` | int | Pick the k-th of multiple matches (negatives allowed) — last resort, order-sensitive |

```yaml
- tap: { id: counter.increment }                               # by id (recommended)
- tap: { id: [stable.refresh, stable_refresh] }                # OR of id candidates (see below)
- tap: { label: "Delete" }                                     # by visible label (e.g. an alert button)
- tap: { id: row.action, within: { id: list.row.3 } }          # scoped to a container's subtree
- tap: { labelMatches: "^Item ", traits: [button], index: 0 }  # first matching button, fields AND-ed
```

> Prefer `id`. For a set of elements (count / existence) use `idMatches`. Use `index` only as a last resort — it breaks when order changes. Full resolution semantics: [selectors](selectors.md).

### Cross-platform ids: a candidate list (BE-0221)

A scenario is shared across platforms only to the extent its selectors are by `id`, and the driver decides which app-side attribute satisfies that `id`. But some platforms can't reproduce the SPEC id **verbatim**: Android's `android:id` (the Views toolkit) allows neither `.` nor `-`, so `stable.refresh` surfaces as `stable_refresh` and `search.results-empty` as `search_results_empty`. To keep **one** scenario running unchanged everywhere, give `id` / `idMatches` a **list of candidates** and the match becomes an OR over them:

```yaml
- wait: { for: { id: [stable.refresh, stable_refresh] }, timeout: 10 }
- count: { sel: { idMatches: [stable.row.*, stable_row_*] }, equals: 5 }
```

The dotted form matches on iOS and Android Compose (which reproduce it verbatim); the underscore form matches on Android Views. Only one form is ever on screen for a given app, so the selection stays deterministic: if **both** candidate forms happened to be present at once, the selector is ambiguous and fails fast — an OR never turns a two-or-more match into a silent pick. The candidate list keeps the id convention **explicit in the scenario**, rather than a hidden driver-side `.`↔`_` rewrite that could conflate distinct ids. The showcase's shared scenarios use this so `showcase-swiftui` / `showcase-compose` / `showcase-views` all run the same files.

## Step grammar (`steps`)

Each step is **exactly one action** + optional modifiers (`capture:` / `name:`). Two or more
actions in one step is a validation error (`scenario/models/steps.py` `_one_action`).

| Action | Form | Description |
|---|---|---|
| `tap` | `tap: <Selector>` | requires unique resolution (fails if ambiguous) |
| `doubleTap` | `doubleTap: <Selector>` | two quick taps on the resolved element |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | long press |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | with `into`, focuses first |
| `clear` | `clear: { into: <Selector> }` | focus the field and remove its entire current content; web context raises |
| `delete` | `delete: { into: <Selector>, count: <int> }` | focus the field and delete `count` characters from the end (`count > 0`); web context raises |
| `select` | `select: { into: <Selector>, mode?: "all" }` | focus the field and select its content (`mode` default `all`); the web context raises — the iOS (XCUITest) backend supports it natively, and codegen emits the native equivalent |
| `copy` | `copy: {}` | copy the active selection to the clipboard; requires a prior `select`; the web context raises — the iOS (XCUITest) backend supports it natively |
| `selectOption` | `selectOption: { sel: <Selector>, option: "..." }` | set a web `<select>` to the option with this value; web only (iOS / Android raise) |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` or `swipe: { from: [x,y], to: [x,y] }` | selector form and coordinate form cannot mix; the directional form **scrolls** |
| `drag` | `drag: { on: <Selector>, direction: up\|down\|left\|right, amount?: <frac> }` | a real pointer **drag** of the element (a handle / divider / slider), not a scroll |
| `pinch` | `pinch: { sel: <Selector>, scale: <num> }` | two-finger magnify; `scale > 0` (`>1` zooms in, `<1` out) |
| `rotate` | `rotate: { sel: <Selector>, radians: <num> }` | two-finger rotation; `>0` is clockwise |
| `handleSystemAlert` | `handleSystemAlert: { sel: <Selector>, timeout: <sec> }` | tap a button on an iOS SpringBoard permission prompt, deterministically ([below](#handlesystemalert-the-deterministic-system-alert-step)); iOS (XCUITest) only. `sel` accepts only `label` / `labelMatches` / `index` |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | condition wait (below) |
| `assert` | `assert: [ <Assertion>... ]` | mid-step verification |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | terminate + relaunch the app (re-applying launch env/args, plus the given overrides), then wait until ready |
| `setLocation` | `setLocation: { lat: <num>, lon: <num> }` | override the simulated GPS location (`simctl location set`) |
| `push` | `push: { payload: {...} }` | deliver a simulated push notification (`simctl push`) with this APNs (Apple Push Notification service) payload |
| `http` | `http: { method?, url, headers?, body?, status?, saveBody? }` | issue an HTTP request (test-data setup / webhook / API); checks `status`, stores the body as `${vars.<saveBody>}` |
| `totp` | `totp: { secret, into: { var } }` | generate an RFC 6238 time-based one-time password (2FA) locally into `${vars.<var>}` |
| `email` | `email: { match: { to?, subject?, subjectMatches? }, extract: { var, bodyMatches }, timeout }` | poll the configured mailbox until a matching message arrives, extract a code into `${vars.<var>}` |
| `manual` | `manual: { label: "...", bypass?: "..." }` | a human takeover recorded during `record` (BE-0185); has no deterministic run-time equivalent, so it **fails loudly** at `run` time — never a silent pass |
| `background` | `background: {}` | send the app to the background (Home button) |
| `foreground` | `foreground: {}` | resume a backgrounded app (`simctl launch`, no settle sleep) |
| `clearKeychain` | `clearKeychain: {}` | reset the Simulator keychain (saved passwords / certificates) |
| `clearClipboard` | `clearClipboard: {}` | clear the Simulator pasteboard |
| `setClipboard` | `setClipboard: { text: "..." }` | seed the Simulator pasteboard for a paste flow |
| `overrideStatusBar` | `overrideStatusBar: { time?, batteryLevel?, batteryState?, cellularBars?, wifiBars? }` | override the status bar for deterministic screenshots |
| `clearStatusBar` | `clearStatusBar: {}` | remove status-bar overrides (restore the live bar) |
| `use` | `use: { component: <file>, with?: {...} }` | expand a reusable component's steps — a compile-time macro ([reuse](#reuse-data-and-tags)) |

Modifiers:

- `capture: [<token>...]` — evidence for this step only ([evidence](evidence.md#b-inline-evidence)).
- `name: <str>` — the step id (the evidence output directory name · report label). Defaults to `step<i>`.
- `from: <str>` — **provenance** ([below](#from-provenance)): the phrase this step was recorded from. Authoring metadata; `run` ignores it.

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

> Internally, when `into` is given, the target is `tap`ped before `type_text` (`orchestrator/actions/`
> `_do_action`).

### `selectOption`

```yaml
- selectOption: { sel: { id: nav.theme-picker }, option: midnight }   # set the <select> to the option whose value is "midnight"
```

For a native HTML `<select>`, whose dropdown is not part of the page's element tree, a coordinate
tap cannot switch the value deterministically. `selectOption` resolves the `<select>` through the
same unique-match core every action uses, then sets the option by its **value** (not its visible
label) and fires a `change` event, so the page reacts exactly as it would to a user's pick. The
value matches what a `value` assertion reads back from the `<select>`, so a selection is directly
assertable. `selectOption` is a web-only action — a `<select>` has no native counterpart on iOS or Android,
so those backends fail the step with a clear "unsupported action" reason rather than doing nothing.

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame center → a screen fraction in a direction (default 0.125)
- swipe: { from: [100, 400], to: [100, 200] }              # raw coordinates (last resort)
```

`{on,direction}` and `{from,to}` must be **exactly one or the other** (mixing or omitting a side
is a validation error).

The **directional** form means "scroll", and each backend realizes it with the primitive that
actually scrolls: a real OS drag on iOS / Android, and — since a mouse drag does not scroll a web
page — a wheel event (desktop) or a touch drag (a mobile [`deviceMode`](drivers.md#playwright-web))
on web (BE-0227). The **coordinate** form is a literal pointer drag for its own sake (a canvas / map
pan / drag handle), the same raw-drag last resort on every backend.

### `drag`

```yaml
- drag: { on: { id: replay.divider }, direction: right }             # drag a grabbed handle
- drag: { on: { id: volume.slider }, direction: up, amount: 0.3 }    # ... a fraction of the screen
```

`drag` is an element-anchored **pointer drag** — it grabs the element and moves it in a direction,
for a resize divider, a slider thumb, a reorder handle: any control you drag rather than scroll. It
shares `swipe`'s directional geometry (`amount` is a fraction of the screen, `0 < amount ≤ 1`;
omitted, a small default), but where a directional `swipe` **scrolls**, `drag` performs a genuine
pointer drag. The distinction only bites on web: there a directional `swipe` is a wheel scroll that
would leave a grabbed handle unmoved, so use `drag` for it; on iOS / Android a real OS drag both
scrolls and moves handles, so the two coincide.

### `doubleTap` / `pinch` / `rotate` (gestures)

```yaml
- doubleTap: { id: gest.doubletap }                    # two quick taps
- pinch:  { sel: { id: gest.pinch },  scale: 2.0 }     # >1 zooms in, 0<scale<1 zooms out
- rotate: { sel: { id: gest.rotate }, radians: 1.57 }  # >0 clockwise (radians)
```

`scale` must be **> 0** (a validation error otherwise). `pinch` / `rotate` require multi-touch, which the iOS (XCUITest) backend and the generated XCUITest (`pinch(withScale:)` / `rotate(_:)`) both provide; a backend without it fails with a "needs multiTouch" reason. `doubleTap` runs everywhere (two taps). (real file: [`demos/showcase/scenarios/gestures.yaml`](../demos/showcase/scenarios/gestures.yaml))

### `wait` (condition wait)

Fixed sleeps are not supported. **`timeout` is mandatory** (no infinite waits).

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

### `http` (request, for test-data setup)

```yaml
- http: { method: POST, url: "https://api.test/seed", body: '{"n":1}', status: 200 }   # fails if status != 200
- http: { url: "https://api.test/token", saveBody: token }   # vars.token ← response body text
- assert:
    - exists: { id: home.title }
```

`http` issues the request from the runner over HTTP — it does **not** go through the UI driver — so a
`status` mismatch fails the step, and `saveBody` stores the response body text as `${vars.<name>}` for
later steps. Touching no device, it is the one device-independent action here.

### `totp` (two-factor one-time password)

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code } }   # vars.code ← current 6-digit OTP
- type: { text: "${vars.code}", into: { id: auth.code } }
```

`totp` computes an [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238) time-based one-time
password locally — from the shared `secret` (base32; keep it in `${secrets.*}`, not in the YAML) and
the current time — and stores the current code in `${vars.<var>}` for a later `type` / `assert`.
This automates a 2FA sign-in without a scripting escape hatch or an LLM: the value is a deterministic
function of the secret and the clock ([BE-0046](../roadmaps/BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)).

### `email` (poll a mailbox for a received code)

```yaml
- email:
    match: { to: "test@example.com", subjectMatches: "verification" }   # which message to wait for
    extract: { var: code, bodyMatches: "[0-9]{6}" }                     # vars.code ← first capture group
    timeout: 30
- type: { text: "${vars.code}", into: { id: auth.otp } }
```

`email` waits for a 2FA / verification code delivered by email: it polls a generic HTTP mailbox
(configured under `targets.<name>.mailbox`, see [configuration](configuration.md#mailbox-the-email-step))
until a message that arrived **after the step started** satisfies `match`, then extracts the value
from its body by the `bodyMatches` regex (first capturing group, or the whole match) into
`${vars.<var>}`. The wait is a **condition wait with a mandatory `timeout`** (no fixed sleep): a
timeout, a matched message whose body the regex can't hit, or an unreachable / non-2xx mailbox is a
clean step failure — never a silent wrong value. Only mail newer than the step's start counts (keyed
on message id, so a stale code from an earlier run is never matched), and among new matches the
newest wins. Deterministic and LLM-free; the endpoint and credentials live in config-referenced
`${secrets.*}`, so the scenario stays app-agnostic ([BE-0046](../roadmaps/BE-0046-otp-email-steps/BE-0046-otp-email-steps.md)).

### `manual`

A human takeover recorded during `record`.

```yaml
- manual: { label: "solve the login CAPTCHA" }                          # no deterministic equivalent (a real CAPTCHA)
- manual: { label: "grant Face ID", bypass: "device-control biometric match (BE-0052)" }   # names the bridge an author could wire
```

`record` emits a `manual` step when a blocker is an *operation* the AI cannot perform — a CAPTCHA, a
biometric prompt, a gesture the agent repeatedly fails to resolve. The human operates the live device
and hands control back (the `acted` handoff, [recording](recording.md#human-in-the-loop-handoff-be-0179));
the step records a marker of the observed transition, not the raw gesture. `bypass`, when set, names
the test-build flag or the device-control / device-state primitive (BE-0035 / BE-0052) an author could
wire to make the step replayable; omitted, it marks a takeover with no such equivalent (a real CAPTCHA).
Every codegen target renders it as a labeled `// TODO`. A `manual` step is **never a silent pass**: it
has no deterministic run-time equivalent, so at `run` time it fails loudly with `ManualStepRequired`,
surfacing `label` and the bypass hint (directives 1 and 2). Wiring the named `bypass` — then replacing
the `manual` step with the deterministic action — is the author's path to a replayable scenario ([BE-0185](../roadmaps/BE-0185-record-human-takeover-step/BE-0185-record-human-takeover-step.md)).

### Device & system control (iOS)

```yaml
- background: {}                                                        # Home button (backgrounds via SpringBoard, no terminate)
- foreground: {}                                                        # resume the backgrounded app (simctl launch)
- clearKeychain: {}                                                     # reset saved passwords / certificates
- clearClipboard: {}                                                    # clear the pasteboard
- setClipboard: { text: "COUPON123" }                                   # seed the pasteboard (paste flows)
- overrideStatusBar: { time: "9:41", batteryLevel: 100, wifiBars: 3 }   # freeze the status bar
- clearStatusBar: {}                                                    # restore the live status bar
```

Like `setLocation` / `push`, these drive the Simulator via `simctl`, so they need a per-device control
channel and fail cleanly on the fake driver / in parallel runs. `overrideStatusBar` is most useful right
before a screenshot or a `visual` assertion, to freeze the clock and signal bars for a stable image.
`background` / `foreground` are the two halves of a background/foreground transition; `foreground`
resumes the app without any settle sleep, so wait for a concrete element afterward if you need one.
`setClipboard` seeds the pasteboard for a paste flow ([BE-0052](../roadmaps/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)).

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
| `event` | an analytics / telemetry event was sent — endpoint + JSON body fields, with a count (needs `--network`) | `event: { url: "https://t.example.com/track", body: { name: purchase_completed }, count: { equals: 1 } }` |
| `requestSequence` | matchers were observed in this order (needs `--network`) | `requestSequence: [ { urlMatches: "/auth/refresh" }, { urlMatches: "/api/account" } ]` |
| `responseSchema` | a captured response body conforms to a JSON Schema (needs `--network`) | `responseSchema: { request: { urlMatches: "/api/items" }, schema: items.json }` |
| `visual` | the screen matches a baseline image (visual regression) | `visual: { baseline: home.png, threshold: 0.02 }` |
| `clipboard` | the device pasteboard matches (read back via `simctl pbpaste`) | `clipboard: { equals: "COUPON123" }` / `clipboard: { matches: "\\d{6}" }` |

- `exists` writes its selector **inline** (`{ id: ... }` directly). `negate` is optional.
- `value` / `label` take `sel:` + **exactly one** of `equals` / `contains` / `matches`.
- `count` takes `sel:` + **exactly one** of `equals` / `atLeast` / `atMost`.
- `enabled` / `disabled` / `selected` take a selector inline.
- `request` matches an **observed network exchange** ([details below](#request-network-assertion)); needs the `--network` run flag.
- `event` matches an **analytics / telemetry event the app sent** ([details below](#event-analytics-event-assertion)); needs the `--network` run flag.
- `requestSequence` checks a list of request matchers were **observed in order** ([details below](#requestsequence-ordered-requests)); needs the `--network` run flag.
- `responseSchema` validates a captured **response body against a JSON Schema** ([details below](#responseschema-json-schema-of-a-response)); needs the `--network` run flag.
- `visual` pixel-compares a screenshot against a baseline image ([details below](#visual-visual-regression)).
- `clipboard` reads the device pasteboard (`simctl pbpaste`) and checks **exactly one** of `equals` / `matches` (regex) — the read-back half of `setClipboard`, for verifying a "copy" action. It needs the per-device control channel, so it is unavailable on the fake driver / in parallel runs and fails cleanly there ([BE-0052](../roadmaps/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake.md)).

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
> [`demos/showcase/scenarios/network_mock.yaml`](../demos/showcase/scenarios/network_mock.yaml))

### `event` (analytics event assertion)

`event` asserts on a behavior the screen never shows: an analytics / telemetry event the app **sent**
([BE-0048](../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md)).
It is a pure check over the same observed exchanges `request` reads (needs the `--network` run flag),
so the verdict stays machine-only — no LLM. It filters the timeline by the event's **endpoint** (the
same `method` / `url` / `urlMatches` / `path` / `pathMatches` matcher as `request`), then by structured
**request-body fields**, and checks how many exchanges survive against a count operator.

| Field | Type | Description |
|---|---|---|
| `method` / `url` / `urlMatches` / `path` / `pathMatches` | str | Endpoint matcher (AND-ed), same meaning as `request` |
| `body` | map | Each `key: value` must be present in the JSON request body and equal the value, compared as text (so `amount: "300"` matches the JSON number `300`; a JSON boolean / null matches `"true"` / `"false"` / `"null"`) |
| `count` | object | Expected multiplicity — **exactly one** of `equals` / `atLeast` / `atMost`. Omitted means **at least one** |

```yaml
expect:
  # the purchase event fired exactly once with the right amount
  - event:
      url: "https://t.example.com/track"
      body: { name: purchase_completed, amount: "300" }
      count: { equals: 1 }
```

> At least one of an endpoint field or `body` must be present, so an event always pins something. A
> non-JSON, non-object, or absent request body matches no `body` criterion (it fails rather than
> guessing). Body values support `${vars.*}` / `${secrets.*}` tokens like the rest of the DSL.

### `requestSequence` (ordered requests)

`requestSequence` asserts that several requests happened **in a given order** — e.g. a token refresh
*before* the protected call ([BE-0048](../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md)).
It is a pure check over the observed timeline (needs the `--network` run flag), so the verdict stays
machine-only. It takes a non-empty list of [`request` matchers](#request-network-assertion) (the same
fields) and matches them as an **ordered subsequence**: each matcher must match a distinct exchange at
a strictly later position than the previous one. Unrelated traffic **may interleave** between them, so
the check is robust to noise; listing the same matcher twice requires two occurrences in order.

```yaml
expect:
  - requestSequence:
      - { method: POST, urlMatches: ".*/auth/refresh" }
      - { method: GET,  urlMatches: ".*/api/account" }
```

> Each matcher uses the same fields as `request` (`method` / `url` / `urlMatches` / `path` /
> `pathMatches` / `status` / `bodyMatches`); a matcher's own `count` is ignored here, since the
> sequence's job is **order**. For a pure multiplicity check, use `request` with `count`.

### `responseSchema` (JSON Schema of a response)

`responseSchema` asserts that a captured **response body conforms to a JSON Schema** — a contract
check the screen can't express ([BE-0048](../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md)).
It is a pure, deterministic check over the observed timeline plus a stored schema file (needs the
`--network` run flag), so the verdict stays machine-only. `request` selects the exchange (the same
matcher fields) whose response is validated; `schema` is a file path resolved within the target's
**schemas directory** (`--schemas` flag, config `targets.<name>.schemas`, or `schemas/` beside the
scenario). Validation uses the `jsonschema` library — install the `schema` extra
(`pip install bajutsu[schema]`).

```yaml
expect:
  - responseSchema:
      request: { method: GET, urlMatches: ".*/api/items" }
      schema: items.json        # resolved within the schemas dir
```

> It validates the **first** matching exchange's response. It fails (rather than guessing) when no
> exchange matches, the schema file is missing, the response has no body or isn't JSON, or the body
> doesn't conform. The schemas dir resolves like `--baselines` for `visual`.

### `visual` (visual regression)

```yaml
- assert:
    - visual: { baseline: "home.png", threshold: 0.02, exclude: [{ x: 0, y: 0, w: 390, h: 47 }] }
    - visual: { baseline: "detail.png", compare: pixelmatch, colorTolerance: 0.1, antialiasing: true }
    - visual: { baseline: "summary-card.png", element: { id: "summary-card" } }  # one element only
    - visual: { baseline: "home.png", exclude: [{ selector: { label: "last updated" } }] }  # mask by element
```

`visual` captures a screenshot and compares it against `baseline` (a PNG resolved inside the run's
baselines dir — `--baselines`, or `baselines/` beside the scenario).

The comparison engine is selectable via `compare` (BE-0165):

| Engine | Description | Default |
|---|---|---|
| `exact` | Pixel-perfect — any channel difference counts as a changed pixel. | Yes (backward-compatible) |
| `pixelmatch` | Perceptual YIQ color distance with anti-aliasing detection. Tolerates sub-pixel rendering noise and one-pixel edge shifts. | No |

When `compare` is omitted, the engine falls back to the target's `visualCompare` config
(under `defaults:` or `targets.<name>`), and then to `exact`.

`threshold` is the allowed percentage of differing pixels (default `0.0` = exact match), shared
by all engines. `colorTolerance` (0–1, default `0.1`) sets the per-pixel perceptual color
tolerance for `pixelmatch`; `antialiasing` (default `true`) discounts anti-aliased pixels from
the diff. `exclude` masks regions before comparing, e.g. a status bar or a clock. Each entry is
either a rectangle in screenshot pixels (`{ x, y, w, h }`) **or** a `{ selector: <Selector> }`
that names an element to mask (BE-0171); the element is resolved to its frame at evaluation time.
A baseline is created or updated with the `approve` command
([cli](cli.md#approve)) or the `serve` UI; a missing baseline fails the assertion. Pair it with
`overrideStatusBar` to keep the clock / battery deterministic. Diffs are surfaced in
`report.html`; for `pixelmatch`, only the surviving (non-discounted) pixels appear in the diff.

**Element-scoped comparison (BE-0171).** By default `visual` compares the whole screen, so any
unrelated change (a banner, a list that grew a row) fails the check and churns the baseline. Give
`element: <Selector>` to compare **only that element**: the screenshot is cropped to the element's
frame and the baseline is that crop, so the check ignores everything outside it. The selector is
resolved with the usual unique-resolution rules — an **ambiguous selector fails immediately**
rather than cropping the first match, and a selector matching nothing fails too. `approve` promotes
an element-scoped baseline exactly as it does a whole-screen one (the baseline is simply a smaller
image).

**Selector-based masking (BE-0171).** A pixel rectangle in `exclude` drifts the moment the layout
reflows or the device resolution changes. Naming the element instead — `{ selector: { label:
"last updated" } }` — is stable across those changes: the element is resolved to its frame and
masked exactly as a rectangle is. A mask selector that matches nothing is a no-op (there is nothing
on screen to hide); an ambiguous one fails, consistent with the determinism rule. Selector masks
and rectangles can be mixed in one `exclude` list, and both work with an element-scoped comparison
(a mask inside the cropped element is translated into the crop's coordinates).

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

Mocks are handed to BajutsuKit via the `BAJUTSU_MOCKS` env (`dump_mocks`, `scenario/serialize.py`). The formal
shape is in [dsl-grammar](dsl-grammar.md#2-grammar-at-a-glance).

## Reuse, data, and tags

A small templating and macro layer wraps the core grammar. It runs **at load time, before the deterministic run**, so the runner only ever sees plain, fully-expanded scenarios. The normative rules (expansion order, `${ns.key}` interpolation, depth limits) are in [dsl-grammar](dsl-grammar.md#6-the-templating--macro-layer). This section covers the authoring perspective.

### Components (`use` → reusable steps)

A **component** is a separate file containing a list of `params` and a list of `steps` that reference them as `${params.<name>}`. A `use` step invokes it, binding params via `with`. `use` is a **compile-time macro**: `expand_components` (`scenario/expand.py`) replaces it with the component's substituted steps before the run. Expansion is recursive — a component may itself `use` another, up to depth 25. It raises an error on a missing or unknown param, a residual `${params.*}` referencing something undeclared, or a reference cycle. No `use` step survives into the run, so determinism is unaffected.

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

A scenario with `data` (inline rows) or `dataFile` (a CSV path — the two are **mutually exclusive**) is expanded into **one scenario per row**, substituting `${row.<column>}` (`expand_data`, `scenario/expand.py`). Each derived scenario is renamed `"<name> [row N: col=val, …]"` and keeps the original preconditions, so every row reinstalls the app fresh and inherits the template's `erase` / `reinstall`.

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

> **Refs stay inside the suite.** A `use` component and a `dataFile` path resolve relative to the
> scenario file, and the resolved file must stay **within the suite root** (the scenarios dir the
> load started from). A ref that leaves it — an absolute path, a `../` chain that escapes the root,
> or a symlink pointing outside — is rejected with a clear error and never read, so a scenario cannot
> make the loader open a file outside its own tree ([BE-0174](../roadmaps/BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment.md)).
> A relative ref that stays inside the root keeps working — a sibling `components/shared.yaml`, or,
> from a scenario in a subdirectory, a `../shared.yaml` that climbs no higher than the root.

### Tags and selection

`tags` label a scenario; the CLI `--tag` / `--exclude` flags pick which scenarios run. A scenario is kept
when it carries at least one `--tag` (or none was given) **and** none of the `--exclude` tags —
`--exclude` wins over `--tag` (`select_scenarios`, `scenario/select.py`). Both flags accept a comma list.

```yaml
- name: checkout smoke
  tags: [smoke, checkout]
  steps:
    - tap: { id: cart.checkout }
```

```bash
uv run bajutsu run --target showcase-swiftui --tag smoke --exclude wip   # run @smoke, skip anything @wip (across the app's scenarios dir)
```

### Secrets (`${secrets.X}`)

Declare secret environment-variable names in config (`secrets: [API_TOKEN, ...]`). Each declared name `X` is resolved from the environment and substituted into the executed step **at action time** as `${secrets.X}`. The scenario file stores the **token**, never the value, and literal values are **auto-masked** in evidence, making secrets safe to commit and review. Unlike `${params.*}` / `${row.*}` (load-time expansion), this namespace is resolved by the run loop.

```yaml
# config declares: secrets: [API_TOKEN]
steps:
  - type: { text: "${secrets.API_TOKEN}", into: { id: auth.token } }   # real value typed; token kept in the report
```

### Runtime variables (`${vars.*}`)

A step's `extract` modifier captures a UI element's property into `vars.*` after the step
executes. Subsequent steps (and scenario-level `expect`) can reference the captured value
via `${vars.<name>}`.

```yaml
steps:
  - tap: { id: counter.inc }
    extract:
      count: { sel: { id: counter.value } }          # vars.count ← element's value (default)
      heading: { sel: { id: header }, prop: label }   # vars.heading ← element's label
  - assert:
      - value: { sel: { id: other.field }, equals: "${vars.count}" }
```

Each `extract` entry specifies a `sel` (selector, resolved via `resolve_unique`) and an
optional `prop` (`value` | `label` | `identifier`; default `value`). If the selector
cannot be uniquely resolved or the property is `None`, the step fails.

### Conditional steps (`if`)

A step can evaluate a condition (using the same assertion DSL) and branch:

```yaml
steps:
  - if:
      condition: { exists: { id: dialog.alert } }
      then:
        - tap: { id: dialog.dismiss }
      else:
        - tap: { id: home.start }
```

The condition is evaluated against the current element tree (with `${...}` interpolation).
If it passes, `then` steps run; otherwise `else` steps run (or nothing if `else` is omitted).
Nested steps share the same `vars.*` bindings as the enclosing scenario. `capture` and
`extract` modifiers are not allowed on `if` steps.

### Iterating over elements (`forEach`)

A step can iterate over all elements matching a selector:

```yaml
steps:
  - forEach:
      sel: { idMatches: "item.*" }
      as: current
      steps:
        - tap: { id: "${vars.current}" }
```

The element list is snapshotted once at loop start. Each matched element's `identifier` is
stored as `vars.<as>` for the nested steps. An element with no identifier fails the step.
Zero matches is a no-op (success). The selector supports `${...}` interpolation. `capture`
and `extract` modifiers are not allowed on `forEach` steps.

## capture token grammar

Shared by `capture:` (per-step) and `capturePolicy[].capture` (rules). The form is
`<kind>[.<modifier>]`.

- **Kinds**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **Modifiers**: `before` / `after` / `around` / `onError`

Validation is over the set of kinds and modifiers (`scenario/models/_base.py` `_validate_capture`). The
acquisition timing per kind, and which are captured, are in
[evidence](evidence.md#evidence-kinds-and-acquisition-timing).

## YAML caveat

PyYAML (YAML 1.1) resolves `on`/`off`/`yes`/`no` to booleans. To prevent the `capturePolicy`
trigger key `on:` from becoming `True`, Bajutsu's YAML loader (`_yaml.py`) treats **only
`true`/`false` as booleans** and keeps `on`/`off`/`yes`/`no` as strings.

## `from` (provenance)

`from:` records **which natural-language phrase a construct was recorded from** (BE-0044). It is an
optional string attached at four levels — the scenario (the original goal), each step, each `expect`
assertion, and each `capturePolicy` rule — so a reviewer can see *why* each part exists and judge
whether `record` normalized the intent faithfully.

```yaml
- name: open settings and reindex
  from: "Open settings, reindex, and confirm the normalization setting is gone"   # the original goal
  steps:
    - tap: { id: settings.open }
      from: "Open settings"
  expect:
    - exists: { label: "Normalization setting changed", negate: true }
      from: "The normalization setting is gone"
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [screenshot.after, network]
      from: "Capture a screenshot and network log on every submit"
```

- **`record` (Tier 1, AI) is the only writer.** It fills `from:` while normalizing the goal into the
  structured scenario; a hand-authored scenario simply omits it (and a dumped scenario stays clean —
  `from:` is pruned when unset).
- **`run` (Tier 2) ignores it entirely** — provenance is authoring metadata, never read by the
  orchestrator, so it adds no AI to the gate and cannot affect pass/fail.
- **Grouping is emergent:** when one utterance produces several steps, they carry the **same** `from:`
  string; there is no span syntax. `lint` reports an advisory provenance-coverage figure (how many
  steps carry `from:`); it never fails a run.
- **Shown in `trace` and the report.** [`bajutsu trace`](cli.md#trace) prints each step's phrase
  inline (`← "<phrase>"`) and `report.html` shows it under the step, collapsing a run of the same
  phrase into one label — turning the timeline into a natural-language ↔ action map.
- The phrase is kept **verbatim** in whatever language the author wrote (not translated).

## Round-trip (load ⇄ dump)

- `load_scenarios(text) -> list[Scenario]`: YAML string → validated models.
- `dump_scenarios(scenarios) -> str`: models → YAML (pruning `None` / empty list / empty dict for
  readability).

`record`'s output goes through this `dump_scenarios`. The generated YAML reloads cleanly via
`load_scenarios`.
