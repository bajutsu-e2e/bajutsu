**English** · [日本語](ja/comparison-maestro.md)

# Bajutsu compared with Maestro

> Where Bajutsu and [Maestro](https://maestro.dev/) agree, and where they diverge. Maestro is the
> closest tool to Bajutsu in shape. Both are open-source, black-box end-to-end (E2E) automation
> frameworks driven by YAML Ain't Markup Language (YAML) files, spanning iOS, Android, and the web.
> Comparing the two separates what belongs to the category from what Bajutsu decided on purpose.

Related: [concepts](concepts.md) · [vision](vision.md) · [ai-boundary](ai-boundary.md) ·
[selectors](selectors.md) · [dsl-grammar](dsl-grammar.md)

---

## What each tool is

**Maestro** is a mobile and web user-interface (UI) automation framework from mobile.dev. It ships
as one Java binary that interprets YAML *flows*. The binary installs a small companion driver
application on the device and drives the accessibility tree. Two commercial products sit around the
open-source command-line interface (CLI). Maestro Studio is a desktop authoring environment;
Maestro Cloud is a hosted device farm. Maestro states its philosophy as automation at arm's length,
with **built-in tolerance** to flakiness.

**Bajutsu** is a natural-language-driven E2E testing tool built on a backend-agnostic driver. AI
writes and investigates scenarios. A deterministic runner decides pass and fail. Bajutsu states its
philosophy as **determinism first**: no fixed sleep, and an ambiguous selector fails rather than
resolving to an arbitrary match.

Both carry the Apache 2.0 license. Both persist tests as plain YAML that a team reviews in a pull
request. The tools part company over one question. **What may a passing verdict depend on?**

## How this comparison was made

Every claim below rests on a primary source rather than on marketing copy.

| Side | Source | Version read |
|---|---|---|
| Maestro | The `mobile-dev-inc/maestro-docs` documentation repository | Commit of 2026-08-26 |
| Maestro | The `mobile-dev-inc/Maestro` source repository | `VERSION_NAME=2.10.0` |
| Bajutsu | This repository's `docs/`, `roadmaps/`, and source | The current `main` |

Where a tool's documentation and its source disagree, the source wins. One case below shows why.
The first-match selector behavior in [Selector resolution](#selector-resolution) comes out of
`maestro-client`'s own `Maestro.kt`. No prose states it.

## Shared ground

Much of what looks distinctive about either tool belongs to the category. Neither side gains an
advantage from any row below.

| Capability | Maestro | Bajutsu |
|---|---|---|
| Tests as reviewable YAML, no compilation | ✅ | ✅ |
| Black-box driving through the accessibility tree | ✅ | ✅ |
| iOS, Android, and web from one scenario format | ✅ | ✅ |
| Flutter and React Native without a new backend | ✅ | ✅ |
| Condition waits in place of a fixed sleep | ✅ | ✅ |
| Reusable subflows or components with parameters | ✅ | ✅ |
| Tag-based suite selection | ✅ | ✅ |
| Setup and teardown callbacks | ✅ | ✅ |
| Screenshot-baseline visual regression | ✅ | ✅ |
| JUnit Extensible Markup Language (XML) and standalone HyperText Markup Language (HTML) reports | ✅ | ✅ |
| A per-run `manifest.json` artifact index | ✅ | ✅ |
| Screen recording and device logs as artifacts | ✅ | ✅ |
| A Model Context Protocol (MCP) server for coding agents | ✅ | ✅ |
| A graphical authoring surface | Studio (desktop) | `serve` (browser) |

## The dividing line: what a verdict may depend on

Three mechanisms decide the character of each tool. Every other difference follows from them.

### Selector resolution

Maestro resolves a selector to its **first match**. `Maestro.kt`'s `findElementWithTimeout` calls
`filter(hierarchy.aggregate()).firstOrNull()`. The `index` selector exists so an author can pick a
different one. Put three "Add to Cart" buttons on a screen, and a bare `tapOn` passes. It taps
whichever the filter ordered first.

Bajutsu resolves a selector to **exactly one element or fails**. Two or more candidates raise
`AmbiguousSelector` ([selectors](selectors.md#resolve_uniqueelements-sel---element)). Here `index`
is the deliberate opt-out for a set the author knows to be non-unique. A scenario that would have
tapped an arbitrary button fails while it is being written. It never passes for the wrong reason.

The trade runs in both directions. Maestro's rule makes a first flow work sooner. Bajutsu's rule
makes a green suite mean more.

### Retry and tolerance

Maestro treats instability as an environmental fact to absorb. Four mechanisms carry that stance:

- `retry` re-runs a block up to three times.
- `retryTapIfNoChange` repeats a tap that changed nothing.
- `optional: true` lets a step fail without failing the flow.
- Maestro Cloud's **smart retries** re-run a red flow whose previous run passed on the same device
  model, with no author involvement.

Bajutsu's grammar has no retry construct anywhere ([dsl-grammar](dsl-grammar.md)). A verdict that
flips is a defect to diagnose. The `flakiness` and `audit --history` commands rank and explain
those flips rather than papering over them.

Maestro's own documentation names the cost. Wrapping a flow in `retry` "could hide genuine app
flakiness, like a button that only works 50% of the time".

### AI in the verdict path

Maestro added three AI commands that a flow may call at run time. `assertWithAI` evaluates a
natural-language assertion against a screenshot. `assertNoDefectsWithAI` audits a screen for visual
defects. `extractTextWithAI` reads a value off the screen into a variable. All three default to
`optional: true`, because a large language model (LLM) answer is probabilistic.

The model itself is **managed by Maestro Cloud**. An account is a prerequisite. Maestro retired the
`MAESTRO_CLI_AI_KEY` and `MAESTRO_CLI_AI_MODEL` variables, so a team can no longer supply its own
key or choose its own model.

Bajutsu forbids that arrangement by construction. Prime directive 1 keeps an LLM off the `run` and
continuous-integration (CI) gate. [ai-boundary](ai-boundary.md) records the split as a tested
property of the tool. Where a team wants an LLM, the credential and the provider stay with the
team. Four mechanisms reach Claude: the Anthropic application programming interface (API), Amazon
Bedrock, the `ant` CLI, and the Claude Code CLI. Setting `provider: none` states the policy in the
repository itself.

Two consequences follow. A Bajutsu CI run costs no tokens and reaches no third party. A Maestro
suite that leans on AI assertions depends at run time on one vendor's availability and pricing.

## Feature comparison

### Platforms and environment

| Dimension | Maestro | Bajutsu |
|---|---|---|
| iOS | Simulator, documented end to end | Simulator, validated end to end; a real-device path is implemented (BE-0238), with signing still in progress (BE-0288) |
| Android | Emulators and physical devices | Emulator, validated end to end in CI |
| Web | Chromium, marked Beta; locale fixed to `en-US`, viewport preset | Playwright, exercised on the Linux gate every commit |
| Desktop operating systems | macOS, Windows, and Linux | macOS and Linux; no Windows documentation |
| Runtime prerequisite | Java 17 or 21 | Python 3.13 through uv |
| Install | One `curl` script, Homebrew, or a `.zip` | `make setup` from a clone; absent from the Python Package Index (PyPI) |
| On-device component | A companion driver application on both platforms | A built UI Automator server on Android; the XCUITest runner on iOS |

Maestro wins this table on reach, and on the cost of the first five minutes. Its Windows support
and its published binary have no Bajutsu counterpart today.

### Actions

Maestro defines 48 command types. Bajutsu defines about 40 actions. The overlap is large, so the
table lists what one side has and the other lacks.

| Only Maestro | Only Bajutsu |
|---|---|
| `setOrientation` — rotate the device | `push` — deliver a push notification |
| `setDarkMode` / `toggleDarkMode` / `assertDarkMode` / `assertLightMode` | `overrideStatusBar` / `clearStatusBar` — freeze the status bar for screenshots |
| `setAirplaneMode` / `toggleAirplaneMode` | `http` — call an endpoint and save the body into a variable |
| `addMedia` — seed the gallery for a media picker | `totp` — compute a time-based one-time password |
| `travel` — simulate motion along a path | `email` — poll a mailbox and extract a value |
| `evalScript` / `runScript` — arbitrary JavaScript | `generate` — a random or current-datetime value |
| `inputRandomNumber` and the `faker` library | `web` — enter a WebView's Document Object Model (DOM) context |
| | `drag`, `pinch`, and `rotate` as first-class gestures |
| | `select` / `copy` / `clear` / `delete` on a focused field |
| | `setPickerValue` — set an iOS wheel picker by row |
| | `manual` — a recorded human takeover that fails loudly on replay |

The pattern stays consistent across the table. Maestro covers **device state** more broadly:
orientation, theme, radio, and gallery. Bajutsu covers **test-data plumbing** more broadly:
one-time passwords, mailboxes, generated values, and direct endpoint calls. Bajutsu spells each of
those as a declarative step rather than as a script.

### Selectors

| Selector family | Maestro | Bajutsu |
|---|---|---|
| Identifier | `id`, treated as a regular expression | `id` (a list ORs platform spellings), `idMatches` glob |
| Visible text | `text`, treated as a regular expression | `label`, `labelMatches` regular expression, `value` |
| Container scoping | `childOf`, `containsChild`, `containsDescendants` | `within`, nestable |
| Position on screen | `above`, `below`, `leftOf`, `rightOf` | — |
| Web-specific | Cascading Style Sheets (`css`) selectors | — (the `web` context normalizes `data-testid` to an identifier) |
| Element size | `width`, `height`, `tolerance` | — |
| Traits | `text`, `long-text`, `square` | A normalized trait vocabulary |
| Element state | `enabled`, `checked`, `focused`, `selected` | Asserted through `enabled` / `disabled` / `selected` |
| Nth of many | `index`, the routine disambiguator | `index`, the deliberate last resort |
| Two or more matches | Resolves to the first | Raises `AmbiguousSelector` |

Maestro's selector language is the richer of the two. Its relational selectors reach a control that
carries no identifier at all, by anchoring on a neighbor. Bajutsu has nothing comparable, and no
roadmap item proposes one. The repository's answer is to fix the application's identifiers instead,
which `doctor` and `coverage` both push toward.

That answer holds for an application a team owns. It fails for a third-party screen. It fails again
for a legacy screen nobody may edit.

### Control flow, data, and reuse

| Capability | Maestro | Bajutsu |
|---|---|---|
| Conditional | `when` with `visible` / `notVisible` / `platform` / a JavaScript expression | `if` with any assertion as the condition |
| Counted loop | `repeat: { times: N }` | — |
| Conditional loop | `repeat: { while: … }` | — |
| Loop over matched elements | Hand-rolled from `index` plus JavaScript | `forEach` over a selector |
| Subroutine | `runFlow` with `file` or inline `commands`, plus `env` | `use` with a component file and named parameters |
| Data-driven cases | Written by hand in JavaScript | `data` rows or a `dataFile` comma-separated values (CSV) file |
| Setup and teardown | `onFlowStart` / `onFlowComplete` | `before` and `after` with `on: always` / `success` / `error` |
| Unpredictable interstitials | `optional: true` per step | `interrupts`, matched opportunistically anywhere |
| Variables | `${VAR}` from `env`, the CLI, or a `MAESTRO_`-prefixed shell variable | `${vars.*}`, `${data.*}`, `${secrets.*}` |
| Scripting escape hatch | A GraalJS sandbox, a HyperText Transfer Protocol (HTTP) client, and `faker` | None, by design |

The two sides answer one need in opposite ways. Maestro hands the author a JavaScript engine and
lets ordinary programming fill every gap. Bajutsu refuses the escape hatch. It grows a first-class
step for each recurring need instead, so that `codegen`, `audit`, and `impact` can still read a
scenario statically.

The refusal costs Bajutsu two conveniences. Neither a counted loop nor a conditional loop has any
expression in its grammar, and no roadmap item covers either.

### Assertions

| Assertion | Maestro | Bajutsu |
|---|---|---|
| Element visible or absent | `assertVisible` / `assertNotVisible` | `exists` with `negate` |
| Text content | Folded into the `text` selector | `value` and `label` with `equals` / `contains` / `matches` |
| Element count | — | `count` with `equals` / `atLeast` / `atMost` |
| Interactive state | Folded into a state selector | `enabled` / `disabled` / `selected` |
| Screenshot baseline | `assertScreenshot` with `cropOn` and a threshold | `visual` with an element scope, masks, and two comparison engines |
| Element-tree baseline | — | `golden` |
| Clipboard content | Read through `copyTextFrom` plus JavaScript | `clipboard` |
| A network request happened | — | `request` |
| An ordered set of requests | — | `requestSequence` |
| An analytics event fired | — | `event` |
| A response matches a JavaScript Object Notation (JSON) Schema | — | `responseSchema` |
| Arbitrary boolean | `assertTrue` over a JavaScript expression | — |
| Natural-language claim | `assertWithAI`, probabilistic and optional by default | — (prime directive 1 forbids it) |

### Network

This is the widest single gap in the comparison, and it runs in Bajutsu's favor.

Maestro has **no network layer**. A search across both the documentation repository and the source
turns up no request interception, no stubbing, and no request assertion. A flow can call an
endpoint from JavaScript to seed data. An application can detect Maestro and point itself at a mock
server. Neither reaches the traffic the application makes during a run.

Bajutsu treats the network as part of the scenario ([network](network.md)):

- `mocks` stub a matched request in protocol, with a status, headers, a body, and a delay.
- `network.filter` scopes observation to named domains.
- `request`, `requestSequence`, and `event` assert on what the application actually sent.
- `responseSchema` validates a captured response body against a JSON Schema.
- `wait: { until: { request } }` waits for traffic rather than for a screen.
- `redact` masks credential-bearing headers before anything reaches disk.

Two needs follow from that list. A team that wants an offline suite has no path in Maestro. A team
that verifies analytics has none either. Bajutsu answers both directly.

### Evidence and reporting

| Dimension | Maestro | Bajutsu |
|---|---|---|
| Report formats | JUnit XML, HTML, and `html-detailed` | `manifest.json`, JUnit XML, Common Test Report Format (CTRF) JSON, and interactive HTML |
| Screenshots on a green run | None, absent `--analyze` or an explicit `takeScreenshot` | Whatever `capturePolicy` rules request |
| Capture policy | Per-command, written inline | Reusable rules keyed to an action, an event, or an error |
| Video | `startRecording` / `stopRecording`, plus `maestro record` | The `video` interval capture kind |
| Device logs | logcat, the simulator log, and the XCTest log | The `deviceLog` and `appTrace` interval kinds |
| Crash and app-not-responding reports | Collected per flow | Surfaced through the device log |
| Element tree on a red step | `screen-hierarchy/` JSON | The `elements` kind, plus opt-in `rawTree` |
| Per-step attribution | `commands.json` | `manifest.json` plus the `actionLog` kind |
| Touch verification | — | `--touch-markers` draws each touch the application received |
| Secret masking | `label` hides a value from the console, not from debug logs | `redact` masks logs, element trees, and network exchanges before they reach disk |

Reporting sits close to parity. Two rows do not. Bajutsu's masking is a storage-level guarantee
rather than a display convenience. Its `capturePolicy` turns "capture on every X" into a rule that
the second run reproduces without help.

### Suite-level analysis

Bajutsu ships a read-only analysis layer with no Maestro counterpart. Every command below is
advisory. None needs a device, none reaches a model, and none gates CI.

| Command | What it reports |
|---|---|
| `audit` | How reproducible a scenario is, graded statically or by repeat-and-diff |
| `audit --history` | Which scenarios flipped their verdict at a constant fingerprint, per device operating-system version |
| `flakiness` | Which scenarios flip worst, ranked worst first |
| `coverage` | Which of the application's declared identifier namespaces the suite exercises |
| `impact` | Which scenario steps a `git` diff puts at risk |
| `stats` | How the whole suite trends — pass rate, duration, and hotspots |
| `triage` | Why a run went red, and what minimal edit would fix it |
| `codegen` | The scenario as a native XCUITest, Playwright, or UI Automator test |
| `export` | The run as a portable `.zip` archive |

Maestro's nearest features live in Maestro Cloud rather than in the CLI. They also answer a
different question. Smart retries **suppress** a flip instead of ranking it. Nothing in Maestro
maps a suite's coverage, selects tests from a diff, or emits a native test.

`triage` has no counterpart either. It assembles a red run's context and diagnoses it, with a
rule-based agent or with Claude under `--ai`. It then proposes a structured fix: `renameId`,
`addIndex`, or `raiseTimeout`. Passing `--apply` patches that fix into the scenario after a diff
preview.

### AI features

| Dimension | Maestro | Bajutsu |
|---|---|---|
| Authoring from a natural-language goal | Through the MCP server and a coding agent | `record`, a first-class command |
| Autonomous exploration | — | `crawl`, a breadth-first screen map |
| Failure diagnosis | `--analyze` produces an insights report | `triage`, rule-based or `--ai` |
| Assertions a model evaluates | `assertWithAI` and `assertNoDefectsWithAI` | Prime directive 1 forbids them |
| Model provider | Managed by Maestro Cloud; an account is a prerequisite | Anthropic API, Bedrock, `ant`, or Claude Code — the team's own |
| Turning AI off entirely | Refrain from calling the AI commands | `provider: none`, plus an AI-free base install |
| Token and cost accounting | — | An attributed ledger and a usage dashboard |
| MCP tools | Nine, including cloud submission and a device viewer | Two tools, plus run evidence as resources |

Maestro's MCP server is the stronger of the two. `inspect_screen`, `run` with inline YAML,
`cheat_sheet`, and the embedded Maestro Viewer give a coding agent a tight authoring loop. Bajutsu's
two tools do not match it.

### Team operation and scale

| Dimension | Maestro | Bajutsu |
|---|---|---|
| Managed device farm | Maestro Cloud, wiped between runs | — |
| Parallel runs | `--shard-all` and `--shard-split` locally; automatic in Cloud | `--workers` over a device pool or browser contexts |
| Device cloud | Hosted, with model and operating-system selection | An Amazon Web Services (AWS) Device Farm submitter and an Appium provider |
| Pull-request integration | Native for GitHub, and able to block a merge | CI annotations through the GitHub integration |
| Notifications | Email, Slack, and webhooks | — |
| Self-hosting the web surface | Not applicable; Studio is a desktop application | `serve` behind authentication, with organizations and roles |
| Secrets | Passed with `-e`; storage is a Cloud-plan feature | A `secrets` panel that sets write-once values |
| Per-run time limit | A 20-minute soft limit in Cloud | None |
| Cost | CLI free; Cloud from about $125 a month for web, $250 for mobile | Free; the hardware is yours |

Maestro Cloud is a finished product with nothing comparable on the Bajutsu side. A team that wants
parallel hosted devices, merge blocking, and Slack notifications on Monday can buy them.

### Ecosystem and maturity

| Signal | Maestro | Bajutsu |
|---|---|---|
| GitHub stars | About 15,500 | 5 |
| Forks | About 940 | 0 |
| Released version | 2.10.0 | Unreleased; `version = "0.0.0"` |
| Package registry | Homebrew and a GitHub release | Absent from PyPI |
| First commit | 2022 | 2026-06-03 |
| Production users named | Microsoft and DoorDash | None |
| Community | A public Slack and a showcase gallery | None |
| Documentation | A hosted site, in English | 47 pages, mirrored in two languages |
| Test suite | Present | 367 test files against 324 modules, with a per-file coverage ratchet |
| Roadmap | GitHub issues | 400 numbered items, 370 of them implemented |

This table is the honest counterweight to everything above. Maestro has four years of production
use behind it. Bajutsu is three months old, has five stars, and needs a clone to install.

## Where Bajutsu is stronger

**Determinism is enforced, not encouraged.** `AmbiguousSelector`, the absence of any retry
construct, and the capability preflight together make a green run evidence about the application.
Maestro's first-match resolution and its retry family make a green run weaker evidence.

**The network is part of the scenario.** Declarative mocks, request and sequence assertions,
analytics-event assertions, and schema validation of a response have no Maestro counterpart. An
offline suite is reachable in one tool and unreachable in the other.

**An LLM never touches the verdict, and the credential never leaves the team.** Bajutsu's AI split
is a tested property with a documented off switch. Maestro's AI assertions run inside the flow, on
a model the vendor manages, against an account the vendor issues.

**A suite gets an analysis layer.** Flakiness ranking, a coverage map, impact analysis from a diff,
a trend dashboard, and self-healing triage all ship in the free CLI. Maestro's nearest features
cost money, run hosted, and aim at suppressing a flip rather than explaining it.

**Codegen is an exit door.** A green scenario emits a native XCUITest, Playwright, or UI Automator
test. A team can leave Bajutsu without abandoning the flows it wrote. Nothing in Maestro does that.

**Evidence is a policy, not a call site.** `capturePolicy` rules and storage-level masking make the
second run reproduce the first run's evidence, with no author effort and no leaked credential.

**Documentation is bilingual and grounded.** Every page cites the implementation and the roadmap
item behind a behavior, in English and in Japanese alike.

## Where Bajutsu is weaker

**Adoption costs much more.** Maestro is one `curl` line and a `.yaml` file. Bajutsu needs a Git
clone, a uv toolchain, and — for iOS — an XCUITest runner built with Xcode. No published package
exists, so nobody can try it in five minutes.

**The ecosystem is effectively absent.** Five stars, no forks, no community, and no named
production user. Maestro's 15,500 stars carry answered questions, blog posts, and worked examples
that a new team leans on daily.

**Windows is unsupported.** Maestro documents macOS, Windows, and Linux. Bajutsu documents neither
Windows support nor a plan for it. That gap excludes a large share of Android developers outright.

**Real-device coverage is thinner.** Maestro drives physical Android devices as a routine case.
Bajutsu validates Android on an emulator alone. Its iOS real-device path still carries signing work
in progress under BE-0288.

**The selector language is narrower.** Maestro's relational selectors reach a control with no
identifier, by anchoring on a neighbor. Bajutsu has no positional selector, no CSS selector, and no
dimension matcher. No roadmap item proposes any of them. Testing a third-party or legacy screen
costs correspondingly more.

**Common loops have no expression.** Maestro's `repeat` covers a fixed count and a `while`
condition. Bajutsu's `forEach` iterates matched elements and nothing else. "Tap Add five times" and
"delete until the inbox is empty" have no direct form.

**Device-state control has gaps.** Orientation, dark mode, and airplane mode are all missing, and
none appears in the roadmap. A suite that must verify a landscape layout, a dark theme, or offline
behavior cannot express the state change today.

**There is no managed cloud.** Parallel hosted devices, native merge blocking, and Slack
notifications are products a team buys from Maestro and must build for Bajutsu.

**The scripting escape hatch is absent.** The refusal is deliberate, and it protects static
analysis. It also means a genuinely novel need waits for a roadmap item. A Maestro author writes
six lines of JavaScript instead.

## Choosing between them

| A team that … | Is better served by |
|---|---|
| Wants a first flow running this afternoon | Maestro |
| Runs its suite from Windows | Maestro |
| Needs hosted parallel devices and merge blocking today | Maestro |
| Tests a third-party or legacy screen with no identifiers | Maestro |
| Needs orientation, dark mode, or airplane mode | Maestro |
| Must not send screens or credentials to a vendor | Bajutsu |
| Treats an unstable test as a defect rather than a retry | Bajutsu |
| Verifies network traffic or analytics events | Bajutsu |
| Needs an offline suite driven by stubbed responses | Bajutsu |
| Wants coverage, impact, and flakiness analysis in CI | Bajutsu |
| Plans to graduate flows into native tests later | Bajutsu |
| Needs its documentation mirrored in two languages | Bajutsu |

## Gaps worth closing

The comparison surfaces six gaps. Each one is concrete, bounded, and unclaimed by any roadmap item.
Each would remove a reason to choose Maestro without touching a prime directive.

1. **Relational selectors.** `above`, `below`, `leftOf`, and `rightOf`, resolved against the element
   frames a driver already reports. Ambiguity keeps failing, so determinism stays intact.
2. **A counted and a conditional loop.** `repeat` with `times`, and a `while` whose condition is an
   assertion. A bound like `scroll`'s `maxScrolls` keeps every loop terminating.
3. **Device orientation.** A `setOrientation` step behind a capability token. The existing preflight
   gates it on backends that cannot honor it.
4. **Dark-mode control and assertion.** The same shape as orientation, and a prerequisite for
   meaningful visual regression across themes.
5. **Airplane mode.** The missing half of offline testing. `mocks` already stub a response, but no
   step severs the radio.
6. **A published package.** Until `pip install bajutsu` works, every evaluation starts with a clone
   and a toolchain.

The first five are authoring features. The sixth decides whether anyone reaches the first five.
