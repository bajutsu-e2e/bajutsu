**English** · [日本語](ja/dsl-grammar.md)

# Scenario DSL grammar (formal reference)

This page is the **normative grammar** of the scenario DSL (domain-specific language): every production, type, default, and validation constraint, derived directly from the pydantic models in `bajutsu/scenario/` (the `models/` subpackage; `extra="forbid"`, so unknown keys are rejected). Where [scenarios](scenarios.md) is the authoring guide (how to write a scenario, with examples), this page is the language spec (what parses and what is rejected). It also covers the templating and macro layer — components, data-driven rows, and `setup` preludes — that surrounds the core grammar.

Related: [scenarios](scenarios.md) (authoring guide) · [selectors](selectors.md) (how selectors/assertions evaluate) · [evidence](evidence.md) · [getting-started](getting-started/index.md)

---

## 1. Notation

The DSL is a tree of YAML nodes, so the grammar is written over the **abstract structure**
(mappings / sequences / scalars), not a character stream.

| Form | Meaning |
|---|---|
| `X ::= …` | production: `X` is defined as … |
| `A \| B` | alternation: `A` or `B` |
| `T?` (on a value) | optional value |
| `{ k: T }` | a YAML mapping with key `k` of type `T` |
| `{ k?: T }` | key `k` is optional |
| `A & B` | a mapping carrying the keys of **both** `A` and `B` |
| `list(T)` | a YAML sequence whose items are `T` |
| `map(K, V)` | a YAML mapping from `K` to `V` |
| `"literal"` | an exact string (a key name or an enum value) |
| `<Name>` | a non-terminal (defined elsewhere on this page) |

Scalar terminals: `string`, `integer`, `number` (int or float), `boolean` (**only** `true` /
`false` — see [§3](#3-the-lexical-layer-yaml)), `any` (any YAML value).

Every mapping rejects keys it does not declare (`_Model`, `scenario/models/_base.py`).

---

## 2. Grammar at a glance

The **reference graph** below shows which non-terminal references which. It makes visible the recursion and sharing that the EBNF text below states but does not show directly: `Selector`'s `within` self-loop; `RequestMatch`, shared by the `request` assertion, the `until: { request }` wait, and `Mock.match`; `Web` and `Component`, which both nest a fresh `Step` list; and the two control-flow steps — `If`, which nests `then`/`else` under an `Assertion` condition, and `ForEach`, which nests `steps` under a `Selector`. The diagram omits actions that carry only scalars and reference no shared non-terminal (`relaunch`, `setLocation`, `push`, `http`, `setClipboard`, `foreground`, and the remaining device / status-bar steps) and the `golden` assertion, whose payload is a bare path.

```mermaid
graph LR
  SF["ScenarioFile"] --> SC["Scenario"]

  SC -->|preconditions| PRE["Preconditions"]
  SC -->|steps| ST["Step"]
  SC -->|expect| AS["Assertion"]
  SC -->|capturePolicy| CR["CaptureRule"]
  SC -->|network| NET["Network"]
  SC -->|mocks| MK["Mock"]
  SC -->|redact| RD["Redact"]
  SC -->|interrupts| IR["Interrupt"]
  SC -->|before| ST
  SC -->|after| AR["AfterRule"]
  AR -->|steps| ST

  ST -->|"tap·doubleTap·longPress·<br/>type·swipe·pinch·rotate·<br/>select·clear·delete·<br/>selectOption·setPickerValue·<br/>drag·scroll·handleSystemAlert"| SEL["Selector"]
  ST -->|wait| WT["Wait"]
  ST -->|assert| AS
  ST -->|use| CMP["Component"]
  ST -->|web| WEB["Web"]
  ST -->|capture| CT["CaptureToken"]
  ST -->|if| IF["If"]
  ST -->|forEach| FE["ForEach"]
  CMP -->|steps| ST
  WEB -->|within| SEL
  WEB -->|steps| ST
  IR -->|condition| AS
  IR -->|steps| ST
  IF -->|condition| AS
  IF -->|"then·else"| ST
  FE -->|sel| SEL
  FE -->|steps| ST

  SEL -->|within| SEL
  WT -->|"for · until:gone"| SEL
  WT -->|until:request| RM["RequestMatch"]

  AS -->|"exists·enabled·<br/>disabled·selected"| SEL
  AS -->|"value·label"| TM["TextMatch"]
  AS -->|count| CM["CountMatch"]
  AS -->|"request·requestSequence·<br/>responseSchema"| RM
  AS -->|event| EM["EventMatch"]
  TM --> SEL
  CM --> SEL

  CR -->|on| TR["Trigger"]
  CR -->|capture| CT
  NET -->|filter| NF["NetworkFilter"]
  MK -->|match| RM
  MK -->|respond| MR["MockResponse"]
```

And the productions in full:

```ebnf
# ── Files ──────────────────────────────────────────────────────────────
# Two on-disk forms: a bare sequence of scenarios, or a mapping that also carries a file-level
# `description` and/or `schema` (the cross-version read gate, BE-0119; default 1, an older
# bajutsu rejects a higher declared version rather than misinterpret it).
ScenarioFile  ::= list(<Scenario>)
               | { schema?: integer, description?: string, scenarios: list(<Scenario>) }
ComponentFile ::= <Component>               # a single mapping (loaded separately)

# ── Scenario ───────────────────────────────────────────────────────────
Scenario ::= {
  name:            string,                  # required
  description?:   string,                   # authoring metadata; `run` never reads it
  from?:           string,                  # provenance: the natural-language goal `record` authored this from (BE-0044)
  tags?:           list(string),            # default []  — selection (§6.4)
  data?:           list(map(string,string)),# inline rows   ┐ XOR
  dataFile?:       string,                  # CSV path      ┘ (§6.3)
  preconditions?:  <Preconditions>,         # default {}
  before?:         list(<Step>),            # default []  — setup phase run ahead of `steps`, reported apart from it (BE-0392); the target config's own precede these
  steps:           list(<Step>),            # required
  expect?:         list(<Assertion>),       # default []  — final checks
  after?:          list(<AfterRule>),       # default []  — teardown rules run once the verdict exists (BE-0392); the target config's own follow these
  capturePolicy?:  list(<CaptureRule>),     # default []
  network?:        <Network>,
  mocks?:          list(<Mock>),            # default []
  redact?:         <Redact>,
  systemAlertHandling?: <SystemAlertHandling>,  # alert guard; on when unset
  iosTipKitHandling?: <bool>,                   # dismiss a blocking TipKit tip; off when unset (iOS only)
  permissions?:    <Permissions>,           # pre-launch OS permission state; default {}
  interrupts?:     list(<Interrupt>),       # default []  — handlers for interstitials that surface at an unpredictable point (BE-0314), appended after the target config's own
}

Component ::= { params?: list(string), steps: list(<Step>) }

# A handler the runner checks opportunistically wherever in the step sequence the matching screen
# surfaces, running `steps` to clear it (BE-0314). Free on a `wait`'s poll tick; every other
# non-`wait` step pays one extra read.
Interrupt ::= { condition: <Assertion>, steps: list(<Step>) }

# One teardown rule (BE-0392): the outcome it answers, and the steps to run for that outcome. The
# outcome is the scenario's own machine-checked verdict, never a model call.
AfterRule ::= { on: "always" | "success" | "error", steps: list(<Step>) }

# ── Preconditions ──────────────────────────────────────────────────────
Preconditions ::= {
  erase?:      boolean,                     # unset inherits the target config's erase, else off (BE-0177); simctl erase first
  reinstall?:  ("clean" | "overwrite"),     # default "clean" — app reinstall when config sets appPath
  launchArgs?: list(string),                # default []
  launchEnv?:  map(string,string),          # default {}    — injected as SIMCTL_CHILD_*
  deeplink?:   string,
  locale?:     string,
  setup?:      string,                      # a reusable prelude file (§6.4)
}

# ── SystemAlertHandling (reactive system-alert guard; on by default) ───
# Native SpringBoard query + tap on XCUITest (no model, reusing BE-0316; BE-0315). An alert that path
# cannot name is reported on the blocked step's own failure, never guessed at (BE-0402).
SystemAlertHandling ::= boolean                                   # true = on with the default policy, false = off
               | { rules?: [<SystemAlertRule>],             # answer a named prompt by choice — native path
                   labels?: [string],                       # ordered button labels — native path
                   visionInstruction?: string,              # free text; reaches no command — `run` rejects it
                   pollInterval?: number }                   # native poll cadence, seconds (default 1)
SystemAlertRule ::= { prompt: notifications|tracking|paste, choice: grant|deny }  # unique prompt per list

Permissions ::= map(PermissionService, PermissionAction)    # applied before the app launches
PermissionService ::= "location" | "camera" | "microphone" | "contacts"
                     | "photos" | "calendar" | "notifications"
PermissionAction  ::= "grant" | "revoke"

# ── Step = exactly one Action + optional modifiers ─────────────────────
Step      ::= <Action> & <StepMods>
StepMods  ::= { capture?: list(<CaptureToken>), extract?: map(string, <Extract>), name?: string, from?: string }
                # `from`: provenance, the natural-language phrase `record` normalized this step from (BE-0044)
                # `name` becomes a real filesystem path segment (the run's step_id, the editor's
                # artifact lookup) — a path separator, or a bare "." / "..", is a load error
Extract   ::= { sel: <Selector>, prop?: ("value"|"label"|"identifier") }   # default "value"
Action    ::=
    { tap:         <Selector> }
  | { tapPoint:    { x: number, y: number } }   # normalized 0..1 (top-left origin); vision fallback for a control absent from the tree (e.g. a no-id tab-bar tab)
  | { doubleTap:   <Selector> }
  | { longPress:   { sel: <Selector>, duration: number } }
  | { type:        { text: string, into?: <Selector>, submit?: boolean } }   # submit default false
  | { clear:       { into: <Selector> } }                  # focus the field and remove its entire current content (web-context raises)
  | { delete:      { into: <Selector>, count: integer } }  # focus the field and delete count characters from the end (count > 0; web-context raises)
  | { select:      { into: <Selector>, mode?: "all" } }    # focus the field and select its content (mode default "all"; web-context raises; codegen emits the native XCUITest equivalent)
  | { copy:        {} }                                    # copy the active selection to the clipboard (requires a prior select; web-context raises)
  | { selectOption:{ sel: <Selector>, option: string } }   # set a web <select> to the option with this value (web only; iOS/Android raise)
  | { setPickerValue:{ sel: <Selector>, value: string } }  # move a wheel-style picker to the row with this value (iOS only; sel addresses one wheel)
  | { swipe:       <Swipe> }                          # directional form scrolls; coordinate form is a raw drag
  | { drag:        <Drag> }                           # pointer-drag a grabbed element (handle / divider / slider), not a scroll
  | { scroll:      <Scroll> }                         # scroll (non-inertially) until `to` is on-screen, else fail at a bound (BE-0326)
  | { back:        {} }                               # navigate back (Android system key / iOS OS back button / web history)
  | { pinch:       { sel: <Selector>, scale: number } }    # scale > 0  (>1 in, <1 out)
  | { rotate:      { sel: <Selector>, radians: number } }  # >0 clockwise
  | { handleSystemAlert: { sel: <Selector>, timeout: number } }  # tap an iOS SpringBoard permission prompt (iOS/XCUITest only); sel accepts only label/labelMatches/index
  | { handleSystemAlert: { prompt: notifications|tracking|paste, choice: grant|deny, timeout: number } }  # same step, label resolved from the run's locale (BE-0320)
  | { wait:        <Wait> }
  | { assert:      list(<Assertion>) }
  | { relaunch:    { env?: map(string,string), args?: list(string) } }
  | { setLocation: { lat: number, lon: number } }
  | { push:        { payload: map(string,any) } }          # APNs payload, e.g. {aps:{alert:"…"}}
  | { http:        { method?: string, url: string, headers?: map(string,string), body?: string, status?: integer, saveBody?: string } }  # method default GET; saveBody → vars.<name>
  | { totp:        { secret: string, into: { var: string } } }  # RFC 6238 OTP → vars.<var> (secret is base32)
  | { email:       { match: { to?: string, subject?: string, subjectMatches?: string }, extract: { var: string, bodyMatches: string }, timeout: number } }  # poll mailbox → vars.<var>
  | { generate:    <Generate> }                            # a random or current-datetime value computed at run time → vars.<var> (BE-0377)
  | { background:       {} }                               # Home button (backgrounds via SpringBoard, no terminate)
  | { foreground:       {} }                               # resume a backgrounded app (simctl launch, no terminate); the other half of background
  | { clearKeychain:    {} }                               # reset saved passwords / certificates
  | { clearClipboard:   {} }                               # clear the pasteboard
  | { setClipboard:     { text: string } }                 # seed the pasteboard with text (simctl pbcopy), for paste flows
  | { overrideStatusBar: { time?: string, batteryLevel?: integer, batteryState?: string, cellularBars?: integer, wifiBars?: integer } }
  | { clearStatusBar:   {} }                               # restore the live status bar
  | { use:         { component: string, with?: map(string,string) } }   # macro (§6.2)
  | { if:          <If> }                                               # conditional (no capture/extract)
  | { forEach:     <ForEach> }                                          # loop (no capture/extract)
  | { web:         <Web> }                                              # enter a WebView's DOM context (BE-0037; no capture/extract)
  | { manual:      { label: string, bypass?: string } }                # human takeover recorded during `record` (BE-0185); fails loudly at run time — no deterministic equivalent unless `bypass` is wired

If ::= { condition: <Assertion>, then: list(<Step>), else?: list(<Step>) }
ForEach ::= { sel: <Selector>, as: string, steps: list(<Step>) }
Web ::= { within: <Selector>, steps: list(<Step>) }
    # `within` resolves natively to exactly one WKWebView host; nested `steps` address the
    # normalized DOM (`data-testid` → Element.identifier), not the native accessibility tree.

Swipe ::=
    { on: <Selector>, direction: ("up"|"down"|"left"|"right"), amount?: number }   # selector form  ┐ XOR
  | { from: <Point>,  to: <Point> }                                                # coordinate form ┘
    # amount (selector form only): travel as a fraction of the screen, 0 < amount ≤ 1; omitted = a small default fraction (0.125)
Drag ::= { on: <Selector>, direction: ("up"|"down"|"left"|"right"), amount?: number }   # element-anchored pointer drag (BE-0227), amount as in Swipe
Scroll ::= { to: <Selector>, direction?: ("up"|"down"|"left"|"right"), within?: <Selector>, amount?: number, maxScrolls?: integer }
    # scroll until `to`'s frame center is on-screen, else fail (BE-0326). direction = scroll direction (default "down"), the inverse of Swipe's finger direction.
    # within: the scrollable container to gesture inside (default: the whole screen). maxScrolls: step bound before failing (default 15, > 0).
    # amount: one step's travel as a fraction of the viewport, 0 < amount ≤ 1; omitted = 0.6. Sets where the loop starts, not its recovery floor (BE-0400).
Point ::= [ number, number ]

Generate ::=
    { random:   <Random>,   into: { var: string } }   # a fresh value the scenario did not already have ┐ XOR
  | { datetime: <Datetime>, into: { var: string } }   # the current time, as text                       ┘
Random ::=
    { string: { length: integer, charset?: ("alnum"|"alpha"|"numeric"|"hex") } }  # length > 0; charset default "alnum" ┐
  | { int:    { min: integer, max: integer } }                                    # inclusive range, min ≤ max          │ XOR
  | { float:  { min: number,  max: number, precision?: integer } }                # min ≤ max; precision ≥ 0 decimals   │
  | { uuid:   {} }                                                                # a version-4 UUID                    ┘
Datetime ::= { format?: string, offsetSeconds?: integer, offsetMinutes?: integer, offsetHours?: integer, offsetDays?: integer, timezone?: string }
    # format: a strftime pattern; omitted = ISO 8601 to the second. The four offsets are signed and additive.
    # timezone: an IANA name (e.g. "America/Los_Angeles"); omitted = UTC. An unrenderable format or an unknown zone fails at load.

# ── Selector (≥1 field; provided fields are AND-ed) ────────────────────
Selector ::= {
  id?:           string | list(string),        # a list is an OR over candidates (BE-0221) — one shared scenario can carry every platform's spelling of an id; list the canonical (dotted) form first
  idMatches?:    string | list(string),         # glob over the id (fnmatch, e.g. "list.row.*"); a list ORs candidates the same way as id
  label?:        string,
  labelMatches?: string,        # regex over the label
  traits?:       list(string),
  value?:        string,
  within?:       <Selector>,    # restrict to a container's subtree
  index?:        integer,       # pick the k-th match when intentionally non-unique
}

# ── Wait (exactly one of for / until) ──────────────────────────────────
Wait  ::= { for: <Selector>, timeout: number }
        | { until: <Until>,   timeout: number }
Until ::= "screenChanged" | "settled"
        | { gone: <Selector> }
        | { request: <RequestMatch> }

# ── Assertions (exactly one kind per item) ─────────────────────────────
Assertion ::=
    { exists:   <Selector> & { negate?: boolean } }   # selector inline; negate default false
  | { value:    <TextMatch> }
  | { label:    <TextMatch> }
  | { count:    <CountMatch> }
  | { enabled:  <Selector> }
  | { disabled: <Selector> }
  | { selected: <Selector> }
  | { request:  <RequestMatch> }
  | { event:    <EventMatch> }        # an analytics/telemetry event the app sent (BE-0048)
  | { requestSequence: list(<RequestMatch>) }   # ≥1 matcher; an ordered/aggregate set of matched exchanges
  | { responseSchema: <ResponseSchemaMatch> }   # validate a captured response body against a JSON Schema (BE-0048)
  | { visual:   <VisualMatch> }
  | { clipboard: <ClipboardMatch> }   # read-back of the device pasteboard (simctl pbpaste)
  | { golden:   <GoldenMatch> }       # compare the live element tree to a recorded golden file (BE-0006)

TextMatch  ::= { sel: <Selector> } & ( {equals:string} | {contains:string} | {matches:string} )
CountMatch ::= { sel: <Selector> } & ( {equals:integer} | {atLeast:integer} | {atMost:integer} )
CountOp    ::= ( {equals:integer} | {atLeast:integer} | {atMost:integer} )   # a count comparison with no selector — an EventMatch's multiplicity
ClipboardMatch ::= ( {equals:string} | {matches:string} )   # exactly one; matches is a regex
GoldenMatch ::= { path: string }   # resolved against the golden context's base directory (BE-0006)

VisualMatch ::= {                  # pixel-compare the screen against a baseline image
  baseline:   string,             # filename resolved inside --baselines (default: baselines/ beside the scenario)
  element?:   <Selector>,         # scope the comparison to this element's frame (BE-0171; default: whole screen)
  compare?:   "exact" | "pixelmatch",  # comparison engine (default: config or "exact"; BE-0165)
  threshold?: number,             # max allowed diff, % of pixels (default 0.0 = exact)
  colorTolerance?: number,        # per-pixel perceptual color tolerance, 0–1 (pixelmatch; default 0.1)
  antialiasing?: boolean,         # discount anti-aliased pixels from the diff (pixelmatch; default true)
  exclude?:   list(<ExcludeRegion> | <SelectorRegion>),  # regions masked before comparing (status bar, clock, …)
}
ExcludeRegion  ::= { x: number, y: number, w: number, h: number }   # screenshot pixels
SelectorRegion ::= { selector: <Selector> }   # mask the element's frame (BE-0171); ambiguous → fail, no match → no-op

RequestMatch ::= {              # ≥1 of the match fields below
  method?:      string,
  url?:         string,         # exact full URL (the endpoint)
  urlMatches?:  string,         # regex/substring over the URL (query strings live here)
  path?:        string,         # exact path (query ignored)
  pathMatches?: string,         # regex over the path
  status?:      integer,
  bodyMatches?: string,         # regex/substring over the request body
  count?:       integer,        # assertion → exact count; wait → lower bound
}

EventMatch ::= {                # ≥1 of an endpoint field (method/url/urlMatches/path/pathMatches) or body
  method?:      string,
  url?:         string,
  urlMatches?:  string,
  path?:        string,
  pathMatches?: string,
  body?:        map(string,string),  # each key must be present and equal (as text) to the request body's JSON value
  count?:       <CountOp>,           # expected multiplicity (default: at least one)
}

ResponseSchemaMatch ::= { request: <RequestMatch>, schema: string }   # `schema` resolved against the app's schemas dir

# ── Evidence capture ───────────────────────────────────────────────────
CaptureToken ::= <Kind> ( "." <Modifier> )?
Kind     ::= "screenshot" | "elements" | "actionLog" | "deviceLog" | "network" | "video" | "appTrace" | "rawTree"
Modifier ::= "before" | "after" | "around" | "onError"

CaptureRule ::= { on: <Trigger>, capture: list(<CaptureToken>), from?: string }
    # `from`: provenance, the instruction this evidence rule was normalized from (BE-0044)
Trigger ::=                                    # exactly one of action / event / result
    { action: string, idMatches?: string }     # idMatches only alongside action
  | { event: "screenChanged" }
  | { result: "error" }

# ── Network / mocks / redact ───────────────────────────────────────────
Network ::= { filter?: { domains?: list(string) } }
Redact  ::= { labels?: list(string), headers?: list(string), fields?: list(string), unmaskHeaders?: list(string) }
    # unmaskHeaders: an explicit, visible opt-out releasing one of the credential-bearing headers
    # (authorization, cookie, set-cookie, …) masked by default (BE-0130)
Mock    ::= { match: <RequestMatch>, respond?: <MockResponse> }   # match: request-side fields only
MockResponse ::= { status?: integer, headers?: map(string,string), body?: string, delayMs?: number }
```

> **Grammar vs. wiring.** This page specifies what **parses and validates**. How completely each
> action is actuated by a given backend, and which evidence kinds are acquired where, is a separate
> question tracked in [drivers](drivers.md) and the
> [architecture status table](architecture.md#implementation-status).

---

## 3. The lexical layer (YAML)

A scenario file is YAML, parsed by Bajutsu's loader (`_yaml.py`), with **one deliberate
deviation** from YAML 1.1:

- **Only `true` / `false` are booleans.** `on` / `off` / `yes` / `no` stay **strings**. This deviation keeps
  the `capturePolicy` trigger key `on:` a key (not the boolean `True`) and keeps id/label values
  like `on` intact. ([scenarios](scenarios.md#yaml-caveat))

Scalar mapping: YAML strings → `string`, YAML ints → `integer`, ints or floats → `number`, and a
`<Point>` is a two-element flow sequence `[x, y]`.

---

## 4. Cardinality & mutual-exclusion constraints

Beyond shapes, the models enforce these rules (each is a `model_validator`; a violation is a load
error). This table is the **authoritative list of "exactly one / at least one / not both"**.

| Construct | Rule | Source |
|---|---|---|
| `Selector` | **≥ 1** field present | `scenario/models/selector.py` |
| `Step` | **exactly one** action key (`tap` … `use`); `capture`/`name` are modifiers, not actions | `scenario/models/steps.py` |
| `Swipe` | **exactly one** form: `{on,direction}` **or** `{from,to}` — never mixed, never half-specified | `scenario/models/actions.py` |
| `Pinch` | `scale` **> 0** | `scenario/models/actions.py` |
| `HandleSystemAlert` | `sel` restricted to `label` / `labelMatches` / `index` (rejects `id`/`idMatches`/`traits`/`value`/`within`) | `scenario/models/actions.py` |
| `Wait` | **exactly one** of `for` / `until` | `scenario/models/assertions.py` |
| `Assertion` | **exactly one** kind (`exists` … `request` … `visual`) | `scenario/models/assertions.py` |
| `TextMatch` (`value`/`label`) | **exactly one** of `equals` / `contains` / `matches` | `scenario/models/assertions.py` |
| `CountMatch` (`count`) | **exactly one** of `equals` / `atLeast` / `atMost` | `scenario/models/assertions.py` |
| `ClipboardMatch` (`clipboard`) | **exactly one** of `equals` / `matches` | `scenario/models/assertions.py` |
| `RequestMatch` | **≥ 1** of `method`/`url`/`urlMatches`/`path`/`pathMatches`/`status`/`bodyMatches` (`count` is not a match field) | `scenario/models/assertions.py` |
| `EventMatch` (`event`) | **≥ 1** of `method`/`url`/`urlMatches`/`path`/`pathMatches`/`body` | `scenario/models/assertions.py` |
| `CountOp` (`event.count`) | **exactly one** of `equals` / `atLeast` / `atMost` | `scenario/models/assertions.py` |
| `Assertion.requestSequence` | **≥ 1** item | `scenario/models/assertions.py` |
| `Trigger` (`capturePolicy[].on`) | **exactly one** of `action` / `event` / `result`; `idMatches` only **with** `action` | `scenario/models/evidence.py` |
| `Scenario` | `data` and `dataFile` **not both** | `scenario/models/scenario.py` |
| every mapping | **no unknown keys** (`extra="forbid"`) | `scenario/models/_base.py` |

`exists` is special: its selector is written **inline** (`exists: { id: home.title }`), and an
optional `negate: true` checks *absence*. The loader rewrites that into `{ sel, negate }` before
validation (`Exists._inline`, `scenario/models/assertions.py`).

---

## 5. Defaults

Omitted optional keys take these values (so a minimal scenario is just `name` + `steps`).

| Field | Default |
|---|---|
| `Scenario.tags` / `expect` / `capturePolicy` / `mocks` / `interrupts` / `before` / `after` | `[]` |
| `Scenario.preconditions` | `{}` (i.e. `erase` unset — off unless the target config says otherwise — and `reinstall: clean`) |
| `Scenario.systemAlertHandling` | unset (alert guard on; dismiss the prompt) |
| `Scenario.iosTipKitHandling` | unset (off — a tip is sometimes the assertion; iOS only) |
| `Scenario.permissions` | `{}` (no pre-launch permission state applied) |
| `Preconditions.erase` | unset — inherits the target config's `erase`, else off (BE-0177) |
| `Preconditions.reinstall` | `clean` |
| `Preconditions.launchArgs` | `[]` |
| `Preconditions.launchEnv` | `{}` |
| `SystemAlertHandling.rules` | `[]` (no named-prompt rules; `labels`/the built-in dismissive labels answer every prompt) |
| `SystemAlertHandling.labels` | `[]` (no layer named a button; the built-in dismissive labels stand in) |
| `SystemAlertHandling.visionInstruction` | unset — and no other value is usable: `run` refuses one (BE-0402) and `record` / `crawl` read the free text only from their own `--alert-vision-instruction` flag |
| `TypeText.submit` | `false` |
| `Exists.negate` | `false` |
| `MockResponse.status` | `200` |
| `MockResponse.headers` | `{}` |
| `Component.params` | `[]` |
| `ScenarioFile.schema` | `1` (BE-0119) |

A complete minimal scenario:

```yaml
- name: opens home
  steps:
    - tap:  { id: onboarding.start }
    - wait: { for: { id: home.title }, timeout: 5 }
  expect:
    - exists: { id: home.title }
```

---

## 6. The templating + macro layer

Around the core grammar sits a small substitution + expansion layer. It runs at load time,
**before** the deterministic run, so the runner only ever sees plain, fully-expanded scenarios.

### 6.1 `${namespace.key}` interpolation

Implementation: `bajutsu/interp.py`. A token is `${namespace.key}` (whitespace inside the braces is
trimmed). Substitution is **type-preserving at the edges**:

- A string that is **exactly one token** (`"${row.qty}"`) becomes the **raw bound value** (e.g. a
  number stays a number).
- A token **embedded** in a larger string is spliced in as text (`"item-${row.id}"`).
- A token whose namespace is not being substituted **right now is left intact**, so each layer fills
  only its own namespace.

Namespaces: `params.*` (components, §6.2), `row.*` (data-driven, §6.3), `secrets.*` (declared
via config `secrets:`, resolved from the environment by the run loop at action time, §6.4), and
`vars.*` (runtime capture via `extract`, §6.5).

### 6.2 Components (`use` → reusable steps)

A `<Component>` is a separate file (`ComponentFile`): a list of `params` and a list of `steps` that
reference them as `${params.<name>}`. A `use` step invokes it, binding the params via `with`:

```yaml
# login.component.yaml
params: [email, password]
steps:
  - type: { text: "${params.email}",    into: { id: auth.email } }
  - type: { text: "${params.password}", into: { id: auth.password } }
  - tap:  { id: auth.submit }
```

```yaml
# in a scenario
steps:
  - use: { component: login.component.yaml, with: { email: "a@b.com", password: "pw" } }
```

`expand_components` (`scenario/expand.py`) **replaces** each `use` with the component's substituted
steps, recursively (a component may itself `use` another, depth ≤ 25). It raises on a missing param,
an unknown param, a residual `${params.*}` referencing something undeclared, or a reference cycle.
Because expansion is pure and compile-time, **no `use` survives into the run** — determinism is
unaffected.

### 6.3 Data-driven scenarios (`data` / `dataFile`)

A scenario with `data` (inline rows) or `dataFile` (a CSV path; mutually exclusive) is expanded into
**one scenario per row**, substituting `${row.<column>}` (`expand_data`, `scenario/expand.py`). Each
derived scenario is renamed `"<name> [row N: col=val, …]"` and **keeps the original preconditions**
(so every row reinstalls the app fresh and inherits the template's `erase` / `reinstall`).

```yaml
- name: search returns a result
  data:
    - { q: apple,  expect: "1 result" }
    - { q: banana, expect: "2 results" }
  steps:
    - type: { text: "${row.q}", into: { id: home.search }, submit: true }
  expect:
    - label: { sel: { id: home.status }, equals: "${row.expect}" }
```

### 6.4 `setup` preludes, secrets, and tag selection

- **`setup`** (a `Preconditions` key, or the app/config default): names a reusable scenario file
  whose steps are **prepended** to this scenario's own (`apply_setups`, `scenario/expand.py`) — a shared
  login / navigation flow written once.
- **`secrets`** (declared in config as `secrets:` — a list of environment-variable names): each
  declared name `X` is resolved from `os.environ[X]` and bound to `${secrets.X}`, substituted into the
  executed step **at action time** (`cli/commands/run.py`, `orchestrator/substitution.py` `_interp_step`). The scenario keeps the
  `${secrets.X}` token, never the value, and the literal values are auto-masked in evidence
  (`Redactor`). Unlike `params.*` / `row.*`, this namespace is resolved by the run loop, not at load.
- **`tags`** + the `--tag` / `--exclude` CLI flags filter which scenarios run; `exclude` wins over
  `include` (`select_scenarios`, `scenario/select.py`).

### 6.5 Expansion order

The load pipeline (`cli/commands/run.py`) applies these deterministically, in order:

```
load_scenarios        # parse + validate against this grammar
  → select_scenarios  # --tag / --exclude
  → apply_setups      # prepend the setup prelude (so a prelude may itself `use` components)
  → expand_components  # `use` → component steps  (${params.*})
  → expand_data        # one scenario per row     (${row.*})
  → run               # the deterministic loop sees only expanded scenarios
```

---

## 7. Validation & round-trip

- `load_scenarios(text) -> list[Scenario]` validates against everything above; the top level must be
  a sequence of scenarios or a `{description, scenarios}` mapping, and any rule in
  [§4](#4-cardinality--mutual-exclusion-constraints) failing is a load error (`scenario/load.py`).
- `dump_scenarios(scenarios) -> str` serializes back to YAML, pruning `None` / empty list / empty
  dict for readability and emitting alias keys (`idMatches`, `launchEnv`, …). The output **reloads
  cleanly** — this is the round-trip `record` relies on (`scenario/serialize.py`).

For the semantics behind the shapes — how a selector resolves to 0/1/2+ elements, how each assertion compares, how waits time out — see [selectors](selectors.md) and [run-loop](run-loop.md). To start writing scenarios by example, see [scenarios](scenarios.md).
