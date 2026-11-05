**English** · [日本語](BE-0466-web-attribute-assertion-ja.md)

# BE-0466 — Assert an arbitrary HTML attribute on the web backend

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0466](BE-0466-web-attribute-assertion.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0466") |
| Implementing PR | [#2248](https://github.com/bajutsu-e2e/bajutsu/pull/2248) |
| Topic | Verification & coverage |
<!-- /BE-METADATA -->

## Introduction

This item adds an `attribute` assertion on the web (Playwright) backend. The assertion checks one named HyperText Markup Language (HTML) attribute of one element. An author writes `attribute: { sel: { id: docs.link }, name: href, equals: /docs }`. The run passes when the `href` attribute of that element equals `/docs`.

The assertion compares with `equals`, `contains`, or `matches`, as the `value` and `label` assertions do. It adds `absent: true` for an attribute that must not be present. The driver reads the attribute on demand, when the runner evaluates the assertion. The element snapshot that every query returns stays unchanged.

The read rides on a new `Driver.read_attribute` method and a new `elementAttribute` capability. Only the web backend advertises the capability. A scenario that uses `attribute` on iOS or Android therefore fails in preflight, before any device is leased.

## Motivation

A web scenario today can check three properties of an element: its accessible label, its `value`, and its traits (such as `selected` or `disabled`). Much of what a web page promises lives in other HTML attributes, which no assertion can reach:

- **Link targets.** A link's `href` decides where a click goes. A scenario can check that the link exists and what it says, but not where it points.
- **Images.** An `<img>` carries its meaning in `src` and `alt`. A scenario cannot check that the right image loaded or that its alternative text is present.
- **State that a page exposes as data.** Pages often mark state in `data-*` attributes (`data-state="ready"`). Pages also use Accessible Rich Internet Applications (ARIA) attributes (`aria-expanded`, `aria-busy`, `aria-current`). A test author wants to assert on that state, because the page itself reports it.

A change in progress narrows part of this gap. It makes the web query fill `value` from the attribute that carries each tag's value: `href` for a link, `src` for an image, and `aria-valuenow` for a custom slider ([#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216)). That change gives each tag one fixed attribute, chosen by the tool.

An element has many attributes, though, and an author often needs a different one. Examples are the `alt` of an image rather than its `src`, or a `data-*` attribute that no tool could guess. A per-tag default cannot cover attributes that are specific to one application.

The observable outcome is this. A web scenario can assert any attribute an author names, with one `attribute` assertion and no change to the tool. A link's target and a `data-state` value are two examples.

A web demo scenario will assert a link's `href` and a `data-*` attribute. It will pass in the web end-to-end (E2E) lane. Element snapshots, goldens, and reports will not grow, because nothing reads an attribute that no assertion names.

## Detailed design

### Authoring surface

```yaml
steps:
  - assert:
      - attribute: { sel: { id: docs.link }, name: href, equals: /docs }
      - attribute: { sel: { id: hero.image }, name: alt, contains: Bajutsu }
      - attribute: { sel: { id: cart }, name: data-state, matches: "^(ready|idle)$" }
      - attribute: { sel: { id: submit }, name: aria-busy, absent: true }
```

The grammar adds one assertion kind:

```
Assertion ::= … | { attribute: <AttributeMatch> }
AttributeMatch ::= { sel: <Selector>, name: string }
                 & ( {equals:string} | {contains:string} | {matches:string} | {absent:true} )
```

- **`sel`** is an ordinary selector. It resolves exactly as it does for `value`. No match fails the assertion, and two or more matches fail it as ambiguous. The query must list the element (a `data-testid`, a `role`, or an interactive tag). A bare `<img>` therefore needs a `data-testid` to be asserted on.
- **`name`** is the attribute name. Validation accepts the HTML attribute-name syntax and rejects an empty name. The name reaches `getAttribute` unchanged. It matches case-insensitively on HTML elements and exactly on SVG or MathML elements, as the DOM does. Validation never lowercases it.
- **The operator** is exactly one of four. `equals`, `contains`, and `matches` follow the `TextMatch` rules that `value` and `label` already use. `matches` is a regular expression. `absent: true` passes when the element has no attribute by that name.
- **Presence differs from emptiness.** An attribute that is present with an empty value reads as `""`. `equals: ""` then passes and `absent: true` fails. A missing attribute fails `equals`, `contains`, and `matches`. The failure reason says the attribute is absent.
- **`${...}` tokens** in the expected string are expanded like any other assertion field.

The assertion works wherever the scenario language accepts an `Assertion`:

- a step's `assert` list;
- the scenario's `expect` list;
- an `if` condition;
- an `interrupts` condition.

A `wait` step takes a selector (`for`) or one of a fixed set of `until` forms: `screenChanged`, `settled`, `gone`, and `request`. No `until` form takes an assertion, so `attribute` does not become a wait condition. Making it one is out of scope.

### Behavior at run time

A step's `assert` list and the scenario's `expect` list run as a condition wait. `_poll_asserts` (`bajutsu/common/orchestrator/loop/_functions.py:129`) calls `driver.query()` again on every tick. It stops when the assertions pass or the lane's wait floor runs out.

The poll exists to close a read race: a value that an action changes can land a moment after the action returns. The attributes this item targets change in exactly that way. `data-state` and `aria-busy` are two examples. The read therefore follows the poll.

The poll's budget is the lane's wait floor and nothing else. `_timeout_floor()` is zero unless `BAJUTSU_MIN_WAIT_TIMEOUT` is set (`bajutsu/common/orchestrator/waits/_functions.py:47`). Today only the iOS and Android lanes set it. The web lane therefore takes one read per block, and the poll adds nothing there. Unit 7 sets a wait floor on the web demo's run, as the Android lane does. A `wait` step cannot stand in for the floor, because no `until` form takes an assertion.

1. Right after `driver.query()` on each tick, a helper `_attributes_for(driver, block)` handles each `attribute` assertion in the block. The helper calls `Driver.read_attribute(sel, name)` once, with the assertion's interpolated selector. The read may raise `SelectorError`, when no element matches or more than one does. The helper then records the reason instead of a value. The assertion fails with that reason, and the poll reads again on the next tick instead of aborting the step.
2. The helper stores the results in a new `attributes` field on `EvalContext`. The key is the assertion's interpolated selector and attribute name. The key cannot be the assertion's position, because `evaluate_one` hands the evaluator the assertion alone. The tick evaluates the block with that context.
3. The evaluator compares the read value with the operator and returns an ordinary `AssertionResult`. A selector that fails to resolve returns the same reason the `value` assertion gives.
4. A still-failing `attribute` assertion keeps the poll going, like `value` and the other tree-derived kinds. The kind therefore stays out of `_READ_ONCE_KINDS` (`_functions.py:109`). That set holds the kinds whose result a re-read cannot change.

An `if` condition and an `interrupts` condition evaluate without a context today. `_run_if` (`_functions.py:1349`) reads the tree once and calls the same helper after that query.

`_InterruptGuard._fire_once` (`_interrupt_guard.py:66`) runs no query of its own. It evaluates against a tree the loop already holds, on every `wait` poll tick and on an act step's pre-action read. It calls the helper against that tree and repeats the read on each tick. Either way, an `attribute` there decides the branch or the fire.

The evaluators stay pure functions of the tree and the context. The read happens in the runner, next to the query it pairs with, as the `clipboard` read does in `_clipboard_for` (`_functions.py:315`). No re-read can change the clipboard. The attribute read, by contrast, repeats with each tree.

The assertion stays inside the prime directives:

- **No large language model (LLM).** The verdict is a string comparison.
- **No fixed pause.** Each read is one synchronous call inside the existing condition wait.
- **No guessing.** An ambiguous selector fails through `resolve_unique`, the same rule a tap uses.
- **App-agnostic.** The attribute name lives in the scenario. The tool holds no per-application list of attributes.

### Reading the attribute in the web driver

`read_attribute` takes a `Selector`, like every other element-addressing method on the `Driver` protocol: `tap`, `select_option`, `wait_for`, and the rest. The driver resolves the selector itself. It uses the same `resolve_unique` (`bajutsu/common/drivers/base/_functions.py:284`) that a tap uses. Element identity therefore stays in the determinism core. `tap: { sel: … }` and `attribute: { sel: … }` reach the same verdict on the same selector.

The Playwright driver reads the attribute in one `page.evaluate` call:

1. The script runs the same walk as `QUERY_JS` (`bajutsu/common/drivers/dom.py`). Each record also carries `el.getAttribute(name)`, which is `null` for a missing attribute.
2. The driver maps the records to `Element`s with `parse_dom`. It keeps each record's attribute beside its element.
3. `resolve_unique(elements, sel)` picks the one element and returns its attribute. No match raises `ElementNotFound`. Two or more matches raise `AmbiguousSelector`. Both are `SelectorError`s.

Resolving against the same walk that carries the attribute leaves no gap in which the page could change between finding the element and reading it. The driver therefore never re-matches a snapshot element to a live node.

The walk is the query's own. A covered element and one outside the viewport read the same as any other listed element.

The name reaches the script `json.dumps`-encoded, as `_point_hits` encodes `identifier` and `label` (`bajutsu/common/drivers/playwright/playwright_driver.py:389`). The driver never splices the name in raw. Each read costs one walk of the page, the price of a second query.

### Driver, capability, and preflight

| Layer | Change | File |
|---|---|---|
| Scenario model | `AttributeMatch`; the `attribute` kind on `Assertion` | `bajutsu/common/scenario/models/assertions/attribute_match.py` (new), `assertion.py` |
| Driver protocol | `def read_attribute(self, sel: Selector, name: str) -> str \| None` | `bajutsu/common/drivers/base/driver.py` |
| Capability | `ELEMENT_ATTRIBUTE = "elementAttribute"` | `bajutsu/common/drivers/base/capability.py` |
| Preflight | an `attribute` assertion needs `elementAttribute`. `_assertions_with_path` reaches neither `web:` / `app:` blocks nor `interrupts`, so the locator recurses into those blocks and into `interrupts` recovery steps as `_navigate_locations` (BE-0464) does, and also checks each `interrupts` condition; `_steps_for_target` also narrows `interrupts` to the entries the target can poll: an entry naming it, and an entry omitting `target` when the target is the initial primary or any `setPrimaryTarget` destination (an omitted-target entry follows the primary in force, `TargetRoster.interrupts_for`, BE-0447), so a web-only entry does not fail an iOS target that never becomes primary | `bajutsu/common/capability/capability_preflight.py` (the locator), `bajutsu/common/runner/pipeline.py` (`_steps_for_target`, beside `_scenario_interrupts_for`) |
| Evaluation | the `_attributes_for` helper, the `EvalContext.attributes` field, and the `attribute` evaluator | `bajutsu/common/orchestrator/loop/_functions.py` (the step/`expect` poll and `_run_if`), `bajutsu/common/orchestrator/loop/_interrupt_guard.py`, `bajutsu/common/assertions/evaluate/` |
| Web driver | `read_attribute` as described above | `bajutsu/common/drivers/playwright/playwright_driver.py` |
| Fake driver | resolves the selector against its seeded tree, returns the attribute seeded for that element, and advertises the capability | `bajutsu/common/drivers/fake/fake_driver.py` |
| Other drivers | raise `UnsupportedAction`, the way `select_option` does on iOS and Android | `adb`, `xcuitest`, `xcuitest_live`, `webview` drivers |
| Config validation | reject an `attribute` in a non-web target's `hooks.interrupts` | `bajutsu/common/config/schema/target_config.py` |

Bajutsu also reaches the Document Object Model (DOM) of a WebView embedded in an iOS app. The route is the BajutsuKit bridge that `WebContextDriver` (`bajutsu/common/drivers/webview/web_context_driver.py`) drives. The bridge is out of scope for the first slice. `WebContextDriver` already raises `UnsupportedAction` for `selectOption`, and it will raise the same for `read_attribute`. The preflight therefore fails an `attribute` assertion nested in a `web:` block on an iOS target, before any device is leased.

A target's config can declare interrupts of its own (`hooks.interrupts`, `bajutsu/common/config/schema/hooks.py:47`). The runner merges them with the scenario's entries. The preflight reads the scenario alone, so it never sees the config's entries. Config validation therefore rejects an `attribute` in the `hooks.interrupts` of a non-web target when the config loads.

The check belongs on the merged `TargetConfig` (`bajutsu/common/config/schema/target_config.py:79`) rather than on `Hooks` itself, which carries no platform. The same `Hooks` model backs the team-wide `defaults.hooks` (`defaults.py:53`). That model merges into each target before the target is validated. One check on `TargetConfig` thus judges both shapes under each target's own platform. The target's platform already decides whether its backend advertises `elementAttribute`, so the check needs no device.

### Reports, goldens, and secrets

Because the read is on demand, an attribute enters the run's output only when an assertion names it.

- **Snapshots and goldens.** The query snapshot and the `Element` type do not change. Golden files, crawl output, and the element tree in a report gain no field.
- **The assertion result.** A failure reason prints the actual value, as the `value` assertion does today (`expected equals='/docs' but actual='/about'`). The string passes the artifact redaction boundary ([BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md)) like every other reason. The boundary masks a resolved secret in the string.
- **What an author can expose.** An author can name an attribute that holds a token. The report then shows that one value, which the author asked for. The redaction boundary still masks values it knows to be secrets. The tool never collects every attribute in bulk, so an attribute nobody named cannot leak.

### Codegen, authoring, and documentation surfaces

- The Playwright generator emits `await expect(locator).toHaveAttribute(name, value)`. It passes a regular expression for `matches` and an escaped regular expression for `contains`. It emits `not.toHaveAttribute(name)` for `absent`. The XCUITest and UI Automator generators emit the labeled `// TODO` they use for other web-only constructs.
- Several places list the assertion vocabulary beyond the model. The implementation checks and updates each of them:

  | Surface | Where |
  |---|---|
  | the JavaScript Object Notation (JSON) schema | the output of `bajutsu schema` |
  | the English pages | `docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/drivers.md`, `docs/codegen.md` |
  | the Japanese mirrors | the same pages under `docs/ja/` |

### Out of scope

- **Selecting an element by attribute** (`sel: { attr: { href: /docs } }`). Matching would need every attribute in the snapshot, which this item avoids. A later item can revisit selection on its own terms.
- **An attribute as a wait condition** (`wait: { until: { attribute: … } }`). No `until` form today takes an assertion. A polled attribute read deserves its own design.
- **The BajutsuKit WebView bridge.** The bridge needs a new message type. It can follow once the Playwright path has settled the contract.
- **DOM properties.** The assertion reads attributes (`getAttribute`), not live properties such as `checked`. Its value is therefore what the markup and the page's script set. The `value` assertion keeps covering the live value of a form control.

### Work breakdown (MECE)

| # | Unit | Done when |
|---|---|---|
| 1 | Scenario model: `AttributeMatch`, the `attribute` kind, name validation, exactly-one operator | model tests load each operator, and reject an empty name, two operators, and `absent: false` |
| 2 | Driver protocol, capability token, preflight requirement, config validation, non-web drivers raise | preflight tests show iOS and Android targets, an `attribute` nested in an iOS `web:` block, and an `attribute` in an `interrupts` condition fail before device lease with a message naming `attribute`. A web-only `interrupts` entry does not fail an iOS target, and an untargeted entry fails a target that a `setPrimaryTarget` step makes primary. A config whose non-web target declares an `attribute` in `hooks.interrupts` fails to load |
| 3 | `_attributes_for`, `EvalContext.attributes`, evaluator | fake-driver tests cover each operator, presence against emptiness, an absent attribute, and an ambiguous selector that fails without a read. An attribute that changes on a later tick passes within the wait floor. An `attribute` in an `if` condition and in an `interrupts` condition reads the attribute and decides the branch or the fire |
| 4 | Playwright `read_attribute` | web driver tests in real Chromium read an attribute through a `data-testid` selector and a label selector, read a covered element and one outside the viewport, return `None` for a missing attribute, and raise `ElementNotFound` and `AmbiguousSelector` exactly where `resolve_unique` does for `tap` |
| 5 | Driver conformance case | the web conformance suite passes with a new `read_attribute` case, and non-web backends skip it by capability |
| 6 | Codegen | the Playwright output for an `attribute` scenario matches a golden, and the other generators emit the labeled `TODO` |
| 7 | Web demo scenario | a scenario under the web demo asserts a link's `href` and a `data-*` attribute that settles after an action. The web demo's run sets `BAJUTSU_MIN_WAIT_TIMEOUT`, and the scenario passes in the web E2E lane |
| 8 | Documentation, schema, and vocabulary lists | the English and Japanese pages list `attribute`, the schema includes it, and `make check` is green |

### Open points for review

- **The kind's name.** We chose `attribute`, which reads naturally beside `value` and `label`. A shorter `attr` saves typing but departs from the spelled-out names the other kinds use.
- **The `absent` operator.** We chose `absent: true` over reusing `exists` with a new field. The question concerns one attribute of an element that does exist.
- **`read_attribute` takes a `Selector`, not an `Element`.** Taking the snapshot `Element` would save the second walk. Each backend would then re-match the element to a live node with its own rule. That rule could call ambiguous what `resolve_unique` already accepted as unique. Every later DOM-capable backend would also re-implement the rule. Resolving the selector in the driver keeps one matching rule, at the cost of one walk per read.

## Alternatives considered

- **Collect every attribute in each query snapshot.** Each `Element` would carry an `attributes` map. The assertion would read from the snapshot like `value`. We rejected this approach for three reasons. Every query would grow with markup that no assertion reads. Golden files would change whenever a page added an unrelated attribute. A `data-*` attribute that holds a token would appear in every report, even when nobody asked for it.
- **Let the target config choose which attribute fills `value`.** A `targets.<name>` map such as `img: alt` would extend the per-tag default of [#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216) with no change to the scenario language. We rejected this approach because it gives each tag a single attribute for the whole target. A scenario could not check both the `src` and the `alt` of an image. An attribute that varies by element, such as `data-state`, has no tag to key on.
- **Add selection by attribute in the same item.** Selection would let an author address an element by `href` alone. We left selection out because matching needs every attribute in the snapshot, which brings back the costs of the first alternative.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Scenario model: `AttributeMatch`, the `attribute` kind, name validation
- [x] Driver protocol, capability token, preflight requirement, config validation, non-web drivers raise
- [x] `_attributes_for`, `EvalContext.attributes`, evaluator
- [x] Playwright `read_attribute`
- [x] Driver conformance case
- [x] Codegen
- [x] Web demo scenario
- [x] Documentation, schema, and vocabulary lists

Log:

- Proposed.
- [#2248](https://github.com/bajutsu-e2e/bajutsu/pull/2248) — Shipped all eight units in one change, built against today's tree with three deviations
  from the design text. The config check reads a new `PlatformSchema.element_attribute` flag (true
  for `web` and `fake`), because the config schema imports no driver and so cannot ask a backend
  for its capabilities; a test pins the flag to each platform's `CAPABILITIES`. `QUERY_JS` stays
  unchanged, because the BajutsuKit WebView bridge keeps a hand-copied twin of it
  (`tests/test_dom_query_js_sync.py`); the driver reads through `ATTRIBUTE_QUERY_JS`, derived from
  `QUERY_JS` by string substitution so the two walks cannot drift. The conformance suite has no
  per-capability skip, so the new case follows the existing capability-promise shape. The web demo
  scenario passes under the new `E2E_WAIT_FLOOR` and fails without it (`actual='loading'`), which
  shows the poll is what catches the settling `data-state`.
- [#2248](https://github.com/bajutsu-e2e/bajutsu/pull/2248) — Review follow-up: the driver method is `read_attributes(reads)`, a batch over every
  `(sel, name)` pair of an assertion block, instead of the design's per-pair
  `read_attribute(sel, name)`. One read per assertion let a block pass on values taken a few
  milliseconds apart that never held together. One walk per block judges every assertion against
  one instant, and costs one walk per tick instead of one per assertion. A selector failure comes
  back as that pair's value, so the other pairs keep their reads.

## References

- [#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216): fills `value` from each tag's own value attribute on the web backend.
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary.md): the artifact redaction boundary that masks secrets in assertion reasons.
- [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step.md): the precedent for a web-only feature behind a capability and a preflight requirement.
- `bajutsu/common/orchestrator/loop/_functions.py`: `_poll_asserts`, the condition wait the read follows, and `_clipboard_for`, the precedent for a read beside the query.
